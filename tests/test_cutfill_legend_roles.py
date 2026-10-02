"""The legend names the pens of the two surfaces (cutfill/elevations.py read_legend_roles), and
what a pen is drawn with says whether it is a contour - rules of 2026-09-27, each from Home 2
Suites C3, where the site's cut and fill were taken between the wrong surfaces:

* THE SWATCH IS THE RUN OF STROKES BESIDE THE CAPTION, not everything in the row out to 420 pt
  (the plan's own linework, 276 pt from the captions, outweighed each row's swatch);
* A PEN TWO ROWS DISAGREE ON IS NAMED BY NEITHER (the first row used to keep it);
* where layers are named as topography, a swatch drawn on another layer is the legend's own;
* two pens the legend puts on different surfaces are two families, however alike;
* a legend read whole is the list of the contour pens;
* a piece drawn with lines and arcs in a pen whose labelled pieces are polylines is drafted
  geometry (a curb, a walk), not a contour;
* a word set in letters the PDF fills AND strokes is drawn filled for the OCR.

Every measured case is in out/verify/2026-09-27/h2s-roles/VERIFY.md.
"""
import math
import os
import sys
from types import SimpleNamespace

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cutfill import elevations as E  # noqa: E402
from cutfill import ocr  # noqa: E402
from cutfill.chains import Chain, StyleGroup, find_gaps  # noqa: E402
from cutfill.geom import poly_length, turning_stats  # noqa: E402
from cutfill.ingest import Stroke  # noqa: E402
from cutfill.labels import make_label  # noqa: E402

fails = 0


def check(ok, what):
    global fails
    print(("ok: " if ok else "FAIL: ") + what)
    if not ok:
        fails += 1


def stroke(idx, pts, gray=0.0, width=0.4, layer="", also_filled=False, closed=False):
    return Stroke(pts=np.asarray(pts, dtype=float), color=(gray, gray, gray), width=width, layer=layer, closed=closed,
                  dash=False, opacity=1.0, filled=False, idx=idx, also_filled=also_filled)


def style(sid, gray, width, dashed, layer="", cand=5000.0):
    return StyleGroup(id=sid, color=(gray, gray, gray), gray=gray, width=width, layer=layer, dashed=dashed,
                      n_strokes=50, n_chains=20, n_candidates=12, cand_length=cand, total_length=cand)


def caption(lid, text, y, x0=1736.0, x1=1853.0):
    return make_label(lid, 0, text, (x0, y - 6.8, x1, y + 6.8), 0.0, "text", 100.0, 0.0)


def dashes(i0, y, gray, xs=(1646.9, 1656.2, 1669.9, 1701.8, 1716.0), lens=(5.0, 9.1, 4.8, 9.8, 9.8)):
    return [stroke(i0 + k, [(x, y), (x + L, y)], gray=gray) for k, (x, L) in enumerate(zip(xs, lens))]


def solid(i0, y, width):
    return [stroke(i0, [(1646.9, y), (1676.4, y)], width=width), stroke(i0 + 1, [(1694.4, y), (1725.8, y)], width=width)]


# ---- 1. the four rows of Home 2 Suites C3, with the plan's linework in their bands -------------------------
rows = [caption(0, "= EXISTING 1' CONTOUR", 1274.2), caption(1, "= EXISTING 5' CONTOUR", 1290.0),
        caption(2, "= PROPOSED 1' CONTOUR", 1305.2), caption(3, "= PROPOSED 5' CONTOUR", 1321.0)]
styles = [style(1, 0.0, 0.4, False), style(13, 0.0, 0.5, False), style(2, 0.5, 0.4, True), style(10, 0.25, 0.4, True)]
sw = dashes(100, 1274.6, 0.5) + dashes(110, 1289.0, 0.25) + solid(120, 1305.8, 0.4) + solid(130, 1320.2, 0.5)
# the plan, 276 - 416 pt from the captions: parking stripes and curb pieces in the black 0.4 pen
plan = [stroke(200, [(1395.1, 1267.9), (1459.9, 1267.9)]), stroke(201, [(1395.1, 1300.3), (1459.9, 1300.3)]),
        stroke(202, [(1395.1, 1332.7), (1459.9, 1332.7)]),
        stroke(203, [(1317.1, 1272.1), (1331.3, 1272.1)]), stroke(204, [(1317.8, 1272.9), (1344.1, 1272.9)]),
        stroke(205, [(1324.6, 1271.5), (1344.5, 1271.5)]), stroke(206, [(1317.1, 1276.8), (1327.7, 1276.8)])]
