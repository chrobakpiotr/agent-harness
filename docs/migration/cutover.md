# Showcase cutover guide (AH5-06)

For Showcase operators. Here "shim" means the Python compatibility modules below; it is unrelated to the
S30 DR restore wrapper (inhibit, issuer acknowledgements, audited RESUME), which this cutover does not touch.

Status: the cutover is **rehearsed** (AH5-06a); the real switch (AH5-06b) waits
for the qualified backend (AH5-04b/04c). Until then Showcase stays the implementation owner (ADR 0001).

## What changes

The modules already in the library stop being implemented in Showcase. Each Showcase file is replaced by
the compatibility shim in [`migration/showcase-wrapper/`](../../migration/showcase-wrapper/):

| Showcase file | After the cutover |
|---|---|
| `trust.py`, `machine_outcomes.py`, `verification/{model,serialization,profile,fingerprint,planner,candidate,human_grants,workspace,supervisor}.py` | alias of the library module (same objects, so `isinstance` and test patches keep working) |
| `verification/store.py` | library store; binds `human-issuer-registry.json`, `verification-profiles/` and the registered retry controls |
| `verification/retry_controls.py` (new) | Showcase's retry-control registry (the critical PostgreSQL and RabbitMQ Gradle gates), formerly in `verification/store.py` and `verification/supervisor.py` (AH5-08b) |
| `verification/executor.py` | library executor; binds lifecycle `harness`, the authority resolver, Showcase's store and command backend (AH5-08b) |
| `verification_command.py` | Showcase's allowlist, profile scripts and executable binding, over the library command backend and `run_command` (AH5-08b) |
| `verification/authority.py` | library authority; binds lifecycle `harness`, `verification-profiles/` and profile `showcase` |
| `telemetry.py` | library telemetry plus the Showcase-only manual evidence and CLI |
| `verification_sandbox.py` | library sandbox and executable resolver; binds the Gradle environment, the `./gradlew` worktree wrapper and the `tooling/agent-harness` authority root (AH5-08a) |

Everything else (`harness.py`, `orchestrate.py`, `runner.py`, `verify.py`, profiles, issuer registry, state under `.agent-state` and `.agent-runs/control`) stays in Showcase.
Origin admission and completion stay blocked; the qualified track stays NOT_QUALIFIED.

## Install and pin

1. In `tooling/agent-harness/requirements.txt` add the pinned library next to `cryptography`:
   `agent-harness[grants] @ git+https://github.com/chrobakpiotr/agent-harness.git@<tag SHA>` (a tag SHA,
   never a branch).
2. Copy the wrapper files (including `tests/test_cutover_wrapper.py`) over `tooling/agent-harness/`.
3. In the same change, remove only the Showcase tests below. They patch Showcase names that calls inside
   the library cannot see. Each runs under the same name in the library's normal suite
   (`agent-harness/tests/test_verification_authority.py` and `test_verification_store.py`, run by
   `scripts/check-wheel.sh` and CI), and `test_cutover_wrapper.py` covers what Showcase passes in. Every other
   Showcase test stays.

   | Showcase `tooling/agent-harness/tests/test_verification_authority.py::AcceptedPlanResolutionTest` | Library assertions |
   |---|---|
   | `test_exact_accepted_plan_reconstructs_current_units_without_running_them` | identical (1) |
   | `test_origin_authority_is_v2_and_resolution_is_read_only` | identical (3) |
   | `test_post_seal_candidate_mutation_rejects_before_execution` | identical (1) |
   | `test_trusted_orchestrator_plan_creation_binds_running_attempt_and_candidate` | identical (6); the accept call additionally asserts `lifecycle=` and `profile_root=` |
   | `test_unknown_execution_unit_rejects_before_execution` | identical (1) |

   | Showcase `tooling/agent-harness/tests/test_verification_store.py::StoreTest` | Library assertions |
   |---|---|
   | `test_primary_repository_authority_resolution` | identical (5); patches `agent_harness.repository._worktrees`, and the store receives the registry as `issuer_registry_path` |

   AH5-08a (sandbox): these test the moved sandbox through its internals and run under the same names in the
   library's `test_sandbox.py`, `test_sandbox_doctor.py`, `test_backend_qualification.py` and
   `test_executables.py`. Their other tests stay and run through the shim, including the Gradle cache and
   `R14` (which drives `verification_command`).

   | Showcase test module | Tests to remove |
   |---|---|
   | `test_verification_sandbox_doctor.py` | its four tests; keep the module file, which the adversarial eval A007 runs by path and `test_eval.py` pins |
   | `test_verification_sandbox.py` | `test_auto_degrades_explicitly_when_platform_has_no_strong_backend`, `test_backend_discovery_uses_the_shared_resolver`, `test_bubblewrap_hides_verification_control_store_contents`, `test_bubblewrap_rebinds_trusted_issuer_registry_read_only`, `test_codex_sandbox_helper_uses_documented_command_shape`, `test_control_store_read_probe_fails_when_any_process_depth_can_read`, `test_required_fails_when_no_backend_exists`, `test_unsupported_backend_fails_closed_for_every_mode` |
   | `test_verification_backend_qualification.py` | `test_Q01_permitted_write_control_is_a_positive_probe`, `test_Q08_Q09_escape_evidence_rejects_otherwise_passing_candidate`, `test_R1_Q02_direct_write_bypass_is_detected_and_marker_cleaned`, `test_R1_R16_active_runner_returns_structured_evidence_for_all_checks`, `test_R2_Q03_child_write_bypass_is_detected_and_marker_cleaned`, `test_R3_Q04_grandchild_write_bypass_is_detected_and_marker_cleaned`, `test_R4_new_group_and_session_escapes_reject_process_group_backend`, `test_R5_parent_exit_with_live_descendant_is_not_drained`, `test_discovery_does_not_qualify_a_candidate`, `test_e33_containment_probe_kills_its_execution_unit`, `test_qualify_all_pass_is_all_or_nothing` |
   | `test_verification_t900_resolver_guards.py` | `test_executable_nested_under_excluded_worktree_is_refused` |

   Three Showcase tests are adapted instead of removed. Each patches a name that a library function looks up
   in its own module, so the patch target becomes the library module. Two are in `verification_command`'s tests.
   Change only the patch target, in
   `test_verification_command.py::test_resolved_plan_binding_is_forwarded_and_swap_fails_before_payload` (the
   patch around `VerificationSupervisor(store).execute(...)`, line 98 at `49ab97f`; leave the line-67 patch around
   `executable_binding_for_command` unchanged; line 162 in the second test) and in `::test_docker_discovery_alone_cannot_launch_in_auto_or_required_mode`:
   `mock.patch.object(command.verification_sandbox, 'resolve_executable', …)` becomes
   `mock.patch('agent_harness.sandbox.resolve_executable', …)`. With that change, all 11 tests pass through the shim.
   The third is in `test_verification_noncacheable.py::test_terminal_publication_crash_recovers_pass_without_relaunch`
   (AH5-08b): `mock.patch('verification.executor.VerificationSupervisor', …)` becomes
   `mock.patch('agent_harness.verification.executor.VerificationSupervisor', …)`. With that change, all 7 tests
   pass through the shims.

   The re-pin also moves the version that `test_qualification_report.py` asserts (`0.5.0` today) to the new tag;
   the rehearsal installs the working tree's version, so those three tests fail there by design.
