"""Lifecycle transitions (AH5-14b-2, step 2).

Verbatim from Showcase `tooling/agent-harness/harness.py` at `792dfad`: claim, heartbeat, release, fail, complete,
start and its rollback, replan, reopen, reconcile and ready, with the packet, worktree, completion-publishing,
human-resolution and retry functions they use (`docs/specs/AH5-14/packet.md`, 80 functions).

The source reads its module's names at call time. The moved code does the same through one explicit binding, the
consumer's namespace ``ns``: every module-level name the source reads (its functions, constants, the shared
``_authority_transaction``, ``fcntl``, ``trust``, ``vc``) is read as ``ns.<name>`` at the moment of use, so the order of
checks, locks and writes, and the consumer's test patches, are unchanged. The names are declared in
``NAMESPACE_NAMES``; ``bind`` refuses a namespace that lacks any of them, or whose three recovery bindings
(``task_workspace_snapshot``, ``restore_task_workspace``, ``restore_attempt_authorization``, Showcase's until AH5-14d)
are not callable, so a missing binding is found before any operation starts. Admission state changes stay for 14c.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import subprocess
import tempfile
from typing import Any

RECOVERY_BINDINGS = ('task_workspace_snapshot', 'restore_task_workspace', 'restore_attempt_authorization')
NAMESPACE_NAMES = (
    '_verification_authority',
    '_verification_store',
    'AC_RE',
    'BRIDGE_SCHEMA_VERSION',
    'COMPLETION_AUTHORITY_SCHEMA_VERSION',
    'COMPLETION_EVIDENCE_SCHEMA_VERSION',
    'DEFAULT_HEARTBEAT_INTERVAL_SECONDS',
    'DEFAULT_LEASE_TTL_SECONDS',
    'FEATURE_ID_RE',
    'PACKET_REVISION_IDENTITY_SCHEME',
    'PROFILE_RE',
    'RECOVERY_RECORD_IDENTITY_SCHEME',
    'TASK_CONTRACT_FIELDS',
    'VALID_ROLES',
    'VALID_TEST_MODES',
    'VERIFICATION_BLOCKAGE_OUTCOMES',
    'WORKTREE_ROOT_SUFFIX',
    '_authority_transaction',
    '_bridge_request_identity',
    '_completion_failpoint',
    '_completion_fault_injector',
    '_completion_record_semantics',
    '_load_state_unlocked',
    '_path_is_selected',
    '_read_packet_identity_bridge',
    '_read_state_unlocked_pure',
    '_valid_completion_commit',
    'active_task_contract',
    'add_completion_record',
    'append_attempt_binding',
    'assert_worktree_protocol_current',
    'builder_pattern_has_concrete_root',
    'canonical_json_sha256',
    'canonical_packet_payload_sha256',
    'changed_paths',
    'checkpoint_worktree',
    'classify_historical_packet_binding',
    'classify_retry_authorizations',
    'completion_authority_dir',
    'completion_record_id',
    'completion_records',
    'completion_verification_artifact',
    'consume_attempt_authorization',
    'correction_is_repaired',
    'correction_records',
    'correction_targets_current_completion',
    'descendants',
    'die',
    'ensure_task_workspace_clean',
    'execution_protocol_paths',
    'fcntl',
    'feature_fingerprint',
    'feature_repo_base',
    'git_common_dir',
    'heartbeat',
    'heartbeat_interval_seconds',
    'historical_feature_fingerprint',
    'is_unrecovered_partial_claim',
    'lease_ttl_seconds',
    'legacy_packet_path',
    'legacy_revision_id',
    'lifecycle_state_lock',
    'load_json',
    'load_validated',
    'locked_state',
    'owner_guard',
    'packet_bound_semantic_contract_sha256',
    'packet_identity_bridge_path',
    'packet_identity_from_embedded_checksum',
    'packet_identity_from_recovery',
    'packet_identity_from_revision',
    'packet_matches_semantic_contract',
    'packet_payload',
    'packet_revision_id',
    'parse_aware_timestamp',
    'parse_timestamp',
    'path_matches',
    'prepare_task_worktree',
    'prior_builder_result_digest',
    'protocol_fingerprint',
    'protocol_version',
    'prune_task_workspace',
    'publish_completion_authority',
    'publish_revision',
    'read_completion_authority',
    'ready_ids',
    'recovery_proves_packet_binding',
    'recovery_record_identity',
    'refresh_lease',
    'reopen_dependency_graph',
    'repair_authorization_records',
    'repair_claim_records',
    'repaired_completion_records',
    'replan_failpoint',
    'repo_root',
    'require_available_completion_origin',
    'require_task_commit_ancestry',
    'required_completion_criteria',
    'resolve_accepted_verification_plan',
    'resolve_active_packet',
    'restore_attempt_authorization',
    'restore_task_workspace',
    'retire_finalized_completion_repair_pointers',
    'retry_binding',
    'reviewers',
    'revision_path',
    'runtime_state_dir',
    'safe_relative_pattern',
    'safe_task_id',
    'save_state',
    'semantic_task_contract',
    'semantic_task_contract_sha256',
    'sha256_bytes',
    'stage_first_task_base',
    'state_path',
    'task_base_commits',
    'task_has_effective_completion',
    'task_index',
    'task_workspace_snapshot',
    'task_worktree_snapshot',
    'trust',
    'utc_now',
    'validate_active_verification_resume',
    'validate_attempt_binding_ledger',
    'validate_completion_binding',
    'validate_completion_proofs',
    'validate_evidence',
    'validate_latest_human_resolution',
    'validate_packet_integrity',
    'validate_pending_verification_resume',
    'validate_replan_task',
    'vc',
    'worktree_path',
    'write_packet',
)


class ModuleNamespace:
    """A live view of a consumer module's globals: each attribute is read at the moment of use, as the source's own
    global lookups were, so the consumer's later definitions and its tests' patches are seen. Works for a module
    loaded under any name (Showcase's tests load `harness.py` as `sdd_harness` without registering it)."""

    __slots__ = ('_globals',)

    def __init__(self, module_globals: dict[str, Any]):
        self._globals = module_globals

    def __getattr__(self, name: str) -> Any:
        try:
            return self._globals[name]
        except KeyError:
            raise AttributeError(name) from None


def bind(ns: Any) -> Any:
    """Check the consumer namespace once, before any transition runs; return it unchanged."""
    missing = [name for name in NAMESPACE_NAMES if not hasattr(ns, name)]
    if missing:
        raise TypeError(f'transition namespace lacks required names: {missing}')
    not_callable = [name for name in RECOVERY_BINDINGS if not callable(getattr(ns, name))]
    if not_callable:
        raise TypeError(f'transition recovery bindings are not callable: {not_callable}')
    return ns


def historical_feature_fingerprint(feature_dir: pathlib.Path, commit: str, *, ns: Any) -> str:
    """Recompute the feature fingerprint from an immutable Git checkpoint."""
    digest = hashlib.sha256()
    for relative in ('spec.md', 'plan.md', 'tasks.json', 'design.json',
                     'design/gate.json', 'verification-contract.json',
                     'wayfinder-handoff.json'):
        result = subprocess.run(['git', 'show', f'{commit}:docs/specs/{feature_dir.name}/{relative}'],
                                cwd=ns.feature_repo_base(feature_dir), capture_output=True, check=False)
        if result.returncode:
            continue
        digest.update(relative.encode()); digest.update(b'\0')
        digest.update(result.stdout); digest.update(b'\0')
    return digest.hexdigest()


def cmd_reconcile_feature(args: argparse.Namespace, *, ns: Any) -> None:
    """Explicitly supersede a feature fingerprint under .agent-state CAS."""
    feature_dir = args.feature_dir.resolve()
    doc = ns.load_validated(feature_dir)
    reason, operator = str(args.reason or '').strip(), str(args.by or '').strip()
    if not reason or not operator:
        ns.die('FEATURE_REPLAN_REJECTED: explicit reason and operator attribution are required')
    ns.runtime_state_dir(feature_dir).mkdir(parents=True, exist_ok=True)
    with ns.lifecycle_state_lock(feature_dir, allow_unvalidated_state=True) as lock:
        path = ns.state_path(feature_dir)
        if not path.is_file() or path.is_symlink():
            ns.die('FEATURE_REPLAN_REJECTED: existing lifecycle state is required')
        state = ns.load_json(path)
        old_fingerprint = state.get('fingerprint')
        new_fingerprint = ns.feature_fingerprint(feature_dir)
        generation = state.get('feature_generation', 1)
        if type(generation) is not int or generation != args.expected_generation:
            ns.die('FEATURE_REPLAN_CAS_CONFLICT: expected generation differs')
        if old_fingerprint == new_fingerprint:
            ns.die('FEATURE_REPLAN_REJECTED: current feature fingerprint is unchanged')
        if state.get('state_version') != 2 or state.get('protocol_version') != ns.protocol_version(feature_dir):
            ns.die('FEATURE_REPLAN_REJECTED: unsupported lifecycle state version')
        base = state.get('base_commit')
        if not isinstance(base, str) or not re.fullmatch(r'[0-9a-f]{40}', base):
            ns.die('FEATURE_REPLAN_REJECTED: historical base checkpoint is unavailable')
        if ns.historical_feature_fingerprint(feature_dir, base) != old_fingerprint:
            ns.die('FEATURE_REPLAN_REJECTED: old fingerprint cannot be proven from its immutable checkpoint')
        tasks = state.get('tasks')
        if not isinstance(tasks, dict) or set(tasks) != set(ns.task_index(doc)):
            ns.die('FEATURE_REPLAN_REJECTED: task identity set changed; explicit feature replacement is required')
        record = {'schema_version': 1, 'kind': 'feature-fingerprint-replan',
            'from_generation': generation, 'to_generation': generation + 1,
            'old_fingerprint': old_fingerprint, 'new_fingerprint': new_fingerprint,
            'historical_checkpoint': base, 'reason': reason, 'operator_attribution': operator,
            'invalidated_verification_authority': True,
            'preserved_task_history': True, 'recorded_at': dt.datetime.now(dt.timezone.utc).isoformat()}
        history = state.setdefault('feature_replans', [])
        if not isinstance(history, list): ns.die('FEATURE_REPLAN_REJECTED: malformed feature replan history')
        state['feature_replans'] = [*history, record]
        prior_authority = state.get('verification_authority')
        if isinstance(prior_authority, dict) and prior_authority.get('accepted_plan_id') is not None:
            plan_history = state.setdefault('verification_plan_history', [])
            if not isinstance(plan_history, list):
                ns.die('FEATURE_REPLAN_REJECTED: malformed verification plan history')
            plan_history.append({'prior_authority': prior_authority, 'superseded_by': None,
                'superseded_at': record['recorded_at'],
                'reason': 'feature fingerprint reconciled; prior verification authority invalidated',
                'from_generation': generation, 'to_generation': generation + 1})
        state['fingerprint'] = new_fingerprint
        state['feature_generation'] = generation + 1
        state['verification_authority'] = {'accepted_plan_id': None, 'generation': generation + 1}
        ns.save_state(feature_dir, state)
        if ns.fcntl is not None: ns.fcntl.flock(lock.fileno(), ns.fcntl.LOCK_UN)
    print(f'REPLANNED feature={feature_dir.name} generation={generation + 1} fingerprint={new_fingerprint}')


def _completion_record_semantics(record: dict[str, Any], *, ns: Any) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != 'created_at'}


