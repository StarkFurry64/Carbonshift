"""Failure injection: what happens when the forecasts are wrong.

- surprise_cloudy_days: actual carbon intensity +X% for N hours (e.g. a cloudy day removes the
  solar dip). The forecasters only see it once it has been observed, through their lag features.
- workload_burst: extra jobs arrive (multiplier x the normal rate) for N hours.
Both return the modified input plus the list of (start, end) event windows in UTC.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.data.jobs import generate_jobs


def _events(spec: dict, end, cfg: dict) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    tz = cfg["timezone_display"]
    day = pd.Timestamp(spec["first_day"], tz=tz)
    out = []
    while True:
        start = (day + pd.Timedelta(hours=spec["start_hour_ist"])).tz_convert("UTC")
        stop = start + pd.Timedelta(hours=spec["duration_h"])
        if stop > pd.Timestamp(end, tz="UTC") - pd.Timedelta(hours=cfg["sim"]["submit_cutoff_h"]):
            return out
        out.append((start, stop))
        day += pd.Timedelta(days=spec["every_days"])


def surprise_cloudy_days(carbon: pd.DataFrame, cfg: dict):
    """carbon: tidy table from load_carbon.load_all. Returns (modified copy, events)."""
    spec = cfg["inject"]["cloudy"]
    events = _events(spec, cfg["sim"]["end"], cfg)
    df = carbon.copy()
    hit = np.zeros(len(df), dtype=bool)
    for a, b in events:
        hit |= ((df["datetime"] >= a) & (df["datetime"] < b)).to_numpy()
    hit &= df["zone"].isin(spec["zones"]).to_numpy()
    df.loc[hit, "ci_lca"] *= 1 + spec["increase_pct"] / 100
    return df, events


def workload_burst(jobs: pd.DataFrame, cfg: dict):
    """Adds a second, independently seeded stream at (multiplier-1)x the normal rate inside the
    event windows, so the normal jobs are unchanged (paired comparison). Returns (jobs, events)."""
    spec = cfg["inject"]["burst"]
    start, end = cfg["sim"]["start"], cfg["sim"]["end"]
    events = _events(spec, end, cfg)
    idx = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), freq="h", inclusive="left")
    scale = np.zeros(len(idx))
    for a, b in events:
        scale[(idx >= a) & (idx < b)] = spec["multiplier"] - 1
    extra = generate_jobs(start, end, cfg, slack_h=jobs["slack_h"].iloc[0], seed=cfg["seed"] + 999,
                          rate_scale=scale, id_prefix="b")
    out = pd.concat([jobs, extra], ignore_index=True).sort_values("submitted_at", ignore_index=True)
    return out, events
