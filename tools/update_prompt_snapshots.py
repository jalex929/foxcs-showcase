#!/usr/bin/env python3
"""
Rewrite the prompt snapshots in evals/tests/snapshots/ from evals/run_model.py.

Run this only after an intended prompt change, then review the diff: the
snapshot files are how a prompt change shows up in code review.

    python tools/update_prompt_snapshots.py
"""

import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parents[1] / "evals"
sys.path.insert(0, str(EVALS))
import run_model as rm  # noqa: E402

SNAPSHOTS = EVALS / "tests" / "snapshots"
CASE_ID = "05_prompt_injection"


def main() -> None:
    case = next(c for c in rm.load_cases() if c["id"] == CASE_ID)
    assignment = rm.load_assignments()[case["assignmentId"]]
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    (SNAPSHOTS / "system_prompt.txt").write_text(rm.SYSTEM_PROMPT + "\n", encoding="utf-8")
    (SNAPSHOTS / f"user_message_{CASE_ID}.txt").write_text(rm.build_user_message(case, assignment) + "\n", encoding="utf-8")
    print(f"Updated snapshots in {SNAPSHOTS}. Review the diff before committing.")


if __name__ == "__main__":
    main()
