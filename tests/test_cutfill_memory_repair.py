"""THE D41 REPAIRS AFTER CRITIC-mem-1 (2026-10-01).

  R1  a stuck read cannot hold the server's read slot for ever: one tesseract run is killed past
      CUTFILL_OCR_TIMEOUT_S (300 s) and the read stops with plain words; a read past CUTFILL_READ_MAX_SECONDS
      (900 s) gives its slot up at once, its page says it took too long and was stopped (and to press Re-read),
      the thread stops at its next step without storing anything, and the host's queue no longer counts it
  R2  joining and gap finding have their OWN bounds from measured bytes against a 2.0 GB step budget (a 15.5
      million pair joining that the old code read in 1.5 GB reads again); a refusal is remembered for the sheet,
      reader and detection, so an open does not read it again; Re-read is the explicit try-again
  R4  "held by another read" is told apart from "locks do not work here": the second falls back to a limit in
      this process and says so once in the log, instead of waiting for ever
  R5  the mutations the first tests missed: no slicing, a slice of 1e12, a joining bound of 1e12, the gap
      abstain removed - each fails a check below (counts, slice sizes, the abstain; never gigabytes)

    python tests/test_cutfill_memory_repair.py     (fails on the commit before it)
"""
import contextlib
import errno
import io
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = Path(tempfile.mkdtemp(prefix="cfmemr_")).resolve()
os.environ["TAKEOFF_JOBS_DIR"] = str(JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="cfmemr_lg_")
os.environ.pop("CUTFILL_OUT", None)
for v in ("CUTFILL_MAX_READS", "CUTFILL_READ_MAX_SECONDS", "CUTFILL_OCR_TIMEOUT_S"):
    os.environ.pop(v, None)

import numpy as np  # noqa: E402
import pymupdf  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

import server  # noqa: E402  (sets CUTFILL_OUT to <jobs>/cutfill, as the product does)
import cutfill.chains as C  # noqa: E402
import cutfill.geom as G  # noqa: E402
import cutfill.glyphs as GL  # noqa: E402
import cutfill.ocr as O  # noqa: E402
import cutfill.project as P  # noqa: E402
import cutfill.readslot as RS  # noqa: E402
from cutfill.ingest import Stroke  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg, flush=True)
    if not ok:
        fails += 1


LONG = 120.0          # every wait is for a condition, with a deadline a loaded machine cannot reach


def wait_for(cond, secs=LONG):
    t0 = time.time()
    while time.time() - t0 < secs:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


rng = np.random.default_rng(7)


def seg(i, a, b, layer="L"):
    return Stroke(pts=np.array([a, b], dtype=float), color=(0, 0, 0), width=0.5, layer=layer, closed=False, dash=False,
                  opacity=1.0, filled=False, idx=i)


# ======================================================================== R5: slicing is used and bounded
# M1 (no slicing): find_text_clusters must walk its pairs through geom.near_pairs, in slices of PAIR_CHUNK
glyph_like = []
for i in range(2500):                           # a hatch of short ticks in a 30 x 30 pt patch: millions of pairs
    x, y = rng.uniform(0, 30, 2)
    glyph_like.append(seg(i, (x, y), (x + 1.5, y + 1.5), "H"))
slices = []
real_np = GL.near_pairs


def spy_np(points, r, chunk=None, tree=None):
    for a, b in real_np(points, r, chunk=chunk, tree=tree):
        slices.append(len(a))
        yield a, b


GL.near_pairs = spy_np
old_chunk = G.PAIR_CHUNK
G.PAIR_CHUNK = 50_000
try:
    GL.find_text_clusters(glyph_like)
finally:
    GL.near_pairs = real_np
    G.PAIR_CHUNK = old_chunk
total = sum(slices)
check(len(slices) > 5 and total > 1_000_000,
      f"find_text_clusters walks its {total:,} pairs through near_pairs, {len(slices)} slices (M1: no slicing)")
check(max(slices or [0]) <= 2 * 50_000 + 2500,
      f"each slice holds at most about PAIR_CHUNK pairs (largest {max(slices or [0]):,} for a chunk of 50,000)")

