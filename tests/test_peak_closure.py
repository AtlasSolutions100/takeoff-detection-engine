"""A HILLTOP IS NOT A PLATEAU - AND A BUILDING PAD IS NOT A HILLTOP.

A surface built from contours cannot rise above its highest contour, so
every summit comes out flat and every pond bottom a floor. Measured against
closed-form truth on the analytic dome (scratchpad/proto_peak_closure.py,
2026-09-17): the fill reads -3.21% at a 10 ft contour interval and the
worst point is 10.0 ft low; one point inside the innermost ring at half the
interval reads -2.28% and 5.63 ft. `closure_points` puts that point there.

The other half of the job is refusing. Oncor C-04's ONLY closed ring is
chain 685: five points, 28,600 SF, elevation 569.9 assigned by `radius` on
a sheet whose contour interval is 1.0 ft - a pad or structure outline that
caught a nearby spot label. Closing it would have added half a foot of fill
over two thirds of an acre, silently. Part 2 pins that refusal on that
sheet's own geometry.

Half the interval is a CONVENTION (hand-takeoff practice, stated as such,
see earthwork.CLOSURE_FRACTION), never a reading: every closure says so and
a spot inside the ring beats it.

    python tests/test_peak_closure.py     (fails before closure_points existed)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

import takeoff_engine.earthwork as ew  # noqa: E402

FAILS = []


def check(ok, msg):
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        FAILS.append(msg)


def ring(cx, cy, r, n=41):
    t = np.linspace(0, 2 * np.pi, n)
    return np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t)])


def frag(y, x0=200.0, x1=800.0, n=30):
    return np.column_stack([np.linspace(x0, x1, n), np.full(n, y)])


# --- 1. a hill closes up, a pond closes down ------------------------------
print("\n1. the innermost ring gets a height, on the right side of it")
HILL = [(ring(500, 500, 50), 100.0), (ring(500, 500, 100), 99.0),
        (ring(500, 500, 150), 98.0)]
got = ew.closure_points(HILL, 1.0, 1.0)
check(len(got) == 1 and got[0]["closed"], "one closure on a three-ring hill  (%d)" % len(got))
c = got[0]
check(c["kind"] == "summit" and abs(c["z"] - 100.5) < 1e-6,
      "the top ring carries half an interval ABOVE itself: %s %s" % (c["kind"], c["z"]))
check(abs(c["x"] - 500) < 1.0 and abs(c["y"] - 500) < 1.0,
      "the point lands in the middle of the ring, not on its repeated closing corner  (%s, %s)"
      % (c["x"], c["y"]))
check(c["source"] == "convention" and "convention" in c["why"],
      "and it says the height is a convention, not a reading")
POND = [(ring(500, 500, 50), 98.0), (ring(500, 500, 100), 99.0),
        (ring(500, 500, 150), 100.0)]
got = ew.closure_points(POND, 1.0, 1.0)
check(len(got) == 1 and got[0]["kind"] == "low point" and abs(got[0]["z"] - 97.5) < 1e-6,
      "the same nesting the other way round reads as a low point at 97.5  (%s)"
      % [(g["kind"], g["z"]) for g in got])

# --- 2. C-04's pad outline, on its own numbers ----------------------------
print("\n2. Oncor C-04 chain 685: the ring that must NOT be closed")
# chain 685's own points, off the sheet: a 528 x 312 pt rectangle
PAD = np.array([[486.8, 753.4], [1014.8, 753.4], [1014.8, 1065.4],
                [486.8, 1065.4], [486.8, 753.4]], float)
C04 = [(PAD, 569.9)] + [(ring(1500, 1500, 60 + 30 * i), 566.0 + i) for i in range(4)]
got = ew.closure_points(C04, 1.0, 0.4167)
pad = [g for g in got if abs(g["ring_z"] - 569.9) < 1e-9]
check(len(pad) == 1 and not pad[0]["closed"],
      "the pad outline is refused, not closed  (%s)"
      % [(g["ring_z"], g["closed"]) for g in got])
check(pad and pad[0]["z"] is None and "vertices" in pad[0]["why"],
      "...and the refusal names the reason an estimator can check: %s"
      % (pad[0]["why"][:70] if pad else None))
check(pad and 27000 <= pad[0]["area_sf"] <= 30000,
      "...with the area at stake stated  (%s SF)" % (pad[0]["area_sf"] if pad else None))
# the same outline drawn as a real contour ring, at a real interval, DOES close
REAL = [(ring(750, 909, 290), 570.0)] +        [(frag(1500 + 120 * i, 0.0, 1500.0), 569.0 - i) for i in range(4)]
got = ew.closure_points(REAL, 1.0, 0.4167)
check(len(got) == 1 and got[0]["closed"] and got[0]["kind"] == "summit",
      "a knoll at the same spot - many vertices, on the 1 ft grid, lower ground "
      "around it - does close  (%s)" % [(g["kind"], g["closed"]) for g in got])
OFFGRID = [(ring(500, 500, 50), 100.35), (ring(500, 500, 100), 99.0),
           (ring(500, 500, 150), 98.0)]
got = [g for g in ew.closure_points(OFFGRID, 1.0, 1.0) if abs(g["ring_z"] - 100.35) < 1e-9]
check(len(got) == 1 and not got[0]["closed"] and "grid" in got[0]["why"],
      "an elevation off the family's own contour grid is refused too  (%s)"
      % [(g["closed"], g["why"][:40]) for g in got])

# --- 3. the sheet's own number beats the convention -----------------------
print("\n3. what the drawing prints wins over what practice assumes")
SPOT = [{"x": 505.0, "y": 498.0, "z": 100.8, "tag": "EG"}]
got = ew.closure_points(HILL, 1.0, 1.0, spots=SPOT)
check(len(got) == 1 and got[0]["source"] == "spot" and abs(got[0]["z"] - 100.8) < 1e-9,
      "a ground spot inside the ring sets the height  (%s @ %s)"
      % (got[0]["source"], got[0]["z"]))
check("EG" in got[0]["why"], "...and the reason names the tag it read  (%s)" % got[0]["why"][:60])

# --- 4. no evidence, no closure -------------------------------------------
print("\n4. where the drawing does not say which way the ground goes")
MIXED = [(ring(400, 400, 60), 567.0), (ring(1400, 1400, 60), 566.0), (frag(900), 568.0)]
got = [g for g in ew.closure_points(MIXED, 1.0, 1.0) if abs(g["ring_z"] - 567.0) < 1e-9]
check(len(got) == 1 and not got[0]["closed"],
      "a ring with higher ground on one side and lower on the other is refused  (%s)"
      % [(g["ring_z"], g["closed"]) for g in got])
check(ew.closure_points(HILL, None, 1.0) == [],
      "no readable contour interval, no closure at all")
check(ew.closure_points([(frag(300), 99.0), (frag(400), 100.0)], 1.0, 1.0) == [],
      "open contours close nothing")
TINY = [(ring(500, 500, 3), 100.0), (ring(500, 500, 100), 99.0)]
check(ew.closure_points(TINY, 1.0, 1.0) == [],
      "a ring smaller than %g SF is a rim or a tree, not a landform" % ew.CLOSURE_MIN_AREA_SF)

# --- 5. the accuracy claim, against closed-form truth ---------------------
print("\n5. the volume, measured against the exact answer for a dome")
# z = 600 - r^2/4000 over a 1000 x 1000 site: a dome 100 ft tall
CENTRE, DATUM = 500.0, 540.0


def dome_z(x, y):
    return 600.0 - ((x - CENTRE) ** 2 + (y - CENTRE) ** 2) / 4000.0


def dome_contours(interval):
    out = []
    for z in np.arange(500.0, 600.0, interval):
        r = np.sqrt(max(0.0, (600.0 - z) * 4000.0))
        if r <= 0:
            continue
        out.append((ring(CENTRE, CENTRE, r, 181), float(z)))
    return out


CELL, OUTER = 4.0, 460.0
_g = np.arange(CENTRE - OUTER, CENTRE + OUTER, CELL)
GX, GY = np.meshgrid(_g, _g)
INSIDE = np.hypot(GX - CENTRE, GY - CENTRE) <= OUTER


def fill_cy(contours, closures=()):
    """The engine's own surface (Surface.interpolator - the same TIN
    cut_fill differences), integrated above the datum over a fixed region.
    The region is held fixed so the comparison is the SURFACE and nothing
    else; cut_fill's own accuracy is pinned in test_earthwork_regress."""
    surf = ew.Surface("proposed")
    surf.contours.extend(contours)
    for c in closures:
        surf.points.append((c["x"], c["y"], c["z"]))
    f, _ = surf.interpolator()
    z = np.asarray(f(GX.ravel(), GY.ravel()), float).reshape(GX.shape)
    ok = INSIDE & ~np.isnan(z)
    return float(np.clip(z[ok] - DATUM, 0, None).sum() * CELL * CELL / 27.0), ok


