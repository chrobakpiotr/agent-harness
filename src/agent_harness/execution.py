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
import http.server
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
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
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

    def _env(self, request, roots):
        return self.env

    def _finish(self, body, request, roots):
        return body

    def _prepare(self, request, roots):
        """Work before the child starts; False refuses the launch (``error`` / ``LAUNCH_FAILED``)."""
        return True

    def _errors(self, request, roots):
        """Where the child's stderr goes: discarded unless a backend keeps it as a diagnostic artifact."""
        return subprocess.DEVNULL

    def run(self, request, roots, cancelled, execution_id, started_at):
        workspace = Path(roots["workspace"])
        if not self._prepare(request, roots):
            now = _now()
            return self._body("error", None, "confirmed", started_at, now, error_code="LAUNCH_FAILED")
        output = self._output(request, roots)
        errors = self._errors(request, roots)
        try:
            child = subprocess.Popen(self._argv(request, roots), cwd=workspace, env=self._env(request, roots),
                                     start_new_session=True,
                                     stdin=subprocess.DEVNULL if self._stdin is None else subprocess.PIPE,
                                     stdout=output, stderr=errors)
            if self._stdin is not None:  # fed from a thread: a child that never reads still times out
                threading.Thread(target=_feed, args=(child.stdin, self._stdin), daemon=True).start()
        except OSError:
            now = _now()
            return self._body("error", None, "confirmed", started_at, now, error_code="LAUNCH_FAILED")
        finally:
            for stream in (output, errors):
                if stream is not subprocess.DEVNULL:
                    stream.close()
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
                "CLAUDE_CODE_USE_VERTEX", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_API_KEY", "OPENROUTER_API_KEY")
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
# Consumer launch options (AH5-12b): additive and validated; without them a run is exactly what it was before.
# None of them can loosen the library minimum: no web tools, sandbox required, login only, evidence not writable.
_CLI_OPTIONS = {
    "codex": {"sandbox_mode", "output_schema", "reasoning", "login_method", "strict"},
    "claude": {"sandbox_mode", "output_schema", "tools", "denied_commands", "deny_read", "max_turns",
               "max_budget_usd", "restricted", "permission_mode"},
    "opencode": set(),
}
_CLAUDE_TOOLS = {"Read", "Edit", "Write", "Glob", "Grep", "Bash", "NotebookEdit", "TodoWrite"}
_CLAUDE_WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}


def _cli_options(provider, options):
    """Validate a consumer's launch options; a malformed or loosening option refuses the backend."""
    options = dict(options or {})
    unknown = set(options) - _CLI_OPTIONS[provider]
    if unknown:
        raise ValueError(f"cli_options not supported for {provider}: {sorted(unknown)}")
    def check(ok, what):
        if not ok:
            raise ValueError(f"invalid cli_options.{what}")
    if "sandbox_mode" in options:
        check(options["sandbox_mode"] in ("read-only", "workspace-write"), "sandbox_mode")
    if "output_schema" in options:
        check(isinstance(options["output_schema"], dict) and options["output_schema"], "output_schema")
    if "reasoning" in options:
        check(options["reasoning"] in ("minimal", "low", "medium", "high", "xhigh"), "reasoning")
    if "login_method" in options:  # the library default, passed explicitly by a consumer; nothing else
        check(options["login_method"] == "chatgpt", "login_method")
    for flag in ("strict", "restricted"):
        if flag in options:
            check(isinstance(options[flag], bool), flag)
    if "tools" in options:
        tools = options["tools"]
        check(isinstance(tools, (list, tuple)) and tools and set(tools) <= _CLAUDE_TOOLS
              and len(set(tools)) == len(tools), "tools")
        check(options.get("sandbox_mode") != "read-only" or not set(tools) & _CLAUDE_WRITE_TOOLS, "tools")
    for name in ("denied_commands", "deny_read"):
        if name in options:
            value = options[name]
            check(isinstance(value, (list, tuple)) and all(isinstance(v, str) and v and "\0" not in v
                                                           for v in value), name)
    if "denied_commands" in options:
        check(all(v.startswith("Bash(") and v.endswith(")") for v in options["denied_commands"]), "denied_commands")
    if "max_turns" in options:
        check(type(options["max_turns"]) is int and options["max_turns"] >= 1, "max_turns")
    if "max_budget_usd" in options:
        check(type(options["max_budget_usd"]) in (int, float) and options["max_budget_usd"] > 0, "max_budget_usd")
    if "permission_mode" in options:  # only the stricter mode may replace the library's acceptEdits
        check(options["permission_mode"] == "dontAsk", "permission_mode")
    return options
