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

## Evidence 06a (2026-10-06)

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
