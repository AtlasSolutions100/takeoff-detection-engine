"""Filled AREA regions: the square-foot takeoff the engine could not do.

Concrete, patio, walking path, gravel parking, pool, water feature - an
estimator buys these by the square foot, never by the linear foot. A client
excavator's own estimate prices "Paths To Subgrade" by the square foot,
"Slab Prep" by the square foot, "Gravel Parking area" in SF (client
document, not an industry prior). Before this module the engine could only
offer a perimeter, so a filled hardscape region reported as "20 LF".

WHY THIS DOES ITS OWN get_drawings() WALK - DO NOT "TIDY" IT INTO extract.py
----------------------------------------------------------------------------
extract.py is the most load-bearing file in the repo: its output feeds the
byte-identical vector gate (`python -m takeoff_engine.regress`). Fills are
deliberately read here, in a second independent pass, so that the stroke
path is not edited at all and the gate is protected BY CONSTRUCTION rather
than by testing. The duplicated ~40 lines of path walking are the price and
they are worth it. `extract.fill_boxes()` already sets this precedent.

WHAT IT DOES AND DOES NOT SEE (measured, Res-A civil engine page index 1)
----------------------------------------------------------------------------
SOLID fills only - PDF path types 'f' and 'fs'. That is slice 1.

A hatched region (stipple concrete, squiggle patio, x-mark walking path) is
NOT a fill: it is thousands of tiny strokes over a boundary polyline kept on
a separate layer (`BG-Hatch Conc` + `BG-L-Conc`, 14 such pairs on that
sheet; 87.4% of the 41,866 Hatch Conc paths fall inside a BG-L-Conc ring).
Recovering those needs ring-closing over chains and is slice 2 - deliberately
not attempted here, because today's closed chains on those layers include a
spurious 11,863 SF mega-ring where a linear metal edger closed on itself.

A pure 'f' path never becomes a StrokeUnit at all (`extract_strokes` skips
anything without 's'), which is why these regions were previously invisible
end to end: not a unit, not a chain, not a style, not a takeoff row.

NAMING - a region's identity is TEXT, never geometry
----------------------------------------------------------------------------
Every legend swatch on that sheet is drawn on ONE CAD layer, because in
paper space the layer means nothing. Three signals, in confidence order:

  1. TAG   - a "<name>  N sf" annotation sitting inside the polygon. This is
             the drafter's own name AND his own area, so it doubles as a
             self-check on our arithmetic and on the page scale.
  2. LEGEND- the region's fill colour equals a legend swatch's fill colour;
             the swatch's adjacent text is the class name.
  3. LAYER - the CAD layer name tail, as the style list already does.

A legend class with NO matching region is reported in `unmatched_legend`
with no quantity at all - never a zero, never a guess. On Res-A civil p1 the
legend is a DOCUMENT-WIDE key: PROPOSED WATER FEATURE and EXISTING MAPPED
WETLANDS are named there but drawn nowhere on the sheet.

SCALE PROVENANCE
----------------------------------------------------------------------------
Every area carries the scale it was computed with, and `scale.tag_check`
reports what the sheet's own printed areas imply. On Res-A civil p1 the
detected scale is 5.167 ft/inch while 13 printed tags all imply 20.00
ft/inch - a 15.0x area error the reader must surface, not hide. Areas are
also emitted in raw pt so a consumer can rescale without re-detecting.
"""

import math
import re
from collections import defaultdict

import numpy as np
import pymupdf

# A fill lighter than this on every channel is a knockout - the white box a
# drafter puts behind text so linework does not run through it. Same
# threshold extract.fill_boxes() already uses, for the same reason.
WHITE_MIN = 0.92

# Smallest region worth a takeoff row, in PAPER area. Deliberately NOT
# scale-derived: this sheet's detected scale is currently 15x wrong, and a
# scale-derived floor would then either hide real regions (too big) or flood
# the list with symbol confetti (too small). A paper floor cannot do either.
# 324 pt2 is 25 SF at 1"=20', 6.3 SF at 1"=10', 100 SF at 1"=40' - all inside
# "is this worth its own takeoff row", and far above the confetti (Res-A civil
# p1 carries 4,992 tree-stem fills averaging 0.33 pt2 each).
MIN_AREA_PT2 = 324.0

# A legend swatch is a short wide bar with its label immediately to the
# right. Measured on Res-A civil p1: every swatch is 121.0 x 15.1 pt and its
# text starts 9.3 pt past the bar's right edge.
#
# Shape alone is NOT enough - measured: it also matched 36 building floor-
# hatch bands on that sheet, which both invented six nonsense legend classes
# ("14'-8", "3/4\"") and ate those bands out of the region pool. A legend is
# a COLUMN: real swatches stack at one x with one width. Requiring a run of
# SWATCH_MIN_STACK of them killed all 36 false positives and kept all 7 real
# ones.
SWATCH_W = (40.0, 260.0)
SWATCH_H = (5.0, 30.0)
SWATCH_ASPECT = 4.0
SWATCH_TEXT_GAP = 34.0
SWATCH_MIN_STACK = 3

