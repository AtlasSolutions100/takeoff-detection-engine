"""A SURFACE WHOSE SAMPLES SIT ON ONE LINE IS REFUSED, NOT CRASHED.

Reproduced 2026-09-08 on Barn C3.0 (a site plan with no contours): an
operator who assigns one straight utility line a side, or three spot
grades along a kerb, passed the count floor (3+ samples) and then hit
scipy's Qhull inside LinearNDInterpolator - "initial simplex is flat" - which
the route answered with a bare 500 Internal Server Error. cut_fill's every
other refusal is a sentence with an error_code and the surface it names;
this one now is too.

On the pre-fix commit the first case below RAISES (QhullError), which is the
failure this file exists to pin.

Run:  python tests/test_ew_collinear.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from takeoff_engine.earthwork import Surface, cut_fill, zone_cut_fill  # noqa: E402

FAILS = []


def check(ok, msg):
    if ok:
        print("ok:", msg)
    else:
        FAILS.append(msg)
        print("FAIL:", msg)


def run(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception as e:      # the pre-fix behaviour: a raise, not a dict
        return {"raised": type(e).__name__ + ": " + str(e)[:80]}


# --- 1. one straight contour a side ----------------------------------------
line_a = [(100.0, 100.0), (200.0, 100.0), (300.0, 100.0), (400.0, 100.0)]
line_b = [(100.0, 140.0), (200.0, 140.0), (300.0, 140.0), (400.0, 140.0)]
ex = Surface("existing", contours=[(line_a, 100.0)])
pr = Surface("proposed", contours=[(line_b, 101.0)])
r = run(cut_fill, ex, pr, 1.0)
check("raised" not in r,
      "one straight contour a side does not raise  (" + str(r.get("raised")) + ")")
check(r.get("error_code") == "surface_collinear",
      "...it is refused as surface_collinear  (got " + str(r.get("error_code")) + ")")
check(r.get("surface", {}).get("name") == "existing",
      "...and the refusal names the surface (existing is judged first)")
check("straight line" in (r.get("error") or ""),
      "...in a sentence an operator can act on: " + str(r.get("error"))[:90])

# --- 2. three collinear spot grades ---------------------------------------
ex = Surface("existing", points=[(100.0, 100.0, 1.0), (200.0, 100.0, 2.0), (300.0, 100.0, 3.0)])
pr = Surface("proposed", points=[(100.0, 100.0, 1.5), (200.0, 100.0, 2.5), (300.0, 100.0, 3.5)])
r = run(cut_fill, ex, pr, 1.0)
check(r.get("error_code") == "surface_collinear",
      "three collinear spot grades a side: surface_collinear  (got "
      + str(r.get("error_code") or r.get("raised")) + ")")

# --- 3. the existing side fine, proposed collinear: names PROPOSED ---------
ex = Surface("existing", points=[(100.0, 100.0, 1.0), (200.0, 100.0, 2.0),
                                 (150.0, 180.0, 3.0), (250.0, 180.0, 2.0)])
r = run(cut_fill, ex, pr, 1.0)
check(r.get("error_code") == "surface_collinear"
      and r.get("surface", {}).get("name") == "proposed",
      "a good existing and a collinear proposed names proposed  ("
      + str(r.get("error_code") or r.get("raised")) + "/"
      + str(r.get("surface", {}).get("name")) + ")")

# --- 4. the count floor still fires first on a short surface ---------------
short = Surface("existing", points=[(100.0, 100.0, 1.0), (200.0, 100.0, 2.0)])
r = run(cut_fill, short, pr, 1.0)
check(r.get("error_code") == "surface_too_few_samples",
      "two samples are still 'too few', not 'collinear'  (got "
      + str(r.get("error_code") or r.get("raised")) + ")")

# --- 5. a bent contour is NOT collinear and still computes -----------------
bent_a = [(100.0, 100.0), (200.0, 100.0), (300.0, 160.0), (400.0, 160.0)]
bent_b = [(100.0, 120.0), (200.0, 120.0), (300.0, 140.0), (400.0, 140.0)]
ex = Surface("existing", contours=[(bent_a, 100.0), (line_b, 102.0)])
pr = Surface("proposed", contours=[(bent_b, 101.0), (line_a, 103.0)])
r = run(cut_fill, ex, pr, 1.0)
check("raised" not in r and "error" not in r and r.get("net_yd3") is not None,
      "a real pair of surfaces still produces a volume  ("
      + str(r.get("error") or r.get("raised") or ("net %.1f yd3" % r.get("net_yd3", float("nan")))) + ")")

# --- 6. the grading-zone route's own floor says the same -------------------
ex = Surface("existing", contours=[(line_a, 100.0)])
zone = [(50.0, 50.0), (450.0, 50.0), (450.0, 200.0), (50.0, 200.0)]
r = run(zone_cut_fill, ex, zone, 1.0, {"mode": "pad", "elev_ft": 100.0})
check(r.get("error_code") == "surface_collinear",
      "zone_cut_fill refuses a collinear existing the same way  (got "
      + str(r.get("error_code") or r.get("raised")) + ")")

# --- 7. the helper itself, on the shapes that matter -----------------------
from takeoff_engine.earthwork import _collinear  # noqa: E402
check(_collinear(np.array([[0, 0], [1, 1], [2, 2]])), "_collinear: a diagonal line")
check(_collinear(np.array([[0, 0], [1, 0]])), "_collinear: two points are a line")
check(not _collinear(np.array([[0, 0], [1, 0], [0, 1]])), "_collinear: a triangle is not")
check(not _collinear(np.array([[0, 0], [100, 0], [50, 0.01]])),
      "_collinear: a hundredth of a point of sag is a shape (Qhull accepts it)")

print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
