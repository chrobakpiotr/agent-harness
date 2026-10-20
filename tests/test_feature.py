"""AH5-14a: feature documents and state paths, identity and writer.

The validation tests are ported from Showcase `tooling/agent-harness/tests/test_harness.py` (`HarnessTest`) at
`fd96eeb`, unchanged apart from calling the library with a test policy that binds what Showcase's shim binds. The
state tests check the equivalence criteria in `docs/specs/AH5-14/packet.md`.
"""
import contextlib
import dataclasses
import hashlib
import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

from agent_harness import feature as f
from agent_harness import feature_state as fs
from agent_harness import state_snapshots
from agent_harness import verification_contract as vc

# Showcase's reviewer routing, as a test constant (it stays Showcase policy).
RISK_TO_REVIEWERS = {
    'architecture': ('architecture-reviewer',), 'api': ('architecture-reviewer',),
    'messaging': ('messaging-reviewer',), 'persistence': ('persistence-reviewer',),
    'concurrency': ('concurrency-reviewer',), 'security': ('security-reviewer',),
    'ai': ('ai-reviewer', 'security-reviewer'), 'infra': ('platform-reviewer',),
    'observability': ('platform-reviewer',), 'frontend': ('frontend-reviewer',),
    'performance': ('performance-reviewer',),
}


def repo_base(feature_dir):
    resolved = feature_dir.resolve()
    if resolved.parent.name == 'specs' and resolved.parent.parent.name == 'docs':
        return resolved.parent.parent.parent
    return pathlib.Path.cwd().resolve()


def is_active(feature_dir):
    try:
        rel = feature_dir.resolve().relative_to(repo_base(feature_dir))
    except ValueError:
        return False
    return len(rel.parts) == 3 and rel.parts[:2] == ('docs', 'specs')


def reviewers(task):
    selected = []
    for risk in task.get('risk_tags', []):
        for reviewer in RISK_TO_REVIEWERS.get(risk, ()):
            if reviewer not in selected:
                selected.append(reviewer)
    return selected


def policy(root, **changes):
    def contract(path):
        return vc.validate_contract(path, repo_root=lambda: root, repository=root,
                                    constitution='docs/agentic-sdd/constitution.md', is_active=is_active)
    return dataclasses.replace(f.FeaturePolicy(
        repo_base=repo_base, is_active=is_active,
        protocol_files=lambda path: sorted(p.resolve() for p in (repo_base(path) / 'AGENTS.md',) if p.exists()),
        profile_path=lambda base, profile: base / 'docs' / 'agentic-sdd' / 'agents' / f'{profile}.md',
        reviewers=reviewers, validate_contract=contract, criterion_ids=vc.criterion_ids,
        state_dir=pathlib.Path('.agent-state'),
        legacy_feature_path=lambda base, name: base / 'docs' / 'specs' / name), **changes)


def git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def refused(call):
    with contextlib.redirect_stderr(io.StringIO()) as err, unittest.TestCase().assertRaises(SystemExit) as raised:
        call()
    return raised.exception.code, err.getvalue()


class FeatureCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name).resolve()
        cwd = pathlib.Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        git(self.root, 'init', '-q')
        git(self.root, 'config', 'user.name', 'Test')
        git(self.root, 'config', 'user.email', 'test@example.invalid')
        git(self.root, 'commit', '--allow-empty', '-q', '-m', 'base')
        self.policy = policy(self.root)

    def validate(self, feature):
        return f.validate(feature, policy=self.policy)

    def feature(self, tasks=None, spec_text=None, *, root=None):
        repo = root or self.root
        feature = repo / 'docs' / 'specs' / 'TST-001'
        feature.mkdir(parents=True)
        roles = repo / 'docs' / 'agentic-sdd' / 'agents'
        roles.mkdir(parents=True, exist_ok=True)
        for profile in (
            'builder', 'evaluator', 'integration', 'architect', 'specialist',
            'architecture-reviewer', 'messaging-reviewer', 'persistence-reviewer',
            'concurrency-reviewer', 'security-reviewer', 'platform-reviewer',
            'ai-reviewer', 'frontend-reviewer', 'performance-reviewer',
            'grill-reviewer', 'prototype-agent', 'prototype-evaluator'
        ):
            (roles / f'{profile}.md').write_text(f'# {profile}\n', encoding='utf-8')
        (feature / 'spec.md').write_text(
            spec_text or '# TST-001\n\n- AC-001: works\n- AC-002: is independently verified\n',
            encoding='utf-8',
        )
        (feature / 'plan.md').write_text('# plan\n', encoding='utf-8')
        doc = {
            'feature': 'TST-001',
            'max_parallel': 4,
            'max_rework_attempts': 2,
            'tasks': tasks or [
                {
                    'id': 'T-001', 'title': 'Build', 'objective': 'Implement it', 'role': 'builder',
                    'depends_on': [], 'allowed_paths': ['modules/domain/**'], 'risk_tags': ['domain'],
                    'acceptance_criteria': ['AC-001'], 'verification': ['./gradlew :domain:test'],
                },
                {
                    'id': 'T-900', 'title': 'Evaluate', 'objective': 'Falsify it', 'role': 'evaluator',
                    'depends_on': ['T-001'], 'allowed_paths': ['docs/specs/TST-001/evidence/**'],
                    'risk_tags': ['evaluation'], 'acceptance_criteria': ['AC-001', 'AC-002'],
                    'verification': ['./gradlew test'],
                },
            ],
        }
        (feature / 'tasks.json').write_text(json.dumps(doc), encoding='utf-8')
        return feature


