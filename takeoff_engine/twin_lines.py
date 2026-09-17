"""Twin lines counted once: one run per wall, on its outside face.

A foundation plan draws every wall as TWO parallel lines, its two faces, and
every continuous footing as two more, its two edges. Measuring a family as
drawn therefore bills each wall about twice. Both clients take one run per
wall: Jeff Ellis measures "the outside dimension of wall" and uses that one
line for the footing and the wall alike (footing video 05:48-05:57);
Beaumont's own 19 length measurements on Barn S1-1.3 total 704.4 ft where
the dashed footing family is 1,055 ft as drawn (FINDINGS 30).

What the drawings show (measured 2026-09-17 on the local sheets):
  Barn S1-1.3, 3/16" = 1'-0": the dashed footing family's parallel partners
  sit at 2.0 / 3.0 / 4.0 / 5.0 / 5.5 / 7.0 / 8.0 ft - the footing
  SCHEDULE's own widths - and the heaviest solid family's at 0.75 ft.
  Ellis S1.02, 1/8" = 1'-0": the wall families pair at 0.5-1.0 ft, beside a
  0.25 ft spacing that is not a wall thickness.
So the pairing band comes from the sheet: a footing family pairs at its
schedule widths, a wall family at a wall thickness (5-17 in). Nothing is
paired on nearness alone.

THE RULE, per sample (one every `step_ft` along every chain):
  1. partner = the nearest point on ANOTHER chain of the same family that
     is parallel (within `ang_deg`), faces it squarely (the offset is mostly
     perpendicular) and sits at an allowed spacing. The pair holds only when
     the partner's own nearest partner is back on this chain (mutual, per
     sample: a long outside face pairs with every piece of an inside face
     broken at the junctions, and a third parallel line cannot steal one).
  2. which face counts: the family's lines are drawn onto a half-foot grid,
     thickened to close gaps up to 3 ft (a door), and the grid is flooded
     from its border. A face whose far side floods is an OUTSIDE face and is
     counted; its twin is not. Neither side flooded is an interior wall and
     both sides flooded is a free-standing one: one face is counted, the
     lower chain id's, and the stretch says so.
  3. an unpaired sample counts in full.
A family's counted length = unpaired + the faces kept. The as-drawn length
is never changed; the counted length sits beside it.

Count-first crowd guard (the CO3 lesson): a family over `max_samples`
samples is refused with a reason, not paired.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

import numpy as np

WALL_BAND_FT = (0.40, 1.40)       # 5-17 in: the wall thicknesses a foundation plan draws
WIDTH_TOL_FT = 0.20               # a schedule width, as plotted
FOOTING_BAND_FT = (1.50, 10.0)    # a footing family whose schedule could not be read
GRID_FT = 0.5
CLOSE_GAP_FT = 3.0
MAX_SAMPLES = 60000
MAX_CELLS = 6_000_000
MIN_MEMBER_FT = 0.10              # a line this close to a face is part of the same wall


def _resample(pts: np.ndarray, step: float):
    """Samples every `step` along a polyline: points, unit tangents, the arc
    length each stands for, and its arc position from the chain's start."""
    seg = np.diff(pts, axis=0)
    L = np.hypot(seg[:, 0], seg[:, 1])
    P, U, W, S = [], [], [], []
    s0 = 0.0
    for (sx, sy), ln, (ax, ay) in zip(seg, L, pts[:-1]):
        if ln <= 1e-9:
            continue
        n = max(1, int(math.ceil(ln / step)))
        t = (np.arange(n) + 0.5) / n
        P.append(np.column_stack((ax + sx * t, ay + sy * t)))
        U.append(np.tile([sx / ln, sy / ln], (n, 1)))
        W.append(np.full(n, ln / n))
        S.append(s0 + t * ln)
        s0 += ln
    if not P:
        z = np.zeros((0, 2))
        return z, z, np.zeros(0), np.zeros(0)
    return np.vstack(P), np.vstack(U), np.concatenate(W), np.concatenate(S)


def _allowed(spacing_ft: float, widths_ft, band_ft) -> bool:
    if widths_ft:
        return any(abs(spacing_ft - w) <= WIDTH_TOL_FT for w in widths_ft)
    return band_ft[0] <= spacing_ft <= band_ft[1]


