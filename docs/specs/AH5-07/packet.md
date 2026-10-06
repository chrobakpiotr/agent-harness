# AH5-07 — Versioned agent practices and a measured eval

- Status: **accepted** and **done** (re-evaluated 2026-10-06: pass).
- Source: master plan AH5-07 (develops H-06); depends on AH5-00. Inputs: `mattpocock/skills` at
  `d81f3a183412e71a5b1e84ca21bc1a35eea03a60` (MIT, © 2026 Matt Pocock): `engineering/diagnosing-bugs`,
  `engineering/retro`, `productivity/writing-for-agents`; Showcase roles at `50c18f9` (read-only).

## Findings (2026-10-05)

- This repository has no role definitions. The roles live in Showcase: `docs/agentic-sdd/agents/*.md`
  (19 roles; `builder`, `evaluator` and `integration` are stack-neutral, the reviewers are mostly
  Showcase-specific) plus thin `.claude/agents/*.md` wrappers that point at them.
- The constitution here is already the shared contract that consumers receive as versioned copies.
- Showcase `docs/agentic-sdd/evals.md` measures the harness workflow (`eval.py`), not agent practice; no
  role mentions a diagnosis loop, a retrospective or writing rules for agent documents.
- Upstream conflicts with this contract:
  - `diagnosing-bugs` asks to show hypotheses to the user and wait; under the SDD protocol the agent
    records them in task evidence, and a human is reached through `needs-human`.
  - `retro` edits steering files and coding standards directly; here it only proposes changes, which
    land through a task packet.
  - `writing-for-agents` prunes and discloses behind pointers; security rules are exempt and stay
    inline (constitution: available offline in every repo).
  - The upstream `GLOSSARY.md`, `CODING_STANDARDS.md` and Skill-tool calls do not exist here.
- H-06 is defined in the reviewed handbook PDF, whose text could not be extracted here; this packet
  works from the master-plan wording only.

## Scope

- `docs/agentic-sdd/practices/{diagnosing,retro,writing-for-agents}.md`: adapted copies, each with a
  notice (upstream path, pinned SHA, licence, list of adaptations); `THIRD_PARTY_NOTICES.md` with the MIT
  text.
- `docs/agentic-sdd/agents/{builder,evaluator}.md`: the stack-neutral Showcase roles, each gaining one
  pointer to the practice it uses (builder → diagnosing on an unexpected red gate; evaluator → diagnosing
  for a reproducible failure; any agent editing an agent document → writing-for-agents). `retro` is
  human-invoked only.
- `AGENTS.md`: map lines only (agent gets the map and references). Human handbook: `docs/agentic-sdd/
  handbook.md`, the full description of roles, practices, eval and update procedure.
- Mini eval `evals/diagnosing/`: a fixture repository with one planted bug and a deterministic grader.
  It checks, by heuristics over the transcript and the handoff, a red reproduction run before any fix, listed
  hypotheses, and a regression test that fails on the base and passes on the fix.

## Out of scope

Copying other upstream skills or the plugin, a skill installer, a second scheduler or lifecycle (the
practices never create or schedule tasks), Showcase changes, live model calls in CI.

## Acceptance criteria

- AC1: each adapted file carries its notice; `diff` against the pin shows only the listed adaptations.
- AC2: the grader passes a reference solution and fails each of: fix without a red repro, repro after
  the fix, missing hypotheses, regression test that does not fail on the base.
- AC3: measured improvement: the same fixture run with a live agent with and without the practice
  (N runs each, manual, not CI); grader scores recorded with model, CLI version and SHA. No claim without
  the numbers.
- AC4: no security rule is moved behind a pointer or removed (`test_docs` check that the constitution's
  security section stays inline and every role links to the constitution).
- AC5: links resolve (`test_docs`), `scripts/check-wheel.sh` green; nothing added to the wheel.

## Decisions (2026-10-05)

1. `builder` and `evaluator` move here with the practices; Showcase keeps its copies until it consumes a
   versioned one.
2. The mini eval lives here, `evals/diagnosing/`.
3. AC3 is authorised with the local `claude` CLI, N=3 runs per arm, manual and never in CI.

## Evidence (2026-10-05)

Environment: macOS 24.6.0 (x86_64), CPython 3.13.16, Claude Code 2.1.289, model `claude-opus-5-5`.

