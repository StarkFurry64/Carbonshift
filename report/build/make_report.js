// Builds report/CarbonShift_Report.docx from data.json + images. Run twice: the second pass fills the
// table of contents with page numbers measured from the first PDF render (toc_pages.json).
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, ImageRun, Table, TableRow, TableCell, WidthType, ShadingType,
  AlignmentType, HeadingLevel, Header, Footer, PageNumber, BorderStyle, LevelFormat, PageBreak,
  TabStopType, TabStopPosition, LeaderType, Tab, VerticalAlign,
} = require("docx");

const B = __dirname, ROOT = path.resolve(B, "..", "..");
const D = JSON.parse(fs.readFileSync(path.join(B, "data.json"), "utf8"));
const TOC = fs.existsSync(path.join(B, "toc_pages.json")) ? JSON.parse(fs.readFileSync(path.join(B, "toc_pages.json"), "utf8")) : {};
const W = 9026;                     // A4 text width in DXA (1" margins)
const FONT = "Arial";
const BLUE = "1F4E79";

// ---------------------------------------------------------------- helpers
const f = (x, n = 1) => (x === undefined || x === null || Number.isNaN(x) ? "–" : Number(x).toFixed(n));
const pct = (x, n = 1) => `${f(x, n)}%`;
function runs(text, base = {}) {          // **bold** and _italic_ inline markup
  const out = [];
  text.split(/(\*\*[^*]+\*\*|_[^_]+_)/).forEach((t) => {
    if (!t) return;
    if (t.startsWith("**")) out.push(new TextRun({ ...base, text: t.slice(2, -2), bold: true }));
    else if (t.startsWith("_") && t.endsWith("_") && t.length > 2) out.push(new TextRun({ ...base, text: t.slice(1, -1), italics: true }));
    else out.push(new TextRun({ ...base, text: t }));
  });
  return out;
}
const P = (text, o = {}) => new Paragraph({ children: runs(text, o.run || {}), alignment: o.align || AlignmentType.JUSTIFIED,
  spacing: { after: o.after ?? 120, before: o.before ?? 0 }, keepNext: o.keepNext });
const bullet = (text, level = 0) => new Paragraph({ children: runs(text), numbering: { reference: "bullets", level },
  spacing: { after: 60 }, alignment: AlignmentType.LEFT });
const numbered = (text) => new Paragraph({ children: runs(text), numbering: { reference: "steps", level: 0 }, spacing: { after: 60 } });
const H1 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(text)], pageBreakBefore: false });
const H2 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(text)] });
const pageBreak = () => new Paragraph({ children: [new PageBreak()] });

let figN = 0, tabN = 0;
function pngSize(file) { const b = fs.readFileSync(file); return [b.readUInt32BE(16), b.readUInt32BE(20)]; }
function img(file, widthIn) {
  const [w, h] = pngSize(file);
  const px = widthIn * 96;
  return new ImageRun({ type: "png", data: fs.readFileSync(file), transformation: { width: px, height: Math.round(px * h / w) } });
}
function figure(file, caption, widthIn = 6.2) {
  figN += 1;
  return [
    new Paragraph({ children: [img(file, widthIn)], alignment: AlignmentType.CENTER, spacing: { before: 120, after: 60 }, keepNext: true }),
    new Paragraph({ children: runs(`**Figure ${figN}.** ${caption}`, { size: 18, color: "444444" }), alignment: AlignmentType.CENTER, spacing: { after: 200 } }),
  ];
}
const NOB = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
function figurePair(a, b, caption, widthIn = 3.05) {
  figN += 1;
  const cell = (file) => new TableCell({ width: { size: W / 2, type: WidthType.DXA }, borders: { top: NOB, bottom: NOB, left: NOB, right: NOB },
    verticalAlign: VerticalAlign.CENTER, children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [img(file, widthIn)] })] });
  return [
    new Table({ width: { size: W, type: WidthType.DXA }, columnWidths: [W / 2, W / 2], borders: { top: NOB, bottom: NOB, left: NOB, right: NOB, insideHorizontal: NOB, insideVertical: NOB },
      rows: [new TableRow({ children: [cell(a), cell(b)] })] }),
    new Paragraph({ children: runs(`**Figure ${figN}.** ${caption}`, { size: 18, color: "444444" }), alignment: AlignmentType.CENTER, spacing: { before: 60, after: 200 } }),
  ];
}
const thin = { style: BorderStyle.SINGLE, size: 4, color: "BFBFBF" };
function table(headers, rows, widths, caption, opts = {}) {
  tabN += 1;
  const total = widths.reduce((a, b) => a + b, 0);
  const cell = (t, i, head, shade, keep = true) => new TableCell({
    width: { size: widths[i], type: WidthType.DXA }, borders: { top: thin, bottom: thin, left: thin, right: thin },
    shading: head ? { type: ShadingType.CLEAR, fill: "DCE8F5", color: "auto" } : shade ? { type: ShadingType.CLEAR, fill: "F3F7EC", color: "auto" } : undefined,
    margins: { top: 50, bottom: 50, left: 90, right: 90 },
    children: [new Paragraph({ alignment: i === 0 || opts.leftAll ? AlignmentType.LEFT : AlignmentType.CENTER, keepNext: keep,
      children: runs(String(t), { size: 18, bold: head }) })],
  });
  return [
    new Paragraph({ children: runs(`**Table ${tabN}.** ${caption}`, { size: 18, color: "444444" }), spacing: { before: 120, after: 60 }, keepNext: true }),
    new Table({ width: { size: total, type: WidthType.DXA }, columnWidths: widths,
      rows: [new TableRow({ tableHeader: true, children: headers.map((h, i) => cell(h, i, true)) }),
        ...rows.map((r, k) => new TableRow({ cantSplit: true, children: r.map((c, i) => cell(c, i, false, opts.highlight && opts.highlight(k), k < rows.length - 1)) }))] }),
    new Paragraph({ children: [], spacing: { after: 120 } }),
  ];
}
const shot = (n) => path.join(B, "shots", n);
const res = (n) => path.join(ROOT, "results", n);

