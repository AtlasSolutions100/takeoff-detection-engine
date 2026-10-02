"""A HEIGHT IN THE GAP OF ITS LINE, AND A GRADE THAT IS NO CONTOUR LABEL (2026-09-27, Home 2 Suites
C3: the street's paving hatch - 21.6-pt strokes of a pen that draws no ground - runs under the
existing contours' printed heights and under the street's spot grades):

1. a printed height with line ends on both sides is in the gap of THE LINE WHOSE TWO ENDS FACE
   EACH OTHER across it, in one pen - before the two nearest ends, whatever strokes they are of;
2. where no such line is there, the two nearest ends, as before;
3. a pen whose "labels" are spot grades printed to the hundredth is set aside, and its grades are
   spot grades again (they were left "contour labels" of lines that are no ground: in no surface).

    python tests/test_cutfill_label_gap.py
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
from cutfill.labels import make_label, resolve_values  # noqa: E402

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
        5: (0.3, 0.25, False, ""),      # a paving hatch: dark gray hairline strokes, 21.6 pt each
        6: (0.7, 1.6, True, "")}        # the existing index contours: screened, dashed, heavy


def sheet(hatch_at=(), grades=(), plain_hatch=14):
    """Six proposed and six existing lines, each two pieces with its height printed in the gap
    between them (x = 300). `hatch_at`: hatch strokes as (x0, y0, x1, y1). `grades`: spot grades
    (text, x, y), each printed between the ends of two hatch strokes."""
    chs, labs, lines = [], [], {}

    def line(sid, y, text, gray):
        k = len(chs)
        chs.append(chain(k, sid, wavy(0, 280, y)))
        chs.append(chain(k + 1, sid, wavy(320, 600, y)))
        labs.append(make_label(len(labs), 0, text, (291, y - 4, 309, y + 4), 0.0, "glyph", 95.0, gray, len_along=18.0, height=8.0))
        lines[text] = (k, k + 1)
    for i in range(6):
        line(1, 100 + 60 * i, str(661 + i), 0.0)
        line(2, 130 + 60 * i, str(651 + i), 0.7)
    hatch = []
    for x0, y0, x1, y1 in hatch_at:
        hatch.append(len(chs))
        chs.append(chain(len(chs), 5, [(x0, y0), (x1, y1)]))
    for k in range(plain_hatch):                         # the hatch away from every label
        chs.append(chain(len(chs), 5, [(40.0 + 30 * k, 900.0), (61.0 + 30 * k, 905.0)]))
    held = []
    for text, x, y in grades:
        a = len(chs)
        chs.append(chain(a, 5, [(x - 33.0, y - 4.0), (x - 12.0, y)]))
        chs.append(chain(a + 1, 5, [(x + 12.0, y), (x + 33.0, y + 4.0)]))
        held.append((a, a + 1))
        labs.append(make_label(len(labs), 0, text, (x - 9, y - 4, x + 9, y + 4), 0.0, "glyph", 95.0, 0.0, len_along=18.0, height=8.0))
    fams = E.contour_families(styles_for(chs, PENS), page_role="grading")
    return chs, labs, fams, lines, hatch, held


# ---- 1. the two ends of one line, before the two nearest ends ----------------------------------------------------
y = 130.0                                                # the existing 651: its ends stand 11 pt off the text
chs, labs, fams, lines, hatch, _ = sheet(hatch_at=[(312.0, y + 1.0, 333.0, y + 6.0)])
check(any(f.style == 5 for f in fams), "the hatch pen is a pen among the sheet's pens (nothing names it)")
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
lab = next(l for l in labs if l.text == "651")
check(sorted(lab.chain_ids) == sorted(lines["651"]),
      f"a hatch stroke ends 3 pt off the height, nearer than the line's own end: the height is in the gap of its line {sorted(lab.chain_ids)} "
      f"(the line's pieces {lines['651']}, the hatch stroke {hatch})")
by = {cid: ct for ct in cts for cid in ct.chain_ids}
check(by[lines["651"][0]] is by[lines["651"][1]] and by[lines["651"][0]].z == 651.0, "the two pieces are one line at 651")
check(by[hatch[0]].z is None and by[hatch[0]] is not by[lines["651"][0]], "and the hatch stroke is no part of it")

# two strokes of the hatch, one ending nearer than the line's end on each side, each running on past
# the text (their ends point away from each other): the two nearest ends, and no line
chs, labs, fams, lines, hatch, _ = sheet(hatch_at=[(297.0, y + 3.0, 318.0, y + 6.0), (303.0, y - 3.0, 282.0, y - 6.0)])
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
lab = next(l for l in labs if l.text == "651")
check(sorted(lab.chain_ids) == sorted(lines["651"]),
      f"a hatch stroke nearer on BOTH sides (they do not face each other across the text): still its line {sorted(lab.chain_ids)}")
# two strokes of the hatch that DO face each other across the text, nearer than the line's ends (the
# street's hatch under the "5988"): a line of a pen that draws a surface comes first
hatch2 = [(312.0, y + 1.0, 333.0, y + 6.0), (288.0, y - 1.0, 267.0, y - 6.0)]
chs, labs, fams, lines, hatch, _ = sheet(hatch_at=hatch2)
f5 = next(f for f in fams if f.style == 5)
f5.role, f5.role_reason = "ignore", "not in the sheet's legend, which shows a pen for every contour row"
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
lab = next(l for l in labs if l.text == "651")
check(sorted(lab.chain_ids) == sorted(lines["651"]),
      f"two strokes of a pen that draws no ground, facing each other across the text and nearer than the line's ends: still its line {sorted(lab.chain_ids)}")
chs, labs, fams, lines, hatch, _ = sheet(hatch_at=hatch2)
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
lab = next(l for l in labs if l.text == "651")
check(sorted(lab.chain_ids) != sorted(lines["651"]),
      f"(what the rule cannot see: were that pen a surface's, the nearer of the two lines would hold the height, as before: "
      f"{sorted(lab.chain_ids)}, {lab.z_note!r})")

# a stroke that runs under the text has its ends either side of it, pointing away: no line's gap
chs, labs, fams, lines, hatch, _ = sheet(hatch_at=[(290.0, y - 2.0, 311.5, y + 2.0)])
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
lab = next(l for l in labs if l.text == "651")
check(sorted(lab.chain_ids) == sorted(lines["651"]), f"a stroke lying under the text: the height is its line's {sorted(lab.chain_ids)}")
check(all(sorted(next(l for l in labs if l.text == t).chain_ids) == sorted(ids) for t, ids in lines.items()),
      "and every other height of the sheet is on its own line, as before")

# ---- 2. where no line's two ends face each other across the text: the two nearest ends, as before -----------------
chs, labs, fams, lines, hatch, _ = sheet()
left, right = lines["651"]
old = next(c for c in chs if c.id == right)
chs.remove(old)
chs.append(chain(right, 6, [(float(px), float(py)) for px, py in old.pts]))     # the far piece drawn in the heavy pen
for k, v in enumerate((655, 660, 665)):                                          # (the heavy pen's own lines and heights)
    chs.append(chain(len(chs), 6, wavy(0, 280, 700.0 + 60 * k)))
    chs.append(chain(len(chs), 6, wavy(320, 600, 700.0 + 60 * k)))
    labs.append(make_label(len(labs), 0, str(v), (291, 696.0 + 60 * k, 309, 704.0 + 60 * k), 0.0, "glyph", 95.0, 0.7, len_along=18.0, height=8.0))
fams = E.contour_families(styles_for(chs, PENS), page_role="grading")
check(any(f.style == 6 for f in fams) and not any(6 in f.styles and 2 in f.styles for f in fams), "the heavy pen is a pen of its own")
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
lab = next(l for l in labs if l.text == "651")
check(sorted(lab.chain_ids) == sorted([left, right]), f"a line that changes its pen at the height: the two nearest ends {sorted(lab.chain_ids)}")

# ---- 3. a pen whose labels are spot grades: set aside, and the grades are spot grades again -------------------------
grades = [("84.94", 100.0, 1000.0), ("85.97", 100.0, 1060.0), ("86.44", 100.0, 1120.0), ("87.33", 100.0, 1180.0)]


def left_out(fams_):
    """The hatch pen as the sheet's legend leaves it (elevations.leave_out_unlisted_pens): no surface."""
    f = next(f for f in fams_ if f.style == 5)
    f.role, f.role_reason = "ignore", "not in the sheet's legend, which shows a pen for every contour row"
    return f


