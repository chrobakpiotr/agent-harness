import contextlib
import copy
import io
import pathlib
import unittest
from types import SimpleNamespace

from agent_harness import lifecycle_checks as checks
from agent_harness.feature import FeaturePolicy
from agent_harness.feature_access import StateAccess


def _state_access(*, store=None, feature_state=None, fingerprint='f' * 64):
    policy = FeaturePolicy(
        repo_base=lambda path: path,
        is_active=lambda _path: True,
        protocol_files=lambda _path: [],
        profile_path=lambda root, profile: root / profile,
        reviewers=lambda _task: [],
        validate_contract=lambda _path: [],
        criterion_ids=lambda _path: set(),
        state_dir=pathlib.Path('.agent-state'),
        legacy_feature_path=lambda root, name: root / name,
    )
    return StateAccess(
        policy=policy,
        common_dir=lambda path: path / '.git',
        locking=None,
        fingerprint=lambda _path: fingerprint,
        protocol_fingerprint=lambda _path: 'protocol',
        save_state=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected state write')),
        load_validated=lambda _path: {},
        read_state_pure=lambda _path, _doc: feature_state,
        verification_store=lambda: store,
        completion_fault_injector=lambda: None,
        recovery_incident=lambda _path: (_ for _ in ()).throw(AssertionError('unexpected recovery lookup')),
        recovery_epoch_required=lambda *_args: (_ for _ in ()).throw(AssertionError('unexpected recovery')),
    )


class LifecycleChecksTest(unittest.TestCase):
    def test_resume_validation_is_read_only_and_fails_closed(self):
        active = {
            'plan_id': 'plan-1', 'task_attempt': 2, 'lifecycle_generation': 3,
            'reason_sha256': 'a' * 64, 'requested_at': '2026-10-24T10:00:00Z',
            'operator': 'reviewer', 'prior_result_sha256': 'b' * 64,
            'prior_result_path': '.agent-runs/F-1/result.json',
        }
        entry = {'attempts': 2, 'verification_resume_active': active,
                 'verification_resume_history': [dict(active)]}
        before = copy.deepcopy(entry)
        self.assertEqual(active, checks.validate_active_verification_resume(entry))
        self.assertEqual(before, entry)

        entry['verification_resume_history'][0]['plan_id'] = 'other-plan'
        before = copy.deepcopy(entry)
        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as raised:
            checks.validate_active_verification_resume(entry)
        self.assertEqual(2, raised.exception.code)
        self.assertIn('VERIFICATION_RESUME_STATE_INVALID', error.getvalue())
        self.assertEqual(before, entry)

    def test_packet_recovery_identity_requires_versioned_recomputable_binding(self):
        packet = {'task': 'T-1', 'revision': 4}
        packet['packet_sha256'] = checks.canonical_packet_payload_sha256(packet)
        revision_id = checks.packet_revision_id(packet)
        recovery = {'recovery_version': 1, 'packet_identity': packet['packet_sha256'],
                    'semantic_contract_sha256': 'c' * 64}
        self.assertEqual(checks.PACKET_PAYLOAD_IDENTITY_SCHEME,
                         checks.packet_identity_from_recovery(recovery).scheme)
        self.assertTrue(checks.recovery_proves_packet_binding(
            recovery, packet, revision_id, 'c' * 64))
        recovery['recovery_version'] = True
        self.assertIsNone(checks.packet_identity_from_recovery(recovery))

    def test_partial_claim_check_does_not_mutate_state(self):
        entry = {'status': 'running', 'attempts': 4, 'active_retry_authorization': 'grant'}
        before = copy.deepcopy(entry)
        self.assertTrue(checks.is_unrecovered_partial_claim(entry))
        self.assertEqual(before, entry)

    def test_accepted_plan_resolution_reads_and_validates_without_mutating_state(self):
        record = {
            'schema_version': 1, 'profile_id': 'profile', 'plan_id': 'plan-1',
            'task_id': 'T-1', 'task_attempt': 2, 'feature_id': 'F-1',
            'feature_fingerprint': 'f' * 64, 'lifecycle_generation': 3,
            'family': {}, 'profile_hash': '1' * 64, 'policy_checkpoint': '2' * 64,
            'candidate_identity': 'candidate', 'final_changed_surface_id': 'surface',
            'origin_binding': {},
        }
        binding = {key: record.get(key) for key in (
            'schema_version', 'profile_id', 'plan_id', 'task_id', 'task_attempt',
            'feature_fingerprint', 'lifecycle_generation', 'family', 'profile_hash',
            'policy_checkpoint', 'candidate_identity', 'final_changed_surface_id', 'origin_binding')}
        state = {'feature_generation': 3, 'tasks': {'T-1': {'status': 'running', 'attempts': 2}},
                 'verification_authority': {'accepted_plan_id': 'plan-1', 'generation': 3,
                                            'binding': binding}}
        before = copy.deepcopy(state)
        calls = []

        class Store:
            def load_plan_record(self, plan_id):
                calls.append(('load_plan_record', plan_id))
                return record

        store = SimpleNamespace(VerificationStore=lambda _repository: Store(),
                                StoreError=type('StoreError', (Exception,), {}))
        access = _state_access(store=store, feature_state=state)

        class Authority:
            @staticmethod
            def validate_plan_record(value, **kwargs):
                calls.append(('validate_plan_record', kwargs))
                assert value is record

        result = checks.resolve_accepted_verification_plan(
            pathlib.Path('/repo'), 'plan-1', access=access,
            feature_dir_for=lambda root, name: root / 'docs' / 'specs' / name,
            verification_authority=lambda: Authority,
        )
        self.assertIs(record, result)
        self.assertEqual(before, state)
        self.assertEqual(('load_plan_record', 'plan-1'), calls[0])
        self.assertEqual(('validate_plan_record', {'repository': pathlib.Path('/repo'), 'reconstruct': True}), calls[1])
        # The resolver receives read/validation capabilities only; writes and recovery are not dependencies.
        self.assertNotIn('save_state', checks.resolve_accepted_verification_plan.__code__.co_names)
        self.assertNotIn('recover', checks.resolve_accepted_verification_plan.__code__.co_names)


if __name__ == '__main__':
    unittest.main()
