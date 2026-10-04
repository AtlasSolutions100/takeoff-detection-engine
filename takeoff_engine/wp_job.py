"""THE WALL PANEL JOB, one per job and per account: the takeoff sheet, the costs and the proposal.

A wall-panel installer (FRP and other sheet goods) prices a job from rooms, sheets and a handful of
costs, and sends his customer a proposal. Built on the flooring estimate's rules
(takeoff_engine/fl_job.py); the browser owns NO number here either:

  * ONE DOCUMENT per job and account (`clean_doc`): the rooms (name, number, panel height, the
    product's words, the molding type, the wall lengths as typed and the walls picked on a sheet as
    GEOMETRY), the sheet rows (size, count, price a sheet and where the price came from), the typed
    lines (moldings, adhesive: what, quantity, unit, price), the job's own inputs (days, hours a day,
    miles, nights, freight, a labor rate or a profit typed for this job), the proposal's date and
    the blanks only a person can know, and THE RATES THE JOB STANDS ON - a copy of the account's
    wall panel rates (wp_settings.snapshot) taken when the job is first written, and again only
    when a person says so. An account that changes its rates later does not move a job it already
    priced; the job says what differs and offers the rates as they are now. A rate the copy does
    NOT hold (the job was started before the account set it) follows the account's settings until
    the job is next written and takes it (wp_settings.standing, take_unset): the job says which
    rates those are.
  * THE PROPOSAL'S SENTENCE NEVER DISAGREES WITH THE JOB (critic wp-1, BLOCKER-1; `scope`): every
    blank the job itself knows - the number of sheets and the sizes (its sheet rows), the product
    words (its rooms) - is filled HERE from the job as it is now, on every read, and can not be
    typed over. Only a blank no part of the job holds is typed for the job.
  * WRITES ARE ACTS (`apply_act`): "room R's height is 8'", "sheet row S counts 12", "this job is
    3 days". An act names only what a person did; an act that carries anything else is refused at
    the route (`act_problem`), a figure by name. A wall picked or drawn on a sheet arrives as a
    point, a line's id or a segment; the server measures it.
  * EVERY FIGURE IS COMPUTED HERE on every read (`compute`): each wall's feet at the scale of THE
    VIEW IT IS DRAWN IN (the sheet's scale in force where the sheet prints one scale; none in
    force, a view that is not known or two views as near = nothing in feet, with the reason), each
    room's wall length and its SUGGESTED sheet count (geometry only: offered with its arithmetic,
    never applied), every line and the total.
  * MONEY: Decimal, whole cents. Each line prints a quantity and a rate; its amount is the PRINTED
    quantity x the PRINTED rate, half up to the cent; the total is the sum of the printed amounts.
  * NO NUMBER OF ANY INSTALLER IS IN THIS FILE. Every rate and threshold is the account's
    (wp_settings); a line whose rate is not set, or whose quantity is not typed, says so and holds
    the proposal. 0 is a decision; blank is not.

THE FORMULAS (the installer's own, as an earlier takeoff sheet of his computed them):
    sheets      count x price a sheet, per row (a price is typed, or a price item's: per sheet, or
                per square foot x the sheet's area, half up to the cent)
    labor       days x hours a day x the hourly rate
    overtime    (hours a day - the daily threshold, never less than 0) x days x the overtime rate
    travel      miles x the amount a mile
    hotel       nights x the amount a night
    freight     the amount typed
    profit      the flat amount when the job's sheets are fewer than the threshold, otherwise the
                amount a day x days; a profit typed for the job replaces it and is shown as typed
    total       the sum of the printed lines
"""
from __future__ import annotations

import csv
import datetime
import io
import math
import re
from decimal import Decimal
from fractions import Fraction
from typing import Optional

from takeoff_engine import fl_rooms as R
from takeoff_engine import wp_settings as S
from takeoff_engine.fl_job import ActError, amount_cents, money, new_id, qty_str

DOC_V = 1
MAX_ROOMS, MAX_WALLS, MAX_SHEETS, MAX_LINES, MAX_TERMS = 300, 200, 60, 60, 200
MAX_COUNT = 100000
MAX_QTY = Decimal(1000000)
GEOMETRY_ONLY = "geometry only: no waste, no openings"
SET_IT = "set it in Settings, Wall panel rates"
# a room's text fields and how long each may be
ROOM_TEXT = {"name": 60, "number": 20, "mfr": 60, "colour": 60, "code": 40, "fire": 40, "thickness": 30, "molding": 40}
JOB_TEXT = {"project": 120, "location": 160}
JOB_QTY = {"days": 366, "hours": 24, "miles": 100000, "nights": 366}
# a named blank of the scope sentence that the rooms can fill: blank name -> (room field, its words)
ROOM_BLANKS = {"thickness": ("thickness", "panel thickness"), "fire_rating": ("fire", "fire rating"),
               "colour": ("colour", "colour"), "color": ("colour", "colour"), "manufacturer": ("mfr", "manufacturer"),
               "product_code": ("code", "product code"), "molding": ("molding", "molding type"),
               "height": ("height", "panel height")}
JOB_BLANKS = ("sheets", "sizes")          # filled from the job's sheet rows
FIELD_BLANKS = ("project", "location", "date")      # filled from the job's own boxes of those names


def blank_kind(name) -> str:
    """Who fills a named blank of the scope sentence: "sheets" (the job's sheet rows), "rooms" (the
    job's rooms), "job" (the job's own Project, Location or Date box) or "typed" (no part of the job
    holds it: a person types it for the job). A name is read whatever its case. (A blank named for a
    figure the job computes - its total, its days, a rate - can not exist: wp_settings.RESERVED_BLANKS.)"""
    k = str(name or "").lower()
    return "sheets" if k in JOB_BLANKS else "rooms" if k in ROOM_BLANKS else "job" if k in FIELD_BLANKS else "typed"


BLANK_FROM = {"sheets": "its sheet rows", "rooms": "its rooms", "job": "its own Project, Location and Date boxes"}


# ---- numbers -----------------------------------------------------------------------------
def _round2(fr) -> Decimal:
    """A length half up to a hundredth, exactly (no binary float in the way)."""
    return Decimal(math.floor(Fraction(fr) * 100 + Fraction(1, 2))).scaleb(-2)


def _n(d: Optional[Decimal], places: int = 4) -> str:
    return qty_str(d, places) if d is not None else ""


def qty0(v, cap=MAX_QTY) -> Optional[str]:
    """A typed quantity as its own string: 0 or more, at most 2 decimals, at most `cap`. None otherwise."""
    d = S._dec(v)
    if d is None or d < 0 or d > Decimal(cap) or d != d.quantize(Decimal("0.01")):
        return None
    return S._num(d)


def _cents0(v) -> Optional[int]:
    """Stored cents: an int from 0 to the cap, else None."""
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v if 0 <= v <= S.MAX_MONEY_CENTS else None