class _Outside:
    """Which grid cells lie outside the family's drawn footprint."""

    def __init__(self, polys, ft_per_pt):
        from scipy import ndimage
        allp = np.vstack(list(polys))
        g = GRID_FT / ft_per_pt
        pad_cells = int(math.ceil(CLOSE_GAP_FT / 2 / GRID_FT)) + 4
        lo = allp.min(axis=0) - pad_cells * g
        hi = allp.max(axis=0) + pad_cells * g
        nx = int(math.ceil((hi[0] - lo[0]) / g)) + 1
        ny = int(math.ceil((hi[1] - lo[1]) / g)) + 1
        while nx * ny > MAX_CELLS:
            g *= 1.5
            nx = int(math.ceil((hi[0] - lo[0]) / g)) + 1
            ny = int(math.ceil((hi[1] - lo[1]) / g)) + 1
        self.g, self.lo, self.nx, self.ny = g, lo, nx, ny
        bar = np.zeros((ny, nx), bool)
        for p in polys:
            for a, b in zip(p[:-1], p[1:]):
                ln = float(np.hypot(*(b - a)))
                k = max(2, int(math.ceil(ln / (g / 2))) + 1)
                t = np.linspace(0.0, 1.0, k)
                xs = ((a[0] + (b[0] - a[0]) * t - lo[0]) / g).astype(int)
                ys = ((a[1] + (b[1] - a[1]) * t - lo[1]) / g).astype(int)
                ok = (xs >= 0) & (xs < nx) & (ys >= 0) & (ys < ny)
                bar[ys[ok], xs[ok]] = True
        self.r = max(1, int(round(CLOSE_GAP_FT / 2 / (g * ft_per_pt))))
        yy, xx = np.mgrid[-self.r:self.r + 1, -self.r:self.r + 1]
        disk = (xx * xx + yy * yy) <= self.r * self.r
        self.bar = ndimage.binary_dilation(bar, structure=disk)
        lab, _n = ndimage.label(~self.bar)
        edge = set(np.unique(np.concatenate((lab[0], lab[-1], lab[:, 0], lab[:, -1])))) - {0}
        self.out = np.isin(lab, list(edge))

    def side(self, p, v):
        """True / False / None: whether stepping from p along v reaches an
        outside cell before a barrier-free inside one (None: off the grid)."""
        for k in range(self.r + 1, self.r + 12):
            x = int((p[0] + v[0] * k * self.g - self.lo[0]) / self.g)
            y = int((p[1] + v[1] * k * self.g - self.lo[1]) / self.g)
            if not (0 <= x < self.nx and 0 <= y < self.ny):
                return True           # stepped off the drawing: outside
            if self.bar[y, x]:
                continue
            return bool(self.out[y, x])
        return None


def _intervals(pos, w, keep_mask):
    """Merged [s0, s1] arc ranges (pt) of the samples NOT kept."""
    out = []
    order = np.argsort(pos)
    for i in order:
        if keep_mask[i]:
            continue
        a, b = pos[i] - w[i] / 2, pos[i] + w[i] / 2
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [[round(a, 1), round(b, 1)] for a, b in out]


