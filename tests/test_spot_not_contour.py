"""A SPOT GRADE IS NOT A CONTOUR'S HEIGHT (2026-09-17).

A spot grade is a POINT the design sets - a wall top, a finish grade, a rim -
and a contour is a LINE at one height. The sheet says which it means by
printing a tag beside the number ("551.2 BW"). Until today the nearest-label
match took the number anyway: measured on Oncor Cloudcrest engine page index
3 = printed C-04, 14 contour rows carried a tagged spot's height, 12 of them
a wall tag (BW 4, TW 5, EW 3) and FOUR of those spread along the contour by
inheritance. Cut read 2,279 CY against the offline analyst's 2,002 (+14%);
refused, it reads 2,056 (+3%) and the row count goes UP (163 -> 170) because
those lines fall to the interval count instead.

Pinned here, on a synthetic sheet (no corpus file needed):
  1. a line with a wall spot beside it and its own contour label further
     out takes THE CONTOUR LABEL - "read it off the line";
  2. a line whose only nearby number is a wall spot gets NO row at all,
     and nothing inherits that value along it;
  3. the refusal is reported (diag spot_tag_refused: how many, which tags);
  4. EG / ME - existing ground, match existing - still seed: they are the
     existing surface's own points (and on C-04 refusing them too moved
     the cut by 0 CY);
  5. is_built_spot_tag itself: the sheet's vocabulary, with or without the
     colon it sometimes prints, and nothing for a plain note word.

    python tests/test_spot_not_contour.py    (fails on the commit before it)
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


class Chain:
    """The chain view suggest_elevations reads (server.earthwork_suggest)."""

    def __init__(self, cid, pts, width=0.5, layer="C-TOPO", klass="dash1", sid=1):
        self.chain_id = cid
        self.pts = np.asarray(pts, float)
        self.length = float(np.hypot(*np.diff(self.pts, axis=0).T).sum())
        self.width = width
        self.layer = layer
        self.klass = klass
        self.color = [0, 0, 0]
        self.style_id = sid


def word(x, y, t, h=10.0, w=None):
    """One page word, as page_words hands them over."""
    w = w if w is not None else 6.0 * len(t)
    return (x, y, x + w, y + h, t, 0, 0, 0)


def line(y, x0=100.0, x1=400.0):
    return [[x0, y], [(x0 + x1) / 2, y], [x1, y]]


# Two contours 60 pt apart. The upper one prints its own label "550" and, a
# little further off, a wall spot "551.2 BW" - the C-04 shape. The lower one
# prints ONLY a wall spot "545.8 TW".
CHAINS = [Chain(1, line(300.0)), Chain(2, line(360.0))]
NEAR_X = 240.0
WORDS = [
    word(NEAR_X, 292.0, "550"),                       # the contour's own label, 3 pt above the line
    word(NEAR_X + 40, 296.0, "551.2"), word(NEAR_X + 76, 296.0, "BW"),
    word(NEAR_X, 353.0, "545.8"), word(NEAR_X + 36, 353.0, "TW"),
]
diag = {}
rows = {int(r["chain_id"]): r for r in ew.suggest_elevations(CHAINS, WORDS, diag=diag)}
print("   rows:", {c: (r.get("z"), r.get("how"), r.get("label_tag")) for c, r in rows.items()})
print("   diag:", {k: v for k, v in diag.items() if k == "spot_tag_refused"})
check(1 in rows and rows[1]["z"] == 550.0,
      "the line with a label of its own takes THAT number, not the wall spot beside it  (%s)"
      % (rows.get(1, {}).get("z"),))
check(2 not in rows,
      "the line whose only number is a wall spot gets no height at all  (%s)"
      % (rows.get(2, {}).get("z") if 2 in rows else None,))
check(not any(r.get("label_tag") and ew.is_built_spot_tag(r.get("label_tag")) for r in rows.values()),
      "...and no row anywhere carries a built-point tag")
sr = diag.get("spot_tag_refused") or {}
check(sr.get("n") == 2 and sr.get("tags") == {"BW": 1, "TW": 1},
      "the refusal is reported, by count and by tag  (%s)" % sr)

# ME still seeds: the existing surface's own point
CH2 = [Chain(3, line(300.0))]
W2 = [word(NEAR_X, 296.0, "586.0"), word(NEAR_X + 40, 296.0, "ME")]
d2 = {}
rows2 = {int(r["chain_id"]): r for r in ew.suggest_elevations(CH2, W2, diag=d2)}
check(3 in rows2 and rows2[3]["z"] == 586.0 and rows2[3].get("label_tag") == "ME",
      "a ME (match existing) number still seeds, and says so on the row  (%s)"
      % ({k: rows2.get(3, {}).get(k) for k in ("z", "label_tag")},))
check("spot_tag_refused" not in d2, "...and nothing is reported as refused for it")

# inheritance: a wall spot must not spread along the contour either. Two
# collinear pieces of one contour, the wall spot beside the first.
CH3 = [Chain(4, line(300.0, 100.0, 400.0)), Chain(5, line(300.0, 402.0, 700.0))]
W3 = [word(NEAR_X, 296.0, "551.2"), word(NEAR_X + 40, 296.0, "BW")]
d3 = {}
rows3 = ew.suggest_elevations(CH3, W3, diag=d3)
check(rows3 == [], "a wall spot cannot spread along the contour by inheritance either  (%d rows)"
      % len(rows3))

# the rule itself
cases = [("BW", True), ("TW", True), ("EW", True), ("FG", True), ("RIM", True), ("INV", True),
         ("FIN:", True), ("tw", True), ("ME", False), ("EG", False), ("ME:", False),
         ("TOP", False), ("", False), (None, False)]
bad = [(t, ew.is_built_spot_tag(t)) for t, want in cases if ew.is_built_spot_tag(t) is not want]
check(not bad, "is_built_spot_tag reads the sheet's vocabulary, colon or not  (wrong: %s)" % bad)

# --- 6. THE REAL CASE, on real geometry -------------------------------------
# Oncor C-04 chain 168, the case named in suggest_elevations' own comment:
# a 0.2 pt gray dashed contour whose nearest printed numbers are three wall
# spots - "551.2 BW" 2.9 pt off the line, "556.1 TW", "557.2 BW". The points
# and the word boxes below are lifted from the verified result and the page
# text (a 199 pt slice of the 1,284 pt chain, so no corpus file is needed -
# the same fixture style as tests/test_ew_suggest.py).
C168 = [[401.5, 821.1], [401.9, 820.0], [404.6, 811.4], [405.7, 808.2], [410.6, 794.4],
        [413.6, 785.9], [419.6, 769.0], [422.6, 760.5], [428.6, 743.5], [431.6, 735.1],
        [433.2, 730.7], [438.4, 718.3], [441.8, 710.0], [448.8, 693.4], [451.6, 684.9],
        [454.9, 667.3], [456.5, 658.4], [459.9, 640.7], [461.6, 631.9]]
W168 = [(417.0, 727.4, 442.16, 738.62, "551.2", 0, 0, 0),
        (444.94, 727.4, 461.13, 738.62, "BW", 0, 0, 0),
        (424.5, 747.86, 449.66, 759.08, "556.1", 0, 0, 0),
        (452.44, 747.86, 468.06, 759.08, "TW", 0, 0, 0),
        (424.14, 769.1, 449.3, 780.32, "557.2", 0, 0, 0),
        (452.08, 769.1, 468.27, 780.32, "BW", 0, 0, 0)]
real = Chain(168, C168, width=0.2, layer="", klass="dash1", sid=14)
real.color = [0.6, 0.6, 0.6]
d4 = {}
rows4 = ew.suggest_elevations([real], W168, diag=d4)
check(rows4 == [],
      "C-04 chain 168 - the case in the code's own comment - now takes no height from its "
      "three wall spots  (%s)" % [(r["chain_id"], r["z"], r.get("label_tag")) for r in rows4])
check((d4.get("spot_tag_refused") or {}).get("tags") == {"BW": 2, "TW": 1},
      "...and the three refusals are reported by tag  (%s)" % (d4.get("spot_tag_refused"),))

print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
