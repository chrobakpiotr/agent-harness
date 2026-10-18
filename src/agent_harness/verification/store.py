"""Canonical verification control-store resolution and immutable publication."""
from __future__ import annotations

import contextlib
import enum
import errno
import hashlib
import json
import os
import pathlib
import re
import subprocess
import stat
import tempfile
import threading
import time
import uuid

from agent_harness.repository import RepositoryError, resolve_control_root as _resolve_control_root
from agent_harness.repository import resolve_primary_worktree as _resolve_primary_worktree
try:
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # pragma: no cover - exercised on POSIX
    msvcrt = None

from .model import Evidence
from .retry import registry as _retry_registry
from .retry import retry_policy_facts
from .serialization import canonical, evidence_record


class StoreError(RuntimeError):
    """Stable control-store failure category."""


def _validate_component(value: str | None, error: str) -> None:
    if not isinstance(value, str) or not _SAFE_COMPONENT.fullmatch(value) or value in {'.', '..'}:
        raise StoreError(error)


def candidate_failure_fingerprint(record: dict) -> str | None:
    """Non-reusable failures use exact candidate authority, never null cache identity."""
    keys = ('repository_id', 'profile_hash', 'gate_id', 'policy_checkpoint',
            'final_changed_surface_id', 'launch_intent_hash')
    if record.get('input_fingerprint') is not None:
        return None
    if any(not isinstance(record.get(key), str) or not record[key] for key in keys):
        return None
    if any(not re.fullmatch(r'[0-9a-f]{64}', record[key]) for key in
           ('profile_hash', 'final_changed_surface_id', 'launch_intent_hash')):
        return None
    if not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', record['policy_checkpoint']):
        return None
    return hashlib.sha256(canonical({'protocol': 'candidate-failure-scope-v1',
                                    **{key: record[key] for key in keys}})).hexdigest()


def failure_fingerprint(record: dict):
    return record.get('failure_fingerprint', record.get('input_fingerprint'))


def _legacy_retry_policy_facts(policy, critical, controls):
    """Read-only parser for retry proofs emitted before controls were bound to commands."""
    if not isinstance(policy, str) or policy not in {'forbid', 'allow'} or type(critical) is not bool or not isinstance(controls, list):
        return None
    if any(not isinstance(item, str) or not item for item in controls) or len(set(controls)) != len(controls):
        return None
    established = (controls == ['no-internal-retries'] or bool(controls) and all(
        (item.startswith('internal-retry-disabled:') and bool(item.split(':', 1)[1])) or
        (len(item.split(':')) == 3 and item.split(':')[0] == 'internal-retry-bounded' and
         bool(item.split(':')[1]) and item.split(':')[2].isdigit() and int(item.split(':')[2]) > 0)
        for item in controls))
    return {'schema_version': 1, 'policy': policy, 'critical': critical,
            'retry_controls': controls,
            'retry_free': bool(critical and policy == 'forbid' and established)}


class ReconciliationOutcome(enum.Enum):
    """Backend assertion about the recorded execution containment scope."""

    PROVEN_DRAINED = 'proven-drained'
    STILL_ACTIVE = 'still-active'
    UNCERTAIN = 'uncertain'


# Authority classification is explicit: these records determine restart truth.
AUTHORITATIVE_IMMUTABLE = frozenset({
    'executions/*/started.json', 'executions/*/admitted.json', 'executions/*/drained.json',
    'runs/*/*/terminal.json', 'failures/*.json', 'failure-resolutions/*.json',
    'grants/*.json', 'consumptions/*.json',
})
DERIVED_REBUILDABLE = frozenset({'state/**', 'runs/*/summary.json', 'runs/*/*/projection.json'})
_SAFE_COMPONENT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z')


def resolve_primary_worktree(repository: str | pathlib.Path) -> pathlib.Path:
    """Resolve the one primary checkout that owns repository-wide authority."""
    try:
        return _resolve_primary_worktree(repository)
    except RepositoryError as error:
        raise StoreError(str(error)) from None


def resolve_control_root(repository: str | pathlib.Path) -> tuple[pathlib.Path, str]:
    """Resolve primary-worktree control root; never fall back to caller checkout."""
    try:
        return _resolve_control_root(repository)
    except RepositoryError as error:
        raise StoreError(str(error)) from None


@contextlib.contextmanager
def repository_lock(control_root: pathlib.Path, timeout: float = 30.0):
    """Kernel-owned exclusive lock released automatically when a process dies."""
    lock = control_root / 'lock'
    _assert_contained(lock, control_root, allow_equal=False)
    lock.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(lock, flags, 0o600)
    except OSError:
        raise StoreError('unsafe-authority-path') from None
    acquired = False
    held = getattr(_repository_lock_context, 'held', None)
    if held is None:
        held = set()
        _repository_lock_context.held = held
    lock_key = os.fspath(pathlib.Path(control_root).resolve())
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise StoreError('unsafe-authority-path')
    if fcntl is None and msvcrt is None:
        os.close(fd)
        raise StoreError('repository-lock-unsupported')
    if msvcrt is not None and os.fstat(fd).st_size == 0:
        os.write(fd, b'\0')
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                acquired = True
                held.add(lock_key)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise StoreError('repository-lock-failed') from None
                if time.monotonic() >= deadline:
                    raise StoreError('busy')
                time.sleep(0.05)
        yield
    finally:
        try:
            if acquired:
                held.discard(lock_key)
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                else:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)


_repository_lock_context = threading.local()


def repository_lock_held(control_root: pathlib.Path) -> bool:
    """Whether this thread owns the repository lock for this control root."""
    held = getattr(_repository_lock_context, 'held', ())
    return os.fspath(pathlib.Path(control_root).resolve()) in held


