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
import shutil
import signal
import subprocess
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
    with _RUNNING_LOCK:
        running = _RUNNING.get(key)
        if running is not None:
            _check_binding(contract.request_digest(running.request), request)
            return running
        marker_path = root / f"{slot}.started"
        if marker_path.exists():  # read-only: a reused ID is checked before anything else
            _check_binding(_marker_digest(marker_path), request)
        missing = [c for c in request["capabilities"] if c not in backend.capabilities]
        code = getattr(backend, "rejection", None)
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


def _seal(source, roots, request):
    """Copy a candidate file into the evidence root and return its contract reference."""
    relative = f"executions/{_slot(request['request_id'])}/candidate"
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

    def run(self, request, roots, cancelled, execution_id, started_at):
        workspace = Path(roots["workspace"])
        try:
            child = subprocess.Popen(self.argv, cwd=workspace, env=self.env, start_new_session=True,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            now = _now()
            return self._body("error", None, "confirmed", started_at, now, error_code="LAUNCH_FAILED")
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
        return self._body(outcome, child.returncode if outcome == "completed" else None,
                          "confirmed" if drained else "unconfirmed", started_at, ended, candidate=candidate)

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
