#!/usr/bin/env python3
"""
Queue worker for on-submission grading, with AI review on a Claude subscription.

A Moodle event observer drops one JSON file per submission into
queue/incoming/. This worker picks each one up, grades it deterministically
with on_submission.py, and sends only the drafts that need judgment
(runs_with_issues) to Claude Code in non-interactive mode. Nothing is
released: every result waits for the release check and the teacher.

    python grader/review_worker.py --queue /srv/foxcs-queue \\
        --exercises grader/exercises --rubrics grader/rubrics \\
        --submissions-root /srv/moodle-submissions --watch 30

Queue layout (created on first run):

    incoming/      new events. Writers create <name>.tmp and rename, so a
                   half-written file is never read
    processing/    the event being worked on
    done/          handled events
    failed/        events whose AI review failed, each with <name>.error.json.
                   There are no automatic retries: move one back to incoming/
                   to try again, or grade it by hand
    drafts.jsonl   on_submission.py's draft store
    review/outputs/<submission_id>.json      AI review results
    review/submissions/<submission_id>/...   the reviewed files, so the
                                             release check can verify quotes

Then, before any grade is pushed:

    python grader/release_check.py --outputs QUEUE/review/outputs \\
        --rubrics grader/rubrics --submissions QUEUE/review/submissions \\
        --drafts QUEUE/drafts.jsonl

The AI step uses the same backend, prompt and schema the eval harness
measures (evals/run_model.py), so what gets evaluated is what runs. On the
default claude-code backend it refuses to start unless Claude Code is
signed in to a subscription, and it stops, putting the event back, if that
ever changes mid-run. Student code runs in the bubblewrap sandbox by default
(grader/sandbox.py), and the worker refuses to start if the sandbox's
self-test finds any isolation property broken. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

GRADER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(GRADER_DIR))
sys.path.insert(0, str(GRADER_DIR.parent / "evals"))
import on_submission as osub  # noqa: E402
import run_model as rm  # noqa: E402

AI_REVIEW_BANDS = ("runs_with_issues",)  # flawless and does_not_run are settled facts; held drafts go straight to the teacher
FOLDERS = ("incoming", "processing", "done", "failed", "review/outputs", "review/submissions")


def ensure_queue(queue: Path) -> None:
    for name in FOLDERS:
        (queue / name).mkdir(parents=True, exist_ok=True)


def recover(queue: Path) -> list[str]:
    """Events left in processing/ by a crash or a restart go back to incoming/."""
    moved = []
    for path in sorted((queue / "processing").glob("*.json")):
        shutil.move(path, queue / "incoming" / path.name)
        moved.append(path.name)
    return moved


def reviewed_hash(queue: Path, submission_id: str) -> str | None:
    """Content hash of the file behind the stored AI review, if there is one."""
    path = queue / "review" / "outputs" / f"{submission_id}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("reviewedBy", {}).get("contentHash")


def pending(queue: Path) -> list[Path]:
    """Events waiting in incoming/, oldest name first. Temporary files are skipped."""
    return sorted(p for p in (queue / "incoming").glob("*.json") if p.is_file())


def load_rubrics(directory: Path) -> dict[str, dict]:
    return {r["id"]: r for r in (json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(directory).glob("*.json")))}


def _fail(queue: Path, claimed: Path, reason: str) -> dict:
    shutil.move(claimed, queue / "failed" / claimed.name)
    rm.write_json(queue / "failed" / f"{claimed.stem}.error.json", {"event": claimed.name, "error": reason})
    return {"event": claimed.name, "status": "failed", "error": reason}


def ai_review(draft: dict, data: dict, rubric: dict, backend, model: str, effort: str, queue: Path) -> dict:
    """Grades the draft's file with the AI backend and writes the result for the release check."""
    event = osub.parse_event(data)
    path = Path(event.files[0].path).resolve()
    name = event.files[0].name
    code = path.read_text(encoding="utf-8")
    case = {"submission": {"files": {name: code}, "explanation": ""}, "programRuns": draft["tests"]}
    result, usage, billed = backend.grade(case, rubric, model, effort)
    sid = event.submission_id
    result.update(submission_id=sid, student_id=event.student_id, assignment_id=event.assignment_id,
                  release=osub.RELEASE_STATUS,
                  reviewedBy={"backend": backend.name, "model": model, "effort": effort, "contentHash": draft["content_hash"]})
    rm.write_json(queue / "review" / "outputs" / f"{sid}.json", result)
    copy_dir = queue / "review" / "submissions" / sid
    shutil.rmtree(copy_dir, ignore_errors=True)
    copy_dir.mkdir()  # review/submissions/ exists: ensure_queue() made it
    (copy_dir / Path(name).name).write_text(code, encoding="utf-8")
    return {"usage": usage, "cost_usd": billed}


