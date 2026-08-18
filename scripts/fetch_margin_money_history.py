#!/usr/bin/env python3
"""Fetch TWSE/TPEx margin-financing amounts in TWD 100 millions."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tw_margin_rate.finmind import FinMindClient, load_dotenv
from tw_margin_rate.paths import local_env_path


TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/margin/balance"
_thread_state = threading.local()


class TpexCertificateAdapter(HTTPAdapter):
    """Keep normal TLS verification while accepting TPEx's legacy chain."""

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
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2017-07-03")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data/cache/market-margin-money-history.json",
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--delay", type=float, default=0.7)
    parser.add_argument("--refresh-days", type=int, default=7)
    return parser.parse_args()


def amount_100m(value: object, divisor: str) -> float:
    amount = (Decimal(str(value).replace(",", "")) / Decimal(divisor)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return float(amount)


def parse_twse_rows(rows: list[dict[str, object]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in rows:
        if row.get("name") != "MarginPurchaseMoney":
            continue
        day = str(row.get("date", ""))
        if not day or row.get("TodayBalance") is None:
            continue
        result[day] = amount_100m(row["TodayBalance"], "100000000")
    if not result:
        raise RuntimeError("FinMind 沒有回傳 MarginPurchaseMoney")
    return result


def parse_tpex_payload(payload: dict[str, object], expected_day: str) -> float:
    if str(payload.get("date", "")) != expected_day.replace("-", ""):
        raise RuntimeError(f"TPEx 日期不符：expected={expected_day}")
    tables = payload.get("tables")
    if not isinstance(tables, list) or not tables:
        raise RuntimeError(f"TPEx {expected_day} 缺少 tables")
    summary = tables[0].get("summary")
    if not isinstance(summary, list):
        raise RuntimeError(f"TPEx {expected_day} 缺少 summary")
    row = next(
        (
            candidate
            for candidate in summary
            if isinstance(candidate, list)
            and len(candidate) > 6
            and candidate[1] == "融資金(仟元)"
        ),
        None,
    )
    if row is None:
        raise RuntimeError(f"TPEx {expected_day} 缺少融資金額")
    return amount_100m(row[6], "100000")


def fetch_tpex_day(day: str) -> float:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            session = getattr(_thread_state, "session", None)
            if session is None:
                session = requests.Session()
                session.mount("https://www.tpex.org.tw", TpexCertificateAdapter())
                session.headers.update(
                    {
                        "Accept": "application/json",
                        "User-Agent": "tw-margin-rate/1.0",
                    }
                )
                _thread_state.session = session
            response = session.post(
                TPEX_URL,
                data={"date": day.replace("-", "/"), "response": "json"},
                timeout=(15, 45),
            )
            response.raise_for_status()
            payload = response.json()
            return parse_tpex_payload(payload, day)
        except Exception as exc:  # network and malformed upstream payload
            last_error = exc
            if attempt < 2:
                session = getattr(_thread_state, "session", None)
                if session is not None:
                    session.close()
                    _thread_state.session = None
                status = getattr(getattr(exc, "response", None), "status_code", None)
                time.sleep(60 if status == 429 else 3 * (attempt + 1))
    raise RuntimeError(f"TPEx {day} 查詢失敗：{last_error}") from last_error


def load_existing(path: Path) -> dict[str, dict[str, float]]:
    if not path.exists():
        return {"twse": {}, "tpex": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, dict[str, float]] = {"twse": {}, "tpex": {}}
    for market in result:
        for row in payload.get("markets", {}).get(market, []):
            result[market][str(row["date"])] = float(row["financed_amount"])
    return result


def write_output(
    output: Path,
    values: dict[str, dict[str, float]],
    *,
    complete: bool,
) -> None:
    common = sorted(set(values["twse"]) & set(values["tpex"]))
    payload = {
        "metadata": {
            "generated_at": datetime.now().astimezone().isoformat(),
            "unit": "TWD 100 million",
            "complete": complete,
            "twse_source": "FinMind TaiwanStockTotalMarginPurchaseShortSale / MarginPurchaseMoney",
            "tpex_source": "TPEx /www/zh-tw/margin/balance / 融資金(仟元)",
            "start": common[0] if common else None,
            "end": common[-1] if common else None,
        },
        "markets": {
            market: [
                {"date": day, "financed_amount": values[market][day]}
                for day in sorted(values[market])
            ]
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
    if args.workers < 1 or args.workers > 16:
        raise ValueError("--workers 必須介於 1 與 16")
    if args.delay < 0:
        raise ValueError("--delay 不得小於 0")
    output = args.output.resolve()
    existing = load_existing(output)
    if output.exists():
        prior_metadata = json.loads(output.read_text(encoding="utf-8")).get(
            "metadata", {}
        )
        if prior_metadata.get("tpex_backfill_source"):
            print("偵測到非官方 TPEx 歷史回填，將以官方端點全部重抓", flush=True)
            existing["tpex"].clear()
    start_day = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_day = datetime.strptime(args.end, "%Y-%m-%d").date()
    if existing["twse"]:
        refresh_start = max(
            start_day,
            datetime.strptime(max(existing["twse"]), "%Y-%m-%d").date()
            - timedelta(days=args.refresh_days),
        )
    else:
        refresh_start = start_day

    load_dotenv(local_env_path())
    client = FinMindClient(
        token=os.environ.get("FINMIND_TOKEN", ""),
        cache_dir=PROJECT_ROOT / "data/cache",
    )
    finmind_rows = client.fetch(
        "TaiwanStockTotalMarginPurchaseShortSale",
        start_date=refresh_start.isoformat(),
        end_date=(end_day + timedelta(days=1)).isoformat(),
        force=True,
    )
    refreshed_twse = parse_twse_rows(finmind_rows)
    refreshed_days = sorted(refreshed_twse)
    if not refreshed_days or refreshed_days[-1] < args.end:
        print(
            f"TWSE 融資金額最新 {refreshed_days[-1] if refreshed_days else 'none'}，"
            f"要求截止 {args.end}",
            flush=True,
        )

    existing["twse"].update(refreshed_twse)
    refresh_text = refresh_start.isoformat()
    refresh_days = {day for day in existing["twse"] if day >= refresh_text}
    expected_days = sorted(
        day
        for day in existing["twse"]
        if day not in existing["tpex"] or day in refresh_days
    )
    refreshed_tpex: dict[str, float] = {}
    print(f"下載 TPEx 官方融資金額：{len(expected_days)} 個交易日", flush=True)
    for offset in range(0, len(expected_days), 100):
        batch = expected_days[offset : offset + 100]
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            pending = {
                executor.submit(fetch_tpex_day, day): day for day in batch
            }
            for future in as_completed(pending):
                day = pending[future]
                refreshed_tpex[day] = future.result()
        completed = offset + len(batch)
        print(f"TPEx 官方資料 {completed}/{len(expected_days)}", flush=True)
        existing["tpex"].update(refreshed_tpex)
        write_output(output, existing, complete=False)
    existing["tpex"].update(refreshed_tpex)
    for market in existing:
        existing[market] = {
            day: value
            for day, value in existing[market].items()
            if args.start <= day <= args.end
        }
    if set(existing["twse"]) != set(existing["tpex"]):
        raise RuntimeError("TWSE 與 TPEx 融資金額日期不一致")

    write_output(output, existing, complete=True)
    print(
        f"融資金額資料完成：{len(existing['twse'])} 日，最新 {max(existing['twse'])}",
        flush=True,
    )


if __name__ == "__main__":
    main()
