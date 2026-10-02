"""What the reviewer did to the sheet's lines in the product, applied to this engine's reading.

The product's review screen lets an operator ERASE a line (a flow line drawn in the contour pen, a
lot line), NAME a family ("EXISTING CONTOUR") and draw the WORK AREA. Until 2026-09-27 none of
that reached the cut / fill: the engine read the raw detection, so a line erased in review still
carried a height into the surface. The host hands the decisions over when Cut / fill opens
(server.cutfill_open -> the page setting `core_edits`, made by `normalize`), and they are applied
at interpret time on the cached reading:

* a piece of this engine's linework that lies on an ERASED line is left out - unless the
  operator set something on that piece here (a typed height, a surface switch: the record made
  in this engine is the later, more specific word), and never a piece lying on a line the
  reviewer put in its place (a split or extended line replaces the original he erased);
* a pen whose linework the review names as a surface's contours takes that surface; the
  operator's own choice in the Families card still outranks it;
* the work area is the limit when none is drawn (compute).

Everything is matched by GEOMETRY, never by id: chain ids move with every detection, and this
engine's pieces are not the product's chains.
"""
from __future__ import annotations

import hashlib
import json
import re

import numpy as np

from .geom import poly_length, resample

MAX_LINES = 4000            # erased / added lines carried per sheet
MAX_PTS = 400               # points kept per carried line
MAX_ZONES = 40

# A label names a surface's contours when it carries a surface word AND exactly one side's word.
# "EXISTING WATER LINE" names no surface; a label with both sides' words names neither; a CAD
# layer name (CE-TOPO-MINR) is left to guess_role, which knows the prefixes.
_SURF = re.compile(r"\b(CONTOURS?|CONT\.?|TOPO\w*|GROUND|GRADES?|INDEX|INTERMEDIATE)\b")
_EXIST = re.compile(r"\b(EXIST\w*|EX\.?|EXG\.?|ORIGINAL|NATURAL|PRE-?DEVELOP\w*)\b")
_PROP = re.compile(r"\b(PROP\w*|FINISH\w*|FIN\.?|DESIGN|NEW|FUTURE|POST-?DEVELOP\w*)\b")
_NOT_SURF = re.compile(r"\b(BREAK|LIMITS?|PIPE|WATER|SEWER|STORM|GAS|FENCE|CURB|WALL|EASEMENT|SETBACK|"
                       r"PAVEMENT|PAVING|SIDEWALK|BUILDING|UTILIT\w*|ELECTRIC\w*|FIBER|TELE\w*)\b")


def role_of_label(text) -> str | None:
    """existing | proposed when the label names that surface's contours, else None."""
    t = str(text or "").upper()
    if not _SURF.search(t) or _NOT_SURF.search(t):
        return None
    e, p = bool(_EXIST.search(t)), bool(_PROP.search(t))
    if e == p:
        return None
    return "existing" if e else "proposed"


def _line(pts) -> list | None:
    try:
        P = np.asarray(pts, dtype=float)
    except (TypeError, ValueError):
        return None
    if P.ndim != 2 or P.shape[1] < 2 or len(P) < 2 or not np.isfinite(P[:, :2]).all():
        return None
    P = P[:, :2]
    if len(P) > 8:
        # the line's shape within 0.4 pt (the matching tolerance is 1.5 pt): a dashed chain carries
        # a vertex per dash end, a sheet's contours are megabytes of them
        from shapely.geometry import LineString
        try:
            P = np.asarray(LineString(P).simplify(0.4, preserve_topology=False).coords, dtype=float)
        except Exception:       # noqa: BLE001 - a degenerate line keeps its own points
            pass
    if len(P) > MAX_PTS:                       # thin a very dense line evenly, both ends kept
        idx = np.unique(np.linspace(0, len(P) - 1, MAX_PTS).round().astype(int))
        P = P[idx]
    if len(P) < 2:
        return None
    return [[round(float(x), 1), round(float(y), 1)] for x, y in P]


