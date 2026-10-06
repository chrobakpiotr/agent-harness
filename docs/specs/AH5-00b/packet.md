# AH5-00b — Versioned shared-contract copies with a drift check

- Status: **accepted** (ordered 2026-10-06).
- Source: constitution "Ownership" rule (consumers receive versioned copies: version + digest + drift check;
  updates by review, never auto-following `main`); AH5-00. Consumers: agent-benchmark (`docs/constitution.md`,
  today a hand-condensed copy), Showcase (its own constitution stays authoritative for Showcase-specific rules).

## Scope

- The canonical text ships in the wheel as `agent_harness/constitution.md`, byte-identical to
  `docs/agentic-sdd/constitution.md` (enforced by a test that runs the same check consumers run).
- CLI (public): `agent-harness constitution` prints the canonical text; `--digest` prints
  `constitution <version> sha256:<hex>`; `--check PATH` exits 0 when PATH is byte-identical to the canonical text
  of the installed version, 1 when it drifted, 2 when it cannot be read.
- Consumer procedure in `docs/migration/consumers.md`: keep a verbatim copy, local rules in a separate file, run
  `--check` in CI against the pinned version; updating = re-pin, then copy the new text through review.

## Acceptance criteria

- AC1: `--check` passes on an exact copy and fails (exit 1) on a one-byte change, a missing trailing newline,
  and another version's text; unreadable path exits 2.
- AC2: the repository's own `docs/agentic-sdd/constitution.md` passes `--check` against the shipped text.
- AC3: the installed wheel prints the same digest from an empty directory; `scripts/check-wheel.sh` green.

## Evidence (2026-10-06)

- AC1: `tests/test_constitution.py` (CLI as a subprocess): exact copy → 0; a one-character change, a missing
  trailing newline and another version line → 1; missing path → 2. A whitespace-tolerant comparison
  (mutation) fails the suite.
- AC2: `docs/agentic-sdd/constitution.md` passes `--check` against the shipped text (same test).
- AC3: `scripts/check-wheel.sh`: 138 tests OK, 1 skipped; the constitution tests run against the installed wheel
  from an empty directory. Digest: `constitution 1.0.1 sha256:15cecfb87da0cb0d9ef2e47310b5ff9330992e50de05ef449b472988a63dcb04`.
- The text gained the check command in its ownership rule, so the version moved to 1.0.1: the 1.0.0 text in
  the `v0.1.0`/`v0.2.0` trees differs, and a version names exactly one text.
- agent-benchmark's `docs/constitution.md` reports drifted (hand-condensed, not a copy); adopting a verbatim copy
  is its change, after a tag that ships this command.

## Independent evaluation (2026-10-06)

Fresh-context evaluator verdict on `2780fc2`: **fail** — `--check ""` (an unset CI variable) skipped the check
and exited 0. Fix: an empty path is opened like any other and fails with 2; a directory is 2; the file's size is
compared before reading it (a large file is not read whole); `--help` and `consumers.md` say 2 also means a
usage error; `consumers.md` says the command needs a release after `0.2.0`. Tests cover the empty path, a
directory and a larger file; restoring the truthy check (mutation) fails them. Everything else held (wheel
contents byte-identical, byte-exact checks for CRLF/BOM/symlinks, print without added newline, 1.0.1 bump
consistent, benchmark copy truthfully drifted).

## Re-check (2026-10-06)

Fresh-context verdict on `bc79e22`: **fail** — a FIFO with no writer hung `--check` forever, and the read was
unbounded for a pipe or a growing file. Fix: only a regular file is a copy (FIFOs, devices → 2), and the read is
capped at the canonical length + 1; a FIFO test with a timeout fails if the regular-file guard is removed.
`/dev/stdin` is deterministic: refused (2) when stdin is a pipe or device, checked when redirected from a file. Builds from `main` are `0.3.0.dev0`, so a wheel with
this command is distinguishable from `0.2.0`.

## Final re-check (2026-10-06)

Verdict on `aa2da19`: **pass** (FIFO, symlink to FIFO, `/dev/zero`, directory, empty path → 2; growing file and
len+1 bytes → 1; `--version` `0.3.0.dev0`). Closed afterwards: the read cap is protected by a test asserting the CLI
reads at most the canonical length + 1 bytes (removing the cap fails it); the file is opened non-blocking and its type checked on the open
handle, so a path swapped to a FIFO between check and read cannot block.
