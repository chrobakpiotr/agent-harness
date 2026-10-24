# F1 — fail-closed verification-contract staleness (behaviour change, for review)

- Status: **candidate implemented, for independent review** (branch `f1-contract-staleness`, code `fcd9a94`); the
  design sections below are kept as decided. Separate from the verbatim extraction. AH5-14c stays blocked until F1 is
  implemented, independently evaluated and adopted.
- Decision (master, 2026-10-23): **fail-closed.** A stale required contract, or one whose currency cannot be
  confirmed, blocks `start` and `complete`. Missing proof of currency is never treated as current.
- Finding: `docs/specs/AH5-13/findings-2026-10-20.md`, F1.

## Today

`agent_harness.verification_contract.validate_contract` (0.15.0+, verbatim from Showcase) computes the expected input
hashes with `input_hashes`. If that raises `OSError` or `SystemExit`, it sets `expected = {}` and **skips the whole
staleness comparison**; the other rules still run. Showcase's `harness.validate()` (library `feature.validate`
since 0.16.0) includes those errors, and `load_validated()` refuses an invalid feature.

## Exact conditions (new)

For a contract file that exists (`verification-contract.json`), each of these becomes a validation error:

| # | Condition | Error |
|---|---|---|
| C1 | an input differs from its recorded hash (unchanged) | `verification contract is stale: inputs.<key> does not match current artifact` |
| C2 | the repository root cannot be resolved (`repo_root` fails) | `verification contract currency cannot be established: repository root unavailable` |
| C3 | an input that must be hashed cannot be read (`spec.md`, `plan.md`, the constitution; `design/gate.json` and `design/master-closure.json` when present) | `verification contract currency cannot be established: <input> unreadable (<error class>)` |
| C4 | the contract records no hash for an input that exists (unchanged: already reported by C1 as a mismatch) | as C1 |

A missing required contract is already refused (`missing required verification-contract.json`, unchanged).
**R1, decided (master, 2026-10-23): every present contract** must be valid, readable and current; a missing required
contract blocks; a missing optional contract keeps today's policy. **Not being able to establish whether a contract
is present or current is never treated as an absent optional contract:** an error from the presence check itself
(for example `verification-contract.json` that exists but cannot be stat'ed or read) is reported as C3, not as
"absent".

Error text names the input and the error class only, never file content.

## Where the check runs, relative to the lock and state change

Unchanged location: `validate_contract` → `feature.validate` → `load_validated`. In `cmd_start` and `cmd_complete`,
`load_validated` runs **before** `locked_state` (Showcase `harness.py:6827`/`6832` and `5396`/`5407` at `3649784`):
- a refused `start` takes no lock, makes no state change and uses **no attempt**;
- a refused `complete` takes no lock, changes no task status and **deletes no evidence**;
- the other `load_validated` callers (about 45) refuse the same way, before their own state access.

No second check site is added. Between the check and the lock, the existing feature fingerprint (stored in state,
checked by `validate_loaded_state` under the lock) already refuses a changed spec, plan, tasks, contract, design
config or gate. It does not cover the constitution or `design/master-closure.json`; **review point R2:** whether to
add those two to the fingerprint is a separate change, not part of F1.

## Updating the contract and retrying

There is no automatic refresh, fallback or hidden bypass. The operator:
1. fixes the cause of C2/C3 (the repository, or the unreadable input), or
2. re-authors the contract through Showcase's existing path (`verification_contract.py generate --force`, which
   validates the result before keeping it), then
3. retries `start` or `complete`; the retry is a normal call, with no new authority.

Diagnostics stay available: `harness.py validate <feature>` lists the errors; recovery operations are unaffected,
because they do not go through `load_validated` (to be confirmed per command in the implementation, R3).

## Impact

- **Existing features:** a feature whose contract inputs are all readable is unaffected. Measured before
  implementation: run `validate` on all features under Showcase's `docs/specs` with and without the change; any
  newly refused feature is listed and reviewed before release.
- **Historical records:** none is rewritten. Completed tasks, completion records and evidence stay as they are;
  only new `start`/`complete` calls (and other `load_validated` callers) see the new refusal.
- **agent-benchmark:** does not use this module; no impact.

## Acceptance criteria (proposed)

1. C2 and C3 produce their errors; C1 is unchanged; a fully readable, current contract validates as before.
2. Through Showcase's `cmd_start` and `cmd_complete`: a refusal uses no attempt, changes no state, deletes no evidence
   (tests on a feature with an unreadable constitution and with an unresolvable repository root).
