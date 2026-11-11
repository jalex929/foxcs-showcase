# Model comparison: fixing a confidence failure

A record of one evaluation cycle: real model runs exposed a repeatable failure, one sentence of the grading prompt changed, and the same runs were repeated to measure the effect.

**Scope.** Six synthetic cases, then five held-out cases, then all eleven through the API, three repeats, two models, run on 2026-11-11. This demonstrates the evaluation method. It is not a benchmark or an accuracy figure, and it does not show which model is better in general.

## Setup

- **Models:** Claude Opus 5.5 and Claude Sonnet 5.5, at high effort.
- **Path:** Claude Code in non-interactive mode on a subscription. The API backend was tested afterward, in [its own section](#the-api-path).
- **Cases:** the six adversarial cases in [`evals/cases/`](../evals/cases/).
- **Scoring:** 11 deterministic checks per case, by [`evals/run_evals.py`](../evals/run_evals.py).
- **Raw outputs:** [`evals/results/2026-11-confidence-prompt/`](../evals/results/2026-11-confidence-prompt/).

## The failure

Before the change, both models failed the same check in all six runs. On a missing submission they scored 0 correctly and sent the work to the teacher, but reported `overallConfidence: high`. The case caps confidence at low.

The repeatability pointed away from random error. The models appeared to be answering "how sure am I that this work is missing?" The system needs "how sure am I that this score reflects what the student can do?" With no work to judge, the honest answer to the second question is low.

## The change

The check was correct against the feedback specification, so it stayed as it was. One line of the prompt changed:

| | Prompt text |
|---|---|
| Before | Set overallConfidence honestly. Use low when evidence is thin. |
| After | overallConfidence is how sure you are that the score reflects what the student can actually do. It is not how sure you are of what you observed. Being certain that work is absent does not make the assessment certain: when there is little or no relevant work to judge, use low. |

## Results

| | Opus 5.5 before | Opus 5.5 after | Sonnet 5.5 before | Sonnet 5.5 after |
|---|---|---|---|---|
| Case runs passed | 15 of 18 | 18 of 18 | 15 of 18 | 17 of 18 |
| Checks passed | 195 of 198 | 198 of 198 | 195 of 198 | 197 of 198 |
| Confidence check on missing work | 0 of 3 | 3 of 3 | 0 of 3 | 3 of 3 |

### Scores and confidence across the three runs

| Case | Model | Before: points | Before: confidence | After: points | After: confidence |
|---|---|---|---|---|---|
| Valid alternative | Opus | 15, 15, 15 | high, medium, high | 15, 15, 15 | high, high, high |
| | Sonnet | 15, 15, 15 | medium, high, medium | 15, 15, 15 | high, high, high |
| Right output, wrong logic | Opus | 7, 7, 7 | medium, high, high | 7, 7, 7 | medium, medium, medium |
| | Sonnet | 6, 6, 6 | medium, medium, high | **4**, 6, 6 | high, high, high |
| Partial solution | Opus | 10, 10, 9 | medium, high, high | 10, 10, 10 | medium, medium, medium |
| | Sonnet | 10, 10, 10 | medium, medium, medium | 11, 11, 10 | high, high, medium |
| Missing submission | Opus | 0, 0, 0 | high, high, high | 0, 0, 0 | low, low, low |
| | Sonnet | 0, 0, 0 | high, high, high | 0, 0, 0 | low, low, low |
| Prompt injection | Opus | 4, 4, 4 | high, high, high | 4, 4, 4 | low, low, low |
| | Sonnet | 4, 4, 3 | high, high, high | 4, 4, 4 | high, high, high |
| Wrong assignment | Opus | 0, 0, 0 | high, high, high | 0, 0, 0 | low, low, low |
| | Sonnet | 0, 1, 3 | medium, high, high | 0, 0, 1 | low, low, low |

## What to take from it

**The targeted failure is gone.** Missing work now reports low confidence in all six runs.

**It did not collapse into "always say low."** Only the missing-submission case has a confidence cap, so a blanket drop would have passed every check. The table above is the check for that. Valid work stayed at high on both models.

**One new failure appeared.** In one Sonnet run, right output with wrong logic scored 4 against an expected range of 5 to 10. That run also reported high confidence and did not request teacher review, which makes it the most concerning result here: a low score, stated confidently, with no flag. Three repeats cannot show whether the prompt change caused it or whether it is ordinary variance.

**Side effects differ by model.** Opus dropped to low confidence on prompt injection, while Sonnet stayed high. Sonnet's confidence rose on several cases where it had been medium. A one-sentence change moved behavior beyond its target, which is why results are compared across every case.

## Held-out test

The case that exposed the failure was also used to verify the fix, so a second test used 5 new cases. Their expected results were written before any model ran them. Both prompts ran on both models, three times each, on the same assignment. The cases and raw outputs are in [`evals/results/2026-11-heldout/`](../evals/results/2026-11-heldout/).

| Case | What was submitted | Expected |
|---|---|---|
| Stub only | A function header with `pass`, and "I ran out of time" | 0 to 4 points, low confidence |
| Explanation only | An empty file and an accurate explanation of `return` | 0 to 3 points, teacher review, low confidence |
| Unreadable | Corrupted characters | 0 to 2 points, teacher review, low confidence |
| Complete, standard | A correct, conventional solution | 13 to 15 points |
| Short but complete | A correct two-line solution | 12 to 15 points |

The last two are controls. "Little or no relevant work" in the new wording could have been read as "short", so a very short correct answer tests for that.

| | Opus 5.5 old | Opus 5.5 new | Sonnet 5.5 old | Sonnet 5.5 new |
|---|---|---|---|---|
| Case runs passed | 9 of 15 | 15 of 15 | 6 of 15 | 15 of 15 |
| Confidence check on low-evidence cases | 3 of 9 | 9 of 9 | 0 of 9 | 9 of 9 |
| Control runs with full marks and high confidence | 6 of 6 | 6 of 6 | 4 of 6 | 6 of 6 |

The old prompt's failure was broader than the original case showed. A stub and an explanation without code drew medium or high confidence on both models, and Sonnet was also highly confident about unreadable text. With the new prompt, every low-evidence run reported low confidence, and every control run kept full marks.

These cases were written with the fix already known, so they test whether it holds across kinds of low-evidence work, not whether it holds across subjects or real student work. Now that they are public, they are no longer held out: the next prompt change needs new unseen cases.

## The API path

Every run above went through Claude Code on a subscription. Grading on submission runs unattended on a server, so it needs the API. The new prompt was then run through the API backend: all 11 cases, three repeats, on both models, at high effort. The raw outputs are in [`evals/results/2026-11-api/`](../evals/results/2026-11-api/).

| | Opus 5.5 | Sonnet 5.5 |
|---|---|---|
| Case runs passed | 33 of 33 | 33 of 33 |
| Errors (refusals, cut-off responses, invalid output, API errors) | 0 | 0 |
| Cost per graded submission | $0.028 | $0.011 |
| Median response time | 14.6 s | 7.6 s |
| Slowest response | 22.7 s | 10.6 s |
| Saved by prompt caching | 19% | 22% |
| Share of cost from output tokens | 91% | 88% |

A first 6-case check on Opus also passed 6 of 6. The 72 paid calls cost $1.50 in total. Costs use published rates per million tokens as of 2026-11: Opus 5.5 at $4 input and $20 output, Sonnet 5.5 at $2 and $10.

### Score consistency across the three runs

Cases not listed scored the same on every run.

| Case | Opus 5.5 | Sonnet 5.5 |
|---|---|---|
| Right output, wrong logic | 7, 6, 6 | 5, 5, 6 |
| Partial solution | 10, 10, 9 | 9, 10, 10 |
| Short but complete | 15, 15, 15 | 15, 14, 15 |

### What to take from it

**Both paths agree.** On the same prompt and the original six cases, the subscription runs passed 18 of 18 on Opus and 17 of 18 on Sonnet, and the API runs passed every case on both. The one subscription miss was a single Sonnet run on the wrong-logic case, which did not recur here. Variance between runs, not the path, is the likeliest explanation.

**Cost depends on output, not input.** Caching the system prompt works, but output tokens are about 90% of the cost. The levers that matter are the model and the effort level, not prompt length.

**Sonnet is the cost case, not yet the routing decision.** It costs about 40% as much as Opus and answers about twice as fast, with equal pass rates here. Eleven cases on one assignment are too few to route real grading by. [Rubric-first grading](rubric-first-grading.md) sets out how a question type moves to a cheaper model: per type, against thresholds set in advance.

**The fallback path was not exercised.** Refusal fallback is on, but no call was refused, so no fallback ran. The runner records which model answered every call, and the queue worker sends any result answered by a fallback model to the teacher. That handling is covered by tests, not by a real run.

### Rough cost at class scale

**These are assumptions, not measurements:** about 120 students answering 5 open-ended questions a week, every answer going to the model, at the measured cost per submission.

| | Per week | Per 36-week school year |
|---|---|---|
| Sonnet 5.5 | about $7 | about $240 |
| Opus 5.5 | about $17 | about $610 |

Deterministic checks per rubric criterion, as planned in [rubric-first-grading.md](rubric-first-grading.md), would lower both by keeping part of the grading away from the model.

## Limits

- All eleven cases so far use one assignment.
- Three repeats measure consistency only roughly.
- Only one prompt change and two models were tested.
- These checks are deterministic. They do not measure whether the feedback is useful to a student.

## Reproduce

```bash
python evals/run_model.py --model claude-opus-5-5 --out out/opus --repeats 3
python evals/run_evals.py --outputs out/opus/run-1

# Through the API, billed per token. Needs ANTHROPIC_API_KEY and: pip install anthropic
python evals/run_model.py --backend api --model claude-sonnet-5-5 --out out/sonnet-api --repeats 3
```

Model output varies between runs, so exact scores will differ.
