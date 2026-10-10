# Version 0.6.1 — Claude may run sandboxed test commands

- Status: accepted (release ordered 2026-10-10). Distribution: annotated Git tag `v0.6.1`; consumers pin the tag SHA.
- Scope: AH5-05b calibration fix (`docs/specs/AH5-05b/packet.md`, "Calibration fix"): Claude `permissions.allow:
  ["Bash"]` under the unchanged sandbox; a diagnostic smoke test. Everything valid under v0.6.0 stays valid.
- Consumer: agent-benchmark AB5-07 pilot (equal conditions for Claude and Codex).

## Acceptance criteria

- AC1: version 0.6.1, CHANGELOG entry, README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit; CI green.
- AC3: annotated tag `v0.6.1` pushed with `main`; a fresh install from the tag SHA reports 0.6.1.
