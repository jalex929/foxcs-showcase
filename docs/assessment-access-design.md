# Assessment access: a daily class code that scales with self-paced learning

**Status: designed, implemented, and unit-tested. Not yet deployed to the live course.** The implementation is PHP inside the private operational workspace, because it talks to Moodle directly. This document is the design and its reasoning.

## The problem

Mastery Checks are short quizzes that close out each lesson. Students reach them at different times because the course is self-paced. The original gate was a quiz password, and I handed it to each student individually as they asked.

That worked for a handful of students. It does not work for a full class moving at its own pace. Every check meant a student waiting at my desk, me stopping whatever I was doing with someone else, and a lesson stalling for anyone who reached the check while I was busy. On a substitute's day it blocked the class entirely.

The goal: an access control that is still meaningful (you take the check in class, after doing the lesson), while scaling to any number of students and running without the teacher in the loop.

## Options considered

| Option | Teacher effort | Works on a sub day | Verdict |
|---|---|---|---|
| Static password, handed out on request | Once per student per check | No | The original. Doesn't scale |
| Request access, teacher clicks approve | Once per student per check | No | Same bottleneck with nicer UI |
| Per-student password sent by message on approval | Once per student per check | No | Same bottleneck |
| Lock-down exam browser | High setup, client install on every device | Depends | Tried earlier. Needed a client app installed on every device and never worked in practice, so it was turned off |
| No gate, enforce after the fact only | None | Yes | Too permissive on its own |
| **Daily class code + automatic gates** | **Once per class period** | **Yes, via sub plans** | **Chosen** |

The deciding question was not "is there a gate?" but **"does the effort grow with the number of students?"** Anything per-student fails that test.

## The design

### Daily class code

One code per course per day. I write it on the board once. Whoever is ready that day uses it.

```
code = HMAC-SHA256(server_secret, course | local school date)  ->  5 digits
       + (day number mod 10)                                    ->  1 digit
```

- **Computed, not stored.** The scheduled job that writes it into Moodle, the teacher's morning notification, and a printed week of codes for sub plans all compute the same value independently. There is nothing to sync and no table of codes to leak.
- **Yesterday's code fails today, by construction.** The final digit advances by one each day, so a code can never equal any code from the previous nine days, whatever the random part produces. With a purely random six digits, a repeat on consecutive days would be rare but possible. Here it is impossible, and a unit test checks the mechanism directly (the day digit steps by exactly one across a year boundary). A mutation test confirmed that removing the day digit is caught.
- **The day boundary is local midnight, not UTC.** For a US school, UTC midnight falls in the evening, which would change the code during after-school hours. Tested across the evening boundary and a daylight-saving change.
- **Digits only,** to avoid capital-letter and O-versus-0 mistakes on school laptops.
- **Different per course,** so one class's code does not open another's check.
- **Rotation is idempotent.** A job runs every ten minutes and sets each Mastery Check's code to today's value only if it is stale. A missed run is fixed by the next one.
- **Never interrupts a student mid-attempt.** The platform asks for the code when an attempt starts, so a student already working is unaffected when the date changes.
- **The secret stays on the server.** The scripts refuse to run if the secret file is readable by everyone or too short. No script logs the code itself.

### Gates that enforce themselves

The code proves presence in class today. These cover what a code alone cannot:

| Gate | Mechanism | Covers |
|---|---|---|
| School network | The quiz's native network-address restriction, with the range derived from the platform's own logs (the address blocks many different students use during school hours) rather than guessed | A code shared with someone at home |
| Lesson prerequisites | Native activity-completion restriction, shown to students ("Not available unless: *Floats: Instruction* is marked complete") | Taking the check before the lesson |
| Class-period check | Existing post-grading gate: attempts finished outside the student's own period get no credit | A code shared with a student in another period |
| Three attempts, averaged | Quiz settings | Guessing |

### Threat model, honestly

| Scenario | Stopped by |
|---|---|
| Student texts today's code to a friend at home | Network gate |
| Friend at school, different class period | Class-period check |
| Friend in the same period who skipped the lesson | Prerequisite gate (see limit below) |
| Student reuses yesterday's code | The code itself, guaranteed |
| Student guesses codes | Six digits, at school, in class, three averaged attempts. Not worth defending further |

Limits I state rather than hide:

- **The prerequisite gate is a pacing nudge.** Instruction pages use manual completion, so a determined student can mark one done without doing it. Integrity rests on the code, the network gate, and the period check.
- **The network gate cannot be lifted per student.** A student who genuinely needs to test off-site gets a separate, individually restricted copy of the check.
- **Make-ups in a different period** currently get zeroed by the period check, which has no excused-attempts list yet.
- **Shared network addresses.** If a district routes many schools through one address, the network gate means "any school in the district." The log analysis shows what the data actually supports.

## Facilitation

- **Teacher, daily:** one line on the board, from a morning notification.
- **Substitute:** a printed list of the next ten days' codes in the sub folder.
- **Student:** sees why a check is locked and what to do next, never needs to find the teacher.
- **Pacing:** a read-only readiness report shows, per student per check, locked or ready, which prerequisites are missing, attempts used, and averages.

## Safety in the rollout tooling

The setup script is built for a live system with real students in it:

- **Dry run by default.** It prints exactly what would change, per quiz, including the student-facing description, and writes nothing.
- **Refuses to write without a recent backup** of the platform.
- **Writes a rollback snapshot first,** with restricted permissions because it holds the old settings. A rollback mode restores it.
- **Refuses any prerequisite with completion tracking turned off,** because that condition could never be met and would lock every student out.
- **Keeps every access condition a teacher already set by hand.**
- **Re-running is a no-op.**
- **Sample course:** a working copy of the course uses one fixed, published code (`123456`), shown on every Mastery Check with a note that the real course changes it daily, so anyone can try the flow. The tooling refuses to put a fixed code on a live course, and the daily rotation skips sample courses.
- **Test mode:** the allowed network is a parameter, so I can add my home address in a sandbox course. That lets me test both the allowed and the blocked case from home before touching the live course.

## Testing

111 unit checks on the pure decision logic, run in CI on every change. The logic is kept free of platform dependencies so it is testable without a running Moodle. Coverage includes:

- Code determinism and its no-repeat guarantee, the local-midnight boundary, and per-course separation.
- Network-range parsing and matching for every format the platform accepts, with edge cases at range boundaries.
- School-network suggestion from synthetic log data, including excluding one student's home address.
- Prerequisite detection, including refusing untracked activities.
- Merging into existing access rules without dropping them, including nested OR rules, and idempotency.
- Intro text rewriting, secret-file permission checks, and rotation targeting.

The platform-facing scripts are syntax-checked in CI. **They have not yet been run against a live platform:** the first real run is a dry run in the sandbox course, following the test checklist above.

## What I would measure after rollout

- Time from "student ready" to "student starts the check," before and after.
- How often the network gate or period check fires. Frequent firing points to a configuration gap, not student behavior.
- Whether average Mastery Check scores change. A drop would suggest the old per-student hand-off was doubling as an informal readiness check, and that it should be replaced deliberately.
