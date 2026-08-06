from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tw_margin_rate.publishing import assert_official_origin


class PublishingTests(unittest.TestCase):
    def test_accepts_exact_https_origin(self) -> None:
        assert_official_origin("https://github.com/willychen0924/tw-margin-rate.git")

    def test_accepts_exact_ssh_origin(self) -> None:
        assert_official_origin("git@github.com:willychen0924/tw-margin-rate.git")

    def test_rejects_lookalike_origin(self) -> None:
        with self.assertRaises(RuntimeError):
            assert_official_origin(
                "git@github.com:someone/willychen0924/tw-margin-rate.git"
            )


if __name__ == "__main__":
    unittest.main()
