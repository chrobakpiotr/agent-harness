"""agent-harness: agent execution, lifecycle and evidence library.

Supported API (ADR 0001, amendment 2026-10-18; pinned by tests/test_supported_api.py): agent_harness.contract;
agent_harness.execution; from agent_harness.qualification only qualify, probe_digest, DEFAULT_IMAGE and
DEFAULT_TARGET; agent_harness.qualification.reference.reviewed_reference; agent_harness.retry_control.RetryControl;
and the agent-harness CLI. Every other module or name, including qualification's probe and report modules, is
internal. Importing this package must have no side effects.
"""

__version__ = "0.18.0"
