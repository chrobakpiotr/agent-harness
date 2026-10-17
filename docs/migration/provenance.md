# Provenance of extracted modules

Source: Showcase `tooling/agent-harness/` at `50c18f947031f1b7bd8e8c6276b2a98b9b46ab98`
(unchanged in that directory through `52e1837`). Same licence (MIT) and copyright holder.
Until cutover (AH5-06) Showcase remains the implementation owner of these moved modules; changes go there
first, except fail-closed seams created by explicit roots (ADR 0001 decision 3), marked **[seam]** below.

| Package module | Source path | Source blob | Changes from source |
|---|---|---|---|
| `agent_harness/machine_outcomes.py` | `machine_outcomes.py` | `4944d690` | none |
| `agent_harness/trust.py` | `trust.py` | `0c4ac157` | shebang and unused `import pathlib` removed |
| `agent_harness/telemetry.py` | `telemetry.py` | `01f76609` | provenance/usage part only: `_MANUAL_*`, `record_manual` and `main` (CLI) not moved — they need `verification.store` and the lifecycle module; shebang and the then-unused `re`, `stat` imports removed |
| `agent_harness/repository.py` | `verification/store.py` (`_git`, `_worktrees`, `resolve_control_root`) | `50c18f9` | `StoreError` → `RepositoryError` with the same codes; new `resolve_repo_context` builds a contract `RepoContext` (parity with the source resolver checked on a repo + linked worktree) |
| `agent_harness/verification/__init__.py` | `verification/__init__.py` | `9ca74f28` | none |
| `agent_harness/verification/model.py` | `verification/model.py` | `9200387e` | none |
| `agent_harness/verification/serialization.py` | `verification/serialization.py` | `ef36fd81` | `from trust import` → `from agent_harness.trust import` |
| `agent_harness/verification/profile.py` | `verification/profile.py` | `14ede4f5` | none |
| `agent_harness/verification/fingerprint.py` | `verification/fingerprint.py` | `a29a7df6` | `resolve_control_root` from `agent_harness.repository` |
| `agent_harness/verification/human_grants.py` | `verification/human_grants.py` | `f221ec36` | none |
| `agent_harness/verification/planner.py` | `verification/planner.py` | `b8038d0b` | none |
| `agent_harness/verification/candidate.py` | `verification/candidate.py` | `2b5fd4e4` | `resolve_control_root` from `agent_harness.repository` |
| `agent_harness/verification/store.py` | `verification/store.py` | `597ace86` | `_git`/`_worktrees`/`resolve_control_root` body replaced by `agent_harness.repository`, `RepositoryError` re-raised as `StoreError` with the same code; no default issuer registry (`__file__`), **[seam]** missing registry fails `FAILURE_GRANT_ISSUER_UNAVAILABLE`; new `profile_root` parameter passed to `validate_plan_record` |
| `agent_harness/verification/authority.py` | `verification/authority.py` | `4ac59537` | no `import harness`: `publish_and_accept`, `resolve_accepted`, `resolve_execution`, `prepare_task_plan` take an injected `lifecycle` port (the Showcase `harness` module fits it); profile root is an explicit `profile_root` parameter instead of `__file__`; `prepare_task_plan` takes `profile_id` (no built-in `showcase`) and **[seam]** refuses a profile resolving outside `profile_root` (`ACCEPTED_PLAN_UNAVAILABLE`, `f756e42`), as `resolve_execution` does; `resolve_execution` guard unchanged |
| `benchmarks/verification_planning.py` (repo, not in the wheel) | `benchmarks/verification_planning.py` | `50690851` | imports from `agent_harness.verification`, unused `sys` import removed; recorded Showcase baseline JSON not copied |
| `agent_harness/qualification/q_probes.py` | `qualification/q_probes.py` at `830c5e6` | `f2e02e58` | none (AH5-04c-1; Harness owns it from here) |
| `agent_harness/qualification/q_lifecycle.py` | `qualification/q_lifecycle.py` at `830c5e6` | `705874a6` | none |
| `agent_harness/qualification/b_probes.py` | `qualification/b_probes.py` at `830c5e6` | `1562b0c9` | none |
| `agent_harness/qualification/report.py` | `qualification/report.py` at `830c5e6` | `a8f35d11` | none; `qualify()` in `__init__.py` reuses its helpers with Harness's `policy_digest` and `tuple.probe_digest`; its Showcase CLI defaults are not used |
| `agent_harness/schemas/verification-profile.schema.json` | `schemas/verification-profile.schema.json` | `375b2f5e` | none |

Check a module against its source:

```text
git -C <showcase> show 50c18f9:tooling/agent-harness/<source path> | diff - src/agent_harness/<module>
```

