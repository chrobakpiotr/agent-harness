# F1 — fail-closed verification-contract staleness (behaviour change, for review)

- Status: **design for review**, refined after master's points 3–6 (2026-10-23); no implementation before the
  consistency question below is decided. Separate from the verbatim
  extraction. AH5-14c stays blocked until F1 is decided and verified.
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
  `complete`, unreadable inputs, a missing historical binding, and an unchanged attempt that completes; the remaining
  race after the check documented, not claimed removed.
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
