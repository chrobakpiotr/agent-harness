"""Lifecycle-bound immutable Verification Execution Plan helpers."""
from __future__ import annotations

import hashlib
import pathlib
import re

from .serialization import canonical


def execution_plan_record(feature_id: str, feature_fingerprint: str, generation: int,
                          profile_id: str, profile, plan, *, task_id: str, task_attempt: int,
                          task_commands=(), origin_binding: str):
    family = {
        'id': plan.family.id, 'base_sha': plan.family.base_sha,
        'origin_policy': plan.family.origin_policy, 'profile_hash': plan.family.profile_hash,
        'policy_checkpoint': plan.family.policy_checkpoint,
        'candidate_identity': plan.family.candidate_identity,
        'final_changed_surface_id': plan.family.final_changed_surface_id,
    }
    obligations = []
    for decision in plan.decisions:
        node, gate = decision.node, decision.node.gate
        identity = {'family_id': family['id'], 'gate_id': node.id,
                    'profile_gate_id': node.profile_gate_id,
                    'occurrence': node.occurrence, 'ordinal': node.ordinal,
                    'requirement_source': 'task-command' if (node.occurrence or node.profile_gate_id is None) else 'profile',
                    'required_origin': 'task' if (node.occurrence or node.profile_gate_id is None) else 'independent',
                    'independent_execution_class': None if (node.occurrence or node.profile_gate_id is None) else INDEPENDENT_CLASS}
        obligation_id = 'verification-obligation-v2:sha256:' + hashlib.sha256(canonical(identity)).hexdigest()
        obligations.append({'obligation_id': obligation_id, **identity,
            'command': gate.command, 'command_hash': gate.command_hash, 'cwd': gate.cwd,
            'dependencies': list(node.dependencies), 'critical': gate.critical,
            'retry_policy': gate.retry_policy, 'retry_controls': list(gate.retry_controls),
            'sandbox': gate.sandbox})
    units = []
    for obligation in obligations:
        member_ids = [obligation['obligation_id']]
        unit_id = 'verification-unit-v2:sha256:' + hashlib.sha256(canonical({'obligation_ids': member_ids, 'required_origin': obligation['required_origin'], 'independent_execution_class': obligation['independent_execution_class']})).hexdigest()
        units.append({'unit_id': unit_id, 'obligation_ids': member_ids, 'required_origin': obligation['required_origin'], 'independent_execution_class': obligation['independent_execution_class']})
    body = {'schema_version': 2, 'feature_id': feature_id,
        'task_id': task_id, 'task_attempt': task_attempt,
        'feature_fingerprint': feature_fingerprint, 'lifecycle_generation': generation,
        'family': family, 'profile_id': profile_id, 'profile_hash': profile.content_hash,
        'policy_checkpoint': plan.family.policy_checkpoint,
        'candidate_identity': family['candidate_identity'],
        'final_changed_surface_id': family['final_changed_surface_id'],
        'origin_binding': origin_binding, 'obligations': obligations,
        'execution_units': units,
        'task_commands': [dict(command=item['command'], cwd=item.get('cwd', '.'))
                          if isinstance(item, dict) else {'command': item, 'cwd': '.'}
                          for item in task_commands]}
    body['plan_id'] = 'verification-plan-v2:sha256:' + hashlib.sha256(canonical(body)).hexdigest()
    return body


def publish_and_accept(repository: pathlib.Path, feature_dir: pathlib.Path, record: dict,
                       *, expected_generation: int, lifecycle, profile_root: pathlib.Path):
    """Subordinate immutable publication followed by authoritative state CAS.

    ``lifecycle`` is the consumer's lifecycle port; this package ships none and never
    writes lifecycle state. It provides ``accept_verification_plan``,
    ``resolve_accepted_verification_plan``, ``load_validated``, ``load_state`` and
    ``feature_fingerprint`` with the Showcase ``harness`` signatures.
    """
    from .store import VerificationStore
    store = VerificationStore(repository, profile_root=profile_root)
    store.publish_plan_record(record)
    return lifecycle.accept_verification_plan(feature_dir, record,
                                              expected_generation=expected_generation)


def resolve_accepted(repository: pathlib.Path, plan_id: str, *, lifecycle) -> dict:
    return lifecycle.resolve_accepted_verification_plan(pathlib.Path(repository), plan_id)


