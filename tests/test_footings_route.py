"""THE FOOTINGS ROUTE (2026-09-16; Wave 2 item 1, server half): the footing
schedule is read once per document and cached, the per-page route joins
every called-out mark to its row (or its digits, flagged), and an unknown
job is a 404 like every other route.

A two-page PDF is fabricated: page 0 the notes sheet with a FOOTING
SCHEDULE, page 1 a foundation plan with marks, bottom-of-footing
elevations, a top-of-wall bracket and a T.O. SLAB tag.

    python tests/test_footings_route.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = tempfile.mkdtemp(prefix="ftg_")
os.environ["TAKEOFF_JOBS_DIR"] = JOBS
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="ftg_lg_")

import pymupdf  # noqa: E402
import server  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


pdf = Path(JOBS) / "structural.pdf"
doc = pymupdf.open()
p0 = doc.new_page(width=612, height=792)
p0.insert_text((300, 80), "FOOTING SCHEDULE", fontsize=9)
y = 110
for row in (("F3.0", "3'-0\"", "3'-0\"", "1'-0\"", "(3)#5 BOTTOM"), ("F7.5", "7'-6\"", "7'-6\"", "1'-6\"", "(8)#5 BOTTOM")):
    x = 60
    for t in row:
        p0.insert_text((x, y), t, fontsize=8); x += 60
    y += 14
y = 110
for row in (("CF2.0", "2'-0\"", "1'-0\"", "(2)#5 BOTTOM", "#5@16\""), ("CF3.0", "3'-0\"", "1'-0\"", "(3)#5 BOTTOM", "#5@12\"")):
    x = 380
    for t in row:
        p0.insert_text((x, y), t, fontsize=8); x += 46
    y += 14
p1 = doc.new_page(width=612, height=792)
for (x, y, mark, bof, tow) in ((100, 200, "CF2.0", "(86'-0\")", "[87'-0\"]"), (300, 200, "CF3.0", "(83'-0\")", None),
                                (300, 400, "CF3.0", "(83'-0\")", None), (500, 200, "F5.5", "(96'-0\")", None)):
    p1.insert_text((x, y), mark, fontsize=8)
    p1.insert_text((x, y + 12), bof, fontsize=8)
    if tow:
        p1.insert_text((x + 40, y), tow, fontsize=8)
p1.insert_text((100, 600), "EL", fontsize=8); p1.insert_text((112, 600), "(87'-0\")", fontsize=8)
p1.insert_text((100, 612), "T.O. SLAB", fontsize=8)
doc.save(str(pdf)); doc.close()

jid = "ftgroute1"
server._job_dir(jid).mkdir(parents=True, exist_ok=True)
server.JOBS[jid] = {"path": str(pdf), "pages": 2, "status": {}, "owner": None}


def get(page):
    r = server.footings(jid, page)
    return json.loads(bytes(r.body)) if hasattr(r, "body") else r


j = get(1)
bm = j["by_mark"]
check(set(bm) == {"CF2.0", "CF3.0", "F5.5"} and bm["CF3.0"]["count"] == 2 and bm["CF3.0"]["bof"] == {"83": 2}, f"the plan's marks with counts and bottom-of-footing elevations (got {bm})")
check(bm["CF3.0"]["schedule"]["from"] == "schedule" and bm["CF3.0"]["schedule"]["width_ft"] == 3.0 and bm["CF3.0"]["schedule"]["depth_ft"] == 1.0 and bm["CF3.0"]["schedule"]["page"] == 0, f"CF3.0 joined to the schedule on page 0 (got {bm['CF3.0']['schedule']})")
check(bm["F5.5"]["schedule"]["from"] == "mark digits" and bm["F5.5"]["schedule"]["width_ft"] == 5.5, "a mark the schedule does not list falls back to its digits, flagged")
cf2 = [c for c in j["callouts"] if c["mark"] == "CF2.0"][0]
check(cf2["bof_ft"] == 86.0 and cf2["tow_ft"] == 87.0, f"the callout carries BOF and TOW (got {cf2})")
check(any(abs(s["elev_ft"] - 87.0) < 0.001 for s in j["slabs"]), f"the T.O. SLAB elevation is listed apart (got {j['slabs']})")
check(j["schedule"] == {"pages": [0], "rows": 4, "scanned": 1}, f"the schedule came from page 0 alone, four rows (got {j['schedule']})")
check((server._job_dir(jid) / "footing_schedule.json").exists(), "the schedule is cached per job")
j0 = get(0)
check(j0["by_mark"] and all(v["schedule"]["from"] == "schedule" for v in j0["by_mark"].values()), "the schedule page's own marks read as callouts too, each with its row")
try:
    server.footings("nosuchjob", 0)
    check(False, "unknown job must 404")
except Exception as e:
    check(getattr(e, "status_code", None) == 404, f"unknown job -> 404 ({getattr(e, 'status_code', e)})")
try:
    server.footings(jid, 9)
    check(False, "a page past the end must 404")
except Exception as e:
    check(getattr(e, "status_code", None) == 404, f"page past the end -> 404 ({getattr(e, 'status_code', e)})")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all footings-route checks passed")
