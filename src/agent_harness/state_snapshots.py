"""Write-ahead snapshots for the repository lifecycle authority tree.

Callers must hold the repository-wide lifecycle lock for prepare, state replacement,
acknowledgement, and retention. The current ref is the only restore authority; prepared
refs are intentionally not restorable.

Verbatim from Showcase `tooling/agent-harness/state_snapshots.py` at `49ab97f` (blob `b66a9ed8`); see
docs/migration/provenance.md.
"""
from __future__ import annotations

import datetime as dt
import functools
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import zlib
from typing import Any

SCHEMA_VERSION = 1
SNAPSHOT_LIMIT = 64
_FEATURE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
_HEX = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
_FILE_MODES = {0o600, 0o640, 0o644, 0o700, 0o750, 0o755}


class SnapshotError(RuntimeError):
    """Lifecycle snapshot failed closed."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('utf-8')


def _git(repository: pathlib.Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(['git', '-C', str(repository), *args], input=input_bytes,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise SnapshotError('snapshot-git-operation-failed')
    return result.stdout


def common_dir(repository: pathlib.Path) -> pathlib.Path:
    return _common_dir_cached(str(pathlib.Path(repository).resolve()))


@functools.lru_cache(maxsize=256)
def _common_dir_cached(repository: str) -> pathlib.Path:
    try:
        value = _git(pathlib.Path(repository), 'rev-parse', '--path-format=absolute', '--git-common-dir').strip()
        common = pathlib.Path(os.fsdecode(value)).resolve(strict=True)
    except (OSError, RuntimeError):
        raise SnapshotError('snapshot-repository-unresolved') from None
    if not common.is_dir():
        raise SnapshotError('snapshot-repository-unresolved')
    return common


def lifecycle_lock_path(repository: pathlib.Path) -> pathlib.Path:
    """One lock shared by every feature using this Git common directory."""
    return common_dir(repository) / 'agent-state-lifecycle.lock'


def current_ref(feature: str) -> str:
    _validate_feature(feature)
    return f'refs/agent-state/{feature}'


def current_repository_ref() -> str:
    return 'refs/agent-state/current'


def _snapshots_prefix(feature: str) -> str:
    _validate_feature(feature)
    return f'refs/agent-state-snapshots/{feature}/'


def _prepared_prefix(feature: str) -> str:
    _validate_feature(feature)
    return f'refs/agent-state-prepared/{feature}/'


def _validate_feature(feature: str) -> None:
    if not isinstance(feature, str) or not _FEATURE.fullmatch(feature):
        raise SnapshotError('snapshot-feature-invalid')


def remote_repository_slug(repository: pathlib.Path) -> str | None:
    try:
        raw = os.fsdecode(_git(repository, 'config', '--get', 'remote.origin.url')).strip()
    except SnapshotError:
        return None
    if raw:
        try:
            if '://' in raw:
                parsed = urllib.parse.urlsplit(raw)
                host = parsed.hostname
                remote_path = parsed.path
            else:
                scp = re.fullmatch(r'(?:[^@/]+@)?([^:/]+):(.+)', raw)
                if scp:
                    host, remote_path = scp.groups()
                else:
                    host, remote_path = '', ''
        except ValueError:
            return None
        components = [part for part in remote_path.strip('/').split('/') if part]
        if components and components[-1].endswith('.git'):
            components[-1] = components[-1][:-4]
        if host and components and not parsed_username(raw):
            return host.lower() + '/' + '/'.join(components).lower()
    return None


def _repo_identity(repository: pathlib.Path) -> str:
    slug = remote_repository_slug(repository)
    if slug:
        return sha256_bytes(('remote:' + slug).encode('utf-8'))
    # Repositories without a canonical origin still get a local-only identity.
    return sha256_bytes(b'local:' + os.fsencode(str(common_dir(repository))))


def parsed_username(raw: str) -> bool:
    """Reject credential-bearing remote URLs from the repository identity."""
    if '://' not in raw:
        return False
    try:
        return urllib.parse.urlsplit(raw).username is not None
    except ValueError:
        return True


def _state_files(state_dir: pathlib.Path, replacement_path: pathlib.Path,
                 replacement: bytes,
                 additional_files: dict[str, tuple[bytes, int]] | None = None,
                 remove_files: set[str] | None = None) -> dict[str, tuple[bytes, int]]:
    state_dir = state_dir.resolve(strict=False)
    replacement_path = replacement_path.resolve(strict=False)
    try:
        replacement_rel = replacement_path.relative_to(state_dir).as_posix()
    except ValueError:
        raise SnapshotError('snapshot-state-path-outside-root') from None
    files: dict[str, tuple[bytes, int]] = {}
    if state_dir.exists():
        if state_dir.is_symlink() or not state_dir.is_dir():
            raise SnapshotError('snapshot-state-root-unsafe')
        for directory, dirnames, filenames in os.walk(state_dir, followlinks=False):
            base = pathlib.Path(directory)
            for name in list(dirnames):
                item = base / name
                if item.is_symlink():
                    raise SnapshotError('snapshot-state-tree-unsafe')
            for name in filenames:
                item = base / name
                if item.is_symlink() or not item.is_file():
                    raise SnapshotError('snapshot-state-tree-unsafe')
                rel = item.relative_to(state_dir).as_posix()
                raw = item.read_bytes()
                mode = item.stat().st_mode & 0o777
                if mode not in _FILE_MODES:
                    raise SnapshotError('snapshot-state-mode-unsafe')
                files[rel] = (raw, mode)
    files[replacement_rel] = (replacement, 0o600)
    for relative, (payload, mode) in (additional_files or {}).items():
        if (not isinstance(relative, str) or not relative or relative.startswith('/') or
                any(part in {'', '.', '..'} for part in relative.split('/')) or
                relative == replacement_rel or mode not in _FILE_MODES or
                not isinstance(payload, bytes)):
            raise SnapshotError('snapshot-additional-file-invalid')
        files[relative] = (payload, mode)
    for relative in remove_files or set():
        if (not isinstance(relative, str) or not relative or relative.startswith('/') or
                any(part in {'', '.', '..'} for part in relative.split('/')) or
                relative == replacement_rel or relative in (additional_files or {})):
            raise SnapshotError('snapshot-remove-file-invalid')
        files.pop(relative, None)
    return files


def _file_descriptors(files: dict[str, tuple[bytes, int]]) -> dict[str, tuple[str, int, int]]:
    return {path: (sha256_bytes(payload), len(payload), mode)
            for path, (payload, mode) in files.items()}


def _file_descriptor_rows(files: dict[str, tuple[bytes, int]]) -> list[dict[str, Any]]:
    return [{'path': path, 'sha256': digest, 'bytes': size, 'mode': mode}
            for path, (digest, size, mode) in sorted(_file_descriptors(files).items())]


def _publish_immutable_file(state_dir: pathlib.Path, relative: str,
                            payload: bytes, mode: int) -> None:
    target = state_dir.joinpath(*pathlib.PurePosixPath(relative).parts)
    try:
        target.relative_to(state_dir)
    except ValueError:
        raise SnapshotError('snapshot-additional-file-invalid') from None
    parent = target.parent
    chain = []
    while parent != state_dir:
        chain.append(parent)
        parent = parent.parent
    for directory in reversed(chain):
        if directory.exists():
            if directory.is_symlink() or not directory.is_dir():
                raise SnapshotError('snapshot-additional-file-unsafe')
        else:
            directory.mkdir()
            _fsync_directory(directory.parent)
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file():
            raise SnapshotError('snapshot-additional-file-unsafe')
        if target.read_bytes() != payload or target.stat().st_mode & 0o777 != mode:
            raise SnapshotError('snapshot-immutable-file-conflict')
        return
    fd, temp_name = tempfile.mkstemp(prefix=target.name + '.', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
        _fsync_directory(target.parent)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _write_blob(repository: pathlib.Path, payload: bytes) -> str:
    oid = os.fsdecode(_git(repository, 'hash-object', '-w', '--stdin', input_bytes=payload).strip())
    if not _HEX.fullmatch(oid):
        raise SnapshotError('snapshot-object-invalid')
    _fsync_git_object(repository, oid)
    return oid


def _write_blobs(repository: pathlib.Path, payloads: list[bytes]) -> list[str]:
    """Write a snapshot's blobs in one Git process, then fsync each new object."""
    if not payloads:
        return []
    with tempfile.TemporaryDirectory(prefix='agent-state-snapshot-blobs.') as temporary:
        root = pathlib.Path(temporary)
        paths = []
        for index, payload in enumerate(payloads):
            path = root / f'{index:08d}'
            path.write_bytes(payload)
            paths.append(path)
        result = subprocess.run(['git', '-C', str(repository), 'hash-object', '-w', '--stdin-paths'],
                                input=b''.join(os.fsencode(path) + b'\n' for path in paths),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            raise SnapshotError('snapshot-git-operation-failed')
        oids = result.stdout.decode('ascii', errors='strict').splitlines()
        if len(oids) != len(payloads) or any(not _HEX.fullmatch(oid) for oid in oids):
            raise SnapshotError('snapshot-object-invalid')
        for oid in set(oids):
            _fsync_git_object(repository, oid)
        return oids


def _tree(repository: pathlib.Path, entries: dict[str, tuple[str, str]]) -> str:
    """Build a Git tree from POSIX paths mapped to (mode, blob_oid)."""
    nested: dict[str, Any] = {}
    for path, (mode, oid) in entries.items():
        parts = path.split('/')
        if not path or path.startswith('/') or any(part in {'', '.', '..'} for part in parts):
            raise SnapshotError('snapshot-path-invalid')
        node = nested
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise SnapshotError('snapshot-path-collision')
            node = child
        if parts[-1] in node:
            raise SnapshotError('snapshot-path-collision')
        node[parts[-1]] = (mode, oid)

    def build(node: dict[str, Any]) -> str:
        rows: list[tuple[bytes, bytes]] = []
        for name, value in node.items():
            raw_name = os.fsencode(name)
            if isinstance(value, dict):
                oid = build(value)
                row = b'040000 tree ' + oid.encode('ascii') + b'\t' + raw_name + b'\0'
                sort_name = raw_name + b'/'
            else:
                mode, oid = value
                object_type = 'tree' if mode == '040000' else 'blob'
                row = mode.encode('ascii') + b' ' + object_type.encode('ascii') + b' ' + oid.encode('ascii') + b'\t' + raw_name + b'\0'
                sort_name = raw_name
            rows.append((sort_name, row))
        rows.sort(key=lambda item: item[0])
        oid = os.fsdecode(_git(repository, 'mktree', '-z', input_bytes=b''.join(row for _, row in rows)).strip())
        _fsync_git_object(repository, oid)
        return oid

    return build(nested)


def _commit_snapshot(repository: pathlib.Path, state_files: dict[str, tuple[bytes, int]],
                     manifest: dict[str, Any]) -> str:
    paths = sorted(state_files)
    manifest_bytes = json.dumps(manifest, sort_keys=True, indent=2).encode() + b'\n'
    oids = _write_blobs(repository, [state_files[path][0] for path in paths] + [manifest_bytes])
    entries = {path: ('100755' if state_files[path][1] & 0o111 else '100644', oid)
               for path, oid in zip(paths, oids[:-1], strict=True)}
    state_tree = _tree(repository, entries)
    manifest_oid = oids[-1]
    root_tree = _tree(repository, {'.agent-state': ('040000', state_tree),
                                   'manifest.json': ('100644', manifest_oid)})
    commit = _git(repository, '-c', 'user.name=Showcase Agent State Snapshot',
                   '-c', 'user.email=agent-state-snapshot@invalid', 'commit-tree', root_tree,
                   '-m', f"agent-state snapshot {manifest['snapshot_id']}").strip()
    value = os.fsdecode(commit)
    if not _HEX.fullmatch(value):
        raise SnapshotError('snapshot-commit-invalid')
    _fsync_git_object(repository, value)
    return value


def _fsync_git_object(repository: pathlib.Path, oid: str) -> None:
    common = common_dir(repository)
    path = common / 'objects' / oid[:2] / oid[2:]
    if path.is_symlink():
        raise SnapshotError('snapshot-object-not-durable')
    if not path.is_file():
        parent = path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            if parent.is_symlink() or not parent.is_dir():
                raise SnapshotError('snapshot-object-not-durable')
            batch = _git(repository, 'cat-file', '--batch', input_bytes=oid.encode('ascii') + b'\n')
            end = batch.find(b'\n')
            if end < 0:
                raise SnapshotError('snapshot-object-not-durable')
            header = batch[:end].decode('ascii').split(' ')
            if len(header) != 3 or header[0] != oid or header[1] not in {'blob', 'tree', 'commit', 'tag'}:
                raise SnapshotError('snapshot-object-not-durable')
            size = int(header[2])
            payload = batch[end + 1:end + 1 + size]
            if len(payload) != size or batch[end + 1 + size:end + 2 + size] != b'\n':
                raise SnapshotError('snapshot-object-not-durable')
            digest = hashlib.sha1 if len(oid) == 40 else hashlib.sha256
            if digest(header[1].encode() + b' ' + str(size).encode() + b'\0' + payload).hexdigest() != oid:
                raise SnapshotError('snapshot-object-integrity-failed')
            if not path.exists():
                fd, temp_name = tempfile.mkstemp(prefix=oid[2:] + '.', dir=parent)
                try:
                    with os.fdopen(fd, 'wb') as handle:
                        handle.write(zlib.compress(header[1].encode() + b' ' + str(size).encode() + b'\0' + payload))
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_name, path)
                    _fsync_directory(parent)
                finally:
                    if os.path.exists(temp_name):
                        os.unlink(temp_name)
        except (OSError, UnicodeDecodeError, ValueError):
            raise SnapshotError('snapshot-object-not-durable') from None
    if path.is_symlink() or not path.is_file():
        raise SnapshotError('snapshot-object-not-durable')
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_directory(path.parent)
    except OSError:
        raise SnapshotError('snapshot-object-not-durable') from None


