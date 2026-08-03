#!/usr/bin/env python3
"""Validate processed history, HTML synchronization, and historical invariance."""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--html", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--through")
    parser.add_argument("--expect-baseline", action="store_true")
    return parser.parse_args()


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_history(payload: dict[str, object]) -> None:
    metadata = payload.get("metadata")
    markets = payload.get("markets")
    if not isinstance(metadata, dict) or not isinstance(markets, dict):
        raise AssertionError("history 缺少 metadata 或 markets")
    rows_by_market: dict[str, list[dict[str, object]]] = {}
    for market in ("twse", "tpex"):
        rows = markets.get(market)
        if not isinstance(rows, list) or not rows:
            raise AssertionError(f"{market} 沒有資料")
        dates: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                raise AssertionError(f"{market} 列格式錯誤")
            for key in (
                "date",
                "maintenance",
                "index",
                "financed_balance",
                "stock_count",
            ):
                if key not in row:
                    raise AssertionError(f"{market} 缺少 {key}")
            dates.append(str(row["date"]))
            for key in ("maintenance", "index"):
                if not math.isfinite(float(row[key])):
                    raise AssertionError(f"{market} {row['date']} {key} 非有限值")
            if int(row["financed_balance"]) <= 0 or int(row["stock_count"]) <= 0:
                raise AssertionError(f"{market} {row['date']} 餘額或股票數不合理")
        if dates != sorted(set(dates)):
            raise AssertionError(f"{market} 日期未嚴格遞增或重複")
        rows_by_market[market] = rows
    twse_dates = [row["date"] for row in rows_by_market["twse"]]
    tpex_dates = [row["date"] for row in rows_by_market["tpex"]]
    if twse_dates != tpex_dates:
        raise AssertionError("TWSE 與 TPEx 日期不一致")
    if metadata.get("end") != twse_dates[-1]:
        raise AssertionError("metadata.end 與最新共同日期不一致")


def compare_rows(
    candidate: dict[str, object], reference: dict[str, object], through: str | None
) -> None:
    for market in ("twse", "tpex"):
        expected = reference["markets"][market]
        if through:
            expected = [row for row in expected if row["date"] <= through]
        actual_map = {row["date"]: row for row in candidate["markets"][market]}
        for index, expected_row in enumerate(expected):
            actual_row = actual_map.get(expected_row["date"])
            if actual_row != expected_row:
                raise AssertionError(
                    f"{market} 歷史差異，第 {index + 1} 列 {expected_row['date']}："
                    f"expected={expected_row}, actual={actual_row}"
                )


def one_decimal(value: float) -> str:
    rounded = Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{rounded:.1f}%"


def format_index(market: str, value: float) -> str:
    if market == "twse":
        rounded = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return f"{rounded:,.0f}"
    rounded = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rounded:.2f}"


def validate_html(payload: dict[str, object], path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    match = re.search(r"  const data = (\[.*?\]);\n  const NS =", source, re.DOTALL)
    if not match:
        raise AssertionError("HTML 找不到嵌入歷史資料")
    embedded = json.loads(html.unescape(match.group(1)))
    twse = {row["date"]: row for row in payload["markets"]["twse"]}
    tpex = {row["date"]: row for row in payload["markets"]["tpex"]}
    common = sorted(set(twse) & set(tpex))
    expected = [
        {
            "d": day,
            "tw": twse[day]["maintenance"],
            "ti": twse[day]["index"],
            "ot": tpex[day]["maintenance"],
            "oi": tpex[day]["index"],
        }
        for day in common
    ]
    if embedded != expected:
        raise AssertionError("HTML 嵌入歷史與 processed JSON 不一致")
    latest = expected[-1]
    required = [
        latest["d"].replace("-", "/"),
        one_decimal(latest["tw"]),
        one_decimal(latest["ot"]),
        format_index("twse", latest["ti"]),
        format_index("tpex", latest["oi"]),
    ]
    for value in required:
        if html.escape(value) not in source and value not in source:
            raise AssertionError(f"HTML 摘要或日期缺少最新值：{value}")


def expect_baseline(payload: dict[str, object]) -> None:
    expected = {
        "twse": {"maintenance": 132.49, "index": 39933.30},
        "tpex": {"maintenance": 118.34, "index": 326.23},
    }
    for market, values in expected.items():
        row = next(
            (
                candidate
                for candidate in payload["markets"][market]
                if candidate["date"] == "2026-07-30"
            ),
            None,
        )
        if row is None:
            raise AssertionError(f"{market} 缺少 2026-07-30 固定回歸基準")
        for key, value in values.items():
            if float(row[key]) != value:
                raise AssertionError(
                    f"{market} 基準 {key} 應為 {value}，實際 {row[key]}"
                )


def main() -> None:
    args = parse_args()
    payload = load(args.history)
    validate_history(payload)
    if args.reference:
        compare_rows(payload, load(args.reference), args.through)
    if args.html:
        validate_html(payload, args.html)
    if args.expect_baseline:
        expect_baseline(payload)
    print(
        f"驗證通過：{len(payload['markets']['twse'])} 個共同日期，"
        f"最新 {payload['metadata']['end']}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"驗證失敗：{exc}", file=sys.stderr)
        raise