# OpenCode has no sandbox of its own: it runs under a macOS Seatbelt profile whose only network is a harness-owned
# relay on 127.0.0.1 (`_OpenRouterRelay`), which holds the OpenRouter key and forwards only the run's `:free` model.
# Shell commands inherit the profile: no network, no DNS, no Docker socket, no Keychain, no LaunchServices
# (`open URL`), no login files, writes only where the other CLIs' sandboxes allow them (`_agent_writable_roots`).
# ponytail: an allow-default profile with targeted denies; a deny-default profile is the upgrade if a hole shows up.
_OPENCODE_SECRETS = ("~/.local/share/opencode", "~/.config/opencode", "~/Library/Keychains")
_OPENCODE_ENV = {"OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
                 "OPENCODE_DISABLE_SHARE": "1", "OPENCODE_DISABLE_CLAUDE_CODE": "1",
                 "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1", "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
                 "OPENCODE_DISABLE_PROJECT_CONFIG": "1"}
_OPENROUTER = "https://openrouter.ai/api/v1"


class AgentCliBackend(ProcessBackend):
    """A coding-agent CLI (``claude``, ``codex`` or ``opencode``) run as a ``ProcessBackend`` child, on the CLI's own login.

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
                 output_limit=8 << 20, sandbox_probe=None, api_key_file=None, upstream=_OPENROUTER,
                 cli_options=None, isolation_probe=None):
        if provider not in ("claude", "codex", "opencode"):
            raise ValueError("provider must be 'claude', 'codex' or 'opencode'")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError("prompt must be a non-empty string")
        env = dict(os.environ if env is None else env)
        # OpenCode's key: a mode-600 file, else OPENROUTER_API_KEY; read at launch, never passed to the child.
        self.api_key_file, self._env_key, self.upstream = api_key_file, env.get("OPENROUTER_API_KEY"), upstream
        for name in _BILLING_ENV:
            env.pop(name, None)
        if provider == "opencode":
            for name in [n for n in env if n.startswith("OPENCODE_")]:
                env.pop(name)
        if candidate is not None and diff_base is not None:
            raise ValueError("candidate and diff_base are exclusive")
        if diff_base is not None and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", diff_base):
            raise ValueError("diff_base must be a full commit id")
        super().__init__([], candidate=candidate, env=env, grace=grace)
        self.diff_base, self.withheld = diff_base, None
        self.error_details = {}  # request_id -> {"kind": "quota_exhausted"|"provider_error", "resets_at", "message"}
        self.provider, self.prompt, self.output_limit = provider, prompt, output_limit
        self._stdin = prompt.encode("utf-8")
        self.sandbox_probe = list(sandbox_probe) if sandbox_probe is not None else _default_probe(provider)
        self.cli_options = _cli_options(provider, cli_options)
        # Tool availability (sandbox_probe) is not isolation: the isolation probe must show the sandbox blocking a
        # write. A caller that supplies its own sandbox_probe (a test double) gets no default isolation probe.
        if isolation_probe is not None:
            self.isolation_probe = list(isolation_probe)
        else:
            self.isolation_probe = (_default_isolation_probe(provider, self.cli_options.get("sandbox_mode"))
                                    if sandbox_probe is None else None)
        self.rejection_reasons = {}  # request_id -> why it was refused
        self.request_counts = {}  # request_id -> requests the OpenCode relay forwarded to OpenRouter

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
        reason = _evidence_exposure(roots)
        if reason is not None:  # the agent could forge the result or plant Git config in it
            return "CAPABILITY_UNSUPPORTED", reason
        if shutil.which(self.provider, path=self.env.get("PATH")) is None:
            return "BACKEND_UNAVAILABLE", f"{self.provider} executable not found on PATH"
        if self.provider == "opencode":
            if not request["model"].endswith(":free") or request["model"].startswith("openrouter/"):
                return "CAPABILITY_UNSUPPORTED", "opencode runs only OpenRouter `:free` models (`vendor/model:free`)"
            if self._api_key() is None:
                return "BACKEND_UNAVAILABLE", "no OpenRouter key: api_key_file (mode 600) or OPENROUTER_API_KEY"
            profile_paths = (roots["workspace"], tempfile.gettempdir(), self.env.get("HOME") or "", self.api_key_file or "")
            if any(c in str(p) for p in profile_paths for c in '"\\'):
                return "CAPABILITY_UNSUPPORTED", "a path cannot be written into the sandbox profile"
        try:
            probe = subprocess.run(self.sandbox_probe, check=False, env=self.env, stdin=subprocess.DEVNULL,
                                   capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as error:
            return "CAPABILITY_UNSUPPORTED", f"sandbox probe failed: {error}"
        if probe.returncode != 0:
            detail = (probe.stderr or probe.stdout).decode("utf-8", "replace").strip()[:500]
            return "CAPABILITY_UNSUPPORTED", f"sandbox unavailable (exit {probe.returncode}): {detail}"
        if self.isolation_probe is not None:
            reason = _isolation_refusal(self.isolation_probe, self.env, roots)
            if reason is not None:
                return "CAPABILITY_UNSUPPORTED", reason
        return None, None

    def _claude_settings(self, roots):
        """The library minimum merged with the consumer's options; the merge only ever adds restrictions."""
        options = self.cli_options
        settings = json.loads(json.dumps(_CLAUDE_SETTINGS))
        settings["sandbox"]["filesystem"]["denyWrite"] = [str(Path(roots["evidence_root"]).resolve())]
        settings["sandbox"]["filesystem"]["denyRead"] += list(options.get("deny_read", ()))
        if options.get("sandbox_mode") == "read-only":  # no write through tools, nor through sandboxed Bash
            settings["sandbox"]["filesystem"]["denyWrite"].append(str(Path(roots["workspace"]).resolve()))
            settings["permissions"]["deny"] += sorted(_CLAUDE_WRITE_TOOLS)
        return settings

    def _argv(self, request, roots):
        model = request["model"]
        options = self.cli_options
        # The prompt goes in on stdin, never as an argument, so a prompt such as "--bare" is never a flag.
        if self.provider == "claude":
            argv = ["claude", "-p"]
            if options.get("restricted"):
                argv += ["--restricted", "--strict-mcp-config", "--no-session-persistence"]
            argv += ["--model", model, "--output-format", "json",
                     "--permission-mode", options.get("permission_mode", "acceptEdits"),
                     "--settings", json.dumps(self._claude_settings(roots)),
                     "--disallowedTools", ",".join(["WebFetch", "WebSearch", *options.get("denied_commands", ())])]
            if "tools" in options:
                argv += ["--tools", ",".join(options["tools"]),
                         "--allowedTools", *[t for t in options["tools"] if t != "Bash"]]
            if "output_schema" in options:
                argv += ["--json-schema", json.dumps(options["output_schema"], separators=(",", ":"), sort_keys=True)]
            if "max_turns" in options:
                argv += ["--max-turns", str(options["max_turns"])]
            if "max_budget_usd" in options:
                argv += ["--max-budget-usd", str(options["max_budget_usd"])]
            return argv
        if self.provider == "opencode":  # `--title` skips a title request, one of the 50 free requests a day
            return ["/usr/bin/sandbox-exec", "-p", self._profile(roots), shutil.which("opencode", path=self.env.get("PATH")),
                    "run", "--format", "json", "--pure", "--title", request["request_id"][:64], "-m",
                    f"openrouter/{model}"]
        argv = ["codex", "exec", "-m", model, "--sandbox", options.get("sandbox_mode", _CODEX_SANDBOX),
                "-c", 'forced_login_method="chatgpt"', "--skip-git-repo-check", "--json", "--ephemeral"]
        if options.get("strict"):  # no approvals to wait for, no web search, no network from the sandbox
            argv += ["-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
                     "-c", "sandbox_workspace_write.network_access=false"]
        if "reasoning" in options:
            argv += ["-c", f'model_reasoning_effort="{options["reasoning"]}"']
        if "output_schema" in options:
            execution_dir = Path(roots["evidence_root"]) / f"executions/{_slot(request['request_id'])}"
            argv += ["--output-schema", str(execution_dir / "output-schema.json"),
                     "--output-last-message", str(execution_dir / "last-message")]
        return [*argv, "-"]

    def _launch_record(self, request, roots):
        """What was asked of the CLI, without the prompt or any secret. Authentication is the *requested* mode only:
        the library cannot see how the provider bills a run, so it never claims a billing source."""
        record = {"schema_version": 1, "provider": self.provider, "argv": self._argv(request, roots),
                  "prompt": {"channel": "stdin", "encoding": "utf-8", "sha256": hashlib.sha256(self._stdin).hexdigest(),
                             "bytes": len(self._stdin)},
                  "credential_environment": "removed", "removed_variables": sorted(_BILLING_ENV),
                  "requested_login": {"claude": "forceLoginMethod=claudeai", "codex": 'forced_login_method="chatgpt"',
                                      "opencode": "harness relay (OpenRouter key held by the relay)"}[self.provider],
                  "billing_source_confirmed": False, "cli_options": sorted(self.cli_options)}
        if self.provider == "claude":
            settings = self._claude_settings(roots)
            tools = set(self.cli_options.get("tools", _CLAUDE_TOOLS))
            denied = set(settings["permissions"]["deny"])
            record["effective_permissions"] = {
                "tools": sorted(tools - denied), "write_tools": sorted((tools & _CLAUDE_WRITE_TOOLS) - denied),
                "sandbox_deny_write": settings["sandbox"]["filesystem"]["denyWrite"],
                "sandbox_deny_read": settings["sandbox"]["filesystem"]["denyRead"],
                "network_allowed_domains": settings["sandbox"]["network"]["allowedDomains"],
                "unsandboxed_commands": settings["sandbox"]["allowUnsandboxedCommands"],
                "web_tools_disallowed": True, "mcp": "strict" if self.cli_options.get("restricted") else "default"}
        return record

    def _api_key(self):
        if self.api_key_file is None:
            return self._env_key or None
        try:
            path = Path(self.api_key_file).expanduser()
            if path.stat().st_mode & 0o077:  # readable by others: refuse rather than use it
                return None
            return path.read_text().strip() or None
        except OSError:
            return None

    def _profile(self, roots):
        home = Path(self.env.get("HOME") or Path.home())
        secrets = [home / p.removeprefix("~/") for p in _LOGIN_PATHS + _OPENCODE_SECRETS]
        if self.api_key_file is not None:
            secrets.append(Path(self.api_key_file).expanduser().resolve())
        mine = [Path(roots["workspace"]).resolve(), self._private.resolve()]
        writable = _agent_writable_roots(roots["workspace"]) + mine

        def paths(items):
            return " ".join(f'(subpath "{p}")' for p in items)
        return "\n".join([
            "(version 1)", "(allow default)", "(deny network*)",
            '(allow network-bind network-inbound (local ip "localhost:*"))',  # OpenCode's in-process server
            f'(allow network-outbound (remote ip "localhost:{self._relay.port}"))',
            ('(deny mach-lookup (global-name "com.apple.dnssd.service") (global-name "com.apple.SecurityServer")'
             ' (global-name "com.apple.securityd.xpc") (global-name-prefix "com.apple.coreservices.")'
             ' (global-name-prefix "com.apple.lsd."))'),
            f"(deny file-read* {paths(secrets)})", f"(allow file-read* {paths(mine)})",
            "(deny file-write*)", f'(allow file-write* {paths(writable)} (subpath "/dev"))'])

    def _env(self, request, roots):
        if self.provider != "opencode":
            return self.env
        config = {"autoupdate": False, "share": "disabled", "small_model": f"openrouter/{request['model']}",
                  "provider": {"openrouter": {"options": {"baseURL": f"http://127.0.0.1:{self._relay.port}/api/v1",
                                                          "apiKey": "harness-relay"},
                                              "models": {request["model"]: {"tool_call": True}}}},
                  # the OS sandbox is the boundary, as for the other CLIs; web tools would only waste requests
                  "permission": {"*": "allow", "webfetch": "deny", "websearch": "deny"}}
        # OpenCode takes its project directory from PWD, not from the process's working directory
        env = {**self.env, **_OPENCODE_ENV, "OPENCODE_CONFIG_CONTENT": json.dumps(config),
               "PWD": str(Path(roots["workspace"]).resolve())}
        for name in ("CONFIG", "DATA", "STATE", "CACHE"):  # OpenCode's own config, auth and sessions: private
            env[f"XDG_{name}_HOME"] = str(self._private / name.lower())
        return env

    def run(self, request, roots, cancelled, execution_id, started_at):
        if self.provider != "opencode":
            return super().run(request, roots, cancelled, execution_id, started_at)
        self._relay = _OpenRouterRelay(self._api_key(), request["model"], self.upstream)
        self._private = Path(tempfile.mkdtemp(prefix="agent-harness-opencode-"))
        try:
            return super().run(request, roots, cancelled, execution_id, started_at)
        finally:
            self.request_counts[request["request_id"]] = self._relay.requests
            self._relay.close()
            shutil.rmtree(self._private, ignore_errors=True)

    def _output_path(self, request, roots):
        return Path(roots["evidence_root"]) / f"executions/{_slot(request['request_id'])}/agent-output"

    def _output(self, request, roots):
        path = self._output_path(request, roots)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("wb")

    def _errors(self, request, roots):
        """D3: the CLI's stderr is kept as a size-bounded diagnostic artifact, checked for secrets like stdout."""
        path = self._output_path(request, roots).parent / "agent-stderr"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("wb")

    def _git_dir(self, request, roots):
        return Path(roots["evidence_root"]) / f"executions/{_slot(request['request_id'])}/base.git"

    def _prepare(self, request, roots):
        """Record the launch, then copy the diff base into our own Git directory before the agent can touch ``.git``."""
        execution_dir = Path(roots["evidence_root"]) / f"executions/{_slot(request['request_id'])}"
        execution_dir.mkdir(parents=True, exist_ok=True)
        if self.provider == "codex" and "output_schema" in self.cli_options:
            (execution_dir / "output-schema.json").write_text(json.dumps(self.cli_options["output_schema"]))
        (execution_dir / "launch.json").write_text(json.dumps(self._launch_record(request, roots), indent=2,
                                                              sort_keys=True) + "\n")
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
        if self.provider == "opencode" and self._relay.key:
            secrets.add(self._relay.key.encode())
        withheld = []  # (artifact kind, reason code, observed bytes or None, limit bytes or None)
        execution_dir = self._output_path(request, roots).parent
        if self.diff_base is not None and body["outcome"] == "completed" and body["drain"] == "confirmed":
            if _has_hardlinks(Path(roots["workspace"])):  # a hardlink can carry any same-volume host file
                withheld.append(("candidate", "CANDIDATE_UNSAFE_OBJECT", None, None))
            else:
                try:
                    diff = _workspace_diff(self._git_dir(request, roots), roots["workspace"], self.diff_base)
                except (OSError, subprocess.CalledProcessError):  # e.g. a nested repository without commits
                    withheld.append(("candidate", "CANDIDATE_UNAVAILABLE", None, None))
                else:
                    if _leaks(diff, secrets):
                        withheld.append(("candidate", "CREDENTIAL_SHAPED_OUTPUT", None, None))
                    else:
                        body["candidate"] = _seal(diff, roots, request)
        if body["candidate"] is not None and _leaks((Path(roots["evidence_root"]) / body["candidate"]["path"])
                                                    .read_bytes(), secrets):
            withheld.append(("candidate", "CREDENTIAL_SHAPED_OUTPUT", None, None))
        published = []
        for name in ("agent-output", "agent-stderr", "last-message"):
            item = execution_dir / name
            if not item.is_file() or (name == "last-message" and not (self.provider == "codex"
                                                                     and "output_schema" in self.cli_options)):
                continue
            size = item.stat().st_size
            if name == "agent-stderr" and size == 0:  # nothing to diagnose: no empty artifact
                item.unlink()
                continue
            # ponytail: the limit is checked after exit, so a runaway CLI can still fill the disk; enforce it while
            # streaming if that ever matters.
            if size > self.output_limit:  # unscanned and unparsed: keep nothing, claim nothing
                withheld.append((name, "OUTPUT_LIMIT_EXCEEDED", size, self.output_limit))
                continue
            content = item.read_bytes()
            if _leaks(content, secrets):
                withheld.append((name, "CREDENTIAL_SHAPED_OUTPUT", None, None))
                continue
            published.append((name, item, content))
        withheld_kinds = {kind for kind, *_ in withheld}
        if withheld_kinds & {"candidate", "agent-output"}:
            # Neither the candidate nor the output is published; the process outcome stays what it was.
            published = [entry for entry in published if entry[0] == "agent-stderr"]
            withheld += [(kind, "WITHHELD_WITH_OUTPUT", None, None)
                         for kind in ("candidate", "agent-output", "last-message")
                         if kind not in withheld_kinds and (kind != "candidate" or body["candidate"] is not None)
                         and (kind == "candidate" or (execution_dir / kind).is_file())]
        for kind, *_ in withheld:  # nothing withheld stays on disk
            (execution_dir / kind).unlink(missing_ok=True)
        if any(kind == "candidate" for kind, *_ in withheld) and body["candidate"] is not None:
            (Path(roots["evidence_root"]) / body["candidate"]["path"]).unlink(missing_ok=True)
            body["candidate"] = None
        body["artifacts"] = [{"name": name, "path": str(item.relative_to(roots["evidence_root"])),
                              "sha256": "sha256:" + hashlib.sha256(content).hexdigest(), "size": len(content)}
                             for name, item, content in published]
        if withheld:
            body["artifacts"].append(self._withheld_record(request, roots, withheld))
            self.withheld = withheld[0][0]
        output = next((content for name, _item, content in published if name == "agent-output"), None)
        if output is None:  # no trusted output: no usage, no provider signals, no resolved model claimed
            body["completion"] = None
            return body
        data = output
        units, failed, model = {"claude": _claude_report, "codex": _codex_report,
                                "opencode": _opencode_report}[self.provider](data)
        if units is not None:
            body["usage_events"] = [{"contract_version": request["contract_version"], "event_id": "usage-1",
                                     "attempt_id": "attempt-1", "source": "provider", "kind": "summary",
                                     "units": units,
                                     "cache_semantics": "included_in_input" if self.provider == "codex"
                                     else "separate"}]
            body["usage_completeness"] = "complete" if None not in units.values() else "partial"
        if body["outcome"] == "completed" and failed:
            body.update(outcome="error", exit_code=None, error_code="PROVIDER_ERROR")
            body["attempts"][0]["outcome"] = "error"
            quota = self._relay.quota if self.provider == "opencode" else None
            self.error_details[request["request_id"]] = quota or _provider_error(data)
        if body["outcome"] != "completed" and body["candidate"] is not None:  # only a completed run has a candidate
            (Path(roots["evidence_root"]) / body["candidate"]["path"]).unlink(missing_ok=True)
            body["candidate"] = None
        body["resolved_model"] = model
        return body

    def _withheld_record(self, request, roots, withheld):
        """D4/D6: `withheld.json` names what was not published and why; never content, matches or secret values."""
        path = self._output_path(request, roots).parent / "withheld.json"
        primary = next(entry for entry in withheld if entry[1] != "WITHHELD_WITH_OUTPUT")
        # Format agreed with agent-benchmark: flat fields for the primary reason, per-artifact detail apart.
        record = {"schema_version": 1, "reason_code": primary[1], "artifacts": [kind for kind, *_ in withheld],
                  "size": primary[2], "limit": primary[3], "size_is_lower_bound": False,
                  "details": [{"artifact": kind, "reason_code": reason, "size": observed, "limit": limit,
                               "size_is_lower_bound": False} for kind, reason, observed, limit in withheld],
                  "secret_detected": False,  # a credential-shaped string is a pattern match, not a confirmed secret
                  "note": "process outcome, artifact publication and task-result acceptance are separate facts"}
        data = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode()
        path.write_bytes(data)
        return {"name": "withheld", "path": str(path.relative_to(roots["evidence_root"])),
                "sha256": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data)}


