"""Q11-Q16 disposable Docker identity and lifecycle qualification probes.

This module is qualification tooling only. Its short-lived controller processes
cannot launch submitted payloads or grant authority to the production runner.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from typing import Any


Q11_Q16 = {
    'Q11': 'EXECUTION_IDENTITY_DURABLE',
    'Q12': 'EXECUTION_IDENTITY_STALE_REJECTED',
    'Q13': 'CANCEL_EXECUTION_UNIT',
    'Q14': 'DRAIN_EXECUTION_UNIT',
    'Q15': 'RESTART_ACTIVE',
    'Q16': 'RESTART_DRAINED',
}
MAX_EVIDENCE_TEXT = 4096
VALID_STATES = {'active', 'drained'}


@dataclasses.dataclass(frozen=True)
class LifecycleProbeRecord:
    check_id: str
    name: str
    status: str
    reason_code: str
    evidence_path: pathlib.Path


def _bounded_text(value: Any) -> str:
    text = value if isinstance(value, str) else ''
    if len(text) <= MAX_EVIDENCE_TEXT:
        return text
    marker = '[truncated]'
    return text[:MAX_EVIDENCE_TEXT - len(marker)] + marker


def _safe_controller_stderr(value: str) -> str:
    """Redact controller stderr before it can enter committed qualification evidence."""
    return '[controller stderr redacted]' if value else ''


def run_q11_q16(
    execute: Callable[[str, str, pathlib.Path, str], dict[str, Any]],
    evidence_root: pathlib.Path,
    *, job_id: str,
) -> list[LifecycleProbeRecord]:
    """Run all six lifecycle probes, keeping independent job-bound artifacts."""
    if not isinstance(job_id, str) or not job_id.strip() or any(character.isspace() for character in job_id):
        raise ValueError('job_id must be a non-empty token')
    evidence_root = evidence_root.resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    records = []
    for check_id, name in Q11_Q16.items():
        check_dir = evidence_root / check_id
        check_dir.mkdir()
        try:
            observed = execute(check_id, name, check_dir, job_id)
            status = observed.get('status')
            if status not in {'pass', 'fail', 'not-run'}:
                status, reason_code = 'not-run', 'INVALID_PROBE_OUTCOME'
            else:
                reason_code = observed.get('reason_code') or 'UNSPECIFIED_PROBE_OUTCOME'
            stdout = _bounded_text(observed.get('stdout', ''))
            stderr = _bounded_text(_safe_controller_stderr(observed.get('stderr', '')))
            details = observed.get('details', {})
            if not isinstance(details, dict):
                details = {'details_omitted': True}
        except Exception as exc:  # Do not put exception messages or secret-bearing values in evidence.
            status, reason_code = 'not-run', 'PROBE_EXCEPTION'
            stdout, stderr = '', ''
            details = {'exception_type': type(exc).__name__[:128]}

        evidence = {
            'check_id': check_id,
            'name': name,
            'job_id': job_id,
            'status': status,
            'reason_code': str(reason_code)[:128],
            'stdout': stdout,
            'stderr': stderr,
            'details': details,
        }
        evidence_path = check_dir / 'probe.json'
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        records.append(LifecycleProbeRecord(check_id, name, status, str(reason_code), evidence_path))
    return records


def write_identity(path: pathlib.Path, identity: dict[str, Any]) -> None:
    """Atomically persist and fsync the probe identity and its parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.identity-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(identity, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_identity(path: pathlib.Path) -> dict[str, Any]:
    identity = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(identity, dict) or identity.get('schema_version') != 1 or
            not isinstance(identity.get('job_id'), str) or
            not isinstance(identity.get('execution_id'), str) or
            type(identity.get('generation')) is not int or identity['generation'] < 1 or
            not isinstance(identity.get('container_id'), str) or
            not isinstance(identity.get('container_name'), str) or
            not isinstance(identity.get('image_digest'), str) or
            not re.fullmatch(r'sha256:[0-9a-f]{64}', identity['image_digest']) or
            identity.get('state') not in VALID_STATES):
        raise ValueError('identity record is malformed')
    if identity['state'] == 'drained':
        terminal = identity.get('terminal')
        if (not isinstance(terminal, dict) or terminal.get('status') != 'exited' or
                terminal.get('pid') != 0 or terminal.get('process_table_empty') is not True or
                type(terminal.get('recorded_by_controller_pid')) is not int):
            raise ValueError('drained identity is missing terminal drainage proof')
    return identity