def exact_cy(ok):
    return float(np.clip(dome_z(GX, GY)[ok] - DATUM, 0, None).sum() * CELL * CELL / 27.0)


for interval, must_improve in ((10.0, True), (1.0, False)):
    cons = dome_contours(interval)
    cl = ew.closure_points(cons, interval, 1.0)
    (before, ok_b), (after, ok_a) = fill_cy(cons), fill_cy(cons, cl)
    # SAME CELLS BOTH TIMES or the comparison is about coverage, not height
    ok = ok_b & ok_a
    (before, _), (after, _) = fill_cy(cons), fill_cy(cons, cl)
    ex = exact_cy(ok)
    eb, ea = before / ex - 1.0, after / ex - 1.0
    print("   dome at %4.0f ft: exact %s  ->  before %s (%+.2f%%)  after %s (%+.2f%%)"
          % (interval, format(ex, ",.0f"), format(before, ",.0f"), 100 * eb,
             format(after, ",.0f"), 100 * ea))
    if must_improve:
        check(len(cl) == 1 and cl[0]["closed"],
              "the dome's top ring is closed at a 10 ft interval  (%d)" % len(cl))
        check(abs(ea) < abs(eb) - 0.002,
              "closing it moves the fill toward the exact answer by more than a "
              "fifth of a point: %+.2f%% -> %+.2f%%" % (100 * eb, 100 * ea))
    else:
        check(abs(ea) <= abs(eb) + 0.0005,
              "at a 1 ft interval the rings already reach the top, so closure "
              "cannot spoil it: %+.2f%% -> %+.2f%%" % (100 * eb, 100 * ea))

print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
