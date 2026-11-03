# Engineering process

FoxCS is built by one person working with AI coding agents across many short sessions, each of which can start with no memory of the last. Most of the process below exists to make that reliable.

## Specification-driven development

Content and tooling are built against written specifications, not conversation memory. Eight canonical authoring specs govern how any lesson in any course is built: content standards, quality standards, the lesson schema, the authoring workflow, voice and tone, mastery-check standards, objectives and skill proficiency, and a catalogue of real interactive-content bugs with the rule that prevents each one. Grading follows its own feedback specification.

## An explicit authority hierarchy

Specs drift. Course-specific planning docs get written quickly and sometimes contradict the canonical ones. So the workspace has a written ranking of which source wins when two disagree:

1. The product owner's decision in the current session (then written into the docs before the session ends).
2. The canonical cross-course specifications.
3. The append-only decisions log.
4. Course-specific plans and maps.
5. Background and historical material.

A course-specific doc that conflicts with a canonical spec is the one that is wrong. Before building any new kind of module, the relevant canonical spec is checked even when a course doc already seems to answer the question.

### A real example

A course-level skills map placed one assessment at the unit level. The canonical mastery-check standard says every lesson gets its own check and there is no separate unit-level check. A build session followed the course doc without cross-checking and built four lessons with no per-lesson assessment. I caught it in review the next session, it was reversed, and the conflict went into a running **Known Tensions** list that every session reads before building. The list exists so the next session sees the trap before stepping in it, rather than having to rediscover it from a long log.

A second entry on that list is still open on purpose: two grading specs define overlapping bonus mechanisms that would double-count the same signal. Rather than guess, the bonus path is disabled until it is reconciled, and the eval harness rejects any output that applies a bonus.

## Logs that survive interrupted sessions

Three logs are updated continuously during a session, not at the end, because a session can be cut off at any moment:

- **Decisions log**: permanent, append-only record of what was decided and why, including discarded alternatives.
- **Work log**: technical state, including exactly what is deployed and what is mid-flight.
- **Conversation log**: questions asked and their answers, including questions still waiting on a reply.

Every session reads all three before starting work. This has recovered real interrupted work: a data migration that was killed mid-command was found by a later session, confirmed as never having executed, and re-run cleanly.

## Hard gates for things that went wrong once

When something reaches students that should not have, the fix includes a check that fails automatically next time, not just a note:

| Incident | Gate added |
|---|---|
| Five lesson pages went live still showing a "prototype" banner | Pre-publish checker fails on visible sandbox or prototype language, including the browser tab title |
| A set of practice questions went live as static text with no way to answer | Same checker fails on any response-eliciting block with no interactive control |
| Coding exercises went live with no rubric, forcing ad-hoc grading later | No coding exercise is published without a rubric file |
| Live content was at risk with no backups | Nightly backups, plus a required manual backup as the first step of any live edit |
| Handing quiz passwords to self-paced students one at a time did not scale | A daily class code, rotated automatically, plus gates that enforce themselves. See [assessment-access-design.md](assessment-access-design.md) |
| Licensed and private material in a public repository | This showcase was extracted with fresh history, and CI fails if such material appears |

## Ground truth over summaries

Summary tables lag reality. Before acting on a status claim, sessions check the live system directly (the actual database records for a course, the actual files deployed) rather than trusting a document that says what should be there.