def _fsync_ref(repository: pathlib.Path, ref: str) -> None:
    common = common_dir(repository)
    path = common / pathlib.PurePosixPath(ref)
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise SnapshotError('snapshot-ref-unsafe')
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            raise SnapshotError('snapshot-ref-not-durable') from None
    else:
        # A ref may have been packed by external Git maintenance. Deleting or
        # changing it must flush packed-refs as well as the surrounding directory.
        packed = common / 'packed-refs'
        if packed.exists():
            if packed.is_symlink() or not packed.is_file():
                raise SnapshotError('snapshot-ref-unsafe')
            try:
                fd = os.open(packed, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except OSError:
                raise SnapshotError('snapshot-ref-not-durable') from None
    parent = path.parent
    while parent == common or common in parent.parents:
        # Git removes empty loose-ref directories after update-ref -d. In that
        # case fsync the nearest surviving ancestor; the containing directory
        # entry is the one whose deletion must be durable.
        if parent.exists():
            if parent.is_symlink() or not parent.is_dir():
                raise SnapshotError('snapshot-ref-unsafe')
            _fsync_directory(parent)
        if parent == common:
            break
        parent = parent.parent


def _read_commit(repository: pathlib.Path, ref_or_commit: str) -> tuple[str, dict[str, bytes]]:
    try:
        commit = os.fsdecode(_git(repository, 'rev-parse', '--verify', f'{ref_or_commit}^{{commit}}').strip())
        # A feature checkout may be nested below the repository root. Without
        # --full-tree Git treats its cwd as an implicit pathspec and omits the
        # snapshot manifest at the root of the commit.
        raw = _git(repository, 'ls-tree', '--full-tree', '-r', '-z', commit)
    except SnapshotError:
        raise
    files: dict[str, bytes] = {}
    records: list[tuple[str, str, str]] = []
    for record in raw.split(b'\0'):
        if not record:
            continue
        try:
            meta, raw_path = record.split(b'\t', 1)
            mode, kind, oid = meta.decode('ascii').split(' ')
            path = os.fsdecode(raw_path)
        except (ValueError, UnicodeDecodeError):
            raise SnapshotError('snapshot-tree-invalid') from None
        if kind != 'blob' or mode not in {'100644', '100755'}:
            raise SnapshotError('snapshot-tree-invalid')
        if path.startswith('/') or any(part in {'', '.', '..'} for part in path.split('/')):
            raise SnapshotError('snapshot-tree-invalid')
        records.append((path, oid, kind))
    batch = _git(repository, 'cat-file', '--batch',
                 input_bytes=b''.join(oid.encode('ascii') + b'\n' for _path, oid, _kind in records))
    cursor = 0
    for path, expected_oid, expected_kind in records:
        end = batch.find(b'\n', cursor)
        if end < 0:
            raise SnapshotError('snapshot-object-missing')
        try:
            header = batch[cursor:end].decode('ascii', errors='strict').split(' ')
        except UnicodeDecodeError:
            raise SnapshotError('snapshot-object-invalid') from None
        if len(header) != 3 or header[0] != expected_oid or header[1] != expected_kind:
            raise SnapshotError('snapshot-object-invalid')
        try:
            size = int(header[2])
        except ValueError:
            raise SnapshotError('snapshot-object-invalid') from None
        cursor = end + 1
        payload_end = cursor + size
        if payload_end >= len(batch) or batch[payload_end:payload_end + 1] != b'\n':
            raise SnapshotError('snapshot-object-truncated')
        files[path] = batch[cursor:payload_end]
        cursor = payload_end + 1
    if cursor != len(batch):
        raise SnapshotError('snapshot-object-trailing-data')
    return commit, files


def _manifest(repository: pathlib.Path, commit: str,
              expected_repository_id: str | None = None) -> tuple[dict[str, Any], dict[str, bytes]]:
    _commit, files = _read_commit(repository, commit)
    raw = files.pop('manifest.json', None)
    if raw is None:
        raise SnapshotError('snapshot-manifest-missing')
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SnapshotError('snapshot-manifest-invalid') from None
    if not isinstance(manifest, dict) or manifest.get('schema_version') != SCHEMA_VERSION:
        raise SnapshotError('snapshot-manifest-invalid')
    expected_repository_id = expected_repository_id or _repo_identity(repository)
    if manifest.get('repository_id') != expected_repository_id:
        raise SnapshotError('snapshot-scope-mismatch')
    snapshot_id = manifest.get('snapshot_id')
    core = {key: value for key, value in manifest.items() if key != 'snapshot_id'}
    if snapshot_id != 'sha256:' + sha256_bytes(canonical_json(core)):
        raise SnapshotError('snapshot-manifest-invalid')
    entries = manifest.get('files')
    if not isinstance(entries, list):
        raise SnapshotError('snapshot-manifest-invalid')
    expected: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise SnapshotError('snapshot-manifest-invalid')
        path = entry.get('path')
        if (not isinstance(path, str) or not path or path.startswith('/') or
                any(part in {'', '.', '..'} for part in path.split('/')) or path in expected):
            raise SnapshotError('snapshot-manifest-invalid')
        expected[path] = entry
    prior_entries = manifest.get('prior_files')
    if 'prior_files' in manifest:
        if not isinstance(prior_entries, list):
            raise SnapshotError('snapshot-manifest-invalid')
        prior_paths = set()
        for entry in prior_entries:
            if not isinstance(entry, dict):
                raise SnapshotError('snapshot-manifest-invalid')
            path = entry.get('path')
            if (not isinstance(path, str) or not path or path.startswith('/') or
                    any(part in {'', '.', '..'} for part in path.split('/')) or
                    path in prior_paths or
                    not isinstance(entry.get('sha256'), str) or
                    re.fullmatch(r'[0-9a-f]{64}', entry['sha256']) is None or
                    type(entry.get('bytes')) is not int or entry['bytes'] < 0 or
                    entry.get('mode') not in _FILE_MODES):
                raise SnapshotError('snapshot-manifest-invalid')
            prior_paths.add(path)
    state_files = {path[len('.agent-state/'):]: value for path, value in files.items()
                   if path.startswith('.agent-state/')}
    if set(state_files) != set(expected):
        raise SnapshotError('snapshot-content-mismatch')
    for path, entry in expected.items():
        raw_value = state_files[path]
        if (entry.get('bytes') != len(raw_value) or entry.get('sha256') != sha256_bytes(raw_value) or
                entry.get('mode') not in _FILE_MODES):
            raise SnapshotError('snapshot-content-mismatch')
    state_path = manifest.get('state_path')
    state_revision = manifest.get('state_revision')
    generation = manifest.get('feature_generation')
    if (not isinstance(state_path, str) or not state_path or state_path.startswith('/') or
            any(part in {'', '.', '..'} for part in state_path.split('/')) or
            state_path not in state_files or
            not isinstance(state_revision, str) or re.fullmatch(r'[0-9a-f]{64}', state_revision) is None or
            sha256_bytes(state_files[state_path]) != state_revision or
            type(generation) is not int or generation < 1):
        raise SnapshotError('snapshot-state-binding-invalid')
    try:
        state_doc = json.loads(state_files[state_path])
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SnapshotError('snapshot-state-binding-invalid') from None
    if (not isinstance(state_doc, dict) or state_doc.get('feature') != manifest.get('feature') or
            state_doc.get('feature_generation', 1) != generation):
        raise SnapshotError('snapshot-state-binding-invalid')
    if any(path != 'manifest.json' and not path.startswith('.agent-state/') for path in files):
        raise SnapshotError('snapshot-content-mismatch')
    return manifest, state_files


def _list_snapshot_refs(repository: pathlib.Path, feature: str,
                        expected_repository_id: str | None = None) -> list[tuple[str, str, dict[str, Any]]]:
    prefix = _snapshots_prefix(feature)
    raw = _git(repository, 'for-each-ref', '--format=%(refname) %(objectname)', prefix)
    expected_repository_id = expected_repository_id or _repo_identity(repository)
    refs = []
    for line in raw.decode().splitlines():
        ref, commit = line.split(' ', 1)
        manifest, _ = _manifest(repository, commit, expected_repository_id)
        if manifest.get('feature') != feature:
            raise SnapshotError('snapshot-scope-mismatch')
        refs.append((ref, commit, manifest))
    return refs


def _prepared_refs(repository: pathlib.Path) -> list[tuple[str, str, str]]:
    raw = _git(repository, 'for-each-ref', '--format=%(refname) %(objectname)',
                'refs/agent-state-prepared/')
    rows = []
    for line in raw.decode('ascii').splitlines():
        ref, commit = line.split(' ', 1)
        match = re.fullmatch(r'refs/agent-state-prepared/([A-Za-z0-9][A-Za-z0-9._-]{0,127})/([0-9a-f]{64})', ref)
        if match is None or not _HEX.fullmatch(commit):
            raise SnapshotError('snapshot-prepared-ref-invalid')
        rows.append((ref, match.group(1), commit))
    return rows


def prepared_snapshot_exists(repository: pathlib.Path) -> bool:
    return bool(_prepared_refs(repository))


def _actual_state_files(state_dir: pathlib.Path) -> dict[str, tuple[bytes, int]]:
    if not os.path.lexists(state_dir):
        return {}
    if state_dir.is_symlink() or not state_dir.is_dir():
        raise SnapshotError('snapshot-state-root-unsafe')
    actual: dict[str, tuple[bytes, int]] = {}
    for directory, dirnames, filenames in os.walk(state_dir, followlinks=False):
        base = pathlib.Path(directory)
        for name in dirnames:
            if (base / name).is_symlink():
                raise SnapshotError('snapshot-state-tree-unsafe')
        for name in filenames:
            item = base / name
            if item.is_symlink() or not item.is_file():
                raise SnapshotError('snapshot-state-tree-unsafe')
            mode = item.stat().st_mode & 0o777
            if mode not in _FILE_MODES:
                raise SnapshotError('snapshot-state-mode-unsafe')
            actual[item.relative_to(state_dir).as_posix()] = (item.read_bytes(), mode)
    return actual


def _snapshot_tree_files(repository: pathlib.Path, commit: str,
                         repository_id: str) -> tuple[dict[str, Any], dict[str, tuple[bytes, int]]]:
    manifest, raw_files = _manifest(repository, commit, repository_id)
    modes = {entry['path']: entry['mode'] for entry in manifest['files']}
    return manifest, {path: (payload, modes[path]) for path, payload in raw_files.items()}


def _atomic_write_state_file(state_dir: pathlib.Path, relative: str,
                             payload: bytes, mode: int) -> None:
    target = state_dir.joinpath(*pathlib.PurePosixPath(relative).parts)
    parent = target.parent
    missing = []
    while parent != state_dir and not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir()
        _fsync_directory(directory.parent)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise SnapshotError('snapshot-state-tree-unsafe')
    fd, temp_name = tempfile.mkstemp(prefix=target.name + '.redo.', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
        _fsync_directory(target.parent)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _advance_snapshot_refs(repository: pathlib.Path, feature: str, commit: str,
                           prepared_ref: str) -> None:
    _validate_feature(feature)
    manifest, _ = _manifest(repository, commit)
    if manifest.get('feature') != feature:
        raise SnapshotError('snapshot-scope-mismatch')
    archive_ref = f'{_snapshots_prefix(feature)}{manifest["snapshot_id"].split(":", 1)[1]}'
    for ref in (archive_ref, current_ref(feature), current_repository_ref()):
        current = subprocess.run(['git', '-C', str(repository), 'rev-parse', '--verify', ref],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        if current.returncode == 0 and current.stdout.strip().decode('ascii') != commit:
            if ref == archive_ref:
                raise SnapshotError('snapshot-archive-ref-conflict')
        _git(repository, 'update-ref', ref, commit)
        _fsync_ref(repository, ref)
    _git(repository, 'update-ref', '-d', prepared_ref)
    _fsync_ref(repository, prepared_ref)


def recover_prepared_snapshots(repository: pathlib.Path, state_dir: pathlib.Path) -> bool:
    """Redo the sole interrupted snapshot transaction before lifecycle state is read.

    Caller holds the repository-wide lifecycle lock. Disk files may contain old or
    prepared versions (including a subset of newly published immutable records),
    but any third value or missing old file is treated as an external conflict.
    """
    repository = pathlib.Path(repository).resolve()
    state_dir = pathlib.Path(os.path.abspath(state_dir))
    prepared = _prepared_refs(repository)
    if not prepared:
        return False
    if len(prepared) != 1:
        raise SnapshotError('snapshot-prepared-ambiguous')
    prepared_ref, feature, commit = prepared[0]
    repository_id = _repo_identity(repository)
    manifest, new_files = _snapshot_tree_files(repository, commit, repository_id)
    if manifest.get('feature') != feature:
        raise SnapshotError('snapshot-prepared-scope-mismatch')
    old_files: dict[str, tuple[bytes, int]] = {}
    prior_descriptors = None
    if 'prior_files' in manifest:
        prior_descriptors = {
            entry['path']: (entry['sha256'], entry['bytes'], entry['mode'])
            for entry in manifest['prior_files']
        }
    has_current_snapshot = current_repository_ref_exists(repository)
    if has_current_snapshot:
        old_commit = current_repository_snapshot_commit(repository)
        if old_commit is None:
            raise SnapshotError('snapshot-current-ref-invalid')
        _old_manifest, old_files = _snapshot_tree_files(repository, old_commit, repository_id)
        if (old_commit != commit and prior_descriptors is not None and
                _file_descriptors(old_files) != prior_descriptors):
            raise SnapshotError('snapshot-prepared-state-conflict')
        if prior_descriptors is None:
            prior_descriptors = _file_descriptors(old_files)
    elif prior_descriptors is None:
        # Older prepared records did not capture the pre-transaction tree. With
        # no acknowledged snapshot, accepting a partial match could overwrite
        # legacy authority whose contents are no longer knowable.
        raise SnapshotError('snapshot-prepared-prior-state-unavailable')
    actual = _actual_state_files(state_dir)
    actual_descriptors = _file_descriptors(actual)
    target_descriptors = _file_descriptors(new_files)
    all_paths = set(actual) | set(prior_descriptors or {}) | set(new_files)
    for path in all_paths:
        value = actual_descriptors.get(path)
        prior = (prior_descriptors or {}).get(path)
        target = target_descriptors.get(path)
        if value is None:
            if prior is not None and target is not None:
                raise SnapshotError('snapshot-prepared-state-conflict')
            continue
        if value != prior and value != target:
            raise SnapshotError('snapshot-prepared-state-conflict')

    # If no disk write began, abandon the unacknowledged redo record. Otherwise
    # complete the exact prepared tree before advancing all restore refs.
    if actual_descriptors == prior_descriptors:
        _git(repository, 'update-ref', '-d', prepared_ref)
        _fsync_ref(repository, prepared_ref)
        return True

    for path in sorted(set(prior_descriptors or {}) - set(new_files)):
        target = state_dir.joinpath(*pathlib.PurePosixPath(path).parts)
        if target.exists():
            target.unlink()
            _fsync_directory(target.parent)
    for path, (payload, mode) in sorted(new_files.items()):
        if actual.get(path) != (payload, mode):
            _atomic_write_state_file(state_dir, path, payload, mode)
    if _actual_state_files(state_dir) != new_files:
        raise SnapshotError('snapshot-prepared-postcheck-failed')
    _advance_snapshot_refs(repository, feature, commit, prepared_ref)
    return True


def discard_prepared_snapshot(repository: pathlib.Path, state_dir: pathlib.Path,
                              feature: str, expected_commit: str,
                              operator: str) -> dict[str, str]:
    """Audibly discard an unacknowledged prepared write when state is absent.

    Caller holds the repository-wide lifecycle lock. The audit record and the
    compare-and-swap deletion of the prepared ref are one Git ref transaction.
    The operator value is provenance, not an authenticated identity.
    """
    repository = pathlib.Path(repository).resolve()
    state_dir = pathlib.Path(os.path.abspath(state_dir))
    _validate_feature(feature)
    if os.path.lexists(state_dir):
        raise SnapshotError('snapshot-prepared-state-present')
    if not isinstance(operator, str) or not operator.strip() or len(operator) > 256 or any(
            ord(character) < 0x20 or ord(character) == 0x7f for character in operator):
        raise SnapshotError('snapshot-discard-operator-invalid')
    if not isinstance(expected_commit, str) or not _HEX.fullmatch(expected_commit):
        raise SnapshotError('snapshot-discard-commit-invalid')

    audit_ref = f'refs/agent-state-discarded/{feature}/{expected_commit}'
    audit_lookup = subprocess.run(
        ['git', '-C', str(repository), 'show-ref', '--verify', '--quiet', audit_ref],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if audit_lookup.returncode == 0:
        audit_commit = os.fsdecode(_git(repository, 'rev-parse', '--verify', audit_ref).strip())
        _resolved, audit_files = _read_commit(repository, audit_commit)
        try:
            record = json.loads(audit_files['discard.json'])
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
            raise SnapshotError('snapshot-discard-audit-invalid') from None
        if (set(audit_files) != {'discard.json'} or not isinstance(record, dict) or
                record.get('action') != 'discard-prepared-snapshot' or
                record.get('feature') != feature or record.get('prepared_commit') != expected_commit or
                not isinstance(record.get('prepared_ref'), str) or
                re.fullmatch(rf'{re.escape(_prepared_prefix(feature))}[0-9a-f]{{64}}',
                             record['prepared_ref']) is None or
                not isinstance(record.get('operator'), str) or not record['operator'].strip() or
                len(record['operator']) > 256 or not isinstance(record.get('recorded_at'), str)):
            raise SnapshotError('snapshot-discard-audit-invalid')
        return {'audit_ref': audit_ref, 'audit_commit': audit_commit,
                'prepared_commit': expected_commit, 'operator': record['operator'],
                'already_discarded': 'true'}
    if audit_lookup.returncode != 1:
        raise SnapshotError('snapshot-discard-audit-unavailable')

    prepared = _prepared_refs(repository)
    if len(prepared) != 1:
        raise SnapshotError('snapshot-prepared-count-invalid')
    prepared_ref, prepared_feature, prepared_commit = prepared[0]
    if prepared_feature != feature:
        raise SnapshotError('snapshot-prepared-scope-mismatch')
    if prepared_commit != expected_commit:
        raise SnapshotError('snapshot-prepared-commit-stale')
    manifest, _ = _snapshot_tree_files(repository, prepared_commit, _repo_identity(repository))
    if manifest.get('feature') != feature:
        raise SnapshotError('snapshot-prepared-scope-mismatch')

    record_bytes = canonical_json({
        'schema_version': 1,
        'action': 'discard-prepared-snapshot',
        'feature': feature,
        'prepared_ref': prepared_ref,
        'prepared_commit': prepared_commit,
        'operator': operator.strip(),
        'recorded_at': dt.datetime.now(dt.timezone.utc).isoformat(),
    }) + b'\n'
    record_blob = _write_blob(repository, record_bytes)
    audit_tree = _tree(repository, {'discard.json': ('100644', record_blob)})
    audit_commit = os.fsdecode(_git(
        repository, '-c', 'user.name=Showcase Agent State Audit',
        '-c', 'user.email=agent-state-audit@invalid', 'commit-tree', audit_tree,
        '-m', f'discard prepared snapshot {prepared_commit}').strip())
    if not _HEX.fullmatch(audit_commit):
        raise SnapshotError('snapshot-discard-audit-invalid')
    _fsync_git_object(repository, audit_commit)
    message = f'agent-state discard operator={operator.strip()} prepared={prepared_commit}'
    transaction = (f'start\ncreate {audit_ref} {audit_commit}\n'
                   f'delete {prepared_ref} {prepared_commit}\nprepare\ncommit\n').encode('ascii')
    try:
        _git(repository, 'update-ref', '--stdin', '--create-reflog', '-m', message,
             input_bytes=transaction)
    except SnapshotError:
        raise SnapshotError('snapshot-prepared-commit-stale') from None
    _fsync_ref(repository, audit_ref)
    _fsync_ref(repository, prepared_ref)
    return {'audit_ref': audit_ref, 'audit_commit': audit_commit,
            'prepared_commit': prepared_commit, 'operator': operator.strip(),
            'already_discarded': 'false'}


def _retention_selection(refs: list[tuple[str, str, dict[str, Any]]], *, cap: int | None = None) -> set[str]:
    if cap is None:
        cap = SNAPSHOT_LIMIT
    if len(refs) <= cap:
        return {ref for ref, _, _ in refs}
    ordered = sorted(refs, key=lambda row: (row[2].get('created_at', ''), row[0]))
    first_by_epoch: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for row in ordered:
        epoch = row[2].get('epoch_id')
        if not isinstance(epoch, str) or not epoch:
            raise SnapshotError('snapshot-epoch-invalid')
        first_by_epoch.setdefault(epoch, row)
    keep = {row[0] for row in first_by_epoch.values()}
    keep.add(ordered[-1][0])
    if len(keep) > cap:
        raise SnapshotError('snapshot-retention-cap-reached')
    for ref, _commit, _manifest in reversed(ordered):
        if len(keep) >= cap:
            break
        keep.add(ref)
    return keep


def _unchanged_snapshot_result(repository: pathlib.Path, state_dir: pathlib.Path,
                               state_path: pathlib.Path, state: dict[str, Any], state_payload: bytes,
                               repository_id: str) -> dict[str, Any] | None:
    """Return the current snapshot when it already captures the exact disk tree.

    This avoids a Git tree/commit/ref transaction for an idempotent state save.
    The full authority tree and snapshot manifest are still verified; any pending
    redo record or byte/mode difference forces the normal transaction path.
    """
    feature = state['feature']
    if _prepared_refs(repository):
        return None
    if not current_ref_exists(repository, feature):
        return None
    commit = current_snapshot_commit(repository, feature)
    if commit is None:
        raise SnapshotError('snapshot-current-ref-invalid')
    manifest, snapshot_files = _snapshot_tree_files(repository, commit, repository_id)
    if manifest.get('feature') != feature:
        raise SnapshotError('snapshot-scope-mismatch')
    actual_files = _actual_state_files(state_dir)
    if not current_repository_ref_exists(repository):
        return None
    repository_commit = current_repository_snapshot_commit(repository)
    if repository_commit is None:
        raise SnapshotError('snapshot-current-repository-ref-invalid')
    _repository_manifest, repository_files = _snapshot_tree_files(
        repository, repository_commit, repository_id)
    if repository_files != actual_files:
        return None
    relative_state_path = state_path.relative_to(state_dir).as_posix()
    if (snapshot_files != actual_files or
            actual_files.get(relative_state_path) != (state_payload, 0o600) or
            manifest.get('state_path') != relative_state_path or
            manifest.get('feature_generation') != state.get('feature_generation', 1)):
        return None
    epoch = state.get('lifecycle_epoch_id')
    if epoch is None:
        created = state.get('created_at')
        epoch = f'legacy:{created}' if isinstance(created, str) and created else 'legacy:unknown'
    if manifest.get('epoch_id') != epoch:
        return None
    return {'commit': commit, 'snapshot_id': manifest['snapshot_id'],
            'state_revision': manifest['state_revision'], 'ref': current_ref(feature)}


def commit_state_snapshot(repository: pathlib.Path, state_dir: pathlib.Path,
                          state_path: pathlib.Path, state_payload: bytes,
                          state: dict[str, Any], *, additional_files: dict[str, tuple[bytes, int]] | None = None,
                          remove_files: set[str] | None = None,
                          fault_injector=None) -> dict[str, Any]:
    """Publish a snapshot, atomically replace state, then advance its restore ref.

    Caller holds ``lifecycle_lock_path(repository)`` for the whole transaction.
    """
    repository = pathlib.Path(repository).resolve()
    state_dir = pathlib.Path(os.path.abspath(state_dir))
    state_path = pathlib.Path(os.path.abspath(state_path))
    if state_dir.is_symlink() or state_path.is_symlink():
        raise SnapshotError('snapshot-state-root-unsafe')
    feature = state.get('feature')
    _validate_feature(feature)
    try:
        parsed_state = json.loads(state_payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SnapshotError('snapshot-state-invalid') from None
    if parsed_state != state:
        raise SnapshotError('snapshot-state-invalid')
    if not additional_files and not remove_files:
        unchanged = _unchanged_snapshot_result(repository, state_dir, state_path, state,
                                                state_payload,
                                                _repo_identity(repository))
        if unchanged is not None:
            return unchanged
    files = _state_files(state_dir, state_path, state_payload, additional_files, remove_files)
    file_entries = [{'path': name, 'sha256': sha256_bytes(value), 'bytes': len(value), 'mode': mode}
                    for name, (value, mode) in sorted(files.items())]
    repository_id = _repo_identity(repository)
    existing = _list_snapshot_refs(repository, feature, repository_id)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    epoch = state.get('lifecycle_epoch_id')
    if not isinstance(epoch, str) or not epoch:
        created = state.get('created_at')
        epoch = f'legacy:{created}' if isinstance(created, str) and created else 'legacy:unknown'
    core = {
        'schema_version': SCHEMA_VERSION,
        'feature': feature,
        'repository_id': repository_id,
        'state_revision': sha256_bytes(state_payload),
        'state_path': state_path.relative_to(state_dir).as_posix(),
        'feature_generation': state.get('feature_generation', 1),
        'epoch_id': epoch,
        'created_at': now,
        'prior_files': _file_descriptor_rows(_actual_state_files(state_dir)),
        'files': file_entries,
    }
    manifest = {**core, 'snapshot_id': 'sha256:' + sha256_bytes(canonical_json(core))}
    commit = _commit_snapshot(repository, files, manifest)
    digest = manifest['snapshot_id'].split(':', 1)[1]
    prepared = f'{_prepared_prefix(feature)}{digest}'
    snapshot_ref = f'{_snapshots_prefix(feature)}{digest}'
    # Preflight pins before creating any visible state or durable ref.
    prospective = existing + [(snapshot_ref, commit, manifest)]
    _retention_selection(prospective)
    _git(repository, 'update-ref', prepared, commit)
    _fsync_ref(repository, prepared)
    if fault_injector:
        fault_injector('after-snapshot-publication')

    state_dir_existed = state_dir.exists()
    state_dir.mkdir(parents=True, exist_ok=True)
    if not state_dir_existed:
        _fsync_directory(state_dir.parent)
    for relative, (payload, mode) in (additional_files or {}).items():
        _publish_immutable_file(state_dir, relative, payload, mode)
    if fault_injector:
        fault_injector('after-additional-files')
    for relative in sorted(remove_files or set()):
        target = state_dir.joinpath(*pathlib.PurePosixPath(relative).parts)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise SnapshotError('snapshot-remove-file-unsafe')
        if target.exists():
            target.unlink()
            _fsync_directory(target.parent)
    fd, temp_name = tempfile.mkstemp(prefix=state_path.name + '.', dir=state_dir)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(state_payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, state_path)
        _fsync_directory(state_dir)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    if fault_injector:
        fault_injector('after-state-replacement')

    # The immutable archive ref exists before the current restore authority advances.
    _git(repository, 'update-ref', snapshot_ref, commit)
    _fsync_ref(repository, snapshot_ref)
    if fault_injector:
        fault_injector('after-archive-ref')
    # Only this ref is restore authority, and it moves after the state replacement is durable.
    _git(repository, 'update-ref', current_ref(feature), commit)
    _fsync_ref(repository, current_ref(feature))
    if fault_injector:
        fault_injector('after-feature-current-ref')
    _git(repository, 'update-ref', current_repository_ref(), commit)
    _fsync_ref(repository, current_repository_ref())
    if fault_injector:
        fault_injector('after-repository-current-ref')
    _git(repository, 'update-ref', '-d', prepared)
    _fsync_ref(repository, prepared)
    refs = _list_snapshot_refs(repository, feature, repository_id)
    retained = _retention_selection(refs)
    for ref, _oid, _manifest in refs:
        if ref not in retained:
            _git(repository, 'update-ref', '-d', ref)
            _fsync_ref(repository, ref)
    return {'commit': commit, 'snapshot_id': manifest['snapshot_id'],
            'state_revision': manifest['state_revision'], 'ref': current_ref(feature)}


def _fsync_directory(path: pathlib.Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def current_snapshot_commit(repository: pathlib.Path, feature: str) -> str | None:
    try:
        return os.fsdecode(_git(repository, 'rev-parse', '--verify', current_ref(feature)).strip())
    except SnapshotError:
        return None


def current_repository_snapshot_commit(repository: pathlib.Path) -> str | None:
    try:
        return os.fsdecode(_git(repository, 'rev-parse', '--verify',
                               f'{current_repository_ref()}^{{commit}}').strip())
    except SnapshotError:
        return None


def current_ref_exists(repository: pathlib.Path, feature: str) -> bool:
    """Treat malformed refs as present so callers fail closed instead of bootstrapping."""
    return _ref_exists(repository, current_ref(feature))


def current_repository_ref_exists(repository: pathlib.Path) -> bool:
    return _ref_exists(repository, current_repository_ref())


def _ref_exists(repository: pathlib.Path, ref: str) -> bool:
    common = common_dir(repository)
    loose = common.joinpath(*pathlib.PurePosixPath(ref).parts)
    if os.path.lexists(loose):
        return True
    packed = common / 'packed-refs'
    if os.path.lexists(packed):
        if packed.is_symlink() or not packed.is_file():
            return True
        try:
            for line in packed.read_text(encoding='ascii').splitlines():
                if line and not line.startswith('#') and not line.startswith('^'):
                    _object_id, separator, name = line.partition(' ')
                    if separator and name == ref:
                        return True
        except (OSError, UnicodeDecodeError):
            return True
    result = subprocess.run(['git', '-C', str(repository), 'for-each-ref', '--format=%(refname)', ref],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if result.returncode != 0:
        return True
    return ref in result.stdout.decode('utf-8', errors='replace').splitlines()


def read_current_snapshot(repository: pathlib.Path, feature: str) -> dict[str, Any]:
    ref = current_ref(feature)
    commit = current_snapshot_commit(repository, feature)
    if commit is None:
        raise SnapshotError('snapshot-unavailable')
    manifest, files = _manifest(repository, commit)
    if manifest.get('feature') != feature:
        raise SnapshotError('snapshot-scope-mismatch')
    return {'commit': commit, 'ref': ref, 'manifest': manifest,
            'files': {f'.agent-state/{path}': raw for path, raw in files.items()}}


def read_current_repository_snapshot(repository: pathlib.Path) -> dict[str, Any]:
    ref = current_repository_ref()
    commit = current_repository_snapshot_commit(repository)
    if commit is None:
        raise SnapshotError('snapshot-unavailable')
    manifest, files = _manifest(repository, commit)
    return {'commit': commit, 'ref': ref, 'manifest': manifest,
            'files': {f'.agent-state/{path}': raw for path, raw in files.items()}}


def current_repository_state_status(repository: pathlib.Path, state_dir: pathlib.Path) -> str:
    repository = pathlib.Path(repository).resolve()
    state_dir = pathlib.Path(os.path.abspath(state_dir))
    if not current_repository_ref_exists(repository):
        return 'absent'
    try:
        snapshot = read_current_repository_snapshot(repository)
        if state_dir.is_symlink():
            return 'invalid'
        if not state_dir.is_dir():
            return 'state-missing'
        actual: dict[str, tuple[bytes, int]] = {}
        for directory, dirnames, filenames in os.walk(state_dir, followlinks=False):
            base = pathlib.Path(directory)
            for name in dirnames:
                if (base / name).is_symlink():
                    return 'invalid'
            for name in filenames:
                path = base / name
                if path.is_symlink() or not path.is_file():
                    return 'invalid'
                mode = path.stat().st_mode & 0o777
                if mode not in _FILE_MODES:
                    return 'invalid'
                actual[path.relative_to(state_dir).as_posix()] = (path.read_bytes(), mode)
        expected_entries = {entry['path']: entry for entry in snapshot['manifest']['files']}
        if set(actual) != set(expected_entries):
            return 'state-mismatch'
        for path, (raw, mode) in actual.items():
            entry = expected_entries[path]
            if (len(raw) != entry['bytes'] or sha256_bytes(raw) != entry['sha256'] or
                    mode != entry['mode'] or snapshot['files'][f'.agent-state/{path}'] != raw):
                return 'state-mismatch'
        return 'consistent'
    except (SnapshotError, OSError, KeyError, TypeError):
        return 'invalid'


def restore_current_snapshot(repository: pathlib.Path, feature: str) -> None:
    """Explicitly restore the newest verified ref; callers must hold lifecycle lock."""
    repository = pathlib.Path(repository).resolve()
    state_dir = repository / '.agent-state'
    if prepared_snapshot_exists(repository):
        raise SnapshotError('snapshot-restore-prepared-pending')
    if os.path.lexists(state_dir):
        raise SnapshotError('snapshot-restore-target-exists')
    snapshot = read_current_repository_snapshot(repository)
    if current_ref_exists(repository, feature):
        feature_snapshot = read_current_snapshot(repository, feature)
        state_path = feature_snapshot['manifest'].get('state_path')
        if (not isinstance(state_path, str) or
                feature_snapshot['files'].get(f'.agent-state/{state_path}') !=
                snapshot['files'].get(f'.agent-state/{state_path}')):
            raise SnapshotError('snapshot-feature-state-mismatch')
    temp = pathlib.Path(tempfile.mkdtemp(prefix='.agent-state.restore.', dir=repository))
    try:
        modes = {entry['path']: entry['mode'] for entry in snapshot['manifest']['files']}
        for relative, raw in snapshot['files'].items():
            rel = pathlib.PurePosixPath(relative)
            if rel.parts[0] != '.agent-state' or any(part in {'', '.', '..'} for part in rel.parts):
                raise SnapshotError('snapshot-restore-path-invalid')
            state_relative = pathlib.PurePosixPath(*rel.parts[1:]).as_posix()
            target = temp.joinpath(*rel.parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            target.chmod(modes[state_relative])
            file_fd = os.open(target, os.O_RDONLY)
            try:
                os.fsync(file_fd)
            finally:
                os.close(file_fd)
        for directory, _dirnames, _filenames in os.walk(temp, topdown=False):
            _fsync_directory(pathlib.Path(directory))
        if os.path.lexists(state_dir):
            raise SnapshotError('snapshot-restore-target-exists')
        os.replace(temp, state_dir)
        _fsync_directory(repository)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
