# Deterministic graders

Two graders live here:

- **`on_submission.py`** grades one Coding Exercise the moment it is uploaded. See [`../docs/on-submission-grading.md`](../docs/on-submission-grading.md).
- **`auto_grade.py`** grades a week of lesson activity in one batch, described below.

## Weekly batch grader

`auto_grade.py` is the deterministic tier of the FoxCS assessment pipeline. Standard-library Python only: no installs, no network, no AI. It reads facts that interactive lesson pages record about themselves and never judges quality.

```bash
python grader/auto_grade.py grader/sample-submissions --out-dir out
```

## Input

One folder per student and lesson, named `<STUDENT_ID>_<lesson_dirname>`. Inside, lesson pages save themselves with a `_completed` suffix and embed either:

- a hidden `<script type="application/json" id="foxcs-telemetry">` block of interaction events (vocab quiz checks, practice drill attempts), or
- hidden timestamp spans (`unlocked_at:` / `completed_at:` on a mastery check, `reviewed_at:` on flashcards).

## Output

| File | Contents |
|---|---|
| `auto_grade_report.csv` | Per submission: saved or not, attempt counts, first-try and final correctness, reflection text and word count, mastery check minutes, XP per activity, naming issues, total XP |
| `needs_review_manifest.csv` | Every file that needs human or AI judgment (project code, application code, written mastery-check answers), with the reason. These are not graded here |

## Rules worth knowing

- **XP requires evidence of real completion**, not just a saved file. A vocab quiz earns XP only when the final check is all correct and the reflection is not empty. A mastery check needs both timestamps.
- **Practice XP is not gated on perfection.** Practice is for practicing.
- **Misnamed files are found, not ignored.** If the exact filename is missing, the grader looks for a file whose name contains the right keywords, flags it with a plain-language note, and deducts a small XP penalty. The penalty never reduces genuine work to zero.
- **Bad data never crashes a batch.** Missing files, missing telemetry, and malformed JSON are recorded as such and the run continues.

See [`../docs/testing.md`](../docs/testing.md) for test coverage and [`sample-submissions/README.md`](sample-submissions/README.md) for the synthetic fixtures.
