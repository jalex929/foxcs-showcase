# Example output

`demo-output/` is the unedited output of:

```bash
python grader/auto_grade.py grader/sample-submissions --out-dir examples/demo-output
```

run against the two synthetic submissions in `grader/sample-submissions/`. CI re-runs the same command on every push.

- `auto_grade_report.csv`: one row per submission. STUDENT-0001 earns 36 XP with one naming note (untouched flashcards). STUDENT-0002 earns 3 XP: the misnamed vocab quiz is found and penalized 2 XP, and the empty practice attempt earns nothing.
- `needs_review_manifest.csv`: the six files that go to the AI-assisted review tier, each with its reason.

## On-submission grading

`on-submission/` holds three example submission events and the draft each one produces:

| Event | Submitted program | Draft |
|---|---|---|
| `event-flawless.json` | Correct solution | `flawless`, 10/10, no review needed |
| `event-runs-with-issues.json` | Uses the text from `input()` without converting it, so `*` repeats the string | `runs_with_issues`, 5/10, held for review |
| `event-does-not-run.json` | Adds a number to text, raising `TypeError` | `does_not_run`, 0/10, feedback quotes the error |

A test re-runs each event and fails if the committed result no longer matches.