3. No other rule changes; the AH5-13 tests that pinned the fail-open behaviour
   (`test_an_unreadable_input_skips_the_staleness_check_only`) are replaced by fail-closed tests, recorded as the
   behaviour change.
4. The measured impact on Showcase's features is recorded and reviewed.
5. A library release on the owner's order, with a semantic-change note; Showcase adopts it with its usual cutover.

## Consistency between the check and the state change (master point 4)

**What the lifecycle lock protects.** `locked_state` holds the repository verification lock and the lifecycle-state
lock (`flock` on `lock_path`) across load, the transition and `save_state`. It serialises the harness's own state
writers: every lifecycle command and the authority publishers inside the transaction.

**It does not cover the writers of the contract or its inputs.** `verification-contract.json` is written by
Showcase's `verification_contract.py generate` (plain write, then validation, no lock); `design/gate.json` by
`design.py`; `spec.md`, `plan.md`, the constitution and `design/master-closure.json` by people and agents through Git.
None of them takes the lifecycle lock. So moving the staleness check under the lock **would not by itself** close the
window between the check and the state change.

**What already holds today.** Once a feature has state, `validate_loaded_state` (under the lock, before the transition)
compares the stored feature fingerprint with the current one. It covers `spec.md`, `plan.md`, `tasks.json`,
`design.json`, `design/gate.json`, `verification-contract.json` and `wayfinder-handoff.json`: a change to any of these
after the early check refuses the operation under the lock. Not covered: the constitution and
`design/master-closure.json`, and the very first state (`initial_state` records the fingerprint of whatever it reads
under the lock, with nothing to compare against).

**Proposed smallest mechanism: bind the transition to the validated inputs.** The early check (`load_validated`)
records a digest of exactly what it validated: the contract bytes and every input hash it compared (spec, plan,
constitution, gate, closure). Under the lock, at lock entry and before the transition runs any side effect, the
library recomputes that digest; a difference refuses with `verification contract inputs changed during the
operation; retry` and nothing is written. Retrying is an ordinary call.

**Guarantee boundary, stated explicitly.** The state change is based on inputs that were re-observed under the lock
immediately before the transition. Because the writers do not take the lock, a write landing after that
re-observation is **not prevented**; it is detected by the next operation (the fingerprint or the staleness check).
This packet does not claim the race is removed. Making the writers take the lock (generate, design, and human Git
edits) is outside the harness's control and not proposed.

The refusals stay before any state change: a refused `start` uses no attempt, a refused `complete` keeps state and
evidence.

## R2 — constitution and `master-closure.json` (analysis only; separate decision)

| Input | Affects contract validity | Written by `complete`? | Effect of adding it to the feature fingerprint |
|---|---|---|---|
| Constitution (`docs/agentic-sdd/constitution.md`) | Yes: its hash is a contract input (`inputs.constitution_sha256`), so a change makes every present contract stale | Only if a task's `allowed_paths` include it and its commit changes it; then the next operation finds the contract stale, as today | Every feature's state would be refused after any constitution change (a repository-wide protocol change). It is already part of the protocol fingerprint, which state records but `validate_loaded_state` does not compare. |
| `design/master-closure.json` | Yes, when present: its hash is a contract input, and the closure replaces a non-passing gate | No: a human authority record, not task output | A changed closure would refuse the feature's state until it is reconciled; closures are rare and deliberate. |

Neither is added automatically; the binding mechanism above already re-checks both under the lock for the operation
in progress. Adding them to the feature fingerprint is a separate change for a separate decision.

## R3 — recovery and diagnostics (call paths, master point 6)

| Command | Goes through the contract check | With F1 |
|---|---|---|
| `restore-state`, `discard-prepared-snapshot`, `recover-verification`, `doctor` | no | unaffected |
| `validate`, `validate-all` | yes, reports errors | **stay available** and list the C1–C3 errors (diagnosis) |
| `recover-state`, `recover-claim`, `status`, `reset`, `migrate-state`, `reconcile-feature`, `human-resolve`, `resume-verification` | yes, through `load_validated` (refuses an invalid feature) | refused as today for a stale contract (C1), and now also for C2/C3 |

No general F1 bypass is added under the name of recovery. If a recovery command must run while a contract cannot be
verified, that is a recovery-specific decision with its own checks (for 14d), not an exception in F1.

## Concrete mechanism for evaluation (master point 4, 2026-10-24)

The fingerprint and a re-read are not presented as atomicity. The mechanism is an explicit identity recorded with the
transition and compared under the lock; its limits are stated below.

