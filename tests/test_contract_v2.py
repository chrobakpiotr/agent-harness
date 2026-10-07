"""Contract v2 (ADR 0005): per-launch target evidence and resource limits, enforced in both directions."""

import copy
import json
import tempfile
import unittest
from importlib import resources
from pathlib import Path

from agent_harness import contract, execution

FIXTURE = json.loads((resources.files("agent_harness") / "contract_fixtures" / "v2-limit-exceeded.json").read_text())


def code_of(test, func, *args):
    with test.assertRaises(contract.ContractError) as caught:
        func(*args)
    return caught.exception.code


class ContractV2Test(unittest.TestCase):
    def setUp(self):
        self.request = copy.deepcopy(FIXTURE["request"])
        self.result = copy.deepcopy(FIXTURE["result"])

    def bad(self, result=None, request=None):
        request = request or self.request
        result = result or self.result
        result["request_digest"] = contract.request_digest(request)
        return code_of(self, contract.validate_result, result, request)

    def test_fixture_validates_and_v1_documents_stay_v1(self):
        contract.validate_result(self.result, self.request)
        v1 = json.loads((resources.files("agent_harness") / "contract_fixtures" / "success.json").read_text())
        contract.validate_result(v1["result"], v1["request"])
        self.assertEqual("MALFORMED", code_of(self, contract.validate_request, {**v1["request"], "limits": None}))
        failed = copy.deepcopy(v1["result"])
        failed.update(outcome="error", error_code="LIMIT_EXCEEDED", completion=None)
        self.assertEqual("MALFORMED", code_of(self, contract.validate_result, failed, v1["request"]))
        for extra in ({"target": None}, {"limits": None}):  # v2 fields never belong to a v1 result
            with self.subTest(extra=extra):
                self.assertEqual("MALFORMED", code_of(self, contract.validate_result, {**v1["result"], **extra},
                                                      v1["request"]))

    def test_versions_must_match_and_be_known(self):
        v1_answer = {k: v for k, v in self.result.items() if k not in ("target", "limits")}
        self.assertEqual("VERSION_MISMATCH", self.bad({**v1_answer, "contract_version": 1}))
        self.assertEqual("VERSION_MISMATCH", code_of(self, contract.validate_request, {**self.request, "contract_version": 3}))

    def test_limit_exceeded_iff_a_non_timeout_limit_fired(self):
        no_code = copy.deepcopy(self.result)
        no_code["error_code"] = "PROVIDER_ERROR"
        self.assertEqual("MALFORMED", self.bad(no_code))
        code_without_fired = copy.deepcopy(self.result)
        code_without_fired["limits"]["fired"] = None
        self.assertEqual("MALFORMED", self.bad(code_without_fired))
        completed = copy.deepcopy(self.result)
        completed.update(outcome="completed", error_code=None, exit_code=0)
        completed["attempts"][0]["outcome"] = "completed"
        self.assertEqual("MALFORMED", self.bad(completed))  # oom fired but outcome completed

    def test_timeout_fired_iff_outcome_timeout(self):
        timeout = copy.deepcopy(self.result)
        timeout.update(outcome="timeout", error_code=None, drain="unconfirmed")
        timeout["attempts"][0]["outcome"] = "timeout"
        timeout["limits"]["fired"] = "timeout"
        contract.validate_result(timeout, self.request)
        unnamed = copy.deepcopy(timeout)
        unnamed["limits"]["fired"] = None
        self.assertEqual("MALFORMED", self.bad(unnamed))
        named_without_timeout = copy.deepcopy(self.result)
        named_without_timeout.update(error_code=None)
        named_without_timeout["limits"]["fired"] = "timeout"
        self.assertEqual("MALFORMED", self.bad(named_without_timeout))

    def test_applied_limits_equal_the_request(self):
        for key, value in (("memory_bytes", 536870913), ("timeout_seconds", 31)):
            rounded = copy.deepcopy(self.result)
            rounded["limits"]["applied"][key] = value
            with self.subTest(key=key):
                self.assertEqual("MALFORMED", self.bad(rounded))
        unlimited = copy.deepcopy(self.request)
        unlimited["limits"] = None
        self.assertEqual("MALFORMED", self.bad(copy.deepcopy(self.result), unlimited))  # limits only when requested

    def test_qualified_needs_target_and_limits_and_only_qualified_has_a_target(self):
        no_target = copy.deepcopy(self.result)
        no_target["target"] = None
        self.assertEqual("MALFORMED", self.bad(no_target))
        no_limits = copy.deepcopy(self.result)
        no_limits.update(limits=None, error_code="PROVIDER_ERROR")
        self.assertEqual("MALFORMED", self.bad(no_limits))
        request = copy.deepcopy(self.request)
        request["capabilities"] = []
        fake_with_target = copy.deepcopy(self.result)
        fake_with_target["isolation_level"] = "fake"
        self.assertEqual("MALFORMED", self.bad(fake_with_target, request))

    def test_unknown_may_omit_limits_and_rejected_has_neither(self):
        unknown = copy.deepcopy(self.result)
        unknown.update(outcome="unknown", error_code=None, drain="unconfirmed", limits=None)
        unknown["attempts"][0]["outcome"] = "unknown"
        contract.validate_result(unknown, self.request)
        rejected = {**self.result, "outcome": "rejected", "error_code": "NOT_QUALIFIED", "execution_id": None,
                    "drain": None, "started_at": None, "ended_at": None, "attempts": [], "artifacts": [],
                    "limits": None, "target": None}
        contract.validate_result(rejected, self.request)
        self.assertEqual("MALFORMED", self.bad({**rejected, "target": self.result["target"]}))

    def test_each_rule_on_its_own(self):
        """One change per case, so only the rule under test can refuse it."""
        def variant(change, request_change=None):
            result, request = copy.deepcopy(self.result), copy.deepcopy(self.request)
            change(result)
            if request_change:
                request_change(request)
            return result, request

        def unlimited_request(r):
            r["limits"] = None

        def no_limits_qualified(r):  # qualified, no limits requested or stated, plain error
            r.update(limits=None, error_code="PROVIDER_ERROR")

        def fake_without_stated_limits(r):  # limits requested, none stated, not qualified, not unknown
            r.update(limits=None, error_code="PROVIDER_ERROR", target=None, isolation_level="fake")

        def drop_qualified(r):
            r["capabilities"] = []

        cases = {
            "rejected with limits": (lambda r: r.update(
                outcome="rejected", error_code="NOT_QUALIFIED", execution_id=None, drain=None, started_at=None,
                ended_at=None, attempts=[], artifacts=[], target=None,
                limits={**r["limits"], "fired": None}), None),
            "qualified states limits": (no_limits_qualified, lambda q: (unlimited_request(q),
                                                                        q.update(capabilities=[]))),
            "limits when requested": (fake_without_stated_limits, drop_qualified),
            "output_truncated bool": (lambda r: r["limits"].update(output_truncated=1), None),
            "target extra field": (lambda r: r["target"].update(region="x"), None),
            "target id": (lambda r: r["target"].update(id="bad id"), None),
            "target image digest": (lambda r: r["target"].update(image_digest="latest"), None),
            "target qualification digest": (lambda r: r["target"].update(qualification_digest="nope"), None),
            "fired value": (lambda r: (r.update(outcome="completed", error_code=None, exit_code=0),
                                       r["attempts"][0].update(outcome="completed"),
                                       r["limits"].update(fired="bogus")), None),
            "limits extra field": (lambda r: r["limits"].update(note="x"), None),
            "applied cpus True": (lambda r: r["limits"]["applied"].update(cpus=True), None),
            "applied float": (lambda r: r["limits"]["applied"].update(memory_bytes=536870912.0), None),
            "applied timeout float": (lambda r: r["limits"]["applied"].update(timeout_seconds=30.0), None),
            "applied extra key": (lambda r: r["limits"]["applied"].update(gpus=1), None),
            "limits without requested limits": (lambda r: r.update(isolation_level="fake", target=None),
                                                lambda q: (unlimited_request(q), q.update(capabilities=[]))),
            "timeout-only limits without requested limits": (
                lambda r: r.update(isolation_level="fake", target=None, error_code="PROVIDER_ERROR",
                                   limits={"applied": {"timeout_seconds": 30}, "fired": None,
                                           "output_truncated": False}),
                lambda q: (unlimited_request(q), q.update(capabilities=[]))),
            "versions not a map": (lambda r: r.update(versions=["backend"]), None),
            "versions key": (lambda r: r.update(versions={"bad key": "x"}), None),
        }
        for name, (change, request_change) in cases.items():
            result, request = variant(change, request_change)
            with self.subTest(rule=name):
                self.assertEqual("MALFORMED", self.bad(result, request))

    def test_a_rejected_result_was_never_launched(self):
        rejected = {**self.result, "outcome": "rejected", "error_code": "NOT_QUALIFIED", "execution_id": None,
                    "drain": None, "started_at": None, "ended_at": None, "attempts": [], "artifacts": [],
                    "limits": None, "target": None, "candidate": None, "usage_events": []}
        contract.validate_result(rejected, self.request)
        traces = {"execution_id": "exec-1", "started_at": "2026-10-07T10:00:00Z", "ended_at": "2026-10-07T10:00:00Z",
                  "drain": "confirmed", "candidate": {"path": "c", "sha256": "sha256:" + "a" * 64, "size": 1},
                  "attempts": self.result["attempts"], "artifacts": self.result["artifacts"],
                  "usage_events": [{"contract_version": 2, "event_id": "u", "attempt_id": "att-1", "source": "harness",
                                    "kind": "summary", "cache_semantics": "separate",
                                    "units": dict.fromkeys(("input_tokens", "output_tokens", "cache_read_tokens",
                                                            "cache_write_tokens"), 0)}]}
        for key, value in traces.items():
            with self.subTest(trace=key):
                self.assertEqual("MALFORMED", self.bad({**rejected, key: value}))
        finished = copy.deepcopy(self.result)  # nothing but an unknown outcome value is wrong here
        finished.update(outcome="finished", error_code=None)
        finished["limits"]["fired"] = None
        self.assertEqual("MALFORMED", self.bad(finished))

    def test_usage_events_carry_the_result_version(self):
        result = copy.deepcopy(self.result)
        result["usage_events"] = [{"contract_version": 1, "event_id": "u-1", "attempt_id": "att-1",
                                   "source": "harness", "kind": "summary", "cache_semantics": "separate",
                                   "units": {"input_tokens": 1, "output_tokens": 1, "cache_read_tokens": 0,
                                             "cache_write_tokens": 0}}]
        result["usage_completeness"] = "complete"
        self.assertEqual("VERSION_MISMATCH", self.bad(result))

    def test_request_limits_are_positive_exact_integers(self):
        for change in ({"cpus": 0}, {"memory_bytes": -1}, {"pids": True}, {"gpus": 1}):
            request = copy.deepcopy(self.request)
            request["limits"].update(change)
            with self.subTest(change=change):
                self.assertEqual("MALFORMED", code_of(self, contract.validate_request, request))
        qualified_without_limits = {**self.request, "limits": None}
        self.assertEqual("MALFORMED", code_of(self, contract.validate_request, qualified_without_limits))


