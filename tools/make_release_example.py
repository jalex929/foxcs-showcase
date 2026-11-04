#!/usr/bin/env python3
"""
Build the committed release-check demo in examples/release-check/.

A batch of eight synthetic grading results: the six eval reference
judgments, plus two deliberately flawed results that show what the release
check catches (evidence quoting code the student never wrote, and full marks
whose rationale names a problem). Then runs the release check on it.

    python tools/make_release_example.py
"""

import copy
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "grader"))
import release_check as rc  # noqa: E402

EVALS = ROOT / "evals"
DEMO = ROOT / "examples" / "release-check"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def add(batch: Path, sid: str, out: dict, case: dict) -> None:
    out = copy.deepcopy(out)
    out.update(submission_id=sid, student_id=f"STUDENT-{sid[:2]}", assignment_id=case["assignmentId"])
    (batch / "outputs" / f"{sid}.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    sub = batch / "submissions" / sid
    sub.mkdir(parents=True)
    for name, code in case["submission"]["files"].items():
        (sub / name).write_text(code, encoding="utf-8")
    (sub / "explanation.txt").write_text(case["submission"]["explanation"] + "\n", encoding="utf-8")


def build(dest: Path) -> None:
    batch = dest / "batch"
    if dest.exists():
        shutil.rmtree(dest)
    (batch / "outputs").mkdir(parents=True)
    cases = {p.stem: load(p) for p in sorted((EVALS / "cases").glob("*.json"))}
    for cid, case in cases.items():
        add(batch, cid, load(EVALS / "reference-outputs" / f"{cid}.json"), case)

    # Flaw 1: the evidence quotes code that is not in the student's file.
    invented = load(EVALS / "reference-outputs" / "03_partial_solution.json")
    invented["rubricResults"][1]["evidence"] = ["add_points ends with `return score * points`."]
    add(batch, "07_invented_quote", invented, cases["03_partial_solution"])

    # Flaw 2: full marks on a criterion whose rationale names a problem.
    contradicts = load(EVALS / "reference-outputs" / "01_valid_alternative.json")
    contradicts["rubricResults"][1]["rationale"] = "The function is missing a return statement."
    add(batch, "08_rationale_disagrees", contradicts, cases["01_valid_alternative"])

    results, outputs = rc.check_batch(batch / "outputs", EVALS / "assignments", batch / "submissions")
    summary = rc.summarize(results, outputs)
    rc.write_reports(dest / "report", results, summary, rc.spot_check_sample(results, 3, rc.batch_seed(results)))


if __name__ == "__main__":
    build(DEMO)
    print(f"Wrote {DEMO}")
