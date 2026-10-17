"""CarbonShift dashboard (Streamlit).

    .venv/bin/streamlit run src/dashboard/app.py

Tabs
  Live demo    follows a controller run as it happens (auto-refresh), or replays a finished one
  Results      every finished live run side by side (CarbonShift vs FIFO on real AWS VMs)
  Simulation   Phase 2: full-year policy comparison and failure tests
  Forecasting  Phase 1: grid patterns and forecast accuracy
Reads only files under results/ (written by src.cloud.controller and the Phase 1–2 scripts).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from src.utils import load_config  # noqa: E402

CFG = load_config()
RESULTS = ROOT / CFG["paths"]["results"]
LIVE = RESULTS / "live"
TZ = CFG["timezone_display"]
ZONES = [r["zone"] for r in CFG["regions"]]
N_VM = CFG["vm"]["max_per_region"]
YEAR0 = pd.Timestamp(CFG["sim"]["start"], tz="UTC")
# same colours as the report charts: blue = Western grid, orange = Southern grid
ZONE_COLOR = {"IN-WE": "#2a78d6", "IN-SO": "#eb6834"}
ZONE_NAME = {"IN-WE": "Western grid (IN-WE)", "IN-SO": "Southern grid (IN-SO)"}
GREY = "#8c8b86"
GREEN = "#1baf7a"

st.set_page_config(page_title="CarbonShift", page_icon="🌿", layout="wide")


def theme_switch() -> None:
    """Dark/light toggle. Streamlit re-sends the theme config on every rerun, so setting it and
    rerunning restyles the whole page (charts and tables included)."""
    if "dark" not in st.session_state:
        st.session_state["dark"] = getattr(getattr(st.context, "theme", None), "type", "light") == "dark"
    want = "dark" if st.session_state["dark"] else "light"
    if st.get_option("theme.base") != want:
        st._config.set_option("theme.base", want)
        st.rerun()


if "view" in st.query_params:              # screenshot mode: always light
    if st.get_option("theme.base") != "light":
        st._config.set_option("theme.base", "light")
        st.rerun()
else:
    theme_switch()
st.markdown("""
<style>
.block-container {padding-top: 2rem; max-width: 1400px;}
.cs-story {border-left: 4px solid #1baf7a; padding: .8rem 1.1rem; border-radius: 6px;
           background: rgba(27,175,122,.08); font-size: 1.05rem; line-height: 1.6;}
.cs-clock {font-size: 1.6rem; font-weight: 700; line-height: 1.2;}
.cs-sub {opacity: .7; font-size: .9rem;}
.vm-row {display: flex; align-items: center; gap: .6rem; margin: .55rem 0;}
.vm-label {width: 11.5rem; font-size: .95rem;}
.vm {width: 4.4rem; height: 2.6rem; border-radius: 8px; display: flex; align-items: center;
     justify-content: center; font-size: .8rem; font-weight: 600;}
