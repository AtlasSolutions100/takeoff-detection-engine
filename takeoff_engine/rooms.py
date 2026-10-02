"""Rooms: the enclosed floor area behind a click, off the detected walls.

THE FLOORING TAKEOFF (a flooring client, via staff, 2026-09-02). By
hand they draw each room, colour it by the flooring that goes in it, add
up the square feet per colour, and separately trace the walls for base /
molding footage. What that needs from the engine:

  * an enclosed room area - the NEGATIVE SPACE between wall lines, which
    nothing else in the engine finds (every other reader follows open
    chains; areas.py reads solid fills only)
  * a boundary that closes ACROSS DOOR OPENINGS, because flooring runs
    through the doorway and the linework leaves that gap open
  * wall footage for the base, with the openings taken back out

HOW. The operator clicks inside a room; the room is whatever the walls
enclose around that click. Concretely:

  1. RASTER the chains of the styles the operator has visible, at `dpi`,
     as `thick`-pixel lines. Visible-only is the role assignment: a
     dimension string or a fixture outline crossing a room would cut it in
     two, and the style checkboxes are how the operator says "these are
     walls, those are not" - the same gesture the whole review UI uses.
  2. BRIDGE each door opening the detector found (extract.door_swings:
     hinge, radius, arc midpoint). The opening in the wall runs from the
     hinge to the strike jamb, one door-width along the wall; which of the
     two arc-endpoint directions that is, the raster itself says - the
     wall continues past the strike jamb, the open leaf points into empty
     room. A thick line is drawn hinge -> strike, closing the gap exactly
     where the door is and nowhere else. No morphological closing wide
     enough to bridge a door: that would also bridge a corridor.
  3. SEAL hairline gaps (a 3 px close): chains that stop a point short of
     the corner they meet. At 1/4" = 1'-0" the narrowest real doorway is
     ~45 pt, so a 3 px seal can never close one.
  4. FLOOD FILL from the click. Leaks to the sheet edge = not enclosed,
     and the answer says so with the size of the leak rather than
     returning the whole sheet as a room.
  5. CONTOUR the fill: outer polygon (simplified to ~1 px), holes kept as
     their own area so a closet or an island counted out of the flooring
     is visible, not silent. Perimeter from the polygon, door widths that
     sit on that boundary subtracted for the base footage.
  6. NAME from the sheet: the room label is text inside the polygon - the
     largest alphabetic line ("MASTER BEDROOM", "BEDROOM 2"), never a
     dimension string.

Every number carries its scale; with no scale the reply is in pt and the
UI says so, the same rule as the structure and trench tools.

WHAT THIS DOES NOT DO. It does not find rooms on its own (no click, no
room) and it does not decide what flooring goes where - the colour is the
estimator's convention, applied in the panel. There is NO truth data for
any of this yet: the corpus is civil, and the first real flooring plans
with the client's own marked-up takeoff beside them are what turns these
numbers from plausible into scored (memory: flooring-calculator-ask).
"""
from __future__ import annotations

import math
import re
from collections import OrderedDict
from typing import Optional

import cv2
import numpy as np

DEFAULT_DPI = 100
WALL_THICK_PX = 2
SEAL_PX = 3
CAVITY_PX = 9
# rays tried for the strike-jamb direction, degrees off the arc-midpoint
# bearing: a 90-degree door puts its endpoints at +-45, a 45-degree door at
# +-22.5, a 135-degree one at +-67.5 - the sweep is not in the record
_JAMB_OFFSETS = tuple(range(20, 71, 5))
_LEAF_OFFSETS = (0, 10, 20, 30, 40, 45, 50, 60, 70, 80, 90)

_DOOR_LAYER = re.compile(r"door|swing|opng|openin|dr\b", re.I)

_CACHE: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
_CACHE_MAX = 6


def _bearing_ink(mask: np.ndarray, x: float, y: float, ang: float,
                 r0: float, r1: float, step: float = 1.0) -> int:
    """Ink pixels along a ray from (x, y) at bearing `ang` (radians),
    between distances r0 and r1."""
    h, w = mask.shape
    c, s = math.cos(ang), math.sin(ang)
    n = 0
    d = r0
    while d <= r1:
        px, py = int(round(x + c * d)), int(round(y + s * d))
        if 0 <= px < w and 0 <= py < h and mask[py, px]:
            n += 1
        d += step
    return n


def _disk_ink(mask: np.ndarray, x: float, y: float, rad: int) -> int:
    """Ink pixels within `rad` px of (x, y)."""
    h, w = mask.shape
    px, py = int(round(x)), int(round(y))
    if not (0 <= px < w and 0 <= py < h):
        return 0
    sub = mask[max(0, py - rad):py + rad + 1, max(0, px - rad):px + rad + 1]
    return int((sub > 0).sum())


def door_bridges(openings: list, ink: np.ndarray, px_per_pt: float,
                 wall: Optional[np.ndarray] = None) -> list:
    """For each detected door opening, the segment (in pt) that closes the
    gap in the wall: hinge -> strike jamb. Returns [{x0,y0,x1,y1,r,score}].

    WHICH WAY IS THE OPENING. The arc's two endpoints sit at the hinge plus
    r along two bearings: one is the door drawn open (a leaf line of ink
    from the hinge into the room), the other is the door closed, which IS
    the opening - empty, lying along the wall, with the wall continuing on
    the far side of the hinge. So a bearing scores by: ink BEHIND the
    hinge along it (the wall the door hangs in), minus ink AHEAD of the
    hinge short of r (the open leaf). `ink` is the raster of EVERY chain,
    not just the visible styles, so the leaf and the arc count even when
    the operator has hidden them as non-walls. Measured 2026-09-08 on
    Belle A1.00 (27 doors): the first cut scored "ink beyond the jamb" and
    a partition wall 1.2r away in the open-leaf direction won on both
    hall doors, bridging them INTO the rooms; this scoring closes both."""
    out = []
    if not openings:
        return out
    # The rays are one pixel wide and a hinge sits up to a few px off the
    # row its wall is drawn on (the fitted arc centre is not the line), so
    # sample rasters fattened by 3 px each way - a miss by a row must not
    # read as "no wall here". WALL evidence (behind the hinge, beyond the
    # jamb) is read off the wall raster when one is given: on the full ink
    # the bed drawn under a bedroom door counted as "wall behind" and the
    # opening was bridged into the room (house set A2.1 bed 2). The leaf
    # test stays on the full ink, where the leaf is.
    ker = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    ink = cv2.dilate(ink, ker)
    wall = ink if wall is None else cv2.dilate(wall, ker)
    for o in openings or []:
        try:
            cx, cy, r = float(o["cx"]), float(o["cy"]), float(o["r"])
            mx, my = float(o["mx"]), float(o["my"])
        except (KeyError, TypeError, ValueError):
            continue
        if r <= 0:
            continue
        mid = math.atan2(my - cy, mx - cx)
        hx, hy = cx * px_per_pt, cy * px_per_pt
        rp = r * px_per_pt
        best, best_score = None, None
        # CANDIDATE BEARINGS. An arc door found from its chain carries its
        # two arc-end bearings exactly (a0, a1): the opening is one of the
        # two, no search. A unit-pass record has only the arc midpoint, so
        # the ends are somewhere 20-70 degrees off it; a leaf's "mid" is
        # the leaf tip and the opening lies 0-90 degrees off it.
        if o.get("a0") is not None and o.get("a1") is not None:
            cands = [float(o["a0"]), float(o["a1"])]
        else:
            offsets = _LEAF_OFFSETS if o.get("source") == "leaf" else _JAMB_OFFSETS
            cands = [mid + sign * math.radians(off) for off in offsets for sign in (1, -1)]
        rad = max(3, int(round(6.0 * px_per_pt)))
        scored = []
        for ang in cands:
            # THE STRIKE JAMB IS ON THE WALL. The far end of the opening
            # sits on the wall line; the far end of the open leaf stands in
            # the room. That disk test decides; the wall continuing behind
            # the hinge and beyond the jamb corroborates; leaf ink along the
            # bearing is a tie-breaker only (a threshold line through the
            # opening reads as leaf ink, Belle A1.00 storage door: 31 hits).
            sx, sy = hx + rp * math.cos(ang), hy + rp * math.sin(ang)
            strike = _disk_ink(wall, sx, sy, rad)
            behind = _bearing_ink(wall, hx, hy, ang + math.pi, rp * 0.3, rp * 1.5)
            beyond = _bearing_ink(wall, hx, hy, ang, rp * 1.05, rp * 1.8)
            leaf = _bearing_ink(ink, hx, hy, ang, rp * 0.15, rp * 0.9)
            scored.append((3 * strike + 2 * behind + 2 * beyond - leaf, ang, leaf))
        # A DRAWN LEAF IS DECISIVE when only one bearing carries one. A
        # pair meeting mid-opening (house set A2.1 bed 2 closet, 5/0) has
        # no wall at either end, and the ray behind the hinge along the
        # open leaf ran through the closet's wall hatch: 50 hits against 8
        # along the real opening, so the bridge was drawn down the leaf
        # into the room. Leaf ink of 60% of the ray on one bearing and
        # under 25% on the other says which is the door drawn open; the
        # threshold-line case has ink on BOTH and falls through to the
        # score as before.
        full = max(1.0, 0.75 * rp)
        if len(scored) == 2:
            (_, ang_a, leaf_a), (_, ang_b, leaf_b) = scored
            if leaf_a >= 0.6 * full and leaf_b <= 0.25 * full:
                scored = [scored[1]]
            elif leaf_b >= 0.6 * full and leaf_a <= 0.25 * full:
                scored = [scored[0]]
        for score, ang, _ in scored:
            if best_score is None or score > best_score:
                best_score, best = score, ang
        if best is None:
            continue
        x1, y1 = cx + r * math.cos(best), cy + r * math.sin(best)
        out.append({"x0": round(cx, 2), "y0": round(cy, 2),
                    "x1": round(x1, 2), "y1": round(y1, 2), "r": round(r, 2),
                    "score": int(best_score)})
    return out