def _typed_cents(v, what: str, zero_ok: bool = True) -> Optional[int]:
    """Cents from a typed amount, or None for a blank; refused in words when it is not an amount."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    c, why = S.money_cents(v)
    if c is None:
        raise ActError(f"{what}: {why}")
    if c == 0 and not zero_ok:
        raise ActError(f"{what}: a price is more than 0 (leave it blank for no price)")
    return c


def _typed_qty(v, what: str, cap=MAX_QTY) -> Optional[str]:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    q = qty0(v, cap)
    if q is None:
        raise ActError(f'{what}: "{str(v)[:30]}" is not a quantity - a number from 0 to {S._num(Decimal(cap))}, at most 2 decimals')
    return q


def _text(v, cap: int, what: str = "") -> str:
    t = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(v if v is not None else "")).split())
    if what and len(t) > cap:
        raise ActError(f"{what}: {len(t)} characters - at most {cap}; shorten it (nothing was cut)")
    return t[:cap]


# ---- lengths as a person types them ------------------------------------------------------
_PLAIN = re.compile(r"^\d+(?:\.\d+)?$")
_FT_IN = re.compile(r"^(\d+(?:\.\d+)?)\s*(?:'|ft|feet|foot)\s*-?\s*(?:(\d+(?:\.\d+)?)(?:\s+(\d+)/(\d+))?\s*(?:\"|in|inch|inches)?)?$")
_IN = re.compile(r"^(\d+(?:\.\d+)?)(?:\s+(\d+)/(\d+))?\s*(?:\"|in|inch|inches)$")


def length_ft(text, bare: bool) -> Optional[Fraction]:
    """A typed length in feet, exactly: 8'-6", 8', 8 ft, 8' 6 1/2", 102", 102 in - and, with `bare`, a
    plain number of feet (12.5). None when it is not one."""
    t = str(text if text is not None else "").strip().lower()
    t = re.sub("[′’]", "'", t)
    t = re.sub("[″”]", '"', t)
    t = re.sub(r"\s+", " ", t)
    if not t:
        return None
    try:
        if _PLAIN.match(t):
            return Fraction(Decimal(t)) if bare else None
        m = _FT_IN.match(t)
        if m:
            inch = Fraction(0)
            if m.group(2):
                inch = Fraction(Decimal(m.group(2))) + (Fraction(int(m.group(3)), int(m.group(4))) if m.group(3) else 0)
            return Fraction(Decimal(m.group(1))) + inch / 12
        m = _IN.match(t)
        if m:
            return (Fraction(Decimal(m.group(1))) + (Fraction(int(m.group(2)), int(m.group(3))) if m.group(2) else 0)) / 12
    except ZeroDivisionError:
        return None
    return None


_COMMA_IN_NUMBER = re.compile(r"(\d+(?:\.\d+)?),(\d+(?:\.\d+)?)")


def parse_lengths(text) -> tuple:
    """(lengths in feet, None), or ([], why). Wall lengths as typed: lengths joined by + (or by a comma
    that stands apart from the digits), each a plain number of feet or a length with its marks -
    "8 + 8 + 12.5", "8, 12.5", "8'-6\" + 4'". THE SUM IS THE WALL LENGTH. Anything that is not a length
    refuses the whole entry: nothing is skipped, so a slip never becomes a shorter wall.

    A COMMA DIRECTLY BETWEEN TWO DIGITS IS REFUSED (critic wp-1, minor 1): "1,250" was read 1 + 250 =
    251 LF and "8,5" 13 LF, where every other box of the panel reads 1,250 as 1250 and the panel itself
    prints lengths with the comma. Nothing here can tell a thousands mark from a decimal comma from two
    lengths, so it says what to write for each."""
    raw = str(text if text is not None else "").strip()
    if not raw:
        return [], None
    m = _COMMA_IN_NUMBER.search(raw)
    if m:
        a, b = m.group(1), m.group(2)
        ways = []
        if "." not in a:
            ways.append(f"{a}{b} for one length")
            if "." not in b:
                ways.append(f"{a}.{b} for a decimal")
        ways.append(f"{a} + {b} for two lengths")
        return [], (f'"{m.group(0)[:30]}" has a comma directly between two digits, which can be read more than one way: write '
                    + ", ".join(ways[:-1]) + (" or " if len(ways) > 1 else "") + ways[-1])
    out = []
    for term in re.split(r"[+,]", raw):
        term = term.strip()
        if not term:
            continue
        v = length_ft(term, bare=True)
        if v is None or v > 10000:
            return [], (f'"{term[:30]}" is not a length - type feet as numbers joined by +, like 8 + 8 + 12.5 '
                        f'(or a length with its marks, like 8\'-6")')
        out.append(v)
    if len(out) > MAX_TERMS:
        return [], f"more than {MAX_TERMS} lengths in one room"
    return out, None


# ---- the proposal's date -----------------------------------------------------------------
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_MDY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_MONTH_D_Y = re.compile(r"^([A-Za-z]{3,9})\.? (\d{1,2})(?:st|nd|rd|th)?,? (\d{4})$")
_D_MONTH_Y = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)? ([A-Za-z]{3,9})\.?,? (\d{4})$")


def _month(word: str) -> Optional[int]:
    w = word.lower()
    hit = [i + 1 for i, m in enumerate(MONTHS) if m.lower() == w or (len(w) == 3 and m.lower().startswith(w)) or (w == "sept" and i == 8)]
    return hit[0] if len(hit) == 1 else None


def parse_date(text) -> Optional[str]:
    """A typed date as "YYYY-MM-DD", or None when it is not one. Taken: October 6, 2026 / Oct 6 2026 /
    6 October 2026 / 10/6/2026 (month/day/year, said in the refusal) / 2026-10-06. A day the month
    does not have, a year with two digits or one outside 2000-2100 is not a date."""
    t = " ".join(str(text if text is not None else "").split())
    y = mo = d = None
    m = _ISO.match(t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = m or _MDY.match(t)
    if m and y is None:
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y is None:
        m = _MONTH_D_Y.match(t)
        if m:
            mo, d, y = _month(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            m = _D_MONTH_Y.match(t)
            if m:
                d, mo, y = int(m.group(1)), _month(m.group(2)), int(m.group(3))
    if y is None or mo is None or not (2000 <= y <= 2100):
        return None
    try:
        return datetime.date(y, mo, d).isoformat()
    except ValueError:
        return None


def date_words(iso) -> str:
    """ "2026-10-06" as the proposal prints it: October 6, 2026. "" when it is not a date."""
    m = _ISO.match(str(iso or ""))
    if not m:
        return ""
    try:
        dt = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return ""
    return f"{MONTHS[dt.month - 1]} {dt.day}, {dt.year}"


# ---- the document ------------------------------------------------------------------------
def default_doc() -> dict:
    return {"v": DOC_V,
            "job": {"project": "", "location": "", "date": None, "labor_rate_cents": None, "days": None, "hours": None, "miles": None,
                    "nights": None, "freight_cents": None, "profit_override_cents": None, "blanks": {}},
            "rooms": [], "sheets": [], "lines": [], "rates": None, "rates_at": None, "rates_later": {}, "seq": 1}


def _fnum(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _pt2(p) -> Optional[list]:
    if isinstance(p, (list, tuple)) and len(p) >= 2 and _fnum(p[0]) is not None and _fnum(p[1]) is not None:
        return [round(float(p[0]), 2), round(float(p[1]), 2)]
    return None


def clean_wall(w) -> Optional[dict]:
    """A stored wall: a detected line picked on a sheet (its id and the fingerprint the server read -
    length and ends - so a sheet detected again is noticed), or a segment drawn by hand."""
    if not isinstance(w, dict):
        return None
    page = w.get("page")
    if isinstance(page, bool) or not isinstance(page, int) or page < 0:
        return None
    wid = _text(w.get("id"), 40) or new_id("w")
    if w.get("src") == "line":
        chain, ln = w.get("chain"), _fnum(w.get("len_pt"))
        a, b = _pt2(w.get("a")), _pt2(w.get("b"))
        if isinstance(chain, bool) or not isinstance(chain, int) or ln is None or ln <= 0 or not a or not b:
            return None
        return {"id": wid, "page": page, "src": "line", "chain": chain, "len_pt": round(ln, 2), "a": a, "b": b}
    if w.get("src") == "drawn":
        seg = w.get("seg")
        if not (isinstance(seg, (list, tuple)) and len(seg) == 4 and all(_fnum(v) is not None for v in seg)):
            return None
        return {"id": wid, "page": page, "src": "drawn", "seg": [round(float(v), 2) for v in seg]}
    return None


def clean_room(r) -> Optional[dict]:
    if not isinstance(r, dict):
        return None
    rid = _text(r.get("id"), 40)
    if not rid:
        return None
    out = {"id": rid}
    for k, cap in ROOM_TEXT.items():
        out[k] = _text(r.get(k), cap)
    h = _text(r.get("height"), 30)
    out["height"] = h if length_ft(h, bare=False) else ""
    t = _text(r.get("typed"), 400)
    out["typed"] = t if parse_lengths(t)[1] is None else ""
    walls, seen = [], set()
    for w in (r.get("walls") or [])[:MAX_WALLS] if isinstance(r.get("walls"), list) else []:
        cw = clean_wall(w)
        if cw and cw["id"] not in seen:
            seen.add(cw["id"])
            walls.append(cw)
    out["walls"] = walls
    return out


def clean_doc(d) -> dict:
    """A stored document as every reader sees it. Only the server writes one (acts); this is the
    reader's guard against a file changed by hand."""
    d = d if isinstance(d, dict) else {}
    j = d.get("job") if isinstance(d.get("job"), dict) else {}
    job = {k: _text(j.get(k), cap) for k, cap in JOB_TEXT.items()}
    job["date"] = parse_date(j.get("date")) if j.get("date") else None
    for k, cap in JOB_QTY.items():
        job[k] = qty0(j.get(k), cap) if j.get(k) is not None else None
    for k in ("labor_rate_cents", "freight_cents", "profit_override_cents"):
        job[k] = _cents0(j.get(k))
    # the blanks typed for the job: only those no part of the job holds (a value an earlier build let a
    # person type over the job's own sheets, sizes or product words is not read and not kept)
    job["blanks"] = {}
    for k, v in list((j.get("blanks") or {}).items())[:40] if isinstance(j.get("blanks"), dict) else []:
        if (isinstance(k, str) and S.BLANK_RX.fullmatch("{" + k + "}") and blank_kind(k) == "typed"
                and k.lower() not in S.RESERVED_BLANKS and _text(v, 120)):
            job["blanks"][k.lower()] = _text(v, 120)
    rooms, seen = [], set()
    for r in (d.get("rooms") or [])[:MAX_ROOMS] if isinstance(d.get("rooms"), list) else []:
        cr = clean_room(r)
        if cr and cr["id"] not in seen:
            seen.add(cr["id"])
            rooms.append(cr)
    sheets, seen = [], set()
    for s in (d.get("sheets") or [])[:MAX_SHEETS] if isinstance(d.get("sheets"), list) else []:
        if not isinstance(s, dict) or not _text(s.get("id"), 40) or _text(s.get("id"), 40) in seen:
            continue
        seen.add(_text(s.get("id"), 40))
        dims = S.size_dims(s.get("size"))
        cnt = s.get("count")
        pc = _cents0(s.get("price_cents"))
        pf = s.get("price_from") if isinstance(s.get("price_from"), dict) else None
        sheets.append({"id": _text(s.get("id"), 40), "size": dims[0] if dims else "",
                       "count": cnt if (isinstance(cnt, int) and not isinstance(cnt, bool) and 0 <= cnt <= MAX_COUNT) else None,
                       "price_cents": pc if pc else None,
                       "price_from": ({"id": _text(pf.get("id"), 40), "name": _text(pf.get("name"), 80), "code": _text(pf.get("code"), 40),
                                       "per": pf.get("per") if pf.get("per") in ("sheet", "sqft") else "sheet",
                                       "price": _text(pf.get("price"), 20), "size": _text(pf.get("size"), 20),
                                       "at": _text(pf.get("at"), 40)} if pf and pc else None)})
    lines, seen = [], set()
    for ln in (d.get("lines") or [])[:MAX_LINES] if isinstance(d.get("lines"), list) else []:
        if not isinstance(ln, dict) or not _text(ln.get("id"), 40) or _text(ln.get("id"), 40) in seen:
            continue
        seen.add(_text(ln.get("id"), 40))
        lines.append({"id": _text(ln.get("id"), 40), "what": _text(ln.get("what"), 120),
                      "qty": qty0(ln.get("qty")) if ln.get("qty") is not None else None,
                      "unit": _text(ln.get("unit"), 12), "price_cents": _cents0(ln.get("price_cents"))})
    ra = d.get("rates_at") if isinstance(d.get("rates_at"), dict) else None
    rl = d.get("rates_later") if isinstance(d.get("rates_later"), dict) else {}
    try:
        seq = max(1, int(d.get("seq") or len(rooms) + 1))
    except (TypeError, ValueError):
        seq = len(rooms) + 1
    return {"v": DOC_V, "job": job, "rooms": rooms, "sheets": sheets, "lines": lines,
            "rates": S.snapshot(d["rates"]) if isinstance(d.get("rates"), dict) else None,
            "rates_at": {"at": _text(ra.get("at"), 40), "by": _text(ra.get("by"), 120)} if ra else None,
            # the values the job took AFTER its first write (it had none then): which, and when
            "rates_later": {k: _text(rl[k], 40) for k in S.SNAP_KEYS if _text(rl.get(k), 40)},
            "seq": seq}


