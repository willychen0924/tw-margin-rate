from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fetch_margin_money_history import parse_tpex_payload, parse_twse_rows


class MarginMoneyTests(unittest.TestCase):
    def test_finmind_margin_purchase_money_converts_to_100m_twd(self) -> None:
        rows = [
            {
                "date": "2026-08-18",
                "name": "MarginPurchaseMoney",
                "TodayBalance": 547_011_379_000,
            },
            {"date": "2026-08-18", "name": "MarginPurchase", "TodayBalance": 1},
        ]
        self.assertEqual(parse_twse_rows(rows), {"2026-08-18": 5470.11})

    def test_tpex_thousand_twd_summary_converts_to_100m_twd(self) -> None:
        payload = {
            "date": "20260818",
            "tables": [
                {
                    "summary": [
                        ["", "合計(張)", "1", "2", "3", "4", "5"],
                        [
                            "",
                            "融資金(仟元)",
                            "182,824,981",
                            "9,880,272",
                            "10,147,501",
                            "94,317",
                            "182,463,435",
                        ],
                    ]
                }
            ],
        }
        self.assertEqual(parse_tpex_payload(payload, "2026-08-18"), 1824.63)



if __name__ == "__main__":
    unittest.main()
