"""F1: `start` records the verification contract's binding with the attempt; `complete` requires it unchanged.

Runs inside Showcase through its shim, with Showcase's own feature and evidence fixtures (borrowed from
`test_harness.HarnessTest`, whose tests are not collected here).
"""
import argparse
import contextlib
import io
import json
import shutil
import subprocess
import unittest
from unittest import mock

import test_harness as th

harness = th.harness
vc = harness.vc

TASKS = [
    {'id': 'T-001', 'title': 'A', 'objective': 'A', 'role': 'builder', 'depends_on': [],
     'allowed_paths': ['a.txt'], 'risk_tags': [], 'acceptance_criteria': ['AC-001'], 'verification': ['true']},
    {'id': 'T-002', 'title': 'B', 'objective': 'B', 'role': 'builder', 'depends_on': ['T-001'],
     'allowed_paths': ['b.txt'], 'risk_tags': [], 'acceptance_criteria': ['AC-001'], 'verification': ['true']},
    {'id': 'T-900', 'title': 'E', 'objective': 'E', 'role': 'evaluator', 'depends_on': ['T-001', 'T-002'],
     'allowed_paths': ['docs/specs/TST-001/evidence/**'], 'risk_tags': [],
     'acceptance_criteria': ['AC-001', 'AC-002', 'VC-001'], 'verification': ['true']},
]


def git(root, *args):
    subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', *args],
                   cwd=root, check=True, capture_output=True)


