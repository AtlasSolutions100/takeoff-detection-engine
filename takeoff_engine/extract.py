"""Vector linework extraction from a PDF page.

Produces StrokeUnits: connected polylines with stroke attributes. A dashed
line exploded by the CAD plot driver arrives as many tiny disconnected
units; a solid polyline arrives as one long unit; a natively-dashed path
arrives as one unit carrying a PDF dash array. All three shapes are
preserved here and disambiguated downstream in chain.py.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field

import numpy as np
import pymupdf

# Two points closer than this (in pt) are considered coincident when
# splitting a path's items into connected polylines.
CONNECT_EPS = 0.05


@contextmanager
def annots_hidden(page):
    """Run a block with the page's PDF annotations hidden, then put them back.

    PyMuPDF's get_drawings() (and get_text) run the page WITH its annotation
    appearance streams, so a client's Bluebeam markups - highlighter runs,
    polylines, count squares, callout boxes - arrive as stroke units and are
    chained, classed and billed like drawn linework. Measured 2026-09-26 on
    eight of a client's civil / structural sheets: +1.3 % to +28.8 % of chain
    length per page came from the markups (a 20 pt yellow highlighter pen was
    an 8,668 pt "solid" family of its own on the utility plan, and a
    structural sheet's footing family grew 27,566 pt).

    MuPDF skips an annotation whose /F flag carries HIDDEN, so the flags are
    set for the duration of the block and restored afterwards: nothing is
    written to the file and the reviewer's page image (get_pixmap, taken
    elsewhere) still shows the markups as delivered. Non-PDF pages have no
    annotations and pass straight through.
    """
    changed = []
    try:
        hidden = int(getattr(pymupdf, "PDF_ANNOT_IS_HIDDEN", 2))
        for a in (page.annots() or []):
            f = int(a.flags)
            if not (f & hidden):
                a.set_flags(f | hidden)
                changed.append((a, f))
    except Exception:
        pass
    try:
        yield
    finally:
        for a, f in changed:
            try:
                a.set_flags(f)
            except Exception:
                pass


def page_annotation_count(page) -> int:
    """How many PDF annotations (markups) the page carries; 0 when none or
    when the document kind has no annotations."""
    try:
        return sum(1 for _ in (page.annots() or []))
    except Exception:
        return 0


@dataclass
class StrokeUnit:
    pts: np.ndarray  # (N, 2) float64, page coordinates in pt
    color: tuple  # stroke RGB, 0-1 floats (or None)
    width: float  # stroke width in pt
    dashes: str  # raw PDF dash string, "[] 0" means solid
    closed: bool = False
    layer: str = ""  # CAD layer (OCG) name when the PDF preserves it
    on_fill: bool = False  # near-white stroke sitting on a darker fill
                           # (white-over-wash is VISIBLE; white-on-paper is
                           # whiteout and gets skipped downstream)
    length: float = field(init=False)

    def __post_init__(self):
        d = np.diff(self.pts, axis=0)
        self.length = float(np.hypot(d[:, 0], d[:, 1]).sum())

    @property
    def start(self) -> np.ndarray:
        return self.pts[0]

    @property
    def end(self) -> np.ndarray:
        return self.pts[-1]

    def direction(self) -> np.ndarray:
        """Overall unit direction start->end (chord, not arc). Cached:
        chaining asks ~500k times per heavy sheet. Anything that mutates
        pts (twin midline averaging) must clear _dir."""
        d = getattr(self, "_dir", None)
        if d is None:
            v = self.pts[-1] - self.pts[0]
            n = np.hypot(*v)
            d = v / n if n > 1e-9 else np.array([1.0, 0.0])
            self._dir = d
        return d


def page_words(page) -> list:
    """Text words in the SAME coordinate space as extract_strokes output.

    page.get_text("words") reports UNROTATED page coordinates, while stroke
    geometry is mapped through page.rotation_matrix into display space. On a
    /Rotate 90|180|270 sheet the two therefore disagree, and every
    word-driven rule - legend rows, dash-text bridging, text-box demotion -
    silently matches the wrong part of the page. Verified by rendering: on a
    270 sheet the raw box for "PROPOSED" lands on unrelated linework while
    the mapped box lands exactly on "PROPOSED BUILDING".

    OCR words need no correction - they come from get_pixmap, which is
    already rotation-aware.
    """
    with annots_hidden(page):
        words = page.get_text("words")
    if not page.rotation:
        return words
    rot = page.rotation_matrix
    out = []
    for w in words:
        a = pymupdf.Point(w[0], w[1]) * rot
        b = pymupdf.Point(w[2], w[3]) * rot
        x0, x1 = sorted((a.x, b.x))
        y0, y1 = sorted((a.y, b.y))
        out.append((x0, y0, x1, y1) + tuple(w[4:]))
    return out


def page_lines(page) -> list:
    """Text LINES in the SAME coordinate space as extract_strokes output.

    page_words() throws away which words the PDF itself considers one line,
    and on a rotated label those words are not left-to-right in display space
    at all. A spot grade written up the side of a sheet - `FG: 15.96` - has
    its tag and its value on one PDF line but nowhere near each other on a
    horizontal row, so a row-window pairing misses every rotated label and
    can reach sideways into a NEIGHBOUR's value and pair the wrong number.
    Measured on Res-B civil page index 2: of 112 printed spot
    labels, 49 were missed for being rotated and 2 were paired to the value
    beside them - one of those a 3.64 ft error on a wall top, which is the
    worst kind of defect because the answer looks right.

    The PDF's own line grouping is rotation-independent, so reading lines
    instead of re-deriving rows fixes both at once.

    Returns [(x0, y0, x1, y1, text)] with the bbox mapped through
    page.rotation_matrix, matching page_words()'s space and tuple shape.
    """
    out = []
    try:
        with annots_hidden(page):
            d = page.get_text("dict")
    except Exception:
        return out
    rot = page.rotation_matrix if page.rotation else None
    for blk in d.get("blocks", []):
        for ln in blk.get("lines", []):
            txt = "".join(sp.get("text", "") for sp in ln.get("spans", []))
            if not txt.strip():
                continue
            bx = ln.get("bbox")
            if not bx:
                continue
            if rot is None:
                x0, y0, x1, y1 = bx
            else:
                a = pymupdf.Point(bx[0], bx[1]) * rot
                b = pymupdf.Point(bx[2], bx[3]) * rot
                x0, x1 = sorted((a.x, b.x))
                y0, y1 = sorted((a.y, b.y))
            out.append((x0, y0, x1, y1, txt))
    return out


def _bezier_points(p0, p1, p2, p3, n=8) -> list:
    t = np.linspace(0, 1, n + 1)[1:]
    pts = []
    for tt in t:
        mt = 1 - tt
        x = mt**3 * p0.x + 3 * mt**2 * tt * p1.x + 3 * mt * tt**2 * p2.x + tt**3 * p3.x
        y = mt**3 * p0.y + 3 * mt**2 * tt * p1.y + 3 * mt * tt**2 * p2.y + tt**3 * p3.y
        pts.append((x, y))
    return pts


def _close(a, b) -> bool:
    return abs(a[0] - b[0]) <= CONNECT_EPS and abs(a[1] - b[1]) <= CONNECT_EPS


def _thin_rect_centerline(pts: np.ndarray, pca_axis: bool = False) -> np.ndarray | None:
    """If a closed polyline is a thin elongated box (a stroked 'thick line
    segment'), return its 2-point centerline; else None.

    Estimate: longest vertex-pair distance is the length axis; half the
    perimeter minus that is the width. Orientation-independent, so rotated
    pipe segments work.

    pca_axis: use the perimeter-weighted PCA long axis instead of the
    longest vertex pair. The corner-to-corner diameter tilts by
    atan(width/length) off a thin box's true axis, which rendered every
    collapsed FILL sliver askew of its printed ink (substation p4 property
    line, 45.7x3.3 fill slivers ~4deg off). Scoped to fill-bearing paths
    for now: flipping the axis for plain stroked rects moved C2.1's
    chain population -300 in the 2026-08-20 corpus gate, and that
    movement is unaudited - widen only behind a fresh corpus audit."""
    if len(pts) > 8:
        return None
    d = np.diff(pts, axis=0)
    perimeter = float(np.hypot(d[:, 0], d[:, 1]).sum())
    diffs = pts[:, None, :] - pts[None, :, :]
    dist = np.hypot(diffs[..., 0], diffs[..., 1])
    k = np.unravel_index(np.argmax(dist), dist.shape)
    diam = float(dist[k])
    if diam < 1e-6:
        return None
    width = max(perimeter / 2 - diam, 0.05)
    # heavy dashed boundaries plot as fat bars up to ~6pt thick
    if width > 7.0 or diam < 3.0 * width:
        return None
    if pca_axis:
        segs = np.diff(pts, axis=0)
        slen = np.hypot(segs[:, 0], segs[:, 1])
        if float(slen.sum()) < 1e-6:
            return None
        emids = (pts[:-1] + pts[1:]) / 2
        w = slen / slen.sum()
        mu = (emids * w[:, None]).sum(axis=0)
        X = emids - mu
        cov = (X * w[:, None]).T @ X
        _, evecs = np.linalg.eigh(cov)
        axis = evecs[:, -1]
        t = (pts - mu) @ axis
        lo, hi = float(t.min()), float(t.max())
        # mu is the perimeter centroid, which centers the line in the ink
        return np.vstack([mu + lo * axis, mu + hi * axis])
    axis = (pts[k[1]] - pts[k[0]]) / diam
    t = (pts - pts[k[0]]) @ axis
    lo, hi = float(t.min()), float(t.max())
    # mean perpendicular offset centers the line inside the box
    mid = (pts - (pts[k[0]] + t[:, None] * axis)).mean(axis=0)
    p0 = pts[k[0]] + lo * axis + mid
    p1 = pts[k[0]] + hi * axis + mid
    return np.vstack([p0, p1])


def _clip_loops(items: list, scissor) -> list | None:
    """The clip path's own outline as a list of (N, 2) loops in raw space, or
    None when the clip IS its scissor rectangle (an axis-aligned box).

    A CAD sheet's viewport is often not a rectangle: the plan window on a
    client's storm sheets is a hexagon with two corners cut off for the title
    block and a profile strip. `get_drawings` reports every clip's `scissor`
    (the bounding box) AND its `items` (the actual path); the extractor
    honoured only the box, so linework in the cut-off corners - which the
    renderer never draws - was measured. Verified by rendering 2026-09-26 on
    Res-A civil page index 9 (printed C4.0): 1,536 strokes under one clip sit
    inside the box and outside its polygon, and the dark-pixel fraction along
    every sampled one is 0.00 against a 0.03-1.00 spread on control strokes
    inside the polygon; 158.9 ft of that was storm pipe on a bid line.
    """
    loops, cur = [], []

    def close_cur():
        nonlocal cur
        if len(cur) >= 3:
            loops.append(np.asarray(cur, dtype=np.float64))
        cur = []

    for it in items:
        op = it[0]
        if op == "l":
            a, b = it[1], it[2]
            if cur and not _close(cur[-1], (a.x, a.y)):
                close_cur()
            if not cur:
                cur.append((a.x, a.y))
            cur.append((b.x, b.y))
        elif op == "c":
            p1, p2, p3, p4 = it[1], it[2], it[3], it[4]
            if cur and not _close(cur[-1], (p1.x, p1.y)):
                close_cur()
            if not cur:
                cur.append((p1.x, p1.y))
            cur.extend(_bezier_points(p1, p2, p3, p4))
        elif op == "re":
            close_cur()
            r = it[1]
            loops.append(np.asarray([(r.x0, r.y0), (r.x1, r.y0),
                                     (r.x1, r.y1), (r.x0, r.y1)], dtype=np.float64))
        elif op == "qu":
            close_cur()
            q = it[1]
            loops.append(np.asarray([(q.ul.x, q.ul.y), (q.ur.x, q.ur.y),
                                     (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)], dtype=np.float64))
    close_cur()
    if not loops:
        return None
    # a single axis-aligned box that matches the scissor is the common case
    # (viewports, text clips): no polygon work for it
    if len(loops) == 1 and scissor is not None:
        L = loops[0]
        xs, ys = np.unique(np.round(L[:, 0], 1)), np.unique(np.round(L[:, 1], 1))
        if len(xs) <= 2 and len(ys) <= 2:
            return None
    # drop duplicate consecutive vertices (a closing repeat of the start)
    out = []
    for L in loops:
        keep = [0] + [k for k in range(1, len(L))
                      if not _close(L[k], L[k - 1])]
        if len(keep) >= 2 and _close(L[keep[-1]], L[keep[0]]):
            keep = keep[:-1]
        if len(keep) >= 3:
            out.append(_ClipLoop(L[keep]))
    return out or None


class _ClipLoop:
    """One loop of a clip path, with what the point test needs precomputed:
    the vertices as a Python list (the per-path test is a short loop over a
    handful of edges, and numpy's per-call overhead loses to it), the box,
    and whether the loop is convex (a convex viewport contains a polyline
    whose box it contains - the common case, decided without a point test)."""

    __slots__ = ("pts", "edges", "lo", "hi", "convex")

    def __init__(self, arr: np.ndarray):
        self.pts = arr
        v = [(float(x), float(y)) for x, y in arr]
        self.edges = [(v[i], v[(i + 1) % len(v)]) for i in range(len(v))]
        self.lo = (float(arr[:, 0].min()), float(arr[:, 1].min()))
        self.hi = (float(arr[:, 0].max()), float(arr[:, 1].max()))
        d = np.roll(arr, -1, axis=0) - arr
        d2 = np.roll(d, -1, axis=0)
        cross = d[:, 0] * d2[:, 1] - d[:, 1] * d2[:, 0]
        sig = cross[np.abs(cross) > 1e-6]
        self.convex = len(sig) == 0 or bool((sig > 0).all() or (sig < 0).all())

    def contains(self, x: float, y: float) -> bool:
        """Even-odd crossing test."""
        inside = False
        for (ax, ay), (bx, by) in self.edges:
            if (ay > y) != (by > y):
                xint = (bx - ax) * (y - ay) / ((by - ay) or 1e-12) + ax
                if x < xint:
                    inside = not inside
        return inside

    def near_edge(self, x: float, y: float, tol: float) -> bool:
        t2 = tol * tol
        for (ax, ay), (bx, by) in self.edges:
            ex, ey = bx - ax, by - ay
            L2 = ex * ex + ey * ey
            if L2 < 1e-12:
                continue
            t = ((x - ax) * ex + (y - ay) * ey) / L2
            t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
            qx, qy = ax + t * ex, ay + t * ey
            if (x - qx) ** 2 + (y - qy) ** 2 <= t2:
                return True
        return False


def _inside_loops(pt_list: list, loops: list, tol: float = 0.5) -> list:
    """Per point: inside ANY loop of this clip (even-odd), or within `tol`
    of a loop edge - the same slop the scissor box gets. Returns a list of
    bools, one per point."""
    n = len(pt_list)
    xs = [float(p[0]) for p in pt_list]
    ys = [float(p[1]) for p in pt_list]
    lo = (min(xs), min(ys))
    hi = (max(xs), max(ys))
    ok = [False] * n
    for L in loops:
        # cheap outs: wholly outside the loop's box touches nothing; a box
        # wholly inside a CONVEX loop is wholly inside (the viewport case)
        if hi[0] < L.lo[0] - tol or hi[1] < L.lo[1] - tol \
                or lo[0] > L.hi[0] + tol or lo[1] > L.hi[1] + tol:
            continue
        if L.convex and L.contains(lo[0], lo[1]) and L.contains(hi[0], lo[1]) \
                and L.contains(hi[0], hi[1]) and L.contains(lo[0], hi[1]):
            return [True] * n
        for k in range(n):
            if not ok[k] and (L.contains(xs[k], ys[k])
                              or (tol > 0 and L.near_edge(xs[k], ys[k], tol))):
                ok[k] = True
    return ok


def _clip_runs(pt_list: list, clip, polys: list | None = None) -> list:
    """Trim a polyline to a clip rect (and, when the clip path is not a
    rectangle, to its polygon): keep runs of consecutive inside points.
    Border-crossing segments lose their outside tail (error bounded by one
    segment length) - fine for linework, exact intersection can come later
    if measurement at viewport edges ever matters."""
    if clip is None and not polys:
        return [pt_list]
    if clip is not None:
        ok = [clip.x0 <= p[0] <= clip.x1 and clip.y0 <= p[1] <= clip.y1
              for p in pt_list]
    else:
        ok = [True] * len(pt_list)
    if polys and any(ok):
        for loops in polys:
            ins = _inside_loops(pt_list, loops)
            ok = [a and b for a, b in zip(ok, ins)]
            if not any(ok):
                break
    runs = []
    cur = []
    for p, keep in zip(pt_list, ok):
        if keep:
            cur.append(p)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def _split_loops(pt_list: list) -> list:
    """Split a polyline at points it revisits, separating closed sub-loops
    from open tails.

    CAD arrow/marker glyphs are often plotted as one stroke that draws a
    closed shape (arrowhead, tick box) and continues with a line: the
    resulting unit starts mid-shape and its endpoints lie about anywhere.
    Splitting the loop out leaves a clean open segment whose endpoints are
    the real connection points, and a closed decoration that chaining
    ignores.
    """
    out = []
    pts = pt_list
    guard = 0
    while len(pts) >= 3 and guard < 16:
        guard += 1
        seen = {}
        split = None
        for k, p in enumerate(pts):
            key = (round(p[0], 1), round(p[1], 1))
            if key in seen and k - seen[key] >= 2:
                split = (seen[key], k)
                break
            seen[key] = k
        if split is None:
            break
        j, k = split
        out.append(pts[j : k + 1])  # the closed loop
        pts = pts[: j + 1] + pts[k + 1 :]  # remainder continues through pts[j]
    out.append(pts)
    return out


def extract_strokes(page: pymupdf.Page) -> list[StrokeUnit]:
    """Extract visible stroke units.

    Honors what the renderer honors and get_drawings alone does not:
    - clip regions: CAD sheets are paper-space viewports; xref/model
      content outside the viewport scissor exists in the file but is never
      drawn ("phantom geometry"). Polylines are trimmed to the effective
      clip; fully-outside strokes are dropped.
    - hidden OCG layers: strokes on layers toggled off are skipped.
    - page rotation: get_drawings returns RAW unrotated coordinates (unlike
      get_text and get_pixmap, which are rotation-aware). Clipping happens
      in raw space, then all geometry is mapped through rotation_matrix so
      output matches the rendered page and the text layer.
    """
    units: list[StrokeUnit] = []
    fill_rects: list = []  # raw-space bboxes of non-white fills
    rot = page.rotation_matrix if page.rotation else None
    off_layers = {
        info["name"]
        for info in (page.parent.get_ocgs() or {}).values()
        if not info.get("on", True)
    }

    # margin for numeric slop at viewport borders
    PAD = 0.5
    clip_stack: list[tuple[int, pymupdf.Rect | None, list | None]] = []

    # the client's PDF markups are hidden for the walk: they are not the
    # drawing (see annots_hidden)
    with annots_hidden(page):
        drawings = page.get_drawings(extended=True)
    for path in drawings:
        ptype = path.get("type") or ""
        level = path.get("level", 0)
        # entries at deeper-or-equal levels have gone out of scope
        while clip_stack and clip_stack[-1][0] >= level:
            clip_stack.pop()
        if ptype == "clip":
            scissor = path.get("scissor")
            rect = None
            loops = None
            if scissor is not None and not pymupdf.Rect(scissor).is_empty:
                # scissor stays in raw space; points are clipped raw too
                rect = pymupdf.Rect(scissor) + (-PAD, -PAD, PAD, PAD)
                # the clip PATH, when it is not the box (a viewport with a
                # corner cut off): what the renderer really honours
                try:
                    loops = _clip_loops(path.get("items") or [], scissor)
                except Exception:
                    loops = None
            clip_stack.append((level, rect, loops))
            continue
        if "f" in ptype:
            fc = path.get("fill")
            # a fill DARKER than near-white creates contrast beneath any
            # white stroke drawn over it (FEMA flood washes etc.)
            if fc and min(fc) <= 0.9 and path.get("rect") is not None:
                fill_rects.append(pymupdf.Rect(path["rect"]))
        if "s" not in ptype:
            continue  # stroke units are what linework is made of
        if path.get("layer") in off_layers:
            continue
        clip = None
        polys = []
        for _, r, loops in clip_stack:
            if r is not None:
                clip = r if clip is None else clip & r
            if loops:
                polys.append(loops)
        color = path.get("color")
        width = path.get("width") or 0.0
        dashes = path.get("dashes") or "[] 0"
        layer = path.get("layer") or ""

        current: list = []  # growing polyline point list

        def emit(pt_list):
            for part in _split_loops(pt_list):
                for run in _clip_runs(part, clip, polys):
                    if len(run) >= 2:
                        pts = np.asarray(run, dtype=np.float64)
                        closed = _close(run[0], run[-1]) and len(run) > 2
                        if closed:
                            # thick line segments are often plotted as thin
                            # closed rectangles (pipe runs, wide dashes);
                            # collapse those to their centerline so they can
                            # chain like any other dash
                            cl = _thin_rect_centerline(pts, pca_axis="f" in ptype)
                            if cl is not None:
                                pts, closed = cl, False
                        if rot is not None:
                            pts = np.column_stack([
                                rot.a * pts[:, 0] + rot.c * pts[:, 1] + rot.e,
                                rot.b * pts[:, 0] + rot.d * pts[:, 1] + rot.f,
                            ])
                        units.append(
                            StrokeUnit(pts=pts, color=color, width=width,
                                       dashes=dashes, closed=closed, layer=layer)
                        )

        def flush():
            nonlocal current
            if len(current) >= 2:
                emit(current)
            current = []

        for item in path["items"]:
            op = item[0]
            if op == "l":
                p1, p2 = item[1], item[2]
                seg = [(p1.x, p1.y), (p2.x, p2.y)]
            elif op == "c":
                p1, p2, p3, p4 = item[1], item[2], item[3], item[4]
                seg = [(p1.x, p1.y)] + _bezier_points(p1, p2, p3, p4)
            elif op == "re":
                r = item[1]
                seg = [
                    (r.x0, r.y0),
                    (r.x1, r.y0),
                    (r.x1, r.y1),
                    (r.x0, r.y1),
                    (r.x0, r.y0),
                ]
            elif op == "qu":
                q = item[1]
                seg = [
                    (q.ul.x, q.ul.y),
                    (q.ur.x, q.ur.y),
                    (q.lr.x, q.lr.y),
                    (q.ll.x, q.ll.y),
                    (q.ul.x, q.ul.y),
                ]
            else:
                continue

            if current and _close(current[-1], seg[0]):
                current.extend(seg[1:])
            else:
                flush()
                current = seg
        flush()

    # Near-white strokes over a darker fill are VISIBLE linework (substation p4
    # draws its floodplain contours white over the FEMA wash - 26k pt of
    # real line). Mark them so the downstream whiteout skip spares them;
    # white on bare paper stays skipped. Fill rects are raw-space, so map
    # them into display space the same way stroke geometry was.
    # only OPEN strokes qualify: closed white shapes over a fill are label
    # knockout frames (they masked text on the wash), and emitting them
    # produced spiky phantom rectangles in the takeoff. The floodplain
    # contours this exists for are open dash marks.
    whites = [u for u in units
              if u.color and min(u.color) > 0.92 and not u.closed]
    if whites and fill_rects:
        rects = []
        for r in fill_rects:
            if rot is not None:
                r = pymupdf.Rect(r) * rot
            r = pymupdf.Rect(r)
            r.normalize()
            rects.append((r.x0 - 1, r.y0 - 1, r.x1 + 1, r.y1 + 1))
        R = np.asarray(rects)
        for u in whites:
            m = u.pts[len(u.pts) // 2]
            hit = ((R[:, 0] <= m[0]) & (m[0] <= R[:, 2]) &
                   (R[:, 1] <= m[1]) & (m[1] <= R[:, 3]))
            if bool(hit.any()):
                u.on_fill = True

    return units


def fill_boxes(page: pymupdf.Page) -> list:
    """Rotation-mapped bboxes of non-white fills, page coords.

    Walls/objects on architectural plans are drawn as FILLED bands; leaders
    and dimension strings are bare strokes. That contrast lets the reviewer
    UI tell a wall face from an annotation drawn with the same pen."""
    rot = page.rotation_matrix if page.rotation else None
    out = []
    with annots_hidden(page):
        drawings = page.get_drawings()
    for path in drawings:
        if "f" not in (path.get("type") or ""):
            continue
        col = path.get("fill")
        if col and min(col) > 0.92:
            continue  # white fill = knockout, not a band
        r = path.get("rect")
        if r is None:
            continue
        if rot is not None:
            r = pymupdf.Rect(r) * rot
        r = pymupdf.Rect(r)
        r.normalize()
        if r.width < 1 and r.height < 1:
            continue
        out.append([round(r.x0, 1), round(r.y0, 1),
                    round(r.x1, 1), round(r.y1, 1)])
    return out


def door_swings(units: list, scale_ft_per_in: float | None = None,
                words: list | None = None) -> list:
    """Door-swing opening symbols: swing arcs corroborated by structure.

    A drafted door is a compass construction - a straight LEAF line from
    the hinge plus an arc swept about that same hinge - so a bare arc is
    never reported on its own. Every arc candidate must be corroborated by
    its leaf line, or by a mirrored partner arc (a double door). That
    corroboration is what keeps civil sheets clean: at 1"=30'-60' the
    scaled door-radius window shrinks to a few pt and contour bends /
    symbol fillets pass every per-arc gate. Measured 2026-08-19 (function
    output, after dedupe): the substation set p4 went 72 phantom openings ->
    1 (a title-block logo glyph); Belle Farms ROAD GRADING 1of2 p1 went
    103 -> 0; Belle Structural p1 went 6 -> 23, all 23 eyeballed real
    (crops in out/verify/2026-08-19/double-doors/).

    Arc candidate: >=5 vertices (Belle Structural p1 flattens 45-degree
    door arcs to 6-pt polylines, 90-degree ones to 9-11 pt), open, points
    equidistant from a fitted center, sweep 35-135 degrees, radius in door
    range (1.25-6 ft when the scale is known; 8-80 pt otherwise).

    Corroboration, either of:
    - LEAF: a straight unit of length ~r joining the fitted center (the
      hinge) to an arc endpoint - the drawn door leaf. Belle Structural p1
      draws each leaf as two parallel strokes, both exactly length r.
    - PAIR: a partner arc with matching radius whose center sits ~r1+r2
      away, both arcs having an endpoint at the shared meeting point (the
      closed leaves touch mid-opening) and bulging to the same side of the
      hinge-hinge line. Measured on Belle Structural p1: three grand-hall
      double doors, each two 45-degree arcs r=27pt with center distance
      54.1pt = (r1+r2) within 0.1% and endpoint-to-meeting-point gap
      0.0-0.1pt; the 90-degree main-entry pair (r=36pt) matches the same
      template. The tight meeting-point gate is load-bearing: adjacent
      section-callout bubbles on the same sheet (r=13.5/14.4pt) sit at
      center distance 1.05x(r1+r2) but their arc endpoints miss the
      meeting point by 14-27pt.

    Returns [{cx, cy, r}] opening zones the takeoff must not measure
    through."""
    if scale_ft_per_in:
        # 1.25 ft radius floor (was 1.5): Belle Structural p1 restroom /
        # office doors are r=24pt = 1.33 ft at the sheet's detected
        # 1/4"=1'-0" scale (its title block also carries a 1/8" candidate,
        # so the detected scale can sit 2x off on mixed-scale sheets) -
        # the 1.5 ft floor missed 7 real doors there.
        r_lo = (1.25 / scale_ft_per_in) * 72
        r_hi = (6.0 / scale_ft_per_in) * 72
    else:
        r_lo, r_hi = 8.0, 80.0
    # absolute paper-space floor: smallest real door measured across the
    # corpus is r=10.8pt (C3.0/C4.0/C9.0 building doors at 1"=20');
    # phantom classes below it: leader-arrowhead curls r=3.2-3.4pt
    # (substation p4, admitted when the 25-35deg fragment window landed at
    # civil scales), logo glyph bowls r=2.1-2.4pt. 8.0 splits the
    # populations with margin both ways.
    r_lo = max(r_lo, 8.0)

    # pass 1 - arc candidates
    cands = []  # (cx, cy, r, end0, end1, arc midpoint, sweep)
    da_cands = []  # double-acting 180deg candidates
    for u in units:
        # arc length = r x sweep: with r in [r_lo, r_hi] and sweep in
        # [35deg, 135deg] a door arc's length must sit in
        # [0.611 r_lo, 2.356 r_hi] - widened to 0.55/2.5 for flattening
        # slop. Only skips units the r/sweep gates would reject anyway,
        # and spares the circle fit on every long contour polyline
        # (substation p4: 5302 -> 1924 fits).
        if not (0.55 * r_lo <= u.length <= 2.5 * r_hi):
            continue
        p = np.asarray(u.pts, dtype=float)
        if u.closed:
            continue
        # 4-point floor: a 30deg fragment flattens to 4 vertices at the
        # drafting tool's ~9deg/vertex (Belle p1 measured); the circle
        # fit needs 3. Count DISTINCT POSITIONS, not vertices: an
        # algebraic circle fit through points that sit in two tight
        # clusters is underdetermined and returns the circle whose
        # DIAMETER is the cluster gap - radius = gap/2, sweep = 180deg,
        # radial residual ~0.01pt - so it sails through the fit-std gate
        # and lands in the 165-195deg double-acting branch. That is the
        # phantom factory: measured 2026-08-22 on the Res-A residence
        # Civil (15 sheets, no doors anywhere), 92 of its 112 reported
        # openings are these hairpins, e.g. a 5-vertex unit with segment
        # lengths [0.19, 30.21, 0.19, 0.19] fitted r=15.1 std=0.009.
        # Dedupe radius-relative - what counts as "the same place"
        # scales with the arc being claimed - using the chord as the
        # radius proxy available before the fit runs.
        if len(p) < 4:
            continue
        _eps = max(0.4, 0.025 * float(np.hypot(*(p[-1] - p[0]))))
        _keep = [p[0]]
        for _q in p[1:]:
            if min(np.hypot(_q[0] - _k[0], _q[1] - _k[1])
                   for _k in _keep) > _eps:
                _keep.append(_q)
                if len(_keep) >= 4:
                    break
        if len(_keep) < 4:
            continue
        chord = float(np.hypot(*(p[-1] - p[0])))
        # arc/chord: 90deg sweep = 1.111, 55deg = 1.039, 45deg = 1.026,
        # 35deg = 1.016. The floor sits below the shallowest drawn leaf
        # (Belle Structural p1 half-open doors sweep 45deg; measured
        # arc/chord 1.0255 after polyline flattening) - corroboration, not
        # this floor, now does the discriminating.
        # 35deg = 1.016, 30deg = 1.0115, 25deg = 1.0080; polyline
        # flattening noise on straight runs measures <= 1.005. The gate
        # sits between so 25-35deg FRAGMENTS reach the candidate pool
        # (their leaf + template-radius gates do the discriminating).
        if chord < 1e-6 or u.length < 1.0075 * chord:
            continue  # straight-ish, not an arc
        # fit center: perpendicular bisector intersection via least squares
        # (algebraic circle fit)
        A = np.column_stack([2 * p[:, 0], 2 * p[:, 1], np.ones(len(p))])
        b = (p ** 2).sum(axis=1)
        try:
            sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        except np.linalg.LinAlgError:
            continue
        cx, cy = float(sol[0]), float(sol[1])
        rr = np.hypot(p[:, 0] - cx, p[:, 1] - cy)
        r = float(rr.mean())
        if r < r_lo or r > r_hi:
            continue
        if float(rr.std()) > max(0.06 * r, 0.8):
            continue  # not circular enough
        va = p[0] - (cx, cy)
        vb = p[-1] - (cx, cy)
        sweep = float(np.degrees(np.arccos(np.clip(
            (va @ vb) / (np.hypot(*va) * np.hypot(*vb) + 1e-9), -1, 1))))
        # The polyline must be AS LONG AS the arc it claims. That is the
        # definition of an arc, and the cheapest way to reject a fit the
        # data did not determine: r x sweep is what the drawn line would
        # measure if it really ran round this circle. Measured over the
        # 1267 openings the corpus reported before this gate: every
        # flattened real arc sits at 0.93-1.00 (Belle Structural door
        # swings 0.998-1.000, Res-C p1 0.999, C3.0 0.997),
        # while the two-cluster hairpins sit at 0.645-0.836 and
        # wandering fits reach 1.8-3.7. The 1.20 ceiling also leaves room
        # for a true 190deg sweep, which arccos reports back as 170.
        _arc = r * np.radians(sweep)
        if _arc < 1e-6 or not (0.90 * _arc <= u.length <= 1.20 * _arc):
            continue
        # 35deg is the confident floor (half-open doors draw at 45deg);
        # 25-35deg admits FRAGMENTS - arcs a crossing line split (Belle
        # Structural p1: two doors survive only as 30.0deg r=27 pieces,
        # perfect circle fits) - but those must corroborate via their
        # LEAF specifically (pair logic assumes whole leaves). 165-195deg
        # is a DOUBLE-ACTING door (glass connector: two 180deg r=36
        # semicircles, no leaf drawn) gated separately below; the 135 cap
        # still excludes callout-bubble halves from the swing path.
        if 165.0 <= sweep <= 195.0:
            da_cands.append((cx, cy, r, p[0], p[-1], p[len(p) // 2]))
            continue
        if not (25.0 <= sweep <= 135.0):
            continue
        cands.append((cx, cy, r, p[0], p[-1], p[len(p) // 2], sweep))

    if not cands:
        return []

    # pass 2 - straight-unit endpoints, the leaf-line pool (length
    # pre-filtered to the door-leaf range to bound the search)
    rows = []
    strt = []   # the WALL pool: every straight unit >= 6pt, any length
    for u in units:
        p = u.pts
        chord = float(np.hypot(*(p[-1] - p[0])))
        if chord < 1e-6 or u.length > 1.02 * chord:
            continue  # not straight enough to be a drawn leaf
        if u.length >= 6.0:
            strt.append((p[0][0], p[0][1], p[-1][0], p[-1][1]))
        if not (0.8 * r_lo <= u.length <= 1.2 * r_hi):
            continue
        rows.append((p[0][0], p[0][1], p[-1][0], p[-1][1], u.length))
    E = np.asarray(rows) if rows else np.zeros((0, 5))
    S = np.asarray(strt) if strt else np.zeros((0, 4))

    def _near_straight(x, y, tol, dirv=None, cos_min=0.978):
        """Mask of straight units passing within tol of (x, y).

        With dirv set only units running within 12deg of that direction
        count - the difference between "some line happens to touch this
        point" and "the wall this door hangs in runs through it"."""
        if not len(S):
            return np.zeros(0, dtype=bool)
        dx = S[:, 2] - S[:, 0]
        dy = S[:, 3] - S[:, 1]
        L2 = dx * dx + dy * dy
        t = np.clip(((x - S[:, 0]) * dx + (y - S[:, 1]) * dy)
                    / np.maximum(L2, 1e-9), 0.0, 1.0)
        m = np.hypot(x - (S[:, 0] + t * dx), y - (S[:, 1] + t * dy)) <= tol
        if dirv is not None:
            L = np.sqrt(np.maximum(L2, 1e-9))
            m = m & ((np.abs(dx * dirv[0] + dy * dirv[1]) / L) >= cos_min)
        return m

    def jamb_ok(cx, cy, r, ends):
        # A drafted door hangs IN A WALL: the hinge sits on the jamb and
        # the closed-leaf position lies along that same wall one radius
        # away, so the hinge and one arc endpoint both sit on a single
        # straight unit running along that radius. Measured 2026-08-22
        # against source crops: true for 25/25 Belle Structural p0 door
        # swings and 24/25 Res-C p1 swings (both sheets
        # eyeballed as real doors), and for 0/6 of the road-edge curve
        # returns Res-A civil p0 reports - those corroborate through
        # pair_ok, two mirrored driveway returns being geometrically
        # indistinguishable from a double door without this test.
        # max(3.0, 0.15r), not max(2.0, 0.10r). Measured 2026-08-22 over
        # the five layered residential documents using the drafter's own CAD
        # layer as truth: at 2.0/0.10 this gate deletes 9 arcs drawn on
        # layer A-DOOR (Res-A landscaping p7/p11/p14/p15/p20, arc-length
        # ratio 0.998-1.000). The p7 case misses by 0.26 pt - its leaf, also
        # on A-DOOR, starts 2.26 pt from the fitted hinge. Note 2.0 is
        # TIGHTER than leaf_ok's own 2.7 pt tolerance at r=18, so the wall
        # test was vetoing doors the leaf test had already corroborated.
        # Loosening recovers 5 of them and adds ZERO phantoms across all 15
        # Res-A civil and all 9 Res-B civil sheets.
        tol = max(3.0, 0.15 * r)
        for ea in ends:
            vx, vy = float(ea[0]) - cx, float(ea[1]) - cy
            n = float(np.hypot(vx, vy))
            if n < 1e-6:
                continue
            d = (vx / n, vy / n)
            if bool((_near_straight(cx, cy, tol, d)
                     & _near_straight(float(ea[0]), float(ea[1]),
                                      tol, d)).any()):
                return True
        return False

    def leaf_ok(cx, cy, r, ends):
        # length window 0.8-1.2r and summed endpoint tolerance
        # max(1.5, 0.15r): measured leaves on Belle Structural p1 are
        # length 0.95-1.00r with endpoint mismatch 0.03-2.98pt at
        # r=24-27pt (the leaf is two parallel strokes ~2pt apart and the
        # far stroke must still match).
        if not len(E):
            return False
        lok = (E[:, 4] >= 0.8 * r) & (E[:, 4] <= 1.2 * r)
        if not bool(lok.any()):
            return False
        # ...capped at 3.0pt absolute. Measured 2026-08-22 across 58
        # human-checked real door leaves (Belle Structural p0 25,
        # Res-C p1 24, C3.0 Site Plan 9) the worst
        # mismatch is 2.84pt; the pipe-elbow flange lines that
        # impersonate leaves on Res-A civil p14 (a NOT-TO-SCALE
        # piping detail) sit at 3.25-3.30pt. Without the cap the 0.15r
        # term buys them 3.69pt of slack at r=24.6.
        # A radius-relative cap max(3.0, c*r) was MEASURED and REFUTED
        # 2026-08-22 over all 217 corpus pages (leafpool scan,
        # out/verify/2026-08-22/doors2): the first phantom admits at
        # c=0.0481 (an angular-dimension arc on Res-B structural p4,
        # r=87.7, miss 4.22) - BEFORE the first lost real door at
        # c=0.0809 (Res-A landscaping p20 r=43.2, on layer A-DOOR, miss
        # 3.50). The window is empty, so no c both recovers a door and
        # holds the zero-phantom bar; that door comes back through
        # pair_ok's template path instead. The elbow flanges sit at
        # c=0.132-0.134 and were never the binding constraint.
        tol = min(max(1.5, 0.15 * r), 3.0)
        for ex, ey in ends:
            da = (np.hypot(E[:, 0] - cx, E[:, 1] - cy)
                  + np.hypot(E[:, 2] - ex, E[:, 3] - ey))
            db = (np.hypot(E[:, 2] - cx, E[:, 3] - cy)
                  + np.hypot(E[:, 0] - ex, E[:, 1] - ey))
            if bool((lok & (np.minimum(da, db) < tol)).any()):
                return True
        return False

    def pair_ok(i):
        """-> (corroborated, template_corroborated).

        The second flag is the TEMPLATE pair: radii equal within 2% and
        sweeps within 1.5deg. A drafter places a double door / bi-fold
        bank by repeating one block, so real pairs are numerically
        identical; a curve that merely wanders into pair geometry is two
        DIFFERENT pieces. Measured 2026-08-22 over all 217 corpus pages
        (pairpool/meetpool scans, out/verify/2026-08-22/doors2): every
        layer-confirmed A-DOOR pair matches at dR <= 0.9% / dsweep <=
        0.7deg, while every non-door pair that passes the loose test
        misses it - road-edge returns on Res-A/Res-B civil dR 9.9-13.7%
        dsweep 8-12deg, existing-ground contour wiggles on C4.0/C8.0/C9.0
        dR 10-14%, a steel-framing axonometric curve pair on Res-A
        structural p8 dsweep 2.5deg, a scanned floor plan's two unequal
        arcs on a client's set dsweep 49deg. The 2% / 1.5deg cuts sit >= 2.2x
        from both populations. A template pair is trusted two ways:
        - it OVERRIDES the jamb veto (the caller): bi-fold banks hang on
          a closet-front line ~one radius from the fitted hinge, so
          jamb_ok misses them by 7-36pt (Res-A landscaping p7/p11/p14/p15,
          20 arcs on layer A-DOOR, all currently deleted);
        - its meeting-point tolerance is max(1.5, 0.11 x r), not 0.05 x r:
          large double doors close on an astragal gap that scales with
          the plot, measured 2.20pt at r=43.2 (Landscaping p20, on
          A-DOOR, 0.0508r - the shipped 0.05r cap misses it by 0.03pt)
          and 7.51pt at r=72.0 (p15, 0.1043r). Between 0.1043 and the
          0.30 scan ceiling the corpus holds NO template pair, so 0.11
          has the widest usable margin; the loose-pair tolerance stays
          at 0.05r, which keeps the near-template section-arc fans
          (Landscaping p17, dR 3.6-14.8%) out."""
        cxi, cyi, ri, a0, a1, mi, _swi = cands[i]
        found = False
        for j, (cxj, cyj, rj, b0, b1, mj, _swj) in enumerate(cands):
            if j == i:
                continue
            # leaf radii match within 15% (leaf-and-a-half doors are
            # unequal; the measured pairs are exactly equal)
            if abs(ri - rj) > 0.15 * max(ri, rj):
                continue
            d = float(np.hypot(cxj - cxi, cyj - cyi))
            rsum = ri + rj
            # hinges sit one opening apart: measured 1.000x(r1+r2) on all
            # four Belle Structural p1 pairs; 8% slop for astragal gaps
            if not (0.92 * rsum <= d <= 1.08 * rsum) or d < 1e-6:
                continue
            templ = (abs(ri - rj) <= 0.02 * max(ri, rj)
                     and abs(_swi - _swj) <= 1.5)
            # meeting point: where the closed leaves touch, ri along the
            # hinge-hinge line. Both arcs must END there - measured gap
            # 0.0-0.1pt on real pairs, 14-27pt on the callout-bubble trap.
            mx = cxi + (ri / d) * (cxj - cxi)
            my = cyi + (ri / d) * (cyj - cyi)
            tol = max(1.5, (0.11 if templ else 0.05) * max(ri, rj))
            if min(np.hypot(a0[0] - mx, a0[1] - my),
                   np.hypot(a1[0] - mx, a1[1] - my)) > tol:
                continue
            if min(np.hypot(b0[0] - mx, b0[1] - my),
                   np.hypot(b1[0] - mx, b1[1] - my)) > tol:
                continue
            # both leaves open into the same room: arc midpoints on the
            # same side of the hinge-hinge line (measured: sides agree on
            # every real pair; a fillet facing a fillet does not)
            ux, uy = cxj - cxi, cyj - cyi
            si = ux * (mi[1] - cyi) - uy * (mi[0] - cxi)
            sj = ux * (mj[1] - cyi) - uy * (mj[0] - cxi)
            if si == 0.0 or sj == 0.0 or (si > 0) != (sj > 0):
                continue
            if templ:
                return True, True
            found = True
        return found, False

    out = []
    confirmed_r = []
    for i, (cx, cy, r, e0, e1, _mid, sweep) in enumerate(cands):
        if sweep < 35.0:
            continue   # fragments resolved in a second pass below
        jok = jamb_ok(cx, cy, r, (e0, e1))
        if jok and leaf_ok(cx, cy, r, (e0, e1)):
            corrob = True
        else:
            po, ps = pair_ok(i)
            # a TEMPLATE pair overrides the jamb veto (see pair_ok);
            # a merely loose pair still needs the wall
            corrob = ps or (jok and po)
        if corrob:
            # mx,my = arc midpoint: tells the consumer which SIDE of the
            # hinge the opening spans (the door chord), so a wall run is
            # cut only across the actual doorway, not the full swing disk
            out.append({"cx": round(cx, 1), "cy": round(cy, 1),
                        "r": round(r, 1),
                        "mx": round(float(_mid[0]), 1),
                        "my": round(float(_mid[1]), 1)})
            confirmed_r.append(r)
    # FRAGMENT pass: a 25-35deg arc is admitted only when (a) its leaf
    # corroborates AND (b) its radius matches a CONFIRMED full door on
    # this page within 15% - the drafting template repeats, so real
    # split doors share r with their whole siblings (Belle p1: r=27.0
    # fragments vs confirmed r=27 doors). Radius-dimension callouts on
    # civil sheets have leaf-like leaders but no confirmed-door radius
    # to match (substation p4 grew +10 phantoms without this gate).
    for i, (cx, cy, r, e0, e1, _mid, sweep) in enumerate(cands):
        if sweep >= 35.0:
            continue
        if not any(abs(r - cr) <= 0.15 * max(r, cr) for cr in confirmed_r):
            continue
        if not jamb_ok(cx, cy, r, (e0, e1)):
            continue
        if leaf_ok(cx, cy, r, (e0, e1)):
            out.append({"cx": round(cx, 1), "cy": round(cy, 1),
                        "r": round(r, 1),
                        "mx": round(float(_mid[0]), 1),
                        "my": round(float(_mid[1]), 1)})

    # double-acting doors: 180deg swing, no leaf drawn. Guards, each
    # measured on Belle Structural p1 vs its own callout bubbles:
    #  - no text inside the circle (bubbles carry sheet numbers; the two
    #    real glass-connector doors are empty)
    #  - both arc endpoints sit on the WALL: a straight unit running
    #    along the CHORD, which is the track the door hangs from. The
    #    old test took any straight line touching either endpoint from
    #    any direction, and that is precisely the signature of a STADIUM
    #    (rounded-rectangle) outline - its two semicircular end caps have
    #    the tank's parallel sides landing on both endpoints, but
    #    PERPENDICULAR to the chord. Measured 2026-08-22: the chord test
    #    keeps both real glass-connector doors on Belle Structural
    #    p0/p1/p2 and rejects all 16 propane-tank / circle-symbol
    #    semicircles that survive the arc-length gate on Res-A and
    #    Res-B civil (CAD layer '34008 Site|P-PROPANE').
    if da_cands:
        def center_crossed(cx, cy, r, chord_dir, ex0, ey0):
            # equipment symbols (substation breakers on bus lines) are
            # circles with straight feeds THROUGH the middle in non-chord
            # directions; a door's only center-adjacent line is the track
            # it hangs on, collinear with its chord. Measured on substation
            # p4: every phantom had a >20deg crossing; the two real glass
            # doors have none.
            if not len(S):
                return False
            dx = S[:, 2] - S[:, 0]
            dy = S[:, 3] - S[:, 1]
            L2 = dx * dx + dy * dy
            t = ((cx - S[:, 0]) * dx + (cy - S[:, 1]) * dy) / np.maximum(L2, 1e-9)
            t = np.clip(t, 0.0, 1.0)
            qx = S[:, 0] + t * dx
            qy = S[:, 1] + t * dy
            near = np.hypot(cx - qx, cy - qy) <= 0.35 * r
            # only strokes long enough to be a feed/bus count as crossings:
            # dashed zone-line MARKS (~11pt) also cross door centers
            # (Belle glass door #2) but a symbol's feed line runs >= r
            L = np.sqrt(np.maximum(L2, 1e-9))
            near &= L >= r
            if not bool(near.any()):
                return False
            cosang = np.abs(dx * chord_dir[0] + dy * chord_dir[1]) / L
            # a crossing goes THROUGH the opening - endpoints on opposite
            # sides of the chord line. Walls that TERMINATE at the door
            # track (measured 5.9pt shy on Belle glass #2) stay one-sided
            # and are jambs, not feeds.
            nx, ny = -chord_dir[1], chord_dir[0]
            s0 = (S[:, 0] - ex0) * nx + (S[:, 1] - ey0) * ny
            s1 = (S[:, 2] - ex0) * nx + (S[:, 3] - ey0) * ny
            through = (s0 * s1) < -1.0
            return bool((near & through & (cosang < 0.94)).any())

        for cx, cy, r, e0, e1, _mid in da_cands:
            if words:
                inside = False
                for w in words:
                    wx, wy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
                    if np.hypot(wx - cx, wy - cy) < r * 0.95:
                        inside = True
                        break
                if inside:
                    continue
            ch = np.asarray(e1, dtype=float) - np.asarray(e0, dtype=float)
            n = float(np.hypot(*ch)) or 1.0
            if center_crossed(cx, cy, r, ch / n, float(e0[0]), float(e0[1])):
                continue
            cu = (float(ch[0]) / n, float(ch[1]) / n)
            if (bool(_near_straight(float(e0[0]), float(e0[1]), 3.0, cu).any())
                    and bool(_near_straight(float(e1[0]), float(e1[1]),
                                            3.0, cu).any())):
                out.append({"cx": round(float(cx), 1),
                            "cy": round(float(cy), 1),
                            "r": round(float(r), 1),
                            "mx": round(float(_mid[0]), 1),
                            "my": round(float(_mid[1]), 1)})
    # dedupe coincident arcs (double-drawn swings)
    ded = []
    for o in out:
        if any(abs(o["cx"] - d["cx"]) < 4 and abs(o["cy"] - d["cy"]) < 4
               and abs(o["r"] - d["r"]) < 4 for d in ded):
            continue
        ded.append(o)
    return ded
