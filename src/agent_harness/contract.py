"""Public execution contract, version 1 (AH5-02). See docs/adr/0002-execution-contract.md.

Documents are plain JSON-compatible dicts. Schemas are closed: unknown fields are rejected, and no
field carries free text (logs, stderr, environment), so a valid result cannot smuggle secrets.

Three facts are never merged: execution ``outcome`` (incl. ``exit_code``), harness ``completion``,
and the consumer's grade (not part of this contract).
"""

import hashlib
import json
import re
from datetime import datetime

CONTRACT_VERSION = 1

OUTCOMES = ("completed", "error", "timeout", "cancel", "unknown", "rejected")
ERROR_CODES = ("CAPABILITY_UNSUPPORTED", "NOT_QUALIFIED", "BACKEND_UNAVAILABLE", "LAUNCH_FAILED",
               "PROVIDER_ERROR", "INTERNAL_ERROR")
CAPABILITIES = ("cancel", "usage", "qualified_isolation")
ISOLATION_LEVELS = ("fake", "controlled", "unqualified", "qualified")

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,6})?Z")
_UNITS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


class ContractError(ValueError):
    """``code`` is one of MALFORMED, VERSION_MISMATCH, BINDING_MISMATCH, UNSAFE_PATH."""

    def __init__(self, code, where):
        super().__init__(f"{code}: {where}")
        self.code = code
        self.where = where


def request_digest(request):
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_request(doc):
    _fields(doc, "request", {"contract_version", "request_id", "trial_id", "task_digest", "config_digest",
                             "provider", "model", "settings", "capabilities", "timeout_seconds",
                             "max_attempts", "input_bindings"})
    _version(doc, "request")
    _id(doc["request_id"], "request.request_id")
    _id(doc["trial_id"], "request.trial_id", nullable=True)
    _match(_DIGEST, doc["task_digest"], "request.task_digest")
    _match(_DIGEST, doc["config_digest"], "request.config_digest")
    _id(doc["provider"], "request.provider")
    _id(doc["model"], "request.model")
    _check(isinstance(doc["settings"], dict), "request.settings")
    for key, value in doc["settings"].items():
        _id(key, "request.settings")
        _check(value is None or isinstance(value, (str, int, float, bool)), f"request.settings.{key}")
    _enum_list(doc["capabilities"], CAPABILITIES, "request.capabilities")
    _int(doc["timeout_seconds"], 1, 86400, "request.timeout_seconds")
    _int(doc["max_attempts"], 1, 100, "request.max_attempts")
    names = []
    for i, binding in enumerate(_list(doc["input_bindings"], "request.input_bindings")):
        where = f"request.input_bindings[{i}]"
        _fields(binding, where, {"name", "sha256"})
        _id(binding["name"], where + ".name")
        _match(_DIGEST, binding["sha256"], where + ".sha256")
        names.append(binding["name"])
    _check(len(names) == len(set(names)), "request.input_bindings: duplicate name")
    return doc