class OfflineBackendsV2Test(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        (root / "workspace").mkdir()
        (root / "evidence").mkdir()
        self.roots = {"workspace": str(root / "workspace"), "evidence_root": str(root / "evidence")}
        self.request = {**copy.deepcopy(FIXTURE["request"]), "capabilities": []}

    def run_scripted(self, request, backend):
        result = execution.launch(request, backend, **self.roots).result(timeout=10)
        contract.validate_result(result, request)
        return result

    def test_process_backend_rejects_limits_without_side_effects(self):
        marker = Path(self.roots["workspace"]) / "ran"
        result = execution.launch(self.request, execution.ProcessBackend(["touch", str(marker)]), **self.roots)
        result = result.result(timeout=10)
        contract.validate_result(result, self.request)
        self.assertEqual(("rejected", "CAPABILITY_UNSUPPORTED"), (result["outcome"], result["error_code"]))
        self.assertFalse(marker.exists())
        self.assertEqual([], list(Path(self.roots["evidence_root"]).iterdir()))

    def test_scripted_backend_scripts_each_fired_limit(self):
        cases = {"oom": ("error", "LIMIT_EXCEEDED"), "pids": ("error", "LIMIT_EXCEEDED"),
                 "disk": ("error", "LIMIT_EXCEEDED"), "timeout": ("timeout", None), None: ("completed", None)}
        for i, (fired, (outcome, code)) in enumerate(cases.items()):
            request = {**self.request, "request_id": f"req-{i}"}
            backend = execution.ScriptedBackend([{"outcome": outcome}], fired=fired, output_truncated=fired is None)
            with self.subTest(fired=fired):
                result = self.run_scripted(request, backend)
                self.assertEqual((outcome, code, fired), (result["outcome"], result["error_code"],
                                                          result["limits"]["fired"]))
                self.assertEqual({**request["limits"], "timeout_seconds": 30}, result["limits"]["applied"])
                self.assertIsNone(result["target"])
        self.assertTrue(self.run_scripted({**self.request, "request_id": "req-t"},
                                          execution.ScriptedBackend(output_truncated=True))["limits"]["output_truncated"])

    def test_a_backend_can_state_its_qualified_target(self):
        target = {"id": "example-not-a-real-target", "qualification_digest": "sha256:" + "f" * 64,
                  "image_digest": "sha256:" + "1" * 64}

        class Qualified(execution.ScriptedBackend):
            isolation_level = "qualified"
            capabilities = ("cancel", "usage", "qualified_isolation")

            def run(self, *args):
                return {**super().run(*args), "target": target}
        qualified = Qualified.__new__(Qualified)
        execution.ScriptedBackend.__init__(qualified, [{"outcome": "error"}], fired="oom")
        qualified.target = target
        request = {**self.request, "request_id": "req-q", "capabilities": ["qualified_isolation"]}
        result = self.run_scripted(request, qualified)
        self.assertEqual(("error", "LIMIT_EXCEEDED", target), (result["outcome"], result["error_code"], result["target"]))
        broken = Qualified.__new__(Qualified)
        execution.ScriptedBackend.__init__(broken, [{"outcome": "completed"}], fired="oom")  # inconsistent script
        broken.target = target
        fallback = self.run_scripted({**request, "request_id": "req-q2"}, broken)
        self.assertEqual(("unknown", target), (fallback["outcome"], fallback["target"]))  # still a valid result
        nameless = Qualified.__new__(Qualified)
        execution.ScriptedBackend.__init__(nameless)
        for i, bad_target in enumerate((None, {}, {**target, "qualification_digest": "nope"})):
            nameless.target = bad_target
            request_id = f"req-q3-{i}"
            with self.subTest(target=bad_target), self.assertRaises(contract.ContractError):
                execution.launch({**request, "request_id": request_id}, nameless, **self.roots)
            self.assertFalse(any(p.name.startswith(execution._slot(request_id))  # refused before anything is written
                                 for p in Path(self.roots["evidence_root"]).rglob("*")))

    def test_v2_without_limits_and_inconsistent_scripts(self):
        unlimited = {**self.request, "request_id": "req-u", "limits": None}
        result = self.run_scripted(unlimited, execution.ScriptedBackend())
        self.assertEqual((2, "completed", None, None),
                         (result["contract_version"], result["outcome"], result["limits"], result["target"]))
        broken = self.run_scripted({**self.request, "request_id": "req-b"},
                                   execution.ScriptedBackend([{"outcome": "completed"}], fired="oom"))
        self.assertEqual("unknown", broken["outcome"])  # an inconsistent script never becomes a success
        with self.assertRaises(ValueError):
            execution.ScriptedBackend(fired="output")


if __name__ == "__main__":
    unittest.main()