# Sheet furniture by layer name: title blocks, text masks, borders. These
# still get reported (so nothing is silently dropped) but default OFF in the
# review UI, the same treatment texture styles already get.
FURNITURE_RE = re.compile(r"(?i)ANNO|TTLB|TITLE|\bTXT\b|TEXT|BORDER|LOGO")

# Payload guard: pages of dense symbol fills should not ship a huge polygon
# list. Regions are emitted largest-first.
MAX_REGIONS = 400

# Printed-tag agreement window. The 13 tags on Res-A civil p1 agree with
# shoelace to ratio 1.000, so anything past a couple of percent is a real
# disagreement (wrong scale, or the tag belongs to a different region).
TAG_TOL_PCT = 2.0


def _bezier(p0, p1, p2, p3, n=8) -> list:
    out = []
    for i in range(1, n + 1):
        t = i / n
        m = 1 - t
        out.append((m**3 * p0.x + 3 * m * m * t * p1.x
                    + 3 * m * t * t * p2.x + t**3 * p3.x,
                    m**3 * p0.y + 3 * m * m * t * p1.y
                    + 3 * m * t * t * p2.y + t**3 * p3.y))
    return out


def _rings(path) -> list:
    """Sub-paths of one drawing path, as point lists. Mirrors the item walk
    in extract._emit; kept local on purpose (see module docstring)."""
    out, cur = [], []
    for it in path["items"]:
        op = it[0]
        if op == "l":
            a, b = it[1], it[2]
            seg = [(a.x, a.y), (b.x, b.y)]
        elif op == "c":
            a, b, c, d = it[1], it[2], it[3], it[4]
            seg = [(a.x, a.y)] + _bezier(a, b, c, d)
        elif op == "re":
            r = it[1]
            seg = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1),
                   (r.x0, r.y1), (r.x0, r.y0)]
        elif op == "qu":
            q = it[1]
            seg = [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y),
                   (q.ll.x, q.ll.y), (q.ul.x, q.ul.y)]
        else:
            continue
        if cur and abs(cur[-1][0] - seg[0][0]) < 0.05 \
                and abs(cur[-1][1] - seg[0][1]) < 0.05:
            cur.extend(seg[1:])
        else:
            if len(cur) >= 3:
                out.append(cur)
            cur = list(seg)
    if len(cur) >= 3:
        out.append(cur)
    return out


def _shoelace(ring) -> float:
    a = 0.0
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return abs(a) / 2.0


def _winding_edges(a, b, x: float, y: float) -> int:
    """Winding number around (x, y) of the edges a[i] -> b[i]."""
    cross = (b[:, 0] - a[:, 0]) * (y - a[:, 1]) - (x - a[:, 0]) * (b[:, 1] - a[:, 1])
    up = (a[:, 1] <= y) & (b[:, 1] > y) & (cross > 0)
    down = (a[:, 1] > y) & (b[:, 1] <= y) & (cross < 0)
    return int(up.sum() - down.sum())


# COST GUARD for _painted. Two exact methods, cheapest first, and a stated
# refusal past both - never an unbounded computation, never a silent guess.
#   nest  - every subpath is a simple ring and no two CROSS (they are
#           disjoint, touch, or one encloses the other). Exact by ring
#           arithmetic: no noding. Bounded by the number of bbox-overlapping
#           ring pairs it must classify.
#   node  - rings cross or self-intersect: node every edge into faces and
#           keep a face iff the fill rule paints it. Its cost follows the
#           number of edge contacts (touches + crossings: up to the SQUARE
#           of the edge count), so those are counted first and noding runs
#           only below NODE_MAX_HITS (and NODE_MAX_EDGES).
# Every pair count is taken in chunks and stops at its cap, so even the
# counting is bounded. Measured over the 6,665-page corpus and at each cap
# with adversarial synthetic paths (core-cutfill out/verify/2026-10-02/
# areas-half/COST.md). A refused fill is listed with its reason and NO
# number - never a partial or guessed figure.
NEST_MAX_RINGS = 20000
NEST_MAX_PAIRS = 200000
NODE_MAX_EDGES = 8000
NODE_MAX_HITS = 20000
_CHUNK = 256


