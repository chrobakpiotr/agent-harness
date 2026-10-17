"""Trusted executable resolution (`agent_harness.executables`).

The first seven tests are ported verbatim from Showcase at `49ab97f` (`tests/test_verification_sandbox.py` and
`tests/test_verification_t900_resolver_guards.py`); only the module alias changed. The default-binder tests are
library-owned.
"""
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

from agent_harness import executables


class ExecutableResolutionTest(unittest.TestCase):
    def test_shared_executable_resolver_ignores_inherited_path_and_records_digest(self):
        with mock.patch.object(executables, 'SYSTEM_EXECUTABLE_DIRECTORIES', ('/usr/bin',)), \
             mock.patch.dict(executables.os.environ, {'PATH': '/tmp/hostile:/usr/bin'}, clear=True):
            binding = executables.resolve_executable('true')
        self.assertEqual('/usr/bin/true', binding['path'])
        self.assertEqual(64, len(binding['sha256']))
        executables.verify_executable_binding(binding)

    def test_shared_resolver_binds_only_the_exact_active_interpreter(self):
        active = str(pathlib.Path(sys.executable).resolve(strict=True))
        binding = executables.resolve_executable(active, active_interpreter=True)
        self.assertEqual({'command': active, 'path': active,
                          'sha256': executables._sha256_file(pathlib.Path(active))}, binding)
        executables.verify_executable_binding(binding)
        with self.assertRaises(executables.ExecutableResolutionError):
            executables.resolve_executable('python3', active_interpreter=True)

        with tempfile.TemporaryDirectory(dir=pathlib.Path.home()) as temporary:
            root = pathlib.Path(temporary).resolve()
            fake = root / 'python3'
            fake.write_text('#!/bin/sh\nexit 0\n')
            fake.chmod(0o700)
            with self.assertRaises(executables.ExecutableResolutionError):
                executables.resolve_executable(str(fake), worktree=root, active_interpreter=True)
            with self.assertRaises(executables.ExecutableResolutionError):
                executables.resolve_executable(str(fake), active_interpreter=True)

    def test_active_interpreter_inside_worktree_sandbox_home_or_tmpdir_is_refused(self):
        for exclusion in ('worktree', 'sandbox_home', 'tmpdir'):
            with tempfile.TemporaryDirectory(dir=pathlib.Path.home()) as temporary:
                root = pathlib.Path(temporary).resolve()
                executable = root / 'bin' / 'python3'
                executable.parent.mkdir()
                executable.write_text('#!/bin/sh\nexit 0\n')
                executable.chmod(0o700)
                kwargs = {} if exclusion == 'tmpdir' else {exclusion: root}
                environment = {'TMPDIR': str(root)} if exclusion == 'tmpdir' else {}
                with self.subTest(exclusion=exclusion), \
                     mock.patch.object(executables.sys, 'executable', str(executable)), \
                     mock.patch.dict(executables.os.environ, environment, clear=False), \
                     self.assertRaisesRegex(executables.ExecutableResolutionError,
                                            '^executable-path-excluded$'):
                    executables.resolve_executable(str(executable), active_interpreter=True, **kwargs)

    def test_shared_executable_resolver_rejects_hostile_paths_and_detects_swap(self):
        with tempfile.TemporaryDirectory(dir=pathlib.Path.home()) as temporary:
            root = pathlib.Path(temporary).resolve()
            executable = root / 'trusted-tool'
            executable.write_text('#!/bin/sh\nexit 0\n')
            executable.chmod(0o700)
            binding = executables.resolve_executable('trusted-tool', toolchain_directories=(str(root),),
                                            tmpdir='/definitely-not-a-temp-root')
            executable.write_text('#!/bin/sh\nexit 1\n')
            with self.assertRaises(executables.ExecutableResolutionError):
                executables.verify_executable_binding(binding)
            with self.assertRaises(executables.ExecutableResolutionError):
                executables.resolve_executable('./trusted-tool', toolchain_directories=(str(root),),
                                      tmpdir='/definitely-not-a-temp-root')

    def test_shared_executable_resolver_rejects_worktree_and_writable_search_roots(self):
        with tempfile.TemporaryDirectory(dir=pathlib.Path.home()) as temporary:
            root = pathlib.Path(temporary).resolve()
            executable = root / 'fake-python3'
            executable.write_text('#!/bin/sh\nexit 0\n')
            executable.chmod(0o700)
            with self.assertRaises(executables.ExecutableResolutionError):
                executables.resolve_executable('fake-python3', toolchain_directories=(str(root),),
                                      worktree=root, tmpdir='/definitely-not-a-temp-root')
        with self.assertRaises(executables.ExecutableResolutionError):
            executables.resolve_executable('true', toolchain_directories=('/tmp',))

    def test_declared_search_directory_inside_excluded_root_is_refused(self):
        with tempfile.TemporaryDirectory(dir=pathlib.Path.home()) as temporary:
            root = pathlib.Path(temporary).resolve()
            worktree = root / 'worktree'
            search = worktree / 'toolchain'
            search.mkdir(parents=True, mode=0o700)
            search.chmod(0o700)

            with self.assertRaisesRegex(executables.ExecutableResolutionError,
                                        '^executable-search-path-excluded$'):
                executables.trusted_executable_directories(
                    toolchain_directories=(str(search),), worktree=worktree)

    def test_executable_nested_under_excluded_worktree_is_refused(self):
        with tempfile.TemporaryDirectory(dir=pathlib.Path.home()) as temporary:
            root = pathlib.Path(temporary).resolve()
            trusted = root / 'trusted'
            worktree = trusted / 'excluded-worktree'
            worktree.mkdir(parents=True, mode=0o700)
            trusted.chmod(0o700)
            worktree.chmod(0o700)
            executable = worktree / 'candidate'
            executable.write_text('#!/bin/sh\nexit 0\n')
            executable.chmod(0o700)
            (trusted / 'candidate').symlink_to(executable)

            # Isolate resolve_executable's resolved-candidate guard: the first
            # test owns the separate trusted-search-directory overlap guard.
            with mock.patch.object(executables, 'trusted_executable_directories',
                                   return_value=(trusted,)):
                with self.assertRaisesRegex(executables.ExecutableResolutionError,
                                            '^executable-path-excluded$'):
                    executables.resolve_executable('candidate',
                        toolchain_directories=(str(trusted),), worktree=worktree,
                        tmpdir=str(root / 'unrelated-tmp'))

    def test_default_binder_resolves_plain_names_and_refuses_paths(self):
        with tempfile.TemporaryDirectory(dir=pathlib.Path.home()) as temporary:
            root = pathlib.Path(temporary).resolve()
            tool = root / 'approved-tool'
            tool.write_text('#!/bin/sh\nexit 0\n')
            tool.chmod(0o700)
            binding = executables.default_executable_binder('approved-tool --version', worktree=None, cwd=None,
                                                            toolchain_directories=(str(root),))
            self.assertEqual(str(tool), binding['path'])
            executables.verify_executable_binding(binding)
            for command in ('./gradlew test', 'bin/tool', '', '"unterminated'):
                with self.assertRaises(executables.ExecutableResolutionError):
                    executables.default_executable_binder(command, worktree=root, cwd=root)


if __name__ == '__main__':
    unittest.main()
