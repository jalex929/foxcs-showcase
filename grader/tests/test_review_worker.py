"""
Tests for review_worker.py: the queue worker that grades each submission
event, sends only judgment cases to the AI backend, and leaves everything
for the release check and the teacher.

The deterministic grader runs for real on the fixture programs. The AI
backend is a fake, so no subscription, key or network is needed.

Run from the repository root with:
    python -m unittest discover -s grader/tests -v
"""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

GRADER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GRADER))
import release_check as rc  # noqa: E402
import review_worker as rw  # noqa: E402

rm, osub = rw.rm, rw.osub
FIXTURES = GRADER / "tests" / "on_submission_fixtures"
EXERCISES = osub.load_exercises(GRADER / "exercises")
RUBRICS = rw.load_rubrics(GRADER / "rubrics")
ASSIGNMENT = "lesson_02_06_coins_to_points"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def ai_result(runs=5, correct=1):
    return {
        "rubricResults": [
            {"criterionId": "runs_without_errors", "pointsEarned": runs, "pointsPossible": 5,
             "evidence": ["Every test run exited with code 0."], "rationale": "The program runs on every input.", "confidence": "high"},
            {"criterionId": "correct_output", "pointsEarned": correct, "pointsPossible": 5,
             "evidence": ["`points = coins * 10` repeats the text instead of multiplying."],
             "rationale": "The input is never converted to a number, so the total is wrong on every input.", "confidence": "high"},
        ],
        "score": {"standardPointsEarned": runs + correct, "standardPointsPossible": 10, "bonusRecommended": 0,
                  "finalPointsEarned": runs + correct},
        "feedback": {"strengths": ["Your program runs and asks for input."],
                     "priorityImprovements": ["Convert the input with int() before multiplying."],
                     "overallFeedback": "It runs, but the point total is wrong because the input is still text.",
                     "nextStep": "Wrap input() in int() and run it with 3 coins."},
        "review": {"teacherReviewRequired": False, "reasons": [], "overallConfidence": "high"},
    }


class FakeBackend:
    name = "fake"

    def __init__(self, script=None, preflight_error=None):
        self.script = list(script or [])
        self.preflight_error = preflight_error
        self.calls = []

    def preflight(self):
        if self.preflight_error:
            raise self.preflight_error

    def grade(self, case, assignment, model, effort):
        self.calls.append({"case": case, "assignment": assignment, "model": model, "effort": effort})
        step = self.script.pop(0) if self.script else ai_result()
        if isinstance(step, Exception):
            raise step
        return json.loads(json.dumps(step)), {"input_tokens": 10}, 0.0


class QueueTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.queue = Path(tmp.name) / "queue"
        rw.ensure_queue(self.queue)
        self.store = osub.ResultStore(self.queue / "drafts.jsonl")
        self.backend = FakeBackend()

    def enqueue(self, fixture, sid="sub-1", name="exercise.py", path=None):
        event = {"submission_id": sid, "student_id": "STUDENT-0001", "assignment_id": ASSIGNMENT,
                 "submitted_at": "2026-01-12T14:00:00Z",
                 "files": [{"name": name, "path": str(path or FIXTURES / fixture)}]}
        dest = self.queue / "incoming" / f"{sid}.json"
        dest.write_text(json.dumps(event), encoding="utf-8")
        return dest

    def process(self, path, rubrics=RUBRICS, root=FIXTURES):
        return rw.process_event(path, self.queue, EXERCISES, rubrics, root, self.store, self.backend, "m", "high")

    def names(self, folder):
        return sorted(p.name for p in (self.queue / folder).iterdir())


