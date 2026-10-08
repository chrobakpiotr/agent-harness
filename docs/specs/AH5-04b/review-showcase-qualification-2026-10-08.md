# Harness review of Showcase AH5-04B-QUAL-001 qualification run (2026-10-08)

- Scope: read-only review of the selected evidence in the T-900 worktree
  (`docs/specs/AH5-04B-QUAL-001/evidence/selected`) against contract v2, the qualification report (ADR 0002) and
  B1–B10 (`grading-requirements.md`). Harness changes nothing in Showcase.
- Target `showcase-docker-desktop-linux-guest`, job `local-20261008T081503Z-3aff2f02a9fb`, tuple Docker Desktop /
  Engine 29.8.2 / kernel 7.0.14-linuxkit / image `sha256:9d72651c…915d5`.

## Result

With Harness `v0.5.0` from this repository:

```text
agent-harness qualification --check report.json --evidence-root selected \
  --capability-report capability.json --job-id local-20261008T081503Z-3aff2f02a9fb
→ passes, sha256:abeef27d1e0d543313d9d3270bc71078a9572fc003f46cff5d8a97e1fafcba97 (exit 0)
```

- `review_subject(report)` recomputes to `sha256:a75a4b34…54`, matching `independent_review.subject`; `review.json`
  sha256 matches its evidence reference; verdict `pass`.
- Capability report: `qualified: true`, `launch_ready: false`, refusal `BACKEND_UNAVAILABLE`, same target and policy
  digest. Qualified, not launchable — no production-readiness claim.
- B10: the recorded v2 request passes `validate_request` and the result passes `validate_result(result, request)`;
  candidate target with `qualification_digest: null`, applied limits equal the request, `fired: null`.

## Points from the 2026-10-07 review

| Point | Status |
|---|---|
| B8 disk limit is workspace plus `/tmp` | fixed: 240 MiB + 16 MiB = 256 MiB, ENOSPC observed |
| B8 timeout kills the container, not only the client | fixed: `container_stopped_by_id: true`, container state exited 137 |
| T-002 exception text bounded | not re-checked here (no exception text in selected evidence) |
| T-004 actions pinned by SHA | not part of this local run |

## Open (Showcase-owned)

1. T-900's registered verification exits 2 (`tooling/agent-harness/qualification/check_selected_report.py` does not
   exist), yet T-900 is recorded completed. Showcase should show which completion path accepted it; a task whose
   registered verification cannot run must not complete on a substitute command without an accepted replan.
2. Root `harness.py validate` reports `TASK_REPLAN_REQUIRED` for T-001 (active legacy packet differs from the planning
   contract). Leave the packet and `.agent-state` untouched; resolve through a Showcase task with the lifecycle
   commands.

Neither changes the qualification evidence above; both block calling the feature cleanly complete.
