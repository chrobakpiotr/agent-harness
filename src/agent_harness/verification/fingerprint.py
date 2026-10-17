"""Read-only Git surface and non-following file observations.

An ObservationSession is scoped to one planner call. Ready-gate and post-execution
checks always create a new session, never reuse stat/mtime-based content caches.
"""
import errno
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


def _pattern_prefixes(patterns):
    prefixes = set()
    broad = False
    for pattern in patterns:
        segments = pattern_segments(pattern)
        literal = []
        for segment in segments:
            if '*' in segment or '?' in segment: break
            literal.append(segment)
        if literal:
            prefixes.add('/'.join(literal))
        else:
            broad = True
    return None if broad else tuple(sorted(prefixes))


def _base_path_candidates(root, base_sha, patterns):
    patterns = tuple(patterns)
    if not patterns:
        return set()
    prefixes = _pattern_prefixes(patterns)
    args = ['ls-tree', '-r', '--name-only', '-z', base_sha]
    if prefixes is not None:
        args.extend(['--', *(f':(literal){prefix}' for prefix in prefixes)])
    return _paths(_git(root, *args))


@lru_cache(maxsize=32)
def _control_runtime_relative(root_name: str) -> str | None:
    return _trusted_runtime_relative(str(pathlib.Path(root_name).resolve()))


def _same_control_namespace(root, relative, canonical):
    relative_parts = relative.split('/')
    canonical_parts = canonical.split('/')
    if (len(relative_parts) < len(canonical_parts) or
            tuple(part.casefold() for part in relative_parts[:len(canonical_parts)]) !=
            tuple(part.casefold() for part in canonical_parts)):
        return False
    # Control namespaces are reserved case-insensitively on every host. Do not
    # let a case-sensitive filesystem turn a spelling variant into candidate
    # input: the same repository policy must hold after checkout elsewhere.
    return True


def _is_control_path(root, relative):
    root = pathlib.Path(root)
    runtime = _control_runtime_relative(os.path.abspath(os.fspath(root)))
    prefixes = ('.git', '.agent-state')
    if any(_same_control_namespace(root, relative, prefix) for prefix in prefixes):
        return True
    if runtime is None:
        return False
    return _same_control_namespace(root, relative, runtime)


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


