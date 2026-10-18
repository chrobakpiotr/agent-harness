"""Authoritative, restart-reconcilable lifecycle for verification executions.

From Showcase `tooling/agent-harness/verification/supervisor.py` at `49ab97f`. The Gradle retry evidence is
consumer policy behind `agent_harness.verification.retry` (the store's registered controls); see
docs/migration/provenance.md.
"""
from __future__ import annotations

import enum
import hashlib
import json
import os
import pathlib
import pickle
import re
import shlex
import stat
import threading
import time
import uuid
import weakref
from dataclasses import dataclass, replace

from .serialization import canonical
from .store import (ReconciliationOutcome, StoreError, VerificationStore,
                    _fsync_directory, publish_create_once, repository_lock, candidate_failure_fingerprint)
from .retry import retry_policy_facts


class SupervisorError(RuntimeError):
    """Execution lifecycle could not be established safely."""


_LAUNCH_CAPABILITY_SEAL = object()
_LAUNCH_CAPABILITY_LOCK = threading.Lock()
_CONSUMED_LAUNCH_CAPABILITIES = weakref.WeakSet()


class LaunchCapability:
    """Ephemeral, process-local proof of one durable launch consumption."""
    __slots__ = ('consumption_id', 'reservation_id', '_seal', '__weakref__')

    def __init__(self, consumption_id: str, reservation_id: str, *, _seal=None):
        if _seal is not _LAUNCH_CAPABILITY_SEAL:
            raise ValueError('launch-capability-unavailable')
        self.consumption_id = consumption_id
        self.reservation_id = reservation_id
        self._seal = _seal


def issue_launch_capability(consumption_id: str, reservation_id: str) -> LaunchCapability:
    """Create the in-memory capability returned to a launch-CAS winner."""
    if not isinstance(consumption_id, str) or not consumption_id:
        raise ValueError('invalid-launch-consumption')
    if not isinstance(reservation_id, str) or not reservation_id:
        raise ValueError('invalid-launch-reservation')
    return LaunchCapability(consumption_id, reservation_id, _seal=_LAUNCH_CAPABILITY_SEAL)


def validate_launch_capability(capability: object, *, consumption_id: str,
                               reservation_id: str) -> bool:
    """Match and atomically spend this process-local capability exactly once."""
    if (not isinstance(capability, LaunchCapability) or
            getattr(capability, '_seal', None) is not _LAUNCH_CAPABILITY_SEAL or
            getattr(capability, 'consumption_id', None) != consumption_id or
            getattr(capability, 'reservation_id', None) != reservation_id):
        return False
    with _LAUNCH_CAPABILITY_LOCK:
        if capability in _CONSUMED_LAUNCH_CAPABILITIES:
            return False
        _CONSUMED_LAUNCH_CAPABILITIES.add(capability)
        return True


_SAFE_ABORT_PROOF_SEAL = object()


@dataclass(frozen=True)
class SafePrelaunchAbortProof:
    """Process-local evidence that a terminal records a safe pre-launch abort."""
    execution_id: str
    receipt_hash: str
    started_hash: str
    _seal: object


def validate_safe_prelaunch_abort_proof(proof: object, terminal: dict) -> bool:
    """Bind an opaque safe-abort proof to the exact terminal receipt."""
    return (isinstance(proof, SafePrelaunchAbortProof) and isinstance(terminal, dict) and
            getattr(proof, '_seal', None) is _SAFE_ABORT_PROOF_SEAL and
            getattr(proof, 'execution_id', None) == terminal.get('execution_id') and
            getattr(proof, 'receipt_hash', None) == terminal.get('receipt_hash') and
            getattr(proof, 'started_hash', None) == terminal.get('started_hash'))


_SAFE_ABORT_AUTHORITY_FIELDS = (
    'launch_reservation_id', 'launch_consumption_id', 'plan_id', 'unit_id',
    'obligation_ids', 'admission_id', 'admission_sha256',
    'reservation_transition_id', 'consumption_transition_id',
    'plan_acceptance_transition_id', 'lifecycle_generation',
)


def _validated_safe_abort_authority_fields(fields: object, started: dict) -> dict:
    if not isinstance(fields, dict) or set(fields) != set(_SAFE_ABORT_AUTHORITY_FIELDS):
        raise SupervisorError('SAFE_PRELAUNCH_ABORT_AUTHORITY_UNAVAILABLE')
    if (fields.get('plan_id') != started.get('plan_id') or
            re.fullmatch(r'launch-reservation-v1:sha256:[0-9a-f]{64}',
                         str(fields.get('launch_reservation_id'))) is None or
            re.fullmatch(r'launch-consumption-v1:sha256:[0-9a-f]{64}',
                         str(fields.get('launch_consumption_id'))) is None or
            re.fullmatch(r'[0-9a-f]{64}', str(fields.get('admission_sha256'))) is None or
            not isinstance(fields.get('unit_id'), str) or not fields['unit_id'] or
            not isinstance(fields.get('obligation_ids'), list) or not fields['obligation_ids'] or
            any(not isinstance(item, str) or re.fullmatch(
                r'verification-obligation-v2:sha256:[0-9a-f]{64}', item) is None
                for item in fields['obligation_ids']) or
            fields['obligation_ids'] != sorted(set(fields['obligation_ids'])) or
            type(fields.get('lifecycle_generation')) is not int or fields['lifecycle_generation'] < 1 or
            any(not isinstance(fields.get(key), str) or not fields[key] for key in (
                'plan_id', 'admission_id', 'reservation_transition_id',
                'consumption_transition_id', 'plan_acceptance_transition_id'))):
        raise SupervisorError('SAFE_PRELAUNCH_ABORT_AUTHORITY_UNAVAILABLE')
    return dict(fields)


