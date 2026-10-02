"""Give heights to contour chains and positions to spot grades.

Contour heights come from three places, and every result remembers which:
  1. a label read in the line's own gap (gap OCR) or sitting on the line
  2. neighbour counting: an unlabelled contour between two labelled ones is
     k intervals away from each of them (perpendicular rays, several samples,
     majority vote, consistency required)
  3. the operator typing a height in the UI

Spot grades are placed at their marker (dot / leader end) when one is drawn,
else at the text centre.
"""
from __future__ import annotations

import math
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon
from shapely.strtree import STRtree

from .chains import Chain, Gap, StyleGroup
from .geom import nearest_on_polyline, resample
from .ingest import Stroke
from .labels import Label

INTERVALS = (0.2, 0.25, 0.5, 1.0, 2.0, 2.5, 4.0, 5.0, 10.0, 20.0)
# CUTFILL_DEBUG_LABEL=30,333 prints what happens to those label ids on the way to a line (stderr)
_DBG_LABELS = {int(x) for x in os.environ.get("CUTFILL_DEBUG_LABEL", "").split(",") if x.strip().isdigit()}
_DBG_CHAINS = {int(x) for x in os.environ.get("CUTFILL_DEBUG_CHAIN", "").split(",") if x.strip().isdigit()}
_DBG_CROSSERS = bool(os.environ.get("CUTFILL_DEBUG_CROSSERS"))  # prints every piece set aside for crossing four lines
_DBG_LEGEND = bool(os.environ.get("CUTFILL_DEBUG_LEGEND"))      # prints which pen each legend row names
_DBG_JOINS = bool(os.environ.get("CUTFILL_DEBUG_JOINS"))        # prints every cross-layer / short-gap join and refusal
# a CAD layer the drafter named as topography (CE-TOPO-MINR, CP-TOPO-MAJR-LOTS, -SURFACE, EXISTING CNTR)
_TOPO_NAMED = re.compile(r"TOPO|CONT|CNTR|SURFACE", re.I)
# ... and as its index contours or the lines between them (CE-TOPO-MAJR / -MINR, CONT-MAJOR, INDEX, INTER)
_LAYER_INDEX = re.compile(r"MAJR|MAJOR|INDEX|INDX", re.I)
_LAYER_BETWEEN = re.compile(r"MINR|MINOR|INTER", re.I)


def layer_rank(layer) -> str | None:
    """"index" / "between" when the layer's own name says which contours it draws, else None."""
    name = str(layer or "").split("|")[-1]
    a, b = bool(_LAYER_INDEX.search(name)), bool(_LAYER_BETWEEN.search(name))
    return "index" if a and not b else "between" if b and not a else None
OPERATOR_HOWS = ("typed", "drawn", "edited")     # heights the operator set: used as given, never dropped
USER_STYLES = {"existing": -1, "proposed": -2}   # synthetic line styles for drawn / edited lines
SWITCH_STYLES = {"existing": -3, "proposed": -4}  # detected lines the operator switched to the other surface


@dataclass
class Family:
    style: int                  # primary style id (the one with the most contour length)
    role: str                   # existing | proposed | ignore
    role_reason: str
    styles: list = field(default_factory=list)   # every style in this family (index + intermediate widths)
    forced: bool = False        # role came from the legend or the operator: never auto-demoted
    interval: float | None = None
    interval_votes: dict = field(default_factory=dict)
    n_contours: int = 0
    n_with_z: int = 0
    n_labels: int = 0
    length_with_z: float = 0.0
    length_total: float = 0.0
    color: tuple = (0, 0, 0)
    gray: float = 0.0
    width: float = 0.0
    dashed: bool = False
    layer: str = ""

    @property
    def sig(self) -> str:
        from .chains import style_sig
        return style_sig(self.color, self.width, self.dashed, self.layer)

    def to_json(self) -> dict:
        return {"style": self.style, "sig": self.sig, "styles": self.styles or [self.style], "role": self.role,
                "role_reason": self.role_reason, "forced": self.forced, "interval": self.interval,
                "interval_votes": {str(k): v for k, v in self.interval_votes.items()},
                "n_contours": self.n_contours, "n_with_z": self.n_with_z, "n_labels": self.n_labels,
                "length_with_z": round(self.length_with_z, 1), "length_total": round(self.length_total, 1),
                "color": [round(c, 3) for c in self.color], "gray": round(self.gray, 2), "width": self.width,
                "dashed": self.dashed, "layer": self.layer}


@dataclass
class Contour:
    id: int
    style: int
    chain_ids: list
    z: float | None = None
    how: str = ""               # label | counted | typed | none
    label_ids: list = field(default_factory=list)
    basis: list = field(default_factory=list)   # [{"contour": id, "label": id, "k": n, "dir": +1/-1}] for counted
    candidates: list = field(default_factory=list)  # competing heights with votes when undecided
    length: float = 0.0
    votes: int = 0
    printed: float | None = None    # the value as printed when a datum shifted z
    z_note: str = ""
    typed_id: str | None = None     # the operator's typed-height record that gave this line its height
    user: str | None = None         # id of the operator's drawn / edited line this contour stands for
    switched: str | None = None     # surface the operator switched this line to (existing | proposed)
    kept: str | None = None         # id of the operator's record this line is drawn from (the reading lost it)

    def to_json(self) -> dict:
        return {"id": self.id, "style": self.style, "chain_ids": self.chain_ids, "z": self.z, "how": self.how,
                "label_ids": self.label_ids, "basis": self.basis, "candidates": self.candidates,
                "length": round(self.length, 1), "votes": self.votes, "printed": self.printed, "z_note": self.z_note,
                "typed_id": self.typed_id, "user": self.user, "switched": self.switched, "kept": self.kept}


@dataclass
class Spot:
    id: int
    label_id: int
    x: float
    y: float
    z: float
    side: str                   # existing | proposed | wall | floor | utility | footing
    tag: str | None
    anchor: str                 # dot | leader | text
    side_reason: str = ""

    def to_json(self) -> dict:
        return {"id": self.id, "label_id": self.label_id, "x": round(self.x, 1), "y": round(self.y, 1), "z": self.z,
                "side": self.side, "tag": self.tag, "anchor": self.anchor, "side_reason": self.side_reason}


# ---------------------------------------------------------------------------
def contour_families(styles: list[StyleGroup], min_candidates: int = 3, min_length: float = 250.0,
                     page_role: str = "grading", forced: dict | None = None) -> list[Family]:
    """Contour families = styles with long smooth chains, merged across widths of
    the same colour (index contours are drawn thicker than intermediate ones but
    belong to the same surface). `forced` = {style_id: (role, reason)} from the
    legend or the operator: those styles are families whatever their shape."""
    forced = forced or {}
    fams: list[Family] = []
    fam_cand: dict[int, int] = {}
    for s in sorted(styles, key=lambda s: -s.cand_length):
        if s.id not in forced and (s.n_candidates < min_candidates or s.cand_length < min_length):
            continue
        merged = None
        for f in fams:
            same_col = abs(f.gray - s.gray) <= 0.15 and all(abs(a - b) <= 0.2 for a, b in zip(f.color, s.color))
            # a dashed pen and a solid pen of one colour are two families when both draw plenty of
            # candidate linework (the hotel site C3: the gray dashed existing contours merged into a
            # darker gray solid family and were called proposed); a small broken-looking style
            # still joins its colour (a thick index contour reads as "dashed" when broken)
            same_dash = f.dashed == s.dashed or s.n_candidates < 10 or fam_cand.get(f.style, 0) < 10
            # two pens the legend put on DIFFERENT surfaces are two families however alike they look
            # (the hotel site C3: black 0.4 named existing and black 0.5 named proposed were merged,
            # and the family took the first name); so are two the operator set apart. A pen the
            # operator set and one the legend named still merge: his choice is the family's, as
            # before (subdivision B: he set the black pens aside, the legend calls one proposed)
            fa, fb = forced.get(f.style), forced.get(s.id)
            if fa and fb and fa[0] != fb[0] and ("you" in fa[1]) == ("you" in fb[1]):
                continue
            if same_col and same_dash and f.layer == s.layer and abs(f.width - s.width) <= 0.35:
                merged = f
                break
        if merged is not None:
            merged.styles.append(s.id)
            merged.length_total += s.cand_length
            continue
        role, why = guess_role(s, page_role)
        fams.append(Family(style=s.id, role=role, role_reason=why, styles=[s.id], color=s.color, gray=s.gray,
                           width=s.width, dashed=s.dashed, layer=s.layer, length_total=s.cand_length))
        fam_cand[s.id] = s.n_candidates
    if page_role != "existing" and any(f.gray >= 0.45 for f in fams):
        for f in fams:
            if f.gray < 0.45 and f.role == "existing" and f.role_reason == "dashed linework":
                f.role = "proposed"
                f.role_reason = "dark linework (the screened family is the existing ground)"
    for f in fams:
        hits = [(sid, forced[sid]) for sid in f.styles if sid in forced]
        if hits:
            # the operator's choice ("set by you") outranks the legend
            hits.sort(key=lambda h: 0 if "you" in h[1][1] else 1)
            f.role, f.role_reason = hits[0][1]
            f.forced = True
    return fams


def leave_out_unlisted_pens(families: list, legend_info: dict) -> list:
    """THE LEGEND, READ WHOLE, IS THE LIST OF THE CONTOUR PENS. When every contour row of the
    legend named a pen (`legend_info` from read_legend_roles: rows == named, two rows at least)
    and both surfaces are among them, a pen the legend does not show is a surface only on the
    drafter's word (a layer named for it) or the operator's: colour and dash alone ("screened
    linework") make no contour pen of what the legend leaves out. The hotel site C3: the paving
    hatch (gray 0.70, 6,582 strokes) was the largest family of the existing ground, with contour
    labels that had landed on it. A legend read in part decides nothing: what was extracted is
    not what is printed. And a pen of the colour and dash of a pen the legend shows stays: a
    legend often shows one line per surface where the plan draws its index contours heavier.
    Returns the families it set aside (their role is "ignore" now)."""
    if not (legend_info.get("rows", 0) >= 2 and legend_info.get("named") == legend_info.get("rows")
            and legend_info.get("roles") == ["existing", "proposed"]):
        return []
    shown = [f for f in families if f.forced and f.role in ("existing", "proposed")]

    def akin(f) -> bool:
        return any(bool(f.dashed) == bool(g.dashed) and abs(f.gray - g.gray) <= 0.15
                   and all(abs(a - b) <= 0.2 for a, b in zip(f.color, g.color)) for g in shown)
    out = [f for f in families if f.role in ("existing", "proposed") and not f.forced
           and not str(f.role_reason).startswith("layer name") and not akin(f)]
    for f in out:
        f.role = "ignore"
        f.role_reason = "not in the sheet's legend, which shows a pen for every contour row"
    return out


def guess_role(s: StyleGroup, page_role: str) -> tuple[str, str]:
    lay = s.layer.upper().split("|")[-1]          # "TB3145104|CP-TOPO-MINR-LOTS": the block name says nothing
    # an annotation layer (spot-grade leaders, text, dimensions) draws no contour, whatever else its
    # name says: subdivision D's C-TOPO-ANNO-SPOT read as "existing" on the word TOPO
    if re.search(r"ANNO|TEXT|DIM\b|LEADER|LABEL|-SPOT", lay):
        return "ignore", f"annotation layer {s.layer!r}"
    # a layer the drafter named for parcels, easements, boundaries or the title block draws no
    # ground (the civil layer names C-PARC, C-ESMT, V-BNDY, C-TBLK): subdivision D's lot lines
    # (C-PARC-LOTS, dark and dashed) held three contour heights that lay across them and were the
    # proposed surface. A layer named for the ground as well is one (CP-TOPO-MAJR-LOTS: the
    # contours ON the lots)
    # (and "PROP-LINE" is the PROPERTY line, not a proposed one: the word PROP alone made it the
    # proposed surface by its layer's name)
    if re.search(r"PARC|ESMT|BNDY|TBLK|PROP[-_ ]?LINE|PROPERTY", lay) and not re.search(r"TOPO|CONT|CNTR|GRAD|SURF", lay):
        return "ignore", f"layer {s.layer!r} is named for parcels, easements or boundaries"
    # civil layer prefixes: CE-/E- = existing, CP-/P- = proposed (one civil firm, NCS-style sets); the word TOPO
    # alone says nothing, both surfaces are topo
    if re.match(r"^(CE|CX|E|EX|EG|EXIST)[-_]", lay) or any(k in lay for k in ("EXIST", "-EX-", "-EX_", "EG-", "-EG", "LIDAR", "SURVEY", "ORIG")):
        return "existing", f"layer name {s.layer!r}"
    if re.match(r"^(CP|P|PR|FG|PG|DG)[-_]", lay) or any(k in lay for k in ("PROP", "-PR-", "FG-", "-FG", "DESIGN", "GRAD", "FIN", "PAD")):
        return "proposed", f"layer name {s.layer!r}"
    if "TOPO" in lay:
        return "existing", f"layer name {s.layer!r}"
    if page_role == "existing":
        return "existing", "existing-conditions sheet"
    if s.gray >= 0.45:
        return "existing", "screened (gray) linework"
    if s.dashed:
        return "existing", "dashed linework"
    return "proposed", "solid dark linework"


def _peel_crossers(eligible: set, cmap: dict, role_of: dict, max_rate: float = 10.0, min_count: int = 2,
                   run_of: dict | None = None) -> set:
    """Pieces that cross other pieces of their own surface more often than a contour can.

    Contours of one surface never cross each other, so within a surface the crossing graph is
    empty apart from lot lines, easements, hatch and fences drawn in the contour pen. Greedy
    peel: the piece with the most crossings per 1000 pt goes first (a lot line crossing three
    contours in 90 pt), its neighbours' counts drop, and the peel stops when no piece crosses at
    more than `max_rate` per 1000 pt with at least `min_count` crossings. A 6,600-pt contour
    crossing 61 lot lines (9 per 1000 pt) keeps its place and ends with no crossings at all.

    `run_of` = {piece: run id} for the dashes of a dashed pen that follow each other end to end:
    a run is judged as one line (its crossings with other lines over its whole length), and
    goes or stays as one. An 18-pt dash crossing two lines is 110 crossings per 1000 pt; the
    2,500-pt dashed contour it belongs to crosses the same two lines."""
    out: set = set()
    run_of = run_of or {}
    by_role: dict = defaultdict(list)
    for cid in eligible:
        r = role_of.get(cmap[cid].style)
        if r in ("existing", "proposed"):
            by_role[r].append(cid)
    for r, ids in by_role.items():
        if len(ids) < 2:
            continue
        geoms = [LineString(cmap[c].pts) for c in ids]
        tree = STRtree(geoms)
        node = [run_of.get(c, ("piece", c)) for c in ids]          # the line a piece is judged with
        members: dict = defaultdict(list)
        for i, k in enumerate(node):
            members[k].append(i)
        nbrs: dict = defaultdict(set)
        for i, g in enumerate(geoms):
            for j in tree.query(g, predicate="crosses"):
                j = int(j)
                if j != i and node[j] != node[i]:
                    nbrs[node[i]].add(node[j])
                    nbrs[node[j]].add(node[i])
        if not nbrs:
            continue
        L = {k: max(sum(cmap[ids[i]].length for i in mem), 1.0) for k, mem in members.items()}
        alive = set(nbrs)
        first_id = {k: min(ids[i] for i in members[k]) for k in nbrs}
        while alive:
            # The line with the highest rate decides, and the peel stops when THAT line is under a
            # threshold - also when it is a stub with one crossing and longer lines over both
            # thresholds remain. That is deliberate, not an oversight: taking the worst of the
            # lines over both thresholds was measured on 17 truth sheets (2026-09-27) and peeled
            # real contours wholesale (subdivision C island, sheet 3.1: fill 23,026 -> 13,107
            # CY against the engineer's 22,148; the hotel site fill 1,309 -> 4,186). What it leaves
            # standing is known: subdivision A's floodplain line crosses six existing contours in
            # 194 pt and stays a contour (open item).
            # Ties go to the lower piece id: the order must not depend on how a set iterates.
            k = max(alive, key=lambda q: (len(nbrs[q] & alive) / L[q], -first_id[q]))
            n = len(nbrs[k] & alive)
            if n < min_count or 1000.0 * n / L[k] <= max_rate:
                break
            for i in members[k]:
                if _DBG_CHAINS and ids[i] in _DBG_CHAINS:
                    print(f"[chain {ids[i]}] peeled: {n} crossings over {L[k]:.0f} pt ({1000.0 * n / L[k]:.1f}/1000)"
                          f"{' as a run of ' + str(len(members[k])) + ' dashes' if len(members[k]) > 1 else ''}", file=sys.stderr)
                out.add(ids[i])
            alive.discard(k)
    return out


def stroke_kind(pts) -> str:
    """How a stroke is drawn: 'line' (two points, or every point on one straight), 'arc' (even
    steps turning one way by even angles: a flattened circle arc) or 'poly' (anything else: a
    polyline through points of its own)."""
    P = np.asarray(pts, dtype=float)
    if len(P) < 2:
        return "line"
    seg = P[1:] - P[:-1]
    ln = np.hypot(seg[:, 0], seg[:, 1])
    keep = ln > 1e-6
    seg, ln = seg[keep], ln[keep]
    if len(seg) <= 1:
        return "line"
    ang = np.arctan2(seg[:, 1], seg[:, 0])
    turn = np.degrees((np.diff(ang) + np.pi) % (2 * np.pi) - np.pi)
    if float(np.abs(turn).max()) < 0.5:
        return "line"
    if len(turn) >= 3:
        inner = ln[1:-1] if len(ln) > 3 else ln
        even_steps = float(inner.std()) <= 0.25 * float(inner.mean())
        one_way = bool((turn > 0).all() or (turn < 0).all())
        even_turn = float(np.abs(turn).std()) <= 0.35 * float(np.abs(turn).mean())
        if even_steps and one_way and even_turn:
            return "arc"
    return "poly"


def drawn_shares(chains: list, strokes: list) -> dict:
    """{piece: the share of its length drawn with straight lines and arcs} (0 = a polyline, 1 =
    lines and arcs), for the pieces whose strokes are known; a piece with none (drawn by the
    operator, kept from a record, read by the product) is left out: nothing is known of it."""
    by_idx = {s.idx: s for s in strokes}
    kind: dict = {}
    out: dict = {}
    for c in chains:
        tot = geo = 0.0
        for m in (c.members or []):
            s = by_idx.get(m)
            if s is None:
                continue
            k = kind.get(m)
            if k is None:
                k = kind[m] = stroke_kind(s.pts)
            tot += float(s.length)
            if k != "poly":
                geo += float(s.length)
        if tot > 0:
            out[c.id] = geo / tot
    return out


def dash_runs(chains: list, dashed_styles: set, reach: float = 12.0) -> dict:
    """{piece: run id} for the dashes of each DASHED pen that follow each other end to end: two
    ends of one pen within `reach` of each other, each the other's only near end (the nearest
    end rule, `_short_unique`, inside one pen). Pieces in no run are left out."""
    by_style: dict = defaultdict(list)
    for c in chains:
        if c.style in dashed_styles and not c.closed:
            by_style[c.style].append(c)
    parent: dict = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for sid, cs in by_style.items():
        if len(cs) < 2:
            continue
        for g in _short_unique(cs, set(), {}, {c.id: c.style for c in cs}, 0, reach, quiet=True):
            parent[find(g.a_chain)] = find(g.b_chain)
    return {c: ("run", find(c)) for c in list(parent)}


# ---------------------------------------------------------------------------
def _let_go(lab, note: str) -> None:
    """A PRINTED NUMBER TAKEN OFF A LINE IS WHAT IT WAS BEFORE IT WAS PUT ON ONE: printed with
    decimals a spot grade (labels.parse_text), printed whole a number. It used to become a
    "number" either way, and a number read from strokes that touches no line is no spot
    (place_spots: a bare OCR integer is too weak): so a grade like "85.97" that had stood between
    two stroke ends - "a spot grade beside the line, not its height", the notes here say - was
    in no surface on a sheet whose text is drawn, and a spot grade on a sheet with a text layer."""
    lab.kind = "spot" if _printed_decimals(lab.text) >= 1 else "number"
    lab.chain_ids = []
    lab.z_note = note


