"""LINE DETECTION CLEAN-UP (2026-09-26, PLAN.md section 10 / audit D20-D26, D37).

Synthetic cases with known answers for every rule the clean-up added:
  * PDF markups are hidden while the page is walked, and put back (D21)
  * a clip PATH that is not its scissor box clips like the renderer (D26)
  * a native dash-dot array is dash2, a zero-gap array solid (D25)
  * a hairpin stroke and a crowded long twin collapse to one stroke;
    long collinear overlaps merge (D20)
  * coincident chains fold: copies, interleaved halves of one line - and
    two neighbouring lines never do (D20, SURVIVORSHIP #24 / #25)
  * a stitch never crosses a pen change or a layer change (D23, #22)
  * planting-layer chains are symbols, not LF (#21)
  * a repeating text-span code in a dashed line's gaps makes it dash-text;
    the same code beside the line does not (D25)

    python tests/test_line_cleanup.py     (fails on the commit before it)
"""
import pathlib
import sys

import numpy as np
import pymupdf

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from takeoff_engine import chain as CH  # noqa: E402
from takeoff_engine import extract as EX  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


def unit(pts, color=(0.0, 0.0, 0.0), width=0.8, layer="", dashes="[] 0", closed=False):
    return EX.StrokeUnit(pts=np.asarray(pts, dtype=np.float64), color=color, width=width,
                         dashes=dashes, closed=closed, layer=layer)


def dashed_chain(cid, x0, y, n, dash=14.0, gap=12.0, phase=0.0, color=(0.0, 0.0, 0.0),
                 width=0.8, layer="L", klass="dash1"):
    """A dashed chain of n marks along +x: pts = mark endpoints, seg_counts 2 each."""
    pts, sc = [], []
    x = x0 + phase
    for _ in range(n):
        pts.append([x, y])
        pts.append([x + dash, y])
        sc.append(2)
        x += dash + gap
    p = np.asarray(pts, dtype=np.float64)
    length = float(p[-1, 0] - p[0, 0])
    c = CH.LineChain(cid, p, klass, color, width, length, n, f"d{dash:.1f} g{gap:.1f}",
                     layer=layer, ink=float(n * dash))
    c.seg_counts = sc
    return c


# ---- 1. markups hidden during extraction, restored after (D21) -------------------------------------
doc = pymupdf.open()
pg = doc.new_page(width=400, height=300)
pg.draw_line((20, 150), (380, 150), color=(0, 0, 0), width=1.0)
an = pg.add_polyline_annot([(30, 40), (200, 60), (370, 40)])
an.set_border(width=3)
an.set_colors(stroke=(1, 0, 0))
an.update()
n_with = len(pg.get_drawings())
units = EX.extract_strokes(pg)
check(EX.page_annotation_count(pg) == 1, "the page carries one markup")
check(n_with >= 2, f"get_drawings sees the markup's ink ({n_with} paths)")
check(len(units) == 1 and abs(units[0].length - 360.0) < 0.5,
      f"extract_strokes returns the drawn line only ({len(units)} units, {units[0].length if units else 0:.1f} pt)")
check(not (int(an.flags) & 2), "the markup is visible again afterwards (HIDDEN flag restored)")
words = EX.page_words(pg)
check(isinstance(words, list), "page_words runs with the markup hidden")

# ---- 2. the clip polygon, not the scissor box (D26) ---------------------------------------------------
P = pymupdf.Point
hexa = [(100, 0), (300, 0), (300, 200), (200, 300), (100, 300)]  # box 100..300 x 0..300 with the
items = [("l", P(*hexa[i]), P(*hexa[(i + 1) % len(hexa)])) for i in range(len(hexa))]  # SE corner cut
loops = EX._clip_loops(items, pymupdf.Rect(100, 0, 300, 300))
check(loops is not None and len(loops) == 1 and len(loops[0].pts) == 5,
      "a five-sided clip path is kept as a polygon")
box_items = [("re", pymupdf.Rect(100, 0, 300, 300))]
check(EX._clip_loops(box_items, pymupdf.Rect(100, 0, 300, 300)) is None,
      "a clip path that IS its scissor box needs no polygon")
