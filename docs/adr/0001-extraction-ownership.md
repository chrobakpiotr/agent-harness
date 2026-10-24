# ADR 0001 — Extraction ownership and order

- Status: **Accepted** (independent review 2026-10-06: accept with amendments, applied below); **amended 2026-10-18** (frozen-copy ownership, cutover independent of qualification, supported API; see the end)
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

- Owners decided 2026-10-07 (`docs/migration/ownership-proposal-2026-10-07.md`): library later for the `eval.py`
  engine, `verification_contract.py`, `design.py`, `wayfinder.py`; Showcase keeps eval suites and `control_plane.py`.
  All stay in Showcase until a packet moves them; `verify.py` needs the executor.
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

## Amendment 2026-10-18 — frozen copies, cutover independent of qualification, supported API

Ordered by the owner 2026-10-18. Context: Showcase's v0.9.0 cutover had not yet landed; AH5-08 had moved the
verification runtime and AH5-09/10 the state snapshots and the eval engine.
This amendment replaces decision 1, the cutover dependency in decision 2, and the public surface in decision 8.
Where the earlier text conflicts, this section applies.

1. **One ownership rule: frozen copies.**
   - A module has exactly one implementation owner at any time.
   - **Before its packet**, the owner is Showcase.
   - **From the freeze notice** of the packet that moves it, the owner is the library:
     - Showcase's file becomes a **frozen copy**. Nobody edits it except to replace it with the shim or re-pin
       at cutover.
     - A change in or around the module goes to the library first, then Showcase re-pins.
     - A Showcase change made before the freeze is ported with a three-way merge against the copy point, as
       AH5-06b-0 did for `49ab97f`.
   - **At Showcase's cutover**, the frozen copy is replaced by the compatibility shim.

   This replaces "Showcase owns moved modules until the cutover" and the per-module handover of decision 1. Decision 3
   (library-first seams) still applies; with this rule it covers every change to a moved module.

   | Library module | Showcase path(s), frozen copy | Freeze notice | First release |
   |---|---|---|---|
   | `trust`, `machine_outcomes`, `telemetry` (provenance/usage part) | `trust.py`, `machine_outcomes.py`, `telemetry.py` | 2026-10-17 (AH5-06b-0) | 0.2.0; re-synced 0.9.0 |
   | `verification.{model,serialization,profile,fingerprint,planner,candidate,human_grants,store,authority}`, profile schema | `verification/{same}.py`, `schemas/verification-profile.schema.json` | 2026-10-17 (AH5-06b-0) | 0.2.0; re-synced 0.9.0 |
   | `executables` | `verification_sandbox.py` lines 31–174 | 2026-10-17 | 0.9.0 |
   | `sandbox`, `verification.workspace` | `verification_sandbox.py`, `verification/workspace.py` | 2026-10-18 (AH5-08) | 0.10.0 |
   | `verification.{supervisor,command,executor,retry}` | `verification/supervisor.py`, `verification_command.py` (generic part), `verification/executor.py` | 2026-10-18 (AH5-08) | 0.11.0 |
   | `state_snapshots` | `state_snapshots.py` | AH5-09 freeze notice | 0.12.0 |
   | `eval_runner` | `eval.py` (engine; `evals/*.json` stay Showcase-owned) | AH5-10 freeze notice | 0.12.0 |
   | `verify_cli` | `verify.py` | AH5-11 freeze notice | 0.13.0 |
   | `task_runner` | `runner.py`, **partial**: the provider-neutral checks; the launch stays Showcase-owned until AH5-12b | AH5-12a freeze notice | 0.13.0 |
   | `execution.AgentCliBackend` | `runner.py`: the agent launch (AH5-12b). The Showcase policy (models, profiles, prompts, provenance) stays in its shim | AH5-12 freeze notice | 0.14.0 |
   | `verification_contract` | `verification_contract.py`, **partial**: the validation; authoring (`generate`) stays Showcase-owned | AH5-13 freeze notice | 0.15.0 |
   | `feature`, `feature_state` | `harness.py`, **partial** (AH5-14a): the feature documents and validation, the state paths, identity checks and the single writer; state loading, lifecycle, admission, recovery and the CLI stay until 14b–e | AH5-14a freeze notice | 0.16.0 |
   | `feature_access` | `harness.py`, **partial** (AH5-14b-1): the lifecycle lock, state loading, the completion-record readers and the integrity checks they need; Showcase's incident and recovery-epoch check stays as a refuse-only hook; transitions (14b-2), admission (14c), recovery operations (14d) and the CLI (14e) stay | AH5-14b-1 freeze notice | 0.17.0 |
   | `lifecycle_checks` | `harness.py`, **partial** (AH5-14b-2 step 1): exact 13-function inventory in `docs/specs/AH5-14/packet.md`; read-only resume/plan/claim/recovery-identity checks and helpers | AH5-14b-2 step 1 | pending 14b-2 cutover |
   | `feature_transitions` | `harness.py`, **partial** (AH5-14b-2 step 2): exact 80-function transition closure in `docs/specs/AH5-14/packet.md`; three mutating recovery helpers remain required Showcase bindings until 14d. Count: the earlier estimate of 98 = 13 (step 1) + 80 (step 2) + 5 not moved in 14b-2: the three Showcase bindings `task_workspace_snapshot`, `restore_task_workspace`, `restore_attempt_authorization` and the stale-lease recovery `recover_stale_leases`, `cmd_recover_stale` (14d) | AH5-14b-2 step 2 | pending 14b-2 cutover |

   The table is kept current in `docs/migration/cutover.md`. Showcase policy that the shims bind stays
   Showcase-owned and is never frozen:
   - the Gradle environment, the `./gradlew` wrapper and authority roots;
   - the command allowlist and profile scripts;
   - the retry-control registry (`verification/retry_controls.py`);
   - profiles, the issuer registry and eval suites;
   - the `telemetry` manual-evidence part;
   - `state_recovery.py`.
2. **The cutover does not wait for backend qualification.** The cutover (AH5-06b) switches Showcase to the library
   with every guard kept:
   - origin admission and completion stay blocked;
   - the qualified track stays NOT_QUALIFIED;
   - `doctor()` reports `qualified: False`;
   - the offline backends never satisfy origin admission.

   It needs only the rehearsal evidence in `docs/specs/AH5-06/packet.md` and the cutover guide. Unblocking execution
   is a separate capability, in later packets, each with its own qualification and review: 04a-2 origin
   admission, 04b qualified verification backend, 04c source/output path. This replaces the order "04b/04c before
   06b" in decision 2 and in the ownership proposal's "runner after 04b". No guard is removed or weakened by a
   move.
3. **Supported API.** The public, versioned surface is:
   - `agent_harness.contract`;
   - `agent_harness.execution`, including `QualifiedDockerBackend`, `QUALIFIED_LIMITS` and `AgentCliBackend`;
   - `agent_harness.qualification`: `qualify`, `probe_digest`, `DEFAULT_IMAGE`, `DEFAULT_TARGET`;
   - `agent_harness.qualification.reference.reviewed_reference`;
   - `agent_harness.retry_control.RetryControl`, the library's own type (added 2026-10-24, AH5-API-1);
   - the `agent-harness` CLI.

   These change only with a CHANGELOG entry, and incompatibly only in a minor release that says so. Every other
   module is internal and may change in any release: `verification.*`, `sandbox`, `executables`,
   `state_snapshots`, `eval_runner`, `telemetry`, `trust`, `repository`, and `qualification`'s probe modules.
   The Showcase shims rely on internal modules and private names, which is safe only at the exact pinned SHA
   (decision 8, unchanged).