def _agent_writable_roots(workspace):
    """Directories the CLI sandboxes let the agent write: the workspace, and /tmp and $TMPDIR (Codex default)."""
    roots = {Path(workspace).resolve(), Path("/tmp").resolve(), Path(tempfile.gettempdir()).resolve()}
    if os.environ.get("TMPDIR"):
        roots.add(Path(os.environ["TMPDIR"]).resolve())
    return sorted(roots)


_ISOLATION_MARKER = "agent-harness-isolation-probe-ran"


def _evidence_exposure(roots):
    """Why the evidence root is reachable by agent writes, judged by real paths; None when it is not (D5).

    The resolved evidence root must lie outside every root the sandboxes let the agent write, and no symlink on the
    path the harness writes through may sit in such a root (the agent could repoint it after this check).
    """
    workspace = Path(roots["workspace"]).resolve()
    writable = _agent_writable_roots(roots["workspace"])

    def inside(path):
        return next((r for r in writable if r == path or r in path.parents), None)
    lexical = Path(roots["evidence_root"])
    for part in [*reversed(lexical.parents), lexical]:
        if part.is_symlink() and inside(part.parent.resolve()) is not None:
            return f"evidence path goes through a symlink the agent can replace ({part})"
    root = inside(lexical.resolve())
    if root is None:
        return None
    reason = f"evidence root is writable by the agent's sandbox ({root})"
    if root == workspace and (workspace / ".git").is_dir():
        reason += ("; the workspace is a primary checkout: run the agent in an isolated linked worktree "
                   "(`git worktree add`) and keep the evidence root outside it")
    return reason