def resolve_execution(repository: pathlib.Path, plan_id: str, *, lifecycle, profile_root: pathlib.Path,
                      unit_id: str | None = None):
    """Resolve exact accepted authority and rebuild the executable plan from it."""
    from .candidate import CandidateSealError, seal_candidate
    from .model import Family
    from .planner import build_plan
    from .profile import load_profile
    from .store import StoreError

    repository = pathlib.Path(repository).resolve(strict=True)
    record = resolve_accepted(repository, plan_id, lifecycle=lifecycle)
    feature_dir = repository / 'docs' / 'specs' / record['feature_id']
    family_record = record['family']
    profile_root = pathlib.Path(profile_root)
    profile_path = (profile_root / (record['profile_id'] + '.json')).resolve(strict=True)
    if profile_path.parent != profile_root.resolve(strict=True):
        raise StoreError('ACCEPTED_PLAN_UNAVAILABLE')
    profile = load_profile(profile_path)
    if (profile.content_hash != record['profile_hash'] or
            family_record.get('profile_hash') != record['profile_hash'] or
            record.get('origin_binding') != family_record.get('origin_policy') or
            family_record.get('policy_checkpoint') != record.get('policy_checkpoint') or
            family_record.get('candidate_identity') != record.get('candidate_identity') or
            family_record.get('final_changed_surface_id') != record.get('final_changed_surface_id')):
        raise StoreError('PLAN_BINDING_MISMATCH')
    try:
        seal = seal_candidate(repository, family_record['base_sha'], {
            'family_id': family_record['id'], 'profile_hash': record['profile_hash'],
            'policy_checkpoint': record['policy_checkpoint'],
            'origin_policy': family_record['origin_policy']},
            trusted_runtime_root=None)
    except CandidateSealError:
        raise StoreError('PLAN_BINDING_MISMATCH') from None
    if (seal.candidate_identity != record['candidate_identity'] or
            seal.changed_surface_id != record['final_changed_surface_id']):
        raise StoreError('PLAN_BINDING_MISMATCH')
    family = Family(family_record['id'], family_record['base_sha'], family_record['origin_policy'],
                    record['profile_hash'], record['policy_checkpoint'],
                    record['candidate_identity'], record['final_changed_surface_id'])
    commands = record['task_commands']
    plan = build_plan(repository, profile, family, task_commands=commands)
    expected = execution_plan_record(record['feature_id'], record['feature_fingerprint'],
        record['lifecycle_generation'], record['profile_id'], profile, plan,
        task_id=record['task_id'], task_attempt=record['task_attempt'],
        task_commands=commands, origin_binding=record['origin_binding'])
    if expected != record:
        raise StoreError('PLAN_BINDING_MISMATCH')
    obligation_by_id = {item['obligation_id']: item for item in record['obligations']}
    units = {item['unit_id']: item for item in record['execution_units']}
    if unit_id is not None and unit_id not in units:
        raise StoreError('PLAN_BINDING_MISMATCH')
    selected_units = [units[unit_id]] if unit_id is not None else list(record['execution_units'])
    selected_obligations = {obligation for unit in selected_units for obligation in unit['obligation_ids']}
    if selected_obligations != {key for key in obligation_by_id if unit_id is None}:
        if unit_id is None:
            raise StoreError('PLAN_BINDING_MISMATCH')
    obligation_order = [item['obligation_id'] for item in record['obligations']]
    selected_indexes = {obligation_order.index(key) for key in selected_obligations}
    decisions = tuple(item for index, item in enumerate(plan.decisions) if index in selected_indexes)
    if len(decisions) != len(selected_indexes):
        raise StoreError('PLAN_BINDING_MISMATCH')
    raise StoreError('VERIFICATION_ORIGIN_ADMISSION_UNAVAILABLE')


