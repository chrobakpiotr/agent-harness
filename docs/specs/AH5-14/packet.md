# AH5-14 — Move `harness.py` in parts; AH5-14a: document and state I/O

- Status: **AH5-14a implemented** on branch `ah5-14a-document-state-io`; rehearsed against Showcase's 0.15.0
  adoption and evaluated (pass with amendments, applied). Release on the owner's order. 14b–e are later packets.
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
  `test_packet_identity_bridge` and `test_cutover_wrapper` pass through the shim; `test_harness` per test (below).
