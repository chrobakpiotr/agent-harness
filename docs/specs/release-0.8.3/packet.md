# Version 0.8.3 — OpenCode on free OpenRouter models

- Status: accepted (release ordered by the owner 2026-10-11). Distribution: annotated Git tag `v0.8.3`; consumers pin
  the tag SHA.
- Scope: AH5-05b, third provider (`docs/specs/AH5-05b/packet.md`), for the agent-benchmark AB5-07 request, plus the
  login-file path false positive. `probe_digest` unchanged, so the shipped reviewed reference is valid.

## Acceptance criteria

- AC1: version 0.8.3, CHANGELOG entry, README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit; CI green.
- AC3: annotated tag `v0.8.3` pushed with `main`; a fresh install from the tag SHA reports 0.8.3 and its shipped
  reference matches the installed probe digest.

## Evidence (2026-10-11)

- AC1: `__version__` 0.8.3; CHANGELOG `## 0.8.3 — 2026-10-11`; README pins `@v0.8.3`.
- AC2: `scripts/check-wheel.sh` on the release commit: 229 tests OK, 2 skipped. CI run 38158250121 for `53b6abf`: success.
- AC3: `refs/tags/v0.8.3` = `5e3ec353` → commit `53b6abf375f4928933d3d8def4d4841e8867626c`, pushed with `main`.
  Fresh CPython 3.13 venv from the tag: `agent-harness 0.8.3`; the shipped reference matches the installed probe
  digest (`sha256:ee87222a…`).