// ---------------------------------------------------------------- data shortcuts
const sim = (pol, slack) => D.sim.find((r) => r.policy === pol && r.slack_h === slack) || {};
const fail = (sc, pol, slack) => D.failure.find((r) => r.scenario === sc && r.policy === pol && r.slack_h === slack) || {};
const cs = (zone, model) => D.carbon_summary.find((r) => r.zone === zone && r.model === model) || {};
const ch = (zone, model, h) => D.carbon_h[`${zone}|${model}|${h}`];
const wl = (model) => D.workload.find((r) => r.model === model) || {};
const L = D.live || {};
const LC = L.carbonshift || {}, LF = L.fifo || {}, LB = L.burst || {};
const liveSaved = LC.carbon_g && LF.carbon_g ? 100 * (1 - LC.carbon_g / LF.carbon_g) : NaN;
const eWE = D.eda["IN-WE"], eSO = D.eda["IN-SO"];
const skill = (z) => 100 * (1 - cs(z, "xgboost").MAE_avg / cs(z, "persistence_24h").MAE_avg);
const S12 = sim("carbonshift", 12);

// ---------------------------------------------------------------- cover
const MEMBERS = [["Tanish Gupta", "16010123132"], ["Tanish Shah", "16010123133"], ["Tanisha Mukherjee", "16010123134"]];
const cover = [
  new Paragraph({ children: [], spacing: { before: 1800 } }),
  new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Advanced Cloud Computing · Lab IA", size: 26, color: "666666" })], spacing: { after: 400 } }),
  new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "CarbonShift", size: 64, bold: true, color: BLUE })], spacing: { after: 160 } }),
  new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "An ML-based cloud resource manager that predicts workload and grid carbon intensity and allocates resources on AWS", size: 30, color: "333333" })], spacing: { after: 900 } }),
  new Table({ width: { size: 6000, type: WidthType.DXA }, alignment: AlignmentType.CENTER, columnWidths: [3600, 2400],
    rows: [["Name", "Roll No."], ...MEMBERS].map((r, k) => new TableRow({ children: r.map((t, i) => new TableCell({
      width: { size: i ? 2400 : 3600, type: WidthType.DXA }, borders: { top: thin, bottom: thin, left: thin, right: thin },
      shading: k === 0 ? { type: ShadingType.CLEAR, fill: "DCE8F5", color: "auto" } : undefined,
      margins: { top: 80, bottom: 80, left: 140, right: 140 },
      children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: t, bold: k === 0, size: 24 })] })] })) })) }),
  new Paragraph({ children: [], spacing: { before: 900 } }),
  new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Semester VII · Submitted 18 October 2026", size: 24, color: "666666" })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Code: github.com/StarkFurry64/Carbonshift (private)", size: 20, color: "888888" })], spacing: { before: 120 } }),
];

// ---------------------------------------------------------------- front matter
const tocEntries = [];
const T1 = (t) => { tocEntries.push([0, t]); return H1(t); };
const T2 = (t) => { tocEntries.push([1, t]); return H2(t); };

const front = [];
const body = [];
const add = (...xs) => xs.flat().forEach((x) => body.push(x));

front.push(H1("Abstract"),
  P(`Cloud batch jobs (reports, model training, data processing) often do not need to run the moment they are submitted. ` +
    `**CarbonShift** exploits that flexibility: it forecasts the carbon intensity of the electricity grid and the incoming workload ` +
    `24 hours ahead with **XGBoost**, and a rolling-horizon scheduler decides **when**, **where** and **on how many virtual machines** each job runs ` +
    `so that every job meets its deadline with the least CO₂. Using four years of hourly Electricity Maps data for the Western (IN-WE) and Southern (IN-SO) Indian grids, ` +
    `the forecaster reduces the error of the strongest simple baseline by ${f(skill("IN-WE"), 0)}% and ${f(skill("IN-SO"), 0)}% respectively. ` +
    `In a full-year simulation of ${D.jobs_2024.toLocaleString("en-US")} jobs, CarbonShift emits **${f(S12.carbon_saved_vs_fifo_pct, 0)}% less CO₂** than running jobs immediately, ` +
    `stays within **${f(S12.regret_vs_oracle_pct)}%** of an oracle with perfect knowledge of the future and misses **${f(S12.deadline_miss_pct, 2)}%** of deadlines. ` +
    `The system was deployed on **AWS** (EC2, SQS, DynamoDB): a laptop controller drives four t3.micro workers in real time, and on identical job streams ` +
    `CarbonShift used **${f(liveSaved, 0)}% less CO₂** than the FIFO baseline on the real VMs. We also evaluate what happens when forecasts are wrong ` +
    `(surprise cloudy days, workload bursts) and show how a confidence gate and safety buffer protect deadlines.`),
  P(`**Keywords:** carbon-aware computing, workload forecasting, XGBoost, autoscaling, AWS EC2, SQS, DynamoDB, temporal and spatial load shifting.`));

// ---------------------------------------------------------------- 1 introduction
add(pageBreak(), T1("1. Introduction"),
  P(`Data centres consume roughly 1–1.5% of global electricity, and the CO₂ emitted per kilowatt-hour depends strongly on **where** and **when** that electricity is produced. ` +
    `In India, coal still dominates at night, while solar generation lowers the carbon intensity of the grid during the day; the Southern grid also has more wind and hydro than the Western grid. ` +
    `Batch workloads are an ideal lever: a job with a 12-hour deadline can be moved to the cleanest hour, or to the cleaner region, without the user noticing.`),
  P(`The assignment asks for an **ML-based cloud resource manager that predicts workload and allocates resources**. CarbonShift does this with two forecasters and a scheduler, ` +
    `and allocates real AWS virtual machines accordingly. Table 1 maps the five assignment requirements to the sections of this report.`),
  table(["Assignment requirement", "Where it is answered"], [
    ["1. Identify input data and features", "Section 3 (data, cleaning, EDA, features)"],
    ["2. Choose the ML algorithm and prediction horizon", "Section 4 (baselines vs XGBoost, 1–24 h ahead)"],
    ["3. Design the resource allocation / autoscaling mechanism", "Section 5 (scheduler, VM controller) and Section 6 (AWS)"],
    ["4. Evaluate prediction accuracy, utilisation, latency, cost and energy", "Sections 4.4, 7 and 8"],
    ["5. Discuss what happens when the prediction is wrong", "Section 9 (failure injection, gate, buffer)"],
  ], [5200, 3826], "Assignment requirements and where they are covered."),
  H2("1.1 Objectives and contributions"),
  bullet("A cleaned, four-year hourly carbon-intensity dataset for two Indian grid zones, with exploratory analysis of its daily and seasonal patterns."),
  bullet("Direct multi-horizon XGBoost forecasters (1–24 h) for grid carbon intensity and for incoming batch workload, compared with simple baselines."),
  bullet("A rolling-horizon, deadline-aware scheduler with a confidence gate and safety buffer, evaluated against FIFO, a simple carbon rule and an oracle."),
  bullet("A real AWS deployment (EC2, SQS, DynamoDB, IAM) driven by a laptop controller that autoscales VMs ahead of planned work, plus a live Streamlit dashboard."),
  bullet("A failure analysis that injects wrong forecasts and workload bursts, and an honest comparison of simulated and measured results."));

