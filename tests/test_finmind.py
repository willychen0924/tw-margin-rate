from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tw_margin_rate.finmind import FinMindClient


class FinMindCacheTests(unittest.TestCase):
    def seed_cache(
        self, client: FinMindClient, dataset: str, rows: list[dict[str, object]]
    ) -> None:
        params = {
            "dataset": dataset,
            "start_date": "2026-08-04",
            "end_date": "2026-08-05",
        }
        path = client._cache_path(dataset, params)
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump({"data": rows}, handle)

    def test_nonempty_cache_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            client = FinMindClient("test-token", Path(temp))
            self.seed_cache(client, "dataset", [{"date": "2026-08-04"}])
            with patch.object(client, "_request") as request:
                rows = client.fetch(
                    "dataset", start_date="2026-08-04", end_date="2026-08-05"
                )
            request.assert_not_called()
            self.assertEqual(rows, [{"date": "2026-08-04"}])

    def test_empty_cache_is_refetched(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            client = FinMindClient("test-token", Path(temp))
            self.seed_cache(client, "dataset", [])
            replacement = [{"date": "2026-08-04"}]
            with patch.object(
                client, "_request", return_value={"status": 200, "data": replacement}
            ) as request:
                rows = client.fetch(
                    "dataset", start_date="2026-08-04", end_date="2026-08-05"
                )
            request.assert_called_once()
            self.assertEqual(rows, replacement)


if __name__ == "__main__":
    unittest.main()
