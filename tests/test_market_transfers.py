import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]
from tw_margin_rate.market_transfers import load_market_transfers, transfer_market, REFERENCE
from tw_margin_rate.calculation import market_for_stock
from build_margin_maintenance_history import load_market_reference
from fetch_market_cap_history import load_market_snapshots, is_twse_for_day


class MarketTransferTests(unittest.TestCase):
    def test_official_event_and_historical_interval(self):
        transfers = load_market_transfers()
        self.assertEqual(transfers, {"8908": "2026-10-16"})
        for day, expected in [("2017-07-03", "tpex"), ("2026-10-15", "tpex"), ("2026-10-16", "twse")]:
            self.assertEqual(transfer_market("8908", day, transfers), expected)
            self.assertEqual(market_for_stock("8908", day, {"8908": "twse"}, transfers, {}), expected)
        self.assertIsNone(transfer_market("2330", "2026-10-16", transfers))

    def test_stale_snapshot_is_corrected_without_mutating_other_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "raw/stock_info/2026"
            folder.mkdir(parents=True)
            rows = [{"stock_id": str(1000+i), "type": "twse", "date": "2026-10-16"} for i in range(2000)]
            rows.append({"stock_id": "8908", "type": "tpex", "date": "2026-10-15"})
            pd.DataFrame(rows).to_parquet(folder / "2026-10-16.parquet")
            days = ["2026-10-15", "2026-10-16"]
            maps, exact = load_market_snapshots(root, days)
            self.assertEqual(maps[days[0]]["8908"], "tpex")
            self.assertEqual(maps[days[1]]["8908"], "twse")
            self.assertTrue(is_twse_for_day("8908", days[1], maps[days[1]], {}, {}, exact_market_snapshot=days[1] in exact))
            self.assertEqual(pd.read_parquet(folder / "2026-10-16.parquet").iloc[-1]["type"], "tpex")

    def test_reference_loader_adds_only_reviewed_listing_date(self):
        frame = pd.DataFrame([{"stock_id": "8908", "type": m, "date": "2026-10-17"} for m in ("twse", "tpex")])
        with patch("build_margin_maintenance_history.latest_complete_stock_info", return_value=Path("unused")), patch("build_margin_maintenance_history.pd.read_parquet", return_value=frame):
            current, listings, delistings = load_market_reference(
                Path("unused"), ROOT / "data/reference/twse-company-info.latest.json",
                ROOT / "data/reference/twse-delisted.latest.html", ROOT / "data/reference/twse-newlisting.latest.json")
        self.assertEqual(current["8908"], "twse")
        self.assertEqual(listings["8908"], "2026-10-16")
        self.assertNotIn("8908", delistings)

    def test_invalid_reference_is_rejected(self):
        original = json.loads(REFERENCE.read_text())
        for change in ({"sources": []}, {"to_market": "tpex"}, {"effective_date": "invalid"}, {"effective_date": "2026-10-15"}):
            payload = json.loads(json.dumps(original));payload["events"][0].update(change)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "events.json";path.write_text(json.dumps(payload))
                with self.assertRaises(ValueError): load_market_transfers(path)
