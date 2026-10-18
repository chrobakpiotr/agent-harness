"""Showcase-side bindings of the agent-harness cutover wrapper (AH5-06). Replaces the moved-module authority
tests that patched Showcase names: the library tests the behaviour, this tests what Showcase passes in."""
import importlib
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

HARNESS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

import harness
import verification_sandbox
from verification import authority, model, store

from agent_harness import sandbox as library_sandbox
from agent_harness.verification import authority as library_authority
from agent_harness.verification import model as library_model
from agent_harness.verification import store as library_store

PROFILES = HARNESS / 'verification-profiles'


ALIASES = ('trust', 'machine_outcomes', 'verification.model', 'verification.serialization', 'verification.profile',
           'verification.fingerprint', 'verification.planner', 'verification.candidate', 'verification.human_grants',
           'verification.workspace', 'verification.supervisor',
           'state_snapshots')


class WrapperBindingTest(unittest.TestCase):
    def test_aliased_modules_are_the_library_modules(self):
        for name in ALIASES:
            with self.subTest(name=name):
                self.assertIs(importlib.import_module(name), importlib.import_module('agent_harness.' + name))
        self.assertIs(model, library_model)

    def test_store_binds_showcase_defaults_and_passes_explicit_values(self):
        import verification_command
        primary = HARNESS.parents[1]
        with mock.patch.object(library_store, 'resolve_control_root',
                               return_value=(primary / '.agent-runs/control/verification-v2', 'id')):
            bound = store.VerificationStore(HARNESS)
        # Anchored to the primary checkout by the library, never taken from this file's location.
        self.assertEqual(HARNESS / 'human-issuer-registry.json', bound.issuer_registry)
        self.assertIs(verification_command.executable_binding_for_command, bound.executable_binder)
        from verification.retry_controls import RETRY_CONTROLS
        self.assertEqual({item.control_id: item for item in RETRY_CONTROLS}, bound.retry_controls)
        self.assertEqual(2, len(bound.retry_controls))
        self.assertEqual(PROFILES, bound.profile_root)
        self.assertIsInstance(bound, library_store.VerificationStore)
        explicit = store.VerificationStore(HARNESS, control_root=HARNESS / 'c', issuer_registry=HARNESS / 'r.json',
                                           profile_root=HARNESS / 'p')
        self.assertEqual((HARNESS / 'c', HARNESS / 'r.json', HARNESS / 'p'),
                         (explicit.root, explicit.issuer_registry, explicit.profile_root))

    def test_authority_passes_arguments_through_and_binds_showcase(self):
        import verification_command
        binder = verification_command.executable_binding_for_command
        calls = {}
        names = ('publish_and_accept', 'resolve_accepted', 'resolve_execution', 'prepare_task_plan',
                 'validate_plan_record')
        for name in names:
            patch = mock.patch.object(library_authority, name,
                                      side_effect=lambda *a, _n=name, **k: calls.setdefault(_n, (a, k)))
            patch.start()
            self.addCleanup(patch.stop)
        record = {'plan_id': 'p'}
        authority.publish_and_accept('repo', 'feature', record, expected_generation=3)
        authority.resolve_accepted('repo', 'plan')
        authority.resolve_execution('repo', 'plan', unit_id='u')
        authority.prepare_task_plan('repo', 'feature', 'T-001', 2, 'base', ['cmd'])
        authority.validate_plan_record(record, repository='repo', reconstruct=True)
        expected = {
            'publish_and_accept': (('repo', 'feature', record),
                                   {'expected_generation': 3, 'lifecycle': harness, 'profile_root': PROFILES}),
            'resolve_accepted': (('repo', 'plan'), {'lifecycle': harness}),
            'resolve_execution': (('repo', 'plan'), {'lifecycle': harness, 'profile_root': PROFILES, 'unit_id': 'u',
                                                     'executable_binder': binder}),
            'prepare_task_plan': (('repo', 'feature', 'T-001', 2, 'base', ['cmd']),
                                  {'lifecycle': harness, 'profile_root': PROFILES, 'profile_id': 'showcase',
                                   'executable_binder': binder}),
            'validate_plan_record': ((record,), {'profile_root': PROFILES, 'repository': 'repo', 'reconstruct': True,
                                                 'executable_binder': binder}),
        }
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(expected[name], calls[name])

    def test_authority_defaults_match_showcase(self):
        calls = {}
        for name in ('resolve_execution', 'validate_plan_record'):
            patch = mock.patch.object(library_authority, name,
                                      side_effect=lambda *a, _n=name, **k: calls.setdefault(_n, k))
            patch.start()
            self.addCleanup(patch.stop)
        authority.resolve_execution('repo', 'plan')
        authority.validate_plan_record({})
        self.assertIsNone(calls['resolve_execution']['unit_id'])
        self.assertEqual((None, False), (calls['validate_plan_record']['repository'],
                                         calls['validate_plan_record']['reconstruct']))

    def test_sandbox_binds_showcase_policy_and_passes_arguments_through(self):
        root, run, home = HARNESS / 'w', HARNESS / 'r', HARNESS / 'h'
        policy = {'environment_policy': verification_sandbox.gradle_environment,
                  'worktree_wrappers': {'./gradlew': 'worktree-gradle-wrapper-invalid'},
                  'authority_roots': ('tooling/agent-harness',)}
        with mock.patch.object(library_sandbox, 'build_plan') as build:
            self.assertIs(build.return_value, verification_sandbox.build_plan(['x'], root, run, 'off', ('/t',)))
        build.assert_called_once_with(['x'], root, run, 'off', ('/t',), **policy)
        with mock.patch.object(library_sandbox, '_safe_env') as env:
            verification_sandbox._safe_env(root, home, ('/t',))
        env.assert_called_once_with(root, home, ('/t',), environment_policy=verification_sandbox.gradle_environment)
        with mock.patch.object(library_sandbox, 'derive_protected_authority_roots') as roots:
            verification_sandbox.derive_protected_authority_roots(root)
        roots.assert_called_once_with(root, ('tooling/agent-harness',))
        with mock.patch.object(library_sandbox, '_bubblewrap') as bwrap:
            verification_sandbox._bubblewrap(['x'], root, home, {'GRADLE_OPTS': 'o', 'HOME': 'h'}, ('/t',), ('b',))
        bwrap.assert_called_once_with(['x'], root, home, {'GRADLE_OPTS': 'o', 'HOME': 'h'}, ('/t',), ('b',),
                                      authority_roots=('tooling/agent-harness',), policy_keys=('GRADLE_OPTS',))
        with mock.patch.object(library_sandbox, '_macos_sandbox_exec') as macos:
            verification_sandbox._macos_sandbox_exec(['x'], root, home, {}, ('/t',), ('b',))
        macos.assert_called_once_with(['x'], root, home, {}, ('/t',), ('b',), authority_roots=('tooling/agent-harness',))
        with mock.patch.dict('os.environ', {'AGENTIC_SDD_GRADLE_RO_DEP_CACHE': ''}):
            self.assertEqual({'GRADLE_USER_HOME': str(home / 'gradle'), 'GRADLE_OPTS': '-Dorg.gradle.daemon=false'},
                             verification_sandbox.gradle_environment({}, home))
        for name in ('PosixProcessGroupBackend', 'ExecutionIdentity', 'qualifies_v2', 'resolve_executable',
                     '_sha256_file', 'ExecutableResolutionError', 'doctor'):
            with self.subTest(name=name):
                self.assertTrue(hasattr(verification_sandbox, name))

    def test_command_backend_binds_showcase_policy(self):
        import verification_command

        from agent_harness.verification import command as library_command
        backend = verification_command.CommandExecutionBackend()
        self.assertIsInstance(backend, library_command.CommandExecutionBackend)
        self.assertIs(verification_sandbox, backend.sandbox)
        self.assertEqual(frozenset(verification_command.PROFILE_SCRIPTS.values()), backend.blocked_executables)
        with mock.patch.object(verification_command, 'verification_argv', return_value=['x']) as argv:
            self.assertEqual(['x'], backend.argv_policy('python3 -V', script_context=None, worktree=None, cwd=None))
        argv.assert_called_once_with('python3 -V', script_context=None, worktree=None, cwd=None)
        with mock.patch.object(library_command, 'run_command', return_value='r') as run:
            self.assertEqual('r', verification_command.run_command('c', cwd='d', run_dir='e', timeout_seconds=1))
        kwargs = run.call_args.kwargs
        self.assertIsInstance(kwargs['backend_factory'](), verification_command.CommandExecutionBackend)
        with mock.patch.object(verification_command, 'verification_argv', return_value=['y']):
            self.assertEqual(['y'], kwargs['argv_policy']('c'))
        with mock.patch.object(store, 'VerificationStore') as bound_store:
            kwargs['store_factory']('root')
        bound_store.assert_called_once_with('root')

    def test_executor_binds_lifecycle_resolver_store_and_backend(self):
        from verification import executor

        from agent_harness.verification import executor as library_executor
        with mock.patch.object(library_executor, 'execute_plan', return_value='done') as execute:
            self.assertEqual('done', executor.execute_plan('repo', 'profile', 'plan', sandbox_mode='off'))
        args, kwargs = execute.call_args
        self.assertEqual(('repo', 'profile', 'plan'), args)
        self.assertIsNone(kwargs['store'])  # built by the library after its authority checks
        with mock.patch.object(executor, 'VerificationStore', return_value='bound-store') as bound:
            self.assertEqual('bound-store', kwargs['store_factory']('root'))
        bound.assert_called_once_with('root')
        self.assertIs(harness, kwargs['lifecycle'])
        self.assertIs(authority.resolve_execution, kwargs['resolve_execution'])
        self.assertEqual('off', kwargs['sandbox_mode'])
        with mock.patch.object(executor, 'CommandExecutionBackend', return_value='patched-backend'):
            self.assertEqual('patched-backend', kwargs['backend_factory']())

    def test_verify_cli_binds_profiles_store_resolver_and_executor(self):
        import verify

        from agent_harness import verify_cli
        with mock.patch.object(verify_cli, 'main', return_value=0) as cli:
            self.assertEqual(0, verify.main(['plan']))
        kwargs = cli.call_args.kwargs
        self.assertEqual(['plan'], cli.call_args.args[0])
        self.assertEqual(PROFILES, kwargs['profile_root'])
        with mock.patch.object(store, 'VerificationStore', return_value='bound') as bound:
            self.assertEqual('bound', kwargs['store_factory']('repo'))
        bound.assert_called_once_with('repo')
        with mock.patch.object(authority, 'resolve_execution', return_value='resolved'):
            self.assertEqual('resolved', kwargs['resolve_execution']('repo', 'plan', unit_id=None))
        from verification import executor
        with mock.patch.object(executor, 'execute_plan', return_value='executed'):
            self.assertEqual('executed', kwargs['execute_plan']('repo', 'profile', 'plan'))

    def test_trust_cli_runs_the_library_cli(self):
        out = subprocess.run([sys.executable, str(HARNESS / 'trust.py'), 'classify', '.agent-runs/x.log'],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn('untrusted', out)


if __name__ == '__main__':
    unittest.main()