def _find(lst: list, i, what: str) -> dict:
    for x in lst:
        if x["id"] == str(i):
            return x
    raise ActError(f"that {what} is not on this job any more - reload to see it as it is")


# ---- the figures -------------------------------------------------------------------------
def wall_pt(w: dict, page_info: Optional[dict]) -> tuple:
    """(length in pt, None), or (None, why). A drawn wall is its own two points; a picked line is the
    sheet's detection NOW - the same line (same id, same ends, same length) or it is not counted."""
    if w["src"] == "drawn":
        s = w["seg"]
        return math.hypot(s[2] - s[0], s[3] - s[1]), None
    if not page_info or not page_info.get("detected"):
        return None, "this sheet has no detection now (it was reset): run Detect, and the line is measured again"
    c = (page_info.get("chains") or {}).get(w["chain"])
    same = lambda p, q: p and q and math.hypot(float(p[0]) - float(q[0]), float(p[1]) - float(q[1])) <= 0.6
    if not c or abs(float(c["len"]) - w["len_pt"]) > 0.011 or not (same(c.get("a"), w["a"]) and same(c.get("b"), w["b"])):
        return None, "the sheet was detected again and this line is not the one that was picked: pick it again"
    return float(c["len"]), None


VIEW_INFERENCE = ("a view's title and scale are printed with the view: general drafting practice, an inference")


def room_figures(room: dict, sizes: list, pages: dict) -> dict:
    """One room's wall length and its suggested sheet count. A WALL IS MEASURED AT THE SCALE OF THE
    VIEW IT IS DRAWN IN (critic wp-1, MAJOR-2). On a sheet that prints one scale (or none) that is the
    sheet's scale in force. On a sheet that prints more than one, it is the scale of the wall's own
    view as the server decided it (`pages[p]["views"][wall id]`, the decision a flooring room goes
    through): a wall whose view is not known, or whose view two titles contest, has NO feet and says
    why. A wall with no feet - no scale in force, no view - shows in pt only, and the room then has no
    wall length (never a partial one) and no suggestion."""
    terms, bad = parse_lengths(room["typed"])
    typed = _round2(sum(terms, Fraction(0)))
    walls, flags = [], []
    picked, complete = Decimal(0), True
    for w in room["walls"]:
        info = pages.get(w["page"]) or {}
        pt, why = wall_pt(w, info)
        sheet_ft = info.get("ft")
        multi = bool(info.get("multi"))
        view = (info.get("views") or {}).get(w["id"]) if multi else None
        sheet_no = w["page"] + 1
        kind = "a line picked" if w["src"] == "line" else "a wall drawn"
        o = {"id": w["id"], "page": w["page"], "src": w["src"], "chain": w.get("chain"), "seg": w.get("seg"),
             "pt": None, "lf": None, "state": None, "short": None, "view": None}
        if pt is None:
            o["words"] = f"sheet {sheet_no}, {kind}: {why}"
            o["short"] = "not measured"
            complete = False
        elif not sheet_ft:
            sw = info.get("words")
            o["pt"] = f"{pt:.2f}"
            o["short"] = "no scale in force"
            o["words"] = (f"sheet {sheet_no}, {kind}: {pt:.2f} pt on the sheet - no scale is in force on this sheet"
                          + (f" ({sw})" if sw else "") + ", so it has no length in feet: set the sheet's scale")
            complete = False
        elif multi and not (view and view.get("ft")):
            # the sheet prints more than one scale and this wall's view is not known, or two titles are as near:
            # no feet, never the sheet's scale by default (a wall in an enlarged plan read twice its length)
            o["pt"] = f"{pt:.2f}"
            o["short"] = "its view's scale is not known"
            o["words"] = (f"sheet {sheet_no}, {kind}: {pt:.2f} pt on the sheet - this sheet prints more than one scale and "
                          + ((view or {}).get("why") or "the view this wall is drawn in was not worked out")
                          + ", so it has no length in feet: type this wall's length in the room's wall lengths instead")
            complete = False
        else:
            ft = view["ft"] if view else sheet_ft
            ptd = Decimal(f"{pt:.2f}")
            lf = _round2(Fraction(ptd) * Fraction(Decimal(repr(float(ft)))) / 72)
            picked += lf
            o["pt"], o["lf"] = f"{ptd}", qty_str(lf, 2)
            # how the length stands: a view decided by its title is an inference - never the clean colour
            o["state"] = "amber" if view else info.get("sheet")
            o["view"] = (view or {}).get("view")
            o["words"] = (f"sheet {sheet_no}, {kind}: {ptd} pt at 1\" = {float(ft):g} ft = {qty_str(lf, 2)} LF"
                          + (f" ({view['view']})" if view and view.get("view") else "")
                          + (" (sheet scale not confirmed)" if info.get("sheet") == "amber" else ""))
            if view:
                flags.append(f"sheet {sheet_no} prints more than one scale: each wall on it is measured at the scale of the view "
                             f"it is drawn in, told by the view title nearest it ({VIEW_INFERENCE}) - check each wall's view")
        walls.append(o)
    total = typed + picked if complete else None
    parts = []
    if terms:
        parts.append(f"typed {qty_str(typed, 2)} LF")
    n_ok = sum(1 for o in walls if o["lf"] is not None)
    if n_ok:
        parts.append(f"{n_ok} on the sheet {qty_str(picked, 2)} LF")
    n_no = len(walls) - n_ok
    words = (" + ".join(parts) + (f" = {qty_str(total, 2)} LF" if total is not None and len(parts) > 1 else "")) if parts else ""
    if n_no:
        words += ("; " if words else "") + f"{n_no} wall{'s' if n_no != 1 else ''} on the sheet not in feet - no wall length for this room yet"
    h = length_ft(room["height"], bare=False)
    return {"typed_lf": qty_str(typed, 2) if terms else None, "walls": walls,
            "wall_lf": qty_str(total, 2) if total is not None and (terms or walls) else None,
            "wall_words": words, "height_words": R.ft_in(float(h)) if h else None,
            "suggestion": suggest(total if (terms or walls) else None, h, sizes, bool(n_no)),
            "flags": sorted(set(flags))}