# M13 (PAIR_CHUNK = 1e12): the default slice is small, and a 4.5 million pair clump is cut into several slices
check(100_000 <= G.PAIR_CHUNK <= 5_000_000, f"the default slice is {G.PAIR_CHUNK:,} pairs (a few tens of MB)")
clump = rng.normal(0, 2.0, (3000, 2))
sizes = [len(a) for a, b in G.near_pairs(clump, 16.6)]
check(sum(sizes) > 4_000_000 and len(sizes) >= 2 and max(sizes) <= G.PAIR_CHUNK + 3000,
      f"with the default slice, {sum(sizes):,} pairs come in {len(sizes)} slices, the largest {max(sizes):,}")

# ======================================================================== R2 + R5: the joining bound
measured = {8_053_950: 763, 9_422_857: 998, 12_682_558: 1_253, 16_357_662: 1_540, 17_301_487: 1_870}   # MiB
check(all(C._join_cost(n) >= mib * 2**20 for n, mib in measured.items()),
      "the joining cost model is at or above every measured step (8.1, 9.4, 12.7, 16.4, 17.3 million pairs)")
check(C._join_cost(18_251_149) > C.STEP_BUDGET and C._join_cost(C.MAX_JOIN_PAIRS) <= C.STEP_BUDGET
      < C._join_cost(C.MAX_JOIN_PAIRS + 1) and C.STEP_BUDGET <= 2.0e9,
      f"the joining bound is {C.MAX_JOIN_PAIRS:,} pairs for a {C.STEP_BUDGET / 1e9:.1f} GB step: the 18.25 million pair "
      f"field that passed 3,000 MB stops")
check(C._join_cost(15_534_332) <= C.STEP_BUDGET and C.MAX_JOIN_PAIRS >= 15_600_000,
      "the critic's 15.5 million pair field that the old code read still reads (M2: a bound of 1e12 fails above)")


class NoWholeList(cKDTree):
    """query_pairs must not be asked for a list past the bound (it would be gigabytes under a mutation)."""
    asked = []

    def query_pairs(self, r, *a, **k):
        n = (int(self.count_neighbors(self, r)) - int(self.n)) // 2
        NoWholeList.asked.append(n)
        if n > 5_000_000:
            raise AssertionError(f"the whole list of {n:,} pairs was asked for")
        return super().query_pairs(r, *a, **k)


# 3,500 dashes 1.5 pt long, all on the same two points: 7,000 ends within the 4.5 pt reach = 24.5 million pairs,
# over the default joining bound, counted without listing them
dense = [seg(i, (0.0, 0.0), (1.5, 0.0)) for i in range(3500)]
real_tree = C.cKDTree
NoWholeList.asked = []
C.cKDTree = NoWholeList
try:
    C.build_chains(dense)
    check(False, "a 24.5 million pair joining past the default bound stops")
except G.SheetTooBig as e:
    check(str(e).startswith(G.TOO_BIG) and "24,496,500 pairs" in str(e) and not NoWholeList.asked,
          f"a 24.5 million pair joining stops before any list is made: {str(e).splitlines()[1]}")
except AssertionError as e:
    check(False, f"joining past the bound: {e}")
finally:
    C.cKDTree = real_tree

# ======================================================================== R2 + R5: the gap bound
check(C.STEP_BUDGET <= 2.5e9 and C.MAX_GAP_PAIRS * 2 * C.GAP_BYTES_PER_PAIR <= 2.5e9 and C.GAP_BYTES_PER_CAND >= 176,
      f"the gap bounds: {C.MAX_GAP_PAIRS:,} pairs at most, {C.GAP_BYTES_PER_CAND} B a candidate, {C.STEP_BUDGET / 1e9:.1f} GB")


def chain(cid, a, b):
    pts = np.linspace(np.array(a, float), np.array(b, float), 5)
    return C.Chain(id=cid, style=0, pts=pts, members=[cid], length=float(np.hypot(*(np.array(b) - np.array(a)))),
                   ink=1.0, closed=False)


# M4 (gap abstain removed): 6,000 chains 13 pt long on the same two points: 12,000 ends within 90 pt = 72 million
# pairs, past MAX_GAP_PAIRS: stopped before any list is made
same = [chain(i, (0, 0), (13, 0)) for i in range(6000)]
NoWholeList.asked = []
C.cKDTree = NoWholeList
try:
    C.find_gaps(same)
    check(False, "a 72 million pair gap search stops")
except G.SheetTooBig as e:
    check(str(e).startswith(G.TOO_BIG) and "finding gaps" in str(e) and not NoWholeList.asked,
          f"a 72 million pair gap search stops before any list is made: {str(e).splitlines()[1]}")
