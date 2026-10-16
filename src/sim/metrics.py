"""Evaluation metrics for one simulation run.

Energy per region-hour (CCF linear power model, per vCPU, plus memory, times PUE):
    kWh = PUE * (VMs * (vCPUs * P_idle + P_mem) + (P_max - P_idle) * busy_vcpu_hours) / 1000
(each job is single-threaded, so it keeps one vCPU busy for its duration).
Carbon = kWh x ACTUAL carbon intensity of that region-hour (never the forecast).
A VM that runs any job in an hour is billed (compute + public IPv4) and powered for the whole hour.
Each job is attributed its region-hour's carbon in proportion to its CPU-seconds, so per-job
carbon sums to the total and can be filtered (e.g. jobs affected by an injected event).
"""
from __future__ import annotations

import numpy as np


def region_hour_kwh(vms: np.ndarray, busy_s: np.ndarray, cfg: dict) -> np.ndarray:
    e, vm = cfg["energy"], cfg["vm"]
    idle_w = e["min_watts_per_vcpu"] * vm["vcpus"] + e["memory_watts_per_gb"] * vm["memory_gb"]
    dyn_w = e["max_watts_per_vcpu"] - e["min_watts_per_vcpu"]
    return e["pue"] * (vms * idle_w + dyn_w * busy_s / 3600) / 1000


def job_carbon(out: dict, actual: np.ndarray, kwh: np.ndarray) -> np.ndarray:
    g = np.zeros(len(out["cpu"]))
    done = out["region"] >= 0
    r, h = out["region"][done], out["hour"][done]
    g[done] = kwh[r, h] * actual[r, h] * out["cpu"][done] / out["busy"][r, h]
    return g


def summarise(out: dict, actual: np.ndarray, cfg: dict, n_regions_provisioned: int,
              mask: np.ndarray | None = None) -> dict:
    """out: simulator result. mask selects a subset of jobs for the job-level metrics
    (carbon is then the attributed carbon of those jobs)."""
    vm = cfg["vm"]
    kwh = region_hour_kwh(out["vms"], out["busy"], cfg)
    jc = job_carbon(out, actual, kwh)
    m = np.ones(len(jc), dtype=bool) if mask is None else mask
    done = out["region"] >= 0
    finish = np.where(done, out["finish"], np.inf)
    wait = (out["start"] - out["submit"])[m & done]
    hours = out["vms"].shape[1]
    vm_hours = out["vms"].sum()
    res = {
        "jobs": int(m.sum()),
        "carbon_kg": jc[m].sum() / 1000,
        "deadline_miss_pct": 100 * np.mean(finish[m] > out["deadline"][m] + 1e-9),
        "unfinished_jobs": int((m & ~done).sum()),
        "avg_wait_h": wait.mean(),
        "p95_wait_h": np.percentile(wait, 95),
        "avg_turnaround_h": (out["finish"] - out["submit"])[m & done].mean(),
    }
    if mask is None:
        disk = n_regions_provisioned * vm["max_per_region"] * vm["disk_price_per_month_usd"] * hours / 730
        vm_hour_price = vm["price_per_hour_usd"] + vm.get("ipv4_price_per_hour_usd", 0.0)
        res.update({
            "carbon_kg": (kwh * np.nan_to_num(actual)).sum() / 1000,
            "energy_kwh": kwh.sum(),
            "vm_hours": vm_hours,
            "idle_vm_hours": vm_hours - out["busy"].sum() / 3600,
            "avg_util_pct": 100 * out["busy"].sum() / max(vm_hours * 3600, 1),
            "cost_usd": vm_hours * vm_hour_price + disk,
            "vm_cost_usd": vm_hours * vm_hour_price,
            "share_in_IN-SO_pct": 100 * np.mean(out["region"][done] == 1),
        })
    return res
