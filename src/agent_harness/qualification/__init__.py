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

PROBE_MODULES = ("b_probes.py", "q_lifecycle.py", "q_probes.py", "report.py")
DEFAULT_TARGET = "showcase-docker-desktop-linux-guest"
DEFAULT_IMAGE = "python@sha256:9d72651cf7018c1f6a1dd6fd02bd68286631c33620bc0f37b0675b21aab915d5"


def probe_digest():
    """sha256 over the probe modules' names and bytes: a changed probe needs a new independent review."""
    here = resources.files(__package__)
    manifest = {name: hashlib.sha256(here.joinpath(name).read_bytes()).hexdigest() for name in PROBE_MODULES}
    return "sha256:" + hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def policy_digest():
    """The qualified grading policy: container flags, limits, grading environment and the mandatory checks."""
    from ..execution import GRADING_ENV, QUALIFIED_LIMITS, QUALIFIED_RUN_FLAGS
    policy = {"policy_version": 1, "run_flags": list(QUALIFIED_RUN_FLAGS), "limits": QUALIFIED_LIMITS,
              "grading_env": GRADING_ENV, "checks": list(contract.QUALIFICATION_CHECKS)}
    return "sha256:" + hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()


def qualify(job_id, out, *, reviewed=None, target_id=DEFAULT_TARGET, image=DEFAULT_IMAGE, docker="docker",
            author="agent-harness-qualify", timeout_seconds=30):
    """Run every check under ``job_id`` into the new, empty directory ``out``; return the session record."""
    from . import report as runner
    out = Path(out).resolve()
    if out.exists() and (out.is_symlink() or not out.is_dir() or any(out.iterdir())):
        raise ValueError("out must be a new, empty directory")
    evidence = out / "evidence"
    evidence.mkdir(parents=True)
    job_id = runner._safe(job_id)
    errors = []
    tuple_ = runner._target_tuple("docker-desktop", docker, image, job_id, errors)
    tuple_["probe_digest"] = probe_digest()
    records = []
    if errors:
        details = {"target_identity_errors": sorted(set(errors))}

        def unavailable(check_id, name, check_dir, _job_id=None):
            return {"status": "not-run", "reason_code": "TARGET_IDENTITY_UNAVAILABLE", "stdout": "", "stderr": "",
                    "details": details}

        records += runner.run_q01_q10(unavailable, evidence)
        records += runner.run_q11_q16(unavailable, evidence, job_id=job_id)
        records += runner.run_b1_b10(unavailable, evidence)
    else:
        q_target, lifecycle, grading = runner._build_probe_targets(image, target_id, docker, timeout_seconds)
        records += runner.run_q01_q10(q_target.execute_q01_q10, evidence)
        records += runner.run_q11_q16(lifecycle.execute_q11_q16, evidence, job_id=job_id)
        records += runner.run_b1_b10(grading.execute_b1_b10, evidence)
    checks = runner._persist_check_job_id(records, target_id, job_id)
    doc = runner.assemble_report(target=target_id, policy_digest=policy_digest(), author=author,
                                 target_tuple=tuple_, checks=checks, evidence_root=evidence)
    if reviewed is None:
        passes = contract.qualification_passes(doc, evidence)  # no review yet: False until one is attached
    else:
        try:
            passes = contract.session_qualification_passes(doc, evidence, *reviewed)
        except (OSError, ValueError):  # ContractError is a ValueError: a mismatch is a non-passing session
            passes = False
    capability = contract.validate_capability_report({
        "contract_version": 1, "target": target_id, "policy_digest": doc["policy_digest"], "discovered": True,
        "supported": all(c["result"] == "pass" for c in doc["checks"]), "qualified": passes,
        "launch_ready": passes, "capabilities": ["qualified_isolation"] if passes else [],
        "refusal": None if passes else "NOT_QUALIFIED"})
    session = {"job_id": job_id, "qualified": passes, "qualification_digest": contract.qualification_digest(doc),
               "reviewed_qualification_digest": (contract.qualification_digest(reviewed[0]) if reviewed else None),
               "probe_digest": tuple_["probe_digest"], "policy_digest": doc["policy_digest"],
               "report": "report.json", "capability_report": "capability.json", "evidence_root": "evidence"}
    for name, value in (("report.json", doc), ("capability.json", capability), ("session.json", session)):
        (out / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return {**session, "report_doc": doc, "capability_doc": capability}
