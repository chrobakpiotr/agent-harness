# Harness review of the final AH5-04B-QUAL-001 qualification report (2026-10-08)

- Scope: read-only review of T-900 checkpoint `989df86`,
  `docs/specs/AH5-04B-QUAL-001/evidence/selected/` in the T-900 worktree. Supersedes the conclusion of
  [`review-showcase-qualification-2026-10-08.md`](review-showcase-qualification-2026-10-08.md), whose report was built on
  the earlier T-002 code.
- Verdict: **04b passes.** Target `showcase-docker-desktop-linux-guest` is qualified for job
  `local-20261008T205033Z-7939417fefb5` only.

## Result

```text
agent-harness qualification --check report.json --evidence-root selected \
  --capability-report capability.json --job-id local-20261008T205033Z-7939417fefb5
→ passes, sha256:f7cdab99c3fb6b18bb25e71c99199235007553ec4c140d62b305cccff29a2600 (Harness v0.5.0, exit 0)
```

- 26/26 checks `pass` (Q01–Q16, B1–B10). Checks and the report with the review removed are identical to T-005's
  report (`0d9deda`, digest `sha256:64e41e36…4170e`).
- `review_subject(report)` = `independent_review.subject` = `review.json` `review_subject` = `sha256:64e41e36…4170e`;
  the review file hash matches its reference; reviewer `codex-independent-evaluator-t900-attempt3-20261008` ≠ author
  `showcase-qualifier`; verdict `pass`.
- B10: the recorded v2 request and result pass `validate_request` and `validate_result(result, request)`; the
  candidate target passes `validate_target(qualified=False)`.
- Capability: `qualified: true`, `launch_ready: false`, refusal `BACKEND_UNAVAILABLE`, same target and policy digest.
- Tuple: Docker Desktop, Engine 29.8.2, kernel 7.0.14-linuxkit, image `sha256:9d72651c…915d5`.
- Fallback: T-006 recorded `NOT_REQUIRED_PENDING_REVIEW` for this report under the owner decision (fallback decided by
  the target's 26 checks; review is the T-900 gate). The GitHub-hosted runner was not run and is not qualified.

## Limits (as stated in the review)

The job ID is a local correlation identifier, and the evidence is repository artifacts with no provider or
cryptographic attestation. The qualification covers this exact target tuple and job; it grants no launch authority and
says nothing about production.

## Housekeeping (Showcase)

- The T-900 worktree has three untracked human-resolution records (`evidence/human-resolutions/T-003/…`, `T-900/…`);
  the primary checkout has four untracked lifecycle files. Commit them so the lifecycle history is versioned.
- Stale `evidence/docker-desktop/{report,capability}.json` and `job-id.txt` (run1) still sit next to the valid run;
  mark them superseded or remove them in a Showcase task.

## Next

04c can depend on this report: `agent-harness qualification --check` with the job ID above. Then 06b cutover.
