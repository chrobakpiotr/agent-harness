#!/usr/bin/env python3
"""Structured provenance and usage telemetry for agentic SDD runs.

All data stays in ignored .agent-runs/. No prompt/secret content is copied into provenance.json;
raw provider stdout/stderr remain separate local artifacts.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import re
import stat
import tempfile
import uuid
from typing import Any

# Provenance and usage telemetry live in agent_harness.telemetry (AH5-06); manual evidence (signed Ed25519
# attestations, trusted issuer registry, lifecycle registration) and the CLI stay here because they need the
# Showcase lifecycle. Verbatim from Showcase `49ab97f` `telemetry.py` lines 255-745 (docs/migration/provenance.md).
from agent_harness.telemetry import *

_MANUAL_ROLES = {'reviewer', 'evaluator', 'architecture-reviewer', 'verification-author'}
_MANUAL_VERDICTS = {'PASS', 'FAIL', 'NEEDS-HUMAN'}
_MANUAL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')
_MANUAL_HASH = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
_MANUAL_ATTESTATION_FIELDS = {
    'schema_version', 'attestation_type', 'attestation_id', 'issuer_registry_id',
    'issuer_registry_checkpoint', 'issuer_registry_sha256', 'issuer_id',
    'reviewer_principal', 'key_fingerprint', 'signature_algorithm', 'role', 'verdict',
    'report_sha256', 'completed_at', 'repository_id', 'feature_id', 'checkpoint_id',
    'candidate_identity', 'final_surface_identity', 'task_id', 'attempt', 'plan_id',
    'family_id', 'plan_acceptance_transition_id', 'lifecycle_generation', 'obligation_ids',
}


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('MANUAL_EVIDENCE_ATTESTATION_INVALID')
        result[key] = value
    return result


def _manual_registry_context(repository: pathlib.Path) -> tuple[dict[str, Any], str, str]:
    """Load only the committed local trusted issuer registry and bind its provenance."""
    registry_path = repository / 'tooling' / 'agent-harness' / 'human-issuer-registry.json'
    if registry_path.is_symlink() or not registry_path.is_file():
        raise ValueError('MANUAL_EVIDENCE_ATTESTATION_INVALID')
    raw = registry_path.read_bytes()
    try:
        committed = __import__('subprocess').check_output(
            ['git', '-C', str(repository), 'show', f'HEAD:{registry_path.relative_to(repository).as_posix()}'],
            stderr=__import__('subprocess').DEVNULL)
        checkpoint = __import__('subprocess').check_output(
            ['git', '-C', str(repository), 'log', '-1', '--format=%H', 'HEAD', '--',
             registry_path.relative_to(repository).as_posix()], text=True,
            stderr=__import__('subprocess').DEVNULL).strip()
    except (OSError, __import__('subprocess').CalledProcessError):
        raise ValueError('MANUAL_EVIDENCE_ATTESTATION_INVALID') from None
    if not checkpoint or raw != committed:
        raise ValueError('MANUAL_EVIDENCE_ATTESTATION_INVALID')
    try:
        registry = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError('MANUAL_EVIDENCE_ATTESTATION_INVALID') from None
    if (not isinstance(registry, dict) or type(registry.get('schema_version')) is not int or
            registry['schema_version'] != 1 or not isinstance(registry.get('issuers'), list)):
        raise ValueError('MANUAL_EVIDENCE_ATTESTATION_INVALID')
    return registry, checkpoint, 'sha256:' + hashlib.sha256(raw).hexdigest()


def _verify_manual_attestation(payload: bytes, *, repository: pathlib.Path,
                               expected: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Verify canonical detached Ed25519 proof against trusted registry and scope."""
    if len(payload) > 65536 or payload.startswith(b'\xef\xbb\xbf') or payload.endswith(b'\n'):
        raise ValueError('MANUAL_EVIDENCE_ATTESTATION_INVALID')
    try:
        outer = json.loads(payload.decode('utf-8', errors='strict'), object_pairs_hook=_unique_json_object)
        from verification.serialization import canonical_jcs
        if (not isinstance(outer, dict) or set(outer) != {'envelope', 'signature'} or
                canonical_jcs(outer) != payload):
            raise ValueError()
        envelope = outer['envelope']
        signature_text = outer['signature']
        if (not isinstance(envelope, dict) or set(envelope) != _MANUAL_ATTESTATION_FIELDS or
                not isinstance(signature_text, str) or not re.fullmatch(r'[A-Za-z0-9_-]{86}', signature_text)):
            raise ValueError()
        signature = base64.urlsafe_b64decode(signature_text + '==')
        if len(signature) != 64 or base64.urlsafe_b64encode(signature).decode('ascii').rstrip('=') != signature_text:
            raise ValueError()
        registry, registry_checkpoint, registry_sha = _manual_registry_context(repository)
        issuer_id = envelope.get('issuer_id')
        matches = [entry for entry in registry['issuers'] if isinstance(entry, dict) and entry.get('issuer_id') == issuer_id]
        if len(matches) != 1:
            raise ValueError()
        issuer = matches[0]
        actions = issuer.get('actions')
        if (issuer.get('enabled') is not True or issuer.get('revoked') is not False or
                not isinstance(actions, list) or any(not isinstance(action, str) for action in actions) or
                'manual-review' not in actions):
            raise ValueError()
        public_key = base64.b64decode(issuer['public_key_ed25519'], validate=True)
        key_fingerprint = 'sha256:' + hashlib.sha256(public_key).hexdigest()
        principal = issuer.get('reviewer_principal', issuer.get('principal'))
        if (len(public_key) != 32 or issuer.get('key_fingerprint') != key_fingerprint or
                not isinstance(principal, str) or not principal):
            raise ValueError()
        if (type(envelope.get('schema_version')) is not int or envelope.get('schema_version') != 1 or envelope.get('attestation_type') != 'manual-review-attestation-v1' or
                envelope.get('issuer_registry_id') != 'trusted-human-issuer-registry-v1' or
                envelope.get('issuer_registry_checkpoint') != registry_checkpoint or
                envelope.get('issuer_registry_sha256') != registry_sha or envelope.get('issuer_id') != issuer_id or
                envelope.get('reviewer_principal') != principal or envelope.get('key_fingerprint') != key_fingerprint or
                envelope.get('signature_algorithm') != 'Ed25519' or envelope.get('role') not in _MANUAL_ROLES or
                envelope.get('verdict') not in _MANUAL_VERDICTS):
            raise ValueError()
        if envelope != {**expected, 'issuer_registry_checkpoint': registry_checkpoint,
                        'issuer_registry_sha256': registry_sha, 'issuer_id': issuer_id,
                        'reviewer_principal': principal, 'key_fingerprint': key_fingerprint,
                        'signature_algorithm': 'Ed25519', 'attestation_type': 'manual-review-attestation-v1',
                        'issuer_registry_id': 'trusted-human-issuer-registry-v1', 'schema_version': 1,
                        'attestation_id': envelope.get('attestation_id')}:
            raise ValueError()
        parsed_uuid = uuid.UUID(envelope['attestation_id'])
        if str(parsed_uuid) != envelope['attestation_id'] or parsed_uuid.version != 4:
            raise ValueError()
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, canonical_jcs(envelope))
        return envelope, hashlib.sha256(payload).hexdigest()
    except ValueError as exc:
        if str(exc).startswith('MANUAL_EVIDENCE_'):
            raise
        raise ValueError('MANUAL_EVIDENCE_ATTESTATION_INVALID') from None
    except Exception:
        raise ValueError('MANUAL_EVIDENCE_ATTESTATION_INVALID') from None


