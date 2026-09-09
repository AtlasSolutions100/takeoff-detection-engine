"""THE LABEL-READING OCR PASS HAS A CLOCK, AND SAYS WHEN IT USED IT.

Barn C3.0 engine page 0 - a site plan with no contours - spent the whole
1,400-call budget on utility and property linework and ran past a 600 s
probe timeout reading nothing, with the panel on "finding elevations" the
whole while (2026-09-08). gap_label_elevations now carries two clocks:

  idle_budget_s  stop when NOTHING has been read by then (the no-contour
                 sheet reads nothing at 20 s or at 20 min)
  time_budget_s  a wide hard cap behind it

and reports both in `diag` (stopped_early, stop_reason, ocr_calls of
patches_planned, seconds), so a cut-short read is shown as cut short
instead of served as "this sheet has no labels".

Driven with stubs: _label_regions hands the pass 60 one-region chains, the
page image is noise that passes the ink gates, and _read_label_patch is a
slow stand-in for tesseract that either reads nothing (the C3.0 shape) or
reads "545" every time. On the pre-fix commit the function has neither
clock: the kwargs are dropped here so the pass runs, and it runs all 60.

Run:  python tests/test_ocr_budget.py
"""
import inspect
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

import takeoff_engine.earthwork as ew  # noqa: E402

FAILS = []


def check(ok, msg):
    if ok:
        print("ok:", msg)
    else:
        FAILS.append(msg)
        print("FAIL:", msg)


N = 60
SIG = inspect.signature(ew.gap_label_elevations).parameters
HAS_CLOCKS = "idle_budget_s" in SIG and "time_budget_s" in SIG
check(HAS_CLOCKS, "gap_label_elevations takes idle_budget_s and time_budget_s")


def regions_stub(chains, exclude=None):
    # kind 1 = a break in the polyline; one region per chain, 20 pt span,
    # spaced across a page wide enough that every crop is on the image
    return {cid: [(1, 60.0 + cid * 30.0, 100.0, 1.0, 0.0, 20.0)] for cid in range(N)}


rng = np.random.default_rng(7)
IMG = np.where(rng.random((260, N * 30 + 200)) < 0.10, 0, 255).astype(np.uint8)


def run(read, **clocks):
    calls = {"n": 0}

    def slow_read(cv2, pytesseract, re, patch):
        calls["n"] += 1
        time.sleep(0.02)
        return read

    real_regions, real_read = ew._label_regions, ew._read_label_patch
    ew._label_regions, ew._read_label_patch = regions_stub, slow_read
    try:
        diag = {}
        kw = {k: v for k, v in clocks.items() if k in SIG}
        if "diag" in SIG:
            kw["diag"] = diag
        t0 = time.monotonic()
        found = ew.gap_label_elevations([], IMG, 1.0, max_patches=10_000, **kw)
        return found, diag, calls["n"], time.monotonic() - t0
    finally:
        ew._label_regions, ew._read_label_patch = real_regions, real_read


# --- 1. the C3.0 shape: nothing reads, the idle clock stops it ------------
found, diag, n, secs = run(None, idle_budget_s=0.25, time_budget_s=30.0)
check(n < N and diag.get("stopped_early") is True and diag.get("stop_reason") == "idle",
      "a pass that reads nothing stops on the IDLE clock, not after all %d patches  "
      "(%d calls, %.2fs, %s)" % (N, n, secs, {k: diag.get(k) for k in ("stopped_early", "stop_reason")}))
check(diag.get("patches_planned") == N and diag.get("ocr_calls") == n and diag.get("chains_read") == 0,
      "...and diag says what was planned, what was read, and that nothing came of it  (%s)"
      % {k: diag.get(k) for k in ("patches_planned", "ocr_calls", "chains_read")})
check(found == {}, "...with no elevations invented")

# --- 2. a sheet that reads: the idle clock never fires ---------------------
found, diag, n, secs = run("545", idle_budget_s=0.25, time_budget_s=30.0)
check(n == N and diag.get("stopped_early") is False and diag.get("stop_reason") is None,
      "a pass that IS reading labels runs every patch past the idle clock  "
      "(%d calls, %.2fs, %s)" % (n, secs, {k: diag.get(k) for k in ("stopped_early", "stop_reason")}))
check(len(found) == N and all(v[0] == 545.0 for v in found.values()),
      "...and every chain got its elevation  (%d read)" % len(found))

# --- 3. the hard cap behind it --------------------------------------------
found, diag, n, secs = run("545", idle_budget_s=0.25, time_budget_s=0.3)
check(n < N and diag.get("stopped_early") is True and diag.get("stop_reason") == "cap",
      "the hard cap stops a reading pass and says CAP, not idle  "
      "(%d calls, %.2fs, %s)" % (n, secs, {k: diag.get(k) for k in ("stopped_early", "stop_reason")}))
check(0 < len(found) < N, "...keeping what it had read so far  (%d of %d)" % (len(found), N))

# --- 4. no clocks: the old behaviour, unchanged ----------------------------
found, diag, n, secs = run(None)
check(n == N and diag.get("stopped_early") is False,
      "with no clock set the pass runs every patch, as before  (%d calls)" % n)

print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
