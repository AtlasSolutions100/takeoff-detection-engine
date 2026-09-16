"""Check the scale in force against the dimensions the sheet PRINTS.

A civil sheet that says 1" = 40' also prints a handful of dimension
strings - 60', 120'-0", 18' - each sitting on a drawn dimension line whose
length on paper is known exactly. Every one of those is an independent
witness to the scale: printed feet / (line length in paper inches) IS a
ft-per-inch reading, made by the drafter, that no caption reader can get
wrong. The scale reader (scale_detect) already has one witness, the drawn
bar; this is the second, and it is the one that catches the failures the
bar cannot - a caption read off the wrong viewport, a 1" = 40' sheet whose
bar is a leftover from a 1" = 20' title block, a typed number that is off
by a factor of two.

What it does NOT do: pick a scale. It reports agreement, disagreement, or
that it could not tell, with the counts and the readings behind the
verdict, so the operator sees the evidence rather than a number that has
replaced theirs. (CLAUDE.md section 5, "what you extracted is not what is
printed": the witnesses here are only the dimension strings PyMuPDF
recovered as text and matched to a line - a raster sheet, or one whose
text is outlined, yields none and the check says so.)

The pairing rule, stated plainly: a dimension string is a word like
`60'`, `120'-0"`, `12'-6"` or `35.5'`; its line is the longest straight
chain run that passes within a few text-heights of the word's centre,
roughly parallel to the row the word sits on, and whose length covers the
word's centre. That is what a dimension line looks like on every CAD
sheet in the corpus (text centred above the line, between two extension
ticks); an overshoot or a string that sits beside an unrelated line shows
up as a lone outlier, which the verdict tolerates, not as a wrong verdict.
"""
from __future__ import annotations

import math
import re

import numpy as np

# whole feet, optional -inches and a fraction:
#   60'   120'-0"   12'-6"   12'-6 1/2"   (60')
# NOT decimal feet: on a civil sheet those are elevations and stations
# ("1140.96'" on Belle Farms ROAD GRADING 2 of 2 is a spot elevation), and
# a dimension line is never labelled to the hundredth of a foot.
DIM = re.compile(
    r"""^\(?(?P<ft>\d{1,4})['’′]"""
    r"""(?:\s*-?\s*(?P<in>\d{1,2})(?:\s?(?P<fn>\d)/(?P<fd>\d))?["”″])?\)?$""")

# a dimension line must be at least this long on paper: shorter runs are
# ticks, leaders and hatch, not something a dimension is written over
MIN_LINE_PT = 24.0
# how far (in text heights) the word centre may sit from the line
NEAR_HEIGHTS = 3.0
# a witness agrees with the scale when it is within this fraction of it -
# 5% covers rounded dimension text (a 118.6 ft run printed as 120') and
# a chain that stops a tick short of the extension line
AGREE_TOL = 0.05
# the verdict needs this many matched witnesses before it says anything
MIN_WITNESSES = 3
# ...and this share of them on one side
VERDICT_SHARE = 0.7
# a dimension word on the same row band as a SCALE caption - within this
# many pt across and along the caption's row - is a bar tick (0 - 30' - 60'
# under "GRAPHIC SCALE"), which is the bar witness again, not an
# independent one
BAR_EXCLUDE_ACROSS_PT = 60.0
BAR_EXCLUDE_ALONG_PT = 320.0


def parse_dim_ft(text: str) -> float | None:
    """`120'-6"` -> 120.5; None when the word is not a dimension."""
    m = DIM.match(text.strip())
    if not m:
        return None
    ft = float(m.group("ft"))
    if m.group("in"):
        inches = float(m.group("in"))
        if m.group("fn"):
            inches += float(m.group("fn")) / float(m.group("fd") or 1)
        ft += inches / 12.0
    return ft if ft > 0 else None


