"""Read-only lifecycle checks the transitions depend on (AH5-14b-2, step 1).

Verbatim from Showcase `tooling/agent-harness/harness.py` at `792dfad`: the verification-resume validators, the
accepted-plan resolver, the partial-claim check and the recovery-identity readers, with the helpers they call. Master
(2026-10-24) moved them with the transitions (option A): they read and refuse; none writes state, repairs data or
starts recovery. The admission operations that change state stay for AH5-14c, the mutating recovery steps for AH5-14d.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
from collections import namedtuple
from collections.abc import Callable
from typing import Any

from .feature import die, sha256_bytes
from .feature_access import StateAccess, canonical_json_sha256

PACKET_PAYLOAD_IDENTITY_SCHEME = 'canonical-packet-payload-sha256-v1'
PACKET_REVISION_IDENTITY_SCHEME = 'canonical-packet-revision-sha256-v1'
RECOVERY_PACKET_IDENTITY_SCHEMES = {1: PACKET_PAYLOAD_IDENTITY_SCHEME}
PacketIdentity = namedtuple('PacketIdentity', ('scheme', 'value'))


def parse_aware_timestamp(value: Any) -> dt.datetime | None:
    """Parse only timestamps that carry an explicit timezone offset."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def validate_verification_resume(entry: dict[str, Any], field: str) -> dict[str, Any] | None:
    """Validate a pending/active resume against its latest immutable history item."""
    active = entry.get(field)
    if active is None:
        return None
    history = entry.get('verification_resume_history')
    required = ('plan_id', 'task_attempt', 'lifecycle_generation', 'reason_sha256',
                'requested_at', 'operator', 'prior_result_sha256', 'prior_result_path')
    if not isinstance(active, dict) or not isinstance(history, list) or not history:
        die('VERIFICATION_RESUME_STATE_INVALID: active resume does not match durable history')
    latest = history[-1]
    if not isinstance(latest, dict) or not all(active.get(key) == latest.get(key) for key in required):
        die('VERIFICATION_RESUME_STATE_INVALID: active resume does not match durable history')
    if (not isinstance(active.get('plan_id'), str) or not active['plan_id'] or
            type(active.get('task_attempt')) is not int or active['task_attempt'] != entry.get('attempts') or
            type(active.get('lifecycle_generation')) is not int or
            not isinstance(active.get('reason_sha256'), str) or
            not re.fullmatch(r'[0-9a-f]{64}', active['reason_sha256']) or
            not isinstance(active.get('requested_at'), str) or
            not isinstance(active.get('operator'), str) or not active['operator'].strip() or
            not isinstance(active.get('prior_result_sha256'), str) or
            not re.fullmatch(r'[0-9a-f]{64}', active['prior_result_sha256']) or
            not isinstance(active.get('prior_result_path'), str) or not active['prior_result_path']):
        die('VERIFICATION_RESUME_STATE_INVALID: active resume tuple is malformed')
    if active.get('requested_at') != latest.get('requested_at') or parse_aware_timestamp(active['requested_at']) is None:
        die('VERIFICATION_RESUME_STATE_INVALID: resume request timestamp is invalid')
    return active


def validate_active_verification_resume(entry: dict[str, Any]) -> dict[str, Any] | None:
    return validate_verification_resume(entry, 'verification_resume_active')


def validate_pending_verification_resume(entry: dict[str, Any]) -> dict[str, Any] | None:
    return validate_verification_resume(entry, 'verification_resume_pending')


def is_unrecovered_partial_claim(entry: dict[str, Any]) -> bool:
    return (entry.get('status') == 'running' and int(entry.get('attempts', 0)) == 4 and
            isinstance(entry.get('active_retry_authorization'), str) and
            not entry.get('claim_recovery'))


def canonical_packet_payload_sha256(packet: dict[str, Any]) -> str:
    """Hash the normalized packet payload, excluding its embedded checksum."""
    body = {key: value for key, value in packet.items() if key != 'packet_sha256'}
    return sha256_bytes(json.dumps(body, sort_keys=True, separators=(',', ':')).encode())


def packet_revision_id(packet: dict[str, Any]) -> str:
    body = {key: value for key, value in packet.items() if key != 'packet_revision_id'}
    return 'sha256:' + sha256_bytes(json.dumps(body, sort_keys=True, separators=(',', ':')).encode())