def suggest(total_lf: Optional[Decimal], height: Optional[Fraction], sizes: list, unmeasured: bool = False) -> dict:
    """THE SUGGESTED SHEET COUNT of one room - offered, never applied: the wall length divided by the
    sheet's width, rounded up, on the shortest sheet of the account's list that is at least as long as
    the panel height. A sheet's width and length are read from its size's own name, the smaller number
    the width (wp_settings.size_dims: an inference, said here). When two sizes tie on that length they
    are ALTERNATIVES: each is offered with its own count, the narrowest first, the others marked `alt`
    ("or") - one room needs one of them, never their sum (critic wp-1, MAJOR-1)."""
    out = {"options": [], "why": None, "words": GEOMETRY_ONLY,
           "inference": "a sheet's width is the smaller number of its size, its length the larger (read from the size's name)"}
    if unmeasured:
        out["why"] = "no suggestion: a wall of this room has no length in feet yet"
    elif total_lf is None or total_lf <= 0:
        out["why"] = "no suggestion: this room has no wall length yet"
    elif height is None:
        out["why"] = "no suggestion: type the panel height with its unit (8', 96\")"
    elif not sizes:
        out["why"] = f"no suggestion: your sheet sizes are not set - {SET_IT}"
    else:
        fit = [s for s in sizes if s[2] >= height]
        if not fit:
            out["why"] = (f"no suggestion: no sheet in your list ({', '.join(s[0] for s in sizes)}) is as long as "
                          f"{R.ft_in(float(height))}")
        else:
            shortest = min(s[2] for s in fit)
            tie = sorted((s for s in fit if s[2] == shortest), key=lambda s: s[1])      # the narrowest first (stable: list order)
            for i, (name, w, _l) in enumerate(tie):
                q = Fraction(total_lf) / w
                n = math.ceil(q)
                # THE QUOTIENT AS IT IS SAID never contradicts the count (critic wp-1, minor 9: "= 13, rounded up: 14"):
                # a whole quotient is that many sheets; one that prints as a whole number at two decimals is said to be
                # a little over or under it
                r2 = _round2(q)
                if q == n:
                    said = f"{n}: {n} sheet{'s' if n != 1 else ''}"
                else:
                    near = (f"a little {'over' if q > r2 else 'under'} {qty_str(r2, 2)}" if r2 == r2.to_integral_value() else qty_str(r2, 2))
                    said = f"{near}, rounded up: {n} sheet{'s' if n != 1 else ''}"
                out["options"].append({
                    "size": name, "count": n, "alt": i > 0,
                    "arith": (f"{qty_str(total_lf, 2)} LF ÷ {S._num(Decimal(w.numerator) / Decimal(w.denominator))} ft wide = "
                              f"{said} of {name} (the shortest sheet in your list at least {R.ft_in(float(height))} long)")})
    return out


def _line(lid, group, what, qty, unit, rate_c, amount_c, how, missing) -> dict:
    return {"id": lid, "group": group, "what": what, "qty": qty, "unit": unit,
            "rate": money(rate_c) if rate_c is not None else "", "amount": money(amount_c) if amount_c is not None else "",
            "amount_cents": amount_c, "how": how, "missing": list(missing)}


def _cost(lid, what, q: Optional[Decimal], unit, rate_c, how, missing, places=4) -> dict:
    """A cost line: its printed quantity x its printed rate = its amount, half up to the cent."""
    qs = qty_str(q, places) if q is not None else ""
    amt = amount_cents(qs, rate_c) if (qs != "" and rate_c is not None and not missing) else None
    return _line(lid, "cost", what, qs, unit, rate_c, amt, how, missing)


def item_price_cents(per: str, price: str, size: str) -> Optional[int]:
    """A price item as a price a sheet: its own price, or its price a square foot x the sheet's area
    (width x length from the size), half up to the cent."""
    if per == "sheet":
        c, _why = S.money_cents(price)
        return c if c else None
    dims = S.size_dims(size)
    n, _why = S.money_cents(price, 4)                 # ten-thousandths of a dollar a square foot
    if not dims or not n:
        return None
    return int(math.floor(Fraction(n) * dims[1] * dims[2] / 100 + Fraction(1, 2))) or None


def _price_words(s: dict) -> str:
    pf = s.get("price_from")
    if not pf:
        return "price typed" if s["price_cents"] is not None else ""
    name = pf["name"] + (f" ({pf['code']})" if pf.get("code") else "")
    if pf["per"] == "sqft":
        dims = S.size_dims(s["size"])
        area = S._num(Decimal((dims[1] * dims[2]).numerator) / Decimal((dims[1] * dims[2]).denominator)) if dims else "?"
        return f"price from {name}: {pf['price']} a square foot x {area} SF ({s['size']}), half up to the cent"
    return f"price from {name}: {pf['price']} a sheet"


def _and(parts: list) -> str:
    """["a"] -> "a"; ["a", "b"] -> "a and b"; ["a", "b", "c"] -> "a, b and c"."""
    parts = [str(p) for p in parts]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _several(words: str) -> str:
    """The words for more than one of a thing: "colours", "fire ratings" - and "panel thicknesses" (it printed
    "panel thicknesss": the plural was made by adding an s to whatever the words were)."""
    return words + ("es" if words.endswith(("s", "x", "ch", "sh")) else "s")


def room_values(doc: dict, name: str) -> list:
    """What the job's rooms give for one product blank, as it stands NOW: every different value, each
    once, in the order the rooms give them (case aside: "Class A" and "class a" are one). Nothing is
    ranked and no room wins: rooms that differ are ALL printed. A panel height is compared by its length
    (8' and 96" are one height) and printed the one way the job prints heights."""
    field, _words = ROOM_BLANKS[name]
    seen, out = set(), []
    for r in doc["rooms"]:
        v = (r.get(field) or "").strip()
        if not v:
            continue
        key = v.casefold()
        if field == "height":
            h = length_ft(v, bare=False)
            if not h:
                continue
            key, v = h, R.ft_in(float(h))
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def item_note(s: dict, items: Optional[list]) -> str:
    """What became of the price item a sheet row took its price from (critic wp-1, minor 4). The row
    KEEPS its price - a price never moves by itself; when the item reads otherwise in the settings now,
    or is gone from them, the row says so. "" when the item is as it was, or the settings were not given."""
    pf = s.get("price_from")
    if not pf or items is None:
        return ""
    it = next((x for x in items if pf.get("id") and x.get("id") == pf["id"]), None)
    if it is None:          # a row priced before the item's id was kept with it: the same name, code and unit
        it = next((x for x in items if (x.get("name"), x.get("code"), x.get("per")) == (pf["name"], pf["code"], pf["per"])), None)
    per = lambda p: "a square foot" if p == "sqft" else "a sheet"
    if it is None:
        return f"its price item is not in your settings any more: the row keeps {pf['price']} {per(pf['per'])}"
    if it.get("per") != pf["per"] or it.get("price") != pf["price"]:
        return (f"its price item reads {it.get('price')} {per(it.get('per'))} in your settings now: the row keeps "
                f"{pf['price']} {per(pf['per'])} until you pick the item again")
    return ""


def scope(doc: dict, rates: dict, sheets_total: Optional[int], sizes_used: list, date_text: str = "") -> dict:
    """THE PROPOSAL'S SCOPE SENTENCE: the account's template with each named blank filled. THE SENTENCE
    NEVER DISAGREES WITH THE JOB (critic wp-1, BLOCKER-1: a count and a size typed for the job stayed
    "14 sheets of 4x8" on the customer's PDF after the rows became 90 of 4x10). So:
      * a blank THE JOB ITSELF KNOWS is filled here, from the job as it is now, on every read, and can
        not be typed over: `sheets` and `sizes` from the sheet rows; the product blanks (ROOM_BLANKS)
        from the rooms - every different value the rooms give, in their order (`room_values`);
        `project`, `location` and `date` from the job's own boxes of those names (`date_text`: the
        date the proposal prints);
      * while the job does not give it (no sheet row counted yet, no room names the thing) the blank is
        open and holds the proposal, saying where in the job to give it;
      * a blank named for a figure the job computes (its total, its days, a rate) can not exist: the
        settings refuse the template (wp_settings.RESERVED_BLANKS), so none is ever typed by hand;
      * only a blank NO PART OF THE JOB HOLDS (a name the template made up) is typed for the job.
    Each blank says its `kind` ("sheets" / "rooms" / "job" / "typed") so the page offers a box only for the last."""
    tpl = rates.get("scope_template")
    if not tpl:
        return {"template": None, "text": None, "blanks": [],
                "missing": [f"the proposal's scope sentence is not set - {SET_IT}"]}
    blanks, missing, values = [], [], {}
    for name in S.template_blanks(tpl):
        kind = blank_kind(name)
        typed, parts, why = None, [], None
        if name == "sheets":
            if sheets_total is None:
                why = "every sheet row needs its count"
            elif sheets_total <= 0:
                why = "the job has no sheets yet"
            else:
                parts = [str(sheets_total)]
        elif name == "sizes":
            if not sizes_used:
                why = "no sheet row has a count yet"
            else:
                parts = list(sizes_used)
        elif kind == "rooms":
            parts = room_values(doc, name)
            if not parts:
                why = f"no room gives a {ROOM_BLANKS[name][1]}"
        elif kind == "job":
            v = date_text if name == "date" else doc["job"].get(name) or ""
            parts = [v] if v else []
            why = None if v else f"the job's {name} is not typed"
        else:
            typed = doc["job"]["blanks"].get(name) or None
            parts = [typed] if typed else []
            why = None if typed else "only a person knows it"
        value = _and(parts) if parts else None
        by = None
        if value:
            by = ("typed for this job" if kind == "typed" else "from the job's sheet rows" if kind == "sheets"
                  else f"from the job's {name.capitalize()} box" if kind == "job"
                  else "from the rooms" + (f" ({len(parts)} different {_several(ROOM_BLANKS[name][1])})" if len(parts) > 1 else ""))
        blanks.append({"name": name, "kind": kind, "value": value, "typed": typed, "auto": value if kind != "typed" else None,
                       "by": by, "why": None if value else why})
        values[name] = parts
        if not value:
            how = ("give the sheet rows their counts (the job fills this blank itself: it is not typed)" if kind == "sheets"
                   else "type it on a room (the job fills this blank from its rooms: it is not typed)" if kind == "rooms"
                   else f"type it in the {name.capitalize()} box of the Proposal section (the job fills this blank itself)" if kind == "job"
                   else "type it for this job")
            missing.append(f"the scope sentence's {name} is blank ({why}): {how}")

    def rep(m):
        parts = values.get(m.group(1).lower())
        if not parts:
            return "___"
        # a value that opens with the very word the template printed just before the blank is not said twice
        before = tpl[:m.start()].split()

        def cut(v):
            first, _sp, rest = v.partition(" ")
            return rest.strip() if (rest.strip() and before and before[-1].casefold() == first.casefold()) else v
        return _and([cut(v) for v in parts])
    return {"template": tpl, "text": S.BLANK_RX.sub(rep, tpl), "blanks": blanks, "missing": missing}


