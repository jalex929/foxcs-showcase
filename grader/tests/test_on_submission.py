"""
Tests for on_submission.py: grading a Coding Exercise the moment it is
submitted. CI enforces 100% line and branch coverage of on_submission.py
with these tests (see docs/testing.md).

Most tests run real student-style programs from on_submission_fixtures/
through a real Python subprocess, the same way production grading does.
A fake runner is used only where a real program cannot produce the case
(for example, the runner itself raising).

Run from the repository root with:
    python -m unittest discover -s grader/tests -v
"""

import dataclasses
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

GRADER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GRADER_DIR))
import on_submission as osub  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "on_submission_fixtures"
EXERCISES_DIR = GRADER_DIR / "exercises"
ASSIGNMENT = "lesson_02_06_coins_to_points"
SPEC = osub.load_exercises(EXERCISES_DIR)[ASSIGNMENT]
FAST_SPEC = dataclasses.replace(SPEC, timeout_seconds=1.5)


def event_for(path, submission_id="sub-1", assignment=ASSIGNMENT, name=None):
    return {
        "submission_id": submission_id,
        "student_id": "STUDENT-0001",
        "assignment_id": assignment,
        "submitted_at": "2026-01-12T14:00:00Z",
        "files": [{"name": name or Path(path).name, "path": str(path)}],
    }


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = osub.ResultStore(self.tmp / "results" / "drafts.jsonl")

    def tearDown(self):
        self._tmp.cleanup()

    def submit(self, fixture, spec=FAST_SPEC, **kw):
        return osub.handle_submission(
            event_for(FIXTURES / fixture, **kw), {ASSIGNMENT: spec}, FIXTURES, self.store
        )

    def write(self, name, content):
        path = self.tmp / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path


# ------------------------------------------------------------ the three bands

class TestBands(TempCase):
    def test_flawless_gets_full_credit_and_no_review(self):
        r = self.submit("flawless.py")
        self.assertEqual(r["band"], "flawless")
        self.assertEqual(r["score"]["standardPointsEarned"], 10.0)
        self.assertEqual(r["score"]["finalPointsEarned"], 10.0)
        self.assertFalse(r["review"]["teacherReviewRequired"])
        self.assertTrue(all(t["passed"] for t in r["tests"]))
        self.assertIn("all 3 test inputs", r["feedback"]["overallFeedback"])

    def test_valid_alternative_is_not_penalized(self):
        r = self.submit("commas_alternative.py")
        self.assertEqual(r["band"], "flawless")
        self.assertEqual(r["score"]["standardPointsEarned"], 10.0)

    def test_runs_but_wrong_gets_runs_credit_only_and_is_held(self):
        r = self.submit("string_repetition.py")
        self.assertEqual(r["band"], "runs_with_issues")
        by_id = {c["criterionId"]: c for c in r["rubricResults"]}
        self.assertEqual(by_id["runs_without_errors"]["pointsEarned"], 5.0)
        self.assertEqual(by_id["correct_output"]["pointsEarned"], 0.0)
        self.assertTrue(r["review"]["teacherReviewRequired"])
        self.assertIn("three coins", r["feedback"]["nextStep"])
        self.assertIn("3333333333", " ".join(r["tests"][0]["problems"]))

    def test_partial_correctness_scales_correct_component(self):
        r = self.submit("partial_zero_case.py")
        self.assertEqual(r["band"], "runs_with_issues")
        self.assertEqual(r["score"]["standardPointsEarned"], 8.33)  # 5 + 5 * 2/3
        self.assertIn("zero coins", r["feedback"]["nextStep"])
        self.assertEqual(r["review"]["overallConfidence"], "medium")

    def test_syntax_error_does_not_run(self):
        r = self.submit("syntax_error.py")
        self.assertEqual(r["band"], "does_not_run")
        self.assertEqual(r["score"]["standardPointsEarned"], 0.0)
        self.assertIn("SyntaxError", r["feedback"]["overallFeedback"])
        self.assertFalse(r["review"]["teacherReviewRequired"])

    def test_runtime_error_does_not_run(self):
        r = self.submit("type_error.py")
        self.assertEqual(r["band"], "does_not_run")
        self.assertIn("TypeError", r["feedback"]["overallFeedback"])
        self.assertIn("0 of 3 runs exited cleanly", r["rubricResults"][0]["evidence"][0])

    def test_scores_add_up_and_never_exceed_possible(self):
        for fixture in ("flawless.py", "string_repetition.py", "partial_zero_case.py", "type_error.py"):
            with self.subTest(fixture=fixture):
                r = self.submit(fixture, submission_id=fixture)
                criteria = sum(c["pointsEarned"] for c in r["rubricResults"])
                self.assertAlmostEqual(criteria, r["score"]["standardPointsEarned"], places=2)
                self.assertLessEqual(r["score"]["standardPointsEarned"], r["score"]["standardPointsPossible"])
                self.assertEqual(r["score"]["bonusRecommended"], 0)