def identity_matches(identity: dict[str, Any], *, job_id: str, execution_id: str,
                     generation: int) -> bool:
    return (identity.get('job_id') == job_id and identity.get('execution_id') == execution_id and
            type(generation) is int and identity.get('generation') == generation)


def _docker(args: list[str], *, docker: str, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run([docker, *args], capture_output=True, text=True,
                          timeout=timeout_seconds, check=False)


def _inspect(identity: dict[str, Any], *, docker: str, timeout_seconds: int) -> tuple[str, int, int]:
    result = _docker(['inspect', '--format', '{{.State.Status}} {{.State.Pid}}', identity['container_id']],
                     docker=docker, timeout_seconds=timeout_seconds)
    if result.returncode != 0:
        return '', -1, result.returncode
    parts = result.stdout.strip().split()
    if len(parts) != 2:
        return '', -1, 2
    try:
        return parts[0], int(parts[1]), 0
    except ValueError:
        return '', -1, 2


def _controller(args: argparse.Namespace) -> dict[str, Any]:
    """One invocation models a controller process start/recovery transition."""
    controller_pid = os.getpid()
    journal = pathlib.Path(args.journal)
    if args.action == 'start':
        if not re.fullmatch(r'.+@sha256:[0-9a-f]{64}', args.image):
            return {'status': 'not-run', 'reason_code': 'WORKLOAD_IMAGE_NOT_DIGEST_PINNED',
                    'details': {'controller_pid': controller_pid}}
        execution_id = uuid.uuid4().hex
        name = args.container_name
        workload = (
            "import pathlib,subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(300)']); "
            "pathlib.Path('/evidence/child.pid').write_text(str(child.pid)+'\\n'); time.sleep(300)"
        )
        mount = f'type=bind,src={pathlib.Path(args.evidence_dir).resolve()},dst=/evidence'
        command = [
            'run', '--detach', '--init', '--name', name, '--network=none', '--read-only',
            '--user=65532:65532', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--pids-limit=64', '--memory=512m', '--memory-swap=512m', '--cpus=1',
            '--tmpfs=/tmp:rw,noexec,nosuid,size=16m', '--mount', mount,
            args.image, 'python3', '-c', workload,
        ]
        started = _docker(command, docker=args.docker, timeout_seconds=args.timeout)
        container_id = started.stdout.strip()
        if started.returncode != 0 or not container_id:
            return {'status': 'fail', 'reason_code': 'CONTAINER_START_FAILED',
                    'details': {'docker_exit_code': started.returncode, 'controller_pid': controller_pid}}
        identity = {
            'schema_version': 1,
            'job_id': args.job_id,
            'execution_id': execution_id,
            'generation': 1,
            'container_id': container_id,
            'container_name': name,
            'image_digest': args.image.rsplit('@', 1)[-1],
            'state': 'active',
            'created_by_controller_pid': controller_pid,
        }
        write_identity(journal, identity)
        return {'status': 'pass', 'reason_code': 'EXECUTION_IDENTITY_DURABLE',
                'details': {'identity_fsynced': True, 'controller_pid': controller_pid,
                            'execution_id': execution_id, 'generation': 1,
                            'container_id': container_id}}

    identity = read_identity(journal)
    if identity.get('job_id') != args.job_id:
        return {'status': 'fail', 'reason_code': 'EXECUTION_IDENTITY_JOB_MISMATCH',
                'details': {'controller_pid': controller_pid}}

    if args.action == 'stale':
        accepted = identity_matches(identity, job_id=args.job_id,
                                    execution_id=identity['execution_id'], generation=args.generation)
        return {'status': 'pass' if not accepted else 'fail',
                'reason_code': 'STALE_IDENTITY_REJECTED' if not accepted else 'STALE_IDENTITY_ACCEPTED',
                'details': {'stale_identity_rejected': not accepted,
                            'persisted_generation': identity['generation'],
                            'supplied_generation': args.generation,
                            'controller_pid': controller_pid}}

    if args.action == 'advance-generation':
        if identity['state'] != 'active' or args.generation != identity['generation']:
            return {'status': 'fail', 'reason_code': 'GENERATION_ADVANCE_CAS_FAILED',
                    'details': {'controller_pid': controller_pid}}
        identity['generation'] += 1
        write_identity(journal, identity)
        return {'status': 'pass', 'reason_code': 'GENERATION_ADVANCED',
                'details': {'persisted_generation': identity['generation'],
                            'previous_generation': args.generation,
                            'controller_pid': controller_pid}}

    if args.action == 'recover-active':
        active = identity['state'] == 'active'
        status, pid, code = _inspect(identity, docker=args.docker, timeout_seconds=args.timeout)
        passed = active and status == 'running' and pid > 0 and code == 0
        return {'status': 'pass' if passed else 'fail',
                'reason_code': 'ACTIVE_IDENTITY_RECOVERED' if passed else 'ACTIVE_IDENTITY_RECOVERY_FAILED',
                'details': {'controller_pid': controller_pid, 'execution_id': identity['execution_id'],
                            'generation': identity['generation'], 'container_id': identity['container_id'],
                            'container_status': status, 'container_pid': pid}}

    if args.action in {'cancel', 'drain'}:
        if identity['state'] != 'active':
            return {'status': 'fail', 'reason_code': 'EXECUTION_NOT_ACTIVE',
                    'details': {'controller_pid': controller_pid}}
        before = _docker(['top', identity['container_id'], '-eo', 'pid,ppid,pgid,sid,comm'],
                         docker=args.docker, timeout_seconds=args.timeout)
        stopped = _docker(['stop', '--time', '1', identity['container_id']],
                          docker=args.docker, timeout_seconds=args.timeout + 5)
        status, pid, inspect_code = _inspect(identity, docker=args.docker, timeout_seconds=args.timeout)
        after = _docker(['top', identity['container_id'], '-eo', 'pid,ppid,pgid,sid,comm'],
                        docker=args.docker, timeout_seconds=args.timeout)
        process_table_before = before.returncode == 0 and len(before.stdout.splitlines()) >= 3
        drained = (stopped.returncode == 0 and inspect_code == 0 and status == 'exited' and pid == 0 and
                   after.returncode != 0)
        if drained:
            identity['state'] = 'drained'
            identity['terminal'] = {'status': status, 'pid': pid, 'process_table_empty': True,
                                    'recorded_by_controller_pid': controller_pid}
            write_identity(journal, identity)
        if args.action == 'cancel':
            passed = drained
            details = {'cancel_requested': True, 'cancelled_and_drained': drained,
                       'generation': identity['generation'], 'container_id': identity['container_id'],
                       'controller_pid': controller_pid}
            reason = 'WHOLE_UNIT_CANCELLED_AND_DRAINED' if passed else 'CANCEL_DRAIN_UNPROVEN'
        else:
            passed = process_table_before and drained
            details = {'process_table_before': process_table_before,
                       'process_table_empty_after_drain': drained, 'generation': identity['generation'],
                       'container_id': identity['container_id'], 'controller_pid': controller_pid}
            reason = 'DRAINED_WITH_PROCESS_TABLE_PROOF' if passed else 'PROCESS_DRAIN_UNPROVEN'
        return {'status': 'pass' if passed else 'fail', 'reason_code': reason, 'details': details}

    if args.action == 'relaunch':
        status, pid, inspect_code = _inspect(identity, docker=args.docker, timeout_seconds=args.timeout)
        terminal = identity['state'] == 'drained' and inspect_code == 0 and status == 'exited' and pid == 0
        terminal = terminal and isinstance(identity.get('terminal'), dict)
        relaunch_attempted = args.generation == identity['generation']
        refused = terminal and relaunch_attempted
        # This is the launch admission path. The durable tombstone refuses before Docker is called;
        # raw `docker start` can restart a stopped container and is intentionally not the API.
        return {'status': 'pass' if refused else 'fail',
                'reason_code': 'TERMINAL_RESTART_REJECTED' if refused else 'TERMINAL_STATE_UNPROVEN',
                'details': {'terminal_restart_rejected': refused,
                            'relaunch_attempted': relaunch_attempted,
                            'launch_admitted': False,
                            'generation': identity['generation'], 'container_id': identity['container_id'],
                            'execution_id': identity['execution_id'],
                            'container_status': status, 'container_pid': pid,
                            'controller_pid': controller_pid}}
    return {'status': 'not-run', 'reason_code': 'UNKNOWN_CONTROLLER_ACTION',
            'details': {'controller_pid': controller_pid}}


class DockerLifecycleProbeTarget:
    """Exercise identity and lifecycle observations on a digest-pinned Docker target."""

    def __init__(self, workload_image: str, *, docker: str = 'docker', timeout_seconds: int = 20):
        if not re.fullmatch(r'.+@sha256:[0-9a-f]{64}', workload_image):
            raise ValueError('workload image must be pinned by a sha256 digest')
        if timeout_seconds < 1:
            raise ValueError('timeout_seconds must be positive')
        self.workload_image = workload_image
        self.docker = docker
        self.timeout_seconds = timeout_seconds

    def _controller(self, action: str, check_dir: pathlib.Path, job_id: str,
                    container_name: str, *, generation: int | None = None) -> dict[str, Any]:
        journal = check_dir / 'execution-identity.json'
        command = [sys.executable, str(pathlib.Path(__file__).resolve()), '--controller', action,
                   '--journal', str(journal), '--job-id', job_id, '--docker', self.docker,
                   '--image', self.workload_image, '--timeout', str(self.timeout_seconds),
                   '--container-name', container_name, '--evidence-dir', str(check_dir)]
        if generation is not None:
            command.extend(['--generation', str(generation)])
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=self.timeout_seconds + 10, check=False)
        try:
            observed = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            return {'status': 'not-run', 'reason_code': 'CONTROLLER_OUTPUT_INVALID',
                    'stdout': '', 'stderr': '',
                    'details': {'controller_exit_code': result.returncode,
                                'stdout_bytes': len(result.stdout.encode('utf-8', errors='replace'))}}
        if result.returncode != 0:
            observed['status'] = 'fail'
            observed['reason_code'] = 'CONTROLLER_FAILED'
        observed['stdout'] = ''
        observed['stderr'] = ''
        return observed

    def _kill_after_persisted_state(self, action: str, check_dir: pathlib.Path,
                                    job_id: str, container_name: str,
                                    expected_state: str) -> dict[str, Any]:
        journal = check_dir / 'execution-identity.json'
        command = [sys.executable, str(pathlib.Path(__file__).resolve()), '--controller', action,
                   '--journal', str(journal), '--job-id', job_id, '--docker', self.docker,
                   '--image', self.workload_image, '--timeout', str(self.timeout_seconds),
                   '--container-name', container_name, '--evidence-dir', str(check_dir),
                   '--hold']
        process = self._spawn_held_controller(command)
        deadline = time.monotonic() + self.timeout_seconds + 10
        try:
            while time.monotonic() < deadline:
                if journal.is_file() and read_identity(journal).get('state') == expected_state:
                    if expected_state != 'active' or self._wait_for_child(check_dir):
                        process.kill()
                        process.communicate(timeout=5)
                        return {'controller_pid': process.pid,
                                'controller_killed': process.returncode == -9,
                                'identity': read_identity(journal)}
                if process.poll() is not None:
                    stdout, _ = process.communicate()
                    raise RuntimeError(f'controller exited before durable state: {stdout[:256]}')
                time.sleep(0.05)
            process.kill()
            process.communicate(timeout=5)
            raise TimeoutError('controller did not persist expected lifecycle state')
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    def _spawn_held_controller(self, command: list[str]) -> subprocess.Popen[str]:
        return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def _wait_for_child(self, check_dir: pathlib.Path) -> bool:
        marker = check_dir / 'child.pid'
        deadline = time.monotonic() + min(self.timeout_seconds, 5)
        while time.monotonic() < deadline:
            if marker.is_file():
                return True
            time.sleep(0.05)
        return False

    def _cleanup(self, container_name: str) -> None:
        subprocess.run([self.docker, 'rm', '-f', container_name], capture_output=True, text=True,
                       timeout=self.timeout_seconds + 5, check=False)

    def execute_q11_q16(self, check_id: str, _name: str, check_dir: pathlib.Path,
                        job_id: str) -> dict[str, Any]:
        if check_id not in Q11_Q16:
            return {'status': 'not-run', 'reason_code': 'UNKNOWN_CHECK', 'details': {}}
        container_name = f'showcase-qual-{check_id.lower()}-{uuid.uuid4().hex[:12]}'
        killed_start = None
        try:
            if check_id == 'Q15':
                killed_start = self._kill_after_persisted_state('start', check_dir, job_id,
                                                                container_name, 'active')
                identity = killed_start['identity']
                started = {'status': 'pass', 'details': {
                    'controller_pid': killed_start['controller_pid'],
                    'execution_id': identity['execution_id'],
                    'generation': identity['generation'],
                    'container_id': identity['container_id']}}
            else:
                started = self._controller('start', check_dir, job_id, container_name)
        except Exception:
            self._cleanup(container_name)
            raise
        if started.get('status') != 'pass':
            self._cleanup(container_name)
            return started
        try:
            ready = self._wait_for_child(check_dir)
            if check_id == 'Q11':
                recovered = self._controller('recover-active', check_dir, job_id, container_name)
                recovered_pid = recovered.get('details', {}).get('controller_pid')
                first_pid = started['details']['controller_pid']
                passed = (ready and recovered.get('status') == 'pass' and recovered_pid != first_pid)
                details = dict(started.get('details', {}))
                details.update({'identity_fsynced': True, 'identity_read_after_controller_exit': passed,
                                'controller_pids': [first_pid, recovered_pid],
                                'generation': started['details']['generation'],
                                'container_id': started['details']['container_id']})
                return {'status': 'pass' if passed else 'fail',
                        'reason_code': 'EXECUTION_IDENTITY_DURABLE' if passed else 'IDENTITY_RELOAD_FAILED',
                        'details': details}
            if check_id == 'Q12':
                previous = started['details']['generation']
                advanced = self._controller('advance-generation', check_dir, job_id,
                                            container_name, generation=previous)
                stale = self._controller('stale', check_dir, job_id, container_name,
                                         generation=previous)
                passed = (advanced.get('status') == 'pass' and stale.get('status') == 'pass' and
                          stale.get('details', {}).get('supplied_generation') == previous and
                          stale.get('details', {}).get('persisted_generation') == previous + 1)
                return {'status': 'pass' if passed else 'fail',
                        'reason_code': 'STALE_IDENTITY_REJECTED' if passed else 'STALE_IDENTITY_ACCEPTED',
                        'details': stale.get('details', {})}
            if check_id in {'Q13', 'Q14'}:
                if not ready:
                    return {'status': 'fail', 'reason_code': 'WORKLOAD_NOT_READY',
                            'details': {'container_id': started['details']['container_id']}}
                stopped = self._controller('cancel' if check_id == 'Q13' else 'drain',
                                           check_dir, job_id, container_name)
                return stopped
            if check_id == 'Q15':
                killed = killed_start
                recovered = self._controller('recover-active', check_dir, job_id, container_name)
                controller_pids = [killed['controller_pid'],
                                   recovered.get('details', {}).get('controller_pid')]
                recovered_details = recovered.get('details', {})
                passed = (ready and killed['controller_killed'] and recovered.get('status') == 'pass' and
                          controller_pids[0] != controller_pids[1] and
                          recovered_details.get('generation') == started['details']['generation'] and
                          recovered_details.get('container_id') == started['details']['container_id'])
                try:
                    cleanup = self._controller('cancel', check_dir, job_id, container_name)
                except Exception:
                    cleanup = {'status': 'not-run'}
                passed = passed and cleanup.get('status') == 'pass'
                return {'status': 'pass' if passed else 'fail',
                        'reason_code': 'RESTART_ACTIVE' if passed else 'ACTIVE_RESTART_RECOVERY_FAILED',
                        'details': {'controller_pids': controller_pids,
                                    'controller_killed': killed['controller_killed'],
                                    'same_active_generation_recovered': passed,
                                    'generation': started['details']['generation'],
                                    'execution_id': started['details']['execution_id'],
                                    'container_id': started['details']['container_id'],
                                    'cleanup_drained': cleanup.get('status') == 'pass'}}

            if not self._wait_for_child(check_dir):
                return {'status': 'fail', 'reason_code': 'WORKLOAD_NOT_READY',
                        'details': {'container_id': started['details']['container_id']}}
            drained = self._kill_after_persisted_state('drain', check_dir, job_id,
                                                       container_name, 'drained')
            recovered = self._controller('relaunch', check_dir, job_id, container_name,
                                         generation=drained['identity']['generation'])
            controller_pids = [started['details']['controller_pid'],
                               drained['controller_pid'],
                               recovered.get('details', {}).get('controller_pid')]
            distinct = len(set(controller_pids)) == 3 and all(isinstance(pid, int) for pid in controller_pids)
            passed = recovered.get('status') == 'pass' and distinct and drained['controller_killed']
            terminal_details = recovered.get('details', {})
            passed = (passed and terminal_details.get('generation') == started['details']['generation'] and
                      terminal_details.get('execution_id') == started['details']['execution_id'] and
                      terminal_details.get('container_id') == started['details']['container_id'])
            terminal_details.update({'controller_pids': controller_pids,
                                     'terminal_restart_rejected': passed,
                                     'controller_killed': drained['controller_killed'],
                                     'terminal_record_fsynced': True,
                                     'generation': started['details']['generation'],
                                     'container_id': started['details']['container_id']})
            return {'status': 'pass' if passed else 'fail',
                    'reason_code': 'RESTART_DRAINED' if passed else 'TERMINAL_RESTART_RECOVERY_FAILED',
                    'details': terminal_details}
        finally:
            try:
                journal = check_dir / 'execution-identity.json'
                if journal.is_file() and read_identity(journal)['state'] == 'active':
                    cleanup = self._controller('cancel', check_dir, job_id, container_name)
                    if cleanup.get('status') != 'pass':
                        raise RuntimeError('probe cleanup could not prove execution drainage')
            finally:
                self._cleanup(container_name)


def _controller_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--controller', dest='action', required=True,
                        choices=('start', 'stale', 'advance-generation', 'recover-active', 'cancel', 'drain', 'relaunch'))
    parser.add_argument('--journal', required=True)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--docker', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--timeout', required=True, type=int)
    parser.add_argument('--container-name', required=True)
    parser.add_argument('--evidence-dir', required=True)
    parser.add_argument('--generation', type=int)
    parser.add_argument('--hold', action='store_true')
    args = parser.parse_args(argv)
    try:
        observed = _controller(args)
        if args.hold:
            time.sleep(300)
    except Exception as exc:
        observed = {'status': 'not-run', 'reason_code': 'CONTROLLER_EXCEPTION',
                    'details': {'exception_type': type(exc).__name__[:128]}}
    print(json.dumps(observed, sort_keys=True))
    return 0 if observed.get('status') == 'pass' else 1


if __name__ == '__main__' and '--controller' in sys.argv:
    raise SystemExit(_controller_cli(sys.argv[1:]))