// ---------------------------------------------------------------- 2 architecture
add(T1("2. System architecture"),
  P(`Figure 1 shows the system. The **control plane** runs on a laptop: it holds the forecasting models, the scheduler, the VM controller and the dashboard. ` +
    `The **data plane** runs on AWS: one Amazon SQS queue and two t3.micro EC2 workers per grid group, and one DynamoDB table that collects results. ` +
    `Every simulated hour (one real minute in the demo, configurable) the controller lets new jobs arrive, re-plans all waiting jobs against fresh forecasts, ` +
    `sends the jobs scheduled for this hour to the right queue, starts or stops VMs, and reads finished results back.`),
  figure(shot("architecture.png"), "CarbonShift architecture: laptop control plane and AWS data plane.", 6.3),
  P(`**Deployment constraints.** The AWS account is on the free plan ($120 credit), which allows a single region, ap-southeast-2 (Sydney), and has an 8-vCPU on-demand quota. ` +
    `Both worker groups therefore run in Sydney, and each group is bound to one Indian grid zone's carbon trace (IN-WE or IN-SO). Because the live demo replays the 2024 test year, ` +
    `the physical location of the VMs does not change any result; it only affects network latency, which is negligible for batch jobs.`));

// ---------------------------------------------------------------- 3 data
const E = D.data;
add(T1("3. Input data and features"),
  H2("3.1 Grid carbon intensity"),
  P(`We use the Electricity Maps hourly datasets for zones **IN-WE** (Western India) and **IN-SO** (Southern India), 2021–2024: ${E.rows_per_zone.toLocaleString("en-US")} hours per zone, ` +
    `with no gaps or duplicates. The target is the **life-cycle (LCA) carbon intensity** in gCO₂eq/kWh; the direct intensity is kept as an option. ` +
    `One single-hour spike (41 g against a local median of 468 g, IN-SO, 19 Jan 2023) was replaced by interpolation. About ${f(E.pct_estimated, 0)}% of the values are Electricity Maps model estimates ` +
    `rather than direct measurements, which we list as a limitation. Training uses 2021–2023 and testing 2024, split by time and never shuffled.`),
  figurePair(res("eda_hourly_profile.png"), res("eda_hour_month_heatmap.png"),
    "Left: average carbon intensity by hour of day (IST). Right: average by hour and month. The midday solar dip and the cleaner monsoon months are clearly visible."),
  table(["Statistic (2021–2024)", "IN-WE (Western)", "IN-SO (Southern)"], [
    ["Mean LCA intensity (g/kWh)", f(eWE.mean, 0), f(eSO.mean, 0)],
    ["Typical within-day range (g/kWh)", f(eWE.median_within_day_range, 0), f(eSO.median_within_day_range, 0)],
    ["Cleanest / dirtiest hour (IST)", `${eWE.cleanest_hour_ist}:00 / ${eWE.dirtiest_hour_ist}:00`, `${eSO.cleanest_hour_ist}:00 / ${eSO.dirtiest_hour_ist}:00`],
    ["Cleanest / dirtiest month", `${eWE.cleanest_month} / ${eWE.dirtiest_month}`, `${eSO.cleanest_month} / ${eSO.dirtiest_month}`],
    ["Weekday vs weekend mean", `${f(eWE.weekday_mean, 0)} / ${f(eWE.weekend_mean, 0)}`, `${f(eSO.weekday_mean, 0)} / ${f(eSO.weekend_mean, 0)}`],
  ], [3600, 2713, 2713], "Key patterns found in the exploratory analysis."),
  P(`Three findings drive the design: (i) a strong **24-hour cycle** with a dip between about 07:00 and 17:00 IST, so delaying work towards midday saves carbon; ` +
    `(ii) the Southern grid was **cleaner than the Western grid in ${f(E.so_cleaner_pct_2024, 0)}% of the hours of 2024** (by ${f(E.avg_gap_2024, 0)} g/kWh on average), so the region choice matters as much as timing; ` +
    `(iii) there is no weekday/weekend effect, so the day-of-week feature carries little information for carbon.`),
  H2("3.2 Batch workload"),
  P(`The workload is a seeded synthetic job stream: Poisson arrivals with an IST office-hours pattern (1.8× during 09:00–18:00, 0.4× at night, 0.6× at weekends; 8 jobs per hour on average), ` +
    `log-normal job sizes clipped to 1–30 CPU-minutes (median 5, mean 7.2), and a deadline slack of 2, 6, 12 or 24 hours. 2024 contains ${D.jobs_2024.toLocaleString("en-US")} jobs. ` +
    `The same seed produces the same jobs for every policy and slack value, so all comparisons are paired.`),
  figure(res("jobs_arrival_profile.png"), "Average CPU-minutes of work arriving per hour. Weekday office-hour peaks approach the capacity of one group (two VMs).", 4.6),
  H2("3.3 Features"),
  table(["Feature group", "Carbon forecaster", "Workload forecaster"], [
    ["Recent values (lags)", "t−1, t−2, t−3, t−24, t−168", "t−1, t−2, t−3, t−24, t−168"],
    ["Rolling statistics", "24 h mean and std, 24 h change of the mean", "24 h and 168 h mean"],
    ["Seasonal references", "same hour yesterday, last week, 7-day same-hour mean", "same hour yesterday / last week, 4-week same hour & weekday"],
    ["Calendar (of target hour)", "hour, day of week, month (IST)", "hour, day of week (IST)"],
    ["Other", "carbon-free energy %, latest change vs yesterday", "–"],
  ], [2300, 3500, 3226], "Features used by the two forecasters (all known at forecast time; no leakage).", { leftAll: true }),
  P(`A unit test verifies that no feature uses future information: changing every value after the forecast origin leaves all features unchanged.`));