chs, labs, fams, lines, hatch, held = sheet(grades=grades)
f5 = left_out(fams)
check(next(l for l in labs if l.text == "84.94").kind == "spot", "as printed, '84.94' is a spot grade")
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
g = [l for l in labs if "." in l.text]
check(all(l.kind == "contour" and sorted(l.chain_ids) == sorted(h) for l, h in zip(g, held)),
      f"a grade between two stroke ends of the hatch is taken for its label: {[(l.text, l.kind, l.chain_ids) for l in g]}")
E.qualify_families(fams, cts, labels=labs)
check(f5.role == "ignore" and "spot grades" in f5.role_reason, f"the pen is set aside: {f5.role_reason}")
check(all(l.kind == "spot" and l.chain_ids == [] for l in g), f"and its grades are spot grades again: {[(l.text, l.kind, l.chain_ids) for l in g]}")
by = {cid: ct for ct in cts for cid in ct.chain_ids}
check(all(by[a].z is None and by[a].label_ids == [] and by[a].how == "" for a, _ in held), "the strokes they stood between carry no height")
check(all(next(l for l in labs if l.text == t).kind == "contour" for t in lines), "the contours' own heights stay contour labels")
resolve_values(labs, None, (651.0, 666.0))
spots = E.place_spots(labs, [Stroke(pts=c.pts, color=(0.0, 0.0, 0.0), width=0.25, layer="", closed=False, dash=False, opacity=1.0,
                                    filled=False, idx=c.id) for c in chs if c.style == 5])
