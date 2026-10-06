"""Target qualification reports (AH5-04b-r): every mandatory check recorded separately with its own evidence,
bound to one exact target, one job and an independent review of this report."""

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_harness import contract

JOB = "github-run-42-attempt-1"


def capability(target, policy_digest, qualified):
    return {"contract_version": 1, "target": target, "policy_digest": policy_digest, "discovered": True,
            "supported": True, "qualified": qualified, "launch_ready": qualified, "capabilities": ["cancel"],
            "refusal": None if qualified else "NOT_QUALIFIED"}


class QualificationReportTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "evidence").mkdir()
        doc = {"contract_version": 1, "target": "test-target", "policy_digest": "sha256:" + "2" * 64,
               "author": "qualifier", "tuple": {"job_id": JOB, "host": "ubuntu-24.04", "kernel": "6.8.0",
                                                "engine": "docker-29.8.2", "workload_image": "sha256:" + "3" * 64},
               "checks": [{"id": i, "result": "pass", "evidence": [self.evidence(i)]}
                          for i in contract.QUALIFICATION_CHECKS],
               "independent_review": None, "created_at": "2026-10-06T00:00:00Z"}
        doc["independent_review"] = self.review(doc)
        self.doc = doc

    def evidence(self, name, text=None):
        path = self.root / "evidence" / f"{name}.log"
        path.write_text(text or f"raw output of {name} in job {JOB}\n")
        data = path.read_bytes()
        return {"path": f"evidence/{name}.log", "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                "size": len(data)}

    def review(self, doc, verdict="pass", reviewer="security-reviewer"):
        subject = contract.review_subject(doc)
        return {"reviewer": reviewer, "subject": subject, "verdict": verdict,
                "evidence": self.evidence(f"review-{reviewer}-{verdict}-{subject[-12:]}",
                                          f"review of {subject}: {verdict}\n")}

    def invalid(self, doc, resign=True):
        """Invalid for the reason under test: the review is re-signed for the edited report first."""
        if resign and doc["independent_review"] is not None:
            doc["independent_review"]["subject"] = contract.review_subject(doc)
        with self.assertRaises(contract.ContractError):
            contract.validate_qualification_report(doc)

    def test_complete_report_passes_and_checks_are_mandatory(self):
        self.assertTrue(contract.qualification_passes(self.doc, self.root))
        missing = copy.deepcopy(self.doc)
        missing["checks"].pop()
        duplicate = copy.deepcopy(self.doc)
        duplicate["checks"][1]["id"] = duplicate["checks"][0]["id"]
        unknown = copy.deepcopy(self.doc)
        unknown["checks"][0]["id"] = "Q17"
        no_evidence = copy.deepcopy(self.doc)
        no_evidence["checks"][0]["evidence"] = []
        for doc in (missing, duplicate, unknown, no_evidence):
            self.invalid(doc)

    def test_the_tuple_pins_the_target_and_the_job(self):
        for key in contract.QUALIFICATION_TUPLE:
            doc = copy.deepcopy(self.doc)
            del doc["tuple"][key]
            with self.subTest(missing=key):
                self.invalid(doc)
        not_a_digest = copy.deepcopy(self.doc)
        not_a_digest["tuple"]["workload_image"] = "latest"
        self.invalid(not_a_digest)

    def test_each_passing_check_needs_evidence_of_its_own(self):
        shared = copy.deepcopy(self.doc)
        shared["checks"][1]["evidence"] = shared["checks"][0]["evidence"]
        self.invalid(shared)
        review_reuses_check = copy.deepcopy(self.doc)
        review_reuses_check["independent_review"]["evidence"] = review_reuses_check["checks"][0]["evidence"][0]
        self.invalid(review_reuses_check)

    def test_the_review_is_independent_and_bound_to_this_report(self):
        by_author = copy.deepcopy(self.doc)
        by_author["independent_review"] = self.review(by_author, reviewer="qualifier")
        self.invalid(by_author)
        other_report = copy.deepcopy(self.doc)
        other_report["checks"][0]["result"] = "fail"  # the review still names the old subject
        self.invalid(other_report, resign=False)

    def test_a_report_cannot_be_relabelled_or_rereviewed_without_new_evidence(self):
        relabelled = copy.deepcopy(self.doc)
        relabelled["tuple"]["job_id"] = "github-run-999-attempt-1"
        relabelled["independent_review"] = self.review(relabelled)  # even a fresh review: the checks name job 42
        with self.assertRaises(contract.ContractError) as caught:
            contract.qualification_passes(relabelled, self.root)
        self.assertIn("does not name the job", str(caught.exception))
        for old, new in (("github-run-42-attempt-10", "github-run-42-attempt-1"), ("job-12", "job-1"), ("job-1", "1")):
            renumbered = copy.deepcopy(self.doc)
            for check in renumbered["checks"]:
                check["evidence"] = [self.evidence(f"{check['id']}-{old}", f"raw output of {check['id']} in job {old}\n")]
            renumbered["tuple"]["job_id"] = new
            renumbered["independent_review"] = self.review(renumbered)
            with self.subTest(old=old, new=new), self.assertRaises(contract.ContractError):
                contract.qualification_passes(renumbered, self.root)
        resigned = copy.deepcopy(self.doc)
        resigned["tuple"]["host"] = "another-host"
        resigned["independent_review"]["subject"] = contract.review_subject(resigned)  # author re-signs, same evidence
        with self.assertRaises(contract.ContractError) as caught:
            contract.qualification_passes(resigned, self.root)
        self.assertIn("does not name the subject", str(caught.exception))
        target, policy = self.doc["target"], self.doc["policy_digest"]
        with self.assertRaises(contract.ContractError):
            contract.validate_capability_binding(capability(target, policy, True), relabelled, self.root,
                                                 "github-run-999-attempt-1")
        for case in ("Qualifier", "QUALIFIER"):
            by_author = copy.deepcopy(self.doc)
            by_author["independent_review"] = self.review(by_author, reviewer=case)
            with self.subTest(reviewer=case):
                self.invalid(by_author, resign=False)

    def test_a_failing_check_or_review_does_not_pass(self):
        failing = copy.deepcopy(self.doc)
        failing["checks"][-1]["result"] = "fail"
        failing["independent_review"] = self.review(failing)
        rejected = copy.deepcopy(self.doc)
        rejected["independent_review"] = self.review(rejected, verdict="fail")
        no_review = dict(copy.deepcopy(self.doc), independent_review=None)
        for doc in (failing, rejected, no_review):
            self.assertFalse(contract.qualification_passes(doc, self.root))

    def test_evidence_is_always_verified(self):
        (self.root / "evidence" / "B1.log").write_text("tampered\n")
        with self.assertRaises(contract.ContractError):
            contract.qualification_passes(self.doc, self.root)
        (self.root / "evidence" / "B1.log").unlink()
        with self.assertRaises(contract.ContractError):
            contract.qualification_passes(self.doc, self.root)
        outside_dir = tempfile.TemporaryDirectory()
        self.addCleanup(outside_dir.cleanup)
        outside = Path(outside_dir.name)
        (outside / "x.log").write_text(f"raw output of B1 in job {JOB}\n")
        (self.root / "evidence" / "B1.log").symlink_to(outside / "x.log")
        with self.assertRaises(contract.ContractError) as caught:
            contract.qualification_passes(self.doc, self.root)
        self.assertEqual("UNSAFE_PATH", caught.exception.code)

    def test_binding_needs_the_same_target_policy_job_and_a_passing_qualification(self):
        target, policy = self.doc["target"], self.doc["policy_digest"]
        contract.validate_capability_binding(capability(target, policy, True), self.doc, self.root, JOB)
        failing = copy.deepcopy(self.doc)
        failing["independent_review"] = self.review(failing, verdict="fail")
        for report, doc, job in ((capability("other-target", policy, True), self.doc, JOB),
                                 (capability(target, "sha256:" + "f" * 64, True), self.doc, JOB),
                                 (capability(target, policy, True), self.doc, "github-run-43-attempt-1"),
                                 (capability(target, policy, True), failing, JOB)):
            with self.subTest(target=report["target"], job=job), self.assertRaises(contract.ContractError):
                contract.validate_capability_binding(report, doc, self.root, job)

    def test_cli_exit_codes(self):
        def run(content, *extra, name="report.json"):
            path = self.root / name
            path.write_text(content if isinstance(content, str) else json.dumps(content))
            return subprocess.run([sys.executable, "-m", "agent_harness", "qualification", "--check",
                                   str(self.root / "report.json"), "--evidence-root", str(self.root), *extra],
                                  capture_output=True, text=True, check=False)
        passing = run(self.doc)
        self.assertEqual(0, passing.returncode, passing.stderr)
        self.assertEqual(contract.qualification_digest(self.doc), passing.stdout.split()[5])
        failing = copy.deepcopy(self.doc)
        failing["independent_review"] = self.review(failing, verdict="fail")
        self.assertEqual(1, run(failing).returncode)
        self.assertEqual(2, run("{not json").returncode)
        self.assertEqual(2, run("[" * 200_000).returncode)  # deep nesting is invalid, not "not passing"
        duplicated = json.dumps(self.doc)[:-1] + ', "target": "other"}'
        self.assertEqual(2, run(duplicated).returncode)
        run(self.doc)
        capability_path = self.root / "capability.json"
        capability_path.write_text(json.dumps(capability("other-target", self.doc["policy_digest"], True)))
        self.assertEqual(1, run(self.doc, "--capability-report", str(capability_path), "--job-id", JOB).returncode)
        self.assertEqual(2, run(self.doc, "--capability-report", str(capability_path)).returncode)  # no job id
        self.assertEqual(2, run(self.doc, "--job-id", JOB).returncode)  # a job id without a report is misuse
        missing_root = subprocess.run([sys.executable, "-m", "agent_harness", "qualification", "--check",
                                       str(self.root / "report.json"), "--evidence-root", str(self.root / "nope")],
                                      capture_output=True, text=True, check=False)
        self.assertEqual(2, missing_root.returncode)
        self.assertIn("nope", missing_root.stderr)
        capability_path.write_text("{")
        broken = run(self.doc, "--capability-report", str(capability_path), "--job-id", JOB)
        self.assertEqual(2, broken.returncode)
        self.assertIn("capability.json", broken.stderr)


if __name__ == "__main__":
    unittest.main()
