"""Bid rows off the cut / fill and off the sheet's own limit line.

An excavator's first move on a site plan is the limits-of-disturbance line: its length is the
fence, everything inside it is Clear & Grub and the strip area (call of 2026-09-15). Everything
here is arithmetic on things already read - the sheet's lines (this engine's or the product's
detection), the computed volumes and the operator's typed numbers - and every row says what it
was made from. Nothing here reads a height.

* `page_lines`      the sheet's lines with the name their CAD layer / family gives them
* `stitch_ring`     a limit line is never ONE chain: the family is joined end to end
* `ring_metrics`    area / perimeter / closure gap of a limit polygon
* `limit_candidates`, `erosion_families`   which families name a limit line / erosion control
* `takeoff_rows`    the bid rows: fence LF, Clear & Grub SF, Strip Topsoil CY, Site Cut / Fill,
                    Rough Place / Export / Import Topsoil, Export Cut / Import Structural, with
                    the typed shrink / swell factors (1.0 = neat line, the default)
* `block_depths`    block-averaged depths for the printed crew map
* `crew_map_png`    the printable map (red cut / blue fill, one printed depth per block)
"""
from __future__ import annotations

import io
import math
import re
import time

import numpy as np

# the sheet's limit line, by CAD layer or family name (a TOPO / GRAD boundary layer is the grading
# limit; a parcel boundary is not)
LIMIT_RE = re.compile(r"LIMITS?\s*OF\s*(DISTURB|GRAD|WORK|CONSTR|CLEAR)|\bLOD\b|DISTURBANCE|GRADING\s*LIMITS?|"
                      r"CLEARING\s*LIMITS?|LIMIT\s*LINE|(TOPO|GRAD)[-_ ]?BOUND", re.I)
# erosion-control families that ARE the fence line on a bid (silt fence, straw wattles, sediment logs)
EROSION_RE = re.compile(r"SILT\s*FENCE|\bSILT\b|SEDIMENT\s*(CONTROL\s*)?LOG|WATTLE|WADDLE|FIBER\s*ROLL|"
                        r"STRAW\s*(ROLL|LOG|BALE)|EROSION\s*LOG|\bSCL\b", re.I)
# CORE dash signatures that are sheet furniture, never a plan line (mirrors core_reading.SKIP_SIGNATURES)
_SKIP_SIG = {"leader", "legend swatch", "grid", "sheet furniture"}
STITCH_MAX_PT = 500.0      # the largest join taken (the largest genuine bridge measured on a limit line was 368 pt)
STITCH_REPORT_PT = 12.0    # joins at least this long are reported (label / dash breaks run 0-124 pt)
DEFAULT_OPTS = {
    "strip_in": None,          # topsoil thickness stripped over the limit, inches (typed)
    "strip_factor": 1.15,      # the strip volume's loosening allowance (his method: area x depth x 1.15)
    "place_depth_in": None,    # Rough Place Topsoil: depth over the green area (limit less footprints / paving)
    "place_less_sf": 0.0,      # footprints and paving taken off the limit for the green area
    "place_pct": None,         # or: the share of the strip that goes back (50 = half)
    "export_factor": 1.0,      # swell on what leaves (1.0 = neat line)
    "import_factor": 1.0,      # shrink on what comes in (1.0 = neat line)
    "fence_source": "auto",    # auto: the erosion-control families when the sheet has them, else the limit line
}


def r1(v) -> float:
    return round(float(v), 1)


# ------------------------------------------------------------------ the sheet's lines, named
def page_lines(an: dict | None, core: dict | None = None) -> list[dict]:
    """Every line on the sheet with the name its CAD layer / family gives it:
    [{id, src, name, fam, pts (np.ndarray), length}]. `src` is "core" (the product's own detection,
    which carries a layer per chain) or "engine" (this reader's chains, named by their style's layer).
    Both are listed when both exist: the operator clicks a drawn line, the ring is stitched from
    whichever source that line belongs to."""
    out = []
    if core and core.get("chains"):
        styles = {s.get("style_id"): s for s in (core.get("styles") or [])}
        for c in core["chains"]:
            if c.get("class") == "annotation" or c.get("dash_signature") in _SKIP_SIG:
                continue
            pts = c.get("points") or []
            if len(pts) < 2:
                continue
            st = styles.get(c.get("style_id")) or {}
            name = (c.get("layer") or st.get("layer") or st.get("label") or st.get("suggested_label") or "")
            fam = name or f"style {c.get('style_id')}"
            out.append({"id": f"core:{c['chain_id']}", "src": "core", "name": name, "fam": fam,
                        "pts": np.asarray(pts, dtype=float), "length": float(c.get("length_pt") or 0.0)})
    if an and an.get("chains"):
        styles = an.get("styles") or []
        for ch in an["chains"]:
            if ch.get("user") is not None:
                continue
            pts = ch.get("pts") or []
            if len(pts) < 2:
                continue
            sid = ch.get("style")
            st = styles[sid] if isinstance(sid, int) and 0 <= sid < len(styles) else {}
            name = st.get("layer") or ""
            fam = name or f"style {sid}"
            out.append({"id": f"engine:{ch['id']}", "src": "engine", "name": name, "fam": fam,
                        "pts": np.asarray(pts, dtype=float), "length": float(ch.get("length") or 0.0)})
    return out


