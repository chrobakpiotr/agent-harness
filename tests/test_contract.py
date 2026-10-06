"""Execution contract v1: golden fixtures from the installed wheel plus malformed inputs."""

import copy
import json
import unittest
from importlib import resources

from agent_harness import contract

FIXTURES = resources.files("agent_harness") / "contract_fixtures"
NAMES = ("success", "fail", "timeout", "unknown-terminal", "missing-qualification")


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def assert_code(test, code, fn, *args):
    with test.assertRaises(contract.ContractError) as caught:
        fn(*args)
    test.assertEqual(caught.exception.code, code, caught.exception.where)


class GoldenFixturesTest(unittest.TestCase):
    def test_fixture_set_is_shipped_and_valid(self):
        shipped = sorted(p.name for p in FIXTURES.iterdir() if p.name.endswith(".json"))
        self.assertEqual(shipped, sorted([*(f"{n}.json" for n in NAMES), "qualification-example.json"]))
        example = json.loads((FIXTURES / "qualification-example.json").read_text(encoding="utf-8"))
        contract.validate_qualification_report(example)
        # A format example of a fictitious target: its review fails and its evidence is not shipped.
        self.assertEqual(("example-not-a-real-target", "fail"),
                         (example["target"], example["independent_review"]["verdict"]))
        for name in NAMES:
            with self.subTest(name):
                doc = fixture(name)
                contract.validate_result(doc["result"], doc["request"])
                if "capability_report" in doc:
                    contract.validate_capability_report(doc["capability_report"])

    def test_repo_context_is_explicit_and_absolute(self):
        repo = {"contract_version": 1, "repo_id": "showcase", "base_sha": "5" * 40, "workspace": "/w",
                "authority_root": "/w/.git", "evidence_root": "/w/.agent-runs"}
        contract.validate_repo_context(repo)
        assert_code(self, "MALFORMED", contract.validate_repo_context, {**repo, "workspace": "w"})
        assert_code(self, "UNSAFE_PATH", contract.validate_repo_context, {**repo, "evidence_root": "/w/../x"})



class MalformedTest(unittest.TestCase):
    def mutate(self, name, change):
        doc = copy.deepcopy(fixture(name))
        change(doc["request"], doc["result"])
        return doc["result"], doc["request"]

    def rejects(self, code, name, change):
        assert_code(self, code, contract.validate_result, *self.mutate(name, change))

    def test_version_mismatch(self):
        self.rejects("VERSION_MISMATCH", "success", lambda q, r: r.update(contract_version=2))
        self.rejects("VERSION_MISMATCH", "success", lambda q, r: q.update(contract_version="1"))
        self.rejects("VERSION_MISMATCH", "success", lambda q, r: r["usage_events"][0].update(contract_version=0))

    def test_binding_mismatch(self):
        self.rejects("BINDING_MISMATCH", "success", lambda q, r: q.update(model="other-model"))
        self.rejects("BINDING_MISMATCH", "success", lambda q, r: r.update(request_id="req-other"))
        self.rejects("BINDING_MISMATCH", "success", lambda q, r: q["capabilities"].append("qualified_isolation"))
        # Correctly re-bound request that requires qualified isolation; result ran under fake isolation.
        self.rejects("BINDING_MISMATCH", "success", lambda q, r: q["capabilities"].append("qualified_isolation")
                     or r.update(request_digest=contract.request_digest(q)))

    def test_no_free_text_fields(self):
        self.rejects("MALFORMED", "success", lambda q, r: r.update(stderr="AWS_SECRET=..."))
        self.rejects("MALFORMED", "success", lambda q, r: r["versions"].update(cli="token with spaces"))
        self.rejects("MALFORMED", "success", lambda q, r: r["attempts"][0].update(env={}))

    def test_unsafe_artifact_paths(self):
        for bad in ("../x", "/etc/passwd", "a//b", "a\\b", "./a", ""):
            with self.subTest(bad):
                self.rejects("UNSAFE_PATH", "success", lambda q, r, bad=bad: r["candidate"].update(path=bad))

    def test_three_states_stay_separate(self):
        self.rejects("MALFORMED", "timeout", lambda q, r: r.update(completion="accepted"))
        self.rejects("MALFORMED", "unknown-terminal", lambda q, r: r.update(drain="confirmed"))
        self.rejects("MALFORMED", "success", lambda q, r: r.update(error_code="INTERNAL_ERROR"))
        self.rejects("MALFORMED", "missing-qualification", lambda q, r: r.update(execution_id="exec-1"))

    def test_usage_rules(self):
        self.rejects("MALFORMED", "timeout", lambda q, r: r.update(usage_completeness="complete"))
        self.rejects("MALFORMED", "success", lambda q, r: r["usage_events"][0].update(kind="summary"))
        self.rejects("MALFORMED", "success", lambda q, r: r["usage_events"][1].update(attempt_id="att-9"))
        self.rejects("MALFORMED", "success",
                     lambda q, r: r["usage_events"][1]["units"].update(input_tokens=-1))
        # complete = every attempt has a summary with integer units; null always means unknown.
        self.rejects("MALFORMED", "success",
                     lambda q, r: r["usage_events"][1]["units"].update(cache_read_tokens=None))
        self.rejects("MALFORMED", "success", lambda q, r: r["usage_events"].pop(1))

    def test_attempt_limits_and_times(self):
        self.rejects("MALFORMED", "fail", lambda q, r: q.update(max_attempts=1) or r.update(request_digest=contract.request_digest(q)))
        self.rejects("MALFORMED", "success", lambda q, r: r.update(ended_at="2026-10-05T09:00:00Z"))
        self.rejects("MALFORMED", "success", lambda q, r: r.update(started_at="2026-10-05 10:00:00"))

    def test_capability_report_chain(self):
        report = fixture("missing-qualification")["capability_report"]
        for change in ({"launch_ready": True}, {"qualified": True}, {"refusal": None}, {"supported": True, "discovered": False}):
            with self.subTest(change):
                assert_code(self, "MALFORMED", contract.validate_capability_report, {**report, **change})


if __name__ == "__main__":
    unittest.main()
