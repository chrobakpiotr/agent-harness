# AH5-08 — Move the verification runtime

- Status: **accepted**. This is step 3 of moving the Showcase harness into the library, and the owner ordered it
  on 2026-10-18. 08a is **done**, released as v0.10.0; 08b follows.
- Source: Showcase `49ab97f`. The moved paths are frozen, read-only here. ADR 0001: decision 1 (after a packet
  moves a module, the library owns it), decision 3 (explicit roots) and decision 4 (no consumer imports).
- Order: 08a and 08b each ship in their own release. Showcase adopts a release with its cutover (AH5-06b) or in a
  later re-pin.

## Findings (2026-10-18)

Status of each module at `49ab97f`:

| Module | Lines | Coupling to Showcase |
|---|---|---|
| `verification_sandbox.py` | 1401 | Lines 31–174 already moved (`agent_harness.executables`). Gradle environment in `_safe_env`: `GRADLE_USER_HOME`, `GRADLE_OPTS`, `AGENTIC_SDD_GRADLE_RO_DEP_CACHE`. `./gradlew` worktree wrapper in `build_plan`. The protected roots hard-code `tooling/agent-harness` and `docs/specs`. |
| `verification/workspace.py` | 220 | None, apart from the barrier text. No caller outside tests (diagnostic-only, ADR 0001). |
| `verification/supervisor.py` | 1102 | Gradle retry evidence (`_registered_gradle_retry_evidence`, `_gradle_user_home_has_init_scripts`). Imports `verification_command.CommandResult`. |
| `verification_command.py` | 352 | Consumer policy: an allowlist of `./gradlew`, `python3`, `npm` and others, `PROFILE_SCRIPTS`, and `executable_binding_for_command`. `CommandExecutionBackend` and `run_command` are generic. |
| `verification/executor.py` | 504 | Calls `harness.load_validated`, `load_state`, `admit_verification_execution`, `consume_verification_launch` and `terminalize_verification_execution`. Imports `verification_command` and `machine_outcomes`. |

Callers outside the moved set use these sandbox names: `_safe_env`, `build_plan`, `ExecutionIdentity`,
`PreparedExecution`, `PosixProcessGroupBackend`, `BackendQualification`, `qualifies_v2`, `_sha256_file`, and the
executable names.

## AH5-08a — sandbox and workspace

- Allowed paths:
  - `src/agent_harness/sandbox.py`
  - `src/agent_harness/verification/workspace.py`
  - `tests/test_sandbox*.py`
  - `tests/test_verification_workspace.py`
  - `tests/test_backend_qualification.py`
  - `migration/showcase-wrapper/verification_sandbox.py`
  - `migration/showcase-wrapper/verification/workspace.py`
  - `migration/showcase-wrapper/tests/test_cutover_wrapper.py`
  - `docs/migration/{provenance,cutover}.md`
  - `docs/specs/AH5-08/`
  - `pyproject.toml`
  - `CHANGELOG.md`
- Seams. Each fails closed, is listed in the provenance doc and has a test. The Showcase shim supplies Showcase's
  policy, so Showcase behaviour is unchanged:
  1. **`environment_policy(env, sandbox_home)`**: adds consumer variables to the isolated environment. Its keys are
     also forwarded by bubblewrap `--setenv`. The library default adds nothing. The shim adds the Gradle variables
     and the read-only Gradle cache verbatim.
  2. **`worktree_wrappers`**: a mapping from a worktree-relative `argv[0]` to its error code. The wrapper is
     resolved inside the worktree with Showcase's regular-file, no-symlink and executable checks. The library
     default is empty, so a path-like `argv[0]` stays refused by `resolve_executable`. The shim passes
     `{'./gradlew': 'worktree-gradle-wrapper-invalid'}`.
  3. **`authority_roots`**: the paths, relative to the primary checkout, that a child may not write. The library
     default is `.agent-runs`, `.agent-state`, `docs/specs` and `.git`, plus the Git common directory. The shim
     adds `tooling/agent-harness`. Any ambiguity in resolving the primary checkout still returns `()`, and the
     strong backends refuse that.
- AC1: every other line keeps the `49ab97f` behaviour. The ported Showcase sandbox, workspace, doctor and
  backend-qualification tests pass in the library suite, except tests whose driver stays in Showcase; those are
  listed with the reason. Each seam has a test, and a mutation of each seam default fails its test.
- AC2: the cutover rehearsal at `49ab97f` with the new shims gives the same results as the AH5-06b-0 rehearsal.
  Every new difference is listed with its cause.
- AC3: `scripts/check-wheel.sh` and CI are green, and a fresh-context evaluation passes.

### Evidence 08a (2026-10-18)

