# Content quality: rubrics, question checks, and fair marking

An AI grader is only as good as the questions and rubrics it grades against. A wrong answer key, or a question that says "two mistakes" when there are three, produces confidently wrong grades no matter how good the model is. So the course content has its own checks, in the private workspace that holds the real assessments. This page describes them; the assessments themselves stay private.

## Every submittable item has a rubric, enforced in CI

A manifest lists every item a student can submit for feedback: Coding Exercises, Mastery Checks, Projects, checkpoints and reflections, each with its rubric file or a written reason it needs none. A checker fails CI if any live item's rubric is missing, empty, has fewer than two real criteria, or still holds a placeholder like `TBD`.

The first run found 14 live items without a usable rubric. Five were answer keys in a format the checker didn't recognize yet. The other nine really were missing, including four exercises that had gone live before the rule existed. All are written now, and the check runs on every push.

## Question checks: Python grades the answer key

Every Mastery Check task whose answer Python can verify has a machine-readable entry, and a checker runs each one:

| Claim in the question | How it's checked |
|---|---|
| "Predict exactly what this displays" | The code runs; its output must equal the key's expected output |
| "This line is broken. Fix it" | The broken line must fail with the named error; the fix must run and print the target |
| "This line has three mistakes" | The count in the wording must equal the mistakes listed |
| "It should display: Continue" | The stated output must equal what the fix prints |
| The question students see | Must match the live build script exactly, so the bank and the course can't drift apart |
| The teacher key | Must state the same answer |

A task that makes a checkable claim without an entry fails the check, so nothing slips through untested. It caught a real error on its first day: a task that said a line had "two mistakes" when it has three. Its own tests replay that bug to prove it stays caught.

## Fair marking: valid code is never wrong for spacing

`print( "Game Over" )` is valid Python, so it's never marked wrong. It earns full credit plus a one-line note on writing Python cleanly.

- **Auto-scored questions** accept every valid combination of quote style and spacing (16 forms per `print()` task). Each one is run through Python in the tests to prove it prints exactly the target. The spaced forms carry a clean-code tip as feedback.
- **Hand-graded and AI-graded answers** follow the same rule, written into every answer key.
- **The in-page editor** shows clean-code tips while students work, and never blocks a run.

## Expected answers vs. reasoning

Most questions have an expected answer (a fill-in-the-blank, a predicted type, the name of a mistake). Those are marked right or wrong against the key. Close paraphrases and any level of English count, because many students are English Language Learners.

Open "explain why" comments use a four-level scale instead:

| Level | The comment |
|---|---|
| 3 | Gives a true reason, specific to this line in this program |
| 2 | Points at a reason, but is generic or partly off |
| 1 | Restates what the code does |
| 0 | Missing, wrong, or about a different line |

Two quick tests settle the level. The **deletion test** asks whether the comment says what's lost if the line is deleted. The **swap test** asks whether the comment could sit above any other line. The average level maps to the "correct" half of the score, and the AI reviewer must quote each comment it scores, so the release check can confirm the quote is real.

## An in-page code editor that produces the submission

Coding Exercises take a `.py` file upload only. In one unit, about 70% of zero scores came from submissions that couldn't be graded: unshared links to an online editor, the wrong exercise, or an untouched starter file. To make file-only submission work on every computer, Chromebooks included, the lessons carry an in-page Python editor ([`platform/code-workspace/`](../platform/code-workspace/)):

- **Run** executes real Python in the browser (Skulpt), with no server.
- **Check** compares the output with the expected output, line by line.
- **Download .py** saves exactly the file the student uploads.
- **Clean-code tips** appear as notes, never as errors.

Its logic has unit tests here. In the private workspace, a real-browser test also runs it with the real Python runtime against each lesson's actual starter code: a starter that should be broken must fail, and the correct fix must pass.
