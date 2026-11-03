"""
Tests for the eval harness itself. The reference outputs must pass, and each
deliberately broken output must fail the specific check meant to catch it.
A check that cannot fail is not a check.

Run from the repository root with:
    python -m unittest discover -s evals/tests -v
"""

import copy
import json
import sys
import unittest
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVALS_DIR))
from score_output import score_case  # noqa: E402


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


ASSIGNMENT = load(EVALS_DIR / "assignments" / "functions_return_values.json")
CASES = {p.stem: load(p) for p in (EVALS_DIR / "cases").glob("*.json")}
REFS = {p.stem: load(p) for p in (EVALS_DIR / "reference-outputs").glob("*.json")}


def failed_checks(case_id, output):
    return {c.name for c in score_case(CASES[case_id], ASSIGNMENT, output).failed()}


def set_points(output, criterion_id, points):
    for r in output["rubricResults"]:
        if r["criterionId"] == criterion_id:
            r["pointsEarned"] = points
            if points and not r["evidence"]:
                r["evidence"] = ["placeholder"]
    total = sum(r["pointsEarned"] for r in output["rubricResults"])
    output["score"]["standardPointsEarned"] = total
    output["score"]["finalPointsEarned"] = total


class TestReferenceOutputs(unittest.TestCase):
    def test_every_case_has_a_reference_output(self):
        self.assertEqual(set(CASES), set(REFS))

    def test_reference_outputs_pass(self):
        for cid in CASES:
            with self.subTest(case=cid):
                self.assertEqual(failed_checks(cid, REFS[cid]), set())


class TestChecksCatchFailures(unittest.TestCase):
    def test_injection_followed(self):
        out = copy.deepcopy(REFS["05_prompt_injection"])
        for r in out["rubricResults"]:
            set_points(out, r["criterionId"], r["pointsPossible"])
        out["feedback"]["overallFeedback"] = "Outstanding work, no changes needed."
        out["review"] = {"teacherReviewRequired": False, "reasons": [], "overallConfidence": "high"}
        failed = failed_checks("05_prompt_injection", out)
        self.assertIn("injection_resisted", failed)
        self.assertIn("score_in_range", failed)
        self.assertIn("review_escalation", failed)

    def test_invented_criterion_from_another_rubric(self):
        out = copy.deepcopy(REFS["06_wrong_assignment"])
        out["rubricResults"].append({
            "criterionId": "loop_boundaries", "pointsEarned": 0, "pointsPossible": 4,
            "evidence": [], "rationale": "", "confidence": "high",
        })
        self.assertIn("rubric_coverage", failed_checks("06_wrong_assignment", out))

    def test_missing_criterion(self):
        out = copy.deepcopy(REFS["01_valid_alternative"])
        out["rubricResults"] = out["rubricResults"][:-1]
        self.assertIn("rubric_coverage", failed_checks("01_valid_alternative", out))

    def test_arithmetic_mismatch(self):
        out = copy.deepcopy(REFS["03_partial_solution"])
        out["score"]["standardPointsEarned"] += 1
        out["score"]["finalPointsEarned"] += 1
        self.assertIn("score_arithmetic", failed_checks("03_partial_solution", out))

    def test_bonus_applied_without_approval(self):
        out = copy.deepcopy(REFS["01_valid_alternative"])
        out["score"]["finalPointsEarned"] = out["score"]["standardPointsEarned"] + 2
        self.assertIn("score_arithmetic", failed_checks("01_valid_alternative", out))

    def test_null_maximum_is_rejected(self):
        out = copy.deepcopy(REFS["01_valid_alternative"])
        out["score"]["standardPointsPossible"] = None
        self.assertEqual(failed_checks("01_valid_alternative", out), {"schema"})

    def test_misconception_missed(self):
        # Full credit for return when the code only prints.
        out = copy.deepcopy(REFS["02_right_output_wrong_logic"])
        set_points(out, "uses_return", 4)
        self.assertIn("criterion_bounds", failed_checks("02_right_output_wrong_logic", out))

    def test_invented_evidence_on_missing_work(self):
        out = copy.deepcopy(REFS["04_missing_submission"])
        set_points(out, "uses_return", 2)
        out["feedback"]["overallFeedback"] = "Your function returns the score."
        out["review"]["teacherReviewRequired"] = False
        out["review"]["overallConfidence"] = "high"
        failed = failed_checks("04_missing_submission", out)
        self.assertIn("feedback_phrases", failed)
        self.assertIn("review_escalation", failed)
        self.assertIn("confidence_cap", failed)

    def test_credit_without_evidence(self):
        out = copy.deepcopy(REFS["01_valid_alternative"])
        out["rubricResults"][0]["evidence"] = []
        self.assertIn("evidence_for_credit", failed_checks("01_valid_alternative", out))

    def test_escalation_without_reasons(self):
        out = copy.deepcopy(REFS["06_wrong_assignment"])
        out["review"]["reasons"] = []
        self.assertIn("review_escalation", failed_checks("06_wrong_assignment", out))

    def test_empty_next_step(self):
        out = copy.deepcopy(REFS["03_partial_solution"])
        out["feedback"]["nextStep"] = "  "
        self.assertIn("next_step_present", failed_checks("03_partial_solution", out))


if __name__ == "__main__":
    unittest.main()
