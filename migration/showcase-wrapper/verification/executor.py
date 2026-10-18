"""Showcase wrapper over `agent_harness.verification.executor` (AH5-08b).

Binds the Showcase lifecycle (`harness`), the authority resolver bound to Showcase's profiles and executable
binder, Showcase's store and Showcase's command backend (its allowlist and sandbox policy), which the library
takes as explicit parameters. The backend is looked up when a gate runs, so a patch of this module's
`CommandExecutionBackend` is the one used.
"""
from agent_harness.verification import executor as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith('__')})
from verification_command import CommandExecutionBackend


def execute_plan(repository, profile, plan, *, store=None, **kwargs):
    import harness
    from verification import authority
    from verification.store import VerificationStore
    kwargs.setdefault('lifecycle', harness)
    kwargs.setdefault('resolve_execution', authority.resolve_execution)
    kwargs.setdefault('backend_factory', lambda: CommandExecutionBackend())
    return _impl.execute_plan(repository, profile, plan, store=store or VerificationStore(repository), **kwargs)