def _straight_runs(chains: list, min_len: float = MIN_LINE_PT) -> list:
    """Straight segments a dimension could be written over: each chain's
    consecutive-point segments, merged while collinear, as
    (x0, y0, x1, y1, length_pt, chain_id)."""
    out = []
    for c in chains:
        pts = c.get("points") if isinstance(c, dict) else getattr(c, "points", None)
        cid = c.get("chain_id") if isinstance(c, dict) else getattr(c, "chain_id", None)
        if not pts or len(pts) < 2:
            continue
        P = np.asarray(pts, float)
        start = 0
        for i in range(1, len(P)):
            last = i == len(P) - 1
            if not last:
                a = P[i] - P[start]
                b = P[i + 1] - P[i]
                na, nb = np.linalg.norm(a), np.linalg.norm(b)
                if na > 0 and nb > 0 and abs(a[0] * b[1] - a[1] * b[0]) / (na * nb) < 0.02:
                    continue
            x0, y0 = P[start]
            x1, y1 = P[i]
            ln = math.hypot(x1 - x0, y1 - y0)
            if ln >= min_len:
                out.append((float(x0), float(y0), float(x1), float(y1), ln, cid))
            start = i
    return out


def _near_bar(w, scale_words: list) -> bool:
    """True when this word sits on a SCALE caption's row band - where the
    graphic bar's tick labels are."""
    cx, cy = (w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0
    for s in scale_words:
        sx, sy = (s[0] + s[2]) / 2.0, (s[1] + s[3]) / 2.0
        horizontal = (s[2] - s[0]) >= (s[3] - s[1])
        across = abs(cy - sy) if horizontal else abs(cx - sx)
        along = abs(cx - sx) if horizontal else abs(cy - sy)
        if across <= BAR_EXCLUDE_ACROSS_PT and along <= BAR_EXCLUDE_ALONG_PT:
            return True
    return False


def _has_row_neighbour(w, words: list) -> bool:
    """True when another word sits beside this one on the same row -
    within two text heights along the row, overlapping it across."""
    x0, y0, x1, y1 = w[:4]
    horizontal = (x1 - x0) >= (y1 - y0)
    h = max((y1 - y0) if horizontal else (x1 - x0), 4.0)
    for o in words:
        if o is w or o[4] == w[4] and o[:4] == w[:4]:
            continue
        ox0, oy0, ox1, oy1 = o[:4]
        if horizontal:
            across = min(y1, oy1) - max(y0, oy0)
            gap = max(ox0 - x1, x0 - ox1)
        else:
            across = min(x1, ox1) - max(x0, ox0)
            gap = max(oy0 - y1, y0 - oy1)
        if across > 0.5 * h and gap < 2.0 * h:
            return True
    return False


# an extension line / tick counts as bounding a span when its end sits
# within this many pt of the dimension line
TICK_TOUCH_PT = 3.0
# ...and it crosses the line at no less than this angle (architectural
# ticks are 45-degree slashes, not perpendicular extension lines)
TICK_MIN_DEG = 30.0


def _span_between_ticks(run, seg_arr, t_word: float,
                        t_excl: tuple | None = None) -> tuple | None:
    """The dimension SPAN the word sits in: from the nearest extension
    line / tick before the word's projection to the nearest after, along
    the run. Returns (t_lo, t_hi) as run parameters, or None when the run
    has no tick on both sides of the word. Ticks whose position falls in
    t_excl (the word's own footprint on the run) are ignored: Revit draws
    a text-mask rectangle behind every dimension string, and its edges
    end on the dimension line exactly like ticks (Belle Farms Structural:
    "16'" was measured across its own 26 pt mask instead of its bay).

    Why not the run's ends: a CAD dimension line is drawn once across a
    whole string of dimensions (Belle Farms Structural: one line the width
    of the building, a tick at every grid, a number in every bay), so the
    run is far longer than any one printed number and the number is not at
    its middle. The ticks are what the drafter measured between."""
    ax, ay, bx, by, ln, _cid = run
    if seg_arr is None or not len(seg_arr):
        return None
    dx, dy = (bx - ax) / ln, (by - ay) / ln          # unit along
    nx, ny = -dy, dx                                 # unit normal
    # every segment's two endpoints, in run coordinates
    ex = seg_arr[:, [0, 2]] - ax
    ey = seg_arr[:, [1, 3]] - ay
    along = ex * dx + ey * dy
    sperp = ex * nx + ey * ny                        # signed
    perp = np.abs(sperp)
    # crossing angle
    sdx, sdy = seg_arr[:, 2] - seg_arr[:, 0], seg_arr[:, 3] - seg_arr[:, 1]
    sl = np.hypot(sdx, sdy)
    with np.errstate(invalid="ignore", divide="ignore"):
        cosang = np.abs((sdx * dx + sdy * dy) / sl)
    steep = cosang <= math.cos(math.radians(TICK_MIN_DEG))
    # a tick either ENDS on the line (an extension line stopping at it) or
    # CROSSES it (a Revit slash, an extension line running through to a
    # second dimension row): both are a mark the drafter measured to
    crosses = (sperp[:, 0] * sperp[:, 1]) <= 0
    ends_on = (perp <= TICK_TOUCH_PT).any(axis=1)
    touches = (crosses | ends_on) & steep
    if not touches.any():
        return None
    # the along-position of the mark: the crossing point when it crosses,
    # else the end that sits on the line
    denom = sperp[:, 0] - sperp[:, 1]
    frac = np.divide(sperp[:, 0], denom, out=np.zeros_like(denom),
                     where=np.abs(denom) > 1e-12)
    at_cross = along[:, 0] + (along[:, 1] - along[:, 0]) * frac
    near_end = np.where(perp[:, 0] <= perp[:, 1], along[:, 0], along[:, 1])
    mark = np.where(crosses, at_cross, near_end)
    tt = mark[touches] / ln
    tt = tt[(tt >= -0.02) & (tt <= 1.02)]
    if t_excl is not None:
        tt = tt[(tt < t_excl[0]) | (tt > t_excl[1])]
    lo = tt[tt < t_word]
    hi = tt[tt > t_word]
    if not len(lo) or not len(hi):
        return None
    return float(lo.max()), float(hi.min())


def _seg_witness(word, runs: list, seg_arr=None) -> list:
    """Every span this dimension word could be written over, nearest line
    first, each as (x0, y0, x1, y1, span_pt, chain_id, offset_pt).

    A word offers up to two readings per nearby line: the bay between the
    ticks either side of it, and - when the word sits over the middle of
    the run - the run itself. Both are real shapes on a CAD sheet (the
    overall "97'" on Belle Farms Structural is centred on a run that the
    bay dimensions' extension lines also end on), and nothing local says
    which one the drafter meant; dimension_check lets the sheet's other
    dimensions decide (the consensus cluster)."""
    x0, y0, x1, y1 = word[:4]
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    h = max(min(x1 - x0, y1 - y0), 4.0)
    out = []
    for run in runs:
        ax, ay, bx, by, ln, cid = run
        dx, dy = bx - ax, by - ay
        t = ((cx - ax) * dx + (cy - ay) * dy) / (ln * ln)
        if t < -0.02 or t > 1.02:
            continue
        px, py = ax + t * dx, ay + t * dy
        d = math.hypot(cx - px, cy - py)
        if d > NEAR_HEIGHTS * h:
            continue
        # the word's own footprint along the run, widened by a text height
        corners = ((x0, y0), (x1, y0), (x0, y1), (x1, y1))
        tc = [((qx - ax) * dx + (qy - ay) * dy) / (ln * ln) for qx, qy in corners]
        pad = h / ln
        excl = (min(tc) - pad, max(tc) + pad)
        # a run that starts AND ends inside the word's footprint is the
        # text mask's own edge (or an underline), not a dimension line: a
        # dimension line always reaches past its text
        if excl[0] <= 0.0 and excl[1] >= 1.0:
            continue
        span = _span_between_ticks(run, seg_arr, t, excl)
        if span is not None:
            t0, t1 = span
            mid = (t0 + t1) / 2.0
            sl = (t1 - t0) * ln
            # the word sits inside the bay it labels, near its middle
            if abs(t - mid) <= 0.3 * (t1 - t0) and sl >= MIN_LINE_PT:
                out.append((ax + t0 * dx, ay + t0 * dy, ax + t1 * dx,
                            ay + t1 * dy, sl, cid, d))
        if 0.2 <= t <= 0.8:
            out.append((ax, ay, bx, by, ln, cid, d))
    out.sort(key=lambda c: (c[6], -c[4]))
    return out


def _pick_reading(readings: list, best_med: float, tol: float) -> dict:
    """The reading a consensus word stands behind: the one nearest the
    cluster median among those within `tol` of it, else simply the nearest.

    The cluster is grown with the tolerance measured from its SEED value,
    so a word can be a member through a reading that is outside `tol` of
    the MEDIAN (seed 10, tol 10 percent, members 9 / 9 / 11: median 9,
    window 8.1-9.9, the word whose only reading is 11 has nothing inside
    it). Barn Structural S1-1.3 hit exactly that and /scale/dimensions
    answered 500 (2026-09-16). Never empty, so never a crash: the nearest
    reading is still this word's best claim.
    """
    inside = [r for r in readings
              if abs(r["implied_ft_per_in"] - best_med) <= tol * best_med]
    return min(inside or readings,
               key=lambda r: abs(r["implied_ft_per_in"] - best_med))


def dimension_check(words: list, chains: list, ft_per_paper_inch: float | None) -> dict:
    """Every printed dimension paired with its line, and the verdict.

    words: (x0, y0, x1, y1, text, ...) in the chains' coordinate space
    (extract.page_words). chains: the stored detect result's chain dicts.
    ft_per_paper_inch: the scale in force, or None (then only the readings
    are reported and the verdict is "no-scale").
    """
    scale_words = [w for w in words if "SCALE" in str(w[4]).upper()]
    runs = _straight_runs(chains)
    # every short straight piece as well, for the ticks (MIN_LINE_PT would
    # drop them): one array, so a candidate run can be checked against all
    # of them at once
    ticks = _straight_runs(chains, min_len=3.0)
    seg_arr = np.asarray([s[:4] for s in ticks], float) if ticks else None
    witnesses, skipped_bar, unmatched, in_sentence = [], 0, [], 0
    seen = set()
    for w in words:
        key = (str(w[4]), round(w[0]), round(w[1]))
        if key in seen:
            continue            # the same word printed twice in one place
        seen.add(key)
        ft = parse_dim_ft(str(w[4]))
        if ft is None:
            continue
        cx, cy = (w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0
        if _near_bar(w, scale_words):
            skipped_bar += 1
            continue
        # A DIMENSION STANDS ALONE. "70' x 9' PULLTHROUGH STALL" and "5'
        # SIDEWALK" (Barn C3.0) carry feet in a sentence, over no line of
        # their own - the first pairing pass took the 70' over a nearby
        # 134 pt run and reported 37 ft/in against a 20 ft/in sheet. A
        # word with another word beside it on its own row is a label.
        if _has_row_neighbour(w, words):
            in_sentence += 1
            continue
        hits = _seg_witness(w, runs, seg_arr)
        if not hits:
            unmatched.append(str(w[4]))
            continue
        witnesses.append({
            "text": str(w[4]), "ft": ft,
            "at": [round(cx, 1), round(cy, 1)],
            "readings": [{"line_pt": round(ln, 1), "chain_id": cid,
                          "implied_ft_per_in": round(ft / (ln / 72.0), 3)}
                         for (_a, _b, _c, _d, ln, cid, _e) in hits[:4]],
        })
    # WHICH READING EACH WORD STANDS BEHIND. Every word's readings go into
    # one pool; the tightest cluster holding the most DISTINCT words is the
    # sheet's own consensus, and a word that has a reading in it takes that
    # one. A word with no reading in the cluster keeps its nearest-line
    # reading and is the outlier the verdict counts against.
    pool = [(i, r["implied_ft_per_in"]) for i, w in enumerate(witnesses)
            for r in w["readings"]]
    best_words, best_med = set(), None
    for _i, v in pool:
        members = [(j, u) for j, u in pool if abs(u - v) <= AGREE_TOL * v]
        wordset = {j for j, _u in members}
        if len(wordset) > len(best_words):
            best_words = wordset
            best_med = float(np.median([u for _j, u in members]))
    for i, w in enumerate(witnesses):
        pick = w["readings"][0]
        if best_med is not None and i in best_words:
            pick = _pick_reading(w["readings"], best_med, AGREE_TOL)
        w["line_pt"] = pick["line_pt"]
        w["chain_id"] = pick["chain_id"]
        w["implied_ft_per_in"] = pick["implied_ft_per_in"]
        w["in_consensus"] = bool(best_med is not None and i in best_words)
    out = {
        "n_dimension_words": len(witnesses) + len(unmatched) + skipped_bar + in_sentence,
        "n_bar_ticks_skipped": skipped_bar,
        "n_in_sentence": in_sentence,
        "n_unmatched": len(unmatched),
        "unmatched": unmatched[:12],
        "witnesses": witnesses,
        "scale_ft_per_in": ft_per_paper_inch,
    }
    if not witnesses:
        out.update({"verdict": "no-witnesses", "n_agree": 0, "n_disagree": 0,
                    "why": "No printed dimension on this sheet could be paired "
                           "with a drawn line, so nothing here checks the scale."})
        return out
    best_cluster = [w for w in witnesses if w["in_consensus"]]
    out["witness_consensus_ft_per_in"] = round(best_med, 3)
    out["witness_consensus_n"] = int(len(best_cluster))
    if not ft_per_paper_inch:
        out.update({"verdict": "no-scale", "n_agree": 0, "n_disagree": 0,
                    "why": f"No scale is in force; {len(witnesses)} printed "
                           f"dimension{'s' if len(witnesses) != 1 else ''} "
                           f"imply about {best_med:.1f} ft per inch."})
        return out
    s = float(ft_per_paper_inch)
    for w in witnesses:
        w["agrees"] = abs(w["implied_ft_per_in"] - s) <= AGREE_TOL * s
    n_agree = sum(1 for w in witnesses if w["agrees"])
    n_dis = len(witnesses) - n_agree
    out.update({"n_agree": n_agree, "n_disagree": n_dis})
    n = len(witnesses)
    if n < MIN_WITNESSES:
        out["verdict"] = "inconclusive"
        out["why"] = (f"Only {n} printed dimension{'s' if n != 1 else ''} "
                      f"could be paired with a line ({n_agree} agree"
                      f"{'s' if n_agree == 1 else ''} with {s:g} ft/in); "
                      f"fewer than {MIN_WITNESSES} is not a check.")
    elif n_agree >= VERDICT_SHARE * n:
        out["verdict"] = "agrees"
        out["why"] = (f"{n_agree} of {n} printed dimensions land within "
                      f"{AGREE_TOL:.0%} of {s:g} ft/in when measured against "
                      f"their own dimension lines.")
    elif len(best_cluster) >= VERDICT_SHARE * n and abs(best_med - s) > AGREE_TOL * s:
        out["verdict"] = "disagrees"
        out["why"] = (f"{len(best_cluster)} of {n} printed dimensions agree "
                      f"with EACH OTHER at about {best_med:.1f} ft/in, not "
                      f"with the {s:g} ft/in in force ({n_agree} of {n} "
                      f"match it). Check the scale before trusting any "
                      f"quantity on this sheet.")
    else:
        out["verdict"] = "inconclusive"
        out["why"] = (f"{n_agree} of {n} printed dimensions match {s:g} "
                      f"ft/in and the rest do not agree with each other "
                      f"either - the pairing of text to line is unreliable "
                      f"on this sheet, so this is not a check.")
    return out
