"""Exploratory analysis of carbon intensity: long-term, diurnal, weekly, seasonal patterns.

Writes PNGs + eda_summary.csv to results/.

    python -m src.data.eda
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.data.load_carbon import load_all
from src.plotting import SEQ_CMAP, TEXT_2, ZONE_COLOR, apply_style, label_line_end, plt
from src.utils import get_logger, load_config, project_path

log = get_logger(__name__)
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
UNIT = "gCO₂eq/kWh (LCA)"


def _with_local_time(df: pd.DataFrame, tz: str) -> pd.DataFrame:
    df = df.copy()
    local = df["datetime"].dt.tz_convert(tz)
    df["hour"], df["dow"], df["month"], df["year"] = local.dt.hour, local.dt.dayofweek, local.dt.month, local.dt.year
    df["date"] = local.dt.floor("D").dt.tz_localize(None)
    return df


def plot_timeseries(df, out):
    fig, ax = plt.subplots(figsize=(11, 4))
    for zone, g in df.groupby("zone"):
        daily = g.groupby("date")["ci_lca"].mean()
        ax.plot(daily.index, daily.values, color=ZONE_COLOR[zone], lw=0.6, alpha=0.35)
        roll = daily.rolling(30, center=True).mean()
        ax.plot(roll.index, roll.values, color=ZONE_COLOR[zone], lw=2, label=f"{zone} (30-day mean)")
        last = roll.dropna()
        label_line_end(ax, last.index[-1], last.iloc[-1], zone, ZONE_COLOR[zone])
    ax.axvspan(pd.Timestamp("2024-01-01"), daily.index.max(), color="#efeeea", zorder=0)
    ax.text(pd.Timestamp("2024-01-10"), ax.get_ylim()[1] * 0.985, "test (2024)", color=TEXT_2, va="top", fontsize=9)
    ax.set_title("Daily mean carbon intensity, 2021–2024")
    ax.set_ylabel(UNIT)
    ax.legend(loc="lower left")
    fig.savefig(out / "eda_timeseries.png")
    plt.close(fig)


def plot_profile(df, key, ticks, title, fname, out):
    fig, ax = plt.subplots(figsize=(8, 4))
    for zone, g in df.groupby("zone"):
        s = g.groupby(key)["ci_lca"]
        mean, q25, q75 = s.mean(), s.quantile(0.25), s.quantile(0.75)
        ax.fill_between(mean.index, q25, q75, color=ZONE_COLOR[zone], alpha=0.15, lw=0)
        ax.plot(mean.index, mean.values, color=ZONE_COLOR[zone], marker="o", ms=4, label=f"{zone} mean (band = IQR)")
        label_line_end(ax, mean.index[-1], mean.iloc[-1], zone, ZONE_COLOR[zone])
    ax.set_xticks(list(range(len(ticks))) if key != "month" else list(range(1, 13)))
    ax.set_xticklabels(ticks)
    ax.set_title(title)
    ax.set_ylabel(UNIT)
    ax.legend(loc="lower left")
    fig.savefig(out / fname)
    plt.close(fig)


def plot_heatmaps(df, out):
    zones = sorted(df["zone"].unique(), reverse=True)  # IN-WE, IN-SO
    fig, axes = plt.subplots(1, len(zones), figsize=(12, 4.2), sharey=True)
    vmin, vmax = df["ci_lca"].quantile([0.02, 0.98])
    for ax, zone in zip(np.atleast_1d(axes), zones):
        piv = df[df.zone == zone].pivot_table(index="hour", columns="month", values="ci_lca", aggfunc="mean")
        im = ax.imshow(piv.values, aspect="auto", origin="lower", cmap=SEQ_CMAP, vmin=vmin, vmax=vmax)
        ax.set_title(f"{zone}: mean by hour × month")
        ax.set_xticks(range(12))
        ax.set_xticklabels([m[0] for m in MONTHS])
        ax.set_yticks(range(0, 24, 3))
        ax.grid(False)
    np.atleast_1d(axes)[0].set_ylabel("Hour of day (IST)")
    cb = fig.colorbar(im, ax=axes, shrink=0.9)
    cb.set_label(UNIT, color=TEXT_2)
    fig.savefig(out / "eda_hour_month_heatmap.png")
    plt.close(fig)


def plot_acf(df, out, max_lag=200):
    fig, ax = plt.subplots(figsize=(8, 3.8))
    for zone, g in df.groupby("zone"):
        x = g.sort_values("datetime")["ci_lca"].to_numpy()
        x = x - x.mean()
        acf = [1.0] + [np.corrcoef(x[:-k], x[k:])[0, 1] for k in range(1, max_lag + 1)]
        ax.plot(range(max_lag + 1), acf, color=ZONE_COLOR[zone], label=zone)
    for lag in (24, 168):
        ax.axvline(lag, color=TEXT_2, lw=0.8, ls=":")
        ax.text(lag + 2, 0.02, f"{lag} h", color=TEXT_2, fontsize=9)
    ax.set_title("Autocorrelation of hourly carbon intensity")
    ax.set_xlabel("Lag (hours)")
    ax.set_ylabel("ACF")
    ax.set_ylim(0, 1.02)
    ax.legend()
    fig.savefig(out / "eda_autocorrelation.png")
    plt.close(fig)


def summary(df) -> pd.DataFrame:
    rows = []
    for zone, g in df.groupby("zone"):
        hourly = g.groupby("hour")["ci_lca"].mean()
        monthly = g.groupby("month")["ci_lca"].mean()
        daily_range = g.groupby("date")["ci_lca"].agg(lambda s: s.max() - s.min())
        rows.append(
            {
                "zone": zone,
                "mean": g["ci_lca"].mean(),
                "std": g["ci_lca"].std(),
                "min": g["ci_lca"].min(),
                "max": g["ci_lca"].max(),
                "cleanest_hour_ist": int(hourly.idxmin()),
                "dirtiest_hour_ist": int(hourly.idxmax()),
                "avg_diurnal_profile_range": hourly.max() - hourly.min(),
                "median_within_day_range": daily_range.median(),
                "cleanest_month": MONTHS[monthly.idxmin() - 1],
                "dirtiest_month": MONTHS[monthly.idxmax() - 1],
                "weekday_mean": g.loc[g.dow < 5, "ci_lca"].mean(),
                "weekend_mean": g.loc[g.dow >= 5, "ci_lca"].mean(),
                "pct_estimated": 100 * g["estimated"].astype(str).str.lower().eq("true").mean(),
            }
        )
    return pd.DataFrame(rows).round(1)


def main():
    cfg = load_config()
    apply_style()
    out = project_path(cfg["paths"]["results"])
    df = _with_local_time(load_all(cfg), cfg["timezone_display"])
    plot_timeseries(df, out)
    plot_profile(df, "hour", [str(h) if h % 3 == 0 else "" for h in range(24)],
                 "Diurnal profile (IST hour of day)", "eda_hourly_profile.png", out)
    plot_profile(df, "dow", DOW, "Weekly profile (IST day of week)", "eda_weekly_profile.png", out)
    plot_profile(df, "month", MONTHS, "Seasonal profile (month)", "eda_monthly_profile.png", out)
    plot_heatmaps(df, out)
    plot_acf(df, out)
    s = summary(df)
    s.to_csv(out / "eda_summary.csv", index=False)
    log.info("EDA charts written to %s", out)
    print(s.T.to_string())


if __name__ == "__main__":
    main()
