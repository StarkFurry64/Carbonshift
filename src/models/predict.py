"""Load trained carbon models and forecast the next 24 h for a zone.

    python -m src.models.predict --zone IN-WE --origin "2024-06-01 06:00"
"""
from __future__ import annotations

import argparse

import joblib
import numpy as np
import pandas as pd

from src.data.features import base_forecast, horizon_features, origin_features, zone_frame
from src.data.load_carbon import load_all
from src.utils import ROOT, get_logger, load_config

log = get_logger(__name__)


def load_models(zone: str, cfg: dict) -> dict:
    return joblib.load(ROOT / cfg["paths"]["models"] / f"carbon_xgb_{zone}.joblib")


def forecast(history: pd.DataFrame, models: dict, cfg: dict) -> pd.Series:
    """history: one zone, hourly, UTC-indexed (ci_lca, cfe_pct), ending at origin T (>= 168 h).
    Returns forecasts for T+1..T+H indexed by target time (UTC)."""
    g = history.iloc[-(24 * 9):]
    X0 = origin_features(g, cfg)
    T = g.index[-1]
    out = {}
    for h in range(1, cfg["forecast"]["horizons"] + 1):
        X = horizon_features(g, X0, h, cfg).iloc[[-1]][models["features"]]
        out[T + pd.Timedelta(hours=h)] = float(base_forecast(X, models["target_mode"]).iloc[0]
                                               + models[h].predict(X)[0])
    return pd.Series(out, name="ci_forecast")


def forecast_matrix(g: pd.DataFrame, models: dict | None, origins: pd.DatetimeIndex, cfg: dict) -> np.ndarray:
    """Vectorised forecasts for many origins at once: [len(origins), H], column h-1 = T+h.
    models=None gives the persistence (same hour yesterday) forecast."""
    H = cfg["forecast"]["horizons"]
    X0 = origin_features(g, cfg)
    out = np.empty((len(origins), H))
    for h in range(1, H + 1):
        X = horizon_features(g, X0, h, cfg).loc[origins]
        if models is None:
            out[:, h - 1] = X["same_hour_yday"].to_numpy()
        else:
            X = X[models["features"]]
            out[:, h - 1] = base_forecast(X, models["target_mode"]).to_numpy() + models[h].predict(X)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zone", default="IN-WE")
    ap.add_argument("--origin", default="2024-06-01 06:00", help="UTC time of the last observation")
    args = ap.parse_args()
    cfg = load_config()
    g = zone_frame(load_all(cfg), args.zone)
    origin = pd.Timestamp(args.origin, tz="UTC")
    fc = forecast(g.loc[:origin], load_models(args.zone, cfg), cfg)
    actual = g["ci_lca"].reindex(fc.index)
    res = pd.DataFrame({"forecast": fc, "actual": actual, "abs_err": (fc - actual).abs()}).round(1)
    res.index = res.index.tz_convert(cfg["timezone_display"])
    print(res.to_string())
    log.info("MAE over the 24 h: %.2f", res["abs_err"].mean())


if __name__ == "__main__":
    main()
