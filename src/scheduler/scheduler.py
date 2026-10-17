"""Rolling-horizon carbon-aware planner (CLAUDE.md "Scheduler").

Called once per simulated hour t with the jobs that are visible and not started. It re-plans
*all* of them against fresh forecasts for hours t .. t+23, but only commits the jobs placed in
hour t; everything else is re-planned next hour.

For each job, earliest deadline first:
  1. Urgent (no feasible hour left outside the safety buffer) -> run now in the cleanest region.
  2. Otherwise score every feasible (region, hour k) inside the deadline minus the buffer:
       score = alpha * forecast_CI * (job kWh + idle kWh of any extra VM it opens)
             + beta  * cost of any extra VM-hour + gamma * k
     Capacity per region-hour is max_per_region VMs; future hours keep headroom for
     forecast arrivals (workload forecast x workload_reserve_frac).
  3. Confidence gate: if the best option is a delay, keep it only if the predicted saving in
     g/kWh beats gate_k x MAE(h) (inflated when recent forecasts have been badly wrong);
     otherwise run now. (Optional adaptive variant: the margin grows when the last 3 h of
     forecasts were much worse than usual.)
"""
from __future__ import annotations

import math

import numpy as np

from src.scheduler.policies import Policy

H_AHEAD = 24


class Planner:
    def __init__(self, policy: Policy, fc: np.ndarray, actual: np.ndarray, wl: np.ndarray | None,
                 mae: np.ndarray, cpu_s: np.ndarray, deadline_h: np.ndarray, cfg: dict):
        """fc: [R, T, 24] carbon forecasts for hours t..t+23 made at tick t; actual: [R, T];
        wl: [T, 24] CPU-seconds forecast to arrive in hours t..t+23; mae: [R, 24] by horizon."""
        self.p = policy
        self.last_load: np.ndarray | None = None
        self.fc = np.where(np.isfinite(fc), fc, np.inf)
        self.wl = wl
        self.mae = mae
        self.cpu = cpu_s
        self.deadline = deadline_h
        s, e, vm = cfg["scheduler"], cfg["energy"], cfg["vm"]
        self.alpha, self.beta, self.gamma = s["alpha"], s["beta"], s["gamma"]
        self.gate_k, self.reserve_frac = s["gate_k"], s["workload_reserve_frac"]
        self.vm_s = 3600.0
        self.cap = vm["max_per_region"] * self.vm_s
        self.dyn_kwh_per_s = (e["max_watts_per_vcpu"] - e["min_watts_per_vcpu"]) * e["pue"] / 1000 / 3600
        self.idle_kwh_per_vmh = (e["min_watts_per_vcpu"] * vm["vcpus"] + e["memory_watts_per_gb"] * vm["memory_gb"]) * e["pue"] / 1000
        self.vm_price = vm["price_per_hour_usd"] + vm.get("ipv4_price_per_hour_usd", 0.0)
        R = fc.shape[0]
        self.blocked = np.ones(R, dtype=bool)
        self.blocked[list(policy.regions)] = False
        # gate inflation: mean |h=1 error| over the last 3 observed hours, relative to MAE(h=1)
        err1 = np.abs(np.nan_to_num(actual - fc[:, :, 0], nan=0.0))
        recent = np.zeros_like(err1)
        for lag in (1, 2, 3):
            recent[:, lag:] += err1[:, :-lag] / 3
        self.inflation = np.maximum(1.0, recent / mae[:, [0]]) if policy.gate_adaptive else np.ones_like(recent)

    def decide(self, t: int, pending: list[int]) -> list[tuple[int, int]]:
        """Returns [(job, region)] to start in hour t, in execution priority order."""
        if self.p.kind == "fifo":
            self.last_load = None
            return [(j, self.p.regions[0]) for j in pending]          # pending is in arrival order
        fc = self.fc[:, t, :]
        R, K = fc.shape
        load = np.zeros((R, K))
        reserve = np.zeros((R, K))
        if self.p.workload is not None and self.wl is not None:
            # arrivals in hour t+c become runnable at t+c+1
            reserve[:, 1:] = self.reserve_frac * self.wl[t, :-1] / len(self.p.regions)
        ks = np.arange(K)
        buf = self.p.safety_buffer_h
        run_now = []
        for j in sorted(pending, key=self.deadline.__getitem__):
            dur = self.cpu[j]
            k_max = math.floor(self.deadline[j] - t - 1 + 1e-9)    # job must fit in an hour ending by its deadline
            k_hi = min(k_max - buf, K - 1)
            r, k = (-1, 0)
            if k_hi > 0:
                r, k = self._best(fc[:, :k_hi + 1], load[:, :k_hi + 1], reserve[:, :k_hi + 1], ks[:k_hi + 1], dur)
                if r >= 0 and k > 0 and self.p.gate:
                    r, k = self._gate(t, fc, load, dur, r, k)
            if r < 0:                                                # urgent, or nothing feasible: run now
                r = self._run_now_region(fc[:, 0], load[:, 0], dur)
                k = 0
            load[r, k] += dur
            if k == 0:
                run_now.append((j, r))
        self.last_load = load            # planned CPU-seconds per (region, hour ahead); used for VM pre-start
        return run_now

    def _best(self, fc, load, reserve, ks, dur):
        feas = (load + reserve + dur <= self.cap)
        feas[self.blocked] = False
        if not feas.any():                                           # ignore headroom before giving up
            feas = (load + dur <= self.cap)
            feas[self.blocked] = False
            if not feas.any():
                return -1, 0
        new_vm = np.ceil((load + dur) / self.vm_s - 1e-9) - np.ceil(load / self.vm_s - 1e-9)
        kwh = self.dyn_kwh_per_s * dur + new_vm * self.idle_kwh_per_vmh
        score = self.alpha * fc * kwh + self.beta * new_vm * self.vm_price + self.gamma * ks
        score = np.where(feas, score, np.inf)
        r, k = np.unravel_index(np.argmin(score), score.shape)
        return int(r), int(k)

    def _gate(self, t, fc, load, dur, r, k):
        now_ok = (load[:, 0] + dur <= self.cap) & ~self.blocked
        if not now_ok.any():
            return r, k
        r0 = int(np.argmin(np.where(now_ok, fc[:, 0], np.inf)))
        saving = fc[r0, 0] - fc[r, k]
        margin = self.gate_k * self.mae[r, k] * self.inflation[r, t]
        return (r, k) if saving > margin else (r0, 0)

    def _run_now_region(self, fc0, load0, dur):
        ok = (load0 + dur <= self.cap) & ~self.blocked
        if ok.any():
            return int(np.argmin(np.where(ok, fc0, np.inf)))
        return int(np.argmin(np.where(self.blocked, np.inf, load0)))  # overflow: least-loaded allowed region
