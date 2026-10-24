#!/usr/bin/env python3
"""Repo-native orchestration core for agentic Spec-Driven Development.

The harness deliberately contains no model SDK. It owns deterministic coordination only:
feature validation, DAG scheduling, task leases, immutable packets, retry/escalation,
state/spec drift detection, specialist routing, and isolated git worktrees.

Model processes are launched by runner.py, which keeps provider-specific concerns out of this core.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
from collections import namedtuple
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any, Iterator

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import trust  # noqa: E402
import verification_contract as vc  # noqa: E402
import state_snapshots  # noqa: E402
import state_recovery  # noqa: E402
from agent_harness import feature as _feature  # noqa: E402
from agent_harness import feature_state as _state  # noqa: E402
from agent_harness import feature_access as _access  # noqa: E402
from agent_harness import lifecycle_checks as _checks  # noqa: E402
from agent_harness import feature_transitions as _transitions  # noqa: E402

STATE_DIR = pathlib.Path('.agent-state')
WORKTREE_ROOT_SUFFIX = '-agent-worktrees'
VALID_ROLES = _feature.VALID_ROLES
RISK_TO_REVIEWERS = {
    'architecture': ('architecture-reviewer',),
    'api': ('architecture-reviewer',),
    'messaging': ('messaging-reviewer',),
    'persistence': ('persistence-reviewer',),
    'concurrency': ('concurrency-reviewer',),
    'security': ('security-reviewer',),
    'ai': ('ai-reviewer', 'security-reviewer'),
    'infra': ('platform-reviewer',),
    'observability': ('platform-reviewer',),
    'frontend': ('frontend-reviewer',),
    'performance': ('performance-reviewer',),
}
FEATURE_ID_RE = _feature.FEATURE_ID_RE
TASK_ID_RE = _feature.TASK_ID_RE
PROFILE_RE = _feature.PROFILE_RE
AC_RE = _feature.AC_RE
DEFAULT_LEASE_TTL_SECONDS = _feature.DEFAULT_LEASE_TTL_SECONDS
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = _feature.DEFAULT_HEARTBEAT_INTERVAL_SECONDS
VALID_TEST_POLICIES = _feature.VALID_TEST_POLICIES
VALID_TEST_MODES = _feature.VALID_TEST_MODES
# Bump only when persisted lifecycle state semantics/schema become incompatible.
# Implementation and documentation changes are recorded separately for audit.
STATE_SCHEMA_VERSION = _feature.STATE_SCHEMA_VERSION

VERIFICATION_BLOCKAGE_OUTCOMES = frozenset({
    'busy', 'stale-input', 'environment-blocked', 'needs-human',
    'verification-blocked', 'verification-owned', 'abandoned',
})
COMPLETION_EVIDENCE_SCHEMA_VERSION = 1
COMPLETION_AUTHORITY_SCHEMA_VERSION = _access.COMPLETION_AUTHORITY_SCHEMA_VERSION
LEGACY_COMPLETION_BINDING_VERSION = _access.LEGACY_COMPLETION_BINDING_VERSION
COMPLETION_CORRECTION_REASON = _access.COMPLETION_CORRECTION_REASON
_completion_fault_injector = None
_state_snapshot_fault_injector = None
_authority_transaction = _access._authority_transaction

# Packet identity values occupy distinct namespaces. Recovery v1 persisted the
# embedded checksum, while M1 revisions hash the complete normalized packet
# (including that checksum). Never infer either scheme from an unversioned hash.
PACKET_PAYLOAD_IDENTITY_SCHEME = _checks.PACKET_PAYLOAD_IDENTITY_SCHEME
PACKET_REVISION_IDENTITY_SCHEME = _checks.PACKET_REVISION_IDENTITY_SCHEME
RECOVERY_PACKET_IDENTITY_SCHEMES = _checks.RECOVERY_PACKET_IDENTITY_SCHEMES
RECOVERY_RECORD_IDENTITY_SCHEME = 'canonical-recovery-record-sha256-v1'
BRIDGE_SCHEMA_VERSION = 1
BRIDGE_CLASSIFICATIONS = {'AUTOMATICALLY_PROVEN', 'HUMAN_ATTESTED', 'AMBIGUOUS'}


PacketIdentity = _checks.PacketIdentity



def _policy() -> _feature.FeaturePolicy:
    """AH5-14a: Showcase's policy for the library's feature documents and state, built per call so tests that
    patch this module's names (STATE_DIR, feature_repo_base, protocol_files, reviewers, ...) reach the library."""
    return _feature.FeaturePolicy(
        repo_base=lambda path: feature_repo_base(path),
        is_active=lambda path: is_active_feature_dir(path),
        protocol_files=lambda path: protocol_files(path),
        profile_path=lambda root, profile: root / 'docs' / 'agentic-sdd' / 'agents' / f'{profile}.md',
        reviewers=lambda task: reviewers(task),
        validate_contract=lambda path: vc.validate_contract(path),
        criterion_ids=lambda path: vc.criterion_ids(path),
        state_dir=STATE_DIR,
        legacy_feature_path=lambda root, name: root / 'docs' / 'specs' / name,
    )



def _valid_incident_record(record: Any, feature: str) -> bool:
    """The record shape `state_recovery.load_incident` accepts; non-emptiness alone is not validity."""
    return (type(record) is dict and record.get('schema_version') == 1 and record.get('feature') == feature and
            record.get('status') == 'observed' and record.get('state_present') is False and
            record.get('state_mutations_during_investigation') is False and record.get('cause') == 'unknown')


def checked_state_recovery_incident(feature_dir: pathlib.Path) -> dict[str, Any] | None:
    """The incident lookup the library's recovery check uses, fail-closed (master, 2026-10-24).

    `None` means no incident; a valid incident record goes on to the recovery-epoch check. Any other result (False,
    an empty or partial record, a string, another type) refuses, and the lookup's own refusals stay as they are.
    """
    incident = state_recovery_incident(feature_dir)
    if incident is None:
        return None
    if not _valid_incident_record(incident, feature_dir.name):
        die('STATE_RECOVERY_INCIDENT_INVALID: the incident lookup returned neither None nor a valid incident record')
    return incident


def _state_access() -> _access.StateAccess:
    """AH5-14b-1: Showcase's bindings for the library's state access, built per call from this module's names, so
    tests that patch them (fcntl, save_state, load_validated, _completion_fault_injector, ...) reach the library."""
    import verification.store as verification_store
    return _access.StateAccess(
        policy=_policy(),
        common_dir=lambda path: git_common_dir(path),
        locking=fcntl,
        fingerprint=lambda path: feature_fingerprint(path),
        protocol_fingerprint=lambda path: protocol_fingerprint(path),
        save_state=lambda *args, **kwargs: save_state(*args, **kwargs),
        load_validated=lambda path: load_validated(path),
        read_state_pure=lambda path, doc: _read_state_unlocked_pure(path, doc),
        verification_store=lambda: verification_store,
        completion_fault_injector=lambda: _completion_fault_injector,
        recovery_incident=lambda path: checked_state_recovery_incident(path),
        recovery_epoch_required=lambda path, incident, status: state_recovery_epoch_required(path, incident, status),
    )



def _verification_authority() -> Any:
    """Showcase's `verification.authority`, for the library's transitions (looked up per call)."""
    import verification.authority as module
    return module


def _verification_store() -> Any:
    """Showcase's `verification.store`, for the library's transitions (looked up per call)."""
    import verification.store as module
    return module


canonical_packet_payload_sha256 = _checks.canonical_packet_payload_sha256


packet_identity_from_embedded_checksum = _checks.packet_identity_from_embedded_checksum


packet_identity_from_revision = _checks.packet_identity_from_revision


packet_identity_from_recovery = _checks.packet_identity_from_recovery


recovery_proves_packet_binding = _checks.recovery_proves_packet_binding


canonical_json_sha256 = _access.canonical_json_sha256


recovery_record_identity = _checks.recovery_record_identity


try:  # Unix/macOS/Linux - the primary targets for this repository.
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - lifecycle authority requires cross-process locking.
    fcntl = None


die = _feature.die


load_json = _feature.load_json


feature_files = _feature.feature_files


task_index = _feature.task_index


repo_root = _feature.repo_root


sha256_bytes = _feature.sha256_bytes


feature_fingerprint = _feature.feature_fingerprint


def feature_repo_base(feature_dir: pathlib.Path) -> pathlib.Path:
    resolved = feature_dir.resolve()
    if resolved.parent.name == 'specs' and resolved.parent.parent.name == 'docs':
        return resolved.parent.parent.parent
    return pathlib.Path.cwd().resolve()


def protocol_files(feature_dir: pathlib.Path) -> list[pathlib.Path]:
    root = feature_repo_base(feature_dir)
    candidates = [
        root / 'AGENTS.md',
        root / 'CLAUDE.md',
        root / 'tooling' / 'agent-harness' / 'requirements.txt',
        root / 'tooling' / 'agent-harness' / 'harness.py',
        root / 'tooling' / 'agent-harness' / 'runner.py',
        root / 'tooling' / 'agent-harness' / 'orchestrate.py',
        root / 'tooling' / 'agent-harness' / 'verification_sandbox.py',
        root / 'tooling' / 'agent-harness' / 'telemetry.py',
        root / 'tooling' / 'agent-harness' / 'control_plane.py',
        root / 'tooling' / 'agent-harness' / 'design.py',
        root / 'tooling' / 'agent-harness' / 'wayfinder.py',
        root / 'tooling' / 'agent-harness' / 'verification_contract.py',
        root / 'tooling' / 'agent-harness' / 'trust.py',
        root / 'tooling' / 'agent-harness' / 'eval.py',
        root / 'tooling' / 'agent-harness' / 'schemas' / 'wayfinder-result.schema.json',
        root / 'tooling' / 'agent-harness' / 'schemas' / 'wayfinder-handoff.schema.json',
        root / 'tooling' / 'agent-harness' / 'schemas' / 'wayfinder-tasks-result.schema.json',
        root / 'tooling' / 'agent-harness' / 'schemas' / 'task-result.schema.json',
        root / 'tooling' / 'agent-harness' / 'schemas' / 'design-result.schema.json',
        root / 'tooling' / 'agent-harness' / 'schemas' / 'verification-contract.schema.json',
        root / '.github' / 'workflows' / 'agentic-sdd.yml',
    ]
    for directory in (root / 'docs' / 'agentic-sdd', root / '.claude' / 'agents'):
        if directory.exists():
            candidates.extend(sorted(p for p in directory.rglob('*.md') if p.is_file()))
    eval_dir = root / 'tooling' / 'agent-harness' / 'evals'
    if eval_dir.exists():
        candidates.extend(sorted(p for p in eval_dir.glob('*.json') if p.is_file()))
    # Deduplicate while retaining deterministic path order.
    return sorted({p.resolve() for p in candidates if p.exists()}, key=lambda p: str(p))


def protocol_fingerprint(feature_dir: pathlib.Path) -> str:
    return _feature.protocol_fingerprint(feature_dir, policy=_policy())


protocol_version = _feature.protocol_version


safe_relative_pattern = _feature.safe_relative_pattern


builder_pattern_has_concrete_root = _feature.builder_pattern_has_concrete_root


pattern_static_prefix = _feature.pattern_static_prefix


patterns_may_overlap = _feature.patterns_may_overlap


transitive_dependencies = _feature.transitive_dependencies


def is_active_feature_dir(feature_dir: pathlib.Path) -> bool:
    root = feature_repo_base(feature_dir)
    try:
        rel = feature_dir.resolve().relative_to(root)
    except ValueError:
        return False
    return len(rel.parts) == 3 and rel.parts[:2] == ('docs', 'specs')


def design_gate_errors(feature_dir: pathlib.Path, spec: pathlib.Path, plan: pathlib.Path) -> list[str]:
    return _feature.design_gate_errors(feature_dir, spec, plan, policy=_policy())


master_implementation_closure_errors = _feature.master_implementation_closure_errors


def validate(feature_dir: pathlib.Path) -> list[str]:
    return _feature.validate(feature_dir, policy=_policy())


def state_key(feature_dir: pathlib.Path) -> str:
    return _state.state_key(feature_dir, policy=_policy(), common_dir=lambda path: git_common_dir(path))


def git_common_dir(feature_dir: pathlib.Path) -> pathlib.Path:
    """Return Git's canonical common directory for this checkout/worktree."""
    return _state.git_common_dir(feature_dir, policy=_policy())


def runtime_state_dir(feature_dir: pathlib.Path) -> pathlib.Path:
    return _state.runtime_state_dir(feature_dir, policy=_policy(), common_dir=lambda path: git_common_dir(path))


def state_path(feature_dir: pathlib.Path) -> pathlib.Path:
    return _state.state_path(feature_dir, policy=_policy(), common_dir=lambda path: git_common_dir(path))


def lock_path(feature_dir: pathlib.Path) -> pathlib.Path:
    return _state.lock_path(feature_dir, policy=_policy(), locking=fcntl)


def recover_prepared_state_locked(feature_dir: pathlib.Path) -> None:
    """Finish an interrupted write-ahead snapshot before reading lifecycle state (the library's).

    The caller must hold the repository-wide lifecycle lock.
    """
    _access.recover_prepared_state_locked(feature_dir, access=_state_access())


def lifecycle_state_lock(feature_dir: pathlib.Path, *,
                         repository_admission_id: str | None = None,
                         allowed_reservation: tuple[str, str] | None = None,
                         allow_unvalidated_state: bool = False) -> Any:
    """Hold repository verifier admission through the lifecycle mutation (the library's)."""
    return _access.lifecycle_state_lock(feature_dir, repository_admission_id=repository_admission_id,
                                        allowed_reservation=allowed_reservation,
                                        allow_unvalidated_state=allow_unvalidated_state, access=_state_access())


def initial_state(feature_dir: pathlib.Path, doc: dict[str, Any], *, project_completions: bool = True) -> dict[str, Any]:
    return _access.initial_state(feature_dir, doc, project_completions=project_completions, access=_state_access())


def load_state(feature_dir: pathlib.Path, doc: dict[str, Any]) -> dict[str, Any]:
    return _access.load_state(feature_dir, doc, access=_state_access())


