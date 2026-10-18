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

## AH5-08a — sandbox and workspace (Showcase `49ab97f`, 2026-10-18)

| Package module | Source blob | Changes from source |
|---|---|---|
| `agent_harness/sandbox.py` | `verification_sandbox.py` `5eed6339`, lines 175–1401 | Imports the resolver from `agent_harness.executables` (lines 31–174) and re-exports it. The unused `shlex` import is dropped, and `re` is added for the wrapper check. **[seam]** three consumer policies, below. `_safe_env` is split into `_isolated_env`, which holds the source body without the Gradle lines, and `_apply_environment_policy`; the passthrough tuple becomes the module constant `_PASSTHROUGH`. Consumer keys therefore come after the base keys, both in the environment and in the bubblewrap `--setenv` list. Nothing serializes or hashes that order: bubblewrap gets no `--clearenv`, the launch receives `plan.env`, and `policy_fingerprint` is computed from sorted paths. |
| `agent_harness/verification/workspace.py` | `e43efc36` | none (the provenance line is added to the docstring) |

New fail-closed seams (ADR 0001, decisions 3 and 4), each with a test and a mutation check:

- **`environment_policy(env, sandbox_home)`** on `_safe_env` and `build_plan`. It adds consumer variables to the
  isolated environment. A key that already exists, a toolchain selector, a malformed name or a non-string value is
  refused with `sandbox-environment-policy-invalid`. The same applies to a passthrough name, even one the host has unset, and to a loader or interpreter hook the isolated base drops: the prefixes `LD_`, `DYLD_`, `PYTHON`, `GIT_` and `NPM_CONFIG_`, and `BASH_ENV`, `ENV`, `NODE_OPTIONS`, `JAVA_TOOL_OPTIONS` and similar. The policy is consumer code, so this is defence in depth. A tool's own option variable, such as `GRADLE_OPTS`, stays the consumer's responsibility. The added keys are forwarded by bubblewrap. The Showcase shim
  passes `gradle_environment`, which holds the source's `GRADLE_USER_HOME`, `GRADLE_OPTS` and
  `AGENTIC_SDD_GRADLE_RO_DEP_CACHE` lines verbatim.
- **`worktree_wrappers`** on `build_plan`. A declared `./name` resolves inside the worktree with the source's
  checks and the consumer's error code. A malformed entry is refused with `sandbox-worktree-wrappers-invalid`.
  Without the entry, a path-like `argv[0]` stays refused by the resolver. The shim passes
  `{'./gradlew': 'worktree-gradle-wrapper-invalid'}`.
- **`authority_roots`** on `derive_protected_authority_roots`, `_bubblewrap`, `_macos_sandbox_exec` and
  `build_plan`. These are consumer roots relative to the primary checkout, added to `.agent-runs`, `.agent-state`,
  `docs/specs`, `.git` and the common directory. An absolute, traversing or empty root is refused with
  `sandbox-authority-roots-invalid`. The shim passes `tooling/agent-harness`, which restores the source's root set
  and policy fingerprint.

Ported tests:
- `test_sandbox`, from `test_verification_sandbox.py` `052e2be5`. The resolver tests are already in
  `test_executables`. The Gradle-cache and issuer-registry-root tests encode Showcase policy; they stay in
  Showcase, where they run through the shim, and here they are replaced by the three seam tests.
- `test_sandbox_doctor`, from `76af8cd7`.
- `test_backend_qualification`, from `67e461c6`. 28 of its 29 tests are ported; R14 drives `verification_command`
  and moves in 08b.
- `test_verification_workspace`, from `84d0ffd3`. The fixture root is resolved, because macOS `/var` is a symlink
  and the store refuses symlinked ancestors. Showcase CI runs on Linux.

## AH5-08b — supervisor, command backend and executor (Showcase `49ab97f`, 2026-10-18)

