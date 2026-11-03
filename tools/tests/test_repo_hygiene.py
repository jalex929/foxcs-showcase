"""
Repository-level guarantees for the public showcase:

- The repository itself scans clean, so the test suite fails even if the CI
  scanner step is ever removed.
- The scanner's command-line exit codes behave as CI relies on.
- The committed synthetic fixtures are exactly what tools/make_fixtures.py
  generates, so nobody can hand-edit real data into them unnoticed.
- Docs only link to files that exist.

Run from the repository root with:
    python -m unittest discover -s tools/tests -v
"""

import filecmp
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import check_showcase_clean as scanner  # noqa: E402

FIXTURES = REPO_ROOT / "grader" / "sample-submissions"


class TestRepositoryIsClean(unittest.TestCase):
    def test_no_findings_in_this_repository(self):
        findings = scanner.scan(REPO_ROOT)
        self.assertEqual(
            findings, [],
            "\n".join(f"{p.relative_to(REPO_ROOT)}:{n} [{r}]" for p, n, r, _ in findings),
        )


class TestScannerCli(unittest.TestCase):
    def run_cli(self, root):
        return subprocess.run(
            [sys.executable, str(TOOLS / "check_showcase_clean.py"), str(root)],
            capture_output=True, text=True,
        )

    def test_clean_folder_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "ok.md").write_text("Synthetic STUDENT-0001 only.", encoding="utf-8")
            result = self.run_cli(tmp)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Clean", result.stdout)

    def test_dirty_folder_exits_1_and_reports_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "notes.md").write_text("line one\nemail me at someone@" + "cps.edu\n", encoding="utf-8")
            result = self.run_cli(tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn("notes.md:2", result.stdout)
        self.assertIn("district-email", result.stdout)

    def test_skips_git_and_pycache(self):
        with tempfile.TemporaryDirectory() as tmp:
            for d in (".git", "__pycache__", "htmlcov"):
                Path(tmp, d).mkdir()
                Path(tmp, d, "blob.bin").write_bytes(b"\x00\x01")
            Path(tmp, ".coverage").write_bytes(b"\x00sqlite")
            result = self.run_cli(tmp)
        self.assertEqual(result.returncode, 0, result.stdout)


class TestFixturesAreReproducible(unittest.TestCase):
    def test_committed_fixtures_match_generator(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(TOOLS / "make_fixtures.py"), tmp],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            generated = sorted(p.relative_to(tmp) for p in Path(tmp).rglob("*") if p.is_file())
            committed = sorted(
                p.relative_to(FIXTURES) for p in FIXTURES.rglob("*")
                if p.is_file() and p.name != "README.md"
            )
            self.assertEqual(generated, committed, "fixture file list differs from generator output")
            for rel in generated:
                with self.subTest(file=str(rel)):
                    self.assertTrue(
                        filecmp.cmp(Path(tmp, rel), FIXTURES / rel, shallow=False),
                        f"{rel} differs from generator output. Regenerate with: "
                        "python tools/make_fixtures.py grader/sample-submissions",
                    )


class TestDocLinks(unittest.TestCase):
    LINK_RE = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")

    def test_relative_markdown_links_resolve(self):
        broken = []
        for md in REPO_ROOT.rglob("*.md"):
            if ".git" in md.parts:
                continue
            for target in self.LINK_RE.findall(md.read_text(encoding="utf-8")):
                if re.match(r"^[a-z]+://", target) or target.startswith("mailto:"):
                    continue
                if not (md.parent / target).exists():
                    broken.append(f"{md.relative_to(REPO_ROOT)} -> {target}")
        self.assertEqual(broken, [])


if __name__ == "__main__":
    unittest.main()