// ---------------------------------------------------------------- 4 ML
add(T1("4. Machine-learning model and prediction horizon"),
  H2("4.1 Horizon"),
  P(`Deadlines in our workload are at most 24 hours and the data is hourly, so both forecasters predict **1 to 24 hours ahead in hourly steps**. ` +
    `The scheduler re-plans every hour with fresh forecasts (rolling horizon), so short-horizon accuracy matters most for urgent decisions and long-horizon accuracy for deciding whether to wait.`),
  H2("4.2 Models"),
  P(`We compare three baselines: **persistence** (same hour yesterday), a **24-hour moving average**, and a **7-day same-hour average**, against **XGBoost** (gradient-boosted trees) with ` +
    `one model per horizon (direct multi-horizon strategy, 24 models per zone). Early stopping uses the last 90 days of the training period, then each model is refit on all of 2021–2023.`),
  P(`An important design choice came from a failed first attempt: predicting the change from the latest reading made XGBoost _worse_ than persistence at mid horizons, because trees approximate ` +
    `the large, almost linear daily swing poorly. Predicting the **correction to "same hour yesterday"** (the residual over persistence) fixed this: XGBoost then beats every baseline at every horizon.`),
  H2("4.3 Carbon forecast accuracy (test year 2024)"),
  table(["Model", "IN-WE MAE", "IN-WE MAPE", "IN-SO MAE", "IN-SO MAPE"],
    ["persistence_24h", "moving_avg_24h", "seasonal_ma_7d", "xgboost"].map((m) => [
      { persistence_24h: "Persistence (same hour yesterday)", moving_avg_24h: "Moving average (24 h)", seasonal_ma_7d: "Seasonal MA (7 days)", xgboost: "**XGBoost (ours)**" }[m],
      f(cs("IN-WE", m).MAE_avg, 2), pct(cs("IN-WE", m).MAPE_avg, 2), f(cs("IN-SO", m).MAE_avg, 2), pct(cs("IN-SO", m).MAPE_avg, 2)]),
    [3626, 1350, 1350, 1350, 1350], "Average error over horizons 1–24 h (MAE in gCO₂eq/kWh).", { highlight: (k) => k === 3 }),
  figurePair(res("carbon_error_vs_horizon.png"), res("carbon_forecast_example.png"),
    "Left: MAE by horizon for both zones (XGBoost in yellow). Right: 24-hour-ahead forecasts against the actual values for one week of 2024.", 3.05),
  P(`XGBoost's error grows from ${f(ch("IN-WE", "xgboost", 1))} g at 1 h to ${f(ch("IN-WE", "xgboost", 24))} g at 24 h for IN-WE, and from ${f(ch("IN-SO", "xgboost", 1))} g to ${f(ch("IN-SO", "xgboost", 24))} g for IN-SO, ` +
    `about 1–2% of the signal. It improves on persistence by ${f(skill("IN-WE"), 0)}% (IN-WE) and ${f(skill("IN-SO"), 0)}% (IN-SO). This error is an order of magnitude smaller than the ` +
    `70–120 g daily swing, which is why shifting work in time is worthwhile; the per-horizon MAE is also used by the scheduler's confidence gate (Section 5).`),
  H2("4.4 Workload forecast"),
  P(`The workload forecaster predicts CPU-hours arriving per hour, again 1–24 h ahead. It is trained on a separate 2021–2023 stream and tested on the 2024 stream used everywhere else.`),
  table(["Model", "MAE (CPU-min/h)", "RMSE (CPU-min/h)"],
    [["Persistence (same hour yesterday)", "persistence_24h"], ["Same hour last week", "seasonal_naive_168h"], ["4-week same hour & weekday", "seasonal_ma_4w"], ["**XGBoost (ours)**", "xgboost"]]
      .map(([n, m]) => [n, f(wl(m).MAE * 60), f(wl(m).RMSE * 60)]),
    [4626, 2200, 2200], `Workload forecast error (mean arrival rate ${f(wl("xgboost").mean_actual_cpu_h * 60)} CPU-min/h).`, { highlight: (k) => k === 3 }),
  P(`Because arrivals are random, much of the remaining error is irreducible. The scheduler therefore uses the workload forecast conservatively: it keeps 50% of the forecast arrivals free in future hours, ` +
    `so deferred jobs do not crowd out work that has not arrived yet.`));

// ---------------------------------------------------------------- 5 scheduler
add(T1("5. Resource allocation and autoscaling design"),
  H2("5.1 Rolling-horizon scheduler"),
  P(`Every hour the scheduler re-plans **all** waiting jobs against the latest 24-hour forecasts and commits only the jobs placed in the current hour; the rest are re-planned an hour later. Per job, in earliest-deadline-first order:`),
  numbered("**Urgent jobs run now.** If no feasible hour remains outside the safety buffer (1 h), the job runs immediately in the group with the lowest forecast carbon intensity that has capacity."),
  numbered("**Score every feasible (group, hour) inside the deadline:** score = α · CI_forecast · (job energy + idle energy of any extra VM it would switch on) + β · extra VM cost + γ · delay. With α = 1, β = 0, γ = 0.001 the scheduler is carbon-first and prefers earlier hours on ties. Capacity is two VMs per group-hour, minus headroom reserved for forecast arrivals."),
  numbered("**Confidence gate.** If the best option is to wait, the job waits only if the predicted saving (g/kWh) exceeds the forecast's typical error at that horizon (test-set MAE). Otherwise it runs now."),
  numbered("**Commit and autoscale.** Jobs placed in the current hour are packed onto VMs (first-fit); the number of VMs per group follows from the plan."),
  H2("5.2 Policies compared"),
  table(["Policy", "Forecast used", "Where it runs", "Gate / buffer"], [
    ["FIFO (baseline)", "none: run immediately", "IN-WE only", "– / –"],
    ["FIFO in IN-SO (reference)", "none: run immediately", "IN-SO only", "– / –"],
    ["Simple carbon rule", "persistence (same hour yesterday)", "both groups", "no / yes"],
    ["**CarbonShift**", "XGBoost carbon + workload", "both groups", "yes / yes"],
    ["Oracle (upper bound)", "actual future carbon and arrivals", "both groups", "– / no"],
  ], [2526, 2800, 1900, 1800], "Policies evaluated on the same job stream.", { highlight: (k) => k === 3 }),
  H2("5.3 VM controller: autoscaling on real VMs"),
  P(`On AWS the plan must become real VM actions. A t3.micro took about **25 s** from the start request until its worker was polling the queue, i.e. a large fraction of a simulated hour. ` +
    `The controller therefore (i) **starts VMs ahead of planned work**, looking ahead as many simulated hours as the boot time spans; (ii) applies a **cool-down** so a VM is stopped only after it ` +
    `has been unneeded for that long and its queue is empty (AWS cannot restart a VM that is still stopping, and bills at least 60 s per start); (iii) counts work still **in the queue** before ` +
    `sending more, so urgent jobs are never queued behind a backlog; and (iv) **always stops every VM** when the run ends or is interrupted.`),
  H2("5.4 Energy, carbon and cost model"),
  P(`Energy follows the Cloud Carbon Footprint linear model with AWS coefficients: per VM-hour, PUE · (vCPUs · P_idle + P_mem + (P_max − P_idle) · busy vCPU-hours), with P_idle = 0.74 W, ` +
    `P_max = 3.5 W per vCPU, 0.392 W/GB of memory and PUE = 1.135. Carbon multiplies each group-hour's energy by the **actual** carbon intensity of that hour (never the forecast). ` +
    `Cost uses the AWS Price List API: t3.micro $0.0132/h plus $0.005/h for its public IPv4 address while running, and $0.768 per month for its 8 GB gp3 disk.`));

