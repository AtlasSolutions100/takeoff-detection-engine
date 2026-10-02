"""THE LIMIT LINE'S ROWS, THE BALANCE, ZONES, THE LAYER STACK, THE GRADE LOOKUP, THE WALLS AND
THE CREW MAP (2026-09-26; PLAN.md section 8, what the merge removed).

Pure functions first (cutfill/takeoff.py, cutfill/walls.py), then a synthetic two-plane project
through compute_cutfill: zones tiling the limit sum to the limit, the layer stack lowers the
proposed grade by its total, grade_at reads the plane back exactly, the crew map renders.

    python tests/test_cutfill_takeoff.py
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
STORE = tempfile.mkdtemp(prefix="cft_")
os.environ["TAKEOFF_JOBS_DIR"] = STORE
os.environ["CUTFILL_OUT"] = os.path.join(STORE, "cutfill")
os.environ["CUTFILL_UI_DIR"] = str(ROOT / "webui" / "cutfill")

import numpy as np  # noqa: E402

from cutfill import takeoff as tk  # noqa: E402
from cutfill.walls import wall_rows  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


# ---- the bid rows ---------------------------------------------------------------------------
tot = {"cut_cy": 1000.0, "fill_cy": 1600.0}
r = tk.takeoff_rows(tot, None, [], None, 30 / 72)
labels = [x["label"] for x in r["rows"]]
check(labels == ["Site Cut", "Site Fill", "Import Structural"] and [x["qty"] for x in r["rows"]] == [1000, 1600, 600],
      f"no limit, no strip, factors 1: the four-row shape the product filed before (fill-heavy: Import Structural 600) - {labels}")
r = tk.takeoff_rows({"cut_cy": 1234.0, "fill_cy": 0.0}, None, [], None, 30 / 72)
check([(x["label"], x["qty"]) for x in r["rows"]] == [("Site Cut", 1234), ("Export Cut", 1234)], "cut-only site: Site Cut 1,234 and Export Cut 1,234, never a doubled site cut")
check("neat line" in r["basis"], "factors of 1 say neat line")
r = tk.takeoff_rows(tot, None, [], {"export_factor": 1.25, "import_factor": 0.9}, 30 / 72)
imp = next(x for x in r["rows"] if x["label"] == "Import Structural")
check(imp["qty"] == 540.0 and "x 0.9" in imp["basis"] and "your factors" in r["basis"], "a typed import factor 0.9 shrinks the 600 CY import to 540 and the basis says so")
limit = {"area_sf": 224058.0, "perim_ft": 1900.0, "source": "picked", "family": "LIMIT OF DISTURBANCE"}
r = tk.takeoff_rows(tot, limit, [], {"strip_in": 8, "strip_factor": 1.0}, 30 / 72)
by = {x["label"]: x for x in r["rows"]}
check(by["Clear & Grub"]["qty"] == 224058.0 and by["Clear & Grub"]["unit"] == "SF", "Clear & Grub = the limit's area")
check(by["Silt / Construction Fence"]["qty"] == 1900.0 and r["fence_from"] == "limit", "no erosion-control lines: the fence is the limit's perimeter")
check(abs(by["Strip Topsoil"]["qty"] - 224058 * 8 / 12 / 27) < 0.1 and "(neat)" in by["Strip Topsoil"]["basis"], f"Strip Topsoil = area x 8 in neat = {by['Strip Topsoil']['qty']} CY (5,532)")
r = tk.takeoff_rows(tot, limit, [], {"strip_in": 8, "strip_factor": 1.15, "place_pct": 50}, 30 / 72)
by = {x["label"]: x for x in r["rows"]}
strip = 224058 * 8 / 12 / 27 * 1.15
check(abs(by["Strip Topsoil"]["qty"] - strip) < 0.1 and "x 1.15" in by["Strip Topsoil"]["basis"], "the 1.15 allowance multiplies the strip and is printed in the basis")
check(abs(by["Rough Place Topsoil"]["qty"] - strip / 2) < 0.1 and abs(by["Export Topsoil"]["qty"] - strip / 2) < 0.1, "50 % placed back: Rough Place and Export Topsoil are each half the strip")
r = tk.takeoff_rows(tot, limit, [], {"strip_in": 6, "strip_factor": 1.0, "place_depth_in": 12, "place_less_sf": 100000}, 30 / 72)
by = {x["label"]: x for x in r["rows"]}
placed = (224058 - 100000) * 1.0 / 27
check(abs(by["Rough Place Topsoil"]["qty"] - placed) < 0.1 and "100,000 SF footprints" in by["Rough Place Topsoil"]["basis"], "rough place by depth: 1 ft over the limit less the footprints and paving")
check("Import Topsoil" in by and abs(by["Import Topsoil"]["qty"] - (placed - 224058 * 6 / 12 / 27)) < 0.1 and "Export Topsoil" not in by, "placing more than was stripped imports the difference")
ero = [{"name": "SE-P-SEDIMENT LOG", "fam": "SE-P-SEDIMENT LOG", "length_pt": 2699.0, "n": 5}]
r = tk.takeoff_rows(tot, limit, ero, None, 30 / 72)
by = {x["label"]: x for x in r["rows"]}
check(abs(by["Silt / Construction Fence"]["qty"] - 2699 * 30 / 72) < 0.2 and r["fence_from"] == "erosion" and "SEDIMENT LOG" in by["Silt / Construction Fence"]["basis"]
      and any("not the limit line" in n for n in r["notes"]), "with erosion-control lines the fence is their length and the note names the limit line it did not use")
r = tk.takeoff_rows(tot, limit, ero, {"fence_source": "limit"}, 30 / 72)
check({x["label"]: x for x in r["rows"]}["Silt / Construction Fence"]["qty"] == 1900.0, "fence_source = limit forces the limit line's perimeter")
r = tk.takeoff_rows(None, limit, [], {"strip_in": 8}, 30 / 72)
check([x["label"] for x in r["rows"]] == ["Silt / Construction Fence", "Clear & Grub", "Strip Topsoil", "Export Topsoil"]
      and r["rows"][3]["qty"] == r["rows"][2]["qty"] and "nothing typed" in r["rows"][3]["basis"],
      "no volumes yet: the limit's rows (fence, Clear & Grub, strip) and, with nothing placed back, the whole strip exports and says why")

# ---- stitching a limit line from its pieces ---------------------------------------------------
sq = [(0, 0), (100, 0), (100, 100), (0, 100)]
pieces = [
    {"id": "core:1", "src": "core", "name": "LOD", "fam": "LOD", "pts": np.array([[0, 0], [100, 0]], float), "length": 100.0},
    {"id": "core:2", "src": "core", "name": "LOD", "fam": "LOD", "pts": np.array([[100, 100], [100, 5]], float), "length": 95.0},   # reversed, 5 pt gap
    {"id": "core:3", "src": "core", "name": "LOD", "fam": "LOD", "pts": np.array([[0, 100], [100, 100]], float), "length": 100.0},
    {"id": "core:4", "src": "core", "name": "LOD", "fam": "LOD", "pts": np.array([[0, 96], [0, 0]], float), "length": 96.0},
    {"id": "core:9", "src": "core", "name": "LOD", "fam": "LOD", "pts": np.array([[900, 900], [950, 900]], float), "length": 50.0},   # too far: left out
    {"id": "core:5", "src": "core", "name": "C-TOPO", "fam": "C-TOPO", "pts": np.array([[0, 50], [100, 50]], float), "length": 100.0},
]
st = tk.stitch_ring(pieces, "core:1")
check(st["n"] == 4 and st["leftover"] == 1 and sorted(st["joins"]) == [0.0, 0.0, 4.0] and st["gap"] == 5.0,
      f"four pieces joined end to end (one reversed), the far one left out, joins on the record and the 5 pt closure gap reported: {st['joins']}, gap {st['gap']}")
m = tk.ring_metrics(st["pts"], 0.5)
check(abs(m["area_sf"] - 100 * 100 * 0.25) < 0.5 and abs(m["perim_ft"] - 400 * 0.5) < 1.0, f"the stitched ring measures the square: {m}")
cands = tk.limit_candidates(pieces)
check(len(cands) == 1 and cands[0]["fam"] == "LOD" and cands[0]["n"] == 5, "limit_candidates names the LOD family (5 lines) and not the contours")
near = tk.nearest_line(pieces, 50, 1.5, tol=4)
check(near is not None and near["id"] == "core:1", "a click near a line finds it")
check(tk.limit_candidates([dict(pieces[5], name="GRADING LIMITS OF DISTURBANCE", fam="x")])[0]["fam"] == "x"
      and not tk.limit_candidates([dict(pieces[5], name="LIMITED ACCESS", fam="y")]), "the limit name test: grading limits of disturbance yes, limited access no")
check([f["fam"] for f in tk.erosion_families([dict(pieces[5], name="SE-P-SEDIMENT LOG", fam="a"), dict(pieces[5], name="SILT FENCE", fam="b"), dict(pieces[5], name="STORM", fam="c")])] == ["a", "b"],
      "erosion families: sediment logs and silt fence, not storm")

# ---- page_lines from a product result and from an engine analysis ------------------------------
core = {"styles": [{"style_id": 7, "layer": "BG-Limit of disturbance"}],
        "chains": [{"chain_id": 3, "style_id": 7, "layer": "BG-Limit of disturbance", "points": [[0, 0], [10, 0]], "length_pt": 10, "class": "dash1"},
                   {"chain_id": 4, "style_id": 7, "layer": "BG-Limit of disturbance", "points": [[0, 0], [10, 0]], "length_pt": 10, "class": "annotation"}]}
an = {"styles": [{"id": 0, "layer": "C-TOPO"}], "chains": [{"id": 1, "style": 0, "pts": [[0, 0], [5, 5]], "length": 7.07}]}
ln = tk.page_lines(an, core)
check([x["id"] for x in ln] == ["core:3", "engine:1"] and ln[0]["name"] == "BG-Limit of disturbance" and ln[1]["name"] == "C-TOPO",
      "page_lines lists the product's chains (annotations skipped) and the reader's, each named by its layer")

# ---- walls from TW / BW grades ---------------------------------------------------------------------
an_w = {"styles": [{"id": 0, "layer": "L-BOULDER WALL"}, {"id": 1, "layer": "C-TOPO"}],
        "chains": [{"id": 10, "style": 0, "pts": [[0, 0], [200, 0]], "length": 200.0}, {"id": 11, "style": 1, "pts": [[0, 300], [200, 300]], "length": 200.0}],
        "spots": [{"id": 0, "side": "wall", "tag": "TW", "z": 104.0, "x": 0, "y": -6, "label_id": 1},
                  {"id": 1, "side": "wall", "tag": "BW", "z": 100.0, "x": 0, "y": 6, "label_id": 2},
                  {"id": 2, "side": "wall", "tag": "TW", "z": 106.0, "x": 200, "y": -6, "label_id": 3},
                  {"id": 3, "side": "wall", "tag": "BW", "z": 100.0, "x": 200, "y": 6, "label_id": 4},
                  {"id": 4, "side": "wall", "tag": "TW", "z": 110.0, "x": 100, "y": 294, "label_id": 5}]}
w = wall_rows(an_w, 0.5, add_ft=1.0)
check(len(w["walls"]) == 1 and len(w["unpaired"]) == 1 and w["unpaired"][0]["z"] == 110.0 and "BW" in w["unpaired"][0]["why"],
      f"one wall from two TW / BW stations; the lone TW on the contour is listed unpaired with why, never given a height: {w['unpaired']}")
wl = w["walls"][0]
check(wl["lf"] == 100.0 and wl["n_stations"] == 2 and wl["h_min"] == 4.0 and wl["h_max"] == 6.0 and abs(wl["h_mean"] - 5.0) < 0.02, f"the wall is 100 ft, 4 to 6 ft high: {wl['lf']} ft, {wl['h_min']}-{wl['h_max']} ft")
# pairing: the nearest BW wins, never one above the TW, and an EW grade is the wall's end at grade
from cutfill.walls import pair_stations  # noqa: E402
sts, unp = pair_stations([{"id": 1, "tag": "TW", "z": 556.1, "x": 456, "y": 766, "label_id": 1}, {"id": 2, "tag": "BW", "z": 557.2, "x": 466, "y": 760, "label_id": 2},
                          {"id": 3, "tag": "BW", "z": 551.2, "x": 438, "y": 718, "label_id": 3}, {"id": 4, "tag": "TW", "z": 565.2, "x": 522, "y": 785, "label_id": 4},
                          {"id": 5, "tag": "EW", "z": 557.6, "x": 747, "y": 703, "label_id": 5}], pair_pt=96.0)
by = {round(s["tw"], 1): s for s in sts}
check(len(sts) == 3 and by[556.1]["bw"] == 551.2 and by[556.1]["h"] == 4.9 and by[565.2]["bw"] == 557.2 and by[565.2]["h"] == 8.0 and not unp,
      f"TW 556.1 skips the BW 557.2 beside it (above it) for BW 551.2; TW 565.2 takes BW 557.2: {[(s['tw'], s['bw'], s['h']) for s in sts]}")
check(by[557.6]["kind"] == "end" and by[557.6]["h"] == 0.0, "an EW grade is a zero-height station")
# an unnamed pen: only the stretch between a wall's own grades is its length; grades far apart are two
# walls; one station is no length until typed; a typed length gives the face
an_u = {"styles": [{"id": 0, "layer": ""}],
        "chains": [{"id": 20, "style": 0, "pts": [[0, 0], [1000, 0]], "length": 1000.0}],
        "spots": [{"id": 0, "side": "wall", "tag": "TW", "z": 104.0, "x": 100, "y": -6, "label_id": 11}, {"id": 1, "side": "wall", "tag": "BW", "z": 100.0, "x": 100, "y": 6, "label_id": 12},
                  {"id": 2, "side": "wall", "tag": "TW", "z": 106.0, "x": 300, "y": -6, "label_id": 13}, {"id": 3, "side": "wall", "tag": "BW", "z": 100.0, "x": 300, "y": 6, "label_id": 14},
                  {"id": 4, "side": "wall", "tag": "TW", "z": 103.0, "x": 900, "y": -6, "label_id": 15}, {"id": 5, "side": "wall", "tag": "BW", "z": 101.0, "x": 900, "y": 6, "label_id": 16}]}
w = wall_rows(an_u, 0.5, add_ft=1.0)
check(len(w["walls"]) == 2, f"an unnamed pen with grades 100 ft and then 300 ft apart: two walls, not one 500 ft line: {[(x['lf'], x['lf_known']) for x in w['walls']]}")
w1 = next(x for x in w["walls"] if x["n_stations"] == 2)
w2 = next(x for x in w["walls"] if x["n_stations"] == 1)
check(w1["lf"] == 100.0 and w1["lf_known"] and abs(w1["face_sf"] - 100 * 6.0) < 1.0 and w1["lf_drawn"] == 500.0,
      f"the two-station wall counts only the 100 ft between its grades (500 ft drawn): face {w1['face_sf']} (600)")
check(w2["lf"] is None and not w2["lf_known"] and w2["face_sf"] is None and w["n_no_length"] == 1 and "type the length" in w["note"],
      "the one-station wall has no length and no face until typed, and the note says so")
w = wall_rows(an_u, 0.5, add_ft=1.0, wall_lengths={w2["key"]: 40})
w2t = next(x for x in w["walls"] if x["n_stations"] == 1)
check(w2t["lf"] == 40 and w2t["lf_typed"] == 40 and abs(w2t["face_sf"] - 40 * (2.0 + 1.0)) < 0.1 and w["n_no_length"] == 0,
      f"a typed 40 ft gives the one-station wall its face: 40 x (2 + 1) = {w2t['face_sf']}")
check(abs(wl["face_sf"] - 100 * (5.0 + 1.0)) < 1.0, f"face SF = 100 ft x (5 ft avg + 1 ft) = 600: {wl['face_sf']}")


class _Plane:
    ok = True

    def __init__(self, z):
        self.z = z

    def sample(self, X, Y):
        return np.full(np.asarray(X).shape, self.z, float)


w = wall_rows(an_w, 0.5, add_ft=1.0, existing=_Plane(103.5), M=None, wall_d=0.5)
check(w["walls"][0]["dig"] and abs(w["walls"][0]["dig"]["mean_ft"] - 4.0) < 0.01, f"dig depth = existing 103.5 - (BW 100 - 0.5 ft footing) = 4.0 ft: {w['walls'][0]['dig']}")

# ---- block depths and the map ----------------------------------------------------------------------
grid = {"nx": 4, "ny": 2, "x0": 0.0, "y0": 0.0, "cell_pt": 10.0,
        "eg": [100, 100, 100, 100, 100, 100, 100, 100], "fg": [101, 103, 99, None, 102, 102, 98, None]}
bd = tk.block_depths(grid, cell_ft=5.0, block_ft=10.0)
check(len(bd) == 2 and bd[0]["dz"] == 2.0 and bd[0]["n"] == 4 and bd[1]["dz"] == -1.5 and bd[1]["n"] == 2, f"block means: the 2x2 blocks average their defined cells (+2.0 over 4 cells, -1.5 over 2): {bd}")

# ---- a synthetic project: two planes, zones, layers, grade_at, the crew map ------------------------
import pymupdf  # noqa: E402
from cutfill.project import Project, compute_cutfill, grade_at, limit_candidates_for, pick_limit, takeoff_preview, crew_map  # noqa: E402

pdf = os.path.join(STORE, "planes.pdf")
doc = pymupdf.open()
pg = doc.new_page(width=600, height=600)
pg.draw_rect(pymupdf.Rect(100, 100, 500, 500))     # a square ring on the sheet (the limit line)
doc.save(pdf)
doc.close()
p = Project.create("planes", pid="planes")
p.add_document(pdf, name="planes.pdf", copy=False)
key = "d0p0"
ps = p.page_settings(key)
ps["role"] = "both"
p.data["settings"].update({"scale_ft_per_in": 72.0, "scale_source": "typed", "cell_ft": 5.0, "display_page": key})   # 1 pt = 1 ft
# a hand-made analysis: existing = the plane z = 100 + 0.01 x (points on a lattice), proposed = 102 flat
pts_e, pts_p = [], []
for y in range(80, 521, 40):
    pts_e.append({"id": len(pts_e), "label_id": None, "x": 80.0, "y": float(y), "z": 100.8, "side": "existing", "tag": "EG", "anchor": "dot"})
    pts_e.append({"id": len(pts_e), "label_id": None, "x": 520.0, "y": float(y), "z": 105.2, "side": "existing", "tag": "EG", "anchor": "dot"})
    pts_p.append({"id": 100 + len(pts_p), "label_id": None, "x": 80.0, "y": float(y), "z": 102.0, "side": "proposed", "tag": "FG", "anchor": "dot"})
    pts_p.append({"id": 100 + len(pts_p), "label_id": None, "x": 520.0, "y": float(y), "z": 102.0, "side": "proposed", "tag": "FG", "anchor": "dot"})
an_s = {"engine": "test", "key": key, "doc": 0, "page": 0, "width": 600.0, "height": 600.0, "sheet": "T1", "page_role": "both", "lines_from": "engine",
        "styles": [{"id": 0, "layer": "C-LIMIT OF DISTURBANCE", "color": [1, 0, 0], "width": 1.0}], "families": [], "gaps": [], "labels": [],
        "chains": [{"id": 1, "style": 0, "length": 1600.0, "ink": 1600.0, "closed": True, "candidate": False, "n": 1,
                    "pts": [[100, 100], [500, 100], [500, 500], [100, 500], [100, 100]]}],
        "contours": [], "spots": pts_e + pts_p, "counts": {}, "notes": [], "scale_candidates": [], "scale_default": None}
os.makedirs(os.path.dirname(p.analysis_path(key)), exist_ok=True)
json.dump(an_s, open(p.analysis_path(key), "w"))
p.save()
c = limit_candidates_for(p, key)
check(c["source"] == "engine" and len(c["limit"]) == 1 and "LIMIT OF DISTURBANCE" in c["limit"][0]["name"] and abs(c["limit"][0]["length_ft"] - 1600) < 1,
      f"the sheet's limit family is offered with its length: {c['limit']}")
r = pick_limit(p, key, x=300, y=101, tol=6)
check(r["metrics"]["area_sf"] == 160000.0 and r["metrics"]["perim_ft"] == 1600.0 and r["limit"]["source"] == "picked", f"a click on the ring sets the 400 x 400 limit: {r['metrics']}")
# existing rises 0.01 ft per ft from x = 80 (100.8) to x = 520 (105.2): z = 100 + 0.01 x; proposed flat 102
res = compute_cutfill(p)
check(not res.get("error"), f"compute: {res.get('error')}")
t = res["totals"]
# inside 100..500: dz = 102 - (100 + 0.01 x) -> fill for x < 200 (1 ft avg over 100 ft), cut for x > 200 (1.5 ft avg over 300 ft)
exp_fill = 100 * 400 * 0.5 / 27
exp_cut = 300 * 400 * 1.5 / 27
check(abs(t["fill_cy"] - exp_fill) / exp_fill < 0.03 and abs(t["cut_cy"] - exp_cut) / exp_cut < 0.03, f"the volumes of two planes: cut {t['cut_cy']} (exp {exp_cut:.0f}), fill {t['fill_cy']} (exp {exp_fill:.0f})")
rows = {x["label"]: x for x in res["takeoff"]["rows"]}
check(rows["Clear & Grub"]["qty"] == 160000.0 and rows["Silt / Construction Fence"]["qty"] == 1600.0 and "C-LIMIT OF DISTURBANCE" in rows["Clear & Grub"]["basis"],
      "compute's takeoff rows carry the picked limit's area and perimeter with the line's name")
# zones tiling the limit: their sum equals the limit's volumes
p.data["settings"]["zones"] = [{"id": 1, "name": "west", "polygon": [[100, 100], [300, 100], [300, 500], [100, 500]], "section_ft": 0},
                               {"id": 2, "name": "east", "polygon": [[300, 100], [500, 100], [500, 500], [300, 500]], "section_ft": 0}]
p.save()
res = compute_cutfill(p)
z = {q["name"]: q for q in res["zones"]}
check(abs(z["west"]["cut_cy"] + z["east"]["cut_cy"] - t["cut_cy"]) < 0.05 * t["cut_cy"] + 1 and abs(z["west"]["fill_cy"] + z["east"]["fill_cy"] - t["fill_cy"]) < 0.05 * t["fill_cy"] + 1
      and abs(z["west"]["area_sf"] + z["east"]["area_sf"] - 160000) < 1, f"two zones tiling the limit sum to the limit's numbers: {z['west']['cut_cy']}+{z['east']['cut_cy']} vs {t['cut_cy']}; fill {z['west']['fill_cy']}+{z['east']['fill_cy']} vs {t['fill_cy']}")
check(z["west"]["fill_cy"] > 0 and z["east"]["cut_cy"] > z["west"]["cut_cy"], "the west zone holds the fill, the east zone the cut")
check(res["takeoff"]["zones_total"]["n"] == 2, "the rows carry the zones' total for the panel")
# a zone with its own section: proposed lowered 1 ft inside it only
p.data["settings"]["zones"][1]["section_ft"] = 1.0
p.save()
res2 = compute_cutfill(p)
z2 = {q["name"]: q for q in res2["zones"]}
check(abs(z2["west"]["cut_cy"] - z["west"]["cut_cy"]) < 0.01 and z2["east"]["cut_cy"] > z["east"]["cut_cy"] + 200 * 400 * 0.9 / 27,
      f"a 1 ft section under the east zone deepens its cut by about its area x 1 ft and leaves the west zone alone: {z['east']['cut_cy']} -> {z2['east']['cut_cy']}")
# itemized (2026-09-28, Koby: "total cut is 10k, engineer boxes lot a 3k, island 5k, lot b 2k"): the
# zones come off the limit's own grid, so they ADD UP to the totals, and each files its own rows
t2 = res2["totals"]
check(res2["zones_itemized"] and abs(z2["west"]["cut_cy"] + z2["east"]["cut_cy"] - t2["cut_cy"]) < 0.2
      and abs(z2["west"]["fill_cy"] + z2["east"]["fill_cy"] - t2["fill_cy"]) < 0.2 and res2["zones_rest"] is None,
      f"the zones tiling the limit ARE its totals (each at its own depth): {z2['west']['cut_cy']} + {z2['east']['cut_cy']} = {t2['cut_cy']}")
rows2 = {x["label"]: x for x in res2["takeoff"]["rows"]}
check({"Site Cut – east", "Site Fill – west"} <= set(rows2) and "Site Cut" not in rows2 and "Site Fill" not in rows2
      and rows2["Site Cut – east"]["kind"] == "cut" and "12 in under finished grade" in rows2["Site Cut – east"]["basis"],
      f"each zone files its own Site Cut / Site Fill row, with its depth in the basis: {sorted(rows2)}")
cut_rows = sum(x["qty"] for x in res2["takeoff"]["rows"] if x["kind"] == "cut")
fill_rows = sum(x["qty"] for x in res2["takeoff"]["rows"] if x["kind"] == "fill")
net_row = next((x for x in res2["takeoff"]["rows"] if x["label"] in ("Export Cut", "Import Structural")), None)
check(net_row is not None and abs(abs(cut_rows - fill_rows) - net_row["qty"]) < 0.01,
      f"the net is the rows' own sum, so the takeoff adds up: cut {cut_rows} - fill {fill_rows} -> {net_row}")
g2 = res2["grid"]
kw = next(i for i, v in enumerate(g2["fg"]) if v is not None and g2["zone"][i] == 0)
ke = next(i for i, v in enumerate(g2["fg"]) if v is not None and g2["zone"][i] == 1)
check(g2["zone_ids"] == [1, 2] and g2["zsec"][kw] == 0.0 and g2["zsec"][ke] == 1.0,
      f"the grid says which zone holds each cell and the depth it was dug to: {g2['zone_ids']}, {g2['zsec'][kw]} / {g2['zsec'][ke]}")
# the rest of the tests are about the limit and its layer stack alone
p.data["settings"]["zones"] = []
p.save()
# the layer stack: its total is the section
from cutfill import server as cfs  # noqa: E402
from cutfill.placement import inputs_sig  # noqa: E402
import asyncio  # noqa: E402


class _R:
    def __init__(self, b):
        self._b = b

    async def json(self):
        return self._b


cfs._projects["planes"] = p
asyncio.run(cfs.patch_settings("planes", _R({"layers": [{"name": "asphalt", "thickness_in": 4}, {"name": "base", "thickness_in": 8}]})))
check(abs(p.data["settings"]["section_ft"] - 1.0) < 1e-9 and len(p.data["settings"]["layers"]) == 2, "two layers of 4 and 8 in make a 1 ft section")
res3 = compute_cutfill(p)
# lowering the proposed grade 1 ft over the 160,000 SF limit moves the NET by exactly area x 1 ft / 27
check(abs((res3["totals"]["net_cy"] - t["net_cy"]) + 160000 / 27) < 3 and res3["section_ft"] == 1.0,
      f"the stack lowers the proposed grade 1 ft over the limit: net {t['net_cy']} -> {res3['totals']['net_cy']} (expected {t['net_cy'] - 160000 / 27:.0f})")
# everything read off the grid is to subgrade too: the heights, the max depths, the map's blocks
g0, g3 = res["grid"], res3["grid"]
k0 = next(i for i, v in enumerate(g0["fg"]) if v is not None)
check(g3["section_ft"] == 1.0 and g0["section_ft"] == 0.0 and abs(g3["fg"][k0] - (g0["fg"][k0] - 1.0)) < 1e-6,
      f"under a 1 ft stack the grid's proposed heights are the subgrade: {g0['fg'][k0]} -> {g3['fg'][k0]}")
check(abs(res3["totals"]["max_cut_ft"] - (t["max_cut_ft"] + 1.0)) < 0.011
      and abs(res3["totals"]["max_fill_ft"] - max(0.0, t["max_fill_ft"] - 1.0)) < 0.011,
      f"the max depths are to subgrade and never negative: cut {t['max_cut_ft']} -> {res3['totals']['max_cut_ft']}, "
      f"fill {t['max_fill_ft']} -> {res3['totals']['max_fill_ft']}")
b0 = tk.block_depths(g0, cell_ft=5.0, block_ft=50.0)
b3 = tk.block_depths(g3, cell_ft=5.0, block_ft=50.0, sub=tk.grid_sub(res3))
check(len(b0) == len(b3) and len(b0) > 10 and all(abs((x["dz"] - 1.0) - y["dz"]) < 0.11 for x, y in zip(b0, b3)),
      f"the crew map's block depths are 1 ft lower under the stack: {b0[0]['dz']} -> {b3[0]['dz']} ({len(b0)} blocks)")
hs0 = sum(h["volume_cy"] for h in res["hotspots"] if h["kind"] == "cut")
hs3 = sum(h["volume_cy"] for h in res3["hotspots"] if h["kind"] == "cut")
check(hs3 > hs0 + 1000, f"the hotspots are to subgrade: the cut areas hold more under the stack ({hs0:.0f} -> {hs3:.0f} CY)")
# an older stored result kept finished grade in its grid: its section comes off when it is read
old = dict(res3, grid={k: v for k, v in g3.items() if k not in ("section_ft", "w")})
old["grid"]["fg"] = [None if v is None else v + 1.0 for v in g3["fg"]]
check(tk.grid_sub(old) == 1.0 and tk.grid_sub(res3) == 0.0 and tk.grid_section(old) == 1.0 and tk.grid_section(res3) == 1.0
      and tk.grid_sub(res) == 0.0 and tk.grid_section(res) == 0.0, "an older result's section comes off its grid when it is read")
bo = tk.block_depths(old["grid"], cell_ft=5.0, block_ft=50.0, sub=tk.grid_sub(old))
check(len(bo) == len(b3) and all(abs(x["dz"] - y["dz"]) < 0.11 for x, y in zip(bo, b3)), "an older result prints the same depths as a fresh one")
png3 = crew_map(p, res3, block_ft=5.0)
check(png3[:8] == b"\x89PNG\r\n\x1a\n", "the crew map of the stacked result renders, at a block too small for its numbers too")
asyncio.run(cfs.patch_settings("planes", _R({"layers": []})))
check(p.data["settings"]["section_ft"] == 0.0, "an empty stack is finished grade")
asyncio.run(cfs.patch_settings("planes", _R({"region": None})))
check(p.data["settings"]["limit"] is None and p.data["settings"]["region"] is None, "clearing the region clears the limit record")
asyncio.run(cfs.patch_settings("planes", _R({"region": [[100, 100], [500, 100], [500, 500], [100, 500]]})))
check(p.data["settings"]["limit"] == {"source": "drawn"}, "a region set through settings is a drawn limit")
# grade_at: the existing plane read back
g = grade_at(p, key, [[200, 300], [400, 300], [5, 5]], "existing")
check(g["z"][0] is not None and abs(g["z"][0] - 102.0) < 0.05 and abs(g["z"][1] - 104.0) < 0.05 and g["z"][2] is None and g["n_found"] == 2,
      f"grade_at reads the existing plane (102 at x=200, 104 at x=400) and None off the surface: {g['z']}")
g = grade_at(p, key, [[300, 300]], "proposed")
check(abs(g["z"][0] - 102.0) < 0.05, "grade_at on the proposed surface")
# the preview before / after compute, and the crew map
p.data["settings"]["takeoff"] = {"strip_in": 6}
p.save()
res4 = compute_cutfill(p)
res4["inputs"] = inputs_sig(p)            # what the compute route keeps with it (a result without it is an older one's)
json.dump(res4, open(os.path.join(p.dir, "result.json"), "w"))
tp = takeoff_preview(p)
check(tp["computed"] and {x["label"] for x in tp["rows"]} >= {"Clear & Grub", "Strip Topsoil", "Site Cut", "Site Fill", "Export Topsoil"},
      f"the preview after a compute carries the limit rows and the volume rows: {[x['label'] for x in tp['rows']]}")
# the cell CSV: its rows sum to the totals (a cell on the edge holds only its part inside). The totals
# sit in the lines above the column names, one cell each, never in the data columns: a TOTAL row
# under the cells put the area in the cut_cy column and a spreadsheet's sum of it gave 499,492 CY
# for 31,233 (check 5A)
import csv as _csv  # noqa: E402


def _cells(txt):
    """(the lines above the column names, the column names, the cells) of an exported CSV."""
    rows = list(_csv.reader(io.StringIO(txt)))
    hi = next(i for i, r in enumerate(rows) if r[:2] == ["row", "col"])
    return rows[:hi], rows[hi], [r for r in rows[hi + 1:] if r]


def _column_sum(txt, name):
    """What a spreadsheet's sum of the WHOLE column gives: every number in it, on whatever line."""
    ci = _cells(txt)[1].index(name)
    s = 0.0
    for r in _csv.reader(io.StringIO(txt)):
        try:
            s += float(r[ci])
        except (IndexError, ValueError):
            pass
    return s


