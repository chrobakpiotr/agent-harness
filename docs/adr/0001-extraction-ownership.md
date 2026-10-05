# ADR 0001 — Extraction ownership and order

- Status: **Proposed** (pending independent review)
- Date: 2026-10-05
- Inputs: `agent-harness-agent-plan-2026-10-05.md` (AH5-01), `docs/migration/source-map.md`

## Context

The Showcase harness is a set of scripts that locate the repository, profiles and trust data from
their own file location and share an upward import (`verification/authority.py` → `harness`).
Verification-v2 launch and completion are deliberately blocked.

## Decision

1. Showcase remains the single implementation owner until AH5-06 cutover; afterwards the library
   is, and Showcase keeps only a pinned thin wrapper.
2. Move in the waves of the source map: leaf modules and resources (03a), explicit `RepoContext`
   and state resolution (03b), authority/planning (04a), one qualified backend (04b), outputs (04c).
   `harness.py` lifecycle CLI is split by separate packets, not moved wholesale.
3. No module is copied with a location-derived root. Explicit context replaces `__file__`/cwd
   inference; profile registration becomes an explicit, controlled input.
4. The library never imports a consumer. Java/Gradle profiles, `spec_inventory`, and the Gradle
   cache knob stay with Showcase as consumer policy.
5. Guards listed under "Diagnostic / incomplete components" move unchanged and keep their tests.

## Consequences

- Each wave needs its own packet, provenance notice (source SHA) and parity tests.
- Fingerprints that include file paths will differ after the move; AH5-03b must define how old
  records are read (explicit compatibility policy) before any state is touched.
- Open: final owner of `control_plane.py`, `design.py`, `wayfinder.py`, `verification_contract.py`,
  `eval.py` (marked undecided in the map).
