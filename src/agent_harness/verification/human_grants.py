"""Verification for externally signed, exact-scope human retry grants."""
from __future__ import annotations

import base64
import hashlib
import json
import pathlib

from .serialization import canonical, canonical_jcs


class HumanGrantError(ValueError):
    """Grant provenance or envelope is not trustworthy."""


def verify_grant(envelope: dict, registry_path: pathlib.Path) -> dict:
    """Validate an Ed25519-signed grant using the trusted issuer registry.

    The harness only verifies signatures. It never accepts or handles a
    private signing key and never turns caller-supplied identity labels into
    authentication.
    """
    required = {'schema_version', 'protocol', 'grant_id', 'failure_id', 'context',
                'retry_scope',
                'terminal_receipt_hash', 'issuer_id', 'authorizer_principal',
                'justification', 'issued_at', 'signature'}
    if (not isinstance(envelope, dict) or set(envelope) != required or
            envelope.get('schema_version') != 1 or
            envelope.get('protocol') != 'critical-gate-retry-grant-v1' or
            not isinstance(envelope.get('context'), dict) or
            not isinstance(envelope.get('retry_scope'), dict) or
            not isinstance(envelope.get('issuer_id'), str) or not envelope['issuer_id'] or
            not isinstance(envelope.get('authorizer_principal'), str) or not envelope['authorizer_principal'] or
            not isinstance(envelope.get('justification'), str) or not envelope['justification'].strip() or
            type(envelope.get('issued_at')) is not int or
            not isinstance(envelope.get('signature'), str)):
        raise HumanGrantError('FAILURE_GRANT_INVALID')
    scope_fields = {'plan_id', 'family_id', 'candidate_identity', 'final_changed_surface_id',
        'lifecycle_generation', 'task_id', 'task_attempt', 'gate_id', 'obligation_id',
        'unit_id', 'retry_slot', 'profile_hash', 'policy_checkpoint', 'origin_binding',
        'fence_fingerprint'}
    scope = envelope['retry_scope']
    if (set(scope) != scope_fields or
            any(not isinstance(scope.get(key), str) or not scope[key]
                for key in scope_fields - {'lifecycle_generation', 'task_attempt'}) or
            type(scope.get('lifecycle_generation')) is not int or scope['lifecycle_generation'] < 1 or
            type(scope.get('task_attempt')) is not int or scope['task_attempt'] < 1):
        raise HumanGrantError('FAILURE_GRANT_INVALID')
    try:
        registry = json.loads(pathlib.Path(registry_path).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        raise HumanGrantError('FAILURE_GRANT_ISSUER_UNAVAILABLE') from None
    if (not isinstance(registry, dict) or registry.get('schema_version') != 1 or
            not isinstance(registry.get('issuers'), list)):
        raise HumanGrantError('FAILURE_GRANT_ISSUER_UNAVAILABLE')
    issuers = [item for item in registry['issuers'] if isinstance(item, dict) and
               item.get('issuer_id') == envelope['issuer_id']]
    if len(issuers) != 1 or issuers[0].get('enabled') is not True:
        raise HumanGrantError('FAILURE_GRANT_ISSUER_UNTRUSTED')
    issuer = issuers[0]
    try:
        key_bytes = base64.b64decode(issuer['public_key_ed25519'], validate=True)
        fingerprint = hashlib.sha256(key_bytes).hexdigest()
        if fingerprint != issuer['key_fingerprint']:
            raise ValueError()
        signature = base64.b64decode(envelope['signature'], validate=True)
        body = {key: value for key, value in envelope.items() if key != 'signature'}
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(signature, canonical_jcs(body))
    except Exception:
        raise HumanGrantError('FAILURE_GRANT_SIGNATURE_INVALID') from None
    return {**envelope, 'grant_hash': hashlib.sha256(canonical(envelope)).hexdigest(),
            'issued_by': envelope['issuer_id'], 'reason': envelope['justification']}
