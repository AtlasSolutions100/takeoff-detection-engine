"""Join PDF strokes into chains (continuous lines, dashed or solid) and group
them into style families. Contour candidates are the long, smooth chains.

Coordinates are page points (rotation-aware). Nothing here knows about
elevations; that is labels.py / elevations.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from .geom import SheetTooBig, end_tangent, poly_length, turning_stats, resample
from .ingest import Stroke
from .readslot import check_current


@dataclass
class StyleGroup:
    id: int
    color: tuple
    gray: float
    width: float
    layer: str
    dashed: bool
    n_strokes: int = 0
    n_chains: int = 0
    n_candidates: int = 0
    cand_length: float = 0.0
    total_length: float = 0.0
    median_stroke: float = 0.0
    link_radius: float = 0.0

    @property
    def sig(self) -> str:
        return style_sig(self.color, self.width, self.dashed, self.layer)

    def to_json(self) -> dict:
        return {
            "id": self.id, "sig": self.sig, "color": [round(c, 3) for c in self.color], "gray": round(self.gray, 3),
            "width": round(self.width, 2), "layer": self.layer, "dashed": self.dashed,
            "n_strokes": self.n_strokes, "n_chains": self.n_chains, "n_candidates": self.n_candidates,
            "cand_length": round(self.cand_length, 1), "total_length": round(self.total_length, 1),
        }


def style_sig(color, width: float, dashed: bool, layer: str) -> str:
    """A stable name for a line style: what the operator's role picks are keyed by. Style ids are
    ordinals that renumber whenever the chaining changes; this does not."""
    r, g, b = (round(float(c) * 20) / 20 for c in color)
    return f"{r:.2f},{g:.2f},{b:.2f}|w{round(float(width), 1):.1f}|{'d' if dashed else 's'}|{layer or ''}"


@dataclass
class Chain:
    id: int
    style: int
    pts: np.ndarray
    members: list = field(default_factory=list)
    length: float = 0.0
    ink: float = 0.0
    closed: bool = False
    max_turn: float = 0.0
    mean_turn: float = 0.0
    n_sharp: int = 0
    candidate: bool = False

    def to_json(self, step: float = 3.0) -> dict:
        pts = resample(self.pts, step) if len(self.pts) > 2 else self.pts
        return {
            "id": self.id, "style": self.style, "length": round(self.length, 1), "ink": round(self.ink, 1),
            "closed": self.closed, "candidate": self.candidate, "n": len(self.members),
            "pts": [[round(float(x), 1), round(float(y), 1)] for x, y in pts],
        }


@dataclass
class Gap:
    """A break between two chain ends that probably holds a label."""
    id: int
    style: int
    a_chain: int
    a_end: int          # 0 = start of chain, 1 = end of chain
    b_chain: int
    b_end: int
    center: tuple
    angle: float        # radians, direction from a to b
    length: float

    def to_json(self) -> dict:
        return {"id": self.id, "style": self.style, "a": self.a_chain, "b": self.b_chain,
                "center": [round(self.center[0], 1), round(self.center[1], 1)],
                "angle_deg": round(math.degrees(self.angle), 1), "length": round(self.length, 1)}


def _style_key(s: Stroke) -> tuple:
    r, g, b = s.color
    return (round(r * 20) / 20, round(g * 20) / 20, round(b * 20) / 20, round(s.width, 1), s.layer)


# Joining lines (_chain_group) and finding gaps (find_gaps) keep the order the pair search hands the pairs
# over (ties in their sorts fall back on it), so they cannot be walked a slice at a time without moving
# results. Each gets its own bound instead, from what the code before D41 measured per pair, against one
# budget: what ONE step of one read may add on top of the read's own 1.2-1.9 GB.
STEP_BUDGET = 2_000_000_000          # bytes
#
# Joining: the step's own commit (peak minus the commit just before build_chains), measured 2026-10-01 on a
# synthetic dash field of one style, under a Job Object guard (phase2/mem/joinmeas.py):
#     pairs          step commit     B a pair   pairs rounded up to a power of two
#      8,053,950        763 MiB        99.3       2^23
#      9,422,857        998 MiB       111.1       2^24
#     12,682,558      1,253 MiB       103.6       2^24
#     16,357,662      1,540 MiB        98.7       2^24
#     17,301,487      1,870 MiB       113.4       2^25
#     18,251,149   >= 1,944 MiB        -          2^25   (stopped by the guard's 3,000 MB cap: MemoryError)
# The jumps sit at the powers of two: the pair search grows a buffer of 16 B pairs by doubling (critic 2 RISK-2).
# All four complete runs fit  step = 82.3-82.7 B x pairs + 16 B x (pairs rounded up to a power of two),  so the
# cost is counted as 84 B a pair plus that buffer (_join_cost), against the 2.0 GB budget: the largest style
# that fits is 17,418,203 pairs (MAX_JOIN_PAIRS). The 15.5 million pair field the old code read costs 1.57 GB
# here and reads; the most any real line style needs is 3,997,484 (Res-A page index 1), 4.4 times under.
JOIN_BYTES_PER_PAIR = 84
#
# Finding gaps: the pairs (16 B each, 32 B while the search builds them) and, for every pair that passes the
# distance and angle tests, a Python tuple (float, int64, int64, float) in a list. Its cost is counted before
# the loop (the same tests, in numpy, a slice at a time): pairs x 16 + candidates x 230 over 2.0 GB stops the
# read. Measured 2026-10-01 on the code before, synthetic collinear dash rows, Job Object guard: 3,049,151 pairs
# -> 291 MB working set, 6,176,316 pairs (1,445,894 candidates) -> 508 MB, from about 84 MB at the start: the
# step took about 424 MB, (424 - 99 MB of pairs) / 1,445,894 = 225 B a candidate (the critic's tracemalloc
# counted 176 B for the tuple alone), so 230. A style whose every pair is a candidate stops at about 8 million
# pairs; one like the measured rows (23 % candidates) at about 28 million. The most any real sheet needs: 1,244,163 pairs (hotel site C3).
GAP_BYTES_PER_PAIR = 16
GAP_BYTES_PER_CAND = 230
MAX_GAP_PAIRS = STEP_BUDGET // (2 * GAP_BYTES_PER_PAIR)      # 62.5 million: the search alone would pass the budget


def _join_cost(npairs: int) -> int:
    """Bytes the joining step takes for one style of `npairs` pairs (the measured model above)."""
    if npairs <= 0:
        return 0
    return JOIN_BYTES_PER_PAIR * npairs + 16 * (1 << (int(npairs) - 1).bit_length())


def _max_join_pairs() -> int:
    lo, hi = 0, 1 << 40
    while lo < hi:                       # the largest count whose cost fits the budget
        mid = (lo + hi + 1) // 2
        if _join_cost(mid) <= STEP_BUDGET:
            lo = mid
        else:
            hi = mid - 1
    return lo


MAX_JOIN_PAIRS = _max_join_pairs()           # 17,418,203 for 2.0 GB (pinned by the tests)


def _count_pairs(tree: cKDTree, r: float) -> int:
    """How many pairs tree.query_pairs(r) would return, in O(n) memory."""
    n = int(tree.n)
    return (int(tree.count_neighbors(tree, r)) - n) // 2 if n >= 2 else 0


def _bounded_pairs(tree: cKDTree, r: float, what: str) -> np.ndarray:
    """The joining's tree.query_pairs(r) as an ndarray, after counting them: past the budget the read stops."""
    npairs = _count_pairs(tree, r)
    if _join_cost(npairs) > STEP_BUDGET:
        raise SheetTooBig(f"{what}: {npairs:,} pairs of line ends in one line style "
                          f"(about {_join_cost(npairs) / 1e9:.1f} GB; the limit is {STEP_BUDGET / 1e9:.1f} GB)")
    return tree.query_pairs(r, output_type="ndarray")


