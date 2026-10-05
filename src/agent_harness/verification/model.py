"""Immutable values shared by verification entry points."""
from dataclasses import dataclass


class InvalidPolicy(ValueError):
    """Invalid trusted policy/input. Messages contain stable codes only."""


@dataclass(frozen=True)
class Probe:
    id: str
    format: str = 'version'


@dataclass(frozen=True)
class Artifact:
    producer: str
    path: str


@dataclass(frozen=True)
class Gate:
    id: str
    command: str
    command_hash: str
    cwd: str = '.'
    inputs: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    applicability: tuple[str, ...] = ('**',)
    mandatory: bool = False
    cacheable: bool = False
    critical: bool = False
    retry_policy: str = 'forbid'
    retry_controls: tuple[str, ...] = ()
    sandbox: str = 'required'
    probes: tuple[Probe, ...] = ()
    produces: tuple[str, ...] = ()
    consumes: tuple[Artifact, ...] = ()
    opaque_environment: bool = False
    opaque_external_state: bool = False
    description: str = ''
    category: str = 'verification'
    expensive: bool = False
    aggregate: bool = False
    independent_execution_classes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Profile:
    schema_version: int
    content_hash: str
    gates: tuple[Gate, ...]


@dataclass(frozen=True)
class Family:
    id: str
    base_sha: str
    origin_policy: str
    profile_hash: str
    policy_checkpoint: str
    candidate_identity: str | None = None
    final_changed_surface_id: str | None = None


@dataclass(frozen=True)
class Surface:
    paths: tuple[str, ...]
    deleted: tuple[str, ...]
    tracked: tuple[str, ...]
    repository_id: str
    base_sha: str


@dataclass(frozen=True)
class FileIdentity:
    path: str
    kind: str
    content_hash: str | None = None
    executable: bool = False


@dataclass(frozen=True)
class Observation:
    manifest: tuple[FileIdentity, ...]
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Node:
    id: str
    gate: Gate
    profile_gate_id: str | None
    dependencies: tuple[str, ...] = ()
    ordinal: int = 0
    occurrence: bool = False
    fresh: bool = False


@dataclass(frozen=True)
class Decision:
    node: Node
    fingerprint: str | None
    decision: str
    action: str
    reason: str
    repository_id: str
    dependencies: tuple[tuple[str, str, str], ...] = ()
    cacheable: bool = False
    dependencies_reusable: bool = True

    def to_record(self, *, safety=None):
        from .serialization import safe_record
        return safe_record(dict(gate_id=self.node.id, profile_gate_id=self.node.profile_gate_id,
                                fingerprint=self.fingerprint, decision=self.decision, action=self.action,
                                reason=self.reason, repository_id=self.repository_id,
                                cacheable=self.cacheable), safety=safety)


@dataclass(frozen=True)
class Plan:
    family: Family
    decisions: tuple[Decision, ...]
    advisory: bool = True

    def to_record(self, *, safety=None):
        from .serialization import safe_record
        return safe_record(dict(schema_version=2, advisory=True, family_id=self.family.id,
                                base_sha=self.family.base_sha, origin_policy=self.family.origin_policy,
                                profile_hash=self.family.profile_hash,
                                candidate_identity=self.family.candidate_identity,
                                final_changed_surface_id=self.family.final_changed_surface_id,
                                decisions=[d.to_record(safety=safety) for d in self.decisions]), safety=safety)


@dataclass(frozen=True)
class Evidence:
    schema_version: int
    evidence_id: str
    family_id: str
    ownership_token: str
    gate_id: str
    repository_id: str
    profile_hash: str
    policy_checkpoint: str
    command_hash: str
    origin_policy: str
    pre_fingerprint: str
    post_fingerprint: str
    sandbox: str
    retry_policy: str
    process_invocations: int
    exit_code: int
    started_at: float
    ended_at: float
    status: str
    artifacts: tuple[FileIdentity, ...]
    dependencies: tuple[tuple[str, str, str], ...]
    receipt_hash: str
    candidate_identity: str | None = None
    final_changed_surface_id: str | None = None


@dataclass(frozen=True)
class FailureKey:
    repository_id: str
    profile_hash: str
    gate_id: str
    fingerprint: str


@dataclass(frozen=True)
class Grant:
    id: str
    failure_id: str
    key: FailureKey
