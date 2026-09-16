"""The customer's bid sheet as a template: an ordered list of lines, each
with a unit and the words that file a takeoff row onto it, kept per seat
like the letterhead (branding.py) and applied to a job's approved pages
for an export in THEIR order and THEIR units.

WHY. Beaumont Excavating (call 2026-09-15, 54:22-59:35): "one sheet with
every quantity, in my bid-sheet order and units". His printed sheet is 19
lines (Mobilization LS ... Outside Slab Prep SF) with three handwritten
additions (Radon LF, Site Drains LF, Boulder walls SF + add 1'), and the
call asked for the silt fence and the two tank counts. That sheet is the
DEFAULT below - every seat starts from it and edits its own copy.

MATCHING. The same grammar the bid view's name rules use in app.js
(bidTokenHit), so a row files the same way on screen and in the export:
tokens are tried on the normalised row name (uppercased, every run of
non-alphanumerics collapsed to one space); a token of three characters or
fewer must stand alone as a word ("SD" never matches inside "SDWK"); a
token written "=WORD" must stand alone at any length ("=FILL" never
matches inside "BACKFILL"); anything else is a substring. A line may also
carry `re`, a regex over the raw label, used for CORE's own calculator row
names. `not` words veto a line so "Outside Slab Prep" does not land on
"Slab Prep". FIRST LINE THAT MATCHES WINS, top to bottom, in the sheet's
own order. A row nothing matches is listed under "not on the bid sheet" -
never dropped (CLAUDE.md 5: silent loss is the failure mode).

UNITS. A row adds into a line only when its unit is the line's unit; a
row in another unit is listed on the line with its own unit and kept out
of the sum, and the export says so. A lump-sum line never sums.

WHERE IT LIVES. `bidsheet.json` beside users.json / branding.json in the
job store: accounts[<user>] then install then the default, present-key
wins so a seat that saved an empty sheet gets an empty sheet.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import uuid
from pathlib import Path

from takeoff_engine.pipesize import size_token

UNITS = ("LS", "SF", "CY", "LF", "EA", "SY", "TON", "GAL")
LINE_MAX = 60
NAME_MAX = 60
NOTE_MAX = 160
TOKENS_MAX = 16
TOKEN_MAX = 40
LABEL_ONLY = ("W", "G", "E")     # one-letter utility tokens never read from a CAD layer path

# Beaumont Excavating's printed sheet, scanned 2026-09-15 (Bid sheet,
# Exclusions, ect.pdf p1): the numbers and units are his handwriting on the
# printed lines; 20-22 are his handwritten additions; 23-25 fill the blank
# rows with what the call asked for.
DEFAULT_LINES = [
    {"n": 1, "name": "Mobilization", "unit": "LS", "match": [], "not": [],
     "note": "lump sum - never summed from the takeoff"},
    {"n": 2, "name": "Clear & Grub", "unit": "SF", "re": r"^Clear & Grub$",
     "match": ["CLEAR", "GRUB", "CLEARING"], "not": []},
    {"n": 3, "name": "Strip Topsoil", "unit": "CY", "re": r"^Strip Topsoil",
     "match": ["STRIP TOPSOIL", "TOPSOIL STRIP"], "not": ["PLACE", "ROUGH"]},
    {"n": 4, "name": "Site Cut", "unit": "CY", "re": r"^(Site Cut|Cut \(earthwork\))$",
     "match": ["=CUT"], "not": ["BOULDER"]},
    {"n": 5, "name": "Site Fill", "unit": "CY", "re": r"^(Site Fill|Fill \(earthwork\))$",
     "match": ["=FILL"], "not": []},
    {"n": 6, "name": "Mass Excavation", "unit": "CY", "re": r"^Mass Excavation",
     "match": ["MASS EXCAVATION", "MASS EX"], "not": []},
    {"n": 7, "name": "Perimeter Drain", "unit": "LF", "re": r"^Perimeter Drain",
     "match": ["PERIMETER DRAIN", "FOUNDATION DRAIN", "FOOTER DRAIN", "FOTER DRAIN"], "not": []},
    {"n": 8, "name": "Structure Excavation", "unit": "CY", "re": r"^Structure Excavation",
     "match": ["STRUCTURE EXCAVATION", "STRUCT EX", "STRUCTURAL EX"], "not": []},
    {"n": 9, "name": "Structure Backfill", "unit": "CY", "re": r"^Structure Backfill",
     "match": ["STRUCTURE BACKFILL", "STRUCT BACKFILL", "STRUCTURAL BACKFILL"], "not": []},
    {"n": 10, "name": "Mass Backfill", "unit": "CY", "match": [], "not": [],
     "note": "struck through on the printed sheet - his notes: \"we don't really use this much\""},
    {"n": 11, "name": "Slab Prep", "unit": "SF", "re": r"^Slab Prep$",
     "match": ["SLAB PREP", "SLAB"], "not": ["OUTSIDE", "PATIO", "WALK", "DRIVE", "EXTERIOR"],
     "note": "any concrete in the house: basement, garage, flat slabs, crawler prep"},
    {"n": 12, "name": "Rough Place Topsoil", "unit": "CY", "re": r"^Rough Place",
     "match": ["ROUGH PLACE", "PLACE TOPSOIL", "RESPREAD"], "not": [],
     "note": "6\" - 8\" on the printed sheet; 1 ft over the green areas in his notes"},
    {"n": 13, "name": "18\" Culvert", "unit": "LF", "match": ["CULVERT"], "not": [],
     "note": "length of culverts and sizes - usually one at the end of the drive"},
    {"n": 14, "name": "Drive Sub Grade Prep", "unit": "SF",
     "match": ["DRIVE SUBGRADE", "DRIVE SUB GRADE", "DRIVEWAY", "DRIVE", "SUBGRADE", "SUB GRADE"], "not": []},
    # Measured on Barn C3.0 (2026-09-16): "PROPOSED STORM SEWER" landed on
    # the sewer line, the OWTS field / chamber trench lines (a leach field,
    # not the house sewer) too, and the contour layers C-TOPO-MINR-E /
    # C-TOPO-MAJR-E fired the one-letter E of the electric line - hence the
    # STORM / FIELD / TOPO / CONTOUR vetoes below.
    {"n": 15, "name": "4\" Sewer", "unit": "LF", "match": ["SEWER", "SANITARY", "SS", "SSWR"],
     "not": ["TANK", "STORM", "FIELD"], "note": "house to a stub or to the septic tank"},
    {"n": 16, "name": "Water", "unit": "LF", "match": ["WATER", "WATR", "WM", "W", "DOMESTIC", "POTABLE"],
     "not": ["TANK", "STORM", "TOPO"], "note": "mechanical room or house to the curb stop; road cut and tap separate"},
    {"n": 17, "name": "Elec/Phone/TV - Trench", "unit": "LF",
     "match": ["ELECTRIC", "=ELEC", "UGE", "UE", "TELEPHONE", "TELE", "COMM", "CATV", "TV", "FIBER", "JOINT TRENCH", "E"],
     "not": ["TOPO", "CONTOUR"], "note": "side of the garage to the transformer or stub"},
    {"n": 18, "name": "Gas Trench only", "unit": "LF", "match": ["GAS", "G"], "not": ["TANK", "TOPO"],
     "note": "house to the stub"},
    {"n": 19, "name": "Outside Slab Prep", "unit": "SF",
     "match": ["OUTSIDE SLAB", "PATIO", "SIDEWALK", "WALK", "FLATWORK", "PATH", "CARPORT", "CAR PORT"], "not": [],
     "note": "concrete / gravel / sand paths, separated by material"},
    {"n": 20, "name": "Radon", "unit": "LF", "re": r"^Radon", "match": ["RADON"], "not": [],
     "note": "handwritten on the sheet: around the interior of the foundation to the mechanical room"},
    {"n": 21, "name": "Site Drains", "unit": "LF",
     "match": ["SITE DRAIN", "STORM", "SD", "ROOF DRAIN", "DOWNSPOUT", "AREA DRAIN", "GUTTER", "DRAIN", "INLET"],
     "not": ["PERIMETER", "FOUNDATION", "FOOTER"],
     "note": "handwritten: downspout, gutter and site drains (storm)"},
    {"n": 22, "name": "Boulder walls", "unit": "SF", "match": ["BOULDER", "ROCK WALL", "SITE WALL", "RETAINING"],
     "not": [], "note": "handwritten: surface SF + add 1'"},
    {"n": 23, "name": "Silt Fence", "unit": "LF", "re": r"^Silt / Construction Fence$",
     "match": ["SILT", "FENCE", "LIMIT OF DISTURBANCE", "LIMITS OF DISTURBANCE", "LIMIT OF WORK", "LIMITS OF WORK", "LOD"],
     "not": [], "note": "from the call: the limits-of-disturbance perimeter (the LOD line itself files here)"},
    {"n": 24, "name": "Septic Tank", "unit": "EA", "match": ["SEPTIC TANK"], "not": [],
     "note": "from the call: the size is read off the callout"},
    {"n": 25, "name": "Propane Tank", "unit": "EA", "match": ["PROPANE TANK"], "not": [],
     "note": "from the call"},
]


# ---- matching ------------------------------------------------------------------------
def norm_text(s) -> str:
    """Uppercase, every run of non-alphanumerics one space, padded so a
    whole-word test is a substring test on ' WORD '."""
    return " " + re.sub(r"[^A-Z0-9]+", " ", str("" if s is None else s).upper()).strip() + " "


def token_hit(norm: str, tok: str) -> bool:
    word = tok.startswith("=")
    t = tok[1:] if word else tok
    if not t:
        return False
    if word or len(t) <= 3:
        return (" " + t + " ") in norm
    return t in norm


def _vetoed(norm: str, nots) -> bool:
    return any(token_hit(norm, t) for t in (nots or ()))


def line_for(label, layer, lines) -> tuple[int | None, str | None]:
    """(index of the first line that files this row, how) - how is "re",
    the token, or None. Two passes like the bid view: the label alone,
    then the label with its CAD layer (one-letter utility tokens only on
    the first)."""
    raw = str("" if label is None else label)
    passes = [norm_text(raw), norm_text(raw + " " + str("" if layer is None else layer))]
    for p, norm in enumerate(passes):
        for i, ln in enumerate(lines):
            if _vetoed(norm, ln.get("not")):
                continue
            rx = ln.get("re")
            if rx and p == 0:
                try:
                    if re.search(rx, raw):
                        return i, "re"
                except re.error:
                    pass
            for tok in ln.get("match") or ():
                if p == 1 and tok in LABEL_ONLY:
                    continue
                if token_hit(norm, tok):
                    return i, tok
    return None, None


# ---- the approved rows -----------------------------------------------------------------
def item_unit(it) -> str:
    """A calculator row carries `unit`; a measured line family carries
    `length_ft` (feet); an object count carries neither."""
    u = it.get("unit")
    if u:
        u = str(u).strip().upper()
        return "LF" if u in ("FT", "FEET", "LF") else u
    if it.get("length_ft") is not None:
        return "LF"
    return "EA"


def item_qty(it) -> float:
    try:
        if it.get("unit"):
            return float(it.get("qty") or 0.0)
        if it.get("length_ft") is not None:
            return float(it.get("length_ft") or 0.0)
        return float(it.get("count") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def build(lines, pages, ignore=None) -> dict:
    """Apply `lines` to the approved pages ([{page, items}]). Returns
    {"lines": [{n, name, unit, qty, rows, other, note}], "unmatched": [rows],
    "pages": [...], "ignored": n}. `ignore` is a set of (page, label) the
    operator unticked on the cross-sheet panel (counted elsewhere)."""
    ignore = set(ignore or ())
    out = []
    for i, ln in enumerate(lines):
        out.append({"n": ln.get("n") or i + 1, "name": ln.get("name", ""), "unit": ln.get("unit", "EA"),
                    "qty": None if ln.get("unit") == "LS" else 0.0, "rows": [], "other": {},
                    "by_size": {}, "no_size_ft": 0.0, "note": ln.get("note", "")})
    unmatched = []
    ignored = 0
    for pg in pages:
        p = pg.get("page")
        for it in pg.get("items") or []:
            label = str(it.get("label", ""))
            if (p, label) in ignore:
                ignored += 1
                continue
            unit = item_unit(it)
            qty = item_qty(it)
            idx, how = line_for(label, it.get("layer"), lines)
            row = {"page": p, "label": label, "qty": round(qty, 1), "unit": unit, "ok": False, "how": how,
                   "count": it.get("count"), "set_by": it.get("qty_set_by")}
            if idx is None:
                unmatched.append(row)
                continue
            L = out[idx]
            ok = L["unit"] != "LS" and unit == L["unit"]
            row["ok"] = ok
            if ok:
                L["qty"] += qty
                if L["unit"] == "LF":          # storm LF per size: the size token in the row's name
                    sz = size_token(label)
                    if sz:
                        L["by_size"][sz] = L["by_size"].get(sz, 0.0) + qty
                    else:
                        L["no_size_ft"] += qty
            else:
                L["other"][unit] = L["other"].get(unit, 0.0) + qty
            L["rows"].append(row)
    for L in out:
        if L["qty"] is not None:
            L["qty"] = round(L["qty"], 1)
        L["other"] = {k: round(v, 1) for k, v in L["other"].items()}
        L["by_size"] = {k: round(v, 1) for k, v in sorted(L["by_size"].items(), key=lambda kv: -kv[1])}
        L["no_size_ft"] = round(L["no_size_ft"], 1)
    return {"lines": out, "unmatched": unmatched, "pages": [pg.get("page") for pg in pages], "ignored": ignored}


def scope_text(scope: str) -> str:
    return {"account": "your account's bid sheet", "install": "this server's bid sheet",
            "default": "the printed sheet (default - not edited yet)"}.get(scope, scope or "")


def _q(v) -> str:
    if v is None:
        return ""
    return f"{int(round(v))}" if abs(v - round(v)) < 0.05 else f"{v:.1f}"


def to_csv(res: dict, doc_name: str, scope: str) -> str:
    """One row per bid line in the sheet's order, blanks kept, then the rows
    that matched no line. Every number is the sum of approved rows; nothing
    is de-duplicated across sheets except what the operator unticked."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["CORE Takeoff - bid sheet", doc_name])
    w.writerow(["template", scope_text(scope)])
    w.writerow(["approved sheets", ", ".join(str(p + 1) for p in res["pages"])
                + (f" ({res['ignored']} row(s) unticked on the cross-sheet panel left out)" if res.get("ignored") else "")])
    w.writerow(["note", "quantities are sums of the approved sheets' rows; a row drawn on several sheets counts once per sheet"])
    w.writerow([])
    w.writerow(["#", "Item", "Unit", "Qty", "Rows", "From"])
    for L in res["lines"]:
        src = "; ".join(f"p{r['page'] + 1}: {r['label']} {_q(r['qty'])} {r['unit']}" + ("" if r["ok"] else " (not summed: other unit)")
                        for r in L["rows"])
        other = ", ".join(f"{_q(v)} {u}" for u, v in L["other"].items())
        # '10 in', not 10": an inch mark inside a quoted CSV cell doubles to ""
        bysz = "; ".join(f"{k.rstrip(chr(34))} in {_q(v)} ft" for k, v in (L.get("by_size") or {}).items())
        if bysz and L.get("no_size_ft"):
            bysz += f"; no size {_q(L['no_size_ft'])} ft"
        w.writerow([L["n"], L["name"], L["unit"],
                    _q(L["qty"]) if L["qty"] not in (None, 0.0) or L["rows"] else "",
                    len(L["rows"]), src + (f" [also {other} in other units]" if other else "")
                    + (f" [by size: {bysz}]" if bysz else "")])
    if res["unmatched"]:
        w.writerow([])
        w.writerow(["", "Not on the bid sheet", "", "", "", "rows no line matched - add a word to a line in Settings, or leave them out on purpose"])
        for r in res["unmatched"]:
            w.writerow(["", r["label"], r["unit"], _q(r["qty"]), 1, f"p{r['page'] + 1}"])
    return buf.getvalue()


