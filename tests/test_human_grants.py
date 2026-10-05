"""Ed25519 retry grants: only a registered, enabled issuer's exact signature is trusted."""

import base64
import hashlib
import json
import pathlib
import tempfile
import unittest

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:  # optional extra agent-harness[grants]
    raise unittest.SkipTest("needs cryptography (agent-harness[grants])") from None

from agent_harness.verification.human_grants import HumanGrantError, verify_grant
from agent_harness.verification.serialization import canonical_jcs

SCOPE = {key: "x" for key in (
    "plan_id", "family_id", "candidate_identity", "final_changed_surface_id", "task_id", "gate_id",
    "obligation_id", "unit_id", "retry_slot", "profile_hash", "policy_checkpoint", "origin_binding",
    "fence_fingerprint")}
SCOPE.update(lifecycle_generation=1, task_attempt=1)


class VerifyGrantTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.registry = pathlib.Path(tmp.name) / "issuers.json"
        self.key = Ed25519PrivateKey.generate()
        self.write_registry(enabled=True)

    def write_registry(self, enabled):
        public = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.registry.write_text(json.dumps({"schema_version": 1, "issuers": [{
            "issuer_id": "test-issuer", "public_key_ed25519": base64.b64encode(public).decode(),
            "key_fingerprint": hashlib.sha256(public).hexdigest(), "enabled": enabled}]}))

    def grant(self, **changes):
        envelope = {"schema_version": 1, "protocol": "critical-gate-retry-grant-v1", "grant_id": "g1",
                    "failure_id": "f1", "context": {}, "retry_scope": dict(SCOPE), "terminal_receipt_hash": "h",
                    "issuer_id": "test-issuer", "authorizer_principal": "human:test", "justification": "retry",
                    "issued_at": 1}
        envelope["signature"] = base64.b64encode(self.key.sign(canonical_jcs(envelope))).decode()
        return {**envelope, **changes}

    def code(self, envelope):
        with self.assertRaises(HumanGrantError) as caught:
            verify_grant(envelope, self.registry)
        return str(caught.exception)

    def test_valid_grant_is_accepted(self):
        self.assertEqual("test-issuer", verify_grant(self.grant(), self.registry)["issued_by"])

    def test_untrusted_or_altered_grants_are_refused(self):
        self.assertEqual("FAILURE_GRANT_SIGNATURE_INVALID", self.code(self.grant(justification="other")))
        self.assertEqual("FAILURE_GRANT_ISSUER_UNTRUSTED", self.code(self.grant(issuer_id="someone")))
        self.assertEqual("FAILURE_GRANT_INVALID", self.code(self.grant(retry_scope={**SCOPE, "task_attempt": 0})))
        self.write_registry(enabled=False)
        self.assertEqual("FAILURE_GRANT_ISSUER_UNTRUSTED", self.code(self.grant()))
        self.registry.unlink()
        self.assertEqual("FAILURE_GRANT_ISSUER_UNAVAILABLE", self.code(self.grant()))


if __name__ == "__main__":
    unittest.main()
