"""Train + evaluate carbon-intensity forecasters for h = 1..24 h ahead.

Direct multi-horizon XGBoost: one model per (zone, horizon). By default the target is the
residual over persistence, y_{T+h} - y_{T+h-24} (forecast.target_mode), so the model learns a
correction to "same hour yesterday" instead of rebuilding it from piecewise-constant splits.
Compared against the baselines in src/models/baselines.py. Split by target year: train 2021–2023, test 2024 (never shuffled).
Early stopping uses the last `early_stop_val_days` of the training period, then each model is
refit on the full training period with the chosen number of trees.

Outputs
  models/carbon_xgb_<ZONE>.joblib          {h: XGBRegressor, "features": [...]}
  results/carbon_metrics_per_horizon.csv   zone, model, horizon, MAE, RMSE, MAPE
  results/carbon_metrics_summary.csv       mean over horizons + h = 1/6/12/24
  results/carbon_error_vs_horizon.png      MAE vs horizon (+ MAPE version)
  results/carbon_forecast_example.png      a 2024 week, actual vs day-ahead forecasts
  data/processed/carbon_forecasts_test.csv test-set predictions (all models)

    python -m src.models.train
"""
from __future__ import annotations

import time

import joblib
import matplotlib.dates
import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from src.data.features import base_forecast, horizon_features, origin_features, target, zone_frame
from src.data.load_carbon import load_all
from src.models.baselines import BASELINES
from src.plotting import SERIES, apply_style, plt
from src.utils import get_logger, load_config, project_path

log = get_logger(__name__)
MODEL_ORDER = ["persistence_24h", "moving_avg_24h", "seasonal_ma_7d", "xgboost"]
MODEL_LABEL = {
    "persistence_24h": "Persistence (same hour yesterday)",
    "moving_avg_24h": "Moving average (last 24 h)",
    "seasonal_ma_7d": "Seasonal MA (same hour, 7 days)",
    "xgboost": "XGBoost (direct, per horizon)",
}
MODEL_COLOR = dict(zip(MODEL_ORDER, SERIES))


def metrics(y: np.ndarray, yhat: np.ndarray) -> dict:
    err = yhat - y
    return {
        "MAE": np.mean(np.abs(err)),
        "RMSE": np.sqrt(np.mean(err**2)),
        "MAPE": 100 * np.mean(np.abs(err) / np.abs(y)),
    }


def fit_xgb(X_tr, d_tr, X_val, d_val, X_full, d_full, params: dict, seed: int) -> XGBRegressor:
    p = {k: v for k, v in params.items() if k != "early_stopping_rounds"}
    probe = XGBRegressor(**p, random_state=seed, early_stopping_rounds=params["early_stopping_rounds"])
    probe.fit(X_tr, d_tr, eval_set=[(X_val, d_val)], verbose=False)
    p["n_estimators"] = probe.best_iteration + 1
    model = XGBRegressor(**p, random_state=seed)
    model.fit(X_full, d_full, verbose=False)
    return model


def run_zone(df: pd.DataFrame, zone: str, cfg: dict) -> tuple[list[dict], pd.DataFrame]:
    g = zone_frame(df, zone)
    y = g["ci_lca"]
    X0 = origin_features(g, cfg)
    train_years, test_years = cfg["split"]["train_years"], cfg["split"]["test_years"]
    val_start = pd.Timestamp(f"{max(train_years)}-12-31 23:00", tz="UTC") - pd.Timedelta(
        days=cfg["split"]["early_stop_val_days"]
    )
    mode = cfg["forecast"]["target_mode"]
    rows, preds, models = [], [], {}
    for h in range(1, cfg["forecast"]["horizons"] + 1):
        X = horizon_features(g, X0, h, cfg)
        yt = target(g, h)
        t_time = g.index + pd.Timedelta(hours=h)
        t_year = t_time.year
        ok = X.notna().all(axis=1).to_numpy() & yt.notna().to_numpy()
        tr = ok & np.isin(t_year, train_years)
        te = ok & np.isin(t_year, test_years)
        val = tr & (t_time > val_start)
        fit = tr & ~val
        base = base_forecast(X, mode)
        delta = yt - base
        model = fit_xgb(X[fit], delta[fit], X[val], delta[val], X[tr], delta[tr],
                        cfg["forecast"]["xgboost"], cfg["seed"])
        models[h] = model

        out = pd.DataFrame({"origin": g.index[te], "zone": zone, "horizon": h,
                            "target_time": t_time[te], "actual": yt[te].to_numpy()})
        out["xgboost"] = base[te].to_numpy() + model.predict(X[te])
        for name, fn in BASELINES.items():
            out[name] = fn(y, h)[te].to_numpy()
        preds.append(out)
        for m in MODEL_ORDER:
            rows.append({"zone": zone, "model": m, "horizon": h, **metrics(out["actual"].to_numpy(), out[m].to_numpy())})
        if h in (1, 6, 12, 24):
            log.info("%s h=%2d trees=%4d  MAE xgb=%.1f persist=%.1f  (train n=%d, test n=%d)", zone, h,
                     model.n_estimators, rows[-1]["MAE"], rows[-4]["MAE"], tr.sum(), te.sum())
    models["features"] = list(X.columns)
    models["target_mode"] = mode
    joblib.dump(models, project_path(f"{cfg['paths']['models']}/carbon_xgb_{zone}.joblib"))
    return rows, pd.concat(preds, ignore_index=True)


