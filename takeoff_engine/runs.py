"""Pipe RUNS: a utility family split at its structures into the pieces an
excavator bids - manhole to manhole, inlet to inlet, end to structure - with
a name, a pipe size read off the sheet, and the structures counted.

WHY. A client (call 2026-09-03) prices sewer manhole to manhole and by pipe
size; the trench mockup (design-mockups/2026-09-25/8-multitrade-steel/
73-trenching.png) lists "SS-1  MH-1 to MH-3 · 412 LF" and counts the
manholes. The detector gives one chain per stroke run and breaks it where
the drafter did (at every symbol and every crossing), which is neither a
run nor a system; this groups the pieces back into runs.

HOW (measured on Res-A civil C4.0, 2026-09-26, against the client's own
Bluebeam runs and his 16 count marks):
  1. Every chain of the picked families is a polyline. Their ENDS are
     snapped together (SNAP_FT of plan) into nodes; an end lying on another
     chain's body splits that chain there (a tee).
  2. STRUCTURES are where the sheet says they are:
       * a leader CALLOUT whose text names one (AREA INLET, RAINCHAIN INLET,
         24in Nyloplast Drainage Structure, MANHOLE, CLEANOUT, CATCH BASIN,
         TRENCH DRAIN) - its tip marks the structure. On Res-A C4.0 all 16 of
         the client's count marks sit on such callouts; the inlet symbols
         themselves are open strokes inside the paving hatch;
       * a small CLOSED symbol (manhole circle, inlet box, at most SYM_FT
         across) on a family whose name says structure (INLET / MH /
         STRUCT / CB ...). Any small closed shape near a run was tried first
         and counted pavers and tree symbols.
     A structure within SYM_REACH_FT of a run's line splits it there.
  3. A RUN runs structure to structure. At a junction with no structure
     (an INSTALL WYE), the two edges that continue straightest through it
     are one run and the third is a lateral that ends there - the client's
     282-ft run passes three wyes. A bend (degree 2) never breaks a run.
  4. NAMES: a structure takes its callout's own words ("AREA INLET #3"),
     else a printed name beside it (MH-3, SSMH-2, CB-4), else MH-n in
     reading order; a run takes a printed SS-1 / STM-2 beside it, else the
     system code from the family's name (STM, SS, W, G, E, RUN) numbered
     longest first.
  5. PIPE SIZE per run: inline codes on the run's own chains (8"SS), a
     leader callout pointing at it, a size printed beside the run with a
     material or system after it (8" PVC - a bare 2" beside a storm line
     was the water service), the family's name (SE-P-UT-WATER-10'') - in
     that order; the source is reported, and a run whose pieces print two
     sizes is MIXED, never averaged.

Everything here is a pure function of the result's chains (page pt), the
page's text lines and the Sheet-callouts rows: no PDF is opened, nothing
is cached, and nothing on the detection path calls it (regress cannot
move).
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

import numpy as np

from .pipesize import INLINE, LEADER, size_token

SNAP_FT = 1.0          # two chain ends this close (in plan feet) are one node
SNAP_MIN_PT = 4.0      # ...and never closer than this many points
SYM_FT = 4.0           # a structure symbol is at most this wide in plan feet
SYM_MIN_PT = 2.0       # ...and at least this wide on paper (glyph noise is smaller)
SYM_REACH_FT = 3.0     # a structure this close to a run's line sits on it
NAME_REACH_PT = 36.0   # a printed name this close to a structure names it
LABEL_REACH_PT = 90.0  # a structure label with no leader stroke marks the nearest
                       # run point within this (marked approximate)
THROUGH_DEG = 60.0     # a run continues through a junction when the straightest
                       # pair deflects no more than this

# STORM before SEWER on purpose: "PROPOSED STORM SEWER" is storm.
_SYS = [
    ("STM", re.compile(r"(?i)STORM|\bSD\b|\bSTM\b|\bRCP\b|CULVERT|DRAIN")),
    ("SS", re.compile(r"(?i)SANITARY|SEWER|SSWR|\bSS\b|\bSAN\b|FORCE ?MAIN|\bFM\b")),
    ("W", re.compile(r"(?i)WATER|WATR|\bWM\b|\bWL\b|DOMESTIC|POTABLE|FIRE ?LINE|IRRIG")),
    ("G", re.compile(r"(?i)\bGAS\b|PROPANE")),
    ("E", re.compile(r"(?i)ELEC|\bUGE\b|\bUE\b|CONDUIT|POWER|TELE|COMM|FIBER|CATV")),
]
# printed names are searched inside a text LINE ("SSMH-2 RIM=5281.4"), so
# the patterns are bounded, not anchored
_STRUCT_NAME = re.compile(r"\b(SSMH|STMH|SDMH|MH|CB|CO|DI|JB|INLET|STR)[- ]?(\d{1,3}[A-Z]?)\b", re.I)
_RUN_NAME = re.compile(r"\b(SS|STM|SD|WL|WM|FM|RUN|LINE|W|G|E)-(\d{1,3}[A-Z]?)\b", re.I)
# a size printed beside a run names the run only with a material or a system
# after it: a bare 2" within reach of a storm line was the water service
_SIZE_WORD = re.compile(r'(?<![\d.])(\d{1,2})\s*(?:"|”|\'\')\s*(HDPE|PVC|RCP|DIP|CMP|DI|STEEL|SDR|C900|SS|SD|W|FM|PE|CPP|ADS)\b', re.I)
# a closed symbol is a structure only on a UTILITY family named for one: a
# landscape layer "BG-SWCR-CB-MATERIALS|BG-L-Paver" is not a catch basin
# (the critic found the pavers and boulders counted as MH-2..MH-5)
_STRUCT_LAYER = re.compile(r"(?i)(?=.*(?:\bUT\b|STORM|STRM|SEWER|SANITARY|\bSS\b|\bSD\b|WATER|DRAIN|\bSAN\b))"
                           r".*(?:INLET|MANHOLE|\bMH\b|STRUCT|CATCH ?BASIN|CLEANOUT|\bCB\b|\bCO\b)")
# a callout that names a structure at its tip
_STRUCT_TEXT = re.compile(r"(?i)\bINLET\b|MANHOLE|\bMH\b|DRAINAGE STRUCTURE|\bSTRUCTURE\b|CLEANOUT|"
                          r"CLEAN OUT|CATCH BASIN|\bCB\b|JUNCTION BOX|TRENCH DRAIN|\bSSMH\b|\bSTMH\b")
# ...but not a note about pipe, or a connection to something else
_NOT_STRUCT_TEXT = re.compile(r"(?i)CONNECTION|CONNECT TO|SEE |RE\. |DAYLIGHT|PIPE\b(?!.*INLET)")
_NAME_STOP = re.compile(r"(?i)\b(STA|RIM|INV|IE|ELEV|EL|FL|CONNECT|INSTALL|SEE|RE)\b|[=:]")


def system_code(label: str, layer: str = "") -> str:
    text = f"{label or ''} {layer or ''}"
    for code, rx in _SYS:
        if rx.search(text):
            return code
    return "RUN"


def _poly_len(pts) -> float:
    p = np.asarray(pts, float)
    if len(p) < 2:
        return 0.0
    d = np.diff(p, axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def _dist_to_poly(g: np.ndarray, x: float, y: float) -> float:
    """Distance from (x, y) to the polyline g (n, 2): to its SEGMENTS, not
    its vertices - a two-vertex run 200 pt long has no vertex near a name
    printed beside its middle."""
    if len(g) == 0:
        return 1e9
    if len(g) == 1:
        return float(math.hypot(g[0][0] - x, g[0][1] - y))
    a = g[:-1]
    b = g[1:]
    d = b - a
    L2 = (d * d).sum(axis=1)
    safe = np.where(L2 > 0, L2, 1.0)
    t = np.clip(((x - a[:, 0]) * d[:, 0] + (y - a[:, 1]) * d[:, 1]) / safe, 0.0, 1.0)
    q = a + t[:, None] * d
    return float(np.hypot(q[:, 0] - x, q[:, 1] - y).min())


def _project(px, py, a, b):
    """(t, distance) of the foot of (px,py) on segment a-b, t clamped."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    qx, qy = ax + t * dx, ay + t * dy
    return t, math.hypot(px - qx, py - qy)


