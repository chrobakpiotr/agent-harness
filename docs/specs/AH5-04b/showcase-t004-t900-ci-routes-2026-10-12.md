# Showcase SDD-OBS-001: T-900 coverage scope, CI fixture fallout, T-004 restart route (2026-10-12)

- Scope: Harness routes for three items from Showcase after T-003's repair. The analysis is a read-only reading of
  the repository at `main` `c92fe01`, the lifecycle state and the task definitions.
- Routes:
  1. the two exclusion-guard tests and the stale `shutil.which` mock belong in T-900's evaluation scope;
  2. the five CI failures are fixed as a fixtures-only maintenance follow-up to `805d5bd` on `main`;
  3. T-004 is restarted from the accepted dependency checkpoints, after its pre-incident worktree is archived and
     removed.

## 1. T-900 evaluation scope (executable-resolution coverage)

Showcase confirms the Harness finding (4fd5d72): in the existing test both paths lie under one worktree, so either
guard alone produces the refusal. With roots under `pathlib.Path.home()`, a probe reaches each guard independently.

T-900's evaluation must include two separate tests in `test_verification_sandbox.py`, each with roots under
`pathlib.Path.home()` (no world-writable ancestor), and each failing when only its own guard is disabled:

1. **A declared search directory inside an excluded root** (the worktree, the sandbox home or `$TMPDIR`) raises
   `executable-search-path-excluded`. This is the guard in `trusted_executable_directories`.
2. **An executable under an excluded worktree nested inside a trusted search directory** raises
   `executable-path-excluded`. This is the guard in `resolve_executable`'s search loop.

Also in T-900's scope: `test_verification_backend_qualification.py`'s
`test_discovery_does_not_qualify_a_candidate` still mocks `shutil.which`, although discovery uses the shared
resolver. Its result therefore depends on whether Docker is present. Rewrite it against `resolve_executable`.

T-900 owns `test_verification_backend_qualification.py`. `test_verification_sandbox.py` is owned only by T-002 and
T-003, so if T-900 requires the two guard tests there, it does so through a T-003 completion correction and repair.
T-900 must not add them to T-003's files itself.

## 2. CI fixture fallout from the ancestry guard (`805d5bd`)

Agentic SDD CI #38250839389 fails five success-path tests whose fixtures complete a task without a valid
`base_commit` or dependency checkpoint, which the guard now correctly refuses:

| Test | File |
|---|---|
| `test_m4_03_complete_required_vc_proof_accepts_completion` (×3: `CanonicalCompletionRepairTests`, `CompletionCorrectionTest`, `LegacyRepairCheckpointBindingProtocolTests`) | `tests/test_harness.py` |
| `test_m43_41_49_c2_requires_checkpoint_and_keeps_other_validation` | `tests/test_harness.py` |
| `test_unplanned_legacy_completion_retains_existing_behavior` | `tests/test_verification_completion_boundary.py` |

Route: **a fixtures-only maintenance commit on `main`, reviewed by Harness,** as a follow-up to `805d5bd`.
- **This is the same route as the guard.** `805d5bd`, like the recovery fixes before it, was lifecycle maintenance
  on `main`, outside the SDD-OBS-001 task DAG. It already changed `harness.py`, which is owned by T-004 and T-009.
  Task-path ownership governs what a task attempt may change; it does not reserve files on `main` against the
  maintenance that broke them. The fixture fallout belongs to the change that caused it.
- **Content.** Each fixture records a real committed feature base: a commit created in the fixture repository and
  stored as `base_commit`, or as the dependency's `checkpoint_commit`, from which the completed checkpoint descends.
  - No change to `harness.py`.
  - No weakening of `require_task_commit_ancestry`.
  - No skip and no mock of the ancestry check.
- **Verification.** The five tests pass, plus the full `test_harness.py` and `test_verification_completion_boundary.py`
  modules. CI must be green on the commit. Also add one assertion per fixture class that a checkpoint not descending
  from the base is still refused, so the fixtures cannot drift back to vacuous passes.