def _resolve_state_unlocked(feature_dir: pathlib.Path, doc: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Resolve canonical or exact historical main-checkout state under the shared lock."""
    return _access._resolve_state_unlocked(feature_dir, doc, access=_state_access())


def state_recovery_incident(feature_dir: pathlib.Path) -> dict[str, Any] | None:
    """Resolve the immutable incident record that fences this feature's lost state."""
    if feature_dir.name != state_recovery.INCIDENT_FEATURE:
        return None
    repository = feature_repo_base(feature_dir)
    incident_path = state_recovery.incident_path(repository)
    relative = incident_path.relative_to(repository).as_posix()
    if not os.path.lexists(incident_path):
        tracked = subprocess.run(['git', '-C', str(repository), 'cat-file', '-e', f'HEAD:{relative}'],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if tracked.returncode == 0:
            die('STATE_RECOVERY_INCIDENT_INVALID: committed incident record is missing from the working tree')
        return None
    try:
        incident, _raw = state_recovery.load_incident(repository, feature_dir.name)
        return incident
    except state_recovery.RecoveryError as exc:
        die(f'STATE_RECOVERY_INCIDENT_INVALID: {exc}')


def state_snapshot_status(feature_dir: pathlib.Path) -> str:
    return _access.state_snapshot_status(feature_dir, access=_state_access())


def state_recovery_required(feature_dir: pathlib.Path) -> bool:
    return _access.state_recovery_required(feature_dir, access=_state_access())


def state_recovery_epoch_required(feature_dir: pathlib.Path, incident: dict[str, Any], snapshot_status: str) -> bool:
    """AH5-14b-1: Showcase's refuse-only recovery-epoch check, verbatim from its state_recovery_required.

    The library calls it only when an incident exists; True (or anything but False, or an error) refuses.
    """
    canonical = state_path(feature_dir)
    if not canonical.is_file() or canonical.is_symlink():
        return True
    if snapshot_status == 'consistent':
        try:
            snapshot_state = json.loads(canonical.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return True
        # An exact verified current snapshot may restore old acknowledged history.
        # A new epoch additionally keeps checking its immutable recovery receipt.
        if isinstance(snapshot_state, dict) and not isinstance(snapshot_state.get('recovery_epoch'), dict):
            return False
    try:
        state = json.loads(canonical.read_text(encoding='utf-8'))
        record_ref = state.get('recovery_epoch') if isinstance(state, dict) else None
        if not isinstance(record_ref, dict):
            return True
        repository = feature_repo_base(feature_dir)
        incident_value, incident_bytes = state_recovery.load_incident(repository, feature_dir.name)
        verified = state_recovery.verify_evidence_baseline(repository, incident_value)
        record_path = record_ref.get('record_path')
        if (record_ref.get('incident_sha256') != sha256_bytes(incident_bytes) or
                record_ref.get('evidence_baseline_sha256') != verified['baseline_sha256'] or
                record_ref.get('feature_fingerprint') != state.get('fingerprint') or
                record_ref.get('repository_id') != state_snapshots._repo_identity(repository) or
                record_ref.get('epoch_id') != state.get('lifecycle_epoch_id') or
                state.get('max_rework_attempts') != 2 or
                not isinstance(record_ref.get('record_sha256'), str) or
                re.fullmatch(r'[0-9a-f]{64}', record_ref['record_sha256']) is None or
                not isinstance(record_path, str) or
                not record_path.startswith('recovery-epochs/') or
                any(part in {'', '.', '..'} for part in pathlib.PurePosixPath(record_path).parts)):
            return True
        state_root = runtime_state_dir(feature_dir)
        if state_root.is_symlink():
            return True
        immutable_path = state_root.joinpath(*pathlib.PurePosixPath(record_path).parts)
        immutable_path.resolve().relative_to(state_root.resolve())
        if immutable_path.is_symlink() or not immutable_path.is_file():
            return True
        record_bytes = immutable_path.read_bytes()
        if sha256_bytes(record_bytes) != record_ref['record_sha256']:
            return True
        record = json.loads(record_bytes)
        return not (record.get('epoch_id') == record_ref.get('epoch_id') and
                    record.get('incident_id') == incident_value.get('incident_id') and
                    record.get('incident_sha256') == record_ref.get('incident_sha256') and
                    record.get('evidence_baseline_sha256') == record_ref.get('evidence_baseline_sha256') and
                    record.get('repository_id') == record_ref.get('repository_id') and
                    record.get('feature_fingerprint') == state.get('fingerprint') and
                    record.get('max_rework_attempts') == 2 and
                    record.get('imported_completions') == [])
    except (OSError, json.JSONDecodeError, state_recovery.RecoveryError, AttributeError, TypeError, KeyError,
            ValueError):
        return True


def _load_state_unlocked(feature_dir: pathlib.Path, doc: dict[str, Any]) -> dict[str, Any]:
    return _access._load_state_unlocked(feature_dir, doc, access=_state_access())


def cmd_recover_state(args: argparse.Namespace) -> None:
    """Create an explicitly requested, empty new lifecycle epoch after state loss.

    Historical task completions and retry grants are deliberately not projected.
    The owner can re-run and independently evaluate work in the new epoch.
    """
    feature_dir = pathlib.Path(args.feature_dir).resolve()
    doc = load_validated(feature_dir)
    if (not isinstance(args.epoch_id, str) or not FEATURE_ID_RE.fullmatch(args.epoch_id) or
            not isinstance(args.operator, str) or not args.operator.strip() or
            len(args.operator) > 256 or any(ord(ch) < 0x20 for ch in args.operator) or
            not isinstance(args.expected_feature_fingerprint, str) or
            re.fullmatch(r'[0-9a-f]{64}', args.expected_feature_fingerprint) is None):
        die('STATE_RECOVERY_INVALID: provide a safe epoch ID, operator label, and current feature fingerprint')
    if type(doc.get('max_rework_attempts', 2)) is not int or doc.get('max_rework_attempts', 2) != 2:
        die('STATE_RECOVERY_POLICY_MISMATCH: new epoch requires max_rework_attempts=2')

    with lock_path(feature_dir).open('a+', encoding='utf-8') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            recover_prepared_state_locked(feature_dir)
            canonical = state_path(feature_dir)
            if os.path.lexists(canonical):
                if not state_recovery_required(feature_dir):
                    try:
                        existing = json.loads(canonical.read_text(encoding='utf-8'))
                        marker = existing.get('recovery_epoch', {})
                        record_path = marker.get('record_path') if isinstance(marker, dict) else None
                        if (not isinstance(record_path, str) or not record_path.startswith('recovery-epochs/') or
                                any(part in {'', '.', '..'} for part in pathlib.PurePosixPath(record_path).parts)):
                            raise ValueError('unsafe recovery record path')
                        record_file = runtime_state_dir(feature_dir).joinpath(
                            *pathlib.PurePosixPath(record_path).parts)
                        existing_record = json.loads(record_file.read_text(encoding='utf-8'))
                    except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
                        existing_record = None
                        marker = {}
                    except ValueError:
                        existing_record = None
                        marker = {}
                    if (marker.get('epoch_id') == args.epoch_id and isinstance(existing_record, dict) and
                            existing_record.get('operator') == args.operator.strip() and
                            existing_record.get('feature_fingerprint') == args.expected_feature_fingerprint):
                        print(f'ALREADY_RECOVERED {feature_dir.name} epoch={args.epoch_id}')
                        return
                die('STATE_RECOVERY_ALREADY_PRESENT: refusing to overwrite existing authority')
            if state_snapshot_status(feature_dir) == 'state-missing':
                die('STATE_SNAPSHOT_RESTORE_AVAILABLE: restore the exact current snapshot before starting a new epoch')
            if os.path.lexists(main_legacy_state_path(feature_dir)):
                die('STATE_RECOVERY_LEGACY_PRESENT: resolve alternate state explicitly before recovery')
            repository = feature_repo_base(feature_dir)
            try:
                incident, incident_bytes = state_recovery.load_incident(repository, feature_dir.name)
                baseline = state_recovery.verify_evidence_baseline(repository, incident)
            except state_recovery.RecoveryError as exc:
                die(f'STATE_RECOVERY_EVIDENCE_INVALID: {exc}')
            remote_slug = state_snapshots.remote_repository_slug(repository)
            if (not isinstance(incident.get('repository'), str) or not remote_slug or
                    '/'.join(remote_slug.split('/')[-2:]) != incident['repository'].lower()):
                die('STATE_RECOVERY_REPOSITORY_MISMATCH: incident does not bind this Git origin')
            if not state_recovery.observation_commit_exists(repository, incident):
                die('STATE_RECOVERY_OBSERVATION_UNAVAILABLE: incident observation checkpoint is missing')
            current_fingerprint = feature_fingerprint(feature_dir)
            if current_fingerprint != args.expected_feature_fingerprint:
                die('STATE_RECOVERY_FINGERPRINT_MISMATCH: accepted feature changed since operator inspection')

            record_path = f'recovery-epochs/{args.epoch_id}/recovery-record.json'
            state_root = runtime_state_dir(feature_dir)
            if state_root.is_symlink() or (state_root.exists() and not state_root.is_dir()):
                die('STATE_RECOVERY_PATH_UNSAFE: lifecycle state root is not a real directory')
            recovery_record = {
                'schema_version': 1,
                'record_type': 'lifecycle-state-recovery-epoch',
                'feature': doc.get('feature', feature_dir.name),
                'incident_id': incident['incident_id'],
                'incident_sha256': sha256_bytes(incident_bytes),
                'repository_id': state_snapshots._repo_identity(repository),
                'feature_fingerprint': current_fingerprint,
                'evidence_baseline_sha256': baseline['baseline_sha256'],
                'evidence_entries_verified': baseline['entries_verified'],
                'observation_head': incident['observation_head'],
                'epoch_id': args.epoch_id,
                'operator': args.operator.strip(),
                'historical_attempts': 'unavailable',
                'historical_packet_lineage': 'unavailable',
                'historical_retry_grants_imported': False,
                'imported_completions': [],
                'completion_policy': 'all tasks pending; prior evidence remains archival',
                'max_rework_attempts': 2,
            }
            record_parts = pathlib.PurePosixPath(record_path).parts
            parent = state_root
            for part in record_parts[:-1]:
                parent = parent / part
                if parent.is_symlink():
                    die('STATE_RECOVERY_PATH_UNSAFE: recovery record parent is a symlink')
            existing_record_path = state_root.joinpath(*record_parts)
            try:
                existing_record_path.resolve().relative_to(state_root.resolve())
            except ValueError:
                die('STATE_RECOVERY_PATH_UNSAFE: recovery record escapes lifecycle state root')
            if os.path.lexists(existing_record_path):
                if existing_record_path.is_symlink() or not existing_record_path.is_file():
                    die('STATE_RECOVERY_RECORD_CONFLICT: existing epoch record is unsafe')
                existing_bytes = existing_record_path.read_bytes()
                try:
                    existing_record = json.loads(existing_bytes)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    die('STATE_RECOVERY_RECORD_CONFLICT: existing epoch record is invalid')
                if not isinstance(existing_record, dict):
                    die('STATE_RECOVERY_RECORD_CONFLICT: existing epoch record is invalid')
                existing_binding = {key: value for key, value in existing_record.items() if key != 'created_at'}
                if existing_binding != recovery_record:
                    die('STATE_RECOVERY_RECORD_CONFLICT: existing epoch ID has different bound content')
                recovery_record = existing_record
                record_bytes = existing_bytes
            else:
                recovery_record['created_at'] = utc_now().isoformat()
                record_bytes = json.dumps(recovery_record, sort_keys=True, indent=2,
                                          allow_nan=False).encode('utf-8') + b'\n'
            state = initial_state(feature_dir, doc, project_completions=False)
            state.update({
                'lifecycle_epoch_id': args.epoch_id,
                'feature_generation': 1,
                'historical_attempts': 'unavailable',
                'max_rework_attempts': 2,
                'recovery_epoch': {
                    'epoch_id': args.epoch_id,
                    'record_path': record_path,
                    'record_sha256': sha256_bytes(record_bytes),
                    'incident_sha256': recovery_record['incident_sha256'],
                    'evidence_baseline_sha256': baseline['baseline_sha256'],
                    'repository_id': recovery_record['repository_id'],
                    'feature_fingerprint': current_fingerprint,
                },
            })
            for entry in state['tasks'].values():
                entry['historical_attempts'] = 'unavailable'
            save_state(feature_dir, state, additional_files={record_path: (record_bytes, 0o600)})
            try:
                state_recovery.verify_evidence_baseline(repository, incident)
            except state_recovery.RecoveryError as exc:
                die(f'STATE_RECOVERY_POSTCHECK_FAILED: {exc}')
            if state_recovery_required(feature_dir):
                die('STATE_RECOVERY_POSTCHECK_FAILED: recovery receipt or evidence baseline did not verify')
            print(f'RECOVERED {feature_dir.name} epoch={args.epoch_id} imported_completions=0 retry_budget=2')
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def cmd_restore_state(args: argparse.Namespace) -> None:
    """Restore only the exact, verified current Git snapshot into a missing state root."""
    feature_dir = pathlib.Path(args.feature_dir).resolve()
    repository = feature_repo_base(feature_dir)
    if not isinstance(args.operator, str) or not args.operator.strip() or len(args.operator) > 256:
        die('STATE_RESTORE_INVALID: provide an operator provenance label')
    if re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', args.expected_snapshot_commit or '') is None:
        die('STATE_RESTORE_INVALID: expected snapshot commit must be a full Git object ID')
    with lock_path(feature_dir).open('a+', encoding='utf-8') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            try:
                pending = state_snapshots._prepared_refs(repository)
                if pending:
                    details = ', '.join(f'{feature}:{commit}' for _ref, feature, commit in pending)
                    die('STATE_RESTORE_PREPARED_SNAPSHOT: state is absent, so ordinary lifecycle recovery '
                        f'cannot run; inspect the prepared commit and invoke discard-prepared-snapshot '
                        f'with --expected-prepared-commit <commit> after confirming it was unacknowledged '
                        f'(pending: {details})')
            except state_snapshots.SnapshotError as exc:
                die(f'STATE_RESTORE_UNAVAILABLE: {exc}')
            try:
                snapshot = state_snapshots.read_current_repository_snapshot(repository)
            except state_snapshots.SnapshotError as exc:
                die(f'STATE_RESTORE_UNAVAILABLE: {exc}')
            if snapshot['commit'] != args.expected_snapshot_commit:
                die('STATE_RESTORE_STALE: current snapshot changed after operator inspection')
            try:
                state_snapshots.restore_current_snapshot(repository, feature_dir.name)
            except state_snapshots.SnapshotError as exc:
                die(f'STATE_RESTORE_FAILED: {exc}')
            if state_snapshot_status(feature_dir) != 'consistent':
                die('STATE_RESTORE_POSTCHECK_FAILED: restored state differs from the current snapshot')
            print(f'RESTORED {feature_dir.name} snapshot={snapshot["commit"]} operator={args.operator.strip()}')
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def cmd_discard_prepared_snapshot(args: argparse.Namespace) -> None:
    """Audited owner action for abandoning an unacknowledged prepared write."""
    feature_dir = pathlib.Path(args.feature_dir).resolve()
    repository = feature_repo_base(feature_dir)
    if not isinstance(args.operator, str) or not args.operator.strip() or len(args.operator) > 256:
        die('STATE_SNAPSHOT_DISCARD_INVALID: provide an operator provenance label')
    with lock_path(feature_dir).open('a+', encoding='utf-8') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            try:
                result = state_snapshots.discard_prepared_snapshot(
                    repository, runtime_state_dir(feature_dir), feature_dir.name,
                    args.expected_prepared_commit, args.operator)
            except state_snapshots.SnapshotError as exc:
                die(f'STATE_SNAPSHOT_DISCARD_FAILED: {exc}')
            action = 'ALREADY_DISCARDED' if result['already_discarded'] == 'true' else 'DISCARDED'
            print(f'{action} {feature_dir.name} prepared={result["prepared_commit"]} '
                  f'audit={result["audit_ref"]} audit_commit={result["audit_commit"]} '
                  f'operator={result["operator"]}')
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read_state_unlocked_pure(feature_dir: pathlib.Path, doc: dict[str, Any]) -> dict[str, Any]:
    """Validate and read authority without adopting, migrating, cleaning, or writing it."""
    return _access._read_state_unlocked_pure(feature_dir, doc, access=_state_access())


def validate_state_identity(feature_dir: pathlib.Path, doc: dict[str, Any], state: dict[str, Any]) -> None:
    _state.validate_state_identity(feature_dir, doc, state, fingerprint=lambda path: feature_fingerprint(path))


def validate_loaded_state(feature_dir: pathlib.Path, doc: dict[str, Any], state: dict[str, Any]) -> None:
    _state.validate_loaded_state(feature_dir, doc, state, fingerprint=lambda path: feature_fingerprint(path))


def cmd_migrate_state(args: argparse.Namespace) -> None:
    """Validate and explicitly adopt a legacy state under the current semantic version."""
    doc = load_validated(args.feature_dir)
    runtime_state_dir(args.feature_dir).mkdir(parents=True, exist_ok=True)
    with lifecycle_state_lock(args.feature_dir, allow_unvalidated_state=True) as lock:
        state, legacy_path = _resolve_state_unlocked(args.feature_dir, doc)
        if not state_path(args.feature_dir).exists() and not legacy_path:
            die(f'no authoritative lifecycle state exists for {args.feature_dir}; migration will not create state')
        validate_state_identity(args.feature_dir, doc, state)
        tasks = state['tasks']
        old_version = state.get('protocol_version')
        legacy_version = old_version is None and state.get('state_version') == 2
        if old_version == protocol_version(args.feature_dir):
            validate_loaded_state(args.feature_dir, doc, state)
            if legacy_path:
                legacy_rel = main_legacy_state_path(args.feature_dir).relative_to(
                    runtime_state_dir(args.feature_dir).resolve(strict=False)).as_posix()
                save_state(args.feature_dir, state, remove_files={legacy_rel})
            print(f'ALREADY_CURRENT {args.feature_dir} protocol_version={old_version}')
        elif legacy_version:
            # Explicit known edge: pre-versioning state v2 -> semantic protocol v1.
            # Verify allowed lifecycle statuses and nonnegative attempt counters; preserve all
            # task metadata verbatim and never consult Git history for completion.
            valid_statuses = {'pending', 'running', 'completed', 'failed', 'escalated'}
            for task_id, entry in tasks.items():
                if not isinstance(entry, dict) or entry.get('status') not in valid_statuses:
                    die(f'invalid lifecycle entry for {task_id}; refusing migration')
                attempts = entry.get('attempts', 0)
                if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
                    die(f'invalid attempt count for {task_id}; refusing migration')
            if not isinstance(state.get('protocol_fingerprint'), str) or not state['protocol_fingerprint']:
                die('legacy runtime state has no protocol audit fingerprint; refusing migration')
            state['protocol_version'] = protocol_version(args.feature_dir)
            legacy_rel = main_legacy_state_path(args.feature_dir).relative_to(
                runtime_state_dir(args.feature_dir).resolve(strict=False)).as_posix()
            save_state(args.feature_dir, state, remove_files={legacy_rel})
            print(
                f'MIGRATED {args.feature_dir} protocol_version={old_version or "legacy-state-v2"}'
                f'->{state["protocol_version"]} tasks={len(tasks)} '
                f'preserved_completed={sum(1 for entry in tasks.values() if entry.get("status") == "completed")}'
            )
        else:
            die(f'no supported lifecycle-state migration from protocol version {old_version!r}')
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def historical_feature_fingerprint(feature_dir: pathlib.Path, commit: str) -> str:
    """Recompute the feature fingerprint from an immutable Git checkpoint."""
    return _transitions.historical_feature_fingerprint(feature_dir, commit, ns=_NS)


def cmd_reconcile_feature(args: argparse.Namespace) -> None:
    """Explicitly supersede a feature fingerprint under .agent-state CAS."""
    return _transitions.cmd_reconcile_feature(args, ns=_NS)


def accept_verification_plan(feature_dir: pathlib.Path, plan_record: dict, *,
                             expected_generation: int) -> dict:
    """Bind a published immutable plan through the sole .agent-state CAS."""
    from verification.authority import validate_plan_record
    validate_plan_record(plan_record, repository=feature_dir.resolve().parents[2], reconstruct=True)
    feature_dir = feature_dir.resolve()
    doc = load_validated(feature_dir)
    if (not isinstance(plan_record, dict) or
            plan_record.get('feature_id') != doc.get('feature', feature_dir.name) or
            plan_record.get('feature_fingerprint') != feature_fingerprint(feature_dir) or
            type(plan_record.get('lifecycle_generation')) is not int or
            plan_record.get('lifecycle_generation') != expected_generation):
        die('VERIFICATION_PLAN_ACCEPTANCE_REJECTED: plan feature/generation binding differs')
    with lifecycle_state_lock(feature_dir) as lock:
        state = _load_state_unlocked(feature_dir, doc)
        generation = state.get('feature_generation', 1)
        if generation != expected_generation:
            die('VERIFICATION_PLAN_ACCEPTANCE_CAS_CONFLICT: lifecycle generation changed')
        task_state = state.get('tasks', {}).get(plan_record.get('task_id'))
        if (not isinstance(task_state, dict) or task_state.get('status') != 'running' or
                task_state.get('attempts') != plan_record.get('task_attempt')):
            die('VERIFICATION_PLAN_ACCEPTANCE_REJECTED: task attempt is not current and running')
        current = state.get('verification_authority')
        binding = {key: plan_record.get(key) for key in (
            'schema_version', 'profile_id', 'plan_id', 'task_id', 'task_attempt', 'feature_fingerprint', 'lifecycle_generation', 'family',
            'profile_hash', 'policy_checkpoint', 'candidate_identity',
            'final_changed_surface_id', 'origin_binding')}
        if (isinstance(current, dict) and current.get('accepted_plan_id') == plan_record['plan_id'] and
                current.get('binding') == binding and
                isinstance(current.get('plan_acceptance_transition_id'), str)):
            return current
        if isinstance(current, dict) and current.get('accepted_plan_id') not in (None, plan_record['plan_id']):
            old_binding = current.get('binding')
            old_task = (state.get('tasks', {}).get(old_binding.get('task_id'))
                        if isinstance(old_binding, dict) else None)
            if (isinstance(old_task, dict) and old_task.get('status') == 'running' and
                    old_task.get('attempts') == old_binding.get('task_attempt')):
                die('VERIFICATION_PLAN_ACCEPTANCE_REPLAN_REQUIRED: prior accepted execution is still current')
            history = state.setdefault('verification_plan_history', [])
            if not isinstance(history, list):
                die('VERIFICATION_PLAN_ACCEPTANCE_REJECTED: malformed plan history')
            history.append({'prior_authority': current, 'superseded_by': plan_record['plan_id'],
                'superseded_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                'reason': 'new trusted task-attempt plan accepted through lifecycle CAS'})
        predecessor = current.get('plan_acceptance_transition_id') if isinstance(current, dict) else None
        if predecessor is not None and not isinstance(predecessor, str):
            die('VERIFICATION_PLAN_ACCEPTANCE_REJECTED: prior transition is malformed')
        plan_digest = canonical_json_sha256(plan_record)
        transition_id = _append_lifecycle_transition(
            state, feature_dir, operation='verification-plan-accepted',
            task_id=plan_record['task_id'], attempt=plan_record['task_attempt'],
            generation=generation, predecessor_ids=[predecessor] if predecessor else [],
            record_refs=[{'record_id': plan_record['plan_id'], 'sha256': plan_digest}])
        state['verification_authority'] = {'accepted_plan_id': plan_record['plan_id'],
            'generation': generation, 'binding': binding,
            'plan_acceptance_transition_id': transition_id,
            'accepted_at': dt.datetime.now(dt.timezone.utc).isoformat()}
        save_state(feature_dir, state)
        if fcntl is not None: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return state['verification_authority']


def resolve_accepted_verification_plan(repository: pathlib.Path, plan_id: str, *,
                                       allowed_task_statuses: set[str] | None = None) -> dict:
    """Resolve subordinate plan bytes and prove current .agent-state acceptance (the library's)."""
    import verification.authority as verification_authority
    return _checks.resolve_accepted_verification_plan(
        repository, plan_id, allowed_task_statuses=allowed_task_statuses, access=_state_access(),
        feature_dir_for=lambda root, name: root / 'docs' / 'specs' / name,
        verification_authority=lambda: verification_authority)


def _append_lifecycle_transition(state: dict[str, Any], feature_dir: pathlib.Path, *,
                                 operation: str, task_id: str, attempt: int, generation: int,
                                 predecessor_ids: list[str], record_refs: list[dict[str, str]],
                                 transition_id: str | None = None) -> str:
    """Append one auditable transition while the canonical state lock is held."""
    import uuid
    revision = state.get('state_revision', 0)
    transitions = state.get('lifecycle_transitions', [])
    if (type(revision) is not int or revision < 0 or not isinstance(transitions, list) or
            any(not isinstance(value, dict) for value in transitions)):
        die('LIFECYCLE_TRANSITION_INVALID: state revision/history is malformed')
    if any(item.get('after_state_revision') != index + 1
           for index, item in enumerate(transitions)):
        die('LIFECYCLE_TRANSITION_INVALID: transition history is discontinuous')
    transition_id = transition_id or str(uuid.uuid4())
    before = revision
    after = before + 1
    scope = f"{feature_dir.name}/{task_id}/{attempt}"
    entry = {'transition_id': transition_id, 'operation': operation, 'scope': scope,
             'lifecycle_generation': generation, 'before_state_revision': before,
             'after_state_revision': after, 'predecessor_transition_ids': list(predecessor_ids),
             'record_refs': list(record_refs), 'recorded_at': utc_now().isoformat()}
    state['lifecycle_transitions'] = [*transitions, entry]
    state['state_revision'] = after
    return transition_id


def admit_verification_execution(repository: pathlib.Path, plan_id: str, unit_id: str, *,
                                 expected_generation: int, expected_state_revision: int,
                                 repository_admission_id: str | None = None) -> dict:
    """CAS-bind exact immutable admission and launch reservation in one .agent-state write."""
    from verification.authority import build_plan_admission_record, build_plan_launch_reservation
    from verification.store import StoreError, VerificationStore
    repository = pathlib.Path(repository).resolve(strict=True)
    plan = resolve_accepted_verification_plan(repository, plan_id)
    feature_dir = repository / 'docs' / 'specs' / plan['feature_id']
    doc = load_validated(feature_dir)
    if type(expected_state_revision) is not int or expected_state_revision < 0:
        raise StoreError('VERIFICATION_EXECUTION_PLAN_STALE')
    store = VerificationStore(repository)
    from verification.authority import validate_plan_record
    with lifecycle_state_lock(feature_dir, repository_admission_id=repository_admission_id,
                                   allowed_reservation=(plan_id, unit_id)):
        state = _load_state_unlocked(feature_dir, doc)
        authority = state.get('verification_authority')
        generation = state.get('feature_generation', 1)
        if (state.get('state_revision', 0) != expected_state_revision or
                generation != expected_generation or plan.get('lifecycle_generation') != generation or
                not isinstance(authority, dict) or authority.get('accepted_plan_id') != plan_id or
                authority.get('generation') != generation or
                not isinstance(authority.get('plan_acceptance_transition_id'), str)):
            raise StoreError('VERIFICATION_EXECUTION_PLAN_STALE')
        task_state = state.get('tasks', {}).get(plan['task_id'])
        if (not isinstance(task_state, dict) or task_state.get('status') != 'running' or
                task_state.get('attempts') != plan['task_attempt']):
            raise StoreError('VERIFICATION_ATTEMPT_BINDING_MISMATCH')
        # Reconstruct candidate and plan while holding lifecycle authority so a
        # stale surface cannot be committed between validation and CAS.
        validate_plan_record(plan, repository=repository, reconstruct=True)
        from verification.authority import plan_admission_id as _plan_admission_id
        units = [item for item in plan['execution_units'] if item.get('unit_id') == unit_id]
        if len(units) != 1:
            raise StoreError('PLAN_BINDING_MISMATCH')
        current = authority.get('launch_reservations', {})
        if not isinstance(current, dict):
            raise StoreError('LIFECYCLE_TRANSITION_INVALID')
        existing = current.get(unit_id)
        if isinstance(existing, dict):
            if existing.get('status') in {'launch_reserved', 'launch_consumed', 'execution_started'}:
                admission = authority.get('admissions', {}).get(unit_id)
                if (not isinstance(admission, dict) or admission.get('admission_id') != existing.get('admission_id') or
                        existing.get('plan_id') != plan_id):
                    raise StoreError('VERIFICATION_EXECUTION_PLAN_CONFLICT')
                bound = store.load_admission_record(admission['admission_id'],
                    expected_hash=authority.get('admission_hashes', {}).get(unit_id))
                return {'admission': bound, 'admission_sha256': authority['admission_hashes'][unit_id],
                        'reservation': existing, 'state_revision': state.get('state_revision', 0),
                        'plan_acceptance_transition_id': authority['plan_acceptance_transition_id']}
        import uuid
        transition_id = str(uuid.uuid4())
        admission = build_plan_admission_record(plan, unit_id,
            repository_id=store.repository_id,
            plan_acceptance_transition_id=authority['plan_acceptance_transition_id'],
            admission_transition_id=transition_id)
        admission, admission_hash = store.publish_admission_record(admission)
        reservation = build_plan_launch_reservation(admission, transition_id=transition_id)
        admissions = dict(authority.get('admissions', {}))
        reservations = dict(authority.get('launch_reservations', {}))
        hashes = dict(authority.get('admission_hashes', {}))
        admissions[unit_id] = admission
        reservations[unit_id] = reservation
        hashes[unit_id] = admission_hash
        before_revision = state.get('state_revision', 0)
        transition_id = _append_lifecycle_transition(state, feature_dir, operation='admission-reserved',
            task_id=plan['task_id'], attempt=plan['task_attempt'], generation=generation,
            predecessor_ids=[authority['plan_acceptance_transition_id']],
            record_refs=[{'record_id': admission['admission_id'], 'sha256': admission_hash},
                         {'record_id': reservation['reservation_id'], 'sha256':
                          canonical_json_sha256(reservation)}], transition_id=transition_id)
        if transition_id != reservation['transition_id'] or transition_id != admission['admission_transition_id']:
            raise StoreError('LIFECYCLE_TRANSITION_INVALID')
        authority = dict(authority)
        authority.update({'admissions': admissions, 'launch_reservations': reservations,
                          'admission_hashes': hashes})
        state['verification_authority'] = authority
        if state.get('state_revision', 0) != before_revision + 1:
            raise StoreError('VERIFICATION_EXECUTION_PLAN_STALE')
        save_state(feature_dir, state)
        return {'admission': admissions[unit_id], 'admission_sha256': hashes[unit_id],
                'reservation': reservations[unit_id], 'state_revision': state['state_revision'],
                'plan_acceptance_transition_id': authority['plan_acceptance_transition_id']}


def consume_verification_launch(repository: pathlib.Path, plan_id: str, unit_id: str, *,
                                reservation_id: str, expected_generation: int,
                                expected_state_revision: int,
                                repository_admission_id: str) -> dict:
    """Spend one exact launch right by lifecycle CAS and return a process-local capability."""
    from verification.authority import (build_launch_consumption_record,
        validate_plan_admission_record, validate_plan_launch_reservation)
    from verification.supervisor import issue_launch_capability
    from verification.store import StoreError, VerificationStore
    repository = pathlib.Path(repository).resolve(strict=True)
    plan = resolve_accepted_verification_plan(repository, plan_id)
    feature_dir = repository / 'docs' / 'specs' / plan['feature_id']
    doc = load_validated(feature_dir)
    store = VerificationStore(repository)
    units = [item for item in plan['execution_units'] if item.get('unit_id') == unit_id]
    if len(units) != 1 or type(expected_state_revision) is not int:
        raise StoreError('PLAN_BINDING_MISMATCH')
    with lifecycle_state_lock(feature_dir, repository_admission_id=repository_admission_id,
                                   allowed_reservation=(plan_id, unit_id)):
        state = _load_state_unlocked(feature_dir, doc)
        authority = state.get('verification_authority')
        generation = state.get('feature_generation', 1)
        if (state.get('state_revision', 0) != expected_state_revision or
                generation != expected_generation or plan.get('lifecycle_generation') != generation or
                not isinstance(authority, dict) or authority.get('accepted_plan_id') != plan_id or
                authority.get('generation') != generation):
            raise StoreError('VERIFICATION_EXECUTION_PLAN_STALE')
        reservations = authority.get('launch_reservations')
        admissions = authority.get('admissions')
        hashes = authority.get('admission_hashes')
        consumptions = authority.get('launch_consumptions', {})
        if not all(isinstance(value, dict) for value in (reservations, admissions, hashes, consumptions)):
            raise StoreError('LIFECYCLE_TRANSITION_INVALID')
        reservation = reservations.get(unit_id)
        admission = admissions.get(unit_id)
        if (not isinstance(reservation, dict) or reservation.get('reservation_id') != reservation_id or
                reservation.get('status') != 'launch_reserved' or
                not isinstance(admission, dict) or not isinstance(hashes.get(unit_id), str)):
            raise StoreError('VERIFICATION_EXECUTION_PLAN_STALE')
        bound_admission = store.load_admission_record(admission.get('admission_id'),
            expected_hash=hashes[unit_id])
        validate_plan_admission_record(bound_admission, plan, units[0])
        validate_plan_launch_reservation(reservation, bound_admission)
        if unit_id in consumptions:
            raise StoreError('VERIFICATION_EXECUTION_PLAN_CONFLICT')
        task_state = state.get('tasks', {}).get(plan['task_id'])
        if (not isinstance(task_state, dict) or task_state.get('status') != 'running' or
                task_state.get('attempts') != plan['task_attempt']):
            raise StoreError('VERIFICATION_ATTEMPT_BINDING_MISMATCH')
        import uuid
        transition_id = str(uuid.uuid4())
        consumption = build_launch_consumption_record(bound_admission, reservation,
                                                       transition_id=transition_id)
        consumption, consumption_hash = store.publish_launch_consumption_record(consumption)
        before_revision = state.get('state_revision', 0)
        transition_id = _append_lifecycle_transition(state, feature_dir, operation='launch-consumed',
            task_id=plan['task_id'], attempt=plan['task_attempt'], generation=generation,
            predecessor_ids=[reservation['transition_id']],
            record_refs=[{'record_id': consumption['consumption_id'], 'sha256': consumption_hash}],
            transition_id=transition_id)
        if transition_id != consumption['transition_id']:
            raise StoreError('LIFECYCLE_TRANSITION_INVALID')
        authority = dict(authority)
        authority['launch_consumptions'] = {**consumptions, unit_id: {
            'consumption_id': consumption['consumption_id'], 'sha256': consumption_hash,
            'reservation_id': reservation_id, 'transition_id': transition_id,
            'status': 'launch_consumed'}}
        state['verification_authority'] = authority
        save_state(feature_dir, state)
        return {'consumption': consumption, 'consumption_sha256': consumption_hash,
                'state_revision': state['state_revision'],
                'capability': issue_launch_capability(consumption['consumption_id'], reservation_id)}


def recover_prelaunch_authority(repository: pathlib.Path, plan_id: str, unit_id: str, *,
                                execution_id: str) -> dict[str, Any]:
    """Recover an exact reserved launch as consumed authority for safe abort.

    The caller must have fenced the prior owner or caught a launch-authorizer
    exception before the supervisor invokes the backend. A reservation without
    consumption is consumed here, then terminalized as a safe prelaunch abort;
    it is never released back into a launchable state.
    """
    from verification.authority import (build_launch_consumption_record,
        validate_plan_admission_record, validate_plan_launch_reservation)
    from verification.store import StoreError, VerificationStore
    repository = pathlib.Path(repository).resolve(strict=True)
    plan = resolve_accepted_verification_plan(repository, plan_id)
    feature_dir = repository / 'docs' / 'specs' / plan['feature_id']
    doc = load_validated(feature_dir)
    store = VerificationStore(repository)
    with lifecycle_state_lock(feature_dir, repository_admission_id=execution_id,
                                   allowed_reservation=(plan_id, unit_id)):
        state = _read_state_unlocked_pure(feature_dir, doc)
        authority = state.get('verification_authority')
        if not isinstance(authority, dict) or authority.get('accepted_plan_id') != plan_id:
            raise StoreError('VERIFICATION_LAUNCH_AUTHORITY_ABSENT')
        reservation = authority.get('launch_reservations', {}).get(unit_id)
        consumption = authority.get('launch_consumptions', {}).get(unit_id)
        admission = authority.get('admissions', {}).get(unit_id)
        admission_hash = authority.get('admission_hashes', {}).get(unit_id)
        if (not isinstance(reservation, dict) or reservation.get('status') != 'launch_reserved' or
                not isinstance(admission, dict) or type(admission_hash) is not str or
                (consumption is not None and (not isinstance(consumption, dict) or
                    consumption.get('status') != 'launch_consumed' or
                    consumption.get('reservation_id') != reservation.get('reservation_id')))):
            raise StoreError('VERIFICATION_LAUNCH_AUTHORITY_ABSENT')
        bound_admission = store.load_admission_record(admission['admission_id'],
                                                      expected_hash=admission_hash)
        units = [item for item in plan.get('execution_units', [])
                 if isinstance(item, dict) and item.get('unit_id') == unit_id]
        if len(units) != 1:
            raise StoreError('VERIFICATION_LAUNCH_AUTHORITY_ABSENT')
        validate_plan_admission_record(bound_admission, plan, units[0])
        validate_plan_launch_reservation(reservation, bound_admission)
        if consumption is None:
            # A crash after admission/reservation but before the consumption CAS
            # still burns this exact launch right. The supervisor will record an
            # ABORTED receipt and no backend launch marker can follow.
            import uuid
            transition_id = str(uuid.uuid4())
            recovered_consumption = build_launch_consumption_record(
                bound_admission, reservation, transition_id=transition_id)
            recovered_consumption, consumption_hash = store.publish_launch_consumption_record(
                recovered_consumption)
            transition_id = _append_lifecycle_transition(state, feature_dir,
                operation='launch-consumed-for-safe-abort', task_id=plan['task_id'],
                attempt=plan['task_attempt'], generation=plan['lifecycle_generation'],
                predecessor_ids=[reservation['transition_id']],
                record_refs=[{'record_id': recovered_consumption['consumption_id'],
                              'sha256': consumption_hash}], transition_id=transition_id)
            if recovered_consumption['transition_id'] != transition_id:
                raise StoreError('VERIFICATION_LAUNCH_AUTHORITY_ABSENT')
            authority = dict(authority)
            consumption = {'consumption_id': recovered_consumption['consumption_id'],
                'sha256': consumption_hash, 'reservation_id': reservation['reservation_id'],
                'transition_id': transition_id, 'status': 'launch_consumed'}
            authority['launch_consumptions'] = {**authority.get('launch_consumptions', {}),
                                                unit_id: consumption}
            state['verification_authority'] = authority
            save_state(feature_dir, state)
        bound_consumption = store.load_launch_consumption_record(
            consumption.get('consumption_id'), admission=bound_admission,
            reservation=reservation, expected_hash=consumption.get('sha256'))
        if (bound_consumption.get('transition_id') != consumption.get('transition_id') or
                bound_consumption.get('consumption_id') != consumption.get('consumption_id')):
            raise StoreError('VERIFICATION_LAUNCH_AUTHORITY_ABSENT')
        return {'schema_version': 1, 'execution_id': execution_id,
            'reservation_id': reservation['reservation_id'],
            'consumption_id': consumption['consumption_id'], 'plan_id': plan_id,
            'unit_id': unit_id, 'obligation_ids': list(units[0]['obligation_ids']),
            'admission_id': admission['admission_id'], 'admission_sha256': admission_hash,
            'reservation_transition_id': reservation['transition_id'],
            'consumption_transition_id': consumption['transition_id'],
            'plan_acceptance_transition_id': authority['plan_acceptance_transition_id'],
            'lifecycle_generation': plan['lifecycle_generation']}


def terminalize_verification_execution(repository: pathlib.Path, terminal: dict, *,
                                        repository_admission_id: str,
                                        safe_abort_proof=None) -> dict:
    """CAS-bind a drained receipt to the exact consumed plan reservation."""
    from verification.store import StoreError, VerificationStore
    required = ('plan_id', 'unit_id', 'launch_reservation_id', 'launch_consumption_id',
                'admission_id', 'admission_sha256', 'reservation_transition_id',
                'consumption_transition_id', 'plan_acceptance_transition_id',
                'lifecycle_generation', 'obligation_ids', 'receipt_hash', 'execution_id')
    if (not isinstance(terminal, dict) or any(key not in terminal for key in required) or
            terminal.get('drainage') != 'DRAINED' or
            not isinstance(repository_admission_id, str) or
            repository_admission_id != terminal.get('execution_id')):
        raise StoreError('VERIFICATION_TERMINAL_BINDING_MISMATCH')
    repository = pathlib.Path(repository).resolve(strict=True)
    store = VerificationStore(repository)
    records = store.reconstruct_execution_terminals(rebuild=False)
    matched = [record for record in records if record.get('execution_id') == terminal['execution_id']]
    if len(matched) != 1 or matched[0] != terminal:
        raise StoreError('VERIFICATION_TERMINAL_BINDING_MISMATCH')
    plan = resolve_accepted_verification_plan(repository, terminal['plan_id'])
    feature_dir = repository / 'docs' / 'specs' / plan['feature_id']
    doc = load_validated(feature_dir)
    units = [unit for unit in plan.get('execution_units', [])
             if isinstance(unit, dict) and unit.get('unit_id') == terminal['unit_id']]
    if len(units) != 1 or terminal.get('obligation_ids') != units[0].get('obligation_ids'):
        raise StoreError('VERIFICATION_TERMINAL_BINDING_MISMATCH')
    with lifecycle_state_lock(feature_dir, repository_admission_id=repository_admission_id,
                                   allowed_reservation=(terminal['plan_id'], terminal['unit_id'])):
        state = _load_state_unlocked(feature_dir, doc)
        authority = state.get('verification_authority')
        generation = state.get('feature_generation', 1)
        if (not isinstance(authority, dict) or authority.get('accepted_plan_id') != plan['plan_id'] or
                authority.get('generation') != generation or generation != terminal['lifecycle_generation'] or
                authority.get('plan_acceptance_transition_id') != terminal['plan_acceptance_transition_id']):
            raise StoreError('VERIFICATION_TERMINAL_BINDING_MISMATCH')
        reservations = authority.get('launch_reservations')
        consumptions = authority.get('launch_consumptions')
        admissions = authority.get('admissions')
        hashes = authority.get('admission_hashes')
        if not all(isinstance(value, dict) for value in (reservations, consumptions, admissions, hashes)):
            raise StoreError('LIFECYCLE_TRANSITION_INVALID')
        reservation = reservations.get(terminal['unit_id'])
        consumption = consumptions.get(terminal['unit_id'])
        admission = admissions.get(terminal['unit_id'])
        if (not isinstance(reservation, dict) or not isinstance(consumption, dict) or
                not isinstance(admission, dict) or
                reservation.get('reservation_id') != terminal['launch_reservation_id'] or
                admission.get('admission_id') != terminal['admission_id'] or
                hashes.get(terminal['unit_id']) != terminal['admission_sha256'] or
                reservation.get('transition_id') != terminal['reservation_transition_id'] or
                consumption.get('consumption_id') != terminal['launch_consumption_id'] or
                consumption.get('reservation_id') != terminal['launch_reservation_id'] or
                consumption.get('status') not in {'launch_consumed', 'execution_terminal', 'safe_prelaunch_abort'}):
            raise StoreError('VERIFICATION_TERMINAL_BINDING_MISMATCH')
        if reservation.get('status') in {'execution_terminal', 'safe_prelaunch_abort'}:
            if reservation.get('terminal_receipt_hash') == terminal['receipt_hash']:
                return reservation
            raise StoreError('VERIFICATION_TERMINAL_CONFLICT')
        safe_prelaunch_abort = (
            terminal.get('result') == 'ABORTED' and
            terminal.get('harness_invocation_upper_bound') == 0 and
            terminal.get('timed_out') is False and terminal.get('cancelled') is False and
            terminal.get('output_observation') == 'UNAVAILABLE')
        if safe_prelaunch_abort:
            if (reservation.get('status') != 'launch_reserved' or
                    consumption.get('status') != 'launch_consumed'):
                raise StoreError('VERIFICATION_TERMINAL_BINDING_MISMATCH')
            from verification.supervisor import validate_safe_prelaunch_abort_proof
            if not validate_safe_prelaunch_abort_proof(safe_abort_proof, terminal):
                raise StoreError('SAFE_PRELAUNCH_ABORT_PROOF_REQUIRED')
            before = state.get('state_revision', 0)
            import uuid
            transition_id = str(uuid.uuid4())
            _append_lifecycle_transition(state, feature_dir, operation='safe-prelaunch-abort',
                task_id=plan['task_id'], attempt=plan['task_attempt'], generation=generation,
                predecessor_ids=[terminal['consumption_transition_id']],
                record_refs=[{'record_id': terminal['execution_id'], 'sha256': terminal['receipt_hash']}],
                transition_id=transition_id)
            reservations = dict(reservations)
            reservations[terminal['unit_id']] = {**reservation,
                'status': 'safe_prelaunch_abort', 'terminal_receipt_hash': terminal['receipt_hash'],
                'terminal_transition_id': transition_id}
            consumptions = dict(consumptions)
            consumptions[terminal['unit_id']] = {**consumption,
                'status': 'safe_prelaunch_abort', 'terminal_receipt_hash': terminal['receipt_hash'],
                'terminal_transition_id': transition_id}
            authority = dict(authority)
            authority['launch_reservations'] = reservations
            authority['launch_consumptions'] = consumptions
            state['verification_authority'] = authority
            if state.get('state_revision') != before + 1:
                raise StoreError('VERIFICATION_TERMINAL_BINDING_MISMATCH')
            save_state(feature_dir, state)
            return reservations[terminal['unit_id']]
        if reservation.get('status') != 'launch_reserved' or consumption.get('status') != 'launch_consumed':
            raise StoreError('VERIFICATION_TERMINAL_BINDING_MISMATCH')
        from verification.authority import validate_plan_admission_record, validate_plan_launch_reservation
        bound_admission = store.load_admission_record(admission['admission_id'], expected_hash=hashes[terminal['unit_id']])
        validate_plan_admission_record(bound_admission, plan, units[0])
        validate_plan_launch_reservation(reservation, bound_admission)
        consumption_hash = consumption.get('sha256')
        if (not isinstance(consumption_hash, str) or
                terminal.get('consumption_transition_id') != consumption.get('transition_id')):
            raise StoreError('VERIFICATION_TERMINAL_BINDING_MISMATCH')
        bound_consumption = store.load_launch_consumption_record(
            consumption['consumption_id'], admission=bound_admission,
            reservation=reservation, expected_hash=consumption_hash)
        if (bound_consumption.get('consumption_id') != terminal['launch_consumption_id'] or
                bound_consumption.get('transition_id') != terminal['consumption_transition_id']):
            raise StoreError('VERIFICATION_TERMINAL_BINDING_MISMATCH')
        before = state.get('state_revision', 0)
        import uuid
        transition_id = str(uuid.uuid4())
        _append_lifecycle_transition(state, feature_dir, operation='execution-terminalized',
            task_id=plan['task_id'], attempt=plan['task_attempt'], generation=generation,
            predecessor_ids=[terminal['consumption_transition_id']],
            record_refs=[{'record_id': terminal['execution_id'], 'sha256': terminal['receipt_hash']}],
            transition_id=transition_id)
        if state.get('state_revision') != before + 1:
            raise StoreError('VERIFICATION_TERMINAL_BINDING_MISMATCH')
        reservation = {**reservation, 'status': 'execution_terminal',
                       'terminal_receipt_hash': terminal['receipt_hash'],
                       'terminal_result': terminal.get('result'),
                       'terminal_transition_id': transition_id}
        consumption = {**consumption, 'status': 'execution_terminal',
                       'terminal_receipt_hash': terminal['receipt_hash'],
                       'terminal_transition_id': transition_id}
        authority = dict(authority)
        authority['launch_reservations'] = {**reservations, terminal['unit_id']: reservation}
        authority['launch_consumptions'] = {**consumptions, terminal['unit_id']: consumption}
        state['verification_authority'] = authority
        save_state(feature_dir, state)
        return reservation



def legacy_state_name(feature_dir: pathlib.Path) -> str:
    return _state.legacy_state_name(feature_dir, policy=_policy(), common_dir=lambda path: git_common_dir(path))


def main_legacy_state_path(feature_dir: pathlib.Path) -> pathlib.Path:
    """Old main-checkout file; the only legacy snapshot safe to adopt automatically."""
    return _state.main_legacy_state_path(feature_dir, policy=_policy(), common_dir=lambda path: git_common_dir(path))


def save_state(feature_dir: pathlib.Path, state: dict[str, Any], *,
               additional_files: dict[str, tuple[bytes, int]] | None = None,
               remove_files: set[str] | None = None) -> None:
    """Write a complete authority snapshot before atomically replacing state (the library's single writer).

    Lifecycle callers hold the shared repository lock for the whole mutation.
    """
    _state.save_state(feature_dir, state, additional_files=additional_files, remove_files=remove_files,
                      policy=_policy(), fault_injector=_state_snapshot_fault_injector,
                      common_dir=lambda path: git_common_dir(path))


def completion_authority_dir(feature_dir: pathlib.Path, kind: str, task_id: str) -> pathlib.Path:
    return _access.completion_authority_dir(feature_dir, kind, task_id, access=_state_access())


legacy_completion_binding_id = _access.legacy_completion_binding_id


def legacy_completion_source_id(source: dict[str, Any]) -> str:
    semantic = {key: value for key, value in source.items() if key != 'source_id'}
    return f'legacy-completion-source-v1:sha256:{canonical_json_sha256(semantic)}'


def publish_legacy_source(feature_dir: pathlib.Path, task_id: str,
                          source: dict[str, Any]) -> bool:
    source = dict(source)
    source['schema_version'] = 1
    source['source_id'] = legacy_completion_source_id(source)
    path = runtime_state_dir(feature_dir) / 'legacy-completion-sources' / feature_dir.name / task_id / \
        f"{source['source_id'].rsplit(':', 1)[-1]}.json"
    with lock_path(feature_dir).open('a+', encoding='utf-8') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            created = publish_completion_authority(path, source)
            return created
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def validate_legacy_binding_shape(binding: Any, feature_dir: pathlib.Path,
                                  task_id: str) -> bool:
    return _access.validate_legacy_binding_shape(binding, feature_dir, task_id, access=_state_access())


def legacy_completion_bindings(feature_dir: pathlib.Path, task_id: str) -> list[dict[str, Any]]:
    return _access.legacy_completion_bindings(feature_dir, task_id, access=_state_access())


def legacy_binding_as_completion(feature_dir: pathlib.Path, task_id: str,
                                 binding: dict[str, Any]) -> dict[str, Any]:
    return _access.legacy_binding_as_completion(feature_dir, task_id, binding, access=_state_access())


completion_record_id = _access.completion_record_id


REPAIR_CHECKPOINT_BINDING_SCHEME = 'completion-repair-checkpoint-binding-v1'


def repair_checkpoint_binding_id(record: dict[str, Any]) -> str:
    semantic = {key: value for key, value in record.items()
                if key not in {'record_id', 'created_at'}}
    return f'{REPAIR_CHECKPOINT_BINDING_SCHEME}:sha256:{canonical_json_sha256(semantic)}'


def repair_checkpoint_bindings(feature_dir: pathlib.Path, task_id: str) -> list[dict[str, Any]]:
    directory = completion_authority_dir(feature_dir, 'completion-repair-checkpoint-bindings', task_id)
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        die('REPAIR_CHECKPOINT_BINDING_INVALID: authority directory is unsafe')
    records = []
    for path in sorted(directory.glob('*.json')):
        try:
            item = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            die(f'REPAIR_CHECKPOINT_BINDING_INVALID: unreadable binding: {exc}')
        required_strings = ('historical_completion_id', 'correction_id', 'repair_authorization_id',
                            'repair_claim_id', 'owner', 'operator', 'reason')
        if (path.is_symlink() or not path.is_file() or not isinstance(item, dict) or
                item.get('schema_version') != 1 or item.get('record_type') != REPAIR_CHECKPOINT_BINDING_SCHEME or
                item.get('protocol_version') != protocol_version(feature_dir) or
                item.get('record_id') != repair_checkpoint_binding_id(item) or
                path.stem != item['record_id'].rsplit(':', 1)[-1] or
                item.get('repository_id') != str(git_common_dir(feature_dir)) or
                item.get('feature') != feature_dir.name or item.get('task') != task_id or
                any(not isinstance(item.get(key), str) or not item[key].strip() for key in required_strings) or
                type(item.get('attempt')) is not int or item['attempt'] < 1 or
                not _valid_completion_commit(item.get('checkpoint')) or
                not _valid_completion_revision(item.get('packet_revision')) or
                not _valid_completion_hash(item.get('contract_fingerprint')) or
                not isinstance(item.get('created_at'), str) or not item['created_at'].strip()):
            die('REPAIR_CHECKPOINT_BINDING_INVALID: malformed or unsupported binding')
        records.append(item)
    by_claim: dict[str, list[dict[str, Any]]] = {}
    for item in records:
        by_claim.setdefault(item['repair_claim_id'], []).append(item)
    if any(len(items) != 1 for items in by_claim.values()):
        die('REPAIR_CHECKPOINT_BINDING_CONFLICT: multiple bindings exist for repair claim')
    return records


def effective_repair_checkpoint(feature_dir: pathlib.Path, task_id: str, claim: dict[str, Any],
                                active: dict[str, Any], historical: dict[str, Any],
                                correction: dict[str, Any], authorization: dict[str, Any]) -> str:
    # Newer immutable repair claims may carry the checkpoint directly.
    checkpoint = claim.get('checkpoint')
    if _valid_completion_commit(checkpoint):
        return checkpoint
    candidates = [item for item in repair_checkpoint_bindings(feature_dir, task_id)
                  if item.get('repair_claim_id') == claim.get('record_id')]
    if not candidates:
        die('REPAIR_CHECKPOINT_BINDING_REQUIRED: legacy repair claim has no immutable checkpoint binding')
    binding, = candidates
    expected = {
        'repository_id': str(git_common_dir(feature_dir)), 'feature': feature_dir.name, 'task': task_id,
        'attempt': claim.get('attempt'), 'historical_completion_id': historical.get('record_id'),
        'correction_id': correction.get('record_id'), 'repair_authorization_id': authorization.get('record_id'),
        'repair_claim_id': claim.get('record_id'), 'owner': claim.get('owner'),
        'packet_revision': active.get('revision_id'), 'contract_fingerprint': active.get('contract_sha256'),
    }
    if any(binding.get(key) != value for key, value in expected.items()):
        die('REPAIR_CHECKPOINT_BINDING_INVALID: binding does not match current repair authority')
    return binding['checkpoint']


def _completion_record_semantics(record: dict[str, Any]) -> dict[str, Any]:
    return _transitions._completion_record_semantics(record, ns=_NS)


_valid_completion_hash = _access._valid_completion_hash


_valid_completion_commit = _access._valid_completion_commit


_valid_completion_revision = _access._valid_completion_revision


validate_completion_authority_shape = _access.validate_completion_authority_shape


def publish_completion_authority(path: pathlib.Path, record: dict[str, Any]) -> bool:
    """Durably publish an immutable record. Return False for exact idempotent replay."""
    return _transitions.publish_completion_authority(path, record, ns=_NS)


def read_completion_authority(feature_dir: pathlib.Path, kind: str, task_id: str,
                              *, record_type: str) -> list[dict[str, Any]]:
    return _access.read_completion_authority(feature_dir, kind, task_id, record_type=record_type, access=_state_access())


def _completion_failpoint(boundary: str) -> None:
    return _transitions._completion_failpoint(boundary, ns=_NS)


def completion_records(feature_dir: pathlib.Path, task_id: str) -> list[dict[str, Any]]:
    return _access.completion_records(feature_dir, task_id, access=_state_access())


def correction_records(feature_dir: pathlib.Path, task_id: str) -> list[dict[str, Any]]:
    return _transitions.correction_records(feature_dir, task_id, ns=_NS)


def repair_authorization_records(feature_dir: pathlib.Path, task_id: str) -> list[dict[str, Any]]:
    return _transitions.repair_authorization_records(feature_dir, task_id, ns=_NS)


def repair_claim_records(feature_dir: pathlib.Path, task_id: str) -> list[dict[str, Any]]:
    return _transitions.repair_claim_records(feature_dir, task_id, ns=_NS)


def repaired_completion_records(feature_dir: pathlib.Path, task_id: str) -> list[dict[str, Any]]:
    return _access.repaired_completion_records(feature_dir, task_id, access=_state_access())


def correction_is_repaired(feature_dir: pathlib.Path, task_id: str,
                           correction: dict[str, Any]) -> bool:
    return _transitions.correction_is_repaired(feature_dir, task_id, correction, ns=_NS)


def correction_targets_current_completion(feature_dir: pathlib.Path, task_id: str,
                                          entry: dict[str, Any],
                                          correction: dict[str, Any]) -> bool:
    """Return whether lifecycle state still names the completion this correction repairs."""
    return _transitions.correction_targets_current_completion(feature_dir, task_id, entry, correction, ns=_NS)


def retire_finalized_completion_repair_pointers(feature_dir: pathlib.Path, task_id: str,
                                                entry: dict[str, Any], *, retired_by: str) -> None:
    """Move a finalized repair's active pointers into immutable attempt-tagged state history."""
    return _transitions.retire_finalized_completion_repair_pointers(feature_dir, task_id, entry, retired_by=retired_by, ns=_NS)


def task_has_effective_completion(feature_dir: pathlib.Path, task_id: str,
                                  entry: dict[str, Any]) -> bool:
    return _transitions.task_has_effective_completion(feature_dir, task_id, entry, ns=_NS)


def effective_task_status(feature_dir: pathlib.Path, task_id: str,
                          entry: dict[str, Any]) -> str:
    corrections = correction_records(feature_dir, task_id)
    if any(not correction_is_repaired(feature_dir, task_id, item) for item in corrections):
        return 'correction_required'
    if task_has_effective_completion(feature_dir, task_id, entry):
        return 'completed'
    return str(entry.get('status', 'pending'))


def remove_state_locked(feature_dir: pathlib.Path) -> None:
    _access.remove_state_locked(feature_dir, access=_state_access())


fail_on_verification_store_error = _access.fail_on_verification_store_error


def locked_state(feature_dir: pathlib.Path, doc: dict[str, Any], *,
                 admission_guard: bool = True) -> Any:
    return _access.locked_state(feature_dir, doc, admission_guard=admission_guard, access=_state_access())



def utc_now() -> dt.datetime:
    return _transitions.utc_now(ns=_NS)


def parse_timestamp(value: Any) -> dt.datetime | None:
    return _transitions.parse_timestamp(value, ns=_NS)


parse_aware_timestamp = _checks.parse_aware_timestamp


def lease_ttl_seconds(doc: dict[str, Any]) -> int:
    return _transitions.lease_ttl_seconds(doc, ns=_NS)


def heartbeat_interval_seconds(doc: dict[str, Any]) -> int:
    return _transitions.heartbeat_interval_seconds(doc, ns=_NS)


def refresh_lease(entry: dict[str, Any], doc: dict[str, Any], *, now: dt.datetime | None = None) -> None:
    return _transitions.refresh_lease(entry, doc, now=now, ns=_NS)


def lease_expired(entry: dict[str, Any], *, now: dt.datetime | None = None) -> bool:
    return _transitions.lease_expired(entry, now=now, ns=_NS)


def heartbeat(feature_dir: pathlib.Path, doc: dict[str, Any], task_id: str, owner: str) -> str:
    # Lease renewal does not claim/change a worktree and must remain available
    # while the verifier's durable repository reservation is active.
    return _transitions.heartbeat(feature_dir, doc, task_id, owner, ns=_NS)


def recover_stale_leases(feature_dir: pathlib.Path, doc: dict[str, Any], *, reason: str = 'lease expired') -> list[str]:
    """Recover expired task leases so a crashed orchestrator can be resumed safely.

    Dirty task worktrees are checkpointed only on that task-local branch as failed-attempt evidence.
    The checkpoint is never exposed to dependents because the task remains failed until re-executed
    and completed successfully.
    """
    recovered: list[str] = []
    now = utc_now()
    with locked_state(feature_dir, doc) as state:
        for task_id, entry in state['tasks'].items():
            if not lease_expired(entry, now=now):
                continue
            if isinstance(entry.get('completion_repair_claim'), dict):
                entry['status'] = 'correction_required'
                entry.setdefault('completion_repair_claim_history', []).append({
                    **entry.pop('completion_repair_claim'), 'recovered_at': now.isoformat(),
                    'recovery_reason': reason,
                })
                for key in ('owner', 'heartbeat_at', 'lease_expires_at', 'claimed_at'):
                    entry.pop(key, None)
                recovered.append(task_id)
                continue
            active_verification_resume = entry.pop('verification_resume_active', None)
            if is_unrecovered_partial_claim(entry):
                die(f'CLAIM_RECOVERY_REQUIRED: {task_id} is a stranded exceptional claim; attest with recover-claim before lease recovery')
            task = active_task_contract(feature_dir, doc, task_id, state=state)
            if not task:
                continue
            feature = str(doc.get('feature', feature_dir.name))
            target = worktree_path(feature, task_id)
            stale_commit = None
            if target.exists():
                stale_commit = checkpoint_worktree(doc, task, target, label='stale-lease')
            entry.update({
                'status': 'failed',
                'last_failure': reason,
                'lease_recovered_at': now.isoformat(),
                'last_attempt_commit': stale_commit,
            })
            if isinstance(active_verification_resume, dict):
                entry['verification_resume_pending'] = active_verification_resume
            entry.pop('start_origin_status', None)
            for key in ('owner', 'heartbeat_at', 'lease_expires_at'):
                entry.pop(key, None)
            recovered.append(task_id)
    return recovered


def assert_repository_verification_drained(feature_dir: pathlib.Path) -> None:
    """Refuse worktree lifecycle mutations while verifier ownership is unresolved."""
    try:
        from verification.store import StoreError as VerificationStoreError, VerificationStore, repository_lock
        store = VerificationStore(feature_dir)
        with repository_lock(store.root):
            store.admit_repository_verification()
    except Exception as exc:
        try:
            from verification.store import StoreError as VerificationStoreError
        except ImportError:
            die(f'VERIFICATION_OWNERSHIP_UNAVAILABLE: {type(exc).__name__}', code=5)
        if isinstance(exc, VerificationStoreError):
            message = str(exc)
            die(f'VERIFICATION_OWNERSHIP_BLOCKED: {message}',
                code={'busy': 3, 'verification-owned': 6}.get(message, 5))
        die(f'VERIFICATION_OWNERSHIP_BLOCKED: {type(exc).__name__}', code=5)


is_unrecovered_partial_claim = _checks.is_unrecovered_partial_claim


def ready_ids(doc: dict[str, Any], state: dict[str, Any], feature_dir: pathlib.Path | None = None) -> list[str]:
    return _transitions.ready_ids(doc, state, feature_dir, ns=_NS)


def cmd_resume_verification(args: argparse.Namespace) -> None:
    """Durably schedule exact-plan verification again without a new builder attempt."""
    doc = load_validated(args.feature_dir)
    reason = args.reason if isinstance(args.reason, str) else ''
    operator = str(getattr(args, 'by', '') or '').strip()
    if (args.task_id not in task_index(doc) or not reason.strip() or len(reason.encode('utf-8')) > 1024 or
            any(ord(char) < 32 and char not in '\t\n' for char in reason) or
            not operator or len(operator.encode('utf-8')) > 256 or
            any(ord(char) < 32 for char in operator)):
        die('VERIFICATION_RESUME_INVALID: task, reason, and operator are required')
    assert_repository_verification_drained(args.feature_dir)
    target = worktree_path(str(doc.get('feature', args.feature_dir.name)), args.task_id)
    if not target.is_dir() or target.is_symlink():
        die('VERIFICATION_RESUME_INVALID: the exact task worktree is unavailable')
    with locked_state(args.feature_dir, doc) as state:
        entry = state.get('tasks', {}).get(args.task_id)
        if (not isinstance(entry, dict) or entry.get('status') != 'escalated' or
                entry.get('control_outcome') != 'verification-blocked'):
            die('VERIFICATION_RESUME_INVALID: task is not stopped on verification-blocked')
        authority = state.get('verification_authority')
        plan_id = authority.get('accepted_plan_id') if isinstance(authority, dict) else None
        generation = state.get('feature_generation', 1)
        if not isinstance(plan_id, str) or authority.get('generation') != generation:
            die('VERIFICATION_RESUME_INVALID: current accepted plan is unavailable')
        try:
            plan = resolve_accepted_verification_plan(
                target, plan_id, allowed_task_statuses={'escalated'})
        except Exception as exc:
            die(f'VERIFICATION_RESUME_INVALID: accepted plan could not be reconstructed ({type(exc).__name__})')
        if (plan.get('feature_id') != doc.get('feature') or plan.get('task_id') != args.task_id or
                plan.get('task_attempt') != entry.get('attempts') or
                plan.get('lifecycle_generation') != generation):
            die('VERIFICATION_RESUME_INVALID: accepted plan does not bind this exact attempt')
        resume_history = entry.get('verification_resume_history', [])
        if not isinstance(resume_history, list):
            die('VERIFICATION_RESUME_INVALID: resume history is malformed')
        prior_resume = resume_history[-1] if resume_history else None
        if prior_resume is not None and not isinstance(prior_resume, dict):
            die('VERIFICATION_RESUME_INVALID: latest resume history entry is malformed')
        prior_result_path = (prior_resume.get('prior_result_path') if prior_resume else
                             entry.get('last_failure_evidence'))
        prior_result_sha256 = prior_builder_result_digest(
            args.feature_dir, entry, args.task_id, prior_result_path)
        if prior_resume is not None and prior_result_sha256 != prior_resume.get('prior_result_sha256'):
            die('VERIFICATION_RESUME_INVALID: prior builder result changed since the previous resume')
        history = entry.setdefault('verification_resume_history', [])
        if not isinstance(history, list):
            die('VERIFICATION_RESUME_INVALID: resume history is malformed')
        resume = {'plan_id': plan_id, 'task_attempt': entry['attempts'],
                  'lifecycle_generation': generation,
                  'reason_sha256': hashlib.sha256(reason.encode('utf-8')).hexdigest(),
                  'prior_result_sha256': prior_result_sha256,
                  'prior_result_path': prior_result_path,
                  'operator': operator,
                  'requested_at': utc_now().isoformat()}
        history.append(dict(resume))
        entry['verification_resume_pending'] = resume
        entry['status'] = 'failed'
        entry.pop('control_outcome', None)
    print(f'VERIFICATION_RESUME_READY {args.task_id} attempt={plan["task_attempt"]} plan={plan_id}')


def cmd_recover_verification(args: argparse.Namespace) -> None:
    """Recover verifier journals and clear only proven-dead unstarted admissions."""
    from verification.store import StoreError, VerificationStore, repository_lock
    from verification.supervisor import VerificationSupervisor
    try:
        store = VerificationStore(pathlib.Path(args.repo))
        results = VerificationSupervisor(store).recover()
        with repository_lock(store.root):
            store.admit_repository_verification()
    except (OSError, StoreError, RuntimeError, ValueError) as exc:
        die(f'VERIFICATION_RECOVERY_UNAVAILABLE: {type(exc).__name__}', code=5)
    print(json.dumps([{'execution_id': item.execution_id, 'state': item.state.value,
                       'reason_code': item.reason_code} for item in results], indent=2))


validate_verification_resume = _checks.validate_verification_resume


validate_active_verification_resume = _checks.validate_active_verification_resume


validate_pending_verification_resume = _checks.validate_pending_verification_resume


def prior_builder_result_digest(feature_dir: pathlib.Path, entry: dict[str, Any], task_id: str,
                                evidence_path: str | None = None) -> str:
    """Resolve and hash the exact passing builder result for a verification resume."""
    return _transitions.prior_builder_result_digest(feature_dir, entry, task_id, evidence_path, ns=_NS)


def trusted_task_history(repository: pathlib.Path, feature_id: str, *,
                         _lifecycle_state: dict | None = None) -> dict:
    """Project trusted task/attempt metrics from the validated DAG and lifecycle authorities."""
    from verification.store import StoreError
    if not isinstance(feature_id, str) or not FEATURE_ID_RE.fullmatch(feature_id):
        raise StoreError('LIFECYCLE_HISTORY_UNAVAILABLE')
    repository = pathlib.Path(repository).resolve(strict=True)
    feature_dir = repository / 'docs' / 'specs' / feature_id
    try:
        doc = load_validated(feature_dir)
        if doc.get('feature') != feature_id:
            raise StoreError('LIFECYCLE_HISTORY_UNAVAILABLE')
        if _lifecycle_state is None:
            state = load_state(feature_dir, doc)
        else:
            validate_state_identity(feature_dir, doc, _lifecycle_state)
            state = _lifecycle_state
    except (OSError, SystemExit, ValueError, TypeError):
        raise StoreError('LIFECYCLE_HISTORY_UNAVAILABLE') from None
    tasks = []
    for task in doc['tasks']:
        task_id = task['id']
        entry = state.get('tasks', {}).get(task_id)
        if not isinstance(entry, dict):
            raise StoreError('LIFECYCLE_HISTORY_UNAVAILABLE')
        try:
            validate_attempt_binding_ledger(entry)
            bindings = entry.get('attempt_bindings', [])
            completions = completion_records(feature_dir, task_id)
            corrections = correction_records(feature_dir, task_id)
        except (SystemExit, OSError, ValueError, TypeError):
            raise StoreError('LIFECYCLE_HISTORY_UNAVAILABLE') from None
        attempts = []
        for binding in sorted(bindings, key=lambda item: item['attempt']):
            attempt_no = binding['attempt']
            matching = [record for record in completions if record.get('attempt') == attempt_no]
            bound = matching[0] if len(matching) == 1 else None
            binding_matches = bool(bound and
                bound.get('packet_revision') == binding.get('packet_revision') and
                bound.get('contract_fingerprint') == binding.get('contract_sha256'))
            attempts.append({
                'attempt': attempt_no, 'binding_status': binding['binding_status'],
                'packet_revision': binding.get('packet_revision'),
                'contract_sha256': binding.get('contract_sha256'),
                'completion_count': len(matching),
                'completion': ({'record_id': bound.get('record_id'), 'checkpoint': bound.get('checkpoint'),
                                'created_at': bound.get('created_at')} if bound else None),
                'ordering': 'KNOWN' if binding_matches else 'UNKNOWN',
            })
        tasks.append({
            'task_id': task_id, 'role': task['role'],
            'compatible_manual_roles': tuple(sorted({task.get('role'), task.get('agent_profile'),
                                                       *task.get('required_reviewers', [])} - {None})),
            'status': entry.get('status'), 'attempts': tuple(attempts),
            'completion_count': len(completions), 'correction_count': len(corrections),
        })
    return {
        'repository_id': hashlib.sha256(os.fsencode(git_common_dir(feature_dir))).hexdigest(),
        'feature_id': feature_id, 'feature_generation': state.get('feature_generation', 1),
        'feature_fingerprint': feature_fingerprint(feature_dir), 'tasks': tuple(tasks),
    }


def resolve_manual_review_scope(repository: pathlib.Path, feature_id: str, *, role: str,
                                task_id: str | None = None, task_attempt: int | None = None,
                                checkpoint: str | None = None,
                                _lifecycle_state: dict | None = None) -> dict:
    """Resolve manual evidence scope from trusted lifecycle history; never trust caller metrics."""
    from verification.store import StoreError
    if role not in {'reviewer', 'evaluator', 'architecture-reviewer', 'verification-author'}:
        raise StoreError('MANUAL_EVIDENCE_SCOPE_INVALID')
    history = trusted_task_history(repository, feature_id, _lifecycle_state=_lifecycle_state)
    if task_attempt is None:
        if task_id is not None:
            if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
                raise StoreError('MANUAL_EVIDENCE_TASK_UNRESOLVED')
            task = next((item for item in history['tasks'] if item['task_id'] == task_id), None)
            if task is None:
                raise StoreError('MANUAL_EVIDENCE_TASK_UNRESOLVED')
            if role not in task['compatible_manual_roles']:
                raise StoreError('MANUAL_EVIDENCE_TASK_ROLE_MISMATCH')
            return {'repository_id': history['repository_id'], 'feature_id': feature_id,
                    'role': role, 'task_id': task_id, 'task_attempt': None,
                    'checkpoint': None, 'ordering': 'UNKNOWN'}
        return {'repository_id': history['repository_id'], 'feature_id': feature_id,
                'role': role, 'task_id': None, 'task_attempt': None,
                'checkpoint': None, 'ordering': 'UNKNOWN'}
    if type(task_attempt) is not int or task_attempt < 1:
        raise StoreError('MANUAL_EVIDENCE_ATTEMPT_UNRESOLVED')
    candidates = []
    for task in history['tasks']:
        if task_id is not None and task['task_id'] != task_id:
            continue
        for attempt in task['attempts']:
            completion = attempt.get('completion')
            if (attempt['attempt'] == task_attempt and attempt['ordering'] == 'KNOWN' and
                    isinstance(completion, dict) and completion.get('checkpoint') == checkpoint):
                candidates.append((task, attempt, completion))
    if len(candidates) != 1:
        raise StoreError('MANUAL_EVIDENCE_ATTEMPT_UNRESOLVED')
    task, attempt, completion = candidates[0]
    if role not in task['compatible_manual_roles']:
        raise StoreError('MANUAL_EVIDENCE_TASK_ROLE_MISMATCH')
    return {
        'repository_id': history['repository_id'], 'feature_id': feature_id,
        'feature_generation': history['feature_generation'], 'role': role,
        'task_id': task['task_id'], 'task_attempt': task_attempt,
        'packet_revision': attempt['packet_revision'], 'contract_sha256': attempt['contract_sha256'],
        'checkpoint': completion['checkpoint'], 'completion_record_id': completion['record_id'],
        'ordering': 'KNOWN',
    }


def register_manual_observation(repository: pathlib.Path, feature_id: str, *, observation_id: str,
                                role: str, task_id: str | None = None,
                                task_attempt: int | None = None, checkpoint: str | None = None,
                                attestation_sha256: str, report_sha256: str,
                                expected_feature_generation: int) -> dict[str, Any]:
    """Record immutable manual provenance under the lifecycle lock.

    T-005's telemetry `record-manual` boundary must validate the signed
    attestation against the accepted issuer registry before calling this API.
    This lifecycle API stores only its digest and re-resolves trusted scope; it
    does not authenticate the signer, accept metrics, or grant obligation coverage.
    """
    from verification.store import StoreError

    if (not isinstance(feature_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', feature_id) or
            not isinstance(observation_id, str) or not re.fullmatch(r'[0-9a-f]{64}', observation_id) or
            not isinstance(attestation_sha256, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', attestation_sha256) or
            not isinstance(report_sha256, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', report_sha256) or
            type(expected_feature_generation) is not int or expected_feature_generation < 1 or
            (task_attempt is not None and (type(task_attempt) is not int or task_attempt < 1))):
        raise StoreError('MANUAL_EVIDENCE_REGISTRATION_INVALID')
    repository = pathlib.Path(repository).resolve(strict=True)
    feature_dir = repository / 'docs' / 'specs' / feature_id
    if not feature_dir.is_dir() or feature_dir.is_symlink():
        raise StoreError('MANUAL_EVIDENCE_FEATURE_UNRESOLVED')
    try:
        doc = load_validated(feature_dir)
        if doc.get('feature') != feature_id:
            raise StoreError('MANUAL_EVIDENCE_FEATURE_UNRESOLVED')
        with locked_state(feature_dir, doc) as state:
            generation = state.get('feature_generation', 1)
            if type(generation) is not int or generation != expected_feature_generation:
                raise StoreError('MANUAL_EVIDENCE_STALE_FEATURE_GENERATION')
            scope = resolve_manual_review_scope(
                repository, feature_id, role=role, task_id=task_id,
                task_attempt=task_attempt, checkpoint=checkpoint,
                _lifecycle_state=state)
            repository_id = hashlib.sha256(os.fsencode(git_common_dir(feature_dir))).hexdigest()
            if scope.get('repository_id') != repository_id:
                raise StoreError('MANUAL_EVIDENCE_SCOPE_CONFLICT')
            record = {
                'schema_version': 1, 'record_type': 'manual-observation',
                'observation_id': observation_id, 'repository_id': repository_id,
                'feature_id': feature_id, 'feature_generation': generation,
                'role': role, 'scope': scope,
                'attestation_sha256': attestation_sha256,
                'report_sha256': report_sha256,
                'attestation_validation_owner': 'tooling/agent-harness/telemetry.py',
                'registered_at': utc_now().isoformat(),
            }
            authority_root = runtime_state_dir(feature_dir) / 'manual-observations' / feature_id
            manual_root = runtime_state_dir(feature_dir) / 'manual-observations'
            state_root = runtime_state_dir(feature_dir)
            if (state_root.is_symlink() or manual_root.is_symlink() or authority_root.is_symlink()):
                raise StoreError('MANUAL_EVIDENCE_AUTHORITY_UNAVAILABLE')
            authority_root.mkdir(parents=True, exist_ok=True)
            if authority_root.resolve().parent != manual_root.resolve():
                raise StoreError('MANUAL_EVIDENCE_AUTHORITY_UNAVAILABLE')
            record_path = authority_root / f'{observation_id}.json'
            payload = (json.dumps(record, indent=2, sort_keys=True) + '\n').encode('utf-8')
            relative_record_path = record_path.relative_to(runtime_state_dir(feature_dir)).as_posix()
            transaction = getattr(_authority_transaction, 'current', None)
            if (not isinstance(transaction, dict) or transaction.get('feature') != feature_id or
                    transaction.get('repository_id') != str(git_common_dir(feature_dir))):
                raise StoreError('MANUAL_EVIDENCE_AUTHORITY_UNAVAILABLE')
            staged = transaction['files'].get(relative_record_path)
            if record_path.exists() or record_path.is_symlink() or staged is not None:
                if record_path.is_symlink() or (record_path.exists() and not record_path.is_file()):
                    raise StoreError('MANUAL_EVIDENCE_OBSERVATION_CONFLICT') from None
                existing_bytes = (staged[0] if staged is not None else record_path.read_bytes())
                try:
                    existing = json.loads(existing_bytes)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise StoreError('MANUAL_EVIDENCE_OBSERVATION_CONFLICT') from None
                candidate = {key: value for key, value in record.items() if key != 'registered_at'}
                prior = ({key: value for key, value in existing.items() if key != 'registered_at'}
                         if isinstance(existing, dict) else None)
                if prior != candidate:
                    raise StoreError('MANUAL_EVIDENCE_OBSERVATION_CONFLICT') from None
                record = existing
                payload = existing_bytes
            else:
                transaction['files'][relative_record_path] = (payload, 0o600)

            ledger = state.get('manual_observation_ledger', [])
            if not isinstance(ledger, list):
                raise StoreError('MANUAL_EVIDENCE_AUTHORITY_INVALID')
            ledger_matches = [item for item in ledger if isinstance(item, dict) and
                              item.get('observation_id') == observation_id]
            ledger_entry = {
                'observation_id': observation_id,
                'record_path': str(record_path),
                'record_sha256': sha256_bytes(payload),
            }
            if ledger_matches and (len(ledger_matches) != 1 or ledger_matches[0] != ledger_entry):
                raise StoreError('MANUAL_EVIDENCE_OBSERVATION_CONFLICT')
            if not ledger_matches:
                ledger.append(ledger_entry)
                state['manual_observation_ledger'] = ledger
            return {**record, 'record_path': str(record_path),
                    'record_sha256': sha256_bytes(payload)}
    except StoreError:
        raise


def reviewers(task: dict[str, Any]) -> list[str]:
    selected: list[str] = []
    for risk in task.get('risk_tags', []):
        for reviewer in RISK_TO_REVIEWERS.get(risk, ()):
            if reviewer not in selected:
                selected.append(reviewer)
    return selected


def packet_payload(doc: dict[str, Any], task: dict[str, Any], feature_dir: pathlib.Path) -> dict[str, Any]:
    return _transitions.packet_payload(doc, task, feature_dir, ns=_NS)


def write_packet(doc: dict[str, Any], task: dict[str, Any], feature_dir: pathlib.Path,
                 *, state: dict[str, Any] | None = None) -> pathlib.Path:
    return _transitions.write_packet(doc, task, feature_dir, state=state, ns=_NS)


def legacy_packet_path(feature_dir: pathlib.Path, task_id: str) -> pathlib.Path:
    return _transitions.legacy_packet_path(feature_dir, task_id, ns=_NS)


safe_task_id = _access.safe_task_id


packet_revision_id = _checks.packet_revision_id


def legacy_revision_id(feature_dir: pathlib.Path, task_id: str) -> str:
    return _transitions.legacy_revision_id(feature_dir, task_id, ns=_NS)


def validate_packet_integrity(packet: dict[str, Any]) -> None:
    return _transitions.validate_packet_integrity(packet, ns=_NS)


def revision_path(feature_dir: pathlib.Path, task_id: str, revision_id: str) -> pathlib.Path:
    return _transitions.revision_path(feature_dir, task_id, revision_id, ns=_NS)


def resolve_active_packet(feature_dir: pathlib.Path, doc: dict[str, Any], task_id: str,
                          *, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve one validated active packet; legacy tasks keep their historical path."""
    return _transitions.resolve_active_packet(feature_dir, doc, task_id, state=state, ns=_NS)


def validate_attempt_binding_ledger(entry: dict[str, Any]) -> None:
    return _transitions.validate_attempt_binding_ledger(entry, ns=_NS)


def append_attempt_binding(entry: dict[str, Any], feature_dir: pathlib.Path, doc: dict[str, Any],
                           task_id: str, packet_path: pathlib.Path) -> None:
    return _transitions.append_attempt_binding(entry, feature_dir, doc, task_id, packet_path, ns=_NS)


def active_task_contract(feature_dir: pathlib.Path, doc: dict[str, Any], task_id: str,
                         *, state: dict[str, Any] | None = None) -> dict[str, Any]:
    return _transitions.active_task_contract(feature_dir, doc, task_id, state=state, ns=_NS)


def packet_matches_semantic_contract(packet: dict[str, Any], doc: dict[str, Any],
                                     task: dict[str, Any], feature_dir: pathlib.Path) -> bool:
    """Validate packet integrity and execution identity, ignoring generation provenance."""
    return _transitions.packet_matches_semantic_contract(packet, doc, task, feature_dir, ns=_NS)


def validate_evidence(path: pathlib.Path, *, require_pass: bool = False) -> list[str]:
    return _transitions.validate_evidence(path, require_pass=require_pass, ns=_NS)


def required_completion_criteria(task: dict[str, Any]) -> set[str]:
    return _transitions.required_completion_criteria(task, ns=_NS)


def completion_verification_artifact(feature_dir: pathlib.Path, value: Any) -> str:
    return _transitions.completion_verification_artifact(feature_dir, value, ns=_NS)


def validate_completion_proofs(evidence: dict[str, Any], task: dict[str, Any],
                               required: set[str], feature_dir: pathlib.Path) -> list[str]:
    return _transitions.validate_completion_proofs(evidence, task, required, feature_dir, ns=_NS)


def load_validated(feature_dir: pathlib.Path) -> dict[str, Any]:
    return _feature.load_validated(feature_dir, policy=_policy())


def cmd_validate(args: argparse.Namespace) -> None:
    errors = validate(args.feature_dir)
    if errors:
        for error in errors:
            print(f'FAIL: {error}')
        raise SystemExit(1)
    print('PASS: feature specification and task DAG are structurally valid')


def cmd_validate_all(args: argparse.Namespace) -> None:
    root = args.specs_root
    if not root.exists():
        die(f'specs root does not exist: {root}')

    documented_dirs = sorted(
        p for p in root.iterdir()
        if p.is_dir() and (p / 'spec.md').exists()
    )
    feature_dirs = [p for p in documented_dirs if (p / 'tasks.json').exists()]
    document_only_dirs = [p for p in documented_dirs if not (p / 'tasks.json').exists()]

    for feature_dir in document_only_dirs:
        print(
            f'INFO {feature_dir}: document-only; no tasks.json; '
            'not executable by validate-all; see docs/specs/INVENTORY.md'
        )

    if not feature_dirs:
        die(f'no executable feature specifications found under {root}')

    failures = 0
    for feature_dir in feature_dirs:
        errors = validate(feature_dir)
        if errors:
            failures += 1
            print(f'FAIL {feature_dir}')
            for error in errors:
                print(f'  - {error}')
        else:
            print(f'PASS {feature_dir}')
    if failures:
        raise SystemExit(1)


def cmd_ready(args: argparse.Namespace) -> None:
    return _transitions.cmd_ready(args, ns=_NS)


def cmd_packet(args: argparse.Namespace) -> None:
    doc = load_validated(args.feature_dir)
    idx = task_index(doc)
    if args.task_id not in idx:
        die(f'unknown task {args.task_id}')
    active = resolve_active_packet(args.feature_dir, doc, args.task_id)
    if getattr(args, 'identity', False):
        print(json.dumps({'revision_id': active['revision_id'],
                          'contract_sha256': active['contract_sha256'],
                          'path': active['path']}))
    elif args.stdout:
        print(json.dumps(active['packet'], indent=2, sort_keys=True))
    elif active['packet'] is not None:
        print(active['path'])
    else:
        print(write_packet(doc, idx[args.task_id], args.feature_dir))


def cmd_reviewers(args: argparse.Namespace) -> None:
    doc = load_validated(args.feature_dir)
    idx = task_index(doc)
    if args.task_id not in idx:
        die(f'unknown task {args.task_id}')
    selected = reviewers(active_task_contract(args.feature_dir, doc, args.task_id))
    print('\n'.join(selected) if selected else 'NO_SPECIALIST_REVIEWERS')


def cmd_claim(args: argparse.Namespace) -> None:
    return _transitions.cmd_claim(args, ns=_NS)


def cmd_recover_claim(args: argparse.Namespace) -> None:
    """Human-attest that an already committed exceptional claim never began execution."""
    doc = load_validated(args.feature_dir)
    task = task_index(doc).get(args.task_id)
    if task is None:
        die(f'unknown task {args.task_id}')
    reason = str(args.reason or '').strip()
    operator = str(args.by or '').strip()
    if not bool(getattr(args, 'attest_no_execution_started', False)):
        die('CLAIM_RECOVERY_ATTESTATION_REQUIRED: pass --attest-no-execution-started')
    if not reason or not operator:
        die('CLAIM_RECOVERY_INVALID: --reason and --by must be non-empty')
    if args.attempt != 4:
        die('CLAIM_RECOVERY_INVALID: recovery is limited to the historical attempt-4 partial-claim incident')
    packet_path = args.feature_dir / 'packets' / f'{args.task_id}.json'
    try:
        packet = json.loads(packet_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        die(f'CLAIM_RECOVERY_PACKET_INVALID: existing immutable packet is unavailable: {exc}')
    if not isinstance(packet, dict) or not packet_matches_semantic_contract(packet, doc, task, args.feature_dir):
        die('CLAIM_RECOVERY_PACKET_INVALID: existing packet does not match the current semantic task contract')
    with locked_state(args.feature_dir, doc) as state:
        entry = state['tasks'].get(args.task_id)
        if not isinstance(entry, dict):
            die('CLAIM_RECOVERY_INVALID: lifecycle entry is malformed')
        existing = entry.get('claim_recovery')
        if existing is not None:
            if (not isinstance(existing, dict) or existing.get('recovery_version') != 1 or
                    existing.get('semantic_contract_sha256') != semantic_task_contract_sha256(args.feature_dir, doc, task) or
                    existing.get('packet_identity') != packet.get('packet_sha256')):
                die('CLAIM_RECOVERY_CONFLICT: stored recovery evidence no longer matches current contract/packet')
            assertion = (existing.get('attempt'), existing.get('authorization_id'), existing.get('owner'),
                         existing.get('reason'), existing.get('operator_provenance'), existing.get('attestation'))
            requested = (args.attempt, args.authorization, args.owner, reason, operator, 'no_execution_started')
            if assertion == requested:
                print(f'ALREADY_RECOVERED {args.task_id} attempt={args.attempt}')
                return
            die('CLAIM_RECOVERY_CONFLICT: a different recovery assertion already exists for this attempt')
        if entry.get('status') != 'running' or entry.get('attempts') != args.attempt:
            die('CLAIM_RECOVERY_INVALID: exact current running status and attempt must match')
        if entry.get('owner') != args.owner:
            die('CLAIM_RECOVERY_INVALID: exact current owner does not match')
        if not is_unrecovered_partial_claim(entry):
            die('CLAIM_RECOVERY_INVALID: lifecycle entry is not an identified historical partial claim')
        claimed_at = parse_timestamp(entry.get('claimed_at'))

        def evidence_attempt(field: str, value: Any) -> tuple[str, int | None]:
            """Return (classification, attempt) for legacy and attempt-bound evidence."""
            if not value:
                return 'absent', None
            explicit = entry.get(f'{field}_attempt')
            if explicit is None and isinstance(value, dict):
                explicit = value.get('attempt')
            if isinstance(explicit, int) and not isinstance(explicit, bool):
                return ('current' if explicit == args.attempt else 'previous'), explicit
            # Legacy release markers delimit the prior attempt when they precede this
            # claim and are semantically paired with its failure/checkpoint record.
            if field == 'released_at':
                released_at = parse_timestamp(value)
                if (released_at is not None and claimed_at is not None and released_at < claimed_at and
                        entry.get('last_failure') and entry.get('last_attempt_commit')):
                    return 'previous', int(entry.get('attempts', 0)) - 1
            if field in ('last_attempt_commit', 'last_failure'):
                release_attempt = entry.get('released_at_attempt')
                released_at = parse_timestamp(entry.get('released_at'))
                if (isinstance(release_attempt, int) and release_attempt < args.attempt and
                        released_at is not None and claimed_at is not None and released_at < claimed_at):
                    return 'previous', release_attempt
                # A legacy release/failure bundle is attributable to the preceding
                # attempt only when the release boundary and claim ordering agree.
                if (release_attempt is None and released_at is not None and claimed_at is not None and
                        released_at < claimed_at and entry.get('last_failure') and entry.get('last_attempt_commit') and
                        int(entry.get('attempts', 0)) == args.attempt):
                    return 'previous', int(entry.get('attempts', 0)) - 1
            return 'ambiguous', None

        evidence_fields = (
            'checkpoint_commit', 'last_attempt_commit', 'completion_marker', 'execution_started_at',
            'execution_journal', 'verification_started_at', 'verification_journal', 'started_at',
            'completed_at', 'failed_at', 'released_at', 'lease_recovered_at', 'start_rollback_reason',
            'last_failure_evidence', 'last_failure',
        )
        for field in evidence_fields:
            value = entry.get(field)
            classification, _ = evidence_attempt(field, value)
            if classification in ('current', 'ambiguous'):
                code = 'CURRENT_ATTEMPT' if classification == 'current' else 'AMBIGUOUS'
                die(f'CLAIM_RECOVERY_{code}_EVIDENCE: lifecycle {field}')
        if entry.get('worktree'):
            die('CLAIM_RECOVERY_AMBIGUOUS_EVIDENCE: worktree')
        evidence_root = git_common_dir(args.feature_dir).parent / '.agent-runs' / str(doc.get('feature', args.feature_dir.name))
        if evidence_root.exists():
            for evidence_path in evidence_root.rglob('*.json'):
                try:
                    evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
                except (OSError, json.JSONDecodeError):
                    die(f'CLAIM_RECOVERY_CONFLICT: malformed runtime evidence {evidence_path}')
                if not isinstance(evidence, dict):
                    die(f'CLAIM_RECOVERY_CONFLICT: ambiguous runtime evidence {evidence_path}')
                if evidence.get('task') == args.task_id:
                    runtime_attempt = evidence.get('attempt')
                    if not isinstance(runtime_attempt, int) or isinstance(runtime_attempt, bool):
                        die('CLAIM_RECOVERY_AMBIGUOUS_EVIDENCE: runtime record')
                    if runtime_attempt == args.attempt:
                        die('CLAIM_RECOVERY_CURRENT_ATTEMPT_EVIDENCE: runtime record')
        grants = entry.get('retry_authorizations')
        if not isinstance(grants, list):
            die('CLAIM_RECOVERY_INVALID: authorization ledger is malformed')
        matching = [g for g in grants if isinstance(g, dict) and g.get('id') == args.authorization]
        if len(matching) != 1:
            die('CLAIM_RECOVERY_INVALID: exact authorization ID must occur once in this task ledger')
        grant = matching[0]
        expected_binding = retry_binding(args.feature_dir, doc, args.task_id, 3, state=state)
        binding = grant.get('binding')
        if (grant.get('version') != 2 or not isinstance(binding, dict) or binding != expected_binding or
                binding.get('binding_version') != 2 or binding.get('expected_status') != 'failed' or
                binding.get('expected_attempts') != args.attempt - 1 or
                binding.get('repository') != str(git_common_dir(args.feature_dir)) or
                binding.get('feature') != str(doc.get('feature', args.feature_dir.name)) or
                binding.get('task') != args.task_id or
                binding.get('protocol_version') != protocol_version(args.feature_dir) or
                grant.get('consumed_at') is None or grant.get('consumed_attempt') != args.attempt or
                entry.get('active_retry_authorization') != args.authorization):
            die('CLAIM_RECOVERY_INVALID: V2 grant must bind failed attempt 3 and be consumed into attempt 4')
        if (not isinstance(grant.get('reason'), str) or not grant['reason'].strip() or
                not isinstance(grant.get('provenance'), str) or not grant['provenance']):
            die('CLAIM_RECOVERY_INVALID: authorization provenance is malformed')
        issued_at = parse_timestamp(grant.get('issued_at'))
        consumed_at = parse_timestamp(grant.get('consumed_at'))
        if issued_at is None or consumed_at is None or consumed_at < issued_at:
            die('CLAIM_RECOVERY_INVALID: authorization issue/consumption history is malformed')
        supersessions = entry.get('retry_authorization_supersessions', [])
        if not isinstance(supersessions, list) or any(
                not isinstance(rel, dict) or not isinstance(rel.get('supersedes'), str) or
                not isinstance(rel.get('authorization_id'), str) or
                not isinstance(rel.get('reason'), str) or not rel['reason'].strip() or
                not isinstance(rel.get('provenance'), str) or not rel['provenance'] or
                parse_timestamp(rel.get('issued_at')) is None or rel.get('supersedes') == rel.get('authorization_id')
                for rel in supersessions):
            die('CLAIM_RECOVERY_INVALID: supersession history is malformed or ambiguous')
        superseded_ids = {rel['supersedes'] for rel in supersessions}
        if len(superseded_ids) != len(supersessions):
            die('CLAIM_RECOVERY_INVALID: supersession history is malformed or ambiguous')
        superseding_grants = [g for g in grants if isinstance(g, dict) and g.get('supersedes') == args.authorization]
        grant_supersedes = grant.get('supersedes')
        lineage = []
        if grant_supersedes is not None:
            lineage = [rel for rel in supersessions if rel.get('supersedes') == grant_supersedes and
                       rel.get('authorization_id') == args.authorization]
            old_grants = [g for g in grants if isinstance(g, dict) and g.get('id') == grant_supersedes]
            old = old_grants[0] if len(old_grants) == 1 else {}
            old_binding = old.get('binding') if isinstance(old.get('binding'), dict) else {}
            old_required = {'repository', 'feature', 'task', 'expected_status', 'expected_attempts',
                            'feature_fingerprint', 'packet_sha256', 'protocol_version'}
            same_old_identity = all(old_binding.get(key) == expected_binding.get(key)
                                    for key in ('repository', 'feature', 'task', 'protocol_version'))
            valid_old = (
                old.get('version') == 1 and set(old_binding) == old_required and same_old_identity and
                old_binding.get('expected_status') == 'failed' and old_binding.get('expected_attempts') == 3 and
                isinstance(old_binding.get('feature_fingerprint'), str) and
                re.fullmatch(r'[0-9a-f]{64}', old_binding['feature_fingerprint']) is not None and
                isinstance(old_binding.get('packet_sha256'), str) and
                re.fullmatch(r'[0-9a-f]{64}', old_binding['packet_sha256']) is not None and
                isinstance(old.get('reason'), str) and bool(old['reason'].strip()) and
                isinstance(old.get('provenance'), str) and bool(old['provenance']) and
                parse_timestamp(old.get('issued_at')) is not None and old.get('consumed_at') is None
            )
            if (len(lineage) != 1 or len(old_grants) != 1 or not valid_old or superseding_grants):
                die('CLAIM_RECOVERY_INVALID: authorization supersession history is malformed or ambiguous')
        if (args.authorization in superseded_ids or
                any(rel.get('authorization_id') == args.authorization for rel in supersessions) != bool(lineage)):
            die('CLAIM_RECOVERY_INVALID: authorization is superseded or involved in a supersession')
        allowed_legacy_ids = {grant_supersedes} if lineage else set()
        if any(not isinstance(g, dict) or
               (g.get('version') != 2 and not (g.get('version') == 1 and g.get('id') in allowed_legacy_ids)) or
               (g is not grant and g.get('consumed_at') is not None) for g in grants):
            die('CLAIM_RECOVERY_INVALID: mixed/legacy or conflicting consumed authorization history')
        if any(g.get('consumed_at') is None and g is not grant and g.get('id') not in superseded_ids for g in grants):
            die('CLAIM_RECOVERY_INVALID: another effective unconsumed authorization is ambiguous')
        now = utc_now()
        entry['claim_recovery'] = {
            'task': args.task_id, 'attempt': args.attempt, 'authorization_id': args.authorization,
            'owner': args.owner, 'reason': reason, 'operator_provenance': operator,
            'attestation': 'no_execution_started',
            'semantic_contract_sha256': semantic_task_contract_sha256(args.feature_dir, doc, task),
            'packet_identity': packet['packet_sha256'], 'recovered_at': now.isoformat(),
            'recovery_version': 1,
        }
        refresh_lease(entry, doc, now=now)
    print(f'RECOVERED_CLAIM {args.task_id} attempt={args.attempt} owner={args.owner}')


def owner_guard(entry: dict[str, Any], owner: str) -> None:
    return _transitions.owner_guard(entry, owner, ns=_NS)


def consume_attempt_authorization(entry: dict[str, Any], task_id: str, doc: dict[str, Any], feature_dir: pathlib.Path | None = None, state: dict[str, Any] | None = None) -> str | None:
    """Consume normal retry budget or one explicit human resume grant."""
    return _transitions.consume_attempt_authorization(entry, task_id, doc, feature_dir, state, ns=_NS)


def retry_binding(feature_dir: pathlib.Path, doc: dict[str, Any], task_id: str, attempts: int,
                  *, state: dict[str, Any] | None = None) -> dict[str, Any]:
    return _transitions.retry_binding(feature_dir, doc, task_id, attempts, state=state, ns=_NS)


TASK_CONTRACT_FIELDS = (
    'id', 'title', 'objective', 'role', 'agent_profile', 'depends_on',
    'allowed_paths', 'acceptance_criteria', 'risk_tags', 'verification',
    'test_mode', 'test_seam',
)


def packet_bound_semantic_contract_sha256(feature_dir: pathlib.Path,
                                          packet: dict[str, Any]) -> str:
    """Reconstruct a materialized packet contract without consulting current planning.

    Packet payloads persist ``agent_profile`` as an execution profile, defaulting it
    to ``role`` when the planning task omits the field. That fallback is packet
    metadata, not a task-contract field. For legacy packets without a semantic hash,
    normalize that default away. Newer packets carry a semantic hash, so recompute
    both shape-valid interpretations and require the embedded hash to match one.
    This preserves explicitly supplied profiles while still validating the hash
    from packet-bound material.
    """
    return _transitions.packet_bound_semantic_contract_sha256(feature_dir, packet, ns=_NS)


def semantic_task_contract(feature_dir: pathlib.Path, doc: dict[str, Any], task: dict[str, Any], *,
                           feature_sha256: str | None = None,
                           test_policy: str | None = None) -> dict[str, Any]:
    """Canonical execution contract shared by retry authorization and packet identity."""
    return _transitions.semantic_task_contract(feature_dir, doc, task, feature_sha256=feature_sha256, test_policy=test_policy, ns=_NS)


def semantic_task_contract_sha256(feature_dir: pathlib.Path, doc: dict[str, Any], task: dict[str, Any], *,
                                 feature_sha256: str | None = None,
                                 test_policy: str | None = None) -> str:
    return _transitions.semantic_task_contract_sha256(feature_dir, doc, task, feature_sha256=feature_sha256, test_policy=test_policy, ns=_NS)


def classify_retry_authorizations(feature_dir: pathlib.Path, doc: dict[str, Any], state: dict[str, Any], task_id: str, attempts: int) -> tuple[str, dict[str, Any] | None]:
    return _transitions.classify_retry_authorizations(feature_dir, doc, state, task_id, attempts, ns=_NS)


def matching_retry_authorization(feature_dir: pathlib.Path, doc: dict[str, Any], state: dict[str, Any], task_id: str, attempts: int) -> dict[str, Any] | None:
    classification, grant = classify_retry_authorizations(feature_dir, doc, state, task_id, attempts)
    return grant if classification == 'valid' else None


def retry_authorization_status(feature_dir: pathlib.Path, doc: dict[str, Any], state: dict[str, Any],
                              task_id: str, attempts: int, authorization_id: str) -> str:
    entry = state['tasks'][task_id]
    grant = next((item for item in entry.get('retry_authorizations', [])
                  if isinstance(item, dict) and item.get('id') == authorization_id), None)
    if grant is None:
        return 'absent'
    if any(isinstance(rel, dict) and rel.get('supersedes') == authorization_id
           for rel in entry.get('retry_authorization_supersessions', [])):
        return 'superseded'
    if grant.get('consumed_at') is not None:
        return 'consumed'
    binding = grant.get('binding', {})
    if grant.get('version') == 1 or binding.get('binding_version', 1) == 1:
        expected = retry_binding(feature_dir, doc, task_id, attempts, state=state)
        same_identity = all(binding.get(key) == expected.get(key) for key in ('repository', 'feature', 'task'))
        return ('legacy-unverifiable' if same_identity and binding.get('expected_attempts') == attempts and
                binding.get('protocol_version') == expected['protocol_version'] else 'stale')
    expected = retry_binding(feature_dir, doc, task_id, attempts, state=state)
    return 'valid' if binding == expected else 'stale'


def human_resolution_identity(explicit: str | None) -> str:
    if explicit and explicit.strip():
        return f'recorded:{explicit.strip()}'
    name = subprocess.run(['git', 'config', 'user.name'], capture_output=True, text=True, check=False).stdout.strip()
    email = subprocess.run(['git', 'config', 'user.email'], capture_output=True, text=True, check=False).stdout.strip()
    return f'git-config:{name} <{email}>' if name or email else 'unverified-local-operator'


def cmd_authorize_retry(args: argparse.Namespace) -> None:
    doc = load_validated(args.feature_dir)
    if args.task_id not in task_index(doc):
        die(f'unknown task {args.task_id}')
    reason = args.reason.strip()
    if not reason:
        die('RETRY_AUTHORIZATION_INVALID: --reason must be non-empty')
    with locked_state(args.feature_dir, doc) as state:
        entry = state['tasks'][args.task_id]
        attempts = int(entry.get('attempts', 0))
        maximum = 1 + int(doc.get('max_rework_attempts', 2))
        if entry.get('status') != 'failed' or attempts < maximum:
            die('RETRY_AUTHORIZATION_INVALID: task must be failed with exhausted normal attempts')
        existing = entry.get('retry_authorizations', [])
        if not isinstance(existing, list):
            die('RETRY_AUTHORIZATION_INVALID: authorization ledger is malformed')
        classification, prior = classify_retry_authorizations(args.feature_dir, doc, state, args.task_id, attempts)
        supersedes = getattr(args, 'supersedes', None)
        if supersedes is not None and (not isinstance(supersedes, str) or not supersedes.strip()):
            die('RETRY_AUTHORIZATION_SUPERSESSION_INVALID: --supersedes must be a non-empty authorization ID')
        if supersedes is not None:
            old = next((item for item in existing if isinstance(item, dict) and item.get('id') == supersedes), None)
            if old is None:
                die('RETRY_AUTHORIZATION_SUPERSESSION_INVALID: exact authorization ID was not found for this task')
            if old.get('consumed_at') is not None:
                die('RETRY_AUTHORIZATION_SUPERSESSION_INVALID: consumed authorization cannot be superseded')
            if any(rel.get('supersedes') == supersedes for rel in entry.get('retry_authorization_supersessions', []) if isinstance(rel, dict)):
                die('RETRY_AUTHORIZATION_SUPERSESSION_INVALID: authorization is already superseded')
            old_binding = old.get('binding', {})
            if (old_binding.get('repository') != str(git_common_dir(args.feature_dir)) or
                    old_binding.get('feature') != str(doc.get('feature', args.feature_dir.name)) or
                    old_binding.get('task') != args.task_id):
                die('RETRY_AUTHORIZATION_SUPERSESSION_INVALID: repository, feature, or task identity differs')
            if old_binding.get('expected_status') != 'failed' or old_binding.get('expected_attempts') != attempts:
                die('RETRY_AUTHORIZATION_SUPERSESSION_INVALID: current failed status or attempt count differs')
            if old.get('version') != 1 or old_binding.get('binding_version', 1) != 1 or classification != 'legacy-unverifiable' or not prior or prior.get('id') != supersedes:
                die('RETRY_AUTHORIZATION_SUPERSESSION_INVALID: only the current unverifiable legacy grant may be superseded')
            if classification == 'valid':
                die('RETRY_AUTHORIZATION_SUPERSESSION_INVALID: legacy authorization is not unusable')
        else:
            if classification == 'valid':
                die('RETRY_AUTHORIZATION_INVALID: a valid authorization already exists for this task state')
            if classification == 'legacy-unverifiable':
                die(f'RETRY_AUTHORIZATION_SUPERSESSION_REQUIRED: specify --supersedes {prior["id"]}')
        binding = retry_binding(args.feature_dir, doc, args.task_id, attempts, state=state)
        grant_id = hashlib.sha256(os.urandom(32)).hexdigest()
        grant = {'version': 2, 'id': grant_id, 'binding': binding,
                 'reason': reason, 'provenance': human_resolution_identity(args.by),
                 'issued_at': utc_now().isoformat(), 'consumed_at': None}
        if supersedes:
            grant['supersedes'] = supersedes
            relation = {'supersedes': supersedes, 'authorization_id': grant_id,
                        'reason': reason, 'provenance': grant['provenance'], 'issued_at': grant['issued_at']}
            entry.setdefault('retry_authorization_supersessions', []).append(relation)
        entry.setdefault('retry_authorizations', []).append(grant)
        entry['last_transition'] = {'kind': 'retry_authorization', 'attempts': attempts,
                                    'authorization_id': grant_id, 'at': grant['issued_at']}
    print(f'RETRY_AUTHORIZATION_CREATED {args.task_id} id={grant["id"]}')


def replan_failpoint(_stage: str) -> None:
    """Test seam for durable transaction failure injection."""
    return _transitions.replan_failpoint(_stage, ns=_NS)


def validate_replan_task(feature_dir: pathlib.Path, doc: dict[str, Any], task_id: str,
                         proposed: Any) -> dict[str, Any]:
    return _transitions.validate_replan_task(feature_dir, doc, task_id, proposed, ns=_NS)


def publish_revision(feature_dir: pathlib.Path, task_id: str,
                     packet: dict[str, Any],
                     staged_files: dict[str, tuple[bytes, int]]) -> tuple[str, pathlib.Path]:
    return _transitions.publish_revision(feature_dir, task_id, packet, staged_files, ns=_NS)


def validate_latest_human_resolution(feature_dir: pathlib.Path, doc: dict[str, Any],
                                     task_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Require the current failed state to be the exact audited human-resolution transition."""
    return _transitions.validate_latest_human_resolution(feature_dir, doc, task_id, entry, ns=_NS)


def cmd_materialize_packet_history(args: argparse.Namespace) -> None:
    """Publish a validated canonical legacy root into immutable repository authority."""
    feature_dir = args.feature_dir.resolve()
    doc = load_validated(feature_dir)
    canonical_feature = git_common_dir(feature_dir).parent / 'docs' / 'specs' / feature_dir.name
    if feature_dir != canonical_feature.resolve():
        die('PACKET_HISTORY_MATERIALIZATION_REJECTED: run from the canonical repository feature path')
    with lock_path(feature_dir).open('a+', encoding='utf-8') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            state = _read_state_unlocked_pure(feature_dir, doc)
            entry = state['tasks'].get(args.task_id)
            lineage = entry.get('packet_lineage') if isinstance(entry, dict) else None
            if (not isinstance(lineage, list) or not lineage or not isinstance(lineage[0], dict) or
                    not lineage[0].get('legacy')):
                die('PACKET_HISTORY_MATERIALIZATION_REJECTED: task has no legacy lineage root')
            # Prove the complete active chain before publishing any history.
            resolve_active_packet(feature_dir, doc, args.task_id, state=state)
            relation = lineage[0]
            path = legacy_packet_path(feature_dir, args.task_id)
            if path.is_symlink():
                die('PACKET_HISTORY_MATERIALIZATION_REJECTED: canonical legacy packet is a symlink')
            try:
                raw = path.read_bytes()
                packet = json.loads(raw)
            except (OSError, json.JSONDecodeError) as exc:
                die(f'PACKET_HISTORY_MATERIALIZATION_REJECTED: canonical legacy packet unavailable: {exc}')
            if (not isinstance(packet, dict) or packet.get('feature') != doc.get('feature', feature_dir.name) or
                    packet.get('task') != args.task_id):
                die('PACKET_HISTORY_MATERIALIZATION_REJECTED: canonical legacy packet identity mismatch')
            validate_packet_integrity(packet)
            if (packet_revision_id(packet) != relation.get('revision_id') or
                    packet_bound_semantic_contract_sha256(feature_dir, packet) != relation.get('contract_sha256')):
                die('PACKET_HISTORY_MATERIALIZATION_REJECTED: canonical legacy packet does not prove lineage root')
            revision_files: dict[str, tuple[bytes, int]] = {}
            revision, published = publish_revision(feature_dir, args.task_id, packet, revision_files)
            if revision_files:
                save_state(feature_dir, state, additional_files=revision_files)
            if revision != relation['revision_id']:
                die('PACKET_HISTORY_MATERIALIZATION_REJECTED: published revision differs from lineage root')
            print(f'MATERIALIZED {args.task_id} revision={revision} raw_sha256={sha256_bytes(raw)} path={published}')
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def packet_identity_bridge_path(feature_dir: pathlib.Path, task_id: str, attempt: int) -> pathlib.Path:
    return _transitions.packet_identity_bridge_path(feature_dir, task_id, attempt, ns=_NS)


def bridge_failpoint(_stage: str) -> None:
    """Test seam for immutable bridge publication failure injection."""


def publish_immutable_bridge(feature_dir: pathlib.Path, state: dict[str, Any],
                            path: pathlib.Path, record: dict[str, Any]) -> None:
    """Publish the bridge and unchanged lifecycle state in one repository snapshot."""
    state_dir = runtime_state_dir(feature_dir).resolve()
    try:
        relative = path.resolve(strict=False).relative_to(state_dir).as_posix()
    except ValueError:
        die('PACKET_IDENTITY_ATTESTATION_REJECTED: bridge path is outside repository authority')
    encoded = (json.dumps(record, indent=2, sort_keys=True) + '\n').encode('utf-8')
    if path.exists() or path.is_symlink():
        die('PACKET_IDENTITY_ATTESTATION_CONFLICT: an immutable bridge already occupies this binding')
    # Preserve the pre-publication failure seams. The snapshot transaction makes
    # the bridge visible only when its repository-wide authority is also advanced.
    bridge_failpoint('B3')
    bridge_failpoint('B4')
    save_state(feature_dir, state, additional_files={relative: (encoded, 0o600)})


def _bridge_request_identity(*, repository: str, feature: str, task: str, attempt: int,
                             recovery_id: str, source_scheme: str, source_value: str,
                             target_scheme: str, target_value: str, target_revision: str,
                             semantic_fingerprint: str, protocol_version_value: int,
                             operator: str, reason: str) -> dict[str, Any]:
    return _transitions._bridge_request_identity(repository=repository, feature=feature, task=task, attempt=attempt, recovery_id=recovery_id, source_scheme=source_scheme, source_value=source_value, target_scheme=target_scheme, target_value=target_value, target_revision=target_revision, semantic_fingerprint=semantic_fingerprint, protocol_version_value=protocol_version_value, operator=operator, reason=reason, ns=_NS)


def _read_packet_identity_bridge(feature_dir: pathlib.Path, task_id: str, attempt: int,
                                 expected_request: dict[str, Any] | None = None) -> dict[str, Any] | None:
    return _transitions._read_packet_identity_bridge(feature_dir, task_id, attempt, expected_request, ns=_NS)


def classify_historical_packet_binding(feature_dir: pathlib.Path, doc: dict[str, Any], task_id: str,
                                        state: dict[str, Any], active: dict[str, Any],
                                        attempt: int) -> dict[str, Any]:
    return _transitions.classify_historical_packet_binding(feature_dir, doc, task_id, state, active, attempt, ns=_NS)


def attest_packet_identity(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    feature_dir = args.feature_dir
    doc = load_validated(feature_dir)
    operator = str(args.by or '').strip()
    reason = str(args.reason or '').strip()
    if not operator or not reason:
        die('PACKET_IDENTITY_ATTESTATION_REJECTED: operator and reason are required')
    if args.source_scheme != PACKET_PAYLOAD_IDENTITY_SCHEME or args.target_scheme != PACKET_PAYLOAD_IDENTITY_SCHEME:
        die('PACKET_IDENTITY_ATTESTATION_REJECTED: unsupported packet identity scheme')
    if args.target_revision_scheme != PACKET_REVISION_IDENTITY_SCHEME:
        die('PACKET_IDENTITY_ATTESTATION_REJECTED: unsupported target revision identity scheme')
    for value in (args.source_value, args.target_value, args.expected_semantic_fingerprint):
        if not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
            die('PACKET_IDENTITY_ATTESTATION_REJECTED: malformed SHA-256 identity')
    if re.fullmatch(r'sha256:[0-9a-f]{64}', args.target_revision) is None:
        die('PACKET_IDENTITY_ATTESTATION_REJECTED: malformed target revision identity')
    if args.expected_recovery_identity_scheme != RECOVERY_RECORD_IDENTITY_SCHEME or re.fullmatch(
            r'sha256:[0-9a-f]{64}', args.expected_recovery_identity) is None:
        die('PACKET_IDENTITY_ATTESTATION_REJECTED: malformed recovery record identity')
    if args.expected_protocol_version != protocol_version(feature_dir):
        die('PACKET_IDENTITY_ATTESTATION_REJECTED: protocol version changed')
    bridge_failpoint('B1')
    runtime_state_dir(feature_dir).mkdir(parents=True, exist_ok=True)
    with lifecycle_state_lock(feature_dir) as lock:
        state = _read_state_unlocked_pure(feature_dir, doc)
        entry = state['tasks'].get(args.task_id)
        if not isinstance(entry, dict):
            die('PACKET_IDENTITY_ATTESTATION_REJECTED: unknown task')
        if (str(git_common_dir(feature_dir)) != args.expected_repository or
                doc.get('feature', feature_dir.name) != args.expected_feature or
                args.task_id != args.expected_task or entry.get('status') != args.expected_status or
                entry.get('status') != 'running' or entry.get('attempts') != args.expected_attempts or
                entry.get('owner') != args.expected_owner or args.expected_attempts < 1):
            die('PACKET_IDENTITY_ATTESTATION_REJECTED: lifecycle compare-and-swap failed')
        recovery = entry.get('claim_recovery')
        recovery_identity = recovery_record_identity(recovery)
        if (not isinstance(recovery, dict) or recovery.get('attempt') != args.expected_attempts or
                recovery_identity != args.expected_recovery_identity):
            die('PACKET_IDENTITY_ATTESTATION_REJECTED: recovery record compare-and-swap failed')
        existing_binding = next((item for item in entry.get('attempt_bindings', [])
                                 if isinstance(item, dict) and item.get('attempt') == args.expected_attempts), None)
        if (existing_binding and existing_binding.get('binding_status') == 'proven' and
                (existing_binding.get('packet_revision') != args.target_revision or
                 existing_binding.get('contract_sha256') != args.expected_semantic_fingerprint)):
            die('PACKET_IDENTITY_ATTESTATION_REJECTED: conflicting proven attempt binding exists')
        active = resolve_active_packet(feature_dir, doc, args.task_id, state=state)
        embedded = packet_identity_from_embedded_checksum(active.get('packet'))
        revision = packet_identity_from_revision(active.get('packet'))
        recovered = packet_identity_from_recovery(recovery)
        if (active['revision_id'] != args.target_revision or
                active['contract_sha256'] != args.expected_semantic_fingerprint or
                recovered is None or recovered.scheme != args.source_scheme or
                recovered.value != args.source_value or embedded is None or
                embedded.scheme != args.target_scheme or embedded.value != args.target_value or
                revision is None or revision.value != args.target_revision or
                recovery.get('semantic_contract_sha256') != args.expected_semantic_fingerprint):
            die('PACKET_IDENTITY_ATTESTATION_REJECTED: packet identity compare-and-swap failed')
        request = _bridge_request_identity(
            repository=args.expected_repository, feature=args.expected_feature, task=args.expected_task,
            attempt=args.expected_attempts, recovery_id=args.expected_recovery_identity,
            source_scheme=args.source_scheme, source_value=args.source_value,
            target_scheme=args.target_scheme, target_value=args.target_value,
            target_revision=args.target_revision,
            semantic_fingerprint=args.expected_semantic_fingerprint,
            protocol_version_value=args.expected_protocol_version, operator=operator, reason=reason)
        path = packet_identity_bridge_path(feature_dir, args.task_id, args.expected_attempts)
        prior = _read_packet_identity_bridge(feature_dir, args.task_id, args.expected_attempts)
        if prior:
            if prior.get('request') == request:
                if fcntl is not None:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                return 'ALREADY_ATTESTED', prior
            die('PACKET_IDENTITY_ATTESTATION_CONFLICT: conflicting bridge already exists')
        bridge_failpoint('B2')
        bridge_id = f'sha256:{canonical_json_sha256(request)}'
        record = {'schema_version': BRIDGE_SCHEMA_VERSION,
                  'protocol_version': args.expected_protocol_version,
                  'classification': 'HUMAN_ATTESTED', 'bridge_id': bridge_id,
                  'request': request, 'created_at': utc_now().isoformat()}
        publish_immutable_bridge(feature_dir, state, path, record)
        bridge_failpoint('B5')
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return 'ATTESTED', record


def cmd_attest_packet_identity(args: argparse.Namespace) -> None:
    result, record = attest_packet_identity(args)
    print(f'{result} {args.task_id} bridge={record["bridge_id"]} classification=HUMAN_ATTESTED')


def cmd_packet_identity_binding(args: argparse.Namespace) -> None:
    doc = load_validated(args.feature_dir)
    # Read-only: this command deliberately does not enter locked_state/save_state.
    state = _read_state_unlocked_pure(args.feature_dir, doc)
    active = resolve_active_packet(args.feature_dir, doc, args.task_id, state=state)
    result = classify_historical_packet_binding(args.feature_dir, doc, args.task_id,
                                                state, active, args.attempt)
    print(json.dumps(result, sort_keys=True))


def cmd_replan_task(args: argparse.Namespace) -> None:
    return _transitions.cmd_replan_task(args, ns=_NS)


def restore_attempt_authorization(entry: dict[str, Any]) -> None:
    """Return a consumed human grant when a start is rolled back before provider execution."""
    if entry.pop('active_human_resume', None) is not None:
        entry['human_resume_grants'] = int(entry.get('human_resume_grants', 0)) + 1
    retry_id = entry.pop('active_retry_authorization', None)
    if retry_id is not None:
        for grant in entry.get('retry_authorizations', []):
            if isinstance(grant, dict) and grant.get('id') == retry_id:
                grant['consumed_at'] = None
                grant.pop('consumed_attempt', None)
                break


def changed_paths(worktree: pathlib.Path) -> list[str]:
    return _transitions.changed_paths(worktree, ns=_NS)


def path_matches(path: str, patterns: list[str]) -> bool:
    return _transitions.path_matches(path, patterns, ns=_NS)


def checkpoint_worktree(doc: dict[str, Any], task: dict[str, Any], target: pathlib.Path, label: str = 'checkpoint') -> str:
    return _transitions.checkpoint_worktree(doc, task, target, label, ns=_NS)


def completion_record_for_entry(feature_dir: pathlib.Path, task_id: str, entry: dict[str, Any],
                                active: dict[str, Any]) -> dict[str, Any]:
    bindings = legacy_completion_bindings(feature_dir, task_id)
    if bindings:
        binding = bindings[0]
        if binding.get('attempt') != entry.get('attempts'):
            die('LEGACY_COMPLETION_CONFLICT: binding attempt differs from lifecycle')
        return legacy_binding_as_completion(feature_dir, task_id, binding)
    if (entry.get('status') == 'completed' and
            not _valid_completion_commit(entry.get('checkpoint_commit'))):
        die('LEGACY_COMPLETION_ATTESTATION_REQUIRED: historical checkpoint is not bound')
    if entry.get('status') == 'completed':
        die('LEGACY_COMPLETION_ATTESTATION_REQUIRED: completed legacy state requires immutable binding')
    recorded_id = entry.get('completion_record_id')
    if recorded_id:
        existing = next((item for item in completion_records(feature_dir, task_id)
                         if item.get('record_id') == recorded_id), None)
        if existing:
            return existing
    evidence_ref = entry.get('evidence')
    body = {
        'schema_version': COMPLETION_AUTHORITY_SCHEMA_VERSION, 'record_type': 'completion',
        'repository_id': str(git_common_dir(feature_dir)), 'feature': feature_dir.name,
        'task': task_id, 'attempt': int(entry.get('attempts', 0)),
        'checkpoint': entry.get('checkpoint_commit'),
        'packet_revision': active['revision_id'], 'contract_fingerprint': active['contract_sha256'],
        'evidence_reference_sha256': sha256_bytes(str(evidence_ref or '').encode()),
        'correction_id': None, 'repair_authorization_id': None,
    }
    body['record_id'] = completion_record_id('completion', body)
    body['created_at'] = entry.get('completed_at') or utc_now().isoformat()
    return body


def legacy_attempt_identity(feature_dir: pathlib.Path, task_id: str,
                            entry: dict[str, Any]) -> str:
    binding = next((item for item in entry.get('attempt_bindings', [])
                    if item.get('attempt') == entry.get('attempts')), {})
    return 'sha256:' + canonical_json_sha256({
        'repository_id': str(git_common_dir(feature_dir)), 'feature': feature_dir.name,
        'task': task_id, 'attempt': entry.get('attempts'),
        'claimed_at': entry.get('claimed_at'), 'attempt_binding': binding,
    })


def classify_legacy_completion(feature_dir: pathlib.Path, doc: dict[str, Any],
                               task_id: str) -> tuple[str, dict[str, Any] | None, list[str]]:
    """Read-only classifier; AUTO requires two independent immutable source records."""
    state = _read_state_unlocked_pure(feature_dir, doc)
    entry = state.get('tasks', {}).get(task_id)
    if not isinstance(entry, dict) or entry.get('status') != 'completed':
        return 'CONFLICT', None, ['stored lifecycle status is not completed']
    binding_dir = completion_authority_dir(feature_dir, 'legacy-completion-bindings', task_id)
    if binding_dir.exists() and (binding_dir.is_symlink() or not binding_dir.is_dir()):
        return 'CONFLICT', None, ['legacy binding authority path is unsafe']
    if binding_dir.exists():
        for binding_path in binding_dir.glob('*.json'):
            try:
                candidate = json.loads(binding_path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                return 'CONFLICT', None, ['existing legacy binding is malformed']
            if (isinstance(candidate, dict) and
                    (candidate.get('schema_version') != 1 or
                     candidate.get('binding_version') != LEGACY_COMPLETION_BINDING_VERSION)):
                return 'UNSUPPORTED', None, ['existing legacy binding schema/version is unsupported']
    try:
        existing = read_completion_authority(feature_dir, 'completion-records', task_id,
                                             record_type='completion')
        existing_bindings = legacy_completion_bindings(feature_dir, task_id)
    except SystemExit:
        return 'CONFLICT', None, ['existing completion authority is malformed or contradictory']
    if existing or existing_bindings:
        return 'CONFLICT', None, ['canonical completion or legacy binding already exists']
    active = resolve_active_packet(feature_dir, doc, task_id, state=state)
    evidence_path = pathlib.Path(str(entry.get('evidence', '')))
    if not evidence_path.is_file() or evidence_path.is_symlink():
        return 'HUMAN_ATTESTATION_REQUIRED', None, ['historical evidence artifact is unavailable']
    try:
        evidence_bytes = evidence_path.read_bytes()
        evidence = json.loads(evidence_bytes)
    except (OSError, json.JSONDecodeError):
        return 'CONFLICT', None, ['historical evidence artifact is malformed']
    evidence_hash = sha256_bytes(evidence_bytes)
    expected = {
        'repository': str(git_common_dir(feature_dir)), 'feature': feature_dir.name,
        'task': task_id, 'attempt': entry.get('attempts'),
        'packet_revision': active['revision_id'],
        'contract_fingerprint': active['contract_sha256'],
    }
    if not isinstance(evidence, dict):
        return 'CONFLICT', None, ['historical evidence artifact is not an object']
    if any(key in evidence and evidence[key] != value for key, value in expected.items()):
        return 'CONFLICT', None, ['historical evidence scope conflicts with current immutable task identity']
    missing_scope = [key for key in expected if key not in evidence]
    evidence_checkpoint = evidence.get('checkpoint')
    if not _valid_completion_commit(evidence_checkpoint):
        return 'CONFLICT', None, ['evidence checkpoint is malformed']
    if _valid_completion_commit(entry.get('checkpoint_commit')) and entry['checkpoint_commit'] != evidence_checkpoint:
        return 'CONFLICT', None, ['lifecycle and evidence checkpoints conflict']

    sources_dir = runtime_state_dir(feature_dir) / 'legacy-completion-sources' / feature_dir.name / task_id
    sources = []
    if sources_dir.is_dir() and not sources_dir.is_symlink():
        for source_path in sorted(sources_dir.glob('*.json')):
            try:
                source_bytes = source_path.read_bytes()
                source = json.loads(source_bytes)
            except (OSError, json.JSONDecodeError):
                return 'CONFLICT', None, [f'malformed immutable source record: {source_path.name}']
            if (source_path.is_symlink() or not isinstance(source, dict) or source.get('schema_version') != 1 or
                    source.get('source_id') != legacy_completion_source_id(source) or
                    source_path.stem != source['source_id'].rsplit(':', 1)[-1] or
                    source.get('repository_id') != str(git_common_dir(feature_dir)) or
                    source.get('feature') != feature_dir.name or source.get('task') != task_id or
                    source.get('attempt') != entry.get('attempts') or
                    source.get('checkpoint') != evidence_checkpoint or
                    source.get('evidence_sha256') != evidence_hash or
                    source.get('packet_revision') != active['revision_id'] or
                    source.get('contract_fingerprint') != active['contract_sha256'] or
                    source.get('attempt_identity') != legacy_attempt_identity(feature_dir, task_id, entry) or
                    source.get('historical_status') != 'completed'):
                return 'CONFLICT', None, [f'contradictory immutable source record: {source_path.name}']
            sources.append({'identity': source['source_id'],
                            'sha256': sha256_bytes(source_bytes),
                            'kind': source.get('kind'), 'immutable': True})
    independent_kinds = {s['kind'] for s in sources if isinstance(s['kind'], str)}
    if len(sources) < 2 or len(independent_kinds) < 2:
        reasons = []
        if missing_scope:
            reasons.append('historical evidence omits task-binding fields requiring explicit attestation: ' + ', '.join(missing_scope))
        reasons.append('fewer than two independent immutable historical records corroborate completion checkpoint')
        return 'HUMAN_ATTESTATION_REQUIRED', None, reasons
    binding = {
        'schema_version': 1, 'binding_version': LEGACY_COMPLETION_BINDING_VERSION,
        'binding_type': 'historical-legacy-completion',
        'repository_id': str(git_common_dir(feature_dir)), 'feature': feature_dir.name,
        'task': task_id, 'attempt': entry['attempts'], 'historical_status': 'completed',
        'attempt_identity': legacy_attempt_identity(feature_dir, task_id, entry),
        'checkpoint': evidence_checkpoint, 'packet_revision': active['revision_id'],
        'contract_fingerprint': active['contract_sha256'], 'evidence_sha256': evidence_hash,
        'evidence_checkpoint': evidence_checkpoint, 'source_material': sources,
        'mode': 'AUTO_VERIFIED',
    }
    binding['binding_id'] = legacy_completion_binding_id(binding)
    return 'AUTO_VERIFIED', binding, []


def publish_legacy_binding(feature_dir: pathlib.Path, binding: dict[str, Any]) -> bool:
    if not validate_legacy_binding_shape(binding, feature_dir, str(binding.get('task', ''))):
        die('LEGACY_COMPLETION_INVALID: refusing to publish malformed binding')
    path = completion_authority_dir(feature_dir, 'legacy-completion-bindings', binding['task']) / 'binding.json'
    return publish_completion_authority(path, binding)


def cmd_classify_legacy_completion(args: argparse.Namespace) -> None:
    doc = load_validated(args.feature_dir)
    classification, binding, reasons = classify_legacy_completion(args.feature_dir, doc, args.task_id)
    print(json.dumps({'classification': classification, 'reasons': reasons,
                      'proposed_binding': binding}, indent=2, sort_keys=True))


def cmd_bind_legacy_completion_auto(args: argparse.Namespace) -> None:
    doc = load_validated(args.feature_dir)
    runtime_state_dir(args.feature_dir).mkdir(parents=True, exist_ok=True)
    from verification.store import StoreError
    try:
        with lifecycle_state_lock(args.feature_dir):
            state = _read_state_unlocked_pure(args.feature_dir, doc)
            existing = legacy_completion_bindings(args.feature_dir, args.task_id)
            if existing:
                if existing[0].get('mode') != 'AUTO_VERIFIED':
                    die('LEGACY_COMPLETION_AUTO_REJECTED: a different binding mode is already authoritative')
                print(f"ALREADY_BOUND {args.task_id} binding={existing[0]['binding_id']}")
                return
            classification, binding, reasons = classify_legacy_completion(args.feature_dir, doc, args.task_id)
            if classification != 'AUTO_VERIFIED' or binding is None:
                die('LEGACY_COMPLETION_AUTO_REJECTED: ' + '; '.join(reasons or [classification]))
            binding['created_at'] = utc_now().isoformat()
            created = publish_legacy_binding(args.feature_dir, binding)
    except StoreError as exc:
        fail_on_verification_store_error(exc)
    print(('BOUND' if created else 'ALREADY_BOUND') + f" {args.task_id} binding={binding['binding_id']}")


def cmd_attest_legacy_completion(args: argparse.Namespace) -> None:
    doc = load_validated(args.feature_dir)
    if args.task_id not in task_index(doc):
        die(f'unknown task {args.task_id}')
    operator, reason = str(args.operator or '').strip(), str(args.reason or '').strip()
    if not operator or not reason:
        die('LEGACY_COMPLETION_ATTESTATION_INVALID: explicit operator and reason are required')
    if not args.attested_fields or not args.sources:
        die('LEGACY_COMPLETION_ATTESTATION_INVALID: explicit attested fields and source references are required')
    raw_evidence_path = pathlib.Path(args.evidence)
    if raw_evidence_path.is_symlink() or not raw_evidence_path.is_file():
        die('LEGACY_COMPLETION_ATTESTATION_INVALID: evidence must be a regular file')
    evidence_path = raw_evidence_path.resolve()
    evidence_bytes = evidence_path.read_bytes()
    evidence_hash = sha256_bytes(evidence_bytes)
    try:
        evidence = json.loads(evidence_bytes)
        attested_fields = json.loads(args.attested_fields)
        source_refs = json.loads(args.sources)
    except (json.JSONDecodeError, TypeError) as exc:
        die(f'LEGACY_COMPLETION_ATTESTATION_INVALID: malformed JSON input: {exc}')
    if (not isinstance(attested_fields, dict) or not isinstance(source_refs, list) or not source_refs or
            any(not isinstance(item, dict) or not isinstance(item.get('identity'), str) or
                not item['identity'] or item.get('immutable') is not True or
                not _valid_completion_hash(item.get('sha256'))
                for item in source_refs)):
        die('LEGACY_COMPLETION_ATTESTATION_INVALID: attested fields must be an object and sources a nonempty array')
    runtime_state_dir(args.feature_dir).mkdir(parents=True, exist_ok=True)
    with lifecycle_state_lock(args.feature_dir) as lock:
        state = _read_state_unlocked_pure(args.feature_dir, doc)
        entry = state['tasks'][args.task_id]
        active = resolve_active_packet(args.feature_dir, doc, args.task_id, state=state)
        repo_id = str(git_common_dir(args.feature_dir))
        expected_projection = {
            'stored_status': 'completed', 'attempts': entry.get('attempts'),
            'attempt_identity': legacy_attempt_identity(args.feature_dir, args.task_id, entry),
            'canonical_completion_absent': True,
            'legacy_checkpoint': entry.get('checkpoint_commit'),
            'packet_revision': active['revision_id'],
            'contract_fingerprint': active['contract_sha256'],
            'evidence_sha256': evidence_hash,
            'evidence_checkpoint': evidence.get('checkpoint') if isinstance(evidence, dict) else None,
            'repository_id': repo_id, 'feature': args.feature_dir.name, 'task': args.task_id,
        }
        for key, actual in expected_projection.items():
            # The structured precondition must explicitly contain every CAS value.
            if key not in args.expected or args.expected[key] != actual:
                die(f'LEGACY_COMPLETION_CAS_REJECTED: expected precondition mismatch for {key}')
        if entry.get('status') != 'completed':
            die('LEGACY_COMPLETION_CAS_REJECTED: lifecycle status no longer matches legacy state')
        if (_valid_completion_commit(entry.get('checkpoint_commit')) and isinstance(evidence, dict) and
                entry['checkpoint_commit'] != evidence.get('checkpoint')):
            die('LEGACY_COMPLETION_CAS_REJECTED: lifecycle and evidence checkpoints conflict')
        if read_completion_authority(args.feature_dir, 'completion-records', args.task_id,
                                     record_type='completion'):
            die('LEGACY_COMPLETION_CAS_REJECTED: canonical completion already exists')
        if not isinstance(evidence, dict):
            die('LEGACY_COMPLETION_CAS_REJECTED: evidence is not a JSON object')
        evidence_binding_values = {
            'checkpoint': evidence.get('checkpoint'), 'packet_revision': active['revision_id'],
            'contract_fingerprint': active['contract_sha256'], 'repository': repo_id,
            'feature': args.feature_dir.name, 'task': args.task_id, 'attempt': entry.get('attempts'),
        }
        if any(key in evidence and evidence[key] != value for key, value in evidence_binding_values.items()):
            die('LEGACY_COMPLETION_CAS_REJECTED: evidence scope conflicts with current task binding')
        if not set(evidence_binding_values).issubset(attested_fields) or any(
                attested_fields[key] != value for key, value in evidence_binding_values.items()):
            die('LEGACY_COMPLETION_ATTESTATION_INVALID: explicitly attested fields do not bind evidence, task and current packet')
        binding = {
            'schema_version': 1, 'binding_version': LEGACY_COMPLETION_BINDING_VERSION,
            'binding_type': 'historical-legacy-completion', 'repository_id': repo_id,
            'feature': args.feature_dir.name, 'task': args.task_id, 'attempt': entry['attempts'],
            'historical_status': 'completed',
            'attempt_identity': expected_projection['attempt_identity'],
            'checkpoint': evidence['checkpoint'], 'packet_revision': active['revision_id'],
            'contract_fingerprint': active['contract_sha256'], 'evidence_sha256': evidence_hash,
            'evidence_checkpoint': evidence['checkpoint'], 'source_material': source_refs,
            'mode': 'HUMAN_ATTESTED', 'operator': operator, 'reason': reason,
            'attested_fields': attested_fields,
        }
        binding['binding_id'] = legacy_completion_binding_id(binding)
        binding['created_at'] = utc_now().isoformat()
        created = publish_legacy_binding(args.feature_dir, binding)
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    print(('BOUND' if created else 'ALREADY_BOUND') + f" {args.task_id} binding={binding['binding_id']}")


def add_completion_record(feature_dir: pathlib.Path, record: dict[str, Any]) -> None:
    return _transitions.add_completion_record(feature_dir, record, ns=_NS)


def validate_completion_binding(evidence: dict[str, Any], feature_dir: pathlib.Path, task_id: str,
                                entry: dict[str, Any], active: dict[str, Any]) -> list[str]:
    return _transitions.validate_completion_binding(evidence, feature_dir, task_id, entry, active, ns=_NS)


def require_available_completion_origin(feature_dir: pathlib.Path, state: dict[str, Any],
                                        task_id: str, entry: dict[str, Any]) -> None:
    """Require every accepted v2 origin obligation to have a bound PASS terminal."""
    return _transitions.require_available_completion_origin(feature_dir, state, task_id, entry, ns=_NS)


def cmd_complete(args: argparse.Namespace) -> None:
    return _transitions.cmd_complete(args, ns=_NS)


def cmd_complete_repair(args: argparse.Namespace) -> None:
    """Publish canonical C2 for an already authorized repair on the same attempt."""
    args.feature_dir = pathlib.Path(args.feature_dir)
    doc = load_validated(args.feature_dir)
    if args.task_id not in task_index(doc) or not str(args.owner or '').strip():
        die('COMPLETION_REPAIR_INVALID: task and owner are required')
    evidence_path = pathlib.Path(args.evidence)
    evidence_errors = validate_evidence(evidence_path, require_pass=True)
    if evidence_errors:
        die('COMPLETION_EVIDENCE_INVALID: ' + '; '.join(evidence_errors))
    try:
        evidence_doc = json.loads(evidence_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        die(f'COMPLETION_EVIDENCE_INVALID: {exc}')
    # F1: a new repaired completion needs the attempt's contract binding, as `complete` does; a replay of an already
    # published repair (below) creates no new completion and is resolved from its immutable record.
    observed_contract = _transitions.observed_contract_binding(args.feature_dir, ns=_NS)
    with locked_state(args.feature_dir, doc) as state:
        contract_binding = _transitions.require_unchanged_contract(args.feature_dir, observed_contract, ns=_NS)
        entry = state['tasks'].get(args.task_id)
        if not entry:
            die(f'unknown task {args.task_id}')
        require_available_completion_origin(args.feature_dir, state, args.task_id, entry)
        claim_projection = entry.get('completion_repair_claim')
        # A replay after publication is resolved from immutable history even if
        # the first publisher already cleared the mutable active-claim projection.
        all_repair_records = repaired_completion_records(args.feature_dir, args.task_id)
        existing = None
        if isinstance(claim_projection, dict):
            existing = next((item for item in all_repair_records
                             if item.get('repair_claim_id') == claim_projection.get('claim_id')), None)
        elif entry.get('status') == 'completed' and all_repair_records:
            existing = all_repair_records[-1]
        if existing is not None:
            active_now = resolve_active_packet(args.feature_dir, doc, args.task_id, state=state)
            binding_now = validate_completion_binding(
                evidence_doc, args.feature_dir, args.task_id, entry, active_now)
            proofs_now = validate_completion_proofs(
                evidence_doc, active_task_contract(args.feature_dir, doc, args.task_id, state=state),
                required_completion_criteria(active_task_contract(args.feature_dir, doc, args.task_id, state=state)),
                args.feature_dir)
            replay_hash = sha256_bytes(evidence_path.read_bytes())
            replay_claim_id = existing.get('repair_claim_id')
            replay_claim = next((x for x in repair_claim_records(args.feature_dir, args.task_id)
                                 if x.get('record_id') == replay_claim_id), None)
            replay_authorization = next((x for x in repair_authorization_records(args.feature_dir, args.task_id)
                                         if replay_claim and x.get('record_id') == replay_claim.get('authorization_id')), None)
            replay_correction = next((x for x in correction_records(args.feature_dir, args.task_id)
                                      if replay_claim and x.get('record_id') == replay_claim.get('correction_id')), None)
            replay_historical = next((x for x in completion_records(args.feature_dir, args.task_id)
                                      if replay_correction and x.get('record_id') == replay_correction.get('original_completion_id')), None)
            replay_checkpoint = (effective_repair_checkpoint(args.feature_dir, args.task_id, replay_claim,
                                active_now, replay_historical, replay_correction, replay_authorization)
                                if all((replay_claim, replay_authorization, replay_correction, replay_historical)) else None)
            if (not binding_now and not proofs_now and evidence_doc.get('checkpoint') == replay_checkpoint and
                    existing.get('evidence_reference_sha256') == replay_hash and
                    existing.get('checkpoint') == evidence_doc.get('checkpoint')):
                print(f'ALREADY_COMPLETED_REPAIR {args.task_id} c2={existing["record_id"]}')
                return
            die('COMPLETION_REPAIR_CONFLICT: finalized claim was replayed with different evidence')
        if (entry.get('status') != 'running' or not isinstance(claim_projection, dict) or
                claim_projection.get('owner') != args.owner):
            die('COMPLETION_REPAIR_OWNER_MISMATCH: active repair claim does not match caller')
        owner_guard(entry, args.owner)
        attempt = entry.get('attempts')
        if type(attempt) is not int or attempt < 1:
            die('COMPLETION_REPAIR_ATTEMPT_INVALID: existing attempt is absent')
        _transitions.require_attempt_contract(args.feature_dir, args.task_id, entry, contract_binding,
                                              unbound_without_contract=False, ns=_NS)  # no new completion unbound
        active = resolve_active_packet(args.feature_dir, doc, args.task_id, state=state)
        task = active_task_contract(args.feature_dir, doc, args.task_id, state=state)
        binding_errors = validate_completion_binding(evidence_doc, args.feature_dir, args.task_id, entry, active)
        claim_id = claim_projection.get('claim_id')
        claims = repair_claim_records(args.feature_dir, args.task_id)
        claim = next((item for item in claims if item.get('record_id') == claim_id), None)
        authorization_id = claim_projection.get('authorization_id')
        authorization = next((item for item in repair_authorization_records(args.feature_dir, args.task_id)
                              if item.get('record_id') == authorization_id), None)
        correction_id = claim_projection.get('correction_id')
        correction = next((item for item in correction_records(args.feature_dir, args.task_id)
                           if item.get('record_id') == correction_id), None)
        historical_id = correction.get('original_completion_id') if correction else None
        historical = next((item for item in completion_records(args.feature_dir, args.task_id)
                           if item.get('record_id') == historical_id), None)
        if not all((claim, authorization, correction, historical)):
            die('COMPLETION_REPAIR_AUTHORITY_INVALID: C1/K1/A1/claim chain is incomplete')
        if (claim.get('owner') != args.owner or claim.get('attempt') != attempt or
                claim.get('correction_id') != correction_id or claim.get('authorization_id') != authorization_id or
                authorization.get('correction_id') != correction_id or
                authorization.get('original_completion_id') != historical_id or
                authorization.get('attempt') != attempt or correction.get('completed_attempt') != attempt or
                correction.get('original_completion_id') != historical_id or
                historical.get('attempt') != attempt or
                authorization.get('packet_revision') != active['revision_id'] or
                correction.get('packet_revision') != active['revision_id'] or
                authorization.get('contract_fingerprint') != active['contract_sha256'] or
                correction.get('contract_fingerprint') != active['contract_sha256']):
            die('COMPLETION_REPAIR_AUTHORITY_INVALID: exact repair authority mismatch')
        effective_checkpoint = effective_repair_checkpoint(
            args.feature_dir, args.task_id, claim, active, historical, correction, authorization)
        if evidence_doc.get('checkpoint') != effective_checkpoint:
            binding_errors.append('REPAIR_CHECKPOINT_MISMATCH: evidence checkpoint differs from immutable repair checkpoint authority')
        if binding_errors:
            die('COMPLETION_EVIDENCE_BINDING_INVALID: ' + '; '.join(binding_errors))
        require_task_commit_ancestry(args.feature_dir, task, state, effective_checkpoint,
                                     operation='COMPLETION_REPAIR')
        proof_errors = validate_completion_proofs(
            evidence_doc, task, required_completion_criteria(task), args.feature_dir)
        if proof_errors:
            die('COMPLETION_EVIDENCE_INVALID: ' + '; '.join(proof_errors))
        if (effective_task_status(args.feature_dir, args.task_id, entry) != 'correction_required' and
                not all_repair_records):
            die('COMPLETION_REPAIR_STATE_INVALID: task is not awaiting repair completion')
        prior = [item for item in all_repair_records
                 if item.get('repair_claim_id') == claim_id]
        evidence_hash = sha256_bytes(evidence_path.read_bytes())
        record = {
            'schema_version': COMPLETION_AUTHORITY_SCHEMA_VERSION,
            'record_type': 'completion-repair', 'protocol_version': 1,
            'repository_id': str(git_common_dir(args.feature_dir)), 'feature': args.feature_dir.name,
            'task': args.task_id, 'attempt': attempt,
            'historical_completion_id': historical_id, 'correction_id': correction_id,
            'repair_authorization_id': authorization_id, 'repair_claim_id': claim_id,
            'owner': args.owner, 'checkpoint': evidence_doc['checkpoint'],
            'evidence_reference_sha256': evidence_hash,
            'packet_revision': active['revision_id'],
            'contract_fingerprint': active['contract_sha256'],
        }
        record['record_id'] = completion_record_id('completion-repair', record)
        record['created_at'] = utc_now().isoformat()
        if prior and all(item.get('record_id') != record['record_id'] for item in prior):
            die('COMPLETION_REPAIR_CONFLICT: repair claim already finalized with different evidence')
        if prior:
            if prior[0].get('record_id') == record['record_id']:
                print(f'ALREADY_COMPLETED_REPAIR {args.task_id} c2={prior[0]["record_id"]}')
                return
            die('COMPLETION_REPAIR_CONFLICT: repair claim already finalized with different evidence')
        path = completion_authority_dir(args.feature_dir, 'completion-repair-records', args.task_id) / \
            f"{record['record_id'].rsplit(':', 1)[-1]}.json"
        claim_slot = path.parent / f"claim-{claim_id.rsplit(':', 1)[-1]}.json"
        slot_record = {key: value for key, value in record.items()
                       if key not in {'record_id', 'created_at'}}
        slot_record['record_id'] = record['record_id']
        slot_record['created_at'] = record['created_at']
        _completion_failpoint('repair-completion-before-publication')
        created = publish_completion_authority(claim_slot, slot_record)
        if not created:
            winner = json.loads(claim_slot.read_text(encoding='utf-8'))
            if winner.get('record_id') != record['record_id']:
                die('COMPLETION_REPAIR_CONFLICT: repair claim finalized by a different C2')
        _completion_failpoint('repair-completion-published')
        entry.update({
            'status': 'completed', 'completed_at': utc_now().isoformat(),
            'checkpoint_commit': record['checkpoint'], 'completion_repair_record_id': record['record_id'],
        })
        entry.setdefault('completion_repair_history', []).append({
            'repair_claim_id': claim_id, 'completion_id': record['record_id'],
            'attempt': attempt, 'completed_at': entry['completed_at'],
        })
        entry.pop('completion_repair_claim', None)
        for key in ('owner', 'heartbeat_at', 'lease_expires_at'):
            entry.pop(key, None)
    print(('COMPLETED_REPAIR' if created else 'ALREADY_COMPLETED_REPAIR') +
          f' {args.task_id} c2={record["record_id"]}')


def cmd_bind_repair_checkpoint(args: argparse.Namespace) -> None:
    """Human-attest an immutable checkpoint for an existing legacy repair claim."""
    args.feature_dir = pathlib.Path(args.feature_dir)
    doc = load_validated(args.feature_dir)
    operator, reason, owner = (str(args.by or '').strip(), str(args.reason or '').strip(),
                               str(args.owner or '').strip())
    if not operator:
        die('REPAIR_CHECKPOINT_BINDING_INVALID: explicit operator is required')
    if not reason:
        die('REPAIR_CHECKPOINT_BINDING_INVALID: explicit reason is required')
    if not owner or not _valid_completion_commit(args.checkpoint):
        die('REPAIR_CHECKPOINT_BINDING_INVALID: owner and full checkpoint commit are required')
    with locked_state(args.feature_dir, doc) as state:
        entry = state['tasks'].get(args.task_id)
        if not entry or entry.get('status') != 'running' or entry.get('attempts') != args.attempt:
            die('REPAIR_CHECKPOINT_BINDING_INVALID: current task attempt/status differs')
        projection = entry.get('completion_repair_claim')
        if not isinstance(projection, dict) or projection.get('claim_id') != args.claim_id or projection.get('owner') != owner:
            die('REPAIR_CHECKPOINT_BINDING_INVALID: active repair claim/owner differs')
        active = resolve_active_packet(args.feature_dir, doc, args.task_id, state=state)
        claim = next((x for x in repair_claim_records(args.feature_dir, args.task_id)
                      if x.get('record_id') == args.claim_id), None)
        authorization = next((x for x in repair_authorization_records(args.feature_dir, args.task_id)
                              if x.get('record_id') == args.authorization_id), None)
        correction = next((x for x in correction_records(args.feature_dir, args.task_id)
                           if x.get('record_id') == args.correction_id), None)
        historical_id = correction.get('original_completion_id') if correction else None
        historical = next((x for x in completion_records(args.feature_dir, args.task_id)
                           if x.get('record_id') == historical_id), None)
        if not all((claim, authorization, correction, historical)):
            die('REPAIR_CHECKPOINT_BINDING_INVALID: C1/K1/A1/claim chain is incomplete')
        if (claim.get('owner') != owner or claim.get('attempt') != args.attempt or
                historical.get('record_id') != args.historical_completion_id or
                claim.get('correction_id') != correction['record_id'] or
                claim.get('authorization_id') != authorization['record_id'] or
                authorization.get('record_id') != args.authorization_id or
                authorization.get('correction_id') != correction['record_id'] or
                authorization.get('original_completion_id') != historical['record_id'] or
                authorization.get('attempt') != args.attempt or
                correction.get('record_id') != args.correction_id or correction.get('completed_attempt') != args.attempt or
                correction.get('packet_revision') != active['revision_id'] or
                authorization.get('packet_revision') != active['revision_id'] or
                correction.get('contract_fingerprint') != active['contract_sha256'] or
                authorization.get('contract_fingerprint') != active['contract_sha256']):
            die('REPAIR_CHECKPOINT_BINDING_INVALID: exact current authority mismatch')
        if claim.get('checkpoint') is not None:
            die('REPAIR_CHECKPOINT_BINDING_INVALID: claim already has a canonical checkpoint')
        if any(x.get('repair_claim_id') == args.claim_id for x in repaired_completion_records(args.feature_dir, args.task_id)):
            die('REPAIR_CHECKPOINT_BINDING_INVALID: repair is already finalized')
        existing = [x for x in repair_checkpoint_bindings(args.feature_dir, args.task_id)
                    if x.get('repair_claim_id') == args.claim_id]
        if existing:
            if (existing[0].get('checkpoint') == args.checkpoint and existing[0].get('operator') == operator and
                    existing[0].get('reason') == reason):
                print(f'ALREADY_BOUND {args.task_id} binding={existing[0]["record_id"]}')
                return
            die('REPAIR_CHECKPOINT_BINDING_CONFLICT: repair claim already has a different binding')
        record = {
            'schema_version': 1, 'record_type': REPAIR_CHECKPOINT_BINDING_SCHEME,
            'repository_id': str(git_common_dir(args.feature_dir)), 'feature': args.feature_dir.name,
            'task': args.task_id, 'attempt': args.attempt, 'historical_completion_id': historical['record_id'],
            'correction_id': correction['record_id'], 'repair_authorization_id': authorization['record_id'],
            'repair_claim_id': claim['record_id'], 'owner': owner, 'checkpoint': args.checkpoint,
            'packet_revision': active['revision_id'], 'contract_fingerprint': active['contract_sha256'],
            'protocol_version': protocol_version(args.feature_dir), 'operator': operator, 'reason': reason,
        }
        record['record_id'] = repair_checkpoint_binding_id(record)
        record['created_at'] = utc_now().isoformat()
        path = completion_authority_dir(args.feature_dir, 'completion-repair-checkpoint-bindings', args.task_id) / \
            f"{record['record_id'].rsplit(':', 1)[-1]}.json"
        _completion_failpoint('repair-checkpoint-binding-before-publication')
        created = publish_completion_authority(path, record)
        _completion_failpoint('repair-checkpoint-binding-published')
    print(('BOUND' if created else 'ALREADY_BOUND') + f' {args.task_id} binding={record["record_id"]}')


def cmd_correct_completion(args: argparse.Namespace) -> None:
    doc = load_validated(args.feature_dir)
    if args.task_id not in task_index(doc):
        die(f'unknown task {args.task_id}')
    operator = str(args.by or '').strip()
    reason = str(args.reason or '').strip()
    if not operator or not reason or args.reason_code != COMPLETION_CORRECTION_REASON:
        die('COMPLETION_CORRECTION_INVALID: explicit operator, reason and supported reason code are required')
    evidence_path = pathlib.Path(args.evidence)
    if not evidence_path.is_file() or evidence_path.is_symlink():
        die('COMPLETION_CORRECTION_INVALID: defect evidence must be an existing regular file')
    evidence_hash = sha256_bytes(evidence_path.read_bytes())
    with locked_state(args.feature_dir, doc) as state:
        entry = state['tasks'][args.task_id]
        active = resolve_active_packet(args.feature_dir, doc, args.task_id, state=state)
        current = correction_records(args.feature_dir, args.task_id)
        if entry.get('status') not in {'completed', 'correction_required'}:
            die('COMPLETION_CORRECTION_INVALID: task has no completed lifecycle event to correct')
        completions = completion_records(args.feature_dir, args.task_id)
        current_completion = next((item for item in completions
                                   if item.get('record_id') == entry.get('completion_record_id')), None)
        existing_for_current = next((item for item in current
                                     if current_completion and
                                     item.get('original_completion_id') == current_completion['record_id']), None)
        original = current_completion
        if existing_for_current:
            original = next((item for item in completions
                             if item.get('record_id') == existing_for_current.get('original_completion_id')), None)
        if original is None:
            original = completion_record_for_entry(args.feature_dir, args.task_id, entry, active)
        original['record_id'] = completion_record_id('completion', original)
        existing_for_original = [item for item in current
                                 if item.get('original_completion_id') == original['record_id']]
        correction = {
            'schema_version': COMPLETION_AUTHORITY_SCHEMA_VERSION,
            'record_type': 'completion-correction', 'repository_id': str(git_common_dir(args.feature_dir)),
            'feature': args.feature_dir.name, 'task': args.task_id,
            'completed_attempt': int(entry.get('attempts', 0)),
            'original_completion_id': original['record_id'],
            'original_completion_checkpoint': original.get('checkpoint'),
            'packet_revision': active['revision_id'], 'contract_fingerprint': active['contract_sha256'],
            'reason_code': args.reason_code, 'reason': reason, 'defect_evidence_sha256': evidence_hash,
            'operator': operator,
        }
        correction['record_id'] = completion_record_id('completion-correction', correction)
        correction['created_at'] = utc_now().isoformat()
        if existing_for_original and all(item['record_id'] != correction['record_id'] for item in existing_for_original):
            die('COMPLETION_CORRECTION_CONFLICT: original completion already has a different correction')
        add_completion_record(args.feature_dir, original)
        path = completion_authority_dir(args.feature_dir, 'completion-corrections', args.task_id) / \
            f"{correction['record_id'].rsplit(':', 1)[-1]}.json"
        created = publish_completion_authority(path, correction)
        _completion_failpoint('correction-published')
        if not correction_is_repaired(args.feature_dir, args.task_id, correction):
            entry.update({'status': 'correction_required', 'active_completion_correction_id': correction['record_id']})
    print(('CORRECTED' if created else 'ALREADY_CORRECTED') +
          f" {args.task_id} correction={correction['record_id']}")


def cmd_authorize_completion_repair(args: argparse.Namespace) -> None:
    doc = load_validated(args.feature_dir)
    if args.task_id not in task_index(doc):
        die(f'unknown task {args.task_id}')
    operator = str(args.by or '').strip()
    reason = str(args.reason or '').strip()
    if not operator or not reason:
        die('COMPLETION_REPAIR_AUTHORIZATION_INVALID: explicit operator and reason are required')
    with locked_state(args.feature_dir, doc) as state:
        entry = state['tasks'][args.task_id]
        correction = next((x for x in correction_records(args.feature_dir, args.task_id)
                           if x.get('record_id') == args.correction_id), None)
        active = resolve_active_packet(args.feature_dir, doc, args.task_id, state=state)
        if not correction:
            die('COMPLETION_REPAIR_AUTHORIZATION_INVALID: correction is absent')
        existing = [x for x in repair_authorization_records(args.feature_dir, args.task_id)
                    if x.get('correction_id') == correction['record_id']]
        authorization = {
            'schema_version': COMPLETION_AUTHORITY_SCHEMA_VERSION,
            'record_type': 'completion-repair-authorization',
            'repository_id': str(git_common_dir(args.feature_dir)), 'feature': args.feature_dir.name,
            'task': args.task_id, 'attempt': int(entry.get('attempts', 0)),
            'correction_id': correction['record_id'],
            'original_completion_id': correction['original_completion_id'],
            'packet_revision': active['revision_id'], 'contract_fingerprint': active['contract_sha256'],
            'reason': reason, 'operator': operator,
        }
        authorization['record_id'] = completion_record_id('completion-repair-authorization', authorization)
        authorization['created_at'] = utc_now().isoformat()
        if any(item['record_id'] == authorization['record_id'] for item in existing):
            print(f"ALREADY_AUTHORIZED {args.task_id} authorization={authorization['record_id']}")
            return
        if (correction_is_repaired(args.feature_dir, args.task_id, correction) or
                correction['completed_attempt'] != entry.get('attempts') or
                correction['packet_revision'] != active['revision_id'] or
                correction['contract_fingerprint'] != active['contract_sha256']):
            die('COMPLETION_REPAIR_AUTHORIZATION_INVALID: correction is repaired or stale')
        if existing and all(item['record_id'] != authorization['record_id'] for item in existing):
            die('COMPLETION_REPAIR_AUTHORIZATION_CONFLICT: correction already has different repair authority')
        path = completion_authority_dir(args.feature_dir, 'completion-repair-authorizations', args.task_id) / \
            f"{authorization['record_id'].rsplit(':', 1)[-1]}.json"
        created = publish_completion_authority(path, authorization)
        _completion_failpoint('repair-authorization-published')
        entry['active_completion_repair_authorization'] = authorization['record_id']
    print(('AUTHORIZED' if created else 'ALREADY_AUTHORIZED') +
          f" {args.task_id} authorization={authorization['record_id']}")


def cmd_claim_completion_repair(args: argparse.Namespace) -> None:
    doc = load_validated(args.feature_dir)
    if args.task_id not in task_index(doc) or not str(args.owner or '').strip():
        die('COMPLETION_REPAIR_CLAIM_INVALID: task and owner are required')
    with locked_state(args.feature_dir, doc) as state:
        entry = state['tasks'][args.task_id]
        authorization = next((x for x in repair_authorization_records(args.feature_dir, args.task_id)
                              if x.get('record_id') == args.authorization), None)
        correction = (next((x for x in correction_records(args.feature_dir, args.task_id)
                            if authorization and x.get('record_id') == authorization.get('correction_id')), None))
        active = resolve_active_packet(args.feature_dir, doc, args.task_id, state=state)
        if (not authorization or not correction or correction_is_repaired(args.feature_dir, args.task_id, correction) or
                authorization.get('task') != args.task_id or authorization.get('attempt') != entry.get('attempts') or
                authorization.get('packet_revision') != active['revision_id'] or
                authorization.get('contract_fingerprint') != active['contract_sha256']):
            die('COMPLETION_REPAIR_AUTHORIZATION_INVALID: exact current authority is absent')
        if entry.get('status') == 'running':
            existing = entry.get('completion_repair_claim')
            if isinstance(existing, dict) and existing.get('authorization_id') == args.authorization and existing.get('owner') == args.owner:
                print(f"ALREADY_CLAIMED {args.task_id} attempt={entry['attempts']}")
                return
            die('COMPLETION_REPAIR_ALREADY_OWNED: another repair continuation is active')
        if effective_task_status(args.feature_dir, args.task_id, entry) != 'correction_required':
            die('COMPLETION_REPAIR_CLAIM_INVALID: task is not awaiting a completion repair')
        prior_claims = read_completion_authority(args.feature_dir, 'completion-repair-claims', args.task_id,
                                                 record_type='completion-repair-claim')
        claim = next((item for item in prior_claims
                      if item.get('correction_id') == correction['record_id'] and
                      item.get('authorization_id') == authorization['record_id']), None)
        if claim and claim.get('owner') != args.owner:
            die('COMPLETION_REPAIR_ALREADY_OWNED: durable repair claim belongs to another owner')
        if claim is None:
            generation = 1 + max((int(item.get('generation', 0)) for item in prior_claims
                                  if item.get('correction_id') == correction['record_id']), default=0)
            claim = {
                'schema_version': COMPLETION_AUTHORITY_SCHEMA_VERSION,
                'record_type': 'completion-repair-claim', 'repository_id': str(git_common_dir(args.feature_dir)),
                'feature': args.feature_dir.name, 'task': args.task_id, 'attempt': int(entry.get('attempts', 0)),
                'correction_id': correction['record_id'], 'authorization_id': authorization['record_id'],
                'owner': args.owner, 'generation': generation,
            }
            claim['record_id'] = completion_record_id('completion-repair-claim', claim)
            claim['created_at'] = utc_now().isoformat()
            claim_path = completion_authority_dir(args.feature_dir, 'completion-repair-claims', args.task_id) / \
                f"{claim['record_id'].rsplit(':', 1)[-1]}.json"
            publish_completion_authority(claim_path, claim)
            _completion_failpoint('repair-claim-published')
        entry.update({
            'status': 'running', 'owner': args.owner, 'completion_repair_claim': {
                'claim_id': claim['record_id'], 'correction_id': correction['record_id'],
                'authorization_id': authorization['record_id'], 'owner': args.owner,
                'generation': claim['generation'],
            },
        })
        refresh_lease(entry, doc)
    print(f"CLAIMED_REPAIR {args.task_id} owner={args.owner} attempt={entry['attempts']}")

def cmd_fail(args: argparse.Namespace) -> None:
    return _transitions.cmd_fail(args, ns=_NS)

def cmd_release(args: argparse.Namespace) -> None:
    return _transitions.cmd_release(args, ns=_NS)

def descendants(idx: dict[str, dict[str, Any]], target: str) -> set[str]:
    return _transitions.descendants(idx, target, ns=_NS)


def prune_task_workspace(feature: str, task_id: str, *, expected_branch_commit: str | None = None,
                         guard_branch: bool = False) -> None:
    return _transitions.prune_task_workspace(feature, task_id, expected_branch_commit=expected_branch_commit, guard_branch=guard_branch, ns=_NS)


def ensure_task_workspace_clean(feature: str, task_id: str) -> None:
    return _transitions.ensure_task_workspace_clean(feature, task_id, ns=_NS)


def task_workspace_snapshot(feature: str, task_id: str) -> dict[str, str | bool | None]:
    target = worktree_path(feature, task_id)
    branch = f'agent/{feature}/{task_id}'
    branch_result = subprocess.run(
        ['git', 'rev-parse', '--verify', f'refs/heads/{branch}'],
        capture_output=True, text=True, check=False,
    )
    branch_commit = branch_result.stdout.strip() if branch_result.returncode == 0 else None
    worktree_commit = None
    if target.exists():
        worktree_commit = subprocess.run(
            ['git', 'rev-parse', 'HEAD'], cwd=target, capture_output=True, text=True, check=True,
        ).stdout.strip()
    return {'branch_commit': branch_commit, 'worktree_commit': worktree_commit}


def restore_task_workspace(feature: str, task_id: str, snapshot: dict[str, str | bool | None]) -> None:
    target = worktree_path(feature, task_id)
    branch = f'agent/{feature}/{task_id}'
    branch_commit = snapshot.get('branch_commit')
    worktree_commit = snapshot.get('worktree_commit')
    branch_ref = subprocess.run(
        ['git', 'rev-parse', '--verify', f'refs/heads/{branch}'],
        capture_output=True, text=True, check=False,
    )
    current_branch_commit = branch_ref.stdout.strip() if branch_ref.returncode == 0 else None
    if branch_commit and current_branch_commit is None:
        subprocess.run(['git', 'branch', branch, str(branch_commit)], check=True)
    if worktree_commit and not target.exists():
        if branch_commit == worktree_commit and current_branch_commit in {None, branch_commit}:
            subprocess.run(['git', 'worktree', 'add', str(target), branch], check=True)
        else:
            # Preserve the exact completed worktree snapshot if another process recreated or
            # advanced its branch while rollback was in progress.
            subprocess.run(['git', 'worktree', 'add', '--detach', str(target), str(worktree_commit)], check=True)


def human_resolution_identity(explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    for key in ('user.email', 'user.name'):
        proc = subprocess.run(['git', 'config', '--get', key], capture_output=True, text=True, check=False)
        value = proc.stdout.strip()
        if value:
            return value
    return 'human'


def audit_reference(value: Any, feature_dir: pathlib.Path) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    path = pathlib.Path(raw)
    if not path.is_absolute():
        return raw
    root = feature_repo_base(feature_dir)
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return f'external:{path.name}'


def write_human_resolution(
    feature_dir: pathlib.Path, doc: dict[str, Any], task_id: str, *, decision: str, decided_by: str,
    prior_entry: dict[str, Any], source: pathlib.Path | None = None,
) -> pathlib.Path:
    stamp = utc_now().strftime('%Y%m%dT%H%M%S.%fZ')
    directory = feature_dir / 'evidence' / 'human-resolutions' / task_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{stamp}.json'
    payload: dict[str, Any] = {
        'schema_version': 1,
        'feature': str(doc.get('feature', feature_dir.name)),
        'task': task_id,
        'decision': decision,
        'decided_by': decided_by,
        'resolved_at': utc_now().isoformat(),
        'action': 'retry',
        'prior_status': str(prior_entry.get('status')),
        'prior_reason': prior_entry.get('last_failure'),
        'prior_evidence': audit_reference(prior_entry.get('last_failure_evidence'), feature_dir),
        'attempts_before_resolution': int(prior_entry.get('attempts', 0)),
    }
    if source is not None:
        payload['source'] = audit_reference(source, feature_dir)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + '.', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return path


def cmd_human_resolve(args: argparse.Namespace) -> None:
    doc = load_validated(args.feature_dir)
    idx = task_index(doc)
    if args.task_id not in idx:
        die(f'unknown task {args.task_id}')
    decision = (args.decision or '').strip()
    source: pathlib.Path | None = None
    if args.decision_file:
        source = pathlib.Path(args.decision_file).resolve()
        if not source.exists() or not source.is_file():
            die(f'human decision file does not exist: {source}')
        decision = source.read_text(encoding='utf-8').strip()
    if not decision:
        die('human resolution requires non-empty --decision or --decision-file')
    decided_by = human_resolution_identity(args.by)

    preliminary = load_state(args.feature_dir, doc)
    prior = preliminary['tasks'].get(args.task_id)
    if not prior or prior.get('status') != 'escalated':
        die(f'{args.task_id} must be escalated before a human can resolve it')
    if prior.get('control_outcome') in VERIFICATION_BLOCKAGE_OUTCOMES:
        die('HUMAN_RESOLUTION_NOT_ALLOWED: verification blockage requires its scoped verification resume path')

    artifact = write_human_resolution(
        args.feature_dir, doc, args.task_id, decision=decision, decided_by=decided_by,
        prior_entry=dict(prior), source=source,
    )
    with locked_state(args.feature_dir, doc) as state:
        entry = state['tasks'][args.task_id]
        if entry.get('status') != 'escalated':
            artifact.unlink(missing_ok=True)
            die(f'{args.task_id} changed state while applying human resolution; retry deliberately')
        history = entry.setdefault('human_resolution_history', [])
        if not isinstance(history, list):
            history = []
            entry['human_resolution_history'] = history
        resolved_at = json.loads(artifact.read_text(encoding='utf-8'))['resolved_at']
        history.append({
            'artifact': str(artifact),
            'decided_by': decided_by,
            'resolved_at': resolved_at,
            'prior_reason': entry.get('last_failure'),
            'attempts': int(entry.get('attempts', 0)),
        })
        entry.update({
            'status': 'failed',
            'human_resolution': str(artifact),
            'human_resolved_at': resolved_at,
            'human_resolved_by': decided_by,
            'human_resume_grants': int(entry.get('human_resume_grants', 0)) + 1,
            'rework_evidence': str(artifact),
        })
        entry['last_transition'] = {
            'kind': 'human_resolution', 'artifact': str(artifact),
            'artifact_sha256': sha256_bytes(artifact.read_bytes()),
            'attempts': int(entry.get('attempts', 0)), 'resolved_at': resolved_at,
        }
        for key in ('owner', 'heartbeat_at', 'lease_expires_at'):
            entry.pop(key, None)
    print(f'HUMAN_RESOLVED {args.task_id} -> retry authorized; artifact={artifact}')


def reopen_dependency_graph(feature_dir: pathlib.Path, doc: dict[str, Any],
                            state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build reopen dependencies without resolving unrelated legacy packets."""
    return _transitions.reopen_dependency_graph(feature_dir, doc, state, ns=_NS)


def cmd_reopen(args: argparse.Namespace) -> None:
    return _transitions.cmd_reopen(args, ns=_NS)


def cmd_status(args: argparse.Namespace) -> None:
    doc = load_validated(args.feature_dir)
    with lock_path(args.feature_dir).open('a+', encoding='utf-8') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            recover_prepared_state_locked(args.feature_dir)
            _cmd_status_locked(args, doc)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _cmd_status_locked(args: argparse.Namespace, doc: dict[str, Any]) -> None:
    if state_recovery_required(args.feature_dir):
        incident = state_recovery_incident(args.feature_dir)
        result = {'feature': doc.get('feature', args.feature_dir.name),
                  'lifecycle_status': 'recovery-required',
                  'state_path': str(state_path(args.feature_dir)),
                  'feature_fingerprint': feature_fingerprint(args.feature_dir),
                  'incident_id': incident.get('incident_id') if incident else None,
                  'snapshot_status': state_snapshot_status(args.feature_dir),
                  'snapshot_commit': state_snapshots.current_repository_snapshot_commit(
                      feature_repo_base(args.feature_dir))}
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"RECOVERY_REQUIRED {result['feature']}: authoritative state is absent; no task status is inferred")
        return
    state = _read_state_unlocked_pure(args.feature_dir, doc)
    bridge_records = []
    for task_id in state.get('tasks', {}):
        task_root = runtime_state_dir(args.feature_dir) / 'packet-identity-bridges' / args.feature_dir.name / task_id
        if task_root.exists() and task_root.is_dir() and not task_root.is_symlink():
            for bridge_path in sorted(task_root.glob('*.json')):
                try:
                    bridge = json.loads(bridge_path.read_text(encoding='utf-8'))
                except (OSError, json.JSONDecodeError):
                    die(f'PACKET_IDENTITY_ATTESTATION_CONFLICT: unreadable bridge {bridge_path.name}')
                if not isinstance(bridge, dict) or bridge.get('classification') != 'HUMAN_ATTESTED':
                    die(f'PACKET_IDENTITY_ATTESTATION_CONFLICT: malformed bridge {bridge_path.name}')
                bridge_records.append(bridge)
    if args.json:
        state['packet_identity_bridges'] = bridge_records
        for tid, entry in state['tasks'].items():
            entry['historical_status'] = entry.get('status')
            entry['effective_status'] = effective_task_status(args.feature_dir, tid, entry)
            entry['status'] = entry['effective_status']
        print(json.dumps(state, indent=2, sort_keys=True))
        return
    now = utc_now()
    for tid, entry in state['tasks'].items():
        task = active_task_contract(args.feature_dir, doc, tid, state=state)
        owner = entry.get('owner', '-')
        lease = '-'
        if entry.get('status') == 'running':
            expires = parse_timestamp(entry.get('lease_expires_at'))
            if expires is None:
                lease = 'stale'
            else:
                remaining = max(0, int((expires - now).total_seconds()))
                lease = f'{remaining}s'
        status = effective_task_status(args.feature_dir, tid, entry)
        print(
            f'{tid:9} {status:18} {task["role"]:11} '
            f'attempts={entry.get("attempts", 0)} owner={owner} lease={lease}  {task["title"]}'
        )
    for bridge in bridge_records:
        request = bridge['request']
        print(f"PACKET_IDENTITY_BRIDGE {bridge['bridge_id']} classification=HUMAN_ATTESTED "
              f"task={request['task']} attempt={request['attempt']} "
              f"source={request['source_identity']['scheme']}:{request['source_identity']['value']} "
              f"target={request['target_identity']['scheme']}:{request['target_identity']['value']} "
              f"revision={request['target_revision_identity']['value']} operator={request['operator']} "
              f"semantic={request['semantic_contract_sha256']} "
              f"recovery={request['recovery_record_identity']['scheme']}:{request['recovery_record_identity']['value']} "
              f"reason={request['reason']} created_at={bridge['created_at']}")


def cmd_reset(args: argparse.Namespace) -> None:
    doc = load_json(args.feature_dir / 'tasks.json') if (args.feature_dir / 'tasks.json').exists() else {'feature': args.feature_dir.name, 'tasks': []}
    if args.full or args.prune_worktrees:
        feature = str(doc.get('feature', args.feature_dir.name))
        for task in doc.get('tasks', []):
            if isinstance(task, dict) and isinstance(task.get('id'), str):
                prune_task_workspace(feature, task['id'])
    if args.full or args.remove_packets:
        shutil.rmtree(args.feature_dir / 'packets', ignore_errors=True)
    remove_state_locked(args.feature_dir)
    suffix = ' + worktrees/packets' if args.full else ''
    print(f'RESET runtime state{suffix} for {args.feature_dir}')


def worktree_path(feature: str, task_id: str) -> pathlib.Path:
    return _transitions.worktree_path(feature, task_id, ns=_NS)


def bootstrap_path_allowed(path: str, feature_rel: pathlib.Path) -> bool:
    normalized = path.replace('\\', '/')
    feature_prefix = str(feature_rel).replace('\\', '/').rstrip('/') + '/'
    wayfinder_prefix = f'docs/wayfinder/{feature_rel.name}/'
    return (
        normalized in {'AGENTS.md', 'CLAUDE.md', '.gitignore', '.github/workflows/agentic-sdd.yml'}
        or normalized.startswith('.claude/agents/')
        or normalized.startswith('tooling/agent-harness/')
        or normalized.startswith('docs/agentic-sdd/')
        or normalized.startswith('docs/specs/SDD-001/')
        or normalized.startswith(wayfinder_prefix)
        or normalized.startswith(feature_prefix)
    )


def execution_protocol_paths(feature_dir: pathlib.Path) -> list[str]:
    return _transitions.execution_protocol_paths(feature_dir, ns=_NS)


def legacy_execution_snapshot_paths(feature_dir: pathlib.Path) -> list[str]:
    """Keep the pre-task synthetic snapshot scope for design and wayfinder callers."""
    root = repo_root()
    try:
        feature_rel = feature_dir.resolve().relative_to(root)
    except ValueError:
        die('feature_dir must be inside the repository')
    candidates = [
        '.gitignore', 'AGENTS.md', 'CLAUDE.md', '.claude/agents',
        'docs/agentic-sdd', '.github/workflows/agentic-sdd.yml', str(feature_rel),
    ]
    return [candidate for candidate in candidates if (root / candidate).exists()]


def _path_is_selected(path: str, selected: list[str]) -> bool:
    return _transitions._path_is_selected(path, selected, ns=_NS)


def current_execution_snapshot(feature_dir: pathlib.Path, doc: dict[str, Any]) -> tuple[str, bool]:
    """Return an immutable snapshot for non-task orchestration callers."""
    root = repo_root()
    head = subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = changed_paths(root)
    try:
        feature_rel = feature_dir.resolve().relative_to(root)
    except ValueError:
        die('feature_dir must be inside the repository')
    unrelated = [path for path in dirty if not bootstrap_path_allowed(path, feature_rel)]
    if unrelated:
        die('primary checkout has uncommitted changes outside the SDD protocol/active feature; '
            f'create a deliberate local checkpoint or stash them before orchestration: {unrelated}')
    selected = legacy_execution_snapshot_paths(feature_dir)
    if not dirty:
        return head, False

    # Preserve the legacy synthetic snapshot for design/wayfinder/verification-contract
    # callers. Task worktrees use task_worktree_snapshot below and never consume this.
    fd, index_name = tempfile.mkstemp(prefix='agent-sdd-index-')
    os.close(fd)
    os.unlink(index_name)
    env = os.environ.copy()
    env['GIT_INDEX_FILE'] = index_name
    try:
        subprocess.run(['git', 'read-tree', head], cwd=root, env=env, check=True, capture_output=True)
        tracked = subprocess.run(['git', 'ls-files'], cwd=root, capture_output=True,
                                 text=True, check=True).stdout.splitlines()
        selected_existing = [path for path in selected
                             if (root / path).exists()
                             or any(_path_is_selected(tracked_path, [path]) for tracked_path in tracked)]
        subprocess.run(['git', 'add', '-A', '--', *selected_existing], cwd=root, env=env, check=True,
                       capture_output=True)
        tree = subprocess.run(['git', 'write-tree'], cwd=root, env=env, capture_output=True,
                              text=True, check=True).stdout.strip()
        commit = subprocess.run(
            ['git', '-c', 'user.name=Agent Harness', '-c',
             'user.email=agent-harness@local.invalid', 'commit-tree', tree, '-p', head,
             '-m', f'agent orchestration base {doc.get("feature", feature_dir.name)}'],
            cwd=root, env=env, capture_output=True, text=True, check=True,
        ).stdout.strip()
    finally:
        if os.path.exists(index_name):
            os.unlink(index_name)
    return commit, True


def task_worktree_snapshot(feature_dir: pathlib.Path) -> str:
    """Return committed HEAD only, rejecting dirty paths that define task freshness."""
    return _transitions.task_worktree_snapshot(feature_dir, ns=_NS)


def stage_first_task_base(feature_dir: pathlib.Path, state: dict[str, Any],
                          durable_state: dict[str, Any], task: dict[str, Any],
                          target: pathlib.Path) -> None:
    """Stage base authority and persist a recovery breadcrumb before worktree creation."""
    return _transitions.stage_first_task_base(feature_dir, state, durable_state, task, target, ns=_NS)


def execution_base(feature_dir: pathlib.Path, doc: dict[str, Any], state: dict[str, Any]) -> str:
    existing = state.get('base_commit')
    if isinstance(existing, str) and existing:
        check = subprocess.run(['git', 'cat-file', '-e', f'{existing}^{{commit}}'], capture_output=True)
        if check.returncode != 0:
            die(f'local orchestration base commit no longer exists: {existing}; reset the feature state')
        return existing

    commit, synthetic = current_execution_snapshot(feature_dir, doc)
    state['base_commit'] = commit
    state['base_kind'] = 'synthetic-local' if synthetic else 'head'
    return commit


def assert_worktree_protocol_current(feature_dir: pathlib.Path, target: pathlib.Path) -> None:
    return _transitions.assert_worktree_protocol_current(feature_dir, target, ns=_NS)


def task_base_commits(task: dict[str, Any], state: dict[str, Any]) -> list[tuple[str, str]]:
    """Return the immutable commits a task branch must contain."""
    return _transitions.task_base_commits(task, state, ns=_NS)


def require_task_commit_ancestry(feature_dir: pathlib.Path, task: dict[str, Any],
                                 state: dict[str, Any], candidate: str, *,
                                 operation: str) -> None:
    """Fail closed unless a task checkpoint contains its accepted base commits."""
    return _transitions.require_task_commit_ancestry(feature_dir, task, state, candidate, operation=operation, ns=_NS)


def prepare_task_worktree(feature_dir: pathlib.Path, doc: dict[str, Any], state: dict[str, Any],
                          task: dict[str, Any], *,
                          durable_state: dict[str, Any] | None = None) -> pathlib.Path:
    return _transitions.prepare_task_worktree(feature_dir, doc, state, task, durable_state=durable_state, ns=_NS)


def cmd_worktree_create(args: argparse.Namespace) -> None:
    doc = load_validated(args.feature_dir)
    idx = task_index(doc)
    task = idx.get(args.task_id)
    if not task:
        die(f'unknown task {args.task_id}')
    with locked_state(args.feature_dir, doc) as state:
        if args.task_id not in ready_ids(doc, state, args.feature_dir):
            die(f'{args.task_id} is not ready')
        task = active_task_contract(args.feature_dir, doc, args.task_id, state=state)
        staged_state = copy.deepcopy(state)
        target = prepare_task_worktree(args.feature_dir, doc, staged_state, task, durable_state=state)
        for key in ('base_commit', 'base_kind'):
            if key in staged_state:
                state[key] = staged_state[key]
        state.pop('pending_base_commit', None)
        state.pop('pending_base_kind', None)
    print(target)


def cmd_start(args: argparse.Namespace) -> None:
    return _transitions.cmd_start(args, ns=_NS)

def rollback_unexecuted_start(feature_dir: pathlib.Path, doc: dict[str, Any], task_id: str, owner: str, reason: str) -> None:
    """Undo a start that failed before a provider process was launched.

    This does not consume rework budget and only succeeds for a clean task worktree.
    """
    return _transitions.rollback_unexecuted_start(feature_dir, doc, task_id, owner, reason, ns=_NS)


def cmd_worktree_remove(args: argparse.Namespace) -> None:
    doc = load_validated(args.feature_dir)
    feature = str(doc.get('feature', args.feature_dir.name))
    target = worktree_path(feature, args.task_id)
    branch = f'agent/{feature}/{args.task_id}'
    if not target.exists():
        die(f'worktree path does not exist: {target}')
    from verification.store import StoreError
    try:
        with lifecycle_state_lock(args.feature_dir):
            if not target.exists():
                die(f'worktree path does not exist: {target}')
            subprocess.run(['git', 'worktree', 'remove', str(target)], check=True)
            if subprocess.run(['git', 'show-ref', '--verify', '--quiet', f'refs/heads/{branch}']).returncode == 0:
                subprocess.run(['git', 'branch', '-D', branch], check=True)
    except StoreError as exc:
        fail_on_verification_store_error(exc)
    print(f'REMOVED {target} and local branch {branch}')



def cmd_heartbeat(args: argparse.Namespace) -> None:
    return _transitions.cmd_heartbeat(args, ns=_NS)


def cmd_recover_stale(args: argparse.Namespace) -> None:
    doc = load_validated(args.feature_dir)
    recovered = recover_stale_leases(args.feature_dir, doc, reason=args.reason)
    if recovered:
        print('RECOVERED ' + ','.join(recovered))
    else:
        print('NO_STALE_LEASES')



def cmd_doctor(_: argparse.Namespace) -> None:
    root = repo_root()
    checks = {
        'git': shutil.which('git'),
        'python3': shutil.which('python3'),
        'python>=3.10': sys.version_info >= (3, 10),
        'gradle-wrapper': (root / 'gradlew') if (root / 'gradlew').exists() else None,
        'codex(optional)': shutil.which('codex'),
        'claude(optional)': shutil.which('claude'),
        'verification-sandbox-module': (root / 'tooling' / 'agent-harness' / 'verification_sandbox.py') if (root / 'tooling' / 'agent-harness' / 'verification_sandbox.py').exists() else None,
        'telemetry-module': (root / 'tooling' / 'agent-harness' / 'telemetry.py') if (root / 'tooling' / 'agent-harness' / 'telemetry.py').exists() else None,
        'control-plane-module': (root / 'tooling' / 'agent-harness' / 'control_plane.py') if (root / 'tooling' / 'agent-harness' / 'control_plane.py').exists() else None,
        'design-module': (root / 'tooling' / 'agent-harness' / 'design.py') if (root / 'tooling' / 'agent-harness' / 'design.py').exists() else None,
        'design-result-schema': (root / 'tooling' / 'agent-harness' / 'schemas' / 'design-result.schema.json') if (root / 'tooling' / 'agent-harness' / 'schemas' / 'design-result.schema.json').exists() else None,
        'wayfinder-module': (root / 'tooling' / 'agent-harness' / 'wayfinder.py') if (root / 'tooling' / 'agent-harness' / 'wayfinder.py').exists() else None,
        'wayfinder-result-schema': (root / 'tooling' / 'agent-harness' / 'schemas' / 'wayfinder-result.schema.json') if (root / 'tooling' / 'agent-harness' / 'schemas' / 'wayfinder-result.schema.json').exists() else None,
        'wayfinder-handoff-schema': (root / 'tooling' / 'agent-harness' / 'schemas' / 'wayfinder-handoff.schema.json') if (root / 'tooling' / 'agent-harness' / 'schemas' / 'wayfinder-handoff.schema.json').exists() else None,
        'wayfinder-tasks-schema': (root / 'tooling' / 'agent-harness' / 'schemas' / 'wayfinder-tasks-result.schema.json') if (root / 'tooling' / 'agent-harness' / 'schemas' / 'wayfinder-tasks-result.schema.json').exists() else None,
        'verification-contract-module': (root / 'tooling' / 'agent-harness' / 'verification_contract.py') if (root / 'tooling' / 'agent-harness' / 'verification_contract.py').exists() else None,
        'verification-contract-schema': (root / 'tooling' / 'agent-harness' / 'schemas' / 'verification-contract.schema.json') if (root / 'tooling' / 'agent-harness' / 'schemas' / 'verification-contract.schema.json').exists() else None,
        'trust-module': (root / 'tooling' / 'agent-harness' / 'trust.py') if (root / 'tooling' / 'agent-harness' / 'trust.py').exists() else None,
        'eval-module': (root / 'tooling' / 'agent-harness' / 'eval.py') if (root / 'tooling' / 'agent-harness' / 'eval.py').exists() else None,
    }
    system = sys.platform
    sandbox_backend = (
        shutil.which('codex')
        or (shutil.which('bwrap') if system.startswith('linux') else None)
        or (shutil.which('sandbox-exec') if system == 'darwin' else None)
    )
    checks['strong-verification-sandbox(optional)'] = sandbox_backend
    failed_required = False
    for profile in sorted({p for profiles in RISK_TO_REVIEWERS.values() for p in profiles} | {'grill-reviewer', 'prototype-agent', 'prototype-evaluator', 'wayfinder-agent', 'wayfinder-synthesizer', 'verification-author'}):
        path = root / 'docs' / 'agentic-sdd' / 'agents' / f'{profile}.md'
        checks[f'profile:{profile}'] = path if path.exists() else None

    for name, value in checks.items():
        ok = bool(value)
        if (name in {'git', 'python3', 'python>=3.10', 'gradle-wrapper', 'verification-sandbox-module', 'telemetry-module', 'control-plane-module', 'design-module', 'design-result-schema', 'wayfinder-module', 'wayfinder-result-schema', 'wayfinder-handoff-schema', 'wayfinder-tasks-schema', 'verification-contract-module', 'verification-contract-schema', 'trust-module', 'eval-module'} or name.startswith('profile:')) and not ok:
            failed_required = True
        display = str(value) if isinstance(value, (str, pathlib.Path)) else None
        print(f'{"PASS" if ok else "MISS"}: {name}' + (f' -> {display}' if display else ''))
    if failed_required:
        raise SystemExit(1)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Repo-native agentic SDD task orchestrator')
    sub = p.add_subparsers(required=True)

    s = sub.add_parser('validate')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser('validate-all')
    s.add_argument('specs_root', nargs='?', type=pathlib.Path, default=pathlib.Path('docs/specs'))
    s.set_defaults(func=cmd_validate_all)

    for name in ('ready', 'status'):
        s = sub.add_parser(name)
        s.add_argument('feature_dir', type=pathlib.Path)
        s.add_argument('--json', action='store_true')
        s.set_defaults(func=globals()[f'cmd_{name}'])

    s = sub.add_parser('recover-state', help='Create an explicitly authorized empty lifecycle epoch after accepted state loss')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('--epoch-id', required=True)
    s.add_argument('--operator', required=True, help='Audited operator provenance label; not authenticated by the harness')
    s.add_argument('--expected-feature-fingerprint', required=True,
                   help='CAS value shown by the recovery-required status report')
    s.set_defaults(func=cmd_recover_state)

    s = sub.add_parser('restore-state', help='Restore only the exact, verified current Git state snapshot')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('--expected-snapshot-commit', required=True,
                   help='Full commit ID shown by read-only snapshot inspection')
    s.add_argument('--operator', required=True, help='Operator provenance label; not authenticated by the harness')
    s.set_defaults(func=cmd_restore_state)

    s = sub.add_parser('discard-prepared-snapshot',
                       help='Audited owner action to discard an unacknowledged snapshot when state is absent')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('--expected-prepared-commit', required=True,
                   help='Full prepared commit ID confirmed to be unacknowledged')
    s.add_argument('--operator', required=True, help='Operator provenance label; not authenticated by the harness')
    s.set_defaults(func=cmd_discard_prepared_snapshot)
    s = sub.add_parser('recover-verification',
                       help='Reconcile verification ownership and release proven-dead prestart reservations')
    s.add_argument('--repo', type=pathlib.Path, default=pathlib.Path.cwd(),
                   help='Repository checkout; recovery runs independently of feature lifecycle locks')
    s.set_defaults(func=cmd_recover_verification)

    s = sub.add_parser('packet')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--stdout', action='store_true')
    s.add_argument('--identity', action='store_true', help='Print active packet revision and semantic contract fingerprint')
    s.set_defaults(func=cmd_packet)

    s = sub.add_parser('materialize-packet-history')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.set_defaults(func=cmd_materialize_packet_history)

    s = sub.add_parser('reviewers')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.set_defaults(func=cmd_reviewers)

    s = sub.add_parser('start')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--owner', required=True)
    s.set_defaults(func=cmd_start)

    s = sub.add_parser('claim')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--owner', required=True)
    s.set_defaults(func=cmd_claim)

    s = sub.add_parser('recover-claim')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--attempt', type=int, required=True)
    s.add_argument('--authorization', required=True)
    s.add_argument('--owner', required=True)
    s.add_argument('--reason', required=True)
    s.add_argument('--by', required=True, help='Operator provenance label; not authenticated by the harness')
    s.add_argument('--attest-no-execution-started', action='store_true', required=True,
                   help='Explicit factual human attestation; the harness cannot infer this condition')
    s.set_defaults(func=cmd_recover_claim)

    s = sub.add_parser('attest-packet-identity')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    for flag in ('expected-repository', 'expected-feature', 'expected-task', 'expected-status',
                 'expected-owner', 'expected-recovery-identity-scheme', 'expected-recovery-identity',
                 'source-scheme', 'source-value', 'target-scheme', 'target-value',
                 'target-revision-scheme', 'target-revision', 'expected-semantic-fingerprint'):
        s.add_argument(f'--{flag}', required=True)
    s.add_argument('--expected-attempts', type=int, required=True)
    s.add_argument('--expected-protocol-version', type=int, required=True)
    s.add_argument('--by', required=True, help='Human operator provenance label; not authenticated by the harness')
    s.add_argument('--reason', required=True)
    s.set_defaults(func=cmd_attest_packet_identity)

    s = sub.add_parser('packet-identity-binding')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--attempt', type=int, required=True)
    s.set_defaults(func=cmd_packet_identity_binding)

    s = sub.add_parser('complete')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--owner', required=True)
    s.add_argument('--evidence', required=True)
    s.set_defaults(func=cmd_complete)

    s = sub.add_parser('complete-repair', help='Publish canonical repaired completion C2 for an authorized repair')
    s.add_argument('feature_dir')
    s.add_argument('task_id')
    s.add_argument('--owner', required=True, help='Must match the active immutable repair claim owner')
    s.add_argument('--evidence', required=True, help='Passing completion evidence bound to the active packet and attempt')
    s.set_defaults(func=cmd_complete_repair)

    s = sub.add_parser('bind-repair-checkpoint',
                       help='Human-attest an immutable checkpoint for a legacy in-flight repair claim')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--attempt', type=int, required=True)
    s.add_argument('--historical-completion-id', required=True)
    s.add_argument('--correction-id', required=True)
    s.add_argument('--authorization-id', required=True)
    s.add_argument('--claim-id', required=True)
    s.add_argument('--owner', required=True)
    s.add_argument('--checkpoint', required=True)
    s.add_argument('--by', required=True, help='Human operator provenance label')
    s.add_argument('--reason', required=True)
    s.set_defaults(func=cmd_bind_repair_checkpoint)

    s = sub.add_parser('correct-completion')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--reason-code', required=True, choices=[COMPLETION_CORRECTION_REASON])
    s.add_argument('--reason', required=True)
    s.add_argument('--evidence', required=True, help='Existing defect evidence file; its content hash is recorded')
    s.add_argument('--by', required=True, help='Operator provenance label')
    s.set_defaults(func=cmd_correct_completion)

    s = sub.add_parser('classify-legacy-completion')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.set_defaults(func=cmd_classify_legacy_completion)

    s = sub.add_parser('bind-legacy-completion-auto')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.set_defaults(func=cmd_bind_legacy_completion_auto)

    s = sub.add_parser('attest-legacy-completion')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--evidence', required=True)
    s.add_argument('--attested-fields', required=True,
                   help='JSON object explicitly attesting checkpoint, repository/task/attempt and packet binding')
    s.add_argument('--sources', required=True,
                   help='JSON array of immutable historical source references with identity and SHA-256')
    s.add_argument('--operator', required=True)
    s.add_argument('--reason', required=True)
    def parse_json_argument(value: str) -> dict[str, Any]:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc
        if not isinstance(parsed, dict):
            raise argparse.ArgumentTypeError('expected a JSON object')
        return parsed
    s.set_defaults(func=cmd_attest_legacy_completion)
    # Keep explicit CAS JSON structured at the CLI boundary.
    s.add_argument('--expected-cas', dest='expected', type=parse_json_argument, required=True)

    s = sub.add_parser('authorize-completion-repair')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--correction-id', required=True)
    s.add_argument('--reason', required=True)
    s.add_argument('--by', required=True, help='Operator provenance label')
    s.set_defaults(func=cmd_authorize_completion_repair)

    s = sub.add_parser('claim-completion-repair')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--authorization', required=True)
    s.add_argument('--owner', required=True)
    s.set_defaults(func=cmd_claim_completion_repair)

    s = sub.add_parser('fail')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--owner', required=True)
    s.add_argument('--reason', required=True)
    s.add_argument('--evidence', help='Optional structured runner evidence for the failed attempt')
    s.add_argument('--escalate', action='store_true', help='Stop immediately for a human decision instead of retrying')
    s.set_defaults(func=cmd_fail)

    s = sub.add_parser('release')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--owner', required=True)
    s.add_argument('--reason', required=True)
    s.set_defaults(func=cmd_release)

    s = sub.add_parser('resume-verification')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--reason', required=True,
                   help='Recovery evidence or operator note that the blocked precondition is repaired')
    s.add_argument('--by', required=True, help='Operator attribution for the auditable verification resume')
    s.set_defaults(func=cmd_resume_verification)

    s = sub.add_parser('human-resolve')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    resolution = s.add_mutually_exclusive_group(required=True)
    resolution.add_argument('--decision', help='Accepted human decision that resolves the escalation within the current spec/plan')
    resolution.add_argument('--decision-file', help='Path to a text/markdown file containing the accepted human decision')
    s.add_argument('--by', help='Human identity recorded in the audit artifact; defaults to git user.email/user.name')
    s.set_defaults(func=cmd_human_resolve)

    s = sub.add_parser('authorize-retry')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--reason', required=True, help='Explicit human decision authorizing one exceptional attempt')
    s.add_argument('--by', help='Operator provenance label; not authenticated by the harness')
    s.add_argument('--supersedes', help='Exact unusable legacy authorization ID to supersede explicitly')
    s.set_defaults(func=cmd_authorize_retry)

    s = sub.add_parser('replan-task')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--expected-status', required=True)
    s.add_argument('--expected-attempts', type=int, required=True)
    s.add_argument('--expected-active-revision', required=True)
    s.add_argument('--expected-contract-sha256', required=True)
    s.add_argument('--expected-feature-generation', type=int,
                   help='Required for failed-task replans; current accepted feature generation')
    s.add_argument('--proposed-task-file', required=True, help='Validated task-model JSON stored inside the feature folder')
    s.add_argument('--reason', required=True)
    s.add_argument('--by', required=True, help='Human/operator provenance label')
    s.add_argument('--checkpoint', help='Optional implementation checkpoint provenance')
    s.set_defaults(func=cmd_replan_task)

    s = sub.add_parser('reopen')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--reason', required=True)
    s.add_argument('--evidence', help='Evaluator evidence that explains why rework is required')
    s.set_defaults(func=cmd_reopen)

    s = sub.add_parser('heartbeat')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('task_id')
    s.add_argument('--owner', required=True)
    s.set_defaults(func=cmd_heartbeat)

    s = sub.add_parser('recover-stale')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('--reason', default='lease expired; recovered for orchestration resume')
    s.set_defaults(func=cmd_recover_stale)

    s = sub.add_parser('reset')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('--prune-worktrees', action='store_true', help='Remove clean task worktrees and local agent branches')
    s.add_argument('--remove-packets', action='store_true', help='Remove generated immutable packets after deliberate re-planning')
    s.add_argument('--full', action='store_true', help='Reset state, clean task worktrees/branches, and remove generated packets')
    s.set_defaults(func=cmd_reset)

    s = sub.add_parser('migrate-state')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.set_defaults(func=cmd_migrate_state)

    s = sub.add_parser('reconcile-feature')
    s.add_argument('feature_dir', type=pathlib.Path)
    s.add_argument('--expected-generation', type=int, required=True)
    s.add_argument('--reason', required=True)
    s.add_argument('--by', required=True, help='audit attribution only')
    s.set_defaults(func=cmd_reconcile_feature)

    for name in ('worktree-create', 'worktree-remove'):
        s = sub.add_parser(name)
        s.add_argument('feature_dir', type=pathlib.Path)
        s.add_argument('task_id')
        s.set_defaults(func=globals()[f'cmd_{name.replace("-", "_")}'])

    s = sub.add_parser('doctor')
    s.set_defaults(func=cmd_doctor)
    return p


# AH5-14b-2: the library's transitions read this module's names through `_NS`; checked once, here, before any
# operation (a missing name or a non-callable recovery binding refuses the import).
_NS = _transitions.bind(_transitions.ModuleNamespace(globals()))


if __name__ == '__main__':
    ns = parser().parse_args()
    ns.func(ns)
