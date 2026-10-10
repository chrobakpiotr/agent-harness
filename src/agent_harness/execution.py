"""Offline launch/cancel API (AH5-05a). See docs/adr/0004-offline-launch-api.md.

``launch(request, backend, workspace=..., evidence_root=...)`` answers a contract v1 request with a contract v1
result. It needs only those two absolute directories, no repository identity, so a trial without a Git base
launches the same way. Two backends:
``ScriptedBackend`` (deterministic, ``isolation_level: fake``) and ``ProcessBackend`` (a real local process
group, ``isolation_level: controlled``; no sandbox, never qualified). Nothing here qualifies isolation.

Every launch is bound to its ``request_id``: a start marker and the terminal result are published create-once
under ``<evidence_root>/executions/``, named by the sha256 of the ID (an ID may contain ``/``), so a relaunch
returns the stored result, a launch interrupted before its result reports ``unknown``, and a different request
reusing the ID is ``BINDING_MISMATCH``. Executions still running at interpreter exit are cancelled.
"""

import atexit
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, contract

_RUNNING = {}  # (resolved executions dir, slot) -> Execution launched by this process
_RUNNING_LOCK = threading.Lock()


@atexit.register
def _cancel_running_at_exit():
    """Children run in their own session; stop them rather than leave them running after the caller exits."""
    with _RUNNING_LOCK:
        running = list(_RUNNING.values())
    for execution in running:
        execution.cancel()
    deadline = time.monotonic() + 30  # one deadline for all, not 30 s each
    for execution in running:
        execution._done.wait(max(0.0, deadline - time.monotonic()))


# A forked child has none of the parent's worker threads; it must not wait for their executions.
os.register_at_fork(after_in_child=_RUNNING.clear)


class Execution:
    """One launch. ``cancel()`` asks the backend to stop; ``result()`` waits for the validated terminal result."""

    def __init__(self, request):
        self.request = request
        self._cancel = threading.Event()
        self._done = threading.Event()
        self._result = None

    def cancel(self):
        self._cancel.set()

    def result(self, timeout=None):
        if not self._done.wait(timeout):
            raise TimeoutError("execution has no terminal result yet")
        return self._result

    def _finish(self, result):
        self._result = result
        self._done.set()


def launch(request, backend, *, workspace, evidence_root):
    contract.validate_request(request)
    if backend.isolation_level == "qualified":  # its target goes into every result, even an unknown one
        contract.validate_target(getattr(backend, "target", None), "backend.target")
    roots = {"workspace": _absolute(workspace, "workspace"), "evidence_root": _absolute(evidence_root, "evidence_root")}
    execution = Execution(request)
    root = Path(roots["evidence_root"]).resolve() / "executions"
    slot = _slot(request["request_id"])
    key = (str(root), slot)
    refuse = getattr(backend, "refuse", None)
    # A probe can take a while: run it before taking the lock, and not when this request already has a result.
    refused = refuse(request, roots) if refuse is not None and not (root / f"{slot}.json").exists() else None
    with _RUNNING_LOCK:
        running = _RUNNING.get(key)
        if running is not None:
            _check_binding(contract.request_digest(running.request), request)
            return running
        marker_path = root / f"{slot}.started"
        if marker_path.exists():  # read-only: a reused ID is checked before anything else
            _check_binding(_marker_digest(marker_path), request)
        missing = [c for c in request["capabilities"] if c not in backend.capabilities]
        code = refused if refuse is not None else getattr(backend, "rejection", None)
        if request.get("limits") is not None and not getattr(backend, "supports_limits", False):
            code = "CAPABILITY_UNSUPPORTED"  # never run a limited request without enforcing its limits
        if missing:
            code = "NOT_QUALIFIED" if "qualified_isolation" in missing else "CAPABILITY_UNSUPPORTED"
        if code:  # never launched: no process, nothing written
            execution._finish(_rejected(request, backend, code))
            return execution
        stored = _stored(root, slot, request, backend)
        if stored is not None:
            execution._finish(stored)
            return execution
        _RUNNING[key] = execution
    threading.Thread(target=_run, args=(execution, roots, backend, root, slot, key), daemon=True).start()
    return execution


