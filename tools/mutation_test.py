#!/usr/bin/env python3
"""
Mutation testing for the grading code. Standard library only.

Coverage says every line ran during the tests. Mutation testing asks the
harder question: if this line were wrong, would any test notice? It makes
one small change at a time (a "mutant"), runs the tests, and expects them
to fail. A mutant the tests don't catch has "survived", which points at
behavior nothing actually checks.

    python tools/mutation_test.py                      # every target
    python tools/mutation_test.py --target release_check --jobs 4

Mutation operators (one change per mutant):
  compare     == <-> !=, < <-> <=, > <-> >=, in <-> not in, is <-> is not
  boolop      and <-> or
  not         `not x` -> `x`
  if          `if cond:` -> `if not (cond):`  (also while and ternaries)
  constant    True <-> False, number n -> n + 1 (inside functions only)
  arithmetic  + <-> -, * <-> /, // -> /
  return      `return value` -> `return None`

Deliberately not mutated: string literals (messages and regex text would
flood the report with changes no test should pin), module-level settings
(a default timeout of 5 or 6 seconds is configuration, not behavior), and
the `if __name__ == "__main__"` guard.

A surviving mutant either points at a missing test or is "equivalent" (it
cannot change behavior). Equivalents are listed with a reason in
tools/mutation_equivalents.json, so every exception is written down and
reviewed. Exits 1 if any mutant survives that is not listed there.
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EQUIVALENTS = ROOT / "tools" / "mutation_equivalents.json"

# target name -> (module path, unittest discover dir, test file pattern)
TARGETS = {
    "on_submission": ("grader/on_submission.py", "grader/tests", "test_on_submission.py"),
    "release_check": ("grader/release_check.py", "grader/tests", "test_release_check.py"),
    "score_output": ("evals/score_output.py", "evals/tests", "test_*.py"),
    "run_model": ("evals/run_model.py", "evals/tests", "test_run_model.py"),
    "review_worker": ("grader/review_worker.py", "grader/tests", "test_review_worker.py"),
}

COMPARE_SWAP = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.LtE, ast.LtE: ast.Lt,
    ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.In: ast.NotIn, ast.NotIn: ast.In,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
}
BINOP_SWAP = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult, ast.FloorDiv: ast.Div}


@dataclass(frozen=True)
class Site:
    """One place a mutation can be applied."""
    index: int
    line: int
    col: int
    operator: str
    description: str

    def key(self, source_lines: list[str]) -> str:
        """Stable identity for the equivalents list: survives unrelated edits
        elsewhere. The column keeps two identical changes on one line apart,
        so excusing one never silently excuses the other."""
        line = source_lines[self.line - 1]
        col = self.col - (len(line) - len(line.lstrip()))
        return f"{self.operator} | {line.strip()} | col {col} | {self.description}"


def _is_main_guard(node: ast.AST) -> bool:
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__")


class _Mutator(ast.NodeTransformer):
    """Walks a module in a fixed order. In counting mode it records every
    site; in apply mode it changes only the site whose index matches."""

    def __init__(self, apply_index: int | None = None):
        self.apply_index = apply_index
        self.sites: list[Site] = []
        self.depth = 0  # > 0 inside a function body

    def _site(self, node: ast.AST, operator: str, description: str) -> bool:
        site = Site(len(self.sites), getattr(node, "lineno", 0), getattr(node, "col_offset", 0), operator, description)
        self.sites.append(site)
        return site.index == self.apply_index

    def _function(self, node):
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1
        return node

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function
    visit_Lambda = _function

    def visit_Expr(self, node):
        # Docstrings and bare string expressions are skipped entirely.
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node
        return self.generic_visit(node)

    def visit_If(self, node):
        if _is_main_guard(node):
            return node
        self.generic_visit(node)
        if self.depth and self._site(node, "if", "condition inverted"):
            node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
        return node

    def visit_While(self, node):
        self.generic_visit(node)
        if self._site(node, "if", "loop condition inverted"):
            node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
        return node

    def visit_IfExp(self, node):
        self.generic_visit(node)
        if self._site(node, "if", "ternary condition inverted"):
            node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
        return node

    def visit_Compare(self, node):
        self.generic_visit(node)
        for i, op in enumerate(node.ops):
            new = COMPARE_SWAP.get(type(op))
            if new and self._site(node, "compare", f"{type(op).__name__} -> {new.__name__}"):
                node.ops[i] = new()
        return node

    def visit_BoolOp(self, node):
        self.generic_visit(node)
        new = ast.Or if isinstance(node.op, ast.And) else ast.And
        if self._site(node, "boolop", f"{type(node.op).__name__} -> {new.__name__}"):
            node.op = new()
        return node

    def visit_UnaryOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.Not) and self._site(node, "not", "not removed"):
            return node.operand
        return node

    def visit_BinOp(self, node):
        self.generic_visit(node)
        new = BINOP_SWAP.get(type(node.op))
        if new and self._site(node, "arithmetic", f"{type(node.op).__name__} -> {new.__name__}"):
            node.op = new()
        return node

    def visit_Constant(self, node):
        if not self.depth:
            return node  # module-level settings are configuration, not behavior
        if isinstance(node.value, bool):  # checked first: a bool is also an int
            new = not node.value
        elif isinstance(node.value, (int, float)):
            new = node.value + 1
        else:
            return node
        if self._site(node, "constant", f"{node.value} -> {new}"):
            return ast.copy_location(ast.Constant(new), node)
        return node

    def visit_Return(self, node):
        self.generic_visit(node)
        returns_value = node.value is not None and not (isinstance(node.value, ast.Constant) and node.value.value is None)
        if returns_value and self._site(node, "return", "value -> None"):
            node.value = ast.Constant(None)
        return node


def find_sites(source: str) -> list[Site]:
    mutator = _Mutator()
    mutator.visit(ast.parse(source))
    return mutator.sites


def mutate(source: str, index: int) -> str:
    tree = ast.parse(source)
    mutator = _Mutator(apply_index=index)
    tree = mutator.visit(copy.deepcopy(tree))
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


# ------------------------------------------------------------ running

def run_tests(workdir: Path, test_dir: str, pattern: str, timeout: float) -> str:
    """'passed', 'failed' or 'timeout' for the test command in workdir."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-f", "-s", test_dir, "-p", pattern],
            cwd=workdir, capture_output=True, timeout=timeout,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired:
        return "timeout"
    return "passed" if proc.returncode == 0 else "failed"