def _callout_name(text: str) -> str:
    """'AREA INLET INV:7725.16' -> 'AREA INLET'; '24in Nyloplast Drainage
    Structure STA:2+59.57 RIM...' -> '24IN NYLOPLAST DRAINAGE STRUCTURE'."""
    t = str(text or "")
    m = _NAME_STOP.search(t)
    if m:
        t = t[:m.start()]
    t = " ".join(t.split()).strip(" -,.")
    return t.upper()[:34] or "STRUCTURE"


def _near_box(x, y, box, pad):
    x0, y0, x1, y1 = box
    return (x0 - pad <= x <= x1 + pad) and (y0 - pad <= y <= y1 + pad)


def find_structures(chains, styles, ft_per_pt: float, run_style_ids=None, callouts=None,
                    words=None, fam=None) -> list:
    """Structures on the sheet, from the labels that name them and from
    closed symbols (see HOW).

    A label ("AREA INLET" / "RAINCHAIN INLET" / "24in Nyloplast Drainage
    Structure") marks its structure through the LEADER stroke drawn from
    the label to it: any chain with one end at the label box and the other
    end on a run of the picked families. The detector does not class these
    strokes as leaders (on Res-A C4.0 its 18 "leader" chains are the north
    arrow's ticks), so they are found from the label side here. With no
    such stroke, the nearest point of a run within LABEL_REACH_PT of the
    label is taken and the structure is marked approximate.

    Returns [{id, x, y, r, source, name?, text?, approx?, chain_id?,
    style_id?, layer}] in reading order (rows top to bottom, left to right).
    """
    sym_max = max(SYM_FT / ft_per_pt, 6.0) if ft_per_pt else 40.0
    reach = max(SNAP_MIN_PT, SYM_REACH_FT / ft_per_pt) if ft_per_pt else 12.0
    st_by = {s.get("style_id"): s for s in styles or []}
    out = []
    fam = fam or []
    fam_pts = np.asarray([p for ch in fam for p in ch.get("points") or []], float) if fam else np.zeros((0, 2))

    def dist_to_fam(x, y):
        if not len(fam_pts):
            return 1e9
        best = 1e9
        for ch in fam:
            pts = ch.get("points") or []
            for i in range(1, len(pts)):
                _, d = _project(x, y, pts[i - 1], pts[i])
                if d < best:
                    best = d
        return best

    def fam_foot(x, y):
        best, bq = 1e9, None
        for ch in fam:
            pts = ch.get("points") or []
            for i in range(1, len(pts)):
                t, d = _project(x, y, pts[i - 1], pts[i])
                if d < best:
                    a, b = pts[i - 1], pts[i]
                    best, bq = d, (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        return best, bq
    labels = []
    for w in words or []:
        try:
            box = (float(w[0]), float(w[1]), float(w[2]), float(w[3]))
            txt = str(w[4]).strip()
        except (TypeError, ValueError, IndexError):
            continue
        if _STRUCT_TEXT.search(txt) and not _NOT_STRUCT_TEXT.search(txt):
            labels.append((box, txt))
    for box, txt in labels:
        best = None                # (rank, dist, tip)
        for ch in chains or []:
            pts = ch.get("points") or []
            if len(pts) < 2:
                continue
            L = float(ch.get("length_pt") or _poly_len(pts))
            if L < 8.0 or L > 400.0:
                continue
            a, b = pts[0], pts[-1]
            for near, tip in ((a, b), (b, a)):
                if not _near_box(near[0], near[1], box, 14.0):
                    continue
                if _near_box(tip[0], tip[1], box, 6.0):
                    continue                     # both ends at the label: underline
                d = dist_to_fam(tip[0], tip[1])
                if d <= reach:
                    cand = (0, d, tip)
                elif d <= 3 * reach:
                    cand = (1, d, tip)
                else:
                    continue
                if best is None or cand[:2] < best[:2]:
                    best = cand
        approx = False
        if best is None:
            d, q = fam_foot((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            if q is None or d > LABEL_REACH_PT:
                continue
            tip, approx = q, True
        else:
            tip = best[2]
        x, y = float(tip[0]), float(tip[1])
        if any(math.hypot(o["x"] - x, o["y"] - y) <= sym_max for o in out):
            continue
        out.append({"x": round(x, 2), "y": round(y, 2), "r": 0.0, "source": "label",
                    "text": txt[:80], "name": _callout_name(txt), "approx": approx,
                    "chain_id": None, "style_id": None, "layer": ""})
    for c in callouts or []:
        txt = str(c.get("text") or "")
        if not _STRUCT_TEXT.search(txt) or _NOT_STRUCT_TEXT.search(txt):
            continue
        try:
            gx, gy = float(c.get("gx")), float(c.get("gy"))
        except (TypeError, ValueError):
            continue
        if any(math.hypot(o["x"] - gx, o["y"] - gy) <= sym_max for o in out):
            continue
        if fam and dist_to_fam(gx, gy) > reach:
            continue
        out.append({"x": round(gx, 2), "y": round(gy, 2), "r": 0.0, "source": "callout",
                    "text": txt[:80], "name": _callout_name(txt),
                    "chain_id": c.get("leader_chain_id"), "style_id": None, "layer": ""})
    for ch in chains or []:
        if ch.get("dash_signature") == "leader":
            continue
        st = st_by.get(ch.get("style_id")) or {}
        name = f"{st.get('suggested_label') or ''} {st.get('layer') or ''}"
        if not _STRUCT_LAYER.search(name):
            continue
        if run_style_ids and ch.get("style_id") in run_style_ids:
            continue
        pts = ch.get("points") or []
        if len(pts) < 4:
            continue
        p = np.asarray(pts, float)
        w = float(p[:, 0].max() - p[:, 0].min())
        h = float(p[:, 1].max() - p[:, 1].min())
        if max(w, h) > sym_max or max(w, h) < SYM_MIN_PT:
            continue
        if math.hypot(p[0, 0] - p[-1, 0], p[0, 1] - p[-1, 1]) > max(1.5, 0.15 * max(w, h)):
            continue
        cx, cy = float(p[:, 0].mean()), float(p[:, 1].mean())
        # the same structure drawn AND called out: keep the callout
        if any(math.hypot(o["x"] - cx, o["y"] - cy) <= sym_max for o in out):
            continue
        out.append({"x": round(cx, 2), "y": round(cy, 2), "r": round(max(w, h) / 2, 2),
                    "source": "symbol", "chain_id": ch.get("chain_id"),
                    "style_id": ch.get("style_id"), "layer": str(st.get("layer") or "")})
    row_h = sym_max
    out.sort(key=lambda s: (round(s["y"] / row_h), s["x"]))
    for i, s in enumerate(out):
        s["id"] = i + 1
    return out


def build_runs(chains, styles, style_ids, ft_per_pt: float, words=None,
               callouts=None, labels=None, exclude_chain_ids=None,
               split_at_structures: bool = True) -> dict:
    """Runs of the families `style_ids`. See the module docstring.

    chains / styles: the result dicts. words: page text LINES (x0,y0,x1,y1,
    text) in the same space, optional. callouts: the Sheet-callouts rows
    ({text, gx, gy, target_chain_id}), optional. labels: {style_id: current
    family name} from the UI, optional.

    split_at_structures: True is the sewer convention (manhole to manhole:
    a structure ends a run). False follows the MAIN through its structures
    and breaks only where a lateral leaves it - which is how the Res-A
    client traced his storm runs (his 282-ft run passes an inlet and three
    wyes); the structures are still found, named and counted.
    """
    style_ids = set(int(s) for s in (style_ids or []))
    excl = set(int(c) for c in (exclude_chain_ids or []))
    labels = {int(k): str(v) for k, v in (labels or {}).items()}
    st_by = {s.get("style_id"): s for s in styles or []}
    fam = [ch for ch in chains or []
           if ch.get("style_id") in style_ids and ch.get("chain_id") not in excl
           and ch.get("class") != "annotation" and len(ch.get("points") or []) >= 2]
    ft = ft_per_pt if ft_per_pt else None
    snap = max(SNAP_MIN_PT, SNAP_FT / ft_per_pt) if ft_per_pt else SNAP_MIN_PT
    reach = max(snap, SYM_REACH_FT / ft_per_pt) if ft_per_pt else 12.0

    structures = find_structures(chains, styles, ft_per_pt or 0.0, style_ids, callouts,
                                 words=words, fam=fam)
    fam_names = []
    for sid in sorted(style_ids):
        s = st_by.get(sid) or {}
        fam_names.append(labels.get(sid) or str(s.get("suggested_label") or s.get("layer") or ""))
    fam_layers = " ".join(str((st_by.get(sid) or {}).get("layer") or "") for sid in style_ids)
    system = system_code(" ".join(fam_names), fam_layers)
    name_size = None
    for nm in fam_names:
        name_size = name_size or size_token(nm)
    name_size = name_size or size_token(fam_layers)

    # ---- 1. cut every chain at the structures on it and at tees ----------
    cuts = defaultdict(list)    # chain_id -> [(seg_index, t, tag)]
    sym_on = {}
    for ch in fam:
        pts = ch["points"]
        for s in structures:
            best = None
            for i in range(1, len(pts)):
                t, d = _project(s["x"], s["y"], pts[i - 1], pts[i])
                if d <= reach + s["r"] and (best is None or d < best[2]):
                    best = (i, t, d)
            if best:
                cuts[ch["chain_id"]].append((best[0], best[1], ("sym", s["id"])))
                sym_on[s["id"]] = True
                # the structure's node sits where it cuts the line (the foot
                # of its perpendicular), not at the symbol's own centre: a
                # symbol 3 ft off the line made a node nothing reached, so
                # the run was cut there yet ended at an "end" (critic, M3)
                if "fd" not in s or best[2] < s["fd"]:
                    a, b = pts[best[0] - 1], pts[best[0]]
                    s["fx"] = a[0] + best[1] * (b[0] - a[0])
                    s["fy"] = a[1] + best[1] * (b[1] - a[1])
                    s["fd"] = best[2]
    for a in fam:
        for end in (a["points"][0], a["points"][-1]):
            for b in fam:
                if b is a:
                    continue
                pts = b["points"]
                best = None
                for i in range(1, len(pts)):
                    t, d = _project(end[0], end[1], pts[i - 1], pts[i])
                    if d <= snap and (best is None or d < best[2]):
                        best = (i, t, d)
                if best:
                    i, t, _ = best
                    at_start = i == 1 and t <= 1e-6
                    at_end = i == len(pts) - 1 and t >= 1 - 1e-6
                    if not at_start and not at_end:
                        cuts[b["chain_id"]].append((i, t, ("tee", None)))
    pieces = []
    for ch in fam:
        pts = [list(map(float, p)) for p in ch["points"]]
        cl = sorted(cuts.get(ch["chain_id"], []), key=lambda c: (c[0], c[1]))
        cur = [pts[0]]
        last_i, last_t = 1, 0.0
        for (i, t, tag) in cl:
            if i < last_i or (i == last_i and t <= last_t + 1e-9):
                continue                     # two cuts at one spot are one
            for k in range(last_i, i):
                cur.append(pts[k])
            a, b = pts[i - 1], pts[i]
            q = [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]
            if math.hypot(q[0] - cur[-1][0], q[1] - cur[-1][1]) > 1e-6:
                cur.append(q)
            if _poly_len(cur) > 1e-6:
                pieces.append({"chain": ch, "pts": cur})
            cur = [q]
            last_i, last_t = i, t
        for k in range(last_i, len(pts)):
            if math.hypot(pts[k][0] - cur[-1][0], pts[k][1] - cur[-1][1]) > 1e-6:
                cur.append(pts[k])
        if _poly_len(cur) > 1e-6:
            pieces.append({"chain": ch, "pts": cur})

    # ---- 2. nodes: snap piece ends together and onto structures ----------
    nodes = []

    def node_for(x, y, sym_id=None):
        for n in nodes:
            if math.hypot(n["x"] - x, n["y"] - y) <= snap:
                if sym_id and not n.get("sym"):
                    n["sym"] = sym_id
                return n
        n = {"x": x, "y": y, "sym": sym_id, "id": len(nodes)}
        nodes.append(n)
        return n
    for s in structures:
        if sym_on.get(s["id"]):
            node_for(s.get("fx", s["x"]), s.get("fy", s["y"]), s["id"])
    edges = []
    for pc in pieces:
        a, b = pc["pts"][0], pc["pts"][-1]
        na = node_for(a[0], a[1])
        nb = node_for(b[0], b[1])
        if na is nb and _poly_len(pc["pts"]) <= snap:
            continue
        edges.append({"a": na["id"], "b": nb["id"], "pts": pc["pts"],
                      "chain_id": pc["chain"]["chain_id"], "len_pt": _poly_len(pc["pts"]),
                      "used": False})
    deg = defaultdict(int)
    adj = defaultdict(list)
    for k, e in enumerate(edges):
        deg[e["a"]] += 1
        deg[e["b"]] += 1
        adj[e["a"]].append(k)
        adj[e["b"]].append(k)

    # the direction an edge leaves a node in, measured to the point 24-60 pt
    # along it (a quarter of a short edge): the drafter's wye symbol puts a
    # 3-pt jog in the pipe line at the junction, and a 10-pt sample read the
    # jog instead of the pipe (Res-A C4.0 chain 1947, deflection 64 degrees
    # for a straight-through main).
    def out_dir(k, nid):
        e = edges[k]
        pts = e["pts"] if e["a"] == nid else e["pts"][::-1]
        p0 = np.asarray(pts[0], float)
        reach_pt = max(24.0, min(60.0, 0.25 * e["len_pt"]))
        acc = 0.0
        prev = p0
        for q in pts[1:]:
            q = np.asarray(q, float)
            step = float(np.hypot(*(q - prev)))
            if acc + step >= reach_pt and step > 0:
                q = prev + (q - prev) * ((reach_pt - acc) / step)
                v = q - p0
                n = float(np.hypot(*v))
                return v / n if n else np.array([1.0, 0.0])
            acc += step
            prev = q
        v = np.asarray(pts[-1], float) - p0
        n = float(np.hypot(*v))
        return v / n if n else np.array([1.0, 0.0])

    # ---- 3. through-pairs at every junction (and, when structures do not
    # break a run, at every structure node with three or more edges) ------
    partner = {}
    junctions = []
    for nid in range(len(nodes)):
        if deg[nid] < 3:
            continue
        if nodes[nid].get("sym") and split_at_structures:
            continue
        ks = list(adj[nid])
        dirs = {k: out_dir(k, nid) for k in ks}
        pairs = []
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                cosang = float(np.clip(dirs[ks[i]] @ dirs[ks[j]], -1, 1))
                deflect = 180.0 - math.degrees(math.acos(cosang))
                pairs.append((deflect, ks[i], ks[j]))
        used = set()
        chosen = []
        for deflect, ka, kb in sorted(pairs):
            if deflect > THROUGH_DEG or ka in used or kb in used:
                continue
            partner[(nid, ka)] = kb
            partner[(nid, kb)] = ka
            used.add(ka)
            used.add(kb)
            chosen.append(round(deflect, 1))
        junctions.append({"x": round(nodes[nid]["x"], 1), "y": round(nodes[nid]["y"], 1),
                          "degree": deg[nid], "through": chosen,
                          "pairs": [round(d, 1) for d, _, _ in sorted(pairs)],
                          "heads": [[[round(float(v), 1) for v in p] for p in
                                     (edges[k]["pts"] if edges[k]["a"] == nid else edges[k]["pts"][::-1])[:3]]
                                    for k in ks],
                          "sym": nodes[nid].get("sym")})

    def breaks_at(nid, k_in):
        n = nodes[nid]
        if n.get("sym") and split_at_structures:
            return True
        if deg[nid] == 2:
            return False
        if deg[nid] == 1:
            return True
        return (nid, k_in) not in partner

    # ---- 4. walk the runs ------------------------------------------------
    runs = []

    def walk(start_edge, start_node):
        path, geom = [], []
        e, n = start_edge, start_node
        while True:
            edge = edges[e]
            edge["used"] = True
            pts = edge["pts"] if edge["a"] == n else edge["pts"][::-1]
            if geom and math.hypot(geom[-1][0] - pts[0][0], geom[-1][1] - pts[0][1]) <= snap:
                geom.extend(pts[1:])
            else:
                geom.extend(pts)
            path.append(e)
            n = edge["b"] if edge["a"] == n else edge["a"]
            if breaks_at(n, e):
                return path, geom, n
            if deg[n] == 2:
                nxt = [k for k in adj[n] if k != e and not edges[k]["used"]]
            else:
                p = partner.get((n, e))
                nxt = [p] if p is not None and not edges[p]["used"] else []
            if not nxt:
                return path, geom, n
            e = nxt[0]

    def start_rank(nid):
        n = nodes[nid]
        if n.get("sym") and split_at_structures:
            return 0
        if deg[nid] == 1:
            return 1
        if deg[nid] >= 3:
            return 2
        return 3
    for nid in sorted(range(len(nodes)), key=lambda i: (start_rank(i), i)):
        for k in adj[nid]:
            if edges[k]["used"]:
                continue
            # from a junction, only an edge that does NOT continue through it
            # starts a run here (its partner's run will pick it up)
            if deg[nid] >= 3 and (nid, k) in partner:
                continue
            # a plain bend never starts a run: it is the middle of one
            if deg[nid] == 2 and not (nodes[nid].get("sym") and split_at_structures):
                continue
            path, geom, end = walk(k, nid)
            runs.append({"edges": path, "geom": geom, "from": nid, "to": end})
    for k, e in enumerate(edges):        # closed loops / through-only remainders
        if not e["used"]:
            path, geom, end = walk(k, e["a"])
            runs.append({"edges": path, "geom": geom, "from": e["a"], "to": end})

    # ---- 5. names, sizes ---------------------------------------------------
    W = []
    for w in words or []:
        try:
            W.append((float(w[0]), float(w[1]), float(w[2]), float(w[3]), str(w[4]).strip()))
        except (TypeError, ValueError, IndexError):
            continue

    def words_near_pt(x, y, r):
        out = []
        for x0, y0, x1, y1, t in W:
            dx = max(x0 - x, 0.0, x - x1)
            dy = max(y0 - y, 0.0, y - y1)
            if math.hypot(dx, dy) <= r:
                out.append((math.hypot(dx, dy), t))
        return sorted(out)
    on_runs = {n["sym"] for n in nodes if n.get("sym")}
    for s in structures:
        if s.get("name"):
            continue
        for d, t in words_near_pt(s["x"], s["y"], NAME_REACH_PT):
            m = _STRUCT_NAME.search(t)
            if m:
                s["name"] = (m.group(1) + "-" + m.group(2)).upper()
                s["name_from"] = "sheet"
                break
    # repeated callout names are numbered in reading order (AREA INLET #3);
    # unnamed structures on the runs count as MH-n
    seen = defaultdict(int)
    for s in structures:
        if s.get("source") in ("callout", "label"):
            seen[s["name"]] += 1
    counts = defaultdict(int)
    k = 0
    for s in structures:
        if s.get("source") in ("callout", "label"):
            if seen[s["name"]] > 1:
                counts[s["name"]] += 1
                s["name"] = f"{s['name']} #{counts[s['name']]}"
            # a label with no leader stroke was placed at the nearest point
            # of a run: said in the name, so the operator knows to check
            if s.get("approx"):
                s["name"] = f"{s['name']} (approx.)"
            s["name_from"] = "sheet"
        elif not s.get("name") and s["id"] in on_runs:
            k += 1
            s["name"] = f"MH-{k}"
            s["name_from"] = "counted"
    sym_name = {s["id"]: s.get("name") for s in structures}
    lead_by_chain = defaultdict(list)
    for c in callouts or []:
        cid = c.get("target_chain_id")
        m = LEADER.search(str(c.get("text") or ""))
        if cid is not None and m:
            lead_by_chain[int(cid)].append(f'{int(m.group(1))}"')
    chain_inline = {ch["chain_id"]: ch.get("inline_labels") or [] for ch in fam}

    out_runs = []
    runs.sort(key=lambda r: -sum(edges[e]["len_pt"] for e in r["edges"]))
    for i, r in enumerate(runs):
        len_pt = sum(edges[e]["len_pt"] for e in r["edges"])
        chain_ids = sorted({edges[e]["chain_id"] for e in r["edges"]})
        sizes = defaultdict(float)
        src = None
        for e in r["edges"]:
            for lab in chain_inline.get(edges[e]["chain_id"], []):
                m = INLINE.match(str(lab).upper())
                if m:
                    sizes[f'{int(m.group(1))}"'] += edges[e]["len_pt"]
                    src = src or "inline"
        if not sizes:
            for e in r["edges"]:
                for sz in lead_by_chain.get(edges[e]["chain_id"], []):
                    sizes[sz] += edges[e]["len_pt"]
                    src = src or "callout"
        g = np.asarray(r["geom"], float)
        if not sizes and W:
            for x0, y0, x1, y1, t in W:
                m = _SIZE_WORD.search(t)
                if not m:
                    continue
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                if _dist_to_poly(g, cx, cy) <= 25.0:
                    sizes[f'{int(m.group(1))}"'] += 1.0
                    src = src or "beside"
        size, size_from, mixed = None, None, False
        if sizes:
            top = sorted(sizes.items(), key=lambda kv: -kv[1])
            size, size_from = top[0][0], src
            mixed = len(top) > 1 and top[1][1] >= 0.25 * top[0][1]
        elif name_size:
            size, size_from = name_size, "family name"
        rname = None
        if W:
            for x0, y0, x1, y1, t in W:
                m = _RUN_NAME.search(t)
                if m:
                    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                    if _dist_to_poly(g, cx, cy) <= 25.0:
                        rname = (m.group(1) + "-" + m.group(2)).upper()
                        break
        a, b = nodes[r["from"]], nodes[r["to"]]

        def endname(n):
            if n.get("sym"):
                return sym_name.get(n["sym"]) or "structure"
            return "junction" if deg[n["id"]] >= 3 else "end"
        out_runs.append({
            "id": i + 1,
            "name": rname or f"{system}-{i + 1}",
            "name_from": "sheet" if rname else "counted",
            "system": system,
            "from": endname(a), "to": endname(b),
            "from_sym": a.get("sym"), "to_sym": b.get("sym"),
            "chain_ids": chain_ids,
            "length_pt": round(len_pt, 2),
            "length_ft": round(len_pt * ft_per_pt, 2) if ft else None,
            "size": size, "size_from": size_from, "size_mixed": mixed,
            "sizes": {k: round(v, 1) for k, v in sizes.items()},
            "points": [[round(x, 2), round(y, 2)] for x, y in r["geom"]],
            "structures": [n for n in (a.get("sym"), b.get("sym")) if n],
        })
    n_on = len(on_runs)
    return {
        "system": system,
        "families": [{"style_id": sid, "name": nm} for sid, nm in zip(sorted(style_ids), fam_names)],
        "runs": out_runs,
        "structures": [dict(s) for s in structures],
        "structures_on_runs": n_on,
        "structure_symbols": len(structures),
        "junctions": junctions,
        "split_at_structures": bool(split_at_structures),
        "snap_pt": round(snap, 2),
        "notes": ([] if fam else ["no lines in the picked families"]),
    }
