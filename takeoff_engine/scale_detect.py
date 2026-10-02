"""Automatic scale detection from plan sheets.

Reads scale notations like:
    SCALE: 1" = 20'        (civil)
    Scale: 1/4" - 1'-0"    (architectural; separators vary: = - :)
    1" = 50'  /  3/32" = 1'-0"
    1 inch = 10 ft.  /  1 INCH = 20 FEET   (spelled-out units)

Sources, in order:
  1. the PDF text layer (as get_text() emits it, then re-assembled from
     row-sorted words - AutoCAD scale-bar blocks store the ratio's number
     as a separate attribute, so get_text() prints "1 inch =      ft." and
     drops the "10" among the tick labels),
  2. the drawn GRAPHIC SCALE bar,
  3. OCR of the rendered page (CAD exports often explode the scale note
     into strokes - C4.0's "SCALE: 1\" = 20'" has no text layer at all).

Returns ALL candidates with context, plus a chosen best (keyword-adjacent
beats bare matches). "NOT TO SCALE"/"N.T.S." presence is reported too -
sheets routinely mix a scaled plan with NTS details, so the reviewer sees
what was found rather than silently trusting one number.

`ScaleResult.source` distinguishes HOW a page came to have no reading:
"" means the sheet printed nothing we recognise; "ocr-unavailable" means
the OCR engine itself could not run (Tesseract not installed / not on
PATH) and the page was therefore never actually checked; "ocr-error"
means OCR was attempted and failed on this page. Two independent audits
were misled by these three cases collapsing into one silent None - one
ran 164 pages degraded without noticing, the other nearly signed off on
"no scale printed" when the truth was "no OCR on PATH".
"""

import logging
import re
import warnings
from dataclasses import dataclass, field

import numpy as np
import pymupdf

_log = logging.getLogger(__name__)
# warn ONCE per process when OCR itself cannot run (see detect_scale) -
# after that each affected page still logs, but does not re-warn.
_OCR_UNAVAILABLE_WARNED = False

# Unit marks. Tick marks (" ') are the common form; sheets also spell the
# units out ("1 inch = 10 ft.", "1 INCH = 20 FEET"). A spelled-out unit must
# end the word (no letter after it) so "in" is not the start of "inside"
# and "ft" not of "ftg"; the tick forms keep matching whatever follows.
_IN = r"""(?:["”″]|(?:inch(?:es)?|in)\.?(?![a-z]))"""
_FT = r"""(?:['’′]|(?:feet|ft)\.?(?![a-z]))"""

# The paper term of a ratio. Architectural scales coarser than one inch to
# the foot are written as MIXED numbers - 1-1/2" = 1'-0", 1 1/2" = 1'-0" -
# and a bare `\d+(?:/\d+)?` cannot hold one: it matched only the "1/2" and
# reported 2.0 ft/in where the truth is 0.667, a 3x error on length and 9x
# on area. Found on three sheets independently by the 201-sheet audit
# (Res-A structural S6-0.0, Res-B structural S6-0.0, Res-A landscaping L801),
# and confirmed against drawn geometry: L801 detail 2's printed 6" dimension
# spans exactly 54.000 pt, i.e. 0.75 paper inches, which is 0.667 ft/in.
# Alternation order matters - mixed, then fraction, then integer - or `\d+`
# would swallow the "1" of "1/2" and stop.
#
# The mixed form is deliberately restricted to ONE AND A HALF. A general
# `\d+[-\s]\d+/\d+` regressed two pages in the 208-page sweep, because the
# space-separated form is ambiguous: `A5.20 1 1/2" = 1'-0"` is a real scale,
# but `XYZ - GR 1 3/4" = 1'-0"` is the 3/4" scale with a detail reference in
# front of it, and `2 3/4" 1 3/4" 1/2"` is a row of dimensions. Read greedily
# those became 1.75 and 2.75 paper inches.
#
# CONVENTION, and the reason the restriction is safe: an architect's scale
# rule carries fixed graduations - 3/32, 1/8, 3/16, 1/4, 3/8, 1/2, 3/4, 1,
# 1-1/2 and 3 inches to the foot - and 1-1/2 is the only one of them that is
# a mixed number. Source: every scale caption in the Res-A/B and Res-C
# sets is drawn from that set; 1-3/4 and 2-3/4 appear in the corpus only as
# dimensions. Labelled an INFERENCE for other drawing standards. If a real
# mixed scale outside 1-1/2 ever turns up, widen this with its sheet as
# evidence rather than by generalising the pattern.
#
# The mixed number's joiner is SPACE, TAB or HYPHEN - deliberately not `\s`,
# which matches a NEWLINE. get_text() separates lines with newlines, so with
# `\s` a whole number ending one line welds onto a fraction starting the
# next: Res-A structural engine page index 25 = printed sheet S5.1 prints
# `PROJECT #24004520.01` in the title block directly above `1/2" = 1'-0"`,
# and the weld read `1\n1/2" = 1'-0"` as 1-1/2" = 0.6667 ft/in - a scale the
# sheet does not print, and since 36f3dd5 a clickable picker option that
# divides every length on the page by three. A caption's own paper term
# never wraps (it is one token in one title bar), so nothing real is lost
# by refusing the line break; the 208-page sweep confirms exactly one
# candidate-set change from this line, that page, that value.
_NUM = r"""(?:1[ \t-]1/2|\d+/\d+|\d+)"""

# The ground term's integer. A bare `\d+` stops at a thousands separator,
# so "1:8,010" read feet=8 - and once the ratio form below is understood at
# all, that is a 1,001x error on every length. The thousands alternative
# must come FIRST in the alternation or `\d+` wins and still stops at the
# comma; the post-comma groups are exactly three digits, so a list like
# "4,5" or "1,23" stays two separate numbers. add() strips the commas
# before parsing.
_FEET_NUM = r"""(?:\d{1,3}(?:,\d{3})+|\d+)"""

# OCR does not always give the inch mark back as an inch mark. Tesseract
# reads Res-A/B's structural title blocks - SCALE: 1/4"=1'-0" - as
# `1/4�=1'-0"`, mangling the double prime into U+FFFD. The pattern then
# cannot advance past that character and the whole caption is missed, which
# is why all 37 Res-A structural and 17 Res-B structural pages reported no
# scale even though OCR had read the text correctly at every dpi tried
# (150/300/450 all produce `SCALE: 1/4�=1'-0"`).
#
# One stray non-word character is therefore allowed where a unit mark
# belongs. It is captured into its OWN group and is deliberately NOT counted
# as evidence by the F92 shape guard: a caption still has to carry a real
# paper term plus either a genuine inch mark or a separator. Here `1/4` and
# `=` supply both, so the match is legitimate on its own merits.
_NOISE = r"""(?P<noise>[^\w\s=:\-])?"""

# 1/4" = 1'-0"   |   1" = 20'   |   1"= 50'   |   1 inch = 10 ft.
# separators = - :  (required here - no keyword to vouch for the match)
_PAT = re.compile(
    rf"""(?P<paper>{_NUM})\s*{_IN}\s*
        (?:[=:\-]\s*){{1,2}}          # OCR sometimes doubles the separator
        (?P<feet>{_FEET_NUM})\s*{_FT}
        (?:\s*-\s*(?P<inches>\d+)\s*{_IN})?""",
    re.X | re.I,
)
_NTS = re.compile(r"(?i)\bN\.?\s*T\.?\s*S\.?\b|NOT\s+TO\s+SCALE")

# OCR often loses the inch/foot marks completely ("SCALE: 1 = = 20").
# Units are then implied by convention (paper inches = real feet), but only
# trust that when anchored right after the SCALE keyword.
# Every part but <feet> is optional, so the word SCALE followed by any bare
# integer also matches - and a keyword hit outranks everything in _choose.
# The separator and the unit marks are captured so that case can be told
# apart and rejected: see the guard in _scan.
_PAT_KEYWORD = re.compile(
    rf"""(?i)scale\s*(?P<kwsep>[:=])?\s*
        (?P<paper>{_NUM})?\s*(?P<inmark>{_IN})?\s*   # OCR may eat the 1"
        {_NOISE}\s*                                        # ... or mangle it
        (?P<sep>(?:[=:\-]\s*){{0,2}})                      # ... or the separator
        (?P<feet>{_FEET_NUM})\s*(?P<ftmark>{_FT})?
        (?:\s*-\s*(?P<inches>\d+)\s*{_IN}?)?""",
    re.X,
)

# OCR word order can put the ratio BEFORE the keyword ('1" 10' SCALE:' on
# the C4.x grading plans): same relaxed body, keyword trailing. Degree
# signs stand in for foot marks in OCR output, so they count as ticks.
# That form carries no separator, so one cannot be required here - instead
# <sep>/<gmark> are captured and checked in _scan: with no separator the
# ground term must declare its own foot mark, or the two numbers are merely
# adjacent rather than related.
_PAT_KEYWORD_PRE = re.compile(
    rf"""(?i)(?P<paper>{_NUM})\s*{_IN}
        (?P<sep>\s*(?:[=:\-]\s*){{0,2}})
        (?P<feet>{_FEET_NUM})\s*(?P<gmark>{_FT}|[°º])?\s*
        (?:-\s*(?P<inches>\d+)\s*{_IN}?\s*)?
        scale\b""",
    re.X,
)

# An '=' or ':' actually relating the two sides of a match. Used to gate the
# feet-and-inches ground form, which is otherwise a dimension callout.
_REL = re.compile(r"[=:]")

# Any REAL unit mark inside a match - inch or foot, tick or spelled out.
# Tells "SCALE 1:100" (no unit anywhere: a dimensionless ratio, see add())
# from `1" : 100'` (units declared: 100 ft to the paper inch). Searched over
# the whole matched text, not the named groups, because _PAT_KEYWORD_PRE's
# inch mark is required but not captured in a group of its own.
_UNIT_MARK = re.compile(rf"{_IN}|{_FT}", re.I)

# Upper bound for the RATIO form, in ft per paper inch. The feet form keeps
# its 400 cap (1" = 400', above every corpus caption), but a unit-free
# ratio is also how small-scale location/vicinity maps are captioned and
# those legitimately pass it - 1:8,010 is 667.5 ft/in. Rather than invent a
# second researched number, mirror the app's own refusal band: server.py
# SCALE_FT_MIN/SCALE_FT_MAX accept up to 1000.0 ft/in (webui/app.js repeats
# the same pair at its SCALE_FT_MAX), so _scan never emits a ratio
# candidate the rest of the app would refuse anyway.
_RATIO_FPI_MAX = 1000.0


@dataclass
class ScaleCandidate:
    text: str
    ft_per_paper_inch: float
    near_scale_keyword: bool
    count: int = 1
    # What this reading GOVERNS: the linework sitting above the title bars
    # that print it, in pt and as a share of everything assigned on the
    # sheet (see _rank). None = not measured - single candidate, ranking
    # skipped, or ranking failed. 0.0 is a MEASURED zero (a caption whose
    # detail has no linework), so test `is None`, never truthiness.
    # `sites` is how many printed caption locations were found for it.
    governs_pt: float | None = None
    governs_pct: float | None = None
    sites: int = 0
    # HOW this reading was recovered, and WHERE off the page. Empty/None for
    # every candidate the ordinary text scan produced - which is why both are
    # omitted from to_dict() when unset, so the serialised candidate list of a
    # page that already reads a scale is byte-identical to before this field
    # existed (the no-regression property; see ScaleResult.to_dict).
    #   "ocr-caption-window" - re-read by OCR off a high-dpi picture of a
    #       small window around a surviving caption head, because the page's
    #       text layer is glyph outlines (_ocr_caption_rescue).
    # `at` is that window's caption head as (x0, y0, x1, y1) in page points,
    # so the reviewer can be told WHERE on the sheet the reading came from
    # rather than being handed a bare number.
    origin: str = ""
    at: tuple | None = None
    # The ground number was read ACROSS A LINE BREAK from its paper term /
    # relation - `1"=` on one get_text() line, `90'` on the next. Set only
    # by the get_text() scan (the row-assembled and OCR paths join words on
    # geometry and emit no line breaks), cleared when the same value is also
    # read intact anywhere on the page. Not serialised on its own: it is
    # evidence for `read_text_layer`'s join-artefact test, and a candidate
    # that test rejects says so through `origin` instead.
    wrapped: bool = False
    # For a `graphic_scale_bar` reading only: WHICH RECOGNISER read the tick
    # ladder, and whether the span was measured on drawn ticks - one of
    # native / native-ticks / psm6-zoom / psm6-zoom-ticks / ocr / ocr-ticks.
    # A bar corroborates a caption only when the two were read by DIFFERENT
    # recognisers (SPEC 6.2 rule 1), and this is how the caller can tell.
    # NOT serialised by to_dict: it is evidence about a second opinion, and
    # a promoted result carries it in `pick["bar_source"]` instead, so a
    # page that already reads a scale serialises byte-identically.
    bar_source: str = ""

    def to_dict(self) -> dict:
        d = {
            "text": self.text,
            "ft_per_paper_inch": self.ft_per_paper_inch,
            "near_scale_keyword": self.near_scale_keyword,
            "count": self.count,
            "governs_pt":
                None if self.governs_pt is None else round(self.governs_pt, 1),
            "governs_pct":
                None if self.governs_pct is None else round(self.governs_pct, 1),
            "sites": self.sites,
        }
        if self.origin:
            d["origin"] = self.origin
        if self.at is not None:
            d["at"] = [round(float(v), 1) for v in self.at]
        return d


