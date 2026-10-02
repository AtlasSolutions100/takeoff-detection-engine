"""THE TWO SURFACES CROSS EACH OTHER (2026-09-27, Jaro North 3: 63 existing contours of sheets C3.0
and C3.1, 19,283 pt, were "not contours" because they cross the proposed contours lot after lot;
the existing ground under the lots was stretched between the lines that were left and the job read
cut 12,003 CY against the engineer's 13,331):

1. an unlabelled, nearly straight piece of a surface pen that crosses four or more lines of the
   OTHER surface is a contour doing what contours do: it stays;
2. one that crosses four or more lines of its OWN surface is no contour (a grade break, a road
   edge, a swale centre line): set aside, as before;
3. one drawn DEAD STRAIGHT across four lines of either surface is no contour (a lot line, a street
   centre line in a contour pen): set aside, as before;
4. a piece of a pen that draws no surface is judged by every line it crosses, as before;
5. the sheet says how many pieces of the surfaces' pens were set aside for what they cross, by this
   rule and the two before it (it used to say nothing).

    python tests/test_cutfill_crossings.py
"""
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cutfill import elevations as E  # noqa: E402
from cutfill.chains import Chain, StyleGroup, find_gaps  # noqa: E402
from cutfill.geom import poly_length, turning_stats  # noqa: E402
from cutfill.labels import make_label  # noqa: E402

fails = 0


def check(ok, what):
    global fails
    print(("ok: " if ok else "FAIL: ") + what)
    if not ok:
        fails += 1


def chain(cid, sid, pts):
    P = np.asarray(pts, dtype=float)
    c = Chain(id=cid, style=sid, pts=P, members=[], closed=False)
    c.length = c.ink = poly_length(P)
    c.max_turn, c.mean_turn, c.n_sharp = turning_stats(P, 4.0)
    c.candidate = True
    return c


def wavy(x0, x1, y, amp=5.0, per=90.0, step=8.0):
    xs = np.arange(x0, x1 + 0.1, step)
    return [(float(x), float(y + amp * math.sin((x - 300.0) / per * 2 * math.pi))) for x in xs]


def across(x0, y0, x1, y1, amp=6.0, per=120.0, n=80):
    """A line from (x0, y0) to (x1, y1) that sways `amp` either side of the straight: nearly straight."""
    L = math.hypot(x1 - x0, y1 - y0)
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    out = []
    for k in range(n + 1):
        s = L * k / n
        o = amp * math.sin(s / per * 2 * math.pi)
        out.append((x0 + ux * s - uy * o, y0 + uy * s + ux * o))
    return out


def styles_for(chs, pens):
    out = []
    for sid, (gray, width, dashed, layer) in pens.items():
        cs = [c for c in chs if c.style == sid]
        sg = StyleGroup(id=sid, color=(gray, gray, gray), gray=gray, width=width, layer=layer, dashed=dashed, n_strokes=len(cs),
                        n_chains=len(cs), median_stroke=50.0, link_radius=1.5, total_length=sum(c.length for c in cs))
        sg.n_candidates = sum(1 for c in cs if c.candidate)
        sg.cand_length = sum(c.length for c in cs if c.candidate)
        out.append(sg)
    return out


PENS = {1: (0.0, 0.5, False, ""),       # the proposed contours: black, solid
        2: (0.7, 0.7, True, ""),        # the existing contours: screened, dashed
        5: (0.3, 0.25, False, "")}      # a pen that draws no surface