// ---------------------------------------------------------------- 6 AWS implementation
add(T1("6. Implementation on AWS"),
  P(`All resources were created in the AWS console (and are reproducible with infra/create.sh), named carbonshift-* and tagged project=carbonshift, so that infra/teardown.sh deletes exactly these and ` +
    `nothing else in the shared account. Table 7 lists them; Figures 5–8 show them in the console and from the AWS CLI.`),
  table(["Resource", "Configuration", "Purpose"], [
    ["4 × EC2 t3.micro", "Ubuntu 24.04 (Canonical), 8 GB gp3, IMDSv2 only, unlimited credits", "Worker VMs, 2 per group"],
    ["2 × SQS standard queue", "visibility 120 s, long polling 20 s, retention 1 day", "Job queue per group"],
    ["DynamoDB table", "carbonshift-results, on-demand, key job_id", "Heartbeats and job results"],
    ["IAM role + instance profile", "SQS receive/delete on carbonshift-jobs-*, DynamoDB PutItem on the table, SSM", "Least-privilege worker access"],
    ["Security group", "no inbound rules, outbound only", "No SSH; nothing can connect in"],
    ["AWS Budget", "$10 monthly alert", "Cost safety net"],
  ], [2300, 4300, 2426], "AWS resources used by CarbonShift (region ap-southeast-2)."),
  P(`**Worker.** Each VM boots with a cloud-init script that installs boto3 and runs worker.py as a systemd service. The worker long-polls its group's queue, runs one job at a time ` +
    `(a CPU-bound Monte-Carlo estimate of π for cpu_seconds divided by the time-compression factor), writes the timings and result to DynamoDB, deletes the message and writes a heartbeat on start. ` +
    `A smoke test confirmed that a 300 CPU-second job ran for exactly 5.00 s at 60× compression on a VM of the correct group.`),
  figure(shot("aws_ec2_instances.png"), "EC2 console: the four CarbonShift workers (two per group) as the controller switches them on and off.", 6.3),
  figure(shot("aws_cli_evidence.png"), "AWS CLI evidence of the queues, results table, security group (zero inbound rules) and least-privilege IAM role.", 6.3),
  figure(shot("aws_dynamodb_heartbeats.png"), "DynamoDB console: one heartbeat per VM, written by each worker when it starts.", 4.6),
  figurePair(shot("aws_service_quota.png"), shot("aws_budget.png"), "Left: the account's 8-vCPU on-demand quota, enough for 4 × t3.micro. Right: the $10 budget alert used as a cost guard.", 3.05),
  H2("6.1 Dashboard"),
  P(`A Streamlit dashboard follows a run live (refreshing every 3 s) or replays a finished one. It explains the current decision in one sentence, shows four key numbers, ` +
    `the carbon intensity so far together with the 24-hour forecast the scheduler is using, each VM with its instance ID and EC2 state, jobs per hour, VMs over time, and a timeline of every job.`),
  figure(shot("dash_live.png"), "Dashboard during the CarbonShift run at 14:30 IST: jobs are being held for the cleaner afternoon, both IN-SO VMs are on and the IN-WE VMs are off.", 6.3));

// ---------------------------------------------------------------- 7 simulation evaluation
const order = ["fifo", "fifo_in_so", "simple_carbon", "cs_no_gate", "carbonshift", "oracle"];
const names = { fifo: "FIFO, IN-WE (baseline)", fifo_in_so: "FIFO, IN-SO", simple_carbon: "Simple carbon rule", cs_no_gate: "CarbonShift, no gate", carbonshift: "**CarbonShift**", oracle: "Oracle" };
add(T1("7. Evaluation: full-year simulation"),
  P(`The simulator replays all of 2024 hour by hour (${D.jobs_2024.toLocaleString("en-US")} jobs, two groups of at most two VMs) with the same scheduler code as the live controller. ` +
    `Table 8 compares the policies at a 12-hour deadline slack.`),
  table(["Policy", "CO₂ (kg)", "Saved vs FIFO", "vs Oracle", "Energy (kWh)", "VM util.", "Avg wait (h)", "Missed", "Cost ($)"],
    order.map((p) => { const r = sim(p, 12); return [names[p], f(r.carbon_kg, 1), pct(r.carbon_saved_vs_fifo_pct, 1), `+${f(r.regret_vs_oracle_pct, 1)}%`, f(r.energy_kwh, 1),
      pct(r.avg_util_pct, 0), f(r.avg_wait_h, 1), pct(r.deadline_miss_pct, 2), f(r.cost_usd, 0)]; }),
    [2226, 850, 950, 850, 900, 750, 850, 800, 850], "Full-year results at 12 h slack (prediction, utilisation, latency, energy, carbon and cost).", { highlight: (k) => k === 4 }),
  P(`**Carbon.** CarbonShift emits ${pct(S12.carbon_saved_vs_fifo_pct, 0)} less CO₂ than FIFO and is within ${pct(S12.regret_vs_oracle_pct)} of the oracle. ` +
    `Moving jobs to the cleaner Southern grid alone saves ${pct(sim("fifo_in_so", 12).carbon_saved_vs_fifo_pct, 0)}; shifting them into the midday solar hours adds the rest. ` +
    `**Utilisation and energy.** Packing deferred jobs into fewer, fuller VM-hours raises utilisation from ${pct(sim("fifo", 12).avg_util_pct, 0)} to ${pct(S12.avg_util_pct, 0)} and cuts energy by ` +
    `${pct(100 * (1 - S12.energy_kwh / sim("fifo", 12).energy_kwh), 0)}. **Latency.** The price is an average wait of ${f(S12.avg_wait_h, 1)} h instead of ${f(sim("fifo", 12).avg_wait_h, 1)} h, ` +
    `still well inside the 12 h slack, with ${pct(S12.deadline_miss_pct, 2)} misses. **Cost.** VM cost falls with the VM-hours; total cost is slightly higher only because four disks are provisioned instead of two.`),
  figurePair(res("carbon_vs_delay.png"), res("policy_comparison.png"),
    "Left: carbon saved versus average delay for deadline slacks of 2, 6, 12 and 24 h. Right: carbon, energy, VM-hours and waiting time per policy at 12 h slack.", 3.05),
  table(["Deadline slack", "2 h", "6 h", "12 h", "24 h"], [
    ["CO₂ saved vs FIFO", ...[2, 6, 12, 24].map((s) => pct(sim("carbonshift", s).carbon_saved_vs_fifo_pct, 1))],
    ["Average wait (h)", ...[2, 6, 12, 24].map((s) => f(sim("carbonshift", s).avg_wait_h, 1))],
    ["Deadline misses", ...[2, 6, 12, 24].map((s) => pct(sim("carbonshift", s).deadline_miss_pct, 2))],
  ], [2826, 1550, 1550, 1550, 1550], "CarbonShift across deadline slack values: more patience, more carbon saved."),
  P(`With only 2 h of slack no job can wait for a cleaner hour, so all of the saving comes from choosing the cleaner group. ` +
    `A notable and honest finding is that the ML forecast is only slightly better than persistence _for scheduling_: the simple carbon rule reaches ${pct(sim("simple_carbon", 12).carbon_saved_vs_fifo_pct, 1)} ` +
    `against ${pct(S12.carbon_saved_vs_fifo_pct, 1)} for CarbonShift and ${pct(sim("oracle", 12).carbon_saved_vs_fifo_pct, 1)} for the oracle. India's grid repeats almost the same daily shape, ` +
    `so even a naive forecast ranks the hours correctly; the ML model mainly helps on unusual days and with lower waiting time.`));

