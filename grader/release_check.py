#!/usr/bin/env python3
"""
FoxCS pre-release check for a batch of AI-assisted grading results.

The teacher approves grades in bulk: they read the rationale, check that
the criteria are right and that grading trends look sound, then push the
whole batch. Nobody re-reads every grade. So something other than a human
eye has to confirm that each grade agrees with its rubric, its evidence,
its rationale, and what actually happened when the program ran. This is
that something. Standard library only.

Every result in the batch lands in exactly one group:

  blocked  cannot be pushed until fixed (wrong rubric, bad arithmetic,
           credit without evidence, evidence quoting code that is not in
           the submission, a grade that contradicts the program run)
  review   needs the teacher's eyes (rationale disagrees with the score,
           praise that does not fit the score, the grader asked for
           review, identical feedback attached to different scores)
  ready    passed every check

Then a small random spot-check sample is drawn from the ready group,
spread across low, middle, and high scores, for the teacher to read in
full. That keeps "the rationale lines up, so the grade is right" honest:
it is tested against a few fully read results every batch.

Usage:
    python grader/release_check.py --outputs DIR --rubrics DIR
        [--submissions DIR] [--drafts FILE] [--sample N] [--seed S] [--out-dir DIR]

Writes release_manifest.csv and release_report.md. Exits 1 if anything is
blocked, so a push script can refuse to run.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

BLOCKED, REVIEW, READY = "blocked", "review", "ready"

# Words that, in a rationale, say something is wrong or absent.
NEGATIVE_CUES = re.compile(
    r"\b(missing|does not|doesn't|did not|didn't|fails?|failed|errors?|incorrect|wrong|lacks?|absent|never|instead of)\b",
    re.I,
)
# "No errors" and "runs without raising an error" are good news, not bad.
NOT_AN_ERROR = re.compile(r"\b(?:no|without)\b(?:\s+\w+){0,2}\s+errors?\b", re.I)
POSITIVE_CUES = re.compile(r"\b(correct(?:ly)?|meets|complete|accurate(?:ly)?|works|valid|clear(?:ly)?)\b", re.I)
STRONG_PRAISE = re.compile(
    r"\b(perfect|excellent|outstanding|flawless|no changes needed|meets every requirement)\b", re.I
)
BACKTICK = re.compile(r"`([^`]+)`")


@dataclass
class Finding:
    level: str  # BLOCKED or REVIEW
    code: str
    detail: str


@dataclass
class ResultCheck:
    submission_id: str
    student_id: str
    assignment_id: str
    score: float | None
    possible: float | None
    findings: list[Finding] = field(default_factory=list)

    @property
    def status(self) -> str:
        levels = {f.level for f in self.findings}
        if BLOCKED in levels:
            return BLOCKED
        return REVIEW if REVIEW in levels else READY


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------ per-result checks

def check_structure(out: dict, rubric: dict) -> list[Finding]:
    """Shape, rubric match, arithmetic, evidence for credit, a next step."""
    missing = [k for k in ("rubricResults", "score", "feedback", "review") if not isinstance(out.get(k), (dict, list))]
    if missing:
        return [Finding(BLOCKED, "schema", f"missing or malformed: {', '.join(missing)}")]
    findings = []
    expected = {c["criterionId"]: c["pointsPossible"] for c in rubric["rubric"]}
    got = [r.get("criterionId") for r in out["rubricResults"]]
    extra, absent = sorted(set(got) - set(expected)), sorted(set(expected) - set(got))
    dupes = sorted({g for g in got if got.count(g) > 1})
    if extra or absent or dupes:
        findings.append(Finding(BLOCKED, "rubric_mismatch",
                                f"not in rubric: {extra or 'none'}; not scored: {absent or 'none'}; scored twice: {dupes or 'none'}"))
    total = 0.0
    for r in out["rubricResults"]:
        cid, earned, possible = r.get("criterionId"), r.get("pointsEarned"), r.get("pointsPossible")
        if not isinstance(earned, (int, float)) or not isinstance(possible, (int, float)):
            findings.append(Finding(BLOCKED, "points_missing", f"{cid}: points are not numbers"))
            continue
        if cid in expected and possible != expected[cid]:
            findings.append(Finding(BLOCKED, "points_possible", f"{cid}: {possible} possible, rubric says {expected[cid]}"))
        if not 0 <= earned <= possible:
            findings.append(Finding(BLOCKED, "points_range", f"{cid}: {earned} outside 0 to {possible}"))
        if earned > 0 and not r.get("evidence"):
            findings.append(Finding(BLOCKED, "credit_without_evidence", f"{cid}: {earned} points with no evidence"))
        total += earned
    score = out["score"]
    earned, possible, final = score.get("standardPointsEarned"), score.get("standardPointsPossible"), score.get("finalPointsEarned")
    if not all(isinstance(v, (int, float)) for v in (earned, possible, final)):
        findings.append(Finding(BLOCKED, "score_missing", "score totals are missing or not numbers"))
    else:
        if abs(total - earned) > 1e-6:
            findings.append(Finding(BLOCKED, "sum_mismatch", f"criteria add to {total:g}, total says {earned:g}"))
        if possible != rubric["standardPointsPossible"]:
            findings.append(Finding(BLOCKED, "total_possible", f"{possible:g} possible, rubric says {rubric['standardPointsPossible']:g}"))
        if final != earned:
            findings.append(Finding(BLOCKED, "bonus_unapproved", f"final {final:g} differs from earned {earned:g}"))
    if not str(out["feedback"].get("nextStep", "")).strip():
        findings.append(Finding(BLOCKED, "no_next_step", "feedback has no next step"))
    return findings


def check_rationale_alignment(out: dict) -> list[Finding]:
    """Full marks explained with problems, or zero explained with only praise."""
    findings = []
    for r in out["rubricResults"]:
        earned, possible = r.get("pointsEarned"), r.get("pointsPossible")
        if not isinstance(earned, (int, float)) or not isinstance(possible, (int, float)) or possible <= 0:
            continue
        text = NOT_AN_ERROR.sub("", " ".join([str(r.get("rationale", ""))] + [str(e) for e in r.get("evidence", [])]))
        negative, positive = NEGATIVE_CUES.search(text), POSITIVE_CUES.search(text)
        cid = r.get("criterionId")
        if earned == possible and negative:
            findings.append(Finding(REVIEW, "full_marks_with_problems",
                                    f"{cid}: full points, but the rationale says '{negative.group()}'"))
        elif earned == 0 and positive and not negative:
            findings.append(Finding(REVIEW, "zero_with_praise",
                                    f"{cid}: zero points, but the rationale only says '{positive.group()}'"))
    return findings


def check_feedback_alignment(out: dict) -> list[Finding]:
    """Student-facing feedback that does not fit the score."""
    score, feedback = out["score"], out["feedback"]
    earned, possible = score.get("standardPointsEarned"), score.get("standardPointsPossible")
    if not isinstance(earned, (int, float)) or not isinstance(possible, (int, float)) or possible <= 0:
        return []
    findings = []
    overall = str(feedback.get("overallFeedback", ""))
    praise = STRONG_PRAISE.search(overall)
    if praise and earned < possible:
        findings.append(Finding(REVIEW, "praise_below_full", f"says '{praise.group()}' at {earned:g}/{possible:g}"))
    if earned == possible and feedback.get("priorityImprovements"):
        findings.append(Finding(REVIEW, "full_marks_with_fixes", "full marks, but lists priority improvements"))
    return findings


def check_grounding(out: dict, submission_text: str | None) -> list[Finding]:
    """Code quoted as evidence must actually be in the student's files.

    Only evidence is checked: feedback may suggest code the student did not
    write, but evidence is a claim about what they did write.
    """
    if submission_text is None:
        return []
    haystack = _norm(submission_text)
    findings = []
    for r in out["rubricResults"]:
        for ev in r.get("evidence", []):
            for quote in BACKTICK.findall(str(ev)):
                if _norm(quote) not in haystack:
                    findings.append(Finding(BLOCKED, "quote_not_in_submission",
                                            f"{r.get('criterionId')}: evidence quotes `{quote}`, which is not in the submission"))
    return findings


def check_against_run(out: dict, draft: dict | None) -> list[Finding]:
    """Grades for facts the program run already settled must agree with it.

    flawless / does_not_run: every criterion both share is a fact.
    runs_with_issues: whether it runs is a fact; how close it got is judgment.
    """
    if not draft or draft.get("band") not in ("flawless", "does_not_run", "runs_with_issues"):
        return []
    facts = {c["criterionId"]: c["pointsEarned"] for c in draft.get("rubricResults", [])}
    if draft["band"] == "runs_with_issues":
        facts = {k: v for k, v in facts.items() if k == "runs_without_errors"}
    findings = []
    for r in out["rubricResults"]:
        cid = r.get("criterionId")
        if cid in facts and r.get("pointsEarned") != facts[cid]:
            findings.append(Finding(BLOCKED, "contradicts_run",
                                    f"{cid}: graded {r.get('pointsEarned')}, but the program run ({draft['band']}) gives {facts[cid]}"))
    return findings


def check_escalation(out: dict) -> list[Finding]:
    review = out["review"]
    findings = []
    if review.get("teacherReviewRequired"):
        reasons = "; ".join(review.get("reasons") or []) or "no reason given"
        findings.append(Finding(REVIEW, "grader_requested_review", reasons))
    elif review.get("overallConfidence") == "low":
        findings.append(Finding(REVIEW, "low_confidence", "the grader marked its own confidence low"))
    return findings


# ------------------------------------------------------------ batch checks

def check_duplicate_feedback(outputs: dict[str, dict], results: dict[str, ResultCheck]) -> None:
    """The same overall feedback attached to different scores reads as a template."""
    by_text: dict[str, list[str]] = {}
    for sid, out in outputs.items():
        text = _norm(str(out.get("feedback", {}).get("overallFeedback", ""))) if isinstance(out.get("feedback"), dict) else ""
        if text:
            by_text.setdefault(text, []).append(sid)
    for sids in by_text.values():
        scores = {results[s].score for s in sids}
        if len(scores) > 1:  # different scores imply more than one result
            for s in sids:
                others = ", ".join(x for x in sids if x != s)
                results[s].findings.append(Finding(REVIEW, "duplicate_feedback",
                                                   f"same overall feedback as {others}, with a different score"))


def spot_check_sample(results: list[ResultCheck], n: int, seed: str) -> list[str]:
    """Draw up to n ready results, spread across low, middle and high scores."""
    ready = sorted((r for r in results if r.status == READY), key=lambda r: (r.score, r.submission_id))
    third = max(1, len(ready) // 3)
    groups = [ready[:third], ready[third:2 * third], ready[2 * third:]]
    rng = random.Random(seed)
    for g in groups:
        rng.shuffle(g)
    picked: list[str] = []
    while len(picked) < min(n, len(ready)):
        for g in groups:
            if g and len(picked) < n:
                picked.append(g.pop().submission_id)
    return picked


def summarize(results: list[ResultCheck], outputs: dict[str, dict]) -> dict:
    scored = [r.score / r.possible for r in results if isinstance(r.score, (int, float)) and r.possible]
    full: dict[str, list[bool]] = {}
    for out in outputs.values():
        for c in out.get("rubricResults", []) if isinstance(out.get("rubricResults"), list) else []:
            if isinstance(c.get("pointsEarned"), (int, float)) and isinstance(c.get("pointsPossible"), (int, float)):
                full.setdefault(c.get("criterionId"), []).append(c["pointsEarned"] == c["pointsPossible"])
    return {
        "count": len(results),
        "status": {s: sum(r.status == s for r in results) for s in (READY, REVIEW, BLOCKED)},
        "percent": None if not scored else {
            "mean": round(100 * statistics.mean(scored), 1),
            "median": round(100 * statistics.median(scored), 1),
            "min": round(100 * min(scored), 1),
            "max": round(100 * max(scored), 1),
        },
        "full_credit_rate": {cid: round(100 * sum(v) / len(v), 1) for cid, v in sorted(full.items())},
    }


# ------------------------------------------------------------ loading and running

def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_drafts(path: Path | None) -> dict[str, dict]:
    """Latest on-submission draft per submission, from its JSON Lines store."""
    drafts: dict[str, dict] = {}
    if path is None:
        return drafts
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            drafts[record["submission_id"]] = record
    return drafts


def submission_text(root: Path | None, submission_id: str) -> str | None:
    if root is None:
        return None
    folder = root / submission_id
    if not folder.is_dir():
        return None
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in sorted(folder.rglob("*")) if p.is_file())


def check_batch(outputs_dir: Path, rubrics_dir: Path, submissions_dir: Path | None = None,
                drafts_path: Path | None = None) -> tuple[list[ResultCheck], dict[str, dict]]:
    rubrics = {r["id"]: r for r in (_read_json(p) for p in sorted(Path(rubrics_dir).glob("*.json")))}
    drafts = load_drafts(drafts_path)
    outputs: dict[str, dict] = {}
    results: dict[str, ResultCheck] = {}
    for path in sorted(Path(outputs_dir).glob("*.json")):
        try:
            out = _read_json(path)
        except json.JSONDecodeError as exc:
            results[path.stem] = ResultCheck(path.stem, "", "", None, None,
                                             [Finding(BLOCKED, "unreadable", f"not valid JSON: {exc.msg}")])
            continue
        sid = str(out.get("submission_id") or path.stem)
        aid = str(out.get("assignment_id") or (out.get("assignment") or {}).get("id") or "")
        score = out.get("score") if isinstance(out.get("score"), dict) else {}
        check = ResultCheck(sid, str(out.get("student_id", "")), aid,
                            score.get("finalPointsEarned"), score.get("standardPointsPossible"))
        outputs[sid] = out
        results[sid] = check
        rubric = rubrics.get(aid)
        if rubric is None:
            check.findings.append(Finding(BLOCKED, "no_rubric", f"no rubric for assignment '{aid}'"))
            continue
        structure = check_structure(out, rubric)
        check.findings += structure
        if any(f.code == "schema" for f in structure):
            continue
        text = submission_text(submissions_dir, sid)
        if submissions_dir is not None and text is None:
            check.findings.append(Finding(REVIEW, "submission_missing", "submission files not found, so evidence quotes were not verified"))
        check.findings += check_grounding(out, text)
        check.findings += check_against_run(out, drafts.get(sid))
        check.findings += check_rationale_alignment(out)
        check.findings += check_feedback_alignment(out)
        check.findings += check_escalation(out)
    check_duplicate_feedback(outputs, results)
    return list(results.values()), outputs


def batch_seed(results: list[ResultCheck]) -> str:
    """Same batch, same sample: reproducible without anyone choosing it."""
    return hashlib.sha256("|".join(sorted(r.submission_id for r in results)).encode()).hexdigest()[:16]


def write_reports(out_dir: Path, results: list[ResultCheck], summary: dict, sample: list[str]) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "release_manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["submission_id", "student_id", "assignment_id", "score", "possible", "status", "spot_check", "findings"])
        for r in sorted(results, key=lambda r: r.submission_id):
            w.writerow([r.submission_id, r.student_id, r.assignment_id, r.score, r.possible, r.status,
                        "yes" if r.submission_id in sample else "",
                        " | ".join(f"{f.code}: {f.detail}" for f in r.findings)])
    report = out_dir / "release_report.md"
    st, pct = summary["status"], summary["percent"]
    lines = [
        "# Release check",
        "",
        f"{summary['count']} results: **{st[READY]} ready**, **{st[REVIEW]} need review**, **{st[BLOCKED]} blocked**.",
        "",
        "Push is " + ("**not allowed** until every blocked result is fixed." if st[BLOCKED] else "allowed once the review items and spot checks are done."),
        "",
        "## Score spread",
        "",
        "No scored results." if pct is None else
        f"Mean {pct['mean']}%, median {pct['median']}%, range {pct['min']}% to {pct['max']}%.",
        "",
        "| Criterion | Full credit |",
        "| --- | --- |",
        *[f"| {cid} | {rate}% |" for cid, rate in summary["full_credit_rate"].items()],
        "",
        "## Spot check: read these in full",
        "",
        *([f"- {sid}" for sid in sample] or ["- (none ready)"]),
        "",
        "## Needs attention",
        "",
    ]
    attention = [r for r in sorted(results, key=lambda r: (r.status != BLOCKED, r.submission_id)) if r.findings]
    lines += [f"- **{r.submission_id}** ({r.status}): " + "; ".join(f.detail for f in r.findings) for r in attention] or ["- Nothing flagged."]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--outputs", type=Path, required=True, help="Folder of grading results, one JSON per submission")
    parser.add_argument("--rubrics", type=Path, required=True, help="Folder of assignment rubric JSON files")
    parser.add_argument("--submissions", type=Path, help="Folder of <submission_id>/ folders, to verify quoted evidence")
    parser.add_argument("--drafts", type=Path, help="on_submission.py draft store (JSON Lines), to cross-check program runs")
    parser.add_argument("--sample", type=int, default=3, help="How many ready results to spot-check (default 3)")
    parser.add_argument("--seed", help="Override the spot-check seed (default: derived from the batch)")
    parser.add_argument("--out-dir", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    results, outputs = check_batch(args.outputs, args.rubrics, args.submissions, args.drafts)
    summary = summarize(results, outputs)
    seed = args.seed if args.seed is not None else batch_seed(results)
    sample = spot_check_sample(results, args.sample, seed)
    manifest, report = write_reports(args.out_dir, results, summary, sample)
    st = summary["status"]
    print(f"{summary['count']} results: {st[READY]} ready, {st[REVIEW]} need review, {st[BLOCKED]} blocked.")
    print(f"  Spot check: {', '.join(sample) or '(none)'}")
    print(f"  Report: {report}\n  Manifest: {manifest}")
    return 1 if st[BLOCKED] else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
