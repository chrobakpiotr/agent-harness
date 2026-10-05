"""Ported from Showcase tooling/agent-harness/tests/test_machine_outcomes.py @ 50c18f9.

The third source test exercises verify.py and moves with it.
"""

import unittest

from agent_harness.machine_outcomes import classify_reason, exit_code


class MachineOutcomeTests(unittest.TestCase):
    def test_stable_control_categories_share_cli_codes(self):
        self.assertEqual(
            {"needs-human": 4, "verification-blocked": 5, "verification-owned": 6},
            {c: exit_code(c) for c in ("needs-human", "verification-blocked", "verification-owned")},
        )

    def test_control_reasons_are_not_generic_verifier_failures(self):
        self.assertEqual("verification-owned", classify_reason("verification-owned"))
        self.assertEqual("needs-human", classify_reason("CRITICAL_FAILURE_FENCE_ACTIVE"))
        self.assertEqual("verification-blocked", classify_reason("VERIFICATION_EXECUTION_PLAN_REQUIRED"))
        self.assertIsNone(classify_reason("verifier-assertion-failed"))

    def test_origin_admission_stays_blocked(self):
        self.assertEqual("verification-blocked", classify_reason("VERIFICATION_ORIGIN_ADMISSION_UNAVAILABLE"))
        self.assertEqual("verification-blocked", classify_reason("backend-not-v2-qualified"))


if __name__ == "__main__":
    unittest.main()
