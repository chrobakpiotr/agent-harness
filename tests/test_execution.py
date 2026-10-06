"""Offline launch API (AH5-05a): every result validates; cancel, timeout and drain are real process facts."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
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
        (root / (execution._slot("req-1") + ".started")).write_text(json.dumps(
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

    def test_request_id_cannot_escape_the_evidence_root(self):
        evidence = Path(self.repo["evidence_root"])
        self.run_to_end(execution.ScriptedBackend(candidate=b"a"), self.request("a"))
        for request_id in ("a/../../../ESCAPED", "x/y"):
            with self.subTest(request_id=request_id):
                result = self.run_to_end(execution.ScriptedBackend(candidate=b"b"), self.request(request_id))
                self.assertEqual("completed", result["outcome"])
                self.assertTrue((evidence / result["candidate"]["path"]).resolve().is_relative_to(evidence))
        self.assertEqual(["authority", "evidence", "workspace"], sorted(p.name for p in self.root.iterdir()))
        self.assertFalse(any(p.name.startswith("ESCAPED") for p in self.root.rglob("*")))

    def test_a_relaunch_racing_a_running_launch_never_hangs_it(self):
        first = execution.launch(self.request(), self.repo, execution.ScriptedBackend(duration=1))
        other_process_view = dict(self.repo, evidence_root=self.repo["evidence_root"] + "/")  # same root, other key
        with mock.patch.dict(execution._RUNNING, clear=True):
            second = execution.launch(self.request(), other_process_view, execution.ScriptedBackend()).result(10)
        self.assertEqual("unknown", second["outcome"])
        self.assertEqual(second, first.result(timeout=10))  # the first record wins; nothing hangs
        self.assertEqual({}, {k: v for k, v in execution._RUNNING.items() if v is first})

    def test_concurrent_launches_of_one_request_run_once(self):
        runs = []

        class Counting(execution.ScriptedBackend):
            def run(self, *args):
                runs.append(1)
                return super().run(*args)
        backend = Counting(duration=0.2)
        executions, barrier = [], threading.Barrier(20)

        def launch_together():
            barrier.wait()
            executions.append(execution.launch(self.request(), self.repo, backend))
        threads = [threading.Thread(target=launch_together) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        results = {json.dumps(e.result(timeout=10), sort_keys=True) for e in executions}
        self.assertEqual((1, 1), (len(runs), len(results)))
        self.assertEqual("completed", json.loads(results.pop())["outcome"])  # not degraded to unknown

    def test_publish_is_create_once(self):
        path = self.root / "record.json"
        execution._publish(path, {"n": 1})
        with self.assertRaises(FileExistsError):
            execution._publish(path, {"n": 2})
        self.assertEqual({"n": 1}, json.loads(path.read_text()))
        self.assertEqual(["record.json"], sorted(p.name for p in self.root.iterdir() if p.is_file()))

    def test_cancel_terminates_gracefully_then_kills_what_ignores_it(self):
        graceful = self.root / "workspace" / "got-sigterm"
        stubborn_pid = self.root / "workspace" / "stubborn.pid"
        stubborn = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
        code = ("import signal, subprocess, sys, time\n"
                f"s = subprocess.Popen([sys.executable, '-c', {stubborn!r}])\n"
                f"open({str(stubborn_pid)!r}, 'w').write(str(s.pid))\n"
                f"signal.signal(signal.SIGTERM, lambda *_: (open({str(graceful)!r}, 'w'), sys.exit(0)))\n"
                "time.sleep(60)\n")
        launched = execution.launch(self.request(), self.repo, self.process(code))
        for _ in range(200):
            if stubborn_pid.exists() and stubborn_pid.read_text():
                break
            time.sleep(0.05)
        time.sleep(0.3)
        launched.cancel()
        result = launched.result(timeout=30)
        self.assertEqual(("cancel", "confirmed"), (result["outcome"], result["drain"]))
        self.assertTrue(graceful.exists())  # SIGTERM first
        with self.assertRaises(ProcessLookupError):  # SIGKILL for what ignores it
            os.kill(int(stubborn_pid.read_text()), 0)

    def test_group_without_permission_counts_as_alive(self):
        with mock.patch.object(execution.os, "killpg", side_effect=PermissionError):
            self.assertTrue(execution._group_alive(12345))
        with mock.patch.object(execution.os, "killpg", side_effect=ProcessLookupError):
            self.assertFalse(execution._group_alive(12345))

    def test_broken_backend_after_cancel_keeps_cancel_requested(self):
        class BrokenAfterCancel(execution.ScriptedBackend):
            def run(self, request, repo, cancelled, *args):
                cancelled.wait(10)
                raise RuntimeError("backend lost")
        launched = execution.launch(self.request(), self.repo, BrokenAfterCancel())
        launched.cancel()
        result = launched.result(timeout=10)
        self.assertEqual(("unknown", True, "unconfirmed"),
                         (result["outcome"], result["cancel_requested"], result["drain"]))

    def test_running_children_are_stopped_when_the_caller_exits(self):
        pid_file = self.root / "workspace" / "child.pid"
        child = self.root / "child.py"
        child.write_text(f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n")
        driver = self.root / "driver.py"
        driver.write_text(
            "import os, sys, time\n"
            "from agent_harness import execution\n"
            f"execution.launch({self.request()!r}, {self.repo!r},\n"
            f"                 execution.ProcessBackend([sys.executable, {str(child)!r}], grace=1.0))\n"
            "for _ in range(200):\n"
            f"    if os.path.exists({str(pid_file)!r}):\n"
            "        break\n"
            "    time.sleep(0.05)\n")
        subprocess.run([sys.executable, str(driver)], check=True, timeout=60)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)

    def test_candidate_must_stay_inside_the_workspace(self):
        with self.assertRaises(ValueError):
            execution.ProcessBackend(["true"], candidate="../out")
        outside = self.root / "outside.txt"
        outside.write_text("secret")
        (self.root / "workspace" / "link.txt").symlink_to(outside)
        result = self.run_to_end(self.process("pass", candidate="link.txt"))
        self.assertIsNone(result["candidate"])

    def test_stored_result_of_another_request_or_a_corrupt_marker_is_malformed(self):
        self.run_to_end(execution.ScriptedBackend())
        root = Path(self.repo["evidence_root"]) / "executions"
        slot = execution._slot("req-1")
        other = dict(self.request(), timeout_seconds=99)
        (root / f"{slot}.json").unlink()
        execution._publish(root / f"{slot}.json", execution._unknown(other, execution.ScriptedBackend(), None,
                                                                     False, None))
        with self.assertRaises(contract.ContractError) as caught:
            execution.launch(self.request(), self.repo, execution.ScriptedBackend())
        self.assertEqual("MALFORMED", caught.exception.code)
        (root / f"{slot}.started").write_text("{")
        with self.assertRaises(contract.ContractError) as caught:
            execution.launch(self.request(), self.repo, execution.ScriptedBackend())
        self.assertEqual("MALFORMED", caught.exception.code)

    def test_changed_request_while_running_is_refused(self):
        running = execution.launch(self.request(), self.repo, execution.ScriptedBackend(duration=5))
        with self.assertRaises(contract.ContractError) as caught:
            execution.launch(self.request(timeout=99), self.repo, execution.ScriptedBackend())
        self.assertEqual("BINDING_MISMATCH", caught.exception.code)
        running.cancel()
        running.result(timeout=10)

    def test_changed_request_reusing_an_id_is_refused_before_capability_checks(self):
        self.run_to_end(execution.ScriptedBackend())
        with self.assertRaises(contract.ContractError) as caught:
            execution.launch(self.request(capabilities=["qualified_isolation"]), self.repo,
                             execution.ScriptedBackend())
        self.assertEqual("BINDING_MISMATCH", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
