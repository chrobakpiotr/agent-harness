# Harness review of Showcase AGENT-STATE-RECOVERY-001 proposal (2026-10-11)

- Scope: read-only review of `docs/specs/AGENT-STATE-RECOVERY-001/harness-review.md`,
  `evidence/incident-record.json` and `evidence/evidence-baseline.json` (Showcase `main` `4325e3e`).
- Verdict: **accept with amendments.** The contract is right: no reset, no migration, no synthesized history, explicit
  new epoch, byte-preserving evidence, revalidation before import, T-009 from scratch. The amendments make the
  safeguard durable and the import tuple complete; they need no second proposal round.

## Verified independently

- Baseline: 88 entries; every path, byte count and sha256 matches the files on disk; none missing, none extra. The
  baseline file's sha256 (`d9bdde1d…9abf`) equals `evidence_baseline_sha256` in the incident record.
- Since the observation all 88 files are Git-tracked (41 were untracked then). That is good: untracked evidence was
  as exposed as `.agent-state` was. The baseline's `git_status` stays a fact of the observation time.
- Backup search, checked by Harness on this host (2026-10-11), so the incident record can cite it as independently
  verified:
  - `tmutil listlocalsnapshots /`: no snapshots;
  - `tmutil destinationinfo`: no destinations configured;
  - no `SDD-OBS-001-*.json` or `packet-revisions` on disk or in Spotlight.

## Timeline facts Harness can add (cause stays `unknown`)

- Present: Harness read `.agent-state/SDD-OBS-001-*.json` and the T-009 packet revisions for the round-4 review
  (`af1e4c9`, 2026-10-10 21:22 +0200). Showcase's `75493a8` (2026-10-10 22:10 +0200) recorded a lifecycle completion,
  so the state was presumably present then too.
- Absent: observed by Harness on 2026-10-11, before the incident record (13:22 UTC).
- A lead, not a cause: between about 22:35 and 22:44 +0200 on 2026-10-10 a Showcase container (`python:3.13-slim`,
  `apt-get install git && pip install … && python -m unittest tooling/agent-harness/tests/test_harness.py …`) was
  running on this host (load average up to 248). If it ran against a mounted checkout, check whether anything in that
  run can remove ignored paths (e.g. `git clean -x`, a tree reset, a fixture that deletes `.agent-state`).

## Amendments

1. **Snapshot ordering.** Take the snapshot under the same lifecycle lock as the state commit, and write it *before*
   the state replacement is acknowledged (write-ahead), with a manifest naming `state_revision`/`feature_generation`.
   A snapshot that lags the state by one commit can resurrect a consumed retry grant or an earlier active packet.
   Restore only the newest snapshot whose manifest verifies. If the newest one does not verify, use the new-epoch
   path, never an older snapshot.
2. **Where it survives.** The Git common directory survives `git clean -fdx` and branch or worktree operations, which
   is the likely class of loss here. It does not survive deleting or re-cloning the repository. Store each snapshot
   also as Git objects under a dedicated ref (e.g. `refs/agent-state/<feature>`). The owner can then push that ref
   for an off-host copy, after a privacy check of what the state contains. Keep the stated limit for hosts where it
   is not pushed.
3. **Retention.** Content-addressed, so unchanged files are stored once. Never prune the newest snapshot or the first
   snapshot of each epoch; state the cap.
4. **The verifier control store.** `.agent-runs/control/verification-v2` is also an ignored directory and is exposed
   in the same way. Either include it in the safeguard or state its own loss behaviour in the spec.
5. **Import tuple.** Besides the checkpoint, the accepted contract, the new packet revision and the epoch, bind:
   - the candidate identity and changed-surface seal of that checkpoint;
   - the accepted spec and verification-contract fingerprint;
   - the evaluator identity, distinct from the builder;
   - the digest of the verification evidence.

   Import a task only when all its dependencies are imported in the new epoch, so the DAG stays consistent.
6. **Retry budget.** The incident decision states the new epoch's budget as a number per task. Old human resolutions
   stay archival and never count as grants (as SR-005 says).
7. **Snapshot tests (SR-006).** Also test: a crash after the snapshot write and before the state commit (the restore
   must not be ahead of the acknowledged state), and concurrent lifecycle commands (one snapshot per commit, no torn
   tree).

Harness reviews the implementation task against SR-001…SR-007 plus these amendments before any recovery write.
