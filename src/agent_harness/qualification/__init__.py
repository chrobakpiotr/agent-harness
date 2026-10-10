"""Target qualification runs (AH5-04c-1): ``qualify(job_id, out)`` runs the Q01–Q16/B1–B10 probes on Docker.

The probe modules ``q_probes``, ``q_lifecycle``, ``b_probes`` and ``report`` are verbatim copies of Showcase's
qualification code (docs/migration/provenance.md); ``probe_digest()`` names exactly that code. A run writes a
contract v1 qualification report whose tuple states ``probe_digest`` and whose ``policy_digest`` is Harness's
qualification policy, the evidence of every check, the capability report and ``session.json``. With
``reviewed=(report, evidence_root)`` the run is a grading session (ADR 0002 amendment, review per tuple): it
qualifies when ``contract.session_qualification_passes`` holds, and ``session.json`` names the session's
qualification digest, the reviewed report's digest and the probe digest, so each grade traces to one review.
"""

import hashlib
import json
from importlib import resources
from pathlib import Path

from .. import contract

PROBE_MODULES = ("__init__.py", "b_probes.py", "q_lifecycle.py", "q_probes.py", "report.py")
DEFAULT_TARGET = "showcase-docker-desktop-linux-guest"
DEFAULT_IMAGE = "python@sha256:9d72651cf7018c1f6a1dd6fd02bd68286631c33620bc0f37b0675b21aab915d5"


def probe_digest():
    """sha256 over the installed probe and orchestration modules (names and bytes): a change needs a new review.

    It names the code installed here, not proof of the code that ran (ADR 0002: authenticity is out of scope)."""
    here = resources.files(__package__)
    manifest = {name: hashlib.sha256(here.joinpath(name).read_bytes()).hexdigest() for name in PROBE_MODULES}
    return "sha256:" + hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def policy_digest():
    """The qualified grading policy: container flags, limits, grading environment and the mandatory checks."""
    from ..execution import (
        GRADING_ENV,
        GRADING_LAYOUT,
        QUALIFIED_LIMITS,
        QUALIFIED_RUN_FLAGS,
    )
    policy = {"policy_version": 1, "run_flags": list(QUALIFIED_RUN_FLAGS), "limits": QUALIFIED_LIMITS,
              "grading_env": GRADING_ENV, "grading_layout": GRADING_LAYOUT,
              "checks": list(contract.QUALIFICATION_CHECKS)}
    return "sha256:" + hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()


TARGET_IDS = {"docker-desktop": DEFAULT_TARGET, "github-runner": "showcase-github-hosted-ubuntu-runner",
              "linux-docker": "linux-docker-engine"}
_RETRYABLE = {"TimeoutExpired", "DockerCommandTimeout"}


def live_tuple(target_kind, docker, image, job_id):
    """(tuple, errors) for the live Docker target, identified as the qualification report records it."""
    from . import report as runner
    if target_kind not in TARGET_IDS:
        raise ValueError(f"target_kind must be one of {sorted(TARGET_IDS)}")
    errors = []
    found = runner._target_tuple("github-runner" if target_kind == "github-runner" else "docker-desktop", docker,
                                 image, job_id, errors)
    if target_kind == "linux-docker":  # a native Linux engine: the same identity facts, no Docker Desktop host
        errors = [e for e in errors if e != "docker_desktop_identity_mismatch"]
    return found, errors