@dataclass
class ScaleResult:
    best: ScaleCandidate | None
    candidates: list = field(default_factory=list)
    has_nts: bool = False
    # "text" | "ocr" | "graphic-bar", or why there is no reading:
    # "" = nothing recognisable printed; "ocr-unavailable" = Tesseract is
    # not installed/on PATH so the page was never checked; "ocr-error" =
    # OCR ran and failed on this page. (The UI wiring for the failure
    # sources is still a named follow-up; the ABSTAINED case is wired - see
    # to_dict.)
    source: str = ""
    # Independent second opinion from the drawn GRAPHIC SCALE bar, whose tick
    # spacing is geometry rather than text. A wrong scale is the most
    # expensive silent failure this engine has - 14 corpus pages were wrong
    # and every one reached the UI as a confident green chip (F92) - and on
    # the sheets that carry both readings the two agree everywhere except
    # exactly where the bug was. Cross-checking is therefore the cheapest
    # detector of the whole failure class, and it costs nothing when there
    # is no bar. This NEVER changes which scale is chosen; it only records
    # whether a second source backs it.
    bar_ft_per_paper_inch: float | None = None
    agrees: bool | None = None      # None = no bar to compare against
    # Why `best` is best, stated by the engine instead of replayed from the
    # candidate list by a reader who has to guess (the banner's
    # scalePickWhy() replays _choose's old branches; against this module it
    # lands on its honest "unknown" arm until it is taught to read this).
    # `pick["why"]` is one of:
    #   "single"    one distinct value - nothing to rank
    #   "governs"   ranked by the linework each printed caption governs;
    #               the winner leads strictly (see `margin`)
    #   "unranked"  several values and no ranking could be made - `reason`
    #               says why; `best` is then the old keyword-then-count
    #               order and must be presented as UNCHECKED, not explained
    #   "bar"       the drawn graphic bar - no printed caption at all
    # Alongside: the winner's governed share, the runner-up and its share,
    # their ratio, how much linework had no caption beneath it, how much
    # was excluded as sheet furniture, and which measure was used ("paths"
    # = raw page geometry; "chains" = the caller's own live chains).
    pick: dict = field(default_factory=dict)
    # get_text() readings refused as join artefacts (ORIGIN_JOIN_ARTEFACT)
    # whose value nothing intact on the page carries. NOT candidates: a
    # promoted reading keeps one distinct value (pick "single" means that),
    # and the UI's "this sheet prints N different scales" is built from
    # `candidates` - measured 2026-09-02 on sundale p1, the refused 90 sat
    # in that list beside the true 30 and was offered as a printed scale one
    # click from 3x. Serialised only when non-empty, so a page with no
    # refusal is byte-identical to before.
    refused: list = field(default_factory=list)

    @property
    def delta_pct(self) -> float | None:
        if not self.best or self.bar_ft_per_paper_inch in (None, 0):
            return None
        a = self.best.ft_per_paper_inch
        if not a:
            return None
        return abs(a - self.bar_ft_per_paper_inch) / self.bar_ft_per_paper_inch * 100.0

    def to_dict(self) -> dict | None:
        """The reading, serialised - or the ABSTENTION, serialised.

        Three shapes, and which one you get is decidable without guessing:

          * a reading      `best` is set. Byte-identical to what this method
                           has always returned; nothing below the `if` was
                           touched.
          * an abstention  `best` is None but candidates survived. Returns a
                           dict carrying `"abstained": True` and every
                           candidate with its value, where it was read and
                           why it was not promoted. `ft_per_paper_inch` and
                           `text` are present and EXPLICITLY None: there is
                           no scale in force and no key here may be mistaken
                           for one.
          * nothing        `best` is None and no candidates. Still None,
                           exactly as before - the honest empty state is not
                           an empty list (see detect_page).

        Before 2026-09-01 the abstained case returned None too, so 26 corpus
        pages that HAD recovered real captions serialised identically to a
        sheet that prints nothing, and the operator saw nothing at all.

        THE NO-REGRESSION RULE, and where it is enforced. This method is not
        the boundary; `detect_page` is. A truthy dict under `result["scale"]`
        would be read as "a scale is in force" by ~40 sites in webui/app.js
        and by the export's `body.image_dpi / det["ft_per_paper_inch"]`, so
        detect_page routes the abstained dict to `result["scale_suggestions"]`
        and leaves `result["scale"]` None - the value every existing consumer
        already gets for these pages. An abstained page's `result["scale"]`
        is therefore unchanged, and a page that reads a scale is unchanged
        byte-for-byte (ScaleCandidate.to_dict omits `origin`/`at` when unset).
        """
        if not self.best:
            if not self.candidates:
                return None
            return {
                "abstained": True,
                # present, and None on purpose: a reader that reaches for the
                # number finds an absence rather than a plausible default.
                "ft_per_paper_inch": None,
                "text": None,
                "source": self.source,
                "has_nts": self.has_nts,
                "candidates": [c.to_dict() for c in self.candidates],
                "bar_ft_per_paper_inch": self.bar_ft_per_paper_inch,
                # both were only ever measured against a `best` there isn't
                "agrees": None,
                "delta_pct": None,
                "pick": dict(self.pick),
                **self._refused_dict(),
            }
        d = self.delta_pct
        return {
            **self.best.to_dict(),
            "source": self.source,
            "has_nts": self.has_nts,
            "candidates": [c.to_dict() for c in self.candidates],
            "bar_ft_per_paper_inch": self.bar_ft_per_paper_inch,
            "agrees": self.agrees,
            "delta_pct": None if d is None else round(d, 1),
            "pick": dict(self.pick),
            **self._refused_dict(),
        }

    def _refused_dict(self) -> dict:
        if not self.refused:
            return {}
        return {"refused": [c.to_dict() for c in self.refused]}


def _frac(s: str) -> float:
    """A scale's paper term: "3", "1/2", or the mixed "1-1/2" / "1 1/2"."""
    s = s.strip()
    m = re.fullmatch(r"(\d+)[-\s](\d+)/(\d+)", s)
    if m:
        return float(m.group(1)) + float(m.group(2)) / float(m.group(3))
    if "/" in s:
        a, b = s.split("/")
        return float(a) / float(b)
    return float(s)


def _scan(text: str) -> list[ScaleCandidate]:
    found: dict[tuple, ScaleCandidate] = {}

    def add(m: re.Match, near: bool):
        paper = _frac(m.group("paper") or "1")
        if paper <= 0 or paper > 12:
            return
        feet = (float(m.group("feet").replace(",", ""))
                + (float(m.group("inches") or 0)) / 12.0)
        if feet <= 0:
            return
        # A colon relating the sides, with NO unit anywhere in the match, is
        # the dimensionless RATIO form: "SCALE 1:100" means 1 unit of paper
        # = 100 of the SAME unit on the ground (standard ratio practice on
        # metric and mapping sheets), so ft per paper inch = 100 paper
        # inches of ground / 12 = 8.333. Read as paper=1", ground=100' it
        # was 100.0 - and the UI's own hand-typed parser (webui/app.js
        # parseScale, its "1:200" arm) already reads a bare ratio this way,
        # so the engine disagreed with its own UI 12x on identical text
        # (audit A2). Any declared unit keeps the feet reading: `1" : 100'`
        # stays 100 ft/in, "SCALE 1:100 FT" stays 100, a feet-and-inches
        # suffix (1:1'-6") is compound rather than a ratio, and the <noise>
        # group - a stand-in for an OCR-mangled inch mark - counts as the
        # inch mark it stands where. <kwsep>, the keyword's own colon in
        # "SCALE:", is captured outside <sep> and never triggers this.
        gd = m.groupdict()
        ratio = (":" in (gd.get("sep") or "")
                 and not gd.get("noise")
                 and not gd.get("inches")
                 and not _UNIT_MARK.search(m.group(0)))
        fpi = (feet / paper) / 12.0 if ratio else feet / paper
        # Plausible drawing scales: 1/8"=1' (8) up to 1"=200'.
        #
        # The 0.5 floor was discarding a real and frequently printed scale.
        # Res-A landscaping L804 prints `SCALE: 3" = 1'-0"` FOUR times - more
        # often than any other caption on that sheet - and 3 paper inches to
        # the foot is 0.3333 ft/in, so every one was thrown away and the
        # engine settled on the 3/4" detail scale instead.
        #
        # The floor cannot simply be lowered, because a dimension string
        # computes to the same number: `6" - 2'` on Res-C p18 is also
        # 0.3333, and `6" - 1'` and `8" - 1'` sit below it. What separates
        # them is the RELATION - a scale says "=" or ":" between its two
        # sides, a dimension callout is joined by a hyphen. So the floor
        # relaxes only for a reading that states one.
        #
        # CONVENTION: 3" = 1'-0" is the LARGEST scale on an architect's rule
        # (3/32, 1/8, 3/16, 1/4, 3/8, 1/2, 3/4, 1, 1-1/2, 3 inches to the
        # foot), and 3 paper inches to the foot is exactly 1/3 ft/in - so 0.33
        # is the floor, sitting just under it. Source: 3" is also the largest
        # caption printed anywhere in the Res-A/B or Res-C sets. Anything
        # larger is half- or full-size shop detailing and stays rejected until
        # a sheet shows otherwise. INFERENCE for other drawing standards.
        #
        # Confirmed against drawn geometry, not the caption: on Res-A
        # landscaping L804 detail 9 the three stacked dimensions measure
        # tick-to-tick 36.00 / 72.00 / 108.00 pt for 2" / 4" / 6", which is
        # 18.000 pt per real inch on all three - exactly 3" = 1'-0".
        floor = 0.33 if _REL.search(m.group(0)) else 0.5
        if not (floor <= fpi <= (_RATIO_FPI_MAX if ratio else 400)):
            return
        # Does the ground number sit on a different text line from the
        # paper term that is supposed to relate to it? get_text() separates
        # lines with "\n" and every `\s*` above walks across one, so a
        # caption whose number is its own text object - the AutoCAD scale
        # block prints `SCALE: 1"=` and the value as separate attributes -
        # welds to whatever line get_text() emits NEXT. On the Walsh
        # Engineering grading plans (public-ceqa sundale-road-30366 engine
        # page index 1, harbison-canyon-21316 index 0) the next line is the
        # drawn bar's LAST tick label, so `1"=` + `90'` read 90 ft/in where
        # the sheet prints 1"=30' and its `30'` sits 1.8pt to the right of
        # the `=` on the same printed row. Measured from paper (or the match
        # start when a keyword form carries no paper term) to the number.
        a = m.start("paper") if gd.get("paper") else m.start()
        wrapped = "\n" in text[a:m.start("feet")]
        key = (round(fpi, 4), near)
        if key in found:
            found[key].count += 1
            found[key].wrapped = found[key].wrapped and wrapped
        else:
            found[key] = ScaleCandidate(m.group(0).strip(), fpi, near,
                                        wrapped=wrapped)

    def compound_needs_relation(m: re.Match) -> bool:
        """Reject a feet-and-inches ground term with nothing relating it.

        N'-M" is how a dimension callout is written, so on its own it is a
        length. It is also the right-hand side of an architectural scale,
        but there the two sides are related in one of two visible ways:

          * an explicit '=' or ':' BETWEEN them (<sep>, not the keyword's
            own colon in "SCALE:") - 1/4" = 1'-0", 3/16" = 1'-0",
            1" = 16'-0"; or
          * the ground side is exactly 1'-0", which is what the notation
            means - "an eighth of an inch to the foot". CONVENTION, from
            these sheets: every architectural caption in the Res-A/B sets
            is written X" = 1'-0", i.e. the ground side is one foot. OCR
            drops the '=' often enough (Res-C p57/p58 read
            '1/8" 1'-0" SCALE:') that the form has to survive without it.

        Anything else - 5'-2", 11'-3 1/2" - is a callout, not a ratio.
        """
        if not m.group("inches"):
            return False
        sep = m.group("sep") or ""
        if _REL.search(sep):
            return False
        return not (int(m.group("feet").replace(",", "")) == 1
                    and int(m.group("inches")) == 0)

    for m in _PAT.finditer(text):
        ctx = text[max(0, m.start() - 30): m.start()]
        add(m, bool(re.search(r"(?i)scale", ctx)))
    for m in _PAT_KEYWORD.finditer(text):
        # A scale is a RATIO, so BOTH of its sides have to be on the page.
        # Every part of this pattern but <feet> is optional, so without a
        # shape test it reduces to "the word SCALE followed by a number" -
        # and a keyword hit outranks everything in _choose. Two live cases:
        # Res-B C3.0 (engine page index 2) row-joined to "... SCALE 2% 2% W
        # CONSTRUCTION ..." and read 1" = 2 ft over the sheet's own "1 inch
        # = 10 ft." (5x on every length, 25x on every area and volume);
        # Res-A civil (engine page index 1) row-joined the bar caption to a
        # 5'-2" dimension callout and read 1" = 5.17 ft over "1 inch = 20
        # ft.". Requiring any ONE token is not enough - the second case
        # carries a foot mark, but it belongs to the dimension, not to a
        # ratio. Require the LEFT side (<paper>) plus something joining it
        # to the right side: its own inch mark, or a separator. The OCR
        # case this pattern exists for ("SCALE: 1 = = 20") has both.
        # <kwsep> is the keyword's own colon in "SCALE:", not a relation
        # between two sides, so it is not evidence on its own.
        if not (m.group("paper")
                and (m.group("inmark") or (m.group("sep") or "").strip())):
            continue
        if compound_needs_relation(m):
            continue
        add(m, True)
    for m in _PAT_KEYWORD_PRE.finditer(text):
        # No separator between the sides -> the ground term has to declare
        # its own unit, else these are two unrelated numbers that only
        # happen to sit beside the word SCALE ('3/8" 6 SCALE', Res-B civil
        # engine page index 6, from OCR).
        if not ((m.group("sep") or "").strip() or m.group("gmark")):
            continue
        if compound_needs_relation(m):
            continue
        add(m, True)
    return list(found.values())