def packet_identity_from_embedded_checksum(packet: Any) -> PacketIdentity | None:
    """Validate and name the legacy packet's canonical payload checksum."""
    if not isinstance(packet, dict):
        return None
    stored = packet.get('packet_sha256')
    if (not isinstance(stored, str) or re.fullmatch(r'[0-9a-f]{64}', stored) is None or
            stored != canonical_packet_payload_sha256(packet)):
        return None
    return PacketIdentity(PACKET_PAYLOAD_IDENTITY_SCHEME, stored)


def packet_identity_from_revision(packet: Any) -> PacketIdentity | None:
    """Name the M1 content revision only when it derives from a valid packet."""
    if packet_identity_from_embedded_checksum(packet) is None:
        return None
    return PacketIdentity(PACKET_REVISION_IDENTITY_SCHEME, packet_revision_id(packet))


def packet_identity_from_recovery(recovery: Any) -> PacketIdentity | None:
    """Infer a bare recovery identity's scheme only from its trusted record version."""
    if not isinstance(recovery, dict):
        return None
    version = recovery.get('recovery_version')
    scheme = (RECOVERY_PACKET_IDENTITY_SCHEMES.get(version)
              if isinstance(version, int) and not isinstance(version, bool) else None)
    value = recovery.get('packet_identity')
    if scheme is None or not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None:
        return None
    return PacketIdentity(scheme, value)


def recovery_proves_packet_binding(recovery: Any, packet: Any,
                                   revision_id: str, contract_sha256: str) -> bool:
    """Prove a recovery checksum and M1 revision identify the same packet object."""
    recovered = packet_identity_from_recovery(recovery)
    embedded = packet_identity_from_embedded_checksum(packet)
    revision = packet_identity_from_revision(packet)
    expected_revision = PacketIdentity(PACKET_REVISION_IDENTITY_SCHEME, revision_id)
    return bool(
        recovered is not None and embedded is not None and revision is not None and
        recovered == embedded and revision == expected_revision and
        isinstance(recovery, dict) and recovery.get('semantic_contract_sha256') == contract_sha256
    )


def recovery_record_identity(recovery: Any) -> str | None:
    if not isinstance(recovery, dict):
        return None
    return f'sha256:{canonical_json_sha256(recovery)}'


def resolve_accepted_verification_plan(repository: pathlib.Path, plan_id: str, *,
                                       allowed_task_statuses: set[str] | None = None, access: StateAccess,
                                       feature_dir_for: Callable[[pathlib.Path, str], pathlib.Path],
                                       verification_authority: Callable[[], Any]) -> dict:
    """Resolve subordinate plan bytes and prove current .agent-state acceptance."""
    store_module = access.verification_store()  # the consumer's modules, looked up per call (its tests patch them)
    VerificationStore, StoreError = store_module.VerificationStore, store_module.StoreError
    store = VerificationStore(repository)
    record = store.load_plan_record(plan_id)
    feature_dir = feature_dir_for(pathlib.Path(repository), record['feature_id'])
    try:
        doc = access.load_validated(feature_dir)
        state = access.read_state_pure(feature_dir, doc)
    except SystemExit:
        raise StoreError('ACCEPTED_PLAN_UNAVAILABLE') from None
    current = state.get('verification_authority')
    generation = state.get('feature_generation', 1)
    task_state = state.get('tasks', {}).get(record.get('task_id'))
    expected = {key: record.get(key) for key in ('schema_version', 'profile_id', 'plan_id', 'task_id', 'task_attempt', 'feature_fingerprint',
        'lifecycle_generation', 'family', 'profile_hash', 'policy_checkpoint',
        'candidate_identity', 'final_changed_surface_id', 'origin_binding')}
    statuses = allowed_task_statuses or {'running'}
    if (not isinstance(current, dict) or current.get('accepted_plan_id') != plan_id or
            current.get('generation') != generation or generation != record.get('lifecycle_generation') or
            record.get('feature_fingerprint') != access.fingerprint(feature_dir) or
            not isinstance(task_state, dict) or task_state.get('status') not in statuses or
            task_state.get('attempts') != record.get('task_attempt') or
            current.get('binding') != expected):
        raise StoreError('ACCEPTED_PLAN_UNAVAILABLE')
    validate_plan_record = verification_authority().validate_plan_record
    validate_plan_record(record, repository=repository, reconstruct=True)
    return record
