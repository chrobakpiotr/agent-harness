"""Grading on the qualified Docker target (AH5-04c): ``QualifiedDockerBackend`` and ``probe_grading_layout``.

This module is part of ``qualification.probe_digest()``: changing how grading runs, or how the layout probe judges
it, needs a new independent review of the target (ADR 0002 amendment).
"""

import errno
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import textwrap
import threading
import time
import uuid
from pathlib import Path

from . import contract
from .execution import _now, _seal

# Exactly the container configuration the AH5-04b qualification ran (Showcase q_probes._base_args and B8/B9).
QUALIFIED_LIMITS = {"cpus": 1, "memory_bytes": 512 << 20, "pids": 64, "disk_bytes": 256 << 20, "output_bytes": 1 << 20}
_WORKSPACE_TMPFS_BYTES = 251_658_240  # + 16 MiB /tmp = the qualified 256 MiB writable disk
QUALIFIED_RUN_FLAGS = (
    "--network=none", "--read-only", "--user=65532:65532", "--cap-drop=ALL", "--security-opt=no-new-privileges",
    "--pids-limit=64", "--memory=512m", "--memory-swap=512m", "--cpus=1", "--tmpfs=/tmp:rw,noexec,nosuid,size=16m",
    "--tmpfs=/dev/shm:ro,noexec,nosuid,size=1m",
    f"--tmpfs=/workspace:rw,noexec,nosuid,size={_WORKSPACE_TMPFS_BYTES},mode=1777",
)
GRADING_ENV = {"PYTHONPATH": "/workspace", "PYTHONDONTWRITEBYTECODE": "1", "HOME": "/tmp"}
# What the backend adds around the qualified flags; part of qualification.policy_digest(), so changing it needs a new
# review. /output is host-bounded (polled, killed above the answers limit), not one of the 04b-qualified mounts.
GRADING_LAYOUT = {"options": ["-i", "--workdir=/workspace"],
                  "mounts": ["type=bind,src=<inputs>,dst=/inputs,readonly", "type=bind,src=<output>,dst=/output"],
                  "entrypoint": ["sh", "-c", 'tar -x -C /workspace && exec "$@"', "grade"]}
_ANSWERS_LIMIT = 1 << 20


