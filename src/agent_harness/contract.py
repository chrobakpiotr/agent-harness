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
from pathlib import Path

CONTRACT_VERSION = 1  # the default version consumers build; requests and results may also use version 2
CONTRACT_VERSIONS = (1, 2)  # v2 (ADR 0005): request `limits`, result `target` and `limits`

OUTCOMES = ("completed", "error", "timeout", "cancel", "unknown", "rejected")
ERROR_CODES = ("CAPABILITY_UNSUPPORTED", "NOT_QUALIFIED", "BACKEND_UNAVAILABLE", "LAUNCH_FAILED",
               "PROVIDER_ERROR", "INTERNAL_ERROR")
LIMIT_EXCEEDED = "LIMIT_EXCEEDED"  # error code of v2 results only: a limit other than the wall time fired
LIMIT_KEYS = ("cpus", "memory_bytes", "pids", "disk_bytes", "output_bytes")
FIRED_LIMITS = ("timeout", "oom", "pids", "disk")
CAPABILITIES = ("cancel", "usage", "qualified_isolation")
ISOLATION_LEVELS = ("fake", "controlled", "unqualified", "qualified")
# Mandatory checks of a target qualification: Showcase Q01-Q16 and the grading boundary B1-B10
# (docs/specs/AH5-04b/grading-requirements.md). A report records each one exactly once.
QUALIFICATION_CHECKS = tuple(f"Q{i:02d}" for i in range(1, 17)) + tuple(f"B{i}" for i in range(1, 11))
# The exact target facts every report states. `job_id` binds the report to one run: a GitHub-hosted job is a fresh
# VM, so its qualification is valid in that job only.
QUALIFICATION_TUPLE = ("job_id", "host", "kernel", "engine", "workload_image")

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
    version = _doc_version(doc, "request", CONTRACT_VERSIONS)
    _fields(doc, "request", {"contract_version", "request_id", "trial_id", "task_digest", "config_digest",
                             "provider", "model", "settings", "capabilities", "timeout_seconds",
                             "max_attempts", "input_bindings"} | ({"limits"} if version == 2 else set()))
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
    if version == 2 and doc["limits"] is not None:
        _fields(doc["limits"], "request.limits", set(LIMIT_KEYS))
        for key in LIMIT_KEYS:
            _int(doc["limits"][key], 1, 2**53, f"request.limits.{key}")
    if version == 2:  # no hidden defaults: a qualified launch is only ever asked for with explicit limits
        _check("qualified_isolation" not in doc["capabilities"] or doc["limits"] is not None,
               "request.limits: required with qualified_isolation")
    return doc


def validate_result(doc, request):
    """Validate ``doc`` and bind it to the exact ``request`` it answers."""
    validate_request(request)
    version = _doc_version(doc, "result", CONTRACT_VERSIONS)
    if version != request["contract_version"]:
        raise ContractError("VERSION_MISMATCH", "result.contract_version: must be the request's version")
    _fields(doc, "result", {"contract_version", "request_id", "request_digest", "execution_id", "outcome",
                            "exit_code", "completion", "error_code", "drain", "cancel_requested",
                            "isolation_level", "resolved_model", "versions", "started_at", "ended_at",
                            "attempts", "candidate", "artifacts", "usage_events", "usage_completeness"}
            | ({"target", "limits"} if version == 2 else set()))
    if doc["request_id"] != request["request_id"] or doc["request_digest"] != request_digest(request):
        raise ContractError("BINDING_MISMATCH", "result.request_id/request_digest")
    outcome = _enum(doc["outcome"], OUTCOMES, "result.outcome")
    _enum(doc["error_code"], ERROR_CODES + ((LIMIT_EXCEEDED,) if version == 2 else ()), "result.error_code",
          nullable=True)
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
    if version == 2:
        _target_and_limits(doc, request, outcome, level)

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
    summaries = _usage(usage, set(attempt_ids), version)
    _check(usage or doc["usage_completeness"] == "unknown", "result.usage_completeness: no events means unknown")
    if doc["usage_completeness"] == "complete":
        _check(set(summaries) == set(attempt_ids)
               and all(v is not None for e in summaries.values() for v in e["units"].values()),
               "result.usage_completeness: complete needs a summary with integer units for every attempt")
    return doc


def validate_target(target, where="result.target"):
    """A v2 qualified target: its id, the passing qualification's digest and the workload image digest."""
    _fields(target, where, {"id", "qualification_digest", "image_digest"})
    _id(target["id"], where + ".id")
    _match(_DIGEST, target["qualification_digest"], where + ".qualification_digest")
    _match(_DIGEST, target["image_digest"], where + ".image_digest")
    return target