**1. What is validated, and its identity.** For a feature with a present contract, `contract_binding` is the SHA-256 of
the canonical JSON `{"contract_sha256": sha256(verification-contract.json bytes), "inputs": input_hashes(...)}`, where
`input_hashes` covers `spec.md`, `plan.md`, the constitution, and `design/gate.json` and `design/master-closure.json`
when present: exactly what `validate_contract` compares. Computing it fails closed (C2/C3) if any of those cannot be
read. For a feature without a contract (optional): `contract_binding` is `null`, as today's policy.

**2. Where reading, validation, locking and writing happen** (after 14b-2 moves `cmd_start`/`cmd_complete` verbatim
into the library; F1 then changes them there):

| Step | `start` | `complete` |
|---|---|---|
| a. pre-check, unlocked | `load_validated` (all of C1–C3), computes `binding_pre` | same |
| b. lock | `locked_state`: repository verification lock, then the lifecycle `flock` | same |
| c. under the lock, before any side effect | recompute `binding_now`; if `binding_now != binding_pre` → refuse `VERIFICATION_CONTRACT_CHANGED_DURING_OPERATION` | recompute `binding_now`; refuse if `binding_now != binding_pre`, **or** if `binding_now != entry['contract_binding']` (the value recorded at this attempt's start) → `VERIFICATION_CONTRACT_CHANGED_SINCE_START` |
| d. transition | record `entry['contract_binding'] = binding_now` with the attempt | as today |
| e. write | the one `save_state` commit of the transaction | same |

A refusal at (a) or (c) raises before (d)–(e): `start` uses no attempt, `complete` changes no status and keeps all
evidence. Tasks started before F1 have no recorded binding; `complete` for them compares (a) and (c) only and does not
invent a historical value (no rewriting of history).

**3. Writers that respect the lock:** only the harness's own state writers: every lifecycle command through
`locked_state`, and the authority publishers inside its transaction. **Not under the lock:** contract authoring
(`verification_contract.py generate`), `design.py` (`design/gate.json`), wayfinder handoffs, and any person, agent or
Git operation editing `spec.md`, `plan.md`, the constitution or `master-closure.json`.

**4. If the inputs change between validation and the write:**
- between (a) and (c): detected at (c), refused, nothing written; the operator retries;
- between this attempt's `start` and its `complete`: detected at `complete` (c), refused; the operator re-authors the
  contract and restarts the task under the new one (a new attempt by the existing rules), or reverts the input;
- between (c) and (e), a window of the transition's own duration: **not detected by this operation**. The commit
  records `binding_now` as read at (c). The next `start` or `complete` detects the change.

**5. The guarantee, and what stays outside it.**
- *Guaranteed:* every committed `start` records the identity of the contract inputs as read under the lock in the same
  transaction; every committed `complete` was checked, under the lock, against the identity recorded at its attempt's
  start. A completion is never accepted against a contract whose inputs, as observed under the lock, changed since the
  attempt started.
- *Not guaranteed:* the files are not snapshotted atomically. An unlocked write that lands after (c) and before (e) is
  not seen by that operation (only by the next). An edit that changes and then restores the same bytes is invisible
  (equal digest, equal content). The harness cannot make the contract's writers take its lock, and F1 does not try.

**Not in F1:** adding the constitution or `master-closure.json` to the feature fingerprint (R2, separate decision); any
recovery exception (R3).

## Decision and conditions (master, 2026-10-24)

- **Accepted**, with a limited guarantee, for implementation **after** the verbatim 14b-2 extraction, as a separate
  behaviour change. The guarantee, in master's wording: *`complete` admits a completion only if the correctly read
  and validated inputs observed during the check under the lock match the binding recorded at `start`.* It does not
  guarantee an atomic snapshot of the files or their immutability until `save_state`; a change after the check may go
  undetected, and the next operation detects it only if it is still observable and that operation re-checks these
  inputs.
- **Binding rules:** a versioned, unambiguously encoded set of input names, their presence and their digests; the
  validation and the binding use the **same bytes read once** (never validate one read and hash another); under the
  lock the required validation and the binding are recomputed; `start` records the binding with the attempt through the
  existing `save_state`; `complete` requires a match with both its own pre-check and the attempt's recorded binding;
  a read error, an invalid contract or a mismatch refuses with no change to the task's state and no loss of evidence;
  `complete` never overwrites the attempt's binding.
- **Attempts started before F1:** no recorded binding blocks a normal `complete`; nothing is back-filled. The path is
  the existing recovery and a new `start` after validation, which keeps the previous attempt and its evidence;
  historical completions stay readable and are not rewritten. (Spelled out command by command with the
  implementation, R3.)
- **Verification required:** refusal for a change before the under-lock check, a change between `start` and
  `complete`, unreadable inputs, a missing historical binding, and an unchanged attempt that completes; plus
  (master, 2026-10-24) `complete` on unchanged inputs passes, a later integration changes the inputs, and the next
  `start` is refused until the contract is refreshed; the remaining race after the check documented, not claimed
  removed.
- AH5-14c stays blocked until F1 is implemented, independently evaluated and its adoption confirmed.

## The input set (master point 3): analysis before implementation

**Which set.** F1 uses the set the contract's existing staleness check already compares (`validate_contract` →
`input_hashes`, verbatim since AH5-13): `spec.md`, `plan.md`, the constitution, and `design/gate.json` and
`design/master-closure.json` **when present**, plus the contract file itself. This is not the R2 question: R2 is
about adding the constitution and the closure to the **feature fingerprint** that guards stored state, and stays a
separate decision. **For confirmation:** that "the existing accepted set" for F1 means exactly this `input_hashes`
set (the constitution, gate and closure are in it today).

