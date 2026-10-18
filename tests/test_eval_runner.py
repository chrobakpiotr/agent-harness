"""The eval engine (AH5-10): suites and the repository come from the consumer, never from this package's location."""
import contextlib
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from agent_harness import eval_runner


def suite(name='demo', cases=None):
    return {'schema_version': 1, 'name': name, 'cases': cases or [
        {'id': 'ok', 'command': [sys.executable, '-c', 'print("hello")'], 'stdout_contains': ['hello']}]}


class EvalRunnerTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = pathlib.Path(temp.name).resolve() / 'repo'
        (self.repo / 'tools' / 'evals').mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        self.suites = self.repo / 'tools' / 'evals'
        (self.suites / 'demo.json').write_text(json.dumps(suite()), encoding='utf-8')

    def test_suites_are_validated_and_destructive_commands_refused(self):
        self.assertEqual([], eval_runner.validate_suite(suite()))
        for fragment in ('git push origin main', 'terraform apply', 'kubectl delete pod x'):
            with self.subTest(fragment=fragment):
                errors = eval_runner.validate_suite(suite(cases=[{'id': 'x', 'command': fragment.split()}]))
                self.assertIn('cases[0].command contains forbidden remote/destructive mutation', errors)
        self.assertIn('cases[1].id must be unique non-empty', eval_runner.validate_suite(suite(cases=[
            {'id': 'a', 'command': ['true']}, {'id': 'a', 'command': ['true']}])))

    def test_suites_and_run_root_come_from_the_consumer(self):
        """[seam] A suite name resolves in the consumer's suites dir; results land in the anchor's repository."""
        path, _doc = eval_runner.load_suite('demo', self.suites)
        self.assertEqual(self.suites / 'demo.json', path)
        with self.assertRaises(SystemExit):
            eval_runner.load_suite('demo', self.repo / 'elsewhere')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            eval_runner.main(['run', '--suite', 'demo', '--run-id', 'r1'], suites_dir=self.suites,
                             anchor=self.suites)
        result = json.loads((self.repo / '.agent-runs' / 'evals' / 'demo' / 'r1' / 'result.json').read_text())
        self.assertEqual(1.0, result['summary']['pass_rate'])
        self.assertIn('PASS ok run=1', out.getvalue())
        listing = io.StringIO()
        with contextlib.redirect_stdout(listing):
            eval_runner.main(['list'], suites_dir=self.suites, anchor=self.suites)
        self.assertEqual('demo\n', listing.getvalue())

    def test_failing_case_fails_the_run(self):
        (self.suites / 'bad.json').write_text(json.dumps(suite('bad', [
            {'id': 'no', 'command': [sys.executable, '-c', 'raise SystemExit(3)']}])), encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as raised:
            eval_runner.main(['run', '--suite', 'bad', '--run-id', 'r2'], suites_dir=self.suites, anchor=self.suites)
        self.assertEqual(1, raised.exception.code)


if __name__ == '__main__':
    unittest.main()
