"""ROOMS: the enclosed floor area behind a click (takeoff_engine/rooms.py).

Synthetic plan, every number known by construction, at 1/4" = 1'-0"
(4 ft per paper inch -> 1 ft = 18 pt; a 20 ft room is 500 px at 100 dpi,
so the 2 px wall and 3 px seal are a few hundredths of a foot):

   room A  20 ft x 15 ft = 300 SF, perimeter 70 LF
   room B  12 ft x 15 ft = 180 SF, perimeter 54 LF
   a 3 ft door in the wall between them, drawn as a GAP in the wall line
   plus a swing-arc opening record (hinge, r = 3 ft, arc midpoint), the
   shape extract.door_swings emits
   a 4 ft x 4 ft closet drawn inside room A (a hole)

What is pinned:
  * the click in room A gives 300 SF within 1.5% (raster at 100 dpi),
    perimeter 70 LF within 2%, ONE door opening of 3 LF on its boundary,
    the closet as a hole of 16 SF that is NOT in the area
  * room B's click gives 180 SF and the same door
  * with the door record removed the two rooms MERGE (the gap is open) -
    that is the doorway bridging doing its job
  * a click on a wall line says so; a room with a wall style hidden LEAKS
    and says so, never returning the sheet as a room
  * the name is the room's label, not a dimension string
  * no scale -> pt only, sf None

Run:  python tests/test_rooms.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from takeoff_engine import rooms  # noqa: E402

FAILS: list[str] = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


FT = 18.0                     # pt per ft at 1/4" = 1'-0" (4 ft per paper inch: 72 / 4)
W_PT, H_PT = 72 * 14, 72 * 10 # a 14x10 in sheet
OX, OY = 100.0, 100.0         # plan origin on the sheet


def P(xf, yf):
    return [OX + xf * FT, OY + yf * FT]


def chain(cid, pts, style=0):
    return {"chain_id": cid, "style_id": style, "points": pts}


def plan(with_door=True, wall_style=0, closet=True):
    # room A: (0,0)-(20,15); room B: (20,0)-(32,15); door at x=20, y 6..9
    chains = [
        chain(0, [P(0, 0), P(32, 0)], wall_style),            # top
        chain(1, [P(0, 15), P(32, 15)], wall_style),          # bottom
        chain(2, [P(0, 0), P(0, 15)], wall_style),            # left
        chain(3, [P(32, 0), P(32, 15)], wall_style),          # right
        chain(4, [P(20, 0), P(20, 6)], wall_style),           # party wall, above the door
        chain(5, [P(20, 9), P(20, 15)], wall_style),          # party wall, below the door
    ]
    if closet:
        chains.append(chain(6, [P(2, 2), P(6, 2), P(6, 6), P(2, 6), P(2, 2)], wall_style))
    # a dimension string wall-to-wall across room A on its own style (NOT a
    # wall - but it would cut the room in two if it were counted as one)
    chains.append(chain(7, [P(0, 12), P(20, 12)], 9))
    openings = []
    if with_door:
        # hinge at (20,6), r = 3 ft, leaf swings into room B: arc midpoint
        # 45 deg between the closed leaf (down the wall, +y) and open (+x)
        hx, hy, r = 20, 6, 3
        import math
        mid = (hx + r * math.cos(math.radians(45)), hy + r * math.sin(math.radians(45)))
        openings.append({"cx": P(hx, hy)[0], "cy": P(hx, hy)[1], "r": r * FT,
                         "mx": P(*mid)[0], "my": P(*mid)[1]})
    return {"chains": chains, "openings": openings,
            "page": {"width_pt": W_PT, "height_pt": H_PT},
            "scale": {"ft_per_paper_inch": 4.0}}


def words():
    # pymupdf 'words' tuples: (x0, y0, x1, y1, text, block, line, word) - the
    # block/line pair is what groups a label's words into one line
    def w(xf, yf, txt, h=4.0, blk=0, wn=0):
        x, y = P(xf, yf)
        return (x, y, x + 3.0 * len(txt), y + h, txt, blk, 0, wn)
    return [w(9, 8, "LIVING", 6.0, 1, 0), w(12, 8, "ROOM", 6.0, 1, 1), w(8, 13, "20'-0\"", 3.0, 2),
            w(24, 8, "BEDROOM", 6.0, 3, 0), w(28, 8, "2", 6.0, 3, 1), w(3, 4, "CL", 3.0, 4),
            # a wall note inside room A, taller than the label: must NOT win
            w(4, 9, "2x6", 7.0, 5, 0), w(6, 9, "STUDS", 7.0, 5, 1), w(9, 9, "@", 7.0, 5, 2), w(10, 9, "16\"", 7.0, 5, 3), w(12, 9, "O.C.", 7.0, 5, 4)]


# ---- room A --------------------------------------------------------------------
print("room A")
res = plan()
a = rooms.pick_room(res, words(), *P(10, 10), styles={0}, dpi=100)
check(a["ok"], "room A is enclosed: " + str(a.get("why")))
if a["ok"]:
    check(abs(a["area_sf"] - (300 - 16)) / 284 < 0.02,
          "area = 284 SF (300 less the 16 SF closet hole), got %.1f" % a["area_sf"])
    # the hole is the closet's interior plus its own wall ink (16 ft of 2 px
    # line at 100 dpi is about 1.3 SF), so 16 to 18.5
    check(a["n_holes"] == 1 and 16.0 <= a["holes_sf"] <= 18.5,
          "the closet is ONE hole of ~16 SF (interior + its wall ink), got %s / %s" % (a["n_holes"], a["holes_sf"]))
    check(abs(a["perim_lf"] - 70) / 70 < 0.02, "perimeter 70 LF, got %.1f" % a["perim_lf"])
    check(len(a["bridges_used"]) == 1 and abs(a["openings_lf"] - 3) < 0.2,
          "one 3 ft door on this boundary, got %d / %s" % (len(a["bridges_used"]), a["openings_lf"]))
    check(a["name"] == "LIVING ROOM", "named from the tallest label line: %r" % a["name"])
    check(4 <= len(a["polygon"]) <= 8, "polygon simplified to a few corners (%d)" % len(a["polygon"]))
    xs = [p[0] for p in a["polygon"]]
    check(abs(min(xs) - P(0, 0)[0]) < 3 and abs(max(xs) - P(20, 0)[0]) < 3,
          "polygon spans the room, not the doorway")

# ---- a label that wraps onto two lines ---------------------------------------------
print("wrapped label")
def words_wrapped():
    def w(xf, yf, txt, h=6.0, blk=0, wn=0):
        x, y = P(xf, yf)
        return (x, y, x + 3.0 * len(txt), y + h, txt, blk, 0, wn)
    # "BRIDAL DRESSING" over "ROOM", centred; a wall note taller and off to the side
    return [w(8, 7, "BRIDAL", 6.0, 1, 0), w(11, 7, "DRESSING", 6.0, 1, 1), w(9.5, 7.5, "ROOM", 6.0, 2, 0),
            w(2, 13, "2x6", 7.0, 5, 0), w(4, 13, "STUDS", 7.0, 5, 1), w(24, 8, "BEDROOM", 6.0, 3, 0), w(28, 8, "2", 6.0, 3, 1)]
wr = rooms.pick_room(res, words_wrapped(), *P(10, 10), styles={0}, dpi=100)
check(wr["ok"] and wr["name"] == "BRIDAL DRESSING ROOM",
      "two adjacent label lines join in reading order, got %r" % wr.get("name"))
wr2 = rooms.pick_room(res, words_wrapped(), *P(26, 4), styles={0}, dpi=100)
check(wr2["ok"] and wr2["name"] == "BEDROOM 2", "the other room is unaffected: %r" % wr2.get("name"))

# ---- room B --------------------------------------------------------------------
print("room B")
b = rooms.pick_room(res, words(), *P(26, 4), styles={0}, dpi=100)
check(b["ok"] and abs(b["area_sf"] - 180) / 180 < 0.02, "room B = 180 SF, got %s" % b.get("area_sf"))
check(b["ok"] and abs(b["perim_lf"] - 54) / 54 < 0.02, "room B perimeter 54 LF, got %s" % b.get("perim_lf"))
check(b["ok"] and len(b["bridges_used"]) == 1, "the same door is on room B's boundary")
check(b["ok"] and b["name"] == "BEDROOM 2", "room B named BEDROOM 2, got %r" % b.get("name"))

# ---- the bridge is what separates them --------------------------------------------
print("doorway")
nodoor = plan(with_door=False)
m = rooms.pick_room(nodoor, words(), *P(10, 10), styles={0}, dpi=100)
check(m["ok"] and abs(m["area_sf"] - (480 - 16)) / 464 < 0.02,
      "without the door record the two rooms MERGE through the gap: %s SF" % m.get("area_sf"))
check(m["ok"] and len(m["bridges_used"]) == 0, "...and no opening is reported")
# the bridge picked the strike jamb (down the wall), not the open leaf
w, br = rooms.sealed_walls(res, {0}, 100)
check(len(br) == 1 and abs(br[0]["x1"] - P(20, 9)[0]) < 1.0 and abs(br[0]["y1"] - P(20, 9)[1]) < 1.0,
      "the bridge runs hinge -> strike jamb along the wall, got %s" % ((br[0]["x1"], br[0]["y1"]) if br else None,))

# ---- role assignment: only visible styles are walls -------------------------------
print("styles")
allst = rooms.pick_room(res, words(), *P(10, 10), styles=None, dpi=100)
# the click at y=10 ft lands in the 20 x 12 ft upper part: 240 SF less the
# closet's ~17 SF = ~223 SF, not the whole room
check(allst["ok"] and abs(allst["area_sf"] - 223) / 223 < 0.03,
      "with the dimension string counted as a wall the room is cut in two: ~223 SF, got %s" % allst.get("area_sf"))
leak = rooms.pick_room(res, words(), *P(10, 10), styles={9}, dpi=100)
check(not leak["ok"] and "not enclosed" in leak["why"], "walls hidden: the fill leaks and says so")
online = rooms.pick_room(res, words(), *P(0, 7), styles={0}, dpi=100)
check(not online["ok"] and "landed on a line" in online["why"], "a click on a wall says so")

# ---- no scale --------------------------------------------------------------------
print("scale")
ns = plan()
ns["scale"] = {}
n = rooms.pick_room(ns, words(), *P(10, 10), styles={0}, dpi=100)
check(n["ok"] and n["area_sf"] is None and n["area_pt2"] > 0, "no scale: pt only, sf None")

# ---- doors drawn as dashed arcs: found from the CHAIN, not the units ----------------
print("doors from chains")
import math as _m


def door_chain(cid, hx, hy, r, sx=1, sy=1, leaf=True, n_arc=30):
    """A quarter arc about (hx, hy) toward (+sx, +sy), preceded by the leaf
    along the y axis - the shape the chain builder makes of a dashed swing."""
    pts = []
    if leaf:
        pts.append([hx, hy + sy * r])
    for i in range(n_arc + 1):
        a = _m.radians(90 * i / n_arc)
        pts.append([hx + sx * r * _m.sin(a), hy + sy * r * _m.cos(a)])
    L = sum(_m.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1))
    return {"chain_id": cid, "style_id": 5, "points": pts, "length_pt": L}


d = door_chain(900, 500.0, 300.0, 54.0)
found = rooms.doors_from_chains([d], 4.0)
check(len(found) == 1, "a leaf + quarter-arc chain is one door")
if found:
    o = found[0]
    check(abs(o["cx"] - 500) < 1 and abs(o["cy"] - 300) < 1 and abs(o["r"] - 54) < 2,
          "hinged at the arc's centre corner with r = 54 pt (3'-0\" at 1/4\"), got %s" % o)
    check(o["mx"] > 500 and o["my"] > 300, "the arc midpoint points into the swing")
    check(o["source"] == "chain" and o["chain_id"] == 900, "the record says where it came from")
check(len(rooms.doors_from_chains([door_chain(901, 500.0, 300.0, 54.0, leaf=False)], 4.0)) == 1,
      "a bare quarter arc is a door too")
check(len(rooms.doors_from_chains([door_chain(902, 500.0, 300.0, 54.0, sx=-1, sy=-1)], 4.0)) == 1
      and rooms.doors_from_chains([door_chain(902, 500.0, 300.0, 54.0, sx=-1, sy=-1)], 4.0)[0]["cx"] == 500.0,
      "any of the four corners can be the hinge")
box = {"chain_id": 903, "style_id": 5, "length_pt": 216.0,
       "points": [[0, 0], [54, 0], [54, 54], [0, 54], [0, 0]] + [[0, 0]] * 6}
check(rooms.doors_from_chains([box], 4.0) == [], "a square box (length 4r) is not a door")
check(rooms.doors_from_chains([door_chain(904, 500.0, 300.0, 12.0)], 4.0) == [],
      "12 pt at 1/4\" is an 8 in door: outside the 1.25-6 ft window")
check(rooms.doors_from_chains([door_chain(905, 500.0, 300.0, 54.0)], 4.0,
                              known=[{"cx": 505, "cy": 302, "r": 54}]) == [],
      "a hinge the unit pass already reported within r/4 is not reported twice")
# ...and it bridges: the synthetic plan with its door drawn as a chain instead of a record
plan_c = plan(with_door=False)
plan_c["chains"].append(door_chain(906, *P(20, 6), 3 * FT, sx=1, sy=1))   # swings into room B, down the wall
bc = rooms.pick_room(plan_c, words(), *P(10, 10), styles={0}, dpi=100)
check(bc["ok"] and abs(bc["area_sf"] - (300 - 16)) / 284 < 0.02 and len(bc["bridges_used"]) == 1,
      "a dashed-arc door closes room A exactly like a swing record: %s SF, %d door" % (bc.get("area_sf"), len(bc.get("bridges_used", []))))

# ---- a door known only by its LEAF (a pair / bi-fold: one arc per door in a
# chain no circle fits, but each leaf is its own 2-point stroke on the layer)
print("doors from leaves")
plan_l = plan(with_door=False)
# the leaf drawn open into room B from the hinge (20,6): 3 ft at 45 degrees
plan_l["chains"].append({"chain_id": 907, "style_id": 7, "layer": "A-Door", "length_pt": 3 * FT,
                         "points": [P(20, 6), P(20 + 3 * _m.cos(_m.radians(45)), 6 + 3 * _m.sin(_m.radians(45)))]})
bl = rooms.pick_room(plan_l, words(), *P(10, 10), styles={0}, dpi=100)
check(bl["ok"] and abs(bl["area_sf"] - (300 - 16)) / 284 < 0.02 and len(bl["bridges_used"]) == 1,
      "a leaf on the door layer closes room A: %s SF, %d door" % (bl.get("area_sf"), len(bl.get("bridges_used", []))))
w_l, br_l = rooms.sealed_walls(plan_l, {0}, 100)
check(len(br_l) == 1 and abs(br_l[0]["x1"] - P(20, 9)[0]) < 1.5 and abs(br_l[0]["y1"] - P(20, 9)[1]) < 1.5,
      "...bridged hinge -> strike along the wall, got %s" % ((br_l[0]["x1"], br_l[0]["y1"]) if br_l else None,))
plan_n = plan(with_door=False)
plan_n["chains"].append({"chain_id": 908, "style_id": 7, "layer": "", "length_pt": 3 * FT,
                         "points": [P(20, 6), P(22.1, 8.1)]})
w_n, br_n = rooms.sealed_walls(plan_n, {0}, 100)
check(len(br_n) == 0, "an unnamed 3 ft stroke is not a door (any short line would be)")
plan_f = plan(with_door=False)
plan_f["chains"].append({"chain_id": 909, "style_id": 7, "layer": "A-Door", "length_pt": 3 * FT,
                         "points": [P(10, 3), P(12.1, 5.1)]})   # floating in room A: both ends on no wall
w_f, br_f = rooms.sealed_walls(plan_f, {0}, 100)
check(len(br_f) == 0, "a door-layer stroke with neither end on a wall abstains")

# ---- MULTI-LEAF RUNS: several arcs in one chain (a pair, a bi-fold) ---------------
print("doors from a multi-arc chain")


def pair_chain(cid, hx, hy, r, layer="A-Door"):
    """Two 45-degree doors meeting in the middle, drawn as ONE chain: leaf 1
    (down from hinge 1), arc 1 sweeping to the meeting point, arc 2 back
    from the meeting point to hinge 2's swing, leaf 2. Hinge 1 at (hx, hy),
    hinge 2 at (hx + 2r, hy). No single circle fits it."""
    pts = [[hx, hy + r]]
    for i in range(25 + 1):                       # arc 1: 90 -> 45 degrees
        a = _m.radians(90 - 45 * i / 25)
        pts.append([hx + r * _m.cos(a), hy + r * _m.sin(a)])
    for i in range(25 + 1):                       # arc 2 about hinge 2: 135 -> 90
        a = _m.radians(135 - 45 * i / 25)
        pts.append([hx + 2 * r + r * _m.cos(a), hy + r * _m.sin(a)])
    pts.append([hx + 2 * r, hy])                  # leaf 2 back to hinge 2? no: leaf runs hinge -> arc end
    L = sum(_m.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1))
    return {"chain_id": cid, "style_id": 5, "points": pts, "length_pt": L, "layer": layer}


pc = pair_chain(950, 400.0, 300.0, 45.0)
_one = rooms._fit_door(pc["points"], pc["length_pt"], 22.5, 108.0)
check(_one is not None and _one[2] > 60,
      "the single-circle fit passes ONE circle through both 45-degree arcs (r ~78 pt, a 4'-4\" door "
      "hinged in mid-air) - which is why two split arcs take precedence over it: got %s" % (_one and round(_one[2], 1)))
fp = rooms.doors_from_chains([pc], 4.0)
check(len(fp) == 2, "a pair drawn as one chain is TWO doors, got %d" % len(fp))
if len(fp) == 2:
    hx = sorted(o["cx"] for o in fp)
    check(abs(hx[0] - 400) < 2 and abs(hx[1] - 490) < 2 and all(abs(o["r"] - 45) < 2 for o in fp),
          "hinged at the two jambs, 2r apart, each r = 45 pt: %s" % [(o["cx"], o["cy"], o["r"]) for o in fp])
    check(all(o["source"] == "chain-split" and o.get("a0") is not None for o in fp),
          "each carries its exact arc-end bearings (no jamb search)")
# unnamed sheet: an arc with a leaf beside it is still a door; a bare arc pair is too
fp_un = rooms.doors_from_chains([pair_chain(951, 400.0, 300.0, 45.0, layer="")], 4.0)
check(len(fp_un) == 2, "without a layer name the two arcs corroborate each other as a pair (%d)" % len(fp_un))
# a lone arc with no leaf and no layer name: not a door by geometry alone
lone = door_chain(952, 500.0, 300.0, 54.0, leaf=False)
lone["points"] = lone["points"] + [[560.0, 300.0], [560.0, 240.0]]     # a stub so the single fit fails
lone["length_pt"] += 120.0
lone["layer"] = ""
check(all(o["source"] != "chain-split" for o in rooms.doors_from_chains([lone], 4.0)),
      "the splitter does not invent a door from a bare arc on an unnamed chain")

# ---- CEILING HEIGHT written in the room --------------------------------------------
print("ceiling note")
def words_ceiling():
    def w(xf, yf, txt, h=4.0, blk=0, wn=0):
        x, y = P(xf, yf)
        return (x, y, x + 3.0 * len(txt), y + h, txt, blk, 0, wn)
    return [w(9, 8, "LIVING", 6.0, 1, 0), w(12, 8, "ROOM", 6.0, 1, 1),
            w(9, 10, "9'-118\"", 4.0, 2, 0), w(12, 10, "CEILING", 4.0, 2, 1),   # the stacked fraction as it arrives
            w(2, 13, "8\"", 4.0, 3, 0), w(3, 13, "DRYWALL", 4.0, 3, 1), w(6, 13, "ON", 4.0, 3, 2), w(7, 13, "CEILING", 4.0, 3, 3),
            w(24, 8, "BEDROOM", 6.0, 4, 0), w(28, 8, "2", 6.0, 4, 1), w(24, 10, "VAULTED", 4.0, 5, 0), w(27, 10, "CEILING", 4.0, 5, 1)]
ca = rooms.pick_room(res, words_ceiling(), *P(10, 10), styles={0}, dpi=100)
check(ca["ok"] and ca["name"] == "LIVING ROOM", "the ceiling line does not steal the room name: %r" % ca.get("name"))
check(ca["ok"] and ca.get("ceiling") and abs(ca["ceiling"]["ft"] - (9 + 1.125 / 12)) < 1e-3,
      "9'-1 1/8\" CEILING read as 9.094 ft (the half-size fraction glyphs unfolded): %s" % ca.get("ceiling"))
check(ca["ok"] and ca["ceiling"]["text"] == "9'-1 1/8\" CEILING", "...and shown unfolded: %r" % (ca.get("ceiling") or {}).get("text"))
cb = rooms.pick_room(res, words_ceiling(), *P(26, 4), styles={0}, dpi=100)
check(cb["ok"] and cb.get("ceiling") == {"ft": None, "text": "VAULTED CEILING"},
      "a vaulted note is reported with no height, never a guess: %s" % cb.get("ceiling"))
check(rooms.ceiling_note([{"text": "8\" DRYWALL ON CEILING"}]) is None, "inches-only on a ceiling line is not a height")
check(rooms.ceiling_note([{"text": "CLG. HT. 10'-0\""}]) == {"ft": 10.0, "text": "CLG. HT. 10'-0\""}, "CLG. HT. 10'-0\" reads 10 ft")
check(rooms.ceiling_note([{"text": "40'-0\" CEILING"}]) is None, "a 40 ft ceiling is refused as implausible (6-30 ft)")
check(rooms.ceiling_note([{"text": "2x6 STUDS"}]) is None, "no ceiling word: nothing")

# a narrow room stacks its label: W.I.C. / 9'-1 1/8" / CEILING on three lines
def words_stacked():
    def w(xf, yf, txt, h=3.0, blk=0, ln=0, wn=0):
        x, y = P(xf, yf)
        return (x, y, x + 3.0 * len(txt), y + h, txt, blk, ln, wn)
    return [w(9, 8, "W.I.C.", 3.0, 1, 0), w(9, 8.3, "9'-118\"", 3.0, 1, 1), w(9, 8.6, "CEILING", 3.0, 1, 2),
            # a dimension string elsewhere in the room, next in reading order but not stacked
            w(3, 12, "12'-10", 3.0, 2, 0, 0), w(6, 12, "1/4\"", 3.0, 2, 0, 1),
            w(24, 8, "GREAT", 6.0, 3, 0, 0), w(28, 8, "ROOM", 6.0, 3, 0, 1), w(24, 10, "VAULTED", 4.0, 4, 0, 0), w(27, 10, "CEILING", 4.0, 4, 0, 1),
            w(24, 11, "12'-10", 3.0, 5, 0, 0), w(27, 11, "1/4\"", 3.0, 5, 0, 1)]
cs = rooms.pick_room(res, words_stacked(), *P(10, 10), styles={0}, dpi=100)
check(cs["ok"] and cs["name"] == "W.I.C.", "W.I.C. is a room name (a room word with no three-letter run): %r" % cs.get("name"))
check(cs["ok"] and cs.get("ceiling") and abs(cs["ceiling"]["ft"] - 9.094) < 1e-3,
      "a height on the line directly above CEILING is the ceiling: %s" % cs.get("ceiling"))
cg = rooms.pick_room(res, words_stacked(), *P(26, 4), styles={0}, dpi=100)
check(cg["ok"] and cg.get("ceiling") == {"ft": None, "text": "VAULTED CEILING"},
      "a dimension merely next in reading order (not stacked on the label) is NOT a ceiling height: %s" % cg.get("ceiling"))

# ---- THE OBJECTS INSIDE A ROOM: what the flooring stops at ---------------------------
print("objects inside a room")


def plan_objects():
    """Room B gains: a TUB drawn the way drafters draw one - an open rim
    against the top wall (three sides, 2'-6" x 5'-0") plus an inner outline,
    on a plumbing layer; a VANITY as an open run of cabinet lines against the
    right wall (2 ft x 4 ft); a TOILET as a closed loop (1.5 x 2.3 ft); and a
    door swing arc from the party door (a cell that must NOT be an object).
    Room A gains a closed 2.5 x 5 loop with no layer name - a guess."""
    p = plan()
    ch = p["chains"]
    nid = 100
    def add(pts, layer, style=9):
        nonlocal nid
        c = chain(nid, pts, style); c["layer"] = layer; ch.append(c); nid += 1
    # tub rim: left side, bottom, right side (top is the wall), x 21..26, y 0..2.5
    add([P(21, 0), P(21, 2.5), P(26, 2.5), P(26, 0)], "A-Plumbing")
    add([P(21.4, 0.4), P(21.4, 2.1), P(25.6, 2.1), P(25.6, 0.4), P(21.4, 0.4)], "A-Plumbing")   # inner outline
    # vanity against the right wall: x 30..32, y 5..9 (open toward the wall)
    add([P(32, 5), P(30, 5), P(30, 9), P(32, 9)], "A-Cabinet")
    # toilet: closed loop
    add([P(28, 12), P(29.5, 12), P(29.5, 14.3), P(28, 14.3), P(28, 12)], "A-Plumbing")
    # a door swing quarter arc from the party door hinge (20,6), r 3, into room B
    import math as _mm
    arc = [P(20 + 3 * _mm.cos(_mm.radians(a)), 6 + 3 * _mm.sin(_mm.radians(a))) for a in range(0, 91, 10)]
    add(arc, "A-Door")
    # room A: an unnamed closed 2.5 x 5 loop
    add([P(8, 1), P(13, 1), P(13, 3.5), P(8, 3.5), P(8, 1)], "")
    return p


po = plan_objects()
ob = rooms.pick_room(po, words(), *P(26, 8), styles={0}, dpi=100)
check(ob["ok"], "room B still fills with the fixtures drawn in it: " + str(ob.get("why")))
objs = ob.get("objects", [])
kinds = sorted(o["kind"] for o in objs)
check(kinds == ["cabinet", "toilet", "tub"], "three objects, named tub / cabinet / toilet from layer + size: %s" % [(o["kind"], o["area_sf"], o["w_ft"], o["h_ft"]) for o in objs])
tub = next((o for o in objs if o["kind"] == "tub"), None)
check(tub is not None and tub["deduct"] is True, "the tub comes out of the flooring by default")
check(tub is not None and 12.3 <= tub["area_sf"] <= 13.0, "tub area: the 12.5 SF drawn box to the OUTER edge of its lines (2 px at 100 dpi), rim strips and inner outline one footprint - not the 9-10 SF of free pixels inside them: %s" % (tub and tub["area_sf"]))
van = next((o for o in objs if o["kind"] == "cabinet"), None)
check(van is not None and van["deduct"] is False and 7.6 <= van["area_sf"] <= 8.8, "the vanity is listed at ~8 SF (the 2 x 4 ft drawn box to the outer edge of its lines) and KEPT (cabinets stay in by default): %s" % (van and van["area_sf"]))
toi = next((o for o in objs if o["kind"] == "toilet"), None)
check(toi is not None and toi["deduct"] is False, "the toilet is listed and kept")
check(not any(o["kind"] == "other" and abs(o["w_ft"] - 3) < 0.6 and abs(o["h_ft"] - 3) < 0.6 for o in objs),
      "the door swing's quarter-disc is not an object")
check(all(len(o["polygon"]) >= 3 for o in objs), "every object carries its polygon for the sheet")
oa = rooms.pick_room(po, words(), *P(10, 10), styles={0}, dpi=100)
ga = [o for o in oa.get("objects", []) if o["guessed"]]
check(len(ga) == 1 and ga[0]["kind"] == "tub" and ga[0]["deduct"] is False,
      "an unnamed 2.5 x 5 loop is a GUESSED tub: listed, never deducted on its own: %s" % [(o["kind"], o["guessed"], o["deduct"]) for o in oa.get("objects", [])])
check(rooms.pick_room(po, words(), *P(26, 8), styles=None, dpi=100).get("objects") == [],
      "with every line a wall there are no objects to list")
check(rooms._obj_kind("A-Plumbing", 2.19, 4.7) == ("tub", False) and rooms._obj_kind("A-Plumbing", 1.4, 1.7) == ("sink", False)
      and rooms._obj_kind("P-FIXT", 3.0, 3.2) == ("shower", False) and rooms._obj_kind("I-Furn", 6, 5) == ("furniture", False)
      and rooms._obj_kind("", 1.5, 1.5) == ("shape", True), "kinds by layer family and size")

# ---- cache -----------------------------------------------------------------------
print("cache")
rooms._CACHE.clear()
k1 = rooms._cache_key("j", 0, 100, {0}, 1.0)
rooms.pick_room(res, [], *P(10, 10), styles={0}, dpi=100, cache_key=k1)
rooms.pick_room(res, [], *P(26, 4), styles={0}, dpi=100, cache_key=k1)
check(len(rooms._CACHE) == 1, "two clicks on one sheet rasterise once")
check(rooms._cache_key("j", 0, 100, {0, 1}, 1.0) != k1 and rooms._cache_key("j", 0, 100, {0}, 2.0) != k1,
      "a different style set or a re-detect is a different key")

print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