def _run(execution, roots, backend, root, slot, key):
    request = execution.request
    started_at = _now()
    execution_id = f"{backend.kind}-{uuid.uuid4()}"
    result = None
    try:
        try:
            body = backend.run(request, roots, execution._cancel, execution_id, started_at)
            result = _envelope(request, backend, execution_id, execution._cancel.is_set(), body)
            contract.validate_result(result, request)
        except Exception:  # noqa: BLE001 - a broken backend or result never becomes success
            result = _unknown(request, backend, execution_id, execution._cancel.is_set(), started_at)
        try:
            _publish(root / f"{slot}.json", result)
        except FileExistsError:  # another launcher recorded this ID first: its record is authoritative
            result = _load(root / f"{slot}.json", request)
    finally:
        with _RUNNING_LOCK:
            _RUNNING.pop(key, None)
        if result is None:
            result = _unknown(request, backend, execution_id, execution._cancel.is_set(), started_at)
        execution._finish(result)


def _absolute(value, name):
    """An absolute directory path without ``.``/``..`` segments (as ``RepoContext`` paths)."""
    if not isinstance(value, (str, os.PathLike)):
        raise contract.ContractError("MALFORMED", name)
    value = os.fspath(value)
    if not value.startswith("/") or "\0" in value or any(part in (".", "..") for part in value.split("/")):
        raise contract.ContractError("UNSAFE_PATH", name)
    return value


def _slot(request_id):
    return hashlib.sha256(request_id.encode("utf-8")).hexdigest()


def _stored(root, slot, request, backend):
    """The stored result for this request ID, ``unknown`` if a start was never finished, else None (claimed)."""
    root.mkdir(parents=True, exist_ok=True)
    result_path = root / f"{slot}.json"
    marker = {"contract_version": contract.CONTRACT_VERSION, "request_id": request["request_id"],
              "request_digest": contract.request_digest(request)}
    try:
        _publish(root / f"{slot}.started", marker)
        return None
    except FileExistsError:
        _check_binding(_marker_digest(root / f"{slot}.started"), request)
    if not result_path.exists():
        # Started by another launcher that has not published a result (or never will): launch state unknown,
        # never rerun. If that launcher finishes later, its result loses to this record.
        try:
            _publish(result_path, _unknown(request, backend, None, False, None))
        except FileExistsError:
            pass
    return _load(result_path, request)


def _marker_digest(path):
    try:
        return json.loads(path.read_text())["request_digest"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise contract.ContractError("MALFORMED", f"start marker {path.name}") from error


def _load(path, request):
    try:
        return contract.validate_result(json.loads(path.read_text()), request)
    except (OSError, ValueError) as error:  # ContractError is a ValueError
        raise contract.ContractError("MALFORMED", f"stored result {path.name}") from error


def _check_binding(digest, request):
    if digest != contract.request_digest(request):
        raise contract.ContractError("BINDING_MISMATCH", "request_id already bound to another request")


def _publish(path, doc):
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    try:
        tmp.write_text(json.dumps(doc, sort_keys=True))
        os.link(tmp, path)  # create-once: fails if the record exists
    finally:
        tmp.unlink(missing_ok=True)


def _v2(request, limits=None, target=None):
    """v2 fields: the target and limits as the backend states them (offline backends state no target)."""
    return {"target": target, "limits": limits} if request["contract_version"] == 2 else {}


def _applied(request, fired=None, output_truncated=False):
    """v2 limits of a scripted answer: the request's limits echoed exactly, or None without requested limits."""
    if request["contract_version"] != 2 or request["limits"] is None:
        return None
    return {"applied": {**request["limits"], "timeout_seconds": request["timeout_seconds"]}, "fired": fired,
            "output_truncated": output_truncated}


def _envelope(request, backend, execution_id, cancel_requested, body):
    limits, target = body.pop("limits", None), body.pop("target", None)
    return {"contract_version": request["contract_version"], "request_id": request["request_id"],
            "request_digest": contract.request_digest(request), "execution_id": execution_id,
            "cancel_requested": cancel_requested, "isolation_level": backend.isolation_level,
            "resolved_model": None, "versions": {"agent-harness": __version__}, **body,
            **_v2(request, limits, target)}


def _rejected(request, backend, code):
    return {"contract_version": request["contract_version"], "request_id": request["request_id"], **_v2(request),
            "request_digest": contract.request_digest(request), "execution_id": None, "outcome": "rejected",
            "exit_code": None, "completion": None, "error_code": code, "drain": None, "cancel_requested": False,
            "isolation_level": backend.isolation_level, "resolved_model": None,
            "versions": {"agent-harness": __version__}, "started_at": None, "ended_at": None, "attempts": [],
            "candidate": None, "artifacts": [], "usage_events": [], "usage_completeness": "unknown"}


def _unknown(request, backend, execution_id, cancel_requested, started_at):
    now = _now()
    started_at = started_at or now
    # A qualified backend names its target even when the launch state is unknown.
    target = getattr(backend, "target", None) if backend.isolation_level == "qualified" else None
    return {"contract_version": request["contract_version"], "request_id": request["request_id"],
            **_v2(request, target=target),
            "request_digest": contract.request_digest(request), "execution_id": execution_id or "unknown",
            "outcome": "unknown", "exit_code": None, "completion": None, "error_code": None, "drain": "unconfirmed",
            "cancel_requested": cancel_requested,
            "isolation_level": backend.isolation_level, "resolved_model": None,
            "versions": {"agent-harness": __version__}, "started_at": started_at, "ended_at": now,
            "attempts": [{"attempt_id": "attempt-1", "outcome": "unknown", "started_at": started_at,
                          "ended_at": now}],
            "candidate": None, "artifacts": [], "usage_events": [], "usage_completeness": "unknown"}


def _seal(source, roots, request, name="candidate"):
    """Copy a candidate (or named artifact) into the evidence root and return its contract reference."""
    relative = f"executions/{_slot(request['request_id'])}/{name}"
    target = Path(roots["evidence_root"]) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(source, bytes):
        target.write_bytes(source)
    else:
        shutil.copyfile(source, target)
    data = target.read_bytes()
    return {"path": relative, "sha256": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data)}


