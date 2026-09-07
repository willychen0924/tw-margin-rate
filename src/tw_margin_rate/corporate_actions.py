"""Explicit, reviewed share-unit changes; never infer a split from price moves."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from tw_margin_rate.calculation import is_ordinary_share


@dataclass(frozen=True)
class StockSplit:
    stock_id: str
    effective_date: str
    new_shares_per_old_share: float

    def __post_init__(self) -> None:
        if not isinstance(self.stock_id, str) or not is_ordinary_share(self.stock_id):
            raise ValueError("拆股代號必須是合格四位數普通股")
        if not isinstance(self.effective_date, str) or date.fromisoformat(self.effective_date).isoformat() != self.effective_date:
            raise ValueError("拆股日期必須為 YYYY-MM-DD")
        factor = self.new_shares_per_old_share
        if isinstance(factor, bool) or not isinstance(factor, (int, float)) or not math.isfinite(factor) or factor <= 1:
            raise ValueError("拆股比例必須是大於 1 的有限數；不適用減資／股票股利")


def load_stock_splits(path: Path) -> tuple[StockSplit, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("events"), list):
        raise ValueError("拆股參考檔格式錯誤")
    events = []
    for row in payload["events"]:
        if row.get("type") != "stock_split":
            raise ValueError("僅接受已查證的 stock_split，不可混用其他公司行動")
        sources = row.get("sources")
        if not isinstance(sources, list) or not sources or not all(
            isinstance(source, dict)
            and source.get("title")
            and urlparse(str(source.get("url", ""))).scheme == "https"
            and urlparse(str(source.get("url", ""))).netloc
            for source in sources
        ):
            raise ValueError("拆股事件必須附可追溯的公告來源")
        event = StockSplit(
            stock_id=row["stock_id"],
            effective_date=row["effective_date"],
            new_shares_per_old_share=row["new_shares_per_old_share"],
        )
        old_par, new_par = row.get("old_par_value"), row.get("new_par_value")
        if not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0
            for value in (old_par, new_par)
        ) or not math.isclose(old_par / new_par, event.new_shares_per_old_share, rel_tol=1e-12):
            raise ValueError("拆股比例與公告前後面額不一致")
        events.append(event)
    ordered = tuple(sorted(events, key=lambda event: (event.effective_date, event.stock_id)))
    if len({(event.stock_id, event.effective_date) for event in ordered}) != len(ordered):
        raise ValueError("拆股事件重複；不可重複調整成本")
    return ordered


class SplitCursor:
    """Convert existing state once, before the first processed day on/after a split.

    The archive's event-day balances are already in new-share units. Only stored
    per-share cost and carried price need conversion; never multiply raw lots.
    Apply even if that stock has no margin row on the effective date, so a later
    reappearance cannot use an old-unit cost/price. Newly initialized positions
    after the event must not be divided again.
    """

    def __init__(self, events: tuple[StockSplit, ...]) -> None:
        self.events = tuple(sorted(events, key=lambda event: (event.effective_date, event.stock_id)))
        if len({(event.stock_id, event.effective_date) for event in self.events}) != len(self.events):
            raise ValueError("拆股事件重複")
        self.next_event = 0
        self.last_day: str | None = None

    def advance(
        self, day: str, average_cost: dict[str, float], last_close: dict[str, float]
    ) -> None:
        if self.last_day is not None and day <= self.last_day:
            raise ValueError("成本狀態必須按不重複且遞增的交易日更新")
        self.last_day = day
        while self.next_event < len(self.events):
            event = self.events[self.next_event]
            if event.effective_date > day:
                break
            for state in (average_cost, last_close):
                if event.stock_id in state:
                    state[event.stock_id] /= event.new_shares_per_old_share
            self.next_event += 1
