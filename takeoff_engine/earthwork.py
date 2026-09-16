"""Cut & fill volumes from labeled contour lines (Delaunay TIN).

The estimator's question: between the EXISTING grade and the PROPOSED
grade, how much material comes out (cut) and how much goes in (fill)?

Model: each surface is a set of contour polylines, each carrying one
elevation in feet. Sampled points of every contour become a Delaunay TIN
(scipy LinearNDInterpolator = piecewise-linear surface). Volumes integrate
the difference over a grid restricted to where BOTH surfaces are defined -
extrapolating beyond a TIN's hull would invent terrain, and invented
terrain is invented truckloads.

Units: page geometry in pt, elevations in ft. ft_per_pt converts plan
distance; volumes report in cubic yards (ft^3 / 27).
"""

import re
from dataclasses import dataclass, field

import numpy as np
from scipy.interpolate import CloughTocher2DInterpolator, LinearNDInterpolator
from scipy.spatial import cKDTree

from .chain import _sample_polyline


@dataclass
class Surface:
    name: str
    contours: list = field(default_factory=list)  # (pts Nx2 pt-space, z_ft)
    points: list = field(default_factory=list)    # (x_pt, y_pt, z_ft) spot grades
    # why interpolator() returned None when the sample count was enough -
    # "collinear" today; None while the surface is fine or merely short
    degenerate: "str | None" = None

    def samples(self, step_pt: float = 6.0):
        """(P Nx2 page pt, Z N ft) - every contour sample plus every spot.

        Split out of interpolator() so the clamped-smooth model can reuse
        the SAME point set the linear TIN is built from; the two models
        must never disagree about what the sheet said.
        """
        xs, zs = [], []
        for pts, z in self.contours:
            s, _ = _sample_polyline(np.asarray(pts, float), step=step_pt,
                                    max_n=250000)
            if len(s):
                xs.append(s)
                zs.append(np.full(len(s), float(z)))
        # spot grades are single points, not polylines: a surveyed FG/TS/TW
        # elevation at one location. On detailed grading sheets they vastly
        # outnumber labelled contours (Barn C3.4: 45 spot grades vs 2 labelled
        # contours), so a TIN built from them is often the ONLY surface the
        # sheet can give.
        if self.points:
            P2 = np.asarray([[float(x), float(y)] for x, y, _ in self.points],
                            dtype=float)
            xs.append(P2)
            zs.append(np.asarray([float(z) for _, _, z in self.points],
                                 dtype=float))
        if not xs:
            return None, None
        P = np.vstack(xs)
        Z = np.concatenate(zs)
        if len(P) < 3:
            return None, None
        return P, Z

    def interpolator(self, step_pt: float = 6.0):
        P, Z = self.samples(step_pt)
        if P is None:
            return None, None
        # ONE LINE IS NOT A SURFACE. Three or more samples that all lie on
        # a single straight line - one straight contour, spot grades along
        # a kerb - pass the count floor and then blow up inside Qhull
        # ("initial simplex is flat"), which the route served as a bare
        # 500. Reproduced 2026-09-08 on Barn C3.0 with one straight chain a
        # side and with three collinear spot grades. Measured here, before
        # scipy sees it, and remembered on the surface so the refusal can
        # name it (_floor_refusal).
        if _collinear(P):
            self.degenerate = "collinear"
            return None, None
        return LinearNDInterpolator(P, Z), P


# --- surface model: piecewise-linear TIN vs CLAMPED smooth ----------------
#
# The default model is and remains the piecewise-linear TIN. A linear TIN
# through contour samples is EXACT ON the contours and regresses everything
# between them toward a plane, so on a sheet whose labelled contours are far
# apart in elevation it terraces the site and under-reads both cut and fill.
# Measured (out/verify/2026-08-31/terrain-audit): Stice p0 reads cut -24.4%
# and fill -28.6% against that sheet's OWN printed RAW QUANTITIES table.
#
# CloughTocher2DInterpolator is scipy's C1-smooth cubic on the same Delaunay
# triangulation, and it restores the between-contour relief. RAW CT IS
# DISQUALIFIED BY MEASUREMENT and must never ship: on Stice it rings to a
# 1,242 ft max dig on a 425-521 ft site (cut +180%), and on Oncor's
# retaining-wall bundles (1-ft contours 2-3 pt apart) it takes fill from
# 7,979 to 13,979 CY (+75%) with 71 ft of phantom fill.
#
# What ships is CT CLAMPED per evaluated point to the z-range of the
# CLAMP_K nearest contour samples. The clamp is what makes the model safe:
# curvature between contours is kept, but an elevation outside the local
# contour bracket cannot be invented, because the bracket is read off the
# sheet's own labels. Measured on Stice p0: cut 1,690,910 -> 2,015,688
# (-24.4% -> -9.9%) and fill 1,164,619 -> 1,484,150 (-28.6% -> -9.0%),
# both moving toward the printed table simultaneously - the signature of a
# systematic terracing bias being removed rather than noise.
#
# k = 12 is the audit's own bracket width, used unchanged so the shipped
# numbers reproduce its measurement exactly. It is a LOCAL bound, so it can
# slightly under-state ringing in a mixed-elevation neighbourhood; the
# volume rows, not the clamp, are the evidence that it holds.
CLAMP_K = 12

# Above this median nearest-distinct-z band, "auto" picks the clamped
# smooth model. Chosen from the separation the two reference jobs actually
# show, and it is a two-job denominator - say so wherever it decides:
#   Oncor C-04  median band 1.0 ft existing / 1.0 ft proposed  -> linear
#   Stice p0    median band 3.2 ft existing / 4.7 ft proposed  -> clamped
# The direction is the audit's counter-measurement, not a preference: on
# Oncor, whose contours are dense, linear is the BETTER surface pointwise -
# against the sheet's 20 printed proposed spot elevations linear scores
# 0.266 ft mean |err| and clamped CT 0.352. Smoothing helps where the
# labels are sparse and is slightly negative where they are dense, so the
# gate is on measured label sparsity and the operator can always override.
AUTO_BAND_FT = 2.0

# Probe budget for the band statistic. Measured stable to the reported
# digit across stride 1..41 (1,872,973 cells down to 1,105) and k 8..48 on
# both reference jobs, so a bounded probe is not an approximation that
# could flip the decision.
_BAND_PROBE_MAX = 20000

SURFACE_MODELS = ("auto", "linear", "clamped_ct")


# --- THE VOLUME FLOOR: what a surface must carry before a volume ships ---
#
# A contour surface is a CLAIM ABOUT TERRAIN. Until 2026-09-02 the only
# entry test cut_fill applied was "the interpolator built", which one
# polyline with 3+ sampled points satisfies - and a Delaunay TIN over N
# samples that all carry the SAME elevation is a horizontal plane. Nothing
# on the sheet says the ground is flat there; the plane is the
# interpolator's invention, and every cubic yard measured against it is
# invented with it.
#
# Reproduced live on the DEM-truth outline sheet
# (dem_hurricane_bench_a_pad_2ft_outline p0, whose contour interval is
# 2 ft): POST /earthwork with existing=[{chain_id 205, z 324}] and
# proposed=[{chain_id 214, z 826}] returned fill 17,997.6 CY over
# area_sf 968 - 502 ft of fill in a 31 ft x 31 ft footprint - with
# existing_contours 1 and proposed_contours 1 printed beside it as if that
# were a basis.
#
# MIN_DISTINCT_Z = 2 is the smallest number that can describe a slope: one
# elevation is a plane, two are a gradient the sheet actually printed. It
# counts CONTOUR ROWS AND SPOT GRADES TOGETHER - a detailed grading sheet
# often prints one contour and a crowd of FG/TS elevations (Barn C3.4: 45
# spots against 2 labelled contours), and a floor that counted contours
# alone would refuse that sheet.
#
# MEASURED OVER EVERY SURFACE PAIR REACHABLE ON THIS BOX, IN EVERY PANEL
# CONFIGURATION AN OPERATOR CAN PRODUCE. The first version of this block
# quoted one configuration and did not say so, which made the floor look far
# gentler than it is. What varies is where the SPOT GRADES go, and that is
# two clicks on the panel (webui/app.js: state.ew.spotSide, and the per-tag
# checkboxes state.ew.spotOn):
#   contours      no spot grades at all - every tag unticked, or a sheet
#                 that offers none
#   spots_pr      the panel's DEFAULT: spotSide "proposed", tags FG/ME/EG/PE
#   spots_ex      one click on EXIST - the same tags, existing surface
#   allspots_pr   every RESOLVED spot regardless of tag, onto proposed. This
#                 is what the first measurement used, and it is the ONLY one
#                 of the five in which the floor refuses nothing real.
#   allspots_ex   the same, onto existing
#
# 28 sheets driven live open -> detect -> /earthwork/suggest, plus the Oncor
# C-04 fixture and the Stice p0 recipe: 30 pairs, of which 14 have rows on
# BOTH sides and so can produce a volume at all. The other 16 offer no
# proposed rows (existing-conditions maps, demo and erosion sheets, the two
# Belle Farms road sheets, and the OCR-only pages the ladder gate empties)
# and are refused before and after, unchanged.
#
# WHICH OF THE 14 THE FLOOR REFUSES, per configuration:
#   contours       4 refused: Barn C4.1-NW, C4.2-NE, C4.4-SE and Home2
#                             Suites C3 - each with a proposed surface of
#                             ONE distinct elevation
#   spots_pr       3 refused: Barn C4.1-NW, C4.2-NE, C4.4-SE (Home2's one
#                             FG spot lands on proposed and rescues it)
#   spots_ex       4 refused: those three + Home2 Suites C3
#   allspots_pr    0 refused
#   allspots_ex    4 refused: those three + Home2 Suites C3
# So the floor refuses THREE real client grading sheets under the panel's
# own default, and a fourth in two of the five configurations - not "one of
# twelve". Those refusals are correct on the merits: in each of them the
# proposed surface is a single distinct elevation, which is exactly the flat
# plane this floor exists to stop. What was wrong was the evidence.
#
# Smallest surfaces that PASS, by distinct elevations:
#   Barn C4.3-SW proposed        2  (3 rows, relief 1.0 ft) - the tightest
#   Barn C4.1-NW proposed        2  (1 contour + 1 FF spot, relief 3.52 ft)
#                                   in allspots_pr only; the FF tag is not
#                                   in the panel's default set, so under
#                                   spots_pr that spot lands on neither side
#   Barn C4.4-SE proposed        2  (allspots_pr, relief 1.48 ft)
#   Barn C4.0 proposed           3
#   dem_pad_5ft proposed         3
#   ... every other surface      4 .. 356
# Two is therefore still the right count floor: three real sheets sit
# exactly on it and a floor of 3 would refuse all of them.
#
# The eleven pairs that pass under the panel default pass with their volumes
# unchanged to the digit (tests/test_earthwork_regress.py, 29 cases; the DEM
# control sheet byte-compared).
MIN_DISTINCT_Z = 2

# ...AND THE COUNT ALONE IS NOT THE TEST. "2 distinct elevations" is a
# COUNT; what makes a surface terrain rather than a plane is RELIEF, and two
# values a ten-thousandth of a foot apart satisfy the count while describing
# the same horizontal plane MIN_DISTINCT_Z exists to refuse. Reproduced live
# 2026-09-02 on dem_hurricane_bench_a_pad_2ft_outline p0 (:9904, this
# branch): existing=[{205, 324.0}, {220, 324.0001}] and
# proposed=[{214, 826.0}, {222, 826.0001}] answered HTTP 200,
# fill 3,367,118.5 CY over area_sf 181,100 - both surfaces reported
# distinct_elevations 2 and relief_ft 0.0.
#
# MIN_RELIEF_FT = 0.01 ft, and it is the finest thing this corpus can print.
# Census of every elevation the readers recovered over the 28-sheet sweep
# below plus the two fixtures - 578 values: 384 carry no decimal, 49 carry
# one, 145 carry two, NONE carries three. The smallest step between two
# distinct elevations on any one sheet:
#   Town Pump #3 C4.00 Grading Map p0   0.01 ft   <- the finest in the corpus
#   Belle Farms ROAD GRADING 2 of 2 p0  0.02 ft
#   dem_road_2ft p0                     0.2 ft
#   Oncor C-04 fixture / Stice p0       0.1 ft
#   every other sheet                   0.2 .. 2 ft
# So 0.01 ft is EXACTLY one printed increment on the finest sheet here: two
# adjacent printed values on Town Pump still pass (0.01 >= 0.01), and a span
# below it cannot be two different numbers a sheet printed. It can only be
# one label read twice, a digit misplaced, or a value nothing drew.
#
# WHAT IT REFUSES, measured over every surface pair reachable (28 live
# sheets through /earthwork/suggest + the Oncor C-04 fixture and the Stice
# p0 recipe, each in all five panel configurations - see MIN_DISTINCT_Z's
# block): NOTHING REAL. The smallest relief on any surface that passes the
# count floor is 1.0 ft (Barn C4.3-SW proposed: 3 rows, 2 distinct, in the
# contours-only, spots-on-proposed and spots-on-existing configurations),
# then 1.48 ft (Barn C4.4-SE proposed, all-spots-on-proposed), 3.1 ft (Town
# Pump existing), 3.52 ft (Barn C4.1-NW proposed, all-spots-on-proposed -
# the "1 contour + 1 FF spot" case), and everything else 4 ft to 230 ft.
# The floor is 100x below the tightest legitimate surface measured and 100x
# above the adversary's 0.0001 ft; the only thing it refuses anywhere in the
# corpus is that adversary shape, in all five configurations.
#
# It makes MIN_DISTINCT_Z arithmetically redundant - one elevation has
# relief 0.0, which is below this - and MIN_DISTINCT_Z is kept anyway, for
# its SENTENCE: "carries only ONE elevation (324 ft), assign a contour at a
# different elevation" is something an operator can act on, and "spans only
# 0 ft" is not. Both refusals carry error_code surface_no_relief, because
# they are the same finding; `surface.distinct_elevations` and
# `surface.relief_ft` in the detail say which one fired.
#
# WHAT IT DOES NOT ADDRESS, stated because the adversary's number invites
# it: the 3.37M CY in that reproduction is large because the two surfaces
# sit 502 ft apart, not because either is flat. Disjoint elevation ranges
# stay DISCLOSED and never refused (surface_basis.caution, and see the
# rejected candidates below) - that decision is unchanged.
MIN_RELIEF_FT = 0.01

# CANDIDATES MEASURED AND REJECTED, so the next session does not re-derive
# them (same folder, same 15 pairs):
#  * "the surface's internal elevation gaps must be <= K x the sheet's
#    contour interval". REFUTED BY STICE. contour_interval reads Stice p0's
#    interval as 0.1 ft (design labels carry a decimal, and 0.1 is the modal
#    step over 331 deltas) while its largest internal gap is 84.5 ft - a
#    ratio of 845. Oncor reads 5, the DEM sheets 1-6. No K keeps Stice and
#    refuses anything.
#  * "the two surfaces' elevation ranges must overlap". Every one of the 11
#    legitimate pairs measures a gap of exactly 0.0 ft and both adversary
#    shapes measure 502 and 116, so on THIS corpus it separates perfectly -
#    but the corpus contains no pure-fill embankment and no deep-cut pad,
#    which are ordinary jobs whose two surfaces legitimately do not overlap.
#    A guard that refuses real work is worse than the hole, so disjoint
#    ranges are DISCLOSED (surface_basis.caution, rendered beside the
#    volume) and never refused. Unlike a single-elevation plane, a disjoint
#    pair is suspicious rather than baseless: every dz it produces is still
#    a difference of two elevations the sheet printed.


def surface_basis(surf: "Surface") -> dict:
    """What one surface actually rests on - counted, never inferred.

    Returned on every volume (result["surface_basis"]) and quoted back in
    the refusal when the floor stops one, so the operator is told WHICH
    surface and WHAT it found rather than "each surface needs at least one
    labeled contour".
    """
    zc = [float(z) for _pts, z in surf.contours]
    zp = [float(z) for _x, _y, z in surf.points]
    zs = sorted({round(v, 4) for v in zc + zp})
    return {"name": surf.name,
            "n_contours": len(zc), "n_spots": len(zp),
            "distinct_elevations": len(zs),
            "elevations_ft": (zs if len(zs) <= 12
                              else zs[:6] + ["..."] + zs[-6:]),
            "z_min_ft": (zs[0] if zs else None),
            "z_max_ft": (zs[-1] if zs else None),
            # FOUR decimals, not three. This is the number MIN_RELIEF_FT is
            # tested against, and rounding it to 3 reported the 0.0001 ft
            # adversary surface as "relief_ft 0.0" while the count floor
            # passed it and a volume shipped. `zs` is already rounded to 4,
            # so nothing here invents precision it did not have.
            "relief_ft": (round(zs[-1] - zs[0], 4) if len(zs) > 1 else 0.0)}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


# RMS distance, in page pt, that a sample set may sit off its own best-fit
# line before it counts as having plan area. Qhull's own tolerance is
# relative machine precision; anything under a millionth of a point is
# not a drawn shape, it is one line sampled.
_COLLINEAR_RMS_PT = 1e-6


def _collinear(P: np.ndarray) -> bool:
    """True when every point in P (Nx2) lies on one straight line."""
    P = np.asarray(P, float)
    if len(P) < 3:
        return True
    Q = P - P.mean(axis=0)
    s = np.linalg.svd(Q, compute_uv=False)
    # s[1]**2 = sum of squared perpendicular distances to the best line
    return float(s[1]) / np.sqrt(len(P)) < _COLLINEAR_RMS_PT


def _floor_refusal(surf: "Surface", interp_ok: bool):
    """None when this surface may produce a volume, else the refusal dict."""
    b = surface_basis(surf)
    if not surf.contours and not surf.points:
        return {"error": f"the {b['name']} surface has no contours and no "
                         f"spot grades assigned - there is nothing to "
                         f"interpolate",
                "error_code": "surface_empty", "surface": b}
    if not interp_ok and getattr(surf, "degenerate", None) == "collinear":
        return {"error": f"the {b['name']} surface's samples "
                         f"({_plural(b['n_contours'], 'contour')}, "
                         f"{_plural(b['n_spots'], 'spot grade')}) all lie "
                         f"on ONE straight line, so they enclose no area "
                         f"and no surface can be built between them. "
                         f"Assign {b['name']} a second contour that is not "
                         f"on the same line, or spot grades off it.",
                "error_code": "surface_collinear", "surface": b}
    if not interp_ok:
        return {"error": f"the {b['name']} surface has fewer than 3 sample "
                         f"points ({_plural(b['n_contours'], 'contour')}, "
                         f"{_plural(b['n_spots'], 'spot grade')}) - a TIN "
                         f"needs three points to exist",
                "error_code": "surface_too_few_samples", "surface": b}
    if b["distinct_elevations"] < MIN_DISTINCT_Z:
        z = b["z_min_ft"]
        return {"error": f"the {b['name']} surface carries only ONE "
                         f"elevation ({z:g} ft) across "
                         f"{_plural(b['n_contours'], 'contour')} and "
                         f"{_plural(b['n_spots'], 'spot grade')}. The "
                         f"surface between them is a flat plane this tool "
                         f"invented, not ground the sheet drew, so no "
                         f"volume is published. Assign a contour at a "
                         f"DIFFERENT elevation to {b['name']}, or add spot "
                         f"grades that are not all {z:g} ft.",
                "error_code": "surface_no_relief", "surface": b}
    # THE COUNT TEST CANNOT SEE A PLANE THAT COUNTS TO TWO (see
    # MIN_RELIEF_FT). Same guard, same code - the operator is being told the
    # same thing, that this surface has no relief - but the sentence has to
    # name the SPAN, because "you have two elevations" is not the problem.
    if b["relief_ft"] < MIN_RELIEF_FT:
        return {"error": f"the {b['name']} surface carries "
                         f"{_plural(b['distinct_elevations'], 'elevation')} "
                         f"({b['z_min_ft']:.10g} to "
                         f"{b['z_max_ft']:.10g} ft) but "
                         f"they span only {b['relief_ft']:g} ft - less than "
                         f"the {MIN_RELIEF_FT:g} ft finest increment any "
                         f"sheet prints, so this is a flat plane whatever "
                         f"the digits say and no volume is published. Check "
                         f"those readings against the drawing: two "
                         f"elevations that close are one label read twice "
                         f"or a digit misplaced, not a slope.",
                "error_code": "surface_no_relief", "surface": b}
    return None


def _basis_pair(existing: "Surface", proposed: "Surface") -> dict:
    """surface_basis for both sides plus the disclosed range comparison."""
    be, bp = surface_basis(existing), surface_basis(proposed)
    out = {"existing": be, "proposed": bp, "ranges_overlap": True,
           "range_gap_ft": 0.0, "caution": None}
    if be["z_min_ft"] is None or bp["z_min_ft"] is None:
        return out
    lo = max(be["z_min_ft"], bp["z_min_ft"])
    hi = min(be["z_max_ft"], bp["z_max_ft"])
    if hi >= lo:
        return out
    gap = round(lo - hi, 3)
    out.update(ranges_overlap=False, range_gap_ft=gap,
               caution=(f"the two surfaces do not overlap in elevation: "
                        f"existing runs {be['z_min_ft']:g}-{be['z_max_ft']:g}"
                        f" ft and proposed {bp['z_min_ft']:g}-"
                        f"{bp['z_max_ft']:g} ft, a {gap:g} ft gap that no "
                        f"contour on either surface crosses, so every cell "
                        f"is cut (or filled) by at least that much. That is "
                        f"normal on a raised pad or a deep excavation and "
                        f"WRONG when a row was assigned to the wrong "
                        f"surface or a digit was misread - check the rows "
                        f"before pricing."))
    return out


