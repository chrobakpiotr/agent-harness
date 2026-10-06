"""Showcase-side bindings of the agent-harness cutover wrapper (AH5-06). Replaces the moved-module authority
tests that patched Showcase names: the library tests the behaviour, this tests what Showcase passes in."""
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

HARNESS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

import harness
import trust
from verification import authority, model, store

from agent_harness.verification import authority as library_authority
from agent_harness.verification import model as library_model
from agent_harness.verification import store as library_store

PROFILES = HARNESS / 'verification-profiles'


class WrapperBindingTest(unittest.TestCase):
    def test_moved_modules_are_the_library(self):
        self.assertIs(model, library_model)
        self.assertIs(trust.classify_path, sys.modules['trust'].classify_path)
        self.assertIs(sys.modules['verification.model'], library_model)

    def test_store_binds_showcase_registry_and_profiles(self):
        with mock.patch.object(library_store, 'resolve_control_root', return_value=(HARNESS / 'x', 'id')):
            bound = store.VerificationStore(HARNESS)
        self.assertEqual(HARNESS / 'human-issuer-registry.json', bound.issuer_registry)
        self.assertEqual(PROFILES, bound.profile_root)
        self.assertIsInstance(bound, library_store.VerificationStore)

    def test_authority_binds_lifecycle_profiles_and_showcase_profile(self):
        calls = {}
        names = ('publish_and_accept', 'resolve_accepted', 'resolve_execution', 'prepare_task_plan',
                 'validate_plan_record')
        patches = [mock.patch.object(library_authority, name,
                                     side_effect=lambda *a, _n=name, **k: calls.setdefault(_n, k))
                   for name in names]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        authority.publish_and_accept('r', 'f', {}, expected_generation=3)
        authority.resolve_accepted('r', 'p')
        authority.resolve_execution('r', 'p', unit_id='u')
        authority.prepare_task_plan('r', 'f', 'T-001', 1, 'b', [])
        authority.validate_plan_record({}, repository='r', reconstruct=True)
        for name in names:
            with self.subTest(name=name):
                if name != 'validate_plan_record':
                    self.assertIs(harness, calls[name]['lifecycle'])
                if name not in ('resolve_accepted',):
                    self.assertEqual(PROFILES, calls[name]['profile_root'])
        self.assertEqual('showcase', calls['prepare_task_plan']['profile_id'])
        self.assertEqual(3, calls['publish_and_accept']['expected_generation'])
        self.assertEqual('u', calls['resolve_execution']['unit_id'])
        self.assertTrue(calls['validate_plan_record']['reconstruct'])

    def test_trust_cli_runs_the_library_cli(self):
        out = subprocess.run([sys.executable, str(HARNESS / 'trust.py'), 'classify', '.agent-runs/x.log'],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn('untrusted', out)


if __name__ == '__main__':
    unittest.main()
