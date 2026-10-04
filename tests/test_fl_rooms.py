"""THE ROOMS' FIGURES ON THE SERVER (takeoff_engine/fl_rooms.py; ruling P, 2026-10-02).

These were the review panel's own arithmetic (tests/test_flooring_ui.js held them until the panel stopped
computing): the same cases, now against the server. Synthetic rooms at 1/4" = 1'-0" (18 pt per foot).
  * base = the ring less the UNION of the openings and the edges left out; an opening beside a corner
    slides to keep its width, and is cut only by an edge shorter than itself; a 2.0 room keeps its old base
  * a room with no floor type is its own "unassigned" row, never dropped
  * net = area less the fixtures ticked out and the shapes clicked out, never negative
  * one flooring row per type, its quantity the ORDER and its parts adding up to it; no price on any row,
    no product words in any label; a 2.0 type keeps its own rows
  * wall area = perimeter x ceiling - each opening at door height
  * transitions: a doorway two rooms of different types share, found by geometry; a closure counts

    python tests/test_fl_rooms.py
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from takeoff_engine import fl_rooms as R  # noqa: E402
from takeoff_engine import fl_job as J  # noqa: E402

fails = 0


def check(ok, msg, got=None):
    global fails
    print(("  ok   " if ok else "  FAIL ") + msg + ("" if ok or got is None else f"  (got {got!r})"))
    if not ok:
        fails += 1


PPF = 18.0
FPP = 1 / PPF


def sq(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def room(pts, openings=None, **kw):
    r = {"uid": kw.pop("uid", "r"), "name": kw.pop("name", "R"), "pts": pts, "area_pt2": R.poly_area(pts), "perim_pt": R.poly_perim(pts),
         "openings": openings if openings is not None else [], "scale": {"how": "sheet", "ft": 4.0}, "type": kw.pop("type", "t1")}
    r.update(kw)
    return J.clean_room(r)


print("base")
R20x15 = sq(0, 0, 360, 270)                    # 20 x 15 ft: perimeter 70 LF
door = {"x": 124, "y": 270, "w_pt": 54, "w_ft": None, "by": "from the swing"}
b = R.base(room(R20x15, [dict(door)]), FPP)
check(abs(b["lf"] - 67) < 1e-9 and abs(b["ring_lf"] - 70) < 1e-9, "perimeter 70 - a 3'-0\" door = base 67 LF", b["lf"])
b = R.base(room(R20x15, [dict(door, w_ft=3.5)]), FPP)
check(abs(b["lf"] - 66.5) < 1e-9, "a typed width (3'-6\") wins over the one read off the sheet")
b = R.base(room(R20x15, [dict(door)], base_off=[[0, 270, 360, 270]]), FPP)
check(abs(b["lf"] - 50) < 1e-9, "a door on a left-out edge is not taken out twice (70 - 20 = 50)")
b = R.base(room(R20x15, [dict(door)], base_off=[[100, 270, 140, 270]]), FPP)
check(abs(b["lf"] - 67) < 1e-9, "a left-out run inside the door's span never puts the base UP")
b = R.base(room(R20x15, [{"x": 359, "y": 200, "w_pt": 400, "by": "from the swing"}]), FPP)
check(b["openings"][0]["clipped"] and abs(b["lf"] - 55) < 1e-9, "an opening wider than its edge is cut to it (70 - 15 = 55)")
b = R.base(room(R20x15, [{"x": 180, "y": 0, "w_pt": 0, "w_ft": None, "by": "added by hand"}]), FPP)
check(abs(b["lf"] - 70) < 1e-9, "an opening added by hand takes nothing out until its width is typed")
b = R.base(room(R20x15, [{"x": 5, "y": 0, "w_pt": 0, "w_ft": 3, "by": "added by hand"}]), FPP)
check(abs(b["openings"][0]["w_lf"] - 3) < 1e-9 and b["openings"][0]["slid"] and not b["openings"][0]["clipped"],
      "a 3'-0\" door clicked 5 pt from a corner is 3'-0\" out, moved along its edge to fit")
leg = J.clean_room({"pts": sq(0, 0, 180, 180), "area_pt2": 32400, "perim_pt": 720, "openings": None, "legacy": {"openings_pt": 54}})
check(abs(R.base(leg, FPP)["lf"] - 37) < 1e-9 and R.base(leg, FPP)["legacy"], "a 2.0 room keeps its old base (40 - 3)")
run = R.straight_run([[0, 0], [120, 0.4], [240, 0], [360, 0.3], [360, 270], [0, 270]], 1)
check(run[0] == 0 and run[2] == 360, "a click on one short edge of a picked ring takes the whole straight wall")
check(R.base(room(R20x15), None) is None, "no scale: no base, never 0")

print("net, deductions, wall area")
bath = room(sq(0, 0, 18 * 7.5, 18 * 7.08), objects=[{"kind": "tub", "area_pt2": 9.2 * 324, "deduct": True, "pts": []},
                                                   {"kind": "cabinet", "area_pt2": 2.2 * 324, "deduct": False, "pts": []}],
            deductions=[{"name": "clicked out", "area_pt2": 1.5 * 324, "pts": []}])
f = R.figures(bath, FPP)
check(abs(f["deduct_sf"] - 10.7) < 1e-9 and abs(f["net_sf"] - (f["area_sf"] - 10.7)) < 1e-9,
      "deducted = ticked objects + clicked-out shapes (9.2 + 1.5); net = area less them")
check(f["deduct_text"] == "tub 9.2 SF, clicked out 1.5 SF", "the deductions in words: " + f["deduct_text"])
tiny = room(sq(0, 0, 18, 18), objects=[{"kind": "tub", "area_pt2": 9 * 324, "deduct": True, "pts": []}])
check(R.figures(tiny, FPP)["net_sf"] == 0, "a deduction can not make a room negative")
check(R.figures(bath, None)["net_sf"] is None, "no scale: net is None, not 0")
bed = room(sq(0, 0, 216, 216), [{"x": 100, "y": 0, "w_pt": 48, "by": "from the swing"}], ceiling_ft=9.094, ceiling_by="sheet")
w = R.figures(bed, FPP)["wall"]
check(abs(w["gross"] - 48 * 9.094) < 1e-6 and abs(w["doors"] - (48 / 18) * 6.667) < 1e-6 and w["by"] == "sheet",
      "wall area = perimeter x height (48 LF x 9'-1 1/8\"), each opening out at door height (6'-8\")")
check(R.figures(room(sq(0, 0, 216, 216)), FPP)["wall"] is None, "no height: no wall area, never 0")
check(R.figures(room(sq(0, 0, 216, 216)), FPP, {"default_ceiling_ft": 9})["wall"]["by"] == "default", "the sheet's default height fills a room with no note")
low = room(sq(0, 0, 216, 216), [{"x": 100, "y": 0, "w_pt": 54, "by": "from the swing"}], ceiling_ft=6.0, ceiling_by="typed")
check(abs(R.figures(low, FPP)["wall"]["doors"] - 18) < 1e-6, "a ceiling lower than a door caps the opening at the ceiling (3 ft x 6 ft)")

print("totals and rows")
T = [{"id": "t1", "name": "Carpet", "unit": "SF", "waste_pct": "10", "mfr": "AcmeMfr", "colour": "Red", "base_code": "WB-1"},
     {"id": "t2", "name": "Tile", "unit": "SF", "waste_pct": "0"}]
hall = room(sq(0, 0, 18 * 10, 18 * 20), uid="h", name="HALL", type="t1")
bedr = room(sq(400, 0, 400 + 18 * 10, 18 * 15), uid="b", name="BED", type="t1")
bth = room(sq(800, 0, 800 + 18 * 5, 18 * 10), uid="t", name="BATH", type="t2")
cl = room(sq(1200, 0, 1200 + 18 * 3, 18 * 4), uid="c", name="closet", type=None)
rooms = [hall, bedr, bth, cl]
figs = [R.figures(r, FPP) for r in rooms]
tot = R.sheet_totals(T, rooms, figs)
check(len(tot) == 3 and tot[2]["i"] == -1 and tot[2]["rooms"] == 1 and abs(tot[2]["sf"] - 12) < 1e-9,
      "an untyped room is its own 'unassigned' row, never dropped")
check(abs(tot[0]["sf"] - 350) < 1e-9 and tot[0]["rooms"] == 2 and tot[0]["order"] == 385, "Carpet: 350 SF net, order 385 (10 % waste, up to a whole SF)")
it = R.items(T, rooms, figs, {}, FPP)
lab = [x["label"] for x in it]
check(lab[:4] == ["Flooring — Carpet", "Base/molding — Carpet", "Flooring — Tile", "Base/molding — Tile"],
      "one floor row and one base row per type with rooms, in type order: " + " | ".join(lab))
check(all("unit_price" not in x and x.get("src") == "flooring" for x in it), "no row carries a price; every row says it is the flooring's")
check(not any(s in " ".join(lab) for s in ("AcmeMfr", "Red", "WB-1")), "no product words and no base code in any label (ruling S)")
fr = it[0]
check(fr["qty"] == 385 and sum(d["qty"] for d in fr["details"]) == 385 and fr["details"][-1]["name"].startswith("waste 10%"),
      "the flooring row is the order and its parts (the rooms' net and one waste part) add up to it", fr["details"])
check(not any(x["label"].startswith("Waste —") for x in it), "no separate waste row for a type of this build")
check(not any("unassigned" in x["label"] for x in it), "the unassigned row never reaches the takeoff")
nw = R.items([{"id": "t1", "name": "Carpet", "unit": "SF", "waste_pct": None}], [hall], [R.figures(hall, FPP)], {}, FPP)
check(nw[0]["label"] == "Flooring — Carpet (waste not set)" and nw[0]["qty"] == 200, "waste not set: the row says so and carries the net")
legacy = R.items([{"id": "t1", "name": "LVP", "unit": "SF", "waste_pct": "10", "legacy_rows": True}], [hall], [R.figures(hall, FPP)], {}, FPP)
check([x["label"] for x in legacy] == ["Flooring — LVP", "Waste — LVP (10%)", "Base/molding — LVP"] and legacy[1]["qty"] == 20,
      "a type migrated from 2.0 keeps its own rows: net, waste (unrounded), base", [(x["label"], x["qty"]) for x in legacy])
sy = R.items([{"id": "t1", "name": "CPT", "unit": "SY", "waste_pct": "7", "kind": "roll"}], [hall], [R.figures(hall, FPP)], {}, FPP)
check(sy[0]["unit"] == "SY" and sy[0]["qty"] == math.ceil(200 / 9 * 1.07) and "(floor-area order)" in sy[0]["label"],
      "a type bid in SY: its row in SY, and a roll good says its order is from the floor area", sy[0])

print("transitions")
B1 = {"x0": 100, "y0": 200, "x1": 100, "y1": 254, "r": 54}
a = J.clean_room({"uid": "a", "pts": sq(0, 0, 100, 300), "area_pt2": 1, "perim_pt": 1, "openings": [], "bridges": [B1], "type": "t1", "scale": {"ft": 4}})
bb = J.clean_room({"uid": "b", "pts": sq(100, 0, 300, 300), "area_pt2": 1, "perim_pt": 1, "openings": [], "bridges": [], "type": "t2", "scale": {"ft": 4}})
tr = R.transitions([a, bb], ["t1", "t2"], [])
check(len(tr["transitions"]) == 1 and tr["open"] == 0 and tr["transitions"][0]["width_pt"] == 54,
      "a door one room's fill bridged and the other room's outline runs along: one transition, the door's width")
tr = R.transitions([a, dict(bb, type="t1")], ["t1", "t2"], [])
check(not tr["transitions"] and tr["open"] == 0, "the same floor on both sides: no transition")
tr = R.transitions([a], ["t1", "t2"], [])
check(not tr["transitions"] and tr["open"] == 1, "a doorway to a room not picked yet is counted as open")
wic = J.clean_room({"uid": "w", "pts": sq(0, 300, 200, 500), "area_pt2": 1, "perim_pt": 1, "openings": [], "type": "t1", "scale": {"ft": 4}})
b2 = J.clean_room({"uid": "x", "pts": sq(200, 300, 400, 500), "area_pt2": 1, "perim_pt": 1, "openings": [], "type": "t2", "scale": {"ft": 4}})
tr = R.transitions([wic, b2], ["t1", "t2"], [[200, 380, 200, 420]])
check(len(tr["transitions"]) == 1 and tr["transitions"][0]["src"] == "closure" and abs(tr["transitions"][0]["width_pt"] - 40) < 1e-9,
      "a gap closed by hand between two types is a strip, the closure's length wide")
check(R.ft_in(2.9993) == "3'-0\"" and R.ft_in(2.5) == "2'-6\"", "feet-inches print as the panel printed them")

print("\nall passed" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