def safe_prelaunch_abort_proof(supervisor, journal: pathlib.Path, execution_id: str,
                               terminal: dict, *, reason: str) -> SafePrelaunchAbortProof:
    """Mint proof only for the exact durable receipt of a proven non-launch abort."""
    try:
        store = supervisor.store
        journal = pathlib.Path(journal)
        expected_journal = store.executions / execution_id
        if (not isinstance(execution_id, str) or not execution_id or
                journal != expected_journal or journal.is_symlink() or
                reason not in {'prepared-never-launched', 'backend-proved-not-launched'} or
                not isinstance(terminal, dict) or terminal.get('execution_id') != execution_id or
                terminal.get('result') != 'ABORTED' or
                terminal.get('harness_invocation_upper_bound') != 0 or
                terminal.get('timed_out') is not False or terminal.get('cancelled') is not False or
                terminal.get('output_observation') != 'UNAVAILABLE'):
            raise ValueError()
        started_path = journal / 'started.json'
        marker_path = journal / 'launching.json'
        try:
            marker_path.lstat()
            raise ValueError()
        except FileNotFoundError:
            pass
        if marker_path.is_symlink():
            raise ValueError()
        started = store._validated_started(journal)
        if started.get('execution_id') != execution_id:
            raise ValueError()
        metadata = started_path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ValueError()
        started_hash = hashlib.sha256(started_path.read_bytes()).hexdigest()
        if any(key in terminal for key in _SAFE_ABORT_AUTHORITY_FIELDS):
            authority_path = journal / 'launch-authority.json'
            authority_metadata = authority_path.lstat()
            if not stat.S_ISREG(authority_metadata.st_mode) or stat.S_ISLNK(authority_metadata.st_mode):
                raise ValueError()
            authority = json.loads(authority_path.read_text(encoding='utf-8'))
            if (authority != {'schema_version': 1, 'execution_id': execution_id,
                              **{key: terminal.get(key) for key in _SAFE_ABORT_AUTHORITY_FIELDS}} or
                    any(terminal.get(key) != authority.get(key)
                        for key in _SAFE_ABORT_AUTHORITY_FIELDS)):
                raise ValueError()
        records = store.reconstruct_execution_terminals(rebuild=False)
        matched = [item for item in records if item.get('execution_id') == execution_id]
        if (terminal.get('started_hash') != started_hash or len(matched) != 1 or
                matched[0] != terminal or not isinstance(terminal.get('receipt_hash'), str) or
                re.fullmatch(r'[0-9a-f]{64}', terminal['receipt_hash']) is None):
            raise ValueError()
        return SafePrelaunchAbortProof(execution_id, terminal['receipt_hash'],
                                       started_hash, _SAFE_ABORT_PROOF_SEAL)
    except (OSError, StoreError, TypeError, ValueError, RuntimeError):
        raise SupervisorError('SAFE_PRELAUNCH_ABORT_PROOF_UNAVAILABLE') from None


class SupervisorState(enum.Enum):
    STARTED = 'STARTED'
    ACTIVE = 'ACTIVE'
    CANCELLING = 'CANCELLING'
    DRAINING = 'DRAINING'
    DRAINED = 'DRAINED'
    TERMINAL = 'TERMINAL'
    UNCERTAIN = 'UNCERTAIN'
    ABORTED_PREPARED = 'ABORTED_PREPARED'


@dataclass(frozen=True)
class RecoveryResult:
    execution_id: str
    state: SupervisorState
    reason_code: str
    terminal: dict | None = None


def _registered_retry_evidence(registry, worktree: pathlib.Path, gate_id: str, command: str,
                               controls: tuple[str, ...]) -> dict | None:
    # [seam] A registered control proves retry-free execution only for its exact gate and command; the
    # consumer collects the evidence, which retry_policy_facts then checks against the registration.
    if len(controls) != 1 or controls[0] not in registry:
        return None
    control = registry[controls[0]]
    if gate_id != control.gate_id or command != control.command:
        raise SupervisorError('retry-policy-violation')
    try:
        return control.collect_evidence(worktree, control)
    except Exception:
        raise SupervisorError('retry-policy-violation') from None


