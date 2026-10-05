"""Read-only Git surface and non-following file observations.

An ObservationSession is scoped to one planner call. Ready-gate and post-execution
checks always create a new session, never reuse stat/mtime-based content caches.
"""
import hashlib
import os
import pathlib
import stat
import subprocess
from functools import lru_cache

from .model import FileIdentity, InvalidPolicy, Observation, Surface
from .profile import affects_descendants, matches, pattern_segments
from .serialization import default_safety, diagnostic_provider_artifact_path, digest, safe_path, sensitive_path


def _git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, check=False)
    if result.returncode:
        raise InvalidPolicy('invalid-git-baseline')
    return result.stdout


def _paths(raw):
    return {os.fsdecode(p) for p in raw.split(b'\0') if p}


@lru_cache(maxsize=32)
def _trusted_runtime_relative(root_name: str) -> str | None:
    """Return only the canonical verification-v2 runtime namespace if local."""
    root = pathlib.Path(root_name).resolve()
    try:
        from agent_harness.repository import resolve_control_root
        runtime, _ = resolve_control_root(root)
        return runtime.resolve(strict=False).relative_to(root).as_posix()
    except ValueError:
        return None
    except Exception:
        raise InvalidPolicy('verification-runtime-namespace-unresolved') from None


def changed_surface(root, base_sha):
    root = pathlib.Path(root)
    # Object IDs only: revision expressions/options are not accepted family identities.
    if not isinstance(base_sha, str) or len(base_sha) not in (40, 64) or any(c not in '0123456789abcdef' for c in base_sha):
        raise InvalidPolicy('invalid-base-sha')
    if _git(root, 'cat-file', '-t', base_sha).strip() != b'commit':
        raise InvalidPolicy('invalid-base-sha')
    _git(root, 'merge-base', '--is-ancestor', base_sha, 'HEAD')
    # Three separate deltas preserve touched paths even when an overlay reverses a commit.
    paths = _paths(_git(root, 'diff', '--no-renames', '--name-only', '-z', base_sha, 'HEAD', '--'))
    paths |= _paths(_git(root, 'diff', '--no-renames', '--name-only', '-z', '--cached', '--'))
    paths |= _paths(_git(root, 'diff', '--no-renames', '--name-only', '-z', '--'))
    # No ignore rule is an authority exclusion. Ignored untracked candidate
    # files remain visible to the changed-surface calculation.
    untracked = _paths(_git(root, 'ls-files', '--others', '-z'))
    paths |= untracked
    tracked = _paths(_git(root, 'ls-files', '-z'))
    baseline = _paths(_git(root, 'ls-tree', '-r', '--name-only', '-z', base_sha))
    runtime = _trusted_runtime_relative(str(root.resolve()))
    def control_path(path):
        return (path == '.git' or path.startswith('.git/') or path == '.agent-state' or
                path.startswith('.agent-state/') or runtime is not None and
                (path == runtime or path.startswith(runtime + '/')))
    paths = {path for path in paths if not control_path(path)}
    tracked = {path for path in tracked if not control_path(path)}
    baseline = {path for path in baseline if not control_path(path)}
    deleted = tuple(sorted(p for p in paths | baseline if not os.path.lexists(root / p)))
    common = os.fsdecode(_git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip())
    return Surface(tuple(sorted(paths)), deleted, tuple(sorted(tracked | baseline | untracked)),
                   digest(str(pathlib.Path(common).resolve())), base_sha)


def _component_kind(root, relative):
    path = pathlib.Path(root)
    for component in relative.split('/'):
        path = path / component
        try:
            info = path.lstat()
        except (FileNotFoundError, NotADirectoryError):
            return 'absent', path, None
        if stat.S_ISLNK(info.st_mode): return 'symlink', path, info
    if stat.S_ISREG(info.st_mode): return 'file', path, info
    if stat.S_ISDIR(info.st_mode): return 'directory', path, info
    return 'unsupported', path, info