except AssertionError as e:
    check(False, f"gap search past the bound: {e}")
finally:
    C.cKDTree = real_tree

# the candidate cost: rows of collinear chains facing each other (most pairs are candidates); under a small budget
# the loop is refused, under the default it gives the same gaps as the code before
rows = []
cid = 0
for r_ in range(40):
    for k in range(40):
        x0 = k * 19.0 + rng.uniform(0, 1)
        rows.append(chain(cid, (x0, r_ * 0.25), (x0 + 13, r_ * 0.25)))
        cid += 1
g_new = C.find_gaps(rows)
real_cost = C._gap_cost
C._gap_cost = lambda *a, **k: 0                 # the code before: no cost check
try:
    g_old = C.find_gaps(rows)
finally:
    C._gap_cost = real_cost
check([(g.a_chain, g.a_end, g.b_chain, g.b_end, g.length) for g in g_new]
      == [(g.a_chain, g.a_end, g.b_chain, g.b_end, g.length) for g in g_old] and len(g_new) > 100,
      f"under the gap bound: the same {len(g_new)} gaps as the code before")
est = []
C._gap_cost = lambda *a, **k: est.append(real_cost(*a, **k)) or est[-1]
try:
    C.find_gaps(rows)
finally:
    C._gap_cost = real_cost
old_budget = C.STEP_BUDGET
C.STEP_BUDGET = int(est[0] * 0.9)
try:
    C.find_gaps(rows)
    check(False, "a gap search whose candidates pass the budget stops")
except G.SheetTooBig as e:
    check("GB; the limit is" in str(e), f"a gap search whose candidates pass the budget stops ({est[0]:,} B estimated)")
finally:
    C.STEP_BUDGET = old_budget

# ======================================================================== R1a: a stuck tesseract is killed
fake = Path(tempfile.mkdtemp(prefix="cfmemr_tess_"))
(fake / "sitecustomize.py").write_text("import time\ntime.sleep(60)\n")
real_tp = O.tesseract_path
O.tesseract_path = lambda: getattr(sys, "_base_executable", sys.executable)   # a real interpreter, not the venv launcher
os.environ["CUTFILL_OCR_TIMEOUT_S"] = "1.5"
old_pp = os.environ.get("PYTHONPATH")
os.environ["PYTHONPATH"] = str(fake)            # the fake "tesseract" sleeps 60 s on start
own_tmp = tempfile.mkdtemp(prefix="cfmemr_tmp_")      # TEMP of its own: other processes' OCR folders are not ours
old_tempdir, tempfile.tempdir = tempfile.tempdir, own_tmp
t0 = time.time()
try:
    O.run_batch([np.full((40, 40), 255, np.uint8)], psm=11)
    check(False, "a tesseract that never returns is stopped")
except O.OcrTooLong as e:
    took = time.time() - t0
    check(took < 90 and str(e).startswith("Reading the printed text took more than 1.5 seconds") and "Re-read" in str(e),
          f"a tesseract that never returns is killed after {took:.1f} s: {e}")
finally:
    O.tesseract_path = real_tp
    tempfile.tempdir = old_tempdir
    os.environ.pop("CUTFILL_OCR_TIMEOUT_S", None)
    if old_pp is None:
        os.environ.pop("PYTHONPATH", None)
    else:
        os.environ["PYTHONPATH"] = old_pp
left = sorted(os.listdir(own_tmp))
check(not left, f"the killed run's tiles do not stay in TEMP ({left})")
check(O.ocr_timeout() == 300.0, "CUTFILL_OCR_TIMEOUT_S unset: 300 s")
for v in ("abc", "0", "-5", "nan", "inf"):
    os.environ["CUTFILL_OCR_TIMEOUT_S"] = v
    check(O.ocr_timeout() == 300.0, f"CUTFILL_OCR_TIMEOUT_S={v!r} -> 300 s")
os.environ.pop("CUTFILL_OCR_TIMEOUT_S", None)

# ======================================================================== R1b / M1: the time limit, and no second read
check(RS.max_seconds() == 900.0, "CUTFILL_READ_MAX_SECONDS unset: 900 s")
for v, want in (("abc", 900.0), ("0", 900.0), ("-1", 900.0), ("nan", 900.0), ("30", 30.0)):
    os.environ["CUTFILL_READ_MAX_SECONDS"] = v
    check(RS.max_seconds() == want, f"CUTFILL_READ_MAX_SECONDS={v!r} -> {want:g}")
