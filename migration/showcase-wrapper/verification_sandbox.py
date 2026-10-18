"""Showcase wrapper over `agent_harness.sandbox` and `agent_harness.executables` (AH5-08a).

Binds Showcase's consumer policy, which the library takes as explicit parameters: the Gradle environment
(`GRADLE_USER_HOME`, no daemon, the optional read-only dependency cache), the `./gradlew` worktree wrapper and
the `tooling/agent-harness` authority root. Verbatim Showcase behaviour; see docs/migration/provenance.md.
"""
import os
import pathlib

from agent_harness import executables as _executables
from agent_harness import sandbox as _impl

globals().update({k: v for k, v in vars(_executables).items() if not k.startswith('__')})
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith('__')})

AUTHORITY_ROOTS = ('tooling/agent-harness',)
WORKTREE_WRAPPERS = {'./gradlew': 'worktree-gradle-wrapper-invalid'}
GRADLE_KEYS = ('GRADLE_USER_HOME', 'GRADLE_OPTS', 'GRADLE_RO_DEP_CACHE')


def gradle_environment(env, sandbox_home):
    extra = {
        'GRADLE_USER_HOME': str(sandbox_home / 'gradle'),
        # Prevent Gradle from trying to share a daemon across sandboxed invocations.
        'GRADLE_OPTS': '-Dorg.gradle.daemon=false',
    }
    # Optional explicitly prepared read-only Gradle dependency cache. Do not point this at the
    # live ~/.gradle cache: Gradle's shared-cache contract expects a separately seeded directory.
    ro_cache = os.environ.get('AGENTIC_SDD_GRADLE_RO_DEP_CACHE')
    if ro_cache:
        candidate = pathlib.Path(ro_cache).expanduser().resolve()
        if candidate.is_dir() and (candidate / 'modules-2').is_dir():
            extra['GRADLE_RO_DEP_CACHE'] = str(candidate)
    return extra


def _safe_env(worktree, sandbox_home, toolchain_directories=()):
    return _impl._safe_env(worktree, sandbox_home, toolchain_directories, environment_policy=gradle_environment)


def derive_protected_authority_roots(repository):
    return _impl.derive_protected_authority_roots(repository, AUTHORITY_ROOTS)


def _bubblewrap(argv, worktree, sandbox_home, env, toolchain_directories=(), executable_bindings=()):
    return _impl._bubblewrap(argv, worktree, sandbox_home, env, toolchain_directories, executable_bindings,
                             authority_roots=AUTHORITY_ROOTS,
                             policy_keys=tuple(key for key in GRADLE_KEYS if key in env))


def _macos_sandbox_exec(argv, worktree, sandbox_home, env, toolchain_directories=(), executable_bindings=()):
    return _impl._macos_sandbox_exec(argv, worktree, sandbox_home, env, toolchain_directories,
                                     executable_bindings, authority_roots=AUTHORITY_ROOTS)


def build_plan(argv, worktree, run_dir, mode='auto', toolchain_directories=()):
    return _impl.build_plan(argv, worktree, run_dir, mode, toolchain_directories,
                            environment_policy=gradle_environment, worktree_wrappers=WORKTREE_WRAPPERS,
                            authority_roots=AUTHORITY_ROOTS)