- **Effect on T-004 and T-009.** Their attempts start from their dependency checkpoints, not from `main`, so they do
  not contain this maintenance (or `805d5bd`). When they are integrated, their `test_harness.py` and
  `test_verification_completion_boundary.py` must keep these fixtures. State that in their handoffs.

Send the diff for review before committing it.

## 3. T-004: restart on the accepted dependencies

Facts:
- In the new epoch T-004 is `pending`, with 0 attempts, no `checkpoint_commit` and no recorded worktree.
- The existing clean worktree at `0019649` ("agent checkpoint SDD-OBS-001 T-004", 2026-10-09) is the
  **pre-incident** attempt. It descends from none of T-001 `06d7f5d`, T-002 `b6a023c` and T-003 `05b63a6`, and it is
  not in `main`'s history.
- `prepare_task_worktree` correctly refuses it (`WORKTREE_STALE_BASE`).

So there is no accepted T-004 checkpoint in this epoch. `0019649` is historical work to preserve, not a checkpoint
to keep building on. Route, the same as for T-002's stale base:

1. **Preserve it.** Create an archive ref:
   `git update-ref refs/archive/showcase/SDD-OBS-001/T-004/0019649<full-sha> 0019649<full-sha>`, matching the T-002
   archive's naming.
2. **Remove the stale workspace** with the lifecycle `worktree-remove` command, not with `git worktree` or `branch -D`
   directly. Check first that it is clean. Do not edit `.agent-state`.
3. **Start T-004 normally.** `start` builds a new worktree from the dependency checkpoints (T-003 `05b63a6` descends
   from T-002 `b6a023c`, so the merge is a plain descent), and the ancestry guard accepts it.
4. **Use the pre-incident content only as reference.** All of it is new-epoch work, verified fresh by T-004's own
   verification and evaluated independently, as with T-002.
5. **No retry grant is needed.** T-004 has used no attempts in this epoch, and removing a stale pre-epoch worktree is
   not a task attempt.

## Follow-up (2026-10-12): fixture review, the T-900 route corrected, T-004 restart verified

### Correction to section 1 and to earlier notes

T-900's `allowed_paths` is `tooling/agent-harness/tests/**` (plus its evidence directory), and its verification
runs `python3 -m unittest discover -s tooling/agent-harness/tests -v`. Two Harness statements were therefore wrong:
- "`test_verification_sandbox.py` is owned only by T-002 and T-003" (section 1 above). T-900 owns it too, through
  the glob.
- "`test_verification_backend_qualification.py` is not run by any task's verification" (in the third-replan
  decision in `showcase-t002-executable-resolution-followup-2026-10-11.md`). T-900's `discover` runs it.

Harness's ownership checks matched exact file names in the commands and missed the glob and `discover`. The
decision to keep that test out of T-003 stands, for the independence reason given there.

### 2. T-900 guard tests: the route

**No T-003 correction is needed.** The lifecycle refuses a second correction of the same completion, and nothing
here needs one:
- T-900 adds the two exclusion-guard tests (section 1) and rewrites the stale `shutil.which` mock as part of its own
  evaluation, inside its `allowed_paths`. Its `discover` command gates them.
- Put the guard tests in a new T-900-owned module, for example
  `tests/test_verification_t900_resolver_guards.py`, rather than editing T-003's `test_verification_sandbox.py`.
  This keeps the builder's tests and the evaluator's tests apart, so the evaluation's additions are attributable.
- Fixing `test_verification_backend_qualification.py` in place is fine, since it is the evaluator's own test.
- If a new guard test fails, that is an evaluation finding against T-003. Production code is not changed in T-900,
  because T-900 is an evaluator. A required fix then goes through T-003's lifecycle, as a new attempt or reopen under
  Showcase's rules, not through a second correction of the same completion.

### 1. CI fixture fix: accept

Reviewed the uncommitted diff in `tests/test_harness.py` and `tests/test_verification_completion_boundary.py`
(59 insertions, 16 deletions):
- **Real ancestry.** Each fixture commits a feature base (`docs`), then a later task checkpoint (a file in the task's
  path), and records them as `base_commit` and `checkpoint_commit`. The fake checkpoint `'c' * 40` becomes the real
  checkpoint.
