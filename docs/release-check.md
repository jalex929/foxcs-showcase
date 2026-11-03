# Release check: checking AI-assisted grades before a bulk push

**Status: implemented and tested (100% line and branch coverage, enforced in CI). Next step: run it on every live batch before the bulk push.**

## Why it exists

I don't re-read every AI-assisted grade. I read the rationale, confirm the criteria are right, check that the grading trends look sound, and then approve the whole batch at once. That is the only way grading fits in an hour a week, and it rests on one assumption: if the criteria are right and the rationale lines up, the grade is right.

The release check turns that assumption into something tested. It looks at every result in the batch, mechanically, before I do. It blocks anything that cannot be correct, flags anything where the score and its explanation disagree, and gives me a small random sample to read in full, so the assumption keeps being checked against fully read results.

## What it checks

| Group | Check | Why it matters |
|---|---|---|
| **Blocked** | Every rubric criterion scored exactly once, and nothing from another rubric | A wrong rubric produces a confident, well-explained, wrong grade |
| **Blocked** | Points within range and matching the rubric; criteria add up to the total; no bonus applied without approval | Arithmetic errors are invisible when skimming rationale |
| **Blocked** | Any criterion that earns points cites evidence | Credit with no evidence is unsupported |
| **Blocked** | Code quoted as evidence actually appears in the student's files | Catches invented evidence, the most dangerous kind of wrong |
| **Blocked** | Grades for facts the program run settled agree with that run | A program that crashed cannot earn "runs without errors" |
| **Blocked** | A next step is present | Feedback without a next step isn't finished |
| **Needs review** | Full points, but the rationale names a problem ("missing", "does not", "fails") | The rationale and score disagree |
| **Needs review** | Zero points, but the rationale only praises | Same, in the other direction |
| **Needs review** | Strong praise below full marks, or full marks with required fixes | Feedback that doesn't fit the score confuses students |
| **Needs review** | The grader asked for review, or marked low confidence | Its own escalation is respected |
| **Needs review** | The same overall feedback attached to different scores | Reads as a template, not a judgment |
| **Ready** | Passed everything | Eligible for the bulk push |

The program-run check uses the drafts from [on-submission grading](on-submission-grading.md). When the run was clearly flawless or clearly broken, every criterion it measured is a fact. When the run was partly right, only "runs without errors" is a fact, and how close the output got stays a judgment call.

Wording checks flag for review rather than block, because word matching can misread a sentence. It knows that "No errors" and "runs without raising an error" are good news.

## What I see

```
6 results: 3 ready, 3 need review, 0 blocked.
  Spot check: 02_right_output_wrong_logic, 03_partial_solution, 01_valid_alternative
```

- **`release_report.md`:** the counts, whether the push is allowed, the score spread and full-credit rate per criterion (the trends I already look at), the spot-check list, and every flagged result with its reasons.
- **`release_manifest.csv`:** one row per result with its group and findings, so the push only sends what is ready and reviewed.
- **Exit code 1 when anything is blocked,** so a push script can refuse to run.

### Spot checks

Each batch, a few ready results are drawn at random, spread across low, middle and high scores, for me to read in full. The draw is seeded from the batch itself, so it's reproducible and nobody chooses it. Agreement on those reads is the evidence that bulk approval is safe. If a spot check turns up a grade I'd change, that's a signal to tighten the checks or the grading prompt before trusting the next batch.

## Testing

`grader/tests/test_release_check.py` (68 tests) builds batches from the [eval suite's](../evals/README.md) synthetic cases and reference judgments.

- **The known-good batch raises no false alarms.** The only results flagged are the ones the grader itself escalated.
- **Every check gets a deliberately broken result** and must catch it.
- **Mutation tested.** Every one of about 200 automatic small changes to the code must be caught by a test (see [testing.md](testing.md#mutation-testing)). At 100% coverage, the first run still found 37 untested behaviors, including that a 1-point credit with no evidence would have slipped through. All are now covered. Two survivors are documented as genuinely equivalent.

## Limits

- **It checks consistency, not quality.** A grade can pass every check and still be a judgment I'd make differently. That is what the spot checks are for.
- **Quote checking only covers code in backticks.** Evidence written in plain words is checked for presence, not accuracy.
- **Wording checks are heuristics.** They can miss a disagreement phrased in unusual words.
