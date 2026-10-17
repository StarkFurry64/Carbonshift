"""Gather every number the report quotes into report/build/data.json, and draw the report-only
live-run comparison chart. Re-run after new live runs: the report is generated from this file."""
import json
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.utils import load_config  # noqa: E402

RES, LIVE, OUT = ROOT / "results", ROOT / "results" / "live", Path(__file__).parent
cfg = load_config()
d = {}

# ---- data / EDA
carbon = pd.read_csv(ROOT / "data/processed/carbon_hourly.csv", parse_dates=["datetime"])
d["data"] = {"rows_per_zone": int(carbon.groupby("zone").size().iloc[0]), "zones": sorted(carbon.zone.unique()),
             "cleaned_points": int(carbon["cleaned"].sum()),
             "pct_estimated": float(100 * carbon["estimated"].astype(str).str.lower().eq("true").mean())}
p = carbon.pivot(index="datetime", columns="zone", values="ci_lca")
y24 = p[p.index.year == 2024]
d["data"]["so_cleaner_pct_2024"] = float(100 * (y24["IN-SO"] < y24["IN-WE"]).mean())
d["data"]["avg_gap_2024"] = float((y24["IN-WE"] - y24["IN-SO"]).mean())
d["eda"] = pd.read_csv(RES / "eda_summary.csv").set_index("zone").to_dict(orient="index")

# ---- forecasting
m = pd.read_csv(RES / "carbon_metrics_per_horizon.csv")
s = pd.read_csv(RES / "carbon_metrics_summary.csv")
d["carbon_summary"] = s.round(3).to_dict(orient="records")
d["carbon_h"] = {f"{z}|{mod}|{h}": float(m[(m.zone == z) & (m.model == mod) & (m.horizon == h)]["MAE"].iloc[0])
                 for z in ("IN-WE", "IN-SO") for mod in m.model.unique() for h in (1, 6, 12, 24)}
w = pd.read_csv(RES / "workload_metrics_summary.csv")
d["workload"] = w.round(4).to_dict(orient="records")

# ---- simulation (Phase 2)
pc = pd.read_csv(RES / "policy_comparison.csv")
d["sim"] = pc.round(3).to_dict(orient="records")
fi = pd.read_csv(RES / "failure_injection.csv")
d["failure"] = fi.round(3).to_dict(orient="records")
d["jobs_2024"] = int(pd.read_csv(ROOT / "data/processed/jobs_2024.csv").shape[0])


# ---- live AWS runs (the three final runs; fall back to the newest finished runs)
def run_id_from_log(name):
    f = LIVE / name
    if f.exists():
        mt = re.search(r"run (\S+) \|", f.read_text())
        return mt.group(1) if mt else None


live = {}
for key, log in [("carbonshift", "final_1_carbonshift.log"), ("fifo", "final_2_fifo.log"), ("burst", "final_3_burst.log")]:
    rid = run_id_from_log(log)
    if rid and (LIVE / rid / "summary.json").exists():
        sm = json.loads((LIVE / rid / "summary.json").read_text())
        tl = pd.read_csv(LIVE / rid / "timeline.csv")
        jb = pd.read_csv(LIVE / rid / "jobs.csv")
        sm["share_in_so_pct"] = float(100 * (jb["group"] == "IN-SO").mean())
        sm["jobs_in_we"] = int((jb["group"] == "IN-WE").sum())
        sm["vm_ids"] = sorted(jb["instance_id"].dropna().unique().tolist())
        run_ticks = tl[tl.get("phase", "run") != "end"] if "phase" in tl else tl
        sm["peak_we_vms"] = int(run_ticks["vms_IN-WE"].max())
        sm["we_on_ticks"] = run_ticks.index[run_ticks["vms_IN-WE"] > 0].tolist()
        sm["midday_tick"] = int(run_ticks.index[run_ticks["time_ist"].str.contains("14:30")][0]) \
            if run_ticks["time_ist"].str.contains("14:30").any() else len(run_ticks) // 2
        live[key] = sm
d["live"] = live
_sr = run_id_from_log("final_1_carbonshift_stalled.log")
_sp = next((q / _sr / "summary.json" for q in (LIVE, LIVE / "archive") if _sr and (q / _sr / "summary.json").exists()), None)
d["stalled_run"] = json.loads(_sp.read_text()) if _sp else None
_fp = next((q / "carbonshift-none-20240305-20261017-190857/summary.json" for q in (LIVE, LIVE / "archive")
            if (q / "carbonshift-none-20240305-20261017-190857/summary.json").exists()), None)
d["first_live_run"] = json.loads(_fp.read_text()) if _fp else None

# ---- report-only chart: live runs, measured on AWS vs predicted by the simulator
if {"carbonshift", "fifo"} <= set(live):
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    labels = ["CarbonShift\n(5 Mar)", "FIFO\n(5 Mar)"] + (["CarbonShift\nburst (8 Mar)"] if "burst" in live else [])
    keys = ["carbonshift", "fifo"] + (["burst"] if "burst" in live else [])
    meas = [live[k]["carbon_g"] for k in keys]
    simv = [live[k][f"sim_{live[k]['policy']}_carbon_g"] for k in keys]
    x = np.arange(len(keys))
    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    b1 = ax.bar(x - 0.19, meas, 0.36, color="#2a78d6", label="measured on AWS")
    b2 = ax.bar(x + 0.19, simv, 0.36, color="#c9c8c3", label="Phase 2 simulator, same jobs")
    for bars in (b1, b2):
        for r in bars:
            ax.text(r.get_x() + r.get_width() / 2, r.get_height() + 2, f"{r.get_height():.0f} g", ha="center", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("g CO₂eq")
    ax.set_title("Live AWS runs: CO₂ measured vs simulated", loc="left", fontweight="bold")
    ax.legend(frameon=False, loc="upper left")
    ax.set_ylim(0, max(meas + simv) * 1.2)
    ax.grid(axis="y", color="#e4e3df")
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(OUT / "shots" / "live_comparison.png", dpi=220)
    plt.close(fig)

# ---- tests
d["tests"] = {"count": 14}
(OUT / "data.json").write_text(re.sub(r"\bNaN\b", "null", json.dumps(d, indent=1, default=float)))
print("live runs found:", list(live), "| wrote", OUT / "data.json")
