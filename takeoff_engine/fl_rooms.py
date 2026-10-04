"""THE ROOMS' FIGURES, computed on the server from stored geometry (ruling P, 2026-10-02).

A room is stored as geometry and decisions - its outline, the fill area the pick measured in pt2,
its door openings, the edges left out of its base, the fixtures and shapes taken out of it, its
ceiling height, how its scale was chosen and its floor type. Every figure a document carries is
computed HERE, on every read, from that and the scale in force: square feet, base footage, wall
area, the takeoff rows. Nothing a browser sends is a figure (critic 3, B3: another account's page
write multiplied a room's net square feet by ten and the estimate followed).

Ported from the review panel's own functions (webui/app.js flBase, flTotals, flItems,
flTransitions - the panel now shows these numbers instead of computing them):

  * base = perimeter - the UNION of the openings and the left-out edges, each an interval along
    the outline, so nothing is taken out twice and an opening never takes out more than its edge;
    an opening beside a corner slides along its edge to keep its width (cut only when the edge is
    shorter than the opening);
  * net floor = the fill area - the fixtures ticked out - the shapes clicked out;
  * wall area = perimeter x ceiling height - each opening at door height (6'-8", an inference from
    general residential practice, not read off any sheet).
"""
from __future__ import annotations

import math
from typing import Optional

DOOR_HT_FT = 6.667
BASE_TOL = 1.5            # pt: a left-out run lies along an edge when both its ends are this close to its line
SY_KINDS = ("roll", "sheet")


def poly_area(pts) -> float:
    n = len(pts or [])
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x0, y0 = pts[i][0], pts[i][1]
        x1, y1 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
        s += x0 * y1 - x1 * y0
    return abs(s) / 2.0


def poly_perim(pts) -> float:
    n = len(pts or [])
    if n < 2:
        return 0.0
    return sum(math.hypot(pts[(i + 1) % n][0] - pts[i][0], pts[(i + 1) % n][1] - pts[i][1]) for i in range(n))


def seg_dist(a, b, x, y):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = ((x - a[0]) * dx + (y - a[1]) * dy) / L2 if L2 else 0.0
    t = max(0.0, min(1.0, t))
    return math.hypot(x - (a[0] + t * dx), y - (a[1] + t * dy)), t


def nearest_edge(pts, x, y) -> Optional[dict]:
    best = None
    n = len(pts or [])
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if not L > 0:
            continue
        d, t = seg_dist(a, b, x, y)
        if best is None or d < best["d"]:
            best = {"i": i, "t": t * L, "d": d, "L": L, "at": [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]}
    return best


