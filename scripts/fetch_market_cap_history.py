#!/usr/bin/env python3
"""Fetch TWSE/TPEx market capitalization for margin-to-market-cap ratios."""

from __future__ import annotations

import argparse
import bisect
import gzip
import io
import json
import math
import os
import re
import ssl
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import requests
from requests.adapters import HTTPAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tw_margin_rate.finmind import FinMindClient, load_dotenv
from tw_margin_rate.calculation import market_for_stock
from tw_margin_rate.paths import discover_stock_data, local_env_path

from build_margin_maintenance_history import load_market_reference


FINMIND_DATASET = "TaiwanStockMarketValue"
TPEX_DAILY_MARKET_VALUE_URL = (
    "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyMarktVal"
)
TWSE_HOME_VALUES_URL = "https://www.twse.com.tw/res/data/zh/home/values.json"
TWSE_WEEKLY_URL = (
    "https://www.twse.com.tw/staticFiles/inspection/inspection/week.zip"
)
TWSE_MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
STOCK_CODE = re.compile(r"\d{4}")
TWSE_SCOPE = "four-digit common shares; ETFs, TDRs, and preferred shares excluded"
TWSE_CLASSIFICATION = (
    "official TWSE listing/delisting intervals plus daily TaiwanStockInfo snapshots; "
    "company listing dates preserved, earlier Innovation Board dates merged; "
    "exact-day snapshots override interval boundaries; scope-reconciliation v3"
)
_thread_state = threading.local()


class TpexCertificateAdapter(HTTPAdapter):
    """Keep certificate verification while accepting TPEx's legacy chain."""

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        context = ssl.create_default_context()
        strict_flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
        if strict_flag:
            context.verify_flags &= ~strict_flag
        pool_kwargs["ssl_context"] = context
        return super().init_poolmanager(
            connections, maxsize, block=block, **pool_kwargs
        )


