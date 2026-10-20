# Consumer matrix (AH5-05)

| Consumer | agent-harness version / SHA | API used | Capability track | Authority store | Status |
|---|---|---|---|---|---|
| `examples/minimal-consumer` | built from the checkout in `scripts/check-wheel.sh` | `agent_harness.contract`, `agent_harness.execution` | fake (scripted) and controlled (local process) | evidence root in a temp dir | runs outside the checkout on every check |
| agent-benchmark | `v0.14.0` = `cc9d33a4feac3f1462e7fe65cdabec3389a70f3a` (its `pyproject.toml`, adoption `e103af9`; a test guards its use of the supported API) | `agent_harness.contract` (incl. limit and error constants), `agent_harness.execution` (`launch`, `ScriptedBackend`, `ProcessBackend`, `AgentCliBackend`, `QualifiedDockerBackend`, `QUALIFIED_LIMITS`), `agent_harness.qualification` (`qualify`, `DEFAULT_IMAGE`), `qualification.reference.reviewed_reference`, CLI `constitution --check` | agent runs: fake, controlled and `AgentCliBackend` (`isolation_level` stated per result); grading: **qualified**, through a reviewed grading session on the Docker Desktop Linux guest (`QualifiedDockerBackend`, `probe_digest`, ADR 0002 amendment) | none | constitution 1.0.1 verbatim, checked in its CI. AB5-07 pilot graded offline on the qualified target. A release that does not change the supported API does not affect it |
| Showcase | `v0.13.0` = `cfd92fcfb0c572b1b462d605709e7be8b7801b71` (its cutover `ea8a54fa`; `grants` extra) | every moved module up to AH5-12a, through the compatibility shims (internal modules, safe only at the exact pin) | verification runtime: the library's, through the shims; NOT_QUALIFIED; origin admission and completion blocked | own (`.agent-state`, `.agent-runs/control`) | cut over to v0.11.0 (`ee17ef2`), v0.12.0 (`cbbdbca`) and v0.13.0 (`ea8a54fa`, CI green). Adopting v0.14.0 (AH5-12b) needs its orchestrator change set and the real-CLI checks (`docs/specs/AH5-12/packet.md`). Later releases are re-pins under `cutover.md`. Moved modules are frozen copies (ADR 0001, amendment 2026-10-18) |

No consumer holds a second authority store: the library resolves Showcase's roots read-only (ADR 0003) and
writes none of them.

Offline launch/cancel exists (ADR 0004, `fake`/`controlled`). Grading has a qualified path: `QualifiedDockerBackend`
in a grading session bound to a reviewed tuple and the probe digest. Agent execution and Showcase's verification
launch are not qualified, and results say so (`isolation_level`). The hardened verification track is a separate
capability (AH5-04a-2/04b/04c), and the cutover does not depend on it (ADR 0001, amendment 2026-10-18).
The supported API is listed in ADR 0001's amendment; consumers that import other modules depend on internals.

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
