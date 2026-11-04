"""
Tests for the bubblewrap sandbox (grader/sandbox.py) and how
on_submission.py uses it.

The command-building and probe-reading tests run anywhere. The tests marked
"real sandbox" run student programs inside bubblewrap and need it installed
(`apt install bubblewrap`). CI sets FOXCS_REQUIRE_SANDBOX=1, so there they
fail instead of skipping.

Run from the repository root with:
    python -m unittest discover -s grader/tests -v
"""

import contextlib
import dataclasses
import io
import itertools
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

GRADER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GRADER))
import on_submission as osub  # noqa: E402
import sandbox as sbx  # noqa: E402

FIXTURES = GRADER / "tests" / "on_submission_fixtures"
SPEC = dataclasses.replace(osub.load_exercises(GRADER / "exercises")["lesson_02_06_coins_to_points"], timeout_seconds=5)
HAVE_BWRAP = shutil.which("bwrap") is not None
REQUIRED = os.environ.get("FOXCS_REQUIRE_SANDBOX") == "1"
MARKER = "FOXCS-CANARY-7f3a91"


def real_sandbox(test):
    if REQUIRED or HAVE_BWRAP:
        return test
    return unittest.skip("bubblewrap is not installed")(test)


class NoSandbox:
    """Looks like a sandbox to run_program but isolates nothing, to prove the self-test notices."""

    name = "none"

    def prefix(self, path):
        return []

    def inside(self, path):
        return str(path)


def report(stdout):
    line = next(ln for ln in stdout.splitlines() if ln.startswith("REPORT "))
    return json.loads(line[len("REPORT "):])


class TestCommand(unittest.TestCase):
    def box(self, **kw):
        return sbx.Bwrap(bwrap=sys.executable, **kw)  # any real executable stands in for bwrap here

    def test_missing_bwrap_explains_how_to_install(self):
        with self.assertRaisesRegex(sbx.SandboxUnavailable, r"`no-such-bwrap` was not found\. Install it with: sudo apt install bubblewrap"):
            sbx.Bwrap(bwrap="no-such-bwrap")

    def test_isolation_flags(self):
        box = self.box(prefixes=("/usr",))
        args = box.prefix(Path("/subs/a/exercise.py"))
        self.assertEqual(args[0], shutil.which(sys.executable))
        for flag in ("--unshare-all", "--die-with-parent", "--new-session", "--clearenv"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--cap-drop") + 1], "ALL")
        self.assertEqual(args[-1], "--")
        pairs = list(itertools.pairwise(args))
        self.assertIn(("--tmpfs", "/tmp"), pairs)
        self.assertIn(("--remount-ro", "/"), pairs)
        self.assertIn(("--chdir", "/tmp"), pairs)
        self.assertIn(("--proc", "/proc"), pairs)
        self.assertIn(("--dev", "/dev"), pairs)
        # /tmp is mounted before the root is made read-only, so it stays writable
        self.assertLess(args.index("--tmpfs"), args.index("--remount-ro"))

    def test_only_the_submitted_file_is_bound_read_only(self):
        args = self.box(prefixes=("/usr",)).prefix(Path("/subs/a/exercise.py"))
        i = args.index(str(Path("/subs/a/exercise.py").resolve()))
        self.assertEqual(args[i - 1:i + 2], ["--ro-bind", str(Path("/subs/a/exercise.py").resolve()), "/sandbox/exercise.py"])
        self.assertNotIn("--bind", args)  # nothing is bound writable
        self.assertEqual(self.box().inside(Path("/x/y/my_file.py")), "/sandbox/my_file.py")

    def test_environment_is_cleared_then_set(self):
        args = self.box(prefixes=("/usr",)).prefix(Path("/s/e.py"))
        self.assertLess(args.index("--clearenv"), args.index("--setenv"))
        sets = [tuple(args[i + 1:i + 3]) for i, a in enumerate(args) if a == "--setenv"]
        self.assertEqual(sets, [("PYTHONHASHSEED", "0"), ("PYTHONIOENCODING", "utf-8")])

    def test_python_outside_usr_is_bound_read_only(self):
        args = self.box(prefixes=("/opt/py", "/usr/local", "/usr", "/home/u/venv")).prefix(Path("/s/e.py"))
        pairs = [tuple(args[i + 1:i + 3]) for i, a in enumerate(args) if a == "--ro-bind"]
        self.assertIn(("/usr", "/usr"), pairs)
        self.assertIn(("/opt/py", "/opt/py"), pairs)
        self.assertIn(("/home/u/venv", "/home/u/venv"), pairs)
        self.assertNotIn(("/usr/local", "/usr/local"), pairs)  # already inside /usr
        self.assertFalse(sbx._under_usr("/usrx"))

    def test_default_prefixes_are_this_python(self):
        self.assertEqual(self.box().prefixes, tuple(sorted({sys.base_prefix, sys.prefix})))

    def test_root_entries_follow_the_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "usr" / "bin").mkdir(parents=True)
            (root / "bin").symlink_to("usr/bin")
            (root / "lib").mkdir()
            # no /lib64 and no /sbin on this host
            args = self.box(prefixes=("/usr",), root=root).prefix(Path("/s/e.py"))
        self.assertIn(["--symlink", "usr/bin", "/bin"], [args[i:i + 3] for i in range(len(args))])
        self.assertIn(["--ro-bind", str(root / "lib"), "/lib"], [args[i:i + 3] for i in range(len(args))])
        self.assertNotIn("/lib64", args)
        self.assertNotIn("/sbin", args)