def _report(backend):
    return {"contract_version": contract.CONTRACT_VERSION, "target": backend.kind, "policy_digest": None,
            "discovered": True, "supported": True, "qualified": False, "launch_ready": False,
            "capabilities": sorted(backend.capabilities), "refusal": "NOT_QUALIFIED"}


class ScriptedBackend:
    """Deterministic answers: one entry per attempt, ``{"outcome": ..., "units": {...} | None}``.

    The last attempt's outcome is the execution outcome. ``duration`` seconds pass before answering, during
    which a cancel turns the execution into ``cancel``. ``rejection`` (a contract error code) refuses every
    request without launching, e.g. ``BACKEND_UNAVAILABLE``.
    """

    kind = "scripted"
    isolation_level = "fake"
    capabilities = ("cancel", "usage")
    supports_limits = True  # scripts a v2 limit outcome; enforces nothing

    def __init__(self, attempts=({"outcome": "completed", "units": None},), *, exit_code=0, completion=None,
                 error_code=None, candidate=None, cache_semantics="separate", duration=0.0, rejection=None,
                 fired=None, output_truncated=False):
        if rejection is not None and rejection not in contract.ERROR_CODES:
            raise ValueError("rejection must be a contract error code")
        if fired is not None and fired not in contract.FIRED_LIMITS:
            raise ValueError("fired must be one of contract.FIRED_LIMITS")
        self.fired, self.output_truncated = fired, output_truncated  # v2 requests with limits only
        self.rejection = rejection  # e.g. BACKEND_UNAVAILABLE: answer `rejected` without launching
        self.attempts, self.exit_code, self.completion = list(attempts), exit_code, completion
        self.error_code, self.candidate, self.cache_semantics, self.duration = (
            error_code, candidate, cache_semantics, duration)

    def report(self):
        return _report(self)

    def run(self, request, roots, cancelled, execution_id, started_at):
        cancelled.wait(self.duration)
        now = _now()
        if cancelled.is_set():
            attempt = {"attempt_id": "attempt-1", "outcome": "cancel", "started_at": started_at, "ended_at": now}
            return {"outcome": "cancel", "exit_code": None, "completion": None, "error_code": None,
                    "drain": "confirmed", "started_at": started_at, "ended_at": now, "attempts": [attempt],
                    "candidate": None, "artifacts": [], "usage_events": [], "usage_completeness": "unknown",
                    "limits": _applied(request)}
        attempts, events = [], []
        for i, step in enumerate(self.attempts, 1):
            attempts.append({"attempt_id": f"attempt-{i}", "outcome": step["outcome"], "started_at": started_at,
                             "ended_at": now})
            if step.get("units") is not None:
                events.append({"contract_version": request["contract_version"], "event_id": f"usage-{i}",
                               "attempt_id": f"attempt-{i}", "source": "harness", "kind": "summary",
                               "units": step["units"], "cache_semantics": self.cache_semantics})
        outcome = attempts[-1]["outcome"]
        complete = len(events) == len(attempts) and all(v is not None for e in events for v in e["units"].values())
        limits = _applied(request, self.fired, self.output_truncated)
        error_code = self.error_code if outcome == "error" else None
        if limits is not None and self.fired in ("oom", "pids", "disk"):
            error_code = contract.LIMIT_EXCEEDED
        return {"outcome": outcome, "exit_code": self.exit_code if outcome == "completed" else None,
                "completion": self.completion if outcome == "completed" else None,
                "error_code": error_code, "limits": limits,
                "drain": "unconfirmed" if outcome in ("timeout", "unknown") else "confirmed",
                "started_at": started_at, "ended_at": now, "attempts": attempts,
                "candidate": _seal(self.candidate, roots, request) if self.candidate is not None else None,
                "artifacts": [], "usage_events": events,
                "usage_completeness": "complete" if complete else ("partial" if events else "unknown")}