class FeatureValidationTest(FeatureCase):
    """Ported from `HarnessTest` (the 11 tests that use only `validate`)."""

    def test_valid_feature(self):
        self.assertEqual([], self.validate(self.feature()))

    def test_inactive_maintained_example_does_not_require_live_design_gate(self):
        feature = self.feature()
        inactive = self.root / 'docs' / 'agentic-sdd' / 'examples' / 'TST-001'
        inactive.parent.mkdir(parents=True, exist_ok=True)
        feature.rename(inactive)
        (inactive / 'design.json').write_text(json.dumps({'required_for_orchestration': True}))
        self.assertFalse(any('design preflight gate is required' in error for error in self.validate(inactive)))

    def test_feature_id_must_match_directory(self):
        feature = self.feature()
        doc = json.loads((feature / 'tasks.json').read_text())
        doc['feature'] = 'OTHER-001'
        (feature / 'tasks.json').write_text(json.dumps(doc))
        self.assertTrue(any('must match feature directory name' in e for e in self.validate(feature)))

    def test_missing_risk_selected_reviewer_profile_is_rejected(self):
        feature = self.feature()
        doc = json.loads((feature / 'tasks.json').read_text())
        doc['tasks'][0]['risk_tags'] = ['architecture']
        (feature / 'tasks.json').write_text(json.dumps(doc))
        (self.root / 'docs' / 'agentic-sdd' / 'agents' / 'architecture-reviewer.md').unlink()
        self.assertTrue(any('selects missing agent profile: architecture-reviewer' in e for e in self.validate(feature)))

    def test_all_spec_acceptance_criteria_require_evaluator_coverage(self):
        feature = self.feature()
        doc = json.loads((feature / 'tasks.json').read_text())
        doc['tasks'][1]['acceptance_criteria'] = ['AC-001']
        (feature / 'tasks.json').write_text(json.dumps(doc))
        self.assertTrue(any('spec acceptance criteria not covered by an evaluator' in e for e in self.validate(feature)))

    def test_missing_acceptance_criterion_is_rejected(self):
        feature = self.feature()
        doc = json.loads((feature / 'tasks.json').read_text())
        doc['tasks'][0]['acceptance_criteria'] = ['AC-999']
        (feature / 'tasks.json').write_text(json.dumps(doc))
        self.assertTrue(any('AC-999' in e for e in self.validate(feature)))

    def test_cycle_is_rejected(self):
        tasks = [
            {'id': 'T-001', 'title': 'A', 'objective': 'A', 'role': 'builder', 'depends_on': ['T-002'],
             'allowed_paths': ['a/**'], 'risk_tags': [], 'acceptance_criteria': ['AC-001'], 'verification': ['true']},
            {'id': 'T-002', 'title': 'B', 'objective': 'B', 'role': 'builder', 'depends_on': ['T-001'],
             'allowed_paths': ['b/**'], 'risk_tags': [], 'acceptance_criteria': ['AC-001'], 'verification': ['true']},
            {'id': 'T-900', 'title': 'E', 'objective': 'E', 'role': 'evaluator', 'depends_on': ['T-001', 'T-002'],
             'allowed_paths': ['e/**'], 'risk_tags': [], 'acceptance_criteria': ['AC-001'], 'verification': ['true']},
        ]
        self.assertTrue(any('cycle' in e for e in self.validate(self.feature(tasks))))

    def test_parallel_builder_write_collision_is_rejected(self):
        tasks = [
            {'id': 'T-001', 'title': 'A', 'objective': 'A', 'role': 'builder', 'depends_on': [],
             'allowed_paths': ['modules/domain/**'], 'risk_tags': [], 'acceptance_criteria': ['AC-001'], 'verification': ['true']},
            {'id': 'T-002', 'title': 'B', 'objective': 'B', 'role': 'builder', 'depends_on': [],
             'allowed_paths': ['modules/domain/**'], 'risk_tags': [], 'acceptance_criteria': ['AC-001'], 'verification': ['true']},
            {'id': 'T-900', 'title': 'E', 'objective': 'E', 'role': 'evaluator', 'depends_on': ['T-001', 'T-002'],
             'allowed_paths': ['e/**'], 'risk_tags': [], 'acceptance_criteria': ['AC-001'], 'verification': ['true']},
        ]
        self.assertTrue(any('overlapping allowed_paths' in e for e in self.validate(self.feature(tasks))))

    def test_nested_parallel_builder_write_collision_is_rejected(self):
        tasks = [
            {'id': 'T-001', 'title': 'A', 'objective': 'A', 'role': 'builder', 'depends_on': [],
             'allowed_paths': ['modules/domain/**'], 'risk_tags': [], 'acceptance_criteria': ['AC-001'], 'verification': ['true']},
            {'id': 'T-002', 'title': 'B', 'objective': 'B', 'role': 'builder', 'depends_on': [],
             'allowed_paths': ['modules/domain/order/**'], 'risk_tags': [], 'acceptance_criteria': ['AC-001'], 'verification': ['true']},
            {'id': 'T-900', 'title': 'E', 'objective': 'E', 'role': 'evaluator', 'depends_on': ['T-001', 'T-002'],
             'allowed_paths': ['e/**'], 'risk_tags': [], 'acceptance_criteria': ['AC-001'], 'verification': ['true']},
        ]
        self.assertTrue(any('overlapping allowed_paths' in e for e in self.validate(self.feature(tasks))))

    def test_unsafe_allowed_path_is_rejected(self):
        feature = self.feature()
        doc = json.loads((feature / 'tasks.json').read_text())
        doc['tasks'][0]['allowed_paths'] = ['../outside/**']
        (feature / 'tasks.json').write_text(json.dumps(doc))
        self.assertTrue(any('unsafe path' in e for e in self.validate(feature)))

    def test_lease_configuration_is_validated(self):
        feature = self.feature()
        doc = json.loads((feature / 'tasks.json').read_text())
        doc['lease_ttl_seconds'] = 100
        doc['heartbeat_interval_seconds'] = 60
        (feature / 'tasks.json').write_text(json.dumps(doc))
        self.assertTrue(any('less than half' in e for e in self.validate(feature)))