// ---------------------------------------------------------------- 8 live AWS evaluation
const liveRow = (k, name) => { const r = L[k] || {}; return [name, r.jobs ?? "–", pct(r.deadline_miss_pct, 1), f(r.avg_wait_h, 2), f(r.vm_hours, 0), pct(r.avg_util_pct, 0),
  f(r.carbon_g, 1), f(r[`sim_${r.policy}_carbon_g`], 1), `$${f(r.real_aws_cost_usd, 3)}`]; };
add(T1("8. Evaluation: live runs on AWS"),
  P(`The controller replayed **Tuesday 5 March 2024** (an ordinary weekday; ${LC.jobs ?? 210} jobs, 6 h slack) on the real VMs at 60× speed (one simulated hour per real minute), ` +
    `once with CarbonShift and once with FIFO, and replayed **Friday 8 March 2024 with a workload burst** (3× arrivals from 10:00 to 16:00) with CarbonShift. ` +
    `Every job travelled through SQS, ran on an EC2 worker and reported to DynamoDB.`),
  table(["Run (real AWS)", "Jobs", "Missed", "Avg wait (h)", "VM-hours", "Util.", "CO₂ (g)", "Sim. (g)", "AWS cost"],
    [liveRow("carbonshift", "**CarbonShift, 5 Mar**"), liveRow("fifo", "FIFO, 5 Mar"), liveRow("burst", "CarbonShift, burst 8 Mar")],
    [2326, 650, 850, 900, 900, 700, 900, 1000, 800], "Measured results of the three final live runs on AWS.", { highlight: (k) => k === 0 }),
  P(`On identical jobs and conditions, CarbonShift used **${f(liveSaved, 0)}% less CO₂ than FIFO on real VMs** (${f(LC.carbon_g, 0)} g vs ${f(LF.carbon_g, 0)} g) with ` +
    `${pct(LC.deadline_miss_pct, 1)} missed deadlines, at the cost of a longer average wait (${f(LC.avg_wait_h, 1)} h vs ${f(LF.avg_wait_h, 1)} h). ` +
    `${f(LC.share_in_so_pct, 0)}% of its jobs ran in the Southern group. In the burst run the IN-SO group became saturated and the scheduler used the **IN-WE VMs as overflow** ` +
    `(${LB.jobs_in_we ?? "–"} of ${LB.jobs ?? "–"} jobs, up to ${LB.peak_we_vms ?? "–"} IN-WE VMs on), keeping misses at ${pct(LB.deadline_miss_pct, 1)}. Each run took about 25 real minutes and cost about one US cent.`),
  figure(shot("live_comparison.png"), "CO₂ of the live runs measured on AWS compared with the simulator's prediction for exactly the same jobs.", 5.0),
  P(`**Real cloud vs simulation.** Measured CO₂ is higher than the simulator predicts because real VMs must be started before work arrives and kept on briefly afterwards; ` +
    `an idle VM still draws about 2 W. The simulator ignores boot time and switches VMs on only in the hours they run jobs. This overhead affects every policy, ` +
    `which is why the fair comparison is CarbonShift against FIFO on the same real VMs, as in Table 10.`),
  figure(shot("dash_burst.png"), "Dashboard during the burst run: the Southern group is full, so the Western-group VMs are switched on for overflow jobs.", 6.3),
  figure(shot("dash_results.png"), "Dashboard results tab: the three live runs side by side.", 6.0),
  figure(shot("controller_log.png"), "Controller output during the CarbonShift live run: per simulated hour, jobs released per group, jobs held, VM targets, progress and CO₂.", 6.3),
  H2("8.1 Engineering issues found on real hardware"),
  P(`The live runs exposed three problems that the simulator could not show. (i) **Stop/start thrash:** VMs stopped as soon as an hour had no work could not be restarted ` +
    `while still "stopping", delaying jobs by an hour; a cool-down fixed it and brought the average wait back to the simulator's value. (ii) **Queue backlog:** when a VM was still booting, ` +
    `jobs spilled into the next hour and an urgent job was queued behind them and missed its deadline by 41 minutes (${pct((D.first_live_run || {}).deadline_miss_pct, 1)} misses in that run). ` +
    `Counting queued work before sending more fixed this. (iii) **Controller stall:** in one run, a single AWS API call from the laptop hung for about three minutes; ` +
    `the controller then replayed three simulated hours within seven seconds, leaving the VMs no time to finish and causing ${pct((D.stalled_run || {}).deadline_miss_pct, 1)} misses. ` +
    `The controller now pauses the simulated clock when it falls behind instead of skipping hours, and that run was repeated (Table 10). ` +
    `These issues are typical of real autoscalers and are a useful lesson of deploying on a real cloud.`));

