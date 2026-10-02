"""What the architectural datum 100'-0" equals, read off a set's own words.

Structural sheets call out footings on an architectural datum (client
call 2026-09-15: "the arch 100 datum can differ per building; typed per
structure, auto-read only as a suggestion, never blind"). The civil set is
where the number lives:

  * A residential civil C3.0 prints ``[100'=5000']`` beside the entry - the datum
    statement itself - and an FFE SUMMARY: ``MAIN LEVEL ENTRY:5001.25'``,
    ``MAIN LEVEL:5000.25'``, ``LOWER LEVEL FFE: 4987.125'``.
  * Another structural S1-1.0 says only what the datum IS:
    ``DATUM ELEVATION 100'-0" EQUALS TOP OF MAIN LEVEL SUBFLOOR ELEVATION.
    SEE CIVIL FOR USGS DATUM.`` - a note with no number.
  * Civil spot labels print shortened elevations (``FFE: 01.25`` for
    5001.25); those are not a datum and are left out (values under 100).

Returns suggestions with their evidence (page, the printed text); the
structure panel offers them and the operator types the value that applies.
"""
from __future__ import annotations

import re

_FT = "['’′]"
_IN = '["”″]'
# "[100'=5000']", "ARCH. 100' = CIVIL 5000.0'" (a word such as CIVIL may follow the equals sign)
DATUM_EQ_RE = re.compile(r"\[?\s*100" + _FT + r"?\s*=\s*(?:[A-Z][A-Z.]*\s+){0,2}(?P<v>\d{3,5}(?:\.\d+)?)\s*" + _FT + r"?\s*\]?", re.I)
DATUM_NOTE_RE = re.compile(
    r"DATUM\s+ELEV(?:ATION)?\s*(?:OF\s*)?100" + _FT + r"?\s*-?\s*0?" + _IN + r"?\s*(?:=|EQUALS)\s*"
    r"(?P<what>[A-Z][A-Z0-9 ./&\-\n]{3,90}?)(?:\.|;|$)", re.I)   # the note wraps lines
LEVEL_RE = re.compile(
    r"(?P<label>(?:T\.?O\.?\s*)?(?:MAIN|LOWER|UPPER|GARAGE|BASEMENT|ENTRY|FINISH(?:ED)?\s+FLOOR|FFE)"
    r"[A-Z0-9 ./]{0,30}?)\s*:\s*(?P<v>\d{4,5}(?:\.\d+)?)\s*" + _FT + r"?", re.I)


def find_datum_notes(pages: list) -> list:
    """pages: [(page_index, text), ...] -> [{page, kind, label, value_ft, text}]
    kind: "datum" (100' = N, the statement), "level" (a named finished
    floor with a civil elevation), "note" (what 100'-0" is, no number).
    Deduplicated on (kind, label, value); order: datum, level, note, then
    page order."""
    out, seen = [], set()

    def add(page, kind, label, value, text):
        key = (kind, (label or "").upper().strip(), value)
        if key in seen:
            return
        seen.add(key)
        out.append({"page": int(page), "kind": kind, "label": (label or "").strip() or None,
                    "value_ft": value, "text": " ".join(str(text).split())[:120]})

    for page, text in pages:
        t = str(text or "")
        for m in DATUM_EQ_RE.finditer(t):
            v = float(m.group("v"))
            if v >= 100:
                add(page, "datum", "100'-0\" equals", v, m.group(0))
        for m in DATUM_NOTE_RE.finditer(t):
            add(page, "note", " ".join(m.group("what").split()), None, m.group(0))
        for m in LEVEL_RE.finditer(t):
            v = float(m.group("v"))
            if v >= 100:
                add(page, "level", m.group("label"), v, m.group(0))
    rank = {"datum": 0, "level": 1, "note": 2}
    out.sort(key=lambda n: (rank.get(n["kind"], 9), n["page"]))
    return out


def civil_of(arch_ft, datum_ft):
    """An architectural elevation (86'-0" = 86.0) on a datum where 100'-0"
    equals `datum_ft` -> the civil elevation; None when either is missing."""
    try:
        a, d = float(arch_ft), float(datum_ft)
    except (TypeError, ValueError):
        return None
    return round(d + (a - 100.0), 3)
