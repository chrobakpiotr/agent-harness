"""[seam] The command backend's consumer policy (AH5-08b): allowlist, parse-only executables, sandbox namespace.

Showcase's own command tests (its allowlist, `./gradlew`, profile scripts) test Showcase policy and stay there.
"""
import pathlib
import tempfile
import types
import unittest
from unittest import mock

from agent_harness import sandbox
from agent_harness.verification import command


class CommandPolicyTest(unittest.TestCase):
    def test_parse_refuses_shell_syntax_and_default_policy_allows_nothing(self):
        self.assertEqual(['python3', '-V'], command.parse_argv('python3 -V'))
        for bad in ('', '   ', 'a | b', 'a && b', 'echo $(id)', 'echo `id`', 'echo ${HOME}', 'a > b', "'"):
            with self.subTest(bad=bad), self.assertRaises(command.CommandRejected):
                command.parse_argv(bad)
        with self.assertRaisesRegex(command.CommandRejected, '^command-not-allowlisted$'):
            command.deny_all('python3 -V')
        with self.assertRaisesRegex(command.CommandRejected, '^unsupported-command-composition$'):
            command.deny_all('a ; b')
        backend = command.CommandExecutionBackend()
        with self.assertRaisesRegex(command.CommandRejected, '^command-not-allowlisted$'):
            backend.prepare(worktree='.', cwd='.', run_dir='.', repository_id='r', command='true',
                            sandbox_mode='off')

    def test_backend_uses_the_consumer_allowlist_blocked_executables_and_sandbox(self):
        seen = []

        def allow(cmd, **context):
            seen.append((cmd, sorted(context)))
            return command.parse_argv(cmd)

        plan_calls = []

        def build_plan(argv, worktree, run_dir, mode, **kwargs):
            plan_calls.append((argv, mode, kwargs))
            raise RuntimeError('no strong verification sandbox available; stop here')

        namespace = types.SimpleNamespace(**{name: getattr(sandbox, name) for name in dir(sandbox)
                                             if not name.startswith('__')})
        namespace.build_plan = build_plan
        backend = command.CommandExecutionBackend(argv_policy=allow, blocked_executables={'./parse-only.sh'},
                                                  sandbox=namespace)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            with self.assertRaisesRegex(command.CommandEnvironmentBlocked, '^VERIFICATION_ORIGIN_ADMISSION_UNAVAILABLE$'):
                backend.prepare(worktree=root, cwd=root, run_dir=root, repository_id='r',
                                command='./parse-only.sh', sandbox_mode='required')
            self.assertEqual([], plan_calls)  # refused before any sandbox plan
            with self.assertRaisesRegex(command.CommandEnvironmentBlocked, '^VERIFICATION_SANDBOX_UNAVAILABLE$'):
                backend.prepare(worktree=root, cwd=root, run_dir=root, repository_id='r', command='python3 -V',
                                sandbox_mode='required', toolchain_directories=('/t',))
        self.assertEqual(['./parse-only.sh', 'python3 -V'], [item[0] for item in seen])
        self.assertEqual(['cwd', 'script_context', 'worktree'], seen[0][1])
        self.assertEqual([(['python3', '-V'], 'required', {'toolchain_directories': ('/t',)})], plan_calls)

    def test_run_command_uses_the_consumer_policy_store_and_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve()
            refused = command.run_command('python3 -V', cwd=root, run_dir=root / 'run', timeout_seconds=1)
            self.assertEqual('command-not-allowlisted', refused.error)
            stores, backends = [], []

            def store_factory(path):
                stores.append(path)
                raise RuntimeError('stop after store selection')

            result = command.run_command('python3 -V', cwd=root, run_dir=root / 'run', timeout_seconds=1,
                                         argv_policy=lambda cmd, **_: command.parse_argv(cmd),
                                         store_factory=store_factory,
                                         backend_factory=lambda: backends.append(1))
            self.assertEqual([root], stores)
            self.assertEqual('execution-configuration-error', result.error)
            with mock.patch('agent_harness.verification.supervisor.VerificationSupervisor') as supervisor:
                supervisor.return_value.execute.return_value = ('result', None)
                self.assertEqual('result', command.run_command(
                    'python3 -V', cwd=root, run_dir=root / 'run', timeout_seconds=1,
                    argv_policy=lambda cmd, **_: command.parse_argv(cmd),
                    store_factory=lambda path: 'store', backend_factory=lambda: 'backend'))
            self.assertEqual('backend', supervisor.return_value.execute.call_args.args[0])
            supervisor.assert_called_once_with('store')


if __name__ == '__main__':
    unittest.main()
