"""AH5-API-1: the supported retry-control interface is the library's own type, with its documented contract."""
import hashlib
import json
import pathlib
import unittest

from agent_harness import retry_control
from agent_harness.verification import retry as internal_retry
from agent_harness.verification import supervisor


def control(**changes):
    fields = {'control_id': 'c-1', 'gate_id': 'g-1', 'command': './gradlew test', 'wrapper_sha256': 'a' * 64,
                  'config_path': 'build.gradle', 'inputs': {'z.gradle': 'c' * 64, 'build.gradle': 'b' * 64},
                  'collect_evidence': lambda worktree, control: {'ok': True},
                  'check_run_dir': lambda run_dir, environment: None}
    fields.update(changes)
    return retry_control.RetryControl(**fields)


class RetryControlTest(unittest.TestCase):
    def test_it_is_the_library_type_not_a_copy(self):
        self.assertIs(internal_retry.RetryControl, retry_control.RetryControl)
        self.assertEqual(['RetryControl'], retry_control.__all__)

    def test_inputs_digest_is_the_documented_canonical_digest(self):
        item = control()
        expected = hashlib.sha256(json.dumps({'control_id': 'c-1', 'inputs': {'build.gradle': 'b' * 64,
                                                                               'z.gradle': 'c' * 64}},
                                             sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                                             allow_nan=False).encode('utf-8')).hexdigest()
        self.assertEqual(expected, item.inputs_digest())
        self.assertEqual(item.inputs_digest(), control(inputs={'build.gradle': 'b' * 64, 'z.gradle': 'c' * 64}).inputs_digest())
        self.assertNotEqual(item.inputs_digest(), control(control_id='c-2').inputs_digest())

    def test_a_callback_exception_is_a_retry_policy_violation(self):
        class AdapterRefusal(Exception):  # a consumer's own error type
            pass

        def refuse(worktree, control):
            raise AdapterRefusal('refused')
        registry = internal_retry.registry([control(collect_evidence=refuse)])
        with self.assertRaisesRegex(supervisor.SupervisorError, 'retry-policy-violation'):
            supervisor._registered_retry_evidence(registry, pathlib.Path('.'), 'g-1', './gradlew test', ('c-1',))
        accepted = internal_retry.registry([control()])
        self.assertEqual({'ok': True}, supervisor._registered_retry_evidence(
            accepted, pathlib.Path('.'), 'g-1', './gradlew test', ('c-1',)))

    def test_a_base_exception_is_not_converted(self):
        def stop(worktree, control):
            raise SystemExit(3)
        registry = internal_retry.registry([control(collect_evidence=stop)])
        with self.assertRaises(SystemExit):
            supervisor._registered_retry_evidence(registry, pathlib.Path('.'), 'g-1', './gradlew test', ('c-1',))


if __name__ == '__main__':
    unittest.main()