def process_event(path: Path, queue: Path, exercises: dict, rubrics: dict, submissions_root: Path,
                  store: osub.ResultStore, backend, model: str = rm.MODEL, effort: str = rm.EFFORT,
                  runner=osub.run_program) -> dict:
    """Handles one queued event. Returns what happened to it."""
    claimed = queue / "processing" / path.name
    shutil.move(path, claimed)
    try:
        data = json.loads(claimed.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _fail(queue, claimed, f"event is not valid JSON: {exc}")
    draft = osub.handle_submission(data, exercises, submissions_root, store, runner)
    if draft.get("accepted") is False:
        return _fail(queue, claimed, draft["error"])
    outcome = {"event": claimed.name, "submission_id": draft["submission_id"], "band": draft["band"]}
    rubric = rubrics.get(draft["assignment_id"])
    if draft["band"] not in AI_REVIEW_BANDS:
        outcome["status"] = "duplicate" if draft.get("duplicate") else "drafted"
    elif rubric is None:
        outcome["status"] = "drafted"
        outcome["note"] = f"no AI rubric for '{draft['assignment_id']}', so it waits for the teacher"
    elif reviewed_hash(queue, draft["submission_id"]) == draft["content_hash"]:
        outcome["status"] = "duplicate"  # this exact file already has an AI review
    else:
        # Reached for new work, and for a requeued event whose earlier review failed or was interrupted.
        try:
            outcome.update(ai_review(draft, data, rubric, backend, model, effort, queue))
        except rm.GradingFailed as exc:
            return {**_fail(queue, claimed, f"AI review failed: {exc}"), "submission_id": draft["submission_id"]}
        except rm.SubscriptionRequired:
            shutil.move(claimed, queue / "incoming" / claimed.name)  # put it back untouched
            raise
        outcome["status"] = "ai_reviewed"
    shutil.move(claimed, queue / "done" / claimed.name)
    return outcome


def run_pass(queue: Path, exercises: dict, rubrics: dict, submissions_root: Path, backend,
             model: str = rm.MODEL, effort: str = rm.EFFORT, log=print, runner=osub.run_program) -> list[dict]:
    store = osub.ResultStore(queue / "drafts.jsonl")
    outcomes = []
    for path in pending(queue):
        outcome = process_event(path, queue, exercises, rubrics, submissions_root, store, backend, model, effort, runner)
        outcomes.append(outcome)
        log(f"{outcome['event']}: {outcome['status']}" + (f" ({outcome['error']})" if "error" in outcome else ""))
    return outcomes


def main(argv: list[str] | None = None, backend=None, sleep=time.sleep) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--exercises", type=Path, required=True, help="Folder of exercise spec JSON files")
    parser.add_argument("--rubrics", type=Path, required=True, help="Folder of assignment rubric JSON files for AI review")
    parser.add_argument("--submissions-root", type=Path, required=True, help="Only files inside this folder are graded")
    parser.add_argument("--backend", default="claude-code", choices=["claude-code", "api"],
                        help="claude-code uses your Claude subscription (default); api is billed per token")
    parser.add_argument("--model", default=rm.MODEL)
    parser.add_argument("--effort", default=rm.EFFORT, choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--sandbox", choices=["bwrap", "none"], default="bwrap",
                        help="bwrap (default) runs student code in a bubblewrap sandbox; none is for trusted test files only")
    parser.add_argument("--watch", type=float, metavar="SECONDS", help="Keep running, checking the queue this often")
    parser.add_argument("--max-passes", type=int, help=argparse.SUPPRESS)  # for tests: stop watching after N passes
    args = parser.parse_args(argv)
    # The sandbox is checked first: student code must never run next to the subscription token unprotected.
    sandbox, problems = osub.build_sandbox(args.sandbox)
    if problems:
        print(f"Stopped: sandbox check failed: {'; '.join(problems)}", file=sys.stderr)
        return 3
    if sandbox is None:
        print("Warning: --sandbox none. Student code runs unsandboxed, next to the subscription token.", file=sys.stderr)
    runner = osub.make_runner(sandbox)
    if backend is None:
        backend = rm.ClaudeCodeBackend() if args.backend == "claude-code" else rm.ApiBackend(*rm.default_client())
    ensure_queue(args.queue)
    for name in recover(args.queue):
        print(f"{name}: recovered from processing/ after an interrupted run")
    exercises, rubrics = osub.load_exercises(args.exercises), load_rubrics(args.rubrics)
    passes = 0
    try:
        backend.preflight()
        while True:
            run_pass(args.queue, exercises, rubrics, args.submissions_root, backend, args.model, args.effort, runner=runner)
            passes += 1
            if args.watch is None or passes == args.max_passes:
                return 0
            sleep(args.watch)
    except rm.SubscriptionRequired as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