check(RS.stop_grace_seconds() == 900.0, "CUTFILL_READ_STOP_GRACE_SECONDS unset: 900 s")
os.environ["CUTFILL_READ_MAX_SECONDS"] = "0.6"
told, got, stuck = [], {}, {}


def stuck_holder():
    try:
        with RS.read_slot(on_expire=told.append) as slot:
            got["holder"] = time.time()
            t0 = time.time()
            while time.time() - t0 < 10:
                stuck["last_check"] = time.time()      # stamped before the check that stops it
                slot.check()
                time.sleep(0.02)
    except RS.ReadTooLong as e:
        stuck["stopped"] = (time.time(), str(e))


def next_read():
    with RS.read_slot(poll_s=0.02):
        got["next"] = time.time()


th1 = threading.Thread(target=stuck_holder)
th1.start()
wait_for(lambda: "holder" in got)
th2 = threading.Thread(target=next_read)
th2.start()
th1.join(LONG)
th2.join(LONG)
check(told == ["This sheet took more than 0.6 seconds to read and is being stopped."],
      f"past the limit the page is told the read is being stopped: {told}")
check("stopped" in stuck and stuck["stopped"][1] ==
      "This sheet took more than 0.6 seconds to read and was stopped. Press Re-read to try again.",
      "the stuck read stops at its next check, with the final words")
check("next" in got and got["next"] >= stuck["last_check"],
      f"the next read starts only after the stopped one's last check "
      f"({got.get('next', 0) - stuck.get('last_check', 0):+.3f} s)")

# a body that never checks keeps its slot while it runs, and still never counts as done
order = []


def no_check_body():
    try:
        with RS.read_slot():
            order.append(("A in", time.time()))
            time.sleep(1.2)          # never calls check(): runs past its 0.6 s limit
            order.append(("A out", time.time()))
        stuck["late"] = "counted as done"
    except RS.ReadTooLong:
        stuck["late"] = "refused"


def other():
    with RS.read_slot(poll_s=0.02):
        order.append(("B in", time.time()))


th3 = threading.Thread(target=no_check_body)
th3.start()
wait_for(lambda: order)
th4 = threading.Thread(target=other)
th4.start()
th3.join(LONG)
th4.join(LONG)
names = [n for n, _ in sorted(order, key=lambda e: e[1])]
check(stuck.get("late") == "refused", "a read that ends after its limit never counts as done")
check(names == ["A in", "A out", "B in"], f"past the limit the slot stays taken until the read has ended: {names}")

# no block for ever: a body stuck for good gives its slot up after the grace
os.environ["CUTFILL_READ_MAX_SECONDS"] = "0.3"
os.environ["CUTFILL_READ_STOP_GRACE_SECONDS"] = "0.5"
abandoned_told, got2 = [], {}
release_zombie = threading.Event()


def zombie():
    got2["asked"] = time.time()                      # before the slot (and its 0.3 + 0.5 s timers) exists
    try:
        with RS.read_slot(on_abandon=abandoned_told.append):
            got2["zombie"] = time.time()
            release_zombie.wait(30)  # stuck "for good" (until the test lets it go)
    except RS.ReadTooLong:
        got2["zombie_end"] = "refused"


err = io.StringIO()
with contextlib.redirect_stderr(err):
    tz = threading.Thread(target=zombie)
    tz.start()
    wait_for(lambda: "zombie" in got2)
    def nxt():
        with RS.read_slot(poll_s=0.02):
            got2["next"] = time.time()
    tn = threading.Thread(target=nxt)
    tn.start()
    tn.join(LONG)
took = got2.get("next", 0) - got2["asked"]
check("next" in got2 and 0.78 <= took < 60.0,
      f"a read stuck for good frees its slot after limit + grace, not earlier: the next read started {took:.2f} s later "
      f"(0.3 + 0.5 s)")
check(abandoned_told == ["This sheet took more than 0.3 seconds to read and was stopped. Press Re-read to try again."]
      and "has not ended; its read slot is freed" in err.getvalue(), "and its page and the log are told")
release_zombie.set()
tz.join(LONG)
check(got2.get("zombie_end") == "refused", "when it finally ends, it still counts for nothing")
os.environ.pop("CUTFILL_READ_STOP_GRACE_SECONDS", None)