def _families(lines: list[dict], rx: re.Pattern) -> list[dict]:
    fam: dict[tuple, dict] = {}
    for ln in lines:
        if not ln["name"] or not rx.search(ln["name"]):
            continue
        k = (ln["src"], ln["fam"])
        d = fam.setdefault(k, {"name": ln["name"], "src": ln["src"], "fam": ln["fam"], "length_pt": 0.0, "n": 0, "ids": [], "seed": None, "seed_len": 0.0})
        d["length_pt"] += ln["length"]
        d["n"] += 1
        d["ids"].append(ln["id"])
        if ln["length"] > d["seed_len"]:
            d["seed"], d["seed_len"] = ln["id"], ln["length"]
    rows = sorted(fam.values(), key=lambda d: -d["length_pt"])
    for d in rows:
        d["length_pt"] = r1(d["length_pt"])
        d.pop("seed_len", None)
    return rows


def limit_candidates(lines: list[dict]) -> list[dict]:
    """Families whose name says they are the limit line, longest first. The product's detection
    (src core) comes first when it has one: it carries the layer on every chain."""
    rows = _families(lines, LIMIT_RE)
    order = {"core": 0, "engine": 1, "ink": 2}
    rows.sort(key=lambda d: (order.get(d["src"], 3), -d["length_pt"]))
    # the same layer through two sources is one offer: the product's chains first, then this
    # reader's, then the raw ink (a patterned line the chaining kept as dots)
    seen: set = set()
    out = []
    for d in rows:
        if d["fam"] in seen:
            continue
        seen.add(d["fam"])
        out.append(d)
    return out


def erosion_families(lines: list[dict]) -> list[dict]:
    """Erosion-control families (silt fence, straw wattles, sediment logs): the fence line of a bid.
    Only one source is counted: the product's detection when it has any, else this reader's."""
    rows = _families(lines, EROSION_RE)
    for src in ("core", "engine", "ink"):
        pick = [d for d in rows if d["src"] == src]
        if pick:
            return pick
    return rows


def lines_of_family(lines: list[dict], src: str, fam: str) -> list[dict]:
    return [ln for ln in lines if ln["src"] == src and ln["fam"] == fam]


class SegIndex:
    """Every segment of every line in one array, so a click finds the nearest line in one
    vectorised pass (a sheet's raw ink is tens of thousands of dashes)."""

    def __init__(self, lines: list[dict]):
        self.lines = lines
        A, B, owner = [], [], []
        for i, ln in enumerate(lines):
            P = ln["pts"]
            if len(P) < 2:
                continue
            A.append(P[:-1])
            B.append(P[1:])
            owner.append(np.full(len(P) - 1, i, dtype=np.int64))
        self.A = np.vstack(A) if A else np.zeros((0, 2))
        self.B = np.vstack(B) if B else np.zeros((0, 2))
        self.owner = np.concatenate(owner) if owner else np.zeros(0, dtype=np.int64)
        self.AB = self.B - self.A
        self.L2 = np.where((self.AB * self.AB).sum(axis=1) > 0, (self.AB * self.AB).sum(axis=1), 1.0)
        self.lo = np.minimum(self.A, self.B)
        self.hi = np.maximum(self.A, self.B)

    def nearest(self, x: float, y: float, tol: float) -> dict | None:
        if not len(self.A):
            return None
        near = (self.lo[:, 0] <= x + tol) & (self.hi[:, 0] >= x - tol) & (self.lo[:, 1] <= y + tol) & (self.hi[:, 1] >= y - tol)
        idx = np.nonzero(near)[0]
        if not len(idx):
            return None
        A, AB, L2 = self.A[idx], self.AB[idx], self.L2[idx]
        t = np.clip(((x - A[:, 0]) * AB[:, 0] + (y - A[:, 1]) * AB[:, 1]) / L2, 0.0, 1.0)
        px = A[:, 0] + t * AB[:, 0]
        py = A[:, 1] + t * AB[:, 1]
        d = np.sqrt((px - x) ** 2 + (py - y) ** 2)
        k = int(np.argmin(d))
        if d[k] > tol:
            return None
        return self.lines[int(self.owner[idx[k]])]


def nearest_line(lines: list[dict], x: float, y: float, tol: float) -> dict | None:
    """The line closest to (x, y) within tol page points, by segment distance."""
    return SegIndex(lines).nearest(x, y, tol)


