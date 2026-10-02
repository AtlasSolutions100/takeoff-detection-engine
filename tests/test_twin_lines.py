"""TWIN LINES COUNTED ONCE (2026-09-17; Wave 2 item 11). A foundation plan
draws each wall as its two faces and each footing as its two edges; both
clients bill one run per wall. takeoff_engine/twin_lines.py pairs the lines
(a wall thickness, or the footing schedule's widths), keeps the outside face
by flooding the footprint, drops a line drawn inside a wall, and leaves the
as-drawn length alone. footing_callouts.group_walls_by_bof takes the result
and counts one run per footing in each bottom-of-footing group.

Synthetic plans with known answers, then the real Barn S1-1.3 sheet when the
local store has it (skipped otherwise - client material never ships):
the client's own 19 length measurements total 704.4 ft.

    python tests/test_twin_lines.py     (fails on the commit before it)
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from takeoff_engine.twin_lines import (family_kind, family_twins,  # noqa: E402
                                       twin_shares)
from takeoff_engine.footing_callouts import group_walls_by_bof  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


FPP = 0.1                      # ft per pt


def pt(ft):
    return ft / FPP


def rect(x0, y0, x1, y1):
    return [[pt(x0), pt(y0)], [pt(x1), pt(y0)], [pt(x1), pt(y1)], [pt(x0), pt(y1)], [pt(x0), pt(y0)]]


def fam(*polys):
    return [{"chain_id": i + 1, "points": p} for i, p in enumerate(polys)]


def near(a, b, tol, msg):
    check(abs(a - b) <= tol, f"{msg} (got {a}, expect {b} +/- {tol})")


T = 0.75
outer, inner = rect(0, 0, 40, 30), rect(T, T, 40 - T, 30 - T)

# 1. a building's walls, two faces each: the outside face is the run
r = twin_shares(fam(outer, inner), FPP)
near(r["drawn_ft"], 274.0, 0.5, "as drawn: both faces")
near(r["counted_ft"], 140.0, 0.5, "counted once: the outside face, 2 x (40 + 30)")
check(r["chains"][1]["counted_pt"] > 0 and r["chains"][2]["counted_pt"] == 0,
      "the OUTER face is the one kept, the inner one is left out")
check(list(r["spacing_ft"]) == ["0.75"], f"the pair sits at the wall thickness ({r['spacing_ft']})")
check(r["chains"][2]["skip"] and r["chains"][2]["skip"][0][0] <= 1.0,
      "the face left out carries its skipped arc ranges for the canvas")

# 2. an interior wall, and the inside face broken at the junction
in_left = [[pt(20 - T / 2), pt(T)], [pt(T), pt(T)], [pt(T), pt(30 - T)], [pt(20 - T / 2), pt(30 - T)]]
in_right = [[pt(20 + T / 2), pt(30 - T)], [pt(40 - T), pt(30 - T)], [pt(40 - T), pt(T)], [pt(20 + T / 2), pt(T)]]
wa = [[pt(20 - T / 2), pt(T)], [pt(20 - T / 2), pt(30 - T)]]
wb = [[pt(20 + T / 2), pt(T)], [pt(20 + T / 2), pt(30 - T)]]
r = twin_shares(fam(outer, in_left, in_right, wa, wb), FPP)
near(r["counted_ft"], 168.5, 1.5, "outside faces + the interior wall once")
check(r["chains"][1]["counted_pt"] * FPP > 139 and r["chains"][2]["counted_pt"] == 0 and r["chains"][3]["counted_pt"] == 0,
      "a long outside face pairs with every piece of the broken inside face (mutual per sample, not per chain)")
near((r["chains"][4]["counted_pt"] + r["chains"][5]["counted_pt"]) * FPP, 28.5, 1.5,
     "an interior wall keeps ONE face, not zero and not two")

# 3. footing edges pair at the schedule's widths, not at a wall thickness
fo, fi = rect(-1, -1, 41, 31), rect(1, 1, 39, 29)
r = twin_shares(fam(fo, fi), FPP, widths_ft=[2.0, 3.0])
near(r["counted_ft"], 148.0, 0.5, "a 2 ft footing drawn as two edges: one run, its outer edge")
check("schedule widths 2, 3" in r["rule"], f"the rule says what paired them ({r['rule']})")
r = twin_shares(fam(fo, fi), FPP, widths_ft=[3.0, 4.0])
near(r["counted_ft"], r["drawn_ft"], 0.5, "edges 2 ft apart when the schedule has no 2 ft footing: nothing pairs")
ft = family_twins(fam(fo, fi), FPP, schedule_widths=[2.0, 3.0])
check(ft["kind"] == "footing" and abs(ft["counted_ft"] - 148.0) <= 0.5,
      f"family_twins reads the spacing and picks the footing rule ({ft['kind']}, {ft['counted_ft']})")
check(family_kind({"0.75": 200.0, "2": 5.0}) == "wall" and family_kind({"2": 200.0, "0.75": 5.0}) == "footing",
      "the kind is decided by where most of the partnered footage sits")

# 4. a 3 ft door in both faces: the flood still finds the outside
o_gap = [[pt(10), 0], [0, 0], [0, pt(30)], [pt(40), pt(30)], [pt(40), 0], [pt(13), 0]]
i_gap = [[pt(10), pt(T)], [pt(T), pt(T)], [pt(T), pt(30 - T)], [pt(40 - T), pt(30 - T)], [pt(40 - T), pt(T)], [pt(13), pt(T)]]
r = twin_shares(fam(o_gap, i_gap), FPP)
near(r["counted_ft"], 137.0, 0.5, "a door gap closes for the flood; the outside face is still the one kept")

# 5. what must NOT pair
r = twin_shares(fam([[0, 0], [pt(20), 0]], [[0, pt(0.25)], [pt(20), pt(0.25)]]), FPP)
check(r["paired_ft"] == 0 and abs(r["counted_ft"] - 40.0) < 0.5, "two lines 3 in apart are not a wall: both count")
r = twin_shares(fam([[0, 0], [pt(20), 0]], [[0, pt(0.75)], [pt(20), pt(0)], ]), FPP)
check(r["counted_ft"] >= r["drawn_ft"] - 12.0, "a line crossing at an angle is not a twin")
r = twin_shares(fam([[0, 0], [pt(20), 0]]), FPP)
check(r["counted_ft"] == 20.0 and r["refused"] is None, "a lone line counts in full")
r = twin_shares([], FPP)
check(r["counted_ft"] == 0 and r["chains"] == {}, "nothing in, nothing out")

# 6. a free-standing wall: one face, the lower chain id's
r = twin_shares([{"chain_id": 7, "points": [[0, 0], [pt(20), 0]]},
                 {"chain_id": 3, "points": [[0, pt(0.75)], [pt(20), pt(0.75)]]}], FPP)
check(abs(r["counted_ft"] - 20.0) < 0.5 and r["chains"][3]["counted_pt"] > 0 and r["chains"][7]["counted_pt"] == 0,
      "a wall with outside on both sides keeps one face, deterministically")

# 7. a ledge line inside the wall (Ellis's "SPAN DECK PANEL 4\" LEDGE"): three lines, one run
ledge = rect(0.33, 0.33, 40 - 0.33, 30 - 0.33)
r = twin_shares(fam(outer, inner, ledge), FPP)
near(r["counted_ft"], 140.0, 0.5, "a line drawn inside a wall is never counted, and the wall is still one run")
check(r["middle_ft"] > 130, f"...and it is reported as the middle line it is ({r['middle_ft']} ft)")

# 8. two separate walls 1.2 ft apart: four lines, two runs
w1a, w1b = [[0, 0], [pt(30), 0]], [[0, pt(0.75)], [pt(30), pt(0.75)]]
w2a, w2b = [[0, pt(1.95)], [pt(30), pt(1.95)]], [[0, pt(2.7)], [pt(30), pt(2.7)]]
r = twin_shares(fam(w1a, w1b, w2a, w2b), FPP)
near(r["counted_ft"], 60.0, 0.5, "two walls close together are two walls, each counted once")

# 9. the crowd guard
many = [[[0, pt(i)], [pt(5000), pt(i)]] for i in range(3)]
r = twin_shares(fam(*many), FPP, max_samples=1000)
check(r["refused"] and r["counted_ft"] == r["drawn_ft"], "over the sample cap: refused with a reason, counted as drawn")

# 10. the footing groups count one run when the twins are known
calls = [{"x": pt(20), "y": pt(-1.5), "bof_ft": 86.0, "mark": "CF2.0"}]
chains = [{"chain_id": 1, "class": "dash1", "style_id": 5, "length_pt": 1480, "points": fo},
          {"chain_id": 2, "class": "dash1", "style_id": 5, "length_pt": 1320, "points": fi}]
g0 = group_walls_by_bof(calls, chains, FPP, reach_ft=40.0)
tw = twin_shares(chains, FPP, widths_ft=[2.0])
skip = {5: {cid: v["skip"] for cid, v in tw["chains"].items()}}
g1 = group_walls_by_bof(calls, chains, FPP, reach_ft=40.0, skip_by_style=skip)
check(g0["once"] is False and g1["once"] is True, "the groups say whether they count once")
near(g0["groups"][0]["lf_ft"], 280.0, 1.0, "without the twins a group is its drawn footage")
near(g1["groups"][0]["lf_ft"], 148.0, 1.0, "with them it is one run per footing")
near(g1["groups"][0]["lf_drawn_ft"], 280.0, 1.0, "and the drawn footage rides beside it")
check(g1["groups"][0]["families"] == g0["groups"][0]["families"], "the per-family breakdown stays as drawn")

# ---- the real sheet ---------------------------------------------------------------------------------
# The fixture lives in the repo (tests/fixtures/barn-s1-1.3: the dashed footing
# family of the S1-1.3 detection, slimmed, plus its footing schedule). It used
# to be read from %TEMP% and the check printed "skip" and PASSED whenever a temp
# clean-up had taken it - a gate that vanished silently (audit 2026-09-26, D37).
# A missing fixture is now a FAILURE, and the family is picked by its pen and
# signature, not by a style id that renumbers between engine versions.
FIX = ROOT / "tests" / "fixtures" / "barn-s1-1.3"
rf = FIX / "p14_footing_family.json"
sf = FIX / "footing_schedule.json"
check(rf.exists() and sf.exists(),
      f"Barn S1-1.3 fixture present at {FIX} (a missing fixture fails, it does not skip)")
if rf.exists() and sf.exists():
    res = json.loads(rf.read_text(encoding="utf-8"))
    fpp = res["scale"]["ft_per_paper_inch"] / 72.0
    sched = json.loads(sf.read_text(encoding="utf-8"))
    widths = sorted({float(v["width_ft"]) for v in sched["rows"].values() if v.get("width_ft")})
    # the footing family: the dashed black 0.8 pt style with the d6.8 g3.3
    # signature (66 chains); its d6.7 g47.3 sibling is the sparse read of the
    # same pen and must not be mixed in
    fam = [s for s in res["styles"]
           if s["class"] == "dash1" and s["signature"].startswith("d6.8 ")]
    check(len(fam) == 1, f"Barn S1-1.3: exactly one d6.8 dashed footing style in the fixture ({len(fam)})")
    sid = fam[0]["style_id"] if fam else None
    foot = [c for c in res["chains"] if c.get("style_id") == sid and c.get("class") != "annotation"]
    check(len(foot) == 66, f"Barn S1-1.3: the footing family holds 66 chains ({len(foot)})")
    r = family_twins(foot, fpp, schedule_widths=widths)
    print(f"   Barn S1-1.3 footing family: drawn {r['drawn_ft']} ft, one run {r['counted_ft']} ft; "
          f"the client's own measurements 704.4 ft")
    check(r["kind"] == "footing", "Barn S1-1.3: the dashed family is read as footing edges")
    check(abs(r["drawn_ft"] - 1055.5) < 5, "Barn S1-1.3: as drawn is 1,055 ft (+50% over the client's 704.4)")
    check(abs(r["counted_ft"] / 704.4 - 1) < 0.12, "Barn S1-1.3: one run is within 12% of the client's 704.4 ft")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all twin-line checks passed")
