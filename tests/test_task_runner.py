"""Ported from Showcase `tooling/agent-harness/tests/test_runner.py` at `49ab97f` (AH5-12a): the provider-neutral checks.

`runner` below binds `agent_harness.task_runner` the way Showcase's shim does, with fixture data in place of
Showcase's files: its task-result schema (`fixtures/task-runner`), its tested projection profiles and the
`showcase` verification profile (`fixtures/verification-profiles`). The provider-launch tests move with AH5-12b.
"""
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from agent_harness import sandbox, task_runner
from agent_harness.verification.profile import load_profile

FIXTURES = pathlib.Path(__file__).resolve().parent / 'fixtures'
PROFILES = {  # Showcase's tested projection profiles (runner.py at 49ab97f), as test data
    ('codex', 'codex-cli 0.160.0'): {'drop_keywords': frozenset({'uniqueItems'})},
    ('claude', '2.1.289 (Claude Code)'): {'drop_keywords': frozenset()},
}


def _bound():
    ns = types.SimpleNamespace(
        REPO=FIXTURES / 'task-runner', HERE=FIXTURES, subprocess=subprocess, hashlib=hashlib,
        verification_sandbox=types.SimpleNamespace(
            verify_executable_binding=sandbox.verify_executable_binding, _safe_env=sandbox._safe_env,
            ExecutableResolutionError=sandbox.ExecutableResolutionError),
        **{name: getattr(task_runner, name) for name in (
            'load', 'canonical_json', 'schema_definition_errors', 'canonical_schema_errors', 'packet_hash',
            'validate_packet_integrity', 'git_changed_paths', 'path_allowed', 'worktree_content_fingerprint',
            'git_snapshot', 'enforce_postconditions')})
    ns.TASK_RESULT_SCHEMA_PATH = ns.REPO / 'tooling/agent-harness/schemas/task-result.schema.json'
    ns.project_task_result_schema = lambda schema, provider, version: task_runner.project_task_result_schema(
        schema, provider, version, profiles=PROFILES)
    ns.task_result_projection = lambda raw, provider, version: task_runner.task_result_projection(
        raw, provider, version, profiles=PROFILES)
    ns.validate_result = lambda result, packet=None, *, canonical_schema=None: task_runner.validate_result(
        result, packet, canonical_schema=canonical_schema if canonical_schema is not None
        else task_runner.load(ns.TASK_RESULT_SCHEMA_PATH))

    def run_verification(packet, worktree, out, timeout_seconds, sandbox_mode='auto', accepted_plan_id=None):
        from agent_harness.verification import authority
        return task_runner.run_verification(
            packet, worktree, out, timeout_seconds, sandbox_mode, accepted_plan_id,
            resolve_accepted=lambda *a, **k: authority.resolve_accepted(*a, **k),
            profile_root=ns.HERE / 'verification-profiles', verify_cli=ns.HERE / 'verify.py',
            safe_env=lambda *a: ns.verification_sandbox._safe_env(*a),
            verify_binding=lambda binding: ns.verification_sandbox.verify_executable_binding(binding))
    ns.run_verification = run_verification
    return ns


runner = _bound()