def sheet(extra):
    """Six proposed lines on the left half (x 0 .. 600) and six existing lines on the right half
    (x 700 .. 1300), each two pieces with its height printed in the gap between them; `extra` =
    [(pen, points)] are the pieces under test, unlabelled. Returns the pieces, labels, pens and
    the ids of the pieces under test."""
    chs, labs = [], []

    def line(sid, x0, y, text, gray):
        k = len(chs)
        chs.append(chain(k, sid, wavy(x0, x0 + 280, y)))
        chs.append(chain(k + 1, sid, wavy(x0 + 320, x0 + 600, y)))
        labs.append(make_label(len(labs), 0, text, (x0 + 291, y - 4, x0 + 309, y + 4), 0.0, "glyph", 95.0, gray, len_along=18.0, height=8.0))
    for i in range(6):
        line(1, 0.0, 100 + 60 * i, str(661 + i), 0.0)
        line(2, 700.0, 100 + 60 * i, str(651 + i), 0.7)
    ids = []
    for sid, pts in extra:
        ids.append(len(chs))
        chs.append(chain(len(chs), sid, pts))
    for k in range(14):                                  # the third pen's own strokes, away from everything
        chs.append(chain(len(chs), 5, [(40.0 + 30 * k, 900.0), (61.0 + 30 * k, 905.0)]))
    fams = E.contour_families(styles_for(chs, PENS), page_role="grading")
    f5 = next(f for f in fams if f.style == 5)
    f5.role, f5.role_reason = "ignore", "not in the sheet's legend, which shows a pen for every contour row"
    return chs, labs, fams, ids


def built(extra):
    chs, labs, fams, ids = sheet(extra)
    stats: dict = {}
    cts = E.build_contours(chs, find_gaps(chs), fams, labs, stats=stats)
    inl = {cid for ct in cts for cid in ct.chain_ids}
    return [i in inl for i in ids], stats, chs, ids


nearly = across(40.0, 70.0, 250.0, 430.0)                 # over the six proposed lines of the left half
P = np.asarray(nearly)
st = float(np.hypot(*(P.max(axis=0) - P.min(axis=0)))) / poly_length(P)
check(0.96 <= st < 0.998, f"the piece under test is nearly straight, not dead straight: {st:.3f}")

# ---- 1. an existing contour across the proposed contours ----------------------------------------------------
kept, stats, chs, ids = built([(2, nearly)])
check(kept == [True] and not stats.get("crossers"),
      f"a nearly straight piece of the existing pen that crosses six PROPOSED lines and none of its own stays a contour: {kept}, {stats}")
kept, stats, chs, ids = built([(1, across(740.0, 70.0, 950.0, 430.0))])
check(kept == [True], "and so does a piece of the proposed pen across six existing lines")

# ---- 2. across its own surface ---------------------------------------------------------------------------------
kept, stats, chs, ids = built([(1, nearly)])
check(kept == [False] and stats.get("crossers") == 1 and abs(stats["crossers_pt"] - poly_length(P)) < 0.2,
      f"the same piece in the PROPOSED pen crosses six lines of its own surface: no contour, and the sheet says so ({kept}, {stats})")
kept, stats, chs, ids = built([(2, across(740.0, 70.0, 950.0, 430.0))])
check(kept == [False], "a piece of the existing pen across six existing lines: no contour")

# ---- 3. dead straight across the other surface ----------------------------------------------------------------
kept, stats, chs, ids = built([(2, [(40.0, 70.0), (250.0, 430.0)])])
check(kept == [False] and stats.get("crossers") == 1,
      f"a piece of the existing pen drawn dead straight across six proposed lines is a lot line, not a contour: {kept}")
kept, stats, chs, ids = built([(2, [(40.0, 70.0), (145.0, 250.0), (250.0, 430.0)])])
check(kept == [False], "dead straight through three points as well")
kept, stats, chs, ids = built([(2, across(40.0, 70.0, 120.0, 207.0))])
check(kept == [True], "a nearly straight piece across two lines crosses too few to say anything, as before")

# ---- 4. a pen that draws no surface -----------------------------------------------------------------------------
kept, stats, chs, ids = built([(5, nearly)])
check(kept == [False], "a nearly straight piece of a pen that draws no surface, across six lines of any surface: set aside, as before")

# ---- both at once: the worst goes first, and the order does not depend on how a set iterates ---------------------
runs = [built([(2, nearly), (1, across(60.0, 70.0, 270.0, 430.0)), (1, across(300.0, 70.0, 510.0, 430.0))]) for _ in range(3)]
check(all(r[0] == [True, False, False] for r in runs), f"an existing contour beside two proposed-pen pieces that cross their own surface: it stays, they go ({runs[0][0]})")

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILED'}")
sys.exit(1 if fails else 0)