class ProcessBackend:
    """A real child process in the ``workspace`` directory, in its own process group (POSIX).

    Timeout and cancel terminate the group (SIGTERM, then SIGKILL after ``grace`` seconds). ``drain:
    confirmed`` means the group was observed empty; a descendant that leaves the group (``setsid``) is not
    tracked, which is why this backend is ``controlled`` and never qualified. ``candidate`` names an output
    file relative to the workspace, sealed into the evidence root by digest. Usage is not observed.
    """

    kind = "process"
    isolation_level = "controlled"
    capabilities = ("cancel",)

    def __init__(self, argv, *, candidate=None, env=None, grace=2.0):
        if candidate is not None and (Path(candidate).is_absolute() or ".." in Path(candidate).parts):
            raise ValueError("candidate must be a path inside the workspace")
        self.argv, self.candidate, self.env, self.grace = list(argv), candidate, env, grace

    def report(self):
        return _report(self)

    _stdin = None  # bytes fed to the child's stdin, else /dev/null

    def _argv(self, request, roots):
        return self.argv

    def _output(self, request, roots):
        return subprocess.DEVNULL

    def _finish(self, body, request, roots):
        return body

    def _prepare(self, request, roots):
        """Work before the child starts; False refuses the launch (``error`` / ``LAUNCH_FAILED``)."""
        return True

    def run(self, request, roots, cancelled, execution_id, started_at):
        workspace = Path(roots["workspace"])
        if not self._prepare(request, roots):
            now = _now()
            return self._body("error", None, "confirmed", started_at, now, error_code="LAUNCH_FAILED")
        output = self._output(request, roots)
        try:
            child = subprocess.Popen(self._argv(request, roots), cwd=workspace, env=self.env, start_new_session=True,
                                     stdin=subprocess.DEVNULL if self._stdin is None else subprocess.PIPE,
                                     stdout=output, stderr=subprocess.DEVNULL)
            if self._stdin is not None:  # fed from a thread: a child that never reads still times out
                threading.Thread(target=_feed, args=(child.stdin, self._stdin), daemon=True).start()
        except OSError:
            now = _now()
            return self._body("error", None, "confirmed", started_at, now, error_code="LAUNCH_FAILED")
        finally:
            if output is not subprocess.DEVNULL:
                output.close()
        deadline = time.monotonic() + request["timeout_seconds"]
        outcome = None
        while child.poll() is None:
            if cancelled.is_set():
                outcome = "cancel"
            elif time.monotonic() >= deadline:
                outcome = "timeout"
            if outcome:
                break
            time.sleep(0.05)
        drained = self._stop_group(child)
        ended = _now()
        if outcome is None:
            outcome = "completed"
        elif outcome == "timeout":
            drained = False  # a timeout does not prove descendants are gone (ADR 0002)
        candidate = None
        if outcome == "completed" and self.candidate is not None:
            source = (workspace / self.candidate).resolve()  # a symlink out of the workspace is not sealed
            if source.is_file() and workspace.resolve() in source.parents:
                candidate = _seal(source, roots, request)
        return self._finish(self._body(outcome, child.returncode if outcome == "completed" else None,
                                       "confirmed" if drained else "unconfirmed", started_at, ended,
                                       candidate=candidate), request, roots)

    def _stop_group(self, child):
        """Terminate whatever is left of the child's process group; True when it was observed empty."""
        for sig, wait in ((signal.SIGTERM, self.grace), (signal.SIGKILL, self.grace)):
            if not _group_alive(child.pid):
                break
            try:
                os.killpg(child.pid, sig)
            except ProcessLookupError:
                break
            end = time.monotonic() + wait
            while time.monotonic() < end and _group_alive(child.pid):
                child.poll()
                time.sleep(0.02)
        try:
            child.wait(timeout=self.grace)
        except subprocess.TimeoutExpired:  # the leader outlived SIGKILL: nothing is confirmed
            return False
        return not _group_alive(child.pid)

    @staticmethod
    def _body(outcome, exit_code, drain, started_at, ended_at, *, error_code=None, candidate=None):
        attempt = {"attempt_id": "attempt-1", "outcome": outcome, "started_at": started_at, "ended_at": ended_at}
        return {"outcome": outcome, "exit_code": exit_code, "completion": None, "error_code": error_code,
                "drain": drain, "started_at": started_at, "ended_at": ended_at, "attempts": [attempt],
                "candidate": candidate, "artifacts": [], "usage_events": [], "usage_completeness": "unknown"}