| Input | Why it affects the contract's validity | Changed by normal `start`/`complete`? | Changed by other tasks? | Self-invalidation risk |
|---|---|---|---|---|
| `spec.md`, `plan.md` | the contract's criteria are derived from them (`inputs.spec_sha256`, `plan_sha256`) | no: no lifecycle command writes them | **yes, for some features:** 151 task `allowed_paths` across Showcase's 43 features cover a contract input; most are blanket `docs/specs/<F>/**`, some name `spec.md`/`plan.md` outright (e.g. `APP-ORCH-001` T-001, `DOMAIN-INDEP-001` T-001) | see below |
| constitution | the contract's independent criteria cite it; `inputs.constitution_sha256` | no | only a task allowed to edit `docs/agentic-sdd/constitution.md` | as below |
| `design/gate.json` | the design decision the contract follows; written by `design.py` | no | covered by blanket `docs/specs/<F>/**` paths | as below |
| `design/master-closure.json` | the human authority that replaces a non-passing gate | no | covered by blanket paths; in practice a human record | as below |

**No self-invalidation within one attempt in the orchestrated flow.** `orchestrate.py` calls `start` and `complete` with
the feature directory of the **primary checkout** (`orchestrate.py:677`, `:700`); the task edits its own worktree. A
task's own edits to `spec.md`/`plan.md` reach the primary checkout only when its work is integrated, so they do not
change the inputs its own `complete` checks.

**Concrete conflicts, reported before implementation (no exception, no automatic rebind):**
1. **A task whose purpose is to change the spec or plan** (allowed paths naming `spec.md`/`plan.md`). Once its work
   is integrated, the contract is stale (C1). That already holds today, and every later operation refuses until the
   contract is re-authored. F1 adds nothing here, but it is a real workflow cost for such features.
2. **New with F1:** a parallel attempt whose inputs change because *another* task's work is integrated while it runs
   is refused at `complete` (`VERIFICATION_CONTRACT_CHANGED_SINCE_START`), and has to be restarted after the contract
   is re-authored. That is the intended guarantee, but it changes how features with spec-editing tasks run.
3. **A manual `complete` run from inside the task worktree** (feature directory under the worktree) would read the
   worktree's own, possibly edited, `spec.md`/`plan.md` and refuse its own completion. The orchestrator does not do
   this; manual use might.

These go to master for a decision before that part of F1 is implemented.

## Decisions on the conflicts (master, 2026-10-24)

- **Input set confirmed:** F1 uses the existing `input_hashes` (spec, plan, constitution, and gate/closure when
  present); none is removed, none added. R2 stays separate.
- **(a) and (b) accepted as the deliberate cost of F1:** a changed input refuses `complete`, including when the change
  is another task's correctly integrated work. No exception for spec- or plan-editing tasks, no automatic rebind. After
  such a change: keep the attempt and its evidence; refresh the contract through the existing authoring and
  acceptance; use the existing recovery and a new `start`. Earlier work may be reused only after it is assessed
  against the new contract, never by counting the previous attempt as passed. The model's work need not be redone
  where the existing process can verify the kept result in the new attempt.
- **(c) accepted:** a manual `complete` from a worktree whose inputs differ from the attempt's binding is refused, with
  a message naming the mismatch and the right place to drive the attempt from. No automatic directory switch and no
  fallback to the primary checkout. A call from the primary checkout checks the right attempt and its required
  evidence just the same; it is not a way around the worktree refusal.

## The order of `complete` and integration (master point 3)

