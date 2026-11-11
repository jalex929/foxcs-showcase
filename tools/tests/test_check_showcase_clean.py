"""The leak scanner must flag each category it claims to catch, and stay quiet on clean text."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_showcase_clean as scanner

# Built from pieces so this test file does not itself trip the scanner.
SAMPLES = {
    "licensed-vendor": "Workbook from G" + "Metrix",
    "real-pseudonym-format": "student PY1-A-" + "ALPHA01",
    "pseudonym-term": "each student gets a code" + "name",
    "district-email": "teacher@" + "cps.edu",
    "live-domain": "https://foxcs" + ".online/course",
    "ipv4": "server at 10.20." + "30.7",
    "hardcoded-password": "PASS" + 'WORD = "ABC123"',
}


class TestScanner(unittest.TestCase):
    def _scan_text(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "doc.md").write_text(text, encoding="utf-8")
            return {name for _, _, name, _ in scanner.scan(Path(tmp))}

    def test_each_category_is_flagged(self):
        for rule, text in SAMPLES.items():
            with self.subTest(rule=rule):
                self.assertIn(rule, self._scan_text(text))

    def test_clean_text_passes(self):
        self.assertEqual(self._scan_text("Pseudonymous IDs like STUDENT-0001. Localhost is 127.0.0.1."), set())

    def test_documentation_addresses_pass(self):
        # RFC 5737 ranges are reserved for examples, so they can never point at a real server.
        self.assertEqual(self._scan_text("Probe 192.0.2.1, 198.51.100.7 and 203.0.113.255."), set())

    def test_neighbors_of_documentation_ranges_are_still_flagged(self):
        for text in ("192.0." + "3.1", "198.51." + "101.7", "203.0." + "114.9", "10.192." + "0.2"):  # pieces, as above
            with self.subTest(text):
                self.assertIn("ipv4", self._scan_text("host " + text))

    def test_binary_files_are_flagged_for_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "workbook.pdf").write_bytes(b"%PDF-1.7")
            self.assertIn("binary-file", {n for _, _, n, _ in scanner.scan(Path(tmp))})


if __name__ == "__main__":
    unittest.main()