# ------------------------------------------------------------ safety

class TestSafety(TempCase):
    def test_every_result_is_a_draft_pending_approval(self):
        for fixture in ("flawless.py", "syntax_error.py", "infinite_loop.py"):
            with self.subTest(fixture=fixture):
                r = self.submit(fixture, submission_id=fixture)
                self.assertEqual(r["release"], "pending_teacher_approval")

    def test_prompt_injection_in_code_changes_nothing(self):
        injected = self.submit("prompt_injection.py", submission_id="a")
        plain = self.submit("string_repetition.py", submission_id="b")
        self.assertEqual(injected["band"], plain["band"])
        self.assertEqual(injected["score"], plain["score"])

    def test_infinite_loop_times_out_and_is_held_unscored(self):
        r = self.submit("infinite_loop.py")
        self.assertIsNone(r["band"])
        self.assertIsNone(r["score"])
        self.assertTrue(r["review"]["teacherReviewRequired"])
        self.assertIn("timed out", r["review"]["reasons"][0])
        self.assertTrue(r["tests"][0]["timedOut"])

    @unittest.skipUnless(sys.platform != "win32", "resource limits are POSIX only")
    def test_memory_limit_stops_runaway_allocation(self):
        r = self.submit("memory_hog.py")
        self.assertEqual(r["band"], "does_not_run")
        self.assertIn("MemoryError", r["feedback"]["overallFeedback"])

    def test_child_runs_isolated_with_minimal_environment(self):
        run = osub.run_program(FIXTURES / "reads_grader_files.py", "2\n", FAST_SPEC)
        self.assertEqual(run.exit_code, 0, run.stderr)
        self.assertIn("ISOLATED=1", run.stdout)
        self.assertIn("ENV=\n", run.stdout)

    def test_path_outside_submissions_root_is_refused(self):
        outside = self.write("sneaky.py", 'print("You have 30 points.")\n')
        r = osub.handle_submission(event_for(outside), {ASSIGNMENT: FAST_SPEC}, FIXTURES, self.store)
        self.assertIsNone(r["score"])
        self.assertIn("outside the submissions folder", r["review"]["reasons"][0])

    def test_grader_exception_is_held_not_raised(self):
        def broken_runner(path, stdin, spec):
            raise RuntimeError("runner exploded")
        r = osub.handle_submission(
            event_for(FIXTURES / "flawless.py"), {ASSIGNMENT: FAST_SPEC}, FIXTURES, self.store, runner=broken_runner
        )
        self.assertIsNone(r["score"])
        self.assertIn("RuntimeError: runner exploded", r["review"]["reasons"][0])
        self.assertIsNotNone(r["content_hash"])


# ------------------------------------------------------------ committed examples

class TestCommittedExamples(TempCase):
    """examples/on-submission/ is what the README shows reviewers. Each
    committed result must match what the code produces from its event."""

    def test_examples_match_fresh_runs(self):
        examples = GRADER_DIR.parent / "examples" / "on-submission"
        events = sorted(examples.glob("event-*.json"))
        self.assertEqual(len(events), 3)
        for ev_path in events:
            with self.subTest(example=ev_path.name):
                event = json.loads(ev_path.read_text())
                event["files"][0]["path"] = str(GRADER_DIR.parent / event["files"][0]["path"])
                fresh = osub.handle_submission(event, osub.load_exercises(EXERCISES_DIR), FIXTURES, self.store)
                committed = json.loads((examples / ev_path.name.replace("event-", "result-")).read_text())
                self.assertEqual(fresh, committed)


# ------------------------------------------------------------ idempotency

class TestIdempotency(TempCase):
    def test_duplicate_delivery_is_not_regraded(self):
        calls = []

        def counting_runner(path, stdin, spec):
            calls.append(stdin)
            return osub.run_program(path, stdin, spec)

        args = (event_for(FIXTURES / "flawless.py"), {ASSIGNMENT: FAST_SPEC}, FIXTURES, self.store)
        first = osub.handle_submission(*args, runner=counting_runner)
        second = osub.handle_submission(*args, runner=counting_runner)
        self.assertEqual(len(calls), 3)  # one run per test, first delivery only
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["score"], first["score"])
        self.assertEqual(len(self.store.path.read_text().splitlines()), 1)

    def test_resubmission_with_new_contents_is_regraded(self):
        f = self.write("exercise.py", (FIXTURES / "string_repetition.py").read_text())
        spec = {ASSIGNMENT: FAST_SPEC}
        first = osub.handle_submission(event_for(f), spec, self.tmp, self.store)
        f.write_text((FIXTURES / "flawless.py").read_text())
        second = osub.handle_submission(event_for(f), spec, self.tmp, self.store)
        self.assertEqual(first["band"], "runs_with_issues")
        self.assertEqual(second["band"], "flawless")
        self.assertNotIn("duplicate", second)
        self.assertEqual(self.store.latest("sub-1")["band"], "flawless")