| Package module | Source blob | Changes from source |
|---|---|---|
| `agent_harness/verification/supervisor.py` | `3e896a36` | **[seam]** The retry-control port below. `_read_regular_worktree_file`, `_gradle_user_home_has_init_scripts` and `_registered_gradle_retry_evidence` move verbatim to the Showcase shim. In their place, `_registered_retry_evidence` matches the registered control's gate and command, then calls the consumer, and the run-directory check goes through the control (`run_namespace`, `check_run_dir`). A consumer exception is a `retry-policy-violation`. `CommandResult` is imported from `.command`. |
| `agent_harness/verification/command.py` | `verification_command.py` `9a83e9b7` | `CommandResult`, `PreparedCommand`, `CommandExecutionBackend`, `run_command` and the errors. **[seam]** The backend takes `argv_policy`, `blocked_executables` and `sandbox` (a namespace, `agent_harness.sandbox` by default), and `run_command` takes `argv_policy`, `store_factory` and `backend_factory`. The default argv policy is `deny_all`: it parses with the source's shell-syntax refusal (`parse_argv`), then refuses with `command-not-allowlisted`. `PROFILE_SCRIPTS`, `_accepted_script_argv`, `verification_argv` and `executable_binding_for_command` stay in Showcase's `verification_command.py`, verbatim. |
| `agent_harness/verification/executor.py` | `88377c9d` | **[seam]** `execute_plan(..., lifecycle=, resolve_execution=, backend_factory=, store_factory=)`. The store is built where the source built it, after the authority and plan-binding checks. The five `harness.*` calls become `lifecycle.*`. An `authority_context` run without a lifecycle and a resolver is refused with `VERIFICATION_ORIGIN_ADMISSION_UNAVAILABLE`. `machine_outcomes` comes from the library. |
| `agent_harness/verification/retry.py` (new) | from store `720053db` | `RetryControl`, `registry()` and `retry_policy_facts()`, which is the source's `_retry_policy_facts` with the registry passed in. The evidence's config path and hashes are checked against the registration, not against Showcase literals. |
| `agent_harness/verification/store.py` | `720053db` | **[seam]** `retry_controls=()`, validated with `registry()`. Persisted and new retry proofs are checked with `retry_policy_facts(..., registry=self.retry_controls)`. Showcase's `_RETRY_CONTROL_*`, `_REGISTERED_GRADLE_BUILD_INPUT_SHA256`, `_NO_RETRY_COMMAND` and `_retry_control_inputs_digest` came over with the store in AH5-04a without being recorded. They are removed from the library and kept verbatim in the shim `verification/retry_controls.py`, and the store shim still exports them. |

The Showcase shims bind Showcase's policy:
- `verification/store.py` binds `retry_controls=RETRY_CONTROLS`.
- `verification_command.py` binds its allowlist, profile scripts and `verification_sandbox` module. They are looked
  up late, so patches of those names apply.
- `verification/executor.py` binds `harness`, `authority.resolve_execution`, Showcase's store and its
  `CommandExecutionBackend`, looked up late.
- `verification/supervisor.py` is an alias.

Ported tests:
- `test_verification_supervisor`, from `9a7e09f2`: 72 of 88. The 16 VC-009 Gradle retry-control tests read or
  copy Showcase's build files and stay in Showcase, where all 88 pass through the shims.
- `test_verification_executor`, from `01b89501`. The two tests that run `python3` pass a test allowlist through
  `backend_factory`, and a lifecycle seam test is added.
- `test_verification_noncacheable`, from `50fea32f`, with the patch targets moved to the library.
- `human_grant_fixture`, from `7d88ce1a`.
- New: `test_verification_retry` (the registry, the facts, and evidence and run-directory checks through the
  consumer) and `test_verification_command` (`deny_all`, the allowlist, blocked executables, the sandbox namespace,
  and the `run_command` factories). Showcase's `test_verification_command` tests Showcase policy and stays there.

## AH5-09 — state snapshots (Showcase `49ab97f`, 2026-10-18)

