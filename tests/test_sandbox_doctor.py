"""Read-only discovery cannot claim qualified verification launch readiness.

Ported from Showcase `tooling/agent-harness/tests/test_verification_sandbox_doctor.py` at `49ab97f` (AH5-08a).
"""
import unittest
from unittest import mock

from agent_harness import sandbox


class SandboxDoctorTest(unittest.TestCase):
    def diagnose(self, system, candidates):
        with mock.patch.object(sandbox.platform, 'system', return_value=system), \
             mock.patch.object(sandbox.shutil, 'which', return_value='/installed/helper'), \
             mock.patch.object(sandbox, 'discover_backends', return_value=tuple(candidates)), \
             mock.patch.object(sandbox, 'qualify_backend') as qualify:
            result = sandbox.doctor()
            qualify.assert_not_called()
            return result

    def test_darwin_discovery_is_not_qualification_or_launch_readiness(self):
        candidates = [sandbox.BackendCandidate(kind, 'unqualified', '/installed/helper', 'darwin')
                      for kind in ('codex-sandbox', 'macos-sandbox-exec')]
        result = self.diagnose('Darwin', candidates)
        self.assertFalse(result['strong_available'])
        self.assertTrue(result['discovered'])
        self.assertTrue(result['qualification_supported'])
        self.assertFalse(result['qualified'])
        self.assertFalse(result['launch_ready'])
        for kind in ('codex-sandbox', 'macos-sandbox-exec'):
            self.assertTrue(result['backends'][kind])
            self.assertEqual({'discovered': True, 'qualification_supported': True,
                              'qualified': False, 'launch_ready': False},
                             result['backend_status'][kind])

    def test_linux_discovery_reports_missing_qualification_dispatch(self):
        candidates = [sandbox.BackendCandidate(kind, 'unqualified', '/installed/helper', 'linux')
                      for kind in ('codex-sandbox', 'bubblewrap')]
        result = self.diagnose('Linux', candidates)
        self.assertTrue(result['discovered'])
        self.assertFalse(result['qualification_supported'])
        self.assertFalse(result['strong_available'])
        self.assertFalse(result['qualified'])
        self.assertFalse(result['launch_ready'])

    def test_docker_discovery_does_not_claim_qualification_or_readiness(self):
        candidate = sandbox.BackendCandidate('docker-container', 'unqualified',
                                             '/installed/docker', 'darwin')
        result = self.diagnose('Darwin', [candidate])
        self.assertTrue(result['discovered'])
        self.assertFalse(result['qualification_supported'])
        self.assertFalse(result['strong_available'])
        self.assertFalse(result['qualified'])
        self.assertFalse(result['launch_ready'])
        self.assertEqual({'discovered': True, 'qualification_supported': False,
                          'qualified': False, 'launch_ready': False},
                         result['backend_status']['docker-container'])

    def test_missing_executables_have_no_readiness(self):
        result = self.diagnose('Darwin', [])
        for key in ('discovered', 'qualification_supported', 'qualified', 'launch_ready',
                    'strong_available'):
            self.assertFalse(result[key])
        self.assertFalse(any(result['backends'].values()))
        self.assertFalse(any(any(status.values()) for status in result['backend_status'].values()))


if __name__ == '__main__':
    unittest.main()