def validate_result(doc, request):
    """Validate ``doc`` and bind it to the exact ``request`` it answers."""
    validate_request(request)
    _fields(doc, "result", {"contract_version", "request_id", "request_digest", "execution_id", "outcome",
                            "exit_code", "completion", "error_code", "drain", "cancel_requested",
                            "isolation_level", "resolved_model", "versions", "started_at", "ended_at",
                            "attempts", "candidate", "artifacts", "usage_events", "usage_completeness"})
    _version(doc, "result")
    if doc["request_id"] != request["request_id"] or doc["request_digest"] != request_digest(request):
        raise ContractError("BINDING_MISMATCH", "result.request_id/request_digest")
    outcome = _enum(doc["outcome"], OUTCOMES, "result.outcome")
    _enum(doc["error_code"], ERROR_CODES, "result.error_code", nullable=True)
    _check((doc["error_code"] is not None) == (outcome in ("rejected", "error")), "result.error_code")
    _check(doc["completion"] is None or (outcome == "completed" and doc["completion"] in ("accepted", "rejected")),
           "result.completion: only a completed execution can carry a completion decision")
    _check(doc["exit_code"] is None or (type(doc["exit_code"]) is int and outcome != "rejected"), "result.exit_code")
    _check(type(doc["cancel_requested"]) is bool, "result.cancel_requested")
    level = _enum(doc["isolation_level"], ISOLATION_LEVELS, "result.isolation_level")
    _id(doc["resolved_model"], "result.resolved_model", nullable=True)
    _check(isinstance(doc["versions"], dict), "result.versions")
    for key, value in doc["versions"].items():
        _id(key, "result.versions")
        _id(value, f"result.versions.{key}")
    _enum(doc["usage_completeness"], ("complete", "partial", "unknown"), "result.usage_completeness")
    attempts = _list(doc["attempts"], "result.attempts")
    artifacts = _list(doc["artifacts"], "result.artifacts")
    usage = _list(doc["usage_events"], "result.usage_events")

    if outcome == "rejected":
        _check(doc["execution_id"] is None and doc["started_at"] is None and doc["ended_at"] is None
               and doc["drain"] is None and doc["candidate"] is None and not attempts and not artifacts
               and not usage, "result: a rejected request was never launched")
        return doc

    _id(doc["execution_id"], "result.execution_id")
    _enum(doc["drain"], ("confirmed", "unconfirmed"), "result.drain")
    _check(outcome != "unknown" or doc["drain"] == "unconfirmed", "result.drain: unknown terminal cannot confirm drain")
    _span(doc, "result")
    if "qualified_isolation" in request["capabilities"] and level != "qualified":
        raise ContractError("BINDING_MISMATCH", "result.isolation_level: request requires qualified isolation")
    _check(1 <= len(attempts) <= request["max_attempts"], "result.attempts")
    attempt_ids = []
    for i, attempt in enumerate(attempts):
        where = f"result.attempts[{i}]"
        _fields(attempt, where, {"attempt_id", "outcome", "started_at", "ended_at"})
        attempt_ids.append(_id(attempt["attempt_id"], where + ".attempt_id"))
        _enum(attempt["outcome"], OUTCOMES[:-1], where + ".outcome")
        _span(attempt, where)
    _check(len(attempt_ids) == len(set(attempt_ids)), "result.attempts: duplicate attempt_id")
    if doc["candidate"] is not None:
        _ref(doc["candidate"], "result.candidate", named=False)
    for i, ref in enumerate(artifacts):
        _ref(ref, f"result.artifacts[{i}]", named=True)
    _usage(usage, set(attempt_ids))
    _check(usage or doc["usage_completeness"] == "unknown", "result.usage_completeness: no events means unknown")
    return doc


def validate_capability_report(doc):
    _fields(doc, "report", {"contract_version", "target", "policy_digest", "discovered", "supported",
                            "qualified", "launch_ready", "capabilities", "refusal"})
    _version(doc, "report")
    _id(doc["target"], "report.target")
    if doc["policy_digest"] is not None:
        _match(_DIGEST, doc["policy_digest"], "report.policy_digest")
    chain = [doc[k] for k in ("discovered", "supported", "qualified", "launch_ready")]
    _check(all(type(v) is bool for v in chain), "report: states must be booleans")
    _check(chain == sorted(chain, reverse=True), "report: launch_ready => qualified => supported => discovered")
    _check(not doc["qualified"] or doc["policy_digest"] is not None, "report.policy_digest: qualification is policy-bound")
    _enum_list(doc["capabilities"], CAPABILITIES, "report.capabilities")
    _enum(doc["refusal"], ERROR_CODES, "report.refusal", nullable=True)
    _check(doc["launch_ready"] == (doc["refusal"] is None), "report.refusal: required exactly when not launch_ready")
    return doc