# ---------------------------------------------------------------------------
# Which of several printed scales governs the drawing
# ---------------------------------------------------------------------------
# A sheet that carries a plan and its details prints one caption per drawing,
# and the choice used to be "keyword-adjacent, then most repeated". On a
# detail sheet that runs exactly backwards: many small details, one big
# plan, so caption count is INVERSELY related to how much drawing a scale
# governs. Audit RC-2, root of eight findings; live regression F113: 08e818d
# made `3" = 1'-0"` readable and count promptly handed Res-A landscaping L804
# (engine page index 29 = printed sheet 30) to it over the `3/4"` scale.
#
# So rank by WHAT EACH CAPTION GOVERNS. The document asserts the binding:
# every multi-scale sheet in the corpus prints a detail's caption in a TITLE
# BAR beneath the detail - bubble number, title, `SCALE: ...`, and a
# horizontal rule - and the drawing is the linework above that rule. Three
# independent drafters agree (Res-A/B landscaping/structural, Res-C): all 104
# caption sites on the corpus' 16 multi-candidate pages sit on such a rule
# (per-page counts in the sweep's sites/rules fields, 2026-08-22 choose
# sweep). Measured rule-to-caption gaps: 7.1 pt on L804, 3.2 on Res-B
# structural S6-0.0, 1.0 on Res-C A1.2; the next horizontal stroke above
# a caption is the detail's own linework, 20+ pt up. CONVENTION, from these
# sheets: the title bar sits BELOW its drawing. Labelled an INFERENCE for
# drafters outside the corpus; where it fails to locate a caption the sheet
# reports "unranked" rather than a wrong answer.
#
# Assignment, per stroke unit: the caption beneath it (rule y >= centre y)
# with the least cost, cost = vertical gap to the rule + horizontal excess
# outside the rule's x-span. Nothing is bucketed; a unit with no caption
# beneath it is UNASSIGNED and reported as such. The measure is
# extract_strokes' own units - the geometry the chain stage prices, before
# chaining - minus whiteout and HATCH-layer strokes (the chain stage's own
# two cheapest demotions). That is a PROXY for the live chains: checked
# against build_chains' live chains under the same assignment on 15 of the
# corpus' 16 multi-candidate pages (the 16th, Res-A structural engine p1,
# is OCR-sourced and was not cross-checked): same winner on 15 of 15,
# winner's share within 8.5 points everywhere (worst: Res-C p8, 73.6%
# raw vs 65.1% chains; see chains_vs_paths.log in
# out/verify/2026-08-22/choose/work/). A caller that already built the
# page's chains can pass them to detect_scale(linework=...) and the
# ranking uses those instead.
#
# What this does NOT do: apply different scales to different regions of one
# sheet. That is per-viewport scaling and a separate design. This picks one
# default, and states why - including the margin, because on a sheet of ten
# details the "winner" can govern under 40% of the linework and the reader
# deserves to know how thin that is.

# How far above a caption's top edge its title-bar rule may sit (measured:
# 7.1 / 3.2 / 1.0 pt across the three drafters), and how far below (the
# rule never sits below the caption on any corpus sheet; 2 pt of slop).
_RULE_ABOVE = 12.0
_RULE_BELOW = 2.0
# The shortest horizontal stroke read as a rule candidate. Res-C draws
# the narrowest title bars in the corpus; its shortest rule measures
# 100.7 pt (A1.2). Text underlines in note blocks are shorter than 60.
_RULE_MIN_PT = 60.0
# A straight stroke spanning at least this fraction of the page in one
# axis, sitting in the outer quarter of the page on the other axis, with
# every caption on the interior side, cuts a SHEET-FURNITURE strip off the
# drawing area (the title-block column: logo, consultant list, revision
# grid). The far side is not counted at all - neither governed nor
# unassigned. Both conditions are load-bearing: the Res-A/B template's divider
# at x = 2736 of 3024 spans 0.93 of the page height and its strip holds
# 24,111 pt of exploded logo and consultant text (L804, measured), which
# would otherwise be assigned to whatever detail column sits nearest - on
# L804 that flips the sheet to `3"`, the exact wrong answer this ranking
# exists to prevent; but a long drawn line in the MIDDLE of a sheet - a
# road centreline, a property line - must never cut, so the strip must hug
# an edge. chain.py's _demote_sheet_furniture solves the same problem by
# layer name + edge position; the L804 title block carries NO layer name
# (463 unnamed units), so geometry has to carry it here.
_DIVIDER_FRAC = 0.8
_EDGE_STRIP = 0.25
# Two leaders closer than this fraction of all assigned linework are a
# TIE: the sheet does not have a single governing scale that this measure
# can name, and saying so beats picking by 0.4% of a proxy. This is a
# numerical-equality guard, not a tuned threshold - the closest real
# margin in the corpus is 1.16x (L804), more than thirty times wider.
_TIE_FRAC = 0.005


def _text_lines(words: list) -> list[list]:
    """Word boxes grouped into printed lines, each sorted left to right.

    Text-layer words carry mupdf's own (block, line) ids - the drafter's
    line breaks - and those are used as they stand (the document asserts,
    we do not re-infer). OCR words are 5-tuples with no ids, so they are
    walked by vertical overlap and the height-keyed column gap already
    measured for _row_text. Sweep LEFT TO RIGHT, not top to bottom: OCR
    puts "SCALE:" a point or two below its own ratio often enough that a
    top-down walk reaches the ratio first, opens a row, and then cannot
    accept a word to its left.
    """
    if words and len(words[0]) >= 7:
        grp: dict = {}
        for w in words:
            grp.setdefault((w[5], w[6]), []).append(w)
        return [sorted(v, key=lambda w: float(w[0])) for v in grp.values()]
    rows: list[list] = []
    for w in sorted(words, key=lambda w: (float(w[0]), float(w[1]))):
        if not str(w[4]).strip():
            continue
        best, best_ov = None, 0.0
        for r in rows:
            p = r[-1]
            h = min(float(p[3]) - float(p[1]), float(w[3]) - float(w[1]))
            ov = min(float(p[3]), float(w[3])) - max(float(p[1]), float(w[1]))
            gap = float(w[0]) - float(p[2])
            if ov > 0.5 * max(h, 1.0) and ov > best_ov and -2.0 <= gap <= max(
                    _ROW_GAP_MIN, _ROW_GAP_HEIGHTS * max(h, 1.0)):
                best, best_ov = r, ov
        if best is None:
            rows.append([w])
        else:
            best.append(w)
    return rows


def _caption_sites(words: list, hlines: list) -> list[dict]:
    """Every printed scale caption, where it sits, and its title-bar rule.

    One site per (printed line, scale value). `rule` is the LONGEST
    horizontal stroke in the band just above the caption that starts at or
    left of the caption's left edge and is at least as wide as the caption
    - an explicit ranking, not first-found. None when the caption has no
    rule; the caption's own box then stands in for it.

    `compound` marks the sites of a line that yielded MORE THAN ONE distinct
    value. Those sites are all given the identical box and rule - they are
    the same printed characters - so nothing downstream can tell them apart
    and the assignment in `_governance` cannot honestly be made. See _rank,
    which refuses to rank such a page. A line printing the SAME value twice
    is not compound: the set below collapses it to one site.
    """
    out = []
    for L in _text_lines(words):
        txt = " ".join(str(w[4]) for w in L)
        cs = _scan(txt)
        if not cs:
            continue
        x0 = min(float(w[0]) for w in L)
        y0 = min(float(w[1]) for w in L)
        x1 = max(float(w[2]) for w in L)
        y1 = max(float(w[3]) for w in L)
        rule = None
        for idx, (hx0, hx1, hy) in hlines:
            if (y0 - _RULE_ABOVE <= hy <= y1 + _RULE_BELOW
                    and hx0 - 4.0 <= x0 <= hx1
                    and hx1 - hx0 >= x1 - x0
                    and (rule is None or hx1 - hx0 > rule[1] - rule[0])):
                rule = (hx0, hx1, hy, idx)
        vals = sorted({round(c.ft_per_paper_inch, 4) for c in cs})
        for v in vals:
            out.append({"fpi": v, "text": txt, "box": (x0, y0, x1, y1),
                        "rule": rule, "compound": len(vals) > 1})
    return out


def _page_linework(page: pymupdf.Page) -> tuple[list, list, list]:
    """The page's stroked linework, read through extract_strokes itself
    so clip regions, hidden OCG layers, rotation and thin-rect centrelines
    are handled by the ONE implementation that already handles them.

    Returns (paths, hlines, dividers):
      paths    [(length, cx, cy), ...] one entry per stroke unit that is
               not whiteout and not on a HATCH layer (the chain stage's own
               two cheapest demotions, same tests), minus the page frame;
               (cx, cy) is the unit's bbox centre.
      hlines   [(path index, (x0, x1, y)), ...] horizontal strokes >=
               _RULE_MIN_PT - the title-bar rule candidates.
      dividers [("v", x, path index) | ("h", y, path index), ...] straight
               strokes spanning >= _DIVIDER_FRAC of the page in one axis.
    """
    from .extract import extract_strokes

    W, H = float(page.rect.width), float(page.rect.height)
    paths: list = []
    hlines: list = []
    dividers: list = []
    for u in extract_strokes(page):
        if u.color and min(u.color) > 0.92 and not u.on_fill:
            continue                    # whiteout (chain.py's skip, verbatim)
        lay = (u.layer or "").upper()
        if "HATCH" in lay and "NO HATCH" not in lay and "NOHATCH" not in lay:
            continue                    # hatch decoration (chain.py's test)
        if len(u.pts) < 2 or u.length <= 0.0:
            continue
        mn = u.pts.min(axis=0)
        mx = u.pts.max(axis=0)
        w, h = float(mx[0] - mn[0]), float(mx[1] - mn[1])
        if w >= _DIVIDER_FRAC * W and h >= _DIVIDER_FRAC * H:
            continue                    # the page frame (finding #10)
        idx = len(paths)
        paths.append((float(u.length),
                      float(mn[0] + mx[0]) / 2.0, float(mn[1] + mx[1]) / 2.0))
        # "Straight" tolerances are sliver widths, not hairlines: a rule or
        # a divider is routinely PLOTTED as a thin filled box, and
        # extract_strokes collapses those to centrelines that keep a little
        # width (the Res-A/B divider at x=2736 is a 4.5 pt sliver whose
        # centreline units carry 2.3-4.5 pt of bbox width; a 0.6 pt test
        # missed all three and the title block silently rejoined the
        # drawing). 4 pt for a rule, 6 for a divider - both far under the
        # 12 pt of _RULE_ABOVE headroom and the quarter-page edge strip
        # that consume them.
        if h <= 4.0 and w >= _RULE_MIN_PT:
            hlines.append((idx, (float(mn[0]), float(mx[0]),
                                 float(mn[1] + mx[1]) / 2.0)))
            if w >= _DIVIDER_FRAC * W:
                dividers.append(("h", float(mn[1] + mx[1]) / 2.0, idx))
        elif w <= 6.0 and h >= _DIVIDER_FRAC * H:
            dividers.append(("v", float(mn[0] + mx[0]) / 2.0, idx))
    return paths, hlines, dividers


def _chain_linework(chains) -> list:
    """(length, cx, cy) per LIVE chain, for a caller that already built
    them: objects with .pts/.length/.klass, or plain (length, cx, cy)
    triples. Annotation chains are the chain stage's own junk verdict and
    stay out of the measure."""
    out = []
    for c in chains:
        if isinstance(c, (tuple, list)) and len(c) == 3:
            out.append((float(c[0]), float(c[1]), float(c[2])))
            continue
        if getattr(c, "klass", None) == "annotation":
            continue
        pts = np.asarray(getattr(c, "pts", ()), dtype=float)
        if len(pts) < 2:
            continue
        mn, mx = pts.min(axis=0), pts.max(axis=0)
        out.append((float(getattr(c, "length", 0.0)),
                    float(mn[0] + mx[0]) / 2.0, float(mn[1] + mx[1]) / 2.0))
    return out


def _governance(sites: list, paths: list, dividers: list, skip: set,
                page_wh: tuple) -> tuple[dict, float, float]:
    """Linework governed per scale value.

    Returns ({fpi: pt}, unassigned pt, furniture pt). A divider whose far
    side is an edge strip holding no caption brackets the drawing area;
    paths beyond it are sheet furniture and count toward neither share nor
    denominator. Paths in `skip` - the title-bar rules and the dividers
    themselves - are not counted either: a rule is one per caption, and
    counting it would be the caption-count bias coming back in through the
    geometry (531 pt per caption on the Res-A/B template).
    """
    W, H = page_wh
    far: list = []
    for kind, pos, _ in dividers:
        ext = W if kind == "v" else H
        j = 0 if kind == "v" else 1
        lo = min(s["box"][j] for s in sites)
        hi = max(s["box"][j + 2] for s in sites)
        if pos >= (1.0 - _EDGE_STRIP) * ext and hi < pos:
            far.append((kind, pos, +1))     # strip beyond pos is furniture
        elif pos <= _EDGE_STRIP * ext and lo > pos:
            far.append((kind, pos, -1))
    tot: dict = {}
    unassigned = 0.0
    furniture = 0.0
    for i, (L, cx, cy) in enumerate(paths):
        if i in skip:
            continue
        if any((cx if kind == "v" else cy) * sgn > pos * sgn
               for kind, pos, sgn in far):
            furniture += L
            continue
        best, bc = None, None
        for s in sites:
            if s["rule"]:
                rx0, rx1, ry = s["rule"][0], s["rule"][1], s["rule"][2]
            else:
                rx0, _, rx1, ry = s["box"]
            if ry < cy - _RULE_BELOW:
                continue           # caption sits above this: not its drawing
            cost = (ry - cy) + max(0.0, rx0 - cx, cx - rx1)
            if bc is None or cost < bc:
                best, bc = s, cost
        if best is None:
            unassigned += L
        else:
            tot[best["fpi"]] = tot.get(best["fpi"], 0.0) + L
    return tot, unassigned, furniture