# a read told to stop starts no new OCR batch
os.environ["CUTFILL_READ_MAX_SECONDS"] = "0.2"
try:
    with RS.read_slot() as s_:
        wait_for(lambda: s_.expired)            # the 0.2 s limit has passed (by condition, not by a pause)
        try:
            O.run_batch([np.full((40, 40), 255, np.uint8)], psm=11)
            check(False, "a stopped read starts no OCR batch")
        except RS.ReadTooLong:
            check(True, "a stopped read starts no new OCR batch (check between batches)")
except RS.ReadTooLong:
    pass
os.environ["CUTFILL_READ_MAX_SECONDS"] = "120"
check(RS.too_long_words(900) == "This sheet took more than 15 minutes to read and was stopped. Press Re-read to try again.",
      "the default limit in words: 15 minutes")


# ---- in the product's own flow
def make_pdf(path, w=612, h=792):
    doc = pymupdf.open()
    pg = doc.new_page(width=w, height=h)
    pg.insert_text((72, 72), "GRADING PLAN 100", fontsize=8)
    pg.draw_line((100, 100), (300, 100))
    doc.save(str(path))
    doc.close()


pdf = JOBS / "sheet.pdf"
make_pdf(pdf)
proj = P.Project.create("repair", pid="repairtest")
proj.add_document(str(pdf))
proj.add_document(str(pdf))
for k in ("d0p0", "d1p0"):
    proj.page_settings(k)["role"] = "both"
proj.save()
server._cf_reader(proj)                       # counted by the host's queue, as an open would
real_read = P.read_sheet
inside = {"now": 0, "most": 0, "calls": 0, "steps": 0}
mode = {"slow": "step"}
gate = threading.Event()
step_gate = threading.Event()


def fake_read(doc, pi, prog=lambda m: None, core=None):
    inside["now"] += 1
    inside["most"] = max(inside["most"], inside["now"])
    inside["calls"] += 1
    try:
        if mode["slow"]:
            prog("reading vectors")
            inside["steps"] += 1
            if mode["slow"] == "forever":
                gate.wait(LONG)               # stuck inside one call, no check, until the test lets it go
            else:
                step_gate.wait(LONG)          # one long step with no check inside, until the test lets it end
            prog("joining lines")             # the next check: the stop lands here
            inside["steps"] += 1
        return real_read(doc, pi, prog, core=core)
    finally:
        inside["now"] -= 1