rect = pymupdf.Rect(100, 0, 300, 300)
run_in = EX._clip_runs([(150, 100), (250, 100)], rect, [loops])
run_cut = EX._clip_runs([(280, 280), (290, 290)], rect, [loops])   # inside the box, in the cut corner
run_mix = EX._clip_runs([(150, 250), (290, 290), (150, 100)], rect, [loops])
check(run_in == [[(150, 100), (250, 100)]], "a stroke inside the polygon is kept whole")
check(run_cut == [], "a stroke inside the box but outside the polygon is dropped")
check(len(run_mix) == 2, "a stroke crossing the cut corner is split there")

# ---- 3. native dash arrays (D25) --------------------------------------------------------------------
check(CH._classify_native("[12.924 0.924 0 0.924] 0")[0] == "dash2", "dash-dot native array -> dash2")
check(CH._classify_native("[11.869 0 0 0] 0")[0] == "solid", "zero-gap native array -> solid")
check(CH._classify_native("[6 3] 0")[0] == "dash1", "plain native array -> dash1")
check(CH._classify_native("[6 3 1 3] 0")[0] == "dash2", "dash, dot native array -> dash2")
check(CH._classify_native("[3] 0")[0] == "dash1", "odd-length native array repeats -> dash1")
check(CH._classify_native("[] 0")[0] == "solid", "empty array -> solid")

# ---- 4. twins: hairpin, crowded long twin, long collinear overlap (D20) ------------------------------
u = unit([(0, 0), (200, 0), (200, 1.0), (0, 1.0)])
out = CH._merge_twin_strokes([u])
check(len(out) == 1 and abs(out[0].length - 200.0) < 1.0,
      f"a hairpin stroke collapses to one arm ({len(out)} units, {out[0].length:.1f} pt)")
trip = [unit([(0, 0), (400, 0)]), unit([(0, 0.5), (400, 0.5)]), unit([(0, 1.0), (400, 1.0)])]
dots = [unit([(x, 0.2), (x + 1.0, 0.2)]) for x in np.arange(0, 400, 0.8)]
out = CH._merge_twin_strokes(trip + dots)
longs = [v for v in out if v.length > 100]
check(len(longs) == 1, f"three copies of a long stroke among a dot crowd collapse to one ({len(longs)})")
out = CH._merge_collinear_overlaps([unit([(0, 0), (400, 0)]), unit([(200, 0.3), (700, 0.3)])])
check(len(out) == 1 and abs(out[0].length - 700.0) < 1.0,
      f"two long collinear overlapping strokes merge into their union ({len(out)}, {out[0].length:.1f} pt)")
out = CH._merge_collinear_overlaps([unit([(0, 0), (400, 0)]), unit([(200, 4.0), (700, 4.0)])])
check(len(out) == 2, "two long parallel strokes 4 pt apart stay two")

# ---- 5. coincident chains (D20, #24, #25) -----------------------------------------------------------
A = dashed_chain(1, 0, 0, 20)
B = dashed_chain(2, 0, 0.3, 20)                       # a copy 0.3 pt aside
out = CH._merge_coincident_chains([A, B])
check(len(out) == 1 and abs(out[0].length - A.length) < 0.5, "a copied chain folds into one")
A = dashed_chain(1, 0, 0, 20)
Bi = dashed_chain(2, 0, 0.4, 19, dash=12.0, gap=14.0, phase=14.0)   # marks in A's gaps, abutting
out = CH._merge_coincident_chains([A, Bi])
check(len(out) == 1, f"interleaved halves of one line fold into one ({len(out)})")
A = dashed_chain(1, 0, 0, 20)
N1 = dashed_chain(2, 0, 3.0, 20)                     # in-phase neighbour 3 pt away
out = CH._merge_coincident_chains([A, N1])
check(len(out) == 2, "an in-phase neighbouring line 3 pt away stays separate")
A = dashed_chain(1, 0, 0, 20)
N2 = dashed_chain(2, 0, 3.0, 19, dash=12.0, gap=14.0, phase=14.0)  # out-of-phase neighbour 3 pt away
out = CH._merge_coincident_chains([A, N2])
check(len(out) == 2, "an out-of-phase neighbouring line 3 pt away stays separate")
A = dashed_chain(1, 0, 0, 20, layer="x1|LOT", color=(0.0, 0.0, 0.0), width=2.2)
S = CH.LineChain(3, np.array([[0.0, 0.1], [A.pts[-1, 0], 0.1]]), "solid", (0.78, 0.78, 0.78), 0.7,
                 float(A.pts[-1, 0]), 1, "solid", layer="x2|LOT")
