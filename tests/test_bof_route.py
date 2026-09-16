"""WALLS BY BOTTOM OF FOOTING, SERVER HALF (2026-09-16; Wave 2 item 3):
GET .../footings carries `groups` once the sheet has a detect result and a
scale (and says why not, otherwise) plus the WALL STEP words; POST
.../footings/split splits a drawn outline by the callouts beside its edges,
a printed STEP between two callouts moving the split.

A one-page PDF is fabricated (a 200 x 120 pt building with a callout beside
each wall, a detached wall run with two callouts and a STEP between them)
and a synthetic detect result is written beside it at 1" = 36' (0.5 ft/pt).

    python tests/test_bof_route.py     (fails on f22916c)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = tempfile.mkdtemp(prefix="bof_")
os.environ["TAKEOFF_JOBS_DIR"] = JOBS
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="bof_lg_")

import pymupdf  # noqa: E402
import server  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


pdf = Path(JOBS) / "plan.pdf"
doc = pymupdf.open()
pg = doc.new_page(width=612, height=792)
# the building's walls: north y=100, east x=300, south y=220, west x=100;
# each mark's centre lands ~8 pt outside its wall, the elevation printed under it
for (x0, y, mark, bof) in ((189, 94, "CF2.0", "(86'-0\")"), (297, 162, "CF2.0", "(86'-0\")"),
                            (189, 230, "CF3.0", "(83'-0\")"), (81, 162, "CF3.0", "(83'-0\")"),
                            (129, 394, "CF2.0", "(86'-0\")"), (249, 394, "CF3.0", "(83'-0\")")):
    pg.insert_text((x0, y), mark, fontsize=8)
    pg.insert_text((x0, y + 12), bof, fontsize=8)
pg.insert_text((219, 412), "STEP", fontsize=8)      # a wall step between the run's two callouts
doc.save(str(pdf)); doc.close()

jid = "bofroute1"
server._job_dir(jid).mkdir(parents=True, exist_ok=True)
server.JOBS[jid] = {"path": str(pdf), "pages": 1, "status": {}, "owner": None}


def body(r):
    return json.loads(bytes(r.body)) if hasattr(r, "body") else r


def chain(cid, sid, pts, klass="dash1"):
    L = sum(((pts[i + 1][0] - pts[i][0]) ** 2 + (pts[i + 1][1] - pts[i][1]) ** 2) ** 0.5 for i in range(len(pts) - 1))
    return {"chain_id": cid, "class": klass, "color": [0, 0, 0], "width": 0.8, "layer": "", "length_pt": L,
            "style_id": sid, "dash_signature": klass, "points": pts}


chains = [chain(1, 11, [[100, 100], [300, 100]]), chain(2, 11, [[300, 100], [300, 220]]),
          chain(3, 11, [[300, 220], [100, 220]]), chain(4, 11, [[100, 220], [100, 100]]),
          chain(5, 3, [[100, 101], [300, 101]], "solid"),
          chain(6, 11, [[100, 400], [300, 400]]),
          chain(7, 11, [[500, 700], [560, 700]])]
styles = [{"style_id": 11, "suggested_label": "dash1 d6.8 g3.3", "class": "dash1", "width": 0.8, "color": [0, 0, 0]},
          {"style_id": 3, "suggested_label": "solid solid", "class": "solid", "width": 1.0, "color": [0, 0, 0]}]
rf = server._job_dir(jid) / "p0_result.json"

j = body(server.footings(jid, 0))
check(j["groups"] is None and "Detect" in (j["groups_reason"] or ""), f"no detect result yet: no groups, and the reason says to run Detect (got {j['groups_reason']!r})")
check(len(j["steps"]) == 1 and abs(j["steps"][0]["x"] - 230) < 4 and abs(j["steps"][0]["y"] - 410) < 4, f"the STEP word is reported with its centre (got {j['steps']})")

rf.write_text(json.dumps({"page": 0, "styles": styles, "chains": chains, "engine_sig": "t"}))
j = body(server.footings(jid, 0))
check(j["groups"] is None and "scale" in (j["groups_reason"] or ""), f"a result without a scale: no groups, the reason names the scale (got {j['groups_reason']!r})")

rf.write_text(json.dumps({"page": 0, "scale": {"ft_per_paper_inch": 36.0, "text": "1\" = 36'"},
                          "styles": styles, "chains": chains, "engine_sig": "t"}))
j = body(server.footings(jid, 0))
g = j["groups"]
by = {r["bof_ft"]: r for r in (g or {}).get("groups", [])}
check(g is not None and set(by) == {83.0, 86.0} and g["ft_per_pt"] == 0.5, f"with lines and a scale: one group per elevation at 0.5 ft/pt (got {sorted(by)}, {g and g.get('ft_per_pt')})")
check(g["footing_style"]["style_id"] == 11 and g["footing_style"]["label"] == "dash1 d6.8 g3.3", f"the footing family is named by its style label (got {g['footing_style']})")
check(abs(by[86.0]["lf_ft"] - 225.0) <= 2.0 and abs(by[83.0]["lf_ft"] - 195.0) <= 2.0, f"footing-line feet per group: north 100 + east 60 + 65 of the detached run (to its printed STEP) -> 86; south 100 + west 60 + 35 -> 83 (got {by[86.0]['lf_ft']}, {by[83.0]['lf_ft']})")
check(by[86.0]["families"].get("3") == 100.0 and g["families"]["3"] == "solid solid", "other families are summed per group and labelled")
check(g["unassigned_ft"] == 30.0, f"the far line is unassigned (got {g['unassigned_ft']})")
check(by[86.0]["marks"] == {"CF2.0": 3} and by[83.0]["marks"] == {"CF3.0": 3}, f"marks per group (got {by[86.0]['marks']}, {by[83.0]['marks']})")
check(by[83.0]["width_ft"] == 3.0 and by[83.0]["width_from"] == "mark digits", "no schedule in this set: the width comes from the mark's digits, and says so")
check(sorted(by[86.0]["chain_ids"]) == [1, 2, 6] and sorted(by[83.0]["chain_ids"]) == [3, 4, 6], "each group lists its footing chains for hover - the split run in both")
check(sorted(by[86.0]["chain_major"]) == [1, 2, 6] and sorted(by[83.0]["chain_major"]) == [3, 4], "the split run paints in the colour of the group holding its larger piece")

# ---- the drawn outline ---------------------------------------------------------------
sp = body(server.footings_split(jid, 0, server.FootingSplitBody(points=[[100, 100], [300, 100], [300, 220], [100, 220]])))
lf = {r["bof_ft"]: r["lf_ft"] for r in sp["groups"]}
check(sp["perimeter_ft"] == 320.0 and lf == {83.0: 160.0, 86.0: 160.0} and sp["unassigned_ft"] == 0.0, f"the building outline splits 160 / 160 and adds up to its perimeter (got {lf}, unassigned {sp['unassigned_ft']})")
check(len(sp["edges"]) == 4 and sp["callouts"] == 6, "one edge record per wall, and the sheet's callout count")
sp2 = body(server.footings_split(jid, 0, server.FootingSplitBody(points=[[100, 400], [300, 400]])))
lf2 = {r["bof_ft"]: r["lf_ft"] for r in sp2["groups"]}
check(sp2["perimeter_ft"] == 100.0 and abs(lf2.get(86.0, 0) - 65.0) <= 2.0 and abs(lf2.get(83.0, 0) - 35.0) <= 2.0, f"the detached run splits at the printed STEP: ~65 ft at 86, ~35 ft at 83 (got {lf2})")
for pts, why in (([[100, 100]], "one point"), ([[1, 2, 3], [4, 5, 6]], "not [x, y] pairs")):
    try:
        server.footings_split(jid, 0, server.FootingSplitBody(points=pts))
        check(False, f"{why} must be refused")
    except Exception as e:
        check(getattr(e, "status_code", None) == 400, f"{why} -> 400 ({getattr(e, 'status_code', e)})")
try:
    server.footings_split("nosuchjob", 0, server.FootingSplitBody(points=[[0, 0], [1, 1]]))
    check(False, "unknown job must 404")
except Exception as e:
    check(getattr(e, "status_code", None) == 404, f"unknown job -> 404 ({getattr(e, 'status_code', e)})")
rf.unlink()
try:
    server.footings_split(jid, 0, server.FootingSplitBody(points=[[0, 0], [1, 1]]))
    check(False, "no detect result must 404")
except Exception as e:
    check(getattr(e, "status_code", None) == 404, f"no detect result -> 404, run detect first ({getattr(e, 'status_code', e)})")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all bof-route checks passed")
