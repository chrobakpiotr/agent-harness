# ADR 0003 — State location and compatibility

- Status: **Accepted**
- Date: 2026-10-05
- Code: `src/agent_harness/repository.py`

## Decision

- The library derives state locations exactly as Showcase does, from Git — never from the module
  location or the process cwd: repository identity = sha256 of the absolute Git common dir; authority root =
  `<primary worktree>/.agent-runs/control/verification-v2`; evidence root = `<primary worktree>/.agent-runs`.
  All linked worktrees therefore share one authority; separate repositories never do.
- Resolution is read-only. Nothing is created or migrated at import or resolution time.
- Fail closed: missing/non-Git paths, bare repositories, an ambiguous primary worktree, an unborn `HEAD`,
  and a symlinked or non-directory state root are errors with stable codes.
- Compatibility: existing Showcase state is read in place, in its current format. Records with an unknown
  `schema_version` are refused, never upgraded. Any format change needs its own ADR with backup and
  downgrade, and is never applied silently.
- Single writer: until cutover only Showcase writes these roots; a repository uses either the Showcase
  scripts or the pinned library, never both.
- The lifecycle store `<common dir>/../.agent-state` moves with the lifecycle module and is not part of
  `RepoContext` v1.

## Consequences

Identity follows the common-dir path: moving or re-cloning a repository yields a new identity, as in Showcase.
