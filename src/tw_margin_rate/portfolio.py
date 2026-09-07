"""Daily vectorized inventory update used by the full-history builder."""

from __future__ import annotations

import numpy as np
import pandas as pd


def update_portfolio(
    margin: pd.DataFrame,
    average_cost: dict[str, float],
    last_close: dict[str, float],
) -> pd.DataFrame:
    """Resolve valid same-stock prices and update cost, without changing raw input.

    State must already be in this day's share units (see SplitCursor). A zero,
    negative or non-finite close is missing, not a free stock. Clear liquidated
    cost even if no valid price has ever been observed.
    """
    margin = margin.copy()
    margin["stock_id"] = margin["stock_id"].astype(str)
    for stock_id in margin.loc[margin["MarginPurchaseTodayBalance"] <= 0, "stock_id"]:
        average_cost.pop(stock_id, None)

    close = pd.to_numeric(margin["close"], errors="coerce").astype(float)
    valid = np.isfinite(close) & (close > 0)
    close = close.where(valid, margin["stock_id"].map(last_close))
    valid = np.isfinite(close) & (close > 0)
    margin["close"] = close
    margin = margin[valid].copy()

    stock_ids = margin["stock_id"]
    close = margin["close"].to_numpy()
    yesterday = margin["MarginPurchaseYesterdayBalance"].astype(float).to_numpy()
    buys = margin["MarginPurchaseBuy"].astype(float).to_numpy()
    sells = margin["MarginPurchaseSell"].astype(float).to_numpy()
    repayments = margin["MarginPurchaseCashRepayment"].astype(float).to_numpy()
    today = margin["MarginPurchaseTodayBalance"].astype(float).to_numpy()
    remaining = np.maximum(yesterday - sells - repayments, 0.0)

    old_cost = stock_ids.map(average_cost).astype(float).to_numpy()
    old_cost = np.where(np.isfinite(old_cost), old_cost, close)
    numerator = remaining * old_cost + buys * close
    new_cost = np.divide(numerator, today, out=close.copy(), where=today > 0)

    for stock_id, stock_close, balance, cost in zip(stock_ids, close, today, new_cost):
        last_close[stock_id] = float(stock_close)
        if balance > 0:
            average_cost[stock_id] = float(cost)
    margin["average_cost"] = new_cost
    margin["market_value"] = close * today
    margin["estimated_debt"] = new_cost * today * 0.60
    return margin