# ------------------------------------------------------------------ stitching the limit line
def stitch_ring(lines: list[dict], seed_id, max_join: float = STITCH_MAX_PT) -> dict:
    """A limit-of-work line is almost never ONE chain: on one civil sheet the layer is 10 open
    chains (dash runs broken at labels and corners) and the longest alone enclosed 4,730 SF of a
    bid line 47 times that. The seed's family is joined end to end, nearest free end first, until no
    end is within `max_join`. Every join is on the record: {"pts", "n", "joins" (pt), "leftover"
    (family lines left out), "gap" (the closure gap, pt), "used" (line ids)}."""
    by_id = {ln["id"]: ln for ln in lines}
    start = by_id.get(seed_id)
    if start is None:
        return {"pts": [], "n": 0, "joins": [], "leftover": 0, "gap": 0.0, "used": []}
    fam = [ln for ln in lines if ln["src"] == start["src"] and ln["fam"] == start["fam"]]
    own = float(np.hypot(*(start["pts"][0] - start["pts"][-1])))
    poly = [tuple(p) for p in start["pts"]]
    used = [start["id"]]
    joins: list[float] = []
    if own >= 2.0 and len(fam) >= 2:
        # every free line's two ends in one array (a limit drawn as a patterned line is thousands
        # of short dashes): each step is one vectorised distance from the ring's two ends
        free = [ln for ln in fam if ln["id"] != start["id"]]
        E = np.array([[ln["pts"][0], ln["pts"][-1]] for ln in free], dtype=float)   # (n, 2, 2)
        alive = np.ones(len(free), dtype=bool)
        while alive.any():
            head, tail = np.asarray(poly[0], float), np.asarray(poly[-1], float)
            dt = np.linalg.norm(E - tail[None, None, :], axis=2)      # (n, 2): tail to each line's start / end
            dh = np.linalg.norm(E - head[None, None, :], axis=2)
            dt[~alive] = np.inf
            dh[~alive] = np.inf
            it = int(np.argmin(dt))
            ih = int(np.argmin(dh))
            d_t, d_h = float(dt.flat[it]), float(dh.flat[ih])
            if min(d_t, d_h) > max_join:
                break
            if d_t <= d_h:
                k, e = divmod(it, 2)
                P = [tuple(p) for p in free[k]["pts"]]
                if e == 1:                      # its end is nearest the tail: walk it backwards
                    P.reverse()
                poly = poly + P
                d = d_t
            else:
                k, e = divmod(ih, 2)
                P = [tuple(p) for p in free[k]["pts"]]
                if e == 0:                      # its start is nearest the head: it runs into the ring reversed
                    P.reverse()
                poly = P + poly
                d = d_h
            alive[k] = False
            used.append(free[k]["id"])
            joins.append(round(d, 1))
    gap = float(math.hypot(poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1])) if len(poly) > 1 else 0.0
    pts = [[round(float(x), 2), round(float(y), 2)] for x, y in poly]
    # drop consecutive duplicates
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > 0.05 or abs(p[1] - out[-1][1]) > 0.05:
            out.append(p)
    return {"pts": out, "n": len(used), "joins": joins, "leftover": len(fam) - len(used), "gap": round(gap, 1), "used": used}


def ring_metrics(pts, ft_per_pt: float) -> dict | None:
    """Area (SF), perimeter (ft) and closure gap (ft) of a limit polygon in page points; the ring is
    closed (last corner joined to the first) - the same convention the volume mask uses - and the
    gap says how far apart the drawn ends really are, so an open trace is judged, never silently
    closed. Same shoelace as the live product's boundary_metrics."""
    p = [(float(q[0]), float(q[1])) for q in (pts or []) if isinstance(q, (list, tuple)) and len(q) >= 2]
    if len(p) < 3:
        return None
    a = 0.0
    per = 0.0
    for i in range(len(p)):
        x0, y0 = p[i - 1]
        x1, y1 = p[i]
        a += (x0 + x1) * (y0 - y1)
        per += math.hypot(x1 - x0, y1 - y0)
    gap = math.hypot(p[0][0] - p[-1][0], p[0][1] - p[-1][1])
    area_pt2 = abs(a / 2.0)
    repaired = False
    # a ring that crosses itself: the shoelace subtracts the loops it winds the other way, and
    # the volumes are computed on the REPAIRED ring (surface.cut_fill_grid, make_valid). The rows
    # use the same ground: the repaired ring's area (one traced limit gave 441,690 SF here and
    # 468,259 SF to the volumes, 6.0 % apart)
    try:
        import shapely
        from shapely.geometry import Polygon
        poly = Polygon(p)
        if not poly.is_valid:
            fixed = shapely.make_valid(poly)
            parts = [g for g in getattr(fixed, "geoms", [fixed]) if g.geom_type in ("Polygon", "MultiPolygon")]
            ar = sum(g.area for g in parts)
            if ar > 0:
                area_pt2, repaired = ar, True
    except Exception:
        pass
    return {"area_sf": r1(area_pt2 * ft_per_pt * ft_per_pt), "perim_ft": r1(per * ft_per_pt),
            "gap_ft": r1(gap * ft_per_pt), "n": len(p), "repaired": repaired}


