"""The supported API (ADR 0001, amendment 2026-10-18) exists; removing a name needs a CHANGELOG-noted release."""
import importlib
import unittest

SUPPORTED = {
    # contract: every name a consumer uses (agent-benchmark's harness_port, schema and tests), so removing one needs a
    # CHANGELOG-noted release; the module as a whole is declared supported (ADR 0001, amendment 2026-10-18).
    'agent_harness.contract': ('validate_request', 'validate_result', 'ContractError', 'ERROR_CODES', 'FIRED_LIMITS',
                               'LIMIT_EXCEEDED', 'OUTCOMES', 'request_digest', 'CONTRACT_VERSION'),
    'agent_harness.execution': ('launch', 'ScriptedBackend', 'ProcessBackend', 'AgentCliBackend',
                                'QualifiedDockerBackend', 'QUALIFIED_LIMITS'),
    'agent_harness.qualification': ('qualify', 'probe_digest', 'DEFAULT_IMAGE', 'DEFAULT_TARGET'),
    'agent_harness.qualification.reference': ('reviewed_reference',),
    'agent_harness.retry_control': ('RetryControl',),
}


class SupportedApiTest(unittest.TestCase):
    def test_every_supported_name_exists(self):
        for module, names in SUPPORTED.items():
            imported = importlib.import_module(module)
            for name in names:
                with self.subTest(name=f'{module}.{name}'):
                    self.assertTrue(hasattr(imported, name))


if __name__ == '__main__':
    unittest.main()
