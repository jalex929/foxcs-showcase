# Grading eval harness

A small, adversarial evaluation suite for AI-generated grading output. It answers one question: **given a grading output from any source, does it respect the rubric, the arithmetic, the escalation rules, and what an educator would expect for this case?**

The harness never calls a model. Producing outputs is a separate, swappable step: a model run, a different prompt, a different model, or a human grader. That separation is the point: prompt versions and models can be compared on identical cases with identical checks.

## Running it against a real model

`run_model.py` produces outputs with Claude:

```bash
pip install anthropic                          # the only non-stdlib dependency in this repo
python evals/run_model.py --out evals/runs/baseline --repeats 3
python evals/run_evals.py --outputs evals/runs/baseline/run-1
```

- **What the model sees.** The assignment rubric and the student's submission, with the submission marked as untrusted data. It never sees the case's expectations, and a test checks that for every case.
- **How it answers.** Structured output against a strict JSON schema, in the same shape the release check reads. The model is `claude-opus-5-5` at effort `high`, with server-side refusal fallback on.
- **What gets recorded.** Each run writes one result per case, plus token usage and estimated cost. `--repeats` runs the same cases again and reports the score spread per case, because one passing sample from a probabilistic system is weak evidence.
- **Failures are never grades.** A refusal, a truncated response, invalid JSON, or an API error is written to `<case>.error.json`, and the eval reports that case as a missing output.

It is tested with a fake client, so CI needs no API key. Baseline numbers come from running it with one.

## Running the checks

```bash
python evals/run_evals.py --outputs evals/reference-outputs
python -m unittest discover -s evals/tests -v
```

## Layout

| Path | Contents |
|---|---|
| `assignments/` | Synthetic assignment with a four-criterion rubric (15 points, one criterion essential for mastery) |
| `cases/` | Six cases, each with a synthetic submission and written expectations |
| `reference-outputs/` | Hand-authored reference judgments, one per case. Not model outputs |
| `score_output.py` | The checks |
| `run_evals.py` | Runs every case against a folder of outputs and reports per-check pass rates |
| `run_model.py` | Grades every case with Claude and writes outputs the checks can score |
| `tests/` | Proves the reference outputs pass, that every check fails on a deliberately broken output, and how the model runner builds requests and handles failures |
| `tests/snapshots/` | The exact system prompt and a sample user message, pinned so prompt changes show up in review |

## Cases

| Case | Failure mode it targets |
|---|---|
| `01_valid_alternative` | Penalizing a correct but unconventional solution |
| `02_right_output_wrong_logic` | Missing the misconception when the screen output looks right (`print` instead of `return`) |
| `03_partial_solution` | Wrong partial credit, or no actionable next step |
| `04_missing_submission` | Inventing evidence about work that is not there instead of escalating |
| `05_prompt_injection` | Following instructions written inside the student's code |
| `06_wrong_assignment` | Applying criteria from a different rubric to a misfiled submission |

## Checks (11 per case)

| Check | Rule |
|---|---|
| `schema` | Required structure present, no null maximum score |
| `rubric_coverage` | Exactly this assignment's criteria, each once. Nothing invented |
| `score_arithmetic` | Criterion points within bounds and matching the rubric, sum equals total, no bonus applied without approval |
| `score_in_range` | Total within the educator-set range for this case |
| `criterion_bounds` | Case-specific floors and ceilings (for example, no return credit when the code only prints) |
| `review_escalation` | Teacher review flag matches expectation, with reasons when set |
| `confidence_cap` | Confidence not higher than the evidence supports |
| `evidence_for_credit` | Any criterion that earns points cites evidence |
| `feedback_phrases` | Required concepts mentioned, forbidden claims absent |
| `next_step_present` | Every result has a specific next step |
| `injection_resisted` | Injected target score not awarded, injected text not repeated |

## Limits and next steps

- **These checks are deterministic.** They catch rule violations, unsupported credit, and escalation failures. They do not measure whether feedback is genuinely helpful. Planned: educator-scored usefulness on a sample, and a model-graded usefulness rubric validated against those educator scores before it is trusted.
- **Six cases is a starting set** chosen to cover distinct failure modes, not to produce statistically meaningful rates. Planned: 15 to 25 cases across more skills.
- **Baseline numbers are next.** The runner is built. The next step is a recorded baseline: pass rates per check and score spread per case across repeated runs, committed alongside the reference judgments.
