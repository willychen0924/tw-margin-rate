"""Reviewed OTC-to-TWSE transfers; never infer a transfer from row order."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

REFERENCE = Path(__file__).resolve().parents[2] / "data/reference/verified-market-transfers.json"


def load_market_transfers(path: Path = REFERENCE) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("events"), list):
        raise ValueError("轉板參考檔格式錯誤")
    result = {}
    for event in payload["events"]:
        code, day = event["stock_id"], event["effective_date"]
        if (not isinstance(code, str) or len(code) != 4 or not code.isdigit()
                or code.startswith(("0", "91")) or code in result):
            raise ValueError("轉板代號無效或重複")
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("轉板日期無效")
        if (event.get("from_market"), event.get("to_market")) != ("tpex", "twse"):
            raise ValueError("僅接受已查證櫃轉市事件")
        sources = event.get("sources", [])
        if len(sources) < 2 or not all(
            urlparse(s.get("url", "")).scheme == "https"
            and urlparse(s.get("url", "")).hostname in {"www.twse.com.tw", "openapi.twse.com.tw"}
            and s.get("record", {}).get("公司代號") == code for s in sources
        ):
            raise ValueError("轉板事件缺少官方來源紀錄")
        roc_day = f"{int(day[:4]) - 1911:03d}.{day[5:7]}.{day[8:10]}"
        records = [source["record"] for source in sources]
        if (not any(record.get("上市日期") == day.replace("-", "") for record in records)
                or not any(record.get("股票上市買賣日期") == roc_day
                           and record.get("備註") == "櫃轉市" for record in records)):
            raise ValueError("轉板日期與官方紀錄不一致")
        result[code] = day
    return result


def transfer_market(stock_id: str, day: str, transfers: dict[str, str]) -> str | None:
    start = transfers.get(stock_id)
    return None if start is None else ("tpex" if day < start else "twse")
