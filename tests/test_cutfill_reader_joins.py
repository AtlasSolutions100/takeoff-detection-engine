"""The reader's joins (cutfill/elevations.py), on drawn geometry - reader batch of 2026-09-27:

* THE NEARER END WINS: a piece end with exactly one other end of its surface within 12 pt
  continues into it whatever the angle, before the long facing pairs (a contour crossing a
  crowned street in a V: its legs end 9 pt apart, turned 90 degrees, and each leg's free end
  used to be paired with a well-aligned end 50 pt away);
* a hairpin is not two neighbours: two pieces that leave each other steadily from the gap are a
  V, two that keep their distance are the next contour;
* a dash is judged with its line: the peel takes the dashes of a dashed pen that follow each
  other end to end as one line;
* a layer the drafter NAMED as topography is a contour by the sheet's own word: a straight piece
  there may cross any number of lot lines, and an open piece may turn corners.

Every measured case behind these rules is in out/verify/2026-09-27/reader-batch/VERIFY.md.
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from shapely.geometry import LineString  # noqa: E402

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


def line(a, b, n=None):
    a, b = np.asarray(a, float), np.asarray(b, float)
    n = n or max(2, int(np.hypot(*(b - a)) / 4.0) + 1)
    return [list(a + (b - a) * t) for t in np.linspace(0.0, 1.0, n)]


def poly(*corners):
    pts = []
    for a, b in zip(corners, corners[1:]):
        seg = line(a, b)
        pts += seg if not pts else seg[1:]
    return pts


def wavy(x0, x1, y, amp=6.0, per=70.0):
    xs = np.linspace(x0, x1, max(2, int(abs(x1 - x0) / 4.0) + 1))
    return [[float(x), float(y + amp * np.sin((x - x0) / per * 2 * np.pi))] for x in xs]


def chain(cid, style, pts, ink=None):
    P = np.asarray(pts, dtype=float)
    L = poly_length(P)
    c = Chain(id=cid, style=style, pts=P, members=[], closed=False)
    c.length = L
    c.ink = L if ink is None else ink
    c.max_turn, c.mean_turn, c.n_sharp = turning_stats(P, 4.0)
    n_samp = max(1, int(L / 4.0))
    cap = max(2, int(0.03 * n_samp)) if L < 300 else max(2, int(0.15 * n_samp))
    c.candidate = L >= 40.0 and c.n_sharp <= cap and c.mean_turn <= 25.0
    return c


def styles_for(chains, pens):
    """pens = {style id: (gray, width, dashed, layer)}"""
    out = []
    for sid, (gray, width, dashed, layer) in pens.items():
        cs = [c for c in chains if c.style == sid]
        sg = StyleGroup(id=sid, color=(gray, gray, gray), gray=gray, width=width, layer=layer, dashed=dashed,
                        n_strokes=len(cs), n_chains=len(cs), median_stroke=50.0, link_radius=1.5,
                        total_length=sum(c.length for c in cs))
        sg.n_candidates = sum(1 for c in cs if c.candidate)
        sg.cand_length = sum(c.length for c in cs if c.candidate)
        out.append(sg)
    return out


def built(chains, pens, labels=()):
    styles = styles_for(chains, pens)
    fams = E.contour_families(styles, page_role="grading")
    gaps = find_gaps(chains)
    cts = E.build_contours(chains, gaps, fams, list(labels))
    return cts, {cid: ct for ct in cts for cid in ct.chain_ids}, fams


def on_line_label(lid, text, at, angle=0.0):
    x, y = at
    return make_label(lid, 0, text, (x - 9, y - 4, x + 9, y + 4), angle, "text", 100.0, 0.5, len_along=18.0, height=8.0)


# ---- 1. the nearer end wins: a V across a street ---------------------------------------------------------
# the contour comes down (N), runs east along the street (M), goes on south (S): two corners, 9 pt
# gaps. F continues N's direction 50 pt beyond N's end: well aligned, and another line.
N = chain(1, 1, line((100, 0), (100, 191)))
M = chain(2, 1, line((106.4, 197.4), (206.4, 197.4)))
S = chain(3, 1, line((212.8, 203.8), (212.8, 400)))
F = chain(4, 1, line((100, 241), (100, 420)))
O = chain(5, 1, wavy(300, 700, 300))             # more of the pen, so that it is a family ...
O2 = chain(7, 1, wavy(300, 700, 360))
pens = {1: (0.5, 0.7, False, "CE-TOPO-MINR")}


def printed():
    # ... with three printed heights: a pen with one or two has no range and loses them
    return [on_line_label(0, "579", (100, 100), 90.0), on_line_label(1, "578", (387.5, 306), 0.0),
            on_line_label(2, "577", (387.5, 366), 0.0)]


cts, by, fams = built([N, M, S, F, O, O2], pens, printed())
check(fams and fams[0].role == "existing", f"the pen is a surface: {[(f.role, f.role_reason) for f in fams]}")
check(by[1] is by[2] is by[3] and by[4] is not by[1],
      f"the legs of the V are one line, the aligned piece 50 pt away is another: {sorted(by[1].chain_ids)} | {sorted(by[4].chain_ids)}")
check(by[1].z == 579.0 and by[4].z is None and by[5].z == 578.0 and by[7].z == 577.0,
      f"the printed height goes round the corners, not across the street: {by[1].z} | {by[4].z}")
# two ends near one end: nothing is unique, the facing pair stands as before
M2 = chain(6, 1, line((93.6, 197.4), (20, 197.4)))
cts, by, _ = built([N, M, M2, S, F, O, O2], pens, printed())
check(by[1] is by[4], "an end with TWO ends near it has no nearest one: the facing pair joins as it always did")

# ---- 2. a hairpin opens from its tip, two neighbours keep their distance ---------------------------------
a = np.asarray(line((0, 0), (0, 60)))                       # up to the tip
b_hair = np.asarray(line((4.6, 60), (28, 0)))               # back down, leaving it
b_par = np.asarray(line((8, 60), (8, 0)))                   # the next contour, trimmed at the same edge
check(E._opens_from(a, True, LineString(b_hair), 4.6) and E._opens_from(b_hair, False, LineString(a), 4.6),
      "the legs of a hairpin leave each other from the tip")
check(not E._opens_from(a, True, LineString(b_par), 8.0) and not E._opens_from(b_par, False, LineString(a), 8.0),
      "two lines side by side do not")
H1 = chain(1, 1, line((0, 300), (0, 60)))
H2 = chain(2, 1, line((4.6, 60), (40, 300)))
P1 = chain(3, 1, line((200, 300), (200, 60)))
P2 = chain(4, 1, line((208, 60), (208, 300)))
cts, by, _ = built([H1, H2, P1, P2, O], pens)
check(by[1] is by[2], "a hairpin's legs, 4.6 pt apart at the tip, are one line")
check(by[3] is not by[4], "two parallel lines trimmed 8 pt apart are two lines")

# ---- 3. a dash is judged with its line -------------------------------------------------------------------
D1 = chain(1, 1, line((0, 100), (300, 100)), ink=300)
D2 = chain(2, 1, line((309, 100), (327, 100)), ink=18)
D3 = chain(3, 1, line((336, 100), (636, 100)), ink=300)
X1 = chain(4, 2, poly((314, 0), (314, 60), (316, 140), (314, 400)))
X2 = chain(5, 2, poly((322, 0), (322, 60), (324, 140), (322, 400)))
cmap = {c.id: c for c in (D1, D2, D3, X1, X2)}
role_of = {1: "existing", 2: "existing"}
alone = E._peel_crossers(set(cmap), cmap, role_of)
runs = E.dash_runs([D1, D2, D3, X1, X2], {1})
check(len({runs.get(1), runs.get(2), runs.get(3)}) == 1 and 4 not in runs and 5 not in runs,
      f"the three dashes are one run; a solid pen makes none: {runs}")
together = E._peel_crossers(set(cmap), cmap, role_of, run_of=runs)
check(alone == {2} and together == set(),
      f"judged alone the 18-pt dash crossing two lines goes (111 per 1000 pt); judged with its 618-pt line it stays: {alone} | {together}")

# ---- 4. a layer named as topography ----------------------------------------------------------------------
lots = [chain(10 + i, 3, line((60 + 40 * i, 40), (60 + 40 * i, 160))) for i in range(5)]
straight = line((20, 100), (260, 100))
topo = [chain(1, 1, straight), chain(2, 1, wavy(300, 700, 200)), chain(3, 1, wavy(300, 700, 260)), chain(4, 1, wavy(300, 700, 320))]
other = [chain(1, 1, straight), chain(2, 1, wavy(300, 700, 200)), chain(3, 1, wavy(300, 700, 260)), chain(4, 1, wavy(300, 700, 320))]


def printed4():
    # three printed heights besides the straight piece's own: the pen has a range with or without it
    return [on_line_label(0, "575", (140, 100), 0.0), on_line_label(1, "576", (387.5, 206), 0.0),
            on_line_label(2, "577", (387.5, 266), 0.0), on_line_label(3, "578", (387.5, 326), 0.0)]


_, by_t, fams_t = built(topo + lots, {1: (0.4, 0.3, False, "XREF|CP-TOPO-MAJR-LOTS"), 3: (0.0, 0.7, False, "C-ANNO-LOTS")}, printed4())
_, by_o, fams_o = built(other + lots, {1: (0.4, 0.3, False, "C-ROAD-EDGE"), 3: (0.0, 0.7, False, "C-ANNO-LOTS")}, printed4())
check(any(f.role == "proposed" for f in fams_t) and any(f.role == "proposed" for f in fams_o),
      f"both pens are a surface: {[(f.role, f.layer) for f in fams_t]} | {[(f.role, f.layer) for f in fams_o]}")
check(1 in by_t and by_t[1].z == 575.0, "a straight piece on a layer named TOPO crosses five lot lines and is still the 575 contour")
check(1 not in by_o and by_o[2].z == 576.0, "the same piece on a layer that names no topography is set aside (a straight line crossing four others)")
corners = poly((0, 0), (50, 5), (52, 55), (102, 58), (105, 108), (155, 111))
c_t = chain(1, 1, corners)
check(c_t.n_sharp >= 3 and c_t.length < 300, f"the test piece: {c_t.length:.0f} pt with {c_t.n_sharp} sharp corners")


def rest():
    return [chain(2, 1, wavy(300, 700, 200)), chain(3, 1, wavy(300, 700, 260)), chain(4, 1, wavy(300, 700, 320))]


_, by_t, fams_t = built([c_t] + rest(), {1: (0.5, 0.3, False, "CP-TOPO-MINR-LOTS")})
_, by_o, fams_o = built([chain(1, 1, corners)] + rest(), {1: (0.5, 0.3, False, "C-WALK")})
check(len(fams_t) == 1 and len(fams_o) == 1 and 2 in by_t and 2 in by_o, "both pens are a family (three smooth pieces each)")
check(1 in by_t and 1 not in by_o, "an open piece with three corners is a contour on a layer named TOPO, and no contour elsewhere")

# ---- 5. what the independent check of 2026-09-27 found on Rose Valley 2B sheet 3.0 ------------------------
# (a) an index contour never continues into a line between two index contours: the layers say which
minor = [chain(1, 1, wavy(0, 400, 100)), chain(2, 1, wavy(0, 400, 160)), chain(3, 1, wavy(0, 400, 220))]
stub = chain(4, 2, line((407.0, 97.0), (425.0, 92.0)))            # 7 pt from the end of piece 1, the MAJOR pen
major = [stub, chain(5, 2, wavy(0, 400, 400)), chain(6, 2, wavy(0, 400, 460)), chain(7, 2, wavy(0, 400, 520))]
two_pens = {1: (0.5, 0.3, True, "CE-TOPO-MINR"), 2: (0.4, 0.3, True, "CE-TOPO-MAJR")}
_, by, fams = built(minor + major, two_pens)
check({f.role for f in fams} == {"existing"} and E.layer_rank("XREF|CE-TOPO-MAJR") == "index"
      and E.layer_rank("CP-TOPO-MINR-LOTS") == "between" and E.layer_rank("C-TOPO") is None,
      "the layers' names say which pen draws the index contours and which the lines between them")
check(by[1] is not by[4], "a line between index contours does not continue into a stub of the index pen 7 pt from its end")
_, by, _ = built(minor + [chain(4, 1, line((407.0, 97.0), (425.0, 92.0)))] + major[1:], two_pens)
check(by[1] is by[4], "the same stub in the line's own pen is its continuation")

# (b) two lines side by side that draw together where they are trimmed are two lines
# (the sheet's own numbers: 21.7 pt apart 30 pt back from ends 9.7 pt apart)
L1 = chain(1, 1, poly((0, 0), (0, 170), (5.9, 190)))
L2 = chain(2, 1, poly((21.5, 0), (21.5, 170), (15.5, 191)))
_, by, _ = built([L1, L2, O, O2], pens)
gap_ = float(np.hypot(*(L1.pts[-1] - L2.pts[-1])))
check(9.0 <= gap_ <= 10.0 and by[1] is not by[2],
      f"two contours 21.5 pt apart, trimmed {gap_:.1f} pt from each other at a lot line, are not joined end to end")
check(E._runs_beside(L1.pts, True, LineString(L2.pts), gap_) and not E._alongside(L2.pts, LineString(L1.pts), L2.pts[-1]),
      "60 - 120 pt back from the gap they still run beside each other (the 15 pt of `_alongside` did not see them)")
V1 = chain(1, 1, poly((0, 0), (110, 0), (126, 8)))
V2 = chain(2, 1, poly((128, 17), (110, 40), (20, 230)))
_, by, _ = built([V1, V2, O, O2], pens)
check(by[1] is by[2] and not E._runs_beside(V1.pts, True, LineString(V2.pts), 9.2),
      "the legs of a V, near each other for their first 30 pt, are long gone from each other by then: one line")

# (c) what a pen's own printed heights say of it
from types import SimpleNamespace as NS  # noqa: E402


def lined(cid, sid, z, how="label", lids=()):
    return E.Contour(id=cid, style=sid, chain_ids=[cid], z=z, how=how, length=300.0, label_ids=list(lids))


cm_ = {k: NS(style=s) for k, s in ((1, 10), (2, 10), (3, 10), (4, 20), (5, 20), (6, 20), (7, 20), (8, 20), (9, 30), (10, 30), (11, 30))}
labs_ = [NS(id=k, value=v, chain_ids=[k]) for k, v in ((1, 570.0), (2, 575.0), (3, 580.0), (4, 571.0), (5, 572.0), (6, 573.0), (7, 574.0),
                                                        (8, 576.0), (9, 575.0), (10, 576.0), (11, 577.0))]
cts_ = [lined(k, cm_[k].style, l.value, lids=[k]) for k, l in zip(range(1, 12), labs_)]
ranks = E.pen_ranks(cts_, cm_, labs_, 1.0)
check(ranks == {10: "index", 20: "between"},
      f"three printed heights, all multiples of 5: the index pen; five, none a multiple: the pen between; a mixed pen says nothing ({ranks})")
free = E.Contour(id=50, style=10, chain_ids=[1], z=None, how="", length=300.0)
between = E.Contour(id=51, style=20, chain_ids=[4], z=None, how="", length=300.0)
both = E.Contour(id=52, style=10, chain_ids=[1, 4], z=None, how="", length=300.0)
mixed = E.Contour(id=53, style=30, chain_ids=[9], z=None, how="", length=300.0)
check(E.pen_allows(free, 570.0, ranks, cm_, 1.0) and not E.pen_allows(free, 574.0, ranks, cm_, 1.0),
      "a line of the index pen stands on 570, never on 574")
check(E.pen_allows(between, 574.0, ranks, cm_, 1.0) and not E.pen_allows(between, 580.0, ranks, cm_, 1.0),
      "a line of the pen between stands on 574, never on 580")
check(E.pen_allows(both, 574.0, ranks, cm_, 1.0) and E.pen_allows(mixed, 575.0, ranks, cm_, 1.0) and E.pen_allows(free, 574.0, {}, cm_, 1.0),
      "a line on pens of both kinds, a pen that says nothing, a sheet with no such pens: any height")
check(E.pen_ranks(cts_, cm_, labs_, 2.0).get(10) is None and E.pen_ranks(cts_, cm_, None, 1.0) == {},
      "at a 2 ft interval the index is every 10 ft (570, 575, 580 are not all index heights); with no labels nothing is said")

print("\n%d failure(s)" % fails)
sys.exit(1 if fails else 0)
