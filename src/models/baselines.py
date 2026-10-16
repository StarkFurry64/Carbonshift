"""Baseline carbon forecasts for origin T, horizon h (all aligned to origin T)."""
from __future__ import annotations

import pandas as pd


def persistence_24h(y: pd.Series, h: int) -> pd.Series:
    """Same hour yesterday: y_{T+h-24}."""
    return y.shift(24 - h)


def moving_average_24h(y: pd.Series, h: int) -> pd.Series:
    """Flat mean of the last 24 observed hours (same value for every horizon)."""
    return y.rolling(24).mean()


def seasonal_ma_7d(y: pd.Series, h: int) -> pd.Series:
    """Mean of the same hour-of-day over the last 7 days."""
    return sum(y.shift(24 * k - h) for k in range(1, 8)) / 7


BASELINES = {
    "persistence_24h": persistence_24h,
    "moving_avg_24h": moving_average_24h,
    "seasonal_ma_7d": seasonal_ma_7d,
}