class TestRouting(QueueTest):
    def test_judgment_case_gets_an_ai_review_ready_for_the_release_check(self):
        outcome = self.process(self.enqueue("string_repetition.py"))
        self.assertEqual((outcome["status"], outcome["band"], outcome["submission_id"]), ("ai_reviewed", "runs_with_issues", "sub-1"))
        self.assertEqual((outcome["usage"], outcome["cost_usd"]), ({"input_tokens": 10}, 0.0))
        out = load(self.queue / "review" / "outputs" / "sub-1.json")
        self.assertEqual((out["submission_id"], out["student_id"], out["assignment_id"]), ("sub-1", "STUDENT-0001", ASSIGNMENT))
        self.assertEqual(out["release"], "pending_teacher_approval")
        draft = self.store.latest("sub-1")
        self.assertEqual(out["reviewedBy"], {"backend": "fake", "model": "m", "effort": "high", "contentHash": draft["content_hash"]})
        code = (FIXTURES / "string_repetition.py").read_text(encoding="utf-8")
        self.assertEqual((self.queue / "review" / "submissions" / "sub-1" / "exercise.py").read_text(encoding="utf-8"), code)
        self.assertEqual(self.names("done"), ["sub-1.json"])
        self.assertEqual((self.names("incoming"), self.names("processing"), self.names("failed")), ([], [], []))

    def test_the_ai_sees_the_code_the_rubric_and_the_run_facts(self):
        self.process(self.enqueue("string_repetition.py"))
        call = self.backend.calls[0]
        self.assertEqual(call["case"]["submission"]["files"],
                         {"exercise.py": (FIXTURES / "string_repetition.py").read_text(encoding="utf-8")})
        self.assertEqual(call["case"]["submission"]["explanation"], "")
        self.assertEqual(call["case"]["programRuns"], self.store.latest("sub-1")["tests"])
        self.assertIs(call["assignment"], RUBRICS[ASSIGNMENT])
        self.assertEqual((call["model"], call["effort"]), ("m", "high"))
        message = rm.build_user_message(call["case"], call["assignment"])
        self.assertIn("<program_runs>", message)
        self.assertIn("- three coins: exit code 0, failed: expected output to contain", message)

    def test_settled_bands_never_reach_the_ai(self):
        for i, (fixture, band) in enumerate((("flawless.py", "flawless"), ("syntax_error.py", "does_not_run"))):
            with self.subTest(band):
                outcome = self.process(self.enqueue(fixture, sid=f"s{i}"))
                self.assertEqual((outcome["status"], outcome["band"]), ("drafted", band))
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.names("review/outputs"), [])
        self.assertEqual(self.names("done"), ["s0.json", "s1.json"])

    def test_held_drafts_go_to_the_teacher_not_the_ai(self):
        outcome = self.process(self.enqueue("flawless.py", name="notes.txt"))
        self.assertEqual((outcome["status"], outcome["band"]), ("drafted", None))
        self.assertEqual(self.backend.calls, [])

    def test_no_rubric_means_no_ai_review(self):
        outcome = self.process(self.enqueue("string_repetition.py"), rubrics={})
        self.assertEqual(outcome["status"], "drafted")
        self.assertEqual(outcome["note"], f"no AI rubric for '{ASSIGNMENT}', so it waits for the teacher")
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.names("done"), ["sub-1.json"])

    def test_same_file_twice_is_reviewed_once(self):
        for _ in range(2):
            outcome = self.process(self.enqueue("string_repetition.py"))
        self.assertEqual(outcome["status"], "duplicate")
        self.assertEqual(len(self.backend.calls), 1)
        self.process(self.enqueue("flawless.py", sid="f"))
        self.assertEqual(self.process(self.enqueue("flawless.py", sid="f"))["status"], "duplicate")

    def test_resubmission_with_new_code_is_reviewed_again(self):
        root = self.queue.parent / "subs"
        root.mkdir()
        target = root / "exercise.py"
        shutil.copy(FIXTURES / "string_repetition.py", target)
        self.process(self.enqueue(None, path=target), root=root)
        target.write_text(target.read_text(encoding="utf-8") + "\n# tried again\n", encoding="utf-8")
        self.backend.script = [ai_result(correct=2)]
        outcome = self.process(self.enqueue(None, path=target), root=root)
        self.assertEqual(outcome["status"], "ai_reviewed")
        self.assertEqual(len(self.backend.calls), 2)
        self.assertEqual(load(self.queue / "review" / "outputs" / "sub-1.json")["score"]["finalPointsEarned"], 7)
        self.assertEqual(self.names("review/submissions/sub-1"), ["exercise.py"])
        self.assertIn("# tried again", (self.queue / "review" / "submissions" / "sub-1" / "exercise.py").read_text(encoding="utf-8"))

    def test_output_without_a_hash_is_reviewed_again(self):
        rm.write_json(self.queue / "review" / "outputs" / "sub-1.json", {"score": {}})
        self.assertIsNone(rw.reviewed_hash(self.queue, "sub-1"))
        self.assertEqual(self.process(self.enqueue("string_repetition.py"))["status"], "ai_reviewed")


