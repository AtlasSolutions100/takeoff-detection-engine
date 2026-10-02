"""Watch-list callouts: the few callouts an estimator wants pulled out by
keyword - septic tank, propane tank, culvert, area inlet, cleanout, wye -
each with the size printed beside it (gallons / inches) and its scope tag
(BY OTHERS, N.I.C., RE. TO MEP / OWTS / ...). Words only, no detect.

WHY A LIST AND NOT EVERY CALLOUT. A client (call 2026-09-15, the septic
and utility part): "so many call outs everywhere" - he rejected a full
callout dump and asked for a keyword watch-list with the tank size read off
the callout, septic and propane separated by type. The sheet callouts panel
already lists every leader; this block sits above it and names only these.

WHAT COUNTS AS A CALLOUT. A short block of text: lines stacked at one left
edge, each line at most 52 characters, at most six lines. A general note
("PROVIDE CLEANOUTS AT ALL DOWNSPOUT LOCATIONS THAT ARE ROUTED TO
UNDERGROUND STORM PIPE...") is long, a numbered note starts "12.", and a
legend key reads "C/O = CLEANOUT" - none of those is a thing on the plan,
so none counts. Measured on Res-A civil C2.0 (index 1): the tank callouts
wrap over two lines ("PROPOSED 1,500 GALLON" / "SEPTIC TANK; RE. TO OWTS"),
which is why blocks and not lines are matched.

A block counts ONCE for the first watch word it contains, in list order;
a block marked BY OTHERS or N.I.C. is counted and flagged, and `in_scope`
leaves it out, so the estimator sees both numbers. Nothing here enters the
takeoff by itself - the panel's "+ n EA" button does that, and says so.
"""
from __future__ import annotations

import re

WATCH = [
    {"key": "septic_tank", "label": "Septic Tank", "words": ["SEPTIC TANK"], "size": "gal"},
    {"key": "propane_tank", "label": "Propane Tank", "words": ["PROPANE TANK", "LP TANK", "LPG TANK"], "size": "gal"},
    {"key": "culvert", "label": "Culvert", "words": ["CULVERT"], "size": "in"},
    {"key": "area_inlet", "label": "Area Inlet", "words": ["AREA INLET", "AREA DRAIN", "YARD INLET", "YARD DRAIN"], "size": "in"},
    {"key": "cleanout", "label": "Cleanout", "words": ["CLEANOUT", "CLEAN OUT", "CLEAN-OUT"], "size": "in"},
    {"key": "wye", "label": "Wye", "words": ["WYE"], "size": "in"},
]
LINE_MAX = 52
BLOCK_LINES = 6
OUT_OF_SCOPE = ("by others", "N.I.C.")
_GAL = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)\s*(?:GAL\b|GALLONS?\b)")
# the 2" of a 7'-2" dimension is not a pipe size: no feet-and-inches tail
_IN = re.compile(r"(?<![\d.'’′-])(\d{1,2})\s*(?:\"|”|''|\bIN\.?\b|INCH)")
# a wrapped note's last line ("FOR ALL CLEANOUTS PRIOR TO INSTALLATION.")
# is a sentence fragment, not a thing on the plan
_FRAG_START = {"FOR", "AND", "TO", "OF", "THE", "WITH", "AT", "IN", "ON", "BY", "OR", "AS", "PRIOR", "SHALL", "ARE", "IS"}
_WAY = re.compile(r"\b(\d)[- ]WAY\b")
_TAG_BY = re.compile(r"\bBY OTHERS\b")
_TAG_NIC = re.compile(r"\bN\.?\s?I\.?\s?C\.?(?=\W|$)")
_TAG_RE = re.compile(r"\bRE\.?\s*(?:TO|:)\s+([A-Z][A-Z/&.]*(?:\s+[A-Z][A-Z/&.]*)?)")
_NUMBERED = re.compile(r"^\s*\d{1,2}\.\s")
_STOP = {"FOR", "PLANS", "PLAN", "DETAILS", "DETAIL", "AND", "&", "CONT.", "CONTINUATION"}