def changed_surface(root, base_sha, *, patterns=(), applicability_patterns=None):
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
    patterns = tuple(patterns)
    applicability_patterns = (patterns if applicability_patterns is None
                               else tuple(applicability_patterns))
    base_candidates = _base_path_candidates(root, base_sha, patterns)
    for path in base_candidates:
        if (not _is_control_path(root, path) and
                any(matches(pattern, path) for pattern in applicability_patterns) and
                not os.path.lexists(root / path)):
            paths.add(path)
    paths = {path for path in paths if not _is_control_path(root, path)}
    deleted = tuple(sorted(p for p in paths if not os.path.lexists(root / p)))
    common = os.fsdecode(_git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip())
    return Surface(tuple(sorted(paths)), deleted, (), digest(str(pathlib.Path(common).resolve())), base_sha,
                   tuple(sorted(base_candidates)), patterns)


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
    def __init__(self, root, surface, safety=None, *, enumerate_paths=True, input_patterns=()):
        self.root = pathlib.Path(root)
        self.surface = surface
        self.safety = safety or default_safety()
        self.identities = {}
        self.enumerate_paths = enumerate_paths
        # Changed/deleted paths are already bounded by the surface delta. Current
        # paths are discovered only inside the literal prefixes requested below.
        self.candidates = set(surface.paths) | set(surface.deleted)
        base_candidates = (surface.base_candidates
                           if set(input_patterns).issubset(surface.base_patterns)
                           else _base_path_candidates(root, surface.base_sha, input_patterns))
        self.base_candidates = set(base_candidates)
        self.candidates.update(path for path in base_candidates
                               if not _is_control_path(root, path))
        self.by_prefix = {}
        for relative in self.candidates:
            parts = relative.split('/')
            for end in range(1, len(parts) + 1):
                self.by_prefix.setdefault('/'.join(parts[:end]), set()).add(relative)
        self.path_cache = {}
        self.scan_failed = False

    @staticmethod
    def _directory_flags():
        return (os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) |
                getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0))

    def _open_directory_path(self, relative):
        """Open every component relative to a pinned parent without following links."""
        current = os.open(self.root, self._directory_flags())
        try:
            for component in relative.split('/'):
                child = os.open(component, self._directory_flags(), dir_fd=current)
                os.close(current)
                current = child
            if not stat.S_ISDIR(os.fstat(current).st_mode):
                raise OSError('not a directory')
            return current
        except OSError:
            os.close(current)
            raise

    def _open_file_path(self, relative):
        """Open a regular-file candidate beneath a pinned, non-symlink parent."""
        parts = relative.split('/')
        parent = '/'.join(parts[:-1])
        parent_fd = (self._open_directory_path(parent) if parent else
                     os.open(self.root, self._directory_flags()))
        try:
            return os.open(parts[-1], os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                           getattr(os, 'O_NONBLOCK', 0) |
                           getattr(os, 'O_CLOEXEC', 0), dir_fd=parent_fd)
        finally:
            os.close(parent_fd)

    def _walk_paths(self, directory, prefix, candidates, *, dir_fd=None):
        opened_fd = dir_fd
        try:
            if opened_fd is None:
                opened_fd = os.open(directory, self._directory_flags())
            if not stat.S_ISDIR(os.fstat(opened_fd).st_mode):
                self.scan_failed = True
                return
            with os.scandir(opened_fd) as entries:
                for entry in sorted(entries, key=lambda e: e.name):
                    relative = prefix + entry.name
                    if _is_control_path(self.root, relative):
                        continue
                    candidates.add(relative)
                    try:
                        is_directory = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        self.scan_failed = True
                        continue
                    if not is_directory:
                        continue
                    child_fd = None
                    try:
                        child_fd = os.open(entry.name, self._directory_flags(), dir_fd=opened_fd)
                        if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
                            self.scan_failed = True
                            continue
                        self._walk_paths(None, relative + '/', candidates, dir_fd=child_fd)
                        child_fd = None  # ownership transferred; recursive call closes it
                    except OSError:
                        self.scan_failed = True
                    finally:
                        if child_fd is not None:
                            os.close(child_fd)
        except OSError:
            self.scan_failed = True
        finally:
            if opened_fd is not None:
                os.close(opened_fd)

    def identity(self, relative):
        # Rejections are observations too. Keep their exact reason, without a
        # content identity, for this evaluation only; fresh checks use a new session.
        if relative not in self.identities:
            self.identities[relative] = self._observe_identity(relative)
        return self.identities[relative]

    def _observe_identity(self, relative):
        if _is_control_path(self.root, relative):
            return None, 'non-cacheable-external-state'
        if diagnostic_provider_artifact_path(relative):
            return None, 'non-cacheable-policy'
        if not safe_path(relative) or not self.safety.safe(relative) or sensitive_path(relative):
            return None, 'non-cacheable-sensitive-identity'
        kind, path, info = _component_kind(self.root, relative)
        if kind == 'symlink': return None, 'non-cacheable-symlink-input'
        if kind == 'unsupported': return None, 'non-cacheable-external-state'
        content_hash = None
        if kind == 'file':
            # Pin each ancestor with openat/O_NOFOLLOW; protecting only the leaf
            # still permits a parent-directory swap to redirect an absolute open.
            try:
                fd = self._open_file_path(relative)
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
            except OSError as exc:
                raced_kind, _, _ = _component_kind(self.root, relative)
                if raced_kind == 'symlink' or exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    return None, 'non-cacheable-symlink-input'
                return None, 'non-cacheable-external-state'
        return FileIdentity(relative, kind, content_hash, bool(info and info.st_mode & 0o111)), None

    def inputs(self, patterns):
        patterns = tuple(patterns)
        if not patterns:
            return Observation((), ())
        candidates = set()
        roots = set()
        matched_patterns = set()
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
            if len(literal) == len(segments):
                candidates.add(pattern)
            elif self.enumerate_paths:
                roots.add(prefix)
        # Discover only subtrees that can contain a match. Literal paths need no
        # directory listing at all; broad patterns with no literal prefix still
        # intentionally cover the repository tree.
        for prefix in sorted(roots, key=lambda value: (value.count('/'), value)):
            if prefix and '' in roots:
                continue
            if prefix and any(prefix.startswith(parent + '/') for parent in roots if parent and parent != prefix):
                continue
            if prefix in self.path_cache:
                candidates.update(self.path_cache[prefix])
                continue
            discovered = set()
            self.path_cache[prefix] = discovered
            if prefix and _is_control_path(self.root, prefix):
                reasons.add('non-cacheable-external-state')
                continue
            directory = self.root
            if prefix:
                kind, _, _ = _component_kind(self.root, prefix)
                if kind == 'symlink':
                    continue
                if kind != 'directory':
                    continue
                try:
                    directory_fd = self._open_directory_path(prefix)
                except OSError:
                    reasons.add('non-cacheable-symlink-input')
                    self.scan_failed = True
                    continue
                self._walk_paths(None, prefix + '/', discovered, dir_fd=directory_fd)
            else:
                self._walk_paths(directory, '', discovered)
            candidates.update(discovered)
        if self.scan_failed:
            reasons.add('non-cacheable-external-state')
        manifest = []
        for relative in sorted(candidates):
            selected_patterns = tuple(p for p in patterns if matches(p, relative))
            selected = bool(selected_patterns)
            if not selected and not any(affects_descendants(p, relative) for p in patterns): continue
            if _is_control_path(self.root, relative):
                reasons.add('non-cacheable-external-state')
                continue
            kind, _, _ = _component_kind(self.root, relative)
            if kind == 'symlink':
                reasons.add('non-cacheable-symlink-input')
                continue
            if not selected: continue
            identity, reason = self.identity(relative)
            if reason: reasons.add(reason)
            elif identity.kind != 'directory' and (
                    identity.kind != 'absent' or relative in self.base_candidates):
                manifest.append(identity)
                if identity.kind == 'file' or relative in self.base_candidates:
                    matched_patterns.update(selected_patterns)
        if any(pattern not in matched_patterns for pattern in patterns):
            reasons.add('empty-declared-input')
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
    patterns = tuple(patterns)
    return ObservationSession(root, surface, safety, enumerate_paths=bool(patterns),
                              input_patterns=patterns).inputs(patterns)


def observe_artifacts(root, paths, *, safety=None):
    safety = safety or default_safety()
    session = ObservationSession(root, Surface((), (), (), '', ''), safety, enumerate_paths=False)
    return session.artifacts(paths)
