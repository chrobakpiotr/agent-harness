"""Ported from Showcase `tooling/agent-harness/tests/test_verification_sandbox.py` at `49ab97f` (AH5-08a).

The resolver tests are in `test_executables.py`. Tests of Showcase policy (the Gradle cache, the
`tooling/agent-harness` root) are replaced by seam tests; see docs/migration/provenance.md.
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from agent_harness import sandbox as vs


class VerificationSandboxTest(unittest.TestCase):
    def test_backend_discovery_uses_the_shared_resolver(self):
        executable_path = str(pathlib.Path(sys.executable).resolve())
        with mock.patch.object(vs.platform, 'system', return_value='Linux'), \
             mock.patch.object(vs.shutil, 'which', side_effect=AssertionError('unbound lookup')), \
             mock.patch.object(vs, 'resolve_executable', side_effect=lambda name, **_kwargs: {
                 'command': name, 'path': executable_path, 'sha256': 'a' * 64}) as resolve:
            candidates = vs.discover_backends()
        self.assertEqual({'codex-sandbox', 'bubblewrap', 'docker-container'},
                         {item.kind for item in candidates})
        self.assertEqual(['codex', 'bwrap', 'sandbox-exec', 'docker'],
                         [call.args[0] for call in resolve.call_args_list])
        self.assertTrue(all(item.state == 'DISCOVERED' and item.qualification is None
                            for item in candidates))

    def test_control_store_read_probe_fails_when_any_process_depth_can_read(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp).resolve()
            control = root / '.agent-runs' / 'control' / 'verification-v2'
            control.mkdir(parents=True)
            worktree = root / 'worktree'
            worktree.mkdir()
            candidate = vs.BackendCandidate('codex-sandbox', 'test', '/usr/bin/codex', 'Darwin')

            def uncontained_probe(_candidate, code, args, *, cwd, **_kwargs):
                result = subprocess.run([sys.executable, '-c', code, *args], cwd=cwd,
                                        capture_output=True, text=True, check=False)
                return result.returncode, result.stdout.strip(), result.stderr.strip()

            for check_id in ('Q02', 'Q03', 'Q04'):
                with self.subTest(check_id=check_id), \
                     mock.patch.object(vs, '_run_probe', side_effect=uncontained_probe):
                    outcome = vs._active_control_read_probe(candidate, check_id,
                        (str(worktree),), (str(root / '.agent-runs'),), worktree)
                self.assertEqual('FAIL', outcome[0])
                self.assertEqual('CONTROL_READ_ALLOWED', outcome[1])
                self.assertIn('trusted-control-read-probe', outcome[2])

    def test_legacy_strong_isolation_is_not_v2_qualification(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(vs, '_codex_helper', return_value=vs.SandboxPlan(
                 ['sandbox','python3','-V'], {}, 'claimed', True, {'network':'disabled'})), \
             mock.patch.object(vs, '_bubblewrap', return_value=None), \
             mock.patch.object(vs, '_macos_sandbox_exec', return_value=None):
            root = pathlib.Path(tmp)
            plan = vs.build_plan(['python3', '-V'], root, root / 'run', 'auto')
            self.assertFalse(vs.qualifies_v2(plan))

    def test_unsupported_backend_fails_closed_for_every_mode(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(vs, '_codex_helper', return_value=None), \
             mock.patch.object(vs, '_bubblewrap', return_value=None), \
             mock.patch.object(vs, '_macos_sandbox_exec', return_value=None):
            root = pathlib.Path(tmp)
            for mode in ('auto', 'off', 'required'):
                if mode == 'required':
                    with self.assertRaises(RuntimeError):
                        vs.build_plan(['python3', '-V'], root, root / mode, mode)
                else:
                    plan = vs.build_plan(['python3', '-V'], root, root / mode, mode)
                    self.assertFalse(vs.qualifies_v2(plan))
    def test_off_is_explicit_and_not_strong(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            plan = vs.build_plan(['python3', '-V'], root, root / 'run', 'off')
            self.assertEqual('off', plan.backend)
            self.assertFalse(plan.strong_isolation)

    def test_required_fails_when_no_backend_exists(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(vs, '_codex_helper', return_value=None), \
             mock.patch.object(vs, '_bubblewrap', return_value=None), \
             mock.patch.object(vs, '_macos_sandbox_exec', return_value=None):
            root = pathlib.Path(tmp)
            with self.assertRaises(RuntimeError):
                vs.build_plan(['python3', '-V'], root, root / 'run', 'required')

    def test_auto_degrades_explicitly_when_platform_has_no_strong_backend(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(vs, '_codex_helper', return_value=None), \
             mock.patch.object(vs, '_bubblewrap', return_value=None), \
             mock.patch.object(vs, '_macos_sandbox_exec', return_value=None):
            root = pathlib.Path(tmp)
            plan = vs.build_plan(['python3', '-V'], root, root / 'run', 'auto')
            self.assertEqual('allowlist-only', plan.backend)
            self.assertFalse(plan.strong_isolation)

    def test_environment_policy_adds_consumer_keys_and_cannot_replace_the_isolated_base(self):
        """[seam] A consumer adds its tool variables; bubblewrap forwards them; the base stays library-owned."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve()
            policy = lambda env, home: {'TOOL_HOME': str(home / 'tool')}
            env = vs._safe_env(root, root / 'home', environment_policy=policy)
            self.assertEqual(str(root / 'home' / 'tool'), env['TOOL_HOME'])
            self.assertNotIn('TOOL_HOME', vs._safe_env(root, root / 'home'))
            for bad in ({'PATH': '/tmp'}, {'HOME': '/tmp'}, {'JAVA_HOME': '/usr'}, {'A=B': 'x'}, {'X': 1},
                        ['TOOL_HOME'], {'': 'x'}, {'a\0': 'x'}, {'X': 'a\0b'}, {'LANG': 'C'},
                        {'LD_PRELOAD': '/x.so'}, {'DYLD_INSERT_LIBRARIES': '/x'}, {'PYTHONPATH': '/x'},
                        {'BASH_ENV': '/x'}, {'NODE_OPTIONS': '-r x'}, {'JAVA_TOOL_OPTIONS': '-x'},
                        {'GIT_DIR': '/x'}, {'git_config_global': '/x'}, {'Path': '/tmp'}, {'java_home': '/usr'}):
                with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'sandbox-environment-policy-invalid'):
                    vs._safe_env(root, root / 'home', environment_policy=lambda env, home, bad=bad: bad)
            (root / '.agent-runs' / 'control' / 'verification-v2').mkdir(parents=True)
            with mock.patch.object(vs, 'resolve_executable', return_value={
                     'command': 'x', 'path': '/usr/bin/true', 'sha256': '0' * 64}), \
                 mock.patch.object(vs, 'derive_protected_authority_roots', return_value=(root / '.agent-runs',)), \
                 mock.patch.object(vs.platform, 'system', return_value='Linux'):
                plan = vs._bubblewrap(['true'], root, root / 'home', env, policy_keys=('TOOL_HOME',))
            index = plan.argv.index('TOOL_HOME')
            self.assertEqual(['--setenv', 'TOOL_HOME', env['TOOL_HOME']], plan.argv[index - 1:index + 2])
            # build_plan derives the forwarded keys from the policy itself.
            with mock.patch.object(vs, '_codex_helper', return_value=None), \
                 mock.patch.object(vs, '_bubblewrap', return_value='bwrap-plan') as bwrap:
                self.assertEqual('bwrap-plan', vs.build_plan(['true'], root, root / 'run', 'auto',
                                                             environment_policy=policy))
            self.assertEqual(('TOOL_HOME',), bwrap.call_args.kwargs['policy_keys'])

    def test_worktree_wrapper_is_only_resolved_when_the_consumer_declares_it(self):
        """[seam] `./name` resolves inside the worktree only when declared, with the consumer's error code."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve()
            wrapper = root / 'buildw'
            wrapper.write_text('#!/bin/sh\nexit 0\n')
            wrapper.chmod(0o700)
            gradlew = root / 'gradlew'
            gradlew.write_text('#!/bin/sh\nexit 0\n')
            gradlew.chmod(0o700)
            for name in ('./buildw', './gradlew'):  # no wrapper is built in
                with self.subTest(name=name), self.assertRaises(vs.ExecutableResolutionError):
                    vs.build_plan([name, 'test'], root, root / 'run', 'off')
            wrappers = {'./buildw': 'worktree-buildw-invalid'}
            plan = vs.build_plan(['./buildw', 'test'], root, root / 'run', 'off', worktree_wrappers=wrappers)
            self.assertEqual([str(wrapper), 'test'], plan.argv)
            self.assertEqual((), plan.executable_bindings)
            wrapper.chmod(0o600)
            with self.assertRaisesRegex(vs.ExecutableResolutionError, '^worktree-buildw-invalid$'):
                vs.build_plan(['./buildw'], root, root / 'run', 'off', worktree_wrappers=wrappers)
            for bad in ({'buildw': 'x'}, {'./a/b': 'x'}, {'./../x': 'x'}, {'./buildw': ''}):
                with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'sandbox-worktree-wrappers-invalid'):
                    vs.build_plan(['./buildw'], root, root / 'run', 'off', worktree_wrappers=bad)

    def test_codex_sandbox_helper_uses_documented_command_shape(self):
        binding = {'command': 'codex', 'path': '/usr/local/bin/codex', 'sha256': '0' * 64}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(vs, 'resolve_executable', return_value=binding), \
             mock.patch.object(vs.platform, 'system', return_value='Darwin'):
            root = pathlib.Path(tmp)
            env = vs._safe_env(root, root / 'home')
            plan = vs._codex_helper(['python3', '-V'], root, env)
            self.assertIsNotNone(plan)
            self.assertEqual(
                ['/usr/local/bin/codex', 'sandbox', 'macos', '--full-auto', 'python3', '-V'],
                plan.argv,
            )


    def test_safe_env_does_not_inherit_provider_or_ci_secrets(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            vs.os.environ,
            {
                'PATH': '/usr/bin:/bin',
                'CI': 'true',
                'GITHUB_TOKEN': 'github-secret',
                'OPENAI_API_KEY': 'openai-secret',
                'ANTHROPIC_API_KEY': 'anthropic-secret',
                'AWS_SECRET_ACCESS_KEY': 'aws-secret',
                'PYTHONPATH': '/attacker/python',
                'PYTHONHOME': '/attacker/python-home',
                'PYTHONSTARTUP': '/attacker/startup.py',
                'PYTHONUSERBASE': '/attacker/userbase',
                'NODE_OPTIONS': '--require /attacker/node.js',
                'LD_PRELOAD': '/attacker/preload.so',
                'LD_LIBRARY_PATH': '/attacker/lib',
                'DYLD_INSERT_LIBRARIES': '/attacker/inject.dylib',
                'BASH_ENV': '/attacker/bashrc',
                'ENV': '/attacker/env',
                'GIT_EXEC_PATH': '/attacker/git',
                'JAVA_TOOL_OPTIONS': '-javaagent:/attacker/agent.jar',
            },
            clear=True,
        ):
            root = pathlib.Path(tmp)
            env = vs._safe_env(root, root / 'home')
            expected_path = os.pathsep.join(str(path) for path in vs.trusted_executable_directories())
            self.assertEqual(expected_path, env['PATH'])
            self.assertEqual('true', env['CI'])
            self.assertNotIn('GITHUB_TOKEN', env)
            self.assertNotIn('OPENAI_API_KEY', env)
            self.assertNotIn('ANTHROPIC_API_KEY', env)
            self.assertNotIn('AWS_SECRET_ACCESS_KEY', env)
            for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONUSERBASE',
                        'NODE_OPTIONS', 'LD_PRELOAD', 'LD_LIBRARY_PATH',
                        'DYLD_INSERT_LIBRARIES', 'BASH_ENV', 'ENV', 'GIT_EXEC_PATH',
                        'JAVA_TOOL_OPTIONS'):
                with self.subTest(key=key):
                    self.assertNotIn(key, env)
            self.assertEqual(str(root / 'home'), env['HOME'])

    def test_safe_env_drops_undeclared_toolchain_selectors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp).resolve()
            with mock.patch.dict(vs.os.environ, {'JAVA_HOME': str(root)}, clear=True):
                env = vs._safe_env(root, root / 'home')
            self.assertNotIn('JAVA_HOME', env)

    def test_authority_roots_are_the_sdd_roots_plus_explicit_consumer_roots(self):
        """[seam] Consumer control-plane roots are explicit and relative to the primary checkout."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve()
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            common = root / '.git'
            default = {path.relative_to(root).as_posix() if path != common else '.git'
                       for path in vs.derive_protected_authority_roots(root)}
            self.assertEqual({'.agent-runs', '.agent-state', 'docs/specs', '.git'}, default)
            consumer = vs.derive_protected_authority_roots(root, ('tooling/agent-harness',))
            self.assertIn(root / 'tooling' / 'agent-harness', consumer)
            for bad in (('/etc',), ('../x',), ('a/../b',), ('',), 'tooling'):
                with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'sandbox-authority-roots-invalid'):
                    vs.derive_protected_authority_roots(root, bad)

    def test_bubblewrap_rebinds_trusted_issuer_registry_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            registry = root / 'tooling/agent-harness/human-issuer-registry.json'
            registry.parent.mkdir(parents=True)
            registry.write_text('{"schema_version":1,"issuers":[]}')
            (root / '.agent-runs/control/verification-v2').mkdir(parents=True)
            with mock.patch.object(vs, 'derive_protected_authority_roots', return_value=(registry,)), \
                 mock.patch.object(vs, 'resolve_executable', return_value={
                     'command': 'bwrap', 'path': '/usr/bin/bwrap', 'sha256': '0' * 64}), \
                 mock.patch.object(vs.platform, 'system', return_value='Linux'):
                plan = vs._bubblewrap(['python3', '-c', 'pass'], root, root / 'home', {})
            self.assertIsNotNone(plan)
            index = plan.argv.index('--ro-bind', plan.argv.index('--ro-bind') + 1)
            self.assertEqual([str(registry), str(registry)], plan.argv[index + 1:index + 3])

    def test_macos_policy_denies_reads_from_verification_control_store(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp).resolve()
            protected = root / '.agent-runs'
            control = protected / 'control' / 'verification-v2'
            control.mkdir(parents=True)
            profile = vs.macos_sandbox_profile((root / 'worktree',), (protected,))
            self.assertIn(f'(deny file-read* (subpath "{control}"))', profile)
            self.assertIn(f'(deny file-write* (subpath "{control}"))', profile)

    def test_bubblewrap_hides_verification_control_store_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp).resolve()
            control = root / '.agent-runs' / 'control' / 'verification-v2'
            control.mkdir(parents=True)
            secret = control / 'grants.json'
            secret.write_text('trusted grant')
            with mock.patch.object(vs, 'derive_protected_authority_roots', return_value=(root / '.agent-runs',)), \
                 mock.patch.object(vs, 'resolve_executable', return_value={
                     'command': 'bwrap', 'path': '/usr/bin/bwrap', 'sha256': '0' * 64}), \
                 mock.patch.object(vs.platform, 'system', return_value='Linux'):
                plan = vs._bubblewrap(['python3', '-c', 'pass'], root / 'worktree', root / 'home', {})
            self.assertIsNotNone(plan)
            index = plan.argv.index('--tmpfs')
            self.assertEqual(str(control), plan.argv[index + 1])


if __name__ == '__main__':
    unittest.main()