def doors_from_chains(chains: list, scale_ft_per_in: Optional[float] = None,
                      known: Optional[list] = None, ink: Optional[np.ndarray] = None,
                      px_per_pt: float = 1.0) -> list:
    """Door swings the detector's unit pass cannot see: a DASHED arc.

    extract.door_swings works on stroke units and needs an arc that is one
    polyline. A drafter who draws the swing dashed (a builder's house
    set A2.1, layer A-Door: every door on the sheet) gives the unit pass
    forty 2-point dashes and no arc, so that sheet reported 0 doors. The
    chain builder, though, joins the dashed arc and its solid leaf into
    ONE chain - measured there: 27 A-Door chains, bbox a square of side
    r = 42 / 48 / 54 pt (2'-4", 2'-8", 3'-0" at 1/4"), length 2.57 r
    (quarter arc 1.571 r + leaf 1.0 r), 40-88 points.

    So: a chain whose bbox is square (within 12%), whose length is a
    quarter arc plus a leaf (2.3-2.85 r) or a bare quarter arc (1.45-1.7
    r), and whose points sit either on the circle of radius r about one
    bbox corner or on the two bbox edges out of that corner (>= 85% of
    them, 7% tolerance) is a door hinged at that corner. r is gated to
    1.25-6 ft when the scale is known, 8-80 pt otherwise - the same window
    door_swings uses. Emits the same {cx, cy, r, mx, my} record, skipping
    any hinge the unit pass already reported within r/4."""
    out = []
    if scale_ft_per_in and scale_ft_per_in > 0:
        ppf = 72.0 / scale_ft_per_in
        r_lo, r_hi = 1.25 * ppf, 6.0 * ppf
    else:
        r_lo, r_hi = 8.0, 80.0
    known_pts = [(float(o.get("cx", 0)), float(o.get("cy", 0)), float(o.get("r", 0)))
                 for o in (known or []) if isinstance(o, dict)]
    for ch in chains or []:
        P = ch.get("points") or []
        if len(P) < 8:
            continue
        # A LAYER NAME IS EVIDENCE. When the drafter named the layer, a door
        # is on a door layer; a quarter-arc on SIT-TREE or North Arrow is a
        # tree or an arrow (a residential construction set 3.0: 10 phantom doors
        # by geometry alone, 0 with this gate). Unnamed chains (Belle's
        # sheets carry no layers) are judged by geometry.
        layer = str(ch.get("layer") or "").strip()
        if layer and not _DOOR_LAYER.search(layer):
            continue
        xs = [p[0] for p in P]
        ys = [p[1] for p in P]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        short = min(w, h)
        if short <= 0 or short < r_lo * 0.8 or max(w, h) > 1.6 * r_hi:
            continue
        L = float(ch.get("length_pt") or 0)
        fit = _fit_door(P, L, r_lo, r_hi)
        # A MULTI-LEAF RUN. A pair, a bi-fold, two doors sharing a wall
        # stub: several arcs in one chain. Split the polyline into its arcs
        # and fit each on its own; two or more arcs beat the single fit,
        # which on a pair either fails or passes one big circle through
        # both 45-degree arcs (a 4'-4" door hinged in mid-air, measured on
        # the synthetic pair in tests/test_rooms.py).
        split = _split_arcs(P, r_lo, r_hi, bool(layer))
        if len(split) >= 2:
            fits, source = split, "chain-split"
        elif fit is not None:
            fits, source = [fit], "chain"
        else:
            fits, source = split, "chain-split"
        for hx, hy, r, mx, my, a0, a1 in fits:
            if any(math.hypot(hx - kx, hy - ky) <= max(kr, r) / 4.0 for kx, ky, kr in known_pts):
                continue
            out.append({"cx": round(hx, 1), "cy": round(hy, 1), "r": round(r, 1),
                        "mx": round(mx, 1), "my": round(my, 1), "source": source,
                        "a0": round(a0, 4), "a1": round(a1, 4),
                        "chain_id": ch.get("chain_id")})
            known_pts.append((hx, hy, r))
    # THE LEAF PASS. A double door, a pair, a bi-fold run: several arcs in
    # one chain, which no single circle fits (house set A2.1 bed 2: chain
    # 721, two 45-degree arcs side by side; the foyer closet: three). Those
    # doors still draw their LEAVES as plain 2-point strokes on the door
    # layer, one per door, each exactly one door-width long. A leaf is a
    # door whose hinge is the end that sits on wall ink; the far end stands
    # in for the arc midpoint, and door_bridges tries bearings 0-90 degrees
    # off it (a leaf drawn closed lies IN the opening, at 0). Needs the ink
    # raster to tell the ends apart, and a named door layer - an unnamed
    # 3 ft stroke is any short line on the sheet.
    if ink is not None:
        H, W = ink.shape
        known_all = known_pts + [(o["cx"], o["cy"], o["r"]) for o in out]
        # wall ink within ~8.5 pt of an end (12 px at 100 dpi). A hinge is
        # drawn a few px off the wall's inner face (house set: 5-10 px), so
        # a tight dilation read both ends as bare; a disk sees the wall.
        rad = max(4, int(round(8.5 * px_per_pt)))

        def _ink_at(x, y):
            px, py = int(round(x * px_per_pt)), int(round(y * px_per_pt))
            if not (0 <= px < W and 0 <= py < H):
                return 0
            sub = ink[max(0, py - rad):py + rad + 1, max(0, px - rad):px + rad + 1]
            return int((sub > 0).sum())

        for ch in chains or []:
            P = ch.get("points") or []
            layer = str(ch.get("layer") or "").strip()
            if len(P) != 2 or not layer or not _DOOR_LAYER.search(layer):
                continue
            (x0, y0), (x1, y1) = P[0], P[1]
            L = math.hypot(x1 - x0, y1 - y0)
            if not (r_lo <= L <= r_hi):
                continue
            i0, i1 = _ink_at(x0, y0), _ink_at(x1, y1)
            if max(i0, i1) < 2 * min(i0, i1) + 10:
                continue                     # cannot tell the hinge: abstain
            hx, hy, fx, fy = (x0, y0, x1, y1) if i0 > i1 else (x1, y1, x0, y0)
            # EITHER end at a known hinge: this stroke is that door's own
            # leaf (a leaf drawn as two parallel strokes, or the second
            # line of a pair found from its arcs) and must not become a
            # second door hinged at its tip - house set A2.1 bed 2 closet:
            # two phantom hinges at the meeting point, bridged into the
            # room.
            if any(math.hypot(hx - kx, hy - ky) <= max(kr, L) / 4.0
                   or math.hypot(fx - kx, fy - ky) <= max(kr, L) / 4.0
                   for kx, ky, kr in known_all):
                continue
            rec = {"cx": round(hx, 1), "cy": round(hy, 1), "r": round(L, 1),
                   "mx": round(fx, 1), "my": round(fy, 1), "source": "leaf",
                   "chain_id": ch.get("chain_id")}
            out.append(rec)
            known_all.append((hx, hy, L))
    return out


def _arc_of(pts, r_lo, r_hi):
    """One arc out of a run of points, or None: a fitted circle with at
    least 85% of the points within 7% of r, r a door width, the ends
    35-135 degrees apart. Returns (cx, cy, r, mx, my, a_end1, a_end2)."""
    fit = _fit_circle(pts)
    if fit is None:
        return None
    cx, cy, r = fit
    on = [p for p in pts if abs(math.hypot(p[0] - cx, p[1] - cy) - r) <= 0.07 * r]
    if len(on) < 5 or len(on) < 0.85 * len(pts):
        return None
    fit = _fit_circle(on)
    if fit is None:
        return None
    cx, cy, r = fit
    if not (r_lo <= r <= r_hi):
        return None
    angs = sorted(math.atan2(p[1] - cy, p[0] - cx) for p in on)
    gaps = [(angs[i + 1] - angs[i]) for i in range(len(angs) - 1)] + [angs[0] + 2 * math.pi - angs[-1]]
    span = 2 * math.pi - max(gaps)
    if not (math.radians(35) <= span <= math.radians(135)):
        return None
    gi = max(range(len(gaps)), key=lambda i: gaps[i])
    a1 = angs[(gi + 1) % len(angs)]
    a2 = angs[gi]
    a_mid = a1 + span / 2.0
    return cx, cy, r, cx + r * math.cos(a_mid), cy + r * math.sin(a_mid), a1, a2


