# AH5-03b — Repository/state compatibility

- Status: accepted.
- Source: master plan AH5-03b; depends on AH5-03a. Showcase input `50c18f9` (read-only).

## Allowed paths

`src/agent_harness/repository.py`, `tests/test_repository.py`, `docs/adr/0003-state-location-compatibility.md`,
`docs/migration/`, `docs/specs/AH5-03b/`.

## Acceptance criteria

- AC1: linked worktrees share the same authority; two separate repositories do not.
- AC2: missing, symlinked and invalid roots fail closed.
- AC3: resolution never writes state (no state on import or resolution).
- AC4: tested on disposable Git repositories/worktrees, including a changed process cwd.
- AC5: old format read only under an explicit compatibility policy; no silent migration.

## Evidence (2026-10-05)

- AC1: `test_linked_worktrees_share_authority_but_not_workspace`, `test_separate_repositories_never_share_authority`;
  parity: Showcase `verification.store.resolve_control_root` and `agent_harness.repository.resolve_control_root`
  return identical root and identity for a main checkout and its linked worktree (CPython 3.13.16).
- AC2: `test_invalid_inputs_fail_closed`, `test_redirected_or_invalid_state_roots_fail_closed`.
- AC3: `test_resolution_is_read_only`; `test_import_has_no_side_effects`.
- AC4: `test_result_does_not_depend_on_cwd_or_entry_subdirectory`; Git 2.39.5.
- AC5: ADR 0003 (no state reader exists yet; the refusal rule binds the readers that move in AH5-04a).
- `scripts/check-wheel.sh`: 41/41 OK on CPython 3.13.16; 40 OK + 1 skipped on 3.9.6; ruff clean.
