"""Feature documents: loading, fingerprints and validation of an SDD feature (AH5-14a).

Verbatim from Showcase `tooling/agent-harness/harness.py` at `fd96eeb`. The consumer's policy is one explicit
`FeaturePolicy`, with no defaults: its repository layout, the protocol files its fingerprint covers, where its agent
profiles live, its reviewer routing, its verification-contract checks and its state directory. State loading,
lifecycle transitions, admission, recovery and the CLI are later parts (AH5-14b–e).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from collections.abc import Callable
from typing import Any

VALID_ROLES = {'architect', 'builder', 'evaluator', 'integration', 'specialist'}
FEATURE_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
TASK_ID_RE = re.compile(r'^T-[A-Z0-9][A-Z0-9._-]*$')
PROFILE_RE = re.compile(r'^[a-z][a-z0-9-]*$')
AC_RE = re.compile(r'\bAC-[A-Z0-9._-]+\b')
DEFAULT_LEASE_TTL_SECONDS = 1800
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 60
VALID_TEST_POLICIES = {'legacy', 'risk-driven'}
VALID_TEST_MODES = {'red-green-refactor', 'existing-suite', 'not-applicable'}
STATE_SCHEMA_VERSION = 1


@dataclasses.dataclass(frozen=True)
class FeaturePolicy:
    """What a consumer decides; every field is required (``docs/specs/AH5-14/packet.md``)."""

    repo_base: Callable[[pathlib.Path], pathlib.Path]
    is_active: Callable[[pathlib.Path], bool]
    protocol_files: Callable[[pathlib.Path], list[pathlib.Path]]
    profile_path: Callable[[pathlib.Path, str], pathlib.Path]
    reviewers: Callable[[dict[str, Any]], list[str]]
    validate_contract: Callable[[pathlib.Path], list[str]]
    criterion_ids: Callable[[pathlib.Path], set[str]]
    state_dir: pathlib.Path
    legacy_feature_path: Callable[[pathlib.Path, str], pathlib.Path]


def die(message: str, code: int = 2) -> None:
    print(f'ERROR: {message}', file=sys.stderr)
    raise SystemExit(code)


def load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        die(f'missing file: {path}')
    except json.JSONDecodeError as exc:
        die(f'invalid JSON in {path}: {exc}')
    if not isinstance(value, dict):
        die(f'expected JSON object in {path}')
    return value


def feature_files(feature_dir: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    return feature_dir / 'spec.md', feature_dir / 'plan.md', feature_dir / 'tasks.json'


def task_index(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {t['id']: t for t in doc.get('tasks', []) if isinstance(t, dict) and isinstance(t.get('id'), str)}


def repo_root() -> pathlib.Path:
    proc = subprocess.run(
        ['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        die('this command must run inside a Git repository')
    return pathlib.Path(proc.stdout.strip()).resolve()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def feature_fingerprint(feature_dir: pathlib.Path) -> str:
    digest = hashlib.sha256()
    paths = list(feature_files(feature_dir))
    paths.extend([
        feature_dir / 'design.json',
        feature_dir / 'design' / 'gate.json',
        feature_dir / 'verification-contract.json',
        feature_dir / 'wayfinder-handoff.json',
    ])
    for path in paths:
        if not path.exists():
            continue
        try:
            name = str(path.relative_to(feature_dir))
        except ValueError:
            name = path.name
        digest.update(name.encode())
        digest.update(b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def protocol_fingerprint(feature_dir: pathlib.Path, *, policy: FeaturePolicy) -> str:
    root = policy.repo_base(feature_dir)
    digest = hashlib.sha256()
    for path in policy.protocol_files(feature_dir):
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path
        digest.update(str(rel).encode())
        digest.update(b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def protocol_version(feature_dir: pathlib.Path) -> int:
    """Compatibility contract for persisted task lifecycle state."""
    return STATE_SCHEMA_VERSION


def safe_relative_pattern(pattern: str) -> bool:
    if not pattern or pathlib.PurePath(pattern).is_absolute():
        return False
    normalized = pattern.replace('\\', '/').strip()
    if normalized in {'.', './', '*', '**', '**/*'}:
        return False
    segments = [part for part in normalized.split('/') if part not in {'', '.'}]
    if not segments or '..' in segments:
        return False
    forbidden_roots = {'.git', '.agent-state', '.agent-runs'}
    return segments[0] not in forbidden_roots


def builder_pattern_has_concrete_root(pattern: str) -> bool:
    normalized = pattern.replace('\\', '/').lstrip('./')
    first = normalized.split('/', 1)[0]
    return bool(first) and not any(char in first for char in '*?[')


def pattern_static_prefix(pattern: str) -> str:
    normalized = pattern.replace('\\', '/').lstrip('./')
    parts: list[str] = []
    for part in normalized.split('/'):
        if any(char in part for char in '*?['):
            break
        if part:
            parts.append(part)
    return '/'.join(parts)


def patterns_may_overlap(left: str, right: str) -> bool:
    # Conservative by design: false positives force an explicit dependency, while false negatives
    # would let two builders race on the same file. Exact paths are handled precisely; glob-vs-glob
    # uses the non-wildcard prefix as the safe approximation.
    import fnmatch

    left_glob = any(char in left for char in '*?[')
    right_glob = any(char in right for char in '*?[')
    if not left_glob and not right_glob:
        return left == right
    if not left_glob:
        return fnmatch.fnmatch(left, right)
    if not right_glob:
        return fnmatch.fnmatch(right, left)
    lp = pattern_static_prefix(left)
    rp = pattern_static_prefix(right)
    if not lp or not rp:
        return True
    return lp == rp or lp.startswith(rp + '/') or rp.startswith(lp + '/')


def transitive_dependencies(idx: dict[str, dict[str, Any]], tid: str) -> set[str]:
    found: set[str] = set()
    stack = list(idx.get(tid, {}).get('depends_on', []))
    while stack:
        dep = stack.pop()
        if dep in found:
            continue
        found.add(dep)
        stack.extend(idx.get(dep, {}).get('depends_on', []))
    return found


def design_gate_errors(feature_dir: pathlib.Path, spec: pathlib.Path, plan: pathlib.Path, *,
                       policy: FeaturePolicy) -> list[str]:
    config_path = feature_dir / 'design.json'
    if not config_path.exists():
        return []
    errors: list[str] = []
    try:
        config = json.loads(config_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return [f'invalid JSON in {config_path}: {exc}']
    if not isinstance(config, dict):
        return ['design.json must contain a JSON object']
    required = config.get('required_for_orchestration', True)
    if not isinstance(required, bool):
        errors.append('design.json.required_for_orchestration must be boolean')
        return errors
    for field in ('grill', 'prototype', 'architecture_grill'):
        value = config.get(field, 'auto')
        if value not in {'auto', 'always', 'off'}:
            errors.append(f'design.json.{field} must be one of auto/always/off')
    if not required or not policy.is_active(feature_dir):
        return errors
    gate_path = feature_dir / 'design' / 'gate.json'
    if not gate_path.exists():
        errors.append('design preflight gate is required before orchestration; run tooling/agent-harness/design.py')
        return errors
    try:
        gate = json.loads(gate_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        errors.append(f'invalid JSON in {gate_path}: {exc}')
        return errors
    if not isinstance(gate, dict):
        return errors + ['design/gate.json must contain a JSON object']
    if gate.get('schema_version') != 1:
        errors.append('design/gate.json schema_version must be 1')
    if gate.get('feature') != feature_dir.name:
        errors.append('design/gate.json feature must match feature directory')
    if gate.get('decision') != 'pass':
        errors.append('design/gate.json decision must be pass')
    expected = {
        'spec_sha256': sha256_bytes(spec.read_bytes()),
        'plan_sha256': sha256_bytes(plan.read_bytes()),
        'design_config_sha256': sha256_bytes(config_path.read_bytes()),
    }
    for field, digest in expected.items():
        if gate.get(field) != digest:
            errors.append(f'design preflight gate is stale: {field} no longer matches current input')
    stages = gate.get('stages')
    if not isinstance(stages, dict):
        errors.append('design/gate.json stages must be an object')
    else:
        for stage in ('spec_grill', 'prototype', 'architecture_grill'):
            record = stages.get(stage)
            if not isinstance(record, dict) or record.get('status') not in {'pass', 'skipped', 'waived'}:
                errors.append(f'design/gate.json stage {stage} must be pass/skipped/waived')
    return errors


def master_implementation_closure_errors(feature_dir: pathlib.Path, spec: pathlib.Path,
                                         plan: pathlib.Path) -> list[str]:
    """Validate an explicit human implementation-authority closure.

    This does not rewrite, waive, or reinterpret design/gate.json. It is a
    narrowly scoped route for continuing implementation when a human has
    explicitly frozen a non-passing design history and authorized the
    implementation contract separately.
    """
    path = feature_dir / 'design' / 'master-closure.json'
    if not path.exists():
        return ['fresh canonical design gate is required; no master implementation closure exists']
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return ['master implementation closure is unreadable or invalid JSON']
    expected = {
        'spec_sha256': sha256_bytes(spec.read_bytes()),
        'plan_sha256': sha256_bytes(plan.read_bytes()),
        'design_config_sha256': sha256_bytes((feature_dir / 'design.json').read_bytes()),
    }
    required = {
        'schema_version': 1,
        'feature': feature_dir.name,
        'authority': 'master-closure',
        'status': 'implementation-authorized',
        'canonical_design_gate': 'not-pass',
        'authority_runs': 16,
    }
    errors = [f'master implementation closure {key} must equal {value!r}'
              for key, value in required.items() if record.get(key) != value]
    inputs = record.get('inputs')
    if not isinstance(inputs, dict):
        errors.append('master implementation closure inputs must be an object')
    else:
        for key, value in expected.items():
            if inputs.get(key) != value:
                errors.append(f'master implementation closure is stale: inputs.{key} does not match')
    if record.get('verification_contract_authorized') is not True:
        errors.append('master implementation closure must authorize post-implementation contract refresh')
    if record.get('commit_authorized') is not True:
        errors.append('master implementation closure must authorize coherent implementation commits')
    return errors


def validate(feature_dir: pathlib.Path, *, policy: FeaturePolicy) -> list[str]:
    errors: list[str] = []
    spec, plan, tasks_path = feature_files(feature_dir)
    for path in (spec, plan, tasks_path):
        if not path.exists():
            errors.append(f'missing required file: {path}')
    if errors:
        return errors

    gate_errors = design_gate_errors(feature_dir, spec, plan, policy=policy)
    if gate_errors:
        closure_errors = master_implementation_closure_errors(feature_dir, spec, plan)
        if closure_errors:
            errors.extend(gate_errors)
            errors.extend(closure_errors)
    errors.extend(policy.validate_contract(feature_dir))

    try:
        doc = json.loads(tasks_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return [f'invalid JSON in {tasks_path}: {exc}']
    if not isinstance(doc, dict):
        return ['tasks.json must contain a JSON object']

    feature = doc.get('feature')
    if not isinstance(feature, str) or not feature.strip():
        errors.append('tasks.json.feature must be a non-empty string')
    else:
        if not FEATURE_ID_RE.fullmatch(feature):
            errors.append(f'tasks.json.feature has unsafe/invalid format: {feature!r}')
        if feature != feature_dir.name:
            errors.append(f'tasks.json.feature must match feature directory name: {feature!r} != {feature_dir.name!r}')

    test_policy = doc.get('test_policy', 'legacy')
    if test_policy not in VALID_TEST_POLICIES:
        errors.append(f'test_policy must be one of {sorted(VALID_TEST_POLICIES)}')

    max_parallel = doc.get('max_parallel', 4)
    if not isinstance(max_parallel, int) or not 1 <= max_parallel <= 8:
        errors.append('max_parallel must be an integer between 1 and 8')
    max_rework = doc.get('max_rework_attempts', 2)
    if not isinstance(max_rework, int) or not 0 <= max_rework <= 5:
        errors.append('max_rework_attempts must be an integer between 0 and 5')

    lease_ttl = doc.get('lease_ttl_seconds', DEFAULT_LEASE_TTL_SECONDS)
    if not isinstance(lease_ttl, int) or not 60 <= lease_ttl <= 86400:
        errors.append('lease_ttl_seconds must be an integer between 60 and 86400')
    heartbeat_interval = doc.get('heartbeat_interval_seconds', DEFAULT_HEARTBEAT_INTERVAL_SECONDS)
    if not isinstance(heartbeat_interval, int) or not 5 <= heartbeat_interval <= 300:
        errors.append('heartbeat_interval_seconds must be an integer between 5 and 300')
    if isinstance(lease_ttl, int) and isinstance(heartbeat_interval, int) and heartbeat_interval * 2 >= lease_ttl:
        errors.append('heartbeat_interval_seconds must be less than half of lease_ttl_seconds')

    tasks = doc.get('tasks')
    if not isinstance(tasks, list) or not tasks:
        return errors + ['tasks.json must contain a non-empty tasks array']

    ids: list[str] = []
    required = (
        'id', 'title', 'objective', 'role', 'depends_on', 'allowed_paths',
        'risk_tags', 'acceptance_criteria', 'verification'
    )
    list_fields = ('depends_on', 'allowed_paths', 'risk_tags', 'acceptance_criteria', 'verification')
    for i, task in enumerate(tasks):
        where = f'tasks[{i}]'
        if not isinstance(task, dict):
            errors.append(f'{where} must be an object')
            continue
        for name in required:
            if name not in task:
                errors.append(f'{where} missing {name}')
        tid = task.get('id')
        if isinstance(tid, str):
            ids.append(tid)
            if not TASK_ID_RE.fullmatch(tid):
                errors.append(f'{where}.id has invalid format: {tid!r}')
        else:
            errors.append(f'{where}.id must be a string')
        if not isinstance(task.get('title'), str) or not task.get('title', '').strip():
            errors.append(f'{where}.title must be a non-empty string')
        if not isinstance(task.get('objective'), str) or not task.get('objective', '').strip():
            errors.append(f'{where}.objective must be a non-empty string')
        if task.get('role') not in VALID_ROLES:
            errors.append(f'{where}.role must be one of {sorted(VALID_ROLES)}')
        if 'agent_profile' in task:
            profile = task.get('agent_profile')
            if not isinstance(profile, str) or not PROFILE_RE.fullmatch(profile):
                errors.append(f'{where}.agent_profile must match {PROFILE_RE.pattern}')
        for name in list_fields:
            if name in task and not isinstance(task[name], list):
                errors.append(f'{where}.{name} must be an array')
        patterns = task.get('allowed_paths', []) if isinstance(task.get('allowed_paths'), list) else []
        for pattern in patterns:
            if not isinstance(pattern, str) or not safe_relative_pattern(pattern):
                errors.append(f'{where}.allowed_paths contains unsafe path pattern: {pattern!r}')
            elif task.get('role') == 'builder' and not builder_pattern_has_concrete_root(pattern):
                errors.append(f'{where}.builder allowed_paths must start with a concrete repo path: {pattern!r}')
        for risk in task.get('risk_tags', []) if isinstance(task.get('risk_tags'), list) else []:
            if not isinstance(risk, str) or not risk.strip():
                errors.append(f'{where}.risk_tags must contain non-empty strings')
        criteria = task.get('acceptance_criteria', []) if isinstance(task.get('acceptance_criteria'), list) else []
        if not criteria:
            errors.append(f'{where}.acceptance_criteria must not be empty')
        for command in task.get('verification', []) if isinstance(task.get('verification'), list) else []:
            if not isinstance(command, str) or not command.strip():
                errors.append(f'{where}.verification must contain non-empty command strings')
        if task.get('role') == 'builder' and test_policy == 'risk-driven':
            mode = task.get('test_mode')
            seam = task.get('test_seam')
            if mode not in VALID_TEST_MODES:
                errors.append(f'{where}.test_mode must be one of {sorted(VALID_TEST_MODES)} when test_policy=risk-driven')
            if not isinstance(seam, str) or not seam.strip():
                errors.append(f'{where}.test_seam must explain the observable seam or why tests are not applicable')

    duplicates = sorted({x for x in ids if ids.count(x) > 1})
    if duplicates:
        errors.append(f'duplicate task ids: {duplicates}')

    known = set(ids)
    idx = task_index(doc)
    for tid, task in idx.items():
        for dep in task.get('depends_on', []):
            if not isinstance(dep, str):
                errors.append(f'{tid} has a non-string dependency')
            elif dep not in known:
                errors.append(f'{tid} depends on unknown task {dep}')
            elif dep == tid:
                errors.append(f'{tid} depends on itself')

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(tid: str, chain: list[str]) -> None:
        if tid in visiting:
            errors.append('dependency cycle: ' + ' -> '.join(chain + [tid]))
            return
        if tid in visited or tid not in idx:
            return
        visiting.add(tid)
        for dep in idx[tid].get('depends_on', []):
            if isinstance(dep, str):
                visit(dep, chain + [tid])
        visiting.remove(tid)
        visited.add(tid)

    for tid in idx:
        visit(tid, [])

    spec_criteria = set(AC_RE.findall(spec.read_text(encoding='utf-8')))
    verification_criteria = policy.criterion_ids(feature_dir)
    accepted_criteria = spec_criteria | verification_criteria
    for tid, task in idx.items():
        for criterion in task.get('acceptance_criteria', []):
            if not isinstance(criterion, str):
                errors.append(f'{tid} has a non-string acceptance criterion')
            elif criterion not in accepted_criteria:
                errors.append(f'{tid} references criterion absent from spec.md and verification-contract.json: {criterion}')

        profiles = [task.get('agent_profile') or task.get('role'), *policy.reviewers(task)]
        for profile in profiles:
            if not isinstance(profile, str) or not PROFILE_RE.fullmatch(profile):
                continue
            profile_path = policy.profile_path(policy.repo_base(feature_dir), profile)
            if not profile_path.exists():
                errors.append(f'{tid} selects missing agent profile: {profile} ({profile_path})')

    # Detect write collisions only for tasks that can actually be concurrent (neither depends on the other).
    ancestors = {tid: transitive_dependencies(idx, tid) for tid in idx}
    for pos, a in enumerate(tasks):
        if not isinstance(a, dict) or not isinstance(a.get('id'), str):
            continue
        for b in tasks[pos + 1:]:
            if not isinstance(b, dict) or not isinstance(b.get('id'), str):
                continue
            aid, bid = a['id'], b['id']
            if aid in ancestors.get(bid, set()) or bid in ancestors.get(aid, set()):
                continue
            if a.get('role') == b.get('role') == 'builder':
                overlaps = sorted(
                    {f'{left} <-> {right}' for left in a.get('allowed_paths', []) for right in b.get('allowed_paths', [])
                     if isinstance(left, str) and isinstance(right, str) and patterns_may_overlap(left, right)}
                )
                if overlaps:
                    errors.append(f'parallel builders {aid} and {bid} have overlapping allowed_paths {overlaps}')

    evaluators = [t for t in tasks if isinstance(t, dict) and t.get('role') == 'evaluator']
    builders = [t for t in tasks if isinstance(t, dict) and t.get('role') == 'builder']
    if builders and not evaluators:
        errors.append('at least one evaluator task is required when builder tasks exist')
    if evaluators and builders:
        covered = set().union(*(set(t.get('depends_on', [])) for t in evaluators))
        uncovered = sorted(t['id'] for t in builders if t.get('id') not in covered)
        if uncovered:
            errors.append(f'builder tasks not directly covered by an evaluator dependency: {uncovered}')
        evaluator_criteria = set().union(*(set(t.get('acceptance_criteria', [])) for t in evaluators))
        uncovered_criteria = sorted(accepted_criteria - evaluator_criteria)
        if uncovered_criteria:
            errors.append(f'spec acceptance criteria not covered by an evaluator (including verification-contract criteria): {uncovered_criteria}')

    integrations = [t for t in tasks if isinstance(t, dict) and t.get('role') == 'integration']
    if integrations and evaluators:
        evaluator_ids = {t['id'] for t in evaluators}
        for task in integrations:
            if not evaluator_ids.intersection(task.get('depends_on', [])):
                errors.append(f'integration task {task.get("id")} must depend on an evaluator')

    return errors


def load_validated(feature_dir: pathlib.Path, *, policy: FeaturePolicy) -> dict[str, Any]:
    errors = validate(feature_dir, policy=policy)
    if errors:
        for error in errors:
            print(f'FAIL: {error}', file=sys.stderr)
        die('feature is invalid')
    return load_json(feature_dir / 'tasks.json')
