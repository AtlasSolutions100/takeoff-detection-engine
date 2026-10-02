"""Find exploded text in the vector data.

CAD exports usually explode SHX / TrueType text into little strokes or filled
outlines. Those glyph pieces are still in the PDF as tiny paths, so a word is
a tight cluster of tiny paths whose points line up along a baseline. Knowing
the baseline angle lets us cut a clean upright crop for OCR, which is what
makes rotated contour labels readable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from .geom import near_pairs
from .ingest import Stroke


@dataclass
class TextCluster:
    id: int
    center: tuple
    angle: float           # radians, baseline direction (normalised to (-90, 90] degrees)
    length: float          # along the baseline (pt)
    height: float          # across (pt)
    stroke_idx: list
    gray: float
    color: tuple
    bbox: tuple            # axis-aligned bbox of the rotated rectangle
    n_curvy: int = 0
    text: str = ""
    conf: float = 0.0
    flipped: bool = False
    letter_idx: list | None = None   # the strokes that are LETTERS (what the OCR is shown); None = every stroke
    alt_text: str = ""               # what the word reads from the other side, where that is a number too
    alt_conf: float = 0.0

    def to_json(self) -> dict:
        return {"id": self.id, "center": [round(self.center[0], 1), round(self.center[1], 1)],
                "angle_deg": round(math.degrees(self.angle), 1), "length": round(self.length, 1),
                "height": round(self.height, 1), "n": len(self.stroke_idx), "gray": round(self.gray, 2),
                "bbox": [round(v, 1) for v in self.bbox], "text": self.text, "conf": round(self.conf, 0)}


def _extent(s: Stroke) -> tuple[float, float]:
    mn = s.pts.min(axis=0)
    mx = s.pts.max(axis=0)
    return float(mx[0] - mn[0]), float(mx[1] - mn[1])


def _is_curvy(s: Stroke) -> bool:
    """A glyph piece: a path that bends (digits are arcs and hooks; dashes are not)."""
    p = s.pts
    if len(p) < 4:
        return False
    v = np.diff(p, axis=0)
    ln = np.linalg.norm(v, axis=1)
    keep = ln > 0.05
    v = v[keep]
    if len(v) < 3:
        return False
    ang = np.arctan2(v[:, 1], v[:, 0])
    d = np.diff(ang)
    d = (d + np.pi) % (2 * np.pi) - np.pi
    total = float(np.abs(d).sum())
    return math.degrees(total) > 60.0


def glyph_stroke_ids(clusters: list["TextCluster"]) -> set:
    out = set()
    for c in clusters:
        out.update(c.stroke_idx)
    return out


def lean_angle(P: np.ndarray, ang: float, span: float = 10.0, step: float = 0.5) -> tuple[float, float, float]:
    """The angle, within `span` degrees of `ang`, at which the points stand LOWEST (least extent
    across the baseline): (angle, the extent there, the extent at `ang`). The letters of a word
    stand on one baseline and reach one height, so the word is lowest when it is read level;
    the main axis of its points (what `ang` is) turns with the slant of the lettering."""
    def across(t):
        r = P @ np.array([-math.sin(t), math.cos(t)])
        return float(r.max() - r.min())
    h0 = across(ang)
    best = (h0, ang)
    for k in range(int(round(2 * span / step)) + 1):
        t = ang + math.radians(-span + k * step)
        h = across(t)
        if h < best[0] - 1e-9:
            best = (h, t)
    return best[1], best[0], h0


def find_text_clusters(strokes: list[Stroke], exclude_idx: set | None = None, max_extent: float = 14.0,
                       link: float = 2.6, min_height: float = 3.0, max_height: float = 16.0) -> list[TextCluster]:
    exclude_idx = exclude_idx or set()
    cand = []
    for s in strokes:
        if s.idx in exclude_idx:
            continue
        if min(s.color) > 0.9 or s.opacity < 0.05:
            continue
        ex, ey = _extent(s)
        if max(ex, ey) > max_extent:
            continue
        if max(ex, ey) < 0.4 and len(s.pts) <= 2:
            continue                     # stipple dot
        cand.append(s)
    if len(cand) < 2:
        return []
    boxes = np.array([[s.pts[:, 0].min(), s.pts[:, 1].min(), s.pts[:, 0].max(), s.pts[:, 1].max()] for s in cand])
    centers = np.column_stack([(boxes[:, 0] + boxes[:, 2]) / 2, (boxes[:, 1] + boxes[:, 3]) / 2])
    parent = np.arange(len(cand))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # the pairs are walked a slice at a time (geom.near_pairs): the groups are the same whatever order
    # the pairs come in, and a sheet full of hatch never holds them all at once (D41: Res-A civil C3.0,
    # 57.3 million pairs; subdivision B, 99.5 million)
    for a, b in near_pairs(centers, max_extent + link):
        dx = np.maximum(0, np.maximum(boxes[a, 0], boxes[b, 0]) - np.minimum(boxes[a, 2], boxes[b, 2]))
        dy = np.maximum(0, np.maximum(boxes[a, 1], boxes[b, 1]) - np.minimum(boxes[a, 3], boxes[b, 3]))
        close = np.hypot(dx, dy) <= link
        for i, j in zip(a[close], b[close]):
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[rj] = ri
    groups: dict[int, list[int]] = {}
    for i in range(len(cand)):
        groups.setdefault(find(i), []).append(i)

    out: list[TextCluster] = []
    for gi, members in groups.items():
        if len(members) < 2:
            continue
        ss = [cand[i] for i in members]
        pts = np.vstack([s.pts for s in ss])
        if len(pts) < 4:
            continue
        c = pts.mean(axis=0)
        q = pts - c
        cov = q.T @ q / len(q)
        w, v = np.linalg.eigh(cov)
        axis = v[:, 1]                      # major axis
        ang = math.atan2(axis[1], axis[0])
        deg = math.degrees(ang)
        if deg > 90 or deg <= -90:
            ang += math.pi if deg <= -90 else -math.pi
        u = np.array([math.cos(ang), math.sin(ang)])
        n = np.array([-u[1], u[0]])
        t = q @ u
        r = q @ n
        L = float(t.max() - t.min())
        H = float(r.max() - r.min())
        n_curvy = sum(1 for s in ss if _is_curvy(s))
        if H < min_height or H > max_height or L < 4.0:
            continue
        if len(ss) <= 3 and L > 40:
            continue
        # a run of parallel straight dashes is not text
        if n_curvy == 0:
            dirs = []
            for s in ss:
                d = s.pts[-1] - s.pts[0]
                nn = np.linalg.norm(d)
                if nn > 0.5:
                    dirs.append(abs(float(d @ u)) / nn)
            if dirs and min(dirs) > 0.9:
                continue
            if len(ss) < 3:
                continue
        # pieces of the line the label sits in: straight, along the baseline, beyond the glyphs
        core_t = []
        tail = []
        for s in ss:
            d = s.pts[-1] - s.pts[0]
            nn = np.linalg.norm(d)
            along = abs(float(d @ u)) / nn if nn > 0.5 else 0.0
            ts = (s.pts - c) @ u
            if not _is_curvy(s) and along > 0.94 and nn >= 1.2:
                tail.append((s, float(ts.min()), float(ts.max())))
            else:
                core_t.append((float(ts.min()), float(ts.max())))
        if core_t and tail:
            lo = min(a for a, b in core_t)
            hi = max(b for a, b in core_t)
            drop = {id(s) for s, a, b in tail if b <= lo + 0.3 or a >= hi - 0.3}
            if drop and len(drop) < len(ss):
                ss = [s for s in ss if id(s) not in drop]
                pts = np.vstack([s.pts for s in ss])
                q = pts - c
                t = q @ u
                r = q @ n
                L = float(t.max() - t.min())
                H = float(r.max() - r.min())
                if H < min_height or L < 4.0:
                    continue
        # --- the word's LETTERS and the angle they stand at. In outline lettering (every letter a
        # closed shape) an open stroke in the word is no letter: it is the line the word is printed
        # on, running THROUGH it. The hotel site C3 does not mask its existing contours under their
        # heights: a 9-pt dash crosses the last digit and "5991" read "5994", six of the seven
        # printed 5991 of the sheet were misread (and a 5981 read 5984 gave its line that height).
        # The strokes stay the word's own (they are kept out of the line joining, as before); the
        # OCR is shown the letters. And the word is turned to where its letters stand lowest: the
        # main axis of slanted lettering is 3 to 9 degrees off the baseline, enough to read a 1 as 7.
        # (A word is never dropped here: which strokes are words decides which are lines, and a
        # word lost at this step gave its strokes back to the line joining - every piece of subdivision
        # A sheet 3.1 changed its number. Where the letters alone are too small to be a
        # word, the word stays as the main axis measured it.)
        # (Outline lettering = SOLID shapes, paths the PDF fills. A closed loop that is only stroked
        # is the 0 of a stroke font, and the 1 beside it is an open stroke: "1000" must keep its 1.)
        # (Only outline lettering is turned. A stroke font is read at its main axis, as before:
        # turned to its lowest - the dashes of its line count among its strokes - subdivision D
        # read a 674 as "t£9", a 668 upside down as "299", a 665 as "66"; measured 2026-09-27.)
        letters = ss
        solid = [s for s in ss if s.closed and (s.filled or getattr(s, "also_filled", False))]
        if 3 <= len(solid) < len(ss):
            letters = solid
        if L >= 2.0 * H and len(solid) >= 3 and (letters is solid or len(solid) == len(ss)):
            lp = np.vstack([s.pts for s in letters])
            ang2, h2, h1 = lean_angle(lp - c, ang)
            if h2 > 0.97 * h1:
                ang2 = ang                      # no lower to speak of: the angle the word had
            if letters is not ss or ang2 != ang:
                u2 = np.array([math.cos(ang2), math.sin(ang2)])
                n2 = np.array([-u2[1], u2[0]])
                q2 = lp - c
                t2, r2 = q2 @ u2, q2 @ n2
                L2, H2 = float(t2.max() - t2.min()), float(r2.max() - r2.min())
                if H2 >= min_height and L2 >= 4.0:
                    ang, u, n, t, r, L, H = ang2, u2, n2, t2, r2, L2, H2
                else:
                    letters = ss
        gray = float(np.mean([0.299 * s.color[0] + 0.587 * s.color[1] + 0.114 * s.color[2] for s in ss]))
        color = tuple(float(np.mean([s.color[k] for s in ss])) for k in range(3))
        cx, cy = float(c[0] + u[0] * (t.max() + t.min()) / 2 + n[0] * (r.max() + r.min()) / 2), \
                 float(c[1] + u[1] * (t.max() + t.min()) / 2 + n[1] * (r.max() + r.min()) / 2)
        hl, hh = L / 2, H / 2
        corners = np.array([[cx + sx * hl * u[0] + sy * hh * n[0], cy + sx * hl * u[1] + sy * hh * n[1]]
                            for sx in (-1, 1) for sy in (-1, 1)])
        bbox = (float(corners[:, 0].min()), float(corners[:, 1].min()), float(corners[:, 0].max()), float(corners[:, 1].max()))
        out.append(TextCluster(id=len(out), center=(cx, cy), angle=ang, length=L, height=H,
                               stroke_idx=[s.idx for s in ss], gray=gray, color=color, bbox=bbox, n_curvy=n_curvy,
                               letter_idx=None if letters is ss else [s.idx for s in letters]))
    return out


def merge_clusters_on_baseline(clusters: list[TextCluster], gap: float = 4.5) -> list[TextCluster]:
    """Join word clusters that sit on one baseline with a small space between them
    ("558.5" + "BW"). Spaces between glyph pieces break the proximity clustering."""
    if not clusters:
        return clusters
    used = [False] * len(clusters)
    out: list[TextCluster] = []
    C = np.array([c.center for c in clusters])
    tree = cKDTree(C)
    for i, a in enumerate(clusters):
        if used[i]:
            continue
        group = [i]
        used[i] = True
        frontier = [i]
        while frontier:
            k = frontier.pop()
            ck = clusters[k]
            u = np.array([math.cos(ck.angle), math.sin(ck.angle)])
            n = np.array([-u[1], u[0]])
            for j in tree.query_ball_point(ck.center, ck.length / 2 + 40):
                if used[j]:
                    continue
                cj = clusters[j]
                dang = abs((math.degrees(cj.angle - ck.angle) + 90) % 180 - 90)
                if dang > 12:
                    continue
                d = np.array(cj.center) - np.array(ck.center)
                along = abs(float(d @ u)) - (ck.length + cj.length) / 2
                across = abs(float(d @ n))
                if along <= gap and across <= 0.5 * max(ck.height, cj.height):
                    used[j] = True
                    group.append(j)
                    frontier.append(j)
        if len(group) == 1:
            out.append(a)
            continue
        members = [clusters[k] for k in group]
        ang = members[0].angle
        u = np.array([math.cos(ang), math.sin(ang)])
        n = np.array([-u[1], u[0]])
        c0 = np.array(members[0].center)
        ts, rs = [], []
        for m in members:
            d = np.array(m.center) - c0
            t0 = float(d @ u)
            ts += [t0 - m.length / 2, t0 + m.length / 2]
            rs += [float(d @ n) - m.height / 2, float(d @ n) + m.height / 2]
        L = max(ts) - min(ts)
        H = max(rs) - min(rs)
        cx = c0 + u * (max(ts) + min(ts)) / 2 + n * (max(rs) + min(rs)) / 2
        hl, hh = L / 2, H / 2
        corners = np.array([[cx[0] + sx * hl * u[0] + sy * hh * n[0], cx[1] + sx * hl * u[1] + sy * hh * n[1]]
                            for sx in (-1, 1) for sy in (-1, 1)])
        bbox = (float(corners[:, 0].min()), float(corners[:, 1].min()), float(corners[:, 0].max()), float(corners[:, 1].max()))
        out.append(TextCluster(id=len(out), center=(float(cx[0]), float(cx[1])), angle=ang, length=L, height=H,
                               stroke_idx=sum((m.stroke_idx for m in members), []),
                               gray=float(np.mean([m.gray for m in members])), color=members[0].color, bbox=bbox,
                               n_curvy=sum(m.n_curvy for m in members),
                               letter_idx=(sum(((m.letter_idx if m.letter_idx is not None else m.stroke_idx) for m in members), [])
                                           if any(m.letter_idx is not None for m in members) else None)))
    for k, c in enumerate(out):
        c.id = k
    return out