cfs._results.pop("planes", None)
csv_txt = cfs.export_csv("planes").body.decode("utf-8")
above, head, cells = _cells(csv_txt)
csv_cut = _column_sum(csv_txt, "cut_cy")
csv_fill = _column_sum(csv_txt, "fill_cy")
t4 = res4["totals"]
check(len(cells) == t4["cells_both"] and all(r[0].isdigit() for r in cells) and head[-1] == "inside"
      and abs(csv_cut - t4["cut_cy"]) < 0.002 * t4["cut_cy"] + 0.5 and abs(csv_fill - t4["fill_cy"]) < 0.002 * t4["fill_cy"] + 0.5,
      f"a whole-column sum gives the totals, no totals row among the cells: cut {csv_cut:.1f} vs {t4['cut_cy']}, "
      f"fill {csv_fill:.1f} vs {t4['fill_cy']} over {len(cells)} cells")
check(above and all(len(r) == 1 for r in above) and f"cut {t4['cut_cy']:,.1f} CY" in above[0][0] and f"fill {t4['fill_cy']:,.1f} CY" in above[0][0],
      f"the totals are said above the column names, in one cell: {above}")
check("proposed_ft" in head and "subgrade_ft" not in head, f"no layer stack: one proposed column, the finished grade ({head})")
# under a layer stack the finished grade and the subgrade have a column each, named for what they hold
# (kept as the compute route keeps a result: with the record of what it read, taken now - this checks the columns)
cfs._results["planes"] = dict(res3, inputs=inputs_sig(p))
csv3 = cfs.export_csv("planes").body.decode("utf-8")
above3, head3, cells3 = _cells(csv3)
ip, isg, ie, idp = (head3.index(c) for c in ("proposed_ft", "subgrade_ft", "existing_ft", "depth_ft(+fill/-cut)"))
check(len(cells3) > 100 and all(abs(float(r[ip]) - float(r[isg]) - 1.0) < 0.002 and abs(float(r[isg]) - float(r[ie]) - float(r[idp])) < 0.002 for r in cells3)
      and any("subgrade" in r[0] and "1 ft below" in r[0] for r in above3),
      f"under the 1 ft stack: proposed_ft is the finished grade, subgrade_ft 1 ft below it, the depth is to the subgrade, the header says so ({head3})")
