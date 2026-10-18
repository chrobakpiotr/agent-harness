"""Showcase wrapper over `agent_harness.verification.command` (AH5-08b), plus Showcase's command policy.

The allowlist (`verification_argv`), the profile scripts and their accepted-plan check, and
`executable_binding_for_command` are Showcase policy, verbatim from `49ab97f`. The library backend and
`run_command` receive them, Showcase's `verification_sandbox` and Showcase's store late-bound, so names patched
here are the ones used. See docs/migration/provenance.md.
"""
from __future__ import annotations

import os
import pathlib
import shlex
import stat

import verification_sandbox

from agent_harness.verification import command as _impl
from agent_harness.verification.command import SHELL_META, CommandRejected

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith('__')})


PROFILE_SCRIPTS = {
    'domain-mutation-threshold': './tooling/scripts/verify-domain-pitest.sh',
    'critical-postgres-regression': './tooling/scripts/verify-critical-postgres-tests.sh',
    'critical-rabbitmq-regression': './tooling/scripts/verify-critical-rabbitmq-tests.sh',
}


def _accepted_script_argv(argv, context, worktree, cwd):
    """Parser permission only; these references never authorize physical launch."""
    from verification.store import StoreError
    if (not isinstance(context, dict) or
            set(context) != {'plan_id', 'obligation_id', 'unit_id'} or
            any(not isinstance(value, str) or not value for value in context.values()) or
            worktree is None or cwd is None or len(argv) != 1):
        raise CommandRejected('script-command-context-required')
    try:
        root = pathlib.Path(worktree).resolve(strict=True)
        if pathlib.Path(cwd).resolve(strict=True) != root:
            raise CommandRejected('script-command-cwd-mismatch')
        from verification.authority import resolve_accepted
        record = resolve_accepted(root, context['plan_id'])
        obligation = next((item for item in record['obligations']
                           if item['obligation_id'] == context['obligation_id']), None)
        unit = next((item for item in record['execution_units']
                     if item['unit_id'] == context['unit_id']), None)
        if (obligation is None or unit is None or
                unit['obligation_ids'] != [context['obligation_id']] or
                unit['required_origin'] != obligation['required_origin'] or
                unit['independent_execution_class'] != obligation['independent_execution_class'] or
                obligation['cwd'] != '.' or obligation['command'] != argv[0] or
                PROFILE_SCRIPTS.get(obligation['profile_gate_id']) != argv[0]):
            raise CommandRejected('script-command-binding-mismatch')
        relative = pathlib.PurePosixPath(argv[0][2:])
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise CommandRejected('script-command-symlink')
        if (not stat.S_ISREG(current.stat().st_mode) or
                current.resolve(strict=True) != root / relative):
            raise CommandRejected('script-command-path-invalid')
    except CommandRejected:
        raise
    except (OSError, ValueError, KeyError, TypeError, SystemExit, StoreError):
        raise CommandRejected('script-command-authority-unavailable') from None
    return argv


def verification_argv(command: str, *, script_context=None, worktree=None, cwd=None) -> list[str]:
    if not isinstance(command, str) or not command.strip():
        raise CommandRejected('invalid-command')
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        raise CommandRejected('invalid-command') from None
    if (not argv or any(token in SHELL_META or '$(' in token or '`' in token or '${' in token
                        for token in argv)):
        raise CommandRejected('unsupported-command-composition')
    executable = argv[0]
    if executable in PROFILE_SCRIPTS.values():
        return _accepted_script_argv(argv, script_context, worktree, cwd)
    allowed = executable in {'./gradlew', 'python3', 'python', 'npm', 'npx', 'true'}
    if executable == 'docker':
        allowed = len(argv) >= 2 and (argv[1] == 'build' or argv[1:3] == ['compose', 'config'])
    elif executable == 'helm':
        allowed = len(argv) >= 2 and argv[1] in {'lint', 'template'}
    elif executable == 'terraform':
        allowed = len(argv) >= 2 and argv[1] in {'fmt', 'validate'}
    elif executable == 'git':
        allowed = len(argv) >= 2 and argv[1] in {'diff', 'status'}
    if not allowed:
        raise CommandRejected('command-not-allowlisted')
    return argv


def executable_binding_for_command(command: str, *, worktree, cwd, toolchain_directories=()):
    """Resolve the command's executable at trusted planning/admission time."""
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        raise CommandRejected('invalid-command') from None
    if not argv:
        raise CommandRejected('invalid-command')
    if argv[0] in PROFILE_SCRIPTS.values():
        try:
            script = (pathlib.Path(worktree).resolve(strict=True) / argv[0][2:]).resolve(strict=True)
            root = pathlib.Path(worktree).resolve(strict=True)
            if (root not in script.parents or not script.is_file() or not os.access(script, os.X_OK) or
                    script.is_symlink()):
                raise CommandRejected('script-command-path-invalid')
            interpreter = verification_sandbox.resolve_executable(
                'bash', toolchain_directories=toolchain_directories, worktree=worktree)
            return {'command': argv[0], 'path': interpreter['path'], 'sha256': interpreter['sha256']}
        except CommandRejected:
            raise
        except (OSError, RuntimeError, ValueError):
            raise CommandRejected('script-command-authority-unavailable') from None
    if argv[0] == './gradlew':
        root = pathlib.Path(worktree).resolve(strict=True)
        directory = pathlib.Path(cwd).resolve(strict=True)
        wrapper = directory / 'gradlew'
        try:
            if root not in wrapper.resolve(strict=True).parents or wrapper.is_symlink():
                raise CommandRejected('worktree-gradle-wrapper-invalid')
            info = wrapper.lstat()
            if not stat.S_ISREG(info.st_mode) or not os.access(wrapper, os.X_OK):
                raise CommandRejected('worktree-gradle-wrapper-invalid')
            return {'command': argv[0], 'path': str(wrapper.resolve(strict=True)),
                    'sha256': verification_sandbox._sha256_file(wrapper)}
        except (OSError, RuntimeError, ValueError):
            raise CommandRejected('worktree-gradle-wrapper-invalid') from None
    try:
        return verification_sandbox.resolve_executable(
            argv[0], toolchain_directories=toolchain_directories, worktree=worktree)
    except verification_sandbox.ExecutableResolutionError as exc:
        raise CommandRejected(str(exc)) from None


class CommandExecutionBackend(_impl.CommandExecutionBackend):
    """Phase-B qualified command facade consumed by the durable supervisor."""

    def __init__(self):
        super().__init__(argv_policy=lambda *args, **kwargs: verification_argv(*args, **kwargs),
                         blocked_executables=PROFILE_SCRIPTS.values(), sandbox=verification_sandbox)


def _store(root):
    from verification.store import VerificationStore
    return VerificationStore(root)


def run_command(command, *, cwd, run_dir, timeout_seconds, sandbox_mode='auto', environment=None,
                required_capabilities=True):
    return _impl.run_command(command, cwd=cwd, run_dir=run_dir, timeout_seconds=timeout_seconds,
                             sandbox_mode=sandbox_mode, environment=environment,
                             required_capabilities=required_capabilities,
                             argv_policy=lambda *args, **kwargs: verification_argv(*args, **kwargs),
                             store_factory=_store, backend_factory=lambda: CommandExecutionBackend())
