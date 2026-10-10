"""Q01-Q10 probes for a disposable Linux-container execution target."""
from __future__ import annotations

import dataclasses
import json
import pathlib
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from typing import Any


Q01_Q10 = {
    'Q01': 'PERMITTED_WRITE',
    'Q02': 'PROTECTED_WRITE_DIRECT',
    'Q03': 'PROTECTED_WRITE_CHILD',
    'Q04': 'PROTECTED_WRITE_GRANDCHILD',
    'Q05': 'CONTAINMENT_CHILD',
    'Q06': 'CONTAINMENT_GRANDCHILD',
    'Q07': 'CONTAINMENT_PARENT_EXIT',
    'Q08': 'CONTAINMENT_NEW_PROCESS_GROUP',
    'Q09': 'CONTAINMENT_NEW_SESSION',
    'Q10': 'CONTAINMENT_BACKGROUND_SHELL',
}


@dataclasses.dataclass(frozen=True)
class ProbeRecord:
    check_id: str
    name: str
    status: str
    reason_code: str
    evidence_path: pathlib.Path


def run_q01_q10(
    execute: Callable[[str, str, pathlib.Path], dict[str, Any]], evidence_root: pathlib.Path,
) -> list[ProbeRecord]:
    """Run every Q01-Q10 probe and preserve separate evidence, including after failures."""
    evidence_root = evidence_root.resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    records = []
    for check_id, name in Q01_Q10.items():
        check_dir = evidence_root / check_id
        check_dir.mkdir()
        try:
            observed = execute(check_id, name, check_dir)
            status = observed.get('status')
            if status not in {'pass', 'fail', 'not-run'}:
                status = 'not-run'
                reason_code = 'INVALID_PROBE_OUTCOME'
            else:
                reason_code = observed.get('reason_code') or 'UNSPECIFIED_PROBE_OUTCOME'
        except Exception as exc:  # preserve the failure and continue the complete suite
            observed = {'stdout': '', 'stderr': '', 'details': {'exception': type(exc).__name__}}
            status = 'not-run'
            reason_code = 'PROBE_EXCEPTION'

        evidence = {
            'check_id': check_id,
            'name': name,
            'status': status,
            'reason_code': reason_code,
            'stdout': observed.get('stdout', ''),
            'stderr': observed.get('stderr', ''),
            'details': observed.get('details', {}),
        }
        evidence_path = check_dir / 'probe.json'
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        records.append(ProbeRecord(check_id, name, status, reason_code, evidence_path))
    return records