def _isolation_refusal(template, env, roots):
    """None when the sandbox demonstrably ran a command and blocked its write into the evidence root; else why not.

    The template runs in the workspace. Its ``{target}`` is a fresh file in the evidence root (in its nearest existing
    ancestor before the first run), ``{dir}`` that directory, ``{workspace}`` the workspace and ``{writable}`` the
    Seatbelt rules for the roots the agent may write; an argument ``{binds}`` becomes bubblewrap binds for them. The
    command must print the marker (it ran inside the sandbox) and must not create the target (the sandbox protected
    the evidence). A sandbox that fails before running anything does not pass by accident.
    """
    probe_dir = Path(roots["evidence_root"])
    while not probe_dir.is_dir():
        probe_dir = probe_dir.parent
    target = probe_dir / f".agent-harness-write-probe-{uuid.uuid4().hex}"
    writable = _agent_writable_roots(roots["workspace"])
    values = {"{dir}": str(probe_dir), "{target}": str(target), "{workspace}": str(Path(roots["workspace"]).resolve()),
              "{writable}": " ".join(f'(subpath "{r}")' for r in writable)}
    argv = []
    for part in template:
        if part == "{binds}":
            argv += [arg for r in writable for arg in ("--bind", str(r), str(r))]
            continue
        for key, value in values.items():
            part = part.replace(key, value)
        argv.append(part)
    try:
        run = subprocess.run(argv, check=False, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
                             cwd=roots["workspace"])
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"sandbox isolation check could not run: {error}"
    finally:
        created = target.exists() or target.is_symlink()
        target.unlink(missing_ok=True)
    if _ISOLATION_MARKER not in run.stdout.decode("utf-8", "replace"):
        detail = (run.stderr or run.stdout).decode("utf-8", "replace").strip()[:300]
        return f"sandbox isolation check did not run inside the sandbox (exit {run.returncode}): {detail}"
    if created:
        return "sandbox isolation check: the agent's sandbox let a write into the evidence root through"
    return None


