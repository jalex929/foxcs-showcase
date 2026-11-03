# Testing and validation

Every suite here runs with a standard Python install (3.10+), no dependencies. CI runs all of them on every push: [`.github/workflows/tests.yml`](../.github/workflows/tests.yml).

## How to run

```bash
python -m unittest discover -s grader/tests -v    # both deterministic graders

# 100% line and branch coverage gate for on-submission grading and the release check (needs: pip install coverage)
python -m coverage run --branch --include=grader/on_submission.py,grader/release_check.py -m unittest discover -s grader/tests
python -m coverage report -m --fail-under=100
python -m unittest discover -s evals/tests -v     # eval harness
python -m unittest discover -s tools/tests -v     # leak scanner
python tools/check_showcase_clean.py              # leak scan of this repo
python evals/run_evals.py                         # eval suite vs. reference judgments
python tools/mutation_test.py                     # mutation testing (under two minutes)
```

Successful output ends with `OK` for each unittest suite, `Clean: no known private-workspace patterns found.` for the scanner, and `Cases passed: 6/6` for the eval run.

## What is covered

### On-submission grader: 46 tests, 100% line and branch coverage

CI fails if any line or branch of `grader/on_submission.py` goes untested. The only exclusion is the `if __name__ == "__main__":` line, and the command line behind it is tested by calling `main()` directly. Most tests run real programs through the real subprocess path rather than mocks.

| Area | Cases |
|---|---|
| Scoring bands | Flawless, valid alternative solution, runs but wrong, partially correct (scaled credit), syntax error, runtime error; criteria always sum to the total and never exceed it |
| Safety | Every result is a draft pending approval; instructions written to "the autograder" change nothing; infinite loop times out and is held unscored; memory limit stops runaway allocation; child runs isolated with a minimal environment; path outside the submissions folder refused; a grader exception is held, not raised |
| Duplicates | Duplicate delivery returns the stored result without re-running; a resubmission with new contents is graded again |
| Held for review | Unknown assignment, no file, two files, wrong extension, missing, empty, oversized, non-UTF-8 |
| Input validation | Every rejection path for events and exercise specs, defaults, duplicate assignment IDs |
| Helpers and storage | Output truncation, byte decoding, error summaries, latest-record-wins storage |
| Command line | Success, invalid event, malformed and missing event files |
| Golden files | Committed example results in `examples/on-submission/` match fresh runs |