// ---------------------------------------------------------------- 9 failure analysis
const fr = (sc, p, s) => fail(sc, p, s);
add(T1("9. What happens when the prediction is wrong"),
  P(`We injected two kinds of forecast error into the full-year simulation, once a week each, and compared CarbonShift with and without its two safety mechanisms:`),
  bullet("**Surprise cloudy day:** actual carbon intensity is 25% higher than forecast from 08:00 to 18:00 IST in both grids; the forecasters only learn about it once it is observed."),
  bullet("**Workload burst:** 3× the normal arrival rate for six hours from 10:00 IST, which the workload forecaster does not predict."),
  table(["Scenario (slack)", "Policy", "Extra CO₂ vs oracle", "Missed (all jobs)", "Missed (jobs near event)", "Avg wait (h)"], [
    ["None (12 h)", "CarbonShift", `+${f(fr("none", "carbonshift", 12).regret_vs_oracle_pct)}%`, pct(fr("none", "carbonshift", 12).deadline_miss_pct, 2), "–", f(fr("none", "carbonshift", 12).avg_wait_h, 1)],
    ["Cloudy (12 h)", "CarbonShift", `+${f(fr("cloudy", "carbonshift", 12).regret_vs_oracle_pct)}%`, pct(fr("cloudy", "carbonshift", 12).deadline_miss_pct, 2), pct(fr("cloudy", "carbonshift", 12).evt_deadline_miss_pct, 2), f(fr("cloudy", "carbonshift", 12).avg_wait_h, 1)],
    ["Cloudy (12 h)", "Adaptive gate", `+${f(fr("cloudy", "cs_adaptive_gate", 12).regret_vs_oracle_pct)}%`, pct(fr("cloudy", "cs_adaptive_gate", 12).deadline_miss_pct, 2), pct(fr("cloudy", "cs_adaptive_gate", 12).evt_deadline_miss_pct, 2), f(fr("cloudy", "cs_adaptive_gate", 12).avg_wait_h, 1)],
    ["Burst (6 h)", "FIFO", `+${f(fr("burst", "fifo", 6).regret_vs_oracle_pct)}%`, pct(fr("burst", "fifo", 6).deadline_miss_pct, 2), pct(fr("burst", "fifo", 6).evt_deadline_miss_pct, 1), f(fr("burst", "fifo", 6).avg_wait_h, 1)],
    ["Burst (6 h)", "**CarbonShift**", `+${f(fr("burst", "carbonshift", 6).regret_vs_oracle_pct)}%`, pct(fr("burst", "carbonshift", 6).deadline_miss_pct, 3), pct(fr("burst", "carbonshift", 6).evt_deadline_miss_pct, 3), f(fr("burst", "carbonshift", 6).avg_wait_h, 1)],
    ["Burst (6 h)", "No buffer", `+${f(fr("burst", "cs_no_buffer", 6).regret_vs_oracle_pct)}%`, pct(fr("burst", "cs_no_buffer", 6).deadline_miss_pct, 3), pct(fr("burst", "cs_no_buffer", 6).evt_deadline_miss_pct, 3), f(fr("burst", "cs_no_buffer", 6).avg_wait_h, 1)],
    ["Burst (6 h)", "No gate, no buffer", `+${f(fr("burst", "cs_no_gate_no_buffer", 6).regret_vs_oracle_pct)}%`, pct(fr("burst", "cs_no_gate_no_buffer", 6).deadline_miss_pct, 3), pct(fr("burst", "cs_no_gate_no_buffer", 6).evt_deadline_miss_pct, 3), f(fr("burst", "cs_no_gate_no_buffer", 6).avg_wait_h, 1)],
  ], [1600, 1900, 1400, 1300, 1526, 1300], "Failure injection results (full-year simulation; \"near event\" = jobs whose deadline window overlaps an injected event).", { highlight: (k) => k === 4 }),
  figure(res("failure_injection.png"), "Failure injection: extra carbon versus the oracle, deadline misses and waiting time, with and without the confidence gate and safety buffer.", 6.3),
  P(`**Wrong carbon forecast.** On surprise cloudy days CarbonShift's gap to the oracle grows from ${f(fr("none", "carbonshift", 12).regret_vs_oracle_pct)}% to ${f(fr("cloudy", "carbonshift", 12).regret_vs_oracle_pct)}%, ` +
    `but no deadline is missed: re-planning every hour uses the observed values as soon as they arrive, and the cost of the error is bounded because jobs deferred to a "clean" hour run there anyway. ` +
    `We also tested an **adaptive gate** that stops trusting forecasts after a large error. It backfired (+${f(fr("cloudy", "cs_adaptive_gate", 12).regret_vs_oracle_pct)}%): "run now" during a cloudy midday ` +
    `meant running in the dirtiest hours. We therefore kept the simple gate, which trades a little carbon for lower waiting time.`),
  P(`**Wrong workload forecast.** During bursts FIFO, confined to one group, missed ${pct(fr("burst", "fifo", 6).evt_deadline_miss_pct, 0)} of the affected jobs. CarbonShift spreads the load across ` +
    `both groups and over time and missed ${pct(fr("burst", "carbonshift", 6).evt_deadline_miss_pct, 2)}; removing the **safety buffer** doubles the misses and removing both mechanisms quadruples them. ` +
    `The live burst run on AWS (Section 8) confirmed this behaviour on real VMs, with the Western-group VMs switched on as overflow capacity.`));

// ---------------------------------------------------------------- 10 discussion
add(T1("10. Discussion and limitations"),
  bullet("**Region choice dominates.** Because the Southern grid was cleaner in every hour of 2024, most of the saving comes from placing work there; time shifting adds the rest. In regions without such a gap, time shifting would matter more."),
  bullet("**Data.** About 96% of the carbon values are Electricity Maps estimates, and the workload is synthetic (but seeded and realistic in shape). Results should be validated on measured data and real job traces."),
  bullet("**Replay instead of live data.** The live demo replays 2024 at 60× speed so decisions can be scored against the recorded truth and policies compared on identical inputs. Connecting to a live carbon-intensity API is future work."),
  bullet("**Single AWS region.** The free plan allows only ap-southeast-2, so both groups run in Sydney and are bound to Indian grid traces. This does not change the scheduling logic, but a production system would use real regions (e.g. ap-south-1 and ap-south-2)."),
  bullet("**Energy model.** The Cloud Carbon Footprint linear power model and hourly VM billing granularity are approximations; embodied emissions and network energy are not included."),
  bullet("**Real-cloud overhead.** VM boot time and the cool-down add idle VM-hours that a simulation ignores; a pool of always-warm VMs or faster-starting containers would reduce it."));

