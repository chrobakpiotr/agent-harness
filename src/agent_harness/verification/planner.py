"""Deterministic advisory DAG and ready-gate rebinding.

No commands are executed and no runtime state is written here. The executor must
admit the repository under its canonical lock, call evaluate_ready_gate immediately
before each action, and validate terminal receipts from the trusted store.
"""
import dataclasses
import heapq
from collections.abc import Mapping

from .fingerprint import ObservationSession, changed_surface
from .model import Decision, Evidence, Family, FileIdentity, Gate, InvalidPolicy, Node, Plan
from .profile import command_identity, matches, probe_value
from .serialization import HASH, IDENTIFIER, default_safety, diagnostic_provider_artifact_path, digest, evidence_record


def _family(profile, family, safety):
    if (family.origin_policy not in ('integration', 'task-completion') or
            family.profile_hash != profile.content_hash or
            not IDENTIFIER.fullmatch(family.id) or not HASH.fullmatch(family.policy_checkpoint) or
            not safety.safe(family.id)):
        raise InvalidPolicy('invalid-family-binding')


def required_nodes(profile, family, surface, task_commands=(), *, safety=None):
    safety = safety or default_safety()
    by_id = {g.id: g for g in profile.gates}
    ordinal = {g.id: i for i, g in enumerate(profile.gates)}
    by_command = {g.command_hash: g for g in profile.gates}
    nodes, fresh_ids, occurrences = {}, set(), []

    def add_profile(id, fresh=False):
        g = by_id[id]
        if fresh: fresh_ids.add(id)
        if id not in nodes:
            dependencies = tuple(dict.fromkeys((*g.depends_on, *(a.producer for a in g.consumes))))
            nodes[id] = Node(id, g, id, dependencies, ordinal[id])
            for dep in dependencies: add_profile(dep, fresh)
        elif fresh:
            for dep in nodes[id].dependencies:
                if dep not in fresh_ids: add_profile(dep, True)

    for index, command in enumerate(task_commands):
        if isinstance(command, str): command, cwd = command, '.'
        elif isinstance(command, Mapping) and set(command) == {'command', 'cwd'}:
            command, cwd = command['command'], command['cwd']
        else: raise InvalidPolicy('invalid-task-command')
        identity = command_identity(command, cwd, safety=safety)
        mapped = by_command.get(identity)
        if family.origin_policy == 'integration':
            if mapped is None:
                id = f'legacy-task-command:{index:04d}:{identity[:16]}'
                g = Gate(id, command, identity, cwd=cwd, cacheable=False)
                nodes[id] = Node(id, g, None, ordinal=len(profile.gates) + index)
            else:
                add_profile(mapped.id)
            continue
        prefix = 'task-command' if mapped else 'legacy-task-command'
        id = f'{prefix}:{index:04d}:{identity[:16]}'
        if mapped:
            dependencies = tuple(dict.fromkeys((*mapped.depends_on, *(a.producer for a in mapped.consumes))))
            for dep in dependencies: add_profile(dep, True)
        else:
            dependencies = ()
        g = mapped or Gate(id, command, identity, cwd=cwd, cacheable=False)
        occurrences.append(Node(id, g, mapped.id if mapped else None, dependencies, index, True, True))
    for g in profile.gates:
        # Mapping borrows policy; task occurrences never discharge a separately
        # applicable mandatory profile requirement (OBLIGATION-02/03).
        if g.mandatory and any(matches(p, path) for p in g.applicability for path in surface.paths):
            add_profile(g.id)
        # ** is the explicit unconditional applicability rule, even on a clean tree.
        elif g.mandatory and g.applicability == ('**',):
            add_profile(g.id)
    nodes = {id: dataclasses.replace(node, fresh=id in fresh_ids) for id, node in nodes.items()}
    nodes.update({n.id: n for n in occurrences})
    edges = {id: set(n.dependencies) for id, n in nodes.items()}
    for previous, current in zip(occurrences, occurrences[1:]): edges[current.id].add(previous.id)
    # Kahn ordering: readiness, profile nodes before occurrences, ordinal, stable id.
    children = {id: [] for id in nodes}
    for id, deps in edges.items():
        for dep in deps: children[dep].append(id)
    ready = []
    def push(id):
        n = nodes[id]
        heapq.heappush(ready, (int(n.occurrence), n.ordinal, id))
    for id, deps in edges.items():
        if not deps: push(id)
    ordered = []
    while ready:
        _, _, id = heapq.heappop(ready)
        ordered.append(nodes[id])
        for child in children[id]:
            edges[child].remove(id)
            if not edges[child]: push(child)
    if len(ordered) != len(nodes): raise InvalidPolicy('invalid-occurrence-dag')
    return tuple(ordered)


