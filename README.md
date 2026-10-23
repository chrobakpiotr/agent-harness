# agent-harness

Shared agent execution contract, extracted from the Showcase agent harness. MIT licensed.

**Scope:**
- the shared execution contract (request, result, usage and capability documents, validators, golden fixtures);
- offline launch/cancel;
- the agent CLI backend;
- target qualification and qualified grading sessions;
- the Showcase harness modules extracted so far: the verification runtime, sandbox, state snapshots and eval engine.

Agent execution and verification launch are not qualified, and results say so. See `CHANGELOG.md`,
`docs/adr/0002-execution-contract.md` and, for the extraction, `docs/migration/`.

## Install

Pin a released tag or commit (no PyPI release):

```text
agent-harness @ git+https://github.com/chrobakpiotr/agent-harness.git@v0.16.0
```

Python ≥ 3.13, no third-party dependencies (Ed25519 grants need the `grants` extra), no side effects on import.

Supported API (ADR 0001, amendment 2026-10-18):
- `agent_harness.contract`;
- `agent_harness.execution`: offline launch/cancel with the scripted fake and the controlled local process (never
  qualified, ADR 0004); `AgentCliBackend`; `QualifiedDockerBackend` and `QUALIFIED_LIMITS` for qualified grading;
- `agent_harness.qualification`: `qualify`, `probe_digest`, `DEFAULT_IMAGE` and `DEFAULT_TARGET`;
- `agent_harness.qualification.reference.reviewed_reference`;
- contract versions 1 and 2 (ADR 0005);
- the `agent-harness` CLI (`agent-harness constitution --check PATH` checks a copy of the shared agent contract for
  drift).

Every other module is internal: the verification runtime, sandbox, state snapshots and eval engine that Showcase
uses through its cutover shims. Internal modules may change in any release.

## Use

```python
import json
from importlib import resources

from agent_harness import contract

fixture = json.loads((resources.files("agent_harness") / "contract_fixtures" / "success.json").read_text())
request, result = fixture["request"], fixture["result"]

contract.validate_request(request)
contract.validate_result(result, request)  # binds the result to this exact request

try:
    contract.validate_result({**result, "request_id": "someone-else"}, request)
except contract.ContractError as error:
    assert error.code == "BINDING_MISMATCH"
```

Errors carry a bounded `code`: `MALFORMED`, `VERSION_MISMATCH`, `BINDING_MISMATCH`, `UNSAFE_PATH`.
Fixtures shipped in the package: `success`, `fail`, `timeout`, `unknown-terminal`,
`missing-qualification`.

Execution `outcome`, harness `completion` and a consumer's grade are three separate facts; the
contract never merges them.

Running coding agents (`AgentCliBackend`), qualifying a grading session (`qualification.qualify`) and grading on the
qualified target (`QualifiedDockerBackend`): see [`docs/usage.md`](docs/usage.md).

## Development

Agents start at `AGENTS.md`. Verification: `scripts/check-wheel.sh`.
