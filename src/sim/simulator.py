"""Discrete-time (hourly) simulator over the 2024 test year: 2 worker groups, max 2 t3.micro VMs each.

Each tick t (start of a UTC hour):
  1. jobs submitted during hour t-1 become visible;
  2. the policy picks which visible jobs start in hour t and where;
  3. jobs are packed first-fit onto the region's VMs (a job runs on one vCPU, <= 30 min, so it
     finishes inside the hour); jobs that do not fit stay pending for the next tick.
Carbon/energy/cost are computed from the realised VM-hours and the ACTUAL carbon intensity.

Experiments (all on the same seeded job stream):
  sweep    : main policies x slack 2/6/12/24 h, no injection
  failure  : CarbonShift ablations under none / surprise cloudy days / workload bursts

    python -m src.sim.simulator                # everything
    python -m src.sim.simulator --only sweep   # or: failure
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ProcessPoolExecutor

import joblib
import numpy as np
import pandas as pd

from src.scheduler.policies import FAILURE_POLICIES, MAIN_POLICIES, build_policies
from src.scheduler.scheduler import Planner
from src.sim.inputs import SimInputs, build_inputs
from src.sim.metrics import summarise
from src.utils import get_logger, load_config, project_path

log = get_logger(__name__)
SCENARIOS = ["none", "cloudy", "burst"]


def simulate(policy, inp: SimInputs, slack_h: float, cfg: dict) -> dict:
    T, R = len(inp.hours), len(inp.zones)
    t0 = inp.hours[0]
    submit = ((inp.jobs["submitted_at"] - t0) / pd.Timedelta(hours=1)).to_numpy()
    cpu = inp.jobs["cpu_seconds"].to_numpy().astype(float)
    deadline = submit + slack_h
    n = len(submit)
    planner = Planner(policy, inp.fc[policy.forecast] if policy.kind == "plan" else inp.fc["xgboost"],
                      inp.actual, inp.wl.get(policy.workload) if policy.workload else None,
                      inp.mae, cpu, deadline, cfg)
    n_vm = cfg["vm"]["max_per_region"]
    start, finish = np.full(n, np.nan), np.full(n, np.nan)
    region, hour = np.full(n, -1), np.full(n, -1)
    vms, busy = np.zeros((R, T)), np.zeros((R, T))
    pending, nxt = [], 0
    for t in range(T):
        while nxt < n and submit[nxt] < t:
            pending.append(nxt)
            nxt += 1
        if not pending:
            continue
        used = np.zeros((R, n_vm))
        started = set()
        for j, r in planner.decide(t, pending):
            for v in range(n_vm):                                   # first fit
                if used[r, v] + cpu[j] <= 3600:
                    start[j] = t + used[r, v] / 3600
                    used[r, v] += cpu[j]
                    finish[j] = t + used[r, v] / 3600
                    region[j], hour[j] = r, t
                    started.add(j)
                    break
        vms[:, t] = (used > 0).sum(axis=1)
        busy[:, t] = used.sum(axis=1)
        if started:
            pending = [j for j in pending if j not in started]
    return {"submit": submit, "deadline": deadline, "cpu": cpu, "start": start, "finish": finish,
            "region": region, "hour": hour, "vms": vms, "busy": busy}


# ---------------------------------------------------------------- experiment runner
_INPUTS: dict[str, SimInputs] = {}


def _cache_path(cfg, scenario):
    return project_path(f"{cfg['paths']['processed']}/sim_inputs_{scenario}.joblib")


def _get_inputs(cfg, scenario) -> SimInputs:
    if scenario not in _INPUTS:
        _INPUTS[scenario] = joblib.load(_cache_path(cfg, scenario))
    return _INPUTS[scenario]


def affected_mask(inp: SimInputs, slack_h: float) -> np.ndarray | None:
    """Jobs whose [submit, deadline] window overlaps an injected event."""
    if not inp.events:
        return None
    s = inp.jobs["submitted_at"]
    d = s + pd.Timedelta(hours=slack_h)
    m = np.zeros(len(s), dtype=bool)
    for a, b in inp.events:
        m |= ((s < b) & (d > a)).to_numpy()
    return m


def run_task(task: tuple[str, str, float]) -> dict:
    scenario, name, slack = task
    cfg = load_config()
    inp = _get_inputs(cfg, scenario)
    pol = build_policies(cfg)[name]
    t0 = time.time()
    out = simulate(pol, inp, slack, cfg)
    row = {"scenario": scenario, "policy": name, "label": pol.label, "slack_h": slack,
           **summarise(out, inp.actual, cfg, len(pol.regions))}
    mask = affected_mask(inp, slack)
    if mask is not None:
        row.update({f"evt_{k}": v for k, v in summarise(out, inp.actual, cfg, len(pol.regions), mask).items()})
    if scenario == "none" and slack == cfg["workload"]["default_slack_h"] and name in ("fifo", "carbonshift", "oracle"):
        np.savez_compressed(project_path(f"{cfg['paths']['processed']}/sim_hourly_{name}.npz"),
                            vms=out["vms"], busy=out["busy"])
    row["runtime_s"] = time.time() - t0
    return row


def add_relative(df: pd.DataFrame) -> pd.DataFrame:
    """carbon saved vs FIFO and regret vs Oracle, within the same scenario and slack."""
    key = ["scenario", "slack_h"]
    idx = df.set_index(key).index
    for prefix in ("", "evt_"):
        c = f"{prefix}carbon_kg"
        if c not in df:
            continue
        fifo = idx.map(df[df.policy == "fifo"].set_index(key)[c]).to_numpy(dtype=float)
        oracle = idx.map(df[df.policy == "oracle"].set_index(key)[c]).to_numpy(dtype=float)
        df[f"{prefix}carbon_saved_vs_fifo_pct"] = 100 * (fifo - df[c]) / fifo
        df[f"{prefix}regret_vs_oracle_pct"] = 100 * (df[c] - oracle) / oracle
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["sweep", "failure"])
    args = ap.parse_args()
    cfg = load_config()
    res = project_path(cfg["paths"]["results"])
    tasks = []
    if args.only in (None, "sweep"):
        tasks += [("none", p, s) for s in cfg["workload"]["slack_values_h"] for p in MAIN_POLICIES]
    if args.only in (None, "failure"):
        tasks += [(sc, p, s) for s in cfg["inject"]["slack_values_h"] for sc in SCENARIOS for p in FAILURE_POLICIES]
    tasks = list(dict.fromkeys(tasks))
    for sc in sorted({t[0] for t in tasks}):
        t0 = time.time()
        joblib.dump(build_inputs(cfg, sc), _cache_path(cfg, sc))
        log.info("built inputs for scenario=%s in %.0fs", sc, time.time() - t0)
    t0 = time.time()
    with ProcessPoolExecutor(cfg["sim"]["n_workers"]) as ex:
        rows = list(ex.map(run_task, tasks))
    log.info("%d simulations in %.0fs", len(rows), time.time() - t0)
    df = add_relative(pd.DataFrame(rows))

    if args.only in (None, "sweep"):
        sweep = df[(df.scenario == "none") & df.policy.isin(MAIN_POLICIES)]
        cols = ["policy", "label", "slack_h", "jobs", "carbon_kg", "carbon_saved_vs_fifo_pct", "regret_vs_oracle_pct",
                "energy_kwh", "cost_usd", "vm_cost_usd", "vm_hours", "idle_vm_hours", "avg_util_pct",
                "avg_wait_h", "p95_wait_h", "avg_turnaround_h", "deadline_miss_pct", "unfinished_jobs",
                "share_in_IN-SO_pct"]
        sweep[cols].round(3).to_csv(res / "policy_comparison.csv", index=False)
    if args.only in (None, "failure"):
        fail = df[df.policy.isin(FAILURE_POLICIES) & df.slack_h.isin(cfg["inject"]["slack_values_h"])]
        fail.drop(columns=["runtime_s"]).round(3).to_csv(res / "failure_injection.csv", index=False)
    from src.sim import plots
    plots.make_all(cfg)


if __name__ == "__main__":
    main()