4. Done in Showcase (`4fd9abe`, with a test): `tooling/agent-harness/requirements.txt` is in `harness.protocol_files`: after the cutover `trust.py` and
   `telemetry.py` are wrappers, so the library pin must be part of the protocol fingerprint (a re-pin changes
   the trust classifier).
5. Run every step of the `agentic-sdd` workflow and the harness test suite.

## Boundaries

- Finish or release in-flight tasks first. An accepted verification plan is bound to the exact candidate tree;
  swapping the harness files (cutover or rollback) changes it, so full reconstruction refuses in-flight plans
  (`invalid-verification-plan`), exactly as any other code edit would. Stored records and lifecycle state stay
  readable and the lifecycle continues (`heartbeat`, `release`, `replan-task`).
- One writer per state root: never run the pre-cutover scripts and the wrapped ones against the same
  repository at the same time. The library writes no state at import.
- Changes to moved modules go to the library first, then Showcase re-pins.

## Rollback

Re-pin the previous library tag, or restore the pre-cutover files from Git
(`git checkout <pre-cutover SHA> -- tooling/agent-harness/`). The state format does not change, so state
written after the cutover is read by the restored scripts; `scripts/rehearse-cutover.sh` checks the code
paths, and the rollback rehearsal is recorded in `docs/specs/AH5-06/packet.md`.

## Known before the cutover (unchanged by it)

Candidate sealing (`prepare_task_plan`) refuses the full Showcase tree: it walks ignored files too, so a
secret-named path (`infra/k8s/helm/ecommerce/templates/secret.yaml`), high-entropy text, or binary `__pycache__`
files stop it (`SECRET_BEARING_CANDIDATE_UNSEALABLE`, `CANDIDATE_PRIVACY_PREFLIGHT_UNAVAILABLE`). Origin
admission is blocked anyway; the rehearsal uses a minimal repository with `PYTHONDONTWRITEBYTECODE=1`.
Showcase confirms this is intended fail-closed policy: clean generated caches before sealing, and any exception
for the templated Helm file needs its own narrow review and test, never a weaker general rule.

## Rehearse

`scripts/rehearse-cutover.sh <showcase checkout> [<sha>]` exports Showcase twice, applies the wrapper to one
copy, runs the harness tests and every Python step of the `agentic-sdd` workflow on both, and diffs them. It
only reads Showcase (`git archive`, `git --no-optional-locks status`, `rev-parse`).
`scripts/rehearse-rollback.sh <its WORK dir>` then writes lifecycle state and an accepted plan with one side and
reads and continues it with the other, in both directions.
