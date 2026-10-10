"""B1-B10 grading-boundary probes; incomplete target controls never pass."""
from __future__ import annotations

import dataclasses
import datetime as dt
import errno
import hashlib
import json
import pathlib
import os
import posixpath
import re
import signal
import selectors
import socket
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from typing import Any

from .q_probes import DockerProbeTarget


B1_B10 = {
    'B1': 'HIDDEN_MATERIAL_ABSENT',
    'B2': 'HIDDEN_EXPECTED_VALUES_UNREADABLE',
    'B3': 'FRESH_DESTROYED_SANDBOX',
    'B4': 'FILESYSTEM_AND_MOUNT_BOUNDARIES',
    'B5': 'NO_NETWORK_EGRESS_DNS_OR_METADATA',
    'B6': 'EXACT_ENVIRONMENT_NO_INHERITED_CREDENTIALS',
    'B7': 'NONROOT_NO_CAPS_NO_NEW_PRIVS_SECCOMP',
    'B8': 'RESOURCE_LIMITS_AND_BOUNDED_OUTPUT',
    'B9': 'BOUNDED_ARTIFACT_AFTER_SANDBOX_DESTROYED',
    'B10': 'PER_GRADE_TARGET_AND_LIMIT_PROVENANCE',
}
MAX_CAPTURED_OUTPUT_BYTES = 1_048_576
WORKSPACE_QUOTA_BYTES = 251_658_240
TMP_QUOTA_BYTES = 16_777_216
COMBINED_DISK_QUOTA_BYTES = WORKSPACE_QUOTA_BYTES + TMP_QUOTA_BYTES
_STANDARD_MOUNT_TARGETS = {
    '/', '/proc', '/dev', '/dev/pts', '/dev/mqueue', '/dev/shm', '/sys', '/sys/fs/cgroup',
    '/etc/hosts', '/etc/hostname', '/etc/resolv.conf', '/tmp',
    '/proc/acpi', '/proc/bus', '/proc/fs', '/proc/interrupts', '/proc/irq', '/proc/kcore',
    '/proc/keys', '/proc/sys', '/proc/sysrq-trigger', '/proc/timer_list', '/sys/firmware',
}
_PROTECTED_HOST_PATHS = (
    '/root', '/home', '/Users', '/run/host-services', '/var/run/docker.sock',
    '/host-credentials', '/host-hidden',
)


def _mount_path(value: str) -> str:
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match.group(1), 8)), value)


def _paths_overlap(left: str, right: str) -> bool:
    left, right = posixpath.normpath(left), posixpath.normpath(right)
    if left == '/' or right == '/':
        return left == right
    common = posixpath.commonpath((left, right))
    return common == left or common == right


def inspect_mount_policy(mountinfo: str, *, expected_external_targets: set[str],
                         expected_tmpfs: dict[str, bool]) -> dict[str, Any]:
    """Fail closed on mounts that overlap host credential locations or exceed the allowlist."""
    targets = []
    malformed = False
    for line in mountinfo.splitlines():
        fields = line.split(' - ', 1)
        if len(fields) != 2:
            malformed = True
            continue
        left, right = fields[0].split(), fields[1].split()
        if len(left) < 5 or len(right) < 2:
            malformed = True
            continue
        targets.append((_mount_path(left[4]), right[0], set(left[5].split(','))))
    protected = sorted({target for target, _, _ in targets
                        if any(_paths_overlap(target, sensitive) for sensitive in _PROTECTED_HOST_PATHS)})
    allowed = _STANDARD_MOUNT_TARGETS | expected_external_targets
    unexpected = sorted({target for target, _, _ in targets} - allowed)
    tmpfs_mounts = {}
    for target, filesystem, options in targets:
        if target in expected_tmpfs:
            tmpfs_mounts.setdefault(target, []).append(
                filesystem == 'tmpfs' and
                (('ro' in options) is expected_tmpfs[target]) and
                (('rw' in options) is not expected_tmpfs[target]))
    tmpfs_match = all(tmpfs_mounts.get(target) == [True] for target in expected_tmpfs)
    return {'mountinfo_valid': bool(targets) and not malformed,
            'protected_overlaps': protected,
            'unexpected_external_mounts': unexpected,
            'exact_external_allowlist': not malformed and not unexpected,
            'tmpfs_mounts_match': tmpfs_match}


def daemon_mounts_match(actual: dict[str, Any] | None, expected_binds: list[dict[str, Any]],
                        expected_tmpfs: dict[str, str]) -> bool:
    if actual is None:
        return False
    binds = actual.get('binds')
    tmpfs = actual.get('tmpfs')
    if not isinstance(binds, list) or not isinstance(tmpfs, dict):
        return False
    normalized_binds = [{key: mount.get(key) for key in ('Type', 'Source', 'Destination', 'RW')}
                        for mount in binds]
    return (sorted(normalized_binds, key=lambda mount: mount['Destination']) ==
            sorted(expected_binds, key=lambda mount: mount['Destination']) and
            tmpfs == expected_tmpfs)


def network_isolation_observed(result: dict[str, Any]) -> bool:
    interfaces = result.get('interfaces')
    if (not isinstance(interfaces, list) or 'lo' not in interfaces or
            result.get('active_non_loopback_interfaces') != [] or result.get('non_loopback_routes') != [] or
            result.get('route_observation_complete') is not True):
        return False
    denied = {'blocked:ENETUNREACH', 'blocked:EHOSTUNREACH'}
    if any(result.get(name) not in denied for name in
           ('egress_tcp', 'metadata_tcp', 'egress_udp', 'metadata_udp')):
        return False
    return result.get('dns') == 'temporary-name-resolution-failure'


