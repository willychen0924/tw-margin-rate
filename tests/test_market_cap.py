from __future__ import annotations

import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fetch_market_cap_history import (
    derive_twse_from_reference_rows,
    is_stock_code,
    is_twse_for_day,
    last_day_per_month,
    official_twse_weekly,
    parse_finmind_twse_rows,
    parse_tpex_payload,
    parse_twse_mi_index_codes,
    scope_reconciliation_rows,
)
from build_margin_maintenance_history import merge_twse_listing_date


class MarketCapTests(unittest.TestCase):
    def test_twse_weekly_retries_a_non_zip_response(self) -> None:
        response = MagicMock(content=b"temporary error page")
        archive = MagicMock()
        archive.__enter__.return_value = archive
        archive.namelist.return_value = ["weekly.xls"]
        archive.read.return_value = b"xls"
        with (
            patch(
                "fetch_market_cap_history.requests.get", return_value=response
            ) as request,
            patch(
                "fetch_market_cap_history.zipfile.ZipFile",
                side_effect=[zipfile.BadZipFile(), archive],
            ),
            patch(
                "fetch_market_cap_history.pd.read_excel",
                return_value=pd.DataFrame([["2026/08/19", "1,234"]]),
            ),
            patch("fetch_market_cap_history.time.sleep") as sleep,
        ):
            result = official_twse_weekly()

        self.assertEqual(result, {"2026-08-19": 1234.0})
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_listing_history_merge_preserves_actual_market_start(self) -> None:
        dates = {"6024": "2017-10-16", "6873": "2024-09-26"}
        merge_twse_listing_date(
            dates, "6024", "2017-10-24", note="櫃轉市"
        )
        merge_twse_listing_date(
            dates, "6873", "2023-03-06", note="創新板"
        )
        merge_twse_listing_date(
            dates, "6423", "2024-05-15", note="創新板"
        )
        self.assertEqual(dates["6024"], "2017-10-16")
        self.assertEqual(dates["6873"], "2023-03-06")
        self.assertEqual(dates["6423"], "2024-05-15")

    def test_stock_code_keeps_four_digit_common_shares_only(self) -> None:
        self.assertTrue(is_stock_code("2330"))
        self.assertFalse(is_stock_code("2881A"))
        self.assertFalse(is_stock_code("0050"))
        self.assertFalse(is_stock_code("9105"))
        self.assertFalse(is_stock_code("00679B"))

    def test_exact_day_market_snapshot_overrides_later_board_date(self) -> None:
        self.assertTrue(
            is_twse_for_day(
                "2237",
                "2026-08-18",
                {"2237": "twse"},
                {"2237": "2026-08-20"},
                {},
                exact_market_snapshot=True,
            )
        )
        self.assertFalse(
            is_twse_for_day(
                "2237",
                "2026-08-18",
                {"2237": "twse"},
                {"2237": "2026-08-20"},
                {},
                exact_market_snapshot=False,
            )
        )

    def test_finmind_twse_market_value_converts_to_100m_twd(self) -> None:
        rows = [
            {"date": "2026-08-18", "stock_id": f"{1000 + index}", "market_value": 100_000_000}
            for index in range(1_001)
        ]
        market_map = {
            row["stock_id"]: "twse" if index < 800 else "tpex"
            for index, row in enumerate(rows)
        }
        amount, matched, total, ordinary_amount = parse_finmind_twse_rows(
            rows, "2026-08-18", market_map
        )
        self.assertEqual(
            (amount, matched, total, ordinary_amount),
            (800.0, 800, 1_001, 1_001.0),
        )

    def test_tpex_market_value_million_twd_converts_to_100m(self) -> None:
        payload = {
            "date": "20260818",
            "tables": [
                {
                    "fields": ["排名", "股票代號", "股票名稱", "發行股數", "收盤價", "市值(佰萬元)"],
                    "data": [
                        [str(index), f"{2000 + index}", "X", "1", "1", "1,000"]
                        for index in range(1, 501)
                    ],
                }
            ],
        }
        amount, count = parse_tpex_payload(payload, "2026-08-18")
        self.assertEqual((amount, count), (5000.0, 500))

    def test_makeup_day_uses_reference_share_count_and_new_close(self) -> None:
        rows = [
            {
                "date": "2017-09-29",
                "stock_id": f"{1000 + index}",
                "market_value": 100_000_000,
            }
            for index in range(1_001)
        ]
        market_map = {str(1000 + index): "twse" for index in range(1_001)}
        reference_prices = {str(1000 + index): 10.0 for index in range(1_001)}
        expected_prices = {str(1000 + index): 11.0 for index in range(1_001)}
        amount, matched, total, adjusted, ordinary_amount = (
            derive_twse_from_reference_rows(
                rows,
                "2017-09-29",
                "2017-09-30",
                market_map,
                reference_prices,
                expected_prices,
            )
        )
        self.assertEqual(
            (amount, matched, total, adjusted, ordinary_amount),
            (1101.1, 1_001, 1_001, 1_001, 1101.1),
        )

    def test_scope_reconciliation_combines_twse_and_official_tpex(self) -> None:
        rows = scope_reconciliation_rows(
            {
                "2026-08-18": {
                    "market_cap": 100.0,
                    "all_ordinary_market_cap": 130.0,
                }
            },
            {"2026-08-18": {"market_cap": 30.13}},
        )
        self.assertEqual(
            rows,
            [
                {
                    "date": "2026-08-18",
                    "twse_plus_tpex": 130.13,
                    "finmind_all_ordinary": 130.0,
                    "difference_pct": 0.1,
                }
            ],
        )

    def test_twse_mi_index_defines_the_historical_official_stock_scope(self) -> None:
        payload = {
            "stat": "OK",
            "date": "20260818",
            "tables": [
                {
                    "title": "每日收盤行情(全部)",
                    "fields": ["證券代號", "證券名稱"],
                    "data": [[f"{1000 + index}", "X"] for index in range(800)]
                    + [["0050", "ETF"], ["9105", "TDR"], ["2881A", "特別股"]],
                }
            ],
        }
        codes = parse_twse_mi_index_codes(payload, "2026-08-18")
        self.assertEqual(len(codes), 800)
        self.assertIn("1000", codes)
        self.assertNotIn("0050", codes)

    def test_official_scope_samples_last_week_of_each_month(self) -> None:
        self.assertEqual(
            last_day_per_month(
                ["2026-01-02", "2026-01-09", "2026-02-06", "2026-02-13"]
            ),
            ["2026-01-09", "2026-02-13"],
        )


if __name__ == "__main__":
    unittest.main()