def publish_completion_authority(path: pathlib.Path, record: dict[str, Any], *, ns: Any) -> bool:
    """Durably publish an immutable record. Return False for exact idempotent replay."""
    if path.parent.is_symlink() or path.parent.resolve().parent != path.parent.parent.resolve():
        ns.die('COMPLETION_AUTHORITY_INVALID: authority path is not canonical')
    payload = json.dumps(record, indent=2, sort_keys=True) + '\n'
    feature = record.get('feature')
    repository_id = record.get('repository_id')
    transaction = getattr(ns._authority_transaction, 'current', None)
    queued: dict[str, tuple[bytes, int]] | None = None
    relative: str | None = None
    if (isinstance(transaction, dict) and isinstance(repository_id, str) and
            transaction.get('repository_id') == repository_id and
            transaction.get('feature') == feature):
        queued = transaction['files']
        try:
            relative = path.resolve(strict=False).relative_to(transaction['state_dir']).as_posix()
        except ValueError:
            ns.die('COMPLETION_AUTHORITY_INVALID: authority path is outside lifecycle state')
    prior_bytes = queued.get(relative) if queued is not None and relative is not None else None
    if path.exists() or path.is_symlink() or prior_bytes is not None:
        error_code = ('COMPLETION_REPAIR_CONFLICT' if path.parent.name == 'completion-repair-records'
                      and path.name.startswith('claim-') else 'COMPLETION_AUTHORITY_CONFLICT')
        if path.is_symlink() or not path.is_file():
            ns.die(f'{error_code}: existing authority is not a regular file')
        try:
            existing = json.loads(prior_bytes.decode('utf-8') if prior_bytes is not None
                                  else path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            ns.die(f'{error_code}: existing authority is unreadable: {exc}')
        same = (isinstance(existing, dict) and
                ((ns.completion_record_id('completion-repair', existing) == record.get('record_id'))
                 if path.parent.name == 'completion-repair-records' and path.name.startswith('claim-')
                 else ns._completion_record_semantics(existing) == ns._completion_record_semantics(record)))
        if not same:
            ns.die(f'{error_code}: immutable authority record conflicts with published content')
        return False

    if (not isinstance(feature, str) or not ns.FEATURE_ID_RE.fullmatch(feature) or
            not isinstance(repository_id, str) or not pathlib.Path(repository_id).is_absolute()):
        ns.die('COMPLETION_AUTHORITY_INVALID: cannot bind record to lifecycle snapshot')
    common_dir = pathlib.Path(repository_id).resolve()
    repository = common_dir.parent
    feature_dir = repository / 'docs' / 'specs' / feature
    if not feature_dir.is_dir() or ns.git_common_dir(feature_dir) != common_dir:
        ns.die('COMPLETION_AUTHORITY_INVALID: record repository does not match canonical feature')
    state_dir = ns.runtime_state_dir(feature_dir).resolve()
    if relative is None:
        try:
            relative = path.resolve(strict=False).relative_to(state_dir).as_posix()
        except ValueError:
            ns.die('COMPLETION_AUTHORITY_INVALID: authority path is outside lifecycle state')
    if state_dir.is_symlink():
        ns.die('COMPLETION_AUTHORITY_INVALID: lifecycle state root is unsafe')
    if queued is not None:
        queued[relative] = (payload.encode('utf-8'), 0o600)
        return True
    state = ns._read_state_unlocked_pure(feature_dir, ns.load_validated(feature_dir))
    try:
        ns.save_state(feature_dir, state, additional_files={relative: (payload.encode('utf-8'), 0o600)})
    except SystemExit as exc:
        if path.parent.name == 'completion-repair-records' and path.name.startswith('claim-'):
            ns.die(f'COMPLETION_REPAIR_CONFLICT: unable to durably publish claim slot ({exc})')
        raise
    return True


def _completion_failpoint(boundary: str, *, ns: Any) -> None:
    transaction = getattr(ns._authority_transaction, 'current', None)
    if (isinstance(transaction, dict) and boundary.endswith('-published') and
            boundary not in transaction['post_commit_failpoints']):
        transaction['post_commit_failpoints'].append(boundary)
        return
    if callable(ns._completion_fault_injector):
        ns._completion_fault_injector(boundary)


def correction_records(feature_dir: pathlib.Path, task_id: str, *, ns: Any) -> list[dict[str, Any]]:
    return ns.read_completion_authority(feature_dir, 'completion-corrections', task_id,
                                     record_type='completion-correction')


def repair_authorization_records(feature_dir: pathlib.Path, task_id: str, *, ns: Any) -> list[dict[str, Any]]:
    return ns.read_completion_authority(feature_dir, 'completion-repair-authorizations', task_id,
                                     record_type='completion-repair-authorization')


def repair_claim_records(feature_dir: pathlib.Path, task_id: str, *, ns: Any) -> list[dict[str, Any]]:
    return ns.read_completion_authority(feature_dir, 'completion-repair-claims', task_id,
                                     record_type='completion-repair-claim')


def correction_is_repaired(feature_dir: pathlib.Path, task_id: str,
                           correction: dict[str, Any], *, ns: Any) -> bool:
    authorizations = {record['record_id']: record for record in ns.repair_authorization_records(feature_dir, task_id)}
    claims = {record['record_id']: record for record in ns.repair_claim_records(feature_dir, task_id)}
    histories = {record['record_id']: record for record in ns.completion_records(feature_dir, task_id)}
    for record in ns.repaired_completion_records(feature_dir, task_id):
        if record.get('correction_id') != correction['record_id']:
            continue
        authorization = authorizations.get(record.get('repair_authorization_id'))
        claim = claims.get(record.get('repair_claim_id'))
        historical = histories.get(record.get('historical_completion_id'))
        if (authorization and authorization.get('correction_id') == correction['record_id'] and
                claim and historical and
                historical.get('record_id') == correction.get('original_completion_id') and
                claim.get('authorization_id') == authorization['record_id'] and
                claim.get('correction_id') == correction['record_id'] and
                claim.get('owner') == record.get('owner') and
                claim.get('attempt') == record.get('attempt') and
                authorization.get('task') == task_id and authorization.get('attempt') == correction.get('completed_attempt') and
                authorization.get('original_completion_id') == correction.get('original_completion_id') and
                authorization.get('repository_id') == correction.get('repository_id') and
                authorization.get('packet_revision') == correction.get('packet_revision') and
                authorization.get('contract_fingerprint') == correction.get('contract_fingerprint') and
                record.get('repository_id') == correction.get('repository_id') and
                record.get('task') == task_id and record.get('attempt') == correction.get('completed_attempt') and
                record.get('packet_revision') == correction.get('packet_revision') and
                record.get('contract_fingerprint') == correction.get('contract_fingerprint')):
            return True
    # Read pre-M4.2 repair completions for historical compatibility. New repairs
    # are published only as distinct completion-repair records above.
    for record in ns.completion_records(feature_dir, task_id):
        if record.get('correction_id') != correction['record_id']:
            continue
        authorization = authorizations.get(record.get('repair_authorization_id'))
        if (authorization and authorization.get('correction_id') == correction['record_id'] and
                authorization.get('task') == task_id and authorization.get('attempt') == correction.get('completed_attempt') and
                record.get('attempt') == correction.get('completed_attempt') and
                record.get('supersedes_completion_id') == correction.get('original_completion_id') and
                record.get('packet_revision') == correction.get('packet_revision') and
                record.get('contract_fingerprint') == correction.get('contract_fingerprint')):
            return True
    return False


def correction_targets_current_completion(feature_dir: pathlib.Path, task_id: str,
                                          entry: dict[str, Any],
                                          correction: dict[str, Any], *, ns: Any) -> bool:
    """Return whether lifecycle state still names the completion this correction repairs."""
    if (entry.get('completion_record_id') == correction.get('original_completion_id') or
            entry.get('active_completion_correction_id') == correction.get('record_id')):
        return True
    repair_record_id = entry.get('completion_repair_record_id')
    return isinstance(repair_record_id, str) and any(
        record.get('record_id') == repair_record_id and
        record.get('correction_id') == correction.get('record_id')
        for record in ns.repaired_completion_records(feature_dir, task_id))


def retire_finalized_completion_repair_pointers(feature_dir: pathlib.Path, task_id: str,
                                                entry: dict[str, Any], *, retired_by: str, ns: Any) -> None:
    """Move a finalized repair's active pointers into immutable attempt-tagged state history."""
    correction_id = entry.get('active_completion_correction_id')
    authorization_id = entry.get('active_completion_repair_authorization')
    if correction_id is None and authorization_id is None:
        return
    if not isinstance(correction_id, str) or not isinstance(authorization_id, str):
        ns.die('COMPLETION_REPAIR_POINTER_INVALID: correction and authorization pointers must be paired')

    correction = next((item for item in ns.correction_records(feature_dir, task_id)
                       if item.get('record_id') == correction_id), None)
    authorization = next((item for item in ns.repair_authorization_records(feature_dir, task_id)
                          if item.get('record_id') == authorization_id), None)
    current_attempt = int(entry.get('attempts', 0))
    correction_attempt = correction.get('completed_attempt') if correction else None
    if (not correction or not authorization or
            not isinstance(correction_attempt, int) or correction_attempt > current_attempt or
            authorization.get('attempt') != correction_attempt or
            authorization.get('correction_id') != correction_id or
            not ns.correction_is_repaired(feature_dir, task_id, correction)):
        ns.die('COMPLETION_REPAIR_POINTER_INVALID: active repair is not finalized for an existing attempt')

    repair = next((item for item in ns.repaired_completion_records(feature_dir, task_id)
                   if item.get('correction_id') == correction_id and
                   item.get('repair_authorization_id') == authorization_id and
                   item.get('attempt') == correction_attempt), None)
    if not repair or entry.get('completion_repair_record_id') != repair.get('record_id'):
        ns.die('COMPLETION_REPAIR_POINTER_INVALID: finalized repair record does not match lifecycle state')

    history = entry.setdefault('completion_repair_pointer_history', [])
    retired = {
        'attempt': correction_attempt,
        'correction_id': correction_id,
        'repair_authorization_id': authorization_id,
        'repair_completion_id': repair['record_id'],
        'retired_by': retired_by,
        'retired_at': ns.utc_now().isoformat(),
    }
    if not any(item.get('attempt') == correction_attempt and item.get('correction_id') == correction_id and
               item.get('repair_authorization_id') == authorization_id for item in history):
        history.append(retired)
    entry.pop('active_completion_correction_id', None)
    entry.pop('active_completion_repair_authorization', None)


def task_has_effective_completion(feature_dir: pathlib.Path, task_id: str,
                                  entry: dict[str, Any], *, ns: Any) -> bool:
    corrections = ns.correction_records(feature_dir, task_id)
    if any(not ns.correction_is_repaired(feature_dir, task_id, item) for item in corrections):
        return False
    repaired = [record for record in ns.repaired_completion_records(feature_dir, task_id)
                if record.get('attempt') == entry.get('attempts')]
    if repaired:
        return True
    if entry.get('status') == 'completed':
        return True
    # A fresh reader can observe a durable completion record written just before the
    # lifecycle snapshot. Match the attempt before treating that crash window as complete.
    return any(record.get('attempt') == entry.get('attempts') and
               (entry.get('status') == 'running' or entry.get('status') == 'correction_required')
               for record in ns.completion_records(feature_dir, task_id))


def utc_now(*, ns: Any) -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_timestamp(value: Any, *, ns: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def lease_ttl_seconds(doc: dict[str, Any], *, ns: Any) -> int:
    return int(doc.get('lease_ttl_seconds', ns.DEFAULT_LEASE_TTL_SECONDS))


def heartbeat_interval_seconds(doc: dict[str, Any], *, ns: Any) -> int:
    return int(doc.get('heartbeat_interval_seconds', ns.DEFAULT_HEARTBEAT_INTERVAL_SECONDS))


def refresh_lease(entry: dict[str, Any], doc: dict[str, Any], *, now: dt.datetime | None = None, ns: Any) -> None:
    current = now or ns.utc_now()
    entry['heartbeat_at'] = current.isoformat()
    entry['lease_expires_at'] = (current + dt.timedelta(seconds=ns.lease_ttl_seconds(doc))).isoformat()


def lease_expired(entry: dict[str, Any], *, now: dt.datetime | None = None, ns: Any) -> bool:
    if entry.get('status') != 'running':
        return False
    expires = ns.parse_timestamp(entry.get('lease_expires_at'))
    if expires is None:
        # Legacy/invalid running state is treated as stale rather than leased forever.
        return True
    return expires <= (now or ns.utc_now())


def heartbeat(feature_dir: pathlib.Path, doc: dict[str, Any], task_id: str, owner: str, *, ns: Any) -> str:
    # Lease renewal does not claim/change a worktree and must remain available
    # while the verifier's durable repository reservation is active.
    with ns.locked_state(feature_dir, doc, admission_guard=False) as state:
        entry = state['tasks'].get(task_id)
        if not entry:
            ns.die(f'unknown task {task_id}')
        ns.owner_guard(entry, owner)
        if entry.get('status') != 'running':
            ns.die(f'{task_id} is not running')
        if ns.is_unrecovered_partial_claim(entry):
            ns.die(f'CLAIM_RECOVERY_REQUIRED: {task_id} must be explicitly recovered before execution heartbeat')
        ns.refresh_lease(entry, doc)
        return str(entry['lease_expires_at'])


def ready_ids(doc: dict[str, Any], state: dict[str, Any], feature_dir: pathlib.Path | None = None, *, ns: Any) -> list[str]:
    max_parallel = int(doc.get('max_parallel', 4))
    running = sum(1 for entry in state['tasks'].values() if entry.get('status') == 'running')
    capacity = max(0, max_parallel - running)
    if capacity == 0:
        return []
    ready: list[str] = []
    for task in doc['tasks']:
        if not isinstance(task, dict) or not isinstance(task.get('id'), str):
            continue
        tid = task['id']
        entry = state['tasks'][tid]
        if entry['status'] not in {'pending', 'failed'}:
            continue
        contract_task = ns.active_task_contract(feature_dir, doc, tid, state=state) if feature_dir is not None else task
        if (entry.get('status') == 'failed' and
                not isinstance(entry.get('verification_resume_pending'), dict) and
                int(entry.get('attempts', 0)) >= 1 + int(doc.get('max_rework_attempts', 2)) and
                int(entry.get('human_resume_grants', 0)) <= 0):
            if feature_dir is None or ns.classify_retry_authorizations(feature_dir, doc, state, tid, int(entry.get('attempts', 0)))[0] != 'valid':
                continue
        if all(ns.task_has_effective_completion(feature_dir, dep, state['tasks'][dep])
               if feature_dir is not None else state['tasks'][dep]['status'] == 'completed'
               for dep in contract_task.get('depends_on', [])):
            ready.append(tid)
    return ready[:capacity]


def prior_builder_result_digest(feature_dir: pathlib.Path, entry: dict[str, Any], task_id: str,
                                evidence_path: str | None = None, *, ns: Any) -> str:
    """Resolve and hash the exact passing builder result for a verification resume."""
    value = evidence_path if evidence_path is not None else entry.get('last_failure_evidence')
    if not isinstance(value, str) or not value:
        ns.die('VERIFICATION_RESUME_EVIDENCE_UNAVAILABLE: prior builder result path is missing')
    feature = str(ns.load_validated(feature_dir).get('feature', feature_dir.name))
    repository = ns.feature_repo_base(feature_dir)
    run_root = repository / '.agent-runs' / feature
    if run_root.is_symlink():
        ns.die('VERIFICATION_RESUME_EVIDENCE_UNAVAILABLE: run evidence root is unsafe')
    try:
        allowed_root = run_root.resolve(strict=True)
        candidate = pathlib.Path(value)
        if candidate.is_symlink():
            ns.die('VERIFICATION_RESUME_EVIDENCE_UNAVAILABLE: builder result is a symlink')
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(allowed_root)
        if not resolved.is_file():
            raise OSError('not a regular result file')
        payload = resolved.read_bytes()
        document = json.loads(payload)
        if not isinstance(document, dict) or document.get('task') != task_id or document.get('status') != 'pass':
            raise ValueError('result is not a passing builder result for this task')
        return ns.sha256_bytes(payload)
    except (OSError, ValueError, json.JSONDecodeError):
        ns.die('VERIFICATION_RESUME_EVIDENCE_UNAVAILABLE: exact passing builder result cannot be verified')


def packet_payload(doc: dict[str, Any], task: dict[str, Any], feature_dir: pathlib.Path, *, ns: Any) -> dict[str, Any]:
    profile = task.get('agent_profile') or task['role']
    root = ns.feature_repo_base(feature_dir)
    try:
        feature_rel = feature_dir.resolve().relative_to(root.resolve())
    except ValueError:
        ns.die('feature directory must live inside the repository')
    payload = {
        'protocol_version': 4,
        'feature': doc.get('feature', feature_dir.name),
        'feature_fingerprint': ns.feature_fingerprint(feature_dir),
        'semantic_contract_sha256': ns.semantic_task_contract_sha256(feature_dir, doc, task),
        'protocol_fingerprint': ns.protocol_fingerprint(feature_dir),
        'task': task['id'],
        'title': task['title'],
        'objective': task['objective'],
        'role': task['role'],
        'agent_profile': profile,
        'depends_on': task.get('depends_on', []),
        'allowed_paths': task.get('allowed_paths', []),
        'acceptance_criteria': task.get('acceptance_criteria', []),
        'risk_tags': task.get('risk_tags', []),
        'required_reviewers': ns.reviewers(task),
        'verification': task.get('verification', []),
        'test_policy': doc.get('test_policy', 'legacy'),
        'test_mode': task.get('test_mode'),
        'test_seam': task.get('test_seam'),
        'lease_policy': {
            'ttl_seconds': ns.lease_ttl_seconds(doc),
            'heartbeat_interval_seconds': ns.heartbeat_interval_seconds(doc),
        },
        'context': {
            'agent_guide': 'AGENTS.md',
            'constitution': 'docs/agentic-sdd/constitution.md',
            'role': f'docs/agentic-sdd/agents/{profile}.md',
            'spec': str(feature_rel / 'spec.md'),
            'plan': str(feature_rel / 'plan.md'),
            'verification_contract': str(feature_rel / 'verification-contract.json') if (feature_dir / 'verification-contract.json').exists() else None,
            'adrs': 'docs/adr/',
            'architecture': 'docs/architecture/README.md',
            'messaging_contract': 'contracts/asyncapi/asyncapi.yml' if 'messaging' in task.get('risk_tags', []) else None,
        },
        'context_trust': {},
        'completion_contract': {
            'agent_must_not_commit': True,
            'agent_must_not_push': True,
            'builder_cannot_self_approve': task['role'] == 'builder',
            'modify_only_allowed_paths': True,
            'record_changed_paths': True,
            'record_commands_and_results': True,
            'record_assumptions_and_residual_risk': True,
        },
    }
    payload['context_trust'] = ns.trust.packet_context_trust(payload['context'])
    canonical = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    payload['packet_sha256'] = ns.sha256_bytes(canonical)
    return payload


def write_packet(doc: dict[str, Any], task: dict[str, Any], feature_dir: pathlib.Path,
                 *, state: dict[str, Any] | None = None, ns: Any) -> pathlib.Path:
    active = ns.resolve_active_packet(feature_dir, doc, task['id'], state=state)
    if active.get('revision_id') != ns.legacy_revision_id(feature_dir, task['id']):
        return pathlib.Path(active['path'])
    try:
        payload = ns.packet_payload(doc, task, feature_dir)
        encoded = json.dumps(payload, indent=2, sort_keys=True) + '\n'
    except Exception as exc:
        ns.die(f'cannot serialize task packet for {task.get("id", "unknown")}: {exc}')
    out_dir = feature_dir / 'packets'
    out = out_dir / f'{task["id"]}.json'
    if out.exists():
        try:
            existing = json.loads(out.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            ns.die(f'immutable task packet cannot be validated: {out}: {exc}')
        if not isinstance(existing, dict) or not ns.packet_matches_semantic_contract(existing, doc, task, feature_dir):
            ns.die(f'immutable task packet has a different semantic contract: {out}; re-plan the task before replacing it')
        # Preserve the original bytes as historical packet/provenance evidence.
        return out

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f'.{out.name}.', dir=out_dir)
    except OSError as exc:
        ns.die(f'cannot prepare immutable task packet {out}: {exc}')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        # Hard-link publication is atomic and refuses to overwrite a concurrent winner.
        # A packet orphaned by a later state-save failure is harmless immutable planning
        # evidence and is reusable on the next claim.
        try:
            os.link(temporary, out)
        except FileExistsError:
            existing = json.loads(out.read_text(encoding='utf-8'))
            if not isinstance(existing, dict) or not ns.packet_matches_semantic_contract(existing, doc, task, feature_dir):
                ns.die(f'immutable task packet has a different semantic contract: {out}; re-plan the task before replacing it')
        dir_fd = os.open(out_dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as exc:
        ns.die(f'cannot atomically publish task packet {out}: {exc}')
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return out


def legacy_packet_path(feature_dir: pathlib.Path, task_id: str, *, ns: Any) -> pathlib.Path:
    return feature_dir / 'packets' / f'{task_id}.json'


def legacy_revision_id(feature_dir: pathlib.Path, task_id: str, *, ns: Any) -> str:
    path = ns.legacy_packet_path(feature_dir, task_id)
    if not path.exists():
        return 'unpublished'
    if path.is_symlink():
        ns.die('ACTIVE_PACKET_AMBIGUOUS: legacy packet path is a symlink')
    try:
        packet = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        ns.die(f'ACTIVE_PACKET_AMBIGUOUS: cannot read legacy packet {path}: {exc}')
    if not isinstance(packet, dict) or not ns.safe_task_id(task_id):
        ns.die('ACTIVE_PACKET_AMBIGUOUS: malformed legacy packet identity')
    ns.validate_packet_integrity(packet)
    return ns.packet_revision_id(packet)


def validate_packet_integrity(packet: dict[str, Any], *, ns: Any) -> None:
    stored = packet.get('packet_sha256')
    if (not isinstance(stored, str) or re.fullmatch(r'[0-9a-f]{64}', stored) is None or
            stored != ns.canonical_packet_payload_sha256(packet)):
        ns.die('ACTIVE_PACKET_AMBIGUOUS: packet integrity check failed')


def revision_path(feature_dir: pathlib.Path, task_id: str, revision_id: str, *, ns: Any) -> pathlib.Path:
    if not ns.safe_task_id(task_id) or not isinstance(revision_id, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', revision_id):
        ns.die('ACTIVE_PACKET_AMBIGUOUS: invalid task or packet revision identifier')
    root = ns.runtime_state_dir(feature_dir) / 'packet-revisions' / feature_dir.name / task_id
    candidate = root / f'{revision_id.split(":", 1)[1]}.json'
    authority_root = ns.runtime_state_dir(feature_dir) / 'packet-revisions'
    feature_root = authority_root / feature_dir.name
    if (root.is_symlink() or feature_root.is_symlink() or candidate.is_symlink() or
            (authority_root.exists() and authority_root.is_symlink()) or
            candidate.resolve().parent != root.resolve()):
        ns.die('ACTIVE_PACKET_AMBIGUOUS: packet revision path escapes its task directory')
    return candidate


def resolve_active_packet(feature_dir: pathlib.Path, doc: dict[str, Any], task_id: str,
                          *, state: dict[str, Any] | None = None, ns: Any) -> dict[str, Any]:
    """Resolve one validated active packet; legacy tasks keep their historical path."""
    if not ns.safe_task_id(task_id) or task_id not in ns.task_index(doc):
        ns.die(f'ACTIVE_PACKET_AMBIGUOUS: unknown or unsafe task identity {task_id!r}')
    if state is None:
        # Lifecycle snapshots are atomically replaced. Readers validate one snapshot
        # without creating/acquiring the repository-wide writer lock.
        state = ns._read_state_unlocked_pure(feature_dir, doc)
    entry = state['tasks'][task_id]
    ns.validate_attempt_binding_ledger(entry)
    active_id = entry.get('active_packet_revision')
    if active_id is None:
        path = ns.legacy_packet_path(feature_dir, task_id)
        if not path.exists():
            # Preserve supported lazy materialization before a running attempt exists.
            if (entry.get('status') == 'running' and
                    (entry.get('active_packet_revision') is not None or entry.get('attempt_bindings'))):
                ns.die('ACTIVE_PACKET_AMBIGUOUS: required legacy packet is missing')
            fingerprint = ns.semantic_task_contract_sha256(feature_dir, doc, ns.task_index(doc)[task_id])
            return {'revision_id': 'unpublished', 'contract_sha256': fingerprint,
                    'packet': None, 'path': str(path), 'legacy': True}
        if path.is_symlink():
            ns.die('ACTIVE_PACKET_AMBIGUOUS: legacy packet path is a symlink')
        try:
            packet = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            ns.die(f'ACTIVE_PACKET_AMBIGUOUS: malformed legacy packet: {exc}')
        if not isinstance(packet, dict):
            ns.die('ACTIVE_PACKET_AMBIGUOUS: legacy packet is not an object')
        ns.validate_packet_integrity(packet)
        if packet.get('feature') != doc.get('feature', feature_dir.name) or packet.get('task') != task_id:
            ns.die('ACTIVE_PACKET_AMBIGUOUS: legacy packet feature/task identity mismatch')
        current = ns.packet_payload(doc, ns.task_index(doc)[task_id], feature_dir)
        # Before replanning, semantic drift is never silently accepted.
        if not ns.packet_matches_semantic_contract(packet, doc, ns.task_index(doc)[task_id], feature_dir):
            # A feature-level explicit replan may supersede a legacy packet
            # only when its old feature fingerprint is exactly the prior
            # fingerprint recorded by the durable feature-replan CAS. Expose
            # it as history for cmd_replan_task; consumers still reject it
            # against the current semantic task contract.
            replans = state.get('feature_replans', [])
            prior = next((item for item in reversed(replans) if isinstance(item, dict) and
                          item.get('new_fingerprint') == ns.feature_fingerprint(feature_dir)), None)
            if (prior is None or packet.get('feature_fingerprint') != prior.get('old_fingerprint') or
                    entry.get('status') not in {'running', 'failed'}):
                ns.die('TASK_REPLAN_REQUIRED: active legacy packet differs from the planning contract')
        return {'revision_id': ns.packet_revision_id(packet),
                'contract_sha256': packet.get('semantic_contract_sha256', current['semantic_contract_sha256']),
                'packet': packet, 'path': str(path), 'legacy': True}
    lineage = entry.get('packet_lineage')
    if not isinstance(lineage, list) or not lineage:
        ns.die('ACTIVE_PACKET_AMBIGUOUS: active revision has no packet lineage')
    ids: list[str] = []
    previous = None
    for relation in lineage:
        if (not isinstance(relation, dict) or relation.get('previous_revision') != previous or
                not isinstance(relation.get('revision_id'), str) or
                not re.fullmatch(r'sha256:[0-9a-f]{64}', relation['revision_id']) or
                not isinstance(relation.get('contract_sha256'), str) or
                not re.fullmatch(r'[0-9a-f]{64}', relation['contract_sha256'])):
            ns.die('ACTIVE_PACKET_AMBIGUOUS: malformed or disconnected packet lineage')
        if not isinstance(relation.get('legacy'), bool):
            ns.die('ACTIVE_PACKET_AMBIGUOUS: packet lineage is missing legacy identity evidence')
        previous = relation['revision_id']
        ids.append(previous)
    if len(ids) != len(set(ids)) or ids[-1] != active_id:
        ns.die('ACTIVE_PACKET_AMBIGUOUS: packet lineage is cyclic or active pointer disagrees')
    requests = entry.get('replan_requests')
    if not isinstance(requests, list) or len(requests) != len(lineage) - 1:
        ns.die('ACTIVE_PACKET_AMBIGUOUS: packet supersession lineage and audit requests disagree')
    seen_request_ids: set[str] = set()
    repository = str(ns.git_common_dir(feature_dir))
    for index, request in enumerate(requests):
        old_relation, new_relation = lineage[index], lineage[index + 1]
        if (not isinstance(request, dict) or
                not isinstance(request.get('request_id'), str) or
                not re.fullmatch(r'[0-9a-f]{64}', request['request_id']) or
                request['request_id'] in seen_request_ids or
                request.get('repository') != repository or
                request.get('feature') != doc.get('feature', feature_dir.name) or
                request.get('task') != task_id or
                request.get('old_revision') != old_relation['revision_id'] or
                request.get('new_revision') != new_relation['revision_id'] or
                request.get('old_contract_sha256') != old_relation['contract_sha256'] or
                request.get('new_contract_sha256') != new_relation['contract_sha256'] or
                request.get('expected_status') not in {'running', 'failed'} or
                not isinstance(request.get('expected_attempts'), int) or
                isinstance(request.get('expected_attempts'), bool) or
                request['expected_attempts'] < (1 if request.get('expected_status') == 'running' else 0) or
                (request.get('expected_status') == 'failed' and
                 (type(request.get('expected_feature_generation')) is not int or
                  request['expected_feature_generation'] < 1)) or
                ('expected_feature_generation' in request and
                 (type(request.get('expected_feature_generation')) is not int or
                  request['expected_feature_generation'] < 1)) or
                not isinstance(request.get('reason'), str) or not request['reason'].strip() or
                not isinstance(request.get('provenance'), str) or not request['provenance'] or
                ns.parse_timestamp(request.get('committed_at')) is None):
            ns.die('ACTIVE_PACKET_AMBIGUOUS: malformed or conflicting replan audit record')
        seen_request_ids.add(request['request_id'])
    path = ns.revision_path(feature_dir, task_id, active_id)
    try:
        packet = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        ns.die(f'ACTIVE_PACKET_AMBIGUOUS: active immutable packet is missing or malformed: {exc}')
    if not isinstance(packet, dict):
        ns.die('ACTIVE_PACKET_AMBIGUOUS: active packet is not an object')
    ns.validate_packet_integrity(packet)
    if ns.packet_revision_id(packet) != active_id or packet.get('task') != task_id or packet.get('feature') != doc.get('feature', feature_dir.name):
        ns.die('ACTIVE_PACKET_AMBIGUOUS: active packet content identity mismatch')
    last = lineage[-1]
    active_contract = ns.packet_bound_semantic_contract_sha256(feature_dir, packet)
    if (packet.get('semantic_contract_sha256') != active_contract or
            active_contract != last['contract_sha256']):
        ns.die('ACTIVE_PACKET_AMBIGUOUS: active packet contract differs from lineage')
    for relation in lineage[:-1]:
        canonical_history = ns.revision_path(feature_dir, task_id, relation['revision_id'])
        # Legacy roots are copied into immutable repository authority during replan.
        # Keep the local legacy fallback only for already-supported old lineages
        # that predate canonical historical materialization.
        historical_path = (canonical_history if canonical_history.exists() or not relation['legacy']
                           else ns.legacy_packet_path(feature_dir, task_id))
        try:
            historical = json.loads(historical_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            ns.die(f'ACTIVE_PACKET_AMBIGUOUS: historical packet in lineage is unavailable: {exc}')
        if not isinstance(historical, dict):
            ns.die('ACTIVE_PACKET_AMBIGUOUS: historical packet in lineage is not an object')
        ns.validate_packet_integrity(historical)
        if (ns.packet_revision_id(historical) != relation['revision_id'] or
                historical.get('task') != task_id or
                historical.get('feature') != doc.get('feature', feature_dir.name)):
            ns.die('ACTIVE_PACKET_AMBIGUOUS: historical packet identity differs from lineage')
        historical_contract = ns.packet_bound_semantic_contract_sha256(feature_dir, historical)
        if (historical.get('semantic_contract_sha256') not in (None, historical_contract) or
                historical_contract != relation['contract_sha256']):
            ns.die('ACTIVE_PACKET_AMBIGUOUS: historical contract differs from lineage')
    if entry.get('status') == 'running':
        attempts = entry.get('attempt_bindings', [])
        latest = max((x for x in attempts if isinstance(x, dict) and isinstance(x.get('attempt'), int)),
                     key=lambda x: x['attempt'], default=None) if isinstance(attempts, list) else None
        if latest is None or latest.get('packet_revision') != active_id or latest.get('contract_sha256') != last['contract_sha256']:
            ns.die('ACTIVE_PACKET_AMBIGUOUS: running lifecycle attempt is not bound to the active packet')
    return {'revision_id': active_id, 'contract_sha256': last['contract_sha256'],
            'packet': packet, 'path': str(path), 'legacy': False}


def validate_attempt_binding_ledger(entry: dict[str, Any], *, ns: Any) -> None:
    history = entry.get('attempt_bindings')
    if history is None:
        return
    if not isinstance(history, list):
        ns.die('ACTIVE_PACKET_AMBIGUOUS: attempt binding ledger is malformed')
    seen: set[int] = set()
    for binding in history:
        if not isinstance(binding, dict):
            ns.die('ACTIVE_PACKET_AMBIGUOUS: attempt binding record is not an object')
        attempt = binding.get('attempt')
        status = binding.get('binding_status')
        if (not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1 or
                attempt > int(entry.get('attempts', 0)) or attempt in seen):
            ns.die('ACTIVE_PACKET_AMBIGUOUS: invalid or duplicate attempt binding number')
        seen.add(attempt)
        if status == 'proven':
            if (not isinstance(binding.get('packet_revision'), str) or
                    not re.fullmatch(r'sha256:[0-9a-f]{64}', binding['packet_revision']) or
                    not isinstance(binding.get('contract_sha256'), str) or
                    not re.fullmatch(r'[0-9a-f]{64}', binding['contract_sha256'])):
                ns.die('ACTIVE_PACKET_AMBIGUOUS: proven attempt lacks packet and contract identity')
        elif status == 'human_attested':
            if (not isinstance(binding.get('packet_revision'), str) or
                    not re.fullmatch(r'sha256:[0-9a-f]{64}', binding['packet_revision']) or
                    not isinstance(binding.get('contract_sha256'), str) or
                    not re.fullmatch(r'[0-9a-f]{64}', binding['contract_sha256']) or
                    not isinstance(binding.get('identity_bridge_id'), str) or
                    not re.fullmatch(r'sha256:[0-9a-f]{64}', binding['identity_bridge_id']) or
                    binding.get('classification') != 'HUMAN_ATTESTED'):
                ns.die('ACTIVE_PACKET_AMBIGUOUS: human-attested attempt lacks explicit bridge evidence')
        elif status == 'ambiguous':
            if ('packet_revision' in binding or 'contract_sha256' in binding or
                    not isinstance(binding.get('observed_active_packet_revision'), str) or
                    not re.fullmatch(r'sha256:[0-9a-f]{64}', binding['observed_active_packet_revision']) or
                    not isinstance(binding.get('observed_contract_sha256'), str) or
                    not re.fullmatch(r'[0-9a-f]{64}', binding['observed_contract_sha256']) or
                    not isinstance(binding.get('evidence'), str) or not binding['evidence'].strip()):
                ns.die('ACTIVE_PACKET_AMBIGUOUS: ambiguous attempt binding fabricates or omits identity evidence')
        else:
            ns.die('ACTIVE_PACKET_AMBIGUOUS: attempt binding status is unknown')


def append_attempt_binding(entry: dict[str, Any], feature_dir: pathlib.Path, doc: dict[str, Any],
                           task_id: str, packet_path: pathlib.Path, *, ns: Any) -> None:
    packet = json.loads(packet_path.read_text(encoding='utf-8'))
    revision_id = ns.packet_revision_id(packet)
    attempt = int(entry.get('attempts', 0))
    history = entry.setdefault('attempt_bindings', [])
    if any(isinstance(item, dict) and item.get('attempt') == attempt for item in history):
        ns.die('ACTIVE_PACKET_AMBIGUOUS: attempt already has a packet binding')
    history.append({'attempt': attempt, 'packet_revision': revision_id,
                    'contract_sha256': packet.get('semantic_contract_sha256') or
                    ns.semantic_task_contract_sha256(feature_dir, doc, ns.task_index(doc)[task_id]),
                    'binding_status': 'proven', 'bound_at': ns.utc_now().isoformat()})


def active_task_contract(feature_dir: pathlib.Path, doc: dict[str, Any], task_id: str,
                         *, state: dict[str, Any] | None = None, ns: Any) -> dict[str, Any]:
    active = ns.resolve_active_packet(feature_dir, doc, task_id, state=state)
    if active.get('legacy'):
        return ns.task_index(doc)[task_id]
    packet = active['packet']
    return {
        'id': task_id, 'title': packet['title'], 'objective': packet['objective'],
        'role': packet['role'], 'agent_profile': packet.get('agent_profile', packet['role']),
        'depends_on': packet.get('depends_on', []), 'allowed_paths': packet.get('allowed_paths', []),
        'acceptance_criteria': packet.get('acceptance_criteria', []),
        'risk_tags': packet.get('risk_tags', []), 'verification': packet.get('verification', []),
        'test_mode': packet.get('test_mode'), 'test_seam': packet.get('test_seam'),
    }


def packet_matches_semantic_contract(packet: dict[str, Any], doc: dict[str, Any],
                                     task: dict[str, Any], feature_dir: pathlib.Path, *, ns: Any) -> bool:
    """Validate packet integrity and execution identity, ignoring generation provenance."""
    stored_hash = packet.get('packet_sha256')
    body = {key: value for key, value in packet.items() if key != 'packet_sha256'}
    if (not isinstance(stored_hash, str) or
            stored_hash != ns.sha256_bytes(json.dumps(body, sort_keys=True, separators=(',', ':')).encode())):
        return False
    packet_task = {field: packet[field] for field in ns.TASK_CONTRACT_FIELDS
                   if field in task and field in packet}
    if 'id' in task and 'id' not in packet_task and isinstance(packet.get('task'), str):
        packet_task['id'] = packet['task']
    # Older immutable packets predate semantic_contract_sha256. Their task projection is
    # sufficient when bound to the unchanged feature contract fingerprint.
    semantic = ns.semantic_task_contract(
        feature_dir, doc, packet_task,
        feature_sha256=packet.get('feature_fingerprint'),
        test_policy=packet.get('test_policy', 'legacy'))
    packet_contract_hash = ns.sha256_bytes(json.dumps(semantic, sort_keys=True, separators=(',', ':')).encode())
    current_contract_hash = ns.semantic_task_contract_sha256(feature_dir, doc, task)
    if packet.get('semantic_contract_sha256') not in (None, packet_contract_hash):
        return False
    current = ns.packet_payload(doc, task, feature_dir)
    provenance_only = {'packet_sha256', 'protocol_fingerprint', 'semantic_contract_sha256'}
    existing_contract = {key: value for key, value in packet.items() if key not in provenance_only}
    current_contract = {key: value for key, value in current.items() if key not in provenance_only}
    return packet_contract_hash == current_contract_hash and existing_contract == current_contract


def validate_evidence(path: pathlib.Path, *, require_pass: bool = False, ns: Any) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f'evidence file does not exist: {path}']
    if path.suffix != '.json':
        return ['completion evidence must be structured JSON produced by a runner or explicit verifier']
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return [f'invalid evidence JSON: {exc}']
    if not isinstance(doc, dict):
        return ['evidence JSON must be an object']
    required = ('status', 'summary', 'changed_paths', 'commands', 'assumptions', 'residual_risks')
    for field in required:
        if field not in doc:
            errors.append(f'evidence JSON missing {field}')
    if doc.get('status') not in {'pass', 'fail', 'needs-human'}:
        errors.append('evidence.status must be pass, fail, or needs-human')
    if require_pass and doc.get('status') != 'pass':
        errors.append('completion evidence must have status=pass')
    if 'summary' in doc and (not isinstance(doc['summary'], str) or not doc['summary'].strip()):
        errors.append('evidence.summary must be a non-empty string')
    for field in ('changed_paths', 'commands', 'assumptions', 'residual_risks', 'findings', 'rework_tasks'):
        if field in doc:
            if not isinstance(doc[field], list):
                errors.append(f'evidence.{field} must be an array')
            elif any(not isinstance(item, str) for item in doc[field]):
                errors.append(f'evidence.{field} must contain only strings')
    return errors


def required_completion_criteria(task: dict[str, Any], *, ns: Any) -> set[str]:
    acceptance = task.get('acceptance_criteria')
    if (not isinstance(acceptance, list) or not acceptance or
            any(not isinstance(value, str) or re.fullmatch(r'(?:AC|VC)-[A-Z0-9._-]+', value) is None
                for value in acceptance)):
        return set()
    return set(acceptance)


def completion_verification_artifact(feature_dir: pathlib.Path, value: Any, *, ns: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError('verification output path must be a non-empty string')
    candidate = pathlib.Path(value)
    if not candidate.is_absolute():
        candidate = ns.feature_repo_base(feature_dir) / candidate
    root = ns.feature_repo_base(feature_dir).resolve()
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f'verification output cannot be safely resolved: {exc}') from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError('verification output must remain inside the canonical repository') from exc
    if not resolved.is_file():
        raise ValueError('verification output must be a regular file')
    return ns.sha256_bytes(resolved.read_bytes())


def validate_completion_proofs(evidence: dict[str, Any], task: dict[str, Any],
                               required: set[str], feature_dir: pathlib.Path, *, ns: Any) -> list[str]:
    errors: list[str] = []
    commands = evidence.get('commands')
    proofs = evidence.get('proofs')
    results = evidence.get('criterion_results')
    verification_results = evidence.get('harness_verification')
    if type(evidence.get('schema_version')) is not int or evidence.get('schema_version') != ns.COMPLETION_EVIDENCE_SCHEMA_VERSION:
        errors.append('completion evidence schema_version must be supported version 1')
    if not required:
        errors.append('active packet must declare at least one well-formed AC/VC completion criterion')
    if not isinstance(commands, list) or not commands or any(not isinstance(x, str) or not x.strip() for x in commands):
        errors.append('completion evidence commands must be non-empty command strings')
        commands = []
    allowed_commands = task.get('verification', [])
    if any(command not in allowed_commands for command in commands):
        errors.append('completion evidence contains a command not declared by the active packet')
    if commands != allowed_commands:
        errors.append('completion evidence must include the active packet verification command sequence')
    if not isinstance(verification_results, list) or len(verification_results) != len(commands):
        errors.append('completion evidence must include a harness result for every verification command')
        verification_results = []
    verified_results: dict[int, dict[str, Any]] = {}
    for index, record in enumerate(verification_results):
        if (not isinstance(record, dict) or record.get('command') != (commands[index] if index < len(commands) else None) or
                type(record.get('exit_code')) is not int or record.get('exit_code') != 0):
            errors.append(f'verification command {index} has no successful harness execution record')
            continue
        try:
            stdout_sha = ns.completion_verification_artifact(feature_dir, record.get('stdout'))
            stderr_sha = ns.completion_verification_artifact(feature_dir, record.get('stderr'))
        except (OSError, ValueError) as exc:
            errors.append(f'verification command {index} output evidence is invalid: {exc}')
            continue
        if not isinstance(record.get('sandbox_backend'), str) or not record['sandbox_backend']:
            errors.append(f'verification command {index} is missing sandbox provenance')
            continue
        verified_results[index] = {
            'command': record['command'], 'exit_code': record['exit_code'],
            'stdout_sha256': stdout_sha, 'stderr_sha256': stderr_sha,
            'sandbox_backend': record['sandbox_backend'],
            'strong_isolation': record.get('strong_isolation') is True,
        }
    if not isinstance(proofs, list) or not proofs:
        errors.append('completion evidence proofs must contain command-linked results')
        proofs = []
    proof_index: dict[str, dict[str, Any]] = {}
    for proof in proofs:
        if not isinstance(proof, dict) or set(proof) != {
                'proof_id', 'command_index', 'command_sha256', 'status', 'exit_code', 'result_sha256', 'criteria'}:
            errors.append('completion proof record has an invalid shape')
            continue
        proof_id = proof.get('proof_id')
        index = proof.get('command_index')
        criteria = proof.get('criteria')
        if (not isinstance(proof_id, str) or not proof_id or proof_id in proof_index or
                type(index) is not int or index < 0 or index >= len(commands)):
            errors.append('completion proof identity or command index is invalid')
            continue
        if (proof.get('status') != 'PASS' or type(proof.get('exit_code')) is not int or proof['exit_code'] != 0 or
                not isinstance(proof.get('command_sha256'), str) or
                proof.get('command_sha256') != ns.sha256_bytes(commands[index].encode()) or
                not isinstance(proof.get('result_sha256'), str) or
                re.fullmatch(r'[0-9a-f]{64}', proof['result_sha256']) is None):
            errors.append(f'completion proof {proof_id} is not a valid successful command receipt')
        receipt = verified_results.get(index)
        if (receipt is None or proof.get('result_sha256') != ns.canonical_json_sha256(receipt)):
            errors.append(f'completion proof {proof_id} does not match durable harness command-output evidence')
        if (not isinstance(criteria, list) or not criteria or
                any(not isinstance(item, str) for item in criteria) or
                (all(isinstance(item, str) for item in criteria) and
                 (len(criteria) != len(set(criteria)) or any(item not in required for item in criteria)))):
            errors.append(f'completion proof {proof_id} has invalid criterion links')
        proof_index[proof_id] = proof
    if not isinstance(results, dict):
        errors.append('completion evidence criterion_results must be an object')
        results = {}
    if set(results) != required:
        missing = sorted(required - set(results))
        extra = sorted(set(results) - required)
        if missing:
            errors.append(f'completion evidence missing required criteria: {missing}')
        if extra:
            errors.append(f'completion evidence contains criteria outside the active packet: {extra}')
    for criterion in required & set(results):
        result = results[criterion]
        if not isinstance(result, dict) or set(result) != {'status', 'proof_ids'} or result.get('status') != 'PASS':
            errors.append(f'{criterion} is not marked PASS with linked command proof')
            continue
        proof_ids = result.get('proof_ids')
        if (not isinstance(proof_ids, list) or not proof_ids or
                any(not isinstance(proof_id, str) for proof_id in proof_ids) or
                (all(isinstance(proof_id, str) for proof_id in proof_ids) and
                 (len(proof_ids) != len(set(proof_ids)) or
                  any(proof_id not in proof_index or criterion not in proof_index[proof_id].get('criteria', [])
                      for proof_id in proof_ids)))):
            errors.append(f'{criterion} has no valid passing command proof')
    return errors


def cmd_ready(args: argparse.Namespace, *, ns: Any) -> None:
    doc = ns.load_validated(args.feature_dir)
    state = ns._read_state_unlocked_pure(args.feature_dir, doc)
    ids = ns.ready_ids(doc, state, args.feature_dir)
    if args.json:
        print(json.dumps(ids))
    else:
        print('\n'.join(ids) if ids else 'NO_READY_TASKS')


def cmd_claim(args: argparse.Namespace, *, ns: Any) -> None:
    doc = ns.load_validated(args.feature_dir)
    with ns.locked_state(args.feature_dir, doc) as state:
        entry = state['tasks'].get(args.task_id)
        if (entry and entry.get('status') == 'failed' and
                int(entry.get('attempts', 0)) >= 1 + int(doc.get('max_rework_attempts', 2)) and
                int(entry.get('human_resume_grants', 0)) <= 0):
            classification, grant = ns.classify_retry_authorizations(
                args.feature_dir, doc, state, args.task_id, int(entry.get('attempts', 0)))
            if classification != 'valid':
                if classification == 'legacy-unverifiable' and grant:
                    ns.die(f'RETRY_AUTHORIZATION_LEGACY_UNVERIFIABLE: use --supersedes {grant["id"]}')
                ns.die(f'RETRY_AUTHORIZATION_REQUIRED: {args.task_id} has no valid retry authorization ({classification})')
        if args.task_id not in ns.ready_ids(doc, state, args.feature_dir):
            ns.die(f'{args.task_id} is not ready')
        # Packet validation/publication is a precondition, not part of the claim commit.
        # If it fails, locked_state's exception-saving behavior persists an unchanged state.
        active_packet = ns.write_packet(doc, ns.task_index(doc)[args.task_id], args.feature_dir, state=state)
        staged_state = copy.deepcopy(state)
        entry = staged_state['tasks'][args.task_id]
        ns.consume_attempt_authorization(entry, args.task_id, doc, args.feature_dir, staged_state)
        now = ns.utc_now()
        entry.update({
            'status': 'running',
            'owner': args.owner,
            'attempts': int(entry.get('attempts', 0)) + 1,
            'claimed_at': now.isoformat(),
        })
        ns.append_attempt_binding(entry, args.feature_dir, doc, args.task_id, active_packet)
        ns.refresh_lease(entry, doc, now=now)
        # This single in-memory publication is the final operation in the body. The durable
        # claim commit is locked_state's atomic state-file replacement; all preparation ran
        # against a detached copy, so exceptions before publication preserve the old state.
        state['tasks'][args.task_id] = entry
    try:
        print(f'CLAIMED {args.task_id} by {args.owner}')
    except OSError:
        # Lifecycle commit already succeeded. Do not turn an output-channel failure into
        # a non-zero claim result that falsely suggests the task was left unclaimed.
        return


def owner_guard(entry: dict[str, Any], owner: str, *, ns: Any) -> None:
    if entry.get('owner') and entry.get('owner') != owner:
        ns.die(f'task is owned by {entry.get("owner")}, not {owner}')


def consume_attempt_authorization(entry: dict[str, Any], task_id: str, doc: dict[str, Any], feature_dir: pathlib.Path | None = None, state: dict[str, Any] | None = None, *, ns: Any) -> str | None:
    """Consume normal retry budget or one explicit human resume grant."""
    max_attempts = 1 + int(doc.get('max_rework_attempts', 2))
    attempts = int(entry.get('attempts', 0))
    grants = int(entry.get('human_resume_grants', 0))
    if attempts >= max_attempts:
        if entry.get('status') != 'failed':
            ns.die(f'RETRY_AUTHORIZATION_INVALID: {task_id} must be failed before exceptional claim')
        if grants > 0:
            entry['human_resume_grants'] = grants - 1
            resolution = str(entry.get('human_resolution') or '') or None
            entry['active_human_resume'] = resolution or 'human-resolution'
            return resolution
        if feature_dir is None or state is None:
            ns.die(f'ATTEMPTS_EXHAUSTED: {task_id} exhausted its {max_attempts} allowed attempts')
        classification, authorization = ns.classify_retry_authorizations(feature_dir, doc, state, task_id, attempts)
        if authorization is None or classification != 'valid':
            if classification == 'legacy-unverifiable':
                ns.die(f'RETRY_AUTHORIZATION_LEGACY_UNVERIFIABLE: {task_id} requires explicit --supersedes {authorization["id"]}')
            ns.die(f'RETRY_AUTHORIZATION_REQUIRED: ATTEMPTS_EXHAUSTED: {task_id} exhausted its {max_attempts} allowed attempts ({classification})')
        authorization['consumed_at'] = ns.utc_now().isoformat()
        authorization['consumed_attempt'] = attempts + 1
        entry['active_retry_authorization'] = authorization['id']
        return str(authorization['id'])
    if grants <= 0:
        return None
    if grants > 0:
        entry['human_resume_grants'] = grants - 1
        resolution = str(entry.get('human_resolution') or '') or None
        entry['active_human_resume'] = resolution or 'human-resolution'
        return resolution
    return None


def retry_binding(feature_dir: pathlib.Path, doc: dict[str, Any], task_id: str, attempts: int,
                  *, state: dict[str, Any] | None = None, ns: Any) -> dict[str, Any]:
    active = ns.resolve_active_packet(feature_dir, doc, task_id, state=state)
    binding = {
        'binding_version': 2,
        'repository': str(ns.git_common_dir(feature_dir)),
        'feature': str(doc.get('feature', feature_dir.name)),
        'task': task_id,
        'expected_status': 'failed',
        'expected_attempts': attempts,
        'contract_sha256': active['contract_sha256'],
        'protocol_version': ns.protocol_version(feature_dir),
    }
    # Revision binding is introduced lazily. Historical v2 grants against a legacy
    # packet retain their semantic V2 interpretation; grants after replan bind both.
    if not active.get('legacy'):
        binding['packet_revision'] = active['revision_id']
    return binding


def packet_bound_semantic_contract_sha256(feature_dir: pathlib.Path,
                                          packet: dict[str, Any], *, ns: Any) -> str:
    """Reconstruct a materialized packet contract without consulting current planning.

    Packet payloads persist ``agent_profile`` as an execution profile, defaulting it
    to ``role`` when the planning task omits the field. That fallback is packet
    metadata, not a task-contract field. For legacy packets without a semantic hash,
    normalize that default away. Newer packets carry a semantic hash, so recompute
    both shape-valid interpretations and require the embedded hash to match one.
    This preserves explicitly supplied profiles while still validating the hash
    from packet-bound material.
    """
    feature = packet.get('feature')
    task_id = packet.get('task')
    feature_sha256 = packet.get('feature_fingerprint')
    test_policy = packet.get('test_policy', 'legacy')
    if (not isinstance(feature, str) or not feature or not isinstance(task_id, str) or
            not ns.safe_task_id(task_id) or not isinstance(feature_sha256, str) or
            re.fullmatch(r'[0-9a-f]{64}', feature_sha256) is None or
            not isinstance(test_policy, str) or not test_policy):
        ns.die('ACTIVE_PACKET_AMBIGUOUS: packet lacks immutable semantic contract inputs')
    task = {field: packet[field] for field in ns.TASK_CONTRACT_FIELDS if field in packet}
    if 'id' not in task:
        task['id'] = task_id
    explicit = dict(task)
    if task.get('agent_profile') == packet.get('role'):
        task.pop('agent_profile')
    def fingerprint_for(contract_task: dict[str, Any]) -> str:
        semantic = ns.semantic_task_contract(
            feature_dir, {'feature': feature}, contract_task,
            feature_sha256=feature_sha256, test_policy=test_policy)
        return ns.sha256_bytes(json.dumps(semantic, sort_keys=True, separators=(',', ':')).encode())

    fingerprint = fingerprint_for(task)
    stored = packet.get('semantic_contract_sha256')
    if stored is not None:
        candidates = {fingerprint, fingerprint_for(explicit)}
        if not isinstance(stored, str) or stored not in candidates:
            ns.die('ACTIVE_PACKET_AMBIGUOUS: packet semantic contract fingerprint is inconsistent')
        fingerprint = stored
    return fingerprint


def semantic_task_contract(feature_dir: pathlib.Path, doc: dict[str, Any], task: dict[str, Any], *,
                           feature_sha256: str | None = None,
                           test_policy: str | None = None, ns: Any) -> dict[str, Any]:
    """Canonical execution contract shared by retry authorization and packet identity."""
    return {
        'feature': doc.get('feature', feature_dir.name),
        'feature_contract_sha256': feature_sha256 or ns.feature_fingerprint(feature_dir),
        'task_contract': {key: task[key] for key in ns.TASK_CONTRACT_FIELDS if key in task},
        'test_policy': test_policy if test_policy is not None else doc.get('test_policy', 'legacy'),
    }


def semantic_task_contract_sha256(feature_dir: pathlib.Path, doc: dict[str, Any], task: dict[str, Any], *,
                                 feature_sha256: str | None = None,
                                 test_policy: str | None = None, ns: Any) -> str:
    semantic = ns.semantic_task_contract(feature_dir, doc, task, feature_sha256=feature_sha256,
                                      test_policy=test_policy)
    return ns.sha256_bytes(json.dumps(semantic, sort_keys=True, separators=(',', ':')).encode())


def classify_retry_authorizations(feature_dir: pathlib.Path, doc: dict[str, Any], state: dict[str, Any], task_id: str, attempts: int, *, ns: Any) -> tuple[str, dict[str, Any] | None]:
    entry = state['tasks'][task_id]
    grants = entry.get('retry_authorizations', [])
    if not isinstance(grants, list):
        ns.die('RETRY_AUTHORIZATION_INVALID: authorization ledger is malformed')
    expected = ns.retry_binding(feature_dir, doc, task_id, attempts, state=state)
    if entry.get('status') != 'failed':
        ns.die('RETRY_AUTHORIZATION_INVALID: task is not failed')
    relations = entry.get('retry_authorization_supersessions', [])
    if not isinstance(relations, list):
        ns.die('RETRY_AUTHORIZATION_INVALID: supersession ledger is malformed')
    superseded_ids: set[str] = set()
    supersession_target: dict[str, str] = {}
    for relation in relations:
        if (not isinstance(relation, dict) or not isinstance(relation.get('supersedes'), str) or
                not isinstance(relation.get('authorization_id'), str) or
                not isinstance(relation.get('reason'), str) or not relation['reason'].strip() or
                not isinstance(relation.get('provenance'), str) or not relation['provenance'] or
                ns.parse_timestamp(relation.get('issued_at')) is None):
            ns.die('RETRY_AUTHORIZATION_INVALID: malformed supersession relation')
        if relation['supersedes'] in superseded_ids:
            ns.die('RETRY_AUTHORIZATION_INVALID: authorization superseded more than once')
        if relation['supersedes'] == relation['authorization_id']:
            ns.die('RETRY_AUTHORIZATION_INVALID: self-supersession is not allowed')
        superseded_ids.add(relation['supersedes'])
        supersession_target[relation['supersedes']] = relation['authorization_id']
    valid: list[dict[str, Any]] = []
    legacy: list[dict[str, Any]] = []
    consumed: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for grant in grants:
        required = {'version', 'id', 'binding', 'reason', 'provenance', 'issued_at', 'consumed_at'}
        if not isinstance(grant, dict) or not required.issubset(grant) or grant.get('version') not in {1, 2} or not isinstance(grant.get('binding'), dict):
            ns.die('RETRY_AUTHORIZATION_INVALID: malformed or unknown authorization')
        if (not isinstance(grant.get('id'), str) or not grant['id'] or
                not isinstance(grant.get('reason'), str) or not grant['reason'].strip() or
                not isinstance(grant.get('provenance'), str) or not grant['provenance'] or
                ns.parse_timestamp(grant.get('issued_at')) is None or
                (grant.get('consumed_at') is not None and ns.parse_timestamp(grant.get('consumed_at')) is None)):
            ns.die('RETRY_AUTHORIZATION_INVALID: malformed audit fields')
        consumed_attempt = grant.get('consumed_attempt')
        if grant.get('consumed_at') is not None and (not isinstance(consumed_attempt, int) or isinstance(consumed_attempt, bool) or consumed_attempt < 1):
            ns.die('RETRY_AUTHORIZATION_INVALID: consumed authorization has no valid consumed_attempt')
        if grant['id'] in seen_ids:
            ns.die('RETRY_AUTHORIZATION_INVALID: duplicate authorization ID')
        seen_ids.add(grant['id'])
        binding = grant['binding']
        binding_version = binding.get('binding_version', 1)
        if not ((binding_version == 2 and grant.get('version') == 2) or
                (binding_version == 1 and grant.get('version') == 1)):
            ns.die('RETRY_AUTHORIZATION_INVALID: unknown authorization binding version')
        if binding_version == 2 and grant.get('version') == 2:
            required_binding = {'binding_version', 'repository', 'feature', 'task', 'expected_status',
                                'expected_attempts', 'contract_sha256', 'protocol_version'}
            allowed_binding = required_binding | {'packet_revision'}
            if (not required_binding.issubset(binding) or set(binding) - allowed_binding or
                    ('packet_revision' in binding and not re.fullmatch(r'sha256:[0-9a-f]{64}', str(binding['packet_revision']))) or
                    not isinstance(binding.get('contract_sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', binding['contract_sha256'])):
                ns.die('RETRY_AUTHORIZATION_INVALID: malformed v2 semantic binding')
        elif binding_version == 1 and grant.get('version') == 1:
            required_binding = {'repository', 'feature', 'task', 'expected_status', 'expected_attempts',
                                'feature_fingerprint', 'packet_sha256', 'protocol_version'}
            if (set(binding) != required_binding or not isinstance(binding.get('packet_sha256'), str) or
                    not re.fullmatch(r'[0-9a-f]{64}', binding['packet_sha256'])):
                ns.die('RETRY_AUTHORIZATION_INVALID: malformed v1 packet binding')
        if grant['id'] in superseded_ids:
            continue
        if grant.get('consumed_at') is not None:
            consumed.append(grant)
            continue
        if binding_version == 2 and grant.get('version') == 2:
            if binding == expected:
                valid.append(grant)
            else:
                stale.append(grant)
        elif binding_version == 1 and grant.get('version') == 1:
            same_identity = all(binding.get(key) == expected.get(key) for key in ('repository', 'feature', 'task'))
            if (same_identity and binding.get('expected_status') == 'failed' and
                    binding.get('expected_attempts') == attempts and
                    binding.get('protocol_version') == expected['protocol_version']):
                legacy.append(grant)
            else:
                stale.append(grant)
        else:
            ns.die('RETRY_AUTHORIZATION_INVALID: unknown authorization binding version')
    grants_by_id = {grant['id']: grant for grant in grants if isinstance(grant, dict) and isinstance(grant.get('id'), str)}
    for old_id, new_id in supersession_target.items():
        old = grants_by_id.get(old_id)
        new = grants_by_id.get(new_id)
        if (old is None or new is None or old.get('version') != 1 or old.get('consumed_at') is not None or
                new.get('version') != 2 or new.get('supersedes') != old_id or
                new.get('binding', {}).get('repository') != expected['repository'] or
                new.get('binding', {}).get('feature') != expected['feature'] or
                new.get('binding', {}).get('task') != task_id or
                new.get('binding', {}).get('expected_status') != 'failed' or
                not isinstance(old.get('binding'), dict) or
                old.get('binding', {}).get('expected_attempts') !=
                    new.get('binding', {}).get('expected_attempts') or
                not isinstance(new.get('binding', {}).get('expected_attempts'), int) or
                isinstance(new.get('binding', {}).get('expected_attempts'), bool) or
                new.get('binding', {}).get('expected_attempts') < 1):
            ns.die('RETRY_AUTHORIZATION_INVALID: supersession relation does not match immutable grant history')
    if len(valid) > 1 or len(legacy) > 1 or (valid and legacy):
        ns.die('RETRY_AUTHORIZATION_INVALID: conflicting eligible authorization history')
    if valid:
        return 'valid', valid[0]
    if legacy:
        return 'legacy-unverifiable', legacy[0]
    if consumed:
        return 'consumed', consumed[0]
    if stale:
        return 'stale', stale[0]
    if superseded_ids:
        return 'superseded', None
    return 'absent', None


def replan_failpoint(_stage: str, *, ns: Any) -> None:
    """Test seam for durable transaction failure injection."""


def validate_replan_task(feature_dir: pathlib.Path, doc: dict[str, Any], task_id: str,
                         proposed: Any, *, ns: Any) -> dict[str, Any]:
    if not ns.safe_task_id(task_id) or not isinstance(proposed, dict) or proposed.get('id') != task_id:
        ns.die('TASK_REPLAN_NOT_ALLOWED: proposed task identity must match the target task')
    required = {'id', 'title', 'objective', 'role', 'depends_on', 'allowed_paths',
                'risk_tags', 'acceptance_criteria', 'verification'}
    if not required.issubset(proposed):
        ns.die('TASK_REPLAN_NOT_ALLOWED: proposed task is missing required planning fields')
    if (not isinstance(proposed['title'], str) or not proposed['title'].strip() or
            not isinstance(proposed['objective'], str) or not proposed['objective'].strip()):
        ns.die('TASK_REPLAN_NOT_ALLOWED: title and objective must be non-empty')
    if proposed.get('role') not in ns.VALID_ROLES:
        ns.die('TASK_REPLAN_NOT_ALLOWED: invalid task role')
    for field in ('depends_on', 'allowed_paths', 'risk_tags', 'acceptance_criteria', 'verification'):
        if not isinstance(proposed.get(field), list):
            ns.die(f'TASK_REPLAN_NOT_ALLOWED: {field} must be an array')
    if any(not isinstance(dep, str) or dep not in ns.task_index(doc) or dep == task_id for dep in proposed['depends_on']):
        ns.die('TASK_REPLAN_NOT_ALLOWED: dependencies must identify other planned tasks')
    for pattern in proposed['allowed_paths']:
        if (not isinstance(pattern, str) or not ns.safe_relative_pattern(pattern) or
                (proposed['role'] == 'builder' and not ns.builder_pattern_has_concrete_root(pattern))):
            ns.die('TASK_REPLAN_NOT_ALLOWED: proposed allowed_paths contains an unsafe path')
    if not proposed['acceptance_criteria'] or any(not isinstance(c, str) for c in proposed['acceptance_criteria']):
        ns.die('TASK_REPLAN_NOT_ALLOWED: acceptance criteria must be non-empty strings')
    accepted = set(ns.AC_RE.findall((feature_dir / 'spec.md').read_text(encoding='utf-8')))
    accepted |= ns.vc.criterion_ids(feature_dir)
    if any(c not in accepted for c in proposed['acceptance_criteria']):
        ns.die('TASK_REPLAN_NOT_ALLOWED: criteria must exist in the accepted spec or verification contract')
    if any(not isinstance(item, str) or not item.strip() for item in proposed['verification']):
        ns.die('TASK_REPLAN_NOT_ALLOWED: verification entries must be non-empty strings')
    if any(not isinstance(item, str) or not item.strip() for item in proposed['risk_tags']):
        ns.die('TASK_REPLAN_NOT_ALLOWED: risk tags must be non-empty strings')
    if 'agent_profile' in proposed and (not isinstance(proposed['agent_profile'], str) or not ns.PROFILE_RE.fullmatch(proposed['agent_profile'])):
        ns.die('TASK_REPLAN_NOT_ALLOWED: invalid agent profile')
    if set(proposed) - set(ns.TASK_CONTRACT_FIELDS):
        ns.die('TASK_REPLAN_NOT_ALLOWED: proposed task contains fields outside the accepted task model')
    replacement = copy.deepcopy(doc)
    replacement['tasks'] = [proposed if task.get('id') == task_id else task for task in doc['tasks']]
    graph = ns.task_index(replacement)
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(tid: str) -> None:
        if tid in visiting:
            ns.die('TASK_REPLAN_NOT_ALLOWED: proposed dependencies introduce a cycle')
        if tid in visited:
            return
        visiting.add(tid)
        for dep in graph[tid].get('depends_on', []):
            visit(dep)
        visiting.remove(tid)
        visited.add(tid)
    for tid in graph:
        visit(tid)
    builders = [task for task in graph.values() if task.get('role') == 'builder']
    evaluators = [task for task in graph.values() if task.get('role') == 'evaluator']
    if builders and not evaluators:
        ns.die('TASK_REPLAN_NOT_ALLOWED: builder tasks require an evaluator in the plan')
    for builder in builders:
        if not any(builder['id'] in evaluator.get('depends_on', []) for evaluator in evaluators):
            ns.die('TASK_REPLAN_NOT_ALLOWED: every builder must be directly covered by an evaluator dependency')
    if evaluators:
        evaluator_criteria = set().union(*(set(task.get('acceptance_criteria', [])) for task in evaluators))
        if not accepted.issubset(evaluator_criteria):
            ns.die('TASK_REPLAN_NOT_ALLOWED: evaluators must cover every accepted criterion')
    integrations = [task for task in graph.values() if task.get('role') == 'integration']
    evaluator_ids = {task['id'] for task in evaluators}
    if any(not evaluator_ids.intersection(task.get('depends_on', [])) for task in integrations):
        ns.die('TASK_REPLAN_NOT_ALLOWED: integrations must depend on an evaluator')
    if proposed['role'] == 'builder' and doc.get('test_policy', 'legacy') == 'risk-driven':
        if proposed.get('test_mode') not in ns.VALID_TEST_MODES or not isinstance(proposed.get('test_seam'), str) or not proposed['test_seam'].strip():
            ns.die('TASK_REPLAN_NOT_ALLOWED: risk-driven builders require a valid test mode and test seam')
    profile = proposed.get('agent_profile') or proposed['role']
    if not (ns.feature_repo_base(feature_dir) / 'docs/agentic-sdd/agents' / f'{profile}.md').is_file():
        ns.die('TASK_REPLAN_NOT_ALLOWED: proposed role profile does not exist')
    return proposed


def publish_revision(feature_dir: pathlib.Path, task_id: str,
                     packet: dict[str, Any],
                     staged_files: dict[str, tuple[bytes, int]], *, ns: Any) -> tuple[str, pathlib.Path]:
    revision_id = ns.packet_revision_id(packet)
    path = ns.revision_path(feature_dir, task_id, revision_id)
    authority_root = ns.runtime_state_dir(feature_dir) / 'packet-revisions'
    feature_root = authority_root / feature_dir.name
    task_root = path.parent
    if task_root.is_symlink() or feature_root.is_symlink() or (authority_root.exists() and authority_root.is_symlink()):
        ns.die('ACTIVE_PACKET_AMBIGUOUS: symlink in immutable packet revision path')
    if task_root.exists() and task_root.resolve().parent != feature_root.resolve():
        ns.die('ACTIVE_PACKET_AMBIGUOUS: packet revision directory escapes repository authority')
    encoded = (json.dumps(packet, indent=2, sort_keys=True) + '\n').encode('utf-8')
    if path.exists():
        if path.read_bytes() != encoded:
            ns.die('PACKET_SUPERSESSION_CONFLICT: revision identity collision')
        return revision_id, path
    ns.replan_failpoint('R2')
    ns.replan_failpoint('R3')
    try:
        relative = path.resolve(strict=False).relative_to(ns.runtime_state_dir(feature_dir).resolve()).as_posix()
    except ValueError:
        ns.die('ACTIVE_PACKET_AMBIGUOUS: packet revision path escapes repository authority')
    if relative in staged_files and staged_files[relative] != (encoded, 0o600):
        ns.die('PACKET_SUPERSESSION_CONFLICT: staged revision identity collision')
    staged_files[relative] = (encoded, 0o600)
    return revision_id, path


def validate_latest_human_resolution(feature_dir: pathlib.Path, doc: dict[str, Any],
                                     task_id: str, entry: dict[str, Any], *, ns: Any) -> dict[str, Any]:
    """Require the current failed state to be the exact audited human-resolution transition."""
    attempts = entry.get('attempts')
    history = entry.get('human_resolution_history')
    artifact_value = entry.get('human_resolution')
    transition = entry.get('last_transition')
    if (not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0 or
            not isinstance(history, list) or not history or not isinstance(history[-1], dict) or
            not isinstance(artifact_value, str) or
            (transition is not None and (not isinstance(transition, dict) or
             transition.get('kind') != 'human_resolution' or
             type(transition.get('attempts')) is not int or transition.get('attempts') != attempts or
             transition.get('artifact') != artifact_value))):
        ns.die('TASK_REPLAN_NOT_ALLOWED: failed task was not most recently changed by a human resolution')
    if transition is None:
        # Upgrade-safe proof for human resolutions written before last_transition existed.
        # Later supported mutations create a retry grant or consume the human grant.
        if (type(entry.get('human_resume_grants')) is not int or entry['human_resume_grants'] < 1 or
                entry.get('active_human_resume') is not None or
                entry.get('active_retry_authorization') is not None):
            ns.die('TASK_REPLAN_NOT_ALLOWED: legacy human resolution is not the latest provable failed-task transition')
    root = (feature_dir / 'evidence' / 'human-resolutions' / task_id).resolve()
    artifact = pathlib.Path(artifact_value)
    if not artifact.is_absolute():
        artifact = ns.feature_repo_base(feature_dir) / artifact
    expected_root = feature_dir / 'evidence' / 'human-resolutions' / task_id
    if any(path.is_symlink() for path in (
            feature_dir / 'evidence', feature_dir / 'evidence' / 'human-resolutions', expected_root)):
        ns.die('TASK_REPLAN_NOT_ALLOWED: human-resolution audit authority path contains a symlink')
    try:
        resolved = artifact.resolve(strict=True)
        resolved.relative_to(root)
        if artifact.is_symlink() or not resolved.is_file():
            raise ValueError('audit artifact is not a regular file')
        raw = resolved.read_bytes()
        record = json.loads(raw)
    except (OSError, ValueError, json.JSONDecodeError):
        ns.die('TASK_REPLAN_NOT_ALLOWED: human-resolution audit artifact is unavailable or outside its authority path')
    latest = history[-1]
    digest = ns.sha256_bytes(raw)
    expected = {
        'schema_version': 1,
        'feature': doc.get('feature', feature_dir.name),
        'task': task_id,
        'action': 'retry',
        'attempts_before_resolution': attempts,
    }
    if (not isinstance(record, dict) or any(record.get(key) != value for key, value in expected.items()) or
            not isinstance(record.get('decision'), str) or not record['decision'].strip() or
            not isinstance(record.get('decided_by'), str) or not record['decided_by'].strip() or
            ns.parse_timestamp(record.get('resolved_at')) is None or
            ns.parse_timestamp(entry.get('human_resolved_at')) is None or
            record.get('decided_by') != entry.get('human_resolved_by') or
            latest.get('artifact') != artifact_value or type(latest.get('attempts')) is not int or
            latest.get('attempts') != attempts or
            latest.get('resolved_at') != entry.get('human_resolved_at') or
            latest.get('decided_by') != record.get('decided_by') or
            ns.parse_timestamp(record['resolved_at']) > ns.parse_timestamp(latest['resolved_at']) or
            (transition is not None and
             (transition.get('resolved_at') != latest.get('resolved_at') or
              transition.get('artifact_sha256') != digest))):
        ns.die('TASK_REPLAN_NOT_ALLOWED: human-resolution audit binding does not match the current failed task state')
    if transition is None:
        resolved_at = ns.parse_timestamp(latest['resolved_at'])
        for grant in entry.get('retry_authorizations', []):
            issued_at = ns.parse_timestamp(grant.get('issued_at')) if isinstance(grant, dict) else None
            if issued_at is None or issued_at > resolved_at:
                ns.die('TASK_REPLAN_NOT_ALLOWED: a retry authorization changed the task after human resolution')
        for relation in entry.get('retry_authorization_supersessions', []):
            issued_at = ns.parse_timestamp(relation.get('issued_at')) if isinstance(relation, dict) else None
            if issued_at is None or issued_at > resolved_at:
                ns.die('TASK_REPLAN_NOT_ALLOWED: authorization supersession changed the task after human resolution')
        used_at = ns.parse_timestamp(entry.get('last_human_resume_used_at'))
        if used_at is not None and used_at > resolved_at:
            ns.die('TASK_REPLAN_NOT_ALLOWED: human retry authorization was consumed after resolution')
    return {'artifact': artifact_value, 'artifact_sha256': digest, 'attempts': attempts,
            'resolved_at': record['resolved_at']}


def packet_identity_bridge_path(feature_dir: pathlib.Path, task_id: str, attempt: int, *, ns: Any) -> pathlib.Path:
    if not ns.safe_task_id(task_id) or not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        ns.die('PACKET_IDENTITY_ATTESTATION_REJECTED: invalid task or attempt')
    root = ns.runtime_state_dir(feature_dir) / 'packet-identity-bridges'
    feature_root = root / feature_dir.name
    task_root = feature_root / task_id
    path = task_root / f'{attempt}.json'
    if (root.is_symlink() or feature_root.is_symlink() or task_root.is_symlink() or
            path.is_symlink() or path.resolve().parent != task_root.resolve()):
        ns.die('PACKET_IDENTITY_ATTESTATION_REJECTED: bridge path escapes repository authority')
    return path


def _bridge_request_identity(*, repository: str, feature: str, task: str, attempt: int,
                             recovery_id: str, source_scheme: str, source_value: str,
                             target_scheme: str, target_value: str, target_revision: str,
                             semantic_fingerprint: str, protocol_version_value: int,
                             operator: str, reason: str, ns: Any) -> dict[str, Any]:
    return {'repository': repository, 'feature': feature, 'task': task, 'attempt': attempt,
            'recovery_record_identity': {'scheme': ns.RECOVERY_RECORD_IDENTITY_SCHEME, 'value': recovery_id},
            'source_identity': {'scheme': source_scheme, 'value': source_value},
            'target_identity': {'scheme': target_scheme, 'value': target_value},
            'target_revision_identity': {'scheme': ns.PACKET_REVISION_IDENTITY_SCHEME,
                                         'value': target_revision},
            'semantic_contract_sha256': semantic_fingerprint,
            'protocol_version': protocol_version_value,
            'classification': 'HUMAN_ATTESTED', 'operator': operator, 'reason': reason}


def _read_packet_identity_bridge(feature_dir: pathlib.Path, task_id: str, attempt: int,
                                 expected_request: dict[str, Any] | None = None, *, ns: Any) -> dict[str, Any] | None:
    path = ns.packet_identity_bridge_path(feature_dir, task_id, attempt)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        ns.die('PACKET_IDENTITY_ATTESTATION_CONFLICT: stored bridge is unreadable')
    if (not isinstance(record, dict) or record.get('schema_version') != ns.BRIDGE_SCHEMA_VERSION or
            record.get('classification') != 'HUMAN_ATTESTED' or
            record.get('bridge_id') != f"sha256:{ns.canonical_json_sha256(record.get('request'))}" or
            not isinstance(record.get('request'), dict) or
            record.get('protocol_version') != record.get('request', {}).get('protocol_version') or
            ns.parse_timestamp(record.get('created_at')) is None):
        ns.die('PACKET_IDENTITY_ATTESTATION_CONFLICT: stored bridge is malformed')
    if expected_request is not None and record.get('request') != expected_request:
        return record
    return record


def classify_historical_packet_binding(feature_dir: pathlib.Path, doc: dict[str, Any], task_id: str,
                                        state: dict[str, Any], active: dict[str, Any],
                                        attempt: int, *, ns: Any) -> dict[str, Any]:
    entry = state['tasks'][task_id] if isinstance(state.get('tasks'), dict) else state
    ns.validate_attempt_binding_ledger(entry)
    binding = next((item for item in entry.get('attempt_bindings', [])
                   if isinstance(item, dict) and item.get('attempt') == attempt), None)
    if (binding and binding.get('binding_status') == 'proven' and
            binding.get('packet_revision') == active['revision_id'] and
            binding.get('contract_sha256') == active['contract_sha256']):
        return {'classification': 'AUTOMATICALLY_PROVEN', 'bridge_id': None}
    if (binding and binding.get('binding_status') == 'proven' and
            (binding.get('packet_revision') != active['revision_id'] or
             binding.get('contract_sha256') != active['contract_sha256'])):
        return {'classification': 'AMBIGUOUS', 'bridge_id': None}
    recovery = entry.get('claim_recovery')
    if (isinstance(recovery, dict) and recovery.get('attempt') == attempt and
            ns.recovery_proves_packet_binding(recovery, active.get('packet'), active['revision_id'],
                                           active['contract_sha256'])):
        return {'classification': 'AUTOMATICALLY_PROVEN', 'bridge_id': None}
    recovery_id = ns.recovery_record_identity(recovery)
    packet_identity = ns.packet_identity_from_embedded_checksum(active.get('packet'))
    revision_identity = ns.packet_identity_from_revision(active.get('packet'))
    bridge = ns._read_packet_identity_bridge(feature_dir, task_id, attempt)
    if (bridge and isinstance(recovery, dict) and recovery.get('attempt') == attempt and
            packet_identity is not None and revision_identity is not None):
        request = bridge['request']
        expected = ns._bridge_request_identity(
            repository=str(ns.git_common_dir(feature_dir)), feature=doc.get('feature', feature_dir.name),
            task=task_id, attempt=attempt, recovery_id=recovery_id or '',
            source_scheme=(ns.packet_identity_from_recovery(recovery).scheme
                           if ns.packet_identity_from_recovery(recovery) else ''),
            source_value=(ns.packet_identity_from_recovery(recovery).value
                          if ns.packet_identity_from_recovery(recovery) else ''),
            target_scheme=packet_identity.scheme, target_value=packet_identity.value,
            target_revision=revision_identity.value,
            semantic_fingerprint=active['contract_sha256'],
            protocol_version_value=ns.protocol_version(feature_dir),
            operator=request.get('operator', ''), reason=request.get('reason', ''))
        if request == expected:
            if (binding and binding.get('binding_status') == 'human_attested' and
                    binding.get('identity_bridge_id') != bridge['bridge_id']):
                return {'classification': 'AMBIGUOUS', 'bridge_id': None}
            return {'classification': 'HUMAN_ATTESTED', 'bridge_id': bridge['bridge_id']}
    return {'classification': 'AMBIGUOUS', 'bridge_id': None}


def cmd_replan_task(args: argparse.Namespace, *, ns: Any) -> None:
    feature_dir = args.feature_dir.resolve()
    doc = ns.load_validated(feature_dir)
    if args.task_id not in ns.task_index(doc):
        ns.die('TASK_REPLAN_NOT_ALLOWED: unknown task')
    reason = str(args.reason or '').strip()
    operator = str(args.by or '').strip()
    if not reason or not operator:
        ns.die('TASK_REPLAN_NOT_ALLOWED: reason and operator provenance are required')
    source_path = pathlib.Path(args.proposed_task_file)
    try:
        resolved_source = source_path.resolve(strict=True)
        resolved_source.relative_to(feature_dir.resolve())
    except (OSError, ValueError):
        ns.die('TASK_REPLAN_NOT_ALLOWED: proposed planning input must be inside the feature directory')
    if source_path.is_symlink() or not resolved_source.is_file():
        ns.die('TASK_REPLAN_NOT_ALLOWED: proposed planning input must be a regular in-feature file')
    try:
        proposed = ns.validate_replan_task(feature_dir, doc, args.task_id,
                                        json.loads(resolved_source.read_text(encoding='utf-8')))
    except (OSError, json.JSONDecodeError) as exc:
        ns.die(f'TASK_REPLAN_NOT_ALLOWED: cannot read proposed task planning input: {exc}')
    proposed_doc = copy.deepcopy(doc)
    proposed_doc['tasks'] = [proposed if task['id'] == args.task_id else task for task in doc['tasks']]
    new_contract = ns.semantic_task_contract_sha256(feature_dir, proposed_doc, proposed)
    repo_identity = str(ns.git_common_dir(feature_dir))
    ns.runtime_state_dir(feature_dir).mkdir(parents=True, exist_ok=True)
    with ns.lifecycle_state_lock(feature_dir) as lock:
        state = ns._load_state_unlocked(feature_dir, doc)
        entry = state['tasks'][args.task_id]
        current_generation = state.get('feature_generation', 1)
        expected_generation = getattr(args, 'expected_feature_generation', None)
        failed_replan = entry.get('status') == 'failed' and args.expected_status == 'failed'
        if expected_generation is None and not failed_replan:
            expected_generation = current_generation
        request_identity = {'repository': repo_identity, 'feature': doc.get('feature', feature_dir.name),
                            'task': args.task_id, 'expected_status': args.expected_status,
                            'expected_attempts': args.expected_attempts,
                            'expected_active_packet_revision': args.expected_active_revision,
                            'expected_contract_sha256': args.expected_contract_sha256,
                            'expected_feature_generation': expected_generation,
                            'proposed_contract_sha256': new_contract, 'proposed_task': proposed,
                            'provenance': operator, 'reason': reason,
                            'checkpoint': getattr(args, 'checkpoint', None)}
        request_id = ns.sha256_bytes(json.dumps(request_identity, sort_keys=True, separators=(',', ':')).encode())
        prior_requests = entry.get('replan_requests', [])
        if not isinstance(prior_requests, list):
            ns.die('ACTIVE_PACKET_AMBIGUOUS: malformed replan request ledger')
        committed = next((r for r in prior_requests if isinstance(r, dict) and r.get('request_id') == request_id), None)
        if committed:
            print(f'ALREADY_REPLANNED {args.task_id} revision={committed["new_revision"]}')
            if ns.fcntl is not None:
                ns.fcntl.flock(lock.fileno(), ns.fcntl.LOCK_UN)
            return
        if prior_requests:
            active_revision = entry.get('active_packet_revision')
            last_request = prior_requests[-1]
            if (not isinstance(last_request, dict) or
                    last_request.get('new_revision') != active_revision):
                ns.die('ACTIVE_PACKET_AMBIGUOUS: latest replan does not produce the active revision')
            if active_revision != args.expected_active_revision:
                ns.die('STALE_ACTIVE_PACKET: a prior replan changed the active packet revision')
        if entry.get('status') == 'completed':
            ns.die('TASK_REPLAN_NOT_ALLOWED: completed tasks cannot be replanned')
        if entry.get('status') != args.expected_status or args.expected_status not in {'running', 'failed'}:
            ns.die('TASK_REPLAN_NOT_ALLOWED: only CAS-bound running tasks or human-resolved failed tasks may be replanned')
        failed_replan = entry.get('status') == 'failed'
        if failed_replan:
            if type(expected_generation) is not int or expected_generation < 1:
                ns.die('TASK_REPLAN_NOT_ALLOWED: failed-task replan requires --expected-feature-generation')
            human_resolution = ns.validate_latest_human_resolution(feature_dir, doc, args.task_id, entry)
            if prior_requests:
                previous_replan_at = ns.parse_aware_timestamp(prior_requests[-1].get('committed_at'))
                resolved_at = ns.parse_aware_timestamp(human_resolution.get('resolved_at'))
                if previous_replan_at is None or resolved_at is None or resolved_at <= previous_replan_at:
                    ns.die('TASK_REPLAN_NOT_ALLOWED: failed task requires a human resolution newer than its latest replan')
        if type(current_generation) is not int or current_generation != expected_generation:
            ns.die('REPLAN_CONCURRENT_CONFLICT: current feature generation differs from request')
        attempts = entry.get('attempts', 0)
        if (not isinstance(attempts, int) or isinstance(attempts, bool) or
                type(args.expected_attempts) is not int or attempts != args.expected_attempts):
            ns.die('REPLAN_CONCURRENT_CONFLICT: current attempt count differs from request')
        active = ns.resolve_active_packet(feature_dir, doc, args.task_id, state=state)
        if active['revision_id'] != args.expected_active_revision:
            ns.die('STALE_ACTIVE_PACKET: active packet revision changed')
        if active['contract_sha256'] != args.expected_contract_sha256:
            ns.die('STALE_CONTRACT_FINGERPRINT: active semantic contract changed')
        if new_contract == active['contract_sha256']:
            ns.die('TASK_REPLAN_NOT_ALLOWED: proposed contract has no semantic change')
        if active['revision_id'] == 'unpublished':
            ns.die('TASK_REPLAN_NOT_ALLOWED: task has no published historical packet')
        ns.replan_failpoint('R1')
        packet = ns.packet_payload(proposed_doc, proposed, feature_dir)
        if packet.get('semantic_contract_sha256') != new_contract:
            ns.die('TASK_REPLAN_NOT_ALLOWED: packet fingerprint differs from proposed planning contract')
        # Publish validated legacy root bytes to the common repository authority
        # before publishing/activating a lineage that depends on them. Immutable
        # publication is idempotent and rejects identity collisions.
        revision_files: dict[str, tuple[bytes, int]] = {}
        if active.get('legacy') and active.get('packet') is not None:
            ns.publish_revision(feature_dir, args.task_id, active['packet'], revision_files)
        revision_id, _packet_path = ns.publish_revision(feature_dir, args.task_id, packet, revision_files)
        # Immutable revisions are part of the complete state authority tree. Persist
        # newly published but not-yet-active revisions as an unchanged-state snapshot
        # before later CAS/activation steps can fail, so a safe orphan remains valid.
        ns.save_state(feature_dir, state, additional_files=revision_files)
        ns.replan_failpoint('R4')
        staged = copy.deepcopy(entry)
        lineage = staged.get('packet_lineage', [])
        if not isinstance(lineage, list):
            ns.die('ACTIVE_PACKET_AMBIGUOUS: packet lineage is malformed')
        if not lineage:
            lineage.append({'previous_revision': None, 'revision_id': active['revision_id'],
                            'contract_sha256': active['contract_sha256'], 'legacy': bool(active.get('legacy'))})
        elif lineage[-1].get('revision_id') != active['revision_id']:
            ns.die('ACTIVE_PACKET_AMBIGUOUS: lineage does not end at active packet')
        lineage.append({'previous_revision': active['revision_id'], 'revision_id': revision_id,
                        'contract_sha256': new_contract, 'legacy': False})
        staged['packet_lineage'] = lineage
        staged['active_packet_revision'] = revision_id
        timestamp = ns.utc_now().isoformat()
        if not failed_replan:
            attempt_history = staged.setdefault('attempt_bindings', [])
            ns.validate_attempt_binding_ledger(staged)
            current_binding = next((record for record in attempt_history
                                    if isinstance(record, dict) and record.get('attempt') == attempts), None)
            binding_result = ns.classify_historical_packet_binding(feature_dir, doc, args.task_id,
                                                                staged, active, attempts)
            historical_binding_classification = binding_result['classification']
            historical_binding_status = {
                'AUTOMATICALLY_PROVEN': 'proven', 'HUMAN_ATTESTED': 'human_attested',
                'AMBIGUOUS': 'ambiguous'}[historical_binding_classification]
            if current_binding is None and historical_binding_classification == 'AUTOMATICALLY_PROVEN':
                attempt_history.append({'attempt': attempts, 'packet_revision': active['revision_id'],
                    'contract_sha256': active['contract_sha256'], 'binding_status': 'proven',
                    'bound_at': ns.utc_now().isoformat(),
                    'evidence': 'versioned recovery payload identity matches the active packet and revision'})
            elif current_binding is None and historical_binding_classification == 'HUMAN_ATTESTED':
                attempt_history.append({'attempt': attempts, 'packet_revision': active['revision_id'],
                    'contract_sha256': active['contract_sha256'], 'binding_status': 'human_attested',
                    'classification': 'HUMAN_ATTESTED', 'identity_bridge_id': binding_result['bridge_id'],
                    'bound_at': ns.utc_now().isoformat(),
                    'evidence': 'explicit immutable human packet identity bridge'})
            elif (current_binding is not None and current_binding.get('binding_status') == 'ambiguous' and
                  historical_binding_classification == 'HUMAN_ATTESTED'):
                current_binding.update({'packet_revision': active['revision_id'],
                    'contract_sha256': active['contract_sha256'], 'binding_status': 'human_attested',
                    'classification': 'HUMAN_ATTESTED', 'identity_bridge_id': binding_result['bridge_id'],
                    'bound_at': ns.utc_now().isoformat(),
                    'evidence': 'explicit immutable human packet identity bridge'})
            elif current_binding is None and historical_binding_classification == 'AMBIGUOUS':
                recovery = staged.get('claim_recovery')
                evidence = 'packet revision was not recorded for this historical attempt'
                if isinstance(recovery, dict) and recovery.get('attempt') == attempts:
                    evidence = 'claim-recovery packet identity differs from the current active packet'
                attempt_history.append({'attempt': attempts, 'binding_status': 'ambiguous',
                    'observed_active_packet_revision': active['revision_id'],
                    'observed_contract_sha256': active['contract_sha256'],
                    'evidence': evidence, 'recorded_at': ns.utc_now().isoformat()})
            if attempts > 1:
                staged['unbound_historical_attempts'] = [n for n in range(1, attempts)
                    if not any(isinstance(x, dict) and x.get('attempt') == n for x in attempt_history)]
            staged['attempt_termination'] = {'attempt': attempts, 'classification': 'REPLAN_SUPERSEDED',
                'reason_code': 'TASK_REPLAN_REQUIRED', 'reason': reason, 'terminated_at': timestamp,
                'active_revision_at_termination': active['revision_id'],
                'contract_sha256_at_termination': active['contract_sha256'],
                'historical_binding_status': historical_binding_status,
                'historical_binding_classification': historical_binding_classification,
                'verification': 'NOT_RUN_BY_REPLAN'}
            if historical_binding_status in {'proven', 'human_attested'}:
                staged['attempt_termination']['packet_revision'] = active['revision_id']
                staged['attempt_termination']['contract_sha256'] = active['contract_sha256']
            if historical_binding_classification == 'HUMAN_ATTESTED':
                staged['attempt_termination']['identity_bridge_id'] = binding_result['bridge_id']
            staged['status'] = 'failed'
            staged['replanned_at'] = timestamp
            staged['last_transition'] = {'kind': 'running_task_replan', 'attempts': attempts, 'at': timestamp}
        staged['replan_requests'] = [*prior_requests, {'request_id': request_id,
            'repository': repo_identity, 'feature': doc.get('feature', feature_dir.name), 'task': args.task_id,
            'old_revision': active['revision_id'], 'new_revision': revision_id,
            'old_contract_sha256': active['contract_sha256'], 'new_contract_sha256': new_contract,
            'expected_status': args.expected_status, 'expected_attempts': attempts,
            'expected_feature_generation': expected_generation, 'reason': reason,
            'provenance': operator, 'checkpoint': args.checkpoint,
            **({} if failed_replan else {
                'historical_binding_classification': historical_binding_classification,
                'identity_bridge_id': binding_result.get('bridge_id')}),
            'committed_at': timestamp}]
        if not failed_replan:
            for key in ('owner', 'heartbeat_at', 'lease_expires_at'):
                staged.pop(key, None)
        ns.replan_failpoint('R5')
        ns.replan_failpoint('R6')
        state['tasks'][args.task_id] = staged
        ns.replan_failpoint('R7')
        ns.save_state(feature_dir, state)  # atomic lifecycle state replace is the activation commit
        ns.replan_failpoint('R8')
        if ns.fcntl is not None:
            ns.fcntl.flock(lock.fileno(), ns.fcntl.LOCK_UN)
    print(f'REPLANNED {args.task_id} revision={revision_id} attempts={attempts}')


def changed_paths(worktree: pathlib.Path, *, ns: Any) -> list[str]:
    tracked = subprocess.run(
        ['git', 'diff', '--name-only', 'HEAD'], cwd=worktree, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    untracked = subprocess.run(
        ['git', 'ls-files', '--others', '--exclude-standard'], cwd=worktree, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return sorted(set(p for p in tracked + untracked if p))


def path_matches(path: str, patterns: list[str], *, ns: Any) -> bool:
    import fnmatch
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def checkpoint_worktree(doc: dict[str, Any], task: dict[str, Any], target: pathlib.Path, label: str = 'checkpoint', *, ns: Any) -> str:
    actual = ns.changed_paths(target)
    patterns = [p for p in task.get('allowed_paths', []) if isinstance(p, str)]
    violations = [path for path in actual if not ns.path_matches(path, patterns)]
    if violations:
        ns.die(f'cannot checkpoint paths outside task allowed_paths: {violations}')

    if actual:
        subprocess.run(['git', 'add', '-A'], cwd=target, check=True)
        feature = str(doc.get('feature', 'feature'))
        message = f'agent {label} {feature} {task["id"]}'
        subprocess.run(
            [
                'git', '-c', 'user.name=Agent Harness',
                '-c', 'user.email=agent-harness@local.invalid',
                'commit', '--no-gpg-sign', '-m', message,
            ],
            cwd=target,
            check=True,
        )
    return subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=target, capture_output=True, text=True, check=True
    ).stdout.strip()


def add_completion_record(feature_dir: pathlib.Path, record: dict[str, Any], *, ns: Any) -> None:
    path = ns.completion_authority_dir(feature_dir, 'completion-records', record['task']) / \
        f"{record['record_id'].rsplit(':', 1)[-1]}.json"
    ns.publish_completion_authority(path, record)


def validate_completion_binding(evidence: dict[str, Any], feature_dir: pathlib.Path, task_id: str,
                                entry: dict[str, Any], active: dict[str, Any], *, ns: Any) -> list[str]:
    expected = {
        'repository': str(ns.git_common_dir(feature_dir)), 'feature': feature_dir.name, 'task': task_id,
        'attempt': entry.get('attempts'), 'packet_revision': active['revision_id'],
        'contract_fingerprint': active['contract_sha256'],
    }
    errors = [f'completion evidence {key} does not match current authority'
              for key, value in expected.items() if evidence.get(key) != value]
    if (not isinstance(evidence.get('checkpoint'), str) or
            re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', evidence['checkpoint']) is None):
        errors.append('completion evidence checkpoint must be a full Git commit id')
    if not isinstance(evidence.get('changed_paths'), list) or any(not isinstance(p, str) for p in evidence['changed_paths']):
        errors.append('completion evidence changed_paths must be a string array')
    return errors


def require_available_completion_origin(feature_dir: pathlib.Path, state: dict[str, Any],
                                        task_id: str, entry: dict[str, Any], *, ns: Any) -> None:
    """Require every accepted v2 origin obligation to have a bound PASS terminal."""
    authorities = [state.get('verification_authority')]
    history = state.get('verification_plan_history', [])
    if isinstance(history, list):
        authorities.extend(item.get('prior_authority') for item in history if isinstance(item, dict))
    for authority in authorities:
        if not isinstance(authority, dict):
            continue
        binding = authority.get('binding')
        plan_id = authority.get('accepted_plan_id')
        is_v2 = (isinstance(plan_id, str) and plan_id.startswith('verification-plan-v2:')) or \
            (isinstance(binding, dict) and binding.get('schema_version') == 2)
        if not is_v2:
            continue
        if (not isinstance(binding, dict) or binding.get('task_id') != task_id or
                binding.get('task_attempt') != entry.get('attempts')):
            continue
        _authority_module = ns._verification_authority()  # the consumer's `verification.authority`, looked up per call
        validate_plan_record, validate_plan_admission_record, validate_plan_launch_reservation, build_plan_launch_reservation = (_authority_module.validate_plan_record, _authority_module.validate_plan_admission_record, _authority_module.validate_plan_launch_reservation, _authority_module.build_plan_launch_reservation)
        _store_module = ns._verification_store()  # the consumer's `verification.store`, looked up per call
        StoreError, VerificationStore = (_store_module.StoreError, _store_module.VerificationStore)
        repository = pathlib.Path(feature_dir).resolve().parents[2]
        try:
            store = VerificationStore(repository)
            plan = store.load_plan_record(plan_id)
            validate_plan_record(plan, repository=repository, reconstruct=True)
            if (plan.get('task_id') != task_id or plan.get('task_attempt') != entry.get('attempts') or
                    plan.get('lifecycle_generation') != binding.get('lifecycle_generation') or
                    plan.get('candidate_identity') != binding.get('candidate_identity') or
                    plan.get('final_changed_surface_id') != binding.get('final_changed_surface_id')):
                raise StoreError('PLAN_BINDING_MISMATCH')
            units = plan.get('execution_units')
            reservations = authority.get('launch_reservations', {})
            admissions = authority.get('admissions', {})
            admission_hashes = authority.get('admission_hashes', {})
            consumptions = authority.get('launch_consumptions', {})
            if not all(isinstance(value, dict) for value in
                       (reservations, admissions, admission_hashes, consumptions)):
                raise StoreError('VERIFICATION_ORIGIN_COVERAGE_INCOMPLETE')
            terminals = store.reconstruct_execution_terminals(rebuild=False)
            if not isinstance(units, list) or not units:
                raise StoreError('VERIFICATION_ORIGIN_COVERAGE_INCOMPLETE')
            if not reservations and not admissions and not consumptions:
                raise StoreError('VERIFICATION_ORIGIN_ADMISSION_UNAVAILABLE')
            for unit in units:
                unit_id = unit.get('unit_id') if isinstance(unit, dict) else None
                reservation = reservations.get(unit_id)
                admission = admissions.get(unit_id)
                consumption = consumptions.get(unit_id)
                admission_hash = admission_hashes.get(unit_id)
                if (not isinstance(reservation, dict) or reservation.get('status') != 'execution_terminal' or
                        reservation.get('terminal_result') != 'PASS' or
                        not isinstance(admission, dict) or not isinstance(consumption, dict) or
                        consumption.get('status') != 'execution_terminal' or
                        consumption.get('reservation_id') != reservation.get('reservation_id') or
                        not isinstance(admission_hash, str) or
                        reservation.get('terminal_receipt_hash') != consumption.get('terminal_receipt_hash')):
                    raise StoreError('VERIFICATION_ORIGIN_COVERAGE_INCOMPLETE')
                bound_admission = store.load_admission_record(
                    admission.get('admission_id'), expected_hash=admission_hash)
                validate_plan_admission_record(bound_admission, plan, unit)
                immutable_reservation = build_plan_launch_reservation(
                    bound_admission, transition_id=reservation.get('transition_id'))
                validate_plan_launch_reservation(immutable_reservation, bound_admission)
                if any(reservation.get(key) != value for key, value in immutable_reservation.items()):
                    raise StoreError('VERIFICATION_ORIGIN_COVERAGE_INCOMPLETE')
                bound_consumption = store.load_launch_consumption_record(
                    consumption.get('consumption_id'), admission=bound_admission,
                    reservation=immutable_reservation, expected_hash=consumption.get('sha256'))
                matches = [terminal for terminal in terminals
                    if terminal.get('receipt_hash') == reservation.get('terminal_receipt_hash')]
                if (len(matches) != 1 or matches[0].get('result') != 'PASS' or
                        matches[0].get('plan_id') != plan_id or
                        matches[0].get('unit_id') != unit_id or
                        matches[0].get('launch_reservation_id') != reservation.get('reservation_id') or
                        matches[0].get('launch_consumption_id') != bound_consumption.get('consumption_id') or
                        matches[0].get('admission_id') != bound_admission.get('admission_id') or
                        matches[0].get('admission_sha256') != admission_hash or
                        matches[0].get('obligation_ids') != unit.get('obligation_ids') or
                        matches[0].get('lifecycle_generation') != plan.get('lifecycle_generation') or
                        matches[0].get('candidate_identity') != plan.get('candidate_identity') or
                        matches[0].get('final_changed_surface_id') != plan.get('final_changed_surface_id')):
                    raise StoreError('VERIFICATION_ORIGIN_COVERAGE_INCOMPLETE')
        except StoreError as exc:
            if str(exc) in {'ACCEPTED_PLAN_UNAVAILABLE', 'VERIFICATION_ORIGIN_ADMISSION_UNAVAILABLE'}:
                ns.die('VERIFICATION_ORIGIN_ADMISSION_UNAVAILABLE: accepted plan is absent or unbindable')
            ns.die('VERIFICATION_ORIGIN_COVERAGE_INCOMPLETE: accepted plan lacks exact terminal coverage')
        except (OSError, ValueError, KeyError, TypeError):
            ns.die('VERIFICATION_ORIGIN_COVERAGE_INCOMPLETE: accepted plan lacks exact terminal coverage')


def cmd_complete(args: argparse.Namespace, *, ns: Any) -> None:
    doc = ns.load_validated(args.feature_dir)
    idx = ns.task_index(doc)
    task = idx.get(args.task_id)
    if not task:
        ns.die(f'unknown task {args.task_id}')
    evidence_path = pathlib.Path(args.evidence)
    evidence_errors = ns.validate_evidence(evidence_path, require_pass=True)
    if evidence_errors:
        ns.die('; '.join(evidence_errors))
    evidence_doc = ns.load_json(evidence_path)

    with ns.locked_state(args.feature_dir, doc) as state:
        entry = state['tasks'].get(args.task_id)
        if not entry:
            ns.die(f'unknown task {args.task_id}')
        ns.require_available_completion_origin(args.feature_dir, state, args.task_id, entry)
        if entry.get('status') == 'completed':
            active = ns.resolve_active_packet(args.feature_dir, doc, args.task_id, state=state)
            binding_errors = ns.validate_completion_binding(evidence_doc, args.feature_dir, args.task_id, entry, active)
            proof_errors = ns.validate_completion_proofs(
                evidence_doc, ns.active_task_contract(args.feature_dir, doc, args.task_id, state=state),
                ns.required_completion_criteria(ns.active_task_contract(
                    args.feature_dir, doc, args.task_id, state=state)), args.feature_dir)
            existing = next((item for item in ns.completion_records(args.feature_dir, args.task_id)
                             if item.get('record_id') == entry.get('completion_record_id')), None)
            evidence_hash = ns.sha256_bytes(evidence_path.read_bytes())
            if (not binding_errors and not proof_errors and existing and
                    existing.get('evidence_reference_sha256') == evidence_hash and
                    existing.get('checkpoint') == evidence_doc.get('checkpoint')):
                print(f'ALREADY_COMPLETED {args.task_id} checkpoint={existing["checkpoint"][:12]}')
                return
            ns.die('COMPLETION_CONFLICT: task already has a different authoritative completion')
        ns.owner_guard(entry, args.owner)
        repair_claim = entry.get('completion_repair_claim')
        is_repair = isinstance(repair_claim, dict)
        if is_repair:
            ns.die('COMPLETION_REPAIR_REQUIRES_CANONICAL_OPERATION: use complete-repair')
        if entry.get('status') != 'running':
            ns.die(f'{args.task_id} is not running')
        ns.validate_active_verification_resume(entry)
        task = ns.active_task_contract(args.feature_dir, doc, args.task_id, state=state)
        active = ns.resolve_active_packet(args.feature_dir, doc, args.task_id, state=state)
        binding_errors = ns.validate_completion_binding(evidence_doc, args.feature_dir, args.task_id, entry, active)
        if binding_errors:
            ns.die('; '.join(binding_errors))
        required = ns.required_completion_criteria(task)
        proof_errors = ns.validate_completion_proofs(evidence_doc, task, required, args.feature_dir)
        if proof_errors:
            ns.die('; '.join(proof_errors))

        repair_authorization = None
        correction_id = None
        for correction in ns.correction_records(args.feature_dir, args.task_id):
            repaired = ns.correction_is_repaired(args.feature_dir, args.task_id, correction)
            if (not repaired or
                    (correction.get('completed_attempt') == entry.get('attempts') and
                     ns.correction_targets_current_completion(
                         args.feature_dir, args.task_id, entry, correction))):
                ns.die('COMPLETION_CORRECTION_REQUIRED: corrected completion requires explicit repair claim')
        ns.retire_finalized_completion_repair_pointers(
            args.feature_dir, args.task_id, entry, retired_by='complete')

        feature = str(doc.get('feature', args.feature_dir.name))
        target = ns.worktree_path(feature, args.task_id)
        reported = sorted(set(evidence_doc['changed_paths']))
        if target.exists():
            actual = ns.changed_paths(target)
            if reported != actual:
                ns.die(f'evidence changed_paths differs from task worktree; reported={reported}, actual={actual}')
            checkpoint = ns.checkpoint_worktree(doc, task, target)
            if ns.changed_paths(target):
                ns.die('COMPLETION_WORKTREE_DIRTY: checkpoint worktree is not clean')
        else:
            checkpoint = evidence_doc['checkpoint']
            if checkpoint != entry.get('checkpoint_commit'):
                ns.die('COMPLETION_CHECKPOINT_MISMATCH: no task worktree exists and evidence checkpoint differs from lifecycle checkpoint')
            exists = subprocess.run(['git', 'cat-file', '-e', f'{checkpoint}^{{commit}}'],
                                    cwd=ns.feature_repo_base(args.feature_dir), capture_output=True).returncode == 0
            if not exists:
                ns.die('COMPLETION_CHECKPOINT_INVALID: checkpoint is not available in the repository')
        if evidence_doc['checkpoint'] != checkpoint:
            ns.die('COMPLETION_CHECKPOINT_MISMATCH: evidence checkpoint differs from verified task checkpoint')
        ns.require_task_commit_ancestry(args.feature_dir, task, state, checkpoint, operation='COMPLETION')

        record = {
            'schema_version': ns.COMPLETION_AUTHORITY_SCHEMA_VERSION, 'record_type': 'completion',
            'repository_id': str(ns.git_common_dir(args.feature_dir)), 'feature': args.feature_dir.name,
            'task': args.task_id, 'attempt': int(entry.get('attempts', 0)),
            'checkpoint': checkpoint, 'packet_revision': active['revision_id'],
            'contract_fingerprint': active['contract_sha256'],
            'evidence_reference_sha256': ns.sha256_bytes(evidence_path.read_bytes()),
            'correction_id': None, 'repair_authorization_id': None,
        }
        record['record_id'] = ns.completion_record_id('completion', record)
        record['created_at'] = ns.utc_now().isoformat()
        ns.add_completion_record(args.feature_dir, record)
        ns._completion_failpoint('completion-published')

        entry.update({
            'status': 'completed', 'completed_at': ns.utc_now().isoformat(),
            'evidence': str(evidence_path), 'checkpoint_commit': checkpoint,
            'completion_record_id': record['record_id'],
        })
        resume = entry.pop('verification_resume_active', None)
        if isinstance(resume, dict):
            resume_history = entry.setdefault('verification_resume_history', [])
            resume_history[-1].update({'result': 'completed', 'ended_at': entry['completed_at']})
            entry.pop('verification_resume_pending', None)
            entry.pop('control_outcome', None)
        if entry.pop('active_human_resume', None) is not None:
            entry['last_human_resume_used_at'] = ns.utc_now().isoformat()
        if entry.pop('active_retry_authorization', None) is not None:
            entry['last_human_resume_used_at'] = ns.utc_now().isoformat()
        entry.pop('start_origin_status', None)
        for key in ('owner', 'heartbeat_at', 'lease_expires_at'):
            entry.pop(key, None)
    print(f'COMPLETED {args.task_id} checkpoint={checkpoint[:12]}')


def cmd_fail(args: argparse.Namespace, *, ns: Any) -> None:
    doc = ns.load_validated(args.feature_dir)
    idx = ns.task_index(doc)
    task = idx.get(args.task_id)
    if not task:
        ns.die(f'unknown task {args.task_id}')
    with ns.locked_state(args.feature_dir, doc) as state:
        entry = state['tasks'].get(args.task_id)
        if not entry:
            ns.die(f'unknown task {args.task_id}')
        ns.owner_guard(entry, args.owner)
        if entry.get('status') != 'running':
            ns.die(f'{args.task_id} is not running')
        ns.validate_active_verification_resume(entry)
        if isinstance(entry.get('completion_repair_claim'), dict):
            entry['status'] = 'correction_required'
            entry['completion_repair_last_failure'] = args.reason
            entry.setdefault('completion_repair_claim_history', []).append({
                **entry.pop('completion_repair_claim'), 'ended_at': ns.utc_now().isoformat(),
                'result': 'failed',
            })
            for key in ('owner', 'heartbeat_at', 'lease_expires_at', 'claimed_at'):
                entry.pop(key, None)
            print(f'REPAIR_FAILED {args.task_id} attempt={entry["attempts"]}; authorization remains scoped')
            return
        task = ns.active_task_contract(args.feature_dir, doc, args.task_id, state=state)
        max_attempts = 1 + int(doc.get('max_rework_attempts', 2))
        attempts = int(entry.get('attempts', 0))
        status = 'escalated' if args.escalate else 'failed'
        feature = str(doc.get('feature', args.feature_dir.name))
        target = ns.worktree_path(feature, args.task_id)
        failed_commit = None
        if target.exists():
            failed_commit = ns.checkpoint_worktree(doc, task, target, label='failed-attempt')
        entry.update({
            'status': status,
            'last_failure': args.reason,
            'failed_at': ns.utc_now().isoformat(),
            'last_attempt_commit': failed_commit,
        })
        entry['last_transition'] = {'kind': 'attempt_failed', 'attempts': attempts,
                                    'status': status, 'at': entry['failed_at']}
        control_outcome = getattr(args, 'control_outcome', None)
        if control_outcome in ns.VERIFICATION_BLOCKAGE_OUTCOMES:
            entry['control_outcome'] = control_outcome
        resume = entry.pop('verification_resume_active', None)
        if isinstance(resume, dict):
            resume_history = entry.setdefault('verification_resume_history', [])
            resume_history[-1].update({'result': control_outcome or status,
                                       'ended_at': entry['failed_at']})
            entry.pop('verification_resume_pending', None)
        if args.evidence:
            entry['last_failure_evidence'] = str(pathlib.Path(args.evidence))
        if entry.pop('active_human_resume', None) is not None:
            entry['last_human_resume_used_at'] = ns.utc_now().isoformat()
        if entry.pop('active_retry_authorization', None) is not None:
            entry['last_human_resume_used_at'] = ns.utc_now().isoformat()
        entry.pop('start_origin_status', None)
        for key in ('owner', 'heartbeat_at', 'lease_expires_at'):
            entry.pop(key, None)
    print(f'{status.upper()} {args.task_id} attempt={attempts}/{max_attempts}')


def cmd_release(args: argparse.Namespace, *, ns: Any) -> None:
    doc = ns.load_validated(args.feature_dir)
    idx = ns.task_index(doc)
    task = idx.get(args.task_id)
    if not task:
        ns.die(f'unknown task {args.task_id}')
    with ns.locked_state(args.feature_dir, doc) as state:
        entry = state['tasks'].get(args.task_id)
        if not entry:
            ns.die(f'unknown task {args.task_id}')
        ns.owner_guard(entry, args.owner)
        if entry.get('status') != 'running':
            ns.die(f'{args.task_id} is not running')
        ns.validate_active_verification_resume(entry)
        if isinstance(entry.get('completion_repair_claim'), dict):
            entry['status'] = 'correction_required'
            entry['completion_repair_last_release'] = args.reason
            entry.setdefault('completion_repair_claim_history', []).append({
                **entry.pop('completion_repair_claim'), 'ended_at': ns.utc_now().isoformat(),
                'result': 'released',
            })
            for key in ('owner', 'heartbeat_at', 'lease_expires_at', 'claimed_at'):
                entry.pop(key, None)
            print(f'REPAIR_RELEASED {args.task_id} attempt={entry["attempts"]}')
            return
        active_verification_resume = entry.pop('verification_resume_active', None)
        task = ns.active_task_contract(args.feature_dir, doc, args.task_id, state=state)
        feature = str(doc.get('feature', args.feature_dir.name))
        target = ns.worktree_path(feature, args.task_id)
        partial_commit = None
        if target.exists():
            partial_commit = ns.checkpoint_worktree(doc, task, target, label='released-attempt')
        entry.update({
            'status': 'failed',
            'last_failure': f'lease released: {args.reason}',
            'released_at': ns.utc_now().isoformat(),
            'last_attempt_commit': partial_commit,
        })
        entry['last_transition'] = {'kind': 'lease_released', 'attempts': entry.get('attempts', 0),
                                    'status': 'failed', 'at': entry['released_at']}
        if isinstance(active_verification_resume, dict):
            entry['verification_resume_pending'] = active_verification_resume
        if entry.pop('active_human_resume', None) is not None:
            entry['last_human_resume_used_at'] = ns.utc_now().isoformat()
        if entry.pop('active_retry_authorization', None) is not None:
            entry['last_human_resume_used_at'] = ns.utc_now().isoformat()
        entry.pop('start_origin_status', None)
        for key in ('owner', 'heartbeat_at', 'lease_expires_at'):
            entry.pop(key, None)
    print(f'RELEASED {args.task_id}')


def descendants(idx: dict[str, dict[str, Any]], target: str, *, ns: Any) -> set[str]:
    result: set[str] = set()
    changed = True
    while changed:
        changed = False
        for tid, task in idx.items():
            if tid == target or tid in result:
                continue
            deps = set(task.get('depends_on', []))
            if target in deps or deps.intersection(result):
                result.add(tid)
                changed = True
    return result


def prune_task_workspace(feature: str, task_id: str, *, expected_branch_commit: str | None = None,
                         guard_branch: bool = False, ns: Any) -> None:
    target = ns.worktree_path(feature, task_id)
    branch = f'agent/{feature}/{task_id}'
    ns.ensure_task_workspace_clean(feature, task_id)
    branch_ref = f'refs/heads/{branch}'
    branch_result = subprocess.run(
        ['git', 'rev-parse', '--verify', branch_ref], capture_output=True, text=True, check=False,
    )
    branch_commit = branch_result.stdout.strip() if branch_result.returncode == 0 else None
    if guard_branch and branch_commit != expected_branch_commit:
        ns.die(f'cannot invalidate task branch {branch}: expected {expected_branch_commit}, found {branch_commit}')
    if target.exists():
        subprocess.run(['git', 'worktree', 'remove', str(target)], check=True)
    if branch_commit is not None:
        subprocess.run(['git', 'update-ref', '-d', branch_ref, branch_commit], check=True)
    elif guard_branch and subprocess.run(
            ['git', 'show-ref', '--verify', '--quiet', branch_ref], check=False).returncode == 0:
        ns.die(f'cannot invalidate task branch {branch}: a branch appeared during cleanup')


def ensure_task_workspace_clean(feature: str, task_id: str, *, ns: Any) -> None:
    target = ns.worktree_path(feature, task_id)
    if target.exists():
        dirty = ns.changed_paths(target)
        ignored = subprocess.run(
            ['git', 'status', '--porcelain', '--ignored=traditional', '--untracked-files=normal'],
            cwd=target, capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        dirty.extend(line[3:] for line in ignored if line.startswith('!! '))
        dirty = sorted(set(dirty))
        if dirty:
            ns.die(f'cannot invalidate dirty descendant worktree {target}: {dirty}')


def reopen_dependency_graph(feature_dir: pathlib.Path, doc: dict[str, Any],
                            state: dict[str, Any], *, ns: Any) -> dict[str, dict[str, Any]]:
    """Build reopen dependencies without resolving unrelated legacy packets."""
    graph = {}
    for tid, task in ns.task_index(doc).items():
        ns.validate_attempt_binding_ledger(state['tasks'][tid])
        if state['tasks'].get(tid, {}).get('active_packet_revision') is not None:
            graph[tid] = ns.active_task_contract(feature_dir, doc, tid, state=state)
        else:
            graph[tid] = task
    return graph


def cmd_reopen(args: argparse.Namespace, *, ns: Any) -> None:
    doc = ns.load_validated(args.feature_dir)
    idx = ns.task_index(doc)
    if args.task_id not in idx:
        ns.die(f'unknown task {args.task_id}')
    feature = str(doc.get('feature', args.feature_dir.name))
    stale: list[str] = []

    # Reopen and descendant invalidation are one state transition. Validate every lease/status under
    # the state lock before deleting any task-local workspace, otherwise a racing worker can lose a
    # live worktree even though the reopen itself is rejected.
    with ns.locked_state(args.feature_dir, doc) as state:
        stale = sorted(ns.descendants(ns.reopen_dependency_graph(args.feature_dir, doc, state), args.task_id))
        target = state['tasks'][args.task_id]
        if target.get('status') != 'completed':
            ns.die(f'{args.task_id} must be completed before it can be reopened')

        running_descendants = [tid for tid in stale if state['tasks'][tid].get('status') == 'running']
        if running_descendants:
            ns.die(
                f'cannot reopen {args.task_id} while descendants are running: '
                f'{running_descendants}; release or recover them first'
            )

        max_attempts = 1 + int(doc.get('max_rework_attempts', 2))
        exhausted = int(target.get('attempts', 0)) >= max_attempts

        ns.retire_finalized_completion_repair_pointers(
            args.feature_dir, args.task_id, target, retired_by='reopen')

        # Preflight every descendant before removing the first workspace. prune_task_workspace
        # repeats the check immediately before each removal to fail closed on intervening edits.
        for tid in stale:
            ns.ensure_task_workspace_clean(feature, tid)

        snapshots = {tid: ns.task_workspace_snapshot(feature, tid) for tid in stale}
        attempted_prunes: list[str] = []
        try:
            for tid in stale:
                # Include the current item so a partially failed worktree/branch removal is restored.
                attempted_prunes.append(tid)
                snapshot = snapshots[tid]
                if snapshot['branch_commit'] is None and snapshot['worktree_commit'] is None:
                    ns.prune_task_workspace(feature, tid)
                else:
                    ns.prune_task_workspace(feature, tid,
                                         expected_branch_commit=snapshot['branch_commit'], guard_branch=True)
        except BaseException:
            restoration_errors = []
            for tid in reversed(attempted_prunes):
                try:
                    ns.restore_task_workspace(feature, tid, snapshots[tid])
                except (OSError, subprocess.CalledProcessError) as exc:
                    restoration_errors.append(f'{tid}: {exc}')
            if restoration_errors:
                ns.die('reopen cleanup failed and workspace rollback was incomplete: ' + '; '.join(restoration_errors))
            raise

        reopened_at = ns.utc_now().isoformat()
        target.update({
            'status': 'escalated' if exhausted else 'failed',
            'last_failure': args.reason,
            'reopened_at': reopened_at,
        })
        target['last_transition'] = {'kind': 'reopened', 'attempts': target.get('attempts', 0),
                                     'status': target['status'], 'at': reopened_at}
        if args.evidence:
            target['rework_evidence'] = str(pathlib.Path(args.evidence))
        for key in ('owner', 'heartbeat_at', 'lease_expires_at'):
            target.pop(key, None)

        for tid in stale:
            entry = state['tasks'][tid]
            history = entry.get('attempt_history')
            if not isinstance(history, list):
                history = []
            history.append({
                'invalidated_at': reopened_at,
                'invalidated_by': args.task_id,
                'prior_status': entry.get('status'),
                'attempts': int(entry.get('attempts', 0)),
                'last_attempt_commit': entry.get('last_attempt_commit'),
                'checkpoint_commit': entry.get('checkpoint_commit'),
                'completion_evidence': entry.get('completion_evidence'),
                'last_failure': entry.get('last_failure'),
            })
            entry.clear()
            entry.update({
                'status': 'pending',
                'attempts': 0,
                'invalidated_by': args.task_id,
                'invalidated_at': reopened_at,
                'attempt_history': history,
            })
    if exhausted:
        print(f'ESCALATED {args.task_id}: rework budget exhausted; invalidated descendants: '
              f'{", ".join(stale) if stale else "none"}')
    else:
        print(f'REOPENED {args.task_id}; invalidated descendants: {", ".join(stale) if stale else "none"}')


def worktree_path(feature: str, task_id: str, *, ns: Any) -> pathlib.Path:
    root = ns.repo_root()
    return root.parent / f'{root.name}{ns.WORKTREE_ROOT_SUFFIX}' / feature / task_id


def execution_protocol_paths(feature_dir: pathlib.Path, *, ns: Any) -> list[str]:
    root = ns.repo_root()
    try:
        feature_rel = feature_dir.resolve().relative_to(root)
    except ValueError:
        ns.die('feature_dir must be inside the repository')
    return [
        '.gitignore', 'AGENTS.md', 'CLAUDE.md', '.claude/agents',
        'docs/agentic-sdd', '.github/workflows/agentic-sdd.yml',
        f'{feature_rel}/spec.md', f'{feature_rel}/plan.md', f'{feature_rel}/tasks.json',
        f'{feature_rel}/design.json', f'{feature_rel}/design/gate.json',
        f'{feature_rel}/verification-contract.json', f'{feature_rel}/wayfinder-handoff.json',
    ]


def _path_is_selected(path: str, selected: list[str], *, ns: Any) -> bool:
    return any(path == item or path.startswith(item.rstrip('/') + '/')
               for item in selected if item)


def task_worktree_snapshot(feature_dir: pathlib.Path, *, ns: Any) -> str:
    """Return committed HEAD only, rejecting dirty paths that define task freshness."""
    root = ns.repo_root()
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True,
                          text=True, check=True).stdout.strip()
    selected = ns.execution_protocol_paths(feature_dir)
    dirty = [path for path in ns.changed_paths(root) if ns._path_is_selected(path, selected)]
    if dirty:
        ns.die('primary checkout has uncommitted protocol/feature files; commit them before task worktree creation: '
            f'{dirty}')
    return head


def stage_first_task_base(feature_dir: pathlib.Path, state: dict[str, Any],
                          durable_state: dict[str, Any], task: dict[str, Any],
                          target: pathlib.Path, *, ns: Any) -> None:
    """Stage base authority and persist a recovery breadcrumb before worktree creation."""
    if task.get('depends_on'):
        return
    if state.get('base_commit') is not None:
        return

    base = durable_state.get('base_commit')
    kind = durable_state.get('base_kind')
    if base is None:
        base = durable_state.get('pending_base_commit')
        kind = durable_state.get('pending_base_kind')
        if base is not None:
            check = subprocess.run(['git', 'cat-file', '-e', f'{base}^{{commit}}'], capture_output=True)
            if (check.returncode != 0 or kind != 'head' or
                    ns.historical_feature_fingerprint(feature_dir, base) != ns.feature_fingerprint(feature_dir)):
                ns.die(f'BASE_AUTHORITY_MISSING: pending first-task base cannot prove the accepted feature: {base}')
    if base is None:
        if target.exists():
            ns.die(f'BASE_AUTHORITY_MISSING: existing worktree has no recorded first-task base: {target}')
        base = ns.task_worktree_snapshot(feature_dir)
        kind = 'head'
        durable_state['pending_base_commit'] = base
        durable_state['pending_base_kind'] = kind
        # The pending pair is only a write-ahead recovery breadcrumb. It lets
        # retries recover the original snapshot after an interrupted Git side
        # effect without publishing base_commit/base_kind on a failed start.
        ns.save_state(feature_dir, durable_state)

    check = subprocess.run(['git', 'cat-file', '-e', f'{base}^{{commit}}'], capture_output=True)
    if check.returncode != 0 or kind != 'head':
        ns.die(f'BASE_AUTHORITY_MISSING: pending first-task base is invalid: {base}')
    state['base_commit'] = base
    state['base_kind'] = kind


def assert_worktree_protocol_current(feature_dir: pathlib.Path, target: pathlib.Path, *, ns: Any) -> None:
    root = ns.repo_root()
    try:
        rel_feature = feature_dir.resolve().relative_to(root)
    except ValueError:
        ns.die('feature_dir must be inside the repository')
    target_feature = target / rel_feature
    if not target_feature.exists():
        ns.die(f'existing task worktree is missing feature specification: {target_feature}')
    if ns.feature_fingerprint(target_feature) != ns.feature_fingerprint(feature_dir):
        ns.die(f'existing task worktree contains stale spec/plan/tasks: {target}')
    # Worktree code/documentation fingerprints change during ordinary harness maintenance.
    # State compatibility is governed by the explicit semantic protocol version.
    if ns.protocol_version(target_feature) != ns.protocol_version(feature_dir):
        ns.die(f'existing task worktree contains incompatible lifecycle protocol: {target}')


def task_base_commits(task: dict[str, Any], state: dict[str, Any], *, ns: Any) -> list[tuple[str, str]]:
    """Return the immutable commits a task branch must contain."""
    dependencies = task.get('depends_on', [])
    if dependencies:
        result: list[tuple[str, str]] = []
        for dependency in dependencies:
            commit = state['tasks'][dependency].get('checkpoint_commit')
            if not ns._valid_completion_commit(commit):
                ns.die(f'WORKTREE_BASE_UNAVAILABLE: dependency {dependency} has no full checkpoint commit')
            result.append((dependency, commit))
        return result
    base = state.get('base_commit')
    if not ns._valid_completion_commit(base):
        ns.die('WORKTREE_BASE_UNAVAILABLE: dependency-free task has no full feature base commit')
    return [('feature base', base)]


def require_task_commit_ancestry(feature_dir: pathlib.Path, task: dict[str, Any],
                                 state: dict[str, Any], candidate: str, *,
                                 operation: str, ns: Any) -> None:
    """Fail closed unless a task checkpoint contains its accepted base commits."""
    if not ns._valid_completion_commit(candidate):
        ns.die(f'{operation}_BASE_INVALID: candidate is not a full Git commit id')
    repo = ns.feature_repo_base(feature_dir)
    exists = subprocess.run(['git', 'cat-file', '-e', f'{candidate}^{{commit}}'], cwd=repo,
                            capture_output=True).returncode == 0
    if not exists:
        ns.die(f'{operation}_BASE_INVALID: candidate commit is unavailable')
    for label, required in ns.task_base_commits(task, state):
        present = subprocess.run(['git', 'cat-file', '-e', f'{required}^{{commit}}'], cwd=repo,
                                 capture_output=True).returncode == 0
        if not present:
            ns.die(f'{operation}_BASE_UNAVAILABLE: required {label} checkpoint is unavailable')
        ancestry = subprocess.run(['git', 'merge-base', '--is-ancestor', required, candidate], cwd=repo,
                                  capture_output=True)
        if ancestry.returncode != 0:
            ns.die(f'{operation}_STALE_BASE: candidate does not descend from {label} checkpoint {required}')


def prepare_task_worktree(feature_dir: pathlib.Path, doc: dict[str, Any], state: dict[str, Any],
                          task: dict[str, Any], *,
                          durable_state: dict[str, Any] | None = None, ns: Any) -> pathlib.Path:
    feature = str(doc.get('feature', feature_dir.name))
    target = ns.worktree_path(feature, task['id'])
    if durable_state is None:
        durable_state = state
    dep_commits: list[str] = []
    for dep in task.get('depends_on', []):
        commit = state['tasks'][dep].get('checkpoint_commit')
        if not commit:
            ns.die(
                f'dependency {dep} has no local checkpoint commit; '
                'complete it from its task worktree before creating this dependent worktree'
            )
        if commit not in dep_commits:
            dep_commits.append(commit)

    first_base_missing = not task.get('depends_on') and state.get('base_commit') is None
    ns.stage_first_task_base(feature_dir, state, durable_state, task, target)
    if target.exists():
        dirty = ns.changed_paths(target)
        if dirty:
            ns.die(f'existing task worktree is dirty; fail/release/checkpoint it before reuse: {dirty}')
        ns.assert_worktree_protocol_current(feature_dir, target)
        reused_head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=target,
                                     capture_output=True, text=True)
        if reused_head.returncode != 0:
            ns.die(f'WORKTREE_BASE_INVALID: cannot resolve existing task worktree HEAD: {target}')
        ns.require_task_commit_ancestry(feature_dir, task, state, reused_head.stdout.strip(),
                                     operation='WORKTREE')
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    branch = f'agent/{feature}/{task["id"]}'
    if subprocess.run(['git', 'show-ref', '--verify', '--quiet', f'refs/heads/{branch}']).returncode == 0:
        ns.die(f'local task branch already exists without a worktree: {branch}; delete it deliberately before recreation')

    if dep_commits:
        base = dep_commits[0]
    elif first_base_missing:
        # Use the exact committed snapshot that was durably recorded as the
        # first historical base; a second HEAD read here could race a commit.
        base = state['base_commit']
    else:
        base = ns.task_worktree_snapshot(feature_dir)
    subprocess.run(['git', 'worktree', 'add', '--quiet', str(target), '-b', branch, base], check=True)
    try:
        for commit in dep_commits[1:]:
            subprocess.run(
                [
                    'git', '-c', 'user.name=Agent Harness',
                    '-c', 'user.email=agent-harness@local.invalid',
                    'merge', '--quiet', '--no-edit', '--no-gpg-sign', commit,
                ],
                cwd=target,
                check=True,
            )
        if ns.changed_paths(target):
            ns.die(f'new worktree is unexpectedly dirty: {target}')

        root = ns.repo_root()
        try:
            rel_feature = feature_dir.resolve().relative_to(root)
        except ValueError:
            ns.die('feature_dir must be inside the repository')
        required = [
            target / rel_feature / 'spec.md',
            target / rel_feature / 'plan.md',
            target / rel_feature / 'tasks.json',
            target / 'AGENTS.md',
            target / 'docs' / 'agentic-sdd' / 'constitution.md',
            target / 'tooling' / 'agent-harness' / 'harness.py',
        ]
        missing = [str(p.relative_to(target)) for p in required if not p.exists()]
        if missing:
            ns.die(
                'worktree base is missing SDD/spec files: '
                f'{missing}. Reset/re-plan the feature; the harness can synthesize a local base without moving your branch.'
            )

        snapshot = ns.task_worktree_snapshot(feature_dir)
        protocol_paths = ns.execution_protocol_paths(feature_dir)
        needs_refresh = (
            ns.feature_fingerprint(target / rel_feature) != ns.feature_fingerprint(feature_dir)
            or ns.protocol_version(target / rel_feature) != ns.protocol_version(feature_dir)
        )
        if needs_refresh:
            path_diff = subprocess.run(
                ['git', 'diff', '--quiet', 'HEAD', snapshot, '--', *protocol_paths], cwd=target
            )
            if path_diff.returncode not in (0, 1):
                ns.die(f'cannot compare task worktree protocol snapshot: {target}')
            stale_paths = path_diff.returncode == 1
        else:
            stale_paths = False
        if stale_paths:
            root_paths = subprocess.run(['git', 'ls-tree', '-r', '--name-only', snapshot],
                                        cwd=ns.repo_root(), capture_output=True, text=True, check=True).stdout.splitlines()
            target_paths = subprocess.run(['git', 'ls-files'], cwd=target,
                                          capture_output=True, text=True, check=True).stdout.splitlines()
            source_selected = [path for path in root_paths if ns._path_is_selected(path, protocol_paths)]
            target_selected = [path for path in target_paths if ns._path_is_selected(path, protocol_paths)]
            removed = sorted(set(target_selected) - set(source_selected))
            if removed:
                subprocess.run(['git', 'rm', '-f', '--', *removed], cwd=target, check=True,
                               capture_output=True)
            if source_selected:
                subprocess.run(
                    ['git', 'restore', '--source', snapshot, '--staged', '--worktree', '--', *source_selected],
                    cwd=target, check=True,
                )
            subprocess.run(
                ['git', '-c', 'user.name=Agent Harness', '-c', 'user.email=agent-harness@local.invalid',
                 'commit', '--no-gpg-sign', '-m', f'agent orchestration refresh {feature}'],
                cwd=target, check=True,
            )

        ns.assert_worktree_protocol_current(feature_dir, target)
    except BaseException as original:
        cleanup_errors = []
        removed = subprocess.run(['git', 'worktree', 'remove', '--force', str(target)],
                                 cwd=ns.repo_root(), capture_output=True, text=True)
        if removed.returncode != 0 and target.exists():
            cleanup_errors.append(removed.stderr.strip() or 'worktree removal failed')
        if not target.exists():
            deleted = subprocess.run(['git', 'branch', '-D', branch], cwd=ns.repo_root(),
                                     capture_output=True, text=True)
            if deleted.returncode != 0:
                cleanup_errors.append(deleted.stderr.strip() or 'task branch removal failed')
        if cleanup_errors:
            raise RuntimeError(f'{original}; cleanup incomplete: {"; ".join(cleanup_errors)}') from original
        raise
    return target


def cmd_start(args: argparse.Namespace, *, ns: Any) -> None:
    doc = ns.load_validated(args.feature_dir)
    idx = ns.task_index(doc)
    task = idx.get(args.task_id)
    if not task:
        ns.die(f'unknown task {args.task_id}')
    with ns.locked_state(args.feature_dir, doc) as state:
        if args.task_id not in ns.ready_ids(doc, state, args.feature_dir):
            ns.die(f'{args.task_id} is not ready')
        original = state['tasks'][args.task_id]
        task = ns.active_task_contract(args.feature_dir, doc, args.task_id, state=state)
        resume = original.get('verification_resume_pending')
        if isinstance(resume, dict):
            ns.validate_pending_verification_resume(original)
            if ns.is_unrecovered_partial_claim(original):
                ns.die(f'CLAIM_RECOVERY_REQUIRED: {args.task_id} has an unrecovered exceptional claim')
            target = ns.worktree_path(str(doc.get('feature', args.feature_dir.name)), args.task_id)
            if not target.is_dir() or target.is_symlink():
                ns.die('VERIFICATION_RESUME_INVALID: the exact task worktree is unavailable')
            try:
                plan = ns.resolve_accepted_verification_plan(
                    target, resume.get('plan_id'), allowed_task_statuses={'failed'})
            except Exception as exc:
                ns.die(f'VERIFICATION_RESUME_INVALID: accepted plan changed before start ({type(exc).__name__})')
            if (resume.get('task_attempt') != original.get('attempts') or
                    resume.get('lifecycle_generation') != state.get('feature_generation', 1) or
                    plan.get('plan_id') != resume.get('plan_id') or
                    plan.get('task_id') != args.task_id or
                    plan.get('task_attempt') != original.get('attempts')):
                ns.die('VERIFICATION_RESUME_INVALID: resume tuple changed before start')
            if resume.get('prior_result_sha256') != ns.prior_builder_result_digest(
                    args.feature_dir, original, args.task_id, resume.get('prior_result_path')):
                ns.die('VERIFICATION_RESUME_INVALID: prior builder result changed before start')
            now = ns.utc_now()
            entry = state['tasks'][args.task_id]
            ns.retire_finalized_completion_repair_pointers(
                args.feature_dir, args.task_id, entry, retired_by='start')
            entry['status'] = 'running'
            entry['owner'] = args.owner
            entry['claimed_at'] = now.isoformat()
            entry['worktree'] = str(target)
            entry['verification_resume_active'] = dict(resume)
            entry.pop('verification_resume_pending', None)
            entry.pop('control_outcome', None)
            ns.refresh_lease(entry, doc, now=now)
            packet = ns.resolve_active_packet(args.feature_dir, doc, args.task_id, state=state)['path']
            packet = pathlib.Path(packet)
        else:
        # Resolve and validate the packet before consuming retry authorization or changing
        # lifecycle state. The immutable publication may safely remain orphaned on failure.
            packet = ns.write_packet(doc, task, args.feature_dir, state=state)
            staged_state = copy.deepcopy(state)
            entry = staged_state['tasks'][args.task_id]
            ns.retire_finalized_completion_repair_pointers(
                args.feature_dir, args.task_id, entry, retired_by='start')
            if ns.is_unrecovered_partial_claim(entry):
                ns.die(f'CLAIM_RECOVERY_REQUIRED: {args.task_id} has an unrecovered exceptional claim')
            ns.consume_attempt_authorization(entry, args.task_id, doc, args.feature_dir, staged_state)
            target = ns.prepare_task_worktree(args.feature_dir, doc, staged_state, task, durable_state=state)
            now = ns.utc_now()
            entry.update({
                'status': 'running',
                'owner': args.owner,
                'attempts': int(entry.get('attempts', 0)) + 1,
                'claimed_at': now.isoformat(),
                'worktree': str(target),
                'start_origin_status': original.get('status'),
            })
            ns.append_attempt_binding(entry, args.feature_dir, doc, args.task_id, packet)
            ns.refresh_lease(entry, doc, now=now)
            state['tasks'][args.task_id] = entry
            for key in ('base_commit', 'base_kind'):
                if key in staged_state:
                    state[key] = staged_state[key]
            state.pop('pending_base_commit', None)
            state.pop('pending_base_kind', None)
    print(json.dumps({'task': args.task_id, 'owner': args.owner, 'worktree': str(target), 'packet': str(packet)}, indent=2))


def rollback_unexecuted_start(feature_dir: pathlib.Path, doc: dict[str, Any], task_id: str, owner: str, reason: str, *, ns: Any) -> None:
    """Undo a start that failed before a provider process was launched.

    This does not consume rework budget and only succeeds for a clean task worktree.
    """
    feature = str(doc.get('feature', feature_dir.name))
    target = ns.worktree_path(feature, task_id)
    if target.exists() and ns.changed_paths(target):
        ns.die(f'cannot roll back unexecuted start for dirty worktree {target}')
    with ns.locked_state(feature_dir, doc) as state:
        entry = state['tasks'][task_id]
        ns.owner_guard(entry, owner)
        if entry.get('status') != 'running':
            ns.die(f'{task_id} is not running')
        resume = entry.pop('verification_resume_active', None)
        if isinstance(resume, dict):
            entry['status'] = 'failed'
            entry['verification_resume_pending'] = resume
            entry['start_rollback_reason'] = reason
            for key in ('owner', 'claimed_at', 'heartbeat_at', 'lease_expires_at'):
                entry.pop(key, None)
            return
        prior_status = entry.pop('start_origin_status', None)
        entry['status'] = prior_status if prior_status in {'pending', 'failed'} else 'pending'
        entry['attempts'] = max(0, int(entry.get('attempts', 0)) - 1)
        ns.restore_attempt_authorization(entry)
        entry['start_rollback_reason'] = reason
        for key in ('owner', 'claimed_at', 'heartbeat_at', 'lease_expires_at', 'worktree'):
            entry.pop(key, None)


def cmd_heartbeat(args: argparse.Namespace, *, ns: Any) -> None:
    doc = ns.load_validated(args.feature_dir)
    expires = ns.heartbeat(args.feature_dir, doc, args.task_id, args.owner)
    print(f'HEARTBEAT {args.task_id} lease_expires_at={expires}')
