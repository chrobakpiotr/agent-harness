# AH5-11 — Move the verification CLI (`verify.py`)

- Status: **accepted**. One small packet in the plan's order (owner, 2026-10-18). Source: Showcase `49ab97f`.
- Allowed paths:
  - `src/agent_harness/verify_cli.py`
  - `tests/test_verify_cli.py`
  - `migration/showcase-wrapper/verify.py`
  - `migration/showcase-wrapper/tests/test_cutover_wrapper.py`
  - `docs/migration/{provenance,cutover}.md`
  - `docs/specs/AH5-11/`, `docs/specs/release-*/`
  - `pyproject.toml`, `CHANGELOG.md`, `README.md`, `src/agent_harness/__init__.py`

## Source and test map

| Showcase (`49ab97f`) | Library | Notes |
|---|---|---|
| `tooling/agent-harness/verify.py` (`a8a22113`) | `agent_harness/verify_cli.py` | Verbatim apart from the seams below. |
| `tooling/agent-harness/tests/test_verify.py` (`c5f354e3`, 49 tests) | `tests/test_verify_cli.py` (50) | The 45 planner, fingerprint and serialization tests move with their modules; they had stayed in Showcase. The 4 CLI tests run in-process. One seam test is added. |
| — | `migration/showcase-wrapper/verify.py` | Shim: `sys.path` setup, the shebang and `__main__`, late-bound Showcase bindings. Mode 100644, as the source. |

## Explicit behaviour changes

- The library CLI has no built-in profile root, store, resolver or executor. A consumer must pass all four.
  Through the Showcase shim, behaviour is unchanged.
- None for Showcase. Its `test_verify` passes 49/49 through the shim, and the CI workflow does not call
  `verify.py`.

## Consumer update

- **Showcase:** at the re-pin to the release containing AH5-11, copy `migration/showcase-wrapper/verify.py` over
  `tooling/agent-harness/verify.py`. No test removals. From the freeze notice, `verify.py` is a frozen copy.
- **agent-benchmark:** none. `verify_cli` is internal (ADR 0001 amendment).

## Acceptance criteria

- AC1: the ported tests pass in the library, with a seam test and a mutation check.
- AC2: through the shim, Showcase's `test_verify` and `test_cutover_wrapper` give the same results as unchanged
  Showcase. A full rehearsal at `49ab97f` gives the 0.12.0 results.
- AC3: `scripts/check-wheel.sh` and CI are green, and a fresh-context evaluation passes.

## Evidence (2026-10-18)

- AC1:
  - `test_verify_cli`: 50 OK.
  - Mutating the profile root (`profile_root.parent`) fails the seam test.
- AC2:
  - Scratch copies of Showcase `49ab97f`: `test_verify` 49 OK on both sides.
  - `test_cutover_wrapper` OK on the wrapped side, including the new `verify` binding test.
