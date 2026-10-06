# AH5-06 — Release and controlled cutover

- Status: **accepted**; integration authorised. 06a (rehearsal) implemented, awaiting independent evaluation;
  06b (cutover) waits for 04b/04c.
- Source: master plan AH5-06; depends on AH5-05, independent review, integration authorisation. Showcase input:
  `91d4671` (`tooling/agent-harness/` unchanged since `50c18f9`), read-only in 06a.

## Findings (2026-10-06)

- Showcase modules that stay (`executor`, `supervisor`, `workspace`, `verify.py`, `verification_command`,
  `harness`, `orchestrate`, `runner`, `design`, `wayfinder`, `verification_contract`) import the moved ones,
  partly through private names (`store._validate_component`, `store` internals patched by Showcase tests).
- The plan AC "qualified track actually qualified" needs 04b/04c; Showcase `main` carries other active work.
- The cutover pins a released library version; the tag push is authorised once the cutover checks pass.

## AH5-06a — Rehearsal (no change to Showcase)

- Allowed paths: `docs/specs/AH5-06/`, `docs/migration/cutover.md`, `migration/showcase-wrapper/`,
  `scripts/rehearse-cutover.sh`, `scripts/rehearse-rollback.sh`, `CHANGELOG.md`, `AGENTS.md`, `pyproject.toml`.
- Method: export Showcase with `git archive` into a temporary directory, replace each moved module with a
  thin wrapper over the installed library wheel (Showcase bindings: profile root, issuer registry, lifecycle
  = `harness`), and run the same checks on the unmodified export (baseline) and the wrapped one.
- AC1: the Showcase harness test suite gives the same pass/fail set on both; every difference is listed with
  its cause.
- AC2: the Showcase CI protocol commands (`harness.py doctor`, `validate-all`, `verification_contract.py
  validate`, `spec_inventory.py --check`) give the same exit code and output on both.
- AC3: rollback rehearsal: state written by the wrapped export is read by the restored original scripts on
  the same disposable repository (single-writer rule, ADR 0001).
- AC4: `docs/migration/cutover.md` operator guide: install, pin, boundaries, rollback.
- AC5: the Showcase repository is unchanged (`git status` and `HEAD` identical before and after).

## AH5-06b — Cutover (later)

Version bump, annotated tag pushed with `main`, Showcase pins the tag SHA and replaces the moved modules
with the wrapper; plan AC (lifecycle/CLI parity, qualified track qualified, no second implementation owner).
Not drafted until 04b/04c land.

## Evidence 06a, first run (2026-10-06) — superseded by the run after evaluation below

Environment: macOS 24.6.0 (x86_64), CPython 3.13.16, cryptography 48.0.1 on both sides (49.0.0 has no
Intel-macOS wheel), library wheel built from this checkout. Run: `WORK=… scripts/rehearse-cutover.sh
<showcase> 91d4671`, then `scripts/rehearse-rollback.sh <WORK>`; result files and per-module logs in
`evidence-rehearsal-91d4671.tar.gz`.

- AC1: 627 tests per side; base 613 ok / 1 FAIL / 13 ERROR, wrapped 606 ok / 7 FAIL / 14 ERROR. The 14 base
  failures are identical on both sides (pre-existing on this host). The 7 differences:
  - 5 × `test_verification_authority` (`exact_accepted_plan_reconstructs…`, `origin_authority_is_v2…`,
    `post_seal_candidate_mutation…`, `trusted_orchestrator_plan_creation…`, `unknown_execution_unit…`): they
    patch names on the Showcase module (`resolve_accepted`, `publish_and_accept`), which calls inside the
    library do not see. They test a moved module; their library ports pass here, and the cutover removes them
    from Showcase (`docs/migration/cutover.md`).
  - 2 × `test_verification_backend_qualification` (R4, R5): process-timing checks under 8 parallel jobs; each
    passes 3/3 alone on both sides.
  Two earlier rehearsal runs found and fixed: alias wrappers loaded by file path lacked the library's names
  (6 × `test_trust`); test subprocesses ran a `python3` without the library (design/wayfinder loops and the
  cross-worktree lifecycle test), fixed by putting the side's venv first on `PATH`, as Showcase CI installs
  into its `python3`.