class FeaturePolicySeamTest(FeatureCase):
    def test_the_policy_has_no_defaults(self):
        with self.assertRaises(TypeError):
            f.FeaturePolicy()
        with self.assertRaises(TypeError):
            f.validate(self.feature())  # no policy, no silent layout

    def test_validation_uses_the_bound_profiles_reviewers_and_contract(self):
        feature = self.feature()
        seen = []
        bound = dataclasses.replace(
            self.policy, profile_path=lambda base, profile: seen.append(profile) or base / 'nowhere' / profile,
            validate_contract=lambda path: ['contract says no'], criterion_ids=lambda path: {'VC-001'})
        errors = f.validate(feature, policy=bound)
        self.assertIn('contract says no', errors)
        self.assertTrue(any('selects missing agent profile: builder' in e for e in errors))
        self.assertIn('evaluator', seen)
        doc = json.loads((feature / 'tasks.json').read_text())
        doc['tasks'][0]['acceptance_criteria'] = ['VC-001']
        (feature / 'tasks.json').write_text(json.dumps(doc))
        known = f.validate(feature, policy=dataclasses.replace(self.policy, criterion_ids=lambda path: {'VC-001'}))
        self.assertFalse(any('references criterion absent' in e for e in known))  # a contract criterion is known
        self.assertTrue(any('references criterion absent' in e and 'VC-001' in e for e in self.validate(feature)))
        routed = dataclasses.replace(self.policy, reviewers=lambda task: ['no-such-reviewer'])
        self.assertTrue(any('selects missing agent profile: no-such-reviewer' in e
                            for e in f.validate(feature, policy=routed)))

    def test_load_validated_refuses_an_invalid_feature(self):
        feature = self.feature()
        self.assertEqual('TST-001', f.load_validated(feature, policy=self.policy)['feature'])
        doc = json.loads((feature / 'tasks.json').read_text())
        doc['feature'] = 'OTHER-001'
        (feature / 'tasks.json').write_text(json.dumps(doc))
        code, err = refused(lambda: f.load_validated(feature, policy=self.policy))
        self.assertEqual(2, code)
        self.assertIn('ERROR: feature is invalid', err)

    def test_protocol_fingerprint_covers_the_bound_files(self):
        feature = self.feature()
        (self.root / 'AGENTS.md').write_text('# agents\n')
        first = f.protocol_fingerprint(feature, policy=self.policy)
        expected = hashlib.sha256(b'AGENTS.md\0# agents\n\0').hexdigest()
        self.assertEqual(expected, first)
        (self.root / 'AGENTS.md').write_text('# changed\n')
        self.assertNotEqual(first, f.protocol_fingerprint(feature, policy=self.policy))
        none = dataclasses.replace(self.policy, protocol_files=lambda path: [])
        self.assertEqual(hashlib.sha256().hexdigest(), f.protocol_fingerprint(feature, policy=none))

    def test_feature_fingerprint_covers_the_feature_files(self):
        feature = self.feature()
        first = f.feature_fingerprint(feature)
        for name in ('spec.md', 'plan.md', 'tasks.json'):
            original = (feature / name).read_text()
            (feature / name).write_text(original + ' ')
            self.assertNotEqual(first, f.feature_fingerprint(feature), name)
            (feature / name).write_text(original)
        self.assertEqual(first, f.feature_fingerprint(feature))
        (feature / 'verification-contract.json').write_text('{}')
        self.assertNotEqual(first, f.feature_fingerprint(feature))