def _gap_cost(pairs: np.ndarray, E: np.ndarray, T: np.ndarray, end_ok: np.ndarray, min_len: float,
              cos_lim: float, chunk: int = 2_000_000) -> int:
    """Bytes find_gaps' loop would hold for these pairs: the same tests as the loop, in numpy, a slice at a time
    (an estimate: a float rounding at a threshold can move one pair either way)."""
    n_cand = 0
    for s in range(0, len(pairs), chunk):
        a = pairs[s:s + chunk, 0]
        b = pairs[s:s + chunk, 1]
        v = E[b] - E[a]
        d = np.hypot(v[:, 0], v[:, 1])
        ok = (a // 2 != b // 2) & end_ok[a] & end_ok[b] & (d >= min_len)
        dn = np.where(d > 0, d, 1.0)
        ca = (T[a, 0] * v[:, 0] + T[a, 1] * v[:, 1]) / dn
        cb = -(T[b, 0] * v[:, 0] + T[b, 1] * v[:, 1]) / dn
        n_cand += int(np.count_nonzero(ok & (ca >= cos_lim) & (cb >= cos_lim)))
    return len(pairs) * GAP_BYTES_PER_PAIR + n_cand * GAP_BYTES_PER_CAND


def usable(s: Stroke) -> bool:
    if s.filled:
        return False
    if min(s.color) > 0.92:          # white-out masks
        return False
    if s.opacity < 0.05:
        return False
    return s.length > 1e-6


def drop_dot_fields(strokes: list[Stroke], dot_len: float = 1.2, radius: float = 6.0, max_neighbors: int = 5) -> list[Stroke]:
    """Stipple/hatch textures are thousands of ~0.1-1pt dots; a dot that has more
    than `max_neighbors` other dots within `radius` is texture, not a dashed line
    (line dots have ~2 neighbours)."""
    dots = [s for s in strokes if s.length < dot_len and not s.closed]
    if len(dots) < 50:
        return strokes
    C = np.array([s.pts.mean(axis=0) for s in dots])
    tree = cKDTree(C)
    # counts only: the neighbour LISTS of a dense stipple field are the D41 runaway (Res-A civil C3.0:
    # 340,624 dots, 1,584,800,780 neighbours within 6 pt, up to 10,108 for one dot; as Python lists
    # that passed 16 GB in memory and kept climbing). The counts are the same numbers.
    counts = np.asarray(tree.query_ball_point(C, radius, return_length=True), dtype=np.int64) - 1
    kill = {s.idx for s, n in zip(dots, counts) if n > max_neighbors}
    return [s for s in strokes if s.idx not in kill]


def rebuild_strips(strokes: list[Stroke], max_perim: float = 30.0, max_pts: int = 5, min_tris: int = 4) -> list[Stroke]:
    """CAD exports draw a wide polyline as a strip of tiny closed triangles that share edges
    (one civil firm's CP-TOPO layers: 1,700 triangles of 4-5 pt perimeter per sheet). Each strip becomes one
    open stroke along its centreline (the midpoints of the shared edges) so the line can be chained
    and labelled like any other. Lone small triangles (arrowheads) are left as they are."""
    from collections import defaultdict
    def is_tiny(s):
        # a strip member: a closed shape of 3-5 vertices that is thin (area small against its perimeter),
        # whatever its size: the export draws long skinny triangles along straight runs
        if not s.closed or not (3 <= len(s.pts) <= max_pts + 1) or s.length > 300.0:
            return False
        pts = s.pts[:-1] if len(s.pts) > 3 and np.allclose(s.pts[0], s.pts[-1]) else s.pts
        if len(pts) < 3:
            return False
        x, y = pts[:, 0], pts[:, 1]
        area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
        return s.length <= max_perim or area / max(s.length, 1e-6) ** 2 < 0.045
    tiny = [s for s in strokes if is_tiny(s)]
    # the other way a CAD export draws a wide polyline: every segment as a thin closed sliver
    # (a 2-pt wide, 17-pt long polygon with rounded joins). Its centreline is the mean of its two
    # long sides.
    keep = []
    slivers = 0
    next_idx0 = max((s.idx for s in strokes), default=0) + 1
    for s in strokes:
        if is_tiny(s):
            continue
        sl = _as_sliver(s, next_idx0)
        if sl is not None:
            keep.append(sl)
            next_idx0 += 1
            slivers += 1
            continue
        keep.append(s)
    if len(tiny) < 4 * min_tris:
        return keep
    by_style: dict = defaultdict(list)
    for s in tiny:
        by_style[_style_key(s)].append(s)
    next_idx = max(next_idx0, max((s.idx for s in strokes), default=0) + 1)
    made = 0
    for key, group in by_style.items():
        if len(group) < min_tris:
            keep.extend(group)
            continue
        tri_edges = []
        edge_owner: dict = defaultdict(list)
        for ti, s in enumerate(group):
            pts = s.pts[:-1] if len(s.pts) > 3 and np.allclose(s.pts[0], s.pts[-1]) else s.pts
            vk = [tuple(np.round(p_ / 0.05).astype(int)) for p_ in pts]
            edges = [frozenset((vk[i], vk[(i + 1) % len(vk)])) for i in range(len(vk))]
            tri_edges.append(edges)
            for e in edges:
                edge_owner[e].append(ti)
        adj: dict = defaultdict(set)
        for e, owners in edge_owner.items():
            if len(owners) == 2:
                adj[owners[0]].add(owners[1])
                adj[owners[1]].add(owners[0])
        seen: set = set()
        for start in range(len(group)):
            if start in seen:
                continue
            comp = []
            stack = [start]
            seen.add(start)
            while stack:
                t = stack.pop()
                comp.append(t)
                for u in adj[t]:
                    if u not in seen:
                        seen.add(u)
                        stack.append(u)
            if len(comp) < min_tris:
                for t in comp:
                    sl = _as_sliver(group[t], next_idx)
                    if sl is not None:
                        keep.append(sl)
                        next_idx += 1
                    else:
                        keep.append(group[t])
                continue
            comp_set = set(comp)
            visited: set = set()
            deg = {t: len(adj[t] & comp_set) for t in comp}
            starts = sorted(comp, key=lambda t: (deg[t], t))       # walk from the strip ends first
            for st in starts:
                if st in visited:
                    continue
                order = [st]
                visited.add(st)
                cur = st
                while True:
                    nxt = [u for u in adj[cur] if u in comp_set and u not in visited]
                    if not nxt:
                        break
                    cur = nxt[0]
                    order.append(cur)
                    visited.add(cur)
                centre = []
                for a, b in zip(order, order[1:]):
                    shared = set(tri_edges[a]) & set(tri_edges[b])
                    if not shared:
                        continue
                    v1, v2 = list(next(iter(shared)))
                    centre.append((np.array(v1, dtype=float) + np.array(v2, dtype=float)) * 0.025)
                if len(centre) >= 2:
                    keep.append(Stroke(pts=np.array(centre), color=group[st].color, width=max(group[st].width, 0.3),
                                       layer=group[st].layer, closed=False, dash=False, opacity=group[st].opacity,
                                       filled=False, idx=next_idx))
                    next_idx += 1
                    made += 1
                else:
                    for t in order:
                        sl = _as_sliver(group[t], next_idx)
                        if sl is not None:
                            keep.append(sl)
                            next_idx += 1
                        else:
                            keep.append(group[t])
    return keep


def _as_sliver(s: Stroke, idx: int) -> Stroke | None:
    """A thin closed polygon (a wide-polyline segment as CAD exports draw it) as one open stroke
    along its centreline; None when the shape is not a sliver."""
    if not s.closed or not (6.0 <= s.length <= 400.0) or len(s.pts) < 4:
        return None
    pts = s.pts[:-1] if len(s.pts) > 3 and np.allclose(s.pts[0], s.pts[-1]) else s.pts
    if len(pts) < 3:
        return None
    x, y = pts[:, 0], pts[:, 1]
    area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
    if area / max(s.length, 1e-6) ** 2 >= 0.045:          # w x L sliver: w*L / (2L+2w)^2 ~ w/4L; a square is 1/16
        return None
    if len(pts) == 3:
        # a lone thin triangle: its spine, from the middle of the shortest side to the far corner
        e = [np.linalg.norm(pts[(i + 1) % 3] - pts[i]) for i in range(3)]
        k = int(np.argmin(e))
        mid = (pts[k] + pts[(k + 1) % 3]) / 2.0
        cl = np.array([mid, pts[(k + 2) % 3]])
        return Stroke(pts=cl, color=s.color, width=max(s.width, 0.3), layer=s.layer, closed=False, dash=False,
                      opacity=s.opacity, filled=False, idx=idx)
    cl = _sliver_centreline(pts)
    if cl is None or len(cl) < 2:
        return None
    return Stroke(pts=cl, color=s.color, width=max(s.width, 0.3), layer=s.layer, closed=False, dash=False,
                  opacity=s.opacity, filled=False, idx=idx)


def _sliver_centreline(pts: np.ndarray) -> np.ndarray | None:
    """Centreline of a thin closed polygon: split the ring at its two farthest-apart vertices,
    resample both sides to the same count and average them."""
    n = len(pts)
    if n < 4:
        return None
    d = np.linalg.norm(pts[:, None, :] - pts[None, :, :], axis=2)
    i, j = np.unravel_index(int(np.argmax(d)), d.shape)
    if i > j:
        i, j = j, i
    side_a = pts[i:j + 1]
    side_b = np.vstack([pts[j:], pts[:i + 1]])[::-1]          # the other way round the ring, same direction as a
    if len(side_a) < 2 or len(side_b) < 2:
        return None
    k = max(len(side_a), len(side_b), 3)
    ra = resample(side_a, max(poly_length(side_a) / (k - 1), 0.2))
    rb = resample(side_b, max(poly_length(side_b) / (k - 1), 0.2))
    m = min(len(ra), len(rb))
    if m < 2:
        return None
    ia = np.linspace(0, len(ra) - 1, m).round().astype(int)
    ib = np.linspace(0, len(rb) - 1, m).round().astype(int)
    c = (ra[ia] + rb[ib]) / 2.0
    return c[1:-1] if len(c) >= 4 else c        # the split points are corners, not the line's ends


def build_chains(strokes: list[Stroke], min_candidate_len: float = 40.0,
                 exclude_idx: set | None = None) -> tuple[list[Chain], list[StyleGroup]]:
    exclude_idx = exclude_idx or set()
    strokes = rebuild_strips([s for s in strokes if usable(s) and s.idx not in exclude_idx])
    strokes = drop_dot_fields(strokes)
    groups: dict[tuple, list[Stroke]] = {}
    for s in strokes:
        groups.setdefault(_style_key(s), []).append(s)

    chains: list[Chain] = []
    styles: list[StyleGroup] = []
    for gid, (key, members) in enumerate(sorted(groups.items(), key=lambda kv: -len(kv[1]))):
        check_current()                  # a read told to stop (readslot) ends between two line styles
        r, g, b, w, layer = key
        lens = np.array([m.length for m in members])
        med = float(np.median(lens)) if len(lens) else 0.0
        short_frac = float((lens < 12).mean()) if len(lens) else 0.0
        dashed = short_frac >= 0.5 and len(members) >= 12
        if dashed:
            med = float(np.median(lens[lens < 12])) if (lens < 12).any() else med
            R = float(np.clip(3.0 * med, 4.0, 36.0))
        elif len(members) >= 12 and med < 60:
            # long-dash patterns (15-40 pt dashes): reach a little more than one dash
            R = float(np.clip(1.2 * med, 1.5, 36.0))
        else:
            R = 1.5
        sg = StyleGroup(id=gid, color=(r, g, b), gray=0.299 * r + 0.587 * g + 0.114 * b, width=w, layer=layer,
                        dashed=dashed, n_strokes=len(members), median_stroke=med, link_radius=R,
                        total_length=float(lens.sum()))
        gchains = _chain_group(members, gid, R, dashed, len(chains))
        for c in gchains:
            c.length = poly_length(c.pts)
            c.max_turn, c.mean_turn, c.n_sharp = turning_stats(c.pts, 4.0)
            n_samp = max(1, int(c.length / 4.0))
            # long and mostly smooth; a swale / ridge contour may hold one or two sharp V's
            # long open polylines (graded contours) kink at every lot line; short ones and
            # closed shapes (buildings, ovals) must stay smooth
            sharp_cap = max(2, int(0.03 * n_samp)) if (c.closed or c.length < 300) else max(2, int(0.15 * n_samp))
            c.candidate = (c.length >= min_candidate_len and c.n_sharp <= sharp_cap and c.mean_turn <= 25.0)
            if c.candidate:
                sg.n_candidates += 1
                sg.cand_length += c.length
        sg.n_chains = len(gchains)
        # "dashed" judged on the long chains themselves: how much of their length is gap
        gf = [1.0 - (ch.ink / ch.length) for ch in gchains if ch.candidate and ch.length > 0]
        if gf:
            sg.dashed = bool(np.median(gf) > 0.12)
        chains.extend(gchains)
        styles.append(sg)
    return chains, styles


def _chain_group(members: list[Stroke], gid: int, R: float, dashed: bool, id0: int) -> list[Chain]:
    n = len(members)
    if n == 0:
        return []
    # closed strokes are chains by themselves
    open_idx = [i for i, m in enumerate(members) if not m.closed]
    chains: list[Chain] = []
    cid = id0
    for i, m in enumerate(members):
        if m.closed:
            chains.append(Chain(id=cid, style=gid, pts=m.pts.copy(), members=[m.idx], ink=m.length, closed=True))
            cid += 1
    if not open_idx:
        return chains
    E = np.zeros((2 * len(open_idx), 2))
    T = np.zeros((2 * len(open_idx), 2))
    L = np.zeros(len(open_idx))
    for k, i in enumerate(open_idx):
        m = members[i]
        E[2 * k] = m.pts[0]
        E[2 * k + 1] = m.pts[-1]
        T[2 * k] = end_tangent(m.pts, False)
        T[2 * k + 1] = end_tangent(m.pts, True)
        L[k] = m.length
    tree = cKDTree(E)
    pairs = _bounded_pairs(tree, R, "joining lines")
    cos_lim = math.cos(math.radians(40))
    cands: list[tuple[float, int, int]] = []
    if len(pairs):
        a = pairs[:, 0]
        b = pairs[:, 1]
        ka = a // 2
        kb = b // 2
        keep = ka != kb
        a, b, ka, kb = a[keep], b[keep], ka[keep], kb[keep]
        if len(a) > 3_000_000:            # pathological texture: keep the closest pairs only
            d0 = np.linalg.norm(E[b] - E[a], axis=1)
            sel = np.argsort(d0)[:3_000_000]
            a, b, ka, kb = a[sel], b[sel], ka[sel], kb[sel]
        dv = E[b] - E[a]
        d = np.linalg.norm(dv, axis=1)
        dn = np.where(d > 1e-9, d, 1.0)
        v = dv / dn[:, None]
        ca = np.einsum("ij,ij->i", T[a], v)
        cb = np.einsum("ij,ij->i", T[b], -v)
        dot_a = L[ka] < 2.5
        dot_b = L[kb] < 2.5
        touching = d <= 0.75
        both_dots = dot_a & dot_b
        ok = (touching | both_dots
              | (dot_a & ~dot_b & (cb >= cos_lim))
              | (~dot_a & dot_b & (ca >= cos_lim))
              | (~dot_a & ~dot_b & (ca >= cos_lim) & (cb >= cos_lim)))
        pen = np.where(both_dots, 0.0, 2.0 - np.where(dot_a, 0.0, ca) - np.where(dot_b, 0.0, cb))
        score = d + 6.0 * pen
        idx = np.nonzero(ok)[0]
        order = idx[np.argsort(score[idx], kind="stable")]
        cands = [(float(score[i]), int(a[i]), int(b[i])) for i in order]
    link = -np.ones(2 * len(open_idx), dtype=int)
    for _, a, b in cands:
        if link[a] < 0 and link[b] < 0:
            link[a] = b
            link[b] = a
    # walk chains
    visited = np.zeros(len(open_idx), dtype=bool)

    def walk(start_end: int) -> list[tuple[int, bool]]:
        seq = []
        e = start_end
        while True:
            k = e // 2
            if visited[k]:
                break
            visited[k] = True
            entering_at_start = (e % 2 == 0)
            seq.append((k, not entering_at_start))     # reversed if we entered at its end
            other = e + 1 if entering_at_start else e - 1
            nxt = link[other]
            if nxt < 0:
                break
            e = nxt
        return seq

    order: list[list[tuple[int, bool]]] = []
    for k in range(len(open_idx)):
        if visited[k]:
            continue
        if link[2 * k] < 0:
            order.append(walk(2 * k))
        elif link[2 * k + 1] < 0:
            order.append(walk(2 * k + 1))
    for k in range(len(open_idx)):          # remaining are cycles
        if not visited[k]:
            order.append(walk(2 * k))
    for seq in order:
        pts_list = []
        mem = []
        ink = 0.0
        for k, rev in seq:
            m = members[open_idx[k]]
            p = m.pts[::-1] if rev else m.pts
            if pts_list and np.allclose(pts_list[-1][-1], p[0], atol=1e-6):
                p = p[1:]
            pts_list.append(p)
            mem.append(m.idx)
            ink += m.length
        pts = np.vstack(pts_list)
        chains.append(Chain(id=cid, style=gid, pts=pts, members=mem, ink=ink, closed=False))
        cid += 1
    return chains


def find_gaps(chains: list[Chain], min_len: float = 5.0, max_len: float = 90.0,
              min_chain_len: float = 12.0, max_angle_deg: float = 30.0,
              exclude_ends: set | None = None, first_id: int = 0) -> list[Gap]:
    """Pairs of chain ends facing each other across a break (label gaps).
    `exclude_ends` = {(chain id, end)} already paired by an earlier pass; `first_id` numbers
    the gaps after that pass's."""
    by_style: dict[int, list[Chain]] = {}
    for c in chains:
        if c.closed or c.length < min_chain_len:
            continue
        by_style.setdefault(c.style, []).append(c)
    gaps: list[Gap] = []
    gid = first_id
    exclude_ends = exclude_ends or set()
    cos_lim = math.cos(math.radians(max_angle_deg))
    for sid, cs in by_style.items():
        check_current()
        E = np.zeros((2 * len(cs), 2))
        T = np.zeros((2 * len(cs), 2))
        for k, c in enumerate(cs):
            E[2 * k] = c.pts[0]
            E[2 * k + 1] = c.pts[-1]
            T[2 * k] = end_tangent(c.pts, False, lookback=8.0)
            T[2 * k + 1] = end_tangent(c.pts, True, lookback=8.0)
        tree = cKDTree(E)
        npairs = _count_pairs(tree, max_len)
        if npairs > MAX_GAP_PAIRS:
            raise SheetTooBig(f"finding gaps in lines: {npairs:,} pairs of line ends in one line style")
        pairs = tree.query_pairs(max_len, output_type="ndarray")
        end_ok = np.array([(cs[i // 2].id, i % 2) not in exclude_ends for i in range(len(E))], dtype=bool)
        cost = _gap_cost(pairs, E, T, end_ok, min_len, cos_lim)
        if cost > STEP_BUDGET:
            raise SheetTooBig(f"finding gaps in lines: {npairs:,} pairs of line ends in one line style "
                              f"(about {cost / 1e9:.1f} GB; the limit is {STEP_BUDGET / 1e9:.1f} GB)")
        cands = []
        for a, b in pairs:
            if a // 2 == b // 2:
                continue
            if (cs[a // 2].id, a % 2) in exclude_ends or (cs[b // 2].id, b % 2) in exclude_ends:
                continue
            d = float(np.linalg.norm(E[b] - E[a]))
            if d < min_len:
                continue
            v = (E[b] - E[a]) / d
            ca = float(T[a] @ v)
            cb = float(T[b] @ (-v))
            if ca < cos_lim or cb < cos_lim:
                continue
            cands.append((d + 20.0 * (2 - ca - cb), a, b, d))
        cands.sort(key=lambda t: t[0])
        used = set()
        for _, a, b, d in cands:
            if a in used or b in used:
                continue
            used.add(a)
            used.add(b)
            ca_, cb_ = cs[a // 2], cs[b // 2]
            v = E[b] - E[a]
            ang = math.atan2(v[1], v[0])
            center = ((E[a][0] + E[b][0]) / 2, (E[a][1] + E[b][1]) / 2)
            gaps.append(Gap(id=gid, style=sid, a_chain=ca_.id, a_end=a % 2, b_chain=cb_.id, b_end=b % 2,
                            center=center, angle=ang, length=d))
            gid += 1
    return gaps
