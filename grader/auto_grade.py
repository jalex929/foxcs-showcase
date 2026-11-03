#!/usr/bin/env python3
"""
FoxCS deterministic auto-grader (school-side tier).

Stdlib only: no pip installs, no network access, no AI. Designed to run on a
locked-down teacher machine from cmd or PowerShell. Reads submission folders
that are already keyed by a pseudonymous student ID (real identities never
reach this script, see docs/privacy-and-data.md) and produces two reports:

1. auto_grade_report.csv: completion and attempt facts for the content that
   is structurally gradable. Interactive HTML lesson pages save themselves
   with a "_completed" suffix and embed either a hidden foxcs-telemetry JSON
   block (vocab quiz, practice drills) or hidden timestamp spans (mastery
   check, flashcards). This script never judges quality. It only reads facts
   the page already recorded about itself: whether it was saved, how many
   attempts a quiz took, whether the first try was right, how long a mastery
   check was unlocked before being marked complete.
2. needs_review_manifest.csv: every file that needs a human or AI judgment
   call (project code, application code, written mastery-check answers).
   These are NOT graded here. They go to the AI-assisted review tier, and
   nothing reaches a student until the teacher approves it (the Release
   Gate, see docs/ai-reliability-and-boundaries.md).

Usage:
    python auto_grade.py <submissions_root> [--out-dir DIR]

<submissions_root> contains one folder per student-lesson submission, named
"<STUDENT_ID>_<lesson_dirname>", for example:

    submissions/
      STUDENT-0001_lesson_01_04_printing_output/
        04_vocab_quiz_completed.html
        05_practice_completed.html
        06_application.py
        ...

Writes auto_grade_report.csv and needs_review_manifest.csv into --out-dir
(default: the current directory).
"""

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# Files matching these suffixes carry a hidden foxcs-telemetry JSON block
# with quiz_check / drill_attempt events. Numeric filename prefixes vary by lesson, so match
# on suffix only.
TELEMETRY_SUFFIXES = {
    "vocab_quiz": "vocab_quiz_completed.html",
    "practice": "practice_completed.html",
}

# Mastery check doesn't carry a telemetry block: it carries two hidden
# timestamp spans instead (see the redesign note in that file's own header
# comment: unlock time and completion time, written automatically, nothing
# for the student to transcribe).
MASTERY_CHECK_SUFFIX = "mastery_check_completed.html"

# Feedback form: completion-only, never content-graded (it's the student's
# opinion of the lesson, not a rubric target).
FEEDBACK_SUFFIX = "feedback_completed.html"

# Flashcards: like mastery check, a hidden timestamp span rather than a telemetry block, since there's nothing to
# score but whether it happened: the button that writes it is itself gated
# in the page's own JS on flipping every card at least once, so by the time
# reviewed_at exists the "no XP for passive content" check has already been
# satisfied client-side.
FLASHCARDS_SUFFIX = "flashcards_completed.html"

TELEMETRY_BLOCK_RE = re.compile(
    r'<script type="application/json" id="foxcs-telemetry"[^>]*>(.*?)</script>',
    re.DOTALL,
)
UNLOCK_TIME_RE = re.compile(r'unlocked_at:([0-9T:.\-Z]+)')
COMPLETE_TIME_RE = re.compile(r'completed_at:([0-9T:.\-Z]+)')
REVIEWED_TIME_RE = re.compile(r'reviewed_at:([0-9T:.\-Z]+)')


def _parse_iso_timestamp(value):
    """Parses a JS `Date.toISOString()` value, with or without milliseconds.

    Real browser-generated timestamps always include milliseconds
    (e.g. "2026-08-20T22:41:36.123Z"); the original hand-typed sample
    fixture omitted them, which masked a real bug where this raised
    ValueError and silently produced an empty
    mastery_check_minutes_unlocked_to_complete for every real submission.
    """
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized timestamp format: {value!r}")
REFLECTION_TEXTAREA_RE = re.compile(
    r'<textarea[^>]*id="memoryTrick"[^>]*>(.*?)</textarea>', re.DOTALL
)

# Anything with one of these suffixes is a real submission artifact but is
# NOT auto-gradable here: it needs a human or AI-assisted judgment call.
# Labeled so the manifest tells the reviewer *why*, not just *that*.
NEEDS_REVIEW_PATTERNS = [
    (re.compile(r'project\.py$'), "project code: holistic rubric grading"),
    (re.compile(r'application\.py$'), "application code: holistic rubric grading"),
    (re.compile(r'mastery_check(_completed)?\.py$'), "mastery check written answers: grade against the lesson's teacher-only answer key"),
    (re.compile(r'example.*\.py$'), "view-only reference file: not a submission, skip"),
    (re.compile(r'project\.html$'), "project instructions/checklist: spot-check tier claims against the code"),
]

