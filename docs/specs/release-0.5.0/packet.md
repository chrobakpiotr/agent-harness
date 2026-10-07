# Version 0.5.0 — candidate targets for a first qualification

- Status: accepted (release ordered 2026-10-07). Distribution: annotated Git tag `v0.5.0`; consumers pin the
  tag SHA. No GitHub Release and no PyPI package until AH5-06.
- Scope: contract v2 amendment (ADR 0005, "Amendment: candidate targets"; independent check pass): an `unqualified`
  result may carry a candidate `target` with `qualification_digest: null`, so a target's first qualification can
  record B10; `validate_target(target, qualified=False)`; fixture `v2-candidate-target.json`. Everything valid under
  v0.4 stays valid.
- Consumer: Showcase resumes AH5-04B-QUAL-001 T-003 (B10 with candidate grading results) and pins this tag in T-004.

## Acceptance criteria

- AC1: version 0.5.0, CHANGELOG entry, README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit.
- AC3: annotated tag `v0.5.0` pushed with `main`; a fresh install from the tag SHA validates both v2 fixtures.

## Evidence (2026-10-07)

- AC1: `__version__` 0.5.0; CHANGELOG `## 0.5.0 — 2026-10-07`; README pins `@v0.5.0`.
- AC2: `scripts/check-wheel.sh` on the release commit: 166 tests OK, 1 skipped.
- AC3: `refs/tags/v0.5.0` = `de22ab60` → commit `16bb93821292096b94889306288ee2caec5f4006`, pushed with `main`
  (`705ded1..16bb938`). Fresh CPython 3.13.16 venv from the tag SHA, empty directory: `agent-harness 0.5.0`;
  `v2-limit-exceeded` and `v2-candidate-target` validate.
- GitHub Actions for `16bb938`: completed success.
