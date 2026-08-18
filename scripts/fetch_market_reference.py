#!/usr/bin/env python3
"""Atomically refresh the official TWSE market-history references."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPANY_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
DELISTED_URL = (
    "https://www.twse.com.tw/company/suspendListingCsvAndHtml"
    "?lang=zh&startYear=&type=html"
)
NEWLISTING_URL = "https://www.twse.com.tw/rwd/zh/company/newlisting?response=json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "data/reference"
    )
    return parser.parse_args()


def download(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "tw-margin-rate/1.0"})
    with urlopen(request, timeout=180) as response:
        return response.read()


def validate(
    company_payload: bytes,
    delisted_payload: bytes,
    newlisting_payload: bytes,
) -> None:
    companies = json.loads(company_payload.decode("utf-8-sig"))
    if not isinstance(companies, list) or len(companies) < 500:
        raise RuntimeError("TWSE 公司基本資料筆數異常")
    required = {"公司代號", "上市日期"}
    if not required.issubset(companies[0]):
        raise RuntimeError("TWSE 公司基本資料缺少必要欄位")

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as handle:
            handle.write(delisted_payload)
            temp_path = Path(handle.name)
        tables = pd.read_html(temp_path)
        if not tables or len(tables[0]) < 100 or tables[0].shape[1] < 3:
            raise RuntimeError("TWSE 終止上市公司表格格式或筆數異常")
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    newlisting = json.loads(newlisting_payload.decode("utf-8-sig"))
    fields = newlisting.get("fields", [])
    rows = newlisting.get("data", [])
    required_listing = {"公司代號", "股票上市買賣日期"}
    if (
        not isinstance(fields, list)
        or not required_listing.issubset(fields)
        or not isinstance(rows, list)
        or len(rows) < 500
    ):
        raise RuntimeError("TWSE 最近上市公司格式或筆數異常")


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_bytes(payload)
    os.replace(temp_path, path)


def main() -> None:
    args = parse_args()
    company_payload = download(COMPANY_URL)
    delisted_payload = download(DELISTED_URL)
    newlisting_payload = download(NEWLISTING_URL)
    validate(company_payload, delisted_payload, newlisting_payload)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(args.output_dir / "twse-company-info.latest.json", company_payload)
    atomic_write(args.output_dir / "twse-delisted.latest.html", delisted_payload)
    atomic_write(args.output_dir / "twse-newlisting.latest.json", newlisting_payload)
    manifest = {
        "fetched_at": datetime.now().astimezone().isoformat(),
        "files": {
            "twse-company-info.latest.json": {
                "source": COMPANY_URL,
                "sha256": hashlib.sha256(company_payload).hexdigest(),
                "bytes": len(company_payload),
            },
            "twse-delisted.latest.html": {
                "source": DELISTED_URL,
                "sha256": hashlib.sha256(delisted_payload).hexdigest(),
                "bytes": len(delisted_payload),
            },
            "twse-newlisting.latest.json": {
                "source": NEWLISTING_URL,
                "sha256": hashlib.sha256(newlisting_payload).hexdigest(),
                "bytes": len(newlisting_payload),
            },
        },
    }
    atomic_write(
        args.output_dir / "latest-manifest.json",
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"市場沿革更新失敗：{exc}", file=sys.stderr)
        raise
