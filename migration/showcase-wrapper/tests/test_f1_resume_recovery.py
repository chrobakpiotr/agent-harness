"""F1 recovery: the way out for an attempt whose pending verification resume can no longer start under F1.

Runs inside Showcase through its shim, on the F1 fixtures (`test_f1_contract_binding`).
"""
import argparse
import contextlib
import io
import json
import subprocess
import unittest

import test_f1_contract_binding as f1

from agent_harness import feature_transitions as ft

harness = f1.harness
vc = f1.vc


class ResumeRecoveryTest(f1.ContractBindingTest):
    passing_builder_result = f1.th.HarnessTest.passing_builder_result

    def feature_fixture(self, tasks):
        """As Showcase's replan tests: tasks with a test mode and seam (a task without them gets a legacy packet that
        a later replan cannot reconstruct, independently of F1)."""
        return f1.th.HarnessTest.feature(self, [dict(task, test_mode='existing-suite', test_seam=f'{task["id"]} seam')
                                                for task in tasks])

    def pending_resume(self):
        """Attempt 1 started under the contract, stopped, and scheduled for verification resume."""
        self.start()
        with contextlib.redirect_stdout(io.StringIO()):
            harness.cmd_release(argparse.Namespace(feature_dir=self.feature, task_id='T-001', owner='worker-a',
                                                   reason='stopped for verification'))
        state = harness.load_state(self.feature, self.doc)
        entry = state['tasks']['T-001']
        result = self.passing_builder_result('T-001')
        entry['last_failure_evidence'] = str(result)
        resume = {'plan_id': 'plan-1', 'task_attempt': 1, 'lifecycle_generation': state.get('feature_generation', 1),
                  'reason_sha256': 'a' * 64, 'requested_at': '2026-10-24T10:00:00+00:00',
                  'operator': 'operator@example.invalid',
                  'prior_result_sha256': harness.sha256_bytes(result.read_bytes()),
                  'prior_result_path': str(result)}
        entry['verification_resume_pending'] = resume
        entry.setdefault('verification_resume_history', []).append(dict(resume))
        harness.save_state(self.feature, state)
        return resume

    def human_resolve_and_replan(self):
        """The existing human path; the replan gives the task a packet under the refreshed contract."""
        with contextlib.redirect_stdout(io.StringIO()):
            harness.cmd_human_resolve(argparse.Namespace(
                feature_dir=self.feature, task_id='T-001', decision='Retry under the refreshed contract.',
                decision_file=None, by='human:operator'))
        state = harness.load_state(self.feature, self.doc)
        active = harness.resolve_active_packet(self.feature, self.doc, 'T-001', state=state)
        proposal = self.feature / 'proposal.json'
        proposal.write_text(json.dumps(dict(harness.task_index(self.doc)['T-001'], objective='A, under the new contract')))
        with contextlib.redirect_stdout(io.StringIO()):
            harness.cmd_replan_task(argparse.Namespace(
                feature_dir=self.feature, task_id='T-001', expected_status='failed',
                expected_attempts=state['tasks']['T-001']['attempts'],
                expected_active_revision=active['revision_id'], expected_contract_sha256=active['contract_sha256'],
                expected_feature_generation=state.get('feature_generation', 1), proposed_task_file=str(proposal),
                reason='contract refreshed', by='human:operator', checkpoint=None))
        f1.git(self.root, 'add', '.')
        f1.git(self.root, 'commit', '-q', '-m', 'replan T-001 under the refreshed contract')

    def rev(self, ref):
        proc = subprocess.run(['git', 'rev-parse', '--verify', '--quiet', ref], cwd=self.root, capture_output=True,
                              text=True, check=False)
        return proc.stdout.strip() or None

    def remove_worktree(self):
        with contextlib.redirect_stdout(io.StringIO()):
            harness.cmd_worktree_remove(argparse.Namespace(feature_dir=self.feature, task_id='T-001'))

    def stopped_attempt(self):
        self.start()
        with contextlib.redirect_stdout(io.StringIO()):
            harness.cmd_release(argparse.Namespace(feature_dir=self.feature, task_id='T-001', owner='worker-a',
                                                   reason='stopped'))
        return self.rev('refs/heads/agent/TST-001/T-001')

    def test_r_worktree_remove_refuses_uncommitted_changes(self):
        tip = self.stopped_attempt()
        worktree = harness.worktree_path('TST-001', 'T-001')
        (worktree / 'a.txt').write_text('not committed\n')
        before = self.state_bytes()
        self.assertIn('WORKTREE_REMOVE_REFUSED', self.refused(self.remove_worktree))
        self.assertTrue(worktree.exists())
        self.assertEqual(tip, self.rev('refs/heads/agent/TST-001/T-001'))
        self.assertIsNone(self.rev(f'refs/attempts/TST-001/T-001/1/{tip}'))
        self.assertEqual(before, self.state_bytes())

    def test_r_worktree_remove_never_overwrites_an_archive_ref(self):
        tip = self.stopped_attempt()
        archive = f'refs/attempts/TST-001/T-001/1/{tip}'
        other = self.rev(f'{tip}^')
        f1.git(self.root, 'update-ref', archive, other)  # an existing ref with the same name, elsewhere
        before = self.state_bytes()
        self.assertIn(f'archive ref {archive} does not point at {tip}; nothing removed',
                      self.refused(self.remove_worktree))
        self.assertEqual(other, self.rev(archive))
        self.assertEqual(tip, self.rev('refs/heads/agent/TST-001/T-001'))
        self.assertTrue(harness.worktree_path('TST-001', 'T-001').exists())
        self.assertEqual(before, self.state_bytes())

    def invalidate(self, resume_identity):
        with contextlib.redirect_stdout(io.StringIO()):
            harness.cmd_invalidate_verification_resume(argparse.Namespace(
                feature_dir=self.feature, task_id='T-001', resume=resume_identity,
                reason='the contract changed; the resume no longer applies', by='human:operator'))

    def identity(self, resume=None, **entry_changes):
        """As the refusal prints it: the pending resume (or the one given) with the attempt's state."""
        entry = {**self.entry(), **entry_changes}
        if resume is not None:
            entry['verification_resume_pending'] = resume
        return ft.verification_resume_identity(entry, ns=harness._NS)

    def set_entry(self, **changes):
        state = harness.load_state(self.feature, self.doc)
        state['tasks']['T-001'].update(changes)
        harness.save_state(self.feature, state)

    def test_r_a_stuck_resume_is_refused_with_its_way_out(self):
        resume = self.pending_resume()
        self.amend_constitution_and_refresh()
        before = self.state_bytes()
        err = self.refused(self.start)
        self.assertIn('VERIFICATION_CONTRACT_CHANGED_SINCE_START: attempt 1 of T-001', err)
        self.assertIn(f'invalidate-verification-resume --resume {self.identity(resume)}', err)
        self.assertEqual(before, self.state_bytes())

    def test_r_invalidation_ends_the_resume_and_a_new_start_runs_under_the_current_contract(self):
        resume = self.pending_resume()
        old_binding = self.entry()['contract_binding']
        evidence = self.entry()['last_failure_evidence']
        self.amend_constitution_and_refresh()
        self.invalidate(self.identity(resume))
        entry = self.entry()
        self.assertNotIn('verification_resume_pending', entry)
        self.assertEqual('escalated', entry['status'])
        self.assertEqual(1, entry['attempts'])
        self.assertEqual(old_binding, entry['contract_binding'])  # never rewritten
        self.assertEqual(evidence, entry['last_failure_evidence'])
        ended = entry['verification_resume_history'][-1]
        self.assertEqual({**resume, 'result': 'invalidated'}, {k: ended[k] for k in (*resume, 'result')})
        self.assertEqual('human:operator', ended['invalidation']['operator'])
        self.assertEqual('the contract changed; the resume no longer applies', ended['invalidation']['reason'])
        self.assertEqual([], harness.completion_records(self.feature, 'T-001'))
        self.assertNotIn('T-001', harness.ready_ids(self.doc, harness.load_state(self.feature, self.doc), self.feature))
        self.human_resolve_and_replan()
        # The stopped attempt's worktree still holds the old contract; `worktree-remove` takes it and its branch,
        # keeping the attempt's commits under an archive ref recorded in the attempt's state.
        branch_head = self.rev('refs/heads/agent/TST-001/T-001')
        self.remove_worktree()
        archive = f'refs/attempts/TST-001/T-001/1/{branch_head}'
        self.assertEqual(branch_head, self.rev(archive))
        self.assertEqual([(archive, branch_head, 1)], [(item['ref'], item['commit'], item['attempt'])
                                                       for item in self.entry()['archived_attempt_refs']])
        self.start()
        self.assertEqual(branch_head, self.rev(archive))
        entry = self.entry()
        self.assertEqual(('running', 2), (entry['status'], entry['attempts']))
        self.assertEqual(vc.contract_binding(self.feature)[1], entry['contract_binding'])
        self.assertNotEqual(old_binding, entry['contract_binding'])

    def test_r_a_resume_other_than_the_one_named_is_refused_without_change(self):
        resume = self.pending_resume()
        before = self.state_bytes()
        for named in (self.identity({**resume, 'requested_at': '2026-10-24T11:00:00+00:00'}),
                      self.identity(last_attempt_commit='0' * 40),  # the same resume on a changed attempt
                      'sha256:' + '0' * 64):
            with self.subTest(named=named):
                err = self.refused(lambda named=named: self.invalidate(named))
                self.assertIn('VERIFICATION_RESUME_INVALIDATION_REFUSED: the pending resume differs', err)
                self.assertEqual(before, self.state_bytes())

    def test_r_without_a_pending_resume_nothing_is_invalidated(self):
        self.start()
        before = self.state_bytes()
        err = self.refused(lambda: self.invalidate('sha256:' + '0' * 64))  # running, no pending resume
        self.assertIn('VERIFICATION_RESUME_INVALIDATION_REFUSED: T-001 has no pending verification resume', err)
        self.assertEqual(before, self.state_bytes())

    def test_r_an_inconsistent_pending_resume_is_refused(self):
        resume = self.pending_resume()
        state = harness.load_state(self.feature, self.doc)
        state['tasks']['T-001']['verification_resume_history'][-1]['operator'] = 'someone else'
        harness.save_state(self.feature, state)
        before = self.state_bytes()
        err = self.refused(lambda: self.invalidate(self.identity(resume)))
        self.assertIn('VERIFICATION_RESUME_STATE_INVALID', err)
        self.assertEqual(before, self.state_bytes())

    def test_r_a_pending_resume_on_a_task_that_is_not_stopped_is_refused(self):
        resume = self.pending_resume()
        for changes in ({'status': 'running'}, {'verification_resume_active': dict(resume)}):
            with self.subTest(changes=list(changes)):
                self.set_entry(**changes)
                before = self.state_bytes()
                err = self.refused(lambda: self.invalidate(self.identity()))
                self.assertIn('VERIFICATION_RESUME_INVALIDATION_REFUSED: T-001 has no pending verification resume',
                              err)
                self.assertEqual(before, self.state_bytes())
                state = harness.load_state(self.feature, self.doc)
                state['tasks']['T-001'].update({'status': 'failed'})
                state['tasks']['T-001'].pop('verification_resume_active', None)
                harness.save_state(self.feature, state)

    def test_r_reason_and_operator_are_required(self):
        resume = self.pending_resume()
        with contextlib.redirect_stdout(io.StringIO()):
            err = self.refused(lambda: harness.cmd_invalidate_verification_resume(argparse.Namespace(
                feature_dir=self.feature, task_id='T-001', resume=self.identity(resume), reason=' ', by='x')))
        self.assertIn('task, reason, and operator are required', err)
        self.assertIn('verification_resume_pending', json.loads(harness.state_path(self.feature).read_text())
                      ['tasks']['T-001'])


def load_tests(loader, tests, pattern):
    """Only this module's own tests; the F1 tests run in `test_f1_contract_binding`."""
    return loader.loadTestsFromNames([f'{__name__}.ResumeRecoveryTest.{name}'
                                      for name in loader.getTestCaseNames(ResumeRecoveryTest)
                                      if name.startswith('test_r_')])


if __name__ == '__main__':
    unittest.main()
