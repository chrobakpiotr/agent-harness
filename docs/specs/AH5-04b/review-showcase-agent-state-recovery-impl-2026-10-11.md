# Harness review of Showcase AGENT-STATE-RECOVERY-001 implementation (2026-10-11)

- Scope: read-only review of the uncommitted Showcase changes on `main` `4325e3e`: `tooling/agent-harness/state_snapshots.py`,
  `state_recovery.py`, the `harness.py` diff, their tests, `harness-review.md` and `incident-record.json`. It checks them
  against the amendments in `review-showcase-agent-state-recovery-2026-10-11.md`.
- Verdict: **changes required.** Most of the design is right. One crash window wedges the repository with no supported way
  out, which is blocking (R1). The full suite must be green before recovery (R2). No recovery write and no ref push yet.

## Accepted as implemented

- **One repository-wide lock.** The lifecycle lock lives in the Git common directory, and every feature takes it, so a
  snapshot can never capture a torn tree. Without `fcntl` the lock fails closed.
- **Complete-tree snapshots.** Each snapshot is Git objects plus a manifest that binds `state_revision`,
  `feature_generation`, feature, epoch, repository identity and per-file sha256, bytes and mode. Objects and refs are
  fsynced.
- **Restore.**
  - Restore requires the exact `refs/agent-state/current` commit (`--expected-snapshot-commit`) and an absent
    `.agent-state`.
  - Every file and the whole manifest are verified first.
  - An invalid newest snapshot never falls back to an older one (tested).
- **Retention.** The cap is 64 per feature. The newest snapshot and the first of each epoch are pinned, and the cap is
  checked before any visible write.
- **New epoch.**
  - It is explicit, under the lock, and bound to the committed, unmodified incident record, all 88 baseline files
    (checked before and after), the origin, the observation commit and a fingerprint CAS.
  - It imports zero completions, attempts, packet revisions or grants. `max_rework_attempts: 2` is recorded.
  - The receipt is published before the state, and a retry is idempotent.
  - A later baseline change makes the feature read-only again.
- **Verifier store.** It stays outside the safeguard and fails closed through the existing unavailable paths, as
  accepted.
- **Import tuple (SR-008).** It is met vacuously, because no import path exists. Keep it that way: any future import
  must implement the full tuple and dependency closure. T-009 must be evaluated fresh.
- **Incident record.** The timeline and the independent backup confirmation match what Harness reported.

## Required changes

### R1 (blocking): a crash after the state replacement wedges every feature

`commit_state_snapshot` runs these steps in order:

1. writes the prepared ref;
2. publishes `additional_files`;
3. replaces the state file;
4. advances `refs/agent-state-snapshots/…`, `refs/agent-state/<feature>` and `refs/agent-state/current`.

A crash anywhere between step 2 and the last ref update leaves `.agent-state` different from `current`.

Reproduced in a scratch repository: one good commit, then a commit that crashes at `after-state-replacement`. The
result:

- `current_repository_state_status` returns `state-mismatch`;
- the prepared ref is left behind;
- `restore_current_snapshot` returns `snapshot-restore-target-exists`.

In `harness.py`:

- `state-mismatch` makes `state_recovery_required` true for **every** feature, because the check covers the whole
  repository.
- Every lifecycle command dies with `STATE_RECOVERY_REQUIRED`.
- `restore-state` refuses because `.agent-state` exists, and `recover-state` refuses with `STATE_RECOVERY_ALREADY_PRESENT`.

The only way out is a manual edit of `.agent-state`, which is forbidden. The current crash tests only check that `current` did
not advance. They never run the next command.

Fix: treat the prepared ref as a redo log, resolved under the lock before any state read.

- **Roll forward.** A prepared ref exists, its manifest verifies, and every file on disk equals either its `current`
  version or its prepared version, with no other files. Then write the prepared versions that are missing (the payloads
  are in the prepared commit), fsync them, advance the refs and delete the prepared ref.
- **Fail closed otherwise.** Use a distinct error, and do not reuse `STATE_RECOVERY_REQUIRED`.
- **Stale prepared ref.** A prepared ref whose tree equals `current` exactly is simply deleted.
- **Restore.** While a prepared ref exists, restore must refuse. Otherwise `.agent-state` lost after such a crash
  restores a state one commit behind what may have been acted on.
- **The two current refs.** The same resolution covers a crash between advancing the feature ref and the repository
  ref.
- **Tests.** For each boundary (`after-snapshot-publication`, `after-additional-files`, `after-state-replacement`,
  between the ref updates), crash, then run the next ordinary lifecycle command and assert that it succeeds on the
  rolled-forward state. Also test crash plus deleted `.agent-state`: restore must refuse.