# ---- the per-seat store (same shape as branding.py) -------------------------------------
def store_path() -> Path:
    root = os.environ.get("TAKEOFF_JOBS_DIR")
    base = (Path(root).expanduser() if root
            else Path(__file__).parent.parent / "out" / "jobs")
    return base / "bidsheet.json"


def _toks(v, cap=TOKENS_MAX) -> list:
    out = []
    if isinstance(v, str):
        v = v.split(",")
    for t in (v or []):
        if not isinstance(t, str):
            continue
        t = " ".join(t.strip().upper().split())[:TOKEN_MAX]
        if t and t not in out:
            out.append(t)
        if len(out) >= cap:
            break
    return out


def clean_lines(lines):
    """A list of valid lines in order, renumbered; a malformed entry is
    dropped, a bad regex is dropped from its line, an unknown unit drops
    the line (the UI only offers UNITS, so that is a client bug, not a
    choice). Returns None when `lines` is not a list at all."""
    if not isinstance(lines, list):
        return None
    out = []
    for ln in lines[:LINE_MAX]:
        if not isinstance(ln, dict):
            continue
        name = ln.get("name")
        if not isinstance(name, str):
            continue
        name = " ".join(name.split())[:NAME_MAX]
        unit = str(ln.get("unit") or "").strip().upper()
        if not name or unit not in UNITS:
            continue
        rec = {"n": len(out) + 1, "name": name, "unit": unit,
               "match": _toks(ln.get("match")), "not": _toks(ln.get("not"))}
        rx = ln.get("re")
        if isinstance(rx, str) and rx.strip():
            try:
                re.compile(rx)
                rec["re"] = rx[:200]
            except re.error:
                pass
        note = ln.get("note")
        if isinstance(note, str) and note.strip():
            rec["note"] = " ".join(note.split())[:NOTE_MAX]
        out.append(rec)
    return out