def build_contours(chains: list[Chain], gaps: list[Gap], families: list[Family],
                   labels: list[Label], min_chain_len: float = 12.0,
                   legend_boxes: list | None = None, min_single: float = 100.0,
                   typed_z: dict | None = None, core_links: dict | None = None,
                   core_chains: dict | None = None, drawn: dict | None = None,
                   stats: dict | None = None) -> list[Contour]:
    """Union chains across facing gaps into contours and attach printed heights.

    A label belongs to a contour when the line's two ends sit just beyond the
    text on both sides along its baseline (the label is IN the line's gap), or
    when the line runs through / alongside the text. The label's own baseline
    angle (from the glyph strokes or the text layer) is what makes this work
    for rotated labels.

    Lines that are not contours are set aside before the neighbour counting:
    utility lines (dash-text in their gaps) and, among the unlabelled lines,
    those that cross three or more other lines of the family (contours never
    cross each other)."""
    import re as _re
    from scipy.spatial import cKDTree
    fam_styles = {s for f in families for s in (f.styles or [f.style])}
    cmap = {c.id: c for c in chains}
    # the smoothness gate: a contour bends, it does not turn corners - except on a layer the drafter
    # NAMED as topography, where a proposed contour kinks at every pad edge and lot line (an open
    # piece there takes the long chains' allowance whatever its length: subdivision A, a 267-pt
    # lot contour with three corners was no contour, and the curb run it continues was counted
    # from its neighbours instead of joined to it)
    _topo_styles = {sid for f in families for sid in (f.styles or [f.style])
                    if _TOPO_NAMED.search(str(f.layer or "").split("|")[-1])}
    eligible = {c.id for c in chains if c.style in fam_styles and c.length >= min_chain_len
                and c.n_sharp <= (max(2, int(0.03 * c.length / 4.0))
                                  if (c.closed or (c.length < 300 and c.style not in _topo_styles))
                                  else max(2, int(0.15 * c.length / 4.0)))}
    style_gray = {}
    style_dashed = {}
    for f in families:
        for s in (f.styles or [f.style]):
            style_gray[s] = f.gray
            style_dashed[s] = bool(f.dashed)

    def ends_index(ids):
        ids = sorted(ids)
        E = np.zeros((2 * len(ids), 2))
        T = np.zeros((2 * len(ids), 2))
        for k, cid in enumerate(ids):
            ch = cmap[cid]
            E[2 * k] = ch.pts[0]
            E[2 * k + 1] = ch.pts[-1]
            T[2 * k] = end_tangent_pts(ch.pts, False)
            T[2 * k + 1] = end_tangent_pts(ch.pts, True)
        return ids, E, T, (cKDTree(E) if len(ids) else None)

    # --- 1. utility lines carry dash-text in their gaps (EX-FO, SS, OHE, W, G ...)
    utility_chains: set = set()
    if eligible:
        el0, E0, T0, tree0 = ends_index(eligible)
        for lab in labels:
            t = lab.text.strip().upper()
            if lab.value is not None:
                continue
            if not _re.match(r"^(EX[-\s]?)?(FO|SS|OHE|OHP|OHU|UGE|UGT|UGP|W|G|E|T|FM|SD|ST|CATV|TEL|TV|GAS|WTR|SAN|ELEC|UG|OH|FOC|FIB)$", t):
                continue
            a = math.radians(lab.angle)
            u = np.array([math.cos(a), math.sin(a)])
            n = np.array([-u[1], u[0]])
            c = np.array(lab.center)
            hl = max(lab.len_along, 4.0) / 2
            hh = max(lab.height, 3.0) / 2
            sides = {1: [], -1: []}
            for k in tree0.query_ball_point(c, hl + 14):
                d = E0[k] - c
                t_ = float(d @ u)
                r_ = float(d @ n)
                if abs(r_) <= hh + 2.5 and hl - 4 <= abs(t_) <= hl + 12:
                    sides[1 if t_ > 0 else -1].append(el0[k // 2])
            if sides[1] and sides[-1]:
                utility_chains.update(sides[1])
                utility_chains.update(sides[-1])
        if utility_chains:
            grow = True
            while grow:
                grow = False
                for g in gaps:
                    if g.length <= 60 and ((g.a_chain in utility_chains) != (g.b_chain in utility_chains)):
                        if g.a_chain in eligible and g.b_chain in eligible:
                            utility_chains.add(g.a_chain)
                            utility_chains.add(g.b_chain)
                            grow = True
            eligible -= utility_chains

    gone_x: set = set()             # pieces set aside by the three crossing rules (1b, 1b, 3)
    # --- 1b. perfectly straight lines crossing four or more others (lot lines, road
    # edges, easements) are not contours and must not win a label
    if eligible:
        el = sorted(eligible)
        geoms_e = [LineString(cmap[cid].pts) for cid in el]
        tree_e = STRtree(geoms_e)
        big_e = [cmap[cid].length >= 40 for cid in el]
        early = set()
        # A piece on a layer the drafter NAMED as topography is a contour by the sheet's own word:
        # it may cross any number of lot lines and contours of the other surface (a straight
        # contour along graded lots crosses every lot line and the existing ground), and only
        # crossings with its OWN surface say it is something else (the peel below). Subdivision A
        # sheet 3.1: the lots' 575 contour (CP-TOPO-MAJR-LOTS, 174 pt, a printed 575 oval on it)
        # was set aside here as "a straight line crossing four others", its label left loose.
        for i, g in enumerate(geoms_e):
            ch = cmap[el[i]]
            if ch.length < 60 or ch.closed:
                continue
            if ch.style in _topo_styles:
                continue
            p = ch.pts
            diag = float(np.hypot(*(p.max(axis=0) - p.min(axis=0))))
            # a rule line (table border, legend underline, a lot line) is drawn solid; a dashed
            # contour chained through its dashes has gaps in its ink and stays a contour however
            # straight its run (subdivision C: a 1,258-pt existing contour with 7 degrees of
            # turn in all was thrown out here with the pieces beside its "616" label; keeping every
            # many-point straight piece instead let 204 solid lot lines into the proposed surface)
            if diag / max(ch.length, 1e-6) < 0.985 or float(ch.ink) / max(ch.length, 1e-6) < 0.85:
                continue
            n = 0
            for j in tree_e.query(g, predicate="crosses"):
                j = int(j)
                if j != i and big_e[j]:
                    n += 1
                    if n >= 4:
                        break
            if n >= 4:
                early.add(el[i])
        if _DBG_JOINS and early:
            by_st: dict = defaultdict(lambda: [0, 0.0])
            for cid in early:
                by_st[cmap[cid].style][0] += 1
                by_st[cmap[cid].style][1] += cmap[cid].length
            role_dbg = {sid: f.role for f in families for sid in (f.styles or [f.style])}
            for sid, (n_, L_) in sorted(by_st.items(), key=lambda kv: -kv[1][1]):
                print(f"[early] style {sid} ({role_dbg.get(sid)}): {n_} straight pieces crossing 4+ lines set aside, {L_:.0f} pt",
                      file=sys.stderr)
        eligible -= early
        gone_x |= early
    # --- 1b. contours of one surface never cross each other. A piece that crosses other pieces of
    # its own surface at a rate no contour has (one crossing per 100 pt) is a lot line, an easement
    # rectangle, a hatch, a silt fence: peeled off worst first, so a real contour that merely
    # crosses many such lines loses its crossings as they go. Subdivision C sheet 3.1: the
    # vicinity map's hatch and 300 lot lines were "proposed" (black solid, like the contours), a
    # pad grade on one of them broke the interval; subdivision B: four easement rectangles in
    # the existing pen crossed 56-88 existing contours each and two of them were counted heights.
    if eligible:
        role_of = {sid: f.role for f in families for sid in (f.styles or [f.style])}
        # a dash is judged with its line (dash_runs): the dashes of a dashed pen that follow each
        # other end to end cross what their line crosses, over their line's length
        runs = dash_runs([cmap[cid] for cid in eligible], {sid for sid, d in style_dashed.items() if d})
        crossers = _peel_crossers(eligible, cmap, role_of, run_of=runs)
        eligible -= crossers
        gone_x |= crossers
    for dc in _DBG_CHAINS:
        ch = cmap.get(dc)
        print(f"[chain {dc}] eligible={dc in eligible} in_family={ch is not None and ch.style in fam_styles} "
              f"length={ch.length if ch else None} early={'early' in dir() and dc in early} peeled={dc in crossers if eligible else None}", file=sys.stderr)

    # --- 2. attach printed heights to lines (geometry of the label's baseline)
    label_pairs: list[tuple[int, int]] = []
    gap_list = [g for g in gaps if g.a_chain in eligible and g.b_chain in eligible]
    gap_role = {sid: f.role for f in families for sid in (f.styles or [f.style])}
    if eligible:
        elig, E, T, tree = ends_index(eligible)
        geoms = [LineString(cmap[cid].pts) for cid in elig]
        stree = STRtree(geoms)
        for lab in labels:
            if lab.pinned and lab.chain_ids:
                # attached by the operator: it stands as long as the line is a contour line
                keep = [int(c) for c in lab.chain_ids if int(c) in eligible]
                if keep and lab.value is not None and lab.used:
                    lab.kind = "contour"
                    lab.chain_ids = keep
                else:
                    _let_go(lab, "the line it was attached to is not a contour line")
                continue
            if lab.value is None or not lab.used or lab.tag:
                continue
            if lab.kind not in ("number", "contour", "spot"):
                continue
            if lab.value < 10 or (lab.source != "text" and lab.conf < 40):
                continue
            if legend_boxes and any(_inside(lab.center, b) for b in legend_boxes):
                continue
            a = math.radians(lab.angle)
            u = np.array([math.cos(a), math.sin(a)])
            n = np.array([-u[1], u[0]])
            c = np.array(lab.center)
            hl = max(lab.len_along, 4.0) / 2
            hh = max(lab.height, 3.0) / 2
            pos, neg = [], []
            for k in tree.query_ball_point(c, hl + 16):
                sg = style_gray.get(cmap[elig[k // 2]].style)
                colour_ok = sg is None or abs(sg - lab.gray) <= 0.28
                d = E[k] - c
                t = float(d @ u)
                r = float(d @ n)
                if abs(r) > hh + 3.0:
                    continue
                if not (hl - 4.0 <= abs(t) <= hl + 14.0):
                    continue
                if abs(float(T[k] @ u)) < 0.5:
                    continue
                (pos if t > 0 else neg).append((abs(t) - hl, elig[k // 2], colour_ok, k))
            pos.sort()
            neg.sort()
            is_int = float(lab.value).is_integer() and lab.kind != "spot"
            big = lab.value >= min_single          # lot / sheet numbers are small integers
            attached = []
            if pos and neg:
                # a label with line ends on both sides is IN the line's gap: the printed
                # colour of the text does not matter (black labels on gray lines happen)
                attached = [pos[0][1], neg[0][1]]
                # THE TWO ENDS OF ONE LINE, before the two nearest ends. The nearest end on each
                # side is whatever stroke happens to stop there; the line the height is printed
                # in has an end on EACH side, in one pen, each pointing at the other across the
                # text. The hotel site C3 (the street's paving hatch, 21.6-pt strokes of a pen
                # that draws no ground, runs under the existing contours' heights): a "5988" in
                # the gap of its contour took the contour's end on one side and a hatch stroke's
                # on the other, a "5986" took two hatch strokes and lost its line - and which
                # one a height took turned on half a point of the word's box.
                # A line of a pen that draws a surface comes before a line of a pen that draws
                # none (two strokes of that hatch face each other across the "5988", nearer than
                # the contour's own ends); among those, the nearer.
                pair = None
                for dp, cp, _okp, kp in pos:
                    for dn, cn, _okn, kn in neg:
                        if cmap[cp].style != cmap[cn].style:
                            continue
                        g = E[kp] - E[kn]
                        L = float(np.linalg.norm(g))
                        if L < 1e-6:
                            continue
                        g = g / L
                        if float(T[kn] @ g) < 0.7 or float(T[kp] @ g) > -0.7:
                            continue
                        rank = (0 if gap_role.get(cmap[cp].style) in ("existing", "proposed") else 1, dp + dn)
                        if pair is None or rank < pair[0]:
                            pair = (rank, cp, cn)
                if pair is not None:
                    attached = [pair[1], pair[2]]
                if _DBG_LABELS and lab.id in _DBG_LABELS:
                    print(f"[label {lab.id}] in a gap: nearest ends {pos[0][1]} / {neg[0][1]}, the two ends of one line: "
                          f"{pair[1:] if pair else None}", file=sys.stderr)
                if attached[0] != attached[1]:
                    label_pairs.append((attached[0], attached[1]))
            if not attached and is_int and big:
                # the line runs under / along the text (white-masked labels)
                box = shapely.box(c[0] - hl - 2, c[1] - hl - 2, c[0] + hl + 2, c[1] + hl + 2)
                best = None
                pt = shapely.points(c)
                for gi in stree.query(box, predicate="intersects"):
                    geom = geoms[gi]
                    dist = geom.distance(pt)
                    if dist > hh + 1.5:
                        continue
                    s = geom.project(pt)
                    q0, q1 = geom.interpolate(max(0.0, s - 6)), geom.interpolate(min(geom.length, s + 6))
                    dv = np.array([q1.x - q0.x, q1.y - q0.y])
                    nv = np.linalg.norm(dv)
                    if nv < 1e-6:
                        continue
                    par = abs(float(dv @ u)) / nv
                    # the line should run along the text; a line straight through the
                    # text centre is accepted at a wider angle (labels sit on kinks)
                    if par < 0.75 and not (dist <= 0.8 and par >= 0.35):
                        continue
                    score = par - dist / 4.0
                    if best is not None and score <= best[0]:
                        continue
                    best = (score, elig[gi])
                if best is not None:
                    attached = [best[1]]
            if not attached and (pos or neg) and is_int and big and (pos or neg)[0][2]:
                attached = [(pos or neg)[0][1]]
            if not attached and is_int:
                # a horizontal label sitting in the gap of a steep line: the gap's two ends
                # face each other across the text whatever the text angle
                best = None
                for g in gap_list:
                    if g.a_chain not in eligible or g.b_chain not in eligible or g.length > 70:
                        continue
                    ga = cmap[g.a_chain].pts[0 if g.a_end == 0 else -1]
                    gb = cmap[g.b_chain].pts[0 if g.b_end == 0 else -1]
                    seg = gb - ga
                    L = float(np.linalg.norm(seg))
                    if L < 1e-6:
                        continue
                    tt = float((c - ga) @ seg) / (L * L)
                    if not (0.05 <= tt <= 0.95):
                        continue
                    perp = abs(float((c - ga)[0] * seg[1] - (c - ga)[1] * seg[0])) / L
                    if perp > max(hh, hl) * 0.8 + 2:
                        continue
                    if best is None or perp < best[0]:
                        best = (perp, g)
                if best is not None:
                    g = best[1]
                    attached = [g.a_chain, g.b_chain]
                    label_pairs.append((g.a_chain, g.b_chain))
            if not attached and is_int and big:
                # A label between two piece ENDS of one pen, whatever the angles: a dashed existing
                # line curves through its own label, so the ends are neither along the text nor a
                # straight gap (subdivision C: 14 of 24 existing labels sat unattached between
                # the ends of the line's own pieces, and the existing surface had 10 heights on 186
                # lines). Two ends within reach, each pointing at the label, on opposite sides of
                # it, from two different pieces of the same pen: the label is in that line's gap.
                # reach: an oval label with its margins opens a gap of 50-60 pt in a dashed line, so
                # the far piece's end can sit 40 pt from the text centre
                near = []
                for k in tree.query_ball_point(c, hl + 40.0):
                    d = c - E[k]
                    L = float(np.linalg.norm(d))
                    if L < 1e-6 or float(T[k] @ d) / L < 0.3:
                        continue
                    near.append((L, elig[k // 2], E[k]))
                near.sort(key=lambda t: t[0])
                best = None
                for i in range(len(near)):
                    for j in range(i + 1, len(near)):
                        La, ca, ea = near[i]
                        Lb, cb, eb = near[j]
                        if ca == cb or cmap[ca].style != cmap[cb].style or not style_dashed.get(cmap[ca].style):
                            continue          # a dashed pen only: solid lines are masked under their
                                              # labels, and lot lines broken by a label are not contours
                                              # (subdivision C: four such pairs made 204 lot lines "proposed")
                        va, vb = ea - c, eb - c
                        cosang = float(va @ vb) / (float(np.linalg.norm(va)) * float(np.linalg.norm(vb)) + 1e-9)
                        if cosang > -0.3:
                            continue
                        if best is None or La + Lb < best[0]:
                            best = (La + Lb, ca, cb)
                if best is not None:
                    attached = [best[1], best[2]]
                    label_pairs.append((best[1], best[2]))
                if _DBG_LABELS and lab.id in _DBG_LABELS:
                    allends = []
                    for k in tree.query_ball_point(c, 60.0):
                        d = c - E[k]; L = float(np.linalg.norm(d))
                        allends.append((round(L, 1), elig[k // 2], cmap[elig[k // 2]].style, round(float(T[k] @ d) / max(L, 1e-6), 2)))
                    print(f"[label {lab.id}] end-pair rule: reach {hl + 40:.0f} near={[(round(L, 1), cid, cmap[cid].style) for L, cid, _ in near[:6]]} best={best} | every eligible end within 60 pt (dist, piece, style, toward): {sorted(allends)[:8]}", file=sys.stderr)
            if attached:
                lab.kind = "contour"
                lab.chain_ids = [int(x) for x in attached]
            if _DBG_LABELS and lab.id in _DBG_LABELS:
                print(f"[label {lab.id} '{lab.text}'] step 2: attached={attached} pos={pos[:2]} neg={neg[:2]} kind={lab.kind}", file=sys.stderr)

    # --- label frame vs surface. A sheet that frames one surface's heights (bold ovals) and prints
    # the other's plain says which surface a label belongs to; the attachments themselves teach the
    # rule (a frame with >= 5 labels, >= 80 % on one surface). A label attached against its frame's
    # surface sits where the two surfaces' lines cross: it goes to the line of the right surface
    # under it, or is let go. Subdivision A: five existing lines carried the proposed line's
    # bold-oval height (573, 575, 577, 580, 574) and one proposed line a plain existing 582.
    style_role = {sid: f.role for f in families for sid in (f.styles or [f.style])}
    by_frame: dict = defaultdict(Counter)
    for lab in labels:
        if lab.kind == "contour" and lab.chain_ids and not lab.pinned:
            role = style_role.get(cmap[lab.chain_ids[0]].style)
            if role in ("existing", "proposed"):
                by_frame[lab.frame][role] += 1
    frame_role = {}
    for fr, cnt in by_frame.items():
        tot = sum(cnt.values())
        role, n_role = cnt.most_common(1)[0]
        if tot >= 5 and n_role >= 0.8 * tot:
            frame_role[fr] = role

    def line_under(lab, want: str):
        """The line of the surface `want` that runs under the label (the on-line test of step 2,
        among that surface's lines only): its piece id, or None."""
        a = math.radians(lab.angle)
        u = np.array([math.cos(a), math.sin(a)])
        c = np.array(lab.center)
        hl = max(lab.len_along, 4.0) / 2
        hh = max(lab.height, 3.0) / 2
        box = shapely.box(c[0] - hl - 2, c[1] - hl - 2, c[0] + hl + 2, c[1] + hl + 2)
        pt = shapely.points(c)
        best = None
        for gi in stree.query(box, predicate="intersects"):
            if style_role.get(cmap[elig[gi]].style) != want:
                continue
            geom = geoms[gi]
            dist = geom.distance(pt)
            if dist > hh + 1.5:
                continue
            sp = geom.project(pt)
            q0, q1 = geom.interpolate(max(0.0, sp - 6)), geom.interpolate(min(geom.length, sp + 6))
            dv = np.array([q1.x - q0.x, q1.y - q0.y])
            nv = np.linalg.norm(dv)
            if nv < 1e-6:
                continue
            par = abs(float(dv @ u)) / nv
            if par < 0.75 and not (dist <= 0.8 and par >= 0.35):
                continue
            score = par - dist / 4.0
            if best is None or score > best[0]:
                best = (score, elig[gi])
        return None if best is None else int(best[1])

    if eligible and len(set(frame_role.values())) >= 2:
        for lab in labels:
            if lab.kind != "contour" or not lab.chain_ids or lab.pinned or lab.frame not in frame_role:
                continue
            want = frame_role[lab.frame]
            have = style_role.get(cmap[lab.chain_ids[0]].style)
            if have == want or have not in ("existing", "proposed"):
                continue
            under = line_under(lab, want)
            frame_name = "framed" if lab.frame else "plain"
            if under is not None:
                lab.chain_ids = [under]
                lab.z_note = f"{frame_name} like the sheet's {want} heights: moved to the {want} line under it"
            else:
                _let_go(lab, f"{frame_name} like the sheet's {want} heights, but no {want} line runs under it")
            if _DBG_LABELS and lab.id in _DBG_LABELS:
                print(f"[label {lab.id}] frame rule: frame={lab.frame!r} want={want} -> {lab.z_note}", file=sys.stderr)

    # --- label ink vs surface: the same, by the ink of the heights. Each pen has its usual ink (five
    # heights or more on its lines, three in four of them screened - or dark); a height in the OTHER
    # ink, on a sheet where a pen of the other surface prints its heights in that ink, sits where the
    # two surfaces' lines cross: it goes to the line of that surface under it, or is let go. Per PEN,
    # not per surface: the hotel site prints the heights of its existing 5-ft contours dark and those
    # of the lines between them screened, and both are right. Subdivision D (C3.0): two boxed black
    # heights of the channel's proposed contours (666, 668) stood on the screened existing lines that
    # pass under them, and 600 pt of existing ground took their heights.
    if eligible:
        fam_of_style = {sid: f.style for f in families for sid in (f.styles or [f.style])}
        ink_n: dict = defaultdict(lambda: [0, 0])          # pen family -> [screened, dark] heights on its lines
        for lab in labels:
            if lab.kind == "contour" and lab.chain_ids and not lab.pinned:
                st_ = cmap[lab.chain_ids[0]].style
                if style_role.get(st_) in ("existing", "proposed"):
                    ink_n[fam_of_style[st_]][0 if lab.gray >= SCREENED_INK else 1] += 1
        usual: dict = {}
        for fid, (n_s, n_d) in ink_n.items():
            if n_s + n_d >= 5 and max(n_s, n_d) >= 0.75 * (n_s + n_d):
                usual[fid] = 0 if n_s > n_d else 1
        fam_role_ = {f.style: f.role for f in families}
        inks_of = defaultdict(set)                          # surface -> the usual inks of its pens
        for fid, k_ in usual.items():
            inks_of[fam_role_[fid]].add(k_)
        # A PEN WHOSE HEIGHTS ARE MIXED TAKES ITS SURFACE'S INK, where the sheet prints each
        # surface's heights in one ink and the two surfaces in different inks (every pen that has
        # a usual ink says so). The heights that stand on a pen against its ink are themselves
        # what keeps it from having one: subdivision D (C3.1), the screened existing pen carried
        # 22 screened heights and 10 dark ones - boxed proposed heights standing where a proposed
        # line crosses it - 69 % where 75 % are asked, and every one of the ten stayed on the
        # existing line it stood on (a boxed 648 gave 94 pt of existing ground its height).
        # Only a pen with five heights or more, most of them in its surface's ink: the sheet's
        # own rule then stands for the three in four the pen falls short of. A pen whose heights
        # are mostly in the OTHER surface's ink says something else - that the pen is on the
        # wrong surface (the same job's existing index pen, dark, "proposed" by its colour, five
        # screened heights and two dark ones on it: roles_by_label_ink reads that, after this).
        surf_ink: dict = {}
        if (len(inks_of.get("existing", ())) == 1 and len(inks_of.get("proposed", ())) == 1
                and inks_of["existing"] != inks_of["proposed"]):
            surf_ink = {r: next(iter(inks_of[r])) for r in ("existing", "proposed")}
        mixed = {fid: (0 if n_s > n_d else 1) for fid, (n_s, n_d) in ink_n.items()
                 if n_s + n_d >= 5 and n_s != n_d and fid not in usual}
        if _DBG_LABELS:
            print(f"[ink rule] heights per pen [screened, dark]: {dict(ink_n)}; usual ink {usual}; by surface {dict(inks_of)}; "
                  f"the sheet's ink per surface {surf_ink}, mixed pens and their greater ink {mixed}", file=sys.stderr)
        for lab in labels:
            if lab.kind != "contour" or not lab.chain_ids or lab.pinned:
                continue
            st_ = cmap[lab.chain_ids[0]].style
            have = style_role.get(st_)
            mine = 0 if lab.gray >= SCREENED_INK else 1
            fid_ = fam_of_style[st_]
            own = usual.get(fid_)
            if own is None and fid_ in mixed and surf_ink.get(have) == mixed[fid_]:
                own = mixed[fid_]
            if have not in ("existing", "proposed") or own is None or own == mine:
                continue
            want = "proposed" if have == "existing" else "existing"
            if mine not in inks_of.get(want, ()):
                continue
            under = line_under(lab, want)
            ink_name = "screened" if mine == 0 else "dark"
            if under is not None:
                lab.chain_ids = [under]
                lab.z_note = f"printed {ink_name} like the sheet's {want} heights: moved to the {want} line under it"
            else:
                _let_go(lab, f"printed {ink_name} like the sheet's {want} heights, but no {want} line runs under it")
            if _DBG_LABELS and lab.id in _DBG_LABELS:
                print(f"[label {lab.id}] ink rule: ink={lab.gray:.2f} want={want} -> {lab.z_note}", file=sys.stderr)

    # a family's labels cluster within a few hundred feet; anything else is a misread
    from .labels import plausible_range
    style_to_fam = {}
    for f in families:
        for s in (f.styles or [f.style]):
            style_to_fam[s] = f.style
    by_fam: dict[int, list[Label]] = defaultdict(list)
    for lab in labels:
        if lab.kind == "contour" and lab.chain_ids:
            by_fam[style_to_fam.get(cmap[lab.chain_ids[0]].style, -1)].append(lab)
    # (A family with one or two labels has no range, and loses them: `rng is None` below. That is
    # too strict by the sheet and it STAYS, measured 2026-09-27 on the reader bench: letting such a
    # pen keep its labels halved the fill of subdivision C's streets on sheet 3.1, 3,601 ->
    # 1,882 CY - the pens with two "labels" there are lot lines under pad grades; kept only on
    # layers named as topography it gave subdivision A's flume its printed 570 (sheet 31, piece
    # 4200) and the counting then took the curb piece next to it from 572 to 569, beside a wall
    # end printed BW 572.31. The labels can come back when the counting knows a sunken
    # structure: out/verify/2026-09-27/reader-batch/VERIFY.md.)
    for fid, labs in by_fam.items():
        vals = np.array([l.value for l in labs], dtype=float)
        rng = plausible_range(list(vals))
        med = float(np.median(vals))
        mad = float(np.median(np.abs(vals - med))) if len(vals) >= 3 else 0.0
        tol = max(6.0 * mad, 30.0)
        for l in labs:
            if l.pinned:
                continue
            bad = rng is None or not (rng[0] - 40 <= l.value <= rng[1] + 40)
            if len(vals) >= 4 and abs(l.value - med) > tol:
                bad = True
            if bad:
                _let_go(l, "not in this family's range")
    labelled_chains = {cid for lab in labels if lab.kind == "contour" for cid in lab.chain_ids}

    # --- 2c. drafted geometry in a contour pen. A contour comes out of a surface model as a
    # polyline through many points; a curb, a walk, a building are drafted with straight lines
    # and arcs (`drawn_shares`). Where a pen's own LABELLED pieces are polylines - the sheet's
    # evidence of how its contours are drawn - an unlabelled piece of that pen drawn with lines
    # and arcs is drafted geometry, not a contour: it takes no height by counting and no ray
    # counts it as a line crossed. The hotel site C3: the pen the legend shows for the PROPOSED
    # CONTOUR also draws every curb, walk and stripe and the building; twelve curb runs took
    # counted heights (5990 - 5994 along 9,500 pt of curb), and the printed heights of the real
    # contours beside them were then set aside as "disagreeing with the neighbouring contours".
    # A dashed pen's dashes are straight strokes: its labelled pieces say so, and nothing is
    # set aside there. Only the pens of a surface are looked at (a pen the operator set aside
    # keeps every piece: his typed heights and joins stand on them - subdivision B moved by
    # 106 CY when its ignored pens were thinned), and only pieces of 100 pt and more: a short
    # straight stretch of a contour between two labels is one straight stroke too, and the
    # short pieces of a curb do no harm (subdivision C sheet 3.2: three pieces of 25 - 81
    # pt went, and the 38-pt piece they tied to its printed 620 stood alone).
    drafted: set = set()
    if drawn:
        fam_role_ = {f.style: f.role for f in families}
        lab_len: dict = defaultdict(lambda: [0.0, 0.0, 0])      # family -> [as polylines, all, pieces] of its labelled pieces
        for cid in labelled_chains:
            if cid in eligible and drawn.get(cid) is not None:
                a_ = lab_len[style_to_fam.get(cmap[cid].style)]
                a_[1] += cmap[cid].length
                a_[2] += 1
                if drawn[cid] <= 0.2:
                    a_[0] += cmap[cid].length
        on = {f_ for f_, (lp_, lt_, n_) in lab_len.items()
              if n_ >= 3 and lp_ >= 0.8 * lt_ and fam_role_.get(f_) in ("existing", "proposed")}
        if on:
            drafted = {cid for cid in eligible if cid not in labelled_chains and cid not in (typed_z or {})
                       and style_to_fam.get(cmap[cid].style) in on and drawn.get(cid) is not None and drawn[cid] >= 0.8
                       and cmap[cid].length >= 100.0}
            eligible -= drafted
            if stats is not None and drafted:
                stats["drafted"] = len(drafted)
                stats["drafted_pt"] = round(sum(cmap[c].length for c in drafted), 1)
            if _DBG_JOINS:
                print(f"[drafted] families {sorted(on)}: {len(drafted)} pieces drawn with lines and arcs set aside, "
                      f"{sum(cmap[c].length for c in drafted):.0f} pt", file=sys.stderr)
    for dc in _DBG_CHAINS:
        if dc in drafted:
            print(f"[chain {dc}] set aside: drawn with lines and arcs ({100 * drawn[dc]:.0f} %) in a pen whose labelled "
                  f"pieces are polylines", file=sys.stderr)

    # --- 3. contours never cross each other: an UNLABELLED, nearly straight line that
    # crosses four or more other lines of its family (grade breaks, road edges, swale
    # centrelines, easements) is not a contour. Removed worst-first.
    if eligible:
        el = sorted(eligible)
        geoms_x = [LineString(cmap[cid].pts) for cid in el]
        tree_x = STRtree(geoms_x)
        big = [cmap[cid].length >= 40 for cid in el]

        def straightness(cid):
            p = cmap[cid].pts
            diag = float(np.hypot(*(p.max(axis=0) - p.min(axis=0))))
            return diag / max(cmap[cid].length, 1e-6)

        # THE TWO SURFACES CROSS EACH OTHER wherever the job cuts or fills: a line of one surface
        # that crosses four lines of the OTHER is doing what contours do. Only the lines of its
        # own surface count against a piece of a surface pen - unless it is drawn dead straight
        # (a lot line, a street centreline, an easement in a contour pen; a contour out of a
        # surface model runs straight over a few feet, not over sixty points and four lines).
        # Subdivision D: the existing contours run nearly straight across the graded lots and
        # cross the proposed contours lot after lot; 32 of them on sheet C3.0 (8,396 pt) and 31
        # on C3.1 (10,887 pt) were set aside here, none crossing more than two lines of the
        # existing ground, and the existing surface under the lots was stretched between what
        # was left (found with the third critic: 3967, 855 pt across the knuckle, "in no line").
        # Subdivision B: 41 of the 45 pieces of the proposed pen this rule sets aside are
        # dead straight (lot lines), and stay set aside.
        crossing: dict[int, set] = {i: set() for i in range(len(el))}
        counted: dict[int, set] = {i: set() for i in range(len(el))}       # the crossings that count against i
        role_x = [gap_role.get(cmap[cid].style) for cid in el]
        dead = [straightness(cid) >= 0.998 for cid in el]
        for i, g in enumerate(geoms_x):
            for j in tree_x.query(g, predicate="crosses"):
                j = int(j)
                if j != i and big[j]:
                    crossing[i].add(j)
                    if role_x[i] not in ("existing", "proposed") or dead[i] or role_x[j] == role_x[i]:
                        counted[i].add(j)
        alive = set(range(len(el)))
        cand = [i for i in alive if el[i] not in labelled_chains and cmap[el[i]].length >= 60
                and straightness(el[i]) >= 0.96]
        for _ in range(400):
            worst = max(cand, key=lambda i: (len(counted[i] & alive), -el[i]), default=None)
            if worst is None or len(counted[worst] & alive) < 4:
                break
            alive.discard(worst)
            cand.remove(worst)
        crossers = {el[i] for i in range(len(el)) if i not in alive}
        if _DBG_CROSSERS:
            for i in range(len(el)):
                if i in alive:
                    continue
                own = sum(1 for j in crossing[i] if gap_role.get(cmap[el[j]].style) == gap_role.get(cmap[el[i]].style))
                print(f"[crosser] piece {el[i]} style {cmap[el[i]].style} {gap_role.get(cmap[el[i]].style)} {cmap[el[i]].length:.0f} pt "
                      f"straight {straightness(el[i]):.3f} crosses {len(crossing[i])} lines, {own} of its own surface", file=sys.stderr)
        eligible -= crossers
        gone_x |= crossers
    # (what the three crossing rules set aside in the pens of the two surfaces is said on the sheet:
    # it used to go without a word, and 19,000 pt of subdivision D's existing contours with it)
    gone_s = [c for c in gone_x if gap_role.get(cmap[c].style) in ("existing", "proposed")]
    if stats is not None and gone_s:
        stats["crossers"] = len(gone_s)
        stats["crossers_pt"] = round(sum(cmap[c].length for c in gone_s), 1)

    # --- 4. one contour = chains joined across facing gaps and label gaps
    parent = {cid: cid for cid in eligible}
    # known heights (printed labels, typed by the operator) per piece, then per joined component:
    # two components with different known heights are two contours meeting and never join, even
    # through unlabelled pieces between them (subdivision A #888: a 581 pad contour reached a 580
    # one through three road pieces; every pair passed, the whole did not)
    chain_z: dict[int, set] = {}
    for lab in labels:
        if lab.kind == "contour" and lab.value is not None:
            for cid in lab.chain_ids:
                chain_z.setdefault(cid, set()).add(lab.value)
    # the operator's typed heights are known heights too: subdivision B's 537 line (printed) was
    # joined to the piece Koby typed 538 on, and took 538 over its whole length
    # A height the operator typed on a piece outranks a printed label on that same piece (he typed 559
    # over a "560" oval: the record says 559, the label stays on screen as printed): the piece's known
    # height is his, so the line he typed on stays one line instead of splitting at the label.
    for cid, z in (typed_z or {}).items():
        if cid in eligible:
            chain_z[cid] = {float(z)}
    comp_z: dict[int, set] = {cid: set(zs) for cid, zs in chain_z.items() if cid in eligible}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b, why: str = "") -> bool:
        ra, rb = find(a), find(b)
        if ra == rb:
            return True
        za, zb = comp_z.get(ra), comp_z.get(rb)
        ok = not (za and zb and za != zb)
        if _DBG_CHAINS and (a in _DBG_CHAINS or b in _DBG_CHAINS):
            print(f"[chain {a}] union with {b} ({why}): {'joined' if ok else 'refused'}; component heights "
                  f"{sorted(za) if za else '-'} | {sorted(zb) if zb else '-'}", file=sys.stderr)
        if not ok:
            return False
        parent[rb] = ra
        if zb:
            comp_z[ra] = (za or set()) | zb
        return True

    # A piece end joins once: a line has two ends and never forks. The style-gap pass and
    # cross_surface_gaps already pair each end once among themselves; the label-pair pass and the
    # product's joins (core_links) did not look, and the core pass unioned a piece into a line
    # that another pass had already continued from the same end (subdivision A, engine+core:
    # ten pieces of the 579 line and the 577 line in one 1,626-pt fork).
    used_ends: set = set()

    def facing_ends(a: int, b: int) -> tuple:
        pa, pb = cmap[a].pts, cmap[b].pts
        best = None
        for ea, qa in ((0, pa[0]), (1, pa[-1])):
            for eb, qb in ((0, pb[0]), (1, pb[-1])):
                d = float(np.hypot(qa[0] - qb[0], qa[1] - qb[1]))
                if best is None or d < best[0]:
                    best = (d, ea, eb)
        return best[1], best[2]

    def end_free(a: int, ea: int, b: int, eb: int, why: str) -> bool:
        if (a, ea) in used_ends or (b, eb) in used_ends:
            if _DBG_CHAINS and (a in _DBG_CHAINS or b in _DBG_CHAINS):
                print(f"[chain {a}] join with {b} ({why}) refused: an end is already continued (no forks)", file=sys.stderr)
            return False
        return True

    # only families that will survive qualify_families join across styles: guess_role calls every
    # "CP-" layer proposed, so the right-of-way line (CP-ROAD-ROW, 1,423 pt, no label) was a
    # proposed piece here and swallowed two lot contours before it was set aside (#756)
    labelled_styles = {cmap[cid].style for lab in labels if lab.kind == "contour" for cid in lab.chain_ids if cid in cmap}
    join_fams = [f for f in families if f.forced or f.role in ("existing", "proposed") and (
        any(sid in labelled_styles for sid in (f.styles or [f.style]))
        or (f.role_reason.startswith("layer name") and re.search(r"TOPO|CONT", f.layer.upper()) is not None))]
    # THE NEARER END WINS. A piece end with exactly one other end of its surface within 12 pt
    # (and that end's only one) continues into it, whatever the angle, and that is decided
    # before the long facing pairs. Subdivision A sheet 3.0: an existing contour crosses the
    # crowned street in a V; its legs end 9 pt from each other at the curbs, turned 90-150
    # degrees, so the facing test refused them and each leg's free end was paired with a
    # well-aligned end 45-60 pt away across the street (292-279 at 45 pt while 291 ends 3 pt
    # from 292; 279-271 at 56 pt across an oval while 279 turns into 280 and 271 comes from 270).
    # what each pen draws, for the joins: by its layer's name, else by its own printed heights
    # (three, all multiples of 5: the index contours; five, none a multiple: the lines between)
    pen_rank = _style_ranks(join_fams)
    printed_on: dict = defaultdict(list)
    for lab in labels:
        if lab.kind == "contour" and lab.value is not None and lab.chain_ids and lab.chain_ids[0] in cmap:
            printed_on[cmap[lab.chain_ids[0]].style].append(float(lab.value))
    for st_, vs_ in printed_on.items():
        on_ = sum(1 for v_ in vs_ if _is_multiple(v_, 5.0))
        if st_ not in pen_rank and len(vs_) >= 3 and on_ == len(vs_):
            pen_rank[st_] = "index"
        elif st_ not in pen_rank and len(vs_) >= 5 and on_ == 0:
            pen_rank[st_] = "between"
    n_near = 0
    for g in near_end_pairs([cmap[cid] for cid in eligible], join_fams, known_z=chain_z, first_id=len(gaps), rank=pen_rank):
        why = f"nearest end {g.length:.1f} pt"
        if find(g.a_chain) != find(g.b_chain) and end_free(g.a_chain, g.a_end, g.b_chain, g.b_end, why) \
                and union(g.a_chain, g.b_chain, why):
            used_ends.add((g.a_chain, g.a_end)); used_ends.add((g.b_chain, g.b_end))
            n_near += 1
    for g in gaps:
        if g.a_chain in eligible and g.b_chain in eligible and g.length <= 60:
            why = f"style gap {g.length:.1f} pt"
            if find(g.a_chain) == find(g.b_chain):
                continue
            if end_free(g.a_chain, g.a_end, g.b_chain, g.b_end, why) and union(g.a_chain, g.b_chain, why):
                used_ends.add((g.a_chain, g.a_end)); used_ends.add((g.b_chain, g.b_end))
    # (pieces of ONE surface only: a bold oval sitting between the end of an existing piece and the
    # end of a proposed pad outline unioned them - subdivision A #316 - and one between a lot
    # contour and the right-of-way line made a 2,481-pt "contour" - #742)
    role_of_style = {sid: f.role for f in families for sid in (f.styles or [f.style])}
    for a, b in label_pairs:
        if a in eligible and b in eligible and role_of_style.get(cmap[a].style) == role_of_style.get(cmap[b].style):
            ea, eb = facing_ends(a, b)
            if find(a) != find(b) and end_free(a, ea, b, eb, "label pair") and union(a, b, "label pair"):
                used_ends.add((a, ea)); used_ends.add((b, eb))
    # pieces of one surface that continue into each other across CAD layers (cross_surface_gaps),
    # paired among the eligible pieces only and never across two pieces the sheet labels with
    # different heights (those are two contours meeting, not one)
    n_cross = 0
    # (the ends already taken are the ends that JOINED above - not every end a facing pair named:
    # a pair the known-height guard or the no-fork rule refused leaves both its ends free)
    for g in cross_surface_gaps([cmap[cid] for cid in eligible], gaps, join_fams, known_z=chain_z, taken=set(used_ends),
                                rank=pen_rank):
        why = f"cross-surface gap {g.length:.1f} pt"
        if find(g.a_chain) != find(g.b_chain) and end_free(g.a_chain, g.a_end, g.b_chain, g.b_end, why) \
                and union(g.a_chain, g.b_chain, why):
            used_ends.add((g.a_chain, g.a_end)); used_ends.add((g.b_chain, g.b_end))
            n_cross += 1
    # the product's detection as joins: engine pieces that lie on ONE CORE chain (core_reading.py,
    # pattern-aware chaining across symbol masks) are one line - same surface, same known height,
    # through the same guarded union as every other join
    n_core = 0
    if core_links:
        by_core: dict = defaultdict(list)
        for cid in eligible:
            k = core_links.get(cid) if isinstance(core_links, dict) else None
            if k is None:
                k = core_links.get(str(cid)) if isinstance(core_links, dict) else None
            if k is not None:
                by_core[(k, role_of_style.get(cmap[cid].style))].append(cid)
        for (k, role), cids in by_core.items():
            if len(cids) < 2 or role not in ("existing", "proposed"):
                continue
            # CORE's dash-pattern chaining strings ADJACENT contours together where their dashes
            # line up (subdivision B: a 37-piece "line" from 527 to 533 ft). So: only pieces that follow
            # each other along the CORE chain, end to end (no overlap in their projections, at most
            # 60 pt apart), and not running alongside each other, are one line.
            cpts = (core_chains or {}).get(k) if isinstance(core_chains, dict) else None
            if cpts is None and isinstance(core_chains, dict):
                cpts = core_chains.get(str(k))
            if not cpts or len(cpts) < 2:
                continue
            line = LineString(cpts)
            spans = []
            for cid in cids:
                pts = cmap[cid].pts
                a = line.project(shapely.points(pts[0])); b = line.project(shapely.points(pts[-1]))
                spans.append((min(a, b), max(a, b), cid))
            spans.sort()
            for (s0, e0, ca), (s1, e1, cb) in zip(spans, spans[1:]):
                gap = s1 - e0
                if gap < -4.0 or gap > 60.0:
                    continue
                pa, pb = cmap[ca].pts, cmap[cb].pts
                # the end of a nearest the start of b, for the alongside test
                ia = 1 if line.project(shapely.points(pa[-1])) >= line.project(shapely.points(pa[0])) else 0
                ib = 0 if line.project(shapely.points(pb[0])) <= line.project(shapely.points(pb[-1])) else 1
                ea, eb = pa[-1 if ia else 0], pb[-1 if ib else 0]
                if _alongside(np.asarray(pb, float), LineString(pa), eb) or _alongside(np.asarray(pa, float), LineString(pb), ea):
                    continue
                why = f"core chain {k}, {gap:.1f} pt apart"
                if find(ca) != find(cb) and end_free(ca, ia, cb, ib, why) and union(ca, cb, why):
                    used_ends.add((ca, ia)); used_ends.add((cb, ib))
                    n_core += 1
    for dc in _DBG_CHAINS:
        if dc in eligible:
            comp = sorted(c for c in eligible if find(c) == find(dc))
            print(f"[chain {dc}] component after the joins: {len(comp)} pieces {comp[:40]}", file=sys.stderr)
    for lab in labels:
        if lab.kind == "contour":
            before_ids = list(lab.chain_ids)
            lab.chain_ids = [cid for cid in lab.chain_ids if cid in eligible]
            if not lab.chain_ids:
                _let_go(lab, "on a line that is not a contour")
            if _DBG_LABELS and lab.id in _DBG_LABELS:
                print(f"[label {lab.id}] eligibility filter: {before_ids} -> {lab.chain_ids} kind={lab.kind}", file=sys.stderr)
    # A number printed to the hundredth ("618.16") among a surface's labels printed whole ("617",
    # "618") is a pad or curb grade that happens to sit on a line, not the line's height. Subdivision
    # C sheet 3.1: one such label on a lot line put 0.16 among the proposed surface's printed
    # differences, the interval fell to 0.2 ft, and eight printed heights (a 617 line carrying seven
    # labels among them) were thrown out as disagreeing with neighbours that were now five steps
    # away. The precision a surface prints its contour labels in is the mode of its labels; a label
    # printed finer than that goes back to being a number (a spot grade, placed like any other).
    by_role: dict = defaultdict(list)
    for lab in labels:
        if lab.kind == "contour" and lab.value is not None and lab.used and lab.chain_ids and not lab.pinned:
            by_role[role_of_style.get(cmap[lab.chain_ids[0]].style)].append(lab)
    for role, labs_r in by_role.items():
        decs = Counter(_printed_decimals(l.text) for l in labs_r)
        mode = decs.most_common(1)[0][0]
        if mode >= 2:
            continue
        for l in labs_r:
            if _printed_decimals(l.text) >= 2:
                _let_go(l, "printed to the hundredth: a spot grade beside the line, not its height")

    groups: dict[int, list[int]] = defaultdict(list)
    for cid in eligible:
        groups[find(cid)].append(cid)
    contours: list[Contour] = []
    chain_to_contour: dict[int, int] = {}
    for k, (root, cids) in enumerate(groups.items()):
        style = cmap[cids[0]].style
        ct = Contour(id=k, style=style, chain_ids=sorted(cids), length=sum(cmap[c].length for c in cids))
        contours.append(ct)
        for c in cids:
            chain_to_contour[c] = k
    votes: dict[int, Counter] = defaultdict(Counter)
    lab_by_contour: dict[int, list[int]] = defaultdict(list)
    for lab in labels:
        if lab.kind != "contour" or lab.value is None or not lab.used:
            continue
        for cid in lab.chain_ids:
            k = chain_to_contour.get(cid)
            if k is None:
                continue
            votes[k][lab.value] += 1
            lab_by_contour[k].append(lab.id)
            break
    for ct in contours:
        if ct.id in votes:
            ranked = votes[ct.id].most_common()
            v, n = ranked[0]
            total = sum(votes[ct.id].values())
            # TWO PRINTED HEIGHTS THAT DISAGREE, ONE VOTE EACH, DECIDE NOTHING: the first one read used
            # to win (most_common keeps the order of arrival on a tie). Subdivision D (C3.1): a
            # 2,319-pt proposed contour carries a "653" and, 360 pt on, a "653" read as "655"; it
            # took 655 because that label came first, and the check then threw out its neighbour's
            # 652 - printed twice, read right twice - as "disagreeing with 10 neighbours". On a tie
            # the line is left to the count from its neighbours, its printed heights listed.
            tie = len(ranked) > 1 and ranked[1][1] == n
            if n >= max(1, math.ceil(total * 0.5)) and not tie:
                ct.z = v
                ct.how = "label"
                ct.votes = n
                ct.label_ids = list(lab_by_contour[ct.id])
            else:
                ct.candidates = [{"z": z, "votes": c} for z, c in ranked]
                if tie:
                    ct.z_note = "its printed heights disagree (" + " / ".join(f"{z:g}" for z, _ in ranked[:3]) + "): counted from the neighbours"
    return contours


def near_end_pairs(chains: list, families: list, known_z: dict | None = None, taken: set | None = None,
                   first_id: int = 0, reach: float = 12.0, rank: dict | None = None) -> list:
    """The short unique pairs of each surface (and of each pen of no surface group): two piece
    ends within `reach` of each other with no third end near either one are one line, at any
    angle (`_short_unique` holds the guards). `taken` = ends already continued."""
    import dataclasses as _dc
    taken = set(taken or ())
    style_of = {c.id: c.style for c in chains}
    rank = _style_ranks(families) if rank is None else rank
    out: list = []
    grouped: set = set()
    for gi, grp in enumerate(role_groups(families)):
        sty = grp["styles"]
        grouped |= set(sty)
        cs = [_dc.replace(c, style=-100 - gi) for c in chains if c.style in sty and not c.closed]
        if len(cs) >= 2:
            out += _short_unique(cs, taken, known_z or {}, style_of, first_id + len(out), reach, rank=rank)
    by_style: dict = defaultdict(list)
    for c in chains:
        if c.style not in grouped and not c.closed:
            by_style[c.style].append(c)
    for sid, cs in by_style.items():
        if len(cs) >= 2:
            out += _short_unique(cs, taken, known_z or {}, style_of, first_id + len(out), reach)
    return out


def _style_ranks(families: list) -> dict:
    """{pen: "index" | "between"} by the name of its layer (`layer_rank`)."""
    out = {}
    for f in families:
        r = layer_rank(f.layer)
        if r:
            for s in (f.styles or [f.style]):
                out[s] = r
    return out


def _short_unique(cs: list, taken: set, known_z: dict, style_of: dict, first_id: int, reach: float = 12.0,
                  quiet: bool = False, rank: dict | None = None) -> list:
    """Two ends of one surface within `reach` with no third end near either one are one line:
    nothing else ends there. A contour trimmed at a lot line where it also kinks (a swale V
    along the lot line) ends 6-9 pt from its own continuation at any angle, and the facing rule
    refuses it (subdivision A: #341/#342, #336/#337, #626/#627 ...). `taken` is updated.

    `rank` = {pen: "index" | "between"} by the layers' own names: an index contour never
    continues into a line between index contours (a 589 line reached the 592 line through a
    16-pt stub of the MAJOR pen at a headwall, subdivision A sheet 3.0, and the check then
    threw out the printed 590 beside it - independent check of 2026-09-27)."""
    from .chains import Gap
    from scipy.spatial import cKDTree
    out: list = []
    ends = []
    body = {}
    pts_of = {}
    for c in cs:
        if len(c.pts) < 2:
            continue
        ends.append((c.id, 0, c.pts[0]))
        ends.append((c.id, 1, c.pts[-1]))
        body[c.id] = LineString(c.pts)
        pts_of[c.id] = np.asarray(c.pts, dtype=float)
    if len(ends) < 4:
        return out
    P = np.array([e[2] for e in ends])
    tree = cKDTree(P)
    for i, (cid, end, pt) in enumerate(ends):
        if (cid, end) in taken:
            continue
        near = [j for j in tree.query_ball_point(pt, reach)
                if ends[j][0] != cid and (ends[j][0], ends[j][1]) not in taken]
        if len(near) != 1:
            continue
        j = near[0]
        back = [k for k in tree.query_ball_point(P[j], reach)
                if ends[k][0] != ends[j][0] and (ends[k][0], ends[k][1]) not in taken]
        if back != [i]:
            continue
        ocid, oend, opt = ends[j]
        if rank:
            ra, rb = rank.get(style_of.get(cid)), rank.get(style_of.get(ocid))
            if ra and rb and ra != rb:
                continue
        # a trim, not a continuation: my end lies on the other piece's BODY (a lot contour
        # trimmed against the curb loop, whose own end happened to start there)
        if _on_body(pt, body[ocid], 3.0) or _on_body(opt, body[cid], 3.0):
            continue
        # two different known heights (printed or typed) are two contours meeting, never one
        za, zb = known_z.get(cid), known_z.get(ocid)
        if za and zb and set(za) != set(zb):
            continue
        # the NEXT contour, not my continuation: on a slope the 537 and 538 lines are trimmed
        # at the same edge 8 pt apart and run side by side; a V-kink at a lot line leaves at once
        v = opt - pt
        d = float(np.hypot(v[0], v[1]))
        # (... or 22 pt apart, drawing together to 9.7 pt where they are trimmed: two lot contours of
        # Subdivision A sheet 3.0, lot 18, were joined end to end and the 573 line took the 572
        # line beside it. `_runs_beside` looks 60 - 120 pt back, where the legs of a V are long
        # gone from each other; a wider `_alongside` would not do - the legs of a short V are near
        # each other for their first 30 pt, and the street V's of sheet 3.0 lost their joins.)
        beside = (_alongside(pts_of[ocid], body[cid], opt) or _alongside(pts_of[cid], body[ocid], pt)
                  or _runs_beside(pts_of[cid], end == 1, body[ocid], d) or _runs_beside(pts_of[ocid], oend == 1, body[cid], d))
        if beside:
            # ... unless the two pieces LEAVE each other from the gap on: a contour doubling back
            # through a narrow ridge or ditch (a hairpin whose legs end at its tip, one dash gap
            # apart) opens steadily away from the tip, two neighbours trimmed at one edge stay the
            # same distance apart. Subdivision C: a dashed existing line zigzags through five
            # such tips 4.5 pt wide; refused here, the line broke in two and 778 pt of it lost the
            # printed 617.
            if not (_opens_from(pts_of[cid], end == 1, body[ocid], d) and _opens_from(pts_of[ocid], oend == 1, body[cid], d)):
                continue
        # A hairpin (subdivision A: a piece running east along a street, and the 577
        # contour's piece starting 8 pt behind its end heading back south-west) has the same
        # local shape as a swale V, so geometry cannot refuse it here: measured on four sheets,
        # a "starts behind the end" refusal split 15 legitimate V's for the one hairpin. The
        # join is made and `_split_disagreeing_pieces` (verify_heights) undoes it on the
        # evidence: the hairpin piece's own neighbours put it two intervals away.
        if _DBG_JOINS and not quiet:
            if d >= 4.0:
                ta = end_tangent_pts(pts_of[cid], end == 1)
                tb = end_tangent_pts(pts_of[ocid], oend == 1)
                fa = float(ta @ v) / d
                fb = float(tb @ (-v)) / d
                behind = min(fa, fb) < -0.5
                print(f"[join] short unique gap {cid}:{end} - {ocid}:{oend} {d:.1f} pt, forward {fa:+.2f} / {fb:+.2f}"
                      f"{' (starts behind the end it continues: a sharp V or a hairpin; the per-piece check decides)' if behind else ''}",
                      file=sys.stderr)
            else:
                print(f"[join] short unique gap {cid}:{end} - {ocid}:{oend} {d:.1f} pt (touching)", file=sys.stderr)
        g = Gap(id=first_id + len(out), style=style_of[cid], a_chain=cid, a_end=end, b_chain=ocid, b_end=oend,
                center=((pt[0] + opt[0]) / 2, (pt[1] + opt[1]) / 2), angle=math.atan2(v[1], v[0]), length=d)
        taken.add((cid, end)); taken.add((ocid, oend))
        out.append(g)
    return out


def cross_surface_gaps(chains: list, gaps: list, families: list, known_z: dict | None = None,
                       taken: set | None = None, rank: dict | None = None) -> list:
    """SAME-SURFACE CONTINUATIONS ACROSS CAD LAYERS. A contour that leaves a lot for the road
    changes layer (CP-TOPO-MINR-LOTS -> -ROAD; CE-TOPO-MINR -> -SURFACE on subdivision A), so
    the per-style gap pass in read_sheet never met its two ends and every such line stayed in
    pieces with no shared height. With every style's surface known, pair the facing ends of any
    two pieces of one surface (up to 60 pt apart, within 40 degrees: contours bend a little where
    a drafter broke them). Ends the style pass already joined keep their partner."""
    import dataclasses as _dc
    from .chains import find_gaps
    if taken is not None:
        used = set(taken)               # the ends the caller has continued already (each end joins once)
    else:
        used = set()
        for g in gaps:
            if g.length <= 60:
                used.add((g.a_chain, g.a_end)); used.add((g.b_chain, g.b_end))
    style_of = {c.id: c.style for c in chains}
    rank = _style_ranks(families) if rank is None else rank
    out = []
    for gi, grp in enumerate(role_groups(families)):
        sty = grp["styles"]
        cs = [_dc.replace(c, style=-100 - gi) for c in chains if c.style in sty and not c.closed]
        if len(cs) < 2:
            continue
        for g in find_gaps(cs, min_len=1.0, max_len=60.0, max_angle_deg=40.0, exclude_ends=used,
                           first_id=len(gaps) + len(out)):
            ra, rb = rank.get(style_of[g.a_chain]), rank.get(style_of[g.b_chain])
            if ra and rb and ra != rb:
                continue                 # an index contour and a line between two of them: never one line
            g.style = style_of[g.a_chain]
            if _DBG_JOINS:
                print(f"[join] facing ends {g.a_chain}:{g.a_end} (style {style_of[g.a_chain]}) - {g.b_chain}:{g.b_end} "
                      f"(style {style_of[g.b_chain]}) {g.length:.1f} pt"
                      f"{' SAME STYLE' if style_of[g.a_chain] == style_of[g.b_chain] else ''}", file=sys.stderr)
            out.append(g)
        # the short unique pairs among the ends still free (the nearer end has had its turn
        # before the long pairs when the caller ran near_end_pairs first)
        taken_now = set(used) | {(g.a_chain, g.a_end) for g in out} | {(g.b_chain, g.b_end) for g in out}
        out += _short_unique(cs, taken_now, known_z or {}, style_of, len(gaps) + len(out), rank=rank)
    return out


def _alongside(other_pts: np.ndarray, line, gap_pt, radius: float = 15.0, frac: float = 0.3) -> bool:
    """Does the other piece run alongside this line (beyond the gap itself)? 30 % of its points
    within `radius` of the line, not counting the 20 pt nearest the gap, means a neighbour."""
    if line is None or len(other_pts) < 2:
        return False
    d_gap = np.hypot(other_pts[:, 0] - gap_pt[0], other_pts[:, 1] - gap_pt[1])
    sel = other_pts[d_gap > 20.0]
    if len(sel) < 2:
        return False
    step = max(1, len(sel) // 40)
    sel = sel[::step]
    near = sum(1 for q in sel if line.distance(shapely.points(q)) <= radius)
    return near / len(sel) >= frac


def _runs_beside(pts: np.ndarray, at_end: bool, other, gap: float, steps=(30.0, 60.0, 80.0, 100.0, 120.0)) -> bool:
    """Does this piece still run beside the other one far from the gap? Walking back from its
    end, its distance to the other piece's body at 60, 80, 100 and 120 pt (as far as it is long;
    60 pt at least) stays within 30 pt (three gaps, when that is more) AND has stopped growing
    (under 6 pt in 100 from the 30-pt mark on): two neighbours trimmed at one edge keep their
    distance (21.7 pt at 30, 24.2 at 120 on subdivision A). The legs of a V are 40 pt and more
    apart by then, and the legs of a slow hairpin are still leaving each other."""
    P = np.asarray(pts, dtype=float)
    if at_end:
        P = P[::-1]
    if other is None or len(P) < 2:
        return False
    seg = np.hypot(*(P[1:] - P[:-1]).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    reach = max(30.0, 3.0 * float(gap))
    seen = []
    for s_ in steps:
        if s_ > total:
            break
        i = int(np.searchsorted(cum, s_))
        t = (s_ - cum[i - 1]) / max(seg[i - 1], 1e-9)
        q = P[i - 1] + t * (P[i] - P[i - 1])
        dist = float(other.distance(shapely.points(q)))
        if dist > reach:
            return False
        seen.append((s_, dist))
    if len(seen) < 2:
        return False
    (s0, d0), (s1, d1) = seen[0], seen[-1]
    return (d1 - d0) / (s1 - s0) < 0.06


def _opens_from(pts: np.ndarray, at_end: bool, other, gap: float, steps=(10.0, 20.0, 30.0)) -> bool:
    """Does this piece leave the other one steadily, walking back from its end? Its distance to
    the other piece's body at 10, 20 and 30 pt from the end (as far as it is long) must grow at
    every step and reach at least twice the gap plus 3 pt: the legs of a V or a hairpin. Two
    lines running side by side keep their distance."""
    P = np.asarray(pts, dtype=float)
    if at_end:
        P = P[::-1]
    if other is None or len(P) < 2:
        return False
    seg = np.hypot(*(P[1:] - P[:-1]).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    last = float(gap)
    n = 0
    for s_ in steps:
        if s_ > total:
            break
        i = int(np.searchsorted(cum, s_))
        t = (s_ - cum[i - 1]) / max(seg[i - 1], 1e-9)
        q = P[i - 1] + t * (P[i] - P[i - 1])
        dist = float(other.distance(shapely.points(q)))
        if dist < last + 1.0:
            return False
        last = dist
        n += 1
    return n >= 2 and last >= 2.0 * float(gap) + 3.0


def _on_body(pt, line, tol: float) -> bool:
    """Does the point lie on the line's body, i.e. closer than `tol` to it but more than 12 pt
    from both of its ends (a T-junction, not a facing end)?"""
    if line is None or line.length < 30.0:
        return False
    p = shapely.points(pt)
    if line.distance(p) > tol:
        return False
    s = line.project(p)
    return 12.0 < s < line.length - 12.0


def end_tangent_pts(pts: np.ndarray, at_end: bool) -> np.ndarray:
    from .geom import end_tangent
    return end_tangent(pts, at_end, lookback=8.0)


def _inside(p, box) -> bool:
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


def qualify_families(families: list[Family], contours: list[Contour], min_labels: int = 3,
                     labels: list[Label] | None = None) -> None:
    """A style with almost no contour labels is not a contour family (utility lines,
    property lines, hatch share colours with contours)."""
    lab_by_id = {l.id: l for l in (labels or [])}
    for fam in families:
        sts = fam.styles or [fam.style]
        lids = [lid for c in contours if c.style in sts for lid in c.label_ids]
        n = len(lids)
        # labels printed to the hundredth are pad and curb grades on their leaders, not contour
        # labels: a pen whose "labels" are nearly all such is the spot-grade leader pen (the hotel
        # site C3: the 0.70 hairline pen carried six "80.95"-style grades on 21-pt leaders and
        # was called the existing surface)
        n_spot = sum(1 for lid in lids if lid in lab_by_id and _printed_decimals(lab_by_id[lid].text) >= 2)
        if n and n_spot >= 0.7 * n and not fam.forced:
            fam.n_labels = n - n_spot
            if not fam.role_reason.startswith("layer name"):
                fam.role = "ignore"
                fam.role_reason = f"its {n_spot} label(s) are spot grades printed to the hundredth, not contour labels"
                # ... AND THE GRADES ARE SPOT GRADES AGAIN. They had been taken for the pen's
                # contour labels (each stands between two stroke ends of it); with the pen set
                # aside they stayed "contour labels" of lines that are no ground, and no surface
                # had them. The hotel site C3: twelve grades along the street (84.94 .. 90.95, the
                # top of asphalt and the edge of the existing asphalt, printed over the paving
                # hatch) were in neither surface.
                for c in contours:
                    if c.style not in sts or not c.label_ids:
                        continue
                    back = [lid for lid in c.label_ids if lid in lab_by_id and not lab_by_id[lid].pinned
                            and _printed_decimals(lab_by_id[lid].text) >= 2]
                    if not back:
                        continue
                    for lid in back:
                        _let_go(lab_by_id[lid], "")
                    c.label_ids = [lid for lid in c.label_ids if lid not in back]
                    c.basis = [b for b in (c.basis or []) if b.get("label") not in back]
                    if not c.label_ids and c.how == "label" and c.user is None:
                        c.z, c.how, c.votes = None, "", 0
                continue
            n = n - n_spot
        fam.n_labels = n
        # A layer the drafter named as topography (CE-TOPO-MAJR-ROAD, CP-TOPO-MINR-DRAIN ...) is a
        # contour family whether or not a label happened to land on it: its heights come from
        # counting off the labelled neighbours or from the operator. Subdivision A: four CP-TOPO
        # layers (majors, road, drain) carried no readable label and were dropped, with every
        # proposed height on them. Anything else with no labels is still set aside.
        named_topo = fam.role_reason.startswith("layer name") and re.search(r"TOPO|CONT", fam.layer.upper()) is not None
        if n < min_labels and fam.role != "ignore" and not fam.forced and not named_topo:
            fam.role = "ignore"
            fam.role_reason = f"only {n} contour label(s) read on it"


SCREENED_INK = 0.45     # a printed height this gray or lighter is screened (the pens use the same line)


def roles_by_label_ink(families: list[Family], contours: list[Contour], labels: list[Label],
                       min_labels: int = 3, share: float = 0.75) -> list:
    """THE INK OF A PEN'S OWN PRINTED HEIGHTS. A sheet that screens its existing ground prints the
    existing heights screened too, and the proposed ones black; its existing INDEX contours may
    still be drawn dark (heavier, or dark and thin) - their heights stay screened. So where the
    sheet shows both inks - an existing pen whose heights are screened, a proposed pen whose
    heights are dark - a pen that is "proposed" on its colour alone and carries screened heights
    (three at least, three in four of them) is a pen of the existing ground.
    Subdivision D, sheets C3.0 / C3.1: the existing index contours (dark, 0.3 wide, dashed; their
    heights 640 .. 665, every one a multiple of 5 and screened) were the proposed surface by the
    rule "dark linework, the screened family is the existing ground": the proposed surface took
    the existing heights along them and the existing count lost every fifth line.
    One way only: an existing pen with dark heights is nothing unusual (the hotel site: the
    existing 5-ft contours, darker gray, heights in the same ink). A pen named by the legend,
    the operator or its layer is never moved. Returns [(family, screened, labels)] moved."""
    lab = {l.id: l for l in labels}
    ink: dict = {}
    for f in families:
        sts = set(f.styles or [f.style])
        ls = [lab[i] for c in contours if c.style in sts for i in c.label_ids if i in lab]
        ink[f.style] = (sum(1 for l in ls if l.gray >= SCREENED_INK), len(ls))

    def mostly(f, screened: bool) -> bool:
        g, n = ink[f.style]
        return n >= min_labels and (g if screened else n - g) >= share * n
    if not (any(f.role == "existing" and mostly(f, True) for f in families)
            and any(f.role == "proposed" and mostly(f, False) for f in families)):
        return []
    out = []
    for f in families:
        if f.role != "proposed" or f.forced or str(f.role_reason).startswith("layer name") or not mostly(f, True):
            continue
        g, n = ink[f.style]
        f.role = "existing"
        f.role_reason = f"its printed heights are screened like the existing ground's ({g} of {n})"
        out.append((f, g, n))
    return out


def _looks_like_height(t: str) -> bool:
    import re
    return bool(re.match(r"^\d{2,5}(\.\d{1,2})?$", t))


# ---------------------------------------------------------------------------
def role_groups(families: list[Family]) -> list[dict]:
    """One counting group per surface: every family with the same role, so index
    contours in one pattern and intermediate ones in another count each other."""
    groups: dict[str, dict] = {}
    for fam in families:
        if fam.role not in ("existing", "proposed"):
            continue
        g = groups.setdefault(fam.role, {"role": fam.role, "styles": set(), "families": [], "interval": None})
        g["styles"].update(fam.styles or [fam.style])
        g["families"].append(fam)
    return list(groups.values())


def infer_intervals(contours: list[Contour], chains: list[Chain], families: list[Family],
                    labels: list[Label] | None = None) -> None:
    """Interval per surface from the differences between spatially neighbouring labelled contours."""
    cmap = {c.id: c for c in chains}
    for fam in families:
        fc = [c for c in contours if c.style in (fam.styles or [fam.style])]
        fam.n_contours = len(fc)
        labelled = [c for c in fc if c.z is not None]
        fam.n_with_z = len(labelled)
        fam.n_labels = sum(len(c.label_ids) for c in fc)
        fam.length_with_z = sum(c.length for c in labelled)
        fam.length_total = sum(c.length for c in fc)
        fam.interval = None
    for grp in role_groups(families):
        fc = [c for c in contours if c.style in grp["styles"]]
        labelled = [c for c in fc if c.z is not None]
        if len(labelled) < 2:
            continue
        fam = grp["families"][0]
        # neighbour differences by ray casting between labelled contours
        geoms, owners = _family_geoms(fc, cmap)
        tree = STRtree(geoms)
        diffs: Counter = Counter()
        zmap = {c.id: c.z for c in fc}
        for c in labelled:
            for (p, n) in _samples(c, cmap, every=90.0, max_n=6):
                for sgn in (1, -1):
                    hits = _ray_hits(p, n * sgn, 120.0, tree, geoms, owners, exclude=c.id)
                    if not hits:
                        continue
                    seen = []
                    for d, oid in hits:
                        if oid in seen:
                            break
                        seen.append(oid)
                        if zmap.get(oid) is not None:
                            k = len(seen)
                            dz = abs(zmap[oid] - c.z)
                            if dz > 0 and k > 0:
                                iv = dz / k
                                iv_r = _snap_interval(iv)
                                if iv_r is not None:
                                    diffs[iv_r] += 1
                            break
        iv, off = _choose_interval(diffs, [c.z for c in labelled])
        grp["interval"] = iv
        for f in grp["families"]:
            f.interval_votes = dict(diffs.most_common(5))
            f.interval = iv
        # a printed height off the surface's grid: halfway between two contours it is a
        # supplemental contour and stands as printed; anywhere else (618.16 on 1 ft contours) it
        # is a spot grade that sits on the line - the line goes back to being counted and the
        # label to being a number, placed like any other spot grade
        lab_by_id = {l.id: l for l in (labels or [])}
        for c in labelled:
            if iv is None or c.how != "label" or not any(abs(c.z - o) < 1e-6 for o in off):
                continue
            half = abs((c.z / iv) - round(c.z / iv))
            if abs(half - 0.5) < 0.02:
                c.z_note = f"printed height halfway between the {iv:g} ft contours: a supplemental contour"
                continue
            if any(lab_by_id[l].pinned for l in c.label_ids if l in lab_by_id):
                c.z_note = f"off the {iv:g} ft contour grid; kept because you attached the label"
                continue
            c.candidates = [{"z": c.z, "votes": c.votes,
                             "note": f"printed {c.z:g} is off the {iv:g} ft contour grid: a spot grade on the line?"}]
            for lid in c.label_ids:
                lab = lab_by_id.get(lid)
                if lab is not None and not lab.pinned:
                    _let_go(lab, f"off the {iv:g} ft contour grid: a spot grade beside the line, not its height")
            c.z = None
            c.how = ""
            c.votes = 0
            c.label_ids = []
        # A printed height far from every other printed height of its surface is a misread, not a
        # peak (subdivision C: a "616" in the gap of a dashed line read as "646" at 45 %
        # confidence, 28 ft above every other existing label, put 20,600 CY of cut in one block).
        # Single-label lines only, and only when the surface has enough labels to say so.
        vals = sorted(c.z for c in labelled if c.z is not None)
        if iv is not None and len(vals) >= 5:
            for c in labelled:
                if c.z is None or c.how != "label" or c.votes > 1:
                    continue
                if any(lab_by_id[l].pinned for l in c.label_ids if l in lab_by_id):
                    continue
                others = list(vals)
                others.remove(c.z)
                nearest = min(abs(v - c.z) for v in others) if others else 0.0
                if nearest <= 10 * iv:
                    continue
                c.candidates = [{"z": c.z, "votes": c.votes,
                                 "note": f"printed {c.z:g} is {nearest:g} ft from every other printed height of this surface: a misread label?"}]
                for lid in c.label_ids:
                    lab = lab_by_id.get(lid)
                    if lab is not None:
                        lab.kind = "number"
                        lab.chain_ids = []
                        lab.used = False
                        lab.z_note = f"{nearest:g} ft from every other printed height of the surface: a misread label, set aside"
                c.z = None
                c.how = ""
                c.votes = 0
                c.label_ids = []


def _printed_decimals(text: str) -> int:
    """How many decimals a printed number carries ("618.16" -> 2, "617" -> 0)."""
    m = re.search(r"\d+(?:\.(\d+))?", text or "")
    return len(m.group(1)) if m and m.group(1) else 0


def _choose_interval(diffs: Counter, zs: list[float]) -> tuple[float | None, list[float]]:
    """The surface's interval, and the printed heights that do not sit on its grid.

    The candidates are the intervals the rays measured between labelled neighbours and the
    printed steps themselves. A candidate must put (nearly) every printed height on its grid:
    the one or two that miss are spot grades that landed on a line or misread labels, never
    the reason to shrink the interval. Among the fitting candidates the votes decide
    (fragmented lines over-count and invent half intervals; missing lines under-count and
    invent doubles), and a half-interval winner that barely beats the printed step loses to
    the printed step. Subdivision C: 177 votes for 1 ft, 4 for 0.2 ft, and the old rule
    ("the interval must divide EVERY printed difference") took 0.2 ft because a pad grade of
    618.16 sat on a lot line."""
    vals = sorted(set(zs))
    if len(vals) < 2:
        return None, []
    cands: dict = dict(diffs)
    for a, b in zip(vals, vals[1:]):
        s = _snap_interval(b - a)
        if s is not None:
            cands.setdefault(s, 0)

    def on_grid(iv):
        return [v for v in vals if abs(v / iv - round(v / iv)) < 0.02]
    fits = []
    for iv, n in cands.items():
        og = on_grid(iv)
        if len(og) >= max(2, math.ceil(0.8 * len(vals))):
            fits.append((iv, n, og))
    if not fits:
        # no grid holds the printed heights (an odd base): the interval must divide every printed
        # difference, else the smallest printed step
        ok = [(iv, n) for iv, n in diffs.items()
              if all(abs((b - a) / iv - round((b - a) / iv)) < 0.02 for a, b in zip(vals, vals[1:]))]
        if not ok:
            d = [round(b - a, 3) for a, b in zip(vals, vals[1:]) if b > a]
            return (_snap_interval(min(d)) if d else None), []
        ok.sort(key=lambda t: (-t[1], -t[0]))
        return ok[0][0], []
    fits.sort(key=lambda t: (-t[1], -t[0]))
    winner, n_win, og = fits[0]
    dmin = min((round(b - a, 3) for a, b in zip(og, og[1:]) if b > a), default=None)
    if dmin is not None and winner < dmin - 1e-6:
        for iv, n, og2 in fits[1:]:
            if abs(iv - dmin) < 1e-6 and n >= 0.8 * n_win:
                winner, og = iv, og2
                break
    return winner, [v for v in vals if v not in og]


def _snap_interval(iv: float) -> float | None:
    best = min(INTERVALS, key=lambda x: abs(x - iv))
    return best if abs(best - iv) <= 0.15 * best + 0.02 else None


def _family_geoms(fc: list[Contour], cmap: dict[int, Chain]):
    """The lines a counting ray can cross for one surface: every piece of every contour, plus
    bridges across the gaps (a ray must not slip through a break and miss a crossing).
    Two kinds of bridge: between the pieces of ONE contour (label gaps), and - since 2026-09-25 -
    between the facing ends of two DIFFERENT contours of the surface that are one physical line
    the drafter left in pieces (subdivision A trims every contour short of the lot lines: 272
    existing contours of 91 pt median, and a ray at one sample crossed a piece its neighbour
    sample missed, so the counts disagreed and 97 lines ended as conflicts). A cross bridge
    takes the labelled contour's identity when exactly one side is labelled, and two pieces the
    sheet labels differently are never bridged."""
    from scipy.spatial import cKDTree
    geoms = []
    owners = []
    all_ends = []                # (point, outward tangent, contour id)
    used_global = set()
    zc = {c.id: c.z for c in fc}
    for c in fc:
        ends = []
        base = len(all_ends)
        for cid in c.chain_ids:
            pts = cmap[cid].pts
            if len(pts) >= 2:
                geoms.append(LineString(pts))
                owners.append(c.id)
                ends.append(pts[0])
                ends.append(pts[-1])
                all_ends.append((pts[0], end_tangent_pts(pts, False), c.id))
                all_ends.append((pts[-1], end_tangent_pts(pts, True), c.id))
        # bridge the gaps between the pieces of one contour (label gaps)
        if len(ends) > 2:
            E = np.array(ends)
            n = len(E)
            used = set()
            for i in range(n):
                if i in used:
                    continue
                best = None
                for j in range(n):
                    if j == i or j // 2 == i // 2 or j in used:
                        continue
                    d = float(np.linalg.norm(E[i] - E[j]))
                    if d <= 70 and (best is None or d < best[0]):
                        best = (d, j)
                if best is not None:
                    used.add(i)
                    used.add(best[1])
                    used_global.add(base + i)
                    used_global.add(base + best[1])
                    geoms.append(LineString([E[i], E[best[1]]]))
                    owners.append(c.id)
    # bridges between the facing ends of two contours that are one physical line
    if len(all_ends) > 2:
        P = np.array([e[0] for e in all_ends])
        tree = cKDTree(P)
        cos45 = math.cos(math.radians(45))
        cands = []
        for i, j in tree.query_pairs(90.0):
            if i in used_global or j in used_global:
                continue
            oi, oj = all_ends[i][2], all_ends[j][2]
            if oi == oj:
                continue
            v = P[j] - P[i]
            d = float(np.linalg.norm(v))
            if d < 1.0:
                continue
            v = v / d
            ci = float(all_ends[i][1] @ v)
            cj = float(all_ends[j][1] @ (-v))
            if ci < cos45 or cj < cos45:
                continue
            zi, zj = zc.get(oi), zc.get(oj)
            if zi is not None and zj is not None and zi != zj:
                continue
            cands.append((d + 30.0 * (2 - ci - cj), i, j))
        cands.sort()
        taken = set()
        for _, i, j in cands:
            if i in taken or j in taken:
                continue
            taken.add(i)
            taken.add(j)
            oi, oj = all_ends[i][2], all_ends[j][2]
            owner = oi if zc.get(oi) is not None else (oj if zc.get(oj) is not None else oi)
            geoms.append(LineString([P[i], P[j]]))
            owners.append(owner)
    return geoms, owners


def _samples(c: Contour, cmap: dict[int, Chain], every: float = 60.0, max_n: int = 8):
    """Sample points + unit normals along the longest chain of a contour."""
    cid = max(c.chain_ids, key=lambda k: cmap[k].length)
    pts = resample(cmap[cid].pts, 4.0)
    L = len(pts)
    if L < 3:
        return []
    step = max(1, int(every / 4.0))
    idx = list(range(step // 2, L - 1, step))[:max_n]
    if not idx:
        idx = [L // 2]
    out = []
    for i in idx:
        a = pts[max(0, i - 2)]
        b = pts[min(L - 1, i + 2)]
        t = b - a
        nt = np.linalg.norm(t)
        if nt < 1e-6:
            continue
        t = t / nt
        n = np.array([-t[1], t[0]])
        out.append((pts[i], n))
    return out


def _piece_samples(pts: np.ndarray, every: float = 60.0, max_n: int = 6):
    """Sample points + unit normals along ONE piece (a chain's points)."""
    pts = resample(pts, 4.0)
    L = len(pts)
    if L < 3:
        return []
    step = max(1, int(every / 4.0))
    idx = list(range(step // 2, L - 1, step))[:max_n]
    if not idx:
        idx = [L // 2]
    out = []
    for i in idx:
        a = pts[max(0, i - 2)]
        b = pts[min(L - 1, i + 2)]
        t = b - a
        nt = np.linalg.norm(t)
        if nt < 1e-6:
            continue
        t = t / nt
        out.append((pts[i], np.array([-t[1], t[0]])))
    return out


def _split_disagreeing_pieces(fc: list, cmap: dict, I: float, tree, geoms, owners, max_ray: float,
                              lab_by_id: dict, next_id: int) -> list:
    """A joined line is verified on EVERY piece, not only its longest one. Subdivision A: the
    existing 579 line ran down lot 16 (labelled), east along the road and - through a hairpin
    join - back south-west through Block 21 on a piece the other sheet shows as 577; `_samples`
    only ever looked at the longest piece, so the line passed and Block 21 got 760 CY of cut.
    For each unlabelled piece of a labelled or counted line, rays to the OTHER lines of the
    surface bound its height (a neighbour k crossings away is within k intervals); when two or
    more rays agree on a set of heights that excludes the line's height, the piece is split off
    and counts alone (the one height that fits, else its candidates). Rays that contradict each
    other (a line the reading lacks) decide nothing. Returns the new contours."""
    # a neighbour votes only when its own height is solid: the operator's, or printed twice or
    # more, or counted and still standing after the line-level rounds. The hotel site C3: a
    # single-label line read "5980" for a printed 5990 sat next to the site's main 5994 contour
    # (1,678 pt) and the first cut of this rule took that line's height away on its say-so.
    solid = {c.id: c.z for c in fc if c.z is not None and (c.how in OPERATOR_HOWS or c.how == "counted"
                                                            or (c.how == "label" and c.votes >= 2))}
    new = []
    for c in fc:
        if c.z is None or c.how not in ("label", "counted") or len(c.chain_ids) < 2:
            continue
        own_label_pieces = {cid for lid in c.label_ids if lid in lab_by_id for cid in (lab_by_id[lid].chain_ids or [])}
        for cid in list(c.chain_ids):
            ch = cmap.get(cid)
            if ch is None or cid in own_label_pieces or ch.length < 30.0 or len(c.chain_ids) < 2:
                continue
            allowed = None
            n_rays = 0
            sides = set()
            for (p, n) in _piece_samples(ch.pts):
                for sgn in (1, -1):
                    seen = []
                    for d, oid in _ray_hits(p, n * sgn, max_ray, tree, geoms, owners, exclude=c.id):
                        if oid in seen:
                            break
                        seen.append(oid)
                        z = solid.get(oid)
                        if z is None:
                            continue
                        # one interval of margin for a line the reading lacks between the piece and
                        # its neighbour (the hotel site: the site's main 5994 contour lost its height
                        # to 5992 / 5993 neighbours read at one crossing with the 5993 line unread)
                        k = len(seen) + 1
                        fits = {round(z + m * I, 3) for m in range(-k, k + 1)}
                        allowed = fits if allowed is None else (allowed & fits)
                        n_rays += 1
                        sides.add(sgn)
                        break
            # the line's height must be ruled out from BOTH sides of the piece: one neighbour two
            # intervals away is a cliff or a misread, two facing ones are a wrong join
            if n_rays < 2 or len(sides) < 2 or not allowed or min(abs(v - c.z) for v in allowed) < 0.01:
                continue
            if _DBG_CHAINS and cid in _DBG_CHAINS:
                print(f"[chain {cid}] split off line #{c.id} ({c.z:g}): {n_rays} rays to solid neighbours allow {sorted(allowed)}", file=sys.stderr)
            c.chain_ids.remove(cid)
            c.length = max(0.0, c.length - ch.length)
            heights = ", ".join(f"{v:g}" for v in sorted(allowed))
            # the piece is left OPEN (no height, not "conflict" which the count skips): the second
            # count after this check gives it a height under propagate's own agreement rules - a
            # split never hands out a height by itself (a 423-pt line moved 4 ft that way on the hotel site)
            nc = Contour(id=next_id, style=ch.style, chain_ids=[cid], z=None, how="", length=ch.length, votes=0,
                         candidates=[{"z": v, "votes": 0, "rays_ok": n_rays, "rays_bad": 0,
                                      "note": "one of the heights its neighbours allow"} for v in sorted(allowed)],
                         z_note=f"split off a {c.z:g} ft line: its neighbours allow {heights} ft")
            next_id += 1
            new.append(nc)
    return new


def _ray_hits(p: np.ndarray, n: np.ndarray, length: float, tree: STRtree, geoms, owners, exclude: int):
    ray = LineString([p + n * 0.5, p + n * length])
    idx = tree.query(ray, predicate="intersects")
    hits = []
    for i in idx:
        oid = owners[i]
        if oid == exclude:
            continue
        inter = ray.intersection(geoms[i])
        if inter.is_empty:
            continue
        for g in getattr(inter, "geoms", [inter]):
            if g.geom_type == "Point":
                d = float(np.hypot(g.x - p[0], g.y - p[1]))
                hits.append((d, oid))
            else:
                x, y = g.coords[0]
                hits.append((float(np.hypot(x - p[0], y - p[1])), oid))
    hits.sort()
    return hits


def _group_interval(grp: dict):
    """The surface's contour interval for counting: what the labelled neighbours measured, else
    the one interval every family of the surface agrees on (the sheet's own note, or a single
    family's read). Subdivision A: 44 labelled lines spread over 272, no two within a ray's
    reach, so the measured interval was None and NO height was ever counted - while every family
    already carried the sheet's 1 ft."""
    if grp.get("interval") is not None:
        return grp["interval"]
    ivs = {f.interval for f in grp["families"] if f.interval}
    return ivs.pop() if len(ivs) == 1 else None


def pen_ranks(contours: list, cmap: dict, labels: list | None, interval: float, styles: set | None = None,
              families: list | None = None) -> dict:
    """{pen: "index" | "between"} by the sheet's own printed heights. A pen whose printed heights
    (three at least) are ALL multiples of five intervals (570, 575, 580 at 1 ft) draws the index
    contours; a pen with five or more printed heights and NOT ONE such multiple draws the lines
    between them. Any other pen says nothing. Subdivision A (independent check, 2026-09-27):
    10 of 10 printed heights on the major pens are multiples of 5, 0 of 39 on the minor pens of
    sheet 3.0, 0 of 21 on sheet 3.1 - and counting had put 574 on a major pen (sheet: 570) and
    580 on two minor ones. Only the printed heights a line stands on are counted (its accepted
    labels, on the piece they are printed on). A pen whose LAYER says what it draws (`layer_rank`:
    CE-TOPO-MAJR, -MINR) needs no printed height to say it, as long as none of its own speaks
    against the name."""
    if not labels or not interval:
        return {}
    M = 5.0 * float(interval)
    lab_by_id = {l.id: l for l in labels}
    named = {s: r for s, r in _style_ranks(families or []).items() if styles is None or s in styles}
    vals: dict = defaultdict(list)
    for ct in contours:
        if ct.z is None or ct.how != "label":
            continue
        for lid in ct.label_ids or []:
            lab = lab_by_id.get(lid)
            if lab is None or lab.value is None or abs(lab.value - ct.z) > 0.01 or not lab.chain_ids:
                continue
            ch = cmap.get(lab.chain_ids[0])
            if ch is not None and (styles is None or ch.style in styles):
                vals[ch.style].append(float(lab.value))
    out = {}
    for st, vs in vals.items():
        on = sum(1 for v in vs if _is_multiple(v, M))
        if len(vs) >= 3 and on == len(vs):
            out[st] = "index"
        elif len(vs) >= 5 and on == 0:
            out[st] = "between"
    for st, r in named.items():
        vs = vals.get(st) or []
        on = sum(1 for v in vs if _is_multiple(v, M))
        if st not in out and (on == len(vs) if r == "index" else on == 0):
            out[st] = r
    return out


def _is_multiple(v: float, m: float) -> bool:
    return abs(v / m - round(v / m)) * m <= 0.01


def pen_allows(ct, z: float, ranks: dict, cmap: dict, interval: float) -> bool:
    """May this line stand at z, by what its pens draw (`pen_ranks`)? A line on an index pen
    stands on a multiple of five intervals only, a line on a pen between them never; a line on
    pens of both kinds, or of neither, may stand anywhere."""
    if not ranks:
        return True
    kinds = {ranks.get(cmap[cid].style) for cid in ct.chain_ids if cid in cmap} - {None}
    if len(kinds) != 1:
        return True
    return _is_multiple(z, 5.0 * float(interval)) == (kinds == {"index"})


def propagate_heights(contours: list[Contour], chains: list[Chain], families: list[Family],
                      rounds: int = 4, max_ray: float = 420.0, budget_s: float = 90.0,
                      labels: list | None = None) -> dict:
    """Count intervals from labelled neighbours to give unlabelled contours a height.

    From several points on the line, look perpendicular in both directions and
    list the contours crossed. Two labelled neighbours on opposite sides must
    agree with the count (|zb - za| = (ka + kb) x interval). One side alone is
    enough when it holds two labelled contours in a row: they say which way is
    uphill. Votes across the samples decide; disagreement leaves the line
    without a height (and lists the candidates). With `labels`, a counted height a line's pen
    cannot carry (`pen_ranks`) is not given."""
    import time as _time
    cmap = {c.id: c for c in chains}
    deadline = _time.time() + budget_s
    summary = {"budget_hit": False}

    def straightness(c):
        cid = max(c.chain_ids, key=lambda k: cmap[k].length)
        p = cmap[cid].pts
        diag = float(np.hypot(*(p.max(axis=0) - p.min(axis=0))))
        return diag / max(cmap[cid].length, 1e-6)

    for grp in role_groups(families):
        if os.environ.get("CUTFILL_DEBUG_CT"):
            print(f"[debug group] {grp['role']} interval {grp['interval']} styles {sorted(grp['styles'])} families {[(f.style, f.interval) for f in grp['families']]}")
        I = _group_interval(grp)
        if I is None:
            continue
        fc = [c for c in contours if c.style in grp["styles"]]
        # short perfectly straight pieces (curbs, grade breaks, lot lines drawn in the contour pen)
        # are neither counted nor crossed: a ray that counts them mis-counts every neighbour
        countable = [c for c in fc if c.z is not None or not (c.length < 200 and straightness(c) > 0.985)]
        geoms, owners = _family_geoms(countable, cmap)
        if not geoms:
            continue
        tree = STRtree(geoms)
        # lines worth counting: long enough to sample, and not the short straight
        # pieces (lot lines, curbs) that fill a grading sheet
        todo = [c for c in fc if c.length >= 30 and not (c.length < 200 and straightness(c) > 0.985)]
        ranks = pen_ranks(fc, cmap, labels, I, grp["styles"], grp["families"])
        for _ in range(rounds):
            changed = 0
            zmap = {c.id: c.z for c in fc}
            for c in todo:
                if c.z is not None or c.how == "conflict":
                    continue
                if _time.time() > deadline:
                    summary["budget_hit"] = True
                    break
                votes: Counter = Counter()
                weak: Counter = Counter()
                basis: dict[float, list] = defaultdict(list)
                voters: dict[float, set] = defaultdict(set)
                strong: Counter = Counter()
                n_samples = 0
                dbg = os.environ.get("CUTFILL_DEBUG_CT") == str(c.id)
                for si, (p, n) in enumerate(_samples(c, cmap, every=60.0, max_n=10)):
                    n_samples += 1
                    sides = []
                    for sgn in (1, -1):
                        hits = _ray_hits(p, n * sgn, max_ray, tree, geoms, owners, exclude=c.id)
                        seen = []
                        labelled = []          # [(k, oid, z)] in crossing order
                        for d, oid in hits:
                            if oid in seen:
                                break         # the ray re-crossed a line: counts past here are unreliable          # hairpin: re-crossing a contour we already crossed
                            seen.append(oid)
                            if zmap.get(oid) is not None:
                                labelled.append((len(seen), oid, zmap[oid]))
                                if len(labelled) >= 2:
                                    break
                        sides.append(labelled)
                    a, b = sides
                    if dbg:
                        print(f"[debug ct {c.id}] sample {si} at {p.round(0)}: side+ {a} side- {b}")
                    if a and b:
                        ka, oa, za = a[0]
                        kb, ob, zb = b[0]
                        if oa != ob and abs(abs(zb - za) - (ka + kb) * I) <= 0.01:
                            s = 1 if zb > za else -1
                            z = round(za + s * ka * I, 3)
                            votes[z] += 2
                            voters[z].add(si)
                            strong[z] += 1
                            basis[z].append({"contour": oa, "k": ka, "z": za, "dir": s})
                            basis[z].append({"contour": ob, "k": kb, "z": zb, "dir": -s})
                            continue
                    decided = False
                    for side in (a, b):
                        if len(side) >= 2:
                            (k1, o1, z1), (k2, o2, z2) = side[0], side[1]
                            if k2 > k1 and abs(abs(z2 - z1) - (k2 - k1) * I) <= 0.01 and z2 != z1:
                                s = 1 if z2 > z1 else -1       # heights rise away from us
                                z = round(z1 - s * k1 * I, 3)
                                votes[z] += 1
                                voters[z].add(si)
                                basis[z].append({"contour": o1, "k": k1, "z": z1, "dir": -s})
                                basis[z].append({"contour": o2, "k": k2, "z": z2, "dir": -s})
                                decided = True
                    if decided:
                        continue
                    for side in (a, b):
                        if len(side) == 1:
                            k1, o1, z1 = side[0]
                            for s in (1, -1):
                                weak[round(z1 + s * k1 * I, 3)] += 1
                if os.environ.get("CUTFILL_DEBUG_CT") == str(c.id):
                    print(f"[debug ct {c.id}] samples {n_samples} votes {dict(votes)} weak {dict(weak)} strong {dict(strong)} voters {{k: sorted(v) for k, v in voters.items()}}")
                if not votes and weak:
                    c.candidates = [{"z": zz, "votes": vv} for zz, vv in weak.most_common(4)]
                    continue
                if not votes:
                    continue
                (z, v), = votes.most_common(1)
                total = sum(votes.values())
                # short lines get one or two sample points: a two-sided agreement there is enough.
                # A two-sided agreement (both neighbours labelled, count and heights consistent)
                # backed by one more sample is enough on a long line too: subdivision A contour
                # #62 had 584 from a two-sided sample and a one-sided pair, 581 from one stray
                # sample, and was left as a "conflict" for the operator because only two samples
                # had seen a labelled neighbour on this sparsely labelled sheet.
                need = 3 if c.length > 300 and n_samples >= 4 else 2
                enough = (len(voters[z]) >= need
                          or (strong[z] >= 1 and len(voters[z]) >= 2)
                          or (strong[z] >= 1 and c.length < 150 and n_samples <= 2))
                if enough and v >= 0.6 * total and pen_allows(c, z, ranks, cmap, I):
                    c.z = z
                    c.how = "counted"
                    c.votes = v
                    c.basis = basis[z][:6]
                    changed += 1
                else:
                    c.candidates = [{"z": zz, "votes": vv} for zz, vv in votes.most_common(4)]
                    if enough and v >= 0.6 * total:
                        summary["pen_refused"] = summary.get("pen_refused", 0) + 1
                        if not c.z_note:
                            index_pen = not _is_multiple(z, 5.0 * I)
                            c.z_note = (f"counted {z:g} ft from its neighbours, but its pen draws the "
                                        f"{'index contours' if index_pen else 'lines between the index contours'}: not given")
            if not changed or summary["budget_hit"]:
                break
        for fam in grp["families"]:
            ff = [c for c in contours if c.style in (fam.styles or [fam.style])]
            fam.n_with_z = sum(1 for c in ff if c.z is not None)
            fam.length_with_z = sum(c.length for c in ff if c.z is not None)
    return summary


def contour_capable_styles(chains: list[Chain], min_curvy: int = 3) -> set:
    """Styles that draw contour-like lines: at least `min_curvy` long chains that bend
    (title-block borders, detail frames and lot lines are straight)."""
    counts: Counter = Counter()
    for c in chains:
        if c.length < 60 or len(c.pts) < 3 or c.closed or c.n_sharp > 2:
            continue                      # rectangles and boxes bend at corners, contours bend smoothly
        diag = float(np.hypot(*(c.pts.max(axis=0) - c.pts.min(axis=0))))
        straight = diag / max(c.length, 1e-6)
        # ... or sways: on gentle ground a contour runs nearly straight across the site and still
        # turns by tens of degrees along its way, with no corner anywhere (the hotel site C3: the
        # existing 5 ft contours, 234-744 pt long, 0.95-0.98 of their diagonal, turning 23-41
        # degrees; a rule or a lot line is 1.000 of its diagonal and turns under 2 degrees)
        if straight < 0.9 or (straight < 0.985 and c.n_sharp == 0 and c.max_turn >= 10.0):
            counts[c.style] += 1
    return {st for st, n in counts.items() if n >= min_curvy}


SWATCH_GAP = 40.0        # pt: a swatch is broken by its sample number (18-27 pt measured), never by this much


def read_legend_roles(labels: list[Label], strokes: list[Stroke], styles: list[StyleGroup],
                      glyph_ids: set | None = None, capable: set | None = None,
                      notes: list | None = None, info: dict | None = None) -> dict:
    """Legend rows such as 'EXISTING CONTOURS  — — 930 — —' name the line style of each
    surface: the sample strokes beside the words give the style ids.
    `capable` (styles with contour-like linework) keeps a legend swatch drawn in the
    title-block pen from naming that pen. Returns {style_id: (role, reason)}; `notes` takes a
    sentence for every pen the legend's rows do not agree on; `info` takes how many contour
    rows the legend has ("rows") and how many of them named a pen ("named").

    THE SWATCH IS THE RUN OF STROKES BESIDE THE CAPTION: the strokes in the row's band, walked
    away from the caption, up to the first gap wider than SWATCH_GAP. Everything in the band
    out to 420 pt used to count, and on the hotel site C3 that reached the plan: three 65-pt
    pieces of the black solid pen, 276 pt from the captions, were 150 of the 198 pt "in" the row
    of the EXISTING 1' CONTOUR (whose own swatch, five gray dashes, 47 pt, fell under the 25 %
    share and was refused), so the pen of the PROPOSED contours was called existing and the
    site's cut and fill were taken between the wrong surfaces.

    A PEN TWO ROWS DISAGREE ON IS NAMED BY NEITHER. The first row to name a pen used to keep it
    (setdefault), whatever the rows after it said."""
    from .chains import _style_key
    glyph_ids = glyph_ids or set()
    key_to_id = {}
    for s in styles:
        r, g, b = s.color
        key_to_id[(round(r * 20) / 20, round(g * 20) / 20, round(b * 20) / 20, round(s.width, 1), s.layer)] = s.id
    by_id = {s.id: s for s in styles}
    # Where the drafter names layers as topography, a contour pen is on such a layer, and a
    # swatch drawn on another one is the legend's own drawing (a pen is its colour, its width AND
    # its layer). Subdivision A: the legend is a detail block on C-DTLS; its swatches named the
    # details pen "proposed" on sheet 3.1 (109 lines of details in the proposed surface) and, by
    # the long reach, a storm-channel pen "existing" on sheet 3.0.
    topo_named = {s.id for s in styles if _TOPO_NAMED.search(str(s.layer or "").split("|")[-1])}
    claims: dict = defaultdict(list)             # pen -> [(role, caption, pt from the caption to its swatch)]
    row_pens: dict = {}                          # caption -> the pens its swatch named
    # a legend row is a short caption ("= PROPOSED CONTOUR"), not a note sentence
    rows = [l for l in labels if "CONTOUR" in l.text.upper() and len(l.text.split()) <= 5]
    for lab in rows:
        t = lab.text.upper()
        if any(k in t for k in ("EXIST", "EX.", "ORIGINAL", "TOPO")):
            role = "existing"
        elif any(k in t for k in ("PROP", "FINISH", "DESIGN", "FG", "NEW", "GRADING", "FINAL")):
            role = "proposed"
        else:
            continue
        row_pens[lab.id] = []
        # the row in the caption's own frame (u along the baseline, n across), so a legend printed
        # vertically reads too (the hotel site C3: "= EXISTING 5' CONTOUR" at 90 degrees was skipped
        # and its pen, the 0.25 dashed, was called proposed); a caption that starts with "=" has its
        # swatch on the LEFT ("- - - = EXISTING 5' CONTOUR"), any other on the right
        x0, y0, x1, y1 = lab.bbox
        a = math.radians(lab.angle)
        u = np.array([math.cos(a), math.sin(a)])
        n = np.array([-u[1], u[0]])
        if abs(u[1]) >= 0.3:
            # a legend printed vertically is not read: on the hotel site C3 its rows sit 15 pt apart and
            # the swatch zones overlap, so "= PROPOSED 5' CONTOUR" named the gray dashed EXISTING pen
            # too; the surface heuristics stay in charge there and say so ("screened linework")
            continue
        cc = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
        corners = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]) - cc
        hl = float(np.abs(corners @ u).max())
        hh = float(np.abs(corners @ n).max())
        left = t.strip().startswith("=")
        band = []                                # (from, to) along the row, measured from the caption's edge
        for s in strokes:
            if s.idx in glyph_ids or s.filled or min(s.color) > 0.9:
                continue
            P = s.pts
            d = P.mean(axis=0) - cc
            across = float(d @ n)
            if abs(across) > hh + 5 or float(np.ptp(P @ n)) > 12:
                continue
            al = (P @ u) - float(cc @ u)
            if left:
                al = -al
            a0, a1 = float(al.min()) - hl, float(al.max()) - hl
            # a swatch sits beside its caption (3-50 pt measured on four sheets); 420 pt is how
            # far the FIRST stroke may be, for a legend whose samples stand in a column of their own
            if a0 < 2 or a0 > 420:
                continue
            band.append((a0, a1, s))
        if not band:
            continue
        band.sort(key=lambda b: (b[0], b[1]))
        reach = band[0][1]
        run = []
        for a0, a1, s in band:                   # sorted and walked: a gap test, not a bucket
            if a0 - reach > SWATCH_GAP:
                break
            run.append(s)
            reach = max(reach, a1)
        counts: Counter = Counter()
        pieces: dict = {}
        for s in run:
            k = _style_key(s)
            if k in key_to_id:
                counts[key_to_id[k]] += s.length
                pieces.setdefault(key_to_id[k], []).append(float(s.length))
        if _DBG_LEGEND:
            print(f"[legend] {lab.text.strip()!r} angle {lab.angle:.0f}: swatch = {len(run)} of {len(band)} strokes in its band, "
                  f"{band[0][0]:.0f} to {reach:.0f} pt from the caption; pens {dict((k, round(v)) for k, v in counts.items())}", file=sys.stderr)
        if not counts:
            continue
        total = sum(counts.values())
        for sid, ln in counts.most_common():
            # the sample must share its style with real plan lines (legend swatches are
            # often drawn in their own pen and then say nothing about the plan)
            if not (ln >= 0.25 * total and by_id[sid].cand_length >= 300 and (capable is None or sid in capable)):
                continue
            # and its dash pattern: a swatch drawn as short dashes names a dashed pen only, a
            # continuous swatch a solid one (the hotel site C3: the "EXISTING 1' CONTOUR" row is dashed
            # in the plan's black pen, whose solid lines are the proposed contours)
            ps_ = pieces.get(sid, [])
            swatch_dashed = len(ps_) >= 3 and max(ps_) < 15.0
            if swatch_dashed != bool(by_id[sid].dashed):
                continue
            if ln > 200.0:
                continue                   # a swatch is a short sample; this much ink is the plan itself
            if topo_named and sid not in topo_named:
                if _DBG_LEGEND:
                    print(f"[legend] {lab.text.strip()!r}: style {sid} is on layer {by_id[sid].layer!r}, not a layer named as "
                          f"topography: the legend's own pen, it names nothing", file=sys.stderr)
                continue
            if _DBG_LEGEND:
                print(f"[legend] {lab.text.strip()!r} angle {lab.angle:.0f} -> style {sid} {by_id[sid].color} w{by_id[sid].width} dashed={by_id[sid].dashed} "
                      f"swatch pieces {len(ps_)} max {max(ps_):.0f} pt ({ln:.0f} of {total:.0f} pt)", file=sys.stderr)
            claims[sid].append((role, lab.text.strip(), band[0][0]))
            row_pens[lab.id].append(sid)
    out: dict = {}
    for sid, cl in claims.items():
        roles = {c[0] for c in cl}
        if len(roles) > 1:
            # no honest ranking between two rows of one legend: the pen keeps the surface the
            # linework gives it, and the operator is told which rows to look at
            if notes is not None:
                rows_ = " / ".join(sorted({c[1].lstrip("= ").strip() for c in cl}))
                notes.append(f"the legend names one pen in rows that disagree ({rows_}): not used for that pen "
                             f"(set its surface in the Families card)")
            if _DBG_LEGEND:
                print(f"[legend] style {sid}: rows disagree {cl}: named by neither", file=sys.stderr)
            continue
        cl.sort(key=lambda c: c[2])
        out[sid] = (cl[0][0], f"legend: {cl[0][1]}")
    if info is not None:
        info["rows"] = len(row_pens)
        info["named"] = sum(1 for pens in row_pens.values() if any(sid in out for sid in pens))
        info["roles"] = sorted({r for r, _ in out.values()})
    return out


def mark_legend_samples(labels: list[Label]) -> int:
    """'91.70   = PROPOSED TOP BACK OF CURB' : the number is a legend sample, not a grade."""
    n = 0
    descr = [l for l in labels if l.kind == "other" and l.text.strip().startswith("=")]
    if not descr:
        return 0
    horizontal = lambda l: abs(((l.angle + 180) % 360) - 180) <= 20
    descr = [d for d in descr if horizontal(d)]
    for lab in labels:
        if lab.value is None or lab.kind in ("percent", "station", "scale", "datum"):
            continue
        if not horizontal(lab):
            continue          # a rotated height on the plan is never a legend row (subdivision C: three
                              # vertical "616" / "617" contour labels beside a creek were "samples")
        x0, y0, x1, y1 = lab.bbox
        cy = (y0 + y1) / 2
        for d in descr:
            dx0, dy0, dx1, dy1 = d.bbox
            if dy0 - 4 <= cy <= dy1 + 4 and 0 <= dx0 - x1 <= 60:
                lab.used = False
                lab.z_note = "legend sample"
                n += 1
                break
    return n


def find_table_boxes(labels: list[Label], chains: list[Chain]) -> list:
    """Legend and table blocks without a LEGEND word: three or more short horizontal rules stacked at
    a regular spacing, with word captions on their rows. The hotel site C3: the legend's swatch rows
    at 15-pt spacing (two 30-pt pieces each, a sample number beside, "... 1' CONTOUR" to the right)
    became contours carrying the sample numbers as heights, one of them in the existing surface.
    Parking stripes stack the same way but carry no captions. Returns boxes to exclude from the plan."""
    rows = []
    for ch in chains:
        P = ch.pts
        if len(P) < 2 or not (15.0 <= ch.length <= 120.0):
            continue
        if float(P[:, 1].max() - P[:, 1].min()) > 3.0:
            continue
        rows.append((float(P[:, 1].mean()), float(P[:, 0].min()), float(P[:, 0].max())))
    if len(rows) < 3:
        return []
    rows.sort()
    captions = [l for l in labels if l.kind == "other" and sum(ch.isalpha() for ch in l.text) >= 4
                and abs(((l.angle + 180) % 360) - 180) <= 20]
    boxes = []
    used = [False] * len(rows)
    for i in range(len(rows)):
        if used[i]:
            continue
        group = [rows[i]]
        used[i] = True
        for j in range(i + 1, len(rows)):
            if used[j]:
                continue
            y, x0, x1 = rows[j]
            gy, gx0, gx1 = group[-1]
            overlap = min(x1, gx1) - max(x0, gx0)
            if 4.0 <= y - gy <= 40.0 and overlap >= 0.5 * min(x1 - x0, gx1 - gx0):
                group.append(rows[j])
                used[j] = True
            elif y - gy > 40.0:
                break
        if len(group) < 3:
            continue
        ys = [g[0] for g in group]
        xmin = min(g[1] for g in group)
        xmax = max(g[2] for g in group)
        # a caption ON each row (within 6 pt of the rule's height, beside it): a legend or a table
        # has one per row, a run of parking stripes has one note for all of them (the hotel site:
        # five plan areas with stripes and a nearby note would have lost their spot grades)
        # A caption starts within 80 pt of its rule's end (a legend's sample number may sit
        # between). Subdivision A: with captions taken up to 250 pt away, plan notes ("DRAIN D",
        # "EXISTING DRAIN A") 150-250 pt west of the lot-grading details' rules pulled three boxes
        # into the plan's east margin and silenced 900 pt of contour. The box spans the captioned
        # rows and their captions only; an uncaptioned rule chained into the group by spacing
        # (a note's leader in the plan) is silenced only if it lies inside that box anyway.
        caps = []
        rows_cap = []
        for gy, gx0, gx1 in group:
            mine = [c for c in captions if abs(c.center[1] - gy) <= 6.0
                    and (0.0 <= c.bbox[0] - gx1 <= 80.0 or 0.0 <= gx0 - c.bbox[2] <= 80.0)]
            if mine:
                rows_cap.append((gy, gx0, gx1))
                caps += mine
        if len(rows_cap) < 3 or len(rows_cap) < 0.6 * len(group):
            continue
        cx0 = min([r[1] for r in rows_cap] + [c.bbox[0] for c in caps])
        cx1 = max([r[2] for r in rows_cap] + [c.bbox[2] for c in caps])
        cy0 = min([r[0] for r in rows_cap] + [c.bbox[1] for c in caps])
        cy1 = max([r[0] for r in rows_cap] + [c.bbox[3] for c in caps])
        boxes.append([cx0 - 10, cy0 - 12, cx1 + 10, cy1 + 12])
    # 2. a data table drawn as a GRID: rows of one length stacked at one spacing with the text in
    # the cells (the hotel site: the STORM DRAIN PIPE DATA table, 17 rows of 259 pt every 24 pt, four
    # column rules, 103 words and numbers inside - its frame counted as a 1,391-pt proposed contour
    # at 5994). Its rows are too long for the legend rule above and its captions sit inside, so
    # the tell is the grid itself plus the text: parking stripes stack the same way with nothing
    # written between them, and a profile's grid carries its numbers outside, beside it.
    # A block of lots is a grid too (equal lot lines stacked evenly, a number and a letter in each):
    # a table's rows sit at most 40 pt apart, three or more column rules cross them, and real words
    # (three letters or more) are written inside. Subdivision A: four lot blocks boxed by the
    # first draft of this rule (a fork's boxes-v3/); subdivision B: one.
    longrows = []
    verticals = []
    for ch in chains:
        P = ch.pts
        if len(P) < 2:
            continue
        if 100.0 <= ch.length <= 600.0 and float(P[:, 1].max() - P[:, 1].min()) <= 3.0:
            longrows.append((float(P[:, 1].mean()), float(P[:, 0].min()), float(P[:, 0].max())))
        elif ch.length >= 40.0 and float(P[:, 0].max() - P[:, 0].min()) <= 3.0:
            verticals.append((float(P[:, 0].mean()), float(P[:, 1].min()), float(P[:, 1].max())))
    longrows.sort()
    used = [False] * len(longrows)
    texts = [l for l in labels if l.text and l.text.strip()]
    for i in range(len(longrows)):
        if used[i]:
            continue
        y, x0, x1 = longrows[i]
        grp = [longrows[i]]
        used[i] = True
        for j in range(i + 1, len(longrows)):
            if used[j]:
                continue
            yj, xj0, xj1 = longrows[j]
            if yj - grp[-1][0] > 60.0:
                break
            if abs(xj0 - x0) <= 6.0 and abs(xj1 - x1) <= 6.0 and yj - grp[-1][0] >= 4.0:
                grp.append(longrows[j])
                used[j] = True
        ys = sorted({round(g[0], 1) for g in grp})
        if len(ys) < 4:
            continue
        gaps = [b - a for a, b in zip(ys, ys[1:])]
        if max(gaps) > 1.6 * min(gaps) or max(gaps) > 40.0:
            continue
        # the header row above the first rule and the last row below the last rule belong to the
        # table (the hotel site: the structure table's row 13 with its GRATE / INV elevations sat
        # 12 pt outside the first box and would have stayed a spot grade)
        pad = sorted(gaps)[len(gaps) // 2] + 4.0
        box = [x0 - 10, ys[0] - pad, x1 + 10, ys[-1] + pad]
        span = ys[-1] - ys[0]
        cols = [v for v in verticals if x0 - 3 <= v[0] <= x1 + 3 and v[1] <= ys[0] + 0.1 * span and v[2] >= ys[-1] - 0.1 * span]
        if len(cols) < 3:
            continue
        inside = [l for l in texts if box[0] <= l.center[0] <= box[2] and box[1] <= l.center[1] <= box[3]]
        words = sum(1 for l in inside if sum(c.isalpha() for c in l.text) >= 3)
        if len(inside) < len(ys) or words < 3:
            continue
        # the block is the whole table: the column rules run up into the header and the frame
        # round the rows (its top border 45 pt above the first row on the hotel site's STORM DRAIN
        # PIPE DATA table, which stayed a counted contour outside a box built from the rows alone)
        for v in cols:
            box[1] = min(box[1], v[1] - 4.0)
            box[3] = max(box[3], v[2] + 4.0)
        for ch in chains:
            P = ch.pts
            if len(P) < 3 or ch.length < 2.0 * (x1 - x0):
                continue
            bx0, by0, bx1, by1 = float(P[:, 0].min()), float(P[:, 1].min()), float(P[:, 0].max()), float(P[:, 1].max())
            if bx0 <= x0 + 6 and bx1 >= x1 - 6 and by0 <= ys[0] + 4 and by1 >= ys[-1] - 4 and (bx1 - bx0) <= 1.5 * (x1 - x0)                     and (by1 - by0) <= 2.0 * max(span, 1.0) + 120.0:
                box = [min(box[0], bx0 - 4.0), min(box[1], by0 - 4.0), max(box[2], bx1 + 4.0), max(box[3], by1 + 4.0)]
        boxes.append(box)
    # one block found from several rule groups (the hotel site: the same legend twice) is one box
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]:
                    boxes[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    del boxes[j]
                    merged = True
                    break
            if merged:
                break
    return boxes


def find_profile_boxes(labels: list[Label], chains: list[Chain]) -> list:
    """Profile views: a column of evenly spaced elevation labels (4870, 4880, ...) beside
    a grid of long horizontal lines. Returns boxes to exclude from the plan."""
    nums = [l for l in labels if l.value is not None and l.kind in ("number", "contour", "spot") and abs(((l.angle + 90) % 180) - 90) < 10]
    boxes = []
    used = set()
    by_x: dict[int, list[Label]] = defaultdict(list)
    for l in nums:
        by_x[int(round(l.center[0] / 6.0))].append(l)
    for kx, col in by_x.items():
        col = sorted(col, key=lambda l: l.center[1])
        if len(col) < 5:
            continue
        ys = np.array([l.center[1] for l in col])
        vs = np.array([l.value for l in col])
        dy = np.diff(ys)
        dv = np.diff(vs)
        if len(dy) < 4:
            continue
        # even spacing on the page and constant value step (either direction)
        if np.std(dy) > 0.15 * np.mean(dy) or np.mean(dy) < 8:
            continue
        step = np.median(np.abs(dv))
        if step <= 0 or np.any(np.abs(np.abs(dv) - step) > 0.01 * step + 0.01):
            continue
        # a profile grid steps by a round amount (1, 2, 5, 10 ft) and prints whole numbers; a row of
        # lot pads falling 0.23 ft per lot (subdivision C 3.2: 623.12, 622.59, 622.34, 622.10 ...) is not
        # a profile, and calling it one silenced every pad grade on the island
        if step < 0.5 or abs(step / 0.5 - round(step / 0.5)) > 0.02:
            continue
        if sum(1 for v in vs if abs(v * 2 - round(v * 2)) < 0.01) < 0.8 * len(vs):
            continue
        y0, y1 = float(ys.min()), float(ys.max())
        x_c = float(np.mean([l.center[0] for l in col]))
        # horizontal grid lines at those heights give the box width
        xs = []
        for ch in chains:
            p = ch.pts
            if ch.length < 100:
                continue
            ext_y = p[:, 1].max() - p[:, 1].min()
            if ext_y > 6:
                continue
            ym = float(p[:, 1].mean())
            if y0 - 10 <= ym <= y1 + 10:
                xs += [float(p[:, 0].min()), float(p[:, 0].max())]
        if len(xs) < 6:
            continue
        pad = float(np.mean(dy))
        boxes.append([min(xs) - 40, y0 - pad, max(xs) + 40, y1 + pad])
    # merge overlapping boxes
    out = []
    for b in boxes:
        for o in out:
            if not (b[2] < o[0] or b[0] > o[2] or b[3] < o[1] or b[1] > o[3]):
                o[0], o[1], o[2], o[3] = min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])
                break
        else:
            out.append(list(b))
    return out


def propagate_global(contours: list[Contour], chains: list[Chain], families: list[Family],
                     rounds: int = 6, max_ray: float = 420.0, budget_s: float = 60.0,
                     labels: list | None = None) -> dict:
    """Heights from the rays of EVERY line of a surface, not only from the line that needs one.

    propagate_heights samples the unlabelled line and looks both ways; on a sheet where 45 of 272
    lines carry a printed height (subdivision A) most of those rays never reach a labelled line
    on both sides and the line stays open. But a ray cast from any line crosses the same
    neighbours: when it crosses an unlabelled line BETWEEN two labelled ones and the count agrees
    with their heights (|zb - za| = steps x interval), the line in between is determined; two
    labelled lines in a row on one side of it say which way is uphill. Votes over every such ray
    decide; a determination needs two independent two-sided rays (or four rays in all) and 70 %
    agreement, else the candidates are listed for the operator. Rounds: a determined line is
    evidence for the next."""
    import time as _time
    cmap = {c.id: c for c in chains}
    deadline = _time.time() + budget_s
    summary = {"counted": 0, "candidates": 0, "budget_hit": False}

    def straightness(c):
        cid = max(c.chain_ids, key=lambda k: cmap[k].length)
        p = cmap[cid].pts
        diag = float(np.hypot(*(p.max(axis=0) - p.min(axis=0))))
        return diag / max(cmap[cid].length, 1e-6)

    for grp in role_groups(families):
        I = _group_interval(grp)
        if I is None:
            continue
        fc = [c for c in contours if c.style in grp["styles"]]
        cts = {c.id: c for c in fc}
        countable = [c for c in fc if c.z is not None or not (c.length < 200 and straightness(c) > 0.985)]
        geoms, owners = _family_geoms(countable, cmap)
        if not geoms:
            continue
        tree = STRtree(geoms)
        wanted = {c.id for c in fc if c.length >= 30 and not (c.length < 200 and straightness(c) > 0.985)}
        ranks = pen_ranks(fc, cmap, labels, I, grp["styles"], grp["families"])
        for _ in range(rounds):
            zmap = {c.id: c.z for c in fc}
            if not any(z is not None for z in zmap.values()):
                break
            det: dict[int, Counter] = defaultdict(Counter)
            strong: dict[int, Counter] = defaultdict(Counter)
            basis: dict[int, dict] = defaultdict(lambda: defaultdict(list))
            for c in countable:
                if _time.time() > deadline:
                    summary["budget_hit"] = True
                    break
                for (p, n) in _samples(c, cmap, every=80.0, max_n=8):
                    for sgn in (1, -1):
                        hits = _ray_hits(p, n * sgn, max_ray, tree, geoms, owners, exclude=c.id)
                        seq = [(c.id, zmap.get(c.id))]
                        seen = {c.id}
                        for d, oid in hits:
                            if oid in seen:
                                break          # the ray re-crossed a line: counts past here are unreliable
                            seen.add(oid)
                            seq.append((oid, zmap.get(oid)))
                        known = [i for i, (o, z) in enumerate(seq) if z is not None]
                        if not known:
                            continue
                        for j, (oj, zj) in enumerate(seq):
                            if zj is not None or oj not in wanted:
                                continue
                            before = [i for i in known if i < j]
                            after = [i for i in known if i > j]
                            if before and after:
                                i, k = before[-1], after[0]
                                zi, zk = seq[i][1], seq[k][1]
                                if abs(abs(zk - zi) - (k - i) * I) <= 0.01:
                                    sgn_z = 1 if zk > zi else -1
                                    z = round(zi + sgn_z * (j - i) * I, 3)
                                    det[oj][z] += 2
                                    strong[oj][z] += 1
                                    basis[oj][z].append({"contour": seq[i][0], "k": j - i, "z": zi, "dir": sgn_z})
                                    basis[oj][z].append({"contour": seq[k][0], "k": k - j, "z": zk, "dir": -sgn_z})
                                continue
                            if len(before) >= 2:
                                i1, i2 = before[-2], before[-1]
                                z1, z2 = seq[i1][1], seq[i2][1]
                                if z2 != z1 and abs(abs(z2 - z1) - (i2 - i1) * I) <= 0.01:
                                    sgn_z = 1 if z2 > z1 else -1
                                    z = round(z2 + sgn_z * (j - i2) * I, 3)
                                    det[oj][z] += 1
                                    basis[oj][z].append({"contour": seq[i2][0], "k": j - i2, "z": z2, "dir": sgn_z})
                            elif len(after) >= 2:
                                k1, k2 = after[0], after[1]
                                z1, z2 = seq[k1][1], seq[k2][1]
                                if z2 != z1 and abs(abs(z2 - z1) - (k2 - k1) * I) <= 0.01:
                                    sgn_z = 1 if z2 > z1 else -1      # heights rise going away: nearer is lower
                                    z = round(z1 - sgn_z * (k1 - j) * I, 3)
                                    det[oj][z] += 1
                                    basis[oj][z].append({"contour": seq[k1][0], "k": k1 - j, "z": z1, "dir": -sgn_z})
            changed = 0
            for oid, cnt in det.items():
                c = cts[oid]
                if c.z is not None:
                    continue
                (z, v), = cnt.most_common(1)
                total = sum(cnt.values())
                if v >= 0.7 * total and (strong[oid][z] >= 2 or v >= 4) and pen_allows(c, z, ranks, cmap, I):
                    c.z = z
                    c.how = "counted"
                    c.votes = v
                    c.basis = basis[oid][z][:6]
                    c.candidates = []
                    changed += 1
                    summary["counted"] += 1
                else:
                    c.candidates = [{"z": zz, "votes": vv} for zz, vv in cnt.most_common(4)]
            if summary["budget_hit"] or not changed:
                break
        summary["candidates"] += sum(1 for c in fc if c.z is None and c.candidates)
    return summary


def verify_heights(contours: list[Contour], chains: list[Chain], families: list[Family], labels: list[Label],
                   max_ray: float = 240.0, max_rounds: int = 24, budget_s: float = 60.0) -> dict:
    """Every height must agree with its neighbours: the next line with a height,
    k crossings away, differs by a multiple of the interval no larger than k x I.

    Conflicts are resolved worst-first, one line per round: a wrong line
    disagrees with all of its neighbours, each neighbour disagrees with just
    that one. Counted heights go first (they are derived); a printed label is
    set aside only when it disagrees with two or more neighbours and nothing
    counted is left to blame. Returns a summary."""
    import time as _time
    cmap = {c.id: c for c in chains}
    lab_by_id = {l.id: l for l in labels}
    summary = {"counted_dropped": 0, "labels_flagged": 0, "kept_conflicts": 0, "budget_hit": False}
    deadline = _time.time() + budget_s
    for grp in role_groups(families):
        I = _group_interval(grp)
        if I is None:
            continue
        fc = [c for c in contours if c.style in grp["styles"]]
        cts = {c.id: c for c in fc}

        def _straight(c):
            cid = max(c.chain_ids, key=lambda k: cmap[k].length)
            pp = cmap[cid].pts
            return float(np.hypot(*(pp.max(axis=0) - pp.min(axis=0)))) / max(cmap[cid].length, 1e-6)
        countable = [c for c in fc if c.z is not None or not (c.length < 200 and _straight(c) > 0.985)]
        geoms, owners = _family_geoms(countable, cmap)
        if not geoms:
            continue
        tree = STRtree(geoms)
        for _round in range(max_rounds):
            if _time.time() > deadline:
                summary["budget_hit"] = True
                break
            zmap = {c.id: c.z for c in fc}
            conflicts = {}
            for c in fc:
                if c.z is None:
                    continue
                ok = bad = 0
                for (p, n) in _samples(c, cmap, every=60.0, max_n=8):
                    for sgn in (1, -1):
                        hits = _ray_hits(p, n * sgn, max_ray, tree, geoms, owners, exclude=c.id)
                        seen = []
                        for d, oid in hits:
                            if oid in seen:
                                break         # the ray re-crossed a line: counts past here are unreliable
                            seen.append(oid)
                            z = zmap.get(oid)
                            if z is None:
                                continue
                            k = len(seen)
                            dz = abs(z - c.z)
                            steps = dz / I
                            if abs(steps - round(steps)) < 0.02 and round(steps) <= k:
                                ok += 1
                            else:
                                bad += 1
                            break
                # at least three rays must disagree: on a sheet drawn in pieces one ray slips
                # through a gap and mis-counts, and that alone must not throw a height out
                if ok + bad >= 2 and bad > ok and bad >= 3:
                    conflicts[c.id] = (ok, bad)
                # A single printed label whose rays reach no line with a height can still be
                # impossible: a ray that crosses k lines before the first height cannot change height
                # by more than k intervals (plus a margin for lines the reading lacks). Long rays,
                # for those lines only (subdivision C: a "646" read from a "616").
                if c.how == "label" and c.votes <= 1 and ok + bad == 0:
                    impossible = 0
                    for (p, n) in _samples(c, cmap, every=60.0, max_n=8):
                        for sgn in (1, -1):
                            seen = []
                            for d, oid in _ray_hits(p, n * sgn, max_ray * 4, tree, geoms, owners, exclude=c.id):
                                if oid in seen:
                                    break
                                seen.append(oid)
                                z = zmap.get(oid)
                                if z is None:
                                    continue
                                if abs(z - c.z) / I > len(seen) + 3:
                                    impossible += 1
                                break
                    if impossible >= 2:
                        conflicts[c.id] = (0, impossible)
            # a height the operator typed or drew stands: say that it disagrees, keep it
            for cid in [k for k, v in conflicts.items() if cts[k].how in OPERATOR_HOWS]:
                ok, bad = conflicts.pop(cid)
                note = f"disagrees with {bad} neighbouring line(s); kept because you set it"
                if cts[cid].z_note != note:
                    cts[cid].z_note = note
                    summary["kept_conflicts"] += 1
            if not conflicts:
                break
            counted = [(bad - ok, bad, cid) for cid, (ok, bad) in conflicts.items() if cts[cid].how == "counted"]
            if counted:
                counted.sort(reverse=True)
                worst = counted[0][0]
                for score, bad, cid in counted:
                    if score < worst:
                        break
                    c = cts[cid]
                    c.candidates = [{"z": c.z, "votes": 0, "rays_ok": conflicts[cid][0], "rays_bad": bad,
                                     "note": f"counted height disagreed with {bad} neighbour(s)"}]
                    c.z = None
                    c.how = "conflict"
                    summary["counted_dropped"] += 1
                continue
            labelled = [(bad - ok, bad, cid, cts[cid].votes) for cid, (ok, bad) in conflicts.items()
                        if cts[cid].how == "label" and bad >= 2 and bad >= 2 * ok]
            # A line carrying three or more agreeing printed labels is what the drafter meant: it
            # stands, and names the disagreement so the neighbours get checked. Subdivision C:
            # a 617 line with seven labels was thrown out by nine rays that a wrong interval had
            # made disagree, and the operator was asked to type the height printed seven times.
            for score, bad, cid, votes in labelled:
                if votes < 3:
                    continue
                c = cts[cid]
                note = f"{votes} printed labels say {c.z:g} ft; {bad} ray(s) to neighbouring lines disagree - check those lines"
                if c.z_note != note:
                    c.z_note = note
                    summary["kept_conflicts"] += 1
            labelled = [t for t in labelled if t[3] < 3]
            if not labelled:
                break
            # the line with the fewest labels loses first (a single label is a misread or a label
            # attached to the wrong line more often than two agreeing ones), then the worst
            labelled.sort(key=lambda t: (t[3], -t[0], -t[1]))
            score, bad, cid, votes = labelled[0]
            c = cts[cid]
            c.candidates = [{"z": c.z, "votes": votes, "rays_ok": conflicts[cid][0], "rays_bad": bad,
                             "note": f"printed height disagreed with {bad} neighbour(s)"}]
            for lid in c.label_ids:
                if lid in lab_by_id:
                    lab_by_id[lid].z_note = "disagrees with the neighbouring contours"
            c.z = None
            c.how = "conflict"
            summary["labels_flagged"] += 1
        # every piece of a joined line must fit its own neighbours (a wrong join shows up on the
        # piece that does not): after the line-level rounds, so that lines set aside above no
        # longer vote, pieces that do not fit are split off and left for the second count
        fc = [c for c in contours if c.style in grp["styles"] and c.chain_ids]
        countable = [c for c in fc if c.z is not None or not (c.length < 200 and _straight(c) > 0.985)]
        geoms, owners = _family_geoms(countable, cmap)
        if geoms:
            split = _split_disagreeing_pieces(fc, cmap, I, STRtree(geoms), geoms, owners, max_ray, lab_by_id,
                                              next_id=max((c.id for c in contours), default=0) + 1)
            if split:
                contours.extend(split)
                summary["pieces_split"] = summary.get("pieces_split", 0) + len(split)
                summary["pieces_split_long"] = summary.get("pieces_split_long", 0) + sum(1 for c in split if c.length > 300)
    return summary


def find_legend_boxes(labels: list[Label], strokes: list[Stroke], page_w: float, page_h: float) -> list:
    """Rectangles that frame a LEGEND block: labels inside are samples, not site data."""
    words = [l for l in labels if l.text.strip().upper().rstrip(":") in ("LEGEND", "KEY", "SYMBOLS")]
    if not words:
        return []
    rects = []
    for s in strokes:
        if not s.closed or len(s.pts) < 4:
            continue
        x0, y0 = s.pts.min(axis=0)
        x1, y1 = s.pts.max(axis=0)
        w, h = x1 - x0, y1 - y0
        if w < 40 or h < 30 or w * h > 0.12 * page_w * page_h:
            continue
        # axis-aligned rectangle check: all points on the bbox edges
        on_edge = np.sum((np.abs(s.pts[:, 0] - x0) < 1.5) | (np.abs(s.pts[:, 0] - x1) < 1.5) |
                         (np.abs(s.pts[:, 1] - y0) < 1.5) | (np.abs(s.pts[:, 1] - y1) < 1.5))
        if on_edge < len(s.pts) * 0.9:
            continue
        rects.append((x0, y0, x1, y1, w * h))
    out = []
    for wd in words:
        cx, cy = wd.center
        inside = [r for r in rects if r[0] - 2 <= cx <= r[2] + 2 and r[1] - 2 <= cy <= r[3] + 2]
        if inside:
            r = min(inside, key=lambda r: r[4])
            out.append([float(r[0]), float(r[1]), float(r[2]), float(r[3])])
    return out


# ---------------------------------------------------------------------------
def pad_key(center, z: float) -> str:
    """A pad's name in the operator's records: where its floor height is printed and what it
    says (label ids renumber from one reading to the next; this does not)."""
    return f"{int(round(center[0]))}_{int(round(center[1]))}_{float(z):.2f}"


def find_floor_pads(labels: list[Label], chains: list[Chain], page_w: float, page_h: float,
                    min_area: float = 2000.0, max_share: float = 0.25) -> list[dict]:
    """PADS THE SHEET PRINTS: a finished-floor height ("FFE=5993.00") inside a closed outline is
    a building whose floor the sheet gives. The outline is the SMALLEST closed line round the
    label (at least `min_area` pt2, at most `max_share` of the sheet: a border or a property
    line is no building). Nothing is done with a pad here: it is an OFFER the operator accepts
    per pad (project.surfaces_for), because the same words mean another thing on another sheet -
    on a subdivision plan "FF=662.5" is the lowest floor allowed for a future house and the
    closed line round it is the lot. The hotel site C3: the floor height was read and used for
    nothing; with the building's outline at 5993.00 the site reads cut 616 / fill 2,237 CY
    against 730 / 1,695 without it (the sheet's own table: 562 / 2,723).
    Returns [{key, label_id, text, z, chain, polygon, area_pt2}]; `polygon` None = no closed
    outline round the label (the pad is listed, it cannot be used)."""
    floors = [l for l in labels if l.kind == "floor" and l.z is not None and l.used]
    if not floors:
        return []
    closed = []
    for c in chains:
        if len(c.pts) < 4:
            continue
        if not c.closed and float(np.hypot(*(c.pts[0] - c.pts[-1]))) > 6.0:
            continue
        try:
            pg = Polygon(c.pts)
            if not pg.is_valid:
                pg = pg.buffer(0)
            if pg.geom_type != "Polygon" or pg.is_empty:
                continue
        except Exception:                # noqa: BLE001 - a degenerate ring is no outline
            continue
        if min_area <= pg.area <= max_share * page_w * page_h:
            closed.append((pg.area, c.id, pg))
    closed.sort(key=lambda t: (t[0], t[1]))
    out = []
    for lab in floors:
        pt = shapely.points(lab.center)
        hit = next(((a, cid, pg) for a, cid, pg in closed if pg.contains(pt)), None)
        rec = {"key": pad_key(lab.center, lab.z), "label_id": lab.id, "text": lab.text, "z": float(lab.z),
               "at": [round(float(lab.center[0]), 1), round(float(lab.center[1]), 1)],
               "chain": None, "polygon": None, "area_pt2": None}
        if hit is not None:
            a, cid, pg = hit
            rec.update(chain=cid, area_pt2=round(float(a), 1),
                       polygon=[[round(float(x), 2), round(float(y), 2)] for x, y in pg.exterior.coords][:-1])
        out.append(rec)
    return out


def pad_points(polygon: list, step: float = 12.0) -> np.ndarray:
    """The points a pad stands on: its outline - every corner, and every `step` pt along each
    side - and a grid of `step` inside it. The corners are points of their own: an outline
    resampled as one line skips them (geom.resample walks by even steps), and the surface 1 pt
    inside a corner then read 103.98 ft on a 104.00 ft pad."""
    pg = Polygon(polygon)
    if not pg.is_valid:
        pg = pg.buffer(0)
    if pg.geom_type != "Polygon":
        pg = max(getattr(pg, "geoms", [pg]), key=lambda g: g.area)
    ext = np.asarray(pg.exterior.coords, dtype=float)
    if len(ext) > 2000:
        ext = np.asarray(pg.simplify(0.5).exterior.coords, dtype=float)
    ring = []
    for a, b in zip(ext[:-1], ext[1:]):
        n = max(1, int(math.ceil(float(np.hypot(*(b - a))) / step)))
        ring.extend(a + (b - a) * (k / n) for k in range(n))
    ring = np.asarray(ring, dtype=float).reshape(-1, 2)
    x0, y0, x1, y1 = pg.bounds
    xs = np.arange(x0 + step / 2.0, x1, step)
    ys = np.arange(y0 + step / 2.0, y1, step)
    inner = pg.buffer(-step / 3.0)
    grid = [(float(x), float(y)) for y in ys for x in xs if inner.contains(shapely.points((float(x), float(y))))]
    return np.vstack([ring[:, :2], np.asarray(grid, dtype=float).reshape(-1, 2)])


def place_spots(labels: list[Label], strokes: list[Stroke], page_default: str = "proposed",
                gray_is_existing: float = 0.45) -> list[Spot]:
    """Spot grades with a position and a side (which surface they belong to)."""
    short = [s for s in strokes if s.length <= 80 and not s.filled and min(s.color) < 0.9]
    C = np.array([[0.5 * (s.pts[:, 0].min() + s.pts[:, 0].max()), 0.5 * (s.pts[:, 1].min() + s.pts[:, 1].max())] for s in short]) if short else np.zeros((0, 2))
    from scipy.spatial import cKDTree
    tree = cKDTree(C) if len(C) else None
    # A LEADER ENDS AT ITS POINT; A DASH GOES ON. A short stroke that starts at the text and whose
    # far end is followed by the next stroke of its own pen - ahead of it on its own line, after
    # a gap of 0.5 to 15 pt - is a dash of a dashed line passing the text, not a leader. Subdivision
    # D (C3.0): the lot-corner grades are printed at their corners, beside the property
    # lines (a 41-pt dash, two dots, the next dash); 57 of 135 took the first dash for a leader
    # and stood at its far end, up to 44 pt (31 ft) into the lot. What stands AT a leader's end
    # is its mark (subdivision C: a cross of two 8.6-pt strokes, an arrow head): a stroke
    # across the end, or behind it, is no next dash.
    ends_l, owner = [], []
    for s in strokes:
        if s.filled or len(s.pts) < 2 or s.length < 1.0:
            continue
        ends_l += [s.pts[0], s.pts[-1]]
        owner += [(s, 0), (s, 1)]
    ends_a = np.asarray(ends_l, dtype=float).reshape(-1, 2)
    etree = cKDTree(ends_a) if len(ends_a) else None

    def crossed(t) -> bool:
        """Is the stroke one arm of a cross: another stroke of its pen and about its length lies
        across it, middle on middle?"""
        a, b = t.pts[0], t.pts[-1]
        m = 0.5 * (a + b)
        v = b - a
        Lt = float(np.hypot(*v))
        if Lt < 1e-6:
            return False
        v = v / Lt
        for k in etree.query_ball_point(m, Lt):
            x, _e = owner[k]
            if (x is t or x.layer != t.layer or abs(x.width - t.width) > 0.05 or not (0.5 * Lt <= x.length <= 2.0 * Lt)
                    or any(abs(p - q) > 0.05 for p, q in zip(x.color, t.color))):
                continue
            w = x.pts[-1] - x.pts[0]
            Lx = float(np.hypot(*w))
            if Lx < 1e-6 or float(np.hypot(*(0.5 * (x.pts[0] + x.pts[-1]) - m))) > 0.2 * Lt:
                continue
            if abs(float(v @ (w / Lx))) <= 0.87:          # 30 degrees off its direction, or more
                return True
        return False

    def goes_on(s, far_is_last: bool) -> bool:
        far = s.pts[-1] if far_is_last else s.pts[0]
        d = (s.pts[-1] - s.pts[-2]) if far_is_last else (s.pts[0] - s.pts[1])
        L = float(np.hypot(*d))
        if L < 1e-6 or etree is None:
            return False
        d = d / L
        for k in etree.query_ball_point(far, 15.0):
            t, e = owner[k]
            if (t is s or t.layer != s.layer or abs(t.width - s.width) > 0.05
                    or any(abs(a - b) > 0.05 for a, b in zip(t.color, s.color))):
                continue
            g = ends_a[k] - far                                  # its nearer end: ahead, on the line
            o = (t.pts[-1] if e == 0 else t.pts[0]) - far        # its other end: further ahead, on the line
            ga, oa = float(g @ d), float(o @ d)
            if not (0.5 <= ga <= 15.0) or oa <= ga:
                continue
            if abs(float(g[0] * d[1] - g[1] * d[0])) > 0.7 or abs(float(o[0] * d[1] - o[1] * d[0])) > 0.7 + 0.03 * oa:
                continue
            # THE MARK AT A LEADER'S END MAY LIE ALONG THE LEADER. Subdivision C: every
            # leader ends at a cross of two 8.6-pt strokes, and the arm of the cross that runs
            # on from the leader (it starts a point beyond the leader's end, in the leader's
            # pen) was "the next dash": 32 spot grades of sheets 3.1 / 3.2 left their cross and
            # stood at their text, 15 to 21 ft away (found by the third critic). A dash of a
            # dashed line is not crossed, middle on middle, by another stroke of its length.
            if crossed(t):
                continue
            return True
        return False
    spots: list[Spot] = []
    sid = 0
    for lab in labels:
        if lab.kind not in ("spot", "wall", "floor", "utility", "footing", "number") or lab.z is None or not lab.used:
            continue
        if lab.kind == "number" and lab.chain_ids:
            continue
        if lab.kind == "number" and lab.source != "text":
            continue          # a bare OCR integer that touches no line is too weak to be a spot
        x0, y0, x1, y1 = lab.bbox
        cx, cy = lab.center
        anchor = "text"
        ax, ay = cx, cy
        if tree is not None:
            near = tree.query_ball_point([cx, cy], 0.5 * (x1 - x0) + 16)
            best_dot = None
            best_leader = None
            for i in near:
                s = short[i]
                ex = s.pts[:, 0].max() - s.pts[:, 0].min()
                ey = s.pts[:, 1].max() - s.pts[:, 1].min()
                dx = 0.0 if x0 <= C[i][0] <= x1 else min(abs(C[i][0] - x0), abs(C[i][0] - x1))
                dy = 0.0 if y0 <= C[i][1] <= y1 else min(abs(C[i][1] - y0), abs(C[i][1] - y1))
                dist = math.hypot(dx, dy)
                if s.closed and max(ex, ey) <= 5 and dist <= 6:
                    if best_dot is None or dist < best_dot[0]:
                        best_dot = (dist, C[i])
                elif not s.closed and 6 <= s.length <= 80 and max(ex, ey) >= 5:
                    p0, p1 = s.pts[0], s.pts[-1]
                    d0 = _box_dist(p0, lab.bbox)
                    d1 = _box_dist(p1, lab.bbox)
                    if min(d0, d1) <= 4 and max(d0, d1) >= 6:
                        far = p1 if d0 < d1 else p0
                        if goes_on(s, far_is_last=d0 < d1):
                            continue
                        if best_leader is None or min(d0, d1) < best_leader[0]:
                            best_leader = (min(d0, d1), far)
            if best_dot is not None:
                ax, ay = float(best_dot[1][0]), float(best_dot[1][1])
                anchor = "dot"
            elif best_leader is not None:
                ax, ay = float(best_leader[1][0]), float(best_leader[1][1])
                anchor = "leader"
        side, why = spot_side(lab, page_default, gray_is_existing)
        spots.append(Spot(id=sid, label_id=lab.id, x=ax, y=ay, z=float(lab.z), side=side, tag=lab.tag,
                          anchor=anchor, side_reason=why))
        sid += 1
    return spots


def screened_heights_as_points(spots: list, labels: list[Label], families: list[Family], contours: list[Contour],
                               boxes: list | None = None, min_conf: float = 85.0) -> list:
    """A SCREENED CONTOUR HEIGHT WITH NO LINE UNDER IT IS A POINT OF THE EXISTING GROUND.
    A sheet may clip its existing contours away inside the graded area and leave their labels
    standing: the height is printed where the line ran. Subdivision D (C3.0 / C3.1): east of
    one street no existing contour is drawn inside the lots and streets, the screened 653 ..
    666 stand alone, and the existing surface there was stretched from lines 300 ft away.
    Only on a sheet that prints its existing heights screened (an existing pen with three
    heights or more, three in four of them screened), only whole numbers on no line, read
    with confidence, inside the range of the existing pens' own heights (5 ft either side),
    outside the legend and the tables. Appended to `spots` (side "existing"); returns them."""
    lab = {l.id: l for l in labels}
    ex_styles = {s for f in families if f.role == "existing" for s in (f.styles or [f.style])}
    ex_labs = [lab[i] for c in contours if c.style in ex_styles for i in c.label_ids if i in lab]
    if len(ex_labs) < 3 or sum(1 for l in ex_labs if l.gray >= SCREENED_INK) < 0.75 * len(ex_labs):
        return []
    zs = [c.z for c in contours if c.style in ex_styles and c.z is not None and c.how != "legend"]
    if not zs:
        return []
    lo, hi = min(zs) - 5.0, max(zs) + 5.0
    have = {sp.label_id for sp in spots}
    out = []
    for l in labels:
        # (kind "contour" with no line: a number the attachment let go, or one that stands beside a line)
        if (l.id in have or l.kind not in ("number", "contour") or l.chain_ids or l.tag or not l.used or l.z is None
                or l.value is None):
            continue
        if l.gray < SCREENED_INK or not float(l.value).is_integer() or not (lo <= l.z <= hi):
            continue
        if l.source != "text" and l.conf < min_conf:
            continue
        if boxes and any(_inside(l.center, b) for b in boxes):
            continue
        sp = Spot(id=len(spots), label_id=l.id, x=float(l.center[0]), y=float(l.center[1]), z=float(l.z), side="existing",
                  tag=None, anchor="text", side_reason="a screened contour height on no line: the existing ground where it is printed")
        spots.append(sp)
        out.append(sp)
    return out


def _box_dist(p, bbox) -> float:
    x0, y0, x1, y1 = bbox
    dx = 0.0 if x0 <= p[0] <= x1 else min(abs(p[0] - x0), abs(p[0] - x1))
    dy = 0.0 if y0 <= p[1] <= y1 else min(abs(p[1] - y0), abs(p[1] - y1))
    return math.hypot(dx, dy)


def spot_side(lab: Label, page_default: str, gray_is_existing: float) -> tuple[str, str]:
    if lab.side in ("wall", "floor", "utility", "footing"):
        return lab.side, f"tag {lab.tag}"
    if lab.side == "existing":
        return "existing", f"tag {lab.tag}"
    if lab.gray >= gray_is_existing and lab.source != "text":
        return "existing", "printed in gray"
    if lab.side == "proposed":
        return "proposed", f"tag {lab.tag}"
    return "default", "untagged"
