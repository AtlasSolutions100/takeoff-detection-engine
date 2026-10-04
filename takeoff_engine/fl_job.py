"""THE FLOORING JOB, one per job and per account: the estimate AND the rooms (rulings H-K, P-W).

The estimate is a document the contractor sends to HIS customer, so it has one source of truth -
this module, on the server - and exact money. Since critic 3 (2026-10-02) the browser owns NO
number and NO state of it:

  * ONE DOCUMENT per job and account (`clean_doc`): the estimate (floor types with their unit,
    rate, waste, packs and words; removal / prep and the typed lines once per job; the estimator's
    notes; the wall ticks by line type) and the rooms of every sheet (outline, the fill's area in
    pt2, openings, edges left out of the base, fixtures and shapes taken out, ceiling, how the scale
    was chosen, the floor type), plus each sheet's pick aids (gaps closed and walls drawn by hand,
    lines ticked). Versioned: every write names the version it read and a stale one is refused
    (critic 3, M3: a stale tab brought a deleted room back).
  * WRITES ARE ACTS (`apply_act` here; the acts that need the drawing - a pick, a traced outline,
    a deduction clicked out, an opening added on a wall - are measured by the server and land here
    as geometry). An act carries what a person did: "room R gets type T", "type T's rate is X",
    "opening O is 3'-0\" wide", "edge E is left out", "room R's scale acknowledged as S". No act
    carries an area, a base, a quantity or a total (critic 3, B3).
  * EVERY FIGURE IS COMPUTED HERE on every read (`compute`, with takeoff_engine/fl_rooms.py):
    square feet, base, order quantities, the takeoff rows, the lines and the total.
  * CHECKS CLOSE BY ACTS, NOT BY FLAGS (ruling R; critic 3, B2: a direct write of "acknowledged"
    flags issued an unpriced carpet line). A price check closes when a rate is set or the line is
    marked no charge; a waste check when a waste is set (0 is a decision); a typed line's check when
    it has a quantity and a rate. Only two checks are acknowledged, each through its own act carrying
    the DIGEST of what the person saw: a room's scale (the scale value) and a room's openings (the
    count). Re-picking, reshaping, re-scaling or re-detecting changes the digest, and the check is
    open again. Nothing in `compute` closes a check.
  * MONEY: rates are whole cents in the bid unit; each line's amount is its PRINTED quantity x the
    rate, half up to the cent; the total is the sum of the printed amounts.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import secrets
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING, InvalidOperation
from typing import Optional

from takeoff_engine import fl_rooms as R

DOC_V = 7                           # 6: rooms sealed by the server (critic 4, B1); 7: the seal binds the sheet and the PDF (critic 5)
UNITS = ("SF", "SY")
PACK_WORDS = ("Boxes", "Cartons", "Rolls", "Pieces")
SF_PER_SY = Decimal(9)
MAX_RATE_CENTS = 10 ** 9            # $10,000,000.00 a unit: anything above is a typing slip
MAX_QTY = Decimal(1000000)          # a typed quantity or a pack size
MAX_TYPES, MAX_LINES, MAX_NOTES = 40, 50, 20
MAX_ROOMS_PAGE = 400
DESC_MAX = 200                      # a typed description; a longer one is refused in words, never cut (critic 4, minor 1)
WORDS = {"SF": "s/f", "SY": "s/y", "LF": "l/f"}
PALETTE = ("#4e9af1", "#f2a33a", "#4fc27a", "#e05c8a", "#b58cf5", "#e8d44d", "#49c7c9", "#c98b5a")
DEFAULT_TYPES = ("Carpet", "LVP", "Tile", "Hardwood")
KINDS = ("", "roll", "sheet", "square", "ceramic", "wood", "other")
# the two waste allowances a flooring contractor stated ("We add on about 7% for waste ... Ceramic
# tile, we add on 15%"; squares "7 to 10%"); every other kind starts with waste NOT SET
WASTE_DEFAULTS = {"square": "7", "ceramic": "15"}
DEDUCT_DEFAULTS = {"tub": True, "shower": True, "cabinet": False, "appliance": False, "toilet": False, "sink": False,
                   "plumbing": False, "furniture": False, "other": False, "shape": False}
WALL_CLASSES = ("exterior", "interior", "shear", "wall (unclassified)", "not a wall")


class ActError(ValueError):
    """An act the document refuses, with the sentence the estimator reads."""


# ---- numbers ----------------------------------------------------------------------------
_GROUPED = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d*)?$")


def dec(v) -> Optional[Decimal]:
    """A finite Decimal from a number or a decimal string, else None. Floats go through repr
    (their shortest decimal), so 0.35 is 0.35, not 0.34999999999999997779. A comma is a thousands
    separator only in a well-formed group ("1,575", "12,345.50"); anything else with a comma
    ("49,75") is refused, never read as 4975 (critic 3, minor 3)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        if isinstance(v, float):
            d = Decimal(repr(v))
        else:
            s = str(v).strip()
            if "," in s:
                if not _GROUPED.match(s):
                    return None
                s = s.replace(",", "")
            d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def cents(v) -> Optional[int]:
    """Whole cents from a typed money amount ("49.75", 49.75, "$1,080"), half up; None for
    blank, zero, negative or absurd - a price of 0 is NO price."""
    if isinstance(v, str):
        v = v.strip().lstrip("$").strip()
    d = dec(v)
    if d is None or d <= 0:
        return None
    c = int((d * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return c if 0 < c <= MAX_RATE_CENTS else None


def money(c: Optional[int]) -> str:
    """Cents as the document prints them: 955200 -> "9,552.00"; None -> ""."""
    if c is None:
        return ""
    neg = c < 0
    c = abs(int(c))
    s = f"{c // 100:,}.{c % 100:02d}"
    return "-" + s if neg else s


def qty_str(q: Decimal, places: int) -> str:
    """The printed quantity: thousands commas, `places` decimals at most, no trailing zeros."""
    q = q.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP) if places else q.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    s = f"{q:,.{places}f}" if places else f"{q:,.0f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def amount_cents(qty_printed: str, rate_c: Optional[int]) -> Optional[int]:
    """THE AMOUNT: the printed quantity x the rate, decimal, half up to the cent."""
    if rate_c is None:
        return None
    q = dec(qty_printed)
    if q is None:
        return None
    return int((q * Decimal(rate_c)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def convert_rate(rate_c: Optional[int], frm: str, to: str) -> Optional[int]:
    """A rate moved to another bid unit, rounded to whole cents (ruling I): $/SY = 9 x $/SF."""
    if rate_c is None or frm == to:
        return rate_c
    r = Decimal(rate_c) * SF_PER_SY if to == "SY" else Decimal(rate_c) / SF_PER_SY
    return int(r.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _icents(v) -> Optional[int]:
    """Stored cents: a positive int up to MAX_RATE_CENTS, else None."""
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v if 0 < v <= MAX_RATE_CENTS else None


_ONE = {"Boxes": "Box", "Cartons": "Carton", "Rolls": "Roll", "Pieces": "Piece"}


def pack_words(n: int, word: str) -> str:
    return f"{n} {_ONE.get(word, word) if n == 1 else word}"


def _ceil_whole(d: Decimal) -> Decimal:
    # a hair under a whole number is that number (binary room areas: 191.99999999 SY is 192)
    r = d.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return r.to_integral_value(rounding=ROUND_CEILING)


def _num(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def parse_ft_in(v) -> Optional[float]:
    """A typed width WITH ITS UNIT: 3'-0\", 3', 3' 6\", 3'-6 1/2\", 3 ft, 3.5 ft, 36\", 36 in -> feet;
    a bare number is refused (None) - "36" took a whole 13 ft wall out as 36 feet (critic 2, M7)."""
    t = str(v if v is not None else "").strip().lower()
    t = re.sub("[′’]", "'", t)
    t = re.sub("[″”]", '"', t)
    t = re.sub(r"\s+", " ", t)
    if not t:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(?:'|ft|feet|foot)\s*-?\s*(?:(\d+(?:\.\d+)?)(?:\s+(\d+)/(\d+))?\s*(?:\"|in|inch|inches)?)?$", t)
    if m:
        inch = float(m.group(2)) + (float(m.group(3)) / float(m.group(4)) if m.group(3) else 0.0) if m.group(2) else 0.0
        return float(m.group(1)) + inch / 12.0
    m = re.match(r"^(\d+(?:\.\d+)?)(?:\s+(\d+)/(\d+))?\s*(?:\"|in|inch|inches)$", t)
    if m:
        return (float(m.group(1)) + (float(m.group(2)) / float(m.group(3)) if m.group(2) else 0.0)) / 12.0
    return None


# ---- state ------------------------------------------------------------------------------
def _txt(v, n):
    s = str(v if v is not None else "")
    s = re.sub(r"[\x00-\x1f\x7f]", " ", s)
    return s[:n]


def _desc(v) -> str:
    t = _txt(v, 100000).strip()
    if len(t) > DESC_MAX:
        raise ActError(f"a description of {len(t)} characters: at most {DESC_MAX} - shorten it (nothing was cut)")
    return t


def _qty_typed(v) -> Optional[str]:
    """A typed quantity as its string: positive, at most 2 decimals, at most 1,000,000."""
    d = dec(v)
    if d is None or d <= 0 or d > MAX_QTY:
        return None
    if d != d.quantize(Decimal("0.01")):
        return None
    return format(d.quantize(Decimal("0.01")).normalize(), "f")


def _pack(p, units):
    if not isinstance(p, dict):
        return None
    size = _qty_typed(p.get("size"))
    unit = p.get("unit") if p.get("unit") in units else None
    word = p.get("word") if p.get("word") in PACK_WORDS else "Boxes"
    return {"size": size, "unit": unit, "word": word} if size and unit else None


def _waste(v) -> Optional[str]:
    d = dec(v)
    if d is None or d < 0 or d > 100:
        return None
    return format(d.quantize(Decimal("0.01")).normalize(), "f")


def _color(v, i=0) -> str:
    s = str(v or "").strip()
    return s if re.match(r"^#[0-9a-fA-F]{6}$", s) else PALETTE[i % len(PALETTE)]


def new_id(prefix="t") -> str:
    return prefix + secrets.token_hex(5)


def clean_type(t, i=0) -> Optional[dict]:
    if not isinstance(t, dict):
        return None
    tid = _txt(t.get("id"), 40).strip()
    if not tid:
        return None
    unit = t.get("unit") if t.get("unit") in UNITS else "SF"
    rs = t.get("rate_src") if isinstance(t.get("rate_src"), dict) else None
    rate_src = {"cents": _icents(rs.get("cents")), "unit": rs.get("unit") if rs.get("unit") in UNITS else unit} if rs else None
    if rate_src and rate_src["cents"] is None:
        rate_src = None
    return {
        "id": tid, "name": _txt(t.get("name"), 40), "color": _color(t.get("color"), i),
        "kind": t.get("kind") if t.get("kind") in KINDS else "", "unit": unit,
        "rate_cents": _icents(t.get("rate_cents")), "base_rate_cents": _icents(t.get("base_rate_cents")),
        "rate_src": rate_src,
        "waste_pct": _waste(t.get("waste_pct")), "waste_by": t.get("waste_by") if t.get("waste_by") in ("typed", "default", "2.0") else None,
        "integral": bool(t.get("integral")),
        "mfr": _txt(t.get("mfr"), 60), "colour": _txt(t.get("colour"), 60), "style": _txt(t.get("style"), 60),
        "base_code": _txt(t.get("base_code"), 30), "base_spec": _txt(t.get("base_spec"), 60),
        "pack": _pack(t.get("pack"), ("SF", "SY")), "base_pack": _pack(t.get("base_pack"), ("LF",)),
        "no_charge": bool(t.get("no_charge")), "base_no_charge": bool(t.get("base_no_charge")),
        "rate_note": _txt(t.get("rate_note"), 160) or None,
        "legacy_rows": bool(t.get("legacy_rows")),
    }


def _area_line(a, d):
    a = a if isinstance(a, dict) else {}
    return {"on": bool(a.get("on")), "desc": _txt(a.get("desc") or d, DESC_MAX), "rate_cents": _icents(a.get("rate_cents"))}


def default_est(with_types: bool = False) -> dict:
    types = []
    if with_types:
        for i, n in enumerate(DEFAULT_TYPES):
            types.append(clean_type({"id": f"t-default-{i}", "name": n}, i))
    return {"types": types, "removal": _area_line(None, "Removal & disposal of existing floor"),
            "prep": _area_line(None, "Floor prep"), "lines": [], "notes": [], "walls": {}}


def clean_est(est) -> dict:
    e = est if isinstance(est, dict) else {}
    types, seen = [], set()
    for i, t in enumerate((e.get("types") or [])[:MAX_TYPES]):
        ct = clean_type(t, i)
        if ct and ct["id"] not in seen:
            seen.add(ct["id"])
            types.append(ct)
    lines = []
    for ln in (e.get("lines") or [])[:MAX_LINES]:
        if not isinstance(ln, dict):
            continue
        lines.append({"id": _txt(ln.get("id"), 40) or f"l{len(lines)}", "desc": _txt(ln.get("desc"), DESC_MAX),
                      "qty": _qty_typed(ln.get("qty")), "rate_cents": _icents(ln.get("rate_cents"))})
    notes = [_txt(n, 200) for n in (e.get("notes") or [])[:MAX_NOTES] if str(n or "").strip()]
    walls = {}
    for k, v in list((e.get("walls") or {}).items())[:300]:
        if isinstance(k, str) and v in ("wall", "not") and 0 < len(k) <= 80:
            walls[k] = v
    return {"types": types, "removal": _area_line(e.get("removal"), "Removal & disposal of existing floor"),
            "prep": _area_line(e.get("prep"), "Floor prep"), "lines": lines, "notes": notes, "walls": walls}


def _pt(p) -> Optional[list]:
    if isinstance(p, (list, tuple)) and len(p) >= 2:
        x, y = _num(p[0]), _num(p[1])
        if x is not None and y is not None:
            return [round(x, 2), round(y, 2)]
    return None


def _pts(v, cap=4000) -> list:
    out = []
    for p in (v or [])[:cap]:
        q = _pt(p)
        if q:
            out.append(q)
    return out


def _seg4(c) -> Optional[list]:
    if isinstance(c, (list, tuple)) and len(c) == 4 and all(_num(v) is not None for v in c):
        return [round(float(v), 2) for v in c]
    return None


def clean_scale(s) -> dict:
    s = s if isinstance(s, dict) else {}
    how = s.get("how") if s.get("how") in ("sheet", "view", "chosen", "zone") else "sheet"
    ft = _num(s.get("ft"))
    choices = []
    for c in (s.get("choices") or [])[:12]:
        if isinstance(c, dict) and _num(c.get("fpi")) and _num(c.get("fpi")) > 0:
            choices.append({"fpi": float(c["fpi"]), "text": _txt(c.get("text"), 80)})
    ct = s.get("contested") if isinstance(s.get("contested"), dict) else None
    typed, was = _num(s.get("typed")), _num(s.get("was_ft"))
    return {"how": how, "ft": ft if ft and ft > 0 else None, "fpi": _num(s.get("fpi")), "text": _txt(s.get("text"), 80) or None,
            "why": _txt(s.get("why"), 400) or None, "multi": bool(s.get("multi")), "choices": choices,
            "ratio": _num(s.get("ratio")) or 1.0, "by": _txt(s.get("by"), 60) or None,
            "contested": {"text": _txt(ct.get("text"), 80), "fpi": _num(ct.get("fpi"))} if ct else None,
            # the scale typed on screen when the room was measured, until the sheet records it (reconcile)
            "typed": typed if typed and typed > 0 else None,
            "sheet": s.get("sheet") if s.get("sheet") in ("none", "amber", "clean") else None,
            "sheet_words": _txt(s.get("sheet_words"), 300) or None,
            # the scale the room stood at before the sheet's scale changed under it: its check is open
            "was_ft": was if was and was > 0 else None,
            "pending": _txt(s.get("pending"), 200) or None}


def _ack(a, keys) -> Optional[dict]:
    if not isinstance(a, dict) or not isinstance(a.get("digest"), str):
        return None
    out = {"digest": a["digest"][:64], "by": _txt(a.get("by"), 120), "at": _txt(a.get("at"), 40)}
    for k in keys:
        if k in a:
            out[k] = a[k] if isinstance(a[k], (int, float, str)) and not isinstance(a[k], bool) else None
    return out


def clean_room(r) -> Optional[dict]:
    """A stored room. Only the server writes rooms (acts and migration); this is the reader's guard."""
    if not isinstance(r, dict):
        return None
    pts = _pts(r.get("pts"))
    if len(pts) < 3:
        return None
    uid = _txt(r.get("uid"), 40).strip() or new_id("r")
    ops = None
    if isinstance(r.get("openings"), list):
        ops = []
        for o in r["openings"][:80]:
            if not isinstance(o, dict) or _num(o.get("x")) is None or _num(o.get("y")) is None:
                continue
            ops.append({"id": _txt(o.get("id"), 20) or new_id("o"), "x": round(float(o["x"]), 2), "y": round(float(o["y"]), 2),
                        "w_pt": round(_num(o.get("w_pt")) or 0.0, 2),
                        "w_ft": _num(o.get("w_ft")) if (_num(o.get("w_ft")) or 0) > 0 else None,
                        "by": _txt(o.get("by") or "added by hand", 40), "why": _txt(o.get("why"), 120) or None})
    objs = []
    for o in (r.get("objects") or [])[:200]:
        if not isinstance(o, dict):
            continue
        objs.append({"kind": _txt(o.get("kind") or "shape", 20), "layer": _txt(o.get("layer"), 60) or None,
                     "area_pt2": max(0.0, _num(o.get("area_pt2")) or 0.0), "w_pt": _num(o.get("w_pt")) or 0.0,
                     "h_pt": _num(o.get("h_pt")) or 0.0, "guessed": bool(o.get("guessed")), "deduct": bool(o.get("deduct")),
                     "manual": bool(o.get("manual")), "pts": _pts(o.get("pts"), 400)})
    deds = []
    for d in (r.get("deductions") or [])[:100]:
        if not isinstance(d, dict):
            continue
        deds.append({"id": _txt(d.get("id"), 20) or new_id("d"), "name": _txt(d.get("name") or "clicked out", 40),
                     "area_pt2": max(0.0, _num(d.get("area_pt2")) or 0.0), "pts": _pts(d.get("pts"), 1000)})
    bridges = []
    for b in (r.get("bridges") or [])[:40]:
        if isinstance(b, dict) and all(_num(b.get(k)) is not None for k in ("x0", "y0", "x1", "y1", "r")):
            bb = {k: round(float(b[k]), 2) for k in ("x0", "y0", "x1", "y1", "r")}
            if (_num(b.get("w")) or 0) > 0:
                bb.update(w=round(float(b["w"]), 2), w_by=_txt(b.get("w_by"), 40) or "from the swing", w_why=_txt(b.get("w_why"), 120) or None)
            bridges.append(bb)
    unplaced = []
    for u in (r.get("doors_unplaced") or [])[:20]:
        if isinstance(u, dict) and _num(u.get("cx")) is not None and _num(u.get("cy")) is not None:
            unplaced.append({"cx": round(float(u["cx"]), 2), "cy": round(float(u["cy"]), 2), "r": round(_num(u.get("r")) or 0.0, 2),
                             "why": _txt(u.get("why"), 120) or None})
    cf = _num(r.get("ceiling_ft"))
    leg = r.get("legacy") if isinstance(r.get("legacy"), dict) else None
    return {
        "uid": uid, "name": _txt(r.get("name"), 60), "source": r.get("source") if r.get("source") in ("click", "drawn", "edited") else "click",
        "click": _pt(r.get("click")), "pts": pts,
        "area_pt2": max(0.0, _num(r.get("area_pt2")) or 0.0), "holes_pt2": max(0.0, _num(r.get("holes_pt2")) or 0.0),
        "n_holes": int(_num(r.get("n_holes")) or 0), "perim_pt": max(0.0, _num(r.get("perim_pt")) or 0.0) or R.poly_perim(pts),
        "bridges": bridges, "doors_unplaced": unplaced, "openings": ops,
        "openings_removed": [q for q in (_pt(o if isinstance(o, list) else [o.get("x"), o.get("y")] if isinstance(o, dict) else None)
                                         for o in (r.get("openings_removed") or [])[:80]) if q],
        "base_off": [s for s in (_seg4(c) for c in (r.get("base_off") or [])[:80]) if s],
        "objects": objs, "deductions": deds, "closures_used": int(_num(r.get("closures_used")) or 0),
        "ceiling_ft": cf if cf and cf > 0 else None, "ceiling_text": _txt(r.get("ceiling_text"), 80) or None,
        "ceiling_by": r.get("ceiling_by") if r.get("ceiling_by") in ("sheet", "typed") else None,
        "scale": clean_scale(r.get("scale")),
        "scale_ack": _ack(r.get("scale_ack"), ("ft", "text")),
        "open_ack": _ack(r.get("open_ack"), ("count",)),
        "type": _txt(r.get("type"), 40) or None,
        "det": _txt(r.get("det"), 40) or None,
        "legacy": {"openings_pt": max(0.0, _num(leg.get("openings_pt")) or 0.0)} if leg else None,
        # the server's seal over what it measured (reconcile); a room whose stored measurements do
        # not match it is measured again from its outline and says so (`unverified`)
        "seal": _txt(r.get("seal"), 64) or None,
        "unverified": _txt(r.get("unverified"), 400) or None,
        # an outline that is not a room on this sheet (off the page, crossing itself, too small or too big): its
        # reason, an open check, and no figure counted (critic 5, B1); set on every read, never by a browser
        "invalid": _txt(r.get("invalid"), 200) or None,
        # what the seal was made against (the PDF's and the key's fingerprints): names the cause when it fails
        "seal_ctx": ({"pdf": _txt(r["seal_ctx"].get("pdf"), 20), "key": _txt(r["seal_ctx"].get("key"), 20)}
                     if isinstance(r.get("seal_ctx"), dict) else None),
        # an earlier build's pick measured again from its outline, acknowledged as it is by the estimator
        "measure_ack": ({"by": _txt(r["measure_ack"].get("by"), 120), "at": _txt(r["measure_ack"].get("at"), 40)}
                        if isinstance(r.get("measure_ack"), dict) else None),
        # left out of the estimate by the estimator, on purpose (an act; on the worksheet)
        "excluded": ({"by": _txt(r["excluded"].get("by"), 120), "at": _txt(r["excluded"].get("at"), 40)}
                     if isinstance(r.get("excluded"), dict) else None),
    }


def clean_aids(a) -> dict:
    a = a if isinstance(a, dict) else {}
    ints = lambda v: [int(x) for x in (v or [])[:2000] if isinstance(x, int) and not isinstance(x, bool)] if isinstance(v, list) else []
    zs = []
    for z in (a.get("zoneScales") or [])[:50]:
        if isinstance(z, dict) and _seg4(z.get("box")) and (_num(z.get("fpi")) or 0) > 0:
            zs.append({"box": _seg4(z["box"]), "fpi": float(z["fpi"]), "text": _txt(z.get("text"), 80)})
    dd = dict(DEDUCT_DEFAULTS)
    for k, v in (a.get("deductDefaults") or {}).items() if isinstance(a.get("deductDefaults"), dict) else []:
        if k in dd and isinstance(v, bool):
            dd[k] = v
    wc = {}
    for k, v in (a.get("wallClass") or {}).items() if isinstance(a.get("wallClass"), dict) else []:
        if str(k).lstrip("-").isdigit() and v in WALL_CLASSES:
            wc[str(int(k))] = v
    dc = _num(a.get("default_ceiling_ft"))
    ws = a.get("wallStyles")
    return {"closures": [s for s in (_seg4(c) for c in (a.get("closures") or [])[:500]) if s],
            "wallLines": [s for s in (_seg4(c) for c in (a.get("wallLines") or [])[:500]) if s],
            "wallAdd": ints(a.get("wallAdd")), "wallDrop": ints(a.get("wallDrop")),
            "wallStyles": ints(ws) if isinstance(ws, list) and ws else None,
            "zoneScales": zs, "deductDefaults": dd,
            "default_ceiling_ft": dc if dc and dc > 0 else None, "wallClass": wc,
            "wallFaces": a.get("wallFaces") if a.get("wallFaces") in (1, 2) else None,
            "wallRows": bool(a.get("wallRows"))}


def default_doc(with_types: bool = False) -> dict:
    return {"v": DOC_V, "est": default_est(with_types), "sheets": {}, "migrated": None}


def clean_doc(d) -> dict:
    d = d if isinstance(d, dict) else {}
    sheets = {}
    for k, s in (d.get("sheets") or {}).items() if isinstance(d.get("sheets"), dict) else []:
        if not str(k).isdigit() or not isinstance(s, dict):
            continue
        rooms, seen = [], set()
        for r in (s.get("rooms") or [])[:MAX_ROOMS_PAGE]:
            cr = clean_room(r)
            if cr and cr["uid"] not in seen:
                seen.add(cr["uid"])
                rooms.append(cr)
        sheets[str(int(k))] = {"rooms": rooms, "aids": clean_aids(s.get("aids")), "seq": int(_num(s.get("seq")) or len(rooms) + 1),
                               "last_type": _txt(s.get("last_type"), 40) or None}
    est = clean_est(d.get("est"))
    ids = {t["id"] for t in est["types"]}
    for s in sheets.values():
        for r in s["rooms"]:
            if r["type"] not in ids:
                r["type"] = None
    mig = d.get("migrated") if isinstance(d.get("migrated"), dict) else None
    return {"v": DOC_V, "est": est, "sheets": sheets, "migrated": mig}


def sheet(doc: dict, page: int) -> dict:
    k = str(int(page))
    if k not in doc["sheets"]:
        doc["sheets"][k] = {"rooms": [], "aids": clean_aids(None), "seq": 1, "last_type": None}
    return doc["sheets"][k]


def find_room(doc: dict, page: int, uid: str) -> dict:
    for r in sheet(doc, page)["rooms"]:
        if r["uid"] == uid:
            return r
    raise ActError("that room is not on this sheet any more - reload to see the current rooms")


def find_type(doc: dict, tid: str) -> dict:
    for t in doc["est"]["types"]:
        if t["id"] == tid:
            return t
    raise ActError("that floor type is not in this estimate any more")


# ---- the room's scale and its checks ----------------------------------------------------
def room_fpp(room: dict) -> Optional[float]:
    ft = (room.get("scale") or {}).get("ft")
    return float(ft) / 72.0 if ft and ft > 0 else None


def scale_words(room: dict) -> str:
    s = room.get("scale") or {}
    how = s.get("how")
    if how == "sheet" or not s.get("text"):
        return "the sheet's scale" + (f" ({s['text']})" if s.get("text") else f" ({_ft_txt(s.get('ft'))})")
    con = s.get("contested")
    return s["text"] + {"view": " - the view title nearest it (an inference)", "chosen": " - chosen by the estimator",
                        "zone": " - its work area's scale"}.get(how, "") + (f" - contested: {con['text']} is as near" if con and how == "view" else "")


def _ft_txt(ft) -> str:
    return f"1\" = {ft:g} ft" if ft else "no scale"


def _h(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()[:20]


def scale_digest(room: dict, stamp) -> str:
    s = room.get("scale") or {}
    return _h(["scale", room["uid"], stamp, room["pts"], s.get("how"), s.get("fpi"),
               round(float(s.get("ft") or 0), 6), s.get("text")])


def openings_digest(room: dict, stamp) -> str:
    ops = room.get("openings")
    lst = [[o["x"], o["y"], o["w_pt"], o["w_ft"], o["by"]] for o in ops] if isinstance(ops, list) else ["legacy", (room.get("legacy") or {}).get("openings_pt")]
    return _h(["openings", room["uid"], stamp, room["pts"], lst, len(room.get("doors_unplaced") or []), room.get("base_off")])


def openings_count(room: dict) -> int:
    ops = room.get("openings")
    return len(ops) if isinstance(ops, list) else 0


def scale_options(room: dict) -> list:
    """Each printed scale the room could be at, with its net square feet there: the check offers
    them side by side, never a lone "confirm" (critic 3, M1)."""
    s = room.get("scale") or {}
    out = []
    if not s.get("multi"):
        # one printed scale and the sheet's scale changed under the room: the one choice is the scale now
        if s.get("was_ft") and s.get("ft"):
            net_pt2 = max(0.0, float(room.get("area_pt2") or 0) - sum(float(o.get("area_pt2") or 0) for o in room.get("objects") or [] if o.get("deduct"))
                          - sum(float(d.get("area_pt2") or 0) for d in room.get("deductions") or []))
            out.append({"fpi": float(s["ft"]), "text": f"1\" = {float(s['ft']):g} ft (the sheet's scale now)", "ft": float(s["ft"]),
                        "net_sf": round(net_pt2 * (float(s["ft"]) / 72.0) ** 2, 1), "current": True})
        return out
    net_pt2 = max(0.0, float(room.get("area_pt2") or 0) - sum(float(o.get("area_pt2") or 0) for o in room.get("objects") or [] if o.get("deduct"))
                  - sum(float(d.get("area_pt2") or 0) for d in room.get("deductions") or []))
    ratio = float(s.get("ratio") or 1.0)
    for c in s.get("choices") or []:
        ft = c["fpi"] * ratio
        out.append({"fpi": c["fpi"], "text": c["text"], "ft": ft, "net_sf": round(net_pt2 * (ft / 72.0) ** 2, 1),
                    "current": abs(ft - float(s.get("ft") or 0)) < 1e-6})
    return out


# ---- what an act may carry, and the figures the server derives on every read --------------
# CRITIC 4, B1: a room_put sent to the act route with "area_pt2": 1000000 on a 10 x 10 pt outline and
# "multi": false issued a $260,630.50 estimate. An act carries GEOMETRY and CHOICES only (an outline,
# a point, an edge, a type id, a typed width or rate, a printed scale chosen); every figure is the
# server's. These keys are figures: an act naming one, at any depth, is refused at the route.
FIGURE_KEYS = frozenset(("area_pt2", "perim_pt", "holes_pt2", "n_holes", "area_sf", "net_sf", "perim_lf", "base_lf", "holes_sf",
                         "deduct_sf", "deduct_pt2", "openings_lf", "openings_pt", "multi", "choices", "ratio", "w_pt", "w_ft",
                         "openings", "bridges", "doors_unplaced", "objects", "deductions", "scale", "seal", "unverified",
                         "open_ack", "scale_ack", "excluded", "amount", "amount_cents", "total", "total_cents", "q", "fig",
                         "typed", "was_ft", "pending"))
# the acts the server makes out of a measurement (_fl_measure); never taken from a browser
SERVER_ONLY_KINDS = frozenset(("room_put", "deduct_put", "opening_put"))
# per kind, a key that is a figure there although a choice elsewhere (room_ceiling's "ft" is a typed height)
_FIGURE_BY_KIND = {"sheet_rescale": ("ft",)}


def figure_fields(act) -> list:
    """The figure keys an act carries (empty when it carries geometry and choices only)."""
    found = []

    def walk(v, depth):
        if depth > 6:
            return
        if isinstance(v, dict):
            for k, x in v.items():
                if k in FIGURE_KEYS:
                    found.append(k)
                walk(x, depth + 1)
        elif isinstance(v, list):
            for x in v[:200]:
                walk(x, depth + 1)

    if isinstance(act, dict):
        walk({k: v for k, v in act.items() if k != "kind"}, 0)
        found += [k for k in _FIGURE_BY_KIND.get(act.get("kind"), ()) if k in act]
    return sorted(set(found))


def seal_body(r: dict) -> str:
    """What the server measured in a room and seals: its outline and source, the fill's area and its
    holes (a pick's area is the fill's own pixel count, which the simplified outline cannot give back
    to the square foot), the fixtures and shapes taken out, the openings and the doors behind them,
    the scale typed on screen, and whether it was measured again for a mismatch."""
    ops = r.get("openings")
    return json.dumps([r["uid"], r["source"], r["pts"], r["area_pt2"], r["holes_pt2"], r["n_holes"],
                       [[o["area_pt2"], o["w_pt"], o["h_pt"], o["pts"]] for o in r["objects"]],
                       [[d["id"], d["area_pt2"], d["pts"]] for d in r["deductions"]],
                       [[o["x"], o["y"], o["w_pt"], o["by"]] for o in ops] if isinstance(ops, list) else None,
                       r["bridges"], r["doors_unplaced"], (r.get("scale") or {}).get("typed"), r.get("unverified"),
                       r.get("legacy")], sort_keys=True, separators=(",", ":"), default=str)


def _band(pts) -> float:
    """How far a filled area may lie from its outline's own area: the fill counts pixels to the ink's
    face, the outline runs through the boundary pixels' centres and is simplified (about a pixel), so
    the two differ by a band along the perimeter - 1.5 pt of it is generous at the pick's 100 dpi."""
    return 1.5 * R.poly_perim(pts) + 0.03 * R.poly_area(pts) + 4.0


def _plausible_room(r: dict) -> bool:
    if r["source"] == "click":
        if abs(r["area_pt2"] + r["holes_pt2"] - R.poly_area(r["pts"])) > _band(r["pts"]):
            return False
    # a fixture's area is the detection's own (its polygon is often a box round it): held to the room, not to its outline
    if sum(o["area_pt2"] for o in r["objects"]) > r["area_pt2"] + 1.0:
        return False
    for d in r["deductions"]:
        if len(d["pts"]) >= 3 and abs(d["area_pt2"] - R.poly_area(d["pts"])) > _band(d["pts"]):
            return False
    return True


def _wh(pts) -> tuple:
    """Width and depth of a shape along its longest edge (pt)."""
    if len(pts) < 2:
        return 0.0, 0.0
    n = len(pts)
    i = max(range(n), key=lambda j: math.hypot(pts[(j + 1) % n][0] - pts[j][0], pts[(j + 1) % n][1] - pts[j][1]))
    dx, dy = pts[(i + 1) % n][0] - pts[i][0], pts[(i + 1) % n][1] - pts[i][1]
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    us = [p[0] * ux + p[1] * uy for p in pts]
    vs = [-p[0] * uy + p[1] * ux for p in pts]
    return max(us) - min(us), max(vs) - min(vs)


# CRITIC 5, B1: an outline was never checked against the sheet - corners at x 5,000 on a 792 pt page issued $39,076
# of tile. Every outline (drawn, edited, picked, migrated) is held to these before a write and on every read.
OUTLINE_MARGIN = 2.0                 # pt outside the page box a corner may sit (a wall line drawn on the sheet's edge)
MIN_AREA_SF, MIN_AREA_PT2 = 1.0, 10.0


def _segs_cross(pts) -> bool:
    """Do any two edges of the closed outline that are not neighbours touch or cross? (vectorised)"""
    import numpy as np
    P = np.asarray(pts, dtype=float)
    n = len(P)
    if n < 4:
        return False
    A, B = P, np.roll(P, -1, axis=0)
    ax, ay, bx, by = A[:, 0][:, None], A[:, 1][:, None], B[:, 0][:, None], B[:, 1][:, None]
    cx, cy, dx, dy = A[:, 0][None, :], A[:, 1][None, :], B[:, 0][None, :], B[:, 1][None, :]

    def orient(px, py, qx, qy, rx, ry):
        v = (qx - px) * (ry - py) - (qy - py) * (rx - px)
        return np.sign(np.where(np.abs(v) < 1e-9, 0.0, v))
    o1, o2 = orient(ax, ay, bx, by, cx, cy), orient(ax, ay, bx, by, dx, dy)
    o3, o4 = orient(cx, cy, dx, dy, ax, ay), orient(cx, cy, dx, dy, bx, by)
    proper = (o1 * o2 < 0) & (o3 * o4 < 0)
    # touching: a corner of one edge lies on the other (collinear and inside its box)
    def on(px, py, qx, qy, rx, ry, o):
        return (o == 0) & (np.minimum(px, qx) - 1e-9 <= rx) & (rx <= np.maximum(px, qx) + 1e-9) \
            & (np.minimum(py, qy) - 1e-9 <= ry) & (ry <= np.maximum(py, qy) + 1e-9)
    touch = on(ax, ay, bx, by, cx, cy, o1) | on(ax, ay, bx, by, dx, dy, o2) | on(cx, cy, dx, dy, ax, ay, o3) | on(cx, cy, dx, dy, bx, by, o4)
    i = np.arange(n)
    adj = (np.abs(i[:, None] - i[None, :]) <= 1) | (np.abs(i[:, None] - i[None, :]) == n - 1)
    hit = (proper | touch) & ~adj
    return bool(hit.any())


def outline_problem(pts, page_wh=None, fpp: Optional[float] = None) -> Optional[str]:
    """Why an outline is not a room on its sheet, in the words the estimator reads; None when it is one."""
    if not isinstance(pts, list) or len(pts) < 3:
        return "an outline needs at least 3 corners"
    clean = []
    for p in pts:
        q = [round(float(p[0]), 2), round(float(p[1]), 2)]
        if not clean or q != clean[-1]:
            clean.append(q)
    if len(clean) > 1 and clean[0] == clean[-1]:
        clean.pop()
    if len({tuple(q) for q in clean}) < 3:
        return "an outline needs at least 3 distinct corners"
    if page_wh and page_wh[0] and page_wh[1]:
        w, h = float(page_wh[0]), float(page_wh[1])
        out = [q for q in clean if not (-OUTLINE_MARGIN <= q[0] <= w + OUTLINE_MARGIN and -OUTLINE_MARGIN <= q[1] <= h + OUTLINE_MARGIN)]
        if out:
            return (f"{len(out)} corner{'s' if len(out) != 1 else ''} off the sheet (the first at x {out[0][0]:g}, y {out[0][1]:g}; "
                    f"the sheet is {w:g} x {h:g} pt)")
    if _segs_cross(clean):
        return "the outline crosses itself (a bow-tie is not a room) - trace it again corner by corner"
    a = R.poly_area(clean)
    if fpp:
        if a * fpp * fpp < MIN_AREA_SF:
            return f"the outline encloses {a * fpp * fpp:.2f} SF - less than {MIN_AREA_SF:g} SF is not a room"
    elif a < MIN_AREA_PT2:
        return f"the outline encloses {a:.1f} pt2 - less than {MIN_AREA_PT2:g} pt2 is not a room"
    if page_wh and page_wh[0] and page_wh[1] and a > float(page_wh[0]) * float(page_wh[1]) + 1e-6:
        return "the outline encloses more than the whole sheet"
    return None


LEGACY_WHY = ("measured again from the outline: re-pick the room to restore its fill measure (it was saved by an earlier "
              "build of this branch, before the seal bound its sheet) - or acknowledge it as it is")
PDF_WHY = ("the drawing behind this job changed since this room was measured (the PDF is not the same file) - "
           "measured again from its outline: re-pick it, or acknowledge it as it is")
KEY_WHY = ("this store's seal key changed since this room was measured (a store restored or moved, or a new key) - "
           "measured again from its outline: re-pick it, or acknowledge it as it is")
KEY_DAMAGED_WHY = "the store's seal key is damaged: ask us - measured again from its outline, held"
ACKABLE_WHYS = ()     # set below, once LEGACY_WHY exists
TAMPER_WHY = ("its stored measurements do not match what the server measured (the file was changed outside the app) - "
              "measured again from its outline")


ACKABLE_WHYS = (LEGACY_WHY, PDF_WHY, KEY_WHY)


def measure_digest(r: dict) -> str:
    return _h(["measure", r["uid"], r["pts"], round(float(r["area_pt2"] or 0), 3), r.get("unverified")])


def _remeasure_from_outline(r: dict, why: str) -> None:
    if r["source"] == "click":
        r["area_pt2"], r["holes_pt2"], r["n_holes"] = R.poly_area(r["pts"]), 0.0, 0
    for o in r["objects"]:
        if len(o["pts"]) >= 3:
            o["area_pt2"] = R.poly_area(o["pts"])
            o["w_pt"], o["h_pt"] = _wh(o["pts"])
        else:
            o["area_pt2"], o["w_pt"], o["h_pt"], o["deduct"] = 0.0, 0.0, 0.0, False
    r["deductions"] = [dict(d, area_pt2=R.poly_area(d["pts"])) for d in r["deductions"] if len(d["pts"]) >= 3]
    r["unverified"] = why


def refigure_room(r: dict, verify=None, legacy: bool = False, trusted: bool = False) -> None:
    """A stored room's figures, derived again (in place) on every read: the perimeter from its
    outline; a drawn or edited room's area from its outline; a picked room's fill area, its holes,
    fixtures, shapes and openings only as the server sealed them - a room whose stored measurements
    do not match the seal (a file changed outside the app) is measured again from its outline and
    keeps an open check. A document of an earlier build (`legacy`, no seals yet) is held to the
    band its outline allows instead. Every opening is held to the edge it sits on."""
    if trusted:
        ok = True                    # verified as it was read, or measured by the server just now
    elif legacy:
        # CRITIC 5, minor 3: no band for an earlier build's document (a +20 % claim hid inside it on a small room).
        # Its picks (and any room with a fill measure in it) are measured again from their outlines, said, and held
        # until re-picked or acknowledged; a drawn room's area is its outline's anyway
        if r["source"] == "click" or r["deductions"] or any(o.get("deduct") for o in r["objects"]):
            _remeasure_from_outline(r, LEGACY_WHY)
        ok = True
    elif verify is not None:             # an unsealed room fails too; the verifier names why (a damaged key, ...)
        got = verify(r)
        ok = got is True
        if not ok and isinstance(got, str):
            _remeasure_from_outline(r, got)
            ok = True                # flagged with its cause
    else:
        ok = _plausible_room(r)
    if not ok:
        _remeasure_from_outline(r, TAMPER_WHY)
    r["perim_pt"] = R.poly_perim(r["pts"])
    if r["source"] != "click":
        r["area_pt2"], r["holes_pt2"], r["n_holes"] = R.poly_area(r["pts"]), 0.0, 0
    if isinstance(r.get("openings"), list):
        for o in r["openings"]:
            e = R.nearest_edge(r["pts"], o["x"], o["y"])
            if e and o["w_pt"] > e["L"]:
                o["w_pt"] = round(e["L"], 2)


def _same(a, b) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) <= 1e-6


def rescale_room(r: dict, facts: dict, zones: list) -> None:
    """A stored room's scale, derived again (in place) on every read from THE SHEET (`facts`, the
    server's: the scale in force - the reviewer's record, else the engine's reading - the engine's
    printed reading, and every printed caption). `multi` and the choices are the sheet's, never a
    browser's. A room measured at a scale typed on screen keeps it only until the sheet records a
    scale: while they differ the room is held (`pending`). A room whose scale moved under it (the
    sheet's scale changed, its view's caption is gone) keeps `was_ft` and its check opens.

    THE MERGE HOOK (page-level scale verdict): `facts` is built by server._fl_sheet_facts; when the
    verdict changes a sheet's scale, every room on it is measured again here on the next read and
    its scale check reopens - nothing else needs to change."""
    sc = r["scale"]
    old = sc.get("ft")
    choices = [{"fpi": float(c["fpi"]), "text": str(c.get("text") or "")} for c in facts.get("choices") or [] if _num(c.get("fpi"))]
    sc["multi"] = bool(facts.get("multi"))
    sc["choices"] = choices
    # how the sheet's scale stands (the page-level verdict): none / amber / clean, with the verdict's words
    sc["sheet"] = facts.get("sheet")
    sc["sheet_words"] = _txt(facts.get("words"), 300) or None
    rec, eng, typed = facts.get("ft"), facts.get("eng"), sc.get("typed")
    sc["pending"] = None
    if typed and not _same(typed, rec):
        base = typed
        sc["pending"] = (f"measured at 1\" = {typed:g} ft typed on screen; the sheet's recorded scale is "
                         + (f"1\" = {rec:g} ft" if rec else "none") + " - held until the sheet's scale is recorded: set it on the "
                         "sheet's scale (a typed or measured scale is recorded at once; one typed in another browser before this "
                         "build lived only there - set it again here)")
    else:
        if typed:
            sc["typed"] = None       # the sheet records it now
        base = rec
    k = (base / eng) if (base and eng) else (1.0 if not base else None)
    sc["ratio"] = k or 1.0
    cx = sum(p[0] for p in r["pts"]) / len(r["pts"])
    cy = sum(p[1] for p in r["pts"]) / len(r["pts"])
    z = next((z for z in zones or [] if min(z["box"][0], z["box"][2]) < cx < max(z["box"][0], z["box"][2])
              and min(z["box"][1], z["box"][3]) < cy < max(z["box"][1], z["box"][3])
              and any(_same(z["fpi"], c["fpi"]) for c in choices)), None)
    if z:
        sc.update(how="zone", fpi=float(z["fpi"]), text=z["text"])
    elif sc["how"] == "zone":
        sc["how"] = "view" if sc.get("fpi") else "sheet"
    if sc["how"] != "sheet" and not any(_same(sc.get("fpi"), c["fpi"]) for c in choices):
        # its view's caption is not printed on the sheet (any more): the sheet's scale, and its check opens
        sc.update(how="sheet", fpi=None, text=None, contested=None)
    if sc["how"] == "sheet" or k is None:
        ft = base
    elif eng and _same(sc["fpi"], eng) and base:
        ft = base
    else:
        ft = float(sc["fpi"]) * k
    sc["ft"] = ft if ft and ft > 0 else None
    if old and sc["ft"] and not _same(old, sc["ft"]):
        sc["was_ft"] = sc.get("was_ft") or old
    if sc.get("was_ft") and _same(sc["was_ft"], sc["ft"]):
        sc["was_ft"] = None


def reconcile(doc: dict, facts: dict, verify=None, legacy: bool = False, trusted: bool = False,
              n_pages: Optional[int] = None) -> dict:
    """Every room's figures and scale derived again from its stored geometry and its sheet (in
    place; returns doc). `facts` {page: the sheet's scale facts (with its page box "wh"), or None
    when the sheet has no detection}; `verify(room, page)` checks the server's seal; `n_pages`: the
    job's page count - a sheet past it is no sheet of this job (critic 5, minor 1). Every outline is
    held to outline_problem; a failing one is flagged and counts in no figure. Called on every read
    of the document and before every write."""
    for k, s in doc["sheets"].items():
        p = int(k)
        f = (facts or {}).get(p)
        for r in s["rooms"]:
            refigure_room(r, (lambda rr, _p=p: verify(rr, _p)) if verify is not None else None, legacy, trusted)
            if f is not None:
                rescale_room(r, f, s["aids"]["zoneScales"])
            if n_pages is not None and p >= int(n_pages):
                r["invalid"] = f"the job has no sheet {p + 1} (it has {int(n_pages)})"
            else:
                r["invalid"] = outline_problem(r["pts"], (f or {}).get("wh"), room_fpp(r))
    return doc


# ---- the computation --------------------------------------------------------------------
def all_rooms(doc: dict) -> list:
    out = []
    for k in sorted(doc["sheets"], key=int):
        for r in doc["sheets"][k]["rooms"]:
            out.append((int(k), r))
    return out


def compute(doc: dict, stamps: Optional[dict] = None, wall_rows: Optional[dict] = None) -> dict:
    """Lines, total, checks, the worksheet, every room's figures and each sheet's takeoff rows, from
    one job's document. `stamps` {page: the page's detection stamp now}: a room measured on an older
    detection has its checks open again. `wall_rows` {page: [rows]}: the centreline wall rows the
    server measured off the detection (they join the sheet's rows)."""
    doc = clean_doc(doc)
    stamps = stamps or {}
    est = doc["est"]
    types = est["types"]
    by_id = {t["id"]: t for t in types}
    rooms_out, figs_by_key = [], {}
    per = {t["id"]: {"rooms": [], "net_sf": Decimal(0), "base_lf": Decimal(0)} for t in types}
    unassigned = []
    pages = {}
    for k in sorted(doc["sheets"], key=int):
        s = doc["sheets"][k]
        p = int(k)
        figs = [R.figures(r, room_fpp(r), s["aids"]) for r in s["rooms"]]
        for r, f in zip(s["rooms"], figs):
            key = f"p{p}:{r['uid']}"
            figs_by_key[key] = f
            rec = {"key": key, "page": p, "uid": r["uid"], "name": r["name"] or "Room", "type": r["type"],
                   "net_sf": Decimal(repr(round(f["net_sf"], 6))) if f["net_sf"] is not None else None,
                   "base_lf": Decimal(repr(round(f["base_lf"], 6))) if f["base_lf"] is not None else Decimal(0),
                   "fig": f, "room": r}
            rooms_out.append(rec)
            if r.get("excluded") or r.get("invalid"):
                continue
            if r["type"] in per and rec["net_sf"] is not None:
                per[r["type"]]["rooms"].append(rec)
                per[r["type"]]["net_sf"] += rec["net_sf"]
                per[r["type"]]["base_lf"] += rec["base_lf"]
            elif rec["net_sf"]:
                unassigned.append(rec)
        sheet_fpp = None
        for r in s["rooms"]:
            if (r.get("scale") or {}).get("how") == "sheet" and room_fpp(r):
                sheet_fpp = room_fpp(r)
                break
        live = [(r, f) for r, f in zip(s["rooms"], figs) if not r.get("excluded") and not r.get("invalid")]
        lr, lf = [r for r, _f in live], [f for _r, f in live]
        rows = R.items(types, lr, lf, s["aids"], sheet_fpp)
        rows += list((wall_rows or {}).get(p) or [])
        tr = R.transitions(lr, [t["id"] for t in types], s["aids"]["closures"])
        pages[p] = {"totals": R.sheet_totals(types, lr, lf), "items": rows, "transitions": tr}
    lines, ws, checks = [], [], []

    def check(cid, kind, text, **kw):
        checks.append(dict({"id": cid, "kind": kind, "text": text, "open": True}, **kw))

    total_net = Decimal(0)
    for t in types:
        g = per[t["id"]]
        if not g["rooms"]:
            continue
        total_net += g["net_sf"]
        unit = t["unit"]
        net_u = g["net_sf"] / SF_PER_SY if unit == "SY" else g["net_sf"]
        w = dec(t["waste_pct"]) if t["waste_pct"] is not None else None
        order = net_u * (1 + (w if w is not None else Decimal(0)) / 100)
        pack = t["pack"]
        if pack:
            size = dec(pack["size"])
            per_pack_u = size / SF_PER_SY if (pack["unit"] == "SF" and unit == "SY") else (size * SF_PER_SY if (pack["unit"] == "SY" and unit == "SF") else size)
            n_packs = int(_ceil_whole(order / per_pack_u))
            q = per_pack_u * n_packs
            qs = qty_str(q, 2)
            packs_txt = f" ({pack_words(n_packs, pack['word'])})"
        else:
            n_packs = None
            q = _ceil_whole(order)
            qs = qty_str(q, 0)
            packs_txt = ""
        words = " ".join(x for x in (t["mfr"], t["style"], ("Color " + t["colour"]) if t["colour"] else "") if x)
        rate = None if t["no_charge"] else t["rate_cents"]
        amt = amount_cents(qs, rate)
        line_id = f"floor:{t['id']}"
        lines.append({"id": line_id, "src": "floor", "type": t["id"],
                      "desc": f"Supply & Install {qs} {WORDS[unit]}{packs_txt} {t['name']}" + (f" {words}" if words else ""),
                      "qty": qs, "rate": money(rate) if rate is not None else ("No charge" if t["no_charge"] else ""),
                      "amount": money(amt) if amt is not None else ("0.00" if t["no_charge"] else ""),
                      "amount_cents": amt if amt is not None else (0 if t["no_charge"] else None)})
        ws.append({"line": line_id, "what": f"{t['name']} floor", "net": f"{net_u.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)} {unit}",
                   "waste": "not set" if w is None else f"{t['waste_pct']}%",
                   "order": f"{order.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)} {unit}",
                   "packs": f"{n_packs} x {pack['size']} {pack['unit']}" if pack else "rounded up to a whole " + unit,
                   "qty": qs, "rooms": g["rooms"]})
        if w is None:
            check(f"waste:{t['id']}", "waste", f"{t['name']}: waste not set (set it, or set 0 on purpose)", type=t["id"])
        if t["rate_cents"] is None and not t["no_charge"]:
            check(f"price:{t['id']}", "price", f"{t['name']}: rooms and no price (type one, or mark the line no charge)", type=t["id"])
        if g["base_lf"] > 0:
            bp = t["base_pack"]
            if bp:
                bsize = dec(bp["size"])
                nb = int(_ceil_whole(g["base_lf"] / bsize))
                bqs = qty_str(bsize * nb, 2)
                btxt = f" ({pack_words(nb, bp['word'])})"
            else:
                nb = None
                bqs = qty_str(_ceil_whole(g["base_lf"]), 0)
                btxt = ""
            what = t["base_code"] or (t["name"] + " base")
            brate = None if t["base_no_charge"] else t["base_rate_cents"]
            bamt = amount_cents(bqs, brate)
            bid = f"base:{t['id']}"
            lines.append({"id": bid, "src": "base", "type": t["id"],
                          "desc": f"Supply & Install {bqs} l/f{btxt} " + ("Integral Base " if t["integral"] else "") + what
                                  + (f" {t['base_spec']}" if t["base_spec"] else ""),
                          "qty": bqs, "rate": money(brate) if brate is not None else ("No charge" if t["base_no_charge"] else ""),
                          "amount": money(bamt) if bamt is not None else ("0.00" if t["base_no_charge"] else ""),
                          "amount_cents": bamt if bamt is not None else (0 if t["base_no_charge"] else None)})
            ws.append({"line": bid, "what": f"{t['name']} base", "net": f"{g['base_lf'].quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)} LF",
                       "waste": "-", "order": f"{g['base_lf'].quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)} LF",
                       "packs": f"{nb} x {bp['size']} LF" if bp else "rounded up to a whole LF", "qty": bqs, "rooms": g["rooms"]})
            if t["base_rate_cents"] is None and not t["base_no_charge"]:
                check(f"baseprice:{t['id']}", "price", f"{t['name']} base: base measured and no price (type one, or mark it no charge)", type=t["id"])
    for a in ("removal", "prep"):
        ln = est[a]
        if not ln["on"]:
            continue
        qs = qty_str(total_net.quantize(Decimal(1), rounding=ROUND_HALF_UP), 0)
        amt = amount_cents(qs, ln["rate_cents"])
        lines.append({"id": a, "src": a, "desc": ln["desc"], "qty": qs, "rate": money(ln["rate_cents"]),
                      "amount": money(amt), "amount_cents": amt})
        ws.append({"line": a, "what": ln["desc"], "net": f"{total_net.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)} SF",
                   "waste": "-", "order": "-", "packs": "the net floor area of every typed room, rounded to a whole SF", "qty": qs, "rooms": []})
        if ln["rate_cents"] is None:
            check(f"price:{a}", "price", f"{ln['desc']}: switched on and no rate")
        if qs == "0":
            # critic 4, M1: "Floor Prep Sand & Skim Coat, 0, 1.25, 0.00" was issued while the room was untyped
            check(f"zero:{a}", "area", f"{ln['desc']}: 0 SF - no typed room has a floor area yet (type the rooms, or switch the line off)")
    for ln in est["lines"]:
        if not ln["desc"].strip():
            continue
        if ln["qty"] is None or ln["rate_cents"] is None:
            check(f"line:{ln['id']}", "line", f"{ln['desc']}: a typed line needs a quantity and a rate")
            amt = None
        else:
            amt = amount_cents(ln["qty"], ln["rate_cents"])
        qs = qty_str(dec(ln["qty"]), 2) if ln["qty"] is not None else ""
        lines.append({"id": f"line:{ln['id']}", "src": "typed", "desc": ln["desc"], "qty": qs,
                      "rate": money(ln["rate_cents"]), "amount": money(amt), "amount_cents": amt})
    # THE ROOMS' CHECKS, for every room in the estimate (a type): its scale on a sheet that prints
    # several, and its openings - each open until acknowledged through its own act, for exactly what
    # the estimator saw (the digest)
    typed_any = False
    for rec in rooms_out:
        r = rec["room"]
        p = rec["page"]
        where = f"sheet {p + 1}, {rec['name']}"
        if r.get("excluded"):
            continue
        if r.get("invalid"):
            check(f"outline:{rec['key']}", "outline", f"{where}: {r['invalid']} - it counts in no figure; trace it again or remove it",
                  room=rec["key"], page=p, uid=r["uid"])
            continue
        if r["type"] not in by_id:
            # critic 4, M1: an untyped room was silently left out of an issued estimate
            check(f"untyped:{rec['key']}", "untyped", f"{where} has no floor type: pick one, or exclude the room from the estimate",
                  room=rec["key"], page=p, uid=r["uid"])
            continue
        typed_any = True
        stamp = stamps.get(p)
        redet = bool(r.get("det") and stamp and r["det"] != stamp)
        if r.get("unverified"):
            ackable = r["unverified"] in ACKABLE_WHYS
            check(f"measure:{rec['key']}", "measure", f"{where}: {r['unverified']}" + ("" if ackable else " - check it, then pick it again or fix its shape"),
                  room=rec["key"], page=p, uid=r["uid"], digest=measure_digest(r) if ackable else None, ackable=ackable)
        if not room_fpp(r):
            sw = r["scale"].get("sheet_words")
            check(f"noscale:{rec['key']}", "noscale",
                  f"{where}: no scale for rooms on this sheet" + (f" - {sw}" if sw else "") + " - set the sheet's scale; the room is measured again at it",
                  room=rec["key"], page=p, uid=r["uid"])
        s = r["scale"]
        if s.get("pending"):
            check(f"unrecorded:{rec['key']}", "scale", f"{where}: {s['pending']}", room=rec["key"], page=p, uid=r["uid"])
        moved = (f"; the sheet's scale changed under it (it was measured at 1\" = {s['was_ft']:g} ft, now 1\" = {s['ft']:g} ft)"
                 if s.get("was_ft") and s.get("ft") else "")
        if (s.get("multi") or moved) and room_fpp(r):
            dg = scale_digest(r, stamp)
            a = r.get("scale_ack")
            ok = bool(a and a.get("digest") == dg) and not s.get("was_ft")
            check(f"scale:{rec['key']}", "scale",
                  f"{where}: measured at {scale_words(r)}" + (" on a sheet that prints several scales" if s.get("multi") else "") + moved
                  + ("; the sheet was detected again since" if redet else "") + " - acknowledge its scale",
                  room=rec["key"], page=p, uid=r["uid"], digest=dg, options=scale_options(r), open=not ok,
                  ack=a if ok else None)
        dg = openings_digest(r, stamp)
        a = r.get("open_ack")
        ok = bool(a and a.get("digest") == dg)
        n = openings_count(r)
        nu = len(r.get("doors_unplaced") or [])
        f = rec["fig"]
        wtxt = ", ".join(R.ft_in(o["w_lf"]) for o in (f["base"]["openings"] if f.get("base") else []) if o.get("on_edge"))
        check(f"open:{rec['key']}", "openings",
              f"{where}: {n} opening{'s' if n != 1 else ''} in its base" + (f" ({wtxt})" if wtxt else "")
              + (f"; {nu} door{'s' if nu != 1 else ''} near it could not be placed" if nu else "")
              + ("; the sheet was detected again since" if redet else "")
              + " - check the doors against the drawing (a door with no record is added by clicking its wall) and confirm",
              room=rec["key"], page=p, uid=r["uid"], digest=dg, count=n, open=not ok, ack=a if ok else None)
    if not typed_any and (lines or rooms_out):
        check("nofloor", "untyped", "no room in this estimate has a floor type: the estimate is held until it has a floor")
    total = sum(x["amount_cents"] or 0 for x in lines)
    # a sheet whose scale is in force but not confirmed (accepted as read, one reading unchecked): said on the
    # worksheet, holding nothing
    sheet_flags = []
    for p, s in sorted((int(k), v) for k, v in doc["sheets"].items()):
        rr = [r for r in s["rooms"] if r["type"] in by_id and not r.get("excluded") and not r.get("invalid")]
        if rr and any((r["scale"] or {}).get("sheet") == "amber" for r in rr):
            w0 = next(((r["scale"] or {}).get("sheet_words") for r in rr if (r["scale"] or {}).get("sheet_words")), None)
            sheet_flags.append({"page": p, "text": f"sheet {p + 1}: sheet scale not confirmed" + (f" - {w0}" if w0 else "")})
    rooms_fig = {}
    for rec in rooms_out:
        f = rec["fig"]
        rooms_fig[rec["key"]] = {k: f[k] for k in ("area_sf", "holes_sf", "perim_lf", "deduct_sf", "net_sf", "base_lf", "deduct_text", "wall",
                                                   "objects_sf", "objects_wh", "deductions_sf")}
        rooms_fig[rec["key"]]["base"] = f["base"]
        rooms_fig[rec["key"]]["scale_options"] = scale_options(rec["room"]) if (rec["room"]["scale"].get("multi") or rec["room"]["scale"].get("was_ft")) else []
        rooms_fig[rec["key"]]["excluded"] = rec["room"].get("excluded")
        rooms_fig[rec["key"]]["scale_words"] = scale_words(rec["room"])
        rooms_fig[rec["key"]]["scale_digest"] = scale_digest(rec["room"], stamps.get(rec["page"]))
        rooms_fig[rec["key"]]["open_digest"] = openings_digest(rec["room"], stamps.get(rec["page"]))
    return {"lines": lines, "total_cents": total, "total": money(total), "checks": checks,
            "open": [c for c in checks if c["open"]], "worksheet": ws,
            "unassigned": [{"key": r["key"], "name": r["name"]} for r in unassigned],
            "sheet_flags": sheet_flags,
            "excluded": [{"key": r["key"], "name": r["name"], "by": r["room"]["excluded"].get("by"), "at": r["room"]["excluded"].get("at")}
                         for r in rooms_out if r["room"].get("excluded")],
            "notes": est["notes"], "rooms": rooms_fig, "pages": pages, "migrated": doc.get("migrated")}


def public(comp: dict) -> dict:
    """The computed reply as the page reads it (Decimals out, rooms by key)."""
    c = dict(comp)
    ws = c.pop("worksheet", None)
    return json.loads(json.dumps(c, default=str))


# ---- acts -------------------------------------------------------------------------------
def _type_index(doc, tid):
    for i, t in enumerate(doc["est"]["types"]):
        if t["id"] == tid:
            return i
    return -1


def apply_act(doc: dict, act: dict, who: str, now: str, stamps: Optional[dict] = None, facts: Optional[dict] = None) -> str:
    """Apply one act of a person to the document (in place). Returns the sentence the status line
    shows; raises ActError with the sentence when the act is refused. The acts that need the drawing
    (a pick, a traced outline, a deduction, an opening found on a wall) are measured by the server
    first and arrive here as geometry (`room_put`, `deduct_put`, `opening_put`)."""
    if not isinstance(act, dict):
        raise ActError("no act")
    kind = act.get("kind")
    if kind not in ACT_KINDS:
        raise ActError(f"unknown act: {str(kind)[:40]}")
    est = doc["est"]
    stamps = stamps or {}
    if kind == "type_add":
        if len(est["types"]) >= MAX_TYPES:
            raise ActError(f"an estimate holds at most {MAX_TYPES} floor types")
        i = len(est["types"])
        t = clean_type({"id": new_id("t"), "name": _txt(act.get("name") or f"Type {i + 1}", 40)}, i)
        est["types"].append(t)
        return f"{t['name']} added"
    if kind == "type_del":
        t = find_type(doc, str(act.get("id")))
        est["types"] = [x for x in est["types"] if x["id"] != t["id"]]
        n = 0
        for s in doc["sheets"].values():
            for r in s["rooms"]:
                if r["type"] == t["id"]:
                    r["type"] = None
                    n += 1
            if s.get("last_type") == t["id"]:
                s["last_type"] = None
        return f"{t['name']} removed" + (f" - {n} room{'s' if n != 1 else ''} have no floor type now" if n else "")
    if kind == "type_set":
        t = find_type(doc, str(act.get("id")))
        return _type_set(t, str(act.get("field") or ""), act.get("value"))
    if kind == "area_set":
        which = act.get("which")
        if which not in ("removal", "prep"):
            raise ActError("no such area line")
        a = est[which]
        f, v = act.get("field"), act.get("value")
        if f == "on":
            a["on"] = bool(v)
        elif f == "desc":
            a["desc"] = _desc(v) or a["desc"]
        elif f == "rate":
            a["rate_cents"] = cents(v) if v not in (None, "") else None
            if v not in (None, "") and a["rate_cents"] is None:
                raise ActError(f'"{v}" is not a rate - type dollars and cents, e.g. 1.25')
        else:
            raise ActError("no such field")
        return f"{a['desc']}: saved"
    if kind == "line_add":
        if len(est["lines"]) >= MAX_LINES:
            raise ActError(f"an estimate holds at most {MAX_LINES} typed lines")
        est["lines"].append({"id": new_id("l"), "desc": _desc(act.get("desc")), "qty": "1", "rate_cents": None})
        return "a typed line added"
    if kind in ("line_set", "line_del", "line_move"):
        lid = str(act.get("id"))
        idx = next((i for i, ln in enumerate(est["lines"]) if ln["id"] == lid), -1)
        if idx < 0:
            raise ActError("that typed line is not in the estimate any more")
        ln = est["lines"][idx]
        if kind == "line_del":
            est["lines"].pop(idx)
            return "the typed line removed"
        if kind == "line_move":
            to = int(_num(act.get("to")) or 0)
            to = max(0, min(len(est["lines"]) - 1, to))
            est["lines"].insert(to, est["lines"].pop(idx))
            return "moved"
        f, v = act.get("field"), act.get("value")
        if f == "desc":
            ln["desc"] = _desc(v)
        elif f == "qty":
            q = _qty_typed(v) if v not in (None, "") else None
            if v not in (None, "") and q is None:
                raise ActError(f'"{v}" is not a quantity - a number, at most 2 decimals')
            ln["qty"] = q
        elif f == "rate":
            c = cents(v) if v not in (None, "") else None
            if v not in (None, "") and c is None:
                raise ActError(f'"{v}" is not a rate - type dollars and cents, e.g. 49.75')
            ln["rate_cents"] = c
        else:
            raise ActError("no such field")
        return "saved"
    if kind == "notes_set":
        est["notes"] = [_txt(n, 200) for n in (act.get("notes") or [])[:MAX_NOTES] if str(n or "").strip()]
        return "notes saved"
    if kind == "walls_mem":
        sig = _txt(act.get("sig"), 80)
        v = act.get("value")
        if not sig:
            raise ActError("no line type")
        if v in ("wall", "not"):
            est["walls"][sig] = v
        else:
            est["walls"].pop(sig, None)
        return "kept with this job's estimate"
    # ---- room acts (page + uid) ----
    page = act.get("page")
    if not isinstance(page, int) or isinstance(page, bool) or page < 0:
        raise ActError("no sheet")
    s = sheet(doc, page)
    if kind == "aids_set":
        key, v = act.get("key"), act.get("value")
        a = dict(s["aids"])
        if key not in a:
            raise ActError("no such setting")
        a[key] = v
        s["aids"] = clean_aids(a)
        if key == "deductDefaults":
            for r in s["rooms"]:
                for o in r["objects"]:
                    if not o["manual"]:
                        o["deduct"] = bool(s["aids"]["deductDefaults"].get(o["kind"])) and not o["guessed"]
        if key == "zoneScales":
            # a work area's scale is one the sheet prints (critic 4, B1): never a number of the browser's
            f = (facts or {}).get(page)
            printed = [c["fpi"] for c in (f or {}).get("choices") or []]
            bad = [z for z in s["aids"]["zoneScales"] if not any(_same(z["fpi"], q) for q in printed)]
            if bad:
                raise ActError(f"{bad[0]['text'] or bad[0]['fpi']} is not a scale this sheet prints - choose one of its captions")
            _apply_zone_scales(s)
        return "saved"
    if kind == "aids_add":
        # one gap closed or one wall drawn, appended on the server: two quick strokes never overwrite each other
        key = act.get("key")
        seg = _seg4(act.get("value"))
        if key not in ("closures", "wallLines") or not seg:
            raise ActError("no such stroke")
        a = dict(s["aids"])
        a[key] = list(a[key]) + [seg]
        s["aids"] = clean_aids(a)
        return f"{len(s['aids'][key])} on this sheet"
    if kind == "aids_line":
        # one line ticked by hand: a wall although its type is not showing, or not a wall although it is
        lid, to = act.get("id"), act.get("to")
        if not isinstance(lid, int) or isinstance(lid, bool) or to not in ("wall", "not", "auto"):
            raise ActError("no such line")
        a = dict(s["aids"])
        a["wallAdd"] = [x for x in a["wallAdd"] if x != lid] + ([lid] if to == "wall" else [])
        a["wallDrop"] = [x for x in a["wallDrop"] if x != lid] + ([lid] if to == "not" else [])
        s["aids"] = clean_aids(a)
        return "saved"
    if kind == "sheet_rescale":
        # critic 4, B1: this act took any number from 0.01 to 100,000 as the sheet's scale. It carries
        # only a printed scale CHOSEN for the rooms measured at the sheet's own scale; with none, the
        # rooms already follow the sheet's recorded scale (reconcile, on every read) and nothing is written
        f = (facts or {}).get(page)
        fpi = _num(act.get("fpi"))
        if fpi is None:
            return "the rooms on this sheet follow its recorded scale: approve the sheet at the scale you mean"
        choice = next((c for c in (f or {}).get("choices") or [] if _same(c["fpi"], fpi)), None)
        if choice is None:
            raise ActError("that is not a scale this sheet prints")
        n = 0
        for r in s["rooms"]:
            sc = r["scale"]
            if sc["how"] == "sheet":
                sc.update(how="chosen", fpi=float(choice["fpi"]), text=str(choice.get("text") or ""))
                n += 1
        return f"{n} room{'s' if n != 1 else ''} on this sheet measured at {choice.get('text')} - each asks for its acknowledgement"
    uid = str(act.get("uid") or "")
    if kind == "room_put":
        room = clean_room(act.get("room"))
        if not room:
            raise ActError("no room")
        if len(s["rooms"]) >= MAX_ROOMS_PAGE and not any(r["uid"] == room["uid"] for r in s["rooms"]):
            raise ActError(f"a sheet holds at most {MAX_ROOMS_PAGE} rooms")
        if not room["name"]:
            room["name"] = f"Room {s['seq']}"
        if room["type"] and _type_index(doc, room["type"]) < 0:
            room["type"] = None
        if room["type"]:
            s["last_type"] = room["type"]
        old = next((i for i, r in enumerate(s["rooms"]) if r["uid"] == room["uid"]), -1)
        if old >= 0:
            s["rooms"][old] = room
        else:
            s["rooms"].append(room)
            s["seq"] += 1
        _apply_zone_scales(s, only=room)
        return room["name"]
    r = find_room(doc, page, uid)
    if kind == "room_del":
        s["rooms"] = [x for x in s["rooms"] if x["uid"] != uid]
        if r["type"] and not s.get("last_type"):
            s["last_type"] = r["type"]          # critic 5, minor 6: a room traced again starts with it
        return f"{r['name']} removed"
    if kind == "measure_ack":
        if r.get("unverified") not in ACKABLE_WHYS:
            raise ActError(f"{r['name']}: nothing to acknowledge here - pick it again or fix its shape")
        if str(act.get("digest") or "") != measure_digest(r):
            raise ActError(f"{r['name']}: it changed since you looked - look again, then acknowledge")
        r["unverified"] = None
        r["measure_ack"] = {"by": _txt(who, 120), "at": now}
        return f"{r['name']}: measured from its outline - acknowledged as it is"
    if kind == "room_name":
        r["name"] = _txt(act.get("name"), 60)
        return "renamed"
    if kind == "room_exclude":
        # critic 4, M1: leaving a room out of the estimate is a decision, recorded on the worksheet
        if act.get("on"):
            r["excluded"] = {"by": _txt(who, 120), "at": now}
            return f"{r['name']}: left out of the estimate (on the worksheet)"
        r["excluded"] = None
        return f"{r['name']}: back in the estimate"
    if kind == "room_type":
        tid = act.get("type")
        if tid in (None, ""):
            r["type"] = None
            return f"{r['name']}: no floor type"
        find_type(doc, str(tid))
        r["type"] = str(tid)
        s["last_type"] = r["type"]
        return f"{r['name']}: {find_type(doc, r['type'])['name']}"
    if kind == "room_ceiling":
        v = act.get("ft")
        f = _num(v) if v not in (None, "") else None
        if v not in (None, "") and not (f and 0 < f < 100):
            raise ActError("a ceiling height in feet, more than 0")
        r["ceiling_ft"] = f
        r["ceiling_by"] = "typed" if f else None
        return "saved"
    if kind == "opening_width":
        oid = str(act.get("oid") or "")
        ops = r.get("openings")
        o = next((x for x in (ops or []) if x["id"] == oid), None)
        if o is None:
            raise ActError("that opening is not on the room any more")
        raw = act.get("value")
        if raw in (None, ""):
            o["w_ft"] = None
            return "width cleared"
        v = parse_ft_in(raw)
        if v is None:
            raise ActError(f'"{raw}" is not a width - type it with its unit: 3\'-0", 3\', 3 ft, 36" or 36 in')
        if not v > 0:
            raise ActError("an opening's width must be more than 0 - remove the opening instead")
        fpp = room_fpp(r)
        e = R.nearest_edge(r["pts"], o["x"], o["y"])
        if fpp and e and v > e["L"] * fpp + 0.01:
            raise ActError(f"{R.ft_in(v)} is wider than the wall it sits on ({R.ft_in(e['L'] * fpp)}) - not taken")
        o["w_ft"] = v
        if o["by"] == "added by hand":
            o["w_pt"] = 0.0
        return f"opening {R.ft_in(v)}"
    if kind == "opening_del":
        oid = str(act.get("oid") or "")
        ops = r.get("openings") or []
        o = next((x for x in ops if x["id"] == oid), None)
        if o is None:
            raise ActError("that opening is not on the room any more")
        r["openings"] = [x for x in ops if x["id"] != oid]
        if o["by"] != "added by hand":
            r["openings_removed"].append([o["x"], o["y"]])
        return "the opening is out: its width is back in the base"
    if kind == "base_toggle":
        x, y = _num(act.get("x")), _num(act.get("y"))
        if x is None or y is None:
            raise ActError("no point")
        e = R.nearest_edge(r["pts"], x, y)
        if not e:
            raise ActError("no edge there")
        at = e["at"]
        hit = next((i for i, run in enumerate(r["base_off"])
                    if R.seg_dist([run[0], run[1]], [run[2], run[3]], at[0], at[1])[0] <= R.BASE_TOL + 1), -1)
        if hit >= 0:
            r["base_off"].pop(hit)
            return f"{r['name']}: that edge is back in the base"
        n = len(r["pts"])
        i = e["i"]
        run = R.straight_run(r["pts"], i) if r["source"] == "click" else [r["pts"][i][0], r["pts"][i][1], r["pts"][(i + 1) % n][0], r["pts"][(i + 1) % n][1]]
        r["base_off"].append([round(v, 2) for v in run])
        fpp = room_fpp(r)
        L = math.hypot(run[2] - run[0], run[3] - run[1])
        return f"{r['name']}: {R.ft_in(L * fpp) if fpp else f'{L:.0f} pt'} left out of the base"
    if kind == "object_toggle":
        oi = act.get("oi")
        if not isinstance(oi, int) or not (0 <= oi < len(r["objects"])):
            raise ActError("no such object")
        o = r["objects"][oi]
        o["deduct"] = bool(act.get("on")) if "on" in act else not o["deduct"]
        o["manual"] = True
        return f"{o['kind']} {'taken out of' if o['deduct'] else 'back in'} the flooring"
    if kind == "deduct_del":
        did = str(act.get("did") or "")
        if not any(d["id"] == did for d in r["deductions"]):
            raise ActError("that deduction is not on the room any more")
        r["deductions"] = [d for d in r["deductions"] if d["id"] != did]
        return "that deduction is back in the flooring"
    if kind == "deduct_put":
        d = act.get("deduction") or {}
        pts = _pts(d.get("pts"), 1000)
        a = _num(d.get("area_pt2"))
        if len(pts) < 3 or not a or a <= 0:
            raise ActError("no shape")
        r["deductions"].append({"id": new_id("d"), "name": _txt(d.get("name") or "clicked out", 40), "area_pt2": a, "pts": pts})
        return "taken out of the flooring"
    if kind == "opening_put":
        o = act.get("opening") or {}
        if _num(o.get("x")) is None or _num(o.get("y")) is None:
            raise ActError("no point")
        if not isinstance(r.get("openings"), list):
            r["openings"] = []
        r["openings"].append({"id": new_id("o"), "x": round(float(o["x"]), 2), "y": round(float(o["y"]), 2),
                              "w_pt": round(_num(o.get("w_pt")) or 0.0, 2), "w_ft": None,
                              "by": _txt(o.get("by") or "added by hand", 40), "why": _txt(o.get("why"), 120) or None})
        return "an opening added"
    if kind == "scale_ack":
        return _scale_ack(r, act, who, now, stamps.get(page))
    if kind == "openings_ack":
        dg = openings_digest(r, stamps.get(page))
        if str(act.get("digest") or "") != dg:
            raise ActError(f"{r['name']}: its openings changed since you looked - look again, then confirm")
        cnt = act.get("count")
        if not isinstance(cnt, int) or cnt != openings_count(r):
            raise ActError(f"{r['name']}: the count you confirmed ({cnt}) is not the {openings_count(r)} openings it has - look again")
        r["open_ack"] = {"digest": dg, "count": cnt, "by": _txt(who, 120), "at": now}
        return f"{r['name']}: {cnt} opening{'s' if cnt != 1 else ''} confirmed"
    raise ActError(f"unknown act: {kind}")


ACT_KINDS = frozenset(("type_add", "type_del", "type_set", "area_set", "line_add", "line_set", "line_del", "line_move",
                       "notes_set", "walls_mem", "aids_set", "aids_add", "aids_line", "sheet_rescale", "room_put", "room_del", "room_name", "room_type",
                       "room_ceiling", "opening_width", "opening_del", "base_toggle", "object_toggle", "deduct_del", "deduct_put",
                       "opening_put", "scale_ack", "openings_ack", "room_exclude", "measure_ack"))


def _scale_ack(r: dict, act: dict, who: str, now: str, stamp) -> str:
    """A room's scale acknowledged AS a value (ruling R): the act names the printed scale the
    estimator saw (fpi) and the digest of the room as it was shown. Naming another printed scale than
    the one in force measures the room at it first (that choice IS the acknowledgement)."""
    s = r["scale"]
    if s.get("pending"):
        raise ActError(f"{r['name']}: {s['pending']}")
    if not s.get("multi") and not s.get("was_ft"):
        raise ActError("this room's sheet prints one scale: nothing to acknowledge")
    dg = scale_digest(r, stamp)
    if str(act.get("digest") or "") != dg:
        raise ActError(f"{r['name']}: its scale or outline changed since you looked - look again, then acknowledge")
    fpi = _num(act.get("fpi"))
    if not s.get("multi"):
        if fpi is None or abs(fpi - float(s["ft"])) > 1e-6:
            raise ActError("that is not the sheet's scale now")
        s["was_ft"] = None
        r["scale_ack"] = {"digest": scale_digest(r, stamp), "ft": float(s["ft"]), "text": f"1\" = {float(s['ft']):g} ft", "by": _txt(who, 120), "at": now}
        return f"{r['name']}: measured at the sheet's scale now (1\" = {float(s['ft']):g} ft) - acknowledged"
    choice = next((c for c in s["choices"] if fpi is not None and abs(c["fpi"] - fpi) < 1e-6), None)
    if choice is None:
        raise ActError("that is not one of the scales this sheet prints")
    ft = choice["fpi"] * float(s.get("ratio") or 1.0)
    s["was_ft"] = None
    if abs(ft - float(s.get("ft") or 0)) > 1e-6:
        s["how"], s["fpi"], s["text"], s["ft"] = "chosen", choice["fpi"], choice["text"], ft
    r["scale_ack"] = {"digest": scale_digest(r, stamp), "ft": ft, "text": choice["text"], "by": _txt(who, 120), "at": now}
    return f"{r['name']}: measured at {choice['text']} - acknowledged"


def _apply_zone_scales(s: dict, only: Optional[dict] = None) -> None:
    """A work-area box that carries a scale: every room whose centre is inside it is measured at it
    (its scale check still asks for the estimator's acknowledgement)."""
    for r in ([only] if only else s["rooms"]):
        cx = sum(p[0] for p in r["pts"]) / len(r["pts"])
        cy = sum(p[1] for p in r["pts"]) / len(r["pts"])
        z = next((z for z in s["aids"]["zoneScales"]
                  if min(z["box"][0], z["box"][2]) < cx < max(z["box"][0], z["box"][2])
                  and min(z["box"][1], z["box"][3]) < cy < max(z["box"][1], z["box"][3])), None)
        sc = r["scale"]
        if z:
            ft = z["fpi"] * float(sc.get("ratio") or 1.0)
            sc.update(how="zone", fpi=z["fpi"], text=z["text"], ft=ft)
        elif sc["how"] == "zone" and not only:
            sc["how"] = "view" if sc.get("fpi") else "sheet"


def _type_set(t: dict, field: str, v) -> str:
    if field == "name":
        t["name"] = _txt(v, 40)
        return "renamed"
    if field == "color":
        t["color"] = _color(v)
        return "colour set"
    if field == "kind":
        t["kind"] = v if v in KINDS else ""
        if t["waste_by"] not in ("typed", "2.0"):
            d = WASTE_DEFAULTS.get(t["kind"])
            t["waste_pct"], t["waste_by"] = (d, "default") if d is not None else (None, None)
        return f"{t['name']}: kind set"
    if field == "unit":
        to = v if v in UNITS else None
        if not to:
            raise ActError("a bid unit is SF or SY")
        frm = t["unit"]
        if to == frm:
            return ""
        c = t["rate_cents"]
        t["unit"] = to
        if c is not None:
            src = t.get("rate_src") or {"cents": c, "unit": frm}
            # a round trip returns the rate as typed (critic 3, minor 4: 49.75/SY -> 5.53/SF -> 49.77/SY)
            nc = src["cents"] if src["unit"] == to else convert_rate(src["cents"], src["unit"], to)
            t["rate_cents"] = nc
            t["rate_src"] = src
            t["rate_note"] = f"${money(c)} / {frm} -> ${money(nc)} / {to}" + ("" if src["unit"] == to else " (rounded to whole cents)")
            return f"{t['name']}: priced per {to} now - {t['rate_note']}"
        t["rate_note"] = None
        return f"{t['name']}: counted and priced per {to} now"
    if field in ("rate", "base_rate"):
        c = cents(v) if v not in (None, "") else None
        if v not in (None, "") and c is None:
            raise ActError(f'"{v}" is not a rate - type dollars and cents, e.g. 49.75')
        if field == "rate":
            t["rate_cents"] = c
            t["rate_src"] = {"cents": c, "unit": t["unit"]} if c is not None else None
            t["rate_note"] = None
        else:
            t["base_rate_cents"] = c
        return "rate saved" if c is not None else "no rate"
    if field == "waste":
        if v in (None, ""):
            t["waste_pct"], t["waste_by"] = None, None
            return "waste not set"
        w = _waste(v)
        if w is None:
            raise ActError(f'"{v}" is not a waste percentage (0 to 100)')
        t["waste_pct"], t["waste_by"] = w, "typed"
        return f"waste {w}%"
    if field in ("integral", "no_charge", "base_no_charge"):
        t[field] = bool(v)
        return "saved"
    if field in ("mfr", "colour", "style", "base_spec"):
        t[field] = _txt(v, 60)
        return "saved"
    if field == "base_code":
        t[field] = _txt(v, 30)
        return "saved"
    if field in ("pack", "base_pack"):
        if v in (None, ""):
            t[field] = None
            return "no packs"
        p = _pack(v, ("SF", "SY") if field == "pack" else ("LF",))
        if p is None:
            raise ActError("a pack size is a number with at most 2 decimals, and its unit")
        t[field] = p
        return "saved"
    raise ActError("no such field")


# ---- the two documents ------------------------------------------------------------------
def customer_csv(comp: dict) -> str:
    """The customer estimate: Description, Qty, Rate, Amount, Total, and the estimator's notes."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Description", "Qty", "Rate", "Amount"])
    for ln in comp["lines"]:
        w.writerow([ln["desc"], ln["qty"], ln["rate"], ln["amount"]])
    w.writerow(["Total", "", "", comp["total"]])
    for n in comp.get("notes") or []:
        w.writerow([])
        w.writerow([n])
    return "﻿" + buf.getvalue()


def worksheet_csv(comp: dict, doc: dict, job_name: str = "") -> str:
    """The internal worksheet: what is behind every line, every room's scale and openings, every
    check and how it closed, and the migration record of a job saved by an earlier build."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Flooring worksheet (internal - not for the customer)", job_name])
    w.writerow([])
    w.writerow(["Line", "What", "Net", "Waste", "Order", "Packs / rounding", "Qty printed"])
    for r in comp["worksheet"]:
        w.writerow([r["line"], r["what"], r["net"], r["waste"], r["order"], r["packs"], r["qty"]])
        for rm in r["rooms"]:
            f = rm["fig"]
            w.writerow(["", f"  sheet {rm['page'] + 1}: {rm['name']}",
                        f"{(rm['net_sf'] or Decimal(0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)} SF net",
                        f"base {rm['base_lf'].quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)} LF",
                        f"scale {scale_words(rm['room'])}",
                        (f"{f['area_sf']:.1f} SF less {f['deduct_text']}" if (f.get("deduct_sf") or 0) > 0 else "")[:120], ""])
            for o in (f["base"]["openings"] if f.get("base") else [])[:40]:
                w.writerow(["", "", "", f"    opening {R.ft_in(o['w_lf'])}" + ("" if o.get("on_edge") else " (not on the outline)"),
                            "typed" if (o.get("w_ft") or 0) > 0 else o.get("by"), o.get("why") or "", ""])
    w.writerow([])
    w.writerow(["Checks", "", "", "", "", "", ""])
    for c in comp["checks"]:
        a = c.get("ack") or {}
        state = "OPEN" if c["open"] else ("acknowledged" if a else "resolved")
        w.writerow([c["kind"], c["text"], state, a.get("text") or (a.get("count") if a.get("count") is not None else ""), a.get("by", ""), a.get("at", ""), ""])
    if comp.get("sheet_flags"):
        w.writerow([])
        w.writerow(["Sheet scale not confirmed (holds nothing)", "", "", "", "", "", ""])
        for x in comp["sheet_flags"]:
            w.writerow(["", x["text"], "", "", "", "", ""])
    if comp.get("excluded"):
        w.writerow([])
        w.writerow(["Excluded from the estimate (on purpose)", "", "", "", "", "", ""])
        for x in comp["excluded"]:
            w.writerow(["", x["name"], x["key"], "", x.get("by") or "", x.get("at") or "", ""])
    mig = (doc or {}).get("migrated")
    if mig:
        w.writerow([])
        w.writerow(["Migrated", mig.get("from", ""), mig.get("at", ""), "", "", "", ""])
        for k, v in (mig.get("before") or {}).items():
            w.writerow(["", k, json.dumps(v, default=str)[:200], "", "", "", ""])
    w.writerow([])
    w.writerow(["Customer total", comp["total"]])
    return "﻿" + buf.getvalue()


# ---- migration of a job saved by an earlier build ----------------------------------------
def _k_at_save(r: dict) -> Optional[float]:
    pl, pp = _num(r.get("perim_lf")), _num(r.get("perim_pt"))
    if pl and pp and pp > 0:
        return pl / pp
    a, ap = _num(r.get("area_sf")), _num(r.get("area_pt2"))
    if a and ap and ap > 0:
        return math.sqrt(a / ap)
    s = _num(r.get("scale_ft_per_in"))
    return s / 72.0 if s else None


def room_from_page(r: dict, i: int, stamp=None) -> Optional[dict]:
    """A room as an earlier build saved it in a page record (2.0, or the round of 2026-10-02),
    as this document stores it: the figures it carried become geometry at the scale they were
    measured at."""
    if not isinstance(r, dict) or not isinstance(r.get("pts"), list) or len(r["pts"]) < 3:
        return None
    k = _k_at_save(r)
    pts = _pts(r["pts"])
    area_pt2 = _num(r.get("area_pt2")) or ((_num(r.get("area_sf")) or 0) / (k * k) if k else R.poly_area(pts))
    holes_pt2 = _num(r.get("holes_pt2")) or ((_num(r.get("holes_sf")) or 0) / (k * k) if k else 0.0)
    perim_pt = _num(r.get("perim_pt")) or ((_num(r.get("perim_lf")) or 0) / k if k else R.poly_perim(pts))
    objs = []
    for o in r.get("objects") or []:
        if not isinstance(o, dict):
            continue
        a = _num(o.get("area_sf"))
        objs.append({"kind": o.get("kind"), "layer": o.get("layer"),
                     "area_pt2": (a / (k * k)) if (a and k) else R.poly_area(o.get("pts") or []),
                     "w_pt": ((_num(o.get("w_ft")) or 0) / k) if k else 0, "h_pt": ((_num(o.get("h_ft")) or 0) / k) if k else 0,
                     "guessed": o.get("guessed"), "deduct": o.get("deduct"), "manual": o.get("manual"), "pts": o.get("pts")})
    deds = []
    for d in r.get("deductions") or []:
        if isinstance(d, dict):
            a = _num(d.get("area_sf"))
            deds.append({"name": d.get("name"), "pts": d.get("pts"),
                         "area_pt2": (a / (k * k)) if (a and k) else R.poly_area(d.get("pts") or [])})
    by = str(r.get("scale_by") or "")
    sv = r.get("scale_verdict") if isinstance(r.get("scale_verdict"), dict) else {}
    ft = _num(r.get("scale_ft_per_in")) or (k * 72.0 if k else None)
    how = "chosen" if by.startswith("inset chosen by hand") else "zone" if by.startswith("work area") else "view" if by.startswith("inset caption") else "sheet"
    choices = [c for c in (sv.get("choices") or []) if isinstance(c, dict)]
    fpi = None
    ratio = 1.0
    for c in choices:
        cft = _num(c.get("ft"))
        if cft and ft and abs(cft - ft) < 1e-6 and _num(c.get("fpi")):
            fpi = float(c["fpi"])
            ratio = cft / fpi
    text = re.sub(r"^(inset caption|inset chosen by hand|work area)\s*", "", by) or None
    if how != "sheet" and fpi is None and ft:
        fpi = ft
    ops = None
    if isinstance(r.get("openings"), list):
        ops = [dict(o, id=new_id("o")) for o in r["openings"] if isinstance(o, dict)]
    legacy = None
    if ops is None and not (r.get("bridges") or []) and not (r.get("base_off") or []):
        legacy = {"openings_pt": ((_num(r.get("openings_lf")) or 0) / k) if k else 0.0}
    elif ops is None:
        # a 2.0 room with bridges: its doors are its openings ("from the swing")
        ops = []
        for b in r.get("bridges") or []:
            if isinstance(b, dict) and all(_num(b.get(q)) is not None for q in ("x0", "y0", "x1", "y1", "r")):
                g = b.get("g") if isinstance(b.get("g"), list) and len(b["g"]) == 4 else [b["x0"], b["y0"], b["x1"], b["y1"]]
                ops.append({"id": new_id("o"), "x": (g[0] + g[2]) / 2, "y": (g[1] + g[3]) / 2,
                            "w_pt": b.get("w") if (_num(b.get("w")) or 0) > 0 else b.get("r"), "w_ft": None,
                            "by": b.get("w_by") or "from the swing", "why": b.get("w_why")})
    return clean_room({
        "uid": r.get("uid") or f"i{i}", "name": r.get("name"), "source": r.get("source"), "click": r.get("click"), "pts": pts,
        "area_pt2": area_pt2, "holes_pt2": holes_pt2, "perim_pt": perim_pt, "bridges": r.get("bridges"),
        "doors_unplaced": r.get("doors_unplaced"), "openings": ops, "openings_removed": r.get("openings_removed"),
        "base_off": r.get("base_off"), "objects": objs, "deductions": deds, "closures_used": r.get("closures"),
        "ceiling_ft": r.get("ceiling_ft"), "ceiling_text": r.get("ceiling_text"), "ceiling_by": r.get("ceiling_by"),
        "scale": {"how": how, "ft": ft, "fpi": fpi, "text": text, "why": sv.get("why"), "multi": bool(sv.get("multi")),
                  "choices": [{"fpi": c.get("fpi"), "text": c.get("text")} for c in choices], "ratio": ratio,
                  # the scale the earlier build measured it at: kept, and held, until the sheet records it
                  "typed": ft if how == "sheet" else None},
        "det": stamp, "legacy": legacy})


def aids_from_page(fl: dict) -> dict:
    return clean_aids({k: fl.get(k) for k in ("closures", "wallLines", "wallAdd", "wallDrop", "wallStyles", "zoneScales",
                                              "deductDefaults", "default_ceiling_ft", "wallClass", "wallFaces", "wallRows")})


def legacy_money(page_states: dict, prices_by_page: dict) -> dict:
    """What a 2.0 build's own takeoff rows priced the flooring at, from its page records: per type
    net SF (+ its waste row) at its $/SF and base LF at its $/LF, each row's qty rounded to a tenth
    as 2.0 printed it. Recorded on the worksheet when the job is migrated."""
    out = {"rows": [], "money": 0.0}
    for p, fl in sorted(page_states.items()):
        types = fl.get("types") if isinstance(fl.get("types"), list) else []
        rooms = fl.get("rooms") if isinstance(fl.get("rooms"), list) else []
        book = prices_by_page.get(p) or {}
        for i, t in enumerate(types):
            if not isinstance(t, dict):
                continue
            mine = [r for r in rooms if isinstance(r, dict) and r.get("type") == i]
            if not mine:
                continue
            name = str(t.get("name") or f"Type {i + 1}")
            net = sum(max(0.0, (_num(r.get("area_sf")) or 0) - sum((_num(o.get("area_sf")) or 0) for o in (r.get("objects") or []) if isinstance(o, dict) and o.get("deduct"))
                          - sum((_num(d.get("area_sf")) or 0) for d in (r.get("deductions") or []) if isinstance(d, dict))) for r in mine)
            base = sum(max(0.0, (_num(r.get("perim_lf")) or 0) - (_num(r.get("openings_lf")) or 0)) for r in mine)
            sfp = _num(t.get("sf_price")) or _num(book.get(f"Flooring — {name}")) or 0.0
            lfp = _num(t.get("lf_price")) or _num(book.get(f"Base/molding — {name}")) or 0.0
            wp = _num(t.get("waste_pct")) or 0.0
            for lab, q, pr in ((f"Flooring — {name}", round(net, 1), sfp),
                               (f"Waste — {name} ({wp:g}%)", round(net * wp / 100, 1) if wp > 0 else 0.0, sfp),
                               (f"Base/molding — {name}", round(base, 1), lfp)):
                if q > 0:
                    ext = round(q * pr, 2) if pr else 0.0
                    out["rows"].append({"page": p, "label": lab, "qty": q, "price": pr or None, "extended": ext})
                    out["money"] = round(out["money"] + ext, 2)
    return out


def migrate(page_states: dict, old_est: Optional[dict], prices_by_page: dict, stamps: dict, now: str) -> Optional[dict]:
    """ONE migration of a job saved by an earlier build into this document (ruling Q), or None
    when the job has no flooring of an earlier build. `old_est`: the per-account estimate the round
    of 2026-10-02 stored (its types, lines, notes, wall ticks and which type each room was). A 2.0
    page record carries its own types and prices instead. Label prices of the 2.0 takeoff panel
    (`prices_by_page`) price a type that has none of its own - once, here; never again (ruling T)."""
    has_rooms = any(isinstance(fl.get("rooms"), list) and fl["rooms"] for fl in page_states.values())
    has_types = any(isinstance(fl.get("types"), list) and fl["types"] for fl in page_states.values())
    if not (has_rooms or has_types or old_est):
        return None
    doc = default_doc()
    src = []
    if isinstance(old_est, dict):
        e = dict(old_est)
        doc["est"] = clean_est(e)
        assign = e.get("assign") if isinstance(e.get("assign"), dict) else {}
        acks = e.get("acks") if isinstance(e.get("acks"), dict) else {}
        src.append("the estimate saved on 2026-10-02 (its price, waste and line acknowledgements are not carried: they close by their data now)")
    else:
        assign, acks = {}, {}
    seen = {t["id"] for t in doc["est"]["types"]}
    by_name, used_ids = {}, set()
    for p, fl in sorted(page_states.items()):
        tys = fl.get("types") if isinstance(fl.get("types"), list) else []
        book = prices_by_page.get(p) or {}
        idmap = {}
        used_here = {r.get("type") for r in (fl.get("rooms") or []) if isinstance(r, dict) and isinstance(r.get("type"), int)}
        if not old_est:
            for i, t in enumerate(tys):
                if not isinstance(t, dict):
                    continue
                name = str(t.get("name") or f"Type {i + 1}")
                unit = "SY" if t.get("unit") == "SY" else "SF"
                fp = t.get("price")
                if not _num(fp):
                    sfp = _num(t.get("sf_price"))
                    fp = (sfp * 9 if unit == "SY" else sfp) if sfp and sfp > 0 else None
                if not _num(fp):
                    fp = _num(book.get(f"Flooring — {name}"))
                bp = t.get("base_price") if _num(t.get("base_price")) else (_num(t.get("lf_price")) or _num(book.get(f"Base/molding — {name}")))
                tid = str(t.get("id") or f"p{p}t{i}")
                idmap[i] = tid
                if tid in seen:
                    continue
                is20 = "sf_price" in t or "lf_price" in t or ("price" not in t and "unit" not in t)
                cl = t.get("carton_lf")
                wp = t.get("waste_pct")
                # A 2.0 page kept its own list of types: the same "Carpet" on two sheets is ONE floor type of the
                # job when nothing contradicts it (a price or waste only one sheet set is taken; a type no room on
                # a sheet uses decides nothing). Two that disagree stay two, named by their sheet.
                in_use = i in used_here
                twin = by_name.get((name.lower(), unit))
                if twin is not None:
                    et = next(x for x in doc["est"]["types"] if x["id"] == twin)
                    wpd = _waste(wp if _num(wp) is not None else 0) if is20 else _waste(wp)
                    ok = ((et["rate_cents"] is None or cents(fp) is None or et["rate_cents"] == cents(fp))
                          and (et["base_rate_cents"] is None or cents(bp) is None or et["base_rate_cents"] == cents(bp))
                          and (not in_use or twin not in used_ids or et["waste_pct"] == wpd))
                    if ok:
                        idmap[i] = twin
                        if et["rate_cents"] is None and cents(fp) is not None:
                            et["rate_cents"], et["rate_src"] = cents(fp), {"cents": cents(fp), "unit": unit}
                        if et["base_rate_cents"] is None and cents(bp) is not None:
                            et["base_rate_cents"] = cents(bp)
                        if in_use and twin not in used_ids:
                            et["waste_pct"], et["waste_by"] = wpd, ("2.0" if is20 else et["waste_by"])
                        if in_use:
                            used_ids.add(twin)
                        continue
                    name = f"{name} (sheet {p + 1})"
                seen.add(tid)
                by_name.setdefault((name.lower(), unit), tid)
                if in_use:
                    used_ids.add(tid)
                doc["est"]["types"].append(clean_type({
                    "id": tid, "name": name, "color": t.get("color"), "kind": t.get("kind"), "unit": unit,
                    "rate_cents": cents(fp), "base_rate_cents": cents(bp),
                    "rate_src": {"cents": cents(fp), "unit": unit} if cents(fp) else None,
                    "waste_pct": wp if (wp is not None and not is20) else (wp if _num(wp) is not None else 0),
                    "waste_by": "2.0" if is20 else t.get("waste_by"),
                    "integral": t.get("integral"), "mfr": t.get("mfr"), "colour": t.get("colour"), "style": t.get("style"),
                    "base_code": t.get("base_code"), "base_spec": t.get("base_spec"),
                    "base_pack": {"size": cl, "unit": "LF", "word": "Cartons"} if cl else None,
                    "legacy_rows": is20}, len(doc["est"]["types"])))
            if isinstance(fl.get("est"), dict):
                old = fl["est"]
                for a in ("removal", "prep"):
                    o = old.get(a) if isinstance(old.get(a), dict) else {}
                    if o.get("on") and not doc["est"][a]["on"]:
                        doc["est"][a] = {"on": True, "desc": _txt(o.get("desc") or doc["est"][a]["desc"], DESC_MAX), "rate_cents": cents(o.get("rate"))}
                for k2, ln in enumerate(old.get("lines") or []):
                    if isinstance(ln, dict) and str(ln.get("desc") or "").strip():
                        doc["est"]["lines"].append({"id": f"p{p}l{k2}", "desc": _txt(ln.get("desc"), DESC_MAX), "qty": _qty_typed(ln.get("qty")),
                                                    "rate_cents": cents(ln.get("rate"))})
        s = sheet(doc, p)
        s["aids"] = aids_from_page(fl)
        for i, r in enumerate(fl.get("rooms") or []):
            room = room_from_page(r, i, stamps.get(p))
            if not room:
                continue
            if old_est:
                room["type"] = assign.get(f"p{p}:{room['uid']}")
                a = acks.get(f"scale:p{p}:{room['uid']}")
                if isinstance(a, dict) and room["scale"]["multi"] and a.get("what") and a.get("what") == room["scale"].get("text"):
                    room["scale_ack"] = {"digest": scale_digest(room, stamps.get(p)), "ft": room["scale"]["ft"],
                                         "text": a.get("what"), "by": _txt(a.get("by"), 120), "at": _txt(a.get("at"), 40)}
            else:
                ti = r.get("type") if isinstance(r, dict) else None
                room["type"] = idmap.get(ti) if isinstance(ti, int) else None
            s["rooms"].append(room)
        s["seq"] = len(s["rooms"]) + 1
        s["last_type"] = next((r["type"] for r in reversed(s["rooms"]) if r.get("type")), None)
    if not old_est:
        src.append("the page records of the 2.0 build (types, prices and each room's type; label prices of its takeoff panel "
                   "price a type that had none, once)")
    doc = clean_doc(doc)
    before = legacy_money(page_states, prices_by_page) if not old_est else {}
    taken = [x for b in (prices_by_page or {}).values() if isinstance(b, dict) for x in (b.get("\u0000from") or [])]
    unpriced = sorted({t["name"] for t in doc["est"]["types"] if t["rate_cents"] is None
                       and any(r["type"] == t["id"] for s in doc["sheets"].values() for r in s["rooms"])})
    doc["migrated"] = {"from": "; ".join(src), "at": now,
                       "before": dict({"2.0 takeoff rows (qty x price, as 2.0 printed them)": before.get("rows", []),
                                       "2.0 flooring money in the takeoff": before.get("money")} if before else {},
                                      **({"prices taken from approved exports": taken} if taken else {}),
                                      **({"no price found (2.0 kept a typed price in the browser's own price book, which this "
                                          "server cannot read; no approved export carried one)": unpriced} if unpriced else {}))}
    return doc