P.read_sheet = fake_read
os.environ["CUTFILL_READ_MAX_SECONDS"] = "1"
try:
    # (1) stopped but still running: counted, holding its slot, the page told at once, no second read
    proj.start_analysis("d0p0")
    wait_for(lambda: proj.analysis_status("d0p0").get("progress", "").endswith("is being stopped."))
    st = proj.analysis_status("d0p0")
    check(st == {"status": "running", "progress": "This sheet took more than 1 second to read and is being stopped."},
          f"the page is told at once that the read is being stopped, while it still runs: {st}")
    check(server._cf_busy() == 1, f"the host still counts the stopping read ({server._cf_busy()})")
    r = proj.start_analysis("d0p0", full=True)       # Re-read pressed while it winds down
    check(r == {"status": "running", "progress": RS.STILL_STOPPING} and proj.jobs["d0p0"].get("rerun") is None,
          f"Re-read while it winds down starts nothing and says so: {r}")
    mode["slow"] = 0
    proj.start_analysis("d1p0")                      # another sheet: waits for the slot
    wait_for(lambda: proj.analysis_status("d1p0").get("progress") == RS.WAITING)
    st1 = proj.analysis_status("d1p0")
    check(st1 == {"status": "queued", "progress": RS.WAITING},
          f"another sheet waits while the stopping read holds the slot: {st1}")
    step_gate.set()                                  # the long step ends: the stop lands at the next check
    wait_for(lambda: proj.analysis_status("d0p0")["status"] not in ("queued", "running"))
    st = proj.analysis_status("d0p0")
    check(st["status"] == "error" and st.get("error", "").startswith(
        "This sheet took more than 1 second to read and was stopped. Press Re-read to try again."),
        f"once it has ended, the page says it was stopped: {st}")
    check(not os.path.exists(proj.analysis_path("d0p0")) and not os.path.exists(proj.reading_path("d0p0")),
          "nothing of the stopped read is stored")
    os.environ["CUTFILL_READ_MAX_SECONDS"] = "120"
    wait_for(lambda: proj.analysis_status("d1p0")["status"] not in ("queued", "running"), 3 * LONG)
    st1 = proj.analysis_status("d1p0")
    check(st1["status"] == "done", f"the waiting sheet is then read normally: {st1}")
    proj.start_analysis("d0p0", full=True)
    wait_for(lambda: proj.analysis_status("d0p0")["status"] not in ("queued", "running"), 3 * LONG)
    st = proj.analysis_status("d0p0")
    check(st["status"] == "done", f"Re-read after it has ended reads the sheet: {st}")
    check(inside["most"] == 1, f"never two reads inside read_sheet at once (most: {inside['most']})")

    # (2) stuck for good inside one call: after limit + grace the slot is freed (no block for ever), the other
    # sheet reads, and THIS sheet still refuses a second read until its thread has really ended
    os.environ["CUTFILL_READ_MAX_SECONDS"] = "0.5"
    os.environ["CUTFILL_READ_STOP_GRACE_SECONDS"] = "0.5"
    mode["slow"] = "forever"
    with contextlib.redirect_stderr(io.StringIO()):
        proj.start_analysis("d0p0", full=True)
        wait_for(lambda: proj.analysis_status("d0p0")["status"] not in ("queued", "running"))
        st = proj.analysis_status("d0p0")
        check(st["status"] == "error" and st.get("error", "").startswith("This sheet took more than 0.5 seconds"),
              f"stuck for good: after limit + grace its page says it was stopped: {st}")
        check(server._cf_busy() == 0, "and it is no longer counted: other reads are not blocked")
        mode["slow"] = 0
        os.environ["CUTFILL_READ_MAX_SECONDS"] = "120"
        proj.start_analysis("d1p0", full=True)
        wait_for(lambda: proj.analysis_status("d1p0")["status"] not in ("queued", "running"), 3 * LONG)
        st1 = proj.analysis_status("d1p0")
        check(st1["status"] == "done", f"another sheet reads while the stuck thread lives: {st1}")
        r = proj.start_analysis("d0p0", full=True)
        check(r == {"status": "error", "error": RS.STILL_STOPPING},
              f"the stuck sheet itself refuses a second read while its thread lives: {r}")
        calls_before = inside["calls"]
        gate.set()                                     # the stuck call finally returns
        wait_for(lambda: not proj._threads["d0p0"].is_alive())
        r = proj.start_analysis("d0p0", full=True)
        wait_for(lambda: proj.analysis_status("d0p0")["status"] not in ("queued", "running"), 3 * LONG)
        check(r["status"] == "queued" and proj.analysis_status("d0p0")["status"] == "done"
              and inside["calls"] == calls_before + 1, "once that thread has ended, Re-read reads the sheet")
finally:
    P.read_sheet = real_read
    gate.set()
    step_gate.set()
    os.environ.pop("CUTFILL_READ_STOP_GRACE_SECONDS", None)
    os.environ["CUTFILL_READ_MAX_SECONDS"] = "120"

# ======================================================================== M5: status and its words together
# A full-suite run under load once saw status "error" with an empty error text: the status was written before the
# traceback was built and stored. Here the traceback is made slow (0.3 s), and a poller reads the status meanwhile.
import traceback as _tb  # noqa: E402

real_fmt = _tb.format_exc
seen_bad, polls = [], {"n": 0}
stop_poll = threading.Event()


def slow_fmt(*a, **k):
    time.sleep(0.3)
    return real_fmt(*a, **k)


def failing_read(doc, pi, prog=lambda m: None, core=None):
    raise RuntimeError("a read that fails")


def poller():
    while not stop_poll.is_set():
        st = proj.analysis_status("d0p0")
        polls["n"] += 1
        if st["status"] == "error" and not st.get("error"):
            seen_bad.append(st)


_tb.format_exc = slow_fmt
P.read_sheet = failing_read
try:
    pt = threading.Thread(target=poller)
    pt.start()
    proj.start_analysis("d0p0", full=True)
    wait_for(lambda: proj.analysis_status("d0p0").get("error", "").startswith("a read that fails"))
    stop_poll.set()
    pt.join(LONG)
    st = proj.analysis_status("d0p0")
    check(st["status"] == "error" and st["error"].startswith("a read that fails") and not seen_bad and polls["n"] > 10,
          f"a page polling all through a failing read never sees an error without its words "
          f"({polls['n']} polls, {len(seen_bad)} bad: {seen_bad[:1]}; last {st['status']})")
