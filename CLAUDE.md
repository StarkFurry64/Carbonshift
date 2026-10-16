# CarbonShift — Carbon-aware batch job scheduler (project context)

## What this project is
Lab IA for *Advanced Cloud Computing*: "Design an ML-based cloud resource manager that predicts
workload and allocates resources." Deadline: **18 October 2026**. Deliverables: working demo on
AWS, screencast video, and a report. (Planned for Azure; switched to AWS on 16 Oct 2026 —
the assignment does not mandate a provider.)

CarbonShift forecasts (a) incoming batch workload and (b) grid carbon intensity per grid zone
for the next 24 hours, then decides **when, where (worker group / grid zone) and on how many VMs** each batch job
runs, so jobs meet their deadlines with the lowest carbon emissions and acceptable cost.

The assignment requires us to cover:
1. Identify input data and features
2. Choose the ML algorithm and prediction horizon
3. Design the resource allocation / autoscaling mechanism
4. Evaluate prediction accuracy, utilization, latency, cost and energy
5. Discuss what happens when the prediction is wrong

## Hard constraints (do not violate)
- **AWS free plan** account `428521272060`, shared with the user's other project **QR-Shield** — never
  touch its Lambda/Amplify/CloudWatch resources. $120 credits until 15 Apr 2027.
- The free plan allows **one region only: `ap-southeast-2` (Sydney)**. Other regions, EC2 Global View and
  account region settings are blocked. Do not upgrade the plan.
- vCPU quota (Running On-Demand Standard) in ap-southeast-2 = **8** → max **4 × t3.micro** (2 vCPU each).
- Worker VMs: **t3.micro**, max 2 per worker group, 2 groups. Official Canonical Ubuntu 24.04 AMI (SSM public
  parameter), never Marketplace AMIs. 8 GB gp3 root disks. Security group with **no inbound rules**
  (no SSH; debug via SSM Session Manager). Public IPv4 only for outbound access to SQS/DynamoDB.
- Every CarbonShift resource is named `carbonshift-*` and tagged `project=carbonshift`; teardown deletes
  only those. Scripts: `infra/create.sh` (+ `--plan`), `infra/vms.sh start|stop|status`, `infra/teardown.sh`.
- Always show the user what will be created and its cost **before** creating AWS resources.
- Keep VMs **stopped** when not testing. Budget alert `carbonshift-guard` ($10) exists.
- Auth: `aws login` (temporary console credentials, no access keys); boto3 needs `boto3[crt]`.
- The scheduler/controller, forecasting models and dashboard run on the **developer's laptop** (control plane).

## Worker groups and grid zones
| Group | AWS region | Electricity Maps zone | Role |
|---|---|---|---|
| `IN-WE` | `ap-southeast-2` | `IN-WE` (Western India) | Region A |
| `IN-SO` | `ap-southeast-2` | `IN-SO` (Southern India) | Region B |
Both groups run physically in Sydney (free-plan single-region limit); each is bound to one Indian grid
zone's carbon trace. The live demo replays the 2024 test year, so physical location does not change
the results — state this in the report.

## Architecture
```
Job generator ──► Amazon SQS queue per group (jobs: id, cpu_seconds, deadline, submitted_at)
                          ▲
Laptop control plane:     │ enqueue at chosen start time
  1. carbon forecaster (per zone, 1–24 h ahead)
  2. workload forecaster (CPU-hours arriving, 1–24 h ahead)
  3. scheduler (rolling horizon, re-plans every simulated hour)
  4. VM controller (boto3: start/stop t3.micro workers)
  5. Streamlit dashboard
                          │
Worker VMs (t3.micro) per group: pull job from SQS → run real small task → write result + timing
to DynamoDB → controller collects metrics.
```
Time compression for the live demo: **1 simulated hour = 1 real minute** (configurable).

## Data
- **Carbon intensity**: Electricity Maps free hourly CSVs (portal.electricitymaps.com/datasets),
  zones IN-WE and IN-SO, years 2021–2024. The user downloads these manually into `data/raw/carbon/`.
  Inspect column names before parsing (expect a UTC datetime column and
  "Carbon Intensity gCO₂eq/kWh (direct)" / "(LCA)"; use the LCA column, keep direct as an option).
  If files are missing, `src/data/synthetic_carbon.py` generates a realistic stand-in
  (daily solar dip, seasonal pattern, noise) so development is never blocked — but final results
  must use real data.
- **Workload**: synthetic batch job generator (`src/data/jobs.py`): Poisson arrivals with a daily
  pattern (higher in office hours IST), job size log-normal (1–30 CPU-minutes), deadline slack
  configurable (2, 6, 12, 24 h). Seeded for reproducibility.
- Split: train 2021–2023, test 2024 (time-based, never shuffle).

