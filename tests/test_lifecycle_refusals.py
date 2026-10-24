"""AH5-14b-2 step 1: each refusal of the read-only lifecycle checks, and the accepted-plan resolver's bindings.

Complements `test_lifecycle_checks`; Showcase's suites exercise the checks inside the transitions through its shim.
"""
import contextlib
import dataclasses
import io
import json
import types
import unittest

from test_feature_access import AccessCase

from agent_harness import lifecycle_checks as lc
from agent_harness.verification import store as library_store


def refused(call):
    with contextlib.redirect_stderr(io.StringIO()) as err, unittest.TestCase().assertRaises(SystemExit) as raised:
        call()
    return raised.exception.code, err.getvalue()


def resume(**changes):
    item = {'plan_id': 'plan-1', 'task_attempt': 2, 'lifecycle_generation': 1, 'reason_sha256': 'a' * 64,
            'requested_at': '2026-10-24T00:00:00+00:00', 'operator': 'op', 'prior_result_sha256': 'b' * 64,
            'prior_result_path': 'results/r.json'}
    item.update(changes)
    return item


class ResumeRefusalTest(unittest.TestCase):
    def entry(self, active, history=None):
        return {'attempts': 2, 'verification_resume_active': active,
                'verification_resume_history': [active] if history is None else history}

    def test_a_consistent_resume_is_returned_and_none_means_no_resume(self):
        item = resume()
        self.assertEqual(item, lc.validate_active_verification_resume(self.entry(item)))
        self.assertIsNone(lc.validate_pending_verification_resume(self.entry(item)))
        self.assertIsNone(lc.validate_verification_resume({'attempts': 1}, 'verification_resume_active'))

    def test_each_inconsistency_refuses(self):
        cases = {
            'no history': self.entry(resume(), history=[]),
            'differs from history': self.entry(resume(), history=[resume(operator='other')]),
            'wrong attempt': {**self.entry(resume()), 'attempts': 3},
            'bad reason digest': self.entry(resume(reason_sha256='x')),
            'blank operator': self.entry(resume(operator=' ')),
            'naive timestamp': self.entry(resume(requested_at='2026-10-24T00:00:00')),
        }
        for name, entry in cases.items():
            with self.subTest(name):
                code, err = refused(lambda entry=entry: lc.validate_active_verification_resume(entry))
                self.assertEqual(2, code)
                self.assertIn('VERIFICATION_RESUME_STATE_INVALID', err)
                self.assertEqual(entry, json.loads(json.dumps(entry)))  # the input is left as it was


class IdentityRefusalTest(unittest.TestCase):
    def packet(self):
        packet = {'feature': 'F', 'task': 'T-001', 'body': [1, 2]}
        packet['packet_sha256'] = lc.canonical_packet_payload_sha256(packet)
        return packet

    def test_partial_claim(self):
        claim = {'status': 'running', 'attempts': 4, 'active_retry_authorization': 'grant-1'}
        self.assertTrue(lc.is_unrecovered_partial_claim(claim))
        for change in ({'status': 'ready'}, {'attempts': 3}, {'active_retry_authorization': None},
                       {'claim_recovery': {'done': True}}):
            with self.subTest(change=change):
                self.assertFalse(lc.is_unrecovered_partial_claim({**claim, **change}))

    def test_recovery_binding_refusals(self):
        packet = self.packet()
        recovery = {'recovery_version': 1, 'packet_identity': packet['packet_sha256'],
                    'semantic_contract_sha256': 'c' * 64}
        revision = lc.packet_revision_id(packet)
        self.assertTrue(lc.recovery_proves_packet_binding(recovery, packet, revision, 'c' * 64))
        for name, args in {
            'boolean version': ({**recovery, 'recovery_version': True}, packet, revision, 'c' * 64),
            'unknown version': ({**recovery, 'recovery_version': 2}, packet, revision, 'c' * 64),
            'changed packet': (recovery, {**packet, 'body': [3]}, revision, 'c' * 64),
            'other revision': (recovery, packet, 'sha256:' + 'e' * 64, 'c' * 64),
            'other contract': (recovery, packet, revision, 'd' * 64),
        }.items():
            with self.subTest(name):
                self.assertFalse(lc.recovery_proves_packet_binding(*args))
        self.assertEqual(f"sha256:{lc.canonical_json_sha256(recovery)}", lc.recovery_record_identity(recovery))
        self.assertIsNone(lc.recovery_record_identity('x'))