class DockerCommandTimeout(subprocess.TimeoutExpired):
    """A timed-out docker run stopped by container ID, with daemon-observed terminal state."""

    def __init__(self, command, timeout, *, output, stderr, container_id, container_state, kill_exit_code,
                 captured_output_bytes=0, output_truncated=False):
        super().__init__(command, timeout, output=output, stderr=stderr)
        self.container_id = container_id
        self.container_state = container_state
        self.kill_exit_code = kill_exit_code
        self.captured_output_bytes = captured_output_bytes
        self.output_truncated = output_truncated


@dataclasses.dataclass(frozen=True)
class GradingRecord:
    check_id: str
    name: str
    status: str
    reason_code: str
    evidence_path: pathlib.Path


def run_b1_b10(
    execute: Callable[[str, str, pathlib.Path], dict[str, Any]], evidence_root: pathlib.Path,
) -> list[GradingRecord]:
    evidence_root = evidence_root.resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    records = []
    for check_id, name in B1_B10.items():
        check_dir = evidence_root / check_id
        check_dir.mkdir()
        try:
            observed = execute(check_id, name, check_dir)
            status = observed.get('status')
            if status not in {'pass', 'fail', 'not-run'}:
                status, reason_code = 'not-run', 'INVALID_PROBE_OUTCOME'
            else:
                reason_code = observed.get('reason_code') or 'UNSPECIFIED_PROBE_OUTCOME'
        except Exception as exc:
            observed = {'stdout': '', 'stderr': '', 'details': {'exception': type(exc).__name__}}
            status, reason_code = 'not-run', 'PROBE_EXCEPTION'
        evidence = {
            'check_id': check_id, 'name': name, 'status': status, 'reason_code': reason_code,
            'stdout': observed.get('stdout', ''), 'stderr': observed.get('stderr', ''),
            'details': observed.get('details', {}),
        }
        evidence_path = check_dir / 'probe.json'
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        records.append(GradingRecord(check_id, name, status, reason_code, evidence_path))
    return records


