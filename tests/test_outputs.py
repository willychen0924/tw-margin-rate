from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_margin_outputs import expect_baseline, validate_history, validate_html
from update_margin_maintenance_chart_data import format_delta, format_index, format_ratio


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

    def test_summary_delta_formatting_uses_taiwan_market_colors(self) -> None:
        self.assertEqual(format_delta(4.04, "pt"), ("up", "▲ 4.0"))
        self.assertEqual(format_delta(-3.48, "pt"), ("down", "▼ 3.5"))
        self.assertEqual(format_delta(-30.91, "億"), ("down", "▼ 30.9億"))
        self.assertEqual(format_delta(0, "億"), ("flat", "— 0.0億"))

    def test_tpex_index_displays_one_decimal(self) -> None:
        self.assertEqual(format_index("twse", 45308.68), "45,309")
        self.assertEqual(format_index("tpex", 390.83), "390.8")
        source = self.html_path.read_text(encoding="utf-8")
        latest_tpex = self.payload["markets"]["tpex"][-1]
        expected = format_index("tpex", latest_tpex["index"])
        self.assertEqual(source.count(f"&gt;{expected}&lt;"), 2)
        self.assertIn("Number(value).toFixed(1)", source)

    def test_compact_header_and_reference_stat_layout_are_present(self) -> None:
        source = self.html_path.read_text(encoding="utf-8")
        self.assertIn("<title>台股上市櫃融資維持率</title>", source)
        self.assertNotIn("&lt;h1&gt;台股上市櫃融資維持率&lt;/h1&gt;", source)
        self.assertIn("台股融資維持率 · 上市 / 櫃買", source)
        display_day = self.payload["metadata"]["end"].replace("-", "/")
        self.assertIn(f"更新時間 {display_day}", source)
        self.assertNotIn("· 每日更新", source)
        self.assertEqual(source.count('class=&quot;mmc-market-chip&quot;'), 2)
        self.assertEqual(source.count('data-role=&quot;stat-maint-value&quot;'), 2)
        self.assertEqual(source.count('class=&quot;mmc-stat-percent&quot;'), 2)
        self.assertIn(".mmc-stat-listed::before { background: var(--mmc-blue); }", source)
        self.assertIn(".mmc-stat-tpex::before { background: #c99a4e; }", source)
        self.assertIn(".mmc-stat-percent { font-size: 26px; font-weight: 700; }", source)
        self.assertIn("--mmc-blue: #4a7fd0;", source)
        self.assertIn("--mmc-orange: #e0783c;", source)
        self.assertIn("--mmc-green: #4e9e72;", source)
        self.assertIn("--mmc-purple: #8a6fc0;", source)
        self.assertIn("--mmc-page: #f5f1e8;", source)
        self.assertIn("--mmc-surface: #ffffff;", source)
        self.assertIn("--mmc-ink: #2f2b25;", source)
        self.assertIn("--mmc-ink-2: #6b655c;", source)
        self.assertIn("--mmc-muted: #a69f93;", source)
        self.assertIn("--mmc-tint: #ece6db;", source)
        self.assertIn("--mmc-active: #2a2622;", source)
        self.assertIn(".mmc-line-maint { stroke: var(--mmc-blue); stroke-width: 2.4; }", source)
        self.assertIn(".mmc-line-index { stroke: var(--mmc-orange); opacity: .7; }", source)
        self.assertIn(".mmc-line-balance { stroke: var(--mmc-green); opacity: .7; }", source)
        self.assertIn(".mmc-line-ratio { stroke: var(--mmc-purple); opacity: .7; }", source)
        self.assertIn("@media (min-width: 701px)", source)
        self.assertIn(".mmc-line { stroke-width: 1.4; }", source)
        self.assertIn(".mmc-line-maint { stroke-width: 2.5; }", source)
        self.assertIn(".mmc-dot { r: 3.2px; }", source)
        self.assertEqual(
            source.count('class=&quot;mmc-dot mmc-dot-maint&quot; r=&quot;5&quot;'),
            2,
        )
        self.assertIn(
            '[data-tone=&quot;up&quot;] { color: var(--mmc-up); background: rgba(199,74,63,.14); }',
            source,
        )
        self.assertIn(
            '[data-tone=&quot;down&quot;] { color: var(--mmc-down); background: rgba(26,158,95,.14); }',
            source,
        )
        self.assertIn("--mmc-down: #1a9e5f;", source)
        self.assertEqual(source.count('data-role=&quot;stat-balance-value&quot;'), 2)
        self.assertEqual(source.count('class=&quot;mmc-balance-total&quot;'), 2)
        self.assertEqual(source.count('data-role=&quot;maint-delta&quot;'), 2)
        self.assertEqual(source.count('data-role=&quot;balance-delta&quot;'), 2)
        for market in ("twse", "tpex"):
            rows = self.payload["markets"][market]
            latest = rows[-1]
            previous = rows[-2] if len(rows) > 1 else latest
            maintenance_delta = format_delta(
                latest["maintenance"] - previous["maintenance"], "pt"
            )[1]
            balance_delta = format_delta(
                latest["financed_amount"] - previous["financed_amount"], "億"
            )[1]
            self.assertIn(maintenance_delta, source)
            self.assertNotIn(f"{maintenance_delta} pt", source)
            self.assertIn(balance_delta, source)
        self.assertIn(".mmc-balance-total { white-space: nowrap; }", source)
        self.assertIn(
            "flex: 0 0 auto; padding: 2px 7px; font-size: 12px; "
            "line-height: 1.2; white-space: nowrap;",
            source,
        )
        self.assertNotIn("💡 B 版：", source)
        self.assertLess(source.index("mmc-stat-row"), source.index("mmc-toolbar"))
        self.assertLess(source.index("mmc-toolbar"), source.index("mmc-market-section"))

    def test_contextual_calendar_axis_is_present(self) -> None:
        source = self.html_path.read_text(encoding="utf-8")
        self.assertIn("const fixedMonthTicks =", source)
        self.assertIn("const fixedYearTicks =", source)
        self.assertIn("fixedMonthTicks(rows, [0, 3, 6, 9])", source)
        self.assertIn("fixedMonthTicks(rows, [0, 6])", source)
        self.assertIn("const yearGuideIndexes =", source)
        self.assertIn("mmc-year-line", source)
        self.assertNotIn("rows[idx].d.slice(5)", source)

    def test_mobile_axes_and_long_press_touch_interaction(self) -> None:
        source = self.html_path.read_text(encoding="utf-8")
        self.assertIn("--mmc-grid: #ded6c8;", source)
        self.assertIn(".mmc-axis-label { fill: var(--mmc-muted); font-size: 9px;", source)
        self.assertIn(".mmc-axis-title { fill: var(--mmc-muted); font-size: 9px; font-weight: 400; letter-spacing: .18em; }", source)
        self.assertIn(".mmc-axis-title { display: none; }", source)
        self.assertIn(".mmc-axis-label { font-size: 18px; font-weight: 500; }", source)
        self.assertIn(".mmc-x-axis-label { font-size: 18px; }", source)
        self.assertIn("const fmtAxisIndex = (market, value) =&gt;", source)
        self.assertIn("if (!isNarrow() || Math.abs(value) &lt; 1000)", source)
        self.assertIn("`${Number((value / 1000).toFixed(1))}k`", source)
        self.assertIn("touch-action: pan-y; user-select: none;", source)
        self.assertIn("-webkit-touch-callout: none;", source)
        self.assertIn("chart.hit.addEventListener(&#x27;pointerdown&#x27;", source)
        self.assertIn("try { chart.hit.setPointerCapture?.(event.pointerId); } catch {}", source)
        self.assertIn("}, 320);", source)
        self.assertIn("event.preventDefault();", source)
        self.assertIn("chart.hit.addEventListener(&#x27;pointercancel&#x27;, finishTouch);", source)

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
        self.assertEqual(source.count('class=&quot;mmc-legend-long&quot;&gt;融資維持率&lt;/span&gt;'), 2)
        self.assertNotIn("&gt;上市融資維持率 &lt;strong", source)
        self.assertNotIn("&gt;櫃買融資維持率 &lt;strong", source)

    def test_mobile_legend_uses_compact_labels(self) -> None:
        source = self.html_path.read_text(encoding="utf-8")
        self.assertEqual(source.count('class=&quot;mmc-legend-long&quot;'), 8)
        self.assertEqual(source.count('class=&quot;mmc-legend-short&quot;'), 8)
        self.assertEqual(source.count('&gt;維持率&lt;/span&gt;'), 2)
        self.assertEqual(source.count('&gt;指數&lt;/span&gt;'), 2)
        self.assertEqual(source.count('&gt;餘額&lt;/span&gt;'), 2)
        self.assertEqual(source.count('&gt;市值比&lt;/span&gt;'), 2)
        self.assertIn(".mmc-summary .mmc-legend-long { display: none; }", source)
        self.assertIn(".mmc-summary .mmc-legend-short { display: inline; }", source)

    def test_chart_is_fixed_to_linear_and_mobile_stats_stack(self) -> None:
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
        self.assertIn("padding: 2px 12px; background: transparent;", source)
        self.assertIn("font-size: 13px; line-height: 1.1;", source)
        self.assertIn("@media (max-width: 640px)", source)
        self.assertIn(
            ".mmc-stat-row { grid-template-columns: 1fr; }",
            source,
        )
        self.assertNotIn(
            ".mmc-stat-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }",
            source,
        )
        self.assertIn("bounds.bottom = narrow ? 366 : 246", source)
        self.assertIn("chartHeight = narrow ? 420 : 282", source)
        self.assertIn("padding: 20px 20px 8px; overflow: hidden;", source)
        self.assertIn("--mmc-surface-soft: #faf7f0;", source)
        self.assertIn("justify-content: flex-start; flex-wrap: wrap; gap: 12px;", source)
        self.assertIn("margin: -20px -20px 10px; padding: 11px 18px;", source)
        self.assertIn("border-bottom: 1px solid var(--mmc-border); background: var(--mmc-surface-soft);", source)
        self.assertIn("padding: 5px 13px; border-radius: 8px;", source)
        self.assertIn("font-size: 15px; font-weight: 800; letter-spacing: .03em;", source)
        self.assertIn("background: rgba(74,127,208,.14); color: #2c5aa6;", source)
        self.assertIn("background: rgba(201,154,78,.18); color: #8a6320;", source)
        self.assertIn("font-size: 12.5px; font-variant-numeric: tabular-nums;", source)
        self.assertIn("margin: 0; padding: 3px 8px; border: 1px solid transparent; border-radius: 7px;", source)
        self.assertIn("margin-left: 1px; color: var(--mmc-ink); font-weight: 800;", source)
        self.assertIn("display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: start; justify-content: stretch; gap: 0 8px;", source)
        self.assertIn("display: grid; grid-column: 2; grid-template-columns: repeat(2, minmax(0, 1fr));", source)
        self.assertIn("justify-content: stretch; gap: 0 8px; width: 100%;", source)
        self.assertIn("min-width: 0; padding: 1px 2px; line-height: 1.1; white-space: nowrap;", source)
        self.assertIn("@media (max-width: 360px)", source)
        self.assertIn("grid-template-columns: auto minmax(0, 1fr); gap: 0 4px;", source)
        self.assertIn("min-width: 0; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 4px; font-size: 11px;", source)
        self.assertIn(".mmc-swatch { width: 8px; height: 8px; }", source)
        self.assertIn(".mmc-market-section { padding: 16px 10px 10px; }", source)
        self.assertIn("position: fixed; left: 0; top: 0; z-index: 100;", source)
        self.assertIn("chart.tooltip.parentElement !== root", source)
        self.assertIn("root.appendChild(chart.tooltip)", source)
        self.assertIn(
            "const desiredTop = plotRect.top + bounds.bottom / chartHeight * plotRect.height - tooltipLift",
            source,
        )
        self.assertIn("const tooltipLift = isNarrow() ? 16 : 12", source)
        self.assertIn("window.innerHeight - box.height - viewportPadding", source)
        self.assertIn("bounds.left = narrow ? 64 : 39", source)
        self.assertIn("bounds.right = narrow ? 672 : 712", source)
        self.assertIn("chart.hit.setAttribute(&#x27;width&#x27;, bounds.right - bounds.left)", source)
        self.assertIn("const axisTitleY = bounds.bottom + 24", source)
        self.assertIn("const leftAxisX = narrow ? bounds.left - 9 : bounds.left - 25", source)
        self.assertIn("const leftAxisAnchor = narrow ? &#x27;end&#x27; : &#x27;start&#x27;", source)
        self.assertIn("const rightAxisX = bounds.right + 9", source)
        self.assertIn("{ x: leftAxisX, y: axisTitleY, &#x27;text-anchor&#x27;: &#x27;start&#x27;", source)
        self.assertIn("{ x: rightAxisX, y: axisTitleY, &#x27;text-anchor&#x27;: &#x27;start&#x27;", source)
        self.assertNotIn("writing-mode: vertical-rl", source)
        self.assertIn("const geometryObserver = new ResizeObserver", source)
        self.assertNotIn("mmc-stat-dot", source)
        self.assertNotIn("融資維持率走勢 ·", source)
        self.assertIn("display: flex; align-items: baseline; justify-content: space-between", source)

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
        for market in ("twse", "tpex"):
            latest = self.payload["markets"][market][-1]
            self.assertIn(format_ratio(latest["margin_market_cap_ratio"]), source)

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
        for latest in (latest_twse, latest_tpex):
            self.assertGreater(latest["market_cap"], 0)
            expected_ratio = latest["financed_amount"] / latest["market_cap"] * 100
            self.assertAlmostEqual(
                latest["margin_market_cap_ratio"], expected_ratio, delta=0.0001
            )

    def test_baseline_has_expected_row_count(self) -> None:
        self.assertGreaterEqual(len(self.payload["markets"]["twse"]), 2207)
        self.assertEqual(
            len(self.payload["markets"]["twse"]),
            len(self.payload["markets"]["tpex"]),
        )


if __name__ == "__main__":
    unittest.main()
