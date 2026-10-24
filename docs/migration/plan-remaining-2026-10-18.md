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

5. **Per packet** (owner, 2026-10-18):
   - the source and test map;
   - the explicit behaviour changes;
   - the consumer update instructions.

   One small packet at a time, in the order below. A cutover of already-moved modules never waits for the end of
   the plan.
6. **One implementation of shared rules.** State integrity and lifecycle transition rules have exactly one
   implementation, the library's. Consumer hooks (Showcase's `state_recovery`) are policy. They can add
   checks and supply records, but they cannot skip or replace a library integrity or transition check. One state
   writer per root (ADR 0001 decision 6).
7. **One execution path.** `runner.py` moves onto the library's existing execution and provider adapters:
   `agent_harness.execution` (`launch`, `AgentCliBackend`, the offline backends) and the verification command
   backend and supervisor. It does not keep or add a second launch/cancel/evidence path for the same operations.
8. **After each cutover**, Showcase replaces its frozen copies with the shims, and fixes to shared code go only to
   Harness.

## Order

| Packet | Showcase source | Lines | Depends on | Seams expected | Notes |
|---|---|---|---|---|---|
| AH5-09 | `state_snapshots.py` | 1,177 | stdlib | none | done (0.12.0). `state_recovery.py` stays in Showcase (incident history) |
| AH5-10 | `eval.py` engine | 215 | stdlib | suites dir, repository anchor | done (0.12.0). Suites stay in Showcase |
| AH5-11 | `verify.py` | 136 | moved modules only | profile root and profile id, lifecycle (already ports) | the verification CLI over the executor |
| AH5-12 | `runner.py` | 970 | moved modules, `telemetry`, `trust` | provider/command policy (the 08b ports), Showcase paths (2 sites) | legacy runner and provider adapters; it no longer waits for 04b |
| AH5-13 | `verification_contract.py` | 352 | `runner`, `telemetry`, `trust`, lazy `harness` | lifecycle port | generic verification-contract validation |
| AH5-14a | `harness.py`: document and state I/O | part of 7,314 | `state_snapshots`, `trust` | feature-dir layout (`docs/specs/<id>`), protocol files list | loading and validation of `feature.json`/plan/packets, state files, migrations (ADR 0003) |
| AH5-14b | `harness.py`: lifecycle transitions | part | 14a | consumer hooks | 14b-1 (0.17.0): state access, lock/load, completion readers, integrity checks; 14b-2 step 1 (committed `d7db118`): `lifecycle_checks` owns its exact 13-function inventory; step 2: `feature_transitions` owns its exact 80-function transition closure. Both inventories are enumerated in `docs/specs/AH5-14/packet.md`; the three mutating recovery helpers remain required Showcase bindings until 14d |
| AH5-14c | `harness.py`: verification admission | part | 14b, the verification modules | none new | `admit`, `consume` and `terminalize_verification_execution`, the replan barrier, origin guards (kept blocked) |
| AH5-14d | `harness.py`: recovery and snapshots | part | 14a/b, `state_snapshots` | **recovery hook** (`state_recovery` stays Showcase-owned) | `restore-state`, `discard-prepared-snapshot`, prepared-snapshot recovery |
| AH5-14e | `harness.py`: CLI and `doctor` | rest | 14a–d | the consumer's command set and doctor checks | the library CLI dispatcher, with a thin Showcase entry point |
| AH5-15 | `orchestrate.py` | 845 | `harness` (14), runner | the consumer's worker/provider config | after 14 |
| AH5-16 | `design.py`, `wayfinder.py` | 689 / 1,110 | `harness`, `runner`, `verification_contract` | REPO/layout seams | after 12–14 |
| AH5-17 | final consumer cleanup and migration closure | inventory only in this plan; implementation follows the listed cutovers | AH5-14 through AH5-16 and the separate `generate` package | supported consumer adapters and exact release pins | final inventory across Showcase and Benchmark; no broad cleanup before the corresponding cutover |
| stays | `control_plane.py`, `spec_inventory.py`, `run_test_harness_sharded.py`, `state_recovery.py`, `evals/*.json`, profiles, registries, `retry_controls.py` | — | — | — | consumer policy, intake or CI glue (owners 2026-10-07). `spec_inventory.py` is re-checked when 14a defines the layout seam |

Each `harness.py` part (14a–e) is cut along existing function boundaries, with its tests ported and a rehearsal. A
part that cannot be cut without a behaviour change is split differently; a guard is never dropped to make a cut
possible.

