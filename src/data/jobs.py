"""Synthetic batch-job stream: Poisson arrivals with an IST daily/weekly pattern, log-normal sizes.

Random draws are made in a fixed order (counts, arrival offsets, sizes) and slack is applied
afterwards, so the same seed gives the *same* jobs for every slack value (paired comparison).

    python -m src.data.jobs            # writes data/processed/jobs_2024.csv + results/jobs_arrival_profile.png
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.utils import get_logger, load_config, project_path

log = get_logger(__name__)


def rate_profile(index: pd.DatetimeIndex, cfg: dict) -> np.ndarray:
    """Expected arrivals per hour for each UTC hour in `index`."""
    w = cfg["workload"]
    local = index.tz_convert(cfg["timezone_display"])
    h = local.hour.to_numpy()
    f = np.full(len(index), w["night_factor"], dtype=float)
    for a, b in w["shoulder_hours_ist"]:
        f[(h >= a) & (h < b)] = w["shoulder_factor"]
    a, b = w["office_hours_ist"]
    f[(h >= a) & (h < b)] = w["office_factor"]
    f[local.dayofweek.to_numpy() >= 5] *= w["weekend_factor"]
    # normalise so that the average over a full week equals 1
    week = pd.date_range("2024-01-01", periods=168, freq="h", tz=cfg["timezone_display"])
    wh = week.hour.to_numpy()
    fw = np.full(168, w["night_factor"], dtype=float)
    for a2, b2 in w["shoulder_hours_ist"]:
        fw[(wh >= a2) & (wh < b2)] = w["shoulder_factor"]
    fw[(wh >= a) & (wh < b)] = w["office_factor"]
    fw[week.dayofweek.to_numpy() >= 5] *= w["weekend_factor"]
    return w["mean_jobs_per_hour"] * f / fw.mean()


def _utc(x) -> pd.Timestamp:
    ts = pd.Timestamp(x)
    return ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")


def generate_jobs(start, end, cfg: dict, slack_h: float | None = None, seed: int | None = None,
                  rate_scale: np.ndarray | None = None, id_prefix: str = "j") -> pd.DataFrame:
    """Jobs submitted in [start, end). rate_scale (one value per hour) multiplies the arrival rate."""
    w = cfg["workload"]
    rng = np.random.default_rng(cfg["seed"] if seed is None else seed)
    idx = pd.date_range(_utc(start), _utc(end), freq="h", inclusive="left")
    lam = rate_profile(idx, cfg) * (1.0 if rate_scale is None else rate_scale)
    counts = rng.poisson(lam)
    hour = np.repeat(np.arange(len(idx)), counts)
    offset_h = rng.uniform(0, 1, len(hour))
    size_min = np.clip(rng.lognormal(np.log(w["size_median_min"]), w["size_sigma"], len(hour)),
                       w["size_min_min"], w["size_max_min"])
    slack = w["default_slack_h"] if slack_h is None else slack_h
    submitted = idx[hour] + pd.to_timedelta(offset_h, unit="h")
    return pd.DataFrame(
        {
            "job_id": [f"{id_prefix}{i:06d}" for i in range(len(hour))],
            "submitted_at": submitted,
            "cpu_seconds": np.round(size_min * 60).astype(int),
            "slack_h": float(slack),
            "deadline": submitted + pd.Timedelta(hours=slack),
        }
    )


def hourly_cpu_hours(jobs: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    """CPU-hours of work arriving in each hour of `index` (the workload forecaster's target)."""
    s = jobs.groupby(jobs["submitted_at"].dt.floor("h"))["cpu_seconds"].sum() / 3600
    return s.reindex(index, fill_value=0.0).rename("cpu_hours")


def main():
    from src.plotting import SERIES, apply_style, plt

    cfg = load_config()
    jobs = generate_jobs(cfg["sim"]["start"], cfg["sim"]["end"], cfg)
    out = project_path(f"{cfg['paths']['processed']}/jobs_2024.csv")
    jobs.to_csv(out, index=False)
    mins = jobs["cpu_seconds"] / 60
    log.info("%d jobs -> %s | %.2f jobs/h | size median %.1f, mean %.1f, max %.0f CPU-min | %.0f CPU-h total",
             len(jobs), out, len(jobs) / 8784, mins.median(), mins.mean(), mins.max(), mins.sum() / 60)

    apply_style()
    local = jobs["submitted_at"].dt.tz_convert(cfg["timezone_display"])
    wk = local.dt.dayofweek < 5
    n_wd, n_we = local[wk].dt.date.nunique(), local[~wk].dt.date.nunique()
    fig, ax = plt.subplots(figsize=(8, 3.8))
    for mask, n, label, color in [(wk, n_wd, "Weekday", SERIES[0]), (~wk, n_we, "Weekend", SERIES[1])]:
        load = (jobs.loc[mask, "cpu_seconds"] / 60).groupby(local[mask].dt.hour).sum() / n
        ax.plot(load.index, load.values, marker="o", ms=4, color=color, label=label)
    ax.axhline(cfg["vm"]["max_per_region"] * 60, color="#52514e", lw=1, ls="--")
    ax.text(23, cfg["vm"]["max_per_region"] * 60 - 3, "capacity of one group (2 VMs)", color="#52514e",
            fontsize=9, ha="right", va="top")
    ax.set_title("CPU-minutes of work arriving per hour (2024 average)")
    ax.set_xlabel("Hour of day (IST)")
    ax.set_ylabel("CPU-minutes / hour")
    ax.set_xticks(range(0, 24, 3))
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper left")
    fig.savefig(project_path(cfg["paths"]["results"]) / "jobs_arrival_profile.png")
    plt.close(fig)


if __name__ == "__main__":
    main()
