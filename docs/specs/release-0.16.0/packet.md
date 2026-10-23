# Version 0.16.0 — feature documents and state I/O (AH5-14a)

- Status: **released** 2026-10-23 on the owner's order. Distribution: annotated Git tag `v0.16.0`; consumers pin the tag
  SHA.
- Scope: AH5-14a `agent_harness.feature` and `agent_harness.feature_state` (`docs/specs/AH5-14/packet.md`). The
  contract, `probe_digest`, the shipped reviewed reference and the supported API are unchanged. No behaviour change.

## Acceptance criteria

- AC1: version 0.16.0, a CHANGELOG entry and the README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit, and CI green.
- AC3: annotated tag `v0.16.0` pushed with `main`. A fresh install from the tag SHA reports 0.16.0, and its shipped
  reference matches the installed probe digest.

## Pre-release evidence (2026-10-23)

- **Evaluation:** pass with amendments at `4a3219a`; amendments applied in `b086960` (`docs/specs/AH5-14/packet.md`).
- **Rehearsal** (`rehearsal-14a`, library `4a3219a`, Showcase `3649784`, its 0.15.0 adoption, base on its pin
  `717f4ba`): test results identical; CI identical after masking; rollback ok in all three directions.
- **Non-docs difference from the rehearsed `4a3219a`:** the `common_dir=` binding, the added tests and the version.
  Checked directly: equivalence on all Showcase features identical; `test_harness` 255/255 and the other affected
  suites pass through the shim with the candidate installed.
- **Consumers:** Showcase adopts with the `harness.py` shim (a behaviour-identical cutover). agent-benchmark does not
  use these modules; a re-pin is optional.
- **Wheel:** `scripts/check-wheel.sh` on `9c24492`: 576 tests OK, 2 skipped. Later commits change only `docs/`.

## Evidence (2026-10-23)

- AC1: `__version__` 0.16.0; CHANGELOG `## 0.16.0`; README pins `@v0.16.0`.
- AC2: `scripts/check-wheel.sh` on the release code (`9c24492`; `fc4ad64` adds only docs): 576 tests OK, 2 skipped.
  CI run 40025422532 for `fc4ad64`: success (Offline package checks, Python 3.13).
- AC3: `refs/tags/v0.16.0` = `7aae296d` → commit `fc4ad6471b9dcec65a17e61ab0975c1de14f79dc`, pushed with `main`.
  - A fresh CPython 3.13 venv from the tag SHA reports `agent-harness 0.16.0`; `feature` and `feature_state`
    import, and `FeaturePolicy()` without its fields raises `TypeError`.
  - The shipped reference carries the installed probe digest (`sha256:ee87222a…`).
- **Branch protection:** the push of `fc4ad64` to `main` was accepted as an admin bypass (no pull request, unsigned
  commits, required checks not run before the push), under the owner's direct-push decision. CI ran after the push
  and passed. The bypass does not mean those protections were satisfied.

## Consumer handoff

| Consumer | Pin now | Adoption of 0.16.0 | Rollback |
|---|---|---|---|
| Showcase | v0.15.0 `717f4ba` (`68d22c7`) | pin `fc4ad64`; copy `harness.py` (mode 100755) from `migration/showcase-wrapper/`, plus the other shims at that SHA (unchanged since 0.15.0); move the three version-pin tests to 0.16.0; run CI | the whole 0.15.0 set: pin `717f4ba` and the previous `harness.py` |
| agent-benchmark | v0.14.0 `cc9d33a` | not needed: it does not use `feature`/`feature_state`, and `contract`, `execution` and `qualification` are unchanged | — |

### Showcase adoption (its commits, checked here 2026-10-23)

- Adoption commit `1b22802d52f59c1629e20edbc7ec7aaf09761d79`, docs commit
  `31bab531abd8dff3a147ce040c1b4a94016d99d9`; pin `fc4ad64`.
- Checked here: `harness.py` (mode 100755), `verification_contract.py`, `runner.py`, `verify.py`,
  `tests/test_cutover_wrapper.py` and `tests/test_runner_launch.py` are byte-identical to v0.16.0. The other changes
  are the version constant, the three version-pin tests (0.15.0 → 0.16.0), its handbook delta and handover.
- CI (Showcase's report, 2026-10-23): the adoption commit's main `CI` run was superseded by the docs-only push. On
  the descendant `31bab53`, main `CI` failed only on the **frontend audit**: five findings (one low, four high) in
  unchanged frontend dependencies, with Angular major upgrades suggested; no frontend dependency file changed in the
  cutover. Backend, Playwright, OWASP, Trivy, SBOM, infrastructure, PIT, repository guards and documentation links
  passed; Agentic SDD CI, CodeQL and Scorecard passed; Dependency Review was skipped (recorded coverage gap).
- Showcase's report: full local Agentic SDD suite 924 passed; `test_harness` 255 passed through the shim; rollback in a
  disposable clone restored the parent tree exactly (`4afaea1b02a54dba2dea98de9f1a4088fb04c8c2`). Direct pushes
  bypassed the PR requirement, two required checks and signatures; the bypasses are not counted as passed checks.
- The frontend audit is pre-existing and outside the cutover; Showcase owns it.