def _copy_repo(root: Path, dest: Path) -> Path:
    ignore = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".coverage", "htmlcov", ".ruff_cache", "mutants")
    shutil.copytree(root, dest, ignore=ignore)
    return dest


def run_target(name: str, jobs: int, limit: int | None = None, log=print,
               root: Path = ROOT, targets: dict = TARGETS) -> dict:
    module, test_dir, pattern = targets[name]
    source = (root / module).read_text(encoding="utf-8")
    sites = find_sites(source)[:limit] if limit else find_sites(source)
    lines = source.splitlines()

    with tempfile.TemporaryDirectory(prefix="mutation-") as tmp:
        workers = [_copy_repo(root, Path(tmp) / f"w{i}") for i in range(max(1, jobs))]
        start = time.monotonic()
        if run_tests(workers[0], test_dir, pattern, timeout=600) != "passed":
            raise SystemExit(f"{name}: tests fail before any mutation; fix them first")
        baseline = time.monotonic() - start
        timeout = max(10.0, baseline * 3)

        free: queue.Queue[Path] = queue.Queue()
        for w in workers:
            free.put(w)
        results: list[dict] = [None] * len(sites)  # type: ignore[list-item]
        lock = threading.Lock()
        done = [0]

        def work(site: Site):
            w = free.get()
            target = w / module
            try:
                target.write_text(mutate(source, site.index), encoding="utf-8")
                outcome = run_tests(w, test_dir, pattern, timeout)
            finally:
                target.write_text(source, encoding="utf-8")
                free.put(w)
            results[site.index] = {"line": site.line, "operator": site.operator,
                                   "description": site.description, "key": site.key(lines),
                                   "outcome": {"passed": "survived", "failed": "killed", "timeout": "timeout"}[outcome]}
            with lock:
                done[0] += 1
                if done[0] % 25 == 0 or done[0] == len(sites):
                    log(f"  {name}: {done[0]}/{len(sites)} mutants run")

        threads = []
        pending: queue.Queue[Site] = queue.Queue()
        for s in sites:
            pending.put(s)

        def drain():
            while True:
                try:
                    s = pending.get_nowait()
                except queue.Empty:
                    return
                work(s)

        for _ in workers:
            t = threading.Thread(target=drain)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    return {"target": name, "module": module, "baseline_seconds": round(baseline, 1), "mutants": results}


def load_equivalents(path: Path = EQUIVALENTS) -> dict[str, str]:
    if not path.exists():
        return {}
    return {e["key"]: e["reason"] for e in json.loads(path.read_text(encoding="utf-8"))}


def summarize(report: dict, equivalents: dict[str, str]) -> dict:
    muts = report["mutants"]
    survived = [m for m in muts if m["outcome"] == "survived"]
    unexplained = [m for m in survived if m["key"] not in equivalents]
    detected = sum(m["outcome"] in ("killed", "timeout") for m in muts)
    scored = len(muts) - (len(survived) - len(unexplained))  # equivalents can't be killed, so they're excluded
    return {
        "target": report["target"],
        "mutants": len(muts),
        "killed": sum(m["outcome"] == "killed" for m in muts),
        "timeout": sum(m["outcome"] == "timeout" for m in muts),
        "equivalent": len(survived) - len(unexplained),
        "survived": len(unexplained),
        "score": 100.0 if scored == 0 else round(100 * detected / scored, 1),
        "unexplained": unexplained,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", action="append", choices=sorted(TARGETS), help="Default: all")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    parser.add_argument("--limit", type=int, help="Only the first N mutants per target (for a quick look)")
    parser.add_argument("--report", type=Path, help="Write the full JSON report here")
    args = parser.parse_args(argv)

    equivalents = load_equivalents()
    reports, summaries = [], []
    for name in args.target or sorted(TARGETS):
        print(f"{name}: generating mutants")
        report = run_target(name, args.jobs, args.limit)
        reports.append(report)
        summaries.append(summarize(report, equivalents))

    print(f"\n{'target':<16}{'mutants':>8}{'killed':>8}{'timeout':>8}{'equiv':>7}{'survived':>9}{'score':>8}")
    for s in summaries:
        print(f"{s['target']:<16}{s['mutants']:>8}{s['killed']:>8}{s['timeout']:>8}{s['equivalent']:>7}{s['survived']:>9}{s['score']:>7}%")
    bad = [m for s in summaries for m in s["unexplained"]]
    for s in summaries:
        for m in s["unexplained"]:
            print(f"  SURVIVED {TARGETS[s['target']][0]}:{m['line']} [{m['operator']}] {m['description']}\n    key: {m['key']}")
    if args.report:
        args.report.write_text(json.dumps({"summaries": summaries, "reports": reports}, indent=2), encoding="utf-8")
    print("\nAll mutants killed or documented as equivalent." if not bad
          else f"\n{len(bad)} mutant(s) survived: add a test, or document why it is equivalent in tools/mutation_equivalents.json.")
    return 1 if bad else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
