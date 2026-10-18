"""Repeatable behavioral/regression eval runner for the Agentic SDD harness.

Suites are checked-in JSON manifests. They run deterministic local commands, capture environment
provenance, pass/fail evidence and timings, and can compare two result files. Live/model-specific
cases are opt-in and may use {provider} / {target} placeholders.

From Showcase `tooling/agent-harness/eval.py` at `49ab97f`. The suites directory and the repository anchor are
explicit (the consumer keeps its suites); see docs/migration/provenance.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import platform
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from typing import Any

FORBIDDEN_FRAGMENTS = (
    'git push', 'git commit', 'git merge', 'git rebase', 'git reset --hard',
    'gh pr create', 'kubectl apply', 'kubectl delete', 'argocd app',
    'terraform apply', 'terraform destroy', 'docker push',
)


def die(message: str) -> None:
    print(f'ERROR: {message}', file=sys.stderr)
    raise SystemExit(2)


def repo_root(anchor: pathlib.Path) -> pathlib.Path:
    # [seam] The consumer names a path inside its repository; the source used this file's location.
    proc = subprocess.run(['git', '-C', str(anchor), 'rev-parse', '--show-toplevel'], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        proc = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        die('eval runner requires a Git repository')
    return pathlib.Path(proc.stdout.strip()).resolve()


def load_suite(value: str, suites_dir: pathlib.Path) -> tuple[pathlib.Path, dict[str, Any]]:
    candidate = pathlib.Path(value)
    if not candidate.exists():
        candidate = pathlib.Path(suites_dir) / (value if value.endswith('.json') else value + '.json')
    try:
        doc = json.loads(candidate.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        die(f'{candidate}: {exc}')
    if not isinstance(doc, dict):
        die('eval suite must contain an object')
    errors = validate_suite(doc)
    if errors:
        die('; '.join(errors))
    return candidate.resolve(), doc


def validate_suite(doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if doc.get('schema_version') != 1:
        errors.append('schema_version must be 1')
    if not isinstance(doc.get('name'), str) or not doc.get('name', '').strip():
        errors.append('name must be non-empty')
    cases = doc.get('cases')
    if not isinstance(cases, list) or not cases:
        return errors + ['cases must be non-empty array']
    ids: set[str] = set()
    for i, case in enumerate(cases):
        where = f'cases[{i}]'
        if not isinstance(case, dict): errors.append(f'{where} must be object'); continue
        cid = case.get('id')
        if not isinstance(cid, str) or not cid.strip() or cid in ids: errors.append(f'{where}.id must be unique non-empty')
        else: ids.add(cid)
        command = case.get('command')
        if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
            errors.append(f'{where}.command must be non-empty string array'); continue
        joined = ' '.join(command).lower()
        if any(fragment in joined for fragment in FORBIDDEN_FRAGMENTS):
            errors.append(f'{where}.command contains forbidden remote/destructive mutation')
        expected = case.get('expected_exit', 0)
        if not isinstance(expected, int): errors.append(f'{where}.expected_exit must be integer')
        for field in ('stdout_contains', 'stderr_contains'):
            value = case.get(field, [])
            if not isinstance(value, list) or any(not isinstance(x, str) for x in value): errors.append(f'{where}.{field} must be string array')
        if 'timeout_seconds' in case and (not isinstance(case['timeout_seconds'], int) or case['timeout_seconds'] < 1): errors.append(f'{where}.timeout_seconds must be positive integer')
    return errors


def git_head(root: pathlib.Path) -> str | None:
    proc = subprocess.run(['git','rev-parse','HEAD'], cwd=root, capture_output=True, text=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def cli_version(name: str | None) -> str | None:
    if not name or not shutil.which(name): return None
    try:
        proc = subprocess.run([name,'--version'], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired): return None
    return (proc.stdout or proc.stderr).strip()[:300] or None


def environment(root: pathlib.Path, provider: str | None) -> dict[str, Any]:
    return {
        'captured_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'git_head': git_head(root), 'python': sys.version.split()[0], 'platform': platform.platform(),
        'machine': platform.machine(), 'cpu_count': os.cpu_count(), 'provider': provider,
        'provider_cli_version': cli_version(provider),
    }


def substitute(command: list[str], provider: str | None, target: str | None) -> list[str]:
    values = {'provider': provider or '', 'target': target or ''}
    rendered = [part.format(**values) for part in command]
    if any('{provider}' in part for part in command) and not provider: die('suite requires --provider')
    if any('{target}' in part for part in command) and not target: die('suite requires --target')
    return rendered


def run_case(root: pathlib.Path, case: dict[str, Any], provider: str | None, target: str | None, iteration: int) -> dict[str, Any]:
    command = substitute(case['command'], provider, target)
    started = time.monotonic()
    try:
        proc = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False, timeout=case.get('timeout_seconds', 120))
        duration_ms = round((time.monotonic()-started)*1000)
        stdout, stderr = proc.stdout, proc.stderr
        expected = int(case.get('expected_exit', 0))
        checks = [proc.returncode == expected]
        checks += [needle in stdout for needle in case.get('stdout_contains', [])]
        checks += [needle in stderr for needle in case.get('stderr_contains', [])]
        return {
            'case': case['id'], 'iteration': iteration, 'status': 'pass' if all(checks) else 'fail',
            'command': command, 'exit_code': proc.returncode, 'expected_exit': expected,
            'duration_ms': duration_ms, 'stdout_sha256': hashlib.sha256(stdout.encode()).hexdigest(),
            'stderr_sha256': hashlib.sha256(stderr.encode()).hexdigest(),
            'stdout_tail': stdout[-2000:], 'stderr_tail': stderr[-2000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {'case':case['id'],'iteration':iteration,'status':'fail','command':command,'timed_out':True,'duration_ms':round((time.monotonic()-started)*1000),'stdout_tail':str(exc.stdout or '')[-2000:],'stderr_tail':str(exc.stderr or '')[-2000:]}


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [int(r.get('duration_ms', 0)) for r in records]
    passes = sum(r.get('status') == 'pass' for r in records)
    by_case: dict[str, dict[str, Any]] = {}
    for record in records:
        group = by_case.setdefault(str(record['case']), {'runs':0,'passes':0,'durations_ms':[]})
        group['runs'] += 1; group['passes'] += record.get('status') == 'pass'; group['durations_ms'].append(record.get('duration_ms',0))
    for group in by_case.values():
        group['pass_rate'] = group['passes']/group['runs'] if group['runs'] else 0
        group['median_duration_ms'] = round(statistics.median(group.pop('durations_ms'))) if group['runs'] else 0
    return {'runs':len(records),'passes':passes,'pass_rate':passes/len(records) if records else 0,'median_duration_ms':round(statistics.median(durations)) if durations else 0,'by_case':by_case}


def run_suite(path: pathlib.Path, doc: dict[str, Any], args: argparse.Namespace, *,
              anchor: pathlib.Path) -> pathlib.Path:
    root = repo_root(anchor)
    run_id = args.run_id or uuid.uuid4().hex[:12]
    out = root / '.agent-runs' / 'evals' / doc['name'] / run_id
    out.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for iteration in range(1, args.repeat+1):
        for case in doc['cases']:
            if case.get('live') is True and not args.include_live:
                continue
            record = run_case(root, case, args.provider, args.target, iteration)
            records.append(record)
            print(f"{'PASS' if record['status']=='pass' else 'FAIL'} {case['id']} run={iteration} {record.get('duration_ms')}ms")
            if record['status'] != 'pass' and args.fail_fast:
                break
        if args.fail_fast and records and records[-1]['status'] != 'pass': break
    result = {
        'schema_version':1,'suite':doc['name'],'suite_path':str(path),'run_id':run_id,
        'environment':environment(root,args.provider),'repeat':args.repeat,'target':args.target,
        'summary':summarize(records),'records':records,
    }
    result_path = out/'result.json'; result_path.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps(result['summary'], indent=2, sort_keys=True))
    print(result_path)
    if result['summary']['pass_rate'] < 1.0: raise SystemExit(1)
    return result_path


def compare(left: pathlib.Path, right: pathlib.Path) -> None:
    a=json.loads(left.read_text()); b=json.loads(right.read_text())
    sa=a['summary']; sb=b['summary']
    print(json.dumps({
        'left':str(left),'right':str(right),
        'pass_rate_delta':sb['pass_rate']-sa['pass_rate'],
        'median_duration_ms_delta':sb['median_duration_ms']-sa['median_duration_ms'],
        'left_environment':a.get('environment',{}),'right_environment':b.get('environment',{}),
    },indent=2,sort_keys=True))


def main(argv=None, *, suites_dir: pathlib.Path, anchor: pathlib.Path) -> None:
    p=argparse.ArgumentParser(description='Agentic SDD behavioral/regression eval runner')
    sub=p.add_subparsers(dest='command',required=True)
    r=sub.add_parser('run'); r.add_argument('--suite',required=True); r.add_argument('--repeat',type=int,default=1); r.add_argument('--provider',choices=('codex','claude')); r.add_argument('--target'); r.add_argument('--include-live',action='store_true'); r.add_argument('--run-id'); r.add_argument('--fail-fast',action='store_true')
    l=sub.add_parser('list')
    c=sub.add_parser('compare'); c.add_argument('left',type=pathlib.Path); c.add_argument('right',type=pathlib.Path)
    args=p.parse_args(argv)
    if args.command=='list':
        for path in sorted(pathlib.Path(suites_dir).glob('*.json')): print(path.stem)
    elif args.command=='compare': compare(args.left,args.right)
    else:
        if args.repeat < 1 or args.repeat > 20: die('--repeat must be 1..20')
        path,doc=load_suite(args.suite, suites_dir); run_suite(path,doc,args,anchor=anchor)
