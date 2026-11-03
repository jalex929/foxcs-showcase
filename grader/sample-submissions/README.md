# Synthetic sample submissions

**Every file here is invented.** No real student work, no real lesson content, no real assessment items. Regenerate with `python tools/make_fixtures.py grader/sample-submissions`.

## STUDENT-0001: "nearing proficiency"

A deliberately realistic, imperfect submission for a lesson on printing output. The gaps form one consistent pattern (confusing *argument* with *output*, and writing generic rather than specific messages), because real students' strengths and gaps show up as patterns across a submission, not as unrelated mistakes on every file.

| File | State | Grader should report |
|---|---|---|
| `01_instruction.html` | Reading only | Not graded, not in manifest |
| `02_example_01.py` | View-only example | In manifest as "skip" |
| `03_flashcards.html` | Untouched, no `_completed` suffix | Fuzzy-matched, naming note, 0 XP |
| `04_vocab_quiz_completed.html` | 2 check attempts: mixed up argument and output, then all correct. Reflection names the mix-up | 2 attempts, first not all correct, all correct by save, full XP |
| `05_practice_completed.html` | 8 drills, 4 correct on first try, 3 corrected on retry, 1 optional preview drill not retried | 8 drills, 4 first-try correct, full XP |
| `06_application.py` | 5 prompts, #4 has `Print(...)` with a capital P | In manifest for review |
| `07_project.html` | Required items checked, bonus items not | In manifest |
| `08_project.py` | Works, but the problem message is generic | In manifest |
| `09_mastery_check_completed.html` | Unlocked and completed 14.6 minutes apart | 14.6 minutes, full XP |
| `10_mastery_check.py` | Written answers, Q2 and Q3 imprecise | In manifest |
| `11_feedback_completed.html` | Ratings filled in | Saved, full XP |

These exact values are asserted by `TestRealFixtureEndToEnd` in `../tests/test_auto_grade.py`.

## STUDENT-0002: sparse and misnamed

A vocab quiz saved as `Vocab Quiz (1).html`, a practice page saved without attempting any drills, a project file, and nothing else. Demonstrates the fuzzy filename match with its XP penalty, zero practice XP for an empty attempt, and missing activities reported as not submitted.
