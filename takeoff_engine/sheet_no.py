"""The sheet number a page's title block prints, for the host's page list, sheet heading and report header.

A refinement of detect_page.read_sheet_number, kept in its own module ON PURPOSE: detect_page.py is one of the files
engine_sig hashes, and editing it would mark every detected page of every job out of date (Detect again, ~30 s a
page). This module is not in engine_sig; the detection result keeps its own `sheet_no` as it was read, and the host
prefers this reading (server.py _page_sheet_no, via the triage sidecar).

What changed, and why (subdivision A sheet 3.0, 2026-09-28): the old ranking puts ANY lettered token ahead of a
number, so "LOT910" - a lot label in the plan body, 13.5 pt, inside the bottom-right 30 % - beat the title block's
"3.0" (16.1 pt). Here, tokens that are never a sheet number are refused before the ranking:
  - a lot or block label ("LOT910", "BLK12");
  - the "OF" of a sheet count: "2 OF 6" read as "OF62" (the third critic's m5).
The ranking itself is the old reader's: lettered first, then the largest, then nearest the corner. A first version
let the largest win outright; the fourth critic (r8) showed a spot grade "60.25" at 16 pt then beat "C3.0" at 14 pt.
On the plan collection (6,946 pages) the two rankings gave the same answers; this one only changes a page where the
old reader picked a refused token: subdivision A p0 ("3.0") and a sewer set 466-470 (abstain). Everything else is the old
reader's too: the corner (x and y past 70 % of the sheet, rotation-aware), the token shapes, the 8 pt floor, the
abstain."""
from __future__ import annotations

import math
import re

import pymupdf

from .detect_page import _SHEET_MIN_PT, _SHEET_TOKEN, _SHEET_TOKEN_NUM

_NOT_SHEET = re.compile(r"^(LOT|BLK|OF)\d")    # lot and block labels of the plan body, the OF of "2 OF 6"


def read_sheet_number(page) -> "str | None":
    """The sheet number this page's title block prints, or None (see the module docstring)."""
    rect = page.rect
    W, H = rect.width, rect.height
    if W <= 0 or H <= 0:
        return None
    rot = page.rotation_matrix if page.rotation else None
    try:
        blocks = page.get_text("dict").get("blocks", [])
    except Exception:
        return None

    def display_center(bbox):
        x0, y0, x1, y1 = bbox
        if rot is not None:
            a = pymupdf.Point(x0, y0) * rot
            b = pymupdf.Point(x1, y1) * rot
            x0, x1 = sorted((a.x, b.x))
            y0, y1 = sorted((a.y, b.y))
        return (x0 + x1) / 2.0, (y0 + y1) / 2.0

    cands = []  # (lettered, size_pt, -corner_dist, token): the old reader's order

    def consider(text, size, bbox):
        t = "".join(text.split()).upper()
        if not t or len(t) > 10 or size < _SHEET_MIN_PT or _NOT_SHEET.match(t):
            return
        lettered = bool(_SHEET_TOKEN.match(t))
        if not lettered and not _SHEET_TOKEN_NUM.match(t):
            return
        cx, cy = display_center(bbox)
        if cx < 0.70 * W or cy < 0.70 * H:
            return
        d = math.hypot((W - cx) / W, (H - cy) / H)
        cands.append((lettered, size, -d, t))

    for b in blocks:
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            spans = ln.get("spans", [])
            for sp in spans:
                consider(sp.get("text", ""), sp.get("size", 0.0), sp.get("bbox", (0, 0, 0, 0)))
            if len(spans) > 1:
                joined = "".join(sp.get("text", "") for sp in spans)
                bb = [min(sp["bbox"][0] for sp in spans), min(sp["bbox"][1] for sp in spans),
                      max(sp["bbox"][2] for sp in spans), max(sp["bbox"][3] for sp in spans)]
                consider(joined, max(sp.get("size", 0.0) for sp in spans), bb)
    if not cands:
        return None
    # detect_page.read_sheet_number's own pick, line for line
    lettered = [c for c in cands if c[0]]
    pool = lettered or cands
    best = max(c[1] for c in pool)
    # 0.5 pt: same-cell text repeats at identical size; distinct cells differ by whole points
    top = [c for c in pool if c[1] >= best - 0.5]
    return max(top, key=lambda c: c[2])[3]
