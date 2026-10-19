"""AH5-12b: runner.py launches through agent_harness.execution (library-owned; runs in a Showcase checkout)."""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

from agent_harness import execution

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / 'runner.py'
spec = importlib.util.spec_from_file_location('sdd_runner_launch', MODULE_PATH)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

CODEX_RESULT = {'status': 'pass', 'summary': 'ok'}
FAKE_CODEX = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys, time
    argv = sys.argv[1:]
    if argv[:1] == ["sandbox"]:
        os.execvp(argv[argv.index("--") + 1], argv[argv.index("--") + 1:])
    if argv[:1] == ["--version"]:
        print("codex-cli 0.160.0"); sys.exit(0)
    prompt = sys.stdin.read()
    mode = os.environ.get("FAKE_MODE", "ok")
    if mode == "sleep":
        time.sleep(60)
    print(json.dumps({"type": "turn.completed",
                      "usage": {"input_tokens": 3, "cached_input_tokens": 0, "output_tokens": 2}}))
    if mode.startswith("secret"):
        print("token sk-ant-" + "a" * 40)
    if mode != "nolast":
        with open(argv[argv.index("--output-last-message") + 1], "w") as f:
            json.dump(RESULT, f)
    with open(os.environ["FAKE_LOG"], "a") as f:
        f.write(json.dumps({"argv": argv, "stdin": prompt}) + "\\n")
    sys.exit(3 if mode == "secretfail" else 0)
