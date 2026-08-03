#!/usr/bin/env python3
"""Report the first and largest row differences between two warm-up histories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    left = json.loads(args.left.read_text(encoding="utf-8"))
    right = json.loads(args.right.read_text(encoding="utf-8"))
    failed = False
    for market in ("twse", "tpex"):
        left_rows = {row["date"]: row for row in left["markets"][market]}
        right_rows = {row["date"]: row for row in right["markets"][market]}
        common = sorted(set(left_rows) & set(right_rows))
        differences = []
        for day in common:
            a = left_rows[day]
            b = right_rows[day]
            if a != b:
                differences.append(
                    (day, abs(float(a["maintenance"]) - float(b["maintenance"])), a, b)
                )
        if differences:
            failed = True
            largest = max(differences, key=lambda item: item[1])
            print(
                f"{market}: {len(differences)} 個差異；第一個 {differences[0][0]} "
                f"left={differences[0][2]} right={differences[0][3]}"
            )
            print(
                f"{market}: 最大維持率差異 {largest[1]:.2f}pp 於 {largest[0]}"
            )
        else:
            print(f"{market}: {len(common)} 個共同日期完全一致")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()

