"""Capture dashboard screenshots for the report with Playwright (uses the installed Google Chrome)."""
import sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8501/"


def shoot(page, query: str, out: str, wait_text: str, width=1400, height=1000, full=True):
    """Streamlit scrolls inside its own container, so size the viewport to the content height."""
    page.set_viewport_size({"width": width, "height": height})
    page.goto(BASE + query, wait_until="networkidle")
    page.get_by_text(wait_text).first.wait_for(timeout=60000)
    page.wait_for_timeout(3000)                      # let Plotly finish drawing
    h = page.evaluate("document.querySelector('[data-testid=\"stMainBlockContainer\"]').scrollHeight")
    page.set_viewport_size({"width": width, "height": int(h) + 60})
    page.wait_for_timeout(2500)
    page.screenshot(path=out, full_page=full)
    print("saved", out)


if __name__ == "__main__":
    jobs = [a.split("|") for a in sys.argv[1:]]      # each: query|out|wait_text
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        page = b.new_page(device_scale_factor=2)
        for q, out, wt in jobs:
            shoot(page, q, out, wt)
        b.close()