def _default_isolation_probe(provider, sandbox_mode=None):
    """A command in the sandbox the run uses (its writable roots) that prints the marker and writes into evidence."""
    attempt = ["sh", "-c", f"echo {_ISOLATION_MARKER}; echo x > {{target}}"]
    if provider == "codex":  # the run's own mode, run from the workspace as `codex exec` is
        return ["codex", "sandbox", "-c", f'sandbox_mode="{sandbox_mode or _CODEX_SANDBOX}"', "--", *attempt]
    if sys.platform == "darwin":  # Seatbelt, which Claude Code's sandbox (and OpenCode's profile) use on macOS
        return ["/usr/bin/sandbox-exec", "-p",
                '(version 1)(allow default)(deny file-write*)(allow file-write* {writable} (subpath "/dev"))', *attempt]
    # Linux: bubblewrap, which Claude Code's sandbox uses: a read-only root with the agent's writable roots bound
    return ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "{binds}", "--unshare-net", *attempt]


def _default_probe(provider):
    if provider == "opencode":  # Seatbelt only; there is no Linux equivalent wired up, so the probe fails there
        if sys.platform != "darwin":
            return ["sh", "-c", "echo 'opencode runs only under the macOS Seatbelt sandbox' >&2; exit 1"]
        return ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)(deny network*)", "/usr/bin/true"]
    if provider == "codex":  # one command in the sandbox mode the run uses (codex-cli 0.160: `codex sandbox -- cmd`)
        return ["codex", "sandbox", "-c", f'sandbox_mode="{_CODEX_SANDBOX}"', "--", "true"]
    if sys.platform != "darwin":  # Claude Code's Linux sandbox needs bubblewrap and socat; macOS uses Seatbelt
        return ["sh", "-c", "command -v bwrap >/dev/null && command -v socat >/dev/null && bwrap --ro-bind / / true"]
    return ["claude", "--version"]


