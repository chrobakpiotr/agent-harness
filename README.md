# agent-harness

Shared agent execution contract, extracted from the Showcase agent harness. MIT licensed.

**Scope of 0.1.x:** the execution contract only — request/result/usage/capability documents,
their validators and golden fixtures. There is no launch/cancel API and no execution backend yet;
nothing in this release is a qualified backend. See `CHANGELOG.md` and `docs/adr/0002-execution-contract.md`.

## Install

Pin a released tag or commit (no PyPI release):

```text
agent-harness @ git+https://github.com/chrobakpiotr/agent-harness.git@v0.1.0
```

Python ≥ 3.9, no third-party dependencies, no side effects on import.

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

## Development

Agents start at `AGENTS.md`. Verification: `scripts/check-wheel.sh`.
