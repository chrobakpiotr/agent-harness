# Version 0.8.0 — retries, Linux targets, qualified grading layout

- Status: accepted (release ordered 2026-10-11). Distribution: annotated Git tag `v0.8.0`; consumers pin the tag SHA.
- Scope: AH5-04c follow-ups (`docs/specs/AH5-04c/packet.md`, "Retries, Linux targets, grading-layout qualification").
  Two independent evaluation rounds (conditions fixed); independent review of the new reference (pass).
- Consumers: agent-benchmark grading sessions; Showcase 04c. A session needs this version's shipped reference.

## Acceptance criteria

- AC1: version 0.8.0, CHANGELOG entry, README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit; CI green.
- AC3: annotated tag `v0.8.0` pushed with `main`; a fresh install from the tag SHA ships a passing reviewed reference
  whose probe and policy digests equal the installed ones and whose tuple states `grading_layout: qualified`.
