"""THE LABEL READER FINISHES: contours first, tesseract in batches, and a
stopped read can carry on (2026-09-17).

Barn C3.0 kept stopping at the 900 s clock with most contour labels unread.
Measured: one patch read was up to FOUR tesseract launches at 0.3-1.2 s
each on this box, and 70% of the 1,901 places the reader planned sat on
lines that are not contours - 264 of the 426 reads it managed went there.
And the 1,400-call budget cut Barn C3.1 (2,620 places) at exactly 1,400
while its diag said "not stopped early".

Pinned here:
  1. the batched reader gives the per-patch reader's answer, patch for
     patch, on printed digits - with a handful of launches, not dozens;
  2. `priority` chains are read first; no priority is the old order;
  3. a pass the CALL budget stops says so (stopped_early, "calls");
  4. a stopped pass resumes from its place, with its reads, and ends
     where an unstopped pass ends; a different plan starts over;
  5. several batches at once give the same answer as one at a time.

    python tests/test_ocr_fast.py      (fails on the commit before it)
"""
import inspect
import pathlib
import random
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import takeoff_engine.earthwork as ew  # noqa: E402

FAILS = []


def check(ok, msg):
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        FAILS.append(msg)


SIG = inspect.signature(ew.gap_label_elevations).parameters
NEW = all(k in SIG for k in ("priority", "workers", "resume"))
check(NEW, "gap_label_elevations takes priority, workers and resume")


