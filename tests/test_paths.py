from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tw_margin_rate.paths import (
    discover_stock_data,
    latest_complete_stock_info,
    local_env_path,
)


class PathTests(unittest.TestCase):
    def test_explicit_stock_data_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            expected = Path(temp).resolve()
            self.assertEqual(discover_stock_data(Path(temp)), expected)

    def test_secret_path_is_outside_project(self) -> None:
        self.assertNotEqual(local_env_path().parent, ROOT)
        self.assertIn("Application Support", str(local_env_path()))

    def test_stock_info_skips_newer_index_only_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "raw/stock_info/2026"
            directory.mkdir(parents=True)
            full = directory / "2026-08-27.parquet"
            index_only = directory / "2026-08-29.parquet"
            pd.DataFrame(
                {
                    "stock_id": [f"{i:04d}" for i in range(2_000)],
                    "type": ["twse"] * 2_000,
                    "date": ["2026-08-27"] * 2_000,
                }
            ).to_parquet(full)
            pd.DataFrame(
                {
                    "stock_id": ["TAIEX", "TPEx"],
                    "type": ["twse", "tpex"],
                    "date": [None, None],
                }
            ).to_parquet(index_only)

            self.assertEqual(
                latest_complete_stock_info(Path(temp) / "raw/stock_info"), full
            )


if __name__ == "__main__":
    unittest.main()
