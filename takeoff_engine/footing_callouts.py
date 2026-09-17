"""Footing callouts and the footing schedule, read off a structural set.

What the sheets print (Beaumont's Barn / Guide structural sets by IMEG,
Jeff Ellis's Van Sickle set; measured 2026-09-16):

  * A FOOTING MARK next to each wall or pad on the foundation plan:
    ``CF2.0`` / ``CF3.0`` (continuous footing, the digits are the width in
    feet), ``F5.5`` / ``F10.0x6.0`` (spread footing, square or L x W),
    Ellis's ``F-24`` (a wall footing 24 in wide) and ``S-36`` (a 36 in
    square pad). Beaumont reads the mark for the trench floor width
    ("CF3.0 vs CF7.5 doubles the dig", call 2026-09-15 28:00-31:17).
  * A PARENTHETICAL ELEVATION beside the mark, ``(86'-0")``: the bottom of
    that footing on the architectural datum (100'-0" = main level subfloor,
    the sheet's own FOUNDATION NOTES). A SQUARE-BRACKET elevation,
    ``[87'-0"]``, is the top of the wall. ``EL (87'-0") T.O. SLAB`` names a
    slab. Nothing here converts the datum - that is a per-structure input.
  * A FOOTING SCHEDULE on the notes sheet: rows ``F3.0 | 3'-0" | 3'-0" |
    1'-0" | (3)#5 BOTTOM ...`` (spread: length, width, thickness) and
    ``CF3.0 | 3'-0" | 1'-0" | (3)#5 BOTTOM | #5@12"`` (continuous: width,
    thickness), often two sub-tables side by side on one row band; Ellis
    prints inches (``S-42 | 42" | 42" | 10" | (5) #4 BARS EACH WAY``).

Everything here is a SUGGESTION with its evidence attached (the word, the
distance, the schedule page): the structure panel shows it as a chip and
the operator's typed number always wins. A mark whose schedule row was not
found falls back to its own digits and says so (``from: "mark digits"``).

Dependency-free beyond the standard library so it can be unit-tested
without a PDF; the server joins it to page words and caches per job.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

MARK_RE = re.compile(
    r"^(?P<pre>CF|WF|SF|PF|F|S|P)\s?-?(?P<n1>\d{1,2}(?:\.\d)?)"
    r"(?:[xX](?P<n2>\d{1,2}(?:\.\d)?))?$")
# 86'-0"  7'-6 1/2"  2'-6"  1'-0"  (86'-0")  [87'-0"]  42"  10"
# (the foot and inch marks come in three glyphs each: ASCII, typographic
# quotes and the prime characters some fonts export)
_FTM = "['’′]"
_INM = '["”″]'
_FTIN = (r"(?P<ft>\d{1,4})" + _FTM + r"\s*-?\s*(?:(?P<in>\d{1,2})(?:\s?(?P<fn>\d)/(?P<fd>\d))?)?" + _INM + r"?"
         r"|(?P<inch>\d{1,3})(?:\s?(?P<ifn>\d)/(?P<ifd>\d))?" + _INM)
FTIN_RE = re.compile(r"^\s*(?:" + _FTIN + r")\s*$")
PAREN_RE = re.compile(r"^\(\s*(?:" + _FTIN + r")\s*\)$")
BRACK_RE = re.compile(r"^\[\s*(?:" + _FTIN + r")\s*\]?$")
_SLAB_WORDS = ("T.O.", "T.O", "TO", "TOS", "SLAB")


def parse_ft_in(text: str):
    """``2'-6"`` -> 2.5, ``42"`` -> 3.5, ``(86'-0")`` -> 86.0, ``[87'-0"]`` -> 87.0;
    None when the token is not a feet-inch or inch dimension."""
    s = str(text or "").strip()
    m = FTIN_RE.match(s) or PAREN_RE.match(s) or BRACK_RE.match(s)
    if not m:
        return None
    if m.group("ft") is not None:
        v = float(m.group("ft"))
        if m.group("in"):
            inch = float(m.group("in"))
            if m.group("fn"):
                inch += float(m.group("fn")) / float(m.group("fd") or 1)
            v += inch / 12.0
        return v
    inch = float(m.group("inch"))
    if m.group("ifn"):
        inch += float(m.group("ifn")) / float(m.group("ifd") or 1)
    return inch / 12.0


def parse_mark(text: str):
    """``CF3.0`` -> {"mark": "CF3.0", "prefix": "CF", "n1": 3.0, "n2": None};
    ``F-24`` -> n1 24 (inches, said by `unit`); None when not a mark."""
    s = str(text or "").strip().upper()
    m = MARK_RE.match(s)
    if not m:
        return None
    n1 = float(m.group("n1"))
    n2 = float(m.group("n2")) if m.group("n2") else None
    # a dash and a whole number of 12 or more reads as inches (F-24, S-36);
    # a decimal reads as feet (CF3.0, F5.5)
    unit = "in" if ("-" in s and "." not in s and n1 >= 12) else "ft"
    # the mark as the sheet prints it (F10.0x6.0 keeps its lowercase x),
    # spaces dropped so "CF 3.0" and "CF3.0" are one mark
    dash = "-" if "-" in s else ""
    mark = m.group("pre") + dash + m.group("n1") + ("x" + m.group("n2") if m.group("n2") else "")
    return {"mark": mark, "prefix": m.group("pre"), "n1": n1,
            "n2": n2, "unit": unit}


def mark_fallback(mark: str) -> dict | None:
    """What the mark's own digits say when no schedule row is found -
    always flagged ``from: "mark digits"`` so the chip can say so."""
    m = parse_mark(mark)
    if not m:
        return None
    k = 12.0 if m["unit"] == "in" else 1.0
    w = m["n1"] / k
    out = {"mark": m["mark"], "width_ft": round(w, 3), "length_ft": None,
           "depth_ft": None, "kind": None, "from": "mark digits"}
    if m["n2"] is not None:
        out["length_ft"] = round(m["n1"] / k, 3)
        out["width_ft"] = round(m["n2"] / k, 3)
        out["kind"] = "spread"
    elif m["prefix"] in ("CF", "WF"):
        out["kind"] = "continuous"
    elif m["prefix"] in ("S", "SF", "P", "PF"):
        out["kind"] = "spread"
        out["length_ft"] = out["width_ft"]
    return out


def _cx(w):
    return (float(w[0]) + float(w[2])) / 2.0


def _cy(w):
    return (float(w[1]) + float(w[3])) / 2.0


def _txt(w):
    return str(w[4]).strip()


def find_footing_callouts(words: list, max_d_pt: float = 60.0) -> dict:
    """Every footing mark on a page with the elevations printed beside it.

    Returns {"callouts": [{mark, prefix, x, y, bof_ft, bof_text, bof_d,
                           tow_ft, tow_text}, ...],
             "slabs": [{elev_ft, text, x, y}],
             "by_mark": {mark: {"count": n, "bof": {"86.0": k, ...}}}}
    A mark takes the NEAREST parenthetical elevation within max_d_pt and the
    nearest bracketed one; a label further than that is left unpaired
    rather than guessed. Distances are page points.
    """
    marks, parens, bracks, slabs = [], [], [], []
    for i, w in enumerate(words):
        t = _txt(w)
        if parse_mark(t):
            marks.append((i, w))
        elif PAREN_RE.match(t):
            parens.append((i, w))
        elif BRACK_RE.match(t):
            bracks.append((i, w))
    # EL (87'-0") T.O. SLAB: a parenthetical elevation with EL before it and
    # T.O. / SLAB within reach
    for i, w in parens:
        x, y = _cx(w), _cy(w)
        # the tag may sit beside the number or on the line under it
        # (Barn S1-1.3 stacks "EL (87'-0\")" over "T.O. SLAB")
        nb = [v for v in words if ((_cx(v) - x) ** 2 + (_cy(v) - y) ** 2) ** 0.5 <= 48.0]
        tags = {_txt(v).upper().rstrip(".") for v in nb}
        if ("EL" in tags or "ELEV" in tags) and ("SLAB" in tags or "T.O" in tags or "TOS" in tags):
            slabs.append({"elev_ft": parse_ft_in(_txt(w)), "text": _txt(w),
                          "x": round(x, 1), "y": round(y, 1)})
    slab_ids = {(s["x"], s["y"]) for s in slabs}

    def nearest(pool, x, y):
        best = None
        for _, v in pool:
            if (round(_cx(v), 1), round(_cy(v), 1)) in slab_ids:
                continue
            d = ((_cx(v) - x) ** 2 + (_cy(v) - y) ** 2) ** 0.5
            if d <= max_d_pt and (best is None or d < best[0]):
                best = (d, v)
        return best

    out = []
    for _, w in marks:
        m = parse_mark(_txt(w))
        x, y = _cx(w), _cy(w)
        row = {"mark": m["mark"], "prefix": m["prefix"], "x": round(x, 1), "y": round(y, 1),
               "bof_ft": None, "bof_text": None, "bof_d": None,
               "tow_ft": None, "tow_text": None}
        b = nearest(parens, x, y)
        if b:
            row["bof_ft"] = parse_ft_in(_txt(b[1]))
            row["bof_text"] = _txt(b[1])
            row["bof_d"] = round(b[0], 1)
        t = nearest(bracks, x, y)
        if t:
            row["tow_ft"] = parse_ft_in(_txt(t[1]))
            row["tow_text"] = _txt(t[1])
        out.append(row)
    by_mark: dict = {}
    for r in out:
        d = by_mark.setdefault(r["mark"], {"count": 0, "bof": Counter(), "unpaired": 0})
        d["count"] += 1
        if r["bof_ft"] is None:
            d["unpaired"] += 1
        else:
            d["bof"][f"{r['bof_ft']:g}"] += 1
    for d in by_mark.values():
        d["bof"] = dict(sorted(d["bof"].items(), key=lambda kv: (-kv[1], kv[0])))
    return {"callouts": out, "slabs": slabs, "by_mark": by_mark}


def read_footing_schedule(words: list, y_tol: float = 5.0) -> dict:
    """The footing schedule as rows keyed by mark.

    A schedule is recognised by its MARK COLUMN: two or more mark words
    stacked at one x (within 8 pt). Each mark's row is the words on its
    line to the right, up to the next mark column (two sub-tables share a
    row band on Barn S0-0.4: F... on the left, CF... on the right) or 420
    pt. Dimension tokens in order are length, width, thickness for a
    spread footing (three) or width, thickness for a continuous one (two);
    tokens with '#' are the reinforcing, kept as printed.

    Returns {"rows": {mark: {kind, length_ft, width_ft, depth_ft, reinf,
                             from: "schedule"}}, "found": bool,
             "columns": n}
    """
    out = {"rows": {}, "found": False, "columns": 0}
    marks = [w for w in words if parse_mark(_txt(w))]
    if len(marks) < 2:
        return out
    # columns: mark words sharing an x0 within 18 pt, at least two deep.
    # 18, not 8: on Barn S0-0.4 the three F10.0xN.0 rows start 11 pt left
    # of the other F rows (a wider mark, the same column) and an 8 pt rule
    # made them a column of their own whose rows ended 4 pt later.
    cols: list = []
    for w in sorted(marks, key=lambda w: float(w[0])):
        for c in cols:
            if abs(c["x0"] - float(w[0])) <= 18.0:
                c["words"].append(w)
                break
        else:
            cols.append({"x0": float(w[0]), "words": [w]})
    cols = [c for c in cols if len(c["words"]) >= 2]
    if not cols:
        return out
    cols.sort(key=lambda c: c["x0"])
    out["columns"] = len(cols)
    xs = [c["x0"] for c in cols]
    for ci, c in enumerate(cols):
        right = xs[ci + 1] - 4.0 if ci + 1 < len(xs) else c["x0"] + 420.0
        for w in c["words"]:
            m = parse_mark(_txt(w))
            y = _cy(w)
            row = [v for v in words if v is not w and abs(_cy(v) - y) <= y_tol
                   and float(v[0]) > float(w[2]) - 1.0 and float(v[0]) < right]
            row.sort(key=lambda v: float(v[0]))
            dims, reinf, seen_reinf = [], [], False
            for v in row:
                t = _txt(v)
                if not seen_reinf and parse_ft_in(t) is not None and not t.startswith(("(", "[")):
                    dims.append(parse_ft_in(t))
                    continue
                if "#" in t or seen_reinf:
                    seen_reinf = True
                    reinf.append(t)
            if not dims:
                continue
            fb = mark_fallback(m["mark"]) or {}
            if len(dims) >= 3:
                rec = {"kind": "spread", "length_ft": dims[0], "width_ft": dims[1], "depth_ft": dims[2]}
            elif len(dims) == 2:
                kind = "continuous" if fb.get("kind") != "spread" else "spread"
                rec = ({"kind": "continuous", "length_ft": None, "width_ft": dims[0], "depth_ft": dims[1]}
                       if kind == "continuous" else
                       {"kind": "spread", "length_ft": dims[0], "width_ft": dims[0], "depth_ft": dims[1]})
            else:
                rec = {"kind": fb.get("kind"), "length_ft": None, "width_ft": dims[0], "depth_ft": None}
            rec.update({"reinf": " ".join(reinf).strip() or None, "from": "schedule",
                        "mark": m["mark"], "y": round(y, 1)})
            out["rows"].setdefault(m["mark"], rec)
    out["found"] = bool(out["rows"])
    return out


def join_schedule(callouts: dict, schedule: dict) -> dict:
    """Attach the schedule row (or the mark-digits fallback, flagged) to each
    mark in callouts["by_mark"]; returns the enriched by_mark map."""
    rows = (schedule or {}).get("rows") or {}
    for mark, d in callouts.get("by_mark", {}).items():
        d["schedule"] = rows.get(mark) or mark_fallback(mark)
    return callouts["by_mark"]


# ---- walls grouped by bottom of footing ---------------------------------------------
# Beaumont colours every wall by the elevation its footing bottoms at (the
# Bluebeam legend on Barn S1-1.3: cyan 96'-98', red 85'-2", magenta 84'-2",
# blue 83'-0") because the dig depth differs per group. Two readings of the
# same idea, both suggestions, one rule: a line takes the callouts within
# reach of it; one callout claims the whole line, several split it between
# their projections - at a WALL STEP word printed between them, else
# halfway - and a line beside no callout stays unassigned, said in feet.
#   * SHEET-WIDE: every plan chain, piece by piece; the family whose lines
#     the callouts sit on most often is the footing outline, and its length
#     per group is reported AS DRAWN (a footing drawn as a closed rectangle
#     counts both long sides - said on the row, never halved silently).
#   * ON THE OUTLINE the operator drew: edge by edge, so the pieces add up
#     to the outline's perimeter exactly - the split of the wall the dig
#     formula runs on.
# Whole-chain-to-nearest-callout was the first cut; measured on S1-1.3
# against Beaumont's own colour bands it sent his 64.6 ft red (85'-2")
# stroke whole to 84'-2" because its legs run down past the 84'-2" callouts.
GROUP_COLOURS = ["#00c8ff", "#ff2d2d", "#ff3df0", "#2d5bff", "#19c46a", "#ff9b1f", "#9b5cff", "#a06a2c"]


def _poly_dist(px, py, pts):
    """Distance from a point to a polyline (page pt)."""
    return _project(px, py, pts, None)[1]


def _cum(pts):
    """Arc length at each vertex of a polyline."""
    out = [0.0]
    for i in range(len(pts) - 1):
        out.append(out[-1] + ((float(pts[i + 1][0]) - float(pts[i][0])) ** 2
                              + (float(pts[i + 1][1]) - float(pts[i][1])) ** 2) ** 0.5)
    return out


def _project(px, py, pts, cum):
    """(arc-length position of the closest point, distance) of a point on a
    polyline; `cum` from _cum (computed here when None)."""
    if cum is None:
        cum = _cum(pts)
    n = len(pts)
    if n == 1:
        return 0.0, ((float(pts[0][0]) - px) ** 2 + (float(pts[0][1]) - py) ** 2) ** 0.5
    best_d, best_s = float("inf"), 0.0
    for i in range(n - 1):
        ax, ay = float(pts[i][0]), float(pts[i][1])
        bx, by = float(pts[i + 1][0]), float(pts[i + 1][1])
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 <= 1e-12 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
        qx, qy = ax + t * dx, ay + t * dy
        d = ((qx - px) ** 2 + (qy - py) ** 2) ** 0.5
        if d < best_d:
            best_d, best_s = d, cum[i] + t * (cum[i + 1] - cum[i])
    return best_s, best_d


def _point_at(pts, cum, s_):
    """The point at arc length s_ on a polyline."""
    if s_ <= 0:
        return float(pts[0][0]), float(pts[0][1])
    for i in range(len(pts) - 1):
        if s_ <= cum[i + 1]:
            seg = cum[i + 1] - cum[i]
            t = 0.0 if seg <= 1e-12 else (s_ - cum[i]) / seg
            return (float(pts[i][0]) + t * (float(pts[i + 1][0]) - float(pts[i][0])),
                    float(pts[i][1]) + t * (float(pts[i + 1][1]) - float(pts[i][1])))
    return float(pts[-1][0]), float(pts[-1][1])


def _pieces_along(pts, calls, steps, reach_pt):
    """Split a polyline between the callouts within `reach_pt` of it.
    Returns [(bof_ft or None, length_pt, s0, s1)] covering the whole
    polyline in arc length. No callout in reach -> one unassigned piece;
    one -> the whole line; several -> every point of the line belongs to
    the NEAREST of them by true distance (so a callout 0.6 ft off the wall
    beats one 9 ft away across the room), the boundaries found by
    bisection, then each boundary between two elevations snapped to the
    WALL STEP word printed between those two callouts (the step nearest
    the boundary, within reach of the line). Adjacent pieces of one
    elevation merge."""
    cum = _cum(pts)
    L = cum[-1]
    if L <= 1e-9:
        return []
    cand = []
    for c in calls:
        s_, d = _project(c["x"], c["y"], pts, cum)
        if d <= reach_pt:
            cand.append((s_, d, round(float(c["bof_ft"]), 3), float(c["x"]), float(c["y"])))
    if not cand:
        return [(None, L, 0.0, L)]
    if len(cand) == 1:
        return [(cand[0][2], L, 0.0, L)]

    def dist(k, s_):
        x, y = _point_at(pts, cum, s_)
        return ((x - cand[k][3]) ** 2 + (y - cand[k][4]) ** 2) ** 0.5

    def owner(s_):
        best, k = None, 0
        for idx in range(len(cand)):
            d = dist(idx, s_)
            if best is None or d < best - 1e-9:
                best, k = d, idx
        return k

    step = max(0.5, min(3.0, L / 100.0))
    n = max(2, int(L / step) + 1)
    samples = [min(L, k * step) for k in range(n)]
    if samples[-1] < L:
        samples.append(L)
    owners = [owner(s_) for s_ in samples]
    # runs of one owner; each boundary refined by bisection between the two samples
    runs = []
    start = 0.0
    for k in range(1, len(samples)):
        if owners[k] != owners[k - 1]:
            a, b = owners[k - 1], owners[k]
            lo, hi = samples[k - 1], samples[k]
            for _ in range(24):
                mid = (lo + hi) / 2.0
                if dist(a, mid) <= dist(b, mid):
                    lo = mid
                else:
                    hi = mid
            cut = (lo + hi) / 2.0
            runs.append([a, start, cut])
            start = cut
    runs.append([owners[-1], start, L])
    # a WALL STEP printed between two callouts of different elevation is the boundary
    stp = []
    for w in steps or ():
        s_, d = _project(w[0], w[1], pts, cum)
        if d <= reach_pt:
            stp.append(s_)
    for k in range(1, len(runs)):
        a, b = runs[k - 1][0], runs[k][0]
        if cand[a][2] == cand[b][2] or not stp:
            continue
        lo_s, hi_s = min(cand[a][0], cand[b][0]), max(cand[a][0], cand[b][0])
        lo_s, hi_s = max(lo_s, runs[k - 1][1]), min(hi_s, runs[k][2])
        cur = runs[k][1]
        best = None
        for s_ in stp:
            if lo_s < s_ < hi_s and (best is None or abs(s_ - cur) < abs(best - cur)):
                best = s_
        if best is not None:
            runs[k - 1][2] = best
            runs[k][1] = best
    out = []
    for o, s0, s1 in runs:
        z = cand[o][2]
        if out and out[-1][0] == z:
            out[-1] = (z, out[-1][1] + (s1 - s0), out[-1][2], s1)
        elif s1 - s0 > 1e-9:
            out.append((z, s1 - s0, s0, s1))
    return out


def _steps_xy(steps):
    return [(float(w["x"]), float(w["y"])) for w in (steps or [])]


def _overlap(s0: float, s1: float, ranges) -> float:
    """Length of [s0, s1] covered by the sorted, merged `ranges`."""
    tot = 0.0
    for a, b in ranges or ():
        lo, hi = max(s0, float(a)), min(s1, float(b))
        if hi > lo:
            tot += hi - lo
    return tot


def group_walls_by_bof(callouts: list, chains: list, ft_per_pt: float,
                       schedule: dict | None = None, reach_ft: float = 10.0,
                       steps: list | None = None,
                       skip_by_style: dict | None = None) -> dict:
    """Sheet-wide groups: {"groups": [{bof_ft, callouts, marks, width_ft,
    width_from, lf_ft, chain_ids, chain_major, families, colour}],
    "footing_style": {style_id, label, share}, "reach_ft", "unassigned_ft",
    "families", "outline_note"}.
    `chains` are the detect result's rows (chain_id, class, style_id,
    length_pt, points, plus label/layer when known); annotation and chains
    under 12 pt are ignored. `schedule` is the document's schedule (rows by
    mark) for the width. `steps` are the WALL STEP words' centres.
    `chain_ids` lists every chain with a piece in the group (for hover);
    `chain_major` the chains whose largest piece is in it (for paint).
    `skip_by_style`: {style_id: {chain_id: [[s0, s1], ...]}} - the arc
    ranges twin_lines found NOT to count (a footing's inner edge, a wall's
    inside face). When the footing family has them, each group's `lf_ft`
    is ONE RUN per footing and `lf_drawn_ft` keeps the as-drawn footage."""
    calls = [c for c in callouts if c.get("bof_ft") is not None]
    out = {"groups": [], "footing_style": None, "reach_ft": reach_ft,
           "unassigned_ft": 0.0, "families": {}, "once": False,
           "outline_note": "outline length as drawn - a footing drawn as a closed rectangle counts both long sides"}
    if not calls or not chains or not ft_per_pt:
        return out
    reach_pt = reach_ft / ft_per_pt
    stp = _steps_xy(steps)
    plan = [c for c in chains if c.get("class") != "annotation" and float(c.get("length_pt", 0)) >= 12.0
            and c.get("points") and len(c["points"]) >= 2]
    # the footing family: the style the callouts sit on most often (nearest chain)
    nearest_style = Counter()
    for c in calls:
        best, sid = None, None
        for ch in plan:
            d = _poly_dist(c["x"], c["y"], ch["points"])
            if best is None or d < best:
                best, sid = d, ch.get("style_id")
        if sid is not None and best is not None and best <= reach_pt:
            nearest_style[sid] += 1
    foot_sid, foot_n = (nearest_style.most_common(1)[0] if nearest_style else (None, 0))
    groups: dict = {}
    fam_label: dict = {}
    sk = (skip_by_style or {})
    skip = sk.get(foot_sid) or sk.get(str(foot_sid)) or {}
    out["once"] = bool(skip)

    def grp(key):
        return groups.setdefault(key, {"bof_ft": key, "callouts": 0, "marks": Counter(), "chain_ids": [],
                                       "chain_major": [], "families": defaultdict(float), "lf_ft": 0.0,
                                       "lf_drawn_ft": 0.0})

    for ch in plan:
        sid = ch.get("style_id")
        fam_label.setdefault(sid, ch.get("label") or ch.get("layer") or f"family {sid}")
        per: dict = {}
        once: dict = {}
        rng = (skip.get(ch["chain_id"]) or skip.get(str(ch["chain_id"])) or []) if sid == foot_sid else []
        for z, ln, s0, s1 in _pieces_along(ch["points"], calls, stp, reach_pt):
            if z is None:
                out["unassigned_ft"] += ln * ft_per_pt
                continue
            per[z] = per.get(z, 0.0) + ln * ft_per_pt
            once[z] = once.get(z, 0.0) + (ln - _overlap(s0, s1, rng)) * ft_per_pt
        if not per:
            continue
        major = max(per.items(), key=lambda kv: kv[1])[0]
        for z, ft in per.items():
            g = grp(z)
            g["families"][sid] += ft
            if sid == foot_sid:
                g["lf_ft"] += once[z]
                g["lf_drawn_ft"] += ft
                g["chain_ids"].append(int(ch["chain_id"]))
                if z == major:
                    g["chain_major"].append(int(ch["chain_id"]))
    for c in calls:
        g = grp(round(float(c["bof_ft"]), 3))
        g["callouts"] += 1
        g["marks"][c["mark"]] += 1
    rows = (schedule or {}).get("rows") if isinstance(schedule, dict) else (schedule or {})
    for i, key in enumerate(sorted(groups)):
        g = groups[key]
        top = g["marks"].most_common(1)[0][0] if g["marks"] else None
        srow = (rows or {}).get(top) if top else None
        width = (srow or {}).get("width_ft") if srow else (mark_fallback(top) or {}).get("width_ft") if top else None
        out["groups"].append({
            "bof_ft": key, "callouts": g["callouts"], "marks": dict(g["marks"]),
            "width_ft": width, "width_from": ("schedule" if srow else ("mark digits" if width is not None else None)),
            "lf_ft": round(g["lf_ft"], 1), "lf_drawn_ft": round(g["lf_drawn_ft"], 1),
            "chain_ids": g["chain_ids"], "chain_major": g["chain_major"],
            "families": {str(k2): round(v, 1) for k2, v in sorted(g["families"].items(), key=lambda kv: -kv[1])[:4]},
            "colour": GROUP_COLOURS[i % len(GROUP_COLOURS)],
        })
    out["footing_style"] = ({"style_id": foot_sid, "label": fam_label.get(foot_sid, f"family {foot_sid}"),
                             "share": round(foot_n / max(len(calls), 1), 3)} if foot_sid is not None else None)
    out["families"] = {str(k): v for k, v in fam_label.items()}
    out["unassigned_ft"] = round(out["unassigned_ft"], 1)
    return out


def split_outline_by_bof(outline_pts: list, callouts: list, ft_per_pt: float,
                         reach_ft: float = 15.0, steps: list | None = None) -> dict:
    """The drawn outline's perimeter split by bottom-of-footing elevation,
    edge by edge with `_pieces_along` (a corner is a wall boundary), so the
    groups plus `unassigned_ft` add up to `perimeter_ft` exactly. A
    two-point outline is one open wall run. `steps` are {x, y} centres of
    WALL STEP words. Returns {"groups": [{bof_ft, lf_ft, share}], "edges":
    [{i, bof_ft, lf_ft, t0, t1}], "unassigned_ft", "perimeter_ft",
    "reach_ft"}."""
    calls = [c for c in callouts if c.get("bof_ft") is not None]
    pts = [(float(p[0]), float(p[1])) for p in (outline_pts or [])]
    out = {"groups": [], "edges": [], "unassigned_ft": 0.0, "perimeter_ft": 0.0, "reach_ft": reach_ft}
    if len(pts) < 2 or not ft_per_pt:
        return out
    reach_pt = reach_ft / ft_per_pt
    stp = _steps_xy(steps)
    per: dict = {}
    n = len(pts)
    # a ring closes on itself; two points are one open wall run
    for i in (range(n) if n >= 3 else [0]):
        a, b = pts[i], pts[(i + 1) % n]
        L = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
        if L <= 1e-9:
            continue
        out["perimeter_ft"] += L * ft_per_pt
        for z, ln, s0, s1 in _pieces_along([a, b], calls, stp, reach_pt):
            ft = ln * ft_per_pt
            if z is None:
                out["unassigned_ft"] += ft
            else:
                per[z] = per.get(z, 0.0) + ft
            out["edges"].append({"i": i, "bof_ft": z, "lf_ft": round(ft, 1),
                                 "t0": round(s0 / L, 4), "t1": round(s1 / L, 4)})
    P = out["perimeter_ft"] or 1.0
    out["groups"] = [{"bof_ft": k, "lf_ft": round(v, 1), "share": round(v / P, 3)} for k, v in sorted(per.items())]
    out["unassigned_ft"] = round(out["unassigned_ft"], 1)
    out["perimeter_ft"] = round(out["perimeter_ft"], 1)
    return out
