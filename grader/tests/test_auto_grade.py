"""
Tests for auto_grade.py. stdlib unittest only, same constraint as the script.

Run from the repository root with:
    python -m unittest discover -s grader/tests -v

Two kinds of coverage on purpose:
- Unit tests build small synthetic HTML fixtures in a temp dir so each
  extraction rule (telemetry JSON, reflection textarea, hidden timestamps,
  needs-review classification) is checked in isolation, including edge
  cases real submissions hit (missing file, malformed JSON, missing
  telemetry block, misnamed files, browser timestamps with milliseconds).
- One end-to-end test runs the grader against the synthetic fixture in
  ../sample-submissions/STUDENT-0001_lesson_01_04_printing_output/ and
  asserts the exact values documented in that fixture's persona (see
  ../sample-submissions/README.md): 2 vocab-quiz check attempts, first
  attempt NOT all correct, 8 practice drills with 4 correct on the first
  try, a 14.6-minute mastery check window.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import auto_grade

REAL_FIXTURE = Path(__file__).resolve().parents[1] / "sample-submissions" / "STUDENT-0001_lesson_01_04_printing_output"


def write(folder: Path, name: str, content: str) -> Path:
    path = folder / name
    path.write_text(content, encoding="utf-8")
    return path


class TestParseSubmissionDirname(unittest.TestCase):
    def test_normal_case(self):
        student_id, lesson = auto_grade.parse_submission_dirname(
            "STUDENT-0001_lesson_01_04_printing_output"
        )
        self.assertEqual(student_id, "STUDENT-0001")
        self.assertEqual(lesson, "lesson_01_04_printing_output")

    def test_no_underscore_does_not_crash(self):
        student_id, lesson = auto_grade.parse_submission_dirname("nolessonhere")
        self.assertEqual(student_id, "nolessonhere")
        self.assertEqual(lesson, "")


class TestScoreTelemetryFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _telemetry_html(self, events):
        block = json.dumps({"events": events})
        return (
            '<html><head><script type="application/json" id="foxcs-telemetry">'
            f"{block}</script></head><body></body></html>"
        )

    def test_first_and_last_attempt_correctness(self):
        events = [
            {"type": "quiz_check", "attempted": [{"correct": True}, {"correct": False}]},
            {"type": "quiz_check", "attempted": [{"correct": True}, {"correct": True}]},
        ]
        path = write(self.folder, "x.html", self._telemetry_html(events))
        saved, attempts, first_ok, last_ok = auto_grade.score_telemetry_file(path, "quiz_check")
        self.assertTrue(saved)
        self.assertEqual(attempts, 2)
        self.assertFalse(first_ok)
        self.assertTrue(last_ok)

    def test_missing_telemetry_block(self):
        path = write(self.folder, "x.html", "<html><body>no telemetry here</body></html>")
        saved, attempts, first_ok, last_ok = auto_grade.score_telemetry_file(path, "quiz_check")
        self.assertTrue(saved)
        self.assertEqual(attempts, 0)
        self.assertIsNone(first_ok)
        self.assertIsNone(last_ok)

    def test_malformed_json_does_not_crash(self):
        html = '<script type="application/json" id="foxcs-telemetry">{not valid json</script>'
        path = write(self.folder, "x.html", html)
        saved, attempts, first_ok, last_ok = auto_grade.score_telemetry_file(path, "quiz_check")
        self.assertTrue(saved)
        self.assertEqual(attempts, 0)
        self.assertIsNone(first_ok)
        self.assertIsNone(last_ok)

    def test_no_matching_event_type(self):
        events = [{"type": "drill_attempt", "attempted": [{"correct": True}]}]
        path = write(self.folder, "x.html", self._telemetry_html(events))
        _saved, attempts, first_ok, _last_ok = auto_grade.score_telemetry_file(path, "quiz_check")
        self.assertEqual(attempts, 0)
        self.assertIsNone(first_ok)


class TestGradeVocabQuiz(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_not_saved(self):
        row = {}
        auto_grade.grade_vocab_quiz(self.folder, row)
        self.assertEqual(row["vocab_quiz_saved"], "N")

    def test_saved_with_reflection(self):
        events = [{"type": "quiz_check", "attempted": [{"correct": True}] * 5}]
        html = (
            '<html><head><script type="application/json" id="foxcs-telemetry">'
            f'{json.dumps({"events": events})}</script></head><body>'
            '<textarea id="memoryTrick">I used a rhyme to remember it</textarea>'
            "</body></html>"
        )
        write(self.folder, "04_vocab_quiz_completed.html", html)
        row = {}
        auto_grade.grade_vocab_quiz(self.folder, row)
        self.assertEqual(row["vocab_quiz_saved"], "Y")
        self.assertEqual(row["vocab_quiz_check_attempts"], 1)
        self.assertEqual(row["vocab_quiz_first_attempt_all_correct"], "Y")
        self.assertEqual(row["vocab_quiz_reflection_word_count"], 7)
        self.assertIn("rhyme", row["vocab_quiz_reflection_text"])

    def test_empty_reflection_counts_zero_words(self):
        html = (
            '<html><body><textarea id="memoryTrick">   </textarea></body></html>'
        )
        write(self.folder, "04_vocab_quiz_completed.html", html)
        row = {}
        auto_grade.grade_vocab_quiz(self.folder, row)
        self.assertEqual(row["vocab_quiz_reflection_word_count"], 0)


class TestGradePractice(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_try_correct_count(self):
        events = [
            {"type": "drill_attempt", "drill_id": "drill1", "correct": True},
            {"type": "drill_attempt", "drill_id": "drill2", "correct": False},
            {"type": "drill_attempt", "drill_id": "drill2", "correct": True},
            {"type": "drill_attempt", "drill_id": "drill3", "correct": True},
        ]
        html = (
            '<html><head><script type="application/json" id="foxcs-telemetry">'
            f'{json.dumps({"events": events})}</script></head><body></body></html>'
        )
        write(self.folder, "05_practice_completed.html", html)
        row = {}
        auto_grade.grade_practice(self.folder, row)
        self.assertEqual(row["practice_saved"], "Y")
        self.assertEqual(row["practice_drills_attempted"], 3)
        # drill1 and drill3 correct on their first recorded attempt; drill2 was not.
        self.assertEqual(row["practice_first_try_correct_count"], 2)

    def test_not_saved(self):
        row = {}
        auto_grade.grade_practice(self.folder, row)
        self.assertEqual(row["practice_saved"], "N")


class TestGradeMasteryCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_minutes_computed_with_milliseconds(self):
        # Real browser-generated new Date().toISOString() values always
        # include milliseconds: this is the format that actually reaches
        # the grader in practice, not the millisecond-free format used
        # elsewhere in this file for readability. Regression test for a
        # real bug the Lesson 01.1 rebuild found: the original regex/strptime
        # silently dropped this field to "" for every real submission.
        html = (
            '<span id="unlockTime">unlocked_at:2026-08-20T22:41:36.123Z</span>'
            '<span id="completeTime">completed_at:2026-08-20T22:41:45.987Z</span>'
        )
        write(self.folder, "09_mastery_check_completed.html", html)
        row = {}
        auto_grade.grade_mastery_check(self.folder, row)
        self.assertEqual(row["mastery_check_saved"], "Y")
        self.assertEqual(row["mastery_check_minutes_unlocked_to_complete"], round(9.864 / 60, 1))

    def test_minutes_computed(self):
        html = (
            '<span id="unlockTime">unlocked_at:2026-08-11T15:10:03Z</span>'
            '<span id="completeTime">completed_at:2026-08-11T15:24:41Z</span>'
        )
        write(self.folder, "09_mastery_check_completed.html", html)
        row = {}
        auto_grade.grade_mastery_check(self.folder, row)
        self.assertEqual(row["mastery_check_saved"], "Y")
        self.assertEqual(row["mastery_check_minutes_unlocked_to_complete"], 14.6)

    def test_unlocked_but_not_completed(self):
        html = '<span id="unlockTime">unlocked_at:2026-08-11T15:10:03Z</span>'
        write(self.folder, "09_mastery_check_completed.html", html)
        row = {}
        auto_grade.grade_mastery_check(self.folder, row)
        self.assertEqual(row["mastery_check_completed_at"], "")
        self.assertNotIn("mastery_check_minutes_unlocked_to_complete", row)

    def test_not_saved(self):
        row = {}
        auto_grade.grade_mastery_check(self.folder, row)
        self.assertEqual(row["mastery_check_saved"], "N")


class TestGradeFeedback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_saved_and_not_saved(self):
        row = {}
        auto_grade.grade_feedback(self.folder, row)
        self.assertEqual(row["feedback_saved"], "N")

        write(self.folder, "11_feedback_completed.html", "<html></html>")
        row2 = {}
        auto_grade.grade_feedback(self.folder, row2)
        self.assertEqual(row2["feedback_saved"], "Y")


class TestGradeFlashcards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_not_saved(self):
        row = {}
        auto_grade.grade_flashcards(self.folder, row)
        self.assertEqual(row["flashcards_saved"], "N")
        self.assertEqual(row["flashcards_xp_awarded"], 0)

    def test_saved_with_reviewed_timestamp(self):
        html = '<span id="reviewedTime">reviewed_at:2026-08-22T14:05:00.000Z</span>'
        write(self.folder, "02_flashcards_completed.html", html)
        row = {}
        auto_grade.grade_flashcards(self.folder, row)
        self.assertEqual(row["flashcards_saved"], "Y")
        self.assertEqual(row["flashcards_reviewed_at"], "2026-08-22T14:05:00.000Z")
        self.assertEqual(row["flashcards_xp_awarded"], auto_grade.XP_TABLE["flashcards"])

    def test_saved_file_missing_reviewed_marker_earns_no_xp(self):
        # An untouched template accidentally saved with the _completed
        # suffix (or a page that got interrupted before the button fired)
        # shouldn't earn XP just for existing.
        write(self.folder, "02_flashcards_completed.html", "<html></html>")
        row = {}
        auto_grade.grade_flashcards(self.folder, row)
        self.assertEqual(row["flashcards_saved"], "Y")
        self.assertEqual(row["flashcards_xp_awarded"], 0)


class TestFindFileFuzzy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_exact_match_no_naming_issue(self):
        write(self.folder, "04_vocab_quiz_completed.html", "x")
        path, naming_issue = auto_grade.find_file_fuzzy(self.folder, "vocab_quiz_completed.html", "vocab_quiz")
        self.assertEqual(path.name, "04_vocab_quiz_completed.html")
        self.assertFalse(naming_issue)

    def test_fuzzy_match_flags_naming_issue(self):
        # Real scenario: a student saves under a name that doesn't match the
        # expected convention at all, the grader should still find it.
        write(self.folder, "VocabQuizFinal.html", "x")
        path, naming_issue = auto_grade.find_file_fuzzy(self.folder, "vocab_quiz_completed.html", "vocab_quiz")
        self.assertEqual(path.name, "VocabQuizFinal.html")
        self.assertTrue(naming_issue)

    def test_no_match_at_all(self):
        write(self.folder, "01_instruction.html", "x")
        path, naming_issue = auto_grade.find_file_fuzzy(self.folder, "vocab_quiz_completed.html", "vocab_quiz")
        self.assertIsNone(path)
        self.assertFalse(naming_issue)

    def test_flashcards_fuzzy_match(self):
        write(self.folder, "MyFlashcardsDone.html", "x")
        path, naming_issue = auto_grade.find_file_fuzzy(self.folder, "flashcards_completed.html", "flashcards")
        self.assertEqual(path.name, "MyFlashcardsDone.html")
        self.assertTrue(naming_issue)


class TestXPAwards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _vocab_quiz_html(self, reflection="A real reflection here."):
        events = [{"type": "quiz_check", "attempted": [{"correct": True}] * 5}]
        return (
            '<html><head><script type="application/json" id="foxcs-telemetry">'
            f'{json.dumps({"events": events})}</script></head><body>'
            f'<textarea id="memoryTrick">{reflection}</textarea>'
            "</body></html>"
        )

    def test_vocab_quiz_full_xp_when_genuinely_complete(self):
        write(self.folder, "04_vocab_quiz_completed.html", self._vocab_quiz_html())
        row = {}
        auto_grade.grade_vocab_quiz(self.folder, row)
        self.assertEqual(row["vocab_quiz_xp_awarded"], auto_grade.XP_TABLE["vocab_quiz"])
        self.assertEqual(row["vocab_quiz_naming_issue"], "")

    def test_vocab_quiz_zero_xp_without_reflection(self):
        write(self.folder, "04_vocab_quiz_completed.html", self._vocab_quiz_html(reflection=""))
        row = {}
        auto_grade.grade_vocab_quiz(self.folder, row)
        self.assertEqual(row["vocab_quiz_xp_awarded"], 0)

    def test_vocab_quiz_naming_penalty_applied(self):
        write(self.folder, "MyQuizVocabDone.html", self._vocab_quiz_html())
        row = {}
        auto_grade.grade_vocab_quiz(self.folder, row)
        expected = max(auto_grade.MIN_XP_AFTER_PENALTY, auto_grade.XP_TABLE["vocab_quiz"] - auto_grade.NAMING_PENALTY_XP)
        self.assertEqual(row["vocab_quiz_xp_awarded"], expected)
        self.assertNotEqual(row["vocab_quiz_xp_awarded"], auto_grade.XP_TABLE["vocab_quiz"])
        self.assertIn("MyQuizVocabDone.html", row["vocab_quiz_naming_issue"])

    def test_naming_penalty_never_zeroes_genuine_work(self):
        # Guard against a future XP_TABLE tweak making the penalty exceed
        # the award and silently produce negative/zero XP for real work.
        self.assertGreaterEqual(auto_grade.XP_TABLE["feedback"] - auto_grade.NAMING_PENALTY_XP, 0)
        xp = auto_grade._apply_naming_penalty(auto_grade.XP_TABLE["feedback"], True)
        self.assertGreaterEqual(xp, auto_grade.MIN_XP_AFTER_PENALTY)

    def test_mastery_check_xp_requires_both_timestamps(self):
        html_incomplete = '<span id="unlockTime">unlocked_at:2026-08-11T15:10:03Z</span>'
        write(self.folder, "09_mastery_check_completed.html", html_incomplete)
        row = {}
        auto_grade.grade_mastery_check(self.folder, row)
        self.assertEqual(row["mastery_check_xp_awarded"], 0)

    def test_practice_xp_awarded_for_any_genuine_attempt_not_gated_on_perfection(self):
        events = [{"type": "drill_attempt", "drill_id": "drill1", "correct": False}]
        html = (
            '<html><head><script type="application/json" id="foxcs-telemetry">'
            f'{json.dumps({"events": events})}</script></head><body></body></html>'
        )
        write(self.folder, "05_practice_completed.html", html)
        row = {}
        auto_grade.grade_practice(self.folder, row)
        self.assertEqual(row["practice_xp_awarded"], auto_grade.XP_TABLE["practice"])

    def test_flashcards_xp_awarded_when_reviewed(self):
        html = '<span id="reviewedTime">reviewed_at:2026-08-22T14:05:00.000Z</span>'
        write(self.folder, "02_flashcards_completed.html", html)
        row = {}
        auto_grade.grade_flashcards(self.folder, row)
        self.assertEqual(row["flashcards_xp_awarded"], auto_grade.XP_TABLE["flashcards"])

    def test_total_xp_sums_all_five(self):
        write(self.folder, "04_vocab_quiz_completed.html", self._vocab_quiz_html())
        write(self.folder, "11_feedback_completed.html", "<html></html>")
        write(self.folder, "02_flashcards_completed.html", '<span id="reviewedTime">reviewed_at:2026-08-22T14:05:00.000Z</span>')
        row = {}
        auto_grade.grade_vocab_quiz(self.folder, row)
        auto_grade.grade_practice(self.folder, row)
        auto_grade.grade_mastery_check(self.folder, row)
        auto_grade.grade_feedback(self.folder, row)
        auto_grade.grade_flashcards(self.folder, row)
        total = (
            row.get("vocab_quiz_xp_awarded", 0)
            + row.get("practice_xp_awarded", 0)
            + row.get("mastery_check_xp_awarded", 0)
            + row.get("feedback_xp_awarded", 0)
            + row.get("flashcards_xp_awarded", 0)
        )
        self.assertEqual(
            total,
            auto_grade.XP_TABLE["vocab_quiz"] + auto_grade.XP_TABLE["feedback"] + auto_grade.XP_TABLE["flashcards"],
        )


class TestCollectNeedsReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_flags_only_judgment_required_files(self):
        for name in [
            "00_table_of_contents.html",
            "01_instruction.html",
            "02_example_01.py",
            "04_vocab_quiz_completed.html",
            "06_application.py",
            "07_project.html",
            "08_project.py",
            "10_mastery_check.py",
        ]:
            write(self.folder, name, "x")
        rows = []
        auto_grade.collect_needs_review(self.folder, "STUDENT-0001", "lesson_01_04_printing_output", rows)
        flagged = {r["file"] for r in rows}
        self.assertIn("06_application.py", flagged)
        self.assertIn("07_project.html", flagged)
        self.assertIn("08_project.py", flagged)
        self.assertIn("10_mastery_check.py", flagged)
        self.assertIn("02_example_01.py", flagged)
        # Read-only/completed files with no judgment-required pattern should
        # not appear: the manifest should only list what genuinely needs review.
        self.assertNotIn("00_table_of_contents.html", flagged)
        self.assertNotIn("01_instruction.html", flagged)
        self.assertNotIn("04_vocab_quiz_completed.html", flagged)

    def test_flags_mastery_check_py_with_or_without_completed_suffix(self):
        # 10_mastery_check.py can be manually saved-as
        # 10_mastery_check_completed.py.
        # Both the pre- and post-rename form are real submissions needing
        # review, the pattern must match either.
        for name in ("10_mastery_check.py", "10_mastery_check_completed.py"):
            with self.subTest(name=name):
                folder = Path(tempfile.mkdtemp())
                try:
                    write(folder, name, "x")
                    rows = []
                    auto_grade.collect_needs_review(folder, "TEST", "lesson", rows)
                    self.assertEqual({r["file"] for r in rows}, {name})
                finally:
                    import shutil
                    shutil.rmtree(folder)


class TestRealFixtureEndToEnd(unittest.TestCase):
    """Grounds every rule above against the checked-in synthetic fixture,
    not just inline examples. Expected values match the persona documented
    in sample-submissions/README.md."""

    @classmethod
    def setUpClass(cls):
        row = {"student_id": "STUDENT-0001", "lesson": "lesson_01_04_printing_output"}
        auto_grade.grade_vocab_quiz(REAL_FIXTURE, row)
        auto_grade.grade_practice(REAL_FIXTURE, row)
        auto_grade.grade_mastery_check(REAL_FIXTURE, row)
        auto_grade.grade_feedback(REAL_FIXTURE, row)
        auto_grade.grade_flashcards(REAL_FIXTURE, row)
        cls.row = row
        cls.manifest = []
        auto_grade.collect_needs_review(REAL_FIXTURE, "STUDENT-0001", "lesson_01_04_printing_output", cls.manifest)

    def test_vocab_quiz(self):
        self.assertEqual(self.row["vocab_quiz_saved"], "Y")
        self.assertEqual(self.row["vocab_quiz_check_attempts"], 2)
        self.assertEqual(self.row["vocab_quiz_first_attempt_all_correct"], "N")
        self.assertEqual(self.row["vocab_quiz_all_correct_by_save"], "Y")
        self.assertGreater(self.row["vocab_quiz_reflection_word_count"], 0)

    def test_practice(self):
        self.assertEqual(self.row["practice_saved"], "Y")
        self.assertEqual(self.row["practice_drills_attempted"], 8)
        self.assertEqual(self.row["practice_first_try_correct_count"], 4)

    def test_mastery_check(self):
        self.assertEqual(self.row["mastery_check_saved"], "Y")
        self.assertEqual(self.row["mastery_check_minutes_unlocked_to_complete"], 14.6)

    def test_feedback(self):
        self.assertEqual(self.row["feedback_saved"], "Y")

    def test_flashcards_not_reviewed(self):
        # The fixture's 03_flashcards.html is untouched (no _completed
        # suffix, no reviewed_at marker). No exact match exists, so
        # find_file_fuzzy() falls back to the untouched file itself (its name
        # contains "flashcard"). flashcards_saved reads "Y" because something
        # matched, but XP correctly comes out to 0 with no reviewed_at marker.
        # This is the accepted tradeoff documented in find_file_fuzzy().
        self.assertEqual(self.row["flashcards_saved"], "Y")
        self.assertEqual(self.row["flashcards_xp_awarded"], 0)
        self.assertIn("03_flashcards.html", self.row["flashcards_naming_issue"])

    def test_needs_review_manifest_has_five_files(self):
        flagged = {r["file"] for r in self.manifest}
        self.assertEqual(
            flagged,
            {
                "02_example_01.py",
                "06_application.py",
                "07_project.html",
                "08_project.py",
                "10_mastery_check.py",
            },
        )


if __name__ == "__main__":
    unittest.main()