# Credentials and routing that would bill an API or cloud account instead of the CLI's own subscription login.
_BILLING_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_USE_VERTEX", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_API_KEY")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
# Claude Code's own Bash sandbox: no network, never an unsandboxed fallback (`failIfUnavailable`), sandboxed Bash
# allowed without a prompt in `-p` mode. WebFetch/WebSearch run outside the sandbox and are disallowed separately.
# Login files are not readable by sandboxed commands, so a model command cannot copy them into the workspace.
_LOGIN_PATHS = ("~/.claude", "~/.claude.json", "~/.codex", "~/.config/claude")
_CLAUDE_SETTINGS = {"sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
                                "autoAllowBashIfSandboxed": True, "network": {"allowedDomains": []},
                                "filesystem": {"denyRead": list(_LOGIN_PATHS)}},
                    # Every Bash call runs in the sandbox (no unsandboxed fallback), so allowing Bash outright gives
                    # Claude what Codex's workspace-write gives: auto-allow alone still prompts for commands that set
                    # variables (`VAR=x cmd`, `env`, `export`), which blocks typical test commands in `-p` mode.
                    "permissions": {"allow": ["Bash"],
                                    "deny": [f"Read({path}/**)" for path in _LOGIN_PATHS]
                                    + [f"Read({path})" for path in _LOGIN_PATHS]},
                    "forceLoginMethod": "claudeai"}  # the subscription login, never an API key or apiKeyHelper
# Shapes of provider credentials; a candidate or output containing one is withheld.
_SECRET_PATTERNS = (re.compile(rb"sk-ant-[A-Za-z0-9_-]{20,}"), re.compile(rb"\bsk-[A-Za-z0-9_-]{32,}"),
                    re.compile(rb'"(?:refresh_token|access_token|id_token)"\s*:'))
_CODEX_SANDBOX = "workspace-write"