## Features
Carbon: lags t-1, t-2, t-3, t-24, t-168; rolling mean/std 24 h; hour of day, day of week, month;
(optional) low-carbon %. Workload: lagged hourly CPU-hours, rolling means, hour/day-of-week.

## Models and horizon
- Horizon: **1–24 h ahead, hourly** (deadlines up to 24 h, data is hourly).
- Baselines: persistence (same hour yesterday), moving average.
- Main: **XGBoost** — direct multi-horizon (one model per horizon h=1..24, or horizon as a feature).
- Optional: quantile XGBoost (P10/P50/P90) for uncertainty-aware scheduling.
- Metrics: MAE, RMSE, MAPE per horizon; plot error vs horizon.
- Save models to `models/` with joblib.

## Scheduler (rolling horizon, every hour)
1. Jobs whose slack (deadline − now − est_duration) < `safety_buffer` → run **now** in the cleanest region.
2. Remaining jobs, earliest deadline first: for every feasible (region, start_hour) within the deadline,
   score = α·(forecast_carbon × job_energy_kWh) + β·(VM cost) + γ·(delay_hours). Pick the min with capacity.
3. Only delay a job if predicted saving > typical forecast error (from test-set MAE) — "confidence gate".
4. VMs per region-hour = ceil(assigned job-load / per-VM capacity), capped at 2. Start/deallocate via SDK.
5. Re-plan next hour with fresh forecasts/actuals.

## Policies to compare (same job stream)
1. FIFO run-immediately, single region (baseline)
2. Simple carbon rule (persistence forecast / fixed "run at midday")
3. **CarbonShift (ML forecasts)**
4. Oracle (actual future carbon) — upper bound

## Evaluation metrics
- Prediction: MAE / RMSE / MAPE (carbon and workload)
- Utilization: avg worker CPU %, idle VM-hours
- Latency: job wait time, completion time, **deadline-miss rate**
- Cost: VM-hours × (t3.micro + public IPv4 hourly price) + gp3 disk (config values from the AWS Price List API)
- Energy: per-vCPU linear power model P = P_idle + (P_max − P_idle) × util, × PUE
  (Cloud Carbon Footprint **AWS** coefficients as config defaults; cite them in the report)
- Carbon: gCO₂ = kWh × actual carbon intensity of that region-hour
- Key chart: carbon saved (%) vs average delay, across slack values 2/6/12/24 h
- Regret vs oracle

## Failure analysis ("what if the prediction is wrong")
- `src/sim/inject.py`: "surprise cloudy day" (actual carbon +X% vs forecast for N hours) and
  "workload burst" (2–3× arrivals). Show re-planning + run-now fallback protecting deadlines.
- Report carbon/deadline impact with and without the confidence gate and safety buffer.

## Repo layout
```
carbonshift/
  CLAUDE.md
  README.md
  requirements.txt
  config.yaml              # regions, prices, power coefficients, α β γ, time compression
  data/raw/carbon/         # Electricity Maps CSVs (gitignored)
  data/processed/
  notebooks/01_eda.ipynb, 02_forecasting.ipynb
  src/data/{load_carbon.py, synthetic_carbon.py, jobs.py, features.py}
  src/models/{train.py, predict.py, baselines.py}
  src/scheduler/{policies.py, scheduler.py}
  src/sim/{simulator.py, inject.py, metrics.py}
  src/cloud/{settings.py, worker.py, controller.py}
  src/dashboard/app.py     # Streamlit
  infra/{create.sh, vms.sh, teardown.sh}   # AWS CLI
  results/                 # CSVs + PNG charts for the report
  tests/
```

## Build order (do one phase at a time, verify before moving on)
1. **Data + forecasting (no cloud)**: load/clean carbon data, EDA plots, features, baselines vs XGBoost,
   metrics per horizon, save models. ✅ when: error-vs-horizon plot and metrics table in `results/`.
2. **Simulator + scheduler (no cloud)**: job generator, 4 policies, metrics, slack sweep, failure injection.
   ✅ when: comparison table + carbon-vs-delay chart in `results/`.
3. **AWS deployment**: infra scripts, worker on t3.micro, SQS queues, controller driving real VMs in accelerated time.
   ✅ when: a 24-sim-hour run completes on AWS and metrics are logged.
4. **Dashboard**: live carbon forecast vs actual, job timeline, VMs per region, cumulative carbon/cost.
5. **Report + screencast assets**: export all charts/tables; write README run instructions.

## Conventions
- Python 3.11+ (venv uses 3.12), pandas, numpy, scikit-learn, xgboost, matplotlib/plotly, streamlit, pyyaml,
  boto3[crt].
- All tunables in `config.yaml`; random seeds fixed.
- Times stored in UTC; display in IST.
- Each script runnable from CLI (`python -m src.models.train`), with clear logging.
- Never commit credentials; auth via `aws login`.
