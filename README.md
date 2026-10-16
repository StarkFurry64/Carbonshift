# CarbonShift — carbon-aware batch job scheduler

ML-based cloud resource manager for the *Advanced Cloud Computing* Lab IA. See `CLAUDE.md` for the full design.

## Setup
```bash
python3.12 -m venv .venv          # 3.11+ works
.venv/bin/pip install -r requirements.txt
```
Put the Electricity Maps hourly CSVs in `data/raw/carbon/<ZONE>/` (e.g. `IN-WE/`, `IN-SO/`); the
folder name is the zone. If no CSVs are present, a synthetic generator is used (development only).

## Phase 1 — data + forecasting
```bash
.venv/bin/python -m src.data.load_carbon   # clean + merge -> data/processed/carbon_hourly.csv
.venv/bin/python -m src.data.eda           # EDA charts -> results/eda_*.png, eda_summary.csv
.venv/bin/python -m src.models.train       # baselines + XGBoost h=1..24 -> models/, results/carbon_*
.venv/bin/python -m src.models.predict --zone IN-SO --origin "2024-06-01 06:00"
.venv/bin/python -m pytest -q tests
```

## Phase 2 — simulator + scheduler (no Azure)
```bash
.venv/bin/python -m src.data.jobs          # 2024 job stream -> data/processed/jobs_2024.csv, results/jobs_arrival_profile.png
.venv/bin/python -m src.models.workload    # workload forecaster -> models/workload_xgb.joblib, results/workload_*
.venv/bin/python -m src.sim.simulator      # slack sweep + failure injection (~2 min, parallel) -> results/
.venv/bin/python -m src.sim.plots          # redraw charts only
```
Outputs: `policy_comparison.csv/.png`, `carbon_vs_delay.png`, `failure_injection.csv/.png`,
`schedule_timeline_example.png`. Policies are defined in `src/scheduler/policies.py`; all tunables in `config.yaml`.
