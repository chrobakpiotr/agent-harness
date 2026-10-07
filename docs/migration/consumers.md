# Consumer matrix (AH5-05)

| Consumer | agent-harness version / SHA | API used | Capability track | Authority store | Status |
|---|---|---|---|---|---|
| `examples/minimal-consumer` | built from the checkout in `scripts/check-wheel.sh` | `agent_harness.contract`, `agent_harness.execution` | fake (scripted) and controlled (local process) | evidence root in a temp dir | runs outside the checkout on every check |
| agent-benchmark | `v0.3.0` = `0ef88c5b5751e18b88a9f7b454823c7bf3012e0b` (pinned in its `pyproject.toml`, its `10a3cc5`) | `agent_harness.contract`, `agent_harness.execution`, CLI `constitution --check` | fake-offline | none | constitution 1.0.1 verbatim, checked in its CI; AB5-05b grading waits for a qualified target (AH5-04b) |
| Showcase | — | — | — | own (`.agent-state`, `.agent-runs/control`) | does not consume the library before AH5-06; default entrypoint unchanged |

No consumer holds a second authority store: the library resolves Showcase's roots read-only (ADR 0003) and
writes none of them.

Offline launch/cancel exists (ADR 0004, `fake`/`controlled`); the hardened track still needs a qualified
backend (AH5-04b/04c). Until then no consumer execution is qualified, and results say so (`isolation_level`).

## Shared agent contract copies (AH5-00b)

Needs `v0.3.0` or later (the command is not in `v0.2.0`). Each consumer keeps a verbatim copy of the constitution of the agent-harness version it pins, and its own
rules in a separate file (Showcase-specific rules stay in Showcase's constitution). In CI, with the pinned
package installed:

```text
agent-harness constitution --check <path of the copy>   # 0 = exact copy, 1 = drifted, 2 = unreadable/usage
```

Create or refresh the copy with `agent-harness constitution > <path>` after re-pinning, through review;
`agent-harness constitution --digest` prints the version and sha256 to record next to the pin. Status:
agent-benchmark keeps a verbatim 1.0.1 copy in `docs/constitution.md`, checked by a unit test in CI (its `10a3cc5`;
its repo rules live in `AGENTS.md`); Showcase has no copy yet.
