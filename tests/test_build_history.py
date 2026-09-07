from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_margin_maintenance_history as builder
from tw_margin_rate.corporate_actions import StockSplit

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


class FullBuilderFixtureTests(unittest.TestCase):
    def test_split_and_zero_price_are_used_in_both_market_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = SimpleNamespace(
                stock_data=root, workspace=root,
                twse_company_info=root / "companies.json",
                twse_delisted_html=root / "delisted.html",
                twse_newlisting_json=root / "newlisting.json",
                stock_splits=root / "splits.json",
                warmup_start="2001-01-05", display_start="2026-09-04",
                end="2026-09-07", no_finmind_fetch=True,
                margin_money_history=root / "money.json", market_cap_history=root / "cap.json",
            )
            joined = root / "year=2026/data.parquet"
            joined.parent.mkdir()
            rows = []
            for day in ("2026-09-04", "2026-09-07"):
                for code in ("6949", "4747"):
                    split = day == "2026-09-07" and code == "6949"
                    close = (5 if split else 100) if code == "6949" else (60 if day == "2026-09-04" else 0)
                    balance = 200 if split else 10
                    rows.append({
                        "date": day, "stock_id": code, "close": close,
                        "MarginPurchaseYesterdayBalance": balance,
                        "MarginPurchaseTodayBalance": balance,
                        "MarginPurchaseBuy": 0, "MarginPurchaseSell": 0,
                        "MarginPurchaseCashRepayment": 0,
                    })
            pd.DataFrame(rows).to_parquet(joined, index=False)
            dates = ("2026-09-04", "2026-09-07")
            money = {market: {day: 100.0 for day in dates} for market in ("twse", "tpex")}
            cap_meta = {
                "market_cap_starts": {m: dates[0] for m in money},
                "twse_source": "fixture", "tpex_source": "fixture",
                "twse_scope": "fixture", "validation_tolerance_pct": 1,
            }
            with (
                patch.object(builder, "load_stock_splits", return_value=(StockSplit("6949", dates[1], 20),)),
                patch.object(builder, "load_market_reference", return_value=({"6949": "twse", "4747": "tpex"}, {}, {})),
                patch.object(builder, "latest_icloud_margin_date", return_value=dates[1]),
                patch.object(builder, "prepare_local_history", return_value=([joined], {day: {"twse": 47000.0, "tpex": 400.0} for day in dates}, dates[1])),
                patch.object(builder, "load_margin_money_history", return_value=money),
                patch.object(builder, "load_market_cap_history", return_value=(money, cap_meta)),
                patch.object(builder, "file_sha256", return_value="fixture"),
            ):
                result = builder.build_history(args)
            for market in ("twse", "tpex"):
                self.assertEqual([r["maintenance"] for r in result["markets"][market]], [166.67, 166.67])
                self.assertEqual([r["stock_count"] for r in result["markets"][market]], [1, 1])
            self.assertEqual(result["markets"]["twse"][-1]["financed_balance"], 200)
            self.assertEqual(result["metadata"]["stock_splits"]["event_count"], 1)


if __name__ == "__main__":
    unittest.main()
