import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

# Showcase profile at 50c18f9, copied as a test input: the package ships no built-in profile.
PROFILES = pathlib.Path(__file__).resolve().parent / 'fixtures' / 'verification-profiles'

from agent_harness.verification import authority
from agent_harness.verification.candidate import seal_candidate
from agent_harness.verification.model import Family
from agent_harness.verification.planner import build_plan
from agent_harness.verification.profile import load_profile
from agent_harness.verification.store import StoreError


class AcceptedPlanResolutionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        subprocess.run(['git', 'config', 'user.email', 'test@example.invalid'], cwd=self.root, check=True)
        subprocess.run(['git', 'config', 'user.name', 'test'], cwd=self.root, check=True)
        (self.root / 'tooling/agent-harness').mkdir(parents=True)
        (self.root / 'tooling/agent-harness/seed.py').write_text('seed = 1\n')
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        subprocess.run(['git', 'commit', '-qm', 'base'], cwd=self.root, check=True)
        self.base = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.root, text=True).strip()
        (self.root / 'tooling/agent-harness/seed.py').write_text('seed = 2\n')
        self.profile_id = 'showcase'
        self.profile = load_profile(PROFILES / 'showcase.json')
        self.family = Family('authority-test', self.base, 'integration', self.profile.content_hash,
                             'a' * 64)
        seal = seal_candidate(self.root, self.base, {
            'family_id': self.family.id, 'profile_hash': self.profile.content_hash,
            'policy_checkpoint': self.family.policy_checkpoint,
            'origin_policy': self.family.origin_policy})
        self.family = Family(self.family.id, self.base, self.family.origin_policy,
            self.family.profile_hash, self.family.policy_checkpoint, seal.candidate_identity,
            seal.changed_surface_id)
        self.plan = build_plan(self.root, self.profile, self.family)
        self.record = authority.execution_plan_record('SDD-OBS-001', 'f' * 64, 2,
            self.profile_id, self.profile, self.plan, task_id='T-001', task_attempt=1,
            origin_binding='integration')

    def test_origin_authority_is_v2_and_execution_fails_closed(self):
        self.assertEqual(2, self.record['schema_version'])
        for obligation in self.record['obligations']:
            self.assertEqual('independent', obligation['required_origin'])
        with mock.patch.object(authority, 'resolve_accepted', return_value=self.record):
            with self.assertRaisesRegex(StoreError, 'VERIFICATION_ORIGIN_ADMISSION_UNAVAILABLE'):
                authority.resolve_execution(self.root, self.record['plan_id'], lifecycle=None, profile_root=PROFILES)

    def test_rehashed_origin_or_membership_cannot_publish(self):
        import copy
        import hashlib
        from agent_harness.verification.serialization import canonical
        from agent_harness.verification.store import VerificationStore
        for mutation in ('missing', 'unknown', 'source', 'class', 'membership'):
            record = copy.deepcopy(self.record)
            if mutation == 'missing':
                record['obligations'][0].pop('required_origin', None)
            elif mutation == 'unknown':
                record['obligations'][0]['required_origin'] = 'manual'
            elif mutation == 'source':
                record['obligations'][0]['requirement_source'] = 'task-command'
            elif mutation == 'class':
                record['obligations'][0]['independent_execution_class'] = 'manual'
            else:
                record['execution_units'][0]['obligation_ids'] *= 2
            keys = {'family_id', 'gate_id', 'profile_gate_id', 'occurrence', 'ordinal',
                    'requirement_source', 'required_origin', 'independent_execution_class'}
            for item, unit in zip(record['obligations'], record['execution_units']):
                previous = item['obligation_id']
                item['obligation_id'] = 'verification-obligation-v2:sha256:' + hashlib.sha256(
                    canonical({k: v for k, v in item.items() if k in keys})).hexdigest()
                unit['obligation_ids'] = [item['obligation_id'] if oid == previous else oid
                                           for oid in unit['obligation_ids']]
                unit['required_origin'] = item.get('required_origin')
                unit['independent_execution_class'] = item['independent_execution_class']
                unit['unit_id'] = 'verification-unit-v2:sha256:' + hashlib.sha256(
                    canonical({k: v for k, v in unit.items() if k != 'unit_id'})).hexdigest()
            body = {k: v for k, v in record.items() if k != 'plan_id'}
            record['plan_id'] = 'verification-plan-v2:sha256:' + hashlib.sha256(canonical(body)).hexdigest()
            with self.subTest(mutation=mutation), self.assertRaises(StoreError):
                VerificationStore(self.root, profile_root=PROFILES).publish_plan_record(record)

    def test_rehashed_malformed_publication_shape_rejects(self):
        import copy
        import hashlib
        from agent_harness.verification.serialization import canonical
        from agent_harness.verification.store import VerificationStore
        mutations = (
            ('origin', 'manual'), ('base_sha', None), ('base_sha', 'not-a-sha'),
            ('candidate_identity', 'candidate-v99:sha256:' + 'a' * 64),
            ('final_changed_surface_id', 123),
            ('dependencies', ['unknown']),
            ('dependencies', ['agentic-sdd-doctor', 'agentic-sdd-doctor']),
            ('dependencies', ['agentic-sdd-doctor']),
            ('retry_controls', ['x', 'x']),
        )
        for field, value in mutations:
            record = copy.deepcopy(self.record)
            if field == 'origin':
                record['origin_binding'] = value
                record['family']['origin_policy'] = value
            elif field == 'base_sha':
                record['family'][field] = value
            elif field in ('candidate_identity', 'final_changed_surface_id'):
                record[field] = value
                record['family'][field] = value
            else:
                record['obligations'][0][field] = value
            record['plan_id'] = 'verification-plan-v2:sha256:' + hashlib.sha256(
                canonical({k: v for k, v in record.items() if k != 'plan_id'})).hexdigest()
            with self.subTest(field=field, value=value), self.assertRaises(StoreError):
                VerificationStore(self.root, profile_root=PROFILES).publish_plan_record(record)

    def test_full_sha1_policy_checkpoint_preserves_existing_shape(self):
        import dataclasses
        family = dataclasses.replace(self.plan.family, policy_checkpoint='a' * 40)
        record = authority.execution_plan_record('SDD-OBS-001', 'f' * 64, 2,
            self.profile_id, self.profile, dataclasses.replace(self.plan, family=family),
            task_id='T-001', task_attempt=1, origin_binding='integration')
        authority.validate_plan_record(record, profile_root=PROFILES)

    def test_invalid_family_identifier_and_unmapped_critical_retry_reject(self):
        import dataclasses
        import hashlib
        from agent_harness.verification.serialization import canonical
        plan = build_plan(self.root, self.profile, self.family, task_commands=['python3 -V'])
        record = authority.execution_plan_record('SDD-OBS-001', 'f' * 64, 2,
            self.profile_id, self.profile, plan, task_id='T-001', task_attempt=1,
            task_commands=['python3 -V'], origin_binding='integration')
        item = next(o for o in record['obligations'] if o['profile_gate_id'] is None)
        item['critical'] = True
        item['retry_policy'] = 'allow'
        record['plan_id'] = 'verification-plan-v2:sha256:' + hashlib.sha256(
            canonical({k: v for k, v in record.items() if k != 'plan_id'})).hexdigest()
        with self.assertRaises(StoreError):
            authority.validate_plan_record(record, profile_root=PROFILES)
        bad_plan = dataclasses.replace(self.plan,
            family=dataclasses.replace(self.plan.family, id='../untrusted'))
        record = authority.execution_plan_record('SDD-OBS-001', 'f' * 64, 2,
            self.profile_id, self.profile, bad_plan, task_id='T-001', task_attempt=1,
            origin_binding='integration')
        with self.assertRaises(StoreError):
            authority.validate_plan_record(record, profile_root=PROFILES)

    def test_profile_absent_permission_cannot_authorize_independent(self):
        import dataclasses
        profile = dataclasses.replace(self.profile, gates=tuple(
            dataclasses.replace(g, independent_execution_classes=()) for g in self.profile.gates))
        with mock.patch('agent_harness.verification.profile.load_profile', return_value=profile):
            with self.assertRaisesRegex(StoreError, 'invalid-verification-plan'):
                authority.validate_plan_record(self.record, profile_root=PROFILES)

    def test_legacy_plan_id_is_unavailable_without_modifying_artifacts(self):
        from agent_harness.verification.store import VerificationStore
        store = VerificationStore(self.root, profile_root=PROFILES)
        with self.assertRaisesRegex(StoreError, 'VERIFICATION_EXECUTION_PLAN_REQUIRED'):
            store.load_plan_record('verification-plan-v1:sha256:' + 'a' * 64)
        self.assertFalse(store.plans.exists())

    def test_unmapped_integration_command_is_task_origin(self):
        plan = build_plan(self.root, self.profile, self.family, task_commands=['python3 -V'])
        record = authority.execution_plan_record('SDD-OBS-001', 'f' * 64, 2,
            self.profile_id, self.profile, plan, task_id='T-001', task_attempt=1,
            task_commands=['python3 -V'], origin_binding='integration')
        authority.validate_plan_record(record, profile_root=PROFILES)
        legacy = [o for o in record['obligations'] if o['profile_gate_id'] is None]
        self.assertEqual(1, len(legacy))
        self.assertEqual('task', legacy[0]['required_origin'])
        self.assertEqual('task-command', legacy[0]['requirement_source'])

    def test_exact_accepted_plan_reconstructs_current_units_without_running_them(self):
        with mock.patch.object(authority, 'resolve_accepted', return_value=self.record):
            with self.assertRaisesRegex(StoreError, 'VERIFICATION_ORIGIN_ADMISSION_UNAVAILABLE'):
                authority.resolve_execution(self.root, self.record['plan_id'], lifecycle=None, profile_root=PROFILES)

    def test_post_seal_candidate_mutation_rejects_before_execution(self):
        (self.root / 'tooling/agent-harness/seed.py').write_text('mutated after seal\n')
        with mock.patch.object(authority, 'resolve_accepted', return_value=self.record):
            with self.assertRaisesRegex(StoreError, 'PLAN_BINDING_MISMATCH'):
                authority.resolve_execution(self.root, self.record['plan_id'], lifecycle=None, profile_root=PROFILES)

    def test_unknown_execution_unit_rejects_before_execution(self):
        with mock.patch.object(authority, 'resolve_accepted', return_value=self.record):
            with self.assertRaisesRegex(StoreError, 'PLAN_BINDING_MISMATCH'):
                authority.resolve_execution(self.root, self.record['plan_id'], lifecycle=None, profile_root=PROFILES, unit_id='unknown')

    def test_trusted_orchestrator_plan_creation_binds_running_attempt_and_candidate(self):
        feature_dir = self.root / 'docs/specs/SDD-OBS-001'
        feature_dir.mkdir(parents=True)
        state = {'feature_generation': 2,
                 'tasks': {'T-001': {'status': 'running', 'attempts': 1}}}
        lifecycle = mock.Mock(load_validated=mock.Mock(return_value={'feature': 'SDD-OBS-001'}),
                              load_state=mock.Mock(return_value=state),
                              feature_fingerprint=mock.Mock(return_value='f' * 64))
        with mock.patch.object(authority, 'publish_and_accept') as accept:
            record = authority.prepare_task_plan(self.root, feature_dir, 'T-001', 1,
                                                 self.base, ['python3 -V'], lifecycle=lifecycle,
                                                 profile_root=PROFILES, profile_id='showcase')
        self.assertEqual('T-001', record['task_id'])
        self.assertEqual(1, record['task_attempt'])
        self.assertEqual(2, record['lifecycle_generation'])
        self.assertEqual(self.family.base_sha, record['family']['base_sha'])
        self.assertEqual(record['candidate_identity'], record['family']['candidate_identity'])
        accept.assert_called_once_with(self.root.resolve(), feature_dir.resolve(), record,
                                       expected_generation=2, lifecycle=lifecycle,
                                       profile_root=PROFILES)

    def test_trusted_orchestrator_cannot_accept_plan_for_noncurrent_attempt(self):
        feature_dir = self.root / 'docs/specs/SDD-OBS-001'
        feature_dir.mkdir(parents=True)
        state = {'feature_generation': 2,
                 'tasks': {'T-001': {'status': 'running', 'attempts': 2}}}
        lifecycle = mock.Mock(load_validated=mock.Mock(return_value={'feature': 'SDD-OBS-001'}),
                              load_state=mock.Mock(return_value=state))
        with mock.patch.object(authority, 'publish_and_accept') as accept:
            with self.assertRaisesRegex(StoreError, 'ACCEPTED_PLAN_UNAVAILABLE'):
                authority.prepare_task_plan(self.root, feature_dir, 'T-001', 1,
                                            self.base, ['python3 -V'], lifecycle=lifecycle,
                                            profile_root=PROFILES, profile_id='showcase')
        accept.assert_not_called()


if __name__ == '__main__':
    unittest.main()
