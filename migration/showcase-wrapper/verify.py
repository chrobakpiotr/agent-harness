#!/usr/bin/env python3
"""Showcase wrapper over `agent_harness.verify_cli` (AH5-11): the thin verification CLI.

Binds Showcase's trusted `verification-profiles/` root and its store, authority resolver and executor shims. They
are looked up when a command runs, so patches of those Showcase modules apply.
"""
import pathlib
import sys

# Isolated Python deliberately omits the script directory. This file is part
# of the trusted control plane, so add only its own canonical directory before
# importing repository-local verification modules.
CONTROL_DIR = pathlib.Path(__file__).resolve().parent
if str(CONTROL_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_DIR))

from agent_harness import verify_cli as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith('__')})


def _store(repo):
    from verification.store import VerificationStore
    return VerificationStore(repo)


def _resolve_execution(*args, **kwargs):
    from verification.authority import resolve_execution
    return resolve_execution(*args, **kwargs)


def _execute_plan(*args, **kwargs):
    from verification.executor import execute_plan
    return execute_plan(*args, **kwargs)


def main(argv=None):
    return _impl.main(argv, profile_root=CONTROL_DIR / 'verification-profiles', store_factory=_store,
                      resolve_execution=_resolve_execution, execute_plan=_execute_plan)


if __name__ == '__main__':
    raise SystemExit(main())
