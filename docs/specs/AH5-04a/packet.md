# AH5-04a — Accepted origin and full obligation coverage

- Status: 04a-1 **accepted**; 04a-2 deferred to Showcase.
- Source: master plan AH5-04a; depends on AH5-03b and accepted SDD-OBS contracts. Showcase input `50c18f9`
  (`tooling/agent-harness/` unchanged through `d7ab1f9`; read-only).

## Findings (2026-10-05)

- Showcase already rejects a forged or rehashed plan and incomplete obligation/unit coverage in
  `verification/authority.validate_plan_record`; tests `test_verification_authority`, `_completion_boundary`.
- Origin admission does not exist anywhere: `authority.resolve_execution` raises
  `VERIFICATION_ORIGIN_ADMISSION_UNAVAILABLE` after validating the plan. The plan requires this guard to stay
  until physical execution exists (04b).
- Plan acceptance is lifecycle state: `publish_and_accept`/`resolve_accepted` call
  `harness.accept_verification_plan`/`resolve_accepted_verification_plan`, which write/read `.agent-state`.
  ADR 0003 keeps that store with the lifecycle module, which no packet moves yet.
- Dependency status: SDD-OBS-001 `spec.md` is `DRAFT`; `design/master-closure.json` is
  `implementation-authorized` with `canonical_design_gate: not-pass`; `verification-contract.json` is `accepted`.
- Further location coupling: profile root and `showcase.json` via `__file__` (authority), issuer registry via
  `__file__` (store); `cryptography` needed by `human_grants`.

## Proposed split

### AH5-04a-1 — Move authority/planning unchanged (parity only)

- Allowed paths: `src/agent_harness/verification/{profile,fingerprint,planner,candidate,store,human_grants,
  authority}.py`, their ported tests and fixtures, `pyproject.toml` (`cryptography==49.0.0`),
  `docs/migration/`, `docs/specs/AH5-04a/`.
- Profile root and issuer registry become explicit parameters (ADR 0001 §3); no `__file__` lookup, no
  built-in `showcase` profile.
- The lifecycle calls (`accept`/`resolve accepted`, `load_validated`, `load_state`, `feature_fingerprint`) become
  an explicit injected port; the library ships no implementation and never writes `.agent-state`.
- `resolve_execution` keeps its unconditional guard; completion stays blocked.
- AC: parity of plan records and refusals with Showcase on the same inputs; the guard and completion-boundary
  tests pass unchanged; no import of `harness`; nothing written at import or plan validation.

### AH5-04a-2 — Origin admission (behaviour change)

Not drafted: it adds behaviour, so by ADR 0001 it lands in Showcase first, under SDD-OBS-001, and
then moves here with parity. Plan AC (forged origin, missing independent obligation, controlled happy path
without bypassing authority) belong to that packet.

## Decisions (2026-10-05)

1. The SDD-OBS-001 master closure (`implementation-authorized`) and the accepted verification contract satisfy
   the "accepted SDD-OBS contracts" dependency.
2. Origin admission is implemented in Showcase first (ADR 0001) and moves here afterwards with parity.
3. 04a-1 takes plan acceptance through an injected lifecycle port; the library ships no implementation of it.
4. `cryptography==49.0.0` is the optional extra `grants`, not a core dependency: releases from 47 ship no
   Intel-macOS wheel. `human_grants` imports it lazily; without it the grant tests skip.