def _pairs(tree, geoms, cap):
    """Unique (i, j), i < j, of geoms whose shapes intersect, or None once
    there are more than `cap` - counted chunk by chunk, so a pathological
    path costs at most one chunk past the cap, never the full square."""
    pi, pj, n = [], [], 0
    for s in range(0, len(geoms), _CHUNK):
        qi, qj = tree.query(geoms[s:s + _CHUNK], predicate="intersects")
        qi = qi + s
        m = qj > qi
        n += int(np.count_nonzero(m))
        if n > cap:
            return None
        pi.append(qi[m]); pj.append(qj[m])
    if not pi:
        return np.zeros(0, dtype=int), np.zeros(0, dtype=int)
    return np.concatenate(pi), np.concatenate(pj)


def _painted(rings, even_odd: bool):
    """The region a multi-subpath fill actually paints, by the PDF fill rule.

    A path is NOT "largest ring minus whatever sits inside it". Measured on
    Loves Lane C4.0 p0: every P1-BLDG-HATCH fill is ONE path of TWO
    triangles (a CAD SOLID quad split on its diagonal, opposite windings,
    nonzero rule). Keeping only the largest ring reported 11,625 SF for a
    23,248 SF building - the second triangle is neither the outer ring nor
    inside it, so it was dropped with no trace.

    Returns (area_pt2 or None, shapely geometry or None, method). A None
    area means REFUSED: method then says why, and the caller must show it.
    Zero-area / degenerate subpaths paint nothing and are dropped first."""
    import shapely
    from shapely.geometry import Polygon
    # a zero-area SIMPLE ring (a line drawn there and back) paints nothing;
    # a zero-shoelace self-crossing ring (an even bow-tie) paints both lobes
    keep_r = [r for r in rings if len(set(r)) >= 3
              and (_shoelace(r) > 1e-9 or not _simple_ring(r))]
    if not keep_r:
        return 0.0, None, "empty"
    if len(keep_r) > NEST_MAX_RINGS:
        return None, None, f"too complex: {len(keep_r):,} subpaths"
    polys = np.asarray([Polygon(r) for r in keep_r], dtype=object)
    simple = bool(np.all(shapely.is_valid(polys)))
    crossing = not simple
    pi = pj = np.zeros(0, dtype=int)
    i_in_j = j_in_i = None
    if simple:
        got = _pairs(shapely.STRtree(polys), polys, NEST_MAX_PAIRS)
        if got is None:
            return None, None, (f"too complex: over {NEST_MAX_PAIRS:,} "
                                f"overlapping subpath pairs")
        pi, pj = got
        if len(pi):
            A = polys
            disjoint = shapely.relate_pattern(A[pi], A[pj], "F********")
            i_in_j = shapely.covers(A[pj], A[pi])
            j_in_i = shapely.covers(A[pi], A[pj])
            crossing = bool(np.any(~disjoint & ~i_in_j & ~j_in_i))
    if not crossing:
        return _nest(keep_r, polys, pi, pj, i_in_j, j_in_i, even_odd)
    n_edges = sum(len(r) for r in keep_r)
    if n_edges > NODE_MAX_EDGES:
        return None, None, (f"too complex: {len(keep_r):,} crossing subpaths, "
                            f"{n_edges:,} edges")
    a = np.concatenate([np.asarray(r, dtype=float) for r in keep_r])
    b = np.concatenate([np.roll(np.asarray(r, dtype=float), -1, axis=0)
                        for r in keep_r])
    segs = shapely.linestrings(np.stack([a, b], axis=1))
    if _pairs(shapely.STRtree(segs), segs, NODE_MAX_HITS) is None:
        return None, None, (f"too complex: {len(keep_r):,} crossing subpaths, "
                            f"over {NODE_MAX_HITS:,} edge contacts")
    return _node(keep_r, even_odd)


