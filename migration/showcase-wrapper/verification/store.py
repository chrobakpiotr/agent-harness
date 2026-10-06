"""Showcase wrapper over `agent_harness.verification.store` (AH5-06).

Binds the Showcase issuer registry and profile root, which the library takes as explicit parameters.
"""
import pathlib

from agent_harness.verification import store as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith('__')})
HARNESS = pathlib.Path(__file__).resolve().parents[1]


class VerificationStore(_impl.VerificationStore):
    def __init__(self, repository, *, control_root=None, issuer_registry=None, profile_root=None):
        super().__init__(
            repository, control_root=control_root,
            issuer_registry=HARNESS / 'human-issuer-registry.json' if issuer_registry is None else issuer_registry,
            profile_root=HARNESS / 'verification-profiles' if profile_root is None else profile_root)
