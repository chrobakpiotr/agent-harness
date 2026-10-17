"""Trusted executable resolution (VC-016): absolute, canonical, digest-bound executables only.

Verbatim from Showcase `tooling/agent-harness/verification_sandbox.py` at `49ab97f` (blob `5eed6339`), lines
31-174; see docs/migration/provenance.md. `default_executable_binder` is library-owned: consumers with their own
command conventions (worktree wrappers such as `./gradlew`, profile scripts) pass their own binder.
"""

import hashlib
import os
import pathlib
import shlex
import stat
import sys

SYSTEM_EXECUTABLE_DIRECTORIES = ('/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin')
TOOLCHAIN_ENVIRONMENT_SELECTORS = ('JAVA_HOME', 'JDK_HOME', 'SDKROOT', 'DEVELOPER_DIR')


class ExecutableResolutionError(ValueError):
    """Executable selection could not be proven to use a trusted immutable identity."""


def _resolved_path(value, *, strict=True):
    path = pathlib.Path(value)
    if not path.is_absolute():
        raise ExecutableResolutionError('executable-path-not-absolute')
    try:
        return path.resolve(strict=strict)
    except (OSError, RuntimeError, ValueError):
        raise ExecutableResolutionError('executable-path-unavailable') from None


def _overlaps(path: pathlib.Path, root: pathlib.Path) -> bool:
    return path == root or path in root.parents or root in path.parents


def _secure_directory(path: pathlib.Path) -> None:
    if not path.is_dir():
        raise ExecutableResolutionError('executable-search-path-not-directory')
    current = path
    while True:
        try:
            info = current.lstat()
        except OSError:
            raise ExecutableResolutionError('executable-search-path-unavailable') from None
        if stat.S_ISLNK(info.st_mode) or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ExecutableResolutionError('executable-search-path-writable')
        if current.parent == current:
            return
        current = current.parent


def trusted_executable_directories(toolchain_directories=(), *, worktree=None,
                                   sandbox_home=None, tmpdir=None) -> tuple[pathlib.Path, ...]:
    if not isinstance(toolchain_directories, (tuple, list)) or any(
            not isinstance(value, str) or not value for value in toolchain_directories):
        raise ExecutableResolutionError('invalid-toolchain-directories')
    excluded = []
    for value in (worktree, sandbox_home, tmpdir if tmpdir is not None else os.environ.get('TMPDIR')):
        if value:
            excluded.append(_resolved_path(value, strict=False))
    result = []
    for value in (*SYSTEM_EXECUTABLE_DIRECTORIES, *toolchain_directories):
        raw = pathlib.Path(value)
        if not raw.is_absolute():
            raise ExecutableResolutionError('executable-search-path-not-absolute')
        try:
            path = raw.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            if value in SYSTEM_EXECUTABLE_DIRECTORIES:
                continue
            raise ExecutableResolutionError('executable-search-path-unavailable') from None
        if value not in SYSTEM_EXECUTABLE_DIRECTORIES and path != raw:
            raise ExecutableResolutionError('executable-search-path-not-canonical')
        _secure_directory(path)
        if any(_overlaps(path, root) for root in excluded):
            raise ExecutableResolutionError('executable-search-path-excluded')
        if path not in result:
            result.append(path)
    if not result:
        raise ExecutableResolutionError('no-trusted-executable-path')
    return tuple(result)


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def resolve_executable(command: str, *, toolchain_directories=(), worktree=None,
                        sandbox_home=None, tmpdir=None,
                        active_interpreter: bool = False) -> dict[str, str]:
    if not isinstance(command, str) or not command or command.strip() != command:
        raise ExecutableResolutionError('invalid-executable-name')
    if active_interpreter:
        raw = pathlib.Path(command)
        if not raw.is_absolute():
            raise ExecutableResolutionError('relative-executable-path-refused')
        try:
            path = raw.resolve(strict=True)
            active_path = pathlib.Path(sys.executable).resolve(strict=True)
            if path != active_path:
                raise ExecutableResolutionError('not-active-control-interpreter')
            excluded = tuple(_resolved_path(value, strict=False) for value in
                (worktree, sandbox_home,
                 tmpdir if tmpdir is not None else os.environ.get('TMPDIR')) if value)
            if (not path.is_file() or not os.access(path, os.X_OK) or
                    any(_overlaps(path, root) for root in excluded)):
                raise ExecutableResolutionError('executable-path-excluded')
            _secure_directory(path.parent)
            canonical = str(path)
            return {'command': canonical, 'path': canonical, 'sha256': _sha256_file(path)}
        except ExecutableResolutionError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise ExecutableResolutionError('trusted-executable-not-found') from None
    directories = trusted_executable_directories(toolchain_directories,
        worktree=worktree, sandbox_home=sandbox_home, tmpdir=tmpdir)
    raw = pathlib.Path(command)
    if raw.is_absolute():
        candidates = (raw,)
    elif '/' in command or '\\' in command or command in {'.', '..'}:
        raise ExecutableResolutionError('relative-executable-path-refused')
    else:
        candidates = tuple(directory / command for directory in directories)
    for candidate in candidates:
        try:
            path = candidate.resolve(strict=True)
            if not path.is_file() or not os.access(path, os.X_OK):
                continue
            if not any(path == directory or directory in path.parents for directory in directories):
                continue
            _secure_directory(path.parent)
            if any(_overlaps(path, root) for root in (
                    _resolved_path(value, strict=False) for value in
                    (worktree, sandbox_home, tmpdir if tmpdir is not None else os.environ.get('TMPDIR'))
                    if value)):
                raise ExecutableResolutionError('executable-path-excluded')
            return {'command': command, 'path': str(path), 'sha256': _sha256_file(path)}
        except ExecutableResolutionError:
            raise
        except (OSError, RuntimeError, ValueError):
            continue
    raise ExecutableResolutionError('trusted-executable-not-found')


def verify_executable_binding(binding: dict[str, str]) -> None:
    if (not isinstance(binding, dict) or set(binding) != {'command', 'path', 'sha256'} or
            not isinstance(binding['command'], str) or not isinstance(binding['path'], str) or
            not isinstance(binding['sha256'], str) or len(binding['sha256']) != 64):
        raise ExecutableResolutionError('executable-binding-invalid')
    path = _resolved_path(binding['path'], strict=True)
    if (str(path) != binding['path'] or not path.is_file() or not os.access(path, os.X_OK) or
            _sha256_file(path) != binding['sha256']):
        raise ExecutableResolutionError('executable-binding-changed')


def default_executable_binder(command: str, *, worktree, cwd, toolchain_directories=()) -> dict[str, str]:
    """Bind a gate command's executable by name through the trusted search path. Path-like executables
    (`./gradlew`, profile scripts) are consumer policy: refused here, bound by the consumer's own binder."""
    del cwd  # a plain name never resolves relative to the working directory
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        raise ExecutableResolutionError('invalid-command') from None
    if not argv:
        raise ExecutableResolutionError('invalid-command')
    return resolve_executable(argv[0], toolchain_directories=toolchain_directories, worktree=worktree)
