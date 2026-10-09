# Harness review round 2 of Showcase SDD-OBS-001 T-004 attempt 6 (2026-10-09)

- Scope: read-only review of the uncommitted T-004 worktree diff on `a35b5a8`; experiments ran in a clone with the
  identical diff applied. Follows [`review-showcase-sdd-obs-t004-2026-10-09.md`](review-showcase-sdd-obs-t004-2026-10-09.md).
- Verdict: **pass with one condition.** Both blockers are fixed. Before completion, strengthen one test so it pins the
  lifecycle lock in `register_manual_observation`. T-005 may then start on this interface.

## Evidence

- Suites: `test_harness` + `test_orchestrate` 310 OK; `test_verification_admission` + `test_verification_supervisor` +
  `test_verification_store` 110 OK (`cryptography` 46.x).
- `a35b5a8` and `main`'s `e3889e9` carry the same sequential-replan change (identical patch id).
- Stranded admission: a verifier was SIGKILLed at each supervisor boundary, then `recover()` was run:

  | Boundary | After recovery |
  |---|---|
  | `after-admission`, `after-prepare`, `after-started`, `during-launch` | `ABORTED_PREPARED`; reservation released; lifecycle mutation allowed |
  | `after-drained` | `TERMINAL`; released |
  | `after-launch` | `UNCERTAIN`; reservation kept; mutation blocked (correct: a launched unit without drain proof needs backend recovery) |

  An owner whose identity is unknown or uncertain counts as alive (the `ps` start time has one-second resolution, so a
  reused pid within that second stays blocked, which is the safe direction).
- Registration mutations against the focused tests: generation CAS removed → caught; conflicting replay accepted →
  caught; repository-id check removed → survives; lifecycle lock replaced by a no-op → **survives**.
- The lock matters. With the lock removed, a concurrent `locked_state` mutation (`feature_generation = 2`) during scope
  resolution is overwritten by the registration's stale `save_state`, leaving a final generation of `None`. With the
  lock, the final generation is `2`.

## Condition (before completion)

`test_register_manual_observation_serializes_scope_resolution_with_lifecycle_mutation` checks only
`mutation_entered.wait(0.05)`, which a slow thread start satisfies without any lock. Assert the outcome as well: after
both threads finish, the final `feature_generation` is the mutation's value and the observation is in the ledger.

## Notes for T-005

1. Signature trust: the API records digests only, and anything with local repository access can call it. T-005's
   `record-manual` must verify the Ed25519 attestation against the accepted issuer registry before calling it, and any
   step that turns a ledger entry into accepted coverage must re-verify from the attestation bytes, not trust the entry
   alone.
2. The record file is written before the ledger entry. After a crash in between, an exact replay completes the
   ledger. Consumers must treat only ledger-referenced records (with a matching `record_sha256`) as registered.
3. Low: the repository-id check in `register_manual_observation` is not pinned by a test (defence in depth). The
   ledger stores absolute `record_path` values, which break if the checkout moves.

Resume: the operator, prior result path and sha256 are recorded and checked before use, and the partial-claim check is
kept; the dedicated tests pass.
