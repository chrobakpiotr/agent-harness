# Showcase T-002 follow-up: bind executable resolution in the shared verification core (2026-10-11)

- Scope: a follow-up task definition for Showcase SDD-OBS-001, by Harness. The analysis is a read-only reading of the
  T-003 worktree (`1508310` plus uncommitted `runner.py`/`test_runner.py`) and of the T-002 shared core it builds
  on.
- Decision: **T-003 stays open** until VC-016 is covered for both direct and runner verification, or the accepted
  contract is revised. The fix belongs in the T-002 shared core, so direct and runner verification resolve
  executables in one place. It does not belong in the runner.

## Why VC-016 is not covered

VC-016 says untrusted worktree, provider, tracker and runtime content cannot select control-plane execution code.
Its hint names "malicious cwd, environment and import/executable paths". The shared core resolves executables from
the inherited environment in four places:

1. **Backends.** `discover_backends`, `_codex_helper`, `_bubblewrap` and `_macos_sandbox_exec` find `codex`,
   `bwrap`, `sandbox-exec` and `docker` with `shutil.which` on the process `PATH`. A relative entry, an empty entry,
   `.` or a writable directory earlier in `PATH` selects a fake "sandbox", and that fake then runs the command without
   isolation. Qualification binds the resolved path, but it qualifies whatever `PATH` found, fake included.
2. **Commands.** `verification_command` allowlists bare names (`python3`, `python`, `npm`, `npx`, `true`, `git`,
   `docker`, `helm`, `terraform`). The sandbox wrapper then `execvp`s them with the inherited `PATH` (`_safe_env`
   passes `PATH` through) and `cwd=worktree`. So a relative or worktree `PATH` entry resolves `python3` to worktree
   content.
3. **Toolchain variables.** `_safe_env` passes `JAVA_HOME`, `JDK_HOME`, `SDKROOT` and `DEVELOPER_DIR` through
   unchecked. Each of them selects executables (`java`, `xcrun` tools).
4. **The runner's `verify.py` launch.** T-003's `run_verification` starts
   `[sys.executable, HERE/'verify.py', …]` with the inherited environment and `cwd=worktree`. `PYTHONPATH`,
   `PYTHONHOME`, `PYTHONSTARTUP` and the user site directory can therefore inject imports into the control plane.

T-003's tests cover none of these paths, and T-002's tests do not either.

## Follow-up task (T-002 shared core)

Allowed paths: `tooling/agent-harness/verification_sandbox.py`, `verification_command.py`,
`verification/executor.py`, `verification/supervisor.py` and their test modules. The runner and `verify.py` call
sites stay in T-003, which only switches to the shared function.

1. **One resolver.** A single function in the shared core resolves every executable the verifier launches: the
   backends, the allowlisted commands and the Python used for control-plane re-entry. A test asserts there is no
   other `shutil.which` or bare-name launch in the verification modules, `runner.py` or `verify.py`.
2. **A trusted search path, not `PATH`.** The resolver searches only absolute, canonical directories. These are a
   fixed system set (`/usr/bin`, `/bin`, `/usr/sbin`, `/sbin`) plus toolchain directories declared in the bound
   verification profile. It refuses:
   - relative or empty entries;
   - any directory inside a worktree, the control repository's task area, the sandbox home or `$TMPDIR`;
   - any directory, or any parent of it, that is writable by group or others.

   `JAVA_HOME`, `JDK_HOME`, `SDKROOT` and `DEVELOPER_DIR` are passed only when they are validated the same way, or
   when the profile binds them.
3. **Bind, then check.** At planning/admission, record each resolved executable as its canonical path and sha256 in
   the plan or qualification identity, so it becomes part of `backend_identity`/`policy_identity`. At launch,
   resolve again and compare. A mismatch fails closed with a distinct reason and requires re-qualification.
4. **Absolute `argv[0]` everywhere.** Backend argv and the command inside the sandbox are both launched by their
   absolute path, so no `execvp` lookup happens. The child's `PATH` is set to the trusted search path, so a tool's
   own sub-launches (`npm` → `node`, `git` → helpers) cannot reach worktree shims either.
   - `./gradlew` stays worktree-relative on purpose: it is part of the code under test and runs inside the sandbox.
     State this exception explicitly in the contract.
5. **Isolated Python re-entry.** Control-plane Python launches (runner → `verify.py`, supervisor re-entry) use the
   resolved interpreter with `-I` (no `PYTHONPATH`, `PYTHONHOME`, user site or script-directory surprises) and an
   explicit environment allowlist.
   - The interpreter must lie outside every worktree.
   - The child environment never carries `PYTHONPATH`, `PYTHONSTARTUP`, `NODE_OPTIONS`, `LD_PRELOAD`, `DYLD_*`,
     `BASH_ENV`, `ENV`, `GIT_EXEC_PATH` or `JAVA_TOOL_OPTIONS`. `_safe_env` already omits most of these; assert all
     of them in a test.