def twin_shares(chains: list, ft_per_pt: float, widths_ft=None,
                band_ft=WALL_BAND_FT, step_ft: float = 0.5,
                ang_deg: float = 8.0, max_samples: int = MAX_SAMPLES) -> dict:
    """{"chains": {chain_id: {"drawn_pt", "counted_pt", "paired_pt",
    "outside_pt", "interior_pt", "spacing_ft", "partner", "skip"}},
    "drawn_ft", "counted_ft", "paired_ft", "interior_ft",
    "spacing_ft": {bin: ft}, "samples", "refused", "rule"}.

    `chains`: rows with chain_id and points (page pt), ONE family.
    `widths_ft`: the schedule's footing widths for a footing family;
    otherwise `band_ft`, a wall thickness, bounds the pairing.
    `skip` lists the arc ranges (pt from the chain's start) not counted."""
    out = {"chains": {}, "drawn_ft": 0.0, "counted_ft": 0.0, "paired_ft": 0.0,
           "interior_ft": 0.0, "spacing_ft": {}, "samples": 0, "refused": None,
           "rule": ("schedule widths " + ", ".join(f"{w:g}" for w in sorted(widths_ft)) + " ft"
                    if widths_ft else f"wall thickness {band_ft[0]:g}-{band_ft[1]:g} ft")}
    if not chains or not ft_per_pt or ft_per_pt <= 0:
        return out
    step = max(step_ft / ft_per_pt, 1.0)
    P, U, W, S, C = [], [], [], [], []
    polys = {}
    for ch in chains:
        pts = np.asarray(ch.get("points") or [], float)
        if pts.ndim != 2 or len(pts) < 2:
            continue
        cid = int(ch["chain_id"])
        p, u, w, s = _resample(pts, step)
        if not len(p):
            continue
        polys[cid] = pts
        P.append(p); U.append(u); W.append(w); S.append(s); C.append(np.full(len(p), cid))
    if not P:
        return out
    P, U, W, S, C = np.vstack(P), np.vstack(U), np.concatenate(W), np.concatenate(S), np.concatenate(C)
    n = len(P)
    out["samples"] = int(n)
    drawn = defaultdict(float)
    for cid, w in zip(C, W):
        drawn[int(cid)] += float(w)

    def plain(reason):
        out["refused"] = reason
        for cid, d in drawn.items():
            out["chains"][cid] = {"drawn_pt": round(d, 2), "counted_pt": round(d, 2), "paired_pt": 0.0,
                                  "outside_pt": 0.0, "interior_pt": 0.0, "spacing_ft": None,
                                  "partner": None, "skip": []}
        out["drawn_ft"] = out["counted_ft"] = round(sum(drawn.values()) * ft_per_pt, 1)
        return out

    if n > max_samples:
        return plain(f"{n:,} samples, over the {max_samples:,} this pass pairs - "
                     "crop the family with a work area first")
    if len(polys) < 2:
        return plain(None)

    from scipy.spatial import cKDTree
    wall_mode = not widths_ft and band_ft[1] <= FOOTING_BAND_FT[0]
    hi_ft = (max(widths_ft) + WIDTH_TOL_FT) if widths_ft else band_ft[1]
    lo_ft = (min(widths_ft) - WIDTH_TOL_FT) if widths_ft else band_ft[0]
    tree = cKDTree(P)
    sin_t = math.sin(math.radians(ang_deg))
    best_q = np.full(n, -1)
    best_d = np.full(n, np.inf)
    middle = np.zeros(n, bool)
    for i, nb in enumerate(tree.query_ball_point(P, r=hi_ft / ft_per_pt + step)):
        ci = C[i]
        ux, uy = U[i]
        # WALL MODE: every parallel line within one wall thickness is part of
        # THIS wall - the two faces and anything drawn between them (Ellis's
        # "SPAN DECK PANEL 4\" LEDGE" line sits 4 in inside the face, so its
        # walls are three lines). A line with wall lines on BOTH sides is a
        # middle line and is never counted; a face pairs with the FARTHEST
        # line on its side, the opposite face. FOOTING MODE: the nearest
        # partner at a schedule width (two separate footings can run closer
        # together than the widest footing, so no cluster is formed there).
        far = {True: (0.0, -1), False: (0.0, -1)}
        for j in nb:
            if C[j] == ci or abs(ux * U[j][1] - uy * U[j][0]) > sin_t:
                continue
            dx, dy = P[j][0] - P[i][0], P[j][1] - P[i][1]
            signed = -dx * uy + dy * ux
            perp = abs(signed)
            if abs(dx * ux + dy * uy) > max(0.5 * perp, step):
                continue
            d_ft = perp * ft_per_pt
            if wall_mode:
                if MIN_MEMBER_FT <= d_ft <= hi_ft and d_ft > far[signed > 0][0]:
                    far[signed > 0] = (d_ft, j)
                continue
            if d_ft < lo_ft or d_ft > hi_ft or not _allowed(d_ft, widths_ft, band_ft):
                continue
            if perp < best_d[i]:
                best_d[i], best_q[i] = perp, j
        if wall_mode:
            (dl, jl), (dr, jr) = far[True], far[False]
            if jl >= 0 and jr >= 0 and dl + dr <= hi_ft:
                middle[i] = True                   # a line inside a wall
                continue
            cand = [(d, j) for d, j in ((dl, jl), (dr, jr)) if j >= 0 and lo_ft <= d <= hi_ft]
            if cand:
                d, j = min(cand)                   # between two walls: its own is the tighter
                best_d[i], best_q[i] = d / ft_per_pt, j

    outside = _Outside(list(polys.values()), ft_per_pt)
    keep = np.ones(n, bool)
    paired = defaultdict(float)
    kept_out = defaultdict(float)
    kept_int = defaultdict(float)
    spacing = defaultdict(list)
    partners = defaultdict(Counter)
    spc_hist = Counter()
    middle_ft = defaultdict(float)
    for i in np.nonzero(middle)[0]:
        keep[i] = False                            # inside a wall: never counted
        paired[int(C[i])] += float(W[i])
        middle_ft[int(C[i])] += float(W[i])
    for i in range(n):
        j = best_q[i]
        if j < 0 or middle[i]:
            continue
        a, b = int(C[i]), int(C[j])
        jj = best_q[j]
        if jj < 0 or int(C[jj]) != a:
            continue                               # not mutual: counts in full
        w = float(W[i])
        paired[a] += w
        partners[a][b] += w
        d_ft = best_d[i] * ft_per_pt
        spacing[a].append(d_ft)
        spc_hist[round(d_ft * 4) / 4] += w * ft_per_pt
        v = P[i] - P[j]
        vn = float(np.hypot(*v))
        v = v / vn
        a_out = outside.side(P[i], v)
        b_out = outside.side(P[j], -v)
        if a_out is True and b_out is False:
            kept_out[a] += w                        # a is the outside face
        elif a_out is False and b_out is True:
            keep[i] = False                         # b is the outside face
        elif a < b:
            kept_int[a] += w                        # interior / free-standing: lower id kept
        else:
            keep[i] = False
    counted = defaultdict(float)
    for i in range(n):
        if keep[i]:
            counted[int(C[i])] += float(W[i])
    for cid, d in drawn.items():
        m = C == cid
        sp = spacing.get(cid)
        out["chains"][cid] = {
            "drawn_pt": round(d, 2), "counted_pt": round(counted.get(cid, 0.0), 2),
            "paired_pt": round(paired.get(cid, 0.0), 2),
            "outside_pt": round(kept_out.get(cid, 0.0), 2),
            "interior_pt": round(kept_int.get(cid, 0.0), 2),
            "middle_pt": round(middle_ft.get(cid, 0.0), 2),
            "spacing_ft": round(float(np.median(sp)), 3) if sp else None,
            "partner": (partners[cid].most_common(1)[0][0] if partners.get(cid) else None),
            "skip": _intervals(S[m], W[m], keep[m]),
        }
    out["drawn_ft"] = round(sum(drawn.values()) * ft_per_pt, 1)
    out["counted_ft"] = round(sum(counted.values()) * ft_per_pt, 1)
    out["paired_ft"] = round(sum(paired.values()) * ft_per_pt, 1)
    out["interior_ft"] = round(sum(kept_int.values()) * ft_per_pt, 1)
    out["middle_ft"] = round(sum(middle_ft.values()) * ft_per_pt, 1)
    out["spacing_ft"] = {f"{k:g}": round(v, 1) for k, v in sorted(spc_hist.items())}
    return out


