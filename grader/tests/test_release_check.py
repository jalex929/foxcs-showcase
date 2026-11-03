"""
Tests for release_check.py: the pre-release check that lets a teacher approve
a batch of AI-assisted grades by rationale and trends without re-reading
every grade. CI enforces 100% line and branch coverage of release_check.py.

Batches are built from the eval suite's synthetic cases and hand-authored
reference judgments (evals/), so the "good" batch is known-good. Every check
then gets a deliberately broken result and must catch it.

Run from the repository root with:
    python -m unittest discover -s grader/tests -v
"""

import copy
import csv
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

GRADER_DIR = Path(__file__).resolve().parents[1]
EVALS = GRADER_DIR.parent / "evals"
sys.path.insert(0, str(GRADER_DIR))
import release_check as rc  # noqa: E402

RUBRICS = EVALS / "assignments"
RUBRIC = json.loads((RUBRICS / "functions_return_values.json").read_text())


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def reference(case_id):
    out = load(EVALS / "reference-outputs" / f"{case_id}.json")
    case = load(EVALS / "cases" / f"{case_id}.json")
    out.update(submission_id=case_id, student_id=f"STUDENT-{case_id[:2]}", assignment_id=case["assignmentId"])
    return out


CASE_IDS = sorted(p.stem for p in (EVALS / "cases").glob("*.json"))


class Batch:
    """A temp folder laid out the way release_check reads a real batch."""

    def __init__(self, root: Path):
        self.root = root
        self.outputs = root / "outputs"
        self.subs = root / "submissions"
        self.outputs.mkdir()
        self.subs.mkdir()

    def add(self, out, files=None):
        (self.outputs / f"{out['submission_id']}.json").write_text(json.dumps(out), encoding="utf-8")
        if files is not None:
            d = self.subs / out["submission_id"]
            d.mkdir(exist_ok=True)
            for name, text in files.items():
                (d / name).write_text(text, encoding="utf-8")
        return out

    def add_case(self, case_id, mutate=None):
        out = reference(case_id)
        if mutate:
            mutate(out)
        case = load(EVALS / "cases" / f"{case_id}.json")
        return self.add(out, dict(case["submission"]["files"], **{"explanation.txt": case["submission"]["explanation"]}))

    def run(self, drafts=None, with_subs=True):
        results, outputs = rc.check_batch(self.outputs, RUBRICS, self.subs if with_subs else None, drafts)
        return {r.submission_id: r for r in results}, outputs


def codes(result):
    return {f.code for f in result.findings}


