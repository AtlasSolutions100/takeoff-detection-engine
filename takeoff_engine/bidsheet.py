"""The customer's bid sheet as a template: an ordered list of lines, each
with a unit and the words that file a takeoff row onto it, kept per seat
like the letterhead (branding.py) and applied to a job's approved pages
for an export in THEIR order and THEIR units.

WHY. An excavation client (call 2026-09-15, 54:22-59:35): "one sheet with
every quantity, in my bid-sheet order and units". His printed sheet is 19
lines (Mobilization LS ... Outside Slab Prep SF) with three hand-added
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

THREE PASSES over the same lines: the label alone; the label plus the
utility a trench run was calculated as (Water / Sewer / Storm / Gas /
Electric - the operator's pick in the trench panel, carried on the row as
`utility`), so a run over a pattern-named family ("dash1 d72.0 g21.6")
lands on the utility's line instead of under "not on the bid sheet"
(audit 2026-09-26, plan 7.4: a 1,526 LF water main filed nowhere); then
the label with its CAD layer (one-letter utility tokens only on the
label passes).

NET ROWS (audit D02 / D17). The cut/fill engine hands over Site Cut and
Site Fill (gross, bank measure) AND the net as its own row - Export Cut
when the site cuts more than it fills, Import Structural the other way
(the live product's calculator says Export Surplus Cut / Import Fill).
A net row is NEVER filed by a cut or fill token: "=CUT" used to take
"Export Cut" onto Site Cut and a cut-only site doubled exactly (1,234 ->
2,468 CY). A row whose label starts with Export or Import (NET_ROW_RE)
files only on a line whose token names the net - EXPORT, IMPORT, HAUL or
NET - or whose `re` matches it. The default sheet carries lines 26 and 27
for exactly these rows; a seat whose saved sheet has no such line sees
its net rows under "not on the bid sheet", never inside Site Cut.

A TRENCH RUN REPLACES ITS FAMILY (audit D03). The trench tool's "(run)"
row is the same footage as the line family it was calculated over, so a
sheet with both counted the sewer twice (563.2 LF against 270 on the
client's estimate). The run row carries `run_of` (the family's label) and
`run_sid` (its style id); the family item carries `by_style` (its footage
per style id, written by approve). On the bid line the family's footage
minus the styles a run covers is what sums, and the run rows sum in full.
The family row stays listed with a note saying what replaced it. Without
`by_style` / `run_sid` (an export from before 2026-09-26) the whole
family is treated as replaced.

UNITS. A row adds into a line only when its unit is the line's unit; a
row in another unit is listed on the line with its own unit and kept out
of the sum, and the export says so. A lump-sum line never sums.

WHERE IT LIVES. `bidsheet.json` beside users.json / branding.json in the
job store: accounts[<user>] then install then the default, present-key
wins so a seat that saved an empty sheet gets an empty sheet.

A SHEET SAVED BEFORE THE NET LINES (release check 2026-10-01). v1.8.5 kept
the same file with no version, and its default had 25 lines: a seat that
saved its own sheet then has no line for Export Cut / Import Structural /
Export Topsoil / Import Topsoil, so those rows fell under "not on the bid
sheet" without a word. bidsheet_versions.json beside it says which
template version each saved sheet was last saved at (missing = 1, v1.8.5;
see versions_path). On load a sheet
below TEMPLATE_VERSION gets the lines UPGRADES names appended at its end -
only those its own lines do not already file, the seat's lines, order,
names and prices untouched - and `added` reports them so Settings can say
so. Nothing is written on a read: the next save stamps the version, so a
line the seat removes after that stays removed.
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

# An excavation client's printed sheet, scanned 2026-09-15 (Bid sheet,
# Exclusions, ect.pdf p1): the numbers and units are his handwriting on the
# printed lines; 20-22 are his own hand-added lines; 23-25 fill the blank
# rows with what the call asked for.
DEFAULT_LINES = [
    {"n": 1, "name": "Mobilization", "unit": "LS", "match": [], "not": [],
     "note": "lump sum - never summed from the takeoff"},
    {"n": 2, "name": "Clear & Grub", "unit": "SF", "re": r"^Clear & Grub$",
     "match": ["CLEAR", "GRUB", "CLEARING"], "not": []},
    {"n": 3, "name": "Strip Topsoil", "unit": "CY", "re": r"^Strip Topsoil",
     "match": ["STRIP TOPSOIL", "TOPSOIL STRIP"], "not": ["PLACE", "ROUGH"]},
    # Lines 4 and 5 are GROSS: what the surfaces say is dug and what is
    # placed, bank measure. The net (haul-off or import) is its own row from
    # the cut/fill engine and has its own lines, 26 and 27 below; the EXPORT /
    # IMPORT / HAUL / NET vetoes keep a net row out of the gross lines even on
    # a seat that edited these tokens (line_for refuses that on its own too).
    # "Export Cut" carries the whole-word CUT and doubled a cut-only site on
    # line 4 (1,234 -> 2,468 CY); BACKFILL never files as site fill.
    {"n": 4, "name": "Site Cut", "unit": "CY", "re": r"^(Site Cut|Cut \(earthwork\))$",
     "match": ["SITE CUT", "=CUT"], "not": ["BOULDER", "EXPORT", "IMPORT", "HAUL", "NET"],
     "note": "gross cut, bank measure - the export (net) is line 26, never added in here"},
    {"n": 5, "name": "Site Fill", "unit": "CY", "re": r"^(Site Fill|Fill \(earthwork\))$",
     "match": ["SITE FILL", "=FILL"], "not": ["EXPORT", "IMPORT", "HAUL", "NET", "BACKFILL"],
     "note": "gross fill, bank measure - the import (net) is line 27, never added in here"},
    {"n": 6, "name": "Mass Excavation", "unit": "CY", "re": r"^Mass Excavation",
     "match": ["MASS EXCAVATION", "MASS EX"], "not": []},
    {"n": 7, "name": "Perimeter Drain", "unit": "LF", "re": r"^Perimeter Drain",
     "match": ["PERIMETER DRAIN", "FOUNDATION DRAIN", "FOOTER DRAIN", "FOTER DRAIN"], "not": []},
    {"n": 8, "name": "Structure Excavation", "unit": "CY", "re": r"^Structure Excavation",
     "match": ["STRUCTURE EXCAVATION", "STRUCT EX", "STRUCTURAL EX"], "not": []},
    {"n": 9, "name": "Structure Backfill", "unit": "CY", "re": r"^Structure Backfill",
     "match": ["STRUCTURE BACKFILL", "STRUCT BACKFILL", "STRUCTURAL BACKFILL"], "not": []},
    {"n": 10, "name": "Mass Backfill", "unit": "CY", "match": [], "not": [],
     "note": "rarely used - delete this line if you never bid it"},
    {"n": 11, "name": "Slab Prep", "unit": "SF", "re": r"^Slab Prep$",
     "match": ["SLAB PREP", "SLAB"], "not": ["OUTSIDE", "PATIO", "WALK", "DRIVE", "EXTERIOR"],
     "note": "any concrete in the house: basement, garage, flat slabs, crawler prep"},
    {"n": 12, "name": "Rough Place Topsoil", "unit": "CY", "re": r"^Rough Place",
     "match": ["ROUGH PLACE", "PLACE TOPSOIL", "RESPREAD"], "not": [],
     "note": "6\" - 8\" deep; 1 ft over the landscaped (green) areas"},
    {"n": 13, "name": "18\" Culvert", "unit": "LF", "match": ["CULVERT"], "not": [],
     "note": "length of culverts and sizes - usually one at the end of the drive"},
    {"n": 14, "name": "Drive Sub Grade Prep", "unit": "SF",
     "match": ["DRIVE SUBGRADE", "DRIVE SUB GRADE", "DRIVEWAY", "DRIVE", "SUBGRADE", "SUB GRADE"], "not": []},
    # Measured on Res-A C3.0 (2026-09-16): "PROPOSED STORM SEWER" landed on
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
     "note": "around the inside of the foundation to the mechanical room"},
    {"n": 21, "name": "Site Drains", "unit": "LF",
     "match": ["SITE DRAIN", "STORM", "SD", "ROOF DRAIN", "DOWNSPOUT", "AREA DRAIN", "GUTTER", "DRAIN", "INLET"],
     "not": ["PERIMETER", "FOUNDATION", "FOOTER"],
     "note": "downspout, gutter and site drains (storm)"},
    {"n": 22, "name": "Boulder walls", "unit": "SF", "match": ["BOULDER", "ROCK WALL", "SITE WALL", "RETAINING"],
     "not": [], "note": "wall face SF, plus 1 ft"},
    {"n": 23, "name": "Silt Fence", "unit": "LF", "re": r"^Silt / Construction Fence$",
     "match": ["SILT", "CONSTRUCTION FENCE", "SEDIMENT LOG", "WATTLE", "LIMIT OF DISTURBANCE", "LIMITS OF DISTURBANCE",
               "LIMIT OF WORK", "LIMITS OF WORK", "LOD"],
     "not": [], "note": "the limits-of-disturbance perimeter (the LOD line itself files here); "
                        "a plain fence is not silt fence and does not file here"},
    {"n": 24, "name": "Septic Tank", "unit": "EA", "match": ["SEPTIC TANK"], "not": [],
     "note": "size read off the callout"},
    {"n": 25, "name": "Propane Tank", "unit": "EA", "match": ["PROPANE TANK"], "not": [],
     "note": "count of propane tanks shown on the plan"},
    # 26-29 are NOT on the printed sheet: they are where the rows of the cut /
    # fill balance go (audit 2026-09-26, D02 / D17). The priced estimate has
    # these lines under its own names (Import Structural, Export Topsoil).
    # Until then "Export Cut" filed onto Site Cut through the =CUT token and
    # Import Structural onto no line at all. Net of the two gross figures,
    # bank measure, no swell or shrink applied unless the operator typed a
    # factor in the engine (the row's basis says so).
    {"n": 26, "name": "Export Cut", "unit": "CY", "re": r"^Export (Surplus )?(Cut|Surplus)",
     "match": ["EXPORT", "HAUL OFF", "HAUL-OFF", "HAUL"], "not": ["TOPSOIL"],
     "note": "net: cut the site fill does not use, bank measure - the haul-off; not part of line 4"},
    {"n": 27, "name": "Import Structural", "unit": "CY", "re": r"^Import (Structural|Fill)",
     "match": ["IMPORT"], "not": ["TOPSOIL"],
     "note": "net: fill the site cut does not cover, bank measure - the structural fill brought in; not part of line 5"},
    {"n": 28, "name": "Export Topsoil", "unit": "CY", "re": r"^Export Topsoil$",
     "match": ["EXPORT TOPSOIL"], "not": [], "note": "stripped topsoil not placed back (the priced estimate's own line)"},
    {"n": 29, "name": "Import Topsoil", "unit": "CY", "re": r"^Import Topsoil$",
     "match": ["IMPORT TOPSOIL"], "not": [], "note": "topsoil placed beyond what was stripped"},
]


# ---- matching ------------------------------------------------------------------------
def norm_text(s) -> str:
    """Uppercase, every run of non-alphanumerics one space, padded so a
    whole-word test is a substring test on ' WORD '."""
    return " " + re.sub(r"[^A-Z0-9]+", " ", str("" if s is None else s).upper()).strip() + " "


def token_hit(norm: str, tok: str) -> bool:
    # The typed word gets the SAME cleaning as the row name (UI verify 2026-10-02 #2: a
    # match word "O-PIPE" never met the row "O-PIPE", which norm_text makes " O PIPE ").
    # Mirrored in app.js bidTokenHit.
    word = tok.startswith("=")
    t = norm_text(tok[1:] if word else tok).strip()
    if not t:
        return False
    if word or len(t) <= 3:
        return (" " + t + " ") in norm
    return t in norm


def _vetoed(norm: str, nots) -> bool:
    return any(token_hit(norm, t) for t in (nots or ()))


# A NET row: the cut/fill hand-off's Export Cut / Import Structural, the live
# calculator's Export Surplus Cut / Import Fill. Mirrored in app.js
# (bidCfKind / BS_NET_RE) so the screen refuses the same filings the CSV does.
NET_ROW_RE = re.compile(r"^(Export|Import)\b", re.I)
NET_WORDS = ("EXPORT", "IMPORT", "HAUL", "NET")
# The trench panel's utility pick -> the words the second pass adds to a
# run's label. Mirrored in app.js (BS_UTILITY_WORDS).
UTILITY_WORDS = {"water": "WATER", "sewer": "SANITARY SEWER", "storm": "STORM DRAIN",
                 "gas": "GAS", "electric": "ELECTRIC"}
RUN_SUFFIX = " (run)"


# A grading zone's own cut / fill row ("Site Cut – Island", cutfill/takeoff.py ZONE_ROW_SEP)
# files exactly as Site Cut / Site Fill: the zone's name never reaches the matcher, so a zone
# called "export pad" or "haul road" cannot veto it off its line. Mirrored in app.js (BS_CF_PART_RE).
CF_PART_RE = re.compile(r"^(Site (?:Cut|Fill)) – .+$")   # the engine's en dash only: a typed "Site Cut - haul" keeps its vetoes


def is_net_row(label, kind=None) -> bool:
    return kind == "net" or bool(NET_ROW_RE.match(str("" if label is None else label)))


# A LAYER'S OWN ROW (release check 2026-10-01). The live product's earthwork
# calculator wrote one row per layer the operator named: Export <layer>,
# Rough Place <layer> (live takeoff_engine/earthwork.py:1126-1160) and
# Remove <layer> (live app.js ewItemLabel). The default sheet names a home
# only for cut (26 Export Cut), and for topsoil (3, 12, 28, 29) - read off
# its own line names; it has no line for asphalt, base course or any other
# layer. So "Export Asphalt" filing on Export Cut through the bare EXPORT
# token (and "Rough Place Asphalt" on Rough Place Topsoil through ^Rough
# Place) was a guess: such a row files only on a line with a token that
# names the layer itself - and, for Export, also the net, as every net row
# must - and otherwise stays under "not on the bid sheet" with its
# quantity. A line's `re` never files it (the defaults are written for
# CORE's own cut / topsoil names). Mirrored in app.js (BS_LAYER_ROW_RE).
# Repair 2026-10-01 (critic RISK-1): "Removal of <x>" is the same row as
# "Remove <x>" (one rule for both spellings), and a cut row keeps its line
# under ordinary variations of its name - "Export cut (net)", "Export Cut -
# Zone A", "Export Cut Material", "Export surplus" - because the layer name
# counts as cut when its FIRST word is cut (after an optional "surplus").
LAYER_ROW_RE = re.compile(r"^(Export|Rough Place|Remove|Removal(?:\s+of)?)\s+(\S.*)$", re.I)
DIRT_LAYER_RE = re.compile(r"^(surplus\s+)?cut\b|^surplus\b|\btopsoil\b", re.I)
LAYER_VERB = {"export": None, "rough place": ("PLACE",), "remove": ("REMOVE", "REMOVAL")}


def _layer_verb(word: str) -> str:
    w = " ".join(str(word).lower().split())
    return "remove" if w.startswith("remov") else w


def foreign_layer(label):
    """(verb, LAYER NAME normalised) for a calculator layer row whose layer
    is not cut or topsoil, else None."""
    m = LAYER_ROW_RE.match(str("" if label is None else label).strip())
    if not m or DIRT_LAYER_RE.search(m.group(2).strip()):
        return None
    name = norm_text(m.group(2)).strip()
    return (_layer_verb(m.group(1)), name) if name else None


def _names_layer(tok: str, fl) -> bool:
    """A token may file a foreign layer row only if it names the layer (its
    words, whole) and the row's verb (Export: the net words, checked by the
    net rule; Rough Place: PLACE; Remove / Removal: REMOVE or REMOVAL)."""
    t = norm_text(str(tok).lstrip("="))
    verb, name = fl
    if (" " + name + " ") not in t:
        return False
    ws = LAYER_VERB.get(verb)
    return ws is None or any((" " + w + " ") in t for w in ws)


# CURB AND GUTTER (repair 2026-10-01, critic RISK-7; base = live). "CURB AND
# GUTTER" 800 LF summed on 21 Site Drains through the bare token GUTTER. An
# INFERENCE from the default sheet's own words: line 21's note is "downspout,
# gutter and site drains (storm)" - a roof or trench gutter is drainage -
# while curb and gutter is concrete flatwork at the pavement edge. So a curb-
# and-gutter row (CURB AND GUTTER, CURB & GUTTER, CURB/GUTTER, C&G, with or
# without a size) files only on a line whose own name says curb (first) or
# gutter, and otherwise under "not on the bid sheet" with its quantity. No
# token is changed. Mirrored in app.js (BS_CG_ROW_RE).
CG_ROW_RE = re.compile(r"\bcurb\s*(?:and|&|n|/|\+)?\s*gutters?\b|\bc\s*&\s*g\b", re.I)
CG_CURB_RE = re.compile(r"\bcurb|\bc\s*&\s*g\b", re.I)
CG_GUTTER_RE = re.compile(r"\bgutter", re.I)


def _names_net(tok: str) -> bool:
    """Only a token that itself says export / import / haul / net may file a
    net row - never CUT, FILL, SITE CUT or a seat's own edit of them."""
    t = str(tok).lstrip("=")
    return any(w in t for w in NET_WORDS)


