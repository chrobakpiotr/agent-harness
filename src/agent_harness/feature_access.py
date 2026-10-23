"""Lifecycle state access: the lock, loading and the completion-record readers it needs (AH5-14b-1).

Verbatim from Showcase `tooling/agent-harness/harness.py` at `3649784`. Everything the consumer decides or that its
tests replace comes in through one `StateAccess` binding with every field required; the library keeps its own
integrity checks (prepared-snapshot recovery, the snapshot status, the state identity and version checks).

The consumer's recovery check is two calls, in the source's order: ``recovery_incident`` (may itself refuse) before
the snapshot status, and ``recovery_epoch_required`` only when an incident exists. The latter **can only refuse**:
anything but an exact ``False`` keeps recovery required, and an error propagates, so it blocks too. Neither may
write state or run recovery; recovery operations are AH5-14d. Lifecycle transitions are AH5-14b-2.
"""
from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import json
import pathlib
import re
import sys
import threading
from collections.abc import Callable, Iterator
from typing import Any

from . import feature_state as _fs
from . import state_snapshots
from .feature import (
    TASK_ID_RE,
    FeaturePolicy,
    die,
    load_json,
    protocol_version,
    sha256_bytes,
)

COMPLETION_AUTHORITY_SCHEMA_VERSION = 1
LEGACY_COMPLETION_BINDING_VERSION = 1
COMPLETION_CORRECTION_REASON = 'COMPLETION_SEMANTICALLY_INVALID'
# The pending authority files of the current lifecycle transaction; consumer code that publishes completion
# authority writes into the same object.
_authority_transaction = threading.local()


@dataclasses.dataclass(frozen=True)
class StateAccess:
    """The consumer's bindings for state access; every field is required (``docs/specs/AH5-14/packet.md``)."""

    policy: FeaturePolicy
    common_dir: Callable[[pathlib.Path], pathlib.Path]
    locking: Any
    fingerprint: Callable[[pathlib.Path], str]
    protocol_fingerprint: Callable[[pathlib.Path], str]
    save_state: Callable[..., None]
    load_validated: Callable[[pathlib.Path], dict[str, Any]]
    read_state_pure: Callable[[pathlib.Path, dict[str, Any]], dict[str, Any]]
    verification_store: Callable[[], Any]
    completion_fault_injector: Callable[[], Any]
    recovery_incident: Callable[[pathlib.Path], dict[str, Any] | None]
    recovery_epoch_required: Callable[[pathlib.Path, dict[str, Any], str], bool]


def canonical_json_sha256(value: Any) -> str:
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def safe_task_id(task_id: str) -> bool:
    return bool(isinstance(task_id, str) and TASK_ID_RE.fullmatch(task_id))


def completion_authority_dir(feature_dir: pathlib.Path, kind: str, task_id: str, *, access: StateAccess) -> pathlib.Path:
    if not safe_task_id(task_id) or kind not in {
        'completion-records', 'completion-corrections', 'completion-repair-authorizations',
        'completion-repair-claims', 'completion-repair-records', 'legacy-completion-bindings',
        'completion-repair-checkpoint-bindings',
    }:
        die('COMPLETION_AUTHORITY_INVALID: unsafe authority path')
    return _fs.runtime_state_dir(feature_dir, policy=access.policy, common_dir=access.common_dir) / kind / feature_dir.name / task_id


def legacy_completion_binding_id(binding: dict[str, Any]) -> str:
    semantic = {key: value for key, value in binding.items()
                if key not in {'binding_id', 'created_at'}}
    return f'legacy-completion-binding-v1:sha256:{canonical_json_sha256(semantic)}'


