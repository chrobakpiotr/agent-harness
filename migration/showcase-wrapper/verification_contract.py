#!/usr/bin/env python3
"""Independent acceptance/verification contract authoring for Agentic SDD (Showcase; validation from agent_harness,
AH5-13).

The feature spec states intended behavior. This module creates a second, separately-authored
verification contract using the spec *plus* accepted ADRs, architecture invariants and existing
behavior so the implementation is not evaluated only against criteria written by the same artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import uuid
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCHEMA = HERE / 'schemas' / 'verification-contract.schema.json'
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import runner as r  # noqa: E402
import telemetry  # noqa: E402
import trust  # noqa: E402

from agent_harness import verification_contract as _impl  # noqa: E402

# AH5-13: the contract validation is the library's. Showcase binds its repository root, its constitution path and its
# active-feature layout, looked up per call (tests patch REPO). Authoring (generate) stays here.
VALID_REQUIREMENT, VALID_ORIGINS, VALID_SOURCE_TYPES = (
    _impl.VALID_REQUIREMENT, _impl.VALID_ORIGINS, _impl.VALID_SOURCE_TYPES)
CONSTITUTION = 'docs/agentic-sdd/constitution.md'
die, sha, load_json, requirement, criterion_ids = (
    _impl.die, _impl.sha, _impl.load_json, _impl.requirement, _impl.criterion_ids)


def repo_root() -> pathlib.Path:
    proc = subprocess.run(['git', '-C', str(REPO), 'rev-parse', '--show-toplevel'], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        proc = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        die('verification-contract generation requires a Git repository')
    return pathlib.Path(proc.stdout.strip()).resolve()


def is_active_feature_dir(feature_dir: pathlib.Path) -> bool:
    resolved = feature_dir.resolve()
    return resolved.parent.name == 'specs' and resolved.parent.parent.name == 'docs'


def input_hashes(feature_dir: pathlib.Path) -> dict[str, str]:
    return _impl.input_hashes(feature_dir, repo_root=lambda: repo_root(), repository=REPO, constitution=CONSTITUTION)


def validate_contract(feature_dir: pathlib.Path, *, require: bool | None = None) -> list[str]:
    return _impl.validate_contract(feature_dir, require=require, repo_root=lambda: repo_root(), repository=REPO,
                                   constitution=CONSTITUTION, is_active=lambda path: is_active_feature_dir(path))



def prompt(feature_dir: pathlib.Path, worktree: pathlib.Path) -> str:
    root = repo_root()
    rel = feature_dir.resolve().relative_to(root)
    spec = (feature_dir / 'spec.md').read_text(encoding='utf-8')
    plan = (feature_dir / 'plan.md').read_text(encoding='utf-8')
    tasks_path = feature_dir / 'tasks.json'
    task_doc = json.loads(tasks_path.read_text(encoding='utf-8')) if tasks_path.exists() else {'tasks': []}
    referenced = sorted({str(item) for task in task_doc.get('tasks', []) if isinstance(task, dict)
                         for item in task.get('acceptance_criteria', [])
                         if isinstance(item, str) and item.startswith('VC-')})
    historical = ''
    baseline = subprocess.run(['git', '-C', str(root), 'show',
        f'HEAD:{rel.as_posix()}/verification-contract.json'], capture_output=True, check=False)
    if baseline.returncode == 0:
        try:
            old_contract = json.loads(baseline.stdout)
            old_criteria = [item for item in old_contract.get('criteria', [])
                            if isinstance(item, dict) and item.get('id') in referenced]
            historical = json.dumps(old_criteria, indent=2, sort_keys=True)
        except (json.JSONDecodeError, TypeError):
            historical = ''
    profile = (worktree / 'docs' / 'agentic-sdd' / 'agents' / 'verification-author.md').read_text(encoding='utf-8')
    return f"""You are independently authoring the verification contract for feature {feature_dir.name}.

ROLE CONTRACT
-------------
{profile}

{trust.policy_text()}

FEATURE ARTIFACTS
-----------------
SPEC (trusted intent, but NOT the only source of truth):
{spec}

PLAN (trusted design decision, but verify it against repository invariants):
{plan}

TASK DAG REFERENCES
------------------
These stable verification criterion IDs are referenced by the executable
task DAG and MUST remain present in your output unless the task DAG itself
has been explicitly revised:
{json.dumps(referenced)}

Prior definitions for referenced IDs, if any, are historical guidance, not
an instruction to preserve criteria that are disproven or obsolete:
{historical}

The same artifacts are available under `{rel}` in this isolated read-only worktree. Inspect AGENTS.md, the constitution,
relevant ADRs, existing tests/contracts and current behavior. Derive criteria that can falsify the implementation even when
spec.md forgot a safety/property requirement. At least one criterion MUST have origin=independent for medium/high-risk work.
Include one criterion for every referenced task-DAG VC identifier, retaining its identifier. Add new criteria with unused IDs.
Do not invent product behavior. Independent criteria should protect already-accepted architecture, compatibility, security,
operability or existing behavior. Exemptions must remain status=proposed unless a pre-existing human approval is explicitly
present in trusted repository artifacts. A `design/master-closure.json`, when present, authorizes implementation and
post-implementation contract refresh only; it does not claim or substitute for a passing canonical Design Gate.