info, notes = {}, []
got = E.read_legend_roles(rows, sw + plan, styles, capable={1, 13, 2, 10}, notes=notes, info=info)
check({k: v[0] for k, v in got.items()} == {2: "existing", 10: "existing", 1: "proposed", 13: "proposed"},
      f"each row names the pen of the swatch beside it: {sorted((k, v[0]) for k, v in got.items())}")
check(got.get(1, ("", ""))[1] == "legend: = PROPOSED 1' CONTOUR", f"the black pen is named by its own row: {got.get(1)}")
check(info == {"rows": 4, "named": 4, "roles": ["existing", "proposed"]} and not notes, f"four rows, four named, nothing to say: {info} {notes}")

# ---- 2. two rows that disagree on one pen: named by neither ------------------------------------------------
rows2 = [caption(0, "= EXISTING CONTOUR", 1274.2), caption(1, "= PROPOSED CONTOUR", 1305.2)]
sw2 = solid(100, 1274.6, 0.4) + solid(110, 1305.8, 0.4)
info, notes = {}, []
got = E.read_legend_roles(rows2, sw2, [style(1, 0.0, 0.4, False)], capable={1}, notes=notes, info=info)
check(got == {} and info["rows"] == 2 and info["named"] == 0, f"one pen under both captions is named by neither: {got} {info}")
check(len(notes) == 1 and "EXISTING CONTOUR" in notes[0] and "PROPOSED CONTOUR" in notes[0] and "=" not in notes[0],
      f"and the operator is told which rows: {notes}")

# ---- 3. layers named as topography: a swatch on another layer is the legend's own ---------------------------
st3 = [style(1, 0.0, 0.9, False, layer="C-DTLS"), style(2, 0.4, 0.3, False, layer="CP-TOPO-MINR-LOTS"),
       style(3, 0.5, 0.3, True, layer="CE-TOPO-MINR")]
sw3 = [stroke(100, [(1646.9, 1305.8), (1676.4, 1305.8)], width=0.9, layer="C-DTLS"),
       stroke(101, [(1694.4, 1305.8), (1725.8, 1305.8)], width=0.9, layer="C-DTLS")]
got = E.read_legend_roles([caption(0, "= PROPOSED CONTOUR", 1305.2)], sw3, st3, capable={1, 2, 3})
check(got == {}, f"a swatch on the details layer names no pen where the sheet has layers named TOPO: {got}")
got = E.read_legend_roles([caption(0, "= PROPOSED CONTOUR", 1305.2)],
                          [stroke(100, [(1646.9, 1305.8), (1676.4, 1305.8)], gray=0.4, width=0.3, layer="CP-TOPO-MINR-LOTS"),
                           stroke(101, [(1694.4, 1305.8), (1725.8, 1305.8)], gray=0.4, width=0.3, layer="CP-TOPO-MINR-LOTS")],
                          st3, capable={1, 2, 3})
check({k: v[0] for k, v in got.items()} == {2: "proposed"}, f"a swatch drawn on the contour layer itself names its pen: {got}")
got = E.read_legend_roles([caption(0, "= PROPOSED CONTOUR", 1305.2)], sw3, [style(1, 0.0, 0.9, False, layer="C-DTLS")], capable={1})
check({k: v[0] for k, v in got.items()} == {1: "proposed"}, f"with no layer named as topography the swatch's pen is named as before: {got}")

# ---- 4. two pens on different surfaces are two families -----------------------------------------------------
fams = E.contour_families([style(1, 0.0, 0.4, False), style(13, 0.0, 0.5, False)], page_role="grading",
                          forced={1: ("existing", "legend: = EXISTING CONTOUR"), 13: ("proposed", "legend: = PROPOSED CONTOUR")})
check(sorted((f.styles, f.role) for f in fams) == [([1], "existing"), ([13], "proposed")],
      f"black 0.4 named existing and black 0.5 named proposed stay apart: {[(f.styles, f.role) for f in fams]}")
fams = E.contour_families([style(1, 0.0, 0.4, False), style(13, 0.0, 0.5, False)], page_role="grading",
                          forced={1: ("proposed", "legend: = PROPOSED 1' CONTOUR"), 13: ("proposed", "legend: = PROPOSED 5' CONTOUR")})
check(len(fams) == 1 and sorted(fams[0].styles) == [1, 13] and fams[0].role == "proposed", "two pens of one surface are one family, as before")
fams = E.contour_families([style(1, 0.0, 0.4, False), style(13, 0.0, 0.5, False)], page_role="grading",
                          forced={1: ("ignore", "set by you"), 13: ("proposed", "legend: PROPOSED CONTOURS")})
check(len(fams) == 1 and fams[0].role == "ignore", f"a pen the operator set and one the legend names still merge, his choice wins: {[(f.styles, f.role) for f in fams]}")