def _nest(rings, polys, pi, pj, i_in_j, j_in_i, even_odd):
    """Exact painted area of simple, non-crossing rings: each ring's own
    region (itself minus its direct children) is painted iff the fill rule
    holds for the winding summed down its chain of enclosing rings.
    Identical twin rings nest by index, so even-odd cancels them and
    nonzero keeps one - which is what a renderer does."""
    from shapely.ops import unary_union
    n = len(rings)
    area = [_shoelace(r) for r in rings]
    sign = []
    for r in rings:
        s = 0.0
        for k in range(len(r)):
            x0, y0 = r[k]
            x1, y1 = r[(k + 1) % len(r)]
            s += x0 * y1 - x1 * y0
        sign.append(1 if s > 0 else -1)
    # rank: bigger first, then index - a container always ranks earlier
    rank = sorted(range(n), key=lambda k: (-area[k], k))
    pos = {k: t for t, k in enumerate(rank)}
    parent = [-1] * n
    if i_in_j is not None:
        for a, b, ab, ba in zip(pi.tolist(), pj.tolist(),
                                i_in_j.tolist(), j_in_i.tolist()):
            for child, cont in ((a, b), (b, a)):
                ok = ab if (child, cont) == (a, b) else ba
                if not ok or pos[cont] > pos[child]:
                    continue
                p = parent[child]
                if p < 0 or pos[cont] > pos[p]:
                    parent[child] = cont      # the nearest enclosing ring
    wind = [0] * n
    for k in rank:
        wind[k] = sign[k] + (wind[parent[k]] if parent[k] >= 0 else 0)
    kids = [[] for _ in range(n)]
    for k in range(n):
        if parent[k] >= 0:
            kids[parent[k]].append(k)
    total, pieces = 0.0, []
    for k in range(n):
        w = wind[k]
        if not ((w % 2 != 0) if even_odd else (w != 0)):
            continue
        own = area[k] - sum(area[c] for c in kids[k])
        if own <= 1e-9:
            continue
        total += own
        g = polys[k]
        if kids[k]:
            g = g.difference(unary_union([polys[c] for c in kids[k]]))
        pieces.append(g)
    if not pieces:
        return 0.0, None, "nest"
    return total, unary_union(pieces), "nest"


def _node(rings, even_odd):
    """Exact painted area of crossing / self-intersecting rings: node every
    edge into faces, keep a face iff the fill rule paints it. Overlapping
    subpaths are counted once by construction (a face is a face)."""
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union
    lines = [LineString(list(r) + [r[0]]) for r in rings]
    a = np.concatenate([np.asarray(r, dtype=float) for r in rings])
    b = np.concatenate([np.roll(np.asarray(r, dtype=float), -1, axis=0)
                        for r in rings])
    keep = []
    for f in polygonize(unary_union(lines)):
        p = f.representative_point()
        w = _winding_edges(a, b, p.x, p.y)
        if (w % 2 != 0) if even_odd else (w != 0):
            keep.append(f)
    if not keep:
        return 0.0, None, "node"
    u = unary_union(keep)
    return float(u.area), u, "node"


def _simple_ring(ring) -> bool:
    """True when the ring does not cross itself, so its shoelace IS the
    area it paints. A self-crossing ring (a bow-tie) cancels its lobes in
    the shoelace sum while a renderer paints both."""
    from shapely.geometry import Polygon
    return Polygon(ring).is_valid


def _ring_perimeter(ring) -> float:
    L = 0.0
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        L += math.hypot(x1 - x0, y1 - y0)
    return L


def _inside(ring, x: float, y: float) -> bool:
    """Crossing-number point-in-polygon."""
    ins = False
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            if x < (x1 - x0) * (y - y0) / (y1 - y0 + 1e-12) + x0:
                ins = not ins
    return ins


# A name made only of dimension-string debris ("UE 52'-2", "10''", "3/4\"")
# is a row-join artefact, not a name. Same failure family as the scale
# reader's "GRAPHIC SCALE 5'-2\"": words far apart on the sheet share a
# y-band. Require some real alphabetic content.
_NAMEY = re.compile(r"[A-Za-z]{3}")
# tokens that are measurements, elevations or leader abbreviations rather
# than part of a name
_DEBRIS = re.compile(r"""(?ix) ^(
      [\d'"./,\-]+            # 12'-7  3/4"  7727.2'  52'-2
    | INV:? | RE\.? | STBK | UE | WL | WS | TYP\.? | \(?TYP\)? )$""")


def _clean_name(text: str, max_words: int = 6) -> str:
    """Keep a tag/legend label only when it still reads as a NAME.

    Words are gathered by y-band, and on a CAD sheet two unrelated labels can
    interleave in x on one band ("CONC. PAVER PATIO;" + "RE. TO STRUCTURAL"
    came back merged on Res-A civil p1). Contiguity fixes the far-apart case
    but not the interleaved one, so anything still carrying measurement
    debris after this filter is reported as UNNAMED rather than as a wrong
    name - an unnamed row the reviewer types over is honest; a plausible
    wrong name is not.
    """
    toks = [t for t in text.split() if t and not _DEBRIS.match(t)]
    if not toks or len(toks) > max_words:
        return ""
    out = " ".join(toks).strip(" ;,:-")
    if len(out) < 4 or not _NAMEY.search(out) or ":" in out:
        return ""
    return out