def parse_args() -> argparse.Namespace:
    current = date.today()
    parser = argparse.ArgumentParser()
    parser.add_argument("--twse-start", default="2017-07-03")
    parser.add_argument("--tpex-start", default="2017-07-03")
    parser.add_argument("--end", default=current.isoformat())
    parser.add_argument("--stock-data", type=Path)
    parser.add_argument(
        "--margin-history",
        type=Path,
        default=PROJECT_ROOT / "data/cache/market-margin-money-history.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data/cache/market-cap-history.json",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--refresh-days", type=int, default=7)
    parser.add_argument("--validation-tolerance-pct", type=float, default=0.5)
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
    parser.add_argument(
        "--twse-newlisting-json",
        type=Path,
        default=PROJECT_ROOT / "data/reference/twse-newlisting.latest.json",
    )
    return parser.parse_args()


def is_stock_code(stock_id: str) -> bool:
    return bool(STOCK_CODE.fullmatch(stock_id)) and not stock_id.startswith(("0", "91"))


def is_twse_for_day(
    stock_id: str,
    day: str,
    market_map: dict[str, str],
    listing_dates: dict[str, str],
    delisting_dates: dict[str, str],
    *,
    exact_market_snapshot: bool,
) -> bool:
    if exact_market_snapshot and market_map.get(stock_id) in {"twse", "tpex"}:
        return market_map[stock_id] == "twse"
    return (
        market_for_stock(
            stock_id,
            day,
            market_map,
            listing_dates,
            delisting_dates,
        )
        == "twse"
    )


def round_100m(value_twd: object) -> float:
    value = (Decimal(str(value_twd)) / Decimal("100000000")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return float(value)


def parse_finmind_twse_rows(
    rows: list[dict[str, object]],
    expected_day: str,
    market_map: dict[str, str],
    listing_dates: dict[str, str] | None = None,
    delisting_dates: dict[str, str] | None = None,
    *,
    exact_market_snapshot: bool = False,
) -> tuple[float, int, int, float]:
    dates = {str(row.get("date", "")) for row in rows}
    if dates != {expected_day}:
        raise RuntimeError(
            f"FinMind {expected_day} 日期不符：{sorted(dates)[:3]}"
        )
    ordinary = [
        row
        for row in rows
        if is_stock_code(str(row.get("stock_id", "")))
        and row.get("market_value") is not None
    ]
    matched = [
        row
        for row in ordinary
        if is_twse_for_day(
            str(row.get("stock_id", "")),
            expected_day,
            market_map,
            listing_dates or {},
            delisting_dates or {},
            exact_market_snapshot=exact_market_snapshot,
        )
    ]
    if len(rows) < 1_000 or len(matched) < 700:
        raise RuntimeError(
            f"FinMind {expected_day} 筆數不合理：all={len(rows)}, twse={len(matched)}"
        )
    amount = sum(Decimal(str(row["market_value"])) for row in matched)
    ordinary_amount = sum(Decimal(str(row["market_value"])) for row in ordinary)
    return round_100m(amount), len(matched), len(rows), round_100m(ordinary_amount)


def derive_twse_from_reference_rows(
    rows: list[dict[str, object]],
    reference_day: str,
    expected_day: str,
    market_map: dict[str, str],
    reference_prices: dict[str, float],
    expected_prices: dict[str, float],
    listing_dates: dict[str, str] | None = None,
    delisting_dates: dict[str, str] | None = None,
    *,
    exact_market_snapshot: bool = False,
) -> tuple[float, int, int, int, float]:
    """Derive a missing make-up Saturday from the previous day's share count."""
    dates = {str(row.get("date", "")) for row in rows}
    if dates != {reference_day}:
        raise RuntimeError(
            f"FinMind 參考日 {reference_day} 日期不符：{sorted(dates)[:3]}"
        )
    amount = Decimal("0")
    ordinary_amount = Decimal("0")
    matched = 0
    adjusted = 0
    for row in rows:
        stock_id = str(row.get("stock_id", ""))
        market_value = row.get("market_value")
        if not is_stock_code(stock_id) or market_value is None:
            continue
        reference_close = reference_prices.get(stock_id)
        expected_close = expected_prices.get(stock_id)
        value = Decimal(str(market_value))
        if (
            reference_close is not None
            and expected_close is not None
            and math.isfinite(reference_close)
            and math.isfinite(expected_close)
            and reference_close > 0
            and expected_close > 0
        ):
            value *= Decimal(str(expected_close)) / Decimal(str(reference_close))
        ordinary_amount += value
        if (
            not is_twse_for_day(
                stock_id,
                expected_day,
                market_map,
                listing_dates or {},
                delisting_dates or {},
                exact_market_snapshot=exact_market_snapshot,
            )
        ):
            continue
        matched += 1
        if (
            reference_close is not None
            and expected_close is not None
            and math.isfinite(reference_close)
            and math.isfinite(expected_close)
            and reference_close > 0
            and expected_close > 0
        ):
            adjusted += 1
        amount += value
    if len(rows) < 1_000 or matched < 700 or adjusted < 700:
        raise RuntimeError(
            f"FinMind {expected_day} 推導筆數不合理："
            f"all={len(rows)}, twse={matched}, adjusted={adjusted}"
        )
    return (
        round_100m(amount),
        matched,
        len(rows),
        adjusted,
        round_100m(ordinary_amount),
    )


def load_close_prices(stock_data: Path, day: str) -> dict[str, float]:
    path = stock_data / f"raw/prices/{day[:4]}/{day}.parquet"
    if not path.exists() or path.stat().st_size == 0:
        raise FileNotFoundError(f"缺少 {day} 收盤價：{path}")
    frame = pd.read_parquet(path, columns=["stock_id", "close"])
    frame = frame.dropna(subset=["stock_id", "close"])
    return dict(zip(frame["stock_id"].astype(str), frame["close"].astype(float)))


def parse_tpex_payload(
    payload: dict[str, object], expected_day: str
) -> tuple[float, int]:
    if str(payload.get("date", "")) != expected_day.replace("-", ""):
        raise RuntimeError(f"TPEx 日期不符：expected={expected_day}")
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        raise RuntimeError(f"TPEx {expected_day} 缺少 tables")
    table = tables[0]
    fields = table.get("fields")
    rows = table.get("data")
    if not isinstance(fields, list) or not isinstance(rows, list):
        raise RuntimeError(f"TPEx {expected_day} 格式錯誤")
    try:
        value_index = fields.index("市值(佰萬元)")
    except ValueError as exc:
        raise RuntimeError(f"TPEx {expected_day} 缺少市值欄位") from exc
    if len(rows) < 500:
        raise RuntimeError(f"TPEx {expected_day} 筆數不合理：{len(rows)}")
    amount_million = sum(
        Decimal(str(row[value_index]).replace(",", "")) for row in rows
    )
    amount_100m = amount_million / Decimal("100")
    return (
        float(amount_100m.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        len(rows),
    )


def load_expected_days(path: Path, start: str, end: str) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    markets = payload.get("markets", {})
    twse = {str(row["date"]) for row in markets.get("twse", [])}
    tpex = {str(row["date"]) for row in markets.get("tpex", [])}
    if twse != tpex:
        raise RuntimeError("TWSE 與 TPEx 融資金額日期不一致")
    days = sorted(day for day in twse if start <= day <= end)
    if not days:
        raise RuntimeError(f"{start}..{end} 沒有共同交易日")
    return days


def load_market_snapshots(
    stock_data: Path, days: list[str]
) -> tuple[dict[str, dict[str, str]], set[str]]:
    all_paths = sorted((stock_data / "raw/stock_info").glob("*/*.parquet"))
    paths = [
        path
        for path in all_paths
        if pq.ParquetFile(path).metadata.num_rows >= 2_000
    ]
    if not paths:
        raise FileNotFoundError("stock_data 沒有完整的 stock_info parquet")
    snapshot_days = [path.stem for path in paths]
    selected: dict[str, Path] = {}
    exact_days: set[str] = set()
    for day in days:
        index = bisect.bisect_right(snapshot_days, day) - 1
        if index < 0:
            # The shared archive began retaining stock_info snapshots in May 2026.
            # Before that point the earliest snapshot is only a fallback; the
            # official listing interval remains authoritative.
            index = 0
        selected[day] = paths[index]
        if paths[index].stem == day:
            exact_days.add(day)

    loaded: dict[Path, dict[str, str]] = {}
    for path in sorted(set(selected.values())):
        frame = pd.read_parquet(path, columns=["stock_id", "type"])
        frame = frame.dropna(subset=["stock_id", "type"])
        loaded[path] = dict(
            zip(frame["stock_id"].astype(str), frame["type"].astype(str))
        )
    return {day: loaded[path] for day, path in selected.items()}, exact_days


def request_session(*, tpex: bool = False) -> requests.Session:
    session = requests.Session()
    if tpex:
        session.mount("https://www.tpex.org.tw", TpexCertificateAdapter())
    session.headers.update(
        {"Accept": "application/json", "User-Agent": "tw-margin-rate/1.0"}
    )
    return session


def fetch_tpex_day(day: str) -> tuple[float, int]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            session = getattr(_thread_state, "tpex_session", None)
            if session is None:
                session = request_session(tpex=True)
                _thread_state.tpex_session = session
            response = session.get(
                TPEX_DAILY_MARKET_VALUE_URL,
                params={"date": day.replace("-", "/"), "response": "json"},
                timeout=(15, 45),
            )
            response.raise_for_status()
            return parse_tpex_payload(response.json(), day)
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"TPEx {day} 市值查詢失敗：{last_error}") from last_error


def parse_twse_mi_index_codes(payload: dict[str, object], day: str) -> set[str]:
    if payload.get("stat") != "OK" or str(payload.get("date", "")) != day.replace(
        "-", ""
    ):
        raise RuntimeError(f"TWSE MI_INDEX {day} 日期或狀態不符")
    for table in payload.get("tables", []):
        if not isinstance(table, dict):
            continue
        fields = table.get("fields")
        rows = table.get("data")
        if not isinstance(fields, list) or not isinstance(rows, list):
            continue
        if "證券代號" not in fields or "每日收盤行情" not in str(
            table.get("title", "")
        ):
            continue
        code_index = fields.index("證券代號")
        codes = {
            str(row[code_index]).strip()
            for row in rows
            if len(row) > code_index and is_stock_code(str(row[code_index]).strip())
        }
        if len(codes) < 700:
            raise RuntimeError(f"TWSE MI_INDEX {day} 普通股筆數不合理：{len(codes)}")
        return codes
    raise RuntimeError(f"TWSE MI_INDEX {day} 缺少每日收盤行情")


def fetch_twse_mi_index_codes(day: str, cache_dir: Path) -> set[str]:
    cache_path = cache_dir / f"{day}.json.gz"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        with gzip.open(cache_path, "rt", encoding="utf-8") as handle:
            cached = json.load(handle)
        codes = cached.get("codes")
        if isinstance(codes, list) and len(codes) >= 700:
            return {str(code) for code in codes}

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            session = getattr(_thread_state, "twse_session", None)
            if session is None:
                session = request_session()
                _thread_state.twse_session = session
            response = session.get(
                TWSE_MI_INDEX_URL,
                params={
                    "date": day.replace("-", ""),
                    "type": "ALLBUT0999",
                    "response": "json",
                },
                timeout=(15, 60),
            )
            response.raise_for_status()
            codes = parse_twse_mi_index_codes(response.json(), day)
            cache_dir.mkdir(parents=True, exist_ok=True)
            temp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
            with gzip.open(temp_path, "wt", encoding="utf-8") as handle:
                json.dump(
                    {"date": day, "codes": sorted(codes)},
                    handle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            os.replace(temp_path, cache_path)
            return codes
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"TWSE MI_INDEX {day} 查詢失敗：{last_error}") from last_error


def load_existing(path: Path) -> dict[str, dict[str, dict[str, object]]]:
    result: dict[str, dict[str, dict[str, object]]] = {"twse": {}, "tpex": {}}
    if not path.exists():
        return result
    payload = json.loads(path.read_text(encoding="utf-8"))
    valid_scope = {
        "twse": (
            payload.get("metadata", {}).get("twse_scope") == TWSE_SCOPE
            and payload.get("metadata", {}).get("market_classification")
            == TWSE_CLASSIFICATION
        ),
        "tpex": True,
    }
    for market in result:
        if not valid_scope[market]:
            continue
        for row in payload.get("markets", {}).get(market, []):
            result[market][str(row["date"])] = dict(row)
    return result


def parse_date_value(value: object) -> str | None:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    for pattern in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def official_twse_weekly() -> dict[str, float]:
    response = requests.get(
        TWSE_WEEKLY_URL,
        headers={"User-Agent": "tw-margin-rate/1.0"},
        timeout=(15, 60),
    )
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".xls")]
        if len(members) != 1:
            raise RuntimeError("TWSE 市值週報壓縮檔格式改變")
        frame = pd.read_excel(io.BytesIO(archive.read(members[0])), header=None)
    result: dict[str, float] = {}
    for row in frame.itertuples(index=False, name=None):
        if len(row) < 2:
            continue
        day = parse_date_value(row[0])
        if not day:
            continue
        try:
            value = float(str(row[1]).replace(",", ""))
        except ValueError:
            continue
        if math.isfinite(value) and value > 0:
            result[day] = value
    if not result:
        raise RuntimeError("TWSE 市值週報沒有可用資料")
    return result