# ---- 5. a legend read whole is the list of the contour pens --------------------------------------------------
def fam_set():
    return E.contour_families([style(1, 0.0, 0.4, False), style(2, 0.5, 0.4, True), style(0, 0.7, 0.4, False),
                               style(5, 0.5, 0.9, True), style(7, 0.4, 0.3, False, layer="CP-TOPO-MINR")], page_role="grading",
                              forced={1: ("proposed", "legend: = PROPOSED CONTOUR"), 2: ("existing", "legend: = EXISTING CONTOUR")})


fams = fam_set()
out = E.leave_out_unlisted_pens(fams, {"rows": 2, "named": 2, "roles": ["existing", "proposed"]})
role = {f.style: f.role for f in fams}
check([f.style for f in out] == [0] and role[0] == "ignore", f"the gray solid pen (a hatch) the legend does not show is no surface: {role}")
check(role[5] == "existing" and role[7] == "proposed", "a heavier pen of a shown pen's colour and dash stays (index contours), and so does a layer named TOPO")
fams = fam_set()
check(E.leave_out_unlisted_pens(fams, {"rows": 3, "named": 2, "roles": ["existing", "proposed"]}) == []
      and E.leave_out_unlisted_pens(fams, {"rows": 2, "named": 2, "roles": ["proposed"]}) == []
      and E.leave_out_unlisted_pens(fams, {}) == [],
      "a legend read in part, or for one surface only, decides nothing")

# ---- 6. how a stroke is drawn ---------------------------------------------------------------------------------
arc = [(50 + 30 * math.cos(t), 50 + 30 * math.sin(t)) for t in np.linspace(0, math.pi / 2, 10)]
poly = [(0, 0), (12, 3), (31, 2), (40, 9), (66, 7), (71, 15), (98, 11), (120, 20), (133, 17), (160, 30)]
check(E.stroke_kind([(0, 0), (80, 3)]) == "line" and E.stroke_kind([(0, 0), (40, 1.5), (80, 3)]) == "line", "two points, or three on one straight: a line")
check(E.stroke_kind(arc) == "arc", "even steps turning one way by even angles: an arc")
check(E.stroke_kind(poly) == "poly", "a contour's own points: a polyline")


def chain(cid, sid, pts, members):
    P = np.asarray(pts, dtype=float)
    c = Chain(id=cid, style=sid, pts=P, members=members, closed=False)
    c.length = c.ink = poly_length(P)
    c.max_turn, c.mean_turn, c.n_sharp = turning_stats(P, 4.0)
    c.candidate = True
    return c


def wavy(x0, x1, y, amp=6.0, per=70.0, step=9.0):
    xs = np.arange(x0, x1 + 0.1, step)
    return [(float(x), float(y + amp * math.sin((x - x0) / per * 2 * math.pi) + 0.8 * math.sin(x * 1.7))) for x in xs]


strokes, chains = [], []
for k, y in enumerate((100, 160, 220, 280)):                      # four contours, each one polyline stroke
    strokes.append(stroke(k, wavy(0, 600, y)))
    chains.append(chain(k, 1, strokes[-1].pts, [k]))
curb = [stroke(10, [(0, 130), (250, 130)]), stroke(11, [(250 + 30 * math.sin(t), 100 + 30 * math.cos(t)) for t in np.linspace(0, math.pi / 2, 10)]),
        stroke(12, [(280, 100), (280, -80)])]
strokes += curb
curb_pts = np.vstack([s.pts for s in curb])
chains.append(chain(4, 1, curb_pts, [10, 11, 12]))
shares = E.drawn_shares(chains, strokes)
check(shares[0] == 0.0 and shares[4] == 1.0, f"a contour is all polyline, a curb all lines and arcs: {shares[0]}, {shares[4]}")
kept = chain(5, 1, [(0, 400), (300, 400)], [])
check(5 not in E.drawn_shares([kept], strokes), "a piece with no strokes behind it (drawn by the operator) has no share: nothing is known of it")


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


def lab(lid, text, x, y):
    return make_label(lid, 0, text, (x - 9, y - 4, x + 9, y + 4), 0.0, "text", 100.0, 0.0, len_along=18.0, height=8.0)


def on(ch, x):
    P = ch.pts
    i = int(np.argmin(np.abs(P[:, 0] - x)))
    return float(P[i, 0]), float(P[i, 1])


def printed(chs):
    return [lab(k, str(5990 + k), *on(chs[k], 300.0)) for k in range(3)]


fams = E.contour_families(styles_for(chains, {1: (0.0, 0.4, False, "")}), page_role="grading",
                          forced={1: ("proposed", "legend: = PROPOSED CONTOUR")})