- AC1: each practice opens with its notice; against the pin, every diff hunk is a listed adaptation
  (`diagnosing` 9 hunks / 23 lines of 138, `retro` 9 / 24 of 44, `writing-for-agents` 4 / 13 of 81).
  `SKILL-MECHANICS.md`, `agents/openai.yaml` and `scripts/hitl-loop.template.sh` were not copied.
- AC2: `tests/test_eval_diagnosing.py`: the reference run passes; a fix without a red repro, a repro only
  after the fix, a handoff without hypotheses, a regression test that passes on the base, and an unfixed
  source each fail.
- AC3: live A/B, `evals/diagnosing/run.py 3` at `fab55ca` plus this packet's working tree (practice and
  role files as committed here), isolated with `--safe-mode --setting-sources project
  --disable-slash-commands` (no user plugins, skills, hooks or CLAUDE.md). Total cost $0.98.

  | Arm | PASS | fix | regression | red before fix | hypotheses | mean cost | mean turns |
  |---|---|---|---|---|---|---|---|
  | baseline | 0/3 | 3/3 | 3/3 | 2/3 | 0/3 | $0.14 | 6.3 |
  | practice | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 | $0.19 | 8.7 |

  The first grading counted hypotheses only in "if … then" form and scored 0/3 in both arms; all three
  practice handoffs held a ranked, numbered list with predictions in other wording. The check now counts
  numbered items under a line naming hypotheses; all six saved runs were regraded with it (no new model
  calls), and no baseline handoff mentions hypotheses, predictions or ruled-out causes.
  Limits: N=3 on one easy fixture; both arms fixed the bug and wrote a regression test every time, so the
  measured gain is process (repro before edit, recorded hypotheses) at about +40 % cost, not fix rate.
  The hypotheses check is what the practice asks for, so it partly measures instruction-following.
- AC4: `test_docs`: constitution rule 11 stays inline, every role links to the constitution, every
  practice carries its notice.
- AC5: `scripts/check-wheel.sh` on 3.13: 99 tests OK, 1 skipped (`test_human_grants`, no cryptography wheel
  on Intel macOS); `evals/` and `docs/` stay out of the wheel.

## Independent evaluation (2026-10-06)

Fresh-context evaluator verdict: **fail**, on the grader only. AC1, AC4 and AC5 held, and the AC3 table
reproduced. Findings and fixes:

- The red-before-fix check missed shell edits other than four idioms (`python3 -c open(...,'w')`, `cp`,
  `git apply`) and counted any `1998`, including `echo`/`print(1998)`. Fix: any shell segment naming
  `invoice.py` that is not a reader, a redirect into it, or a tree-rewriting Git command counts as an edit; a
  red event must be a `python` command whose output, but not its own text, holds the symptom.
- A numbered list of steps counted as hypotheses; bullets and an earlier "hypothesis" line failed real
  lists. Fix: list items (numbered or bullets) under any line naming hypotheses, each stating a prediction or
  outcome.
- A regression test failing on the base through an ImportError counted. Fix: the base run must fail with
  assertion failures only.
- The evaluator role now asks for the red command only for a `fail` that can be executed.

All cases are in `tests/test_eval_diagnosing.py`. The six live runs regraded with the fixed grader give the
same table as above. Correction to the AC3 reading: at N=3, red before fix (3/3 vs 2/3) is one run and
not evidence of a process gain; the only clear difference is the recorded hypotheses, which the practice
asks for. The packet's "passes only if" wording now says the checks are heuristics. The run artifacts
(`.agent-runs/` is ignored by Git) are kept as `evidence/live-run-20261005T211008Z.tar.gz`; regrade with
`python3.13 evals/diagnosing/grade.py <run>/<arm-i> <run>/<arm-i>.jsonl` after extracting it.

## Re-evaluation (2026-10-06)

Fresh-context evaluator verdict on `052ed10`: **pass**. The four findings are closed, AC2 holds, and the six
runs regrade to the AC3 table. Remaining, accepted as the documented heuristic limit (none affects the saved
runs): deliberate gaming still passes (`patch`/`python3 fix.py` edits, `print(1997+1)` as "repro", step lists
worded with "if"/"confirmed"); honest shapes still fail (a base repro via `git stash`, heredoc text naming
`invoice.py`, read-only `awk`/`python3 invoice.py`, `skipped=` in the base summary, hypotheses in a table or
prose). Follow-up when the eval is reused: compare `invoice.py` content before and after each tool call instead
of inferring edits from commands, and drop heredoc bodies from command scanning.
