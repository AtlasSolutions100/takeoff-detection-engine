"""Capture the redesign's screens from the LIVE app on :8600 into
out/verify/<date>/redesign/<set>/ - a real browser (the system Edge, driven
headless by Playwright), the real server, the real flow a client uses.

    .venv/Scripts/python.exe tools/redesign_capture.py before
    .venv/Scripts/python.exe tools/redesign_capture.py after [--palette nightcrew] [--theme light]

Playwright is a dev-only tool here (pip install playwright); it drives the
installed Edge (channel="msedge"), so no browser download is needed.

The demo plans open FRESH (the server sweeps the previous run), so every
detected screen below is detected live in this run - nothing pre-baked.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, Page

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8600"


def outdir(set_name: str) -> Path:
    d = ROOT / "out" / "verify" / _dt.date.today().isoformat() / "redesign" / set_name
    d.mkdir(parents=True, exist_ok=True)
    return d


class Cap:
    def __init__(self, page: Page, out: Path):
        self.page, self.out, self.log = page, out, []

    def shot(self, name: str, full: bool = False, clip=None, marks=None):
        """marks: [(css selector, note, kind)] - kind ok | diff | defect.
        The element's box is read from the live page at the moment of the
        capture, so the annotation lands on what the screenshot shows."""
        p = self.out / f"{name}.png"
        boxes = []
        for sel, note, kind in marks or []:
            r = self.page.evaluate(
                "(s) => { const e = document.querySelector(s); if (!e) return null;"
                " const b = e.getBoundingClientRect();"
                " return b.width && b.height ? [b.x, b.y, b.width, b.height] : null; }", sel)
            boxes.append({"sel": sel, "note": note, "kind": kind, "rect": r})
        self.page.screenshot(path=str(p), full_page=full, clip=clip)
        self.log.append({"file": p.name, "url": self.page.url,
                         "at": time.strftime("%H:%M:%S"), "marks": boxes})
        print("  saved", p.relative_to(ROOT), f"({sum(1 for b in boxes if b['rect'])}/{len(boxes)} marks)" if boxes else "")
        return p

    def settle(self, ms: int = 900):
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        self.page.wait_for_timeout(ms)


def set_look(page: Page, palette: str | None, theme: str | None, accent: str | None = None):
    """Apply the Appearance choice the way the app stores it, then reload."""
    js = []
    if accent:
        js.append(f"localStorage.setItem('core-accent', {json.dumps(accent)});")
    if palette:
        js.append(f"localStorage.setItem('core-palette', {json.dumps(palette)});")
    if theme:
        js.append(f"localStorage.setItem('core-theme', {json.dumps(theme)});")
    if js:
        page.evaluate("\n".join(js))
        page.reload()


def open_demo(cap: Cap, plan: str):
    page = cap.page
    # a hash-only change from #settings does not always re-show the grid;
    # a real load does, and it is the path a client takes anyway
    page.goto("about:blank")
    page.goto(BASE + "/#projects")
    cap.settle()
    tab = page.locator("#pvTabDemo")
    if tab.count() and tab.is_visible():
        tab.click()
        cap.settle(400)
    btn = page.locator(f'[title*="{plan}"], [aria-label*="{plan}"]').first
    btn.click()
    cap.settle(1500)


def detect(cap: Cap, timeout_s: int = 240):
    page = cap.page
    page.locator("#detectBtn").click()
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        page.wait_for_timeout(1000)
        busy = page.evaluate("() => { const f = document.getElementById('runFlag');"
                             " return !!(f && !f.hidden); }")
        n = page.evaluate("() => (document.getElementById('stylesCount')||{}).textContent||''")
        if not busy and n.strip():
            break
    cap.settle(1200)
    print(f"  detect finished in {time.time() - t0:.0f}s")


def run_before(cap: Cap):
    page = cap.page
    page.goto(BASE + "/#projects")
    cap.settle()
    cap.shot("01-projects")
    page.goto(BASE + "/#settings")
    cap.settle()
    cap.shot("02-settings")
    open_demo(cap, "C4.0-SITE-GRADING-PLAN-Rev.1.pdf")
    cap.shot("03-sheet-open")
    detect(cap)
    cap.shot("04-sheet-detected")
    page.locator("#toolsBtn").click()
    cap.settle(400)
    cap.shot("05-tools-menu")
    page.keyboard.press("Escape")
    cf = page.locator("#cfBtn")
    if cf.count() and cf.is_visible():
        cf.click()
    else:
        page.locator("#toolsBtn").click()
        page.locator("#cfBtn").click()
    cap.settle(6000)
    cap.shot("06-cutfill-embed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("set", help="before | after | any label")
    ap.add_argument("--palette", default=None)
    ap.add_argument("--accent", default=None)
    ap.add_argument("--theme", default=None)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    a = ap.parse_args()
    out = outdir(a.set + (f"-{a.accent}" if a.accent else "") + (f"-{a.palette}" if a.palette else "")
                 + (f"-{a.theme}" if a.theme else "") + (f"-{a.width}" if a.width != 1440 else ""))
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=not a.headed)
        ctx = b.new_context(viewport={"width": a.width, "height": a.height}, device_scale_factor=1)
        page = ctx.new_page()
        errors = []
        page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.goto(BASE + "/#projects")
        set_look(page, a.palette, a.theme, a.accent)
        cap = Cap(page, out)
        if a.set == "before":
            run_before(cap)
        else:
            import importlib.util
            spec = importlib.util.spec_from_file_location("flows", Path(__file__).with_name("redesign_flows.py"))
            flows = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(flows)
            flows.run(cap, a)
        (out / "capture-log.json").write_text(json.dumps({"shots": cap.log, "console_errors": errors}, indent=2))
        print(f"{len(cap.log)} screens, {len(errors)} console errors -> {out.relative_to(ROOT)}")
        for e in errors[:20]:
            print("   ", e)
        b.close()


if __name__ == "__main__":
    sys.exit(main())
