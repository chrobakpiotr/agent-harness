"""Feature lifecycle state: its location, identity checks and the single writer (AH5-14a).

Verbatim from Showcase `tooling/agent-harness/harness.py` at `fd96eeb`, with the consumer's `FeaturePolicy`
(`agent_harness.feature`) passed explicitly. `save_state` is the only state writer; it commits through
`state_snapshots.commit_state_snapshot` as the source does. Loading and locking move with AH5-14b/14d.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
from collections.abc import Callable
from typing import Any

from . import state_snapshots
from .feature import (
    FeaturePolicy,
    die,
    feature_fingerprint,
    protocol_version,
    sha256_bytes,
    task_index,
)

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - lifecycle authority requires cross-process locking.
    fcntl = None


def state_key(feature_dir: pathlib.Path, *, policy: FeaturePolicy, common_dir: Callable[[pathlib.Path], pathlib.Path] | None = None) -> str:
    return sha256_bytes(f'{_common(feature_dir, policy, common_dir)}\0{feature_dir.name}'.encode())[:20]


def git_common_dir(feature_dir: pathlib.Path, *, policy: FeaturePolicy) -> pathlib.Path:
    """Return Git's canonical common directory for this checkout/worktree."""
    result = subprocess.run(
        ['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'],
        cwd=policy.repo_base(feature_dir), capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        die('cannot resolve the common Git directory for lifecycle state')
    common = pathlib.Path(result.stdout.strip()).resolve()
    if not common.is_dir():
        die(f'Git common directory does not exist: {common}')
    return common


def runtime_state_dir(feature_dir: pathlib.Path, *, policy: FeaturePolicy, common_dir: Callable[[pathlib.Path], pathlib.Path] | None = None) -> pathlib.Path:
    if policy.state_dir.is_absolute():
        return policy.state_dir
    # Store lifecycle state next to the common Git directory so all linked worktrees
    # resolve the same location while preserving <main-checkout>/.agent-state.
    common = _common(feature_dir, policy, common_dir)
    return common.parent / policy.state_dir


def state_path(feature_dir: pathlib.Path, *, policy: FeaturePolicy, common_dir: Callable[[pathlib.Path], pathlib.Path] | None = None) -> pathlib.Path:
    return (runtime_state_dir(feature_dir, policy=policy, common_dir=common_dir)
            / f'{feature_dir.name}-{state_key(feature_dir, policy=policy, common_dir=common_dir)}.json')


def _common(feature_dir: pathlib.Path, policy: FeaturePolicy,
            common_dir: Callable[[pathlib.Path], pathlib.Path] | None) -> pathlib.Path:
    """The common Git directory through the consumer's binding when it passes one (its tests patch it)."""
    return common_dir(feature_dir) if common_dir is not None else git_common_dir(feature_dir, policy=policy)


def lock_path(feature_dir: pathlib.Path, *, policy: FeaturePolicy, locking: Any = fcntl) -> pathlib.Path:
    # Snapshots capture the complete repository authority tree. One lock prevents
    # cross-feature commits from producing a torn snapshot.
    if locking is None:  # the consumer passes its own `fcntl` binding, so its tests reach this guard
        die('LIFECYCLE_LOCK_UNAVAILABLE: cross-process lifecycle locking is required')
    return state_snapshots.lifecycle_lock_path(policy.repo_base(feature_dir))


def legacy_state_name(feature_dir: pathlib.Path, *, policy: FeaturePolicy, common_dir: Callable[[pathlib.Path], pathlib.Path] | None = None) -> str:
    # Historical key: SHA-256 of the main checkout's resolved feature path.
    repo_root = _common(feature_dir, policy, common_dir).parent
    main_feature = policy.legacy_feature_path(repo_root, feature_dir.name)
    name = f'{feature_dir.name}-{sha256_bytes(str(main_feature.resolve()).encode())[:20]}.json'
    return name


def main_legacy_state_path(feature_dir: pathlib.Path, *, policy: FeaturePolicy, common_dir: Callable[[pathlib.Path], pathlib.Path] | None = None) -> pathlib.Path:
    """Old main-checkout file; the only legacy snapshot safe to adopt automatically."""
    root = _common(feature_dir, policy, common_dir).parent
    return root / '.agent-state' / legacy_state_name(feature_dir, policy=policy, common_dir=common_dir)


def validate_state_identity(feature_dir: pathlib.Path, doc: dict[str, Any], state: dict[str, Any], *,
                            fingerprint: Callable[[pathlib.Path], str] = feature_fingerprint) -> None:
    expected = fingerprint(feature_dir)
    if state.get('fingerprint') != expected:
        die('feature spec/plan/tasks fingerprint differs; refusing lifecycle state adoption/migration')
    if state.get('feature', feature_dir.name) != doc.get('feature', feature_dir.name):
        die('runtime state logical feature identity differs from tasks.json; refusing lifecycle state adoption/migration')
    expected_ids = set(task_index(doc))
    tasks = state.get('tasks')
    if not isinstance(tasks, dict) or set(tasks) != expected_ids:
        die('runtime state task set differs from tasks.json; refusing lifecycle state adoption/migration')


def validate_loaded_state(feature_dir: pathlib.Path, doc: dict[str, Any], state: dict[str, Any], *,
                          fingerprint: Callable[[pathlib.Path], str] = feature_fingerprint) -> None:
    validate_state_identity(feature_dir, doc, state, fingerprint=fingerprint)
    expected = fingerprint(feature_dir)
    expected_version = protocol_version(feature_dir)
    stored_version = state.get('protocol_version')
    if stored_version is None and state.get('state_version') == 2:
        die(
            'runtime state predates explicit protocol versioning; run '
            f'`harness.py migrate-state {feature_dir}` to validate and upgrade it'
        )
    if stored_version != expected_version:
        die(
            f'runtime state protocol/schema version {stored_version!r} is incompatible with '
            f'current version {expected_version}; use `harness.py migrate-state {feature_dir}` '
            'when a supported migration exists, otherwise revise/re-plan deliberately'
        )
    expected_ids = set(task_index(doc))
    actual_ids = set(state.get('tasks', {}))
    if expected_ids != actual_ids:
        die('runtime state task set differs from tasks.json; reset the feature state')


def save_state(feature_dir: pathlib.Path, state: dict[str, Any], *,
               additional_files: dict[str, tuple[bytes, int]] | None = None,
               remove_files: set[str] | None = None, policy: FeaturePolicy,
               fault_injector: Callable[..., Any] | None = None,
               common_dir: Callable[[pathlib.Path], pathlib.Path] | None = None) -> None:
    """Write a complete authority snapshot before atomically replacing state.

    Lifecycle callers hold the shared repository lock for the whole mutation.
    """
    state_dir = runtime_state_dir(feature_dir, policy=policy, common_dir=common_dir)
    path = state_path(feature_dir, policy=policy, common_dir=common_dir)
    try:
        payload = json.dumps(state, indent=2, sort_keys=True, allow_nan=False).encode('utf-8') + b'\n'
        state_snapshots.commit_state_snapshot(
            policy.repo_base(feature_dir), state_dir, path, payload, state,
            additional_files=additional_files,
            remove_files=remove_files,
            fault_injector=fault_injector,
        )
    except state_snapshots.SnapshotError as exc:
        die(f'LIFECYCLE_SNAPSHOT_FAILED: {exc}')
