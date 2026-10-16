"""Phase 2 charts from results/policy_comparison.csv and results/failure_injection.csv."""
from __future__ import annotations

import matplotlib.dates
import numpy as np
import pandas as pd

from src.data.features import zone_frame
from src.data.load_carbon import load_all
from src.plotting import SERIES, SURFACE, TEXT, TEXT_2, ZONE_COLOR, apply_style, plt
from src.utils import ROOT, project_path

POLICY_COLOR = {"carbonshift": SERIES[0], "simple_carbon": SERIES[1], "oracle": SERIES[2], "cs_no_gate": SERIES[3]}
VARIANT_COLOR = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]   # fixed slot order 1–5
SHORT = {
    "fifo": "FIFO (IN-WE)", "fifo_in_so": "FIFO (IN-SO)", "simple_carbon": "Simple carbon rule",
    "carbonshift": "CarbonShift", "oracle": "Oracle",
    "cs_no_gate": "No gate", "cs_no_buffer": "No buffer", "cs_no_gate_no_buffer": "No gate, no buffer",
    "cs_adaptive_gate": "Adaptive gate",
}


def carbon_vs_delay(df: pd.DataFrame, out) -> None:
    fig, ax = plt.subplots(figsize=(8.5, 5))
    for p in ["simple_carbon", "cs_no_gate", "carbonshift", "oracle"]:
        s = df[df.policy == p].sort_values("slack_h")
        ax.plot(s["avg_wait_h"], s["carbon_saved_vs_fifo_pct"], color=POLICY_COLOR[p], marker="o", ms=7,
                mec=SURFACE, mew=1.5, ls="--" if p == "oracle" else "-",
                label="CarbonShift, no gate" if p == "cs_no_gate" else SHORT[p], zorder=3)
        if p == "carbonshift":
            for _, r in s.iterrows():
                ax.annotate(f"{r.slack_h:g} h slack", (r.avg_wait_h, r.carbon_saved_vs_fifo_pct), xytext=(10, -16),
                            textcoords="offset points", fontsize=8.5, color=TEXT_2,
                            bbox=dict(fc=SURFACE, ec="none", pad=1.5), zorder=4)
    for p, txt in [("fifo", "FIFO, IN-WE (baseline)"), ("fifo_in_so", "FIFO, IN-SO (region switch only)")]:
        r = df[df.policy == p].iloc[0]
        ax.plot(r.avg_wait_h, r.carbon_saved_vs_fifo_pct, "s", color=TEXT_2, ms=7, mec=SURFACE, mew=1.5, zorder=3)
        ax.annotate(txt, (r.avg_wait_h, r.carbon_saved_vs_fifo_pct), xytext=(8, 4), textcoords="offset points",
                    fontsize=8.5, color=TEXT)
    ax.set_title("Carbon saved vs average job delay (slack 2 / 6 / 12 / 24 h)")
    ax.set_xlabel("Average wait from submission to start (hours)")
    ax.set_ylabel("Carbon saved vs FIFO baseline (%)")
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=-2)
    ax.legend(loc="lower right")
    fig.savefig(out / "carbon_vs_delay.png")
    plt.close(fig)


def policy_bars(df: pd.DataFrame, slack: float, out) -> None:
    order = ["fifo", "fifo_in_so", "simple_carbon", "cs_no_gate", "carbonshift", "oracle"]
    s = df[df.slack_h == slack].set_index("policy").reindex(order)
    names = {**SHORT, "cs_no_gate": "CarbonShift, no gate"}
    panels = [("carbon_kg", "Carbon (kg CO₂eq)", "{:.1f}"), ("energy_kwh", "Energy (kWh)", "{:.1f}"),
              ("vm_hours", "VM-hours", "{:,.0f}"), ("avg_wait_h", "Average wait (h)", "{:.1f}")]
    fig, axes = plt.subplots(1, 4, figsize=(14, 3.6), sharey=True)
    y = np.arange(len(order))
    for ax, (col, title, fmt) in zip(axes, panels):
        ax.barh(y, s[col], color=SERIES[0], height=0.6)
        for yi, v in zip(y, s[col]):
            ax.text(v, yi, " " + fmt.format(v), va="center", fontsize=8.5, color=TEXT)
        ax.set_title(title, fontsize=10.5)
        ax.grid(axis="y", visible=False)
        ax.set_xlim(0, s[col].max() * 1.3)
    axes[0].set_yticks(y)
    axes[0].set_yticklabels([names[p] for p in order])
    axes[0].invert_yaxis()
    fig.suptitle(f"Policy comparison, full 2024 simulation, {slack:g} h deadline slack", x=0.01, ha="left",
                 fontsize=13, fontweight="semibold")
    fig.tight_layout()
    fig.savefig(out / "policy_comparison.png")
    plt.close(fig)