def _split_arcs(P, r_lo, r_hi, layered):
    """The doors in a chain that carries SEVERAL arcs (a pair, a bi-fold, two
    doors either side of a wall stub - house set A2.1 chains 721, 725, 729:
    leaf + arc + arc + leaf, or arc + leaf + stub + leaf + arc).

    Cut the polyline into STRAIGHT pieces (a segment at least 4x the chain's
    median segment and 6 pt - a leaf or a wall stub; a dashed arc's own
    segments are 2-2.5 pt) and CURVED pieces (everything between, split
    again at any turn over 40 degrees - the cusp where two arcs of a pair
    meet). Each curved piece is fitted on its own (_arc_of). An arc is a
    door when a straight piece of about its radius runs from the arc to its
    centre (the leaf), or when a second arc of the same radius sits one
    diameter away (its partner in a pair); on a NAMED door layer a bare arc
    is enough, as it is for the single-arc fit. Measured on house set A2.1:
    the 7 door-layer chains the single fit rejected are 4 multi-arc runs (7
    doors) and 3 dashed rectangles (overhead doors, no arc, none found)."""
    pts = [P[0]]
    for p in P[1:]:
        if math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) > 1e-6:
            pts.append(p)
    if len(pts) < 8:
        return []
    segs = []
    for i in range(len(pts) - 1):
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        segs.append((math.hypot(x1 - x0, y1 - y0), math.atan2(y1 - y0, x1 - x0)))
    med = sorted(s[0] for s in segs)[len(segs) // 2]
    long_thr = max(6.0, 4.0 * med)
    straights = []           # (length, (x0, y0), (x1, y1))
    curves = []              # [points]
    cur = [pts[0]]
    for i, (L, ang) in enumerate(segs):
        if L >= long_thr:
            if len(cur) >= 5:
                curves.append(cur)
            straights.append((L, pts[i], pts[i + 1]))
            cur = [pts[i + 1]]
            continue
        if i > 0 and segs[i - 1][0] < long_thr:
            turn = abs((ang - segs[i - 1][1] + math.pi) % (2 * math.pi) - math.pi)
            if turn > math.radians(40):
                if len(cur) >= 5:
                    curves.append(cur)
                cur = [pts[i]]
        cur.append(pts[i + 1])
    if len(cur) >= 5:
        curves.append(cur)
    cands = []
    for c in curves:
        chord = math.hypot(c[-1][0] - c[0][0], c[-1][1] - c[0][1])
        if chord < 0.5 * r_lo:
            continue
        a = _arc_of(c, r_lo, r_hi)
        if a is not None:
            cands.append(a)
    out = []
    for i, a in enumerate(cands):
        cx, cy, r = a[0], a[1], a[2]
        leaf = any(0.8 * r <= L <= 1.25 * r
                   and min(math.hypot(p[0] - cx, p[1] - cy), math.hypot(q[0] - cx, q[1] - cy)) <= 0.2 * r
                   for L, p, q in straights)
        pair = any(j != i and abs(b[2] - r) <= 0.15 * r
                   and abs(math.hypot(b[0] - cx, b[1] - cy) - (b[2] + r)) <= 0.15 * (b[2] + r)
                   for j, b in enumerate(cands))
        if leaf or pair or layered:
            out.append(a)
    return out


def _fit_circle(pts):
    """Algebraic (Kasa) circle fit: x^2 + y^2 + a x + b y + c = 0."""
    A = np.array([[p[0], p[1], 1.0] for p in pts], dtype=float)
    b = -np.array([p[0] ** 2 + p[1] ** 2 for p in pts], dtype=float)
    try:
        (a, bb, c), *_ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return None
    cx, cy = -a / 2.0, -bb / 2.0
    r2 = cx * cx + cy * cy - c
    if r2 <= 0:
        return None
    return cx, cy, math.sqrt(r2)


def _fit_door(P, L, r_lo, r_hi):
    """One door out of a chain: a circle fitted to the ARC points (hinge =
    centre), the leaf tolerated as a straight run ending at the centre.

    The first cut assumed a quarter arc hinged at a bbox corner; a swing
    drawn past 90 degrees, or a leaf drawn outside the arc's quadrant,
    puts the hinge inside the box's edge and failed it (house set A2.1:
    chains 720/728/735, bbox 58 x 42 for a 42 pt door). Fitting the circle
    on the points that are NOT on a straight run through the centre - two
    passes: fit all, drop the leaf, refit - takes the hinge from the arc
    itself. Accepts when >= 80% of the points sit on that circle (7%) or
    on the leaf, the arc spans 35-135 degrees, r is a door, and the chain
    is not much longer than arc + leaf (a wall stub can ride along)."""
    fit = _fit_circle(P)
    if fit is None:
        return None
    cx, cy, r = fit
    for _ in range(2):
        arc = [p for p in P if abs(math.hypot(p[0] - cx, p[1] - cy) - r) <= 0.07 * r]
        if len(arc) < 5:
            return None
        fit = _fit_circle(arc)
        if fit is None:
            return None
        cx, cy, r = fit
    if not (r_lo <= r <= r_hi):
        return None
    arc = [p for p in P if abs(math.hypot(p[0] - cx, p[1] - cy) - r) <= 0.07 * r]
    angs = sorted(math.atan2(p[1] - cy, p[0] - cx) for p in arc)
    # angular span, handling the wrap: the largest gap between neighbours
    gaps = [(angs[i + 1] - angs[i]) for i in range(len(angs) - 1)] + [angs[0] + 2 * math.pi - angs[-1]]
    span = 2 * math.pi - max(gaps)
    if not (math.radians(35) <= span <= math.radians(135)):
        return None
    # the arc's two ends, then the leaf = points on either radius to an end
    gi = max(range(len(gaps)), key=lambda i: gaps[i])
    a_end1 = angs[(gi + 1) % len(angs)]
    a_end2 = angs[gi]
    ok = len(arc)
    for p in P:
        d = math.hypot(p[0] - cx, p[1] - cy)
        if abs(d - r) <= 0.07 * r or d > 1.07 * r or d < 1e-6:
            continue
        a = math.atan2(p[1] - cy, p[0] - cx)
        if min(abs((a - a_end1 + math.pi) % (2 * math.pi) - math.pi),
               abs((a - a_end2 + math.pi) % (2 * math.pi) - math.pi)) <= math.radians(4):
            ok += 1
    if ok < 0.8 * len(P):
        return None
    if L > 3.2 * r or L < 0.55 * r * span:
        return None
    a_mid = a_end1 + span / 2.0        # forward from the end after the gap
    return cx, cy, r, cx + r * math.cos(a_mid), cy + r * math.sin(a_mid), a_end1, a_end2


def wall_raster(chains: list, styles: Optional[set], w_pt: float, h_pt: float,
                dpi: int = DEFAULT_DPI, thick: int = WALL_THICK_PX) -> np.ndarray:
    """uint8 mask (255 = wall ink) of the chains whose style is in `styles`
    (None = every chain), drawn as `thick`-px polylines at `dpi`."""
    k = dpi / 72.0
    W, H = max(1, int(round(w_pt * k))), max(1, int(round(h_pt * k)))
    mask = np.zeros((H, W), dtype=np.uint8)
    for ch in chains or []:
        if styles is not None and ch.get("style_id") not in styles:
            continue
        P = ch.get("points") or []
        if len(P) < 2:
            continue
        pts = np.array([[int(round(p[0] * k)), int(round(p[1] * k))] for p in P],
                       dtype=np.int32).reshape(-1, 1, 2)
        cv2.polylines(mask, [pts], False, 255, thickness=thick, lineType=cv2.LINE_8)
    return mask


def _cache_key(job_id, page, dpi, styles, stamp, anno_tag=None):
    return (job_id, page, dpi, tuple(sorted(styles)) if styles is not None else None, stamp, anno_tag)


# Annotation layers whose strokes must never act as wall, even when the
# chain stage fused them into a wall chain: dimension and extension lines,
# text, notes. Door and window layers are NOT here - they are openings the
# bridge pass reasons about.
_ANNO_LAYER = re.compile(r"dim|dimension|text|anno|note|leader", re.I)
# The shortest raw wall-layer stroke that joins the wall raster on its own
# (pt): four inches at 1/4" = 1'-0". A wall piece the chain stage set aside
# is a jamb or a jog and runs 10-30 pt; a dash of a dashed footprint on the
# same layer runs 4-6 (house set A2.1, measured 2026-09-26).
EXTRA_MIN_PT = 8.0
# Layers in the wall SET that draw no wall LINE (the panel's own rule): the
# hatch between a wall's faces, and the drafter's flooring boundaries and
# flooring hatch. Their chains stay walls; their raw strokes are not rescued.
_NOT_WALL_LINE = re.compile(r"hatch|floor", re.I)


def annotation_mask(strokes: list, wall_layers: set, w_pt: float, h_pt: float,
                    dpi: int = DEFAULT_DPI) -> tuple:
    """(anno, extra): what the drafter's CAD LAYERS say about the walls, on
    a sheet that carries them.

    The chain stage joins by pattern and colour, not by layer, and it sets
    short pieces aside. Both bit BED #3 on house set A2.1: the A-Dim line
    at x = 1254 down its east side was fused into a wall chain (555, through
    a 54 pt diagonal join) and became the room's east wall, 26 pt short of
    the drawn face at x = 1280 - 15 SF of bedroom filed in the hall - while
    the 18 pt A-wall jamb stub and the 17 pt jog that actually close that
    corner are in no chain at all. Taking the dimension ink out alone opened
    a 4.5 pt hole above the hall wall and the fill poured through.

      anno   pixels the fill may cross even where a wall chain runs: ink
             that only ANNOTATION strokes drew (`_ANNO_LAYER`, dilated by a
             pixel), LESS every pixel a stroke on a wall layer also covers,
             so a dimension line crossing a real wall never cuts it;
      extra  pixels that ARE wall although no ticked chain drew them: every
             raw stroke of EXTRA_MIN_PT or longer on one of the ticked
             styles' layers, at the wall stroke's width - the drafter's own
             short pieces (BED #3's 18 pt jamb stub and 17 pt jog, the
             linen closet's 24-30 pt walls, BED #2's 10-27 pt closet
             jambs). Shorter strokes are dashes: MECH's water heater is a
             dashed footprint of 4.5-5.7 pt pieces on the same A-wall
             layer, and joined it walled 6.4 SF of the room off as a hole.

    `strokes` are (layer, pts) pairs in page pt; `wall_layers` the layer
    names of the styles ticked as walls. (None, None) when the sheet has no
    layer names to go by (Belle's sheets carry none): the chains stand."""
    if not wall_layers:
        return None, None
    k = dpi / 72.0
    W, H = max(1, int(round(w_pt * k))), max(1, int(round(h_pt * k)))
    anno = np.zeros((H, W), dtype=np.uint8)
    wall_ink = np.zeros((H, W), dtype=np.uint8)
    extra = np.zeros((H, W), dtype=np.uint8)
    n_anno = n_wall = 0
    for layer, P in strokes or []:
        lay = str(layer or "")
        if not lay or len(P) < 2:
            continue
        pts = np.array([[int(round(float(p[0]) * k)), int(round(float(p[1]) * k))] for p in P],
                       dtype=np.int32).reshape(-1, 1, 2)
        if lay in wall_layers:
            cv2.polylines(wall_ink, [pts], False, 1, thickness=WALL_THICK_PX + 1, lineType=cv2.LINE_8)
            # only a WALL layer's pieces are rescued. A hatch or flooring
            # layer is in the wall set (it encloses rooms) but draws no wall
            # LINE - the same rule the panel's wall classing applies - and
            # its raw strokes are the pattern the chain stage threw out:
            # the inset's carpet dots and 45 pt SPC hatch lines (A-Flooring)
            # walled 4.5 SF of the inset bedroom off as a hole.
            if _NOT_WALL_LINE.search(lay):
                continue
            length = float(sum(math.hypot(P[i + 1][0] - P[i][0], P[i + 1][1] - P[i][1])
                               for i in range(len(P) - 1)))
            if length >= EXTRA_MIN_PT:
                cv2.polylines(extra, [pts], False, 1, thickness=WALL_THICK_PX, lineType=cv2.LINE_8)
                n_wall += 1
        elif _ANNO_LAYER.search(lay):
            cv2.polylines(anno, [pts], False, 1, thickness=1, lineType=cv2.LINE_8)
            n_anno += 1
    if not n_wall and not n_anno:
        return None, None
    if n_anno:
        anno = cv2.dilate(anno, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
        anno = (anno & (1 - wall_ink)).astype(np.uint8)
    else:
        anno = None
    return anno, (extra if n_wall else None)


def sealed_walls(result: dict, styles: Optional[set], dpi: int, cache_key=None,
                 anno: Optional[np.ndarray] = None,
                 extra: Optional[np.ndarray] = None):
    """The wall raster with door bridges drawn and hairline gaps sealed,
    plus the bridge list. Cached per (job, page, dpi, styles, result stamp)
    so a dozen clicks on one sheet rasterise once. `anno` and `extra` (see
    annotation_mask) are what the sheet's CAD layers add: raw strokes on
    the ticked styles' layers join the wall raster, and ink that only
    annotation strokes drew leaves it - both before the bridges and the
    seal."""
    if cache_key is not None and cache_key in _CACHE:
        _CACHE.move_to_end(cache_key)
        return _CACHE[cache_key]
    pg = result.get("page") or {}
    w_pt, h_pt = float(pg.get("width_pt") or 0), float(pg.get("height_pt") or 0)
    if w_pt <= 0 or h_pt <= 0:
        raise ValueError("result carries no page size")
    k = dpi / 72.0
    chains = result.get("chains") or []
    wall = wall_raster(chains, styles, w_pt, h_pt, dpi)
    if extra is not None and extra.shape == wall.shape:
        wall[extra > 0] = 255
    if anno is not None and anno.shape == wall.shape:
        wall[anno > 0] = 0
    # orientation evidence for the doors comes from EVERY chain (leaf, arc,
    # the wall behind the hinge), whatever the operator has visible
    ink = wall if styles is None else wall_raster(chains, None, w_pt, h_pt, dpi)
    openings = list(result.get("openings") or [])
    # the leaf pass tells a hinge from a free end by wall ink at each end -
    # on a raster WITHOUT the door layer, or the leaf's own stroke makes
    # both ends look like wall and every leaf abstains
    door_layer = [ch for ch in chains if _DOOR_LAYER.search(str(ch.get("layer") or ""))]
    ink_nodoor = ink
    if door_layer:
        keep = [ch for ch in chains if not _DOOR_LAYER.search(str(ch.get("layer") or ""))]
        ink_nodoor = wall_raster(keep, None, w_pt, h_pt, dpi)
    openings += doors_from_chains(chains, _scale(result), known=openings, ink=ink_nodoor, px_per_pt=k)
    bridges = door_bridges(openings, ink, k, wall=wall)
    for b in bridges:
        # A fitted hinge sits a few pt INTO the opening (the arc centre is
        # not the wall's end), so a bridge drawn hinge-to-jamb leaves a
        # sliver between the wall's end and the hinge - house set A2.1
        # bed 2: a 5 pt passage at the top of its door, and the room ran
        # into the hall through it. Overshoot both ends by 0.15 r (8 pt at
        # most); the overshoot lands on wall either way.
        dx, dy = b["x1"] - b["x0"], b["y1"] - b["y0"]
        L = math.hypot(dx, dy) or 1.0
        ext = min(8.0, 0.15 * b["r"]) / L
        x0, y0 = b["x0"] - dx * ext, b["y0"] - dy * ext
        x1, y1 = b["x1"] + dx * ext, b["y1"] + dy * ext
        cv2.line(wall, (int(round(x0 * k)), int(round(y0 * k))),
                 (int(round(x1 * k)), int(round(y1 * k))), 255,
                 thickness=WALL_THICK_PX, lineType=cv2.LINE_8)
    if SEAL_PX > 1:
        ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (SEAL_PX, SEAL_PX))
        wall = cv2.morphologyEx(wall, cv2.MORPH_CLOSE, ker)
    out = (wall, bridges)
    if cache_key is not None:
        _CACHE[cache_key] = out
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return out


_ALPHA = re.compile(r"[A-Za-z]{3,}")
# a dimension string carries feet/inch marks (20'-0", 3'6", 12") or is a
# long / decimal number; a bare "2" is a room number and stays with its line
_DIMENSION = re.compile(r"(^.*['\"″′].*$)|(^\d{3,}$)|(^\d+[.,]\d+$)|(^[\d]+x[\d]+$)")


# What a room is CALLED on a plan, and what is written inside a room that
# is not its name. Measured 2026-09-08 on house set A2.1: the tallest text
# inside a room was the wall note ("2x6 STUDS @ 16\" O.C.", "12'-6\" WALL
# HT.", "INSULATED"), never the label - a label is a room word, short, and
# near the middle of the room.
_ROOM_WORDS = re.compile(
    r"\b(BED(ROOM)?|BATH(ROOM)?|KITCHEN|GARAGE|ROOM|HALL(WAY)?|CLOSET|W\.?I\.?C\.?|PANTRY|FOYER|"
    r"LAUNDRY|MUD(ROOM)?|DINING|LIVING|GREAT|FAMILY|OFFICE|STUDY|DEN|STORAGE|MECH(ANICAL)?|"
    r"SHOP|PORCH|PATIO|ENTRY|NOOK|UTILITY|RESTROOM|TOILET|LINEN|MASTER|PRIMARY|LOBBY|"
    r"CORRIDOR|VESTIBULE|STAIR(S)?|LOFT|BONUS|FLEX|MEDIA|THEATER|GYM|SAUNA|SUITE|POWDER|"
    r"BREAK|CONFERENCE|RECEPTION|LOUNGE|CAFE|CLASSROOM|LAB|JANITOR|ELEC(TRICAL)?|"
    r"DRESSING|SALON|SPA|WORK(SHOP)?|CRAWL|ATTIC|BASEMENT|CELLAR|BAR|WC)\b", re.I)
_NOTE_WORDS = re.compile(
    r"\b(STUDS?|DRYWALL|SLAB|CEILING|INSUL(ATION|ATED)?|CONC(RETE)?|O\.C\.|HT\.?|HEIGHT|SLOPE|"
    r"BELOW|ABOVE|T\.O\.|FDN|COMMON|VAULTED|HIGH|LIFT|FUTURE|HEATER|ELECT|GAS|STUB|WATER|LINE|"
    r"SHUT|OFF|ACCESS|SELF|CLOSING|MIN\.?|OPTIONAL|TYP\.?|SEE|SCHEDULE|R\.O\.|DOOR|WINDOW|"
    r"STEEL|WALL|WALLS|PLATE|BEAM|HEADER|FLUSH|PROVIDED|OUTLET|R\d\d|VERIFY|NOTE|SHEAR)\b", re.I)


def _lines_inside(words: list, poly_px: np.ndarray, k: float, drop_dims: bool) -> list:
    """The text LINES whose words sit inside the polygon, in reading order:
    [{text, words, h, cy}], grouped by pymupdf's own (block, line) when the
    words carry it, else by vertical overlap. `drop_dims` leaves dimension
    strings out (the room namer never wants one); the ceiling reader needs
    them, its whole subject is a dimension."""
    inside = []
    for wd in words or []:
        try:
            x0, y0, x1, y1, txt = float(wd[0]), float(wd[1]), float(wd[2]), float(wd[3]), str(wd[4])
        except (IndexError, TypeError, ValueError):
            continue
        t = txt.strip()
        if not t or (drop_dims and _DIMENSION.match(t)):
            continue
        cx, cy = (x0 + x1) / 2 * k, (y0 + y1) / 2 * k
        if cv2.pointPolygonTest(poly_px, (float(cx), float(cy)), False) < 0:
            continue
        key = (int(wd[5]), int(wd[6])) if len(wd) >= 7 else None
        inside.append((x0, y0, x1, y1, t, key))
    lines = {}
    order = []
    for w in inside:
        cy, h = (w[1] + w[3]) / 2, (w[3] - w[1])
        key = w[5]
        if key is None:
            for kk, ln in lines.items():
                if abs(ln["cy"] - cy) <= 0.5 * max(h, ln["h"]):
                    key = kk
                    break
            else:
                key = ("v", len(lines))
        if key not in lines:
            lines[key] = {"cy": cy, "h": h, "words": []}
            order.append(key)
        lines[key]["words"].append(w)
    out = []
    for key in order:
        ln = lines[key]
        ws = sorted(ln["words"], key=lambda w: w[0])
        out.append({"text": " ".join(w[4] for w in ws), "words": ws, "h": ln["h"], "cy": ln["cy"]})
    return out


# A CEILING HEIGHT written in the room. House set A2.1 prints one in every
# room ("9'-1 1/8\" CEILING", the fraction as half-size glyphs that arrive
# as "9'-118\""); other sets write "CLG. HT. 9'-0\"" or "9' CEILING". The
# reader wants a feet-and-inches value on a line that says ceiling; a
# "VAULTED CEILING" line is reported with no height so the panel asks for
# one instead of pretending. "8\" DRYWALL ON CEILING" carries inches only
# and is not a height.
_CEIL = re.compile(r"\b(CEIL(ING)?S?|CLG)\b", re.I)
_FT_IN = re.compile(r"""(\d{1,2})\s*['’′]\s*-?\s*(?:(\d{1,4})(?:\s*(\d{1,2})/(\d{1,2}))?\s*["”″])?""")
_FRAC_CODE = {"12": 0.5, "14": 0.25, "34": 0.75, "18": 0.125, "38": 0.375, "58": 0.625, "78": 0.875,
              "116": 1 / 16, "316": 3 / 16, "516": 5 / 16, "716": 7 / 16, "916": 9 / 16,
              "1116": 11 / 16, "1316": 13 / 16, "1516": 15 / 16}


def _feet_of(m) -> Optional[float]:
    ft = int(m.group(1))
    inch = m.group(2)
    if inch is None:
        return float(ft)
    if len(inch) <= 2 and int(inch) < 12:
        v = ft + int(inch) / 12.0
    else:
        # a stacked fraction folded into the digits: "118" = 1 1/8
        for cut in (1, 2):
            a, b = inch[:cut], inch[cut:]
            if a and int(a) < 12 and b in _FRAC_CODE:
                v = ft + (int(a) + _FRAC_CODE[b]) / 12.0
                break
        else:
            return None
    if m.group(3) and m.group(4):
        try:
            v += int(m.group(3)) / int(m.group(4)) / 12.0
        except ZeroDivisionError:
            return None
    return v


# THE OBJECTS INSIDE A ROOM, and whether flooring stops at them. A flooring
# takeoff is wall to wall, through the doorway to the threshold, UNDER a
# range, a fridge, a toilet and the furniture - and it stops at a tub, a
# shower pan, a hearth, and (crew by crew) at built-in cabinets. So the
# engine lists every closed, fixture-sized shape inside the fill, names it
# from its CAD layer and its size, and says which kind is deducted by
# default: tubs and showers. Cabinets are LISTED and kept (Koby, 2026-09-09:
# "not cabinets"); the panel has the switch. On a sheet with no layer names
# a shape is a shape - listed, never deducted on its own - and the operator
# clicks it. Measured on house set A2.1: 6 plumbing loops, 12 cabinet, 4
# appliance, 11 furniture, 6 mechanical, all 1-12 ft; Belle A1.00 has 7
# closed fixture-sized loops and no names at all.
_OBJ_SKIP_LAYER = re.compile(r"hatch|wall|dim|text|border|door|window|glaz|floor|grid|title|sheet|anno|note|roof|fdn|beam|slab", re.I)
_OBJ_KIND_LAYER = (
    (re.compile(r"plumb|fixt|sanit|bath", re.I), "plumbing"),
    (re.compile(r"cabin|casework|millwork|island|counter", re.I), "cabinet"),
    (re.compile(r"applian|equip|mech", re.I), "appliance"),
    (re.compile(r"furn|chair|table|bed|sofa|desk", re.I), "furniture"),
)
OBJ_DEDUCT_DEFAULT = {"tub": True, "shower": True}
OBJ_MIN_FT, OBJ_MAX_FT = 1.0, 12.0
# how far growing a cell to its drawn outline may push any side of its box
# (ft): a rim, a curb, an apron - never a neighbouring fixture. The house
# set A2.1's tub rim is 7.7 pt = 0.43 ft at 1/4"; a vanity beside a tub sits a
# foot or more off it.
OBJ_GROW_FT = 0.75


def _obj_kind(layer: str, w: float, h: float) -> tuple:
    """(kind, guessed). Sizes in ft, from general practice: a tub is about
    2.5 x 5 (30 x 60 in) to 3 x 6; a shower pan 3 x 3 to 5 x 5; a toilet
    about 1.5 x 2.3; a lavatory under 2.5 ft on its long side."""
    # sizes are the CELL's, a line width inside the drawn outline on every
    # side (the house set's 2'-6" x 5'-0" main-bath tub measures 2.19 x 4.7),
    # so every floor is a few inches under the catalogue size
    lo, hi = min(w, h), max(w, h)
    tubby = 2.0 <= lo <= 3.4 and 4.0 <= hi <= 6.6
    showery = 2.4 <= lo <= 6.0 and hi <= 6.0 and hi / max(lo, 1e-6) <= 1.6 and not tubby
    fam = None
    for rx, name in _OBJ_KIND_LAYER:
        if layer and rx.search(layer):
            fam = name
            break
    if fam == "plumbing":
        if tubby:
            return "tub", False
        if showery:
            return "shower", False
        # a toilet is 28-30 in deep; a lavatory is under 2 ft either way
        if 1.0 <= lo <= 2.0 and 2.0 <= hi <= 2.9:
            return "toilet", False
        if hi <= 2.8:
            return "sink", False
        return "plumbing", False
    if fam:
        return fam, False
    if layer:
        return "other", False
    # no layer names: a tub-shaped loop is only ever a guess
    if tubby:
        return "tub", True
    return "shape", True


def room_objects(chains: list, region: np.ndarray, styles, k: float,
                 fpp: Optional[float], room_px: int, openings: Optional[list] = None,
                 widths: Optional[dict] = None, debug: Optional[list] = None) -> list:
    """The fixtures inside a room fill: the CELLS the non-wall linework
    encloses within it, each grown to the DRAWN OUTLINE around it.

    A tub is rarely one closed loop: the house set draws it as a rim against
    three walls plus an inner outline, a vanity as an open run of cabinet
    lines against the wall. What every fixture has in common is that its
    lines, together with the walls, ENCLOSE a piece of the room. So: raster
    every chain that is not a wall style (and not on a hatch / dimension /
    text / door layer) inside the fill, and every connected piece of the
    fill they cut off - other than the biggest one, the open floor - is a
    candidate. A candidate is fixture-sized (1-12 ft), is named by the
    layer that owns most of the linework around it and by its size
    (_obj_kind), and is dropped when a detected door swing sits in it (the
    quarter-disc between a door arc and its wall is a cell too).

    THE DEDUCTION IS THE DRAWN OUTLINE, not the free pixels inside it. The
    cell stops at the inner edge of every line and loses every stroke
    drawn inside the fixture (the tub's inner outline, its model text, the
    shower's drain), and the rim strips between an outline and its inner
    line are thinner than a foot and never cells of their own: house set
    A2.1's shower cell held 10.8 SF inside a 60 x 36 in outline (15.0 SF)
    and its tub cell 9.2 SF inside 5'-0 x 2'-10 (14.2 SF). So each cell is
    grown to the piece of "everything in the room that is not open floor"
    - the ink and every cell, rim strips included - that holds it, with
    its holes filled: that is the outline's outer edge. The pen is drawn
    at its plotted width (`widths`: style id -> pt; a pen of unknown width
    is drawn 2 px wide), because the outer edge of a 1.9 pt tub outline is
    where the drafter's 5'-0" dimension ends. Two guards keep a neighbour's
    lines from riding in: the growth may not push any side of the box out
    by more than OBJ_GROW_FT, and the footprint stays under 60 % of the
    room; a cell that fails either keeps its own free pixels, as before.
    Cells sharing one footprint (the tub's rim strips and its pan) are one
    object.

    `region` is the room's filled pixel mask at `k` px per pt; `styles` the
    wall style ids (None = every chain is a wall, so nothing is an object)."""
    out = []
    if fpp is None or region is None or region.size == 0 or styles is None:
        return out
    H, W = region.shape
    ys_, xs_ = np.nonzero(region)
    if xs_.size == 0:
        return out
    rx0, rx1, ry0, ry1 = xs_.min() / k - 2, xs_.max() / k + 2, ys_.min() / k - 2, ys_.max() / k + 2
    ink = np.zeros((H, W), dtype=np.uint8)
    cand = []                                   # chains that can bound an object
    for ch in chains or []:
        if ch.get("style_id") in styles:
            continue
        layer = str(ch.get("layer") or "").strip()
        if layer and _OBJ_SKIP_LAYER.search(layer):
            continue
        P = ch.get("points") or []
        if len(P) < 2:
            continue
        xs = [p[0] for p in P]
        ys = [p[1] for p in P]
        if max(xs) < rx0 or min(xs) > rx1 or max(ys) < ry0 or min(ys) > ry1:
            continue
        pts = np.array([[int(round(p[0] * k)), int(round(p[1] * k))] for p in P], dtype=np.int32).reshape(-1, 1, 2)
        # the pen at its plotted width; a 1 px line still seals a 4-connected
        # fill (LINE_8 strokes are 8-connected), so a hairline pen is honest
        w_pt = (widths or {}).get(ch.get("style_id"))
        try:
            thick = max(1, int(round(float(w_pt) * k))) if w_pt else 2
        except (TypeError, ValueError):
            thick = 2
        cv2.polylines(ink, [pts], False, 1, thickness=thick, lineType=cv2.LINE_8)
        cand.append((layer, P))
    if not cand:
        return out
    free = (region.astype(np.uint8) & (1 - ink)).astype(np.uint8)
    n_lab, lab, stats, cents = cv2.connectedComponentsWithStats(free, connectivity=4)
    if n_lab <= 2:
        return out
    order = sorted(range(1, n_lab), key=lambda i: -stats[i, cv2.CC_STAT_AREA])
    # everything in the room that is NOT the open floor: the ink and every
    # cell, rim strips included. A fixture's drawn outline is the outer edge
    # of the piece of this that holds its cell.
    notfloor = (region.astype(np.uint8) & (lab != order[0]).astype(np.uint8)).astype(np.uint8)
    _n2, lab2 = cv2.connectedComponents(notfloor, connectivity=8)
    # The footprint is clipped to the FILL on purpose. A fixture against a
    # wall is drawn to the wall's face line and the far half of its outline
    # sits under the wall stroke, which the fill never held: deducting the
    # whole drawn outline would take out floor the room was never given.
    # House set A2.1: shower outline 54 x 90 pt = 15.0 SF drawn, 14.4 SF
    # inside the fill; tub 5'-0 x 2'-10 = 14.2 SF drawn, 14.1 inside.
    door_pts = []
    for o in openings or []:
        try:
            door_pts.append((float(o["mx"]) * k, float(o["my"]) * k))
        except (KeyError, TypeError, ValueError):
            pass
    cells = []
    grown = set()                               # footprint ids already listed
    for i in order[1:]:
        area_px = int(stats[i, cv2.CC_STAT_AREA])
        x, y, w, h = (int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                      int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT]))
        w_ft, h_ft = w / k * fpp, h / k * fpp
        if min(w_ft, h_ft) < OBJ_MIN_FT or max(w_ft, h_ft) > OBJ_MAX_FT:
            continue
        if area_px < 0.4 * (k * k) / (fpp * fpp):       # under 0.4 SF: a line's own thickness
            continue
        if area_px >= 0.6 * max(room_px, 1):
            continue
        mask = (lab == i).astype(np.uint8)
        # a door swing is a cell too: skip any cell holding a detected arc's midpoint
        if any(0 <= int(round(py)) < H and 0 <= int(round(px)) < W and mask[int(round(py)), int(round(px))]
               for px, py in door_pts):
            continue
        # GROW TO THE DRAWN OUTLINE: the not-floor piece holding this cell,
        # holes filled, when it stays fixture-shaped
        cy_, cx_ = np.nonzero(mask)
        comp = int(lab2[cy_[0], cx_[0]]) if cx_.size else 0
        if comp and comp in grown:
            continue                            # the same fixture, listed already
        if comp:
            def _filled_of(labels, cid):
                foot = (labels == cid).astype(np.uint8)
                fcs, _h = cv2.findContours(foot, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                filled = np.zeros_like(foot)
                if fcs:
                    cv2.drawContours(filled, fcs, -1, 1, thickness=cv2.FILLED)
                fx, fy, fw, fh = cv2.boundingRect(filled)
                grow = max(x - fx, y - fy, (fx + fw) - (x + w), (fy + fh) - (y + h), 0)
                return filled, (fx, fy, fw, fh), grow, int(cv2.countNonZero(filled))
            filled, (fx, fy, fw, fh), grow_px, f_px = _filled_of(lab2, comp)
            ok = grow_px / k * fpp <= OBJ_GROW_FT and f_px < 0.6 * max(room_px, 1) and f_px > area_px
            if debug is not None:
                debug.append({"cell": (round(x / k, 1), round(y / k, 1), round((x + w) / k, 1), round((y + h) / k, 1)),
                              "foot": (round(fx / k, 1), round(fy / k, 1), round((fx + fw) / k, 1), round((fy + fh) / k, 1)),
                              "grow_pt": round(grow_px / k, 1), "cell_px": area_px, "foot_px": f_px, "ok": bool(ok)})
            if ok:
                mask = filled
                x, y, w, h = fx, fy, fw, fh
                w_ft, h_ft = w / k * fpp, h / k * fpp
                area_px = f_px
                grown.add(comp)
        # who owns the linework around it: chain points within 3 px of the cell
        ring = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)))
        votes = {}
        for layer, P in cand:
            n = 0
            for p in P:
                px, py = int(round(p[0] * k)), int(round(p[1] * k))
                if 0 <= px < W and 0 <= py < H and ring[py, px]:
                    n += 1
            if n:
                votes[layer] = votes.get(layer, 0) + n
        layer = max(votes, key=votes.get) if votes else ""
        cs, hier = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cs:
            continue
        c = max(cs, key=cv2.contourArea)
        poly = cv2.approxPolyDP(c, 1.0, True).reshape(-1, 2)
        cells.append({"box": (x, y, x + w, y + h), "area_px": area_px, "layer": layer,
                      "w_ft": w_ft, "h_ft": h_ft,
                      "poly": [[round(float(px) / k, 1), round(float(py) / k, 1)] for px, py in poly]})
    # merge a cell whose box sits inside another's (the tub's inner outline
    # inside its rim): the outer keeps its box and polygon, the areas add.
    # Outer first by BOX, not by pixels - the rim's ring holds fewer pixels
    # than the pan it surrounds.
    cells.sort(key=lambda c: -((c["box"][2] - c["box"][0]) * (c["box"][3] - c["box"][1])))
    merged = []
    for c in cells:
        host = None
        for m in merged:
            bx = m["box"]
            if c["box"][0] >= bx[0] - 2 and c["box"][1] >= bx[1] - 2 and c["box"][2] <= bx[2] + 2 and c["box"][3] <= bx[3] + 2:
                host = m
                break
        if host is not None:
            host["area_px"] += c["area_px"]
            if not host["layer"]:
                host["layer"] = c["layer"]
        else:
            merged.append(c)
    for c in merged:
        kind, guessed = _obj_kind(c["layer"], c["w_ft"], c["h_ft"])
        out.append({
            "kind": kind, "guessed": guessed, "layer": c["layer"] or None,
            "w_ft": round(c["w_ft"], 2), "h_ft": round(c["h_ft"], 2),
            # what comes out of THIS fill: the drawn outline, less the strip
            # under the wall stroke the fill never held
            "area_sf": round(c["area_px"] / (k * k) * fpp * fpp, 1),
            "deduct": bool(OBJ_DEDUCT_DEFAULT.get(kind)) and not guessed,
            "polygon": c["poly"],
        })
    out.sort(key=lambda o: -o["area_sf"])
    return out


