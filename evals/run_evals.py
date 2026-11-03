#!/usr/bin/env python3
"""
Run the FoxCS grading eval suite against a folder of grading outputs.

    python evals/run_evals.py --outputs evals/reference-outputs

Each file in --outputs is named <case_id>.json and holds one structured
grading output for that case. The harness does not call a model. Producing
outputs (a Claude run, a different prompt, a human grader) is a separate,
swappable step, so the same checks can compare prompt versions or models
side by side.

Exit code is 0 only if every case passes every check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score_output import score_case  # noqa: E402

EVALS_DIR = Path(__file__).resolve().parent


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_cases() -> list[dict]:
    return [load_json(p) for p in sorted((EVALS_DIR / "cases").glob("*.json"))]


def load_assignments() -> dict[str, dict]:
    return {a["id"]: a for a in (load_json(p) for p in (EVALS_DIR / "assignments").glob("*.json"))}


def run(outputs_dir: Path) -> list:
    assignments = load_assignments()
    results = []
    for case in load_cases():
        out_path = outputs_dir / f"{case['id']}.json"
        if not out_path.exists():
            print(f"{case['id']:<30} MISSING OUTPUT ({out_path})")
            results.append(None)
            continue
        res = score_case(case, assignments[case["assignmentId"]], load_json(out_path))
        status = "PASS" if res.passed else "FAIL"
        print(f"{case['id']:<30} {status}  ({sum(c.passed for c in res.checks)}/{len(res.checks)} checks)")
        for c in res.failed():
            print(f"    x {c.name}: {c.detail}")
        results.append(res)
    return results


def summarize(results: list) -> bool:
    scored = [r for r in results if r is not None]
    by_check: dict[str, list[bool]] = {}
    for r in scored:
        for c in r.checks:
            by_check.setdefault(c.name, []).append(c.passed)
    print("\nPer-check pass rate:")
    for name, vals in by_check.items():
        print(f"  {name:<22} {sum(vals)}/{len(vals)}")
    passed = sum(r.passed for r in scored)
    print(f"\nCases passed: {passed}/{len(results)}")
    return passed == len(results)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--outputs", type=Path, default=EVALS_DIR / "reference-outputs")
    args = parser.parse_args()
    ok = summarize(run(args.outputs))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