check(abs(_column_sum(csv3, "cut_cy") - res3["totals"]["cut_cy"]) < 0.002 * res3["totals"]["cut_cy"] + 0.5,
      "and its cut column still sums to the stacked totals")
# a result stored before the grid carried its section (its grid at finished grade) gives the same cells
cfs._results["planes"] = dict(old, inputs=inputs_sig(p))
_, head_o, cells_o = _cells(cfs.export_csv("planes").body.decode("utf-8"))
check(head_o == head3 and len(cells_o) == len(cells3)
      and all(abs(float(a) - float(b)) < 0.002 for ra, rb in zip(cells_o, cells3) for a, b in zip(ra, rb)),
      "an older stored result gives the same columns and cells")
cfs._results.pop("planes", None)
png = crew_map(p, res4, block_ft=50.0)
check(png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000, f"the crew map renders a PNG ({len(png)} bytes)")
from PIL import Image  # noqa: E402
im = Image.open(io.BytesIO(png)).convert("RGB")
px = np.asarray(im)
red = ((px[:, :, 0] > 150) & (px[:, :, 1] < 140) & (px[:, :, 2] < 140)).sum()
blue = ((px[:, :, 2] > 150) & (px[:, :, 0] < 140) & (px[:, :, 1] < 170)).sum()
check(red > 0 and blue > 0 and red > blue, f"red (cut) and blue (fill) cells on the map, more red than blue on this site: {red} / {blue} px")

print("\n%d failure(s)" % fails)
sys.exit(1 if fails else 0)