.vm {flex-direction: column; line-height: 1.15;}
.vm small {font-weight: 400; font-size: .68rem; opacity: .85;}
.vm.running {color: white;}
.vm.pending {color: white; opacity: .55;}
.vm.stopping {border: 2px dashed; opacity: .8;}
.vm.stopped {border: 2px dashed rgba(140,139,134,.6); opacity: .65;}
.vm {width: 6.2rem; height: 3rem;}
div[data-testid="stMetricValue"] {font-size: 2rem;}
</style>""", unsafe_allow_html=True)


# ---------------------------------------------------------------- data
def list_runs(include_dry: bool = False) -> list[Path]:
    runs = [p for p in LIVE.glob("*") if (p / "timeline.csv").exists()]
    if not include_dry:
        runs = [p for p in runs if not p.name.endswith("-dry")]
    return sorted(runs, key=lambda p: p.stat().st_mtime, reverse=True)


def read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return pd.DataFrame()


def read_summary(run: Path) -> dict | None:
    p = run / "summary.json"
    return json.loads(p.read_text()) if p.exists() else None


@st.cache_resource(show_spinner="Loading forecasts…")
def sim_inputs(scenario: str):
    p = ROOT / CFG["paths"]["processed"] / f"sim_inputs_{scenario}.joblib"
    return joblib.load(p) if p.exists() else None


def to_ist(sim_hours) -> pd.Series:
    """Hours since 1 Jan 2024 (UTC) -> IST datetimes (naive) for plotting."""
    ts = YEAR0 + pd.to_timedelta(np.asarray(sim_hours, dtype=float), unit="h")
    return pd.Series(ts).dt.tz_convert(TZ).dt.tz_localize(None)


def hhmm(sim_hour: float) -> str:
    return to_ist([sim_hour]).iloc[0].strftime("%H:%M")


def run_label(run: Path) -> str:
    s = read_summary(run) or {}
    parts = run.name.split("-")
    policy = {"carbonshift": "CarbonShift", "fifo": "FIFO"}.get(parts[0], parts[0])
    when = time.strftime("%d %b %H:%M", time.localtime(run.stat().st_mtime))
    speed = f"{int(s['seconds_per_sim_hour'])} s/h" if s else "running"
    return f"{policy} · {parts[1]} · {when} · {speed}"


def style(fig: go.Figure, height: int = 300, ytitle: str = "") -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=8, b=8), hovermode="x unified",
                      font=dict(size=13), yaxis_title=ytitle, xaxis_title=None,
                      legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="left", x=0))
    fig.update_xaxes(showgrid=False, tickformat="%a %H:%M")
    return fig


def chart(fig: go.Figure, key: str) -> None:
    st.plotly_chart(fig, width="stretch", key=key, config={"displayModeBar": False})


# ---------------------------------------------------------------- live demo pieces
def story(row: pd.Series, inp, finished: bool, summary: dict | None, states: dict) -> str:
    """One plain-English paragraph describing what is happening right now."""
    t = int(row["sim_hour"])
    ci = {z: row[f"ci_actual_{z}"] for z in ZONES}
    clean = min(ci, key=ci.get)
    dirty = max(ci, key=ci.get)
    vms = ", ".join(f"<b>{sum(st == 'running' for st, _ in states[z])}</b> running in {z}" for z in ZONES)
    if finished and summary:
        pol = summary["policy"]
        sim_fifo = summary.get("sim_fifo_carbon_g")
        extra = (f" Running the same jobs immediately (FIFO) would have emitted about <b>{sim_fifo:.0f} g</b>, so "
                 f"CarbonShift saved about <b>{100 * (1 - summary['carbon_g'] / sim_fifo):.0f}%</b>."
                 if sim_fifo and pol != "fifo" else "")
        return (f"✅ <b>Run finished and all VMs stopped.</b> <b>{summary['jobs_finished']}</b> of {summary['jobs']} jobs ran on real AWS VMs, "
                f"<b>{summary['deadline_miss_pct']:.0f}%</b> missed their deadline, average wait "
                f"<b>{summary['avg_wait_h']:.1f} h</b>, total <b>{summary['carbon_g']:.0f} g CO₂</b>.{extra}")
    s = (f"🕐 It is <b>{hhmm(t)}</b> on the replayed day. The <b>{ZONE_NAME[clean]}</b> is cleaner right now "
         f"(<b>{ci[clean]:.0f}</b> vs {ci[dirty]:.0f} gCO₂/kWh). ")
    if inp is not None and t < inp.fc["xgboost"].shape[1]:
        r = inp.zones.index(clean)
        fc = inp.fc["xgboost"][r, t, :12]
        k = int(np.nanargmin(fc))
        if fc[k] < ci[clean] - 10:
            s += f"The forecast says it will drop to <b>{fc[k]:.0f}</b> around <b>{hhmm(t + k)}</b>. "
    waiting = int(row["waiting"])
    sent = sum(int(row[f"sent_{z}"]) for z in ZONES)
    s += (f"So the scheduler is <b>holding {waiting} jobs</b> for a cleaner hour and released <b>{sent}</b> this hour. "
          if waiting else f"No jobs are waiting; it released <b>{sent}</b> this hour. ")
    return s + f"VMs: {vms}."


VM_TEXT = {"running": "ON", "pending": "starting…", "stopping": "stopping…", "stopped": "off"}


def vm_states(row: pd.Series, finished: bool) -> dict[str, list[tuple[str, str]]]:
    """[(state, instance id)] per group, exactly as recorded from EC2 after each decision.
    Older runs only stored a count; a finished run always ends with every VM stopped."""
    out = {}
    for z in ZONES:
        if f"vm_{z}_1_state" in row.index:
            out[z] = [(str(row[f"vm_{z}_{i}_state"]), str(row[f"vm_{z}_{i}_id"])) for i in range(1, N_VM + 1)]
        else:
            on = 0 if finished else int(row[f"vms_{z}"])
            out[z] = [("running" if i < on else "stopped", "") for i in range(N_VM)]
    return out


def vm_panel(states: dict[str, list[tuple[str, str]]]) -> str:
    html = ""
    for z in ZONES:
        boxes = ""
        for i, (state, iid) in enumerate(states[z]):
            fill = f"background:{ZONE_COLOR[z]};" if state in ("running", "pending") else f"border-color:{ZONE_COLOR[z]};" \
                if state == "stopping" else ""
            short = f"<small>{iid[:2]}…{iid[-4:]}</small>" if iid.startswith("i-") else ""
            boxes += f'<div class="vm {state}" style="{fill}">VM {i + 1} {VM_TEXT.get(state, state)}{short}</div>'
        html += f'<div class="vm-row"><div class="vm-label">{ZONE_NAME[z]}</div>{boxes}</div>'
    return html


def carbon_chart(tl: pd.DataFrame, inp, finished: bool) -> go.Figure:
    """Solid = what happened so far; dotted = the 24 h forecast the scheduler is using now."""
    t = int(tl["sim_hour"].iloc[-1])
    x_past = to_ist(tl["sim_hour"])
    now = x_past.iloc[-1]
    fig = go.Figure()
    for r, z in enumerate(ZONES):
        fig.add_trace(go.Scatter(x=x_past, y=tl[f"ci_actual_{z}"], name=f"{z} so far", mode="lines",
                                 line=dict(color=ZONE_COLOR[z], width=3)))
        if not finished and inp is not None and t < inp.fc["xgboost"].shape[1]:
            fig.add_trace(go.Scatter(x=to_ist(np.arange(t, t + 24)), y=inp.fc["xgboost"][r, t],
                                     name=f"{z} forecast", mode="lines",
                                     line=dict(color=ZONE_COLOR[z], width=2.5, dash="dot")))
    if not finished:
        fig.add_vrect(x0=now, x1=now + pd.Timedelta(hours=24), fillcolor=GREY, opacity=0.08, line_width=0)
        fig.add_vline(x=now, line=dict(color=GREY, width=1.5))
        fig.add_annotation(x=now, y=1, yref="paper", text="now", showarrow=False, xanchor="left",
                           yanchor="top", font=dict(color=GREY))
        fig.add_annotation(x=now + pd.Timedelta(hours=12), y=1, yref="paper", text="forecast →", showarrow=False,
                           yanchor="top", font=dict(color=GREY))
    return style(fig, 340, "gCO₂eq per kWh")


def jobs_chart(tl: pd.DataFrame) -> go.Figure:
    x = to_ist(tl["sim_hour"])
    fig = go.Figure()
    for z in ZONES:
        fig.add_trace(go.Bar(x=x, y=tl[f"sent_{z}"], name=f"started in {z}", marker_color=ZONE_COLOR[z]))
    fig.add_trace(go.Scatter(x=x, y=tl["waiting"], name="held for a cleaner hour", mode="lines",
                             line=dict(color=GREY, width=2.5, dash="dash")))
    fig.update_layout(barmode="stack", bargap=0.2)
    return style(fig, 280, "jobs")


def vms_chart(tl: pd.DataFrame) -> go.Figure:
    x = to_ist(tl["sim_hour"])
    fig = go.Figure()
    for z in ZONES:
        fig.add_trace(go.Scatter(x=x, y=tl[f"vms_{z}"], name=z, mode="lines", line_shape="hv",
                                 line=dict(color=ZONE_COLOR[z], width=3)))
    fig.update_yaxes(range=[-0.15, N_VM + 0.3], dtick=1)
    return style(fig, 280, "VMs on")


def job_timeline(jobs: pd.DataFrame, now_h: float, t_start: float, t_end: float) -> go.Figure:
    """One row per job (in order of arrival): thin grey while waiting, thick colour while running."""
    j = jobs[jobs["submit_h"] <= now_h].sort_values("submit_h").reset_index(drop=True)
    fig = go.Figure()
    # sunny hours (07:00-17:00 IST) for context: that is when the grid is cleanest
    day0 = to_ist([t_start]).iloc[0].normalize()
    for d in range(2):
        fig.add_vrect(x0=day0 + pd.Timedelta(days=d, hours=7), x1=day0 + pd.Timedelta(days=d, hours=17),
                      fillcolor="#eda100", opacity=0.07, line_width=0)
    fig.add_annotation(x=day0 + pd.Timedelta(hours=12), y=1.02, yref="paper", text="☀ sunny hours (cleaner grid)",
                       showarrow=False, font=dict(color=GREY, size=12))

    def segments(starts, ends, rows):
        xs, ys = [], []
        for s0, e0, y in zip(starts, ends, rows):
            xs += [s0, e0, np.nan]
            ys += [y, y, None]
        return to_ist(xs), ys

    wait_end = np.minimum(np.where(np.isfinite(j["start_h"]), j["start_h"], now_h), now_h)
    x, y = segments(j["submit_h"], wait_end, j.index)
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name="waiting for cleaner power",
                             line=dict(color="rgba(140,139,134,0.45)", width=1.2), hoverinfo="skip"))
    for z in ZONES:
        d = j[(j["group"] == z) & np.isfinite(j["start_h"]) & (j["start_h"] <= now_h)]
        if d.empty:
            continue
        x, y = segments(d["start_h"], np.maximum(d["finish_h"], d["start_h"] + 0.25), d.index)
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name=f"running in {ZONE_NAME[z]}",
                                 line=dict(color=ZONE_COLOR[z], width=5), hoverinfo="skip"))
        info = np.stack([d["job_id"], [hhmm(v) for v in d["submit_h"]], [hhmm(v) for v in d["start_h"]],
                         (d["start_h"] - d["submit_h"]).round(1)], axis=-1)
        fig.add_trace(go.Scatter(x=to_ist(d["start_h"]), y=d.index, mode="markers", showlegend=False,
                                 marker=dict(color=ZONE_COLOR[z], size=6), customdata=info,
                                 hovertemplate=f"<b>%{{customdata[0]}}</b><br>arrived %{{customdata[1]}}<br>"
                                               f"started %{{customdata[2]}} in {z}<br>waited %{{customdata[3]}} h"
                                               "<extra></extra>"))
    fig.update_yaxes(showticklabels=False, showgrid=False, title="jobs, in order of arrival ↑", zeroline=False)
    fig.update_xaxes(range=[to_ist([t_start]).iloc[0], to_ist([t_end]).iloc[0]])
    fig.update_layout(hovermode="closest")
    return style(fig, 460)


def render_run(run: Path, upto: int | None = None, key: str = "v") -> None:
    tl, jobs, summary = read_csv(run / "timeline.csv"), read_csv(run / "jobs.csv"), read_summary(run)
    if tl.empty:
        st.info("Waiting for the first simulated hour of this run…")
        return
    last = len(tl) - 1
    upto = last if upto is None else min(upto, last)
    view = tl.iloc[: upto + 1]
    row = view.iloc[-1]
    finished = summary is not None and upto == last
    states = vm_states(row, finished)
    scenario = (summary or {}).get("scenario", run.name.split("-")[1])
    inp = sim_inputs(scenario)

    # 1. clock + progress
    c1, c2 = st.columns([3, 2], vertical_alignment="center")
    day = to_ist([row["sim_hour"]]).iloc[0].strftime("%a %d %b %Y")
    c1.markdown(f'<div class="cs-clock">🕐 {hhmm(row["sim_hour"])} IST</div>'
                f'<div class="cs-sub">Replayed day: {day} · simulated hour {upto + 1} of {len(tl)}</div>',
                unsafe_allow_html=True)
    if summary is not None and upto == last:
        status = "✅ finished · all VMs stopped"
    elif summary is not None:
        status = "⏪ replaying a finished run"
    else:
        age = time.time() - float(tl["real_time"].iloc[-1])
        step = float(np.median(np.diff(tl["real_time"]))) if len(tl) > 2 else 60.0
        status = (f"⚠️ no update for {age / 60:.0f} min: controller stopped? Check: infra/vms.sh status"
                  if age > 3 * step + 30 else "🟢 running on AWS…")
    total = len(tl) if summary is not None else max(len(tl), 24)
    c2.progress(1.0 if summary is not None and upto == last else min(1.0, (upto + 1) / total), text=status)

    # 2. the story
    st.markdown(f'<div class="cs-story">{story(row, inp, finished, summary, states)}</div>', unsafe_allow_html=True)
    st.write("")

    # 3. four big numbers
    k = st.columns(4)
    n = len(jobs)
    k[0].metric("Jobs finished", f"{int(row['jobs_done'])} / {n}", border=True)
    k[1].metric("Held for a cleaner hour", int(row["waiting"]), border=True)
    k[2].metric("Deadlines missed", int(row["missed_so_far"]), border=True)
    k[3].metric("CO₂ so far", f"{row['carbon_g_so_far']:.1f} g", border=True)

    # 4. carbon chart + VMs
    a, b = st.columns([2, 1], gap="large")
    with a:
        st.markdown("##### Grid carbon intensity")
        st.caption("Solid: what happened so far · dotted: the 24-hour forecast the scheduler is using now. "
                   "Lower = cleaner electricity.")
        chart(carbon_chart(view, inp, finished), f"{key}-carbon")
    with b:
        st.markdown("##### AWS virtual machines")
        st.caption("Each box is one real t3.micro VM with its instance ID, showing the same state as "
                   "EC2 → Instances (starting… / ON / stopping… / off).")
        st.markdown(vm_panel(states), unsafe_allow_html=True)
        if summary and finished:
            st.markdown("##### Cost of this run")
            st.markdown(f"Real AWS cost **${summary['real_aws_cost_usd']:.3f}** · "
                        f"{summary['real_minutes']:.0f} real minutes · {summary['vm_hours']:.0f} VM-hours (simulated)")

    # 5. two small charts
    a, b = st.columns(2, gap="large")
    with a:
        st.markdown("##### Jobs each hour")
        st.caption("Bars: jobs started (colour = where). Dashed: jobs held back waiting for cleaner power.")
        chart(jobs_chart(view), f"{key}-jobs")
    with b:
        st.markdown("##### VMs switched on over time")
        st.caption("Autoscaling: VMs follow the planned work, mostly in the cleaner grid.")
        chart(vms_chart(view), f"{key}-vms")

    st.markdown("##### Every job, one row each")
    st.caption("Rows are jobs in order of arrival. Thin grey line = waiting for cleaner power; "
               "thick coloured bar = running (colour = which grid). Hover a dot for details.")
    chart(job_timeline(jobs, float(row["sim_hour"]) + 1, float(tl["sim_hour"].iloc[0]),
                       float(tl["sim_hour"].iloc[-1]) + 1), f"{key}-timeline")
    if finished:
        with st.expander("Run details (summary.json)"):
            st.json(summary)


def live_tab() -> None:
    runs = list_runs()
    if not runs:
        st.info("No runs yet. Start one in the terminal:\n\n"
                "`.venv/bin/python -m src.cloud.controller --seconds-per-hour 20 --prewarm`")
        return
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    names = [r.name for r in runs]
    if st.session_state.get("run_choice") not in names:
        st.session_state["run_choice"] = names[0]                 # newest run by default
    with c1.popover(f"📂  Run: {run_label(LIVE / st.session_state['run_choice'])}", width="stretch"):
        st.radio("Choose a run (newest first)", names, key="run_choice",
                 format_func=lambda n: run_label(LIVE / n))
    run = LIVE / st.session_state["run_choice"]
    finished = (run / "summary.json").exists()
    mode = c2.segmented_control("Mode", ["Live", "Replay"], default="Replay" if finished else "Live",
                                help="Live refreshes every 3 s while the controller runs. Replay steps through a run.")
    st.divider()
    if mode != "Replay":
        @st.fragment(run_every="3s")
        def live_view():
            render_run(run)
        live_view()
        return
    tl = read_csv(run / "timeline.csv")
    if tl.empty:
        st.info("This run has no data yet.")
        return
    last = len(tl) - 1
    c1, c2 = st.columns([5, 1], vertical_alignment="bottom")
    upto = c1.slider("Replay: drag through the day", 0, last, last, key=f"upto-{run.name}",
                     format="hour %d")
    play = c2.button("▶ Play the day", width="stretch")
    if play:
        holder = st.empty()
        for i in range(last + 1):
            with holder.container():
                render_run(run, i, key=f"p{i}")
            time.sleep(0.6)
    else:
        render_run(run, upto)


# ---------------------------------------------------------------- other tabs
def results_tab() -> None:
    rows = [s for p in list_runs() if (s := read_summary(p))]
    if not rows:
        st.info("No finished runs yet.")
        return
    d = pd.DataFrame(rows)
    d["Policy"] = d["policy"].map({"carbonshift": "CarbonShift", "fifo": "FIFO"}).fillna(d["policy"])
    d["Run"] = d["Policy"] + " · " + d["scenario"] + " · " + d["seconds_per_sim_hour"].astype(int).astype(str) + " s/h"
    pairs = d.groupby(["day", "scenario", "seconds_per_sim_hour"])
    for (day, sc, sph), g in pairs:
        if {"carbonshift", "fifo"} <= set(g.policy):
            cs, ff = g[g.policy == "carbonshift"].iloc[0], g[g.policy == "fifo"].iloc[0]
            k = st.columns(3)
            k[0].metric("CO₂ saved vs FIFO (real AWS)", f"{100 * (1 - cs.carbon_g / ff.carbon_g):.0f}%", border=True)
            k[1].metric("CarbonShift / FIFO CO₂", f"{cs.carbon_g:.0f} g / {ff.carbon_g:.0f} g", border=True)
            k[2].metric("Average wait", f"{cs.avg_wait_h:.1f} h vs {ff.avg_wait_h:.1f} h", border=True)
            st.caption(f"Same day ({day}), same jobs, same speed ({int(sph)} s per simulated hour).")
            break
    st.markdown("##### CO₂ of each run on real AWS VMs")
    fig = go.Figure(go.Bar(x=d["carbon_g"], y=d["Run"], orientation="h",
                           marker_color=[GREEN if p == "carbonshift" else GREY for p in d["policy"]],
                           text=d["carbon_g"].round(0).astype(int).astype(str) + " g", textposition="outside"))
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(range=[0, d["carbon_g"].max() * 1.2])
    chart(style(fig, 90 + 45 * len(d)), "results-bar")
    st.markdown("##### All finished runs")
    st.dataframe(d[["Run", "jobs", "deadline_miss_pct", "avg_wait_h", "vm_hours", "carbon_g", "real_minutes",
                    "real_aws_cost_usd"]].rename(columns={
                        "jobs": "Jobs", "deadline_miss_pct": "Missed %", "avg_wait_h": "Avg wait (h)",
                        "vm_hours": "VM-hours", "carbon_g": "CO₂ (g)", "real_minutes": "Minutes",
                        "real_aws_cost_usd": "AWS cost ($)"}).round(2), width="stretch", hide_index=True)


def figure(name: str, title: str, text: str) -> None:
    p = RESULTS / name
    if p.exists():
        with st.container(border=True):
            st.markdown(f"##### {title}")
            st.caption(text)
            st.image(str(p), width="stretch")


def simulation_tab() -> None:
    st.markdown("A whole year (2024) simulated hour by hour: about **70,000 jobs**, 2 groups, 2 VMs each, "
                "with the same scheduler that drives the live runs.")
    pc = read_csv(RESULTS / "policy_comparison.csv")
    if not pc.empty:
        s = pc[pc.slack_h == 12].set_index("policy")
        if {"carbonshift", "fifo", "oracle"} <= set(s.index):
            k = st.columns(4)
            k[0].metric("CO₂ saved vs FIFO", f"{s.loc['carbonshift', 'carbon_saved_vs_fifo_pct']:.0f}%", border=True)
            k[1].metric("Gap to perfect Oracle", f"{s.loc['carbonshift', 'regret_vs_oracle_pct']:.1f}%", border=True)
            k[2].metric("Deadlines missed", f"{s.loc['carbonshift', 'deadline_miss_pct']:.2f}%", border=True)
            k[3].metric("Average wait", f"{s.loc['carbonshift', 'avg_wait_h']:.1f} h", border=True)
            st.caption("CarbonShift with 12-hour deadlines, compared with running every job immediately (FIFO).")
    a, b = st.columns(2)
    with a:
        figure("carbon_vs_delay.png", "More patience → more carbon saved",
               "Each point is one deadline length (2, 6, 12, 24 h). Further up = more CO₂ saved; further right = jobs wait longer.")
        figure("failure_injection.png", "What if the forecast is wrong?",
               "Surprise cloudy days and sudden job bursts: CarbonShift keeps deadlines while staying close to the best possible.")
    with b:
        figure("policy_comparison.png", "All policies side by side",
               "CO₂, energy, VM-hours and waiting time for each policy over the whole year (12 h deadlines).")
        figure("schedule_timeline_example.png", "One week: FIFO vs CarbonShift",
               "FIFO runs work the moment it arrives; CarbonShift moves it to the cleaner grid and the midday solar hours.")


def forecasting_tab() -> None:
    st.markdown("Hourly grid data for **2021–2024** (Electricity Maps). Models trained on 2021–2023 and tested on 2024.")
    m = read_csv(RESULTS / "carbon_metrics_summary.csv")
    if not m.empty:
        x = m[m.model == "xgboost"].set_index("zone")
        p = m[m.model == "persistence_24h"].set_index("zone")
        k = st.columns(4)
        for i, z in enumerate(ZONES):
            k[2 * i].metric(f"{z} forecast error (avg)", f"{x.loc[z, 'MAE_avg']:.1f} g", border=True)
            k[2 * i + 1].metric(f"{z} better than 'same as yesterday'",
                                f"{100 * (1 - x.loc[z, 'MAE_avg'] / p.loc[z, 'MAE_avg']):.0f}%", border=True)
    a, b = st.columns(2)
    with a:
        figure("eda_hourly_profile.png", "The grid is cleanest at midday",
               "Average carbon intensity by hour of day: solar power lowers it from about 07:00 to 17:00.")
        figure("carbon_error_vs_horizon.png", "How accurate is the forecast?",
               "Average error by how far ahead we predict. Lower is better; XGBoost (yellow) beats every simple method.")
    with b:
        figure("carbon_forecast_example.png", "Forecast vs reality",
               "One week of 2024: the 24-hour-ahead forecast (yellow) follows the real values (black) closely.")
        figure("eda_timeseries.png", "Four years of data",
               "Daily average per grid: the Southern grid is cleaner, and both are cleanest in the monsoon (July).")


# ---------------------------------------------------------------- page
# Screenshot mode for the report: ?view=live|results|simulation|forecasting[&run=<run_id>][&upto=<hour>]
QP = st.query_params
if "view" in QP:
    st.markdown("<style>header, [data-testid='stToolbar'] {display:none !important;}</style>", unsafe_allow_html=True)
    v = QP["view"]
    if v == "live":
        run = LIVE / QP["run"] if "run" in QP else list_runs()[0]
        render_run(run, int(QP["upto"]) if "upto" in QP else None, key="shot")
    else:
        {"results": results_tab, "simulation": simulation_tab, "forecasting": forecasting_tab}[v]()
    st.stop()

h1, h2 = st.columns([6, 1], vertical_alignment="center")
h1.title("🌿 CarbonShift")
h2.toggle("🌙 Dark mode", key="dark")
st.markdown("Runs batch jobs **when** and **where** the electricity is cleanest, using machine-learning forecasts "
            "of grid carbon, on real AWS virtual machines.")
t1, t2, t3, t4 = st.tabs(["🔴 Live demo", "🏁 Results", "🧪 Simulation (whole year)", "📈 Forecasting"])
with t1:
    live_tab()
with t2:
    results_tab()
with t3:
    simulation_tab()
with t4:
    forecasting_tab()