class QualifiedDockerBackend:
    """Grades one prepared workspace per request on the qualified Docker target (contract v2 only).

    One instance is one grading session: it is bound at construction to one passing qualification of the caller's
    own ``job_id`` and grades any number of requests. Per request: a fresh container with exactly the qualified
    flags; the workspace streams in as a tar into a tmpfs, ``cases`` is mounted read-only at
    ``/inputs/cases.json``, ``command`` runs in ``/workspace`` with ``GRADING_ENV`` only; after exit the container
    is removed and its absence confirmed, and only then is ``/output/answers.json`` (regular file, at most 1 MiB)
    read and sealed as the ``answers`` artifact. Expected values never enter the container; the verdict is the
    caller's.
    """

    kind = "qualified-docker"
    isolation_level = "qualified"
    capabilities = ("cancel", "qualified_isolation")
    supports_limits = True

    def __init__(self, qualification, *, qualification_evidence, capability_report, job_id, image, command, cases,
                 reviewed=None, target_kind="docker-desktop", docker="docker", grace=2.0):
        # reviewed=(report, evidence_root): a session report qualified by matching a reviewed tuple (ADR 0002)
        contract.validate_capability_binding(capability_report, qualification, qualification_evidence, job_id,
                                             reviewed=reviewed)
        passes = (contract.qualification_passes(qualification, qualification_evidence) if reviewed is None
                  else contract.session_qualification_passes(qualification, qualification_evidence, *reviewed))
        if not capability_report["qualified"] or not passes:
            raise contract.ContractError("NOT_QUALIFIED", "the qualification does not pass")
        if reviewed is not None:  # a session names the installed probe code and policy; both must be this library's
            from . import qualification as installed
            if (qualification["tuple"].get("probe_digest") != installed.probe_digest() or
                    qualification["policy_digest"] != installed.policy_digest()):
                raise contract.ContractError("NOT_QUALIFIED", "session probe/policy digest is not this library's")
        digest = qualification["tuple"]["workload_image"]
        if not isinstance(image, str) or not image.endswith("@" + digest):
            raise ValueError("image must be the qualified workload image pinned by its digest")
        if not isinstance(command, (list, tuple)) or not command or not all(isinstance(a, str) for a in command):
            raise ValueError("command must be a non-empty argv list")
        self.qualification, self.image, self.command = qualification, image, list(command)
        self.target_kind = target_kind
        self.cases, self.docker, self.grace = Path(cases), docker, grace
        self.target = {"id": qualification["target"], "image_digest": digest,
                       "qualification_digest": contract.qualification_digest(qualification)}
        self.rejection_reasons = {}

    @property
    def rejection_reason(self):
        return next(reversed(self.rejection_reasons.values()), None)

    def report(self):
        return {"contract_version": contract.CONTRACT_VERSION, "target": self.target["id"],
                "policy_digest": self.qualification["policy_digest"], "discovered": True, "supported": True,
                "qualified": True, "launch_ready": True, "capabilities": sorted(self.capabilities), "refusal": None}

    def refuse(self, request, roots):
        code, reason = self._refusal(request)
        if code is not None:
            self.rejection_reasons[request["request_id"]] = reason
        return code

    def _refusal(self, request):
        if request["contract_version"] != 2 or request["limits"] != QUALIFIED_LIMITS:
            return "CAPABILITY_UNSUPPORTED", "a qualified grading request is contract v2 with exactly QUALIFIED_LIMITS"
        if not self.cases.is_file():
            return "CAPABILITY_UNSUPPORTED", "cases file missing"
        from .qualification import (
            live_tuple,  # the same identification the qualification recorded
        )
        want = self.qualification["tuple"]
        live, errors = live_tuple(self.target_kind, self.docker, self.image, want["job_id"])
        if any("unavailable" in error for error in errors):
            return "BACKEND_UNAVAILABLE", f"docker target not identifiable: {sorted(errors)}"
        drift = sorted({k for k in ("host", "kernel", "engine", "workload_image") if live.get(k) != want.get(k)}
                       | set(errors))
        if drift:  # the qualification covers exactly its tuple
            return "NOT_QUALIFIED", f"live target differs from the qualified tuple: {drift}"
        return None, None

    def _docker(self, *args, timeout=30, check=True, **kwargs):
        return subprocess.run([self.docker, *args], capture_output=True, text=True, timeout=timeout, check=check,
                              stdin=subprocess.DEVNULL, **kwargs).stdout

    def run(self, request, roots, cancelled, execution_id, started_at):
        name = f"agent-harness-grade-{uuid.uuid4().hex[:16]}"
        members = _workspace_members(Path(roots["workspace"]))
        if members is None:  # larger than the qualified workspace tmpfs, so it could never be graded as qualified
            now = _now()
            return self._body(request, "error", None, "confirmed", started_at, now, error_code="LAUNCH_FAILED")
        with tempfile.TemporaryDirectory(prefix="ah-grade-") as tmp:
            inputs, output = Path(tmp) / "inputs", Path(tmp) / "output"
            inputs.mkdir()
            output.mkdir(mode=0o777)
            output.chmod(0o777)  # the container user (65532) writes here
            shutil.copyfile(self.cases, inputs / "cases.json")
            mounts = [m.replace("<inputs>", str(inputs)).replace("<output>", str(output))
                      for m in GRADING_LAYOUT["mounts"]]
            argv = [self.docker, "run", GRADING_LAYOUT["options"][0], f"--name={name}", *QUALIFIED_RUN_FLAGS,
                    "--mount", mounts[0], "--mount", mounts[1], *GRADING_LAYOUT["options"][1:],
                    *[f"--env={k}={v}" for k, v in GRADING_ENV.items()],
                    self.image, *GRADING_LAYOUT["entrypoint"], *self.command]
            child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
            captured, truncated = bytearray(), threading.Event()
            reader = threading.Thread(target=_drain_capped, args=(child.stdout, captured, truncated), daemon=True)
            reader.start()
            writer = threading.Thread(target=_stream_tar, args=(child.stdin, Path(roots["workspace"]), members),
                                      daemon=True)
            writer.start()
            # The wall clock starts once the workspace is in; the stream itself is bounded by the tmpfs size and by
            # _STREAM_SECONDS, and cancel applies throughout.
            stream_deadline, deadline, outcome = time.monotonic() + _STREAM_SECONDS, None, None
            while child.poll() is None:
                now = time.monotonic()
                if deadline is None and not writer.is_alive():
                    deadline = now + request["timeout_seconds"]
                if cancelled.is_set():
                    outcome = "cancel"
                elif (deadline is None and now >= stream_deadline) or (deadline is not None and now >= deadline):
                    outcome = "timeout"
                elif _tree_bytes(output) > _ANSWERS_LIMIT + _OUTPUT_SLACK:
                    outcome = "disk"  # /output is outside the qualified tmpfs set: the host bounds it
                if outcome:
                    break
                time.sleep(0.02)
            container = self._container_id(name)
            if outcome:
                self._docker("kill", container or name, check=False)
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                child.kill()
            reader.join(5)
            child.stdout.close()
            state = self._state(container or name)
            self._docker("rm", "-f", container or name, check=False)
            gone = container is not None and not self._docker(
                "ps", "-a", "--no-trunc", f"--filter=id={container}", "--format={{.ID}}", check=False).strip()
            ended = _now()
            answers = _read_answers(output) if gone else None
        oom = bool(state and state.get("OOMKilled"))
        exit_code = state.get("ExitCode") if state else None
        fired = {"timeout": "timeout", "disk": "disk"}.get(outcome, "oom" if oom else None)
        if outcome == "disk" or (outcome is None and oom):
            outcome = "error"
        elif outcome is None:
            outcome = "completed" if gone and type(exit_code) is int else "unknown"
        body = self._body(request, outcome, exit_code if outcome == "completed" else None,
                          "confirmed" if gone else "unconfirmed", started_at, ended,
                          error_code=contract.LIMIT_EXCEEDED if outcome == "error" else None,
                          fired=fired, truncated=truncated.is_set())
        if answers is not None:
            body["artifacts"] = [{"name": "answers", **_seal(answers, roots, request, "answers")}]
        return body

    def _body(self, request, outcome, exit_code, drain, started_at, ended, *, error_code=None, fired=None,
              truncated=False):
        attempt = {"attempt_id": "attempt-1", "outcome": outcome, "started_at": started_at, "ended_at": ended}
        return {"outcome": outcome, "exit_code": exit_code, "completion": None, "error_code": error_code,
                "drain": drain, "started_at": started_at, "ended_at": ended, "attempts": [attempt],
                "candidate": None, "artifacts": [], "usage_events": [], "usage_completeness": "unknown",
                "target": dict(self.target),
                "limits": {"applied": {**request["limits"], "timeout_seconds": request["timeout_seconds"]},
                           "fired": fired, "output_truncated": truncated}}

    def _container_id(self, name):
        found = self._docker("inspect", "--format", "{{.Id}}", name, check=False).strip()
        return found if re.fullmatch(r"[0-9a-f]{64}", found) else None

    def _state(self, name):
        try:
            return json.loads(self._docker("inspect", "--format", "{{json .State}}", name, check=False) or "null")
        except ValueError:
            return None


