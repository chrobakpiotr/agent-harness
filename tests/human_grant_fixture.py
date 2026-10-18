"""Test-only Ed25519 issuer setup; private keys never enter repository state.

From Showcase `tooling/agent-harness/tests/human_grant_fixture.py` at `49ab97f` (AH5-08b).
"""
import base64
import hashlib
import json
import pathlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_harness.verification.serialization import canonical_jcs
from agent_harness.verification.store import VerificationStore


def trusted_test_store(root):
    root = pathlib.Path(root)
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    registry = root / 'test-issuer-registry.json'
    registry.write_text(json.dumps({'schema_version': 1, 'issuers': [{
        'issuer_id': 'test-issuer', 'public_key_ed25519': base64.b64encode(public).decode('ascii'),
        'key_fingerprint': hashlib.sha256(public).hexdigest(), 'enabled': True,
    }]}), encoding='utf-8')
    return VerificationStore(root, control_root=root / 'control', issuer_registry=registry), key


def signed_test_grant(store, key, grant_id, failure_id, context, reason='isolated-test-authorization',
                      retry_scope=None):
    failure = store.failure_receipt(failure_id)
    retry_scope = retry_scope or {
        'plan_id': 'verification-plan-v1:sha256:test-current-plan',
        'family_id': 'family-2', 'candidate_identity': 'candidate-current',
        'final_changed_surface_id': 'surface-current', 'lifecycle_generation': 2,
        'task_id': 'T-TEST', 'task_attempt': 2, 'gate_id': context['gate_id'],
        'obligation_id': context['gate_id'], 'unit_id': context['gate_id'],
        'retry_slot': 'critical-retry-slot-v1:test', 'profile_hash': context['profile_hash'],
        'policy_checkpoint': 'checkpoint-current', 'origin_binding': 'task-completion',
        'fence_fingerprint': context['fingerprint'],
    }
    envelope = {'schema_version': 1, 'protocol': 'critical-gate-retry-grant-v1',
        'grant_id': grant_id, 'failure_id': failure_id, 'context': context, 'retry_scope': retry_scope,
        'terminal_receipt_hash': failure['terminal_receipt_hash'], 'issuer_id': 'test-issuer',
        'authorizer_principal': 'human:test-principal', 'justification': reason, 'issued_at': 1}
    envelope['signature'] = base64.b64encode(key.sign(canonical_jcs(envelope))).decode('ascii')
    return store.import_failure_grant(envelope)
