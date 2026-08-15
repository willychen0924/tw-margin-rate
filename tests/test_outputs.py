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

    def test_baseline_has_expected_row_count(self) -> None:
        self.assertGreaterEqual(len(self.payload["markets"]["twse"]), 2207)
        self.assertEqual(
            len(self.payload["markets"]["twse"]),
            len(self.payload["markets"]["tpex"]),
        )


if __name__ == "__main__":
    unittest.main()