class AgentCliBackend(ProcessBackend):
    """A coding-agent CLI (``claude`` or ``codex``) run as a ``ProcessBackend`` child, on the CLI's own login.

    The command line is fixed per provider; the request's ``provider`` must match and its ``model`` is passed
    through. API-key variables are removed from the child's environment, so a run never bills an API account.
    stdout is kept (up to ``output_limit`` bytes) in the evidence root and its usage report becomes one usage
    summary. Before every launch the CLI's sandbox is probed; if it cannot start, the request is ``rejected``
    with ``CAPABILITY_UNSUPPORTED`` and ``rejection_reason`` says why, with no fallback to another mode.
    ``isolation_level`` stays ``controlled``: the CLI's sandbox is not a qualified target.
    """

    kind = "agent-cli"
    capabilities = ("cancel", "usage")

    def __init__(self, provider, prompt, *, candidate=None, diff_base=None, env=None, grace=2.0,
                 output_limit=8 << 20, sandbox_probe=None):
        if provider not in ("claude", "codex"):
            raise ValueError("provider must be 'claude' or 'codex'")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError("prompt must be a non-empty string")
        env = dict(os.environ if env is None else env)
        for name in _BILLING_ENV:
            env.pop(name, None)
        if candidate is not None and diff_base is not None:
            raise ValueError("candidate and diff_base are exclusive")
        if diff_base is not None and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", diff_base):
            raise ValueError("diff_base must be a full commit id")
        super().__init__([], candidate=candidate, env=env, grace=grace)
        self.diff_base, self.withheld = diff_base, None
        self.provider, self.prompt, self.output_limit = provider, prompt, output_limit
        self._stdin = prompt.encode("utf-8")
        self.sandbox_probe = list(sandbox_probe) if sandbox_probe is not None else _default_probe(provider)
        self.rejection_reasons = {}  # request_id -> why it was refused

    @property
    def rejection_reason(self):
        """The reason of the most recent refusal (``rejection_reasons`` holds them per request)."""
        return next(reversed(self.rejection_reasons.values()), None)

    def refuse(self, request, roots):
        """A contract error code when this request must not launch (nothing started, nothing written)."""
        code, reason = self._refusal(request, roots)
        if code is not None:
            self.rejection_reasons[request["request_id"]] = reason
        return code

    def _refusal(self, request, roots):
        if request["provider"] != self.provider or not _MODEL.fullmatch(request["model"]):
            return "CAPABILITY_UNSUPPORTED", "request provider/model does not match this backend"
        evidence = Path(roots["evidence_root"]).resolve()
        writable = [r for r in _agent_writable_roots(roots["workspace"]) if r == evidence or r in evidence.parents]
        if writable:  # the agent could forge the result or plant Git config in it
            return "CAPABILITY_UNSUPPORTED", f"evidence root is writable by the agent's sandbox ({writable[0]})"
        if shutil.which(self.provider, path=self.env.get("PATH")) is None:
            return "BACKEND_UNAVAILABLE", f"{self.provider} executable not found on PATH"
        try:
            probe = subprocess.run(self.sandbox_probe, check=False, env=self.env, stdin=subprocess.DEVNULL,
                                   capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as error:
            return "CAPABILITY_UNSUPPORTED", f"sandbox probe failed: {error}"
        if probe.returncode != 0:
            detail = (probe.stderr or probe.stdout).decode("utf-8", "replace").strip()[:500]
            return "CAPABILITY_UNSUPPORTED", f"sandbox unavailable (exit {probe.returncode}): {detail}"
        return None, None

    def _argv(self, request, roots):
        model = request["model"]
        # The prompt goes in on stdin, never as an argument, so a prompt such as "--bare" is never a flag.
        if self.provider == "claude":
            settings = json.loads(json.dumps(_CLAUDE_SETTINGS))
            settings["sandbox"]["filesystem"]["denyWrite"] = [str(Path(roots["evidence_root"]).resolve())]
            return ["claude", "-p", "--model", model, "--output-format", "json",
                    "--permission-mode", "acceptEdits", "--settings", json.dumps(settings),
                    "--disallowedTools", "WebFetch,WebSearch"]
        return ["codex", "exec", "-m", model, "--sandbox", _CODEX_SANDBOX, "-c", 'forced_login_method="chatgpt"',
                "--skip-git-repo-check", "--json", "--ephemeral", "-"]

    def _output_path(self, request, roots):
        return Path(roots["evidence_root"]) / f"executions/{_slot(request['request_id'])}/agent-output"

    def _output(self, request, roots):
        path = self._output_path(request, roots)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("wb")

    def _git_dir(self, request, roots):
        return Path(roots["evidence_root"]) / f"executions/{_slot(request['request_id'])}/base.git"

    def _prepare(self, request, roots):
        """Copy the diff base into a Git directory of our own before the agent can touch the workspace's ``.git``."""
        if self.diff_base is None:
            return True
        workspace = Path(roots["workspace"]).resolve()
        git_dir = self._git_dir(request, roots)
        try:
            _git(git_dir, None, "init", "-q", "--bare", str(git_dir))
            _git(git_dir, None, "fetch", "-q", "--no-tags", str(workspace / ".git"), self.diff_base)
            _git(git_dir, None, "cat-file", "-e", f"{self.diff_base}^{{commit}}")
        except (OSError, subprocess.CalledProcessError):
            return False
        return True

    def _finish(self, body, request, roots):
        secrets = _login_secrets(self.env.get("HOME"))
        if self.diff_base is not None and body["outcome"] == "completed" and body["drain"] == "confirmed":
            if _has_hardlinks(Path(roots["workspace"])):  # a hardlink can carry any same-volume host file
                return self._withhold(body, request, roots, "candidate")
            try:
                diff = _workspace_diff(self._git_dir(request, roots), roots["workspace"], self.diff_base)
            except (OSError, subprocess.CalledProcessError):  # e.g. a nested repository without commits
                return self._withhold(body, request, roots, "candidate")
            if _leaks(diff, secrets):
                return self._withhold(body, request, roots, "candidate")
            body["candidate"] = _seal(diff, roots, request)
        if body["candidate"] is not None and _leaks((Path(roots["evidence_root"]) / body["candidate"]["path"])
                                                    .read_bytes(), secrets):
            return self._withhold(body, request, roots, "candidate")
        path = self._output_path(request, roots)
        size = path.stat().st_size
        # ponytail: the limit is checked after exit, so a runaway CLI can still fill the disk; enforce it while
        # streaming if that ever matters.
        if size > self.output_limit:  # unscanned and unparsed: keep nothing, claim nothing
            return self._withhold(body, request, roots, "agent-output")
        data = path.read_bytes()
        if _leaks(data, secrets):
            return self._withhold(body, request, roots, "agent-output")
        body["artifacts"] = [{"name": "agent-output", "path": str(path.relative_to(roots["evidence_root"])),
                              "sha256": "sha256:" + hashlib.sha256(data).hexdigest(), "size": size}]
        units, failed, model = (_claude_report if self.provider == "claude" else _codex_report)(data)
        if units is not None:
            body["usage_events"] = [{"contract_version": request["contract_version"], "event_id": "usage-1",
                                     "attempt_id": "attempt-1", "source": "provider", "kind": "summary",
                                     "units": units,
                                     "cache_semantics": "separate" if self.provider == "claude"
                                     else "included_in_input"}]
            body["usage_completeness"] = "complete" if None not in units.values() else "partial"
        if body["outcome"] == "completed" and failed:
            body.update(outcome="error", exit_code=None, error_code="PROVIDER_ERROR")
            body["attempts"][0]["outcome"] = "error"
        body["resolved_model"] = model
        return body

    def _withhold(self, body, request, roots, what):
        """A credential-shaped string in the output: keep neither candidate nor output, report an error."""
        for name in ("candidate", "agent-output"):
            (Path(roots["evidence_root"]) / f"executions/{_slot(request['request_id'])}/{name}").unlink(missing_ok=True)
        self.withheld = what
        body.update(outcome="error", exit_code=None, completion=None, error_code="PROVIDER_ERROR", candidate=None,
                    artifacts=[], usage_events=[], usage_completeness="unknown")
        body["attempts"][0]["outcome"] = "error"
        return body


def _agent_writable_roots(workspace):
    """Directories the CLI sandboxes let the agent write: the workspace, and /tmp and $TMPDIR (Codex default)."""
    roots = {Path(workspace).resolve(), Path("/tmp").resolve(), Path(tempfile.gettempdir()).resolve()}
    if os.environ.get("TMPDIR"):
        roots.add(Path(os.environ["TMPDIR"]).resolve())
    return sorted(roots)


def _default_probe(provider):
    if provider == "codex":  # one command in the sandbox mode the run uses (codex-cli 0.160: `codex sandbox -- cmd`)
        return ["codex", "sandbox", "-c", f'sandbox_mode="{_CODEX_SANDBOX}"', "--", "true"]
    if sys.platform != "darwin":  # Claude Code's Linux sandbox needs bubblewrap and socat; macOS uses Seatbelt
        return ["sh", "-c", "command -v bwrap >/dev/null && command -v socat >/dev/null && bwrap --ro-bind / / true"]
    return ["claude", "--version"]


def _claude_report(data):
    """(units, failed, model) from ``claude -p --output-format json``; units None when unreadable."""
    try:
        doc = json.loads(data)
        usage = doc["usage"]
        units = {"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
                 "cache_read_tokens": usage.get("cache_read_input_tokens"),
                 "cache_write_tokens": usage.get("cache_creation_input_tokens")}
    except (ValueError, KeyError, TypeError):
        return None, True, None
    if any(v is not None and (type(v) is not int or v < 0) for v in units.values()):
        return None, True, None
    models = doc.get("modelUsage")
    model = next(iter(models)) if isinstance(models, dict) and len(models) == 1 else None
    return units, doc.get("is_error") is not False or doc.get("subtype") != "success", model


def _codex_report(data):
    """(units, failed, model) from the last ``turn.completed`` of ``codex exec --json``.

    Codex reports the thread's running total in every ``turn.completed``, so the last one is the run's usage.
    """
    usage, failed = None, False
    for line in data.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type") if isinstance(event, dict) else None
        if kind in ("turn.failed", "error"):
            failed = True
        elif kind == "turn.completed":
            usage = event.get("usage") or {}
    if usage is None:
        return None, True, None
    # cache writes are reported by newer CLIs only (codex-cli 0.160: cache_write_input_tokens)
    units = {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
             "cache_read_tokens": usage.get("cached_input_tokens", 0),
             "cache_write_tokens": usage.get("cache_write_input_tokens")}
    required = [units[k] for k in ("input_tokens", "output_tokens", "cache_read_tokens")]
    if any(type(v) is not int or v < 0 for v in required) or (
            units["cache_write_tokens"] is not None and
            (type(units["cache_write_tokens"]) is not int or units["cache_write_tokens"] < 0)):
        return None, True, None
    return units, failed, None


def _has_hardlinks(workspace):
    for path in workspace.rglob("*"):
        if ".git" in path.relative_to(workspace).parts:
            continue
        info = path.lstat()
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            return True
    return False


def _login_secrets(home):
    """String values (20+ chars) of the CLIs' login files under ``home``; matched literally in outputs."""
    values = set()
    if not home:
        return values
    for relative in (".codex/auth.json", ".claude/.credentials.json", ".claude.json"):
        try:
            doc = json.loads((Path(home) / relative).read_text())
        except (OSError, ValueError):
            continue
        stack = [doc]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, str) and len(item) >= 20:
                values.add(item.encode())
    return values