def qualify(job_id, out, *, reviewed=None, target_kind="docker-desktop", target_id=None, image=DEFAULT_IMAGE,
            docker="docker", author="agent-harness-qualify", timeout_seconds=60, retries=2):
    """Run every check under ``job_id`` into the new, empty directory ``out``; return the session record.

    A check that ended only because a Docker command timed out is run again, up to ``retries`` times, in a fresh
    scratch directory; ``evidence/retries.json`` records every earlier attempt. The backend's own grading layout is
    probed too (``execution.probe_grading_layout``); its verdict is the tuple fact ``grading_layout``, so a session
    qualifies only if it passes as it did in the reviewed run."""
    from types import SimpleNamespace

    from ..execution import probe_grading_layout
    from . import report as runner
    if not isinstance(job_id, str) or not job_id or runner._safe(job_id) != job_id:
        raise ValueError("job_id must be a safe identifier (letters, digits and ._:/@+-)")
    if reviewed is not None:  # before any probe runs: an invalid reference must not leave a half-written run
        contract.validate_qualification_report(reviewed[0])
    target_id = target_id or TARGET_IDS.get(target_kind)
    out = Path(out)
    if out.is_symlink() or (out.exists() and (not out.is_dir() or any(out.iterdir()))):
        raise ValueError("out must be a new, empty directory")
    out = out.resolve()
    evidence = out / "evidence"
    evidence.mkdir(parents=True)
    tuple_, errors = live_tuple(target_kind, docker, image, job_id)
    tuple_["probe_digest"] = probe_digest()
    if errors:
        details = {"target_identity_errors": sorted(set(errors))}

        def unavailable(check_id, name, check_dir, _job_id=None):
            return {"status": "not-run", "reason_code": "TARGET_IDENTITY_UNAVAILABLE", "stdout": "", "stderr": "",
                    "details": details}

        groups = [(runner.run_q01_q10, unavailable, False), (runner.run_q11_q16, unavailable, True),
                  (runner.run_b1_b10, unavailable, False)]
    else:
        q_target, lifecycle, grading = runner._build_probe_targets(image, target_id, docker, timeout_seconds)
        groups = [(runner.run_q01_q10, q_target.execute_q01_q10, False),
                  (runner.run_q11_q16, lifecycle.execute_q11_q16, True),
                  (runner.run_b1_b10, grading.execute_b1_b10, False)]

    def run(group, root, only=None):
        run_group, execute, needs_job = group
        if only is not None:
            original = execute

            def execute(check_id, name, check_dir, *job, _original=original):
                if check_id in only:
                    return _original(check_id, name, check_dir, *job)
                return {"status": "not-run", "reason_code": "NOT_RETRIED", "stdout": "", "stderr": "", "details": {}}
        return run_group(execute, root, job_id=job_id) if needs_job else run_group(execute, root)

    records = {r.check_id: r for group in groups for r in run(group, evidence)}
    attempts = {}
    for attempt in range(1, retries + 1):
        retry = {cid for cid, r in records.items() if _timed_out(Path(r.evidence_path))}
        if not retry:
            break
        scratch = out / f".retry-{attempt}"
        for group in groups:
            for record in run(group, scratch / str(groups.index(group)), only=retry):
                if record.check_id not in retry:
                    continue
                target = Path(records[record.check_id].evidence_path).parent
                attempts.setdefault(record.check_id, []).append(json.loads((target / "probe.json").read_text()))
                for item in target.iterdir():
                    item.unlink() if item.is_file() else None
                for item in Path(record.evidence_path).parent.iterdir():
                    if item.is_file():
                        (target / item.name).write_bytes(item.read_bytes())
                records[record.check_id] = SimpleNamespace(check_id=record.check_id, status=record.status,
                                                           evidence_path=str(target / "probe.json"))
    if attempts:
        (evidence / "retries.json").write_text(json.dumps(attempts, indent=2, sort_keys=True) + "\n")
    layout_dir = out / "grading-layout"
    layout_dir.mkdir()
    layout = (probe_grading_layout(image, layout_dir, docker=docker, timeout_seconds=timeout_seconds)
              if not errors else {"passed": False, "checks": {}, "results": {}, "reason": sorted(set(errors))})
    (evidence / "grading-layout.json").write_text(json.dumps(layout, indent=2, sort_keys=True) + "\n")
    tuple_["grading_layout"] = "qualified" if layout["passed"] else "failed"
    checks = runner._persist_check_job_id(list(records.values()), target_id, job_id)
    doc = runner.assemble_report(target=target_id, policy_digest=policy_digest(), author=author,
                                 target_tuple=tuple_, checks=checks, evidence_root=evidence)
    if reviewed is None:
        passes = contract.qualification_passes(doc, evidence)  # no review yet: False until one is attached
    else:
        try:
            passes = contract.session_qualification_passes(doc, evidence, *reviewed)
        except (OSError, ValueError):  # ContractError is a ValueError: a mismatch is a non-passing session
            passes = False
    passes = passes and layout["passed"]
    capability = contract.validate_capability_report({
        "contract_version": 1, "target": target_id, "policy_digest": doc["policy_digest"], "discovered": True,
        "supported": all(c["result"] == "pass" for c in doc["checks"]), "qualified": passes,
        "launch_ready": passes, "capabilities": ["qualified_isolation"] if passes else [],
        "refusal": None if passes else "NOT_QUALIFIED"})
    session = {"job_id": job_id, "qualified": passes, "qualification_digest": contract.qualification_digest(doc),
               "reviewed_qualification_digest": (contract.qualification_digest(reviewed[0]) if reviewed else None),
               "probe_digest": tuple_["probe_digest"], "policy_digest": doc["policy_digest"],
               "target_kind": target_kind, "grading_layout": tuple_["grading_layout"], "retried": sorted(attempts),
               "report": "report.json", "capability_report": "capability.json", "evidence_root": "evidence"}
    for name, value in (("report.json", doc), ("capability.json", capability), ("session.json", session)):
        (out / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return {**session, "report_doc": doc, "capability_doc": capability}


def _timed_out(probe_json):
    try:
        observed = json.loads(probe_json.read_text())
    except (OSError, ValueError):
        return False
    details = observed.get("details") if isinstance(observed, dict) else None
    return (observed.get("reason_code") == "PROBE_EXCEPTION" and isinstance(details, dict)
            and details.get("exception") in _RETRYABLE)