def plot_error_vs_horizon(m: pd.DataFrame, metric: str, out_path, title: str) -> None:
    zones = ["IN-WE", "IN-SO"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.4), sharey=True)
    for ax, zone in zip(axes, zones):
        for model in MODEL_ORDER:
            s = m[(m.zone == zone) & (m.model == model)].sort_values("horizon")
            ax.plot(s["horizon"], s[metric], color=MODEL_COLOR[model], label=MODEL_LABEL[model],
                    marker="o", ms=3.5)
        ax.set_title(f"{zone}")
        ax.set_xlabel("Forecast horizon (hours ahead)")
        ax.set_xticks([1, 6, 12, 18, 24])
        ax.set_xlim(0.5, 24.5)
    axes[0].set_ylabel(f"{metric} ({'%' if metric == 'MAPE' else 'gCO₂eq/kWh'})")
    axes[0].set_ylim(bottom=0)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=4, bbox_to_anchor=(0.5, -0.04))
    fig.suptitle(title, x=0.01, ha="left", fontsize=13, fontweight="semibold")
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(out_path)
    plt.close(fig)


def plot_example_week(preds: pd.DataFrame, out_path, h: int = 24) -> None:
    start, end = pd.Timestamp("2024-03-04", tz="UTC"), pd.Timestamp("2024-03-11", tz="UTC")
    fig, axes = plt.subplots(2, 1, figsize=(11, 6.2), sharex=True)
    for ax, zone in zip(axes, ["IN-WE", "IN-SO"]):
        p = preds[(preds.zone == zone) & (preds.horizon == h)]
        p = p[(p.target_time >= start) & (p.target_time < end)].sort_values("target_time")
        x = p["target_time"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        ax.plot(x, p["actual"], color="#0b0b0b", lw=2, label="Actual")
        ax.plot(x, p["persistence_24h"], color=MODEL_COLOR["persistence_24h"], lw=1.6, label=MODEL_LABEL["persistence_24h"])
        ax.plot(x, p["xgboost"], color=MODEL_COLOR["xgboost"], lw=1.6, label=MODEL_LABEL["xgboost"])
        ax.set_title(f"{zone}: {h} h-ahead forecasts vs actual (4–10 Mar 2024, IST)")
        ax.set_ylabel("gCO₂eq/kWh")
    axes[1].xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%a %d %b"))
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.02))
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(out_path)
    plt.close(fig)


def main():
    cfg = load_config()
    apply_style()
    t0 = time.time()
    df = load_all(cfg)
    rows, preds = [], []
    for zone in [r["zone"] for r in cfg["regions"]]:
        r, p = run_zone(df, zone, cfg)
        rows += r
        preds.append(p)
    m = pd.DataFrame(rows)
    preds = pd.concat(preds, ignore_index=True)
    res = project_path(cfg["paths"]["results"])

    m.round(3).to_csv(res / "carbon_metrics_per_horizon.csv", index=False)
    summ = m.groupby(["zone", "model"])[["MAE", "RMSE", "MAPE"]].mean().add_suffix("_avg")
    for h in (1, 6, 12, 24):
        summ[f"MAE_h{h}"] = m[m.horizon == h].set_index(["zone", "model"])["MAE"]
    persist = summ.xs("persistence_24h", level="model")["MAE_avg"]
    summ["MAE_skill_vs_persistence_%"] = 100 * (1 - summ["MAE_avg"] / persist.reindex(summ.index.get_level_values(0)).to_numpy())
    summ = summ.reindex(MODEL_ORDER, level="model").round(2)
    summ.to_csv(res / "carbon_metrics_summary.csv")

    plot_error_vs_horizon(m, "MAE", res / "carbon_error_vs_horizon.png",
                          "Test-set (2024) MAE by forecast horizon")
    plot_error_vs_horizon(m, "MAPE", res / "carbon_mape_vs_horizon.png",
                          "Test-set (2024) MAPE by forecast horizon")
    plot_example_week(preds, res / "carbon_forecast_example.png")
    preds.to_csv(project_path(f"{cfg['paths']['processed']}/carbon_forecasts_test.csv"), index=False)
    log.info("done in %.0fs", time.time() - t0)
    print(summ.to_string())


if __name__ == "__main__":
    main()
