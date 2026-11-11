#!/usr/bin/env python3
"""
FoxCS on-submission auto-grader for Coding Exercises.

Grades one submission event the moment it arrives (for example, when a
student uploads a .py file to a Moodle Assignment), instead of waiting for a
weekly batch. Standard library only.

What it does, in order:

1. Validates the event (required fields, types) and the submitted file
   (exactly one .py file, inside the submissions folder, under the size
   limit, valid UTF-8).
2. Looks up the exercise spec for the assignment. No spec means no grade:
   the submission is held for review rather than scored against nothing.
3. Runs the student's program once per test case in a separate Python
   process with a time limit, a memory limit, and isolated mode, feeding
   the test's stdin and checking stdout.
4. Scores with the Coding Exercise rubric standard: 50% for running without
   errors, 50% for correct output. Three bands:
     flawless          runs and every test passes: full credit
     does_not_run      a syntax or runtime error: no credit (the harshest band)
     runs_with_issues  runs, some tests fail: runs credit plus partial
                       correct credit, held for review because "how close"
                       is a judgment call
5. Returns a DRAFT. Every result is pending teacher approval. This module
   never writes a grade anywhere a student can see it.

It is idempotent: the same submission delivered twice returns the stored
result without re-running anything, while a resubmission (same ID, new
file contents) is graded again.

Time, memory, and output limits stop runaway programs, not hostile ones.
For untrusted code, --sandbox bwrap also runs each program in a bubblewrap
sandbox (sandbox.py): no network, no view of other files or processes. It
self-tests before grading anything. See docs/on-submission-grading.md.

Usage:
    python grader/on_submission.py EVENT_JSON --exercises DIR --submissions-root DIR --store FILE [--sandbox bwrap]
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import sandbox as sbx

RUBRIC_RUNS = "runs_without_errors"
RUBRIC_CORRECT = "correct_output"
RELEASE_STATUS = "pending_teacher_approval"
MAX_CAPTURED_CHARS = 10_000
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_BYTES = 50_000
DEFAULT_MEMORY_LIMIT_MB = 256

# Runs inside the child process. Applies resource limits where the platform
# supports them, then runs the student file as __main__. Kept as a string so
# the limits apply to the child only, never to the grader itself.
CHILD_BOOTSTRAP = """
import runpy, sys
try:
    import resource
    mem = int(sys.argv[2]) * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
except (ImportError, ValueError, OSError):
    pass