def _target_and_limits(doc, request, outcome, level):
    """Contract v2 (ADR 0005): which qualified target ran the launch and which resource limit, if any, ended it."""
    target, limits, requested = doc["target"], doc["limits"], request["limits"]
    if target is not None:
        validate_target(target)
    if outcome == "rejected":
        _check(target is None and limits is None, "result: a rejected request has no target or limits")
    else:
        _check((target is not None) == (level == "qualified"), "result.target: required exactly for qualified isolation")
        _check(level != "qualified" or limits is not None or outcome == "unknown",
               "result.limits: a qualified launch states its limits")
        _check(limits is None or requested is not None, "result.limits: only when the request sets limits")
        # An unknown terminal cannot state what was applied; every other launch with requested limits does.
        _check(limits is not None or requested is None or outcome == "unknown",
               "result.limits: required when the request sets limits")
    fired = None
    if limits is not None:
        _fields(limits, "result.limits", {"applied", "fired", "output_truncated"})
        expected = {**(requested or {}), "timeout_seconds": request["timeout_seconds"]}
        _fields(limits["applied"], "result.limits.applied", set(expected))
        for key, value in expected.items():  # equal and an int: True == 1 and 1.0 == 1 must not pass
            _int(limits["applied"][key], value, value, f"result.limits.applied.{key}")
        fired = _enum(limits["fired"], FIRED_LIMITS, "result.limits.fired", nullable=True)
        _check(type(limits["output_truncated"]) is bool, "result.limits.output_truncated")
        _check((fired == "timeout") == (outcome == "timeout"), "result.limits.fired: timeout iff outcome timeout")
    # LIMIT_EXCEEDED is an error code, and error codes exist only on `error` (rejected results have no limits),
    # so a fired limit other than timeout always ends as outcome `error`.
    _check((doc["error_code"] == LIMIT_EXCEEDED) == (fired in ("oom", "pids", "disk")),
           "result.error_code: LIMIT_EXCEEDED iff a limit other than timeout fired")


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


def validate_qualification_report(doc):
    """One exact target tuple's qualification: every mandatory check recorded separately with its evidence."""
    _fields(doc, "qualification", {"contract_version", "target", "policy_digest", "author", "tuple", "checks",
                                   "independent_review", "created_at"})
    _version(doc, "qualification")
    _id(doc["target"], "qualification.target")
    _match(_DIGEST, doc["policy_digest"], "qualification.policy_digest")
    _id(doc["author"], "qualification.author")
    _check(isinstance(doc["tuple"], dict) and set(QUALIFICATION_TUPLE) <= set(doc["tuple"]),
           f"qualification.tuple: must state {list(QUALIFICATION_TUPLE)}")
    for key, value in doc["tuple"].items():
        _id(key, "qualification.tuple")
        _id(value, f"qualification.tuple.{key}")
    _match(_DIGEST, doc["tuple"]["workload_image"], "qualification.tuple.workload_image")
    ids = []
    for i, check in enumerate(_list(doc["checks"], "qualification.checks")):
        where = f"qualification.checks[{i}]"
        _fields(check, where, {"id", "result", "evidence"})
        ids.append(_enum(check["id"], QUALIFICATION_CHECKS, where + ".id"))
        _enum(check["result"], ("pass", "fail", "not-run"), where + ".result")
        for j, ref in enumerate(_list(check["evidence"], where + ".evidence")):
            _ref(ref, f"{where}.evidence[{j}]", named=False)
        _check(check["result"] != "pass" or check["evidence"], where + ": a pass needs evidence")
    _check(sorted(ids) == sorted(QUALIFICATION_CHECKS), "qualification.checks: exactly one per mandatory check")
    # Each passing check needs evidence of its own: one file cannot stand in for several checks.
    owners = {}
    for check in doc["checks"]:
        for ref in check["evidence"]:
            owners.setdefault(ref["sha256"], set()).add(check["id"])
    for check in doc["checks"]:
        _check(check["result"] != "pass" or any(owners[r["sha256"]] == {check["id"]} for r in check["evidence"]),
               f"qualification.checks[{check['id']}]: a pass needs evidence of its own")
    review = doc["independent_review"]
    if review is not None:
        where = "qualification.independent_review"
        _fields(review, where, {"reviewer", "subject", "verdict", "evidence"})
        _id(review["reviewer"], where + ".reviewer")
        _check(review["reviewer"].casefold() != doc["author"].casefold(), where + ".reviewer: must not be the author")
        _check(review["subject"] == review_subject(doc), where + ".subject: must be this report's review_subject")
        _enum(review["verdict"], ("pass", "fail"), where + ".verdict")
        _ref(review["evidence"], where + ".evidence", named=False)
        _check(review["evidence"]["sha256"] not in owners, where + ".evidence: must not be check evidence")
    _time(doc["created_at"], "qualification.created_at")
    return doc


def review_subject(doc):
    """What an independent review signs off: the report digest without the review itself."""
    return request_digest({**doc, "independent_review": None})


