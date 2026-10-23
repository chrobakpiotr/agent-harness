"""AH5-14b-1: lifecycle state access (lock, load, completion-record readers) and the refuse-only recovery hook.

Showcase's own suites exercise the moved code end to end through its shim; these tests pin the library's contract:
the hook rules master set (2026-10-23), the lock-and-load order, and the binding having no defaults.
"""
import dataclasses
import json
import types
import unittest
from unittest import mock

from test_feature import FeatureCase, refused

from agent_harness import feature as f
from agent_harness import feature_access as fa
from agent_harness import feature_state as fs
from agent_harness.verification import store as library_store


class AccessCase(FeatureCase):
    def setUp(self):
        super().setUp()
        self.saved = []
        self.injector = None
        self.incident = None
        self.epoch = lambda path, incident, status: False

        def save(feature_dir, state, **kwargs):
            self.saved.append((state, kwargs))
            fs.save_state(feature_dir, state, policy=self.policy, **kwargs)
        self.access = fa.StateAccess(
            policy=self.policy, common_dir=lambda path: fs.git_common_dir(path, policy=self.policy),
            locking=fs.fcntl, fingerprint=f.feature_fingerprint,
            protocol_fingerprint=lambda path: f.protocol_fingerprint(path, policy=self.policy),
            save_state=save, load_validated=lambda path: f.load_validated(path, policy=self.policy),
            read_state_pure=lambda path, doc: fa._read_state_unlocked_pure(path, doc, access=self.access),
            verification_store=lambda: library_store,
            completion_fault_injector=lambda: self.injector,
            recovery_incident=lambda path: self.incident,
            recovery_epoch_required=lambda path, incident, status: self.epoch(path, incident, status))

    def doc(self, feature):
        return json.loads((feature / 'tasks.json').read_text())


class RecoveryHookTest(AccessCase):
    def test_the_epoch_hook_may_only_refuse(self):
        feature = self.feature()
        self.incident = {'incident_id': 'i-1'}
        cases = [(False, False), (True, True), (None, True), (0, True), ('no', True), ((), True)]
        for verdict, required in cases:  # only an exact False allows; 0 == False must still refuse
            with self.subTest(verdict=verdict):
                self.epoch = lambda path, incident, status, verdict=verdict: verdict
                self.assertIs(required, fa.state_recovery_required(feature, access=self.access))

    def test_an_error_in_the_hook_blocks(self):
        feature = self.feature()
        self.incident = {'incident_id': 'i-1'}

        def broken(path, incident, status):
            raise RuntimeError('hook failed')
        self.epoch = broken
        with self.assertRaises(RuntimeError):
            fa.state_recovery_required(feature, access=self.access)

    def test_without_an_incident_the_epoch_hook_is_not_consulted(self):
        feature = self.feature()
        self.epoch = lambda path, incident, status: self.fail('no incident: no epoch check')
        self.assertFalse(fa.state_recovery_required(feature, access=self.access))

    def test_the_library_snapshot_check_refuses_before_the_hook(self):
        """The hook cannot override the library's own integrity check."""
        feature = self.feature()
        self.incident = {'incident_id': 'i-1'}
        calls = []
        self.epoch = lambda path, incident, status: calls.append(status) or False
        for status in ('invalid', 'state-missing', 'state-mismatch'):
            with self.subTest(status), mock.patch.object(fa, 'state_snapshot_status', return_value=status):
                self.assertTrue(fa.state_recovery_required(feature, access=self.access))
        self.assertEqual([], calls)

    def test_the_incident_lookup_runs_first_and_may_refuse(self):
        feature = self.feature()
        order = []

        def incident(path):
            order.append('incident')
            f.die('STATE_RECOVERY_INCIDENT_INVALID: test')
        access = dataclasses.replace(self.access, recovery_incident=incident)
        with mock.patch.object(fa, 'state_snapshot_status', side_effect=lambda *a, **k: order.append('snapshot')):
            code, err = refused(lambda: fa.state_recovery_required(feature, access=access))
        self.assertEqual((2, ['incident']), (code, order))
        self.assertIn('STATE_RECOVERY_INCIDENT_INVALID', err)

    def test_a_required_recovery_blocks_loading(self):
        feature = self.feature()
        self.incident = {'incident_id': 'i-1'}
        self.epoch = lambda path, incident, status: True
        code, err = refused(lambda: fa.load_state(feature, self.doc(feature), access=self.access))
        self.assertEqual(2, code)
        self.assertIn('STATE_RECOVERY_REQUIRED', err)
        self.assertEqual([], self.saved)

    def test_the_binding_has_no_defaults(self):
        with self.assertRaises(TypeError):
            fa.StateAccess()
        with self.assertRaises(TypeError):
            fa.state_recovery_required(self.feature())


