"""Generates the synthetic sample submissions for the portfolio showcase.
Everything here is invented. No real student work, no licensed content."""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])

def page(title, body, telemetry=None):
    tel = ""
    if telemetry is not None:
        tel = ('\n<script type="application/json" id="foxcs-telemetry">'
               + json.dumps(telemetry, indent=2) + "</script>")
    return (f"<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            f"<title>{title}</title>{tel}\n</head>\n<body>\n{body}\n</body>\n</html>\n")

def ts(m, s=0):
    return f"2026-01-12T14:{m:02d}:{s:02d}.000Z"

def student_0001(d):
    d.mkdir(parents=True, exist_ok=True)
    (d / "01_instruction.html").write_text(page("Printing Output: Instruction",
        "<h1>Printing Output</h1>\n<p>Synthetic placeholder for the lesson's instruction page. Reading only, no save mechanism.</p>"))
    (d / "02_example_01.py").write_text('# View-only example file.\nprint("Hello, world!")\n')
    (d / "03_flashcards.html").write_text(page("Printing Output: Flashcards",
        "<p>Untouched flashcards page: no _completed suffix and no reviewed_at marker.</p>"))
    quiz_events = [
        {"type": "quiz_check", "t": ts(2), "attempt": 1, "attempted": [
            {"term": "function", "correct": True}, {"term": "argument", "correct": False},
            {"term": "output", "correct": False}, {"term": "string", "correct": True},
            {"term": "syntax", "correct": True}]},
        {"type": "quiz_check", "t": ts(3), "attempt": 2, "attempted": [
            {"term": t, "correct": True} for t in ["function", "argument", "output", "string", "syntax"]]},
    ]
    (d / "04_vocab_quiz_completed.html").write_text(page("Printing Output: Vocab Quiz",
        '<h1>Vocab Quiz</h1>\n<label for="memoryTrick">What memory trick did you use, if any?</label>\n'
        '<textarea id="memoryTrick">I mixed up argument and output at first. Now I think of the argument as what goes in the parentheses and the output as what shows up on screen.</textarea>',
        {"events": quiz_events}))
    first = {1: True, 2: True, 3: False, 4: True, 5: True, 6: False, 7: False, 8: False}
    drill_events = []
    m = 5
    for drill, ok in first.items():
        drill_events.append({"type": "drill_attempt", "t": ts(m), "drill_id": f"drill{drill}", "attempt": 1, "correct": ok})
        m += 1
        if not ok and drill != 7:  # drill 7 is optional preview content, not retried
            drill_events.append({"type": "drill_attempt", "t": ts(m), "drill_id": f"drill{drill}", "attempt": 2, "correct": True})
            m += 1
    (d / "05_practice_completed.html").write_text(page("Printing Output: Practice",
        "<h1>Practice</h1>\n<p>8 drills. Telemetry below is what the page records about itself.</p>", {"events": drill_events}))
    (d / "06_application.py").write_text(
        '# Application: answer each prompt with working code.\n\n'
        '# 1. Print your favorite food.\nprint("Tacos")\n\n'
        '# 2. Print two lines about your weekend.\nprint("I played soccer.")\nprint("I read a book.")\n\n'
        '# 3. Print a number and a string.\nprint(42)\nprint("forty-two")\n\n'
        '# 4. Print a greeting.\nPrint("Hi there!")  # bug left in on purpose: capital P\n\n'
        '# 5. Print a blank line between two messages.\nprint("Top")\nprint()\nprint("Bottom")\n')
    (d / "07_project.html").write_text(page("Printing Output: Project Checklist",
        '<h1>Project Checklist</h1>\n'
        '<label><input type="checkbox" checked> Required: three status lines</label>\n'
        '<label><input type="checkbox" checked> Required: one problem message</label>\n'
        '<label><input type="checkbox"> Bonus tier 1: formatted banner</label>\n'
        '<label><input type="checkbox"> Bonus tier 2: menu screen</label>'))
    (d / "08_project.py").write_text(
        '# Game status screen\nprint("Loading level 1...")\nprint("Health: 100")\nprint("Coins: 0")\n'
        'print("Something went wrong, try again.")  # present but generic\n')
    (d / "09_mastery_check_completed.html").write_text(page("Printing Output: Mastery Check",
        '<h1>Mastery Check</h1>\n<p>Synthetic placeholder. The real page is password-gated and its items are not published.</p>\n'
        f'<span id="unlockTime" hidden>unlocked_at:{ts(20, 0)}</span>\n'
        f'<span id="completeTime" hidden>completed_at:{ts(34, 36)}</span>'))
    (d / "10_mastery_check.py").write_text(
        '# Mastery check written answers (synthetic).\n'
        '# Q1: Fix the line so it runs.\nprint("Ready")\n'
        '# Q2: Name the mistake: the quote was not closed.\n'
        '# Q3: Name the mistake: something is wrong with the parentheses.\n'
        '# Q4: Explain: print() shows the argument on screen, it does not store it anywhere.\n')
    (d / "11_feedback_completed.html").write_text(page("Printing Output: Feedback",
        '<h1>Lesson Feedback</h1>\n<p>Rating: 4 of 5</p>\n<p>Still hard to explain: argument</p>'))

def student_0002(d):
    """A second, sparser submission: a misnamed vocab quiz and no mastery check."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "Vocab Quiz (1).html").write_text(page("Printing Output: Vocab Quiz",
        '<textarea id="memoryTrick">Rhymes.</textarea>',
        {"events": [{"type": "quiz_check", "t": ts(1), "attempt": 1,
                     "attempted": [{"term": "function", "correct": True}, {"term": "argument", "correct": True}]}]}))
    (d / "05_practice_completed.html").write_text(page("Printing Output: Practice", "<p>Saved without attempting any drills.</p>", {"events": []}))
    (d / "08_project.py").write_text('print("Level 1")\n')

student_0001(root / "STUDENT-0001_lesson_01_04_printing_output")
student_0002(root / "STUDENT-0002_lesson_01_04_printing_output")
print("ok")