class DockerProbeTarget:
    """Run Q01-Q10 against a digest-pinned image with conservative container controls."""

    def __init__(self, workload_image: str, *, docker: str = 'docker', timeout_seconds: int = 20):
        if '@sha256:' not in workload_image:
            raise ValueError('workload image must be pinned by a sha256 digest')
        if timeout_seconds < 1:
            raise ValueError('timeout_seconds must be positive')
        self.workload_image = workload_image
        self.docker = docker
        self.timeout_seconds = timeout_seconds

    def _run(self, args: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.docker, *args], capture_output=True, text=True,
            timeout=timeout or self.timeout_seconds, check=False,
        )

    def _base_args(self) -> list[str]:
        return [
            '--network=none', '--read-only', '--user=65532:65532', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--pids-limit=64', '--memory=512m',
            '--memory-swap=512m', '--cpus=1', '--tmpfs=/tmp:rw,noexec,nosuid,size=16m',
            '--tmpfs=/dev/shm:ro,noexec,nosuid,size=1m',
        ]

    @staticmethod
    def _python(code: str) -> list[str]:
        return ['python3', '-c', code]

    def _filesystem_probe(self, check_id: str, check_dir: pathlib.Path) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix=f'{check_id.lower()}-') as scratch:
            scratch_path = pathlib.Path(scratch)
            workspace = scratch_path / 'workspace'
            protected = scratch_path / 'protected'
            workspace.mkdir(mode=0o777)
            protected.mkdir(mode=0o777)
            marker = protected / 'attempted-write'
            workspace_mount = f'type=bind,src={workspace},dst=/workspace'
            protected_mount = f'type=bind,src={protected},dst=/protected,readonly'
            if check_id == 'Q01':
                code = "from pathlib import Path; Path('/workspace/allowed').write_text('ok'); print('allowed-write')"
            else:
                write = "from pathlib import Path; Path('/protected/attempted-write').write_text('bad')"
                if check_id == 'Q02':
                    code = f"try:\n {write}\n print('wrote')\nexcept OSError:\n print('denied')"
                elif check_id == 'Q03':
                    code = (
                        "import subprocess,sys; p=subprocess.run([sys.executable,'-c',"
                        + repr(write)
                        + "]); print('child-wrote' if p.returncode == 0 else 'child-denied')"
                    )
                else:
                    child = (
                        "import subprocess,sys; p=subprocess.run([sys.executable,'-c',"
                        + repr(write)
                        + "]); sys.exit(0 if p.returncode else 4)"
                    )
                    code = (
                        "import subprocess,sys; p=subprocess.run([sys.executable,'-c',"
                        + repr(child)
                        + "]); print('grandchild-denied' if p.returncode == 0 else 'grandchild-wrote')"
                    )
            proc = self._run([
                'run', '--rm', *self._base_args(), '--mount', workspace_mount, '--mount', protected_mount,
                self.workload_image, *self._python(code),
            ])
            wrote = marker.exists()
            if check_id == 'Q01':
                allowed = (workspace / 'allowed').is_file()
                passed = proc.returncode == 0 and allowed
                reason = 'PERMITTED_WRITE_OBSERVED' if passed else 'PERMITTED_WRITE_FAILED'
            else:
                passed = proc.returncode == 0 and not wrote and 'denied' in proc.stdout
                reason = 'PROTECTED_WRITE_DENIED' if passed else 'PROTECTED_WRITE_SUCCEEDED_OR_UNCERTAIN'
            return {
                'status': 'pass' if passed else 'fail', 'reason_code': reason,
                'stdout': proc.stdout, 'stderr': proc.stderr,
                'details': {'exit_code': proc.returncode, 'protected_path_written': wrote},
            }

    def _containment_probe(self, check_id: str, check_dir: pathlib.Path) -> dict[str, Any]:
        name = f'showcase-qual-{check_id.lower()}-{uuid.uuid4().hex[:12]}'
        if check_id == 'Q10':
            code = "import subprocess,time; subprocess.Popen(['/bin/sh','-c','sleep 60 & echo $! >/evidence/child.pid; wait']); time.sleep(60)"
        else:
            child_start = 'start_new_session=True' if check_id == 'Q09' else (
                'preexec_fn=lambda: __import__("os").setpgrp()' if check_id == 'Q08' else '')
            grandchild = check_id == 'Q06'
            child_code = 'import time; time.sleep(60)'
            if grandchild:
                child_code = (
                    "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c',"
                    + repr(child_code)
                    + "]); open('/evidence/grandchild.pid','w').write(str(p.pid)); time.sleep(60)"
                )
            else:
                child_code = (
                    "import os,time; open('/evidence/child.pid','w').write(str(os.getpid())); time.sleep(60)"
                )
            kwargs = f', {child_start}' if child_start else ''
            child_name = 'grandchild' if grandchild else 'child'
            parent_exit = 'import os; os._exit(0)' if check_id == 'Q07' else 'time.sleep(60)'
            code = (
                'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c",'
                + repr(child_code)
                + f']{kwargs}); open("/evidence/{child_name}.pid","w").write(str(p.pid)); '
                + parent_exit
            )

        check_dir = check_dir.resolve()
        check_dir.chmod(0o777)
        evidence_mount = f'type=bind,src={check_dir},dst=/evidence'

        start = self._run([
            'run', '--detach', '--init', '--name', name, *self._base_args(), '--mount', evidence_mount,
            self.workload_image,
            *self._python(code),
        ])
        if start.returncode != 0 or not start.stdout.strip():
            return {'status': 'fail', 'reason_code': 'CONTAINER_START_FAILED', 'stdout': start.stdout,
                    'stderr': start.stderr, 'details': {'exit_code': start.returncode}}
        container_id = start.stdout.strip()
        if check_id == 'Q07':
            child_pid_file = check_dir / 'child.pid'
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not child_pid_file.exists():
                time.sleep(.05)
            state = self._run(['inspect', '--format', '{{.State.Status}} {{.State.ExitCode}}', container_id])
            top = self._run(['top', container_id, '-eo', 'pid,ppid,pgid,sid,comm'])
            remove = self._run(['rm', '-f', container_id])
            child_started = child_pid_file.is_file()
            unit_stopped = (state.returncode == 0 and state.stdout.startswith('exited 0') and
                            top.returncode != 0 and remove.returncode == 0)
            passed = child_started and unit_stopped
            return {
                'status': 'pass' if passed else 'fail',
                'reason_code': 'PARENT_EXIT_DRAINED_UNIT' if passed else 'PARENT_EXIT_DRAIN_UNPROVEN',
                'stdout': '\n'.join((start.stdout, child_pid_file.read_text() if child_started else '',
                                     state.stdout, top.stdout, remove.stdout)),
                'stderr': '\n'.join((start.stderr, state.stderr, top.stderr, remove.stderr)),
                'details': {'container_id': container_id, 'child_started': child_started,
                            'unit_stopped': unit_stopped, 'removed': remove.returncode == 0},
            }
        marker_path = check_dir / ('grandchild.pid' if check_id == 'Q06' else 'child.pid')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not marker_path.is_file():
            time.sleep(.05)
        top = self._run(['top', container_id, '-eo', 'pid,ppid,pgid,sid,comm'])
        stop = self._run(['stop', '--time', '1', container_id], timeout=self.timeout_seconds + 5)
        inspect = self._run(['inspect', '--format', '{{.State.Status}}', container_id])
        remove = self._run(['rm', '-f', container_id])
        ready_ok = marker_path.is_file()
        contained = top.returncode == 0 and top.stdout.count('\n') >= 2
        drained = stop.returncode == 0 and inspect.returncode == 0 and inspect.stdout.strip() == 'exited'
        passed = ready_ok and contained and drained and remove.returncode == 0
        return {
            'status': 'pass' if passed else 'fail',
            'reason_code': 'CONTAINER_DESCENDANTS_DRAINED' if passed else 'CONTAINMENT_OR_DRAIN_FAILED',
            'stdout': '\n'.join((start.stdout, marker_path.read_text() if ready_ok else '', top.stdout, stop.stdout, inspect.stdout)),
            'stderr': '\n'.join((start.stderr, top.stderr, stop.stderr, inspect.stderr, remove.stderr)),
            'details': {'container_id': container_id, 'ready': ready_ok, 'descendant_visible': contained,
                        'container_exited': drained, 'removed': remove.returncode == 0},
        }

    def execute_q01_q10(self, check_id: str, name: str, evidence_dir: pathlib.Path) -> dict[str, Any]:
        if check_id in {'Q01', 'Q02', 'Q03', 'Q04'}:
            return self._filesystem_probe(check_id, evidence_dir)
        if check_id in {'Q05', 'Q06', 'Q07', 'Q08', 'Q09', 'Q10'}:
            return self._containment_probe(check_id, evidence_dir)
        return {'status': 'not-run', 'reason_code': 'UNKNOWN_CHECK', 'stdout': '', 'stderr': '', 'details': {}}