class RunnerTest(unittest.TestCase):
    def test_validate_result_requires_contract(self):
        with self.assertRaises(SystemExit):
            runner.validate_result({'status': 'pass'})
        runner.validate_result({
            'status': 'pass', 'summary': 'ok', 'changed_paths': [], 'commands': [],
            'assumptions': [], 'residual_risks': []
        })

    def test_codex_and_claude_result_schemas_are_projected_per_exact_cli_version(self):
        canonical = runner.load(runner.REPO / 'tooling/agent-harness/schemas/task-result.schema.json')
        codex, codex_dropped = runner.project_task_result_schema(canonical, 'codex', 'codex-cli 0.160.0')
        claude, claude_dropped = runner.project_task_result_schema(canonical, 'claude', '2.1.289 (Claude Code)')

        self.assertFalse(codex['additionalProperties'])
        self.assertEqual(set(codex['properties']), set(codex['required']))
        self.assertFalse(codex['properties']['tdd_evidence']['additionalProperties'])
        self.assertEqual(set(codex['properties']['tdd_evidence']['properties']),
                         set(codex['properties']['tdd_evidence']['required']))
        self.assertNotIn('uniqueItems', codex['properties']['rework_tasks'])
        self.assertEqual(['/properties/rework_tasks/uniqueItems'], codex_dropped)
        self.assertIn('uniqueItems', claude['properties']['rework_tasks'])
        self.assertEqual([], claude_dropped)

        valid = {
            'status': 'pass', 'summary': 'ok', 'changed_paths': [], 'commands': [],
            'assumptions': [], 'residual_risks': [], 'findings': [], 'rework_tasks': [],
            'tdd_evidence': {'red': 'failed', 'green': 'passed', 'refactor': 'passed'},
        }
        self.assertEqual([], runner.canonical_schema_errors(valid, canonical))
        self.assertEqual([], runner.canonical_schema_errors(valid, codex))
        self.assertEqual([], runner.canonical_schema_errors(valid, claude))

    def test_canonical_validation_enforces_dropped_unique_items_constraint(self):
        canonical = runner.load(runner.REPO / 'tooling/agent-harness/schemas/task-result.schema.json')
        projected, dropped = runner.project_task_result_schema(canonical, 'codex', 'codex-cli 0.160.0')
        self.assertIn('/properties/rework_tasks/uniqueItems', dropped)
        invalid = {
            'status': 'fail', 'summary': 'bad duplicate', 'changed_paths': [], 'commands': [],
            'assumptions': [], 'residual_risks': [], 'findings': [],
            'rework_tasks': ['T-001', 'T-001'],
            'tdd_evidence': {'red': 'r', 'green': 'g', 'refactor': 'f'},
        }
        self.assertEqual([], runner.canonical_schema_errors(invalid, projected))
        self.assertTrue(any('uniqueItems' in error for error in runner.canonical_schema_errors(invalid, canonical)))
        with self.assertRaises(SystemExit):
            runner.validate_result(invalid, canonical_schema=canonical)

    def test_canonical_validation_enforces_nested_enum_pattern_and_unknown_fields(self):
        canonical = runner.load(runner.REPO / 'tooling/agent-harness/schemas/task-result.schema.json')
        valid = {
            'status': 'fail', 'summary': 'ok', 'changed_paths': [], 'commands': [],
            'assumptions': [], 'residual_risks': [], 'rework_tasks': ['T-001'],
            'tdd_evidence': {'red': 'r', 'green': 'g', 'refactor': 'f'},
        }
        for mutate in (
            lambda value: value.update(status='unknown'),
            lambda value: value.update(rework_tasks=['bad-id']),
            lambda value: value['tdd_evidence'].update(extra='x'),
        ):
            invalid = json.loads(json.dumps(valid))
            mutate(invalid)
            self.assertTrue(runner.canonical_schema_errors(invalid, canonical))

    def test_unknown_provider_version_or_schema_keyword_fails_closed(self):
        canonical = runner.load(runner.REPO / 'tooling/agent-harness/schemas/task-result.schema.json')
        with self.assertRaises(ValueError):
            runner.project_task_result_schema(canonical, 'codex', 'codex-cli 0.161.0')
        with self.assertRaises(ValueError):
            runner.project_task_result_schema(canonical, 'unknown', '1.0')
        incompatible = json.loads(json.dumps(canonical))
        incompatible['patternProperties'] = {'.*': {'type': 'string'}}
        with self.assertRaises(ValueError):
            runner.project_task_result_schema(incompatible, 'codex', 'codex-cli 0.160.0')
        incompatible = json.loads(json.dumps(canonical))
        incompatible['properties']['findings']['prefixItems'] = [{'type': 'string'}]
        self.assertTrue(runner.canonical_schema_errors({}, incompatible))

    def test_projection_manifest_hashes_exact_provider_schema_bytes(self):
        canonical_bytes = runner.TASK_RESULT_SCHEMA_PATH.read_bytes()
        _, projected_bytes, record = runner.task_result_projection(
            canonical_bytes, 'codex', 'codex-cli 0.160.0')
        self.assertEqual('codex', record['provider'])
        self.assertEqual('codex-cli 0.160.0', record['cli_version'])
        self.assertEqual(runner.hashlib.sha256(canonical_bytes).hexdigest(),
                         record['canonical_schema_sha256'])
        self.assertEqual(runner.hashlib.sha256(projected_bytes).hexdigest(),
                         record['projection_sha256'])
        self.assertEqual(['/properties/rework_tasks/uniqueItems'], record['dropped_constraints'])

    def test_packet_protocol_v3_requires_lease_policy(self):
        packet = {
            'protocol_version': 3, 'context': {}, 'lease_policy': {'ttl_seconds': 1800}
        }
        packet['packet_sha256'] = runner.packet_hash(packet)
        runner.validate_packet_integrity(packet)
        broken = dict(packet)
        broken['lease_policy'] = {}
        broken['packet_sha256'] = runner.packet_hash(broken)
        with self.assertRaises(SystemExit):
            runner.validate_packet_integrity(broken)

    def test_protocol_v4_validates_context_trust(self):
        packet={'protocol_version':4,'context':{'spec':'docs/specs/X/spec.md'},'context_trust':{'spec':'trusted'},'lease_policy':{'ttl_seconds':1800}}
        packet['packet_sha256']=runner.packet_hash(packet)
        runner.validate_packet_integrity(packet)
        packet['context_trust']={'spec':'untrusted'}; packet['packet_sha256']=runner.packet_hash(packet)
        with self.assertRaises(SystemExit): runner.validate_packet_integrity(packet)

    def test_red_green_result_requires_tdd_evidence(self):
        packet={'test_policy':'risk-driven','test_mode':'red-green-refactor'}
        result={'status':'pass','summary':'ok','changed_paths':[],'commands':[],'assumptions':[],'residual_risks':[]}
        with self.assertRaises(SystemExit): runner.validate_result(result,packet)
        result['tdd_evidence']={'red':'test failed before change','green':'same test passed','refactor':'suite passed'}
        runner.validate_result(result,packet)

    def test_read_only_postcondition_rejects_reviewer_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            import subprocess
            subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
            subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=root, check=True)
            subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=root, check=True)
            (root / 'a.txt').write_text('before\n')
            subprocess.run(['git', 'add', 'a.txt'], cwd=root, check=True)
            subprocess.run(['git', 'commit', '-qm', 'base'], cwd=root, check=True)
            before = runner.git_snapshot(root)
            (root / 'a.txt').write_text('after\n')
            result = {
                'status': 'pass', 'summary': 'review', 'changed_paths': ['a.txt'], 'commands': [],
                'assumptions': [], 'residual_risks': []
            }
            with self.assertRaises(SystemExit):
                runner.enforce_postconditions({'allowed_paths': ['**']}, root, before, result, review_existing=True)

    def test_runner_without_lifecycle_accepted_plan_fails_closed_before_payload(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as out_tmp:
            root = pathlib.Path(tmp)
            subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
            subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=root, check=True)
            subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=root, check=True)
            (root / 'a.txt').write_text('base\n', encoding='utf-8')
            subprocess.run(['git', 'add', 'a.txt'], cwd=root, check=True)
            subprocess.run(['git', 'commit', '-qm', 'base'], cwd=root, check=True)

            (root / 'a.txt').write_text('implementation\n', encoding='utf-8')
            packet = {
                'verification': [
                    "python3 -c \"from pathlib import Path; Path('a.txt').write_text('tampered\\\\n')\""
                ]
            }
            # Runtime verification evidence lives outside the task worktree in real runs.
            out = pathlib.Path(out_tmp)
            before_paths = runner.git_changed_paths(root)
            ok, results = runner.run_verification(packet, root, out, 30, sandbox_mode='off')

            self.assertFalse(ok)
            self.assertEqual('verification-blocked', results[-1]['machine_category'])
            self.assertEqual('VERIFICATION_EXECUTION_PLAN_REQUIRED', results[-1]['reason_code'])
            self.assertEqual(['a.txt'], before_paths)
            self.assertEqual(before_paths, runner.git_changed_paths(root))

    def test_runner_consumes_only_the_supplied_accepted_plan(self):
        import json
        import subprocess

        from agent_harness.verification.profile import load_profile
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            completed = subprocess.CompletedProcess([], 0,
                json.dumps({'outcome': 'PASS', 'plan_id': 'verification-plan-v1:sha256:test'}), '')
            profile = load_profile(runner.HERE / 'verification-profiles/showcase.json')
            control_binding = {'command': 'python3', 'path': '/trusted/python3', 'sha256': 'a' * 64}
            record = {'profile_id': 'showcase', 'profile_hash': profile.content_hash,
                      'control_executable_binding': control_binding}
            with mock.patch('agent_harness.verification.authority.resolve_accepted', return_value=record), \
                 mock.patch.object(runner.verification_sandbox, 'verify_executable_binding') as verify, \
                 mock.patch.object(runner.subprocess, 'run', return_value=completed) as run:
                ok, results = runner.run_verification({}, root, root, 30,
                    accepted_plan_id='verification-plan-v1:sha256:test')
            self.assertTrue(ok)
            self.assertEqual('PASS', results[0]['outcome'])
            command = run.call_args.args[0]
            self.assertIn('--plan-id', command)
            self.assertNotIn('--base', command)
            self.assertEqual('-I', command[1])
            self.assertEqual(control_binding['path'], command[0])
            verify.assert_called_once_with(control_binding)
            self.assertNotIn('PYTHONPATH', run.call_args.kwargs['env'])
            self.assertNotIn('PYTHONHOME', run.call_args.kwargs['env'])

    def test_runner_refuses_missing_or_changed_control_binding_before_launch(self):
        from agent_harness.verification.profile import load_profile
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            profile = load_profile(runner.HERE / 'verification-profiles/showcase.json', repository=root)
            for binding, error in ((None, None),
                    ({'command': 'python3', 'path': '/trusted/python3', 'sha256': 'a' * 64},
                     runner.verification_sandbox.ExecutableResolutionError('executable-binding-changed'))):
                record = {'profile_id': 'showcase', 'profile_hash': profile.content_hash}
                if binding is not None:
                    record['control_executable_binding'] = binding
                with self.subTest(binding=binding), \
                     mock.patch('agent_harness.verification.authority.resolve_accepted', return_value=record), \
                     mock.patch.object(runner.verification_sandbox, 'verify_executable_binding',
                                       side_effect=error) as verify, \
                     mock.patch.object(runner.subprocess, 'run') as run:
                    ok, results = runner.run_verification({}, root, root, 10,
                        accepted_plan_id='verification-plan-v2:sha256:' + 'a' * 64)
                self.assertFalse(ok)
                self.assertEqual('verification-blocked', results[0]['machine_category'])
                run.assert_not_called()
                if binding is not None:
                    verify.assert_called_once_with(binding)

    def test_runner_launches_verify_under_isolated_active_interpreter(self):
        from agent_harness.verification.profile import load_profile
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            out = root / 'runs'
            out.mkdir()
            profile = load_profile(runner.HERE / 'verification-profiles/showcase.json', repository=root)
            record = {'profile_id': 'showcase', 'profile_hash': profile.content_hash}
            with mock.patch('agent_harness.verification.authority.resolve_accepted', return_value=record):
                ok, results = runner.run_verification({}, root, out, 10,
                    accepted_plan_id='verification-plan-v2:sha256:' + 'a' * 64)
            self.assertFalse(ok)
            self.assertEqual('PLAN_BINDING_MISMATCH', results[0]['reason_code'])
            self.assertNotIn('ModuleNotFoundError', str(results))

    def test_fingerprint_does_not_follow_untracked_symlink(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_tmp:
            root = pathlib.Path(tmp)
            outside = pathlib.Path(outside_tmp)
            subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
            subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=root, check=True)
            subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=root, check=True)
            (root / 'tracked.txt').write_text('base\\n', encoding='utf-8')
            subprocess.run(['git', 'add', 'tracked.txt'], cwd=root, check=True)
            subprocess.run(['git', 'commit', '-qm', 'base'], cwd=root, check=True)

            first_target = outside / 'first.txt'
            second_target = outside / 'second.txt'
            first_target.write_text('same-content\\n', encoding='utf-8')
            second_target.write_text('same-content\\n', encoding='utf-8')
            link = root / 'untracked-link'

            try:
                link.symlink_to(first_target)
            except OSError as exc:
                self.skipTest(f'symlink creation is unavailable: {exc}')

            original = runner.worktree_content_fingerprint(root)

            # External target contents are not part of the worktree fingerprint.
            first_target.write_text('changed-secret\\n', encoding='utf-8')
            self.assertEqual(original, runner.worktree_content_fingerprint(root))

            # The symlink object itself is part of the worktree, so retargeting it
            # must change the fingerprint even when both targets have equal content.
            first_target.write_text('same-content\\n', encoding='utf-8')
            link.unlink()
            link.symlink_to(second_target)
            self.assertNotEqual(original, runner.worktree_content_fingerprint(root))

    def test_fingerprint_tracks_broken_symlink_target_without_dereference(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
            subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=root, check=True)
            subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=root, check=True)
            (root / 'tracked.txt').write_text('base\\n', encoding='utf-8')
            subprocess.run(['git', 'add', 'tracked.txt'], cwd=root, check=True)
            subprocess.run(['git', 'commit', '-qm', 'base'], cwd=root, check=True)

            link = root / 'broken-link'
            try:
                link.symlink_to('missing-a')
            except OSError as exc:
                self.skipTest(f'symlink creation is unavailable: {exc}')

            first = runner.worktree_content_fingerprint(root)
            link.unlink()
            link.symlink_to('missing-b')
            second = runner.worktree_content_fingerprint(root)

            self.assertNotEqual(first, second)