## VC-016 tests (direct and runner path, each)

- **Fake executables on `PATH`.** `PATH` starts with `.`, with a relative directory, or with a worktree directory
  holding fake `python3`, `git`, `sandbox-exec` and `codex` that each write a marker file. Verification uses the
  trusted binaries or is rejected before launch. No marker exists afterwards.
- **Executable changed after qualification.** A bound executable whose bytes change after qualification (a test copy
  in a profile directory) fails closed at launch.
- **Python injection.** `PYTHONPATH` and `PYTHONSTARTUP` pointing into the worktree, and a `sitecustomize.py` in the
  worktree, have no effect on `verify.py`: a marker proves the injected code never ran.
- **Toolchain variable into the worktree.** `JAVA_HOME` pointing into the worktree is rejected, or is ignored in
  favour of the profile binding.
- **Shadowing package.** `cwd=worktree` with a shadowing `verification/` package in the worktree: the control
  repository's modules are the ones imported.
- **Import graph.** The resolver is the only resolution site (requirement 1).

## Note on T-003's uncommitted timeout change

`run_verification` no longer kills the requester on timeout. It waits on `communicate()` with no bound, trusting the
shared engine to enforce the deadline and drain. If the engine hangs, the runner hangs too. Keep the reason (do not
strand a detached child), but bound the second wait (timeout plus a fixed grace). Past that bound, report `TIMEOUT`
with the drain unconfirmed and fence the worktree. That is, do not evaluate postconditions as if the run were
terminal.

## Sequence

1. Showcase registers this follow-up against T-002's shared core, and Harness reviews it against requirements 1–5
   and the tests above.
2. T-003 rebases on it. It switches the `verify.py` launch to the shared resolver with `-I`, adds the runner-path
   VC-016 tests and resolves the timeout note.
3. Only then can T-003 close. The alternative is a contract revision that explicitly narrows VC-016, accepted
   through the normal spec-change path.

## Registration route (2026-10-12)

**Use `replan-task` on T-003.** No new task, no reopen of T-002, no feature replacement, no `.agent-state` edit.

Why this route fits the accepted contract and the lifecycle:

- **VC-016 already belongs to T-003.** It is in T-003's acceptance criteria, next to VC-010, VC-015 and VC-017.
  T-002's acceptance criteria do not include it. So T-002's completed attempt met its own contract and stays
  untouched. The shared-core change is the work that T-003's existing criterion requires; only T-003's permitted
  surface was too narrow.
- **`replan-task` is the supported operation for this.** It takes a CAS-bound running task (T-003 is `running`,
  attempt 1) and a full in-feature proposal whose `allowed_paths` and `verification` may change. It validates:
  - the paths are safe;
  - the criteria exist in the accepted spec or verification contract;
  - the dependencies are planned tasks.

  The task-ID set and the DAG stay unchanged, and the new packet revision enters T-003's lineage through the normal
  publication path.
- **No parallel write collision.** The shared-core files are owned only by T-002 (an ancestor of T-003) and T-009
  (a descendant). `verify.py` is owned only by T-001 (an ancestor). The validator checks overlapping
  `allowed_paths` only between builders that can run in parallel, so widening T-003 is admissible. T-009 later
  builds on T-003's result.

Alternatives, rejected:
- **Reopen T-002.** T-002 did not fail its contract. `reopen` also refuses while a descendant (T-003) is running,
  and it would prune descendant workspaces, discarding T-003's attempt.
- **Standalone feature.** The DAG does not model cross-feature dependencies. T-003 could not bind the fix's
  checkpoint, and two lifecycles would edit the same files.
- **`reconcile-feature` with a new task ID.** It rejects changes to the task-ID set by design.

Proposal for `docs/specs/SDD-OBS-001/replan-proposals/T-003.json`. Fields not listed stay as they are now:

- `allowed_paths`: keep `runner.py` and `tests/test_runner.py`, and add:
  - `tooling/agent-harness/verification_sandbox.py`, `verification_command.py`, `verification/executor.py`,
    `verification/supervisor.py`, `verify.py`;
  - their test modules (`tests/test_verification_sandbox.py`, `test_verification_command.py`,
    `test_verification_executor.py`, `test_verification_supervisor.py`, and the `verify.py` tests).
- `verification`: keep the runner command, and add T-002's verification command unchanged, so the shared core
  stays green. If any `verify.py` tests exist, add their command too.
- `objective`: append requirements 1–5 above, in one sentence each.
- `test_seam`: append the VC-016 tests above. Each runs on the direct path and on the runner path, and must first
  fail on the current code (red-green-refactor).
- `acceptance_criteria`: unchanged. VC-016 and VC-017 are already listed.

Order:
1. Showcase writes the proposal.
2. Harness reviews it as a contract change before it is applied.
3. `replan-task T-003` runs with `--expected-status running` and the current feature generation, under the normal
   reason and operator attribution.
4. T-003 implements the change, and Harness reviews the result against requirements 1–5, the VC-016 tests and the
   bounded post-timeout wait.