def valid_evidence(value, *, safety=None):
    """Validate complete structured receipts, never a cache index or provider claim.

The store is responsible for ownership-token authentication against its journal.
This structural check cannot establish control-store provenance by itself.
"""
    if not isinstance(value, Evidence): return False
    try:
        record = evidence_record(value, safety=safety)
        record.pop('receipt_hash')
        return (type(value.schema_version) is int and value.schema_version == 2 and value.status == 'pass' and
                type(value.exit_code) is int and value.exit_code == 0 and
                bool(value.ownership_token) and value.pre_fingerprint == value.post_fingerprint and
                ((value.candidate_identity is None and value.final_changed_surface_id is None) or
                 (value.candidate_identity is not None and value.final_changed_surface_id is not None)) and
                bool(value.pre_fingerprint) and 0 <= value.started_at <= value.ended_at and
                type(value.process_invocations) is int and value.process_invocations >= 1 and
                (value.retry_policy != 'forbid' or value.process_invocations == 1) and
                digest(record) == value.receipt_hash)
    except (ValueError, TypeError, AttributeError): return False


class _Evaluation:
    def __init__(self, root, profile, family, evidence, probes, safety, surface=None):
        self.root, self.profile, self.family = root, profile, family
        self.evidence, self.probes, self.safety = dict(evidence or {}), probes or {}, safety
        self.surface = surface or changed_surface(root, family.base_sha)
        self.session = ObservationSession(root, self.surface, safety)
        self.by_id = {g.id: g for g in profile.gates}
        self.memo = {}
        # Hold the supplied receipts for this evaluation only. Object identity avoids
        # hashing the receipt to find its validation result, and shares aliases too.
        self.receipts = {}

    def receipt(self, gate_id):
        value = self.evidence.get(gate_id)
        key = id(value)
        if key not in self.receipts:
            valid = valid_evidence(value, safety=self.safety)
            # Preserve FileIdentity equality used by artifact observation. Malformed
            # or alternate runtime objects cannot become artifact membership claims.
            artifacts = frozenset(a for a in value.artifacts if isinstance(a, FileIdentity)) if valid else frozenset()
            self.receipts[key] = (valid, artifacts)
        return self.receipts[key]

    def dependency(self, id):
        if id not in self.memo:
            g = self.by_id[id]
            deps = tuple(dict.fromkeys((*g.depends_on, *(a.producer for a in g.consumes))))
            node = Node(id, g, id, deps)
            self.memo[id] = self.evaluate(node, continuation=True)
        return self.memo[id]

    def evaluate(self, node, *, continuation):
        gate, family = node.gate, self.family
        observation = self.session.inputs(gate.inputs)
        reasons = list(observation.reasons)
        if any(diagnostic_provider_artifact_path(p) for p in (*gate.inputs, *gate.produces, *(a.path for a in gate.consumes))):
            reasons.append('non-cacheable-policy')
        if not gate.cacheable: reasons.append('legacy-task-command' if node.profile_gate_id is None else 'non-cacheable-policy')
        if gate.opaque_environment or gate.opaque_external_state: reasons.append('non-cacheable-external-state')
        probes = []
        for p in gate.probes:
            raw = self.probes.get(gate.id, {}).get(p.id)
            value = probe_value(p, raw, self.safety)
            if value is None:
                reasons.append('non-cacheable-sensitive-identity' if isinstance(raw, str) and not self.safety.safe(raw)
                               else 'non-cacheable-probe')
            else: probes.append((p.id, p.format, value))
        bindings, dependencies_valid = [], True
        consumed = []
        for dep in node.dependencies:
            decision = self.dependency(dep)
            receipt = self.evidence.get(dep)
            if decision.action != 'REUSE': dependencies_valid = False
            if self.receipt(dep)[0]:
                bindings.append((dep, receipt.evidence_id, receipt.pre_fingerprint))
            else:
                bindings.append((dep, '', ''))
        for artifact in gate.consumes:
            try:
                current = self.session.artifacts((artifact.path,))
                valid, artifacts = self.receipt(artifact.producer)
                if not valid or current[0] not in artifacts:
                    dependencies_valid = False
                consumed.extend(dataclasses.asdict(x) for x in current)
            except (ValueError, OSError): dependencies_valid = False
        fingerprint = None
        if not reasons:
            fingerprint = digest(dict(schema_version=self.profile.schema_version,
                profile_hash=self.profile.content_hash, gate_id=gate.id, node_id=node.id,
                command_hash=gate.command_hash, cwd=gate.cwd, origin_policy=family.origin_policy,
                sandbox=gate.sandbox, retry_policy=gate.retry_policy, critical=gate.critical,
                retry_controls=gate.retry_controls, inputs=gate.inputs,
                manifest=[dataclasses.asdict(x) for x in observation.manifest],
                dependencies=bindings, probes=probes, policy_checkpoint=family.policy_checkpoint,
                repository_id=self.surface.repository_id, artifacts=consumed,
                candidate_identity=family.candidate_identity,
                final_changed_surface_id=family.final_changed_surface_id))
        prior = self.evidence.get(node.id)
        valid, _ = self.receipt(node.id)
        artifact_valid = True
        if valid:
            try: artifact_valid = self.session.artifacts(gate.produces) == prior.artifacts
            except (ValueError, OSError): artifact_valid = False
        reason, classification = 'no-reusable-evidence', 'RUN_NOW'
        if reasons:
            reason = ('non-cacheable-sensitive-identity' if 'non-cacheable-sensitive-identity' in reasons
                      else 'non-cacheable-symlink-input' if 'non-cacheable-symlink-input' in reasons else reasons[0])
        elif family.origin_policy == 'task-completion' and node.fresh and not continuation:
            reason = 'fresh-task-completion'
        elif valid and family.origin_policy == 'task-completion' and prior.family_id != family.id:
            reason = 'different-family'
        elif valid:
            binding_matches = (prior.pre_fingerprint == fingerprint and prior.repository_id == self.surface.repository_id
                and prior.profile_hash == self.profile.content_hash and prior.policy_checkpoint == family.policy_checkpoint
                and prior.command_hash == gate.command_hash and prior.gate_id == node.id
                and prior.origin_policy == family.origin_policy and prior.sandbox == gate.sandbox
                and prior.retry_policy == gate.retry_policy and prior.dependencies == tuple(bindings)
                and prior.candidate_identity == family.candidate_identity
                and prior.final_changed_surface_id == family.final_changed_surface_id)
            if binding_matches and dependencies_valid and artifact_valid:
                classification, reason = 'ALREADY_GREEN', 'exact-evidence'
            else:
                classification = 'INVALIDATED_BY_THIS_PATCH'
                reason = ('dependency-not-reusable' if not dependencies_valid else
                          'artifact-mismatch' if not artifact_valid else 'inputs-changed')
        elif prior is not None: reason = 'invalid-cache'
        elif not dependencies_valid: reason = 'dependency-not-reusable'
        return Decision(node, fingerprint, classification, 'REUSE' if classification == 'ALREADY_GREEN' else 'RUN',
                        reason, self.surface.repository_id, tuple(bindings), not reasons, dependencies_valid)


