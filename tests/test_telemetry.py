"""Ported from Showcase tooling/agent-harness/tests/test_telemetry.py @ 50c18f9 (provenance part).

Manual-evidence tests move with verification.store; the two-consumer test is new (AH5-03a).
"""

import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from agent_harness import telemetry


def provenance(root, feature, task, run, **doc):
    path = root / ".agent-runs" / feature / task / run / "x" / "provenance.json"
    telemetry.atomic_write_json(path, {"orchestration_id": run, "task": task, **doc})
    return path


class TelemetryTest(unittest.TestCase):
    def test_codex_jsonl_usage_is_extracted_without_cost_guessing(self):
        stream = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "abc"}),
            json.dumps({"type": "turn.completed",
                        "usage": {"input_tokens": 10, "cached_input_tokens": 4, "output_tokens": 2}}),
        ])
        data = telemetry.parse_codex_jsonl(stream)
        self.assertEqual("abc", data["thread_id"])
        self.assertEqual(10, data["usage"]["input_tokens"])
        self.assertIsNone(data["cost_usd"])

    def test_provider_parsers_drop_nested_unrecognized_metadata_and_secret_ids(self):
        canary = 'telemetry-canary-secret-91d3'
        stream = '\n'.join([
            json.dumps({'type': 'thread.started', 'thread_id': canary}),
            json.dumps({'type': 'turn.completed', 'usage': {
                'input_tokens': 10, canary: canary, 'provider_extension': {'value': canary},
            }}),
        ])
        with mock.patch.dict(__import__('os').environ, {'TELEMETRY_TEST_SECRET': canary}):
            codex = telemetry.parse_codex_jsonl(stream)
            claude = telemetry.parse_claude_envelope({
                'session_id': canary, 'usage': {'output_tokens': 3, canary: canary},
                'provider_extension': {'secret': canary}, 'total_cost_usd': None,
            })

        self.assertNotIn(canary, json.dumps({'codex': codex, 'claude': claude}))
        self.assertIsNone(codex['thread_id'])
        self.assertEqual({'input_tokens': 10}, codex['usage'])
        self.assertIsNone(claude['session_id'])
        self.assertEqual({'output_tokens': 3}, claude['usage'])
        self.assertIsNone(claude['cost_usd'])

    def test_claude_cost_metadata_is_preserved(self):
        data = telemetry.parse_claude_envelope({
            "session_id": "s1", "total_cost_usd": 0.123, "duration_ms": 55, "num_turns": 3,
            "usage": {"input_tokens": 12},
        })
        self.assertEqual(0.123, data["cost_usd"])
        self.assertEqual(3, data["num_turns"])

    def test_claude_parser_drops_unrepresentably_large_duration_integers(self):
        data = telemetry.parse_claude_envelope({
            'duration_ms': 10**1000,
            'duration_api_ms': 10**1000,
        })

        self.assertIsNone(data['duration_ms'])
        self.assertIsNone(data['duration_api_ms'])

    def test_summary_can_filter_orchestration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for idx, run in enumerate(("r1", "r2")):
                provenance(root, "F-1", f"T-{idx}", run, provider="claude", status="pass", duration_ms=10,
                           provider_metadata={"cost_usd": 0.1, "usage": {"input_tokens": 5}})
            summary = telemetry.summarize(root, "F-1", "r1")
            self.assertEqual(1, summary["runs"])
            self.assertEqual(0.1, summary["known_cost_usd"])
            self.assertEqual(5, summary["tokens"]["input_tokens"])

    def test_summary_never_renders_nested_provider_metadata_or_unknown_cost(self):
        canary = 'telemetry-canary-secret-91d3'
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            path = root / '.agent-runs' / 'F-1' / 'T-1' / 'run' / 'x' / 'provenance.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                'schema_version': 2, 'invocation_id': 'safe-invocation',
                'provider': 'codex', 'status': 'pass', 'orchestration_id': 'run',
                'duration_ms': 10, 'provider_metadata': {
                    'cost_usd': None,
                    'usage': {'input_tokens': 5, canary: canary},
                    'diagnostic': {'nested': canary},
                },
            }))

            summary = telemetry.summarize(root, 'F-1')
            encoded = json.dumps(summary, sort_keys=True)

            self.assertNotIn(canary, encoded)
            self.assertEqual(1, summary['runs'])
            self.assertEqual(0, summary['known_cost_runs'])
            self.assertEqual(1, summary['unknown_cost_runs'])
            self.assertIsNone(summary['known_cost_usd'])
            self.assertEqual(5, summary['tokens']['input_tokens'])
            self.assertEqual(1, summary['known_usage_runs'])
            self.assertEqual(0, summary['unknown_usage_runs'])

    def test_summary_does_not_treat_invalid_cost_or_token_shapes_as_known(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            path = root / '.agent-runs' / 'F-1' / 'T-1' / 'run' / 'x' / 'provenance.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                'provider': 'codex', 'status': 'pass',
                'provider_metadata': {
                    'cost_usd': float('nan'),
                    'usage': {'input_tokens': 4, 'api_key': 'not-a-token-count'},
                },
            }))

            summary = telemetry.summarize(root, 'F-1')

            self.assertEqual(0, summary['known_cost_runs'])
            self.assertEqual(1, summary['unknown_cost_runs'])
            self.assertIsNone(summary['known_cost_usd'])
            self.assertEqual({'input_tokens': 4}, summary['tokens'])

    def test_summary_marks_missing_usage_and_cost_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            path = root / '.agent-runs' / 'F-1' / 'T-1' / 'run' / 'x' / 'provenance.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'provider': 'codex', 'status': 'pass'}))

            summary = telemetry.summarize(root, 'F-1')

            self.assertEqual(1, summary['unknown_cost_runs'])
            self.assertEqual(1, summary['unknown_usage_runs'])
            self.assertEqual(0, summary['known_usage_runs'])
            self.assertEqual({}, summary['tokens'])
            self.assertNotIn('saved_cost_usd', summary)
            self.assertNotIn('saved_time_ms', summary)

    def test_reconcile_running_marks_only_owned_orphans_abandoned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            a = provenance(root, "X", "T-1", "run-a", status="running")
            b = provenance(root, "X", "T-2", "run-a", status="running")
            c = provenance(root, "X", "T-1", "run-b", status="running")
            changed = telemetry.reconcile_running(root, "X", "run-a", {"T-1"}, reason="test")
            self.assertEqual([str(a)], changed)
            self.assertEqual("abandoned", json.loads(a.read_text())["status"])
            self.assertEqual("running", json.loads(b.read_text())["status"])
            self.assertEqual("running", json.loads(c.read_text())["status"])

    def test_two_consumer_repositories_do_not_mix_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_a, repo_b = pathlib.Path(tmp, "a"), pathlib.Path(tmp, "b")
            for repo in (repo_a, repo_b):
                subprocess.run(["git", "init", "-q", str(repo)], check=True)
            a = provenance(repo_a, "F", "T-1", "run", provider="codex", status="running")
            provenance(repo_b, "F", "T-1", "run", provider="claude", status="running")
            self.assertEqual({"codex": 1}, telemetry.summarize(repo_a, "F")["providers"])
            self.assertEqual({"claude": 1}, telemetry.summarize(repo_b, "F")["providers"])
            self.assertEqual([str(a)], telemetry.reconcile_running(repo_a, "F", "run"))
            self.assertEqual({"running": 1}, telemetry.summarize(repo_b, "F")["statuses"])


if __name__ == "__main__":
    unittest.main()