Whether this replan uses any of T-003's retry budget is decided by Showcase's existing replan rules. It needs no
human grant beyond the usual operator attribution.

## Review of the T-003 replan proposal (2026-10-12)

Reviewed `docs/specs/SDD-OBS-001/planning/T-003.json`, sha256 `8d38db8b86f8c08f355b47b3798e16ec18145d8ef7e64db08020c5d1dfed98b4`
(hash verified, file untracked).

Verdict: **accept with one required change.** Run `replan-task` only after the change is made and the file is
re-hashed.

Verified:
- **Identity and criteria.** The ID, role, `depends_on` (T-001, T-002) and `acceptance_criteria` are unchanged;
  VC-016 and VC-017 are listed.
- **`allowed_paths`.** It adds the four shared-core files, `verify.py` and their tests. Overlap check against every
  other builder: the paths are shared only with ancestors (T-001: `verify.py` and `tests/test_verify.py`; T-002: the
  shared core and its tests) and with the descendant T-009 (`runner.py`, executor, supervisor and their tests).
  There is no parallel builder, so the overlap rule holds.
- **`verification`.** T-003's runner command, plus T-002's verification command exactly as in `tasks.json`.
- **Requirements 1–5,** in the objective and the test seam:
  1. one resolver for backends, allowlisted commands and control-plane Python, with a test proving it is the only
     resolution and launch path;
  2. trusted canonical search paths only, refusing relative, empty, worktree, control-task-area, sandbox-home,
     `$TMPDIR` and group/other-writable directories, with the toolchain selectors validated;
  3. path and sha256 bound at planning/admission and checked again at launch;
  4. absolute `argv[0]` and a trusted child `PATH`, with the `./gradlew` exception stated;
  5. control-plane Python launched with `-I` and an environment allowlist.
- **The VC-016 cases.** Both the direct and the runner path are covered and must first fail on the current code:
  fake `python3`, `git`, `sandbox-exec` and `codex` on `PATH`; bytes changed after qualification;
  `PYTHONPATH`/`PYTHONSTARTUP`/`sitecustomize`; `JAVA_HOME` pointing into the worktree; a shadowing package under
  the working directory.
- **Bounded drainage after a timeout.** An unconfirmed drain is never treated as terminal, and no false-green
  postcondition is evaluated.

Required change:
- **R1. `test_verify.py` must gate completion.** The proposal allows edits to `verify.py` and
  `tests/test_verify.py`, but `verification` never runs `test_verify.py`. Direct-path VC-016 tests placed there would
  not gate T-003, and a regression in T-001's `verify.py` tests would go unnoticed. Add T-001's test command
  unchanged: `python3 -m unittest tooling/agent-harness/tests/test_verify.py
  tooling/agent-harness/tests/test_verification_profile.py -v`.

Non-blocking:
- **Name the excluded variables** in the test seam, so "import/startup and loader injection variables" is testable
  as a fixed list: `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP`, `PYTHONUSERBASE`, `NODE_OPTIONS`, `LD_PRELOAD`,
  `LD_LIBRARY_PATH`, `DYLD_*`, `BASH_ENV`, `ENV`, `GIT_EXEC_PATH`, `JAVA_TOOL_OPTIONS`.
- **State that the interpreter lies outside every worktree.** The interpreter used for control-plane Python must be
  resolved like every other executable, and must lie outside every worktree. The objective implies this through the
  single resolver; saying it explicitly avoids a `sys.executable` shortcut.

## T-003 scope for plan/admission binding (2026-10-12)

**Decision: replan T-003 again, widening `allowed_paths` to the profile, plan and admission modules.** The objective
and the test seam stay unchanged. Showcase is right that the active packet cannot meet requirements 2 and 3:
- the profile model has no field for declared toolchain directories;
- the plan does not bind executable identities;
- the immutable execution records in `verification/store.py` are outside the packet.

Alternatives considered, rejected:
- **Bind only in the sandbox qualification** (`policy_identity` in `verification_sandbox.py`). That detects a swap
  between qualification and launch, but not between planning and launch. The objective says planning/admission.
- **A control-repository constant instead of profile-declared toolchain directories.** That is security-equivalent,
  since both are control-repository content. But it changes the objective's wording, so it would need a spec change
  through the normal path. It is not worth that when widening is admissible.
- **Ambient `PATH`, or deferring the binding.** Both would violate VC-016 and the active packet.

Admissibility. Owners of the added paths, against T-003:

| Owner | Relation to T-003 | Paths |
|---|---|---|
| T-001 | ancestor | `model.py`, `profile.py`, `planner.py`, `serialization.py`, `test_verification_profile.py`, `test_verify.py` |
| T-002 | ancestor | `store.py`, `test_verification_store.py` |
| T-006 | descendant | `verification-profiles/showcase.json` |
| T-009 | descendant | `authority.py`, `store.py`, `test_verification_store.py` |

