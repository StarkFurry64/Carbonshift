"""Final report build: data -> screenshots -> controller-log image -> docx (2 passes for TOC page numbers) -> pdf.

    .venv/bin/python report/build/finalize.py
"""
import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image

B = Path(__file__).parent
ROOT = B.parents[1]
PY = str(ROOT / ".venv/bin/python")
SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
DOCX, PDF = ROOT / "report/CarbonShift_Report.docx", ROOT / "report/CarbonShift_Report.pdf"


def sh(*cmd, **kw):
    print("$", " ".join(map(str, cmd))[:160])
    subprocess.run(list(map(str, cmd)), check=True, **kw)


# 1. numbers + live comparison chart
sh(PY, B / "collect.py")
d = json.loads((B / "data.json").read_text())
live = d["live"]
run_id = {k: live[k]["run_id"] for k in live}

# 2. dashboard screenshots (live view at a chosen hour, cropped to the top part; results tab in full)
shots = []
if "carbonshift" in live:
    shots.append((f"?view=live&run={run_id['carbonshift']}&upto={live['carbonshift']['midday_tick']}", "dash_live_full.png", "Grid carbon intensity"))
if "burst" in live:
    ticks = live["burst"]["we_on_ticks"]
    up = ticks[len(ticks) // 2] if ticks else live["burst"]["midday_tick"]
    shots.append((f"?view=live&run={run_id['burst']}&upto={up}", "dash_burst_full.png", "Grid carbon intensity"))
shots.append(("?view=results", "dash_results_full.png", "All finished runs"))
sh(PY, B / "shoot.py", *[f"{q}|{B / 'shots' / o}|{w}" for q, o, w in shots])
for src, dst, frac in [("dash_live_full.png", "dash_live.png", 0.56), ("dash_burst_full.png", "dash_burst.png", 0.56),
                       ("dash_results_full.png", "dash_results.png", 1.0)]:
    p = B / "shots" / src
    if p.exists():
        im = Image.open(p)
        w, h = im.size
        im = im.crop((0, 0, w, int(h * frac))).convert("RGB")
        # trim the white margin below the last element
        from PIL import ImageChops
        bbox = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255))).point(lambda v: 255 if v > 12 else 0).getbbox()
        if bbox:
            im = im.crop((0, 0, w, min(h, bbox[3] + 30)))
        im.save(B / "shots" / dst)

# 3. controller log excerpt (CarbonShift run, morning to afternoon)
log = (ROOT / "results/live/final_1_carbonshift.log").read_text().splitlines()
keep = [ln.split(" controller: ", 1)[1] for ln in log if " controller: " in ln]
head = [k for k in keep if k.startswith("run ")][:1]
i0 = next((i for i, k in enumerate(keep) if k.startswith("[05 Mar 06:30 IST]")), 0)
i1 = next((i for i, k in enumerate(keep) if k.startswith("[05 Mar 16:30 IST]")), len(keep) - 1)
ticks = keep[i0 + 1:i1 + 1]                     # contiguous: 07:30 ... 16:30 incl. start/stop lines
tail = [k for k in keep if k.startswith("all ") or k.startswith("stopping VMs")]
text = "\n".join(head + ["..."] + ticks + ["..."] + tail)
text = text.replace("carbonshift-none-20240305-", "cs-")
p = subprocess.run([PY, B / "figures.py", "terminal", "controller_log.png",
                    "$ python -m src.cloud.controller --policy carbonshift --yes"], input=text, text=True, check=True)


# 4. two-pass build: measure where each section landed, then write the TOC page numbers
def build():
    sh("node", B / "make_report.js", cwd=B)
    sh(SOFFICE, "--headless", "--convert-to", "pdf", "--outdir", DOCX.parent, DOCX, stdout=subprocess.DEVNULL)


build()
entries = json.loads((B / "toc_entries.json").read_text())
pages = {}
n = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", PDF], capture_output=True, text=True).stdout).group(1))
for pg in range(2, n + 1):                    # pdf page 1 is the cover; printed page = pdf page - 1
    txt = subprocess.run(["pdftotext", "-f", str(pg), "-l", str(pg), "-layout", PDF, "-"], capture_output=True, text=True).stdout
    lines = [ln.strip() for ln in txt.splitlines()]
    for e in entries:
        if e not in pages and e in lines and pg > 2:      # skip the contents page itself
            pages[e] = pg - 1
pages.setdefault("Abstract", 1)
(B / "toc_pages.json").write_text(json.dumps(pages, indent=1))
print("toc:", pages)
build()
print("pages:", subprocess.run(["pdfinfo", PDF], capture_output=True, text=True).stdout.split("Pages:")[1].split()[0])
