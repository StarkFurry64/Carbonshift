"""Report-only figures: architecture diagram, AWS CLI evidence and controller-log images."""
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

OUT = Path(__file__).parent / "shots"
INK, INK2, LINE = "#0b0b0b", "#52514e", "#c9c8c3"
BLUE, ORANGE, GREEN, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
plt.rcParams.update({"font.size": 10, "font.family": "DejaVu Sans"})


def box(ax, x, y, w, h, title, lines=(), fc="white", ec=LINE, tc=INK, lw=1.4, title_size=10.5):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.008,rounding_size=0.012", fc=fc, ec=ec, lw=lw))
    ax.text(x + w / 2, y + h - 0.022, title, ha="center", va="top", fontsize=title_size, weight="bold", color=tc)
    for i, ln in enumerate(lines):
        ax.text(x + w / 2, y + h - 0.055 - i * 0.026, ln, ha="center", va="top", fontsize=8.6, color=INK2)


def arrow(ax, a, b, text="", color=INK2, rad=0.0, tx=0, ty=0, style="-|>", ls="-"):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle=style, mutation_scale=13, color=color, lw=1.4,
                                 connectionstyle=f"arc3,rad={rad}", linestyle=ls))
    if text:
        ax.text((a[0] + b[0]) / 2 + tx, (a[1] + b[1]) / 2 + ty, text, ha="center", va="center", fontsize=8.3,
                color=color, bbox=dict(fc="white", ec="none", pad=1.2))


def architecture():
    fig, ax = plt.subplots(figsize=(13, 7.6))
    ax.set_xlim(-0.012, 1.004)
    ax.set_ylim(0, 1)
    ax.axis("off")
    # data sources
    box(ax, 0.005, 0.78, 0.165, 0.15, "Electricity Maps", ["hourly carbon intensity", "IN-WE, IN-SO 2021–2024"],
        fc="#fff8e6", ec=YELLOW, title_size=10)
    box(ax, 0.005, 0.56, 0.165, 0.15, "Job generator", ["seeded Poisson arrivals", "sizes 1–30 CPU-min"],
        fc="#fff8e6", ec=YELLOW, title_size=10)
    # laptop control plane
    ax.add_patch(FancyBboxPatch((0.20, 0.27), 0.37, 0.70, boxstyle="round,pad=0.01,rounding_size=0.02",
                                fc="#f4f8fd", ec=BLUE, lw=1.8, ls="--"))
    ax.text(0.385, 0.96, "Laptop control plane (Python)", ha="center", va="top", fontsize=11.5, weight="bold", color=BLUE)
    box(ax, 0.215, 0.77, 0.158, 0.13, "Carbon forecaster", ["XGBoost, 1–24 h ahead", "one model per hour"], title_size=9.5)
    box(ax, 0.397, 0.77, 0.158, 0.13, "Workload forecaster", ["XGBoost, 1–24 h ahead", "CPU-hours arriving"], title_size=9.5)
    box(ax, 0.215, 0.585, 0.34, 0.14, "CarbonShift scheduler", ["re-plans every hour (rolling horizon)",
                                                                 "carbon score · confidence gate · safety buffer"])
    box(ax, 0.215, 0.43, 0.34, 0.12, "VM controller", ["boto3 · starts VMs ahead of work, stops idle ones",
                                                         "sends jobs, collects results"])
    box(ax, 0.215, 0.29, 0.34, 0.10, "Streamlit dashboard", ["live view · replay · results"], title_size=10)
    arrow(ax, (0.17, 0.855), (0.215, 0.84))
    arrow(ax, (0.17, 0.635), (0.215, 0.65))
    arrow(ax, (0.30, 0.77), (0.33, 0.725))
    arrow(ax, (0.47, 0.77), (0.44, 0.725))
    arrow(ax, (0.385, 0.585), (0.385, 0.55))
    arrow(ax, (0.385, 0.43), (0.385, 0.39))
    # AWS
    ax.add_patch(FancyBboxPatch((0.61, 0.04), 0.385, 0.93, boxstyle="round,pad=0.01,rounding_size=0.02",
                                fc="#fff6f0", ec=ORANGE, lw=1.8, ls="--"))
    ax.text(0.8025, 0.96, "AWS · ap-southeast-2 (free plan)", ha="center", va="top", fontsize=11.5, weight="bold", color=ORANGE)
    groups = [("IN-WE", BLUE, "Western-grid group", 0.70), ("IN-SO", ORANGE, "Southern-grid group", 0.40)]
    for z, c, label, y0 in groups:
        ax.text(0.8025, y0 + 0.205, label, ha="center", fontsize=9.5, color=c, weight="bold")
        box(ax, 0.625, y0 + 0.03, 0.15, 0.14, "SQS queue", ["carbonshift-", f"jobs-{z}"], ec=c, title_size=9.5)
        box(ax, 0.80, y0 + 0.105, 0.18, 0.075, f"t3.micro  {z}-1", ["worker.py"], ec=c, title_size=9.2)
        box(ax, 0.80, y0 + 0.015, 0.18, 0.075, f"t3.micro  {z}-2", ["worker.py"], ec=c, title_size=9.2)
        arrow(ax, (0.775, y0 + 0.125), (0.80, y0 + 0.14), color=c)
        arrow(ax, (0.775, y0 + 0.08), (0.80, y0 + 0.055), color=c)
    box(ax, 0.625, 0.07, 0.17, 0.13, "DynamoDB", ["carbonshift-results", "timings + results"], ec=GREEN, title_size=10)
    box(ax, 0.81, 0.07, 0.17, 0.13, "Security", ["IAM role: least privilege", "SG: no inbound rules"], title_size=10)
    # flows between laptop and AWS
    arrow(ax, (0.555, 0.53), (0.625, 0.80), text="jobs\n(SQS)", tx=-0.012, ty=0.02)
    arrow(ax, (0.555, 0.50), (0.625, 0.50))
    arrow(ax, (0.555, 0.445), (0.80, 0.43), text="start / stop VMs\n(EC2 API)", color=GREEN, ls="--", tx=0.0, ty=-0.045)
    arrow(ax, (0.89, 0.415), (0.75, 0.20), text="write\nresults", color=GREEN, rad=-0.15, tx=0.055, ty=0.0)
    arrow(ax, (0.625, 0.12), (0.555, 0.44), text="read\nresults", color=GREEN, rad=-0.3, tx=-0.035, ty=-0.02)
    fig.savefig(OUT / "architecture.png", dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def terminal(text: str, out: str, title: str, width=12.5):
    """Render command output as a terminal-style image (evidence from the AWS CLI / controller)."""
    lines = text.rstrip("\n").split("\n")
    h = 0.6 + 0.205 * len(lines)
    fig = plt.figure(figsize=(width, h))
    fig.patch.set_facecolor("#1e1f22")
    fig.text(0.012, 1 - 0.28 / h, title, color="#9ad1b4", family="monospace", fontsize=9.5, va="top", weight="bold")
    fig.text(0.012, 1 - 0.55 / h, "\n".join(lines), color="#e8e6e3", family="monospace", fontsize=8.6, va="top",
             linespacing=1.45)
    fig.savefig(OUT / out, dpi=200, facecolor=fig.get_facecolor())
    plt.close(fig)


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "architecture"
    if what == "architecture":
        architecture()
    else:   # terminal <out.png> <title> < stdin
        terminal(sys.stdin.read(), sys.argv[2], sys.argv[3])