# Fuzzy fallback keyword sets, used ONLY when the exact
# expected filename isn't found. Real students will misname files (wrong
# convention, typo, missing "_completed"): the grader should still make its
# best attempt to identify what the file actually is rather than silently
# reporting "not submitted." All keywords (lowercase substrings) must appear
# in the filename for a fuzzy match. See find_file_fuzzy() and XP_TABLE below
# for how a fuzzy match gets flagged and lightly penalized rather than either
# ignored or treated as if nothing were wrong.
FUZZY_KEYWORDS = {
    "vocab_quiz": ["vocab", "quiz"],
    "practice": ["practice"],
    "mastery_check": ["mastery"],
    "feedback": ["feedback"],
    "flashcards": ["flashcard"],
}

# Baseline XP values. XP is a separate incentive layer from the completion columns
# above: awarded only when the activity shows genuine completion (not just
# "the file exists"), never awarded for an untouched template.
XP_TABLE = {
    "vocab_quiz": 5,
    "practice": 8,
    "mastery_check": 20,
    "feedback": 3,
    "flashcards": 3,
}
NAMING_PENALTY_XP = 2  # flat deduction when the file had to be fuzzy-matched
MIN_XP_AFTER_PENALTY = 1  # a naming slip never zeroes out otherwise-genuine work


def parse_submission_dirname(dirname):
    """'STUDENT-0001_lesson_01_04_printing_output' -> ('STUDENT-0001', 'lesson_01_04_printing_output')."""
    if "_" not in dirname:
        return dirname, ""
    student_id, _, lesson = dirname.partition("_")
    return student_id, lesson


def find_file(folder: Path, suffix: str):
    matches = sorted(folder.glob(f"*{suffix}"))
    return matches[0] if matches else None


def find_file_fuzzy(folder: Path, exact_suffix: str, fuzzy_type: str, extension: str = ".html"):
    """Returns (path, naming_issue). Tries the exact expected suffix first
    (naming_issue False, no penalty). If that's not found, falls back to any
    file of the given extension whose lowercased name contains every fuzzy
    keyword for this type (naming_issue True): a best-effort identification
    of a misnamed submission, not a silent "not submitted." Returns
    (None, False) if nothing matches at all, exact or fuzzy.

    Deliberately does not try to distinguish a genuine misnamed submission
    from an accidentally-matched untouched template: the completion-quality
    checks each grade_*() function already runs (attempts, all-correct,
    non-empty reflection, unlock/complete timestamps) naturally score an
    untouched template as 0 XP regardless, so no extra logic is needed here
    to guard against that case.
    """
    exact = find_file(folder, exact_suffix)
    if exact:
        return exact, False
    keywords = FUZZY_KEYWORDS.get(fuzzy_type, [])
    if not keywords:
        return None, False
    candidates = sorted(p for p in folder.glob(f"*{extension}") if p.is_file())
    for p in candidates:
        name_lower = p.name.lower()
        if all(k in name_lower for k in keywords):
            return p, True
    return None, False


def _apply_naming_penalty(xp: int, naming_issue: bool) -> int:
    if xp and naming_issue:
        return max(MIN_XP_AFTER_PENALTY, xp - NAMING_PENALTY_XP)
    return xp