_STREAM_SECONDS = 120  # streaming at most 240 MiB into the container
_OUTPUT_SLACK = 64 << 10  # polling overshoot allowance on /output


def _workspace_members(workspace):
    """Directories and single-link regular files to stream (no ``.git``, symlinks, hardlinks or devices); None if
    their apparent size exceeds the qualified workspace tmpfs."""
    members, total = [], 0
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace)
        if ".git" in relative.parts:
            continue
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1):
            members.append((path, str(relative)))
            total += info.st_size if stat.S_ISREG(info.st_mode) else 0
            if total > _WORKSPACE_TMPFS_BYTES:
                return None
    return members


def _stream_tar(pipe, workspace, members):
    import tarfile
    try:
        with tarfile.open(fileobj=pipe, mode="w|") as archive:
            for path, arcname in members:
                archive.add(path, arcname=arcname, recursive=False)
    except OSError:
        pass
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _tree_bytes(root):
    total, stack = 0, [root]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    else:
                        total += entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
    return total


def _drain_capped(pipe, captured, truncated, limit=1 << 20):
    for chunk in iter(lambda: pipe.read(65536), b""):
        room = limit - len(captured)
        if room > 0:
            captured.extend(chunk[:room])
        if len(chunk) > room:
            truncated.set()


