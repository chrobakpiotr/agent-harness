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

    def test_the_package_declares_every_pinned_module(self):
        """The declaration a consumer reads (the package docstring; README and AGENTS.md say the same) names every
        pinned module, and pins nothing it does not declare."""
        import agent_harness
        declared = ' '.join(agent_harness.__doc__.split())
        for module in SUPPORTED:
            with self.subTest(module=module):
                self.assertIn(module, declared)


if __name__ == '__main__':
    unittest.main()
