"""
End-to-end tests for auto_grade.py as a command-line program: the way it is
actually run. Covers exit codes, report shape, the second synthetic
submission, and a golden-file check that the committed demo output in
examples/demo-output/ still matches what the code produces today.

Run from the repository root with:
    python -m unittest discover -s grader/tests -v
"""

import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GRADER_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = GRADER_DIR.parent
SCRIPT = GRADER_DIR / "auto_grade.py"
SUBMISSIONS = GRADER_DIR / "sample-submissions"
GOLDEN = REPO_ROOT / "examples" / "demo-output"

REPORT_ID_COLUMNS = ["student_id", "lesson"]
MANIFEST_COLUMNS = ["student_id", "lesson", "file", "reason"]


def run_grader(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True, text=True,
    )


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class TestExitCodes(unittest.TestCase):
    def test_missing_folder_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_grader(Path(tmp) / "does-not-exist", "--out-dir", tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Not a folder", result.stderr)

    def test_empty_folder_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty"
            empty.mkdir()
            result = run_grader(empty, "--out-dir", tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn("No submission folders", result.stderr)

    def test_loose_files_without_folders_exit_1(self):
        # Submissions must be folders. A stray file at the root is not one.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "subs"
            root.mkdir()
            (root / "stray.html").write_text("x", encoding="utf-8")
            result = run_grader(root, "--out-dir", tmp)
        self.assertEqual(result.returncode, 1)

    def test_out_dir_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nested" / "out"
            result = run_grader(SUBMISSIONS, "--out-dir", out)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((out / "auto_grade_report.csv").is_file())
            self.assertTrue((out / "needs_review_manifest.csv").is_file())


class TestReportsFromSyntheticSubmissions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        cls.result = run_grader(SUBMISSIONS, "--out-dir", out)
        cls.report = {r["student_id"]: r for r in read_csv(out / "auto_grade_report.csv")}
        cls.manifest = read_csv(out / "needs_review_manifest.csv")
        with open(out / "auto_grade_report.csv", encoding="utf-8") as f:
            cls.report_header = next(csv.reader(f))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_run_succeeds(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        self.assertIn("Graded 2 submission(s).", self.result.stdout)

    def test_console_counts_every_naming_issue(self):
        # One untouched flashcards file (STUDENT-0001) plus one misnamed vocab
        # quiz (STUDENT-0002). Flashcards were once left out of this count.
        self.assertIn("2 file(s) were fuzzy-matched", self.result.stdout)

    def test_report_columns(self):
        self.assertEqual(self.report_header[:2], REPORT_ID_COLUMNS)
        self.assertEqual(self.report_header[-1], "total_xp_awarded")
        self.assertEqual(self.report_header.count("student_id"), 1)

    def test_one_row_per_submission_folder(self):
        self.assertEqual(set(self.report), {"STUDENT-0001", "STUDENT-0002"})

    def test_total_xp_is_sum_of_parts(self):
        parts = ["vocab_quiz", "practice", "mastery_check", "feedback", "flashcards"]
        for sid, row in self.report.items():
            with self.subTest(student=sid):
                expected = sum(int(row[f"{p}_xp_awarded"] or 0) for p in parts)
                self.assertEqual(int(row["total_xp_awarded"]), expected)

    def test_student_0001_totals(self):
        self.assertEqual(self.report["STUDENT-0001"]["total_xp_awarded"], "36")

    def test_student_0002_misnamed_quiz_found_and_penalized(self):
        row = self.report["STUDENT-0002"]
        self.assertEqual(row["vocab_quiz_saved"], "Y")
        self.assertIn("Vocab Quiz (1).html", row["vocab_quiz_naming_issue"])
        self.assertEqual(row["vocab_quiz_xp_awarded"], "3")  # 5 minus the 2 XP naming penalty

    def test_student_0002_empty_practice_earns_nothing(self):
        row = self.report["STUDENT-0002"]
        self.assertEqual(row["practice_saved"], "Y")
        self.assertEqual(row["practice_drills_attempted"], "0")
        self.assertEqual(row["practice_xp_awarded"], "0")

    def test_student_0002_missing_activities_reported_not_submitted(self):
        row = self.report["STUDENT-0002"]
        for activity in ("mastery_check", "feedback", "flashcards"):
            with self.subTest(activity=activity):
                self.assertEqual(row[f"{activity}_saved"], "N")
                self.assertEqual(row[f"{activity}_xp_awarded"], "0")

    def test_manifest_shape_and_contents(self):
        self.assertTrue(self.manifest)
        self.assertEqual(list(self.manifest[0].keys()), MANIFEST_COLUMNS)
        flagged = {(r["student_id"], r["file"]) for r in self.manifest}
        self.assertIn(("STUDENT-0002", "08_project.py"), flagged)
        self.assertEqual(len(self.manifest), 6)
        for r in self.manifest:
            self.assertTrue(r["reason"].strip(), f"empty reason for {r['file']}")

    def test_graded_html_never_appears_in_manifest(self):
        for r in self.manifest:
            self.assertNotRegex(r["file"], r"_completed\.html$")


class TestGoldenDemoOutput(unittest.TestCase):
    """The README points reviewers at examples/demo-output/. It must match
    what the code produces today, or the showcase is showing stale results."""

    def test_committed_demo_output_matches_fresh_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_grader(SUBMISSIONS, "--out-dir", tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in ("auto_grade_report.csv", "needs_review_manifest.csv"):
                with self.subTest(file=name):
                    fresh = (Path(tmp) / name).read_text(encoding="utf-8")
                    committed = (GOLDEN / name).read_text(encoding="utf-8")
                    self.assertEqual(
                        fresh, committed,
                        f"examples/demo-output/{name} is stale. Regenerate with: "
                        "python grader/auto_grade.py grader/sample-submissions --out-dir examples/demo-output",
                    )


if __name__ == "__main__":
    unittest.main()
