"""Fail-closed structured output boundary (agent_harness.verification.serialization)."""

import unittest

from agent_harness.verification import serialization as s

SAFE = s.SafetyPolicy()


class SafeRecordTest(unittest.TestCase):
    def test_accepts_known_fields(self):
        record = {"gate_id": "unit-tests", "status": "pass", "exit_code": 0, "path": "build/report.xml"}
        self.assertEqual(record, s.safe_record(record, safety=SAFE))

    def test_rejects_unknown_fields_secrets_and_unsafe_paths(self):
        rejected = [
            {"stderr": "anything"},
            {"gate_id": "token=abc123secret"},
            {"path": "../outside"},
            {"path": "config/.env"},
            {"path": ".agent-runs/F/T/run/stdout.log"},
            {"status": "green"},
            {"exit_code": float("nan")},
        ]
        for record in rejected:
            with self.subTest(record), self.assertRaises(ValueError):
                s.safe_record(record, safety=SAFE)

    def test_known_secret_values_are_rejected_and_redacted(self):
        policy = s.SafetyPolicy(known_secrets=("hunter22",))
        with self.assertRaises(ValueError):
            s.safe_record({"gate_id": "x-hunter22"}, safety=policy)
        self.assertEqual("<redacted>", policy.display("x-hunter22"))


class CanonicalJsonTest(unittest.TestCase):
    def test_rfc8785_number_and_key_order(self):
        value = {"b": [333333333.33333329, 1e30, 4.50, 2e-3, 1e-27], "a": [None, True, False]}
        self.assertEqual(
            b'{"a":[null,true,false],"b":[333333333.3333333,1e+30,4.5,0.002,1e-27]}', s.canonical_jcs(value)
        )

    def test_rejects_non_finite(self):
        with self.assertRaises(ValueError):
            s.canonical_jcs(float("inf"))


if __name__ == "__main__":
    unittest.main()