| Package module | Source blob | Changes from source |
|---|---|---|
| `agent_harness/state_snapshots.py` | `b66a9ed8` | none (the provenance line is added to the docstring). Known Showcase literals, kept for parity: the snapshot and audit commits' `user.name` (`Showcase Agent State Snapshot`/`Audit`). Nothing reads them back. |

- The module is generic. Its refs are `refs/agent-state*`, the state root is `.agent-state` (ADR 0003), and the
  repository identity comes from `remote.origin.url` or the local common directory. It imports only the standard
  library.
- Known limitation: the identity comes from the remote named `origin`, so it is not the common-directory
  `RepoContext` identity of ADR 0003. A consumer whose remote has another name gets a local, path-based identity.
  If `origin` is later added or renamed, the identity flips, and existing snapshots then fail with
  `snapshot-scope-mismatch`. This is a candidate seam for a later packet.
- `state_recovery.py` stays in Showcase. It validates the one-off recovery of Showcase's lost SDD-OBS-001 state
  against Showcase's own incident record, `docs/specs/AGENT-STATE-RECOVERY-001/evidence/`. That is consumer
  history, not shared behaviour.
- Ported tests: `test_state_snapshots`, from `cb5afb4a`, 25 of 26.
  `test_missing_state_restore_error_points_to_audited_discard_command` runs Showcase's `harness.py` CLI and stays
  in Showcase, where it passes through the shim.

## AH5-10 — the eval engine (Showcase `49ab97f`, 2026-10-18)

| Package module | Source blob | Changes from source |
|---|---|---|
| `agent_harness/eval_runner.py` | `eval.py` `ffb0a1f4` | **[seam]** `HERE`, `REPO` and `DEFAULT_SUITES` are removed. `repo_root(anchor)`, `load_suite(value, suites_dir)`, `run_suite(..., anchor=)` and `main(argv=None, *, suites_dir, anchor)` take the consumer's suites directory and a path inside its repository. An anchor outside Git fails closed (`eval runner requires a Git repository`). The source's fallback to the working directory's checkout lives in the Showcase shim (`_anchor()`), where `REPO` is always the checkout. Noted for the Showcase owner, verbatim and unchanged: `run_suite` builds `.agent-runs/evals/<suite name>/<run id>` without validating either part. The shebang and the `__main__` block move to the shim. |

- The suites (`evals/*.json`) stay Showcase-owned, as the owners decided on 2026-10-07. The shim binds
  `DEFAULT_SUITES = evals/` and `REPO = <checkout>`, so `python3 tooling/agent-harness/eval.py …` behaves as
  before.
- Tests:
  - Showcase's `test_eval` checks Showcase's own suite files, so it stays in Showcase, where it passes through the
    shim. Showcase's `eval.py list` and `run --suite baseline` also run through the shim.
  - The library adds `test_eval_runner`: suite validation and refused destructive commands, suite resolution in
    the consumer's directory, results under the anchor's repository, and failure exit codes.

## AH5-11 — the verification CLI (Showcase `49ab97f`, 2026-10-18)

| Package module | Source blob | Changes from source |
|---|---|---|
| `agent_harness/verify_cli.py` | `verify.py` `a8a22113` | **[seam]** `main(argv=None, *, profile_root, store_factory, resolve_execution, execute_plan)`, all required. The `plan` command resolves a logical profile ID under `profile_root`; the source used `<this file>/verification-profiles`. `grant-import` and `run` use the consumer's store factory, resolver and executor; the source imported Showcase's modules. Imports come from the library. The `sys.path` setup, the shebang and the `__main__` block move to the shim. `StoreError` is imported once at module level. |

- Ported tests: `test_verify_cli`, from `test_verify.py` `c5f354e3`, 49 + 1.
  - 45 are planner, fingerprint and serialization tests. They had stayed in Showcase when those modules moved, and
    now run in the library.
  - The 4 CLI tests call `verify_cli.main` in-process with test bindings; the source ran `verify.py` as a
    subprocess or patched Showcase modules.
  - The added seam test shows a logical profile resolves only under the consumer's root.
- Showcase's `test_verify` passes 49/49 through the shim, which binds late. No removals.
