# Extraction source map (AH5-01)

- Source: Showcase `tooling/agent-harness/` at `50c18f947031f1b7bd8e8c6276b2a98b9b46ab98`
  (checkout HEAD matched this SHA, clean tree, 2026-10-05). Read-only survey; nothing copied yet.
- Ownership rule: until cutover (AH5-06) Showcase is the only implementation owner of every module
  below. After cutover the "Owner after cutover" column becomes the single owner; the old copy is
  replaced by a thin wrapper, never kept as a second active implementation.
- Status of this map: **proposed** (see `docs/adr/0001-extraction-ownership.md`). Owner columns are
  proposals derived from the master plan's scope split, not accepted policy.

## Execution modules (15,525 lines total)

`int:` lists harness-internal imports only; everything else is stdlib unless noted.
Wave = AH5 task that may first move the module.

| Module | Lines | int: imports | Coupling to Showcase / state / env | Tests | Owner after cutover | Wave |
|---|---|---|---|---|---|---|
| `machine_outcomes.py` | 44 | — | none | `test_machine_outcomes` | library | 03a |
| `trust.py` | 90 | — | path patterns name `.agent-state`, `.agent-runs` | `test_trust` | library | 03a |
| `verification/model.py` | 176 | — | none | indirect | library | 03a |
| `verification/serialization.py` | 246 | `.model`, `trust` (top-level) | none | indirect | library | 03a |
| `telemetry.py` | 425 | — | writes `<repo>/.agent-runs/...`; `--repo` defaults to cwd; `__file__` for harness path in provenance; provider-exposed cost only (no price table) | `test_telemetry` | library | 03a |
| `schemas/*.json` (9) | — | — | read by path relative to the harness dir | via validators | library (resources in wheel) | 03a |
| `harness.py` state helpers: `repo_root`, `git_common_dir`, `state_key`, `runtime_state_dir`, `feature_repo_base` | part of 5,307 | — | `git rev-parse --git-common-dir`; state at `<common>/../.agent-state`; falls back to **cwd** when feature dir is not `docs/specs/*` | `test_harness` | library (`RepoContext`) | 03b |
| `verification/store.py` `resolve_control_root`, lock | 1,217 | `.model`, `.serialization` | state at `<primary worktree>/.agent-runs/control/verification-v2`; mkdir lock, no stale auto-recovery; issuer registry via `__file__` | `test_verification_store` | library | 03b |
| `verification/profile.py`, `fingerprint.py`, `planner.py` | 194/207/305 | `.model`, `.profile`, `.serialization`, `.fingerprint` | fingerprint uses Git common dir | `test_verification_profile*`, `_planner` via `verify` | library | 04a |
| `verification/authority.py` | 273 | `.serialization` + **`import harness` (upward)** | profile root via `__file__` (`verification-profiles/`), `showcase.json` hard-coded in `prepare_task_plan`; feature path `docs/specs/<id>` | `test_verification_authority`, `_completion_boundary` | library, redesigned (see ADR) | 04a |
| `verification/candidate.py` | 348 | `.serialization` | Git common dir; process (`git`) | `test_verification_candidate` | library | 04a |
| `verification_sandbox.py` | 1,105 | — | backends: codex-sandbox, bubblewrap, macos-sandbox-exec, docker; `ctypes`, `signal`; env `HOME`/`PATH`/`TMPDIR`, `AGENTIC_SDD_GRADLE_RO_DEP_CACHE` (Gradle = Showcase-specific); roots include `docs/specs` | `test_verification_sandbox*`, `_backend_qualification` | library (Gradle cache knob → consumer policy) | 04b |
| `verification_command.py` | 297 | `verification_sandbox` | process launch, monotonic clock | `test_verification_command`, `_script_commands` | library | 04b |
| `verification/supervisor.py`, `executor.py` | 532/372 | `.store`, `.planner`, `.model`, `.serialization`, `machine_outcomes`, `verification_command` | per-start identity, terminal/drain records | `test_verification_supervisor`, `_executor`, `_noncacheable`, `_phase_d` | library | 04b |
| `verification/workspace.py` | 220 | `.serialization` | **diagnostic-only** records | `test_verification_workspace` | library | 04c |
| `verification/human_grants.py` | 73 | `.serialization` | issuer registry JSON | `human_grant_fixture` | library | 04a |
| `verify.py` | 128 | `verification.*`, `machine_outcomes` | profile root via `__file__` | `test_verify` | library CLI | 04a |
| `runner.py` | 670 | `machine_outcomes`, `telemetry`, `trust`, `verification_sandbox` | provider CLIs (codex/claude); `REPO = __file__.parents[1]`; `.agent-runs` | `test_runner` | library (provider adapters) | not before 04b; packet needed |
| `harness.py` (lifecycle CLI, rest) | 5,307 | `trust`, `verification_contract` | `docs/specs/<feature>`; `protocol_files` hard-codes `tooling/agent-harness/*`, `.claude/agents`, `.github/workflows/agentic-sdd.yml`; `fcntl` | `test_harness`, `test_replan`, `test_packet_identity_bridge` | library, split by packet | not scheduled; too large for one move |
| `orchestrate.py` | 529 | `harness`, `machine_outcomes`, `telemetry`, `verification.authority` | `REPO = __file__.parents[1]`; threads | `test_orchestrate` | library | after `harness.py` split |
| `design.py`, `wayfinder.py`, `verification_contract.py` | 689/1,110/352 | `harness`, `runner`, `telemetry`, `trust`, `design` | `REPO = __file__.parents[1]`; `.agent-runs/design|verification-contract`; `wayfinder` uses `fcntl` (POSIX) | `test_design`, `test_wayfinder`, `test_verification_contract` | library (SDD protocol tooling) | undecided |
| `eval.py`, `evals/*.json` | 215 | — | `.agent-runs/evals`; `git --show-toplevel` | `test_eval` | library (harness behavioural evals, constitution #26) | undecided |
| `control_plane.py` | 247 | — | **network** (GitHub/Jira, read-only intake); `.agent-state/control-plane` | `test_control_plane` | undecided (intake, not execution) | not in MVP |
| `spec_inventory.py` | 149 | — | `docs/specs` inventory | `test_spec_inventory` | Showcase (project docs) | stays |
| `verification-profiles/showcase.json` | — | — | Java/Gradle gates | `test_verification_profile_showcase` | **Showcase** (consumer policy) | stays |
| `benchmarks/verification_planning.py` + baseline | — | `unittest.mock` of planner | harness performance regression | `test_verification_benchmark` | library (regression benchmark, not agent-benchmark corpus) | with 04a |
| `human-issuer-registry.json` | — | — | trust root data | — | consumer-provided (state/trust, not package data) | 04a |
| `requirements.txt` (`cryptography==49.0.0`) | — | — | only third-party dependency | — | library dependency when its user moves | with its user |

Out of scope for extraction (plan): UI, AMQP poison gate, Redis gate, Order, any `.agent-state` /
`.agent-runs` contents, receipts, logs, secrets.

## Cross-cutting dependencies to redesign, not copy

- **Location-derived roots.** 8 modules set `REPO = HERE.parents[1]` or resolve profiles/registry via
  `__file__`. In a wheel `__file__` is inside `site-packages`, so these become wrong silently.
  Replace with an explicit `RepoContext` (workspace, repo ID, base SHA, authority root, evidence root).
- **Profile discovery.** `authority.py` and `verify.py` trust profiles only under the harness dir;
  `prepare_task_plan` hard-codes `showcase`. Needs controlled registration of consumer profiles
  where the trust boundary is explicit (AH5-04a), not a search path.
- **Upward import.** `verification/authority.py` imports `harness` (lifecycle CLI) lazily. The library
  package must have no import cycle; the needed functions (`load_validated`, `load_state`, plan
  acceptance) move behind the lifecycle module boundary first.
- **cwd fallback.** `feature_repo_base` falls back to `Path.cwd()`; `telemetry --repo` defaults to cwd.
  Library entry points take explicit context; only the CLI may default from cwd.
- **Git.** Process dependency on `git`: `--git-common-dir` (state identity, 5 call sites),
  `--show-toplevel` (4), worktree list (store). Linked worktrees share state via the common dir.
- **Clock/process.** `time.time`/`monotonic`, `uuid4`, `subprocess`, `signal`, `ctypes`, `fcntl`
  (POSIX-only). Need injectable clock/ID seams only where tests already need them.
- **Public CLI today.** `harness.py` 31 subcommands (validate … doctor), `verify.py` plan/run/grant-import,
  `wayfinder.py` 9, `verification_contract.py` 3, `eval.py` 3, `telemetry.py` 2, `control_plane.py` 4,
  `trust.py` 2. All are script-path invocations (`python3 tooling/agent-harness/<x>.py`).
- **Formats.** JSON state/records with `schema_version`; 9 JSON schemas; hash-bound fingerprints
  include file *paths* (`protocol_files`), so moving files changes fingerprints → compatibility
  policy needed (AH5-03b), never a silent migration.

## Diagnostic / incomplete components (must stay so after the move)

- `resolve_execution` (`authority.py:135`) and `execute_plan(authority_context=…)` (`executor.py:127`)
  raise `VERIFICATION_ORIGIN_ADMISSION_UNAVAILABLE` unconditionally.
- v2 completion is blocked against legacy proofs (`test_verification_completion_boundary`).
- `verification/workspace.py` records carry `authority: diagnostic-only`.
- `verification_sandbox.doctor()` always reports `qualified: False`, `launch_ready: False`;
  Docker discovery is not qualification.

## Single-writer state rule

At any time exactly one implementation writes a given state root (`.agent-state`,
`.agent-runs/control/verification-v2`). During migration the library must not write Showcase state;
consumers run either the Showcase scripts or the pinned library wrapper, never both against the
same repository. The library never writes state at import time.

## Rollback

Before cutover nothing changes in Showcase, so rollback = stop using the library. After cutover
(AH5-06) the Showcase wrapper pins a library version/digest; rollback = re-pin the previous version
or restore the pre-cutover scripts from Git history, tested on disposable state. A state format
change needs its own ADR, backup and downgrade path.
