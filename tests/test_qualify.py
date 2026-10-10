"""qualify() (AH5-04c-1): verbatim probes, probe/policy digests and per-tuple session qualification (ADR 0002)."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from importlib import resources
from pathlib import Path
from unittest import mock

from agent_harness import contract, execution, qualification
from agent_harness.qualification import report as runner

# Showcase `tooling/agent-harness/qualification/<file>` blobs at main 830c5e6 (provenance.md): byte-identical copies.
SOURCE_BLOBS = {"q_probes.py": "f2e02e58635fafe4ac1613484ec105fffaa71089",
                "q_lifecycle.py": "705874a6a34c6347d35c6393222802e7861fa7e5",
                "b_probes.py": "1562b0c940c2ebd88b04438f634fad210f8fb959",
                "report.py": "a8f35d1196b643425c74108564348eaca0345389"}
TUPLE = {"host": "Docker_Desktop", "kernel": "7.0.14-linuxkit", "engine": "DockerEngine_29.8.2",
         "workload_image": "sha256:" + "9" * 64}


class FakeTarget:
    def execute_q01_q10(self, check_id, name, check_dir):
        return {"status": "pass", "reason_code": "OBSERVED", "stdout": "", "stderr": "", "details": {}}

    def execute_q11_q16(self, check_id, name, check_dir, job_id):
        return self.execute_q01_q10(check_id, name, check_dir)

    execute_b1_b10 = execute_q01_q10


class QualifyTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, patched in (("_target_tuple", lambda kind, docker, image, job_id, errors: {"job_id": job_id, **TUPLE}),
                              ("_build_probe_targets", lambda *a: (FakeTarget(), FakeTarget(), FakeTarget()))):
            patcher = mock.patch.object(runner, name, patched)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.layout = {"passed": True, "checks": {"stub": True}, "results": {}}
        patcher = mock.patch.object(execution, "probe_grading_layout", lambda *a, **k: self.layout)
        patcher.start()
        self.addCleanup(patcher.stop)

    def reviewed_reference(self):
        first = qualification.qualify("ref-job", self.root / "ref")
        self.assertFalse(first["qualified"])  # all checks pass, but no independent review yet
        doc, evidence = first["report_doc"], self.root / "ref" / "evidence"
        subject = contract.review_subject(doc)
        review = evidence / "review.txt"
        review.write_text(f"independent review of {subject}: pass\n")
        data = review.read_bytes()
        doc["independent_review"] = {"reviewer": "evaluator", "subject": subject, "verdict": "pass",
                                     "evidence": {"path": "review.txt", "size": len(data),
                                                  "sha256": "sha256:" + hashlib.sha256(data).hexdigest()}}
        self.assertTrue(contract.qualification_passes(doc, evidence))
        return doc, evidence

    def test_probes_are_byte_identical_to_the_showcase_source(self):
        here = resources.files("agent_harness.qualification")
        for name, blob in SOURCE_BLOBS.items():
            data = here.joinpath(name).read_bytes()
            self.assertEqual(blob, hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(), name)

    def test_session_qualifies_against_a_reviewed_reference_and_names_all_three_digests(self):
        reviewed = self.reviewed_reference()
        session = qualification.qualify("session-1", self.root / "s1", reviewed=reviewed)
        self.assertTrue(session["qualified"])
        record = json.loads((self.root / "s1" / "session.json").read_text())
        self.assertEqual((contract.qualification_digest(session["report_doc"]),
                          contract.qualification_digest(reviewed[0]), qualification.probe_digest(),
                          qualification.policy_digest()),
                         (record["qualification_digest"], record["reviewed_qualification_digest"],
                          record["probe_digest"], record["policy_digest"]))
        capability = json.loads((self.root / "s1" / "capability.json").read_text())
        contract.validate_capability_binding(capability, session["report_doc"], self.root / "s1" / "evidence",
                                             "session-1", reviewed=reviewed)
        self.assertEqual(qualification.probe_digest(), session["report_doc"]["tuple"]["probe_digest"])

    def test_session_on_other_probe_code_or_tuple_does_not_qualify(self):
        reviewed = self.reviewed_reference()
        with mock.patch.object(qualification, "probe_digest", lambda: "sha256:" + "0" * 64):
            self.assertFalse(qualification.qualify("s2", self.root / "s2", reviewed=reviewed)["qualified"])
        with mock.patch.object(runner, "_target_tuple",
                               lambda kind, docker, image, job_id, errors: {"job_id": job_id, **TUPLE,
                                                                            "kernel": "6.9"}):
            self.assertFalse(qualification.qualify("s3", self.root / "s3", reviewed=reviewed)["qualified"])

    def test_a_failing_check_or_unavailable_target_never_qualifies(self):
        reviewed = self.reviewed_reference()
        with mock.patch.object(FakeTarget, "execute_b1_b10",
                               lambda self, c, n, d: {"status": "fail", "reason_code": "X", "stdout": "", "stderr": "",
                                                      "details": {}}):
            self.assertFalse(qualification.qualify("s4", self.root / "s4", reviewed=reviewed)["qualified"])

        def unavailable(kind, docker, image, job_id, errors):
            errors.append("docker_info_unavailable")
            return {"job_id": job_id, **TUPLE}
        with mock.patch.object(runner, "_target_tuple", unavailable):
            result = qualification.qualify("s5", self.root / "s5", reviewed=reviewed)
        self.assertFalse(result["qualified"])
        self.assertEqual({"not-run"}, {c["result"] for c in result["report_doc"]["checks"]})

    def test_probe_flags_equal_the_backend_flags_and_digests_cover_code_and_layout(self):
        from agent_harness import execution
        from agent_harness.qualification import q_probes
        probe_flags = q_probes.DockerProbeTarget("python@sha256:" + "0" * 64)._base_args()
        workspace = [f for f in execution.QUALIFIED_RUN_FLAGS if f.startswith("--tmpfs=/workspace")]
        self.assertEqual(list(execution.QUALIFIED_RUN_FLAGS), probe_flags + workspace)
        self.assertEqual(("__init__.py", "b_probes.py", "q_lifecycle.py", "q_probes.py", "report.py"),
                         qualification.PROBE_MODULES)
        before = qualification.probe_digest()
        copy = self.root / "probes"
        copy.mkdir()
        for name in qualification.PROBE_MODULES:
            copy.joinpath(name).write_bytes(resources.files("agent_harness.qualification").joinpath(name).read_bytes())
        with mock.patch.object(qualification.resources, "files", lambda _package: copy):
            self.assertEqual(before, qualification.probe_digest())
            copy.joinpath("q_probes.py").write_bytes(copy.joinpath("q_probes.py").read_bytes() + b"#")
            self.assertNotEqual(before, qualification.probe_digest())
        policy = qualification.policy_digest()
        with mock.patch.dict(execution.GRADING_LAYOUT, {"options": ["-i"]}):
            self.assertNotEqual(policy, qualification.policy_digest())

    def test_capability_of_a_passing_session_is_launch_ready_with_qualified_isolation(self):
        session = qualification.qualify("s6", self.root / "s6", reviewed=self.reviewed_reference())
        self.assertEqual((True, True, True, ["qualified_isolation"], None),
                         tuple(session["capability_doc"][k] for k in
                               ("supported", "qualified", "launch_ready", "capabilities", "refusal")))
        first = qualification.qualify("s7", self.root / "s7")  # no reference: all pass, not qualified
        self.assertEqual((True, False, [], "NOT_QUALIFIED"),
                         tuple(first["capability_doc"][k] for k in ("supported", "qualified", "capabilities",
                                                                     "refusal")))

    def test_bad_job_id_symlinked_out_or_invalid_reference_fail_before_any_probe(self):
        with self.assertRaises(ValueError):
            qualification.qualify("job 1", self.root / "j1")
        (self.root / "target").mkdir()
        (self.root / "link").symlink_to(self.root / "target")
        with self.assertRaises(ValueError):
            qualification.qualify("j2", self.root / "link")
        with self.assertRaises(contract.ContractError):
            qualification.qualify("j3", self.root / "j3", reviewed=({"contract_version": 1}, self.root))
        self.assertFalse((self.root / "j1").exists() or (self.root / "j3").exists())
        self.assertEqual([], list((self.root / "target").iterdir()))

    def test_a_timed_out_check_is_retried_and_its_first_attempt_recorded(self):
        calls = {"B1": 0}

        def flaky(self, check_id, name, check_dir):
            if check_id == "B1":
                calls["B1"] += 1
                if calls["B1"] == 1:
                    raise subprocess.TimeoutExpired("docker", 30)
            return {"status": "pass", "reason_code": "OBSERVED", "stdout": "", "stderr": "", "details": {}}
        reviewed = self.reviewed_reference()
        with mock.patch.object(FakeTarget, "execute_b1_b10", flaky):
            result = qualification.qualify("r1", self.root / "r1", reviewed=reviewed)
        self.assertTrue(result["qualified"])
        self.assertEqual((2, ["B1"]), (calls["B1"], result["retried"]))
        retries = json.loads((self.root / "r1" / "evidence" / "retries.json").read_text())
        self.assertEqual("PROBE_EXCEPTION", retries["B1"][0]["reason_code"])

    def test_a_check_that_keeps_timing_out_stays_not_run(self):
        def always(self, check_id, name, check_dir):
            if check_id == "B8":
                raise subprocess.TimeoutExpired("docker", 30)
            return {"status": "pass", "reason_code": "OBSERVED", "stdout": "", "stderr": "", "details": {}}
        reviewed = self.reviewed_reference()
        with mock.patch.object(FakeTarget, "execute_b1_b10", always):
            result = qualification.qualify("r2", self.root / "r2", reviewed=reviewed, retries=2)
        self.assertFalse(result["qualified"])
        self.assertEqual(2, len(json.loads((self.root / "r2" / "evidence" / "retries.json").read_text())["B8"]))

    def test_a_failed_grading_layout_never_qualifies(self):
        reviewed = self.reviewed_reference()
        self.layout = {"passed": False, "checks": {"stub": False}, "results": {}}
        result = qualification.qualify("l1", self.root / "l1", reviewed=reviewed)
        self.assertEqual((False, "failed"), (result["qualified"], result["report_doc"]["tuple"]["grading_layout"]))

    def test_linux_docker_kind_accepts_a_non_desktop_engine(self):
        def native(kind, docker, image, job_id, errors):
            errors.append("docker_desktop_identity_mismatch")
            return {"job_id": job_id, **TUPLE, "host": "Ubuntu_24.04_LTS"}
        with mock.patch.object(runner, "_target_tuple", native):
            found, errors = qualification.live_tuple("linux-docker", "docker", "img@sha256:" + "0" * 64, "j")
            self.assertEqual(([], "Ubuntu_24.04_LTS"), (errors, found["host"]))
            self.assertEqual(["docker_desktop_identity_mismatch"],
                             qualification.live_tuple("docker-desktop", "docker", "img", "j")[1])
        with self.assertRaises(ValueError):
            qualification.live_tuple("windows", "docker", "img", "j")

    def test_out_must_be_new_and_empty(self):
        (self.root / "used").mkdir()
        (self.root / "used" / "x").write_text("x")
        with self.assertRaises(ValueError):
            qualification.qualify("j", self.root / "used")


if __name__ == "__main__":
    unittest.main()


class ShippedReferenceTest(unittest.TestCase):
    def test_shipped_reference_passes_and_names_this_librarys_code_and_policy(self):
        from agent_harness.qualification.reference import reviewed_reference
        report, evidence = reviewed_reference()
        self.assertTrue(contract.qualification_passes(report, evidence))
        self.assertEqual((qualification.probe_digest(), qualification.policy_digest()),
                         (report["tuple"]["probe_digest"], report["policy_digest"]))
        with self.assertRaises(contract.ContractError):
            reviewed_reference("no-such-target")
