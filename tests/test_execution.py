"""Offline launch API (AH5-05a): every result validates; cancel, timeout and drain are real process facts."""

import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_harness import contract, execution


def digest(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


class ExecutionTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        for name in ("workspace", "authority", "evidence"):
            (self.root / name).mkdir()
        self.repo = {"contract_version": 1, "repo_id": "repo", "base_sha": "a" * 40,
                     "workspace": str(self.root / "workspace"), "authority_root": str(self.root / "authority"),
                     "evidence_root": str(self.root / "evidence")}

    def request(self, request_id="req-1", capabilities=(), timeout=30, max_attempts=3):
        return contract.validate_request({
            "contract_version": 1, "request_id": request_id, "trial_id": None, "task_digest": digest("task"),
            "config_digest": digest("config"), "provider": "fake", "model": "fake-model", "settings": {},
            "capabilities": list(capabilities), "timeout_seconds": timeout, "max_attempts": max_attempts,
            "input_bindings": []})

    def run_to_end(self, backend, request=None):
        request = request or self.request()
        result = execution.launch(request, self.repo, backend).result(timeout=60)
        contract.validate_result(result, request)
        return result

    def process(self, code, **kwargs):
        return execution.ProcessBackend([sys.executable, "-c", code], grace=1.0, **kwargs)

    def test_scripted_outcomes_validate(self):
        units = {"input_tokens": 3, "output_tokens": 4, "cache_read_tokens": 0, "cache_write_tokens": 0}
        done = self.run_to_end(execution.ScriptedBackend(
            [{"outcome": "error", "units": units}, {"outcome": "completed", "units": units}],
            completion="accepted", candidate=b"patch"), self.request(capabilities=["usage"]))
        self.assertEqual(("completed", 0, "accepted", "complete", "fake", 2),
                         (done["outcome"], done["exit_code"], done["completion"], done["usage_completeness"],
                          done["isolation_level"], len(done["attempts"])))
        sealed = Path(self.repo["evidence_root"]) / done["candidate"]["path"]
        self.assertEqual(done["candidate"]["sha256"], "sha256:" + hashlib.sha256(sealed.read_bytes()).hexdigest())
        for i, outcome in enumerate(("error", "timeout", "unknown")):
            with self.subTest(outcome=outcome):
                result = self.run_to_end(execution.ScriptedBackend(
                    [{"outcome": outcome}], error_code="PROVIDER_ERROR"), self.request(f"req-{outcome}-{i}"))
                self.assertEqual(outcome, result["outcome"])
                self.assertEqual("unconfirmed" if outcome != "error" else "confirmed", result["drain"])

    def test_scripted_cancel_while_running(self):
        launched = execution.launch(self.request(), self.repo, execution.ScriptedBackend(duration=30))
        launched.cancel()
        result = launched.result(timeout=10)
        self.assertEqual(("cancel", True, "confirmed"), (result["outcome"], result["cancel_requested"], result["drain"]))

    def test_process_cancel_stops_child_and_grandchild_and_confirms_drain(self):
        pid_file = self.root / "workspace" / "grandchild.pid"
        code = ("import subprocess, sys, time\n"
                "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                f"open({str(pid_file)!r}, 'w').write(str(g.pid))\n"
                "time.sleep(60)\n")
        launched = execution.launch(self.request(), self.repo, self.process(code))
        for _ in range(200):
            if pid_file.exists() and pid_file.read_text():
                break
            time.sleep(0.05)
        launched.cancel()
        result = launched.result(timeout=30)
        contract.validate_result(result, self.request())
        self.assertEqual(("cancel", True, "confirmed", "controlled", None),
                         (result["outcome"], result["cancel_requested"], result["drain"], result["isolation_level"],
                          result["exit_code"]))
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)

    def test_surviving_descendant_leaves_drain_unconfirmed(self):
        with mock.patch.object(execution, "_group_alive", return_value=True):
            launched = execution.launch(self.request(), self.repo, self.process("import time; time.sleep(60)"))
            launched.cancel()
            result = launched.result(timeout=30)
        self.assertEqual(("cancel", "unconfirmed"), (result["outcome"], result["drain"]))

    def test_process_timeout_is_never_a_confirmed_drain(self):
        result = self.run_to_end(self.process("import time; time.sleep(60)"), self.request(timeout=1))
        self.assertEqual(("timeout", "unconfirmed", None), (result["outcome"], result["drain"], result["exit_code"]))

    def test_process_exit_code_and_sealed_candidate(self):
        result = self.run_to_end(self.process("open('out.txt', 'w').write('candidate'); raise SystemExit(3)",
                                              candidate="out.txt"))
        self.assertEqual(("completed", 3, "confirmed", 9),
                         (result["outcome"], result["exit_code"], result["drain"], result["candidate"]["size"]))

    def test_launch_failure_is_an_error_not_unknown(self):
        result = self.run_to_end(execution.ProcessBackend([str(self.root / "missing-binary")]))
        self.assertEqual(("error", "LAUNCH_FAILED"), (result["outcome"], result["error_code"]))

    def test_missing_capability_is_rejected_without_side_effects(self):
        marker = self.root / "workspace" / "started"
        for capabilities, code in ((["qualified_isolation"], "NOT_QUALIFIED"), (["usage"], "CAPABILITY_UNSUPPORTED")):
            with self.subTest(capabilities=capabilities):
                request = self.request(capabilities=capabilities)
                result = execution.launch(request, self.repo,
                                          self.process(f"open({str(marker)!r}, 'w')")).result(timeout=10)
                contract.validate_result(result, request)
                self.assertEqual(("rejected", code), (result["outcome"], result["error_code"]))
        self.assertFalse(marker.exists())
        self.assertEqual([], list(Path(self.repo["evidence_root"]).iterdir()))

    def test_relaunch_returns_the_stored_result_and_refuses_another_request(self):
        first = self.run_to_end(execution.ScriptedBackend())
        again = execution.launch(self.request(), self.repo, execution.ScriptedBackend()).result(timeout=10)
        self.assertEqual(first, again)
        with self.assertRaises(contract.ContractError) as caught:
            execution.launch(self.request(timeout=99), self.repo, execution.ScriptedBackend())
        self.assertEqual("BINDING_MISMATCH", caught.exception.code)

    def test_start_without_result_reconciles_to_unknown_and_never_reruns(self):
        root = Path(self.repo["evidence_root"]) / "executions"
        root.mkdir()
        (root / "req-1.started").write_text(json.dumps(
            {"contract_version": 1, "request_id": "req-1", "request_digest": contract.request_digest(self.request())}))
        marker = self.root / "workspace" / "ran"
        result = execution.launch(self.request(), self.repo,
                                  self.process(f"open({str(marker)!r}, 'w')")).result(timeout=10)
        contract.validate_result(result, self.request())
        self.assertEqual(("unknown", "unconfirmed"), (result["outcome"], result["drain"]))
        self.assertFalse(marker.exists())

    def test_broken_backend_result_becomes_unknown(self):
        class Broken(execution.ScriptedBackend):
            def run(self, *args):
                return {**super().run(*args), "completion": "accepted", "outcome": "error"}
        result = self.run_to_end(Broken())
        self.assertEqual(("unknown", None), (result["outcome"], result["completion"]))

    def test_offline_backends_report_not_qualified(self):
        for backend in (execution.ScriptedBackend(), execution.ProcessBackend(["true"])):
            with self.subTest(backend=backend.kind):
                report = contract.validate_capability_report(backend.report())
                self.assertEqual((False, False, "NOT_QUALIFIED"),
                                 (report["qualified"], report["launch_ready"], report["refusal"]))


if __name__ == "__main__":
    unittest.main()