### R2 (blocking for recovery): the full `test_harness` suite must be green

The last complete run (285 tests) failed, and the fixes since then were checked only by targeted reruns. Run the
complete module once after R1 and record the result in the packet. If it is too slow:

- Every load now walks and hashes the full tree and reads the snapshot through Git twice.
- `publish_completion_authority` writes a full snapshot for each record, which means several snapshots per command.

Batching the records of one command into one `save_state` (via `additional_files`) is the cheap speed-up. Do it only
if the suite time demands it.

## Non-blocking

1. **Retention order.** Retention sorts by wall-clock `created_at`. A clock step backwards can make an older snapshot
   look newest. Making each snapshot commit's parent the previous `current` gives a monotonic order that Git can check
   (`merge-base --is-ancestor`), plus a browsable history.
2. **Restore audit.** The `restore-state` operator label is only printed. Put it in the ref update's reflog message
   (`update-ref -m`) or in a separate restore ref, so the restore stays exact and is still audited.
3. **Local repository identity.** Without a credential-free origin, the identity is the absolute path of the common
   directory. Moving the clone then makes every snapshot `invalid`, which means recovery-required. Showcase has an origin,
   so this does not apply now. State the limit.
4. **Naming.** `repository_id` in completion records is a filesystem path, while in snapshots it is a hash. Rename one
   of them before they are compared by mistake.

## Before the recovery write

1. R1 and R2 are done, and Harness re-checks R1.
2. The owner checks the privacy of the ref payload: a complete `.agent-state` tree, including packet revisions,
   operator labels and paths.
3. The owner gives explicit authorization for `recover-state`. Pushing `refs/agent-state/current` is a separate,
   optional decision.

## Recheck of R1 and R2 (Showcase `b422e6c`, `c9ed6c9`)

Verdict: **R1 resolved; R2 accepted on Showcase's evidence.** The recovery write may proceed once the owner has
checked the privacy of the ref payload and authorized `recover-state`. Two follow-ups do not block this recovery.

### Verified independently

- **Crash at every boundary.** `state_snapshots.py` from `c9ed6c9` was run in scratch repositories, with one good
  commit followed by a crashing commit that also publishes an immutable record. The crash points were
  `after-snapshot-publication`, `after-additional-files`, `after-state-replacement`, `after-archive-ref`,
  `after-feature-current-ref` and `after-repository-current-ref`. After every one of them,
  `recover_prepared_snapshots` resolved the transaction:
  - a crash before any disk write discarded the record;
  - every other crash rolled forward;
  - the status was then `consistent`, with no prepared ref left.
- **Crash plus loss of `.agent-state`.** Recovery fails closed (`snapshot-prepared-state-conflict`) and restore
  refuses (`snapshot-restore-prepared-pending`), as required.
- **Recovery before reads.**
  - It runs under the lock in `_load_state_unlocked`, in `cmd_status` and in `cmd_recover_state`.
  - Pure reads that do not recover die with `STATE_RECOVERY_REQUIRED` until the next ordinary command recovers.
    That is acceptable.
  - `restore-state` refuses while a prepared ref exists.
- **Showcase's own tests.** Run from the exported `c9ed6c9` tree: `test_state_snapshots` and `test_state_recovery`,
  26 tests OK. Harness did not rerun the 288-test `test_harness` module. Showcase reports it green in a sequential
  run (2,227 s) and in a four-worker run (831 s).

### Follow-ups (not blocking this recovery)

1. **First snapshot of a legacy tree.** Suppose a repository has a pre-snapshot `.agent-state` and no current ref,
   and its first snapshot transaction crashes before the state replacement. Recovery then compares against an empty
   old tree. The unchanged legacy state file is neither old nor new, so recovery reports
   `snapshot-prepared-state-conflict`, with no supported way out.
   - Reproduced in a scratch repository.
   - It does not affect this recovery: Showcase has no `.agent-state` and no current ref, so the first write starts
     from an empty tree.
   - Fix before the safeguard reaches repositories that hold legacy state: record each file's prior digest in the
     prepared manifest, and use those digests as the old tree when there is no current ref.
2. **Way out after crash plus loss.** In that case `restore-state` says to resolve the prepared snapshot "with an
   ordinary lifecycle command first", but every ordinary command then fails with the conflict.
   - Add an explicit, audited owner command that discards the prepared record, with a CAS on its commit. Restore
     can then proceed to the last acknowledged snapshot. Alternatively, document the owner procedure.
   - Discarding is semantically correct: the prepared transaction was never acknowledged.
