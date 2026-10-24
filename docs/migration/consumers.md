# Consumer matrix (AH5-05)

| Consumer | agent-harness version / SHA | API used | Capability track | Authority store | Status |
|---|---|---|---|---|---|
| `examples/minimal-consumer` | built from the checkout in `scripts/check-wheel.sh` | `agent_harness.contract`, `agent_harness.execution` | fake (scripted) and controlled (local process) | evidence root in a temp dir | runs outside the checkout on every check |
| agent-benchmark | `v0.14.0` = `cc9d33a4feac3f1462e7fe65cdabec3389a70f3a` (its `pyproject.toml`, adoption `e103af9`, handover `23fe652`; stays on 0.14.0 for v0.15.0, v0.16.0 and v0.17.0, which change nothing it uses (its decisions, 2026-10-20 and 2026-10-23, handovers `5ebeea2` and `6187d1e`; it re-pins when a release changes `contract`, `execution` or `qualification`); a test guards its use of the supported API) | `agent_harness.contract` (incl. limit and error constants), `agent_harness.execution` (`launch`, `ScriptedBackend`, `ProcessBackend`, `AgentCliBackend`, `QualifiedDockerBackend`, `QUALIFIED_LIMITS`), `agent_harness.qualification` (`qualify`, `DEFAULT_IMAGE`), `qualification.reference.reviewed_reference`, CLI `constitution --check` | agent runs: fake, controlled and `AgentCliBackend` (`isolation_level` stated per result); grading: **qualified**, through a reviewed grading session on the Docker Desktop Linux guest (`QualifiedDockerBackend`, `probe_digest`, ADR 0002 amendment) | none | constitution 1.0.1 verbatim, checked in its CI. AB5-07 pilot graded offline on the qualified target. A release that does not change the supported API does not affect it |
| Showcase | `v0.18.0` = `6d1c69a170dee8638b5535aed3f9aadb57845ba6` (lifecycle/incident adoption `dfbcbbd8baa5ff74d54631869da13b0d50ad210f`; RetryControl API adoption `422cc0c4e00717c16bc0a6023c214afbd8473b3c`; test-only correction `ac552f0097214e339124a7f21b64174b2c0c4e3b`; `grants` extra) | lifecycle checks/transitions via exact-pin compatibility shim; public `agent_harness.retry_control.RetryControl`; fail-closed recovery-incident adapter | verification runtime: library runtime through shims; `NOT_QUALIFIED`; origin admission and completion blocked | own (`.agent-state`, `.agent-runs/control`), with Showcase-owned recovery callbacks | Exact code SHA `ac552f0097214e339124a7f21b64174b2c0c4e3b`; main CI `40104353741`, Agentic SDD CI `40104353595`, CodeQL `40104353607`, Scorecard `40104353720` all succeeded. Dependency Review was skipped on push, not passed. Harness's 14b-2 evidence is 295/299; the four known unpassed cases (two baselines and environment-only workspace/sandbox-doctor) and their baseline evidence are detailed in the [Showcase handover](https://github.com/chrobakpiotr/showcase-application/blob/main/docs/reviews/showcase-handover-ah5-14b2-adoption-2026-10-24.md). Rollback of all adoption commits restored exact parent tree `d7097d50c068ce7f2bb5cff2d95c3cdf166b9010`, full 0.17.0 pin, and prior wrapper. Benchmark stays on v0.14.0. Incident fix is adopted. F1 remains separate; AH5-14c stays blocked. S30-06 remains `DEFERRED / NOT QUALIFIED`, consumer disabled. |

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
