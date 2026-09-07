from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_margin_maintenance_history import (
    MARGIN_COLUMNS,
    PRICE_COLUMNS,
    load_cache,
    load_cache_day,
    local_margin_cache_boundary,
    current_markets_from_stock_info,
)


class StockInfoMarketTests(unittest.TestCase):
    def test_latest_market_wins_regardless_of_row_order(self) -> None:
        rows = [
            {"stock_id": "6423", "type": "twse", "date": "2024-12-04"},
            {"stock_id": "6423", "type": "twse", "date": "2026-01-22"},
            {"stock_id": "6423", "type": "tpex", "date": "2026-09-07"},
        ]
        for ordered in (rows, rows[::-1]):
            self.assertEqual(current_markets_from_stock_info(pd.DataFrame(ordered)), {"6423": "tpex"})

    def test_same_market_duplicates_and_undated_records_are_safe(self) -> None:
        rows = [{"stock_id": "2330", "type": "twse", "date": date} for date in ("2026-09-07", "2026-09-07", "None", None)]
        self.assertEqual(current_markets_from_stock_info(pd.DataFrame(rows)), {"2330": "twse"})

    def test_newest_date_market_conflict_is_rejected(self) -> None:
        rows = [{"stock_id": "6423", "type": market, "date": "2026-09-07"} for market in ("twse", "tpex")]
        with self.assertRaisesRegex(ValueError, "6423.*衝突"):
            current_markets_from_stock_info(pd.DataFrame(rows))

    def test_undated_conflicting_market_is_rejected(self) -> None:
        rows = [{"stock_id": "6423", "type": "twse", "date": "None"}, {"stock_id": "6423", "type": "tpex", "date": "2026-09-07"}]
        with self.assertRaisesRegex(ValueError, "6423.*日期不明"):
            current_markets_from_stock_info(pd.DataFrame(rows))

    def test_historical_market_conflict_does_not_override_newest(self) -> None:
        rows = [{"stock_id": "6423", "type": market, "date": "2026-01-22"} for market in ("twse", "tpex")]
        rows.append({"stock_id": "6423", "type": "tpex", "date": "2026-09-07"})
        self.assertEqual(current_markets_from_stock_info(pd.DataFrame(rows)), {"6423": "tpex"})


class CacheDayTests(unittest.TestCase):
    def write_cache(self, root: Path, name: str, rows: list[dict[str, object]]) -> Path:
        path = root / name
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump({"data": rows}, handle)
        return path

    def test_empty_cache_has_expected_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_cache(Path(temp), "empty.json.gz", [])
            frame = load_cache(path, MARGIN_COLUMNS)
            self.assertTrue(frame.empty)
            self.assertEqual(list(frame.columns), MARGIN_COLUMNS)

    def test_paired_empty_cache_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            margin = self.write_cache(root, "margin.json.gz", [])
            prices = self.write_cache(root, "prices.json.gz", [])
            self.assertIsNone(load_cache_day(margin, prices))

    def test_partial_empty_cache_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            margin = self.write_cache(root, "margin.json.gz", [])
            price_row = {column: 1 for column in PRICE_COLUMNS}
            prices = self.write_cache(root, "prices.json.gz", [price_row])
            with self.assertRaisesRegex(ValueError, "cache is incomplete"):
                load_cache_day(margin, prices)

    def test_cache_boundary_uses_margin_date_when_prices_are_newer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_data = Path(temp)
            margin = stock_data / "raw/chips_margin/2026/2026-07-24.parquet"
            prices = stock_data / "raw/prices/2026/2026-07-27.parquet"
            margin.parent.mkdir(parents=True)
            prices.parent.mkdir(parents=True)
            margin.touch()
            prices.touch()
            self.assertEqual(
                local_margin_cache_boundary(stock_data, "2026-08-03"),
                "2026-07-24",
            )


if __name__ == "__main__":
    unittest.main()
