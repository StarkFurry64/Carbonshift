# CarbonShift — carbon-aware batch job scheduler

ML-based cloud resource manager for the *Advanced Cloud Computing* Lab IA. See `CLAUDE.md` for the full design.
Runs on AWS (free plan, `ap-southeast-2`): 4 × t3.micro workers in 2 groups bound to the Western (IN-WE) and
Southern (IN-SO) Indian grids, SQS job queues, a DynamoDB results table; the controller runs on the laptop.

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

## Phase 3 — AWS deployment
Log in once per session (temporary credentials, no access keys): `aws login`, then
`aws configure set region ap-southeast-2`.
```bash
infra/create.sh --plan              # what would be created (read-only)
infra/create.sh                     # create everything (or follow the manual console steps; we did that)
infra/create.sh --render-userdata   # VM startup scripts for manual launches -> infra/userdata/
infra/vms.sh status|start|stop      # only VMs tagged project=carbonshift
infra/teardown.sh                   # delete every carbonshift-* resource (asks first)

# live demo: replays one 2024 day on the real VMs and stops them at the end (or on Ctrl+C)
.venv/bin/python -m src.cloud.controller --dry-run                           # fake VMs, no AWS, ~3 s
.venv/bin/python -m src.cloud.controller --prewarm                           # 24 sim h at 60 s/h (~27 min)
.venv/bin/python -m src.cloud.controller --seconds-per-hour 20 --prewarm     # presentation speed (~10 min)
.venv/bin/python -m src.cloud.controller --policy fifo --prewarm             # baseline on real VMs
.venv/bin/python -m src.cloud.controller --scenario cloudy|burst --prewarm   # failure injection, live
```
Each run writes `results/live/<run_id>/{timeline.csv, jobs.csv, summary.json}`; job results are also in
DynamoDB (`carbonshift-results`, filter on `run_id`).

## Phase 4 — dashboard
```bash
.venv/bin/streamlit run src/dashboard/app.py      # http://localhost:8501
```
Tabs: **Live run** (auto-refresh while a controller runs, or replay a finished run), **Compare runs**,
**Simulation (Phase 2)**, **Forecasting (Phase 1)**.
