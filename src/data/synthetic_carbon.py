"""Synthetic hourly carbon-intensity generator (development fallback only).

Produces the same schema as `load_carbon.load_all()` so downstream code is unchanged.
Pattern: zone base level + seasonal cycle + midday solar dip (IST) + AR(1) noise.
Final results must use real Electricity Maps data.

    python -m src.data.synthetic_carbon
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.utils import get_logger, load_config, project_path

log = get_logger(__name__)

# zone -> (mean gCO2/kWh, solar dip depth, seasonal amplitude)
ZONE_PARAMS = {"IN-WE": (660.0, 60.0, 25.0), "IN-SO": (520.0, 90.0, 45.0)}


def generate_zone(zone: str, years: list[int], seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base, dip, season_amp = ZONE_PARAMS.get(zone, (600.0, 70.0, 30.0))
    idx = pd.date_range(f"{min(years)}-01-01", f"{max(years)}-12-31 23:00", freq="h", tz="UTC")
    ist_hour = ((idx.hour + 5.5) % 24).to_numpy()
    doy = idx.dayofyear.to_numpy()
    solar = np.clip(np.cos((ist_hour - 12.5) / 12 * 2 * np.pi), 0, None) ** 2   # midday bump
    seasonal = season_amp * np.cos((doy - 200) / 365.25 * 2 * np.pi)            # monsoon low ~July
    noise = np.zeros(len(idx))
    eps = rng.normal(0, 12, len(idx))
    for i in range(1, len(idx)):
        noise[i] = 0.95 * noise[i - 1] + eps[i]
    lca = base + seasonal - dip * solar + noise
    cfe = np.clip(100 * (1 - lca / (base * 1.25)), 0, 100)
    return pd.DataFrame(
        {
            "datetime": idx,
            "zone": zone,
            "ci_lca": lca.round(2),
            "ci_direct": (lca * 0.91).round(2),
            "cfe_pct": cfe.round(2),
            "re_pct": (cfe * 0.85).round(2),
            "estimated": True,
        }
    )


def generate_all(cfg: dict) -> pd.DataFrame:
    years = cfg["carbon"]["synthetic"]["years"]
    zones = [r["zone"] for r in cfg["regions"]]
    return pd.concat(
        [generate_zone(z, years, cfg["seed"] + i) for i, z in enumerate(zones)], ignore_index=True
    )


if __name__ == "__main__":
    cfg = load_config()
    df = generate_all(cfg)
    out = project_path(f"{cfg['paths']['processed']}/carbon_synthetic.csv")
    df.to_csv(out, index=False)
    log.info("wrote %d synthetic rows to %s", len(df), out)