_QUOTA = re.compile(r"usage limit|rate limit|quota|limit reached|hit your (?:usage )?limit|session limit", re.IGNORECASE)


def _provider_error(data):
    """Classify a provider failure from the CLI's own output: subscription quota exhausted (with the reset time when
    the CLI states it as a Unix time, e.g. Claude's ``usage limit reached|<epoch>``) or any other provider error."""
    text = data.decode("utf-8", "replace")
    messages = []
    try:
        doc = json.loads(text)
        if isinstance(doc, dict):
            messages.append(str(doc.get("result", "")))
    except ValueError:
        for line in text.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") in ("error", "turn.failed"):
                error = event.get("error")
                if isinstance(error, dict):  # OpenCode: {"name": "APIError", "data": {"message": …}}
                    data = error.get("data")
                    messages.append(str(error.get("message") or (data.get("message", "") if isinstance(data, dict) else "")))
                else:
                    messages.append(str(event.get("message", "")))
    message = " ".join(m for m in messages if m)[:300]
    return {"kind": "quota_exhausted" if _QUOTA.search(message) else "provider_error",
            "resets_at": _reset_time(message), "message": message}


def _reset_time(message, now=None):
    """Unix time of the stated reset: ``…|<epoch>``, Claude's ``resets 2:50pm (Europe/Warsaw)`` or Codex's ``try again
    at 7:57 PM.`` (host local time); the next such time. A time after a date (``at Oct 14th, …``) is not matched."""
    epoch = re.search(r"\|(\d{10})\b", message)
    if epoch:
        return int(epoch.group(1))
    match = re.search(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)\s*\(([A-Za-z_]+(?:/[A-Za-z_+-]+)*)\)", message, re.IGNORECASE)
    if match:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            zone = ZoneInfo(match.group(4))
        except (ZoneInfoNotFoundError, ValueError):
            return None
        current = (now or datetime.now(zone)).astimezone(zone)
    else:
        match = re.search(r"try again at\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)\b", message, re.IGNORECASE)
        if not match:
            return None
        current = now or datetime.now().astimezone()  # the CLI states the host's local time
    hour = int(match.group(1)) % 12 + (12 if match.group(3).lower() == "pm" else 0)
    reset = current.replace(hour=hour, minute=int(match.group(2) or 0), second=0, microsecond=0)
    if reset <= current:
        reset += timedelta(days=1)
    return int(reset.timestamp())


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