// ---------------------------------------------------------------- 11 conclusion
add(T1("11. Conclusion and future work"),
  P(`CarbonShift shows that a small ML-based resource manager can cut the carbon footprint of deferrable cloud batch work substantially without breaking deadlines. ` +
    `XGBoost forecasts 1–24 hours ahead with ${f(cs("IN-WE", "xgboost").MAPE_avg, 1)}–${f(cs("IN-SO", "xgboost").MAPE_avg, 1)}% error; the rolling-horizon scheduler saves ` +
    `${pct(S12.carbon_saved_vs_fifo_pct, 0)} CO₂ over a full simulated year while staying within ${pct(S12.regret_vs_oracle_pct)} of an oracle; and on real AWS VMs it used ` +
    `${f(liveSaved, 0)}% less CO₂ than FIFO for the same jobs. When forecasts are wrong, hourly re-planning, the confidence gate and the safety buffer keep the damage small.`),
  P(`Future work: live carbon-intensity data, real multi-region deployment, uncertainty-aware (quantile) forecasts in the gate, containers or a warm pool to cut boot overhead, and real job traces.`));

// ---------------------------------------------------------------- references
add(T1("References"),
  ...[
    "Electricity Maps. Hourly carbon intensity datasets, zones IN-WE and IN-SO, 2021–2024. https://portal.electricitymaps.com",
    "Chen, T. and Guestrin, C. (2016). XGBoost: A Scalable Tree Boosting System. Proceedings of KDD '16, 785–794.",
    "Wiesner, P., Behnke, I., Scheinert, D., Gontarska, K. and Thamsen, L. (2021). Let's Wait Awhile: How Temporal Workload Shifting Can Reduce Carbon Emissions in the Cloud. Proceedings of Middleware '21.",
    "Radovanović, A. et al. (2023). Carbon-Aware Computing for Datacenters. IEEE Transactions on Power Systems, 38(2).",
    "Cloud Carbon Footprint. Methodology (energy coefficients and PUE for AWS). https://www.cloudcarbonfootprint.org/docs/methodology",
    "Amazon Web Services. Amazon EC2 User Guide; Amazon SQS Developer Guide; Amazon DynamoDB Developer Guide; AWS Price List API. https://docs.aws.amazon.com",
  ].map((t, i) => P(`[${i + 1}] ${t}`, { align: AlignmentType.LEFT, after: 80 })));

// ---------------------------------------------------------------- appendix
add(T1("Appendix A. How to reproduce"),
  ...[
    "python -m src.data.load_carbon; python -m src.data.eda    # Phase 1: data, EDA",
    "python -m src.models.train; python -m src.models.workload # forecasters",
    "python -m src.sim.simulator                               # Phase 2: simulation",
    "infra/create.sh --plan | infra/vms.sh status | infra/teardown.sh",
    "python -m src.cloud.controller --policy carbonshift --yes # live AWS run",
    "python -m src.cloud.controller --seconds-per-hour 20 --prewarm   # demo speed",
    "streamlit run src/dashboard/app.py                        # dashboard",
  ].map((t) => new Paragraph({ children: [new TextRun({ text: t, font: "Courier New", size: 16 })], spacing: { after: 30 }, alignment: AlignmentType.LEFT })),
  P(`All tunables (regions, prices, energy coefficients, scheduler weights, time compression) are in config.yaml; random seeds are fixed. ${D.tests.count} automated tests cover feature leakage, ` +
    `simulator invariants (no job before submission, no VM above 100%, work conserved, deadlines met) and the controller end to end with fake VMs.`, { before: 120 }));

// ---------------------------------------------------------------- table of contents (static, page numbers from the previous render)
const tocParas = [new Paragraph({ children: [new TextRun({ text: "Contents", bold: true, size: 30, color: BLUE })], spacing: { before: 360, after: 160 } }),
  ...tocEntries.map(([lvl, t]) => new Paragraph({
    indent: { left: lvl ? 400 : 0 }, spacing: { after: lvl ? 40 : 90 },
    tabStops: [{ type: TabStopType.RIGHT, position: W, leader: LeaderType.DOT }],
    children: [new TextRun({ text: t, bold: !lvl, size: lvl ? 20 : 22 }),
      new TextRun({ children: [new Tab(), String(TOC[t] ?? "")], size: lvl ? 20 : 22 })] }))];

// ---------------------------------------------------------------- document
const doc = new Document({
  creator: "Tanish Gupta, Tanish Shah, Tanisha Mukherjee", title: "CarbonShift – Lab IA Report",
  styles: {
    default: { document: { run: { font: FONT, size: 21 }, paragraph: { spacing: { line: 270 } } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 30, bold: true, color: BLUE, font: FONT }, paragraph: { spacing: { before: 320, after: 140 }, outlineLevel: 0, keepNext: true } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, color: "2E5C8A", font: FONT }, paragraph: { spacing: { before: 220, after: 100 }, outlineLevel: 1, keepNext: true } },
    ],
  },
  numbering: { config: [
    { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 500, hanging: 260 } } } }] },
    { reference: "steps", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 500, hanging: 300 } } } }] },
  ] },
  sections: [
    { properties: { page: { margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } } }, children: cover },
    { properties: { page: { margin: { top: 1300, bottom: 1300, left: 1440, right: 1440 }, pageNumbers: { start: 1 } } },
      headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT,
        children: [new TextRun({ text: "CarbonShift · Lab IA · Advanced Cloud Computing", size: 16, color: "888888" })] })] }) },
      footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
        children: [new TextRun({ children: ["Page ", PageNumber.CURRENT], size: 18, color: "666666" })] })] }) },
      children: [...front, ...tocParas, ...body] },
  ],
});
Packer.toBuffer(doc).then((buf) => {
  const out = path.join(ROOT, "report", "CarbonShift_Report.docx");
  fs.writeFileSync(out, buf);
  fs.writeFileSync(path.join(B, "toc_entries.json"), JSON.stringify(tocEntries.map((e) => e[1])));
  console.log("wrote", out, "| figures", figN, "| tables", tabN);
});