def compute(doc: dict, rates: Optional[dict], pages: Optional[dict] = None, company: Optional[str] = None,
            contact: Optional[str] = None, today: Optional[str] = None, unprintable=None,
            settings_problem: Optional[str] = None, price_items: Optional[list] = None) -> dict:
    """Everything the page, the worksheet and the proposal show, from one job's document, the rates it
    stands on and its sheets (`pages` {page: {detected, ft, sheet, words, multi, chains, views}} - the
    server's reading of each sheet a wall sits on, and of each wall's own view on a sheet that prints
    more than one scale). `company`, `contact`: the account's company block as the proposal prints it
    (None = not asked). `today`: the server's date ("YYYY-MM-DD"), the proposal's date until a person
    types another. `unprintable(text)` -> the letters of a text the proposal's font can not print: a
    field that has any holds the proposal BY NAME - nothing is ever printed as "?" (critic wp-1, minor
    6). `settings_problem`: the account's settings could not be read - said, and the proposal held.
    `price_items`: the account's price items as they are now (a row whose item changed says so).
    Every value in the answer is a string or a whole number."""
    doc = clean_doc(doc)
    rates = S.snapshot(rates or S.blank())
    job, pages = doc["job"], (pages or {})
    sizes = [d for d in (S.size_dims(s) for s in rates["sizes"]) if d]
    rc = lambda k: S.cents_of(rates, k)
    D = lambda v: Decimal(v) if v is not None else None
    lines = []

    rooms = {r["id"]: room_figures(r, sizes, pages) for r in doc["rooms"]}

    # the sheets, a line a row
    sheets_so_far, counts_known, sizes_used, price_notes = 0, True, [], []
    for i, s in enumerate(doc["sheets"]):
        label = f"Sheets {s['size']}" if s["size"] else f"Sheets, row {i + 1}"
        miss = []
        if not s["size"]:
            miss.append(f"{label}: no size")
        if s["count"] is None:
            miss.append(f"{label}: no count")
            counts_known = False
        else:
            sheets_so_far += s["count"]
            if s["count"] > 0 and s["size"] and s["size"] not in sizes_used:
                sizes_used.append(s["size"])
        if s["price_cents"] is None and s["count"] != 0:
            miss.append(f"{label}: no price a sheet")
        qs = qty_str(Decimal(s["count"]), 0) if s["count"] is not None else ""
        amt = (0 if s["count"] == 0 else amount_cents(qs, s["price_cents"])) if not miss else None
        note = item_note(s, price_items)
        if note:
            price_notes.append(f"{label}: {note}")
        lines.append(_line("sheet:" + s["id"], "sheets", label, qs, "sheets", s["price_cents"], amt,
                           _price_words(s) + (f" - {note}" if note else ""), miss))
    sheets_total = sheets_so_far if counts_known else None

    # the typed lines (moldings, adhesive, anything by the piece): no rule of ours
    for ln in doc["lines"]:
        if not ln["what"] and ln["qty"] is None and ln["price_cents"] is None:
            continue
        what = ln["what"] or "a typed line"
        miss = []
        if not ln["what"]:
            miss.append("a typed line has no name")
        if ln["qty"] is None:
            miss.append(f"{what}: no quantity")
        if ln["price_cents"] is None:
            miss.append(f"{what}: no price")
        qs = qty_str(Decimal(ln["qty"]), 2) if ln["qty"] is not None else ""
        amt = amount_cents(qs, ln["price_cents"]) if not miss else None
        lines.append(_line("line:" + ln["id"], "typed", what, qs, ln["unit"], ln["price_cents"], amt, "typed", miss))

    days, hours, miles, nights = (D(job[k]) for k in ("days", "hours", "miles", "nights"))
    # labor
    typed_rate = job["labor_rate_cents"] is not None
    lr = job["labor_rate_cents"] if typed_rate else rc("labor_rate")
    miss = []
    if lr is None:
        miss.append(f"Labor: no hourly rate - type one for this job, or {SET_IT}")
    if days is None:
        miss.append("Labor: days not typed (0 for none)")
    if hours is None:
        miss.append("Labor: hours a day not typed (0 for none)")
    q = days * hours if (days is not None and hours is not None) else None
    how = (f"{_n(days)} days x {_n(hours)} hours a day = {_n(q)} hours" if q is not None else "") \
        + (("; " if q is not None else "") + ("the rate typed for this job" if typed_rate else "your labor rate") if lr is not None else "")
    lines.append(_cost("labor", "Labor", q, "hours", lr, how, miss))
    # overtime
    after, otr = D(rates["overtime_after_hours"]), rc("overtime_rate")
    miss = []
    if after is None:
        miss.append(f"Overtime: the hours a day after which overtime starts is not set - {SET_IT}")
    if otr is None:
        miss.append(f"Overtime: the overtime rate is not set - {SET_IT}")
    q, how = None, ""
    if days is None or hours is None:
        how = "waits for the labor days and hours a day"
    elif after is not None:
        over = max(Decimal(0), hours - after)
        q = over * days
        how = f"{_n(hours)} hours a day less {_n(after)} = {_n(over)} over, x {_n(days)} days = {_n(q)} overtime hours"
    lines.append(_cost("overtime", "Overtime", q, "hours", otr, how, miss))
    # travel, hotel
    for lid, what, qv, unit, key, qwhy, rwhy in (
            ("travel", "Travel", miles, "miles", "travel_per_mile", "miles not typed (0 for none)", "the amount a mile is not set"),
            ("hotel", "Hotel", nights, "nights", "hotel_nightly", "nights not typed (0 for none)", "the amount a night is not set")):
        miss = []
        if qv is None:
            miss.append(f"{what}: {qwhy}")
        if rc(key) is None:
            miss.append(f"{what}: {rwhy} - {SET_IT}")
        lines.append(_cost(lid, what, qv, unit, rc(key), "", miss, places=2))
    # freight: an amount typed for the job
    fr = job["freight_cents"]
    lines.append(_line("freight", "cost", "Freight", "", "", None, fr, "typed for this job" if fr is not None else "",
                       [] if fr is not None else ["Freight: not typed (0 for none)"]))
    # profit: the rule, or the amount typed for this job (shown as typed)
    under, flat, per_day = rates["profit_flat_under_sheets"], rc("profit_flat"), rc("profit_per_day")
    rule_amt, rule_how, rule_miss, rule_q, rule_rate = None, "", [], None, None
    if under is None:
        rule_miss.append(f"Profit: the sheet count under which the flat amount applies is not set - {SET_IT}")
    elif sheets_total is None:
        rule_how = "waits for every sheet row's count"
    elif sheets_total < under:
        if flat is None:
            rule_miss.append(f"Profit: the flat amount is not set - {SET_IT}")
        else:
            rule_amt = flat
            rule_how = f"{sheets_total} sheet{'s' if sheets_total != 1 else ''} on the job, fewer than {under}: the flat amount"
    else:
        if per_day is None:
            rule_miss.append(f"Profit: the amount a day is not set - {SET_IT}")
        elif days is None:
            rule_how = "waits for the labor days"
        else:
            rule_q, rule_rate = days, per_day
            rule_amt = amount_cents(qty_str(days, 2), per_day)
            rule_how = f"{sheets_total} sheets on the job, not fewer than {under}: {_n(days, 2)} days x the amount a day"
    ov = job["profit_override_cents"]
    if ov is not None:
        said = (f"; the rule gives {money(rule_amt)} ({rule_how})" if rule_amt is not None
                else "; the rule gives no amount now" + (f" ({rule_how})" if rule_how else ""))
        lines.append(_line("profit", "cost", "Profit (typed for this job)", "", "", None, ov, "typed for this job" + said, []))
    else:
        # a rule that waits (for a sheet row's count, for the days) has no amount; the line it waits for says what is missing
        lines.append(_line("profit", "cost", "Profit", qty_str(rule_q, 2) if rule_q is not None else "", "days" if rule_q is not None else "",
                           rule_rate, rule_amt if not rule_miss else None, rule_how, rule_miss))

    total = sum(x["amount_cents"] or 0 for x in lines)
    # the proposal's date: the one a person typed for this job, else the server's date today - shown before issuing
    typed_date = date_words(job["date"])
    date = {"text": typed_date or date_words(today), "typed": bool(typed_date),
            "by": "typed for this job" if typed_date else "today, by the server's clock" if date_words(today) else None}
    sc = scope(doc, rates, sheets_total, sizes_used, date["text"])
    hold = [m for x in lines for m in x["missing"]] + sc["missing"]
    if settings_problem:
        hold.append(settings_problem)
    if company is not None and not str(company).strip():
        hold.append("your company name is not set - set it in Settings, Company (the proposal prints it)")
    # EVERY LETTER THE PROPOSAL PRINTS IS ONE ITS FONT HAS, or the proposal is held naming the field
    if unprintable is not None:
        named = [("your company name (Settings, Company)", company), ("your company's contact line (Settings, Company)", contact),
                 ("the project name", job["project"]), ("the location", job["location"]),
                 ("the scope sentence in your settings", S.BLANK_RX.sub(" ", sc["template"] or ""))]
        named += [(f"the scope sentence's {b['name']} ({b['by']})", b["value"]) for b in sc["blanks"] if b["value"] and b["kind"] != "job"]
        named += [(f"note {i + 1} of your proposal notes (Settings, Wall panels)", n) for i, n in enumerate(rates["notes"])]
        for what, text in named:
            odd = unprintable(text) if text else []
            if odd:
                hold.append(f"{what} has {len(odd)} letter{'s' if len(odd) != 1 else ''} the proposal's font can not print ("
                            + " ".join(odd[:12]) + (" ..." if len(odd) > 12 else "") + "): write "
                            + ("it" if len(odd) == 1 else "them") + " in plain letters - nothing is printed as a question mark")
    if not hold and any(x["amount_cents"] is None for x in lines):
        hold.append("a line has no amount yet")
    if not hold and total <= 0:
        hold.append("the total is 0.00: there is nothing to propose yet")
    flags = sorted({f for r in rooms.values() for f in r["flags"]})
    # THE JOB'S SUGGESTED LINE: ONE CHOICE A ROOM (critic wp-1, MAJOR-1: a room offered 66 of 4x10 OR 53 of 5x10 was
    # added up as 119 sheets). The choice counted is the room's first option - the narrowest sheet of the shortest
    # length that fits; a room's other sizes of that length stay on the room as alternatives and are never added.
    sums, with_alts = {}, 0
    for r in doc["rooms"]:
        opts = rooms[r["id"]]["suggestion"]["options"]
        if opts:
            sums[opts[0]["size"]] = sums.get(opts[0]["size"], 0) + opts[0]["count"]
            with_alts += 1 if len(opts) > 1 else 0
    said = ""
    if sums:
        said = ("one size a room - the narrowest sheet of the shortest length that fits - each room rounded up on its own"
                + (f"; {with_alts} room{'s' if with_alts != 1 else ''} could take another size of that length instead: "
                   "listed on the room, not added here" if with_alts else "") + " - " + GEOMETRY_ONLY)
    return {"rooms": rooms, "lines": lines, "total_cents": total, "total": money(total), "total_words": amount_words(total),
            "sheets_total": sheets_total, "sheets_so_far": sheets_so_far, "scope": sc, "notes": list(rates["notes"]),
            "hold": hold, "issue": not hold, "flags": flags, "date": date, "price_notes": price_notes,
            "suggested": [{"size": k, "count": v} for k, v in sums.items()], "suggested_words": said}


