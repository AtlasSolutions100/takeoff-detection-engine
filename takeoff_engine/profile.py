"""Profile-view detection: find the graphs on a sheet and calibrate their axes.

A PROFILE is a graph drawn on a construction sheet - distance along a route
(stationing) on X, elevation on Y, with the vertical deliberately exaggerated
so grade changes are readable. (CONVENTION: general civil practice for
plan-and-profile drawings. Corroborated on this corpus by water-main
set 2, p2, which prints "NOTE: PROFILE VERTICALLY EXAGGERATED 3:1",
and by Belle Farms ROAD GRADING 1 of 2, whose caption states both scales.)

There is NO linear construction quantity inside a profile. Its grid, its
ground line and its grade line are a picture of elevations, not lengths of
anything that gets built. On Belle Farms ROAD GRADING 1 of 2 engine page
index 0 the engine currently emits 58,896.7 pt of profile as 49,081 ft of
takeoff - 17.2% of that sheet. On Loves Lane C9.1 it is 60.7%.

--------------------------------------------------------------------------
WHAT THIS MODULE DOES AND DOES NOT DO
--------------------------------------------------------------------------
It locates profile regions and reports their axis calibration, with the
provenance of every number. It is a pure function of the page: it imports
only `takeoff_engine.extract`, mutates nothing, and is wired to nothing.
`detect_page` does not call it; see WIRING at the bottom of this file for
what that would take.

It does NOT compute a cut/fill volume, and must not be extended to.
A profile is a CENTRELINE. Cubic yards additionally need the typical
cross-section (roadway width, side-slope ratio, subgrade depth) which lives
on a different sheet, and a shrink/swell factor which is usually in a soils
report and often not in the plan set at all. A volume produced from a
centreline alone would be a confident wrong number with no visible symptom.

--------------------------------------------------------------------------
WHY THREE BRANCHES
--------------------------------------------------------------------------
Measured over 706 corpus PDFs / 6,724 pages on 2026-08-23
(out/verify/2026-08-22/profile-survey/SURVEY.md), 57 pages in 21 documents
carry a profile. No single signal reaches them all:

  LAYER   at least 42 pages draw on a CAD layer named *PROF*/*PROFILE*.
          Unambiguous where present - and it is the ONLY signal on the 28
          pages whose drawing text was converted to outlines or rasterised
          (all 10 Loves Lane sheets, a mixed-use set p3-p17 and p26, a
          residential set p3/p4). But 0.0% of water-main set 1's profile-box length
          carries any layer at all, and 1.1% of Belle Farms 1of2's.
          Read layers from extract_strokes, NOT from doc.get_ocgs(): the
          Res-A civil's OCG dictionary declares no PROF layer while its page
          streams draw 122,400.2 pt on three of them.
  LABELS  24 pages carry both a station-label axis and an elevation-label
          axis. This is the only branch that yields a SCALE.
  CAPTION 35 pages print an explicit HORIZONTAL and VERTICAL scale pair.
          Used as a cross-check, never as the primary: it is stale on Belle
          Farms 2 of 2 (says 1"=80'/1"=20', the labels measure 59.99/15.00)
          and 2x out on the three half-size 17x11 documents, where the
          labels describe the PDF and the caption describes a full-size
          print that this PDF is not.

Ranking is explicit and every result says which branch fired
(`ProfileRegion.source`), per CLAUDE.md sec.5 "first-writer-wins discards
the right answer".

--------------------------------------------------------------------------
WHY IT ABSTAINS
--------------------------------------------------------------------------
Two things on a PLAN look exactly like a profile axis and are not: contour
labels running down a slope, and stationing along a curved alignment. Both
appear on water-main set 1 p299, whose upper drawing is a plan.

Measured margin over 110 label series on the 57 pages:

    genuine axis series (105):  max residual  0.842 ft, max (1-r2) 1.35e-5
    impostors             (5):  min residual  2.593 ft, min (1-r2) 2.33e-3

3.1x on residual, 173x on (1-r2). `MAX_RESID_FT` sits at the genuine end of
that band and `ABSTAIN_RESID_FT` at the impostor end; a series that lands
between them is returned with `abstain=True` and no scale, rather than being
guessed either way.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

import numpy as np

from .extract import StrokeUnit, page_words

# --- station / elevation label grammar ------------------------------------
# A station: 0+00, 12+39, 100+25, 15+56.97. The lookarounds stop 1+2 and stop
# a match inside a longer number.
_STA = re.compile(r"^(\d{1,3})\+(\d{2}(?:\.\d+)?)$")
# An elevation axis label is a bare 3-5 digit integer. Decimals are DATA, not
# axis labels: every axis series in the corpus is whole-numbered.
_INT = re.compile(r"^(\d{3,5})$")

_IN = r"""(?:["”″]|(?:inch(?:es)?|in)\.?(?![a-z]))"""
_FT = r"""(?:['’′]|(?:feet|ft)\.?(?![a-z]))"""
_RAT = rf"""(\d+(?:\.\d+)?)\s*{_IN}\s*(?:[=:\-]\s*){{1,2}}(\d+(?:\.\d+)?)\s*{_FT}?"""
# Both word orders seen in the corpus, and both spellings of HORZ:
#   "HORIZONTAL SCALE: 1" = 20'"   (Loves Lane, a mixed-use set, a river set)
#   "SCALE: HORZ. 1"=20'"          (Res-A / Res-B)
#   "PROFILE 1"=80' (HOR), 1"=20' (VERT)"   (Belle Farms)
_H_FIRST = re.compile(rf"(?i)HOR(?:IZ|Z)?[A-Z]*\.?\s*(?:SCALE)?\s*[:=]?\s*{_RAT}", re.X)
_V_FIRST = re.compile(rf"(?i)VER(?:T)?[A-Z]*\.?\s*(?:SCALE)?\s*[:=]?\s*{_RAT}", re.X)
_H_LAST = re.compile(rf"(?i){_RAT}\s*[(\[]?\s*HOR", re.X)
_V_LAST = re.compile(rf"(?i){_RAT}\s*[(\[]?\s*VER", re.X)

# CAD layer tokens that name a profile view. Same vocabulary as
# earthwork._NON_PLAN_LAYER, deliberately: it was read off the drafters' own
# layer names and it matches every profile layer in the corpus
# (C-PROFILE-EG/FG, O-PROF-VIEW-*, C-ROAD-PROF-*, PNP-PROF-*, U-*-PROFILE,
# OBJ_PROFILE, Stream-CT-PROF-FGC).
_PROF_LAYER = re.compile(r"(?i)(?:^|[-_|\s])(PROF|PROFILE)(?:[-_|\s]|$)")

# --- tuning constants, each with the measurement behind it -----------------
# Sort-and-walk gap tolerances. A label row is one text line; 6.0 pt is far
# under the smallest inter-row gap seen (storage site p11 labels the same box at
# top and foot, 585 pt apart) and over the largest within-row baseline wobble
# measured (0.4 pt on water-main set 1 p299's plan stationing).
ROW_TOL_PT = 6.0
# Elevation labels sit in one column. 8.0 pt covers the drift of a
# right-aligned column between 3- and 4-digit values.
COL_TOL_PT = 8.0
MIN_LABELS = 4          # 4 is the smallest genuine series in the corpus
                        # (Res-A p7/p10/p11 elevation columns, n=4)
MIN_SPAN_PT = 200.0     # smallest genuine station row span: 516 pt
                        # (a river set p3). 200 leaves headroom without
                        # admitting a 3-label callout cluster.
MIN_COL_SPAN_PT = 100.0
# Constant-step test for an elevation column. Every genuine column in the
# corpus is exactly constant; 2% admits rounding in the label centres.
STEP_TOL = 0.02
# See "WHY IT ABSTAINS". Genuine worst 0.842 ft; best impostor 2.593 ft.
MAX_RESID_FT = 1.0
ABSTAIN_RESID_FT = 2.5


@dataclass
class AxisFit:
    """A label series fitted to a straight line, with its own error bars."""

    kind: str                  # "station" | "elevation"
    n: int
    labels: list[str]
    pos0: float                # page coord ALONG the axis, first label
    pos1: float                # page coord ALONG the axis, last label
    across: float              # page coord ACROSS it: a station row's y,
                               # an elevation column's x
    val0: float                # ft
    val1: float
    step_ft: float             # 0.0 when the labels are not evenly stepped
    ft_per_pt: float           # signed: negative Y = elevation up the page
    ft_per_paper_inch: float   # abs(ft_per_pt) * 72
    max_resid_ft: float
    r2: float
    left_to_right: bool = True  # station series only; False on Res-A p9

    @property
    def trustworthy(self) -> bool:
        return self.max_resid_ft <= MAX_RESID_FT

    @property
    def abstain(self) -> bool:
        """True in the band between a good fit and a known impostor."""
        return MAX_RESID_FT < self.max_resid_ft <= ABSTAIN_RESID_FT


@dataclass
class ProfileRegion:
    """One profile graph on one page. A page may carry several."""

    box: tuple[float, float, float, float]   # page coords, x0 y0 x1 y1
    source: str          # "layer" | "labels" | "caption+layer" | ...
    why: str             # one sentence naming the evidence
    station: AxisFit | None = None
    elevation: AxisFit | None = None
    caption_h_ft_per_in: float | None = None
    caption_v_ft_per_in: float | None = None
    layers: list[str] = field(default_factory=list)
    layer_len_pt: float = 0.0
    notes: list[str] = field(default_factory=list)

    # ---- what a caller actually wants -----------------------------------
    @property
    def h_ft_per_paper_inch(self) -> float | None:
        """Horizontal scale, LABELS FIRST. None when nothing is trustworthy.

        The caption is never preferred over a good label fit: it is stale on
        Belle Farms 2 of 2 and 2x out on every half-size sheet in the corpus.
        """
        if self.station and self.station.trustworthy:
            return self.station.ft_per_paper_inch
        return self.caption_h_ft_per_in

    @property
    def v_ft_per_paper_inch(self) -> float | None:
        if self.elevation and self.elevation.trustworthy:
            return self.elevation.ft_per_paper_inch
        return self.caption_v_ft_per_in

    @property
    def exaggeration(self) -> float | None:
        h, v = self.h_ft_per_paper_inch, self.v_ft_per_paper_inch
        return (h / v) if (h and v) else None

    @property
    def caption_disagrees(self) -> float | None:
        """Ratio caption/labels when both exist and differ by >2%.

        A finding, not an error: on the three half-size documents the caption
        is right for the full-size print and the labels are right for this
        PDF, and the engine measures this PDF.
        """
        worst = None
        for cap, fit in ((self.caption_h_ft_per_in, self.station),
                         (self.caption_v_ft_per_in, self.elevation)):
            if cap and fit and fit.trustworthy and fit.ft_per_paper_inch:
                r = cap / fit.ft_per_paper_inch
                if abs(r - 1.0) > 0.02 and (worst is None
                                            or abs(r - 1) > abs(worst - 1)):
                    worst = r
        return worst


# ==========================================================================
# helpers
# ==========================================================================
def _walk(items: list, tol: float, key) -> list[list]:
    """Sort-and-walk gap clustering.

    NOT round(x/TOL): a bucket answers "which bin", not "are these within
    TOL of each other", and a bin edge between two labels splits a real
    series. CLAUDE.md sec.5, defect shape A.
    """
    out: list[list] = []
    for v in sorted(items, key=key):
        if out and key(v) - key(out[-1][-1]) <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return out


def _dedupe(words: list) -> list:
    """Drop words drawn twice at the same coordinates.

    A storage-site set prints every station and elevation label TWICE at
    identical positions (verified on engine page index 11: "0+00" at
    (884.2, 667.0) twice, and so for every label in the row). Left in, the
    tied pair fails a strict monotonic test and five real profile pages
    score as having no axis.
    """
    seen, out = set(), []
    for w in words:
        k = (round(w[0], 1), round(w[1], 1), round(w[2], 1), round(w[3], 1),
             str(w[4]).strip())
        if k in seen:
            continue
        seen.add(k)
        out.append(w)
    return out


def _monotonic(vs: list[float]) -> bool:
    return (all(b > a for a, b in zip(vs, vs[1:]))
            or all(b < a for a, b in zip(vs, vs[1:])))


def _lsq(xs: list[float], ys: list[float]):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    m = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    b = my - m * mx
    res = [y - (m * x + b) for x, y in zip(xs, ys)]
    syy = sum((y - my) ** 2 for y in ys)
    r2 = 1 - sum(r * r for r in res) / syy if syy else 1.0
    return m, b, max(abs(r) for r in res), r2


def read_captions(words: list) -> tuple[float | None, float | None]:
    """The page's stated horizontal and vertical scale, in ft per paper inch.

    Returns (None, None) when the page states no pair - true of the storage site
    and water-main sets 1 and 2, which give a graphic bar or nothing at all.
    """
    full = re.sub(r"\s+", " ", " ".join(str(w[4]).strip() for w in words))
    hm, vm = _H_FIRST.search(full), _V_FIRST.search(full)
    if not (hm and vm):
        hm, vm = _H_LAST.search(full), _V_LAST.search(full)
    if not (hm and vm):
        return None, None
    try:
        h = float(hm.group(2)) / float(hm.group(1))
        v = float(vm.group(2)) / float(vm.group(1))
    except (ValueError, ZeroDivisionError):
        return None, None
    return h, v


def station_axes(words: list) -> list[AxisFit]:
    """Every row of >=4 station labels that behaves like an axis."""
    pts = []
    for x0, y0, x1, y1, *rest in words:
        m = _STA.match(str(rest[0]).strip())
        if m:
            pts.append(((y0 + y1) / 2, (x0 + x1) / 2,
                        int(m.group(1)) * 100 + float(m.group(2)),
                        str(rest[0]).strip()))
    out = []
    for row in _walk(pts, ROW_TOL_PT, lambda e: e[0]):
        if len(row) < MIN_LABELS:
            continue
        row = sorted(row, key=lambda e: e[1])
        if not _monotonic([e[2] for e in row]):
            continue
        if row[-1][1] - row[0][1] < MIN_SPAN_PT:
            continue
        f = _lsq([e[1] for e in row], [e[2] for e in row])
        if not f:
            continue
        m, _b, mr, r2 = f
        steps = {round(b[2] - a[2], 2) for a, b in zip(row, row[1:])}
        out.append(AxisFit(
            kind="station", n=len(row), labels=[e[3] for e in row],
            pos0=row[0][1], pos1=row[-1][1],
            across=sum(e[0] for e in row) / len(row),
            val0=row[0][2], val1=row[-1][2],
            step_ft=(steps.pop() if len(steps) == 1 else 0.0),
            ft_per_pt=m, ft_per_paper_inch=abs(m) * 72.0,
            max_resid_ft=mr, r2=r2, left_to_right=m > 0))
    return sorted(out, key=lambda a: (-a.n, a.max_resid_ft))


def elevation_axes(words: list) -> list[AxisFit]:
    """Every column of >=4 evenly-stepped integers that behaves like an axis.

    The even-step test is what keeps CONTOUR labels out: contours are evenly
    stepped in VALUE too, but they are not evenly stepped in POSITION, which
    the residual test then catches (water-main set 1 p299: step exactly 5 ft, fit
    residual 2.593 ft against 0.026 ft for a real axis).
    """
    pts = []
    for x0, y0, x1, y1, *rest in words:
        m = _INT.match(str(rest[0]).strip())
        if m:
            pts.append(((x0 + x1) / 2, (y0 + y1) / 2, float(m.group(1)),
                        str(rest[0]).strip()))
    out = []
    for col in _walk(pts, COL_TOL_PT, lambda e: e[0]):
        if len(col) < MIN_LABELS:
            continue
        col = sorted(col, key=lambda e: e[1])
        if not _monotonic([e[2] for e in col]):
            continue
        if col[-1][1] - col[0][1] < MIN_COL_SPAN_PT:
            continue
        steps = [abs(b[2] - a[2]) for a, b in zip(col, col[1:])]
        if min(steps) <= 0 or (max(steps) - min(steps)) / max(steps) > STEP_TOL:
            continue
        f = _lsq([e[1] for e in col], [e[2] for e in col])
        if not f:
            continue
        m, _b, mr, r2 = f
        out.append(AxisFit(
            kind="elevation", n=len(col), labels=[e[3] for e in col],
            pos0=col[0][1], pos1=col[-1][1],
            across=sum(e[0] for e in col) / len(col),
            val0=col[0][2], val1=col[-1][2],
            step_ft=steps[0], ft_per_pt=m, ft_per_paper_inch=abs(m) * 72.0,
            max_resid_ft=mr, r2=r2))
    return sorted(out, key=lambda a: (-a.n, a.max_resid_ft))


def layer_regions(units: list[StrokeUnit]) -> list[ProfileRegion]:
    """Profile boxes from CAD layer names alone.

    This is the branch for the 28 pages whose drawing text is outlines or
    raster. On Loves Lane C9.1 it returns the box (354.1, 836.0)-(2096.8,
    1510.0) holding 92,316.1 pt on five O-PROF-* layers, on a page whose
    entire text layer is 84 words.
    """
    hits = [u for u in units if u.layer and _PROF_LAYER.search(u.layer)]
    if not hits:
        return []
    # One region per connected cluster of profile-layer geometry would be
    # better; a single bbox is what is justified by what has been measured,
    # and the caller is told so.
    pts = np.vstack([u.pts for u in hits])
    box = (float(pts[:, 0].min()), float(pts[:, 1].min()),
           float(pts[:, 0].max()), float(pts[:, 1].max()))
    by_layer: dict[str, float] = {}
    for u in hits:
        by_layer[u.layer] = by_layer.get(u.layer, 0.0) + u.length
    total = sum(by_layer.values())
    note = ("single bounding box over all profile-layer geometry; if the "
            "sheet carries two stacked profiles this box spans both")
    return [ProfileRegion(
        box=box, source="layer",
        why=(f"{len(hits)} stroke units totalling {total:,.1f} pt on "
             f"{len(by_layer)} CAD layer(s) matching PROF/PROFILE"),
        layers=sorted(by_layer, key=lambda k: -by_layer[k]),
        layer_len_pt=total, notes=[note])]


def _merge_overlapping(regions: list[ProfileRegion]) -> list[ProfileRegion]:
    """Union regions whose boxes overlap; keep the better-fitting axes.

    Overlap, not proximity. Two different drawings on one page (water-main set 1
    p299: a plan above a profile) have disjoint boxes and stay separate. Two
    label rows of the SAME graph always overlap, because the elevation column
    spans the box between them - Belle Farms 1 of 2 and storage-site p11 both
    label one box at top and foot, 646 pt and 585 pt apart.
    """
    out: list[ProfileRegion] = []
    for r in sorted(regions, key=lambda z: -(z.box[2] - z.box[0])):
        for q in out:
            if (r.box[0] < q.box[2] and r.box[2] > q.box[0]
                    and r.box[1] < q.box[3] and r.box[3] > q.box[1]):
                q.box = (min(q.box[0], r.box[0]), min(q.box[1], r.box[1]),
                         max(q.box[2], r.box[2]), max(q.box[3], r.box[3]))
                # keep whichever station fit has more labels; a longer series
                # is a better-conditioned fit, not merely a luckier one
                if r.station and (q.station is None
                                  or r.station.n > q.station.n):
                    q.station = r.station
                if r.elevation and (q.elevation is None
                                    or r.elevation.n > q.elevation.n):
                    q.elevation = r.elevation
                for n in r.notes:
                    if n not in q.notes:
                        q.notes.append(n)
                break
        else:
            out.append(r)
    return out


def find_profiles(page, units: list[StrokeUnit] | None = None
                  ) -> list[ProfileRegion]:
    """Every profile region on one page, best-evidenced branch first.

    `page` is a pymupdf Page. `units` is extract_strokes(page) if the caller
    already has it; pass it to avoid a second extraction on a heavy sheet.
    Read-only: nothing on the page or in `units` is mutated.
    """
    words = _dedupe(page_words(page))
    cap_h, cap_v = read_captions(words)
    sax = station_axes(words)
    eax = elevation_axes(words)

    regions: list[ProfileRegion] = []

    # --- branch 1: labels. The only branch that yields a scale. ----------
    # Pair each station row with the elevation column whose y-range contains
    # it, so a page carrying two DIFFERENT drawings does not merge them into
    # one poisoned fit (water-main set 1 p299 is a plan above a profile).
    used_elev: set[int] = set()
    for s in sax:
        best, best_i = None, None
        for i, e in enumerate(eax):
            if i in used_elev:
                continue
            lo, hi = min(e.pos0, e.pos1), max(e.pos0, e.pos1)
            # the station row sits at the top or the foot of the box, so its
            # y must be within a caption's height of the column's y range.
            if not (lo - 120 <= s.across <= hi + 120):
                continue
            if best is None or e.n > best.n:
                best, best_i = e, i
        if best is None:
            continue
        used_elev.add(best_i)
        # the box is the union of the two label extents: the station row
        # gives x, the elevation column gives y, and the column's own x
        # position is one edge of the box.
        xs = sorted((s.pos0, s.pos1, best.across))
        ys = sorted((best.pos0, best.pos1, s.across))
        r = ProfileRegion(
            box=(xs[0], ys[0], xs[-1], ys[-1]),
            source="labels",
            why=(f"{s.n} station labels {s.labels[0]}..{s.labels[-1]} at "
                 f"{s.ft_per_paper_inch:.4f} ft/paper-in (max resid "
                 f"{s.max_resid_ft:.3f} ft) crossed with {best.n} elevation "
                 f"labels {best.labels[0]}..{best.labels[-1]} at "
                 f"{best.ft_per_paper_inch:.4f} (max resid "
                 f"{best.max_resid_ft:.3f} ft)"),
            station=s, elevation=best,
            caption_h_ft_per_in=cap_h, caption_v_ft_per_in=cap_v)
        if s.abstain or best.abstain:
            r.notes.append(
                "ABSTAIN: a label series fits worse than a genuine axis "
                "(<=0.842 ft measured) but better than a known impostor "
                "(>=2.593 ft). No scale is asserted.")
        if not s.trustworthy or not best.trustworthy:
            r.notes.append(
                "at least one label series fails the residual test; it is "
                "probably plan contour labels or curved-alignment stationing, "
                "not an axis (measured on a plan-and-profile sheet whose plan sits above the graph)")
        d = r.caption_disagrees
        if d is not None:
            r.notes.append(
                f"CAPTION DISAGREES WITH LABELS by {d:.3f}x. Believe the "
                f"labels: they describe this PDF. On the corpus this is "
                f"either a stale caption copied between sheets or a half-size "
                f"reproduction whose caption is true of the full-size print.")
        if not s.left_to_right:
            r.notes.append("stationing runs RIGHT TO LEFT on this sheet "
                           "(measured on one profile sheet of the corpus)")
        regions.append(r)

    # A profile is normally labelled TWICE along X - a station row at the top
    # of the box and another at the foot (Belle Farms 1 of 2: y = 108.5 and
    # y = 754.5, 22 and 20 labels, fitting 80.0157 and 80.0164). Both pair
    # with the same elevation column, so the loop above emits one region per
    # row. Merge regions that overlap: they are one graph.
    regions = _merge_overlapping(regions)

    # --- branch 2: CAD layer. Adds the outlined-text pages. --------------
    if units is not None:
        for lr in layer_regions(units):
            lr.caption_h_ft_per_in = cap_h
            lr.caption_v_ft_per_in = cap_v
            # If a label-derived region already covers this box, merge the
            # evidence rather than reporting the same graph twice.
            merged = False
            for r in regions:
                if (lr.box[0] < r.box[2] and lr.box[2] > r.box[0]
                        and lr.box[1] < r.box[3] and lr.box[3] > r.box[1]):
                    r.layers = lr.layers
                    r.layer_len_pt = lr.layer_len_pt
                    r.source = "labels+layer"
                    r.why += f"; {lr.why}"
                    merged = True
                    break
            if not merged:
                if cap_h and cap_v:
                    lr.source = "layer+caption"
                    lr.why += (f"; page states H 1\"={cap_h:g}' "
                               f"V 1\"={cap_v:g}'")
                lr.notes.append(
                    "no readable station or elevation labels on this page - "
                    "the drawing text is outlines or raster. Scale here comes "
                    "from the caption only and has NOT been corroborated "
                    "against label geometry.")
                regions.append(lr)

    # --- branch 3: caption alone. Detect only, never calibrate. ----------
    if not regions and cap_h and cap_v and abs(cap_h - cap_v) > 1e-9:
        regions.append(ProfileRegion(
            box=(0.0, 0.0, 0.0, 0.0), source="caption",
            why=(f"page states two different scales for one drawing "
                 f"(H 1\"={cap_h:g}', V 1\"={cap_v:g}'), which only a profile "
                 f"or a section does"),
            caption_h_ft_per_in=cap_h, caption_v_ft_per_in=cap_v,
            notes=["region NOT located: no labels and no profile CAD layer. "
                   "Flag the page for a human; do not exclude geometry on "
                   "this evidence alone."]))
    return regions


def strokes_in_profile(units: list[StrokeUnit],
                       regions: list[ProfileRegion],
                       pad_pt: float = 12.0) -> tuple[list[int], float]:
    """Indices of `units` wholly inside a located profile box, and their pt.

    Wholly inside, not merely overlapping: a match line or a leader that
    crosses the box edge belongs to the plan. `pad_pt` matches the slack the
    survey used so the numbers in SURVEY.md reproduce.

    A region whose box is (0,0,0,0) - branch 3, caption only - selects
    nothing, deliberately.
    """
    idx: list[int] = []
    total = 0.0
    boxes = [r.box for r in regions if r.box[2] > r.box[0]]
    if not boxes:
        return idx, total
    for i, u in enumerate(units):
        p = u.pts
        xs0, xs1 = float(p[:, 0].min()), float(p[:, 0].max())
        ys0, ys1 = float(p[:, 1].min()), float(p[:, 1].max())
        for bx0, by0, bx1, by1 in boxes:
            if (xs0 >= bx0 - pad_pt and xs1 <= bx1 + pad_pt
                    and ys0 >= by0 - pad_pt and ys1 <= by1 + pad_pt):
                idx.append(i)
                total += u.length
                break
    return idx, total


# ==========================================================================
# WIRING - what detect_page / server.py would need. NOT DONE HERE.
# ==========================================================================
# Nothing in this file is called by anything. To use it:
#
# 1. takeoff_engine/detect_page.py
#    After extract_strokes and BEFORE build_chains, call
#        regions = profile.find_profiles(page, units)
#    and put a serialised form on the result dict as `result["profiles"]`
#    (box, source, why, h/v ft-per-paper-inch, exaggeration, notes). Adding
#    a key is additive and cannot move the gate on its own.
#    Excluding the geometry is a SEPARATE, larger change: strokes_in_profile
#    gives the indices, but dropping them changes every downstream count on
#    the 57 pages and needs its own before/after corpus diff. Do not smuggle
#    it in with the reporting change.
#    detect_page.py is in _ENGINE_FILES, so touching it changes engine_sig
#    and every cached result re-detects. Expect a full corpus re-run.
#
# 2. takeoff_engine/scale_detect.py
#    ScaleResult.ft_per_paper_inch is a single isotropic scalar and is
#    structurally unable to describe a page with a horizontal and a vertical
#    scale (F126). A profile region carries two. Either scale_detect grows a
#    per-region scale, or the profile regions stay a separate field and the
#    takeoff refuses to price anything inside one. The second is smaller and
#    is what the abstain-first posture argues for.
#
# 3. server.py + webui/app.js
#    A `profiles` array on the result wants a panel row per region: the box,
#    which branch fired, both scales with their provenance, and the caption
#    disagreement when there is one. The UI should be able to isolate a
#    profile region the way it isolates a style, so a reviewer can see what
#    is being held back. server.py is CRLF; webui/* is LF.
#
# 4. Gate
#    This module is off the chain path entirely - it imports extract only and
#    is called by nothing - so `python -m takeoff_engine.regress` must be
#    unchanged by its presence. Run it as a collateral guard, not as
#    evidence that the module works.
#
# WIRED 2026-09-26 (fix/trenching): server.py `GET .../profile` calls
# find_profiles + read_profile below and caches p<N>_profile.json. Still off
# the chain path: nothing in detect_page reads this file, and profile.py is
# not in detect_page._ENGINE_FILES, so engine_sig does not move.


# ==========================================================================
# READING a calibrated profile: ground and pipe per station
# ==========================================================================
# What the trench tool needs from a profile is one number per station: the
# depth from the surface to the pipe invert. A client takes it off the sheet
# with a scale (call 2026-09-03: existing ground to pipe bottom, every time
# the depth crosses 6 / 8 / 10 / 12 ft a mark, LF per band). This reads the
# same picture the same way:
#
#   * every stroke inside the region box is grouped by PEN (CAD layer,
#     colour, width, dash state), because a drafter draws one thing per pen;
#   * each pen is sampled at every station (the x where a vertical line at
#     that station crosses the pen's segments), so a pen becomes a series of
#     elevations along the stationing;
#   * a pen drawn as TWO parallel lines a pipe-diameter apart is a PIPE
#     (crown and invert - Res-A C4.2's C-STRM-PROF, water main W-1's 8" HDPE);
#     a single continuous line above the pipes is a SURFACE. Dashed surfaces
#     read as existing ground and solid ones as finished grade (CONVENTION:
#     general drafting practice; the sheet's own label - EXISTING GRADE,
#     FINAL GRADE, EXISTING GROUND PROFILE - wins when one sits beside the
#     line);
#   * horizontal grid rules and vertical station rules are never a line of
#     anything (a grid rule has zero rise; a pipe or a ground line does not);
#   * every printed elevation on the graph (grade-break callouts with a
#     station, the invert column along the foot) is compared with the read,
#     so the read carries its own error bars.
#
# It abstains rather than guesses: no pipe pen -> no depth; a surface with
# no crossing at a station -> no depth there (a dashed line is interpolated
# across its own gaps only).

# stations are sampled this far apart, in feet. 1 ft is finer than any
# printed invert (Res-A prints every 25 ft, water main 1's are on callouts) and
# keeps a 600-ft profile at 600 samples.
READ_STEP_FT = 1.0
# a pen has to reach across this share of the graph's station span before
# it can be a surface or a pipe. Crossing-pipe symbols (a sewer crossing on
# Res-A C4.2 is a 6-pt ellipse) and structure symbols are far under it.
MIN_SPAN_FRAC = 0.08
# a horizontal grid rule: no rise at all over a real length
GRID_RISE_PT = 0.02
GRID_LEN_PT = 60.0
# a dashed pen is interpolated across gaps up to this many feet of station
# (existing-ground dashes on both sheets gap under 10 ft)
DASH_GAP_FT = 40.0
# a pipe drawn as two lines: the two crossings sit this far apart, in feet
# of elevation (4-in pipe 0.33 ft ... a 36-in culvert 3 ft). Wider is two
# separate things.
PIPE_GAP_MAX_FT = 3.5
PIPE_GAP_MIN_FT = 0.15
# a pen crossing fewer than this share of the stations inside its own span
# is ticks, arrows or a mask, not a line of anything (a dashed existing
# ground line still reaches 0.6-0.8)
MIN_COVERAGE = 0.15
_ANNO_LAYER = re.compile(r"(?i)TEXT|TXT|ANNO|TTLE|TITLE|DIM|LEAD|NOTE|HATCH|MASK|(?:^|[-_|])MS(?:[-_|]|$)")
# a surface label is a SHORT phrase naming the surface: EXISTING GRADE,
# FINAL GRADE, PROPOSED GRADE, EXISTING GROUND PROFILE, FG, EG. Anchored
# and capped, because "GRADE BREAK STA = 0+00.00" and a footer's file path
# "...\WATER MAIN PLAN AND PROFILE" both contain the words
_SURF_WORD = re.compile(r"(?i)^\s*(?:(?:EXIST(?:ING|\.)?|FINAL|FINISH(?:ED)?|PROPOSED|ORIGINAL|NATURAL|PROP\.?)\s+)?"
                        r"(?:GRADE|GROUND(?:\s+PROFILE)?|PROFILE\s+GRADE|EG|FG)\s*(?:LINE|PROFILE)?\s*$")
_PIPE_WORD = re.compile(r'(?i)(\d{1,2})\s*(?:"|”|\'\')\s*(HDPE|PVC|RCP|DIP|CMP|DI|STEEL|SDR|C900)?')
_STA_IN_TEXT = re.compile(r"(?i)STA\.?\s*[=:]?\s*(\d{1,3})\+(\d{2}(?:\.\d+)?)")
_ELEV_IN_TEXT = re.compile(r"(?i)(?:ELEV|EL|INV|IE|INVERT|FL)\.?\s*[=:]?\s*(\d{3,5}\.\d{1,2})")
_ELEV_WORD = re.compile(r"^(\d{3,5}\.\d{1,2})'?$")


@dataclass
class ProfileLine:
    """One pen inside a profile box, sampled along the stationing."""

    pen: str
    layer: str
    color: list
    width: float
    dashed: bool
    role: str                 # "surface" | "pipe"
    kind: str                 # surface: "existing" | "finished" | "surface";
                              # pipe: "pipe"
    label: str | None         # sheet text found beside it, if any
    coverage: float           # crossings / stations within its own span
    sta0: float
    sta1: float
    two_line: bool
    od_ft: float | None       # median crown-invert gap, two-line pens
    # [station, low elevation, high elevation] per sample; None where no
    # crossing (kept as null so the series stays aligned to `stations`)
    samples: list

    def to_dict(self) -> dict:
        return {
            "pen": self.pen, "layer": self.layer, "color": self.color,
            "width": self.width, "dashed": self.dashed, "role": self.role,
            "kind": self.kind, "label": self.label,
            "coverage": round(self.coverage, 3),
            "sta0": round(self.sta0, 2), "sta1": round(self.sta1, 2),
            "two_line": self.two_line,
            "od_ft": None if self.od_ft is None else round(self.od_ft, 3),
            "samples": [[round(s, 2),
                         None if lo is None else round(lo, 3),
                         None if hi is None else round(hi, 3)]
                        for s, lo, hi in self.samples],
        }


@dataclass
class ProfileRead:
    region: ProfileRegion
    step_ft: float
    stations: list
    surfaces: list            # ProfileLine, finished grade first when both
    pipes: list               # ProfileLine, most covered stationing first
    checks: list              # printed elevations vs the read
    notes: list
    station: AxisFit | None = None    # the axes as READ (snapped to the grid)
    elevation: AxisFit | None = None
    title: str = ""                   # the graph's printed title, when one sits under it
    system: str = ""                  # STM / SS / W / G / E read off the title, else ""

    def to_dict(self) -> dict:
        r = self.region
        st = self.station or r.station
        el = self.elevation or r.elevation
        return {
            "box": [round(v, 2) for v in r.box],
            "source": r.source, "why": r.why, "notes": list(r.notes) + list(self.notes),
            "title": self.title, "system": self.system,
            "h_ft_per_in": None if not st else st.ft_per_paper_inch,
            "v_ft_per_in": None if not el else el.ft_per_paper_inch,
            "exaggeration": (st.ft_per_paper_inch / el.ft_per_paper_inch)
                            if (st and el and el.ft_per_paper_inch) else None,
            "station": None if not st else {
                "pos0": st.pos0, "val0": st.val0,
                "ft_per_pt": st.ft_per_pt, "across": st.across,
                "labels": st.labels, "max_resid_ft": st.max_resid_ft,
                "left_to_right": st.left_to_right},
            "elevation": None if not el else {
                "pos0": el.pos0, "val0": el.val0,
                "ft_per_pt": el.ft_per_pt, "across": el.across,
                "labels": el.labels, "max_resid_ft": el.max_resid_ft},
            "step_ft": self.step_ft,
            "stations": [round(s, 2) for s in self.stations],
            "surfaces": [s.to_dict() for s in self.surfaces],
            "pipes": [p.to_dict() for p in self.pipes],
            "checks": self.checks,
        }


def _pen_key(u: StrokeUnit) -> tuple:
    col = tuple(round(float(c), 2) for c in (u.color or ()))
    dashed = bool(u.dashes and u.dashes.strip() not in ("", "[] 0", "[]", "[ ] 0"))
    return (u.layer or "", col, round(float(u.width or 0.0), 1), dashed)


def _crossings(segs: np.ndarray, xs: np.ndarray) -> list:
    """For every x in xs: the sorted y values where the segments cross the
    vertical line at x. segs is (N, 4): x0 y0 x1 y1, verticals excluded."""
    out = [[] for _ in xs]
    if not len(segs):
        return out
    x0, y0, x1, y1 = segs[:, 0], segs[:, 1], segs[:, 2], segs[:, 3]
    lo = np.minimum(x0, x1)
    hi = np.maximum(x0, x1)
    for i, x in enumerate(xs):
        m = (lo <= x) & (x <= hi)
        if not m.any():
            continue
        t = (x - x0[m]) / (x1[m] - x0[m])
        ys = y0[m] + t * (y1[m] - y0[m])
        ys = np.sort(ys)
        # a shared vertex between two segments is one crossing, not two
        keep = [float(ys[0])]
        for v in ys[1:]:
            if v - keep[-1] > 0.05:
                keep.append(float(v))
        out[i] = keep
    return out


def _axis_values(fit: AxisFit) -> list:
    out = []
    for lab in fit.labels:
        m = _STA.match(lab)
        if m:
            out.append(int(m.group(1)) * 100 + float(m.group(2)))
        else:
            try:
                out.append(float(lab))
            except ValueError:
                out.append(None)
    return out


def _snap_axis(fit: AxisFit, rules: list, tol_pt: float = 4.0) -> AxisFit:
    """A copy of `fit` refitted on the grid rules its labels sit beside.

    Each label's fitted position is moved to the nearest rule within tol_pt
    and the line is refitted; with fewer than two snapped labels, or a
    refit that moves the scale by more than 1 %, the label fit stands.
    """
    if not rules or fit is None:
        return fit
    rs = np.asarray(sorted(set(round(r, 2) for r in rules)))
    vals = _axis_values(fit)
    pos, val = [], []
    for v in vals:
        if v is None:
            continue
        p = fit.pos0 + (v - fit.val0) / fit.ft_per_pt
        j = int(np.argmin(np.abs(rs - p)))
        if abs(rs[j] - p) <= tol_pt:
            pos.append(float(rs[j]))
            val.append(v)
    if len(pos) < 2:
        return fit
    # The labels sit a constant distance from their rules (a text centre
    # against a line), so the correction is a SHIFT of the axis, never a new
    # scale: the label fit's scale is kept and its anchor moves by the mean
    # label-to-rule offset. A refit on the rule positions themselves gave
    # 7.209 pt/ft against a 7.200 grid on one sheet (the rules are found at
    # 0.1-pt rounding); the shift keeps the exact 7.200. Offsets that do not
    # agree (one label snapped to the wrong rule) leave the fit alone.
    deltas = [p - (fit.pos0 + (v - fit.val0) / fit.ft_per_pt) for p, v in zip(pos, val)]
    if max(deltas) - min(deltas) > 1.0:
        return fit
    shift = float(np.mean(deltas))
    out = AxisFit(**{**fit.__dict__})
    out.pos0 = fit.pos0 + shift
    out.pos1 = fit.pos1 + shift
    return out


def read_profile(page, region: ProfileRegion, units: list[StrokeUnit] | None = None,
                 step_ft: float = READ_STEP_FT) -> ProfileRead | None:
    """Sample every pen inside `region` along its stationing; classify the
    pens into surfaces and pipes; compare with the printed elevations.

    Returns None when the region has no trustworthy station AND elevation
    axis - there is no scale to read anything with, and a guess here is a
    priced number.
    """
    from .extract import extract_strokes, page_lines

    st, el = region.station, region.elevation
    if not (st and el and st.trustworthy and el.trustworthy):
        return None
    if units is None:
        units = extract_strokes(page)
    bx0, by0, bx1, by1 = region.box
    pad = 30.0

    # group the strokes inside the box by pen; keep non-vertical, non-grid
    # segments of each, and keep the grid rules themselves for the axis snap
    pens: dict[tuple, list] = {}
    h_cand: dict[float, list] = {}   # y -> [(x_lo, x_hi, pen, seg)] horizontal pieces
    v_rules: list = []               # (x, y_lo, y_hi) of vertical rules
    for u in units:
        if u.layer and _ANNO_LAYER.search(u.layer):
            continue
        # a white stroke is a text mask (whiteout under a label), never a line
        if u.color and len(u.color) >= 3 and min(u.color[:3]) > 0.92:
            continue
        p = u.pts
        if len(p) < 2:
            continue
        if (p[:, 0].min() < bx0 - pad or p[:, 0].max() > bx1 + pad
                or p[:, 1].min() < by0 - pad or p[:, 1].max() > by1 + pad):
            continue
        k = _pen_key(u)
        segs = pens.setdefault(k, [])
        for a, b in zip(p[:-1], p[1:]):
            dx, dy = float(b[0] - a[0]), float(b[1] - a[1])
            if abs(dx) < 1e-6:
                if abs(dy) >= GRID_LEN_PT:
                    v_rules.append((float(a[0]), min(a[1], b[1]), max(a[1], b[1])))
                continue                                  # a vertical rule
            seg = (float(a[0]), float(a[1]), float(b[0]), float(b[1]))
            if abs(dy) <= GRID_RISE_PT and abs(dx) >= GRID_LEN_PT:
                h_cand.setdefault(round(float(a[1]), 1), []).append(
                    (min(a[0], b[0]), max(a[0], b[0]), k, seg))
                continue                                  # decided below
            segs.append(seg)
    # A horizontal grid rule reaches across the graph (in pieces, where
    # labels break it); a pipe drawn FLAT for 50 ft does not (water main W-1
    # sta 4+24 to 4+69 and 5+14 to 5+59: the first cut of this filter
    # deleted the pipe there). Union length at one y decides.
    h_rules: list = []
    for y, pieces in h_cand.items():
        iv = sorted((lo, hi) for lo, hi, _, _ in pieces)
        total, cur = 0.0, None
        for lo, hi in iv:
            if cur is None or lo > cur[1]:
                if cur:
                    total += cur[1] - cur[0]
                cur = [lo, hi]
            else:
                cur[1] = max(cur[1], hi)
        if cur:
            total += cur[1] - cur[0]
        if total >= 0.5 * (bx1 - bx0):
            h_rules.append((y, iv[0][0], iv[-1][1]))
        else:
            for _, _, k, seg in pieces:
                pens.setdefault(k, []).append(seg)

    # ---- snap each axis onto the grid rules. The label fit puts an axis
    # value at the label's CENTRE; the drafter puts it on the rule the label
    # names, 0.5-2 pt away (water main W-1: 0.53 pt = 0.07 ft, a bias every
    # invert then carried). A label whose rule is within 4 pt moves onto it.
    st = _snap_axis(st, [x for x, _, _ in v_rules])
    el = _snap_axis(el, [y for y, _, _ in h_rules])
    sta_at = lambda x: st.val0 + (x - st.pos0) * st.ft_per_pt          # noqa: E731
    x_at = lambda s: st.pos0 + (s - st.val0) / st.ft_per_pt              # noqa: E731
    elev_at = lambda y: el.val0 + (y - el.pos0) * el.ft_per_pt           # noqa: E731

    # stations to sample: the box's own x extent, whole feet
    s_lo, s_hi = sorted((sta_at(bx0), sta_at(bx1)))
    s_first = float(np.ceil(s_lo / step_ft) * step_ft)
    stations = list(np.arange(s_first, s_hi + 1e-9, step_ft))
    if len(stations) < 10:
        return None
    xs = np.array([x_at(s) for s in stations])

    span_ft = max(s_hi - s_lo, 1e-9)
    lines: list[ProfileLine] = []
    for k, segs in pens.items():
        if not segs:
            continue
        S = np.asarray(segs, float)
        x_lo, x_hi = float(np.minimum(S[:, 0], S[:, 2]).min()), float(np.maximum(S[:, 0], S[:, 2]).max())
        p0, p1 = sorted((sta_at(x_lo), sta_at(x_hi)))
        if (p1 - p0) / span_ft < MIN_SPAN_FRAC:
            continue
        cr = _crossings(S, xs)
        n_in = sum(1 for s in stations if p0 - 1e-6 <= s <= p1 + 1e-6)
        n_hit = sum(1 for c in cr if c)
        if n_in == 0 or n_hit < 3:
            continue
        # crossings -> elevations, sorted low to high
        elevs = [sorted(elev_at(y) for y in c) for c in cr]
        n2 = sum(1 for e in elevs if len(e) == 2)
        # A pipe pen crosses a station TWICE (crown and invert) at most of
        # its stations, the two a pipe-diameter apart. The typical gap is
        # measured on the exactly-two stations only: a station where a
        # callout tick or a crossing symbol adds a third line must not widen
        # it.
        two = n2 >= max(3, int(0.5 * n_hit))
        od = None
        if two:
            od = float(np.median([e[1] - e[0] for e in elevs if len(e) == 2]))
            if not (PIPE_GAP_MIN_FT <= od <= PIPE_GAP_MAX_FT):
                two, od = False, None
        if os.environ.get("PROFILE_DEBUG"):
            counts = {}
            for e in elevs:
                counts[len(e)] = counts.get(len(e), 0) + 1
            mid = next((e for e in elevs[len(elevs) // 2:] if e), [])
            print(f"[profile] pen {k}: sta {p0:.0f}-{p1:.0f} n_in {n_in} n_hit {n_hit} n2 {n2} "
                  f"crossings-per-station {dict(sorted(counts.items()))} two {two} od {od} "
                  f"mid-station elevations {[round(v, 2) for v in mid]}", file=sys.stderr)
        samples = []
        if two:
            tol = max(0.5 * od, 0.15)
            for s, e in zip(stations, elevs):
                best = None
                for a, b in zip(e, e[1:]):
                    err = abs((b - a) - od)
                    if err <= tol and (best is None or err < best[0]):
                        best = (err, a, b)
                samples.append([s, best[1], best[2]] if best else [s, None, None])
        else:
            # ONE line, followed by continuity: where the pen crosses a
            # station more than once (a dashed pen shared with another dashed
            # element, a tick), take the crossing nearest the running value,
            # and refuse a jump no ground line makes between two stations
            # (water main W-1: the EXISTING GROUND PROFILE leader shares the
            # ground's pen and crosses 4 ft above it where a dash is missing).
            # The pen's median elevation only CHOOSES among several crossings
            # until a station has been accepted; the jump test runs from an
            # accepted value, never from the median (a line that starts 2 ft
            # from its own median is the normal case, not a jump).
            # The first accepted value must still be NEAR the median (within
            # FIRST_JUMP): the same pen draws the graph's bottom rule 27 ft
            # under the ground on water main W-1, and an unconditional first
            # acceptance locked the tracker onto it and rejected every real
            # ground crossing as a jump.
            singles = [e[0] for e in elevs if len(e) == 1]
            ref = float(np.median(singles)) if singles else None
            last = None
            jump = 1.5 + 1.5 * step_ft
            first_jump = 6.0 + 2.0 * step_ft
            for s, e in zip(stations, elevs):
                if not e:
                    samples.append([s, None, None])
                    continue
                guess = last if last is not None else ref
                v = e[0] if len(e) == 1 or guess is None else min(e, key=lambda q: abs(q - guess))
                if last is not None and abs(v - last) > jump:
                    samples.append([s, None, None])
                    continue
                if last is None and ref is not None and abs(v - ref) > first_jump:
                    samples.append([s, None, None])
                    continue
                last = v
                samples.append([s, v, v])
            # the tail of that leader converges onto the line, so the jump
            # test lets its last few stations through: a value that leaves
            # the chord of its neighbours by more than a ground line bends
            # over 15 ft of station is dropped (two passes: the first drop
            # can expose the next).
            # The chord is drawn between the nearest kept samples at least
            # 3 ft away on each side, so a run of impostor stations cannot
            # vouch for one another.
            for _pass in range(3):
                idx = [i for i, (_, lo, _) in enumerate(samples) if lo is not None]
                drop = set()
                for j, i in enumerate(idx):
                    si = samples[i][0]
                    a = next((idx[q] for q in range(j - 1, -1, -1) if si - samples[idx[q]][0] >= 3.0), None)
                    b = next((idx[q] for q in range(j + 1, len(idx)) if samples[idx[q]][0] - si >= 3.0), None)
                    if a is None or b is None:
                        continue
                    sa, va = samples[a][0], samples[a][1]
                    sb, vb = samples[b][0], samples[b][1]
                    if sb - sa > 15.0:
                        continue
                    t = (si - sa) / (sb - sa)
                    # 0.8 ft off a 15-ft chord: more than a ground line bends
                    # (a 3:1 ditch bank over 15 ft sags 0.5 ft), less than a
                    # leader's tail as it converges onto the line
                    if abs(samples[i][1] - (va + t * (vb - va))) > 0.8:
                        drop.add(i)
                for i in drop:
                    samples[i] = [samples[i][0], None, None]
                if not drop:
                    break
        n_hit = sum(1 for _, lo, _ in samples if lo is not None)
        if n_hit < 3:
            continue
        layer, col, wid, dashed = k
        cov = n_hit / n_in
        if cov < MIN_COVERAGE:
            continue
        # a dashed CAD line often reaches the PDF as short solid pieces with
        # real gaps: coverage well under 1 inside its own span says so
        if not dashed and cov < 0.8:
            dashed = True
        lines.append(ProfileLine(
            pen=f"{layer}|{col}|{wid}|{'dashed' if dashed else 'solid'}",
            layer=layer, color=list(col), width=wid, dashed=dashed,
            role="", kind="", label=None, coverage=cov, sta0=p0, sta1=p1,
            two_line=two, od_ft=od, samples=samples))
    if not lines:
        return ProfileRead(region, step_ft, stations, [], [], [],
                           ["no line inside the profile box spans enough of its stationing to be a ground line or a pipe"],
                           station=st, elevation=el)

    # ---- roles. Pipes are two-line pens; surfaces are single-line pens that
    # sit above every pipe. A single-line pen far below the topmost single
    # line is a pipe drawn as one line.
    def med_low(ln: ProfileLine) -> float:
        v = [lo for _, lo, _ in ln.samples if lo is not None]
        return float(np.median(v)) if v else -1e9

    singles = [ln for ln in lines if not ln.two_line]
    twos = [ln for ln in lines if ln.two_line]
    surfaces: list[ProfileLine] = []
    pipes: list[ProfileLine] = list(twos)
    if singles:
        top = max(med_low(ln) for ln in singles)
        for ln in singles:
            # within 6 ft of the highest single line: a ground or grade
            # line (existing and finished sit a few feet apart)
            if med_low(ln) >= top - 6.0:
                surfaces.append(ln)
            else:
                pipes.append(ln)
    for ln in surfaces:
        ln.role = "surface"
    for ln in pipes:
        ln.role = "pipe"
        ln.kind = "pipe"

    # ---- labels beside the lines (the sheet's own words win over the dash
    # convention)
    try:
        tlines = page_lines(page)
    except Exception:
        tlines = []
    near = []
    for tl in tlines:
        try:
            tx0, ty0, tx1, ty1 = float(tl[0]), float(tl[1]), float(tl[2]), float(tl[3])
            txt = str(tl[4]).strip()
        except (TypeError, ValueError, IndexError):
            continue
        if tx1 < bx0 - 80 or tx0 > bx1 + 80 or ty1 < by0 - 80 or ty0 > by1 + 80:
            continue
        near.append((tx0, ty0, tx1, ty1, txt))
    # Each LABEL goes to the line it POINTS AT: the leader stroke drawn from
    # the label box to the line (one end at the box, the tip elsewhere) puts
    # the tip on the line; a label with no leader must sit on the line
    # itself. Never "the nearest line to the label": on the synthetic test
    # sheet the EXISTING GROUND label sits nearer the finished grade than
    # the ground its leader reaches.
    def line_gap(ln, x, y):
        """vertical distance (pt) from (x, y) to the pen at that station"""
        s = sta_at(x)
        vals = [(ss, v[0]) for ss, *v in ln.samples if v[0] is not None]
        if not vals or s < vals[0][0] - 2 or s > vals[-1][0] + 2:
            return None
        j = int(np.argmin([abs(ss - s) for ss, _ in vals]))
        return abs(y - (el.pos0 + (vals[j][1] - el.val0) / el.ft_per_pt))
    claimed: dict = {}
    for tx0, ty0, tx1, ty1, txt in near:
        role = "surface" if _SURF_WORD.search(txt) else "pipe" if _PIPE_WORD.search(txt) else None
        if role is None:
            continue
        # the leader: a short stroke with one end at the label box
        tips = []
        for u in units:
            p = u.pts
            if len(p) < 2 or u.length < 12.0 or u.length > 400.0:
                continue
            for a, b in ((p[0], p[-1]), (p[-1], p[0])):
                if tx0 - 14 <= a[0] <= tx1 + 14 and ty0 - 14 <= a[1] <= ty1 + 14 \
                        and not (tx0 - 6 <= b[0] <= tx1 + 6 and ty0 - 6 <= b[1] <= ty1 + 6):
                    tips.append((float(b[0]), float(b[1])))
        cands = tips if tips else [((tx0 + tx1) / 2, (ty0 + ty1) / 2)]
        reach = 10.0 if tips else 16.0
        best = None
        for x, y in cands:
            for ln in lines:
                if ln.role != role:
                    continue
                g = line_gap(ln, x, y)
                if g is not None and g <= reach and (best is None or g < best[0]):
                    best = (g, ln)
        if best and (id(best[1]) not in claimed or best[0] < claimed[id(best[1])]):
            claimed[id(best[1])] = best[0]
            best[1].label = txt.strip()[:60]
    for ln in surfaces:
        lab = (ln.label or "").upper()
        if re.search(r"EXIST", lab):
            ln.kind = "existing"
        elif re.search(r"FINAL|FINISH|PROPOSED|\bFG\b", lab):
            ln.kind = "finished"
        else:
            ln.kind = "existing" if ln.dashed else "finished"
    # finished grade first (the surface the trench is dug from once the road
    # is built); existing after it; the operator can switch. Within a kind,
    # and among the pipes, the pen with the most COVERED stationing leads: a
    # hairline that reaches across the whole graph at 15 % coverage is not
    # the pipe, the two heavy lines at 80 % are.
    covered = lambda ln: ln.coverage * (ln.sta1 - ln.sta0)               # noqa: E731
    surfaces.sort(key=lambda ln: (0 if ln.kind == "finished" else 1, -covered(ln)))
    pipes.sort(key=lambda ln: -covered(ln))

    # ---- interpolate a surface across its own small gaps (a dashed pen's
    # dashes; a solid pen broken by a label mask; the stations a tracker
    # refused). A solid unbroken line has no gaps and is unchanged.
    for ln in surfaces:
        idx = [i for i, (_, lo, _) in enumerate(ln.samples) if lo is not None]
        for a, b in zip(idx, idx[1:]):
            if b - a <= 1:
                continue
            sa, la, ha = ln.samples[a]
            sb, lb, hb = ln.samples[b]
            if sb - sa > DASH_GAP_FT:
                continue
            for i in range(a + 1, b):
                t = (ln.samples[i][0] - sa) / (sb - sa)
                ln.samples[i] = [ln.samples[i][0], la + t * (lb - la), ha + t * (hb - ha)]

    # ---- printed elevations vs the read
    checks = []
    def read_at(ln: ProfileLine, s: float, which: int):
        """The pen's elevation at station s (linear between samples)."""
        vals = [(ss, v[which]) for ss, *v in ln.samples if v[which] is not None]
        if not vals:
            return None
        if s < vals[0][0] - 2.0 or s > vals[-1][0] + 2.0:
            return None
        for (s0, v0), (s1, v1) in zip(vals, vals[1:]):
            if s0 <= s <= s1:
                return v0 + (v1 - v0) * (s - s0) / (s1 - s0) if s1 > s0 else v0
        return vals[0][1] if s < vals[0][0] else vals[-1][1]
    targets = [(f"pipe invert ({p.layer or p.pen})", p, 0) for p in pipes] + \
              [(f"{s.kind} surface ({s.layer or s.pen})", s, 0) for s in surfaces]
    def compare(sta, printed, kind, text):
        best = None
        for name, ln, which in targets:
            v = read_at(ln, sta, which)
            if v is None:
                continue
            d = v - printed
            if best is None or abs(d) < abs(best["diff"]):
                best = {"line": name, "read": round(v, 3), "diff": round(d, 3)}
        row = {"kind": kind, "sta": round(sta, 2), "printed": printed, "text": text[:80]}
        row.update(best or {"line": None, "read": None, "diff": None})
        checks.append(row)
    # callouts naming a station and an elevation in one line of text
    for tx0, ty0, tx1, ty1, txt in near:
        ms, me = _STA_IN_TEXT.search(txt), _ELEV_IN_TEXT.search(txt)
        if ms and me:
            sta = int(ms.group(1)) * 100 + float(ms.group(2))
            if s_lo - 5 <= sta <= s_hi + 5:
                compare(sta, float(me.group(1)), "callout", txt)
    # the elevation column along the foot / head of the graph: a bare number
    # in the axis' range whose x is a station inside the pipe's span
    vlo = min(el.val0, el.val1) - 15
    vhi = max(el.val0, el.val1) + 15
    words = _dedupe(page_words(page))
    for w in words:
        m = _ELEV_WORD.match(str(w[4]).strip())
        if not m:
            continue
        v = float(m.group(1))
        if not (vlo <= v <= vhi):
            continue
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        if cx < bx0 or cx > bx1 or cy < by0 - 60 or cy > by1 + 100:
            continue
        sta = sta_at(cx)
        compare(sta, v, "column", str(w[4]).strip())
    # ---- the graph's title, and the system it names: the PROFILE line
    # printed under the box (BR-STORM EAST PROFILE) or, on a plan-and-profile
    # sheet, the sheet title (WATER MAIN PLAN AND PROFILE). A run of another
    # system must not take this graph's depths.
    title, system = "", ""
    from .runs import system_code
    cands = []
    for tx0, ty0, tx1, ty1, txt in near:
        if not re.search(r"(?i)\bPROFILE\b", txt) or len(txt) > 60:
            continue
        cx = (tx0 + tx1) / 2
        if cx < bx0 - 40 or cx > bx1 + 40:
            continue
        # under the graph within 90 pt, else above it within 60
        if by1 <= ty0 <= by1 + 90:
            cands.append((0, ty0 - by1, txt))
        elif by0 - 60 <= ty1 <= by0:
            cands.append((1, by0 - ty1, txt))
    if cands:
        cands.sort()
        title = cands[0][2].strip()[:60]
        system = system_code(title)
        if system == "RUN":
            system = ""
    notes = []
    if not pipes:
        notes.append("no pipe found in the profile (no pen drawn as two parallel lines a "
                     "pipe-diameter apart, or as one line below the ground) - depths "
                     "cannot be read here")
    if not surfaces:
        notes.append("no ground or grade line found in the profile")
    if len(surfaces) > 1:
        notes.append("two surface lines: " + " / ".join(
            f"{s.kind} ({s.label or ('dashed' if s.dashed else 'solid')})" for s in surfaces)
            + " - the finished grade is used unless you switch")
    return ProfileRead(region, step_ft, stations, surfaces, pipes, checks, notes,
                       station=st, elevation=el, title=title, system=system)


def depth_series(read: ProfileRead, surface_i: int = 0, pipe_i: int = 0) -> list:
    """[station, depth_ft] where both the chosen surface and the pipe's invert
    exist. Depth = surface - invert (outside bottom of the pipe as drawn);
    a pipe above its surface reads 0 and is counted in the caller's note."""
    if not read.surfaces or not read.pipes:
        return []
    if surface_i >= len(read.surfaces) or pipe_i >= len(read.pipes):
        return []
    s, p = read.surfaces[surface_i], read.pipes[pipe_i]
    out = []
    for (st, slo, _), (_, plo, _) in zip(s.samples, p.samples):
        if slo is None or plo is None:
            continue
        out.append([st, max(slo - plo, 0.0)])
    return out