def _read_manual_input(repository: pathlib.Path, relative_name: str, *, limit: int,
                       reason: str) -> tuple[bytes, pathlib.Path]:
    relative = pathlib.PurePosixPath(relative_name)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError(reason)
    candidate = repository.joinpath(*relative.parts)
    if candidate.is_symlink():
        raise ValueError(reason)
    try:
        resolved = candidate.resolve(strict=True)
        store_root = (repository / '.agent-runs').resolve(strict=True)
        resolved.relative_to(store_root)
        fd = os.open(resolved, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except (OSError, ValueError):
        raise ValueError(reason) from None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise ValueError(reason)
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b''.join(chunks)
        after = os.fstat(fd)
        current = resolved.stat(follow_symlinks=False)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_nlink, item.st_mode,
                                 item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if (len(payload) != before.st_size or identity(before) != identity(after) or
                identity(after) != identity(current)):
            raise ValueError(reason + '_RACE')
        return payload, resolved
    finally:
        os.close(fd)


def _store_manual_snapshot(path: pathlib.Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    except FileExistsError:
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ValueError('MANUAL_EVIDENCE_ATTESTATION_INVALID') from None
        return
    with os.fdopen(fd, 'wb') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def verify_manual_observation_for_coverage(repository: pathlib.Path,
                                          observation: dict[str, Any]) -> dict[str, Any]:
    """Re-verify immutable report and signature bytes before any coverage CAS.

    This is deliberately separate from registration: the lifecycle owner must call
    it immediately before its own coverage transaction and still perform that CAS.
    """
    repository = pathlib.Path(repository).resolve(strict=True)
    if (not isinstance(observation, dict) or observation.get('record_type') != 'manual-observation' or
            type(observation.get('schema_version')) is not int or observation.get('schema_version') != 1 or
            not re.fullmatch(r'sha256:[0-9a-f]{64}', str(observation.get('attestation_sha256', ''))) or
            not re.fullmatch(r'sha256:[0-9a-f]{64}', str(observation.get('report_sha256', '')))):
        raise ValueError('MANUAL_EVIDENCE_ATTESTATION_INVALID')
    attestation_digest = observation['attestation_sha256'].removeprefix('sha256:')
    report_digest = observation['report_sha256'].removeprefix('sha256:')
    attestation_bytes, _ = _read_manual_input(
        repository, f'.agent-runs/manual-attestations/{attestation_digest}.json', limit=65536,
        reason='MANUAL_EVIDENCE_ATTESTATION_INVALID')
    report_bytes, _ = _read_manual_input(
        repository, f'.agent-runs/manual-report-snapshots/{report_digest}.md', limit=1024 * 1024,
        reason='MANUAL_EVIDENCE_ATTESTATION_INVALID')
    if (hashlib.sha256(attestation_bytes).hexdigest() != attestation_digest or
            hashlib.sha256(report_bytes).hexdigest() != report_digest):
        raise ValueError('MANUAL_EVIDENCE_ATTESTATION_INVALID')
    fields = _manual_report_fields(report_bytes)
    try:
        outer = json.loads(attestation_bytes.decode('utf-8'), object_pairs_hook=_unique_json_object)
        envelope = outer['envelope']
        scope = observation.get('scope')
        if not isinstance(scope, dict):
            raise ValueError()
        expected = {
            'attestation_id': envelope.get('attestation_id'), 'role': observation['role'],
            'verdict': fields['Verdict'], 'report_sha256': observation['report_sha256'],
            'completed_at': fields['Completed at'], 'repository_id': observation['repository_id'],
            'feature_id': observation['feature_id'], 'checkpoint_id': fields['Reviewed checkpoint'],
            'candidate_identity': None, 'final_surface_identity': None,
            'task_id': scope.get('task_id'), 'attempt': scope.get('task_attempt'),
            'plan_id': None, 'family_id': None, 'plan_acceptance_transition_id': None,
            'lifecycle_generation': None, 'obligation_ids': [],
        }
        if fields['Feature'] != observation['feature_id']:
            raise ValueError()
        _verify_manual_attestation(attestation_bytes, repository=repository, expected=expected)
    except Exception:
        raise ValueError('MANUAL_EVIDENCE_ATTESTATION_INVALID') from None
    return {'attestation_sha256': observation['attestation_sha256'],
            'report_sha256': observation['report_sha256'],
            'reviewer_principal': envelope['reviewer_principal'],
            'completed_at': envelope['completed_at'], 'checkpoint_id': envelope['checkpoint_id']}
def _manual_report_fields(payload: bytes) -> dict[str, str]:
    try:
        text = payload.decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID') from None
    if len(payload) > 1024 * 1024:
        raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID')
    # Secret-bearing reports are rejected before hashing; only the safe digest
    # and path may enter durable telemetry.
    from verification.serialization import default_safety
    safety = default_safety()
    if not safety.safe(text):
        raise ValueError('MANUAL_EVIDENCE_REPORT_SECRET')
    fields = {}
    fenced = False
    html_comment = False
    html_block = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(('```', '~~~')):
            fenced = not fenced
            continue
        if fenced or stripped.startswith('>') or (line and line[0].isspace()):
            continue
        if html_block:
            if not stripped:
                html_block = False
            continue
        if '<!--' in line:
            html_comment = True
        if html_comment:
            if '-->' in line:
                html_comment = False
            continue
        if stripped.startswith('<') and not re.match(r'^(Feature|Reviewed checkpoint|Verdict|Completed at|Task):', line):
            html_block = True
            continue
        match = re.fullmatch(r'(Feature|Reviewed checkpoint|Completed at|Task): `([^`\r\n]+)`', line)
        verdict_match = re.fullmatch(r'Verdict: \*\*(PASS|FAIL|NEEDS-HUMAN)\*\*', line)
        if match or verdict_match:
            name, value = (match.groups() if match else ('Verdict', verdict_match.group(1)))
            if name in fields:
                raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID')
            fields[name] = value
    if not {'Feature', 'Reviewed checkpoint', 'Verdict', 'Completed at'} <= set(fields):
        raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID')
    if fields['Verdict'] not in _MANUAL_VERDICTS:
        raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', fields['Completed at']):
        raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID')
    try:
        completed = dt.datetime.fromisoformat(fields['Completed at'].replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID') from None
    if completed > utc_now().replace(microsecond=0):
        raise ValueError('MANUAL_EVIDENCE_CHRONOLOGY_INVALID')
    return fields


def record_manual(*, repo: pathlib.Path, feature: str, role: str, provider: str,
                  checkpoint: str, verdict: str, report: str, task: str | None = None,
                  task_attempt: str | None = None, plan_id: str | None = None,
                  provider_run_id: str | None = None, attestation: str | None = None) -> pathlib.Path:
    """Record safe manual provenance only; never changes task lifecycle state."""
    if (not _MANUAL_ID.fullmatch(feature or '') or role not in _MANUAL_ROLES or
            not provider or not _MANUAL_HASH.fullmatch(checkpoint or '') or verdict not in _MANUAL_VERDICTS):
        raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID')
    from verification.store import StoreError, VerificationStore
    root = pathlib.Path(repo).resolve(strict=True)
    store = VerificationStore(root)
    primary = store.root.parents[2]
    # Check registry commit binding before other checkpoint checks can mask a
    # locally modified trust source as an ordinary dirty-worktree rejection.
    if attestation:
        _manual_registry_context(primary)
    manual_root = store.root / 'manual-reports'
    manual_root.mkdir(parents=True, exist_ok=True)
    relative = pathlib.PurePosixPath(report)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID')
    path = (primary / pathlib.Path(*relative.parts))
    if path.is_symlink():
        raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID')
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(manual_root.resolve(strict=True))
    except (OSError, ValueError):
        raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID') from None
    if resolved.is_symlink():
        raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID')
    fd = os.open(resolved, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 1024 * 1024:
            raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID')
        chunks = []
        remaining = 1024 * 1024 + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b''.join(chunks)
        after = os.fstat(fd)
        path_after = path.stat(follow_symlinks=False)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_nlink, item.st_mode,
                                 item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if len(payload) != before.st_size or identity(before) != identity(after) or identity(after) != identity(path_after):
            raise ValueError('MANUAL_EVIDENCE_REPORT_SNAPSHOT_RACE')
    finally:
        os.close(fd)
    fields = _manual_report_fields(payload)
    if fields['Feature'] != feature or fields['Reviewed checkpoint'] != checkpoint or fields['Verdict'] != verdict:
        raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID')
    if plan_id is not None:
        raise ValueError('MANUAL_EVIDENCE_PLAN_BINDING_UNAVAILABLE')
    head = __import__('subprocess').check_output(['git', '-C', str(primary), 'rev-parse', 'HEAD'],
                                                text=True, stderr=__import__('subprocess').DEVNULL).strip()
    if head != checkpoint:
        raise ValueError('MANUAL_EVIDENCE_CHECKPOINT_UNAVAILABLE')
    dirty = __import__('subprocess').check_output(['git', '-C', str(primary), 'status', '--porcelain', '--untracked-files=all'],
                                                  text=True, stderr=__import__('subprocess').DEVNULL)
    if dirty:
        raise ValueError('MANUAL_EVIDENCE_CHECKPOINT_UNAVAILABLE')
    object_type = __import__('subprocess').check_output(['git', '-C', str(primary), 'cat-file', '-t', checkpoint],
                                                       text=True, stderr=__import__('subprocess').DEVNULL).strip()
    if object_type != 'commit':
        raise ValueError('MANUAL_EVIDENCE_CHECKPOINT_UNAVAILABLE')
    if not attestation:
        raise ValueError('MANUAL_EVIDENCE_ATTESTATION_INVALID')
    harness_path = pathlib.Path(__file__).resolve().parent
    if str(harness_path) not in __import__('sys').path:
        __import__('sys').path.insert(0, str(harness_path))
    import harness as lifecycle
    from verification.serialization import canonical_jcs
    if task_attempt is not None and (not isinstance(task_attempt, str) or
                                     not re.fullmatch(r'[1-9][0-9]*', task_attempt)):
        raise ValueError('MANUAL_EVIDENCE_REPORT_ATTEMPT_UNRESOLVED')
    attempt_number = int(task_attempt) if task_attempt is not None else None
    try:
        feature_dir = primary / 'docs' / 'specs' / feature
        lock = getattr(lifecycle, 'lifecycle_state_lock', None)
        resolve_scope = getattr(lifecycle, 'resolve_manual_review_scope', None)
        load_validated = getattr(lifecycle, 'load_validated', None)
        load_state = getattr(lifecycle, '_load_state_unlocked', None)
        if not all(callable(item) for item in (lock, resolve_scope, load_validated, load_state)):
            raise ValueError('MANUAL_EVIDENCE_AUTHORITY_UNAVAILABLE')
        with lock(feature_dir):
            feature_doc = load_validated(feature_dir)
            lifecycle_state = load_state(feature_dir, feature_doc)
            expected_generation = lifecycle_state.get('feature_generation', 1)
            trusted_scope = resolve_scope(
                primary, feature, role=role, task_id=task, task_attempt=attempt_number,
                checkpoint=checkpoint, _lifecycle_state=lifecycle_state)
    except Exception as exc:
        code = str(exc)
        raise ValueError(code if code.startswith('MANUAL_EVIDENCE_') else
                         'MANUAL_EVIDENCE_SCOPE_UNAVAILABLE') from None
    trusted_task = trusted_scope.get('task_id')
    if ((trusted_task is not None and fields.get('Task') != trusted_task) or
            (trusted_task is None and 'Task' in fields)):
        raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID')
    repository_id = hashlib.sha256(os.fsencode(lifecycle.git_common_dir(primary / 'docs' / 'specs' / feature))).hexdigest()
    if (trusted_scope.get('repository_id') != repository_id or
            trusted_scope.get('feature_id') != feature or
            trusted_scope.get('feature_generation', expected_generation) != expected_generation):
        raise ValueError('MANUAL_EVIDENCE_SCOPE_CONFLICT')
    expected_envelope = {
        'attestation_id': None,
        'role': role,
        'verdict': verdict,
        'report_sha256': 'sha256:' + hashlib.sha256(payload).hexdigest(),
        'completed_at': fields['Completed at'],
        'repository_id': repository_id,
        'feature_id': feature,
        'checkpoint_id': checkpoint,
        'candidate_identity': None,
        'final_surface_identity': None,
        'task_id': trusted_scope.get('task_id'),
        'attempt': trusted_scope.get('task_attempt'),
        'plan_id': None,
        'family_id': None,
        'plan_acceptance_transition_id': None,
        'lifecycle_generation': None,
        'obligation_ids': [],
    }
    attestation_payload, _ = _read_manual_input(primary, attestation, limit=65536,
                                                reason='MANUAL_EVIDENCE_ATTESTATION_INVALID')
    signed, attestation_sha = _verify_manual_attestation(
        attestation_payload, repository=primary, expected=expected_envelope)
    # Keep the exact bytes addressable for any later coverage decision. A later
    # coverage consumer must re-run signature and scope validation from these bytes.
    runs_root = primary / '.agent-runs'
    attestation_store = runs_root / 'manual-attestations' / (attestation_sha + '.json')
    report_store = runs_root / 'manual-report-snapshots' / (hashlib.sha256(payload).hexdigest() + '.md')
    _store_manual_snapshot(attestation_store, attestation_payload)
    _store_manual_snapshot(report_store, payload)
    semantic_projection = {
        'repository_id': repository_id, 'feature_id': feature, 'role': role,
        'reviewer_principal': signed['reviewer_principal'], 'verdict': verdict,
        'report_sha256': expected_envelope['report_sha256'], 'completed_at': fields['Completed at'],
        'task_id': trusted_scope.get('task_id'), 'attempt': trusted_scope.get('task_attempt'),
        'plan_id': None, 'family_id': None, 'lifecycle_generation': None,
        'plan_acceptance_transition_id': None, 'checkpoint_id': checkpoint,
        'candidate_identity': None, 'final_surface_identity': None, 'obligation_ids': [],
    }
    observation_id = hashlib.sha256(canonical_jcs(semantic_projection)).hexdigest()
    submit = getattr(lifecycle, 'register_manual_observation', None)
    if not callable(submit):
        raise ValueError('MANUAL_EVIDENCE_AUTHORITY_UNAVAILABLE')
    result = submit(repository=primary, feature_id=feature, observation_id=observation_id,
                    role=role, task_id=trusted_scope.get('task_id'),
                    task_attempt=trusted_scope.get('task_attempt'), checkpoint=checkpoint,
                    attestation_sha256='sha256:' + attestation_sha,
                    report_sha256=expected_envelope['report_sha256'],
                    expected_feature_generation=expected_generation)
    return pathlib.Path(result['record_path'])


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description='Record or summarize local agentic SDD provenance/usage telemetry')
    p.add_argument('--repo', type=pathlib.Path, default=pathlib.Path.cwd())
    p.add_argument('--feature')
    p.add_argument('--orchestration-id')
    sub = p.add_subparsers(dest='command')
    summary = sub.add_parser('summary')
    summary.add_argument('--repo', type=pathlib.Path, default=pathlib.Path.cwd())
    summary.add_argument('--feature')
    summary.add_argument('--orchestration-id')
    manual = sub.add_parser('record-manual')
    manual.add_argument('--repo', type=pathlib.Path, default=pathlib.Path.cwd())
    manual.add_argument('--feature', required=True)
    manual.add_argument('--role', required=True, choices=sorted(_MANUAL_ROLES))
    manual.add_argument('--provider', required=True)
    manual.add_argument('--checkpoint', required=True)
    manual.add_argument('--verdict', required=True, choices=sorted(_MANUAL_VERDICTS))
    manual.add_argument('--report', required=True)
    manual.add_argument('--attestation', required=True)
    manual.add_argument('--task')
    manual.add_argument('--task-attempt')
    manual.add_argument('--plan-id')
    manual.add_argument('--provider-run-id')
    args = p.parse_args()
    if args.command == 'record-manual':
        try:
            record = record_manual(repo=args.repo, feature=args.feature, role=args.role,
                provider=args.provider, checkpoint=args.checkpoint, verdict=args.verdict,
                report=args.report, task=args.task, task_attempt=args.task_attempt,
                plan_id=args.plan_id, provider_run_id=args.provider_run_id,
                attestation=args.attestation)
        except (OSError, ValueError, RuntimeError) as exc:
            import sys
            print(json.dumps({'status': 'verification-blocked' if 'UNAVAILABLE' in str(exc) or 'RACE' in str(exc) else 'invalid-policy',
                              'reason_code': str(exc)}, sort_keys=True), file=sys.stderr)
            raise SystemExit(5 if 'UNAVAILABLE' in str(exc) or 'RACE' in str(exc) else 2)
        print(json.dumps({'status': 'recorded', 'record': str(record)}, sort_keys=True))
        return
    repo = getattr(args, 'repo', pathlib.Path.cwd())
    print(json.dumps(summarize(repo.resolve(), getattr(args, 'feature', None),
                               getattr(args, 'orchestration_id', None)), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
