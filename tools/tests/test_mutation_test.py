"""
Tests for the mutation tester itself, against a tiny sample module with a
strong test suite and a weak one. The weak suite runs every line (full
coverage) but asserts almost nothing; mutation testing must tell the two
apart. That difference is the whole point of the tool.

Run from the repository root with:
    python -m unittest discover -s tools/tests -v
"""

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import mutation_test as mt  # noqa: E402

SAMPLE = Path(__file__).resolve().parent / "mutation_sample"
SOURCE = (SAMPLE / "sample.py").read_text(encoding="utf-8")
STRONG = {"sample": ("sample.py", "tests", "test_strong.py")}
WEAK = {"sample": ("sample.py", "tests", "test_weak.py")}


def run(targets):
    return mt.run_target("sample", jobs=2, log=lambda *_: None, root=SAMPLE, targets=targets)


class TestSites(unittest.TestCase):
    def test_finds_each_operator(self):
        ops = {s.operator for s in mt.find_sites(SOURCE)}
        self.assertEqual(ops, {"if", "compare", "boolop", "not", "constant", "return"})

    def test_skips_docstrings_and_module_settings(self):
        lines = SOURCE.splitlines()
        for s in mt.find_sites(SOURCE):
            with self.subTest(site=s):
                self.assertNotIn("LIMIT", lines[s.line - 1])
                self.assertNotIn('"""', lines[s.line - 1])

    def test_skips_main_guard(self):
        sites = mt.find_sites('def f():\n    return 1\n\nif __name__ == "__main__":\n    f()\n')
        self.assertTrue(all(s.line == 2 for s in sites))

    def test_every_mutant_is_valid_python_and_differs(self):
        original = ast.dump(ast.parse(SOURCE))
        for s in mt.find_sites(SOURCE):
            with self.subTest(site=s):
                mutated = mt.mutate(SOURCE, s.index)
                self.assertNotEqual(ast.dump(ast.parse(mutated)), original)

    def test_one_change_per_mutant(self):
        sites = mt.find_sites(SOURCE)
        a, b = mt.mutate(SOURCE, sites[0].index), mt.mutate(SOURCE, sites[1].index)
        self.assertNotEqual(a, b)

    def test_specific_mutations(self):
        def mutant_for(src, operator):
            site = next(s for s in mt.find_sites(src) if s.operator == operator)
            return mt.mutate(src, site.index)
        self.assertIn("x != 1", mutant_for("def f(x):\n    return x == 1\n", "compare"))
        self.assertIn("a or b", mutant_for("def f(a, b):\n    return a and b\n", "boolop"))
        self.assertIn("return x", mutant_for("def f(x):\n    return not x\n", "not"))
        self.assertIn("if not x", mutant_for("def f(x):\n    if x:\n        return 1\n", "if"))
        self.assertIn("return None", mutant_for("def f(x):\n    return x\n", "return"))
        self.assertIn("a - b", mutant_for("def f(a, b):\n    return a + b\n", "arithmetic"))
        self.assertIn("False", mutant_for("def f():\n    return True\n", "constant"))
        self.assertIn("while not x", mutant_for("def f(x):\n    while x:\n        x -= 1\n", "if"))
        self.assertIn("2 if not x", mutant_for("def f(x):\n    return 2 if x else 3\n", "if"))

    def test_identical_changes_on_one_line_get_distinct_keys(self):
        src = "def f(a, b):\n    return a <= 0 or b < 0\n"
        keys = [s.key(src.splitlines()) for s in mt.find_sites(src) if s.operator == "constant"]
        self.assertEqual(len(keys), 2)
        self.assertEqual(len(set(keys)), 2)

    def test_keys_are_stable_and_readable(self):
        s = mt.find_sites(SOURCE)[0]
        key = s.key(SOURCE.splitlines())
        self.assertEqual(key.count(" | "), 3)
        self.assertEqual(key, mt.find_sites(SOURCE)[0].key(SOURCE.splitlines()))


class TestRuns(unittest.TestCase):
    def test_strong_suite_kills_every_mutant(self):
        summary = mt.summarize(run(STRONG), {})
        self.assertEqual(summary["survived"], 0, summary["unexplained"])
        self.assertEqual(summary["score"], 100.0)

    def test_weak_suite_with_full_coverage_lets_mutants_survive(self):
        summary = mt.summarize(run(WEAK), {})
        self.assertGreater(summary["survived"], 5)
        self.assertLess(summary["score"], 70.0)

    def test_documented_equivalents_are_excluded_not_counted_as_kills(self):
        report = run(WEAK)
        survivors = [m for m in report["mutants"] if m["outcome"] == "survived"]
        summary = mt.summarize(report, {survivors[0]["key"]: "reason"})
        self.assertEqual(summary["equivalent"], 1)
        self.assertEqual(summary["survived"], len(survivors) - 1)

    def test_original_file_is_never_modified(self):
        run(STRONG)
        self.assertEqual((SAMPLE / "sample.py").read_text(encoding="utf-8"), SOURCE)

    def test_failing_baseline_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "sample.py").write_text(SOURCE)
            (root / "tests" / "test_bad.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test(self):\n        self.fail()\n")
            with self.assertRaises(SystemExit):
                mt.run_target("sample", 1, log=lambda *_: None, root=root,
                              targets={"sample": ("sample.py", "tests", "test_bad.py")})

    def test_timeouts_count_as_detected(self):
        report = {"target": "t", "mutants": [{"outcome": "timeout", "key": "k"}, {"outcome": "killed", "key": "j"}]}
        self.assertEqual(mt.summarize(report, {})["score"], 100.0)


class TestEquivalentsFile(unittest.TestCase):
    def test_every_entry_has_a_reason_and_matches_real_code(self):
        entries = json.loads(mt.EQUIVALENTS.read_text(encoding="utf-8"))
        for e in entries:
            with self.subTest(key=e["key"]):
                self.assertGreater(len(e["reason"]), 20)
                source = (mt.ROOT / e["file"]).read_text(encoding="utf-8")
                keys = {s.key(source.splitlines()) for s in mt.find_sites(source)}
                self.assertIn(e["key"], keys, "stale entry: the code it excuses has changed")


if __name__ == "__main__":
    unittest.main()