def _simplify(P: list, tol: float = 0.8) -> list:
    if len(P) <= 8:
        return list(P)
    arr = np.array(P, dtype=np.float32).reshape(-1, 1, 2)
    s = cv2.approxPolyDP(arr, tol, True).reshape(-1, 2)
    return [list(map(float, p)) for p in s] if len(s) >= 3 else list(P)


def _ft_in_text(v: float) -> str:
    """9.094 -> 9'-1 1/8\" (nearest sixteenth)."""
    ft = int(v)
    inches = (v - ft) * 12.0
    whole = int(inches)
    six = int(round((inches - whole) * 16))
    if six == 16:
        whole, six = whole + 1, 0
    if whole == 12:
        ft, whole = ft + 1, 0
    frac = ""
    if six:
        n, d = six, 16
        while n % 2 == 0:
            n //= 2
            d //= 2
        frac = " %d/%d" % (n, d)
    return "%d'-%d%s\"" % (ft, whole, frac)


def _line_box(ln: dict):
    """(x0, y0, x1, y1) of a _lines_inside line, from its words."""
    ws = ln.get("words") or []
    if not ws:
        return None
    return (min(w[0] for w in ws), min(w[1] for w in ws), max(w[2] for w in ws), max(w[3] for w in ws))


def ceiling_note(lines: list) -> Optional[dict]:
    """{"ft": 9.09, "text": "9'-1 1/8\" CEILING"} from the room's own lines,
    {"ft": None, "text": "VAULTED CEILING"} when the sheet says a ceiling is
    there but not how high, None when nothing mentions one. A height must
    be 6-30 ft. The first line with a height wins; a vaulted note only
    stands when no line has one."""
    vaulted = None
    lines = list(lines or [])
    for i, ln in enumerate(lines):
        t = ln["text"]
        if not _CEIL.search(t):
            continue
        # a narrow room stacks the label: "9'-1 1/8\"" on one line and
        # "CEILING" on the next (house set A2.1 W.I.C.), so the height may
        # sit on the line before or after the ceiling word
        cands = [t]
        bb = _line_box(ln)
        for j in (i - 1, i + 1):
            if not (0 <= j < len(lines)) or not _FT_IN.fullmatch(lines[j]["text"].strip()):
                continue
            # STACKED, not merely next in reading order: directly above or
            # below within 1.6 text heights and overlapping sideways. The
            # great room's "VAULTED CEILING" had a 12'-10 1/4" wall
            # dimension as its next line and read a 12'-10" ceiling.
            ob = _line_box(lines[j])
            if bb is None or ob is None:
                continue
            gap = min(abs(ob[1] - bb[3]), abs(bb[1] - ob[3]))
            if gap > 1.6 * max(bb[3] - bb[1], 1.0) or ob[2] < bb[0] or ob[0] > bb[2]:
                continue
            other = lines[j]["text"].strip()
            cands.append(other + " " + t if j < i else t + " " + other)
        for tt in cands:
            for m in _FT_IN.finditer(tt):
                v = _feet_of(m)
                if v is not None and 6.0 <= v <= 30.0:
                    # the text as printed, with a folded fraction unfolded
                    # ("9'-118\"" back to 9'-1 1/8\"): what the operator reads
                    shown = tt[:m.start()] + _ft_in_text(v) + tt[m.end():]
                    return {"ft": round(v, 3), "text": shown.strip()[:60]}
        if vaulted is None and re.search(r"\bVAULT|\bSLOPED|\bCATHEDRAL", t, re.I):
            vaulted = {"ft": None, "text": t.strip()[:60]}
    return vaulted