def failure_chart(df: pd.DataFrame, out) -> None:
    variants = ["carbonshift", "cs_no_gate", "cs_no_buffer", "cs_no_gate_no_buffer", "cs_adaptive_gate"]
    scen = [("none", "No injection"), ("cloudy", "Cloudy days"), ("burst", "Workload bursts")]
    panels = [("regret_vs_oracle_pct", "Extra carbon vs Oracle (%)"), ("deadline_miss_pct", "Deadline misses (%)"),
              ("avg_wait_h", "Average wait (h)")]
    slacks = sorted(df.slack_h.unique())
    fig, axes = plt.subplots(len(slacks), 3, figsize=(14, 3.6 * len(slacks)), squeeze=False)
    x = np.arange(len(scen))
    w = 0.16
    for row, slack in zip(axes, slacks):
        d = df[df.slack_h == slack]
        for ax, (col, title) in zip(row, panels):
            for i, v in enumerate(variants):
                vals = [d[(d.scenario == sc) & (d.policy == v)][col].iloc[0] for sc, _ in scen]
                ax.bar(x + (i - 2) * w, vals, width=w * 0.9, color=VARIANT_COLOR[i],
                       label="CarbonShift (gate + buffer)" if v == "carbonshift" else SHORT[v])
            ax.axhline(0, color=TEXT_2, lw=0.8)
            ax.set_xticks(x)
            ax.set_xticklabels([s[1] for s in scen])
            ax.set_title(f"{title}, {slack:g} h slack", fontsize=10.5)
            ax.grid(axis="x", visible=False)
    miss_max = max(ax.get_ylim()[1] for ax in axes[:, 1])
    for ax in axes[:, 1]:
        ax.set_ylim(0, miss_max)                                    # same scale in both rows
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=5, bbox_to_anchor=(0.5, -0.02))
    fig.suptitle("Failure injection: CarbonShift with and without the confidence gate and safety buffer",
                 x=0.01, ha="left", fontsize=13, fontweight="semibold")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(out / "failure_injection.png")
    plt.close(fig)


def timeline(cfg: dict, out) -> None:
    proc = ROOT / cfg["paths"]["processed"]
    runs = {p: np.load(proc / f"sim_hourly_{p}.npz") for p in ("fifo", "carbonshift")}
    hours = pd.date_range(pd.Timestamp(cfg["sim"]["start"], tz="UTC"), periods=runs["fifo"]["vms"].shape[1], freq="h")
    win = (hours >= pd.Timestamp("2024-03-04", tz="Asia/Kolkata")) & (hours < pd.Timestamp("2024-03-11", tz="Asia/Kolkata"))
    x = hours[win].tz_convert(cfg["timezone_display"]).tz_localize(None)
    zones = [r["zone"] for r in cfg["regions"]]
    carbon = load_all(cfg)
    fig, axes = plt.subplots(3, 1, figsize=(11, 7.5), sharex=True, gridspec_kw={"height_ratios": [1.2, 1, 1]})
    for z in zones:
        ci = zone_frame(carbon, z)["ci_lca"].reindex(hours[win])
        axes[0].plot(x, ci.to_numpy(), color=ZONE_COLOR[z], label=z)
    axes[0].set_ylabel("gCO₂eq/kWh")
    axes[0].set_title("Actual carbon intensity (IST)")
    axes[0].legend(loc="lower left", ncol=2)
    for ax, (p, title) in zip(axes[1:], [("fifo", "FIFO run-now: CPU-minutes run per hour"),
                                          ("carbonshift", "CarbonShift (12 h slack): CPU-minutes run per hour")]):
        bottom = np.zeros(win.sum())
        for r, z in enumerate(zones):
            v = runs[p]["busy"][r, win] / 60
            ax.bar(x, v, bottom=bottom, width=1 / 24 * 0.9, color=ZONE_COLOR[z], label=z, align="edge")
            bottom += v
        ax.axhline(cfg["vm"]["max_per_region"] * 60, color=TEXT_2, lw=0.8, ls="--")
        ax.set_ylabel("CPU-min")
        ax.set_title(title)
    axes[1].legend(loc="upper left", ncol=2)
    axes[2].xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%a %d %b"))
    fig.tight_layout()
    fig.savefig(out / "schedule_timeline_example.png")
    plt.close(fig)


def make_all(cfg: dict) -> None:
    apply_style()
    res = project_path(cfg["paths"]["results"])
    if (res / "policy_comparison.csv").exists():
        df = pd.read_csv(res / "policy_comparison.csv")
        carbon_vs_delay(df, res)
        policy_bars(df, cfg["workload"]["default_slack_h"], res)
    if (res / "failure_injection.csv").exists():
        failure_chart(pd.read_csv(res / "failure_injection.csv"), res)
    if (ROOT / cfg["paths"]["processed"] / "sim_hourly_fifo.npz").exists():
        timeline(cfg, res)


if __name__ == "__main__":
    from src.utils import load_config

    make_all(load_config())
