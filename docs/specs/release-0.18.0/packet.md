# Version 0.18.0 — lifecycle transitions (AH5-14b-2), supported RetryControl (AH5-API-1), fail-closed incident lookup

- Status: **released** 2026-10-24 on the owner's order. Distribution: annotated Git tag `v0.18.0`; consumers pin the tag
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

## Evidence (2026-10-24)

- AC1: `__version__` 0.18.0; CHANGELOG `## 0.18.0` naming each change and its kind; README pins `@v0.18.0`.
- AC2: `scripts/check-wheel.sh` on the release code (`a204a3b`; `6d1c69a` adds only docs): 617 tests OK, 2 skipped.
  CI run 40096877972 for `6d1c69a` on `main`: success (Offline package checks, Python 3.13).
- AC3: `refs/tags/v0.18.0` = `c3cc1bd4` → commit `6d1c69a170dee8638b5535aed3f9aadb57845ba6`, pushed with `main`.
  - A fresh CPython 3.13 venv from the tag SHA reports `agent-harness 0.18.0`; `feature_transitions`,
    `lifecycle_checks` and `retry_control` import; `retry_control.RetryControl` is the library's own type.
  - The shipped reference carries the installed probe digest (`sha256:ee87222a…`), as in 0.17.0.
- **Branch protection:** the push of `6d1c69a` to `main` was accepted as an admin bypass (no pull request, unsigned
  commits, required checks not run before the push), under the owner's direct-push decision. CI ran after the push
  and passed. The bypass does not mean those protections were satisfied.

## Consumer handoff

| Consumer | Pin now | Adoption of 0.18.0 | Rollback |
|---|---|---|---|
| Showcase | v0.17.0 `137b0ba` (`87e4ad6`, code checkpoint `792dfad`) | **1)** pin `6d1c69a`; copy `harness.py` (mode 100755) from `migration/showcase-wrapper/` (it carries the step-2 bindings and the fail-closed incident lookup) and `tests/test_cutover_wrapper.py`; move the three version-pin tests to 0.18.0; run CI. Report the incident fix's adoption separately (its own check: an invalid lookup result refuses before it could count as "no incident"). **2)** separately: switch `verification/retry_controls.py` to `agent_harness.retry_control.RetryControl`, use `inputs_digest()`, raise its own adapter error instead of `SupervisorError`; confirm both control digests and the callback refusal through the real integration | the whole 0.17.0 set: pin `137b0ba`, the previous `harness.py` and test copies; the retry change reverts on its own |
| agent-benchmark | v0.14.0 `cc9d33a` | not needed: none of the changed modules is used; `contract`, `execution` and `qualification` are unchanged | — |
