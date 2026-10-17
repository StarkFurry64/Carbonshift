"""Live controller: replays one day of the 2024 job stream on the real AWS worker VMs.

Runs on the laptop. Every simulated hour (= `--seconds-per-hour` real seconds) it:
  1. lets that hour's jobs "arrive" (same seeded job stream as the Phase 2 simulator);
  2. asks the scheduler (any Phase 2 policy, default CarbonShift) which jobs start now and where;
  3. sends those jobs to the group's SQS queue (job size scaled to compressed time);
  4. starts / stops VMs: on when work is planned now or within the VM boot time, off when the
     group has nothing planned and its queue is empty;
  5. collects finished jobs from DynamoDB and logs a timeline row (VMs, queue, carbon so far).
It ends when every job has finished (or the time cap is hit) and ALWAYS stops the VMs on exit,
including Ctrl+C. Outputs go to results/live/<run_id>/: timeline.csv, jobs.csv, summary.json.

    python -m src.cloud.controller --dry-run                      # no AWS: fake VMs, instant
    python -m src.cloud.controller --seconds-per-hour 20 --prewarm  # live AWS demo (~10 min)
    python -m src.cloud.controller --policy fifo                   # baseline on real VMs
"""
from __future__ import annotations

import argparse
import json
import math
import time
from collections import deque
from dataclasses import replace

import numpy as np
import pandas as pd

from src.cloud.settings import cloud_settings
from src.scheduler.policies import build_policies
from src.scheduler.scheduler import Planner
from src.sim.metrics import region_hour_kwh, summarise
from src.sim.inputs import SimInputs, build_inputs
from src.sim.simulator import _cache_path, simulate
from src.utils import get_logger, load_config, project_path

log = get_logger("controller")
ACTIVE = ("pending", "running")


# ---------------------------------------------------------------- clocks
class RealClock:
    def now(self) -> float:
        return time.time()

    def sleep_until(self, t: float) -> None:
        time.sleep(max(0.0, t - time.time()))


class VirtualClock:
    """For --dry-run: time jumps instead of sleeping, so a whole day takes seconds."""

    def __init__(self, start: float = 1.8e9):
        self.t = start

    def now(self) -> float:
        return self.t

    def sleep_until(self, t: float) -> None:
        self.t = max(self.t, t)


# ---------------------------------------------------------------- cloud back-ends
def _parse_item(item: dict) -> dict:
    return {k: float(v["N"]) if "N" in v else v.get("S") for k, v in item.items()}


