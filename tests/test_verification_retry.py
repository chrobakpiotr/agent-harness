"""[seam] Registered retry controls (AH5-08b): the consumer collects evidence, the library checks it."""
import hashlib
import json
import pathlib
import tempfile
import unittest

from agent_harness.verification.retry import RetryControl, registry, retry_policy_facts
from agent_harness.verification.store import StoreError, VerificationStore
from agent_harness.verification.supervisor import (
    SupervisorError,
    VerificationSupervisor,
)

try:
    from test_verification_supervisor import LifecycleBackend
except unittest.SkipTest:  # the supervisor tests need cryptography (agent-harness[grants])
    raise unittest.SkipTest("needs cryptography (agent-harness[grants])") from None

SHA = 'c' * 64


def control(**changes):
    calls = changes.pop('calls', [])

    def collect(worktree, item):
        calls.append(('collect', pathlib.Path(worktree), item.control_id))
        return {'schema_version': 1, 'control_id': item.control_id, 'gate_id': item.gate_id,
                'command': item.command, 'config_path': item.config_path,
                'config_sha256': item.inputs[item.config_path], 'wrapper_sha256': item.wrapper_sha256,
                'registered_inputs_sha256': item.inputs_digest(), 'strict_max_retries': 0,
                'fail_on_passed_after_retry': True}

    def check(run_dir, environment):
        calls.append(('check', pathlib.Path(run_dir), environment))

    fields = {'control_id': 'tool-retry-off-v1', 'gate_id': 'critical-gate', 'command': './run-critical.sh',
              'wrapper_sha256': SHA, 'config_path': 'build.cfg', 'inputs': {'build.cfg': SHA, 'run-critical.sh': SHA},
              'collect_evidence': collect, 'check_run_dir': check, 'run_namespace': 'registered-tool'}
    fields.update(changes)
    return RetryControl(**fields)


