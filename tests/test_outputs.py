from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_margin_outputs import expect_baseline, validate_history, validate_html


class OutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.history_path = Path(
            os.environ.get(
                "TW_MARGIN_HISTORY_PATH",
                ROOT / "data/processed/margin-maintenance-history.json",
            )
        )
        cls.html_path = Path(
            os.environ.get("TW_MARGIN_HTML_PATH", ROOT / "docs/index.html")
        )
        cls.root_html_path = Path(
            os.environ.get("TW_MARGIN_ROOT_HTML_PATH", ROOT / "index.html")
        )
        cls.payload = json.loads(cls.history_path.read_text(encoding="utf-8"))

    def test_published_baseline_numbers(self) -> None:
        expect_baseline(self.payload)

    def test_history_schema_and_common_dates(self) -> None:
        validate_history(self.payload)

    def test_html_matches_processed_history(self) -> None:
        validate_html(self.payload, self.html_path)
        validate_html(self.payload, self.root_html_path)

    def test_root_html_is_exact_docs_mirror(self) -> None:
        self.assertEqual(self.root_html_path.read_bytes(), self.html_path.read_bytes())

    def test_contextual_calendar_axis_is_present(self) -> None:
        source = self.html_path.read_text(encoding="utf-8")
        self.assertIn("const fixedMonthTicks =", source)
        self.assertIn("const fixedYearTicks =", source)
        self.assertIn("fixedMonthTicks(rows, [0, 3, 6, 9])", source)
        self.assertIn("fixedMonthTicks(rows, [0, 6])", source)
        self.assertIn("const yearGuideIndexes =", source)
        self.assertIn("mmc-year-line", source)
        self.assertNotIn("rows[idx].d.slice(5)", source)

    def test_balance_series_has_clickable_legend_controls(self) -> None:
        source = self.html_path.read_text(encoding="utf-8")
        self.assertEqual(source.count('data-series=&quot;balance&quot;'), 2)
        self.assertEqual(source.count('data-role=&quot;balance-path&quot;'), 2)
        self.assertEqual(source.count('data-role=&quot;balance-dot&quot;'), 2)
        self.assertIn("mmc-line-balance", source)
        self.assertIn("seriesVisibility", source)
        self.assertIn("fmtBalance", source)
        self.assertNotIn("萬張", source)
        self.assertIn("億", source)
        self.assertNotIn("130% 警戒線", source)
        self.assertNotIn("stroke-dasharray: 5 3", source)
        self.assertNotIn("mmc-balance-axis-label", source)
        self.assertNotIn("`餘額 ${fmtBalanceAmount(balanceDomain[1])}`", source)
        self.assertNotIn("mmc-legend-label", source)
        self.assertNotIn("mmc-legend-unit", source)
        self.assertEqual(source.count("mmc-balance-reading"), 3)
        self.assertIn(".mmc-summary .mmc-balance-reading { gap: 1px; }", source)
        self.assertEqual(source.count("融資維持率 &lt;strong"), 2)
        self.assertNotIn("&gt;上市融資維持率 &lt;strong", source)
        self.assertNotIn("&gt;櫃買融資維持率 &lt;strong", source)

    def test_chart_is_fixed_to_linear_and_mobile_stats_stay_side_by_side(self) -> None:
        source = self.html_path.read_text(encoding="utf-8")
        self.assertNotIn("data-scale=", source)
        self.assertIn("let scaleMode = &#x27;linear&#x27;;", source)
        self.assertIn("let range = &#x27;1y&#x27;;", source)
        self.assertIn(
            'class=&quot;btn btn-primary&quot; data-range=&quot;1y&quot; '
            'aria-pressed=&quot;true&quot;',
            source,
        )
        self.assertIn(
            'class=&quot;btn&quot; data-range=&quot;all&quot; '
            'aria-pressed=&quot;false&quot;',
            source,
        )
        self.assertIn('data-range=&quot;3m&quot;', source)
        self.assertIn("range.endsWith(&#x27;m&#x27;)", source)
        self.assertIn(
            "grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px;",
            source,
        )
        self.assertIn("bounds.bottom = narrow ? 366 : 246", source)
        self.assertIn("chartHeight = narrow ? 420 : 300", source)
        self.assertIn("const geometryObserver = new ResizeObserver", source)
        self.assertNotIn("mmc-stat-dot", source)
        self.assertNotIn("融資維持率走勢 ·", source)
        self.assertIn("flex-direction: column; align-items: center", source)

    def test_series_toggles_are_remembered_per_market(self) -> None:
        source = self.html_path.read_text(encoding="utf-8")
        self.assertIn("tw-margin-rate:series-visibility:v1", source)
        self.assertIn("currentSeriesPreferences", source)
        self.assertIn("saveSeriesPreferences();", source)
        self.assertIn("applySeriesPreferences", source)
        self.assertIn("localStorage.setItem(storageKey", source)
        self.assertIn("event.source !== frame.contentWindow", source)
        self.assertIn("frame.addEventListener('load', sendState)", source)

    def test_index_toggle_keeps_right_axis_visible(self) -> None:
        source = self.html_path.read_text(encoding="utf-8")
        self.assertNotIn("if (chart.seriesVisibility.index) {", source)
        self.assertIn("chart.indexPath.style.display = chart.seriesVisibility.index", source)
        self.assertIn("mmc-axis-title-index", source)

    def test_margin_market_cap_ratio_has_per_market_starts_and_is_toggleable(self) -> None:
        source = self.html_path.read_text(encoding="utf-8")
        self.assertEqual(source.count('data-series=&quot;ratio&quot;'), 2)
        self.assertEqual(source.count('data-role=&quot;ratio-path&quot;'), 2)
        self.assertEqual(source.count('data-role=&quot;ratio-dot&quot;'), 2)
        self.assertEqual(source.count('data-role=&quot;ratio-value&quot;'), 2)
        self.assertIn("mmc-line-ratio", source)
        self.assertIn("融資市值比", source)
        self.assertIn("nullablePathFor", source)
        self.assertIn("d[keys.ratio] == null ? Number.NaN", source)
        self.assertIn("0.37%", source)
        self.assertIn("1.70%", source)

        starts = self.payload["metadata"]["market_cap_starts"]
        self.assertEqual(starts["twse"], "2017-07-03")
        self.assertEqual(starts["tpex"], "2017-07-03")
        self.assertIn(
            "preferred shares excluded",
            self.payload["metadata"]["market_cap_scopes"]["twse"],
        )
        for market in ("twse", "tpex"):
            rows = self.payload["markets"][market]
            before = [row for row in rows if row["date"] < starts[market]]
            covered = [row for row in rows if row["date"] >= starts[market]]
            self.assertFalse(before)
            self.assertTrue(covered)
            self.assertTrue(
                all(
                    row["market_cap"] is None
                    and row["margin_market_cap_ratio"] is None
                    for row in before
                )
            )
            self.assertTrue(
                all(
                    row["market_cap"] > 0
                    and 0 < row["margin_market_cap_ratio"] < 100
                    for row in covered
                )
            )

        latest_twse = self.payload["markets"]["twse"][-1]
        latest_tpex = self.payload["markets"]["tpex"][-1]
        self.assertEqual(latest_twse["market_cap"], 1479725.4)
        self.assertEqual(latest_twse["margin_market_cap_ratio"], 0.3697)
        self.assertEqual(latest_tpex["margin_market_cap_ratio"], 1.702)

    def test_baseline_has_expected_row_count(self) -> None:
        self.assertGreaterEqual(len(self.payload["markets"]["twse"]), 2207)
        self.assertEqual(
            len(self.payload["markets"]["twse"]),
            len(self.payload["markets"]["tpex"]),
        )


if __name__ == "__main__":
    unittest.main()