def _rank(cands: list[ScaleCandidate], page: pymupdf.Page | None = None,
          words: list | None = None, linework=None
          ) -> tuple[ScaleCandidate | None, dict]:
    """The default scale for the sheet, and why.

    One distinct value: it wins, why="single". Several: each is scored by
    the linework above whose title bars it is printed (see the section
    comment) and the strict leader wins, why="governs", with the margin
    stated. When that cannot be done - no geometry, one printed line
    carrying two scales, a value whose caption cannot be located, nothing
    assigned, or two leaders too close to call - the old keyword-then-count
    order stands in and why="unranked" with the reason, so a reader knows
    the pick is UNCHECKED rather than explained.
    """
    if not cands:
        return None, {}
    order = sorted(cands, key=lambda c: (-c.near_scale_keyword, -c.count))
    values = sorted({round(c.ft_per_paper_inch, 4) for c in cands})
    if len(values) == 1:
        return order[0], {"why": "single"}

    def rep(v):   # the reading standing for a value: keyword first, then count
        return next(c for c in order if round(c.ft_per_paper_inch, 4) == v)

    def unranked(reason):
        return order[0], {"why": "unranked", "reason": reason,
                          "values": values}

    if page is None and linework is None:
        return unranked("no geometry")
    try:
        if words is None:
            from .extract import page_words
            words = page_words(page)
        if page is not None:
            paths, hlines, dividers = _page_linework(page)
            page_wh = (float(page.rect.width), float(page.rect.height))
        else:
            paths, hlines, dividers = [], [], []
            page_wh = (0.0, 0.0)
        measure = "paths"
        if linework is not None:
            # the caller's live chains replace the raw units; the title-bar
            # rules and dividers are still read off the page for placement
            paths = _chain_linework(linework)
            measure = "chains"
        sites = _caption_sites(words, hlines)
    except Exception as e:  # noqa: BLE001 - ranking must never take the reading down
        return unranked(f"geometry failed: {type(e).__name__}")
    for c in cands:
        c.sites = sum(1 for s in sites
                      if s["fpi"] == round(c.ft_per_paper_inch, 4))
    # A plan-and-profile sheet prints BOTH its scales on ONE printed line:
    #   PROFILE 1"=80' (HOR), 1"=20' (VERT)
    #     Belle Farms Upper Parcel ROAD GRADING 1 of 2.pdf engine page index 0
    #   H: 1" = 20';  V: 1" = 5'
    #     a river set's sheet C101, engine page index 3 of both
    #     Plans/Plans.pdf and Addenda/Addenda/1.pdf - the same drawing twice
    # `_caption_sites` makes one site per (line, value), so those two get an
    # IDENTICAL box and rule, and `_governance`'s strict `cost < bc` then
    # hands the WHOLE line's ink to whichever value it visits first - the
    # smaller, since the sites are emitted sorted. Defect shape B,
    # first-writer-wins, inside the new ranker: the runner-up gets a literal
    # zero, so the near-tie guard cannot fire either (the river set's C101
    # reported `governs 100.0%` with `runner_up_pct 0.0` while taking the
    # profile's VERTICAL exaggeration as the sheet scale - 4x wrong on every
    # length, 16x on every area, and `agrees` null because the sheet draws
    # no bar).
    #
    # There is no honest split. The compound line's drawing is a PROFILE: a
    # graph whose X axis is at one scale and whose Y axis is at another, so
    # its ink is governed by both at once and by neither alone. Halving it,
    # or reading the (HOR)/(VERT) markers to pick a side, would turn an
    # unknown into a guess and still report `governs`. So the whole page is
    # unrankable: the old keyword-then-count order stands in and is flagged
    # UNCHECKED. That is a lost ranking, never a wrong number.
    #
    # Whole page, not just the compound site: once a line's ink has gone
    # somewhere arbitrary, every share on the sheet is fiction even when the
    # winner happens to survive. Belle Farms 2 of 2 engine page index 0 is
    # exactly that - it still reads 60.0, but on a reported 71.4/28.6 whose
    # 28.6 belongs to 20' and 80' jointly. Measured before this guard:
    # 1 of 2 p0 flipped 60.0 -> 20.0 (3x wrong, no symptom); 2 of 2 p0 held
    # by luck. Verified against the drawn geometry on 1 of 2 p0: the plan's
    # own unlabelled bar spans 143.96 pt for its 0-120' ticks = 60.02 ft/in,
    # while the profile's axes measure 1890.11 pt / 2100 ft = 79.995 (HOR)
    # and 540.12 pt / 150 ft = 19.996 (VERT).
    #
    # Tested before `missing`: an unattributable line is the stronger
    # statement - the attribution itself is unsafe, not merely incomplete.
    if any(s["compound"] for s in sites):
        return unranked("compound caption")
    missing = [v for v in values if not any(s["fpi"] == v for s in sites)]
    if missing:
        return unranked(f"no caption located for {missing}")
    skip = set()
    if measure == "paths":
        skip = {s["rule"][3] for s in sites if s["rule"]}
        skip |= {d[2] for d in dividers}
    tot, unassigned, furniture = _governance(sites, paths, dividers, skip,
                                             page_wh)
    assigned = sum(tot.values())
    if assigned <= 0.0:
        return unranked("no linework beneath any caption")
    for c in cands:
        v = round(c.ft_per_paper_inch, 4)
        c.governs_pt = tot.get(v, 0.0)
        c.governs_pct = 100.0 * c.governs_pt / assigned
    ranked = sorted(values, key=lambda v: -tot.get(v, 0.0))
    top, second = tot.get(ranked[0], 0.0), tot.get(ranked[1], 0.0)
    denom = assigned + unassigned
    info = {
        "values": values,
        "measure": measure,
        "governs_pct": round(100.0 * top / assigned, 1),
        "runner_up": ranked[1],
        "runner_up_pct": round(100.0 * second / assigned, 1),
        "margin": None if second <= 0.0 else round(top / second, 2),
        "unassigned_pct":
            0.0 if denom <= 0.0 else round(100.0 * unassigned / denom, 1),
        "furniture_pct": 0.0 if furniture <= 0.0 else round(
            100.0 * furniture / (denom + furniture), 1),
        "sites": len(sites),
        "rules": sum(1 for s in sites if s["rule"]),
    }
    if top - second <= _TIE_FRAC * assigned:
        info.update(why="unranked", reason="tie")
        return order[0], info
    info["why"] = "governs"
    return rep(ranked[0]), info


def _choose(cands: list[ScaleCandidate]) -> ScaleCandidate | None:
    """The geometry-free pick: keyword-adjacent first, then most repeated.
    Kept for callers with no page in hand; detect_scale ranks through
    _rank, which falls back to exactly this order when it cannot rank."""
    return _rank(cands)[0]


_TICK = re.compile(r"^(\d{1,4})\s*[\'’]?$")

# What the corpus actually captions a drawn scale bar. Enumerated from a
# census of every >=3-numeric-label tick row over the 650-page coverage set
# (out/verify/2026-08-22/bar/caption_census.jsonl), not guessed:
#   GRAPHIC SCALE (22 text pages; substation, Res-A civil, grading set),
#   GRAPHIC SCALE IN FEET (a commercial set), SCALE IN FEET (Res-C C1.0,
#   Loves Lane C0.00), (SCALE IN FEET) (all stair-set sheets, OCR),
#   lowercase `scale` alone (8 Res-A landscaping sheets), SCALE FEET
#   (water main 2), HORIZ. SCALE IN FEET / HORIZONTAL SCALE: 1"=  (water main 1,
#   mixed-use profile sheets), SCALE: <ratio> (Res-A landscaping details,
#   Res-C L-1/L-2 scans).
# Every one of those starts a word whose ALPHABETIC CORE is exactly
# GRAPHIC or SCALE. The core must be exact: `GRAPHICALLY` inside a survey
# note manufactured both of the grading set's bar readings (p7 180.0 and p10 18.0,
# against bars that truly draw 300.0 and 30.0), and `TOPOGRAPHIC` /
# `SCALE.DWG`-style tokens are title-block text, not captions.
_BAR_HEAD_CORES = ("GRAPHIC", "SCALE")
# A head is refused when its own printed line says the bar is not the
# plan's horizontal drawing scale:
#   VERT/VERTICAL - profile sheets draw a second, exaggerated bar
#     ("VERT. SCALE IN FEET" under a 5 ft bar beside a 40 ft horizontal
#     one, water main 1 p297); reading it would shrink every plan length.
#     This head test is NOT the refusal - it only stops a VERT-captioned
#     head from starting a search. Both of water main 1 p297's bars sit
#     inside the reach of the surviving HORIZ. head, and the vertical
#     row loses to the horizontal one there purely because
#     `sorted(rows, key=len, reverse=True)` is stable and the horizontal
#     bar happens to be drawn above; traced by hand the vertical row
#     returns 10.0, a 4x error. A contractor's plan set p4 is the same shape and
#     is right today only because 150-dpi OCR drops its pale-grey
#     VERTICAL labels. `_row_axis`, applied per row, is the real
#     refusal; sort order is not allowed to carry a correctness
#     property.
#   INCHES - `REF. SCALE IN INCHES 0 1` is a print-calibration ruler
#     (checks the sheet was not reduced on a copier), not a drawing scale
#     (F112; 46 pages across two documents).
#   MAP - `Soil Map may not be valid at this scale.` sits beside two
#     soil-report exhibit bars (a sewer project p418) whose ratios are not
#     drawing scales.
#   NOTED/SHOWN - `scale: AS NOTED` / `SCALE: AS SHOWN` is the sheet
#     asserting its scale VARIES per view (a client's schedule sheets, F112's
#     correct refusals); no single number near that head speaks for the
#     page, and on a scanned schedule the numbers nearby are door tags.
_BAR_HEAD_STOP = ("VERT", "VERTICAL", "INCHES", "MAP", "MAPS",
                  "NOTED", "SHOWN")


def _alpha_core(text: str) -> str:
    """Leading run of letters after any leading punctuation: '(SCALE' ->
    'SCALE', 'SCALE:1"' -> 'SCALE', 'scale.' -> 'SCALE'."""
    m = re.match(r"[^A-Za-z]*([A-Za-z]+)", str(text))
    return m.group(1).upper() if m else ""


def _bar_heads(ws: list) -> list:
    """Caption-head words, GRAPHIC-core first, refusing heads whose own
    printed line disqualifies the bar (see _BAR_HEAD_STOP)."""
    out = []
    for w in ws:
        core = _alpha_core(w[4])
        if core not in _BAR_HEAD_CORES:
            continue
        hy0, hy1 = float(w[1]), float(w[3])
        hh = max(hy1 - hy0, 1.0)
        hx = (float(w[0]) + float(w[2])) / 2
        line = [x for x in ws
                if min(hy1, float(x[3])) - max(hy0, float(x[1]))
                > 0.5 * min(hh, max(float(x[3]) - float(x[1]), 1.0))
                and abs(((float(x[0]) + float(x[2])) / 2) - hx) < 250]
        cores = {_alpha_core(x[4]) for x in line}
        if cores & set(_BAR_HEAD_STOP):
            continue
        if _NTS.search(" ".join(str(x[4]) for x in line)):
            continue
        out.append(w)
    out.sort(key=lambda w: _alpha_core(w[4]) != "GRAPHIC")
    return out[:8]


def _page_ticks(page) -> list:
    """Vertical stroke edges in display space: [(x, y0, y1), ...].

    These are the drawn tick marks a bar's labels sit on - short vertical
    'l' segments (substation, Res-A landscaping box edges) and the vertical
    edges of the thin filled boxes a checkerboard bar is built from
    (stair set, grading set, water main 1). Long strokes are excluded so borders and
    gridlines do not offer themselves as ticks.
    """
    out = []
    try:
        rot = page.rotation_matrix
        off = {info["name"]
               for info in (page.parent.get_ocgs() or {}).values()
               if not info.get("on", True)}
        for dr in page.get_drawings():
            if dr.get("layer") in off:
                continue
            for it in dr["items"]:
                if it[0] == "l":
                    a, b = it[1] * rot, it[2] * rot
                    if abs(a.x - b.x) < 0.7 and 0.8 <= abs(a.y - b.y) <= 60:
                        out.append(((a.x + b.x) / 2,
                                    min(a.y, b.y), max(a.y, b.y)))
                elif it[0] in ("re", "qu"):
                    r = it[1].rect if it[0] == "qu" else pymupdf.Rect(it[1])
                    r = pymupdf.Rect(r * rot)
                    r.normalize()
                    if r.height <= 20 and r.width >= 2:
                        out.append((r.x0, r.y0, r.y1))
                        out.append((r.x1, r.y0, r.y1))
                    elif r.width <= 3 and 0.8 <= r.height <= 60:
                        out.append(((r.x0 + r.x1) / 2, r.y0, r.y1))
    except Exception:
        return []
    return out


