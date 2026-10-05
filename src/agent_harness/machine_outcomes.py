"""Stable cross-surface control outcome categories and CLI transport codes."""

from __future__ import annotations

CONTROL_EXIT_CODES = {
    'needs-human': 4,
    'verification-blocked': 5,
    'verification-owned': 6,
}

HUMAN_REQUIRED_REASONS = frozenset({
    'CRITICAL_FAILURE_FENCE_ACTIVE', 'FAILURE_GRANT_REQUIRED',
    'FAILURE_GRANT_AUTHORITY_REQUIRED', 'FAILURE_GRANT_ISSUER_UNAVAILABLE',
    'FAILURE_GRANT_ISSUER_UNTRUSTED', 'FAILURE_GRANT_SIGNATURE_INVALID',
})
BLOCKED_REASONS = frozenset({
    'VERIFICATION_ORIGIN_ADMISSION_UNAVAILABLE',
    'VERIFICATION_EXECUTION_PLAN_REQUIRED', 'ACCEPTED_PLAN_UNAVAILABLE',
    'PLAN_BINDING_MISMATCH', 'CANDIDATE_SEALING_SNAPSHOT_UNAVAILABLE',
    'CANDIDATE_SEALING_UNSAFE_OBJECT', 'CANDIDATE_SEALING_SNAPSHOT_RACE',
    'SECRET_BEARING_CANDIDATE_UNSEALABLE',
    'VERIFICATION_SANDBOX_UNAVAILABLE',
    'backend-not-v2-qualified',
})


def exit_code(category: str) -> int | None:
    """Return the stable shell transport code for a control outcome."""
    return CONTROL_EXIT_CODES.get(category)


def is_control_outcome(category: str) -> bool:
    return category in CONTROL_EXIT_CODES


def classify_reason(reason: str) -> str | None:
    """Map stable control errors without conflating them with verifier failure."""
    if reason == 'verification-owned':
        return 'verification-owned'
    if reason in HUMAN_REQUIRED_REASONS:
        return 'needs-human'
    if reason in BLOCKED_REASONS:
        return 'verification-blocked'
    return None