def prepare_task_plan(repository: pathlib.Path, feature_dir: pathlib.Path, task_id: str,
                      task_attempt: int, base_sha: str, task_commands, *, lifecycle,
                      profile_root: pathlib.Path, profile_id: str):
    """Trusted orchestration publishes and lifecycle-accepts post-seal authority."""
    from .candidate import CandidateSealError, seal_candidate
    from .model import Family
    from .planner import build_plan
    from .profile import load_profile
    from .store import StoreError, _validate_component

    repository = pathlib.Path(repository).resolve(strict=True)
    feature_dir = pathlib.Path(feature_dir).resolve(strict=True)
    doc = lifecycle.load_validated(feature_dir)
    state = lifecycle.load_state(feature_dir, doc)
    task_state = state.get('tasks', {}).get(task_id)
    if (not isinstance(task_state, dict) or task_state.get('status') != 'running' or
            task_state.get('attempts') != task_attempt):
        raise StoreError('ACCEPTED_PLAN_UNAVAILABLE')
    _validate_component(profile_id, 'ACCEPTED_PLAN_UNAVAILABLE')
    profile = load_profile(pathlib.Path(profile_root) / (profile_id + '.json'))
    family_id = f'{feature_dir.name.lower()}-{task_id.lower()}-attempt-{task_attempt}'
    policy_checkpoint = profile.content_hash
    origin_binding = 'task-completion'
    try:
        seal = seal_candidate(repository, base_sha, {
            'family_id': family_id, 'profile_hash': profile.content_hash,
            'policy_checkpoint': policy_checkpoint, 'origin_policy': origin_binding})
    except CandidateSealError as exc:
        raise StoreError(exc.reason) from None
    family = Family(family_id, base_sha, origin_binding, profile.content_hash,
                    policy_checkpoint, seal.candidate_identity, seal.changed_surface_id)
    commands = [dict(command=item['command'], cwd=item.get('cwd', '.'))
                if isinstance(item, dict) else {'command': item, 'cwd': '.'} for item in task_commands]
    plan = build_plan(repository, profile, family, task_commands=commands)
    record = execution_plan_record(doc['feature'], lifecycle.feature_fingerprint(feature_dir),
        state.get('feature_generation', 1), profile_id, profile, plan,
        task_id=task_id, task_attempt=task_attempt, task_commands=commands,
        origin_binding=origin_binding)
    publish_and_accept(repository, feature_dir, record,
                       expected_generation=state.get('feature_generation', 1),
                       lifecycle=lifecycle, profile_root=profile_root)
    return record


INDEPENDENT_CLASS = 'harness-managed-independent-execution-v1'