def _row_ticks(ticks: list, row_words: list, near: list) -> tuple:
    """(tick x-positions, drawn band) available to one label row.

    Band: the row's own x-range (padded) by 2.5 label-heights above and
    below - the substation draws ticks under the labels, Res-A landscaping above,
    the stair set's bar boxes sit below. A stroke that lies mostly INSIDE some
    word's box is a glyph stroke, not a tick (the stair set's vector-outlined '4'
    is itself a vertical line exactly where a tick would be) - but only a
    word CONTAINING a letter or digit vetoes: OCR reads the drawn bar
    itself back as punctuation ('|', '\\\\'), and letting those boxes veto
    ate the real end tick on the stair set's improvement sheet.

    The second return value is where the drawn bar actually SITS -
    (y0, y1), the MEDIAN vertical extent of the accepted strokes, or None
    when none were accepted. A bar is many strokes of one height, so the
    median is the bar itself and a stray stroke that wandered into the
    search band cannot move it; min/max could. `_row_axis` needs it to
    tell a profile's vertical bar from its horizontal one.
    """
    lo = min(float(w[1]) for w in row_words)
    hi = max(float(w[3]) for w in row_words)
    h = max(hi - lo, 2.0)
    x0 = min(float(w[0]) for w in row_words) - 30.0
    x1 = max(float(w[2]) for w in row_words) + 30.0
    y0, y1 = lo - 2.5 * h, hi + 2.5 * h
    out, ext = [], []
    for x, ty0, ty1 in ticks:
        if not (x0 <= x <= x1 and ty1 > y0 and ty0 < y1 and ty1 - ty0 <= 4 * h):
            continue
        glyph = False
        for w in near:
            if not re.search(r"[0-9A-Za-z]", str(w[4])):
                continue
            if float(w[0]) - 0.4 <= x <= float(w[2]) + 0.4:
                ov = min(ty1, float(w[3])) - max(ty0, float(w[1]))
                if ov >= 0.6 * (ty1 - ty0):
                    glyph = True
                    break
        if not glyph:
            out.append(x)
            ext.append((ty0, ty1))
    if not ext:
        return [], None
    a = sorted(t[0] for t in ext)
    b = sorted(t[1] for t in ext)
    return sorted(out), (a[len(a) // 2], b[len(b) // 2])


# Axis captions. A plan-and-profile sheet draws TWO scale bars: the
# horizontal one measures distance along the plan, the vertical one is
# deliberately exaggerated so the grades are readable. Water main 1 p297
# prints 40 ft/in horizontal against 5 ft/in vertical (8x); a contractor's set
# p4 prints 30 against 5 (6x). Reading the vertical bar as the page scale
# divides every plan length by that factor - silently.
#
# CONVENTION: on a plan-and-profile sheet the HORIZONTAL scale governs
# plan lengths and the vertical is a deliberate exaggeration. Source:
# these sheets print both bars captioned exactly so - water main 1 "HORIZ.
# SCALE IN FEET" / "VERT. SCALE IN FEET", the contractor's "HORIZONTAL" /
# "VERTICAL" - and F126 measured a 4x vertical exaggeration on Belle
# Farms from the profile's own axis labels. Labelled an INFERENCE for any
# drafter who captions differently: if a sheet ever means something else
# by these words, this refuses a reading rather than inventing one.
_AXIS_VERT = ("VERT", "VERTICAL")
_AXIS_HORIZ = ("HORIZ", "HORIZONTAL")


def _axis_marks(near: list) -> list:
    """Axis words near a caption head: [(axis, cx, y0, y1, captioned)].

    `captioned` is True when the word shares its printed line with a
    GRAPHIC/SCALE word, i.e. it is part of a full bar caption ("HORIZ.
    SCALE IN FEET") rather than a bare axis label ("VERTICAL" alone on
    its line). The two say different things about what they are next to,
    which `_row_axis` depends on. Line membership is the same y-overlap
    test `_bar_heads` uses.
    """
    out = []
    for w in near:
        core = _alpha_core(w[4])
        axis = ("VERT" if core in _AXIS_VERT
                else "HORIZ" if core in _AXIS_HORIZ else "")
        if not axis:
            continue
        wy0, wy1 = float(w[1]), float(w[3])
        wh = max(wy1 - wy0, 1.0)
        wx = (float(w[0]) + float(w[2])) / 2
        cap = any(_alpha_core(x[4]) in _BAR_HEAD_CORES
                  and min(wy1, float(x[3])) - max(wy0, float(x[1]))
                  > 0.5 * min(wh, max(float(x[3]) - float(x[1]), 1.0))
                  and abs(((float(x[0]) + float(x[2])) / 2) - wx) < 250
                  for x in near)
        out.append((axis, wx, wy0, wy1, cap))
    return out


def _row_axis(ri: int, boxes: list, bands: list, marks: list) -> str:
    """Which axis this label row belongs to: "VERT", "HORIZ" or "".

    NOT "the nearest axis word to the row". Proximity does not separate
    these and the corpus says so: on water main 1 p297 the vertical bar's
    labels sit 3.37pt from the HORIZ. caption - which belongs to the bar
    ABOVE them - and 4.98pt from their own VERT. one. A rule that took
    the nearer would be memorising 1.6pt of noise (AGENT-BRIEF 1.5) and
    would get that row wrong. Nor is it "the caption on the far side of
    the bar": the two layouts the corpus prints disagree about which
    side the caption goes on, so no side rule fits both.

    What separates them is WHAT KIND of axis word it is:

      A full caption line - the axis word shares its line with the head
      word, "HORIZ. SCALE IN FEET" - captions a BAR. A water main set,
      2025.pdf, engine page index 297 stacks two of them:
          354.2-359.5  20 10 0 20 40
          361.2-365.8  [bar 1]
          368.3-373.4  HORIZ. SCALE IN FEET
          376.7-381.8  5 2.5 0 5 10
          382.1-385.9  [bar 2]
          390.9-395.9  VERT. SCALE IN FEET
      HORIZ. is 2.58pt from bar 1 and 8.71pt from bar 2; VERT. is 4.98pt
      from bar 2 and 25.14pt from bar 1. Each caption goes to its own
      bar by a 3.4x and 5.0x margin, and each row inherits its bar's.

      A bare axis word labels the NUMBERS beside it.
      <contractor folder>/Plan Set.pdf, engine page index 4 draws ONE bar
      serving both axes, with a label row on each side:
          401.8-407.0  VERTICAL
          ~408 -413.3  5 2.5 5           (dropped by 150-dpi OCR today)
          ~415 -421    [bar]
          426.2-432.0  30 0 15 30
          431.5-436.8  HORIZONTAL
      A bar rule cannot work here - both rows share the one drawn band,
      416.52-423.72 - but HORIZONTAL is 0.00pt from the lower row and
      18.24pt from the upper, VERTICAL 0.84pt from the upper and 19.20pt
      from the lower. Today OCR reads only the lower row, and it still
      gets HORIZONTAL by 19.20pt against 0.00pt.

    Both are exclusive assignments: a caption goes to its nearest target
    and is not available to any other, so an axis word cannot vote twice.
    Reach is 6 label-heights and the word's centre must lie in the
    target's x-window, so a "VERTICAL" in a note elsewhere on the sheet
    cannot veto a bar. A tie between the two axes returns VERT, which
    refuses the row rather than guessing.

    "" - no axis word in reach - is the ordinary single-bar sheet:
    nothing about the axis is asserted, so nothing is refused.
    """
    def claim(targets, want_captioned):
        """Axis of the marks that pick target `ri` as their nearest."""
        hits = []
        for axis, mx, my0, my1, cap in marks:
            if cap is not want_captioned:
                continue
            best, bg = None, None
            for i, t in enumerate(targets):
                if t is None:
                    continue
                x0, x1, h = boxes[i][0] - 30.0, boxes[i][1] + 30.0, \
                    max(boxes[i][3] - boxes[i][2], 2.0)
                if not (x0 <= mx <= x1):
                    continue
                g = max(t[0] - my1, my0 - t[1], 0.0)
                if g >= 6.0 * h:
                    continue
                if bg is None or g < bg:
                    best, bg = i, g
            if best == ri:
                hits.append((bg, axis))
        if not hits:
            return ""
        g0 = min(g for g, _a in hits)
        return "VERT" if any(a == "VERT" and g <= g0 + 1e-9
                             for g, a in hits) else "HORIZ"

    # A full caption line captions the drawn bar; fall back to the row's
    # own box on a page that draws no ticks (a caller with no page).
    return (claim([b if b else (x[2], x[3]) for b, x in zip(bands, boxes)],
                  True)
            or claim([(x[2], x[3]) for x in boxes], False))


def _snap_label(w, row_ticks: list) -> float | None:
    """The drawn tick a label marks, or None.

    The tick must fall within the label's own box (pad 1.2 pt): drafters
    place the label over its tick, but not all of them CENTRE it there.
    Res-A landscaping aligns the end labels by outer edge - the '0' box
    edge is 0.66 pt from its tick while the centre is 2.49 pt inside, and
    the '16' centre is 5.64 pt inside. Spanning centres instead of ticks
    read those bars 12.5-12.7% high (audit finding 16), which is +26.8%
    on every area and +42.8% on every volume.
    """
    bx0, bx1 = float(w[0]) - 1.2, float(w[2]) + 1.2
    cx = (float(w[0]) + float(w[2])) / 2
    best = None
    for x in row_ticks:
        if bx0 <= x <= bx1 and (best is None or abs(x - cx) < abs(best - cx)):
            best = x
    return best


def _zero_anchored_span(row: list) -> tuple | None:
    """(zero tick, far tick) for a label row sorted by x, or None.

    From each "0" label (leftmost first) walk right, then left, keeping
    labels while their values strictly increase: "10 | 0 | 5 10 20 40"
    yields 0..40, the pre-zero 10 is never visited. The walk also stops at
    a second bar's zero ("0 50 100  0 20 40" -> 0..100).
    """
    zeros = [i for i, (v, _x) in enumerate(row) if v == 0]
    for zi in zeros:
        for side in (row[zi + 1:], row[:zi][::-1]):
            last, best = 0.0, None
            for v, x in side:
                if v <= last:
                    break
                last, best = v, (v, x)
            if best is not None and abs(best[1] - row[zi][1]) > 4:
                return row[zi], best
    return None


def _mirror_zero(row: list, row_ticks: list) -> float | None:
    """Recover a zero OCR dropped, from the bar's own symmetry.

    Civil bars draw one unit of the scale LEFT of zero as a finer
    subdivision, so the first major value appears on BOTH sides at equal
    distance: "30 | 0 | 15 30 60 120" (grading set), "2 | 0 | 2 4" (stair set),
    "20 10 | 0 | 20 40" (water main 1). CONVENTION from those sheets and
    Res-A civil, five independent drafters. When the row has no zero but
    two equal labels, the zero is their midpoint - accepted ONLY if a
    drawn tick sits within 2 pt of it, so the geometry asserts the zero
    rather than the labels implying one. OCR drops exactly this '0' often
    enough to matter: every stair-set page, grading set p7/p10 (which today ship 18.0
    and 180.0 against drawn 30.0 and 300.0 because the zero-less fallback
    spans pre-zero label to far label).
    """
    if not row_ticks:
        return None
    zx = []
    for i in range(len(row)):
        for j in range(i + 1, len(row)):
            if row[i][0] == row[j][0] and row[i][0] > 0 \
                    and row[j][1] - row[i][1] > 8:
                mid = (row[i][1] + row[j][1]) / 2
                near = [x for x in row_ticks if abs(x - mid) <= 2.0]
                if near:
                    zx.append(min(near, key=lambda x: abs(x - mid)))
    if not zx or max(zx) - min(zx) > 2.0:
        return None            # none found, or two pairs disagree
    return sorted(zx)[len(zx) // 2]


def _ocr_words_loud(img, px_to_pt: float, min_conf: int = 35) -> tuple:
    """ocr_words, but an OCR-engine failure is LOUD and distinguishable.

    Returns (words, reason): reason is "" when OCR ran, "ocr-unavailable"
    when the engine itself cannot run (Tesseract not installed / not on
    PATH, pytesseract missing), "ocr-error" when it ran and failed. The
    old shape - `except Exception: pass` around the whole OCR block -
    returned the same silent None for "no scale on this sheet" and "no
    OCR on this machine"; two independent audits hit that in one night,
    one running 164 pages degraded with no warning, the other nearly
    signing off "no scale printed" when the truth was "no Tesseract on
    PATH". detect_scale forwards the reason as ScaleResult.source, and
    the first failure per process also warns.
    """
    global _OCR_UNAVAILABLE_WARNED
    try:
        from .text_detect import ocr_words
        return ocr_words(img, px_to_pt=px_to_pt, min_conf=min_conf), ""
    except Exception as e:
        down = (isinstance(e, ImportError)
                or type(e).__name__ == "TesseractNotFoundError")
        if not down:
            _log.warning("scale OCR failed on this page (%s: %s)",
                         type(e).__name__, e)
            return [], "ocr-error"
        if not _OCR_UNAVAILABLE_WARNED:
            _OCR_UNAVAILABLE_WARNED = True
            msg = ("OCR is unavailable (%s: %s) - scale detection is "
                   "running DEGRADED: pages whose scale is only printed "
                   "as raster/outlined text will read as 'no scale "
                   "found'. Install Tesseract / put it on PATH."
                   % (type(e).__name__, e))
            _log.warning(msg)
            warnings.warn(msg, RuntimeWarning, stacklevel=3)
        else:
            _log.warning("OCR unavailable (%s) - page scanned without OCR",
                         type(e).__name__)
        return [], "ocr-unavailable"


def _ocr_zoom(page, region, min_conf: int = 35, psm: int = 11) -> list:
    """Re-OCR one small region at high dpi; words in page display coords.

    The 150-dpi full-page pass reads the stair set's small bar labels only
    partially ('2' alone on its demolition sheet); a 600-dpi render of just
    the caption window reads the full '2 0 2 4' row. Cost is bounded by
    the region size and only paid when a caption head was already found.

    `psm` defaults to 11 so the caption-window rescue is unchanged;
    `graphic_scale_bar` passes 6, because a tick ladder is one printed line
    of digits rather than sparse text (see `text_detect.ocr_words`).
    """
    try:
        from .text_detect import ocr_words

        rc = pymupdf.Rect(region) & page.rect
        if rc.is_empty or rc.width < 8 or rc.height < 8:
            return []
        dpi = min(600.0, 6000.0 * 72.0 / max(rc.width, rc.height))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72),
                              clip=rc, colorspace="rgb")
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, 3)
        ws = ocr_words(img, px_to_pt=72.0 / dpi, min_conf=min_conf, psm=psm)
        return [(x0 + rc.x0, y0 + rc.y0, x1 + rc.x0, y1 + rc.y0, t)
                for x0, y0, x1, y1, t in ws]
    except Exception:
        return []


# OCR confusion immediately before an inch mark: the leading "1" of a
# stylised caption comes back as a bar/bracket ('SCALE: |"=20\'' measured on
# a residential construction set p2, 2026-09-01). Only the character glued to a
# quote mark is rewritten - a bare | elsewhere stays untouched.
_OCR_ONE_CONFUSION = re.compile(r"[|Il!{\[]\s*(?=[\"“”″])")

# Caption heads worth a second look when the full-page pass parsed nothing:
# the word SCALE (with or without punctuation) or the GRAPHIC of
# "GRAPHIC SCALE IN FEET".
_CAPTION_HEAD_OCR = re.compile(r"^(?:SCALE[:.]?|GRAPHIC)$", re.I)