finally:
    _tb.format_exc = real_fmt
    P.read_sheet = real_read
    stop_poll.set()

# ======================================================================== R2: a refusal is remembered
calls = {"n": 0}


def too_big(doc, pi, prog=lambda m: None, core=None):
    calls["n"] += 1
    raise G.SheetTooBig("joining lines: 99 pairs of line ends in one line style")


P.read_sheet = too_big
try:
    proj.start_analysis("d0p0", full=True)
    wait_for(lambda: proj.analysis_status("d0p0")["status"] == "error" and calls["n"] == 1)
    st = proj.analysis_status("d0p0")
    check(st["status"] == "error" and st["error"].startswith(G.TOO_BIG) and calls["n"] == 1,
          f"a sheet too big to read says so: {st['error'].splitlines()[0]!r}")
    check(os.path.exists(proj.refusal_path("d0p0")), "the refusal is remembered for the sheet")
    proj.jobs.clear()
    r1 = proj.start_analysis("d0p0")
    r2 = proj.start_analysis("d0p0", force=True)
    time.sleep(0.3)
    check(calls["n"] == 1 and r1 == {"status": "error", "error": G.TOO_BIG} and r2["status"] == "error",
          f"an open or an Update does not read it again ({calls['n']} reads): {r1}")
    fresh = P.Project("repairtest")              # another process, or after a restart
    check(fresh.analysis_status("d0p0") == {"status": "error", "error": G.TOO_BIG},
          "a fresh process shows the remembered refusal")
    fresh.close()
    proj.start_analysis("d0p0", full=True)        # Re-read: the explicit try again
    wait_for(lambda: calls["n"] == 2 and proj.analysis_status("d0p0")["status"] == "error")
    check(calls["n"] == 2, "Re-read reads it again")
    sha = proj.data["docs"][0]["sha"]
    proj.data["docs"][0]["sha"] = "changed"
    check(proj.refusal("d0p0") is None, "a refusal made for another PDF does not hold")
    proj.data["docs"][0]["sha"] = sha
    P.read_sheet = real_read
    proj.jobs.clear()
    proj.start_analysis("d0p0", full=True)
    wait_for(lambda: proj.analysis_status("d0p0")["status"] not in ("queued", "running"), 120)
    check(proj.analysis_status("d0p0")["status"] == "done" and not os.path.exists(proj.refusal_path("d0p0")),
          "a read that succeeds forgets the refusal")
finally:
    P.read_sheet = real_read
proj.close()

# ======================================================================== R4: busy versus locks that do not work
real_call = RS._lock_call
RS._lock_call = lambda fd: (_ for _ in ()).throw(OSError(errno.EACCES, "held"))
try:
    fdx = os.open(str(JOBS / "x.lock"), os.O_RDWR | os.O_CREAT)
    check(RS._try_lock(fdx) is False, "EACCES (Windows: held elsewhere) means busy")
    RS._lock_call = lambda fd: (_ for _ in ()).throw(BlockingIOError(errno.EWOULDBLOCK, "held"))
    check(RS._try_lock(fdx) is False, "EWOULDBLOCK (flock: held elsewhere) means busy")
    RS._lock_call = lambda fd: (_ for _ in ()).throw(OSError(errno.ENOLCK, "no locks"))
    try:
        RS._try_lock(fdx)
        check(False, "ENOLCK is not busy")
    except RS.LocksUnsupported:
        check(True, "ENOLCK means locks do not work here, not busy")
    os.close(fdx)
    err = io.StringIO()
    order = []

    def worker(i):
        with RS.read_slot(poll_s=0.02):
            order.append(("in", time.time()))
            time.sleep(0.2)
            order.append(("out", time.time()))

    os.environ.pop("CUTFILL_MAX_READS", None)
    with contextlib.redirect_stderr(err):
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        [t.start() for t in ts]
        [t.join(LONG) for t in ts]
    inside = most = 0
    for ev in sorted(order, key=lambda e: (e[1], e[0] == "in")):
        inside += 1 if ev[0] == "in" else -1
        most = max(most, inside)
    check(len(order) == 6 and most == 1,
          f"without locks every read still runs, one at a time in this process ({len(order) // 2} reads, at most {most} at once)")
    check(len([ln for ln in err.getvalue().splitlines() if ln.startswith("cutfill: file locks")]) == 1, f"and the log says so once: {err.getvalue().strip()[:120]}")