class ObservationSession:
    def __init__(self, root, surface, safety=None, *, enumerate_paths=True):
        self.root = pathlib.Path(root)
        self.surface = surface
        self.safety = safety or default_safety()
        self.identities = {}
        self.candidates = set(surface.tracked) | set(surface.paths) | set(surface.deleted)
        self.scan_failed = False
        def walk(directory, prefix=''):
            try:
                with os.scandir(directory) as entries:
                    entries = sorted(entries, key=lambda e: e.name)
            except OSError:
                self.scan_failed = True
                return
            for entry in entries:
                relative = prefix + entry.name
                runtime = _trusted_runtime_relative(str(self.root.resolve()))
                if (relative == '.git' or relative == '.agent-state' or
                        relative.startswith('.agent-state/') or
                        runtime is not None and (relative == runtime or relative.startswith(runtime + '/'))):
                    continue
                self.candidates.add(relative)
                if entry.is_dir(follow_symlinks=False): walk(entry.path, relative + '/')
        if enumerate_paths: walk(self.root)
        self.by_prefix = {}
        for relative in self.candidates:
            parts = relative.split('/')
            for end in range(1, len(parts) + 1):
                self.by_prefix.setdefault('/'.join(parts[:end]), set()).add(relative)

    def identity(self, relative):
        # Rejections are observations too. Keep their exact reason, without a
        # content identity, for this evaluation only; fresh checks use a new session.
        if relative not in self.identities:
            self.identities[relative] = self._observe_identity(relative)
        return self.identities[relative]

    def _observe_identity(self, relative):
        if diagnostic_provider_artifact_path(relative):
            return None, 'non-cacheable-policy'
        if not safe_path(relative) or not self.safety.safe(relative) or sensitive_path(relative):
            return None, 'non-cacheable-sensitive-identity'
        kind, path, info = _component_kind(self.root, relative)
        if kind == 'symlink': return None, 'non-cacheable-symlink-input'
        if kind == 'unsupported': return None, 'non-cacheable-external-state'
        content_hash = None
        if kind == 'file':
            # O_NOFOLLOW also closes a final-component replacement between lstat/open.
            try:
                fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                with os.fdopen(fd, 'rb') as stream:
                    opened = os.fstat(stream.fileno())
                    if not stat.S_ISREG(opened.st_mode): return None, 'non-cacheable-external-state'
                    h = hashlib.sha256()
                    tail = b''
                    overlap = max([256, *(len(s.encode('utf-8')) for s in self.safety.known_secrets)])
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        inspected = tail + block
                        if not self.safety.safe(inspected.decode('utf-8', errors='replace')):
                            return None, 'non-cacheable-sensitive-identity'
                        h.update(block)
                        tail = inspected[-overlap:]
                    content_hash = h.hexdigest()
                    info = opened
            except OSError:
                return None, 'non-cacheable-external-state'
        return FileIdentity(relative, kind, content_hash, bool(info and info.st_mode & 0o111)), None

    def inputs(self, patterns):
        candidates = set()
        reasons = {'non-cacheable-external-state'} if self.scan_failed else set()
        for pattern in patterns:
            if diagnostic_provider_artifact_path(pattern): reasons.add('non-cacheable-policy')
            segments = pattern_segments(pattern)
            literal = []
            for segment in segments:
                if '*' in segment or '?' in segment: break
                literal.append(segment)
            prefix = '/'.join(literal)
            candidates.update(self.by_prefix.get(prefix, ()) if prefix else self.candidates)
            # A linked ancestor is relevant even when no descendant was enumerated.
            candidates.update('/'.join(literal[:end]) for end in range(1, len(literal) + 1))
            if len(literal) == len(segments): candidates.add(pattern)
        manifest = []
        for relative in sorted(candidates):
            selected = any(matches(p, relative) for p in patterns)
            if not selected and not any(affects_descendants(p, relative) for p in patterns): continue
            kind, _, _ = _component_kind(self.root, relative)
            if kind == 'symlink':
                reasons.add('non-cacheable-symlink-input')
                continue
            if not selected: continue
            identity, reason = self.identity(relative)
            if reason: reasons.add(reason)
            elif identity.kind != 'directory': manifest.append(identity)
        return Observation(tuple(manifest), tuple(sorted(reasons)))


    def artifacts(self, paths):
        """Use this evaluation's identity cache for producer and consumer reads."""
        manifest = []
        for path in sorted(paths):
            if not safe_path(path) or not self.safety.safe(path): raise InvalidPolicy('unsafe-artifact-path')
            identity, reason = self.identity(path)
            if reason or identity.kind != 'file': raise ValueError('invalid-artifact-identity')
            manifest.append(identity)
        return tuple(manifest)


def observe_inputs(root, patterns, surface, *, safety=None):
    return ObservationSession(root, surface, safety).inputs(patterns)


def observe_artifacts(root, paths, *, safety=None):
    safety = safety or default_safety()
    session = ObservationSession(root, Surface((), (), (), '', ''), safety, enumerate_paths=False)
    return session.artifacts(paths)