def _ground_value_marked(text: str) -> bool:
    """Does the caption's GROUND value carry its own unit mark?

    OCR eats the '0\" tail of an architectural scale: Res-C ID-1.04
    prints SCALE: 1/4\" = 1'0\" and comes back 'SCALE: 1/4\" = 10',
    which _scan reads as a quarter inch to TEN FEET - 40 ft/in where the
    truth is 4, silently 10x wrong (measured 2026-09-01, three pages).
    An OCR-rescued caption whose last number has no mark within reach is
    therefore refused outright: a missing reading is recoverable by the
    operator, a wrong one reaches a bid. Either quote kind is accepted -
    this OCR routinely returns a foot mark as a double quote."""
    runs = list(re.finditer(r"\d+", text))
    if not runs:
        return False
    tail = text[runs[-1].end():runs[-1].end() + 3]
    return bool(re.match(r"\s*['’′\"“”″]", tail))


def _ocr_caption_rescue(page, words) -> list:
    """Re-read the caption WINDOWS when full-page OCR mangled the ratio.

    Measured failure this recovers (survey 2026-09-01, FABLE-002): on
    sheets whose text is vector outlines, the 150-dpi pass reads the
    caption only partially - storage site C.3.1 comes back 'SCALE: = 40'
    with the 1\" dropped, so _scan has nothing to match even though every
    word of 'SCALE: 1" = 40\'' is printed on the sheet. 296 of 728
    plan-like corpus pages return no reading, and the three
    vector-outline earthwork ground-truth sheets are all in that set.

    So: find the surviving caption HEADS in the full-page words, re-OCR a
    small window around each at high dpi (_ocr_zoom), normalise the
    one-glyph confusions, and _scan the reassembled rows. Cost is bounded
    (at most 8 windows, each ~400x100 pt) and only paid on pages that had
    no reading at all.
    """
    heads = []
    for w in words:
        if _CAPTION_HEAD_OCR.match(w[4].strip()):
            # one window per distinct location; two heads of the same
            # caption ("GRAPHIC" + "SCALE") collapse into one
            if any(abs(w[0] - h[0]) < 60 and abs(w[1] - h[1]) < 40
                   for h in heads):
                continue
            heads.append(w)
        if len(heads) >= 8:
            break
    out, seen = [], set()
    for h in heads:
        x0, y0 = h[0], h[1]
        # Three window shapes per head, because the window CONTENT steers
        # Tesseract's segmentation (measured on storage site C.3.1: a tight
        # single-row window reads 'SCALE: 1" 40" ='; a wider one drops the
        # 1" entirely). Tight row first, then wide, then the band BELOW the
        # head for bar-style captions whose ratio prints under the ruler.
        for region in ((x0 - 80, y0 - 18, x0 + 240, y0 + 30),
                       (x0 - 60, y0 - 25, x0 + 340, y0 + 75),
                       (x0 - 50, y0 + 25, x0 + 330, y0 + 95)):
            zw = _ocr_zoom(page, region, min_conf=30)
            if not zw:
                continue
            row = _row_text(zw)
            # High-dpi OCR of these fonts detaches the inch mark into its
            # own token ('SCALE: 1 " 40"'): glue a quote mark back onto
            # the digit it follows before scanning.
            row = re.sub(r"(\d)\s+(?=[\"“”″])", r"\1", row)
            row = _OCR_ONE_CONFUSION.sub("1", row)
            found = [c for c in _scan(row) if _ground_value_marked(c.text)]
            for c in found:
                key = round(c.ft_per_paper_inch, 4)
                if key in seen:
                    continue
                seen.add(key)
                # Where this reading came from, carried with it. These
                # candidates are never promoted, so the operator is the one
                # who decides - and a number with no provenance is not a
                # thing anyone can decide about. `at` is the caption HEAD
                # (the surviving "SCALE"/"GRAPHIC" word), not the window,
                # because the head is a real thing on the page and the
                # window is our own search box.
                c.origin = "ocr-caption-window"
                c.at = (h[0], h[1], h[2], h[3])
                out.append(c)
            if found:
                break            # this head's caption is read; next head
    return out


def _head_window(ws: list, h) -> list:
    """The words a caption head can own a bar among: within 90pt vertically
    and 320pt horizontally of the head's centre."""
    hy = (h[1] + h[3]) / 2
    hx = (h[0] + h[2]) / 2
    return [w for w in ws
            if abs(((w[1] + w[3]) / 2) - hy) < 90
            and abs(((w[0] + w[2]) / 2) - hx) < 320]


def _label_rows(cand: list) -> list:
    """Numeric tick labels grouped into rows of three or more, each entry
    (value, centre x, centre y, word)."""
    labs = []
    for w in cand:
        m = _TICK.match(str(w[4]).strip())
        if m:
            labs.append((float(m.group(1)),
                         (w[0] + w[2]) / 2, (w[1] + w[3]) / 2, w))
    # The labels of one bar share a row. Cluster on the vertical
    # gap between neighbours rather than fixed buckets: a "0"
    # glyph box centres ~0.7pt below its "10"/"40" neighbours,
    # and round(cy / 9) splits them whenever that straddles a
    # bucket edge (Res-A p4/p11).
    labs.sort(key=lambda t: t[2])
    rows: list = []
    for t in labs:
        if rows and t[2] - rows[-1][-1][2] < 4.5:
            rows[-1].append(t)
        else:
            rows.append([t])
    return [r for r in rows if len(r) >= 3]


def bar_tick_labels(words: list) -> set:
    """Every value printed as a tick label on a drawn bar's ladder: the
    numeric labels in rows of three or more inside a caption head's window
    (the same window and row test graphic_scale_bar reads from). Words
    only - no geometry - so it is exactly what get_text() had in reach when
    it emitted the page, which is what the join-artefact test asks."""
    ws = [w for w in words if str(w[4]).strip()]
    out: set = set()
    for h in _bar_heads(ws):
        for r in _label_rows(_head_window(ws, h)):
            out |= {round(t[0], 4) for t in r}
    return out


def _zoom_measure(page, window, psm: int, source: str, measure):
    """One high-dpi re-read of a bar window, through the same guards."""
    zws = [w for w in _ocr_zoom(page, window, psm=psm) if str(w[4]).strip()]
    if not zws:
        return None
    zrows = _label_rows(zws)
    return measure(zrows, zws, source) if zrows else None


def graphic_scale_bar(words: list, page=None,
                      ocr_retry: bool = False,
                      words_source: str = "text") -> ScaleCandidate | None:
    """Measure a drawn scale bar - the ruler with tick labels.

    Some sheets state no ratio anywhere; they print a bar labelled 0 / 50' /
    100' and expect you to measure it. That leaves quantities stuck in points
    with no way to price them, so read it directly: find the numeric tick
    labels sitting on one horizontal line near a caption head (see
    _BAR_HEAD_CORES) and divide feet by the paper distance spanned.

    The span is measured TICK to TICK: the ticks are geometry the
    drafter placed, the labels are typography placed by eye (audit
    finding 16 measured +12.5% from spanning label centres). Each
    endpoint label snaps to the tick inside its own box, and a row with
    no drawn tick in its band, or whose endpoints touch none, is refused
    - numbers with no drawn ruler under them are keynotes, not a bar.

    A tick span is measured, NOT exact, and the earlier comment here
    claiming otherwise oversold it. Two things put a tick reading off a
    round number: the drafter's own rounding when the bar was placed
    (a storage-site set p5 ships 40.0167 against its own printed `HORIZ.
    SCALE: 1" = 40'`, +0.042%), and `_snap_label` taking a neighbouring
    stroke when several sit inside one label box (vehicle shop Cu2.05 p0 ships
    19.9543 because it took a stroke at 338.73 where the regular tick
    series puts the end tick at 338.40, -0.229%). Both are well inside
    any useful tolerance and neither is a defect; the accurate claim is
    that tick spans are NEVER SNAPPED - they are reported as measured,
    so a residual like these is visible instead of being rounded away
    into a number the sheet does not draw. The measured residual
    distribution over the corpus is in the bar2 report.

    Only when the caller passes no `page` at all - the second-opinion
    callers in `_corroborate` - do label centres stand in, and only then
    does the engineering-standard snap list apply.

    Civil bars are often laid out "10 | 0 | 5 10 20 40": one unit of the
    scale is drawn to the LEFT of zero as a finer subdivision. When a "0"
    label is on the row, anchor there and walk outward while the labels
    keep increasing. Without a zero the row is accepted only if strictly
    increasing left to right (a zero-less ladder like "20 40 80"); a
    non-monotone zero-less row is a bar whose zero OCR dropped -
    _mirror_zero recovers it from the drawn geometry or the row is
    refused. Every label on the row must then agree with the measured
    rate (a misread label like a commercial set's '20'->'0' otherwise ships a
    5x error), or the row is refused.

    WHERE THE LABELS CAME FROM is recorded on the returned candidate as
    `bar_source`, because a bar only corroborates a caption when the two
    were read by DIFFERENT recognisers (SPEC 6.2 rule 1). `words_source`
    says what the caller handed in - "text" for `page_words()` glyphs,
    "ocr" for the 150-dpi psm-11 page pass - and the ladder actually used
    is stamped one of:
      native / native-ticks       label boxes from the PDF's own text
      psm6-zoom / psm6-zoom-ticks label boxes from `_ocr_zoom(..., psm=6)`
      ocr-zoom / ocr-zoom-ticks   label boxes from `_ocr_zoom(..., psm=11)`,
                                  the pre-stage-1b retry, kept as a last
                                  resort so no page loses a bar it had
      ocr / ocr-ticks             label boxes from the caller's own OCR
    A `*-ticks` reading measured its span on drawn tick geometry; the
    plain form spanned label centres (no page, so no ticks to snap to).
    An `ocr*` bar therefore corroborates nothing on a page whose caption
    is itself psm-11 OCR - same recogniser, same render, one mistake.

    THE ZOOM RUNS on three conditions, not one. It used to run only when
    `not rows`, which let a corrupt ladder suppress the independent pixel
    read: on a commercial set page index 3 the 150-dpi pass returns `10 0 4`
    (printed 0 10 20 40 - the 20 read as 0 and the 40 as 4), that
    satisfies "rows exist", the zoom is skipped and the corrupt row then
    fails the rate check, so the page returns nothing where the sheet
    draws a bar. It now runs when (a) no row was found, (b) rows were
    found and every one was refused by the guards, or (c) the rows are
    built from non-native words at all (`words_source != "text"`) - and a
    ladder that passes from the zoom is PREFERRED over one that passes
    from the caller's words, because it is the independent read.
    """
    ws = [w for w in words if str(w[4]).strip()]
    ticks = None

    def _measure(rows, near, source):
        """One head's label rows, through every guard. Returns the reading
        or None when the guards refuse all of them; `source` is where the
        rows' label boxes came from (see the docstring's bar_source list)."""
        nonlocal ticks
        if ticks is None:
            ticks = _page_ticks(page) if page is not None else []
        # Every row's label box and the drawn band its labels sit on.
        # _row_axis hands the axis captions out EXCLUSIVELY, so it needs
        # all the rows at once, not just the one in hand.
        boxes, bands, rowticks = [], [], []
        for r in rows:
            rw = [t[3] for t in r]
            boxes.append((min(float(w[0]) for w in rw),
                          max(float(w[2]) for w in rw),
                          min(float(w[1]) for w in rw),
                          max(float(w[3]) for w in rw)))
            xs, bd = _row_ticks(ticks, rw, near) if ticks else ([], None)
            rowticks.append(xs)
            bands.append(bd)
        marks = _axis_marks(near)
        for ri, row4 in sorted(enumerate(rows), key=lambda p: -len(p[1])):
            row_words = [t[3] for t in row4]
            rt, band = rowticks[ri], bands[ri]
            # No drawn tick anywhere in this row's band: nothing on the
            # page is a ruler here, so the row is just numbers. Believe a
            # bar only when the drafter drew one. Four inventions on real
            # construction documents came through the label-centre branch
            # with zero ticks - a fuel-stop set's A2.8 Equipment Schedule p0
            # and its ADD1 re-issue read 1" = 236.56' off the word SCALE
            # inside "SCALE OUNCE 12#-192 oz", spanning the QTY column,
            # the ITEM NO. column and the 3 of "PREP TABLE 3 FT"; an aviation
            # set p196 read 454.43' off spec 22 05 19's "Scale Range for
            # Domestic Cold-Water Piping: 0 to 150 deg F"; Orig Full Spec
            # Manual p56 read 4.92' off "Per Section 01 00 00". All four
            # returned None before the bar rewrite. The cost is one true
            # reading corpus-wide (see the report); a kitchen equipment
            # schedule asserting a drawing scale is the worse trade.
            #
            # Only when the caller gave us no page at all - _corroborate's
            # second-opinion callers - do label centres still stand in,
            # because then "no tick" cannot be distinguished from "never
            # looked". Those readings never become the page scale.
            if page is not None and not rt:
                continue
            # A profile's vertical-exaggeration bar is not the plan scale,
            # and `_row_axis` below decides which row is which. IT IS NOT
            # CALLED, deliberately. Gate 1 measured an ablation with it
            # disabled across all 6,724 corpus pages and got a ZERO diff -
            # it changes no page in either direction - while introducing a
            # rank-1 failure on a 0.81 pt margin: move only the two caption
            # LINES on water main 1 p297 into the ordinary caption-above-its-own-
            # bar layout (no tick, label or stroke touched) and VERT. lands
            # 5.24 pt from the HORIZONTAL band against 6.05 from its own,
            # refuses the correct 40 ft/in row, and the 10 ft/in row then
            # ships unasserted - a silent 4x under-scale where doing nothing
            # returns 40. The tie-break meant to force a refusal is per MARK,
            # not per TARGET, so a mis-claimed mark leaves the other row
            # unguarded. AGENT-BRIEF section 1.5: a margin thinner than the
            # noise is not a distinction. The profile problem is real - see
            # F126 - and needs a rule with measurable separation, not this.
            # `_row_axis` and `_axis_marks` are kept, unused, as the measured
            # starting point for that work.
            row = []
            snapped = set()
            for v, cx, _cy, w in row4:
                tx = _snap_label(w, rt) if rt else None
                if tx is not None:
                    snapped.add(round(tx, 2))
                    row.append((v, tx))
                else:
                    row.append((v, cx))
            row.sort(key=lambda t: t[1])
            if rt and not any(v == 0 for v, _x in row):
                zx = _mirror_zero(row, rt)
                if zx is not None:
                    snapped.add(round(zx, 2))
                    row.append((0.0, zx))
                    row.sort(key=lambda t: t[1])
            span = _zero_anchored_span(row)
            if span is None:
                vals = [v for v, _x in row]
                if vals != sorted(set(vals)):
                    continue        # zero-less and not increasing: refused
                span = (row[0], row[-1])
            (v0, x0), (v1, x1) = span
            dft, dpt = abs(v1 - v0), abs(x1 - x0)
            if dft <= 0 or dpt <= 4:
                continue
            # with drawn ticks by the row, both endpoints must sit on one
            if rt and not (round(x0, 2) in snapped
                           and round(x1, 2) in snapped):
                continue
            # every label the bar owns must agree with the measured rate
            lo, hi = min(x0, x1), max(x0, x1)
            ok = True
            for v, x in row:
                if not (lo - dpt - 2 <= x <= hi + 2):
                    continue        # beyond the bar: another bar's labels
                if v0 == 0:
                    pred = abs(x - x0) * v1 / dpt
                else:
                    pred = v0 + (x - x0) * (v1 - v0) / (x1 - x0)
                if abs(pred - v) > max(0.15 * v, 0.06 * max(v0, v1), 0.35):
                    ok = False
                    break
            if not ok:
                continue
            ft_per_inch = 72.0 * dft / dpt
            if not (1.0 <= ft_per_inch <= 600.0):
                continue
            how = "ticks"
            if not (rt and round(x0, 2) in snapped
                    and round(x1, 2) in snapped):
                how = "labels"
                # Engineering scales are round numbers; measuring label
                # centres lands a hair off (50.06). Snap only when the
                # reading is already within 2.5% of a standard, so a
                # genuinely odd bar is reported as measured rather than
                # forced onto a value it is not. A tick-measured span is
                # never snapped - it is not exact either (see the
                # docstring: 40.0167 on storage site p5, 19.9543 on vehicle shop
                # Cu2.05), but its residual is the drafter's own, and
                # reporting it as measured keeps that visible.
                for std in (10, 20, 30, 40, 50, 60, 80, 100, 200):
                    if abs(ft_per_inch - std) <= 0.025 * std:
                        ft_per_inch = float(std)
                        break
            return ScaleCandidate(
                text=f'graphic bar {v0:g}-{v1:g} ft over {dpt:.1f}pt '
                     f'(1" = {ft_per_inch:g}\', {how})',
                ft_per_paper_inch=ft_per_inch,
                near_scale_keyword=True,
                bar_source=source + ("-ticks" if how == "ticks" else ""))
        return None

    for h in _bar_heads(ws):
        hy = (h[1] + h[3]) / 2
        hx = (h[0] + h[2]) / 2
        near = _head_window(ws, h)
        rows = _label_rows(near)
        own = "native" if words_source == "text" else "ocr"
        cand = _measure(rows, near, own) if rows else None
        # The pixel zoom runs on (a) no rows, (b) rows that every guard
        # refused, or (c) rows that are not native text - the caller's own
        # recogniser cannot be the second opinion on its own reading. Its
        # ladder wins when it passes: it is the independent one.
        if page is not None and ocr_retry and (
                cand is None or words_source != "text"):
            win = (hx - 330, hy - 95, hx + 330, hy + 95)
            zcand = _zoom_measure(page, win, 6, "psm6-zoom", _measure)
            if zcand is not None:
                return zcand
            if cand is None:
                # Nothing from the caller's words and nothing at psm 6.
                # The psm-11 zoom is what this page had BEFORE this stage,
                # and psm 6 is better on average, not everywhere: measured
                # over the 6,460-page OCR sweep, a mixed-use block set
                # p44 (`0 20' 40'` under SCALE IN FEET, truth 20 ft/in) is
                # read by the psm-11 zoom and not by the psm-6 one. Keeping
                # it as a last resort makes this stage strictly additive on
                # the bar path - it cannot cost a page a reading it had.
                # It is NOT independent of a psm-11 caption (SPEC 6.2 rule
                # 1 lists native text, the psm-6 zoom, and ticks with an
                # endpoint from those - not this), and `ocr-zoom` says so,
                # so it can be a page's own bar reading and can never
                # corroborate a caption the same recogniser produced.
                zcand = _zoom_measure(page, win, 11, "ocr-zoom", _measure)
                if zcand is not None:
                    return zcand
        if cand is not None:
            return cand
    return None


