"""Showcase wrapper over `agent_harness.verification.executor` (AH5-08b).

Binds the Showcase lifecycle (`harness`), the authority resolver bound to Showcase's profiles and executable
binder, Showcase's store and Showcase's command backend (its allowlist and sandbox policy), which the library
takes as explicit parameters. The store and backend are looked up when used (the store after the authority
checks, as in the source), so patches of this module's `VerificationStore` and `CommandExecutionBackend` apply.
"""
from agent_harness.verification import executor as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith('__')})
from verification.store import VerificationStore
from verification_command import CommandExecutionBackend


def execute_plan(repository, profile, plan, *, store=None, **kwargs):
    import harness
    from verification import authority
    kwargs.setdefault('lifecycle', harness)
    kwargs.setdefault('resolve_execution', authority.resolve_execution)
    kwargs.setdefault('backend_factory', lambda: CommandExecutionBackend())
    kwargs.setdefault('store_factory', lambda root: VerificationStore(root))
    return _impl.execute_plan(repository, profile, plan, store=store, **kwargs)