def validate_plan_record(record, *, profile_root, repository=None, reconstruct=False):
    """One fail-closed validator for publication and lifecycle authority."""
    from .store import StoreError, _validate_component
    from .profile import load_profile, command_identity
    from .model import InvalidPolicy, Family
    required = {'schema_version', 'plan_id', 'feature_id', 'task_id', 'task_attempt',
        'feature_fingerprint', 'lifecycle_generation', 'family', 'profile_id', 'profile_hash',
        'policy_checkpoint', 'candidate_identity', 'final_changed_surface_id',
        'origin_binding', 'obligations', 'execution_units', 'task_commands'}
    try:
        if not isinstance(record, dict) or set(record) != required or type(record['schema_version']) is not int or record['schema_version'] != 2:
            raise ValueError()
        for key in ('feature_id', 'task_id', 'profile_id'):
            _validate_component(record[key], 'invalid-verification-plan')
        for key in ('feature_fingerprint', 'profile_hash'):
            if not isinstance(record[key], str) or len(record[key]) != 64 or any(c not in '0123456789abcdef' for c in record[key]): raise ValueError()
        if not isinstance(record['policy_checkpoint'], str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', record['policy_checkpoint']): raise ValueError()
        for key in ('task_attempt', 'lifecycle_generation'):
            if type(record[key]) is not int or record[key] < 1: raise ValueError()
        body = {k: v for k, v in record.items() if k != 'plan_id'}
        if record['plan_id'] != 'verification-plan-v2:sha256:' + hashlib.sha256(canonical(body)).hexdigest(): raise ValueError()
        profile_root = pathlib.Path(profile_root)
        profile_path = (profile_root / (record['profile_id'] + '.json')).resolve(strict=True)
        if profile_path.parent != profile_root.resolve(strict=True): raise ValueError()
        profile = load_profile(profile_path)
        if profile.content_hash != record['profile_hash']: raise ValueError()
        family = record['family']
        if set(family) != {'id', 'base_sha', 'origin_policy', 'profile_hash', 'policy_checkpoint', 'candidate_identity', 'final_changed_surface_id'}: raise ValueError()
        _validate_component(family['id'], 'invalid-verification-plan')
        if not isinstance(family['base_sha'], str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', family['base_sha']): raise ValueError()
        if family['origin_policy'] not in ('integration', 'task-completion'): raise ValueError()
        for key in ('candidate_identity', 'final_changed_surface_id'):
            if not isinstance(record[key], str) or not re.fullmatch(r'[0-9a-f]{64}', record[key]): raise ValueError()
        for key in ('profile_hash', 'policy_checkpoint', 'candidate_identity', 'final_changed_surface_id'):
            if family[key] != record[key] or not isinstance(record[key], str) or not record[key]: raise ValueError()
        if family['origin_policy'] != record['origin_binding']: raise ValueError()
        if not isinstance(record['task_commands'], list): raise ValueError()
        for item in record['task_commands']:
            if not isinstance(item, dict) or set(item) != {'command', 'cwd'}: raise ValueError()
            command_identity(item['command'], item['cwd'])
        identity_keys = {'family_id', 'gate_id', 'profile_gate_id', 'occurrence', 'ordinal', 'requirement_source', 'required_origin', 'independent_execution_class'}
        obligation_keys = identity_keys | {'obligation_id', 'command', 'command_hash', 'cwd', 'dependencies', 'critical', 'retry_policy', 'retry_controls', 'sandbox'}
        obligations = record['obligations']; units = record['execution_units']
        if not isinstance(obligations, list) or not obligations or not isinstance(units, list) or len(units) != len(obligations): raise ValueError()
        seen = set()
        by_gate = {g.id: g for g in profile.gates}
        for item, unit in zip(obligations, units):
            if not isinstance(item, dict) or set(item) != obligation_keys: raise ValueError()
            task = item['occurrence'] or item['profile_gate_id'] is None
            if type(item['occurrence']) is not bool or type(item['ordinal']) is not int or item['ordinal'] < 0 or item['family_id'] != family['id']: raise ValueError()
            origin = 'task' if task else 'independent'; cls = None if task else INDEPENDENT_CLASS
            if (item['required_origin'], item['independent_execution_class'], item['requirement_source']) != (origin, cls, 'task-command' if task else 'profile'): raise ValueError()
            if task and not item['gate_id'].startswith(('task-command:', 'legacy-task-command:')): raise ValueError()
            if type(item['critical']) is not bool or item['retry_policy'] not in ('allow', 'forbid') or item['sandbox'] not in ('required', 'best-effort', 'off'): raise ValueError()
            if not isinstance(item['retry_controls'], list) or any(not isinstance(c, str) or not c for c in item['retry_controls']) or len(set(item['retry_controls'])) != len(item['retry_controls']): raise ValueError()
            if item['critical'] and item['retry_policy'] != 'forbid': raise ValueError()
            gate = by_gate.get(item['profile_gate_id'])
            if not task and (gate is None or cls not in gate.independent_execution_classes or item['sandbox'] != 'required' or item['gate_id'] != gate.id): raise ValueError()
            if item['command_hash'] != command_identity(item['command'], item['cwd']): raise ValueError()
            if gate is not None:
                for key in ('command', 'command_hash', 'cwd', 'critical', 'retry_policy', 'sandbox'):
                    if item[key] != getattr(gate, key): raise ValueError()
                if item['retry_controls'] != list(gate.retry_controls): raise ValueError()
            if not isinstance(item['dependencies'], list) or any(not isinstance(d, str) or d not in by_gate for d in item['dependencies']) or len(set(item['dependencies'])) != len(item['dependencies']): raise ValueError()
            expected_dependencies = list(dict.fromkeys((*gate.depends_on, *(a.producer for a in gate.consumes)))) if gate is not None else []
            if item['dependencies'] != expected_dependencies: raise ValueError()
            identity = {k: item[k] for k in identity_keys}
            oid = 'verification-obligation-v2:sha256:' + hashlib.sha256(canonical(identity)).hexdigest()
            if item['obligation_id'] != oid or oid in seen: raise ValueError()
            seen.add(oid)
            expected_unit = {'obligation_ids': [oid], 'required_origin': origin, 'independent_execution_class': cls}
            uid = 'verification-unit-v2:sha256:' + hashlib.sha256(canonical(expected_unit)).hexdigest()
            if unit != {'unit_id': uid, **expected_unit}: raise ValueError()
        if reconstruct:
            from .candidate import seal_candidate, CandidateSealError
            from .planner import build_plan
            try:
                seal = seal_candidate(repository, family['base_sha'], {'family_id': family['id'], 'profile_hash': record['profile_hash'], 'policy_checkpoint': record['policy_checkpoint'], 'origin_policy': family['origin_policy']}, trusted_runtime_root=None)
            except CandidateSealError:
                raise ValueError() from None
            if seal.candidate_identity != record['candidate_identity'] or seal.changed_surface_id != record['final_changed_surface_id']: raise ValueError()
            plan = build_plan(repository, profile, Family(**family), task_commands=record['task_commands'])
            expected = execution_plan_record(record['feature_id'], record['feature_fingerprint'], record['lifecycle_generation'], record['profile_id'], profile, plan, task_id=record['task_id'], task_attempt=record['task_attempt'], task_commands=record['task_commands'], origin_binding=record['origin_binding'])
            if expected != record: raise ValueError()
        return record
    except StoreError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        raise StoreError('invalid-verification-plan') from None