# How far apart two words can sit and still read as one line. The
# row-join used to weld text from opposite ends of a 42-inch sheet into one
# "line" - which is how both keyword misreads were manufactured. On Res-A
# civil p1 the bar caption "GRAPHIC SCALE" (x=810..897pt) and an unrelated
# 5'-2" dimension callout (x=1341pt) shared a row and joined as
# `SCALE 5'-2"`. This cap now decides row MEMBERSHIP in _row_text rather
# than marking a gap after the fact, so a hole this wide starts another row
# instead of being papered over with a "|"; the measurements behind the
# number are unchanged.
#
# Keyed to glyph height, because OCR word boxes and title text are sized
# very differently from body text. Measured across the corpus:
#   widest gap INSIDE a real caption   23.5pt at 9.1pt glyphs  = 2.6x
#     (C9.0 OCR "SCALE: 1" 20'" - the hole where OCR dropped the '=')
#   narrowest FALSE weld              424.8pt at 12.6pt glyphs = 34x
#     (Res-A civil p1 "1 inch = 20 ft." -> "STBK", and 444pt for the 5'-2")
# 6x sits between the two with 2.3x headroom over the widest real caption
# gap. An earlier 2.5x missed C9.0's caption by 0.7pt and cost it its scale.
_ROW_GAP_MIN = 24.0      # pt, floor for small text
_ROW_GAP_HEIGHTS = 6.0   # x the taller of the two neighbouring words


# Whether two words sit on the same printed line is a PROXIMITY question -
# do these two boxes touch the same strip of paper - and it used to be
# answered with a fixed grid, `round(y0 / 8)`. A grid answers a different
# question. Two words 1.5pt apart land in different 8pt bins whenever a bin
# edge falls between them, and the caption is then emitted with a hole in
# it. Measured on Res-B civil engine page index 8 (printed sheet 9): '20'
# sits at y0=1245.47 (bin 156) while '1', 'inch', '=' and 'ft.' sit at
# y0=1243.93 (bin 155), so the join emitted `1 inch = ft.` and the '20'
# surfaced 40 words later. Res-C engine page indices 11 and 12 lose their
# '=' to the same edge, 1.9pt low.
#
# Replacing the grid with a vertical proximity test alone is NOT enough, and
# the corpus says so. A 42-inch sheet carries several columns, so two
# unrelated printed lines two feet apart routinely overlap in y - and any
# rule that groups on y alone puts them in one row, where the second line's
# box then defines the band and evicts the first line's own '='. Measured:
# a "keep the band all the words share" walk drops `1 inch = 10 ft.` on
# C4 GRADING PLAN p0 (welded to `PAID BY <CITY NAME> PUBLIC WORKS`,
# 250pt to its left, whose box ends 0.0pt above the '=') and on storage site
# p3, and C4 p0 then falls through to the graphic bar and reports
# 6.008 ft/in against the sheet's own 10 - a wrong number where there was a
# right one. Nearest-neighbour walks fail the other way: "new row when the
# y gap exceeds half a glyph height" chains rows up to 448pt tall and
# destroys the Res-A civil p1 and Res-B civil p2 captions.
#
# So the row is assembled in BOTH dimensions, which is what "the same
# printed line" actually means: sweep the words left to right, and a word
# joins the open line it overlaps most in y, provided that line's right edge
# is within the column gap above. Both tolerances come from the geometry -
# the boxes' own overlap, and the height-keyed gap that was already measured
# for the "|" guard. Nothing is bucketed and no new constant is introduced.
#
# Scored against mupdf's own line ids over the 208-page client-folder corpus
# (60,110 printed lines that are horizontal in display space), printed lines
# split across more than one row: 8pt grid 948 (1.577%), y-only band 716
# (1.191%), this 594 (0.988%); worst number of printed lines welded into one
# row: 90 / 37 / 34. Grouping cost for all 208 pages: 0.07s / 0.14s / 3.6s.


def _row_text(words: list) -> str:
    """Join word boxes into printed lines: rows top to bottom, words left to right.

    A "|" separates two rows that are NOT one line continuing into the next:
    same band, different column (the Res-A civil p1 case - the bar caption
    "GRAPHIC SCALE" at x=810..897pt and a 5'-2" dimension callout at
    x=1341pt), or any other pair that is not stacked-and-aligned. "|" is not
    a scale separator and \\s* cannot cross it, so no pattern matches over a
    break, while the 30-character keyword lookback in _scan still sees a
    caption sitting on the near side of one.

    The wrap into the line printed directly BELOW is left joined, so a
    caption whose keyword and ratio are on two lines still reads, and
    _PAT_KEYWORD_PRE still recovers OCR that reports one ratio-first
    ('1" 10' SCALE:' on the C4.x grading plans).
    """
    rows: list[list] = []
    live: list[list] = []          # [band_lo, band_hi, x_right, h_max, words]
    for w in sorted(words, key=lambda w: float(w[0])):
        if not str(w[4]).strip():
            continue
        x0, y0 = float(w[0]), float(w[1])
        x1 = max(float(w[2]), x0)
        y1 = max(float(w[3]), y0 + 1.0)   # a degenerate box still owns a band
        h = y1 - y0
        best, best_ov, still = None, 0.0, []
        for r in live:
            # Out of reach to the right: this word cannot belong to that
            # line, and neither can any later one (the sweep only moves
            # right), so drop it from the open set.
            if x0 - r[2] > max(_ROW_GAP_MIN, _ROW_GAP_HEIGHTS * max(r[3], h)):
                continue
            still.append(r)
            ov = min(r[1], y1) - max(r[0], y0)
            if ov > best_ov:
                best, best_ov = r, ov
        live = still
        if best is None:
            best = [y0, y1, x1, h, []]
            live.append(best)
            rows.append(best)
        else:
            best[0] = max(best[0], y0)     # the band the line's words share
            best[1] = min(best[1], y1)
            best[2] = max(best[2], x1)
            best[3] = max(best[3], h)
        best[4].append(w)

    def extent(r):
        ws = r[4]
        return (min(float(w[1]) for w in ws),
                max(max(float(w[3]), float(w[1]) + 1.0) for w in ws),
                min(float(w[0]) for w in ws),
                max(float(w[2]) for w in ws))

    out: list[str] = []
    prev = None
    for r in sorted(rows, key=lambda r: (min(float(w[1]) for w in r[4]),
                                         min(float(w[0]) for w in r[4]))):
        lo, hi, xa, xb = extent(r)
        if prev is not None:
            below = min(prev[1], hi) <= max(prev[0], lo)     # stacked, not beside
            under = min(prev[3], xb) > max(prev[2], xa)      # same column
            if not (below and under):
                out.append("|")
        out.extend(str(w[4]) for w in r[4])
        prev = (lo, hi, xa, xb)
    return " ".join(out)


# Two independent readings within this much of each other corroborate. The
# bar is read off tick geometry and its labels, so a percent or two of slack
# is normal; the errors this catches were 3% (a welded pipe callout) up to
# 387% (a dimension read as a caption), so the threshold is not delicate.
BAR_AGREE_PCT = 2.0


def _corroborate(res: ScaleResult, words: list, page=None) -> ScaleResult:
    """Attach the graphic bar's opinion to an already-chosen text reading.

    Purely additive - `res.best` is never replaced. If the two disagree the
    UI says so and the estimator calibrates, which is the whole point: every
    one of F92's 14 wrong pages reached the screen as a confident green chip.

    Callers that can pass `page` should: it lets the bar measure from the
    drawn tick strokes instead of label centres, which on edge-aligned-label
    drafters (Res-A landscaping) reads up to 12.7% truer.
    """
    if not res.best:
        return res
    try:
        bar = graphic_scale_bar(words, page)
    except Exception:
        return res
    if not bar or not bar.ft_per_paper_inch:
        return res
    res.bar_ft_per_paper_inch = bar.ft_per_paper_inch
    d = res.delta_pct
    res.agrees = (d is not None and d <= BAR_AGREE_PCT)
    # WHICH recogniser read the ladder travels with the corroboration. A
    # second opinion is only a second opinion if it came from somewhere
    # else, and a reader cannot check that after the fact from a number.
    if bar.bar_source:
        res.pick["bar_source"] = bar.bar_source
    return res


def _independent_bar(page, native_words, ocr_ws):
    """A bar reading the OCR caption did not produce itself (SPEC 6.2 rule 1).

    The caption in hand came from the 150-dpi psm-11 page pass. A ladder
    that same pass read is NOT a second opinion on it: one recogniser, one
    render, one mistake, and the rate guard cannot see it because the guard
    derives its rate from those same labels. Independent means the labels
    came from (i) the PDF's own text, (ii) the psm-6 pixel zoom, or (iii)
    drawn tick geometry with an endpoint label from (i)/(ii) - which is
    what a `native*` or `psm6-zoom*` bar_source says.

    Native words are tried first and cost nothing when the page has no
    native caption head (the outline sheets this matters on have none).
    """
    for ws, src, retry in ((native_words or [], "text", False),
                           (ocr_ws or [], "ocr", True)):
        if not ws:
            continue
        try:
            bar = graphic_scale_bar(ws, page, ocr_retry=retry,
                                    words_source=src)
        except Exception:
            continue
        if not bar or not bar.ft_per_paper_inch:
            continue
        if (bar.bar_source or "").startswith(("native", "psm6-zoom")):
            return bar
    return None