The rehearsal used library `1b546f5` with the shims at that commit, against Showcase `49ab97f`. Showcase `HEAD`
moved to `3786776` during the run through other work; `tooling/agent-harness` is unchanged. Results, logs, the
rollback output and the isolated reruns are in `evidence-rehearsal-08a-49ab97f.tar.gz`.

- **AC1.**
  - 70 tests OK across `test_sandbox`, `test_sandbox_doctor`, `test_backend_qualification`,
    `test_verification_workspace` and `test_executables`.
  - Each seam guard was mutated, and each mutation fails its test. That covers the policy that replaces base or
    passthrough keys, the loader and interpreter prefixes, NUL and empty keys, the derived `policy_keys`, an
    undeclared or default wrapper, consumer roots that are dropped or traversing, and a removed default root.
  - The wheel check results are in `docs/specs/release-0.10.0/packet.md`.
- **AC2, counts.** Base 917 tests (905 ok, 3 FAIL, 9 ERROR). Wrapped 933 tests (896 ok, 29 FAIL, 8 ERROR).
- **AC2, ok on base but not on wrapped:**
  - The 6 removals already listed for 0.9.0.
  - The new 08a removals in the cutover guide: 11 backend-qualification tests, the 4 doctor tests, 1 t900 test and
    5 of the 8 listed sandbox tests. The other 3 listed sandbox tests patch `_codex_helper`, `_bubblewrap` and
    `_macos_sandbox_exec`, so through the shim their outcome depends on which sandbox tools the host has. They passed
    in this run and failed in an earlier one, and they stay on the list.
  - The 2 `test_verification_command` tests, which the guide adapts. With only the patch-target change, Showcase's
    `test_verification_command` passes 11/11 through the shims.
  - `test_m4_1_18` and `test_m4_1_19`: concurrency timing; each passes 3/3 alone on both sides.
- **AC2, CI.** The only exit-code difference is `eval.py run --suite adversarial`:
  - A002 runs `test_verification_sandbox.py`.
  - A006 runs the authority and store modules.
  - A007 runs the sandbox, doctor and supervisor modules.

  All three fail only through tests on the removal list.
- **AC2, rollback.** wrapped → base, base → wrapped and base → base are all ok.
- **AC3, independent evaluation.**
  - First verdict: pass with amendments. Four findings:
    1. two `verification_command` tests missing from the cutover lists;
    2. seam checks that no test caught;
    3. loader and interpreter variables accepted by the environment policy;
    4. documentation gaps.
  - All four are addressed in `17fc6c8`.
  - Re-check verdict: **pass with one amendment**. Following the guide's removals of the whole doctor module would
    fail Showcase CI, because the adversarial eval A007 runs `test_verification_sandbox_doctor.py` by path and
    `test_eval.py` pins that path. The guide now removes the module's four tests and keeps the file. With the
    removals and adaptations applied in a scratch tree, A002, A006 and A007 pass. Also applied:
    - the line-98 wording;
    - case-insensitive reserved names in the environment policy, so `Path` cannot replace `PATH` on Windows.

## AH5-08b — supervisor, command backend and executor

- Status: **done**, released as v0.11.0 (2026-10-18). Source: Showcase `49ab97f`.
- **Finding:** the library store already carries Showcase's retry-control registry. That covers
  `_RETRY_CONTROL_BINDINGS`, the wrapper and input SHA-256s for `apps/ecommerce/...`, `settings.gradle`, `gradlew`
  and the critical PostgreSQL and RabbitMQ scripts, plus the `ecommerce.gradle` config path checked in
  `_retry_policy_facts`. It came over with the store in AH5-04a and was not recorded. This is a decision-4 gap, and
  08b closes it.
- Allowed paths:
  - `src/agent_harness/verification/{store,supervisor,executor,command,retry}.py`
  - `tests/test_verification_{supervisor,executor,command,noncacheable,store,retry}*.py`
  - `migration/showcase-wrapper/{verification_command.py,verification/supervisor.py,verification/executor.py,verification/store.py,verification/retry_controls.py}`
  - `migration/showcase-wrapper/tests/test_cutover_wrapper.py`
  - `docs/migration/{provenance,cutover}.md`
  - `docs/specs/AH5-08/`, `docs/specs/release-*/`
  - `pyproject.toml`, `CHANGELOG.md`, `README.md`, `src/agent_harness/__init__.py`