got = sorted(round(s.z, 2) for s in spots)
check(got == [684.94, 685.97, 686.44, 687.33], f"each is a point of the sheet, at the height it prints (short numbers add the sheet's hundreds): {got}")

chs, labs, fams, lines, hatch, held = sheet(grades=grades)
left_out(fams)
cts = E.build_contours(chs, find_gaps(chs), fams, labs)
pinned = next(l for l in labs if l.text == "84.94")
pinned.pinned = True
E.qualify_families(fams, cts, labels=labs)
check(pinned.kind == "contour" and sorted(pinned.chain_ids) == sorted(held[0]), "a label the operator attached to a line stays where he put it")
check(all(l.kind == "spot" for l in labs if "." in l.text and l is not pinned), "the others are spot grades again")

# ---- 4. a number taken off a line is what it was before: with decimals a spot grade, whole a number ----------------
chs, labs, fams, lines, hatch, held = sheet(grades=grades)        # the hatch pen a "proposed" pen on its colour:
cts = E.build_contours(chs, find_gaps(chs), fams, labs)          # its grades are finer than the surface's whole heights
g = [l for l in labs if "." in l.text]
check(all(l.kind == "spot" and l.chain_ids == [] and "spot grade beside the line" in l.z_note for l in g),
      f"a grade printed finer than its surface's heights is let go as a SPOT GRADE: {[(l.text, l.kind) for l in g][:2]} ({g[0].z_note!r})")
resolve_values(labs, None, (651.0, 666.0))
spots = E.place_spots(labs, [])
check(sorted(round(s.z, 2) for s in spots) == [684.94, 685.97, 686.44, 687.33],
      "and is a point of the sheet though it was read from strokes (it used to become a bare number: no point)")
w = make_label(99, 0, "664", (0, 0, 18, 8), 0.0, "glyph", 95.0, 0.0, len_along=18.0, height=8.0)
w.kind, w.chain_ids = "contour", [1]
E._let_go(w, "why")
check(w.kind == "number" and w.chain_ids == [] and w.z_note == "why", "a whole number taken off a line is a number, as before")

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILED'}")
sys.exit(1 if fails else 0)
