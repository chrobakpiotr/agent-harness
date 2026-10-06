# ADR 0001 — Extraction ownership and order

- Status: **Accepted** (independent review 2026-10-06: accept with amendments, applied below)
- Date: 2026-10-05; amended 2026-10-06
- Inputs: `agent-harness-agent-plan-2026-10-05.md` (AH5-01), `docs/migration/source-map.md`

## Context

The Showcase harness is a set of scripts that locate the repository, profiles and trust data from
their own file location and share an upward import (`verification/authority.py` → `harness`).
Verification-v2 launch and completion are deliberately blocked.

## Decision

1. **Ownership.** Showcase is the single implementation owner of each **moved** module until the AH5-06
   cutover; afterwards the library is. New behaviour in or around moved modules — origin admission (04a-2),
   backend qualification (04b), source/output path (04c) — is implemented in Showcase by its owner (Showcase
   handoff `docs/reviews/showcase-agent-harness-prerequisites-2026-10-06.md`, `8c7a251`); Harness reviews it
   against the shared contract and ports accepted changes with parity before a release. Ownership of a moved
   module transfers only at the cutover checkpoint. Capability entirely outside moved modules (the offline
   launch API, the constitution CLI) is library-owned from creation. A per-module handover before the cutover
   (recorded 2026-10-06) is superseded by this rule.
   After the cutover ownership stays per module: Showcase keeps `harness.py`, `orchestrate.py`, `runner.py`,
   `verify.py`, executor, supervisor, workspace, sandbox, its profiles and issuer registry until a packet
   moves them; the moved modules become the compatibility shim of `docs/migration/cutover.md`.
2. **Order.** Waves of the source map: leaf modules and resources (03a), explicit `RepoContext` and state
   resolution (03b), authority/planning (04a-1), offline launch API (05a), cutover rehearsal (06a), shared
   contract copies (00b); then one qualified backend (04b, which origin admission 04a-2 needs) and outputs
   (04c) before the real cutover (06b). `harness.py` is split by separate packets, not moved wholesale.
3. **Explicit roots.** No module is copied with a location-derived root. Explicit context replaces
   `__file__`/cwd inference; profile registration becomes an explicit, controlled input.
   Library-first changes to a moved module are allowed only where this redesign creates a seam the source does
   not have; each must fail closed, be listed in `docs/migration/provenance.md`, and be parity-checked on
   Showcase inputs (so far: missing issuer registry → `FAILURE_GRANT_ISSUER_UNAVAILABLE`; profile outside
   `profile_root` refused in `prepare_task_plan`, `f756e42`). Any other behaviour change goes to the owner first.
4. **No consumer imports.** The library never imports a consumer. Java/Gradle profiles, `spec_inventory`, and
   the Gradle cache knob stay with Showcase as consumer policy.
5. **Guards.** Guards listed under "Diagnostic / incomplete components" move unchanged with the module that
   holds them and keep their tests where the test's driver lives (the completion-boundary test drives the
   Showcase lifecycle and stays there). The offline backends (`ScriptedBackend`, `ProcessBackend`;
   `isolation_level: fake/controlled`) never satisfy origin admission or completion and never report a
   qualified launch; only a backend qualified under the 04b packet may, and only through the authority path.
6. **Single writer.** Exactly one implementation writes a given state root (`.agent-state`,
   `.agent-runs/control/verification-v2`). Before the cutover the library writes no Showcase state; consumers
   run either the Showcase scripts or the pinned shim against a repository, never both. The library writes no
   state at import.
7. **Rollback.** Before the cutover Showcase changes only through its own owners' tasks (e.g. `4fd9abe` put
   the library pin into `protocol_files` in advance); nothing in Showcase depends on the library yet. After it, rollback is re-pinning the previous
   library tag or restoring the pre-cutover files (`git checkout <pre-cutover SHA> -- tooling/agent-harness/`),
   after finishing or releasing in-flight tasks (an accepted plan is bound to the exact tree; rehearsed in
   AH5-06a). A state format change needs its own ADR, backup and downgrade path (ADR 0003).
8. **Pins and surface.** Consumers pin an annotated tag's commit SHA, never `main`; no PyPI until AH5-06.
   The public surface is `agent_harness.contract`, `agent_harness.execution` and the CLI. The cutover shim
   relies on internal modules and private names, which is safe only at the exact pinned SHA; Showcase's
   `protocol_files` includes the pin (`requirements.txt`, Showcase `4fd9abe`).
9. **Shared contract.** This repository owns the shared constitution text; consumers keep verbatim copies
   checked with `agent-harness constitution --check` (AH5-00b); Showcase-specific rules stay in Showcase.

## Open

- Owners for `control_plane.py`, `design.py`, `wayfinder.py`, `verification_contract.py`, `eval.py` and
  `verify.py`: they stay in Showcase until a packet moves them (`verify.py` needs the executor).
- 04a-2 waits for an accepted, registered Showcase task under SDD-OBS-001 (the task DAG has none yet).
- 04b waits for a qualification target: the local Docker Desktop Linux guest is a candidate only. Fallback
  (2026-10-06): a GitHub-hosted `ubuntu` runner in Showcase CI (free, `sudo` and Docker available). Each job is a
  fresh VM with a changing image, so a qualification binds only to that job's exact tuple (runner image
  version, kernel, engine, pinned workload image digest, policy) and is re-run with its own evidence per job;
  nothing stays qualified between jobs. Owner of the qualification: Showcase (04b). 04c follows 04a-2 and 04b.

## Consequences

- Each wave needs its own packet, provenance notice (source SHA) and parity tests.
- Path-bearing fingerprints differ after the move; old records are read under ADR 0003.
- `verification/workspace.py` records carry `authority: diagnostic-only`;
  `verification_sandbox.doctor()` always reports `qualified: False`, `launch_ready: False`; Docker discovery
  is not qualification.