def line_for(label, layer, lines, utility=None, kind=None) -> tuple[int | None, str | None]:
    """(index of the first line that files this row, how) - how is "re",
    the token, or None. Three passes like the bid view: the label alone,
    the label plus its trench utility word (when the row carries one), then
    the label with its CAD layer (one-letter utility tokens only on the
    label passes). A net row (see NET_ROW_RE) is filed only by a token that
    names the net, or by a line's own regex."""
    raw = str("" if label is None else label)
    m = CF_PART_RE.match(raw)
    if m:
        raw = m.group(1)
    if CG_ROW_RE.search(raw):
        # curb and gutter files only on a line whose NAME says curb / gutter (never on Site
        # Drains through its GUTTER token, nor on Gas through the one-letter G of "C&G")
        for want in (CG_CURB_RE, CG_GUTTER_RE):
            for i, ln in enumerate(lines):
                if want.search(str(ln.get("name") or "")):
                    return i, "name"
        return None, None
    net = is_net_row(raw, kind)
    fl = foreign_layer(raw) if kind != "net" else None
    uw = UTILITY_WORDS.get(str(utility or "").strip().lower())
    passes = [(0, norm_text(raw))]
    if uw:
        passes.append((1, norm_text(raw + " " + uw)))
    passes.append((2, norm_text(raw + " " + str("" if layer is None else layer))))
    for p, norm in passes:
        for i, ln in enumerate(lines):
            if _vetoed(norm, ln.get("not")):
                continue
            rx = ln.get("re")
            if rx and p == 0 and not fl:
                try:
                    if re.search(rx, raw):
                        return i, "re"
                except re.error:
                    pass
            for tok in ln.get("match") or ():
                if p == 2 and tok in LABEL_ONLY:
                    continue
                if net and not _names_net(tok):
                    continue
                if fl:
                    # a layer row files on a token that names its layer and verb, whichever
                    # spelling of the verb the row uses ("Removal of x" on a REMOVE X line)
                    if _names_layer(tok, fl):
                        return i, tok
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


