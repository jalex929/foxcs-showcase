# Grading pipeline and capability status

"AI autograder" can suggest an unattended service that grades and posts results by itself. FoxCS is deliberately not that. This document describes what actually runs, how, and what is still planned.

## Status legend

- **Deployed**: in use with real students.
- **Implemented**: built and tested, runs locally, not yet part of the live workflow.
- **In development**: partially built.
- **Planned**: designed, not built.

## Capability table

| Capability | Status | Notes |
|---|---|---|
| Moodle platform, interactive lessons, telemetry plugin | Deployed | See [architecture.md](architecture.md) |
| Native quiz and assignment submission | Deployed | Mastery checks auto-scored by Moodle's quiz engine |
| Instruction completion credit from telemetry | Deployed | Awarded in real time when the lesson-complete event fires |
| Procedural gates before quality grading | Deployed | Example: attempts finished outside the student's class period are flagged before any content grading |
| On-submission grading of Coding Exercises (`grader/on_submission.py`) | Implemented, not deployed | Runs the student's code at upload in a bubblewrap sandbox (no network, no view of other files or processes) that self-tests before use, scores 50% runs / 50% correct, returns a draft. 100% line and branch coverage in CI. Needs installing on the server before going live. See [on-submission-grading.md](on-submission-grading.md) |
| Weekly batch grader (`grader/auto_grade.py`) | Implemented | Tested against synthetic and generated submissions. Built for the folder-based delivery format; the live course now records most of the same facts through Moodle and the telemetry plugin |
| AI-assisted review of code and written work | Deployed, teacher-operated | Run in a local AI coding session with the rubric and feedback spec as context. Not an API service |
| Required rubric file before any coding exercise is published | Deployed | Added after a batch of exercises went live without one |
| Structured output and score validation rules | Specified | Defined in the feedback spec and followed by the reviewer |
| Release check before every bulk push (`grader/release_check.py`) | Implemented | Blocks results that can't be right, flags score-rationale disagreements, and draws a spot-check sample. 100% line and branch coverage in CI. See [release-check.md](release-check.md) |
| Eval harness for AI grading outputs (`evals/`) | Implemented | Validates outputs from any source |
| Model runner for the eval harness (`evals/run_model.py`) | Implemented | Grades the eval cases through Claude Code in non-interactive mode on a Claude subscription (default), or through the API. Same prompt and strict schema either way. Records token usage and score spread across repeated runs. Tested with a fake CLI and a fake client. Baseline numbers not yet run |
| Queue worker with AI review (`grader/review_worker.py`) | Implemented, not deployed | Takes each submission event from a queue folder, grades it deterministically, and sends only the judgment cases to the model runner's Claude Code backend. Refuses to start unless Claude Code is on a subscription. Sandbox on by default; refuses to start if the sandbox self-test fails. 100% line and branch coverage in CI. See [on-submission-grading.md](on-submission-grading.md#moodle-integration) |
| Writing approved grades back to Moodle | Deployed, teacher-run | CLI scripts against Moodle's own APIs, for example a tool that converts a project tier to the correct grade |
| Above-and-beyond bonus points | Not active | Two specifications define overlapping bonus mechanisms that would double-count. Held until reconciled. The eval harness rejects any output that applies a bonus |
| Spreadsheet dashboard with intervention lists | In development | |
| Retrieval-grounded feedback | Planned | See [rag-roadmap.md](rag-roadmap.md) |

## How the AI-assisted tier works today

### 1. How does the reviewer receive submissions?

The teacher exports submissions from Moodle. Moodle usernames are pseudonymous IDs, so files arrive keyed by a pseudonymous ID and never by name. The teacher opens an AI coding session in the private workspace, where the submission files, the assignment rubric, and the feedback spec are all available locally. The deterministic grader's needs-review manifest is the worklist.

### 2. How are prompts and rubrics assembled?

By file, not by a prompt builder. Each coding exercise has a rubric file (required before it can be published). The feedback spec is standing context for every grading session. Mastery-check written answers are graded against a teacher-only answer key. Today the session reads these files directly.

The next step is a small deterministic context builder: given an assignment ID and its skill IDs, assemble exactly the right rubric, answer key, and feedback rules. That is also the first stage of the retrieval roadmap.

### 3. How are grades and feedback validated?

The feedback spec requires the reviewer to produce structured data first (criterion results with evidence, score, feedback, review flags) and render the student-facing text from that same structure. It lists sixteen validation rules, including:

- Every rubric criterion has a selected level, and criterion scores add up to the total.
- Earned points never exceed possible points unless an approved bonus is applied.
- The maximum score is never null. A missing rubric configuration is held for review, never sent.
- The narrative feedback matches the rubric, and no correct criterion is described as incorrect.
- Low-confidence work is held for teacher review.

I don't re-read every grade. I read the rationale, check the criteria and the batch's grading trends, and approve the batch. So before that happens, the [release check](release-check.md) runs on every result. It blocks anything that cannot be correct, flags anything where the score and its explanation disagree, and picks a few results for me to read in full. `evals/score_output.py` applies case-specific expectations to the same output format, to compare grading prompts and models.

### 4. How does feedback return to Moodle?

After approval, by script. Grades are written through Moodle's own grade APIs rather than direct database edits, and a full backup runs before any live change.

### 5. What requires teacher approval?

Everything. No score, feedback, XP award, or recommendation reaches a student without approval, including high-confidence results. Approval happens per batch: blocked results must be fixed, flagged results are reviewed individually, a spot-check sample is read in full, and then the rest is approved together.

### 6. What happens when grading fails?

| Failure | Behavior |
|---|---|
| Missing file | Recorded as not submitted. No crash |
| File saved under the wrong name | Fuzzy-matched by keyword, flagged with a plain-language note, small XP penalty |
| Malformed or missing telemetry | Recorded as saved with zero attempts. No crash |
| Browser timestamp formats | Both with and without milliseconds parse. A real bug here once silently blanked a column, and now has a regression test |
| Rubric cannot be applied confidently, work is missing, or an unexpected approach is used | Review trigger: held for the teacher with stated reasons |
| Score configuration missing or invalid | Never sent to the student. Held for review |
| Instructions embedded in student work | Treated as data, flagged for the teacher. Covered by an eval case |

## Grading output format

The reviewer's structured output, abbreviated. The eval harness validates this shape.

```json
{
  "rubricResults": [
    {
      "criterionId": "uses_return",
      "pointsEarned": 0,
      "pointsPossible": 4,
      "evidence": ["add_points calls print(score + points) and has no return statement."],
      "rationale": "The function displays the value but does not give it back.",
      "confidence": "high"
    }
  ],
  "score": {"standardPointsEarned": 6, "standardPointsPossible": 15, "bonusRecommended": 0, "finalPointsEarned": 6},
  "feedback": {"strengths": [], "priorityImprovements": [], "overallFeedback": "", "nextStep": ""},
  "review": {"teacherReviewRequired": false, "reasons": [], "overallConfidence": "high"}
}
```