CLEAN_PROBE = {"network": False, "neighbor": False, "host_dirs": [], "env": ["LC_CTYPE", "PWD", "PYTHONHASHSEED", "PYTHONIOENCODING"],
               "grader_process": False, "writable_outside_tmp": [], "tmp_writable": True}


class TestCheckProbe(unittest.TestCase):

    def check(self, **changes):
        return sbx.check_probe(0, "noise\n" + json.dumps({**CLEAN_PROBE, **changes}) + "\n", "")

    def test_clean_report_has_no_problems(self):
        self.assertEqual(self.check(), [])

    def test_each_broken_property_is_named(self):
        cases = {
            "the network is reachable": {"network": True},
            "files next to the submission are visible": {"neighbor": True},
            "host folders are visible: /etc, /home": {"host_dirs": ["/etc", "/home"]},
            "unexpected environment variables: ANTHROPIC_API_KEY, HOME": {"env": ["HOME", "PWD", "ANTHROPIC_API_KEY"]},
            "the grader's own process is visible": {"grader_process": True},
            "writable outside /tmp: /sandbox/probe.py, /sandbox/probe-new-file": {"writable_outside_tmp": ["/sandbox/probe.py", "/sandbox/probe-new-file"]},
            "/tmp is not writable, so programs that save files would fail": {"tmp_writable": False},
        }
        for message, change in cases.items():
            with self.subTest(message):
                self.assertEqual(self.check(**change), [message])

    def test_every_problem_is_reported_at_once(self):
        self.assertEqual(len(self.check(network=True, neighbor=True, grader_process=True)), 3)

    def test_a_sandbox_that_did_not_run(self):
        self.assertEqual(sbx.check_probe(1, "", "bwrap: setting up uid map: Permission denied\n"),
                         ["sandbox did not run (exit 1): bwrap: setting up uid map: Permission denied"])
        self.assertEqual(sbx.check_probe(None, "not json", "  \n"), ["sandbox did not run (exit None): no output"])

    def test_probe_source_is_filled_in(self):
        source = sbx.probe_source(Path("/t/other.py"), 4242)
        self.assertIn("'/t/other.py'", source)
        self.assertIn("/proc/4242/environ", source)
        self.assertIn(repr(sbx.HOST_DIRS), source)
        compile(source, "probe", "exec")


class TestSelfTest(unittest.TestCase):
    def test_no_sandbox_is_caught(self):
        problems = osub.sandbox_self_test(NoSandbox())
        self.assertIn("files next to the submission are visible", problems)
        self.assertIn("the grader's own process is visible", problems)
        self.assertTrue(any(p.startswith("host folders are visible: ") for p in problems))
        self.assertTrue(any(p.startswith("writable outside /tmp: ") for p in problems))

    def test_build_sandbox_choices(self):
        self.assertEqual(osub.build_sandbox("none"), (None, []))
        saved = sbx.Bwrap

        def unavailable():
            raise sbx.SandboxUnavailable("no bwrap here")

        sbx.Bwrap = unavailable
        try:
            self.assertEqual(osub.build_sandbox("bwrap"), (None, ["no bwrap here"]))
        finally:
            sbx.Bwrap = saved

    def test_make_runner(self):
        self.assertIs(osub.make_runner(None), osub.run_program)
        box = NoSandbox()
        runner = osub.make_runner(box)
        self.assertIs(runner.func, osub.run_program)
        self.assertEqual(runner.keywords, {"sandbox": box})


