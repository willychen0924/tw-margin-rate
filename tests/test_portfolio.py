from __future__ import annotations

import copy
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tw_margin_rate.calculation import updated_average_cost
from tw_margin_rate.corporate_actions import SplitCursor, StockSplit, load_stock_splits
from tw_margin_rate.portfolio import update_portfolio


def row(code="2330", close=60, yesterday=100, buys=0, sells=0, repay=0, today=100):
    return {
        "stock_id": code, "close": close,
        "MarginPurchaseYesterdayBalance": yesterday,
        "MarginPurchaseBuy": buys, "MarginPurchaseSell": sells,
        "MarginPurchaseCashRepayment": repay, "MarginPurchaseTodayBalance": today,
    }


class PortfolioTests(unittest.TestCase):
    def test_vectorized_cost_matches_hand_checkable_scalar_formula(self):
        for yesterday, buys, sells, repay, today in (
            (100, 20, 10, 0, 110), (100, 0, 20, 10, 70), (10, 3, 10, 5, 3),
        ):
            with self.subTest(flows=(yesterday, buys, sells, repay, today)):
                cost, prices = {"2330": 50.0}, {}
                out = update_portfolio(pd.DataFrame([
                    row(yesterday=yesterday, buys=buys, sells=sells, repay=repay, today=today)
                ]), cost, prices)
                expected = updated_average_cost(
                    old_cost=50, close=60, yesterday=yesterday, buys=buys,
                    sells=sells, cash_repayments=repay, today=today,
                )
                self.assertEqual(cost["2330"], expected)
                self.assertEqual(out.iloc[0].estimated_debt, expected * today * .6)
                self.assertEqual(out.iloc[0].market_value, 60 * today)

    def test_invalid_prices_carry_same_stock_only(self):
        for invalid in (0, -1, math.nan, math.inf, -math.inf, None):
            with self.subTest(close=invalid):
                cost, prices = {"2330": 50.0, "2317": 75.0}, {"2330": 60.0, "2317": 90.0}
                out = update_portfolio(pd.DataFrame([
                    row(close=invalid), row("2317", close=invalid), row("1234", close=invalid),
                ]), cost, prices)
                self.assertEqual(list(out.stock_id), ["2330", "2317"])
                self.assertEqual(list(out.close), [60.0, 90.0])
                self.assertNotIn("1234", cost)
                self.assertNotIn("1234", prices)

    def test_invalid_carried_price_is_never_reused(self):
        for invalid in (0, -1, math.nan, math.inf):
            cost, prices = {}, {"2330": invalid}
            self.assertTrue(update_portfolio(pd.DataFrame([row(close=0)]), cost, prices).empty)
            self.assertNotIn("2330", cost)

    def test_zero_price_with_buy_uses_carried_price_in_cost(self):
        cost, prices = {"2330": 50.0}, {"2330": 60.0}
        out = update_portfolio(pd.DataFrame([row(close=0, buys=10, today=110)]), cost, prices)
        self.assertEqual(cost["2330"], (100 * 50 + 10 * 60) / 110)
        self.assertEqual(out.iloc[0].close, 60)

    def test_missing_price_liquidation_still_clears_cost(self):
        cost, prices = {"2330": 999.0}, {}
        out = update_portfolio(pd.DataFrame([row(close=math.nan, sells=100, today=0)]), cost, prices)
        self.assertTrue(out.empty)
        self.assertNotIn("2330", cost)
        update_portfolio(pd.DataFrame([row(yesterday=0, buys=5, today=5)]), cost, prices)
        self.assertEqual(cost["2330"], 60)

    def test_first_valid_price_and_reentry_initialize_at_close(self):
        cost, prices = {}, {}
        update_portfolio(pd.DataFrame([row(close=0)]), cost, prices)
        update_portfolio(pd.DataFrame([row()]), cost, prices)
        self.assertEqual(cost["2330"], 60)
        update_portfolio(pd.DataFrame([row(sells=100, today=0)]), cost, prices)
        self.assertNotIn("2330", cost)
        update_portfolio(pd.DataFrame([row(close=30, yesterday=0, buys=5, today=5)]), cost, prices)
        self.assertEqual(cost["2330"], 30)

    def test_input_frame_is_not_modified(self):
        frame = pd.DataFrame([row(close=0)])
        original = frame.copy(deep=True)
        update_portfolio(frame, {}, {"2330": 60.0})
        pd.testing.assert_frame_equal(frame, original)

    def test_no_automatic_split_from_balance_or_price_jump(self):
        cost, prices = {"2330": 50.0}, {"2330": 60.0}
        SplitCursor(()).advance("2026-09-07", cost, prices)
        update_portfolio(pd.DataFrame([row(close=6, yesterday=1000, today=1000)]), cost, prices)
        self.assertEqual(cost["2330"], 50.0)