class AcceptedPlanBindingTest(AccessCase):
    def records(self):
        keys = ('schema_version', 'profile_id', 'plan_id', 'task_id', 'task_attempt', 'feature_fingerprint',
                'lifecycle_generation', 'family', 'profile_hash', 'policy_checkpoint', 'candidate_identity',
                'final_changed_surface_id', 'origin_binding')
        record = {key: key for key in keys}
        record.update({'plan_id': 'plan-1', 'task_id': 'T-001', 'task_attempt': 1, 'feature_fingerprint': 'f' * 64,
                       'lifecycle_generation': 2, 'feature_id': 'TST-001'})
        state = {'feature_generation': 2, 'tasks': {'T-001': {'status': 'running', 'attempts': 1}},
                 'verification_authority': {'accepted_plan_id': 'plan-1', 'generation': 2,
                                            'binding': {key: record[key] for key in keys}}}
        return record, state

    def resolve(self, state, record, **changes):
        seen = {}

        class Store:
            def __init__(self, repository):
                seen['store'] = repository

            def load_plan_record(self, plan_id):
                return record
        authority = types.SimpleNamespace(validate_plan_record=lambda rec, **kw: seen.setdefault('validated', kw))
        access = dataclasses.replace(
            self.access, verification_store=lambda: types.SimpleNamespace(VerificationStore=Store,
                                                                         StoreError=library_store.StoreError),
            load_validated=lambda path: seen.setdefault('feature_dir', path) and {'feature': 'TST-001'},
            read_state_pure=lambda path, doc: state, fingerprint=lambda path: 'f' * 64)
        result = lc.resolve_accepted_verification_plan(
            self.root, 'plan-1', access=access, verification_authority=lambda: authority,
            feature_dir_for=lambda root, name: root / 'specs-elsewhere' / name, **changes)
        return result, seen

    def test_the_bindings_are_used(self):
        record, state = self.records()
        result, seen = self.resolve(state, record)
        self.assertIs(record, result)
        self.assertEqual(self.root / 'specs-elsewhere' / 'TST-001', seen['feature_dir'])
        self.assertEqual({'repository': self.root, 'reconstruct': True}, seen['validated'])

    def test_any_stale_acceptance_is_unavailable(self):
        record, state = self.records()
        for current in ({**state, 'verification_authority': None}, {**state, 'feature_generation': 3},
                        {**state, 'tasks': {'T-001': {'status': 'completed', 'attempts': 1}}},
                        {**state, 'tasks': {'T-001': {'status': 'running', 'attempts': 2}}},
                        {**state, 'verification_authority': {**state['verification_authority'], 'binding': {}}}):
            with self.subTest(current=current), \
                    self.assertRaisesRegex(library_store.StoreError, 'ACCEPTED_PLAN_UNAVAILABLE'):
                self.resolve(current, record)
        with self.assertRaisesRegex(library_store.StoreError, 'ACCEPTED_PLAN_UNAVAILABLE'):
            self.resolve(state, {**record, 'feature_fingerprint': 'e' * 64})
        changed = {**record, 'feature_fingerprint': 'e' * 64}  # the binding agrees; only the feature has changed
        current = {**state, 'verification_authority': {**state['verification_authority'], 'binding': {
            **state['verification_authority']['binding'], 'feature_fingerprint': 'e' * 64}}}
        with self.assertRaisesRegex(library_store.StoreError, 'ACCEPTED_PLAN_UNAVAILABLE'):
            self.resolve(current, changed)
        result, _ = self.resolve({**state, 'tasks': {'T-001': {'status': 'escalated', 'attempts': 1}}}, record,
                                 allowed_task_statuses={'escalated'})
        self.assertIs(record, result)


if __name__ == '__main__':
    unittest.main()