def build_plan(root, profile, family, *, task_commands=(), continuation=False, evidence=None, probes=None, safety=None):
    safety = safety or default_safety()
    _family(profile, family, safety)
    surface = changed_surface(root, family.base_sha)
    nodes = required_nodes(profile, family, surface, task_commands, safety=safety)
    evaluation = _Evaluation(root, profile, family, evidence, probes, safety, surface)
    return Plan(family, tuple(evaluation.evaluate(n, continuation=continuation) for n in nodes))


def evaluate_ready_gate(root, profile, family, node, *, evidence=None, probes=None, continuation=False,
                        safety=None, trusted_runtime_root=None):
    """Reobserve current files/dependencies/artifacts. Caller owns admission and lock.

Never pass a fingerprint from an advisory plan as authority. This function has no
side effects and deliberately does not claim repository admission on its own.
"""
    safety = safety or default_safety()
    _family(profile, family, safety)
    if family.candidate_identity is not None or family.final_changed_surface_id is not None:
        if family.candidate_identity is None or family.final_changed_surface_id is None:
            raise InvalidPolicy('candidate-binding-incomplete')
        from .candidate import CandidateSealError, seal_candidate
        try:
            current_candidate = seal_candidate(root, family.base_sha, {
                'family_id': family.id, 'profile_hash': family.profile_hash,
                'policy_checkpoint': family.policy_checkpoint,
                'origin_policy': family.origin_policy,
            }, trusted_runtime_root=trusted_runtime_root)
        except CandidateSealError as exc:
            raise InvalidPolicy(exc.reason) from None
        if (current_candidate.candidate_identity != family.candidate_identity or
                current_candidate.changed_surface_id != family.final_changed_surface_id):
            raise InvalidPolicy('sealed-candidate-mutated')
    by_id = {g.id: g for g in profile.gates}
    if node.profile_gate_id is not None:
        gate = by_id.get(node.profile_gate_id)
        if gate != node.gate:
            raise InvalidPolicy('stale-gate-policy')
        dependencies = tuple(dict.fromkeys((*gate.depends_on, *(a.producer for a in gate.consumes))))
        if node.dependencies != dependencies:
            raise InvalidPolicy('stale-gate-dependencies')
    elif node.gate.cacheable or node.dependencies:
        raise InvalidPolicy('invalid-legacy-node')
    return _Evaluation(root, profile, family, evidence, probes, safety).evaluate(node, continuation=continuation)