def _opencode_report(data):
    """(units, failed, model) summed over the ``step_finish`` events of ``opencode run --format json``.

    OpenCode reports input without cache reads (``cache.read`` apart) and reasoning apart from output; reasoning is
    counted as output. A cache count missing from any step stays null (unknown), never 0.
    """
    totals = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    steps, failed = 0, False
    for line in data.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type") if isinstance(event, dict) else None
        if kind == "error":
            failed = True
        elif kind == "step_finish":
            try:
                tokens = event["part"]["tokens"]
                cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
                values = {"input_tokens": tokens["input"], "output_tokens": tokens["output"],
                          "reasoning": tokens.get("reasoning", 0),
                          "cache_read_tokens": cache.get("read"), "cache_write_tokens": cache.get("write")}
            except (KeyError, TypeError, AttributeError):
                return None, True, None
            if any(v is not None and (type(v) is not int or v < 0) for v in values.values()) or None in (
                    values["input_tokens"], values["output_tokens"], values["reasoning"]):
                return None, True, None
            values["output_tokens"] += values.pop("reasoning")
            for key, value in values.items():
                totals[key] = None if value is None or totals[key] is None else totals[key] + value
            steps += 1
    if not steps:
        return None, True, None
    return totals, failed, None


class _OpenRouterRelay:
    """The only network an OpenCode run has: 127.0.0.1, one ``:free`` model, chat completions, key added here.

    After OpenRouter's daily free-model cap (a 429 naming ``per-day``) it forwards nothing more and answers 402,
    which OpenCode does not retry, so a stopped trial spends no further requests. Other errors pass through.
    """

    def __init__(self, key, model, upstream=_OPENROUTER):
        self.key, self.model, self.upstream = key, model, upstream
        self.requests, self.quota = 0, None
        relay = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                relay._relay(self)

            def do_GET(self):
                relay._answer(self, 403, "only chat completions")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    @staticmethod
    def _answer(handler, status, message, body=None):
        body = body or json.dumps({"error": {"message": message, "code": status}}).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _relay(self, handler):
        try:
            body = handler.rfile.read(int(handler.headers.get("Content-Length") or 0))
            model = json.loads(body).get("model")
        except (ValueError, AttributeError):
            return self._answer(handler, 400, "invalid request")
        if handler.path != "/api/v1/chat/completions" or model != self.model:
            return self._answer(handler, 403, f"only chat completions of {self.model}")
        if self.quota is not None:
            return self._answer(handler, 402, self.quota["message"])
        self.requests += 1
        request = urllib.request.Request(self.upstream + "/chat/completions", data=body, method="POST",
                                         headers={"Authorization": f"Bearer {self.key}",
                                                  "Content-Type": "application/json"})
        try:
            upstream = urllib.request.urlopen(request, timeout=300)
        except urllib.error.HTTPError as error:
            data = error.read()
            if error.code == 429 and b"per-day" in data:
                self.quota = {"kind": "quota_exhausted", "resets_at": _openrouter_reset(error.headers, data),
                              "message": data.decode("utf-8", "replace")[:300]}
                return self._answer(handler, 402, self.quota["message"])
            return self._answer(handler, error.code, "", data)
        except OSError as error:
            return self._answer(handler, 502, f"OpenRouter unreachable: {error}")
        with upstream:  # streamed as it arrives (server-sent events), ended by closing the connection
            handler.send_response(upstream.status)
            handler.send_header("Content-Type", upstream.headers.get("Content-Type", "application/json"))
            handler.send_header("Connection", "close")
            handler.end_headers()
            while chunk := upstream.read1(65536):
                handler.wfile.write(chunk)
                handler.wfile.flush()
        handler.close_connection = True


