from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_margin_maintenance_history import (
    MARGIN_COLUMNS,
    PRICE_COLUMNS,
    load_cache,
    load_cache_day,
)


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


if __name__ == "__main__":
    unittest.main()