class StockSplitTests(unittest.TestCase):
    def test_split_preserves_debt_and_value_without_flows(self):
        cost, prices = {"2330": 50.0, "2317": 90.0}, {"2330": 60.0, "2317": 100.0}
        cursor = SplitCursor((StockSplit("2330", "2026-09-07", 2),))
        cursor.advance("2026-09-04", cost, prices)
        self.assertEqual(cost["2330"], 50.0)
        cursor.advance("2026-09-07", cost, prices)
        out = update_portfolio(pd.DataFrame([row(close=30, yesterday=200, today=200)]), cost, prices)
        self.assertEqual(out.iloc[0].estimated_debt, 50 * 100 * .6)
        self.assertEqual(out.iloc[0].market_value, 60 * 100)
        self.assertEqual(cost["2317"], 90.0)
        self.assertEqual(prices["2317"], 100.0)
        cursor.advance("2026-09-08", cost, prices)
        self.assertEqual(cost["2330"], 25.0)

    def test_6949_actual_event_uses_official_factor_not_balance_ratio(self):
        cost, prices = {"6949": 725.4061505131}, {"6949": 1490.0}
        cursor = SplitCursor((StockSplit("6949", "2026-09-07", 20),))
        cursor.advance("2026-09-07", cost, prices)
        self.assertEqual(prices["6949"], 74.5)
        frame = pd.DataFrame([row("6949", close=67.1, yesterday=106072, buys=1043, sells=2032, today=105083)])
        out = update_portfolio(frame, cost, prices)
        self.assertAlmostEqual(cost["6949"], 36.5763072520688, places=10)
        self.assertAlmostEqual(out.iloc[0].estimated_debt / 100000, 23.0612885698, places=8)
        self.assertEqual(out.iloc[0].MarginPurchaseYesterdayBalance, 106072)
        self.assertEqual(out.iloc[0].MarginPurchaseTodayBalance, 105083)

    def test_split_before_buys_sales_and_repayments(self):
        cost, prices = {"2330": 50.0}, {"2330": 60.0}
        SplitCursor((StockSplit("2330", "2026-09-07", 2),)).advance("2026-09-07", cost, prices)
        update_portfolio(pd.DataFrame([row(close=35, yesterday=200, buys=20, sells=10, repay=5, today=205)]), cost, prices)
        self.assertEqual(cost["2330"], (185 * 25 + 20 * 35) / 205)

    def test_missing_event_day_or_stock_row_converts_carried_price_once(self):
        for days in (("2026-09-04", "2026-09-08"), ("2026-09-04", "2026-09-07", "2026-09-08")):
            cost, prices = {"2330": 50.0}, {"2330": 60.0}
            cursor = SplitCursor((StockSplit("2330", "2026-09-07", 2),))
            for day in days:
                cursor.advance(day, cost, prices)
            out = update_portfolio(pd.DataFrame([row(close=0, yesterday=200, today=200)]), cost, prices)
            self.assertEqual(out.iloc[0].close, 30.0)
            self.assertEqual(cost["2330"], 25.0)

    def test_new_position_after_split_is_not_divided(self):
        cost, prices = {}, {}
        cursor = SplitCursor((StockSplit("2330", "2026-09-07", 2),))
        cursor.advance("2026-09-07", cost, prices)
        cursor.advance("2026-09-08", cost, prices)
        update_portfolio(pd.DataFrame([row(close=30, yesterday=0, buys=5, today=5)]), cost, prices)
        self.assertEqual(cost["2330"], 30.0)

    def test_multiple_splits_accumulate_in_effective_date_order(self):
        cost, prices = {"2330": 100.0}, {"2330": 120.0}
        cursor = SplitCursor((StockSplit("2330", "2026-09-07", 2), StockSplit("2330", "2026-03-09", 5)))
        cursor.advance("2026-09-08", cost, prices)
        self.assertEqual(cost["2330"], 10.0)
        self.assertEqual(prices["2330"], 12.0)

    def test_repeated_or_backward_dates_are_rejected(self):
        cursor = SplitCursor(())
        cursor.advance("2026-09-07", {}, {})
        for day in ("2026-09-07", "2026-09-04"):
            with self.assertRaisesRegex(ValueError, "遞增"):
                cursor.advance(day, {}, {})

    def test_invalid_split_factors_dates_and_universe_are_rejected(self):
        for factor in (0, -1, .5, 1, True, math.nan, math.inf, "2", None):
            with self.subTest(factor=factor), self.assertRaises(ValueError):
                StockSplit("2330", "2026-09-07", factor)
        for code in ("0050", "9105", "12345", 2330):
            with self.assertRaises(ValueError):
                StockSplit(code, "2026-09-07", 2)
        with self.assertRaises(ValueError):
            StockSplit("2330", "2026-02-30", 2)

    def test_checked_in_registry_has_only_six_verified_events(self):
        events = load_stock_splits(ROOT / "data/reference/verified-stock-splits.json")
        self.assertEqual([(e.stock_id, e.effective_date, e.new_shares_per_old_share) for e in events], [
            ("8932", "2026-03-09", 2), ("8937", "2026-04-13", 4),
            ("3086", "2026-04-20", 10), ("5904", "2026-08-10", 10),
            ("4747", "2026-08-31", 2), ("6949", "2026-09-07", 20),
        ])

    def test_registry_rejects_duplicate_unsourced_and_other_actions(self):
        payload = json.loads((ROOT / "data/reference/verified-stock-splits.json").read_text())
        variants = []
        duplicate = copy.deepcopy(payload)
        duplicate["events"].append(duplicate["events"][0])
        variants.append(duplicate)
        unsourced = copy.deepcopy(payload)
        unsourced["events"][0]["sources"] = []
        variants.append(unsourced)
        other = copy.deepcopy(payload)
        other["events"][0]["type"] = "cash_capital_reduction"
        variants.append(other)
        wrong_par = copy.deepcopy(payload)
        wrong_par["events"][0]["new_par_value"] = 1
        variants.append(wrong_par)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.json"
            for variant in variants:
                path.write_text(json.dumps(variant), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_stock_splits(path)


if __name__ == "__main__":
    unittest.main()