def load() -> dict:
    out = {"install": None, "accounts": {}}
    try:
        raw = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if not isinstance(raw, dict):
        return out
    inst = clean_lines(raw.get("install"))
    out["install"] = inst
    accts = raw.get("accounts")
    if isinstance(accts, dict):
        for k, v in accts.items():
            cl = clean_lines(v)
            if isinstance(k, str) and k and cl is not None:
                out["accounts"][k] = cl
    return out


def default_lines() -> list:
    return [dict(ln, match=list(ln["match"]), **{"not": list(ln["not"])}) for ln in DEFAULT_LINES]


def resolve(user: str | None) -> tuple[list, str]:
    """(lines, scope): the account's own sheet, else the install's, else
    the printed default. An account that saved an EMPTY sheet gets an
    empty sheet - present-key wins, as with the letterhead."""
    d = load()
    if user and user in d["accounts"]:
        return d["accounts"][user], "account"
    if d["install"] is not None:
        return d["install"], "install"
    return default_lines(), "default"


def save(user: str | None, lines) -> tuple[list, str]:
    """Write the seat's sheet (a list) or remove it (None -> back to the
    default). Atomic tmp + os.replace like branding.save."""
    d = load()
    cl = clean_lines(lines) if lines is not None else None
    raw = {"install": d["install"], "accounts": dict(d["accounts"])}
    if user:
        if cl is None:
            raw["accounts"].pop(user, None)
        else:
            raw["accounts"][user] = cl
    else:
        raw["install"] = cl
    f = store_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(raw, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, f)
    return resolve(user)
