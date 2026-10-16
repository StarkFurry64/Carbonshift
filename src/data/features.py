"""Feature construction for direct multi-horizon carbon forecasting.

A forecast is issued at origin T (after observing y_T) for targets T+h, h = 1..H.
Naming follows CLAUDE.md relative to the first forecast hour t = T+1:
lag k = y_{t-k} = y_{T+1-k}, so lag1 is the latest observation.

Origin features (shared by all horizons): lags, rolling mean/std, CFE %, origin calendar.
Horizon features (depend on h): calendar of the target hour, same hour yesterday / last week,
7-day same-hour mean. For h <= 24 all of these are known at T, so there is no leakage.
"""
from __future__ import annotations

import pandas as pd


def origin_features(g: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """g: one zone, hourly, indexed by UTC datetime, with columns ci_lca and cfe_pct."""
    f = cfg["forecast"]
    y = g["ci_lca"]
    X = pd.DataFrame(index=g.index)
    for k in f["lags"]:
        X[f"lag{k}"] = y.shift(k - 1)
    for w in f["rolling_windows"]:
        X[f"roll_mean_{w}"] = y.rolling(w).mean()
        X[f"roll_std_{w}"] = y.rolling(w).std()
    X["diff_1"] = y - y.shift(1)
    X["diff_24"] = y - y.shift(24)                              # level change vs same hour yesterday
    X["roll_mean_24_change"] = X["roll_mean_24"] - X["roll_mean_24"].shift(24)
    if f.get("use_cfe_feature"):
        X["cfe_lag1"] = g["cfe_pct"]
    local = g.index.tz_convert(cfg["timezone_display"])
    X["origin_hour"] = local.hour
    return X


def horizon_features(g: pd.DataFrame, X0: pd.DataFrame, h: int, cfg: dict) -> pd.DataFrame:
    y = g["ci_lca"]
    X = X0.copy()
    target_local = (g.index + pd.Timedelta(hours=h)).tz_convert(cfg["timezone_display"])
    X["target_hour"] = target_local.hour
    X["target_dow"] = target_local.dayofweek
    X["target_month"] = target_local.month
    X["same_hour_yday"] = y.shift(24 - h)
    X["same_hour_lastweek"] = y.shift(168 - h)
    X["same_hour_ma7"] = sum(y.shift(24 * k - h) for k in range(1, 8)) / 7
    X["lag1_minus_yday"] = X["lag1"] - X["same_hour_yday"]
    return X


def base_forecast(X: pd.DataFrame, mode: str) -> pd.Series:
    """Reference the model predicts a correction to (see forecast.target_mode in config)."""
    return {"residual_persistence": X["same_hour_yday"], "delta_lag1": X["lag1"], "level": 0.0 * X["lag1"]}[mode]


def target(g: pd.DataFrame, h: int) -> pd.Series:
    """Actual value at T+h, aligned to origin T."""
    return g["ci_lca"].shift(-h)


def zone_frame(df: pd.DataFrame, zone: str) -> pd.DataFrame:
    return df[df["zone"] == zone].set_index("datetime").sort_index()[["ci_lca", "cfe_pct"]]