class TaskRunnerSeamTest(unittest.TestCase):
    """[seam] Consumer policy is explicit: no built-in projection profiles, schema, CLI or profile root."""

    def test_projection_has_no_built_in_profiles_and_validation_needs_the_schema(self):
        schema = task_runner.load(runner.TASK_RESULT_SCHEMA_PATH)
        with self.assertRaisesRegex(ValueError, 'unsupported task-result schema profile'):
            task_runner.project_task_result_schema(schema, 'codex', 'codex-cli 0.160.0', profiles={})
        with self.assertRaises(TypeError):
            task_runner.validate_result({'status': 'pass'})  # no default canonical schema

    def test_verification_runs_the_consumer_cli_with_its_profile_root_and_binding_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve()
            profile = load_profile(FIXTURES / 'verification-profiles/showcase.json', repository=root)
            binding = {'command': 'python3', 'path': sys.executable, 'sha256': 'a' * 64}
            record = {'profile_id': 'showcase', 'profile_hash': profile.content_hash,
                      'control_executable_binding': binding}
            checked, completed = [], subprocess.CompletedProcess([], 0, '{"outcome": "PASS"}', '')
            with mock.patch.object(task_runner.subprocess, 'run', return_value=completed) as run:
                ok, rows = task_runner.run_verification(
                    {}, root, root / 'out', 5, accepted_plan_id='plan-1',
                    resolve_accepted=lambda repo, plan_id: record, profile_root=FIXTURES / 'verification-profiles',
                    verify_cli=root / 'consumer-verify.py', safe_env=lambda *a: {'PATH': '/usr/bin'},
                    verify_binding=checked.append)
            self.assertTrue(ok, rows)
            self.assertEqual([binding], checked)
            self.assertEqual([sys.executable, '-I', str(root / 'consumer-verify.py'), 'run'], run.call_args.args[0][:4])
            ok, rows = task_runner.run_verification(
                {}, root, root / 'out', 5, accepted_plan_id='plan-1', resolve_accepted=lambda repo, plan_id: record,
                profile_root=root / 'no-profiles', verify_cli=root / 'v.py', safe_env=lambda *a: {},
                verify_binding=checked.append)
            self.assertFalse(ok)
            self.assertEqual('ACCEPTED_PLAN_UNAVAILABLE', rows[0]['reason_code'])


    def test_profile_hash_mismatch_refuses_before_any_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve()
            record = {'profile_id': 'showcase', 'profile_hash': 'sha256:' + '0' * 64,
                      'control_executable_binding': {'command': 'python3', 'path': sys.executable, 'sha256': 'a' * 64}}
            with mock.patch.object(task_runner.subprocess, 'run') as run:
                ok, rows = task_runner.run_verification(
                    {}, root, root / 'out', 5, accepted_plan_id='plan-1', resolve_accepted=lambda repo, plan_id: record,
                    profile_root=FIXTURES / 'verification-profiles', verify_cli=root / 'v.py',
                    safe_env=lambda *a: {}, verify_binding=lambda binding: None)
            self.assertEqual((False, 'PLAN_BINDING_MISMATCH'), (ok, rows[0]['reason_code']))
            run.assert_not_called()

    def test_profile_is_loaded_per_call_so_consumer_patches_reach_it(self):
        """The source looks load_profile up per call; Showcase's tests patch it there (evaluation finding)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve()
            profile = load_profile(FIXTURES / 'verification-profiles/showcase.json', repository=root)
            record = {'profile_id': 'showcase', 'profile_hash': profile.content_hash,
                      'control_executable_binding': {'command': 'python3', 'path': sys.executable, 'sha256': 'a' * 64}}
            completed = subprocess.CompletedProcess([], 0, '{"outcome": "PASS"}', '')
            with mock.patch('agent_harness.verification.profile.load_profile', return_value=profile) as patched, \
                 mock.patch.object(task_runner.subprocess, 'run', return_value=completed):
                ok, _rows = task_runner.run_verification(
                    {}, root, root / 'out', 5, accepted_plan_id='plan-1', resolve_accepted=lambda repo, plan_id: record,
                    profile_root=root / 'not-read-because-patched', verify_cli=root / 'v.py',
                    safe_env=lambda *a: {}, verify_binding=lambda binding: None)
            self.assertTrue(ok)
            patched.assert_called_once()


if __name__ == "__main__":
    unittest.main()
