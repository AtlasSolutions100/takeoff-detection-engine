"""Small geometry helpers on numpy polylines (page points, y down)."""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np


# Nothing the operator does changes what a read holds (it reads the whole page: the work area, erased lines and
# roles are applied after it), so the words promise nothing: a Re-read would refuse again.
TOO_BIG = "This sheet is too big to read here: it holds more lines or a bigger page than one read can take."


class SheetTooBig(RuntimeError):
    """A sheet whose reading would need more memory than one read may take here. The read stops
    and says so in plain words; it never returns part of a reading (D41, 2026-10-01). The refusal is
    remembered for the sheet (Project.remember_refusal); Re-read is the operator's "try again"."""

    def __init__(self, what: str = ""):
        # the first line is what the page shows; the detail stays folded under it
        super().__init__(TOO_BIG + (f"\n{what}" if what else ""))


# How many neighbour pairs one step of a pair search may hold in memory at a time. Each pair costs
# about 24 bytes as the search hands it over (two indices and a distance), and a few more passes of
# the same length follow it, so 2 million pairs is roughly 50-150 MB per step.
PAIR_CHUNK = 2_000_000


def near_pairs(points: np.ndarray, r: float, chunk: int | None = None, tree=None):
    """Every pair (i, j), i < j, of `points` no more than `r` apart, handed over a slice at a time as
    two index arrays, so the whole list never sits in memory at once. The SET of pairs is the one
    cKDTree.query_pairs(r) returns; the ORDER is not, so use it only where the order cannot matter
    (a union of groups). Res-A civil C3.0: 211,639 glyph candidates within 16.6 pt make 57.3 million
    pairs, held whole that was 0.9 GB of indices plus several arrays of the same length."""
    from scipy.spatial import cKDTree
    chunk = int(chunk if chunk is not None else PAIR_CHUNK)        # read at call time
    P = np.asarray(points, dtype=float)
    n = len(P)
    if n < 2:
        return
    tree = tree if tree is not None else cKDTree(P)
    cnt = np.asarray(tree.query_ball_point(P, r, return_length=True), dtype=np.int64)   # O(n): counts only
    cum = np.cumsum(cnt)
    start = 0
    while start < n:
        base = int(cum[start - 1]) if start else 0
        end = int(np.searchsorted(cum, base + chunk, side="right"))
        end = min(n, max(end, start + 1))
        sub = cKDTree(P[start:end])
        m = sub.sparse_distance_matrix(tree, r, output_type="ndarray")
        i = m["i"].astype(np.int64) + start
        j = m["j"].astype(np.int64)
        del m
        keep = i < j
        yield i[keep], j[keep]
        start = end


def poly_length(pts: np.ndarray) -> float:
    if len(pts) < 2:
        return 0.0
    d = np.diff(pts, axis=0)
    return float(np.sqrt((d * d).sum(axis=1)).sum())


def cum_length(pts: np.ndarray) -> np.ndarray:
    if len(pts) < 2:
        return np.zeros(len(pts))
    d = np.diff(pts, axis=0)
    seg = np.sqrt((d * d).sum(axis=1))
    return np.concatenate([[0.0], np.cumsum(seg)])


def resample(pts: np.ndarray, step: float) -> np.ndarray:
    """Resample a polyline at roughly `step` spacing (keeps both ends)."""
    pts = np.asarray(pts, dtype=float)
    if len(pts) < 2:
        return pts.copy()
    cl = cum_length(pts)
    total = cl[-1]
    if total <= step:
        return np.vstack([pts[0], pts[-1]])
    n = max(2, int(math.ceil(total / step)) + 1)
    s = np.linspace(0.0, total, n)
    x = np.interp(s, cl, pts[:, 0])
    y = np.interp(s, cl, pts[:, 1])
    return np.column_stack([x, y])