def official_twse_recent(year: int) -> dict[str, float]:
    response = requests.get(
        TWSE_HOME_VALUES_URL,
        headers={"User-Agent": "tw-margin-rate/1.0"},
        timeout=(15, 45),
    )
    response.raise_for_status()
    rows = response.json().get("market", [])
    result: dict[str, float] = {}
    for day_text, value in rows:
        month, day = (int(part) for part in str(day_text).split("/"))
        result[f"{year:04d}-{month:02d}-{day:02d}"] = float(value)
    return result


def comparison_rows(
    candidate: dict[str, dict[str, object]], official: dict[str, float]
) -> list[dict[str, object]]:
    result = []
    for day in sorted(set(candidate) & set(official)):
        actual = float(candidate[day]["market_cap"])
        expected = float(official[day])
        difference_pct = (actual - expected) / expected * 100.0
        result.append(
            {
                "date": day,
                "candidate": round(actual, 2),
                "official": round(expected, 2),
                "difference_pct": round(difference_pct, 4),
            }
        )
    return result


def last_day_per_month(days: list[str]) -> list[str]:
    selected: dict[str, str] = {}
    for day in sorted(days):
        selected[day[:7]] = day
    return sorted(selected.values())


def scope_reconciliation_rows(
    twse: dict[str, dict[str, object]],
    tpex: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    result = []
    for day in sorted(set(twse) & set(tpex)):
        expected = twse[day].get("all_ordinary_market_cap")
        if expected is None:
            continue
        actual = float(twse[day]["market_cap"]) + float(tpex[day]["market_cap"])
        expected = float(expected)
        result.append(
            {
                "date": day,
                "twse_plus_tpex": round(actual, 2),
                "finmind_all_ordinary": round(expected, 2),
                "difference_pct": round((actual - expected) / expected * 100.0, 4),
            }
        )
    return result


def official_twse_scope_rows(
    values: dict[str, dict[str, object]],
    days: list[str],
    client: FinMindClient,
    *,
    workers: int,
    cache_dir: Path,
) -> list[dict[str, object]]:
    def compare(day: str) -> dict[str, object]:
        codes = fetch_twse_mi_index_codes(day, cache_dir)
        rows = client.fetch(
            FINMIND_DATASET,
            start_date=day,
            end_date=(
                datetime.strptime(day, "%Y-%m-%d").date() + timedelta(days=1)
            ).isoformat(),
            force=False,
        )
        expected = round_100m(
            sum(
                Decimal(str(row["market_value"]))
                for row in rows
                if str(row.get("stock_id", "")) in codes
                and row.get("market_value") is not None
            )
        )
        actual = float(values[day]["market_cap"])
        return {
            "date": day,
            "candidate": round(actual, 2),
            "official_code_scope": expected,
            "official_code_count": len(codes),
            "difference_pct": round((actual - expected) / expected * 100.0, 4),
        }

    result = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {executor.submit(compare, day): day for day in days}
        for future in as_completed(pending):
            result.append(future.result())
    return sorted(result, key=lambda row: str(row["date"]))


def validation_summary(
    rows: list[dict[str, object]], *, gating: bool = True
) -> dict[str, object]:
    return {
        "gating": gating,
        "count": len(rows),
        "max_abs_difference_pct": (
            round(max(abs(float(row["difference_pct"])) for row in rows), 4)
            if rows
            else None
        ),
        "rows": rows,
    }


def write_output(
    output: Path,
    values: dict[str, dict[str, dict[str, object]]],
    *,
    starts: dict[str, str],
    end: str,
    validation: dict[str, object] | None,
    complete: bool,
    tolerance_pct: float,
) -> None:
    first_days = {
        market: min(rows) if rows else None for market, rows in values.items()
    }
    last_days = {
        market: max(rows) if rows else None for market, rows in values.items()
    }
    validation_maxima = []
    if validation:
        for details in validation.values():
            if not details.get("gating", True):
                continue
            maximum = details.get("max_abs_difference_pct")
            if maximum is not None:
                validation_maxima.append(float(maximum))
    validation_passed = bool(validation_maxima) and max(validation_maxima) <= tolerance_pct
    payload = {
        "metadata": {
            "generated_at": datetime.now().astimezone().isoformat(),
            "unit": "TWD 100 million",
            "market_cap_starts": starts,
            "start_by_market": first_days,
            "end": min(day for day in last_days.values() if day is not None),
            "complete": complete,
            "validation_tolerance_pct": tolerance_pct,
            "validation_passed": validation_passed if complete else False,
            "twse_source": (
                "FinMind TaiwanStockMarketValue; daily TWSE ordinary shares summed by "
                "official listing interval; three make-up Saturdays use prior share count"
            ),
            "twse_scope": TWSE_SCOPE,
            "tpex_source": "TPEx /afterTrading/dailyMarktVal / 市值(佰萬元)",
            "market_classification": TWSE_CLASSIFICATION,
            "twse_official_scope_sample": (
                "last weekly-report day of each month plus recent official days"
            ),
        },
        "validation": validation or {},
        "markets": {
            market: [values[market][day] for day in sorted(values[market])]
            for market in ("twse", "tpex")
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=output.parent, delete=False
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        temp_path = Path(handle.name)
    os.replace(temp_path, output)


def main() -> None:
    args = parse_args()
    if args.workers < 1 or args.workers > 8:
        raise ValueError("--workers 必須介於 1 與 8")
    if args.refresh_days < 0:
        raise ValueError("--refresh-days 不得小於 0")
    twse_start_day = datetime.strptime(args.twse_start, "%Y-%m-%d").date()
    tpex_start_day = datetime.strptime(args.tpex_start, "%Y-%m-%d").date()
    end_day = datetime.strptime(args.end, "%Y-%m-%d").date()
    if twse_start_day > end_day:
        raise ValueError("--twse-start 不得晚於 --end")
    if tpex_start_day > end_day:
        raise ValueError("--tpex-start 不得晚於 --end")

    stock_data = discover_stock_data(args.stock_data)
    all_expected_days = load_expected_days(
        args.margin_history, min(args.twse_start, args.tpex_start), args.end
    )
    expected_days = {
        "twse": [day for day in all_expected_days if day >= args.twse_start],
        "tpex": [day for day in all_expected_days if day >= args.tpex_start],
    }
    if not expected_days["twse"] or not expected_days["tpex"]:
        raise RuntimeError("上市或櫃買市值期間沒有交易日")
    market_maps, exact_market_days = load_market_snapshots(
        stock_data, expected_days["twse"]
    )
    _, listing_dates, delisting_dates = load_market_reference(
        stock_data,
        args.twse_company_info,
        args.twse_delisted_html,
        args.twse_newlisting_json,
    )
    values = load_existing(args.output)
    refresh_start = (end_day - timedelta(days=args.refresh_days)).isoformat()
    needed = {
        market: [
            day
            for day in expected_days[market]
            if day >= refresh_start or day not in values[market]
        ]
        for market in ("twse", "tpex")
    }
    needed_days = sorted(set(needed["twse"]) | set(needed["tpex"]))
    twse_needed = set(needed["twse"])
    tpex_needed = set(needed["tpex"])

    load_dotenv(local_env_path())
    client = FinMindClient(
        token=os.environ.get("FINMIND_TOKEN", ""),
        cache_dir=PROJECT_ROOT / "data/cache",
    )

    # FinMind omits the three historical make-up Saturdays that are present in
    # the price and margin datasets. Derive those days from the previous
    # trading day's shares outstanding and the Saturday closing prices.
    expected_index = {day: index for index, day in enumerate(expected_days["twse"])}
    makeup_days = [
        day
        for day in needed["twse"]
        if datetime.strptime(day, "%Y-%m-%d").weekday() >= 5
    ]
    for day in makeup_days:
        index = expected_index[day]
        if index == 0:
            raise RuntimeError(f"{day} 沒有可用的前一交易日")
        reference_day = expected_days["twse"][index - 1]
        rows = client.fetch(
            FINMIND_DATASET,
            start_date=reference_day,
            end_date=(
                datetime.strptime(reference_day, "%Y-%m-%d").date()
                + timedelta(days=1)
            ).isoformat(),
            force=False,
        )
        amount, matched, total, adjusted, ordinary_amount = (
            derive_twse_from_reference_rows(
                rows,
                reference_day,
                day,
                market_maps[day],
                load_close_prices(stock_data, reference_day),
                load_close_prices(stock_data, day),
                listing_dates,
                delisting_dates,
                exact_market_snapshot=day in exact_market_days,
            )
        )
        values["twse"][day] = {
            "date": day,
            "market_cap": amount,
            "matched_count": matched,
            "source_row_count": total,
            "derived_from": reference_day,
            "price_adjusted_count": adjusted,
            "all_ordinary_market_cap": ordinary_amount,
        }
        twse_needed.discard(day)
    needed["twse"] = [day for day in needed["twse"] if day in twse_needed]
    needed_days = sorted(twse_needed | tpex_needed)

    print(
        f"下載市值：上市 {len(needed['twse'])} 日、櫃買 {len(needed['tpex'])} 日",
        flush=True,
    )
    for offset in range(0, len(needed_days), 25):
        batch = needed_days[offset : offset + 25]
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            twse_pending = {
                executor.submit(
                    client.fetch,
                    FINMIND_DATASET,
                    start_date=day,
                    end_date=(
                        datetime.strptime(day, "%Y-%m-%d").date()
                        + timedelta(days=1)
                    ).isoformat(),
                    force=day >= refresh_start,
                ): day
                for day in batch
                if day in twse_needed
            }
            tpex_pending = {
                executor.submit(fetch_tpex_day, day): day for day in batch
                if day in tpex_needed
            }
            for future in as_completed(twse_pending):
                day = twse_pending[future]
                amount, matched, total, ordinary_amount = parse_finmind_twse_rows(
                    future.result(),
                    day,
                    market_maps[day],
                    listing_dates,
                    delisting_dates,
                    exact_market_snapshot=day in exact_market_days,
                )
                values["twse"][day] = {
                    "date": day,
                    "market_cap": amount,
                    "matched_count": matched,
                    "source_row_count": total,
                    "all_ordinary_market_cap": ordinary_amount,
                }
            for future in as_completed(tpex_pending):
                day = tpex_pending[future]
                amount, count = future.result()
                values["tpex"][day] = {
                    "date": day,
                    "market_cap": amount,
                    "source_row_count": count,
                }
        completed = offset + len(batch)
        print(f"市值資料 {completed}/{len(needed_days)}", flush=True)
        write_output(
            args.output,
            values,
            starts={"twse": args.twse_start, "tpex": args.tpex_start},
            end=args.end,
            validation=None,
            complete=False,
            tolerance_pct=args.validation_tolerance_pct,
        )

    starts = {"twse": args.twse_start, "tpex": args.tpex_start}
    for market in values:
        values[market] = {
            day: row
            for day, row in values[market].items()
            if starts[market] <= day <= args.end
        }
    for market in values:
        missing = sorted(set(expected_days[market]) - set(values[market]))
        if missing:
            raise RuntimeError(f"{market} 缺少市值日期：{missing[:5]}")

    recent_official = official_twse_recent(end_day.year)
    weekly_official = official_twse_weekly()
    recent = comparison_rows(values["twse"], recent_official)
    weekly = comparison_rows(values["twse"], weekly_official)
    weekly_scope_days = last_day_per_month(
        [
            day
            for day in set(values["twse"]) & set(weekly_official)
            if "derived_from" not in values["twse"][day]
        ]
    )
    recent_scope_days = [
        day
        for day in set(values["twse"]) & set(recent_official)
        if "derived_from" not in values["twse"][day]
    ]
    official_scope_days = sorted(set(weekly_scope_days) | set(recent_scope_days))
    official_scope = official_twse_scope_rows(
        values["twse"],
        official_scope_days,
        client,
        workers=min(args.workers, 2),
        cache_dir=PROJECT_ROOT / "data/cache/TWSEMIINDEX",
    )
    reconciliation = scope_reconciliation_rows(values["twse"], values["tpex"])
    validation = {
        "twse_official_code_scope": validation_summary(official_scope),
        "finmind_all_four_digit_combined_scope_gap": validation_summary(
            reconciliation, gating=False
        ),
        "twse_recent_official_total_scope_gap": validation_summary(
            recent, gating=False
        ),
        "twse_weekly_official_total_scope_gap": validation_summary(
            weekly, gating=False
        ),
    }
    if not official_scope or not reconciliation or not recent or not weekly:
        raise RuntimeError("TWSE 普通股名單、最近日或週報沒有交叉驗證日期")
    write_output(
        args.output,
        values,
        starts=starts,
        end=args.end,
        validation=validation,
        complete=True,
        tolerance_pct=args.validation_tolerance_pct,
    )
    print(
        f"市值資料完成：上市 {len(expected_days['twse'])} 日、"
        f"櫃買 {len(expected_days['tpex'])} 日，最新 {args.end}；"
        "TWSE 官方普通股名單核對最大差 "
        f"{validation['twse_official_code_scope']['max_abs_difference_pct']}%；"
        "TWSE 官方總市值口徑差（非阻擋）最近日 "
        f"{validation['twse_recent_official_total_scope_gap']['max_abs_difference_pct']}%、"
        "週報 "
        f"{validation['twse_weekly_official_total_scope_gap']['max_abs_difference_pct']}%",
        flush=True,
    )


if __name__ == "__main__":
    main()
