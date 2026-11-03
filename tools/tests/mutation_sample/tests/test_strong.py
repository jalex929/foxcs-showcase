import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sample  # noqa: E402


class Strong(unittest.TestCase):
    def test_grade(self):
        self.assertIsNone(sample.grade(1, 0))
        self.assertIsNone(sample.grade(0, -1))
        self.assertIsNone(sample.grade(-1, 4))
        self.assertEqual(sample.grade(4, 4), "full")
        self.assertEqual(sample.grade(5, 4), "full")
        self.assertEqual(sample.grade(3, 4), "partial")
        self.assertEqual(sample.grade(1, 1), "full")
        self.assertEqual(sample.grade(0, 4), "none")
        self.assertEqual(sample.grade(1, 4), "partial")
        self.assertEqual(sample.grade(0, 1), "none")

    def test_passing(self):
        self.assertTrue(sample.is_passing(5))
        self.assertFalse(sample.is_passing(4))
