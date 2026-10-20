# AH5-14 — Move `harness.py` in parts; AH5-14a: document and state I/O

- Status: **AH5-14a design** (authorised by master, 2026-10-20); implementation follows on branch
  `ah5-14a-document-state-io`. 14b–e are later packets.
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
(15 sites), `harness.feature_fingerprint`, `harness.protocol_fingerprint`, `harness.git_common_dir`,
`harness.save_state`, `harness.load_validated` or `harness._state_snapshot_fault_injector` still reaches the code it
patches. Moved functions that a Showcase test patches and that another moved function calls get that callee from the
policy or a parameter, never from a frozen import: `save_state(..., fault_injector=)` takes the injector per call;
`validate_state_identity`/`validate_loaded_state` call the fingerprint through the shim's binding.

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
7. **Guards kept:** every `die` and refusal in the moved functions is present and tested in the library; nothing that
   was refused is accepted.

## Not in AH5-14a

- No change to the AH5-13 findings (`docs/specs/AH5-13/findings-2026-10-20.md`); F1 is to be decided before 14c.
- `generate`'s migration onto `AgentCliBackend` is a separate behaviour-change packet.