- **A genuine stale case.** `assert_stale_checkpoint_refused` uses the repository's root commit, which precedes the
  base, and expects `COMPLETION_STALE_BASE`. It runs in `test_m4_03`, inherited by the three fixture classes. The
  boundary module adds `test_unplanned_legacy_completion_refuses_stale_checkpoint`.
- **`mkdir(..., exist_ok=True)`** in the linked-worktree tests follows from `docs/` now being committed.
- **No change to `harness.py`,** and no skip, mock or weakening of `require_task_commit_ancestry`.
- **Tests rerun by Harness** on an export of `HEAD` with the two files replaced:
  - the four `test_harness` cases (`test_m4_03` ×3, `test_m43_41_49_c2…`) pass;
  - `test_verification_completion_boundary.py` passes 6 of 7.
  - The seventh, `test_real_plan_acceptance_cannot_be_completed_with_legacy_proofs`
    (`SECRET_BEARING_CANDIDATE_UNSEALABLE`), errors identically on the unmodified `HEAD` on this host. It is not
    caused by this diff, and CI does not report it.
  - The module can only be loaded after `test_harness` (it imports `harness` first), which `discover` provides. That
    is pre-existing.
- **Commit once Showcase's full `test_harness` and `test_verification_completion_boundary` run is green,** and record
  the CI run.

### 3. T-004: restart verified

- `refs/archive/showcase/SDD-OBS-001/T-004/00196494…` points at the pre-incident commit.
- T-004 is `running`, attempt 1, and its worktree HEAD is T-003's `05b63a6`.
- Its in-progress `orchestrate.py` change is reviewed when Showcase hands it over.

## Review of T-004 attempt 1 (checkpoint `fa88d52`, 2026-10-12)

`fa88d52`'s parent is `05b63a6` (T-003). It changes exactly T-004's four `allowed_paths`: `harness.py`,
`orchestrate.py`, `tests/test_harness.py` and `tests/test_orchestrate.py`. T-004 is `running`, attempt 1. Its
criteria include VC-016.

Verdict: **changes required (R1).** Everything else reviewed is accepted.

### Verified

- **Admission and the lifecycle lock.** `lifecycle_state_lock` takes the verifier's `repository_lock`, admits
  repository verification, then takes the lifecycle lock and runs the prepared-snapshot redo before yielding.
  `locked_state` uses it by default.
  - Order checked across sites: the repository lock always comes before the lifecycle lock.
  - `assert_repository_verification_drained` takes and releases the repository lock before `locked_state`; it is
    never nested inside the lifecycle lock.
  - `heartbeat` deliberately skips admission (`admission_guard=False`), so a lease can be renewed while verification
    runs.
  - No inversion found.
- **Resume without spending an attempt.**
  - `resume-verification` requires a task `escalated` on `verification-blocked`, the current accepted plan
    reconstructed for exactly this attempt and generation, and the prior passing builder result's digest. It records
    `verification_resume_pending` with `failed` status and operator/reason attribution.
  - `start` then activates the resume without incrementing `attempts` and without relaunching the builder. Before
    activating, it re-resolves the accepted plan and the builder-result digest.
  - The orchestrator's `run_resumed_verification` reruns only the exact accepted plan.
- **Fail-closed result parsing.** `verification_evidence_summary` keeps only allowlisted fields and checks:
  - outcome, category, exit-code consistency;
  - PASS requiring every planned gate, in order, each PASS;
  - gate field formats, and finite, bounded, non-negative numbers.

  Anything else is `MALFORMED_VERIFICATION_RESULT`, and a family or profile mismatch is `PLAN_BINDING_MISMATCH`.
  Raw command output is never persisted.
- **Manual-observation registration.** It runs under `locked_state`, with a feature-generation CAS and a
  re-resolved trusted scope. The record is staged in the snapshot transaction. It is idempotent: an exact replay
  (excluding `registered_at`) reuses the stored bytes, and anything else is `MANUAL_EVIDENCE_OBSERVATION_CONFLICT`.
  The ledger entry is added in the same transaction. The docstring scopes signer authentication to T-005.
