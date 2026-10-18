# Consumer matrix (AH5-05)

| Consumer | agent-harness version / SHA | API used | Capability track | Authority store | Status |
|---|---|---|---|---|---|
| `examples/minimal-consumer` | built from the checkout in `scripts/check-wheel.sh` | `agent_harness.contract`, `agent_harness.execution` | fake (scripted) and controlled (local process) | evidence root in a temp dir | runs outside the checkout on every check |
| agent-benchmark | `v0.8.4` = `9138648e01605ac62763a11140ac82671f7306d8` (its `pyproject.toml`) | `agent_harness.contract` (incl. limit and error constants), `agent_harness.execution` (`launch`, `ScriptedBackend`, `ProcessBackend`, `AgentCliBackend`, `QualifiedDockerBackend`, `QUALIFIED_LIMITS`), `agent_harness.qualification` (`qualify`, `DEFAULT_IMAGE`), `qualification.reference.reviewed_reference`, CLI `constitution --check` | agent runs: fake, controlled and `AgentCliBackend` (`isolation_level` stated per result); grading: **qualified**, through a reviewed grading session on the Docker Desktop Linux guest (`QualifiedDockerBackend`, `probe_digest`, ADR 0002 amendment) | none | constitution 1.0.1 verbatim, checked in its CI. AB5-07 pilot graded offline on the qualified target. Releases 0.9.0 to 0.11.0 do not change its API, so re-pinning is optional |
| Showcase | `v0.5.0` = `16bb93821292096b94889306288ee2caec5f4006` (`tooling/agent-harness/requirements.txt`, `grants` extra) | today: `agent_harness.contract` and `__version__`, from its own qualification report checker. After the cutover: every moved module, through the compatibility shims (internal modules, safe only at the exact pin) | verification runtime: Showcase's own, NOT_QUALIFIED; origin admission and completion blocked | own (`.agent-state`, `.agent-runs/control`) | cutover to v0.9.0 or later is ready (AH5-06b, guide in `cutover.md`). Moved modules are frozen copies (ADR 0001, amendment 2026-10-18) |

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