class StateAccessTest(AccessCase):
    def test_fresh_state_is_not_written_before_the_operation(self):
        feature = self.feature()
        state = fa.load_state(feature, self.doc(feature), access=self.access)
        self.assertEqual({'T-001': {'status': 'pending', 'attempts': 0}, 'T-900': {'status': 'pending', 'attempts': 0}},
                         state['tasks'])
        self.assertEqual(f.feature_fingerprint(feature), state['fingerprint'])
        self.assertEqual([], self.saved)
        self.assertFalse(fs.state_path(feature, policy=self.policy).exists())

    def test_locked_state_commits_once_through_the_bound_writer_and_fault_injector(self):
        feature = self.feature()
        boundaries = []
        self.injector = boundaries.append
        with fa.locked_state(feature, self.doc(feature), access=self.access) as state:
            state['tasks']['T-001']['status'] = 'ready'
            fa._authority_transaction.current['post_commit_failpoints'].append('after-commit')
        self.assertEqual(1, len(self.saved))
        self.assertEqual('ready', self.saved[0][0]['tasks']['T-001']['status'])
        self.assertEqual(['after-commit'], boundaries)
        self.assertIsNone(getattr(fa._authority_transaction, 'current', None))
        again = fa.load_state(feature, self.doc(feature), access=self.access)
        self.assertEqual('ready', again['tasks']['T-001']['status'])

    def test_a_failure_inside_the_lock_writes_nothing(self):
        feature = self.feature()
        with self.assertRaises(RuntimeError), fa.locked_state(feature, self.doc(feature), access=self.access) as state:
            state['tasks']['T-001']['status'] = 'ready'
            raise RuntimeError('operation failed')
        self.assertEqual([], self.saved)
        self.assertFalse(fs.state_path(feature, policy=self.policy).exists())

    def test_a_verification_owned_repository_refuses_with_its_exit_code(self):
        feature = self.feature()
        owned = types.SimpleNamespace(StoreError=library_store.StoreError,
                                      repository_lock=library_store.repository_lock,
                                      repository_lock_held=lambda root: True,
                                      VerificationStore=library_store.VerificationStore)
        access = dataclasses.replace(self.access, verification_store=lambda: owned)
        code, err = refused(lambda: fa.locked_state(feature, self.doc(feature), access=access).__enter__())
        self.assertEqual(6, code)
        self.assertIn('VERIFICATION_OWNERSHIP_BLOCKED: verification-owned', err)

    def test_the_lock_refuses_without_cross_process_locking(self):
        feature = self.feature()
        access = dataclasses.replace(self.access, locking=None)
        _code, err = refused(lambda: fa.load_state(feature, self.doc(feature), access=access))
        self.assertIn('LIFECYCLE_LOCK_UNAVAILABLE', err)

    def test_a_changed_feature_refuses_the_stored_state(self):
        feature = self.feature()
        with fa.locked_state(feature, self.doc(feature), access=self.access) as state:
            state['tasks']['T-001']['status'] = 'ready'
        (feature / 'plan.md').write_text('# changed plan\n')
        _code, err = refused(lambda: fa.load_state(feature, self.doc(feature), access=self.access))
        self.assertIn('fingerprint differs', err)

    def test_completion_records_are_read_and_checked(self):
        feature = self.feature()
        common = self.access.common_dir(feature)
        record = {'schema_version': 1, 'record_type': 'completion', 'repository_id': str(common),
                  'feature': 'TST-001', 'task': 'T-001', 'attempt': 1, 'checkpoint': 'c' * 40,
                  'packet_revision': 'sha256:' + 'a' * 64, 'contract_fingerprint': 'b' * 64,
                  'evidence_reference_sha256': 'd' * 64, 'correction_id': None, 'repair_authorization_id': None,
                  'created_at': '2026-10-23T00:00:00Z'}
        record['record_id'] = fa.completion_record_id('completion', record)
        directory = fa.completion_authority_dir(feature, 'completion-records', 'T-001', access=self.access)
        directory.mkdir(parents=True)
        path = directory / f"{record['record_id'].rsplit(':', 1)[-1]}.json"
        path.write_text(json.dumps(record))
        self.assertEqual([record], fa.completion_records(feature, 'T-001', access=self.access))
        state = fa.initial_state(feature, self.doc(feature), access=self.access)
        self.assertEqual(('completed', 1, record['record_id']),
                         tuple(state['tasks']['T-001'][k] for k in ('status', 'attempts', 'completion_record_id')))
        path.write_text(json.dumps({**record, 'task': 'T-900'}))
        _code, err = refused(lambda: fa.completion_records(feature, 'T-001', access=self.access))
        self.assertIn('COMPLETION_AUTHORITY_INVALID', err)
        _code, err = refused(lambda: fa.completion_authority_dir(feature, 'elsewhere', 'T-001', access=self.access))
        self.assertIn('unsafe authority path', err)


if __name__ == '__main__':
    unittest.main()
