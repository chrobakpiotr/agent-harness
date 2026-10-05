# Changelog

## 0.1.0 — 2026-10-05

- Execution contract v1 (`agent_harness.contract`): `validate_request`, `validate_result`,
  `validate_capability_report`, `validate_repo_context`, `request_digest`, `ContractError`.
- Golden fixtures in the wheel: success, fail, timeout, unknown-terminal, missing-qualification.
- `agent-harness --help/--version` CLI; no other commands.
- Not included: launch/cancel API, execution backends, Showcase runtime modules.
