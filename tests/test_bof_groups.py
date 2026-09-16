"""WALLS GROUPED BY BOTTOM OF FOOTING (2026-09-16; Wave 2 item 3; Beaumont
colours every wall by the elevation its footing bottoms at). Two readings:
sheet-wide (every plan line joins the nearest callout's group within
reach; the footing family is the one the callouts sit on) and on the drawn
outline (each edge sampled every 2 ft joins the nearest callout, so the
groups add up to the perimeter). Synthetic sheet, pt == ft.

    python tests/test_bof_groups.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from takeoff_engine.footing_callouts import (GROUP_COLOURS, group_walls_by_bof,  # noqa: E402
                                             split_outline_by_bof)

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


def ch(cid, sid, pts, klass="dash1", label=None):
    L = sum(((pts[i + 1][0] - pts[i][0]) ** 2 + (pts[i + 1][1] - pts[i][1]) ** 2) ** 0.5 for i in range(len(pts) - 1))
    return {"chain_id": cid, "style_id": sid, "class": klass, "width": 0.8, "length_pt": L, "points": pts, "label": label}


# a 100 x 60 building: footing outline (family 11, dashed) as four wall runs,
# wall faces (family 3, solid) beside them, an annotation leader, and a far line
chains = [
    ch(1, 11, [[0, 0], [100, 0]]), ch(2, 11, [[100, 0], [100, 60]]), ch(3, 11, [[100, 60], [0, 60]]), ch(4, 11, [[0, 60], [0, 0]]),
    ch(5, 3, [[0, 1], [100, 1]], "solid"), ch(6, 3, [[100, 1], [100, 59]], "solid"),
    ch(7, 9, [[10, 30], [40, 30]], "annotation"),
    ch(8, 11, [[300, 300], [340, 300]]),                       # nothing near it
]
# callouts: the north and east walls bottom at 86, the south and west at 83
calls = [{"mark": "CF2.0", "x": 50, "y": -4, "bof_ft": 86.0}, {"mark": "CF2.0", "x": 104, "y": 30, "bof_ft": 86.0},
         {"mark": "CF3.0", "x": 50, "y": 64, "bof_ft": 83.0}, {"mark": "CF3.0", "x": -4, "y": 30, "bof_ft": 83.0},
         {"mark": "F5.5", "x": 50, "y": 30, "bof_ft": None}]  # a pad with no elevation: not a group
sched = {"rows": {"CF2.0": {"width_ft": 2.0, "from": "schedule"}}}
g = group_walls_by_bof(calls, chains, 1.0, sched, reach_ft=10)
by = {x["bof_ft"]: x for x in g["groups"]}
check(set(by) == {83.0, 86.0}, f"two groups, one per bottom-of-footing elevation (got {sorted(by)})")
check(g["footing_style"]["style_id"] == 11 and g["footing_style"]["share"] == 1.0, f"the footing family is the one every callout sits on (got {g['footing_style']})")
check(by[86.0]["lf_ft"] == 160.0 and by[83.0]["lf_ft"] == 160.0, f"the footing outline's length per group: north+east 160, south+west 160 (got {by[86.0]['lf_ft']}, {by[83.0]['lf_ft']})")
check(sorted(by[86.0]["chain_ids"]) == [1, 2] and sorted(by[83.0]["chain_ids"]) == [3, 4], "each group knows its footing chains")
check(by[86.0]["families"].get("3") == 158.0 and "3" not in by[83.0]["families"], "other families are summed per group too (the wall faces sit with the north/east walls)")
check(by[86.0]["marks"] == {"CF2.0": 2} and by[86.0]["width_ft"] == 2.0 and by[86.0]["width_from"] == "schedule", "marks and the schedule width per group")
check(by[83.0]["width_ft"] == 3.0 and by[83.0]["width_from"] == "mark digits", "a group whose mark has no schedule row takes the digits, and says so")
check(g["unassigned_ft"] == 40.0, f"the far line is unassigned, said in feet (got {g['unassigned_ft']})")
check(by[83.0]["colour"] == GROUP_COLOURS[0] and by[86.0]["colour"] == GROUP_COLOURS[1], "colours by elevation order")
check("both long sides" in g["outline_note"], "the outline-length caveat is on the result")
check(group_walls_by_bof([], chains, 1.0)["groups"] == [] and group_walls_by_bof(calls, [], 1.0)["groups"] == [], "no callouts or no chains: nothing invented")
# a footing line that runs past two elevations (an L round a corner, 100 ft along the top then
# 60 ft down the side) with the WALL STEP printed 20 ft down the side: 120 ft to the first
# callout's elevation, 40 ft to the second - a whole-chain assignment would give all 160 to one
ell = [ch(9, 11, [[0, 0], [100, 0], [100, 60]])]
two = [{"mark": "CF2.0", "x": 50, "y": -4, "bof_ft": 86.0}, {"mark": "CF3.0", "x": 104, "y": 45, "bof_ft": 83.0}]
g2 = group_walls_by_bof(two, ell, 1.0, reach_ft=10, steps=[{"x": 106, "y": 20}])
by2 = {x["bof_ft"]: x for x in g2["groups"]}
check(by2[86.0]["lf_ft"] == 120.0 and by2[83.0]["lf_ft"] == 40.0, f"a chain past two callouts is split at the printed WALL STEP (got {by2[86.0]['lf_ft']}, {by2[83.0]['lf_ft']})")
check(by2[86.0]["chain_ids"] == [9] and by2[83.0]["chain_ids"] == [9] and by2[86.0]["chain_major"] == [9] and by2[83.0]["chain_major"] == [], "both groups list the chain for hover; only the group holding its larger piece paints it")
g3 = group_walls_by_bof(two, ell, 1.0, reach_ft=10)
by3 = {x["bof_ft"]: x for x in g3["groups"]}
check(abs(by3[86.0]["lf_ft"] - 95.6) < 0.15 and abs(by3[83.0]["lf_ft"] - 64.4) < 0.15, f"no step printed: every point goes to the nearer callout by true distance - the boundary is where (x-50)^2+16 = (104-x)^2+45^2, x = 95.6 (got {by3[86.0]['lf_ft']}, {by3[83.0]['lf_ft']})")
# a callout right beside the wall beats one across the room, whatever their positions along it
room = [ch(10, 11, [[0, 0], [100, 0]])]
across = [{"mark": "CF2.0", "x": 50, "y": 4, "bof_ft": 85.167}, {"mark": "CF2.0", "x": 50, "y": 60, "bof_ft": 84.167},
          {"mark": "F5.5", "x": 20, "y": 70, "bof_ft": 84.167}]
g4 = group_walls_by_bof(across, room, 1.0, reach_ft=100)
check({x["bof_ft"]: x["lf_ft"] for x in g4["groups"] if x["lf_ft"]} == {85.167: 100.0}, f"the callout 4 ft off the wall owns all of it although two 84'-2 callouts 60-70 ft away are within reach (got {[(x['bof_ft'], x['lf_ft']) for x in g4['groups']]})")
one = group_walls_by_bof(two[:1], ell, 1.0, reach_ft=10)
check({x["bof_ft"]: x["lf_ft"] for x in one["groups"]} == {86.0: 160.0}, "one callout in reach claims the whole line, round the corner included")

# ---- the drawn outline, split by group ----------------------------------------------
sp = split_outline_by_bof([[0, 0], [100, 0], [100, 60], [0, 60]], calls, 1.0, reach_ft=15)
lf = {x["bof_ft"]: x["lf_ft"] for x in sp["groups"]}
check(sp["perimeter_ft"] == 320.0 and abs(lf.get(86.0, 0) + lf.get(83.0, 0) + sp["unassigned_ft"] - 320.0) < 0.01, f"the groups plus unassigned add up to the perimeter (got {lf}, unassigned {sp['unassigned_ft']})")
check(lf.get(86.0) == 160.0 and lf.get(83.0) == 160.0, f"a callout beside an edge claims the whole wall: north+east to 86, south+west to 83 (got {lf})")
check(len(sp["edges"]) == 4 and all(e["bof_ft"] is not None for e in sp["edges"]), "every edge reports its group, for the canvas")
sp2 = split_outline_by_bof([[0, 0], [100, 0], [100, 60], [0, 60]], calls, 1.0, reach_ft=3)
check(sp2["unassigned_ft"] == 320.0 and sp2["groups"] == [], "with a tiny reach nothing is claimed - the perimeter stays unassigned rather than guessed")
check(split_outline_by_bof([], calls, 1.0)["groups"] == [], "no outline: nothing")
# one long wall with two callouts at different elevations: split halfway, or at the WALL STEP printed between them
two = [{"mark": "CF2.0", "x": 20, "y": -4, "bof_ft": 86.0}, {"mark": "CF3.0", "x": 80, "y": -4, "bof_ft": 83.0}]
sp3 = split_outline_by_bof([[0, 0], [100, 0]], two, 1.0, reach_ft=15)
lf3 = {x["bof_ft"]: x["lf_ft"] for x in sp3["groups"]}
check(lf3 == {83.0: 50.0, 86.0: 50.0}, f"two callouts on one wall, no step printed: split halfway between them (got {lf3})")
sp4 = split_outline_by_bof([[0, 0], [100, 0]], two, 1.0, reach_ft=15, steps=[{"x": 70, "y": 6}])
lf4 = {x["bof_ft"]: x["lf_ft"] for x in sp4["groups"]}
check(lf4 == {83.0: 30.0, 86.0: 70.0}, f"a WALL STEP printed between them moves the split to the step (got {lf4})")
sp5 = split_outline_by_bof([[0, 0], [100, 0]], two, 1.0, reach_ft=15, steps=[{"x": 70, "y": 40}])
check({x["bof_ft"]: x["lf_ft"] for x in sp5["groups"]} == {83.0: 50.0, 86.0: 50.0}, "a step word far from the wall does not move the split")
sp6 = split_outline_by_bof([[0, 0], [100, 0]], [dict(two[0]), dict(two[1], bof_ft=86.0)], 1.0, reach_ft=15, steps=[{"x": 70, "y": 6}])
check({x["bof_ft"]: x["lf_ft"] for x in sp6["groups"]} == {86.0: 100.0}, "two callouts at the same elevation: one group, the step is not a boundary")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all bof-group checks passed")
