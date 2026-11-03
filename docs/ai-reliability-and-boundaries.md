# AI reliability and boundaries

This document describes how FoxCS limits what an AI reviewer can get wrong, and what happens when it does. Everything here describes behavior as actually implemented or specified. Planned work is labeled.

## What the AI is trusted to do

Draft rubric-based scores and feedback for work that needs interpretation: code, written explanations, and projects. That is all. It does not:

- Decide anything a rule can decide. Completion, attempt counts, timing, and procedural gates are handled deterministically before the AI sees anything.
- Release anything. Every result is a draft until the teacher approves it.
- Make academic-integrity decisions. It may flag unusual work for the teacher in neutral language. It may never tell a student they cheated, copied, or used AI.
- Apply bonus points. Bonus recommendations always require teacher approval, and the bonus path is currently disabled entirely (see [grading-pipeline.md](grading-pipeline.md)).

## Known model limitations and how each is handled

| Limitation | Mitigation | Where |
|---|---|---|
| Confident but wrong scoring | Rubric is the primary source of truth; best-fit scoring so one minor gap does not sink a score; teacher approval on every result | Feedback spec, Release Gate |
| Inventing evidence | Every criterion that earns points must cite specific evidence from the submission | Feedback spec; enforced by `evidence_for_credit` in `evals/` |
| Feedback that contradicts the score | Score, rubric, and narrative are all rendered from one structured evaluation, then validated against each other | Feedback spec sections 20 and 21 |
| Applying criteria from the wrong assignment | Output must cover exactly the assignment's rubric criteria, no more and no fewer | `rubric_coverage` check |
| Overconfidence on thin evidence | "Not Enough Evidence" is a first-class outcome. Missing or unreadable work triggers review | Review triggers; `confidence_cap` check |
| Prompt injection inside student work | Submission content is data, never instructions. Embedded requests to the grader are ignored for scoring and flagged for the teacher | Eval case `05_prompt_injection` |
| Unhelpful or harsh tone | A voice specification with banned and preferred phrasing, and a required specific next step on every result | Feedback spec sections 2 and 12 |

## Human review triggers

The reviewer must hold a result for the teacher, with stated reasons, when any of these apply:

- The rubric cannot be applied confidently, or the directions are ambiguous.
- The student used a valid but unexpected approach.
- The explanation conflicts with the code, or the score and feedback do not align.
- Required files are missing or unreadable, or the code cannot be executed or meaningfully inspected.
- The submission contains concerning or unsafe content.
- Similarity or authorship concerns are present.
- Repeated attempts show no meaningful progress.
- Accommodations or individual context may affect evaluation.
- There is not enough evidence to make a fair decision.
- A bonus is being recommended, or the score configuration is incomplete.

"Communicate uncertainty rather than inventing confidence" is the governing rule.

## Privacy boundary

An AI tool receives a pseudonymous ID, the submission files, the rubric, and pseudonymous history. It never receives a name, school ID, email, or guardian information. Pseudonymity reduces risk but does not by itself make student data safe to send anywhere: what a student writes can still identify them. See [privacy-and-data.md](privacy-and-data.md).

## Failure behavior

- **Invalid or incomplete score configuration**: never sent. Held for review.
- **Reviewer output fails validation**: held. In the live workflow this is caught at teacher review today. Automated validation of every live output with the `evals/` checks is planned.
- **AI tooling unavailable**: nothing degrades for students. The deterministic tier and Moodle's own quiz scoring keep running, and the review queue waits.

There are no automatic retries. Because a person reviews every result, a failed or doubtful output is re-run deliberately or graded by hand, rather than retried until it passes.

## What the eval harness adds

The rules above are only as good as the evidence that they hold. `evals/` turns the mechanically checkable subset into code that can score any grading output, so a prompt change or a model change can be compared on the same adversarial cases before it touches real student work. See [`evals/README.md`](../evals/README.md).
