"""The CORE detector's lines as this engine's reading.

CORE (`takeoff_engine` in the product) chains the sheet's strokes by DASH PATTERN: it knows a
`d18.0 g9.0` line's period, so a symbol mask two periods long is a gap of the pattern, not a break.
On subdivision B its gray dashed existing contours come out as 81 chains of median 419 pt against
this engine's 606 of median 45 pt (2026-09-25, `out/verify/2026-09-25/core-vs-engine/`). This module
turns a CORE page result (`p<N>_result.json` in a job, or `doc0_p<N>.json` from its CLI) into the
Chain / StyleGroup lists `read_sheet` would have built, so everything downstream - labels, frames,
gaps, counting, the operator's records - runs unchanged on CORE's lines. The cut / fill read is then
the SECOND pass over one detection, which is what the product wants: one line detection feeding
every tool.

What is not carried over: CORE's `class == "annotation"` chains (leaders, dimension lines, text
underlines) and the dash signatures it names as furniture (`leader`, `legend swatch`, `grid`,
`sheet furniture`). CORE's `length_pt` includes the gaps of a dashed line; `ink_pt` is the drawn
part, and this engine keeps both the same way.
"""
from __future__ import annotations

import numpy as np

from .chains import Chain, StyleGroup
from .geom import poly_length, turning_stats

SKIP_SIGNATURES = {"leader", "legend swatch", "grid", "sheet furniture"}


def _style_key(c: dict) -> tuple:
    r, g, b = (c.get("color") or [0, 0, 0])[:3]
    return (round(r * 20) / 20, round(g * 20) / 20, round(b * 20) / 20, round(float(c.get("width") or 0.0), 1),
            c.get("layer") or "", str(c.get("class", "")).startswith("dash"))


def chains_from_core(core: dict, min_candidate_len: float = 40.0) -> tuple[list[Chain], list[StyleGroup]]:
    """CORE's page result -> (chains, styles) in this engine's shape."""
    rows = [c for c in (core.get("chains") or [])
            if c.get("class") != "annotation" and c.get("dash_signature") not in SKIP_SIGNATURES
            and len(c.get("points") or []) >= 2]
    groups: dict[tuple, list[dict]] = {}
    for c in rows:
        groups.setdefault(_style_key(c), []).append(c)
    chains: list[Chain] = []
    styles: list[StyleGroup] = []
    for gid, (key, members) in enumerate(sorted(groups.items(), key=lambda kv: -len(kv[1]))):
        r, g, b, w, layer, dashed = key
        lens = np.array([float(m.get("length_pt") or 0.0) for m in members])
        dash_lens = [t[0] for m in members for t in [m.get("template") or []] if t]
        sg = StyleGroup(id=gid, color=(r, g, b), gray=0.299 * r + 0.587 * g + 0.114 * b, width=w, layer=layer,
                        dashed=bool(dashed), n_strokes=int(sum(max(1, int(m.get("n_dashes") or 1)) for m in members)),
                        median_stroke=float(np.median(dash_lens)) if dash_lens else (float(np.median(lens)) if len(lens) else 0.0),
                        link_radius=1.5, total_length=float(lens.sum()))
        for m in members:
            pts = np.asarray(m["points"], dtype=float)
            if len(pts) < 2:
                continue
            closed = bool(len(pts) > 3 and float(np.hypot(*(pts[0] - pts[-1]))) < 1.0)
            c = Chain(id=len(chains), style=gid, pts=pts, members=[], closed=closed)
            c.length = poly_length(c.pts)
            c.ink = float(m.get("ink_pt") or 0.0) or c.length
            c.max_turn, c.mean_turn, c.n_sharp = turning_stats(c.pts, 4.0)
            n_samp = max(1, int(c.length / 4.0))
            sharp_cap = max(2, int(0.03 * n_samp)) if (c.closed or c.length < 300) else max(2, int(0.15 * n_samp))
            c.candidate = (c.length >= min_candidate_len and c.n_sharp <= sharp_cap and c.mean_turn <= 25.0)
            if c.candidate:
                sg.n_candidates += 1
                sg.cand_length += c.length
            chains.append(c)
        sg.n_chains = len(members)
        styles.append(sg)
    return chains, styles


def core_links(chains: list[Chain], core: dict, tol: float = 1.5, min_cover: float = 0.6) -> dict[int, int]:
    """Which CORE chain each engine chain lies on: {engine chain id: CORE chain_id}. An engine
    piece belongs to a CORE chain when >= 60 % of its samples (every 8 pt) lie within 1.5 pt of it.
    Two engine pieces on one CORE chain are one line as far as CORE's pattern-aware chaining could
    tell (a symbol mask two dash periods long is a gap of the pattern, not a break); the engine
    keeps its own, more complete linework (strip rebuild, small triangles) and takes the joins."""
    from shapely.geometry import LineString, Point
    from shapely.strtree import STRtree
    from .geom import resample
    rows = [c for c in (core.get("chains") or []) if c.get("class") != "annotation"
            and c.get("dash_signature") not in SKIP_SIGNATURES and len(c.get("points") or []) >= 2]
    if not rows:
        return {}
    geoms = [LineString(np.asarray(c["points"], dtype=float)) for c in rows]
    ids = [int(c["chain_id"]) for c in rows]
    tree = STRtree(geoms)
    out: dict[int, int] = {}
    for ch in chains:
        if len(ch.pts) < 2 or ch.length < 8.0:
            continue
        samples = resample(ch.pts, 8.0)
        if len(samples) < 2:
            continue
        hits: dict[int, int] = {}
        for q in samples:
            pt = Point(float(q[0]), float(q[1]))
            for gi in tree.query(pt.buffer(tol)):
                if geoms[gi].distance(pt) <= tol:
                    hits[gi] = hits.get(gi, 0) + 1
        if not hits:
            continue
        gi, n = max(hits.items(), key=lambda kv: kv[1])
        if n >= min_cover * len(samples):
            out[ch.id] = ids[gi]
    return out
