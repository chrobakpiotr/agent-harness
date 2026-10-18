"""Ported from Showcase `tooling/agent-harness/tests/test_verification_noncacheable.py` at `49ab97f` (AH5-08b)."""
"""Controlled launches exercise real non-reusable terminal and failure authority."""
import dataclasses
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_verification_supervisor import LifecycleBackend

from agent_harness.verification.executor import execute_plan
from agent_harness.verification.model import Family
from agent_harness.verification.planner import build_plan, evaluate_ready_gate
from agent_harness.verification.profile import load_profile
from agent_harness.verification.store import StoreError, VerificationStore
from agent_harness.verification.supervisor import VerificationSupervisor

try:
    from human_grant_fixture import signed_test_grant, trusted_test_store
except ModuleNotFoundError:  # the grant fixture needs cryptography (agent-harness[grants])
    raise unittest.SkipTest("needs cryptography (agent-harness[grants])") from None


class PayloadBackend(LifecycleBackend):
    def __init__(self, store, *, exit_code=0, timeout=False, mutate=None, drained=True):
        super().__init__(store, store.root / 'controlled-backend',
                         inspect=None if drained else 'UNCERTAIN')
        self.exit_code, self.timeout, self.mutate = exit_code, timeout, mutate

    def launch(self, prepared, **kwargs):
        result = super().launch(prepared, **kwargs)
        payload = subprocess.run([sys.executable, '-c',
            f"print('diagnostic output'); raise SystemExit({self.exit_code})"],
            capture_output=True, text=True, check=False)
        result.exit_code = payload.returncode
        result.stdout, result.stderr = payload.stdout, payload.stderr
        result.timed_out = self.timeout
        if self.mutate:
            self.mutate()
        return result


class NoncacheableExecutionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), 'config', 'user.name', 'test'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'config', 'user.email', 'test@example.invalid'], check=True)
        (self.root / 'seed').write_text('base\n')
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'base'], check=True)
        self.base = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD'], text=True).strip()
        (self.root / 'seed').write_text('candidate\n')
        self.profile = load_profile({'schema_version': 1, 'gates': [{
            'id': 'noncacheable', 'command': 'python3 -c pass', 'inputs': ['seed'],
            'mandatory': True, 'cacheable': False, 'critical': True,
            'retry_policy': 'forbid', 'retry_controls': ['no-internal-retries'],
        }]})
        self.family = Family('noncacheable-family', self.base, 'integration',
                             self.profile.content_hash, 'a' * 40)
        self.store = VerificationStore(self.root)

    def run_gate(self, attempt, backend, family=None):
        family = family or self.family
        plan = build_plan(self.root, self.profile, family)
        with mock.patch('agent_harness.verification.executor.CommandExecutionBackend', return_value=backend):
            return execute_plan(self.root, self.profile, plan, store=self.store, attempt_id=attempt)

    def terminals(self):
        return self.store.reconstruct_execution_terminals(rebuild=False)

    def test_exit_zero_is_terminal_pass_without_reusable_evidence_and_reruns(self):
        backend = PayloadBackend(self.store)
        result = self.run_gate('first', backend)
        self.assertEqual('PASS', result.outcome, result.to_record())
        self.assertIsNone(result.gates[0].evidence)
        self.assertIsNone(result.gates[0].fingerprint)
        terminal = self.terminals()[0]
        self.assertEqual('PASS', terminal['result'])
        self.assertEqual('DRAINED', terminal['drainage'])
        self.assertTrue(terminal['post_observation']['stable'])
        started = json.loads((self.store.executions / terminal['execution_id'] / 'started.json').read_text())
        self.assertEqual(started['candidate_identity'], terminal['candidate_identity'])
        self.assertEqual(started['final_changed_surface_id'], terminal['final_changed_surface_id'])
        self.assertNotIn('verification_evidence', terminal)
        self.assertEqual([], list(self.store.iter_evidence()))
        result = self.run_gate('second', backend)
        self.assertEqual('PASS', result.outcome, result.to_record())
        self.assertEqual(2, backend.events.count('launch'))
        self.assertEqual(2, len(self.terminals()))

    def test_critical_failure_has_candidate_scope_not_null_or_global(self):
        failed = PayloadBackend(self.store, exit_code=1)
        self.assertEqual('FAIL', self.run_gate('failed', failed).outcome)
        failure = self.store.active_failure_fences()[0]
        self.assertIsInstance(failure['context']['fingerprint'], str)
        self.assertTrue(failure['context']['fingerprint'])
        blocked = self.run_gate('retry', PayloadBackend(self.store))
        self.assertEqual('needs-human', blocked.outcome, blocked.to_record())
        self.assertEqual('CRITICAL_FAILURE_FENCE_ACTIVE', blocked.gates[0].error)
        renamed = self.run_gate('renamed-family', PayloadBackend(self.store),
            dataclasses.replace(self.family, id='renamed-family'))
        self.assertEqual('needs-human', renamed.outcome, renamed.to_record())
        (self.root / 'seed').write_text('different candidate\n')
        unrelated = self.run_gate('new-candidate', PayloadBackend(self.store))
        self.assertEqual('PASS', unrelated.outcome)

    def test_unscoped_historical_failure_cannot_gain_permission_from_new_scope(self):
        VerificationSupervisor(self.store).execute(PayloadBackend(self.store, exit_code=1),
            worktree=self.root, family_id='historical', attempt_id='old', gate_id='noncacheable',
            command='python3 -c pass', cwd=self.root, run_dir=self.store.root / 'old',
            timeout_seconds=2, sandbox_mode='required', profile_hash=self.profile.content_hash,
            critical=True, retry_policy='forbid', retry_controls=('no-internal-retries',))
        result = self.run_gate('new', PayloadBackend(self.store))
        self.assertNotEqual('PASS', result.outcome)
        self.assertEqual('CRITICAL_FAILURE_CONTEXT_REQUIRED', result.gates[0].error)

    def test_derived_failure_scope_cannot_be_replaced_or_malformed(self):
        self.run_gate('scope', PayloadBackend(self.store))
        journal = next(self.store.executions.iterdir())
        path = journal / 'started.json'
        original = json.loads(path.read_text())
        for value in (None, '0' * 64, 'unknown'):
            changed = {**original, 'failure_fingerprint': value}
            path.write_text(json.dumps(changed))
            with self.subTest(value=value), self.assertRaisesRegex(
                    StoreError, 'invalid-execution-failure-scope'):
                self.store._validated_started(journal)
        path.write_text(json.dumps(original))

    def test_signed_retry_remains_single_use_for_noncacheable_failure(self):
        self.assertEqual('FAIL', self.run_gate('failed', PayloadBackend(self.store, exit_code=1)).outcome)
        failure = self.store.active_failure_fences()[0]
        started = json.loads((self.store.executions / failure['execution_id'] / 'started.json').read_text())
        family = dataclasses.replace(self.family, candidate_identity=started['candidate_identity'],
            final_changed_surface_id=started['final_changed_surface_id'])
        node = build_plan(self.root, self.profile, family).decisions[0].node
        scope = {'plan_id': 'verification-plan-v2:sha256:test-only-unqualified',
            'family_id': family.id, 'candidate_identity': family.candidate_identity,
            'final_changed_surface_id': family.final_changed_surface_id, 'lifecycle_generation': 1,
            'task_id': 'T-TEST', 'task_attempt': 1, 'gate_id': node.id,
            'obligation_id': 'test-obligation', 'unit_id': 'test-unit', 'retry_slot': 'test-slot',
            'profile_hash': self.profile.content_hash, 'policy_checkpoint': family.policy_checkpoint,
            'origin_binding': family.origin_policy, 'fence_fingerprint': failure['context']['fingerprint']}
        with tempfile.TemporaryDirectory() as issuer_root:
            issuer_store, key = trusted_test_store(issuer_root)
            self.store.issuer_registry = issuer_store.issuer_registry
            signed_test_grant(self.store, key, 'one-retry', failure['failure_id'],
                              failure['context'], retry_scope=scope)
            def ready():
                return evaluate_ready_gate(self.root, self.profile, family, node,
                                           trusted_runtime_root=self.store.root)
            def observe(_result, before):
                after = ready()  # Includes the real sealed-candidate drift check.
                return {'stable': before.fingerprint == after.fingerprint and after.action == 'RUN',
                        'candidate_identity': family.candidate_identity,
                        'final_changed_surface_id': family.final_changed_surface_id}
            kwargs = dict(worktree=self.root, family_id=family.id, attempt_id='authorized-retry',
                gate_id=node.id, command=node.gate.command, cwd=self.root,
                run_dir=self.store.root / 'test-retry', timeout_seconds=2, sandbox_mode='required',
                preflight=ready, post_observer=observe, input_fingerprint=None,
                profile_hash=self.profile.content_hash, critical=True, retry_policy='forbid',
                retry_controls=('no-internal-retries',), failure_grant_id='one-retry',
                candidate_identity=family.candidate_identity,
                final_changed_surface_id=family.final_changed_surface_id,
                policy_checkpoint=family.policy_checkpoint, retry_scope=scope)
            result, _ready = VerificationSupervisor(self.store).execute(PayloadBackend(self.store), **kwargs)
            self.assertEqual(0, result.exit_code)
            self.assertTrue(self.store.grant_consumed('one-retry'))
            self.assertIsNone(self.store.current_failure(failure['context']))
            kwargs['attempt_id'] = 'second-retry'
            with self.assertRaisesRegex(RuntimeError, 'FAILURE_GRANT_CONSUMED'):
                VerificationSupervisor(self.store).execute(PayloadBackend(self.store), **kwargs)
            self.assertEqual('needs-human', self.run_gate('fresh', PayloadBackend(self.store)).outcome)

    def test_nonzero_timeout_mutation_and_unknown_drainage_never_pass(self):
        expected = {'nonzero': 'FAIL', 'timeout': 'TIMEOUT', 'mutation': 'ERROR', 'drainage': 'ERROR'}
        for mode in expected:
            with self.subTest(mode=mode):
                # Distinct source surfaces prevent an earlier fence masking the payload result.
                (self.root / 'seed').write_text('candidate-' + mode + '\n')
                backend = PayloadBackend(self.store, exit_code=1 if mode == 'nonzero' else 0,
                    timeout=mode == 'timeout', drained=mode != 'drainage',
                    mutate=(lambda: (self.root / 'seed').write_text('drift\n')) if mode == 'mutation' else None)
                count = len(self.terminals())
                result = self.run_gate(mode, backend)
                self.assertEqual(expected[mode], result.outcome, result.to_record())
                self.assertEqual(1, backend.events.count('launch'))
                if mode == 'drainage':
                    self.assertEqual(count, len(self.terminals()))
                else:
                    terminal = next(t for t in self.terminals() if t['result'] == expected[mode])
                    self.assertEqual('DRAINED', terminal['drainage'])

    def test_terminal_publication_crash_recovers_pass_without_relaunch(self):
        backend = PayloadBackend(self.store)
        supervisor = VerificationSupervisor(self.store)
        supervisor.inject_crash_at = 'after-drained'
        with mock.patch('agent_harness.verification.executor.VerificationSupervisor', return_value=supervisor):
            result = self.run_gate('recover', backend)
        self.assertNotEqual('PASS', result.outcome)
        self.assertEqual(1, backend.events.count('launch'))
        recovered = VerificationSupervisor(self.store).recover(lambda _started: backend)
        self.assertEqual('PASS', recovered[0].terminal['result'])
        self.assertNotIn('verification_evidence', recovered[0].terminal)
        result = self.run_gate('recover', backend)
        self.assertEqual('PASS', result.outcome, result.to_record())
        self.assertEqual(1, backend.events.count('launch'))
