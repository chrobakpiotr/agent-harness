# Harness review of Showcase SDD-OBS-001 T-001 worktree diff (2026-10-09)

- Scope: read-only review of the uncommitted diff in the `SDD-OBS-001/T-001` worktree (base `78ea4dd`, attempt 4,
  running): `verification/fingerprint.py`, `model.py`, `planner.py`, `tests/test_verify.py`. All four are inside the
  task's `allowed_paths`.
- Verdict: **pass**, with two low notes.

## Evidence

- Registered verification: `test_verify.py` + `test_verification_profile.py` — 51 tests OK under Python 3.13.
- Differential test, base (`HEAD`) against new `changed_surface` + `observe_inputs` on 13 scratch repositories. The
  manifest and content hashes are identical for:
  - a clean glob;
  - a modified file;
  - an untracked file under a glob;
  - an ignored file under a glob or at the root;
  - a deleted tracked file;
  - a file committed after the base;
  - a broad `**/*.md` pattern.

  Every difference fails closed:

  | Case | Base | New |
  |---|---|---|
  | `--skip-worktree` file deleted | not on the changed surface | on the surface (gate applies) |
  | zero-match input pattern | phantom manifest entry, cacheable | `non-cacheable-policy` (spec: "zero-match patterns are non-cacheable and always run fresh") |
  | literal directory pattern (`lib`) | empty manifest, cacheable — directory contents never fingerprinted | `non-cacheable-policy` |
  | symlinked prefix directory | `non-cacheable-symlink-input` | same plus `non-cacheable-policy` |
  | case-variant `.Agent-State/` | observed and cacheable | excluded, non-cacheable |

## Notes (low)

1. `_is_control_path` case-folds. On a case-sensitive filesystem (Linux CI) a distinct `.Agent-State/` or `.GIT/` tree
   is dropped from the changed surface, so a gate whose applicability names it would not trigger. Inputs there are
   non-cacheable, so no stale cache reuse; fold only where the filesystem is case-insensitive, or keep it and document.
2. A literal directory input pattern is now always non-cacheable (correct; the base silently fingerprinted nothing).
   Profiles that want caching must use `dir/**`.

Performance: the macOS figures (5.30 s median, 8.75 Git subprocesses per run) are within the 10 s/20 s limits;
Linux CI stays the normative evidence, as the handoff says.

## Lifecycle checkpoint

The builder does not commit. The checkpoint comes from the lifecycle: the evaluator's PASS evidence goes to
`harness.py complete docs/specs/SDD-OBS-001 T-001 --owner codex-sdd-obs-t001-after-runner-fix-20261008 --evidence
<pass-evidence.json>`. That checks the binding and `allowed_paths` and records the checkpoint commit. Run it with the
primary checkout's `harness.py`, not the worktree's copy.