class TestFailures(QueueTest):
    def test_failed_review_is_recorded_and_can_be_requeued(self):
        self.backend.script = [rm.GradingFailed("claude reported error_during_execution: no detail")]
        outcome = self.process(self.enqueue("string_repetition.py"))
        self.assertEqual(outcome, {"event": "sub-1.json", "status": "failed", "submission_id": "sub-1",
                                   "error": "AI review failed: claude reported error_during_execution: no detail"})
        self.assertEqual(self.names("failed"), ["sub-1.error.json", "sub-1.json"])
        self.assertEqual(load(self.queue / "failed" / "sub-1.error.json"), {"event": "sub-1.json", "error": outcome["error"]})
        self.assertEqual(self.names("review/outputs"), [])  # a failure is never turned into a grade
        # The teacher requeues it by hand. The draft store already has this file, but it still gets its review.
        shutil.move(self.queue / "failed" / "sub-1.json", self.queue / "incoming" / "sub-1.json")
        self.assertEqual(self.process(self.queue / "incoming" / "sub-1.json")["status"], "ai_reviewed")
        self.assertEqual(len(self.backend.calls), 2)

    def test_losing_the_subscription_puts_the_event_back_and_stops(self):
        self.backend.script = [rm.SubscriptionRequired("signed out")]
        path = self.enqueue("string_repetition.py")
        with self.assertRaises(rm.SubscriptionRequired):
            self.process(path)
        self.assertEqual((self.names("incoming"), self.names("processing"), self.names("done")), (["sub-1.json"], [], []))
        self.assertEqual(self.process(path)["status"], "ai_reviewed")

    def test_unreadable_events_fail_without_stopping_the_queue(self):
        bad = self.queue / "incoming" / "bad.json"
        for content in (b"{not json", b"\xff\xfe"):
            with self.subTest(content=content):
                bad.write_bytes(content)
                outcome = self.process(bad)
                self.assertEqual(outcome["status"], "failed")
                self.assertTrue(outcome["error"].startswith("event is not valid JSON: "))
        bad.write_text(json.dumps({"submission_id": "x"}), encoding="utf-8")
        outcome = self.process(bad)
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["error"], "missing field: files")
        self.assertEqual(self.backend.calls, [])

    def test_unexpected_errors_are_not_swallowed(self):
        self.backend.script = [ZeroDivisionError()]
        with self.assertRaises(ZeroDivisionError):
            self.process(self.enqueue("string_repetition.py"))
        self.assertEqual(self.names("processing"), ["sub-1.json"])  # recover() picks it up on the next start
        self.assertEqual(rw.recover(self.queue), ["sub-1.json"])
        self.assertEqual((self.names("incoming"), self.names("processing")), (["sub-1.json"], []))