# --- LADDER: is a set of elevations shaped like drawn contours? ----------
#
# THE HOLE THIS CLOSES (measured 2026-09-02). Every consensus filter this
# codebase had keys on NATIVE text: the native band gate in
# suggest_elevations needs 3+ glyph-derived values, server.py's `wit`
# filter needs 6+ glyph or resolved-spot values, and its group ranking
# keeps a band only when the band holds 2+ rows or a glyph-backed one. On a
# sheet whose every label is a vector outline there are none of those, so
# all three are inert and a gap-OCR population of ANY shape became rows.
# Overwriting the DEM outline sheet's gap cache with three crafted reads
# (324 / 710 / 826) on real chains gave 3 rows, all how="gap-ocr",
# suggestions_dropped 0 - three "surfaces" 500 ft apart on a sheet whose
# contour interval is 2 ft. Those three values are not invented for the
# test: the v2 OCR pass read 826/710/324 out of dimension texts on Barn
# C3.0, and the native band gate is the only thing that stopped them there.
#
# WHAT IS REFUTABLE WITH NO NATIVE TEXT AT ALL: contours are drawn at a
# CONSTANT interval (general drafting practice, and every sheet in this
# corpus states its own in a note), so a population read off them steps by
# that interval - or a small multiple of it where a label was missed - OVER
# AND OVER. A set whose every adjacent gap differs is not a contour ladder,
# whatever the digits say. That test needs nothing but the values.
#
# WHAT THE NUMBERS BELOW WERE MEASURED ON, and how the first attempt got it
# wrong. ladder_verdict runs INSIDE suggest_elevations, over every row the
# readers produced, BEFORE server.py's `wit` filter and its glyph-first
# group ranking ever see them. The first version of this comment was written
# against the values the ROUTE returns, which is the population AFTER those
# filters - a different set on every page where anything is filtered - and
# it described Barn C0.00 p0 as "{464, 535}, 2 distinct, NOT TESTABLE, rows
# kept". Live through /earthwork/suggest that page reports n_values 4,
# testable true, interval 71 ft, repeats 1/3, laddered FALSE, dropped 6,
# kept 0: every row dropped, not kept. The numbers here are now read off
# `ocr_ladder.verdict`, which IS the pre-filter measurement.
#
# THE DENOMINATOR. A 30-page sweep (28 sheets through the live open ->
# detect -> /earthwork/suggest path, plus Oncor engine pages 3 and 4) finds
# NINE pages where the gate fires - a page qualifies only when NO row on it
# is glyph-backed (inline/radius). The first count said three.
#
# LADDER_MIN_REPEATS = 2. "repeats" = how many adjacent gaps equal the modal
# gap. Every OCR-only page in that sweep, and the two crafted shapes:
#
#   LADDERS (rows kept)
#     Oncor engine p4          46 distinct, step 1 ft, repeats 42/45,
#                              share 0.93 -> all 106 rows kept
#     dem_pad_2ft_outline p0   16 distinct, step 2 ft, repeats  9/15,
#                              share 0.60 -> all 17 rows kept (this is the
#                              fix that recovered them in the first place)
#     Plans2 C2.1 Grading p0   11 distinct, step 1 ft, repeats  8/10,
#                              share 0.80 -> all 18 rows kept
#     the 3-row OCR fixture    {3380, 3382, 3384}, repeats 2/2, share 1.00
#                              -> LADDER. Not a corpus page: it is pinned by
#                              tests/test_gap_ocr_rows.py, and it is the
#                              reason 2 rather than 8 is the floor.
#   NOT LADDERS (every row dropped)
#     Barn C0.00 p0             4 distinct, step 71 ft, repeats 1/3, 0.33
#     Barn C2.1 Demo p0         5 distinct, step 52 ft, repeats 1/4, 0.25
#     Ernies Too C2.1 Demo p0   3 distinct, step  3 ft, repeats 1/2, 0.50
#     Ernies Too C4 Grading p0  4 distinct, step 265 ft, repeats 1/3, 0.33
#     Plans2 C4 GRADING p0      4 distinct, step 265 ft, repeats 1/3, 0.33
#                               ({145, 435, 700, 4004} - a grading plan
#                               cannot read 145 ft and 4004 ft at once)
#     remay C1.4 Grading p0     3 distinct, step  1 ft, repeats 1/2, 0.50
#     the crafted adversary     {324, 710, 826}, repeats 1/2, share 0.50
#   NOT TESTABLE (rows kept and disclosed - see _ladder_gate)
#     Home2 Suites C3 Grading   2 distinct {411, 444} -> 9 rows kept
#
# The separation `repeats` earns is 42, 9 and 8 against 1, 1, 1, 1, 1, 1 -
# far wider than the first measurement could see. It is `repeats` that earns
# it and not `share`: two of the six refused pages (Ernies Too C2.1 Demo and
# remay C1.4 Grading) measure share exactly 0.50, as does the crafted
# adversary, and LADDER_MIN_SHARE does not reject any of the three - only
# their single repeat does.
#
# WHY 2 AND NOT 8. On corpus pages alone anything in [2, 8] separates
# perfectly. 2 is kept because it is the smallest number that can
# demonstrate a repeat at all, and because the smallest population this
# codebase asserts must SURVIVE - the 3-value gap-OCR fixture, repeats 2/2 -
# sits exactly on it. Raising the floor to match the corpus margin would
# refuse a population whose every gap is the same step, which is the most
# ladder-shaped thing there is.
LADDER_MIN_REPEATS = 2
# ...and the modal step must still account for half the gaps, so a
# population that repeats one step twice by coincidence among many others is
# not called a ladder. Measured over the same nine pages: the ladders
# measure 0.93, 0.80 and 0.60, the non-ladders 0.50, 0.50, 0.50, 0.33, 0.33
# and 0.25. 0.50 therefore rejects NOTHING that `repeats` does not already
# reject (the three at exactly 0.50 are not below it) and clears the closest
# legitimate page, the outline sheet, by 0.10.
LADDER_MIN_SHARE = 0.5
# Splitting multiplier. Inside one ladder a gap larger than this is a
# different population, not a stretch of unlabelled contours. Largest
# internal gap on each of the three laddered corpus pages, in units of that
# page's own step: dem_pad_2ft_outline 12 ft = 6.0x, Oncor p4 3 ft = 3.0x,
# Plans2 C2.1 2 ft = 2.0x. All three come back n_bands 1, so no real page
# here is split at all; 8 leaves a third again above the worst of them, and
# is what bands a lone 5388 off a 3376-3382 ladder.
LADDER_BAND_MULT = 8.0


def ladder_verdict(values, min_repeats: int = LADDER_MIN_REPEATS,
                   min_share: float = LADDER_MIN_SHARE,
                   band_mult: float = LADDER_BAND_MULT) -> dict:
    """Does this set of elevations step like drawn contours?

    Pure: takes numbers, returns a verdict. `laddered` False means the
    population cannot be shown to step and nothing in it should mint a
    surface. `testable` False means it is too small to judge either way,
    and a caller must NOT read that as a refusal - see suggest_elevations,
    which keeps those rows and discloses them rather than inventing a
    verdict. The one such page in the sweep (Home2 Suites C3 Grading p0, 2
    distinct values {411, 444}, 9 rows kept) is then refused by the volume
    floor anyway, on its proposed side - but that is the floor's decision
    and not this function's, and a 2-value population landing both values
    on BOTH surfaces would be a legitimate one-step pair the floor passes.
    keep_lo/keep_hi bound the largest band when it IS a ladder.
    """
    v = sorted({round(float(x), 2) for x in values if x is not None})
    out = {"n_values": len(v), "testable": False, "laddered": False,
           "interval_ft": None, "repeats": 0, "n_gaps": 0, "share": None,
           "n_bands": 0, "keep_lo": None, "keep_hi": None, "why": ""}
    if len(v) < 3:
        out["why"] = (f"{_plural(len(v), 'distinct elevation')} - too few to "
                      f"show a repeating contour interval either way")
        return out
    d = np.round(np.diff(np.asarray(v, dtype=float)), 2)
    d = d[d > 1e-6]
    if not len(d):
        out["why"] = "the elevations do not step"
        return out
    vals, cnt = np.unique(d, return_counts=True)
    i = int(cnt.argmax())
    step, m = float(vals[i]), int(cnt[i])
    share = round(float(m) / len(d), 3)
    out.update(testable=True, interval_ft=step, repeats=m,
               n_gaps=int(len(d)), share=share)
    if m < min_repeats or share < min_share:
        out["why"] = (f"these {len(v)} elevations do not step like contours: "
                      f"the commonest gap between adjacent values is "
                      f"{step:g} ft and it occurs {m} time"
                      f"{'' if m == 1 else 's'} in {len(d)} ({share:.0%}). "
                      f"Contours are drawn at a constant interval, so a "
                      f"population read off them repeats one step at least "
                      f"{min_repeats} times and over {min_share:.0%} of the "
                      f"gaps.")
        return out
    cuts = [k + 1 for k in range(len(d)) if d[k] > band_mult * step]
    edges = [0] + cuts + [len(v)]
    bands = [(edges[k], edges[k + 1]) for k in range(len(edges) - 1)]
    a, b = max(bands, key=lambda ab: (ab[1] - ab[0], v[ab[1] - 1] - v[ab[0]]))
    out.update(laddered=True, n_bands=len(bands),
               keep_lo=v[a], keep_hi=v[b - 1],
               why=(f"steps by {step:g} ft ({m} of {len(d)} gaps, "
                    f"{share:.0%})"
                    + ("" if len(bands) == 1 else
                       f"; {len(bands)} bands more than {band_mult:g}x"
                       f"{step:g} ft apart, keeping {v[a]:g}-{v[b - 1]:g} ft")))
    return out


def _clamped_ct_eval(P, Z):
    """f(gx, gy) -> z: C1 CloughTocher clipped to the local contour bracket.

    Returns NaN outside the hull exactly as LinearNDInterpolator does (CT
    shares the triangulation), so a caller's defined-region mask is
    unaffected by the choice of model.
    """
    f = CloughTocher2DInterpolator(P, Z)
    tree = cKDTree(P)
    k = min(CLAMP_K, len(P))

    def ev(gx, gy):
        gx = np.asarray(gx, dtype=float)
        shape = gx.shape
        gxf = gx.ravel()
        gyf = np.asarray(gy, dtype=float).ravel()
        z = f(gxf, gyf)
        _, idx = tree.query(np.column_stack([gxf, gyf]), k=k, workers=-1)
        if idx.ndim == 1:
            idx = idx[:, None]
        zn = Z[idx]
        # np.clip propagates NaN, so cells outside the hull stay undefined
        return np.clip(z, zn.min(axis=1), zn.max(axis=1)).reshape(shape)

    return ev


def label_band_ft(P, Z, gx, gy) -> dict:
    """How far apart in ELEVATION the labels around each cell are.

    For each probed cell: the z of the nearest contour sample, and the z of
    the nearest sample carrying a DIFFERENT elevation. Their difference is
    the vertical gap the TIN is interpolating blindly across at that cell -
    the measured footprint of terracing. Reported as a median over a
    bounded probe of the cells the volume is actually integrated over.
    """
    n = len(gx)
    if n == 0 or len(P) < 2:
        return {"median_ft": None, "n_probe": 0}
    step = max(1, int(np.ceil(n / _BAND_PROBE_MAX)))
    qx, qy = gx[::step], gy[::step]
    k = min(max(CLAMP_K, 2), len(P))
    tree = cKDTree(P)
    _, idx = tree.query(np.column_stack([qx, qy]), k=k, workers=-1)
    if idx.ndim == 1:
        idx = idx[:, None]
    zn = Z[idx]
    z1 = zn[:, 0]
    diff = np.abs(zn - z1[:, None]) > 1e-9
    has2 = diff.any(axis=1)
    if not has2.any():
        return {"median_ft": None, "n_probe": int(len(qx))}
    z2 = zn[np.arange(len(zn)), np.argmax(diff, axis=1)]
    band = np.abs(z2 - z1)[has2]
    return {"median_ft": round(float(np.median(band)), 2),
            "mean_ft": round(float(band.mean()), 2),
            "p90_ft": round(float(np.percentile(band, 90)), 2),
            "n_probe": int(len(qx))}


def _points_in_poly(GX, GY, poly):
    """Vectorised even-odd (ray casting) test over a whole grid.

    Even-odd rather than winding: a limit-of-work line drawn as a single ring
    is the normal case, and even-odd also does the right thing for a figure
    that doubles back on itself, which a hand-traced boundary sometimes does.
    Written against the grid in one pass - a per-cell Python loop over a
    4-million-cell grid would take minutes.
    """
    x = GX.ravel()
    y = GY.ravel()
    inside = np.zeros(x.shape, dtype=bool)
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        # does the horizontal ray from (x, y) cross edge j->i ?
        straddles = (yi > y) != (yj > y)
        if straddles.any():
            # x of the edge at this y; guard the horizontal edge
            dy = (yj - yi) or 1e-12
            xcross = (xj - xi) * (y - yi) / dy + xi
            inside ^= straddles & (x < xcross)
        j = i
    return inside.reshape(GX.shape)


def cut_fill(existing: Surface, proposed: Surface, ft_per_pt: float,
             cell_ft: float = 2.0, existing_offset_ft: float = 0.0,
             proposed_offset_ft: float = 0.0,
             return_grid: bool = False,
             boundary: "list | None" = None,
             surface_model: str = "auto") -> dict:
    """Grid-integrate (proposed - existing) where both TINs are defined.

    Offsets LOWER a surface before differencing (z - offset). The client's
    workflow (Beaumont, F52): strip topsoil first - a thickness from the
    soils report, never on the plans - so the existing ground the cut/fill
    starts from is that much lower; remove named layers (asphalt, road
    base) the same way. On the proposed side the sheet draws finish grade
    but the dig stops at subgrade under the pavement stack, so the proposed
    surface drops by that stack. Lowering existing by e and proposed by p
    shifts every dz by (e - p): net_yd3 moves by exactly area * (e - p) / 27.

    return_grid=True adds "grid": the full dz array (NaN where either TIN
    is undefined), the "ok" mask, and the grid's origin / cell size in page
    pt so a caller can paint a cut/fill map registered to the sheet. The
    default call path (offsets 0, no grid) returns exactly what it always
    did - same keys, same numbers.

    boundary is the LIMIT OF WORK: a polygon in page pt, outside which no
    volume is counted. Without it the region integrated over is wherever BOTH
    TINs are defined - which is the intersection of the two surfaces' convex
    hulls, and a hull bulges anywhere the graded area is concave or the
    contours sprawl. Two clients reported the same consequence independently:
    Jason Knudson asked "can we boundary these?" of a 172,094 CY read, and on
    a 600-acre mass-grading sheet Josh Stice scored us against his own takeoff
    at cut 2.1M vs his 2.2M (close) but fill 2.6M vs his 1.6M - "it's filling
    all that stuff outside" - and pointed at the grading-limit line the sheet
    already draws.

    Passing None is not a special case that is merely handled; it is the
    original code path untouched, so every existing number is unchanged.

    surface_model picks how each surface is EVALUATED between its contours
    (see SURFACE_MODELS above): "linear" is the piecewise-linear TIN this
    module has always used, "clamped_ct" is the C1-smooth model clipped to
    the local contour bracket, and "auto" measures the sheet's own label
    sparsity and picks one. Whichever runs, the result says so in
    surface_model / surface_model_source / surface_model_why - a volume
    must never leave here without naming the surface that produced it.
    """
    model_req = str(surface_model or "auto")
    if model_req not in SURFACE_MODELS:
        return {"error": f"surface_model must be one of "
                         f"{', '.join(SURFACE_MODELS)}"}
    fe, pe = existing.interpolator()
    fp, pp = proposed.interpolator()
    # THE FLOOR (see MIN_DISTINCT_Z above). Each surface is judged on its
    # own and NAMED in its own refusal: the old single sentence ("each
    # surface needs at least one labeled contour (and 3+ sample points)")
    # never said which side was short, and its "at least one" WAS the hole -
    # one contour is a plane and a plane is not terrain. Existing is tested
    # first so a body short on both sides names existing, deterministically.
    for _surf, _ok in ((existing, fe is not None), (proposed, fp is not None)):
        bad = _floor_refusal(_surf, _ok)
        if bad is not None:
            bad["basis"] = _basis_pair(existing, proposed)
            return bad
    lo = np.maximum(pe.min(axis=0), pp.min(axis=0))
    hi = np.minimum(pe.max(axis=0), pp.max(axis=0))
    if (hi <= lo).any():
        return {"error": "the two surfaces do not overlap in plan"}
    cell_pt = cell_ft / ft_per_pt
    nx = max(int((hi[0] - lo[0]) / cell_pt), 1)
    ny = max(int((hi[1] - lo[1]) / cell_pt), 1)
    # cap the grid so a giant sheet cannot stall the service; widen cells
    while nx * ny > 4_000_000:
        cell_pt *= 1.5
        cell_ft *= 1.5
        nx = max(int((hi[0] - lo[0]) / cell_pt), 1)
        ny = max(int((hi[1] - lo[1]) / cell_pt), 1)
    gx = lo[0] + (np.arange(nx) + 0.5) * cell_pt
    gy = lo[1] + (np.arange(ny) + 0.5) * cell_pt
    GX, GY = np.meshgrid(gx, gy)
    ze = fe(GX, GY)
    zp = fp(GX, GY)
    ok = ~(np.isnan(ze) | np.isnan(zp))
    if boundary is not None:
        poly = np.asarray(boundary, dtype=float)
        if poly.ndim != 2 or poly.shape[0] < 3 or poly.shape[1] != 2:
            return {"error": "the boundary needs at least 3 points, each [x, y] "
                             "in page points"}
        inside = _points_in_poly(GX, GY, poly)
        if not (ok & inside).any():
            return {"error": "no part of the graded surface falls inside that "
                             "boundary - check it is the limit-of-work line and "
                             "not a detail or a legend box"}
        ok = ok & inside
    if not ok.any():
        return {"error": "surfaces overlap in plan but their TINs do not"}
    # THE DEFINED REGION IS DECIDED BY THE LINEAR TIN, ALWAYS. `ok` above is
    # computed from the linear evaluation and is never recomputed for a
    # different model, so area_sf, the boundary clip and the map's extent
    # are identical whichever surface model runs and a before/after is a
    # comparison of elevations only. (CT shares the Delaunay hull, so this
    # costs nothing - it removes a floating-point edge case, not a region.)
    Pex, Zex = existing.samples()
    Ppr, Zpr = proposed.samples()
    gxi, gyi = GX[ok], GY[ok]
    band_ex = label_band_ft(Pex, Zex, gxi, gyi)
    band_pr = label_band_ft(Ppr, Zpr, gxi, gyi)
    bands = [b["median_ft"] for b in (band_ex, band_pr)
             if b.get("median_ft") is not None]
    band_ft = max(bands) if bands else None
    if model_req == "auto":
        # Decided per JOB, not per surface: the two reference measurements
        # show the clamped model wins on BOTH surfaces together or neither
        # (Stice one-surface-only variants both score worse than doing
        # both), and the volume is a difference of the two.
        if band_ft is None:
            model = "linear"
            why = ("linear: this sheet's contours carry too few distinct "
                   "elevations to measure label sparsity")
        elif band_ft >= AUTO_BAND_FT:
            model = "clamped_ct"
            why = (f"clamped smooth: labels are sparse - the median gap "
                   f"between the two nearest contour elevations is "
                   f"{band_ft} ft, at or over the {AUTO_BAND_FT} ft "
                   f"threshold, so a linear TIN would terrace the ground "
                   f"between them")
        else:
            model = "linear"
            why = (f"linear TIN: labels are dense - the median gap between "
                   f"the two nearest contour elevations is {band_ft} ft, "
                   f"under the {AUTO_BAND_FT} ft threshold, and on a dense "
                   f"sheet the linear surface matched printed spot "
                   f"elevations better than the smooth one")
        source = "auto (measured label sparsity)"
    else:
        model = model_req
        source = "operator"
        why = ("linear TIN: chosen on the panel"
               if model == "linear" else
               "clamped smooth: chosen on the panel")
    if model == "clamped_ct":
        # evaluated only on the cells that already count, so the smooth
        # model costs one CT build + one k-nearest query per surface
        ze = np.where(ok, np.nan, np.nan)
        zp = np.where(ok, np.nan, np.nan)
        ze[ok] = _clamped_ct_eval(Pex, Zex)(gxi, gyi)
        zp[ok] = _clamped_ct_eval(Ppr, Zpr)(gxi, gyi)
        # a clamped value can still come back NaN where CT declines a
        # degenerate triangle; those cells leave the defined region
        still = ~(np.isnan(ze) | np.isnan(zp))
        ok = ok & still
        if not ok.any():
            return {"error": "the smooth surface model could not evaluate "
                             "this sheet - switch the surface model to "
                             "Linear on the panel"}
    # guarded so the zero-offset path is the exact expression it always was
    if existing_offset_ft:
        ze = ze - float(existing_offset_ft)
    if proposed_offset_ft:
        zp = zp - float(proposed_offset_ft)
    dz_grid = zp - ze
    dz = dz_grid[ok]
    cell_area_ft2 = cell_ft * cell_ft
    fill_ft3 = float(dz[dz > 0].sum() * cell_area_ft2)
    cut_ft3 = float(-dz[dz < 0].sum() * cell_area_ft2)
    out = {
        # Stated so a reader of the export can tell a site-wide figure from a
        # figure clipped to a limit of work. Silence would make the two
        # indistinguishable, which is the whole defect this argument fixes.
        "boundary_applied": boundary is not None,
        "boundary_points": (len(boundary) if boundary is not None else 0),
        "cut_yd3": round(cut_ft3 / 27.0, 1),
        "fill_yd3": round(fill_ft3 / 27.0, 1),
        # The deepest single cell each way, in feet - what the color map's
        # darkest spots ARE. A volume alone hides whether the site moves
        # 2 ft everywhere or 40 ft in one canyon; the extremes are the
        # sanity check an estimator runs by eye against the contours.
        "max_cut_ft": round(float(-dz[dz < 0].min()) if (dz < 0).any() else 0.0, 2),
        "max_fill_ft": round(float(dz[dz > 0].max()) if (dz > 0).any() else 0.0, 2),
        "net_yd3": round((fill_ft3 - cut_ft3) / 27.0, 1),
        "area_sf": round(float(ok.sum()) * cell_area_ft2, 0),
        "cell_ft": round(cell_ft, 2),
        "existing_contours": len(existing.contours),
        "proposed_contours": len(proposed.contours),
        # WHICH SURFACE PRODUCED THIS NUMBER. The two models disagree by
        # +19% cut on a sparse sheet, so a volume that does not name its
        # interpolant is a volume an operator cannot check. Reported on
        # every call, including the linear default, so the field's absence
        # never has to mean anything.
        "surface_model": model,
        "surface_model_source": source,
        "surface_model_why": why,
        "label_band_ft": band_ft,
        "label_band_existing_ft": band_ex.get("median_ft"),
        "label_band_proposed_ft": band_pr.get("median_ft"),
        # WHAT THE NUMBER RESTS ON, counted per surface, on every answer -
        # not only when something is wrong. existing_contours /
        # proposed_contours above count ROWS; this counts DISTINCT
        # ELEVATIONS, which is the quantity the floor is about and the one
        # a reader needs to see a flat plane coming. `caution` is the
        # disjoint-range disclosure (see _basis_pair): a sentence, never a
        # refusal, and null on every corpus pair measured.
        "surface_basis": _basis_pair(existing, proposed),
    }
    if return_grid:
        # row i / col j of dz covers page-pt box
        #   [x0 + j*cell_pt, x0 + (j+1)*cell_pt] x [y0 + i*cell_pt, ...]
        # (samples sit at the cell centres), so an nx-by-ny image placed at
        # bbox registers to the sheet without a half-cell shift
        out["grid"] = {
            "dz": np.where(ok, dz_grid, np.nan),
            "ok": ok,
            "x0_pt": float(lo[0]), "y0_pt": float(lo[1]),
            "cell_pt": float(cell_pt), "nx": int(nx), "ny": int(ny),
            "bbox_pt": [float(lo[0]), float(lo[1]),
                        float(lo[0] + nx * cell_pt),
                        float(lo[1] + ny * cell_pt)],
        }
    return out


# --- material balance: export / reuse / import ---------------------------
#
# The estimator's second question, after "how much moves?": how much LEAVES,
# how much gets REUSED, how much must be BOUGHT. Beaumont, 2026-08-21
# transcript line 186 - HIS words, not the interviewer's: "in a building or
# something, I can't reuse that top soil. So, yeah, now I need to know,
# structural dirt wise, what I can use to replace that."
#
# Their vocabulary is EXPORT and IMPORT (Estimate #853 line items; bid-sheet
# exclusion 23, "All import and export is subject to loading and knock down
# fee's"). Deliberately NOT "haul off": that phrasing came from the
# interviewer, and exclusion 6 ("Topsoil and fill material required fro the
# job will be stockpiled onsite") plus the "On site" suffix on both #853
# balance lines show their export frequently never leaves the property.
# Hence the per-row destination note, defaulting to "on site".
#
# Every default below is measured against Beaumont Estimate #853 (SWR Barn
# Residence, 4/29/2026) - their own priced takeoff, not an industry prior:
#
#     Strip Topsoil        5,730 CYD     Site Cut            5,551 CYD
#     Rough Place Topsoil  2,865 CYD     Site Fill          17,123 CYD
#     Export Topsoil       2,865 CYD     Import Structural  11,581 CYD
#
#   * 2,865 + 2,865 = 5,730 exactly. The strip splits between respread and
#     export - but that split is the estimator's judgement (soils report,
#     landscape area), so NO default is seeded for it. #853's exact 50/50 is
#     a fact about that job, not a rule.
#   * 17,123 - 5,551 = 11,572 against their 11,581: a 9 CYD / 0.078%
#     residual. Two consequences, both measurements:
#       - site cut IS reused against site fill 1:1, so cut_reuse_pct
#         defaults to 100 - the only defaulted assumption here;
#       - NO swell / shrink / compaction factor is applied anywhere. A
#         factor would put tens of percent between those two numbers, not
#         0.078%. export_factor / import_factor therefore default to 1.0 and
#         exist only so a user whose job needs one can type it. Volumes are
#         in-place yards measured between two surfaces; this module does not
#         convert between in-place, loose and compacted volume, because
#         #853 does not.
#   * Had the stripped topsoil counted as structural reuse, their import
#     would read 11,572 - 5,730 = 5,842. It reads 11,581 - so #853 confirms
#     line 186 as a NUMBER. Removal layers therefore default to 100% export.
#
# The 9 CYD residual is an OPEN QUESTION for the client, not an assumption.

_BALANCE_UNIT = "CY"
# #853 writes CYD. The app writes CY on every existing earthwork row and CY
# is the commoner abbreviation, so one client's house style does not get to
# churn the whole app; revisit as a per-client display preference.


def clamp_pct(v, default: float = 0.0) -> float:
    """A percentage the UI may hand us blank, negative or over 100."""
    try:
        p = float(v)
    except (TypeError, ValueError):
        return default
    if p != p:                      # NaN
        return default
    return min(max(p, 0.0), 100.0)


