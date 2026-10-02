"""Turn printed text (text layer or OCR) into elevation evidence.

A Label is one printed thing: "570.3", "558.5 BW", "92.46 FG", "575" in a
contour gap, "FFE=5993.00", "ELEV 100.00 = 8800.00". Parsing is deliberately
literal: value as printed, tag as printed. Datum / abbreviation resolution is
a separate, visible step (resolve_values) so the UI can show both numbers.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# Tags that name a point elevation. side: which surface the number belongs to.
#   existing  -> ground today (EG, ME, EX, EOA, TBC-existing...)
#   proposed  -> finished design grade
#   wall      -> built wall grades (TW/BW): proposed surface on each side of a wall
#   floor     -> finished floor (not ground; used through a rule)
#   utility   -> rim/invert (never a surface point)
TAGS: dict[str, tuple[str, str]] = {
    "TW": ("wall", "top of wall"), "BW": ("wall", "bottom of wall"), "TOW": ("wall", "top of wall"),
    "BOW": ("wall", "bottom of wall"), "T/W": ("wall", "top of wall"), "B/W": ("wall", "bottom of wall"),
    "EW": ("wall", "end of wall"),
    "FG": ("proposed", "finished grade"), "FS": ("proposed", "finished surface"), "FL": ("proposed", "flow line"),
    "TC": ("proposed", "top of curb"), "BC": ("proposed", "bottom of curb"), "TBC": ("proposed", "top back of curb"),
    "TA": ("proposed", "top of asphalt"), "TP": ("proposed", "top of pavement"), "GB": ("proposed", "grade break"),
    "HP": ("proposed", "high point"), "LP": ("proposed", "low point"), "PAD": ("proposed", "pad"),
    "SG": ("proposed", "subgrade"), "TOS": ("proposed", "top of slab"), "BOS": ("proposed", "bottom of slab"),
    "TG": ("proposed", "top of grate"), "EOP": ("proposed", "edge of pavement"), "EP": ("proposed", "edge of pavement"),
    "EOA": ("proposed", "edge of asphalt"), "TOC": ("proposed", "top of concrete"), "BOC": ("proposed", "back of curb"),
    "RIM": ("proposed", "rim"), "GR": ("proposed", "grate"), "GRATE": ("proposed", "grate"),
    "EG": ("existing", "existing grade"), "EX": ("existing", "existing"), "EXIST": ("existing", "existing"),
    "ME": ("existing", "match existing"), "MEG": ("existing", "match existing grade"), "EOA-EX": ("existing", "existing edge of asphalt"),
    "FFE": ("floor", "finished floor"), "FF": ("floor", "finished floor"), "FFL": ("floor", "finished floor"),
    "INV": ("utility", "invert"), "IE": ("utility", "invert"), "FL-INV": ("utility", "invert"),
    "BOF": ("footing", "bottom of footing"), "TOF": ("footing", "top of footing"), "T/FTG": ("footing", "top of footing"),
    "B/FTG": ("footing", "bottom of footing"), "T/SLAB": ("floor", "top of slab"),
}
_TAG_ALT = "|".join(sorted((re.escape(t) for t in TAGS), key=len, reverse=True))
_NUM = r"(\d{1,5}(?:\.\d{1,3})?)"
RE_PERCENT = re.compile(r"\d\s*%")
RE_STATION = re.compile(r"\b\d{1,4}\+\d{2}(?:\.\d+)?\b")
# "100.00 = 8800.00", "[100'=5000']", "ARCH. 100' = CIVIL 5000.0'", "NOTE: ARCHITECTURAL 100' = CIVIL 7923.0'":
# a word such as CIVIL may sit between the equals sign and the real elevation
RE_DATUM = re.compile(
    r"(\d{2,3}(?:\.\d{1,2})?)\s*'?\s*(?:-\s*0\s*\"?)?\s*=\s*(?:[A-Z][A-Z.]*\s*){0,2}(\d{3,5}(?:[.,]\d{1,3})?)\s*'?|"
    r"(\d{3,5}(?:[.,]\d{1,3})?)\s*'?\s*=\s*(?:[A-Z][A-Z.]*\s*){0,2}(\d{2,3}(?:\.\d{1,2})?)\s*'?(?:-\s*0\s*\"?)?", re.I)
# the sheet says its short spot grades are truncated CIVIL elevations ("7605.25 -> 05.25", "ALL SPOT
# ELEVATIONS ... ARE CIVIL ELEVATIONS AND DO NOT CORRELATE TO ARCH. ELEVATIONS"): an architectural
# datum note on such a sheet is for the structural set, never for these numbers
RE_TRUNCATED_NOTE = re.compile(r"\d{4}(?:\.\d+)?\s*-?>\s*\d{2}(?:\.\d+)?|SPOT ELEVATIONS?[^.]{0,60}CIVIL ELEVATIONS", re.I)
RE_TAG_NUM = re.compile(r"^(?:(%s)\.?\s*[:=]?\s*)?%s(?:\s*[±+-]?\s*)?(?:\s*(%s)\.?)?$" % (_TAG_ALT, _NUM, _TAG_ALT), re.I)
RE_FTIN = re.compile(r"^(?:(%s)\s*[:=]?\s*)?(\d{1,4})'\s*-?\s*(\d{1,2})(?:\s*(\d)/(\d))?\"?$" % _TAG_ALT, re.I)
RE_DOTTED_TAG = re.compile(r"\b([A-Z])\.([A-Z])\.([A-Z])?\.?", re.I)
RE_SCALE = re.compile(r"(\d+(?:/\d+)?)\s*(?:\"|''|”|″)\s*=\s*(\d+)\s*['’′]|1\s*(?:inch|in\.?)\s*=\s*(\d+)\s*(?:ft|feet|['’′])", re.I)
RE_SCALE_RATIO = re.compile(r"SCALE\s*[:=]?\s*1\s*:\s*(\d+)", re.I)


@dataclass
class Label:
    id: int
    page: int
    text: str
    bbox: tuple
    angle: float = 0.0          # degrees, text baseline direction
    source: str = "text"        # text | ocr | gap
    conf: float = 100.0
    gray: float = 0.0           # ink darkness 0 = black
    kind: str = "other"         # contour | spot | wall | floor | utility | footing | percent | station | datum | scale | other
    value: float | None = None  # as printed
    tag: str | None = None
    side: str | None = None     # existing | proposed | wall | floor | utility | footing
    gap_id: int | None = None
    chain_ids: list = field(default_factory=list)
    # resolution (filled by resolve_values)
    z: float | None = None      # elevation in the sheet's real datum, feet
    z_note: str = ""            # how z was derived from value ("+5900 (abbreviated)" / "datum 100=8800")
    used: bool = True           # operator can switch a label off
    datum: tuple | None = None  # (project, real) for datum labels
    datum_arch: bool = False    # the note names the ARCHITECTURAL datum (100' = civil N): for structural callouts
    scale: float | None = None  # feet per inch for scale labels
    len_along: float = 0.0      # extent along the baseline (pt)
    height: float = 0.0         # extent across the baseline (pt)
    station: tuple | None = None  # (A, B) for "A+B" text: a station, or "datum base + offset" when a datum says so
    pinned: bool = False        # the operator attached this label to a line (chain_ids set before matching)
    near: int | None = None     # an unattached number next to a contour line: that line's chain id
    frame: str | None = None    # pen of the small strokes framing the text (an oval / box): "gray|w0.9"; None = plain
    turned: str | None = None   # the number the word reads from the OTHER side (a word read from its strokes)

    @property
    def center(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        return (0.5 * (x0 + x1), 0.5 * (y0 + y1))

    def to_json(self) -> dict:
        return {
            "id": self.id, "page": self.page, "text": self.text,
            "bbox": [round(v, 1) for v in self.bbox], "angle": round(self.angle, 1), "source": self.source,
            "conf": round(self.conf, 0), "gray": round(self.gray, 2), "kind": self.kind, "value": self.value,
            "tag": self.tag, "side": self.side, "gap_id": self.gap_id, "chain_ids": self.chain_ids,
            "z": None if self.z is None else round(self.z, 2), "z_note": self.z_note, "used": self.used,
            "datum": self.datum, "datum_arch": self.datum_arch, "scale": self.scale, "len_along": round(self.len_along, 1), "height": round(self.height, 1),
            "pinned": self.pinned, "near": self.near, "frame": self.frame, "turned": getattr(self, "turned", None),
        }


def clean_read(text: str) -> str:
    """OCR of a tight crop often catches a piece of the line next to the label
    ("5992 -", "-565", "568."). Strip such fragments off numeric reads."""
    t = text.strip()
    # a height printed IN its line: the dashes either side of it are read as a long dash, a
    # tilde, a bracket ("652—", "—663", "~668", "(655": subdivision D, seven of the screened
    # existing heights of one sheet were words, not numbers). Only when what is left is a plain
    # number: no other text loses a character here.
    # ... or a number with a tag this reader knows (a spot grade on its leader: the arrow head is
    # read as a tilde and an equals sign; the hotel site C3, "~ = 92.46 FG" was no spot grade)
    t3 = re.sub(r"^[\s\-_.,|:;'~\\/(—–=]+", "", t)
    t3 = re.sub(r"[\s\-_.,|:;'~\\/)—–=]+$", "", t3)
    if t3 != t and re.fullmatch(r"\d{2,5}(\.\d{1,3})?", t3):
        return t3
    if t3 != t and re.match(r"^[0-9A-Za-z]", t3):
        m = RE_TAG_NUM.match(normalise(t3).replace("ELEV.", "").replace("ELEV", "").strip())
        if m and (m.group(1) or m.group(3)):
            return t3
    t2 = re.sub(r"^[\s\-_.,|:;']+", "", t)
    t2 = re.sub(r"[\s\-_.,|:;']+$", "", t2)
    t2 = re.sub(r"\s+-\s*$", "", t2)
    return t2 if t2 else t


def normalise(text: str) -> str:
    t = text.strip().upper()
    t = t.replace("’", "'").replace("”", '"').replace("“", '"').replace("−", "-")
    t = re.sub(r"\s+", " ", t)
    # dotted tags: G.B. -> GB, T.B.C. -> TBC, H.P. -> HP
    t = re.sub(r"\b([A-Z])\.\s?([A-Z])\.\s?([A-Z])\.?", r"\1\2\3", t)
    t = re.sub(r"\b([A-Z])\.\s?([A-Z])\.?", r"\1\2", t)
    return t


def parse_text(text: str) -> dict:
    """Classify one printed line. Returns dict(kind, value, tag, side, datum, scale)."""
    t = normalise(text)
    res = {"kind": "other", "value": None, "tag": None, "side": None, "datum": None, "scale": None, "station": None,
           "datum_arch": False, "truncated_note": False}
    if not t:
        return res
    if RE_TRUNCATED_NOTE.search(t):
        res["truncated_note"] = True
    m = RE_DATUM.search(t)
    if m and "=" in t and not RE_SCALE.search(t):
        if m.group(1):
            proj, real = float(m.group(1)), float(m.group(2).replace(",", ""))
        else:
            real, proj = float(m.group(3).replace(",", "")), float(m.group(4))
        if real > proj + 100:
            res.update(kind="datum", datum=(proj, real), value=real, datum_arch=("ARCH" in t))
            return res
    m = RE_SCALE.search(t)
    if m:
        if m.group(1):
            inch = m.group(1)
            inch_v = float(inch.split("/")[0]) / float(inch.split("/")[1]) if "/" in inch else float(inch)
            res.update(kind="scale", scale=float(m.group(2)) / inch_v if inch_v else None)
        else:
            res.update(kind="scale", scale=float(m.group(3)))
        return res
    m = RE_SCALE_RATIO.search(t)
    if m:
        res.update(kind="scale", scale=float(m.group(1)) / 12.0)
        return res
    if RE_PERCENT.search(t):
        res["kind"] = "percent"
        return res
    if RE_STATION.search(t):
        m = re.search(r"(\d{1,4})\+(\d{1,3}(?:\.\d+)?)", t)
        res["kind"] = "station"
        if m:
            res["station"] = (float(m.group(1)), float(m.group(2)))
        return res
    m = RE_FTIN.match(t)
    if m:
        tag = (m.group(1) or "").upper() or None
        v = float(m.group(2)) + float(m.group(3)) / 12.0
        if m.group(4) and m.group(5) and float(m.group(5)):
            v += float(m.group(4)) / float(m.group(5)) / 12.0
        side = TAGS[tag][0] if tag in TAGS else None
        res.update(kind=_kind_from_side(side, tagged=bool(tag), decimal=True), value=v, tag=tag, side=side)
        return res
    # "FFE=5993.00" / "TW=545.5" / "545.5 TW" / "570.3"
    t2 = t.replace("ELEV.", "").replace("ELEV", "").replace("EL.", "").replace("EL ", "").strip()
    m = RE_TAG_NUM.match(t2)
    if m:
        tag = (m.group(1) or m.group(3) or "").upper() or None
        v = float(m.group(2))
        decimal = "." in m.group(2)
        side = TAGS[tag][0] if tag in TAGS else None
        res.update(kind=_kind_from_side(side, tagged=bool(tag), decimal=decimal), value=v, tag=tag, side=side)
        return res
    return res


def _kind_from_side(side: str | None, tagged: bool, decimal: bool) -> str:
    if side in ("wall", "floor", "utility", "footing"):
        return side
    if tagged:
        return "spot"
    return "spot" if decimal else "number"     # bare integer: contour label or integer spot, decided later


def make_label(id: int, page: int, text: str, bbox: tuple, angle: float = 0.0, source: str = "text",
               conf: float = 100.0, gray: float = 0.0, len_along: float | None = None,
               height: float | None = None) -> Label:
    raw = text.strip()
    text = clean_read(raw) if source in ("ocr", "glyph", "gap") else raw
    p = parse_text(text)
    x0, y0, x1, y1 = (float(v) for v in bbox)
    if len_along is None or height is None:
        horiz = abs(((angle + 90) % 180) - 90) < 45
        len_along = (x1 - x0) if horiz else (y1 - y0)
        height = (y1 - y0) if horiz else (x1 - x0)
    lab = Label(id=id, page=page, text=text, bbox=(x0, y0, x1, y1), angle=angle,
                source=source, conf=conf, gray=gray, kind=p["kind"], value=p["value"], tag=p["tag"],
                side=p["side"], datum=p["datum"], datum_arch=bool(p.get("datum_arch")), scale=p["scale"],
                len_along=float(len_along), height=float(height),
                station=p.get("station"))
    return lab


_TURNED = str.maketrans("01689", "01986")


def turned(text: str) -> str | None:
    """The same digits read from the other side: "999" is "666" upside down. None when a digit
    has no meaning turned (2, 3, 4, 5, 7) or the turned number would start with a zero."""
    t = text.strip()
    if not re.fullmatch(r"[01689]{3,5}", t):
        return None
    u = t[::-1].translate(_TURNED)
    return None if u.startswith("0") else u


def upright_heights(labels: list[Label]) -> list:
    """A WHOLE NUMBER READ UPSIDE DOWN. A contour height is printed along its line at any
    angle, and where the text is exploded into strokes nothing says which way up it reads: 666
    and 999 are the same strokes. The sheet's own heights say it: a number outside their range
    whose turned reading is inside it was read from the wrong side. Subdivision D (C3.0): a
    screened "666" on an existing contour was read "999" and set aside as "outside the sheet's
    elevation range". The turned reading is what the OCR read from the other side where it
    kept one (`Label.turned`: the hotel site C3, a "5990" printed down the sheet read "0665"),
    else the same digits turned (0, 1, 6, 8, 9). Only whole numbers of three to five digits,
    never a number from the text layer (its direction is given), never one with decimals
    ("91.66" and "99.16" are both heights of one site: the range cannot tell them apart).
    Returns the labels it turned."""
    vals = [l.value for l in labels if l.value is not None and l.value >= 100 and l.used
            and l.kind in ("number", "contour", "spot") and float(l.value).is_integer()]
    rng = plausible_range(vals)
    if rng is None:
        return []
    # 25 ft either side of the heights read (the margin resolve_values gives a printed number):
    # the lowest height of a sheet is as likely to be the one read from the wrong side as any
    # (the hotel site C3: a 5981, three feet under the lowest height read, stayed "6865")
    lo, hi = rng[0] - 25.0, rng[1] + 25.0
    out = []
    for l in labels:
        if l.source == "text" or l.value is None or l.tag or not l.used or l.kind not in ("number", "contour", "spot"):
            continue
        if not float(l.value).is_integer():
            continue
        u = str(getattr(l, "turned", None) or "").strip()
        if not re.fullmatch(r"\d{3,5}", u) or u.startswith("0"):
            u = turned(l.text)
        if u is None or lo <= l.value <= hi or not (lo <= float(u) <= hi):
            continue
        was = l.text.strip()
        l.text, l.value, l.turned = u, float(u), was
        l.angle = (l.angle + 180.0) % 360.0
        l.z_note = f"read {was} from the other side: {u} is in the sheet's range"
        out.append(l)
    return out


# --------------------------------------------------------------------------
def plausible_range(values: list[float]) -> tuple[float, float] | None:
    """The dominant cluster of printed elevations on a sheet (a site spans tens of
    feet, not thousands). Returns (lo, hi) or None."""
    vs = sorted(v for v in values if v is not None and v >= 10)
    if len(vs) < 3:
        return None
    best = None
    i = 0
    for i in range(len(vs)):
        j = i
        while j + 1 < len(vs) and vs[j + 1] - vs[i] <= 150:
            j += 1
        if best is None or (j - i) > best[0]:
            best = (j - i, vs[i], vs[j])
    if best is None or best[0] + 1 < 3:
        return None
    return (best[1], best[2])


def resolve_values(labels: list[Label], datum: tuple | None = None, contour_range: tuple | None = None,
                   abbrev_base: float | None = None, datum_off: bool = False, base_is_typed: bool = False) -> dict:
    """Fill Label.z from Label.value.

    datum          (project, real): a sheet note like "100.00 = 8800.00"
    contour_range  (lo, hi) of contour-derived elevations if known; used to resolve
                   abbreviated spots ("91.70" on a 5980-5996 site -> 5991.70)
    Returns a summary dict for the UI.
    """
    vals = [l.value for l in labels if l.kind in ("contour", "spot", "wall", "floor", "number") and l.value is not None]
    rng = contour_range or plausible_range(vals)
    summary = {"range": rng, "datum": datum, "abbreviated": 0, "datum_applied": 0, "rejected": 0,
               "abbrev_base": abbrev_base, "datum_off": bool(datum_off)}
    # abbrev_base: the operator's own base for short elevations ("48.07" + 7700), which needs no
    # range and outranks the datum window; datum_off: the sheet says its short numbers are
    # truncated civil elevations, so the datum note (architectural) is not applied to them
    for l in labels:
        l.z = None
        # the note a label carries from the attachment step ("no existing line runs under it",
        # "printed to the hundredth") is what the operator needs to see; resolution adds to it
        # rather than wiping it (before 2026-09-26 every such reason vanished here)
        if l.kind in ("percent", "station", "scale", "datum", "other"):
            l.z_note = ""
        # "100+22" on a sheet whose datum note says 100 = 8800: the project base plus an offset
        if l.kind == "station" and l.station and datum is not None and abs(l.station[0] - datum[0]) < 1e-6:
            l.kind = "spot"
            l.value = l.station[0] + l.station[1]
            l.z = datum[1] + l.station[1]
            l.z_note = f"{datum[0]:g}+{l.station[1]:g} with datum {datum[0]:g} = {datum[1]:g}"
            summary["datum_applied"] += 1
            continue
        if l.value is None or l.kind in ("percent", "station", "scale", "datum", "other"):
            continue
        v = l.value
        printed_decimal_ = "." in str(l.text) or bool(l.tag)
        # a bare small integer that attached to no line (kind "number": a scale bar's 0 / 10 / 20, a
        # lot or detail number) is never a spot grade, whatever range the other labels suggest
        if l.kind == "number" and v < 100 and not printed_decimal_:
            l.z_note = "a small number with no decimals: not an elevation"
            summary["rejected"] += 1
            continue
        if abbrev_base is not None and v < 100 and printed_decimal_ and l.kind in ("spot", "wall", "floor", "number", "contour"):
            l.z = float(abbrev_base) + v
            l.z_note = f"+{float(abbrev_base):g} (short number, base {'you set' if base_is_typed else 'from the sheet'})"
            summary["abbreviated"] += 1
            continue
        if datum is not None and not datum_off:
            proj, real = datum
            if abs(v - proj) <= 60:
                l.z = v + (real - proj)
                l.z_note = f"datum {proj:g} = {real:g}"
                summary["datum_applied"] += 1
                continue
        if rng is not None:
            lo, hi = rng
            if lo - 25 <= v <= hi + 25:
                l.z = v
                continue
            base = math.floor((lo + hi) / 2 / 100) * 100
            printed_decimal = "." in str(l.text) or bool(l.tag)
            # a short number printed to the THOUSANDTH is no spot grade (they are printed to the
            # hundredth): subdivision D prints "CALLED 55.967 AC." on the neighbouring tract, twice;
            # each became the existing ground at 655.97 ft, 10 ft under the contours beside it
            if v < 100 and printed_decimal and not l.tag and re.search(r"\.\d{3}", str(l.text)):
                l.z_note = "a short number printed to the thousandth: not a spot grade"
                summary["rejected"] += 1
                continue
            if v < 100 and printed_decimal and (l.source == "text" or l.conf >= 60):
                cand = base + v
                if lo - 25 <= cand <= hi + 25:
                    l.z = cand
                    l.z_note = f"+{base:g} (abbreviated)"
                    summary["abbreviated"] += 1
                    continue
                cand2 = base - 100 + v
                if lo - 25 <= cand2 <= hi + 25:
                    l.z = cand2
                    l.z_note = f"+{base - 100:g} (abbreviated)"
                    summary["abbreviated"] += 1
                    continue
            l.z_note = "outside the sheet's elevation range"
            summary["rejected"] += 1
            continue
        # no range to judge by: a bare small integer with no tag (a scale bar's 0 / 10 / 20, a lot
        # number, a detail number) is not an elevation (Res-A C3.1: the graphic scale's numerals had
        # become spot grades at 0-40 ft)
        if v < 100 and not printed_decimal_ and not l.tag:
            l.z_note = "a small number with no decimals: not an elevation"
            summary["rejected"] += 1
            continue
        l.z = v
    return summary


def find_label_frames(labels: list, strokes: list, glyph_ids=()) -> int:
    """The pen of the small vector strokes that FRAME a printed number: the oval round a proposed
    height, the box round a finished-floor note. Sheets distinguish their surfaces this way (subdivision
    A's legend: proposed contour = bold black oval, existing = plain gray text or a gray
    oval), and a label sitting where two surfaces' lines cross belongs to the surface its frame
    says. A framing stroke lies in the text's band (within 5 pt across, 8 pt beyond the ends) and
    is either a long side (>= 50 % of the text length along the baseline) or an end arc (centred
    beyond the text's end); the digits of an exploded label are neither. Returns the count framed."""
    import math
    import numpy as np
    from collections import defaultdict
    from scipy.spatial import cKDTree
    small = []
    # (glyph strokes are NOT excluded: the arcs of an oval cluster like glyphs and were lost with
    # them - subdivision A went from 52 framed labels to 27; an exploded label's own digits fail
    # the side / arc tests below)
    for st in strokes:
        pts = st.pts
        if len(pts) < 2 or st.width is None or st.width <= 0:
            continue
        ext = max(float(pts[:, 0].max() - pts[:, 0].min()), float(pts[:, 1].max() - pts[:, 1].min()))
        if ext > 36.0 or ext < 1.0:
            continue
        g = 0.299 * st.color[0] + 0.587 * st.color[1] + 0.114 * st.color[2]
        if g > 0.95:
            continue                      # white masks
        small.append((st, g))
    if not small:
        return 0
    C = np.array([[(st.pts[:, 0].min() + st.pts[:, 0].max()) / 2, (st.pts[:, 1].min() + st.pts[:, 1].max()) / 2] for st, _ in small])
    tree = cKDTree(C)
    n = 0
    for lab in labels:
        if lab.value is None:
            continue
        a = math.radians(lab.angle)
        u = np.array([math.cos(a), math.sin(a)])
        nv = np.array([-u[1], u[0]])
        c = np.array(lab.center)
        hl = max(lab.len_along, 4.0) / 2
        hh = max(lab.height, 3.0) / 2
        pens = defaultdict(list)
        for k in tree.query_ball_point(c, hl + 12.0):
            st, g = small[k]
            d = st.pts - c
            t = d @ u
            r = d @ nv
            if float(np.abs(r).max()) > hh + 5.0 or float(np.abs(t).max()) > hl + 8.0:
                continue
            along = float(t.max() - t.min())
            tc = float((t.max() + t.min()) / 2)
            # the oval is smaller than the text extent the text layer reports for rotated text
            # (axis-aligned character boxes): a side is >= half the text, an arc sits beyond 60 %
            if along >= 0.5 * 2 * hl or abs(tc) >= 0.6 * hl:
                pens[f"{g:.2f}|w{st.width:.1f}"].append((float(t.min()), float(t.max())))
        best = None
        for pen, spans in pens.items():
            if len(spans) < 2:
                continue
            lo = min(s0 for s0, _ in spans)
            hi = max(s1 for _, s1 in spans)
            if lo <= -hl + 3.0 and hi >= hl - 3.0 and (best is None or len(spans) > best[0]):
                best = (len(spans), pen)
        lab.frame = best[1] if best else None
        n += 1 if best else 0
    return n
