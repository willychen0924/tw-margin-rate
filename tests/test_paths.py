from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tw_margin_rate.paths import discover_stock_data


class PathTests(unittest.TestCase):
    def test_explicit_stock_data_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            expected = Path(temp).resolve()
            self.assertEqual(discover_stock_data(Path(temp)), expected)


if __name__ == "__main__":
    unittest.main()