# ------------------------------------------------------------ held for review

class TestHeldForReview(TempCase):
    def handle(self, event):
        return osub.handle_submission(event, {ASSIGNMENT: FAST_SPEC}, self.tmp, self.store)

    def assertHeld(self, result, reason_fragment):
        self.assertIsNone(result["score"])
        self.assertTrue(result["review"]["teacherReviewRequired"])
        self.assertIn(reason_fragment, result["review"]["reasons"][0])
        self.assertEqual(self.store.latest(result["submission_id"])["review"], result["review"])

    def test_unknown_assignment_is_not_scored_against_nothing(self):
        r = osub.handle_submission(event_for(FIXTURES / "flawless.py", assignment="mystery"), {}, FIXTURES, self.store)
        self.assertHeld(r, "No exercise spec")

    def test_no_files(self):
        ev = event_for("x.py")
        ev["files"] = []
        self.assertHeld(self.handle(ev), "No file was submitted")

    def test_two_files(self):
        a, b = self.write("a.py", "print(1)\n"), self.write("b.py", "print(2)\n")
        ev = event_for(a)
        ev["files"].append({"name": "b.py", "path": str(b)})
        self.assertHeld(self.handle(ev), "Expected exactly one .py file")

    def test_wrong_extension(self):
        self.assertHeld(self.handle(event_for(self.write("answer.txt", "print(1)\n"))), "Expected exactly one .py file")

    def test_missing_file(self):
        self.assertHeld(self.handle(event_for(self.tmp / "gone.py")), "could not be found")

    def test_empty_file(self):
        self.assertHeld(self.handle(event_for(self.write("empty.py", ""))), "is empty")

    def test_oversized_file(self):
        big = self.write("big.py", "# " + "x" * (FAST_SPEC.max_bytes + 10) + "\n")
        self.assertHeld(self.handle(event_for(big)), "over the")

    def test_not_utf8(self):
        self.assertHeld(self.handle(event_for(self.write("latin1.py", b"print('caf\xe9')\n"))), "not valid UTF-8")


# ------------------------------------------------------------ event parsing

class TestParseEvent(unittest.TestCase):
    def base(self):
        return event_for("/tmp/x.py")

    def test_valid(self):
        ev = osub.parse_event(self.base())
        self.assertEqual(ev.files[0].name, "x.py")

    def test_rejections(self):
        cases = {
            "not an object": [],
            "missing field": {k: v for k, v in self.base().items() if k != "student_id"},
            "wrong type": {**self.base(), "submission_id": 42},
            "bool is not a string": {**self.base(), "submission_id": True},
            "blank string": {**self.base(), "assignment_id": "  "},
            "files not a list": {**self.base(), "files": "x.py"},
            "file not an object": {**self.base(), "files": ["x.py"]},
            "file missing path": {**self.base(), "files": [{"name": "x.py"}]},
        }
        for label, data in cases.items():
            with self.subTest(label):
                with self.assertRaises(osub.InvalidEvent):
                    osub.parse_event(data)

    def test_invalid_event_is_rejected_without_storing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = osub.ResultStore(Path(tmp) / "r.jsonl")
            r = osub.handle_submission({"nope": 1}, {}, Path(tmp), store)
            self.assertFalse(r["accepted"])
            self.assertIn("missing field", r["error"])
            self.assertFalse(store.path.exists())


# ------------------------------------------------------------ exercise specs

