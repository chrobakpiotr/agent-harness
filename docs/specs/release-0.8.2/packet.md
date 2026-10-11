# Version 0.8.2 — reset time from Claude's session-limit message

- Status: accepted (release ordered by the owner 2026-10-11). Distribution: annotated Git tag `v0.8.2`; consumers pin
  the tag SHA.
- Scope: AH5-05b pilot handover (`docs/specs/AH5-05b/packet.md`): `resets_at` parsed from Claude's real message.
  `probe_digest` unchanged, so the shipped reviewed reference is valid.

## Acceptance criteria

- AC1: version 0.8.2, CHANGELOG entry, README install line.
- AC2: `scripts/check-wheel.sh` green on the release commit; CI green.
- AC3: annotated tag `v0.8.2` pushed with `main`; a fresh install from the tag SHA reports 0.8.2 and its shipped
  reference matches the installed probe digest.