def stitch_status(st: dict, ft_per_pt: float | None) -> str:
    """One plain line about what the stitcher did, in the operator's units."""
    fmt = (lambda v: f"{round(v * ft_per_pt):,} ft") if ft_per_pt else (lambda v: f"{round(v):,} pt")
    big = [j for j in st.get("joins", []) if j >= STITCH_REPORT_PT]
    m = f"limit stitched from {st['n']} lines" if st.get("n", 0) > 1 else "limit set from one line"
    if big:
        m += f"; {len(big)} gap{'s' if len(big) > 1 else ''} bridged, largest {fmt(max(big))}"
    if st.get("leftover"):
        m += f"; {st['leftover']} line{'s' if st['leftover'] > 1 else ''} of this family left out (further than {fmt(STITCH_MAX_PT)})"
    if st.get("gap", 0) > STITCH_REPORT_PT:
        m += f"; the ends are {fmt(st['gap'])} apart and are joined"
    return m


# ------------------------------------------------------------------ the bid rows
def _f(v, default=None):
    try:
        if v is None or v == "":
            return default
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def takeoff_opts(raw: dict | None) -> dict:
    o = dict(DEFAULT_OPTS)
    for k in o:
        if raw and k in raw:
            o[k] = raw[k]
    o["strip_in"] = _f(o["strip_in"])
    o["strip_factor"] = _f(o["strip_factor"], 1.15) or 1.0
    o["place_depth_in"] = _f(o["place_depth_in"])
    o["place_less_sf"] = max(_f(o["place_less_sf"], 0.0) or 0.0, 0.0)
    o["place_pct"] = _f(o["place_pct"])
    o["export_factor"] = _f(o["export_factor"], 1.0) or 1.0
    o["import_factor"] = _f(o["import_factor"], 1.0) or 1.0
    if o["export_factor"] <= 0:
        o["export_factor"] = 1.0
    if o["import_factor"] <= 0:
        o["import_factor"] = 1.0
    if o["fence_source"] not in ("auto", "erosion", "limit"):
        o["fence_source"] = "auto"
    return o


ZONE_ROW_SEP = " – "     # "Site Cut – Island": the zone's own row (host CF_ROW_RE, bid sheet CF_PART_RE)


