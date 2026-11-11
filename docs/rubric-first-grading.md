# Rubric-first grading on submission

**Status: designed, not built.** This document records the target design and the order it will be built in. Nothing here is deployed. See [grading-pipeline.md](grading-pipeline.md) for what runs today.

## The problem

Students learn from feedback they get while the work is still fresh. Today, open-ended answers wait for a weekly review, so feedback arrives days later.

The harder problem is maintainability. Every open-ended question needs a rubric, and rubrics have been the weakest link. In the current version, rubrics were assumed to exist that had never been written, and coding exercises went live without them. A design that depends on rubrics has to make a missing or weak rubric impossible to publish, not just discouraged.

## The decision in one paragraph

Every question that is not closed-form is graded against a rubric the moment it is submitted, and the student sees the rubric-based feedback right away. The score is shown as pending until the teacher approves it. Closed-form questions, and every rubric criterion that can be checked mechanically, are scored without AI. The AI scores only the criteria that need judgment. Rubrics are content: written with the question, built from templates, drafted by AI, approved and rated by the teacher, and checked by a publish gate.

## How a submission is graded

1. **Closed-form questions are scored by rules.** Multiple choice, matching, sorting, and any question with one correct answer or a small set of them never reach the AI.
2. **Every rubric criterion declares how it is checked.** A criterion is either `deterministic` or `judgment`. Deterministic criteria run as code. Judgment criteria go to the AI.
3. **The AI sees the deterministic results as facts.** As with program runs today, it may cite them but never contradict them.
4. **Feedback is released immediately. The score waits.** The student sees what they did well, the most important fix, and a next step. The score is marked pending until the teacher approves the batch.
5. **The release check still runs on every result** before anything is shown, and blocks results that cannot be right.

### What can be deterministic

The goal is to send as little as possible to the AI. Before a criterion is marked `judgment`, these checks are tried in order:

| Check | Example |
|---|---|
| Exact or normalized match | Ignore case, spacing, and trailing punctuation |
| Pattern match | A regular expression for an accepted answer form |
| Set membership | Any one of a short list of accepted answers |
| Numeric tolerance | 3.14 within 0.01 |
| Program behavior | Run the code on test inputs in the sandbox |
| Code structure | Parse the code and check that `add_points` contains a `return` |

Code structure checks matter most. A rubric line like "the function returns the new score" is a fact about the code, not a judgment, so it should not cost an AI call or depend on one.

## Rubrics as content

### One file per question

Each open-ended question carries, in the same file:

- the prompt and the skill it assesses
- the rubric, with each criterion's points, check type, and level descriptions
- a model answer
- common misconceptions, each with the feedback it should trigger
- generated test answers (see below)

### Templates by question type

Most rubrics follow a small number of shapes. Each question type gets a template, and a question adds only its own key points:

| Type | What the template already defines |
|---|---|
| Explain this code | Accuracy, use of the right terms, connection to this program |
| Predict the output and justify it | Correct prediction (deterministic), reasoning |
| Find and fix the bug | Bug identified, fix works (deterministic), explanation |
| Short written response | Claim, evidence, accuracy |

### Drafting, approval, and rating

1. The AI drafts a rubric from the question, the model answer, and the template.
2. The teacher approves or edits it. Nothing is published without approval.
3. The teacher rates each draft with a short rubric for rubrics: criteria are observable, match the skill, separate the score levels clearly, cover the known misconceptions, and produce feedback a student can act on.
4. High-rated rubrics become reference examples for future drafts. Low-rated ones show what the drafting step gets wrong.

The ratings are the path to drafting without review. Once drafts for a question type consistently rate high and pass their generated tests, that type can move to spot-checked approval instead of reviewing every draft.

### Generated test answers

Every question gets a set of synthetic answers: strong, partial, a common misconception, wrong, blank, and one that tries to instruct the grader. The teacher approves the expected result for each. These become that question's regression tests, run by the existing [eval harness](../evals/README.md), so a change to a rubric, the prompt, or the model is checked against every question's own cases.

### The publish gate

A question cannot be published unless:

- it has an approved rubric
- every criterion declares a check type
- every deterministic criterion has a working check
- its generated test answers exist, are approved, and pass

This is the gate that would have stopped the current version's failures, where questions went live without rubrics.

## Mastery Checks

Mastery Checks allow three attempts, averaged. Feedback between attempts has to coach without handing over the answer. The feedback rules gain one constraint for attempts 1 and 2: name the gap and point to the skill, but do not state the correct answer. A new eval check tests this on the generated cases.

## The path to full automation

Moving from "score pending" to "score released automatically" is decided by evidence, one question type and one model at a time.

For each question type, record how often the teacher approves the AI's score unchanged, across enough real submissions. Two numbers decide promotion:

- **Agreement rate:** how often the teacher accepts the score as it is.
- **High-confidence error rate:** how often a result marked high confidence was wrong. This is the dangerous one, because it is the result nobody looks at twice. Model comparison runs have already shown it happening: one run gave a confidently wrong score and asked for no review.

A question type is promoted to automatic scores only when both numbers clear thresholds set in advance, and is demoted automatically if they slip. Spot checks continue after promotion.

### Model routing

Promotion is decided per model. If Claude Sonnet 5.5 meets the thresholds for a question type, that type is graded by Sonnet, at about 40% of the cost of Claude Opus 5.5 in measured runs. Types where Sonnet falls short, or results Sonnet flags with low confidence, go to Opus.

## Build order

Students return only after each stage meets its exit criterion. The aim is to avoid releasing something that looks finished but breaks at scale.

| Stage | Exit criterion |
|---|---|
| 1. Question file format and templates | Every existing open-ended question converts to the format without loss |
| 2. Deterministic checks | Each check type has unit and mutation tests; a sample of real rubrics is classified by check type |
| 3. Publish gate | CI fails on any question missing a rubric, a check type, or approved test answers |
| 4. Generated test answers | Every question has an approved set, run by the eval harness |
| 5. Rubric drafting and rating | Drafts and ratings are recorded for at least one full unit |
| 6. On-submission pipeline | Feedback released and score held, end to end on synthetic submissions, with the release check |
| 7. Calibration tracking | Agreement and high-confidence error rates reported per question type and model |

## Costs and limits

- **The API is required.** Grading on submission runs unattended on a server, which the subscription path cannot do. Measured cost is about $0.01 per AI-graded answer on Sonnet 5.5 and $0.03 on Opus 5.5. Deterministic criteria cost nothing.
- **Feedback reaches students before the teacher sees it.** The release check limits the risk, but cannot catch feedback that is plausible and wrong. This is accepted because the score, which is what affects grades, still waits.
- **Rubric writing moves earlier.** Authoring a question now includes its rubric and test answers. The templates and drafting exist to keep that affordable.
- **Thresholds for promotion are not set yet.** They need real submissions to choose sensibly.
