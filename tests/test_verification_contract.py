"""Ported from Showcase `tooling/agent-harness/tests/test_verification_contract.py` at `fd96eeb` (AH5-13).

The four source tests set the module's `REPO`; here the bindings are explicit. The other tests cover the seams and
further validation rules; the packet lists the mutations they catch, a selection and not an exhaustive list.
"""
import hashlib
import json
import pathlib
import subprocess
import tempfile
import unittest

from agent_harness import verification_contract as vc

CONSTITUTION = 'docs/agentic-sdd/constitution.md'


class VerificationContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        (self.root / 'docs/agentic-sdd').mkdir(parents=True)
        (self.root / CONSTITUTION).write_text('# constitution\n')
        self.feature = self.root / 'docs/specs/VC-TEST-001'
        self.feature.mkdir(parents=True)
        (self.feature / 'spec.md').write_text('# spec\nRisk: high\n- AC-001: works\n')
        (self.feature / 'plan.md').write_text('# plan\n')
        (self.feature / 'design.json').write_text(json.dumps({'verification_contract': 'required'}))
        (self.feature / 'design').mkdir()
        (self.feature / 'design/gate.json').write_text('{}')
        self.binding_args = {'repo_root': lambda: self.root, 'repository': self.root, 'constitution': CONSTITUTION}

    def input_hashes(self):
        return vc.input_hashes(self.feature, **self.binding_args)

    def validate(self, **kwargs):
        return vc.validate_contract(self.feature, **{**self.binding_args, 'is_active': lambda p: True, **kwargs})

    def binding(self, **kwargs):
        return vc.contract_binding(self.feature, **{**self.binding_args, 'is_active': lambda p: True, **kwargs})

    def contract(self):
        return {
            'schema_version': 1, 'feature': 'VC-TEST-001', 'status': 'accepted', 'inputs': self.input_hashes(),
            'criteria': [{'id': 'VC-001', 'statement': 'Preserve invariant', 'origin': 'independent',
                          'source_type': 'architecture-invariant', 'sources': ['constitution'],
                          'verification_hint': 'architecture test'}],
            'exemptions': [], 'summary': 'ok', 'assumptions': []}

    def write(self, doc):
        (self.feature / 'verification-contract.json').write_text(json.dumps(doc))

    # -- ported (source: test_verification_contract.py) --

    def test_required_contract_must_exist(self):
        self.assertIn('missing required verification-contract.json', self.validate())

    def test_valid_required_contract_has_independent_criterion(self):
        self.write(self.contract())
        self.assertEqual([], self.validate())
        self.assertEqual({'VC-001'}, vc.criterion_ids(self.feature))

    def test_contract_becomes_stale_when_plan_changes(self):
        self.write(self.contract())
        (self.feature / 'plan.md').write_text('# changed\n')
        self.assertTrue(any('stale' in e for e in self.validate()))

    def test_agent_cannot_self_accept_exemption(self):
        doc = self.contract()
        doc['exemptions'] = [{'id': 'VX-001', 'scope': 'load', 'reason': 'expensive', 'status': 'accepted',
                              'approved_by': 'verification-author'}]
        self.write(doc)
        self.assertTrue(any('human' in e for e in self.validate()))

    # -- seams --

    def test_the_constitution_comes_from_the_bound_root_and_path(self):
        """[seam] Before the contract exists it is read from `repository`, afterwards from `repo_root()`."""
        other = self.root / 'other'
        (other / 'docs/agentic-sdd').mkdir(parents=True)
        (other / CONSTITUTION).write_text('# other constitution\n')
        before = vc.input_hashes(self.feature, repo_root=lambda: self.fail('no contract: no root lookup'),
                                 repository=other, constitution=CONSTITUTION)
        self.assertEqual(vc.sha(other / CONSTITUTION), before['constitution_sha256'])
        self.write(self.contract())
        after = vc.input_hashes(self.feature, repo_root=lambda: self.root, repository=other, constitution=CONSTITUTION)
        self.assertEqual(vc.sha(self.root / CONSTITUTION), after['constitution_sha256'])
        (self.root / 'rules.md').write_text('# moved\n')
        moved = vc.input_hashes(self.feature, repo_root=lambda: self.root, repository=other, constitution='rules.md')
        self.assertEqual(vc.sha(self.root / 'rules.md'), moved['constitution_sha256'])

    def test_a_changed_constitution_or_design_record_makes_the_contract_stale(self):
        for name in (CONSTITUTION, 'docs/specs/VC-TEST-001/spec.md', 'docs/specs/VC-TEST-001/design/gate.json'):
            with self.subTest(name):
                self.write(self.contract())
                original = (self.root / name).read_text()
                (self.root / name).write_text(original + 'changed\n')
                self.assertTrue(any('stale' in e for e in self.validate()))
                (self.root / name).write_text(original)
        (self.feature / 'design/master-closure.json').write_text('{}')
        self.assertIn('master_closure_sha256', self.input_hashes())
        self.write(self.contract())
        (self.feature / 'design/master-closure.json').write_text('{"changed": true}')
        self.assertIn('verification contract is stale: inputs.master_closure_sha256 does not match current artifact',
                      self.validate())

    def test_missing_or_empty_inputs_are_stale(self):
        for inputs in ({}, None):
            with self.subTest(inputs=inputs):
                doc = self.contract()
                if inputs is None:
                    del doc['inputs']
                else:
                    doc['inputs'] = inputs
                self.write(doc)
                errors = self.validate()
                for key in ('spec_sha256', 'plan_sha256', 'constitution_sha256', 'design_gate_sha256'):
                    self.assertIn(f'verification contract is stale: inputs.{key} does not match current artifact',
                                  errors)

    def test_currency_that_cannot_be_established_fails_closed(self):
        """F1 (replaces the source's fail-open skip): no repository root, or an input that cannot be read, is an error
        and never a missing input; the binding is withheld."""
        self.write(self.contract())
        unavailable = 'verification contract currency cannot be established: repository root unavailable'
        for root in (lambda: vc.die('no repository'), lambda: (_ for _ in ()).throw(OSError('gone'))):
            with self.subTest(root=root):
                self.assertEqual(([unavailable], None), self.binding(repo_root=root))
        (self.feature / 'plan.md').unlink()
        (self.feature / 'plan.md').mkdir()  # exists, cannot be read as a file
        errors, binding = self.binding()
        self.assertIsNone(binding)
        self.assertEqual(1, len(errors))
        self.assertRegex(errors[0], r'^verification contract currency cannot be established: plan_sha256 unreadable '
                                    r'\((IsADirectoryError|PermissionError)\)$')
        (self.feature / 'plan.md').rmdir()
        self.assertIn('verification contract currency cannot be established: plan_sha256 unreadable '
                      '(FileNotFoundError)', self.validate())

    def test_an_unconfirmed_repository_is_its_own_error(self):
        self.write(self.contract())

        def unconfirmed():
            raise vc.RepositoryUnconfirmed('feature is not in the repository')
        self.assertEqual((['verification contract currency cannot be established: repository identity unconfirmed '
                           '(feature is not in the repository)'], None), self.binding(repo_root=unconfirmed))

    def test_an_unreadable_contract_is_not_an_absent_one(self):
        (self.feature / 'verification-contract.json').mkdir()
        errors, binding = self.binding(require=False)
        self.assertIsNone(binding)
        self.assertRegex(errors[0], r'^verification-contract.json unreadable \(')

    def test_a_removed_design_input_is_not_stale_but_changes_the_binding(self):
        """As the source, validation does not compare a design input that is absent (Showcase validates scratch copies
        without one); the binding records the absence, so a removal after `start` still refuses `complete`."""
        self.write(self.contract())
        before = self.binding()[1]
        (self.feature / 'design/gate.json').unlink()
        errors, after = self.binding()
        self.assertEqual([], errors)
        self.assertEqual({'present': False}, after['inputs']['design_gate_sha256'])
        self.assertNotEqual(before, after)

    def test_the_binding_comes_from_the_validated_bytes_and_names_every_input(self):
        self.write(self.contract())
        errors, binding = self.binding()
        self.assertEqual([], errors)
        digests = self.input_hashes()
        contract_sha = hashlib.sha256((self.feature / 'verification-contract.json').read_bytes()).hexdigest()
        self.assertEqual({'version': 1, 'contract': {'present': True, 'sha256': contract_sha}, 'inputs': {
            'spec_sha256': {'present': True, 'sha256': digests['spec_sha256']},
            'plan_sha256': {'present': True, 'sha256': digests['plan_sha256']},
            'constitution_sha256': {'present': True, 'sha256': digests['constitution_sha256']},
            'design_gate_sha256': {'present': True, 'sha256': digests['design_gate_sha256']},
            'master_closure_sha256': {'present': False}}}, binding)
        self.assertEqual(vc.INPUT_KEYS, tuple(binding['inputs']))
        # Any change to the contract or an input, valid or not, changes the binding or withholds it.
        doc = self.contract()
        doc['summary'] = 'reworded'
        self.write(doc)
        self.assertNotEqual(binding, self.binding()[1])
        self.write(self.contract())
        self.assertEqual(binding, self.binding()[1])
        (self.feature / 'spec.md').write_text('# spec\nRisk: high\n- AC-001: changed\n')
        self.assertEqual((None,), self.binding()[1:])
        doc = self.contract()  # re-authored for the new spec
        self.write(doc)
        changed = self.binding()[1]
        self.assertNotEqual(binding['inputs']['spec_sha256'], changed['inputs']['spec_sha256'])

    def test_a_design_config_that_cannot_be_read_is_not_an_absent_one(self):
        """F1: `Path.exists()` reports False for a symlink loop; requiredness must not fall back to 'optional'."""
        config = self.feature / 'design.json'
        config.unlink()
        config.symlink_to(config)  # ELOOP on read
        errors, binding = self.binding()
        self.assertIsNone(binding)
        self.assertRegex(errors[0], r'^verification contract requirement cannot be established: design.json '
                                    r'unreadable \(OSError\)$')
        with self.assertRaises(OSError):
            vc.requirement(self.feature)

    def test_the_binding_records_an_absent_contract(self):
        self.assertEqual(([], {'version': 1, 'contract': {'present': False}}),
                         self.binding(require=False))
        self.assertEqual((['missing required verification-contract.json'], None), self.binding())
        self.assertEqual([], self.validate(require=False))

    def test_requiredness_follows_the_bound_layout_and_the_explicit_override(self):
        """[seam] `is_active` decides whether `required` applies; `require=` overrides both ways."""
        self.assertEqual([], self.validate(is_active=lambda p: False))
        self.assertIn('missing required verification-contract.json', self.validate(is_active=lambda p: False,
                                                                                   require=True))
        self.assertEqual([], self.validate(require=False))
        doc = self.contract()  # only a required contract needs an independent criterion
        doc['criteria'][0]['origin'] = 'spec-derived'
        self.write(doc)
        self.assertEqual([], self.validate(require=False))
        (self.feature / 'verification-contract.json').unlink()
        seen = []
        self.validate(is_active=lambda p: seen.append(p) or True)
        self.assertEqual([self.feature], seen)

    # -- further rules --

    def test_document_level_rules(self):
        cases = {
            'design.json.verification_contract must be required/optional/off':
                lambda d: (self.feature / 'design.json').write_text(json.dumps({'verification_contract': 'maybe'})),
            'verification-contract.json schema_version must be 1': lambda d: d.update(schema_version=2),
            'verification-contract.json feature must match feature directory': lambda d: d.update(feature='X'),
            'verification-contract.json status must be accepted': lambda d: d.update(status='proposed'),
            'verification-contract.json criteria must be non-empty': lambda d: d.update(criteria=[]),
            'verification-contract.json exemptions must be an array': lambda d: d.update(exemptions={}),
            'verification criterion ids must be unique': lambda d: d['criteria'].append(dict(d['criteria'][0])),
            'required verification contract must contain at least one independent criterion':
                lambda d: d['criteria'][0].update(origin='spec-derived'),
        }
        for message, change in cases.items():
            with self.subTest(message):
                (self.feature / 'design.json').write_text(json.dumps({'verification_contract': 'required'}))
                doc = self.contract()
                change(doc)
                self.write(doc)
                self.assertIn(message, self.validate())

    def test_unparsable_or_non_object_contracts_are_refused(self):
        (self.feature / 'verification-contract.json').write_text('{not json')
        self.assertTrue(self.validate()[0].startswith('invalid JSON in verification-contract.json'))
        self.assertEqual(set(), vc.criterion_ids(self.feature))
        self.write([1])
        self.assertEqual(['verification-contract.json must contain an object'], self.validate())

    def test_each_criterion_field_is_checked(self):
        cases = {
            'criteria[0] must be an object': 'x',
            'criteria[0].id must start with VC-': {'id': 'AC-1'},
            'criteria[0].statement must be non-empty': {'statement': ' '},
            'criteria[0].origin must be spec-derived/independent': {'origin': 'agent'},
            'criteria[0].source_type is invalid': {'source_type': 'vibes'},
            'criteria[0].sources must be a non-empty string array': {'sources': ['ok', ' ']},
            'criteria[0].verification_hint must be non-empty': {'verification_hint': ''},
        }
        for message, change in cases.items():
            with self.subTest(message):
                doc = self.contract()
                doc['criteria'] = [change] if isinstance(change, str) else [{**doc['criteria'][0], **change}]
                self.write(doc)
                self.assertIn(message, self.validate())

    def test_exemptions_need_a_unique_id_a_final_status_and_a_human_approver(self):
        def exemption(**fields):  # the schema's fields: id, scope, reason, status, approved_by
            return {'id': 'VX-001', 'scope': 'load', 'reason': 'expensive', 'status': 'rejected', **fields}
        cases = {
            'exemptions[0] must be an object': ['x'],
            'exemptions[0].id must be a unique VX-* id': [exemption(id='X-1')],
            'exemptions[1].id must be a unique VX-* id': [exemption(), exemption()],
            'exemptions[0] is still proposed; a human must explicitly accept/reject it': [exemption(status='proposed')],
            'exemptions[0].status is invalid': [exemption(status='waived')],
            'exemptions[0] accepted exemption requires approved_by=human or human:<name>':
                [exemption(status='accepted', approved_by='humanoid')],
        }
        for message, exemptions in cases.items():
            with self.subTest(message):
                doc = self.contract()
                doc['exemptions'] = exemptions
                self.write(doc)
                self.assertIn(message, self.validate())
        for fields in ({'status': 'accepted', 'approved_by': 'human'}, {'status': 'accepted', 'approved_by': 'human:alice'},
                       {'status': 'rejected'}):  # a rejected exemption needs no approver
            doc = self.contract()
            doc['exemptions'] = [exemption(**fields)]
            self.write(doc)
            self.assertEqual([], self.validate())

    def test_criterion_ids_keep_only_string_ids(self):
        doc = self.contract()
        doc['criteria'] += [{'id': 7}, 'x', {'statement': 'no id'}]
        self.write(doc)
        self.assertEqual({'VC-001'}, vc.criterion_ids(self.feature))

    def test_die_exits_with_code_2(self):
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as raised:
            vc.die('bad')
        self.assertEqual((2, 'ERROR: bad\n'), (raised.exception.code, err.getvalue()))

    def test_requirement_defaults_to_optional(self):
        (self.feature / 'design.json').unlink()
        self.assertEqual('optional', vc.requirement(self.feature))
        self.assertEqual([], self.validate())
        (self.feature / 'design.json').write_text(json.dumps({}))
        self.assertEqual('optional', vc.requirement(self.feature))


if __name__ == '__main__':
    unittest.main()
