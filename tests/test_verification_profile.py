import itertools
import json
from importlib import resources
from unittest import mock
import unittest

from agent_harness.verification.model import Artifact, Gate, InvalidPolicy, Probe
from agent_harness.verification.profile import load_profile, matches
from agent_harness.verification.serialization import SafetyPolicy, safe_record


def profile(*gates, safety=None):
    return load_profile({'schema_version': 1, 'gates': list(gates)}, safety=safety)


def gate(id='unit', **changes):
    return dict({'id': id, 'command': 'python3 -m unittest', 'inputs': ['src/**'],
                 'mandatory': True, 'cacheable': True}, **changes)


class ProfileTest(unittest.TestCase):
    def test_profile_schema_fields_match_runtime_gate_loader(self):
        schema_path = resources.files('agent_harness') / 'schemas' / 'verification-profile.schema.json'
        schema = json.loads(schema_path.read_text(encoding='utf-8'))
        runtime_fields = set(Gate.__dataclass_fields__) - {'command_hash'}
        gate_schema = schema['properties']['gates']['items']
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual({'schema_version', 'gates'}, set(schema['properties']))
        self.assertEqual({'schema_version', 'gates'}, set(schema['required']))
        self.assertEqual(1, schema['properties']['schema_version']['const'])
        self.assertFalse(gate_schema['additionalProperties'])
        self.assertEqual(runtime_fields, set(gate_schema['properties']))
        self.assertEqual({'id', 'command', 'inputs'}, set(gate_schema['required']))
        probes = gate_schema['properties']['probes']['items']
        self.assertFalse(probes['additionalProperties'])
        self.assertEqual(set(Probe.__dataclass_fields__), set(probes['properties']))
        self.assertEqual({'id', 'format'}, set(probes['required']))
        consumes = gate_schema['properties']['consumes']['items']
        self.assertFalse(consumes['additionalProperties'])
        self.assertEqual(set(Artifact.__dataclass_fields__), set(consumes['properties']))
        self.assertEqual({'producer', 'path'}, set(consumes['required']))

        with self.assertRaises(InvalidPolicy):
            profile(gate(obsolete_policy_field=True))

    def test_glob_table(self):
        for pattern, path, expected in [
            ('src/**/x?.py', 'src/x1.py', True), ('src/**/x?.py', 'src/a/b/x1.py', True),
            ('src/*', 'src/a/b', False), ('src/*', 'src/', False),
            ('**', 'a b/雪.py', True), ('*.py', 'A.PY', False),
            ('a/**', 'a', True), ('a**b', 'ab', False),
        ]:
            with self.subTest(pattern=pattern, path=path):
                if pattern == 'a**b':
                    with self.assertRaises(InvalidPolicy): matches(pattern, path)
                else: self.assertEqual(expected, matches(pattern, path))
        for bad in ['/a', '../a', 'a/../b', 'a\\b', '[ab]', '{a,b}', 'a//b']:
            with self.subTest(bad=bad), self.assertRaises(InvalidPolicy): matches(bad, 'a')

    def test_reject_ambiguous_and_cyclic_profiles(self):
        for gates in [(gate(), gate()), (gate('a'), gate('b')),
                      (gate('a', depends_on=['b']), gate('b', command='python3 -V', depends_on=['a'])),
                      (gate(depends_on=['absent']),), (gate(unknown=True),),
                      (gate(probes={}),), (gate(consumes={}),), (gate(cacheable='true'),)]:
            with self.subTest(gates=gates), self.assertRaises(InvalidPolicy): profile(*gates)

    def test_command_and_cwd_are_exact(self):
        p = profile(gate('a'), gate('b', command='python3  -m unittest'),
                    gate('c', cwd='sub'))
        self.assertEqual(3, len({g.command_hash for g in p.gates}))

    def test_invalid_artifact_overlap_and_producer(self):
        for gates in [(gate(produces=['src/result']),),
                      (gate(consumes=[{'producer': 'absent', 'path': 'out/data'}]),),
                      (gate(produces=['../escape']),), (gate(inputs=['out/data/**'], produces=['out/data']),)]:
            with self.assertRaises(InvalidPolicy): profile(*gates)

    def test_adversarial_globs_use_bounded_segment_matching(self):
        from agent_harness.verification import profile as matching
        # Historical RED: 16 repetitions exceeded two seconds. Prevent regex
        # dispatch as a deterministic oracle instead of a wall-clock deadline.
        with mock.patch.object(matching.re, 'compile', side_effect=AssertionError('regex glob')):
            for n in (16, 20, 24, 64):
                self.assertFalse(matches('*a' * n + 'b', 'a' * (2 * n)))
                self.assertTrue(matches('*a' * n + 'b', 'a' * (2 * n) + 'b'))

    def test_segment_match_semantics_exhaustively(self):
        from functools import lru_cache
        @lru_cache(None)
        def reference(pattern, text):
            if not pattern: return not text
            if pattern[0] == '*':
                return reference(pattern[1:], text) or bool(text and reference(pattern, text[1:]))
            return bool(text and (pattern[0] == '?' or pattern[0] == text[0])
                        and reference(pattern[1:], text[1:]))
        for size in range(1, 5):
            for parts in itertools.product('ab*?', repeat=size):
                pattern = ''.join(parts)
                if '**' in pattern: continue
                for length in range(1, 5):
                    for letters in itertools.product('ab', repeat=length):
                        text = ''.join(letters)
                        self.assertEqual(reference(pattern, text), matches(pattern, text), (pattern, text))

    def test_safe_boundary(self):
        safety = SafetyPolicy(('CANARY-secret-12345',))
        for field in ['command', 'id', 'description']:
            with self.subTest(field=field), self.assertRaises(InvalidPolicy):
                profile(gate(**{field: 'CANARY-secret-12345'}), safety=safety)
        for value in [{'stdout': 'anything'}, {'usage': {'evil': 'CANARY-secret-12345'}},
                      {'reason': 'CANARY-secret-12345'}, {'command': 'python3 -V'}]:
            with self.assertRaises(ValueError): safe_record(value, safety=safety)
        self.assertEqual({'status': 'pass', 'duration_seconds': 1.2},
                         safe_record({'status': 'pass', 'duration_seconds': 1.2}, safety=safety))


if __name__ == '__main__': unittest.main()
