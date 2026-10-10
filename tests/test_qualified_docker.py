"""QualifiedDockerBackend (AH5-04c-2): grading on the qualified target, checked against a fake `docker`.

The fake records every docker argv, extracts the streamed workspace, and plays the container per FAKE_MODE.
An opt-in real run: AGENT_HARNESS_REAL_DOCKER=1 with the qualified image present (see the last test).
"""

import hashlib
import json
import os
import stat
import tempfile
import textwrap
import unittest
from pathlib import Path

from agent_harness import contract, execution

JOB = "local-session-7"
IMAGE_DIGEST = "sha256:9d72651cf7018c1f6a1dd6fd02bd68286631c33620bc0f37b0675b21aab915d5"
IMAGE = f"python@{IMAGE_DIGEST}"
FAKE_DOCKER = r'''#!/usr/bin/env python3
import io, json, os, sys, tarfile, time, pathlib
root = pathlib.Path(os.environ["FAKE_ROOT"])
args = sys.argv[1:]
with open(root / "docker-calls.jsonl", "a") as log:
    log.write(json.dumps(args) + "\n")
state_dir = root / "containers"; state_dir.mkdir(exist_ok=True)
mode = os.environ.get("FAKE_MODE", "answer")
if args[:1] == ["version"]: print(os.environ.get("FAKE_ENGINE", "29.8.2")); sys.exit(0)
if args[:1] == ["info"]: print("Docker Desktop|" + os.environ.get("FAKE_KERNEL", "7.0.14-linuxkit")); sys.exit(0)
if args[:2] == ["image", "inspect"]: print(json.dumps([os.environ.get("FAKE_IMAGE", args[-1])])); sys.exit(0)
if args[:1] == ["run"]:
    name = next(a.split("=", 1)[1] for a in args if a.startswith("--name="))
    mounts = {}
    for i, a in enumerate(args):
        if a == "--mount":
            parts = dict(p.split("=", 1) for p in args[i + 1].split(",") if "=" in p)
            mounts[parts["dst"]] = parts["src"]
    workspace = root / "seen-workspace"
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as archive:
        archive.extractall(workspace, filter="data")
    (state_dir / name).write_text(json.dumps({"Running": True, "OOMKilled": False, "ExitCode": None}))
    out = pathlib.Path(mounts["/output"])
    if mode == "sleep":
        time.sleep(60)
    if mode == "flood":
        sys.stdout.write("x" * (2 << 20)); sys.stdout.flush()
    if mode == "symlink":
        (out / "answers.json").symlink_to("/etc/passwd")
    elif mode == "big":
        (out / "answers.json").write_bytes(b"x" * ((1 << 20) + 1))
    elif mode != "sleep":
        cases = json.loads(pathlib.Path(mounts["/inputs"], "cases.json").read_text())
        (out / "answers.json").write_text(json.dumps({c["id"]: {"value": 1} for c in cases["cases"]}))
    oom = mode == "oom"
    (state_dir / name).write_text(json.dumps({"Running": False, "OOMKilled": oom, "ExitCode": 137 if oom else 0}))
    sys.exit(137 if oom else 0)
if args[:1] == ["kill"]:
    p = state_dir / args[1]
    if p.exists():
        p.write_text(json.dumps({"Running": False, "OOMKilled": False, "ExitCode": 137}))
    sys.exit(0)
if args[:1] == ["inspect"]:
    p = state_dir / args[-1]
    print(p.read_text() if p.exists() else "null"); sys.exit(0 if p.exists() else 1)
if args[:1] == ["rm"]:
    if mode != "stuck":
        (state_dir / args[-1]).unlink(missing_ok=True)
    sys.exit(0)
if args[:1] == ["ps"]:
    name = next(a.split("=", 2)[2] for a in args if a.startswith("--filter=name="))[1:-1]
    print("deadbeef" if (state_dir / name).exists() else ""); sys.exit(0)
sys.exit(2)
'''


class QualifiedDockerTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        for name in ("workspace", "evidence", "qual", "qual/evidence", "bin"):
            (self.root / name).mkdir()
        docker = self.root / "bin" / "docker"
        docker.write_text(FAKE_DOCKER)
        docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
        self.docker = str(docker)
        os.environ["FAKE_ROOT"] = str(self.root)
        os.environ["FAKE_MODE"] = "answer"
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in
                                 ("FAKE_ROOT", "FAKE_MODE", "FAKE_ENGINE", "FAKE_KERNEL", "FAKE_IMAGE")])
        (self.root / "workspace" / "src").mkdir()
        (self.root / "workspace" / "src" / "mod.py").write_text("def f(x):\n    return x\n")
        (self.root / "workspace" / ".git").mkdir()
        (self.root / "workspace" / ".git" / "config").write_text("[core]\n")
        (self.root / "workspace" / "link").symlink_to("/etc/passwd")
        self.cases = self.root / "cases.json"
        self.cases.write_text(json.dumps({"module": "src.mod", "cases": [{"id": "c1", "function": "f", "args": [1]}]}))
        self.qualification = self.report()
        self.capability = {"contract_version": 1, "target": "showcase-docker-desktop-linux-guest",
                           "policy_digest": self.qualification["policy_digest"], "discovered": True,
                           "supported": True, "qualified": True, "launch_ready": False,
                           "capabilities": ["qualified_isolation"], "refusal": "BACKEND_UNAVAILABLE"}

    def evidence(self, name, text=None, job=JOB):
        path = self.root / "qual" / "evidence" / f"{name}.log"
        path.write_text(text or f"raw output of {name} in job {job}\n")
        data = path.read_bytes()
        return {"path": f"evidence/{name}.log", "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                "size": len(data)}

    def report(self, job=JOB, review=True, **extra):
        doc = {"contract_version": 1, "target": "showcase-docker-desktop-linux-guest",
               "policy_digest": "sha256:" + "4" * 64, "author": "qualifier",
               "tuple": {"job_id": job, "host": "Docker_Desktop", "kernel": "7.0.14-linuxkit",
                         "engine": "DockerEngine_29.8.2", "workload_image": IMAGE_DIGEST, **extra},
               "checks": [{"id": i, "result": "pass", "evidence": [self.evidence(i, job=job)]}
                          for i in contract.QUALIFICATION_CHECKS],
               "independent_review": None, "created_at": "2026-10-10T00:00:00Z"}
        if not review:
            return contract.validate_qualification_report(doc)
        subject = contract.review_subject(doc)
        doc["independent_review"] = {"reviewer": "evaluator", "subject": subject, "verdict": "pass",
                                     "evidence": self.evidence("review", f"review of {subject}: pass\n")}
        return contract.validate_qualification_report(doc)

    def backend(self, **kw):
        args = {"qualification_evidence": str(self.root / "qual"), "capability_report": self.capability,
                "job_id": JOB, "image": IMAGE, "command": ["python3", "-c", "DRIVER", "/inputs/cases.json",
                                                           "/output/answers.json"],
                "cases": str(self.cases), "docker": self.docker, "grace": 0.2}
        args.update(kw)
        return execution.QualifiedDockerBackend(self.qualification, **args)

    def request(self, request_id="grade-1", limits=execution.QUALIFIED_LIMITS, timeout=30, version=2):
        doc = {"contract_version": version, "request_id": request_id, "trial_id": None,
               "task_digest": "sha256:" + "1" * 64, "config_digest": "sha256:" + "2" * 64, "provider": "grader",
               "model": "driver-1", "settings": {}, "capabilities": ["qualified_isolation"],
               "timeout_seconds": timeout, "max_attempts": 1, "input_bindings": []}
        if version == 2:
            doc["limits"] = dict(limits) if limits is not None else None
        return contract.validate_request(doc)

    def grade(self, backend, request):
        result = execution.launch(request, backend, workspace=str(self.root / "workspace"),
                                  evidence_root=str(self.root / "evidence")).result(120)
        return contract.validate_result(result, request)

    def calls(self):
        return [json.loads(line) for line in (self.root / "docker-calls.jsonl").read_text().splitlines()]

    def test_grades_with_exactly_the_qualified_flags_and_seals_answers_after_destroy(self):
        result = self.grade(self.backend(), self.request())
        self.assertEqual(("completed", 0, "qualified", "confirmed"),
                         (result["outcome"], result["exit_code"], result["isolation_level"], result["drain"]))
        self.assertEqual({"id": "showcase-docker-desktop-linux-guest", "image_digest": IMAGE_DIGEST,
                          "qualification_digest": contract.qualification_digest(self.qualification)},
                         result["target"])
        self.assertEqual(({**execution.QUALIFIED_LIMITS, "timeout_seconds": 30}, None, False),
                         (result["limits"]["applied"], result["limits"]["fired"], result["limits"]["output_truncated"]))
        run = next(c for c in self.calls() if c[:1] == ["run"])
        for flag in ("--network=none", "--read-only", "--user=65532:65532", "--cap-drop=ALL",
                     "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=512m", "--memory-swap=512m",
                     "--cpus=1", "--tmpfs=/tmp:rw,noexec,nosuid,size=16m", "--tmpfs=/dev/shm:ro,noexec,nosuid,size=1m",
                     "--tmpfs=/workspace:rw,noexec,nosuid,size=251658240,mode=1777", "--workdir=/workspace",
                     "--env=PYTHONPATH=/workspace", "--env=PYTHONDONTWRITEBYTECODE=1", "--env=HOME=/tmp"):
            self.assertIn(flag, run)
        self.assertEqual([], [a for a in run if a.startswith("--env=") and a.split("=", 2)[1] not in
                              ("PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "HOME")])
        self.assertTrue(any(a.endswith("dst=/inputs,readonly") for a in run))
        self.assertEqual(IMAGE, run[run.index("sh") - 1])
        self.assertEqual(["python3", "-c", "DRIVER", "/inputs/cases.json", "/output/answers.json"], run[-5:])
        seen = self.root / "seen-workspace"
        self.assertTrue((seen / "src" / "mod.py").is_file())
        self.assertFalse((seen / ".git").exists() or (seen / "link").exists())  # no .git, no links streamed
        order = [c[0] for c in self.calls()]
        self.assertLess(order.index("rm"), order.index("ps"))  # absence confirmed after removal
        answers = result["artifacts"][0]
        data = (self.root / "evidence" / answers["path"]).read_bytes()
        self.assertEqual(("answers", {"c1": {"value": 1}}), (answers["name"], json.loads(data)))

    def test_refused_before_launch_without_exact_limits_or_on_tuple_drift(self):
        backend = self.backend()
        for i, request in enumerate((self.request("v1", version=1),
                                     self.request("other", limits={**execution.QUALIFIED_LIMITS, "pids": 128}))):
            # (a qualified_isolation request without limits is already invalid in contract v2)
            self.assertEqual(("rejected", "CAPABILITY_UNSUPPORTED"),
                             (self.grade(backend, request)["outcome"], self.grade(backend, request)["error_code"]))
        for i, (var, value) in enumerate((("FAKE_ENGINE", "29.9.0"), ("FAKE_KERNEL", "6.0"),
                                          ("FAKE_IMAGE", "python@sha256:" + "0" * 64))):
            os.environ[var] = value
            result = self.grade(backend, self.request(f"drift-{i}"))
            self.assertEqual(("rejected", "NOT_QUALIFIED"), (result["outcome"], result["error_code"]))
            self.assertIn("differs from the qualified tuple", backend.rejection_reasons[f"drift-{i}"])
            os.environ.pop(var)
        self.assertFalse(any(c[:1] == ["run"] for c in self.calls()))

    def test_session_report_qualifies_by_matching_a_reviewed_tuple(self):
        probe = {"probe_digest": "sha256:" + "7" * 64}
        reviewed_root = self.root / "reviewed"
        (reviewed_root / "evidence").mkdir(parents=True)
        reviewed = self.report(job="reviewed-job", **probe)  # evidence lands in qual/; copy it for the reviewed root
        for ref in [r for c in reviewed["checks"] for r in c["evidence"]] + [reviewed["independent_review"]["evidence"]]:
            (reviewed_root / ref["path"]).write_bytes((self.root / "qual" / ref["path"]).read_bytes())
        session = self.report(review=False, **probe)
        with self.assertRaises(contract.ContractError):
            execution.QualifiedDockerBackend(session, qualification_evidence=str(self.root / "qual"),
                                             capability_report=self.capability, job_id=JOB, image=IMAGE,
                                             command=["true"], cases=str(self.cases), docker=self.docker)
        backend = execution.QualifiedDockerBackend(
            session, qualification_evidence=str(self.root / "qual"), capability_report=self.capability, job_id=JOB,
            image=IMAGE, command=["true"], cases=str(self.cases), docker=self.docker,
            reviewed=(reviewed, reviewed_root))
        self.assertEqual(contract.qualification_digest(session), backend.target["qualification_digest"])

    def test_session_binding_is_checked_at_construction(self):
        with self.assertRaises(contract.ContractError):
            self.backend(job_id="another-job")
        with self.assertRaises(contract.ContractError):
            self.backend(capability_report={**self.capability, "qualified": False})
        with self.assertRaises(ValueError):
            self.backend(image="python:3.13")

    def test_timeout_and_oom_name_the_fired_limit(self):
        os.environ["FAKE_MODE"] = "sleep"
        result = self.grade(self.backend(), self.request("t", timeout=1))
        self.assertEqual(("timeout", "timeout"), (result["outcome"], result["limits"]["fired"]))
        self.assertIn(["kill"], [c[:1] for c in self.calls()])
        os.environ["FAKE_MODE"] = "oom"
        result = self.grade(self.backend(), self.request("o"))
        self.assertEqual(("error", contract.LIMIT_EXCEEDED, "oom"),
                         (result["outcome"], result["error_code"], result["limits"]["fired"]))

    def test_unsafe_or_oversized_answers_are_not_sealed_and_output_is_capped(self):
        for i, mode in enumerate(("symlink", "big")):
            os.environ["FAKE_MODE"] = mode
            self.assertEqual([], self.grade(self.backend(), self.request(f"u{i}"))["artifacts"])
        os.environ["FAKE_MODE"] = "flood"
        self.assertTrue(self.grade(self.backend(), self.request("f"))["limits"]["output_truncated"])

    def test_container_that_is_not_confirmed_gone_reads_nothing(self):
        os.environ["FAKE_MODE"] = "stuck"
        result = self.grade(self.backend(), self.request("s"))
        self.assertEqual(("unconfirmed", []), (result["drain"], result["artifacts"]))
        self.assertNotEqual("completed", result["outcome"])

    @unittest.skipUnless(os.environ.get("AGENT_HARNESS_REAL_DOCKER") == "1", "opt-in real Docker run")
    def test_real_docker_grades_one_request(self):
        driver = textwrap.dedent("""\
            import importlib, json, sys
            spec = json.load(open(sys.argv[1]))
            mod = importlib.import_module(spec["module"])
            out = {c["id"]: {"value": getattr(mod, c["function"])(*c["args"])} for c in spec["cases"]}
            json.dump(out, open(sys.argv[2], "w"))
            """)
        backend = self.backend(docker="docker", command=["python3", "-c", driver, "/inputs/cases.json",
                                                         "/output/answers.json"])
        result = self.grade(backend, self.request("real"))
        data = (self.root / "evidence" / result["artifacts"][0]["path"]).read_bytes()
        self.assertEqual(("completed", {"c1": {"value": 1}}), (result["outcome"], json.loads(data)))


if __name__ == "__main__":
    unittest.main()