def room_name(words: list, poly_px: np.ndarray, k: float) -> Optional[str]:
    """The room's label: of the text lines whose centre is inside the
    polygon, the one that reads like a room name. Scored: room words in,
    note words out, short over long, nearer the room's centre over the
    edge, taller as a last tie-break. Nothing that reads like a note is
    returned - the caller names the room "Room N" instead, which is a
    smaller lie than "INSULATED". `words` are pymupdf 'words' tuples."""
    lines = _lines_inside(words, poly_px, k, drop_dims=True)
    if not lines:
        return None
    m = cv2.moments(poly_px)
    ccx = (m["m10"] / m["m00"]) / k if m["m00"] else 0.0
    ccy = (m["m01"] / m["m00"]) / k if m["m00"] else 0.0
    best = None
    cands = []
    for ln in lines:
        ws = ln["words"]
        text = ln["text"]
        # three letters in a row, or a room word: "W.I.C." has the room
        # word and no run of letters, and lost its name to the run test
        # (house set A2.1: "Room 8")
        if not (_ALPHA.search(text) or _ROOM_WORDS.search(text)):
            continue
        n_room = len(_ROOM_WORDS.findall(text))
        n_note = len(_NOTE_WORDS.findall(text))
        if n_note and not n_room:
            continue
        lx = sum((w[0] + w[2]) / 2 for w in ws) / len(ws)
        ly = sum((w[1] + w[3]) / 2 for w in ws) / len(ws)
        dist = math.hypot(lx - ccx, ly - ccy)
        score = (3.0 * n_room - 4.0 * n_note - 0.05 * max(0, len(text) - 20)
                 - 0.002 * dist + 0.1 * ln["h"] + (0.5 if text.isupper() else 0.0))
        if n_room == 0 and (len(ws) > 3 or not text.isupper()):
            continue
        cand = {"score": score, "text": text, "cx": lx, "cy": ly, "h": ln["h"],
                "x0": min(w[0] for w in ws), "x1": max(w[2] for w in ws),
                "y0": min(w[1] for w in ws), "y1": max(w[3] for w in ws)}
        cands.append(cand)
        if best is None or score > best["score"]:
            best = cand
    if best is None:
        return None
    # A LABEL WRAPS. "BRIDAL DRESSING" over "ROOM" (Belle A1.00), "PRIMARY"
    # over "BEDROOM": the scorer prefers the shorter, more central line and
    # returned "ROOM". A candidate line directly above or below the winner
    # (within 1.4 x its height, centres within half a width) is the same
    # label; join in reading order. Same-height only, so a note under a
    # label is not glued on.
    parts = [best]
    for c in cands:
        if c is best or abs(c["h"] - best["h"]) > 0.35 * best["h"]:
            continue
        gap = min(abs(c["y0"] - best["y1"]), abs(best["y0"] - c["y1"]))
        wid = max(best["x1"] - best["x0"], c["x1"] - c["x0"], 1.0)
        if gap <= 1.4 * best["h"] and abs(c["cx"] - best["cx"]) <= 0.5 * wid:
            parts.append(c)
    parts.sort(key=lambda c: c["cy"])
    return " ".join(c["text"] for c in parts)[:60]