Measured in Showcase at `3649784` (`harness.py` unchanged since):

- A task works in its own worktree and commits there; `complete` records that commit as the attempt's
  `checkpoint_commit` (`cmd_complete`).
- A dependent task's worktree is built from its dependencies' checkpoint commits (`prepare_task_worktree`,
  `harness.py:6225` ff., merging them at `:6278`); `integration` tasks are built the same way.
- **No harness or orchestrator command merges task work into the primary checkout's branch.** `orchestrate.py` runs
  `start` and `complete` with the primary checkout's feature directory (`:677`, `:700`), whose `spec.md`, `plan.md`,
  gate, closure and constitution therefore stay as they were throughout the feature's attempts. Integration into the
  primary branch happens outside the lifecycle, after completion.

**So the first case applies:** the current flow lets `complete` run before integration, on the inputs as they were;
the later integration makes the contract stale (C1) for every subsequent operation, until the contract is
re-authored. No process conflict: `complete` never requires an earlier integration that changes the inputs. F1 keeps
this order and does not change it.

**Planning (operational recommendation):** integrating changes to a feature's contract inputs (its spec, plan, gate,
closure, or the constitution) can invalidate parallel attempts of the same feature that depend on those inputs. Plan
such integrations outside active attempts that depend on them. F1 builds no scheduler for this.

## Implementation (candidate, 2026-10-24)

**Library, `agent_harness.verification_contract`:**
- `contract_binding(feature_dir, *, require, repo_root, repository, constitution, is_active) -> (errors, binding)`:
  reads `verification-contract.json` and each input **once**, validates those bytes and builds the binding from the
  same bytes. `validate_contract` is now `contract_binding(...)[0]` (same errors as before, plus C2/C3).
- Reads fail closed: only `FileNotFoundError` counts as "absent"; any other `OSError` is C3 (`<key> unreadable
  (<class>)`, or `verification-contract.json unreadable (<class>)` for the contract itself). A missing `spec.md`,
  `plan.md` or constitution is C3 (`FileNotFoundError`). A repository root that cannot be resolved (`SystemExit` or
  `OSError` from `repo_root`) is C2. The fail-open `expected = {}` is gone.
- **Binding, as implemented** (supersedes the hash-of-canonical-JSON and `null` sketched in the mechanism above; master
  asked for an explicit, versioned encoding of names, presence and digests): with a contract,
  `{"version": 1, "contract": {"present": true, "sha256": …}, "inputs": {"<key>": {"present": true, "sha256": …} |
  {"present": false}}}` for every key in `INPUT_KEYS` (`spec_sha256`, `plan_sha256`, `constitution_sha256`,
  `design_gate_sha256`, `master_closure_sha256`, always all five, in that order); without a contract (optional
  policy) `{"version": 1, "contract": {"present": false}}`. With any error there is no binding (`None`).
- Unchanged from the source: an absent design input (gate, closure) is not compared with the contract's recorded
  hash. A first draft also reported a recorded-but-absent design input as stale; Showcase's `wayfinder to-tasks`
  validates a scratch copy without `design/gate.json` (`test_wayfinder`), so that rule was dropped as outside the
  agreed scope. The binding records the absence, so a removal between `start` and `complete` still refuses.