def validate_legacy_binding_shape(binding: Any, feature_dir: pathlib.Path,
                                  task_id: str, *, access: StateAccess) -> bool:
    if (not isinstance(binding, dict) or binding.get('schema_version') != 1 or
            binding.get('binding_version') != LEGACY_COMPLETION_BINDING_VERSION or
            binding.get('binding_type') != 'historical-legacy-completion' or
            binding.get('binding_id') != legacy_completion_binding_id(binding) or
            binding.get('repository_id') != str(access.common_dir(feature_dir)) or
            binding.get('feature') != feature_dir.name or binding.get('task') != task_id or
            binding.get('mode') not in {'AUTO_VERIFIED', 'HUMAN_ATTESTED'} or
            type(binding.get('attempt')) is not int or binding['attempt'] < 1 or
            binding.get('historical_status') != 'completed' or
            not _valid_completion_commit(binding.get('checkpoint')) or
            binding.get('checkpoint') != binding.get('evidence_checkpoint') or
            not _valid_completion_revision(binding.get('packet_revision')) or
            not _valid_completion_hash(binding.get('contract_fingerprint')) or
            not _valid_completion_hash(binding.get('evidence_sha256')) or
            not _valid_completion_commit(binding.get('evidence_checkpoint')) or
            not isinstance(binding.get('source_material'), list) or not binding['source_material'] or
            not isinstance(binding.get('attempt_identity'), str) or
            re.fullmatch(r'sha256:[0-9a-f]{64}', binding['attempt_identity']) is None or
            any(not isinstance(source, dict) or not isinstance(source.get('identity'), str) or
                not source['identity'] or source.get('immutable') is not True or
                not _valid_completion_hash(source.get('sha256'))
                for source in binding['source_material']) or
            not isinstance(binding.get('created_at'), str) or not binding['created_at'].strip()):
        return False
    if binding['mode'] == 'HUMAN_ATTESTED':
        fields = binding.get('attested_fields')
        required = {'checkpoint': binding['checkpoint'], 'packet_revision': binding['packet_revision'],
                    'contract_fingerprint': binding['contract_fingerprint'],
                    'repository': binding['repository_id'], 'feature': binding['feature'],
                    'task': task_id, 'attempt': binding['attempt']}
        return (isinstance(binding.get('operator'), str) and bool(binding['operator'].strip()) and
                isinstance(binding.get('reason'), str) and bool(binding['reason'].strip()) and
                isinstance(fields, dict) and all(fields.get(key) == value for key, value in required.items()))
    kinds = {source.get('kind') for source in binding['source_material']
             if isinstance(source, dict) and isinstance(source.get('kind'), str)}
    return len(binding['source_material']) >= 2 and len(kinds) >= 2


def legacy_completion_bindings(feature_dir: pathlib.Path, task_id: str, *, access: StateAccess) -> list[dict[str, Any]]:
    directory = completion_authority_dir(feature_dir, 'legacy-completion-bindings', task_id, access=access)
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        die('LEGACY_COMPLETION_INVALID: unsafe binding directory')
    bindings = []
    for path in sorted(directory.glob('*.json')):
        if path.is_symlink() or not path.is_file():
            die('LEGACY_COMPLETION_INVALID: binding authority is not a regular file')
        try:
            binding = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            die(f'LEGACY_COMPLETION_INVALID: unreadable binding: {exc}')
        if path.name != 'binding.json' or not validate_legacy_binding_shape(binding, feature_dir, task_id, access=access):
            die('LEGACY_COMPLETION_INVALID: malformed or unsupported binding')
        bindings.append(binding)
    if len(bindings) > 1:
        die('LEGACY_COMPLETION_CONFLICT: multiple immutable bindings exist')
    return bindings