def pick_room(result: dict, words: list, x_pt: float, y_pt: float,
              styles: Optional[set] = None, dpi: int = DEFAULT_DPI,
              cache_key=None, closures: Optional[list] = None,
              scale: Optional[float] = None,
              scale_by: Optional[str] = None,
              anno: Optional[np.ndarray] = None,
              extra: Optional[np.ndarray] = None) -> dict:
    """The room enclosing (x_pt, y_pt). See the module docstring.

    `closures` are operator-drawn segments [[x0,y0,x1,y1], ...] in pt that
    count as wall for this fill: the fix for a cased opening the sheet
    leaves open, a partition drawn in no line style at all (Belle A1.00
    p1: the storage and office partitions stop at the door hinge and the
    rooms run into the hall as drawn), or a doorway the detector missed.
    Two clicks by the operator, and the fill is honest about it - the
    reply names how many closures it used.

    `scale` is the feet per paper inch every square foot below is computed
    from, and `scale_by` says who set it. The CALLER decides: the scale in
    force on the sheet (the reviewer's pick, a typed scale, else the
    engine's reading), or the caption under an inset the click sits in.
    Until 2026-09-26 this read the engine's raw reading out of the cached
    result and ignored the reviewer (Belle A1.00 BRIDAL: 210.1 SF at the
    engine's 4 ft/in with the reviewer's 8 in force - 840.5 SF). None =
    the engine's reading, as before.

    Returns {"ok": True, polygon, area_sf|None, area_pt2, perim_lf|None,
    perim_pt, openings_lf|None, openings_pt, holes_sf|None, holes_pt2,
    name, bridges_used, closures_used, scale_ft_per_in, scale_by, dpi} or
    {"ok": False, "why": ...}.
    """
    k = dpi / 72.0
    if not (scale and scale > 0):
        scale = _scale(result)
        scale_by = "engine" if scale else None
    wall, bridges = sealed_walls(result, styles, dpi, cache_key, anno=anno, extra=extra)
    # the door swings, for the object pass: a bridge keeps hinge and strike
    # but not the arc midpoint, so the swing's midpoint is rebuilt 45
    # degrees off the opening on either side (one of them is the swing);
    # the unit-pass records carry mx/my already
    bridges_src = []
    for b in bridges:
        dx, dy = b["x1"] - b["x0"], b["y1"] - b["y0"]
        for sgn in (1, -1):
            a = math.atan2(dy, dx) + sgn * math.pi / 4
            bridges_src.append({"mx": b["x0"] + b["r"] * 0.7 * math.cos(a),
                                "my": b["y0"] + b["r"] * 0.7 * math.sin(a)})
    if closures:
        wall = wall.copy()               # never draw into the cached raster
        for c in closures:
            try:
                x0, y0, x1, y1 = (float(v) for v in c[:4])
            except (TypeError, ValueError):
                continue
            cv2.line(wall, (int(round(x0 * k)), int(round(y0 * k))),
                     (int(round(x1 * k)), int(round(y1 * k))), 255,
                     thickness=WALL_THICK_PX + 1, lineType=cv2.LINE_8)
    H, W = wall.shape
    px, py = int(round(x_pt * k)), int(round(y_pt * k))
    if not (0 <= px < W and 0 <= py < H):
        return {"ok": False, "why": "that point is off the sheet"}
    if wall[py, px]:
        return {"ok": False, "why": "that click landed on a line - click on the open floor inside the room"}
    # flood from the click over the NON-wall pixels
    free = (wall == 0).astype(np.uint8)
    ff_mask = np.zeros((H + 2, W + 2), dtype=np.uint8)
    cv2.floodFill(free, ff_mask, (px, py), 2, loDiff=0, upDiff=0,
                  flags=4 | cv2.FLOODFILL_FIXED_RANGE)
    region = (free == 2).astype(np.uint8)
    # WALL CAVITIES. A CAD wall is two parallel lines a few pt apart, and
    # where it ends at a door jamb the cavity between them is often not
    # capped - so the fill runs into the wall and along it, and the room
    # grows a tendril the width of the wall (Belle A1.00: 371 LF of
    # perimeter and 91 corners on a 4-room fill). An opening by
    # CAVITY_PX removes anything narrower than a wall; no real room or
    # corridor is (9 px is 0.36 ft at 1/4", 0.72 ft at 1/8").
    if CAVITY_PX > 1:
        ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (CAVITY_PX, CAVITY_PX))
        opened = cv2.morphologyEx(region, cv2.MORPH_OPEN, ker)
        n_lab, lab = cv2.connectedComponents(opened, connectivity=4)
        if n_lab > 1:
            want = lab[py, px]
            if want == 0:
                # the click sat inside the shaved rim: take the largest piece
                counts = np.bincount(lab.ravel())
                counts[0] = 0
                want = int(counts.argmax())
            region = (lab == want).astype(np.uint8)
    n_px = int(region.sum())
    edge = int(region[0, :].sum() + region[-1, :].sum() + region[:, 0].sum() + region[:, -1].sum())
    if edge > 0:
        sc = scale
        sf = (n_px / (k * k)) * (sc / 72.0) ** 2 if sc else None
        return {"ok": False,
                "why": "not enclosed - the fill leaked to the edge of the sheet"
                       + (" (%s SF of open floor)" % _fmt(sf) if sf else "")
                       + ". A wall is missing from the visible line types, or a "
                         "doorway the detector did not find is open: show the "
                         "wall style, or draw the room outline instead.",
                "leak_px": n_px}
    # THE FILL STOPS AT THE INK'S EDGE; THE ROOM STOPS AT THE LINE. A CAD
    # wall line is drawn ON the face of the wall and has no width in the
    # drawing's own terms, so the room reaches the line's centre, and the
    # fill - which stops where the wall stroke begins - is short by half
    # the stroke on every side: 2 x 0.7 pt on a 216 x 268 pt bedroom is
    # 1.2 % (house set A2.1 PRIMARY BEDROOM read 175.8 SF against 179.0
    # printed, every room 1-2 % under, the same way). Grow the fill by one
    # pixel of the two-pixel wall stroke, INTO WALL INK ONLY - never into
    # open floor, never through an opening - and measure that.
    if WALL_THICK_PX >= 2:
        ker3 = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
        grown = cv2.dilate(region, ker3)
        region = (grown & ((wall > 0).astype(np.uint8) | region)).astype(np.uint8)
        n_px = int(region.sum())
    contours, hier = cv2.findContours(region, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if not contours or hier is None:
        return {"ok": False, "why": "no enclosed area at that point"}
    hier = hier[0]
    # the outer contour that contains the click (there is one region, but be exact)
    outer_i = None
    for i, c in enumerate(contours):
        if hier[i][3] == -1 and cv2.pointPolygonTest(c, (float(px), float(py)), False) >= 0:
            outer_i = i
            break
    if outer_i is None:
        outer_i = max((i for i in range(len(contours)) if hier[i][3] == -1),
                      key=lambda i: cv2.contourArea(contours[i]), default=0)
    outer = contours[outer_i]
    # The DISPLAY polygon is simplified (~1 px, at most 0.1% of the ring);
    # every NUMBER comes from the unsimplified fill and contours, so a cut
    # corner in the drawing can never move a square foot.
    eps = max(1.0, 0.001 * cv2.arcLength(outer, True))
    poly = cv2.approxPolyDP(outer, eps, True)
    poly_px = poly.reshape(-1, 2).astype(np.float32)
    holes_px = 0.0
    n_holes = 0
    for i, c in enumerate(contours):
        if hier[i][3] == outer_i:
            # a hole is the closet's interior PLUS its own wall ink - the
            # part of the room the flooring does not cover
            holes_px += float(cv2.contourArea(c))
            n_holes += 1
    # perimeter off the simplified ring: the raw raster contour staircases
    # along a diagonal wall and would over-read it by up to 41%
    perim_px = float(cv2.arcLength(poly, True))
    # door widths on THIS boundary: a bridge whose midpoint lies within a
    # wall's thickness of the fill's edge (the bridge is drawn ON the wall
    # line; the fill stops at the ink's inner face)
    used = []
    tol = WALL_THICK_PX + SEAL_PX
    for b in bridges:
        mx, my = (b["x0"] + b["x1"]) / 2 * k, (b["y0"] + b["y1"]) / 2 * k
        d = cv2.pointPolygonTest(outer, (float(mx), float(my)), True)
        if abs(d) <= tol:
            used.append(b)
    openings_pt = sum(b["r"] for b in used)
    pt = 1.0 / k
    area_pt2 = (n_px) * pt * pt                     # filled pixels: holes already out
    holes_pt2 = holes_px * pt * pt
    perim_pt = perim_px * pt
    sc = scale
    fpp = (sc / 72.0) if sc else None
    out = {
        "ok": True,
        "polygon": [[round(float(p[0]) * pt, 2), round(float(p[1]) * pt, 2)] for p in poly_px],
        "area_pt2": round(area_pt2, 1), "perim_pt": round(perim_pt, 1),
        "openings_pt": round(openings_pt, 1), "holes_pt2": round(holes_pt2, 1),
        "n_holes": n_holes,
        "area_sf": round(area_pt2 * fpp * fpp, 1) if fpp else None,
        "perim_lf": round(perim_pt * fpp, 1) if fpp else None,
        "openings_lf": round(openings_pt * fpp, 1) if fpp else None,
        "holes_sf": round(holes_pt2 * fpp * fpp, 1) if fpp else None,
        "name": room_name(words, poly_px.reshape(-1, 1, 2), k),
        # the ceiling height written in the room, when the sheet prints one
        # (house set A2.1: every room), for wall area = perimeter x height
        "ceiling": ceiling_note(_lines_inside(words, poly_px.reshape(-1, 1, 2), k, drop_dims=False)),
        # the fixtures inside the fill, each with the part of it the
        # flooring would not cover; tubs and showers deducted by default
        "objects": room_objects(result.get("chains") or [], region, styles, k, fpp, n_px,
                                openings=list(result.get("openings") or []) + bridges_src,
                                widths={s.get("style_id"): s.get("width")
                                        for s in (result.get("styles") or [])
                                        if isinstance(s, dict)}),
        "bridges_used": [{k2: b[k2] for k2 in ("x0", "y0", "x1", "y1", "r")} for b in used],
        "bridges_total": len(bridges),
        "closures_used": len(closures or []),
        "scale_ft_per_in": sc, "scale_by": scale_by, "dpi": dpi,
    }
    return out


def _scale(result: dict) -> Optional[float]:
    sc = (result or {}).get("scale") or {}
    v = sc.get("ft_per_paper_inch")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _fmt(v) -> str:
    return "{:,.0f}".format(v) if v is not None else "?"