def takeoff_rows(totals: dict | None, limit: dict | None, erosion: list[dict] | None, opts: dict | None,
                 ft_per_pt: float | None, parts: list | None = None) -> dict:
    """The bid rows, each with its basis. `totals` are compute's (cut_cy, fill_cy, bank measure),
    `limit` = {area_sf, perim_ft, source, family} for the limit in force (None: no limit),
    `erosion` = erosion_families() of the display sheet, `opts` = takeoff_opts(). With no strip,
    no rough place and both factors 1.0 the rows are Site Cut / Site Fill and the net as
    Export Cut or Import Structural - exactly the rows the product filed before this module.
    `parts` = itemized grading zones (and the rest of the limit), [{name, section_ft, cut_cy,
    fill_cy}], which add up to the totals: each files its own "Site Cut – <zone>" / "Site Fill –
    <zone>" row instead of the one pair, and the net is the rows' own sum."""
    o = takeoff_opts(opts)
    rows: list[dict] = []
    notes: list[str] = []
    ef, imf = o["export_factor"], o["import_factor"]
    fx = (lambda f: f" x {f:g}" if abs(f - 1.0) > 1e-9 else "")

    def add(label, qty, unit, basis, kind):
        if qty is None or qty <= 0:
            return
        rows.append({"label": label, "qty": r1(qty), "unit": unit, "basis": basis, "kind": kind})

    # ---- the limit line's own rows
    area_sf = _f((limit or {}).get("area_sf"))
    perim_ft = _f((limit or {}).get("perim_ft"))
    lim_txt = ""
    if limit:
        src = limit.get("source") or "drawn"
        lim_txt = (f"the sheet's {limit['family']} line" if src == "picked" and limit.get("family")
                   else "the limit you drew" if src == "drawn" else "the limit")
        if limit.get("repaired"):
            lim_txt += " (it crosses itself: repaired, the same area the volumes use)"
    ero_ft = 0.0
    ero_names = []
    for fam in erosion or []:
        L = _f(fam.get("length_ft"))
        if L is None and ft_per_pt:
            L = _f(fam.get("length_pt"), 0.0) * ft_per_pt
        if L:
            ero_ft += L
            ero_names.append(fam.get("name") or fam.get("fam") or "erosion control")
    fence_from = None
    if ero_ft > 0 and o["fence_source"] in ("auto", "erosion"):
        fence_from = "erosion"
        add("Silt / Construction Fence", ero_ft, "LF",
            f"the sheet's erosion-control lines: {', '.join(dict.fromkeys(ero_names))} ({ero_ft:,.0f} ft)", "fence")
        if perim_ft:
            notes.append(f"Silt fence read from the erosion-control lines ({ero_ft:,.0f} ft), not the limit line ({perim_ft:,.0f} ft around).")
    elif perim_ft:
        fence_from = "limit"
        add("Silt / Construction Fence", perim_ft, "LF", f"{lim_txt}: {perim_ft:,.0f} ft around", "fence")
        if o["fence_source"] == "erosion":
            notes.append("No erosion-control lines (silt fence, wattles, sediment logs) on this sheet: the fence is the limit line's perimeter.")
    if area_sf:
        add("Clear & Grub", area_sf, "SF", f"{lim_txt}: {area_sf:,.0f} SF inside", "clear")
    strip_cy = 0.0
    if area_sf and o["strip_in"] and o["strip_in"] > 0:
        strip_cy = area_sf * (o["strip_in"] / 12.0) / 27.0 * o["strip_factor"]
        add("Strip Topsoil", strip_cy, "CY",
            f"{area_sf:,.0f} SF x {o['strip_in']:g} in{fx(o['strip_factor'])}" + (" (allowance)" if abs(o["strip_factor"] - 1.0) > 1e-9 else " (neat)"),
            "strip")
    elif o["strip_in"] and o["strip_in"] > 0 and not area_sf:
        notes.append("Strip Topsoil needs a limit: pick the sheet's limit line or draw one.")

    # ---- the volumes
    cut = max(_f((totals or {}).get("cut_cy"), 0.0) or 0.0, 0.0)
    fill = max(_f((totals or {}).get("fill_cy"), 0.0) or 0.0, 0.0)
    if parts and totals is not None:
        # one row pair per zone; the net is the sum of the rows as filed, so the takeoff adds up.
        # Two zones with one name get a number each: a label is the key of the host's quantity
        # override and price, and two rows sharing one would take each other's
        cut_r = fill_r = 0
        used: set = set()
        for p in parts:
            base = str(p.get("name") or "zone").strip() or "zone"
            nm, k = base, 1
            while nm.lower() in used:             # "Lot", "Lot (2)", "Lot" -> the third is "Lot (3)"
                k += 1
                nm = f"{base} ({k})"
            used.add(nm.lower())
            d_in = round(float(p.get("section_ft") or 0.0) * 12.0, 2)
            to = f"subgrade, {d_in:g} in under finished grade" if d_in else "finished grade"
            c = int(round(max(_f(p.get("cut_cy"), 0.0) or 0.0, 0.0)))
            f_ = int(round(max(_f(p.get("fill_cy"), 0.0) or 0.0, 0.0)))
            add(f"Site Cut{ZONE_ROW_SEP}{nm}", c, "CY", f"cut inside {nm}, dug to {to}, bank measure", "cut")
            add(f"Site Fill{ZONE_ROW_SEP}{nm}", f_, "CY", f"fill inside {nm}, to {to}, bank measure", "fill")
            cut_r += c
            fill_r += f_
        cut, fill = float(cut_r), float(fill_r)
    else:
        add("Site Cut", round(cut), "CY", "cut between the two surfaces, bank measure", "cut")
        add("Site Fill", round(fill), "CY", "fill between the two surfaces, bank measure", "fill")
    # ---- rough place / export / import topsoil
    placed = None
    if o["place_depth_in"] and o["place_depth_in"] > 0 and area_sf:
        green = max(area_sf - o["place_less_sf"], 0.0)
        placed = green * (o["place_depth_in"] / 12.0) / 27.0
        add("Rough Place Topsoil", placed, "CY",
            f"{area_sf:,.0f} SF" + (f" - {o['place_less_sf']:,.0f} SF footprints and paving" if o["place_less_sf"] > 0 else "")
            + f" = {green:,.0f} SF x {o['place_depth_in']:g} in", "respread")
    elif o["place_pct"] is not None and o["place_pct"] > 0 and strip_cy > 0:
        placed = strip_cy * o["place_pct"] / 100.0
        add("Rough Place Topsoil", placed, "CY", f"{o['place_pct']:g} % of the {strip_cy:,.0f} CY stripped", "respread")
    elif o["place_depth_in"] and o["place_depth_in"] > 0 and not area_sf:
        notes.append("Rough Place Topsoil needs a limit for the green area.")
    if placed is not None or strip_cy > 0:
        # what was stripped and not placed back leaves (with nothing typed for the rough place, all
        # of it - the same default the product's balance had); placing more than came off imports
        placed_v = placed or 0.0
        if strip_cy - placed_v > 0.05:
            add("Export Topsoil", (strip_cy - placed_v) * ef, "CY",
                f"{strip_cy:,.0f} CY stripped - {placed_v:,.0f} CY rough placed{fx(ef)}" + ("" if placed is not None else " (nothing typed for the rough place: all of it)"), "export")
        elif placed_v - strip_cy > 0.05:
            add("Import Topsoil", (placed_v - strip_cy) * imf, "CY",
                f"{placed_v:,.0f} CY rough placed - {strip_cy:,.0f} CY stripped{fx(imf)}", "import")
    # ---- the net: cut reused against fill 1:1, the balance leaves or comes in
    if cut > fill:
        add("Export Cut", round(cut - fill) * ef, "CY", f"cut {cut:,.0f} - fill {fill:,.0f} CY{fx(ef)}"
            + (" (the zone rows above, as filed; the totals' net can differ by up to half a yard per row, from rounding)" if parts and totals is not None else ""), "export")
    elif fill > cut:
        add("Import Structural", round(fill - cut) * imf, "CY", f"fill {fill:,.0f} - cut {cut:,.0f} CY{fx(imf)}"
            + (" (the zone rows above, as filed; the totals' net can differ by up to half a yard per row, from rounding)" if parts and totals is not None else ""), "import")
    neat = abs(ef - 1.0) < 1e-9 and abs(imf - 1.0) < 1e-9
    basis = ("neat line: in-place yards between the two surfaces, no swell or shrink applied"
             if neat else f"export x {ef:g}, import x {imf:g} (your factors); the surfaces themselves are neat line")
    return {"rows": rows, "notes": notes, "basis": basis, "fence_from": fence_from, "opts": o,
            "limit": limit, "erosion_ft": r1(ero_ft)}