target = sys.argv[1]
sys.argv = [target]
runpy.run_path(target, run_name="__main__")
"""


class InvalidEvent(ValueError):
    """The submission event itself is malformed."""


class InvalidExercise(ValueError):
    """An exercise spec file is malformed."""


@dataclass(frozen=True)
class TestCase:
    name: str
    stdin: str
    expected_contains: tuple[str, ...]
    expected_not_contains: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExerciseSpec:
    assignment_id: str
    points_possible: float
    tests: tuple[TestCase, ...]
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_bytes: int = DEFAULT_MAX_BYTES
    memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB


@dataclass(frozen=True)
class SubmittedFile:
    name: str
    path: str


@dataclass(frozen=True)
class SubmissionEvent:
    submission_id: str
    student_id: str
    assignment_id: str
    submitted_at: str
    files: tuple[SubmittedFile, ...]


@dataclass(frozen=True)
class RunResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool


@dataclass
class GradeDraft:
    submission_id: str
    student_id: str
    assignment_id: str
    content_hash: str | None
    band: str | None
    rubricResults: list = field(default_factory=list)
    score: dict | None = None
    tests: list = field(default_factory=list)
    feedback: dict = field(default_factory=dict)
    review: dict = field(default_factory=dict)
    release: str = RELEASE_STATUS

    def to_dict(self) -> dict:
        return asdict(self)


Runner = Callable[[Path, str, ExerciseSpec], RunResult]


# ---------------------------------------------------------------- loading

def _require(data: dict, key: str, kind, error: type[ValueError]):
    if key not in data:
        raise error(f"missing field: {key}")
    value = data[key]
    if not isinstance(value, kind) or isinstance(value, bool):
        names = kind.__name__ if isinstance(kind, type) else " or ".join(k.__name__ for k in kind)
        raise error(f"field {key} must be {names}")
    if isinstance(value, str) and not value.strip():
        raise error(f"field {key} must not be empty")
    return value


def parse_event(data: object) -> SubmissionEvent:
    if not isinstance(data, dict):
        raise InvalidEvent("event must be a JSON object")
    files_raw = _require(data, "files", list, InvalidEvent)
    files = []
    for item in files_raw:
        if not isinstance(item, dict):
            raise InvalidEvent("each file must be an object with name and path")
        files.append(SubmittedFile(
            name=_require(item, "name", str, InvalidEvent),
            path=_require(item, "path", str, InvalidEvent),
        ))
    return SubmissionEvent(
        submission_id=_require(data, "submission_id", str, InvalidEvent),
        student_id=_require(data, "student_id", str, InvalidEvent),
        assignment_id=_require(data, "assignment_id", str, InvalidEvent),
        submitted_at=_require(data, "submitted_at", str, InvalidEvent),
        files=tuple(files),
    )


def parse_exercise(data: object) -> ExerciseSpec:
    if not isinstance(data, dict):
        raise InvalidExercise("exercise must be a JSON object")
    tests_raw = _require(data, "tests", list, InvalidExercise)
    if not tests_raw:
        raise InvalidExercise("exercise needs at least one test")
    tests = []
    for t in tests_raw:
        if not isinstance(t, dict):
            raise InvalidExercise("each test must be an object")
        expected = _require(t, "expectedContains", list, InvalidExercise)
        forbidden = t.get("expectedNotContains", [])
        stdin = t.get("stdin", "")
        if not expected:
            raise InvalidExercise("expectedContains must not be empty")
        if not isinstance(stdin, str):
            raise InvalidExercise("stdin must be a string")
        if not isinstance(forbidden, list) or not all(isinstance(x, str) and x for x in expected + forbidden):
            raise InvalidExercise("expected output entries must be non-empty strings")
        tests.append(TestCase(
            name=_require(t, "name", str, InvalidExercise),
            stdin=stdin,
            expected_contains=tuple(expected),
            expected_not_contains=tuple(forbidden),
        ))
    points = _require(data, "pointsPossible", (int, float), InvalidExercise)
    if points <= 0:
        raise InvalidExercise("pointsPossible must be positive")
    timeout = data.get("timeoutSeconds", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise InvalidExercise("timeoutSeconds must be a positive number")
    return ExerciseSpec(
        assignment_id=_require(data, "assignmentId", str, InvalidExercise),
        points_possible=float(points),
        tests=tuple(tests),
        timeout_seconds=float(timeout),
        max_bytes=int(data.get("maxBytes", DEFAULT_MAX_BYTES)),
        memory_limit_mb=int(data.get("memoryLimitMb", DEFAULT_MEMORY_LIMIT_MB)),
    )


def load_exercises(directory: Path) -> dict[str, ExerciseSpec]:
    specs: dict[str, ExerciseSpec] = {}
    for path in sorted(Path(directory).glob("*.json")):
        spec = parse_exercise(json.loads(path.read_text(encoding="utf-8")))
        if spec.assignment_id in specs:
            raise InvalidExercise(f"duplicate assignmentId: {spec.assignment_id}")
        specs[spec.assignment_id] = spec
    return specs


# ---------------------------------------------------------------- running

def _clip(text: str | bytes | None) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if len(text) > MAX_CAPTURED_CHARS:
        return text[:MAX_CAPTURED_CHARS] + "\n[output truncated]"
    return text


def run_program(path: Path, stdin: str, spec: ExerciseSpec, sandbox: sbx.Bwrap | None = None) -> RunResult:
    """Runs one student file in a separate, isolated Python process,
    inside the sandbox when one is given."""
    if sandbox is None:
        wrapper, target, cwd = [], str(path), str(path.parent)
    else:
        wrapper, target, cwd = sandbox.prefix(path), sandbox.inside(path), None
    try:
        proc = subprocess.run(
            [*wrapper, sys.executable, "-I", "-c", CHILD_BOOTSTRAP, target, str(spec.memory_limit_mb)],
            input=stdin,
            capture_output=True,
            encoding="utf-8",  # implies text mode
            errors="replace",
            timeout=spec.timeout_seconds,
            cwd=cwd,
            env=dict(sbx.CHILD_ENV),
        )
    except subprocess.TimeoutExpired as exc:
        return RunResult(None, _clip(exc.stdout), _clip(exc.stderr), timed_out=True)
    return RunResult(proc.returncode, _clip(proc.stdout), _clip(proc.stderr), timed_out=False)


def make_runner(sandbox: sbx.Bwrap | None) -> Runner:
    return run_program if sandbox is None else functools.partial(run_program, sandbox=sandbox)


def sandbox_self_test(sandbox: sbx.Bwrap) -> list[str]:
    """Runs a probe program in the sandbox. Returns every isolation property
    that does not hold; an empty list means it is safe to grade with."""
    with tempfile.TemporaryDirectory(prefix="foxcs-probe-") as tmp:
        neighbor = Path(tmp) / "another_students_file.py"
        neighbor.write_text("print('private')\n", encoding="utf-8")
        probe = Path(tmp) / "probe.py"
        allowed = tuple(x for x in getattr(sandbox, "prefixes", ()) if not sbx._under_usr(x))
        probe.write_text(sbx.probe_source(neighbor, os.getpid(), allowed), encoding="utf-8")
        spec = ExerciseSpec("sandbox-self-test", 0, (), timeout_seconds=15)
        run = run_program(probe, "", spec, sandbox)
    return sbx.check_probe(run.exit_code, run.stdout, run.stderr)


def build_sandbox(choice: str) -> tuple[sbx.Bwrap | None, list[str]]:
    """The sandbox for a --sandbox choice, and any reason not to grade with it."""
    if choice == "none":
        return None, []
    try:
        sandbox = sbx.Bwrap()
    except sbx.SandboxUnavailable as exc:
        return None, [str(exc)]
    return sandbox, sandbox_self_test(sandbox)


# ---------------------------------------------------------------- grading


def _error_summary(stderr: str) -> str:
    lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    return lines[-1].strip() if lines else "the program exited with an error"


def _held(event: SubmissionEvent, reasons: list[str], content_hash: str | None = None) -> GradeDraft:
    """A result with no score: the teacher decides."""
    return GradeDraft(
        submission_id=event.submission_id,
        student_id=event.student_id,
        assignment_id=event.assignment_id,
        content_hash=content_hash,
        band=None,
        feedback={"nextStep": "Your teacher will review this submission."},
        review={"teacherReviewRequired": True, "reasons": reasons, "overallConfidence": "low"},
    )


def validate_file(event: SubmissionEvent, spec: ExerciseSpec, submissions_root: Path) -> tuple[Path | None, str | None]:
    """Returns (path, None) for a gradable file, or (None, reason)."""
    py_files = [f for f in event.files if f.name.lower().endswith(".py")]
    if not event.files:
        return None, "No file was submitted."
    if len(py_files) != 1 or len(event.files) != 1:
        return None, f"Expected exactly one .py file, got: {', '.join(f.name for f in event.files)}."
    root = Path(submissions_root).resolve()
    path = Path(py_files[0].path).resolve()
    if not path.is_relative_to(root):
        return None, "Submitted file path is outside the submissions folder."
    if not path.is_file():
        return None, "Submitted file could not be found."
    size = path.stat().st_size
    if size == 0:
        return None, "Submitted file is empty."
    if size > spec.max_bytes:
        return None, f"Submitted file is {size} bytes, over the {spec.max_bytes}-byte limit."
    try:
        path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return None, "Submitted file is not valid UTF-8 text."
    return path, None


def check_output(stdout: str, test: TestCase) -> tuple[bool, list[str]]:
    problems = [f"expected output to contain {s!r}" for s in test.expected_contains if s not in stdout]
    problems += [f"output should not contain {s!r}" for s in test.expected_not_contains if s in stdout]
    return not problems, problems


def grade_file(event: SubmissionEvent, spec: ExerciseSpec, path: Path, runner: Runner = run_program) -> GradeDraft:
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    half = spec.points_possible / 2
    results = []
    crashed: RunResult | None = None
    timed_out = False
    for test in spec.tests:
        run = runner(path, test.stdin, spec)
        record = {"name": test.name, "exitCode": run.exit_code, "timedOut": run.timed_out}
        if run.timed_out:
            timed_out = True
            record.update(passed=False, problems=[f"did not finish within {spec.timeout_seconds:g} seconds"])
        elif run.exit_code != 0:
            crashed = crashed or run
            record.update(passed=False, problems=[_error_summary(run.stderr)])
        else:
            passed, problems = check_output(run.stdout, test)
            record.update(passed=passed, problems=problems)
        results.append(record)

    if timed_out:
        draft = _held(event, ["A test run timed out (possible infinite loop or waiting for unexpected input)."], content_hash)
        draft.tests = results
        return draft

    passed_count = sum(r["passed"] for r in results)
    total = len(results)
    if crashed is not None:
        band, runs_pts, correct_pts = "does_not_run", 0.0, 0.0
        error = _error_summary(crashed.stderr)
        feedback = {
            "overallFeedback": f"Your program stopped with an error before it could finish: {error}",
            "nextStep": "Run your file, read the last line of the error message, fix that line, and run it again before resubmitting.",
        }
        review = {"teacherReviewRequired": False, "reasons": [], "overallConfidence": "high"}
    elif passed_count == total:
        band, runs_pts, correct_pts = "flawless", half, half
        feedback = {
            "overallFeedback": f"Your program runs without errors and produces the expected output for all {total} test inputs.",
            "nextStep": "Try one more input of your own and predict the output before you run it.",
        }
        review = {"teacherReviewRequired": False, "reasons": [], "overallConfidence": "high"}
    else:
        band, runs_pts = "runs_with_issues", half
        correct_pts = round(half * passed_count / total, 2)
        # A list, not next(...): a failing test always exists on this path, so
        # next()'s "ran out" branch could never run and would hold coverage below 100%.
        first_problem = [r for r in results if not r["passed"]][0]  # noqa: RUF015
        feedback = {
            "overallFeedback": f"Your program runs without errors, but {total - passed_count} of {total} test inputs did not produce the expected output.",
            "nextStep": f"Check the '{first_problem['name']}' case: {first_problem['problems'][0]}.",
        }
        review = {
            "teacherReviewRequired": True,
            "reasons": [f"Runs, but {total - passed_count} of {total} test inputs failed. Credit on the correct-output half is a judgment call under the rubric."],
            "overallConfidence": "medium",
        }

    total_pts = round(runs_pts + correct_pts, 2)
    return GradeDraft(
        submission_id=event.submission_id,
        student_id=event.student_id,
        assignment_id=event.assignment_id,
        content_hash=content_hash,
        band=band,
        rubricResults=[
            {"criterionId": RUBRIC_RUNS, "pointsEarned": runs_pts, "pointsPossible": half,
             "evidence": [f"{sum(1 for r in results if r['exitCode'] == 0)} of {total} runs exited cleanly."]},
            {"criterionId": RUBRIC_CORRECT, "pointsEarned": correct_pts, "pointsPossible": half,
             "evidence": [f"{passed_count} of {total} test inputs produced the expected output."]},
        ],
        score={"standardPointsEarned": total_pts, "standardPointsPossible": spec.points_possible,
               "bonusRecommended": 0, "finalPointsEarned": total_pts},
        tests=results,
        feedback=feedback,
        review=review,
    )


# ---------------------------------------------------------------- storage

class ResultStore:
    """Append-only JSON Lines store. The latest record per submission wins."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def latest(self, submission_id: str) -> dict | None:
        if not self.path.exists():
            return None
        found = None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                if record.get("submission_id") == submission_id:
                    found = record
        return found

    def append(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------- entry point

def handle_submission(
    event_data: object,
    exercises: dict[str, ExerciseSpec],
    submissions_root: Path,
    store: ResultStore,
    runner: Runner = run_program,
) -> dict:
    """Grades one submission event. Never raises for bad input: anything
    that cannot be graded comes back held for teacher review."""
    try:
        event = parse_event(event_data)
    except InvalidEvent as exc:
        return {"accepted": False, "error": str(exc), "release": RELEASE_STATUS}

    spec = exercises.get(event.assignment_id)
    if spec is None:
        draft = _held(event, [f"No exercise spec for assignment '{event.assignment_id}'. Not scored against a missing rubric."])
        store.append(draft.to_dict())
        return draft.to_dict()

    path, problem = validate_file(event, spec, submissions_root)
    if problem:
        draft = _held(event, [problem])
        store.append(draft.to_dict())
        return draft.to_dict()

    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    previous = store.latest(event.submission_id)
    if previous is not None and previous.get("content_hash") == content_hash:
        return {**previous, "duplicate": True}

    try:
        draft = grade_file(event, spec, path, runner)
    except Exception as exc:  # the handler must never take the queue down
        draft = _held(event, [f"Grader error, needs manual grading: {type(exc).__name__}: {exc}"], content_hash)
    store.append(draft.to_dict())
    return draft.to_dict()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("event", type=Path, help="Submission event JSON file")
    parser.add_argument("--exercises", type=Path, required=True, help="Folder of exercise spec JSON files")
    parser.add_argument("--submissions-root", type=Path, required=True, help="Only files inside this folder are graded")
    parser.add_argument("--store", type=Path, required=True, help="JSON Lines file of grading drafts")
    parser.add_argument("--sandbox", choices=["none", "bwrap"], default="none",
                        help="bwrap runs each program in a bubblewrap sandbox; use it for any untrusted code")
    args = parser.parse_args(argv)
    sandbox, problems = build_sandbox(args.sandbox)
    if problems:
        print(json.dumps({"accepted": False, "error": "sandbox check failed: " + "; ".join(problems)}))
        return 3
    try:
        event_data = json.loads(args.event.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"accepted": False, "error": f"could not read event: {exc}"}))
        return 2
    result = handle_submission(event_data, load_exercises(args.exercises), args.submissions_root, ResultStore(args.store),
                               make_runner(sandbox))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("accepted", True) else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
