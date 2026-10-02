"""Surfaces = triangulated evidence points, and the volume grid between two of them.

Each evidence point carries a source id so any grid cell can name the printed
things its number came from (three vertices of the existing triangle, three
of the proposed one, and behind each vertex the label / rule it came from).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import Delaunay
from shapely.geometry import Polygon

from .geom import resample


@dataclass
class Evidence:
    """One point with a height and a story."""
    id: int
    x: float
    y: float
    z: float
    kind: str                  # contour | spot | wall | derived | typed
    page: str                  # page key "d0p3"
    ref: dict = field(default_factory=dict)   # {"contour": id} | {"spot": id, "label": id} | {"rule": ...}
    surface: str = ""          # existing | proposed

    def to_json(self) -> dict:
        return {"id": self.id, "x": round(self.x, 1), "y": round(self.y, 1), "z": round(self.z, 3), "kind": self.kind,
                "page": self.page, "ref": self.ref, "surface": self.surface}


class Surface:
    def __init__(self, name: str, points: list[Evidence]):
        self.name = name
        self.points = points
        self.tri = None
        self.interp = None
        self.hull: Polygon | None = None
        if len(points) >= 3:
            xy = np.array([[p.x, p.y] for p in points])
            z = np.array([p.z for p in points])
            # drop exact duplicates (Qhull dislikes them)
            key = np.round(xy, 2)
            _, keep = np.unique(key, axis=0, return_index=True)
            keep = np.sort(keep)
            self.xy = xy[keep]
            self.z = z[keep]
            self.ids = np.array([points[i].id for i in keep])
            try:
                self.tri = Delaunay(self.xy, qhull_options="QJ")
                self.interp = LinearNDInterpolator(self.tri, self.z)
                hull_pts = self.xy[self.tri.convex_hull.ravel()]
                self.hull = shapely.convex_hull(shapely.multipoints(self.xy))
            except Exception:
                self.tri = None

    @property
    def ok(self) -> bool:
        return self.tri is not None

    def sample(self, X: np.ndarray, Y: np.ndarray) -> np.ndarray:
        if not self.ok:
            return np.full(X.shape, np.nan)
        return self.interp(np.column_stack([X.ravel(), Y.ravel()])).reshape(X.shape)

    def simplices(self, X: np.ndarray, Y: np.ndarray) -> np.ndarray:
        """Evidence ids (n,3) of the triangle under each query point (-1 outside)."""
        if not self.ok:
            return -np.ones((X.size, 3), dtype=int)
        s = self.tri.find_simplex(np.column_stack([X.ravel(), Y.ravel()]))
        out = -np.ones((X.size, 3), dtype=int)
        ok = s >= 0
        out[ok] = self.ids[self.tri.simplices[s[ok]]]
        return out

    def z_at(self, x: float, y: float) -> float:
        if not self.ok:
            return float("nan")
        return float(self.interp([[x, y]])[0])


def contour_points(pts: np.ndarray, z: float, step: float = 6.0) -> np.ndarray:
    r = resample(pts, step)
    return np.column_stack([r, np.full(len(r), z)])


@dataclass
class GridResult:
    cell_pt: float
    ft_per_pt: float
    x0: float
    y0: float
    nx: int
    ny: int
    eg: np.ndarray             # (ny,nx) existing z or nan
    fg: np.ndarray             # the surface the volumes are taken to: finished grade less the section
    inside: np.ndarray         # bool (ny,nx)
    cut_cy: float
    fill_cy: float
    area_sf: float
    coverage: float            # inside cells with both surfaces / inside cells
    n_inside: int
    n_both: int
    repaired: bool = False     # the limit polygon was invalid (self-touching) and was repaired
    unclipped: int = 0         # boundary cells whose clip failed and kept the cell-centre rule
    section_ft: float = 0.0    # what fg was lowered by (0 = fg is finished grade)
    weight: np.ndarray | None = None   # the part of each cell inside the limit (0..1)
    # grading zones computed on THIS grid (cut_fill_grid(parts=...)): one dict per zone, in order,
    # and the rest of the limit; zone_idx = the zone holding most of each cell (-1 = the rest),
    # sec_cell = the section that cell's fg was lowered by
    parts: list | None = None
    rest: dict | None = None
    zone_idx: np.ndarray | None = None
    sec_cell: np.ndarray | None = None
    cutc: np.ndarray | None = None       # each cell's own cut / fill (area-weighted ft, every part summed)
    fillc: np.ndarray | None = None


def area_weights(geom, X: np.ndarray, Y: np.ndarray, cell_pt: float) -> tuple[np.ndarray, int]:
    """The part of each cell (centres X, Y, side cell_pt) inside `geom`, 0..1: the centre test, then
    every cell near the boundary clipped exactly (the rule cut_fill_grid applies to the limit).
    Returns (weights, cells whose clip failed and kept the centre rule)."""
    inside = shapely.contains_xy(geom, X.ravel(), Y.ravel()).reshape(X.shape)
    weight = inside.astype(float)
    ring = geom.boundary.buffer(cell_pt * 0.75)
    edge = shapely.intersects_xy(ring, X.ravel(), Y.ravel()).reshape(X.shape)
    half = cell_pt / 2.0
    unclipped = 0
    for j, i in zip(*np.nonzero(edge)):
        cell = shapely.box(X[j, i] - half, Y[j, i] - half, X[j, i] + half, Y[j, i] + half)
        try:
            weight[j, i] = cell.intersection(geom).area / (cell_pt * cell_pt)
        except Exception:
            unclipped += 1
    return weight, unclipped


def polygonal(g):
    """The area part of a geometry (None when there is none): a clip of two polygons that touch along
    a line or at a corner is a collection of an area and a line or a point, and only the area counts
    (a zone with a corner on the limit's line used to stop the whole compute)."""
    if g is None or g.is_empty:
        return None
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    polys = [q for q in shapely.get_parts(g) if q.geom_type in ("Polygon", "MultiPolygon") and q.area > 0]
    return shapely.union_all(polys) if polys else None


def _zoned(existing: Surface, proposed: Surface, X, Y, eg, weight, clip_region, cell_pt: float, cell_ft: float,
           section_ft: float, zones: list, order: list | None = None) -> dict:
    """Grading zones on the limit's own grid: each zone is dug to its own section, the rest of the
    limit to `section_ft`. A cell split between zones counts in each by the part of it inside.
    Zones are made disjoint in `order` (the caller passes the smallest first, so a pad inside a lot
    and a sliver two zones share count in the smaller zone), and no cell ever holds more zone than
    it holds limit, so the zones and the rest add up to the limit exactly. One pass, one zone's
    weights at a time (ten zones on a big job used to hold ten weight grids at once). Returns the
    totals, every part's numbers in the zones' own order, the zone holding most of each cell, each
    cell's section and each cell's own cut and fill (area-weighted feet, every part summed)."""
    pg = proposed.sample(X, Y)
    finite = np.isfinite(eg) & np.isfinite(pg)
    cell_area_sf = cell_ft * cell_ft
    n = len(zones)
    order = list(order) if order is not None else list(range(n))
    zsum = np.zeros_like(weight)
    best = np.zeros_like(weight)
    dom = np.full(weight.shape, -1, dtype=np.int32)             # -1 = the rest of the limit
    cutc = np.zeros_like(weight)
    fillc = np.zeros_like(weight)
    secs = [float(z["section_ft"]) for z in zones]
    parts: list = [None] * n
    taken = None
    unclipped = 0

    def tally(w, d):
        dz = np.where(finite & (w > 0), (pg - d - eg) * w, 0.0)
        np.add(cutc, np.where(dz < 0, -dz, 0.0), out=cutc)
        np.add(fillc, np.where(dz > 0, dz, 0.0), out=fillc)
        tot = float(w.sum())
        return {"cut_cy": float(-dz[dz < 0].sum() * cell_area_sf / 27.0), "fill_cy": float(dz[dz > 0].sum() * cell_area_sf / 27.0),
                "area_sf": tot * cell_area_sf, "coverage": (float(w[finite].sum()) / tot) if tot > 0 else 0.0,
                "cells": int((finite & (w > 0)).sum()), "section_ft": d}

    for i in order:
        g = zones[i]["geom"]
        try:
            gi = polygonal(g.intersection(clip_region))
            if gi is not None and taken is not None:
                gi = polygonal(gi.difference(taken))
        except Exception:
            gi = polygonal(shapely.make_valid(g).intersection(clip_region))
            if gi is not None and taken is not None:
                gi = polygonal(gi.difference(taken))
        if gi is None or gi.area <= 0:
            parts[i] = {"cut_cy": 0.0, "fill_cy": 0.0, "area_sf": 0.0, "coverage": 0.0, "cells": 0, "section_ft": secs[i]}
            continue
        w, u = area_weights(gi, X, Y, cell_pt)
        unclipped += u
        w = np.minimum(w, np.clip(weight - zsum, 0.0, None))     # never more zone in a cell than limit left in it
        np.add(zsum, w, out=zsum)
        parts[i] = tally(w, secs[i])
        more = w > best
        best[more] = w[more]
        dom[more] = i
        taken = gi if taken is None else shapely.union_all([taken, gi])
    w_rest = np.clip(weight - zsum, 0.0, None)
    w_rest[w_rest < 1e-6] = 0.0
    rest = tally(w_rest, section_ft)
    dom[w_rest > best] = -1
    sec_cell = np.where(dom >= 0, np.array(secs + [section_ft], dtype=float)[dom], section_ft)
    return {"cut_cy": sum(p["cut_cy"] for p in parts) + rest["cut_cy"], "fill_cy": sum(p["fill_cy"] for p in parts) + rest["fill_cy"],
            "rest": rest, "parts": parts, "zone_idx": dom, "sec_cell": sec_cell, "fg": pg - sec_cell, "unclipped": unclipped,
            "cutc": cutc, "fillc": fillc}


def cut_fill_grid(existing: Surface, proposed: Surface, region: Polygon, cell_ft: float, ft_per_pt: float,
                  section_ft: float = 0.0, zones: list | None = None, zone_order: list | None = None) -> GridResult:
    """`section_ft` lowers the proposed surface inside the limit: the engineer's dirt models compare
    the existing ground with the SUBGRADE under streets (finished grade less the pavement section,
    0.9-2.25 ft on one client's jobs), so a street region is only comparable with its section.
    `zones` = the grading zones, [{"geom", "section_ft"}]: each is dug to its own section and the
    rest of the limit to `section_ft`, on this one grid, so the zones and the rest add up to the
    totals (GridResult.parts / .rest / .zone_idx / .sec_cell). Without zones nothing changes."""
    cell_pt = cell_ft / ft_per_pt
    minx, miny, maxx, maxy = region.bounds
    nx = max(1, int(math.ceil((maxx - minx) / cell_pt)))
    ny = max(1, int(math.ceil((maxy - miny) / cell_pt)))
    xs = minx + (np.arange(nx) + 0.5) * cell_pt
    ys = miny + (np.arange(ny) + 0.5) * cell_pt
    X, Y = np.meshgrid(xs, ys)
    inside = shapely.contains_xy(region, X.ravel(), Y.ravel()).reshape(X.shape)
    # Boundary cells count by the fraction of their area inside the region, so the area equals the
    # polygon's and the volume no longer moves with where the grid happens to start (two integrators
    # with the cell-centre rule disagreed by 3.5 % of the cells on the same 141,583 SF island).
    # An operator's hand-drawn limit can touch or cross itself (a 6-corner subdivision C limit: GEOS
    # "side location conflict"); contains_xy tolerates that, intersection raises. The limit is
    # repaired first (the polygonal part of make_valid, so inside and the clips agree), and a cell
    # whose clip still fails keeps the cell-centre rule; the result counts both so compute can say so.
    clip_region = region
    repaired = False
    if not region.is_valid:
        fixed = shapely.make_valid(region)
        polys = [g for g in getattr(fixed, "geoms", [fixed]) if g.geom_type in ("Polygon", "MultiPolygon") and g.area > 0]
        clip_region = shapely.union_all(polys) if polys else region.buffer(0)
        inside = shapely.contains_xy(clip_region, X.ravel(), Y.ravel()).reshape(X.shape)
        repaired = True
    weight = inside.astype(float)
    ring = clip_region.boundary.buffer(cell_pt * 0.75)
    edge = shapely.intersects_xy(ring, X.ravel(), Y.ravel()).reshape(X.shape)
    half = cell_pt / 2.0
    unclipped = 0
    for j, i in zip(*np.nonzero(edge)):
        cell = shapely.box(X[j, i] - half, Y[j, i] - half, X[j, i] + half, Y[j, i] + half)
        try:
            weight[j, i] = cell.intersection(clip_region).area / (cell_pt * cell_pt)
        except Exception:
            unclipped += 1
    inside = weight > 0
    eg = existing.sample(X, Y)
    sec = float(section_ft or 0.0)
    if zones:
        z = _zoned(existing, proposed, X, Y, eg, weight, clip_region, cell_pt, cell_ft, sec, zones, order=zone_order)
        both = inside & np.isfinite(eg) & np.isfinite(z["fg"])
        n_inside = int(inside.sum())
        return GridResult(cell_pt=cell_pt, ft_per_pt=ft_per_pt, x0=minx, y0=miny, nx=nx, ny=ny, eg=eg, fg=z["fg"],
                          inside=inside, cut_cy=z["cut_cy"], fill_cy=z["fill_cy"], area_sf=float(weight.sum() * cell_ft * cell_ft),
                          coverage=(float(weight[both].sum() / weight.sum()) if n_inside else 0.0), n_inside=n_inside,
                          n_both=int(both.sum()), repaired=repaired, unclipped=unclipped + z["unclipped"], section_ft=sec,
                          weight=weight, parts=z["parts"], rest=z["rest"], zone_idx=z["zone_idx"], sec_cell=z["sec_cell"],
                          cutc=z["cutc"], fillc=z["fillc"])
    # fg is the surface the volumes are taken to (subgrade under a section). Everything read off
    # the grid - the hotspots, the max depths, the crew map, the cell CSV, the 3-D view - reads
    # THIS, so a block's printed depth and the totals say the same thing (the section used to be
    # taken off inside the volume sum only: a map drawn under a 9 in stack printed depths 0.75 ft
    # away from the totals in its own header)
    fg = proposed.sample(X, Y) - sec
    both = inside & np.isfinite(eg) & np.isfinite(fg)
    dz = np.where(both, (fg - eg) * weight, 0.0)   # + fill, - cut  (feet, area-weighted)
    cell_area_sf = cell_ft * cell_ft
    fill_cy = float(dz[dz > 0].sum() * cell_area_sf / 27.0)
    cut_cy = float(-dz[dz < 0].sum() * cell_area_sf / 27.0)
    n_inside = int(inside.sum())
    n_both = int(both.sum())
    area_sf = float(weight.sum() * cell_area_sf)
    return GridResult(cell_pt=cell_pt, ft_per_pt=ft_per_pt, x0=minx, y0=miny, nx=nx, ny=ny, eg=eg, fg=fg,
                      inside=inside, cut_cy=cut_cy, fill_cy=fill_cy, area_sf=area_sf,
                      coverage=(float(weight[both].sum() / weight.sum()) if n_inside else 0.0), n_inside=n_inside, n_both=n_both,
                      repaired=repaired, unclipped=unclipped, section_ft=sec, weight=weight)
