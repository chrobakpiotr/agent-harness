# AH5-06 — Release and controlled cutover

- Status: **accepted**; integration authorised. 06a (rehearsal) **done** (third evaluation: pass);
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
  thin wrapper (later: compatibility shim) over the installed library wheel (Showcase bindings: profile root, issuer registry, lifecycle
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

## Re-evaluation (2026-10-06)

Fresh-context evaluator verdict on `7a9a620`: **fail**. Findings 1, 2, 4 and 6 closed. Corrections to the
section above, and fixes:

- Not closed: `test_verification_completion_boundary` (6 tests) never ran in the rehearsal: its per-module run
  lacked the harness directory on `PYTHONPATH`, so it failed to import on both sides. The "TMPDIR" attribution
  above was wrong for it. Fix: the harness directory is on `PYTHONPATH`; these are the only Showcase tests
  besides the five removed ones that drive the wrapped authority against the real lifecycle.
- Overstated: `test_cutover_wrapper.py` let swapped positional arguments, a dropped `repository`, dropped
  store `control_root`/registry and non-replacing aliases through. Fix: it now asserts the exact arguments of
  every authority entry point, store pass-through, and module identity for every alias; each of those eight
  mutations fails it.
- The CI masking now happens in the script (`ci-masked.diff`); skipped workflow commands are listed
  (`ci-skipped.txt`) instead of dropped silently.
- Not rehearsed: guide steps 3 (removing the five tests) and 4 (`protocol_files`), both Showcase changes in
  06b. The planning benchmark fails on both sides on this host (Showcase code included), as in AH5-04a.

## Evidence 06a, third run (2026-10-06)

Committed scripts, rehearsed SHA `91d4671` (Showcase `HEAD` moved `2b2a428` → `3b63e43` during the run through
other work; `tooling/agent-harness` unchanged): `evidence-rehearsal-91d4671-r3.tar.gz`.

- AC1: base 632 tests (631 ok, 1 FAIL: planning benchmark, host speed); wrapped 636 (629 ok, 6 FAIL,
  1 ERROR). Differences: the 5 authority patch-boundary tests (removed at cutover), R4 process timing under
  parallel load, and the 4 `test_cutover_wrapper` tests (wrapped only, all ok). `completion_boundary`: 6/6 ok
  on both sides.
- AC2: 21 workflow steps (19 non-Python or install/test/report lines listed as skipped); exit codes identical;
  output identical after the script's masking of numbers and run IDs.
- AC3: rollback wrapped → base, base → wrapped, base → base: ok.
- AC5: read-only Git commands only; `tooling/agent-harness` unchanged.

## Third evaluation (2026-10-06)

Fresh-context evaluator verdict on `fdbc017`: **pass** — A (completion boundary runs, 6/6 both sides;
per-module totals equal CI-style `discover`), B (21 shim mutations all caught) and C closed; r3 archive
byte-identical to its run. Low findings fixed in `8f2d067`: the CI mask now hides only durations and eval
result IDs (a changed count stays visible); the rehearsal builds the wheel and copies the shim from a clean
export of the committed `HEAD` and prints its SHA; `test_cutover_wrapper` also pins the shim's defaults
(`unit_id`, `repository`, `reconstruct`). Wording: the "19 skipped" counts workflow shell lines (installs,
the unit-test run, the report gate), not steps.

## Evidence 06a, fourth run (2026-10-06)

Library `8f2d067` (committed `HEAD`), rehearsed Showcase `91d4671` (Showcase `HEAD` moved `5f4c86f` →
`640b618` during the run through other work; `tooling/agent-harness` unchanged):
`evidence-rehearsal-91d4671-r4.tar.gz`.

- AC1: base 632 (631 ok, 1 FAIL: planning benchmark, host speed); wrapped 637 (631 ok, 5 FAIL, 1 ERROR).
  Differences: exactly the 5 authority patch-boundary tests (removed at cutover) and the 5
  `test_cutover_wrapper` tests (wrapped only, all ok). `completion_boundary` 6/6 on both sides.
- AC2: 21 workflow steps; exit codes identical; output identical after masking durations and eval IDs.
- AC3: rollback wrapped → base, base → wrapped, base → base: ok.
- AC5: read-only Git commands only; `tooling/agent-harness` unchanged.

## Update (2026-10-06)

Guide step 4 is done in Showcase: `4fd9abe` adds `requirements.txt` to `harness.protocol_files`, with a test.
ADR 0001 (accepted, amended) now carries the single-writer rule and rollback cited above (decisions 6, 7).

## AH5-06b-0 — Re-sync with Showcase `49ab97f` (2026-10-17)

- Status: **done**. Showcase changed the moved modules during SDD-OBS-001 (T-001–T-009), as it owned them (ADR 0001,
  decision 1). They are brought over before the cutover, and v0.9.0 is the cutover pin.
- Showcase was frozen for the re-sync: `tooling/agent-harness` was unchanged at `49ab97f` throughout.
- Method: a three-way merge per module (base `50c18f9`, ours the library, theirs `49ab97f`). New seams for
  Showcase-only dependencies: `issuer_registry_path` and `executable_binder`, with no process-cwd root. The source
  blobs, seams and ported tests are recorded in `docs/migration/provenance.md`.
- AC1: every changed module carries the `49ab97f` behaviour, apart from the recorded seams. The ported Showcase tests
  pass in the library suite, and each new seam has a test.
- AC2: the cutover rehearsal at `49ab97f` with the committed library and shims. The test sets are identical apart
  from the differences listed with their causes, and CI exit codes are identical apart from the listed one.
- AC3: rollback rehearsal on the same results directory.
- AC4: the cutover guide lists every Showcase test the cutover removes.

### Evidence

Library `027e587`, Showcase `49ab97f` (`HEAD` unchanged during the run). Results, logs, the rollback output, the
wheel check and isolated reruns are in `evidence-rehearsal-49ab97f.tar.gz`.

- **AC1.** `scripts/check-wheel.sh`: 259 tests OK, 2 skipped.
  - The wheel suite includes `test_executables`, the ported `test_telemetry`, `test_verification_authority` and
    `test_verification_store`, and the seam test
    `test_registry_path_is_anchored_to_the_primary_checkout_and_fails_closed`.
  - Each guard mutation in `test_executables` fails exactly its own test.
- **AC2, test counts.** Base 915 tests (906 ok, 3 FAIL, 6 ERROR). Wrapped 933 tests (919 ok, 7 FAIL, 7 ERROR).
  - The wrapped side runs 18 more: 6 in `test_cutover_wrapper` (all ok) and the 13 tests of
    `test_qualification_report`. Those 13 cannot import on the base side, whose venv has no library, and appear
    there as 1 loader error.
- **AC2, ok on base but not on wrapped:**
  - The 5 `AcceptedPlanResolutionTest` tests and `StoreTest.test_primary_repository_authority_resolution`. They patch
    Showcase module names that calls inside the library cannot see. They run under the same names in the library
    suite, and the cutover removes them (`docs/migration/cutover.md`). The CI `eval.py run --suite adversarial` exit 1
    comes from A006, which runs exactly these two modules.
  - 3 tests in `test_qualification_report` assert the pinned 0.5.0, while the rehearsal installs the working tree's
    version. They move with the re-pin.
  - `test_real_plan_acceptance_cannot_be_completed_with_legacy_proofs` errors on both sides when run alone, with
    `SECRET_BEARING_CANDIDATE_UNSEALABLE`. The fixture writes the absolute temp path into an untracked
    `evidence.json`, and on macOS that path exceeds the seal's entropy threshold. The trip depends on the random
    suffix: it passed on the base side in this run. This is a latent Showcase test issue, reported to Showcase.
  - `test_e33_containment_probe_kills_its_execution_unit` and `test_concurrent_sequential_replans_have_one_winner`
    pass 3/3 alone on both sides. They are process timing under parallel load.
- **AC2, failing on base but ok on wrapped.** R5, the 4 `b10` grading probes and
  `test_m4_1_18_concurrent_identical_binding_safe`: the same parallel-load timing.
- **AC2, fixed during the re-sync.** An earlier run at `96ff8f5` showed 12 `test_telemetry` manual-evidence errors
  and the signed-manual lifecycle test. The cause was the stale manual-evidence part of the telemetry shim. It was
  rebuilt verbatim from `49ab97f` (`027e587`), and those tests now match the base side.
- **AC2, CI.** All 21 workflow steps were run. The only exit-code difference is the adversarial suite above.
- **AC3.** `scripts/rehearse-rollback.sh`: wrapped → base, base → wrapped and base → base are all ok.
- **AC4.** `docs/migration/cutover.md` lists the 6 removals and the version-pin move (`9701d87`).