def point_in(pts, x, y) -> bool:
    inside = False
    n = len(pts or [])
    j = n - 1
    for i in range(n):
        xi, yi = pts[i][0], pts[i][1]
        xj, yj = pts[j][0], pts[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def off_span(a, b, run):
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if not L > 0:
        return None
    ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
    off = lambda P: abs((P[0] - a[0]) * uy - (P[1] - a[1]) * ux)
    p, q = (run[0], run[1]), (run[2], run[3])
    if off(p) > BASE_TOL or off(q) > BASE_TOL:
        return None
    tp = (p[0] - a[0]) * ux + (p[1] - a[1]) * uy
    tq = (q[0] - a[0]) * ux + (q[1] - a[1]) * uy
    lo, hi = max(0.0, min(tp, tq)), min(L, max(tp, tq))
    return (lo, hi) if hi > lo else None


def straight_run(pts, i):
    """The straight run through edge i (a picked outline is a simplified raster ring: one wall is
    several short edges)."""
    n = len(pts)
    a, b = pts[i], pts[(i + 1) % n]
    L = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
    ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
    on = lambda P: abs((P[0] - a[0]) * uy - (P[1] - a[1]) * ux) <= BASE_TOL
    s, e, g = i, (i + 1) % n, 0
    while g < n and on(pts[(s - 1) % n]) and (s - 1) % n != e:
        s = (s - 1) % n
        g += 1
    g = 0
    while g < n and on(pts[(e + 1) % n]) and (e + 1) % n != s:
        e = (e + 1) % n
        g += 1
    return [pts[s][0], pts[s][1], pts[e][0], pts[e][1]]


def opening_pt(o: dict, fpp: Optional[float]) -> float:
    wf = o.get("w_ft")
    if isinstance(wf, (int, float)) and wf > 0 and fpp:
        return float(wf) / fpp
    wp = o.get("w_pt")
    return float(wp) if isinstance(wp, (int, float)) and wp > 0 else 0.0


def base(room: dict, fpp: Optional[float]) -> Optional[dict]:
    """THE BASE OF ONE ROOM at `fpp` feet per pt: {lf, ring_lf, openings: [placed...], openings_lf,
    off_lf, off_runs, out_lf, legacy, unplaced}. None without a scale."""
    if not fpp:
        return None
    pts = room.get("pts") or []
    perim_pt = float(room.get("perim_pt") or 0) or poly_perim(pts)
    ring_lf = perim_pt * fpp
    ops = room.get("openings")
    off = room.get("base_off") or []
    leg = room.get("legacy") or {}
    if not isinstance(ops, list):
        # a room saved by the 2.0 build: the ring less the openings it recorded
        o_pt = float(leg.get("openings_pt") or 0)
        o = o_pt * fpp
        return {"lf": max(0.0, ring_lf - o), "ring_lf": ring_lf, "openings": [], "openings_lf": o, "off_lf": 0.0,
                "off_runs": 0, "out_lf": o, "legacy": True, "unplaced": 0}
    n = len(pts)
    spans = [[] for _ in range(n)]
    placed = []
    for o in ops:
        w = opening_pt(o, fpp)
        e = nearest_edge(pts, float(o.get("x") or 0), float(o.get("y") or 0)) if n >= 3 else None
        if not e or e["d"] > 8 or not w > 0:
            placed.append(dict(o, w_lf=w * fpp, on_edge=False, clipped=False, slid=False))
            continue
        lo, hi, slid = e["t"] - w / 2, e["t"] + w / 2, False
        if w <= e["L"]:
            if lo < 0:
                hi -= lo
                lo = 0.0
                slid = True
            if hi > e["L"]:
                lo -= hi - e["L"]
                hi = e["L"]
                slid = True
        else:
            lo, hi = 0.0, e["L"]
        spans[e["i"]].append((lo, hi))
        placed.append(dict(o, w_lf=(hi - lo) * fpp, on_edge=True, clipped=hi - lo < w - 1e-6,
                           slid=bool(slid and hi - lo >= w - 1e-6)))
    off_pt = 0.0
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for run in off:
            sp = off_span(a, b, run)
            if sp:
                spans[i].append(sp)
                off_pt += sp[1] - sp[0]
    out_pt = 0.0
    for lst in spans:
        lst.sort()
        cur = None
        for lo, hi in lst:
            if cur is None or lo > cur[1]:
                if cur is not None:
                    out_pt += cur[1] - cur[0]
                cur = [lo, hi]
            else:
                cur[1] = max(cur[1], hi)
        if cur is not None:
            out_pt += cur[1] - cur[0]
    openings_lf = sum(p["w_lf"] for p in placed if p["on_edge"])
    return {"lf": max(0.0, ring_lf - out_pt * fpp), "ring_lf": ring_lf, "openings": placed, "openings_lf": openings_lf,
            "off_lf": off_pt * fpp, "off_runs": len(off), "out_lf": out_pt * fpp, "legacy": False,
            "unplaced": sum(1 for p in placed if not p["on_edge"])}


def ceiling(room: dict, aids: dict) -> Optional[dict]:
    c = room.get("ceiling_ft")
    if isinstance(c, (int, float)) and c > 0:
        return {"ft": float(c), "by": room.get("ceiling_by") or "sheet"}
    d = (aids or {}).get("default_ceiling_ft")
    if isinstance(d, (int, float)) and d > 0:
        return {"ft": float(d), "by": "default"}
    return None


def figures(room: dict, fpp: Optional[float], aids: Optional[dict] = None) -> dict:
    """One room's figures at `fpp` feet per pt (None = no scale: pt only)."""
    k2 = fpp * fpp if fpp else None
    area_pt2 = float(room.get("area_pt2") or 0)
    ded_pt2 = 0.0
    ded_words = []
    for o in room.get("objects") or []:
        if o.get("deduct"):
            a = float(o.get("area_pt2") or 0)
            ded_pt2 += a
            ded_words.append((str(o.get("kind") or "shape"), a))
    for d in room.get("deductions") or []:
        a = float(d.get("area_pt2") or 0)
        ded_pt2 += a
        ded_words.append((str(d.get("name") or "clicked out"), a))
    out = {"area_pt2": area_pt2, "deduct_pt2": ded_pt2, "fpp": fpp,
           "area_sf": area_pt2 * k2 if k2 else None,
           "holes_sf": float(room.get("holes_pt2") or 0) * k2 if k2 else None,
           "perim_lf": float(room.get("perim_pt") or 0) * fpp if fpp else None,
           "deduct_sf": ded_pt2 * k2 if k2 else None,
           "net_sf": max(0.0, (area_pt2 - ded_pt2) * k2) if k2 else None,
           "deduct_text": ", ".join(f"{n} {a * k2:.1f} SF" for n, a in ded_words) if k2 else ""}
    out["objects_sf"] = [float(o.get("area_pt2") or 0) * k2 if k2 else 0.0 for o in room.get("objects") or []]
    out["objects_wh"] = [[float(o.get("w_pt") or 0) * fpp, float(o.get("h_pt") or 0) * fpp] if fpp else [0.0, 0.0] for o in room.get("objects") or []]
    out["deductions_sf"] = [float(d.get("area_pt2") or 0) * k2 if k2 else 0.0 for d in room.get("deductions") or []]
    b = base(room, fpp)
    out["base"] = b
    out["base_lf"] = b["lf"] if b else None
    c = ceiling(room, aids or {})
    if c and fpp:
        gross = out["perim_lf"] * c["ft"]
        doors = min(gross, (b["openings_lf"] if b else 0.0) * min(c["ft"], DOOR_HT_FT))
        out["wall"] = {"gross": gross, "net": gross - doors, "doors": doors, "ft": c["ft"], "by": c["by"]}
    else:
        out["wall"] = None
    return out


def ft_in(ft: float) -> str:
    if ft is None or not math.isfinite(ft):
        return "?"
    f = math.floor(ft)
    i = round((ft - f) * 12 * 8) / 8
    if i >= 12:
        f += 1
        i -= 12
    w = math.floor(i)
    fr = i - w
    frs = {0.125: " 1/8", 0.25: " 1/4", 0.375: " 3/8", 0.5: " 1/2", 0.625: " 5/8", 0.75: " 3/4", 0.875: " 7/8"}.get(fr, "") if fr else ""
    return f"{f}'-{w}{frs}\""


def _r1(v: float) -> float:
    return math.floor(v * 10 + 0.5) / 10 if v >= 0 else -math.floor(-v * 10 + 0.5) / 10


def _fmt(v, d=0) -> str:
    if v is None or not math.isfinite(v):
        return "?"
    s = f"{v:,.{d}f}"
    if d and "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def transitions(rooms: list, type_ids: list, closures: list) -> dict:
    """TRANSITION STRIPS: a doorway two typed rooms share where the floor changes type (port of
    the panel's flTransitions: openings are every typed room's bridges plus the gaps closed by hand;
    a room is on an opening's side when its outline comes within a wall's thickness, 9 pt)."""
    out, opn = [], 0
    typed = [(r, i) for i, r in enumerate(rooms) if r.get("type") in type_ids]
    if not typed:
        return {"transitions": out, "open": opn}
    openings, seen = [], {}

    def put(x0, y0, x1, y1, w, src, who=None):
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        key = (round(mx / 3), round(my / 3))
        o = seen.get(key)
        if o is None:
            o = {"mx": mx, "my": my, "w": w, "src": src, "listed": set()}
            seen[key] = o
            openings.append(o)
        if who is not None:
            o["listed"].add(who)

    for r, i in typed:
        for b in r.get("bridges") or []:
            try:
                w = float(b.get("w")) if (b.get("w") or 0) > 0 else float(b.get("r") or 0)
                put(float(b["x0"]), float(b["y0"]), float(b["x1"]), float(b["y1"]), w, "door", i)
            except (KeyError, TypeError, ValueError):
                continue
    for c in closures or []:
        if isinstance(c, list) and len(c) == 4:
            put(c[0], c[1], c[2], c[3], math.hypot(c[2] - c[0], c[3] - c[1]), "closure")

    def edge_dist(pts, x, y):
        best = math.inf
        for i in range(len(pts)):
            d, _t = seg_dist(pts[i], pts[(i + 1) % len(pts)], x, y)
            best = min(best, d)
        return best

    for o in openings:
        sides = []
        for r, i in typed:
            geo = edge_dist(r["pts"], o["mx"], o["my"]) if len(r.get("pts") or []) >= 3 else math.inf
            d = 0.0 if i in o["listed"] else geo
            if d <= 9:
                sides.append((d, i, r["type"]))
        sides.sort()
        if len(sides) < 2:
            opn += 1
            continue
        a = sides[0]
        b = next((s for s in sides if s[2] != a[2]), None)
        if b is None:
            continue
        ta, tb = sorted((type_ids.index(a[2]), type_ids.index(b[2])))
        out.append({"a": a[1], "b": b[1], "ta": ta, "tb": tb, "width_pt": o["w"], "src": o["src"]})
    return {"transitions": out, "open": opn}


def sheet_totals(types: list, rooms: list, figs: list) -> list:
    """Per floor type on one sheet, for the panel's type rows: net SF, base LF, rooms, wall SF, and
    the net and order in the type's bid unit as its takeoff row carries them."""
    out = []
    ids = [t["id"] for t in types]
    for i, t in enumerate(types):
        w = t.get("waste_pct")
        out.append({"i": i, "id": t["id"], "name": t["name"], "color": t.get("color"), "unit": "SY" if t.get("unit") == "SY" else "SF",
                    "kind": t.get("kind") or "", "integral": bool(t.get("integral")),
                    "waste_set": w not in (None, ""), "waste_pct": float(w) if w not in (None, "") else 0.0,
                    "waste_by": t.get("waste_by"), "sf": 0.0, "lf": 0.0, "rooms": 0, "wall_sf": 0.0, "wall_rooms": 0})
    un = {"i": -1, "id": None, "name": "unassigned", "color": None, "unit": "SF", "kind": "", "integral": False, "waste_set": False,
          "waste_pct": 0.0, "waste_by": None, "sf": 0.0, "lf": 0.0, "rooms": 0, "wall_sf": 0.0, "wall_rooms": 0}
    for r, f in zip(rooms, figs):
        g = out[ids.index(r["type"])] if r.get("type") in ids else un
        g["rooms"] += 1
        g["sf"] += f["net_sf"] or 0.0
        g["lf"] += f["base_lf"] or 0.0
        if f.get("wall"):
            g["wall_sf"] += f["wall"]["net"]
            g["wall_rooms"] += 1
    for g in out + [un]:
        g["net"] = g["sf"] / 9.0 if g["unit"] == "SY" else g["sf"]
        g["sy"] = g["sf"] / 9.0
        g["order"] = math.ceil(g["net"] * (1 + g["waste_pct"] / 100) - 1e-9) if g["waste_set"] and g["net"] > 0 else g["net"]
    if un["rooms"]:
        out.append(un)
    return out


def items(types: list, rooms: list, figs: list, aids: dict, sheet_fpp: Optional[float]) -> list:
    """THE TAKEOFF ROWS of one sheet for one account: QUANTITIES ONLY (ruling T: the price of a
    floor type is the estimate's, one place; a flooring row carries no price and the generic price
    book skips it) and no typed product words (ruling S: no manufacturer, colour, style or base code
    in a row that an approved export may carry). Each row is marked src "flooring"."""
    rows = []
    ids = [t["id"] for t in types]
    tot = sheet_totals(types, rooms, figs)
    named = lambda r, i: r.get("name") or f"Room {i + 1}"
    for g in tot:
        if g["i"] < 0 or not g["rooms"]:
            continue
        t = types[g["i"]]
        unit = "SY" if t.get("unit") == "SY" else "SF"
        mine = [(r, i, f) for i, (r, f) in enumerate(zip(rooms, figs)) if r.get("type") == t["id"]]
        waste = t.get("waste_pct")
        w = float(waste) if waste not in (None, "") else None
        conv = (lambda sf: sf / 9.0) if unit == "SY" else (lambda sf: sf)
        if g["sf"] > 0:
            if t.get("legacy_rows"):
                # a type migrated from the 2.0 build keeps its own rows: net, then waste, unrounded
                rows.append({"label": f"Flooring — {t['name']}", "qty": _r1(g["sf"]), "unit": "SF", "src": "flooring",
                             "details": [{"name": named(r, i), "qty": _r1(f["net_sf"] or 0), "unit": "SF"} for r, i, f in mine]})
                if w and w > 0:
                    rows.append({"label": f"Waste — {t['name']} ({_fmt(w, 1)}%)", "qty": _r1(g["sf"] * w / 100), "unit": "SF",
                                 "src": "flooring",
                                 "details": [{"name": f"{_fmt(w, 1)}% of {_fmt(g['sf'], 1)} SF measured", "qty": _r1(g["sf"] * w / 100), "unit": "SF"}]})
            else:
                net_u = conv(g["sf"])
                if w is not None:
                    raw = net_u * (1 + w / 100)
                    qty = math.ceil(raw - 1e-9)
                else:
                    qty = _r1(net_u)
                details = []
                for r, i, f in mine:
                    notes = []
                    if (f["deduct_sf"] or 0) > 0:
                        notes.append(f"{_fmt(f['area_sf'], 1)} SF less {f['deduct_text']}")
                    if unit == "SY":
                        notes.append(f"{_fmt(f['net_sf'], 1)} SF ÷ 9")
                    details.append({"name": named(r, i), "qty": _r1(conv(f["net_sf"] or 0)), "unit": unit,
                                    **({"note": "; ".join(notes)[:80]} if notes else {})})
                parts = sum(d["qty"] for d in details)
                if w is None:
                    diff = _r1(qty - parts)
                    if abs(diff) >= 0.05:
                        details.append({"name": "rounding of the rooms' tenths", "qty": diff, "unit": unit,
                                        "note": "so the parts add up to the row"})
                else:
                    details.append({"name": f"waste {_fmt(w, 1)}%, rounded up to a whole {unit}", "qty": _r1(qty - parts), "unit": unit,
                                    "note": f"net {_fmt(net_u, 2)} + waste = order {_fmt(qty, 1)} {unit}"})
                tail = ("" if w is not None else " (waste not set)") + (" (floor-area order)" if t.get("kind") in SY_KINDS else "")
                rows.append({"label": (f"Flooring — {t['name']}" + tail)[:80], "qty": qty, "unit": unit,
                             "src": "flooring", "details": details})
        if g["lf"] > 0:
            head = "Integral base — " if t.get("integral") else "Base/molding — "
            rows.append({"label": (head + t["name"])[:80], "qty": _r1(g["lf"]), "unit": "LF", "src": "flooring",
                         "details": [{"name": named(r, i), "qty": _r1(f["base_lf"] or 0), "unit": "LF"} for r, i, f in mine]})
    walls = [(r, i, f) for i, (r, f) in enumerate(zip(rooms, figs)) if f.get("wall") and f["wall"]["net"] > 0]
    if walls:
        rows.append({"label": "Wall area — perimeter × ceiling", "qty": _r1(sum(f["wall"]["net"] for _r, _i, f in walls)),
                     "unit": "SF", "src": "flooring",
                     "details": [{"name": named(r, i), "qty": _r1(f["wall"]["net"]), "unit": "SF",
                                  "note": (f"{_fmt(f['perim_lf'], 1)} LF × {ft_in(f['wall']['ft'])} "
                                           + {"sheet": "from the sheet", "typed": "typed"}.get(f["wall"]["by"], "default")
                                           + (f", -{_fmt(f['wall']['doors'])} SF of doors" if f["wall"]["doors"] > 0 else ""))[:80]}
                                 for r, i, f in walls]})
    tr = transitions(rooms, ids, (aids or {}).get("closures") or [])
    by_pair: dict = {}
    for x in tr["transitions"]:
        by_pair.setdefault((x["ta"], x["tb"]), []).append(x)
    for (ta, tb), lst in by_pair.items():
        rows.append({"label": f"Transition strips — {types[ta]['name']} / {types[tb]['name']}"[:80], "qty": len(lst), "unit": "EA",
                     "src": "flooring",
                     "details": [{"name": f"{named(rooms[x['a']], x['a'])} ↔ {named(rooms[x['b']], x['b'])}"[:60], "qty": 1, "unit": "EA",
                                  "note": (f"door {ft_in(x['width_pt'] * sheet_fpp)} wide" if sheet_fpp else f"door {_fmt(x['width_pt'])} pt wide")}
                                 for x in lst]})
    return rows