def family_kind(spacing_hist: dict) -> str:
    """"wall" or "footing" from where a family's partners actually sit: most
    of its partnered footage under 1.5 ft is a wall's two faces."""
    wall = sum(v for k, v in spacing_hist.items() if float(k) < FOOTING_BAND_FT[0])
    foot = sum(v for k, v in spacing_hist.items() if float(k) >= FOOTING_BAND_FT[0])
    return "footing" if foot > wall else "wall"


def probe_spacings(chains: list, ft_per_pt: float, max_ft: float = 10.0) -> dict:
    """Where a family's parallel partners sit before any rule is chosen:
    {bin_ft: ft of line}, one sample per foot."""
    wide = twin_shares(chains, ft_per_pt, widths_ft=None, band_ft=(0.15, max_ft),
                       step_ft=1.0, max_samples=20000)
    return wide.get("spacing_ft") or {}


def family_twins(chains: list, ft_per_pt: float, schedule_widths=None) -> dict:
    """The whole decision for one family: probe where its partners sit, pick
    the rule (a wall thickness, or the schedule's widths for a footing
    family), pair, and say which rule ran."""
    hist = probe_spacings(chains, ft_per_pt)
    kind = family_kind(hist) if hist else "wall"
    widths = sorted({float(w) for w in (schedule_widths or []) if w and float(w) >= FOOTING_BAND_FT[0]})
    if kind == "footing":
        res = twin_shares(chains, ft_per_pt, widths_ft=widths or None,
                          band_ft=FOOTING_BAND_FT)
        if not widths:
            res["rule"] = f"footing edges {FOOTING_BAND_FT[0]:g}-{FOOTING_BAND_FT[1]:g} ft (no schedule read)"
    else:
        res = twin_shares(chains, ft_per_pt)
    res["kind"] = kind
    res["probe"] = hist
    return res
