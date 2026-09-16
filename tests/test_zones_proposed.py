"""PROPOSED-SURFACE ZONES + SPOT DEPTHS ON THE MAP (2026-09-16; Wave 2 items
4 and 5). zone_cut_fill mode "proposed" differences the sheet's own
proposed surface inside the zone (coverage said, the proposed layer stack
lowering it, refusals in words); the zone route builds that surface from
proposed rows / points and offers the biggest text inside the polygon as
the zone's name; map_labels prints one depth per block for the whole-sheet
map and the map route carries them in its cached meta.

    python tests/test_zones_proposed.py     (fails on the commit before it)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = tempfile.mkdtemp(prefix="zp_")
os.environ["TAKEOFF_JOBS_DIR"] = JOBS
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="zp_lg_")

import pymupdf  # noqa: E402
import server  # noqa: E402
from takeoff_engine.earthwork import Surface, map_labels, zone_cut_fill  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


def flat(name, z, x0=0, y0=0, x1=200, y1=200):
    """A level surface: four spot grades at the corners (a TIN needs 3+ off one line)."""
    s = Surface(name)
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        s.points.append((float(x), float(y), float(z)))
    return s


# ---- mode "proposed" ------------------------------------------------------------------------
ex = flat("existing", 100.0)
pr = flat("proposed", 101.0)                     # one foot of fill everywhere
zone = [[50, 50], [150, 50], [150, 150], [50, 150]]   # 100 x 100 pt at 1 ft/pt = 10,000 SF
r = zone_cut_fill(ex, zone, 1.0, {"mode": "proposed"}, cell_ft=2.0, proposed=pr)
check("error" not in r and r["mode"] == "proposed", f"proposed mode runs (got {r.get('error')})")
check(abs(r["fill_yd3"] - 10000 / 27.0) < 2.0 and r["cut_yd3"] == 0.0 and r["coverage"] == 1.0, f"one foot of fill over 10,000 SF = 370 CY, full coverage (got fill {r['fill_yd3']}, cut {r['cut_yd3']}, coverage {r['coverage']})")
r2 = zone_cut_fill(ex, zone, 1.0, {"mode": "proposed", "subgrade_offset_ft": 0.5}, cell_ft=2.0, proposed=pr)
check(abs(r2["fill_yd3"] - 5000 / 27.0) < 2.0, f"the proposed layer stack lowers the design: 6 in of base halves the fill (got {r2['fill_yd3']})")
half = flat("proposed", 102.0, x0=0, y0=0, x1=100, y1=200)   # the proposed surface stops at x = 100
r3 = zone_cut_fill(ex, zone, 1.0, {"mode": "proposed"}, cell_ft=2.0, proposed=half)
check(0.45 <= r3["coverage"] <= 0.55 and abs(r3["fill_yd3"] - 2 * 5000 / 27.0) < 4.0, f"where the proposed surface stops the cells are left out and coverage says so (got coverage {r3['coverage']}, fill {r3['fill_yd3']})")
check(r3["cell_labels"] and all(c["x"] <= 101 for c in r3["cell_labels"]), "the per-cell labels only where the design is defined")
e1 = zone_cut_fill(ex, zone, 1.0, {"mode": "proposed"}, cell_ft=2.0, proposed=None)
check("error" in e1 and "PROPOSED" in e1["error"] and e1.get("error_code") == "surface_empty", "no proposed surface: a refusal in words, not a zero")
two = Surface("proposed"); two.points.append((0.0, 0.0, 101.0)); two.points.append((10.0, 0.0, 101.0))
e2 = zone_cut_fill(ex, zone, 1.0, {"mode": "proposed"}, cell_ft=2.0, proposed=two)
check("error" in e2 and e2.get("error_code") == "surface_too_few_samples", "two points make no surface: refused")
far = flat("proposed", 101.0, x0=500, y0=500, x1=600, y1=600)
e3 = zone_cut_fill(ex, zone, 1.0, {"mode": "proposed"}, cell_ft=2.0, proposed=far)
check("error" in e3 and "does not reach this zone" in e3["error"], "a proposed surface elsewhere on the sheet: refused, said where")
rp = zone_cut_fill(ex, zone, 1.0, {"mode": "pad", "elev_ft": 101.0}, cell_ft=2.0)
check(abs(rp["fill_yd3"] - r["fill_yd3"]) < 0.5, "a pad at the same elevation gives the same fill - the modes agree")

# ---- map_labels ---------------------------------------------------------------------------------
ny, nx = 50, 100                                   # 2 ft cells -> 100 x 200 ft
dz = np.full((ny, nx), -1.0); dz[:, 50:] = 2.0     # west half a foot of cut, east half two of fill
ok = np.ones((ny, nx), bool); ok[:10, :] = False   # the top ten rows undefined
grid = {"dz": dz, "ok": ok, "bbox_pt": [1000.0, 500.0, 1000.0 + nx * 2.0, 500.0 + ny * 2.0], "cell_pt": 2.0, "nx": nx, "ny": ny}
lab = map_labels(grid, 2.0, spacing_ft=20.0)
check(len(lab) == 10 * 4 and all(abs(l["dz"]) in (1.0, 2.0) for l in lab), f"one depth per 20 ft block over the defined cells: 10 x 4 blocks (got {len(lab)})")
check(min(l["y"] for l in lab) > 500.0 + 20.0 and all(l["dz"] == -1.0 for l in lab if l["x"] < 1100) and all(l["dz"] == 2.0 for l in lab if l["x"] > 1100), "labels sit in page pt inside the map's box, cut west, fill east, none over the undefined rows")
check(len(map_labels(grid, 2.0, spacing_ft=0)) == len(map_labels(grid, 2.0, spacing_ft=30.0)), "a bad spacing falls back to 30 ft")
check(len(map_labels(grid, 2.0, spacing_ft=2.0, cap=50)) <= 50, "the cap grows the block until the count fits")
check(map_labels({"dz": np.zeros((2, 2)), "ok": np.zeros((2, 2), bool), "bbox_pt": [0, 0, 4, 4], "cell_pt": 2.0, "nx": 2, "ny": 2}, 2.0) == [], "nothing defined: no labels")

# ---- the zone route: proposed rows / points + the suggested name ----------------------------------
pdf = Path(JOBS) / "site.pdf"
doc = pymupdf.open()
pg = doc.new_page(width=612, height=792)
pg.insert_text((90, 110), "BARN", fontsize=24)               # the structure's name inside the zone
pg.insert_text((90, 130), "7712.50", fontsize=8)              # a number never names a zone
pg.insert_text((60, 95), "WL", fontsize=40)                   # a pipe label, TALLER than the name: never (Barn C3.0 took it)
pg.insert_text((55, 145), "STONE PATIO; RE. TO", fontsize=12)  # a note: never
pg.insert_text((55, 70), "FFE: 7733.25", fontsize=12)         # an elevation: never
pg.insert_text((70, 340), "POOL", fontsize=20)                # a name on two stacked lines...
pg.insert_text((70, 364), "HOUSE", fontsize=20)               # ...joins
pg.insert_text((330, 560), "SLAB ON GRADE", fontsize=24)      # plain words, no structure word: the zone keeps its default
pg.insert_text((310, 400), "GUIDE RESIDENCE", fontsize=20)   # outside the first zone, inside the second (x 310-470)
doc.save(str(pdf)); doc.close()
jid = "zproute1"
d = server._job_dir(jid)
d.mkdir(parents=True, exist_ok=True)
server.JOBS[jid] = {"path": str(pdf), "pages": 1, "status": {}, "owner": None}
# three contours across the whole sheet, so both zones (50-150 and 300-500) sit inside the TIN
chains = [{"chain_id": 1, "class": "solid", "style_id": 1, "length_pt": 612, "points": [[0, 0], [612, 0]]},
          {"chain_id": 2, "class": "solid", "style_id": 1, "length_pt": 612, "points": [[0, 300], [612, 300]]},
          {"chain_id": 3, "class": "solid", "style_id": 1, "length_pt": 612, "points": [[0, 600], [612, 600]]}]
(d / "p0_result.json").write_text(json.dumps({"page": 0, "scale": {"ft_per_paper_inch": 72.0, "text": "1\" = 72'"}, "styles": [{"style_id": 1, "suggested_label": "c"}], "chains": chains, "engine_sig": "t"}))
zone_pts = [[50, 50], [150, 50], [150, 150], [50, 150]]
body = server.GradingZoneBody(polygon=zone_pts, design={"mode": "proposed"}, cell_ft=2.0,
                              existing_rows=[{"chain_id": 1, "z": 100}, {"chain_id": 2, "z": 100}, {"chain_id": 3, "z": 100}],
                              proposed_rows=[{"chain_id": 1, "z": 101}, {"chain_id": 2, "z": 101}, {"chain_id": 3, "z": 101}])
j = json.loads(bytes(server.earthwork_zone(jid, 0, body).body))
check(j["mode"] == "proposed" and abs(j["fill_yd3"] - 10000 / 27.0) < 3.0 and j["cut_yd3"] == 0.0, f"the route builds the proposed surface from the rows and differences it inside the zone (got {j.get('fill_yd3')}, {j.get('cut_yd3')})")
check(j["suggested_name"] == "BARN", f"the structure's name inside the zone is offered - a taller pipe label, a note and an elevation never win (got {j.get('suggested_name')!r})")
body2 = server.GradingZoneBody(polygon=zone_pts, design={"mode": "proposed"}, cell_ft=2.0, proposed_layers=[{"name": "Base", "thickness_in": 6}],
                               existing_rows=body.existing_rows, proposed_rows=body.proposed_rows)
j2 = json.loads(bytes(server.earthwork_zone(jid, 0, body2).body))
check(abs(j2["fill_yd3"] - 5000 / 27.0) < 3.0 and j2["layers"] and j2["layers"][0]["name"] == "Base", f"a base under the finished grade lowers the design and is listed (got {j2.get('fill_yd3')}, {j2.get('layers')})")
try:
    server.earthwork_zone(jid, 0, server.GradingZoneBody(polygon=zone_pts, design={"mode": "proposed"}, existing_rows=body.existing_rows))
    check(False, "no proposed rows must be refused")
except Exception as e:
    check(getattr(e, "status_code", None) == 400, f"no proposed rows -> 400 with the reason ({getattr(e, 'status_code', e)})")
jp = json.loads(bytes(server.earthwork_zone(jid, 0, server.GradingZoneBody(polygon=[[300, 300], [500, 300], [500, 500], [300, 500]], design={"mode": "pad", "elev_ft": 100.5}, existing_rows=body.existing_rows)).body))
check(jp["suggested_name"] == "GUIDE RESIDENCE", "the pad zone over the other building takes that building's name")


def zname(poly):
    jj = json.loads(bytes(server.earthwork_zone(jid, 0, server.GradingZoneBody(polygon=poly, design={"mode": "pad", "elev_ft": 100.5}, existing_rows=body.existing_rows)).body))
    return jj.get("suggested_name")


check(zname([[50, 300], [150, 300], [150, 400], [50, 400]]) == "POOL HOUSE", f"stacked lines of one size join into the name (got {zname([[50, 300], [150, 300], [150, 400], [50, 400]])!r})")
check(zname([[300, 520], [500, 520], [500, 600], [300, 600]]) is None, "plain words with no structure word never name a zone - it keeps its default (an abstention, not a guess)")
check(server._zone_text_name(jid, 0, [[50, 50], [150, 50]]) is None and server._zone_text_name("nope", 0, zone_pts) is None, "a two-point polygon or an unknown job: no name, no crash")
check(any(k[2] == 0 for k in server._ZONE_TEXT_CACHE), "the page's text is read once and cached - the modal's live preview asks on every keystroke")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all proposed-zone checks passed")