No builder that runs in parallel with T-003 owns any of them, so the overlap rule holds. T-006 and T-009 later build
on T-003's result.

Proposal content. Keep everything in the active T-003 revision and add:
- **`allowed_paths`:**
  - `verification/model.py`, `verification/profile.py`, `tests/test_verification_profile.py`: a toolchain-directory
    field in the profile, validated by requirement 2's rules (absolute, canonical, outside every worktree, the
    control task area, the sandbox home and `$TMPDIR`, and not writable by group or others, including ancestors);
  - `verification/planner.py` and `verification/serialization.py`: each execution unit's command identity carries
    the resolved canonical path and sha256 of its executable and of the backend executable;
  - `verification/store.py` and `tests/test_verification_store.py`: admission and plan-store validation of those
    bindings, and the launch-time recheck against the stored binding;
  - `verification/authority.py` and `tests/test_verification_authority.py`, only as far as plan-authority
    construction needs the new fields;
  - `verification-profiles/showcase.json`, `tests/test_verification_profile_showcase.py` and
    `tests/test_verification_profile_applicability.py`: declare the real toolchain directories, so Showcase's own
    `./gradlew`, `npm` and `python3` verification keeps working once ambient `PATH` is gone. Without this, every
    later task's verification fails closed until T-006.
- **`verification`:** keep the current commands, including T-001's and T-002's. Add
  `python3 -m unittest tooling/agent-harness/tests/test_verification_authority.py
  tooling/agent-harness/tests/test_verification_profile_showcase.py
  tooling/agent-harness/tests/test_verification_profile_applicability.py -v`.
- **`test_seam`:** append the following.
  - A plan or admission record without executable bindings, or with bindings that no longer match, is refused. An
    existing plan from before this change is stale and must be planned again; it is never upgraded silently.
  - Changing a bound executable between planning and launch fails at launch.
  - A profile toolchain directory that violates requirement 2 is refused when the profile is loaded.

Unchanged: the ID, `depends_on`, `acceptance_criteria` and the objective.

Process: Showcase writes the proposal and sends its path and sha256. Harness reviews it, and Showcase then runs
`replan-task T-003 --expected-status running` with the current CAS values. T-003 stays incomplete until the plan and
admission binding and the launch-time checks are implemented and verified.

## Review of the second T-003 replan proposal (2026-10-12)

Reviewed `docs/specs/SDD-OBS-001/planning/T-003.json`, sha256 `92f2b478e142d95bc42b18dc38f690b9a324a30c285eca1a589812a102ed166f`
(hash verified). It was compared field by field with T-003's active packet revision `sha256:fc07592a…`. T-003 is
`running`, attempt 2, feature generation 1.

Verdict: **accept.** Showcase may run `replan-task T-003 --expected-status running` with the current CAS values and
operator attribution.

- **Unchanged.** `title`, `objective`, `role`, `depends_on`, `risk_tags`, `acceptance_criteria` and `test_mode` are
  byte-identical to the active revision.
- **`allowed_paths`.** It adds exactly the twelve paths approved in "T-003 scope for plan/admission binding" (above)
  and removes nothing:
  - `model.py`, `profile.py`, `planner.py`, `serialization.py`, `store.py`, `authority.py`;
  - `verification-profiles/showcase.json`;
  - the profile, store, authority, profile-showcase and applicability tests.
- **`verification`.** Nothing is removed. The R1 command for T-001 (`test_verify.py`, `test_verification_profile.py`)
  is still present, next to the runner and T-002 commands. The authority, profile-showcase and applicability command
  is added, and all three test modules exist.
- **`test_seam`.** The active text is kept verbatim as a prefix. The appended sentence covers:
  - plan or admission refusing absent or mismatched executable bindings;
  - old plans planned again, never upgraded in place;
  - an executable swapped between planning and launch failing at launch;
  - an invalid toolchain directory refused when the profile is loaded.

T-003 still completes only after the plan and admission binding and the launch-time checks are implemented and
verified. Harness reviews that implementation.

## Adding the profile schema to T-003 (2026-10-12)

Request: add `tooling/agent-harness/schemas/verification-profile.schema.json` to T-003's `allowed_paths`. T-003 is
`running`, attempt 3, on revision `sha256:55c47faa…`.

Verdict: **accept, as a one-path replan.** `objective`, `acceptance_criteria`, `verification` and `test_seam` stay
unchanged.

- **The path is needed.** The schema's top level is `additionalProperties: false`, with exactly `schema_version` and
  `gates`. `test_verification_profile.py`'s `test_profile_schema_fields_match_runtime_gate_loader` asserts that exact
  set. So the approved toolchain-directory field cannot be added to the profile model without the schema, and T-003's
  required verification would fail.
- **The overlap rule holds.** The only other owner is T-001, an ancestor. That test file is already in T-003's scope.
- **Version the change explicitly.** T-003 chooses one of these, states it, and tests it:
  - keep `schema_version` 1 with the field optional, where an absent field means no declared toolchain
    directories (the system set only);
  - or move to `schema_version` 2 with the field required, updating `showcase.json` in the same change.

  Either way, a plan built from a profile of the other shape is stale and must be planned again, as the seam
  already requires.