- Ports. Each fails closed, each has a test, and the Showcase shim binds Showcase's policy verbatim:
  1. **Retry controls** (`agent_harness.verification.retry`):
     - `RetryControl(control_id, gate_id, command, wrapper_sha256, config_path, inputs, collect_evidence,
       check_run_dir)`. `collect_evidence` and `check_run_dir` are the consumer's callables.
     - `VerificationStore(retry_controls=...)` validates persisted and new retry proofs against the registry.
       The supervisor collects evidence and checks the run directory through it.
     - The default registry is empty: only the literal no-op control holds, and any other declared control on a
       critical `forbid` gate is a `retry-policy-violation`.
     - The Showcase registry, which holds the constants and the Gradle evidence and init-script checks verbatim,
       moves to `migration/showcase-wrapper/verification/retry_controls.py`.
  2. **Command policy** (`agent_harness.verification.command`):
     - `CommandResult`, `PreparedCommand`, `CommandExecutionBackend` and `run_command` move.
     - The backend takes `argv_policy(command, script_context, worktree, cwd)` (the consumer allowlist),
       `blocked_executables` (Showcase's profile scripts, refused at prepare as in the source), and the sandbox
       policy that is passed to `agent_harness.sandbox.build_plan`.
     - `run_command` takes a `store_factory`.
     - `verification_argv`, `PROFILE_SCRIPTS`, `_accepted_script_argv` and `executable_binding_for_command` stay in
       Showcase's `verification_command.py`, now a shim plus policy.
  3. **Lifecycle** (executor):
     - `execute_plan(..., lifecycle=, resolve_execution=, backend_factory=)`. The source's
       `import harness` calls become `lifecycle.load_validated`, `load_state`, `admit_verification_execution`,
       `consume_verification_launch` and `terminalize_verification_execution`.
     - Without a lifecycle, an `authority_context` run is refused with `VERIFICATION_ORIGIN_ADMISSION_UNAVAILABLE`.
       Without a backend factory, the store is used with the library backend and the default argv policy, which
       allows nothing (`command-not-allowlisted`).
- AC1: everything else keeps the `49ab97f` behaviour. The ported supervisor, executor, command and noncacheable tests
  pass in the library suite. Tests whose driver stays in Showcase are listed with the reason. Each port has a test
  and a mutation check.
- AC2: the cutover rehearsal at `49ab97f` gives the 08a results plus the listed 08b differences. The
  post-removal CI eval commands pass.
- AC3: `scripts/check-wheel.sh` and CI are green, and a fresh-context evaluation passes.

### Evidence 08b (2026-10-18)

The rehearsal used library `0a52799` against Showcase `49ab97f` (Showcase `HEAD` was `3786776`;
`tooling/agent-harness` is unchanged). Results, logs, rollback output and isolated runs are in
`evidence-rehearsal-08b-49ab97f.tar.gz`. `84e1f14` then added the executor's `store_factory`, which changes only
when the store is built, and more tests.

- **AC1.**
  - The full library suite passes: 411 tests at `0a52799`; the count at release is in `docs/specs/release-0.11.0`.
  - Seam mutations were applied to the registry, facts, supervisor binding, consumer exceptions, run-dir check, the
    store and `admit_and_reserve` registry, `deny_all`, blocked executables, the executor lifecycle and
    `store_factory`. After the evaluator's amendments, each one fails the suite.
- **AC2, counts.** Base 915 tests (906 ok, 3 FAIL, 6 ERROR). Wrapped 936 tests (899 ok, 30 FAIL, 7 ERROR).
- **AC2, ok on base but not on wrapped.** Exactly the guide's lists:
  - the authority and store removals (6);
  - the backend-qualification (11), sandbox (5 this run), doctor (4) and t900 (1) removals;
  - the three patch-target adaptations: two in `verification_command`, one in `noncacheable`.
- **AC2, Showcase through the shims.** All 88 supervisor tests pass, including the 16 Gradle tests that stay.
  `test_verification_command` passes 11/11 and `test_verification_noncacheable` 7/7 once adapted.
- **AC2, CI.** The only exit-code difference is the adversarial suite: A002, A006 and A007 fail before the
  removals. On a scratch tree with the removals and adaptations applied, the evaluator ran these, and all pass:
  - `harness.py doctor` and `validate-all`;
  - the `verification_contract.py validate` runs;
  - `orchestrate.py --plan`;
  - the trust and telemetry CLIs;
  - `eval.py run` for the `baseline`, `adversarial` (7/7) and `shipping-preflight` suites.
- **AC2, rollback.** wrapped → base, base → wrapped and base → base are all ok.
- **AC3, independent evaluation.**
  - First verdict: pass with amendments. Four findings:
    1. the executor shim built its store before the authority checks, which made two Showcase tests vacuous;
    2. retry-fact guards no test caught;
    3. one supervisor test kept in Showcase without a reason;
    4. an ambiguous adaptation line in the guide.
  - All four are addressed in `84e1f14`.