def _read_answers(output):
    """``answers.json`` from the output directory: no-follow, regular file, at most 1 MiB; else None."""
    try:
        fd = os.open(output / "answers.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = os.read(fd, _ANSWERS_LIMIT + 1)
        return data if len(data) <= _ANSWERS_LIMIT else None
    finally:
        os.close(fd)


_RUNTIME_ENV = {"HOSTNAME", "PWD", "LC_CTYPE"}  # set by Docker, the shell and Python for every process


def probe_grading_layout(image, out, *, docker="docker", timeout_seconds=60):
    """Exercise the backend's own grading layout on the target (AH5-04c-1): one grade through the real argv
    (stdin tar into /workspace, read-only /inputs, /output, working directory and environment), a writer of more
    than the answers limit to /output (must be killed as ``disk``) and a sleeper (must time out). Returns the
    evidence document; ``passed`` is true only when every case behaves as qualified."""
    out = Path(out)
    workspace, evidence = out / "workspace", out / "backend-evidence"
    (workspace / "pkg").mkdir(parents=True)
    evidence.mkdir()
    marker = uuid.uuid4().hex
    (workspace / "pkg" / "marker.txt").write_text(marker)
    cases = out / "cases.json"
    cases.write_text(json.dumps({"probe": marker}))
    canary = "AGENT_HARNESS_HOST_CANARY"
    report = textwrap.dedent("""\
        import json, os, sys
        try:
            open("/inputs/written", "w").write("x"); inputs_errno = 0
        except OSError as exc:
            inputs_errno = exc.errno
        json.dump({"cwd": os.path.realpath("."), "env": {k: os.environ.get(k) for k in sorted(os.environ)},
                   "inputs_errno": inputs_errno, "cases": json.load(open(sys.argv[1])),
                   "marker": open("/workspace/pkg/marker.txt").read()}, open(sys.argv[2], "w"))
        """)
    flood = "open('/output/flood.bin', 'wb').write(b'x' * (3 << 20)); import time; time.sleep(60)"
    backend = object.__new__(QualifiedDockerBackend)  # an unbound instance: the probe runs before any qualification
    backend.image, backend.cases, backend.docker, backend.grace = image, cases, docker, 2.0
    backend.target = {"id": "grading-layout-probe", "image_digest": "sha256:" + image.rsplit("@sha256:", 1)[-1],
                      "qualification_digest": None}
    results = {}
    previous = os.environ.get(canary)
    os.environ[canary] = marker  # must not reach the container: only GRADING_ENV is passed
    try:
        for name, command, timeout in (("grade", ["python3", "-c", report, "/inputs/cases.json",
                                                  "/output/answers.json"], timeout_seconds),
                                       ("output-flood", ["python3", "-c", flood], timeout_seconds),
                                       ("timeout", ["python3", "-c", "import time; time.sleep(60)"], 2)):
            backend.command = command
            request = contract.validate_request({
                "contract_version": 2, "request_id": f"layout-{name}", "trial_id": None,
                "task_digest": "sha256:" + "0" * 64, "config_digest": "sha256:" + "0" * 64, "provider": "harness",
                "model": "grading-layout", "settings": {}, "capabilities": ["qualified_isolation"],
                "timeout_seconds": timeout, "max_attempts": 1, "input_bindings": [], "limits": QUALIFIED_LIMITS})
            body = backend.run(request, {"workspace": str(workspace), "evidence_root": str(evidence)},
                               threading.Event(), name, _now())
            answers = None
            if body["artifacts"]:
                answers = json.loads((evidence / body["artifacts"][0]["path"]).read_text())
            results[name] = {"outcome": body["outcome"], "fired": body["limits"]["fired"], "drain": body["drain"],
                             "answers": answers}
    finally:
        if previous is None:
            os.environ.pop(canary, None)
        else:
            os.environ[canary] = previous
    grade = results["grade"]["answers"] or {}
    env = grade.get("env", {})
    try:  # exactly: the image's own ENV, GRADING_ENV, and what the runtime sets for every process
        image_env = dict(item.split("=", 1) for item in json.loads(subprocess.run(
            [docker, "image", "inspect", "--format", "{{json .Config.Env}}", image], capture_output=True, text=True,
            timeout=60, check=True).stdout) or [])
    except (OSError, subprocess.SubprocessError, ValueError):
        image_env = None
    allowed = None if image_env is None else {**image_env, **GRADING_ENV}
    checks = {
        "grade completed with answers after destroy": results["grade"]["outcome"] == "completed" and bool(grade)
        and results["grade"]["drain"] == "confirmed",
        "working directory is /workspace": grade.get("cwd") == "/workspace",
        "workspace streamed in": grade.get("marker") == marker,
        "inputs mounted read-only": grade.get("inputs_errno") == errno.EROFS
        and grade.get("cases") == {"probe": marker},
        "environment is exactly the image's ENV plus GRADING_ENV": allowed is not None
        and {k: v for k, v in env.items() if k not in _RUNTIME_ENV} == allowed and canary not in env
        and marker not in json.dumps(env),
        "/output over the answers limit is killed as disk": results["output-flood"]["fired"] == "disk"
        and results["output-flood"]["answers"] is None,
        "wall clock fires as timeout": results["timeout"]["outcome"] == "timeout"
        and results["timeout"]["fired"] == "timeout",
    }
    return {"passed": all(checks.values()), "checks": checks, "results": results}
