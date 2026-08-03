#!/usr/bin/env python3
"""Build market margin-maintenance estimates from the local FinMind archive.

The calculation follows the stock-level inventory-cost approach used throughout
this project.  It keeps a moving average cost for every financed stock and then
aggregates market value and estimated margin debt by market:

    maintenance = sum(close * balance) / sum(avg_cost * balance * 0.60) * 100

Only four-digit ordinary shares are included. ETFs (codes beginning with 0)
and TDRs (codes beginning with 91) are excluded.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tw_margin_rate.calculation import market_for_stock
from tw_margin_rate.finmind import FinMindClient, load_dotenv
from tw_margin_rate.paths import discover_stock_data


MARGIN_COLUMNS = [
    "date",
    "stock_id",
    "MarginPurchaseBuy",
    "MarginPurchaseCashRepayment",
    "MarginPurchaseSell",
    "MarginPurchaseTodayBalance",
    "MarginPurchaseYesterdayBalance",
]
PRICE_COLUMNS = ["date", "stock_id", "close"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock-data", type=Path)
    parser.add_argument("--workspace", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--twse-company-info",
        type=Path,
        default=PROJECT_ROOT / "data/reference/twse-company-info.latest.json",
    )
    parser.add_argument(
        "--twse-delisted-html",
        type=Path,
        default=PROJECT_ROOT / "data/reference/twse-delisted.latest.html",
    )
    parser.add_argument("--warmup-start", default="2001-01-05")
    parser.add_argument("--display-start", default="2017-07-03")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument(
        "--no-finmind-fetch",
        action="store_true",
        help="Use existing cache only; do not fetch dates after the iCloud archive.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/margin-maintenance-history.json"),
    )
    return parser.parse_args()


def roc_or_iso_date(value: object) -> str | None:
    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    match = re.fullmatch(r"(\d{2,3})年(\d{2})月(\d{2})日", text)
    if match:
        return f"{int(match.group(1)) + 1911:04d}-{match.group(2)}-{match.group(3)}"
    return None


def load_market_reference(
    stock_data: Path, twse_company_info: Path, twse_delisted_html: Path
) -> tuple[dict[str, str], dict[str, str], set[str]]:
    stock_info_files = sorted((stock_data / "raw/stock_info").glob("*/*.parquet"))
    if not stock_info_files:
        raise FileNotFoundError("No stock_info parquet files found")

    stock_info = pd.read_parquet(
        stock_info_files[-1], columns=["stock_id", "type", "date"]
    )
    stock_info = stock_info.dropna(subset=["type"])
    stock_info = stock_info[stock_info["type"].isin(["twse", "tpex"])]
    stock_info = stock_info.drop_duplicates("stock_id", keep="first")
    current_market = dict(zip(stock_info["stock_id"].astype(str), stock_info["type"]))

    with twse_company_info.open(encoding="utf-8") as handle:
        listed_rows = json.load(handle)
    twse_listing_dates: dict[str, str] = {}
    for row in listed_rows:
        stock_id = str(row.get("公司代號", "")).strip()
        listing_date = roc_or_iso_date(row.get("上市日期"))
        if stock_id and listing_date:
            twse_listing_dates[stock_id] = listing_date

    tables = pd.read_html(twse_delisted_html)
    if not tables:
        raise ValueError("TWSE delisted-company table is empty")
    delisted_table = tables[0]
    delisted_codes = {
        str(value).strip()
        for value in delisted_table.iloc[:, 2].tolist()
        if re.fullmatch(r"\d{4}", str(value).strip())
    }
    return current_market, twse_listing_dates, delisted_codes


def load_cache(path: Path, columns: list[str]) -> pd.DataFrame:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    frame = pd.DataFrame(payload.get("data", []))
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{path} is missing columns: {missing}")
    return frame[columns]


def cache_file_for_date(cache_root: Path, dataset: str, day: str) -> Path | None:
    matches = sorted((cache_root / dataset).glob(f"{day}_*.json.gz"))
    return matches[-1] if matches else None


def latest_icloud_margin_date(stock_data: Path) -> str:
    files = sorted((stock_data / "raw/chips_margin").glob("*/*.parquet"))
    if not files:
        raise FileNotFoundError("No iCloud margin parquet files found")
    return files[-1].stem


def ensure_finmind_cache(workspace: Path, first_day: str, end: str) -> None:
    """Fetch one market day at a time to avoid FinMind's market-query row cap."""
    load_dotenv(workspace / ".env")
    import os

    token = os.environ.get("FINMIND_TOKEN", "")
    client = FinMindClient(token=token, cache_dir=workspace / "data/cache")
    cursor = datetime.strptime(first_day, "%Y-%m-%d").date()
    cutoff = datetime.strptime(end, "%Y-%m-%d").date()
    while cursor <= cutoff:
        if cursor.weekday() < 5:
            day = cursor.isoformat()
            next_day = (cursor + timedelta(days=1)).isoformat()
            client.fetch(
                "TaiwanStockPrice", start_date=day, end_date=next_day, force=False
            )
            client.fetch(
                "TaiwanStockMarginPurchaseShortSale",
                start_date=day,
                end_date=next_day,
                force=False,
            )
        cursor += timedelta(days=1)


def sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def prepare_local_history(
    stock_data: Path, warmup_start: str, end: str
) -> tuple[list[Path], dict[str, dict[str, float]], str]:
    """Scan the iCloud parquet archive once and materialize compact yearly joins."""
    temp_root = Path(tempfile.mkdtemp(prefix="margin-maintenance-"))
    joined_root = temp_root / "joined"
    years = range(int(warmup_start[:4]), int(end[:4]) + 1)
    connection = duckdb.connect()
    connection.execute("SET threads = 6")
    connection.execute("SET preserve_insertion_order = false")
    index_frames: list[pd.DataFrame] = []
    print("scanning local parquet archive by year", flush=True)
    for year in years:
        margin_pattern = stock_data / f"raw/chips_margin/{year}/*.parquet"
        price_pattern = stock_data / f"raw/prices/{year}/*.parquet"
        if not list(margin_pattern.parent.glob("*.parquet")):
            continue
        if not list(price_pattern.parent.glob("*.parquet")):
            continue

        year_directory = joined_root / f"year={year}"
        year_directory.mkdir(parents=True, exist_ok=True)
        year_output = year_directory / "data.parquet"
        connection.execute(
            f"""
            COPY (
                WITH margin AS (
                    SELECT
                        CAST(date AS VARCHAR) AS date,
                        CAST(stock_id AS VARCHAR) AS stock_id,
                        MarginPurchaseBuy,
                        MarginPurchaseCashRepayment,
                        MarginPurchaseSell,
                        MarginPurchaseTodayBalance,
                        MarginPurchaseYesterdayBalance
                    FROM read_parquet('{sql_path(margin_pattern)}', union_by_name=true)
                    WHERE CAST(date AS VARCHAR) BETWEEN '{warmup_start}' AND '{end}'
                      AND regexp_full_match(CAST(stock_id AS VARCHAR), '[0-9]{{4}}')
                      AND CAST(stock_id AS VARCHAR) NOT LIKE '0%'
                      AND CAST(stock_id AS VARCHAR) NOT LIKE '91%'
                ),
                prices AS (
                    SELECT
                        CAST(date AS VARCHAR) AS date,
                        CAST(stock_id AS VARCHAR) AS stock_id,
                        MAX(close) AS close
                    FROM read_parquet('{sql_path(price_pattern)}', union_by_name=true)
                    WHERE CAST(date AS VARCHAR) BETWEEN '{warmup_start}' AND '{end}'
                      AND regexp_full_match(CAST(stock_id AS VARCHAR), '[0-9]{{4}}')
                      AND CAST(stock_id AS VARCHAR) NOT LIKE '0%'
                      AND CAST(stock_id AS VARCHAR) NOT LIKE '91%'
                    GROUP BY 1, 2
                )
                SELECT m.*, p.close
                FROM margin AS m
                LEFT JOIN prices AS p USING (date, stock_id)
                ORDER BY m.date, m.stock_id
            ) TO '{sql_path(year_output)}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        index_frames.append(
            connection.execute(
                f"""
                SELECT
                    CAST(date AS VARCHAR) AS date,
                    MAX(CASE WHEN stock_id = 'TAIEX' THEN close END) AS twse,
                    MAX(CASE WHEN stock_id = 'TPEx' THEN close END) AS tpex
                FROM read_parquet('{sql_path(price_pattern)}', union_by_name=true)
                WHERE CAST(date AS VARCHAR) BETWEEN '{warmup_start}' AND '{end}'
                  AND stock_id IN ('TAIEX', 'TPEx')
                GROUP BY 1
                ORDER BY 1
                """
            ).df()
        )
        print(f"prepared local archive year {year}", flush=True)

    index_frame = pd.concat(index_frames, ignore_index=True).sort_values("date")
    connection.close()

    index_map: dict[str, dict[str, float]] = {}
    for row in index_frame.itertuples(index=False):
        index_map[str(row.date)] = {
            "twse": float(row.twse) if finite(row.twse) else math.nan,
            "tpex": float(row.tpex) if finite(row.tpex) else math.nan,
        }
    yearly_files = sorted(joined_root.glob("year=*/*.parquet"))
    if not yearly_files or not index_map:
        raise RuntimeError("The local parquet scan produced no usable data")
    return yearly_files, index_map, max(index_map)


def collect_cache_sources(
    workspace: Path, local_end: str, warmup_start: str, end: str
) -> list[tuple[str, Path, Path]]:
    cache_root = workspace / "data/cache"
    margin_cache = cache_root / "TaiwanStockMarginPurchaseShortSale"
    sources: dict[str, tuple[str, Path, Path]] = {}
    for margin_path in sorted(margin_cache.glob("*.json.gz")):
        day = margin_path.name[:10]
        if day <= local_end or day < warmup_start or day > end:
            continue
        price_path = cache_file_for_date(cache_root, "TaiwanStockPrice", day)
        if price_path:
            sources[day] = (day, margin_path, price_path)
    return [sources[key] for key in sorted(sources)]


def ordinary_share_mask(stock_ids: pd.Series) -> pd.Series:
    return (
        stock_ids.str.fullmatch(r"\d{4}")
        & ~stock_ids.str.startswith("0")
        & ~stock_ids.str.startswith("91")
    )


def finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_history(args: argparse.Namespace) -> dict[str, object]:
    current_market, listing_dates, delisted_codes = load_market_reference(
        args.stock_data, args.twse_company_info, args.twse_delisted_html
    )
    archive_end = latest_icloud_margin_date(args.stock_data)
    if not args.no_finmind_fetch and archive_end < args.end:
        first_missing = (
            datetime.strptime(archive_end, "%Y-%m-%d").date() + timedelta(days=1)
        ).isoformat()
        ensure_finmind_cache(args.workspace, first_missing, args.end)
    yearly_files, index_map, local_end = prepare_local_history(
        args.stock_data, args.warmup_start, args.end
    )
    cache_sources = collect_cache_sources(
        args.workspace, local_end, args.warmup_start, args.end
    )

    average_cost: dict[str, float] = {}
    last_close: dict[str, float] = {}
    last_index = {"twse": math.nan, "tpex": math.nan}
    history = {"twse": [], "tpex": []}
    processed_days = 0

    def process_day(day: str, margin: pd.DataFrame) -> None:
        nonlocal processed_days
        if day in index_map:
            for market in ("twse", "tpex"):
                if finite(index_map[day][market]):
                    last_index[market] = index_map[day][market]

        margin = margin.copy()
        margin["stock_id"] = margin["stock_id"].astype(str)
        margin["close"] = margin["close"].where(
            margin["close"].notna(), margin["stock_id"].map(last_close)
        )
        margin = margin[margin["close"].notna()].copy()

        stock_ids = margin["stock_id"]
        close = margin["close"].astype(float).to_numpy()
        yesterday = margin["MarginPurchaseYesterdayBalance"].astype(float).to_numpy()
        buys = margin["MarginPurchaseBuy"].astype(float).to_numpy()
        sells = margin["MarginPurchaseSell"].astype(float).to_numpy()
        repayments = margin["MarginPurchaseCashRepayment"].astype(float).to_numpy()
        today = margin["MarginPurchaseTodayBalance"].astype(float).to_numpy()
        remaining = np.maximum(yesterday - sells - repayments, 0.0)

        old_cost = stock_ids.map(average_cost).astype(float).to_numpy()
        old_cost = np.where(np.isfinite(old_cost), old_cost, close)
        numerator = remaining * old_cost + buys * close
        new_cost = np.divide(
            numerator,
            today,
            out=close.copy(),
            where=today > 0,
        )

        for stock_id, stock_close, balance, cost in zip(stock_ids, close, today, new_cost):
            last_close[stock_id] = float(stock_close)
            if balance > 0:
                average_cost[stock_id] = float(cost)
            else:
                average_cost.pop(stock_id, None)

        if day >= args.display_start:
            margin["average_cost"] = new_cost
            margin["market"] = [
                market_for_stock(
                    stock_id, day, current_market, listing_dates, delisted_codes
                )
                for stock_id in stock_ids
            ]
            margin["market_value"] = close * today
            margin["estimated_debt"] = new_cost * today * 0.60

            for market in ("twse", "tpex"):
                group = margin[(margin["market"] == market) & (today > 0)]
                debt = float(group["estimated_debt"].sum())
                market_value = float(group["market_value"].sum())
                maintenance = market_value / debt * 100.0 if debt > 0 else math.nan
                history[market].append(
                    {
                        "date": day,
                        "maintenance": round(maintenance, 2),
                        "index": round(last_index[market], 2),
                        "financed_balance": int(group["MarginPurchaseTodayBalance"].sum()),
                        "stock_count": int((group["MarginPurchaseTodayBalance"] > 0).sum()),
                    }
                )

        processed_days += 1
        if processed_days % 250 == 0:
            print(f"processed {processed_days} trading days through {day}", flush=True)

    for year_directory in sorted({path.parent for path in yearly_files}):
        yearly = pd.concat(
            [pd.read_parquet(path) for path in sorted(year_directory.glob("*.parquet"))],
            ignore_index=True,
        ).sort_values(["date", "stock_id"])
        for day, margin in yearly.groupby("date", sort=True):
            process_day(str(day), margin)

    for day, margin_path, price_path in cache_sources:
        margin = load_cache(margin_path, MARGIN_COLUMNS)
        prices = load_cache(price_path, PRICE_COLUMNS)
        margin["stock_id"] = margin["stock_id"].astype(str)
        margin = margin[ordinary_share_mask(margin["stock_id"])].copy()
        prices["stock_id"] = prices["stock_id"].astype(str)
        prices = prices.drop_duplicates("stock_id", keep="last")
        price_series = prices.set_index("stock_id")["close"]
        index_map[day] = {
            "twse": float(price_series["TAIEX"]),
            "tpex": float(price_series["TPEx"]),
        }
        margin = margin.merge(
            prices[["stock_id", "close"]], on="stock_id", how="left", validate="one_to_one"
        )
        process_day(day, margin)

    print(
        f"processed {processed_days} trading days through {history['twse'][-1]['date']}",
        flush=True,
    )

    return {
        "metadata": {
            "generated_at": pd.Timestamp.now(tz="Asia/Taipei").isoformat(),
            "source": "Local FinMind archive (iCloud + workspace cache)",
            "warmup_start": args.warmup_start,
            "display_start": args.display_start,
            "end": history["twse"][-1]["date"],
            "formula": "sum(close*balance) / sum(avg_cost*balance*0.60) * 100",
            "universe": "Four-digit ordinary shares; ETFs and TDRs excluded",
            "algorithm_version": "stock-level-moving-average-v1",
            "market_reference": {
                "stock_info": "latest iCloud TaiwanStockInfo parquet",
                "twse_company_info": {
                    "file": args.twse_company_info.name,
                    "sha256": file_sha256(args.twse_company_info),
                },
                "twse_delisted": {
                    "file": args.twse_delisted_html.name,
                    "sha256": file_sha256(args.twse_delisted_html),
                },
            },
        },
        "markets": history,
    }


def main() -> None:
    args = parse_args()
    args.workspace = args.workspace.resolve()
    args.stock_data = discover_stock_data(args.stock_data)
    payload = build_history(args)
    output = args.output if args.output.is_absolute() else args.workspace / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"wrote {output}", flush=True)


if __name__ == "__main__":
    main()
