"""THE SUGGEST ROUTE READS CONTOURS FIRST, RE-READS AN OLD CUT-SHORT READ,
AND CAN KEEP READING (2026-09-17).

  * the label reader is handed the families the sheet calls contours
    (priority), a worker count and the server's call budget;
  * a cached read that an OLDER reader cut short (Barn C3.0: 426 of 1,901
    places at the 900 s clock) is read again; a complete one is served;
  * a read this reader cut short keeps its place beside the cache
    (p<N>_elevgaps2.resume.json - never in the reply), the reply says
    `resumable`, and ?more=1 carries it on from there; once it finishes,
    the place is gone.

The real route on a synthetic job, the reader stubbed.

    python tests/test_ocr_resume_route.py     (fails on the commit before it)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp(prefix="ocrresume_")
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


check(hasattr(server, "OCR_WORKERS") and server.OCR_WORKERS >= 1,
      "server has a worker count for the label read (%s)" % getattr(server, "OCR_WORKERS", None))
check(getattr(server, "OCR_CALLS", 0) > 1400,
      "...and a call budget wider than the engine's 1,400 (%s)" % getattr(server, "OCR_CALLS", None))
READER = getattr(earthwork, "GAP_READER", None)
check(READER is not None, "the engine names its reader (GAP_READER)")

JID = "ocrresume01"
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
    "styles": [{"style_id": 1, "suggested_label": "EXISTING CONTOUR (legend)"},
               {"style_id": 2, "suggested_label": "C-TOPO", "layer": "C-TOPO"},
               {"style_id": 3, "suggested_label": "BG-L-PLNT-TREE", "layer": "BG-L-PLNT-TREE"}],
    "chains": [
        {"chain_id": 1, "points": _line(300.0), "length_pt": 200.0, "width": 0.5,
         "class": "dash1", "layer": "", "style_id": 1, "color": [0, 0, 0]},
        {"chain_id": 2, "points": _line(340.0), "length_pt": 200.0, "width": 0.5,
         "class": "solid", "layer": "C-TOPO", "style_id": 2, "color": [0, 0, 0]},
        {"chain_id": 3, "points": _line(380.0), "length_pt": 200.0, "width": 0.5,
         "class": "solid", "layer": "BG-L-PLNT-TREE", "style_id": 3, "color": [0, 0, 0]},
    ],
}
(d / "p0_result.json").write_text(json.dumps(RESULT))
server.JOBS[JID] = {"path": str(pdf), "status": {}, "owner": None}
GC = d / "p0_elevgaps2.json"
DIAG = d / "p0_elevgaps2.diag.json"
RES = d / "p0_elevgaps2.resume.json"
CALLS = []
PLAN = {"stop": True}
STATE = {"plan_sig": "abc", "pos": 7, "calls": 7, "seconds": 3.0, "reads": {"2": [[545.0, 150.0, 340.0, 1]]}}


def fake_gap(chains, img, scale, exclude=None, diag=None, **kw):
    CALLS.append(kw)
    if diag is not None:
        diag.update({"patches_planned": 20, "ocr_calls": 7, "regions_seen": 7,
                     "seconds": 3.0, "chains_read": 1, "reader": READER,
                     "stopped_early": PLAN["stop"], "stop_reason": "cap" if PLAN["stop"] else None})
        if PLAN["stop"]:
            diag["resume"] = dict(STATE)
    return {2: (545.0, 150.0, 340.0)}


def suggest(**kw):
    real = earthwork.gap_label_elevations
    earthwork.gap_label_elevations = fake_gap
    try:
        return server.earthwork_suggest(JID, 0, **kw)
    except TypeError as e:                       # the route has no `more`
        return {"error": repr(e)}
    finally:
        earthwork.gap_label_elevations = real


# --- 1. what the reader is handed -------------------------------------------
r = suggest()
kw = CALLS[-1] if CALLS else {}
check(set(kw.get("priority") or ()) == {1, 2},
      "the reader is handed the contour families first: the legend's and the C-TOPO layer's, not the "
      "planting family (%s)" % sorted(kw.get("priority") or ()))
check(kw.get("workers") == getattr(server, "OCR_WORKERS", None) and kw.get("max_patches") == getattr(server, "OCR_CALLS", None),
      "...with the server's worker count and call budget (%s, %s)" % (kw.get("workers"), kw.get("max_patches")))
check(kw.get("resume") is None, "...and no place to resume from on a first read")

# --- 2. a read this reader cut short: kept place, flagged, not in the reply ---
ocr = r.get("elevation_ocr") or {}
check(RES.exists() and json.loads(RES.read_text()) == STATE,
      "the cut-short read's place is kept beside the cache")
check("resume" not in json.loads(DIAG.read_text()) and "resume" not in ocr,
      "...and not written into the diag the browser is sent")
check(ocr.get("stopped_early") is True and ocr.get("resumable") is True,
      "the reply says the read stopped early and CAN be carried on (%s)"
      % {k: ocr.get(k) for k in ("stopped_early", "resumable")})
n = len(CALLS)
r = suggest()
check(len(CALLS) == n and (r.get("elevation_ocr") or {}).get("resumable") is True,
      "asking again without more=1 serves the short read from cache - no new pass")

# --- 3. keep reading -----------------------------------------------------------
PLAN["stop"] = False
r = suggest(more=1)
check(len(CALLS) == n + 1 and (CALLS[-1] or {}).get("resume") == STATE,
      "?more=1 runs the reader again FROM THE KEPT PLACE (%s)" % ((CALLS[-1] or {}).get("resume") if len(CALLS) > n else r))
ocr = r.get("elevation_ocr") or {}
check(ocr.get("stopped_early") is False and not ocr.get("resumable") and not RES.exists(),
      "...and once it finishes, the kept place is gone and nothing offers to resume")
n = len(CALLS)
suggest(more=1)
check(len(CALLS) == n, "?more=1 on a finished read is a plain read from cache")

# --- 4. old readers' caches --------------------------------------------------------
for f in (GC, DIAG, RES):
    if f.exists():
        f.unlink()
GC.write_text(json.dumps({"3": [826.0, 150.0, 380.0]}))
DIAG.write_text(json.dumps({"patches_planned": 1901, "ocr_calls": 426, "seconds": 900.3,
                            "stopped_early": True, "stop_reason": "cap", "chains_read": 1}))
n = len(CALLS)
r = suggest()
check(len(CALLS) == n + 1 and (r.get("elevation_ocr") or {}).get("reader") == READER,
      "a read an OLDER reader cut short is read again with this one")
# ...and one the old 1,400-CALL budget stopped, which that reader recorded as
# NOT stopped early (Barn C3.1: 1,400 calls over 2,620 places)
GC.write_text(json.dumps({"3": [444.0, 150.0, 380.0]}))
DIAG.write_text(json.dumps({"patches_planned": 2620, "ocr_calls": 1400, "seconds": 351.5,
                            "stopped_early": False, "stop_reason": None, "chains_read": 3}))
n = len(CALLS)
r = suggest()
check(len(CALLS) == n + 1,
      "an old read the 1,400-call budget stopped - recorded as NOT stopped early - is read again too")
GC.write_text(json.dumps({"2": [545.0, 150.0, 340.0]}))
DIAG.write_text(json.dumps({"patches_planned": 20, "ocr_calls": 12, "seconds": 4.0,
                            "stopped_early": False, "stop_reason": None, "chains_read": 1}))
n = len(CALLS)
r = suggest()
check(len(CALLS) == n, "a COMPLETE read from an older reader is served as it is (same answers - FINDINGS 38)")

if fails:
    print("\n%d check(s) failed" % len(fails))
    sys.exit(1)
print("\nall label-read resume route checks passed")