class RetryControlTest(unittest.TestCase):
    def execute(self, store, root, *, gate_id='critical-gate', command='./run-critical.sh',
                controls=('tool-retry-off-v1',), environment=None):
        backend = LifecycleBackend(store, root / 'unit')
        VerificationSupervisor(store).execute(
            backend, worktree=root, family_id='f', attempt_id='a', gate_id=gate_id, command=command, cwd=root,
            run_dir=root / 'run', timeout_seconds=2, sandbox_mode='required', critical=True,
            retry_policy='forbid', retry_controls=controls, environment=environment)
        return backend

    def test_registered_control_runs_through_the_consumer_and_is_recorded(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp).resolve()
            store = VerificationStore(root, control_root=root / 'control', retry_controls=(control(calls=calls),))
            backend = self.execute(store, root)
            self.assertEqual(['collect', 'check'], [call[0] for call in calls])
            self.assertEqual('registered-tool', calls[1][1].parent.name)  # the control's own run namespace
            self.assertEqual(calls[1][1], backend.prepare_kwargs['run_dir'])
            terminal = json.loads(next(store.executions.glob('*/terminal.json')).read_text())
            proof = terminal['retry_policy_proof']
            self.assertTrue(proof['retry_free'])
            self.assertEqual('tool-retry-off-v1', proof['control_evidence']['control_id'])
            # A fresh store with the same registry reconstructs the proof; without it the proof is refused.
            same = VerificationStore(root, control_root=root / 'control', retry_controls=(control(),))
            self.assertEqual(1, len(same.reconstruct_execution_terminals()))
            bare = VerificationStore(root, control_root=root / 'control')
            with self.assertRaisesRegex(StoreError, 'retry-policy-violation'):
                bare.reconstruct_execution_terminals()
            # A new admission carrying the registered proof is refused by a store without the registry.
            started = json.loads(next(store.executions.glob('*/started.json')).read_text())
            replay = {**started, 'execution_id': 'f' * 32}
            fresh = VerificationStore(root, control_root=root / 'fresh-control')  # no prior journals to rescan
            with self.assertRaisesRegex(StoreError, 'retry-policy-violation'):
                fresh.admit_and_reserve('f' * 32, {**replay, 'repository_id': fresh.repository_id})

    def test_unregistered_control_and_consumer_failures_are_violations(self):
        def raising(*_args):
            raise OSError('consumer failure')

        mismatched = []
        cases = {
            'empty registry': ((), {}),
            'other gate': ((control(calls=mismatched),), {'gate_id': 'other-gate'}),
            'other command': ((control(calls=mismatched),), {'command': './other.sh'}),
            'evidence raises': ((control(collect_evidence=raising),), {}),
            'run dir check raises': ((control(check_run_dir=raising),), {}),
            'evidence mismatch': ((control(collect_evidence=lambda worktree, item: {'schema_version': 1}),), {}),
        }
        for name, (controls, overrides) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as temp:
                root = pathlib.Path(temp).resolve()
                store = VerificationStore(root, control_root=root / 'control', retry_controls=controls)
                with self.assertRaisesRegex(SupervisorError, 'retry-policy-violation'):
                    self.execute(store, root, **overrides)
                self.assertFalse(list(store.executions.glob('*/started.json')))
        self.assertEqual([], mismatched)  # the consumer's evidence code never runs for another gate or command

    def test_facts_check_the_evidence_against_the_registration(self):
        item = control()
        known = registry((item,))
        evidence = item.collect_evidence(pathlib.Path('.'), item)
        facts = retry_policy_facts('forbid', True, [item.control_id], registry=known, gate_id=item.gate_id,
                                   command=item.command, control_evidence=evidence)
        self.assertTrue(facts['retry_free'])
        for key, value in (('config_sha256', 'd' * 64), ('wrapper_sha256', 'd' * 64),
                           ('registered_inputs_sha256', 'd' * 64), ('strict_max_retries', 1),
                           ('fail_on_passed_after_retry', False), ('config_path', 'other.cfg')):
            with self.subTest(key=key):
                facts = retry_policy_facts('forbid', True, [item.control_id], registry=known,
                                           gate_id=item.gate_id, command=item.command,
                                           control_evidence={**evidence, key: value})
                self.assertFalse(facts['retry_free'])
        for name, kwargs in (('other gate', {'gate_id': 'other-gate', 'command': item.command}),
                             ('other command', {'gate_id': item.gate_id, 'command': './other.sh'}),
                             ('other command hash', {'gate_id': item.gate_id, 'command_hash': 'e' * 64}),
                             ('evidence control', {'gate_id': item.gate_id, 'command': item.command,
                                                   'control_evidence': {**evidence, 'control_id': 'other'}}),
                             ('evidence gate', {'gate_id': item.gate_id, 'command': item.command,
                                                'control_evidence': {**evidence, 'gate_id': 'other-gate'}}),
                             ('evidence command', {'gate_id': item.gate_id, 'command': item.command,
                                                   'control_evidence': {**evidence, 'command': './other.sh'}}),
                             ('extra key', {'gate_id': item.gate_id, 'command': item.command,
                                            'control_evidence': {**evidence, 'extra': 1}}),
                             ('missing key', {'gate_id': item.gate_id, 'command': item.command,
                                              'control_evidence': {k: v for k, v in evidence.items()
                                                                   if k != 'strict_max_retries'}})):
            with self.subTest(name):
                kwargs.setdefault('control_evidence', evidence)
                self.assertFalse(retry_policy_facts('forbid', True, [item.control_id], registry=known,
                                                    **kwargs)['retry_free'])
        command_hash = hashlib.sha256(item.command.encode()).hexdigest()
        self.assertTrue(retry_policy_facts('forbid', True, [item.control_id], registry=known, gate_id=item.gate_id,
                                           command_hash=command_hash, control_evidence=evidence)['retry_free'])
        self.assertFalse(retry_policy_facts('forbid', True, [item.control_id], registry={}, gate_id=item.gate_id,
                                            command=item.command, control_evidence=evidence)['retry_free'])
        noop = hashlib.sha256(b'python3 -c pass').hexdigest()
        self.assertTrue(retry_policy_facts('forbid', True, ['no-internal-retries'], registry={},
                                           command_hash=noop)['retry_free'])

    def test_malformed_registry_is_refused(self):
        for bad in ([object()], [control(), control()], [control(wrapper_sha256='x')], [control(inputs={})],
                    [control(inputs={'/etc/x': SHA, 'build.cfg': SHA})], [control(inputs={'../x': SHA, 'build.cfg': SHA})],
                    [control(run_namespace='a/b')], [control(run_namespace='..')], [control(collect_evidence=None)],
                    [control(gate_id='')]):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'invalid-retry-controls-registry'):
                registry(bad)


if __name__ == '__main__':
    unittest.main()
