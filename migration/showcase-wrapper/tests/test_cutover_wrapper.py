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
from verification import authority, model, store

from agent_harness.verification import authority as library_authority
from agent_harness.verification import model as library_model
from agent_harness.verification import store as library_store

PROFILES = HARNESS / 'verification-profiles'


ALIASES = ('trust', 'machine_outcomes', 'verification.model', 'verification.serialization', 'verification.profile',
           'verification.fingerprint', 'verification.planner', 'verification.candidate', 'verification.human_grants')


class WrapperBindingTest(unittest.TestCase):
    def test_aliased_modules_are_the_library_modules(self):
        for name in ALIASES:
            with self.subTest(name=name):
                self.assertIs(importlib.import_module(name), importlib.import_module('agent_harness.' + name))
        self.assertIs(model, library_model)

    def test_store_binds_showcase_defaults_and_passes_explicit_values(self):
        with mock.patch.object(library_store, 'resolve_control_root', return_value=(HARNESS / 'x', 'id')):
            bound = store.VerificationStore(HARNESS)
        self.assertEqual(HARNESS / 'human-issuer-registry.json', bound.issuer_registry)
        self.assertEqual(PROFILES, bound.profile_root)
        self.assertIsInstance(bound, library_store.VerificationStore)
        explicit = store.VerificationStore(HARNESS, control_root=HARNESS / 'c', issuer_registry=HARNESS / 'r.json',
                                           profile_root=HARNESS / 'p')
        self.assertEqual((HARNESS / 'c', HARNESS / 'r.json', HARNESS / 'p'),
                         (explicit.root, explicit.issuer_registry, explicit.profile_root))

    def test_authority_passes_arguments_through_and_binds_showcase(self):
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
            'resolve_execution': (('repo', 'plan'), {'lifecycle': harness, 'profile_root': PROFILES, 'unit_id': 'u'}),
            'prepare_task_plan': (('repo', 'feature', 'T-001', 2, 'base', ['cmd']),
                                  {'lifecycle': harness, 'profile_root': PROFILES, 'profile_id': 'showcase'}),
            'validate_plan_record': ((record,), {'profile_root': PROFILES, 'repository': 'repo', 'reconstruct': True}),
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

    def test_trust_cli_runs_the_library_cli(self):
        out = subprocess.run([sys.executable, str(HARNESS / 'trust.py'), 'classify', '.agent-runs/x.log'],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn('untrusted', out)


if __name__ == '__main__':
    unittest.main()