def _contiguous_run(cands: list, x0: float, x1: float, max_gap: float) -> list:
    """The run of words that actually touch each other AND overlap [x0,x1].

    Words are collected by y-band, which on a CAD sheet joins text from
    opposite ends of the page. Keeping only the gap-connected run through
    the anchor's x-span is what turns that back into one label.
    """
    if not cands:
        return []
    cands = sorted(cands, key=lambda w: w[0])
    runs, cur = [], [cands[0]]
    for w in cands[1:]:
        if w[0] - cur[-1][2] <= max_gap:
            cur.append(w)
        else:
            runs.append(cur)
            cur = [w]
    runs.append(cur)
    for run in runs:
        if run[0][0] <= x1 and run[-1][2] >= x0:
            return run
    return []


_SF_SPAN = re.compile(r"(?i)^\s*(\d[\d,]*(?:\.\d+)?)\s*(?:sf|s\.f\.)\s*$")
# how far above its value the tag's name line may sit, in pt
TAG_NAME_RISE = 60.0


def _area_tags(page: pymupdf.Page, rot) -> list:
    """'<name>  1,234.56 sf' annotations: the drafter's own area for a region.

    Read from TEXT SPANS, not from the words list, and paired by span COLOUR.
    A span is atomic, so "3,086.86 sf" and "Flooring, Ceiling, Glazing" each
    arrive whole; the word-level route grouped by y-band instead and merged
    unrelated labels that happen to share a band ("CONC. PAVER PATIO;" came
    back fused with "RE. TO STRUCTURAL"). On Res-A civil p1 every area tag is
    colour 0x800080 against 0x000000 body text, so same-colour + directly
    above is an exact pairing.

    Returns [{value, x, y, name}] in DISPLAY space, matching the geometry.

    The VALUE is the trustworthy half: it is the drafter's own computation
    for exactly this polygon, so it cross-checks both our arithmetic and the
    page scale. The NAME may be a modelling-scheme label rather than a
    material ("Flooring, Ceiling, Glazing" is Revit's area scheme, not a
    finish), which is why it is only the first rung of the naming ladder.
    """
    def disp(bbox):
        r = pymupdf.Rect(bbox)
        if rot is not None:
            r = pymupdf.Rect(r) * rot
        r = pymupdf.Rect(r)
        r.normalize()
        return [r.x0, r.y0, r.x1, r.y1]

    spans = []
    try:
        d = page.get_text("dict")
    except Exception:
        return []
    for blk in d.get("blocks", []):
        for line in blk.get("lines", []):
            for sp in line.get("spans", []):
                t = (sp.get("text") or "").strip()
                if t:
                    spans.append({"t": t, "c": sp.get("color", 0),
                                  "b": disp(sp["bbox"])})
    out = []
    for s in spans:
        m = _SF_SPAN.match(s["t"])
        if not m:
            continue
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if val <= 0:
            continue
        b = s["b"]
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        w = max(b[2] - b[0], 1.0)
        best = None
        for o in spans:
            if o is s or o["c"] != s["c"] or _SF_SPAN.match(o["t"]):
                continue
            ob = o["b"]
            rise = cy - (ob[1] + ob[3]) / 2
            if not (0 < rise <= TAG_NAME_RISE):
                continue
            if min(b[2], ob[2]) - max(b[0], ob[0]) < 0.3 * w:
                continue      # not stacked over the value
            if best is None or rise < best[0]:
                best = (rise, o)
        name = _clean_name(best[1]["t"], max_words=8) if best else ""
        out.append({"value": val, "x": cx, "y": cy, "name": name})
    return out


def _swatch_columns(filled: list, outlines: list) -> tuple:
    """Split swatch-shaped FILLS into real legend swatches and ordinary bands.

    A legend stacks its swatches at one x with one width; an isolated bar of
    swatch-like proportions out in the drawing is floor hatch, a wall band or
    a table rule.

    The column evidence must include OUTLINE-only bars, not just filled ones:
    on Res-A civil p1 five of the seven legend rows are hatched rather than
    filled, so the filled swatches number two and would never reach a stack
    of three on their own. Counting the stroked bars finds all seven and
    confirms both filled ones.
    """
    bars = [(s["bbox"], s) for s in filled] + [(b, None) for b in outlines]
    bars.sort(key=lambda t: (t[0][0], t[0][2] - t[0][0]))
    cols, cur = [], []
    for bbox, s in bars:
        if cur:
            rx0, rx1 = cur[0][0][0], cur[0][0][2]
            if abs(bbox[0] - rx0) > 4.0 \
                    or abs((bbox[2] - bbox[0]) - (rx1 - rx0)) > 6.0:
                cols.append(cur)
                cur = []
        cur.append((bbox, s))
    if cur:
        cols.append(cur)
    keep, rejected = [], []
    for members in cols:
        tgt = keep if len(members) >= SWATCH_MIN_STACK else rejected
        tgt.extend(s for _, s in members if s is not None)
    return keep, rejected