class TestExerciseSpecs(unittest.TestCase):
    def base(self):
        return json.loads((EXERCISES_DIR / "coins_to_points.json").read_text())

    def test_defaults_apply(self):
        data = self.base()
        for key in ("timeoutSeconds", "maxBytes", "memoryLimitMb"):
            data.pop(key)
        data["tests"] = [{"name": "t", "expectedContains": ["ok"]}]
        spec = osub.parse_exercise(data)
        self.assertEqual(spec.timeout_seconds, osub.DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(spec.max_bytes, osub.DEFAULT_MAX_BYTES)
        self.assertEqual(spec.tests[0].stdin, "")
        self.assertEqual(spec.tests[0].expected_not_contains, ())

    def test_rejections(self):
        def with_(**changes):
            return {**self.base(), **changes}

        def with_test(**changes):
            t = {**self.base()["tests"][0], **changes}
            return with_(tests=[t])

        cases = {
            "not an object": "x",
            "no tests": with_(tests=[]),
            "test not an object": with_(tests=["t"]),
            "empty expectedContains": with_test(expectedContains=[]),
            "stdin not a string": with_test(stdin=3),
            "forbidden not a list": with_test(expectedNotContains="x"),
            "blank expected entry": with_test(expectedContains=[""]),
            "non-string expected entry": with_test(expectedContains=[30]),
            "points wrong type": with_(pointsPossible="10"),
            "points zero": with_(pointsPossible=0),
            "timeout wrong type": with_(timeoutSeconds="5"),
            "timeout negative": with_(timeoutSeconds=-1),
        }
        for label, data in cases.items():
            with self.subTest(label):
                with self.assertRaises(osub.InvalidExercise):
                    osub.parse_exercise(data)

    def test_type_error_message_names_all_accepted_types(self):
        with self.assertRaisesRegex(osub.InvalidExercise, "int or float"):
            osub.parse_exercise({**self.base(), "pointsPossible": "10"})

    def test_duplicate_assignment_ids_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("a.json", "b.json"):
                Path(tmp, name).write_text(json.dumps(self.base()))
            with self.assertRaises(osub.InvalidExercise):
                osub.load_exercises(Path(tmp))

    def test_shipped_exercises_load(self):
        self.assertIn(ASSIGNMENT, osub.load_exercises(EXERCISES_DIR))


# ------------------------------------------------------------ helpers

class TestHelpers(unittest.TestCase):
    def test_clip(self):
        self.assertEqual(osub._clip(None), "")
        self.assertEqual(osub._clip(b"caf\xc3\xa9"), "café")
        self.assertEqual(osub._clip("short"), "short")
        long = osub._clip("x" * (osub.MAX_CAPTURED_CHARS + 5))
        self.assertTrue(long.endswith("[output truncated]"))
        self.assertEqual(len(long), osub.MAX_CAPTURED_CHARS + len("\n[output truncated]"))

    def test_error_summary(self):
        self.assertEqual(osub._error_summary("Traceback...\n  line\nValueError: bad\n\n"), "ValueError: bad")
        self.assertEqual(osub._error_summary("   \n"), "the program exited with an error")

    def test_check_output(self):
        t = osub.TestCase("t", "", ("good",), ("bad",))
        self.assertEqual(osub.check_output("good", t), (True, []))
        ok, problems = osub.check_output("bad", t)
        self.assertFalse(ok)
        self.assertEqual(len(problems), 2)

    def test_timeout_keeps_partial_output(self):
        run = osub.run_program(FIXTURES / "infinite_loop.py", "1\n", dataclasses.replace(SPEC, timeout_seconds=1))
        self.assertTrue(run.timed_out)
        self.assertIsNone(run.exit_code)
        self.assertIsInstance(run.stdout, str)


class TestResultStore(unittest.TestCase):
    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(osub.ResultStore(Path(tmp) / "none.jsonl").latest("x"))

    def test_latest_record_wins_and_blank_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = osub.ResultStore(Path(tmp) / "deep" / "r.jsonl")
            store.append({"submission_id": "a", "v": 1})
            store.append({"submission_id": "b", "v": 1})
            with store.path.open("a") as f:
                f.write("\n")
            store.append({"submission_id": "a", "v": 2})
            self.assertEqual(store.latest("a")["v"], 2)
            self.assertEqual(store.latest("b")["v"], 1)
            self.assertIsNone(store.latest("c"))


# ------------------------------------------------------------ command line

class TestMain(unittest.TestCase):
    def run_main(self, event_path, tmp):
        out = io.StringIO()
        with redirect_stdout(out):
            code = osub.main([
                str(event_path), "--exercises", str(EXERCISES_DIR),
                "--submissions-root", str(FIXTURES), "--store", str(Path(tmp) / "r.jsonl"),
            ])
        return code, json.loads(out.getvalue())

    def test_grades_from_event_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ev = Path(tmp) / "event.json"
            ev.write_text(json.dumps(event_for(FIXTURES / "flawless.py")))
            code, result = self.run_main(ev, tmp)
        self.assertEqual(code, 0)
        self.assertEqual(result["band"], "flawless")

    def test_invalid_event_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            ev = Path(tmp) / "event.json"
            ev.write_text(json.dumps({"files": []}))
            code, result = self.run_main(ev, tmp)
        self.assertEqual(code, 2)
        self.assertFalse(result["accepted"])

    def test_unreadable_or_malformed_event_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not json")
            for path in (bad, Path(tmp) / "missing.json"):
                with self.subTest(path=path.name):
                    code, result = self.run_main(path, tmp)
                    self.assertEqual(code, 2)
                    self.assertIn("could not read event", result["error"])


if __name__ == "__main__":
    unittest.main()
