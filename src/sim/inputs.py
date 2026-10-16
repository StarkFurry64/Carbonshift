"""Simulation inputs per scenario: actual carbon, forecast matrices, workload forecasts, jobs.

Forecasts are computed for every tick up front (vectorised); at tick t the scheduler only sees
forecasts made from data up to hour t-1, so this is equivalent to forecasting online.
"""
from __future__ import annotations

from dataclasses import dataclass

import joblib
import numpy as np
import pandas as pd

from src.data.features import zone_frame
from src.data.jobs import generate_jobs, hourly_cpu_hours
from src.data.load_carbon import load_all
from src.models import workload as wl_model
from src.models.predict import forecast_matrix, load_models
from src.scheduler.scheduler import H_AHEAD
from src.sim import inject
from src.utils import ROOT


@dataclass
class SimInputs:
    zones: list[str]
    hours: pd.DatetimeIndex
    actual: np.ndarray            # [R, T] g/kWh
    fc: dict                      # source -> [R, T, 24], hours t..t+23 as seen at tick t
    wl: dict                      # source -> [T, 24] CPU-seconds arriving in hours t..t+23
    mae: np.ndarray               # [R, 24] XGBoost test MAE by horizon (confidence gate)
    jobs: pd.DataFrame            # job_id, submitted_at, cpu_seconds (slack applied per run)
    events: list


def _ahead(a: np.ndarray, K: int) -> np.ndarray:
    """[.., T] -> [.., T, K] with out[.., t, k] = a[.., t+k] (NaN past the end)."""
    T = a.shape[-1]
    padded = np.concatenate([a, np.full(a.shape[:-1] + (K,), np.nan)], axis=-1)
    return np.stack([padded[..., k:k + T] for k in range(K)], axis=-1)


def build_inputs(cfg: dict, scenario: str) -> SimInputs:
    hours = pd.date_range(pd.Timestamp(cfg["sim"]["start"], tz="UTC"), pd.Timestamp(cfg["sim"]["end"], tz="UTC"),
                          freq="h", inclusive="left")
    origins = hours - pd.Timedelta(hours=1)          # tick t uses data up to hour t-1
    zones = [r["zone"] for r in cfg["regions"]]
    events = []

    carbon = load_all(cfg)
    if scenario == "cloudy":
        carbon, events = inject.surprise_cloudy_days(carbon, cfg)
    actual, f_xgb, f_per = [], [], []
    for z in zones:
        g = zone_frame(carbon, z)
        actual.append(g["ci_lca"].reindex(hours).to_numpy())
        f_xgb.append(forecast_matrix(g, load_models(z, cfg), origins, cfg))
        f_per.append(forecast_matrix(g, None, origins, cfg))
    actual = np.array(actual)
    fc = {"xgboost": np.array(f_xgb), "persistence": np.array(f_per), "oracle": _ahead(actual, H_AHEAD)}

    cutoff = hours[-1] + pd.Timedelta(hours=1) - pd.Timedelta(hours=cfg["sim"]["submit_cutoff_h"])
    jobs = generate_jobs(cfg["sim"]["start"], cfg["sim"]["end"], cfg)
    if scenario == "burst":
        jobs, events = inject.workload_burst(jobs, cfg)
    jobs = jobs[jobs["submitted_at"] < cutoff].reset_index(drop=True)

    y = wl_model.history_and_test(cfg)
    arrivals = hourly_cpu_hours(jobs, hours)
    y.loc[hours] = arrivals.to_numpy()                # forecaster sees what actually arrives
    wl_models = joblib.load(ROOT / cfg["paths"]["models"] / "workload_xgb.joblib")
    wl = {"xgboost": wl_model.forecast_matrix(y, wl_models, origins, cfg) * 3600,
          "oracle": np.nan_to_num(_ahead(arrivals.to_numpy() * 3600, H_AHEAD))}

    m = pd.read_csv(ROOT / cfg["paths"]["results"] / "carbon_metrics_per_horizon.csv")
    m = m[m.model == "xgboost"]
    mae = np.array([m[m.zone == z].sort_values("horizon")["MAE"].to_numpy() for z in zones])
    return SimInputs(zones, hours, actual, fc, wl, mae, jobs[["job_id", "submitted_at", "cpu_seconds"]], events)
