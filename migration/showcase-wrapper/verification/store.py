"""Showcase wrapper over `agent_harness.verification.store` (AH5-06, AH5-08b).

Binds the Showcase issuer registry path, profile root, executable binder and registered retry controls, which
the library takes as explicit parameters. The retry registry names stay importable from here.
"""
import pathlib

from agent_harness.verification import store as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith('__')})
from verification.retry_controls import (  # noqa: F401  (Showcase's registry, formerly defined here)
    _NO_RETRY_COMMAND,
    _REGISTERED_GRADLE_BUILD_INPUT_SHA256,
    _RETRY_CONTROL_BINDINGS,
    _RETRY_CONTROL_INPUT_SHA256,
    _RETRY_CONTROL_WRAPPER_SHA256,
    RETRY_CONTROLS,
    _retry_control_inputs_digest,
)

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
            executable_binder=executable_binding_for_command, retry_controls=RETRY_CONTROLS)