- **Tests rerun by Harness** on an export of `fa88d52`, in `python:3.13.16-slim` with cryptography 49.0.0:
  `test_harness.py` gave 308 OK (sharded runner, 6 workers, 125 s), and `test_orchestrate.py` passed (exit 0).

### Required

- **R1 (VC-016). The orchestrator's control-Python launches bypass the executable-resolution controls.**
  `orchestrate.py` starts `verify.py` as `[sys.executable, HERE/'verify.py', 'run', …]`, with `cwd=worktree`, the
  inherited environment, no `-I` and no check against the plan's `control_executable_binding`. It does so in two
  places:
  - `run_started_task` (line 404) — pre-existing at base `05b63a6`;
  - the new `run_resumed_verification` (line 496).

  T-003 secured only `runner.py`'s launch, because `orchestrate.py` was outside its scope. Harness's T-003 reviews
  did not flag this third launch path either. T-004 owns `orchestrate.py` and lists VC-016, so both launches are
  T-004's to fix.
  - Fix: launch the accepted plan's `control_executable_binding` after `verify_executable_binding`, with `-I` and
    `verification_sandbox._safe_env(...)`, exactly as `runner.run_verification` does. Preferably share one helper
    rather than keep two copies. Refuse a missing or changed binding as `PLAN_BINDING_MISMATCH` before launch.
  - Tests, for both orchestrator paths:
    - `PYTHONPATH`/`PYTHONSTARTUP` injection does not reach `verify.py` (marker absent);
    - a changed interpreter after planning is refused before launch;
    - the launched argv is the bound absolute interpreter with `-I`.

### Note

The CI fixture patch on `main` must be preserved when T-004 is integrated, as stated in section 2.

## Review of the T-004 R1 fix (`d4e7e08`, 2026-10-12)

`d4e7e08`'s parent is `fa88d52`. It changes only `orchestrate.py` and `tests/test_orchestrate.py`, within T-004's
paths.

Verdict: **accept after R2.** R1 is resolved.

- **One helper for both paths.** `run_accepted_verification` resolves the accepted plan and requires
  `control_executable_binding`, the profile hash and `verify_executable_binding`. It then launches the bound
  absolute interpreter with `-I`, `verification_sandbox._safe_env(...)` and the same argv as the runner. Both
  `run_started_task` and `run_resumed_verification` use it.
- **Mutation checks** on the `test_orchestrate.py` suite (15 tests): with `-I` removed from the argv, 3 tests fail;
  with `verify_executable_binding` skipped, 2 tests fail.
- **Tests rerun by Harness** on an export of `d4e7e08`, in `python:3.13.16-slim` with cryptography 49.0.0:
  - `test_harness.py`: 308 OK with `run_test_harness_sharded.py --jobs 6`, in 122 s;
  - `test_orchestrate.py`: 15 OK.

  This supplies the passing `test_harness` run that Showcase's stopped sharded run lacked.

Required:
- **R2. Refuse before launching, not after.** `run_accepted_verification` launches `verify.py` before its callers
  check the plan's identity:
  - `run_resumed_verification` checks `accepted.get('task_id') != task_id or accepted.get('task_attempt') !=
    attempt` only after the call. In `fa88d52` that check ran before launch, so this is a regression.
  - `run_started_task` compares `accepted_plan != plan_record` after the launch.

  A mismatched plan is therefore executed before it is refused. Pass the expected `task_id`, `task_attempt` and,
  for a started task, the exact `plan_record` into the helper. Check them after `resolve_accepted_verification_plan`
  and before `subprocess.run`.
  - Test, on both paths: a mismatched task, attempt or plan record raises before any subprocess starts
    (`subprocess.run` asserted not called).

## Review of the T-004 R2 fix (`64b2501`, 2026-10-12)

`64b2501`'s parent is `d4e7e08`. It changes only `orchestrate.py` and `tests/test_orchestrate.py`.

Verdict: **accept.** T-004 may complete through the lifecycle.