## AH5-17 — final consumer cleanup and migration closure

- **Status:** scope preparation authorized by the owner on 2026-10-24; execution is deferred until the relevant
  cutovers are complete. This packet does not pause AH5-14b-2 or authorize broad cleanup now.
- **Inventory basis:** Showcase `d0679550c478aac01d2bec541d7fee77a05eb1e`; Benchmark
  `cd4e14192bc2e74dec5eb8f9eb7d0c2942ea4ee4`; current migration plan and ownership records. Recheck both trees at
  AH5-17 start because these are moving branches.
- **Rule:** every cutover removes the replaced shared implementation in that same cutover. AH5-17 handles only the
  final cross-consumer audit and leftovers; it is not a reason to keep executable duplicate runtime code around.
- **Classification unit:** the table below is an inventory of paths, not ownership of whole files. At each cutover,
  record callable-level ownership: shared mechanisms move to Harness; project policy, trust data and explicit
  bindings stay with the consumer. Split mixed modules where both responsibilities remain.

| Consumer module or artifact | Classification now | Target stage | Condition for removal or closure |
|---|---|---|---|
| Showcase `tooling/agent-harness/harness.py` | Frozen partial copy; shared state access is in 14b-1, lifecycle transitions remain | AH5-14b-2 step 2, then 14c, 14d and 14e | Replace each moved implementation at its cutover; retain only explicit policy/bindings and the CLI entry point until 14e. Keep the shared `_authority_transaction` binding until all completion publishers use the same supported object. |
| Showcase `runner.py` | Mixed: provider model/profile/permission policy and CLI adapter are consumer bindings; provider-neutral result checks already delegate to Harness; CLI construction/launch remains a shared-runtime candidate | AH5-15 migrates the orchestrator's launch calls; generate/design/wayfinder cut over in their own packets | AH5-15 must not wait for AH5-16 or generate. Keep a compatibility path only for named later-cutover callers; remove each old builder after its last caller migrates and parity/refusal tests pass. Keep provider policy as explicit inputs. |
| Showcase `orchestrate.py` | Mixed: worker scheduling, leases/heartbeats, lifecycle coordination and result aggregation are common runtime; Showcase task/profile policy is consumer input | AH5-15, after AH5-14 | Move common orchestration and its own callers in AH5-15. Do not make AH5-15 depend on design/wayfinder/generate cutovers; remove shared implementation at this cutover and keep only a documented adapter for remaining callers. |
| Showcase `verification_contract.py` | AH5-13 validation is fully delegated; local entry points are repository/layout bindings plus separate authoring (`generate`) | AH5-13 complete; authoring is its own package/cutover | No known local shared-validator copy remains: `input_hashes` and `validate_contract` delegate to `agent_harness.verification_contract`; constants and helpers are aliases. Keep these thin binding wrappers while callers need them. The local `generate`/prompt/CLI and its launch path are not validation leftovers and must migrate as a separate package. |
| Showcase `design.py` and `wayfinder.py` | Mixed: feature/map policy, repository paths, accepted artifacts and CLI surfaces are consumer inputs; generic provider launch, lifecycle and result processing are shared-runtime candidates | AH5-16 after AH5-14 and AH5-15 interfaces are available | These cutovers may depend on AH5-15's published interface, but AH5-15 does not wait for them. Retain only their named legacy callers/builders until their own migration passes; remove each old path after its final caller moves. |
| Showcase `verification_command.py`, `verification/retry_controls.py`, `verification-profiles/showcase.json`, `human-issuer-registry.json`, and compatibility shims | Mixed: Showcase command allowlist, retry-control IDs/digests, provider profiles and issuer trust data are consumer policy/bindings; generic retry evidence validation, safe file reads and supervisor mechanics are shared runtime | Per consuming cutover; audit at AH5-17 | Split callback/helper responsibilities at function level. Keep Showcase control identities, digests, profile and trust policy. Move generic mechanisms only through a supported Harness API; remove private imports after the replacement and binding tests land. |
| Showcase `telemetry.py` and `state_recovery.py` | Mixed: common provenance/usage telemetry is already delegated; signed manual-review evidence and Showcase incident/evidence readers encode consumer trust policy; generic safe I/O/hash helpers require callable-level review | Existing telemetry cutover; recovery mutations remain AH5-14d; final audit AH5-17 | Do not assign whole files by name. Keep issuer/incident formats and recovery policy with Showcase unless separately accepted. Remove any repeated generic mechanism in the cutover that replaces it. Preserve producers/readers needed for historical evidence and old formats. |
| Showcase `control_plane.py`, `spec_inventory.py`, `run_test_harness_sharded.py`, `evals/*.json`, schemas, profiles, docs and registries | Mixed inventory: project intake/CI data and accepted evaluation cases are consumer-owned; schemas and any executable evaluation engine must be assigned by producer, validator and callers, not by directory | Per related tool cutover; final callable/artifact inventory in AH5-17 | For every schema/eval artifact, record its producer, validator, callers and historical readers. Move shared executable validation/engine logic to Harness; retain consumer policy, fixtures, accepted eval cases and compatibility data. Delete no historical evidence or schema reader without an explicit replacement. |
| Benchmark `src/agent_benchmark/{schema,report,compare,pricing,cli,grader,zero_spend,runner,__main__,__init__}.py` | Benchmark-specific schema projection, reporting, grading, pricing, policy and orchestration; not Harness runtime | Remain Benchmark-owned; final audit in AH5-17 | Keep while AB5 behavior uses it. Shared contract/execution rules stay imported from the pinned Harness package, never copied here. |
| Benchmark `src/agent_benchmark/harness_port.py` and `tests/test_harness_port.py` | Necessary adapter and consumer-binding tests. At pinned v0.14.0, README/CHANGELOG declare `qualification` and `qualification.reference.reviewed_reference` supported, matching the adapter's guard; the package `__init__` docstring lists only `contract` and `execution`. v0.17.0 README/ADR likewise declare qualification public while AGENTS and the package docstring omit it. This is an open support-declaration conflict, not evidence that the qualification imports are private. | Remain pinned at v0.14.0; Harness resolves the conflicting declarations without changing the pin or disabling qualification behavior | Keep current qualification controls and historical results. The import guard must match the authoritative versioned support declaration. Reconcile README/CHANGELOG/ADR with AGENTS and package documentation, then recheck the exact-pinned consumer guard. A later pin may be justified by a required fix or removal of a vulnerability in an API Benchmark uses, even if its signature is unchanged; record exact release SHA and rerun consumer integration/pin tests. |
| Benchmark `tasks/*/base/**`, manifests, fixtures, reports and evidence | Frozen benchmark corpus and historical evidence, including snapshots containing Harness source | Remain Benchmark data | Never deduplicate as runtime or edit in place. A changed task snapshot requires a new task version and digest; retain historical results and readers. |
| Both consumers: test suites and docs | Consumer policy, binding, integration, pin and migration evidence | Every cutover; closure evidence in AH5-17 | Shared logic tests live in Harness; consumer tests remain for policy, bindings and end-to-end integration. Keep historical evidence and older-format readers. |

