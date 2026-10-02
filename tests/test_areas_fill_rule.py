"""AREAS: a filled path is measured by the PDF fill rule over ALL its subpaths
(takeoff_engine/areas.py `_painted`).

Defect it pins (2026-10-02, UI verify BLOCKER): Loves Lane C4.0 p0's building
fill is six paths of TWO triangles each (a CAD SOLID quad split on its
diagonal, opposite windings, nonzero rule). page_areas kept the larger
triangle and dropped the other - 11,625 SF reported for a 23,248 SF building,
on every such fill in the corpus (v1.8.5 too).

Synthetic paths, every number known by construction (pt2, no scale):
  * two-triangle quad, opposite windings      -> the whole quad
  * two-triangle quad, same winding           -> the whole quad
  * square with a square hole: even-odd       -> minus the hole
        nonzero, hole wound the other way     -> minus the hole
        nonzero, hole wound the SAME way      -> the full square (a renderer
                                                  paints it: winding 2)
  * two overlapping squares (they CROSS):
        nonzero -> their union, counted once; even-odd -> overlap cancels
  * a bow-tie (one self-crossing ring)        -> both lobes, not their
                                                  cancelled shoelace
  * twin identical rings: nonzero -> once; even-odd -> nothing painted
  * the cost guard: past the caps the row carries NO number and says why
And, when the sheet is on this machine, the real C4.0 row (skipped, and said
so, when it is not).

Run:  python tests/test_areas_fill_rule.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402

from takeoff_engine import areas as A  # noqa: E402

FAILS = []


def check(ok, msg):
    print(("ok   " if ok else "FAIL ") + msg)
    if not ok:
        FAILS.append(msg)


def page_with(rings, even_odd=False, fill=(0.5, 0.5, 0.5)):
    """One page, ONE filled path made of the given closed subpaths."""
    doc = pymupdf.open()
    pg = doc.new_page(width=800, height=800)
    sh = pg.new_shape()
    for r in rings:
        sh.draw_polyline([pymupdf.Point(x, y) for x, y in r] + [pymupdf.Point(*r[0])])
    sh.finish(fill=fill, color=None, even_odd=even_odd, closePath=False)
    sh.commit()
    return doc, pg


def measure(rings, even_odd=False):
    doc, pg = page_with(rings, even_odd)
    d = pg.get_drawings()
    res = A.page_areas(pg, None, None)
    return res, d


def area_of(rings, even_odd=False):
    res, _ = measure(rings, even_odd)
    regs = res["regions"]
    return regs[0]["area_pt2"] if regs else 0.0


def near(a, b, tol=0.5):
    return a is not None and abs(a - b) <= tol


def sq(x, y, s, ccw=False):
    pts = [(x, y), (x + s, y), (x + s, y + s), (x, y + s)]
    return pts[::-1] if ccw else pts


# ---- 1. the C4.0 shape: a quad as two triangles -----------------------------------------
# quad 100 x 50 at (100,100); split on the diagonal (100,100)-(200,150)
t1 = [(200, 100), (100, 100), (200, 150)]          # one winding
t2 = [(100, 100), (200, 150), (100, 150)]          # the other
res, d = measure([t1, t2])
check(len(d) == 1 and len(d[0]["items"]) >= 6,
      f"synthetic C4.0 path is ONE path of two subpaths ({len(d)} path(s))")
check(d[0].get("even_odd") is False, "it is drawn with the nonzero rule")
check(near(res["regions"][0]["area_pt2"], 5000.0),
      f"two-triangle quad, opposite windings = the whole quad 5000 pt2 "
      f"(got {res['regions'][0]['area_pt2']}; the old code gave 2500)")
check(res["groups"][0]["area_pt2"] == res["regions"][0]["area_pt2"],
      "the group row carries the same full area")
xs = [p[0] for p in res["regions"][0]["points"]]
ys = [p[1] for p in res["regions"][0]["points"]]
check(abs((max(xs) - min(xs)) * (max(ys) - min(ys)) - 5000) < 1,
      "the outline drawn is the quad, not one triangle")
t2_same = [(100, 100), (100, 150), (200, 150)]
check(near(area_of([[(100, 100), (200, 100), (200, 150)], t2_same]), 5000.0),
      "two-triangle quad, same winding = 5000 pt2")

# ---- 2. holes: even-odd vs nonzero --------------------------------------------------
outer = sq(100, 100, 100)
hole_rev = sq(130, 130, 40, ccw=True)
hole_same = sq(130, 130, 40)
check(near(area_of([outer, hole_same], even_odd=True), 8400.0),
      "even-odd square with a hole = 10000 - 1600 = 8400")
check(near(area_of([outer, hole_rev], even_odd=False), 8400.0),
      "nonzero, hole wound the other way = 8400")
check(near(area_of([outer, hole_same], even_odd=False), 10000.0),
      "nonzero, inner ring wound the SAME way = 10000 (winding 2 is painted)")

# ---- 3. overlapping (crossing) subpaths ---------------------------------------------
a_sq, b_sq = sq(100, 100, 100), sq(150, 150, 100)
check(near(area_of([a_sq, b_sq], even_odd=False), 17500.0),
      "nonzero, two crossing squares = their union 17500 (overlap counted once)")
check(near(area_of([a_sq, b_sq], even_odd=True), 15000.0),
      "even-odd, two crossing squares = 20000 - 2 x 2500 = 15000")

# ---- 4. a self-crossing ring (bow-tie) ----------------------------------------------
bow = [(100, 100), (200, 200), (200, 100), (100, 200)]
check(A._shoelace(bow) < 1.0, "the bow-tie's shoelace cancels to ~0 (the trap)")
check(near(area_of([bow]), 5000.0), "bow-tie paints both lobes = 5000")

# ---- 5. twin identical rings ---------------------------------------------------------
twin = sq(100, 100, 100)
check(near(area_of([twin, list(twin)], even_odd=False), 10000.0),
      "nonzero twin rings = 10000, counted once")
res_eo, _ = measure([twin, list(twin)], even_odd=True)
check(not res_eo["regions"], "even-odd twin rings paint nothing: no region")

# ---- 6. degenerate subpaths ----------------------------------------------------------
line = [(100, 300), (300, 300), (100, 300)]
check(near(area_of([a_sq, line, [(5, 5), (5, 5), (5, 5)]]), 10000.0),
      "a zero-area and a collapsed subpath change nothing")

# ---- 7. the cost guard ---------------------------------------------------------------
many = [sq(50 + 30 * i, 50, 20) for i in range(6)]          # six disjoint squares
check(near(area_of(many), 2400.0), "six disjoint squares = 2400 (nest method)")
area, geom, how = A._painted(many, False)
check(how == "nest" and near(area, 2400.0), f"disjoint rings take the no-noding path ({how})")
area, geom, how = A._painted([a_sq, b_sq], False)
check(how == "node" and near(area, 17500.0), f"crossing rings take the noding path ({how})")
old = (A.NEST_MAX_RINGS, A.NODE_MAX_EDGES, A.NEST_MAX_PAIRS)
try:
    A.NEST_MAX_RINGS = 3
    res, _ = measure(many)
    r0 = res["regions"][0] if res["regions"] else {}
    g0 = res["groups"][0] if res["groups"] else {}
    check(r0.get("area_pt2") is None and "too complex" in (r0.get("unmeasured") or ""),
          f"past the ring cap: NO number and the reason ({r0.get('unmeasured')!r})")
    check(g0.get("area_pt2") is None and g0.get("area_sf") is None
          and g0.get("n_unmeasured") == 1 and g0.get("unmeasured"),
          "the group row has no number either, and says why")
    A.NEST_MAX_RINGS = old[0]
    A.NODE_MAX_EDGES = 6
    area, geom, how = A._painted([a_sq, b_sq], False)
    check(area is None and "crossing" in how,
          f"crossing rings past the edge cap are refused ({how!r})")
    A.NODE_MAX_EDGES = old[1]
    old_hits = A.NODE_MAX_HITS
    A.NODE_MAX_HITS = 3
    area, geom, how = A._painted([a_sq, b_sq], False)
    check(area is None and "edge contacts" in how,
          f"crossing rings past the contact cap are refused ({how!r})")
    A.NODE_MAX_HITS = old_hits
    A.NEST_MAX_PAIRS = 0
    area, geom, how = A._painted([sq(100, 100, 100), sq(120, 120, 10)], False)
    check(area is None and "pairs" in how, f"past the pair cap: refused ({how!r})")
finally:
    A.NEST_MAX_RINGS, A.NODE_MAX_EDGES, A.NEST_MAX_PAIRS = old

# ---- 8. the real sheet, when it is here ----------------------------------------------
_REL = Path("TakeoffPro AI") / "Plans" / "C4.0-SITE-GRADING-PLAN-Rev.1.pdf"
C40 = next((c for c in (ROOT.parent / _REL,
                        Path(r"C:/Users/kobys/OneDrive/Desktop/past/P/Takeoff Automation") / _REL)
            if c.exists()), None)
if C40 is None:
    print("skip C4.0: the corpus is not on this machine")
else:
    pg = pymupdf.open(str(C40))[0]
    res = A.page_areas(pg, {"ft_per_paper_inch": 20.0}, None)
    g = next((g for g in res["groups"] if g["layer"].endswith("P1-BLDG-HATCH")), None)
    check(g is not None and g["n"] == 6, "C4.0: the building fill row, 6 regions")
    check(g is not None and near(g["area_pt2"], 301309.5, 1.0),
          f"C4.0 building = 301,309.5 pt2 (got {g and g['area_pt2']}; was 150,658.3)")
    check(g is not None and near(g["area_sf"], 23249.2, 0.2),
          f"C4.0 building = 23,249.2 SF at 1\" = 20' (PDF paint 23,247.9; was 11,624.9)")

if FAILS:
    print(f"\n{len(FAILS)} FAILED")
    sys.exit(1)
print("\nall passed")
