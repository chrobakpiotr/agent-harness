"""Diagnostic full-source copies. These records confer no launch/PASS authority.

Verbatim from Showcase `tooling/agent-harness/verification/workspace.py` at `49ab97f`; see docs/migration/provenance.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import stat
import time
from dataclasses import dataclass

from . import candidate, store
from .serialization import canonical

BARRIER = b'gitdir: /__showcase_workspace_git_unavailable__\n'


class WorkspaceError(ValueError):
    """Stable reason without source bytes."""


@dataclass(frozen=True)
class Workspace:
    root: pathlib.Path
    source: pathlib.Path
    repository: pathlib.Path
    seal: candidate.CandidateSeal
    authority_bindings: dict
    manifest: bytes


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def _safe(path, device):
    try:
        store._assert_contained(path, pathlib.Path(path.anchor), allow_equal=False)
        info = path.lstat()
    except (store.StoreError, OSError):
        raise WorkspaceError('workspace-unsafe-object') from None
    if info.st_dev != device or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise WorkspaceError('workspace-unsafe-object')
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise WorkspaceError('workspace-unsafe-object')
    return info


def _snapshot(root, base, runtime):
    objects = set()
    _, head, repository_id, _ = candidate._paths(root, base, runtime, source_objects=objects)
    device = root.stat().st_dev
    snapshots = {}
    total = 0
    started = time.monotonic()
    for relative in sorted(objects):
        if time.monotonic() - started > candidate.MAX_SECONDS:
            raise WorkspaceError('workspace-snapshot-unavailable')
        if '.git' in relative.split('/'):
            raise WorkspaceError('workspace-nested-git-unsupported')
        path = root / relative
        info = _safe(path, device)
        if stat.S_ISDIR(info.st_mode):
            snapshots[relative] = ('directory', info.st_mode & 0o777, None, _identity(info))
        else:
            data, metadata = candidate._read_worktree(root, relative, device)
            if data is None:
                raise WorkspaceError('workspace-snapshot-race')
            candidate._privacy_check(data)
            total += len(data)
            if total > candidate.MAX_BYTES:
                raise WorkspaceError('workspace-snapshot-unavailable')
            snapshots[relative] = ('file', metadata['mode'], data, metadata['identity'])
    current = set()
    _, current_head, current_repository, _ = candidate._paths(root, base, runtime, source_objects=current)
    if objects != current or head != current_head or repository_id != current_repository:
        raise WorkspaceError('workspace-snapshot-race')
    for relative, (_, _, _, identity) in snapshots.items():
        if _identity(_safe(root / relative, device)) != identity:
            raise WorkspaceError('workspace-snapshot-race')
    return snapshots, repository_id


def _projection(snapshot):
    return [{'path': path, 'kind': kind, 'mode': mode,
             'sha256': hashlib.sha256(data).hexdigest() if data is not None else None}
            for path, (kind, mode, data, _) in sorted(snapshot.items())]


def _same_seal(root, seal, bindings, runtime):
    if candidate.seal_candidate(root, seal.base_sha, bindings, trusted_runtime_root=runtime) != seal:
        raise WorkspaceError('workspace-candidate-drift')


def materialize(repository, seal, authority_bindings, *, family, attempt, execution, policy):
    """Preflight every source byte, then create a single-use diagnostic workspace."""
    root = pathlib.Path(os.path.abspath(repository))
    # Resolve control identity only after rejecting unresolved symlink components.
    store._assert_contained(root / '.agent-runs', root)
    runtime, repository_id = store.resolve_control_root(root)
    store._assert_contained(runtime, runtime.parent.parent.parent)
    for value in (family, attempt, execution):
        store._validate_component(value, 'workspace-invalid-binding')
    if not isinstance(policy, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', policy):
        raise WorkspaceError('workspace-invalid-binding')
    destination = runtime / 'runs' / family / attempt / 'artifacts' / execution
    store._assert_contained(destination, runtime)
    # Trusted control parents exist before collecting the full source.
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise WorkspaceError('workspace-collision')
    _same_seal(root, seal, authority_bindings, runtime)
    snapshot, observed_repository = _snapshot(root, seal.base_sha, runtime)
    if observed_repository != repository_id:
        raise WorkspaceError('workspace-repository-drift')
    record = {'schema_version': 1, 'authority': 'diagnostic-only', 'repository_id': repository_id,
              'family': family, 'attempt': attempt, 'execution': execution, 'policy': policy,
              'candidate_identity': seal.candidate_identity, 'changed_surface_id': seal.changed_surface_id,
              'head_sha': seal.head_sha, 'base_sha': seal.base_sha, 'entries': _projection(snapshot)}
    manifest = canonical(record)
    try:
        destination.mkdir()
    except FileExistsError:
        raise WorkspaceError('workspace-collision') from None
    source = destination / 'source'
    source.mkdir()
    source_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for relative, (kind, mode, data, _) in sorted(snapshot.items()):
            components = relative.split('/')
            parent_fd = os.dup(source_fd)
            try:
                for component in components[:-1]:
                    next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                      dir_fd=parent_fd)
                    os.close(parent_fd)
                    parent_fd = next_fd
                if kind == 'directory':
                    os.mkdir(components[-1], mode=0o700, dir_fd=parent_fd)
                else:
                    fd = os.open(components[-1], os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent_fd)
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(data)
                        os.fchmod(stream.fileno(), mode)
                        stream.flush()
                        os.fsync(stream.fileno())
            finally:
                os.close(parent_fd)
        barrier_fd = os.open('.git', os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                             0o600, dir_fd=source_fd)
        with os.fdopen(barrier_fd, 'wb') as stream:
            stream.write(BARRIER)
            stream.flush()
            os.fsync(stream.fileno())
        for relative, (kind, mode, _, _) in sorted(snapshot.items(), reverse=True):
            if kind == 'directory':
                # No command has access to this diagnostic tree during publication.
                store._assert_contained(source / relative, source)
                (source / relative).chmod(mode)
    finally:
        os.close(source_fd)
    workspace = Workspace(destination, source, root, seal, dict(authority_bindings), manifest)
    # Partial creation is retained and never becomes accepted on a retry.
    validate(workspace, published=False)
    store.publish_create_once(destination / 'manifest.json', json.loads(manifest))
    return workspace


def validate(workspace, *, published=True):
    """Check original/copy drift, allowing regular newly generated output bytes."""
    runtime, repository_id = store.resolve_control_root(workspace.repository)
    try:
        record = json.loads(workspace.manifest)
        for key in ('family', 'attempt', 'execution'):
            store._validate_component(record[key], 'workspace-invalid-binding')
        expected = runtime / 'runs' / record['family'] / record['attempt'] / 'artifacts' / record['execution']
        if (workspace.root != expected or workspace.source != expected / 'source' or
                record['repository_id'] != repository_id or
                record['candidate_identity'] != workspace.seal.candidate_identity or
                record['changed_surface_id'] != workspace.seal.changed_surface_id or
                record['head_sha'] != workspace.seal.head_sha or
                record['base_sha'] != workspace.seal.base_sha or
                record['authority'] != 'diagnostic-only' or record['schema_version'] != 1):
            raise WorkspaceError('workspace-binding-drift')
        store._assert_contained(expected, runtime)
    except (KeyError, TypeError, ValueError, store.StoreError):
        raise WorkspaceError('workspace-binding-drift') from None
    if published:
        try:
            manifest_path = workspace.root / 'manifest.json'
            _safe(manifest_path, workspace.root.stat().st_dev)
            raw, _ = candidate._read_worktree(workspace.root, 'manifest.json', workspace.root.stat().st_dev)
            stored = json.loads(raw)
        except (OSError, ValueError, candidate.CandidateSealError):
            raise WorkspaceError('workspace-manifest-unavailable') from None
        if canonical(stored) != workspace.manifest:
            raise WorkspaceError('workspace-manifest-drift')
    _same_seal(workspace.repository, workspace.seal, workspace.authority_bindings, runtime)
    original, _ = _snapshot(workspace.repository, workspace.seal.base_sha, runtime)
    if _projection(original) != json.loads(workspace.manifest)['entries']:
        raise WorkspaceError('workspace-original-drift')
    device = workspace.source.stat().st_dev
    _safe(workspace.source, device)
    for directory, dirs, files in os.walk(workspace.source, followlinks=False):
        for name in dirs + files:
            _safe(pathlib.Path(directory) / name, device)
    if (workspace.source / '.git').read_bytes() != BARRIER:
        raise WorkspaceError('workspace-git-barrier-drift')
    for relative, (kind, mode, data, _) in original.items():
        path = workspace.source / relative
        info = _safe(path, device)
        if (info.st_mode & 0o777) != mode or stat.S_ISDIR(info.st_mode) != (kind == 'directory'):
            raise WorkspaceError('workspace-copy-drift')
        if kind == 'file':
            copied, _ = candidate._read_worktree(workspace.source, relative, device)
            if copied != data:
                raise WorkspaceError('workspace-copy-drift')
