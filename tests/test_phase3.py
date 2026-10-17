"""Controller end-to-end in dry-run mode (fake VMs, virtual clock): no AWS calls."""
import argparse
import shutil

import numpy as np
import pytest

from src.cloud.controller import Controller, FakeCloud, VirtualClock, default_day, load_inputs
from src.utils import load_config


def _run(policy="carbonshift", scenario="none", hours=8, sph=60.0, prewarm=False):
    cfg = load_config()
    inp = load_inputs(cfg, scenario)
    args = argparse.Namespace(policy=policy, scenario=scenario, hours=hours, slack=6.0, seconds_per_hour=sph,
                              prewarm=prewarm, dry_run=True, yes=True, day=default_day(cfg, inp))
    clock = VirtualClock()
    cloud = FakeCloud(cfg, [r["zone"] for r in cfg["regions"]], clock)
    ctl = Controller(args, cfg, inp, cloud, clock)
    summary = ctl.run()
    return ctl, cloud, summary


@pytest.mark.parametrize("policy,sph,prewarm", [("carbonshift", 60.0, False), ("fifo", 60.0, False),
                                                 ("carbonshift", 20.0, True)])
def test_dry_run_completes_and_stops_vms(policy, sph, prewarm):
    ctl, cloud, s = _run(policy, sph=sph, prewarm=prewarm)
    assert s["jobs"] > 0 and s["jobs_finished"] == s["jobs"]
    assert s["deadline_miss_pct"] == 0
    assert all(st == "stopped" for st in cloud.states().values())          # nothing left running
    assert (ctl.vms <= ctl.n_vm).all()
    jobs = ctl.jobs_frame()
    assert (jobs["start_h"] >= jobs["submit_h"]).all()                     # never before submission
    allowed = {ctl.zones[r] for r in ctl.pol.regions}
    assert set(jobs["group"]) <= allowed
    for f in ("timeline.csv", "jobs.csv", "summary.json"):
        assert (ctl.out / f).exists()
    shutil.rmtree(ctl.out)                                                # keep results/live clean


def test_scenario_day_is_an_event_day():
    cfg = load_config()
    inp = load_inputs(cfg, "cloudy")
    day = default_day(cfg, inp)
    assert any(s.tz_convert(cfg["timezone_display"]).strftime("%Y-%m-%d") == day for s, _ in inp.events)


def test_job_scaling_matches_compression():
    ctl, cloud, _ = _run(sph=20.0, hours=4)
    rt = np.array([j["real_runtime_s"] for j in cloud.done])
    sim = np.array([j["sim_cpu_seconds"] for j in cloud.done])
    assert np.allclose(rt, sim * 20.0 / 3600)                              # 1 sim hour = 20 real s
    shutil.rmtree(ctl.out)
