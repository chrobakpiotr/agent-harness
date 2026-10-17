"""Showcase wrapper over `agent_harness.verification.store` (AH5-06).

Binds the Showcase issuer registry path, profile root and executable binder, which the library takes as
explicit parameters.
"""
import pathlib

from agent_harness.verification import store as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith('__')})
HARNESS = pathlib.Path(__file__).resolve().parents[1]
REGISTRY_PATH = 'tooling/agent-harness/human-issuer-registry.json'


class VerificationStore(_impl.VerificationStore):
    def __init__(self, repository, *, control_root=None, issuer_registry=None, profile_root=None):
        from verification_command import executable_binding_for_command
        # Production (no control_root) never takes a registry path from the caller or from this file's location:
        # the library anchors Showcase's registry path to the primary checkout.
        super().__init__(
            repository, control_root=control_root, issuer_registry=issuer_registry,
            issuer_registry_path=REGISTRY_PATH if control_root is None else None,
            profile_root=HARNESS / 'verification-profiles' if profile_root is None else profile_root,
            executable_binder=executable_binding_for_command)