def typed_amounts(doc: dict) -> dict:
    """The amounts a person typed, as an input shows them again ("1234.50"; "" for none): the page
    prints these strings and formats no money of its own."""
    doc = clean_doc(doc)
    j, s = doc["job"], S.money_str
    return {"labor_rate": s(j["labor_rate_cents"]) or "", "freight": s(j["freight_cents"]) or "", "profit": s(j["profit_override_cents"]) or "",
            "sheets": {x["id"]: s(x["price_cents"]) or "" for x in doc["sheets"]},
            "lines": {x["id"]: s(x["price_cents"]) or "" for x in doc["lines"]}}


# ---- the amount in words -----------------------------------------------------------------
_ONES = ("Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen Fifteen Sixteen "
         "Seventeen Eighteen Nineteen").split()
_TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety")
# the name of each power of a thousand, largest first; a number past the largest is refused, never cut short
_POWERS = ((10 ** 18, "Quintillion"), (10 ** 15, "Quadrillion"), (10 ** 12, "Trillion"), (10 ** 9, "Billion"),
           (10 ** 6, "Million"), (1000, "Thousand"))


def whole_words(n: int) -> str:
    """A whole number from 0 up, in words: 5318 -> "Five Thousand Three Hundred Eighteen". A number too
    large to name (10^21 and up) raises ValueError: its words are never printed short of its figures."""
    n = int(n)
    if n < 0 or n >= 10 ** 21:
        raise ValueError(f"no words for {n}")
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return _ONES[n // 100] + " Hundred" + (" " + whole_words(n % 100) if n % 100 else "")
    for size, name in _POWERS:
        if n >= size:
            return whole_words(n // size) + " " + name + (" " + whole_words(n % size) if n % size else "")
    raise ValueError(f"no words for {n}")


def amount_words(cents_total: int) -> str:
    """Whole cents as a written amount: 531807 -> "Five Thousand Three Hundred Eighteen Dollars and
    Seven Cents"; no cents, no cents clause."""
    c = max(0, int(cents_total))
    d, c = divmod(c, 100)
    out = f"{whole_words(d)} Dollar{'' if d == 1 else 's'}"
    if c:
        out += f" and {whole_words(c)} Cent{'' if c == 1 else 's'}"
    return out


# ---- acts --------------------------------------------------------------------------------
# what each act may carry (besides its kind). An act names what a person did and nothing else: any
# other key is refused at the route, a figure by name (act_problem).
ACT_KEYS = {
    "job_set": ("field", "value"), "blank_set": ("name", "value"), "rates_take": ("part",),
    "room_add": ("name",), "room_set": ("id", "field", "value"), "room_del": ("id",),
    "wall_pick": ("room", "page", "chain"), "wall_draw": ("room", "page", "seg"), "wall_del": ("room", "wall"),
    "sheet_add": ("size",), "sheet_set": ("id", "field", "value"), "sheet_price_item": ("id", "item"), "sheet_del": ("id",),
    "line_add": (), "line_set": ("id", "field", "value"), "line_del": ("id",),
}
SERVER_ONLY_KINDS = frozenset(("wall_put",))          # what the server makes out of a measurement
FIGURE_KEYS = frozenset(("len_pt", "len_ft", "lf", "pt", "wall_lf", "typed_lf", "amount", "amount_cents", "total", "total_cents",
                         "price_cents", "rate_cents", "labor_rate_cents", "freight_cents", "profit_override_cents",
                         "rates", "rates_at", "walls", "wall", "a", "b", "suggestion", "sheets_total", "area", "price_from"))


def act_problem(act) -> Optional[str]:
    """Why an act is refused before anything is read, or None."""
    if not isinstance(act, dict):
        return "no act"
    kind = act.get("kind")
    if kind in SERVER_ONLY_KINDS:
        return f"{kind} is the server's own act (what it measured): send what you did - a line picked, a wall drawn - and the server measures it"
    if kind not in ACT_KEYS:
        return f"unknown act: {str(kind)[:40]}"
    extra = sorted(k for k in act if k != "kind" and k not in ACT_KEYS[kind])
    if not extra:
        # what a person typed or chose is a plain value; the one piece of geometry is a drawn wall's two ends
        for k, v in act.items():
            if k == "seg":
                if not (isinstance(v, list) and len(v) == 4 and all(_fnum(x) is not None for x in v)):
                    return "a drawn wall is its two ends: four numbers - refused, nothing written"
            elif isinstance(v, (dict, list)):
                return f"{k} is one plain value (what was typed or chosen), not a structure - refused, nothing written"
        return None
    figs = [k for k in extra if k in FIGURE_KEYS]
    if figs:
        return (f"an act carries what you did, never a figure: {', '.join(figs)} "
                f"{'is a figure' if len(figs) == 1 else 'are figures'} the server computes - refused, nothing written")
    return f"{kind} carries {', '.join(ACT_KEYS[kind]) or 'nothing'}: {', '.join(str(k)[:30] for k in extra)} is not part of it - refused, nothing written"


def apply_act(doc: dict, act: dict, who: str, now: str, settings_now: Optional[dict] = None) -> str:
    """Apply one act to the document (in place). Returns the sentence the status line shows; raises
    ActError with the sentence when it is refused. `settings_now`: the account's wall panel settings
    at this moment (a price item is read from them; `rates_take` copies them into the job)."""
    kind = act.get("kind") if isinstance(act, dict) else None
    if kind not in ACT_KEYS and kind not in SERVER_ONLY_KINDS:
        raise ActError(f"unknown act: {str(kind)[:40]}")
    job = doc["job"]
    if kind == "job_set":
        f, v = act.get("field"), act.get("value")
        if f in JOB_TEXT:
            job[f] = _text(v, JOB_TEXT[f], f)
            return "saved"
        if f in JOB_QTY:
            job[f] = _typed_qty(v, f, JOB_QTY[f])
            return f"{f}: {job[f]}" if job[f] is not None else f"{f}: not typed"
        if f == "labor_rate":
            job["labor_rate_cents"] = _typed_cents(v, "labor rate")
            return "labor rate typed for this job" if job["labor_rate_cents"] is not None else "labor: your own rate again"
        if f == "freight":
            job["freight_cents"] = _typed_cents(v, "freight")
            return "freight saved" if job["freight_cents"] is not None else "freight: not typed"
        if f == "profit":
            job["profit_override_cents"] = _typed_cents(v, "profit")
            return "profit typed for this job (shown as typed)" if job["profit_override_cents"] is not None else "profit: by the rule again"
        if f == "date":
            t = _text(v, 40, "date")
            if not t:
                job["date"] = None
                return "the proposal's date: today's, again"
            iso = parse_date(t)
            if not iso:
                raise ActError(f'"{t}" is not a date - write it like October 6, 2026 or 10/6/2026 (month/day/year)')
            job["date"] = iso
            return f"the proposal's date: {date_words(iso)}"
        raise ActError("no such field")
    if kind == "blank_set":
        name = str(act.get("name") or "").lower()
        if not S.BLANK_RX.fullmatch("{" + name + "}") or len(name) > S.BLANK_NAME_MAX:
            raise ActError("no such blank")
        if blank_kind(name) != "typed":
            # THE JOB FILLS IT (critic wp-1, BLOCKER-1): a count, a size or a product word typed here went stale
            # the moment the job's rows or rooms changed, and the customer's proposal printed it
            raise ActError(f"{name} is filled by the job itself, each time, from {BLANK_FROM[blank_kind(name)]}: "
                           "change it there - it is not typed here")
        if name in S.RESERVED_BLANKS:
            raise ActError(f"{name} is a figure of the job itself: it is not typed into the sentence")
        v = _text(act.get("value"), 120, name)
        if v:
            job["blanks"][name] = v
            return f"{name}: typed for this job"
        job["blanks"].pop(name, None)
        return f"{name}: not typed"
    if kind == "rates_take":
        # all of them, or one part (critic wp-1, minor 3: a corrected note could not be taken into a priced job
        # without its rates moving too): "rates" = the rates, thresholds, sizes and molding types; "words" = the
        # proposal's sentence and notes
        part = act.get("part") or "all"
        if part not in ("all", "rates", "words"):
            raise ActError("no such part of the settings")
        new = S.snapshot(settings_now or S.blank())
        if part == "all" or doc.get("rates") is None:
            doc["rates"], doc["rates_later"] = new, {}
            doc["rates_at"] = {"at": now, "by": _text(who, 120)}
        else:
            if not isinstance(doc.get("rates_later"), dict):
                doc["rates_later"] = {}
            for k in S.SNAP_KEYS:
                if (k in S.WORD_KEYS) == (part == "words"):
                    doc["rates"][k] = new[k]
                    doc["rates_later"][k] = now
        return ("this job stands on your wall panel rates as they are now" if part == "all"
                else "this job stands on your rates, thresholds and sizes as they are now" if part == "rates"
                else "this job's proposal uses your sentence and notes as they are now")
    if kind == "room_add":
        if len(doc["rooms"]) >= MAX_ROOMS:
            raise ActError(f"a job holds at most {MAX_ROOMS} rooms")
        name = _text(act.get("name"), ROOM_TEXT["name"], "name") or f"Room {doc['seq']}"
        room = clean_room({"id": new_id("r"), "name": name})
        doc["rooms"].append(room)
        doc["seq"] = int(doc.get("seq") or 1) + 1
        return f"{name} added"
    if kind == "room_set":
        r = _find(doc["rooms"], act.get("id"), "room")
        f, v = act.get("field"), act.get("value")
        if f in ROOM_TEXT:
            r[f] = _text(v, ROOM_TEXT[f], f)
            return "saved"
        if f == "height":
            t = _text(v, 30, "height")
            if t and not length_ft(t, bare=False):
                raise ActError(f'"{t}" is not a height - type it with its unit: 8\', 8 ft, 96" or 8\'-6"')
            r["height"] = t
            return f"panel height {R.ft_in(float(length_ft(t, bare=False)))}" if t else "no panel height"
        if f == "typed":
            t = _text(v, 400, "wall lengths")
            terms, why = parse_lengths(t)
            if why:
                raise ActError(why + " - nothing was saved")
            r["typed"] = t
            return f"{r['name']}: {qty_str(_round2(sum(terms, Fraction(0))), 2)} LF typed" if terms else f"{r['name']}: no typed wall lengths"
        raise ActError("no such field")
    if kind == "room_del":
        r = _find(doc["rooms"], act.get("id"), "room")
        doc["rooms"] = [x for x in doc["rooms"] if x["id"] != r["id"]]
        return f"{r['name'] or 'the room'} removed"
    if kind == "wall_put":
        r = _find(doc["rooms"], act.get("room"), "room")
        w = clean_wall(act.get("wall"))
        if not w:
            raise ActError("no wall")
        if len(r["walls"]) >= MAX_WALLS:
            raise ActError(f"a room holds at most {MAX_WALLS} walls picked on the sheets")
        if w["src"] == "line" and any(x["src"] == "line" and x["page"] == w["page"] and x["chain"] == w["chain"] for x in r["walls"]):
            raise ActError("that line is already one of this room's walls")
        r["walls"].append(w)
        return f"{r['name']}: a wall added from sheet {w['page'] + 1}"
    if kind == "wall_del":
        r = _find(doc["rooms"], act.get("room"), "room")
        n = len(r["walls"])
        r["walls"] = [x for x in r["walls"] if x["id"] != str(act.get("wall"))]
        if len(r["walls"]) == n:
            raise ActError("that wall is not on the room any more")
        return f"{r['name']}: the wall is off"
    if kind == "sheet_add":
        if len(doc["sheets"]) >= MAX_SHEETS:
            raise ActError(f"a job holds at most {MAX_SHEETS} sheet rows")
        size = ""
        if act.get("size") not in (None, ""):
            dims = S.size_dims(act.get("size"))
            if not dims:
                raise ActError(f'"{str(act.get("size"))[:30]}" is not a sheet size - width x length in feet, like 4x8')
            size = dims[0]
        doc["sheets"].append({"id": new_id("s"), "size": size, "count": None, "price_cents": None, "price_from": None})
        return "a sheet row added"
    if kind in ("sheet_set", "sheet_del", "sheet_price_item"):
        s = _find(doc["sheets"], act.get("id"), "sheet row")
        if kind == "sheet_del":
            doc["sheets"] = [x for x in doc["sheets"] if x["id"] != s["id"]]
            return "the sheet row removed"
        if kind == "sheet_price_item":
            item = next((it for it in (settings_now or {}).get("price_items") or [] if it["id"] == str(act.get("item"))), None)
            if item is None:
                raise ActError("that price item is not in your settings any more")
            if item["per"] == "sqft" and not s["size"]:
                raise ActError("choose the row's size first: a price a square foot needs the sheet's area")
            c = item_price_cents(item["per"], item["price"], s["size"])
            if not c:
                raise ActError("that price item gives no price for this size")
            if c > S.MAX_MONEY_CENTS:
                raise ActError(f"that price item gives {money(c)} a sheet for this size: a price here is at most {money(S.MAX_MONEY_CENTS)}")
            s["price_cents"] = c
            s["price_from"] = {"id": item["id"], "name": item["name"], "code": item["code"], "per": item["per"], "price": item["price"],
                               "size": s["size"], "at": now}
            return f"{money(c)} a sheet - {_price_words(s)}"
        f, v = act.get("field"), act.get("value")
        if f == "size":
            dims = S.size_dims(v)
            if not dims:
                raise ActError(f'"{str(v)[:30]}" is not a sheet size - width x length in feet, like 4x8')
            s["size"] = dims[0]
            pf = s.get("price_from")
            if pf and pf["per"] == "sqft":
                c = item_price_cents("sqft", pf["price"], s["size"])
                if not c or c > S.MAX_MONEY_CENTS:
                    raise ActError(f"at that size the row's price a square foot gives no price a sheet this job can hold (at most {money(S.MAX_MONEY_CENTS)})")
                s["price_cents"] = c
                pf["size"] = s["size"]
                return f"{s['size']}: the price follows the size - {money(s['price_cents'])} a sheet ({_price_words(s)})"
            return f"size {s['size']}" + (f" - the price stayed {money(s['price_cents'])} a sheet: check it for this size" if s["price_cents"] else "")
        if f == "count":
            if v in (None, ""):
                s["count"] = None
                return "no count"
            d = S._dec(v)
            if d is None or d < 0 or d > MAX_COUNT or d != d.to_integral_value():
                raise ActError(f'"{str(v)[:30]}" is not a sheet count - a whole number, 0 or more')
            s["count"] = int(d)
            return f"{s['count']} sheet{'s' if s['count'] != 1 else ''}"
        if f == "price":
            s["price_cents"] = _typed_cents(v, "price a sheet", zero_ok=False)
            s["price_from"] = None
            return f"{money(s['price_cents'])} a sheet, typed" if s["price_cents"] else "no price"
        raise ActError("no such field")
    if kind == "line_add":
        if len(doc["lines"]) >= MAX_LINES:
            raise ActError(f"a job holds at most {MAX_LINES} typed lines")
        doc["lines"].append({"id": new_id("l"), "what": "", "qty": None, "unit": "", "price_cents": None})
        return "a typed line added"
    if kind in ("line_set", "line_del"):
        ln = _find(doc["lines"], act.get("id"), "typed line")
        if kind == "line_del":
            doc["lines"] = [x for x in doc["lines"] if x["id"] != ln["id"]]
            return "the typed line removed"
        f, v = act.get("field"), act.get("value")
        if f == "what":
            ln["what"] = _text(v, 120, "what")
        elif f == "unit":
            ln["unit"] = _text(v, 12, "unit")
        elif f == "qty":
            ln["qty"] = _typed_qty(v, "quantity")
        elif f == "price":
            ln["price_cents"] = _typed_cents(v, "price")
        else:
            raise ActError("no such field")
        return "saved"
    raise ActError(f"{kind} needs the sheet: it is measured by the server first")


def take_unset(doc: dict, live: Optional[dict], now: str) -> list:
    """AT A WRITE: every value the job's own copy of the rates does not hold and the account's settings
    now do is taken into the copy, and is the job's from then on (critic wp-1, minor 2: a job started
    before the rates were loaded kept an empty copy and said "not set" for good). -> the keys taken.
    A value the copy holds is never touched; a job with no copy yet is not this function's (its first
    write copies the whole record)."""
    if not isinstance(doc.get("rates"), dict) or not isinstance(live, dict):
        return []
    eff, follows = S.standing(doc["rates"], live)
    if not isinstance(doc.get("rates_later"), dict):
        doc["rates_later"] = {}
    for k in follows:
        doc["rates"][k] = eff[k]
        doc["rates_later"][k] = now
    return follows


# ---- the two documents -------------------------------------------------------------------
# THE PROPOSAL IS BUILT FROM THIS LIST AND NOTHING ELSE. A rate, the days, the profit or any cost
# line is not on it, so none of them can reach the customer by being added to the job later.
PROPOSAL_KEYS = ("company", "contact", "date", "project", "location", "scope", "notes", "total", "total_words")


def proposal(comp: dict, doc: dict, company: str, contact: str, date: Optional[str] = None) -> dict:
    """What the customer's proposal prints, by allow-list (PROPOSAL_KEYS). Its date is the one `compute`
    answered - the date the page showed before issuing - unless a caller names another."""
    job = clean_doc(doc)["job"]
    if date is None:
        date = (comp.get("date") or {}).get("text") or ""
    full = {"company": str(company or "").strip(), "contact": str(contact or "").strip(), "date": date,
            "project": job["project"], "location": job["location"], "scope": (comp.get("scope") or {}).get("text") or "",
            "notes": list(comp.get("notes") or []), "total": comp.get("total") or "", "total_words": comp.get("total_words") or ""}
    return {k: full[k] for k in PROPOSAL_KEYS}


def worksheet_csv(comp: dict, doc: dict, rates: Optional[dict], job_name: str = "", differs: Optional[list] = None,
                  follows: Optional[list] = None) -> str:
    """The internal worksheet: every room with its walls and their arithmetic, the suggestion beside
    it, every line with what is behind it, the rates the job stands on (`follows`: the keys it follows
    the account's settings for, not yet its own), what holds the proposal, and the scope sentence with
    where each blank's value came from. Never for the customer."""
    doc = clean_doc(doc)
    rates = S.snapshot(rates or S.blank())
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Wall panel worksheet (internal - not for the customer)", job_name])
    w.writerow([])
    w.writerow(["Rooms", "No.", "Panel height", "Wall length LF", "How", "Product", "Molding"])
    for r in doc["rooms"]:
        f = comp["rooms"][r["id"]]
        prod = " / ".join(x for x in (r["mfr"], r["colour"], r["code"], r["fire"], r["thickness"]) if x)
        w.writerow([r["name"], r["number"], f["height_words"] or (r["height"] or "not typed"), f["wall_lf"] or "-", f["wall_words"], prod, r["molding"]])
        if r["typed"]:
            w.writerow(["", "", "", f["typed_lf"], "typed: " + r["typed"], "", ""])
        for o in f["walls"]:
            w.writerow(["", "", "", o["lf"] or "-", o["words"], "", ""])
        sg = f["suggestion"]
        for o in sg["options"]:
            w.writerow(["", "", "", "", f"{'or (instead, not as well)' if o.get('alt') else 'suggested'}: {o['arith']} - {sg['words']}", "", ""])
        if sg["why"]:
            w.writerow(["", "", "", "", sg["why"], "", ""])
        for fl in f["flags"]:
            w.writerow(["", "", "", "", "check: " + fl, "", ""])
    if comp.get("suggested"):
        w.writerow(["", "", "", "", "suggested in all: " + ", ".join(f"{x['count']} of {x['size']}" for x in comp["suggested"])
                    + f" ({comp['suggested_words']}; a sheet's width and length are read from its size's name, an inference)", "", ""])
    w.writerow([])
    w.writerow(["Line", "Qty", "Unit", "Rate", "Amount", "How"])
    for ln in comp["lines"]:
        w.writerow([ln["what"], ln["qty"], ln["unit"], ln["rate"], ln["amount"] or "-", "; ".join([ln["how"]] + ln["missing"]).strip("; ")])
    w.writerow(["Total", "", "", "", comp["total"], "the sum of the amounts above"])
    w.writerow(["In words", comp["total_words"]])
    w.writerow([])
    ra = doc.get("rates_at")
    w.writerow(["The rates this job stands on", ("taken from your settings " + ra["at"] + (" by " + ra["by"] if ra.get("by") else "")) if ra
                else "your settings as they are now (the job has not been written yet)"])
    follows, later = list(follows or []), doc.get("rates_later") or {}
    how = lambda k: ("follows your settings now: the job had none, and takes it at its next change" if k in follows
                     else f"taken from your settings {later[k]}, after the rest" if k in later else "")
    said = lambda k: [how(k)] if how(k) else []           # a fourth column only where there is something to say
    for k in S.RATE_KEYS:
        w.writerow(["", S.WORDS[k], rates[k] if rates[k] is not None else "not set"] + said(k))
    w.writerow(["", "sheet sizes", ", ".join(rates["sizes"]) or "not set"] + said("sizes"))
    for k in ("molding_types", "scope_template", "notes"):
        if how(k):
            w.writerow(["", S.SNAP_WORDS[k], "", how(k)])
    for d in differs or []:
        w.writerow(["", "changed since", d])
    for d in comp.get("price_notes") or []:
        w.writerow(["", "changed since", d])
    w.writerow([])
    sc = comp["scope"]
    w.writerow(["Proposal", "issued" if comp["issue"] else "HELD"])
    for h in comp["hold"]:
        w.writerow(["", "missing", h])
    dt = comp.get("date") or {}
    if dt.get("text"):
        w.writerow(["", "date", dt["text"], dt.get("by") or ""])
    w.writerow(["", "scope sentence", sc["text"] or "not set"])
    for b in sc["blanks"]:
        w.writerow(["", "blank " + b["name"], b["value"] or "", b["by"] or b["why"] or ""])
    for n in comp["notes"]:
        w.writerow(["", "note", n])
    return "﻿" + buf.getvalue()