''').replace('RESULT', repr(CODEX_RESULT))


def git(root, *args):
    subprocess.run(['git', *args], cwd=root, check=True, capture_output=True)


class RunnerLaunchTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name).resolve()
        self.repo, self.runs, bin_dir = self.root / 'repo', self.root / 'runs', self.root / 'bin'
        for path in (self.repo, self.runs, bin_dir):
            path.mkdir()
        git(self.repo, 'init', '-q')
        git(self.repo, 'config', 'user.email', 't@example.com')
        git(self.repo, 'config', 'user.name', 'T')
        (self.repo / 'a.txt').write_text('a\n')
        git(self.repo, 'add', 'a.txt')
        git(self.repo, 'commit', '-qm', 'base')
        self.worktree = self.root / 'wt'
        git(self.repo, 'worktree', 'add', '-q', str(self.worktree))
        codex = bin_dir / 'codex'
        codex.write_text(FAKE_CODEX)
        codex.chmod(codex.stat().st_mode | stat.S_IXUSR)
        self.log = self.root / 'calls.jsonl'
        self.packet = self.root / 'packet.json'
        self.packet.write_text(json.dumps({'feature': 'F', 'task': 'T-1', 'role': 'builder',
                                           'agent_profile': 'builder'}))
        env = {'PATH': f'{bin_dir}:/usr/bin:/bin', 'HOME': str(self.root), 'FAKE_LOG': str(self.log),
               'OPENAI_API_KEY': 'sk-should-never-reach-the-cli'}
        writable = lambda ws: [pathlib.Path(ws).resolve()]
        for patch in (mock.patch.dict(os.environ, env, clear=True),
                      mock.patch.object(runner, 'RUNS', self.runs),
                      mock.patch.object(runner, 'render_prompt', return_value='fix the bug ✓'),
                      mock.patch.object(runner, 'validate_packet_integrity'),
                      mock.patch.object(runner, 'validate_result'),
                      mock.patch.object(runner, 'enforce_postconditions'),
                      mock.patch.object(runner, 'cli_version', return_value='codex-cli 0.160.0'),
                      mock.patch.object(execution, '_agent_writable_roots', writable),
                      mock.patch.object(execution, '_default_isolation_probe',
                                        return_value=['sh', '-c', f'echo {execution._ISOLATION_MARKER}'])):
            patch.start()
            self.addCleanup(patch.stop)

    def main(self, *extra, mode='ok'):
        argv = ['runner.py', str(self.packet), '--provider', 'codex', '--worktree', str(self.worktree),
                '--skip-verification', *extra]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, 'argv', argv), mock.patch.dict(os.environ, {'FAKE_MODE': mode}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                runner.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def run_dirs(self):
        return [p.parent for p in self.runs.rglob('provenance.json')]

    def provenance(self):
        (run,) = self.run_dirs()
        return run, json.loads((run / 'provenance.json').read_text())

    def test_without_a_model_the_run_is_refused_before_anything_is_written(self):
        """D1: no --model and no configured model: refused, no run dir, no CLI call."""
        code, _, err = self.main()
        self.assertNotEqual(0, code)
        self.assertIn('pass --model', err)
        self.assertEqual([], list(self.runs.iterdir()))
        self.assertFalse(self.log.exists())
        with mock.patch.dict(runner.DEFAULT_MODELS, {'codex': 'gpt-x'}):
            self.assertEqual(0, self.main()[0])
        self.assertEqual(('gpt-x', 'configuration'), tuple(self.provenance()[1][k]
                                                          for k in ('model_requested', 'model_source')))

    def test_preflight_starts_nothing_and_reports_a_refusal(self):
        """Row 5: Showcase can check a run before the lifecycle starts an attempt."""
        code, out, _ = self.main('--model', 'm', '--preflight')
        self.assertEqual((0, 'preflight: ok\n'), (code, out))
        self.assertEqual([], list(self.runs.iterdir()))
        self.assertFalse(self.log.exists())
        with mock.patch.object(runner, 'RUNS', self.worktree / '.agent-runs'):  # evidence the agent could write
            code, _, err = self.main('--model', 'm', '--preflight')
        self.assertNotEqual(0, code)
        self.assertIn('preflight refused: evidence root is writable', err)
        self.assertFalse((self.worktree / '.agent-runs').exists())

    def test_a_run_records_the_library_result_and_reads_the_task_result_from_its_artifacts(self):
        code, out, _ = self.main('--model', 'm', '--reasoning', 'high', '--agent-timeout', '120')
        self.assertEqual(0, code)
        run, provenance = self.provenance()
        self.assertEqual(str(run / 'result.json'), out.strip().splitlines()[-1])
        self.assertEqual('pass', json.loads((run / 'result.json').read_text())['status'])
        record = provenance['agent_execution']
        self.assertEqual(('completed', 0, None), (record['outcome'], record['exit_code'], record['withheld_reason']))
        self.assertEqual(('m', 'argument', 120, 0), (provenance['model_requested'], provenance['model_source'],
                                                     provenance['agent_timeout_seconds'],
                                                     provenance['provider_exit_code']))
        self.assertEqual(3, provenance['provider_metadata']['usage']['input_tokens'])
        (call,) = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual('fix the bug ✓', call['stdin'])  # row 1: the full prompt on stdin, not in argv
        self.assertNotIn('fix the bug ✓', call['argv'])
        for flag in ('approval_policy="never"', 'web_search="disabled"', 'sandbox_workspace_write.network_access=false',
                     'model_reasoning_effort="high"', 'forced_login_method="chatgpt"', '--output-schema'):
            self.assertIn(flag, call['argv'])
        self.assertEqual('workspace-write', call['argv'][call['argv'].index('--sandbox') + 1])
        self.assertEqual(('forced_login_method="chatgpt"', False),
                         (record['requested_login'], record['billing_source_confirmed']))
        self.assertIsNone(record['provider_error'])
        self.assertNotIn('sk-should-never-reach-the-cli', ((run / record['artifacts']['agent-output']).parent / 'launch.json').read_text())
        # D3: stdout/stderr are read from the artifacts, no second copy; an old run's logs read as they are
        self.assertFalse((run / 'stdout.log').exists())
        self.assertIn('turn.completed', runner.run_output(run, 'stdout'))
        legacy = self.root / 'legacy'
        legacy.mkdir()
        (legacy / 'stdout.log').write_text('old')
        self.assertEqual('old', runner.run_output(legacy, 'stdout'))

    def test_a_reviewer_run_is_read_only(self):
        self.main('--model', 'm', '--profile', 'security-reviewer')
        (call,) = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual('read-only', call['argv'][call['argv'].index('--sandbox') + 1])

    def test_withheld_output_keeps_the_process_outcome_and_gives_no_task_result(self):
        """D4/D6: publication is refused, the process facts stay, nothing is parsed from withheld output."""
        code, _, err = self.main('--model', 'm', mode='secret')
        self.assertNotEqual(0, code)
        self.assertIn('output withheld (CREDENTIAL_SHAPED_OUTPUT)', err)
        self.assertNotIn('sk-ant-', err)
        run, provenance = self.provenance()
        self.assertEqual('output-withheld', provenance['status'])
        record = provenance['agent_execution']
        self.assertEqual(('completed', 0, 'CREDENTIAL_SHAPED_OUTPUT'),
                         (record['outcome'], record['exit_code'], record['withheld_reason']))
        self.assertNotIn('agent-output', record['artifacts'])
        self.assertEqual({'usage': None, 'cost_usd': None}, provenance['provider_metadata'])
        self.assertFalse((run / 'result.json').exists())

    def test_a_process_failure_keeps_its_status_even_when_the_output_is_withheld(self):
        """Row 11: a non-zero exit stays provider-error; the withheld reason is recorded beside it."""
        self.assertNotEqual(0, self.main('--model', 'm', mode='secretfail')[0])
        _, provenance = self.provenance()
        self.assertEqual(('provider-error', 3, 'CREDENTIAL_SHAPED_OUTPUT'),
                         (provenance['status'], provenance['agent_execution']['exit_code'],
                          provenance['agent_execution']['withheld_reason']))

    def test_no_last_message_means_no_task_result(self):
        """Row 9: Codex without its last message gives no task result."""
        code, _, err = self.main('--model', 'm', mode='nolast')
        self.assertNotEqual(0, code)
        self.assertIn('codex wrote no last message', err)
        run, _ = self.provenance()
        self.assertFalse((run / 'result.json').exists())

    def test_a_refusal_at_launch_is_recorded_as_preflight_refused(self):
        with mock.patch.object(runner, 'RUNS', self.worktree / '.agent-runs'):  # evidence the agent could write
            code, _, err = self.main('--model', 'm')
            (run,) = [p.parent for p in (self.worktree / '.agent-runs').rglob('provenance.json')]
        self.assertNotEqual(0, code)
        self.assertIn('refused before launch: evidence root is writable', err)
        self.assertEqual('preflight-refused', json.loads((run / 'provenance.json').read_text())['status'])
        self.assertFalse(self.log.exists())

    def test_the_agent_timeout_cancels_the_run(self):
        """D2: --agent-timeout bounds the agent; the outcome is recorded, never a task result."""
        code, _, err = self.main('--model', 'm', '--agent-timeout', '1', mode='sleep')
        self.assertNotEqual(0, code)
        self.assertIn('ended timeout', err)
        _, provenance = self.provenance()
        self.assertEqual(('provider-error', 'timeout', None),
                         (provenance['status'], provenance['agent_execution']['outcome'],
                          provenance['agent_execution']['exit_code']))

    def test_claude_options_keep_the_profile_permissions(self):
        """Rows 2-3: the flags claude_command passed, as validated options; reviewers cannot write."""
        args = mock.Mock(provider='claude', max_turns=7, max_budget_usd=1.5)
        schema = {'$schema': 'x', 'type': 'object'}
        builder = runner.agent_cli_options(args, False, schema)
        reviewer = runner.agent_cli_options(args, True, schema)
        self.assertEqual({'type': 'object'}, builder['output_schema'])
        self.assertEqual(['Read', 'Edit', 'Write', 'Glob', 'Grep', 'Bash'], builder['tools'])
        self.assertEqual(('read-only', ['Read', 'Glob', 'Grep', 'Bash']), (reviewer['sandbox_mode'], reviewer['tools']))
        for options in (builder, reviewer):
            self.assertEqual((runner.DENIED_BASH, runner.CLAUDE_DENY_READ, 7, 1.5, True, 'dontAsk'),
                             tuple(options[k] for k in ('denied_commands', 'deny_read', 'max_turns',
                                                        'max_budget_usd', 'restricted', 'permission_mode')))
            execution.AgentCliBackend('claude', 'x', cli_options=options)  # the library accepts them


if __name__ == '__main__':
    unittest.main()
