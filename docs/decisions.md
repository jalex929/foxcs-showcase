# Design decisions

The decisions that shape this system, each with what was rejected and what it costs. They're short on purpose: the linked docs have the detail.

| # | Decision | Instead of |
|---|---|---|
| 1 | Grade facts with code, judgment with AI, and release nothing without approval | One AI pass over everything |
| 2 | Hold any result that needs judgment, rather than guess | Always produce a score |
| 3 | Approve in batches, behind an automated release check | Reviewing every result by hand, or trusting high-confidence results |
| 4 | Standard library only, with one optional exception | A conventional dependency stack |
| 5 | Mutation testing as a CI gate | Treating 100% coverage as proof |
| 6 | Pin the prompt with snapshot tests and require structured output | Free-form prompts and parsing prose |
| 7 | A daily class code plus automatic gates | Handing out quiz passwords per student |
| 8 | Extract the showcase with fresh history and a leak scanner | Publishing the working repository |

## 1. Grade facts with code, judgment with AI, and release nothing without approval

**Context.** Grading has to fit in one hour a week, and the output reaches teenagers.
**Decision.** Deterministic code handles what can be verified: whether work was saved, whether a program runs, whether output matches. An AI reviewer handles what needs interpretation. A teacher approves everything before release.
**Rejected.** Sending everything to a model. Completion and run results are facts; asking a model to re-derive them adds cost and a new way to be wrong.
**Cost.** Two systems to maintain, and a hand-off between them. See [architecture.md](architecture.md).

## 2. Hold any result that needs judgment, rather than guess

**Context.** The coding rubric gives partial credit "depending on how close" the student got.
**Decision.** On-submission grading scores only the clear cases: it all works, or it doesn't run. Partial results, timeouts, invalid files, and missing rubrics get no automatic score and wait for review, with the reason attached.
**Rejected.** A formula for partial credit. It would be confidently wrong on exactly the cases that matter most.
**Cost.** Some results wait. Measuring how many is one of the first things to track after rollout. See [on-submission-grading.md](on-submission-grading.md).

## 3. Approve in batches, behind an automated release check

**Context.** In practice, the teacher reads rationale and grading trends and approves a batch at once. Nobody re-reads every grade.
**Decision.** Before any push, a release check examines every result. It blocks anything that cannot be right (rubric mismatches, bad arithmetic, evidence quoting code that isn't in the submission, grades contradicting the program run). It flags score/rationale disagreements for review, and draws a seeded spot-check sample to read in full.
**Rejected.** Claiming per-result review that doesn't happen, or trusting results the model marked high-confidence.
**Cost.** Structured output is required from every grading run, and wording checks are heuristics. See [release-check.md](release-check.md).

## 4. Standard library only, with one optional exception

**Context.** The grading machine allows no package installs.
**Decision.** Everything runs on a plain Python install, including the mutation tester and the leak scanner. The one exception is the model runner, which imports the official `anthropic` SDK only when it actually calls the API. Its tests use a fake client.
**Rejected.** Third-party test and mutation tools. They would be more familiar, but couldn't run where the code has to run.
**Cost.** Some tooling is hand-built and has to be tested itself. The mutation tester has its own test suite for that reason.

## 5. Mutation testing as a CI gate

**Context.** Several modules reached 100% line and branch coverage.
**Decision.** Every small change to the grading code (a flipped comparison, an inverted condition, a nudged number) must be caught by a test. Any exception is listed with a written reason, and CI fails on anything else.
**Rejected.** Treating coverage as the bar. At 100% coverage, the first mutation runs still found 91 untested behaviors across four modules, including a 1-point credit with no evidence that would have passed the release check.
**Cost.** About two minutes of CI per run, and occasional triage of equivalent mutants. See [testing.md](testing.md#mutation-testing).

## 6. Pin the prompt with snapshot tests and require structured output

**Context.** For an LLM feature, the prompt is code. A small wording change can change behavior.
**Decision.** The model returns JSON against a strict schema, in the same shape the release check reads. The exact system prompt and a sample user message are committed, and a test fails when they change, so prompt changes show up in review. A test also checks that the expected answers never reach the model.
**Rejected.** Parsing free-form text, and editing prompts without a diff.
**Cost.** Intended prompt changes need one extra command to update the snapshots. See [evals/README.md](../evals/README.md).

## 7. A daily class code plus automatic gates

**Context.** Handing quiz passwords to self-paced students one at a time didn't scale.
**Decision.** One code per class per day, computed from a server secret and the date, with automatic gates around it.
**Rejected.** Any gate whose effort grows with the number of students.
**Cost.** A shared code is possible. The other gates make it worth very little. See [assessment-access-design.md](assessment-access-design.md).

## 8. Extract the showcase with fresh history and a leak scanner

**Context.** The working repository holds licensed course material, assessment content, and operational detail.
**Decision.** This repository was extracted with fresh history. Every student in it is synthetic. CI fails if a vendor name, a real ID format, a district contact, live infrastructure, a credential, or an unexpected binary file appears.
**Rejected.** Cleaning up the working repository in place. Its history would still hold everything removed.
**Cost.** Changes are ported by hand from the working repository, and checked by the scanner and tests. See [privacy-and-data.md](privacy-and-data.md).
