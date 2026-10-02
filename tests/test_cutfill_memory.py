"""A CUT / FILL SHEET READ CANNOT EAT THE SERVER (D41, 2026-10-01).

Measured on Beaumont's Barn civil C3.0 (engine page index 2), the read the product runs (engine + core joins):
  * finding printed text: 211,639 glyph candidates made 57,254,993 neighbour pairs, held whole with
    several arrays of the same length: the read passed 3.7 GB there and was stopped;
  * joining lines, drop_dot_fields: 340,624 stipple dots had 1,584,800,780 neighbours within 6 pt, built as
    Python lists only to be counted: an unguarded run passed 16 GB (47 GB committed) and kept climbing.
Fixed without moving a result: the pair walk goes a slice at a time (the same SET of pairs; the groups do not
depend on their order), the dots are counted, not listed. The two steps whose result DOES depend on the pairs'
order stop with "This sheet is too big to read here." past their own bounds instead of running on. At most
CUTFILL_MAX_READS fresh reads run at once per server, across its processes (OS file locks in the store folder,
freed by the OS when a process dies), and a waiting read says so in the status the page polls. The render
routes hold dpi, size and pixels.

Pinned here:
  1. geom.near_pairs hands over exactly query_pairs' set, whatever the slice size (dense clumps, duplicates)
  2. find_text_clusters gives the same clusters as the whole-list code on a dense synthetic sheet
  3. drop_dot_fields keeps the same strokes, and never asks for neighbour lists
  4. joining: below its bound the same chains; above it the read stops, saying so plainly
  5. read slots: one at a time by default; a garbage CUTFILL_MAX_READS is 1; 2 lets two in; threads exclude
     each other; a holder KILLED mid-read frees its slot (no stale lock); a closed project stops waiting
  6. a sheet read waiting for the slot shows "queued" + the plain words, a re-run asked meanwhile is neither
     dropped nor doubled, and it reads once when the slot frees
  7. /clip refuses a size or dpi past its bounds; /image lowers a giant page and says the dpi it used (X-Render-DPI)
  8. the host page image refuses a dpi outside 10-300 and a giant picture (never another dpi than asked)

    python tests/test_cutfill_memory.py     (fails on the commit before it)
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = Path(tempfile.mkdtemp(prefix="cfmem_")).resolve()
os.environ["TAKEOFF_JOBS_DIR"] = str(JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="cfmem_lg_")
os.environ.pop("CUTFILL_OUT", None)
os.environ.pop("CUTFILL_MAX_READS", None)

import numpy as np  # noqa: E402
import pymupdf  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

import server  # noqa: E402  (sets CUTFILL_OUT to <jobs>/cutfill, as the product does)
import cutfill.chains as C  # noqa: E402
import cutfill.geom as G  # noqa: E402
import cutfill.glyphs as GL  # noqa: E402
import cutfill.project as P  # noqa: E402
import cutfill.readslot as RS  # noqa: E402
import cutfill.server as CS  # noqa: E402
from cutfill.ingest import Stroke  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg, flush=True)
    if not ok:
        fails += 1


rng = np.random.default_rng(41)

# ---- 1. the pair walk is the same set --------------------------------------------------------------------
pts = np.vstack([rng.uniform(0, 400, (3000, 2)),            # scattered
                 rng.normal(200, 1.5, (1500, 2)),            # a dense clump (a hatch)
                 np.repeat(rng.uniform(0, 400, (200, 2)), 4, axis=0)])   # stacked duplicates
n = len(pts)
want = cKDTree(pts).query_pairs(16.6, output_type="ndarray")
want = np.sort(want[:, 0].astype(np.int64) * n + want[:, 1])
for chunk in (7, 1000, 50_000, G.PAIR_CHUNK):
    got = [a * n + b for a, b in G.near_pairs(pts, 16.6, chunk=chunk)]
    got = np.sort(np.concatenate(got)) if got else np.zeros(0, np.int64)
    check(np.array_equal(got, want), f"near_pairs = query_pairs' set with slices of {chunk:,} ({len(want):,} pairs)")
check(list(G.near_pairs(pts[:1], 5.0)) == [], "one point: no pairs")


# ---- 2. the glyph clusters are the same ------------------------------------------------------------------
def old_find_text_clusters(strokes, exclude_idx=None, max_extent=14.0, link=2.6, min_height=3.0, max_height=16.0):
    """find_text_clusters as it was (whole pair list): the union step only, then the same tail."""
    real = G.near_pairs

    def whole(points, r, chunk=None, tree=None):
        pr = cKDTree(points).query_pairs(r, output_type="ndarray")
        if len(pr):
            yield pr[:, 0], pr[:, 1]
    GL.near_pairs = whole
    try:
        return GL.find_text_clusters(strokes, exclude_idx, max_extent, link, min_height, max_height)
    finally:
        GL.near_pairs = real


def glyph_strokes():
    out = []
    k = 0
    # words: rows of small curvy letters
    for w in range(120):
        x0, y0 = rng.uniform(0, 1500), rng.uniform(0, 1000)
        ang = rng.uniform(-1.2, 1.2)
        u, nn = np.array([math.cos(ang), math.sin(ang)]), np.array([-math.sin(ang), math.cos(ang)])
        for c in range(int(rng.integers(2, 6))):
            t = np.linspace(0, 2 * math.pi, 9)
            loc = np.column_stack([2 + 2 * np.cos(t), 3 + 3.5 * np.sin(t)])
            p = np.array([x0, y0]) + (loc[:, :1] + 5.5 * c) * u + loc[:, 1:] * nn
            out.append(Stroke(pts=p, color=(0, 0, 0), width=0.3, layer="", closed=False, dash=False, opacity=1.0,
                              filled=False, idx=k))
            k += 1
    # a dense hatch field of short ticks, with duplicates
    for i in range(4000):
        x, y = rng.uniform(600, 700), rng.uniform(600, 660)
        p = np.array([[x, y], [x + 1.5, y + 1.5]])
        for _ in range(1 + (i % 3 == 0)):
            out.append(Stroke(pts=p.copy(), color=(0.5, 0.5, 0.5), width=0.2, layer="H", closed=False, dash=False,
                              opacity=1.0, filled=False, idx=k))
            k += 1
    return out


gs = glyph_strokes()
new = [c.to_json() for c in GL.find_text_clusters(gs)]
old = [c.to_json() for c in old_find_text_clusters(gs)]
check(len(new) > 50 and new == old, f"find_text_clusters: the same {len(new)} clusters as the whole-list code")


# ---- 3. dots are counted, not listed ---------------------------------------------------------------------
class NoLists(cKDTree):
    def query_ball_point(self, x, r, *a, **k):
        if np.asarray(x).ndim == 2 and len(x) > 1 and not k.get("return_length"):
            raise AssertionError("neighbour LISTS asked for every dot")
        return super().query_ball_point(x, r, *a, **k)


def old_drop_dot_fields(strokes, dot_len=1.2, radius=6.0, max_neighbors=5):
    dots = [s for s in strokes if s.length < dot_len and not s.closed]
    if len(dots) < 50:
        return strokes
    Cc = np.array([s.pts.mean(axis=0) for s in dots])
    counts = np.array([len(x) - 1 for x in cKDTree(Cc).query_ball_point(Cc, radius)])
    kill = {s.idx for s, m in zip(dots, counts) if m > max_neighbors}
    return [s for s in strokes if s.idx not in kill]


dots = []
for i in range(3000):
    c = rng.normal(300, 4, 2) if i < 2000 else rng.uniform(0, 1000, 2)   # a stipple field and loose dots
    dots.append(Stroke(pts=np.array([c, c + [0.3, 0.0]]), color=(0, 0, 0), width=0.2, layer="", closed=False,
                       dash=False, opacity=1.0, filled=False, idx=i))
want_ids = [s.idx for s in old_drop_dot_fields(dots)]
real_tree = C.cKDTree
C.cKDTree = NoLists
try:
    got_ids = [s.idx for s in C.drop_dot_fields(dots)]
    check(got_ids == want_ids and 0 < len(got_ids) < len(dots),
          f"drop_dot_fields: the same {len(got_ids)} of {len(dots)} strokes kept, without neighbour lists")
except AssertionError as e:
    check(False, f"drop_dot_fields: {e}")
finally:
    C.cKDTree = real_tree


# ---- 4. joining and gap finding: same below the bound, a plain stop above it ------------------------------
def dash_field(nn, spread):
    out = []
    for i in range(nn):
        x, y = rng.uniform(0, spread), rng.uniform(0, spread)
        a = rng.uniform(0, math.pi)
        out.append(Stroke(pts=np.array([[x, y], [x + 4 * math.cos(a), y + 4 * math.sin(a)]]), color=(0, 0, 0),
                          width=0.5, layer="L", closed=False, dash=False, opacity=1.0, filled=False, idx=i))
    return out


field = dash_field(3000, 400)
ch1, st1 = C.build_chains(field)
real_qp = C._bounded_pairs
C._bounded_pairs = lambda tree, r, what: tree.query_pairs(r, output_type="ndarray")      # the code before
try:
    ch0, st0 = C.build_chains(field)
finally:
    C._bounded_pairs = real_qp
check([c.to_json() for c in ch1] == [c.to_json() for c in ch0] and len(ch1) > 100,
      f"below the joining bound: build_chains makes the same {len(ch1)} chains as before")
g1 = C.find_gaps(ch1)
check(isinstance(g1, list), f"find_gaps runs below the bound ({len(g1)} gaps)")
old_max = C.STEP_BUDGET
C.STEP_BUDGET = 100_000
try:
    C.build_chains(dash_field(3000, 60))
    check(False, "past MAX_JOIN_PAIRS the joining stops")
except G.SheetTooBig as e:
    first = str(e).split("\n")[0]
    check(first == G.TOO_BIG and "pairs of line ends" in str(e),
          f"past MAX_JOIN_PAIRS the read stops and says so plainly: {first!r}")
finally:
    C.STEP_BUDGET = old_max

# ---- 5. read slots ---------------------------------------------------------------------------------------
check(RS.max_reads() == 1, "CUTFILL_MAX_READS unset: one read at a time")
for v, want_n in (("", 1), ("abc", 1), ("0", 1), ("-3", 1), ("2", 2), (" 3 ", 3)):
    os.environ["CUTFILL_MAX_READS"] = v
    check(RS.max_reads() == want_n, f"CUTFILL_MAX_READS={v!r} -> {want_n}")
os.environ.pop("CUTFILL_MAX_READS", None)
check(RS.slot_dir().startswith(str(JOBS)), f"the slots live in the job store ({RS.slot_dir()})")

HOLDER = r'''
import os, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["CUTFILL_OUT"] = sys.argv[2]
from cutfill.readslot import read_slot
with read_slot():
    open(sys.argv[3], "w").write("held")
    time.sleep(600)
'''


def hold_in_child(flag):
    f = Path(flag)
    if f.exists():
        f.unlink()
    pr = subprocess.Popen([sys.executable, "-c", HOLDER, str(ROOT), os.environ["CUTFILL_OUT"], str(f)])
    t0 = time.time()
    while not f.exists() and time.time() - t0 < 120:
        time.sleep(0.05)
    return pr


said = []
child = hold_in_child(JOBS / "held1")
got = {}


def waiter():
    with RS.read_slot(prog=said.append, poll_s=0.05):
        got["at"] = time.time()


th = threading.Thread(target=waiter)
th.start()
t_w = time.time()
while not said and time.time() - t_w < 120:          # by condition: under load the waiter may start late
    time.sleep(0.02)
check("at" not in got and said == [RS.WAITING], f"another process holds the slot: the read waits and says {said}")
killed_at = time.time()
child.kill()                      # dies mid-read, holding the slot: no clean-up runs
child.wait()
th.join(120)
check("at" in got and got["at"] - killed_at < 60.0,
      f"a holder killed mid-read frees its slot: the waiter got it {got.get('at', 0) - killed_at:.2f} s later")

# threads of one process exclude each other too, and two slots let two in
order = []


def worker(name, hold):
    with RS.read_slot(poll_s=0.02):
        order.append(("in", name, time.time()))
        time.sleep(hold)
        order.append(("out", name, time.time()))


ts = [threading.Thread(target=worker, args=(i, 0.3)) for i in range(3)]
[t.start() for t in ts]
[t.join() for t in ts]
inside, most = 0, 0
for ev in sorted(order, key=lambda e: (e[2], e[0] == "in")):
    inside += 1 if ev[0] == "in" else -1
    most = max(most, inside)
check(most == 1, f"threads of one process: at most one inside at a time (saw {most})")
os.environ["CUTFILL_MAX_READS"] = "2"
order.clear()
ts = [threading.Thread(target=worker, args=(i, 0.4)) for i in range(4)]
[t.start() for t in ts]
[t.join() for t in ts]
inside, most = 0, 0
for ev in sorted(order, key=lambda e: (e[2], e[0] == "in")):
    inside += 1 if ev[0] == "in" else -1
    most = max(most, inside)
check(most == 2, f"CUTFILL_MAX_READS=2: two at a time, never three (saw {most})")
os.environ.pop("CUTFILL_MAX_READS", None)

child = hold_in_child(JOBS / "held2")
try:
    with RS.read_slot(alive=lambda: False, poll_s=0.02):
        check(False, "a closed project's read must not start")
except RuntimeError as e:
    check("closed" in str(e), "a closed project stops waiting and never reads")
child.kill()
child.wait()


# ---- 6. a waiting sheet read in the product's own flow ----------------------------------------------------
def make_pdf(path, w=612, h=792):
    doc = pymupdf.open()
    pg = doc.new_page(width=w, height=h)
    pg.insert_text((72, 72), "GRADING PLAN 100", fontsize=8)
    pg.draw_line((100, 100), (300, 100))
    doc.save(str(path))
    doc.close()


pdf = JOBS / "sheet.pdf"
make_pdf(pdf)
proj = P.Project.create("mem test", pid="memtest")
proj.add_document(str(pdf))
proj.page_settings("d0p0")["role"] = "both"
proj.save()
reads = []
real_read = P.read_sheet


def counted(*a, **k):
    reads.append(time.time())
    return real_read(*a, **k)


P.read_sheet = counted
child = hold_in_child(JOBS / "held3")
try:
    proj.start_analysis("d0p0")
    t0 = time.time()
    st = proj.analysis_status("d0p0")
    while not (st.get("status") == "queued" and st.get("progress") == RS.WAITING) and time.time() - t0 < 120:
        time.sleep(0.05)
        st = proj.analysis_status("d0p0")
    check(st == {"status": "queued", "progress": RS.WAITING},
          f"the page's status says the read waits, in plain words: {st}")
    r2 = proj.start_analysis("d0p0", force=True)          # Update pressed while it waits
    check(r2.get("status") == "queued" and proj.jobs["d0p0"].get("rerun") == "fast",
          "a re-run asked while it waits is kept as ONE re-run (not dropped, not a second thread)")
    time.sleep(0.5)
    check(reads == [], "nothing is read while another process holds the slot")
    child.kill()
    child.wait()
    t0 = time.time()
    while proj.analysis_status("d0p0")["status"] in ("queued", "running") and time.time() - t0 < 360:
        time.sleep(0.1)
    st = proj.analysis_status("d0p0")
    check(st["status"] == "done", f"once the slot frees the sheet is read and done ({st.get('status')}: {str(st.get('error', ''))[:200]})")
    check(len(reads) == 1, f"read once, then the re-run re-interpreted the cached reading ({len(reads)} fresh reads)")
finally:
    P.read_sheet = real_read
    if child.poll() is None:
        child.kill()
proj.close()

# ---- 7. /clip and /image ---------------------------------------------------------------------------------
CS._projects.pop("memtest", None)
big = JOBS / "big.pdf"
make_pdf(big, 14400, 14400)                 # the largest page a PDF can hold: 200 x 200 in
pj = P.Project.create("render", pid="rendertest")
pj.add_document(str(pdf))
pj.add_document(str(big))
CS._projects["rendertest"] = pj
host_ok, CS.PROJECT_OK = CS.PROJECT_OK, None       # the host's registry knows no such job: the engine alone
seen = []
real_render = type(pj.doc(0)).render


def spy(self, i, dpi=110, clip=None, gray=False):
    seen.append((dpi, clip))
    return b"png"


type(pj.doc(0)).render = spy
try:
    for kw in ({"w": 5000, "h": 5000, "dpi": 2400}, {"w": 5000}, {"h": 401}, {"w": 0}, {"dpi": 2400}, {"dpi": 5}):
        n0 = len(seen)
        try:
            CS.clip_image("rendertest", "d0p0", x=300, y=300, **kw)
            check(False, f"/clip {kw} is refused")
        except CS.HTTPException as e:
            check(e.status_code == 422 and len(seen) == n0,
                  f"/clip {kw} is refused, nothing drawn ({e.status_code}: {e.detail})")
    r = CS.clip_image("rendertest", "d0p0", x=300, y=300)
    check(r.headers.get("x-render-dpi") == "300", f"/clip says the dpi it drew at ({r.headers.get('x-render-dpi')})")
    dpi, clip = seen[-1]
    check((round(clip[2] - clip[0]), round(clip[3] - clip[1]), dpi) == (80, 40, 300.0),
          f"/clip's own default thumbnail is unchanged (80 x 40 pt at 300 dpi): {dpi}")
    for bad in (float("nan"), float("inf")):
        try:
            CS.clip_image("rendertest", "d0p0", x=300, y=300, dpi=bad)
            check(False, f"/clip dpi={bad} refused")
        except CS.HTTPException as e:
            check(e.status_code == 422, f"/clip dpi={bad} is refused (422)")
    r = CS.page_image("rendertest", "d0p0", dpi=100)
    check(seen[-1] == (100, None) and r.headers.get("x-render-dpi") == "100",
          f"/image: the page's own whole-sheet ask (100 dpi) is unchanged and says so: {seen[-1]}")
    r = CS.page_image("rendertest", "d0p0", dpi=400, x0=100, y0=100, x1=500, y1=400)
    check(seen[-1][0] == 400 and r.headers.get("x-render-dpi") == "400",
          "/image: the page's zoomed ask (400 dpi on a part of the sheet) is unchanged")
    r = CS.page_image("rendertest", "d1p0", dpi=100)
    dpi = seen[-1][0]
    px = (14400 * dpi / 72) ** 2
    check(px <= CS.RENDER_MAX_PX * 1.01 and dpi < 100 and r.headers.get("x-render-dpi") == str(dpi),
          f"/image of a 200 x 200 in page at 100 dpi is drawn at {dpi} dpi ({px / 1e6:.1f} million pixels) "
          f"and says so (X-Render-DPI {r.headers.get('x-render-dpi')})")
    CS.page_image("rendertest", "d1p0", dpi=9999, x0=0, y0=0, x1=14400, y1=14400)
    check(seen[-1][0] <= 400 and (14400 * seen[-1][0] / 72) ** 2 <= CS.RENDER_MAX_PX * 1.01,
          f"/image dpi=9999 over the whole giant page is held ({seen[-1][0]} dpi)")
finally:
    type(pj.doc(0)).render = real_render
png = CS.clip_image("rendertest", "d0p0", x=150, y=100, w=200, h=100, dpi=400)
check(png.status_code == 200 and png.body[:4] == b"\x89PNG", "a real /clip render still answers a PNG")
pj.close()
CS._projects.pop("rendertest", None)
CS.PROJECT_OK = host_ok

# ---- 8. the host's page image ----------------------------------------------------------------------------
for jid, src in (("cfmem000001", pdf), ("cfmem000002", big)):
    server._job_dir(jid).mkdir(parents=True, exist_ok=True)
    server.JOBS[jid] = {"path": str(src), "pages": 1, "status": {0: "done"}, "owner": "alice"}
for bad in (5000, 301, 9, 0, -1):
    try:
        server.page_image("cfmem000001", 0, dpi=bad)
        check(False, f"host /image dpi={bad} is refused")
    except server.HTTPException as e:
        check(e.status_code == 422 and not (server._job_dir("cfmem000001") / f"p0_{bad}.png").exists(),
              f"host /image dpi={bad} is refused, nothing drawn or cached ({e.detail})")
r = server.page_image("cfmem000001", 0, dpi=130)
check(Path(r.path).name == "p0_130.png", "host /image: the page's own 130 dpi is unchanged")
try:
    server.page_image("cfmem000002", 0, dpi=300)
    check(False, "host /image of a 200 x 200 in page at 300 dpi is refused")
except server.HTTPException as e:
    check(e.status_code == 413, f"host /image of a 200 x 200 in page at 300 dpi is refused ({e.status_code}: {e.detail})")

print(f"\n{'FAILED' if fails else 'passed'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