def end_tangent(pts: np.ndarray, at_end: bool, lookback: float = 6.0) -> np.ndarray:
    """Unit vector pointing OUT of the polyline at the given end."""
    pts = np.asarray(pts, dtype=float)
    if len(pts) < 2:
        return np.array([1.0, 0.0])
    if at_end:
        tip = pts[-1]
        i = len(pts) - 2
        while i > 0 and np.linalg.norm(pts[i] - tip) < lookback:
            i -= 1
        v = tip - pts[i]
    else:
        tip = pts[0]
        i = 1
        while i < len(pts) - 1 and np.linalg.norm(pts[i] - tip) < lookback:
            i += 1
        v = tip - pts[i]
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else np.array([1.0, 0.0])


def turning_stats(pts: np.ndarray, step: float = 4.0) -> tuple[float, float, int]:
    """Return (max_turn_deg, mean_abs_turn_deg, n_sharp>45) on a resampled polyline."""
    r = resample(pts, step)
    if len(r) < 3:
        return 0.0, 0.0, 0
    v = np.diff(r, axis=0)
    ang = np.arctan2(v[:, 1], v[:, 0])
    d = np.diff(ang)
    d = (d + np.pi) % (2 * np.pi) - np.pi
    d = np.degrees(np.abs(d))
    return float(d.max()), float(d.mean()), int((d > 45).sum())


def point_seg_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    ab = b - a
    ab2 = float(ab @ ab)
    if ab2 < 1e-12:
        return float(np.linalg.norm(p - a))
    t = float(np.clip(((p - a) @ ab) / ab2, 0.0, 1.0))
    return float(np.linalg.norm(p - (a + t * ab)))


def nearest_on_polyline(p: np.ndarray, pts: np.ndarray) -> tuple[float, np.ndarray, int]:
    """Distance, nearest point and segment index from p to the polyline."""
    pts = np.asarray(pts, dtype=float)
    if len(pts) == 1:
        return float(np.linalg.norm(p - pts[0])), pts[0], 0
    a = pts[:-1]
    b = pts[1:]
    ab = b - a
    ab2 = (ab * ab).sum(axis=1)
    ab2[ab2 < 1e-12] = 1e-12
    t = ((p - a) * ab).sum(axis=1) / ab2
    t = np.clip(t, 0.0, 1.0)
    q = a + t[:, None] * ab
    d = np.sqrt(((q - p) ** 2).sum(axis=1))
    i = int(d.argmin())
    return float(d[i]), q[i], i


def bbox(pts: np.ndarray) -> tuple[float, float, float, float]:
    pts = np.asarray(pts)
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


def similarity_from_pairs(src: Sequence[Sequence[float]], dst: Sequence[Sequence[float]]) -> np.ndarray:
    """Least-squares similarity transform (scale+rotation+translation) mapping src->dst.
    Returns a 3x3 matrix. Needs >= 2 pairs."""
    s = np.asarray(src, dtype=float)
    d = np.asarray(dst, dtype=float)
    if len(s) < 2:
        raise ValueError("need at least two point pairs")
    ms = s.mean(axis=0)
    md = d.mean(axis=0)
    s0 = s - ms
    d0 = d - md
    # complex-number formulation of a 2D similarity fit
    sc = s0[:, 0] + 1j * s0[:, 1]
    dc = d0[:, 0] + 1j * d0[:, 1]
    denom = (sc.conj() * sc).sum()
    if abs(denom) < 1e-12:
        raise ValueError("degenerate source points")
    z = (sc.conj() * dc).sum() / denom
    a, b = z.real, z.imag
    M = np.array([[a, -b, 0.0], [b, a, 0.0], [0.0, 0.0, 1.0]])
    t = md - (M[:2, :2] @ ms)
    M[0, 2], M[1, 2] = t
    return M


def apply_affine(M: np.ndarray, pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=float)
    if pts.ndim == 1:
        v = M @ np.array([pts[0], pts[1], 1.0])
        return v[:2]
    h = np.column_stack([pts, np.ones(len(pts))])
    return (M @ h.T).T[:, :2]


def polygon_area(pts: np.ndarray) -> float:
    pts = np.asarray(pts, dtype=float)
    if len(pts) < 3:
        return 0.0
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
