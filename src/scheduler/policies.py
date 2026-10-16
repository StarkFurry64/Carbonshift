"""Policy definitions. Every policy runs through the same simulator and job stream.

kind="fifo": run every visible job immediately, in arrival order, in one fixed region.
kind="plan": rolling-horizon carbon-aware planner (src/scheduler/scheduler.py) that differs only
in which carbon forecast it trusts, whether the confidence gate is on, the safety buffer, and
which workload forecast it uses to keep headroom for future arrivals.
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Policy:
    name: str
    label: str
    kind: str                       # "fifo" | "plan"
    regions: tuple[int, ...] = (0, 1)
    forecast: str = "xgboost"       # carbon forecast: xgboost | persistence | oracle
    workload: str | None = "xgboost"  # workload forecast for headroom: xgboost | oracle | None
    gate: bool = False
    gate_adaptive: bool = False
    safety_buffer_h: int = 1


def build_policies(cfg: dict) -> dict[str, Policy]:
    buf = int(cfg["scheduler"]["safety_buffer_h"])
    cs = Policy("carbonshift", "CarbonShift (XGBoost + gate + buffer)", "plan",
                forecast="xgboost", workload="xgboost", gate=True, safety_buffer_h=buf,
                gate_adaptive=bool(cfg["scheduler"]["gate_adaptive"]))
    pols = [
        Policy("fifo", "FIFO run-now, IN-WE only (baseline)", "fifo", regions=(0,)),
        Policy("fifo_in_so", "FIFO run-now, IN-SO only (reference)", "fifo", regions=(1,)),
        Policy("simple_carbon", "Simple carbon rule (persistence)", "plan",
               forecast="persistence", workload=None, gate=False, safety_buffer_h=buf),
        cs,
        # perfect knowledge of carbon and arrivals, so it needs no gate and no buffer (upper bound)
        Policy("oracle", "Oracle (actual future carbon)", "plan",
               forecast="oracle", workload="oracle", gate=False, safety_buffer_h=0),
        # ablations for the failure analysis
        replace(cs, name="cs_no_gate", label="CarbonShift, no gate", gate=False),
        replace(cs, name="cs_no_buffer", label="CarbonShift, no buffer", safety_buffer_h=0),
        replace(cs, name="cs_no_gate_no_buffer", label="CarbonShift, no gate & no buffer",
                gate=False, safety_buffer_h=0),
        replace(cs, name="cs_adaptive_gate", label="CarbonShift, adaptive gate", gate_adaptive=True),
    ]
    return {p.name: p for p in pols}


MAIN_POLICIES = ["fifo", "fifo_in_so", "simple_carbon", "cs_no_gate", "carbonshift", "oracle"]
FAILURE_POLICIES = ["fifo", "carbonshift", "cs_no_gate", "cs_no_buffer", "cs_no_gate_no_buffer",
                    "cs_adaptive_gate", "oracle"]