class VerificationSupervisor:
    """Serialize admission, durable start, qualified launch, drainage and closure.

    A backend facade implements ``prepare``, ``launch``, ``inspect`` and
    ``cancel_and_drain``. Preparation must return a durable Phase-B execution
    identity and all-pass policy/qualification identities before this class
    publishes STARTED. The legacy callable path is retained only to record a
    known pre-launch refusal; it cannot bypass the qualified facade.
    """

    def __init__(self, store: VerificationStore):
        self.store = store
        self.inject_crash_at: str | None = None

    def _crash(self, boundary: str) -> None:
        if self.inject_crash_at == boundary:
            raise RuntimeError('injected-crash:' + boundary)

    @staticmethod
    def _outcome(result) -> str:
        if getattr(result, 'error', None):
            return 'ERROR'
        if getattr(result, 'timed_out', False):
            return 'TIMEOUT'
        return 'PASS' if getattr(result, 'exit_code', None) == 0 else 'FAIL'

    @staticmethod
    def _read_json(path: pathlib.Path) -> dict | None:
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    @classmethod
    def _launch_marker_state(cls, journal: pathlib.Path, started: dict) -> str:
        path = journal / 'launching.json'
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return 'absent'
        except OSError:
            return 'invalid'
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            return 'invalid'
        marker = cls._read_json(path)
        if (marker is None or marker.get('schema_version') not in {1, 2} or
                marker.get('execution_id') != started.get('execution_id') or
                marker.get('execution_identity') != started.get('execution_identity') or
                marker.get('launch_intent_hash') != started.get('launch_intent_hash')):
            return 'invalid'
        if marker['schema_version'] == 2 and any(
                not isinstance(marker.get(key), str) or
                re.fullmatch(pattern, marker[key]) is None
                for key, pattern in (
                    ('launch_reservation_id', r'launch-reservation-v1:sha256:[0-9a-f]{64}'),
                    ('launch_consumption_id', r'launch-consumption-v1:sha256:[0-9a-f]{64}'))):
            return 'invalid'
        return 'present'

    def _write_state(self, journal: pathlib.Path, name: str, record: dict) -> None:
        publish_create_once(journal / name, record, fault=self.store._fault)

    def _publish_drained(self, journal: pathlib.Path, started: dict, receipt_hash: str,
                         *, reason: str = 'backend-proven-drained') -> None:
        record = {'schema_version': 2, 'execution_id': started['execution_id'],
                  'repository_id': self.store.repository_id, 'backend': started['backend'],
                  'status': 'drained', 'reason': reason, 'terminal_receipt_hash': receipt_hash,
                  'ended_at': time.time()}
        publish_create_once(journal / 'drained.json', record, fault=self.store._fault)

    def execute(self, backend, **kwargs):
        """Delegate ownership to a detached supervisor process before any admission."""
        if not getattr(backend, 'requires_detached_supervisor', False):
            # Deterministic fake backends exercise lifecycle logic in-process;
            # only the supported command facade is allowed to create payloads.
            return self._execute_owned(backend, **kwargs)
        if (os.name != 'posix' or not hasattr(os, 'fork') or
                threading.current_thread() is not threading.main_thread() or
                threading.active_count() != 1):
            raise SupervisorError('TRUSTED_SUPERVISOR_PROCESS_UNAVAILABLE')
        read_fd, write_fd = os.pipe()
        try:
            child = os.fork()
        except OSError:
            os.close(read_fd)
            os.close(write_fd)
            raise SupervisorError('TRUSTED_SUPERVISOR_PROCESS_UNAVAILABLE') from None
        if child == 0:
            os.close(read_fd)
            try:
                os.setsid()
                try:
                    result, ready = self._execute_owned(backend, **kwargs)
                    result_fields = {name: getattr(result, name, None) for name in (
                        'argv', 'cwd', 'started_at', 'ended_at', 'duration_seconds', 'exit_code',
                        'stdout', 'stderr', 'timed_out', 'error', 'sandbox_backend',
                        'strong_isolation', 'protected_paths', 'descendant_containment')}
                    payload = ('ok', result_fields, ready)
                except BaseException as exc:
                    try:
                        pickle.dumps(exc, protocol=pickle.HIGHEST_PROTOCOL)
                        payload = ('exception', exc)
                    except (pickle.PickleError, TypeError, AttributeError):
                        if isinstance(exc, (StoreError, SupervisorError, RuntimeError, ValueError)):
                            payload = ('error', type(exc).__name__, str(exc))
                        else:
                            payload = ('error', 'SupervisorError', 'TRUSTED_SUPERVISOR_FAILED')
                try:
                    with os.fdopen(write_fd, 'wb', closefd=True) as stream:
                        pickle.dump(payload, stream, protocol=pickle.HIGHEST_PROTOCOL)
                except (BrokenPipeError, OSError, pickle.PickleError):
                    pass
            finally:
                os._exit(0)
        os.close(write_fd)
        try:
            with os.fdopen(read_fd, 'rb', closefd=True) as stream:
                payload = pickle.load(stream)
            _, status = os.waitpid(child, 0)
        except (EOFError, OSError, pickle.PickleError, ValueError):
            try:
                os.waitpid(child, 0)
            except ChildProcessError:
                pass
            raise SupervisorError('TRUSTED_SUPERVISOR_RESULT_UNAVAILABLE') from None
        if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
            raise SupervisorError('TRUSTED_SUPERVISOR_RESULT_UNAVAILABLE')
        if not isinstance(payload, tuple) or not payload:
            raise SupervisorError('TRUSTED_SUPERVISOR_RESULT_UNAVAILABLE')
        if payload[0] == 'exception' and len(payload) == 2 and isinstance(payload[1], BaseException):
            raise payload[1]
        if payload[0] == 'error' and len(payload) == 3:
            exception = {'StoreError': StoreError, 'SupervisorError': SupervisorError,
                         'RuntimeError': RuntimeError, 'ValueError': ValueError}.get(
                             payload[1], SupervisorError)
            raise exception(payload[2])
        if payload[0] != 'ok' or len(payload) != 3 or not isinstance(payload[1], dict):
            raise SupervisorError('TRUSTED_SUPERVISOR_RESULT_UNAVAILABLE')
        from .command import CommandResult
        return CommandResult(**payload[1]), payload[2]

    def _execute_owned(self, backend, *, worktree: pathlib.Path, family_id: str,
                attempt_id: str, gate_id: str, command: str, cwd: pathlib.Path,
                run_dir: pathlib.Path, timeout_seconds: float, sandbox_mode: str,
                environment=None, preflight=None, terminal_publisher=None,
                post_observer=None, recovery_observer=None, terminal_record_builder=None,
                input_fingerprint: str | None = None, profile_hash: str | None = None,
                critical: bool = False, retry_policy: str = 'forbid',
                retry_controls: tuple[str, ...] = (), failure_grant_id: str | None = None,
                candidate_identity: str | None = None, final_changed_surface_id: str | None = None,
                plan_id: str | None = None, retry_scope: dict | None = None,
                policy_checkpoint: str | None = None, toolchain_directories=(),
                expected_executable_binding: dict | None = None, launch_authorizer=None,
                prelaunch_recovery_authorizer=None, prelaunch_terminalizer=None):
        if retry_policy not in {'forbid', 'allow'} or not isinstance(critical, bool):
            raise SupervisorError('invalid-retry-policy')
        if not isinstance(retry_controls, (tuple, list)) or any(not isinstance(x, str) for x in retry_controls):
            raise SupervisorError('invalid-retry-controls')
        retry_controls = tuple(retry_controls)
        if len(set(retry_controls)) != len(retry_controls):
            raise SupervisorError('invalid-retry-controls')
        control_evidence = _registered_retry_evidence(
            self.store.retry_controls, worktree, gate_id, command, retry_controls)
        retry_facts = retry_policy_facts(retry_policy, critical, list(retry_controls),
                                          registry=self.store.retry_controls,
                                          gate_id=gate_id, command=command,
                                          control_evidence=control_evidence)
        if retry_facts is None or (critical and retry_policy == 'forbid' and not retry_facts['retry_free']):
            raise SupervisorError('retry-policy-violation')
        retry_policy_proof = retry_facts
        intent_hash = hashlib.sha256(command.encode('utf-8')).hexdigest()
        derived_failure_fingerprint = candidate_failure_fingerprint({
            'repository_id': self.store.repository_id, 'family_id': family_id,
            'policy_checkpoint': policy_checkpoint, 'profile_hash': profile_hash, 'gate_id': gate_id,
            'candidate_identity': candidate_identity,
            'final_changed_surface_id': final_changed_surface_id,
            'launch_intent_hash': intent_hash, 'input_fingerprint': input_fingerprint})
        execution_id = hashlib.sha256(canonical({
            'repository_id': self.store.repository_id, 'family_id': family_id,
            'attempt_id': attempt_id, 'gate_id': gate_id, 'command_hash': intent_hash,
        })).hexdigest()[:32]
        repository_admission_id = execution_id
        if not hasattr(backend, 'prepare'):
            raise SupervisorError('EXECUTION_BACKEND_REQUIRED')

        # A competing request must observe the in-flight admission and fail
        # before waiting long enough to become a new, sequential execution.
        # Recovery remains an explicit operation and may take the normal lock.
        with repository_lock(self.store.root, timeout=0):
            self._recover_locked(lambda _record: backend, recovery_observer,
                prelaunch_recovery_authorizer=prelaunch_recovery_authorizer,
                prelaunch_terminalizer=prelaunch_terminalizer)
            self.store.admit_repository_verification()
            self._crash('after-admission')
            ready = preflight() if preflight else None
            fence_context = {'repository_id': self.store.repository_id, 'profile_hash': profile_hash,
                             'gate_id': gate_id, 'fingerprint': input_fingerprint or derived_failure_fingerprint}
            has_fence_context = all(isinstance(fence_context.get(key), str) and fence_context[key]
                                    for key in ('profile_hash', 'gate_id', 'fingerprint'))
            active_failure = None
            unresolved_failure = None
            if derived_failure_fingerprint is not None and any(
                    not isinstance(failure.get('context', {}).get('fingerprint'), str) or
                    not failure['context']['fingerprint']
                    for failure in self.store.active_failure_fences()):
                raise SupervisorError('CRITICAL_FAILURE_CONTEXT_REQUIRED')
            if has_fence_context:
                unresolved_failure = self.store.current_failure(fence_context)
                latest_failure = self.store.failure_for_scope(fence_context)
                # A PASS-resolved failure permits exact evidence reuse, but a
                # mandatory fresh RUN still needs a new one-shot grant.
                is_reuse = ready is not None and ready.action == 'REUSE'
                active_failure = unresolved_failure if is_reuse else latest_failure
                if is_reuse and unresolved_failure is None:
                    return None, ready
                if active_failure is not None and not failure_grant_id:
                    raise SupervisorError('CRITICAL_FAILURE_FENCE_ACTIVE')
                if active_failure is None and failure_grant_id:
                    raise SupervisorError('FAILURE_GRANT_STALE')
            elif self.store.active_failure_fences():
                # An entry point without trusted plan identity cannot prove it
                # is independent of an active failure fence.
                raise SupervisorError('CRITICAL_FAILURE_CONTEXT_REQUIRED')
            elif failure_grant_id:
                raise SupervisorError('FAILURE_GRANT_SCOPE_MISMATCH')
            if ready is not None and ready.action == 'REUSE':
                # A scoped retry grant turns an otherwise reusable plan into
                # exactly one fresh execution opportunity.
                try:
                    ready = replace(ready, action='RUN', reason='failure-grant-retry')
                except TypeError:
                    ready.action = 'RUN'
                    ready.reason = 'failure-grant-retry'
            backend_run_dir = pathlib.Path(run_dir)
            if control_evidence is not None:
                # A preceding gate can write build-tool state (e.g. Gradle user-home init scripts) under
                # the shared plan run directory. Give each execution a stable,
                # isolated namespace so prepare/start crash replay retains its
                # backend policy identity without reusing another gate's home.
                control = self.store.retry_controls[retry_controls[0]]
                backend_run_dir = backend_run_dir / control.run_namespace / execution_id
                try:
                    control.check_run_dir(backend_run_dir, environment)
                except Exception:
                    raise SupervisorError('retry-policy-violation') from None
            try:
                prepared = backend.prepare(worktree=pathlib.Path(worktree), cwd=pathlib.Path(cwd),
                    run_dir=backend_run_dir, repository_id=self.store.repository_id,
                    command=command, sandbox_mode=sandbox_mode, environment=environment,
                    control_root=self.store.root, execution_id=execution_id,
                    toolchain_directories=toolchain_directories, plan_id=plan_id,
                    expected_executable_binding=expected_executable_binding)
                self._crash('after-prepare')
            except Exception as exc:
                if isinstance(exc, SupervisorError):
                    raise
                raise SupervisorError(getattr(exc, 'reason_code', None) or str(exc) or
                                       'EXECUTION_PREPARE_FAILED') from None
            identity = getattr(prepared, 'identity', None)
            if hasattr(identity, 'to_dict'):
                identity = identity.to_dict()
            if not isinstance(identity, dict) or not identity:
                raise SupervisorError('EXECUTION_IDENTITY_UNAVAILABLE')
            backend_identity = (getattr(prepared, 'backend_identity', None) or
                                identity.get('backend_identity'))
            policy_identity = (getattr(prepared, 'policy_identity', None) or
                               identity.get('policy_identity'))
            qualification = getattr(prepared, 'qualification_fingerprint', None)
            if not all(isinstance(value, str) and value for value in
                       (backend_identity, policy_identity, qualification or policy_identity)):
                raise SupervisorError('EXECUTION_IDENTITY_UNAVAILABLE')

            retry_consumption = None
            if active_failure is not None:
                exact_context = {**fence_context, 'policy_identity': policy_identity,
                                 'backend_identity': backend_identity}
                if not isinstance(retry_scope, dict):
                    raise SupervisorError('FAILURE_GRANT_SCOPE_MISMATCH')
                try:
                    retry_consumption = self.store.consume_failure_grant(
                        failure_grant_id, failure_id=active_failure['failure_id'],
                        context=exact_context, retry_scope=retry_scope, execution_id=execution_id,
                        lock_held=True)
                except StoreError as exc:
                    raise SupervisorError(str(exc)) from None
                self._crash('after-grant-consumed')

            journal = self.store.executions / execution_id
            existing = self._read_json(journal / 'started.json')
            if existing is not None:
                if (existing.get('launch_intent_hash') != intent_hash or
                        existing.get('family_id') != family_id or existing.get('attempt_id') != attempt_id or
                        existing.get('gate_id') != gate_id or existing.get('execution_identity') != identity or
                        existing.get('policy_identity') != policy_identity or
                        existing.get('qualification_fingerprint') != (qualification or policy_identity) or
                        existing.get('backend_identity') != backend_identity or
                        existing.get('input_fingerprint') != input_fingerprint or
                        existing.get('candidate_identity') != candidate_identity or
                        existing.get('final_changed_surface_id') != final_changed_surface_id or
                        existing.get('profile_hash') != profile_hash or
                        existing.get('policy_checkpoint') != policy_checkpoint or
                        existing.get('plan_id') != plan_id or
                        existing.get('retry_policy_proof') != retry_policy_proof):
                    raise StoreError('immutable-record-collision')
                terminal = self.store._read_execution_terminal(journal, existing)
                if terminal is None:
                    raise StoreError('verification-owned')
                observation = self._read_json(journal / 'observation.json') or {}
                result = self._result_from_observation(observation, existing)
                if terminal_publisher:
                    terminal_publisher(result, ready, terminal)
                self.store.reconstruct_execution_terminals()
                return result, ready
            journal.mkdir(parents=True, exist_ok=True)
            started = {
                'schema_version': 2, 'lifecycle_protocol': 1, 'execution_id': execution_id,
                'repository_id': self.store.repository_id, 'worktree': str(pathlib.Path(worktree).resolve()),
                'family_id': family_id, 'attempt_id': attempt_id, 'gate_id': gate_id,
                'backend': backend_identity, 'backend_identity': backend_identity,
                'policy_identity': policy_identity, 'qualification_fingerprint': qualification or policy_identity,
                'execution_identity': identity, 'input_fingerprint': input_fingerprint,
                'candidate_identity': candidate_identity,
                'final_changed_surface_id': final_changed_surface_id,
                'profile_hash': profile_hash, 'policy_checkpoint': policy_checkpoint,
                'critical': bool(critical),
                'plan_id': plan_id,
                'retry_policy': retry_policy, 'retry_controls': list(retry_controls),
                'retry_policy_proof': retry_policy_proof,
                'predecessor_failure_id': active_failure['failure_id'] if retry_consumption else None,
                'consumption_id': retry_consumption['consumption_id'] if retry_consumption else None,
                'retry_proof': ({'failure_id': active_failure['failure_id'],
                    'grant_id': failure_grant_id, 'consumption_id': retry_consumption['consumption_id'],
                    'grant_hash': retry_consumption['grant_hash'], 'retry_scope': retry_scope}
                    if retry_consumption else None),
                'retry_scope': retry_scope if retry_consumption else None,
                'launch_intent_hash': intent_hash, 'command_identity': intent_hash,
                'supervisor_generation': uuid.uuid4().hex, 'protocol_version': 1,
                'started_at': time.time(),
            }
            if derived_failure_fingerprint is not None:
                started['failure_fingerprint'] = derived_failure_fingerprint
            started_hash = publish_create_once(journal / 'started.json', started, fault=self.store._fault)
            _fsync_directory(self.store.executions)
            self._crash('after-started')

            launch_authority = None
            if launch_authorizer is not None:
                if not isinstance(plan_id, str) or not plan_id:
                    raise SupervisorError('VERIFICATION_ORIGIN_ADMISSION_UNAVAILABLE')
                metadata = {
                    'repository_id': self.store.repository_id,
                    'repository_admission_id': repository_admission_id,
                    'execution_id': execution_id,
                    'family_id': family_id,
                    'attempt_id': attempt_id,
                    'gate_id': gate_id,
                    'plan_id': plan_id,
                    'command_hash': intent_hash,
                    'candidate_identity': candidate_identity,
                    'final_changed_surface_id': final_changed_surface_id,
                    'profile_hash': profile_hash,
                    'policy_checkpoint': policy_checkpoint,
                    'input_fingerprint': input_fingerprint,
                }
                try:
                    launch_authority = launch_authorizer(metadata)
                except SupervisorError:
                    raise
                except Exception as exc:
                    raise SupervisorError(getattr(exc, 'reason_code', None) or
                                           'VERIFICATION_LAUNCH_AUTHORIZATION_FAILED') from None
                if (not isinstance(launch_authority, dict) or
                        not isinstance(launch_authority.get('reservation_id'), str) or
                        re.fullmatch(r'launch-reservation-v1:sha256:[0-9a-f]{64}',
                                     launch_authority['reservation_id']) is None or
                        not isinstance(launch_authority.get('consumption_id'), str) or
                        re.fullmatch(r'launch-consumption-v1:sha256:[0-9a-f]{64}',
                                     launch_authority['consumption_id']) is None or
                        launch_authority.get('plan_id') != plan_id or
                        not validate_launch_capability(
                            launch_authority.get('capability'),
                            consumption_id=launch_authority['consumption_id'],
                            reservation_id=launch_authority['reservation_id'])):
                    raise SupervisorError('VERIFICATION_LAUNCH_CAPABILITY_INVALID')

            launch_marker = {'schema_version': 2 if launch_authority else 1,
                             'execution_id': execution_id,
                             'execution_identity': identity, 'launch_intent_hash': intent_hash,
                             'launch_requested_at': time.time()}
            if launch_authority:
                launch_marker.update({
                    'launch_reservation_id': launch_authority['reservation_id'],
                    'launch_consumption_id': launch_authority['consumption_id']})
                launch_marker.update({key: launch_authority[key] for key in (
                    'plan_id', 'unit_id', 'obligation_ids', 'admission_id', 'admission_sha256',
                    'reservation_transition_id', 'consumption_transition_id',
                    'plan_acceptance_transition_id', 'lifecycle_generation') if key in launch_authority})
            publish_create_once(journal / 'launching.json', launch_marker, fault=self.store._fault)
            self._crash('during-launch')
            try:
                result = backend.launch(prepared, command=command, cwd=pathlib.Path(cwd),
                    timeout_seconds=timeout_seconds, environment=environment)
            except Exception as exc:
                raise SupervisorError(getattr(exc, 'reason_code', None) or str(exc) or
                                       'EXECUTION_LAUNCH_UNCERTAIN') from None
            self._crash('after-launch')

            observed = self._inspect(backend, identity)
            cancelled = bool(getattr(result, 'timed_out', False))
            if cancelled or observed.status not in {'DRAINED', 'NOT_LAUNCHED'}:
                self._write_state(journal, 'draining.json', {'schema_version': 1,
                    'execution_id': execution_id, 'state': 'DRAINING', 'at': time.time()})
                self._crash('after-draining')
                if observed.status in {'ACTIVE', 'NOT_DRAINED'} or cancelled:
                    self._write_state(journal, 'cancelling.json', {'schema_version': 1,
                        'execution_id': execution_id, 'state': 'CANCELLING', 'at': time.time()})
                    self._crash('after-cancel-started')
                    observed = backend.cancel_and_drain(identity, timeout_seconds)
                    cancelled = True
            if observed.status != 'DRAINED':
                raise SupervisorError('EXECUTION_RECONCILIATION_UNCERTAIN' if observed.status == 'UNCERTAIN'
                                      else 'EXECUTION_NOT_DRAINED')

            self._crash('after-drain-proof')
            post_observation = post_observer(result, ready) if post_observer else {}
            observation = {
                'schema_version': 2, 'execution_id': execution_id,
                'exit_code': getattr(result, 'exit_code', None),
                'timed_out': bool(getattr(result, 'timed_out', False)), 'cancelled': cancelled,
                'output_observation': 'CAPTURED', 'output_persistence': 'OMITTED',
                'started_at': getattr(result, 'started_at', started['started_at']),
                'ended_at': getattr(result, 'ended_at', time.time()), 'post_observation': post_observation,
                'result': self._outcome(result),
            }
            if post_observation.get('stable') is False and observation['result'] == 'PASS':
                observation['result'] = 'ERROR'
            publish_create_once(journal / 'observation.json', observation, fault=self.store._fault)
            self._crash('after-drained')
            terminal_material = (terminal_record_builder(result, ready, post_observation)
                                 if terminal_record_builder and observation['result'] == 'PASS' else None)
            receipt = {
                'schema_version': 2, 'execution_id': execution_id,
                'repository_id': self.store.repository_id, 'started_hash': started_hash,
                'execution_identity': identity, 'backend_identity': backend_identity,
                'candidate_identity': candidate_identity,
                'final_changed_surface_id': final_changed_surface_id,
                'policy_identity': policy_identity, 'command_identity': intent_hash,
                'retry_policy_proof': retry_policy_proof,
                'harness_invocation_upper_bound': 1,
                'exit_code': observation['exit_code'], 'timed_out': observation['timed_out'],
                'cancelled': cancelled, 'drainage': 'DRAINED',
                'output_observation': 'CAPTURED', 'output_persistence': 'OMITTED',
                'post_observation': post_observation, 'result': observation['result'],
                'ended_at': observation['ended_at'],
            }
            if launch_authority:
                receipt['launch_reservation_id'] = launch_authority['reservation_id']
                receipt['launch_consumption_id'] = launch_authority['consumption_id']
                receipt.update({key: launch_authority[key] for key in (
                    'plan_id', 'unit_id', 'obligation_ids', 'admission_id', 'admission_sha256',
                    'reservation_transition_id', 'consumption_transition_id',
                    'plan_acceptance_transition_id', 'lifecycle_generation') if key in launch_authority})
            if terminal_material is not None:
                receipt['verification_evidence'] = terminal_material
            self.store.publish_execution_terminal(receipt)
            self._reconcile_phase_d(receipt, started)
            self._crash('after-terminal')
            if terminal_publisher:
                terminal_publisher(result, ready, receipt)
            self._crash('after-evidence-projection')
            terminal = next(item for item in self.store.reconstruct_execution_terminals(rebuild=False)
                            if item['execution_id'] == execution_id)
            self.store.rebuild_terminal_evidence(terminal)
            self.store.publish_execution_projection(terminal)
            self._crash('after-index-projection')
            self._publish_drained(journal, started, terminal['receipt_hash'])
            return result, ready

    @staticmethod
    def _inspect(backend, identity):
        try:
            return backend.inspect(identity)
        except Exception:
            return type('Reconciliation', (), {'status': 'UNCERTAIN',
                'reason_code': 'EXECUTION_RECONCILIATION_UNCERTAIN', 'evidence': 'backend-inspection-failed'})()

    @staticmethod
    def _result_from_observation(observation: dict, started: dict):
        return type('RecoveredCommandResult', (), {
            'argv': (), 'cwd': started.get('worktree', ''),
            'started_at': observation.get('started_at', started.get('started_at', 0)),
            'ended_at': observation.get('ended_at', started.get('started_at', 0)),
            'duration_seconds': max(0.0, observation.get('ended_at', 0) - observation.get('started_at', 0)),
            'exit_code': observation.get('exit_code'), 'stdout': '', 'stderr': '',
            # CAPTURED records the execution-time observation; raw streams are
            # intentionally omitted, so a recovered result has no diagnostics.
            'output_persistence': observation.get('output_persistence', 'OMITTED'),
            'timed_out': observation.get('timed_out', False),
            'error': ('EXECUTION_ABORTED' if observation.get('result') == 'ABORTED' else
                      'recovered-output-unavailable' if observation.get('output_observation') != 'CAPTURED' else None),
            'sandbox_backend': started.get('backend'), 'strong_isolation': True,
            'protected_paths': True, 'descendant_containment': 'strong',
        })()

    def _reconciler(self, backend):
        def reconcile(record):
            identity = record.get('execution_identity')
            if not identity:
                return ReconciliationOutcome.UNCERTAIN
            result = self._inspect(backend, identity)
            return {'DRAINED': ReconciliationOutcome.PROVEN_DRAINED,
                    'ACTIVE': ReconciliationOutcome.STILL_ACTIVE,
                    'NOT_DRAINED': ReconciliationOutcome.STILL_ACTIVE,
                    'UNCERTAIN': ReconciliationOutcome.UNCERTAIN}.get(
                        result.status, ReconciliationOutcome.UNCERTAIN)
        return reconcile

    def recover(self, backend_factory=None, recovery_observer=None, *,
                prelaunch_recovery_authorizer=None, prelaunch_terminalizer=None) -> tuple[RecoveryResult, ...]:
        """Reconcile every unresolved STARTED record using fresh durable identities."""
        with repository_lock(self.store.root):
            return self._recover_locked(backend_factory, recovery_observer,
                prelaunch_recovery_authorizer=prelaunch_recovery_authorizer,
                prelaunch_terminalizer=prelaunch_terminalizer)

    def _recover_locked(self, backend_factory=None, recovery_observer=None, *,
                        prelaunch_recovery_authorizer=None, prelaunch_terminalizer=None
                        ) -> tuple[RecoveryResult, ...]:
        recovered = []
        for journal, started, closure in self.store._scan_executions():
            if closure is not None:
                continue
            execution_id = started['execution_id']
            if (started.get('predecessor_failure_id') is not None or
                    started.get('consumption_id') is not None or started.get('retry_proof') is not None):
                try:
                    self.store.validate_started_retry_proof(started)
                except StoreError:
                    recovered.append(RecoveryResult(execution_id, SupervisorState.UNCERTAIN,
                        'RETRY_PROOF_MISSING'))
                    continue
            terminal = self._read_json(journal / 'terminal.json')
            if terminal is not None:
                terminal = next(item for item in self.store.reconstruct_execution_terminals(rebuild=True)
                                if item['execution_id'] == execution_id)
                if (terminal.get('result') == 'ABORTED' and
                        terminal.get('harness_invocation_upper_bound') == 0 and
                        terminal.get('launch_consumption_id') is not None):
                    if prelaunch_terminalizer is None:
                        recovered.append(RecoveryResult(execution_id, SupervisorState.UNCERTAIN,
                            'SAFE_PRELAUNCH_ABORT_TERMINALIZATION_REQUIRED', terminal))
                        continue
                    try:
                        proof = safe_prelaunch_abort_proof(self, journal, execution_id, terminal,
                                                           reason='prepared-never-launched')
                        prelaunch_terminalizer(terminal, proof)
                    except Exception:
                        recovered.append(RecoveryResult(execution_id, SupervisorState.UNCERTAIN,
                            'SAFE_PRELAUNCH_ABORT_TERMINALIZATION_FAILED', terminal))
                        continue
                self._reconcile_phase_d(terminal, started)
                self.store.rebuild_terminal_evidence(terminal)
                self._publish_drained(journal, started, terminal['receipt_hash'],
                                      reason='recovered-terminal-closure')
                recovered.append(RecoveryResult(execution_id, SupervisorState.TERMINAL,
                                                 'TERMINAL_RECONSTRUCTED', terminal))
                continue
            # No launch-request marker proves the trusted supervisor never called
            # the Phase-B launch primitive. Persist an abort, never inferred success.
            launch_marker_state = self._launch_marker_state(journal, started)
            if launch_marker_state == 'invalid':
                recovered.append(RecoveryResult(execution_id, SupervisorState.UNCERTAIN,
                                                 'EXECUTION_LAUNCH_MARKER_INVALID'))
                continue
            if launch_marker_state == 'absent':
                authority_fields = None
                if started.get('plan_id') is not None and prelaunch_recovery_authorizer is None:
                    recovered.append(RecoveryResult(execution_id, SupervisorState.UNCERTAIN,
                        'SAFE_PRELAUNCH_ABORT_AUTHORITY_REQUIRED'))
                    continue
                if prelaunch_recovery_authorizer is not None:
                    try:
                        authority_fields = prelaunch_recovery_authorizer(started)
                        if authority_fields is not None:
                            authority_fields = _validated_safe_abort_authority_fields(
                                authority_fields, started)
                    except Exception:
                        recovered.append(RecoveryResult(execution_id, SupervisorState.UNCERTAIN,
                            'SAFE_PRELAUNCH_ABORT_AUTHORITY_UNAVAILABLE'))
                        continue
                recovered.append(self._abort_prepared(journal, started, 'prepared-never-launched',
                    authority_fields=authority_fields,
                    prelaunch_terminalizer=prelaunch_terminalizer))
                continue
            if backend_factory is None:
                recovered.append(RecoveryResult(execution_id, SupervisorState.UNCERTAIN,
                                                 'EXECUTION_RECOVERY_REQUIRED'))
                continue
            backend = backend_factory(started)
            identity = started['execution_identity']
            inspection = self._inspect(backend, identity)
            status = inspection.status
            if (status == 'DRAINED' and
                    getattr(inspection, 'reason_code', None) in
                    {'NOT_LAUNCHED', 'LAUNCH_FAILED_BEFORE_PAYLOAD'} and
                    self._read_json(journal / 'observation.json') is None):
                recovered.append(self._abort_prepared(journal, started, 'backend-proved-not-launched',
                    prelaunch_terminalizer=prelaunch_terminalizer))
                continue
            if (journal / 'cancelling.json').exists() and status in {'ACTIVE', 'NOT_DRAINED'}:
                status = backend.cancel_and_drain(identity, 5).status
            if status in {'ACTIVE', 'NOT_DRAINED'}:
                recovered.append(RecoveryResult(execution_id, SupervisorState.ACTIVE,
                                                 'EXECUTION_ALREADY_ACTIVE'))
                continue
            if status == 'UNCERTAIN':
                recovered.append(RecoveryResult(execution_id, SupervisorState.UNCERTAIN,
                                                 'EXECUTION_RECONCILIATION_UNCERTAIN'))
                continue
            observation = self._read_json(journal / 'observation.json')
            if observation is None:
                if (journal / 'cancelling.json').exists() and status == 'DRAINED':
                    # Cancellation interrupted before observation. Record that
                    # output and post-input evidence are unavailable; do not
                    # substitute an empty-output hash for unknown data.
                    if recovery_observer is None:
                        recovered.append(RecoveryResult(execution_id, SupervisorState.UNCERTAIN,
                            'POST_EXECUTION_OBSERVATION_UNAVAILABLE'))
                        continue
                    try:
                        post_observation = recovery_observer(started)
                    except Exception:
                        post_observation = None
                    if not isinstance(post_observation, dict):
                        recovered.append(RecoveryResult(execution_id, SupervisorState.UNCERTAIN,
                            'POST_EXECUTION_OBSERVATION_UNAVAILABLE'))
                        continue
                    observation = {'schema_version': 2, 'exit_code': None,
                        'timed_out': False, 'cancelled': True,
                        'output_observation': 'UNAVAILABLE', 'output_persistence': 'UNAVAILABLE',
                        'post_observation': post_observation,
                        'result': 'ABORTED', 'ended_at': time.time()}
                else:
                    recovered.append(RecoveryResult(execution_id, SupervisorState.UNCERTAIN,
                                                     'TERMINAL_OBSERVATION_MISSING'))
                    continue
            receipt = self._terminal_from_observation(journal, started, identity, observation)
            self.store.publish_execution_terminal(receipt)
            self._reconcile_phase_d(receipt, started)
            terminal = next(item for item in self.store.reconstruct_execution_terminals(rebuild=True)
                            if item['execution_id'] == execution_id)
            self._publish_drained(journal, started, terminal['receipt_hash'],
                                  reason='recovered-proven-drainage')
            recovered.append(RecoveryResult(execution_id, SupervisorState.TERMINAL,
                                             'TERMINAL_RECONSTRUCTED', terminal))
        return tuple(recovered)

    def _reconcile_phase_d(self, terminal: dict, started: dict) -> None:
        """Rebuild critical-failure authority from immutable execution truth."""
        if (terminal.get('result') == 'FAIL' and not terminal.get('timed_out') and
                not terminal.get('cancelled') and started.get('critical') is True):
            self.store.publish_critical_failure(terminal['execution_id'],
                predecessor_failure_id=started.get('predecessor_failure_id'),
                consumption_id=started.get('consumption_id'))
        retry = started.get('retry_proof')
        if terminal.get('result') == 'PASS' and isinstance(retry, dict):
            self.store.publish_failure_resolution(retry['failure_id'], terminal['execution_id'], result='PASS')

    def _abort_prepared(self, journal: pathlib.Path, started: dict, reason: str, *,
                        authority_fields: dict | None = None,
                        prelaunch_terminalizer=None) -> RecoveryResult:
        execution_id = started['execution_id']
        observation = {'schema_version': 2, 'exit_code': None, 'timed_out': False, 'cancelled': False,
            'output_observation': 'UNAVAILABLE', 'output_persistence': 'UNAVAILABLE',
            'post_observation': {},
            'result': 'ABORTED', 'ended_at': time.time()}
        receipt = self._terminal_from_observation(journal, started,
            started.get('execution_identity'), observation)
        if authority_fields is not None:
            authority_record = {'schema_version': 1, 'execution_id': execution_id,
                                **authority_fields}
            publish_create_once(journal / 'launch-authority.json', authority_record,
                                fault=self.store._fault)
            receipt.update(authority_fields)
        self.store.publish_execution_terminal(receipt)
        terminal = next(item for item in self.store.reconstruct_execution_terminals(rebuild=True)
                        if item['execution_id'] == execution_id)
        if authority_fields is not None:
            if prelaunch_terminalizer is None:
                return RecoveryResult(execution_id, SupervisorState.UNCERTAIN,
                    'SAFE_PRELAUNCH_ABORT_TERMINALIZATION_REQUIRED', terminal)
            try:
                proof = safe_prelaunch_abort_proof(self, journal, execution_id, terminal, reason=reason)
                prelaunch_terminalizer(terminal, proof)
            except Exception:
                return RecoveryResult(execution_id, SupervisorState.UNCERTAIN,
                    'SAFE_PRELAUNCH_ABORT_TERMINALIZATION_FAILED', terminal)
        self._publish_drained(journal, started, terminal['receipt_hash'], reason=reason)
        return RecoveryResult(execution_id, SupervisorState.ABORTED_PREPARED,
                              'PREPARED_NOT_LAUNCHED', terminal)

    def _terminal_from_observation(self, journal, started, identity, observation):
        terminal = {'schema_version': 2, 'execution_id': started['execution_id'],
            'repository_id': self.store.repository_id,
            'started_hash': hashlib.sha256((journal / 'started.json').read_bytes()).hexdigest(),
            'execution_identity': identity or {'state': 'NOT_PREPARED'},
            'candidate_identity': started.get('candidate_identity'),
            'final_changed_surface_id': started.get('final_changed_surface_id'),
            'backend_identity': started.get('backend_identity', started['backend']),
            'policy_identity': started.get('policy_identity', 'unqualified'),
            'command_identity': started['launch_intent_hash'],
            'retry_policy_proof': started.get('retry_policy_proof'),
            'harness_invocation_upper_bound': 1 if (journal / 'launching.json').exists() else 0,
            'exit_code': observation.get('exit_code'), 'timed_out': observation.get('timed_out', False),
            'cancelled': observation.get('cancelled', False), 'drainage': 'DRAINED',
            'output_observation': observation.get('output_observation', 'CAPTURED'),
            'output_persistence': ('UNAVAILABLE' if observation.get('output_observation') == 'UNAVAILABLE'
                                   else 'OMITTED'),
            'post_observation': observation.get('post_observation', {}),
            'result': observation.get('result', 'ABORTED'), 'ended_at': observation.get('ended_at', time.time())}
        launch_marker = self._read_json(journal / 'launching.json')
        if isinstance(launch_marker, dict):
            for marker_key, terminal_key in ((key, key) for key in (
                    'launch_reservation_id', 'launch_consumption_id', 'plan_id', 'unit_id',
                    'obligation_ids', 'admission_id', 'admission_sha256',
                    'reservation_transition_id', 'consumption_transition_id',
                    'plan_acceptance_transition_id', 'lifecycle_generation')):
                if marker_key in launch_marker:
                    terminal[terminal_key] = launch_marker[marker_key]
        return terminal