- AC2: `harness.py doctor`, `spec_inventory.py --check`, `harness.py validate-all`, `verification_contract.py
  validate` (SDD-001, SDD-OBS-001): exit 0 on both sides, output identical after normalising the export and
  venv paths.
- AC3: rollback rehearsal on a minimal repository (`DEMO-002` T-001): wrapped → base and base → wrapped each
  claim, publish and lifecycle-accept a verification plan, swap the harness files, then read the stored plan
  and continue the lifecycle (`heartbeat`); full reconstruction refuses the in-flight plan after the swap,
  exactly as a one-comment edit with the original scripts does (control: base → base without a swap resolves
  it). Operator rule recorded in the guide: finish or release in-flight tasks before a cutover or rollback.
- AC4: `docs/migration/cutover.md`.
- AC5: the rehearsal runs only `git archive`, `status` and `rev-parse` on Showcase. Other work moved Showcase
  `HEAD` during the runs (`91d4671` → `0eab56f`, plus uncommitted files outside the harness);
  `tooling/agent-harness/` has no status lines and is identical between `91d4671` and the new `HEAD`.
- Found in Showcase, unchanged by the cutover: candidate sealing refuses the full tree (secret-named Helm
  template, high-entropy text, ignored `__pycache__` binaries), so `prepare_task_plan` only works on a minimal
  repository today.

## Independent evaluation (2026-10-06)

Fresh-context evaluator verdict on `0e357c6`: **fail**. Findings and fixes:

- High: the `trust.py` alias wrapper turned the Showcase `trust.py` CLI into a silent no-op (exit 0, no
  output), and the rehearsal ran only 4 of the CI steps, so the "Smoke-test trust and telemetry CLIs" step
  would have passed while checking nothing. Fix: run as a script, the wrapper calls the library CLI; the
  rehearsal now runs every Python step of the Showcase `agentic-sdd` workflow at the rehearsed SHA (21).
- The first evidence archive came from an earlier script than the one committed. Fix: the archive below is
  produced by the committed scripts; the first archive is kept unchanged.
- Removing the five authority tests would leave the Showcase bindings untested. Fix: the wrapper ships
  `tests/test_cutover_wrapper.py` (store registry/profile root, authority lifecycle/profile root/`showcase`,
  module identity, trust CLI); wrong bindings and the old trust wrapper each fail it. The guide names the five
  tests to remove and keeps every other Showcase test.
- `harness.protocol_files` hashes `trust.py`/`telemetry.py`, which become wrappers: the guide makes adding
  `requirements.txt` (the library pin) to the protocol fingerprint a required 06b Showcase change.
- 12 `test_verification_workspace` errors and one completion-boundary error came from macOS `/var` vs
  `/private/var`; the rehearsal now sets a resolved `TMPDIR`. `git status` on Showcase now uses
  `--no-optional-locks`.

## Evidence 06a after evaluation (2026-10-06)

Run with the committed scripts at the rehearsed SHA `91d4671` (Showcase `HEAD` `a6fac3c`, unchanged during the
run): `evidence-rehearsal-91d4671-r2.tar.gz` (summary, results, diffs, per-module logs, rollback output).

- AC1: base 627 tests (624 ok, 2 FAIL, 1 ERROR); wrapped 631 (624 ok, 5 FAIL, 2 ERROR), the 4 extra being
  `test_cutover_wrapper` (all ok). Differences: the 5 authority patch-boundary tests (to be removed, see the
  guide) and `test_R5_parent_exit_with_live_descendant_is_not_drained`, which failed on the base side this
  time (process timing under parallel load; passes alone on both sides). Every other result is identical.
- AC2: 21 workflow steps; exit codes identical; output identical after masking durations and run IDs.
- AC3: `rehearse-rollback.sh`: wrapped → base, base → wrapped and base → base all ok, as in the first run.
- AC5: `tooling/agent-harness` unchanged; only read-only Git commands on Showcase.
