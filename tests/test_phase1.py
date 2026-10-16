import numpy as np
import pandas as pd
import pytest

from src.data.features import horizon_features, origin_features, target, zone_frame
from src.data.synthetic_carbon import generate_all
from src.models.baselines import persistence_24h, seasonal_ma_7d
from src.utils import load_config


@pytest.fixture(scope="module")
def setup():
    cfg = load_config()
    cfg["carbon"]["synthetic"]["years"] = [2023]
    df = generate_all(cfg)
    return cfg, zone_frame(df, "IN-WE")


def test_synthetic_schema(setup):
    cfg, g = setup
    assert len(g) == 8760 and g.index.tz is not None and g["ci_lca"].notna().all()


def test_features_use_only_past(setup):
    """Every feature at origin T must be unchanged if the future (> T) is altered."""
    cfg, g = setup
    T = g.index[5000]
    g2 = g.copy()
    g2.loc[g2.index > T, ["ci_lca", "cfe_pct"]] = -999.0
    for h in (1, 12, 24):
        a = horizon_features(g, origin_features(g, cfg), h, cfg).loc[T]
        b = horizon_features(g2, origin_features(g2, cfg), h, cfg).loc[T]
        pd.testing.assert_series_equal(a, b)


def test_target_and_baselines_alignment(setup):
    cfg, g = setup
    y = g["ci_lca"]
    i, h = 400, 5
    assert target(g, h).iloc[i] == y.iloc[i + h]
    assert persistence_24h(y, h).iloc[i] == y.iloc[i + h - 24]
    assert np.isclose(seasonal_ma_7d(y, h).iloc[i], np.mean([y.iloc[i + h - 24 * k] for k in range(1, 8)]))