Ported tests: `test_trust` (all), `test_machine_outcomes` (2 of 3; the `verify.py` case moves with it),
`test_telemetry` (4 of 6; the two manual-evidence cases need the lifecycle module),
`test_verification_profile` (the `showcase.json` case uses an inline gate), `test_verification_candidate`,
`test_verification_store` (all), `test_verification_profile_applicability` (all, on the fixture profile), `test_verification_authority` (13 of 15: the executor case moves with 04b,
lifecycle acceptance stays with the Showcase lifecycle module; the lifecycle is a mock port and
`tests/fixtures/verification-profiles/showcase.json` is the source profile `5180d881` as test input).
`test_verification_benchmark` (verbatim, limits unchanged; runs only with `AGENT_HARNESS_BENCHMARK=1`, set in CI:
on a 1.4 GHz i5 the Showcase code itself measures an 11.9–13.6 s median against the 10 s limit).
`test_verification_completion_boundary` drives the Showcase lifecycle CLI and stays there;
`test_verification_profile_showcase` checks Showcase policy and drives `verify.py` (stays in Showcase until a
packet moves it), so it stays too.

Parity (AH5-04a-1): on one disposable repository Showcase `50c18f9` (Python 3.13) and the package
(Python 3.13) produce byte-identical plan records, with and without a task command, and the same
`invalid-verification-plan` refusal for a forged `plan_id`, a dropped obligation or unit, a changed origin
and a traversing `profile_id`.

## Re-sync with Showcase `49ab97f` (AH5-06b-0, 2026-10-17)

The moved modules below changed in Showcase during SDD-OBS-001 (T-001–T-009), while Showcase owned them
(ADR 0001, decision 1). Each was brought over with a three-way merge:
- base: Showcase `50c18f9`, the original copy point;
- ours: the library module;
- theirs: Showcase `49ab97f`.

Every adaptation recorded above is kept. The source blobs are at `49ab97f`:

| Package module | Source blob | Changes from source beyond the rows above |
|---|---|---|
| `agent_harness/telemetry.py` | `de87162a` | Still the provenance/usage part only. The manual-evidence part (`_MANUAL_*`, Ed25519 attestation, `record_manual`, `verify_manual_observation_for_coverage`) and `main` stay in Showcase: they need the issuer registry and the lifecycle module. The import is `agent_harness.verification.serialization`. |
| `agent_harness/verification/model.py` | `c74e4b34` | none |
| `agent_harness/verification/serialization.py` | `a1f0393b` | none (keeps `agent_harness.trust`) |
| `agent_harness/verification/profile.py` | `722c147f` | none |
| `agent_harness/verification/fingerprint.py` | `ffb4f45d` | none (keeps `agent_harness.repository`) |
| `agent_harness/verification/planner.py` | `f2b0ee1b` | none |
| `agent_harness/verification/store.py` | `720053db` | **[seam]** see below. `resolve_primary_worktree` wraps `agent_harness.repository`, which carries the same refactor. |
| `agent_harness/verification/authority.py` | `e32aae69` | **[seam]** `executable_binder` (below). The profile still comes from the explicit `profile_root`/`profile_id`. `command_root` is the explicit repository, never `Path.cwd()`. |
| `agent_harness/schemas/verification-profile.schema.json` | `9cab9361` | none |
| `agent_harness/executables.py` (new) | `verification_sandbox.py` `5eed6339`, lines 31–174 | Verbatim resolver (`resolve_executable`, `verify_executable_binding`, `trusted_executable_directories` and helpers), plus the library-owned `default_executable_binder`. |

New fail-closed seams (ADR 0001, decision 3), each with a test:

- **Issuer registry anchored to the primary checkout.** Showcase's store refuses a caller-supplied registry in
  production (`issuer-registry-override-forbidden`) and derives
  `tooling/agent-harness/human-issuer-registry.json` from the primary checkout. The library keeps that guard. The
  consumer names the path relative to the checkout (`issuer_registry_path`); the library anchors it to the primary
  checkout that `resolve_control_root()` selected, and refuses an absolute or traversing path
  (`issuer-registry-path-invalid`). Without one, there is no registry, and grants fail with
  `FAILURE_GRANT_ISSUER_UNAVAILABLE`.
- **Executable binder injected.** Showcase's authority imports `verification_command.executable_binding_for_command`
  (with `./gradlew` and profile scripts) and `verification_sandbox`'s resolver. Both are Showcase-only, so the
  library cannot import them (decision 4). `execution_plan_record`, `validate_plan_record`, `resolve_execution`,
  `prepare_task_plan` and `VerificationStore` take `executable_binder`. The default binds plain names through
  `agent_harness.executables` and refuses path-like executables. The Showcase shim passes Showcase's binder.
- **No process-cwd root.** `command_root = repository or Path.cwd()` becomes the explicit repository
  (`test_boundaries`).

Ported tests:
- `test_telemetry`: the 10 generic tests. The 11 manual-evidence tests stay with their code in Showcase.
- `test_verification_authority`: 21 tests. The executor tests stay until the executor moves.
- `test_verification_store`: 32 tests. The one supervisor-driven test stays until the supervisor moves. The registry
  test is adapted to the anchored path, and a seam test is added.
- `test_verification_profile` and `test_verification_profile_applicability`, against the refreshed fixture
  profile `660b5897`.
- `test_verification_benchmark`.
- `test_executables`: 7 verbatim resolver and guard tests from `test_verification_sandbox.py` and
  `test_verification_t900_resolver_guards.py`, plus a default-binder test.
