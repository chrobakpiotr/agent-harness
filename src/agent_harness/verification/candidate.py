"""Privacy-first deterministic candidate sealing helpers.

These helpers are the trusted boundary for turning a repository worktree into
an immutable candidate/surface identity. Advisory planner fingerprints remain
separate and cannot be used as lifecycle authority.
"""
from __future__ import annotations

import hashlib
import math
import os
import pathlib
import re
import stat
import subprocess
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass

from .serialization import canonical, sensitive_path

MAX_FILE = 256 * 1024 * 1024
MAX_OBJECTS = 100_000
MAX_BYTES = 4 * 1024 * 1024 * 1024
MAX_SECONDS = 300
SECRET_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE | re.ASCII) for pattern in (
    r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----',
    r'(?:password|passwd|secret|token|api[_-]?key|credential|private[_-]?key|client[_-]?secret|access[_-]?key)\s*[:=]\s*\S+',
    r'(?:AKIA|ASIA)[0-9A-Z]{16}',
    r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})',
    r'sk-[A-Za-z0-9_-]{16,}',
    r'[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^@\s/]+@',
    r'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}',
))
ASCII_TOKENS = re.compile(r'[A-Za-z0-9_+/=-]+', re.ASCII)


class CandidateSealError(ValueError):
    """Stable fail-closed reason; message never contains candidate bytes."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class CandidateSeal:
    candidate_identity: str
    changed_surface_id: str
    head_sha: str
    base_sha: str
    entries: tuple[dict, ...]


def _git(root: pathlib.Path, *args: str) -> bytes:
    try:
        result = subprocess.run(['git', '-C', str(root), *args], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, check=False)
    except OSError:
        raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE') from None
    if result.returncode:
        raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE')
    return result.stdout


def _git_optional(root: pathlib.Path, *args: str) -> bytes | None:
    try:
        result = subprocess.run(['git', '-C', str(root), *args], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, check=False)
    except OSError:
        raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE') from None
    return result.stdout if result.returncode == 0 else None


def _decode_path(raw: bytes) -> str:
    try:
        value = raw.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        raise CandidateSealError('CANDIDATE_SEALING_UNSAFE_OBJECT') from None
    if (not value or value.startswith('/') or '\\' in value or
            any(part in {'', '.', '..'} for part in value.split('/')) or
            unicodedata.normalize('NFC', value) != value or '\x00' in value):
        raise CandidateSealError('CANDIDATE_SEALING_UNSAFE_OBJECT')
    return value


def _paths(root: pathlib.Path, base_sha: str, trusted_runtime_root: pathlib.Path | None = None, *, source_objects: set[str] | None = None) -> tuple[dict[str, set[str]], str, str, pathlib.Path]:
    if not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', base_sha):
        raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE')
    if _git(root, 'cat-file', '-t', base_sha).strip() != b'commit':
        raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE')
    head_sha = _git(root, 'rev-parse', '--verify', 'HEAD^{commit}').strip().decode('ascii', 'strict')
    _git(root, 'merge-base', '--is-ancestor', base_sha, head_sha)
    common = pathlib.Path(os.fsdecode(_git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip())).resolve()
    repo_id = hashlib.sha256(os.fsencode(common)).hexdigest()
    result: dict[str, set[str]] = {}
    tracked = {_decode_path(raw) for raw in _git(root, 'ls-files', '-z').split(b'\0') if raw}
    commands = (
        ('committed', ('diff', '--no-renames', '--name-only', '-z', base_sha, head_sha, '--')),
        ('staged', ('diff', '--no-renames', '--name-only', '-z', '--cached', '--')),
        ('unstaged', ('diff', '--no-renames', '--name-only', '-z', '--')),
    )
    for layer, command in commands:
        for raw in _git(root, *command).split(b'\0'):
            if raw:
                path = _decode_path(raw)
                result.setdefault(path, set()).add(layer)
    git_dir = pathlib.Path(os.fsdecode(_git(root, 'rev-parse', '--absolute-git-dir').strip())).resolve()
    git_rel = None
    try:
        git_rel = git_dir.relative_to(root.resolve()).as_posix()
    except ValueError:
        pass
    state_dir = common.parent / '.agent-state'
    excluded_prefixes = {'.git'}
    try:
        if state_dir.resolve(strict=False) == (root / '.agent-state').resolve(strict=False):
            excluded_prefixes.add('.agent-state')
    except (OSError, RuntimeError):
        raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE') from None
    if trusted_runtime_root is None:
        try:
            from agent_harness.repository import resolve_control_root
            verification_runtime, _repository_id = resolve_control_root(root)
        except Exception:
            raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE') from None
    else:
        verification_runtime = pathlib.Path(trusted_runtime_root)
    try:
        runtime_relative = verification_runtime.resolve(strict=False).relative_to(root).as_posix()
        excluded_prefixes.add(runtime_relative)
    except ValueError:
        # A runtime namespace outside this candidate root excludes no paths.
        pass
    if git_rel:
        excluded_prefixes.add(git_rel)

    def excluded(path: str) -> bool:
        return any(path == prefix or path.startswith(prefix + '/') for prefix in excluded_prefixes)

    # Enumerate the filesystem to include ignored untracked files. Ignore rules
    # are intentionally not consulted. Only exact trusted control roots above
    # are excluded.
    seen: set[str] = set()
    root_stat = root.stat(follow_symlinks=False)
    if not stat.S_ISDIR(root_stat.st_mode):
        raise CandidateSealError('CANDIDATE_SEALING_UNSAFE_OBJECT')
    def walk(directory: pathlib.Path, prefix: str = '') -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError:
            raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE') from None
        with_entries = entries
        for item in with_entries:
            relative = f'{prefix}{item.name}'
            if excluded(relative):
                # A reserved name is excluded only at its exact trusted root.
                if relative in excluded_prefixes - {'.git'} and item.is_symlink():
                    raise CandidateSealError('CANDIDATE_SEALING_UNSAFE_OBJECT')
                continue
            _decode_path(relative.encode('utf-8', errors='strict'))
            if sensitive_path(relative):
                raise CandidateSealError('SECRET_BEARING_CANDIDATE_UNSEALABLE')
            seen.add(relative)
            if source_objects is not None:
                source_objects.add(relative)
            if len(seen) > MAX_OBJECTS:
                raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE')
            if item.is_dir(follow_symlinks=False):
                try:
                    if item.stat(follow_symlinks=False).st_dev != root_stat.st_dev:
                        raise CandidateSealError('CANDIDATE_SEALING_UNSAFE_OBJECT')
                except OSError:
                    raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE') from None
                walk(pathlib.Path(item.path), relative + '/')
            elif item.is_file(follow_symlinks=False) or item.is_symlink():
                # Tracked paths are already represented by Git layers. Every
                # untracked path is candidate-relevant, including ignored files.
                if relative not in tracked:
                    result.setdefault(relative, set()).add('untracked')
            else:
                result.setdefault(relative, set()).add('special')
    walk(root)
    for path in result:
        if excluded(path):
            raise CandidateSealError('CANDIDATE_SEALING_UNSAFE_OBJECT')
    return result, head_sha, repo_id, common


def _privacy_check(data: bytes) -> None:
    if b'\x00' in data:
        raise CandidateSealError('CANDIDATE_PRIVACY_PREFLIGHT_UNAVAILABLE')
    try:
        text = data.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        raise CandidateSealError('CANDIDATE_PRIVACY_PREFLIGHT_UNAVAILABLE') from None
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        raise CandidateSealError('SECRET_BEARING_CANDIDATE_UNSEALABLE')
    for match in ASCII_TOKENS.finditer(text):
        token = match.group(0).encode('ascii')
        if len(token) < 32:
            continue
        counts = Counter(token)
        entropy = -sum((count / len(token)) * math.log2(count / len(token)) for count in counts.values())
        if entropy > 4.2:
            raise CandidateSealError('SECRET_BEARING_CANDIDATE_UNSEALABLE')


def _read_worktree(root: pathlib.Path, relative: str, root_device: int) -> tuple[bytes | None, dict | None]:
    path = root / relative
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None, None
    except OSError:
        raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_RACE') from None
    if (stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or
            before.st_nlink != 1 or before.st_dev != root_device):
        raise CandidateSealError('CANDIDATE_SEALING_UNSAFE_OBJECT')
    if before.st_size > MAX_FILE:
        raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE')
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_size, opened.st_nlink) != (
                    before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_nlink):
                raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_RACE')
            data = stream.read(MAX_FILE + 1)
            after = os.fstat(stream.fileno())
    except CandidateSealError:
        raise
    except OSError:
        raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_RACE') from None
    identity_before = (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns,
                       before.st_ctime_ns, before.st_nlink)
    identity_after = (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns,
                      after.st_ctime_ns, after.st_nlink)
    if identity_before != identity_after or len(data) != before.st_size or len(data) > MAX_FILE:
        raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_RACE')
    return data, {'kind': 'file', 'mode': before.st_mode & 0o777, 'executable': bool(before.st_mode & 0o111),
                  'identity': identity_before}


def _git_mode(root: pathlib.Path, revision: str, relative: str) -> int | None:
    record = _git_optional(root, 'ls-tree', '-z', revision, '--', f':(literal){relative}')
    if record is None or not record.strip(b'\0'):
        return None
    try:
        rows = [row for row in record.split(b'\0') if row]
        if len(rows) != 1:
            raise ValueError()
        mode = rows[0].split(b'\t', 1)[0].split()[0]
        return int(mode, 8)
    except (IndexError, ValueError):
        raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE') from None


def _index_entry(root: pathlib.Path, relative: str) -> tuple[int, str] | None:
    record = _git_optional(root, 'ls-files', '--stage', '-z', '--', f':(literal){relative}')
    if record is None or not record.strip(b'\0'):
        return None
    try:
        rows = [row for row in record.split(b'\0') if row]
        if len(rows) != 1:
            raise ValueError()
        facts = rows[0].split(b'\t', 1)[0].split()
        return int(facts[0], 8), facts[1].decode('ascii')
    except (IndexError, ValueError):
        raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE') from None


def seal_candidate(root: pathlib.Path, base_sha: str, authority_bindings: dict, *,
                   trusted_runtime_root: pathlib.Path | None = None) -> CandidateSeal:
    """Privacy-check exact observed bytes before publishing any content hash."""
    root = pathlib.Path(root).resolve(strict=True)
    started = time.monotonic()
    layers, head_sha, repo_id, _common = _paths(root, base_sha, trusted_runtime_root)
    if not isinstance(authority_bindings, dict) or not authority_bindings:
        raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE')
    root_stat = root.stat(follow_symlinks=False)
    snapshots: dict[str, tuple[bytes | None, dict | None, dict[str, bytes]]] = {}
    total = 0
    for relative in sorted(layers):
        if time.monotonic() - started > MAX_SECONDS:
            raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE')
        data, metadata = _read_worktree(root, relative, root_stat.st_dev)
        layer_bytes: dict[str, bytes] = {}
        if 'committed' in layers[relative]:
            committed = _git_optional(root, 'show', f'{head_sha}:{relative}')
            if committed is not None:
                layer_bytes['committed'] = committed
        if 'staged' in layers[relative]:
            index_entry = _index_entry(root, relative)
            if index_entry is not None:
                staged = _git_optional(root, 'cat-file', 'blob', index_entry[1])
                if staged is not None:
                    layer_bytes['staged'] = staged
        if data is not None:
            layer_bytes['worktree'] = data
        for content in layer_bytes.values():
            if len(content) > MAX_FILE:
                raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE')
            _privacy_check(content)
            total += len(content)
        snapshots[relative] = (data, metadata, layer_bytes)
        if total > MAX_BYTES or len(snapshots) > MAX_OBJECTS:
            raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE')
    if time.monotonic() - started > MAX_SECONDS:
        raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE')
    # Re-enumerate before any durable identity is returned; also ensure no
    # candidate path was added/removed while the seal snapshot was read.
    current_layers, current_head, current_repo, _ = _paths(root, base_sha, trusted_runtime_root)
    if current_head != head_sha or current_repo != repo_id or set(current_layers) != set(layers):
        raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_RACE')
    entries = []
    for relative in sorted(layers):
        data, metadata, layer_bytes = snapshots[relative]
        entry = {'path': relative, 'changes': sorted(layers[relative]), 'state': 'deleted' if data is None else 'present'}
        entry['head_mode'] = _git_mode(root, head_sha, relative) if 'committed' in layers[relative] else None
        index_entry = _index_entry(root, relative) if 'staged' in layers[relative] else None
        entry['index_mode'] = index_entry[0] if index_entry is not None else None
        entry['layer_content_sha256'] = {
            layer: hashlib.sha256(content).hexdigest() for layer, content in sorted(layer_bytes.items())
        }
        if metadata:
            entry.update({key: metadata[key] for key in ('kind', 'mode', 'executable')})
        entries.append(entry)
        if metadata:
            try:
                after = (root / relative).lstat()
            except OSError:
                raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_RACE') from None
            identity_after = (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns,
                              after.st_ctime_ns, after.st_nlink)
            if identity_after != metadata['identity']:
                raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_RACE')
    surface_projection = {'version': 'final-changed-surface-v1', 'base_sha': base_sha,
                          'head_sha': head_sha, 'repository_id': repo_id, 'entries': entries}
    surface_id = hashlib.sha256(canonical(surface_projection)).hexdigest()
    candidate_projection = {'version': 'final-candidate-identity-v1', 'base_sha': base_sha,
                            'final_changed_surface_id': surface_id, 'authority_bindings': authority_bindings}
    candidate_id = hashlib.sha256(canonical(candidate_projection)).hexdigest()
    final_layers, final_head, final_repo, _ = _paths(root, base_sha, trusted_runtime_root)
    if final_head != head_sha or final_repo != repo_id or set(final_layers) != set(layers):
        raise CandidateSealError('CANDIDATE_SEALING_SNAPSHOT_RACE')
    return CandidateSeal(candidate_id, surface_id, head_sha, base_sha, tuple(entries))