class FeatureStateTest(FeatureCase):
    def test_state_lives_beside_the_common_git_directory_under_its_key(self):
        feature = self.feature()
        common = pathlib.Path(git(self.root, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
        key = hashlib.sha256(f'{common}\0TST-001'.encode()).hexdigest()[:20]
        self.assertEqual(common, fs.git_common_dir(feature, policy=self.policy))
        self.assertEqual(key, fs.state_key(feature, policy=self.policy))
        self.assertEqual(self.root / '.agent-state', fs.runtime_state_dir(feature, policy=self.policy))
        self.assertEqual(self.root / '.agent-state' / f'TST-001-{key}.json', fs.state_path(feature, policy=self.policy))
        absolute = dataclasses.replace(self.policy, state_dir=self.root / 'elsewhere')
        self.assertEqual(self.root / 'elsewhere', fs.runtime_state_dir(feature, policy=absolute))
        self.assertEqual(state_snapshots.lifecycle_lock_path(self.root), fs.lock_path(feature, policy=self.policy))
        legacy = hashlib.sha256(str((self.root / 'docs/specs/TST-001').resolve()).encode()).hexdigest()[:20]
        self.assertEqual(f'TST-001-{legacy}.json', fs.legacy_state_name(feature, policy=self.policy))
        self.assertEqual(self.root / '.agent-state' / f'TST-001-{legacy}.json',
                         fs.main_legacy_state_path(feature, policy=self.policy))

    def test_a_linked_worktree_resolves_the_same_state(self):
        feature = self.feature()
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'feature')
        linked = self.root.parent / f'{self.root.name}-linked'
        git(self.root, 'worktree', 'add', '-q', str(linked))
        self.addCleanup(subprocess.run, ['git', 'worktree', 'remove', '--force', str(linked)], cwd=self.root,
                        capture_output=True)
        other = linked / 'docs' / 'specs' / 'TST-001'
        self.assertEqual(fs.state_path(feature, policy=self.policy), fs.state_path(other, policy=self.policy))

    def test_identity_and_version_refusals(self):
        feature = self.feature()
        doc = json.loads((feature / 'tasks.json').read_text())
        good = {'fingerprint': f.feature_fingerprint(feature), 'feature': 'TST-001',
                'tasks': {'T-001': {}, 'T-900': {}}, 'protocol_version': f.STATE_SCHEMA_VERSION}
        fs.validate_loaded_state(feature, doc, good)
        cases = {
            'fingerprint differs; refusing lifecycle state adoption/migration': {'fingerprint': 'x'},
            'logical feature identity differs': {'feature': 'OTHER'},
            'task set differs from tasks.json; refusing': {'tasks': {'T-001': {}}},
            'predates explicit protocol versioning': {'protocol_version': None, 'state_version': 2},
            'protocol/schema version 99 is incompatible': {'protocol_version': 99},
        }
        for message, change in cases.items():
            with self.subTest(message):
                code, err = refused(lambda: fs.validate_loaded_state(feature, doc, {**good, **change}))
                self.assertEqual((2, True), (code, message in err))
        code, err = refused(lambda: fs.validate_state_identity(feature, doc, good, fingerprint=lambda path: 'other'))
        self.assertIn('fingerprint differs', err)

    def test_the_single_writer_commits_a_snapshot_in_the_source_format(self):
        feature = self.feature()
        state = {'feature': 'TST-001', 'tasks': {'T-001': {'status': 'ready'}}, 'b': 1, 'a': [1.5]}
        calls = []

        def injector(point, *args, **kwargs):
            calls.append(point)
        fs.save_state(feature, state, policy=self.policy, fault_injector=injector)
        path = fs.state_path(feature, policy=self.policy)
        expected = json.dumps(state, indent=2, sort_keys=True, allow_nan=False).encode('utf-8') + b'\n'
        self.assertEqual(expected, path.read_bytes())
        self.assertTrue(calls)  # the injector reaches the snapshot commit
        self.assertTrue(state_snapshots.current_ref_exists(self.root, 'TST-001'))
        with self.assertRaises(ValueError):  # as the source: a value JSON cannot hold is refused before any write
            fs.save_state(feature, {'bad': float('nan')}, policy=self.policy)
        self.assertEqual(expected, path.read_bytes())  # nothing replaced


if __name__ == '__main__':
    unittest.main()
