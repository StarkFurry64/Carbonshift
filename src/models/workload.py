"""Workload forecaster: CPU-hours of batch work arriving per hour, 1–24 h ahead.

Trained on a synthetic 2021–2023 job stream (different seed), tested on the 2024 stream the
simulator uses. Same direct multi-horizon set-up as the carbon models (one XGBoost per horizon).
The scheduler uses it to keep headroom free in future hours for jobs that have not arrived yet.

    python -m src.models.workload
"""
from __future__ import annotations

import joblib
import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from src.data.jobs import generate_jobs, hourly_cpu_hours
from src.plotting import SERIES, apply_style, plt
from src.utils import ROOT, get_logger, load_config, project_path

log = get_logger(__name__)
MODEL_ORDER = ["persistence_24h", "seasonal_naive_168h", "seasonal_ma_4w", "xgboost"]
MODEL_LABEL = {
    "persistence_24h": "Persistence (same hour yesterday)",
    "seasonal_naive_168h": "Same hour last week",
    "seasonal_ma_4w": "Same hour & weekday, 4-week mean",
    "xgboost": "XGBoost (direct, per horizon)",
}


def series_for(start, end, cfg, seed) -> pd.Series:
    idx = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), freq="h", inclusive="left")
    return hourly_cpu_hours(generate_jobs(start, end, cfg, seed=seed), idx)


def history_and_test(cfg: dict) -> pd.Series:
    """2021–2023 training stream (seed + offset) followed by the 2024 simulator stream (seed)."""
    train = series_for("2021-01-01", cfg["sim"]["start"], cfg, cfg["seed"] + cfg["workload"]["train_seed_offset"])
    test = series_for(cfg["sim"]["start"], cfg["sim"]["end"], cfg, cfg["seed"])
    return pd.concat([train, test])


def features(y: pd.Series, h: int, cfg: dict) -> pd.DataFrame:
    X = pd.DataFrame(index=y.index)
    for k in cfg["workload_forecast"]["lags"]:
        X[f"lag{k}"] = y.shift(k - 1)
    X["roll_mean_24"] = y.rolling(24).mean()
    X["roll_mean_168"] = y.rolling(168).mean()
    local = (y.index + pd.Timedelta(hours=h)).tz_convert(cfg["timezone_display"])
    X["target_hour"] = local.hour
    X["target_dow"] = local.dayofweek
    X["same_hour_yday"] = y.shift(24 - h)
    X["same_hour_lastweek"] = y.shift(168 - h)
    X["same_hour_dow_ma4"] = sum(y.shift(168 * k - h) for k in range(1, 5)) / 4
    return X


def baselines(X: pd.DataFrame) -> dict[str, pd.Series]:
    return {"persistence_24h": X["same_hour_yday"], "seasonal_naive_168h": X["same_hour_lastweek"],
            "seasonal_ma_4w": X["same_hour_dow_ma4"]}


def forecast_matrix(y: pd.Series, models: dict, origins: pd.DatetimeIndex, cfg: dict) -> np.ndarray:
    """[len(origins), H] forecasts of CPU-hours arriving in hours origin+1 .. origin+H."""
    H = cfg["forecast"]["horizons"]
    out = np.empty((len(origins), H))
    for h in range(1, H + 1):
        X = features(y, h, cfg).loc[origins, models["features"]]
        out[:, h - 1] = np.clip(models[h].predict(X), 0, None)
    return out


def main():
    cfg = load_config()
    apply_style()
    y = history_and_test(cfg)
    test_start = pd.Timestamp(cfg["sim"]["start"], tz="UTC")
    rows, models = [], {}
    for h in range(1, cfg["forecast"]["horizons"] + 1):
        X = features(y, h, cfg)
        yt = y.shift(-h)
        t_time = y.index + pd.Timedelta(hours=h)
        ok = X.notna().all(axis=1).to_numpy() & yt.notna().to_numpy()
        tr, te = ok & (t_time < test_start), ok & (t_time >= test_start)
        m = XGBRegressor(**cfg["workload_forecast"]["xgboost"], random_state=cfg["seed"])
        m.fit(X[tr], yt[tr], verbose=False)
        models[h] = m
        preds = {"xgboost": np.clip(m.predict(X[te]), 0, None), **{k: v[te].to_numpy() for k, v in baselines(X).items()}}
        for name in MODEL_ORDER:
            err = preds[name] - yt[te].to_numpy()
            rows.append({"model": name, "horizon": h, "MAE": np.abs(err).mean(), "RMSE": np.sqrt((err**2).mean())})
    models["features"] = list(X.columns)
    joblib.dump(models, ROOT / cfg["paths"]["models"] / "workload_xgb.joblib")

    m = pd.DataFrame(rows)
    res = project_path(cfg["paths"]["results"])
    m.round(4).to_csv(res / "workload_metrics_per_horizon.csv", index=False)
    summ = m.groupby("model")[["MAE", "RMSE"]].mean().reindex(MODEL_ORDER)
    summ["mean_actual_cpu_h"] = y[y.index >= test_start].mean()
    summ.round(4).to_csv(res / "workload_metrics_summary.csv")

    fig, ax = plt.subplots(figsize=(8, 4))
    for name, color in zip(MODEL_ORDER, SERIES):
        s = m[m.model == name]
        ax.plot(s["horizon"], s["MAE"] * 60, color=color, marker="o", ms=3.5, label=MODEL_LABEL[name])
    ax.set_title("Workload forecast: test-set (2024) MAE by horizon")
    ax.set_xlabel("Forecast horizon (hours ahead)")
    ax.set_ylabel("MAE (CPU-minutes / hour)")
    ax.set_xticks([1, 6, 12, 18, 24])
    ax.set_ylim(bottom=0)
    ax.legend(loc="lower right")
    fig.savefig(res / "workload_error_vs_horizon.png")
    plt.close(fig)
    print((summ * [60, 60, 60]).round(1).rename(columns=lambda c: c + " (CPU-min/h)").to_string())


if __name__ == "__main__":
    main()