def _legend_fill_classes(swatches: list, words: list) -> list:
    """A legend swatch's identity is the text immediately to its right."""
    if not words:
        return []
    out = []
    for s in swatches:
        x0, y0, x1, y1 = s["bbox"]
        cy = (y0 + y1) / 2
        near = [w for w in words
                if abs((w[1] + w[3]) / 2 - cy) <= (y1 - y0) * 0.75 + 3
                and x1 - 2 <= w[0] <= x1 + SWATCH_TEXT_GAP]
        if not near:
            continue
        anchor = min(near, key=lambda w: w[0])
        acy = (anchor[1] + anchor[3]) / 2
        row = [w for w in words
               if abs((w[1] + w[3]) / 2 - acy) <= 3.5
               and anchor[0] - 1 <= w[0] <= anchor[0] + 260]
        # gap-connected run only: the notes column shares this y-band and
        # appended "DRAINS." to "PROPOSED WATER FEATURE" without this
        run = _contiguous_run(row, anchor[0], anchor[2], max_gap=9.0)
        label = _clean_name(" ".join(str(w[4]).strip() for w in run),
                            max_words=8)
        if 3 <= len(label) <= 60:
            out.append({"label": label, "fill": s["fill"],
                        "layer": s["layer"], "bbox": s["bbox"]})
    return out