- **The checks come first.** `run_accepted_verification` now takes keyword-only, required `expected_task_id` and
  `expected_task_attempt`, plus an optional `expected_record`. It refuses `PLAN_BINDING_MISMATCH` on any mismatch of
  plan ID, task, attempt (which must be an `int`) or the exact record. It does this right after
  `resolve_accepted_verification_plan` and before the binding check and `subprocess.run`.
  - `run_started_task` passes the exact `plan_record` it prepared.
  - `run_resumed_verification` passes the resume's task and attempt.
  - The checks that used to run after the launch are removed.
- **Tests rerun by Harness** on an export of `64b2501`, in `python:3.13.16-slim` with cryptography 49.0.0:
  `test_orchestrate.py` gave 16 OK.
  - Mutation check: with the task-ID check disabled, 1 test fails.
  - `test_harness.py` does not import `orchestrate`, and neither it nor `harness.py` changed since `d4e7e08`, so
    Harness's 308-OK run on `d4e7e08` covers this commit.

On completion: bind the verification evidence into the completion record, committed rather than untracked, and keep
the CI fixture patch on `main` in mind for T-004's integration.

## T-004 completion, the CI fixture commit, and the T-005 route (2026-10-12)

### T-004 and the fixture commit: verified

- **T-004.** `completed`, attempt 1, at `64b2501` (the reviewed R2 commit). The evidence is committed in `c1326ae`:
  `.agent-runs/SDD-OBS-001-T-004-attempt-1/` with the completion record, the environment, and the verification
  stdout, stderr and exit code, all tracked.
- **The fixture maintenance commit `e43a170`** changes only `tests/test_harness.py`,
  `tests/test_verification_completion_boundary.py` and its evidence under
  `.agent-runs/CI-38250839389-fixture-maintenance/`. There is no production change. Since the Harness review, two
  fixture-only additions in the boundary module:
  - The fixture task's `allowed_paths` gains `tooling/agent-harness/**` inside the test's own temporary repository,
    and the checkpoint file is moved there.
  - The accepted-plan fixture passes the real feature base as `base_sha`, instead of the checkpoint. That is the
    correct base for a plan, and it explains why all 7 boundary tests now pass.

  Both are acceptable. It is recorded as passing on Linux (297 harness tests, 6 workers; boundary 7/7). The
  transient Git ref-lock message from a fixture subprocess, with exit code 0, is noise.
- **Integration.** When T-004 is integrated, keep `e43a170`'s fixture changes in both files. T-004's own
  `test_harness.py` work overlaps them, so resolve by keeping both.

### T-005: the expired lease, a casualty of the stale-worktree defect

Facts:
- T-005 is `running`, attempt 1, owner `codex`. It was claimed on 2026-10-11 at 21:20 UTC, in the new epoch, two
  minutes after T-002's stale completion. Its lease expired at 21:57 UTC.
- Its worktree is clean, with HEAD at `38042d5` ("agent checkpoint SDD-OBS-001 T-005", 2026-10-09). That is the
  pre-incident attempt, which merges the pre-incident T-004 `0019649`. It descends from neither accepted T-001
  `06d7f5d` nor accepted T-002 `b6a023c`.
- So this attempt started on a pre-incident worktree through the stale-worktree reuse defect, which `805d5bd` has
  since fixed. Nothing was done in the new epoch: no commit beyond the pre-incident ones, and no dirty file.

Route, using lifecycle commands only and no `.agent-state` edit:
1. **Recover the lease** with `recover-stale` (or `release` by the recorded owner), giving the reason explicitly:
   `lease expired; attempt 1 started on pre-incident worktree 38042d5 through the stale-worktree reuse defect fixed
   in 805d5bd; no new-epoch work performed`.
   - Both commands leave T-005 `failed` with `attempts: 1`; neither gives the attempt back.
   - `recover-stale` acts on every expired lease, so first confirm that T-005 is the only one.
2. **Preserve the history, then remove the stale workspace.** Create archive refs for `38042d5` (and its
   pre-incident merge parent `f052609`, if not already reachable) under `refs/archive/showcase/SDD-OBS-001/T-005/`.
   Then run `worktree-remove T-005`. Otherwise the next start refuses it with `WORKTREE_STALE_BASE`, as the guard
   intends.
3. **Start T-005.** This becomes attempt 2, built from T-002 `b6a023c`, which contains T-001. Use the pre-incident
   content only as reference, verified fresh.