Process: the same as before. Showcase writes the proposal (the active revision's content plus this path), sends its
sha256, and runs `replan-task T-003 --expected-status running` after Harness confirms that the diff is exactly this
one path.

Confirmed (2026-10-12): proposal sha256 `9d62ac1979f8b8dcd5c1bbd51daa90f8e24dd4db6879f0d1cbd6737181ae2024` (hash
verified) against the active revision `55c47faa…` (T-003 `running`, attempt 3, generation 1).
- **The only difference** is the added path `tooling/agent-harness/schemas/verification-profile.schema.json`.
- **Everything else is identical:** `title`, `objective`, `role`, `depends_on`, `risk_tags`, `acceptance_criteria`,
  `verification`, `test_mode`, `test_seam` and `agent_profile`. The proposal's `id` equals the packet's `task`
  (T-003).
- **Versioning:** schema version 1 with an optional `toolchain_directories`, where an absent field means no declared
  toolchain directories. That is one of the two accepted options. It must be covered by the profile tests and the
  schema-parity test.
- **Next step:** run `replan-task T-003 --expected-status running`.

## Review of the T-003 implementation (worktree diff `a74c583b…`, 2026-10-12)

Reviewed the uncommitted diff in `SDD-OBS-001/T-003` on base `b6a023c`. The sha256 matches. All 16 changed files
are within the active revision's `allowed_paths`.

Verdict: **changes required (B1–B5).** T-003 must not complete yet.

### Verified as designed

- **`resolve_executable`/`trusted_executable_directories`:**
  - absolute canonical directories only;
  - the worktree, the sandbox home and `$TMPDIR` are excluded;
  - a directory, or any of its ancestors, that is writable by group or others is refused;
  - relative or `.`-style names are refused;
  - the resolved executable must lie in a trusted directory, and is returned with its sha256.
- **The environment.** `_safe_env` drops the inherited `PATH` and sets the trusted one. `JAVA_HOME`, `JDK_HOME`,
  `SDKROOT` and `DEVELOPER_DIR` pass only when validated and declared by the profile. Everything else is an
  allowlist.
- **The profile.** Schema v1 gains an optional `toolchain_directories`, validated and fingerprinted. The schema
  and the parity test are updated.
- **Planning binding.** `execution_plan_record` stores `executable_bindings` (gate, `command_hash`, path and
  sha256). `validate_plan_record` re-hashes them and re-resolves against the repository, and `resolve_execution`
  rebuilds the record and compares it, so a swap before resolution gives `PLAN_BINDING_MISMATCH`.
- **The `./gradlew` exception** is explicit: a regular file inside the worktree, not a symlink, bound by digest.
- **Bounded drain** after a timeout reports `UNCERTAIN`, never success.
- **Tests rerun by Harness** on an export with the diff applied, in `python:3.13.16-slim` with cryptography
  49.0.0:
  - runner: 25 OK;
  - store, executor, supervisor, command and sandbox: 136 OK (the supervisor module included);
  - authority, profile-showcase and applicability: 26 OK;
  - `test_verify` and profile: 2 failures (B5).

### Required changes

- **B1. The runner's control-Python launch is broken in real use.** `[python, '-I', HERE/'verify.py', …]` fails
  with `ModuleNotFoundError: No module named 'verification'` on every interpreter, 3.13.16 included. The cause:
  `-I` removes the script's directory from `sys.path`. Reproduced both on macOS and in the Linux container.
  `test_runner.py` mocks `subprocess.run`, so it never sees this.
  - Fix: keep `-I`, and have `verify.py` put its own resolved control directory on `sys.path` explicitly (it is
    trusted control code). Alternatively, launch a fixed `-I -c` bootstrap that does the same.
  - Add a runner test that really starts the control Python, without mocking the launch.
- **B2. The wrong interpreter is selected.** The runner resolves `python3` by name from the trusted directories.
  On this host that is `/usr/bin/python3`, i.e. Python 3.9.6: it cannot even import `verification/supervisor.py`
  (`str | None` raises `TypeError`), and it has no `cryptography`. `showcase.json` declares no toolchain
  directories.
  - Fix: use the running control interpreter (`sys.executable`), validated and digest-bound by the same resolver
    rules (absolute, canonical, outside excluded roots, secure directories). That is the interpreter the direct
    path already runs under. Alternatively, declare the pinned interpreter's directory in `showcase.json`.
  - Test: the runner launches exactly the validated control interpreter.
- **B3. The plan's binding is never compared at launch.** `CommandExecutionBackend.prepare` reads
  `expected_executable_binding` and refuses a `plan_id` without it. But `VerificationSupervisor.run` calls
  `backend.prepare(…)` with neither `plan_id` nor the binding, so that code path is dead. The launch check
  (`verify_executable_binding` in `launch`) only re-hashes the binding resolved moments earlier in the same
  `prepare`. An executable replaced after `resolve_execution`, either by new bytes or by shadowing in an earlier
  trusted directory, is launched without detection.
  - Fix: pass the unit's plan binding from the executor through `supervisor.run` into `backend.prepare(plan_id=…,
    expected_executable_binding=…)`.
  - Test: replace the executable after the plan is resolved and before launch, through the real executor,
    supervisor and bridge chain. Expect a refusal with nothing spawned. The current test hand-builds a
    `SandboxPlan`.
- **B4. Requirement 1 is not met.** `verification_sandbox.discover_backends()`, the qualification-time discovery of
  `codex`, `bwrap`, `sandbox-exec` and `docker`, still uses `shutil.which` on the inherited `PATH`. The test the
  seam requires, proving that the shared resolver is the only resolution site, is missing.
  - Move `discover_backends` to `resolve_executable`, and add that test, covering the verification modules,
    `verify.py` and `runner.py`'s verification path.
  - `runner.py`'s `shutil.which('codex'/'claude')` and `cli_version` concern agent-CLI launch, not verification.
    Exempt them by name in the test.
- **B5. No environment passes all four required commands.** On Linux, `test_verify.py`'s
  `test_literal_control_paths_are_never_observed_or_opened` fails for `.GIT/config` and `.Agent-State/…`. That
  failure predates T-003: the base `b6a023c` fails it identically, because the test assumes a case-insensitive
  filesystem. On macOS, the supervisor module cannot import, since there is no cryptography 49.0.0 wheel for this
  host.
  - T-003 owns `test_verify.py` and `planner.py`. The safer fix is to refuse case variants of control path names on
    every platform; the alternative is to make the test platform-correct.
  - Completion evidence must then show all four commands green in one recorded environment.

### Non-blocking

- `/usr/local/bin` was added to the fixed system set (`/usr/bin`, `/bin`, `/usr/sbin`, `/sbin` were agreed). It is
  root-owned here and in the Python images, but state that explicitly.
- `_secure_directory` checks only group/other write. Consider also requiring the owner to be root or the current
  user.
- The process backend's gate (`[sys.executable, '-c', gate, …]`) runs outside the sandbox without `-I`. No
  injection is possible: a worktree `sitecustomize.py`/`usercustomize.py` was tested and not loaded, and the gate's
  environment is allowlisted. Add `-I` anyway, for consistency with requirement 5.

## Third T-003 replan request: `fingerprint.py` and the backend-qualification test (2026-10-12)

T-003 is `running`, attempt 4, on revision `sha256:6208bb…`. Showcase reports that B1–B4 are implemented, with
runner 26, the shared core 142 and authority/profile 26 OK in one Linux environment, and three B5 failures left.

Verdict: **accept `verification/fingerprint.py`; do not add `tests/test_verification_backend_qualification.py`.**

- **`tooling/agent-harness/verification/fingerprint.py`: accept.**
  - It is needed: the protected prefixes `('.git', '.agent-state')` are compared case-sensitively at line 83, which
    is where B5's production fix belongs.
  - The only other owner is T-001, an ancestor.
  - It is covered by T-003's existing `test_verify.py` command.
- **`tooling/agent-harness/tests/test_verification_backend_qualification.py`: reject.**
  - Its owner is T-900, the independent evaluator.
  - It is not run by any task's `verification`, T-003's included, so it does not gate T-003.
  - A builder rewriting the evaluator's own test would weaken the independence that T-900 exists for.
  - Instead, T-003 proves the new discovery behaviour in its own scope (`tests/test_verification_sandbox.py`):
    discovery uses the shared resolver, ignores the inherited `PATH`, and discovery alone still never qualifies a
    backend.
  - T-003's completion handoff states that `discover_backends` no longer calls `shutil.which`, so T-900's test
    mocking it is stale and T-900 updates it during evaluation.

Proposal: the active revision `6208bb…` plus `tooling/agent-harness/verification/fingerprint.py` in
`allowed_paths`. Nothing else changes, including the `objective` and `verification`. Append to `test_seam`:

> Case variants of protected control paths (`.git`, `.agent-state` and their nested forms) are refused consistently
> on case-sensitive and case-insensitive filesystems. Backend discovery uses the shared resolver, ignores the
> inherited `PATH`, and discovery alone never qualifies a backend; this is tested in T-003's own sandbox tests.

Send the proposal's sha256. Harness confirms that the diff is exactly that path plus this sentence before
`replan-task`.

Note: T-003 is on attempt 4 against `max_rework_attempts: 2`. Showcase confirms, under its existing rules, that
these replans are accounted for correctly and are not silently consuming or bypassing the rework budget.

Confirmed (2026-10-12): `replan-proposals/T-003.json`, sha256 `d9902a1763a38e5087305d373fd30d1893e43c97b7ce3d8343d8ee7d95a477dc`
(hash verified), against the active revision `6208bb…` (T-003 `running`, attempt 4).
- **The only differences:**
  - the added path `tooling/agent-harness/verification/fingerprint.py`;
  - `test_seam`, which keeps the active text verbatim and appends the accepted sentence (the same wording, only
    without code formatting).
- **Everything else is identical:** `id`, `title`, `objective`, `role`, `depends_on`, `risk_tags`,
  `acceptance_criteria`, `verification`, `test_mode` and `agent_profile`.
- **Attempt 4 is accounted for.** The state records one consumed owner retry grant for T-003, which explains attempt
  4 under `max_rework_attempts: 2`. Showcase states that a running-task replan does not consume an attempt.
- **Next step:** run `replan-task T-003 --expected-status running`.

## Review of the T-003 B1–B5 implementation (diff `fe150f9e…`, 2026-10-12)

T-003 is `running`, attempt 5, on revision `sha256:7019712b…`. The worktree diff on base `b6a023c` matches the stated
sha256. All 20 changed files are within the active `allowed_paths`.

Verdict: **accept.** T-003 may complete through the lifecycle.

- **B1.** `verify.py` adds only its own canonical directory to `sys.path` before importing control modules. The real
  launch `python -I tooling/agent-harness/verify.py --help` now exits 0 (it failed before on every interpreter).
  `test_runner_launches_verify_under_isolated_active_interpreter` covers it.
- **B2.** The runner launches `bind_control_interpreter(sys.executable)`: the running control interpreter, never
  `python3` from `PATH`. It is validated (absolute, canonical, secure directory, outside the worktree), digest-bound
  and rechecked before launch. There are tests for the active binary and for refusing an interpreter inside the
  worktree.
- **B3.** `_binding_for_gate` selects exactly one plan binding for the gate, matching `command_hash`. The executor
  passes `plan_id` and `expected_executable_binding` through `supervisor.run` into `backend.prepare`, which refuses a
  mismatch. `test_resolved_plan_binding_is_forwarded_and_swap_fails_before_payload` and
  `test_plan_id_and_executable_binding_reach_backend_prepare` cover the chain.
- **B4.** `discover_backends` uses the shared resolver. A `shutil.which` patched to raise proves that neither
  discovery nor the command bridge looks executables up any other way. The only remaining `shutil.which` calls are
  `runner.py`'s agent-CLI lookups, exempt as agreed.
- **B5.** `fingerprint.py` treats case variants of `.git` and `.agent-state` as protected on every filesystem. The
  former Linux failures pass.
- **Tests rerun by Harness** on an export with the diff applied, in `python:3.13.16-slim` with cryptography 49.0.0:
  26, 59, 142 and 26 OK, matching Showcase's evidence. In the evidence (`/tmp/showcase-T003-b5-evidence/`) the
  manifest sha256 `3597e11c…` matches, every log's sha256 matches its manifest entry, every log ends `OK` with the
  stated count, and the manifest binds this diff and packet revision.
- **T-900.** `test_verification_backend_qualification.py` fails as expected, because it still mocks `shutil.which`.
  It stays T-900's to update. Keep the note in T-003's completion handoff.

Notes for completion:
- **Bind the evidence durably.** It lives under `/tmp`, so copy it into T-003's completion evidence through the
  normal flow before `/tmp` is cleared.
- **Carried-over non-blocking points:** state that `/usr/local/bin` is in the fixed trusted set, and consider an
  ownership check in `_secure_directory`. A static scan that keeps `shutil.which` out of the verification modules
  would make B4's runtime proof permanent.

## T-003 completion check (2026-10-12)

T-003 is `completed`, attempt 5, at checkpoint `916af0b`.
- **Ancestry.** Its parent is `b6a023c` (T-002), and it descends from `06d7f5d` (T-001).
- **It is the reviewed code.** `git diff --binary b6a023c 916af0b` hashes to `fe150f9e…`, exactly the diff Harness
  accepted (20 files).
- **The evidence verifies.** `evidence/task-completions/T-003-attempt-5/b5-linux/manifest.json` hashes to
  `3597e11c…`. Its four logs match their recorded sha256s (26, 59, 142 and 26 tests), and it binds the same diff.
- **Gap: the evidence is untracked.** `T-003-attempt-5.json` and `T-003-attempt-5/` are untracked in Git. That is
  the same exposure that lost evidence with the original `.agent-state`. Commit them; T-002's completion evidence
  should be committed the same way.
- **Next:** the T-900 independent evaluation, which includes updating the stale `shutil.which` mock in
  `test_verification_backend_qualification.py`.

## Review of the T-003 attempt-5 repair (control-interpreter plan binding, 2026-10-12)

Context: the independent evaluation of `916af0b` (`evidence/evaluations/T-003-attempt-5-independent-followup.json`)
failed T-003 with one required finding. The control-plane interpreter was validated and digest-bound only at launch
(`bind_control_interpreter`). It did not go through the shared resolver, and it was not bound in the plan, as
requirement 3 demands for every executable. **Harness missed this when accepting B2 above:** B2 asked for "validated
and digest-bound by the same resolver rules" and was accepted for a launch-time binding. The finding is correct.

T-003 is `running` for repair, attempt 5, under an active completion correction. The worktree diff on `916af0b`,
`eaa76883…`, matches the evidence manifest's `code_diff_sha256`. All six changed files are within `allowed_paths`.

Verdict: **accept after R1.**

Verified:
- **The binding lives in the plan.** `execution_plan_record` adds `control_executable_binding`, produced by
  `resolve_executable(sys.executable, active_interpreter=True)`. That mode accepts only the exact active
  interpreter: absolute, canonical, outside the worktree, the sandbox home and `$TMPDIR`, in a secure directory, and
  digest-bound. `bind_control_interpreter` is removed, so the shared resolver is again the only resolution path.
- **Validation checks it.** `validate_plan_record` requires the key and re-hashes the binding. It also requires the
  binding to name the current interpreter and, with a repository, recomputes it. The runner's `resolve_accepted`
  reaches `validate_plan_record(…, reconstruct=True)` through `resolve_accepted_verification_plan`.
- **Launch uses it.** The runner launches the plan's `control_executable_binding` after `verify_executable_binding`,
  and refuses a missing binding (`PLAN_BINDING_MISMATCH`). The interpreter is no longer chosen at launch.
- **Plans of the older shape are refused,** because the key set changed. That is consistent with "replanned, never
  upgraded".
- **Tests rerun by Harness** in `python:3.13.16-slim` with cryptography 49.0.0, on an export with the diff applied:
  27, 59, 141 and 28 OK.
  - In the evidence (`evaluations/T-003-attempt-5-repair/manifest.json`, sha256 `64ecfffc…`), every
    `log_sha256` matches and every log ends `OK` with the same counts.
  - The new tests cover:
    - refusal before launch when the control binding is missing or changed;
    - the control interpreter resolved through the shared resolver and bound in the plan;
    - a swap after planning being rejected;
    - the resolver accepting only the exact active interpreter.

Required:
- **R1. Restore the test that an active interpreter inside the worktree is refused.**
  `test_control_interpreter_inside_worktree_is_refused` was removed. Its replacement passes a fake, non-active
  binary located in the worktree, which is refused as `not-active-control-interpreter` before the exclusion is ever
  reached.
  - Mutation check: with the exclusion check in the `active_interpreter` branch disabled, every test in the sandbox,
    authority and runner modules still passes.
  - Add a test in which the active interpreter (`sys.executable`, patched to a copy inside the worktree, or the
    worktree set to contain it) gives `executable-path-excluded`. Do the same for the sandbox home and `$TMPDIR`.

Notes:
- **Evidence.** The evaluation and repair evidence (`evaluations/T-003-attempt-5-independent-followup.json` and
  `evaluations/T-003-attempt-5-repair/`) are untracked. Commit them with the repair, as was done for attempt 5.
- **T-900.** The `shutil.which` mock in the evaluator test stays T-900's follow-up, as the evaluator recorded.

## T-003 repair completion check (2026-10-12)

T-003 is `completed`, attempt 5, at `05b63a6`, which has parent `916af0b`. Its completion correction is recorded,
and the evaluation and repair evidence is committed in `c92fe01`.

- **R1 is resolved.** `test_active_interpreter_inside_worktree_sandbox_home_or_tmpdir_is_refused` asserts
  `executable-path-excluded` for each root. Harness's mutation check: with the active-interpreter overlap guard
  disabled (`verification_sandbox.py` line 127), 3 tests fail.
- **Tests rerun by Harness** on an export of `05b63a6`, in `python:3.13.16-slim` with cryptography 49.0.0: 27, 59,
  142 and 28 OK.
- **New finding, coverage only, production behaviour correct.** Two other exclusion guards are not exercised:
  - line 92, which refuses a toolchain or search directory inside the worktree, the sandbox home or `$TMPDIR`;
  - line 153, which refuses a resolved executable inside those roots.

  With either disabled, all 86 tests in the sandbox, command, authority, profile and runner modules still pass.
  - Likely cause: the tests that should cover them build their roots under `/tmp`, which is world-writable, so
    `_secure_directory`'s writable-ancestor check refuses them first.
  - In the real layout (worktrees under `~/IdeaProjects/…-agent-worktrees`, no world-writable ancestor), only the
    exclusion guard applies, so a regression there would go unnoticed.
  - Line 153 is largely redundant with the trusted-directory containment check. Line 92 is the real defence against
    a profile declaring a directory inside a worktree.
  - Route: T-003 is completed, and `test_verification_sandbox.py` is owned only by T-002 and T-003. Pass this to
    the T-900 evaluator as a finding. Fix it through a T-003 completion correction only if T-900 requires it. The fix
    is small: build those roots with `tempfile.TemporaryDirectory(dir=pathlib.Path.home())`, as other tests here
    already do, so only the exclusion guard can refuse them.