class ContractBindingTest(unittest.TestCase):
    setUp_harness = th.HarnessTest.setUp
    tearDown = th.HarnessTest.tearDown
    feature_fixture = th.HarnessTest.feature
    passing_completion_evidence = th.HarnessTest.passing_completion_evidence

    def setUp(self):
        self.setUp_harness()
        patch = mock.patch.object(vc, 'REPO', self.root)  # the contract's repository root is the fixture's
        patch.start()
        self.addCleanup(patch.stop)
        self.feature = self.feature_fixture(TASKS)
        self.doc = harness.load_json(self.feature / 'tasks.json')
        (self.root / 'AGENTS.md').write_text('# agents\n')
        (self.root / 'docs/agentic-sdd/constitution.md').write_text('# constitution\n')
        (self.root / 'tooling/agent-harness').mkdir(parents=True)
        (self.root / 'tooling/agent-harness/harness.py').write_text('# stub in worktree\n')
        (self.root / '.gitignore').write_text('.agent-state/\ndocs/specs/*/packets/\n')
        self.author_contract()
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-q', '-m', 'base')
        self.addCleanup(self.remove_worktrees)

    def remove_worktrees(self):
        for task_id in ('T-001', 'T-002'):
            path = harness.worktree_path('TST-001', task_id)
            if path.exists():
                subprocess.run(['git', 'worktree', 'remove', '--force', str(path)], cwd=self.root, check=False)
        shutil.rmtree(self.root.parent / f'{self.root.name}{harness.WORKTREE_ROOT_SUFFIX}', ignore_errors=True)

    def author_contract(self, summary='ok'):
        """What authoring and acceptance produce: an accepted contract current with the inputs as they are now."""
        (self.feature / 'verification-contract.json').write_text(json.dumps({
            'schema_version': 1, 'feature': 'TST-001', 'status': 'accepted', 'inputs': vc.input_hashes(self.feature),
            'criteria': [{'id': 'VC-001', 'statement': 'Preserve invariant', 'origin': 'independent',
                          'source_type': 'architecture-invariant', 'sources': ['constitution'],
                          'verification_hint': 'architecture test'}],
            'exemptions': [], 'summary': summary, 'assumptions': []}))

    def state_bytes(self):
        return {path.name: path.read_bytes() for path in harness.runtime_state_dir(self.feature).glob('*.json')}

    def entry(self, task_id='T-001'):
        return harness.load_state(self.feature, self.doc)['tasks'][task_id]

    def start(self, task_id='T-001'):
        with contextlib.redirect_stdout(io.StringIO()):
            harness.cmd_start(argparse.Namespace(feature_dir=self.feature, task_id=task_id, owner='worker-a'))

    def evidence(self, task_id='T-001', name='a.txt'):
        worktree = harness.worktree_path('TST-001', task_id)
        (worktree / name).write_text('work\n')
        checkpoint = harness.checkpoint_worktree(
            self.doc, harness.active_task_contract(self.feature, self.doc, task_id), worktree)
        path = self.root / f'{task_id}-result.json'
        path.write_text(json.dumps(self.passing_completion_evidence(
            self.feature, self.doc, task_id, self.entry(task_id)['attempts'], checkpoint)))
        return path

    def complete(self, evidence, task_id='T-001'):
        with contextlib.redirect_stdout(io.StringIO()):
            harness.cmd_complete(argparse.Namespace(feature_dir=self.feature, task_id=task_id, owner='worker-a',
                                                    evidence=str(evidence)))

    def refused(self, call):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as raised:
            call()
        self.assertEqual(2, raised.exception.code)
        return err.getvalue()

    def reconcile(self):
        generation = json.loads(harness.state_path(self.feature).read_text()).get('feature_generation', 1)
        with contextlib.redirect_stdout(io.StringIO()):
            harness.cmd_reconcile_feature(argparse.Namespace(
                feature_dir=self.feature, reason='contract refreshed', by='human:test',
                expected_generation=generation))

    def amend_constitution_and_refresh(self, *, reconcile=True):
        """The contract re-authored and accepted for an amended constitution, then the feature reconciled: valid,
        current and under a matching fingerprint again, so only the attempt's binding can tell."""
        (self.root / 'docs/agentic-sdd/constitution.md').write_text('# constitution, amended\n')
        self.author_contract()
        git(self.root, 'commit', '-q', '-am', 'amend the constitution; refresh the contract')
        if reconcile:
            self.reconcile()

    def without_contract(self):
        (self.feature / 'verification-contract.json').unlink()
        self.set_vc_coverage(False)
        git(self.root, 'commit', '-q', '-am', 'no verification contract')

    def set_vc_coverage(self, covered):
        doc = json.loads((self.feature / 'tasks.json').read_text())
        criteria = doc['tasks'][2]['acceptance_criteria']
        if covered:
            criteria.append('VC-001')
        else:
            criteria.remove('VC-001')
        (self.feature / 'tasks.json').write_text(json.dumps(doc))
        self.doc = doc

    def test_an_unchanged_attempt_records_its_binding_and_completes(self):
        self.start()
        binding = self.entry()['contract_binding']
        self.assertEqual(vc.contract_binding(self.feature), ([], binding))
        self.assertTrue(binding['contract']['present'])
        self.complete(self.evidence())
        self.assertEqual('completed', self.entry()['status'])
        self.assertEqual(binding, self.entry()['contract_binding'])  # complete never rewrites it

    def test_a_change_between_start_and_complete_refuses_and_keeps_state_and_evidence(self):
        self.start()
        evidence = self.evidence()
        self.amend_constitution_and_refresh()
        before = self.state_bytes()
        err = self.refused(lambda: self.complete(evidence))
        self.assertIn('VERIFICATION_CONTRACT_CHANGED_SINCE_START: attempt 1 of T-001: verification-contract.json, '
                      'constitution_sha256 differs from the binding recorded at start; state and evidence are kept',
                      err)
        self.assertIn(f'this run read {self.feature}', err)
        self.assertEqual(before, self.state_bytes())
        self.assertTrue(evidence.exists())
        self.assertEqual('running', self.entry()['status'])

    def test_before_reconciliation_the_fingerprint_refuses_first(self):
        self.start()
        evidence = self.evidence()
        self.amend_constitution_and_refresh(reconcile=False)
        before = self.state_bytes()
        self.assertIn('fingerprint differs', self.refused(lambda: self.complete(evidence)))
        self.assertEqual(before, self.state_bytes())

    def test_a_reauthored_contract_alone_refuses(self):
        self.start()
        evidence = self.evidence()
        self.author_contract(summary='re-accepted')
        git(self.root, 'commit', '-q', '-am', 're-accept the contract')
        self.reconcile()
        err = self.refused(lambda: self.complete(evidence))
        self.assertIn('VERIFICATION_CONTRACT_CHANGED_SINCE_START: attempt 1 of T-001: verification-contract.json '
                      'differs', err)

    def test_an_input_changed_and_not_reauthored_refuses_as_stale(self):
        self.start()
        evidence = self.evidence()
        (self.root / 'docs/agentic-sdd/constitution.md').write_text('# constitution, amended\n')
        before = self.state_bytes()
        err = self.refused(lambda: self.complete(evidence))
        self.assertIn('verification contract is stale: inputs.constitution_sha256', err)
        self.assertEqual(before, self.state_bytes())

    def changing_after_first_read(self):
        real = vc.contract_binding
        calls = []

        def contract_binding(feature_dir, **kwargs):
            result = real(feature_dir, **kwargs)
            calls.append(feature_dir)
            if len(calls) == 1:  # between the unlocked pre-check and the check under the lock
                self.amend_constitution_and_refresh()
            return result
        return mock.patch.object(vc, 'contract_binding', contract_binding), calls

    def test_a_change_before_the_locked_check_refuses_start_without_using_an_attempt(self):
        self.reconcile_ready()
        patch, calls = self.changing_after_first_read()
        with patch:
            err = self.refused(self.start)
        self.assertEqual(2, len(calls))
        self.assertIn('VERIFICATION_CONTRACT_CHANGED_DURING_OPERATION: verification-contract.json, constitution_sha256 '
                      'changed during the operation; nothing was written', err)
        self.assertEqual(0, int(self.entry().get('attempts', 0)))
        self.assertEqual('pending', self.entry()['status'])
        self.assertNotIn('contract_binding', self.entry())
        self.assertFalse(harness.worktree_path('TST-001', 'T-001').exists())

    def reconcile_ready(self):
        """Lifecycle state that a reconciliation can supersede: a base checkpoint, as after a first start."""
        state = harness.load_state(self.feature, self.doc)
        state['base_commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.root, text=True).strip()
        state['base_kind'] = 'head'
        harness.save_state(self.feature, state)

    def test_a_change_before_the_locked_check_refuses_complete(self):
        self.start()
        evidence = self.evidence()
        patch, _ = self.changing_after_first_read()
        with patch:
            err = self.refused(lambda: self.complete(evidence))
        self.assertIn('VERIFICATION_CONTRACT_CHANGED_DURING_OPERATION', err)
        self.assertEqual('running', self.entry()['status'])

    def claim(self, task_id='T-001'):
        with contextlib.redirect_stdout(io.StringIO()):
            harness.cmd_claim(argparse.Namespace(feature_dir=self.feature, task_id=task_id, owner='worker-a'))

    def test_claim_records_the_binding_like_start(self):
        self.claim()
        self.assertEqual('running', self.entry()['status'])
        self.assertEqual(vc.contract_binding(self.feature)[1], self.entry()['contract_binding'])

    def test_a_change_before_the_locked_check_refuses_claim_without_using_an_attempt(self):
        self.reconcile_ready()
        patch, calls = self.changing_after_first_read()
        with patch:
            err = self.refused(self.claim)
        self.assertEqual(2, len(calls))
        self.assertIn('VERIFICATION_CONTRACT_CHANGED_DURING_OPERATION', err)
        self.assertEqual(0, int(self.entry().get('attempts', 0)))
        self.assertNotIn('contract_binding', self.entry())

    def test_a_feature_outside_the_configured_repository_is_refused_without_fallback(self):
        other = self.root.parent / f'{self.root.name}-other'
        (other / 'docs/agentic-sdd').mkdir(parents=True)
        shutil.copy2(self.root / 'docs/agentic-sdd/constitution.md', other / 'docs/agentic-sdd/constitution.md')
        try:
            git(other, 'init', '-q')
            git(other, 'add', '.')
            git(other, 'commit', '-q', '-m', 'other')  # validation passes there; only the identity differs
            before = self.state_bytes()
            with mock.patch.object(vc, 'REPO', other):  # the current directory is still the feature's repository
                err = self.refused(self.start)
        finally:
            shutil.rmtree(other, ignore_errors=True)
        self.assertIn('verification contract currency cannot be established: repository identity unconfirmed', err)
        self.assertEqual(before, self.state_bytes())

    def test_a_worktree_of_the_configured_repository_is_the_same_repository(self):
        worktree = self.root.parent / f'{self.root.name}-wt'
        git(self.root, 'worktree', 'add', '-q', '--detach', str(worktree))
        try:
            errors, binding = vc.contract_binding(worktree / 'docs/specs/TST-001')
            self.assertEqual([], errors)
            self.assertEqual(vc.contract_binding(self.feature)[1]['inputs'], binding['inputs'])
        finally:
            subprocess.run(['git', 'worktree', 'remove', '--force', str(worktree)], cwd=self.root, check=False)

    def test_unreadable_inputs_refuse_start_and_complete(self):
        constitution = self.root / 'docs/agentic-sdd/constitution.md'
        try:
            constitution.chmod(0)
            before = self.state_bytes()
            err = self.refused(self.start)
            self.assertIn('verification contract currency cannot be established: constitution_sha256 unreadable '
                          '(PermissionError)', err)
            self.assertEqual(before, self.state_bytes())
            constitution.chmod(0o644)
            self.start()
            evidence = self.evidence()
            constitution.chmod(0)
            before = self.state_bytes()
            self.assertIn('constitution_sha256 unreadable (PermissionError)',
                          self.refused(lambda: self.complete(evidence)))
            self.assertEqual(before, self.state_bytes())
        finally:
            constitution.chmod(0o644)

    def test_an_attempt_without_a_recorded_binding_cannot_complete_normally(self):
        self.start()
        evidence = self.evidence()
        state = harness.load_state(self.feature, self.doc)
        del state['tasks']['T-001']['contract_binding']  # an attempt started before F1
        harness.save_state(self.feature, state)
        before = self.state_bytes()
        err = self.refused(lambda: self.complete(evidence))
        self.assertIn('VERIFICATION_CONTRACT_BINDING_MISSING: attempt 1 of T-001 was started without a recorded '
                      'contract binding; state and evidence are kept', err)
        self.assertEqual(before, self.state_bytes())
        self.assertNotIn('contract_binding', self.entry())  # nothing back-filled

    def test_without_a_contract_the_optional_policy_is_unchanged(self):
        self.without_contract()
        self.start()
        self.assertEqual({'version': 1, 'contract': {'present': False}}, self.entry()['contract_binding'])
        evidence = self.evidence()
        state = harness.load_state(self.feature, self.doc)
        del state['tasks']['T-001']['contract_binding']  # nor for an attempt started before F1
        harness.save_state(self.feature, state)
        self.complete(evidence)
        self.assertEqual('completed', self.entry()['status'])

    def test_a_contract_added_after_start_refuses_complete(self):
        self.without_contract()
        self.start()
        evidence = self.evidence()
        self.author_contract()
        self.set_vc_coverage(True)
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-q', '-m', 'add a verification contract')
        self.reconcile()
        err = self.refused(lambda: self.complete(evidence))
        self.assertIn('VERIFICATION_CONTRACT_CHANGED_SINCE_START: attempt 1 of T-001: verification-contract.json, '
                      'constitution_sha256, design_gate_sha256, master_closure_sha256, plan_sha256, spec_sha256', err)

    def test_complete_before_integration_passes_and_a_later_integration_blocks_the_next_start(self):
        self.start()
        self.complete(self.evidence())
        self.assertEqual('completed', self.entry()['status'])
        # Integration outside the lifecycle: a change to a contract input lands in the primary checkout.
        (self.root / 'docs/agentic-sdd/constitution.md').write_text('# constitution, as integrated\n')
        git(self.root, 'commit', '-q', '-am', 'integrate T-001')
        before = self.state_bytes()
        err = self.refused(lambda: self.start('T-002'))
        self.assertIn('verification contract is stale: inputs.constitution_sha256', err)
        self.assertEqual(before, self.state_bytes())
        # Refreshed through authoring and acceptance; the changed contract is then adopted by reconciliation.
        self.author_contract()
        git(self.root, 'commit', '-q', '-am', 'refresh the verification contract')
        self.assertIn('fingerprint differs', self.refused(lambda: self.start('T-002')))
        self.reconcile()
        self.start('T-002')
        self.assertEqual(vc.contract_binding(self.feature)[1], self.entry('T-002')['contract_binding'])
        self.assertNotEqual(self.entry('T-001')['contract_binding'], self.entry('T-002')['contract_binding'])


class CompleteRepairBindingTest(th.CanonicalCompletionRepairTests):
    """F1 at `complete-repair`: a new repaired completion of an attempt needs that attempt's binding."""

    def completion_fixture(self, **kwargs):
        patch = mock.patch.object(vc, 'REPO', self.root)  # the fixture repository, as the identity check requires
        patch.start()
        self.addCleanup(patch.stop)
        return super().completion_fixture(**kwargs)

    def set_binding(self, feature, doc, binding):
        state = harness.load_state(feature, doc)
        entry = state['tasks']['T-A']
        if binding is None:
            entry.pop('contract_binding', None)
        else:
            entry['contract_binding'] = binding
        harness.save_state(feature, state)

    def repair_refused(self, feature, path):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            self._complete(feature, path)
        return err.getvalue()

    def test_f1_a_repair_of_an_attempt_without_a_binding_is_refused(self):
        feature, doc, _, _, _, path = self._setup_repair()
        self.set_binding(feature, doc, None)
        before = harness.load_state(feature, doc)['tasks']['T-A']
        err = self.repair_refused(feature, path)
        self.assertIn('VERIFICATION_CONTRACT_BINDING_MISSING: attempt 5 of T-A', err)
        self.assertEqual(before, harness.load_state(feature, doc)['tasks']['T-A'])
        self.assertEqual([], harness.repaired_completion_records(feature, 'T-A'))

    def test_f1_a_repair_against_a_different_binding_is_refused(self):
        feature, doc, _, _, _, path = self._setup_repair()
        binding = json.loads(json.dumps(vc.contract_binding(feature)[1]))
        binding['inputs']['constitution_sha256'] = {'present': True, 'sha256': '0' * 64}  # started under another
        self.set_binding(feature, doc, binding)
        err = self.repair_refused(feature, path)
        self.assertIn('VERIFICATION_CONTRACT_CHANGED_SINCE_START: attempt 5 of T-A: constitution_sha256 differs', err)
        self.assertEqual([], harness.repaired_completion_records(feature, 'T-A'))

    def test_f1_a_repair_with_the_attempt_binding_completes_and_keeps_it(self):
        feature, doc, _, _, _, path = self._setup_repair()
        binding = vc.contract_binding(feature)[1]
        self.set_binding(feature, doc, binding)
        self._complete(feature, path)
        entry = harness.load_state(feature, doc)['tasks']['T-A']
        self.assertEqual('completed', entry['status'])
        self.assertEqual(binding, entry['contract_binding'])
        self.assertEqual(1, len(harness.repaired_completion_records(feature, 'T-A')))


def load_tests(loader, tests, pattern):
    """Only this module's own tests; the inherited repair tests run in `test_harness`."""
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(ContractBindingTest))
    suite.addTests(loader.loadTestsFromNames(
        [f'{__name__}.CompleteRepairBindingTest.{name}' for name in loader.getTestCaseNames(CompleteRepairBindingTest)
         if name.startswith('test_f1_')]))
    return suite


if __name__ == '__main__':
    unittest.main()
