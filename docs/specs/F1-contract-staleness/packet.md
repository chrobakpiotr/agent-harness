# F1 — fail-closed verification-contract staleness (behaviour change, for review)

- Status: **design for review**; no implementation before review (master, 2026-10-23). Separate from the verbatim
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
**Review point R1:** the new errors apply to every existing contract, required or optional, because a present
contract is already checked for staleness today whatever its requirement. The alternative is to limit C2/C3 to
required contracts; harness recommends all present contracts (a present but unverifiable contract is not evidence).

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