def _leaks(data, secrets):
    return any(value in data for value in secrets) or any(p.search(data) for p in _SECRET_PATTERNS)


# Git after the agent ran: our own GIT_DIR, no user/system config, no hooks, fsmonitor, filters, external diff or
# textconv, and a minimal environment, so nothing the agent wrote (.git/config, .gitattributes) runs on the host.
_GIT_HARDENING = ("-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "core.attributesFile=/dev/null",
                  "-c", "diff.external=", "-c", "core.sshCommand=false", "-c", "protocol.allow=never",
                  "-c", "protocol.file.allow=always")


def _git(git_dir, work_tree, *args, index=None):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent", "LC_ALL": "C",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
           "GIT_DIR": str(git_dir)}
    if work_tree is not None:
        env["GIT_WORK_TREE"] = str(work_tree)
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    return subprocess.run(["git", *_GIT_HARDENING, *args], env=env, check=True, capture_output=True,
                          stdin=subprocess.DEVNULL).stdout


def _workspace_diff(git_dir, workspace, base):
    """``git diff --binary <base>`` of the whole workspace (untracked included, ``.git`` never) from our GIT_DIR."""
    with tempfile.TemporaryDirectory() as tmp:
        index = Path(tmp) / "index"
        _git(git_dir, workspace, "read-tree", base, index=index)
        _git(git_dir, workspace, "add", "-A", "--", ".", ":(exclude).git", index=index)
        return _git(git_dir, workspace, "diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", base,
                    index=index)


def _feed(pipe, data):
    try:
        pipe.write(data)
    except OSError:
        pass
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _now():
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --- Qualified grading target (AH5-04c-2) -------------------------------------------------------------------------

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
                 reviewed=None, docker="docker", grace=2.0):
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
        try:
            engine = self._docker("version", "--format", "{{.Server.Version}}").strip()
            host, kernel = self._docker("info", "--format", "{{.OperatingSystem}}|{{.KernelVersion}}").strip() \
                .split("|", 1)
            repo_digests = json.loads(self._docker("image", "inspect", "--format", "{{json .RepoDigests}}",
                                                   self.image) or "[]")
        except (OSError, subprocess.SubprocessError, ValueError):
            return "BACKEND_UNAVAILABLE", "docker is not reachable or the image is not present"
        want = self.qualification["tuple"]
        live = {"engine": f"DockerEngine_{engine}", "host": host.replace(" ", "_"), "kernel": kernel}
        drift = [k for k, v in live.items() if want[k] != v]
        if self.image not in (repo_digests or []):
            drift.append("workload_image")
        if drift:  # the qualification covers exactly its tuple
            return "NOT_QUALIFIED", f"live target differs from the qualified tuple: {sorted(drift)}"
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