def score_telemetry_file(path: Path, event_type: str):
    """Returns (saved, attempt_count, first_try_all_correct, last_attempt_all_correct)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    m = TELEMETRY_BLOCK_RE.search(text)
    if not m:
        return True, 0, None, None
    try:
        telemetry = json.loads(m.group(1))
    except json.JSONDecodeError:
        return True, 0, None, None
    events = [e for e in telemetry.get("events", []) if e.get("type") == event_type]
    if not events:
        return True, 0, None, None
    first_attempt = events[0]
    last_attempt = events[-1]

    def all_correct(evt):
        attempted = evt.get("attempted", [])
        if not attempted:
            return None
        return all(a.get("correct") for a in attempted)

    return True, len(events), all_correct(first_attempt), all_correct(last_attempt)


def grade_vocab_quiz(folder: Path, row: dict):
    path, naming_issue = find_file_fuzzy(folder, TELEMETRY_SUFFIXES["vocab_quiz"], "vocab_quiz")
    if not path:
        row["vocab_quiz_saved"] = "N"
        row["vocab_quiz_xp_awarded"] = 0
        row["vocab_quiz_naming_issue"] = ""
        return
    saved, attempts, first_ok, last_ok = score_telemetry_file(path, "quiz_check")
    text = path.read_text(encoding="utf-8", errors="replace")
    reflection_m = REFLECTION_TEXTAREA_RE.search(text)
    reflection_text = re.sub(r"<[^>]+>", "", reflection_m.group(1)).strip() if reflection_m else ""
    row["vocab_quiz_saved"] = "Y" if saved else "N"
    row["vocab_quiz_check_attempts"] = attempts
    row["vocab_quiz_first_attempt_all_correct"] = "" if first_ok is None else ("Y" if first_ok else "N")
    row["vocab_quiz_all_correct_by_save"] = "" if last_ok is None else ("Y" if last_ok else "N")
    row["vocab_quiz_reflection_word_count"] = len(reflection_text.split())
    row["vocab_quiz_reflection_text"] = reflection_text
    row["vocab_quiz_naming_issue"] = f"Saved as '{path.name}', not the expected filename: please use the correct naming convention next time." if naming_issue else ""
    earned = bool(last_ok) and len(reflection_text.split()) > 0
    row["vocab_quiz_xp_awarded"] = _apply_naming_penalty(XP_TABLE["vocab_quiz"] if earned else 0, naming_issue)


def grade_practice(folder: Path, row: dict):
    path, naming_issue = find_file_fuzzy(folder, TELEMETRY_SUFFIXES["practice"], "practice")
    if not path:
        row["practice_saved"] = "N"
        row["practice_xp_awarded"] = 0
        row["practice_naming_issue"] = ""
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    row["practice_naming_issue"] = f"Saved as '{path.name}', not the expected filename: please use the correct naming convention next time." if naming_issue else ""
    m = TELEMETRY_BLOCK_RE.search(text)
    row["practice_saved"] = "Y"
    if not m:
        row["practice_check_events"] = 0
        row["practice_xp_awarded"] = 0
        return
    try:
        telemetry = json.loads(m.group(1))
    except json.JSONDecodeError:
        row["practice_check_events"] = 0
        row["practice_xp_awarded"] = 0
        return
    events = telemetry.get("events", [])
    row["practice_check_events"] = len(events)
    # Attempts are keyed per-drill; a drill "passes first try" if its
    # earliest recorded check for that drill id was fully correct.
    first_seen = {}
    for evt in events:
        drill_id = evt.get("drill_id") or evt.get("drill") or evt.get("id")
        if drill_id is None:
            continue
        first_seen.setdefault(drill_id, evt)
    drills_seen = len(first_seen)
    first_try_correct = sum(
        1 for evt in first_seen.values() if evt.get("correct") is True
    )
    row["practice_drills_attempted"] = drills_seen
    row["practice_first_try_correct_count"] = first_try_correct
    # XP rewards genuine attempt/completion, not perfection -- practice save
    # is deliberately not gated on getting every drill right (see
    # the lesson design), so XP shouldn't be either.
    earned = drills_seen > 0
    row["practice_xp_awarded"] = _apply_naming_penalty(XP_TABLE["practice"] if earned else 0, naming_issue)


def grade_mastery_check(folder: Path, row: dict):
    path, naming_issue = find_file_fuzzy(folder, MASTERY_CHECK_SUFFIX, "mastery_check")
    if not path:
        row["mastery_check_saved"] = "N"
        row["mastery_check_xp_awarded"] = 0
        row["mastery_check_naming_issue"] = ""
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    unlock_m = UNLOCK_TIME_RE.search(text)
    complete_m = COMPLETE_TIME_RE.search(text)
    row["mastery_check_saved"] = "Y"
    row["mastery_check_unlocked_at"] = unlock_m.group(1) if unlock_m else ""
    row["mastery_check_completed_at"] = complete_m.group(1) if complete_m else ""
    row["mastery_check_naming_issue"] = f"Saved as '{path.name}', not the expected filename: please use the correct naming convention next time." if naming_issue else ""
    if unlock_m and complete_m:
        try:
            delta = _parse_iso_timestamp(complete_m.group(1)) - _parse_iso_timestamp(unlock_m.group(1))
            row["mastery_check_minutes_unlocked_to_complete"] = round(delta.total_seconds() / 60, 1)
        except ValueError:
            row["mastery_check_minutes_unlocked_to_complete"] = ""
    earned = bool(unlock_m) and bool(complete_m)
    row["mastery_check_xp_awarded"] = _apply_naming_penalty(XP_TABLE["mastery_check"] if earned else 0, naming_issue)


def grade_feedback(folder: Path, row: dict):
    path, naming_issue = find_file_fuzzy(folder, FEEDBACK_SUFFIX, "feedback")
    row["feedback_saved"] = "Y" if path else "N"
    row["feedback_naming_issue"] = f"Saved as '{path.name}', not the expected filename: please use the correct naming convention next time." if (path and naming_issue) else ""
    row["feedback_xp_awarded"] = _apply_naming_penalty(XP_TABLE["feedback"] if path else 0, naming_issue)


def grade_flashcards(folder: Path, row: dict):
    path, naming_issue = find_file_fuzzy(folder, FLASHCARDS_SUFFIX, "flashcards")
    if not path:
        row["flashcards_saved"] = "N"
        row["flashcards_xp_awarded"] = 0
        row["flashcards_naming_issue"] = ""
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    reviewed_m = REVIEWED_TIME_RE.search(text)
    row["flashcards_saved"] = "Y"
    row["flashcards_reviewed_at"] = reviewed_m.group(1) if reviewed_m else ""
    row["flashcards_naming_issue"] = f"Saved as '{path.name}', not the expected filename: please use the correct naming convention next time." if naming_issue else ""
    earned = bool(reviewed_m)
    row["flashcards_xp_awarded"] = _apply_naming_penalty(XP_TABLE["flashcards"] if earned else 0, naming_issue)


def collect_needs_review(folder: Path, student_id: str, lesson: str, manifest_rows: list):
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        for pattern, reason in NEEDS_REVIEW_PATTERNS:
            if pattern.search(path.name):
                manifest_rows.append({
                    "student_id": student_id,
                    "lesson": lesson,
                    "file": path.name,
                    "reason": reason,
                })
                break


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("submissions_root", help="Folder containing one subfolder per <STUDENT_ID>_<lesson_dirname> submission")
    parser.add_argument("--out-dir", default=".", help="Where to write the two CSV reports (default: current directory)")
    args = parser.parse_args()

    root = Path(args.submissions_root)
    if not root.is_dir():
        print(f"Not a folder: {root}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report_rows = []
    manifest_rows = []

    submission_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    if not submission_dirs:
        print(f"No submission folders found directly inside {root}.", file=sys.stderr)
        sys.exit(1)

    for folder in submission_dirs:
        student_id, lesson = parse_submission_dirname(folder.name)
        row = {"student_id": student_id, "lesson": lesson}
        grade_vocab_quiz(folder, row)
        grade_practice(folder, row)
        grade_mastery_check(folder, row)
        grade_feedback(folder, row)
        grade_flashcards(folder, row)
        row["total_xp_awarded"] = (
            row.get("vocab_quiz_xp_awarded", 0)
            + row.get("practice_xp_awarded", 0)
            + row.get("mastery_check_xp_awarded", 0)
            + row.get("feedback_xp_awarded", 0)
            + row.get("flashcards_xp_awarded", 0)
        )
        report_rows.append(row)
        collect_needs_review(folder, student_id, lesson, manifest_rows)

    report_fields = [
        "student_id", "lesson",
        "vocab_quiz_saved", "vocab_quiz_check_attempts",
        "vocab_quiz_first_attempt_all_correct", "vocab_quiz_all_correct_by_save",
        "vocab_quiz_reflection_word_count", "vocab_quiz_reflection_text",
        "vocab_quiz_xp_awarded", "vocab_quiz_naming_issue",
        "practice_saved", "practice_check_events",
        "practice_drills_attempted", "practice_first_try_correct_count",
        "practice_xp_awarded", "practice_naming_issue",
        "mastery_check_saved", "mastery_check_unlocked_at",
        "mastery_check_completed_at", "mastery_check_minutes_unlocked_to_complete",
        "mastery_check_xp_awarded", "mastery_check_naming_issue",
        "feedback_saved", "feedback_xp_awarded", "feedback_naming_issue",
        "flashcards_saved", "flashcards_reviewed_at",
        "flashcards_xp_awarded", "flashcards_naming_issue",
        "total_xp_awarded",
    ]
    report_path = out_dir / "auto_grade_report.csv"
    with report_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=report_fields, extrasaction="ignore")
        writer.writeheader()
        for row in report_rows:
            writer.writerow(row)

    manifest_path = out_dir / "needs_review_manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["student_id", "lesson", "file", "reason"])
        writer.writeheader()
        for row in manifest_rows:
            writer.writerow(row)

    naming_issue_count = sum(
        1 for row in report_rows
        for key in ("vocab_quiz_naming_issue", "practice_naming_issue", "mastery_check_naming_issue", "feedback_naming_issue", "flashcards_naming_issue")
        if row.get(key)
    )

    print(f"Graded {len(report_rows)} submission(s).")
    print(f"  Auto-grade report: {report_path}")
    print(f"  Needs-review manifest ({len(manifest_rows)} file(s)): {manifest_path}")
    print("  Needs-review files were NOT graded. Route them (and this manifest) to the")
    print("  AI-assisted review tier. Nothing generated by AI reaches a student without")
    print("  teacher approval (the Release Gate).")
    if naming_issue_count:
        print(f"  {naming_issue_count} file(s) were fuzzy-matched due to a naming issue: see the")
        print("  *_naming_issue columns in the report. A small XP penalty was already applied;")
        print("  no other action needed unless you want to remind the student directly.")
    print("  XP values are configurable: adjust XP_TABLE in this script if needed.")


if __name__ == "__main__":
    main()
