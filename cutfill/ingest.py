"""PDF ingestion with PyMuPDF.

Everything this module returns is in ROTATION-AWARE page coordinates (points,
origin top-left, y down) so that vectors, text and rendered images all agree.
PyMuPDF's get_drawings() returns raw, unrotated coordinates; we map them
through page.rotation_matrix here so nobody downstream has to remember.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pymupdf


@dataclass
class Stroke:
    pts: np.ndarray            # (n,2) page points, rotation-aware
    color: tuple               # stroke rgb 0..1
    width: float
    layer: str
    closed: bool
    dash: bool                 # PDF-level dash array present
    opacity: float
    filled: bool               # the path is FILLED ONLY (no outline): a patch, a solid glyph - not linework
    idx: int = -1              # index into the page stroke list
    also_filled: bool = False  # the path is stroked AND filled (type "fs"): linework to the reader,
                               # a solid shape to anything that draws it again (a word for the OCR)

    @property
    def length(self) -> float:
        d = np.diff(self.pts, axis=0)
        return float(np.sqrt((d * d).sum(axis=1)).sum()) if len(self.pts) > 1 else 0.0

    @property
    def gray(self) -> float:
        r, g, b = self.color
        return 0.299 * r + 0.587 * g + 0.114 * b


@dataclass
class TextLine:
    text: str
    bbox: tuple                # x0,y0,x1,y1 rotation-aware
    dir: tuple                 # writing direction (cos, sin), page-rotation aware
    size: float
    color: tuple               # rgb 0..1
    words: list = field(default_factory=list)   # [(text, bbox)]
    len_along: float = 0.0     # extent along the baseline (from the character boxes)
    height: float = 0.0        # extent across the baseline

    @property
    def center(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        return (0.5 * (x0 + x1), 0.5 * (y0 + y1))


@dataclass
class PageInfo:
    index: int
    width: float
    height: float
    rotation: int
    label: str = ""            # printed sheet number if we can find one


def _int_to_rgb(c: int) -> tuple:
    return (((c >> 16) & 255) / 255.0, ((c >> 8) & 255) / 255.0, (c & 255) / 255.0)


def _bezier(p0, p1, p2, p3, n: int = 8) -> list:
    out = []
    for i in range(1, n + 1):
        t = i / n
        mt = 1 - t
        x = mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t ** 3 * p3[0]
        y = mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t ** 3 * p3[1]
        out.append((x, y))
    return out


class Document:
    """Thin wrapper around a pymupdf document with cached per-page extraction."""

    def __init__(self, path: str):
        self.path = path
        self.doc = pymupdf.open(path)

    def close(self) -> None:
        """Release the file handle (the host deletes the PDF after this)."""
        try:
            self.doc.close()
        except Exception:
            pass

    def __len__(self):
        return len(self.doc)

    def page_info(self, i: int) -> PageInfo:
        pg = self.doc[i]
        r = pg.rect
        return PageInfo(index=i, width=float(r.width), height=float(r.height), rotation=int(pg.rotation),
                        label=self.sheet_label(i))

    def sheet_label(self, i: int) -> str:
        """Best-effort printed sheet number: the largest text in the bottom-right title block."""
        pg = self.doc[i]
        r = pg.rect
        best = ("", 0.0)
        try:
            d = pg.get_text("dict", clip=pymupdf.Rect(r.x0 + 0.8 * r.width, r.y0 + 0.75 * r.height, r.x1, r.y1))
        except Exception:
            return ""
        import re
        for b in d.get("blocks", []):
            for l in b.get("lines", []):
                t = "".join(s["text"] for s in l["spans"]).strip()
                if not t or len(t) > 8:
                    continue
                if not re.match(r"^[A-Z]{0,3}[-\s]?\d+(\.\d+)?[A-Z]?$", t):
                    continue
                size = max(s["size"] for s in l["spans"])
                if size > best[1]:
                    best = (t, size)
        return best[0]

    # ---- rendering -------------------------------------------------------
    def render(self, i: int, dpi: float = 110, clip: tuple | None = None, gray: bool = False) -> bytes:
        pg = self.doc[i]
        kw = {"dpi": int(round(dpi))}
        if clip is not None:
            kw["clip"] = pymupdf.Rect(*clip)
        if gray:
            kw["colorspace"] = pymupdf.csGRAY
        pix = pg.get_pixmap(**kw)
        return pix.tobytes("png")

    def render_array(self, i: int, dpi: float, clip: tuple | None = None) -> np.ndarray:
        """Grayscale uint8 array of the page (or clip) at dpi."""
        pg = self.doc[i]
        kw = {"dpi": int(round(dpi)), "colorspace": pymupdf.csGRAY}
        if clip is not None:
            kw["clip"] = pymupdf.Rect(*clip)
        pix = pg.get_pixmap(**kw)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        return arr[:, :, 0].copy()

    def render_rgb_array(self, i: int, dpi: float, clip: tuple | None = None) -> np.ndarray:
        pg = self.doc[i]
        kw = {"dpi": int(round(dpi)), "colorspace": pymupdf.csRGB}
        if clip is not None:
            kw["clip"] = pymupdf.Rect(*clip)
        pix = pg.get_pixmap(**kw)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        return arr[:, :, :3].copy()

    # ---- vectors -----------------------------------------------------------
    def strokes(self, i: int) -> list[Stroke]:
        pg = self.doc[i]
        M = pg.rotation_matrix
        out: list[Stroke] = []
        try:
            drawings = pg.get_drawings(extended=True)
        except TypeError:
            drawings = pg.get_drawings()

        def tp(p):
            q = pymupdf.Point(p) * M
            return (q.x, q.y)

        for d in drawings:
            typ = d.get("type")
            if typ not in ("s", "fs", "f"):
                continue
            items = d.get("items") or []
            if not items:
                continue
            stroked = typ in ("s", "fs")
            filled = typ in ("fs", "f")
            color = d.get("color") if stroked else None
            if color is None:
                color = d.get("fill") or (0.0, 0.0, 0.0)
            color = tuple(float(c) for c in color[:3]) if len(color) >= 3 else (float(color[0]),) * 3
            width = float(d.get("width") or 0.0)
            layer = d.get("layer") or ""
            dashes = d.get("dashes") or ""
            dash = bool(dashes) and dashes.strip() not in ("[] 0", "[ ] 0", "")
            opacity = float(d.get("stroke_opacity", 1.0) if stroked else d.get("fill_opacity", 1.0) or 1.0)
            closed = bool(d.get("closePath"))
            # walk items into continuous polylines
            polys: list[list] = []
            cur: list = []
            last = None
            for it in items:
                kind = it[0]
                if kind == "l":
                    a, b = tp(it[1]), tp(it[2])
                    if last is None or abs(a[0] - last[0]) > 1e-6 or abs(a[1] - last[1]) > 1e-6:
                        if len(cur) > 1:
                            polys.append(cur)
                        cur = [a]
                    cur.append(b)
                    last = b
                elif kind == "c":
                    a = tp(it[1])
                    if last is None or abs(a[0] - last[0]) > 1e-6 or abs(a[1] - last[1]) > 1e-6:
                        if len(cur) > 1:
                            polys.append(cur)
                        cur = [a]
                    pts = _bezier(a, tp(it[2]), tp(it[3]), tp(it[4]))
                    cur.extend(pts)
                    last = pts[-1]
                elif kind == "re":
                    r = it[1]
                    corners = [tp((r.x0, r.y0)), tp((r.x1, r.y0)), tp((r.x1, r.y1)), tp((r.x0, r.y1))]
                    if len(cur) > 1:
                        polys.append(cur)
                    cur = corners + [corners[0]]
                    polys.append(cur)
                    cur = []
                    last = None
                elif kind == "qu":
                    q = it[1]
                    corners = [tp(q.ul), tp(q.ur), tp(q.lr), tp(q.ll)]
                    if len(cur) > 1:
                        polys.append(cur)
                    cur = corners + [corners[0]]
                    polys.append(cur)
                    cur = []
                    last = None
            if len(cur) > 1:
                polys.append(cur)
            for poly in polys:
                pts = np.asarray(poly, dtype=float)
                # drop consecutive duplicates
                keep = np.ones(len(pts), dtype=bool)
                keep[1:] = np.any(np.abs(np.diff(pts, axis=0)) > 1e-6, axis=1)
                pts = pts[keep]
                if len(pts) < 2:
                    continue
                is_closed = closed or (len(pts) > 3 and np.allclose(pts[0], pts[-1], atol=1e-6))
                out.append(Stroke(pts=pts, color=color, width=width, layer=layer, closed=is_closed,
                                  dash=dash, opacity=opacity, filled=filled and not stroked,
                                  also_filled=filled and stroked))
        for k, s in enumerate(out):
            s.idx = k
        return out

    # ---- text --------------------------------------------------------------
    def text_lines(self, i: int) -> list[TextLine]:
        pg = self.doc[i]
        out: list[TextLine] = []
        try:
            d = pg.get_text("rawdict")
        except Exception:
            return out
        # get_text() boxes are in the UNROTATED page frame while every stroke goes through
        # page.rotation_matrix (strokes() above): on a /Rotate 270 sheet (most of the client
        # civil sets) every text label sat a page-width away from its line, wall grades were
        # dropped as "outside the contoured area" and heights attached to the wrong contours.
        # Map boxes, character boxes and the writing direction through the same matrix, once.
        M = pg.rotation_matrix

        def tbox(bb):
            xs, ys = [], []
            for px, py in ((bb[0], bb[1]), (bb[2], bb[1]), (bb[0], bb[3]), (bb[2], bb[3])):
                q = pymupdf.Point(px, py) * M
                xs.append(q.x); ys.append(q.y)
            return (min(xs), min(ys), max(xs), max(ys))

        def tdir(dx, dy):
            ux = M.a * dx + M.c * dy
            uy = M.b * dx + M.d * dy
            n = (ux * ux + uy * uy) ** 0.5 or 1.0
            return ux / n, uy / n

        for b in d.get("blocks", []):
            if b.get("type", 0) != 0:
                continue
            for l in b.get("lines", []):
                spans = l.get("spans", [])
                text = "".join("".join(ch["c"] for ch in s.get("chars", [])) for s in spans).strip()
                if not text:
                    continue
                size = max(float(s["size"]) for s in spans)
                col = _int_to_rgb(int(spans[0].get("color", 0)))
                dr = l.get("dir", (1.0, 0.0))
                x0, y0, x1, y1 = tbox(l["bbox"])
                words = []
                for s in spans:
                    words.append(("".join(ch["c"] for ch in s.get("chars", [])), tbox(s["bbox"])))
                # rotated text: measure along the writing direction from the character boxes
                ux, uy = tdir(float(dr[0]), float(dr[1]))
                ts = []
                for s in spans:
                    for ch in s.get("chars", []):
                        cx0, cy0, cx1, cy1 = tbox(ch["bbox"])
                        for px, py in ((cx0, cy0), (cx1, cy0), (cx0, cy1), (cx1, cy1)):
                            ts.append(px * ux + py * uy)
                len_along = (max(ts) - min(ts)) if ts else float(x1 - x0)
                height = size * 0.78
                out.append(TextLine(text=text, bbox=(float(x0), float(y0), float(x1), float(y1)),
                                    dir=(ux, uy), size=size, color=col, words=words,
                                    len_along=float(len_along), height=float(height)))
        return out

    def words(self, i: int) -> list[tuple]:
        return self.doc[i].get_text("words")

    def full_text(self, i: int) -> str:
        try:
            return self.doc[i].get_text()
        except Exception:
            return ""
