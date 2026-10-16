"""Load + clean Electricity Maps hourly CSVs into one tidy table.

Layout: data/raw/carbon/<ZONE>/*.csv — the folder name (whitespace stripped) is the zone.
Falls back to the synthetic generator when no CSVs are found.

Output columns: datetime (UTC), zone, ci_lca, ci_direct, cfe_pct, re_pct, estimated,
ci_raw (target before cleaning), cleaned (bool).

    python -m src.data.load_carbon
"""
from __future__ import annotations

import pandas as pd

from src.data.synthetic_carbon import generate_all
from src.utils import ROOT, get_logger, load_config, project_path

log = get_logger(__name__)


def _read_zone(folder, c: dict) -> pd.DataFrame:
    files = sorted(folder.glob("*.csv"))
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    missing = [k for k in (c["datetime_col"], c["target_col"], c["direct_col"]) if k not in df.columns]
    if missing:
        raise KeyError(f"{folder.name}: expected columns not found: {missing}; got {list(df.columns)}")
    out = pd.DataFrame(
        {
            "datetime": pd.to_datetime(df[c["datetime_col"]], utc=True),
            "zone": folder.name.strip(),
            "ci_lca": df[c["target_col"]].astype(float),
            "ci_direct": df[c["direct_col"]].astype(float),
            "cfe_pct": df.get(c["cfe_col"]),
            "re_pct": df.get(c["re_col"]),
            "estimated": df.get("Data estimated"),
        }
    )
    return out.drop_duplicates("datetime").sort_values("datetime").reset_index(drop=True)


def _clean(df: pd.DataFrame, spike_g: float) -> pd.DataFrame:
    """Reindex to a full hourly grid, interpolate gaps, and remove single-hour spikes."""
    zone = df["zone"].iloc[0]
    df = df.set_index("datetime")
    full = pd.date_range(df.index.min(), df.index.max(), freq="h", tz="UTC")
    n_gap = len(full.difference(df.index))
    df = df.reindex(full)
    df["zone"] = zone
    df["cleaned"] = df["ci_lca"].isna()
    for col in ("ci_lca", "ci_direct"):
        med = df[col].rolling(5, center=True, min_periods=3).median()
        spike = (df[col] - med).abs() > spike_g
        if col == "ci_lca":
            df["ci_raw"] = df[col]
            df["cleaned"] |= spike
            for ts in df.index[spike]:
                log.warning("%s: spike at %s (%s=%.1f, local median %.1f) -> interpolated",
                            zone, ts, col, df.at[ts, col], med[ts])
        df.loc[spike, col] = float("nan")
    num = ["ci_lca", "ci_direct", "cfe_pct", "re_pct"]
    df[num] = df[num].interpolate(limit_direction="both")
    log.info("%s: %d hours, %d gap hours filled, %d values cleaned", zone, len(df), n_gap, int(df["cleaned"].sum()))
    return df.rename_axis("datetime").reset_index()


def load_all(cfg: dict | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    c = cfg["carbon"]
    raw = ROOT / cfg["paths"]["raw_carbon"]
    folders = sorted(p for p in raw.glob("*") if p.is_dir() and any(p.glob("*.csv"))) if raw.exists() else []
    if not folders:
        log.warning("no carbon CSVs under %s — using SYNTHETIC data (not valid for final results)", raw)
        df = generate_all(cfg)
        df["ci_raw"], df["cleaned"] = df["ci_lca"], False
        return df
    frames = [_clean(_read_zone(f, c), c["spike_threshold_g"]) for f in folders]
    df = pd.concat(frames, ignore_index=True)
    if c.get("use_direct"):
        df["ci_lca"], df["ci_direct"] = df["ci_direct"], df["ci_lca"]
        log.info("use_direct=true: target column is the DIRECT intensity")
    return df


if __name__ == "__main__":
    cfg = load_config()
    df = load_all(cfg)
    out = project_path(f"{cfg['paths']['processed']}/carbon_hourly.csv")
    df.to_csv(out, index=False)
    log.info("wrote %d rows (%s) to %s", len(df), ", ".join(df["zone"].unique()), out)
    print(df.groupby("zone")["ci_lca"].describe().round(1))