def _sid(v):
    """A style id off a row: an int, or a numeric string; anything else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return None


def run_rows(items) -> list:
    """The trench tool's run rows among one page's items: [(run_of, run_sid,
    item)]. A row is a run when it says so (`kind: trench_run`) or, for an
    export from before the field existed, when its label ends " (run)" - in
    which case the family it stands for is the label without the suffix."""
    out = []
    for it in items or []:
        label = str(it.get("label", ""))
        legacy = label.endswith(RUN_SUFFIX)
        if it.get("kind") != "trench_run" and not legacy:
            continue
        of = it.get("run_of") or (label[:-len(RUN_SUFFIX)] if legacy else "")
        out.append((str(of), _sid(it.get("run_sid")), it))
    return out


def family_replacement(it, runs) -> tuple[float, int]:
    """(footage of this line family replaced by trench runs, number of runs)
    for a measured family item. With `by_style` on the family and `run_sid`
    on every run, exactly the styles the runs were calculated over are
    replaced; otherwise the whole family is (the run IS that footage)."""
    label = str(it.get("label", ""))
    mine = [(sid, r) for of, sid, r in runs if of == label]
    if not mine or it.get("unit") or it.get("length_ft") is None:
        return 0.0, 0
    ft = item_qty(it)
    # tagged runs (kind trench_run, run_of set) are parts of the family - the trench tool's runs
    # go structure to structure inside one family - so their own feet are what the family loses;
    # a legacy "(run)" row with no tags stands for the whole family, as before
    tagged = [r for _, r in mine if r.get("kind") == "trench_run" and r.get("run_of")]
    if tagged and len(tagged) == len(mine):
        covered = 0.0
        for r in tagged:
            try:
                covered += float(item_qty(r) or 0.0)
            except (TypeError, ValueError):
                pass
        return min(covered, ft), len(mine)
    bs = it.get("by_style") if isinstance(it.get("by_style"), dict) else None
    if bs and all(sid is not None for sid, _ in mine):
        covered = 0.0
        for sid in {sid for sid, _ in mine}:
            try:
                covered += float(bs.get(str(sid), bs.get(sid, 0.0)) or 0.0)
            except (TypeError, ValueError):
                pass
        return min(covered, ft), len(mine)
    return ft, len(mine)


def build(lines, pages, ignore=None) -> dict:
    """Apply `lines` to the approved pages ([{page, items}]). Returns
    {"lines": [{n, name, unit, qty, rows, other, note}], "unmatched": [rows],
    "pages": [...], "ignored": n}. `ignore` is a set of (page, label) the
    operator unticked on the cross-sheet panel (counted elsewhere). A row's
    `qty` is what was measured; `counted` is what reached the line's sum
    (less than qty only for a family a trench run replaced, `note` says)."""
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
        items = pg.get("items") or []
        runs = run_rows(items)
        for it in items:
            label = str(it.get("label", ""))
            if (p, label) in ignore:
                ignored += 1
                continue
            unit = item_unit(it)
            qty = item_qty(it)
            counted = qty
            note = ""
            replaced, nruns = family_replacement(it, runs) if runs else (0.0, 0)
            utility = str(it.get("utility") or "").strip().lower()
            if nruns:
                counted = max(0.0, qty - replaced)
                note = (f"{_q(replaced)} {unit} replaced by {nruns} trench run row{'s' if nruns > 1 else ''}"
                        + (f"; {_q(counted)} {unit} not run counts" if counted > 0.05 else ""))
                # the family follows its run onto the utility's line, so a
                # pattern-named family a run replaced is listed beside the
                # run that carries its footage, never under "not on the sheet"
                if not utility:
                    utility = next((str(r.get("utility") or "").strip().lower()
                                    for of, _, r in runs if of == label and r.get("utility")), "")
            idx, how = line_for(label, it.get("layer"), lines, utility=utility or None, kind=it.get("kind"))
            row = {"page": p, "label": label, "qty": round(qty, 1), "counted": round(counted, 1), "unit": unit,
                   "ok": False, "how": how, "count": it.get("count"), "set_by": it.get("qty_set_by"),
                   "note": note, "utility": utility or None, "kind": it.get("kind")}
            if idx is None:
                unmatched.append(row)
                continue
            L = out[idx]
            ok = L["unit"] != "LS" and unit == L["unit"]
            row["ok"] = ok
            if ok:
                L["qty"] += counted
                if L["unit"] == "LF":          # storm LF per size: the size token in the row's name
                    sz = size_token(label)
                    if sz:
                        L["by_size"][sz] = L["by_size"].get(sz, 0.0) + counted
                    else:
                        L["no_size_ft"] += counted
            else:
                L["other"][unit] = L["other"].get(unit, 0.0) + counted
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
        src = "; ".join(f"p{r['page'] + 1}: {r['label']} {_q(r['qty'])} {r['unit']}"
                        + (f" [{r['utility']}]" if r.get("utility") else "")
                        + ("" if r["ok"] else " (not summed: other unit)")
                        + (f" ({r['note']})" if r.get("note") else "")
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
            # what counts: a family a trench run replaced lists the feet no run covers
            w.writerow(["", r["label"], r["unit"], _q(r.get("counted", r["qty"])), 1,
                        f"p{r['page'] + 1}" + (f" ({r['note']})" if r.get("note") else "")])
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


# The template's version. 1 = v1.8.5 (25 lines, the file had no version);
# 2 = lines 26-29 for the cut / fill balance's net rows (audit D02 / D17).
TEMPLATE_VERSION = 2
UPGRADES = {2: ("Export Cut", "Import Structural", "Export Topsoil", "Import Topsoil")}


def _version(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 1


def upgrade_lines(lines: list, version: int) -> tuple[list, list]:
    """(lines, names added): a sheet saved at `version` with the default
    lines added since appended at its end, renumbered after the seat's own.
    A line is skipped when the sheet already has a line of that name or
    one of its lines already files that row (the seat made its own home
    for it), or when the sheet is full (LINE_MAX) - the row then stays
    listed under "not on the bid sheet", never dropped."""
    out = [dict(ln) for ln in lines]
    added = []
    for v in range(_version(version) + 1, TEMPLATE_VERSION + 1):
        for name in UPGRADES.get(v, ()):
            src = next((ln for ln in DEFAULT_LINES if ln["name"] == name), None)
            if src is None or len(out) >= LINE_MAX:
                continue
            if any(norm_text(ln.get("name")) == norm_text(name) for ln in out):
                continue
            if line_for(name, None, out)[0] is not None:
                continue
            out.append(dict(src, n=len(out) + 1, match=list(src["match"]), **{"not": list(src["not"])}))
            added.append(name)
    return out, added


# PER SHEET (repair 2026-10-01, critic RISK-2). Each saved sheet carries its
# own version, kept in a sidecar file beside bidsheet.json that v1.8.5's code
# never reads or writes - so an undo to v1.8.5 and back keeps the marks (its
# save rewrites bidsheet.json without them). A save writes only the seat's
# own sheet and stamps only it; every other sheet is written back exactly as
# it was on disk, keeps its version and is upgraded on its own next load,
# with its own "lines added" note. A sheet stamped by a newer build is never
# stamped down. Read: the sidecar, else the in-file "versions" key a build
# of this branch wrote before the sidecar (dev e71e67b), else 1 (v1.8.5).
def versions_path() -> Path:
    return store_path().with_name("bidsheet_versions.json")


def _read_raw() -> dict:
    try:
        raw = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _read_versions(raw: dict) -> dict:
    """{"install": v | None, "accounts": {user: v}} - sidecar first, then the
    old in-file key, per sheet."""
    out = {"install": None, "accounts": {}}
    for src in (raw.get("versions"), _json_or_none(versions_path())):
        if not isinstance(src, dict):
            continue
        if src.get("install") is not None:
            out["install"] = src.get("install")
        acc = src.get("accounts")
        if isinstance(acc, dict):
            out["accounts"].update({k: v for k, v in acc.items() if v is not None})
    return out


def _json_or_none(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_atomic(f: Path, obj) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, f)


def load() -> dict:
    out = {"install": None, "accounts": {}, "added": {"install": [], "accounts": {}}}
    raw = _read_raw()
    if not raw:
        return out
    vers = _read_versions(raw)
    vacc = vers["accounts"]
    inst = clean_lines(raw.get("install"))
    if inst is not None:
        inst, out["added"]["install"] = upgrade_lines(inst, vers.get("install"))
    out["install"] = inst
    accts = raw.get("accounts")
    if isinstance(accts, dict):
        for k, v in accts.items():
            cl = clean_lines(v)
            if isinstance(k, str) and k and cl is not None:
                cl, add = upgrade_lines(cl, vacc.get(k))
                out["accounts"][k] = cl
                if add:
                    out["added"]["accounts"][k] = add
    return out


def default_lines() -> list:
    return [dict(ln, match=list(ln["match"]), **{"not": list(ln["not"])}) for ln in DEFAULT_LINES]


def resolve_full(user: str | None) -> tuple[list, str, list]:
    """(lines, scope, added): resolve() plus the names of the lines a
    sheet saved on an older template gained on this load (empty once the
    seat saves)."""
    d = load()
    if user and user in d["accounts"]:
        return d["accounts"][user], "account", d["added"]["accounts"].get(user, [])
    if d["install"] is not None:
        return d["install"], "install", d["added"]["install"]
    return default_lines(), "default", []


def resolve(user: str | None) -> tuple[list, str]:
    """(lines, scope): the account's own sheet, else the install's, else
    the printed default. An account that saved an EMPTY sheet gets an
    empty sheet - present-key wins, as with the letterhead. A sheet saved
    on an older template comes with the lines added since (upgrade_lines)."""
    lines, scope, _ = resolve_full(user)
    return lines, scope


def save(user: str | None, lines) -> tuple[list, str]:
    """Write the seat's sheet (a list) or remove it (None -> back to the
    default). Atomic tmp + os.replace like branding.save. Only this seat's
    sheet is written and stamped (see versions_path): every other sheet is
    written back as it was on disk, with its own version."""
    old = _read_raw()
    vers = _read_versions(old)
    cl = clean_lines(lines) if lines is not None else None
    accts = old.get("accounts") if isinstance(old.get("accounts"), dict) else {}
    raw = {"install": old.get("install"), "accounts": dict(accts)}
    if user:
        if cl is None:
            raw["accounts"].pop(user, None)
            vers["accounts"].pop(user, None)
        else:
            raw["accounts"][user] = cl
            vers["accounts"][user] = max(_version(vers["accounts"].get(user)), TEMPLATE_VERSION)
    else:
        raw["install"] = cl
        vers["install"] = None if cl is None else max(_version(vers["install"]), TEMPLATE_VERSION)
    # the lines first, the stamp second: a crash between leaves the sheet
    # unstamped, which re-adds nothing it has by name (upgrade_lines)
    _write_atomic(store_path(), raw)
    _write_atomic(versions_path(), vers)
    return resolve(user)