def _fsync_directory(path: pathlib.Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_replace_projection(path: pathlib.Path, record: dict, *, fault=None) -> None:
    """Replace rebuildable projection via fsynced same-directory temp file."""
    payload = json.dumps(record, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False).encode() + b'\n'
    _assert_contained(path, path.parent.parent if path.parent.name == 'state' else path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    temp_path = pathlib.Path(temp_name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            if fault:
                fault('projection-before-write')
            stream.write(payload)
            stream.flush()
            if fault:
                fault('projection-before-fsync')
            os.fsync(stream.fileno())
        if fault:
            fault('projection-before-replace')
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def _assert_contained(path: pathlib.Path, root: pathlib.Path, *, allow_equal: bool = False) -> None:
    """Reject symlinks and prove resolved artifact remains beneath root."""
    root = pathlib.Path(root).resolve()
    path = pathlib.Path(os.path.abspath(path))
    probe = path
    while probe != probe.parent:
        if probe.is_symlink():
            raise StoreError('unsafe-authority-path')
        probe = probe.parent
    try:
        relative = path.resolve(strict=False).relative_to(root)
        if not allow_equal and not relative.parts:
            raise ValueError()
    except (OSError, ValueError, RuntimeError):
        raise StoreError('unsafe-authority-path') from None


def publish_create_once(path: pathlib.Path, record: dict, *, fault=None) -> str:
    """Atomically and durably create immutable JSON; identical replay is safe."""
    payload = json.dumps(record, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False).encode() + b'\n'
    path = pathlib.Path(path)
    # Check existing ancestors before mkdir: validating only afterwards can
    # create directories outside the control root through a symlinked parent.
    _assert_contained(path, path.parent, allow_equal=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_contained(path, path.parent, allow_equal=False)
    temp_path = path.parent / f'.{path.name}.{uuid.uuid4().hex}.tmp'
    fd = None
    try:
        if fault:
            fault('before-publication')
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            fd = None
            if fault:
                fault('during-write')
            stream.write(payload)
            stream.flush()
            if fault:
                fault('before-file-fsync')
            os.fsync(stream.fileno())
        if fault:
            fault('before-atomic-publication')
        try:
            # Hard-link publication is atomic and cannot replace an authority.
            os.link(temp_path, path)
        except FileExistsError:
            try:
                existing = path.read_bytes()
            except OSError:
                raise StoreError('immutable-record-unreadable') from None
            if existing != payload:
                raise StoreError('immutable-record-collision')
        if fault:
            fault('before-directory-fsync')
        _fsync_directory(path.parent)
    except StoreError:
        raise
    except OSError:
        if path.exists():
            # The atomic name may be visible, but directory durability is
            # uncertain. Preserve the bytes and make readers fail closed.
            marker = path.with_name(path.name + '.publication-uncertain')
            try:
                marker.write_text('uncertain\n', encoding='ascii')
                _fsync_directory(path.parent)
            except OSError:
                pass
        raise StoreError('immutable-record-publication-failed') from None
    finally:
        if fd is not None:
            os.close(fd)
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # A leftover private temp is not authority and is never scanned.
            pass
    return hashlib.sha256(payload).hexdigest()


class VerificationStore:
    def __init__(self, repository: str | pathlib.Path, *, control_root: pathlib.Path | None = None,
                 issuer_registry: pathlib.Path | None = None, profile_root: pathlib.Path | None = None,
                 issuer_registry_path: str | None = None, executable_binder=None, retry_controls=()):
        # An explicit control root is only a test seam; identity always follows
        # the canonical Git common-dir model used by the repository harness.
        production_resolution = control_root is None
        if production_resolution and issuer_registry is not None:
            raise StoreError('issuer-registry-override-forbidden')
        if production_resolution:
            control_root, repository_id = resolve_control_root(repository)
        else:
            # Explicit roots are used by isolated state-machine tests only.
            # Production entry points omit this argument and require Git identity.
            repository_id = hashlib.sha256(str(pathlib.Path(repository).resolve()).encode()).hexdigest()
        self.root = pathlib.Path(control_root).resolve()
        if issuer_registry is not None:
            # Explicit registry: only with an explicit (test) control root, see the guard above.
            self.issuer_registry = pathlib.Path(issuer_registry)
        elif production_resolution and issuer_registry_path is not None:
            # Consumer policy names where the registry lives; the trust root is the primary checkout
            # that resolve_control_root() selected, never the caller's worktree or this package. [seam]
            relative = pathlib.PurePosixPath(issuer_registry_path)
            if relative.is_absolute() or not relative.parts or '..' in relative.parts:
                raise StoreError('issuer-registry-path-invalid')
            self.issuer_registry = self.root.parents[2].joinpath(*relative.parts)
        else:
            # No built-in registry: without one every grant fails FAILURE_GRANT_ISSUER_UNAVAILABLE.
            self.issuer_registry = None
        self.profile_root = profile_root
        # Consumer command conventions (e.g. `./gradlew`); None means agent_harness.executables' default.
        self.executable_binder = executable_binder
        # Consumer-registered retry controls [seam]; empty means only the literal no-op control holds.
        self.retry_controls = _retry_registry(retry_controls)
        self.repository_id = repository_id
        self.executions = self.root / 'executions'
        self.runs = self.root / 'runs'
        self.failures = self.root / 'failures'
        self.resolutions = self.root / 'failure-resolutions'
        self.grants = self.root / 'grants'
        self.consumptions = self.root / 'consumptions'
        self.plans = self.root / 'plans'
        # These immutable records are subordinate to the lifecycle CAS in
        # .agent-state. Presence in this store never grants launch authority.
        self.admissions = self.root / 'admissions'
        self.launch_consumptions = self.root / 'launch-consumptions'
        self._fault_injector = None

    def _fault(self, boundary: str) -> None:
        if self._fault_injector:
            self._fault_injector(boundary)

    def publish_plan_record(self, record: dict) -> dict:
        """Publish a content-addressed immutable plan; this is subordinate only."""
        from .authority import validate_plan_record
        validate_plan_record(record, profile_root=self.profile_root, executable_binder=self.executable_binder)
        path = self.plans / (hashlib.sha256(record['plan_id'].encode()).hexdigest() + '.json')
        _assert_contained(path, self.root)
        publish_create_once(path, record, fault=self._fault)
        return record

    def load_plan_record(self, plan_id: str) -> dict:
        if not isinstance(plan_id, str) or not plan_id.startswith('verification-plan-v2:sha256:'):
            raise StoreError('VERIFICATION_EXECUTION_PLAN_REQUIRED')
        path = self.plans / (hashlib.sha256(plan_id.encode()).hexdigest() + '.json')
        _assert_contained(path, self.root)
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise StoreError('ACCEPTED_PLAN_UNAVAILABLE') from None
        from .authority import validate_plan_record
        validate_plan_record(record, profile_root=self.profile_root, executable_binder=self.executable_binder)
        if record.get('plan_id') != plan_id:
            raise StoreError('ACCEPTED_PLAN_UNAVAILABLE')
        return record

    def publish_admission_record(self, record: dict) -> tuple[dict, str]:
        """Publish immutable admission data; .agent-state alone grants authority."""
        from .authority import validate_plan_admission_record_shape
        validate_plan_admission_record_shape(record)
        path = self.admissions / (hashlib.sha256(record['admission_id'].encode()).hexdigest() + '.json')
        _assert_contained(path, self.root)
        # create-once publication is already atomic and fsynced. Avoid taking
        # the repository admission lock here: lifecycle callers publish the
        # candidate while holding that lock, and .agent-state alone grants it.
        digest = publish_create_once(path, record, fault=self._fault)
        return record, digest

    def load_admission_record(self, admission_id: str, *, expected_hash: str | None = None) -> dict:
        if (not isinstance(admission_id, str) or
                not admission_id.startswith('verification-admission-v1:sha256:')):
            raise StoreError('ADMISSION_UNAVAILABLE')
        path = self.admissions / (hashlib.sha256(admission_id.encode()).hexdigest() + '.json')
        _assert_contained(path, self.root)
        try:
            payload = path.read_bytes()
            record = json.loads(payload.decode('utf-8'))
        except (OSError, UnicodeError, ValueError):
            raise StoreError('ADMISSION_UNAVAILABLE') from None
        from .authority import validate_plan_admission_record_shape
        validate_plan_admission_record_shape(record)
        if (record.get('admission_id') != admission_id or
                (expected_hash is not None and hashlib.sha256(payload).hexdigest() != expected_hash)):
            raise StoreError('ADMISSION_UNAVAILABLE')
        return record

    def publish_launch_consumption_record(self, record: dict) -> tuple[dict, str]:
        """Store a consumption candidate; authority is granted only by state CAS."""
        if not isinstance(record, dict):
            raise StoreError('invalid-launch-consumption')
        consumption_id = record.get('consumption_id')
        body = {key: value for key, value in record.items() if key != 'consumption_id'}
        expected = 'launch-consumption-v1:sha256:' + hashlib.sha256(canonical(body)).hexdigest()
        if consumption_id != expected:
            raise StoreError('invalid-launch-consumption')
        path = self.launch_consumptions / (
            hashlib.sha256(consumption_id.encode()).hexdigest() + '.json')
        _assert_contained(path, self.root)
        digest = publish_create_once(path, record, fault=self._fault)
        return record, digest

    def load_launch_consumption_record(self, consumption_id: str, *, admission: dict,
                                       reservation: dict, expected_hash: str | None = None) -> dict:
        if (not isinstance(consumption_id, str) or
                not consumption_id.startswith('launch-consumption-v1:sha256:')):
            raise StoreError('invalid-launch-consumption')
        path = self.launch_consumptions / (
            hashlib.sha256(consumption_id.encode()).hexdigest() + '.json')
        _assert_contained(path, self.root)
        try:
            payload = path.read_bytes()
            record = json.loads(payload.decode('utf-8'))
        except (OSError, UnicodeError, ValueError):
            raise StoreError('invalid-launch-consumption') from None
        if (record.get('consumption_id') != consumption_id or
                (expected_hash is not None and hashlib.sha256(payload).hexdigest() != expected_hash)):
            raise StoreError('invalid-launch-consumption')
        from .authority import validate_launch_consumption_record
        validate_launch_consumption_record(record, admission, reservation)
        return record

    def _validated_started(self, directory: pathlib.Path, *, allow_legacy_retry: bool = False) -> dict:
        if not directory.is_dir() or directory.is_symlink() or not _SAFE_COMPONENT.fullmatch(directory.name):
            raise StoreError('invalid-execution-history')
        started = directory / 'started.json'
        try:
            if not started.is_file() or started.is_symlink():
                raise StoreError('invalid-execution-history')
            record = json.loads(started.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            raise StoreError('invalid-execution-history') from None
        required = {'schema_version', 'execution_id', 'repository_id', 'backend', 'worktree',
                    'launch_intent_hash', 'started_at', 'family_id', 'attempt_id', 'gate_id'}
        if (not isinstance(record, dict) or not required <= set(record) or record.get('schema_version') != 2 or
                record.get('execution_id') != directory.name or record.get('repository_id') != self.repository_id or
                not all(isinstance(record.get(k), str) and record[k] for k in
                        ('backend', 'worktree', 'family_id', 'attempt_id', 'gate_id')) or
                not isinstance(record.get('launch_intent_hash'), str) or
                not re.fullmatch(r'[0-9a-f]{64}', record['launch_intent_hash']) or
                type(record.get('started_at')) not in (float, int)):
            raise StoreError('invalid-execution-history')
        if 'failure_fingerprint' in record:
            expected_scope = candidate_failure_fingerprint(record)
            if expected_scope is None or record['failure_fingerprint'] != expected_scope:
                raise StoreError('invalid-execution-failure-scope')
        if 'retry_policy_proof' in record:
            expected_retry = retry_policy_facts(record.get('retry_policy'), record.get('critical'),
                record.get('retry_controls'), registry=self.retry_controls, gate_id=record.get('gate_id'),
                command_hash=record.get('launch_intent_hash'),
                control_evidence=(record.get('retry_policy_proof', {}).get('control_evidence')
                                  if isinstance(record.get('retry_policy_proof'), dict) else None))
            legacy_retry = _legacy_retry_policy_facts(record.get('retry_policy'), record.get('critical'),
                                                       record.get('retry_controls'))
            current_retry_valid = expected_retry is not None and record.get('retry_policy_proof') == expected_retry
            legacy_retry_valid = (allow_legacy_retry and legacy_retry is not None and
                                  record.get('retry_policy_proof') == legacy_retry)
            if not current_retry_valid and not legacy_retry_valid:
                raise StoreError('retry-policy-violation')
        elif record.get('critical') is True and record.get('retry_policy') == 'forbid':
            raise StoreError('retry-policy-violation')
        return record

    def _validate_admission(self, directory: pathlib.Path, started: dict) -> None:
        admitted_path = directory / 'admitted.json'
        if not admitted_path.exists():
            return  # Historical journals from the earlier protocol are readable.
        _assert_contained(admitted_path, self.root)
        try:
            admitted = json.loads(admitted_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise StoreError('invalid-execution-history') from None
        required = {'schema_version', 'execution_id', 'repository_id', 'status', 'admitted_at'}
        if (not isinstance(admitted, dict) or not required <= set(admitted) or
                admitted.get('schema_version') != 2 or admitted.get('execution_id') != started['execution_id'] or
                admitted.get('repository_id') != self.repository_id or admitted.get('status') != 'admitted' or
                type(admitted.get('admitted_at')) not in (int, float)):
            raise StoreError('invalid-execution-history')

    def _scan_executions(self) -> list[tuple[pathlib.Path, dict, dict | None]]:
        executions = []
        if not self.executions.exists():
            return executions
        _assert_contained(self.executions, self.root)
        if self.executions.is_symlink() or not self.executions.is_dir():
            raise StoreError('invalid-execution-history')
        try:
            directories = sorted(self.executions.iterdir())
        except OSError:
            raise StoreError('invalid-execution-history') from None
        for directory in directories:
            if not _SAFE_COMPONENT.fullmatch(directory.name):
                raise StoreError('invalid-execution-history')
            if any((directory / name).exists() for name in
                   ('started.json.publication-uncertain', 'drained.json.publication-uncertain',
                    'admitted.json.publication-uncertain')):
                raise StoreError('invalid-execution-history')
            if not (directory / 'started.json').exists():
                # STARTED is the payload-launch barrier. A directory containing
                # only private STARTED publication temporaries (or nothing) is
                # a crash before authority became visible and therefore cannot
                # represent an execution. Any other content is ambiguous.
                if directory.is_symlink() or not directory.is_dir():
                    raise StoreError('invalid-execution-history')
                _assert_contained(directory, self.root)
                try:
                    contents = list(directory.iterdir())
                except OSError:
                    raise StoreError('invalid-execution-history') from None
                if any(item.is_symlink() or not item.is_file() or
                            not re.fullmatch(r'\.started\.json\.[0-9a-f]{32}\.tmp', item.name)
                            for item in contents):
                    raise StoreError('invalid-execution-history')
                continue
            started = self._validated_started(directory, allow_legacy_retry=True)
            _assert_contained(directory, self.root)
            self._validate_admission(directory, started)
            drained_path = directory / 'drained.json'
            closure = None
            if drained_path.exists() or drained_path.is_symlink():
                _assert_contained(drained_path, self.root)
                try:
                    if not drained_path.is_file() or drained_path.is_symlink():
                        raise ValueError()
                    closure = json.loads(drained_path.read_text(encoding='utf-8'))
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    raise StoreError('invalid-execution-history') from None
                required = {'schema_version', 'execution_id', 'repository_id', 'backend', 'status', 'ended_at'}
                if (not isinstance(closure, dict) or not required <= set(closure) or closure.get('schema_version') != 2 or
                        closure.get('execution_id') != directory.name or closure.get('repository_id') != self.repository_id or
                        closure.get('backend') != started['backend'] or closure.get('status') != 'drained' or
                        type(closure.get('ended_at')) not in (int, float)):
                    raise StoreError('invalid-execution-history')
                if started.get('lifecycle_protocol') == 1:
                    terminal = self._read_execution_terminal(directory, started)
                    if (terminal is None or
                            closure.get('terminal_receipt_hash') != terminal.get('receipt_hash')):
                        raise StoreError('invalid-execution-history')
            executions.append((directory, started, closure))
        return executions

    def admit_repository_verification(self, reconciler=None, *, allow_execution_id: str | None = None) -> None:
        executions = self._scan_executions()
        for directory, started, closure in executions:
            if closure is not None:
                continue
            # A lifecycle CAS may advance only the exact execution whose
            # immutable launch authority it is validating. All other active
            # executions remain repository-wide barriers.
            if allow_execution_id is not None and started.get('execution_id') == allow_execution_id:
                continue
            try:
                outcome = reconciler(started) if reconciler is not None else ReconciliationOutcome.UNCERTAIN
            except Exception:
                outcome = ReconciliationOutcome.UNCERTAIN
            if outcome == ReconciliationOutcome.PROVEN_DRAINED:
                if started.get('lifecycle_protocol') == 1:
                    # Phase-C execution journals are not closable from liveness
                    # alone: immutable terminal truth and its rebuildable
                    # projections must precede repository admission release.
                    terminal = self._read_execution_terminal(directory, started)
                    if terminal is None:
                        raise StoreError('verification-owned')
                    self.reconstruct_execution_terminals()
                record = {'schema_version': 2, 'execution_id': started['execution_id'],
                          'repository_id': self.repository_id, 'backend': started['backend'],
                          'status': 'drained', 'reason': 'reconciled-proven-drained',
                          **({'terminal_receipt_hash': terminal['receipt_hash']}
                             if started.get('lifecycle_protocol') == 1 else {}),
                          'ended_at': time.time()}
                publish_create_once(directory / 'drained.json', record, fault=self._fault)
            elif outcome in (ReconciliationOutcome.STILL_ACTIVE, ReconciliationOutcome.UNCERTAIN):
                raise StoreError('verification-owned')
            else:
                raise StoreError('invalid-reconciliation-outcome')
        if any(closure is None and started.get('execution_id') != allow_execution_id
               for _, started, closure in self._scan_executions()):
            raise StoreError('verification-owned')

    def _read_execution_terminal(self, directory: pathlib.Path, started: dict) -> dict | None:
        path = directory / 'terminal.json'
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise StoreError('invalid-execution-terminal') from None
        supplied = record.pop('receipt_hash', None)
        from .serialization import canonical
        actual = hashlib.sha256(canonical(record)).hexdigest()
        record['receipt_hash'] = supplied
        if (supplied != actual or record.get('execution_id') != started['execution_id'] or
                record.get('repository_id') != self.repository_id or record.get('drainage') != 'DRAINED' or
                record.get('candidate_identity') != started.get('candidate_identity') or
                record.get('final_changed_surface_id') != started.get('final_changed_surface_id')):
            raise StoreError('invalid-execution-terminal')
        self._validate_terminal_authority_binding(directory, record)
        if (started.get('predecessor_failure_id') is not None or
                started.get('consumption_id') is not None or started.get('retry_proof') is not None):
            self.validate_started_retry_proof(started)
        return record

    @staticmethod
    def _validate_terminal_authority_binding(directory: pathlib.Path, record: dict) -> None:
        keys = {
            'launch_reservation_id', 'launch_consumption_id', 'plan_id', 'unit_id',
            'obligation_ids', 'admission_id', 'admission_sha256',
            'reservation_transition_id', 'consumption_transition_id',
            'plan_acceptance_transition_id', 'lifecycle_generation',
        }
        if not any(key in record for key in keys):
            return
        try:
            marker_path = directory / 'launching.json'
            marker_metadata = marker_path.lstat()
        except FileNotFoundError:
            marker_metadata = None
        except OSError:
            raise StoreError('invalid-execution-terminal') from None
        if (not keys <= set(record) or
                not isinstance(record.get('obligation_ids'), list) or
                not record['obligation_ids'] or type(record.get('lifecycle_generation')) is not int or
                record['lifecycle_generation'] < 1 or
                not all(isinstance(record.get(key), str) and record[key] for key in
                        keys - {'obligation_ids', 'lifecycle_generation'})):
            raise StoreError('invalid-execution-terminal')
        if marker_metadata is not None:
            if not stat.S_ISREG(marker_metadata.st_mode) or stat.S_ISLNK(marker_metadata.st_mode):
                raise StoreError('invalid-execution-terminal')
            try:
                marker = json.loads(marker_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                raise StoreError('invalid-execution-terminal') from None
            if any(record.get(key) != marker.get(key) for key in keys):
                raise StoreError('invalid-execution-terminal')
            return

        # Recovery may safely terminalize a consumed authorization only when
        # no launch marker exists and the durable create-once authority record
        # proves which reservation it is aborting. This is deliberately limited
        # to a prelaunch abort; all other terminals require launching.json.
        if (record.get('result') != 'ABORTED' or
                record.get('harness_invocation_upper_bound') != 0 or
                record.get('timed_out') is not False or record.get('cancelled') is not False or
                record.get('output_observation') != 'UNAVAILABLE'):
            raise StoreError('invalid-execution-terminal')
        authority_path = directory / 'launch-authority.json'
        try:
            authority_metadata = authority_path.lstat()
            if not stat.S_ISREG(authority_metadata.st_mode) or stat.S_ISLNK(authority_metadata.st_mode):
                raise StoreError('invalid-execution-terminal')
            authority = json.loads(authority_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise StoreError('invalid-execution-terminal') from None
        expected = {'schema_version': 1, 'execution_id': record.get('execution_id'),
                    **{key: record.get(key) for key in keys}}
        if (authority != expected or record.get('execution_id') != authority.get('execution_id') or
                authority.get('schema_version') != 1):
            raise StoreError('invalid-execution-terminal')

    def admit_and_reserve(self, execution_id: str, record: dict, reconciler=None) -> pathlib.Path:
        """Serialize validation, reconciliation, rescan, and immutable admission."""
        _validate_component(execution_id, 'invalid-execution-id')
        if record.get('execution_id') != execution_id or record.get('repository_id') != self.repository_id:
            raise StoreError('invalid-start-record')
        if record.get('schema_version') != 2:
            raise StoreError('invalid-start-record')
        if 'retry_policy_proof' in record:
            expected_retry = retry_policy_facts(record.get('retry_policy'), record.get('critical'),
                record.get('retry_controls'), registry=self.retry_controls, gate_id=record.get('gate_id'),
                command_hash=record.get('launch_intent_hash'),
                control_evidence=(record.get('retry_policy_proof', {}).get('control_evidence')
                                  if isinstance(record.get('retry_policy_proof'), dict) else None))
            if expected_retry is None or record.get('retry_policy_proof') != expected_retry:
                raise StoreError('retry-policy-violation')
        elif record.get('critical') is True and record.get('retry_policy') == 'forbid':
            raise StoreError('retry-policy-violation')
        journal = self.executions / execution_id
        _assert_contained(journal, self.root)
        with repository_lock(self.root):
            self.admit_repository_verification(reconciler)
            if journal.exists():
                if journal.is_symlink() or not journal.is_dir():
                    raise StoreError('invalid-execution-history')
                started_path = journal / 'started.json'
                contents = list(journal.iterdir())
                for item in contents:
                    if item == started_path:
                        continue
                    if (not re.fullmatch(r'\.started\.json\.[0-9a-f]{32}\.tmp', item.name) or
                            item.is_symlink() or not item.is_file()):
                        raise StoreError('invalid-execution-history')
                if started_path.exists():
                    existing = self._validated_started(journal)
                    if existing != record:
                        raise StoreError('immutable-record-collision')
                    for item in contents:
                        if item != started_path:
                            item.unlink()
                    _fsync_directory(journal)
                    return journal
                # A crash may leave the directory or a private create-once
                # temporary before started.json became the launch barrier.
                for item in contents:
                    item.unlink()
                _fsync_directory(journal)
            self._fault('before-admission-publication')
            journal.mkdir(parents=True, exist_ok=True)
            # The start journal is the atomic reservation and admission fact.
            # A second record would create a crash gap between reservation
            # and start, so no separately authoritative admitted marker exists.
            publish_create_once(journal / 'started.json', record, fault=self._fault)
            _fsync_directory(self.executions)
        return journal

    def _scan_terminals(self) -> list[dict]:
        records = []
        if not self.runs.exists():
            return records
        _assert_contained(self.runs, self.root)
        for family_dir in sorted(self.runs.iterdir()):
            _validate_component(family_dir.name, 'invalid-terminal-identity')
            _assert_contained(family_dir, self.root)
            if not family_dir.is_dir() or family_dir.is_symlink():
                raise StoreError('invalid-terminal-identity')
            for evidence_dir in sorted(family_dir.iterdir()):
                _validate_component(evidence_dir.name, 'invalid-terminal-identity')
                _assert_contained(evidence_dir, self.root)
                if not evidence_dir.is_dir() or evidence_dir.is_symlink():
                    raise StoreError('invalid-terminal-identity')
                terminal = evidence_dir / 'terminal.json'
                projection = evidence_dir / 'projection.json'
                if projection.exists() or projection.is_symlink():
                    _assert_contained(projection, self.root)
                if not terminal.exists() and not terminal.is_symlink():
                    if projection.exists():
                        raise StoreError('projection-without-terminal')
                    continue
                _assert_contained(terminal, self.root)
                try:
                    record = json.loads(terminal.read_text(encoding='utf-8'))
                    from .serialization import validate_evidence_record
                    record = validate_evidence_record(record)
                except Exception:
                    raise StoreError('invalid-terminal-evidence') from None
                if record['family_id'] != family_dir.name or record['evidence_id'] != evidence_dir.name or record['repository_id'] != self.repository_id:
                    raise StoreError('invalid-terminal-evidence')
                if (evidence_dir / 'terminal.json.publication-uncertain').exists():
                    raise StoreError('terminal-publication-uncertain')
                if projection.exists():
                    try:
                        derived = json.loads(projection.read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        derived = None
                    if derived != {'schema_version': 2, 'evidence_id': record['evidence_id'],
                                   'family_id': record['family_id'], 'receipt_hash': record['receipt_hash'],
                                   'status': record['status']}:
                        # Terminal authority wins; replace derived data from it.
                        self.rebuild_projection(record)
                records.append(record)
                if not projection.exists():
                    self.rebuild_projection(record)
        return records

    def reconstruct_terminals(self) -> list[dict]:
        """Rebuild only projections from validated immutable terminal receipts."""
        return self._scan_terminals()

    def rebuild_projection(self, terminal: dict) -> None:
        _validate_component(terminal.get('family_id'), 'invalid-terminal-identity')
        _validate_component(terminal.get('evidence_id'), 'invalid-terminal-identity')
        path = self.runs / terminal['family_id'] / terminal['evidence_id'] / 'projection.json'
        projection = {'schema_version': 2, 'evidence_id': terminal['evidence_id'],
                      'family_id': terminal['family_id'], 'receipt_hash': terminal['receipt_hash'],
                      'status': terminal['status']}
        _atomic_replace_projection(path, projection, fault=self._fault)

    def publish_execution_terminal(self, record: dict) -> str:
        """Create once the authoritative receipt for a fully drained execution."""
        required = {'schema_version', 'execution_id', 'repository_id', 'started_hash',
                    'execution_identity', 'backend_identity', 'policy_identity', 'command_identity',
                    'exit_code', 'timed_out', 'cancelled', 'drainage', 'output_observation',
                    'post_observation', 'result', 'ended_at'}
        if (not isinstance(record, dict) or set(record) - (required | {'receipt_hash', 'verification_evidence',
                'retry_policy_proof', 'harness_invocation_upper_bound', 'candidate_identity',
                'final_changed_surface_id', 'stdout_hash', 'stderr_hash', 'output_persistence',
                'launch_reservation_id', 'launch_consumption_id', 'plan_id', 'unit_id',
                'obligation_ids', 'admission_id', 'admission_sha256',
                'reservation_transition_id', 'consumption_transition_id',
                'plan_acceptance_transition_id', 'lifecycle_generation'}) or
                not required <= set(record) or type(record.get('schema_version')) is not int or
                record.get('schema_version') != 2 or
                record.get('repository_id') != self.repository_id or
                record.get('drainage') != 'DRAINED' or type(record.get('timed_out')) is not bool or
                type(record.get('cancelled')) is not bool or
                record.get('output_observation') not in ('CAPTURED', 'UNAVAILABLE') or
                record.get('result') not in ('PASS', 'FAIL', 'ERROR', 'TIMEOUT', 'ABORTED') or
                type(record.get('ended_at')) not in (int, float)):
            raise StoreError('invalid-execution-terminal')
        hashes = (record.get('stdout_hash'), record.get('stderr_hash'))
        if ('stdout_hash' in record or 'stderr_hash' in record or
              'output_persistence' not in record or
              record.get('output_persistence') not in ('OMITTED', 'UNAVAILABLE') or
              (record.get('output_observation') == 'CAPTURED' and record.get('output_persistence') != 'OMITTED') or
              (record.get('output_observation') == 'UNAVAILABLE' and record.get('output_persistence') != 'UNAVAILABLE')):
            raise StoreError('invalid-execution-terminal')
        if record.get('output_observation') == 'UNAVAILABLE' and record.get('result') != 'ABORTED':
            raise StoreError('invalid-execution-terminal')
        _validate_component(record.get('execution_id'), 'invalid-execution-terminal')
        self._validate_terminal_authority_binding(self.executions / record['execution_id'], record)
        if 'candidate_identity' in record:
            started = self._validated_started(self.executions / record['execution_id'])
            if (record.get('candidate_identity') != started.get('candidate_identity') or
                    record.get('final_changed_surface_id') != started.get('final_changed_surface_id')):
                raise StoreError('invalid-execution-terminal')
        evidence = record.get('verification_evidence')
        if evidence is not None:
            _validate_component(record.get('execution_id'), 'invalid-execution-terminal')
            try:
                from .serialization import validate_evidence_record
                evidence = validate_evidence_record(evidence)
            except Exception:
                raise StoreError('invalid-execution-terminal') from None
            started = self._validated_started(self.executions / record['execution_id'])
            if (record.get('result') != 'PASS' or evidence.get('repository_id') != self.repository_id or
                    evidence.get('family_id') != started.get('family_id') or
                    evidence.get('gate_id') != started.get('gate_id') or
                    evidence.get('ownership_token') != started.get('attempt_id') or
                    evidence.get('pre_fingerprint') != started.get('input_fingerprint')):
                raise StoreError('invalid-execution-terminal')
        if (not isinstance(record.get('execution_identity'), dict) or
                not all(isinstance(record.get(key), str) and record[key] for key in
                        ('execution_id', 'backend_identity', 'policy_identity', 'command_identity',
                         'started_hash'))):
            raise StoreError('invalid-execution-terminal')
        # A retry-free claim is derived from the trusted STARTED facts and a
        # single durable terminal for its execution identity. It is never
        # accepted as an independent boolean from the caller.
        started = self._validated_started(self.executions / record['execution_id'])
        proof = record.get('retry_policy_proof')
        if started.get('critical') is True and started.get('retry_policy') == 'forbid':
            expected = started.get('retry_policy_proof')
            if (not isinstance(proof, dict) or proof != expected or
                    record.get('harness_invocation_upper_bound') not in {0, 1} or
                    proof.get('retry_free') is not True or
                    record.get('command_identity') != started.get('command_identity') or
                    started.get('command_identity') != started.get('launch_intent_hash') or
                    record.get('execution_identity') != started.get('execution_identity')):
                raise StoreError('retry-policy-violation')
        elif proof != started.get('retry_policy_proof'):
            raise StoreError('invalid-execution-terminal')
        from .serialization import canonical
        body = dict(record)
        supplied = body.pop('receipt_hash', None)
        receipt_hash = hashlib.sha256(canonical(body)).hexdigest()
        if supplied is not None and supplied != receipt_hash:
            raise StoreError('invalid-execution-terminal')
        body['receipt_hash'] = receipt_hash
        path = self.executions / record['execution_id'] / 'terminal.json'
        started = self._validated_started(self.executions / record['execution_id'])
        if (started.get('predecessor_failure_id') is not None or
                started.get('consumption_id') is not None or started.get('retry_proof') is not None):
            self.validate_started_retry_proof(started)
        _assert_contained(path, self.root)
        return publish_create_once(path, body, fault=self._fault)

    def publish_execution_projection(self, terminal: dict) -> None:
        """Rebuildable per-execution view and index, always downstream of terminal truth."""
        path = self.executions / terminal['execution_id'] / 'projection.json'
        projection = {'schema_version': 1, 'execution_id': terminal['execution_id'],
                      'receipt_hash': terminal['receipt_hash'], 'result': terminal['result']}
        _atomic_replace_projection(path, projection, fault=self._fault)
        records = self.reconstruct_execution_terminals(rebuild=False)
        index = {'schema_version': 1, 'executions': [
            {'execution_id': item['execution_id'], 'receipt_hash': item['receipt_hash'],
             'result': item['result']} for item in records]}
        _atomic_replace_projection(self.root / 'state' / 'execution-index.json', index, fault=self._fault)

    def publish_evidence_record(self, record: dict) -> str:
        """Publish a validated immutable evidence record embedded by a terminal receipt."""
        try:
            from .serialization import validate_evidence_record
            validated = validate_evidence_record(record)
        except Exception:
            raise StoreError('invalid-terminal-evidence') from None
        if validated.get('repository_id') != self.repository_id:
            raise StoreError('invalid-terminal-evidence')
        _validate_component(validated.get('family_id'), 'invalid-terminal-evidence')
        _validate_component(validated.get('evidence_id'), 'invalid-terminal-evidence')
        path = self.runs / validated['family_id'] / validated['evidence_id'] / 'terminal.json'
        _assert_contained(path, self.root)
        return publish_create_once(path, validated, fault=self._fault)

    def rebuild_terminal_evidence(self, terminal: dict) -> None:
        """Recreate evidence authority/projection from embedded terminal truth."""
        if terminal.get('schema_version') != 2:
            # Historical v1 receipts remain readable but cannot mint current
            # reusable evidence during restart reconstruction.
            return
        record = terminal.get('verification_evidence')
        if record is None:
            return
        self.publish_evidence_record(record)
        self._scan_terminals()

    def reconstruct_execution_terminals(self, *, rebuild: bool = True) -> list[dict]:
        """Recover immutable execution truth and repair only derived projections."""
        records = []
        if not self.executions.exists():
            return records
        _assert_contained(self.executions, self.root)
        for directory in sorted(self.executions.iterdir()):
            if not directory.is_dir() or directory.is_symlink() or not (directory / 'terminal.json').exists():
                continue
            try:
                record = json.loads((directory / 'terminal.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                raise StoreError('invalid-execution-terminal') from None
            supplied = record.pop('receipt_hash', None)
            from .serialization import canonical
            actual = hashlib.sha256(canonical(record)).hexdigest()
            record['receipt_hash'] = supplied
            started = self._validated_started(directory, allow_legacy_retry=True)
            self._validate_terminal_authority_binding(directory, record)
            if (started.get('predecessor_failure_id') is not None or
                    started.get('consumption_id') is not None or started.get('retry_proof') is not None):
                self.validate_started_retry_proof(started)
            expected_started_hash = hashlib.sha256((directory / 'started.json').read_bytes()).hexdigest()
            evidence = record.get('verification_evidence')
            if evidence is not None:
                try:
                    from .serialization import validate_evidence_record
                    evidence = validate_evidence_record(evidence)
                except Exception:
                    raise StoreError('invalid-execution-terminal') from None
                if (record.get('result') != 'PASS' or evidence.get('repository_id') != self.repository_id or
                        evidence.get('family_id') != started.get('family_id') or
                        evidence.get('gate_id') != started.get('gate_id') or
                        evidence.get('ownership_token') != started.get('attempt_id') or
                        evidence.get('pre_fingerprint') != started.get('input_fingerprint')):
                    raise StoreError('invalid-execution-terminal')
            legacy_output_ok = (record.get('output_observation') == 'CAPTURED' and
                all(isinstance(record.get(key), str) and re.fullmatch(r'[0-9a-f]{64}', record[key])
                    for key in ('stdout_hash', 'stderr_hash'))) or (
                record.get('output_observation') == 'UNAVAILABLE' and record.get('result') == 'ABORTED' and
                (record.get('stdout_hash'), record.get('stderr_hash')) == (None, None))
            current_output_ok = ('stdout_hash' not in record and 'stderr_hash' not in record and
                'output_persistence' in record and
                record.get('output_persistence') in ('OMITTED', 'UNAVAILABLE') and
                ((record.get('output_observation') == 'CAPTURED' and record.get('output_persistence') == 'OMITTED') or
                 (record.get('output_observation') == 'UNAVAILABLE' and record.get('output_persistence') == 'UNAVAILABLE')))
            if (type(record.get('schema_version')) is not int or record.get('schema_version') not in (1, 2) or
                    record.get('execution_id') != directory.name or
                    record.get('repository_id') != self.repository_id or supplied != actual or
                    record.get('drainage') != 'DRAINED' or
                    record.get('output_observation') not in ('CAPTURED', 'UNAVAILABLE') or
                    (record.get('output_observation') == 'UNAVAILABLE' and record.get('result') != 'ABORTED') or
                    not (legacy_output_ok if record.get('schema_version') == 1 else current_output_ok) or
                    record.get('started_hash') != expected_started_hash or
                    record.get('execution_identity') != started.get('execution_identity')):
                raise StoreError('invalid-execution-terminal')
            if (record.get('retry_policy_proof') != started.get('retry_policy_proof') or
                    (started.get('critical') is True and started.get('retry_policy') == 'forbid' and
                     (record.get('harness_invocation_upper_bound') not in {0, 1} or
                      started['retry_policy_proof'].get('retry_free') is not True or
                      record.get('command_identity') != started.get('command_identity') or
                      started.get('command_identity') != started.get('launch_intent_hash')))):
                raise StoreError('retry-policy-violation')
            records.append(record)
        if rebuild:
            for record in records:
                path = self.executions / record['execution_id'] / 'projection.json'
                expected = {'schema_version': 1, 'execution_id': record['execution_id'],
                            'receipt_hash': record['receipt_hash'], 'result': record['result']}
                try:
                    current = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    current = None
                if current != expected:
                    _atomic_replace_projection(path, expected, fault=self._fault)
            # Immutable evidence receipts and their projections precede the
            # mutable index projection during both normal publication and
            # recovery.
            for record in records:
                self.rebuild_terminal_evidence(record)
            _atomic_replace_projection(self.root / 'state' / 'execution-index.json',
                {'schema_version': 1, 'executions': [{'execution_id': item['execution_id'],
                    'receipt_hash': item['receipt_hash'], 'result': item['result']} for item in records]},
                fault=self._fault)
        return records

    def publish_evidence(self, evidence: Evidence) -> str:
        record = evidence_record(evidence)
        _validate_component(evidence.family_id, 'invalid-terminal-identity')
        _validate_component(evidence.evidence_id, 'invalid-terminal-identity')
        receipt = self.publish_evidence_record(record)
        return receipt

    def iter_evidence(self):
        historical_v1_evidence = set()
        for execution in self.reconstruct_execution_terminals(rebuild=False):
            if execution.get('schema_version') == 1 and isinstance(execution.get('verification_evidence'), dict):
                evidence = execution['verification_evidence']
                historical_v1_evidence.add((evidence.get('family_id'), evidence.get('evidence_id')))
        for record in self._scan_terminals():
            if (record.get('family_id'), record.get('evidence_id')) not in historical_v1_evidence:
                yield record

    def critical_failures(self, *, repository_id: str, profile_hash: str, gate_id: str, fingerprint: str):
        context = {'repository_id': repository_id, 'profile_hash': profile_hash,
                   'gate_id': gate_id, 'fingerprint': fingerprint}
        failure = self.current_failure(context)
        return [failure] if failure is not None else []

    @staticmethod
    def _failure_id(body: dict) -> str:
        return 'critical-failure-sha256-v1:' + hashlib.sha256(canonical(body)).hexdigest()

    def _terminal_for_execution(self, execution_id: str) -> tuple[dict, dict]:
        _validate_component(execution_id, 'invalid-terminal-evidence')
        journal = self.executions / execution_id
        started = self._validated_started(journal, allow_legacy_retry=True)
        records = self.reconstruct_execution_terminals(rebuild=False)
        terminal = next((item for item in records if item.get('execution_id') == execution_id), None)
        if terminal is None:
            raise StoreError('terminal-execution-not-found')
        return terminal, started

    def publish_critical_failure(self, execution_id: str, *, predecessor_failure_id: str | None = None,
                                 consumption_id: str | None = None) -> dict:
        """Materialize immutable failure authority from a validated Phase-C terminal."""
        terminal, started = self._terminal_for_execution(execution_id)
        if (terminal.get('result') != 'FAIL' or terminal.get('timed_out') is True or
                terminal.get('cancelled') is True or terminal.get('drainage') != 'DRAINED' or
                started.get('critical') is not True):
            raise StoreError('not-critical-verification-failure')
        if predecessor_failure_id is None:
            predecessor_failure_id = started.get('predecessor_failure_id')
        if consumption_id is None:
            consumption_id = started.get('consumption_id')
        context = {'repository_id': self.repository_id,
            'profile_hash': started.get('profile_hash'), 'gate_id': started.get('gate_id'),
            'fingerprint': failure_fingerprint(started),
            'policy_identity': terminal.get('policy_identity'),
            'backend_identity': terminal.get('backend_identity')}
        if predecessor_failure_id is not None:
            predecessor = self.failure_receipt(predecessor_failure_id)
            if (predecessor is None or predecessor['context'] != context or not consumption_id or
                    not self._consumption_proves(consumption_id, predecessor_failure_id, execution_id)):
                raise StoreError('RETRY_PROOF_MISSING')
        semantic = {
            'schema_version': 1, 'protocol': 'critical-failure-v1',
            'context': context, 'execution_id': execution_id,
            'terminal_receipt_hash': terminal['receipt_hash'],
            'command_identity': terminal['command_identity'],
            'failure_classification': 'CRITICAL_VERIFICATION_FAIL',
            'failure_reason': 'verification-command-failed',
            'predecessor_failure_id': predecessor_failure_id,
            'consumption_id': consumption_id,
        }
        record = {'failure_id': self._failure_id(semantic), **semantic}
        path = self.failures / f"{record['failure_id']}.json"
        _assert_contained(path, self.root)
        publish_create_once(path, record, fault=self._fault)
        self._project_failures()
        return record

    def _read_failure_receipt(self, path: pathlib.Path) -> dict:
        _assert_contained(path, self.root)
        if path.with_name(path.name + '.publication-uncertain').exists():
            raise StoreError('critical-failure-publication-uncertain')
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise StoreError('invalid-critical-failure-receipt') from None
        if not isinstance(record, dict):
            raise StoreError('invalid-critical-failure-receipt')
        required = {'failure_id', 'schema_version', 'protocol', 'context', 'execution_id',
                    'terminal_receipt_hash', 'command_identity', 'failure_classification',
                    'failure_reason', 'predecessor_failure_id', 'consumption_id'}
        body = {key: value for key, value in record.items() if key != 'failure_id'}
        if (not isinstance(record, dict) or set(record) != required or
                record.get('schema_version') != 1 or record.get('protocol') != 'critical-failure-v1' or
                record.get('failure_classification') != 'CRITICAL_VERIFICATION_FAIL' or
                record.get('failure_id') != self._failure_id(body) or path.name != f"{record.get('failure_id')}.json"):
            raise StoreError('invalid-critical-failure-receipt')
        terminal, started = self._terminal_for_execution(record['execution_id'])
        context = {'repository_id': self.repository_id, 'profile_hash': started.get('profile_hash'),
                   'gate_id': started.get('gate_id'), 'fingerprint': failure_fingerprint(started),
                   'policy_identity': terminal.get('policy_identity'),
                   'backend_identity': terminal.get('backend_identity')}
        if (terminal['receipt_hash'] != record['terminal_receipt_hash'] or terminal['result'] != 'FAIL' or
                started.get('critical') is not True or record.get('context') != context):
            raise StoreError('invalid-critical-failure-receipt')
        predecessor = record.get('predecessor_failure_id')
        if predecessor is not None:
            prior = self.failure_receipt(predecessor)
            if (prior is None or prior.get('context') != context or not self._consumption_proves(
                    record.get('consumption_id'), predecessor, record['execution_id'])):
                raise StoreError('RETRY_PROOF_MISSING')
        return record

    def failure_receipt(self, failure_id: str) -> dict | None:
        _validate_component(failure_id, 'invalid-critical-failure-id')
        path = self.failures / f'{failure_id}.json'
        if not path.exists():
            return None
        return self._read_failure_receipt(path)

    def _failure_records(self) -> list[dict]:
        records = []
        if self.failures.exists():
            _assert_contained(self.failures, self.root)
            for path in sorted(self.failures.glob('*.json')):
                records.append(self._read_failure_receipt(path))
        # The terminal receipt is the source of truth. Re-materialize a missing
        # failure receipt deterministically after a crash at that boundary.
        for terminal in self.reconstruct_execution_terminals(rebuild=False):
            if terminal.get('result') != 'FAIL' or terminal.get('timed_out') or terminal.get('cancelled'):
                continue
            started = self._validated_started(self.executions / terminal['execution_id'], allow_legacy_retry=True)
            if started.get('critical') is True:
                context = {'repository_id': self.repository_id,
                    'profile_hash': started.get('profile_hash'), 'gate_id': started.get('gate_id'),
                    'fingerprint': failure_fingerprint(started),
                    'policy_identity': terminal.get('policy_identity'),
                    'backend_identity': terminal.get('backend_identity')}
                expected_id = self._failure_id({
                    'schema_version': 1, 'protocol': 'critical-failure-v1',
                    'context': context,
                    'execution_id': terminal['execution_id'],
                    'terminal_receipt_hash': terminal['receipt_hash'],
                    'command_identity': terminal['command_identity'],
                    'failure_classification': 'CRITICAL_VERIFICATION_FAIL',
                    'failure_reason': 'verification-command-failed',
                    'predecessor_failure_id': started.get('predecessor_failure_id'),
                    'consumption_id': started.get('consumption_id'),
                })
                if not (self.failures / f'{expected_id}.json').exists():
                    self.publish_critical_failure(terminal['execution_id'],
                        predecessor_failure_id=started.get('predecessor_failure_id'),
                        consumption_id=started.get('consumption_id'))
                    receipt = self.failure_receipt(expected_id)
                    if receipt is None:
                        raise StoreError('invalid-critical-failure-receipt')
                    records.append(receipt)
        return records

    def _resolved_failure_ids(self) -> set[str]:
        resolved = set()
        if self.resolutions.exists():
            _assert_contained(self.resolutions, self.root)
            for path in sorted(self.resolutions.glob('*.json')):
                _assert_contained(path, self.root)
                if path.with_name(path.name + '.publication-uncertain').exists():
                    raise StoreError('failure-resolution-publication-uncertain')
                try:
                    record = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    raise StoreError('invalid-failure-resolution') from None
                if (not isinstance(record, dict) or set(record) != {
                        'schema_version', 'failure_id', 'execution_id', 'terminal_receipt_hash',
                        'consumption_id', 'result'} or
                        path.name != f"{record.get('failure_id')}--{record.get('consumption_id')}.json" or
                        record.get('schema_version') != 1 or record.get('result') != 'PASS'):
                    raise StoreError('invalid-failure-resolution')
                failure = self.failure_receipt(record['failure_id'])
                terminal, _started = self._terminal_for_execution(record['execution_id'])
                proof = self.validate_retry_proof(record['execution_id'],
                    required_failure_id=record['failure_id'])
                if (failure is None or terminal.get('result') != 'PASS' or
                        terminal.get('receipt_hash') != record['terminal_receipt_hash'] or
                        proof.get('consumption_id') != record['consumption_id']):
                    raise StoreError('invalid-failure-resolution')
                resolved.add(record['failure_id'])
        return resolved

    def current_failure(self, context: dict) -> dict | None:
        if not isinstance(context, dict) or context.get('repository_id') != self.repository_id:
            return None
        required = {'profile_hash', 'gate_id', 'fingerprint'}
        if not required <= set(context):
            return None
        records = self._failure_records()
        relevant = [record for record in records if all(record['context'].get(key) == value
                    for key, value in context.items() if key in required)]
        predecessors = {record.get('predecessor_failure_id') for record in relevant
                        if record.get('predecessor_failure_id')}
        resolved = self._resolved_failure_ids() | predecessors
        unresolved = [record for record in relevant if record['failure_id'] not in resolved]
        if len(unresolved) > 1:
            raise StoreError('ambiguous-critical-failure-lineage')
        return unresolved[0] if unresolved else None

    def failure_for_scope(self, context: dict) -> dict | None:
        """Return the latest immutable failure for a scope, including a PASS-resolved fence.

        A successful authorized retry resolves the current incident and permits
        evidence reuse, but does not turn its one-shot grant into reusable
        authority for a later mandatory fresh execution.
        """
        if not isinstance(context, dict) or context.get('repository_id') != self.repository_id:
            return None
        if not {'profile_hash', 'gate_id', 'fingerprint'} <= set(context):
            return None
        required = {'profile_hash', 'gate_id', 'fingerprint'}
        relevant = [record for record in self._failure_records()
                    if all(record['context'].get(key) == value for key, value in context.items()
                           if key in required)]
        predecessors = {record.get('predecessor_failure_id') for record in relevant
                        if record.get('predecessor_failure_id')}
        tips = [record for record in relevant if record['failure_id'] not in predecessors]
        if len(tips) > 1:
            raise StoreError('ambiguous-critical-failure-lineage')
        return tips[0] if tips else None

    def active_failure_fences(self) -> tuple[dict, ...]:
        records = self._failure_records()
        predecessors = {item.get('predecessor_failure_id') for item in records
                        if item.get('predecessor_failure_id')}
        # PASS resolution permits reuse, but leaves the historical scope
        # fenced for a later mandatory fresh execution.
        return tuple(item for item in records if item['failure_id'] not in predecessors)

    def issue_failure_grant(self, *_args, **_kwargs) -> dict:
        """Automation cannot issue grants; use import_failure_grant for a signed envelope."""
        raise StoreError('FAILURE_GRANT_AUTHORITY_REQUIRED')

    def import_failure_grant(self, envelope: dict) -> dict:
        """Verify an externally signed human grant and publish it immutably."""
        from .human_grants import HumanGrantError, verify_grant
        try:
            grant = verify_grant(envelope, self.issuer_registry)
        except HumanGrantError as exc:
            raise StoreError(str(exc)) from None
        grant_id = grant.get('grant_id')
        failure_id = grant.get('failure_id')
        context = grant.get('context')
        _validate_component(grant_id, 'invalid-grant-id')
        failure = self.failure_receipt(failure_id)
        current = self.failure_for_scope(context)
        retry_scope = grant.get('retry_scope')
        if (failure is None or failure['context'] != context or current is None or current['failure_id'] != failure_id or
                not isinstance(retry_scope, dict) or retry_scope.get('gate_id') != context.get('gate_id') or
                retry_scope.get('profile_hash') != context.get('profile_hash') or
                retry_scope.get('fence_fingerprint') != context.get('fingerprint')):
            raise StoreError('FAILURE_GRANT_SCOPE_MISMATCH')
        if grant.get('terminal_receipt_hash') != failure['terminal_receipt_hash']:
            raise StoreError('FAILURE_GRANT_SCOPE_MISMATCH')
        record = grant
        _assert_contained(self.grants / f'{grant_id}.json', self.root)
        publish_create_once(self.grants / f'{grant_id}.json', record, fault=self._fault)
        self._project_grants()
        return record

    def _grant(self, grant_id: str) -> dict:
        _validate_component(grant_id, 'invalid-grant-id')
        path = self.grants / f'{grant_id}.json'
        _assert_contained(path, self.root)
        if path.with_name(path.name + '.publication-uncertain').exists():
            raise StoreError('FAILURE_GRANT_INVALID')
        try:
            grant = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise StoreError('FAILURE_GRANT_REQUIRED') from None
        if not isinstance(grant, dict):
            raise StoreError('FAILURE_GRANT_INVALID')
        from .human_grants import HumanGrantError, verify_grant
        envelope = {key: grant.get(key) for key in ('schema_version', 'protocol', 'grant_id', 'failure_id',
            'context', 'retry_scope', 'terminal_receipt_hash', 'issuer_id', 'authorizer_principal', 'justification',
            'issued_at', 'signature')}
        try:
            verified = verify_grant(envelope, self.issuer_registry)
        except HumanGrantError:
            raise StoreError('FAILURE_GRANT_INVALID') from None
        if (grant.get('protocol') != 'critical-gate-retry-grant-v1' or grant.get('grant_id') != grant_id or
                grant.get('grant_hash') != verified.get('grant_hash') or
                grant.get('issued_by') != verified.get('issued_by') or
                grant.get('reason') != verified.get('reason')):
            raise StoreError('FAILURE_GRANT_INVALID')
        return grant

    def consume_failure_grant(self, grant_id: str | None, *, failure_id: str | None,
                              context: dict, retry_scope: dict, execution_id: str,
                              lock_held: bool = False) -> dict:
        if not grant_id:
            raise StoreError('FAILURE_GRANT_REQUIRED')
        _validate_component(execution_id, 'invalid-execution-identity')
        with (contextlib.nullcontext() if lock_held else repository_lock(self.root)):
            grant = self._grant(grant_id)
            path = self.consumptions / f'{grant_id}.json'
            if path.exists():
                raise StoreError('FAILURE_GRANT_CONSUMED')
            failure = self.failure_for_scope(context)
            if failure is None:
                raise StoreError('CRITICAL_FAILURE_FENCE_NOT_ACTIVE')
            if (failure_id != failure['failure_id'] or grant.get('failure_id') != failure['failure_id'] or
                    grant.get('context') != failure.get('context') or failure.get('context') != context or
                    grant.get('retry_scope') != retry_scope or
                    grant.get('terminal_receipt_hash') != failure.get('terminal_receipt_hash')):
                raise StoreError('FAILURE_GRANT_SCOPE_MISMATCH')
            semantic = {'schema_version': 1, 'protocol': 'failure-grant-consumption-v1',
                'consumption_id': 'failure-consumption-sha256-v1:' + hashlib.sha256(canonical({
                    'grant_id': grant_id, 'failure_id': failure['failure_id'],
                    'execution_id': execution_id, 'context': context,
                    'retry_scope': retry_scope})).hexdigest(),
                'grant_id': grant_id, 'grant_hash': grant['grant_hash'],
                'failure_id': failure['failure_id'], 'terminal_receipt_hash': failure['terminal_receipt_hash'],
                'execution_id': execution_id, 'context': context, 'retry_scope': retry_scope}
            record = {**semantic, 'consumption_hash': hashlib.sha256(canonical(semantic)).hexdigest()}
            _assert_contained(path, self.root)
            try:
                publish_create_once(path, record, fault=self._fault)
            except StoreError as exc:
                if str(exc) in {'immutable-record-collision', 'immutable-record-unreadable'}:
                    raise StoreError('FAILURE_GRANT_CONSUMED') from None
                raise
            self._project_grants()
            return record

    def _consumption_proves(self, consumption_id: str, failure_id: str, execution_id: str) -> bool:
        if not self.consumptions.exists():
            return False
        for path in sorted(self.consumptions.glob('*.json')):
            record = self._read_consumption(path)
            if (record.get('consumption_id') == consumption_id and record.get('failure_id') == failure_id and
                    record.get('execution_id') == execution_id):
                return True
        return False

    def _read_consumption(self, path: pathlib.Path) -> dict:
        _assert_contained(path, self.root)
        if path.with_name(path.name + '.publication-uncertain').exists():
            raise StoreError('invalid-grant-consumption')
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise StoreError('invalid-grant-consumption') from None
        if not isinstance(record, dict):
            raise StoreError('invalid-grant-consumption')
        grant = self._grant(path.stem)
        body = {key: value for key, value in record.items() if key != 'consumption_hash'}
        semantic_id = 'failure-consumption-sha256-v1:' + hashlib.sha256(canonical({
            'grant_id': record.get('grant_id'), 'failure_id': record.get('failure_id'),
            'execution_id': record.get('execution_id'), 'context': record.get('context'),
            'retry_scope': record.get('retry_scope')})).hexdigest()
        if (record.get('schema_version') != 1 or record.get('protocol') != 'failure-grant-consumption-v1' or
                record.get('grant_id') != path.stem or record.get('grant_hash') != grant.get('grant_hash') or
                record.get('failure_id') != grant.get('failure_id') or
                record.get('context') != grant.get('context') or
                record.get('retry_scope') != grant.get('retry_scope') or
                record.get('terminal_receipt_hash') != grant.get('terminal_receipt_hash') or
                record.get('consumption_id') != semantic_id or
                record.get('consumption_hash') != hashlib.sha256(canonical(body)).hexdigest()):
            raise StoreError('invalid-grant-consumption')
        _validate_component(record.get('execution_id'), 'invalid-grant-consumption')
        return record

    def grant_consumed(self, grant_id: str) -> bool:
        self._grant(grant_id)
        path = self.consumptions / f'{grant_id}.json'
        if not path.exists():
            return False
        self._read_consumption(path)
        return True

    def validate_started_retry_proof(self, started: dict, *, required_failure_id: str | None = None) -> dict:
        execution_id = started.get('execution_id')
        proof = started.get('retry_proof')
        if not isinstance(proof, dict) or not self._consumption_proves(
                proof.get('consumption_id'), proof.get('failure_id'), execution_id):
            raise StoreError('RETRY_PROOF_MISSING')
        if required_failure_id is not None and proof.get('failure_id') != required_failure_id:
            raise StoreError('RETRY_PROOF_MISSING')
        if proof.get('grant_id') is None or not self.grant_consumed(proof['grant_id']):
            raise StoreError('RETRY_PROOF_MISSING')
        consumed = self._read_consumption(self.consumptions / f"{proof['grant_id']}.json")
        expected_context = {'repository_id': self.repository_id,
            'profile_hash': started.get('profile_hash'), 'gate_id': started.get('gate_id'),
            'fingerprint': failure_fingerprint(started),
            'policy_identity': started.get('policy_identity'),
            'backend_identity': started.get('backend_identity')}
        if (proof.get('grant_hash') != consumed.get('grant_hash') or
                proof.get('retry_scope') != consumed.get('retry_scope') or
                started.get('retry_scope') != consumed.get('retry_scope') or
                consumed.get('context') != expected_context or
                consumed.get('execution_id') != execution_id or
                started.get('predecessor_failure_id') != proof.get('failure_id') or
                started.get('consumption_id') != proof.get('consumption_id')):
            raise StoreError('RETRY_PROOF_MISSING')
        return proof

    def validate_retry_proof(self, execution_id: str, *, required_failure_id: str | None = None) -> dict:
        terminal, started = self._terminal_for_execution(execution_id)
        proof = self.validate_started_retry_proof(started, required_failure_id=required_failure_id)
        if terminal.get('repository_id') != self.repository_id:
            raise StoreError('RETRY_PROOF_MISSING')
        return proof

    def publish_failure_resolution(self, failure_id: str, execution_id: str, *, result: str) -> dict:
        failure = self.failure_receipt(failure_id)
        if failure is None or result != 'PASS':
            raise StoreError('invalid-failure-resolution')
        terminal, _started = self._terminal_for_execution(execution_id)
        proof = self.validate_retry_proof(execution_id, required_failure_id=failure_id)
        if (terminal.get('result') != 'PASS' or proof.get('failure_id') != failure_id or
                failure.get('context', {}).get('repository_id') != self.repository_id):
            raise StoreError('RETRY_PROOF_MISSING')
        body = {'schema_version': 1, 'failure_id': failure_id,
                'execution_id': execution_id, 'terminal_receipt_hash': terminal['receipt_hash'],
                'consumption_id': proof['consumption_id'], 'result': 'PASS'}
        path = self.resolutions / f"{failure_id}--{proof['consumption_id']}.json"
        publish_create_once(path, body, fault=self._fault)
        self._project_failures()
        return body

    def retry_state(self, failure_id: str) -> str:
        failure = self.failure_receipt(failure_id)
        if failure is None:
            raise StoreError('RETRY_PROOF_MISSING')
        for path in sorted(self.consumptions.glob('*.json')) if self.consumptions.exists() else ():
            item = self._read_consumption(path)
            if item.get('failure_id') == failure_id:
                return 'CONSUMED'
        return 'AVAILABLE'

    def _project_failures(self) -> None:
        records = self._failure_records_without_rebuild()
        superseded = {item.get('predecessor_failure_id') for item in records
                      if item.get('predecessor_failure_id')}
        resolved = self._resolved_failure_ids() | superseded
        tips = [item['failure_id'] for item in records if item['failure_id'] not in superseded]
        _atomic_replace_projection(self.root / 'state' / 'failure-index.json',
            {'schema_version': 1, 'failures': [item['failure_id'] for item in records],
             'unresolved': [item['failure_id'] for item in records
                            if item['failure_id'] not in resolved],
             'active_fences': tips}, fault=self._fault)

    def _failure_records_without_rebuild(self) -> list[dict]:
        if not self.failures.exists():
            return []
        return [self._read_failure_receipt(path) for path in sorted(self.failures.glob('*.json'))]

    def _project_grants(self) -> None:
        grants = []
        if self.grants.exists():
            for path in sorted(self.grants.glob('*.json')):
                item = self._grant(path.stem)
                grants.append({'grant_id': item['grant_id'], 'failure_id': item['failure_id'],
                               'consumed': self.grant_consumed(item['grant_id'])})
        _atomic_replace_projection(self.root / 'state' / 'grant-index.json',
            {'schema_version': 1, 'grants': grants}, fault=self._fault)

    def rebuild_failure_projections(self) -> None:
        self._failure_records()
        self._project_failures()
        self._project_grants()
