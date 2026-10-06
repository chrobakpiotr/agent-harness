"""Target qualification reports: every mandatory check recorded separately with evidence (AH5-04b-r)."""

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from importlib import resources
from pathlib import Path

from agent_harness import contract

EXAMPLE = json.loads((resources.files("agent_harness") / "contract_fixtures" / "qualification-example.json")
                     .read_text())


def capability(target, policy_digest, qualified):
    return {"contract_version": 1, "target": target, "policy_digest": policy_digest, "discovered": True,
            "supported": True, "qualified": qualified, "launch_ready": qualified, "capabilities": ["cancel"],
            "refusal": None if qualified else "NOT_QUALIFIED"}


class QualificationReportTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.doc = copy.deepcopy(EXAMPLE)
        for ref in [r for c in self.doc["checks"] for r in c["evidence"]] + [self.doc["independent_review"]["evidence"]]:
            path = self.root / "evidence" / ref["path"].split("/")[-1]
            path.parent.mkdir(exist_ok=True)
            path.write_text(f"evidence for {path.stem}\n")

    def invalid(self, doc):
        with self.assertRaises(contract.ContractError):
            contract.validate_qualification_report(doc)

    def test_complete_report_passes_and_every_check_is_mandatory(self):
        self.assertTrue(contract.qualification_passes(self.doc))
        self.assertEqual(26, len(self.doc["checks"]))
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

    def test_a_failing_check_or_review_does_not_pass(self):
        failing = copy.deepcopy(self.doc)
        failing["checks"][-1].update(result="fail")
        no_review = dict(copy.deepcopy(self.doc), independent_review=None)
        rejected = copy.deepcopy(self.doc)
        rejected["independent_review"]["verdict"] = "fail"
        not_run = copy.deepcopy(self.doc)
        not_run["checks"][3].update(result="not-run", evidence=[])
        for doc in (failing, no_review, rejected, not_run):
            self.assertFalse(contract.qualification_passes(doc))

    def test_evidence_files_must_match(self):
        contract.verify_qualification_evidence(self.doc, self.root)
        (self.root / "evidence" / "B1.log").write_text("tampered\n")
        with self.assertRaises(contract.ContractError):
            contract.verify_qualification_evidence(self.doc, self.root)
        (self.root / "evidence" / "B1.log").unlink()
        with self.assertRaises(contract.ContractError):
            contract.verify_qualification_evidence(self.doc, self.root)
        (self.root / "evidence" / "B1.log").write_text("evidence for B1\n")
        outside = self.root.parent / f"{self.root.name}-outside"
        outside.mkdir()
        self.addCleanup(lambda: (outside / "x.log").unlink(missing_ok=True) or outside.rmdir())
        (outside / "x.log").write_text("evidence for B1\n")
        (self.root / "evidence" / "B1.log").unlink()
        (self.root / "evidence" / "B1.log").symlink_to(outside / "x.log")
        with self.assertRaises(contract.ContractError) as caught:
            contract.verify_qualification_evidence(self.doc, self.root)
        self.assertEqual("UNSAFE_PATH", caught.exception.code)

    def test_capability_report_binds_only_to_a_passing_qualification_of_its_target(self):
        target, policy = self.doc["target"], self.doc["policy_digest"]
        contract.validate_capability_binding(capability(target, policy, True), self.doc)
        contract.validate_capability_binding(capability(target, policy, False), self.doc)
        failing = copy.deepcopy(self.doc)
        failing["checks"][0]["result"] = "fail"
        for report, doc in ((capability("other-target", policy, True), self.doc),
                            (capability(target, "sha256:" + "f" * 64, True), self.doc),
                            (capability(target, policy, True), failing)):
            with self.subTest(report=report["target"]), self.assertRaises(contract.ContractError):
                contract.validate_capability_binding(report, doc)

    def test_cli_exit_codes(self):
        def run(doc, *extra):
            path = self.root / "report.json"
            path.write_text(json.dumps(doc) if isinstance(doc, dict) else doc)
            return subprocess.run([sys.executable, "-m", "agent_harness", "qualification", "--check", str(path),
                                   *extra], capture_output=True, check=False).returncode
        self.assertEqual(0, run(self.doc, "--evidence-root", str(self.root)))
        failing = copy.deepcopy(self.doc)
        failing["checks"][0]["result"] = "fail"
        self.assertEqual(1, run(failing))
        self.assertEqual(2, run("{not json"))
        (self.root / "evidence" / "B1.log").write_text("tampered\n")
        self.assertEqual(2, run(self.doc, "--evidence-root", str(self.root)))
        report = self.root / "capability.json"
        report.write_text(json.dumps(capability("other-target", self.doc["policy_digest"], True)))
        self.assertEqual(1, run(self.doc, "--capability-report", str(report)))
        digest = subprocess.run([sys.executable, "-m", "agent_harness", "qualification", "--check",
                                 str(self.root / "report.json")], capture_output=True, text=True, check=False).stdout.split()[3]
        self.assertEqual(contract.qualification_digest(self.doc), digest)
        self.assertEqual(digest, "sha256:" + hashlib.sha256(json.dumps(
            self.doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