def normalize(core: dict | None, body: dict | None) -> dict | None:
    """The review's decisions as this engine keeps them. `core` is the product's page result
    (the erased chains' geometry is taken from it, by the ids the review screen holds NOW);
    `body` = {excluded_chain_ids, added_chains: [{points}], labels: {style id: label},
    chain_labels: {chain id: label}, work_zones: [[x0, y0, x1, y1]]}. None when nothing was done."""
    if not isinstance(body, dict):
        return None
    chains = {}
    for c in (core or {}).get("chains") or []:
        try:
            chains[int(c.get("chain_id"))] = c
        except (TypeError, ValueError):
            continue
    erased, added = [], []
    for cid in (body.get("excluded_chain_ids") or [])[:MAX_LINES * 4]:
        try:
            c = chains.get(int(cid))
        except (TypeError, ValueError):
            continue
        ln = _line((c or {}).get("points"))
        if ln and len(erased) < MAX_LINES:
            erased.append(ln)
    for c in (body.get("added_chains") or [])[:MAX_LINES]:
        ln = _line((c or {}).get("points") if isinstance(c, dict) else None)
        if ln:
            added.append(ln)
    # only the names that say a surface travel: the rest of the label book is not this engine's
    named = []
    lab = body.get("labels") if isinstance(body.get("labels"), dict) else {}
    clab = body.get("chain_labels") if isinstance(body.get("chain_labels"), dict) else {}
    own = {}
    for k, v in clab.items():
        try:
            own[int(k)] = str(v or "")
        except (TypeError, ValueError):
            continue
    by_style = {}
    for k, v in lab.items():
        try:
            by_style[int(k)] = str(v or "")
        except (TypeError, ValueError):
            continue
    gone = set()
    for cid in body.get("excluded_chain_ids") or []:
        try:
            gone.add(int(cid))
        except (TypeError, ValueError):
            continue
    for cid, c in chains.items():
        if cid in gone:
            continue
        text = own.get(cid)
        if text is None:
            try:
                text = by_style.get(int(c.get("style_id")))
            except (TypeError, ValueError):
                text = None
        role = role_of_label(text)
        if not role:
            continue
        ln = _line(c.get("points"))
        if ln and len(named) < MAX_LINES:
            named.append({"role": role, "label": str(text)[:80], "pts": ln})
    zones = []
    for z in (body.get("work_zones") or [])[:MAX_ZONES]:
        try:
            x0, y0, x1, y1 = (float(v) for v in z[:4])
        except (TypeError, ValueError):
            continue
        if np.isfinite([x0, y0, x1, y1]).all() and abs(x1 - x0) > 1 and abs(y1 - y0) > 1:
            zones.append([round(min(x0, x1), 1), round(min(y0, y1), 1), round(max(x0, x1), 1), round(max(y0, y1), 1)])
    if not (erased or named or zones):
        return None
    out = {"erased": erased, "added": added, "named": named, "work_zones": zones}
    out["sig"] = hashlib.sha1(json.dumps(out, sort_keys=True).encode()).hexdigest()[:12]
    return out


def sig_of(edits: dict | None) -> str:
    return str((edits or {}).get("sig") or "")


def summary_of(edits: dict | None) -> dict | None:
    """What the page settings keep (the lines themselves live in a file beside the analysis):
    the signature, the counts and the work area."""
    if not edits:
        return None
    return {"sig": sig_of(edits), "erased": len(edits.get("erased") or []), "added": len(edits.get("added") or []),
            "named": len(edits.get("named") or []), "work_zones": edits.get("work_zones") or []}


def _tree(lines: list):
    from shapely.geometry import LineString
    from shapely.strtree import STRtree
    geoms = [LineString(ln) for ln in lines if ln and len(ln) >= 2]
    return (geoms, STRtree(geoms)) if geoms else ([], None)


def _cover(samples: np.ndarray, geoms: list, tree, tol: float) -> float:
    """The share of the samples lying within `tol` of any of the lines."""
    if tree is None or not len(samples):
        return 0.0
    from shapely.geometry import Point
    n = 0
    for q in samples:
        pt = Point(float(q[0]), float(q[1]))
        for gi in tree.query(pt.buffer(tol)):
            if geoms[int(gi)].distance(pt) <= tol:
                n += 1
                break
    return n / float(len(samples))