def page_areas(page: pymupdf.Page, scale: dict | None = None,
               words: list | None = None) -> dict:
    """Solid-filled regions on one page, with area in SF and a name.

    `scale` is the dict from scale_detect.detect_scale().to_dict(); `words`
    the rotation-mapped words from extract.page_words(). Both optional: with
    no scale the regions still come back, measured in pt, with SF null.
    """
    rot = page.rotation_matrix if page.rotation else None
    fpi = (scale or {}).get("ft_per_paper_inch")
    fpp = (fpi / 72.0) if fpi else None

    def to_display(pts):
        if rot is None:
            return pts
        return [((rot.a * x + rot.c * y + rot.e),
                 (rot.b * x + rot.d * y + rot.f)) for x, y in pts]

    def bar_shaped(w, h):
        return (h > 0 and SWATCH_W[0] <= w <= SWATCH_W[1]
                and SWATCH_H[0] <= h <= SWATCH_H[1] and w / h >= SWATCH_ASPECT)

    raw, swatches, outline_bars = [], [], []
    # a client's PDF markups (highlighter polygons, count squares) are fills
    # too and must never become measured regions - same rule as the stroke
    # extractor (extract.annots_hidden)
    from .extract import annots_hidden
    with annots_hidden(page):
        _drawings = page.get_drawings()
    for path in _drawings:
        ptype = path.get("type") or ""
        if "f" not in ptype:
            # stroke-only paths are not regions, but a bar-shaped one is
            # evidence of a legend COLUMN (see _swatch_columns). Test the
            # raw rect first - under /Rotate 90|270 w and h simply swap, so
            # the cheap check is orientation-free and spares 900k transforms.
            r0 = path.get("rect")
            if r0 is None or not ("s" in ptype):
                continue
            a, b = abs(r0.width), abs(r0.height)
            if not (bar_shaped(a, b) or bar_shaped(b, a)):
                continue
            rr = pymupdf.Rect(r0) * rot if rot is not None else pymupdf.Rect(r0)
            rr = pymupdf.Rect(rr)
            rr.normalize()
            outline_bars.append([rr.x0, rr.y0, rr.x1, rr.y1])
            continue
        fill = path.get("fill")
        if not fill or min(fill) > WHITE_MIN:
            continue           # knockout mask, not a region
        rings = [to_display(r) for r in _rings(path)]
        if not rings:
            continue
        areas = [_shoelace(r) for r in rings]
        k = int(np.argmax(areas))
        outer = rings[k]
        gross = areas[k]
        geom = None
        refused = None
        # A fill paints nothing outside its own path rect, so this bounds
        # the painted area from above whatever the rule or ring shape.
        r0 = path.get("rect")
        bound = abs(r0.width * r0.height) if r0 is not None else sum(areas)
        if bound < MIN_AREA_PT2 or (
                len(rings) == 1 and (len(rings[0]) <= 4
                                     or _simple_ring(rings[0]))):
            # one simple ring (shoelace is exact), or a path that cannot
            # reach the area floor whatever it paints: the original cheap
            # arithmetic, unchanged
            if gross <= 0:
                continue
            holes = sum(a for i, a in enumerate(areas)
                        if i != k and _inside(outer, *rings[i][0]))
            net = max(gross - holes, 0.0)
        else:
            net, geom, how = _painted(rings, bool(path.get("even_odd")))
            if net is None:
                refused = how
            elif net <= 0:
                continue
            if geom is not None:
                # the outline drawn and walked is the painted region's,
                # not one triangle of it
                big = max(getattr(geom, "geoms", [geom]),
                          key=lambda g: g.area)
                outer = [tuple(c) for c in big.exterior.coords[:-1]]
                gross = big.area
        xs = [p[0] for p in outer]
        ys = [p[1] for p in outer]
        bbox = ([float(v) for v in geom.bounds] if geom is not None
                else [min(xs), min(ys), max(xs), max(ys)])
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        rec = {"fill": [round(float(c), 3) for c in fill],
               "layer": path.get("layer") or "",
               "outer": outer, "net": net, "gross": gross,
               "perim": _ring_perimeter(outer), "bbox": bbox,
               "n_rings": len(rings), "geom": geom, "refused": refused,
               # order key: a refused fill ranks by its rect, the most it
               # could paint
               "rank": net if net is not None else bound}
        if refused:
            # measured nothing, so neither a swatch nor below the floor can
            # be decided - list it, with the reason, never a number
            raw.append(rec)
            continue
        # a legend swatch is furniture AND the naming key - hold it aside
        # before the area floor, since swatches are small. Whether it really
        # IS a swatch is decided by column structure below, not shape alone.
        if bar_shaped(w, h):
            swatches.append(rec)
            continue
        if net < MIN_AREA_PT2:
            continue
        raw.append(rec)

    # bars that failed the column test are ordinary geometry - put them back
    swatches, not_swatches = _swatch_columns(swatches, outline_bars)
    raw.extend(r for r in not_swatches if r["net"] >= MIN_AREA_PT2)

    legend_classes = _legend_fill_classes(swatches, words or [])
    by_colour = {}
    for lc in legend_classes:
        by_colour.setdefault(tuple(lc["fill"]), lc["label"])

    tags = _area_tags(page, rot)
    raw.sort(key=lambda r: -r["rank"])
    truncated = max(0, len(raw) - MAX_REGIONS)
    raw = raw[:MAX_REGIONS]

    from shapely.geometry import Point as _Point
    used_tags = set()
    regions = []
    for i, r in enumerate(raw):
        tag = None
        for ti, t in enumerate(tags):
            if ti in used_tags:
                continue
            bx = r["bbox"]
            if not (bx[0] <= t["x"] <= bx[2] and bx[1] <= t["y"] <= bx[3]):
                continue
            g = r.get("geom")
            if (g.contains(_Point(t["x"], t["y"])) if g is not None
                    else _inside(r["outer"], t["x"], t["y"])):
                tag = t
                used_tags.add(ti)
                break
        name, src = None, None
        if tag and tag["name"]:
            name, src = tag["name"], "tag"
        elif tuple(r["fill"]) in by_colour:
            name, src = by_colour[tuple(r["fill"])], "legend"
        elif r["layer"]:
            name, src = r["layer"].split("|")[-1], "layer"
        unmeasured = r.get("refused")
        area_sf = r["net"] * fpp * fpp if fpp and not unmeasured else None
        perim_lf = r["perim"] * fpp if fpp and not unmeasured else None
        delta = None
        check = None
        if tag and area_sf:
            delta = (area_sf - tag["value"]) / tag["value"] * 100.0
            check = "ok" if abs(delta) <= TAG_TOL_PCT else "mismatch"
        regions.append({
            "region_id": i,
            "fill": r["fill"],
            "layer": r["layer"],
            "name": name,
            "name_source": src,
            "area_pt2": None if unmeasured else round(r["net"], 1),
            "perim_pt": None if unmeasured else round(r["perim"], 1),
            # why there is no number: the fill is too complex to measure
            # within the cost guard (see _painted). Never a partial figure.
            "unmeasured": unmeasured,
            "area_sf": round(area_sf, 1) if area_sf is not None else None,
            "perim_lf": round(perim_lf, 1) if perim_lf is not None else None,
            "bbox": [round(v, 1) for v in r["bbox"]],
            "n_rings": r["n_rings"],
            "furniture": bool(FURNITURE_RE.search(r["layer"])),
            "printed_sf": tag["value"] if tag else None,
            "sf_check": check,
            "sf_delta_pct": round(delta, 1) if delta is not None else None,
            "points": [[round(float(x), 2), round(float(y), 2)]
                       for x, y in r["outer"]],
        })

    # GROUPS are the takeoff-facing unit: one appearance = one class. A group
    # holds region_ids, never its own copy of the geometry, so one region can
    # be referenced by several priced rows (a client's estimate bills the same
    # paved area twice: once to subgrade, once for 4" road base).
    gmap = defaultdict(list)
    for reg in regions:
        gmap[(tuple(reg["fill"]), reg["layer"])].append(reg)
    groups = []
    for gi, ((fill, layer), members) in enumerate(sorted(
            gmap.items(),
            key=lambda kv: -sum(m["area_pt2"] or 0 for m in kv[1]))):
        named = [m for m in members if m["name_source"] == "tag"]
        # Same ladder as a region - tag, then legend, then layer - except
        # that a tag names ONE polygon, so it may only name the GROUP when
        # every tagged member agrees on it. Members that disagree fall
        # through to the legend/layer signals rather than one of them
        # winning arbitrarily.
        if named and len({m["name"] for m in named}) == 1:
            gname, gsrc = named[0]["name"], "tag"
        elif tuple(fill) in by_colour:
            gname, gsrc = by_colour[tuple(fill)], "legend"
        elif layer:
            gname, gsrc = layer.split("|")[-1], "layer"
        else:
            gname, gsrc = None, None
        for m in members:
            m["group_id"] = gi
        # one unmeasured member makes the group total unknowable: no number
        # at all rather than a sum that silently leaves a fill out
        n_un = sum(1 for m in members if m["unmeasured"])
        a_pt2 = None if n_un else sum(m["area_pt2"] for m in members)
        p_pt = None if n_un else sum(m["perim_pt"] for m in members)
        printed = [m["printed_sf"] for m in members if m["printed_sf"]]
        bad = [m for m in members if m["sf_check"] == "mismatch"]
        # like-for-like: only the TAGGED members may be compared against the
        # printed total. Summing all members against a partial set of tags
        # manufactures a difference that is really just the untagged regions.
        tagged_pt2 = sum(m["area_pt2"] or 0 for m in members if m["printed_sf"])
        groups.append({
            "group_id": gi,
            "fill": list(fill),
            "layer": layer,
            "name": gname,
            "name_source": gsrc,
            "n": len(members),
            "region_ids": [m["region_id"] for m in members],
            "area_pt2": None if n_un else round(a_pt2, 1),
            "perim_pt": None if n_un else round(p_pt, 1),
            "area_sf": round(a_pt2 * fpp * fpp, 1) if fpp and not n_un else None,
            "perim_lf": round(p_pt * fpp, 1) if fpp and not n_un else None,
            "n_unmeasured": n_un,
            "unmeasured": next((m["unmeasured"] for m in members
                                if m["unmeasured"]), None),
            "furniture": all(m["furniture"] for m in members),
            "printed_sf": round(sum(printed), 2) if printed else None,
            # our measurement of ONLY the tagged members - the number that is
            # actually comparable with printed_sf
            "area_sf_tagged": (round(tagged_pt2 * fpp * fpp, 1)
                               if fpp and printed else None),
            "n_printed": len(printed),
            "n_mismatch": len(bad),
        })

    # legend classes with no region: named, NO quantity. On a document-wide
    # legend most rows describe some other sheet.
    matched = {tuple(r["fill"]) for r in regions}
    unmatched = [{"label": lc["label"], "fill": lc["fill"],
                  "layer": lc["layer"], "bbox": [round(v, 1) for v in lc["bbox"]]}
                 for lc in legend_classes if tuple(lc["fill"]) not in matched]

    # what the sheet's OWN printed areas say the scale is - the only
    # independent check available without a human
    implied = []
    for reg in regions:
        if reg["printed_sf"] and (reg["area_pt2"] or 0) > 0:
            implied.append(math.sqrt(reg["printed_sf"] / reg["area_pt2"]) * 72.0)
    n_ok = sum(1 for r in regions if r["sf_check"] == "ok")
    n_bad = sum(1 for r in regions if r["sf_check"] == "mismatch")
    tag_check = {
        "n_tags": len(tags),
        "n_matched": n_ok + n_bad,
        "n_ok": n_ok,
        "n_mismatch": n_bad,
        "implied_ft_per_paper_inch":
            round(float(np.median(implied)), 3) if implied else None,
        "unattached": [{"value": t["value"], "name": t["name"],
                        "x": round(t["x"], 1), "y": round(t["y"], 1)}
                       for i, t in enumerate(tags) if i not in used_tags],
    }
    return {
        "scale": {
            "ft_per_paper_inch": fpi,
            "text": (scale or {}).get("text"),
            "source": (scale or {}).get("source"),
            "tag_check": tag_check,
        },
        "regions": regions,
        "groups": groups,
        "unmatched_legend": unmatched,
        "legend_classes": [{"label": lc["label"], "fill": lc["fill"]}
                           for lc in legend_classes],
        "truncated": truncated,
    }
