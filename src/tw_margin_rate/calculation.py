"""Pure calculation helpers shared by the builder and unit tests."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable


def is_ordinary_share(stock_id: object) -> bool:
    """Return whether a code belongs to the project's ordinary-share universe."""
    code = str(stock_id)
    return bool(re.fullmatch(r"\d{4}", code)) and not code.startswith(
        ("0", "91")
    )


def remaining_financed_balance(
    yesterday: float, sells: float, cash_repayments: float
) -> float:
    return max(float(yesterday) - float(sells) - float(cash_repayments), 0.0)


def updated_average_cost(
    *,
    old_cost: float | None,
    close: float,
    yesterday: float,
    buys: float,
    sells: float,
    cash_repayments: float,
    today: float,
) -> float | None:
    """Apply the official stock-level moving-average financing-cost rule."""
    today = float(today)
    if today <= 0:
        return None
    close = float(close)
    prior_cost = close if old_cost is None or not math.isfinite(old_cost) else old_cost
    remaining = remaining_financed_balance(yesterday, sells, cash_repayments)
    return (remaining * prior_cost + float(buys) * close) / today


def market_maintenance(rows: Iterable[tuple[float, float, float]]) -> float:
    """Aggregate (close, balance, average_cost) tuples into a market rate."""
    market_value = 0.0
    estimated_debt = 0.0
    for close, balance, average_cost in rows:
        market_value += float(close) * float(balance)
        estimated_debt += float(average_cost) * float(balance) * 0.60
    if estimated_debt <= 0:
        raise ValueError("estimated margin debt must be positive")
    result = market_value / estimated_debt * 100.0
    if not math.isfinite(result):
        raise ValueError("maintenance result is not finite")
    return result


def market_for_stock(
    stock_id: str,
    day: str,
    current_market: dict[str, str],
    twse_listing_dates: dict[str, str],
    twse_delisting_dates: dict[str, str],
) -> str:
    """Resolve historical market from official TWSE listing intervals."""
    listing_date = twse_listing_dates.get(stock_id)
    delisting_date = twse_delisting_dates.get(stock_id)
    if listing_date and day < listing_date:
        return "tpex"
    if listing_date and (not delisting_date or day < delisting_date):
        return "twse"
    if delisting_date and day < delisting_date:
        return "twse"

    market = current_market.get(stock_id)
    if market == "twse":
        return "twse"
    if market == "tpex":
        return "tpex"
    if delisting_date:
        # A stale post-delisting FinMind row still belongs to its last known
        # TWSE market.  A genuine relisting is handled by current_market above.
        return "twse"
    return "tpex"
