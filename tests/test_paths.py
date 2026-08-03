from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tw_margin_rate.paths import discover_stock_data, local_env_path


class PathTests(unittest.TestCase):
    def test_explicit_stock_data_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            expected = Path(temp).resolve()
            self.assertEqual(discover_stock_data(Path(temp)), expected)

    def test_secret_path_is_outside_project(self) -> None:
        self.assertNotEqual(local_env_path().parent, ROOT)
        self.assertIn("Application Support", str(local_env_path()))


if __name__ == "__main__":
    unittest.main()