def qualification_passes(doc, evidence_root):
    """True only when every mandatory check passed with its own verified evidence and the review passed."""
    verify_qualification_evidence(doc, evidence_root)
    review = doc["independent_review"]
    return all(c["result"] == "pass" for c in doc["checks"]) and review is not None and review["verdict"] == "pass"


def qualification_digest(doc):
    validate_qualification_report(doc)
    return request_digest(doc)  # sha256 of the canonical document


def verify_qualification_evidence(doc, evidence_root):
    """Every evidence file exists inside ``evidence_root`` with the recorded size and digest; the evidence of each
    passing check names the report's ``job_id`` and the review evidence names the review ``subject``, so a
    report cannot be relabelled for another job or reviewed under another subject without new evidence."""
    validate_qualification_report(doc)
    root = Path(evidence_root).resolve(strict=True)
    refs = [ref for check in doc["checks"] for ref in check["evidence"]]
    if doc["independent_review"] is not None:
        refs.append(doc["independent_review"]["evidence"])
    for ref in refs:
        path = (root / ref["path"]).resolve()
        if not path.is_relative_to(root):
            raise ContractError("UNSAFE_PATH", f"qualification evidence {ref['path']}")
        if not path.is_file() or path.stat().st_size != ref["size"]:
            raise ContractError("BINDING_MISMATCH", f"qualification evidence {ref['path']}")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        if "sha256:" + digest.hexdigest() != ref["sha256"]:
            raise ContractError("BINDING_MISMATCH", f"qualification evidence {ref['path']}")
    # The job id as a whole token (ID characters as boundaries): `attempt-1` does not match `attempt-10`.
    job = re.compile(rb"(?<![A-Za-z0-9._:/@+-])" + re.escape(doc["tuple"]["job_id"].encode()) + rb"(?![A-Za-z0-9._:/@+-])")
    for check in doc["checks"]:
        if check["result"] == "pass" and not any(job.search(_evidence_bytes(root, r)) for r in check["evidence"]):
            raise ContractError("BINDING_MISMATCH", f"qualification.checks[{check['id']}]: evidence does not name the job")
    review = doc["independent_review"]
    if review is not None and review["subject"].encode() not in _evidence_bytes(root, review["evidence"]):
        raise ContractError("BINDING_MISMATCH", "qualification.independent_review: evidence does not name the subject")
    return doc


def _evidence_bytes(root, ref):
    return (root / ref["path"]).resolve().read_bytes()  # size and digest already verified


def validate_capability_binding(report, qualification, evidence_root, job_id):
    """A report may claim ``qualified`` only with a passing, evidence-verified qualification of the same target and
    policy digest from the caller's own job (``job_id``). ``validate_capability_report`` alone does not make
    ``qualified`` meaningful; consumers that rely on qualification must call this."""
    validate_capability_report(report)
    validate_qualification_report(qualification)
    if report["target"] != qualification["target"] or report["policy_digest"] != qualification["policy_digest"]:
        raise ContractError("BINDING_MISMATCH", "report.target/policy_digest")
    if qualification["tuple"]["job_id"] != job_id:
        raise ContractError("BINDING_MISMATCH", "qualification.tuple.job_id: qualified in another job")
    if report["qualified"] and not qualification_passes(qualification, evidence_root):
        raise ContractError("BINDING_MISMATCH", "report.qualified: the qualification does not pass")
    return report


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


def _usage(events, attempt_ids, version):
    event_ids, summaries = set(), {}
    for i, event in enumerate(events):
        where = f"result.usage_events[{i}]"
        _fields(event, where, {"contract_version", "event_id", "attempt_id", "source", "kind", "units",
                               "cache_semantics"})
        _version(event, where, (version,))
        event_id = _id(event["event_id"], where + ".event_id")
        _check(event_id not in event_ids, where + ".event_id: duplicate")
        event_ids.add(event_id)
        _check(event["attempt_id"] in attempt_ids, where + ".attempt_id: unknown attempt")
        _enum(event["source"], ("provider", "harness"), where + ".source")
        if _enum(event["kind"], ("stream", "summary"), where + ".kind") == "summary":
            _check(event["attempt_id"] not in summaries, where + ": one summary per attempt")
            summaries[event["attempt_id"]] = event
        _fields(event["units"], where + ".units", set(_UNITS))
        for unit in _UNITS:
            value = event["units"][unit]
            _check(value is None or (type(value) is int and value >= 0), f"{where}.units.{unit}")
        _enum(event["cache_semantics"], ("separate", "included_in_input", "unknown"), where + ".cache_semantics")
    return summaries


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


def _version(doc, where, allowed=(CONTRACT_VERSION,)):
    if type(doc["contract_version"]) is not int or doc["contract_version"] not in allowed:
        raise ContractError("VERSION_MISMATCH", f"{where}.contract_version")


def _doc_version(doc, where, allowed):
    _check(isinstance(doc, dict) and "contract_version" in doc, f"{where}.contract_version")
    _version(doc, where, allowed)
    return doc["contract_version"]


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