def seal_pass(before, after, *, family, evidence_id, ownership_token, started_at, ended_at,
              artifacts, process_invocations=1, safety=None, candidate_identity=None,
              final_changed_surface_id=None):
    """Construct a terminal PASS only after the executor independently observes post inputs.

Publication and journal validation belong to the store/executor. Non-cacheable
executions need diagnostic terminal records, not a reusable PASS from this helper.
"""
    if not before.fingerprint or before.fingerprint != after.fingerprint or before.node != after.node:
        raise ValueError('stale-input')
    if not before.dependencies_reusable or not after.dependencies_reusable or any(not e or not f for _, e, f in before.dependencies):
        raise ValueError('dependency-not-reusable')
    if tuple(a.path for a in artifacts) != tuple(sorted(before.node.gate.produces)):
        raise ValueError('invalid-artifact-identity')
    if any(a.kind != 'file' or not a.content_hash for a in artifacts): raise ValueError('invalid-artifact-identity')
    g = before.node.gate
    if any(diagnostic_provider_artifact_path(p) for p in (*g.inputs, *g.produces, *(a.path for a in g.consumes))):
        raise ValueError('diagnostic-provider-artifact-identity')
    value = Evidence(2, evidence_id, family.id, ownership_token, before.node.id, before.repository_id,
                     family.profile_hash, family.policy_checkpoint, g.command_hash, family.origin_policy,
                     before.fingerprint, after.fingerprint, g.sandbox, g.retry_policy, process_invocations,
                     0, started_at, ended_at, 'pass', tuple(artifacts), before.dependencies, '',
                     candidate_identity, final_changed_surface_id)
    record = evidence_record(value, safety=safety, include_receipt=False)
    value = dataclasses.replace(value, receipt_hash=digest(record))
    if not valid_evidence(value, safety=safety): raise ValueError('invalid-terminal-evidence')
    return value