**Mutation tested.** See [Mutation testing](#mutation-testing) below. At 100% coverage, the first run still let 19 changes survive, including a missed check that partial credit rounds to cents and that a 1-point exercise is accepted. All are now covered or documented as equivalent.

### Release check: 68 tests, 100% line and branch coverage

Batches are built from the eval suite's synthetic cases and reference judgments, so the good batch is known-good. The known-good batch raises no false alarms; the only results flagged are the ones the grader escalated itself. Every check gets a deliberately broken result: a criterion from another rubric, a criterion scored twice, points out of range, arithmetic off, an unapproved bonus, credit without evidence, evidence quoting code that isn't in the submission, a grade contradicting the program run, full marks explained with a problem, zero explained with praise, praise below full marks, and the same feedback on different scores. Spot-check sampling is tested for spread across low, middle and high scores (across 30 seeds) and for reproducibility. See [release-check.md](release-check.md).

### Weekly batch grader: 55 tests

| Area | Cases |
|---|---|
| Telemetry parsing | First vs. last attempt correctness, missing telemetry block, malformed JSON, no matching event type |
| Vocab quiz | Not saved, saved with reflection, empty reflection, XP only when all correct and reflection present |
| Practice drills | First-try correctness counted per drill, not per event |
| Mastery check | Timing with and without milliseconds (regression test for a real bug), missing timestamp |
| Flashcards | XP only with the reviewed marker |
| File naming | Exact match preferred over fuzzy, fuzzy match flagged and penalized, penalty never zeroes real work, untouched template earns nothing |
| Needs-review manifest | Only judgment-required files listed, both naming forms of a written mastery check caught |
| End to end | Full run against a synthetic submission whose persona is documented in [`grader/sample-submissions/README.md`](../grader/sample-submissions/README.md), asserting exact values |
| Command line | Exit codes for a missing folder, an empty folder, and loose files; output folder created; report and manifest columns; one row per submission; total XP equals the sum of its parts; every naming issue counted in the console summary (regression test for a real under-count) |
| Second submission | Misnamed quiz found and penalized, empty practice attempt earns nothing, missing activities reported as not submitted |
| Golden file | The committed `examples/demo-output/` must match a fresh run byte for byte, so the README never shows stale results |

### Eval harness: 48 tests

Each of the 11 checks in `evals/score_output.py` has a test that feeds it a deliberately broken output (injection followed, invented rubric criterion, wrong arithmetic, unapproved bonus, null maximum, missed misconception, invented evidence on missing work, credit without evidence, escalation without reasons, empty next step) and asserts that the right check fails. The reference outputs must pass every check. A check that cannot fail is not tested.

Beyond that:

- **Dataset integrity.** Rubric points add up, criterion IDs are unique, every case is well formed and its ID matches its filename, score ranges are possible, every criterion bound names a real criterion within its points, and every injection case is actually testable (the injected target falls outside the expected range, and the injected text really appears in the submission).
- **Check edge cases.** Missing keys stop later checks, duplicate criteria, mismatched or negative points, fractional points, inclusive range edges, confidence ordering, case-insensitive phrase matching, forbidden text hidden in a rationale.
- **Runner CLI.** Exit 0 on the reference outputs, exit 1 with `MISSING OUTPUT` when a case has no output, exit 1 naming the failing check when an output is broken.

### Leak scanner and repository hygiene: 9 tests

- Each leak category the scanner claims to catch is caught, clean text passes, and binary files are flagged for review.
- **This repository itself scans clean**, so the suite fails even if the CI scanner step were removed. (It caught a real slip while these tests were being written.)
- Scanner exit codes and `file:line` reporting work as CI relies on, and `.git` and `__pycache__` are skipped.
- **Committed fixtures are byte-identical to `tools/make_fixtures.py` output**, so nothing can be hand-edited into them unnoticed.
- Every relative link in the Markdown docs resolves.

## Mutation testing

Coverage shows that every line ran during the tests. It doesn't show that a test would notice if a line were wrong. `tools/mutation_test.py` checks that directly. It makes one small change at a time to the grading code: flips a comparison, swaps `and` for `or`, inverts a condition, nudges a number, returns `None`. Then it runs the tests and expects them to fail. A change the tests don't catch is a surviving mutant, and it points at behavior nothing checks.

| Module | First run (already at 100% coverage) | Now | Documented equivalents |
| --- | --- | --- | --- |
| On-submission grading | 88.7%: 19 of 168 mutants survived | 100% of 166 | 3 |
| Release check | 83.0%: 37 of 218 mutants survived | 100% of 204 | 2 |
| Eval scorer | 90.5%: 13 of 137 mutants survived | 100% of 137 | 1 |

Mutant counts dropped where the first run exposed code that could be removed.

What the survivors pointed at, and what changed:

- **Untested behavior, now tested.** Examples: a 1-point credit with no evidence would not have been blocked. A forbidden phrase hidden in a "strength" was invisible to the eval. Partial credit wasn't pinned to cents. Bounds exactly at the limit were never exercised.
- **Code that didn't need to exist, now removed.** A guard in the spot-check sampler that could never change the result. A length check implied by the one after it. A `text=True` already implied by `encoding=`.
- **A bug in the mutation tester itself.** Two identical changes on one line shared an identity, so excusing one would have silently excused the other. Fixed, with a regression test.
- **Genuinely equivalent changes, written down.** For example, `> 1e-6` versus `>= 1e-6` on a sum of rubric points, or JSON indentation in command-line output. Each is listed in `tools/mutation_equivalents.json` with its reason. A test fails if an entry goes stale.

The tester is standard-library Python, like the rest of the repository. Its own 15 tests run it against a tiny sample module with two test suites: one that checks behavior, and one that runs every line but asserts almost nothing. The weak suite has full coverage and a low mutation score, which is the difference the tool exists to show. CI runs mutation testing as a separate job and fails on any unexplained survivor. The full run takes under two minutes.

## What is not covered here

Honest gaps, so nobody has to guess:

- **No baseline model runs yet.** The eval harness validates outputs, but this repository does not yet include outputs from an actual model run. That is the next step for `evals/`.
- **The eval checks are deterministic.** They catch structural and rule violations (arithmetic, rubric coverage, escalation, forbidden claims). They do not judge whether feedback is genuinely helpful. That needs educator review or a carefully validated model-graded rubric, and is planned.
- **Platform tests live in the private workspace.** The live platform has its own CI: content checkers with their own test suites, a PHP harness for the practice-ladder wiring checker, and a headless-browser test proving a student's typed answer survives save on a real lesson page. Those depend on licensed course content and are not reproduced here.
- **No load or concurrency testing.** Class sizes have not required it. The on-submission store is a single append-only file, which suits one worker. Several concurrent workers would need a real queue or database.
- **On-submission isolation is not tested against hostile code**, because it is not designed to stop it. That is the container's job in production. See [on-submission-grading.md](on-submission-grading.md).
- **Six eval cases is a starting set**, chosen for coverage of distinct failure modes rather than statistical power.

## Why this shape

For a system whose outputs reach students, a reproducible test suite with honest gaps is more useful evidence than a coverage percentage. The deterministic tier is tested the conventional way. The probabilistic tier is tested by fixing the expectations and checking any output against them, so prompt changes and model changes can be compared on the same cases.