def set_points(out, cid, pts):
    for r in out["rubricResults"]:
        if r["criterionId"] == cid:
            r["pointsEarned"] = pts
    total = sum(r["pointsEarned"] for r in out["rubricResults"])
    out["score"]["standardPointsEarned"] = total
    out["score"]["finalPointsEarned"] = total


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.batch = Batch(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()


# ------------------------------------------------------------ known-good batch

class TestKnownGoodBatch(TempCase):
    def test_reference_judgments_raise_no_false_alarms(self):
        for cid in CASE_IDS:
            self.batch.add_case(cid)
        results, _ = self.batch.run()
        for cid, r in results.items():
            with self.subTest(case=cid):
                self.assertNotEqual(r.status, rc.BLOCKED, r.findings)
                # The only reason a good judgment needs review is that the grader asked for it.
                self.assertTrue(codes(r) <= {"grader_requested_review", "low_confidence"}, r.findings)

    def test_grader_escalations_surface_for_review(self):
        for cid in CASE_IDS:
            self.batch.add_case(cid)
        results, _ = self.batch.run()
        for cid in ("04_missing_submission", "05_prompt_injection", "06_wrong_assignment"):
            self.assertEqual(results[cid].status, rc.REVIEW)
        self.assertIn("TODO", results["04_missing_submission"].findings[0].detail)


# ------------------------------------------------------------ blocked

class TestBlocked(TempCase):
    def check(self, mutate, expected_code, case="03_partial_solution"):
        self.batch.add_case(case, mutate)
        r = self.batch.run()[0][case]
        self.assertEqual(r.status, rc.BLOCKED, r.findings)
        self.assertIn(expected_code, codes(r))
        return r

    def test_missing_sections(self):
        r = self.check(lambda o: o.pop("review"), "schema")
        self.assertEqual(codes(r), {"schema"})  # nothing else runs on a broken shape

    def test_criterion_from_another_rubric(self):
        r = self.check(lambda o: o["rubricResults"].append(
            {"criterionId": "loop_boundaries", "pointsEarned": 0, "pointsPossible": 4, "evidence": []}), "rubric_mismatch")
        detail = next(f.detail for f in r.findings if f.code == "rubric_mismatch")
        self.assertEqual(detail, "not in rubric: ['loop_boundaries']; not scored: none; scored twice: none")

    def test_rubric_mismatch_names_missing_and_doubled(self):
        def m(o):
            o["rubricResults"][1] = copy.deepcopy(o["rubricResults"][0])
        r = self.check(m, "rubric_mismatch")
        detail = next(f.detail for f in r.findings if f.code == "rubric_mismatch")
        self.assertEqual(detail, "not in rubric: none; not scored: ['uses_return']; scored twice: ['runs_without_errors']")

    def test_criterion_scored_twice(self):
        self.check(lambda o: o["rubricResults"].append(copy.deepcopy(o["rubricResults"][-1])), "rubric_mismatch")

    def test_points_not_numbers(self):
        self.check(lambda o: o["rubricResults"][0].update(pointsEarned="four"), "points_missing")

    def test_points_possible_differs_from_rubric(self):
        self.check(lambda o: o["rubricResults"][0].update(pointsPossible=10), "points_possible")

    def test_points_out_of_range(self):
        def m(o):
            o["rubricResults"][0]["pointsEarned"] = 6
            o["score"]["standardPointsEarned"] += 2
            o["score"]["finalPointsEarned"] += 2
        self.check(m, "points_range")

    def test_credit_without_evidence(self):
        self.check(lambda o: o["rubricResults"][0].update(evidence=[]), "credit_without_evidence")

    def test_even_one_point_needs_evidence(self):
        def m(o):
            o["rubricResults"][3]["evidence"] = []  # explanation_matches_code: 1 point in this case
        self.check(m, "credit_without_evidence")

    def test_zero_points_need_no_evidence(self):
        self.batch.add_case("03_partial_solution", lambda o: o["rubricResults"][2].update(evidence=[]))
        r = self.batch.run()[0]["03_partial_solution"]
        self.assertNotIn("credit_without_evidence", codes(r))

    def test_totals_missing(self):
        self.check(lambda o: o["score"].update(standardPointsPossible=None), "score_missing")

    def test_sum_mismatch(self):
        def m(o):
            o["score"]["standardPointsEarned"] += 1
            o["score"]["finalPointsEarned"] += 1
        self.check(m, "sum_mismatch")

    def test_total_possible_differs(self):
        self.check(lambda o: o["score"].update(standardPointsPossible=20), "total_possible")

    def test_bonus_applied_without_approval(self):
        self.check(lambda o: o["score"].update(finalPointsEarned=o["score"]["finalPointsEarned"] + 2), "bonus_unapproved")

    def test_empty_next_step(self):
        self.check(lambda o: o["feedback"].update(nextStep="  "), "no_next_step")

    def test_quoted_code_not_in_submission(self):
        def m(o):
            o["rubricResults"][1]["evidence"] = ["add_points ends with `return score * points`."]
        r = self.check(m, "quote_not_in_submission")
        self.assertIn("return score * points", r.findings[0].detail)

    def test_quote_matching_ignores_whitespace(self):
        def m(o):
            o["rubricResults"][1]["evidence"] = ["add_points ends with `return   score +\n points`."]
        self.batch.add_case("03_partial_solution", m)
        r = self.batch.run()[0]["03_partial_solution"]
        self.assertNotIn("quote_not_in_submission", codes(r))

    def test_no_rubric_for_assignment(self):
        r = self.check(lambda o: o.update(assignment_id="mystery"), "no_rubric")
        self.assertEqual(codes(r), {"no_rubric"})

    def test_unreadable_file(self):
        (self.batch.outputs / "broken.json").write_text("{not json")
        r = self.batch.run()[0]["broken"]
        self.assertEqual((r.status, codes(r)), (rc.BLOCKED, {"unreadable"}))

    def test_submission_id_falls_back_to_filename(self):
        out = reference("01_valid_alternative")
        del out["submission_id"]
        (self.batch.outputs / "from-filename.json").write_text(json.dumps(out))
        self.assertIn("from-filename", self.batch.run(with_subs=False)[0])

    def test_assignment_id_falls_back_to_assignment_block(self):
        def m(o):
            del o["assignment_id"]
        self.batch.add_case("01_valid_alternative", m)
        r = self.batch.run()[0]["01_valid_alternative"]
        self.assertEqual(r.assignment_id, RUBRIC["id"])
        self.assertEqual(r.status, rc.READY)


# ------------------------------------------------------------ program-run cross-check

class TestAgainstProgramRun(TempCase):
    def drafts(self, *records):
        path = self.tmp / "drafts.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records) + "\n")
        return path

    def draft(self, sid, band, runs, correct):
        return {"submission_id": sid, "band": band, "rubricResults": [
            {"criterionId": "runs_without_errors", "pointsEarned": runs},
            {"criterionId": "correct_output", "pointsEarned": correct}]}

    def test_agreeing_grade_passes(self):
        self.batch.add_case("01_valid_alternative")
        r = self.batch.run(self.drafts(self.draft("01_valid_alternative", "flawless", 4, 4)))[0]["01_valid_alternative"]
        self.assertNotIn("contradicts_run", codes(r))

    def test_credit_for_running_when_it_crashed(self):
        self.batch.add_case("01_valid_alternative")
        r = self.batch.run(self.drafts(self.draft("01_valid_alternative", "does_not_run", 0, 0)))[0]["01_valid_alternative"]
        self.assertEqual(r.status, rc.BLOCKED)
        self.assertIn("contradicts_run", codes(r))

    def test_partial_band_only_fixes_the_runs_half(self):
        # 03 grades correct_output 0; the run's suggested 2 is judgment, not fact.
        self.batch.add_case("03_partial_solution")
        r = self.batch.run(self.drafts(self.draft("03_partial_solution", "runs_with_issues", 4, 2)))[0]["03_partial_solution"]
        self.assertNotIn("contradicts_run", codes(r))
        self.batch.add_case("03_partial_solution", lambda o: set_points(o, "runs_without_errors", 2))
        r = self.batch.run(self.drafts(self.draft("03_partial_solution", "runs_with_issues", 4, 2)))[0]["03_partial_solution"]
        self.assertIn("contradicts_run", codes(r))

    def test_held_drafts_settle_nothing(self):
        self.batch.add_case("01_valid_alternative")
        held = {"submission_id": "01_valid_alternative", "band": None, "rubricResults": []}
        r = self.batch.run(self.drafts(held))[0]["01_valid_alternative"]
        self.assertNotIn("contradicts_run", codes(r))

    def test_latest_draft_wins(self):
        self.batch.add_case("01_valid_alternative")
        path = self.drafts(self.draft("01_valid_alternative", "does_not_run", 0, 0),
                           self.draft("01_valid_alternative", "flawless", 4, 4))
        r = self.batch.run(path)[0]["01_valid_alternative"]
        self.assertNotIn("contradicts_run", codes(r))


# ------------------------------------------------------------ needs review

class TestNeedsReview(TempCase):
    def review_codes(self, case, mutate):
        self.batch.add_case(case, mutate)
        r = self.batch.run()[0][case]
        self.assertEqual(r.status, rc.REVIEW, r.findings)
        return codes(r)

    def test_full_marks_but_rationale_names_a_problem(self):
        def m(o):
            o["rubricResults"][1]["rationale"] = "The function is missing a return statement."
        self.assertIn("full_marks_with_problems", self.review_codes("01_valid_alternative", m))

    def test_no_errors_is_not_a_problem(self):
        for phrase in ("No errors.", "It runs without raising an error.", "Runs with no syntax errors."):
            with self.subTest(phrase=phrase):
                self.assertEqual(rc.check_rationale_alignment({"rubricResults": [
                    {"criterionId": "c", "pointsEarned": 4, "pointsPossible": 4, "rationale": phrase}]}), [])

    def test_zero_with_only_praise(self):
        def m(o):
            o["rubricResults"][2]["rationale"] = "The output is correct."
            o["rubricResults"][2]["evidence"] = ["Prints the score."]
        self.assertIn("zero_with_praise", self.review_codes("03_partial_solution", m))

    def test_zero_with_mixed_rationale_is_fine(self):
        self.assertEqual(rc.check_rationale_alignment({"rubricResults": [
            {"criterionId": "c", "pointsEarned": 0, "pointsPossible": 3,
             "rationale": "Accurate wording, but it does not describe this code."}]}), [])

    def test_partial_points_are_not_judged_by_wording(self):
        self.assertEqual(rc.check_rationale_alignment({"rubricResults": [
            {"criterionId": "c", "pointsEarned": 2, "pointsPossible": 4, "rationale": "Correct, but missing a case."}]}), [])

    def test_one_point_criteria_are_checked_too(self):
        found = rc.check_rationale_alignment({"rubricResults": [
            {"criterionId": "c", "pointsEarned": 1, "pointsPossible": 1, "rationale": "The header is missing."}]})
        self.assertEqual([(f.code, f.detail) for f in found],
                         [("full_marks_with_problems", "c: full points, but the rationale says 'missing'")])
        found = rc.check_rationale_alignment({"rubricResults": [
            {"criterionId": "c", "pointsEarned": 0, "pointsPossible": 1, "rationale": "Clear and complete."}]})
        self.assertEqual([f.detail for f in found], ["c: zero points, but the rationale only says 'Clear'"])

    def test_unscorable_criteria_are_skipped(self):
        self.assertEqual(rc.check_rationale_alignment({"rubricResults": [
            {"criterionId": "a", "pointsEarned": None, "pointsPossible": 4},
            {"criterionId": "b", "pointsEarned": 0, "pointsPossible": 0, "rationale": "correct"}]}), [])

    def test_strong_praise_below_full_marks(self):
        def m(o):
            o["feedback"]["overallFeedback"] = "Excellent work on this one."
        self.assertIn("praise_below_full", self.review_codes("03_partial_solution", m))

    def test_full_marks_with_required_fixes(self):
        def m(o):
            o["feedback"]["priorityImprovements"] = ["Add a second call."]
        self.assertIn("full_marks_with_fixes", self.review_codes("01_valid_alternative", m))

    def test_feedback_check_needs_numeric_totals(self):
        praise = {"overallFeedback": "Excellent!", "priorityImprovements": ["x"]}
        self.assertEqual(rc.check_feedback_alignment({"score": {"standardPointsEarned": None, "standardPointsPossible": 15},
                                                      "feedback": praise}), [])
        self.assertEqual(rc.check_feedback_alignment({"score": {"standardPointsEarned": 0, "standardPointsPossible": None},
                                                      "feedback": praise}), [])
        self.assertEqual(rc.check_feedback_alignment({"score": {"standardPointsEarned": 0, "standardPointsPossible": 0},
                                                      "feedback": praise}), [])

    def test_feedback_check_on_a_one_point_item(self):
        found = rc.check_feedback_alignment({"score": {"standardPointsEarned": 0, "standardPointsPossible": 1},
                                             "feedback": {"overallFeedback": "Perfect."}})
        self.assertEqual([f.detail for f in found], ["says 'Perfect' at 0/1"])

    def test_low_confidence(self):
        def m(o):
            o["review"]["overallConfidence"] = "low"
        self.assertIn("low_confidence", self.review_codes("01_valid_alternative", m))

    def test_review_without_reasons_still_surfaces(self):
        def m(o):
            o["review"] = {"teacherReviewRequired": True, "reasons": [], "overallConfidence": "high"}
        self.batch.add_case("01_valid_alternative", m)
        r = self.batch.run()[0]["01_valid_alternative"]
        self.assertIn("no reason given", r.findings[0].detail)

    def test_missing_submission_files(self):
        self.batch.add(reference("01_valid_alternative"))  # no files written
        r = self.batch.run()[0]["01_valid_alternative"]
        self.assertIn("submission_missing", codes(r))

    def test_without_submissions_folder_quotes_are_not_checked(self):
        self.batch.add_case("01_valid_alternative", lambda o: o["rubricResults"][1].update(evidence=["`made up code`"]))
        r = self.batch.run(with_subs=False)[0]["01_valid_alternative"]
        self.assertEqual(r.status, rc.READY)

    def test_same_feedback_different_scores(self):
        a = self.batch.add_case("02_right_output_wrong_logic")
        self.batch.add_case("03_partial_solution", lambda o: o["feedback"].update(overallFeedback=a["feedback"]["overallFeedback"]))
        results, _ = self.batch.run()
        for sid in ("02_right_output_wrong_logic", "03_partial_solution"):
            self.assertIn("duplicate_feedback", codes(results[sid]))
        detail = next(f.detail for f in results["03_partial_solution"].findings if f.code == "duplicate_feedback")
        self.assertEqual(detail, "same overall feedback as 02_right_output_wrong_logic, with a different score")

    def test_same_feedback_same_score_is_fine(self):
        a = self.batch.add_case("02_right_output_wrong_logic")
        b = copy.deepcopy(a)
        b.update(submission_id="02b", student_id="STUDENT-99")
        case = load(EVALS / "cases" / "02_right_output_wrong_logic.json")
        self.batch.add(b, dict(case["submission"]["files"], **{"explanation.txt": case["submission"]["explanation"]}))
        results, _ = self.batch.run()
        self.assertNotIn("duplicate_feedback", codes(results["02b"]))

    def test_malformed_feedback_block_skips_duplicate_check(self):
        out = reference("01_valid_alternative")
        out["feedback"] = "not an object"
        self.batch.add(out, {"x.py": ""})
        results, _ = self.batch.run()
        self.assertIn("schema", codes(results["01_valid_alternative"]))


# ------------------------------------------------------------ spot check and summary

def fake(sid, score, findings=()):
    return rc.ResultCheck(sid, sid, "a", score, 10, list(findings))


class TestSpotCheck(unittest.TestCase):
    def test_spread_across_low_middle_high(self):
        results = [fake(f"s{i:02}", i) for i in range(9)]
        for seed in map(str, range(30)):
            picked = rc.spot_check_sample(results, 3, seed)
            scores = sorted(int(p[1:]) for p in picked)
            with self.subTest(seed=seed):
                self.assertTrue(scores[0] <= 2 and 3 <= scores[1] <= 5 and scores[2] >= 6, scores)

    def test_low_band_first_then_middle(self):
        results = [fake("lo", 1), fake("mid", 2), fake("hi", 3)]
        self.assertEqual(rc.spot_check_sample(results, 2, "x"), ["lo", "mid"])

    def test_groups_by_score_not_by_name(self):
        results = [fake(f"z{i}", 0) for i in range(3)] + [fake(f"a{i}", 5) for i in range(3)] + [fake(f"m{i}", 9) for i in range(3)]
        picked = rc.spot_check_sample(results, 1, "x")
        self.assertTrue(picked[0].startswith("z"), picked)

    def test_one_requested_gives_one(self):
        self.assertEqual(len(rc.spot_check_sample([fake("a", 1), fake("b", 2), fake("c", 3)], 1, "x")), 1)

    def test_reproducible_for_the_same_seed(self):
        results = [fake(f"s{i:02}", i) for i in range(12)]
        self.assertEqual(rc.spot_check_sample(results, 4, "x"), rc.spot_check_sample(results, 4, "x"))

    def test_only_ready_results_are_sampled(self):
        results = [fake("ready", 5), fake("held", 5, [rc.Finding(rc.REVIEW, "c", "d")])]
        self.assertEqual(rc.spot_check_sample(results, 3, "x"), ["ready"])

    def test_sample_never_exceeds_what_is_ready(self):
        self.assertEqual(len(rc.spot_check_sample([fake("a", 1), fake("b", 2)], 5, "x")), 2)

    def test_zero_requested_or_nothing_ready(self):
        self.assertEqual(rc.spot_check_sample([fake("a", 1)], 0, "x"), [])
        self.assertEqual(rc.spot_check_sample([], 3, "x"), [])

    def test_batch_seed_depends_on_membership_not_order(self):
        a, b = fake("a", 1), fake("b", 2)
        self.assertEqual(rc.batch_seed([a, b]), rc.batch_seed([b, a]))
        self.assertNotEqual(rc.batch_seed([a]), rc.batch_seed([a, b]))


class TestSummary(TempCase):
    def test_spread_and_full_credit_rates(self):
        for cid in CASE_IDS:
            self.batch.add_case(cid)
        results, outputs = self.batch.run()
        s = rc.summarize(list(results.values()), outputs)
        self.assertEqual(s["count"], 6)
        self.assertEqual(s["status"], {"ready": 3, "review": 3, "blocked": 0})
        self.assertEqual(s["percent"], {"mean": 37.8, "median": 33.3, "min": 0.0, "max": 100.0})
        self.assertEqual(s["full_credit_rate"]["runs_without_errors"], 66.7)

    def test_non_numeric_points_left_out_of_rates(self):
        outputs = {"x": {"rubricResults": [{"criterionId": "c", "pointsEarned": "four", "pointsPossible": 4},
                                           {"criterionId": "d", "pointsEarned": 2, "pointsPossible": 2}]}}
        self.assertEqual(rc.summarize([fake("x", 2)], outputs)["full_credit_rate"], {"d": 100.0})

    def test_rounding_of_spread(self):
        results = [rc.ResultCheck("a", "a", "x", 1, 3), rc.ResultCheck("b", "b", "x", 2, 3), rc.ResultCheck("c", "c", "x", 2, 3)]
        self.assertEqual(rc.summarize(results, {})["percent"], {"mean": 55.6, "median": 66.7, "min": 33.3, "max": 66.7})

    def test_nothing_scored(self):
        s = rc.summarize([fake("x", None)], {"x": {"rubricResults": "bad"}})
        self.assertIsNone(s["percent"])
        self.assertEqual(s["full_credit_rate"], {})


# ------------------------------------------------------------ command line and reports

class TestMain(TempCase):
    def main(self, *extra):
        out = io.StringIO()
        with redirect_stdout(out):
            code = rc.main(["--outputs", str(self.batch.outputs), "--rubrics", str(RUBRICS),
                            "--submissions", str(self.batch.subs), "--out-dir", str(self.tmp / "report"), *extra])
        return code, out.getvalue()

    def test_clean_batch_exits_0_and_writes_reports(self):
        for cid in CASE_IDS:
            self.batch.add_case(cid)
        code, out = self.main("--sample", "2")
        self.assertEqual(code, 0)
        self.assertIn("3 ready, 3 need review, 0 blocked", out)
        report = (self.tmp / "report" / "release_report.md").read_text()
        self.assertIn("Push is allowed", report)
        self.assertIn("| runs_without_errors | 66.7% |", report)
        with (self.tmp / "report" / "release_manifest.csv").open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 6)
        self.assertEqual(sum(r["spot_check"] == "yes" for r in rows), 2)
        self.assertTrue(all(r["status"] == "ready" for r in rows if r["spot_check"]))

    def test_blocked_batch_exits_1(self):
        self.batch.add_case("01_valid_alternative", lambda o: o["score"].update(finalPointsEarned=17))
        code, _ = self.main()
        self.assertEqual(code, 1)
        report = (self.tmp / "report" / "release_report.md").read_text()
        self.assertIn("**not allowed**", report)
        self.assertIn("**01_valid_alternative** (blocked)", report)

    def test_explicit_seed_and_empty_report_sections(self):
        self.batch.add_case("04_missing_submission")
        code, out = self.main("--seed", "fixed")
        self.assertEqual(code, 0)
        self.assertIn("Spot check: (none)", out)
        report = (self.tmp / "report" / "release_report.md").read_text()
        self.assertIn("- (none ready)", report)

    def test_blocked_listed_before_review(self):
        self.batch.add_case("04_missing_submission")  # review, sorts first by name
        self.batch.add_case("06_wrong_assignment", lambda o: o["score"].update(finalPointsEarned=17))  # blocked
        self.main()
        report = (self.tmp / "report" / "release_report.md").read_text()
        self.assertLess(report.index("**06_wrong_assignment** (blocked)"), report.index("**04_missing_submission** (review)"))

    def test_default_sample_is_three_and_seeded_by_batch(self):
        for i in range(6):
            out = reference("01_valid_alternative")
            out.update(submission_id=f"c{i}", student_id=f"S{i}")
            case = load(EVALS / "cases" / "01_valid_alternative.json")
            self.batch.add(out, case["submission"]["files"])
        self.main()
        with (self.tmp / "report" / "release_manifest.csv").open(encoding="utf-8") as f:
            picked = [r["submission_id"] for r in csv.DictReader(f) if r["spot_check"]]
        results, _ = self.batch.run()
        expected = rc.spot_check_sample(list(results.values()), 3, rc.batch_seed(list(results.values())))
        self.assertEqual(sorted(picked), sorted(expected))
        self.assertEqual(len(picked), 3)

    def test_explicit_seed_is_used(self):
        for i in range(6):
            out = reference("01_valid_alternative")
            out.update(submission_id=f"c{i}", student_id=f"S{i}")
            self.batch.add(out, load(EVALS / "cases" / "01_valid_alternative.json")["submission"]["files"])
        _, out = self.main("--seed", "fixed", "--sample", "1")
        results, _ = self.batch.run()
        self.assertIn(f"Spot check: {rc.spot_check_sample(list(results.values()), 1, 'fixed')[0]}", out)

    def test_outputs_and_rubrics_are_required(self):
        for args in (["--rubrics", str(RUBRICS)], ["--outputs", str(self.batch.outputs)]):
            with self.subTest(args=args), redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                import contextlib
                with contextlib.redirect_stderr(io.StringIO()):
                    rc.main(args)

    def test_reports_create_folders_and_overwrite(self):
        target = self.tmp / "a" / "b"
        rc.write_reports(target, [], rc.summarize([], {}), [])
        rc.write_reports(target, [fake("x", 1)], rc.summarize([fake("x", 1)], {}), [])
        self.assertIn("x", (target / "release_manifest.csv").read_text())

    def test_nothing_flagged_and_no_scores(self):
        _, report = rc.write_reports(self.tmp / "r", [], rc.summarize([], {}), [])
        text = report.read_text()
        self.assertIn("No scored results.", text)
        self.assertIn("Nothing flagged.", text)


if __name__ == "__main__":
    unittest.main()
