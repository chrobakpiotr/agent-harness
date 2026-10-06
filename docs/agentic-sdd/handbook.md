# Agent practices handbook

For humans. Agents get the map in `AGENTS.md` and the pointers inside their role; this page explains the
whole set, how it is measured and how it changes.

## Roles

| Role | File | Practices it points at |
|---|---|---|
| Builder | [agents/builder.md](agents/builder.md) | diagnosing (unexpected red gate or test, bug fix), writing-for-agents (editing an agent document) |
| Evaluator | [agents/evaluator.md](agents/evaluator.md) | diagnosing (an executable `fail` carries the red command, run and shown) |

Both come from Showcase `docs/agentic-sdd/agents/` at `50c18f9`; Showcase keeps its copies (and its
stack-specific reviewers) until it consumes a versioned copy from here. Every role links to the
[constitution](constitution.md), which keeps the security rules inline.

## Practices

Adapted from [mattpocock/skills](https://github.com/mattpocock/skills) at `d81f3a18` (MIT, see
[THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md)). Each file opens with its list of adaptations.

- [diagnosing](practices/diagnosing.md): build a tight loop that goes red on the symptom, minimise it,
  record 3–5 falsifiable hypotheses in the task evidence, instrument one variable at a time, write the
  regression test before the fix. No loop means BLOCKED or MISSING_ENVIRONMENT, never PASS.
- [writing-for-agents](practices/writing-for-agents.md): how to write `AGENTS.md`, roles and practices
  (context pointers, information hierarchy, pruning). Security rules are exempt from pruning and disclosure.
- [retro](practices/retro.md): run only when a human asks. It proposes environment changes in order of
  severity, each tied to a real failure; accepted proposals land through a task packet. It never edits
  steering files or creates tasks itself.

## Measurement

`evals/diagnosing/` is a fixture repository with one planted bug (`invoice.py` truncates cents), hidden
tests and a deterministic grader. A run passes only with all four: a correct fix, a regression test that
fails on the base with assertion failures and passes on the fix, a red reproduction (a `python` command
printing the symptom `1998`) before the first change of `invoice.py`, and three hypotheses stating a
prediction or outcome in `HANDOFF.md`. The transcript checks are heuristics; `grade.py` lists them.

- Grader self-test (offline, in CI): `tests/test_eval_diagnosing.py`.
- Live A/B (manual, costs money, needs an authorised order): `python3.13 evals/diagnosing/run.py 3`. The
  `baseline` arm gets the builder role without its Practices section, the `practice` arm gets it whole.
  Claude runs with `--safe-mode --setting-sources project --disable-slash-commands`, so user plugins,
  skills, hooks and CLAUDE.md do not leak in. Results go to `.agent-runs/evals/diagnosing/<time>/`
  (ignored by Git); record the summary in the task evidence.

## Changing a practice

Follow constitution rule 13: start from a real failure case, change the practice through a task packet,
and re-run the live A/B; keep the old and new numbers side by side. Upstream updates are a new pin, never
a silent follow of `main`.