**Separate remaining package — `verification_contract.py` authoring (`generate`):** this is not the AH5-13
validation move and must not disappear between AH5-13 and AH5-16. Inventory its CLI, command builder, runner/telemetry
dependencies, prompts, artifacts and all callers as a distinct package. Assign it a reviewed packet after its
prerequisites are known. Its shared launch path must migrate to Harness even though the authoring policy and contract
content remain consumer inputs. Remove the old command builder only after every caller—including `design.py` and
`wayfinder.py` if they invoke it—uses the replacement and consumer integration tests pass. This package may follow
AH5-15; it does not block AH5-15 completion.

**Closure checks:** no executable copies of shared runtime remain; no consumer imports private Harness APIs; no
parallel legacy launch path remains in migrated tools; each remaining adapter has a documented supported contract and
exact pin; Showcase and Benchmark adoption/integration evidence is current; ownership and migration documentation
match both repositories. All shared behavior tests belong in Harness; consumer policy/binding/integration tests stay
with their consumer. Preserve snapshots, historical results, evidence, schemas and any readers needed for old formats.

## Adapter scope (what Showcase passes in)

The library takes project policy only through these seams. Showcase's shims in `migration/showcase-wrapper/` bind
them at the pinned release, v0.12.0 = `1c569f20aa30a680c025b7a305cf50ccb1735cd0`.