def validate_repo_context(doc):
    """Explicit repository context; never inferred from the installed module location or cwd."""
    _fields(doc, "repo", {"contract_version", "repo_id", "base_sha", "workspace", "authority_root", "evidence_root"})
    _version(doc, "repo")
    _id(doc["repo_id"], "repo.repo_id")
    _match(_SHA, doc["base_sha"], "repo.base_sha")
    for key in ("workspace", "authority_root", "evidence_root"):
        value = doc[key]
        _check(isinstance(value, str) and value.startswith("/"), f"repo.{key}: must be absolute")
        if any(part in (".", "..") for part in value.split("/")) or "\0" in value:
            raise ContractError("UNSAFE_PATH", f"repo.{key}")
    return doc


def _usage(events, attempt_ids):
    event_ids, summaries = set(), set()
    for i, event in enumerate(events):
        where = f"result.usage_events[{i}]"
        _fields(event, where, {"contract_version", "event_id", "attempt_id", "source", "kind", "units",
                               "cache_semantics"})
        _version(event, where)
        event_id = _id(event["event_id"], where + ".event_id")
        _check(event_id not in event_ids, where + ".event_id: duplicate")
        event_ids.add(event_id)
        _check(event["attempt_id"] in attempt_ids, where + ".attempt_id: unknown attempt")
        _enum(event["source"], ("provider", "harness"), where + ".source")
        if _enum(event["kind"], ("stream", "summary"), where + ".kind") == "summary":
            _check(event["attempt_id"] not in summaries, where + ": one summary per attempt")
            summaries.add(event["attempt_id"])
        _fields(event["units"], where + ".units", set(_UNITS))
        for unit in _UNITS:
            value = event["units"][unit]
            _check(value is None or (type(value) is int and value >= 0), f"{where}.units.{unit}")
        _enum(event["cache_semantics"], ("separate", "included_in_input", "unknown"), where + ".cache_semantics")


def _ref(ref, where, named):
    _fields(ref, where, {"name", "path", "sha256", "size"} if named else {"path", "sha256", "size"})
    if named:
        _id(ref["name"], where + ".name")
    path = ref["path"]
    _check(isinstance(path, str), where + ".path")
    if (not path or len(path) > 255 or path.startswith("/") or "\\" in path or "\0" in path
            or any(part in ("", ".", "..") for part in path.split("/"))):
        raise ContractError("UNSAFE_PATH", where + ".path")
    _match(_DIGEST, ref["sha256"], where + ".sha256")
    _int(ref["size"], 0, 2**53, where + ".size")


def _span(doc, where):
    start, end = _time(doc["started_at"], where + ".started_at"), _time(doc["ended_at"], where + ".ended_at")
    _check(start <= end, where + ": ended_at before started_at")


def _time(value, where):
    _match(_TIME, value, where)
    try:
        return datetime.fromisoformat(value[:-1])
    except ValueError:
        raise ContractError("MALFORMED", where) from None


def _fields(doc, where, expected):
    _check(isinstance(doc, dict) and set(doc) == expected, f"{where}: fields must be exactly {sorted(expected)}")


def _version(doc, where):
    if type(doc["contract_version"]) is not int or doc["contract_version"] != CONTRACT_VERSION:
        raise ContractError("VERSION_MISMATCH", f"{where}.contract_version")


def _id(value, where, nullable=False):
    if value is None and nullable:
        return None
    return _match(_ID, value, where)


def _match(pattern, value, where):
    _check(isinstance(value, str) and pattern.fullmatch(value) is not None, where)
    return value


def _enum(value, allowed, where, nullable=False):
    _check(value in allowed or (nullable and value is None), where)
    return value


def _enum_list(values, allowed, where):
    _check(isinstance(values, list) and all(v in allowed for v in values) and len(values) == len(set(values)), where)


def _int(value, low, high, where):
    _check(type(value) is int and low <= value <= high, where)


def _list(value, where):
    _check(isinstance(value, list), where)
    return value


def _check(ok, where):
    if not ok:
        raise ContractError("MALFORMED", where)
