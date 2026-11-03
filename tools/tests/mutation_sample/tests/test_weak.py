import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sample  # noqa: E402


class Weak(unittest.TestCase):
    """Runs every line (full coverage) but checks almost nothing."""

    def test_runs(self):
        sample.grade(1, 0)
        sample.grade(4, 4)
        sample.grade(0, 4)
        sample.is_passing(5)
