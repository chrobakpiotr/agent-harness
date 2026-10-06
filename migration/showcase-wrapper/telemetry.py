#!/usr/bin/env python3
"""Structured provenance and usage telemetry for agentic SDD runs.

All data stays in ignored .agent-runs/. No prompt/secret content is copied into provenance.json;
raw provider stdout/stderr remain separate local artifacts.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import stat

# Provenance and usage telemetry live in agent_harness.telemetry (AH5-06); manual evidence and the CLI
# stay here because they need the Showcase lifecycle.
from agent_harness.telemetry import *

_MANUAL_ROLES = {'reviewer', 'evaluator', 'architecture-reviewer', 'verification-author'}
_MANUAL_VERDICTS = {'PASS', 'FAIL', 'NEEDS-HUMAN'}
_MANUAL_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')
_MANUAL_HASH = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')


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
    if not fields['Completed at'].endswith('Z'):
        raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID')
    try:
        dt.datetime.fromisoformat(fields['Completed at'].replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID') from None
    return fields


def record_manual(*, repo: pathlib.Path, feature: str, role: str, provider: str,
                  checkpoint: str, verdict: str, report: str, task: str | None = None,
                  task_attempt: str | None = None, plan_id: str | None = None,
                  provider_run_id: str | None = None) -> pathlib.Path:
    """Record safe manual provenance only; never changes task lifecycle state."""
    if (not _MANUAL_ID.fullmatch(feature or '') or role not in _MANUAL_ROLES or
            not provider or not _MANUAL_HASH.fullmatch(checkpoint or '') or verdict not in _MANUAL_VERDICTS):
        raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID')
    from verification.store import StoreError, VerificationStore
    root = pathlib.Path(repo).resolve(strict=True)
    store = VerificationStore(root)
    primary = store.root.parents[2]
    task_index_path = primary / 'docs' / 'specs' / feature / 'tasks.json'
    if not task_index_path.is_file():
        raise ValueError('MANUAL_EVIDENCE_FEATURE_UNRESOLVED')
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
    if task_attempt is not None:
        if not task_attempt.isdigit() or int(task_attempt) < 1:
            raise ValueError('MANUAL_EVIDENCE_REPORT_ATTEMPT_UNRESOLVED')
        # The durable attempt binding is created by the lifecycle claim CAS.
        state_candidates = list((primary / '.agent-state').glob(f'{feature}-*.json'))
        resolved_attempts = []
        for state_path in state_candidates:
            try:
                state = json.loads(state_path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                continue
            for task_id, entry in state.get('tasks', {}).items():
                if task is not None and task_id != task:
                    continue
                if any(item.get('attempt') == int(task_attempt) for item in entry.get('attempt_bindings', [])):
                    resolved_attempts.append(task_id)
        if len(resolved_attempts) != 1:
            raise ValueError('MANUAL_EVIDENCE_REPORT_ATTEMPT_UNRESOLVED')
        if task is not None and task != resolved_attempts[0]:
            raise ValueError('MANUAL_EVIDENCE_REPORT_ATTEMPT_UNRESOLVED')
        task = resolved_attempts[0]
        # Packet-binding history proves that the attempt existed; only its
        # immutable completion record can additionally bind the reviewed
        # checkpoint to that exact attempt.
        feature_dir = primary / 'docs' / 'specs' / feature
        harness_path = pathlib.Path(__file__).resolve().parent
        if str(harness_path) not in __import__('sys').path:
            __import__('sys').path.insert(0, str(harness_path))
        import harness as lifecycle
        checkpoint_records = [item for item in lifecycle.completion_records(feature_dir, task)
                              if item.get('attempt') == int(task_attempt) and item.get('checkpoint') == checkpoint]
        if len(checkpoint_records) != 1:
            raise ValueError('MANUAL_EVIDENCE_REPORT_ATTEMPT_UNRESOLVED')
    if task is not None:
        if not _MANUAL_ID.fullmatch(task) or fields.get('Task') != task:
            raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID')
        tasks = json.loads(task_index_path.read_text(encoding='utf-8'))
        task_doc = next((item for item in tasks.get('tasks', []) if item.get('id') == task), None)
        compatible_roles = {task_doc.get('role'), task_doc.get('agent_profile'), *task_doc.get('required_reviewers', [])} if task_doc else set()
        if role == 'reviewer' and task_doc and task_doc.get('role') == 'builder':
            compatible_roles.add('reviewer')
        if task_doc is None or role not in compatible_roles:
            raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID')
    elif 'Task' in fields:
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
    if not provider or (provider_run_id is not None and len(provider_run_id) > 256):
        raise ValueError('MANUAL_EVIDENCE_REPORT_INVALID')
    relative_safe = resolved.relative_to(primary).as_posix()
    identity_body = {'feature': feature, 'role': role, 'provider': provider,
                     'checkpoint': checkpoint, 'verdict': verdict, 'report_path': relative_safe,
                     'report_sha256': hashlib.sha256(payload).hexdigest(), 'task': task,
                     'task_attempt': task_attempt, 'plan_id': plan_id, 'provider_run_id': provider_run_id}
    identity = hashlib.sha256(json.dumps(identity_body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    record = {'schema_version': 1, 'kind': 'manual-observation', 'observation_id': identity,
              **identity_body, 'recorded_at': iso_now()}
    target = store.root / 'manual' / f'{identity}.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = json.loads(target.read_text(encoding='utf-8'))
        if {key: value for key, value in existing.items() if key != 'recorded_at'} != identity_body | {
                'schema_version': 1, 'kind': 'manual-observation', 'observation_id': identity}:
            raise ValueError('MANUAL_EVIDENCE_CONFLICT')
        return target
    atomic_write_json(target, record)
    return target


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
                plan_id=args.plan_id, provider_run_id=args.provider_run_id)
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
