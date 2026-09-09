"""ONE ELEVATION READ PER PAGE, however many people are asking.

THE DEFECT (measured live 2026-09-02, throwaway store on :9970, Oncor
Cloudcrest Substation Civils - 20251212, engine page index 3 = printed
C-04, 589 chains).

    cold  GET /api/job/<j>/page/3/earthwork/suggest    217.2 s, 200, 150 rows
    warm  (p3_elevgaps2.json already on disk)            1.4 s
    two clients started 3 s apart, cold        220.0 s and 219.3 s

That last line is the defect: two full pytesseract passes over the same
page for one answer. The route cached the FINISHED result and nothing else,
so every request that arrived before the first one landed did the whole
read again. Two is not an edge case:

  * two people on one job - which is what the 2026-09-02 screen share was,
    the owner and his business partner on the same deployed build; and
  * ONE person who reloads, which is what the partner did when the panel
    looked hung. The browser's abort does not stop the server: this is a
    sync `def`, so it runs to completion on the threadpool and writes the
    cache. But the reloaded page reads the cache as absent and starts its
    own pass, so reloading at t=60 s moves the finish from t=220 to t=280.

THE FIX is a per-(job, page) lock around the OCR with the cache re-read
INSIDE it, so the second caller waits for the first and then uses its
result. This test drives the real route function on a synthetic job with a
deliberately slow gap_label_elevations, and COUNTS the passes: two
concurrent callers, one read.

No service and no browser - the route is called directly, twice, from two
threads. On 512f474 it counts 2.

    python tests/test_elev_single_flight.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp(prefix="elevlock_")
os.environ["TAKEOFF_JOBS_DIR"] = str(Path(_TMP) / "jobs")
os.environ["TAKEOFF_LEDGER_DIR"] = str(Path(_TMP) / "ledger")

import pymupdf  # noqa: E402
import server  # noqa: E402
from takeoff_engine import earthwork  # noqa: E402

fails = []


def check(ok, msg):
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails.append(msg)


# --- 1. the lock registry: one lock per page, shared by every caller -------
have_lock = hasattr(server, "_elevgap_lock")
check(have_lock, "server has a per-page elevation-read lock")
if have_lock:
    a = server._elevgap_lock("J", 3)
    b = server._elevgap_lock("J", 3)
    c = server._elevgap_lock("J", 4)
    d = server._elevgap_lock("K", 3)
    check(a is b, "two callers on the SAME job+page get the SAME lock")
    check(a is not c, "a different page gets its own lock")
    check(a is not d, "...and so does a different job")

# --- 2. the route itself: two concurrent callers, ONE OCR pass -------------
JID = "elevlock01"
d = server._job_dir(JID)
d.mkdir(parents=True, exist_ok=True)
pdf = d / "sheet.pdf"
doc = pymupdf.open()
doc.new_page(width=612, height=792)
doc.save(str(pdf))
doc.close()


def _line(y):
    return [[100.0, y], [200.0, y], [300.0, y]]


RESULT = {
    "scale": {"ft_per_paper_inch": 40.0, "source": "test"},
    "styles": [{"style_id": 1, "suggested_label": "EXISTING CONTOUR (legend)"}],
    "chains": [
        {"chain_id": 1, "points": _line(300.0), "length_pt": 200.0,
         "width": 0.5, "class": "dash1", "layer": "C-TOPO", "style_id": 1,
         "color": [0, 0, 0]},
        {"chain_id": 2, "points": _line(340.0), "length_pt": 200.0,
         "width": 0.5, "class": "dash1", "layer": "C-TOPO", "style_id": 1,
         "color": [0, 0, 0]},
    ],
}
(d / "p0_result.json").write_text(json.dumps(RESULT))
server.JOBS[JID] = {"path": str(pdf), "status": {}, "owner": None}

CALLS = []
_real_gap = earthwork.gap_label_elevations


def slow_gap(chains, img, scale, exclude=None, **kw):   # kw: time_budget_s, diag (2026-09-08)
    """Stand-in for the 217 s pytesseract loop, at 1/100th the price."""
    CALLS.append(time.time())
    time.sleep(2.0)
    return {}


earthwork.gap_label_elevations = slow_gap
try:
    out = {}

    def go(i):
        t0 = time.time()
        try:
            r = server.earthwork_suggest(JID, 0)
            out[i] = (time.time() - t0, time.time(),
                      "%d rows" % len(r["suggestions"]))
        except Exception as e:                       # noqa: BLE001
            out[i] = (time.time() - t0, time.time(), "ERR " + repr(e)[:160])

    ths = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    ths[0].start()
    time.sleep(0.4)           # the second client, mid-read - i.e. the reload
    ths[1].start()
    for t in ths:
        t.join()
finally:
    earthwork.gap_label_elevations = _real_gap

for i in sorted(out):
    print("   caller %d: waited %.2fs -> %s" % (i, out[i][0], out[i][2]))
check(all(str(out[i][2]).endswith("rows") for i in out),
      "both callers get an answer")
check(len(CALLS) == 1,
      "THE FIXED DEFECT: the page is OCR'd ONCE for two concurrent callers "
      "(got %d passes; on 512f474 this is 2, measured 220.0 s and 219.3 s "
      "on a cold C-04)" % len(CALLS))
# ...and it is a JOIN, not a queue: the waiter finishes WITH the reader
# rather than 0.4 s after it, which is what a second full pass looks like.
if len(out) == 2:
    apart = abs(out[1][1] - out[0][1])
    check(apart < 0.2,
          "the second caller finishes WITH the first (%.2fs apart) - it "
          "joined the running read instead of queueing a second one behind "
          "it, which on 512f474 lands 0.4s later, the exact interval it "
          "started late" % apart)

# --- 3. and a warm page still costs nothing --------------------------------
CALLS.clear()
earthwork.gap_label_elevations = slow_gap
try:
    t0 = time.time()
    r = server.earthwork_suggest(JID, 0)
    warm = time.time() - t0
finally:
    earthwork.gap_label_elevations = _real_gap
check(len(CALLS) == 0 and "suggestions" in r,
      "a page already read is served from its cache with no OCR at all "
      "(%.2fs)" % warm)

if fails:
    print("\n%d check(s) failed" % len(fails))
    sys.exit(1)
print("\nall elevation single-flight checks passed")