4. **The rework budget.** Attempt 1 was used up by a lifecycle defect, not by builder work. If T-005 later exhausts
   `max_rework_attempts: 2`, the owner may grant one retry under `authorize-retry`, citing this record. Do not grant
   it in advance.

T-005 route checked (2026-10-12):
- **Lease recovered** with the recorded reason; `lease_recovered_at` is 20:56:39 UTC.
- **Archive refs** exist for `38042d5` and `f052609`.
- **The restart is clean:** T-005 is `running`, attempt 2, on a clean worktree at `b6a023c`, which descends from
  T-001 and T-002.
- **No other task** is running.

## Review of T-005 attempt 2 (uncommitted worktree diff on `b6a023c`, 2026-10-12)

The diff (`git diff --binary`, sha256 `cb9faa11…`) changes only T-005's `allowed_paths`, `telemetry.py` and
`tests/test_telemetry.py`. T-005 depends on T-001 and T-002 only.

Verdict: **accept for completion.** End-to-end verification against T-004's real API goes into T-900's evaluation
scope (below).

### Verified

- **The packet command,** rerun by Harness in `python:3.13.16-slim` with cryptography 49.0.0 on an export of
  `b6a023c` with the diff applied: `test_telemetry.py` gave 22 OK.
- **Against T-004's accepted API**, on a combined tree (T-004 `64b2501` plus this diff; the paths do not overlap):
  - `test_telemetry.py` gave 22 OK.
  - Every lifecycle name the adapter uses exists in `64b2501`: `lifecycle_state_lock`, `load_validated`,
    `_load_state_unlocked`, `resolve_manual_review_scope`, `register_manual_observation`, `git_common_dir`.
  - The adapter's exact calls bind against the real signatures (`inspect.signature(...).bind`).
  - `register_manual_observation` returns a dict with `record_path`, which the adapter reads.
- **The adapter's lifecycle use.** It resolves the trusted scope under `lifecycle_state_lock`, captures the feature
  generation, verifies the signed attestation against that scope and the report digest, and stores content-addressed
  snapshots. It then submits through `register_manual_observation` with `expected_feature_generation`.
  - That API re-takes the lock, applies the generation CAS and re-resolves the scope, so the gap between the
    pre-check and the submit is safe.
  - When an API is missing, the adapter fails closed (`MANUAL_EVIDENCE_AUTHORITY_UNAVAILABLE`). It never reads
    `.agent-state` directly and never grants coverage or transitions.

### Gap: the DAG, verified at integration

`record-manual` needs T-004's lifecycle API, and T-004's docstring names T-005 as its caller. But T-005 does not
depend on T-004, so T-005's base cannot contain that API, and its tests stub all of it (`create=True`).

Replanning T-005 to depend on T-004 would move its base and cost a restart. That is not worth it, because:
- the adapter fails closed;
- the signatures are verified above;
- T-900 (an evaluator that depends on every builder and owns `tests/**`) evaluates the merged tree.

**Requirement for T-900:** an unstubbed end-to-end `record-manual` test on the merged tree, built from a real
feature fixture with lifecycle state, a committed issuer registry and a signed Ed25519 attestation. Assert that:
- the record is staged in `.agent-state/manual-observations/<feature>/` together with its ledger entry, in one
  snapshot;
- the feature generation is unchanged;
- an exact replay is idempotent, and a conflicting replay is refused;
- a stale generation (a CAS mismatch) is refused;
- the registration authorizes no transition and no obligation coverage.

If it fails, it is a T-900 finding against T-005 (or T-004), fixed through that task's lifecycle.

### Non-blocking

The adapter depends on T-004's private `_load_state_unlocked`, plus `lifecycle_state_lock`. A public read-only helper
in `harness.py` (for example, one returning the scope and generation under the lock) would make this less brittle
at integration. That is T-004's or T-009's file, and a later change.

## T-005 completion check and the consolidated T-900 scope (2026-10-12)

**T-005: verified.** T-005 is `completed`, attempt 2, at `c06108e`, whose parent is `b6a023c`. `git diff --binary
b6a023c c06108e` hashes to `cb9faa11…`, the reviewed diff, and touches only `telemetry.py` and
`tests/test_telemetry.py`. The completion evidence and logs are committed in `f811efc`.