# ------------------------------------------------------------------ the crew map
def grid_sub(result: dict) -> float:
    """What to take off a stored grid's proposed heights: nothing when the grid carries its section
    (its fg IS the surface the volumes were taken to), the result's section on an older result,
    whose grid kept finished grade while its totals were to subgrade."""
    g = (result or {}).get("grid") or {}
    if g.get("section_ft") is not None:
        return 0.0
    return float((result or {}).get("section_ft") or 0.0)


def grid_section(result: dict) -> float:
    """The section the result's volumes were taken to (0 = finished grade)."""
    g = (result or {}).get("grid") or {}
    v = g.get("section_ft") if g.get("section_ft") is not None else (result or {}).get("section_ft")
    return float(v or 0.0)


def block_k(nx: int, ny: int, cell_ft: float, block_ft: float, cap: int = 2000) -> int:
    """Cells along a block's side: the block asked for, grown until at most `cap` blocks remain."""
    k = max(1, int(round(float(block_ft) / max(float(cell_ft), 1e-6))))
    while ((nx + k - 1) // k) * ((ny + k - 1) // k) > cap:
        k += 1
    return k


def block_depths(grid: dict, cell_ft: float, block_ft: float = 30.0, cap: int = 2000, sub: float = 0.0) -> list[dict]:
    """One printed depth per `block_ft` block of the cut / fill grid: the MEAN depth of the block's
    cells that have both surfaces (each cell by the part of it inside the limit, when the grid
    carries that), placed at those cells' centre. Never one cell's spike. [{x, y, dz, n}] in page
    points; the block grows until at most `cap` labels remain. `sub` is taken off the proposed
    heights (grid_sub: an older result's section)."""
    nx, ny = int(grid["nx"]), int(grid["ny"])
    eg = np.array([np.nan if v is None else v for v in grid["eg"]], dtype=float).reshape(ny, nx)
    fg = np.array([np.nan if v is None else v for v in grid["fg"]], dtype=float).reshape(ny, nx)
    dz = fg - float(sub or 0.0) - eg
    ok = np.isfinite(dz)
    wt = grid.get("w")
    wt = np.array([0.0 if v is None else v for v in wt], dtype=float).reshape(ny, nx) if wt else np.ones((ny, nx))
    cell_pt = float(grid["cell_pt"])
    x0, y0 = float(grid["x0"]), float(grid["y0"])
    k = block_k(nx, ny, cell_ft, block_ft, cap)
    out = []
    for bj in range(0, ny, k):
        for bi in range(0, nx, k):
            m = ok[bj:bj + k, bi:bi + k]
            if not m.any():
                continue
            jj, ii = np.nonzero(m)
            blk = dz[bj:bj + k, bi:bi + k][m]
            ww = wt[bj:bj + k, bi:bi + k][m]
            mean = float((blk * ww).sum() / ww.sum()) if ww.sum() > 0 else float(blk.mean())
            out.append({"x": round(x0 + (bi + ii.mean() + 0.5) * cell_pt, 1), "y": round(y0 + (bj + jj.mean() + 0.5) * cell_pt, 1),
                        "dz": round(mean, 1), "n": int(m.sum())})
    return out


def _font(size: int):
    from PIL import ImageFont
    for path in ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(path, size)
        except (OSError, ValueError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def crew_map_png(render_clip, result: dict, block_ft: float = 30.0, zones: list | None = None,
                 title: str = "Cut / fill map", scale_text: str = "", width_px: int = 2200) -> bytes:
    """The printable crew map: the sheet under the cut / fill grid, red where the ground is cut and
    blue where it is filled (deeper = stronger), one printed depth per `block_ft` block (the block
    mean, C for cut, F for fill, feet), the limit and the zones outlined, the totals in a header.
    `render_clip(x0, y0, x1, y1, dpi)` renders that page rectangle (page points) to PNG bytes."""
    from PIL import Image, ImageDraw
    g = result["grid"]
    cell_pt = float(g["cell_pt"])
    x0, y0 = float(g["x0"]), float(g["y0"])
    x1, y1 = x0 + g["nx"] * cell_pt, y0 + g["ny"] * cell_pt
    pad = 0.04 * max(x1 - x0, y1 - y0) + 2 * cell_pt
    cx0, cy0, cx1, cy1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    dpi = max(40.0, min(200.0, width_px / max((cx1 - cx0) / 72.0, 1e-6)))
    base = Image.open(io.BytesIO(render_clip(cx0, cy0, cx1, cy1, dpi))).convert("RGB")
    # fade the drawing so the colours read
    base = Image.blend(base, Image.new("RGB", base.size, (255, 255, 255)), 0.35)
    sc = dpi / 72.0
    W, H = base.size
    # map page pt -> pixel; the render may have been clipped to the page, so measure the mapping
    sx = W / (cx1 - cx0)
    sy = H / (cy1 - cy0)
    to_px = lambda x, y: ((x - cx0) * sx, (y - cy0) * sy)
    eg = np.array([np.nan if v is None else v for v in g["eg"]], dtype=float).reshape(g["ny"], g["nx"])
    fg = np.array([np.nan if v is None else v for v in g["fg"]], dtype=float).reshape(g["ny"], g["nx"])
    sub = grid_sub(result)          # an older result's grid is at finished grade
    sec = grid_section(result)
    dz = fg - sub - eg
    ok = np.isfinite(dz)
    mag = np.abs(np.where(ok, dz, 0.0))
    nz = mag[ok & (mag > 0.01)]
    ref = float(np.percentile(nz, 95)) if nz.size else 1.0
    ref = ref if ref > 0.01 else 1.0
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for j in range(g["ny"]):
        for i in range(g["nx"]):
            if not ok[j, i]:
                continue
            d = dz[j, i]
            a = int(255 * (0.14 + 0.5 * min(abs(d) / ref, 1.0)))
            col = (198, 40, 40, a) if d < 0 else (21, 101, 192, a)
            px0, py0 = to_px(x0 + i * cell_pt, y0 + j * cell_pt)
            px1, py1 = to_px(x0 + (i + 1) * cell_pt, y0 + (j + 1) * cell_pt)
            ld.rectangle([px0, py0, px1 + 0.5, py1 + 0.5], fill=col)
    img = Image.alpha_composite(base.convert("RGBA"), layer)
    d = ImageDraw.Draw(img)
    # the limit and the zones
    reg = result.get("region") or []
    if len(reg) >= 3:
        d.line([to_px(*p) for p in reg] + [to_px(*reg[0])], fill=(36, 87, 197, 255), width=max(2, int(3 * sc / 1.5)))
    zf = _font(max(12, int(16 * sc / 1.5)))
    names = []
    for z in zones or []:
        P = z.get("polygon") or []
        if len(P) >= 3:
            d.line([to_px(*p) for p in P] + [to_px(*P[0])], fill=(60, 60, 60, 255), width=max(2, int(2.5 * sc / 1.5)))
            cx = sum(p[0] for p in P) / len(P)
            cy = sum(p[1] for p in P) / len(P)
            names.append((to_px(cx, cy), z.get("name") or "zone"))
    # block-averaged printed depths. A block must be wide enough for its own number at the
    # smallest type that still prints: at 30 ft blocks on a 1" = 40' sheet the numbers ran into
    # each other ("C 3.0C 4.7C 0.8"), so the block grows until its number fits and the header
    # says which block was printed
    cell_ft = float(result.get("cell_ft") or 1.0)
    k = block_k(int(g["nx"]), int(g["ny"]), cell_ft, block_ft)
    fmin = 11
    while k * cell_pt * sx < _text_w(d, "C 88.8", _font(fmin)) + 8 and k < max(int(g["nx"]), int(g["ny"])):
        k += 1
    block_eff = k * cell_ft
    labels = block_depths(g, cell_ft, block_eff, sub=sub)
    fs = fmin
    for size in range(26, fmin, -1):
        if _text_w(d, "C 88.8", _font(size)) + 8 <= 0.9 * k * cell_pt * sx:
            fs = size
            break
    f = _font(fs)
    # no number over another, none over a zone's name: the fuller block keeps its number, a thin
    # block at the edge of the grid or of the limit goes without (the header counts them)
    taken = [_text_box(d, xy, nm, zf, 4) for xy, nm in names]
    blank = 0
    for lb in sorted(labels, key=lambda b: -b["n"]):
        txt = f"{'C' if lb['dz'] < 0 else 'F'} {abs(lb['dz']):.1f}"
        box = _text_box(d, to_px(lb["x"], lb["y"]), txt, f, 3)
        if any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in taken):
            blank += 1
            continue
        taken.append(box)
        _halo(d, to_px(lb["x"], lb["y"]), txt, f, (150, 20, 20) if lb["dz"] < 0 else (10, 60, 150))
    for xy, nm in names:
        _halo(d, xy, nm, zf, (40, 40, 40))
    # header
    t = result.get("totals") or {}
    hf = _font(max(14, int(19 * sc / 1.5)))
    sf = _font(max(11, int(14 * sc / 1.5)))
    num = lambda k: round(float(t.get(k) or 0.0))      # noqa: E731 - whole yards; a zero prints as 0, never -0
    tot_txt = (f"cut {num('cut_cy'):,.0f} CY, fill {num('fill_cy'):,.0f} CY, "
               f"net {num('net_cy'):+,.0f} CY (+ fill) on {num('area_sf'):,.0f} SF")
    head = [f"{title}: {tot_txt}"]
    if _text_w(d, head[0], hf) > W - 24:        # the title and the totals on a line each
        head = [f"{title}:", tot_txt]
    blk_txt = (f"a {block_eff:g} ft block" if abs(block_eff - float(block_ft)) < 1e-6
               else f"a {block_eff:g} ft block ({float(block_ft):g} ft asked: its numbers would not fit at this size)")
    parts = ["red = cut, blue = fill (deeper = stronger)", f"each number is the average depth in feet over {blk_txt}"]
    zsec = (result.get("grid") or {}).get("zsec")
    # "to subgrade" only when a cell was lowered: zones all at 0 with no stack are finished grade
    if result.get("zones_itemized") and zsec and (sec or any(float(v or 0.0) != 0.0 for v in zsec)):
        parts.append("depths are to subgrade, each grading zone at its own depth"
                     + (f" (the rest of the limit {sec:g} ft)" if sec and result.get("zones_rest") else "") + ", like the totals")
    elif sec:
        parts.append(f"depths are to subgrade, {sec:g} ft below finished grade, like the totals")
    if blank:
        parts.append(f"{blank} block{'' if blank == 1 else 's'} left without a number (no room: a thin block at an edge, or under a zone's name)")
    parts.append(f"grid {result.get('cell_ft', '?')} ft")
    if scale_text:
        parts.append(f"sheet {scale_text}")
    parts.append(time.strftime("%Y-%m-%d %H:%M"))
    small, cur = [], ""
    for part in parts:                      # the small print wraps instead of running off the sheet
        nxt = part if not cur else cur + "; " + part
        if cur and _text_w(d, nxt, sf) > W - 24:
            small.append(cur)
            cur = part
        else:
            cur = nxt
    if cur:
        small.append(cur)
    top = 8 + int(len(head) * hf.size * 1.4)
    hh = int(top + len(small) * sf.size * 1.6 + 12)
    out = Image.new("RGB", (W, H + hh), (255, 255, 255))
    od = ImageDraw.Draw(out)
    for n, ln in enumerate(head):
        od.text((12, 8 + int(n * hf.size * 1.4)), ln, fill=(20, 20, 20), font=hf)
    for n, ln in enumerate(small):
        od.text((12, top + 2 + int(n * sf.size * 1.6)), ln, fill=(70, 70, 70), font=sf)
    out.paste(img.convert("RGB"), (0, hh))
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _text_w(d, text, font) -> float:
    try:
        bb = d.textbbox((0, 0), text, font=font)
        return float(bb[2] - bb[0])
    except Exception:       # noqa: BLE001
        return len(text) * 7.0


def _text_box(d, xy, text, font, pad: float = 0.0) -> tuple:
    """The box a centred text takes at xy, grown by `pad` px: (x0, y0, x1, y1)."""
    x, y = xy
    try:
        bb = d.textbbox((0, 0), text, font=font)
        w, h = bb[2] - bb[0], bb[3] - bb[1]
    except Exception:       # noqa: BLE001
        w, h = len(text) * 7, 12
    return (x - w / 2 - pad, y - h / 2 - pad, x + w / 2 + pad, y + h / 2 + pad)


def _halo(d, xy, text, font, fill):
    x, y = xy
    try:
        bb = d.textbbox((0, 0), text, font=font)
        w, h = bb[2] - bb[0], bb[3] - bb[1]
    except Exception:
        w, h = len(text) * 7, 12
    px, py = x - w / 2, y - h / 2
    for dx in (-2, 0, 2):
        for dy in (-2, 0, 2):
            d.text((px + dx, py + dy), text, fill=(255, 255, 255), font=font)
    d.text((px, py), text, fill=fill, font=font)
