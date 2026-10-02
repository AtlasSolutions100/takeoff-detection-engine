"""Page-to-page registration so evidence on one sheet can be used on another.

Three sources, in order of trust:
  typed   two (or more) point pairs the operator clicked on both sheets
  text    the same printed words (street names, lot numbers, coordinates)
          found on both sheets at consistent relative positions
  identity same sheet size, same drawing set: most grading sets plot the
          site in the same place on every sheet
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

import numpy as np

from .geom import similarity_from_pairs, apply_affine


def identity() -> list[list[float]]:
    return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def from_pairs(src_pts, dst_pts) -> list[list[float]]:
    M = similarity_from_pairs(src_pts, dst_pts)
    return M.tolist()


def matrix_stats(M) -> dict:
    M = np.asarray(M, dtype=float)
    a, b = M[0, 0], M[1, 0]
    scale = math.hypot(a, b)
    rot = math.degrees(math.atan2(b, a))
    return {"scale": round(scale, 5), "rotation_deg": round(rot, 3), "tx": round(float(M[0, 2]), 2), "ty": round(float(M[1, 2]), 2)}


_WORD = re.compile(r"^[A-Z][A-Z0-9'&\-\.]{3,}$")
# words a sheet set prints in the same place on every sheet (title block, border, legend, notes):
# they say nothing about where the DRAWING sits. Two neighbouring sheets share them all, and they
# used to "confirm the same frame" and stack the two sheets' contours on top of each other.
_FRAME_WORDS = {
    "SHEET", "SHEETS", "SCALE", "DATE", "DRAWN", "CHECKED", "DESIGNED", "APPROVED", "PROJECT", "REVISION", "REVISIONS",
    "ENGINEER", "ENGINEERS", "ENGINEERING", "ASSOCIATES", "CONSULTING", "CONSULTANTS", "GROUP", "SUITE", "PHONE", "FAX",
    "EMAIL", "SURVEY", "SURVEYOR", "SURVEYING", "LEGEND", "NOTES", "NOTE", "MATCH", "MATCHLINE", "LINE", "NORTH", "PLAN",
    "PLANS", "GRADING", "DRAINAGE", "TITLE", "FILE", "PAGE", "PHASE", "PRELIMINARY", "CONSTRUCTION", "CONTOUR", "CONTOURS",
    "EXISTING", "PROPOSED", "ELEVATION", "ELEVATIONS", "SPOT", "FEET", "INCH", "BENCHMARK", "BENCH", "MARK", "DATUM",
    "LICENSE", "SEAL", "SEALED", "COPYRIGHT", "COUNTY", "CITY", "STATE", "TEXAS", "UTAH", "SUBDIVISION", "UNIT", "BLOCK",
    "LOT", "LOTS", "STREET", "DRIVE", "ROAD", "LANE", "COURT", "VICINITY", "LOCATION", "INDEX", "NUMBER", "DESCRIPTION",
    "TYPICAL", "DETAIL", "DETAILS", "SECTION", "SECTIONS", "GENERAL", "SYMBOL", "SYMBOLS", "ABBREVIATIONS", "GRAPHIC",
    "HORIZONTAL", "VERTICAL", "FIRM", "REGISTRATION", "RELEASED", "ISSUED", "REVIEW", "PERMIT", "OWNER", "DEVELOPER",
    "CLIENT", "ARCHITECT", "LANDSCAPE", "CIVIL", "STRUCTURAL", "SITE", "SITEWORK", "IMPROVEMENTS", "ADDITION", "ACRES",
    "TRACT", "ABSTRACT", "CONTROL", "POINT", "POINTS", "COORDINATES", "GRID", "BEARING", "BEARINGS", "FINISHED",
    "FLOOR", "GRADE", "GRADES", "SLOPE", "CURB", "GUTTER", "FLOW", "DRAWING", "DRAWINGS", "ADDRESS", "PROFESSIONAL",
    "REGISTERED", "ENVIRONMENTAL", "WWW.", "INC.", "LLC", "PLLC", "P.E.", "R.P.L.S."}
_MATCHLINE = re.compile(r"MATCH\s*LINE", re.I)


def has_match_line(labels: list[dict]) -> bool:
    """Does the sheet print a match line note? Then a neighbour sheet continues the drawing
    beside it, and the two share their whole title block without sharing a frame."""
    return any(_MATCHLINE.search(str(l.get("text") or "")) for l in labels)


# the sheet's outer band, as a fraction of the labels' own extent: the title block strip and the
# notes column live there (subdivision C 3.1: a firm name / CHAIRMAN / SECRETARY at x >= 0.94 of the
# width, the notes column's words at x <= 0.07), the drawing does not
_BAND_X, _BAND_Y = 0.08, 0.06


def _in_band(labels: list[dict]):
    xs0 = [l["bbox"][0] for l in labels]; xs1 = [l["bbox"][2] for l in labels]
    ys0 = [l["bbox"][1] for l in labels]; ys1 = [l["bbox"][3] for l in labels]
    if not xs0:
        return lambda l: False
    x0, x1, y0, y1 = min(xs0), max(xs1), min(ys0), max(ys1)
    bx, by = (x1 - x0) * _BAND_X, (y1 - y0) * _BAND_Y

    def test(l):
        cx = (l["bbox"][0] + l["bbox"][2]) / 2; cy = (l["bbox"][1] + l["bbox"][3]) / 2
        return cx < x0 + bx or cx > x1 - bx or cy < y0 + by or cy > y1 - by
    return test


def text_anchor_pairs(labels_src: list[dict], labels_dst: list[dict]) -> list[tuple]:
    """Words printed exactly once on each sheet (not numbers, not tags, not the sheet frame's own
    words, not words in the sheet's outer band) -> point pairs."""
    def index(labels):
        d = defaultdict(list)
        band = _in_band(labels)
        for l in labels:
            t = l["text"].strip().upper()
            if not _WORD.match(t) or any(ch.isdigit() for ch in t[:1]) or t.rstrip(".") in _FRAME_WORDS or t in _FRAME_WORDS:
                continue
            if band(l):
                continue
            x0, y0, x1, y1 = l["bbox"]
            d[t].append(((x0 + x1) / 2, (y0 + y1) / 2))
        return {k: v[0] for k, v in d.items() if len(v) == 1}
    a = index(labels_src)
    b = index(labels_dst)
    common = sorted(set(a) & set(b))
    return [(a[k], b[k], k) for k in common]


def ransac_similarity(pairs: list[tuple], tol: float = 6.0, iters: int = 400, seed: int = 0) -> tuple[list[list[float]] | None, list]:
    """Fit a similarity from noisy word pairs; returns (matrix, inlier words)."""
    if len(pairs) < 2:
        return None, []
    rng = np.random.default_rng(seed)
    src = np.array([p[0] for p in pairs])
    dst = np.array([p[1] for p in pairs])
    best = (None, [])
    n = len(pairs)
    for _ in range(iters if n > 2 else 1):
        i, j = (rng.choice(n, 2, replace=False) if n > 2 else (0, 1))
        try:
            M = similarity_from_pairs(src[[i, j]], dst[[i, j]])
        except ValueError:
            continue
        st = matrix_stats(M)
        if not (0.2 <= st["scale"] <= 5.0):
            continue
        pred = apply_affine(M, src)
        err = np.linalg.norm(pred - dst, axis=1)
        inl = np.nonzero(err <= tol)[0]
        if len(inl) > len(best[1]):
            best = (M, list(inl))
    if best[0] is None or len(best[1]) < 2:
        return None, []
    inl = best[1]
    M = similarity_from_pairs(src[inl], dst[inl])
    return M.tolist(), [pairs[k][2] for k in inl]


def auto_align(labels_src: list[dict], labels_dst: list[dict], same_size: bool) -> dict:
    pairs = text_anchor_pairs(labels_src, labels_dst)
    M, words = ransac_similarity(pairs)
    if M is not None and len(words) >= 3:
        st = matrix_stats(M)
        near_identity = abs(st["scale"] - 1) < 0.003 and abs(st["rotation_deg"]) < 0.2 and abs(st["tx"]) < 2 and abs(st["ty"]) < 2
        if near_identity and same_size:
            return {"matrix": identity(), "source": "identity", "note": f"{len(words)} shared words confirm the same frame", "words": words}
        return {"matrix": M, "source": "text", "note": f"fit on {len(words)} shared words", "words": words, **st}
    if same_size:
        if has_match_line(labels_src) or has_match_line(labels_dst):
            # neighbours along a match line: same size, the same title block, a different frame
            return {"matrix": identity(), "source": "unaligned", "words": [],
                    "note": "the sheets meet at a match line: they continue each other and do not share a frame - "
                            "click two matching points, or keep the limit on one sheet"}
        return {"matrix": identity(), "source": "identity", "note": "same sheet size; no shared words found to check", "words": []}
    return {"matrix": identity(), "source": "unaligned", "note": "different sheet sizes and no shared words: click two matching points", "words": []}