**What T-900's evaluation must add, all inside T-900's `tests/**` and gated by its `discover` command:**

1. **Executable-resolution guards** (section 1 above). Two tests in a T-900-owned module, with roots under
   `pathlib.Path.home()`, each failing when only its own guard is disabled:
   - a declared search directory inside an excluded root raises `executable-search-path-excluded`;
   - an executable under an excluded worktree nested inside a trusted directory raises `executable-path-excluded`.
2. **The stale discovery test.** Rewrite `test_verification_backend_qualification.py`'s
   `test_discovery_does_not_qualify_a_candidate` against `resolve_executable` instead of `shutil.which`.
3. **End-to-end `record-manual` on the merged tree** (T-005 review), unstubbed, using a real feature and its
   lifecycle state, a committed issuer registry and a signed Ed25519 attestation. Assert:
   - the record and the ledger entry publish in one snapshot;
   - the generation is unchanged;
   - an exact replay is idempotent;
   - a conflicting replay and a stale generation are refused;
   - no transition or coverage is granted.

A failure in any of these is a T-900 finding against the owning builder task (T-003, T-004 or T-005), fixed through
that task's lifecycle. It is never fixed by T-900 editing production code.

## Review of T-006 attempt 1 and its performance finding (2026-10-13)

The T-006 worktree has HEAD `55388d4` (the merge of the T-003, T-004 and T-005 checkpoints). The uncommitted diff
changes four files within T-006's `allowed_paths`:
- `verification-profiles/showcase.json`, which adds `apps/ecommerce/frontend/**` to the aggregate Gradle gate
  inputs;
- the profile-showcase, parity and benchmark tests.

Verdict: **accept for completion. Do not route the benchmark to T-003.**

### The benchmark is not a T-003 regression

`benchmarks/verification_planning.py` times `build_plan` only: 100 gates, 5,000 files, 3 runs after a warm-up.
Harness ran it on Linux (`python:3.13.16-slim`) against an export of T-006's tree with the diff applied:

| Run | Durations (s) | Median (s) | Git calls/run |
|---|---|---|---|
| As is (T-003's `fingerprint.py`) | 8.74, 8.77, 8.82 | 8.77 | 9 |
| With `fingerprint.py` from `b6a023c`, before T-003 | 8.71, 8.84, 8.80 | 8.80 | 9 |

- Both runs are within the limits (median at most 10 s, maximum at most 20 s, at most 12 Git calls).
- T-003's change has no measurable effect.
- Showcase's runs (12.4–24.1 s) varied by a factor of two within one invocation. That points to contention on the
  host at the time, not to the code.
- T-006's packet command passes on Linux: 13 OK, both without and with `CI=true`, which enables the timing
  thresholds.

**Where the time goes.** cProfile of the same run: about 70 % of `build_plan` is glob matching, i.e.
`profile.matches` → `reachable` → `segment_matches`, about 1.0 M calls through `planner.required_nodes`, where every
gate's patterns are evaluated against every file. Fingerprinting (`fingerprint.inputs`) is most of the rest. That
code lives in `profile.py` and `planner.py`, owned by T-001 and, since the second replan, T-003. It is not in
T-003's resolver work.

**The headroom is thin.** 8.8 s against a 10 s limit. Route:
1. **Confirm in real CI.** The Agentic SDD CI runs `discover` on Linux with `CI=true`, so the benchmark's thresholds
   apply there. Record the CI run's benchmark output in the evidence once the branch is integrated.
2. **If CI exceeds the limits,** that is a performance finding against the planner's matching (`profile.py` and
   `planner.py`), not a T-006 defect and not a T-003 regression. Fix it through the owning task's lifecycle, as a
   correction of T-001 or T-003. The fix is to index gate patterns against the file list once, instead of matching
   per gate and per file. Do not weaken the threshold.
3. **For now:** T-006 completes on its packet command, with the Harness Linux measurement (median 8.77 s) cited in
   its evidence, next to the local 23.5 s run and the note that the local run was contended.
