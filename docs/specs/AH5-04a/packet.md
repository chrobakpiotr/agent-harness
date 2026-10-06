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
4. `cryptography==49.0.0` is the optional extra `grants`, not a core dependency: 49.0.0 ships no Intel-macOS
   wheel (47.x and 48.x ship universal2 wheels). `human_grants` imports it lazily; without it the grant tests
   skip. `check-wheel.sh` installs the pinned extra and fails unless that exact version imports; only on
   Intel macOS it installs `cryptography<49` (48.0.1) instead and prints the version used.
5. The planning benchmark (`benchmarks/verification_planning.py`, source map "with 04a") joins 04a-1. Its limits
   stay as in Showcase and it runs in CI only (`AGENT_HARNESS_BENCHMARK=1`): this development host misses them
   with the Showcase code too, so locally it reports skipped, not PASS.
6. Python floor 3.13 (Showcase target): the moved modules keep their 3.10+ syntax and APIs verbatim; the
   `v0.1.0` tag keeps 3.9 (CHANGELOG, Unreleased).

## Independent evaluation (2026-10-06)

Fresh-context evaluator verdict: **needs-human**, no code defect. Plan IDs, lifecycle accept calls and 11
refusal codes match Showcase on one disposable repo; the guard holds; nothing is written at import or plan
validation; new parameters fail closed. Follow-ups:

- `test_verification_profile_applicability` had been dropped without a record: ported (3 tests, fixture
  profile). `test_verification_profile_showcase` stays in Showcase (consumer policy, `verify.py`).
- `prepare_task_plan` loaded a profile resolving outside `profile_root` (refused only at publish): now refused
  before sealing; `test_trusted_orchestrator_refuses_a_profile_outside_the_root` fails without the check.
- The floor change is recorded as decision 6.
- Open for a human: the AC "completion-boundary tests pass unchanged" is met only in Showcase, which runs its
  own copy; no test here drives the package authority through a real lifecycle (the port is mocked).
- Known: `test_primary_repository_authority_resolution` needs a Git checkout (`check-wheel.sh` always runs
  from one); from a `git archive` export it fails.