class AwsCloud:
    """The real thing: EC2 workers, SQS queues and the DynamoDB results table (all tagged carbonshift)."""

    def __init__(self, cfg: dict, groups: list[str], clock):
        import boto3

        s = cloud_settings(cfg)
        self.s, self.groups, self.clock = s, groups, clock
        self.ec2 = boto3.client("ec2", region_name=s["REGION"])
        self.sqs = boto3.client("sqs", region_name=s["REGION"])
        self.ddb = boto3.client("dynamodb", region_name=s["REGION"])
        res = self.ec2.describe_instances(Filters=[
            {"Name": f"tag:{s['TAG_KEY']}", "Values": [s["TAG_VALUE"]]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}])
        self.instances: dict[str, list[str]] = {g: [] for g in groups}
        for r in res["Reservations"]:
            for i in r["Instances"]:
                tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
                if tags.get("group") in self.instances:
                    self.instances[tags["group"]].append(i["InstanceId"])
        for g in groups:
            self.instances[g].sort()
            if not self.instances[g]:
                raise RuntimeError(f"no EC2 instances tagged group={g}")
        self.queue_url = {g: self.sqs.get_queue_url(QueueName=s["QUEUE_PREFIX"] + g)["QueueUrl"] for g in groups}

    def states(self) -> dict[str, str]:
        ids = [i for g in self.groups for i in self.instances[g]]
        res = self.ec2.describe_instances(InstanceIds=ids)
        return {i["InstanceId"]: i["State"]["Name"] for r in res["Reservations"] for i in r["Instances"]}

    def start(self, ids: list[str]) -> None:
        if ids:
            try:
                self.ec2.start_instances(InstanceIds=ids)
            except Exception as e:          # e.g. instance still stopping: retried next tick
                log.warning("start %s failed: %s", ids, e)

    def stop(self, ids: list[str]) -> None:
        if ids:
            try:
                self.ec2.stop_instances(InstanceIds=ids)
            except Exception as e:
                log.warning("stop %s failed: %s", ids, e)

    def purge(self) -> None:
        for g, url in self.queue_url.items():
            visible, inflight = self.queue_depth(g)
            if visible + inflight:
                log.warning("purging %d leftover messages from %s", visible + inflight, url)
                self.sqs.purge_queue(QueueUrl=url)

    def queue_depth(self, g: str) -> tuple[int, int]:
        a = self.sqs.get_queue_attributes(QueueUrl=self.queue_url[g], AttributeNames=[
            "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
        return int(a["ApproximateNumberOfMessages"]), int(a["ApproximateNumberOfMessagesNotVisible"])

    def send(self, g: str, msgs: list[dict]) -> None:
        for i in range(0, len(msgs), 10):
            batch = [{"Id": str(n), "MessageBody": json.dumps(m)} for n, m in enumerate(msgs[i:i + 10])]
            resp = self.sqs.send_message_batch(QueueUrl=self.queue_url[g], Entries=batch)
            if resp.get("Failed"):
                raise RuntimeError(f"SQS send failed: {resp['Failed']}")

    def _scan(self, expr: str, values: dict) -> list[dict]:
        pages = self.ddb.get_paginator("scan").paginate(
            TableName=self.s["TABLE"], FilterExpression=expr, ExpressionAttributeValues=values)
        return [_parse_item(it) for p in pages for it in p["Items"]]

    def results(self, run_id: str) -> list[dict]:
        return self._scan("run_id = :r", {":r": {"S": run_id}})

    def heartbeats(self) -> dict[str, float]:
        return {h["instance_id"]: h["worker_started_at"] for h in self._scan("kind = :k", {":k": {"S": "heartbeat"}})}


class FakeCloud:
    """Same interface as AwsCloud, simulated in-process (for --dry-run and tests).
    VMs take `boot_s` to start; workers process their group's queue in arrival order,
    one job at a time, for cpu_seconds / 60 real seconds (exactly like src/cloud/worker.py)."""

    def __init__(self, cfg: dict, groups: list[str], clock, boot_s: float = 50.0):
        self.groups, self.clock, self.boot_s = groups, clock, boot_s
        n = cfg["vm"]["max_per_region"]
        self.instances = {g: [f"fake-{g}-{i + 1}" for i in range(n)] for g in groups}
        self.group_of = {i: g for g in groups for i in self.instances[g]}
        self.state = {i: "stopped" for i in self.group_of}
        self.ready_at: dict[str, float] = {}
        self.free_at: dict[str, float] = {}
        self.hb: dict[str, float] = {}
        self.queue = {g: deque() for g in groups}
        self.inflight: list[dict] = []
        self.done: list[dict] = []

    def _advance(self) -> None:
        now = self.clock.now()
        for i, st in self.state.items():
            if st == "pending" and now >= self.ready_at[i]:
                self.state[i] = "running"
                self.hb[i] = self.ready_at[i]
        self.done += [j for j in self.inflight if j["finished_at"] <= now]
        self.inflight = [j for j in self.inflight if j["finished_at"] > now]
        for g in self.groups:
            vms = [i for i in self.instances[g] if self.state[i] == "running"]
            while self.queue[g] and vms:
                vm = min(vms, key=lambda i: self.free_at[i])
                job = self.queue[g][0]
                start = max(self.free_at[vm], job["enqueued_at"])
                if start > now:
                    break
                self.queue[g].popleft()
                finish = start + job["cpu_seconds"] / 60
                self.free_at[vm] = finish
                rec = {**job, "kind": "result", "group": g, "instance_id": vm, "received_at": start,
                       "started_at": start, "finished_at": finish, "real_runtime_s": finish - start}
                (self.done if finish <= now else self.inflight).append(rec)

    def states(self) -> dict[str, str]:
        self._advance()
        return dict(self.state)

    def start(self, ids: list[str]) -> None:
        now = self.clock.now()
        for i in ids:
            if self.state[i] == "stopped":
                self.state[i] = "pending"
                self.ready_at[i] = now + self.boot_s
                self.free_at[i] = now + self.boot_s

    def stop(self, ids: list[str]) -> None:
        for i in ids:
            self.state[i] = "stopped"

    def purge(self) -> None:
        pass

    def queue_depth(self, g: str) -> tuple[int, int]:
        self._advance()
        return len(self.queue[g]), sum(j["group"] == g for j in self.inflight)

    def send(self, g: str, msgs: list[dict]) -> None:
        self.queue[g].extend(msgs)

    def results(self, run_id: str) -> list[dict]:
        self._advance()
        return [j for j in self.done if j["run_id"] == run_id]

    def heartbeats(self) -> dict[str, float]:
        self._advance()
        return dict(self.hb)


# ---------------------------------------------------------------- the controller
class Controller:
    def __init__(self, args, cfg: dict, inp: SimInputs, cloud, clock):
        self.a, self.cfg, self.inp, self.cloud, self.clock = args, cfg, inp, cloud, clock
        self.pol = build_policies(cfg)[args.policy]
        self.S = float(args.seconds_per_hour)
        self.n_vm = cfg["vm"]["max_per_region"]
        self.zones = inp.zones
        self.groups = [self.zones[r] for r in self.pol.regions]
        # replay window: first whole UTC hour at/after local midnight of --day
        start_local = pd.Timestamp(args.day, tz=cfg["timezone_display"])
        start_utc = start_local.tz_convert("UTC").ceil("h")
        self.t0 = int(inp.hours.get_loc(start_utc))
        hours_since = ((inp.jobs["submitted_at"] - inp.hours[0]) / pd.Timedelta(hours=1)).to_numpy()
        sel = (hours_since >= self.t0) & (hours_since < self.t0 + args.hours)
        self.jobs = inp.jobs[sel].reset_index(drop=True)
        self.submit = hours_since[sel]
        self.cpu = self.jobs["cpu_seconds"].to_numpy().astype(float)
        self.deadline = self.submit + args.slack
        self.n = len(self.jobs)
        self.max_ticks = args.hours + int(math.ceil(args.slack)) + 2
        self.planner = Planner(self.pol, inp.fc[self.pol.forecast] if self.pol.kind == "plan" else inp.fc["xgboost"],
                               inp.actual, inp.wl.get(self.pol.workload) if self.pol.workload else None,
                               inp.mae, self.cpu, self.deadline, cfg)
        # VMs take ~boot_s to start: look that many simulated hours ahead when deciding to start them
        self.lookahead = max(1, math.ceil(cfg["cloud"]["vm_boot_s_estimate"] / self.S))
        # cool-down: a VM stays on until it has been unneeded for this many ticks (avoids stop/start
        # thrash; AWS cannot restart a VM that is still 'stopping', and bills at least 60 s per start)
        self.cooldown = self.lookahead
        self.need_hist = {r: deque(maxlen=self.cooldown) for r in range(len(inp.zones))}
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.run_id = f"{args.policy}-{args.scenario}-{pd.Timestamp(args.day):%Y%m%d}-{stamp}" + ("-dry" if args.dry_run else "")
        self.out = project_path(f"{cfg['paths']['results']}/live/{self.run_id}")
        R = len(self.zones)
        self.vms = np.zeros((R, self.max_ticks))
        self.busy = np.zeros((R, self.max_ticks))
        self.sent_tick = np.full(self.n, -1)
        self.region = np.full(self.n, -1)
        self.start_h = np.full(self.n, np.nan)
        self.finish_h = np.full(self.n, np.nan)
        self.vm_id = np.full(self.n, "", dtype=object)
        self.timeline: list[dict] = []
        self.real_vm_seconds = 0.0

    # --- helpers
    def sim_h(self, real_t: float) -> float:
        """Real wall-clock time -> simulated hour, using the clock anchors (see tick_due)."""
        ar, asim = self.anchors[0]
        for a_real, a_sim in self.anchors:
            if real_t >= a_real:
                ar, asim = a_real, a_sim
        return asim + (real_t - ar) / self.S

    def tick_due(self, k: int) -> float:
        ar, asim = self.anchors[-1]
        return ar + (self.t0 + k - asim) * self.S

    def ist(self, t_hours: float) -> str:
        ts = self.inp.hours[0] + pd.Timedelta(hours=t_hours)
        return ts.tz_convert(self.cfg["timezone_display"]).strftime("%d %b %H:%M")

    def collect(self) -> None:
        idx = {jid: k for k, jid in enumerate(self.jobs["job_id"])}
        for r in self.cloud.results(self.run_id):
            k = idx.get(r["job_id"].split("#", 1)[1])
            if k is None or not np.isnan(self.finish_h[k]):
                continue
            self.start_h[k] = self.sim_h(r["started_at"])
            self.finish_h[k] = self.sim_h(r["finished_at"])
            self.vm_id[k] = r["instance_id"]
            col = int(self.start_h[k]) - self.t0
            if 0 <= col < self.max_ticks:
                self.busy[self.region[k], col] += self.cpu[k]

    def pack(self, chosen: list[tuple[int, int]]) -> tuple[list[tuple[int, int]], np.ndarray]:
        """First-fit onto max_per_region VMs per group-hour (same capacity rule as the simulator).
        Work already sent but not yet finished (queue backlog, e.g. while a VM was still booting)
        occupies capacity first, so new jobs, in particular urgent ones, are not queued behind it."""
        used = np.zeros((len(self.zones), self.n_vm))
        unfinished = (self.sent_tick >= 0) & np.isnan(self.finish_h)
        for r in range(len(self.zones)):
            backlog = float(self.cpu[unfinished & (self.region == r)].sum())
            for v in range(self.n_vm):
                used[r, v] = min(3600.0, backlog)
                backlog -= used[r, v]
        ok = []
        for j, r in chosen:
            for v in range(self.n_vm):
                if used[r, v] + self.cpu[j] <= 3600:
                    used[r, v] += self.cpu[j]
                    ok.append((j, r))
                    break
        return ok, (used > 0).sum(axis=1)

    def needed_vms(self, r: int, vms_now: int, pending: list[int]) -> int:
        """VMs group r should have on: busy now, or work planned within the boot look-ahead."""
        need = vms_now
        load = self.planner.last_load
        if load is not None:
            for k in range(1, min(self.lookahead, load.shape[1] - 1) + 1):
                need = max(need, math.ceil(load[r, k] / 3600 - 1e-9))
        elif self.pol.kind == "fifo" and r in self.pol.regions:   # FIFO backlog runs next hour
            backlog = sum(self.cpu[j] for j in pending)
            need = max(need, math.ceil(backlog / 3600 - 1e-9))
        return min(need, self.n_vm)

    def scale(self, states: dict[str, str], vms_now: np.ndarray, pending: list[int]) -> dict[str, tuple[int, int]]:
        info = {}
        for r in self.pol.regions:
            g = self.zones[r]
            ids = self.cloud.instances[g]
            active = [i for i in ids if states.get(i) in ACTIVE]
            need = self.needed_vms(r, int(vms_now[r]), pending)
            keep = max([need, *self.need_hist[r]])          # needed now or within the cool-down window
            self.need_hist[r].append(need)
            if need > len(active):
                to_start = [i for i in ids if states.get(i) == "stopped"][: need - len(active)]
                if to_start:
                    log.info("  start %s (%s)", g, ", ".join(to_start))
                    self.cloud.start(to_start)
            elif keep < len(active):
                visible, inflight = self.cloud.queue_depth(g)
                if visible + inflight == 0:            # never stop a VM that may be mid-job
                    to_stop = [i for i in reversed(ids) if states.get(i) == "running"][: len(active) - keep]
                    if to_stop:
                        log.info("  stop  %s (%s)", g, ", ".join(to_stop))
                        self.cloud.stop(to_stop)
            info[g] = (len(active), keep)
        return info

    def stop_all(self) -> None:
        """Stop every CarbonShift VM. A VM still booting ('pending') cannot be stopped yet, so retry."""
        try:
            for _ in range(30):
                states = self.cloud.states()
                ids = [i for g in self.cloud.instances for i in self.cloud.instances[g]]
                running = [i for i in ids if states.get(i) == "running"]
                if running:
                    log.info("stopping VMs: %s", ", ".join(running))
                    self.cloud.stop(running)
                if not any(states.get(i) == "pending" for i in ids):
                    return
                self.clock.sleep_until(self.clock.now() + 5)
            log.error("some VMs are still booting — run infra/vms.sh stop in a minute")
        except Exception as e:   # never leave the user without a clear instruction
            log.error("could not stop VMs (%s) — run infra/vms.sh stop", e)

    def prewarm(self) -> None:
        ids = [i for g in self.groups for i in self.cloud.instances[g]]
        t_req = self.clock.now()
        states = self.cloud.states()
        already = {i for i in ids if states.get(i) == "running"}     # worker already up
        log.info("pre-warming %d VMs and waiting for their workers ...", len(ids))
        self.cloud.start([i for i in ids if states.get(i) == "stopped"])
        for _ in range(60):
            hb = self.cloud.heartbeats()
            ready = [i for i in ids if i in already or hb.get(i, 0) >= t_req - 5]
            if len(ready) == len(ids):
                for r in self.pol.regions:                  # keep pre-warmed VMs through the first cool-down
                    self.need_hist[r].extend([self.n_vm] * self.cooldown)
                log.info("all workers ready after %.0f s", self.clock.now() - t_req)
                return
            self.clock.sleep_until(self.clock.now() + 5)
        log.warning("not all workers reported in; continuing")

    # --- main loop
    def run(self) -> dict:
        a = self.a
        log.info("run %s | policy=%s scenario=%s | %d jobs submitted %s → %s IST | slack %g h | "
                 "1 sim hour = %g s | groups %s", self.run_id, a.policy, a.scenario, self.n,
                 self.ist(self.t0), self.ist(self.t0 + a.hours), a.slack, self.S, ", ".join(self.groups))
        self.cloud.purge()
        if a.prewarm:
            self.prewarm()
        self.run_start = self.clock.now()
        self.anchors = [(self.run_start, float(self.t0))]   # (real time, simulated hour) pairs
        pending, nxt = [], 0
        R = len(self.zones)
        try:
            for k in range(self.max_ticks):
                due, now = self.tick_due(k), self.clock.now()
                if now > due + self.S / 2:
                    # the laptop fell behind (slow AWS call, sleep...): pause the simulated clock rather
                    # than replaying the missed hours back-to-back, which would squeeze the VMs' time
                    log.warning("controller %.0f s behind schedule: pausing the simulated clock", now - due)
                    self.anchors.append((now, float(self.t0 + k)))
                    due = now
                self.clock.sleep_until(due)
                t = self.t0 + k
                while nxt < self.n and self.submit[nxt] < t:
                    pending.append(nxt)
                    nxt += 1
                self.collect()
                chosen = self.planner.decide(t, pending) if pending else []
                ok, vms_now = self.pack(chosen)
                msgs = {r: [] for r in range(R)}
                for j, r in ok:
                    self.sent_tick[j], self.region[j] = k, r
                    msgs[r].append({"job_id": f"{self.run_id}#{self.jobs.job_id[j]}", "run_id": self.run_id,
                                    # worker runs cpu_seconds/60 real s; scale so 1 sim hour = S real s
                                    "cpu_seconds": self.cpu[j] * self.S / 60, "sim_cpu_seconds": self.cpu[j],
                                    "sim_submit_h": self.submit[j], "sim_deadline_h": self.deadline[j],
                                    "sim_hour": t, "enqueued_at": self.clock.now()})
                for r, m in msgs.items():
                    if m:
                        self.cloud.send(self.zones[r], m)
                started = {j for j, _ in ok}
                pending = [j for j in pending if j not in started]
                states = self.cloud.states()
                vm_info = self.scale(states, vms_now, pending)
                after = self.cloud.states()            # state after this tick's start/stop requests
                for r in range(R):
                    g = self.zones[r]
                    on = sum(after.get(i) in ACTIVE for i in self.cloud.instances.get(g, []))
                    self.vms[r, k] = on
                    self.real_vm_seconds += on * self.S
                row = self.tick_row(k, t, len(chosen), ok, pending, vm_info, after)
                self.timeline.append(row)
                self.write_progress()
                log.info("[%s IST] sent %2d (%s) | waiting %3d | VMs %s | done %d/%d | CO₂ %.1f g | missed %d",
                         self.ist(t), len(ok), " ".join(f"{z} {row[f'sent_{z}']}" for z in self.zones),
                         len(pending), " ".join(f"{g} {vm_info[g][0]}→{vm_info[g][1]}" for g in vm_info),
                         row["jobs_done"], self.n, row["carbon_g_so_far"], row["missed_so_far"])
                if nxt >= self.n and not pending and np.isfinite(self.finish_h).all():
                    log.info("all %d jobs finished", self.n)
                    break
            else:
                log.warning("time cap reached with %d jobs unfinished", int(np.isnan(self.finish_h).sum()))
            # give in-flight jobs a moment to report back
            for _ in range(6):
                self.collect()
                if np.isfinite(self.finish_h[self.sent_tick >= 0]).all():
                    break
                self.clock.sleep_until(self.clock.now() + max(2.0, self.S / 6))
        finally:
            self.stop_all()
            if self.timeline:
                self.timeline.append(self.end_row(len(self.timeline) - 1))
            self.write_progress()
        return self.write_summary(k + 1)

    # --- outputs
    def carbon_so_far(self, upto: int) -> tuple[np.ndarray, float]:
        kwh = region_hour_kwh(self.vms[:, :upto], self.busy[:, :upto], self.cfg)
        ci = np.nan_to_num(self.inp.actual[:, self.t0:self.t0 + upto])
        return kwh, float((kwh * ci).sum())

    def tick_row(self, k, t, n_chosen, ok, pending, vm_info, states: dict | None = None) -> dict:
        _, co2 = self.carbon_so_far(k + 1)
        done = np.isfinite(self.finish_h)
        missed = int((self.finish_h[done] > self.deadline[done] + 1e-9).sum()
                     + ((~done) & (self.deadline < t)).sum())
        fc = self.planner.fc[:, t, 0]
        row = {"run_id": self.run_id, "tick": k, "sim_hour": t,
               "time_utc": str(self.inp.hours[t]), "time_ist": self.ist(t), "real_time": self.clock.now(),
               "arrived_total": int((self.submit < t).sum()), "waiting": len(pending),
               "jobs_done": int(done.sum()), "missed_so_far": missed, "carbon_g_so_far": co2}
        for r, z in enumerate(self.zones):
            row[f"sent_{z}"] = sum(rr == r for _, rr in ok)
            row[f"vms_{z}"] = int(self.vms[r, k])
            row[f"vms_target_{z}"] = vm_info.get(z, (0, 0))[1]
            row[f"ci_actual_{z}"] = float(self.inp.actual[r, t])
            row[f"ci_forecast_{z}"] = float(fc[r]) if np.isfinite(fc[r]) else np.nan
            for i, iid in enumerate(self.cloud.instances.get(z, [])):
                row[f"vm_{z}_{i + 1}_id"] = iid
                row[f"vm_{z}_{i + 1}_state"] = (states or {}).get(iid, "stopped")
        row["phase"] = "run"
        return row

    def end_row(self, k: int) -> dict:
        """Final timeline record written after all VMs were told to stop."""
        self.collect()
        row = dict(self.timeline[-1]) if self.timeline else {}
        states = self.cloud.states()
        _, co2 = self.carbon_so_far(k + 1)
        done = np.isfinite(self.finish_h)
        t_end = int(row.get("sim_hour", self.t0)) + 1
        row.update({"sim_hour": t_end, "time_utc": str(self.inp.hours[min(t_end, len(self.inp.hours) - 1)]),
                    "time_ist": self.ist(t_end)})
        for r, z in enumerate(self.zones):
            row[f"ci_actual_{z}"] = float(self.inp.actual[r, min(t_end, self.inp.actual.shape[1] - 1)])
            row[f"ci_forecast_{z}"] = np.nan
        row.update({"tick": k + 1, "real_time": self.clock.now(), "phase": "end", "waiting": 0,
                    "jobs_done": int(done.sum()), "carbon_g_so_far": co2,
                    "missed_so_far": int((~(self.finish_h <= self.deadline + 1e-9)).sum())})
        for z in self.zones:
            row[f"sent_{z}"] = 0
            ids = self.cloud.instances.get(z, [])
            row[f"vms_{z}"] = sum(states.get(i) in ACTIVE for i in ids)
            for i, iid in enumerate(ids):
                row[f"vm_{z}_{i + 1}_state"] = states.get(iid, "stopped")
        return row

    def jobs_frame(self) -> pd.DataFrame:
        return pd.DataFrame({
            "job_id": self.jobs["job_id"], "sim_cpu_seconds": self.cpu,
            "submit_h": self.submit, "deadline_h": self.deadline,
            "submit_ist": [self.ist(x) for x in self.submit],
            "group": [self.zones[r] if r >= 0 else "" for r in self.region],
            "sent_tick": self.sent_tick, "start_h": self.start_h, "finish_h": self.finish_h,
            "wait_h": self.start_h - self.submit, "missed": ~(self.finish_h <= self.deadline + 1e-9),
            "instance_id": self.vm_id})

    def write_progress(self) -> None:
        pd.DataFrame(self.timeline).to_csv(self.out / "timeline.csv", index=False)
        self.jobs_frame().to_csv(self.out / "jobs.csv", index=False)

    def write_summary(self, ticks: int) -> dict:
        vm = self.cfg["vm"]
        kwh, co2 = self.carbon_so_far(ticks)
        done = np.isfinite(self.finish_h)
        vm_hours = float(self.vms[:, :ticks].sum())
        price = vm["price_per_hour_usd"] + vm["ipv4_price_per_hour_usd"]
        wait = (self.start_h - self.submit)[done]
        summary = {
            "run_id": self.run_id, "policy": self.a.policy, "scenario": self.a.scenario, "dry_run": self.a.dry_run,
            "day": str(self.a.day), "seconds_per_sim_hour": self.S, "slack_h": self.a.slack,
            "jobs": self.n, "jobs_finished": int(done.sum()),
            "deadline_miss_pct": 100 * float(np.mean(~(self.finish_h <= self.deadline + 1e-9))) if self.n else 0.0,
            "avg_wait_h": float(wait.mean()) if done.any() else None,
            "p95_wait_h": float(np.percentile(wait, 95)) if done.any() else None,
            "sim_hours": ticks, "vm_hours": vm_hours, "energy_kwh": float(kwh.sum()), "carbon_g": co2,
            "avg_util_pct": 100 * float(self.busy.sum() / max(vm_hours * 3600, 1)),
            "vm_cost_usd_sim_equivalent": vm_hours * price,
            "real_minutes": (self.clock.now() - self.run_start) / 60,
            "real_aws_cost_usd": self.real_vm_seconds / 3600 * price,
        }
        summary.update(self.simulated_reference())
        (self.out / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
        log.info("summary → %s\n%s", self.out / "summary.json",
                 "\n".join(f"  {k:28s} {v:.4g}" if isinstance(v, float) else f"  {k:28s} {v}" for k, v in summary.items()))
        return summary

    def simulated_reference(self) -> dict:
        """What the Phase 2 simulator predicts for exactly these jobs (this policy and FIFO)."""
        inp = replace(self.inp, jobs=self.jobs)
        pols = build_policies(self.cfg)
        out = {}
        for name in dict.fromkeys([self.a.policy, "fifo"]):
            res = simulate(pols[name], inp, self.a.slack, self.cfg)
            m = summarise(res, self.inp.actual, self.cfg, len(pols[name].regions))
            out[f"sim_{name}_carbon_g"] = m["carbon_kg"] * 1000
            out[f"sim_{name}_avg_wait_h"] = m["avg_wait_h"]
        return out


def default_day(cfg: dict, inp: SimInputs) -> str:
    """The configured demo day, or for an injected scenario the first event day on/after it."""
    day = pd.Timestamp(cfg["cloud"]["live_default_day"], tz=cfg["timezone_display"])
    for start, _ in inp.events:
        local = start.tz_convert(cfg["timezone_display"])
        if local >= day:
            return local.strftime("%Y-%m-%d")
    return day.strftime("%Y-%m-%d")


def load_inputs(cfg: dict, scenario: str) -> SimInputs:
    p = _cache_path(cfg, scenario)
    if p.exists():
        import joblib

        return joblib.load(p)
    log.info("building simulation inputs for scenario=%s ...", scenario)
    return build_inputs(cfg, scenario)


def main():
    cfg = load_config()
    lc = cfg["cloud"]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--policy", default="carbonshift", choices=sorted(build_policies(cfg)))
    ap.add_argument("--day", default=None, help=f"IST day to replay (2024); default {lc['live_default_day']}, "
                    "or that week's event day for --scenario cloudy/burst")
    ap.add_argument("--hours", type=int, default=24, help="hours of job arrivals to replay")
    ap.add_argument("--slack", type=float, default=lc["live_default_slack_h"], help="deadline slack (h)")
    ap.add_argument("--scenario", default="none", choices=["none", "cloudy", "burst"])
    ap.add_argument("--seconds-per-hour", type=float, default=cfg["time_compression"]["real_seconds_per_sim_hour"])
    ap.add_argument("--prewarm", action="store_true", help="start the VMs before the run")
    ap.add_argument("--dry-run", action="store_true", help="fake VMs, no AWS, runs instantly")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args()

    inp = load_inputs(cfg, args.scenario)
    args.day = args.day or default_day(cfg, inp)
    clock = VirtualClock() if args.dry_run else RealClock()
    groups = [r["zone"] for r in cfg["regions"]]
    cloud = FakeCloud(cfg, groups, clock) if args.dry_run else AwsCloud(cfg, groups, clock)
    ctl = Controller(args, cfg, inp, cloud, clock)
    if not args.dry_run and not args.yes:
        est_min = ctl.max_ticks * ctl.S / 60
        print(f"\nLIVE AWS RUN: {ctl.n} jobs, policy {args.policy}, up to {len(ctl.groups) * ctl.n_vm} t3.micro VMs, "
              f"at most ~{est_min:.0f} min (usually less), est. cost < ${len(ctl.groups) * ctl.n_vm * est_min / 60 * 0.0182 + 0.01:.2f}.\n"
              f"VMs are stopped automatically at the end or on Ctrl+C.")
        if input("Start? [y/N] ").strip().lower() != "y":
            print("aborted")
            return
    ctl.run()


if __name__ == "__main__":
    main()