# --- 1. batched == per patch, on printed digits -------------------------------
def patch(txt, line=False, dashes=False, h=25, w=90):
    """A flattened label patch as the reader cuts it (12 pt band at 150 dpi)."""
    im = np.full((h, w), 255, np.uint8)
    if txt:
        cv2.putText(im, txt, (8, h - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.55, 0, 1, cv2.LINE_AA)
    if line:
        cv2.line(im, (0, h // 2), (w, h // 2), 0, 1)
    if dashes:
        cv2.line(im, (0, h // 2), (6, h // 2), 0, 2)
        cv2.line(im, (w - 6, h // 2), (w, h // 2), 0, 2)
    return im


rng = np.random.default_rng(3)
noise = np.where(rng.random((25, 90)) < 0.08, 0, 255).astype(np.uint8)
PATCHES = [patch("545"), patch("7890"), patch("583.5"), patch("12"), patch(""),
           patch("7621", dashes=True), patch("566", line=True), noise,
           patch("4410.25"), patch("5657", dashes=True)]
have_tess = shutil.which("tesseract") is not None
if not hasattr(ew, "_read_label_patches"):
    check(False, "earthwork has a batched patch reader (_read_label_patches)")
elif not have_tess:
    print("SKIP: tesseract is not on PATH here - part 1 needs it (it is on this box and on prod)")
else:
    import pytesseract
    launches = {"batch": 0, "single": 0}
    real_run, real_rt = subprocess.run, pytesseract.pytesseract.run_tesseract

    def counting_run(*a, **k):
        launches["batch"] += 1
        return real_run(*a, **k)

    def counting_rt(*a, **k):
        launches["single"] += 1
        return real_rt(*a, **k)

    import re as _re
    subprocess.run = counting_run
    pytesseract.pytesseract.run_tesseract = counting_rt
    try:
        t0 = time.monotonic()
        one = [ew._read_label_patch(cv2, pytesseract, _re, p) for p in PATCHES]
        t_one = time.monotonic() - t0
        n_one = dict(launches)
        launches.update(batch=0, single=0)
        t0 = time.monotonic()
        many = ew._read_label_patches(cv2, pytesseract, _re, PATCHES)
        t_many = time.monotonic() - t0
        n_many = dict(launches)
    finally:
        subprocess.run = real_run
        pytesseract.pytesseract.run_tesseract = real_rt
    print("   per patch:", one, f"({n_one}, {t_one:.1f} s)")
    print("   batched:  ", many, f"({n_many}, {t_many:.1f} s)")
    check(many == one, "the batched reader gives the per-patch answer for every patch")
    check(sum(v is not None for v in one) >= 4,
          "...and the patches are real labels, not all blanks (%d of %d read)"
          % (sum(v is not None for v in one), len(one)))
    check(n_many["batch"] <= 2 and n_many["single"] == 0,
          "the batch takes at most 2 tesseract launches for %d patches (%s)" % (len(PATCHES), n_many))
    check(n_one["single"] >= len(PATCHES),
          "...where one patch at a time took %d" % n_one["single"])

# --- the stub harness for parts 2-5 (as tests/test_ocr_budget.py) --------------
REGS = {}


def regions_stub(chains, exclude=None):
    return {cid: [tuple(r) for r in regs] for cid, regs in REGS.items()}


IMG = np.where(np.random.default_rng(7).random((260, 1400)) < 0.10, 0, 255).astype(np.uint8)


BLANK = np.full((520, 1400), 255, np.uint8)   # no ink: every region is gated


def run(read="545", delay=0.0, jitter=0.0, img=None, **kw):
    calls = []

    def reader(cv2_, pt_, re_, p):
        calls.append(1)
        if delay or jitter:
            time.sleep(delay + random.random() * jitter)
        return read(p) if callable(read) else read

    real = ew._label_regions, ew._read_label_patch
    ew._label_regions, ew._read_label_patch = regions_stub, reader
    try:
        diag = {}
        kw = {k: v for k, v in kw.items() if k in SIG}
        found = ew.gap_label_elevations([], IMG if img is None else img, 1.0,
                                        diag=diag, **kw)
        return found, diag, len(calls)
    finally:
        ew._label_regions, ew._read_label_patch = real


def reg(i, rnd):
    # kind 1 (a break), 20 pt span, laid out on a grid that stays on the image
    return (1, 40.0 + i * 60.0, 40.0 + rnd * 45.0, 1.0, 0.0, 20.0)


# --- 2. contours first ----------------------------------------------------------
REGS.clear()
REGS.update({i: [reg(i, 0), reg(i, 1)] for i in range(6)})
found, diag, n = run(max_patches=4, priority={3, 4})
check(set(found) == {3, 4},
      "with priority {3, 4} and room for four reads, chains 3 and 4 are the ones read (%s)" % sorted(found))
found, diag, n = run(max_patches=4)
check(set(found) == {0, 1, 2, 3},
      "with no priority the order is the old round-robin: every chain's first place, in id order (%s)"
      % sorted(found))
check(diag.get("contour_first") in (0, None) or not NEW, "...and no place is counted as contour-first")

# --- 3. the call budget is not a silent cap ---------------------------------------
check(diag.get("stopped_early") is True and diag.get("stop_reason") == "calls",
      "a pass the CALL budget stops says so: stopped_early, 'calls'  (%s)"
      % {k: diag.get(k) for k in ("stopped_early", "stop_reason", "ocr_calls", "patches_planned")})
full, dfull, _ = run(max_patches=10_000)
check(dfull.get("stopped_early") is False and dfull.get("regions_seen") == dfull.get("patches_planned") == 12,
      "a pass that reaches the end says it did, every place seen  (%s)"
      % {k: dfull.get(k) for k in ("stopped_early", "regions_seen", "patches_planned")})

# --- 4. resume --------------------------------------------------------------------
# chain 0 has ONE place, read before the cut (round one goes in id order):
# a resume that dropped the earlier reads would lose it, since nothing
# after the cut reads it again
REGS.clear()
REGS.update({i: [reg(i, 0), reg(i, 1), reg(i, 2)] for i in range(1, 6)})
REGS[0] = [reg(0, 0)]


def by_row(p):
    return "545"


full, dfull, n_full = run(read=by_row, max_patches=10_000)
cut, dcut, n_cut = run(read=by_row, max_patches=5)
res = dcut.get("resume")
check(isinstance(res, dict) and res.get("pos") == dcut.get("regions_seen") and 0 < res.get("pos", 0) < 16,
      "a stopped pass leaves its place and reads to carry on from  (pos %s, %d reads)"
      % ((res or {}).get("pos"), len((res or {}).get("reads") or {})))
check(0 in cut, "...chain 0 (one place only) was read before the cut  (%s)" % sorted(cut))
more, dmore, n_more = run(read=by_row, max_patches=10_000, resume=res)
check(dmore.get("resumed") is True and more == full,
      "KEEP READING: the resumed pass ends with exactly the elevations of the uncut one  (%s vs %s)"
      % (sorted(more), sorted(full)))
check(n_cut + n_more == n_full and dmore.get("ocr_calls") == dfull.get("ocr_calls"),
      "...reading each place once in total (%d + %d = %d), and its diag counts both halves (%s)"
      % (n_cut, n_more, n_full, dmore.get("ocr_calls")))
check(dmore.get("stopped_early") is False and "resume" not in dmore,
      "...and, finished, it leaves nothing to resume")
if isinstance(res, dict):
    other = dict(res, plan_sig="0" * 16)
    again, dagain, n_again = run(read=by_row, max_patches=10_000, resume=other)
    check(dagain.get("resumed") is False and n_again == n_full and again == full,
          "a resume state from a DIFFERENT plan is ignored: the pass starts over (%d reads)" % n_again)

# --- 5. several batches at once, the same answer -----------------------------------
REGS.clear()
REGS.update({i: [reg(i, r) for r in range(5)] for i in range(20)})
random.seed(11)


def by_patch(p):
    # different values on different places, so order matters to the answer
    return ["545", "546", "545", None][int(p.sum()) % 4]


one, d1, n1 = run(read=by_patch, max_patches=10_000, workers=1)
four, d4, n4 = run(read=by_patch, max_patches=10_000, workers=4, jitter=0.004)
check(one == four and d1.get("ocr_calls") == d4.get("ocr_calls"),
      "four batches at once, finishing in any order, give the one-at-a-time answer "
      "(%d chains, %s calls each)" % (len(one), d1.get("ocr_calls")))
check(d4.get("workers") == 4, "...and diag says how many ran at once")
capped, dcap, _ = run(read="545", max_patches=10_000, workers=4)
check(dcap.get("ocr_calls") == 20 * 3,
      "the three-reads-a-chain cap holds with batches in flight: 20 chains x 3 = %s calls"
      % dcap.get("ocr_calls"))

# --- 6. the count keeps moving where nothing is read --------------------------------
# A run of regions the ink gates reject builds no batch, so it reports
# nothing: on a sheet whose plan is mostly hatch and solid fill the panel's
# "N of M places" would sit still for the whole pass (the one-at-a-time loop
# told it every ten regions).
REGS.clear()
REGS.update({i: [reg(i % 20, i // 20)] for i in range(200)})
reports = []
found, diag, n = run(read="545", max_patches=10_000, img=BLANK, progress=reports.append)
pos = [r["pos"] for r in reports]
check(n == 0 and found == {} and diag.get("patches_planned") == 200,
      "the blank page reaches tesseract not once, as before  (%d calls, %d chains)" % (n, len(found)))
check(len(reports) >= 4 and pos == sorted(pos) and pos[-1] == 200,
      "...and the panel is still told where the pass is, %d times, climbing to 200  (%s)"
      % (len(reports), pos[:6]))

print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
