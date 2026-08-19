#!/usr/bin/env python3
"""Embed a rebuilt margin-maintenance history in the standalone chart HTML."""

from __future__ import annotations

import argparse
import html
import json
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--html", type=Path, required=True)
    return parser.parse_args()


def one_decimal(value: float) -> str:
    rounded = Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{rounded:.1f}%"


def format_index(market: str, value: float) -> str:
    if market == "twse":
        rounded = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return f"{rounded:,.0f}"
    rounded = Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{rounded:.1f}"


def format_balance(value: int | float) -> str:
    rounded = Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{rounded:.1f}"


def format_stat_balance(value: int | float) -> str:
    rounded = Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{rounded:,.1f}"


def format_ratio(value: int | float) -> str:
    rounded = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rounded:.2f}%"


def format_delta(value: int | float, unit: str) -> tuple[str, str]:
    decimal = Decimal(str(value))
    rounded = abs(decimal).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    suffix = "" if unit == "pt" else unit
    if rounded == 0:
        return "flat", f"— {rounded:.1f}{suffix}"
    tone = "up" if decimal > 0 else "down"
    arrow = "▲" if decimal > 0 else "▼"
    return tone, f"{arrow} {rounded:,.1f}{suffix}"


def replace_exactly_twice(pattern: str, values: list[str], source: str) -> str:
    iterator = iter(values)
    count = 0

    def replacement(match: re.Match[str]) -> str:
        nonlocal count
        try:
            value = next(iterator)
        except StopIteration:
            return match.group(0)
        count += 1
        return f"{match.group(1)}{value}{match.group(3)}"

    updated = re.sub(pattern, replacement, source)
    if count != len(values):
        raise RuntimeError(f"Expected {len(values)} replacements, made {count}")
    return updated


def replace_tone_values(
    class_name: str,
    role: str,
    values: list[tuple[str, str]],
    source: str,
) -> str:
    iterator = iter(values)
    count = 0
    pattern = (
        rf"(&lt;span class=&quot;{class_name}&quot; data-tone=&quot;)"
        rf"(?:up|down|flat)"
        rf"(&quot; data-role=&quot;{role}&quot;&gt;).*?(&lt;/span&gt;)"
    )

    def replacement(match: re.Match[str]) -> str:
        nonlocal count
        try:
            tone, label = next(iterator)
        except StopIteration:
            return match.group(0)
        count += 1
        return f"{match.group(1)}{tone}{match.group(2)}{label}{match.group(3)}"

    updated = re.sub(pattern, replacement, source)
    if count != len(values):
        raise RuntimeError(f"Expected {len(values)} {role} replacements, made {count}")
    return updated


def main() -> None:
    args = parse_args()
    payload = json.loads(args.history.read_text(encoding="utf-8"))
    twse = {row["date"]: row for row in payload["markets"]["twse"]}
    tpex = {row["date"]: row for row in payload["markets"]["tpex"]}
    common_dates = sorted(set(twse) & set(tpex))
    if not common_dates:
        raise RuntimeError("The rebuilt history has no common TWSE/TPEx dates")

    rows = [
        {
            "d": day,
            "tw": twse[day]["maintenance"],
            "ti": twse[day]["index"],
            "tb": twse[day]["financed_amount"],
            "tr": twse[day].get("margin_market_cap_ratio"),
            "ot": tpex[day]["maintenance"],
            "oi": tpex[day]["index"],
            "ob": tpex[day]["financed_amount"],
            "or": tpex[day].get("margin_market_cap_ratio"),
        }
        for day in common_dates
    ]
    latest = rows[-1]
    previous = rows[-2] if len(rows) > 1 else latest
    embedded = html.escape(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")), quote=True
    )

    source = args.html.read_text(encoding="utf-8")
    source, count = re.subn(
        r"(  const data = )\[.*?\](;\n  const NS =)",
        lambda match: f"{match.group(1)}{embedded}{match.group(2)}",
        source,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise RuntimeError(f"Expected one embedded data array, replaced {count}")

    values = [one_decimal(latest["tw"]), one_decimal(latest["ot"])]
    source = replace_exactly_twice(
        r"(&lt;span data-role=&quot;stat-maint-value&quot;&gt;)([^&]*?)(&lt;/span&gt;)",
        [value.removesuffix("%") for value in values],
        source,
    )
    source = replace_exactly_twice(
        r"(&lt;strong data-role=&quot;maint-value&quot;&gt;)([^&]*?)(&lt;/strong&gt;)",
        values,
        source,
    )

    index_values = [
        format_index("twse", latest["ti"]),
        format_index("tpex", latest["oi"]),
    ]
    source = replace_exactly_twice(
        r"(&lt;span class=&quot;mmc-stat-index&quot;&gt;)([^&]*?)(&lt;/span&gt;)",
        index_values,
        source,
    )
    source = replace_exactly_twice(
        r"(&lt;strong data-role=&quot;index-value&quot;&gt;)([^&]*?)(&lt;/strong&gt;)",
        index_values,
        source,
    )

    balance_values = [format_balance(latest["tb"]), format_balance(latest["ob"])]
    source = replace_exactly_twice(
        r"(&lt;strong data-role=&quot;balance-value&quot;&gt;)([^&]*?)(&lt;/strong&gt;)",
        balance_values,
        source,
    )
    source = replace_exactly_twice(
        r"(&lt;strong data-role=&quot;stat-balance-value&quot;&gt;)([^&]*?)(&lt;/strong&gt;)",
        [format_stat_balance(latest["tb"]), format_stat_balance(latest["ob"])],
        source,
    )

    source = replace_tone_values(
        "mmc-delta",
        "maint-delta",
        [
            format_delta(latest["tw"] - previous["tw"], "pt"),
            format_delta(latest["ot"] - previous["ot"], "pt"),
        ],
        source,
    )
    source = replace_tone_values(
        "mmc-mini-delta",
        "balance-delta",
        [
            format_delta(latest["tb"] - previous["tb"], "億"),
            format_delta(latest["ob"] - previous["ob"], "億"),
        ],
        source,
    )

    ratio_values = [format_ratio(latest["tr"]), format_ratio(latest["or"])]
    source = replace_exactly_twice(
        r"(&lt;strong data-role=&quot;ratio-value&quot;&gt;)([^&]*?)(&lt;/strong&gt;)",
        ratio_values,
        source,
    )

    display_day = latest["d"].replace("-", "/")
    source, count = re.subn(
        r"(class=&quot;mmc-updated&quot;&gt;更新時間 )\d{4}/\d{2}/\d{2}",
        rf"\g<1>{display_day}",
        source,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"Expected one updated-date label, replaced {count}")

    source, count = re.subn(
        r"(data-role=&quot;date&quot; class=&quot;mmc-date mmc-visually-hidden&quot;&gt;)"
        r"\d{4}/\d{2}/\d{2}",
        rf"\g<1>{display_day}",
        source,
    )
    if count != 2:
        raise RuntimeError(f"Expected two hidden date labels, replaced {count}")

    args.html.write_text(source, encoding="utf-8")
    print(
        f"embedded {len(rows)} fully recalculated rows through {latest['d']} "
        f"(TWSE {values[0]} / {index_values[0]} / {balance_values[0]}億 / {ratio_values[0]}, "
        f"TPEx {values[1]} / {index_values[1]} / {balance_values[1]}億 / {ratio_values[1]})"
    )


if __name__ == "__main__":
    main()