cts = E.build_contours(chains, find_gaps(chains), fams, printed(chains), drawn=shares)
by = {cid: ct for ct in cts for cid in ct.chain_ids}
check(all(k in by for k in (0, 1, 2, 3)) and 4 not in by,
      f"three labelled polylines say how this pen draws a contour: the curb is set aside, the unlabelled contour stays ({sorted(by)})")
check(by[0].z == 5990.0 and by[2].z == 5992.0, "the printed heights stand")
cts = E.build_contours(chains, find_gaps(chains), fams, printed(chains))
check(4 in {cid for ct in cts for cid in ct.chain_ids}, "with nothing known of how the pieces are drawn, nothing is set aside")
cts = E.build_contours(chains, find_gaps(chains), fams, printed(chains)[:2], drawn=shares)
check(4 in {cid for ct in cts for cid in ct.chain_ids}, "two labelled pieces are not evidence enough (three at least)")
# a dashed pen: every dash is a straight stroke, its labelled pieces say so, nothing is set aside
dstrokes, dchains = [], []
for k, y in enumerate((100, 160, 220, 280)):
    P = np.asarray(wavy(0, 600, y, step=9.0))
    mem = []
    for j in range(0, len(P) - 1, 2):
        dstrokes.append(stroke(len(dstrokes), [P[j], P[j + 1]], gray=0.5))
        mem.append(dstrokes[-1].idx)
    dchains.append(chain(k, 2, P, mem))
dsh = E.drawn_shares(dchains, dstrokes)
fams_d = E.contour_families(styles_for(dchains, {2: (0.5, 0.4, True, "")}), page_role="grading",
                            forced={2: ("existing", "legend: = EXISTING CONTOUR")})
cts = E.build_contours(dchains, find_gaps(dchains), fams_d, printed(dchains), drawn=dsh)
check(dsh[0] == 1.0 and {0, 1, 2, 3} <= {cid for ct in cts for cid in ct.chain_ids},
      "a dashed pen's pieces are all straight strokes, labelled ones too: every one of them stays a contour")

# ---- 7. the gentle contour pen is a contour pen ---------------------------------------------------------------
sway = [chain(k, 3, [(0, 60 * k), (200, 60 * k), (360, 60 * k + 110), (560, 60 * k + 130)], []) for k in range(4)]
rules = [chain(10 + k, 4, [(0, 50 * k), (250, 50 * k), (500, 50 * k + 0.5)], []) for k in range(4)]
cap = E.contour_capable_styles(sway + rules)
d0 = float(np.hypot(*(sway[0].pts.max(axis=0) - sway[0].pts.min(axis=0)))) / sway[0].length
check(0.9 <= d0 < 0.985 and sway[0].n_sharp == 0 and sway[0].max_turn >= 10.0,
      f"the test contour sways: {d0:.3f} of its diagonal, turning {sway[0].max_turn:.0f} degrees, no corner")
check(3 in cap and 4 not in cap, f"lines that sway are contour-like, rules are not: {sorted(cap)}")

# ---- 8. a word in letters the PDF fills and strokes is drawn filled -------------------------------------------
sq = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
cl = SimpleNamespace(id=0, angle=0.0, center=(5.0, 5.0), length=10.0, height=10.0, stroke_idx=[0])
hollow = ocr.render_cluster(cl, {0: stroke(0, sq, width=0.375, closed=True)}, 300)
full = ocr.render_cluster(cl, {0: stroke(0, sq, width=0.375, closed=True, also_filled=True)}, 300)
cy, cx = hollow.shape[0] // 2, hollow.shape[1] // 2
check(hollow[cy, cx] == 255 and full[cy, cx] == 0, "an outline the PDF also fills is drawn solid for the OCR; one it only strokes stays a line")
ring = {0: stroke(0, sq, closed=True, also_filled=True),
        1: stroke(1, [(3, 3), (7, 3), (7, 7), (3, 7), (3, 3)], closed=True, also_filled=True)}
cl2 = SimpleNamespace(id=0, angle=0.0, center=(5.0, 5.0), length=10.0, height=10.0, stroke_idx=[0, 1])
img = ocr.render_cluster(cl2, ring, 300)
check(img[cy, cx] == 255 and img[cy, int(cx - 4 * 300 / 72)] == 0, "and the hole of a letter stays open (even-odd)")
old = SimpleNamespace(pts=np.asarray(sq, float), color=(0, 0, 0), width=0.4, closed=True, filled=False, idx=0)
check(ocr.render_cluster(cl, {0: old}, 300)[cy, cx] == 255, "a stroke of a reading kept from before (no such field) is drawn as it was")

print("\n%d failure(s)" % fails)
sys.exit(1 if fails else 0)
