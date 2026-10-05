# Consumer matrix (AH5-05)

| Consumer | agent-harness version / SHA | API used | Capability track | Authority store | Status |
|---|---|---|---|---|---|
| `examples/minimal-consumer` | built from the checkout in `scripts/check-wheel.sh` | `agent_harness.contract` | fake (consumer-side) | none | runs outside the checkout on every check |
| agent-benchmark | `v0.1.0` = `e57fda8477675fd0722878e4993b19651a6aedc4` (pinned in its `pyproject.toml`) | `agent_harness.contract` only (guarded by its `test_harness_port`) | fake-offline | none | adapter `harness_port` in progress (AB5-06a) |
| Showcase | — | — | — | own (`.agent-state`, `.agent-runs/control`) | does not consume the library before AH5-06; default entrypoint unchanged |

No consumer holds a second authority store: the library resolves Showcase's roots read-only (ADR 0003) and
writes none of them.

Open for the hardened track: a launch/cancel API and a qualified backend (AH5-04b/04c). Until then every
consumer execution is fake and must be labelled so.