| Seam (library) | Showcase binding | Packet |
|---|---|---|
| `VerificationStore(issuer_registry_path=, profile_root=, executable_binder=, retry_controls=)` | `tooling/agent-harness/human-issuer-registry.json`, `verification-profiles/`, `verification_command.executable_binding_for_command`, `verification/retry_controls.RETRY_CONTROLS` | 06b-0, 08b |
| authority `lifecycle=`, `profile_root=`, `profile_id=`, `executable_binder=` | `harness`, `verification-profiles/`, `showcase`, its binder | 04a-1, 06b-0 |
| sandbox `environment_policy=`, `worktree_wrappers=`, `authority_roots=` | Gradle environment, `./gradlew`, `tooling/agent-harness` | 08a |
| command backend `argv_policy=`, `blocked_executables=`, `sandbox=`; `run_command(argv_policy=, store_factory=, backend_factory=)` | `verification_argv` (allowlist), `PROFILE_SCRIPTS`, its `verification_sandbox` module, its store | 08b |
| executor `lifecycle=`, `resolve_execution=`, `backend_factory=`, `store_factory=` | `harness`, `authority.resolve_execution`, its backend and store | 08b |
| `RetryControl(collect_evidence=, check_run_dir=)` | the Gradle retry evidence and init-script checks | 08b |
| eval `suites_dir=`, `anchor=` | `evals/`, its checkout | 10 |
| still to define | the feature layout (`docs/specs/<id>`), the protocol-file list, the recovery hook (`state_recovery`), the CLI command set, `doctor` checks, runner provider config | 11–16 |

## Showcase cutover, separate from capability

- **AH5-06b (cutover)** can land at any release from 0.9.0 on. It switches the moved modules to the shims and keeps
  every guard. Later releases are re-pins under the same guide.
- **Capability** packets, each with its own qualification and review, never bundled into a move:
  - AH5-04a-2 origin admission;
  - AH5-04b qualified verification backend;
  - AH5-04c source/output path.

### Public-API dependencies of the consumers: proposal (AH5-API-1, separate from the verbatim extraction, 2026-10-24)

The declared supported surface (ADR 0001, amendment 2026-10-18, point 3) is: `agent_harness.contract` and
`agent_harness.execution` as modules; `qualification.qualify`, `probe_digest`, `DEFAULT_IMAGE`, `DEFAULT_TARGET`;
`qualification.reference.reviewed_reference`; the CLI. `tests/test_supported_api.py` pins only a subset of it.

| Consumer | Import (symbol, need) | Within the declared contract? | Proposed supported interface |
|---|---|---|---|
| agent-benchmark | `qualification.qualify`, `qualification.DEFAULT_IMAGE` (grading qualification); `qualification.reference.reviewed_reference` (the shipped reviewed reference) | **yes**, each name is declared | none needed; pin the names it uses in `test_supported_api` |
| agent-benchmark | `contract.validate_request`, `validate_result`, `ContractError`, `ERROR_CODES`, `FIRED_LIMITS`, `LIMIT_EXCEEDED`, `OUTCOMES`; in tests `request_digest`, `CONTRACT_VERSION` (request/result schema and its enumerations) | **yes**, `contract` is declared as a module, but only `validate_request`/`validate_result` are pinned | pin these names in `test_supported_api`, so removing one needs a CHANGELOG-noted release |
| agent-benchmark | `execution.launch`, `ScriptedBackend`, `ProcessBackend`, `AgentCliBackend`, `QualifiedDockerBackend`, `QUALIFIED_LIMITS` | **yes**, all pinned | none |
| Showcase `verification/retry_controls.py` (its Gradle retry-evidence policy) | `verification.retry.RetryControl` (registering a retry control), `verification.serialization.canonical` (canonical JSON for the control's digest), `verification.supervisor.SupervisorError` (signalling a policy violation) | **no**: internal modules | declare exactly these three names as supported (not the modules) in ADR 0001 point 3 and pin them; no code change, no rename |
| Showcase compatibility shims (`verification/*`, `state_snapshots`, `trust`, `telemetry`, `eval`, `harness`, `runner`, `verify`, `verification_contract`, …) | whole internal modules or their private names | no, by design: safe only at the exact pinned SHA (ADR decision 8) | retired by AH5-17 as each consumer calls the library's supported entry points directly; not made public to close a checklist |

Benchmark's import guard (`tests/test_harness_port.py`) checks its own list of import lines; that list is consistent with
the declaration above, but the library's pin test is narrower than the declaration. Proposal: one small change, its own
release on the owner's order, that (1) pins in `test_supported_api` every name either consumer uses from the declared
modules, and (2) declares and pins the three retry-policy names. Benchmark's pin and its qualification checks stay as
they are until then; historical benchmark results are unaffected.
