# Owners for the "undecided" Showcase modules — proposal (2026-10-07)

- Status: **accepted** (2026-10-07). Until a packet moves a module it stays in Showcase (ADR 0001).
- Inputs: master plan scope split (library: shared execution/lifecycle/evidence, provider adapters, validation and
  safety; Showcase: project rules, spec/VC, Java/Gradle gates, application); `docs/migration/source-map.md`;
  Showcase `HEAD` sizes and imports.

| Module | Lines | Depends on | Proposed owner after cutover | When | Why |
|---|---|---|---|---|---|
| `eval.py` (engine) | 215 | stdlib only | library | any time (leaf; parity move) | Behavioural evals of the harness itself (constitution #26); no project rules |
| `evals/*.json` (suites) | — | `eval.py` | **Showcase** | stays | Suites name Showcase features (`SHIP-PLATFORM-001`, resilience spec): consumer data, like profiles |
| `verification_contract.py` | 352 | `runner`, `telemetry`, `trust` | library | after `runner` moves | Validates verification contracts (generic SDD validation); needs the provider adapter first |
| `design.py`, `wayfinder.py` | 689 / 1,110 | `harness`, `runner`, `telemetry`, `trust`, `design`, `verification_contract` | library | after the `harness.py` split and `runner` | Generic SDD process tooling (design gate, discovery); tied to the lifecycle CLI |
| `control_plane.py` | 247 | stdlib (network intake: GitHub/Jira) | **Showcase** | stays (revisit after MVP) | Read-only issue intake, not execution; network access widens the library's trust surface |

Consequences if accepted:

- 06b keeps these in Showcase (the shim covers only already moved modules); each "library" row needs its own packet
  with parity, in the order: `eval.py` engine → `runner` (provider adapters, needs a packet; after 04b) →
  `verification_contract.py` → `harness.py` split → `design.py`, `wayfinder.py`.
- `eval.py` is the only one that could move now; its value is small until a library consumer runs harness evals,
  so moving it can wait for a reason.