Return ONLY JSON conforming to tooling/agent-harness/schemas/verification-contract.schema.json.
"""


def provider_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        print_command=False, sandbox='read-only', model=args.model, reasoning=args.reasoning,
        profile='verification-author', review_existing=True, max_turns=args.max_turns,
        max_budget_usd=args.max_budget_usd,
    )


def generate(feature_dir: pathlib.Path, args: argparse.Namespace) -> pathlib.Path:
    import harness as h  # lazy: avoids circular import when harness validates contracts

    feature_dir = feature_dir.resolve()
    for name in ('spec.md', 'plan.md'):
        if not (feature_dir / name).exists():
            die(f'missing {name}')
    design_errors = h.design_gate_errors(feature_dir, feature_dir / 'spec.md', feature_dir / 'plan.md')
    if design_errors:
        closure_errors = h.master_implementation_closure_errors(
            feature_dir, feature_dir / 'spec.md', feature_dir / 'plan.md')
        if closure_errors:
            die('verification contract must be authored after a current design gate or valid master implementation closure: ' +
                '; '.join(closure_errors))
    existing = feature_dir / 'verification-contract.json'
    if existing.exists() and not args.force:
        die(f'refusing to overwrite existing contract without --force: {existing}')
    base = h.execution_base(feature_dir, {'feature': feature_dir.name}, {})
    root = h.repo_root()
    wt_root = root.parent / f'{root.name}-agent-worktrees' / feature_dir.name
    wt_root.mkdir(parents=True, exist_ok=True)
    wt = wt_root / f'_verification-{uuid.uuid4().hex[:8]}'
    subprocess.run(['git', 'worktree', 'add', '--detach', str(wt), base], cwd=root, check=True, capture_output=True)
    run_id = args.run_id or uuid.uuid4().hex
    runtime = root / '.agent-runs' / 'verification-contract' / feature_dir.name / run_id
    runtime.mkdir(parents=True, exist_ok=True)
    result_path = runtime / 'result.json'
    text = prompt(feature_dir, wt)
    p_args = provider_args(args)
    cmd = r.codex_command(p_args, text, wt, result_path, schema_path=wt / SCHEMA.relative_to(REPO)) if args.provider == 'codex' else r.claude_command(p_args, text, wt, schema_path=wt / SCHEMA.relative_to(REPO))
    (runtime / 'prompt.txt').write_text(text, encoding='utf-8')
    started = time.monotonic()
    provenance = {'schema_version': 1, 'kind': 'verification-contract', 'feature': feature_dir.name, 'provider': args.provider, 'run_id': run_id, 'started_at': telemetry.iso_now(), 'status': 'running'}
    telemetry.atomic_write_json(runtime / 'provenance.json', provenance)
    try:
        proc = subprocess.run(cmd, cwd=wt, text=True, capture_output=True, check=False)
        (runtime / 'stdout.log').write_text(proc.stdout, encoding='utf-8')
        (runtime / 'stderr.log').write_text(proc.stderr, encoding='utf-8')
        if proc.returncode != 0:
            die(f'{args.provider} exited with {proc.returncode}: {(proc.stderr or proc.stdout)[-2000:]}')
        raw = r.extract_claude_result(proc.stdout) if args.provider == 'claude' else load_json(result_path)
        if raw.get('status') != 'pass':
            die(f'verification author did not pass: {raw.get("summary")}')
        if not isinstance(raw.get('criteria'), list) or not raw['criteria']:
            die('verification author returned no criteria')
        contract = {
            'schema_version': 1,
            'feature': feature_dir.name,
            'status': 'accepted',
            'authored_by': {'profile': 'verification-author', 'provider': args.provider, 'run_id': run_id},
            'generated_at': telemetry.iso_now(),
            'inputs': input_hashes(feature_dir),
            'summary': raw.get('summary'),
            'criteria': raw.get('criteria'),
            'exemptions': raw.get('exemptions', []),
            'assumptions': raw.get('assumptions', []),
        }
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text(json.dumps(contract, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        errors = validate_contract(feature_dir, require=requirement(feature_dir) == 'required')
        if errors:
            existing.unlink(missing_ok=True)
            die('generated verification contract is invalid: ' + '; '.join(errors))
        provenance.update({'status': 'pass', 'completed_at': telemetry.iso_now(), 'duration_ms': round((time.monotonic()-started)*1000), 'contract_sha256': sha(existing)})
        telemetry.atomic_write_json(runtime / 'provenance.json', provenance)
        return existing
    finally:
        subprocess.run(['git', 'worktree', 'remove', '--force', str(wt)], cwd=root, check=False, capture_output=True)
        shutil.rmtree(wt, ignore_errors=True)


def main() -> None:
    p = argparse.ArgumentParser(description='Independent verification-contract gate')
    sub = p.add_subparsers(dest='command', required=True)
    v = sub.add_parser('validate'); v.add_argument('feature_dir', type=pathlib.Path)
    s = sub.add_parser('show'); s.add_argument('feature_dir', type=pathlib.Path)
    g = sub.add_parser('generate'); g.add_argument('feature_dir', type=pathlib.Path); g.add_argument('--provider', choices=('codex','claude'), required=True); g.add_argument('--model'); g.add_argument('--reasoning', choices=('low','medium','high','xhigh')); g.add_argument('--max-turns', type=int, default=20); g.add_argument('--max-budget-usd', type=float); g.add_argument('--run-id'); g.add_argument('--force', action='store_true')
    args = p.parse_args()
    if args.command == 'validate':
        errors = validate_contract(args.feature_dir.resolve())
        if errors:
            for error in errors: print('ERROR:', error, file=sys.stderr)
            raise SystemExit(2)
        print(f'OK {args.feature_dir}')
    elif args.command == 'show':
        print((args.feature_dir / 'verification-contract.json').read_text(encoding='utf-8'), end='')
    else:
        print(generate(args.feature_dir, args))


if __name__ == '__main__':
    main()