def legacy_binding_as_completion(feature_dir: pathlib.Path, task_id: str,
                                 binding: dict[str, Any], *, access: StateAccess) -> dict[str, Any]:
    body = {
        'schema_version': COMPLETION_AUTHORITY_SCHEMA_VERSION, 'record_type': 'completion',
        'repository_id': binding['repository_id'], 'feature': binding['feature'],
        'task': task_id, 'attempt': binding['attempt'], 'checkpoint': binding['checkpoint'],
        'packet_revision': binding['packet_revision'],
        'contract_fingerprint': binding['contract_fingerprint'],
        'evidence_reference_sha256': binding['evidence_sha256'],
        'correction_id': None, 'repair_authorization_id': None,
        'legacy_binding_id': binding['binding_id'],
    }
    body['record_id'] = completion_record_id('completion', body)
    body['created_at'] = binding['created_at']
    if not validate_completion_authority_shape(body, 'completion'):
        die('LEGACY_COMPLETION_INVALID: reconstructed completion shape is invalid')
    return body


def completion_record_id(scheme: str, record: dict[str, Any]) -> str:
    semantic = {key: value for key, value in record.items() if key not in {'record_id', 'created_at'}}
    return f'{scheme}:sha256:{canonical_json_sha256(semantic)}'


def _valid_completion_hash(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _valid_completion_commit(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', value) is not None


def _valid_completion_revision(value: Any) -> bool:
    return value == 'unpublished' or (isinstance(value, str) and
                                      re.fullmatch(r'sha256:[0-9a-f]{64}', value) is not None)


def validate_completion_authority_shape(record: dict[str, Any], record_type: str) -> bool:
    if record_type == 'completion':
        return (type(record.get('attempt')) is int and record['attempt'] > 0 and
                _valid_completion_commit(record.get('checkpoint')) and
                _valid_completion_revision(record.get('packet_revision')) and
                _valid_completion_hash(record.get('contract_fingerprint')) and
                _valid_completion_hash(record.get('evidence_reference_sha256')) and
                ((record.get('correction_id') is None and record.get('repair_authorization_id') is None and
                  'supersedes_completion_id' not in record) or
                 (isinstance(record.get('correction_id'), str) and
                  isinstance(record.get('repair_authorization_id'), str) and
                  isinstance(record.get('supersedes_completion_id'), str))))
    if record_type == 'completion-correction':
        return (type(record.get('completed_attempt')) is int and record['completed_attempt'] > 0 and
                isinstance(record.get('original_completion_id'), str) and
                _valid_completion_commit(record.get('original_completion_checkpoint')) and
                _valid_completion_revision(record.get('packet_revision')) and
                _valid_completion_hash(record.get('contract_fingerprint')) and
                record.get('reason_code') == COMPLETION_CORRECTION_REASON and
                isinstance(record.get('reason'), str) and bool(record['reason'].strip()) and
                _valid_completion_hash(record.get('defect_evidence_sha256')) and
                isinstance(record.get('operator'), str) and bool(record['operator'].strip()))
    if record_type == 'completion-repair-authorization':
        return (type(record.get('attempt')) is int and record['attempt'] > 0 and
                isinstance(record.get('correction_id'), str) and
                isinstance(record.get('original_completion_id'), str) and
                _valid_completion_revision(record.get('packet_revision')) and
                _valid_completion_hash(record.get('contract_fingerprint')) and
                isinstance(record.get('reason'), str) and bool(record['reason'].strip()) and
                isinstance(record.get('operator'), str) and bool(record['operator'].strip()))
    if record_type == 'completion-repair-claim':
        return (type(record.get('attempt')) is int and record['attempt'] > 0 and
                isinstance(record.get('correction_id'), str) and
                isinstance(record.get('authorization_id'), str) and
                isinstance(record.get('owner'), str) and bool(record['owner'].strip()) and
                type(record.get('generation')) is int and record['generation'] > 0)
    if record_type == 'completion-repair':
        return (type(record.get('attempt')) is int and record['attempt'] > 0 and
                all(isinstance(record.get(key), str) and record[key] for key in (
                    'historical_completion_id', 'correction_id', 'repair_authorization_id',
                    'repair_claim_id', 'owner')) and
                _valid_completion_commit(record.get('checkpoint')) and
                _valid_completion_revision(record.get('packet_revision')) and
                _valid_completion_hash(record.get('contract_fingerprint')) and
                _valid_completion_hash(record.get('evidence_reference_sha256')) and
                record.get('protocol_version') == 1 and
                isinstance(record.get('created_at'), str) and bool(record['created_at']))
    return False


def read_completion_authority(feature_dir: pathlib.Path, kind: str, task_id: str,
                              *, record_type: str, access: StateAccess) -> list[dict[str, Any]]:
    directory = completion_authority_dir(feature_dir, kind, task_id, access=access)
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        die('COMPLETION_AUTHORITY_INVALID: authority directory is unsafe')
    records = []
    paths = sorted(directory.glob('*.json')) if directory.exists() else []
    for path in paths:
        if record_type == 'completion-repair' and path.name.startswith('claim-'):
            continue
        if path.is_symlink() or not path.is_file():
            die('COMPLETION_AUTHORITY_INVALID: authority record is not a regular file')
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            die(f'COMPLETION_AUTHORITY_INVALID: unreadable {record_type} record: {exc}')
        if (not isinstance(record, dict) or record.get('schema_version') != COMPLETION_AUTHORITY_SCHEMA_VERSION or
                record.get('record_type') != record_type or not isinstance(record.get('record_id'), str) or
                record.get('record_id') != completion_record_id(record_type, record) or
                path.stem != record['record_id'].rsplit(':', 1)[-1]):
            die(f'COMPLETION_AUTHORITY_INVALID: malformed or unsupported {record_type} record')
        if (record.get('repository_id') != str(access.common_dir(feature_dir)) or
                record.get('feature') != feature_dir.name or record.get('task') != task_id or
                not validate_completion_authority_shape(record, record_type)):
            die(f'COMPLETION_AUTHORITY_INVALID: {record_type} scope does not match canonical repository/task')
        records.append(record)
    transaction = getattr(_authority_transaction, 'current', None)
    if isinstance(transaction, dict) and transaction.get('feature') == feature_dir.name:
        try:
            relative_directory = directory.resolve(strict=False).relative_to(
                transaction['state_dir']).as_posix()
        except ValueError:
            die('COMPLETION_AUTHORITY_INVALID: pending authority directory escaped lifecycle state')
        for relative, (payload, _mode) in sorted(transaction['files'].items()):
            if pathlib.PurePosixPath(relative).parent.as_posix() != relative_directory or not relative.endswith('.json'):
                continue
            if record_type == 'completion-repair' and pathlib.PurePosixPath(relative).name.startswith('claim-'):
                continue
            try:
                record = json.loads(payload.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                die(f'COMPLETION_AUTHORITY_INVALID: malformed pending {record_type} record')
            if (not isinstance(record, dict) or record.get('schema_version') != COMPLETION_AUTHORITY_SCHEMA_VERSION or
                    record.get('record_type') != record_type or not isinstance(record.get('record_id'), str) or
                    record.get('record_id') != completion_record_id(record_type, record) or
                    pathlib.PurePosixPath(relative).stem != record['record_id'].rsplit(':', 1)[-1]):
                die(f'COMPLETION_AUTHORITY_INVALID: malformed pending {record_type} record')
            if (record.get('repository_id') != str(access.common_dir(feature_dir)) or
                    record.get('feature') != feature_dir.name or record.get('task') != task_id or
                    not validate_completion_authority_shape(record, record_type)):
                die(f'COMPLETION_AUTHORITY_INVALID: pending {record_type} scope does not match canonical repository/task')
            records.append(record)
    return records


def completion_records(feature_dir: pathlib.Path, task_id: str, *, access: StateAccess) -> list[dict[str, Any]]:
    records = read_completion_authority(feature_dir, 'completion-records', task_id,
                                        record_type='completion', access=access)
    bindings = legacy_completion_bindings(feature_dir, task_id, access=access)
    if bindings:
        reconstructed = legacy_binding_as_completion(feature_dir, task_id, bindings[0], access=access)
        if all(item.get('record_id') != reconstructed['record_id'] for item in records):
            records.append(reconstructed)
    return records


def repaired_completion_records(feature_dir: pathlib.Path, task_id: str, *, access: StateAccess) -> list[dict[str, Any]]:
    directory = completion_authority_dir(feature_dir, 'completion-repair-records', task_id, access=access)
    if not directory.exists():
        return []
    records = []
    for path in sorted(directory.glob('claim-*.json')):
        if path.is_symlink() or not path.is_file():
            die('COMPLETION_REPAIR_INVALID: claim finalization slot is not a regular file')
        try:
            item = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            die(f'COMPLETION_REPAIR_INVALID: unreadable claim finalization slot: {exc}')
        if (not isinstance(item, dict) or item.get('record_type') != 'completion-repair' or
                item.get('record_id') != completion_record_id('completion-repair', item) or
                not validate_completion_authority_shape(item, 'completion-repair') or
                item.get('repository_id') != str(access.common_dir(feature_dir)) or
                item.get('feature') != feature_dir.name or item.get('task') != task_id or
                path.stem != 'claim-' + item.get('repair_claim_id', '').rsplit(':', 1)[-1]):
            die('COMPLETION_REPAIR_INVALID: malformed claim finalization slot')
        records.append(item)
    return records


def recover_prepared_state_locked(feature_dir: pathlib.Path, *, access: StateAccess) -> None:
    """Finish an interrupted write-ahead snapshot before reading lifecycle state.

    The caller must hold the repository-wide lifecycle lock.
    """
    try:
        state_snapshots.recover_prepared_snapshots(
            access.policy.repo_base(feature_dir), _fs.runtime_state_dir(feature_dir, policy=access.policy, common_dir=access.common_dir))
    except state_snapshots.SnapshotError as exc:
        die(f'STATE_SNAPSHOT_RECOVERY_FAILED: {exc}')


@contextlib.contextmanager
def lifecycle_state_lock(feature_dir: pathlib.Path, *,
                         repository_admission_id: str | None = None,
                         allowed_reservation: tuple[str, str] | None = None,
                         allow_unvalidated_state: bool = False, access: StateAccess) -> Iterator[Any]:
    """Hold repository verifier admission through the lifecycle mutation."""
    store_module = access.verification_store()  # the consumer's store module, looked up per call (its tests patch it)
    StoreError, repository_lock = store_module.StoreError, store_module.repository_lock
    repository_lock_held = store_module.repository_lock_held
    store = store_module.VerificationStore(feature_dir)
    # The supervisor owns this repository lock while it performs the exact
    # launch CAS. Skip only this thread's already-held kernel lock, and only
    # when the unresolved journal proves the supplied admission ID.
    lock_context = repository_lock(store.root)
    lock_is_held = repository_lock_held(store.root)
    if lock_is_held:
        if isinstance(repository_admission_id, str) and repository_admission_id:
            unresolved = [started for _journal, started, closure in store._scan_executions()
                          if closure is None and started.get('execution_id') == repository_admission_id]
            if len(unresolved) != 1:
                raise StoreError('verification-owned')
            lock_context = contextlib.nullcontext()
        else:
            raise StoreError('verification-owned')
    with lock_context:
        # Only an in-lock launch/recovery callback may exclude one unresolved
        # execution from the repository barrier. A drained terminal CAS gets
        # ordinary admission after acquiring the repository lock.
        store.admit_repository_verification(
            allow_execution_id=repository_admission_id if lock_is_held else None)
        with _fs.lock_path(feature_dir, policy=access.policy, locking=access.locking).open('a+', encoding='utf-8') as lock:
            if access.locking is not None:
                access.locking.flock(lock.fileno(), access.locking.LOCK_EX)
            try:
                recover_prepared_state_locked(feature_dir, access=access)
                state_file = _fs.state_path(feature_dir, policy=access.policy, common_dir=access.common_dir)
                if state_file.is_file() and not state_file.is_symlink():
                    doc = access.load_validated(feature_dir)
                    # Reconcile and explicit migration validate their prior
                    # state against a historical authority before replacing it.
                    # The lock still inspects these bytes for active reservations.
                    state = (load_json(state_file) if allow_unvalidated_state else
                             access.read_state_pure(feature_dir, doc))
                    authority = state.get('verification_authority')
                    reservations = authority.get('launch_reservations', {}) if isinstance(authority, dict) else {}
                    if not isinstance(reservations, dict):
                        raise StoreError('LIFECYCLE_TRANSITION_INVALID')
                    active = [reservation for reservation in reservations.values()
                        if isinstance(reservation, dict) and reservation.get('status') in
                        {'launch_reserved', 'launch_consumed', 'execution_started'}]
                    if active:
                        accepted_plan_id = authority.get('accepted_plan_id') if isinstance(authority, dict) else None
                        if (allowed_reservation is None or allowed_reservation[0] != accepted_plan_id or
                                any(reservation.get('plan_id') != accepted_plan_id for reservation in active)):
                            raise StoreError('verification-owned')
                        if (not repository_admission_id and
                              allowed_reservation[1] not in reservations and
                              all(reservation.get('unit_id') != allowed_reservation[1] for reservation in active)):
                            raise StoreError('verification-owned')
                yield lock
            finally:
                if access.locking is not None:
                    access.locking.flock(lock.fileno(), access.locking.LOCK_UN)


def initial_state(feature_dir: pathlib.Path, doc: dict[str, Any], *, project_completions: bool = True, access: StateAccess) -> dict[str, Any]:
    state = {
        'state_version': 2,
        'protocol_version': protocol_version(feature_dir),
        'feature': doc.get('feature', feature_dir.name),
        'fingerprint': access.fingerprint(feature_dir),
        'protocol_fingerprint': access.protocol_fingerprint(feature_dir),
        'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'tasks': {
            t['id']: {'status': 'pending', 'attempts': 0}
            for t in doc['tasks'] if isinstance(t, dict) and isinstance(t.get('id'), str)
        },
    }
    # Rebuild completion projection from immutable C1/C2 authority when the
    # mutable lifecycle snapshot was lost. Preserve the historical attempt.
    for task_id, entry in state['tasks'].items() if project_completions else ():
        c2 = repaired_completion_records(feature_dir, task_id, access=access)
        if c2:
            latest = c2[-1]
            entry.update({'status': 'completed', 'attempts': latest['attempt'],
                          'checkpoint_commit': latest['checkpoint'],
                          'completion_repair_record_id': latest['record_id']})
            continue
        c1 = completion_records(feature_dir, task_id, access=access)
        if c1:
            latest = c1[-1]
            entry.update({'status': 'completed', 'attempts': latest['attempt'],
                          'checkpoint_commit': latest['checkpoint'],
                          'completion_record_id': latest['record_id']})
    return state


def load_state(feature_dir: pathlib.Path, doc: dict[str, Any], *, access: StateAccess) -> dict[str, Any]:
    with _fs.lock_path(feature_dir, policy=access.policy, locking=access.locking).open('a+', encoding='utf-8') as lock:
        if access.locking is not None:
            access.locking.flock(lock.fileno(), access.locking.LOCK_EX)
        state = _load_state_unlocked(feature_dir, doc, access=access)
        if access.locking is not None:
            access.locking.flock(lock.fileno(), access.locking.LOCK_UN)
    return state


def _resolve_state_unlocked(feature_dir: pathlib.Path, doc: dict[str, Any], *, access: StateAccess) -> tuple[dict[str, Any], bool]:
    """Resolve canonical or exact historical main-checkout state under the shared lock."""
    canonical = _fs.state_path(feature_dir, policy=access.policy, common_dir=access.common_dir)
    legacy = _fs.main_legacy_state_path(feature_dir, policy=access.policy, common_dir=access.common_dir)
    candidates: list[tuple[pathlib.Path, dict[str, Any], bool]] = []
    for path, is_legacy in ((canonical, False), (legacy, True)):
        if path.exists():
            state = load_json(path)
            _fs.validate_state_identity(feature_dir, doc, state, fingerprint=access.fingerprint)
            candidates.append((path, state, is_legacy))

    if len(candidates) > 1:
        first_path, first_state, _ = candidates[0]
        for other_path, other_state, _ in candidates[1:]:
            if other_state != first_state:
                die(
                    'conflicting valid lifecycle states exist at '
                    f'{first_path} and {other_path}; preserve both and resolve the conflict explicitly'
                )
        # Byte-equivalent duplicate state resolves deterministically to canonical.
        # The caller removes the redundant legacy path transactionally with the snapshot.
        return candidates[0][1], True
    if candidates:
        _, state, is_legacy = candidates[0]
        return state, is_legacy

    return initial_state(feature_dir, doc, access=access), False


def state_snapshot_status(feature_dir: pathlib.Path, *, access: StateAccess) -> str:
    repository = access.policy.repo_base(feature_dir)
    repository_status = state_snapshots.current_repository_state_status(
        repository, _fs.runtime_state_dir(feature_dir, policy=access.policy, common_dir=access.common_dir))
    if repository_status in {'invalid', 'state-missing', 'state-mismatch'}:
        return repository_status
    if repository_status == 'absent':
        if state_snapshots.current_ref_exists(repository, feature_dir.name):
            return 'invalid'
        return 'absent'
    if not state_snapshots.current_ref_exists(repository, feature_dir.name):
        return 'absent'
    try:
        snapshot = state_snapshots.read_current_snapshot(repository, feature_dir.name)
    except state_snapshots.SnapshotError:
        return 'invalid'
    canonical = _fs.state_path(feature_dir, policy=access.policy, common_dir=access.common_dir)
    if canonical.is_symlink() or not canonical.is_file():
        return 'state-missing'
    if (snapshot['manifest'].get('state_path') != canonical.name or
            snapshot['manifest'].get('state_revision') != sha256_bytes(canonical.read_bytes())):
        return 'state-mismatch'
    return 'consistent'


def state_recovery_required(feature_dir: pathlib.Path, *, access: StateAccess) -> bool:
    incident = access.recovery_incident(feature_dir)
    snapshot_status = state_snapshot_status(feature_dir, access=access)
    if snapshot_status in {'invalid', 'state-missing', 'state-mismatch'}:
        return True
    # Once this feature has an accepted loss incident, alternate legacy paths are
    # not substitutes for the absent canonical authority. Only an explicitly
    # reviewed recovery operation may establish the new epoch.
    if not incident:
        return False
    # The consumer's recovery-epoch check (Showcase: its incident history) may only refuse: anything but an exact
    # `False` keeps recovery required, and an error propagates, so it blocks too. It writes nothing.
    return access.recovery_epoch_required(feature_dir, incident, snapshot_status) is not False


def _load_state_unlocked(feature_dir: pathlib.Path, doc: dict[str, Any], *, access: StateAccess) -> dict[str, Any]:
    recover_prepared_state_locked(feature_dir, access=access)
    if state_recovery_required(feature_dir, access=access):
        die('STATE_RECOVERY_REQUIRED: state and current recovery authority disagree; use only a reviewed restore or new-epoch operation')
    state, legacy_path = _resolve_state_unlocked(feature_dir, doc, access=access)
    if legacy_path:
        _fs.validate_loaded_state(feature_dir, doc, state, fingerprint=access.fingerprint)
        if _fs.state_path(feature_dir, policy=access.policy, common_dir=access.common_dir).exists():
            legacy_rel = _fs.main_legacy_state_path(feature_dir, policy=access.policy, common_dir=access.common_dir).relative_to(
                _fs.runtime_state_dir(feature_dir, policy=access.policy, common_dir=access.common_dir).resolve(strict=False)).as_posix()
            access.save_state(feature_dir, state, remove_files={legacy_rel})
    elif not _fs.state_path(feature_dir, policy=access.policy, common_dir=access.common_dir).exists():
        # Fresh state is returned to the caller and persisted only after its lifecycle
        # operation succeeds. A validation error must never leave a shadow authority.
        return state
    else:
        _fs.validate_loaded_state(feature_dir, doc, state, fingerprint=access.fingerprint)
    return state


def _read_state_unlocked_pure(feature_dir: pathlib.Path, doc: dict[str, Any], *, access: StateAccess) -> dict[str, Any]:
    """Validate and read authority without adopting, migrating, cleaning, or writing it."""
    if state_recovery_required(feature_dir, access=access):
        die('STATE_RECOVERY_REQUIRED: authoritative state is absent; read-only projection is not lifecycle authority')
    state, _legacy_path = _resolve_state_unlocked(feature_dir, doc, access=access)
    _fs.validate_loaded_state(feature_dir, doc, state, fingerprint=access.fingerprint)
    return state


def fail_on_verification_store_error(exc: Exception) -> None:
    message = str(exc)
    die(f'VERIFICATION_OWNERSHIP_BLOCKED: {message}',
        code={'busy': 3, 'verification-owned': 6}.get(message, 5))


@contextlib.contextmanager
def locked_state(feature_dir: pathlib.Path, doc: dict[str, Any], *,
                 admission_guard: bool = True, access: StateAccess) -> Iterator[dict[str, Any]]:
    _fs.runtime_state_dir(feature_dir, policy=access.policy, common_dir=access.common_dir).mkdir(parents=True, exist_ok=True)
    StoreError = access.verification_store().StoreError
    if admission_guard:
        guard = lifecycle_state_lock(feature_dir, access=access)
        try:
            lock = guard.__enter__()
        except StoreError as exc:
            fail_on_verification_store_error(exc)
    else:
        guard = _fs.lock_path(feature_dir, policy=access.policy, locking=access.locking).open('a+', encoding='utf-8')
        lock = guard
        if access.locking is not None:
            access.locking.flock(lock.fileno(), access.locking.LOCK_EX)
    previous_transaction = getattr(_authority_transaction, 'current', None)
    _authority_transaction.current = {
        'feature': feature_dir.name,
        'repository_id': str(access.common_dir(feature_dir)),
        'state_dir': _fs.runtime_state_dir(feature_dir, policy=access.policy, common_dir=access.common_dir).resolve(),
        'files': {},
        'post_commit_failpoints': [],
    }
    try:
        state = _load_state_unlocked(feature_dir, doc, access=access)
        yield state
        pending = _authority_transaction.current['files']
        access.save_state(feature_dir, state, additional_files=pending or None)
        for boundary in _authority_transaction.current['post_commit_failpoints']:
            injector = access.completion_fault_injector()
            if callable(injector):
                injector(boundary)
    finally:
        _authority_transaction.current = previous_transaction
        if admission_guard:
            guard.__exit__(*sys.exc_info())
        else:
            if access.locking is not None:
                access.locking.flock(lock.fileno(), access.locking.LOCK_UN)
            lock.close()


def remove_state_locked(feature_dir: pathlib.Path, *, access: StateAccess) -> None:
    _fs.runtime_state_dir(feature_dir, policy=access.policy, common_dir=access.common_dir).mkdir(parents=True, exist_ok=True)
    with lifecycle_state_lock(feature_dir, access=access):
        _fs.state_path(feature_dir, policy=access.policy, common_dir=access.common_dir).unlink(missing_ok=True)