# The origin stamped on a get_text() reading refused as a join artefact:
# its number was read across a line break from its `1"=` AND is one of the
# drawn bar's own tick labels (see read_text_layer). Kept in the result so
# the operator can see what the text layer produced and why it was not used.
ORIGIN_JOIN_ARTEFACT = "text-linebreak-join"


def read_text_layer(text: str, words: list | None, page=None,
                    linework=None, corroborate: bool = True,
                    nts: bool | None = None) -> ScaleResult | None:
    """The text-layer reading: get_text() first, then the same words
    re-assembled by printed row, then the drawn bar off the tick labels.
    None when the text layer yields nothing (the caller goes on to OCR).

    Words-level on purpose - `page` is only used for ranking geometry and
    the bar's tick strokes - so a frozen page can be replayed without the
    PDF (tests/test_scale_bar_contradiction.py).

    THE JOIN-ARTEFACT RULE (CLAUDE.md section 5, "what you extracted is not
    what is printed"). get_text() is a reading ORDER, not a printed line:
    a caption whose number is its own text object welds, across "\n", to
    whatever get_text() emits next. When that next thing is the drawn bar's
    tick ladder the weld is `1"=` + the ladder's last label - 90 on a
    sheet printing 1"=30' (public-ceqa sundale-road-30366 engine page
    index 1) and 180 on one printing 1"=60' (harbison-canyon-21316 index
    0): 3x on every length, 9x on every area and volume, delivered as an
    uncontested "single" that _corroborate could only annotate. Both
    conditions are structural and both are required:

      * `wrapped`  - the number sits on a different get_text() line from
                     its paper term (a caption's two sides never wrap; a
                     block that prints them as separate attributes is
                     exactly the case that welds); and
      * the value is one of the bar's tick labels on THIS page's words
                     (`bar_tick_labels`) - so a caption that merely
                     happens to wrap keeps reading.

    Such a candidate is not a caption reading at all, so it leaves the
    ranking and the page falls through to the row-assembled text - which
    joins on geometry and reads `1"= 30'` off the same words - or to the
    bar, or abstains. On a promoted result it stays in `refused`, stamped
    ORIGIN_JOIN_ARTEFACT, so the refusal is visible and never a silent
    drop - but NOT in `candidates`, which the UI reads as the sheet's
    printed scales (an abstention, having nothing else, carries the
    artefact as its only candidate so the suggestions panel can show it).
    """
    if nts is None:
        nts = bool(_NTS.search(text))
    cands = _scan(text)
    joins: list[ScaleCandidate] = []
    if cands and words is not None and any(c.wrapped for c in cands):
        try:
            ticks = bar_tick_labels(words)
        except Exception:
            ticks = set()
        for c in cands:
            if c.wrapped and round(c.ft_per_paper_inch, 4) in ticks:
                c.origin = ORIGIN_JOIN_ARTEFACT
                joins.append(c)
        cands = [c for c in cands if c not in joins]

    def with_joins(res: ScaleResult) -> ScaleResult:
        # A refused join whose value the intact readings ALSO carry adds
        # nothing but a duplicate: the weld happened to land on the right
        # number (the mixed-use block set's engine page index 38 welds
        # `1" =` to a bar whose ladder prints the sheet's own 10, and the
        # row-assembled text reads `1" = 10'` regardless). Only a join
        # that stands for a value nothing else read is worth showing.
        have = {round(c.ft_per_paper_inch, 4) for c in res.candidates}
        extra = [c for c in joins
                 if round(c.ft_per_paper_inch, 4) not in have]
        if extra:
            res.refused = list(res.refused) + extra
        return res

    if cands:
        best, pick = _rank(cands, page, words, linework)
        res = ScaleResult(best, cands, nts, "text", pick=pick)
        if corroborate and words is not None:
            res = _corroborate(res, words)
        return with_joins(res)
    if words is None:
        return None
    try:
        # Same text layer, re-assembled by row. AutoCAD scale-bar blocks keep
        # the ratio's number as its own attribute text, so get_text() emits
        # "1 inch =         ft." and prints the "10" elsewhere; sorting the
        # words by (row, x) puts it back: "1 inch = 10 ft.".
        cands = _scan(_row_text(words))
        if cands:
            best, pick = _rank(cands, page, words, linework)
            res = ScaleResult(best, cands, nts, "text", pick=pick)
            return with_joins(_corroborate(res, words) if corroborate
                              else res)

        # No stated ratio: try the drawn GRAPHIC SCALE bar off the text layer
        # before paying for OCR. Its tick labels ("0", "50'", "100'") are
        # real text on most CAD sheets, and OCR mangles them ("100'" -> "00").
        bar = graphic_scale_bar(words, page)
        if bar:
            return with_joins(ScaleResult(
                bar, [bar], nts, "graphic-bar",
                pick={"why": "bar", "bar_source": bar.bar_source}))
    except Exception:
        pass
    if joins:
        # The text layer produced only a join artefact and no row-assembled
        # caption or readable bar backs a value: abstain with the artefact
        # on view rather than fall to OCR as if nothing had been read.
        return ScaleResult(None, joins, nts, "text",
                           pick={"why": "abstained-join",
                                 "reason": ("the only text-layer reading "
                                            "welds a caption to a bar tick "
                                            "label across a line break"),
                                 "values": sorted({round(c.ft_per_paper_inch, 4)
                                                   for c in joins})})
    return None


def _ocr_promotion(best, cands, nts, pick, page, native_words,
                   words) -> ScaleResult:
    """The OCR caption path's promotion decision (SPEC 7.5).

    An OCR caption is what a lossy recogniser HAPPENED to recover, and no
    filter over the recovered text can see what it dropped - the evidence
    that would refute the survivor is exactly the evidence that failed to
    arrive (CLAUDE.md section 5, fourth shape). Measured on this branch,
    2026-09-02, with the bar read at psm 6:

      A commercial p10 promotes 10 off a DETAIL 1 caption while the sheet's
        own bar draws `0 10 20 40` under `1" = 20'` - 2x on length, 4x on
        area.
      A storage-site p1 promotes 1.0 off a detail's `1" = 1'-0"` while
        the site plan's bar draws `0 40' 80'` under `SCALE: 1" = 40'` -
        40x low.
      The storage-site p3 promotes 10 where the sheet prints
        `SCALE: 1" = 40'` - 4x low.

    So a caption an independent bar CONTRADICTS is refused: `best` is None,
    the candidates stay on view, and `pick["values_refused"]` names the
    value that was not promoted. An abstained page is not a silent one -
    `to_dict` serialises it with `abstained` true, `detect_page` routes it
    to `scale_suggestions`, and accepting the value there is one click,
    recorded as a REVIEWER decision (tests/test_scale_suggestions.py).

    WHERE NO INDEPENDENT BAR EXISTS AT ALL, the reading is kept and stamped
    `pick["corroborated"] = False`, and this is a DELIBERATE NARROWING of
    SPEC 7.5, which asks for an abstention there too. The reason is
    measured, not preferred. Over the whole corpus - `detect_scale` with
    OCR on all 6,460 pages whose text layer reads nothing, 2026-09-02 -
    SPEC 7.5 as written turns 83 pages into abstentions: 6 where a bar
    contradicts the caption, and 77 where no bar could be read at all. Of
    those 77, five were checked against 400-dpi crops of the sheets and
    FOUR were already right:
      grading set p8 `1 inch = 30 ft.` under a 15/30/60/120 ladder - right
      fuel stop E1.1 `1/8"=1'-0"` (8 ft/in), no bar printed at all - right
      commercial p9  SECTION A-A `HORIZONTAL SCALE: 1" = 10'` - right
      commercial p13 `GRAPHIC SCALE: 1"=50'` over a 50/0/50/100/150 bar
                     whose checkerboard draws no stroke `_page_ticks`
                     accepts, so the tick guard refuses it - right
      storage p3     promotes 10 where the sheet prints `1" = 40'` - WRONG
    Abstaining on all 77 to catch that one costs 13 correct readings per
    wrong one removed. The refusal ON CONTRADICTION removes all 6 wrong
    ones and costs nothing.

    WHAT THIS LEAVES OPEN, stated because it is the real hole: a caption
    that is wrong and has no readable bar to refute it still ships, and
    Storage p3 is exactly that page. `corroborated: False` discloses it
    and a disclosure is weaker than an abstention (CLAUDE.md section 5).
    Closing it needs a reader that recovers the captions OCR drops - the
    outline lanes, stages 3-4 - not a blanket refusal of the OCR path.

    THE TEXT PATH IS NOT TOUCHED. A native caption's promotion rules are
    unchanged in this stage; only `source == "ocr"` readings come here.
    """
    if best is None:
        return ScaleResult(best, cands, nts, "ocr", pick=pick)
    bar = _independent_bar(page, native_words, words)
    d = None
    if bar is not None and bar.ft_per_paper_inch:
        d = (abs(best.ft_per_paper_inch - bar.ft_per_paper_inch)
             / bar.ft_per_paper_inch * 100.0)
    if bar is None:
        # nothing independent to check against - kept, and said so
        pick = dict(pick)
        pick["corroborated"] = False
        pick["bar_source"] = None
        return ScaleResult(best, cands, nts, "ocr", pick=pick)
    if d is not None and d <= BAR_AGREE_PCT:
        pick = dict(pick)
        pick["bar_source"] = bar.bar_source
        pick["corroborated"] = True
        out = ScaleResult(best, cands, nts, "ocr", pick=pick)
        out.bar_ft_per_paper_inch = bar.ft_per_paper_inch
        out.agrees = True
        return out
    out = ScaleResult(
        None, cands, nts, "ocr",
        pick={"why": "abstained-ocr-single",
              "reason": ("a bar read independently of this OCR caption "
                         "contradicts it"),
              "values_refused": sorted(
                  {round(c.ft_per_paper_inch, 4) for c in cands}),
              "bar_source": None if bar is None else bar.bar_source})
    if bar is not None:
        out.bar_ft_per_paper_inch = bar.ft_per_paper_inch
    return out


def detect_scale(page: pymupdf.Page, ocr_if_needed: bool = True,
                 corroborate: bool = True, linework=None) -> ScaleResult:
    """Read the sheet's scale. `linework` - the page's already-built chains,
    when the caller has them - replaces the raw-geometry proxy that ranks
    several printed scales by what each governs (see _rank); it never
    changes which captions are found."""
    text = page.get_text()
    nts = bool(_NTS.search(text))
    try:
        from .extract import page_words
        words = page_words(page)
    except Exception:
        words = None
    native_words = words
    res = read_text_layer(text, words, page, linework, corroborate, nts)
    if res is not None and res.best is not None:
        return res
    # A text-layer ABSTENTION (a join artefact and nothing else) is held,
    # not returned: OCR still gets its turn on the picture, and the held
    # abstention is what comes back if OCR reads nothing either - never
    # the bare "nothing printed" result, which would hide what was read.
    held = res
    if held is not None and not ocr_if_needed:
        return held

    if ocr_if_needed:
        try:
            from .text_detect import ocr_words

            dpi = 150
            pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72),
                                  colorspace="rgb")
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, 3)
            words, ocr_down = _ocr_words_loud(img, px_to_pt=72.0 / dpi,
                                              min_conf=35)
            ocr_text = _row_text(words)
            cands = _scan(ocr_text)
            # No caption came back: the drawn bar (measured off the page's
            # own tick strokes, with a high-dpi re-read of the caption
            # window when the full-page pass dropped the labels) is the
            # remaining source. This block returns on EVERY path so the
            # legacy pageless bar call below it never runs - its
            # label-centre measurement would resurrect exactly the
            # readings the tick guards refuse (a commercial set p3's 4.43).
            # The legacy lines are left textually untouched because
            # feat/choose edits them; whichever lands second deletes them.
            if not cands:
                n2 = nts or bool(_NTS.search(ocr_text))
                bar = graphic_scale_bar(words, page, ocr_retry=True,
                                        words_source="ocr")
                if bar:
                    return ScaleResult(
                        bar, [bar], n2, "graphic-bar",
                        pick={"why": "bar", "bar_source": bar.bar_source})
                # Caption-window rescue, strictly additive: it runs only
                # on pages every path above found NOTHING on, so no page
                # that reads today can change its reading.
                rcands = _ocr_caption_rescue(page, words)
                if rcands:
                    # Rescued candidates NEVER become best, even a lone
                    # value. Measured the same evening this was built,
                    # promotion produced two confident wrong scales by two
                    # different mechanisms: OCR ate the '0" of Res-C's
                    # 1/4" = 1'0" (read 40 ft/in, truth 4 - the unit-mark
                    # filter now refuses that shape), and on storage
                    # C.3.5 the only caption whose OCR survived was the
                    # KEY MAP's 1"=300' while the sheet's own HORIZ
                    # 1"=40' / VERT 1"=10' did not scan - a "single"
                    # value that is single only by OCR sampling bias,
                    # 7.5x wrong. There is no honest ranking over
                    # captions we may not even have all of, so the
                    # reading abstains and the candidates stay visible
                    # (CLAUDE.md section 5). Upgrading a candidate to
                    # best on independent bar-geometry agreement is the
                    # named follow-up.
                    values = sorted({round(c.ft_per_paper_inch, 4)
                                     for c in rcands})
                    return ScaleResult(
                        None, rcands, n2, "ocr",
                        pick={"why": "abstained-ocr",
                              "reason": ("OCR-rescued captions are "
                                         "candidates only, never a pick"),
                              "values": values})
                # "" = the sheet printed nothing we recognise;
                # "ocr-unavailable"/"ocr-error" = the page was never
                # actually checked (see _ocr_words_loud)
                return held or ScaleResult(None, [], n2, ocr_down)
            nts = nts or bool(_NTS.search(ocr_text))
            if cands:
                best, pick = _rank(cands, page, words, linework)
                return _ocr_promotion(best, cands, nts, pick, page,
                                      native_words, words)
            bar = graphic_scale_bar(words)
            if bar:
                return ScaleResult(bar, [bar], nts, "graphic-bar",
                                   pick={"why": "bar"})
        except Exception:
            pass

    return held or ScaleResult(None, [], nts, "")