def _blocks(lines, max_chars=LINE_MAX, gap=1.7, dx=6.0):
    """Stack short lines that share a left edge and follow each other
    within a line pitch or so. Returns [[(x0, y0, x1, y1, text), ...]]."""
    L = sorted(((float(l[0]), float(l[1]), float(l[2]), float(l[3]), str(l[4]).strip())
                for l in lines if str(l[4]).strip()), key=lambda l: (l[1], l[0]))
    used = [False] * len(L)
    out = []
    for i, l in enumerate(L):
        if used[i] or len(l[4]) > max_chars:
            continue
        used[i] = True
        blk = [l]
        cur = l
        while len(blk) < BLOCK_LINES:
            h = max(cur[3] - cur[1], 6.0)
            nxt = None
            for j in range(i + 1, len(L)):
                m = L[j]
                if m[1] - cur[1] > gap * h:
                    break
                if used[j] or len(m[4]) > max_chars:
                    continue
                if abs(m[0] - cur[0]) <= dx and m[1] - cur[1] > 0.5 * h:
                    nxt = j
                    break
            if nxt is None:
                break
            used[nxt] = True
            blk.append(L[nxt])
            cur = L[nxt]
        out.append(blk)
    return out


def _tags(text: str) -> list:
    tags = []
    if _TAG_BY.search(text):
        tags.append("by others")
    if _TAG_NIC.search(text):
        tags.append("N.I.C.")
    for m in _TAG_RE.finditer(text):
        words = []
        for w in m.group(1).split():
            if w.upper().strip(".,;") in _STOP:
                break
            words.append(w.strip(".,;"))
        if words:
            t = "RE. TO " + " ".join(words)
            if t not in tags:
                tags.append(t)
    return tags


def _size(text: str, kind: str):
    """(value, unit) printed in the block, or (None, None); a qualifier
    like 2-WAY is returned as its own value for a cleanout."""
    if kind == "gal":
        m = _GAL.search(text)
        if m:
            return int(m.group(1).replace(",", "")), "gal"
        return None, None
    m = _WAY.search(text)
    if m:
        return int(m.group(1)), "way"
    m = _IN.search(text)
    if m:
        return int(m.group(1)), "in"
    return None, None


def _fmt(v, u) -> str:
    if u == "gal":
        return f"{v:,} gal"
    if u == "way":
        return f"{v}-way"
    return f'{v}"'


def find_watch_callouts(lines, watch=None) -> dict:
    """{"items": [{kind, label, text, size, size_unit, size_text, tags, x, y,
    lines}], "groups": [{kind, label, count, in_scope, sizes, tags}],
    "watch": [labels]} from a page's text lines (x0, y0, x1, y1, text)."""
    watch = watch or WATCH
    items = []
    seen = set()
    for blk in _blocks(lines):
        text = " ".join(l[4] for l in blk)
        up = text.upper()
        if " = " in up or _NUMBERED.match(blk[0][4]):
            continue        # a legend key or a numbered note, not a thing on the plan
        first = (up.split() or [""])[0].strip(".,;:")
        if first in _FRAG_START or " SHALL " in f" {up} ":
            continue        # spec language / a sentence fragment of a wrapped note
        hit = None
        for w in watch:
            if any(k in up for k in w["words"]):
                hit = w
                break
        if hit is None:
            continue
        x = round(min(l[0] for l in blk), 1)
        y = round(min(l[1] for l in blk), 1)
        key = (hit["key"], up, round(x / 3), round(y / 3))
        if key in seen:
            continue
        seen.add(key)
        v, u = _size(up, hit["size"])
        items.append({"kind": hit["key"], "label": hit["label"], "text": text[:160],
                      "size": v, "size_unit": u, "size_text": _fmt(v, u) if v is not None else None,
                      "tags": _tags(up), "x": x, "y": y, "lines": len(blk)})
    groups = []
    for w in watch:
        mine = [it for it in items if it["kind"] == w["key"]]
        if not mine:
            continue
        sizes: dict = {}
        tags: dict = {}
        out_of_scope = 0
        for it in mine:
            if it["size_text"]:
                sizes[it["size_text"]] = sizes.get(it["size_text"], 0) + 1
            for t in it["tags"]:
                tags[t] = tags.get(t, 0) + 1
            if any(t in OUT_OF_SCOPE for t in it["tags"]):
                out_of_scope += 1
        groups.append({"kind": w["key"], "label": w["label"], "count": len(mine),
                       "in_scope": len(mine) - out_of_scope, "sizes": sizes, "tags": tags})
    return {"items": items, "groups": groups, "watch": [w["label"] for w in watch]}
