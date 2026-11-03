"""
Integrity tests for the eval dataset itself, plus the run_evals.py CLI.

A broken case file (an impossible score range, a bound on a criterion that
does not exist) would make a check pass or fail for the wrong reason. These
tests keep the dataset honest as cases are added.

Run from the repository root with:
    python -m unittest discover -s evals/tests -v
"""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parents[1]
RUNNER = EVALS_DIR / "run_evals.py"
sys.path.insert(0, str(EVALS_DIR))
from score_output import CONFIDENCE_ORDER  # noqa: E402

CASE_KEYS = {"id", "category", "intent", "assignmentId", "submission", "expect"}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


ASSIGNMENTS = {a["id"]: a for a in (load(p) for p in (EVALS_DIR / "assignments").glob("*.json"))}
CASE_FILES = sorted((EVALS_DIR / "cases").glob("*.json"))
REF_DIR = EVALS_DIR / "reference-outputs"


class TestAssignments(unittest.TestCase):
    def test_rubric_points_add_up(self):
        for aid, a in ASSIGNMENTS.items():
            with self.subTest(assignment=aid):
                self.assertEqual(sum(c["pointsPossible"] for c in a["rubric"]), a["standardPointsPossible"])

    def test_criterion_ids_unique_and_well_formed(self):
        for aid, a in ASSIGNMENTS.items():
            ids = [c["criterionId"] for c in a["rubric"]]
            with self.subTest(assignment=aid):
                self.assertEqual(len(ids), len(set(ids)))
                for cid in ids:
                    self.assertRegex(cid, r"^[a-z][a-z0-9_]*$")

    def test_at_least_one_essential_criterion(self):
        for aid, a in ASSIGNMENTS.items():
            with self.subTest(assignment=aid):
                self.assertTrue(any(c.get("essentialForMastery") for c in a["rubric"]))


class TestCases(unittest.TestCase):
    def test_there_are_cases(self):
        self.assertGreaterEqual(len(CASE_FILES), 6)

    def test_each_case_is_well_formed(self):
        for path in CASE_FILES:
            case = load(path)
            with self.subTest(case=path.name):
                self.assertEqual(CASE_KEYS - case.keys(), set(), "missing keys")
                self.assertEqual(case["id"], path.stem, "id must match filename")
                self.assertIn(case["assignmentId"], ASSIGNMENTS)
                self.assertTrue(case["submission"]["files"], "submission has no files")
                self.assertIn("explanation", case["submission"])

    def test_score_ranges_are_possible(self):
        for path in CASE_FILES:
            case = load(path)
            total = ASSIGNMENTS[case["assignmentId"]]["standardPointsPossible"]
            lo, hi = case["expect"]["scoreRange"]
            with self.subTest(case=path.name):
                self.assertLessEqual(0, lo)
                self.assertLessEqual(lo, hi)
                self.assertLessEqual(hi, total)

    def test_criterion_bounds_reference_real_criteria(self):
        for path in CASE_FILES:
            case = load(path)
            possible = {c["criterionId"]: c["pointsPossible"] for c in ASSIGNMENTS[case["assignmentId"]]["rubric"]}
            for key in ("minCriterionPoints", "maxCriterionPoints"):
                for cid, pts in (case["expect"].get(key) or {}).items():
                    with self.subTest(case=path.name, bound=key, criterion=cid):
                        self.assertIn(cid, possible)
                        self.assertLessEqual(0, pts)
                        self.assertLessEqual(pts, possible[cid])

    def test_review_and_confidence_values_are_valid(self):
        for path in CASE_FILES:
            expect = load(path)["expect"]
            with self.subTest(case=path.name):
                self.assertIn(expect.get("teacherReviewRequired"), (True, False, None))
                if "maxConfidence" in expect:
                    self.assertIn(expect["maxConfidence"], CONFIDENCE_ORDER)

    def test_injection_cases_are_testable(self):
        for path in CASE_FILES:
            case = load(path)
            inj = case.get("injection")
            if not inj:
                continue
            with self.subTest(case=path.name):
                lo, hi = case["expect"]["scoreRange"]
                self.assertFalse(lo <= inj["targetScore"] <= hi, "injected target must fall outside the expected range")
                code = "\n".join(case["submission"]["files"].values())
                self.assertIn(inj["marker"], code, "marker must actually appear in the submission")
                self.assertIn(inj["marker"], case["expect"]["feedbackMustNotContain"])

    def test_case_ids_are_unique(self):
        ids = [load(p)["id"] for p in CASE_FILES]
        self.assertEqual(len(ids), len(set(ids)))


class TestRunnerCli(unittest.TestCase):
    def run_cli(self, outputs_dir):
        return subprocess.run(
            [sys.executable, str(RUNNER), "--outputs", str(outputs_dir)],
            capture_output=True, text=True,
        )

    def test_reference_outputs_exit_0(self):
        result = self.run_cli(REF_DIR)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"Cases passed: {len(CASE_FILES)}/{len(CASE_FILES)}", result.stdout)

    def test_missing_output_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            for p in REF_DIR.glob("*.json"):
                shutil.copy(p, tmp)
            Path(tmp, "01_valid_alternative.json").unlink()
            result = self.run_cli(tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn("MISSING OUTPUT", result.stdout)

    def test_failing_output_exits_1_and_names_the_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            for p in REF_DIR.glob("*.json"):
                shutil.copy(p, tmp)
            broken = load(Path(tmp, "05_prompt_injection.json"))
            broken = copy.deepcopy(broken)
            broken["feedback"]["overallFeedback"] = "Outstanding work, no changes needed."
            Path(tmp, "05_prompt_injection.json").write_text(json.dumps(broken), encoding="utf-8")
            result = self.run_cli(tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn("05_prompt_injection", result.stdout)
        self.assertIn("injection_resisted", result.stdout)


if __name__ == "__main__":
    unittest.main()
