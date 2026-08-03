from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tw_margin_rate.calculation import (
    is_ordinary_share,
    market_for_stock,
    market_maintenance,
    remaining_financed_balance,
    updated_average_cost,
)


class CalculationTests(unittest.TestCase):
    def test_ordinary_share_filter(self) -> None:
        self.assertTrue(is_ordinary_share("2330"))
        for code in ("0050", "9105", "912398", "ABC1", "123"):
            self.assertFalse(is_ordinary_share(code), code)

    def test_remaining_balance_never_negative(self) -> None:
        self.assertEqual(remaining_financed_balance(10, 8, 5), 0)

    def test_buy_updates_weighted_cost(self) -> None:
        result = updated_average_cost(
            old_cost=50,
            close=70,
            yesterday=100,
            buys=20,
            sells=10,
            cash_repayments=0,
            today=110,
        )
        self.assertAlmostEqual(result or 0, (90 * 50 + 20 * 70) / 110)

    def test_sell_and_repayment_keep_old_unit_cost(self) -> None:
        result = updated_average_cost(
            old_cost=50,
            close=40,
            yesterday=100,
            buys=0,
            sells=20,
            cash_repayments=10,
            today=70,
        )
        self.assertEqual(result, 50)

    def test_first_position_initializes_at_close(self) -> None:
        result = updated_average_cost(
            old_cost=None,
            close=60,
            yesterday=0,
            buys=10,
            sells=0,
            cash_repayments=0,
            today=10,
        )
        self.assertEqual(result, 60)

    def test_zero_balance_clears_state(self) -> None:
        result = updated_average_cost(
            old_cost=50,
            close=45,
            yesterday=10,
            buys=0,
            sells=10,
            cash_repayments=0,
            today=0,
        )
        self.assertIsNone(result)

    def test_market_formula_is_hand_checkable(self) -> None:
        result = market_maintenance([(60, 10, 50), (30, 20, 25)])
        expected = (60 * 10 + 30 * 20) / (50 * 10 * 0.6 + 25 * 20 * 0.6) * 100
        self.assertAlmostEqual(result, expected)
        self.assertTrue(math.isfinite(result))

    def test_market_formula_rejects_zero_debt(self) -> None:
        with self.assertRaises(ValueError):
            market_maintenance([])

    def test_transfer_is_tpex_before_twse_listing(self) -> None:
        market = market_for_stock(
            "1234", "2020-01-01", {"1234": "twse"}, {"1234": "2021-01-01"}, set()
        )
        self.assertEqual(market, "tpex")

    def test_transfer_is_twse_on_listing_date(self) -> None:
        market = market_for_stock(
            "1234", "2021-01-01", {"1234": "twse"}, {"1234": "2021-01-01"}, set()
        )
        self.assertEqual(market, "twse")

    def test_delisted_fallback(self) -> None:
        self.assertEqual(market_for_stock("1234", "2020-01-01", {}, {}, {"1234"}), "twse")
        self.assertEqual(market_for_stock("5678", "2020-01-01", {}, {}, set()), "tpex")


if __name__ == "__main__":
    unittest.main()

