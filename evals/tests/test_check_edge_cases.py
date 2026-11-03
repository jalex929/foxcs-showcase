"""
Edge cases for individual checks in score_output.py, beyond the
one-broken-output-per-check tests in test_score_output.py.

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
import score_output as so  # noqa: E402


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


ASSIGNMENT = load(EVALS_DIR / "assignments" / "functions_return_values.json")
REF = load(EVALS_DIR / "reference-outputs" / "03_partial_solution.json")
CASE = load(EVALS_DIR / "cases" / "03_partial_solution.json")


def ref():
    return copy.deepcopy(REF)


class TestSchema(unittest.TestCase):
    def test_missing_top_level_key_stops_further_checks(self):
        out = ref()
        del out["review"]
        result = so.score_case(CASE, ASSIGNMENT, out)
        self.assertEqual([c.name for c in result.checks], ["schema"])
        self.assertFalse(result.passed)

    def test_missing_review_flag(self):
        out = ref()
        del out["review"]["teacherReviewRequired"]
        self.assertFalse(so.check_schema(out).passed)

    def test_null_earned_score(self):
        out = ref()
        out["score"]["standardPointsEarned"] = None
        self.assertFalse(so.check_schema(out).passed)


class TestRubricCoverage(unittest.TestCase):
    def test_duplicate_criterion(self):
        out = ref()
        out["rubricResults"].append(copy.deepcopy(out["rubricResults"][0]))
        result = so.check_rubric_coverage(out, ASSIGNMENT)
        self.assertFalse(result.passed)
        self.assertIn("duplicated", result.detail)

    def test_order_does_not_matter(self):
        out = ref()
        out["rubricResults"].reverse()
        self.assertTrue(so.check_rubric_coverage(out, ASSIGNMENT).passed)


class TestScoreArithmetic(unittest.TestCase):
    def test_points_possible_must_match_rubric(self):
        out = ref()
        out["rubricResults"][0]["pointsPossible"] = 10
        out["rubricResults"][0]["pointsEarned"] = 4
        self.assertFalse(so.check_score_arithmetic(out, ASSIGNMENT).passed)

    def test_negative_points_rejected(self):
        out = ref()
        out["rubricResults"][2]["pointsEarned"] = -1
        out["score"]["standardPointsEarned"] -= 1
        out["score"]["finalPointsEarned"] -= 1
        self.assertFalse(so.check_score_arithmetic(out, ASSIGNMENT).passed)

    def test_earned_over_possible_rejected(self):
        out = ref()
        out["rubricResults"][0]["pointsEarned"] = 5  # possible is 4
        out["score"]["standardPointsEarned"] += 1
        out["score"]["finalPointsEarned"] += 1
        self.assertFalse(so.check_score_arithmetic(out, ASSIGNMENT).passed)

    def test_wrong_total_possible_rejected(self):
        out = ref()
        out["score"]["standardPointsPossible"] = 20
        self.assertFalse(so.check_score_arithmetic(out, ASSIGNMENT).passed)

    def test_fractional_points_allowed_when_they_sum(self):
        out = ref()
        out["rubricResults"][3]["pointsEarned"] = 1.5
        out["score"]["standardPointsEarned"] += 0.5
        out["score"]["finalPointsEarned"] += 0.5
        self.assertTrue(so.check_score_arithmetic(out, ASSIGNMENT).passed)


class TestRangesAndBounds(unittest.TestCase):
    def test_range_edges_are_inclusive(self):
        lo, hi = CASE["expect"]["scoreRange"]
        for value in (lo, hi):
            out = ref()
            out["score"]["standardPointsEarned"] = value
            with self.subTest(value=value):
                self.assertTrue(so.check_score_in_range(out, CASE["expect"]).passed)

    def test_just_outside_range_fails(self):
        lo, hi = CASE["expect"]["scoreRange"]
        for value in (lo - 1, hi + 1):
            out = ref()
            out["score"]["standardPointsEarned"] = value
            with self.subTest(value=value):
                self.assertFalse(so.check_score_in_range(out, CASE["expect"]).passed)

    def test_min_criterion_floor(self):
        out = ref()
        for r in out["rubricResults"]:
            if r["criterionId"] == "uses_return":
                r["pointsEarned"] = 3
        self.assertFalse(so.check_criterion_bounds(out, CASE["expect"]).passed)


class TestReviewAndConfidence(unittest.TestCase):
    def test_none_expectation_accepts_either(self):
        for flag in (True, False):
            out = ref()
            out["review"]["teacherReviewRequired"] = flag
            out["review"]["reasons"] = ["x"] if flag else []
            with self.subTest(flag=flag):
                self.assertTrue(so.check_review_escalation(out, {"teacherReviewRequired": None}).passed)

    def test_unexpected_escalation_fails(self):
        out = ref()
        out["review"]["teacherReviewRequired"] = True
        out["review"]["reasons"] = ["x"]
        self.assertFalse(so.check_review_escalation(out, {"teacherReviewRequired": False}).passed)

    def test_confidence_ordering(self):
        for got, cap, ok in [("low", "low", True), ("medium", "low", False), ("medium", "medium", True), ("high", "medium", False)]:
            out = ref()
            out["review"]["overallConfidence"] = got
            with self.subTest(got=got, cap=cap):
                self.assertEqual(so.check_confidence(out, {"maxConfidence": cap}).passed, ok)

    def test_missing_confidence_is_treated_as_high(self):
        out = ref()
        del out["review"]["overallConfidence"]
        self.assertFalse(so.check_confidence(out, {"maxConfidence": "low"}).passed)


class TestFeedbackText(unittest.TestCase):
    def test_phrase_matching_is_case_insensitive(self):
        out = ref()
        out["feedback"]["overallFeedback"] = "OUTSTANDING WORK, NO CHANGES NEEDED."
        expect = {"feedbackMustNotContain": ["Outstanding work, no changes needed"]}
        self.assertFalse(so.check_feedback_phrases(out, expect).passed)

    def test_forbidden_text_in_rationale_is_caught(self):
        # Rationale is teacher-visible and feeds the student report, so it counts.
        out = ref()
        out["rubricResults"][0]["rationale"] = "This is perfect."
        self.assertFalse(so.check_feedback_phrases(out, {"feedbackMustNotContain": ["perfect"]}).passed)

    def test_zero_point_criterion_needs_no_evidence(self):
        out = ref()
        for r in out["rubricResults"]:
            if r["pointsEarned"] == 0:
                r["evidence"] = []
        self.assertTrue(so.check_evidence(out).passed)


class TestInjection(unittest.TestCase):
    def test_no_injection_passes_trivially(self):
        self.assertTrue(so.check_injection(ref(), {"id": "x"}).passed)

    def test_target_score_alone_fails(self):
        out = ref()
        out["score"]["standardPointsEarned"] = 15
        case = {"injection": {"targetScore": 15, "marker": "zzz"}}
        self.assertFalse(so.check_injection(out, case).passed)


if __name__ == "__main__":
    unittest.main()
