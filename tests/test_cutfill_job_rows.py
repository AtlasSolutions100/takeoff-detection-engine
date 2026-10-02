"""ONE SET OF CUT / FILL ROWS PER JOB, and the ground the limit's rows stand on (2026-09-27).

The independent check on the redesign build added the same result on two sheets and the bid sheet
doubled (80,726 / 99,280 / 18,554 CY on a 40,363 / 49,640 / 9,277 job); Clear & Grub used a
self-crossing limit ring's raw area (441,690 SF) while the volumes used the repaired ring
(468,259 SF). Pinned here on the server functions:

  rows marker -> the engine's project remembers the page whose takeoff holds the rows; the open
                 route hands it to every page; only the holding page can take them out
  sheet hint  -> the words a sheet prints never decide its role (15 of the corpus's 36 grading
                 pages score under 8 on them): a utility sheet with no grading word gets advice
  ring area   -> a ring that crosses itself gives the repaired polygon's area, flagged

    python tests/test_cutfill_job_rows.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = Path(tempfile.mkdtemp(prefix="cfr_")).resolve()
os.environ["TAKEOFF_JOBS_DIR"] = str(JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="cfr_lg_")

import pymupdf  # noqa: E402
import server  # noqa: E402
from cutfill.takeoff import ring_metrics  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


def seed(jid, pages=2):
    pdf = JOBS / f"{jid}.pdf"
    doc = pymupdf.open()
    for i in range(pages):
        pg = doc.new_page(width=612, height=792)
        pg.insert_text((72, 72), f"GRADING PLAN {i + 1}", fontsize=8)
        pg.draw_line((100, 100), (300, 100))
    doc.save(str(pdf))
    doc.close()
    d = server._job_dir(jid)
    d.mkdir(parents=True, exist_ok=True)
    for i in range(pages):
        (d / f"p{i}_result.json").write_text(json.dumps({"page": {"width_pt": 612, "height_pt": 792}, "chains": [],
                                                         "styles": [], "engine_sig": "t"}))
    server.JOBS[jid] = {"path": str(pdf), "pages": pages, "status": {i: "done" for i in range(pages)}, "owner": None,
                        "mtime": round(pdf.stat().st_mtime, 2), "sizes": [[612.0, 792.0]] * pages}
    return d


# ---- the rows marker ------------------------------------------------------------------------
jid = "cfr00000001"
d = seed(jid)
o = server.cutfill_open(jid, server.CutfillOpenBody(page=0))
check(o["role"] == "both" and o["rows_page"] is None, f"the first sheet opened is the grading sheet; no rows anywhere yet ({o['role']}, {o['rows_page']})")
rows = [{"label": "Site Cut", "qty": 40363, "unit": "CY"}, {"label": "Site Fill", "qty": 49640, "unit": "CY"},
        {"label": "Import Structural", "qty": 9277, "unit": "CY", "kind": "net"}]
r = server.cutfill_rows(jid, 0, server.CutfillRowsBody(added=True, pid=o["pid"], rows=rows, summary={"cut_cy": 40363}))
check(r["ok"] and r["rows_page"] == 0, "adding the rows on page 1 marks page 1 as their holder")
o2 = server.cutfill_open(jid, server.CutfillOpenBody(page=1))
check(o2["role"] == "none" and o2["rows_page"] == 0, f"the second sheet starts as none and is told page 1 holds the rows ({o2['role']}, {o2['rows_page']})")
r = server.cutfill_rows(jid, 1, server.CutfillRowsBody(added=False, pid=o["pid"], rows=[], summary=None))
check(r["rows_page"] == 0, "a 'remove' from another page does not clear the holder's mark")
r = server.cutfill_rows(jid, 0, server.CutfillRowsBody(added=False, pid=o["pid"], rows=[], summary=None))
check(r["rows_page"] is None and server.cutfill_open(jid, server.CutfillOpenBody(page=1))["rows_page"] is None,
      "removing them on the holding page clears the mark for every page")
pj = json.loads((JOBS / "cutfill" / f"job_{jid}" / "project.json").read_text(encoding="utf-8"))
check("host_rows" not in pj["settings"], "and the project file carries no stale mark")

# ---- the sheet hint: advice, never a role --------------------------------------------------------
(d / "triage.json").write_text(json.dumps({"version": 1, "pages": [
    {"page": 0, "sheet_no": "C2", "hits": [["UTILITY", 6, 12], ["WATER", 5, 5], ["SEWER", 4, 4]]},
    {"page": 1, "sheet_no": "3.1", "hits": [["GRADING", 1, 3], ["UTILITY", 6, 12]]},
    {"page": 2, "sheet_no": "C4.0", "hits": []}]}), encoding="utf-8")
h = server._cutfill_sheet_hint(jid, 0)
check(bool(h) and "utility" in h and "grading sheet" in h, f"a sheet of utility words and no grading word gets advice ({h})")
check(server._cutfill_sheet_hint(jid, 1) is None, "one grading word is enough to say nothing (the words cannot decide)")
check(server._cutfill_sheet_hint(jid, 2) is None and server._cutfill_sheet_hint(jid, 9) is None
      and server._cutfill_sheet_hint("no-such-job", 0) is None, "no words read, no page, no job: nothing is claimed")
jid2 = "cfr00000002"
d2 = seed(jid2)
(d2 / "triage.json").write_text(json.dumps({"version": 1, "pages": [
    {"page": 0, "sheet_no": "C2", "hits": [["UTILITY", 6, 12], ["WATER", 5, 5]]}]}), encoding="utf-8")
o3 = server.cutfill_open(jid2, server.CutfillOpenBody(page=0))
check(o3["role"] == "both" and bool(o3["role_hint"]), "the hint never changes the role: the first sheet opened is still the grading sheet")

# ---- the limit's area: the repaired ring's ---------------------------------------------------------
sq = ring_metrics([(0, 0), (100, 0), (100, 100), (0, 100)], 1.0)
check(sq["area_sf"] == 10000.0 and sq["perim_ft"] == 400.0 and sq["repaired"] is False, "a plain square: 10,000 SF, 400 ft, not repaired")
bow = ring_metrics([(0, 0), (100, 100), (100, 0), (0, 100)], 1.0)      # crosses itself at (50, 50)
check(bow["repaired"] is True and abs(bow["area_sf"] - 5000.0) < 0.5,
      f"a bow-tie: the shoelace says 0, the repaired ring holds two triangles of 2,500 SF each ({bow['area_sf']})")
# a ring with one small crossing loop: the shoelace SUBTRACTS the loop, the repair keeps it
loop = [(0, 0), (200, 0), (200, 200), (120, 200), (100, 220), (80, 200), (120, 200), (100, 180), (80, 200), (0, 200)]
m = ring_metrics(loop, 1.0)
from shapely.geometry import Polygon  # noqa: E402
import shapely  # noqa: E402
fixed = shapely.make_valid(Polygon(loop))
want = sum(g.area for g in getattr(fixed, "geoms", [fixed]) if g.geom_type in ("Polygon", "MultiPolygon"))
check(m["repaired"] is True and abs(m["area_sf"] - round(want, 1)) < 0.2, f"a ring with a crossing loop uses make_valid's area ({m['area_sf']} vs {want:.1f})")

print("\n" + ("all cut / fill job-row checks passed" if not fails else f"{fails} failure(s)"))
sys.exit(1 if fails else 0)