def boundary_metrics(pts, ft_per_pt):
    """Area (SF), perimeter (ft) and closure gap (ft) of a limit-of-work ring
    given in page points, or None for fewer than three points.

    The ring is treated as closed - last point joined to first - which is
    the convention the volume mask already uses; the gap says how far apart
    the drawn ends actually are, so a traced-but-open line is judged by the
    operator rather than silently closed. Same shoelace expression as the
    layers-only branch of the volumes route, so the two never disagree.
    Beaumont's step one on every site plan (2026-09-15, 05:48-06:27): the
    limits line's length is the fence, everything inside it is Clear & Grub
    and the strip area."""
    p = [(float(q[0]), float(q[1])) for q in (pts or [])
         if isinstance(q, (list, tuple)) and len(q) >= 2]
    if len(p) < 3:
        return None
    a = 0.0
    per = 0.0
    for i in range(len(p)):
        x0, y0 = p[i - 1]
        x1, y1 = p[i]
        a += (x0 + x1) * (y0 - y1)
        per += ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    gap = ((p[0][0] - p[-1][0]) ** 2 + (p[0][1] - p[-1][1]) ** 2) ** 0.5
    return {"area_sf": round(abs(a / 2.0) * ft_per_pt * ft_per_pt, 1),
            "perim_ft": round(per * ft_per_pt, 1),
            "gap_ft": round(gap * ft_per_pt, 1),
            "n": len(p)}


def material_balance(cut_yd3: float, fill_yd3: float, layers: list,
                     cut_reuse_pct: float = 100.0,
                     export_factor: float = 1.0,
                     import_factor: float = 1.0,
                     cut_area_sf: float | None = None,
                     layer_area_sf: float | None = None) -> dict:
    """Split the computed volumes into export / reuse / import.

        reuse_pool  = cut x cut_reuse%  +  SUM(layer x layer.reuse%)
        fill_demand = fill
        reuse       = min(reuse_pool, fill_demand)
        import      = fill_demand - reuse       -> "Import Structural"
        surplus     = reuse_pool  - reuse       -> "Export Surplus Cut"
        per layer:  volume x export%            -> "Export <name>"
                    volume x respread%          -> "Rough Place <name>"

    `layers` is the list `_earthwork_run` already builds - each
    {name, side, thickness_in, volume_yd3} plus the UI's reuse_pct /
    respread_pct / destination.

    Returns the summary plus `rows`, ready to become takeoff line items.
    """
    cut = max(float(cut_yd3 or 0.0), 0.0)
    fill = max(float(fill_yd3 or 0.0), 0.0)
    cut_reuse = clamp_pct(cut_reuse_pct, 100.0)
    try:
        ef = max(float(export_factor), 0.0) or 1.0
    except (TypeError, ValueError):
        ef = 1.0
    try:
        imf = max(float(import_factor), 0.0) or 1.0
    except (TypeError, ValueError):
        imf = 1.0

    def r1(v: float) -> float:
        return round(float(v), 1)

    removals = []
    for lay in layers or []:
        # TRAP 2 - PROPOSED LAYERS MUST NOT ENTER THE BALANCE. The proposed
        # stack (road base, paving) LOWERS the proposed surface in cut_fill,
        # so fill_yd3 is fill to SUBGRADE and that stack sits on top of it,
        # already priced as its own "Place <name>" row. Adding it to
        # fill_demand double-counts it; treating it as site dirt is worse -
        # nobody backfills a driveway section with the spoil pile.
        if str(lay.get("side")) != "existing":
            continue
        vol = max(float(lay.get("volume_yd3") or 0.0), 0.0)
        reuse_p = clamp_pct(lay.get("reuse_pct"), 0.0)
        respread_p = clamp_pct(lay.get("respread_pct"), 0.0)
        if reuse_p + respread_p > 100.0:     # UI clamps, but never trust it
            respread_p = max(100.0 - reuse_p, 0.0)
        export_p = max(100.0 - reuse_p - respread_p, 0.0)
        removals.append({
            "name": str(lay.get("name") or "layer"),
            "volume_yd3": vol,
            "reuse_pct": reuse_p, "respread_pct": respread_p,
            "export_pct": export_p,
            "destination": str(lay.get("destination") or "on site")[:40],
            "offer_yd3": r1(vol * reuse_p / 100.0),
            "used_yd3": 0.0,
        })

    # TRAP 1 - fill_yd3 ALREADY CONTAINS THE TOPSOIL REPLACEMENT. cut_fill()
    # lowers the existing surface by the strip thickness, which shifts every
    # dz by (e - p), so stripping a foot raises fill by a foot across the
    # overlap. That IS Beaumont's "structural dirt to replace that" and it is
    # already inside this number. Do not add the strip volume to fill_demand.
    fill_demand = fill

    # Round FIRST, then subtract, so the arithmetic an estimator reads off
    # the panel closes exactly instead of drifting a tenth.
    cut_r, demand_r = r1(cut), r1(fill_demand)
    cut_offer_r = r1(cut_r * cut_reuse / 100.0)
    pool_r = r1(cut_offer_r + sum(rm["offer_yd3"] for rm in removals))
    reuse_r = min(pool_r, demand_r)
    import_r = r1(demand_r - reuse_r)
    surplus_r = r1(pool_r - reuse_r)

    # Draw the reuse from the site cut first, then from any removal layer the
    # estimator marked reusable, in list order. The ordering only bites when a
    # removal layer is ALSO marked reusable; otherwise the cut is the only
    # thing in the pool, which is #853's shape and the ordinary case. "Cut
    # first" is an INFERENCE (it is already in the machine and already being
    # moved), not a rule Beaumont stated - it is on the open-questions list.
    cut_used_r = min(cut_offer_r, reuse_r)
    left = r1(reuse_r - cut_used_r)
    for rm in removals:
        rm["used_yd3"] = min(rm["offer_yd3"], left)
        left = r1(left - rm["used_yd3"])
    # Cut that is NOT reused has to leave: the part the estimator never
    # offered (cut_reuse_pct < 100) plus the offered part fill did not need.
    # Dropping the un-offered part would delete real truckloads from the bid.
    cut_export_r = r1(cut_r - cut_used_r)

    cut_area = float(cut_area_sf) if cut_area_sf else None
    lay_area = float(layer_area_sf) if layer_area_sf else None
    rows = []
    for rm in removals:
        # "Export Topsoil" and "Rough Place Topsoil" are #853's own line
        # names and fall out of a layer named Topsoil. "Export <anything
        # else>" follows the same shape and is an INFERENCE, exactly like the
        # existing "Remove <name>" / "Place <name>" rows.
        # what this layer exports = the share never offered for reuse, plus
        # any offered share the fill did not actually need
        spare = r1(rm["offer_yd3"] - rm["used_yd3"])
        base = r1(rm["volume_yd3"] * rm["export_pct"] / 100.0)
        if base + spare > 0:
            q = r1((base + spare) * ef)
            if q > 0:
                rows.append({
                    "label": f'Export {rm["name"]}'.strip(),
                    "qty": q, "unit": _BALANCE_UNIT, "kind": "export",
                    "basis": f'{r1(rm["volume_yd3"]):,} CY x '
                             f'{rm["export_pct"]:g}% export'
                             + (f' + {spare:,} CY offered but not needed'
                                if spare > 0 else '')
                             + (f' x {ef:g}' if ef != 1.0 else ''),
                    "area_sf": lay_area, "area_source": "layer area",
                    "destination": rm["destination"],
                })
        if rm["respread_pct"] > 0:
            q = r1(rm["volume_yd3"] * rm["respread_pct"] / 100.0)
            if q > 0:
                rows.append({
                    "label": f'Rough Place {rm["name"]}'.strip(),
                    "qty": q, "unit": _BALANCE_UNIT, "kind": "respread",
                    "basis": f'{r1(rm["volume_yd3"]):,} CY x '
                             f'{rm["respread_pct"]:g}% respread',
                    "area_sf": lay_area, "area_source": "layer area",
                    "destination": rm["destination"],
                })
    if cut_export_r > 0:
        # PROVISIONAL NAME - not Beaumont's. #853's nearest line is
        # "Allowances- Export rocks and boulders to lower field". Open
        # question: what do they call surplus cut?
        q = r1(cut_export_r * ef)
        rows.append({
            "label": "Export Surplus Cut", "qty": q, "unit": _BALANCE_UNIT,
            "kind": "export",
            "basis": f'site cut {cut_r:,} CY - reused {cut_used_r:,} CY'
                     + (f' (only {cut_reuse:g}% offered for reuse)'
                        if cut_reuse < 100.0 else '')
                     + (f' x {ef:g}' if ef != 1.0 else ''),
            "area_sf": cut_area, "area_source": "cut/fill area",
            "destination": "on site",
        })
    if import_r > 0:
        q = r1(import_r * imf)
        rows.append({
            "label": "Import Structural", "qty": q, "unit": _BALANCE_UNIT,
            "kind": "import",
            "basis": f'fill {demand_r:,} CY - reused {reuse_r:,} CY'
                     + (f' x {imf:g}' if imf != 1.0 else ''),
            "area_sf": cut_area, "area_source": "cut/fill area",
            "destination": "on site",
        })

    export_total = r1(sum(r["qty"] for r in rows if r["kind"] == "export"))
    return {
        "reuse_pool_yd3": pool_r,
        "fill_demand_yd3": demand_r,
        "reuse_yd3": reuse_r,
        "import_yd3": r1(import_r * imf),
        "surplus_yd3": r1(surplus_r * ef),
        # the site cut's own fate, so the panel can show that every yard of
        # it is accounted for even when the estimator says none is reusable
        "cut_yd3": cut_r,
        "cut_reused_yd3": cut_used_r,
        "cut_export_yd3": r1(cut_export_r * ef),
        "export_total_yd3": export_total,
        "respread_total_yd3": r1(sum(r["qty"] for r in rows
                                     if r["kind"] == "respread")),
        "cut_reuse_pct": cut_reuse,
        "export_factor": ef, "import_factor": imf,
        "cut_area_sf": cut_area, "layer_area_sf": lay_area,
        "rows": rows,
        # every volume here is in-place yards between the two surfaces; see
        # the module comment for why no swell/compaction factor is applied.
        # "Neat line" is the estimator's word for exactly that - the volume
        # between the drawn surfaces, with no over-excavation, side-slope
        # or working-room allowance added (general estimating usage, not a
        # term any sheet here prints); the client's own Estimate #853 is
        # priced on that basis, so the sentence names it.
        "basis": "neat-line, in-place cubic yards between the two surfaces; "
                 "no swell, compaction, over-excavation or working-room "
                 "allowance applied" if (ef == 1.0 and imf == 1.0) else
                 "neat-line, in-place cubic yards between the two surfaces; "
                 "your export/import factors applied, no over-excavation "
                 "or working-room allowance",
    }