def _openrouter_reset(headers, data):
    """Unix time of the cap's reset: ``X-RateLimit-Reset`` (milliseconds) when given, else the next UTC midnight."""
    value = headers.get("X-RateLimit-Reset")
    if value is None:
        try:
            value = json.loads(data)["error"]["metadata"]["headers"]["X-RateLimit-Reset"]
        except (ValueError, KeyError, TypeError):
            pass
    try:
        return int(value) // 1000
    except (TypeError, ValueError):
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return int(midnight.timestamp())


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
    for relative in (".codex/auth.json", ".claude/.credentials.json", ".claude.json", ".local/share/opencode/auth.json"):
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
            elif isinstance(item, str) and len(item) >= 20 and not _existing_path(item):  # e.g. project paths
                values.add(item.encode())
    return values


def _existing_path(value):
    try:
        return Path(value).is_absolute() and Path(value).exists()
    except (OSError, ValueError):
        return False


def _encodings(secret):
    """The secret as written and in the encodings a model command could use to copy it out unnoticed: hex, and
    base64/base64url at each of the three byte alignments (the stable middle part of each encoding)."""
    import base64
    forms = {secret, secret.hex().encode(), secret.hex().upper().encode()}
    for encode in (base64.b64encode, base64.urlsafe_b64encode):
        for shift in range(3):
            encoded = encode(b"\0" * shift + secret).rstrip(b"=")
            middle = encoded[4:-4]  # independent of what precedes or follows the secret
            if len(middle) >= 16:
                forms.add(middle)
    return forms


def _leaks(data, secrets):
    forms = {form for secret in secrets for form in _encodings(secret)}
    return any(form in data for form in forms) or any(p.search(data) for p in _SECRET_PATTERNS)


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


# The qualified grading backend lives in `grading` (covered by `qualification.probe_digest()`); re-exported here.
_GRADING_NAMES = {"_WORKSPACE_TMPFS_BYTES", "GRADING_ENV", "GRADING_LAYOUT", "QUALIFIED_LIMITS",
                  "QUALIFIED_RUN_FLAGS", "QualifiedDockerBackend", "probe_grading_layout"}


def __getattr__(name):  # lazy re-export: `grading` imports from this module
    if name in _GRADING_NAMES:
        from . import grading
        return getattr(grading, name)
    raise AttributeError(name)
