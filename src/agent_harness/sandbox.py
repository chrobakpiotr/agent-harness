"""OS-level isolation wrapper for deterministic verification commands.

The command itself must already have passed runner.py's strict allowlist. This module adds a second
boundary: no network and constrained filesystem writes where the host supports it. `auto` prefers
Codex's local sandbox helper, then native platform tooling. `required` fails closed if no strong
backend exists; `off` is an explicit human opt-out.

From Showcase `tooling/agent-harness/verification_sandbox.py` at `49ab97f` (blob `5eed6339`), lines 175-1401;
lines 31-174 are `agent_harness.executables`. Consumer policy (extra environment, worktree wrappers, consumer
authority roots) is passed in explicitly; see docs/migration/provenance.md.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any

from agent_harness.executables import (  # noqa: F401  (re-exported for the Showcase surface)
    SYSTEM_EXECUTABLE_DIRECTORIES,
    TOOLCHAIN_ENVIRONMENT_SELECTORS,
    ExecutableResolutionError,
    _overlaps,
    _resolved_path,
    _secure_directory,
    _sha256_file,
    resolve_executable,
    trusted_executable_directories,
    verify_executable_binding,
)


@dataclass(frozen=True)
class SandboxPlan:
    argv: list[str]
    env: dict[str, str]
    backend: str
    strong_isolation: bool
    details: dict[str, Any]
    qualification: 'BackendQualification | None' = None
    executable_bindings: tuple[dict[str, str], ...] = ()


class BackendProtocol:
    """Trusted interface required of a v2 execution backend."""

    name: str

    def qualify(self, worktree: pathlib.Path, authority_paths: tuple[pathlib.Path, ...]) -> dict[str, Any]:
        raise NotImplementedError

    def launch(self, plan: SandboxPlan, *, cwd: pathlib.Path, env: dict[str, str]):
        raise NotImplementedError

    def drain(self, process, timeout: float) -> bool:
        raise NotImplementedError


@dataclass(frozen=True)
class BackendCandidate:
    """A discovered executable; discovery alone never authorizes a launch."""

    kind: str
    version: str
    executable: str
    platform: str
    state: str = 'DISCOVERED'
    qualification: 'BackendQualification | None' = None


@dataclass(frozen=True)
class BackendQualification:
    status: str
    backend_identity: str
    policy_identity: str
    fingerprint: str
    protected_paths: tuple[str, ...]
    checks: tuple['QualificationCheck', ...]
    reason_codes: tuple[str, ...]

    @property
    def authorizes_execution(self) -> bool:
        names = {check.check_id for check in self.checks}
        return (self.status == 'QUALIFIED' and names == set(QUALIFICATION_IDS) and
                len(self.checks) == len(QUALIFICATION_IDS) and
                all(check.status == 'PASS' and check.evidence for check in self.checks))


@dataclass(frozen=True)
class QualificationCheck:
    check_id: str
    name: str
    status: str
    reason_code: str
    evidence: str


@dataclass(frozen=True)
class ExecutionIdentity:
    """Strict, serializable identity for one execution-unit generation."""

    execution_id: str
    backend_identity: str
    unit_id: str
    repository_id: str
    policy_identity: str
    birth_identity: str = ''
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {'schema_version': self.schema_version, 'execution_id': self.execution_id,
                'backend_identity': self.backend_identity, 'unit_id': self.unit_id,
                'birth_identity': self.birth_identity, 'repository_id': self.repository_id,
                'policy_identity': self.policy_identity}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> 'ExecutionIdentity':
        fields = {'schema_version', 'execution_id', 'backend_identity', 'unit_id',
                  'birth_identity', 'repository_id', 'policy_identity'}
        if not isinstance(value, dict) or set(value) != fields or value.get('schema_version') != 1:
            raise ValueError('invalid-execution-identity')
        if any(not isinstance(value[key], str) or not value[key] for key in fields - {'schema_version'}):
            raise ValueError('invalid-execution-identity')
        return cls(value['execution_id'], value['backend_identity'], value['unit_id'],
                   value['repository_id'], value['policy_identity'], value['birth_identity'], 1)


@dataclass(frozen=True)
class Reconciliation:
    status: str
    reason_code: str
    evidence: str


@dataclass(frozen=True)
class PreparedExecution:
    identity: ExecutionIdentity
    state_path: pathlib.Path


@dataclass
class RunningExecution:
    identity: ExecutionIdentity
    process: subprocess.Popen
    state_path: pathlib.Path


class PosixProcessGroupBackend:
    """Concrete POSIX session adapter; candidate only until adversarial probes pass.

    The prepared UUID is durable before launch. Process-group membership is not
    treated as containment proof: sessions can be escaped and are rejected by
    qualification until a host proves descendant coverage.
    """

    name = 'posix-process-group/v1'

    def discover(self) -> bool:
        return os.name == 'posix' and hasattr(os, 'killpg')

    def prepare(self, root: pathlib.Path, *, repository_id: str, policy_identity: str,
                execution_id: str | None = None, backend_identity: str | None = None) -> PreparedExecution:
        root = pathlib.Path(root).resolve()
        unit_id = execution_id or uuid.uuid4().hex
        unit_dir = root / unit_id
        unit_dir.mkdir(parents=True, exist_ok=False)
        identity = ExecutionIdentity(unit_id, backend_identity or self.name, str(unit_dir), repository_id,
                                     policy_identity, uuid.uuid4().hex)
        state = {'schema_version': 1, 'identity': identity.to_dict(), 'state': 'PREPARED'}
        self._write_state(unit_dir / 'unit.json', state)
        return PreparedExecution(identity, unit_dir / 'unit.json')

    def launch(self, prepared: PreparedExecution, argv: list[str], *, cwd: pathlib.Path,
               env: dict[str, str]) -> RunningExecution:
        state = self._read_state(prepared.state_path)
        if state['identity'] != prepared.identity.to_dict() or state['state'] != 'PREPARED':
            raise RuntimeError('execution-unit-not-prepared')
        self._write_state(prepared.state_path, {**state, 'state': 'LAUNCHING'})
        read_gate, write_gate = os.pipe()
        gate = ('import os,sys; fd=int(sys.argv[1]); token=os.read(fd,1); os.close(fd); '
                'os._exit(125) if token != b"1" else os.execvpe(sys.argv[2],sys.argv[2:],os.environ)')
        process = None
        try:
            process = subprocess.Popen([sys.executable, '-I', '-c', gate, str(read_gate), *argv],
                                       cwd=cwd, env=env, start_new_session=True,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       pass_fds=(read_gate,))
            os.close(read_gate)
            birth = self._birth_identity(process.pid)
            self._write_state(prepared.state_path, {**state, 'state': 'ACTIVE', 'pid': process.pid,
                                                   'birth_identity': birth})
            os.write(write_gate, b'1')
            os.close(write_gate)
        except (OSError, IndexError, ValueError):
            # Do not leave an untracked payload running when the host cannot
            # provide the generation marker. The trusted shim is held on a pipe
            # until the identity is durable, so requested payload code has not
            # executed at this point.
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                process.wait()
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
            try:
                os.close(read_gate)
            except OSError:
                pass
            self._write_state(prepared.state_path, {**state, 'state': 'LAUNCH_FAILED'})
            raise RuntimeError('process-birth-identity-unavailable') from None
        finally:
            try:
                os.close(write_gate)
            except OSError:
                pass
        return RunningExecution(prepared.identity, process, prepared.state_path)

    def inspect(self, identity: ExecutionIdentity) -> Reconciliation:
        return self.reconcile(identity)

    def cancel(self, identity: ExecutionIdentity) -> str:
        state_path = self._state_path(identity)
        try:
            state = self._read_state(state_path)
            recorded = ExecutionIdentity.from_dict(state['identity'])
        except (OSError, ValueError, KeyError, TypeError):
            return 'UNCERTAIN'
        if recorded != identity:
            return 'UNCERTAIN'
        if state.get('state') in {'PREPARED', 'LAUNCH_FAILED', 'DRAINED'}:
            return 'DRAINED'
        if state.get('state') not in {'ACTIVE', 'CANCELLING'} or not isinstance(state.get('pid'), int):
            return 'UNCERTAIN'
        try:
            os.killpg(state['pid'], signal.SIGKILL)
            state['state'] = 'CANCELLING'
            self._write_state(state_path, state)
            return 'CANCEL_REQUESTED'
        except ProcessLookupError:
            return self._group_status(state['pid'])
        except OSError:
            return 'UNCERTAIN'

    def cancel_and_drain(self, identity: ExecutionIdentity, timeout: float) -> Reconciliation:
        before = self.inspect(identity)
        if before.status == 'DRAINED':
            return before
        if before.status == 'UNCERTAIN':
            return before
        requested = self.cancel(identity)
        if requested == 'UNCERTAIN':
            return Reconciliation('UNCERTAIN', 'CANCEL_UNCERTAIN', 'execution-unit-cancel-not-confirmed')
        result = self.drain(identity, timeout)
        return Reconciliation(result, f'CANCEL_{result}', f'cancel={requested};drain={result}')

    def drain(self, identity: ExecutionIdentity, timeout: float) -> str:
        deadline = time.monotonic() + max(0, timeout)
        state_path = self._state_path(identity)
        while time.monotonic() <= deadline:
            try:
                state = self._read_state(state_path)
                recorded = ExecutionIdentity.from_dict(state['identity'])
            except (OSError, ValueError, KeyError, TypeError):
                return 'UNCERTAIN'
            if recorded != identity:
                return 'UNCERTAIN'
            pid = state.get('pid')
            if state.get('state') in {'PREPARED', 'LAUNCH_FAILED', 'DRAINED'}:
                return 'DRAINED'
            if not isinstance(pid, int):
                return 'UNCERTAIN'
            status = self._group_status(pid)
            if status == 'DRAINED':
                self._write_state(state_path, {**state, 'state': 'DRAINED'})
                return 'DRAINED'
            if status == 'UNCERTAIN':
                return 'UNCERTAIN'
            time.sleep(min(.05, max(0, deadline - time.monotonic())))
        return 'NOT_DRAINED' if isinstance(self._read_state(state_path).get('pid'), int) else 'UNCERTAIN'

    def reconcile(self, identity: ExecutionIdentity) -> Reconciliation:
        try:
            state = self._read_state(self._state_path(identity))
            recorded = ExecutionIdentity.from_dict(state['identity'])
            if recorded != identity:
                return Reconciliation('UNCERTAIN', 'STALE_EXECUTION_IDENTITY', 'identity-mismatch')
            if state['state'] == 'PREPARED':
                return Reconciliation('DRAINED', 'NOT_LAUNCHED', 'prepared-unit-has-no-payload')
            if state['state'] == 'LAUNCH_FAILED':
                return Reconciliation('DRAINED', 'LAUNCH_FAILED_BEFORE_PAYLOAD', 'trusted-gate-never-released')
            if state['state'] == 'DRAINED':
                return Reconciliation('DRAINED', 'DRAINED_MARKER', 'previous-positive-drainage-proof')
            pid = state.get('pid')
            birth = state.get('birth_identity')
            if not isinstance(pid, int) or not isinstance(birth, str):
                return Reconciliation('UNCERTAIN', 'DRAINAGE_UNCERTAIN', 'launch-state-incomplete')
            status = self._group_status(pid)
            if status == 'DRAINED':
                # Persist the positive empty-unit observation so a fresh
                # facade can distinguish a known drained unit from missing
                # or stale identity state.
                self._write_state(self._state_path(identity), {**state, 'state': 'DRAINED'})
                return Reconciliation('DRAINED', 'GROUP_ABSENT', 'killpg-zero-returned-esrch')
            if status == 'NOT_DRAINED':
                current_birth = self._birth_identity(pid)
                if current_birth != birth:
                    return Reconciliation('UNCERTAIN', 'STALE_EXECUTION_IDENTITY', 'pid-generation-mismatch')
                return Reconciliation('ACTIVE', 'GROUP_PRESENT', 'live-process-group-member')
            return Reconciliation('UNCERTAIN', 'DRAINAGE_UNCERTAIN', 'process-table-unavailable')
        except (OSError, ValueError, KeyError, TypeError):
            return Reconciliation('UNCERTAIN', 'DRAINAGE_UNCERTAIN', 'identity-state-unavailable')

    @staticmethod
    def _state_path(identity: ExecutionIdentity) -> pathlib.Path:
        # unit_id names the pre-created canonical registry directory, never a PID.
        return pathlib.Path(identity.unit_id) / 'unit.json'

    @staticmethod
    def _write_state(path: pathlib.Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix('.tmp')
        payload = json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')
        with temporary.open('wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _read_state(path: pathlib.Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or value.get('schema_version') != 1:
            raise ValueError('invalid-execution-unit-state')
        return value

    @staticmethod
    def _birth_identity(pid: int) -> str:
        # Linux exposes the kernel process-start tick. macOS exposes the
        # equivalent microsecond process start time through proc_pidinfo; using
        # `ps lstart` would lose precision and is intentionally not accepted.
        if platform.system().lower() == 'darwin':
            class ProcBsdInfo(ctypes.Structure):
                _fields_ = [
                    ('pbi_flags', ctypes.c_uint32), ('pbi_status', ctypes.c_uint32),
                    ('pbi_xstatus', ctypes.c_uint32), ('pbi_pid', ctypes.c_uint32),
                    ('pbi_ppid', ctypes.c_uint32), ('pbi_uid', ctypes.c_uint32),
                    ('pbi_gid', ctypes.c_uint32), ('pbi_ruid', ctypes.c_uint32),
                    ('pbi_rgid', ctypes.c_uint32), ('pbi_svuid', ctypes.c_uint32),
                    ('pbi_svgid', ctypes.c_uint32), ('rfu_1', ctypes.c_uint32),
                    ('pbi_comm', ctypes.c_char * 16), ('pbi_name', ctypes.c_char * 32),
                    ('pbi_nfiles', ctypes.c_uint32), ('pbi_pgid', ctypes.c_uint32),
                    ('pbi_pjobc', ctypes.c_uint32), ('e_tdev', ctypes.c_uint32),
                    ('e_tpgid', ctypes.c_uint32), ('pbi_nice', ctypes.c_int32),
                    ('pbi_start_tvsec', ctypes.c_uint64), ('pbi_start_tvusec', ctypes.c_uint64),
                ]
            try:
                libproc = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
                proc_pidinfo = libproc.proc_pidinfo
                proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                         ctypes.c_void_p, ctypes.c_int]
                proc_pidinfo.restype = ctypes.c_int
                info = ProcBsdInfo()
                size = ctypes.sizeof(info)
                copied = proc_pidinfo(pid, 3, 0, ctypes.byref(info), size)
                if copied != size or info.pbi_pid != pid or not info.pbi_start_tvsec:
                    raise OSError('process-birth-identity-unavailable')
                return f'{info.pbi_start_tvsec}:{info.pbi_start_tvusec}:{pid}'
            except (OSError, AttributeError):
                raise OSError('process-birth-identity-unavailable') from None
        # Other hosts fail closed unless an equally strong native source is
        # implemented; a coarse process-table timestamp is not a substitute.
        stat = pathlib.Path(f'/proc/{pid}/stat')
        if not stat.exists():
            raise OSError('process-birth-identity-unavailable')
        fields = stat.read_text(encoding='ascii').split()
        return f'{fields[21]}:{pid}'

    @staticmethod
    def _group_status(pgid: int) -> str:
        # killpg(0) also reports zombies. They are no longer live execution
        # members, so consult the process table and require positive evidence
        # that no non-zombie member of this process group exists.
        try:
            rows = subprocess.check_output(['/bin/ps', '-axo', 'pgid=,stat='],
                                           stderr=subprocess.DEVNULL, text=True)
            for row in rows.splitlines():
                fields = row.split()
                if len(fields) >= 2 and fields[0].isdigit() and int(fields[0]) == pgid:
                    if not fields[1].startswith('Z'):
                        return 'NOT_DRAINED'
            return 'DRAINED'
        except (OSError, subprocess.SubprocessError, ValueError):
            return 'UNCERTAIN'


_QUALIFICATION_VERSION = 'verification-backend-q-v1'
REQUIRED_ADVERSARIAL_CHECKS = (
    'PERMITTED_WRITE', 'PROTECTED_WRITE_DIRECT', 'PROTECTED_WRITE_CHILD',
    'PROTECTED_WRITE_GRANDCHILD', 'CONTAINMENT_CHILD', 'CONTAINMENT_GRANDCHILD',
    'CONTAINMENT_PARENT_EXIT', 'CONTAINMENT_NEW_PROCESS_GROUP', 'CONTAINMENT_NEW_SESSION',
    'CONTAINMENT_BACKGROUND_SHELL', 'EXECUTION_IDENTITY_DURABLE',
    'EXECUTION_IDENTITY_STALE_REJECTED', 'CANCEL_EXECUTION_UNIT', 'DRAIN_EXECUTION_UNIT',
    'RESTART_ACTIVE', 'RESTART_DRAINED',
)
QUALIFICATION_IDS = tuple(f'Q{index:02d}' for index in range(1, 17))
QUALIFICATION_CHECKS = tuple(zip(QUALIFICATION_IDS, REQUIRED_ADVERSARIAL_CHECKS))


def compare_execution_identity(expected: ExecutionIdentity, observed: ExecutionIdentity) -> Reconciliation:
    """Reject PID/unit reuse unless the backend generation marker also matches."""
    if expected.to_dict() != observed.to_dict():
        return Reconciliation('UNCERTAIN', 'STALE_EXECUTION_IDENTITY', 'identity-generation-mismatch')
    return Reconciliation('ACTIVE', 'IDENTITY_MATCH', 'backend-generation-match')


def admit_payload(qualification: BackendQualification) -> bool:
    """Payload is admitted only by a complete, all-PASS qualification result."""
    return qualification.authorizes_execution


def _canonical_paths(paths) -> tuple[str, ...]:
    return tuple(sorted({str(pathlib.Path(path).expanduser().resolve(strict=False)) for path in paths}))


def sandbox_policy_identity(writable_paths, protected_paths) -> str:
    """Stable identity of writable, write-protected and inaccessible paths."""
    no_access = derive_no_access_authority_roots(protected_paths)
    payload = json.dumps({
        'version': 2,
        'writable': _canonical_paths(writable_paths),
        'protected': _canonical_paths(protected_paths),
        'no_access': _canonical_paths(no_access),
        'network': 'denied',
        'descendant_policy': 'contained-unit-required',
    }, sort_keys=True, separators=(',', ':')).encode()
    return 'sha256:' + hashlib.sha256(payload).hexdigest()


DEFAULT_AUTHORITY_ROOTS = ('.agent-runs', '.agent-state', 'docs/specs')


def _authority_relative_roots(consumer_roots) -> tuple[str, ...]:
    # [seam] Consumer control-plane roots are relative to the primary checkout; nothing absolute or traversing.
    if not isinstance(consumer_roots, (tuple, list)):
        raise ValueError('sandbox-authority-roots-invalid')
    for value in consumer_roots:
        parts = pathlib.PurePosixPath(value).parts if isinstance(value, str) else ()
        if not parts or pathlib.PurePosixPath(value).is_absolute() or '..' in parts:
            raise ValueError('sandbox-authority-roots-invalid')
    return (*DEFAULT_AUTHORITY_ROOTS, *consumer_roots)


def derive_protected_authority_roots(repository: pathlib.Path, consumer_roots=()) -> tuple[pathlib.Path, ...]:
    """Resolve repository-wide authority from Git common-dir ownership.

    This deliberately ignores the linked-worktree cwd when selecting the
    authority owner. Any resolution ambiguity returns an empty tuple so callers
    cannot mistake an incomplete policy for a qualified one.
    """
    relative = _authority_relative_roots(consumer_roots)
    try:
        repo = pathlib.Path(repository).resolve(strict=True)
        common_text = subprocess.check_output(
            ['git', '-C', str(repo), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
            stderr=subprocess.DEVNULL, text=True).strip()
        common = pathlib.Path(common_text).resolve(strict=True)
        rows = subprocess.check_output(['git', '-C', str(repo), 'worktree', 'list', '--porcelain'],
                                       stderr=subprocess.DEVNULL, text=True)
        owners = []
        for line in rows.splitlines():
            if line.startswith('worktree '):
                try:
                    candidate = pathlib.Path(line[9:]).resolve(strict=True)
                except (OSError, RuntimeError):
                    # Git may retain prunable worktree records; they are not owners.
                    continue
                dotgit = candidate / '.git'
                if dotgit.is_dir() and dotgit.resolve() == common:
                    owners.append(candidate)
        if len(owners) != 1:
            return ()
        primary = owners[0]
        roots = _canonical_paths((*(primary / item for item in relative), primary / '.git', common))
        return tuple(pathlib.Path(path) for path in roots)
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return ()


def derive_no_access_authority_roots(protected_paths) -> tuple[pathlib.Path, ...]:
    """Return control data that children must neither read nor write."""
    return tuple(pathlib.Path(path) / 'control' / 'verification-v2'
                 for path in _canonical_paths(protected_paths)
                 if pathlib.Path(path).name == '.agent-runs')


def macos_sandbox_profile(writable_paths, protected_paths) -> str:
    """Generate deterministic deny-by-default filesystem policy text."""
    quote = lambda value: json.dumps(str(pathlib.Path(value).resolve(strict=False)))
    rows = ['(version 1)', '(deny default)', '(allow process*)', '(allow file-read*)',
            '(allow sysctl-read)', '(allow mach-lookup)', '(allow ipc-posix*)', '(allow signal)']
    no_access = derive_no_access_authority_roots(protected_paths)
    rows.extend(f'(deny file-read* (subpath {quote(path)}))' for path in no_access)
    rows.extend(f'(deny file-write* (subpath {quote(path)}))' for path in no_access)
    rows.extend(f'(deny file-write* (subpath {quote(path)}))' for path in _canonical_paths(protected_paths))
    rows.extend(f'(allow file-write* (subpath {quote(path)}))' for path in _canonical_paths(writable_paths))
    return '\n'.join(rows) + '\n'


def discover_backends() -> tuple[BackendCandidate, ...]:
    """Report host candidates without claiming their safety properties."""
    system = platform.system().lower()
    specs = [('codex-sandbox', 'codex'), ('bubblewrap', 'bwrap'),
             ('macos-sandbox-exec', 'sandbox-exec'), ('docker-container', 'docker')]
    found = []
    for kind, command in specs:
        try:
            binding = resolve_executable(command)
        except ExecutableResolutionError:
            continue
        executable = binding['path']
        if kind == 'bubblewrap' and system != 'linux':
            continue
        if kind == 'macos-sandbox-exec' and system != 'darwin':
            continue
        if kind == 'codex-sandbox' and system not in {'darwin', 'linux'}:
            continue
        found.append(BackendCandidate(kind, 'unqualified', executable, system))
    return tuple(found)


def _candidate_probe_argv(candidate: BackendCandidate, argv: list[str], cwd: pathlib.Path,
                          writable: tuple[str, ...], protected: tuple[str, ...],
                          profile_path: pathlib.Path) -> list[str] | None:
    if candidate.kind == 'macos-sandbox-exec' and candidate.platform == 'darwin':
        profile_path.write_text(macos_sandbox_profile(writable, protected), encoding='utf-8')
        return [candidate.executable, '-f', str(profile_path), *argv]
    if candidate.kind == 'codex-sandbox' and candidate.platform == 'darwin':
        return [candidate.executable, 'sandbox', 'macos', '--full-auto', *argv]
    return None


def _run_probe(candidate: BackendCandidate, code: str, args: list[str], *, cwd: pathlib.Path,
               writable: tuple[str, ...], protected: tuple[str, ...], profile_path: pathlib.Path,
               timeout: float = 5) -> tuple[int | None, str, str]:
    command = [sys.executable, '-c', code, *args]
    wrapped = _candidate_probe_argv(candidate, command, cwd, writable, protected, profile_path)
    if wrapped is None:
        return None, '', 'candidate-probe-wrapper-unsupported'
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': os.environ.get('HOME', str(cwd)),
           'TMPDIR': os.environ.get('TMPDIR', '/tmp'), 'PYTHONIOENCODING': 'utf-8'}
    try:
        result = subprocess.run(wrapped, cwd=cwd, env=env, capture_output=True, text=True,
                                 timeout=timeout, check=False)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return None, '', 'probe-timeout'
    except OSError as exc:
        return None, '', f'probe-launch:{type(exc).__name__}'
    finally:
        profile_path.unlink(missing_ok=True)


def _sandbox_launch_unavailable(stderr: str) -> bool:
    return ('sandbox_apply: Operation not permitted' in stderr or
            "execvp() of 'macos' failed" in stderr)


def _active_filesystem_probe(candidate: BackendCandidate, check_id: str, writable,
                             protected, worktree: pathlib.Path) -> tuple[str, str, str]:
    writable_paths = tuple(pathlib.Path(item) for item in writable)
    protected_paths = tuple(pathlib.Path(item) for item in protected)
    if not writable_paths or not protected_paths:
        return 'UNCERTAIN', 'PROBE_ROOTS_UNAVAILABLE', 'writable-or-protected-root-missing'
    writable_root = writable_paths[0]
    try:
        writable_root.mkdir(parents=True, exist_ok=True)
        writable_root = writable_root.resolve(strict=True)
    except OSError as exc:
        return 'UNCERTAIN', 'WRITABLE_PROBE_ROOT_UNAVAILABLE', type(exc).__name__
    profile = writable_root / f'.qualification-{uuid.uuid4().hex}.sb'
    nonce = uuid.uuid4().hex
    if check_id == 'Q01':
        marker = writable_root / f'.permitted-{nonce}'
        code = 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("ok"); print("created")'
        status, stdout, stderr = _run_probe(candidate, code, [str(marker)], cwd=worktree,
                                             writable=(*writable, str(writable_root)),
                                             protected=protected, profile_path=profile)
        passed = status == 0 and stdout == 'created' and marker.exists()
        marker.unlink(missing_ok=True)
        return ('PASS', 'PERMITTED_WRITE_CONFIRMED', 'marker-created-in-writable-root') if passed else (
            'UNSUPPORTED' if _sandbox_launch_unavailable(stderr) else
            'FAIL' if status is not None else 'UNCERTAIN',
            'CANDIDATE_SANDBOX_LAUNCH_UNAVAILABLE' if _sandbox_launch_unavailable(stderr) else
            'PERMITTED_WRITE_FAILED',
            f'exit={status};stdout={stdout};stderr={stderr[:200]}')

    authority = next((pathlib.Path(path) for path in protected
                      if pathlib.Path(path).is_dir() and pathlib.Path(path) != worktree), None)
    if authority is None:
        return 'UNCERTAIN', 'PROTECTED_ROOT_UNAVAILABLE', 'no-existing-protected-directory'
    marker = authority / f'.qualification-{nonce}'
    alias = writable_root / f'.authority-alias-{nonce}'
    try:
        alias.symlink_to(authority, target_is_directory=True)
    except OSError:
        alias = None
    if check_id != 'Q02' and alias is not None:
        alias.unlink(missing_ok=True)
        alias = None
    direct_code = (
        'import os,sys; p=sys.argv[1]; '
        'exec("try:\\n fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); os.close(fd); print(\\\"created\\\")\\nexcept OSError as e: print(\\\"denied:\\"+str(e.errno))")'
    )
    attempts: list[tuple[str, list[str], str]] = [('absolute', [str(marker)], direct_code)]
    attempts.append(('relative', [os.path.relpath(marker, worktree)], direct_code))
    if alias is not None:
        attempts.append(('symlink', [str(alias / marker.name)], direct_code))
    if check_id == 'Q02':
        for label, args, code in attempts:
            rc, stdout, stderr = _run_probe(candidate, code, args, cwd=worktree,
                                             writable=(*writable, str(writable_root)),
                                             protected=protected, profile_path=profile)
            if rc != 0 or not stdout.startswith('denied:') or marker.exists():
                marker.unlink(missing_ok=True)
                if alias is not None:
                    alias.unlink(missing_ok=True)
                unavailable = _sandbox_launch_unavailable(stderr)
                return ('UNSUPPORTED' if unavailable else 'FAIL' if rc is not None else 'UNCERTAIN',
                        'CANDIDATE_SANDBOX_LAUNCH_UNAVAILABLE' if unavailable else 'PROTECTED_DIRECT_BYPASS',
                        f'{label}:exit={rc};stdout={stdout};stderr={stderr[:200]}')
        shell_code = ('import subprocess,sys; p=sys.argv[1]; '
                      'r=subprocess.run(["/bin/sh","-c",'
                      '"python3 -c \\\"import os,sys; os.open(sys.argv[1],os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)\\\" \"$1\"",'
                      '"probe",p],capture_output=True); print("denied" if r.returncode else "created")')
        shell_leaf = 'import os,sys; os.open(sys.argv[1],os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)'
        shell_code = ('import subprocess,sys; r=subprocess.run(["/bin/sh","-c",'
                      + repr('exec "$@"') + ',"probe",sys.executable,"-c",'
                      + repr(shell_leaf) + ',sys.argv[1]],capture_output=True); '
                      'print("denied" if r.returncode else "created")')
        rc, stdout, stderr = _run_probe(candidate, shell_code, [str(marker)], cwd=worktree,
                                         writable=(*writable, str(writable_root)),
                                         protected=protected, profile_path=profile)
        if rc != 0 or stdout != 'denied' or marker.exists():
            marker.unlink(missing_ok=True)
            if alias is not None:
                alias.unlink(missing_ok=True)
            unavailable = _sandbox_launch_unavailable(stderr)
            return ('UNSUPPORTED' if unavailable else 'FAIL' if rc is not None else 'UNCERTAIN',
                    'CANDIDATE_SANDBOX_LAUNCH_UNAVAILABLE' if unavailable else 'PROTECTED_SHELL_BYPASS',
                    f'exit={rc};stdout={stdout};stderr={stderr[:200]}')
        if alias is not None:
            alias.unlink(missing_ok=True)
        read_result = _active_control_read_probe(candidate, check_id, writable, protected, worktree)
        if read_result[0] != 'PASS':
            return read_result
        return 'PASS', 'PROTECTED_WRITE_AND_CONTROL_READ_DENIED', 'direct-write-and-control-read-denied'

    if check_id == 'Q03':
        child_only_code = ('import subprocess,sys; r=subprocess.run([sys.executable,"-c",' +
                           repr(direct_code) + ',sys.argv[1]],capture_output=True,text=True); print(r.stdout.strip())')
        rc, stdout, stderr = _run_probe(candidate, child_only_code, [str(marker)], cwd=worktree,
                                         writable=(*writable, str(writable_root)),
                                         protected=protected, profile_path=profile)
        marker.unlink(missing_ok=True)
        if stdout.startswith('denied:') and not marker.exists() and rc == 0:
            read_result = _active_control_read_probe(candidate, check_id, writable, protected, worktree)
            if read_result[0] != 'PASS':
                return read_result
            return 'PASS', 'DESCENDANT_WRITE_AND_CONTROL_READ_DENIED', f'{check_id}:{stdout}'
        unavailable = _sandbox_launch_unavailable(stderr)
        return ('UNSUPPORTED' if unavailable else 'FAIL' if rc is not None else 'UNCERTAIN',
                'CANDIDATE_SANDBOX_LAUNCH_UNAVAILABLE' if unavailable else 'DESCENDANT_PROTECTED_WRITE_NOT_PROVEN',
                f'exit={rc};stdout={stdout};stderr={stderr[:200]}')
    code = (
        'import subprocess,sys; p=sys.argv[1]; '
        'leaf="import os,sys;\\ntry:\\n fd=os.open(sys.argv[1],os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); os.close(fd); print(\\\"created\\\")\\n'
        'except OSError as e: print(\\\"denied:\\"+str(e.errno))"; '
        'child="import subprocess,sys; r=subprocess.run([sys.executable,\'-c\',"+repr(leaf)+",sys.argv[1]],capture_output=True,text=True); print(r.stdout.strip())"; '
        'r=subprocess.run([sys.executable,"-c",child,p],capture_output=True,text=True); print(r.stdout.strip())'
    )
    rc, stdout, stderr = _run_probe(candidate, code, [str(marker)], cwd=worktree,
                                     writable=(*writable, str(writable_root)),
                                     protected=protected, profile_path=profile)
    marker.unlink(missing_ok=True)
    if stdout.startswith('denied:') and not marker.exists() and rc == 0:
        read_result = _active_control_read_probe(candidate, check_id, writable, protected, worktree)
        if read_result[0] != 'PASS':
            return read_result
        return 'PASS', 'DESCENDANT_WRITE_AND_CONTROL_READ_DENIED', f'{check_id}:{stdout}'
    unavailable = _sandbox_launch_unavailable(stderr)
    return ('UNSUPPORTED' if unavailable else 'FAIL' if rc is not None else 'UNCERTAIN',
            'CANDIDATE_SANDBOX_LAUNCH_UNAVAILABLE' if unavailable else 'DESCENDANT_PROTECTED_WRITE_NOT_PROVEN',
            f'exit={rc};stdout={stdout};stderr={stderr[:200]}')


def _active_control_read_probe(candidate: BackendCandidate, check_id: str, writable,
                               protected, worktree: pathlib.Path) -> tuple[str, str, str]:
    """Prove control records remain unreadable at the same process depth as write probes."""
    roots = derive_no_access_authority_roots(protected)
    control = roots[0] if len(roots) == 1 else None
    if control is None or not control.is_dir() or control.is_symlink():
        return 'UNCERTAIN', 'CONTROL_STORE_UNAVAILABLE', 'no-canonical-control-store-root'
    marker = control / f'.qualification-read-{uuid.uuid4().hex}'
    try:
        marker.write_text('trusted-control-read-probe', encoding='ascii')
        read_leaf = ('import pathlib,sys;\ntry:\n print("READ:"+pathlib.Path(sys.argv[1]).read_text())\n'
                     'except OSError as e: print("DENIED:"+str(e.errno))')
        if check_id == 'Q02':
            code = read_leaf
        elif check_id == 'Q03':
            code = ('import subprocess,sys; r=subprocess.run([sys.executable,"-c",' +
                    repr(read_leaf) + ',sys.argv[1]],capture_output=True,text=True); print(r.stdout.strip())')
        else:
            child = ('import subprocess,sys; r=subprocess.run([sys.executable,"-c",' +
                     repr(read_leaf) + ',sys.argv[1]],capture_output=True,text=True); print(r.stdout.strip())')
            code = ('import subprocess,sys; r=subprocess.run([sys.executable,"-c",' +
                    repr(child) + ',sys.argv[1]],capture_output=True,text=True); print(r.stdout.strip())')
        rc, stdout, stderr = _run_probe(candidate, code, [str(marker)], cwd=worktree,
            writable=(*writable, str(pathlib.Path(writable[0]))), protected=protected,
            profile_path=pathlib.Path(writable[0]) / f'.qualification-read-{uuid.uuid4().hex}.sb')
        if rc == 0 and stdout.startswith('DENIED:') and 'trusted-control-read-probe' not in stdout:
            return 'PASS', 'CONTROL_READ_DENIED', stdout
        unavailable = _sandbox_launch_unavailable(stderr)
        return ('UNSUPPORTED' if unavailable else 'FAIL' if rc is not None else 'UNCERTAIN',
                'CANDIDATE_SANDBOX_LAUNCH_UNAVAILABLE' if unavailable else 'CONTROL_READ_ALLOWED',
                f'exit={rc};stdout={stdout};stderr={stderr[:200]}')
    except OSError as exc:
        return 'UNCERTAIN', 'CONTROL_PROBE_SETUP_FAILED', type(exc).__name__
    finally:
        marker.unlink(missing_ok=True)


def _active_containment_probe(candidate: BackendCandidate, check_id: str, worktree: pathlib.Path,
                              writable: tuple[str, ...], protected: tuple[str, ...]) -> tuple[str, str, str]:
    if not writable:
        return 'UNCERTAIN', 'WRITABLE_PROBE_ROOT_UNAVAILABLE', 'no-writable-root'
    root = pathlib.Path(writable[0])
    try:
        root.mkdir(parents=True, exist_ok=True)
        root = root.resolve(strict=True)
    except OSError as exc:
        return 'UNCERTAIN', 'WRITABLE_PROBE_ROOT_UNAVAILABLE', type(exc).__name__
    nonce = uuid.uuid4().hex
    pid_file = root / f'.containment-{nonce}.pid'
    profile = root / f'.containment-{nonce}.sb'
    mode = check_id
    leaf = '''import os,sys,time
mode=sys.argv[1]
out=sys.argv[2]
result="not-attempted"
try:
    if mode == "Q08":
        os.setpgid(0, 0)
        result="escape-succeeded"
    elif mode == "Q09":
        os.setsid()
        result="escape-succeeded"
except OSError:
    result="escape-denied"
with open(out,"w") as stream: stream.write(str(os.getpid())+" "+result)
time.sleep(30)
'''
    child = [sys.executable, '-c', leaf, mode, str(pid_file)]
    if check_id == 'Q06':
        inner = 'import subprocess,sys,time; subprocess.Popen(sys.argv[1:]); time.sleep(30)'
        outer = ('import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' +
                 repr(inner) + ',*sys.argv[1:]]); time.sleep(30)')
        argv = [sys.executable, '-c', outer, *child]
    elif check_id == 'Q10':
        shell = 'sleep 30 & echo $! > "$1"; exit 0'
        code = ('import subprocess,sys; subprocess.run(["/bin/sh","-c",sys.argv[1],"probe",sys.argv[2]])')
        argv = [sys.executable, '-c', code, shell, str(pid_file)]
    elif check_id == 'Q05':
        parent = 'import subprocess,sys,time; p=subprocess.Popen(sys.argv[1:]); open(sys.argv[-1],"w").write(str(p.pid)); time.sleep(30)'
        argv = [sys.executable, '-c', parent, *child, str(pid_file)]
    elif check_id == 'Q07':
        parent = 'import subprocess,sys; p=subprocess.Popen(sys.argv[1:]); open(sys.argv[-1],"w").write(str(p.pid))'
        argv = [sys.executable, '-c', parent, *child, str(pid_file)]
    elif check_id in {'Q08', 'Q09'}:
        parent = 'import subprocess,sys,time; subprocess.Popen(sys.argv[1:]); time.sleep(30)'
        argv = [sys.executable, '-c', parent, *child, str(pid_file)]
    else:
        argv = child
    wrapped = _candidate_probe_argv(candidate, argv, worktree, (*writable, str(root)), protected, profile)
    if wrapped is None:
        pid_file.unlink(missing_ok=True)
        return 'UNSUPPORTED', 'CANDIDATE_PROCESS_PROBE_UNSUPPORTED', candidate.kind
    process = None
    child_pid = None
    try:
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': os.environ.get('HOME', str(root)),
               'TMPDIR': os.environ.get('TMPDIR', '/tmp')}
        process = subprocess.Popen(wrapped, cwd=worktree, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not pid_file.exists():
            if process.poll() is not None and check_id not in {'Q07', 'Q10'}:
                break
            time.sleep(.02)
        if pid_file.exists():
            try:
                child_pid = int(pid_file.read_text().split()[0])
            except (OSError, ValueError, IndexError):
                child_pid = None
        if child_pid is None:
            exited = process.poll() if process is not None else None
            return ('UNSUPPORTED' if exited is not None else 'UNCERTAIN',
                    'CANDIDATE_REJECTED_PROCESS_PROBE' if exited is not None else 'DESCENDANT_PID_UNAVAILABLE',
                    f'{check_id}:probe-did-not-publish-pid;exit={exited}')
        try:
            actual_group = os.getpgid(child_pid)
            live = True
        except (ProcessLookupError, PermissionError):
            actual_group, live = None, False
        if check_id in {'Q07', 'Q10'}:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                return 'FAIL', 'PARENT_DID_NOT_EXIT', check_id
            state = PosixProcessGroupBackend._group_status(process.pid)
            contained = live and actual_group == process.pid and state == 'NOT_DRAINED'
            return ('PASS', 'PARENT_EXIT_DESCENDANT_NOT_DRAINED', f'{check_id}:group={actual_group};state={state}') if contained else (
                'FAIL' if state == 'DRAINED' or (live and actual_group != process.pid) else 'UNCERTAIN',
                'PARENT_EXIT_DESCENDANT_NOT_VISIBLE', f'{check_id}:live={live};group={actual_group};state={state}')
        if check_id in {'Q08', 'Q09'}:
            outcome = pid_file.read_text().split(maxsplit=1)[1] if pid_file.exists() else ''
            contained = live and actual_group == process.pid
            passed = contained and outcome == 'escape-denied'
            return ('PASS', 'DESCENDANT_REMAINS_IN_UNIT', f'{check_id}:group={actual_group};{outcome}') if passed else (
                'FAIL' if live else 'UNCERTAIN', 'DESCENDANT_ESCAPED_EXECUTION_UNIT',
                f'{check_id}:live={live};group={actual_group};{outcome}')
        contained = live and actual_group == process.pid
        return ('PASS', 'DESCENDANT_ASSOCIATED_WITH_UNIT', f'{check_id}:group={actual_group}') if contained else (
            'FAIL' if live else 'UNCERTAIN', 'DESCENDANT_NOT_ASSOCIATED',
            f'{check_id}:live={live};group={actual_group};leader={process.pid}')
    except (OSError, subprocess.SubprocessError) as exc:
        return 'UNCERTAIN', 'CONTAINMENT_PROBE_ERROR', type(exc).__name__
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except OSError:
                pass
        if process is not None:
            try:
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
                process.wait()
        pid_file.unlink(missing_ok=True)
        profile.unlink(missing_ok=True)


def _run_active_qualification_check(candidate: BackendCandidate, check_id: str, name: str,
                                    *, worktree: pathlib.Path, writable: tuple[str, ...],
                                    protected: tuple[str, ...], repository_id: str,
                                    policy_identity: str) -> tuple[str, str, str]:
    if check_id in {'Q01', 'Q02', 'Q03', 'Q04'}:
        return _active_filesystem_probe(candidate, check_id, writable, protected, worktree)
    if check_id == 'Q12':
        expected = ExecutionIdentity('exec', 'backend/v1', 'unit', repository_id,
                                     policy_identity, 'birth-a')
        observed = ExecutionIdentity('exec', 'backend/v1', 'unit', repository_id,
                                     policy_identity, 'birth-b')
        result = compare_execution_identity(expected, observed)
        return ('PASS', 'STALE_IDENTITY_REJECTED', result.reason_code) if result.status == 'UNCERTAIN' else (
            'FAIL', 'STALE_IDENTITY_ACCEPTED', result.status)
    if check_id in {'Q05', 'Q06', 'Q07', 'Q08', 'Q09', 'Q10'}:
        return _active_containment_probe(candidate, check_id, worktree, writable, protected)
    if check_id in {'Q11', 'Q13', 'Q14', 'Q15', 'Q16'}:
        backend = PosixProcessGroupBackend()
        unit_root = pathlib.Path(writable[0]) / f'.qualification-unit-{uuid.uuid4().hex}'
        try:
            prepared = backend.prepare(unit_root, repository_id=repository_id,
                                       policy_identity=policy_identity)
        except OSError as exc:
            return 'UNCERTAIN', 'UNIT_PREPARE_FAILED', type(exc).__name__
        if check_id == 'Q11':
            valid = backend._read_state(prepared.state_path)['identity'] == prepared.identity.to_dict()
            shutil.rmtree(unit_root, ignore_errors=True)
            return ('PASS', 'EXECUTION_IDENTITY_DURABLE', 'prepared-identity-fsynced') if valid else (
                'FAIL', 'EXECUTION_IDENTITY_NOT_DURABLE', 'identity-readback-mismatch')
        if check_id == 'Q16':
            try:
                running = backend.launch(prepared, [sys.executable, '-c', 'import time; time.sleep(30)'],
                                         cwd=worktree, env={'PATH': os.environ.get('PATH', '/usr/bin:/bin')})
            except (OSError, RuntimeError) as exc:
                shutil.rmtree(unit_root, ignore_errors=True)
                return 'FAIL', 'EXECUTION_IDENTITY_UNAVAILABLE', str(exc)
            identity = ExecutionIdentity.from_dict(running.identity.to_dict())
            fresh = PosixProcessGroupBackend()
            fresh.cancel(identity)
            drained = fresh.drain(identity, 2)
            later = PosixProcessGroupBackend().reconcile(identity)
            try:
                running.process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                running.process.kill()
                running.process.communicate()
            shutil.rmtree(unit_root, ignore_errors=True)
            return ('PASS', 'RESTART_DRAINED', later.evidence) if drained == 'DRAINED' and later.status == 'DRAINED' else (
                'UNCERTAIN' if drained == 'UNCERTAIN' or later.status == 'UNCERTAIN' else 'FAIL',
                'RESTART_DRAINED_UNPROVEN', f'drain={drained};reconcile={later.status}')
        if check_id == 'Q13':
            descendant_file = unit_root / 'descendant.pid'
            payload = ('import subprocess,sys,time,pathlib; children=[]; end=time.monotonic()+.5; '
                       'exec("while time.monotonic()<end:\\n p=subprocess.Popen([sys.executable,\'-c\',\'import time;time.sleep(30)\'])\\n children.append(p.pid)\\n pathlib.Path(sys.argv[1]).write_text(\',\'.join(map(str,children)))\\n time.sleep(.01)"); '
                       'time.sleep(30)')
            try:
                running = backend.launch(prepared, [sys.executable, '-c', payload, str(descendant_file)],
                                         cwd=worktree, env={'PATH': os.environ.get('PATH', '/usr/bin:/bin')})
            except (OSError, RuntimeError) as exc:
                shutil.rmtree(unit_root, ignore_errors=True)
                return 'FAIL', 'EXECUTION_IDENTITY_UNAVAILABLE', str(exc)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not descendant_file.exists():
                time.sleep(.01)
            child_known = descendant_file.exists()
            result = PosixProcessGroupBackend().cancel_and_drain(running.identity, 2)
            try:
                running.process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                running.process.kill()
                running.process.communicate()
            shutil.rmtree(unit_root, ignore_errors=True)
            if not child_known:
                return 'FAIL', 'DESCENDANT_NOT_STARTED_BEFORE_CANCEL', 'cancellation-probe-race-not-established'
            return ('PASS', 'WHOLE_UNIT_CANCELLED_AND_DRAINED', result.evidence) if result.status == 'DRAINED' else (
                'UNCERTAIN' if result.status == 'UNCERTAIN' else 'FAIL', 'WHOLE_UNIT_CANCELLATION_UNPROVEN', result.evidence)
        if check_id == 'Q14':
            try:
                running = backend.launch(prepared, [sys.executable, '-c', 'import time; time.sleep(.2)'],
                                         cwd=worktree, env={'PATH': os.environ.get('PATH', '/usr/bin:/bin')})
            except (OSError, RuntimeError) as exc:
                shutil.rmtree(unit_root, ignore_errors=True)
                return 'FAIL', 'EXECUTION_IDENTITY_UNAVAILABLE', str(exc)
            running.process.wait(timeout=2)
            result = backend.reconcile(running.identity)
            running.process.communicate()
            shutil.rmtree(unit_root, ignore_errors=True)
            return ('PASS', 'DRAINED_WITH_PROCESS_TABLE_PROOF', result.evidence) if result.status == 'DRAINED' else (
                'UNCERTAIN' if result.status == 'UNCERTAIN' else 'FAIL', 'DRAINAGE_NOT_PROVEN', result.evidence)
        try:
            running = backend.launch(prepared, [sys.executable, '-c', 'import time; time.sleep(30)'],
                                     cwd=worktree, env={'PATH': os.environ.get('PATH', '/usr/bin:/bin')})
        except (OSError, RuntimeError) as exc:
            shutil.rmtree(unit_root, ignore_errors=True)
            return 'FAIL', 'EXECUTION_IDENTITY_UNAVAILABLE', str(exc)
        if check_id == 'Q15':
            identity = ExecutionIdentity.from_dict(running.identity.to_dict())
            fresh_backend = PosixProcessGroupBackend()
            fresh = fresh_backend.reconcile(identity)
            cleanup = fresh_backend.cancel_and_drain(identity, 2)
            try:
                running.process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                running.process.kill()
                running.process.communicate()
            shutil.rmtree(unit_root, ignore_errors=True)
            return ('PASS', 'RESTART_ACTIVE', f'{fresh.evidence};cleanup={cleanup.status}') if fresh.status == 'ACTIVE' else (
                'UNCERTAIN', 'RESTART_ACTIVE_UNPROVEN', fresh.evidence)
        result = backend.cancel_and_drain(running.identity, 2)
        running.process.communicate(timeout=2)
        shutil.rmtree(unit_root, ignore_errors=True)
        return ('PASS', 'EXECUTION_UNIT_CANCELLED_AND_DRAINED', result.evidence) if result.status == 'DRAINED' else (
            'UNCERTAIN' if result.status == 'UNCERTAIN' else 'FAIL', 'EXECUTION_UNIT_DRAIN_NOT_PROVEN', result.evidence)
    return 'UNSUPPORTED', 'UNKNOWN_QUALIFICATION_CHECK', name


def qualify_backend(candidate: BackendCandidate, *, worktree: pathlib.Path, writable_paths,
                    protected_paths, repository_id: str) -> BackendQualification:
    """Actively run all mandatory probes; incomplete evidence always rejects."""
    writable = _canonical_paths(writable_paths)
    protected = _canonical_paths(protected_paths)
    policy_id = sandbox_policy_identity(writable, protected)
    backend_name = (f'codex-{candidate.platform}' if candidate.kind == 'codex-sandbox'
                    else candidate.kind)
    backend_id = f'{backend_name}:{candidate.version}:{candidate.platform}:{candidate.executable}'
    body = {
        'backend': backend_id, 'policy': policy_id, 'protected': protected,
        'containment': 'strong-descendant-unit', 'repository': repository_id,
        'qualification_version': _QUALIFICATION_VERSION,
    }
    fingerprint = 'sha256:' + hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    checks = []
    for check_id, name in QUALIFICATION_CHECKS:
        try:
            status, reason, evidence = _run_active_qualification_check(
                candidate, check_id, name, worktree=pathlib.Path(worktree), writable=writable,
                protected=protected, repository_id=repository_id, policy_identity=policy_id)
        except Exception as exc:
            status, reason, evidence = 'UNCERTAIN', 'QUALIFICATION_PROBE_EXCEPTION', type(exc).__name__
        checks.append(QualificationCheck(check_id, name, status, reason, evidence))
    checks = tuple(checks)
    status = 'QUALIFIED' if all(check.status == 'PASS' for check in checks) else 'REJECTED'
    body['checks'] = [{'check_id': c.check_id, 'status': c.status,
                       'reason_code': c.reason_code, 'evidence': c.evidence} for c in checks]
    fingerprint = 'sha256:' + hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    reasons = tuple(sorted({c.reason_code for c in checks if c.status != 'PASS'}))
    return BackendQualification(status, backend_id, policy_id, fingerprint, protected, checks, reasons)


def reconcile_execution(identity: ExecutionIdentity) -> Reconciliation:
    """Conservatively reconcile a persisted identity without backend proof."""
    if (not identity.execution_id or not identity.backend_identity or not identity.unit_id or
            not identity.birth_identity or not identity.repository_id or not identity.policy_identity):
        return Reconciliation('UNCERTAIN', 'DRAINAGE_UNCERTAIN', 'execution-identity-incomplete')
    # No installed candidate currently provides a restart-stable unit oracle.
    return Reconciliation('UNCERTAIN', 'DRAINAGE_UNCERTAIN', 'backend-reconciliation-unavailable')


def qualifies_v2(plan: SandboxPlan) -> bool:
    """Only a concrete qualification record can authorize v2 launch."""
    proof = plan.qualification
    return bool(isinstance(proof, BackendQualification) and proof.authorizes_execution and
                proof.backend_identity.startswith(plan.backend + ':'))


def _safe_env(worktree: pathlib.Path, sandbox_home: pathlib.Path, toolchain_directories=(), *,
              environment_policy=None) -> dict[str, str]:
    return _apply_environment_policy(_isolated_env(worktree, sandbox_home, toolchain_directories),
                                     sandbox_home, environment_policy)


_PASSTHROUGH = (
    'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'TERM', 'COLORTERM', 'CI',
    'SYSTEMROOT', 'WINDIR', 'PATHEXT', 'COMSPEC',
)
_REFUSED_POLICY_KEYS = frozenset({
    'BASH_ENV', 'ENV', 'NODE_OPTIONS', 'NODE_PATH', 'JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS',
    'PERL5OPT', 'PERL5LIB', 'RUBYOPT', 'RUBYLIB', 'SHELLOPTS', 'PS4', 'PROMPT_COMMAND', 'IFS', 'CDPATH',
})
_REFUSED_POLICY_PREFIXES = ('LD_', 'DYLD_', 'PYTHON', 'GIT_', 'NPM_CONFIG_')


def _apply_environment_policy(env: dict[str, str], sandbox_home: pathlib.Path,
                              environment_policy) -> dict[str, str]:
    if environment_policy is None:
        return env
    # [seam] Consumer tool variables (e.g. a build tool's home) may add keys but never replace the
    # isolated base (PATH, HOME, TMPDIR, proxies, passthrough, toolchain selectors) or add the loader and
    # interpreter hooks the isolated base drops. The policy is consumer code; this is defence in depth.
    extra = environment_policy(dict(env), sandbox_home)
    # Upper-cased: environment names are case-insensitive on Windows.
    reserved = {key.upper() for key in (*env, *_PASSTHROUGH, *TOOLCHAIN_ENVIRONMENT_SELECTORS)}
    if not isinstance(extra, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) or not key or '=' in key or '\0' in key
            or '\0' in value or key.upper() in reserved
            or key.upper() in _REFUSED_POLICY_KEYS or key.upper().startswith(_REFUSED_POLICY_PREFIXES)
            for key, value in extra.items()):
        raise ValueError('sandbox-environment-policy-invalid')
    return {**env, **extra}


def _isolated_env(worktree: pathlib.Path, sandbox_home: pathlib.Path, toolchain_directories=()) -> dict[str, str]:
    sandbox_home.mkdir(parents=True, exist_ok=True)
    (sandbox_home / 'tmp').mkdir(exist_ok=True)
    directories = trusted_executable_directories(toolchain_directories, worktree=worktree,
                                                  sandbox_home=sandbox_home)
    env = {key: os.environ[key] for key in _PASSTHROUGH if key in os.environ}
    env.update({
        'PATH': os.pathsep.join(str(path) for path in directories),
        'HOME': str(sandbox_home),
        'TMPDIR': str(sandbox_home / 'tmp'),
        'XDG_CACHE_HOME': str(sandbox_home / 'cache'),
        'npm_config_cache': str(sandbox_home / 'npm-cache'),
        'PIP_CACHE_DIR': str(sandbox_home / 'pip-cache'),
        'GIT_TERMINAL_PROMPT': '0',
        'CI': env.get('CI', 'true'),
        # Common proxy variables are cleared as defense in depth. Strong backends also isolate network.
        'HTTP_PROXY': '', 'HTTPS_PROXY': '', 'ALL_PROXY': '', 'NO_PROXY': '*',
        'http_proxy': '', 'https_proxy': '', 'all_proxy': '', 'no_proxy': '*',
    })
    declared = tuple(pathlib.Path(path) for path in toolchain_directories)
    for key in TOOLCHAIN_ENVIRONMENT_SELECTORS:
        value = os.environ.get(key)
        if not value:
            continue
        try:
            selector = _resolved_path(value, strict=True)
            _secure_directory(selector)
            excluded = tuple(pathlib.Path(item).resolve(strict=False) for item in
                             (worktree, sandbox_home, env['TMPDIR']))
            if any(_overlaps(selector, root) for root in excluded):
                continue
            if not any(selector == path or path in selector.parents or
                       (selector / 'bin').resolve(strict=False) == path for path in declared):
                continue
            env[key] = str(selector)
        except (ExecutableResolutionError, OSError, RuntimeError, ValueError):
            # An inherited selector is optional. Invalid or undeclared values are
            # rejected by omission, never passed through to a child process.
            continue
    return env


def _codex_helper(argv: list[str], worktree: pathlib.Path, env: dict[str, str],
                  toolchain_directories=(), executable_bindings=()) -> SandboxPlan | None:
    try:
        binding = resolve_executable('codex', toolchain_directories=toolchain_directories,
                                     worktree=worktree, sandbox_home=env['HOME'], tmpdir=env['TMPDIR'])
    except ExecutableResolutionError as exc:
        if str(exc) == 'trusted-executable-not-found':
            return None
        raise
    codex = binding['path']
    if not codex:
        return None
    system = platform.system().lower()
    platform_name = 'macos' if system == 'darwin' else 'linux' if system == 'linux' else None
    if not platform_name:
        return None
    return SandboxPlan(
        [codex, 'sandbox', platform_name, '--full-auto', *argv],
        env,
        f'codex-{platform_name}',
        True,
        {'network': 'disabled', 'filesystem': 'workspace-write'}, None,
        tuple(executable_bindings) + (binding,),
    )


def _bubblewrap(argv: list[str], worktree: pathlib.Path, sandbox_home: pathlib.Path, env: dict[str, str],
               toolchain_directories=(), executable_bindings=(), *, authority_roots=(),
               policy_keys=()) -> SandboxPlan | None:
    if platform.system().lower() != 'linux':
        return None
    try:
        binding = resolve_executable('bwrap', toolchain_directories=toolchain_directories,
                                     worktree=worktree, sandbox_home=sandbox_home,
                                     tmpdir=env.get('TMPDIR'))
    except ExecutableResolutionError as exc:
        if str(exc) == 'trusted-executable-not-found':
            return None
        raise
    bwrap = binding['path']
    cmd = [
        bwrap, '--die-with-parent', '--new-session', '--unshare-all', '--share-user',
        '--ro-bind', '/', '/',
        '--bind', str(worktree), str(worktree),
        '--bind', str(sandbox_home), str(sandbox_home),
        '--proc', '/proc', '--dev', '/dev',
        '--chdir', str(worktree),
    ]
    protected = derive_protected_authority_roots(worktree, authority_roots)
    if not protected:
        return None
    # The writable worktree bind may contain control-plane code when running
    # from the primary checkout. Rebind every existing authority root read-only
    # after the workspace bind so descendants cannot replace trust anchors.
    existing_protected = tuple(path for path in protected if path.exists())
    for path in existing_protected:
        cmd += ['--ro-bind', str(path), str(path)]
    no_access = derive_no_access_authority_roots(protected)
    for path in no_access:
        if not path.is_dir() or path.is_symlink():
            return None
        cmd += ['--tmpfs', str(path)]
    for key in ('HOME', 'TMPDIR', 'XDG_CACHE_HOME', 'npm_config_cache', 'PIP_CACHE_DIR',
                'GIT_TERMINAL_PROMPT', 'CI', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', *policy_keys):
        if key in env:
            cmd += ['--setenv', key, env[key]]
    cmd += ['--', *argv]
    writable = (worktree, sandbox_home)
    return SandboxPlan(cmd, env, 'bubblewrap', True,
        {'network': 'unshared', 'root': 'read-only', 'worktree': 'writable',
         'protected_roots': [str(path) for path in existing_protected],
         'no_access_roots': [str(path) for path in no_access],
         'policy_fingerprint': sandbox_policy_identity(writable, protected)}, None,
        tuple(executable_bindings) + (binding,))


def _macos_sandbox_exec(argv: list[str], worktree: pathlib.Path, sandbox_home: pathlib.Path, env: dict[str, str],
                        toolchain_directories=(), executable_bindings=(), *,
                        authority_roots=()) -> SandboxPlan | None:
    if platform.system().lower() != 'darwin':
        return None
    try:
        binding = resolve_executable('sandbox-exec', toolchain_directories=toolchain_directories,
                                     worktree=worktree, sandbox_home=sandbox_home,
                                     tmpdir=env.get('TMPDIR'))
    except ExecutableResolutionError as exc:
        if str(exc) == 'trusted-executable-not-found':
            return None
        raise
    sandbox_exec = binding['path']
    # Keep the policy deliberately small: read system/toolchain files, write only task workspace and
    # isolated build home, and expose no network. This is stricter than relying on shell allowlists.
    profile = sandbox_home / 'verification.sb'
    protected = derive_protected_authority_roots(worktree, authority_roots)
    if not protected:
        return None
    write_roots = (worktree, sandbox_home)
    profile.write_text(macos_sandbox_profile(write_roots, protected), encoding='utf-8')
    return SandboxPlan(
        [sandbox_exec, '-f', str(profile), *argv], env, 'macos-sandbox-exec', True,
        {'network': 'denied-by-default', 'write_roots': [str(worktree), str(sandbox_home)],
         'protected_roots': [str(path) for path in protected],
         'no_access_roots': [str(path) for path in derive_no_access_authority_roots(protected)],
         'policy_fingerprint': sandbox_policy_identity(write_roots, protected)}, None,
        tuple(executable_bindings) + (binding,),
    )


def build_plan(
    argv: list[str], worktree: pathlib.Path, run_dir: pathlib.Path, mode: str = 'auto',
    toolchain_directories=(), *, environment_policy=None, worktree_wrappers=None, authority_roots=(),
) -> SandboxPlan:
    if mode not in {'auto', 'required', 'off'}:
        raise ValueError(f'unknown verification sandbox mode: {mode}')
    wrappers = dict(worktree_wrappers or {})
    # [seam] A consumer's worktree wrapper is one `./name` entry with its own error code.
    if any(not isinstance(name, str) or not re.fullmatch(r'\./[A-Za-z0-9][A-Za-z0-9._-]*', name) or
           not isinstance(code, str) or not code for name, code in wrappers.items()):
        raise ValueError('sandbox-worktree-wrappers-invalid')
    sandbox_home = run_dir / 'verification-home'
    env = _isolated_env(worktree, sandbox_home, toolchain_directories)
    base_keys = tuple(env)
    env = _apply_environment_policy(env, sandbox_home, environment_policy)
    policy_keys = tuple(key for key in env if key not in base_keys)
    argv = list(argv)
    if not argv:
        raise ExecutableResolutionError('invalid-command')
    if argv[0] in wrappers:
        code = wrappers[argv[0]]
        wrapper = pathlib.Path(worktree) / argv[0][2:]
        try:
            info = wrapper.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or not os.access(wrapper, os.X_OK):
                raise ExecutableResolutionError(code)
            argv[0] = str(wrapper.resolve(strict=True))
        except (OSError, RuntimeError, ValueError):
            raise ExecutableResolutionError(code) from None
        command_bindings = ()
    else:
        binding = resolve_executable(argv[0], toolchain_directories=toolchain_directories,
                                     worktree=worktree, sandbox_home=sandbox_home, tmpdir=env['TMPDIR'])
        argv[0] = binding['path']
        command_bindings = (binding,)
    if mode == 'off':
        return SandboxPlan(argv, env, 'off', False, {'explicit_opt_out': True},
                           executable_bindings=command_bindings)

    for factory in (
        lambda: _codex_helper(argv, worktree, env, toolchain_directories, command_bindings),
        lambda: _bubblewrap(argv, worktree, sandbox_home, env, toolchain_directories, command_bindings,
                            authority_roots=authority_roots, policy_keys=policy_keys),
        lambda: _macos_sandbox_exec(argv, worktree, sandbox_home, env, toolchain_directories, command_bindings,
                                    authority_roots=authority_roots),
    ):
        plan = factory()
        if plan is not None:
            return plan

    if mode == 'required':
        raise RuntimeError(
            'no strong verification sandbox available; install Codex CLI or bubblewrap, '
            'or use macOS sandbox-exec. Use --verification-sandbox off only as an explicit opt-out.'
        )
    return SandboxPlan(
        argv, env, 'allowlist-only', False,
        {'warning': 'OS sandbox unavailable; strict command allowlist and isolated environment only'},
        executable_bindings=command_bindings,
    )


def doctor() -> dict[str, Any]:
    """Read-only discovery report; no root-bound qualification is issued here."""
    system = platform.system().lower()
    candidates = {candidate.kind: candidate for candidate in discover_backends()}
    status = {}
    for kind in ('codex-sandbox', 'bubblewrap', 'macos-sandbox-exec', 'docker-container'):
        candidate = candidates.get(kind)
        # _candidate_probe_argv currently implements only these Darwin paths.
        supported = bool(candidate and candidate.platform == 'darwin' and
                         kind in ('codex-sandbox', 'macos-sandbox-exec'))
        status[kind] = {'discovered': candidate is not None,
                        'qualification_supported': supported,
                        'qualified': False, 'launch_ready': False}
    backends = {kind: value['discovered'] for kind, value in status.items()}
    return {
        'platform': system, 'backends': backends, 'backend_status': status,
        'discovered': any(backends.values()),
        'qualification_supported': any(value['qualification_supported'] for value in status.values()),
        # Docker has capability-spike evidence, but no adapter-backed
        # qualification or generation-safe lifecycle reconciliation yet.
        # Discovery has no exact policy/roots proof, nor lifecycle admission.
        'qualified': False, 'launch_ready': False, 'strong_available': False,
    }
