"""WHAT THE INK OF THE PRINTED HEIGHTS SAYS, and four rules beside it (2026-09-27, each from Jaro
North 3, sheets C3.0 / C3.1, where the existing index contours and the lot lines were the
PROPOSED surface and the job read cut 6,894 CY against the engineer's 13,331):

1. a layer named for parcels, easements, boundaries or the property line draws no ground;
2. a whole number read from the wrong side ("999" for 666) is turned by the sheet's own range;
3. a height printed IN its line loses the dashes read beside it ("652-" with a long dash);
4. a pen that is proposed on its colour alone and carries screened heights is a pen of the
   existing ground (roles_by_label_ink);
5. a height printed in the other surface's ink goes to that surface's line under it, or is let
   go (build_contours, per pen);
6. a screened contour height on no line is a point of the existing ground;
7. a leader ends at its point; a dash of a dashed line goes on (place_spots);
8. a short number printed to the thousandth is no spot grade (an acreage);
9. two printed heights that disagree on one line, one vote each, decide nothing: the line is
   counted from its neighbours (the first one read used to win).

Every measured case: out/verify/2026-09-27/jaro-roles/VERIFY.md.

    python tests/test_cutfill_label_ink.py
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
from cutfill.ingest import Stroke  # noqa: E402
from cutfill.labels import clean_read, make_label, turned, upright_heights  # noqa: E402

fails = 0


def check(ok, what):
    global fails
    print(("ok: " if ok else "FAIL: ") + what)
    if not ok:
        fails += 1


def stroke(idx, pts, gray=0.0, width=0.4, layer="", closed=False):
    return Stroke(pts=np.asarray(pts, dtype=float), color=(gray, gray, gray), width=width, layer=layer, closed=closed,
                  dash=False, opacity=1.0, filled=False, idx=idx)


def pen(sid, gray, width, dashed, layer=""):
    return StyleGroup(id=sid, color=(gray, gray, gray), gray=gray, width=width, layer=layer, dashed=dashed,
                      n_strokes=50, n_chains=20, n_candidates=12, cand_length=5000.0, total_length=5000.0)


# ---- 1. layers that draw no ground ------------------------------------------------------------------------
for layer, want in (("337.102 Unit 3 Plat|C-PARC-LOTS", "ignore"), ("337.102 Unit 3 Plat|C-PROP-LINE", "ignore"),
                    ("X|V-BNDY-ADJN", "ignore"), ("X|C-ESMT", "ignore"), ("337.102_TBLK|C-TBLK-LWRK", "ignore"),
                    ("TB3145104|CP-TOPO-MAJR-LOTS", "proposed"), ("CE-TOPO-MINR", "existing"), ("PROP-CONTOUR", "proposed"),
                    ("C-TOPO-ANNO-SPOT", "ignore")):
    got = E.guess_role(pen(1, 0.0, 0.5, True, layer), "grading")
    check(got[0] == want, f"layer {layer!r}: {got[0]} ({got[1]})")
check(E.guess_role(pen(1, 0.0, 0.5, True, ""), "grading")[0] == "existing" and E.guess_role(pen(1, 0.0, 0.5, False, "0"), "grading")[0] == "proposed",
      "a pen with no telling layer is read by its colour and dash, as before")

# ---- 2. read from the wrong side --------------------------------------------------------------------------
check(turned("999") == "666" and turned("666") == "999" and turned("699") == "669" and turned("1669") == "6991",
      "the same strokes from the other side: 999 is 666, 699 is 669")
check(turned("5991") is None and turned("900") is None and turned("66") is None and turned("91.66") is None,
      "a digit that means nothing turned, a turned number starting with 0, two digits, a decimal: not turned")


def num(lid, text, x, y, source="glyph", angle=30.0, gray=0.7, conf=95.0):
    return make_label(lid, 0, text, (x - 9, y - 4, x + 9, y + 4), angle, source, conf, gray, len_along=18.0, height=8.0)


sheet = [num(k, str(v), 100 + 40 * k, 100) for k, v in enumerate((652, 655, 658, 660, 661, 663, 664, 665, 668, 670))]
a, b, c, d, e = num(20, "999", 500, 100), num(21, "699", 540, 100), num(22, "999", 580, 100, source="text"), \
    num(23, "661", 620, 100), num(24, "99.16", 660, 100)
got = upright_heights(sheet + [a, b, c, d, e])
check([l.id for l in got] == [20] and a.value == 666.0 and a.text == "666" and abs(a.angle - 210.0) < 1e-6 and "999" in a.z_note,
      f"999 on a sheet of 652 .. 670 is 666, turned half a turn, and says what was read: {a.text} at {a.angle:g} ({a.z_note})")
check(b.value == 699.0, "699 could be a height of this sheet (a site spans tens of feet, and the number itself is among the heights read): left as read")
check(c.value == 999.0 and d.value == 661.0 and e.value == 99.16, "a number from the text layer, one already in the range, a decimal: left as read")
check(upright_heights([num(1, "999", 0, 0), num(2, "12", 0, 0)]) == [], "a sheet with no range of heights turns nothing")

# ---- 3. a height printed in its line -----------------------------------------------------------------------
for raw, want in (("652—", "652"), ("—663", "663"), ("~668", "668"), ("(655", "655"), ("- 653 -", "653"), ("5992 -", "5992"),
                  ("[100]", "[100]"), ("(E)", "(E)"), ("a 664", "a 664"), ("92. 89~", "92. 89~"), ("=>", "=>"), ("91.64 G.B", "91.64 G.B")):
    check(clean_read(raw) == want, f"{raw!r} reads {clean_read(raw)!r}")


# ---- 4 / 5. the ink of the heights ---------------------------------------------------------------------------
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
        3: (0.0, 0.3, True, "")}        # the existing INDEX contours: dark, thin, dashed


def sheet_of(extra_labels=(), through=None, index_ink=0.7, index_vals=(650, 655, 660, 665, 670, 675)):
    """Six proposed lines, six existing ones, six existing index lines; each line is two pieces
    with its height printed in the gap between them (x = 300). `through`: a proposed line
    that runs through (x, y) unbroken."""
    chs, labs = [], []

    def line(sid, y, text, gray, source="glyph"):
        k = len(chs)
        chs.append(chain(k, sid, wavy(0, 280, y)))
        chs.append(chain(k + 1, sid, wavy(320, 600, y)))
        if text is not None:
            labs.append(make_label(len(labs), 0, text, (291, y - 4, 309, y + 4), 0.0, source, 95.0, gray, len_along=18.0, height=8.0))
        return k
    for i in range(6):
        line(1, 100 + 60 * i, str(661 + i), 0.0)
    ex = [line(2, 130 + 60 * i, str(651 + i), 0.7) for i in range(6)]
    ix = [line(3, 520 + 60 * i, None if v is None else str(v), index_ink) for i, v in enumerate(index_vals)]
    if through is not None:
        chs.append(chain(len(chs), 1, wavy(0, 600, through)))
    for lb in extra_labels:
        lb.id = len(labs)
        labs.append(lb)
    fams = E.contour_families(styles_for(chs, PENS), page_role="grading")
    return chs, labs, fams, ex, ix


chs, labs, fams, ex, ix = sheet_of()
role = {f.style: (f.role, f.role_reason) for f in fams}
check(role[1][0] == "proposed" and role[2][0] == "existing" and role[3] == ("proposed", "dark linework (the screened family is the existing ground)"),
      f"by colour alone the dark dashed pen is the proposed surface: {role[3]}")
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
moved = E.roles_by_label_ink(fams, cts, labs)
f3 = next(f for f in fams if f.style == 3)
check([(f.style, g, n) for f, g, n in moved] == [(3, 6, 6)] and f3.role == "existing"
      and f3.role_reason == "its printed heights are screened like the existing ground's (6 of 6)",
      f"its six heights are screened like the existing ground's: it is a pen of the existing ground ({f3.role_reason})")
check(next(f for f in fams if f.style == 1).role == "proposed" and next(f for f in fams if f.style == 2).role == "existing",
      "the two pens the colour read right stay")
# the second pass: every line on its own surface
for l in labs:
    l.kind, l.chain_ids, l.z_note = "number", [], ""
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
by = {cid: ct for ct in cts for cid in ct.chain_ids}
check(all(by[k].z == v for k, v in zip(ix, (650.0, 655.0, 660.0, 665.0, 670.0, 675.0))),
      "built again, the index lines carry their heights as lines of the existing ground")
check(E.roles_by_label_ink(fams, cts, labs) == [], "and nothing is left to move")

chs, labs, fams, ex, ix = sheet_of(index_ink=0.0)
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
check(E.roles_by_label_ink(fams, cts, labs) == [], "a dark pen with dark heights stays where the colour put it")
chs, labs, fams, ex, ix = sheet_of(index_vals=(650, 655, None, None, None, None))
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
check(any(f.style == 3 and f.role == "proposed" for f in fams) and E.roles_by_label_ink(fams, cts, labs) == [],
      "two heights are not evidence enough (three at least)")
chs, labs, fams, ex, ix = sheet_of()
next(f for f in fams if f.style == 3).forced = True
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
check(E.roles_by_label_ink(fams, cts, labs) == [], "a pen the legend or the operator named is never moved")
chs, labs, fams, ex, ix = sheet_of()
next(f for f in fams if f.style == 3).role_reason = "layer name 'CP-TOPO-MAJR'"
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
check(E.roles_by_label_ink(fams, cts, labs) == [], "nor one its layer names")
chs, labs, fams, ex, ix = sheet_of()
for l in labs[:6]:
    l.gray = 0.7                        # a sheet that screens every height: the ink tells nothing
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
check(E.roles_by_label_ink(fams, cts, labs) == [], "where the proposed heights are screened too, the ink says nothing: nothing moves")

# 5. a height in the other surface's ink, in the gap of a line
dark = make_label(0, 0, "664", (141, 126 + 5 * math.sin((150 - 300) / 90 * 2 * math.pi) - 4, 159, 134 + 5 * math.sin((150 - 300) / 90 * 2 * math.pi)), 0.0,
                  "glyph", 95.0, 0.1, len_along=18.0, height=8.0)


def with_gap(chs_, cid, x0, x1):
    """The piece `cid` broken in two round x0 .. x1 (a label's mask); returns the new pieces' ids."""
    old = next(c for c in chs_ if c.id == cid)
    P = old.pts
    left, right = P[P[:, 0] <= x0], P[P[:, 0] >= x1]
    chs_.remove(old)
    a_ = chain(cid, old.style, left)
    b_ = chain(max(c.id for c in chs_) + 1, old.style, right)
    chs_ += [a_, b_]
    return a_.id, b_.id


chs, labs, fams, ex, ix = sheet_of(extra_labels=[dark])
la, lb_ = with_gap(chs, ex[0], 136.0, 164.0)            # the first existing line (y = 130) is broken under the dark height
fams = E.contour_families(styles_for(chs, PENS), page_role="grading")
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
got = next(l for l in labs if l.text == "664" and l.gray == 0.1)
check(got.kind == "number" and got.chain_ids == [] and got.z_note == "printed dark like the sheet's proposed heights, but no proposed line runs under it",
      f"a dark height in the gap of a screened line, no proposed line under it: let go ({got.kind}, {got.z_note!r})")
by = {cid: ct for ct in cts for cid in ct.chain_ids}
check(by[la].z == 651.0 and by[lb_].z == 651.0, f"the existing line keeps its own height: {by[la].z}")
dark2 = make_label(0, 0, "664", (141, 126 + 5 * math.sin((150 - 300) / 90 * 2 * math.pi) - 4, 159, 134 + 5 * math.sin((150 - 300) / 90 * 2 * math.pi)), 0.0,
                   "glyph", 95.0, 0.1, len_along=18.0, height=8.0)
chs, labs, fams, ex, ix = sheet_of(extra_labels=[dark2], through=130.0)
la, lb_ = with_gap(chs, ex[0], 136.0, 164.0)
fams = E.contour_families(styles_for(chs, PENS), page_role="grading")
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
got = next(l for l in labs if l.text == "664" and l.gray == 0.1)
thr = next(c.id for c in chs if c.style == 1 and abs(c.pts[0][0]) < 1e-6 and abs(c.pts[-1][0] - 600.0) < 1e-6)
check(got.kind == "contour" and got.chain_ids == [thr] and "moved to the proposed line under it" in got.z_note,
      f"with a proposed line under it, it goes to that line: {got.chain_ids} ({got.z_note!r})")
# per pen: a pen whose heights are all dark keeps them, whatever the other pens of its surface print
chs, labs, fams, ex, ix = sheet_of(index_ink=0.0)
for f in fams:
    if f.style == 3:
        f.role, f.role_reason, f.forced = "existing", "legend: = EXISTING 5' CONTOUR", True
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
by = {cid: ct for ct in cts for cid in ct.chain_ids}
check(all(by[k].z == v for k, v in zip(ix, (650.0, 655.0, 660.0, 665.0, 670.0, 675.0))),
      "an existing pen that prints its heights dark (the 5-ft contours of another sheet) keeps them: the rule is per pen")

# ---- 6. a screened height on no line ------------------------------------------------------------------------
chs, labs, fams, ex, ix = sheet_of()
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
E.roles_by_label_ink(fams, cts, labs)
for l in labs:
    l.z = l.value
alone = [num(900, "653", 450, 900), num(901, "654", 480, 900, gray=0.1), num(902, "653.5", 510, 900), num(903, "700", 540, 900),
         num(904, "655", 570, 900, conf=60.0), num(905, "656", 300, 40)]
for l in alone:
    l.z = l.value
alone[5].chain_ids, alone[5].kind = [ex[0]], "contour"
spots = []
got = E.screened_heights_as_points(spots, labs + alone, fams, cts, boxes=[])
check([(s.label_id, s.z, s.side, s.x, s.y) for s in got] == [(900, 653.0, "existing", 450.0, 900.0)] and spots == got,
      f"one screened whole number on no line, in the existing range, read with confidence: a point of the existing ground "
      f"({[(s.label_id, s.z) for s in got]})")
check("existing ground where it is printed" in got[0].side_reason and got[0].anchor == "text", "and it says what it is")
check(E.screened_heights_as_points([], labs + [alone[0]], fams, cts, boxes=[[400, 850, 500, 950]]) == [], "not inside a legend or a table")
for l in labs:
    l.gray = 0.0
check(E.screened_heights_as_points([], labs + [alone[0]], fams, cts, boxes=[]) == [],
      "and only on a sheet that prints its existing heights screened")

# ---- 7. a leader ends at its point; a dash goes on -----------------------------------------------------------
spot = make_label(0, 0, "652.63", (1345.3, 282.2, 1366.9, 286.9), 0.0, "ocr", 96.0, 0.2)
spot.z = 652.63
lot_line = [stroke(0, [(1369.1, 320.8), (1369.1, 279.4)], width=0.48, layer="X|C-PROP-LINE"),      # the dash beside the text
            stroke(1, [(1369.1, 328.0), (1369.1, 324.4)], width=0.48, layer="X|C-PROP-LINE"),      # dot
            stroke(2, [(1369.1, 335.2), (1369.1, 331.6)], width=0.48, layer="X|C-PROP-LINE"),      # dot
            stroke(3, [(1369.1, 380.2), (1369.1, 338.8)], width=0.48, layer="X|C-PROP-LINE")]      # the next dash
sp = E.place_spots([spot], lot_line)[0]
check(sp.anchor == "text" and abs(sp.x - 1356.1) < 0.1 and abs(sp.y - 284.55) < 0.1,
      f"a lot-corner grade beside a dash-dot property line stands where it is printed, not 41 pt down the line: {sp.anchor} ({sp.x:.1f}, {sp.y:.1f})")
sp = E.place_spots([spot], lot_line[:1])[0]
check(sp.anchor == "leader" and abs(sp.y - 320.8) < 0.1, "the same stroke with nothing beyond its end is a leader, as before")
lead = [stroke(0, [(784.1, 1289.9), (777.0, 1304.0), (770.0, 1318.3)], width=0.72),
        stroke(1, [(764.3, 1324.6), (772.6, 1322.3)], width=0.72), stroke(2, [(767.3, 1319.3), (769.6, 1327.6)], width=0.72)]
s2 = make_label(0, 0, "617.28", (784.7, 1285.7, 815.4, 1295.7), 0.0, "text", 100.0, 0.0)
s2.z = 617.28
sp = E.place_spots([s2], lead)[0]
check(sp.anchor == "leader" and abs(sp.x - 770.0) < 0.1 and abs(sp.y - 1318.3) < 0.1,
      f"a leader with its cross at the end stays a leader (the cross lies across the end, not beyond it): ({sp.x:.1f}, {sp.y:.1f})")
# the arm of the cross that runs ON from the leader, a point beyond its end (Clearwater Creek 5 sheet 3.1, the
# strokes of "618.70" as the sheet draws them: 32 such grades stood at their text; found by the third critic)
lead2 = [stroke(0, [(807.4, 1268.4), (804.0, 1252.7), (809.4, 1252.7)], width=0.72),
         stroke(1, [(804.2, 1274.8), (812.5, 1272.6)], width=0.72), stroke(2, [(807.2, 1269.5), (809.5, 1277.9)], width=0.72)]
s3 = make_label(0, 0, "618.70", (810.0, 1248.5, 840.8, 1258.5), 0.0, "text", 100.0, 0.0)
s3.z = 618.70
sp = E.place_spots([s3], lead2)[0]
check(sp.anchor == "leader" and abs(sp.x - 807.4) < 0.1 and abs(sp.y - 1268.4) < 0.1,
      f"a leader whose cross has an arm running on from its end stays a leader: the arm is crossed, middle on middle ({sp.anchor}, {sp.x:.1f}, {sp.y:.1f})")
sp = E.place_spots([s3], lead2[:1] + lead2[2:])[0]
check(sp.anchor == "text", "the same arm with nothing across it is a next dash: the grade stands at its text")
crossing = lot_line + [stroke(9, [(1348.0, 359.5), (1390.0, 359.5)], width=0.48, layer="X|C-PROP-LINE")]
sp = E.place_spots([spot], lot_line[:1] + lot_line[3:] + [stroke(9, [(1365.0, 359.5), (1373.2, 359.5)], width=0.48, layer="X|C-PROP-LINE")])[0]
check(sp.anchor == "leader", "(a dash of 41 pt with an 8-pt stroke across its middle is no cross: the stroke across is not of its length - "
                             "and with no dot between, the next dash is 18 pt off, out of reach: a leader, as before)")
sp = E.place_spots([spot], crossing)[0]
check(sp.anchor == "text", "a lot line crossed by another line of its pen further on is still a dashed line passing the text")
other_pen = lot_line[:1] + [stroke(1, [(1369.1, 328.0), (1369.1, 324.4)], width=0.3, layer="X|C-PROP-LINE")]
check(E.place_spots([spot], other_pen)[0].anchor == "leader", "a stroke of another pen beyond the end is no next dash")
aside = lot_line[:1] + [stroke(1, [(1372.1, 328.0), (1372.1, 324.4)], width=0.48, layer="X|C-PROP-LINE")]
check(E.place_spots([spot], aside)[0].anchor == "leader", "nor one 3 pt off the line")

# ---- 9. two printed heights that disagree, one vote each, decide nothing -----------------------------------
def one_line_two_heights(first, second):
    """The third proposed line (663) drawn unbroken, its height printed on it twice: at x = 100
    and at x = 300 (the labels masked over the line, as the sheets print them)."""
    chs_, labs_, fams_, ex_, ix_ = sheet_of()
    chs_[:] = [c for c in chs_ if c.id not in (4, 5)] + [chain(4, 1, wavy(0, 600, 220))]
    labs_[:] = [l for l in labs_ if not (l.text == "663" and l.gray == 0.0)]

    def on_line(text, x):
        y = 220 + 5 * math.sin((x - 300) / 90 * 2 * math.pi)
        return make_label(0, 0, text, (x - 9, y - 4, x + 9, y + 4), 0.0, "glyph", 99.0, 0.0, len_along=18.0, height=8.0)
    a_, b_ = on_line(first, 100.0), on_line(second, 300.0)
    a_.id, b_.id = 900, 901
    labs_[:0] = [a_, b_]
    fams_ = E.contour_families(styles_for(chs_, PENS), page_role="grading")
    cts_ = E.build_contours(chs_, find_gaps(chs_), fams_, labs_)
    return chs_, labs_, fams_, cts_, next(ct for ct in cts_ if 4 in ct.chain_ids)


chs, labs, fams, cts, ln = one_line_two_heights("665", "663")            # the wrong one read FIRST
check(ln.z is None and ln.how == "" and sorted(c["z"] for c in ln.candidates) == [663.0, 665.0]
      and ln.z_note == "its printed heights disagree (665 / 663): counted from the neighbours",
      f"a line printed 665 once and 663 once takes neither from its labels: {ln.z} {ln.candidates} ({ln.z_note!r})")
E.infer_intervals(cts, chs, fams, labs)
E.propagate_heights(cts, chs, fams, labels=labs)
check(ln.z == 663.0 and ln.how == "counted", f"its neighbours (662 above, 664 below) give it 663: {ln.z} {ln.how}")
chs, labs, fams, cts, ln = one_line_two_heights("663", "665")            # and the other way round
check(ln.z is None and sorted(c["z"] for c in ln.candidates) == [663.0, 665.0], "whichever was read first: the order of arrival decides nothing")
chs, labs, fams, cts, ln = one_line_two_heights("663", "663")
check(ln.z == 663.0 and ln.how == "label" and ln.votes == 2, "two printed heights that agree are the line's height, as before")

# ---- 8. a short number printed to the thousandth is no spot grade ---------------------------------------------
from cutfill.labels import resolve_values  # noqa: E402

grades = [num(k, f"{v:.2f}", 100 + 40 * k, 300, gray=0.0) for k, v in enumerate((652.12, 655.4, 658.95, 660.0, 661.5, 663.25, 664.1))]
acres, short, tagged = num(50, "55.967", 500, 300, gray=0.5), num(51, "55.96", 540, 300, gray=0.0), num(52, "FG 55.125", 580, 300, gray=0.0)
resolve_values(grades + [acres, short, tagged])
check(acres.z is None and acres.z_note == "a short number printed to the thousandth: not a spot grade",
      f"'55.967' (the acres of a tract) on a sheet of 652 .. 664 is no 655.967: {acres.z} ({acres.z_note!r})")
check(short.z == 655.96 and tagged.z == 655.125, f"a short grade to the hundredth, and one with its tag, are still resolved: {short.z}, {tagged.z}")

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILED'}")
sys.exit(1 if fails else 0)
