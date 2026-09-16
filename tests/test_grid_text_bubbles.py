"""GRID BUBBLES LETTERED BY LIVE TEXT (2026-09-16; Jeff Ellis, ECI
Foundations, plan J16). chain._grid_annotation accepted a bubble as
"lettered" only when vector glyph ink sat inside the circle (Belle
Structural: outlined letters). Ellis Foundation S1.02 (ellis_plan.pdf p43,
engine index 42) draws its 1-15 / A-O labels as page TEXT, so no bubble was
lettered, the pass returned early, and 13,368 pt of dashed grid axes -
27.9% of the sheet's takeoff length, 1,485 ft at 1/8" = 1' - stayed in the
takeoff as walls. A short page word centred inside the circle now letters
it too, and an axis with a jog is accepted when both ends terminate at
lettered bubbles.

Synthetic sheet, pt == the drawing's own units: five dashed axes dropping
from a lettered top row to a lettered bottom row, one of them jogged; a
dashed pipe of another pen running THROUGH a lettered circle (the manhole
keep-case) that must stay. Fails on d0dc055 (words never reached the pass).

    python tests/test_grid_text_bubbles.py
"""
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from takeoff_engine.chain import build_chains  # noqa: E402
from takeoff_engine.extract import StrokeUnit  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


GREY = (0.78, 0.79, 0.8)
BLUE = (0.1, 0.2, 0.9)
R = 8.0


def circle(cx, cy, r=R, n=24):
    pts = [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]
    pts.append(pts[0])
    return StrokeUnit(np.array(pts, float), (0.14, 0.12, 0.13), 0.5, "[] 0", closed=True)


def dashed(pts, color=GREY, width=4.2):
    return StrokeUnit(np.array(pts, float), color, width, "[12 6] 0")


def word(cx, cy, text):
    return (cx - 3.0, cy - 4.0, cx + 3.0, cy + 4.0, text)


def sheet(with_words=True, jog=True):
    units, words = [], []
    xs = [100, 200, 300, 400, 500]
    for i, x in enumerate(xs):
        units.append(circle(x, 50)); words.append(word(x, 50, str(i + 1)))
        xb = x + (6 if (jog and x == 300) else 0)
        units.append(circle(xb, 600)); words.append(word(xb, 600, str(i + 1)))
        if jog and x == 300:
            # the jogged axis: 6 pt sideways half way down (Ellis grid 1)
            units.append(dashed([(x, 58), (x, 320), (xb, 330), (xb, 592)]))
        else:
            units.append(dashed([(x, 58), (x, 592)]))
    # the keep-case: a blue dashed pipe THROUGH a lettered manhole circle
    # on the top row's line, continuing both ways
    units.append(circle(700, 50)); words.append(word(700, 50, "M1"))
    units.append(dashed([(700, 10), (700, 400)], color=BLUE, width=0.8))
    # an ordinary wall (dashed, same pen as the grid) that ENDS at one
    # bubble only and is not straight: must survive the jog relaxation
    units.append(dashed([(900, 58), (900, 200), (960, 260), (960, 400)]))
    units.append(circle(900, 50)); words.append(word(900, 50, "W"))
    return units, (words if with_words else [])


def run(with_words=True, jog=True):
    units, words = sheet(with_words, jog)
    chains = build_chains(units, words=words)
    grid = [c for c in chains if c.klass == "annotation" and getattr(c, "dash_signature", "") == "grid"]
    return chains, grid


# ---- the fix: text-lettered bubbles arm the pass --------------------------------
chains, grid = run()
straight_axes = [c for c in grid if c.length >= 400 and abs(c.pts[0][0] - c.pts[-1][0]) < 1.0]
check(len(straight_axes) == 4, f"the four straight axes between text-lettered bubbles are grid annotation (got {len(straight_axes)})")
jogged = [c for c in grid if c.length >= 400 and 1.0 <= abs(c.pts[0][0] - c.pts[-1][0]) <= 10.0]
check(len(jogged) == 1, f"the jogged axis is grid too - both ends terminate at lettered bubbles (got {len(jogged)})")
bub = [c for c in chains if 40 < c.length < 60 and abs(c.pts[0][1] - c.pts[-1][1]) < 1.0]
check(bub and all(c.klass == "annotation" for c in bub if c.pts[:, 0].min() < 600), "the grid bubbles themselves are annotation")
pipe = [c for c in chains if c.length >= 300 and abs(c.pts[0][0] - 700) < 1.0]
check(len(pipe) == 1 and pipe[0].klass != "annotation", f"the blue pipe THROUGH a lettered manhole circle keeps its class ({pipe[0].klass if pipe else 'missing'})")
wall = [c for c in chains if c.pts[:, 0].max() > 950 and c.length >= 300]
check(len(wall) == 1 and wall[0].klass != "annotation", f"a dashed wall that ends at ONE bubble and bends stays a wall ({wall[0].klass if wall else 'missing'})")
takeoff_len = sum(c.length for c in chains if c.klass != "annotation")
check(takeoff_len < 1200, f"takeoff length is the pipe + the wall only ({takeoff_len:.0f} pt)")

# ---- without words nothing changes from the vector-only behaviour ----------------
chains0, grid0 = run(with_words=False)
check(len(grid0) == 0, "with no page words (and no vector glyphs) the pass stays out - the old behaviour, unchanged")
check(sum(c.length for c in chains0 if c.klass != "annotation") > 2500, "so the axes are still takeoff there (the defect this test pins)")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all grid-text-bubble checks passed")
