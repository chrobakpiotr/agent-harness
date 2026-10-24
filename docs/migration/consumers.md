# Consumer matrix (AH5-05)

| Consumer | agent-harness version / SHA | API used | Capability track | Authority store | Status |
|---|---|---|---|---|---|
| `examples/minimal-consumer` | built from the checkout in `scripts/check-wheel.sh` | `agent_harness.contract`, `agent_harness.execution` | fake (scripted) and controlled (local process) | evidence root in a temp dir | runs outside the checkout on every check |
| agent-benchmark | `v0.14.0` = `cc9d33a4feac3f1462e7fe65cdabec3389a70f3a` (its `pyproject.toml`, adoption `e103af9`, handover `23fe652`; stays on 0.14.0 for v0.15.0, v0.16.0 and v0.17.0, which change nothing it uses (its decisions, 2026-10-20 and 2026-10-23, handovers `5ebeea2` and `6187d1e`; it re-pins when a release changes `contract`, `execution` or `qualification`); a test guards its use of the supported API) | `agent_harness.contract` (incl. limit and error constants), `agent_harness.execution` (`launch`, `ScriptedBackend`, `ProcessBackend`, `AgentCliBackend`, `QualifiedDockerBackend`, `QUALIFIED_LIMITS`), `agent_harness.qualification` (`qualify`, `DEFAULT_IMAGE`), `qualification.reference.reviewed_reference`, CLI `constitution --check` | agent runs: fake, controlled and `AgentCliBackend` (`isolation_level` stated per result); grading: **qualified**, through a reviewed grading session on the Docker Desktop Linux guest (`QualifiedDockerBackend`, `probe_digest`, ADR 0002 amendment) | none | constitution 1.0.1 verbatim, checked in its CI. AB5-07 pilot graded offline on the qualified target. A release that does not change the supported API does not affect it |
| Showcase | `v0.17.0` = `137b0ba057c4d69c94fc1d8b04b0af8d3b0b039e` (Showcase adoption `87e4ad688b17970dbd0afdc660d4a04ed6d0351e`; `grants` extra) | lifecycle state access and moved completion readers/integrity checks through the compatibility shim; all other moved modules through exact-pin shims | verification runtime: the library's, through the shims; NOT_QUALIFIED; origin admission and completion blocked | own (`.agent-state`, `.agent-runs/control`) | Current Showcase checkpoint is `792dfade2775b9d3d6cef5b4020380d84fd1a2c4`, which includes the 0.17.0 adoption and a separate frontend audit fix. Exact adoption CI `40076326938` failed on the old frontend audit and a transient Docker Hub 503 pulling Keycloak; the E2E rerun on that SHA passed. Descendant CI `40077527346`, Agentic SDD `40077527344`, CodeQL `40077527462`, and Scorecard `40077527301` passed. Dependency Review was skipped on push. Rollback of `87e4ad6` restored the exact `8ba5231` parent tree, including the full 0.16.0 pin and previous wrapper. Incident lookup fix `ff06352` is pending a future release and separate Showcase adoption. See `docs/specs/release-0.17.0/packet.md` and the Showcase [adoption handover](https://github.com/chrobakpiotr/showcase-application/blob/e8df24f39aa80b6b13b4feae96bc80bf8ef4cf72/docs/reviews/showcase-handover-ah5-14b1-adoption-2026-10-24.md). Later releases are re-pins under `cutover.md`. Moved modules are frozen copies (ADR 0001, amendment 2026-10-18) |

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

v0.18.0 (`6d1c69a`; AH5-14b-2, AH5-API-1, fail-closed incident lookup) is released; Showcase's adoption is pending
(`docs/specs/release-0.18.0/packet.md`). agent-benchmark stays on v0.14.0.
