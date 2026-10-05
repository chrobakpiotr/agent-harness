# AH5-00 — Operating contract and bootstrap

- Status: accepted.
- Source: `agent-harness-agent-plan-2026-10-05.md`, section AH5-00, and
  `master-agent-handover-2026-10-05.md` (external planning documents).
- Base: empty repository (no commits), remote `origin` untouched.

## Allowed paths

`AGENTS.md`, `CLAUDE.md`, `.gitignore`, `docs/agentic-sdd/constitution.md`, `docs/specs/AH5-00/`,
`pyproject.toml`, `src/agent_harness/__init__.py`, `src/agent_harness/__main__.py`, `tests/`,
`scripts/check-wheel.sh`, `.github/workflows/ci.yml`, `LICENSE`.

## Out of scope

Copying any Showcase source, execution contract types (AH5-02),
source map (AH5-01), commits, push, PR, publication.

## Acceptance criteria

- AC1: a wheel built from the checkout installs into a fresh venv and `import agent_harness`
  plus `agent-harness --help` work from an empty cwd outside the checkout.
- AC2: importing the package has no runtime side effects (no files written, no output,
  cwd and environment unchanged).
- AC3: the agent operating rules (read order, Git policy, shared contract) are available
  locally in the repository.

## Verification

`scripts/check-wheel.sh` (build → fresh venv → import/help from empty cwd → unit tests
against the installed wheel), `ruff check`, `git diff --check`. CI runs the same script.

## Evidence (2026-10-05)

Environment: macOS 24.6.0, CPython 3.9.6 and 3.13.16 (via uv), ruff 0.16.10.

- AC1: `scripts/check-wheel.sh` → wheel `agent_harness-0.0.1-py3-none-any.whl` built, installed in a fresh
  venv, `agent-harness --help` from an empty dir, module resolved from `site-packages`, 4/4 tests OK on 3.9 and 3.13.
- AC2: `test_import_has_no_side_effects` (empty stdout/stderr, empty cwd). Mutation check: appending a
  file write to `__init__.py` made it and `test_help_works_offline_from_empty_cwd` FAIL.
- AC3: `AGENTS.md`, `CLAUDE.md`, `docs/agentic-sdd/constitution.md`; `test_relative_links_resolve` OK.
- `ruff check .` clean; `git diff --check` clean (intent-to-add, then reset).
- CI workflow `.github/workflows/ci.yml` written; NOT RUN (nothing pushed).
- Licence: MIT; `LICENSE` copied from Showcase (same copyright holder).
