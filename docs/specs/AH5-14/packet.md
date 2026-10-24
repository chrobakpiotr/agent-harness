# AH5-14 — Move `harness.py` in parts; AH5-14a: document and state I/O

- Status: **AH5-14a released in 0.16.0** (2026-10-23); rehearsed against Showcase's 0.15.0 adoption and evaluated
  (pass with amendments, applied). 14b–e are later packets.
- Source: Showcase `tooling/agent-harness/harness.py` at `fd96eeb` (7,314 lines, 222 functions). The rehearsal runs
  on Showcase's confirmed state after its 0.15.0 adoption; until that commit is reported, no claim is made about
  compatibility with the new consumer state.
- Rules (plan): verbatim, explicit fail-closed seams, project policy never built in, guards moved never removed, one
  state writer per root, no parallel implementation. Cut along existing function boundaries; a part that cannot be cut
  without a behaviour change is split differently.

## How the cut was found

A call graph of `harness.py` (AST, every function's calls and module globals). The document functions and the state
paths, identity checks and writer depend only on each other, `state_snapshots`, `verification_contract` and module
constants. **State loading does not**: `_load_state_unlocked`, `_read_state_unlocked_pure` and `lifecycle_state_lock`
call recovery (`state_recovery_required`, `recover_prepared_state_locked`: AH5-14d), and `_resolve_state_unlocked` →
`initial_state` calls the completion records (`completion_records`, `repaired_completion_records`: AH5-14b). Moving
loading now would drag recovery and completion along or need a hook that could skip their checks. So **state loading
stays in Showcase until 14b/14d move what it calls**; 14a moves what can move without a behaviour change.

## AH5-14a scope: exact functions

**Moves to `agent_harness.feature` (documents):** `die`, `load_json`, `feature_files`, `task_index`, `repo_root`,
`sha256_bytes`, `feature_fingerprint`, `protocol_fingerprint`, `protocol_version` (with `STATE_SCHEMA_VERSION`),
`safe_relative_pattern`, `builder_pattern_has_concrete_root`, `pattern_static_prefix`, `patterns_may_overlap`,
`transitive_dependencies`, `design_gate_errors`, `master_implementation_closure_errors`, `validate`,
`load_validated`, and the protocol constants `VALID_ROLES`, `FEATURE_ID_RE`, `TASK_ID_RE`, `PROFILE_RE`, `AC_RE`,
`DEFAULT_LEASE_TTL_SECONDS`, `DEFAULT_HEARTBEAT_INTERVAL_SECONDS`, `VALID_TEST_POLICIES`, `VALID_TEST_MODES`.

**Moves to `agent_harness.feature_state` (state paths, identity, the writer):** `state_key`, `git_common_dir`,
`runtime_state_dir`, `state_path`, `lock_path`, `legacy_state_name`, `main_legacy_state_path`,
`validate_state_identity`, `validate_loaded_state`, `save_state`.

**Stays in Showcase (policy), bound through the policy object:** `feature_repo_base` and `is_active_feature_dir` (the
`docs/specs/<feature>` layout, with the working-directory fallback), `protocol_files` (its 21 files and 3
directories), `reviewers` with `RISK_TO_REVIEWERS`, the agent-profile location `docs/agentic-sdd/agents/<profile>.md`,
`STATE_DIR = .agent-state`, and its `verification_contract` shim (`validate_contract`, `criterion_ids`).

**Stays in Showcase until a later part:** state loading and locking (`load_state`, `_resolve_state_unlocked`,
`initial_state`, `_load_state_unlocked`, `_read_state_unlocked_pure`, `lifecycle_state_lock`, `locked_state`,
`remove_state_locked`) → 14b/14d; recovery (`recover_prepared_state_locked`, `state_recovery_*`,
`state_snapshot_status`, `cmd_recover_state`, `cmd_restore_state`, `cmd_discard_prepared_snapshot`) → 14d;
`cmd_migrate_state`, `cmd_validate`, `cmd_validate_all` → 14e (the CLI); everything else → 14b–e.

## Consumer bindings (one explicit policy object, no defaults)

`FeaturePolicy` (frozen dataclass), every field required:

| Field | Meaning | Showcase binding |
|---|---|---|
| `repo_base(feature_dir)` | the repository a feature belongs to | `feature_repo_base`: `docs/specs/<feature>` → the checkout, else the working directory |
| `is_active(feature_dir)` | an active feature directory | `is_active_feature_dir` |
| `protocol_files(feature_dir)` | the files the protocol fingerprint covers | `protocol_files` |
| `profile_path(root, profile)` | an agent profile's file | `root / 'docs/agentic-sdd/agents' / f'{profile}.md'` |
| `reviewers(task)` | the reviewer profiles a task selects | `reviewers` (`RISK_TO_REVIEWERS`) |
| `validate_contract(feature_dir)`, `criterion_ids(feature_dir)` | the verification-contract checks | its `verification_contract` shim |
| `state_dir` | the state directory, relative to the common Git directory's parent, or absolute | `STATE_DIR` |
| `legacy_feature_path(root, name)` | where the historical state key's feature lived | `root / 'docs' / 'specs' / name` |

The shim builds the policy **per call** from its module names, so a Showcase test that patches `harness.STATE_DIR`
(15 sites), `harness.protocol_fingerprint`, `harness.save_state` or `harness.load_validated` still reaches the code it
patches. Where a moved function calls another moved name that a Showcase test patches, the shim passes its own name
as a parameter, looked up per call: `common_dir=` (`harness.git_common_dir`, for every state path), `locking=`
(`harness.fcntl`, for the lock guard), `fingerprint=` (`harness.feature_fingerprint`, for the identity checks) and
`fault_injector=` (`harness._state_snapshot_fault_injector`, for the writer). The first two were found by the
rehearsal and the evaluation (below); each has a library test.

## Equivalence criteria (acceptance)

1. **Verbatim:** the diff from the source is only the seams (`provenance.md` lists each hunk); formats (state JSON
   `indent=2, sort_keys=True, allow_nan=False`, trailing newline), error messages and exit codes are unchanged.
2. **One writer:** `save_state` is the only state writer, in the library; it writes through
   `state_snapshots.commit_state_snapshot` exactly as today. The shim keeps no second write path.
3. **Same state location and key:** for a fixture checkout and a linked worktree, `state_path`, `lock_path`,
   `state_key` and the legacy path are byte-identical before and after.
4. **Same fingerprints:** `feature_fingerprint` and `protocol_fingerprint` give the same digest on Showcase's own
   features before and after (protocol files change by the shim itself, which is expected and recorded).
5. **Same validation:** `validate()` returns the same errors, in the same order, on every feature under Showcase's
   `docs/specs` and on the error fixtures of `test_harness`.
6. **Consumer suites:** Showcase's `test_harness`, `test_verification_lifecycle`, `test_verification_authority`,
   `test_orchestrate`, `test_wayfinder` and the rest pass through the shim; the rehearsal shows no difference beyond the
   expected version-pin tests; rollback restores the previous set.
7. **Guards kept:** every `die` and refusal in the moved functions is present; nothing that was refused is accepted.
   The library tests the refusals listed in the evaluation record below (a selection checked by mutation, not a
   claim of full mutation coverage); Showcase's suites cover the rest through the shim.

## Not in AH5-14a

- No change to the AH5-13 findings (`docs/specs/AH5-13/findings-2026-10-20.md`); F1 is to be decided before 14c.
- `generate`'s migration onto `AgentCliBackend` is a separate behaviour-change packet.

## AH5-14a evidence (2026-10-20 to 2026-10-23)

- **Equivalence (criteria 3–5), measured:** in one checkout, Showcase's original `harness.py` and the shim give
  identical `validate()` errors, feature fingerprints, state paths and keys, legacy names and lock paths on all 43
  features under `docs/specs` (the evaluator re-measured 50 directories, protocol fingerprints and `git_common_dir`
  included: no difference). Re-checked after the amendments: identical.
- **Preliminary rehearsal** (library `8518f75`, Showcase `fd96eeb`): CI identical; differences were the expected
  version-pin tests, the shim's new wrapper test, and **one regression**:
  `test_lifecycle_state_fails_closed_when_cross_process_lock_is_unavailable`. The lock guard had moved into the
  library and read its own `fcntl`, so Showcase's test, which removes `harness.fcntl`, no longer reached it. Fixed in
  `4a3219a` (`locking=`), with a library test.
- **Final rehearsal** (`rehearsal-14a`, library `4a3219a`, Showcase `36497846b0e23121ff1d094412b23be5eb19c998`, the
  0.15.0 adoption; `BASE_PINNED=1`, base on Showcase's pin `717f4ba`): **test results identical** (base and
  wrapped: 921 ok, 2 FAIL, 1 ERROR, the baseline failures); CI 21 commands, exit codes identical, output identical
  after masking; rollback wrapped → base, base → wrapped and base → base all ok. Host window
  2026-10-20T11:46:28Z to 2026-10-21T17:32:07Z: the host was suspended for most of it; load 3–8 at the start,
  about 21 at the end.
- **Independent evaluation:** pass with amendments at `4a3219a`. Confirmed: verbatim apart from the seams (AST
  comparison per function), `FeaturePolicy` without defaults, the shim byte-identical otherwise, one writer and the
  unchanged state format, the `fcntl` fix. Amendments, applied:
  1. A patched `harness.git_common_dir` did not reach the moved state functions (one Showcase test still refused,
     for a different reason). Fixed with `common_dir=`; the test refuses with `STATE_RECOVERY_REQUIRED` again, as with
     the original; a library test covers the binding.
  2. The provenance entry was missing: added (`docs/migration/provenance.md`).
  3. 10 refusals survived mutation in the library suite: the design-gate decision, the master-closure fallback, the
     evaluator requirement, integration depending on an evaluator, the heartbeat bound, an exact path inside a glob,
     the forbidden roots, the `agent_profile` format, the missing common directory, the handoff record in the
     fingerprint. Each now has a library test, and each mutation is caught.
  4. The Showcase-origin literals and 5. the binding defaults are recorded in provenance.
- **Non-docs difference from the rehearsed `4a3219a`:** `common_dir=` in `feature_state` and the shim, and the
  added tests. Checked directly: equivalence identical; `test_verification_authority`, `test_replan`,
  `test_packet_identity_bridge` and `test_cutover_wrapper` pass through the shim. `test_harness`, one test per
  process through the shim at `b086960`: 255/255 pass. Three needed the candidate installed (they start `harness.py`
  in a subprocess, which loads the installed library, not the source tree): with `b086960` installed they pass too.

## AH5-14b — boundaries and dependencies (recorded before any code, 2026-10-23)

Authorised by master (2026-10-23): lifecycle transitions. Source: Showcase `harness.py` at `3649784` (unchanged
since `fd96eeb`). Method: the same call graph as 14a, from each transition's command.

**Every lifecycle transition runs on one base.** `cmd_claim`, `heartbeat`/`cmd_heartbeat`, `recover_stale_leases`,
`cmd_release`, `cmd_fail`, `cmd_complete`, `cmd_start`, `cmd_replan_task`, `cmd_reopen`, `cmd_reconcile_feature` and
`ready_ids` all read or mutate state through `locked_state` (lock, load, write) or `_read_state_unlocked_pure`
(read). That base is about 530 lines:

| Group | Functions | Depends on |
|---|---|---|
| Lock and load | `locked_state`, `lifecycle_state_lock`, `_load_state_unlocked`, `_resolve_state_unlocked`, `initial_state`, `_read_state_unlocked_pure`, `fail_on_verification_store_error` | 14a (`save_state`, `state_path`, `lock_path`, `validate_loaded_state`); the two groups below |
| Completion-record readers (read only) | `completion_records`, `repaired_completion_records`, `read_completion_authority`, `validate_completion_authority_shape`, `legacy_completion_bindings`, `validate_legacy_binding_shape`, `legacy_binding_as_completion`, `completion_authority_dir`, `completion_record_id`, `legacy_completion_binding_id`, `canonical_json_sha256`, `safe_task_id`, `_valid_completion_*` | none outside this table |
| Recovery checks on load | `recover_prepared_state_locked` and `state_snapshot_status` (the library's `state_snapshots`); `state_recovery_required` and `state_recovery_incident` | `state_recovery_required` reads Showcase's own incident history and recovery epochs (`state_recovery`, which stays Showcase-owned), with Showcase specifics such as `max_rework_attempts == 2` |

**Consequence.** The transitions cannot move first, as the plan's order (14b before 14d) assumes. Moved alone, they
would call back into Showcase for locking, loading and the recovery check, and a shared integrity rule (refuse
lifecycle access while recovery is required) would stay on the consumer side, which plan rule 6 forbids. By the
plan's own rule, a part that cannot be cut without a behaviour change is split differently.

**Proposed split, for master's confirmation before implementation:**

- **14b-1, state access.** Move the lock-and-load group, the completion-record readers and the generic recovery
  checks verbatim. `state_recovery_required` splits along its own first line: the library keeps the snapshot check
  (`invalid`, `state-missing`, `state-mismatch` → recovery required), and the incident and recovery-epoch part
  becomes a **required consumer hook** `recovery_required(feature_dir, snapshot_status) -> bool`. It is called
  exactly where the source calls `state_recovery_required`, and **it can only refuse**: `True` blocks access, `False`
  leaves the library's own checks in force. It can never mark state as recovered or skip a library check.
  Same order of validation, locking and writing, same errors; `save_state` stays the single writer.
- **14b-2, transitions.** Then claim, heartbeat, release, fail, complete, replan, reconcile, ready and reopen, with
  their CAS and generation guards, on top of 14b-1. They also need packets and worktrees (`write_packet`,
  `resolve_active_packet`, `prepare_task_worktree`, …), mapped in 14b-2's own boundaries.
- **Not moved:** recovery operations (`cmd_recover_state`, `cmd_restore_state`, `cmd_discard_prepared_snapshot`,
  claim recovery) stay for 14d; admission (`admit_verification_execution`, `consume_verification_launch`,
  `terminalize_verification_execution`, the resume validators) for 14c, which stays blocked until F1 is resolved.

Rehearsal for 14b-1 runs on Showcase's committed 0.16.0 adoption (in progress at the time of writing).

## AH5-14b-1 implementation (2026-10-23)

- **Decided (master, 2026-10-23):** the 14b-1/14b-2 split; the refuse-only hook on four conditions (refuse only, never
  bypass a library check or admit a refused state; a refusal, an error or a malformed result blocks; an explicit
  binding, a missing one is not consent; no state writes, no recovery). The library keeps its own integrity checks.
- **Implemented** in `agent_harness.feature_access` (`docs/migration/provenance.md`): verbatim apart from the
  `StateAccess` seam and the hook split. The order of checks, locking and writing is the source's: repository
  verification lock → lifecycle lock → prepared-snapshot recovery → recovery check → load and identity checks →
  transition → `save_state` → post-commit fault points.
- **How the hook conditions are met:** the epoch hook is consulted only after the library's snapshot check and only
  with an incident; `... is not False` makes any value but an exact `False` refuse; exceptions propagate; every
  `StateAccess` field is required (`StateAccess()` and calls without `access=` raise `TypeError`); Showcase's hook is
  its own verbatim read-only code. Each has a library test.

### AH5-14b-1 independent evaluation (2026-10-24)

- **Verdict: pass with amendments** at `c3a563035a1f4cc9b909a27ee70dfa359fca721d`. Confirmed: all 26 moved
  functions AST-identical to the source apart from the declared seams; the order of checks, locks and writes
  unchanged; error strings and exit codes unchanged; every write through `access.save_state` (no second writer); the
  hook split behaviour-identical for every input (the source tail always returns a literal bool), with no way found for
  the hook to lift a library refusal; every patched name reaches the moved code; one shared `_authority_transaction`.
  Library 45 tests; 107 targeted Showcase tests through the shim.
- **Applied:** library tests for four refusals that only Showcase's suites (or neither) pinned: the read-only
  projection's recovery refusal, prepared-snapshot recovery before the recovery check, repository admission before the
  lock, and the pending transaction authority. Each mutation is now caught. The provenance claim is corrected.
- **The incident lookup is a second consumer hook, documented as it is:** `recovery_incident(feature_dir)` returns the
  incident record, or a falsy value for "no incident" (the source's `if not incident`); it may refuse by `die`. A falsy
  result skips only the epoch check; the library's snapshot check still runs. It cannot lift a library refusal.
  **Open for master:** whether to also refuse a result that is neither `None` nor a non-empty dict. That is a behaviour
  change from the source (verbatim today) and is not made without a decision.
- **Condition (d)** (no state writes, no recovery) is a contract the library cannot enforce; Showcase's hook is its own
  verbatim read-only code (file reads and Git reads in `state_recovery`).
- **Informational:** the shim's `lifecycle_state_lock`/`locked_state` wrappers build the binding at call, not at
  `__enter__`; no test patches `fcntl` or `STATE_DIR` between the two.

**AH5-14b-1 released in 0.17.0** (2026-10-24, `137b0ba`). 14b-2 (transitions) follows after its review; 14c stays
blocked by F1.

## AH5-14b-2 — boundaries (pre-decision analysis, 2026-10-24)

The blocker/options below are historical analysis. Master selected Option A and authorized implementation on
2026-10-24; the recorded decision and two-step assignment follow this section.

Source: Showcase `harness.py` at `792dfad` (its 0.17.0 adoption checkpoint; identical to `87e4ad6`). Method: the
transitions' call graph beyond what 0.15–0.17 already moved.

**Size.** The transitions (claim, heartbeat, release, fail, complete, start, replan, reopen, reconcile, ready and the
lease helpers) and what they call add **98 functions, about 2,750 lines**: packets (20 functions, ~550 lines),
worktrees (10, ~270), completion publishing and repair records (17, ~430), human resolution, retry and replan (9,
~610), and the commands themselves with small helpers (26, ~640).

**The blocker.** Several transitions call functions that the recorded boundary assigns to later parts:

| Transition | Calls, assigned to 14c (admission) | Calls, assigned to 14d (recovery) |
|---|---|---|
| `claim` | — | — |
| `release`, `fail` | `validate_verification_resume`, `validate_active_verification_resume` | — |
| `complete` | the two above | — |
| `start` | `validate_pending_verification_resume`, `validate_verification_resume`, `resolve_accepted_verification_plan` | `is_unrecovered_partial_claim`, `task_worktree_snapshot` |
| `heartbeat` | — | `is_unrecovered_partial_claim` |
| `replan` | — | `packet_identity_from_recovery`, `recovery_proves_packet_binding`, `recovery_record_identity` |
| `reopen` | — | `task_workspace_snapshot`, `restore_task_workspace` |
| rollback of an unexecuted `start` | — | `restore_attempt_authorization` |

(`consume_attempt_authorization` and `completion_verification_artifact`, first matched as admission, belong to the
transitions: a human retry grant consumed by `claim`/`start`, and the completion evidence check of `complete`.)

**What they are.** The 14c functions here are **read-only checks**: the resume validators check a task's state
entry against its durable resume history and refuse an inconsistent one; `resolve_accepted_verification_plan` reads
the plan record and the state and raises unless the acceptance is current. The admission operations that change state
(`admit_verification_execution`, `consume_verification_launch`, `terminalize_verification_execution`) are **not** in
the transitions' closure. Of the 14d functions, `is_unrecovered_partial_claim` and the three recovery-identity
functions are read-only; `task_workspace_snapshot`, `restore_task_workspace` and `restore_attempt_authorization`
change the workspace or state inside `reopen` and the start rollback.

**Why it blocks the order.** The transitions cannot move verbatim without these functions; 14c is blocked by F1; F1
needs `start`/`complete` moved (14b-2). As recorded, the three parts wait on each other.

**Proposal, for master's decision (no code before it):**
- **Option A (recommended):** 14b-2 moves the transitions with their own groups (packets, worktrees, completion
  publishing, human/retry/replan) and the **read-only** checks they call from the 14c/14d groups: the three resume
  validators, `resolve_accepted_verification_plan`, `is_unrecovered_partial_claim` and the three recovery-identity
  readers. They are lifecycle integrity rules, which plan rule 6 wants in one library implementation. The **mutating**
  recovery steps (`task_workspace_snapshot`, `restore_task_workspace`, `restore_attempt_authorization`) stay Showcase's
  and are passed in as required bindings, called exactly where the source calls them, until 14d. The admission state
  changes stay for 14c.
- **Option B:** all nine stay Showcase's and are passed in as required bindings until 14c/14d. Fewer lines move now, but
  shared integrity checks stay on the consumer side for longer.
- In both, 14b-2 is large (~2,750 lines): it can ship in two steps (claim/heartbeat/release/fail/ready/reconcile and
  their groups; then start/complete/replan/reopen), each rehearsed on its own.

At the time of this analysis, only the dependency decision was blocked. That decision has since been made and step 1
is committed as `d7db118`; step 2 is the remaining implementation.

### AH5-14b-2 decision and step assignment (master: option A, 2026-10-24)

- **Reassigned to 14b-2**, verbatim, from the 14c/14d groups: `validate_verification_resume`,
  `validate_active_verification_resume`, `validate_pending_verification_resume`, `resolve_accepted_verification_plan`,
  `is_unrecovered_partial_claim`, `packet_identity_from_recovery`, `recovery_proves_packet_binding`,
  `recovery_record_identity`. They change no admission rule and do not close F1.
- **Read-only, checked including the helpers they call:** the only helpers outside earlier releases are
  `parse_aware_timestamp` (its `.replace` is `str.replace`), `packet_identity_from_embedded_checksum`,
  `packet_identity_from_revision`, `canonical_packet_payload_sha256` and `packet_revision_id`: no write, no in-place
  change of their arguments or of state, no Git command, no recovery. `resolve_accepted_verification_plan` constructs a
  `VerificationStore` (paths only; `resolve_control_root` and the constructor create nothing) and reads through
  `load_plan_record`, `load_validated` and `_read_state_unlocked_pure`, none of which writes.
- **Step 1 (shared checks and reads; implemented, `d7db118`):** the eight functions above and those five helpers, in
  `agent_harness.lifecycle_checks`, with `PacketIdentity` and the identity scheme constants.
  `resolve_accepted_verification_plan` takes the released `StateAccess` plus two required bindings:
  `feature_dir_for(root, feature_id)` (Showcase's `docs/specs/<id>`) and `verification_authority()` (its module, per
  call); Showcase's patches of `load_validated`, `_read_state_unlocked_pure`, `feature_fingerprint` and its
  `verification.store` reach it. Library tests: `test_lifecycle_checks` (4) and `test_lifecycle_refusals` (6, every
  refusal; 6 mutations caught).
- **Step 2 (transitions; exact local-function closure from those entry points):** `_bridge_request_identity`,
  `_completion_failpoint`, `_completion_record_semantics`, `_path_is_selected`, `_read_packet_identity_bridge`,
  `active_task_contract`,
  `add_completion_record`, `append_attempt_binding`, `assert_worktree_protocol_current`, `changed_paths`,
  `checkpoint_worktree`, `classify_historical_packet_binding`, `classify_retry_authorizations`, `cmd_claim`,
  `cmd_complete`, `cmd_fail`, `cmd_heartbeat`, `cmd_ready`, `cmd_reconcile_feature`, `cmd_release`, `cmd_reopen`,
  `cmd_replan_task`, `cmd_start`, `completion_verification_artifact`, `consume_attempt_authorization`,
  `correction_is_repaired`, `correction_records`, `correction_targets_current_completion`, `descendants`,
  `ensure_task_workspace_clean`, `execution_protocol_paths`, `heartbeat`, `heartbeat_interval_seconds`,
  `historical_feature_fingerprint`, `lease_expired`, `lease_ttl_seconds`, `legacy_packet_path`, `legacy_revision_id`,
  `owner_guard`, `packet_bound_semantic_contract_sha256`, `packet_identity_bridge_path`,
  `packet_matches_semantic_contract`, `packet_payload`, `parse_timestamp`, `path_matches`, `prepare_task_worktree`,
  `prior_builder_result_digest`, `prune_task_workspace`, `publish_completion_authority`, `publish_revision`,
  `ready_ids`, `refresh_lease`, `reopen_dependency_graph`, `repair_authorization_records`, `repair_claim_records`,
  `replan_failpoint`, `require_available_completion_origin`, `require_task_commit_ancestry`,
  `required_completion_criteria`, `resolve_active_packet`, `retire_finalized_completion_repair_pointers`,
  `retry_binding`, `revision_path`, `rollback_unexecuted_start`, `semantic_task_contract`,
  `semantic_task_contract_sha256`, `stage_first_task_base`, `task_base_commits`, `task_has_effective_completion`,
  `task_worktree_snapshot`, `utc_now`, `validate_attempt_binding_ledger`, `validate_completion_binding`,
  `validate_completion_proofs`, `validate_evidence`, `validate_latest_human_resolution`, `validate_packet_integrity`,
  `validate_replan_task`, `worktree_path`, and `write_packet` (80 functions). This is the AST-resolved local call
  closure of `cmd_claim`, `heartbeat`, `cmd_heartbeat`, `cmd_release`, `cmd_fail`, `cmd_complete`, `cmd_start`,
  `rollback_unexecuted_start`, `cmd_replan_task`, `cmd_reopen`, `cmd_reconcile_feature`, `ready_ids`, and `cmd_ready`,
  after removing 14a/14b-1/step-1 library-owned functions and the Showcase bindings below. The wrapper-only
  `_policy` and `_state_access` remain adapter construction, not transition implementations. **Required Showcase bindings until 14d:**
  `task_workspace_snapshot`, `restore_task_workspace`, `restore_attempt_authorization`, called exactly where the source
  calls them, with their arguments, results and errors; no default, no no-op, no second implementation; a missing
  binding is detected before an operation that can change state starts. Stale-lease recovery (`recover_stale_leases`,
  `cmd_recover_stale`) stays for 14d.
- Each step leaves a working set, one writer (`save_state`) and the shared `_authority_transaction`. Two steps do not
  imply two releases.
- The incident-lookup fix (`incident-lookup-fail-closed`) and F1 stay separate in diffs and evidence.

### AH5-14b-2 step 1 closed (2026-10-24)

- **Code:** `d7db1185747aeba578ea7cd966c01d40a529bd1f`. Later commits on the branch (`8e3de4a`, `149b3f7`, `e7686ec`,
  `197a98d`, `8fee4cb`) change only `docs/`.
- **Harness CI:** the workflow runs only on pushes and pull requests to `main`, so branch pushes started none; run
  [40082420228](https://github.com/chrobakpiotr/agent-harness/actions/runs/40082420228) (manual `workflow_dispatch`
  on the branch) for `8fee4cb` (code identical to `d7db118`): **success**. No empty commit was made.
- **Library:** wheel check 604 tests OK, 2 skipped (Showcase's run); `test_lifecycle_checks`, `test_lifecycle_refusals`,
  6 mutations caught.
- **Showcase through the shim at `792dfad`:** 294/299 in a full local run; the other five are the two baseline
  failures, `test_verification_workspace` and `test_verification_sandbox_doctor` (environment in this macOS copy,
  identical with the released shim), and `test_verification_lifecycle`'s `/private/var` vs `/var` fixture comparison,
  test-side only and fixed by Showcase in `d067955` (CI green); with that fix, `test_verification_lifecycle` and
  `test_verification_authority` pass (26 tests) against `d7db118`.
- **Count reconciliation:** 98 = 13 + 80 + 5 (ownership table, `feature_transitions` row).
- **AH5-13 validation copies:** none remain. Showcase's `verification_contract.py` at `d067955` is byte-identical to the
  released shim; what is local is its repository/layout bindings and the separate `generate` package.