@real_sandbox
class TestRealSandbox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box, problems = osub.build_sandbox("bwrap")
        if problems:
            raise AssertionError(f"sandbox self-test failed: {problems}")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.target = self.dir / "exercise.py"
        shutil.copy(FIXTURES / "escape_attempt.py", self.target)
        (self.dir / "another_student.py").write_text(f"token = '{MARKER}'\n", encoding="utf-8")
        os.environ["FOXCS_TEST_SECRET"] = MARKER
        self.addCleanup(os.environ.pop, "FOXCS_TEST_SECRET", None)

    def test_self_test_passes(self):
        self.assertEqual(osub.sandbox_self_test(self.box), [])

    def test_escape_attempt_finds_nothing(self):
        before = self.target.read_text(encoding="utf-8")
        run = osub.run_program(self.target, "3\n", SPEC, self.box)
        self.assertEqual(run.exit_code, 0, run.stderr)
        self.assertIn("You have 30 points.", run.stdout)
        found = report(run.stdout)
        self.assertNotIn(MARKER, run.stdout)
        self.assertEqual([f for f in found["found"] if not f.startswith("environ ")], [])  # no other files visible
        self.assertNotEqual(found["network"], "open")
        self.assertFalse(found["tampered"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), before)

    def test_without_the_sandbox_the_same_program_succeeds(self):
        """The control: proves it is the sandbox, not the program, that stops the escape."""
        run = osub.run_program(self.target, "3\n", SPEC)
        self.assertIn(MARKER, run.stdout)
        self.assertTrue(report(run.stdout)["tampered"])

    def test_grades_are_the_same_inside_the_sandbox(self):
        event = osub.SubmissionEvent("s", "u", "a", "t", ())
        runner = osub.make_runner(self.box)
        for fixture in ("flawless.py", "string_repetition.py", "syntax_error.py", "type_error.py", "commas_alternative.py"):
            with self.subTest(fixture):
                plain = osub.grade_file(event, SPEC, (FIXTURES / fixture).resolve())
                boxed = osub.grade_file(event, SPEC, (FIXTURES / fixture).resolve(), runner)
                self.assertEqual((boxed.band, boxed.score, boxed.tests), (plain.band, plain.score, plain.tests))

    def test_limits_still_apply_inside(self):
        loop = osub.run_program((FIXTURES / "infinite_loop.py").resolve(), "1\n", dataclasses.replace(SPEC, timeout_seconds=1), self.box)
        self.assertTrue(loop.timed_out)
        hog = osub.run_program((FIXTURES / "memory_hog.py").resolve(), "1\n", SPEC, self.box)
        self.assertNotEqual(hog.exit_code, 0)
        self.assertIn("MemoryError", hog.stderr)

    def test_programs_can_still_use_tmp(self):
        script = self.dir / "uses_tmp.py"
        script.write_text("open('notes.txt', 'w').write('hi')\nprint(open('notes.txt').read(), __import__('os').getcwd())\n", encoding="utf-8")
        run = osub.run_program(script, "", SPEC, self.box)
        self.assertEqual(run.stdout.strip(), "hi /tmp")
        self.assertFalse((self.dir / "notes.txt").exists())

    def test_cli_grades_inside_the_sandbox(self):
        event = {"submission_id": "s1", "student_id": "STUDENT-0001", "assignment_id": SPEC.assignment_id,
                 "submitted_at": "2026-01-12T14:00:00Z", "files": [{"name": "exercise.py", "path": str(self.target)}]}
        event_path = self.dir / "event.json"
        event_path.write_text(json.dumps(event), encoding="utf-8")
        argv = [str(event_path), "--exercises", str(GRADER / "exercises"), "--submissions-root", str(self.dir),
                "--store", str(self.dir / "store.jsonl"), "--sandbox", "bwrap"]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(osub.main(argv), 0)
        self.assertEqual(json.loads(out.getvalue())["band"], "flawless")
        self.assertNotIn(MARKER, out.getvalue())


class TestCliSandboxRefusal(unittest.TestCase):
    def test_failed_self_test_refuses_to_grade(self):
        saved = osub.build_sandbox
        osub.build_sandbox = lambda choice: (None, ["the network is reachable", "host folders are visible: /etc"])
        try:
            with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out:
                code = osub.main([str(Path(tmp) / "never-read.json"), "--exercises", str(GRADER / "exercises"),
                                  "--submissions-root", tmp, "--store", str(Path(tmp) / "s.jsonl"), "--sandbox", "bwrap"])
                self.assertFalse((Path(tmp) / "s.jsonl").exists())
        finally:
            osub.build_sandbox = saved
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out.getvalue()), {
            "accepted": False, "error": "sandbox check failed: the network is reachable; host folders are visible: /etc"})


if __name__ == "__main__":
    unittest.main()