class DockerGradingProbeTarget(DockerProbeTarget):
    """Measure grading-relevant target controls without enabling a grading backend."""

    def __init__(self, workload_image: str, *, docker: str = 'docker', timeout_seconds: int = 20,
                 target_id: str = 'showcase-docker-grading-target'):
        super().__init__(workload_image, docker=docker, timeout_seconds=timeout_seconds)
        self.target_id = target_id

    def _run(self, args: list[str], *, timeout: int | None = None, env: dict[str, str] | None = None):
        command_args = list(args)
        container_name = None
        remove_after_success = False
        if command_args and command_args[0] == 'run':
            container_name = next((value.split('=', 1)[1] for value in command_args[1:]
                                   if value.startswith('--name=')), None)
            if container_name is None and '--name' in command_args:
                name_index = command_args.index('--name')
                if name_index + 1 < len(command_args):
                    container_name = command_args[name_index + 1]
            if container_name is None:
                container_name = f'showcase-probe-{uuid.uuid4().hex}'
                command_args.insert(1, f'--name={container_name}')
                remove_after_success = True
            command_args = [value for value in command_args if value != '--rm']
        command = [self.docker, *command_args]
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                start_new_session=True)
        captured = {'stdout': bytearray(), 'stderr': bytearray(), 'bytes': 0, 'truncated': False}
        selector = selectors.DefaultSelector()
        for name, stream in (('stdout', proc.stdout), ('stderr', proc.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        timed_out = False
        deadline = time.monotonic() + (timeout or self.timeout_seconds)
        drain_deadline = None
        exited_deadline = None
        while proc.poll() is None or selector.get_map():
            now = time.monotonic()
            if proc.poll() is None and now >= deadline:
                timed_out = True
                self._kill_process_group(proc)
                proc.wait()
                drain_deadline = time.monotonic() + 0.25
            elif proc.poll() is not None and exited_deadline is None:
                exited_deadline = now + 0.25
            cutoff = drain_deadline if timed_out else exited_deadline
            if cutoff is not None and now >= cutoff:
                captured['truncated'] = True
                break
            wait_for = min(0.05, max(0.0, (cutoff if cutoff is not None else deadline) - now))
            for key, _ in selector.select(wait_for):
                stream, name = key.fileobj, key.data
                try:
                    chunk = os.read(stream.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    continue
                remaining = MAX_CAPTURED_OUTPUT_BYTES - captured['bytes']
                kept = chunk[:max(remaining, 0)]
                captured[name].extend(kept)
                captured['bytes'] += len(kept)
                captured['truncated'] |= len(kept) < len(chunk)
        returncode = proc.poll()
        if returncode is None:
            self._kill_process_group(proc)
            returncode = proc.wait()
        for key in list(selector.get_map().values()):
            selector.unregister(key.fileobj)
        selector.close()
        proc.stdout.close()
        proc.stderr.close()

        stdout = bytes(captured['stdout']).decode(errors='replace')
        stderr = bytes(captured['stderr']).decode(errors='replace')
        if timed_out:
            container_id, container_state, kill_exit_code = (None, None, None)
            if container_name is not None:
                container_id, container_state, kill_exit_code = self._stop_container_by_id(container_name)
            raise DockerCommandTimeout(command, timeout or self.timeout_seconds, output=stdout,
                                       stderr=stderr, container_id=container_id,
                                       container_state=container_state, kill_exit_code=kill_exit_code,
                                       captured_output_bytes=captured['bytes'],
                                       output_truncated=captured['truncated'])
        result = subprocess.CompletedProcess(command, returncode, stdout, stderr)
        result.captured_output_bytes = captured['bytes']
        result.output_truncated = captured['truncated']
        if remove_after_success and container_name:
            self._remove_container(container_name)
        return result

    @staticmethod
    def _kill_process_group(proc) -> None:
        pid = getattr(proc, 'pid', None)
        if pid is None:
            proc.kill()
            return
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _control(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run([self.docker, *args], capture_output=True, text=True,
                              check=False, timeout=10)

    def _stop_container_by_id(self, name: str):
        lookup = self._control(['inspect', '--format={{.Id}}', name])
        if lookup.returncode != 0 or not lookup.stdout.strip():
            return None, None, None
        container_id = lookup.stdout.strip()
        killed = self._control(['kill', container_id])
        observed = self._control([
            'inspect', '--format={{.Id}}|{{.State.Status}}|{{.State.ExitCode}}', container_id,
        ])
        state = None
        if observed.returncode == 0:
            fields = observed.stdout.strip().split('|')
            if len(fields) == 3 and fields[0] == container_id:
                try:
                    state = {'status': fields[1], 'exit_code': int(fields[2])}
                except ValueError:
                    state = None
        self._control(['rm', container_id])
        return container_id, state, killed.returncode

    def _remove_container(self, name: str) -> None:
        lookup = self._control(['inspect', '--format={{.Id}}', name])
        if lookup.returncode == 0 and lookup.stdout.strip():
            self._control(['rm', lookup.stdout.strip()])

    def _container_state_by_name(self, name: str) -> dict[str, Any] | None:
        lookup = self._control(['inspect', '--format={{.Id}}', name])
        if lookup.returncode != 0 or not lookup.stdout.strip():
            return None
        container_id = lookup.stdout.strip()
        observed = self._control([
            'inspect', '--format={{.Id}}|{{.State.Status}}|{{.State.ExitCode}}|{{.State.OOMKilled}}',
            container_id,
        ])
        if observed.returncode != 0:
            return None
        fields = observed.stdout.strip().split('|')
        if len(fields) != 4 or fields[0] != container_id or fields[3] not in {'true', 'false'}:
            return None
        try:
            exit_code = int(fields[2])
        except ValueError:
            return None
        return {'container_id': container_id, 'status': fields[1], 'exit_code': exit_code,
                'oom_killed': fields[3] == 'true'}

    def _container_mounts_by_name(self, name: str) -> dict[str, Any] | None:
        result = self._control(['inspect', '--format={{json .Mounts}}', name])
        if result.returncode != 0:
            return None
        try:
            mounts = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(mounts, (list, dict)):
            return None
        tmpfs_result = self._control(['inspect', '--format={{json .HostConfig.Tmpfs}}', name])
        if tmpfs_result.returncode != 0:
            return None
        try:
            tmpfs = json.loads(tmpfs_result.stdout) or {}
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(tmpfs, dict) or any(not isinstance(path, str) or not isinstance(options, str)
                                              for path, options in tmpfs.items()):
            return None
        if isinstance(mounts, dict):
            if set(mounts) != set(tmpfs):
                return None
            mounts = []
        if any(not isinstance(mount, dict) or mount.get('Type') not in {'bind', 'tmpfs'}
               for mount in mounts):
            return None
        return {'binds': [{key: mount.get(key) for key in ('Type', 'Source', 'Destination', 'RW')}
                          for mount in mounts if mount.get('Type') == 'bind'],
                'tmpfs': tmpfs}

    def _remove_container_by_id(self, container_id: str) -> bool:
        removed = self._control(['rm', container_id])
        return removed.returncode == 0

    @staticmethod
    def _cgroup_limit_observation(stdout: str, exit_code: int) -> dict[str, Any]:
        expected = {
            'cpu.max': '100000 100000', 'memory.max': '536870912',
            'memory.swap.max': '0', 'pids.max': '64',
        }
        try:
            observed = json.loads(stdout)
        except (TypeError, json.JSONDecodeError):
            observed = None
        return {'expected': expected, 'observed': observed,
                'exact_match': exit_code == 0 and observed == expected}

    @staticmethod
    def _memory_oom_observed(state: dict[str, Any] | None) -> bool:
        return isinstance(state, dict) and state.get('oom_killed') is True

    def _run_python(self, code: str, *, name: str | None = None, mounts: tuple[str, ...] = (),
                    timeout: int | None = None, argv_extra: tuple[str, ...] = (),
                    docker_env: dict[str, str] | None = None):
        docker_args = ['run', '--rm']
        if name:
            docker_args += ['--name', name]
        docker_args += [*self._base_args()]
        for mount in mounts:
            docker_args += ['--mount', mount]
        docker_args += [self.workload_image, *self._python(code), *argv_extra]
        return self._run(docker_args, timeout=timeout, env=docker_env)

    def _disk_limit_probe(self) -> dict[str, Any]:
        code = (
            "import errno,json; chunk=b'x'*1048576; written={'workspace':0,'tmp':0}; error=None\n"
            "try:\n"
            " with open('/workspace/workspace-probe','wb',buffering=0) as f:\n"
            "  for _ in range(240): written['workspace']+=f.write(chunk)\n"
            " with open('/tmp/tmp-probe','wb',buffering=0) as f:\n"
            "  for _ in range(16): written['tmp']+=f.write(chunk)\n"
            " with open('/tmp/tmp-probe','ab',buffering=0) as f: f.write(b'x')\n"
            "except OSError as exc:\n error='ENOSPC' if exc.errno==errno.ENOSPC else type(exc).__name__\n"
            "dev_shm_blocked=False\n"
            "try:\n with open('/dev/shm/shm-probe','wb',buffering=0) as f: f.write(b'x')\n"
            "except OSError:\n dev_shm_blocked=True\n"
            "total=sum(written.values()); observed=error=='ENOSPC' and total==268435456 and dev_shm_blocked\n"
            "print(json.dumps({'workspace_bytes':written['workspace'],'tmp_bytes':written['tmp'],'total_bytes':total,'limit_observed':observed,'overflow_errno':error,'dev_shm_write_blocked':dev_shm_blocked}))\n"
            "raise SystemExit(not observed)\n"
        )
        proc = self._run([
            'run', '--rm', *self._base_args(),
            f'--tmpfs=/workspace:rw,noexec,nosuid,size={WORKSPACE_QUOTA_BYTES},mode=1777',
            self.workload_image, *self._python(code),
        ], timeout=30)
        try:
            observed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            observed = {}
        return {
            'exit_code': proc.returncode,
            **observed,
            'limit_bytes': COMBINED_DISK_QUOTA_BYTES,
            'limit_observed': proc.returncode == 0 and observed.get('limit_observed') is True,
            'filesystem': {'workspace': 'tmpfs', 'tmp': 'tmpfs'},
            'stderr_tail': proc.stderr[-300:],
        }

    def _b10_candidate_request(self, probe_code: str, timeout_seconds: int) -> dict[str, Any]:
        limits = {
            'cpus': 1, 'memory_bytes': 536_870_912, 'pids': 64,
            'disk_bytes': COMBINED_DISK_QUOTA_BYTES, 'output_bytes': MAX_CAPTURED_OUTPUT_BYTES,
        }
        code_digest = hashlib.sha256(probe_code.encode()).hexdigest()
        config = json.dumps({'image': self.workload_image, 'limits': limits,
                             'timeout_seconds': timeout_seconds}, sort_keys=True, separators=(',', ':'))
        config_digest = hashlib.sha256(config.encode()).hexdigest()
        nonce = uuid.uuid4().hex
        return {
            'contract_version': 2,
            'request_id': f'b10-{nonce}',
            'trial_id': f'b10-trial-{nonce}',
            'task_digest': f'sha256:{code_digest}',
            'config_digest': f'sha256:{config_digest}',
            'provider': 'showcase-docker-grading-probe',
            'model': 'pinned-workload-image',
            'settings': {}, 'capabilities': [], 'timeout_seconds': timeout_seconds,
            'max_attempts': 1,
            'input_bindings': [{'name': 'grading-probe', 'sha256': f'sha256:{code_digest}'}],
            'limits': limits,
        }

    @staticmethod
    def _utc_now() -> str:
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')

    def _b10_result(self, contract, request: dict[str, Any], *, outcome: str, started_at: str,
                    ended_at: str, exit_code: int | None, drain: str, target: dict[str, Any] | None,
                    fired: str | None, output_truncated: bool, execution_id: str,
                    attempt_id: str) -> dict[str, Any]:
        known = outcome != 'unknown'
        result = {
            'contract_version': 2, 'request_id': request['request_id'],
            'request_digest': contract.request_digest(request), 'execution_id': execution_id,
            'outcome': outcome, 'exit_code': exit_code,
            'completion': None,
            'error_code': ('LIMIT_EXCEEDED' if fired in {'oom', 'pids', 'disk'} else
                           'PROVIDER_ERROR' if outcome == 'error' else None),
            'drain': drain, 'cancel_requested': False,
            'isolation_level': 'unqualified', 'resolved_model': None,
            'versions': {'backend': 'docker-probe'},
            'started_at': started_at, 'ended_at': ended_at,
            'attempts': [{'attempt_id': attempt_id, 'outcome': outcome,
                          'started_at': started_at, 'ended_at': ended_at}],
            'candidate': None, 'artifacts': [], 'usage_events': [],
            'usage_completeness': 'unknown', 'target': target,
            'limits': ({
                'applied': {**request['limits'], 'timeout_seconds': request['timeout_seconds']},
                'fired': fired, 'output_truncated': output_truncated,
            } if known else None),
        }
        if target is not None:
            contract.validate_target(target, qualified=False)
        contract.validate_result(result, request)
        return result

    def execute_b1_b10(self, check_id: str, name: str, evidence_dir: pathlib.Path) -> dict[str, Any]:
        if check_id == 'B1':
            with tempfile.TemporaryDirectory(prefix='hidden-bundle-') as bundle:
                canary = f'hidden-canary-{uuid.uuid4().hex}'
                (pathlib.Path(bundle) / canary).write_text(canary, encoding='ascii')
                code = (
                    "import os,sys; token=sys.argv[1]; found=[]\n"
                    "forbidden={'/proc','/sys','/dev'}\n"
                    "for root,dirs,files in os.walk('/',topdown=True,onerror=lambda _:None):\n"
                    " dirs[:]=[d for d in dirs if os.path.join(root,d) not in forbidden]\n"
                    " for n in files+dirs:\n"
                    "  p=os.path.join(root,n)\n"
                    "  if token in n: found.append(p)\n"
                    "  try:\n   if os.path.isfile(p) and os.path.getsize(p)<=1048576 and token in open(p,errors='ignore').read(): found.append(p)\n"
                    "  except (OSError,PermissionError): pass\n"
                    "visible=bool(found) or any(token in v for v in os.environ.values())\n"
                    "print('hidden-canary-visible' if visible else 'hidden-canary-absent')\n"
                    "raise SystemExit(visible)\n"
                )
                proc = self._run_python(code, argv_extra=(canary,))
            passed = proc.returncode == 0 and 'hidden-canary-absent' in proc.stdout
            return self._observed(proc, passed, 'HIDDEN_MATERIAL_ABSENT',
                                  {'hidden_bundle_created_outside_target': True, 'host_bundle_mounted': False})

        if check_id == 'B2':
            with tempfile.TemporaryDirectory(prefix='hidden-expected-') as secret_root:
                secret = pathlib.Path(secret_root) / f'expected-{uuid.uuid4().hex}.txt'
                secret.write_text(uuid.uuid4().hex, encoding='ascii')
                hidden_path = f'/host-hidden/{secret.name}'
                code = (
                    "import pathlib,sys; p=pathlib.Path(sys.argv[1]); visible=p.exists(); "
                    "print('expected-visible' if visible else 'expected-unreadable'); raise SystemExit(visible)"
                )
                proc = self._run_python(code, argv_extra=(hidden_path,), mounts=(), timeout=self.timeout_seconds)
                passed = proc.returncode == 0 and 'expected-unreadable' in proc.stdout
                return self._observed(proc, passed, 'HIDDEN_EXPECTED_VALUE_UNREADABLE',
                                      {'host_expected_value_created': True, 'host_path_was_mounted': False,
                                       'tested_container_path': hidden_path})

        if check_id == 'B3':
            marker = uuid.uuid4().hex
            first = self._run_python(f"from pathlib import Path; Path('/tmp/marker').write_text({marker!r})")
            second = self._run_python("from pathlib import Path; print('marker-absent' if not Path('/tmp/marker').exists() else 'marker-present')")
            passed = first.returncode == 0 and second.returncode == 0 and 'marker-absent' in second.stdout
            return {
                'status': 'pass' if passed else 'fail', 'reason_code': 'SANDBOX_STATE_NOT_PERSISTED' if passed else 'SANDBOX_STATE_PERSISTED',
                'stdout': first.stdout + second.stdout, 'stderr': first.stderr + second.stderr,
                'details': {'first_exit': first.returncode, 'second_exit': second.returncode,
                            'second_sandbox_marker_absent': 'marker-absent' in second.stdout},
            }

        if check_id == 'B4':
            with tempfile.TemporaryDirectory(prefix='grading-mounts-') as mounts_root:
                root = pathlib.Path(mounts_root)
                workspace, inputs = root / 'workspace', root / 'inputs'
                workspace.mkdir(mode=0o777); inputs.mkdir(mode=0o777)
                workspace.chmod(0o777)
                (inputs / 'fixture.txt').write_text('read-only input', encoding='ascii')
                workspace_mount = f'type=bind,src={workspace.resolve()},dst=/workspace'
                inputs_mount = f'type=bind,src={inputs.resolve()},dst=/inputs,readonly'
                container_name = f'showcase-b4-{uuid.uuid4().hex[:12]}'
                code = (
                    "import json,os,pathlib\n"
                    "def can_write(p):\n try:\n  pathlib.Path(p).write_text('probe'); return True\n except OSError: return False\n"
                    "data={'uid':os.getuid(),'workspace_rw':can_write('/workspace/probe'),"
                    "'input_ro':not can_write('/inputs/fixture.txt'),'root_ro':not can_write('/qual-probe'),"
                    "'tmpfs': any(line.split(' - ')[-1].split()[0]=='tmpfs' and line.split()[4]=='/tmp' for line in pathlib.Path('/proc/self/mountinfo').read_text().splitlines()),"
                    "'mountinfo':pathlib.Path('/proc/self/mountinfo').read_text()}; print(json.dumps(data)); "
                    "raise SystemExit(not(data['uid']!=0 and data['workspace_rw'] and data['input_ro'] and data['root_ro'] and data['tmpfs']))"
                )
                try:
                    proc = self._run_python(code, name=container_name, mounts=(workspace_mount, inputs_mount))
                    actual_mounts = self._container_mounts_by_name(container_name)
                finally:
                    self._remove_container(container_name)
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                data = {}
            mountinfo = data.pop('mountinfo', '')
            mount_policy = inspect_mount_policy(
                mountinfo, expected_external_targets={'/workspace', '/inputs'},
                expected_tmpfs={'/tmp': False, '/dev/shm': True})
            data.update(mount_policy)
            expected_mounts = [
                {'Type': 'bind', 'Source': str(workspace.resolve()), 'Destination': '/workspace', 'RW': True},
                {'Type': 'bind', 'Source': str(inputs.resolve()), 'Destination': '/inputs', 'RW': False},
            ]
            data['daemon_mounts_match'] = daemon_mounts_match(
                actual_mounts, expected_mounts,
                {'/tmp': 'rw,noexec,nosuid,size=16m',
                 '/dev/shm': 'ro,noexec,nosuid,size=1m'})
            passed = (proc.returncode == 0 and data.get('uid', 0) != 0 and data.get('workspace_rw') and
                      data.get('input_ro') and data.get('root_ro') and data.get('tmpfs') and
                      data.get('mountinfo_valid') and data.get('exact_external_allowlist') and
                      data.get('tmpfs_mounts_match') and not data.get('protected_overlaps') and
                      data.get('daemon_mounts_match'))
            sanitized = subprocess.CompletedProcess(proc.args, proc.returncode, json.dumps(data), proc.stderr)
            return self._observed(sanitized, passed, 'FILESYSTEM_BOUNDARIES_OBSERVED', data)

        if check_id == 'B5':
            code = (
                "import errno,json,pathlib,socket; result={}; "
                "interfaces=sorted(name for _,name in socket.if_nameindex()); result['interfaces']=interfaces\n"
                "try:\n"
                " flags={name:int(pathlib.Path('/sys/class/net',name,'flags').read_text().strip(),16) for name in interfaces}\n"
                " result['active_non_loopback_interfaces']=sorted(name for name,value in flags.items() if name!='lo' and value & 1)\n"
                " routes=[line.split()[0] for line in pathlib.Path('/proc/net/route').read_text().splitlines()[1:] if len(line.split())>=8 and line.split()[0]!='lo']\n"
                " routes += [line.split()[-1] for line in pathlib.Path('/proc/net/ipv6_route').read_text().splitlines() if line.split() and line.split()[-1]!='lo']\n"
                " result['non_loopback_routes']=sorted(set(routes)); result['route_observation_complete']=True\n"
                "except (OSError,ValueError):\n result['active_non_loopback_interfaces']=None; result['non_loopback_routes']=None; result['route_observation_complete']=False\n"
                "forbidden=[('egress_tcp','1.1.1.1',443),('metadata_tcp','169.254.169.254',80)]; "
                "\nfor name,host,port in forbidden:\n try:\n  s=socket.create_connection((host,port),timeout=.5); s.close(); result[name]='connected'\n except OSError as e: result[name]='blocked:'+errno.errorcode.get(e.errno,'UNKNOWN')\n"
                "for name,host in [('egress_udp','1.1.1.1'),('metadata_udp','169.254.169.254')]:\n"
                " try:\n  s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.sendto(b'probe',(host,53)); result[name]='send-accepted'\n"
                " except OSError as e: result[name]='blocked:'+errno.errorcode.get(e.errno,'UNKNOWN')\n"
                " try: s.close()\n except UnboundLocalError: pass\n"
                "try: socket.getaddrinfo('example.com',443); result['dns']='resolved'\nexcept socket.gaierror as e: result['dns']='temporary-name-resolution-failure' if e.errno==socket.EAI_AGAIN else 'dns-error:'+str(e.errno)\nexcept OSError as e: result['dns']='dns-os-error:'+str(e.errno)\n"
                "print(json.dumps(result)); raise SystemExit(0 if result['route_observation_complete'] and 'lo' in interfaces and not result['active_non_loopback_interfaces'] and not result['non_loopback_routes'] and result['egress_tcp'] in ('blocked:ENETUNREACH','blocked:EHOSTUNREACH') and result['metadata_tcp'] in ('blocked:ENETUNREACH','blocked:EHOSTUNREACH') and result['egress_udp'] in ('blocked:ENETUNREACH','blocked:EHOSTUNREACH') and result['metadata_udp'] in ('blocked:ENETUNREACH','blocked:EHOSTUNREACH') and result['dns']=='temporary-name-resolution-failure' else 1)"
            )
            proc = self._run_python(code)
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                data = {}
            passed = proc.returncode == 0 and network_isolation_observed(data)
            return self._observed(proc, passed, 'EGRESS_DNS_METADATA_BLOCKED', data)

        if check_id == 'B6':
            canary = f'host-env-canary-{uuid.uuid4().hex}'
            with tempfile.TemporaryDirectory(prefix='host-credentials-') as host_creds:
                (pathlib.Path(host_creds) / 'canary').write_text(canary, encoding='ascii')
                hidden_file_path = '/host-credentials/canary'
                container_name = f'showcase-b6-{uuid.uuid4().hex[:12]}'
                code = (
                    "import json,os,pathlib,sys; names=('SHOWCASE_HOST_CANARY','AWS_ACCESS_KEY_ID','ANTHROPIC_API_KEY'); "
                    "env={n:(n in os.environ) for n in names}; "
                    "file_canary=pathlib.Path(sys.argv[-2]).exists(); value_canary=sys.argv[-1] in os.environ.values(); "
                    "data={'inherited_env':env,'host_file_canary_absent':not file_canary,'host_env_canary_absent':not value_canary,'mountinfo':pathlib.Path('/proc/self/mountinfo').read_text()}; "
                    "print(json.dumps(data)); raise SystemExit(any(env.values()) or file_canary or value_canary)"
                )
                try:
                    proc = self._run_python(code, name=container_name,
                                            argv_extra=(hidden_file_path, canary),
                                            docker_env={**os.environ, 'SHOWCASE_HOST_CANARY': canary})
                    actual_mounts = self._container_mounts_by_name(container_name)
                finally:
                    self._remove_container(container_name)
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                data = {}
            mount_policy = inspect_mount_policy(
                data.pop('mountinfo', ''), expected_external_targets=set(),
                expected_tmpfs={'/tmp': False, '/dev/shm': True})
            data.update(mount_policy)
            data['daemon_mounts_match'] = daemon_mounts_match(
                actual_mounts, [],
                {'/tmp': 'rw,noexec,nosuid,size=16m',
                 '/dev/shm': 'ro,noexec,nosuid,size=1m'})
            passed = (proc.returncode == 0 and data and data.get('mountinfo_valid') and
                      not any(data.get('inherited_env', {}).values()) and
                      data.get('exact_external_allowlist') and not data.get('protected_overlaps') and
                      data.get('tmpfs_mounts_match') and
                      data.get('host_file_canary_absent') and data.get('host_env_canary_absent') and
                      data.get('daemon_mounts_match'))
            sanitized = subprocess.CompletedProcess(proc.args, proc.returncode, json.dumps(data), proc.stderr)
            return self._observed(sanitized, passed, 'HOST_ENV_AND_CREDENTIALS_ABSENT', data)

        if check_id == 'B7':
            code = (
                "import ctypes,json,os,pathlib,socket\n"
                "text=pathlib.Path('/proc/self/status').read_text()\n"
                "get=lambda n: next((x.split()[1] for x in text.splitlines() if x.startswith(n+':')),None)\n"
                "status={'uid':os.getuid(),'capeff':get('CapEff'),'no_new_privs':get('NoNewPrivs'),'seccomp':get('Seccomp')}\n"
                "try:\n socket.socket(socket.AF_INET,socket.SOCK_RAW,socket.IPPROTO_ICMP); status['raw_socket']='allowed'\n"
                "except OSError:\n status['raw_socket']='blocked'\n"
                "try:\n os.setuid(0); status['setuid']='allowed'\n"
                "except OSError:\n status['setuid']='blocked'\n"
                "libc=ctypes.CDLL(None,use_errno=True)\n"
                "rc=libc.mount(b'none',b'/tmp',b'tmpfs',0,None)\n"
                "status['mount']='allowed' if rc==0 else 'blocked'\n"
                "print(json.dumps(status))\n"
                "raise SystemExit(status['uid']==0 or status['capeff']!='0000000000000000' or status['no_new_privs']!='1' or status['seccomp']!='2' or status['raw_socket']!='blocked' or status['setuid']!='blocked' or status['mount']!='blocked')\n"
            )
            proc = self._run_python(code)
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                data = {}
            passed = proc.returncode == 0 and data
            return self._observed(proc, passed, 'PRIVILEGES_RESTRICTED', data)

        if check_id == 'B8':
            # Exercise each limit independently; configuration alone cannot pass this check.
            checks: dict[str, Any] = {}
            limits = self._run_python(
                "import pathlib,json; p=pathlib.Path('/sys/fs/cgroup'); "
                "print(json.dumps({n:(p/n).read_text().strip() if (p/n).exists() else None for n in ('cpu.max','memory.max','memory.swap.max','pids.max')}))"
            )
            checks['configured'] = self._cgroup_limit_observation(limits.stdout, limits.returncode)
            wall_name = f'showcase-b8-wall-{uuid.uuid4().hex[:10]}'
            try:
                self._run_python('import time; time.sleep(5)', name=wall_name, timeout=2)
                checks['wall'] = {'status': 'not-killed-by-deadline'}
            except DockerCommandTimeout as exc:
                checks['wall'] = {
                    'status': 'container-timeout', 'container_id': exc.container_id,
                    'container_state': exc.container_state, 'kill_exit_code': exc.kill_exit_code,
                    'container_stopped_by_id': bool(
                        exc.container_id and exc.kill_exit_code == 0 and
                        exc.container_state == {'status': 'exited', 'exit_code': 137}
                    ),
                }
            except subprocess.TimeoutExpired:
                checks['wall'] = {'status': 'timeout-without-container-state',
                                  'container_stopped_by_id': False}

            memory_name = f'showcase-b8-memory-{uuid.uuid4().hex[:10]}'
            memory = None
            try:
                memory = self._run_python(
                    "x=bytearray(600*1024*1024); print('memory-allocation-completed')",
                    name=memory_name, timeout=15,
                )
            except DockerCommandTimeout as exc:
                checks['memory_timeout'] = {
                    'container_id': exc.container_id,
                    'container_state': exc.container_state,
                    'kill_exit_code': exc.kill_exit_code,
                }
            memory_state = self._container_state_by_name(memory_name)
            if memory_state and memory_state.get('container_id'):
                self._remove_container_by_id(memory_state['container_id'])
            checks['memory'] = {
                'exit_code': memory.returncode if memory is not None else None,
                'container_state': memory_state,
                'limit_observed': self._memory_oom_observed(memory_state),
                'stderr_tail': memory.stderr[-300:] if memory is not None else '',
            }
            pids = self._run_python(
                "import errno,json,subprocess,sys; ps=[]\n"
                "try:\n\n while True: ps.append(subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)']))\n"
                "except OSError as e:\n print(json.dumps({'pid_limit_observed':e.errno==errno.EAGAIN,'children':len(ps),'errno':e.errno}))\n"
                "finally:\n\n [p.kill() for p in ps]\n [p.wait() for p in ps]\n",
                timeout=15,
            )
            try:
                pids_observed = json.loads(pids.stdout)
            except json.JSONDecodeError:
                pids_observed = {}
            checks['pids'] = {'exit_code': pids.returncode, 'evidence': pids_observed,
                              'limit_named': pids_observed.get('pid_limit_observed') is True}

            checks['disk'] = self._disk_limit_probe()

            output = self._run_python("print('X'*1100000)", timeout=15)
            output_bytes = output.captured_output_bytes
            checks['output'] = {
                'captured_bytes': output_bytes,
                'required_max_bytes': MAX_CAPTURED_OUTPUT_BYTES,
                'truncated': output.output_truncated,
                'limit_observed': output.output_truncated and output_bytes == MAX_CAPTURED_OUTPUT_BYTES,
            }
            passed = (checks['configured']['exact_match'] and checks['wall']['container_stopped_by_id'] and
                      checks['memory']['limit_observed'] and checks['pids']['limit_named'] and
                      checks['disk']['limit_observed'] and checks['output']['limit_observed'])
            reason = 'ALL_RESOURCE_LIMITS_ENFORCED' if passed else 'RESOURCE_LIMIT_COUNTEREXAMPLE'
            return {'status': 'pass' if passed else 'fail', 'reason_code': reason,
                    'stdout': json.dumps({k: v for k, v in checks.items() if k != 'output'}, sort_keys=True) +
                              f"\noutput_captured_bytes={output_bytes}\n",
                    'stderr': output.stderr[-500:], 'details': checks}

        if check_id == 'B9':
            with tempfile.TemporaryDirectory(prefix='bounded-output-') as tmp:
                output_root = pathlib.Path(tmp); output_root.chmod(0o777)
                output = output_root / 'artifact.bin'
                mount = f'type=bind,src={output_root},dst=/output'
                code = "from pathlib import Path; Path('/output/artifact.bin').write_bytes(b'grade-result')"
                container_name = f'showcase-b9-{uuid.uuid4().hex[:12]}'
                proc = self._run_python(code, name=container_name, mounts=(mount,))
                expected_payload = b'grade-result'
                state = self._container_state_by_name(container_name)
                container_id = state.get('container_id') if state else None
                removed = self._control(['rm', container_id]) if container_id else None
                inventory = self._control([
                    'ps', '-a', '--no-trunc', '--filter', f'id={container_id}', '--format={{.ID}}',
                ]) if removed and removed.returncode == 0 else None
                gone = bool(inventory and inventory.returncode == 0 and not inventory.stdout.strip())
                # Read no mounted result until the Docker API confirms the immutable ID is absent.
                payload = b''
                artifact_read = False
                if gone:
                    try:
                        directory_fd = os.open(
                            output_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        )
                        try:
                            artifact_fd = os.open(
                                'artifact.bin', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                dir_fd=directory_fd,
                            )
                            try:
                                if stat.S_ISREG(os.fstat(artifact_fd).st_mode):
                                    chunks = []
                                    remaining = 1_048_577
                                    while remaining:
                                        chunk = os.read(artifact_fd, min(65_536, remaining))
                                        if not chunk:
                                            break
                                        chunks.append(chunk)
                                        remaining -= len(chunk)
                                    payload = b''.join(chunks)
                                    artifact_read = True
                            finally:
                                os.close(artifact_fd)
                        finally:
                            os.close(directory_fd)
                    except OSError:
                        # Missing, symlinked, or unreadable artifacts are not evidence.
                        pass
                exists = artifact_read
                artifact_matches = payload == expected_payload
                passed = (proc.returncode == 0 and exists and len(payload) <= 1_048_576 and
                          artifact_matches and gone)
                return {'status': 'pass' if passed else 'fail',
                        'reason_code': 'BOUNDED_ARTIFACT_AFTER_DESTROY' if passed else 'ARTIFACT_OR_DESTROY_BOUNDARY_FAILED',
                        'stdout': proc.stdout, 'stderr': proc.stderr,
                        'details': {'container_id': container_id, 'container_destroyed_before_result_read': gone,
                                    'remove_exit_code': removed.returncode if removed else None,
                                    'inventory_exit_code': inventory.returncode if inventory else None,
                                    'artifact_read': artifact_read,
                                    'artifact_matches_written_bytes': artifact_matches,
                                    'artifact_bytes': len(payload),
                                    'artifact_sha256': hashlib.sha256(payload).hexdigest()}}

        if check_id == 'B10':
            try:
                from agent_harness import contract
            except ImportError:
                return {'status': 'not-run', 'reason_code': 'PINNED_CONTRACT_UNAVAILABLE',
                        'stdout': '', 'stderr': '', 'details': {'contract_version': 2}}

            probe_code = (
                "import json,pathlib; p=pathlib.Path('/sys/fs/cgroup'); "
                "print(json.dumps({n:(p/n).read_text().strip() if (p/n).exists() else None "
                "for n in ('cpu.max','memory.max','memory.swap.max','pids.max')}))"
            )
            timeout_seconds = min(self.timeout_seconds, 10)
            request = self._b10_candidate_request(probe_code, timeout_seconds)
            execution_id, attempt_id = f'b10-exec-{uuid.uuid4().hex}', f'b10-attempt-{uuid.uuid4().hex}'
            started_at = self._utc_now()
            container_name = f'showcase-b10-{uuid.uuid4().hex[:12]}'
            target = {
                'id': self.target_id, 'qualification_digest': None,
                'image_digest': 'sha256:' + self.workload_image.rsplit('@sha256:', 1)[1],
            }
            code = (
                "import json,pathlib; p=pathlib.Path('/sys/fs/cgroup'); "
                "print(json.dumps({n:(p/n).read_text().strip() if (p/n).exists() else None "
                "for n in ('cpu.max','memory.max','memory.swap.max','pids.max')}))"
            )
            try:
                proc = self._run([
                    'run', f'--name={container_name}', *self._base_args(),
                    f'--tmpfs=/workspace:rw,noexec,nosuid,size={WORKSPACE_QUOTA_BYTES},mode=1777',
                    self.workload_image, *self._python(code),
                ], timeout=timeout_seconds)
            except DockerCommandTimeout as exc:
                ended_at = self._utc_now()
                terminal_confirmed = bool(
                    exc.container_id and exc.kill_exit_code == 0 and
                    exc.container_state == {'status': 'exited', 'exit_code': 137}
                )
                outcome = 'timeout' if terminal_confirmed else 'unknown'
                result = self._b10_result(
                    contract, request, outcome=outcome, started_at=started_at, ended_at=ended_at,
                    exit_code=137 if terminal_confirmed else None,
                    drain='confirmed' if terminal_confirmed else 'unconfirmed',
                    target=target if terminal_confirmed else None,
                    fired='timeout' if terminal_confirmed else None,
                    output_truncated=exc.output_truncated, execution_id=execution_id,
                    attempt_id=attempt_id,
                )
                passed = False
                details = {'request': request, 'result': result, 'container_id': exc.container_id,
                           'container_state': exc.container_state, 'kill_exit_code': exc.kill_exit_code,
                           'captured_output_bytes': exc.captured_output_bytes}
                return {'status': 'fail', 'reason_code': 'B10_PROBE_TIMEOUT', 'stdout': exc.output or '',
                        'stderr': exc.stderr or '', 'details': details}

            ended_at = self._utc_now()
            state = self._container_state_by_name(container_name)
            if state and state.get('container_id'):
                self._remove_container_by_id(state['container_id'])
            configured = self._cgroup_limit_observation(proc.stdout, proc.returncode)
            terminal_known = bool(state and state.get('status') == 'exited' and
                                  type(state.get('exit_code')) is int)
            fired = 'oom' if self._memory_oom_observed(state) else None
            outcome = ('unknown' if not terminal_known else
                       'error' if fired or proc.returncode != 0 else 'completed')
            result = self._b10_result(
                contract, request, outcome=outcome, started_at=started_at, ended_at=ended_at,
                exit_code=state.get('exit_code') if terminal_known else None,
                drain='confirmed' if terminal_known else 'unconfirmed',
                target=target if terminal_known else None,
                fired=fired if terminal_known else None, output_truncated=proc.output_truncated,
                execution_id=execution_id, attempt_id=attempt_id,
            )
            passed = bool(
                terminal_known and state.get('exit_code') == proc.returncode == 0
                and configured['exact_match'] and not proc.output_truncated
                and target['image_digest'] == 'sha256:' + self.workload_image.rsplit('@sha256:', 1)[1]
            )
            return {
                'status': 'pass' if passed else 'fail',
                'reason_code': 'CANDIDATE_RESULT_VALIDATED' if passed else 'B10_OBSERVATION_MISMATCH',
                'stdout': proc.stdout, 'stderr': proc.stderr[-500:],
                'details': {'request': request, 'result': result, 'container_state': state,
                            'configured_limits': configured, 'candidate_target': target},
            }
        return {'status': 'not-run', 'reason_code': 'UNKNOWN_CHECK', 'stdout': '', 'stderr': '', 'details': {}}

    @staticmethod
    def _observed(proc, passed: bool, reason: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
        return {'status': 'pass' if passed else 'fail', 'reason_code': reason,
                'stdout': proc.stdout, 'stderr': proc.stderr,
                'details': {'exit_code': proc.returncode, **(details or {})}}
