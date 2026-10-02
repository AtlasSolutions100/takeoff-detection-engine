"""Pipe size on a run: where a line family's size comes from, per family.

One excavator prices storm by size ("storm LF per size; a colour change is a
profile change") and another sets the trench width by the pipe size, so a
run needs its size on it. Three places the sheet prints it, in the order
they are trusted:
  1. the family's NAME (its legend label or CAD layer): SE-P-UT-WATER-10''
     - `size_token`, the same rule as the bid view's bidSizeToken;
  2. inline codes on the line itself: 8"SS, 12"SD, E8"W - the codes
     chain.py already attaches to a chain (inline_labels);
  3. leader callouts pointing at the line: '18" RCP FL=586.30' - the
     Sheet callouts pairing (text + target chain).
`family_sizes` weighs 2 and 3 by run length and says, per family, whether
it is NAMED (size in the name), SINGLE (nameless, one size on its runs -
`suggest` is that size, the panel offers to name the family with it),
MIXED (two sizes on its runs: split the family first, never guess) or
none. A named family whose runs print another size is flagged
(`agrees: false`); a family with no evidence at all is not listed.
Measured 2026-09-16: a residential C3.0 prints every size in the family
names and none on the runs; a substation C-03 prints them on leaders ('18" RCP',
'30" RCP') off OCR - hence the lookbehind that refuses '218"' and the
2" of a 7'-2" dimension.
"""
from __future__ import annotations

import re
from collections import defaultdict

# two lookbehinds: not the tail of a longer number (218"), not the inches of
# a feet-and-inches dimension (7'-2") - but WATER-10'' keeps its dash
SIZE_IN_NAME = re.compile(r"(?<![\d.])(?<!['’′]-)(\d{1,2})\s*(?:\"|”|''|[- ]?IN(?:CH)?\b)", re.I)
INLINE = re.compile(r'^(?:EX?-?)?(\d{1,2})["″”]?(?:W|SS|SD|FM|G|WL|SL)$')
LEADER = re.compile(r"(?<![\d.])(?<!['’′]-)(\d{1,2})\s*(?:\"|”|''|\bIN\b|INCH)")
# a leader's tip lands on the nearest line, and on substation C-03 that was a
# contour three times out of five: a contour is never a pipe, whatever
# points at it
NOT_A_PIPE = re.compile(r"CONTOUR|TOPO|GRADE|PROPERTY|SETBACK|EASEMENT|FENCE|CURB|WALL", re.I)
# name a family from its runs only when enough of it is sized: a fifth of
# its length or two separate runs, else the panel says "too little"
SUGGEST_SHARE = 0.2


def size_token(text) -> str | None:
    """'SE-P-UT-WATER-10'\\'' -> '10"'; '18" CMP CULVERT' -> '18"'; the 2" of
    7'-2" is not a size; no unit, no size."""
    m = SIZE_IN_NAME.search(str("" if text is None else text))
    return f'{int(m.group(1))}"' if m else None


def family_sizes(chains, styles, labels=None, callouts=None, ft_per_pt=1.0) -> list:
    """[{style_id, label, name_size, verdict, suggest, by_size: {size: {ft,
    chains, inline, leader}}, no_size_ft, total_ft, agrees}] for every
    family with a size anywhere, longest first."""
    labels = labels or {}
    by_style: dict = defaultdict(list)
    for ch in chains or []:
        if ch.get("class") == "annotation":
            continue
        by_style[ch.get("style_id")].append(ch)
    lead_by_chain: dict = defaultdict(list)
    for c in callouts or []:
        cid = c.get("target_chain_id")
        if cid is None:
            continue
        m = LEADER.search(str(c.get("text") or ""))
        if m:
            lead_by_chain[int(cid)].append(f'{int(m.group(1))}"')
    out = []
    for st in styles or []:
        sid = st.get("style_id")
        chs = by_style.get(sid) or []
        if not chs:
            continue
        name = str(labels.get(sid) or labels.get(str(sid)) or st.get("label") or st.get("suggested_label") or "")
        name_size = size_token(name) or size_token(st.get("layer") or "")
        not_pipe = bool(NOT_A_PIPE.search(name + " " + str(st.get("layer") or "")))
        by_size: dict = {}
        no_size = 0.0
        total = 0.0
        for ch in chs:
            ft = float(ch.get("length_pt") or 0.0) * ft_per_pt
            total += ft
            found: dict = {}
            for t in ch.get("inline_labels") or []:
                m = INLINE.match(str(t).strip().upper())
                if m:
                    found.setdefault(f'{int(m.group(1))}"', {"inline": 0, "leader": 0})["inline"] += 1
            if not not_pipe:
                for s in lead_by_chain.get(int(ch.get("chain_id", -1)), []):
                    found.setdefault(s, {"inline": 0, "leader": 0})["leader"] += 1
            if not found:
                no_size += ft
                continue
            for s, ev in found.items():
                rec = by_size.setdefault(s, {"ft": 0.0, "chains": 0, "inline": 0, "leader": 0})
                rec["ft"] += ft / len(found)
                rec["chains"] += 1
                rec["inline"] += ev["inline"]
                rec["leader"] += ev["leader"]
        if not by_size and not name_size:
            continue
        sizes = sorted(by_size, key=lambda s: -by_size[s]["ft"])
        if name_size:
            verdict = "named"
        elif len(sizes) == 1:
            verdict = "single"
        elif sizes:
            verdict = "mixed"
        else:
            verdict = "none"
        agrees = None
        if name_size and sizes:
            agrees = sizes == [name_size]
        suggest = None
        if verdict == "single":
            top = by_size[sizes[0]]
            if top["chains"] >= 2 or (total > 0 and top["ft"] >= SUGGEST_SHARE * total):
                suggest = sizes[0]
        out.append({"style_id": sid, "label": name, "name_size": name_size, "verdict": verdict,
                    "suggest": suggest,
                    "by_size": {s: {"ft": round(by_size[s]["ft"], 1), "chains": by_size[s]["chains"],
                                    "inline": by_size[s]["inline"], "leader": by_size[s]["leader"]} for s in sizes},
                    "no_size_ft": round(no_size, 1), "total_ft": round(total, 1), "agrees": agrees})
    out.sort(key=lambda r: -r["total_ft"])
    return out