def render_cut_fill_map(grid: dict) -> tuple[bytes, dict]:
    """Paint a dz grid as an RGBA PNG: cut (dz < 0) blue, fill (dz > 0)
    yellow-orange, |dz| -> alpha 0.15..0.75, undefined cells transparent.

    Colour convention is the client's (Beaumont: "cut blue, fill yellow"),
    not an industry standard. Alpha saturates at the 95th percentile of
    |dz| so one deep pad corner does not wash out the rest of the map;
    the reference depth is returned so a legend can say what full
    opacity means.
    """
    import io

    from PIL import Image

    dz = np.asarray(grid["dz"], float)
    ok = np.asarray(grid["ok"], bool)
    mag = np.abs(np.where(ok, dz, 0.0))
    # below a millimetre is TIN floating-point noise, not earth: leave clear
    EPS = 0.003
    nz = mag[ok & (mag > EPS)]
    ref = float(np.percentile(nz, 95)) if nz.size else 1.0
    if ref <= EPS:
        ref = 1.0
    t = np.clip(mag / ref, 0.0, 1.0)
    alpha = np.where(ok & (mag > EPS), 0.15 + 0.60 * t, 0.0)
    H, W = dz.shape
    rgba = np.zeros((H, W, 4), np.uint8)
    cut = ok & (dz < -EPS)
    fill = ok & (dz > EPS)
    rgba[cut] = (30, 100, 255, 0)       # blue
    rgba[fill] = (255, 190, 20, 0)      # yellow-orange
    rgba[..., 3] = np.round(alpha * 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
    meta = {
        "bbox_pt": grid["bbox_pt"], "cell_pt": grid["cell_pt"],
        "nx": grid["nx"], "ny": grid["ny"],
        "ref_dz_ft": round(ref, 3),
        "cut_cells": int(cut.sum()), "fill_cells": int(fill.sum()),
    }
    return buf.getvalue(), meta


# --- grading zones: declared design vs detected existing ------------------
#
# The estimator's workflow when the sheet does NOT draw a full proposed
# surface (measured on the Stice case, EXCAVATION-PLAN.md section 25):
# pick a region and DECLARE the design - "pad at 2716.0", "1.5% plane
# falling north", "balance it", "strip 8 inches" - then compute exact
# volumes of that declared design against the detected existing ground.
# Reference UX is the AGTEK-style region workflow.

def _zone_centroid(poly: np.ndarray) -> tuple:
    """Area centroid of a simple polygon (shoelace); falls back to the
    vertex mean when the area is ~zero (degenerate ring)."""
    x, y = poly[:, 0], poly[:, 1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    cross = x * yn - xn * y
    a = cross.sum() / 2.0
    if abs(a) < 1e-9:
        return float(x.mean()), float(y.mean())
    cx = ((x + xn) * cross).sum() / (6.0 * a)
    cy = ((y + yn) * cross).sum() / (6.0 * a)
    return float(cx), float(cy)


def zone_cut_fill(existing: Surface, polygon, ft_per_pt: float,
                  design: dict, cell_ft: float = 2.0,
                  strip_in: float = 0.0, label_target: int = 120) -> dict:
    """Volumes of a DECLARED design surface against the detected existing
    ground, inside one polygon (page pt).

    design is one of:
      {mode:"pad", elev_ft}                 constant elevation
      {mode:"plane", elev_ft, slope_pct, azimuth_deg, anchor:[x,y]}
          elev_ft at the anchor (default: polygon centroid), falling
          slope_pct per 100 ft ALONG the azimuth. Azimuth is compass-style
          on the sheet: 0 = up the page, 90 = right, measured clockwise.
          That reads the top of the sheet as north - general drafting
          practice, an INFERENCE, not something any one sheet declares;
          the UI says so next to the input.
      {mode:"balance"}                      bisect a pad elevation until
          |cut - fill| < 0.5 CY; reports the solved elevation and its
          offset from the existing mean.
      {mode:"strip_only"}                   no design surface: cut/fill are
          zero and only the strip term computes.

    strip_in > 0 lowers the EXISTING surface by that many inches before
    differencing (same convention as cut_fill's existing_offset_ft:
    stripped ground starts lower, so the design's fill includes replacing
    that depth) and reports the stripped volume as strip_yd3 = area x
    depth. In strip_only mode nothing is differenced, so strip_yd3 is the
    only volume.

    Returns volumes + a "grid" dict (render_cut_fill_map shape) + coarse
    cell_labels [{x, y, dz}] (<= label_target of them) for the per-cell
    numbers display. {"error": reason} on any refusal - never silent.
    """
    poly = np.asarray(polygon, dtype=float)
    if poly.ndim != 2 or poly.shape[0] < 3 or poly.shape[1] != 2:
        return {"error": "the zone polygon needs at least 3 corners, "
                         "each [x, y] in page points"}
    x, y = poly[:, 0], poly[:, 1]
    area_pt2 = abs(float((x * np.roll(y, -1) - np.roll(x, -1) * y).sum())) / 2.0
    zone_area_sf = area_pt2 * ft_per_pt * ft_per_pt
    if zone_area_sf < 1.0:
        return {"error": f"that zone is degenerate - its corners enclose "
                         f"{zone_area_sf:.3f} SF of ground. Draw a region, "
                         f"not a line."}
    mode = str((design or {}).get("mode") or "")
    if mode not in ("pad", "plane", "balance", "strip_only"):
        return {"error": f"unknown design mode {mode!r} - expected pad, "
                         "plane, balance or strip_only"}
    # THE FLOOR, ON THE EXISTING SURFACE (see _floor_refusal for the
    # /earthwork one). Until 2026-09-02 this route's only entry test was "3
    # sample points", so the very rows /earthwork refuses published a volume
    # here: measured on the DEM outline sheet, an existing surface of ONE
    # contour at 324 ft against a declared pad at 826 ft returned 417,664 CY
    # of fill through /earthwork/zone while POST /earthwork on the same job,
    # page and chain answered 400 surface_no_relief. A guard that lives on
    # one route and not on its sibling is not a guard - it is a detour, and
    # this one had the priced takeoff and the drawing on the far side of it.
    #
    # THE TEST IS NOT THE SAME ONE, and the difference is the point. In
    # cut_fill BOTH surfaces are read off the sheet, so a surface carrying
    # one elevation means one label was read and everything off that line is
    # invention. Here the design is DECLARED by the operator, and the
    # question about the existing side is only whether the engine MEASURED
    # the ground or invented it. Five spot grades that all read 100.0 ft are
    # a positive statement that the ground is level - ordinary on a paved
    # lot or a pad being re-graded, and tests/test_grading_zone.py's plane
    # cases are exactly that. ONE label is silence beyond its own line.
    # So the count that matters here is INDEPENDENT LABELS, not distinct
    # elevations: a no-relief refusal on this route would refuse real work.
    fe, _pe = existing.interpolator()
    _b = surface_basis(existing)
    if not existing.contours and not existing.points:
        return {"error": "the existing surface has no contours and no spot "
                         "grades assigned - there is nothing to interpolate",
                "error_code": "surface_empty",
                "surface": _b, "basis": {"existing": _b}}
    if fe is None and existing.degenerate == "collinear":
        return {"error": "the existing surface's samples all lie on ONE "
                         "straight line, so they enclose no area and no "
                         "surface can be built - assign EXISTING a second "
                         "contour off that line, or spot grades off it",
                "error_code": "surface_collinear",
                "surface": _b, "basis": {"existing": _b}}
    if fe is None:
        return {"error": "the existing surface needs at least 3 sample "
                         "points - assign labelled contours and/or spot "
                         "grades to EXISTING first",
                "error_code": "surface_too_few_samples",
                "surface": _b, "basis": {"existing": _b}}
    if _b["n_contours"] + _b["n_spots"] < 2:
        return {"error": f"the existing surface rests on a SINGLE label "
                         f"({_plural(_b['n_contours'], 'contour')}, "
                         f"{_plural(_b['n_spots'], 'spot grade')} at "
                         f"{_b['z_min_ft']:g} ft). Every foot of ground away "
                         f"from it is a level plane this tool invented, and "
                         f"the design is differenced against that invention, "
                         f"so no volume is published. Assign a second "
                         f"labelled contour or spot grade to EXISTING - it "
                         f"may read the same elevation, but it has to be "
                         f"read.",
                "error_code": "surface_one_label",
                "surface": _b, "basis": {"existing": _b}}

    cell_ft = float(cell_ft or 2.0)
    if cell_ft <= 0:
        cell_ft = 2.0
    cell_pt = cell_ft / ft_per_pt
    lo = poly.min(axis=0)
    hi = poly.max(axis=0)
    nx = max(int((hi[0] - lo[0]) / cell_pt), 1)
    ny = max(int((hi[1] - lo[1]) / cell_pt), 1)
    # same stall guard as cut_fill: widen cells rather than hang the service
    while nx * ny > 4_000_000:
        cell_pt *= 1.5
        cell_ft *= 1.5
        nx = max(int((hi[0] - lo[0]) / cell_pt), 1)
        ny = max(int((hi[1] - lo[1]) / cell_pt), 1)
    gx = lo[0] + (np.arange(nx) + 0.5) * cell_pt
    gy = lo[1] + (np.arange(ny) + 0.5) * cell_pt
    GX, GY = np.meshgrid(gx, gy)
    inside = _points_in_poly(GX, GY, poly)
    if not inside.any():
        return {"error": "the zone is smaller than one grid cell at "
                         f"{cell_ft:g} ft cells - draw it larger"}
    ze = fe(GX, GY)
    ok = inside & ~np.isnan(ze)
    if not ok.any():
        return {"error": "the existing surface does not reach this zone - "
                         "the TIN built from the assigned contours and spot "
                         "grades is undefined everywhere inside it. Assign "
                         "elevations nearer the zone, or move it."}
    existing_mean = float(ze[ok].mean())
    try:
        strip_ft = max(float(strip_in or 0.0), 0.0) / 12.0
    except (TypeError, ValueError):
        strip_ft = 0.0
    zs = ze - strip_ft          # existing AFTER the strip comes off
    cell_area_ft2 = cell_ft * cell_ft

    def _vols(dz_arr):
        d = dz_arr[ok]
        fill3 = float(d[d > 0].sum() * cell_area_ft2)
        cut3 = float(-d[d < 0].sum() * cell_area_ft2)
        return cut3, fill3

    balance_elev = None
    if mode == "pad":
        try:
            elev = float(design["elev_ft"])
        except (KeyError, TypeError, ValueError):
            return {"error": "pad mode needs elev_ft, the pad's finish "
                             "elevation in feet"}
        zp = np.full_like(ze, elev)
        dz = zp - zs
    elif mode == "plane":
        try:
            elev = float(design["elev_ft"])
        except (KeyError, TypeError, ValueError):
            return {"error": "plane mode needs elev_ft, the elevation at "
                             "the anchor point in feet"}
        try:
            slope = float(design.get("slope_pct") or 0.0)
        except (TypeError, ValueError):
            slope = 0.0
        try:
            az = float(design.get("azimuth_deg") or 0.0)
        except (TypeError, ValueError):
            az = 0.0
        anc = design.get("anchor")
        if (isinstance(anc, (list, tuple)) and len(anc) == 2):
            ax, ay = float(anc[0]), float(anc[1])
        else:
            ax, ay = _zone_centroid(poly)
        rad = np.radians(az)
        # page y grows DOWN, so "up the page" (az 0) is -y
        ux, uy = float(np.sin(rad)), -float(np.cos(rad))
        d_ft = ((GX - ax) * ux + (GY - ay) * uy) * ft_per_pt
        zp = elev - (slope / 100.0) * d_ft
        dz = zp - zs
    elif mode == "balance":
        # net = fill - cut is strictly increasing in the pad elevation, so
        # bisection cannot miss; 0.5 CY is the requested tolerance
        lo_e = float(zs[ok].min()) - 1.0
        hi_e = float(zs[ok].max()) + 1.0
        mid = (lo_e + hi_e) / 2.0
        for _ in range(80):
            mid = (lo_e + hi_e) / 2.0
            cut3, fill3 = _vols(np.full_like(ze, mid) - zs)
            if abs(cut3 - fill3) / 27.0 < 0.5:
                break
            if fill3 > cut3:
                hi_e = mid
            else:
                lo_e = mid
        balance_elev = mid
        dz = np.full_like(ze, mid) - zs
    else:  # strip_only: no design surface, volumes only from the strip term
        dz = np.zeros_like(ze)

    cut_ft3, fill_ft3 = _vols(dz)
    d = dz[ok]
    area_sf = float(ok.sum()) * cell_area_ft2
    strip_yd3 = area_sf * strip_ft / 27.0
    coverage = float(ok.sum()) / float(inside.sum())

    # coarse labels: aggregate the fine grid into <= label_target blocks,
    # each labelled with the MEAN dz of its defined cells and positioned at
    # their centroid (so a label on the zone's edge still sits inside it)
    k = 1
    if nx * ny > label_target:
        k = int(np.ceil(np.sqrt(nx * ny / float(label_target))))
        while ((nx + k - 1) // k) * ((ny + k - 1) // k) > label_target:
            k += 1
    labels = []
    dz_ok = np.where(ok, dz, np.nan)
    for bi in range(0, ny, k):
        for bj in range(0, nx, k):
            blk = dz_ok[bi:bi + k, bj:bj + k]
            m = ~np.isnan(blk)
            if not m.any():
                continue
            sub_ok = ok[bi:bi + k, bj:bj + k]
            xs = GX[bi:bi + k, bj:bj + k][sub_ok]
            ys = GY[bi:bi + k, bj:bj + k][sub_ok]
            labels.append({"x": round(float(xs.mean()), 1),
                           "y": round(float(ys.mean()), 1),
                           "dz": round(float(blk[m].mean()), 1)})

    out = {
        "mode": mode,
        "cut_yd3": round(cut_ft3 / 27.0, 1),
        "fill_yd3": round(fill_ft3 / 27.0, 1),
        "net_yd3": round((fill_ft3 - cut_ft3) / 27.0, 1),
        "area_sf": round(area_sf, 0),
        "zone_area_sf": round(zone_area_sf, 0),
        # < 1.0 whenever the existing TIN stops short of the polygon: the
        # volumes cover only the defined share, and the caller must say so
        "coverage": round(coverage, 3),
        "max_cut_ft": round(float(-d[d < 0].min()) if (d < 0).any() else 0.0, 2),
        "max_fill_ft": round(float(d[d > 0].max()) if (d > 0).any() else 0.0, 2),
        "existing_mean_ft": round(existing_mean, 2),
        "existing_n_contours": len(existing.contours),
        "existing_n_spots": len(existing.points),
        "strip_in": round(strip_ft * 12.0, 2),
        "strip_yd3": round(strip_yd3, 1),
        "cell_ft": round(cell_ft, 2),
        "cell_labels": labels,
        "grid": {
            "dz": dz_ok,
            "ok": ok,
            "x0_pt": float(lo[0]), "y0_pt": float(lo[1]),
            "cell_pt": float(cell_pt), "nx": int(nx), "ny": int(ny),
            "bbox_pt": [float(lo[0]), float(lo[1]),
                        float(lo[0] + nx * cell_pt),
                        float(lo[1] + ny * cell_pt)],
        },
    }
    if balance_elev is not None:
        out["balance_elev_ft"] = round(balance_elev, 2)
        out["balance_delta_ft"] = round(balance_elev - existing_mean, 2)
    return out


# ---- label-in-gap machinery ----------------------------------------------
# Contour elevation labels sit in a BREAK of their own line, or over a white
# mask stroke painted along it. Both are located from the vector geometry.
# Constants are measured on Oncor Cloudcrest C-04 (engine page index 3 =
# printed C-04), whose 27 existing + 20 proposed labels are hand-verified in
# out/verify/2026-08-31/oncor-c04-offline/report.json. Each states its
# measurement; a value from one sheet is a sample, not a convention, so the
# spans are chosen between measured populations, not at their edges.

# A gap step: a polyline jump big enough to be part of a label break.
# C-04's label breaks decompose into sub-gaps of 9.8-35.2 pt (tiny glyph
# stubs welded into the chain split them); the largest NON-label jump that
# must not trigger is the existing contours' own 9.0 pt dash gap. 9.5 sits
# between the two measured populations. The floor also scales with the
# chain's own median jump (x1.3): a smooth curve digitised at ~11 pt
# vertex spacing (C-04 chain 813) otherwise reads as one endless "break".
# 1.3 is bounded above by the existing d18/g9 contours, whose label breaks
# measure down to 17.7 pt against a 13.5 pt median jump (ratio 1.31) - a
# larger factor starts eating real label breaks.
_GAP_STEP_PT = 9.5
_GAP_STEP_MED_FACTOR = 1.3
# Sub-gaps merge across drawn stubs totalling <= 8 pt: real dashes on these
# sheets carry >= 9 pt of ink, welded glyph stubs measured 0.5-6 pt.
_GAP_STUB_PT = 8.0
# A merged break must span 14-110 pt end to end: the narrowest verified C-04
# label break chord is 18.7 pt (14 leaves margin for tighter text) and the
# widest 72.4; 110 tolerates a longer 4-digit-with-decimals label before the
# OCR patch grows useless.
_GAP_SPAN_PT = (14.0, 110.0)
# Ranking span for candidate patches: C-04's 3-digit labels occupy breaks
# centered on ~26 pt; regions closest to this are OCR'd first when a page
# offers more candidates than the budget.
_LABEL_SPAN_PT = 26.0
# Near-white ink is invisible on paper: C-04's mask strokes (sids 44-48)
# are exactly 1.0; the lightest visible screening measured on that sheet is
# 0.7 gray. 0.92 splits the populations.
WHITE_MIN = 0.92
# A mask stroke is FAT white drawn along the line it hides: C-04 masks are
# 3.2 pt wide, white glyph halos 0.1-1.2 pt. 1.5 splits them.
_MASK_WIDTH_MIN = 1.5
# One mask dash covers one label: drawn lengths measured 16-25 pt on C-04's
# ten mask-covered labels (12-60 leaves room for longer labels).
_MASK_SEG_PT = (12.0, 60.0)
# The mask lies ON the chain it hides: mask-center-to-chain distance
# measured 0.1-3.9 pt across those ten labels.
_MASK_DIST_PT = 4.0
# ...and runs along it: |cos| between mask segment and local chain tangent
# measured 0.79-0.97 (the lowest is a curving contour under a straight
# mask). 0.75 keeps that case and still rejects crossing strokes.
_MASK_COS = 0.75
# A native text token whose center sits within half a text-line height of
# the break chord is IN the break; label text on these sheets is 7-10 pt
# tall, so 6 pt covers it without reaching the next contour (~10-15 pt away
# on C-04's tightest strip).
_INLINE_PERP_PT = 6.0
# A native token within this of the polyline is printed ON the line itself -
# the PDF's own glyphs there outrank a raster OCR of the same spot, so a
# gap-OCR value never overrides such a match. Measured: Stice's verified
# on-line labels sit 0.2-1.6 pt from their chain; C-04's stolen spot
# tokens sit 2.1-4.4 pt off it (at 6.0 this guard kept spot 570.3 on
# chain 207 against the OCR's correct 570). 3.0 splits the populations.
_ON_LINE_PT = 3.0


def _chain_white(c) -> bool:
    """Near-white stroke = invisible ink (a mask), never a contour."""
    col = getattr(c, "color", None)
    try:
        return bool(col) and min(col) >= WHITE_MIN
    except (TypeError, ValueError):
        return False


def _gap_regions(p: np.ndarray) -> list:
    """Merged label-sized breaks of one polyline.

    Walks the segment list (a gap test, not a bucket): a region opens at a
    jump >= the chain's gap-step threshold and extends across further such
    jumps while the drawn stubs between them total <= _GAP_STUB_PT. A run
    whose chord outgrows the span cap is SPLIT greedily instead of thrown
    away - discarding it silently lost chain 800's label on C-04 when its
    break merged into a longer run. Returns
    [(cx, cy, ux, uy, span), ...] - chord center, unit direction, length.
    """
    d = np.diff(p, axis=0)
    jump = np.hypot(d[:, 0], d[:, 1])
    pos = jump[jump > 0.5]
    med = float(np.median(pos)) if len(pos) else 0.0
    step = max(_GAP_STEP_PT, _GAP_STEP_MED_FACTOR * med)
    out = []
    n = len(jump)
    runs = []
    i = 0
    while i < n:
        if jump[i] < step:
            i += 1
            continue
        run = [i]
        stub = 0.0
        j = i + 1
        while j < n:
            if jump[j] >= step:
                run.append(j)
                stub = 0.0
            else:
                stub += jump[j]
                if stub > _GAP_STUB_PT:
                    break
            j += 1
        runs.append(run)
        i = run[-1] + 1

    def _chord(a_i, b_i):
        return float(np.hypot(*(p[b_i + 1] - p[a_i])))

    for run in runs:
        s = 0
        while s < len(run):
            e = s
            while e + 1 < len(run) \
                    and _chord(run[s], run[e + 1]) <= _GAP_SPAN_PT[1]:
                e += 1
            a, b = p[run[s]], p[run[e] + 1]
            span = float(np.hypot(*(b - a)))
            if _GAP_SPAN_PT[0] <= span <= _GAP_SPAN_PT[1]:
                u = (b - a) / span
                out.append((float((a[0] + b[0]) / 2),
                            float((a[1] + b[1]) / 2),
                            float(u[0]), float(u[1]), span))
            s = e + 1
        # A LONG run also gets one window per big gap step, centered on
        # the STEP itself with the step's own direction. A run's chord is
        # a straight line, but the break curves: on C-04, chain 805's
        # label sat 13 pt perpendicular to its 58 pt region chord (out of
        # the OCR band) and chain 801's sat past the chord's end after
        # the >110 pt split - both are covered only by walking the actual
        # polyline. 2 * _LABEL_SPAN_PT is where one centered patch stops
        # covering a region's ends; 14 pt is the narrowest verified label
        # break (the span floor).
        if _chord(run[0], run[-1]) > 2 * _LABEL_SPAN_PT:
            for idx in run:
                L = float(jump[idx])
                if L < _GAP_SPAN_PT[0]:
                    continue
                a, b = p[idx], p[idx + 1]
                u = (b - a) / L
                out.append((float((a[0] + b[0]) / 2),
                            float((a[1] + b[1]) / 2),
                            float(u[0]), float(u[1]),
                            float(min(L + 8.0, 1.4 * _LABEL_SPAN_PT))))
    return out


def _label_regions(chains: list, exclude: set | None = None) -> dict:
    """Places where a chain's own elevation label can sit.

    {chain_id: [(kind, cx, cy, ux, uy, span), ...]} with kind 0 = under a
    white mask stroke (strongest: something was deliberately hidden there),
    kind 1 = a merged break of the polyline. Chains in `exclude` (legend
    non-contours, white masks) get no regions - their labels are not
    elevations, and the mask strokes themselves serve only as hints for the
    chains they cover.
    """
    exclude = exclude or set()
    plan = [c for c in chains
            if c.length >= 60 and len(c.pts) >= 3 and _is_plan_chain(c)
            and not _chain_white(c) and int(c.chain_id) not in exclude]
    regions: dict = {}
    for c in plan:
        p = np.asarray(c.pts, float)
        for cx, cy, ux, uy, span in _gap_regions(p):
            regions.setdefault(int(c.chain_id), []).append(
                (1, cx, cy, ux, uy, span))
    masks = [c for c in chains
             if _chain_white(c) and (getattr(c, "width", 0) or 0)
             >= _MASK_WIDTH_MIN]
    if masks and plan:
        samp, owner, tang = [], [], []
        for c in plan:
            p = np.asarray(c.pts, float)
            for a, b in zip(p[:-1], p[1:]):
                L = float(np.hypot(*(b - a)))
                if L < 1e-6:
                    continue
                u = (b - a) / L
                k = max(int(L / 3.0), 1)
                for t in np.linspace(0.0, 1.0, k, endpoint=False):
                    samp.append(a + t * (b - a))
                    owner.append(int(c.chain_id))
                    tang.append(u)
        if samp:
            tree = cKDTree(np.asarray(samp))
            tang = np.asarray(tang)
            for c in masks:
                p = np.asarray(c.pts, float)
                for a, b in zip(p[:-1], p[1:]):
                    L = float(np.hypot(*(b - a)))
                    if not (_MASK_SEG_PT[0] <= L <= _MASK_SEG_PT[1]):
                        continue
                    u = (b - a) / L
                    ctr = (a + b) / 2
                    dd, ii = tree.query(ctr, k=12)
                    dd = np.atleast_1d(dd)
                    ii = np.atleast_1d(ii)
                    d, i = float(dd[0]), int(ii[0])
                    if d > _MASK_DIST_PT:
                        continue
                    if abs(float(tang[i] @ u)) < _MASK_COS:
                        continue
                    cid = int(owner[i])
                    # ambiguity guard: when a SECOND chain also passes
                    # within mask distance (C-04's south strip runs its
                    # contours 10 pt apart under one dashed mask line),
                    # the label under this mask cannot be assigned by
                    # geometry - a wrong owner hands a neighbour's
                    # elevation to the chain, which is worse than absence
                    other = next((k for k in range(1, len(ii))
                                  if k < len(dd)
                                  and int(owner[int(ii[k])]) != cid), None)
                    if other is not None \
                            and float(dd[other]) <= _MASK_DIST_PT:
                        continue
                    # one region per spot: a mask redrawn over an already
                    # found break would double the patch for no new info
                    if any(np.hypot(r[1] - ctr[0], r[2] - ctr[1]) < 8.0
                           for r in regions.get(cid, [])):
                        continue
                    # the label follows the CHAIN under the mask, not the
                    # mask stroke: on C-04's south strip the masks run at
                    # 0 deg over contours tilted -10 to -17 deg, and OCR
                    # of the mask-angled patch clipped every digit - so
                    # the region takes the chain's local tangent
                    tv = tang[i]
                    regions.setdefault(cid, []).append(
                        (0, float(ctr[0]), float(ctr[1]),
                         float(tv[0]), float(tv[1]), L))
    return regions


# WHICH `how` VALUES MEAN "A LABEL ON THE SHEET WAS READ".
#
# One list, used by the server's own wording (infill_intervals' anchor
# sentence) and mirrored by ELEV_PROV in webui/app.js. "printed" is a claim
# about the NUMBER - that some text on the sheet really says it - and never
# about WHOSE number it is: a `radius` hit is the nearest printed token
# within max_d_pt, which on a crowded sheet is regularly a spot annotation
# belonging to something else (see _label_tag below).
PRINTED_HOWS = ("radius", "inline", "gap-ocr")


def is_printed_how(how) -> bool:
    """True only when a label printed on the sheet was actually read."""
    return how in PRINTED_HOWS


# A TAGGED ELEVATION IS NOT A CONTOUR LABEL.
#
# Oncor C-04's own LEGEND keys its spot annotations - it prints
#   "100.0  PROPOSED SPOT ELEVATION", "ME 100.0  MATCH EXISTING ELEVATION",
#   "TW 100.0  PROPOSED TOP OF WALL ELEVATION",
#   "BW 100.0  PROPOSED BOTTOM OF WALL ELEVATION",
#   "EW 100.0  PROPOSED END OF WALL ELEVATION"
# (convention read off THIS SHEET's legend, not an industry prior). The plan
# body prints the pair the other way round, "551.2 BW", on one text line.
#
# That matters because the radius pass matches the nearest printed number
# within 16 pt. On C-04 chain 168 the nearest number is 2.7 pt away and it
# is "551.2 BW" - a bottom-of-wall spot height, with a "550" contour leader
# a few points further off. Reported as a bare "EL 551.2" the chip states a
# wall height as a contour elevation, and nothing on screen says otherwise.
#
# So the tag travels with the match: any 1-4 letter ALL-CAPS token printed
# on the same text row, immediately before or after the number, is recorded
# as `label_tag` and the UI marks the chip with it. We do NOT expand the
# abbreviation - the sheet's legend is where its meaning lives.
_TAG_RE = re.compile(r"^[A-Z]{1,4}$")
_TAG_ROW_FRAC = 0.6      # vertical centres within 0.6x the number's height
_TAG_GAP_FRAC = 1.6      # horizontal gap up to 1.6x its height


def _tag_index(words: list) -> list:
    """The ALL-CAPS tokens a `_label_tag` lookup can attach to a number."""
    out = []
    for w in words:
        try:
            t = str(w[4]).strip()
        except (IndexError, TypeError):
            continue
        if _TAG_RE.match(t):
            out.append((float(w[0]), float(w[1]), float(w[2]), float(w[3]), t))
    return out


def _label_tag(w, tags: list):
    """The tag printed with this number on the same text row, or None.

    Nearest wins, and both orders are accepted: the plan body writes
    "551.2 BW" and the legend writes "BW 100.0".
    """
    x0, y0, x1, y1 = float(w[0]), float(w[1]), float(w[2]), float(w[3])
    h = max(y1 - y0, 1.0)
    cy = (y0 + y1) / 2
    best = None
    for ax0, ay0, ax1, ay1, t in tags:
        if abs((ay0 + ay1) / 2 - cy) > _TAG_ROW_FRAC * h:
            continue
        gap = ax0 - x1 if ax0 >= x1 else x0 - ax1
        if gap < -0.5 or gap > _TAG_GAP_FRAC * h:
            continue
        if best is None or gap < best[0]:
            best = (gap, t)
    return best[1] if best else None


def suggest_elevations(chains: list, words: list,
                       max_d_pt: float = 16.0,
                       gap_z: dict | None = None,
                       exclude: set | None = None,
                       diag: dict | None = None) -> list:
    """Auto-pair elevation-looking labels with the contour they sit on.

    Returns [{chain_id, z, label_xy, dist, how, label_tag}] - a SUGGESTION
    for the review UI; the human confirms/edits, per the agreed
    human-in-loop flow.
    `how` discloses the evidence: "inline" (native token in the chain's own
    break), "gap-ocr" (OCR of the break), "radius" (nearest label within
    max_d_pt), "inherited" (propagated along the contour, pass 2).
    `label_tag` is the ALL-CAPS annotation printed with the matched number
    ("BW", "TW", "ME", ...) or None - see _label_tag: a tagged number is a
    spot annotation the sheet's legend keys, not a contour label, and the
    chip has to be able to say so.
    Chains in `exclude` never seed and never take a label - see the
    explicit source ranking below.
    `diag`, when a dict is passed, is filled with what the LADDER GATE did
    (key "ocr_ladder": applied / dropped / the verdict) so the caller can
    report it. The rows themselves are the return value either way; a
    caller that passes nothing loses only the explanation.
    """

    exclude = exclude or set()
    tags = _tag_index(words)
    cands = []
    for w in words:
        t = str(w[4]).strip()
        if re.fullmatch(r"\d{3,4}(\.\d{1,2})?", t):
            v = float(t)
            if 10.0 <= v <= 9999.0:
                cands.append(((w[0] + w[2]) / 2, (w[1] + w[3]) / 2, v,
                              _label_tag(w, tags)))
    # AN OCR-ONLY POPULATION IS STILL A POPULATION.
    #
    # Until 2026-09-02 this returned [] whenever the page had no native
    # elevation-looking token, BEFORE the gap_z merge below - so a sheet
    # whose every label is a glyph outline (pymupdf: 0 words) got zero rows
    # although the gap-OCR pass had read its labels, and /earthwork answered
    # 400 "each surface needs at least one labeled contour". Measured on the
    # DEM-truth outline sheet dem_hurricane_bench_a_pad_2ft_outline p0: 17
    # cached gap reads (3376..3428), 0 rows. That is the corpus failure mode
    # the sheet was built to reproduce (text_layer: NONE), not a corner case.
    #
    # What an OCR-only population is ALLOWED to do (cands empty, gap_z set):
    #   - pass 0 (inline) and pass 1 (radius) have nothing to match and
    #     write nothing; `seen` reaches the merge empty;
    #   - the 25-ft ANCHOR rule below still runs, over the gap values alone,
    #     once there are 6+ of them: a lone impossible read (5388 among
    #     33xx) is still dropped. Mutually-supporting misreads pass it, as
    #     they already do on a native sheet;
    #   - the NATIVE BAND gate (3+ glyph-derived values, 100 ft) CANNOT run:
    #     there are no natives, and the server's `wit` filter and glyph-first
    #     group ranking have no native row and no resolved spot grade to
    #     lean on either (on an outline sheet the spot grades are outlines
    #     too). SINCE 2026-09-02 THE LADDER GATE STANDS IN FOR IT - see
    #     ladder_verdict and the block at the end of this function: with no
    #     native text anywhere on the page, the gap-OCR population must show
    #     a repeating contour interval on its own terms or none of it mints
    #     a row. It is a WEAKER test than the native band gate (a regular
    #     ladder of wrong digits would pass it) and it is not a substitute
    #     for the disclosure: every surviving row still carries
    #     how="gap-ocr", which webui/app.js ELEV_PROV renders as "read from
    #     a picture" (cls "ocr", solid border) with the tooltip that the
    #     digits themselves can be wrong. The `how` vocabulary is unchanged
    #     - a new value would render as "source unknown" there;
    #   - pass 2 (inherited) propagates gap-ocr rows along the contour
    #     exactly as it propagates native ones.
    # With cands NON-EMPTY nothing here changes: the early return, the
    # ranking and every guard are as before (tests/test_gap_ocr_rows.py
    # pins a frozen 36e065f fixture; tests/test_earthwork_regress.py pins
    # the volumes).
    if not cands and not gap_z:
        return []
    # Explicit source ranking, stored in the `dist` slot so the existing
    # keep-the-closest comparisons ARE the ranking (no first-writer-wins):
    #   perp  native token inside the chain's own break ("inline") - its
    #         perpendicular distance to the break chord, so a CLOSER
    #         radius token still wins the chain (on Stice, rank-flat
    #         inline let a neighbour's token in a phantom break outrank
    #         the chain's own on-line label 202 times)
    #   0.5   OCR of the chain's own break              ("gap-ocr") -
    #         never numerically displaces an inline match (the explicit
    #         same-text rule below is the only path), and never a radius
    #         match closer than _ON_LINE_PT: a native token that close is
    #         printed ON the line, and the PDF's own glyphs outrank a
    #         raster re-read of the same spot
    #   d     nearest-label radius match, d <= max_d_pt ("radius")
    #   999   inherited along the contour               ("inherited")
    out, seen, how, tag = [], {}, {}, {}

    # ONE WRITER FOR `seen`, AND IT CANNOT FORGET THE PROVENANCE.
    # The emit below used to read `how.get(cid, "radius")` - so a future
    # writer that set `seen` and forgot `how` would be REPORTED AS PRINTED,
    # and the UI's abstain branch could never fire for it. Every write goes
    # through here, `kind` is positional, and `tag` is reset with the value
    # so a replaced match can never keep the old one's annotation.
    def _put(cid, d, v, cx, cy, kind, tg=None):
        seen[cid] = (d, v, cx, cy)
        how[cid] = kind
        tag[cid] = tg

    samples, owner = [], []
    for c in chains:
        # a profile box or the title block is not plan ground - see
        # _NON_PLAN_LAYER. Filtering the sample pool once keeps both the
        # direct match below and the propagation in pass 2 off them.
        # Excluded chains (legend non-contours, white masks) leave the pool
        # entirely, so a label they used to steal now reaches the nearest
        # chain that can actually be a contour.
        if c.length < 60 or not _is_plan_chain(c) \
                or int(c.chain_id) in exclude:
            continue
        s, _ = _sample_polyline(np.asarray(c.pts), step=4.0, max_n=60000)
        if len(s):
            samples.append(s)
            owner += [c.chain_id] * len(s)
    if not samples:
        return []
    tree = cKDTree(np.vstack(samples))
    owner = np.array(owner)

    # Pass 0 - native tokens sitting inside a chain's own break. Geometry
    # (perpendicular distance to the break chord), not a bucket. A token
    # consumed here is REMOVED from the radius pool: on C-04 the property
    # line and mask strokes each grabbed a nearby contour's inline label as
    # their own z through the radius match - the theft this pass ends.
    # The break's OWNER must also be the STRICT nearest chain to the
    # token: on the Stice sheet (1"=200', terrace contours closer together
    # than the label text is tall) any laxer rule pulls labels sideways
    # into neighbours' break regions - a 2.0 pt margin moved 336 of the
    # operator's 1,487 verified rows off their values, 0.5 pt still moved
    # 124. With owner == nearest, the inline pass can never assign a
    # token to a different chain than the radius pass would, so its whole
    # effect is rank: the chain's own in-break label outranks farther
    # radius tokens and OCR re-reads. C-04's genuine in-break tokens all
    # have their own chain as the strict nearest once the mask and
    # property-line families are excluded from the pool.
    consumed = set()
    # no native token: nothing to place in a region (OCR-only path above)
    regions = _label_regions(chains, exclude=exclude) if cands else {}
    if regions:
        flat = []
        for cid, regs in regions.items():
            for kind, cx, cy, ux, uy, span in regs:
                flat.append((cid, kind, cx, cy, ux, uy, span))
        for ci, (tx, ty, v, tg) in enumerate(cands):
            best = None
            for cid, kind, cx, cy, ux, uy, span in flat:
                dx, dy = tx - cx, ty - cy
                lon = abs(dx * ux + dy * uy)
                per = abs(dx * -uy + dy * ux)
                if lon <= span / 2 + 3.0 and per <= _INLINE_PERP_PT:
                    if best is None or per < best[0]:
                        best = (per, cid)
            if best is None:
                continue
            per, cid = best
            d_near, i_near = tree.query([tx, ty])
            if int(owner[int(i_near)]) != cid:
                continue  # another chain owns this token
            consumed.add(ci)
            # rank by the token's PATH distance, the same metric the
            # radius pass uses - ranking by chord-perp let a phantom
            # region's far token outrank the chain's own on-line label
            # (43 Stice rows moved before this was equalised). Inline
            # keeps its tag (and OCR protection) but can never prefer a
            # token the radius pass would have rejected.
            d_rank = max(float(d_near), 0.05)
            if cid in seen and seen[cid][0] <= d_rank:
                continue
            _put(cid, d_rank, v, tx, ty, "inline", tg)
    for ci, (cx, cy, v, tg) in enumerate(cands):
        if ci in consumed:
            continue
        d, i = tree.query([cx, cy])
        if d > max_d_pt:
            continue
        cid = int(owner[i])
        # one contour can carry several copies of its label; keep closest
        if cid in seen and seen[cid][0] <= d:
            continue
        _put(cid, d, v, cx, cy, "radius", tg)
    # gap-OCR labels (see gap_label_elevations) are read out of the chain's
    # own break - stronger than any radius hit except a native token
    # actually printed on the line (see the ranking above)
    if gap_z:
        # OCR misreads produce lone impossible values (195 and 861 on a
        # 548-588ft sheet). Real elevations never stand alone: contours
        # step in small intervals and spot grades cluster with them, so a
        # gap-read value with no other elevation within 25ft - when the
        # sheet offers 6+ to compare against - is a misread, not terrain.
        anchor = sorted({s[1] for s in seen.values()}
                        | {v for v, _, _ in gap_z.values()})
        # Native band gate: with >= 3 distinct GLYPH-derived values on the
        # page (radius or inline - a real witnessed population), an OCR
        # value more than 100 ft from every one of them is a misread, not
        # a second surface. OCR only fills gaps in a surface the native
        # labels already witness; measured on Barn C3.0, the v2 pass also
        # read 826/710/324/307/120/100 out of dimension texts against a
        # 7,7xx-ft native population, and those mutually-supporting reads
        # sail through the 25-ft anchor rule below. 100 ft matches the
        # endpoint's surface-cluster gap.
        native_vals = sorted({s[1] for s in seen.values()})
        for cid, (v, cx, cy) in gap_z.items():
            cid = int(cid)
            if cid in exclude:
                continue
            if len(native_vals) >= 3 and min(
                    (abs(v - a) for a in native_vals), default=0.0) > 100.0:
                continue
            if len(anchor) >= 6:
                near = min((abs(v - a) for a in anchor if a != v),
                           default=0.0)
                if near > 25.0:
                    continue
            if cid not in seen or (how.get(cid) != "inline"
                                   and seen[cid][0] > 0.5
                                   and not (how.get(cid) == "radius"
                                            and seen[cid][0] <= _ON_LINE_PT)):
                _put(cid, 0.5, v, cx, cy, "gap-ocr")
            elif how.get(cid) == "inline" and abs(seen[cid][1] - v) > 0.01:
                # inline vs gap-OCR disagreement. Two measured shapes:
                #  - DIFFERENT texts occupy the area: on C-04 the break
                #    held a vector-exploded label (569/570/571) and a
                #    nearby native SPOT value (565.2/567.8/570.3) merely
                #    overlapped it - the OCR read was right 3 of 3, so a
                #    structurally different value defers to the OCR;
                #  - the SAME text, misread: on Barn C3.0 the native 7725
                #    sat in its own break and the OCR returned 7715 - one
                #    digit off at equal length. Native glyphs are exact;
                #    a raster re-read of the same string cannot beat
                #    them, so equal-length/one-digit-apart keeps inline.
                a, b = f"{seen[cid][1]:g}", f"{v:g}"
                same_text = (len(a) == len(b)
                             and sum(x != y for x, y in zip(a, b)) <= 1)
                # ...and the OCR value must be corroborated by another
                # elevation within 5 ft (a contour interval or two on the
                # measured sheets - C-04 steps 1 ft, Stice 2.5-5): the
                # C-04 wins (569/570/571 over spot values) all have
                # adjacent-contour support, while Stice's one such
                # override was a 400 misread displacing a verified 472.5
                # with nothing within 25 ft of it.
                support = min((abs(v - x) for x in anchor if x != v),
                              default=1e9)
                if not same_text and support <= 5.0:
                    _put(cid, 0.5, v, cx, cy, "gap-ocr")
    # Pass 2 - propagate along the contour. A long contour splits into
    # several chains at wide gaps, and only the fragment nearest a printed
    # label matches directly (Oncor p4: 29 labeled chains, but each
    # contour's sibling fragments showed nothing on click). End-to-end
    # continuation identifies the same contour: ends within 60pt, each
    # end's outward tangent pointing at the other, and small lateral
    # offset - adjacent PARALLEL contours never meet end-to-end, so a
    # neighbour's elevation cannot leak across. A component whose direct
    # matches disagree keeps only its direct matches (ambiguity is worse
    # than absence).
    ch_ok = [c for c in chains
             if c.length >= 60 and len(c.pts) >= 2 and _is_plan_chain(c)
             and int(c.chain_id) not in exclude]
    ends, tangs, cid_of = [], [], []
    for c in ch_ok:
        p = np.asarray(c.pts, float)
        for e_i, (end, inner) in enumerate(((p[0], p[min(3, len(p) - 1)]),
                                            (p[-1], p[max(len(p) - 4, 0)]))):
            t = end - inner
            n = float(np.hypot(*t))
            if n < 1e-6:
                t = np.array([1.0, 0.0])
            else:
                t = t / n
            ends.append(end)
            tangs.append(t)
            cid_of.append(int(c.chain_id))
    if ends:
        ends = np.asarray(ends)
        etree = cKDTree(ends)
        parent = {int(c.chain_id): int(c.chain_id) for c in ch_ok}

        def _find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        pairs = sorted(map(tuple, etree.query_pairs(60.0)))
        chain_of = {int(c.chain_id): c for c in ch_ok}
        for i, j in pairs:
            if cid_of[i] == cid_of[j]:
                continue
            # A contour continuing itself past a gap keeps its CAD layer and
            # its linetype. Anything that changes either is a different
            # object whose end merely happens to land nearby, and welding it
            # in hands it an elevation it never had: on Guide civil page 3 a
            # BG-L-Riprap rock apron took 7915 as proposed, and an
            # A-GR PATIO chain took 7921 from a label 907 pt away - 126 ft on
            # the ground. Union-find is transitive, so without this guard
            # each 60 pt hop compounds into an unbounded reach.
            ca, cb = chain_of.get(cid_of[i]), chain_of.get(cid_of[j])
            if ca is None or cb is None:
                continue
            if (getattr(ca, "layer", "") or "") != (getattr(cb, "layer", "") or ""):
                continue
            if ca.klass != cb.klass:
                continue
            v = ends[j] - ends[i]
            d = float(np.hypot(*v))
            if d < 1e-6:
                cos_i = cos_j = 1.0
                lat = 0.0
            else:
                u = v / d
                cos_i = float(tangs[i] @ u)
                cos_j = float(tangs[j] @ -u)
                lat = abs(float(tangs[i][0] * v[1] - tangs[i][1] * v[0]))
            if cos_i < 0.86 or cos_j < 0.86 or lat > 5.0:
                continue
            ra, rb = _find(cid_of[i]), _find(cid_of[j])
            if ra != rb:
                parent[ra] = rb
        comp = {}
        for c in ch_ok:
            comp.setdefault(_find(int(c.chain_id)), []).append(int(c.chain_id))
        for members in comp.values():
            zs = {seen[m][1] for m in members if m in seen}
            if len(zs) != 1:
                continue  # unlabeled or conflicting component
            z = zs.pop()
            src = next(m for m in members if m in seen and seen[m][1] == z)
            _, _, cx, cy = seen[src]
            for m in members:
                if m not in seen:
                    # dist 999 is a SENTINEL, not a measurement - see
                    # EW_DIST_INHERITED in webui/app.js, which is why no
                    # screen may render it as "999pt away".
                    _put(m, 999.0, z, cx, cy, "inherited", tag.get(src))
    for cid, (d, v, cx, cy) in seen.items():
        # NO DEFAULT PROVENANCE. `_put` is the only writer and it always
        # records one, so this can only fire if that invariant is broken -
        # and then it must ABSTAIN ("unknown"), never claim "radius", which
        # the UI reads as printed.
        out.append({"chain_id": cid, "z": v,
                    "label_xy": [round(cx, 1), round(cy, 1)],
                    "dist": round(float(d), 1),
                    "how": how.get(cid) or "unknown",
                    "label_tag": tag.get(cid)})
    rows = sorted(out, key=lambda r: r["z"])
    return _ladder_gate(rows, diag)


# NO NATIVE WITNESS, NO FREE PASS. The three filters that police an OCR row
# all key on native text (see the block inside suggest_elevations), so on a
# page where NOT ONE row is glyph-backed they are all inert and the gap-OCR
# population minted rows unchecked. This is the stand-in, and it keys on the
# only thing such a page has: the shape of the population itself.
#
# WHERE IT LIVES AND WHY HERE. In suggest_elevations, not in server.py's
# suggest route. The rule reads nothing but the rows' own z values and the
# `how` already on them, which is exactly what this function owns; the route
# holds spot grades and legend boxes that would make no difference to it,
# and every other caller of this function - the offline recipe under
# out/verify, tests/test_ew_suggest.py, tests/test_gap_ocr_rows.py - would
# have been left unguarded had it gone in the route. The route's job is to
# REPORT the drop (suggestions_dropped, ocr_ladder), which it now does.
#
# IT RUNS ONLY WHEN NO ROW IS GLYPH-BACKED, WHICH IS NOT THE SAME AS "ONLY
# ON OCR-ONLY SHEETS". This comment used to say the gate "never fires" on a
# native sheet. That is false, and it matters, because it was doing the work
# of a safety argument. Measured over a 30-page sweep: Oncor engine page 4
# is a native sheet whose elevation rows happen to be gap-OCR and inherited
# with not one inline or radius row, so the gate DOES fire there - it
# ladders at 1 ft over 42 of 45 gaps (share 0.93) and keeps all 106 rows.
# Nothing moves, but the guarantee is "it happens to ladder cleanly", not
# "it cannot reach this page", and those are different promises. The pages
# where the gate genuinely cannot fire are the ones with inline/radius rows:
# Oncor p3 (C-04, 20 radius rows), Stice p0, all four native DEM sheets, and
# every Barn C4.x grading sheet. Their row sets are byte-identical to before.
def _ladder_gate(rows: list, diag: dict | None) -> list:
    gap = [r for r in rows if r.get("how") == "gap-ocr"]
    native = [r for r in rows if r.get("how") in ("inline", "radius")]
    if not gap or native:
        if diag is not None:
            diag["ocr_ladder"] = {
                "applied": False, "dropped": 0,
                "why": ("this page has glyph-derived elevation rows, so the "
                        "native filters apply and the ladder gate stands "
                        "down") if native else
                       "no gap-OCR rows on this page"}
        return rows
    v = ladder_verdict([r["z"] for r in rows])
    if not v["testable"]:
        # ABSTAIN, DO NOT REFUSE. Too few values to show a step either way,
        # and a population this small is the floor's problem, not the
        # gate's - the one such page in the sweep (Home2 Suites C3 Grading
        # p0, {411, 444}, 9 rows) is refused by cut_fill on its proposed
        # side, while 2 values landing on both surfaces would be a
        # legitimate one-step pair. Dropping them here would only hide from
        # the operator what the reader saw. They stay, disclosed as gap-ocr,
        # and the verdict says why no test was possible.
        kept = rows
    elif v["laddered"]:
        kept = [r for r in rows if v["keep_lo"] <= r["z"] <= v["keep_hi"]]
    else:
        kept = []
    if diag is not None:
        diag["ocr_ladder"] = {"applied": True,
                              "dropped": len(rows) - len(kept),
                              "kept": len(kept), "verdict": v}
    return kept


# OCR patch budget, counted in TESSERACT CALLS (the numpy gates are free).
# Candidate regions measured with the v2 triggers: C-04 ~950 raw (202
# chains, ~600 reach tesseract), Barn C3.0 ~2,000, Stice 600-acre ~3,500.
# The global order below is round-robin across chains (every chain's best
# region before any chain's second - the v1 first-come cap starved C-04's
# proposed contours completely, chains 799+ hit `patches_before=400` on
# all 20). 1,400 covers every gate-passing region on C-04 (900 cut it off
# in round ~3 and lost chain 800's fourth-ranked label break) and bounds
# the one-time per-page cache build at ~3 min of tesseract on the largest
# measured sheet.
_OCR_BUDGET = 1400
# Per chain: stop after three reads - a long contour repeats its label,
# and the third read breaks the tie when one of the first two is a
# misread (the majority rule in the conflict resolution below). A fourth
# adds nothing that the ranking can use.
_OCR_READS_PER_CHAIN = 3
# regions between progress reports (gap_label_elevations `progress`): ten is
# ~2 s unloaded at 0.23 s a call, well under the panel's poll
_PROGRESS_EVERY = 10
# Patch band height: label text on these sheets is 7-10 pt tall; a 12 pt
# band clears it, while v1's 22 pt band pulled in the neighbouring contour
# on C-04's tight strip (~10-15 pt between contours) and its labels with it.
_OCR_BAND_PT = 12.0
# "Ink" is anything darker than 235/255. C-04's screened existing labels
# anti-alias to 193-230 in the 150 dpi raster (thin 0.6-gray strokes) - a
# 200 threshold read whole gray labels as blank paper and every
# existing-contour read vanished; 235 still excludes clean paper (255).
_OCR_INK_LEVEL = 235
# A patch more than 35% ink sits inside hatch or a solid fill, not a
# label (inferred from the C-04 patches, stated as inference).
_OCR_INK_MAX = 0.35
# The label-vs-linework discriminator is ink OFF the chord line: the
# polyline cannot say which segments are pen-up (a drawn 18 pt dash and an
# 18 pt label break are the same jump), but label digits are 7-10 pt tall
# while the linework this must reject is <= 3.5 pt wide - so beyond
# +-2.0 pt of the chord a dash or an empty break is clean and a label is
# not. Measured on C-04's 40 verified label regions: off-chord ink
# 0.025-0.32; drawn dashes and empty breaks sit at ~0. 0.02 passed 40/40
# labels and prunes ~40% of the non-label regions before tesseract, which
# is what keeps the budget for regions that can pay (v1 spent its whole
# 400-patch first-come budget on drawn dashes and starved chains 799+).
_OCR_OFFCHORD_MIN = 0.02
_OCR_OFFCHORD_HALF_PT = 2.0


def _ocr_digit_string(pytesseract, re, img) -> tuple:
    """(valid elevation string or None, raw attempts) for one binary image.

    psm 7 treats the patch as one text line; psm 8 as one word - the
    fallback recovers labels whose leading digit psm 7 clips (measured on
    C-04: 803/804 read only under psm 8).
    """
    outs = []
    for psm in (7, 8):
        try:
            t = pytesseract.image_to_string(
                img,
                config=f"--psm {psm} -c tessedit_char_whitelist=0123456789.",
            ).strip().strip(".")
        except Exception:
            return None, outs
        outs.append(t)
        if re.fullmatch(r"\d{3,4}(\.\d{1,2})?", t):
            return t, outs
    return None, outs


def _read_label_patch(cv2, pytesseract, re, patch) -> str | None:
    """OCR one flattened label patch, two variants with an explicit rule.

    V1: 2x upscale + Otsu, geometry untouched. V2: same, then drop
    connected components that are line-like (w > 2.2h), sub-glyph
    (h < 10 px at 2x150dpi ~ a 2.4 pt speck), or hollow-diagonal
    (area < 15% of box - a stroke crossing the band), and tight-crop what
    remains. V2 unglues digits from the dashes that touch them but can
    also hallucinate: on C-04 it repaired 5833->583 yet misread 572 as
    972. Rule, measured against the 47 hand-verified C-04 labels:
      - V1 valid and V2 a shorter substring of it -> V2 (V1 = same digits
        plus a stray stroke read as an extra digit: 5657->565, 5797->579);
      - V1 valid otherwise -> V1;
      - only V2 valid -> V2, but ONLY if every non-empty V1 raw attempt is
        a substring of it (V1 glimpsed a fragment of the same digits; this
        rejected V2's one plausible-but-wrong read, 575 for a true 577).
    """
    big = cv2.resize(patch, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, bw1 = cv2.threshold(big, 0, 255,
                           cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    t1, raw1 = _ocr_digit_string(pytesseract, re, bw1)
    _, inv = cv2.threshold(big, 0, 255,
                           cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(inv, 8)
    boxes = []
    keep = np.zeros_like(inv)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if h < 10 or h > inv.shape[0] - 2 or w > 2.2 * h \
                or area < 0.15 * w * h:
            continue
        keep[lab == i] = 255
        boxes.append((x, y, w, h))
    t2 = None
    if boxes:
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[0] + b[2] for b in boxes)
        y1 = max(b[1] + b[3] for b in boxes)
        tight = 255 - keep[max(y0 - 6, 0):y1 + 6, max(x0 - 6, 0):x1 + 6]
        t2, _ = _ocr_digit_string(pytesseract, re, tight)
    if t1 is not None and t2 is not None:
        if t2 != t1 and len(t2) < len(t1) and t2 in t1:
            return t2
        return t1
    if t1 is not None:
        return t1
    if t2 is not None and all(r in t2 for r in raw1 if r):
        return t2
    return None


def gap_label_elevations(chains: list, page_img: np.ndarray,
                         px_per_pt: float,
                         max_patches: int = _OCR_BUDGET,
                         exclude: set | None = None,
                         time_budget_s: float | None = None,
                         idle_budget_s: float | None = None,
                         diag: dict | None = None,
                         progress=None) -> dict:
    """Read elevation labels printed INSIDE breaks of their own contour.

    Contour elevations sit in a gap of the line, rotated to follow it.
    Native text misses them when the glyphs are exploded to vectors, and
    whole-page OCR misses them because they are rotated (Oncor p4: the
    571-576 labels were found by neither). The chain itself knows where a
    label can sit - a merged break of its own polyline, or a white mask
    stroke painted along it (_label_regions) - so a small patch per
    region, rotated flat by the region's own direction, reads them.

    Patches are scheduled round-robin across chains (every chain's best
    region before any chain's second), regions ranked mask-first then by
    closeness to the measured label span - an explicit ranking, so a
    budget cut trims the least label-like patches instead of whichever
    chains happened to come last (the v1 failure on C-04).

    Returns {chain_id: (z, cx_pt, cy_pt)}. Chains whose gap labels
    disagree are dropped - ambiguity is worse than absence.
    """
    import re

    try:
        import cv2
        import pytesseract
    except Exception:
        return {}
    if page_img.ndim == 3:
        gray = cv2.cvtColor(page_img, cv2.COLOR_RGB2GRAY)
    else:
        gray = page_img
    regions = _label_regions(chains, exclude=exclude)
    if not regions:
        return {}
    # rank regions inside each chain, then interleave chains round-robin;
    # within one round the most label-like regions go first so the budget
    # cut is by likelihood, not by chain order
    for regs in regions.values():
        regs.sort(key=lambda r: (r[0], abs(r[5] - _LABEL_SPAN_PT)))
    order = []
    rnd = 0
    while True:
        layer = [(cid, regs[rnd]) for cid, regs in regions.items()
                 if len(regs) > rnd]
        if not layer:
            break
        layer.sort(key=lambda t: (t[1][0], abs(t[1][5] - _LABEL_SPAN_PT),
                                  t[0]))
        order += layer
        rnd += 1

    reads: dict = {}
    n_ocr = 0  # the budget counts tesseract calls; the numpy gates that
    # reject empty breaks and drawn dashes are ~free and must not starve
    # later chains the way v1's patch counter did
    #
    # AND A WALL CLOCK. The call budget bounds tesseract CALLS, not time:
    # Barn C3.0 (a site plan with no contours at all) spent the whole 1,400
    # on utility and property linework - more than ten minutes of OCR that
    # read nothing (a 600 s probe timed out before it finished) with the
    # panel on "finding elevations" the whole while. `time_budget_s` stops the pass, and `diag` says so - what was
    # read out of what was planned - so a truncated read is reported as
    # truncated rather than served as complete.
    # Two clocks: `time_budget_s` is a wide hard cap (a runaway pass ends
    # and diag says it was cut short); `idle_budget_s` stops a pass that
    # has READ NOTHING by then. The idle clock is OFF on the service by
    # default - measured 2026-09-08, Barn C3.0 (no contours) does get two
    # raw reads at call 158 / 33 s, and under the 10x load this box sees
    # from other tenants a 120 s idle clock would empty Belle Farms (first
    # read 18 s unloaded) before it touched C3.0. server.py OCR_BUDGET_S
    # has the per-sheet table. diag says which clock fired, if any.
    import time as _time
    t0 = _time.monotonic()
    stopped_early = False
    stop_reason = None
    # PROGRESS, for the panel. `progress` (server-side: a sidecar the panel
    # polls) is told every _PROGRESS_EVERY regions where the pass is:
    # regions visited of planned - the honest fraction, since a region may
    # be skipped by the ink gates without a tesseract call - plus calls and
    # raw reads so far. Until 2026-09-09 the panel could show only a clock,
    # because this loop reported nothing until it was done.
    n_seen = 0

    def _tell(final=False):
        if progress is None:
            return
        try:
            progress({"pos": n_seen, "planned": len(order), "calls": n_ocr,
                      "reads": len(reads),
                      "seconds": round(_time.monotonic() - t0, 1),
                      "final": final})
        except Exception:
            pass

    _tell()
    for cid, (kind, cx, cy, ux, uy, span) in order:
        n_seen += 1
        if n_seen % _PROGRESS_EVERY == 0:
            _tell()
        if n_ocr >= max_patches:
            break
        elapsed = _time.monotonic() - t0
        if time_budget_s is not None and elapsed > time_budget_s:
            stopped_early, stop_reason = True, "cap"
            break
        if (idle_budget_s is not None and elapsed > idle_budget_s
                and not reads):
            stopped_early, stop_reason = True, "idle"
            break
        if len(reads.get(cid, ())) >= _OCR_READS_PER_CHAIN:
            continue
        ang = float(np.degrees(np.arctan2(uy, ux)))
        if ang > 90:
            ang -= 180
        elif ang <= -90:
            ang += 180
        # crop a square that covers the rotated text box, rotate flat.
        # A break region needs margin (the digits can outgrow the visible
        # break); a mask region must stay tight - C-04's south strip
        # prints its labels ~40 pt apart, and a wide mask patch caught the
        # NEIGHBOUR's digits alongside the chain's own.
        w_pt = span + (16.0 if kind == 0 else 30.0)
        h_pt = _OCR_BAND_PT
        D = int((np.hypot(w_pt, h_pt) + 6) * px_per_pt)
        cxp, cyp = int(cx * px_per_pt), int(cy * px_per_pt)
        x0, y0 = max(cxp - D // 2, 0), max(cyp - D // 2, 0)
        crop = gray[y0:y0 + D, x0:x0 + D]
        if crop.size == 0:
            continue
        M = cv2.getRotationMatrix2D((cxp - x0, cyp - y0), ang, 1.0)
        rot = cv2.warpAffine(crop, M, (crop.shape[1], crop.shape[0]),
                             borderValue=255)
        bw = int(w_pt * px_per_pt / 2)
        bh = int(h_pt * px_per_pt / 2)
        mx, my = cxp - x0, cyp - y0
        patch = rot[max(my - bh, 0):my + bh, max(mx - bw, 0):mx + bw]
        if patch.size == 0:
            continue
        # ink gates (see the constants): a solid/hatch patch cannot be a
        # label, and a patch with no ink OFF the chord is an empty break
        # or the drawn line itself - skipping both keeps the tesseract
        # time for patches that can pay
        if float(np.mean(patch < _OCR_INK_LEVEL)) > _OCR_INK_MAX:
            continue
        off = int(_OCR_OFFCHORD_HALF_PT * px_per_pt)
        mid = patch.shape[0] // 2
        rows = np.r_[0:max(mid - off, 0),
                     min(mid + off + 1, patch.shape[0]):patch.shape[0]]
        if len(rows) and float(
                np.mean(patch[rows] < _OCR_INK_LEVEL)) < _OCR_OFFCHORD_MIN:
            continue
        n_ocr += 1
        txt = _read_label_patch(cv2, pytesseract, re, patch)
        if txt is None and kind == 0 and n_ocr < max_patches:
            # mask retry with break-sized margin: the tight mask patch
            # clips a label whose digits outgrow the mask stroke (C-04
            # chain 806); the wide patch stays second choice because in
            # a dense strip it can catch a neighbour's digits - a wrong
            # wide read still has to survive the band + kind ranking
            wide = span + 30.0
            D2 = int((np.hypot(wide, h_pt) + 6) * px_per_pt)
            x2, y2 = max(cxp - D2 // 2, 0), max(cyp - D2 // 2, 0)
            crop2 = gray[y2:y2 + D2, x2:x2 + D2]
            if crop2.size:
                M2 = cv2.getRotationMatrix2D((cxp - x2, cyp - y2), ang, 1.0)
                rot2 = cv2.warpAffine(crop2, M2,
                                      (crop2.shape[1], crop2.shape[0]),
                                      borderValue=255)
                bw2 = int(wide * px_per_pt / 2)
                mx2, my2 = cxp - x2, cyp - y2
                patch2 = rot2[max(my2 - bh, 0):my2 + bh,
                              max(mx2 - bw2, 0):mx2 + bw2]
                if patch2.size:
                    n_ocr += 1
                    txt = _read_label_patch(cv2, pytesseract, re, patch2)
        if txt is None:
            continue
        v = float(txt)
        if 10.0 <= v <= 9999.0:
            reads.setdefault(cid, []).append((v, float(cx), float(cy), kind))
    # Conflict resolution - an explicit ranking, measured on C-04's
    # misread population (a line stroke read as an extra or swapped
    # digit: 5674/567, 375/575, 978/578, 382/582, 904/584, 675/575):
    #  1. dominant band: cluster every read value on GAPS > 30 ft (a
    #     gap-walk, not a bucket) and, with >= 6 distinct values and a
    #     band of >= 3, keep only reads inside the biggest band.
    #     Misreads SUPPORT EACH OTHER under a nearest-neighbour rule -
    #     C-04 read both 5657 and 5674, 17 ft apart, and each vouched
    #     for the other - but they cannot outnumber the real labels'
    #     band. 30 because a sheet's contour labels step 1-10 ft (C-04
    #     545-590 dense at 1 ft, Stice 425-521, Barn 7583-7900), while
    #     the closest measured leading-digit misread, 675 against a 590
    #     band edge, sits 85 away; the endpoint's 100 ft surface gap
    #     let that one in;
    #  2. majority: a value read from two regions beats a value read
    #     from one (the label repeats along the contour, the misread
    #     does not repeat the same way);
    #  3. a single break (kind 1) value beats mask reads - a mask patch
    #     in a dense strip can catch a NEIGHBOUR's label, a chain's own
    #     break cannot;
    #  4. still ambiguous -> drop the chain (worse than absence).
    all_vals = sorted({v for vals in reads.values() for v, _, _, _ in vals})
    band = None
    if len(all_vals) >= 6:
        groups, cur = [], [all_vals[0]]
        for prev, v in zip(all_vals, all_vals[1:]):
            if v - prev > 30.0:
                groups.append(cur)
                cur = []
            cur.append(v)
        groups.append(cur)
        top = max(groups, key=len)
        if len(top) >= 3:
            band = set(top)
    found: dict = {}
    for cid, vals in reads.items():
        if band is not None:
            vals = [t for t in vals if t[0] in band]
            if not vals:
                continue
        zs = {v for v, _, _, _ in vals}
        if len(zs) > 1:
            counts = {}
            for v, _, _, _ in vals:
                counts[v] = counts.get(v, 0) + 1
            best = max(counts.values())
            lead = [v for v, n in counts.items() if n == best]
            if best >= 2 and len(lead) == 1:
                vals = [t for t in vals if t[0] == lead[0]]
                zs = {lead[0]}
        if len(zs) > 1:
            brk = [t for t in vals if t[3] == 1]
            if len({v for v, _, _, _ in brk}) == 1:
                vals = brk
                zs = {v for v, _, _, _ in vals}
        if len(zs) == 1:
            v, cx, cy, _ = vals[0]
            found[int(cid)] = (v, cx, cy)
    _tell(final=True)
    if diag is not None:
        diag.update({"patches_planned": len(order), "ocr_calls": n_ocr,
                     "seconds": round(_time.monotonic() - t0, 1),
                     "stopped_early": stopped_early,
                     "stop_reason": stop_reason,
                     "idle_budget_s": idle_budget_s,
                     "time_budget_s": time_budget_s,
                     "chains_read": len(found)})
    return found


# --- existing vs proposed: the surface split ------------------------------
# The suggest reply must say WHICH rule decided each row (split_by), and the
# rules apply in an explicit precedence - never first-writer-wins:
#   a. legend verdict   the sheet's own legend names the family
#   b. color screening  gray = screened existing, dark = proposed - only
#                       when the page actually draws both populations
#   c. width median     today's behaviour, the monochrome fallback
_LEGEND_TAG = re.compile(r"\(legend\)\s*$", re.I)
# Screening constants, measured on Oncor C-04: existing contours print
# 0.5-0.6 equal-RGB gray, proposed contours 0.0 black; the sheet's
# annotation grays sit at 0.502 and 0.702. A "gray" must have near-equal
# channels (tolerance 0.05 covers AutoCAD's 0.5019... float grays) - a real
# color (blue water line, red demo) gets NO screening verdict and falls
# through to width rather than being guessed at.
_EQ_RGB_TOL = 0.05
# Gray band: above _DARK_MAX with headroom (0.35), below white ink (0.88 <
# WHITE_MIN so no stroke is both "gray" and "white").
_GRAY_BAND = (0.35, 0.88)
# Dark: C-04 proposed is 0.0; 0.30 leaves room for near-black plots while
# staying clear of the 0.5 grays.
_DARK_MAX = 0.30
# A population is >= 2 rows of that ink: one stray gray stroke on an
# otherwise black-and-white sheet must not flip the whole page to color
# screening (the monochrome Barn sheets are exactly that case).
_POP_MIN = 2
# ...AND the smaller population must be a real SHARE of the sheet, not two
# strokes. The absolute floor above does not do what its own comment claims:
# two strays are enough to trip it, and measured 2026-09-03 two sheets in the
# corpus sit exactly one stroke from that edge -
#
#   sheet                       gray   dark   minority share   screening
#   Oncor C-04 p3                 89     26         23%        fires, CORRECT
#   Oncor C-06 p5                 60     15         20%        fires, CORRECT
#   Oncor C-08 p7                 34     18         35%        fires, CORRECT
#   Belle Farms ROAD GRADING      39      1          2%        held off
#   C2.1-Grading-Plan              1     17          6%        held off
#   C4.0 / C4.1-C4.4 (mono)        0   5-13          0%        held off
#
# - Belle Farms would screen 39 rows against 2 if it carried one more dark
# stroke, and C2.1 would screen 17 against 2 with one more gray. Neither
# sheet draws two populations; each just happens to carry a stray.
#
# 15% sits in the empty band between the highest wrong case (6%) and the
# lowest right one (20%), so it closes both near-misses and MOVES NO SHEET
# MEASURED: all three correct pages keep screening, all five held-off pages
# stay held off. The threshold is a share of the seeded rows because that is
# the quantity that distinguishes "the drafter screened one population" from
# "a stray got in" - an absolute count cannot, at any value, since a big
# sheet's stray count scales with the sheet.
_POP_SHARE_MIN = 0.15
# inked legend rows a side before the legend's own polarity overrides the
# grey-existing / dark-proposed convention (split_rows_surface)
_POLARITY_MIN = 2


def _ink_kind(color) -> str | None:
    """"gray" / "dark" / "white" for a near-equal-RGB stroke, else None."""
    if not color:
        return None
    try:
        vals = [float(v) for v in color]
    except (TypeError, ValueError):
        return None
    if not vals:
        return None
    if max(vals) - min(vals) > _EQ_RGB_TOL:
        return None  # a real color; screening says nothing about it
    v = sum(vals) / len(vals)
    if v >= WHITE_MIN:
        return "white"
    if v <= _DARK_MAX:
        return "dark"
    if _GRAY_BAND[0] <= v <= _GRAY_BAND[1]:
        return "gray"
    return None


def family_verdicts(styles: list) -> dict:
    """What the sheet's own legend says each style family is.

    {style_id: {"role", "label"}} for legend-matched families only:
      "existing"/"proposed"  a CONTOUR row that names its surface
      "contour"              a CONTOUR row that does not
      "noncontour"           any other legend row (property line, swale,
                             grade break, fence...) - NEVER seeds rows
    Matching is on the legend row's OWN words (CONTOUR / EXIST / PROPOSED),
    not an invented taxonomy of line types (CLAUDE.md section 4); anything
    the legend did not match falls through to color/width unlabeled.
    """
    out: dict = {}
    for s in styles or []:
        if not isinstance(s, dict):
            continue
        sid = s.get("style_id")
        lab = s.get("suggested_label") or ""
        if sid is None or not _LEGEND_TAG.search(lab):
            continue
        up = lab.upper()
        if "CONTOUR" in up:
            # BOTH TOKENS -> ABSTAIN. This was a substring test with EXIST
            # checked first, so an ordinary caption like "PROPOSED CONTOUR TO
            # MATCH EXISTING GRADE (legend)" resolved to "existing" and put
            # every chain in that family confidently on the wrong surface -
            # and the legend tier has absolute precedence in
            # split_rows_surface with no consistency check behind it, so
            # nothing downstream could catch it. A row naming both surfaces
            # does not say which one it IS; "contour" keeps the family
            # seeding rows while letting colour and width decide the side,
            # which is the same shape as the scale reader abstaining on a
            # contradiction rather than picking the first candidate.
            has_ex, has_pr = "EXIST" in up, "PROPOSED" in up
            role = ("contour" if has_ex and has_pr
                    else "existing" if has_ex
                    else "proposed" if has_pr
                    else "contour")
        else:
            role = "noncontour"
        out[int(sid)] = {"role": role, "label": lab.strip()}
    return out


def split_rows_surface(rows: list, chain_info: dict,
                       verdicts: dict) -> dict:
    """Assign surface + split_by to each suggest row, by the precedence
    documented above. Mutates rows in place; returns {rule: count}.

    chain_info: {chain_id: {"style_id", "color", "width"}} from the stored
    detect result. Rows whose chain is unknown fall to the width rule.
    """
    counts = {"legend": 0, "color": 0, "width": 0}
    undecided = []
    kinds = {}
    for r in rows:
        info = chain_info.get(r.get("chain_id")) or {}
        kinds[r["chain_id"]] = _ink_kind(info.get("color"))
        v = verdicts.get(info.get("style_id")) or {}
        role = v.get("role")
        if role in ("existing", "proposed"):
            r["surface"] = role
            r["split_by"] = "legend"
            counts["legend"] += 1
        else:
            undecided.append(r)
    # color screening only where the page truly draws both populations -
    # counted over ALL seeded rows so a legend-decided family still proves
    # the sheet is a two-ink sheet
    n_gray = sum(1 for k in (kinds[r["chain_id"]] for r in rows)
                 if k == "gray")
    n_dark = sum(1 for k in (kinds[r["chain_id"]] for r in rows)
                 if k == "dark")
    # BOTH floors: at least _POP_MIN rows of each ink, and the smaller of the
    # two at least _POP_SHARE_MIN of every seeded row on the sheet. The share
    # is over len(rows), not over gray+dark, so a sheet that is mostly a third
    # ink cannot have two small populations vote it two-toned between them.
    _ink_share = (min(n_gray, n_dark) / len(rows)) if rows else 0.0
    two_inks = (n_gray >= _POP_MIN and n_dark >= _POP_MIN
                and _ink_share >= _POP_SHARE_MIN)
    # WHICH INK IS WHICH. Grey = existing, dark = proposed is a drafting
    # convention (screened existing ground under solid proposed work), not
    # something a sheet states - and a sheet CAN say otherwise: its legend
    # rows name the existing and proposed families, and those families
    # carry ink. When both named families are present and inked the
    # opposite way round, the legend wins for every colour-decided row;
    # when they agree, the convention is confirmed by the sheet; when the
    # legend names neither (or both carry the same ink), the convention
    # stands alone and the panel says so. counts["polarity"] carries the
    # verdict out to the reply.
    ink_of = {"existing": {"gray": 0, "dark": 0},
              "proposed": {"gray": 0, "dark": 0}}
    for r in rows:
        if r.get("split_by") == "legend":
            k = kinds[r["chain_id"]]
            if k in ("gray", "dark"):
                ink_of[r["surface"]][k] += 1

    # Two inked rows a side before the legend may flip the convention: one
    # chain is a sample, not a family (the precedence test in
    # tests/test_ew_suggest.py draws one contrarian chain per legend row
    # and expects the convention to stand).
    def _major(d):
        if d["gray"] == d["dark"] or d["gray"] + d["dark"] < _POLARITY_MIN:
            return None
        return "gray" if d["gray"] > d["dark"] else "dark"
    ex_ink, pr_ink = _major(ink_of["existing"]), _major(ink_of["proposed"])
    gray_is = "existing"
    if ex_ink and pr_ink and ex_ink != pr_ink:
        gray_is = "existing" if ex_ink == "gray" else "proposed"
        counts["polarity"] = ("legend-confirmed" if gray_is == "existing"
                              else "legend-flipped")
    else:
        counts["polarity"] = "convention"
    dark_is = "proposed" if gray_is == "existing" else "existing"
    still = []
    for r in undecided:
        k = kinds[r["chain_id"]]
        if two_inks and k in ("gray", "dark"):
            r["surface"] = gray_is if k == "gray" else dark_is
            r["split_by"] = "color"
            counts["color"] += 1
        else:
            still.append(r)
    # width fallback: the median rule the UI applies today, computed over
    # every returned row so a monochrome sheet reproduces today's split
    # exactly.
    #
    # ...EXCEPT WHEN IT CANNOT SPLIT. Belle Farms ROAD GRADING (40 rows) and
    # C2.1-Grading (18 rows) carry one line weight: every row is <= the
    # median, so every contour was filed as EXISTING with nothing on the
    # sheet to say so - the symptom Koby reported ("it was labelled EXIST or
    # skipped"). The same happens when the rule lands every row on ONE side
    # for any reason. A guess that cannot distinguish is not a guess, it is
    # a default wearing a guess's clothes; here the rows are left with NO
    # surface (split_by "none") and the panel says the sheet gave no
    # evidence, so the operator assigns them - or says "all existing" if
    # that is what the sheet draws. (Corpus measurement: see
    # tests/test_split_guards.py, which records which sheets abstain.)
    #
    # "Cannot split" means ONE stroke weight, not "the median landed on
    # the heavy side". The cut is the LOWER median of the widths, and when
    # every row still sits at or below it the cut drops to the next weight
    # down, so two distinct weights ALWAYS separate - a two-row sheet at
    # 0.2 / 0.9 pt puts the 0.9 on proposed rather than filing both as
    # existing because the upper median happened to be 0.9.
    # The reference is the WHOLE sheet's weights, as it always was - a lone
    # leftover row is judged against the sheet, not against itself - and a
    # sheet with one weight on every row is the one that cannot split.
    ws = sorted((r.get("width") or 0) for r in rows)
    distinct = sorted(set(ws))
    separates = len(distinct) >= 2
    wmed = ws[(len(ws) - 1) // 2] if ws else 0
    if separates and wmed >= distinct[-1]:
        wmed = distinct[-2]
    for r in still:
        w = r.get("width") or 0
        if separates:
            r["surface"] = "existing" if w <= wmed else "proposed"
            r["split_by"] = "width"
            counts["width"] += 1
        else:
            r["surface"] = None
            r["split_by"] = "none"
            counts["none"] = counts.get("none", 0) + 1
    return counts


# --- interval infill: the unlabelled contours between two labelled ones ---
#
# A grading sheet labels a MINORITY of its contours. The draughtsman writes
# 556 and 561 and draws the four lines between them unlabelled, because a
# reader counts them: the contour interval is stated on the sheet, so the
# four in between can only be 557, 558, 559, 560. The engine has been
# throwing that information away and interpolating blindly across the gap.
#
# Measured cost of throwing it away, Oncor C-04 (the offline analyst did
# this fan by hand; his row set is the answer key, report.json how="interp"):
# rebuilding with LABELLED ROWS ONLY against the full human set, same cells,
# gives cut 1,781.0 -> 2,001.9 CY (+12.4%) and fill 6,871.5 -> 6,932.2
# (+0.9%); the infilled rows also EXTEND the defined surface from 79,248 to
# 83,796 SF, worth ~1,047 CY more fill, so the full effect is +16.1% fill.
#
# THE REFUSAL RULE IS THE LOAD-BEARING PART, and it is the analyst's own
# (report.json's `dropped` list shows him applying exactly it): assign the
# monotone fan ONLY when the number of unlabelled siblings crossed between
# two labelled contours equals dz/interval - 1. If a line is missing, or an
# extra line got in, or the transect clipped a curve twice, the count does
# not match and NOTHING is assigned. Ambiguity is worse than absence: an
# absent contour leaves the TIN to interpolate, a wrong one puts a confident
# wrong elevation into a bid.
#
# The interval is READ, never invented - from the sheet's own CONTOUR
# INTERVAL note if the caller has one, else the modal delta between adjacent
# distinct labelled elevations. Where that modal delta is design-label noise
# rather than a contour interval, the count test simply never matches and
# the whole pass abstains, which is the correct outcome. Measured: on Stice
# p0 the modal adjacent-delta is 0.1 ft (219 of 331 deltas) because the
# existing side carries decimal design labels, and this pass assigns zero
# rows there rather than inventing a 0.1 ft fan.

# How far a transect may reach looking for the flanking labelled contour,
# as a multiple of the sheet's own median inter-contour spacing. Only the
# FIRST labelled crossing on each side is ever used, so a generous reach
# costs search, not accuracy.
_INFILL_REACH_SPACINGS = 30.0
# Stations per labelled chain: the transect is cast perpendicular at each.
# More stations = more independent votes per candidate; they must AGREE.
_INFILL_STATIONS = 8
# A fan wider than this is not something a crossing count can honestly
# resolve on a real sheet - one missed line and every elevation in it is
# wrong. Refuse rather than guess.
_INFILL_MAX_FAN = 12
# dz/interval must land on an integer this closely to count as a whole
# number of contour steps.
_INFILL_STEP_TOL = 0.02
# Transect budget, so a 1,500-contour sheet cannot stall the request.
_INFILL_MAX_TRANSECTS = 6000


def contour_interval(zs, note_ft=None) -> dict:
    """The sheet's contour interval - read off the labels, never invented.

    note_ft, when the caller has read the sheet's own CONTOUR INTERVAL
    note, wins outright: the sheet stating its interval is better evidence
    than any statistic over the labels we happened to resolve.

    Otherwise: the modal delta between ADJACENT DISTINCT labelled
    elevations. `share` is what fraction of those deltas the modal value
    accounts for - reported, not thresholded, so the caller and the
    operator can see how much consensus is behind it. Measured: Oncor C-04
    existing 1.0 ft on 33 of 34 deltas (0.97); Stice p0 existing 0.1 ft on
    219 of 331 (0.66), which is decimal design-label noise and is exactly
    why the count rule, not this share, decides whether anything ships.
    """
    if note_ft:
        try:
            v = float(note_ft)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            return {"interval_ft": v, "source": "sheet's contour-interval "
                    "note", "modal_count": 0, "n_deltas": 0, "share": None}
    u = np.unique(np.round(np.asarray(list(zs), dtype=float), 3))
    if len(u) < 3:
        return {"interval_ft": None, "source": "too few distinct labelled "
                "elevations to read an interval", "modal_count": 0,
                "n_deltas": 0, "share": None}
    d = np.round(np.diff(u), 2)
    d = d[d > 1e-6]
    if not len(d):
        return {"interval_ft": None, "source": "labelled elevations do not "
                "step", "modal_count": 0, "n_deltas": 0, "share": None}
    vals, cnt = np.unique(d, return_counts=True)
    i = int(cnt.argmax())
    return {"interval_ft": float(vals[i]),
            "source": "modal step between adjacent labelled elevations",
            "modal_count": int(cnt[i]), "n_deltas": int(len(d)),
            "share": round(float(cnt[i]) / len(d), 3)}


def _seg_crossings(a, b, pts):
    """Parameters t in (0,1) where segment a->b crosses polyline pts.

    Plain segment-segment intersection, vectorised over the polyline. A
    grazing/parallel touch (denominator ~0) is dropped rather than counted:
    a transect that runs along a contour has not crossed it.
    """
    d = b - a
    p0 = pts[:-1]
    p1 = pts[1:]
    e = p1 - p0
    den = d[0] * e[:, 1] - d[1] * e[:, 0]
    live = np.abs(den) > 1e-12
    if not live.any():
        return np.empty(0)
    w = p0[live] - a
    den = den[live]
    t = (w[:, 0] * e[live][:, 1] - w[:, 1] * e[live][:, 0]) / den
    u = (w[:, 0] * d[1] - w[:, 1] * d[0]) / den
    m = (t > 1e-9) & (t < 1 - 1e-9) & (u >= 0.0) & (u <= 1.0)
    return t[m]


def _median_contour_spacing(items) -> float:
    """Median distance from a contour sample to the nearest sample of a
    DIFFERENT chain - how far apart this sheet draws its contours, in pt."""
    xs, ids = [], []
    for cid, pts, _z in items:
        s, _ = _sample_polyline(np.asarray(pts, float), step=12.0,
                                max_n=4000)
        if len(s):
            xs.append(s)
            ids.append(np.full(len(s), cid))
    if not xs:
        return 0.0
    P = np.vstack(xs)
    I = np.concatenate(ids)
    if len(P) < 4:
        return 0.0
    k = min(24, len(P))
    dist, idx = cKDTree(P).query(P, k=k, workers=-1)
    other = I[idx] != I[:, None]
    has = other.any(axis=1)
    if not has.any():
        return 0.0
    first = np.argmax(other, axis=1)
    d = dist[np.arange(len(P)), first][has]
    return float(np.median(d))


def minor_k(adjacent_runs) -> int:
    """The modal sibling count of the adjacent-label refusals (0 = none)."""
    if not adjacent_runs:
        return 0
    from collections import Counter
    k, n = Counter(int(r) for r in adjacent_runs).most_common(1)[0]
    return int(k)


def _minor_interval(step: float, adjacent_runs: list):
    """A finer contour interval implied by the fans between ADJACENT labels.
    step/(k+1) when at least 3 such fans exist and >= 60% of them crossed the
    same k >= 1 unlabelled siblings; else None. Deliberately a consensus, not
    a single fan: one stray line between two labels is not an interval."""
    if not adjacent_runs or len(adjacent_runs) < 3:
        return None
    from collections import Counter
    k, n = Counter(int(r) for r in adjacent_runs).most_common(1)[0]
    if k < 1 or n / len(adjacent_runs) < 0.6:
        return None
    return float(step) / (k + 1)


def infill_intervals(assigned: list, candidates: list,
                     interval_ft=None, note_ft=None,
                     anchor_how: dict | None = None) -> dict:
    """Suggest elevations for unlabelled contours between two anchors.

    assigned:   [{chain_id, pts Nx2 page pt, z, family}] on ONE surface
    candidates: [{chain_id, pts, family}] unlabelled chains, same surface
    anchor_how: {chain_id: the assigned row's own `how`}. WITHOUT IT THE
                SENTENCE MUST NOT SAY "LABELLED". An assigned row's
                elevation may itself be `inherited` - welded across from
                another fragment, with no label printed on that line at
                all - and on Oncor C-04 that is true of BOTH ends of 13 of
                the 35 infilled rows. Calling such an anchor "labelled" is
                a false statement about the sheet, printed on the tooltip
                an estimator checks the suggestion against. Anything this
                map does not vouch for is worded as an anchor, not a label
                (abstain, never upgrade).

    `family` is the chain's style family (style_id): A TRANSECT COUNTS ONLY
    SAME-FAMILY SIBLINGS. A contour's unlabelled neighbours are drawn in the
    same linetype it is - that is what makes them countable - and without
    the restriction every property line, swale and lot line the transect
    crosses is counted as a sibling and the fan never matches. Measured on
    Oncor C-04 with the restriction OFF, feeding all 589 chains as
    candidates: the proposed surface (whose contours are a single family,
    style 41) assigned 0 of the analyst's 14 rows. With it on, see
    out/verify/2026-08-31/cutfill-accuracy/a3_infill.json.

    Returns {"rows": [...], "interval": {...}, "refused": {...},
             "n_transects": int}. Each row is
    {chain_id, z, how: "interval-infill", inferred: True, votes, src,
     anchors_printed}
    where `src` is the sentence shown to the operator - it names the two
    contours the fan was counted between and says, of each, whether the
    sheet prints a label on it, so the suggestion can be checked against
    the sheet without leaving the panel. `anchors_printed` is that same
    fact as a number (0, 1 or 2) so a caller can assert the CLAIM rather
    than match the sentence.

    Nothing here decides anything: these are SUGGESTIONS the operator
    confirms, and every one is flagged inferred so an inferred elevation is
    never mistaken for a printed one.
    """
    out = {"rows": [], "refused": {"count_mismatch": 0, "no_flanking_label": 0,
                                   "fan_too_wide": 0, "not_whole_steps": 0,
                                   "repeat_crossing": 0, "vote_conflict": 0,
                                   "adjacent_labels_nothing_between": 0},
           "n_transects": 0}

    def _prep(rows, want_z):
        out2 = []
        for d in rows or []:
            p = np.asarray(d.get("pts"), dtype=float)
            if p.ndim != 2 or len(p) < 2:
                continue
            z = d.get("z")
            if want_z:
                if z is None:
                    continue
                z = float(z)
            else:
                z = None
            out2.append((int(d["chain_id"]), p, z, d.get("family")))
        return out2

    assigned = _prep(assigned, True)
    candidates = _prep(candidates, False)
    iv = contour_interval([z for _, _, z, _ in assigned], note_ft=note_ft)
    out["interval"] = iv
    if interval_ft:
        iv = {"interval_ft": float(interval_ft), "source": "caller",
              "modal_count": 0, "n_deltas": 0, "share": None}
        out["interval"] = iv
    step = iv.get("interval_ft")
    if not step or step <= 0 or not assigned or not candidates:
        return out

    # A contour's countable siblings are the families its own LABELLED
    # members are drawn in. Anything else on the sheet is invisible to the
    # transect - not skipped over, simply not a contour of this surface.
    fams = {f for _, _, _, f in assigned if f is not None}
    allc = list(assigned)
    allc += [(cid, p, None, f) for cid, p, _z, f in candidates
             if (not fams) or f in fams]
    out["n_candidates_in_family"] = len(allc) - len(assigned)
    out["families"] = sorted(str(f) for f in fams)
    if len(allc) == len(assigned):
        return out
    # geometry index: bbox per chain, so a transect only tests the chains it
    # could possibly reach
    box = np.asarray([[p[:, 0].min(), p[:, 1].min(),
                       p[:, 0].max(), p[:, 1].max()] for _, p, _, _ in allc])
    zof = {cid: z for cid, _, z, _ in allc}
    reach = _INFILL_REACH_SPACINGS * (_median_contour_spacing(
        [(c, p, z) for c, p, z, _ in assigned]) or 0.0)
    if reach <= 0:
        return out

    def _fan(step, adjacent_runs):
        """One counting pass at `step`; returns (votes, conflicted).
        `adjacent_runs` collects, for every transect refused as 'adjacent
        labels, nothing between', how many unlabelled siblings it crossed."""
        votes: dict = {}
        conflicted: set = set()
        for oid, opts, oz, _of in assigned:
            if out["n_transects"] >= _INFILL_MAX_TRANSECTS:
                break
            n = len(opts)
            idxs = np.unique(np.linspace(0, n - 1, _INFILL_STATIONS).astype(int))
            for i in idxs:
                j0 = max(0, i - 2)
                j1 = min(n - 1, i + 2)
                tan = opts[j1] - opts[j0]
                L = float(np.hypot(*tan))
                if L < 1e-9:
                    continue
                nrm = np.array([-tan[1] / L, tan[0] / L])
                origin = opts[i]
                out["n_transects"] += 1
                for sign in (1.0, -1.0):
                    a = origin
                    b = origin + sign * reach * nrm
                    lo = np.minimum(a, b)
                    hi = np.maximum(a, b)
                    near = ((box[:, 0] <= hi[0]) & (box[:, 2] >= lo[0])
                            & (box[:, 1] <= hi[1]) & (box[:, 3] >= lo[1]))
                    hits = []
                    for k in np.nonzero(near)[0]:
                        cid = allc[k][0]
                        if cid == oid:
                            continue
                        for t in _seg_crossings(a, b, allc[k][1]):
                            hits.append((float(t), cid))
                    if not hits:
                        continue
                    hits.sort()
                    # walk outward to the first LABELLED crossing
                    run = []
                    zend = None
                    cend = None
                    for t, cid in hits:
                        if zof.get(cid) is None:
                            run.append(cid)
                        else:
                            zend = zof[cid]
                            cend = cid
                            break
                    if zend is None:
                        out["refused"]["no_flanking_label"] += 1
                        continue
                    if len(run) != len(set(run)):
                        # the transect clipped one contour twice - it is running
                        # along a curve, not across the fan
                        out["refused"]["repeat_crossing"] += 1
                        continue
                    dz = zend - oz
                    steps = dz / step
                    rs = round(steps)
                    if abs(steps - rs) > _INFILL_STEP_TOL:
                        out["refused"]["not_whole_steps"] += 1
                        continue
                    if abs(rs) < 2:
                        # the two labels are one interval apart: there is
                        # nothing between them. Not a refusal - a walk with
                        # nothing to say. Counted separately so the refusal
                        # buckets stay readable as failures - and when the walk
                        # DID cross unlabelled siblings, the sheet's interval is
                        # finer than its labels: remembered for the minor pass.
                        out["refused"]["adjacent_labels_nothing_between"] += 1
                        if abs(rs) == 1 and run:
                            adjacent_runs.append(len(run))
                        continue
                    if abs(rs) - 1 > _INFILL_MAX_FAN:
                        out["refused"]["fan_too_wide"] += 1
                        continue
                    # THE RULE: crossed unlabelled siblings must be exactly
                    # dz/interval - 1, or nothing is assigned
                    if len(run) != abs(rs) - 1:
                        out["refused"]["count_mismatch"] += 1
                        continue
                    sgn = 1.0 if dz > 0 else -1.0
                    for m, cid in enumerate(run, start=1):
                        z = round(oz + sgn * m * step, 4)
                        prev = votes.get(cid)
                        if prev is None:
                            votes[cid] = {"z": z, "n": 1,
                                          "src": (oid, oz, cend, zend)}
                        elif abs(prev["z"] - z) > 1e-6:
                            # two transects read this line differently: refuse it
                            conflicted.add(cid)
                        else:
                            prev["n"] += 1
        return votes, conflicted

    adj_runs: list = []
    votes, conflicted = _fan(step, adj_runs)
    # THE MINOR INTERVAL (Barn C3.0, 2026-09-16): the labelled contours are
    # the 5 ft index lines, the sheet draws 1 ft intermediates between them,
    # and every fan between two adjacent labels was refused as "adjacent
    # labels, nothing between" - 133 of them - while 161 unlabelled contours
    # waited. When those refusals consistently crossed the same k siblings,
    # the sheet's interval is step / (k + 1): count the fans again at it and
    # keep the index-step votes that already stood.
    minor = _minor_interval(step, adj_runs)
    if minor:
        out["interval"] = dict(out["interval"],
                               index_interval_ft=float(step),
                               interval_ft=float(minor),
                               source=(f"{_fmt_z(step)} ft between the labelled contours, "
                                       f"{minor_k(adj_runs)} unlabelled contours drawn between "
                                       f"adjacent labels: a {_fmt_z(minor)} ft interval"),
                               minor_from={"k": minor_k(adj_runs), "transects": len(adj_runs)})
        out["refused_at_index_interval"] = dict(out["refused"])
        for k in out["refused"]:
            out["refused"][k] = 0
        out["n_transects"] = 0
        v2, c2 = _fan(minor, [])
        for cid, v in v2.items():
            prev = votes.get(cid)
            if prev is None:
                votes[cid] = v
            elif abs(prev["z"] - v["z"]) > 1e-6:
                conflicted.add(cid)
            else:
                prev["n"] += v["n"]
        conflicted |= c2
        step = minor
    for cid in conflicted:
        votes.pop(cid, None)
    out["refused"]["vote_conflict"] = len(conflicted)
    howof = anchor_how or {}

    def _anchor(cid, z):
        """Name one end of the fan, and say what backs it."""
        if is_printed_how(howof.get(int(cid))):
            return f"labelled {_fmt_z(z)} (line {cid})", True
        return f"{_fmt_z(z)} (line {cid}, itself unlabelled)", False

    for cid, v in sorted(votes.items()):
        oid, oz, cend, zend = v["src"]
        nz = _fmt_z(v["z"])
        a_txt, a_ok = _anchor(oid, oz)
        b_txt, b_ok = _anchor(cend, zend)
        n_ok = int(a_ok) + int(b_ok)
        tail = ("not a printed label, confirm against the sheet" if n_ok == 2
                else "one end carries no printed label either, confirm "
                     "against the sheet" if n_ok == 1
                else "NEITHER end carries a printed label, confirm against "
                     "the sheet")
        out["rows"].append({
            "chain_id": int(cid), "z": v["z"], "how": "interval-infill",
            "inferred": True, "votes": v["n"], "anchors_printed": n_ok,
            "src": (f"counted between {a_txt} and {b_txt} at the sheet's "
                    f"{_fmt_z(step)} ft contour interval - {tail}"),
            "label": f"{nz} (inferred)"})
    return out


def _fmt_z(v: float) -> str:
    f = float(v)
    return str(int(round(f))) if abs(f - round(f)) < 1e-6 else f"{f:g}"


# --- spot grades: the elevations a grading sheet actually prints ----------

# The grade type printed before the value. FG finish grade, FIN finished
# floor, TS/BS top/bottom of step or stair, TW/BW top/bottom of wall,
# EG existing grade, TC top of curb, RIM structure rim, INV invert,
# ME match existing, HP/LP high/low point. Verified against the Beaumont
# SWR sheets 2026-08-21; the list is the sheet's own vocabulary, not a
# guess (each appears with a colon and a value to its right).
# Layers that are not plan ground. A profile or section box is a DIFFERENT
# VIEW drawn on the same sheet: its axes are elevation-against-station, not
# plan position, so a "contour" found inside one is grid, not ground - and
# its station lines get handed elevations off nearby pipe-invert numbers.
# Title-block layers carry sheet metadata; the sheet DATE reads as a clean
# four-digit elevation. Measured on Beaumont Guide civil engine page index 3:
# 18 of 51 suggestions sat on C-ROAD-PROF-VIEW and one (2026, the date) on
# G-ANNO-TTLB-THIN, and 18 of the 23 PROPOSED contours were profile geometry.
# Rejecting on the interval alone does not work - 7 of those 18 carry a clean
# whole-foot value.
# The tokens are read off the layer names in Beaumont's Guide and Barn civil
# sets. That they carry the same meaning under other CAD layer standards is
# an INFERENCE, not something these sheets establish.
# UTILITY LINES ARE NOT CONTOURS (Barn C3.0, 2026-09-16): the 10 in water
# main on layer SE-P-UT-WATER-10'' took a 7720 label by radius and became a
# quarter of the proposed surface's length. Whole tokens only, so DRAINAGE /
# WATERSHED in a topo layer name do not match.
_UTILITY_LAYER_TOKENS = (
    r"WATER|WTR|SEWER|SEW|SAN|SANITARY|SS|STORM|STM|SD|GAS|ELEC|ELECTRIC"
    r"|UGE|OHE|OHP|UT|UTIL|UTILITY|PIPE|CULV|CULVERT|FORCE|FM|IRR|IRRIG"
    r"|CATV|TELE|TEL|COMM|FIBER|FO|DRAIN")
_UTILITY_LAYER = re.compile(
    r"(?i)(?:^|[-_|\s])(" + _UTILITY_LAYER_TOKENS + r")(?:[-_|\s]|$)")
_NON_PLAN_LAYER = re.compile(
    r"(?i)(?:^|[-_|\s])(PROF|PROFILE|SECT|SECTION|TTLB|TITLEBLOCK"
    r"|" + _UTILITY_LAYER_TOKENS + r")(?:[-_|\s]|$)")
# the same question asked of a style's NAME (legend row or typed label):
# a family the sheet calls a water line, a sewer or a pipe is not a contour
# whatever its layer says.
UTILITY_NAME_RE = re.compile(
    r"(?i)\b(WATER(?:\s?(?:LINE|MAIN|SERVICE))?|SEWER|SANITARY|STORM|GAS|ELECTRIC"
    r"|ELEC\.?|UNDERGROUND|CONDUIT|TELEPHONE|CABLE|FIBER|PIPE|CULVERT"
    r"|FORCE\s?MAIN|IRRIGATION|DRAIN(?:\s?(?:PIPE|LINE))?)\b")


def is_utility_name(name: str) -> bool:
    """True when a style/legend name says utility line (see UTILITY_NAME_RE);
    a name that also says CONTOUR is a contour (the legend wins)."""
    s = str(name or "")
    if not s or "CONTOUR" in s.upper():
        return False
    return bool(UTILITY_NAME_RE.search(s)) or bool(_UTILITY_LAYER.search(s))


def _is_plan_chain(c) -> bool:
    """False for chains that cannot be a contour.

    Two tests, because the layer one is not always available. On Beaumont's
    Guide civil grading sheet ALL plan linework carries an empty CAD layer,
    so a layer guard is vacuous there - but the chainer has already decided
    that a traced numeral is `annotation`, and a contour is linework by
    definition. Measured across Guide civil p2/p3 and Barn civil p3/p6:
    exactly one elevation suggestion sits on an annotation-class chain, and
    it is the printed number "7919" traced off layer C-TOPO-TEXT and handed
    to the TIN as a contour at 7919 ft. Zero other suggestions are touched.
    """
    if getattr(c, "klass", "") == "annotation":
        return False
    return not _NON_PLAN_LAYER.search(getattr(c, "layer", "") or "")


# FL flow line and FFE finished floor elevation were added 2026-08-22: both
# appear in the Beaumont Guide civil legend, and 11 printed labels were being
# skipped for want of them. Same standard of evidence as the rest of the list
# - the sheet's own vocabulary, each printed with a value beside it.
# PE = pad elevation, the finish grade of a building pad. Measured absent
# on 2026-08-30: Stice's Coral Mountain preliminary grading sheet prints
# ~204 "PE 486.0" pad callouts and every one was invisible to this reader
# (out/verify/2026-08-30/stice-recipe/RECIPE.md). A pad elevation is
# PROPOSED ground, like FG.
# EW = end of wall, added 2026-08-30: Oncor Cloudcrest C-04's own legend
# prints "EW 100.0  PROPOSED END OF WALL ELEVATION" and the sheet carries
# 4 printed EW callouts (568.6/569.8/558.8/557.6) that were invisible for
# want of the tag. Sheet vocabulary, same standard as the rest.
# PE (pad elevation) and WSE (water surface) are LINE-READER-ONLY tags: the
# word-pair pass below was measured wrong 9/9 times it fired for them
# corpus-wide ("...HDPE..." material codes, "2% PE in 50 Yrs" probability
# notes, rotated lot labels pairing with a neighbour's decimal) while the
# whole-line readers took all 505 real labels (sweep: worktree
# suspicious-shannon-55137d, out/verify/2026-08-30/pe-spot-tag/).
_SPOT_TAG = re.compile(
    r"^(FG|FIN|FFE|TS|BS|TW|BW|EG|FF|FL|TC|RIM|INV|ME|HP|LP|EW)\s*:?$",
    re.I)
# EXISTING-ground tags. Everything else describes the built surface.
_SPOT_EXISTING = {"EG", "ME"}
_SPOT_VALUE = re.compile(r"^\d{1,4}(\.\d{1,2})?$")
_FULL_ELEV = re.compile(r"^\d{3,4}$")

# One whole PDF text line that IS a spot grade: a tag, an optional colon or
# equals, a value, and an optional foot mark. Anchored at both ends so a line
# carrying anything else cannot match - the point of reading lines is that a
# neighbour's value is on a DIFFERENT line and can no longer be captured.
# The glued form `FIN:05.25'` is the one the sheet's own legend prints as its
# example, and the word-pair reader could never see it.
# PE is deliberately absent here (unlike _SPOT_TAG): Stice's 191 PE pads all
# read through the word pass today, and adding PE to the line pass would move
# every one of those rows' anchor coordinates for no change in value.
_SPOT_LINE = re.compile(
    r"^\s*(FG|FIN|FFE|TS|BS|TW|BW|EG|FF|FL|TC|RIM|INV|ME|HP|LP|EW|PE|WSE)"
    r"\s*[:=]?\s*(\d{1,4}(?:\.\d{1,2})?)\s*'?\s*$", re.I)
# The same whole-line label with the VALUE PRINTED FIRST: "583.5 ME".
# Oncor Cloudcrest C-04 prints every plan callout this way (21 of them)
# while its legend prints the samples tag-first ("ME 100.0") - the reader
# parsed only the tag-first order and returned nothing but the legend's own
# illustration. Both orders are the drafter's choice, not a convention.
_SPOT_LINE_VT = re.compile(
    r"^\s*(\d{1,4}(?:\.\d{1,2})?)\s*'?\s+"
    r"(FG|FIN|FFE|TS|BS|TW|BW|EG|FF|FL|TC|RIM|INV|ME|HP|LP|EW|PE|WSE)\s*[:=]?\s*$",
    re.I)
# These sheets DECLARE their datum in the spot-elevation legend:
#   "NOTE: ALL SPOT ELEVATIONS ARE TRUNCATED CIVIL ELEVATIONS
#    (IE. EXAMPLE: TOP OF PATIO @ 7705.25' = FIN:05.25')"
# That single line is worth more than any amount of consensus guessing, and
# it is the sheet's own statement rather than an inference. Barn civil p3
# says 7705.25 (datum 7700) while its labelled contours pushed consensus to
# 7900 - so every one of its 272 spot grades came out exactly 200.00 ft
# high, resolved and confident. Guide civil p2 says 7905.25 and consensus
# happened to agree, which is why the defect hid.
_SPOT_DATUM_LINE = re.compile(
    r"(?i)(?:EXAMPLE|\bIE\b|\bI\.E\.)[^0-9]{0,30}(\d{3,5})\.(\d{1,2})")


def read_spot_datum(lines: list) -> float | None:
    """The hundreds band the sheet's own legend declares, or None.

    Returns e.g. 7700.0 for a legend reading "@ 7705.25'". Only a 3-5 digit
    whole part counts, so a date or a dimension cannot be mistaken for one.
    """
    for ln in (lines or ()):
        m = _SPOT_DATUM_LINE.search(" ".join(str(ln[4]).split()))
        if not m:
            continue
        whole = int(m.group(1))
        if 100 <= whole <= 99999:
            return float(whole // 100 * 100)
    return None


# Two readings of the same tag closer than this are the same printed label.
# The two passes place one label 2.5-3.4 pt apart on Guide civil p2, and the
# nearest genuinely distinct same-tag labels there are far further off, so
# 10 pt separates them with room on both sides.
_SPOT_SAME_R2 = 10.0 ** 2

# --- legend-box exclusion: the legend's sample rows are not ground --------
#
# legend_detect hands back one sample box PER LINE-STYLE ROW, but a legend's
# spot-elevation rows carry no line swatch, so they sit in the vertical GAP
# between detected boxes. Measured on Oncor C-04: line-sample boxes end at
# y=114.9 and resume at y=212.7, and the five spot-sample rows ("100.0",
# "ME 100.0" ... "EW 100.0", 19.2 pt pitch) live in that 97.8 pt gap.
# Bridging vertically-stacked boxes into one block covers them; 120 pt holds
# the measured 97.8 with margin and is far below any gap a plan area between
# two separate legend blocks would open (the C-04 sheet is 1728 pt tall).
# Boxes only merge when their x-ranges overlap, so two legends in different
# corners of a sheet stay two blocks.
_LEGEND_ROW_BRIDGE = 120.0


def _legend_blocks(boxes) -> list:
    """Merge per-row legend sample boxes into whole-legend blocks."""
    bs = sorted((tuple(float(v) for v in b[:4]) for b in (boxes or ())),
                key=lambda b: (b[1], b[0]))
    blocks: list = []
    for b in bs:
        if blocks:
            x0, y0, x1, y1 = blocks[-1]
            if (b[1] - y1 <= _LEGEND_ROW_BRIDGE
                    and not (b[2] < x0 or b[0] > x1)):
                blocks[-1] = (min(x0, b[0]), y0, max(x1, b[2]),
                              max(y1, b[3]))
                continue
        blocks.append(b)
    return blocks


# A contour label inside the legend is the legend's SAMPLE, not terrain.
# Oncor C-08 prints "468" on both its PROPOSED CONTOUR and EXISTING CONTOUR
# swatches; the sheet's contours run 530-586 ft, and the two 468 rows
# entered the suggest list (one existing, one proposed) 60-120 ft below
# every real label. The per-row test the route used could not catch them:
# the legend reader had the PROPOSED CONTOUR row's box 11 pt short of the
# "468" and had missed the EXISTING CONTOUR row altogether (measured
# 2026-09-08). The merged BLOCK the spot-grade reader already uses covers
# both, because the legend's other nine rows fix where the legend is.
_LEGEND_LABEL_PAD_X = 6.0
_LEGEND_LABEL_PAD_Y = 8.0


def label_in_legend(xy, legend_boxes) -> bool:
    """True when a label position lies inside the legend: inside any
    merged legend block (per-row boxes bridged, _legend_blocks), with the
    same small pad the per-row test always had."""
    if not xy or xy[0] is None or xy[1] is None:
        return False
    x, y = float(xy[0]), float(xy[1])
    for b in _legend_blocks(legend_boxes):
        if (b[0] - _LEGEND_LABEL_PAD_X <= x <= b[2] + _LEGEND_LABEL_PAD_X
                and b[1] - _LEGEND_LABEL_PAD_Y <= y <= b[3] + _LEGEND_LABEL_PAD_Y):
            return True
    return False


# Fallback legend signature for when no legend boxes are available (the
# confidence endpoint reads only the text layer; older cached results may
# predate legend_samples). A legend prints ONE sample row per tag, every row
# the SAME sample value, in one tight column: C-04's read as ME/TW/BW/EW all
# exactly 100.0, x-centres within 0.6 pt, rows 19.2 pt apart. The rule below
# asks for >= 3 spots with an identical value in a <= 6 pt column at <= 30 pt
# consecutive spacing (10x / 1.5x the measured figures). False-positive risk,
# stated: three REAL callouts printing the same elevation, column-aligned
# within 6 pt and packed at under 30 pt pitch, would be swallowed - that is a
# wall/kerb run at constant grade with its labels stacked touching each
# other, which none of the four measured sheets (C-04, Barn C3.0/C3.1,
# Stice p0) exhibits. Walked with gap tests, not bucketed (CLAUDE.md 5).
_LEGEND_COL_DX = 6.0
_LEGEND_COL_DY = 30.0


def _legend_sample_rows(spots: list) -> set:
    """Indices of spots that look like a legend's own sample column."""
    by_val: dict = {}
    for i, g in enumerate(spots):
        by_val.setdefault(g["value"], []).append(i)
    drop: set = set()
    for ii in by_val.values():
        if len(ii) < 3:
            continue
        ii = sorted(ii, key=lambda i: spots[i]["y"])
        run = [ii[0]]

        def _flush(run):
            if len(run) >= 3:
                drop.update(run)

        for prev, cur in zip(ii, ii[1:]):
            xs = [spots[i]["x"] for i in run] + [spots[cur]["x"]]
            if (spots[cur]["y"] - spots[prev]["y"] <= _LEGEND_COL_DY
                    and max(xs) - min(xs) <= _LEGEND_COL_DX):
                run.append(cur)
            else:
                _flush(run)
                run = [cur]
        _flush(run)
    return drop


def read_spot_grades(words: list, max_dx: float = 26.0,
                     row_tol: float = 6.0, lines: list | None = None,
                     datum_info: dict | None = None,
                     legend_boxes: list | None = None) -> list:
    """Spot elevations printed as `FG: 15.96` / `TW: 7918.60` / `583.5 ME`.

    Civil sheets abbreviate: the value carries only the last two digits and
    the hundreds come from the nearest full contour label (`7715` written in
    a break in its own contour). Beaumont's own sheets do this everywhere -
    125 spot grades on C3.1 alone - and none of them match the 3-4-digit
    pattern `suggest_elevations` looks for, so the whole population was
    invisible.

    Resolving the hundreds needs care, because a sheet is full of other
    4-digit numbers - the DATE (2026) and the legend's own sample text
    (7900) both look like contour labels. Rule, measured on the Beaumont
    sheets:
      1. value already >= 1000 -> a full elevation, use as printed;
      2. otherwise find the page DATUM by consensus. A real contour label's
         own last two digits must fall inside the spread of the spot values
         (a labelled contour sits at an elevation the spot grades cover),
         and at least TWO surviving labels must agree on the same hundreds
         band. That kills the date (one lone 20xx) and the legend 7900
         (its last two digits, 00, sit outside the 12-31 spread). The
         hundreds then come from the nearest label in that band, choosing
         the multiple of 100 that lands closest to it - so 15.96 near 7715
         gives 7715.96, and 98.20 near 7700 gives 7698.20.
      3. no credible datum -> z is None and resolved is False. The caller
         asks the human for the datum; we never invent one. (Barn C3.1
         prints 125 spot grades and NO usable contour label - one typed
         number unlocks the whole sheet.)

    words: (x0, y0, x1, y1, text) in the SAME space as chain geometry -
    pass takeoff_engine.extract.page_words(page), not page.get_text("words"),
    or rotated sheets silently mis-place every grade.

    legend_boxes: the detect result's `legend_samples` row boxes, when the
    caller has them. Spot labels inside the merged legend block are the
    legend's own sample rows (C-04 prints "ME 100.0"... as its illustration),
    not ground; they are dropped BEFORE the datum machinery so their sample
    values cannot vote, and the count is disclosed in
    datum_info["legend_excluded"]. Without boxes the column-cluster fallback
    (_legend_sample_rows) still runs.

    Returns [{tag, value, z, base, x, y, resolved}]; an unresolved spot also
    carries why="no_datum" (z is None ONLY there, and the count is disclosed
    in datum_info["z_missing"] - 0.0 is a real elevation, never a missing
    one).
    """
    ws = [w for w in words if str(w[4]).strip()]
    raw_anchors = [((w[0] + w[2]) / 2, (w[1] + w[3]) / 2, float(w[4]))
                   for w in ws if _FULL_ELEV.fullmatch(str(w[4]).strip())]
    out = []
    # Where pass A already read a label, keyed by tag. A grid bucket is the
    # wrong tool here: the two passes place the same label a few points apart
    # (a line's bbox centre against a tag-to-value midpoint), so any bucket
    # edge between them lets the label through twice. Measured on Guide civil
    # p2: 12 labels double-counted at 2.5-3.4 pt apart. A radius test is what
    # the question actually is.
    line_at: dict = {}
    # Line bboxes pass A consumed. A matched line IS one whole label (both
    # regexes are anchored), so a tag word inside one is already read and
    # must not source a second pairing in pass B - on C-04 the tag of
    # "585.3 ME" otherwise re-grabs the NEXT callout's 585.7 printed 18.7 pt
    # to its right, a duplicate 36 pt from where that value truly sits (the
    # 10 pt same-label radius cannot catch it because it is not the same
    # position; this is the cross-capture defect, one level up).
    consumed: list = []

    # Pass A - whole PDF text lines. The PDF's own line grouping is
    # rotation-independent, so this reads the labels written up the side of a
    # sheet that a horizontal row window cannot see, and it cannot pair a tag
    # to a neighbour's value because the two are on different lines.
    for ln in (lines or ()):
        t = str(ln[4]).strip()
        m = _SPOT_LINE.match(t)
        if m:
            tag, val = m.group(1).upper(), float(m.group(2))
        else:
            # value-first print order ("583.5 ME") - same label, the
            # drafter's other choice. See _SPOT_LINE_VT.
            m = _SPOT_LINE_VT.match(t)
            if not m:
                continue
            tag, val = m.group(2).upper(), float(m.group(1))
        x = (ln[0] + ln[2]) / 2.0
        y = (ln[1] + ln[3]) / 2.0
        out.append({"tag": tag, "value": val,
                    "x": round(float(x), 1), "y": round(float(y), 1),
                    "side": "existing" if tag in _SPOT_EXISTING else "proposed"})
        line_at.setdefault(tag, []).append((x, y))
        consumed.append((float(ln[0]), float(ln[1]),
                         float(ln[2]), float(ln[3])))

    # Pass B - the original word-pair reader, for anything the line grouping
    # split (a tag and its value emitted as two lines). Anything already found
    # in pass A is skipped: the line reading is the more trustworthy of the
    # two, because it cannot cross into a neighbouring label.
    for w in ws:
        t = str(w[4]).strip()
        m = _SPOT_TAG.match(t)
        if not m:
            continue
        tag = m.group(1).upper()
        cx = (w[0] + w[2]) / 2
        cy = (w[1] + w[3]) / 2
        # this tag word sits inside a line pass A already read whole - done
        if any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in consumed):
            continue
        best = None
        for v in ws:
            if v is w:
                continue
            if abs(((v[1] + v[3]) / 2) - cy) > row_tol:
                continue
            dx = v[0] - w[2]
            if not (-2.0 < dx < max_dx):
                continue
            if not _SPOT_VALUE.fullmatch(str(v[4]).strip()):
                continue
            if best is None or dx < best[0]:
                best = (dx, v)
        # DELIBERATELY right-side only. The value-first order ("583.5 ME")
        # is handled by the whole-line pass above (_SPOT_LINE_VT), where the
        # PDF's own line grouping vouches that value and tag belong together.
        # A left-side WORD search was tried here 2026-08-30 and measured
        # harmful on both regression sheets: Stice p0's rotated pad clusters
        # put a neighbouring pad's value 0.6 pt left of a PE tag while the
        # tag's own value sat 5.8 pt right (10 of 191 pads flipped to the
        # neighbour's elevation, one junk PE 103.0), and Barn C3.1 grew 17
        # duplicate rows from second tags re-reading a neighbour's value.
        if best is None:
            continue
        val = float(best[1][4])
        x = (w[0] + best[1][2]) / 2
        y = cy
        # Pass A already read this label - keep its reading and drop this one,
        # whatever value this pass arrived at. Pass A is the more trustworthy
        # of the two precisely because a line cannot reach into a neighbour's
        # value, which is how the word reader put 10.36 against a TW: 14.00
        # tag and reported a wall top 3.64 ft out.
        if any((x - px) ** 2 + (y - py) ** 2 <= _SPOT_SAME_R2
               for px, py in line_at.get(tag, ())):
            continue
        out.append({"tag": tag, "value": val, "x": round(float(x), 1),
                    "y": round(float(y), 1),
                    "side": "existing" if tag in _SPOT_EXISTING else "proposed"})

    # --- legend-sample exclusion, BEFORE any datum voting ----------------
    # The legend's sample rows carry a made-up value (C-04: 100.0 on every
    # row). Left in, they do not just pollute the list - they vote in the
    # datum consensus below and can mutually "corroborate" each other.
    blocks = _legend_blocks(legend_boxes)
    # same 6/8 pt pad the contour-suggestion legend filter uses in server.py
    in_block = set()
    for i, g in enumerate(out):
        if any(b[0] - 6 <= g["x"] <= b[2] + 6 and b[1] - 8 <= g["y"] <= b[3] + 8
               for b in blocks):
            in_block.add(i)
    drop = in_block | _legend_sample_rows(out)
    n_legend = len(drop)
    if drop:
        out = [g for i, g in enumerate(out) if i not in drop]

    # --- page datum by consensus (see the docstring) ---
    partial = [g["value"] for g in out if g["value"] < 1000.0]
    anchors = []
    if partial:
        vlo, vhi = min(partial), max(partial)
        for ax, ay, av in raw_anchors:
            if vlo - 15.0 <= (av % 100.0) <= vhi + 15.0:
                anchors.append((ax, ay, av))
    bands = {}
    for a in anchors:
        bands.setdefault(int(a[2] // 100), []).append(a)
    band = max(bands.values(), key=len) if bands else []
    if len(band) < 2:
        band = []          # one lone number is not a datum

    # The sheet's own declared datum versus the consensus band - and NEITHER
    # always wins, because each has a measured failure on this one plan set:
    #
    #   Barn C3.1 (engine index 3): the note's 7705.25 is RIGHT and the band
    #   is a false [7900] built from the legend's own sample text, two labels
    #   strong. Declared-always-wins was added for this sheet; before it, all
    #   272 spot grades shipped exactly 200 ft high.
    #
    #   Barn C3.3 (engine index 5, printed sheet C3.3): the note reads
    #   "EXAMPLE: TOP OF PATIO @ 7605.25'" - a stale boilerplate value - while
    #   the sheet's own contours print 7720/7725/7730/7735, a four-label band.
    #   Declared-always-wins shipped all 171 spot grades exactly 100 ft LOW,
    #   marked resolved, under a green banner. Same defect on C3.5 and C3.6
    #   (1 grade each).
    #
    # The prefix cannot distinguish them: every datum line in the set, genuine
    # and stale alike, begins "EXAMPLE:" (it is how the legend note is
    # worded). What distinguishes them is BAND STRENGTH. Three or more full
    # elevations printed on the sheet body agreeing with each other outrank
    # one boilerplate legend sentence; a two-label band is exactly the shape a
    # legend echo produces and must not veto the note. Measured on all 15
    # pages of the Barn set: this rule corrects the 3 poisoned pages (173
    # grades fall to their genuine bands) and preserves C3.1's 272.
    #
    # Where the two sources disagree AT ALL, the disagreement is reported, not
    # swallowed - `datum_info["conflict"]` - so the UI can show both numbers
    # and offer the override. A wrong elevation that is visible is survivable;
    # the shipped defect was wrong-and-green with the override hidden.
    declared = read_spot_datum(lines)
    band_base = float(int(band[0][2] // 100) * 100) if band else None
    conflict = (declared is not None and band_base is not None
                and abs(band_base - declared) > 50.0)
    use_declared = declared is not None and not (conflict and len(band) >= 3)
    if datum_info is not None:
        datum_info.update(
            declared=declared, band_base=band_base, band_n=len(band),
            conflict=bool(conflict),
            chosen=("declared" if use_declared and declared is not None
                    else "band" if band else None))

    # A 3-digit spot on a 3-digit site is printed in full, not truncated.
    # Measured 2026-08-30 on Stice's Coral Mountain sheet (site runs
    # 425-521 ft): 191 PE pad elevations print as 466.0-style values and
    # the >= 1000 rule alone left every one unresolved. The truncation
    # convention keeps the LAST TWO digits (Barn: 7705.25' prints as
    # 05.25'), so a value >= 100 is only "full" when the sheet's own full
    # elevations corroborate it - at least two page anchors within 100 ft.
    # A lone 3-digit value on a 7,000-ft sheet still falls through to the
    # datum machinery / honest unresolved.
    full3 = sorted(av for _, _, av in raw_anchors)
    # The spot population can also corroborate ITSELF: Oncor C-04 prints all
    # 21 tagged callouts in full (551.2-586.4, one 35-ft family) and not one
    # bare 3-digit word anywhere on the page (its contour labels are exploded
    # vector glyphs, invisible to the text layer), so the anchor rule alone
    # left every correct printed value unresolved. Three or more tagged spots
    # agreeing within 100 ft is the same standard the anchor band already
    # uses (truncated values are always < 100, so a 3-digit family is a
    # printed-in-full family); a LONE 3-digit value still falls through to
    # the datum machinery / honest unresolved, and two values cannot vouch
    # for each other. Stated residual risk: three non-elevation tag-value
    # pairs landing inside one 100-ft window would self-corroborate - none of
    # the four measured sheets prints even one such pair.
    spot3 = sorted(g["value"] for g in out if 100.0 <= g["value"] < 1000.0)
    def _corroborated(v):
        import bisect
        i = bisect.bisect_left(full3, v - 100.0)
        j = bisect.bisect_right(full3, v + 100.0)
        if (j - i) >= 2:
            return True
        i = bisect.bisect_left(spot3, v - 100.0)
        j = bisect.bisect_right(spot3, v + 100.0)
        return (j - i) >= 3   # v itself is in spot3: itself + 2 others

    for g in out:
        val = g["value"]
        if val >= 1000.0:
            g.update(z=val, base=None, resolved=True, res="printed")
        elif val >= 100.0 and _corroborated(val):
            g.update(z=val, base=None, resolved=True, res="printed")
        elif use_declared and val < 100.0:
            g.update(z=declared + val, base=declared, resolved=True, res="note")
        elif band:
            ax, ay, av = min(band, key=lambda a: (a[0] - g["x"]) ** 2
                                                 + (a[1] - g["y"]) ** 2)
            k = round((av - val) / 100.0)
            g.update(z=val + 100.0 * k, base=av, resolved=True, res="inferred")
        else:
            # z is None ONLY here, and each such spot says so itself. This
            # is "genuinely absent": the value printed truncated and no
            # credible page datum exists. (0.0 is a real elevation; every
            # missing-z test downstream must be `is None`, CLAUDE.md 5.)
            g.update(z=None, base=None, resolved=False, why="no_datum")
    if datum_info is not None:
        datum_info["legend_excluded"] = n_legend
        datum_info["z_missing"] = sum(1 for g in out if g["z"] is None)
    return out


# --- COVERAGE: how much of this sheet's contour linework carries a height --
#
# THE HOLE THIS FILLS. Every fact needed to answer "how many of this sheet's
# contours have an elevation" was already on the wire - the suggestion rows
# carry `how` per row, the route reports n_chains per excluded family - and
# nothing added them up. Measured by grep on 512f474: `spot_z_missing`
# exists for SPOT grades and no field anywhere states a contour denominator.
# So a sheet where the reader elevated 150 of 368 contour lines rendered
# byte-identically to one where it elevated all of them, and the operator's
# only route to the difference was counting linework by eye.
#
# WHAT IS COUNTED, and why each exclusion is the route's own.
#
# The DENOMINATOR is this sheet's contour linework: chains that
#   - could be a contour at all (`_is_plan_chain`: not an annotation glyph,
#     not on a profile/section/title-block layer);
#   - the suggest route did not exclude (a family the legend named as
#     something other than a contour, and near-white mask ink);
#   - sit in a family the engine has a reason to call a contour: either the
#     sheet's own legend named that family a contour row, or the family
#     produced at least one elevation. A family with neither is NOT
#     silently added to the denominator (it would make every sheet look
#     hopeless) and NOT silently dropped either - it is reported as
#     `unclassed`, because CLAUDE.md s5's rule is that a cap must be said
#     out loud. Measured 2026-09-02: Barn civil p2 has 351 chains in
#     contour families and 1,000 more in families with no contour evidence
#     at all; reporting "351" with no mention of the 1,000 would be a
#     coverage claim over a third of the sheet's linework.
#
# AND NEITHER IS ANYTHING ELSE. Until 2026-09-03 that promise held for the
# LAST filter only: the three above it (`_is_plan_chain`, near-white ink,
# the route's exclusion list) dropped their chains with a bare `continue`
# and no record, and on the DEM outline sheet that hid 22 chains carrying
# 51% of the page. `set_aside` now names every one of them by the reason it
# was set aside, with count and footage, and `plan_chains`/`plan_len_pt`
# give the wider denominator those classes belong to; `page_chains` /
# `page_len_pt` are the total the parts must add up to. The rule this
# enforces is that a coverage number may UNDERSTATE and may never overstate
# - so where a class cannot be judged, it is named in the statement rather
# than left out of the arithmetic.
#
# The NUMERATOR is the chains that carry a suggestion row, split by the
# `how` that produced it, so "carries an elevation" can never be read as
# "carries a printed elevation". Measured on Oncor C-04 (engine page index
# 3 = printed sheet C-04, job e183a0d44d89): 368 contour chains, 150 with a
# row - 0 inline, 20 radius, 69 gap-ocr, 26 inherited, 35 interval-infill.
#
# LENGTH AS WELL AS COUNT, and length is the honest one. C-04's 150 elevated
# chains are 40.8% of the contour chains but 59.0% of the contour footage:
# the reader lands labels on the long through-going contours and misses the
# fragments. A count-only statement understates the coverage; a length-only
# one hides how many separate lines a reviewer has to look at. Both ship.
#
# PURE. No PDF, no numpy, no I/O: plain dicts in, plain dict out, so the
# arithmetic can be frozen against a recorded row set
# (tests/test_ew_coverage.py) rather than re-measured off a sheet.
COVERAGE_HOWS = ("inline", "radius", "gap-ocr", "inherited",
                 "interval-infill", "unknown")


class _CovChain:
    """Attribute view over a stored chain dict, so this function and the
    route agree on `_is_plan_chain` / `_chain_white` by CALLING them rather
    than by two authors writing the same test twice."""

    __slots__ = ("chain_id", "style_id", "length", "klass", "layer", "color")

    def __init__(self, d):
        self.chain_id = d.get("chain_id")
        self.style_id = d.get("style_id")
        self.length = d.get("length_pt") or 0.0
        self.klass = d.get("class") or ""
        self.layer = d.get("layer") or ""
        self.color = d.get("color")


def contour_coverage(chains, rows, verdicts=None, excluded=None) -> dict:
    """How much of this sheet's contour linework carries an elevation.

    chains:   [{chain_id, style_id, length_pt, class, layer, color}] - the
              stored detect result's chains, as dicts.
    rows:     the /earthwork/suggest suggestion rows, [{chain_id, how, z}].
    verdicts: family_verdicts() output, {style_id: {"role", "label"}}.
    excluded: chain ids the route took out of the seeding pool.

    Returns a dict of counts and paper-point lengths - never a percentage:
    the caller renders the share, so the numerator and the denominator are
    always both on screen (CLAUDE.md s2).
    """
    verdicts = verdicts or {}
    excl = {int(c) for c in (excluded or ())}
    chs = [_CovChain(c) for c in (chains or [])]
    by_id = {int(c.chain_id): c for c in chs if c.chain_id is not None}

    rows = [r for r in (rows or []) if r is not None]
    seeded, by_how = {}, {h: 0 for h in COVERAGE_HOWS}
    for r in rows:
        cid = r.get("chain_id")
        if cid is None:
            continue
        cid = int(cid)
        how = r.get("how") or "unknown"
        if how not in by_how:
            how = "unknown"
        seeded[cid] = how

    # The legend's own verdict, and the families that produced a row. A
    # noncontour verdict only DISQUALIFIES when the same legend read also
    # found a contour family somewhere - the route's rule, for the reason
    # its comment gives (Stice: a legend that named the contours "PROPOSED
    # LOT LINE" would otherwise delete 634 verified rows).
    legend_fam = {int(sid) for sid, v in verdicts.items()
                  if v.get("role") in ("existing", "proposed", "contour")}
    noncontour = {int(sid) for sid, v in verdicts.items()
                  if v.get("role") == "noncontour"}
    legend_saw_contours = bool(legend_fam)
    row_fam = {by_id[cid].style_id for cid in seeded if cid in by_id}
    fam = legend_fam | {int(s) for s in row_fam if s is not None}

    n = ln = cov_n = cov_len = 0
    un_n = un_len = 0
    counted = set()
    fam_len: dict = {}
    unelevated = []
    elevated = []
    # EVERY CHAIN LANDS IN EXACTLY ONE BUCKET, AND EVERY BUCKET IS REPORTED.
    #
    # Until 2026-09-03 the four filters above the `else: unclassed`
    # accumulator each did a bare `continue`, so only the chains that
    # survived ALL of them could be reported as unclassed - and the comment
    # said the opposite in as many words ("NOT silently dropped either").
    # Measured on the DEM pad_2ft_outline sheet: 22 chains carrying
    # 33,301.7 pt - 51% of everything the engine assembled on that page -
    # were in neither count and in no reported class. 16 of them are the
    # family the OCR'd legend called "GRADING NOTES", grey 0.4 pt, the
    # same ink as the family it called EXISTING CONTOUR. On screen that
    # became "57 contour lines, 21 carry an elevation (58% of that contour
    # footage)" when the elevated share of the sheet's whole plan linework
    # is 33.5%: a coverage number that OVERSTATES by 24 points on the one
    # sheet where the reader is weakest, which is the direction CLAUDE.md
    # s5 forbids.
    #
    # The bucket order below is the filter order above, unchanged, so the
    # DENOMINATOR is the same set of chains it always was - this adds
    # names and counts, it moves nothing into or out of `contour_chains`.
    #
    # WHAT COUNTS AS "PLAN LINEWORK", and why `not_plan` is not in it.
    # `plan_len_pt` is the honest wider denominator: the contour families,
    # the linework in no contour family, the families an exclusion removed,
    # and near-white mask ink - every stroke that could be a contour and
    # is not proven otherwise. It does NOT include `not_plan`, which is
    # what `_is_plan_chain` rejects: chains the chainer classed
    # `annotation` (a traced glyph) or that sit on a profile / title-block
    # layer. On the outline sheet those six chains are 15,926.1 pt and two
    # of them are the sheet BORDER at 7,776 pt each; folding a drawing
    # frame into a contour denominator would misstate in the other
    # direction and make the disclosure unreadable. So they are counted,
    # named and kept OUT of the share - the caller prints their count, and
    # a reader who disagrees can add them back from the numbers on screen.
    sa_n: dict = {"legend_noncontour": 0, "excluded_other": 0, "white": 0,
                  "unclassed": 0, "not_plan": 0, "no_chain_id": 0}
    sa_len: dict = {k: 0.0 for k in sa_n}
    sa_labels: dict = {}

    def _aside(kind, c):
        sa_n[kind] += 1
        sa_len[kind] += c.length

    for c in chs:
        cid = int(c.chain_id) if c.chain_id is not None else None
        if cid is None:
            _aside("no_chain_id", c)
            continue
        if not _is_plan_chain(c):
            _aside("not_plan", c)
            continue
        if _chain_white(c):
            _aside("white", c)
            continue
        if cid in excl or (legend_saw_contours and c.style_id in noncontour):
            v = verdicts.get(c.style_id) or verdicts.get(str(c.style_id)) or {}
            if v.get("role") == "noncontour":
                _aside("legend_noncontour", c)
                lab = v.get("label")
                if lab:
                    sa_labels.setdefault("legend_noncontour", set()).add(lab)
            else:
                _aside("excluded_other", c)
            continue
        if c.style_id is not None and int(c.style_id) in fam:
            n += 1
            ln += c.length
            fam_len[int(c.style_id)] = fam_len.get(int(c.style_id), 0.0) \
                + c.length
            if cid in seeded:
                cov_n += 1
                cov_len += c.length
                counted.add(cid)
                elevated.append({"chain_id": cid,
                                 "style_id": int(c.style_id),
                                 "length_pt": round(c.length, 1)})
            else:
                unelevated.append({"chain_id": cid,
                                   "style_id": int(c.style_id),
                                   "length_pt": round(c.length, 1)})
        else:
            un_n += 1
            un_len += c.length
            _aside("unclassed", c)
    # by_how describes the NUMERATOR - the contour chains this coverage
    # statement claims are elevated - not every row the route returned. A
    # row on a chain outside the denominator (an annotation glyph the
    # reader elevated, a chain the exclusions removed) is counted in
    # rows_outside instead, so with_elev and the row count reconcile
    # rather than quietly disagreeing.
    for cid, how in seeded.items():
        if cid in counted:
            by_how[how] += 1
    # Longest first: a reviewer given 218 unlabelled lines should meet the
    # ones that carry the most ground first.
    unelevated.sort(key=lambda u: (-u["length_pt"], u["chain_id"]))
    # THE OTHER HALF OF THE DENOMINATOR, BY NAME. `unelevated` alone lets a
    # caller list the lines that hold no height; it does NOT let one say
    # how many of this sheet's contour lines carry an elevation RIGHT NOW,
    # because the elevated half is a bare count and the operator can delete
    # a row after the count was taken. Measured 2026-09-03 on the DEM 1 ft
    # sheet: delete twenty rows with the panel's own x and the served
    # counts still read 144/101/43 while the live list holds 81 - and the
    # twenty deleted contours appear in no list at all, so the review
    # surface this feature exists for cannot show them again. Naming both
    # halves makes `contour_chains` restatable from the live rows by
    # anything holding this dict, and the sum of the two lists IS
    # `contour_chains` by construction.
    elevated.sort(key=lambda u: (-u["length_pt"], u["chain_id"]))
    return {
        "contour_chains": n,
        "contour_len_pt": round(ln, 1),
        "with_elev": cov_n,
        "with_elev_len_pt": round(cov_len, 1),
        "no_elev": n - cov_n,
        "no_elev_len_pt": round(ln - cov_len, 1),
        "by_how": by_how,
        # rows whose chain is not in the contour denominator at all (a row
        # on an excluded or non-plan chain). Reported so with_elev and
        # len(rows) can be reconciled instead of quietly disagreeing.
        "rows": len(seeded),
        "rows_outside": len(seeded) - len(counted),
        "unclassed_chains": un_n,
        "unclassed_len_pt": round(un_len, 1),
        # EVERYTHING THE CONTOUR COUNT DOES NOT HOLD, by the reason it does
        # not hold it. The four filters above the family test used to drop
        # their chains with no record at all; each is named here with its
        # count, its footage and - for a legend exclusion - the legend's own
        # words, so a reader can see what was set aside and decide whether
        # to believe it. `unclassed` repeats unclassed_chains on purpose:
        # this dict is the complete partition and it must add up on its own.
        "set_aside": {k: {"n": sa_n[k], "len_pt": round(sa_len[k], 1),
                          **({"labels": sorted(sa_labels[k])}
                             if k in sa_labels else {})}
                      for k in sa_n},
        # The wider denominator the caller can stand behind: every stroke
        # that COULD be a contour - the families, the unclassed, the
        # exclusions, the near-white mask ink - and not the annotation and
        # title-block geometry `_is_plan_chain` rejects (see above).
        "plan_chains": n + sa_n["unclassed"] + sa_n["legend_noncontour"]
                         + sa_n["excluded_other"] + sa_n["white"],
        "plan_len_pt": round(ln + sa_len["unclassed"]
                             + sa_len["legend_noncontour"]
                             + sa_len["excluded_other"] + sa_len["white"], 1),
        # ...and the whole page, so the parts can be checked against a total
        # nothing was taken out of.
        "page_chains": len(chs),
        "page_len_pt": round(sum(c.length for c in chs), 1),
        "families": sorted(int(s) for s in fam),
        # the denominator, named line by line and in two halves that sum to
        # contour_chains: the caller can restate the coverage from its own
        # live row set instead of trusting a count taken once (see above).
        "elevated": elevated,
        "unelevated": unelevated,
    }
