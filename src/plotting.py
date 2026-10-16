"""Shared matplotlib style for report charts (validated categorical palette, recessive axes)."""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"
# Categorical slots in fixed order (blue, orange, aqua, yellow) — never cycled.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
ZONE_COLOR = {"IN-WE": SERIES[0], "IN-SO": SERIES[1]}
SEQ_CMAP = "Blues"


def apply_style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.edgecolor": GRID,
            "axes.labelcolor": TEXT_2,
            "axes.titlecolor": TEXT,
            "axes.titlesize": 12,
            "axes.titleweight": "semibold",
            "axes.titlelocation": "left",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "axes.axisbelow": True,
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "xtick.color": TEXT_2,
            "ytick.color": TEXT_2,
            "font.size": 10,
            "lines.linewidth": 2,
            "legend.frameon": False,
            "legend.labelcolor": TEXT,
            "figure.dpi": 110,
            "savefig.dpi": 150,
            "savefig.bbox": "tight",
        }
    )


def label_line_end(ax, x, y, text, color) -> None:
    """Direct label at the right end of a line (text in ink, marker carries the colour)."""
    ax.plot([x], [y], "o", color=color, ms=5, mec=SURFACE, mew=1.5)
    ax.annotate(text, (x, y), xytext=(6, 0), textcoords="offset points", va="center", color=TEXT, fontsize=9)
