"""Registered retry controls: consumer-declared proofs that a critical gate runs without internal retries.

A critical `forbid` gate is retry-free only for the literal no-op command or for a control the consumer
registers: the exact gate and command, the wrapper digest, and the digests of every input the control reads.
The consumer collects the evidence (it knows its build tool); the library checks it against the registration.
With an empty registry only the no-op control holds. From Showcase `tooling/agent-harness/verification/store.py`
and `supervisor.py` at `49ab97f`, where the registry was Showcase's; see docs/migration/provenance.md.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .serialization import canonical

NO_RETRY_COMMAND = 'python3 -c pass'
_SHA256 = re.compile(r'[0-9a-f]{64}')


@dataclass(frozen=True)
class RetryControl:
    control_id: str
    gate_id: str
    command: str
    wrapper_sha256: str
    config_path: str
    inputs: Mapping[str, str]
    # (worktree, control) -> evidence dict; any exception is a retry-policy violation.
    collect_evidence: Callable
    # (backend_run_dir, environment) -> None; any exception is a retry-policy violation.
    check_run_dir: Callable
    run_namespace: str = 'registered-retry-control'

    def inputs_digest(self) -> str:
        return hashlib.sha256(canonical({'control_id': self.control_id,
                                         'inputs': dict(sorted(self.inputs.items()))})).hexdigest()


def registry(controls=()) -> dict[str, RetryControl]:
    """Validate a consumer's controls; a malformed registration refuses the whole registry."""
    result = {}
    for control in controls:
        if (not isinstance(control, RetryControl) or control.control_id in result or
                not all(isinstance(value, str) and value for value in
                        (control.control_id, control.gate_id, control.command, control.config_path,
                         control.run_namespace)) or
                not _SHA256.fullmatch(str(control.wrapper_sha256)) or not isinstance(control.inputs, Mapping) or
                control.config_path not in control.inputs or
                any(not isinstance(path, str) or not path or path.startswith('/') or '..' in path.split('/') or
                    not _SHA256.fullmatch(str(digest)) for path, digest in control.inputs.items()) or
                '/' in control.run_namespace or control.run_namespace in {'.', '..'} or
                not callable(control.collect_evidence) or not callable(control.check_run_dir)):
            raise ValueError('invalid-retry-controls-registry')
        result[control.control_id] = control
    return result


_EVIDENCE_SHAPE = {'schema_version', 'control_id', 'gate_id', 'command', 'config_path', 'config_sha256',
                   'wrapper_sha256', 'registered_inputs_sha256', 'strict_max_retries',
                   'fail_on_passed_after_retry'}


def retry_policy_facts(policy, critical, controls, *, registry, gate_id=None, command=None, command_hash=None,
                       control_evidence=None):
    if not isinstance(policy, str) or policy not in {'forbid', 'allow'} or type(critical) is not bool or not isinstance(controls, list):
        return None
    if any(not isinstance(item, str) or not item for item in controls) or len(set(controls)) != len(controls):
        return None
    # A declaration proves retry-free execution only when it matches the literal
    # no-op command used by unit tests, or includes the supervisor's bound
    # configuration receipt for a registered gate.
    noop_hash = hashlib.sha256(NO_RETRY_COMMAND.encode('utf-8')).hexdigest()
    noop = (controls == ['no-internal-retries'] and
            (command == NO_RETRY_COMMAND or command_hash == noop_hash))
    registered = False
    verified_control = None
    if len(controls) == 1 and controls[0] in registry:
        control = registry[controls[0]]
        expected_hash = hashlib.sha256(control.command.encode('utf-8')).hexdigest()
        evidence = control_evidence
        registered = (gate_id == control.gate_id and
                      (command == control.command or command_hash == expected_hash) and
                      isinstance(evidence, dict) and set(evidence) == _EVIDENCE_SHAPE and
                      evidence.get('schema_version') == 1 and evidence.get('control_id') == controls[0] and
                      evidence.get('gate_id') == control.gate_id and evidence.get('command') == control.command and
                      evidence.get('config_path') == control.config_path and
                      evidence.get('config_sha256') == control.inputs[control.config_path] and
                      isinstance(evidence.get('wrapper_sha256'), str) and
                      evidence.get('wrapper_sha256') == control.wrapper_sha256 and
                      evidence.get('registered_inputs_sha256') == control.inputs_digest() and
                      type(evidence.get('strict_max_retries')) is int and evidence['strict_max_retries'] == 0 and
                      evidence.get('fail_on_passed_after_retry') is True)
        if registered:
            verified_control = evidence
    established = noop or registered
    result = {'schema_version': 1, 'policy': policy, 'critical': critical,
              'retry_controls': controls,
              'retry_free': bool(critical and policy == 'forbid' and established)}
    if verified_control is not None:
        result['control_evidence'] = verified_control
    return result
