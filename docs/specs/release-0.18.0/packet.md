# Version 0.18.0 — lifecycle transitions (AH5-14b-2), supported RetryControl (AH5-API-1), fail-closed incident lookup

- Status: prepared; release on the owner's order. Distribution: annotated Git tag `v0.18.0`; consumers pin the tag
  SHA.
- **Not a single verbatim migration.** It contains one verbatim move, one additive API and one behaviour change in the
  Showcase adapter, each as its own commit with its own criteria and evidence:

| Change | Kind | Commits | Evidence |
|---|---|---|---|
| AH5-14b-2 step 1 (read-only checks) | verbatim move | `d7db118` | `docs/specs/AH5-14/packet.md`: CI 40082420228; Showcase 294/299 through the shim; 6 mutations caught |
| AH5-14b-2 step 2 (transitions) | verbatim move | `0d64733`, review amendments (tests) `b6edb43` | AH5-14 packet: review pass with amendments (applied); Showcase 295/299 through the shim; rehearsal on Showcase `d067955` identical, rollback ok |
| AH5-API-1 `retry_control.RetryControl` | additive supported API | `39fd164` | `tests/test_retry_control.py`, `test_supported_api`; Showcase's digest comparison for both controls (equal) before adoption |
| Fail-closed incident lookup | behaviour change in the Showcase adapter | `ff06352` | AH5-14 packet: wrapper test with mutation; Showcase's incident/recovery tests through the shim |
| Supported-API wording | docs | `f9d5d3d`, `88b33f1` | qualification sources unchanged (`probe_digest`) |

## Acceptance criteria

- AC1: version 0.18.0, a CHANGELOG entry naming each change and its kind, and the README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit, and CI green.
- AC3: annotated tag `v0.18.0` pushed with `main`. A fresh install from the tag SHA reports 0.18.0, and its shipped
  reference matches the installed probe digest.

## Pre-release evidence (2026-10-24)

- **Integrated candidate** `dc81534bc1a1d0b3bc5cd39323ba109f0151ffd0` (branch `release-0.18.0-candidate`, the five
  changes merged): `scripts/check-wheel.sh` 617 tests OK, 2 skipped; Harness CI run 40095229489 (dispatch) success.
- **The combined shim** (step 2 and the incident fix together), with the integrated build installed, against Showcase
  `d067955`: `test_cutover_wrapper`, `test_state_recovery`, `test_replan`, `test_verification_authority`, and the
  `test_harness` incident, recovery, lock-ordering, lock-guard and claim-recovery tests pass.
- **Caught during integration:** a docstring line added to `qualification/__init__.py` changed `probe_digest` (its
  source is part of the digest) and broke the shipped reviewed reference; the line was removed (`88b33f1`) and the
  shipped-reference test passes. The qualification sources are identical to 0.17.0.
- **Consumers:** Showcase adopts the `harness.py` shim (pin, shim, three version-pin tests) and separately the retry
  API; agent-benchmark uses none of this and stays on 0.14.0.
- **Release candidate** `a204a3b66190b7bb60590d3cb19cd1e643a55dea` (version 0.18.0): `scripts/check-wheel.sh` 617
  tests OK, 2 skipped; Harness CI run 40096074883 (dispatch) success. Later commits change only `docs/`.