def erased_pieces(chains: list, edits: dict | None, keep: set | None = None, tol: float = 1.5,
                  min_cover: float = 0.6) -> tuple[set, float, int]:
    """(ids of this engine's pieces lying on a line erased in review, their length in pt, how many
    more lie on an erased line but are kept because the operator set something on them here).
    A piece lies on a line when >= 60 % of its samples (every 8 pt) are within 1.5 pt of it - the
    test the joins use (core_reading.core_links). A piece that lies on a line the reviewer ADDED
    (the two halves of a split, an extended line) stands: the original was erased to make room."""
    er, er_tree = _tree((edits or {}).get("erased") or [])
    if er_tree is None:
        return set(), 0.0, 0
    ad, ad_tree = _tree((edits or {}).get("added") or [])
    keep = keep or set()
    out: set = set()
    length = 0.0
    held = 0
    for ch in chains:
        if len(ch.pts) < 2 or ch.length < 8.0:
            continue
        s = resample(ch.pts, 8.0)
        if len(s) < 2:
            continue
        if _cover(s, er, er_tree, tol) < min_cover:
            continue
        if ad_tree is not None and _cover(s, ad, ad_tree, tol) >= min_cover:
            continue
        if ch.id in keep:
            held += 1
            continue
        out.add(ch.id)
        length += float(ch.length)
    return out, length, held


def roles_from_review(chains: list, styles: list, edits: dict | None, tol: float = 1.5, min_cover: float = 0.6,
                      min_length: float = 300.0, min_share: float = 0.5, max_other: float = 0.2) -> dict:
    """{this engine's style id: (role, reason)} for the pens whose linework the review names as a
    surface's contours. A pen takes the name when the named lines cover at least half of its
    candidate linework and 300 pt of it, and the other surface's name covers under a fifth of
    that: a pen the review names both ways is left to the engine's own reading, and said."""
    named = (edits or {}).get("named") or []
    if not named:
        return {}
    trees = {}
    labels = {}
    for role in ("existing", "proposed"):
        rows = [n for n in named if n.get("role") == role]
        trees[role] = _tree([n["pts"] for n in rows])
        for n in rows:
            labels.setdefault(role, {})
            labels[role][n.get("label") or ""] = labels[role].get(n.get("label") or "", 0.0) + poly_length(np.asarray(n["pts"], dtype=float))
    votes: dict = {}
    total: dict = {}            # the pen's candidate linework among the pieces in play (erased ones are not)
    for ch in chains:
        if not ch.candidate or len(ch.pts) < 2 or ch.length < 40.0:
            continue
        total[ch.style] = total.get(ch.style, 0.0) + float(ch.length)
        s = resample(ch.pts, 8.0)
        if len(s) < 2:
            continue
        for role, (geoms, tree) in trees.items():
            if tree is not None and _cover(s, geoms, tree, tol) >= min_cover:
                v = votes.setdefault(ch.style, {"existing": 0.0, "proposed": 0.0})
                v[role] += float(ch.length)
    out = {}
    for st in styles:
        v = votes.get(st.id)
        if not v:
            continue
        role = max(v, key=lambda r: v[r])
        other = v["proposed" if role == "existing" else "existing"]
        if v[role] < min_length or v[role] < min_share * max(total.get(st.id, 0.0), 1.0):
            continue
        if other > max_other * v[role]:
            out[st.id] = (None, f"named both ways in review ({v['existing']:.0f} pt existing, {v['proposed']:.0f} pt proposed)")
            continue
        name = max(labels.get(role, {"": 0.0}).items(), key=lambda kv: kv[1])[0]
        out[st.id] = (role, f"named in review: “{name}”" if name else "named in review")
    return out


def work_area(edits: dict | None):
    """The review's work area as one shapely geometry (the union of its boxes), or None."""
    zones = (edits or {}).get("work_zones") or []
    if not zones:
        return None
    import shapely
    from shapely.geometry import box
    g = shapely.union_all([box(*z) for z in zones])
    return None if g.is_empty else g
