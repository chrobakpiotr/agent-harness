# Remaining extraction plan (2026-10-18)

- Status: **accepted** plan, ordered by the owner 2026-10-18: extract the whole shared harness, in small parts.
  Each step is still its own packet with its own acceptance.
- Inputs:
  - ADR 0001 with its amendment of 2026-10-18 (frozen copies; the cutover is independent of qualification;
    supported API);
  - `source-map.md` and `ownership-proposal-2026-10-07.md`;
  - Showcase `49ab97f`.

## Rules for every packet

1. **Small and verbatim.** Copy the source verbatim at a pinned Showcase SHA. The only changes are explicit,
   fail-closed seams, each listed in `provenance.md` with a test and a mutation check. Project policy
   (Gradle/Java, Showcase paths, profiles, registries, suites, incident records) never enters the library. It is
   passed in through parameters, adapters or a configuration object, and the Showcase shim binds it.
2. **Guards are moved, never removed.** Origin admission and completion stay blocked, NOT_QUALIFIED stays, and
   `doctor()` stays unqualified. Unblocking is a separate capability packet, below.
3. **Freeze, then move.** A freeze notice to Showcase names the paths. From then on the library owns them and
   Showcase's files are frozen copies (ADR 0001 amendment).
4. **Evidence.**
   - ported tests in the library suite;
   - a cutover rehearsal against Showcase, with every difference explained and the post-removal CI evals passing;
   - rollback;
   - a fresh-context evaluation;
   - release on the owner's order.

## Order

| Packet | Showcase source | Lines | Depends on | Seams expected | Notes |
|---|---|---|---|---|---|
| AH5-09 | `state_snapshots.py` | 1,177 | stdlib | none | done (0.12.0). `state_recovery.py` stays in Showcase (incident history) |
| AH5-10 | `eval.py` engine | 215 | stdlib | suites dir, repository anchor | done (0.12.0). Suites stay in Showcase |
| AH5-11 | `verify.py` | 136 | moved modules only | profile root and profile id, lifecycle (already ports) | the verification CLI over the executor |
| AH5-12 | `runner.py` | 970 | moved modules, `telemetry`, `trust` | provider/command policy (the 08b ports), Showcase paths (2 sites) | legacy runner and provider adapters; it no longer waits for 04b |
| AH5-13 | `verification_contract.py` | 352 | `runner`, `telemetry`, `trust`, lazy `harness` | lifecycle port | generic verification-contract validation |
| AH5-14a | `harness.py`: document and state I/O | part of 7,314 | `state_snapshots`, `trust` | feature-dir layout (`docs/specs/<id>`), protocol files list | loading and validation of `feature.json`/plan/packets, state files, migrations (ADR 0003) |
| AH5-14b | `harness.py`: lifecycle transitions | part | 14a | consumer hooks | claim, heartbeat, release, complete, repair, replan, reconcile; the CAS and generation guards |
| AH5-14c | `harness.py`: verification admission | part | 14b, the verification modules | none new | `admit`, `consume` and `terminalize_verification_execution`, the replan barrier, origin guards (kept blocked) |
| AH5-14d | `harness.py`: recovery and snapshots | part | 14a/b, `state_snapshots` | **recovery hook** (`state_recovery` stays Showcase-owned) | `restore-state`, `discard-prepared-snapshot`, prepared-snapshot recovery |
| AH5-14e | `harness.py`: CLI and `doctor` | rest | 14a–d | the consumer's command set and doctor checks | the library CLI dispatcher, with a thin Showcase entry point |
| AH5-15 | `orchestrate.py` | 845 | `harness` (14), runner | the consumer's worker/provider config | after 14 |
| AH5-16 | `design.py`, `wayfinder.py` | 689 / 1,110 | `harness`, `runner`, `verification_contract` | REPO/layout seams | after 12–14 |
| stays | `control_plane.py`, `spec_inventory.py`, `run_test_harness_sharded.py`, `state_recovery.py`, `evals/*.json`, profiles, registries, `retry_controls.py` | — | — | — | consumer policy, intake or CI glue (owners 2026-10-07). `spec_inventory.py` is re-checked when 14a defines the layout seam |

Each `harness.py` part (14a–e) is cut along existing function boundaries, with its tests ported and a rehearsal. A
part that cannot be cut without a behaviour change is split differently; a guard is never dropped to make a cut
possible.

## Showcase cutover, separate from capability

- **AH5-06b (cutover)** can land at any release from 0.9.0 on. It switches the moved modules to the shims and keeps
  every guard. Later releases are re-pins under the same guide.
- **Capability** packets, each with its own qualification and review, never bundled into a move:
  - AH5-04a-2 origin admission;
  - AH5-04b qualified verification backend;
  - AH5-04c source/output path.
