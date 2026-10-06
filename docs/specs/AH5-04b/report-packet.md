# AH5-04b-r — Qualification report document and check command

- Status: **accepted** and **done** (final check 2026-10-06: pass); released in 0.3.0. Harness owns the format and validator; Showcase owns the probes,
  the qualification run and its raw evidence (ADR 0001).
- Source: Showcase handoff (`9ffb1b4`): every Q01–Q16 and B1–B10 check must be recorded separately with its own
  result and evidence, bound to one exact target and policy digest; contract v1 `CapabilityReport` has no
  per-check fields. Grading checks: `grading-requirements.md`.

## Scope

- `contract.validate_qualification_report(doc)`: one report per exact target tuple: `target`, `policy_digest`,
  `tuple` (host/runner image, kernel, engine, workload image digest…), `checks` (one per mandatory check id:
  `pass`/`fail`/`not-run`, evidence references `{path, sha256, size}`), `independent_review` and `created_at`.
  Mandatory checks: Q01–Q16 and B1–B10 (`contract.QUALIFICATION_CHECKS`); missing, duplicate or unknown ids
  are invalid. `qualification_passes(doc)`: every check `pass` with evidence and the review `pass`.
- `contract.validate_capability_binding(report, qualification)`: same target and policy digest; `qualified`
  (and so `launch_ready`) only when the qualification passes.
- `contract.qualification_digest(doc)`: sha256 of the canonical document (the value ADR 0005 proposes to carry in
  results).
- CLI `agent-harness qualification --check FILE [--evidence-root DIR] [--capability-report FILE]`: exit 0 valid
  and passing, 1 valid but not passing (or binding refused), 2 invalid/unreadable; with `--evidence-root`, every
  evidence file must exist inside the root with the stated size and digest.

## Acceptance criteria

- AC1: a complete passing report validates and passes; each of a missing check, duplicate id, unknown id, pass
  without evidence, failing check, missing/failing review is detected (invalid or not passing as specified).
- AC2: evidence verification rejects a missing, tampered, wrong-size or outside-root evidence file.
- AC3: a capability report claiming `qualified` binds only to a passing report with the same target and policy
  digest.
- AC4: CLI exit codes 0/1/2 as specified from the installed wheel; `scripts/check-wheel.sh` green.

## Evidence (2026-10-06)

- AC1–AC3: `tests/test_qualification.py`: the shipped example passes; a missing check, duplicate id, unknown id
  (`Q17`) and a pass without evidence are invalid; a failing check, a `not-run` check, a missing or failing review
  do not pass; evidence verification rejects a tampered, missing and symlinked-outside file (`UNSAFE_PATH`); a
  capability report binds only for the same target and policy digest, and `qualified` only to a passing report.
  Mutations caught: check set not enforced, review ignored, `qualified` without passing, evidence outside the root,
  pass without evidence.
- AC4: CLI exit 0 (passing, evidence verified), 1 (failing check; capability report for another target),
  2 (invalid JSON; tampered evidence); the printed digest equals `qualification_digest`. `scripts/check-wheel.sh`:
  145 tests OK, 1 skipped.
- `contract_fixtures/qualification-example.json` is pinned in the golden fixture set as a format example of a
  fictitious target (`example-not-a-real-target`).

## Independent evaluation (2026-10-06)

Fresh-context verdict on `2da78f6`: **fail**. Fixed: nested JSON exited 1 with a traceback (now 2, also for
duplicate keys); an invalid capability report was blamed on the other file; the `tuple` pinned nothing and had no
job identity (now `job_id`, `host`, `kernel`, `engine`, `workload_image` digest are mandatory and binding takes the
caller's `job_id`); evidence was optional, so forged digests passed and one file could serve every check (now
passing always re-hashes evidence, and each passing check needs evidence of its own); `validate_capability_report`
alone was not documented as insufficient (ADR 0002); the review was not tied to the report (now `subject` =
report digest without the review, reviewer ≠ author, evidence distinct); the example fixture bound like a real
report (its review now fails and its evidence is not shipped). Each new rule has a test whose mutation fails it
(job binding, evidence verification, shared evidence, author self-review, unbound review, check set, image digest,
review reusing check evidence). Remaining limits are listed in ADR 0002.

## Re-check (2026-10-06)

Verdict on `bb2cf18`: **fail** — the review and job bindings were satisfiable by the report's author (a passing
report could be relabelled for another job and re-signed with the same evidence). Fixed: each passing check's
evidence must name the `job_id` and the review evidence must name its `subject`, so relabelling needs new
evidence; the remaining authenticity limit is stated in ADR 0002 instead of claimed closed. Also: reviewer vs
author compared case-insensitively; `--job-id` without `--capability-report` is misuse (2); a missing evidence
root is blamed on the root; tampered or missing evidence is documented as 2. Isolated tests catch the job,
subject and case-folding mutations.

## Third check (2026-10-06)

Verdict on `9e01ec4`: **fail** on one line — the job id was matched as a substring, so evidence of job
`…attempt-10` satisfied a report for `…attempt-1` (also `job-12`→`job-1`, `job-1`→`1`). Fixed: the job id must
appear as a whole token (ID characters as boundaries); the three renumbering cases are tested and substring
matching (mutation) fails them. F2–F5 closed; no regressions.

## Final check (2026-10-06)

Verdict on `b8e1e90`: **pass** — whole-token job match verified on 18 cases (renumbering rejected, real mentions
and JSON contexts matched, metacharacters escaped, no backtracking on 10–50 MB). A job id followed by a sentence
period does not match (fails closed).
