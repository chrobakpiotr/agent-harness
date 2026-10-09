# Harness answer: SDD-OBS-001 T-009 manual reviewer principal and origin policy (2026-10-10)

- Scope: read-only check of the T-009 worktree (`6c9f492`, packet `sha256:3b24bbfa…93bb`) against SDD-OBS-001 spec
  §§ profile origin policy and authenticated manual reviewer provenance, and plan item 207.
- Not an implementation review: T-009 is not ready for completion.

## Is there another trusted source for `required_manual_reviewer_principal`?

No. The spec makes the accepted profile the only source:

- spec 290 and plan 207: the profile binds exactly one canonical principal per manual-origin gate, resolved from the
  trusted issuer registry and included in gate, obligation, family and final-plan identity;
- spec 1709: a persona name, provider output, report text, `--by`, `--operator` or caller-selected principal is not
  identity proof.

The issuer registry authenticates an issuer but does not say which principal an obligation requires. Not adding a
caller fallback was correct.

## A second gap: per-gate origin policy

AC-OBS-043 (in T-009) puts per-gate origin-qualification policy in the same declarative profile object, bound by
`profile_hash`, family and final plan. The current model has only a family-level `origin_policy`
(`model.py:59`, `planner.py:18`), and `verification-profiles/showcase.json` declares no per-gate origin. The positive
coverage path (AC-OBS-060) and AC-OBS-043 both need profile, model and plan-projection changes in T-001-owned files.

## Required scope update

A sequential replan of the running T-009 adds:

- `tooling/agent-harness/verification/model.py`, `verification/profile.py`, `verification/planner.py`;
- `tooling/agent-harness/schemas/verification-profile.schema.json`, `tests/test_verification_profile.py`;
- `tooling/agent-harness/verification-profiles/showcase.json` and `tests/test_verification_profile_showcase.py`, used
  only if the new per-gate fields become mandatory for existing profiles.

`test_seam` additions:

- a manual-origin gate without exactly one registered, enabled, `manual-review` principal invalidates the profile before
  plan publication;
- the principal and per-gate origin are part of `profile_hash`, obligation, family and final-plan identity (changing
  either changes the plan id);
- a valid signature from another principal is refused for the obligation;
- an untrusted request cannot upgrade a gate's origin.

T-001 and T-006 checkpoints remain history; T-900 evaluates the combined result. Then implementation review, with the
cases listed in the follow-up of `review-showcase-sdd-obs-t009-replan-2026-10-09.md` plus safe prelaunch-abort
terminalization.
