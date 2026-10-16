import numpy as np
import pandas as pd
import pytest

from src.data.jobs import generate_jobs
from src.scheduler.policies import build_policies
from src.sim.inputs import SimInputs, _ahead
from src.sim.simulator import simulate
from src.utils import load_config


def test_job_stream_is_seeded_and_slack_independent():
    cfg = load_config()
    a = generate_jobs("2024-01-01", "2024-01-08", cfg, slack_h=2)
    b = generate_jobs("2024-01-01", "2024-01-08", cfg, slack_h=24)
    assert len(a) > 0
    pd.testing.assert_series_equal(a["submitted_at"], b["submitted_at"])
    assert ((b["deadline"] - b["submitted_at"]) == pd.Timedelta(hours=24)).all()
    assert a["cpu_seconds"].between(60, 1800).all()


def test_ahead():
    a = np.arange(5.0)[None, :]
    out = _ahead(a, 3)
    assert out[0, 1].tolist() == [1, 2, 3] and np.isnan(out[0, 4, 1])


@pytest.fixture(scope="module")
def small_inputs():
    cfg = load_config()
    T, K = 24 * 14, 24
    hours = pd.date_range("2024-01-01", periods=T, freq="h", tz="UTC")
    rng = np.random.default_rng(0)
    actual = np.stack([600 + 50 * np.sin(np.arange(T) / 24 * 2 * np.pi), 500 + 80 * np.cos(np.arange(T) / 24 * 2 * np.pi)])
    ahead = _ahead(actual, K)
    fc = {"xgboost": ahead + rng.normal(0, 5, ahead.shape), "persistence": ahead, "oracle": ahead}
    jobs = generate_jobs("2024-01-01", hours[-1] - pd.Timedelta(hours=30), cfg)[["job_id", "submitted_at", "cpu_seconds"]]
    wl = {"xgboost": np.full((T, K), 3600.0), "oracle": np.full((T, K), 3600.0)}
    return cfg, SimInputs(["IN-WE", "IN-SO"], hours, actual, fc, wl, np.full((2, K), 8.0), jobs, [])


@pytest.mark.parametrize("name", ["fifo", "carbonshift", "cs_no_gate_no_buffer", "oracle"])
def test_simulation_invariants(small_inputs, name):
    cfg, inp = small_inputs
    pol = build_policies(cfg)[name]
    out = simulate(pol, inp, 12, cfg)
    done = out["region"] >= 0
    assert done.all()                                                 # light load: everything runs
    assert (out["start"][done] >= out["submit"][done]).all()          # never before submission
    assert (out["busy"] <= out["vms"] * 3600 + 1e-6).all()            # no VM over 100 %
    assert (out["vms"] <= cfg["vm"]["max_per_region"]).all()
    assert np.isclose(out["busy"].sum(), out["cpu"].sum())            # work conserved
    assert set(np.unique(out["region"])) <= set(pol.regions)
    if name != "fifo":
        assert (out["finish"] <= out["deadline"] + 1e-9).all()
