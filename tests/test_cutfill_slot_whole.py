"""TWO SHEETS' ANALYSES NEVER OVERLAP IN ANY STAGE (live proof P6, 2026-10-01).

The read slot held only read_sheet; on Barn civil C3.0 the interpretation after it (150 s) ran beside the next sheet's
read and the server tree went from 2,059 to 2,414 MB (out/verify/2026-10-01/live-memory/VERIFY.md). The slot now holds
the whole analysis. Pinned here, with every stage of two sheets' analyses timed (read, interpretation):
  1. two fresh analyses started together: no stage of one overlaps any stage of the other
  2. two re-interpretations on cached readings started together: the same
  3. a re-interpretation asked while another sheet is being read waits in line, saying so
     ("Waiting in line. Another sheet is being read first."), and then runs

    python tests/test_cutfill_slot_whole.py     (fails on the commit before it)
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = Path(tempfile.mkdtemp(prefix="cfslot_")).resolve()
os.environ["TAKEOFF_JOBS_DIR"] = str(JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="cfslot_lg_")
os.environ.pop("CUTFILL_OUT", None)
for v in ("CUTFILL_MAX_READS", "CUTFILL_READ_MAX_SECONDS", "CUTFILL_READ_STOP_GRACE_SECONDS"):
    os.environ.pop(v, None)

import pymupdf  # noqa: E402

import server  # noqa: E402,F401  (sets CUTFILL_OUT to <jobs>/cutfill, as the product does)
import cutfill.project as P  # noqa: E402
import cutfill.readslot as RS  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg, flush=True)
    if not ok:
        fails += 1


def wait_for(cond, secs=240.0):
    t0 = time.time()
    while time.time() - t0 < secs:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


doc = pymupdf.open()
pg = doc.new_page(width=612, height=792)
pg.insert_text((72, 72), "GRADING PLAN 100", fontsize=8)
pg.draw_line((100, 100), (300, 100))
pdf = JOBS / "sheet.pdf"
doc.save(str(pdf))
doc.close()
proj = P.Project.create("slot", pid="slottest")
proj.add_document(str(pdf))
proj.add_document(str(pdf))
for k in ("d0p0", "d1p0"):
    proj.page_settings(k)["role"] = "both"
proj.save()

stages = []                      # (key, stage, start, end)
started = []                     # (key, stage) as each stage begins
gates = {}                       # stage -> threading.Event that stage waits for (check 3)
mu = threading.Lock()
real_read, real_interp = P.read_sheet, P.interpret
hold = {"read": 0.4, "interpret": 1.5}   # each stage lasts at least this long (longer than a waiter's 0.5 s poll),
                                         # so an overlap cannot hide in a quick run
key_of_doc = {}


def timed(stage, fn, key_from):
    def w(*a, **k):
        key = key_from(*a, **k)
        with mu:
            started.append((key, stage))
        t0 = time.time()
        try:
            time.sleep(hold[stage])
            g = gates.get(stage)
            if g is not None:
                g.wait(240)                    # held until the test lets it go: no race with the clock
            return fn(*a, **k)
        finally:
            with mu:
                stages.append((key, stage, t0, time.time()))
    return w


P.read_sheet = timed("read", real_read, lambda doc, pi, *a, **k: key_of_doc.get(id(doc), "?"))
P.interpret = timed("interpret", real_interp, lambda project, key, *a, **k: key)
key_of_doc[id(proj.doc(0))] = "d0p0"
key_of_doc[id(proj.doc(1))] = "d1p0"


def overlaps(rows):
    bad = []
    for i, (k1, s1, a1, b1) in enumerate(rows):
        for k2, s2, a2, b2 in rows[i + 1:]:
            if k1 != k2 and a1 < b2 and a2 < b1:
                bad.append(f"{k1} {s1} [{a1 % 100:.2f}-{b1 % 100:.2f}] with {k2} {s2} [{a2 % 100:.2f}-{b2 % 100:.2f}]")
    return bad


def done_both():
    return all(proj.analysis_status(k)["status"] not in ("queued", "running") for k in ("d0p0", "d1p0"))


try:
    # 1. two fresh analyses at once
    proj.start_analysis("d0p0", full=True)
    proj.start_analysis("d1p0", full=True)
    wait_for(done_both)
    sts = {k: proj.analysis_status(k)["status"] for k in ("d0p0", "d1p0")}
    fresh = list(stages)
    kinds = sorted({(k, s) for k, s, _, _ in fresh})
    check(sts == {"d0p0": "done", "d1p0": "done"} and kinds == [("d0p0", "interpret"), ("d0p0", "read"),
                                                                ("d1p0", "interpret"), ("d1p0", "read")],
          f"both sheets were read and interpreted ({sts}, stages {kinds})")
    bad = overlaps(fresh)
    check(not bad, f"two fresh analyses never overlap in any stage ({len(fresh)} stages; overlaps: {bad[:2]})")

    # 2. two re-interpretations on the cached readings at once
    stages.clear()
    proj.start_analysis("d0p0", force=True)
    proj.start_analysis("d1p0", force=True)
    wait_for(done_both)
    again = list(stages)
    check([s for _, s, _, _ in again].count("interpret") == 2 and not any(s == "read" for _, s, _, _ in again),
          f"both re-interpreted their cached readings, nothing read again ({[(k, s) for k, s, _, _ in again]})")
    bad = overlaps(again)
    check(not bad, f"two re-interpretations never overlap ({bad[:2]})")

    # 3. a re-interpretation asked while another sheet is being read waits in line, saying so
    stages.clear()
    started.clear()
    gates["read"] = threading.Event()                    # d0p0's read holds the slot until the words were seen
    proj.start_analysis("d0p0", full=True)
    wait_for(lambda: ("d0p0", "read") in started)      # d0p0 is inside the slot, reading (not merely started)
    proj.start_analysis("d1p0", force=True)
    seen = wait_for(lambda: proj.analysis_status("d1p0") == {"status": "queued", "progress": RS.WAITING}, 60)
    check(seen, f"a re-interpretation behind another sheet's read shows: {RS.WAITING!r}")
    hold["read"] = hold["interpret"] = 0.0
    gates.pop("read").set()
    wait_for(done_both)
    check(proj.analysis_status("d1p0")["status"] == "done" and not overlaps(list(stages)),
          f"and then runs, after the read ({proj.analysis_status('d1p0')['status']})")
finally:
    for g in gates.values():
        g.set()
    P.read_sheet, P.interpret = real_read, real_interp
    proj.close()

print(f"\n{'FAILED' if fails else 'passed'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