class TestReleaseCheckHandoff(QueueTest):
    def check(self):
        results, _ = rc.check_batch(self.queue / "review" / "outputs", GRADER / "rubrics",
                                    self.queue / "review" / "submissions", self.queue / "drafts.jsonl")
        return {r.submission_id: r for r in results}

    def test_a_consistent_review_passes_the_release_check(self):
        self.process(self.enqueue("string_repetition.py"))
        result = self.check()["sub-1"]
        self.assertEqual([f.code for f in result.findings if f.level == rc.BLOCKED], [])

    def test_a_review_that_contradicts_the_run_is_blocked(self):
        self.backend.script = [ai_result(runs=0, correct=1)]
        self.process(self.enqueue("string_repetition.py"))
        self.assertIn("contradicts_run", [f.code for f in self.check()["sub-1"].findings])

    def test_a_review_quoting_code_the_student_did_not_write_is_blocked(self):
        invented = ai_result()
        invented["rubricResults"][1]["evidence"] = ["`points = int(coins) * 10` converts the input."]
        self.backend.script = [invented]
        self.process(self.enqueue("string_repetition.py"))
        self.assertIn("quote_not_in_submission", [f.code for f in self.check()["sub-1"].findings])


class TestPassAndMain(QueueTest):
    def args(self, *extra):
        return ["--queue", str(self.queue), "--exercises", str(GRADER / "exercises"), "--rubrics", str(GRADER / "rubrics"),
                "--submissions-root", str(FIXTURES), "--sandbox", "none", *extra]

    def test_pass_handles_every_pending_event_in_order(self):
        self.enqueue("flawless.py", sid="b")
        self.enqueue("string_repetition.py", sid="a")
        (self.queue / "incoming" / "c.json.tmp").write_text("{", encoding="utf-8")  # still being written
        (self.queue / "incoming" / "d.json").mkdir()
        logged = []
        outcomes = rw.run_pass(self.queue, EXERCISES, RUBRICS, FIXTURES, self.backend, log=logged.append)
        self.assertEqual([o["event"] for o in outcomes], ["a.json", "b.json"])
        self.assertEqual(logged, ["a.json: ai_reviewed", "b.json: drafted"])
        self.assertEqual(self.names("incoming"), ["c.json.tmp", "d.json"])
        self.assertEqual(self.backend.calls[0]["model"], rm.MODEL)
        self.assertEqual(self.backend.calls[0]["effort"], rm.EFFORT)

    def test_pass_logs_the_failure_reason(self):
        (self.queue / "incoming" / "bad.json").write_text("{", encoding="utf-8")
        logged = []
        rw.run_pass(self.queue, EXERCISES, RUBRICS, FIXTURES, self.backend, log=logged.append)
        self.assertEqual(len(logged), 1)
        self.assertTrue(logged[0].startswith("bad.json: failed (event is not valid JSON: "))

    def test_one_pass_by_default(self):
        self.enqueue("string_repetition.py")
        sleeps = []
        with contextlib.redirect_stdout(io.StringIO()):
            code = rw.main(self.args("--model", "x", "--effort", "low"), backend=self.backend, sleep=sleeps.append)
        self.assertEqual((code, sleeps), (0, []))
        self.assertEqual((self.backend.calls[0]["model"], self.backend.calls[0]["effort"]), ("x", "low"))
        self.assertEqual(self.names("done"), ["sub-1.json"])

    def test_watch_keeps_checking_the_queue(self):
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 1:
                self.enqueue("string_repetition.py")  # arrives while the worker waits

        with contextlib.redirect_stdout(io.StringIO()):
            code = rw.main(self.args("--watch", "2.5", "--max-passes", "3"), backend=self.backend, sleep=sleep)
        self.assertEqual((code, sleeps), (0, [2.5, 2.5]))
        self.assertEqual(len(self.backend.calls), 1)

    def test_creates_the_queue_and_recovers_interrupted_events(self):
        shutil.rmtree(self.queue)
        rw.ensure_queue(self.queue)
        self.enqueue("string_repetition.py")
        shutil.move(self.queue / "incoming" / "sub-1.json", self.queue / "processing" / "sub-1.json")
        shutil.rmtree(self.queue / "done")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rw.main(self.args(), backend=self.backend)
        self.assertIn("sub-1.json: recovered from processing/ after an interrupted run", out.getvalue())
        self.assertEqual(self.names("done"), ["sub-1.json"])

    def test_refuses_to_start_without_a_subscription(self):
        self.enqueue("string_repetition.py")
        backend = FakeBackend(preflight_error=rm.SubscriptionRequired("signed in with 'api_key'"))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            code = rw.main(self.args(), backend=backend)
        self.assertEqual(code, 2)
        self.assertIn("Stopped: signed in with 'api_key'", err.getvalue())
        self.assertEqual(self.names("incoming"), ["sub-1.json"])
        self.assertFalse((self.queue / "drafts.jsonl").exists())  # nothing was even drafted

    def test_stops_cleanly_if_the_subscription_goes_away_mid_run(self):
        self.enqueue("string_repetition.py")
        self.backend.script = [rm.SubscriptionRequired("signed out")]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(rw.main(self.args("--watch", "1"), backend=self.backend, sleep=self.fail), 2)
        self.assertIn("Stopped: signed out", err.getvalue())
        self.assertEqual(self.names("incoming"), ["sub-1.json"])

    def test_every_folder_argument_is_required(self):
        full = self.args()
        for flag in ("--queue", "--exercises", "--rubrics", "--submissions-root"):
            i = full.index(flag)
            with self.subTest(flag), contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
                rw.main(full[:i] + full[i + 2:], backend=self.backend)
            self.assertIn(f"the following arguments are required: {flag}", err.getvalue())

    def test_sandbox_is_on_by_default_and_checked_first(self):
        seen = []
        saved = osub.build_sandbox
        osub.build_sandbox = lambda choice: seen.append(choice) or (None, ["the network is reachable"])
        self.enqueue("string_repetition.py")
        args = [a for a in self.args() if a not in ("--sandbox", "none")]
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                code = rw.main(args, backend=FakeBackend(preflight_error=AssertionError("must not get this far")))
        finally:
            osub.build_sandbox = saved
        self.assertEqual((code, seen), (3, ["bwrap"]))
        self.assertIn("Stopped: sandbox check failed: the network is reachable", err.getvalue())
        self.assertEqual(self.names("incoming"), ["sub-1.json"])
        self.assertFalse((self.queue / "drafts.jsonl").exists())

    def test_running_without_the_sandbox_is_loud(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            rw.main(self.args(), backend=self.backend)
        self.assertIn("Warning: --sandbox none. Student code runs unsandboxed", err.getvalue())

    def test_sandboxed_runner_is_used(self):
        box = object()
        saved = osub.build_sandbox
        osub.build_sandbox = lambda choice: (box, [])
        runners = []
        saved_pass = rw.run_pass
        rw.run_pass = lambda *a, **kw: runners.append(kw["runner"]) or []
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                rw.main([a for a in self.args() if a not in ("--sandbox", "none")], backend=self.backend)
        finally:
            osub.build_sandbox, rw.run_pass = saved, saved_pass
        self.assertEqual(runners[0].keywords, {"sandbox": box})
        self.assertEqual(err.getvalue(), "")

    def test_default_backends(self):
        built = []
        saved = rm.ClaudeCodeBackend, rm.default_client
        rm.ClaudeCodeBackend = lambda: built.append("claude-code") or self.backend
        rm.default_client = lambda: built.append("api") or (object(), ())
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rw.main(self.args())
                rw.main(self.args("--backend", "api"))
        finally:
            rm.ClaudeCodeBackend, rm.default_client = saved
        self.assertEqual(built, ["claude-code", "api"])


if __name__ == "__main__":
    unittest.main()
