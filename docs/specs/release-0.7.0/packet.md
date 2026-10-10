# Version 0.7.0 — grading on the qualified target

- Status: accepted (release ordered 2026-10-10). Distribution: annotated Git tag `v0.7.0`; consumers pin the tag SHA.
- Scope: AH5-04c (`docs/specs/AH5-04c/packet.md`): `QualifiedDockerBackend`; `qualification.qualify()` with the moved
  probes; the shipped reviewed reference; the ADR 0002 amendment. Independent evaluations of the backend and of
  `qualify` (pass with conditions, all fixed); independent review of the reference (pass). Everything valid under
  v0.6.1 stays valid.
- Consumers: agent-benchmark AB5-07 grading; Showcase 04c.

## Acceptance criteria

- AC1: version 0.7.0, CHANGELOG entry, README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit; CI green.
- AC3: annotated tag `v0.7.0` pushed with `main`; a fresh install from the tag SHA ships a passing reviewed reference
  whose probe and policy digests equal the installed ones.