out = CH._merge_coincident_chains([A, S])
check(len(out) == 1 and out[0].klass == "dash1", "two xrefs drawing one LOT leaf keep the dashed one")
S2 = CH.LineChain(3, np.array([[0.0, 0.1], [A.pts[-1, 0], 0.1]]), "solid", (0.78, 0.78, 0.78), 0.7,
                  float(A.pts[-1, 0]), 1, "solid", layer="x2|EASEMENT")
out = CH._merge_coincident_chains([dashed_chain(1, 0, 0, 20, layer="x1|LOT", width=2.2), S2])
check(len(out) == 2, "a different layer name over the same geometry is two features")

# ---- 6. stitches never cross a pen or a layer change (D23, #22) -------------------------------------
def solid(cid, x0, x1, color, layer=""):
    return CH.LineChain(cid, np.array([[x0, 50.0], [x1, 50.0]]), "solid", color, 0.7,
                        float(x1 - x0), 1, "solid", layer=layer)
out = CH._stitch_across_styles([solid(1, 0, 130, (0.0, 0.0, 1.0)), solid(2, 131, 240, (0.0, 1.0, 1.0))])
check(len(out) == 2, "blue and cyan fences meeting end to end stay two chains")
out = CH._stitch_across_styles([solid(1, 0, 130, (0.0, 0.0, 0.0)), solid(2, 131, 240, (0.5, 0.5, 0.5))])
check(len(out) == 1, "a black line continuing as its screened grey half stitches")
out = CH._stitch_across_styles([solid(1, 0, 130, (0.0, 0.0, 1.0)), solid(2, 131, 240, (0.5, 0.5, 1.0))])
check(len(out) == 1, "a blue line continuing as a faded blue stitches")
out = CH._stitch_across_styles([solid(1, 0, 130, (0.0, 0.0, 0.0), "A|C-TOPO"), solid(2, 131, 240, (0.0, 0.0, 0.0), "A|BG-L-Conc")])
check(len(out) == 2, "a stitch never crosses a CAD layer change")
check(CH._pen_kin((0, 0, 1), (0, 1, 1)) is False and CH._pen_kin((0, 0, 0), (0.6, 0.6, 0.6)) is True,
      "_pen_kin: hue change no, shade change yes")

# ---- 7. planting layers are symbols (#21) -----------------------------------------------------------
tree = unit([(0, 0), (300, 0), (300, 200), (0, 200), (0, 0)], layer="H-Section|BG-L-PLNT-TREE-STEM", width=0.4)
chains = CH.build_chains([tree])
check(chains and all(c.klass == "annotation" and c.dash_signature == "plant symbol" for c in chains),
      "a chain on a PLNT layer is a plant symbol, not linework")
wall = unit([(0, 0), (300, 0), (300, 200), (0, 200), (0, 0)], layer="H-Section|BG-L-Boulders", width=0.4)
chains = CH.build_chains([wall])
check(chains and all(c.klass != "annotation" for c in chains), "a boulder-wall layer keeps its linework")

# ---- 8. dash-text from text-span codes in the gaps (D25) ---------------------------------------------
c = dashed_chain(1, 0, 100, 12, dash=36.0, gap=30.0)
gap_words = []
for k in range(0, 12, 2):
    gx = 36.0 + k * 66.0 + 15.0
    gap_words.append((gx - 6, 96.0, gx + 6, 104.0, "SS"))
CH._promote_gap_text_chains([c], gap_words)
check(c.klass == "dash-text" and c.label_texts.count("SS") >= 3,
      f"a repeating 'SS' in the gaps makes the line dash-text ({c.klass})")
c2 = dashed_chain(2, 0, 100, 12, dash=36.0, gap=30.0)
side_words = [(x - 6, 116.0, x + 6, 124.0, "FTG") for x in (60.0, 200.0, 340.0, 480.0)]
CH._promote_gap_text_chains([c2], side_words)
check(c2.klass == "dash1", "the same code printed 20 pt beside the line leaves it alone")
c3 = dashed_chain(3, 0, 100, 12, dash=36.0, gap=30.0)
CH._promote_gap_text_chains([c3], gap_words[:2])
check(c3.klass == "dash1", "two occurrences are not a cadence")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all line clean-up checks passed")