finally:
    RS._lock_call = real_call
os.environ.pop("CUTFILL_READ_MAX_SECONDS", None)


# ======================================================================== M2: the OCR page bound
check(P.OCR_BYTES_PER_PX >= 9.0 and P.OCR_MAX_PX * P.OCR_BYTES_PER_PX <= C.STEP_BUDGET,
      f"the OCR page bound: {P.OCR_MAX_PX / 1e6:.0f} million pixels x {P.OCR_BYTES_PER_PX} B = "
      f"{P.OCR_MAX_PX * P.OCR_BYTES_PER_PX / 1e9:.2f} GB, inside the {C.STEP_BUDGET / 1e9:.1f} GB step budget")
check(P.OCR_MAX_PX >= 3 * (36 * 200) * (48 * 200), "a 36 x 48 in sheet (69.1 million pixels at 200 dpi) is 3 times under it")

COST = r"""
import os, sys, ctypes
sys.path.insert(0, sys.argv[1])
import pymupdf
from cutfill.ingest import Document
d = pymupdf.open(); d.new_page(width=20 * 72, height=20 * 72); d.save(sys.argv[2]); d.close()
doc = Document(sys.argv[2])
def commit():
    if os.name == "nt":
        import ctypes.wintypes as wt
        class PMC(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("pf", wt.DWORD)] + [(n, ctypes.c_size_t) for n in
                        ("pws", "ws", "a", "b", "c", "d", "pu", "ppu")]
        k = ctypes.windll.kernel32; k.GetCurrentProcess.restype = wt.HANDLE
        ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD]
        m = PMC(); m.cb = ctypes.sizeof(PMC)
        ctypes.windll.psapi.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(m), m.cb)
        return m.pu, m.ppu
    import resource
    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    return v, v
c0, p0 = commit()
a = doc.render_rgb_array(0, 200)
c1, p1 = commit()
px = a.shape[0] * a.shape[1]
print((p1 - max(c0, p0 if os.name == "nt" else c0)) / px)
"""
out = subprocess.run([sys.executable, "-c", COST, str(ROOT), str(JOBS / "px20.pdf")], capture_output=True, text=True)
try:
    bpp = float(out.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    bpp = -1.0
check(0 < bpp <= P.OCR_BYTES_PER_PX * 1.1,
      f"the RGB render really costs {bpp:.2f} B a pixel at its peak (bound sized for {P.OCR_BYTES_PER_PX}): "
      f"a drift above it fails here")

rendered = []
real_strokes = type(proj.doc(0)).strokes


class Sentinel(Exception):
    pass


def spy_strokes(self, i):
    rendered.append(i)
    raise Sentinel()


real_tp2 = P.tesseract_path
P.tesseract_path = lambda: "tesseract"
type(proj.doc(0)).strokes = spy_strokes
try:
    from cutfill.ingest import Document
    big_pdf = JOBS / "roll80.pdf"
    make_pdf(big_pdf, 80 * 72, 80 * 72)            # 80 x 80 in: 256 million pixels at 200 dpi
    try:
        P.read_sheet(Document(str(big_pdf)), 0)
        check(False, "an 80 x 80 in page is refused")
    except G.SheetTooBig as e:
        check(not rendered and "256 million pixels" in str(e),
              f"an 80 x 80 in page (256 million pixels) is refused before anything is read: {str(e).splitlines()[1]}")
    except Sentinel:
        check(False, "an 80 x 80 in page is refused before anything is read (it was read)")
    ok_pdf = JOBS / "roll72.pdf"
    make_pdf(ok_pdf, 72 * 72, 72 * 72)             # 72 x 72 in: 207 million pixels
    try:
        P.read_sheet(Document(str(ok_pdf)), 0)
        check(False, "a 72 x 72 in page goes on to be read")
    except Sentinel:
        check(rendered == [0], "a 72 x 72 in page (207 million pixels) goes on to be read")
    except G.SheetTooBig as e:
        check(False, f"a 72 x 72 in page goes on to be read ({e})")
finally:
    P.tesseract_path = real_tp2
    type(proj.doc(0)).strokes = real_strokes

# ======================================================================== M4: words that promise nothing false
check("Re-read" not in G.TOO_BIG and G.TOO_BIG.startswith("This sheet is too big to read here"),
      f"a too-big refusal does not promise that Re-read helps: {G.TOO_BIG!r}")

print(f"\n{'FAILED' if fails else 'passed'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