**Library, `agent_harness.feature_transitions`** (F1's own functions, not moved code): `observed_contract_binding`,
`require_unchanged_contract`, `require_attempt_contract`, `contract_binding_difference`.
- `cmd_start` and `cmd_claim` (both start an attempt): pre-check right after `load_validated`; under the lock, first
  statement of the transaction, `require_unchanged_contract` (`VERIFICATION_CONTRACT_INVALID` or
  `VERIFICATION_CONTRACT_CHANGED_DURING_OPERATION`); the new attempt records `contract_binding`. A verification resume
  (`start` with `verification_resume_pending`) continues the same attempt: it requires that attempt's binding like
  `complete` and does not record a new one.
- `cmd_complete`: pre-check after evidence validation; under the lock, first statement, the same recheck; after the
  read-only status, owner, origin and resume checks and before any side effect, `require_attempt_contract`:
  `VERIFICATION_CONTRACT_CHANGED_SINCE_START` (names the differing file and inputs) or
  `VERIFICATION_CONTRACT_BINDING_MISSING` (no recorded binding while a contract is present). No binding and no
  contract: allowed (optional policy). The binding is never written by `complete`. The idempotent replay of an
  already completed task (`ALREADY_COMPLETED`) is not an attempt completion and is not checked against the binding.
- Every refusal raises before `save_state`, so no state is written (`locked_state` saves only on normal exit).

**Shim:** `migration/showcase-wrapper/verification_contract.py` adds `contract_binding(feature_dir, *, require=None)`
with Showcase's bindings, which the transitions reach as `ns.vc.contract_binding`. `harness.py` is unchanged.

**What the existing fingerprint already did, and what F1 adds** (found while testing). The feature fingerprint
(`feature.feature_fingerprint`) covers `spec.md`, `plan.md`, `tasks.json`, `design.json`, `design/gate.json`,
`verification-contract.json` and `wayfinder-handoff.json`, and every lifecycle command refuses a changed fingerprint
until `reconcile-feature` adopts it; `reconcile-feature` keeps running attempts running. So before F1:
- a contract re-authored (for any input change) and reconciled while an attempt ran let that attempt `complete`
  against the new contract: **F1 refuses this** (`CHANGED_SINCE_START`), the case the tests drive;
- the constitution and `master-closure.json` are outside the fingerprint; a change to them without re-authoring is
  C1 (stale), and with re-authoring the contract file changes, so the case above applies;
- an unreadable input or no repository root skipped the staleness comparison: **F1 refuses (C2/C3).**
R2 (adding the constitution and the closure to the fingerprint) stays separate and is not needed for F1's guarantee.

**Refreshing after a refusal**, command by command (no rebind, no back-fill): re-author and accept the contract
(`verification_contract.py generate` and its acceptance), commit, `reconcile-feature` (a changed contract file changes
the fingerprint), then the existing recovery of the refused attempt (`fail` or `release`, or `recover-state` when its
lease expired) and a new `start`, which records the new binding. The refused attempt and its evidence stay in the
state and on disk.

**Guarantee (master's wording):** `complete` admits a completion only if the correctly read and validated inputs
observed during the check under the lock match the binding recorded at `start`. It is not an atomic snapshot: a
change after the check under the lock and before the transaction's write is not detected by that operation; the next
operation detects it only if it is still observable and that operation re-checks these inputs. An edit that restores
the same bytes is invisible. The contract's writers do not take the lifecycle lock, and F1 does not make them.

## Verification (candidate `fcd9a94`, 2026-10-24)

**Library** (wheel installed outside the checkout, `scripts/check-wheel.sh`): 621 tests OK (2 skipped); `ruff 0.16.10`
clean; `git diff --check` clean. `tests/test_verification_contract.py` replaces the source's fail-open test
(`test_an_unreadable_input_skips_the_staleness_check_only`) with C2/C3, the unreadable contract, the binding's
encoding and same-read identity, an absent design input, and an absent contract.

**End to end through Showcase's shim** (`migration/showcase-wrapper/tests/test_f1_contract_binding.py`, run in Showcase
`ac552f0` with the candidate installed; Showcase's own feature and evidence fixtures): 12 tests OK:

| Required case | Test |
|---|---|
| change before the under-lock check (start: no attempt used, no worktree, no binding) | `test_a_change_before_the_locked_check_refuses_start_without_using_an_attempt` |
| same, at complete | `test_a_change_before_the_locked_check_refuses_complete` |
| change between start and complete (state bytes and evidence kept, status `running`) | `test_a_change_between_start_and_complete_refuses_and_keeps_state_and_evidence`, `test_a_reauthored_contract_alone_refuses`, `test_a_contract_added_after_start_refuses_complete` |
| unreadable inputs, at start and at complete | `test_unreadable_inputs_refuse_start_and_complete` |
| missing historical binding (no back-fill) | `test_an_attempt_without_a_recorded_binding_cannot_complete_normally` |
| unchanged attempt completes; binding not rewritten | `test_an_unchanged_attempt_records_its_binding_and_completes` |
| complete before integration passes; a later integration blocks the next start until the contract is refreshed (and reconciled) | `test_complete_before_integration_passes_and_a_later_integration_blocks_the_next_start` |
| optional policy without a contract unchanged | `test_without_a_contract_the_optional_policy_is_unchanged` |
| stale (C1) and fingerprint order | `test_an_input_changed_and_not_reauthored_refuses_as_stale`, `test_before_reconciliation_the_fingerprint_refuses_first` |

**Mutations** (each applied to the installed library, F1 tests rerun, then restored and compared byte for byte): all
six caught: no recheck under the lock in `start` (1 failure) or in `complete` (1); no attempt check in `complete` (4);
`start` records no binding (3 failures, 4 errors); a missing binding allowed (1); the recorded binding not compared
(3).

**Showcase suite** (`ac552f0`, candidate installed, 299 units as in AH5-14): 290 OK. Non-passing:
- the 4 known cases (below), except that `test_register_manual_observation_…` passed in this run;
- **6 caused by F1, all the intended historical-binding refusal:** these fixtures write a `running` attempt directly
  into the state, with a contract present and no `contract_binding`, then call `complete`; F1 refuses with
  `VERIFICATION_CONTRACT_BINDING_MISSING` before the check each test exercises:
  `test_harness.CompletionCorrectionTest.test_m4_03_complete_required_vc_proof_accepts_completion`,
  `test_harness.CompletionCorrectionTest.test_completion_rejects_checkpoint_not_descending_from_dependency`,
  `test_harness.CanonicalCompletionRepairTests.test_unresolved_older_correction_still_refuses_completion`,
  `test_harness.CanonicalCompletionRepairTests.test_repaired_correction_from_prior_epoch_does_not_block_reused_attempt_number`,
  `test_harness.CanonicalCompletionRepairTests.test_new_completion_repair_is_not_treated_as_replay_of_older_repair`,
  `test_harness.CanonicalCompletionRepairTests.test_current_attempt_correction_requires_canonical_repair_completion`.
  Adoption fixes them on the Showcase side (see below), not by any library exception.
- `test_wayfinder` failed with the first draft (the dropped recorded-but-absent rule above); rerun after the fix in
  the rehearsal below.

**Showcase features** (all 50 under `docs/specs` at `ac552f0`, `harness.py validate` with 0.18.0 and with the
candidate): output identical; 43 valid, 7 invalid on both for reasons unrelated to F1. Three features have a contract
(`AH5-04B-QUAL-001`, `SDD-001`, `SDD-OBS-001`); Showcase's live lifecycle state has no running attempt in them (read
only), so no attempt is left without a binding by the adoption.

## Known non-passing Showcase cases (exact identifiers)

From the AH5-14b-2 step 2 run that reported 295/299 (`res17c`, one unit per line, Showcase `792dfad` + `d067955`):
1. `test_harness.HarnessTest.test_register_manual_observation_serializes_scope_resolution_with_lifecycle_mutation`
   (`FAILED (failures=1)`);
2. `test_verification_completion_boundary.OriginCompletionBoundaryTest.test_real_plan_acceptance_cannot_be_completed_with_legacy_proofs`
   (`ERROR`, `StoreError: SECRET_BEARING_CANDIDATE_UNSEALABLE`);
3. `test_verification_workspace`: in that run a single unit, `FAILED (errors=12)`; the run kept no per-method names.
   **Evidence gap: the 12 method names of that run are not preserved.** The `rehearsal-14b2` logs do not supply them
   either: there the module ran 14 tests, all OK, on both sides. Not reconstructed;
4. `test_verification_sandbox_doctor`: `NO TESTS RAN` (0 tests collected), so it has no method identifiers.

`rehearsal-14b2` (same code, separate runner, both sides identical) had a different set: `test_concurrent_sequential_
replans_have_one_winner` (FAIL and ERROR), `test_register_manual_observation_…` (FAIL) and
`test_real_plan_acceptance_…` (ERROR), recorded there as 2 FAIL, 2 ERROR.

**Relation to F1's criteria:**
- **1 does not affect them.** It tests that `register_manual_observation` (verification-store registration) serializes
  with a lifecycle mutation, using 2–3 s thread waits; it fails under load (`mutation.is_alive()`). F1 changes neither
  that function nor `locked_state`. F1's "under the lock" relies on `locked_state`'s exclusion between lifecycle
  commands, which passing tests cover (`test_lifecycle_mutation_and_verifier_reservation_share_atomic_guard`,
  `test_concurrent_sequential_replans_have_one_winner`, `test_conflicting_failed_task_replans_serialize_with_one_winner`
  OK in the F1 run). With the candidate it passed in the full run and 5 of 5 times alone.
- **2 does not affect them.** It errors in `verification.authority.prepare_task_plan` (candidate sealing) before any
  lifecycle command runs; F1 does not touch that path. Its guarantee (an accepted v2 plan blocks a legacy-proof
  `complete` with `VERIFICATION_ORIGIN_ADMISSION_UNAVAILABLE`) is not an F1 criterion. If it ran, F1 would not change
  its outcome: its fixture has no contract (both bindings absent), and `require_available_completion_origin` runs
  before F1's attempt check; the sibling `test_v2_normal_completion_rejects_fabricated_legacy_success`, which asserts
  the same refusal through `cmd_complete`, passes with the candidate.
- 3 and 4 are environment-only modules (workspace sandboxing and sandbox doctor), outside the paths F1 changes.

## Independent review (fresh context, 2026-10-24, on `0748b5c`): PASS WITH FINDINGS

No blocker. Confirmed: same-read validation and binding; no side effect before the checks under the lock; refusals
persist nothing; unambiguous, versioned binding; no scope beyond F1; the packet's fingerprint, reconcile and guarantee
statements. Findings and their handling:

1. **Resume dead end (medium, for decision):** a `verification_resume_pending` attempt whose contract changed is refused
   by every resume `start` (`CHANGED_SINCE_START`), and `fail`/`release` need `running`; no existing command clears the
   pending resume of a failed entry. See the open points below.
2. **Fixed:** `requirement()` used `Path.exists()`, which reports False for a symlink loop (and, from Python 3.14, for
   a permission error), so a required contract could be read as optional. Now only a missing `design.json` is
   "optional"; an unreadable one is `verification contract requirement cannot be established: design.json unreadable
   (<class>)`.
3. **Fixed:** requiredness and validity of `design.json` came from two reads; now one.
4. **Fixed (message):** the refusal said "the feature directory the attempt was started from", which the binding does
   not record; it now says to drive the task from the checkout whose feature directory started it (an orchestrated
   attempt: the primary checkout) and names the directory this run read.
5. **Not changed (for decision):** Showcase's `repo_root()` in the shim falls back to the current directory's Git
   repository when `git -C REPO` fails (pre-F1 code, also used by authoring).
6. **Fixed (tests):** `claim` is now tested (binding recorded; a change before the locked check refuses with no
   attempt used); the claim mutant now fails. Still untested end to end: resume `start` (depends on point 1).
7. **Noted:** `rollback_unexecuted_start` leaves the rolled-back attempt's `contract_binding` on the pending entry; the
   next `start` replaces it, and `complete` requires `running`.
8. **Noted:** besides C2/C3, a design input that is not a regular file (for example a directory) and a non-UTF-8
   contract are now errors too (both fail closed).

Rechecked after the fixes (Showcase `ac552f0`, candidate installed): `test_f1_contract_binding` (14),
`test_wayfinder`, `test_verification_contract`: 32 OK; library tests 30 OK.

## Open points for master

**Resume (review finding 1).** Options: (a) leave it: such an attempt stays blocked until a human-resolution path is
decided (no bypass); (b) let `fail`/`release`-equivalent recovery clear a pending resume whose contract binding no
longer matches, keeping its history and evidence, so a new `start` can run under the refreshed contract (a recovery
change, outside F1 as agreed, so it would need its own decision); recommendation: (b), as a separate small package
after F1, with F1 documenting the current block.

**`repo_root` fallback (review finding 5):** keep it (authoring depends on it), or make the binding's root lookup
fail closed without the fallback.

`complete-repair` (`cmd_complete_repair`) is still Showcase's own code in the shim (it moves with a later packet). It
runs `load_validated`, so C1–C3 block it, but it does not check the attempt's binding. It publishes a canonical
repaired completion for an existing completion under an explicit repair authorization, so it is a recovery path. F1
adds no bypass and no check there. **For decision:** require the attempt binding in `complete-repair` too when it
moves, or keep it as a recovery path covered by C1–C3 only.

## Adoption (Showcase, after a release on the owner's order)

1. Pin the release and copy the shim `verification_contract.py` (adds `contract_binding`); `harness.py` is unchanged.
2. Add `tests/test_f1_contract_binding.py` from the shim.
3. Update the fixtures of the six tests above: they write a `running` attempt into the state for a feature with a
   contract, so record the binding that attempt's `start` would have recorded, after the contract is written. Two
   lines in `tests/test_harness.py` at `ac552f0`, verified in a copy (all six then pass; what each test asserts is
   unchanged):
   - in the shared completion fixture, after `entry.update({'status': status, 'attempts': attempts, 'owner':
     'worker-a', 'checkpoint_commit': checkpoint_commit})` (line 4347):
     `entry['contract_binding'] = harness.vc.contract_binding(feature)[1]`;
   - in `test_completion_rejects_checkpoint_not_descending_from_dependency`, before its `harness.save_state(feature,
     state)` (line 4443): `state['tasks']['T-B']['contract_binding'] = harness.vc.contract_binding(feature)[1]`.
4. Operationally: attempts started before the adoption on a feature with a contract cannot complete normally; follow
   *Refreshing after a refusal* above (at adoption time, none are running).
5. Run the full suite and CI; rollback is the previous pin and shim, as before.
