"""Legend reader: let the sheet label its own line styles.

Many sheets carry a LEGEND box pairing a short line sample with a name
("PROPERTY LINE", "PROPOSED CL", "EDGE OF PAVEMENT"). Reading it turns
style labeling from a human chore into sheet-provided ground truth:

  1. locate the word LEGEND (text layer or the words list passed in),
  2. collect label rows beneath it (words grouped into lines),
  3. for each row, the line SAMPLE is the linework in the strip just left
     of the label text,
  4. characterize the sample (color, weight, class, avg dash length) and
     match it against the sheet's detected styles.

Output maps style_id -> legend label, used to pre-fill suggested labels.
"""

import re
from collections import defaultdict

import numpy as np

from .chain import _classify_cadence, _weight_class

# legend geometry (pt): how far below LEGEND to look, label row grouping,
# and the sample strip to the left of each label
REGION_H = 420.0
REGION_W = 520.0
_WORDS = re.compile(r"[A-Za-z]{2}")


def _is_usable_label(t: str) -> bool:
    """A legend label an estimator could act on.

    A legend row's text is only worth propagating if it says something. On
    a client's residential civil C4.3 the swatch for the footer drain has blue flow
    CHEVRONS drawn inside the sample bar, and the row reader picked up the
    chevrons as the label: six styles, 12,379 pt, all named `> > >`. One of
    them is `SE-P-FND DRN` at 10,437 pt = 1,449.6 ft of perimeter drain - the
    most valuable line item on the sheet, and per the client's own estimate
    the one they price highest per foot. Sheet 2 does the same with dimension
    fragments (`"> 12'-1`, 17,761 pt).

    Worse than useless: the legend label OVERWRITES the CAD layer name, which
    would have read `SE-P-FND DRN` and been perfectly serviceable. Refusing
    the label restores that fallback.

    Two letters in a row is the whole test - it admits "PVC" and "4in" and
    rejects chevrons, arrowheads and dimension fragments.
    """
    t = t or ""
    # A trailing colon is a form-field caption or a section header
    # ("Field Book No:", "FINAL GRADING NOTES:"), never the name of a thing
    # that gets built. C9.0's title block matched "Field Book No:" to real
    # linework through a table rule sitting in the strip.
    if t.rstrip().endswith(":"):
        return False
    return bool(_WORDS.search(t))


# "BOW = BOTTOM OF WALL", "C/O = CLEANOUT", "FFE = FINISHED FLOOR ELEVATION":
# a short all-caps tag, an equals sign, then words. The shape of a definition
# row in a spot-elevation or abbreviation table. A LINE-legend row is a drawn
# sample beside a NAME ("SS SS PROPOSED SANITARY SEWER LINE"); it does not
# need an equals sign, and the corpus's genuine inline "=" definitions
# ("### = SHEAR WALL") arrive through the inline path with its own guards.
_DEF_ROW = re.compile(r"^[A-Z][A-Z0-9]{0,3}(?:/[A-Z0-9]{1,3})?\s*=\s")


def _assign_label(store: dict, sid, label: str) -> None:
    """Record a style's legend label, preferring the more usable one.

    setdefault made this first-writer-wins, so a chevron row that happened to
    be processed first kept its `> > >` even though the reader had ALREADY
    computed `PROPOSED 4" RIGID PERFORATED PVC FOOTER DRAIN PIPE` for the same
    style and joined it correctly. The right answer was computed and thrown
    away.
    """
    if not _is_usable_label(label):
        return
    cur = store.get(sid)
    if cur is None or not _is_usable_label(cur):
        store[sid] = label


ROW_TOL = 7.0
SAMPLE_W = 240.0
SAMPLE_H = 7.0
# A legend row names a plan style only in the swatch's own pen: max per-channel
# RGB difference between the swatch and the style it may name (see the dashed
# matcher for the measured case that set it).
LEGEND_COLOR_TOL = 0.1


def read_legend(words: list, chains: list, units: list | None = None) -> dict:
    """Returns {"entries": [...], "style_labels": {style_id: label}}."""
    seen = set()
    deduped = []
    for w in words:
        key = (str(w[4]).strip().upper(), round(w[0] / 6), round(w[1] / 6))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(w)
    words = deduped
    legend_words = [w for w in words if str(w[4]).strip().upper() == "LEGEND"]
    entries = []
    style_labels: dict = {}

    for lw in legend_words:
        lx, ly = lw[0], lw[3]
        region = [w for w in words
                  if ly - 6 <= w[1] <= ly + REGION_H
                  and lx - REGION_W * 0.55 <= w[0] <= lx + REGION_W
                  and str(w[4]).strip().upper() != "LEGEND"]
        # group words into rows
        rows = defaultdict(list)
        for w in region:
            cy = (w[1] + w[3]) / 2
            key = round(cy / ROW_TOL)
            rows[key].append(w)
        for _, ws in sorted(rows.items()):
            ws.sort(key=lambda w: w[0])
            # text layer + OCR both contribute the same words at slightly
            # different boxes; drop x-overlapping repeats of the same text
            kept = []
            for w in ws:
                t = str(w[4]).strip()
                dup = any(str(k[4]).strip().upper() == t.upper()
                          and min(w[2], k[2]) - max(w[0], k[0])
                          > 0.5 * (w[2] - w[0])
                          for k in kept)
                if not dup:
                    kept.append(w)
            ws = kept
            # OCR sometimes reads the tail of the sample symbol as a stray
            # short token ("Ct 8' MASONRY WALL"); legend labels are caps, so
            # drop leading short tokens containing lowercase letters
            while ws and len(str(ws[0][4]).strip()) <= 3 \
                    and any(ch.islower() for ch in str(ws[0][4])):
                ws = ws[1:]
            if not ws:
                continue
            # The label is the word run NEAREST the sample strip. The row
            # bucket spans the whole legend region's width, so a legend row
            # and the title-block column at the same height glue into one
            # string - C9.0 shipped "VALLEY GUTTER C COPYRIGHT 2025" that
            # way. A column boundary is a gap no phrase contains: words
            # within a run sit a few pt apart, columns sit tens of pt apart.
            runs = [[ws[0]]]
            for prev, w in zip(ws, ws[1:]):
                if w[0] - prev[2] > 28.0:
                    runs.append([])
                runs[-1].append(w)
            ws = runs[0]
            # An index-contour row prints its NUMBER riding the sample bar,
            # a bar-width gap before the words: "7900   EXISTING CONTOUR
            # INTERVAL". A first run with no two letters in a row is not a
            # name by itself - join the next run rather than orphan the row.
            if len(runs) > 1 and not _is_usable_label(
                    " ".join(str(w[4]).strip() for w in ws)):
                ws = ws + runs[1]
            label = " ".join(str(w[4]).strip() for w in ws).strip()
            if not (3 <= len(label) <= 60):
                continue
            x0 = ws[0][0]
            cy = float(np.mean([(w[1] + w[3]) / 2 for w in ws]))
            sample = _sample_in_strip(chains, x0 - SAMPLE_W, x0 - 4.0, cy)
            _sbox = [x0 - SAMPLE_W, cy - 9.0, x0 - 4.0, cy + 9.0]
            if sample is None and units is not None:
                # swatches are tiny and often fail to chain (pattern-filled
                # bars, touching marks); the RAW strip marks still spell the
                # cadence out - read it directly and join to a style by
                # measured geometry below
                sample = _sample_from_units(units, x0 - SAMPLE_W, x0 - 4.0, cy)
            if sample is None:
                continue
            if not sample.get("swatch_template") and units is not None:
                alt = _sample_from_units(units, x0 - SAMPLE_W, x0 - 4.0, cy)
                if alt and alt.get("swatch_template"):
                    sample["swatch_template"] = alt["swatch_template"]
            # A row shaped "TAG = LONG NAME" is an ABBREVIATION DEFINITION -
            # the vocabulary a spot-elevation or callout table teaches - not
            # a line type. Its "sample" here is usually whatever plan
            # linework happens to cross the strip left of the table: on Res-A
            # C3.0 the existing-contour family itself (grey d9.0 g4.5, 105
            # chains, 87,441 pt) satisfied the 60%-containment test with a
            # short near-horizontal fragment and was named "BOW = BOTTOM OF
            # WALL", while the sheet's real "EXISTING CONTOUR" row went
            # unmatched because BOW had already claimed the style. The flag
            # keeps the row VISIBLE as vocabulary but bars it from labeling
            # any style below. A definition with a genuinely dedicated
            # swatch is the self-join shape, which is already handled.
            entries.append({"label": label, "sample_box": _sbox,
                            "definition": bool(_DEF_ROW.match(label)),
                            **sample})

    # inline "SWATCH = NAME" definitions: sheets without a LEGEND box
    # define a symbol in place ("### = SHEAR WALL", Belle Structural p2).
    # Anchor on an '=' word; the NAME is the word run to its right, the
    # sample is the geometry strip to its left. Guards, both measured:
    #  - a scale note ('1" = 20'') has TEXT in the left strip and a
    #    numeric right side - rejected by the no-words-left test and the
    #    mostly-alpha label test
    #  - a title-block '=' with no same-row words yields no label
    for w in words:
        if str(w[4]).strip() != "=":
            continue
        ex0, ex1 = w[0], w[2]
        ecy = (w[1] + w[3]) / 2
        eh = max(w[3] - w[1], 6.0)
        # the swatch of a '= NAME' definition hugs the '=': a tight strip
        # keeps distant room labels (Belle: 'HALL' 220pt left) from
        # vetoing the row, and keeps unrelated plan geometry out of the
        # sample. A scale note's '1"' sits 5-30pt left - still caught.
        strip_x0, strip_x1 = ex0 - 110.0, ex0 - 4.0
        if any(ww[2] > strip_x0 and ww[0] < strip_x1
               and abs((ww[1] + ww[3]) / 2 - ecy) <= eh
               for ww in words if ww is not w):
            continue
        right = sorted((ww for ww in words
                        if abs((ww[1] + ww[3]) / 2 - ecy) <= eh
                        and ww[0] >= ex1 - 1.0 and ww[0] - ex1 <= 60.0),
                       key=lambda ww: ww[0])
        run = []
        cur_x = ex1
        for ww in right:
            if run and ww[0] - cur_x > 24.0:
                break
            run.append(ww)
            cur_x = ww[2]
        label = " ".join(str(ww[4]).strip() for ww in run).strip()
        alpha = sum(ch.isalpha() for ch in label)
        if not (3 <= len(label) <= 60) or alpha < 3 or alpha < len(label) * 0.5:
            continue
        sample = _sample_in_strip(chains, strip_x0, strip_x1, ecy)
        if sample is None and units is not None:
            sample = _sample_from_units(units, strip_x0, strip_x1, ecy)
        if sample is None:
            continue
        if not sample.get("swatch_template") and units is not None:
            alt = _sample_from_units(units, strip_x0, strip_x1, ecy)
            if alt and alt.get("swatch_template"):
                sample["swatch_template"] = alt["swatch_template"]
        entries.append({"label": label,
                        "sample_box": [strip_x0, ecy - 9.0, strip_x1, ecy + 9.0],
                        **sample})

    if not entries:
        return {"entries": [], "style_labels": {}}

    # the sample IS a chain of some style; the join is exact - label that
    # style and nothing else. But only when the style is DISTINCTIVE: a
    # legend sample landing in the generic black-solid bucket would smear
    # its label over every plain solid on the sheet (substation masonry wall
    # labeled 118 unrelated lines). Dashed cadences, non-black colors, and
    # heavy weights identify a real style; plain thin solids do not.
    by_style_count: dict = defaultdict(int)
    for c in chains:
        by_style_count[c.style_id] += 1
    # exact joins that land on the SWATCH'S OWN style are self-joins: the
    # legend bar chained into a tiny style of its own instead of sharing
    # the plan style (LTSCALE: paper-space swatch, model-space plan).
    # Measured (substation p4): ADJACENT PROPERTY LINE joined a 261pt 3-chain
    # 'misc' style holding its own swatch bar, while the real 2437pt
    # d9-d45-g9 family sat unlabeled. A style that is mostly legend-strip
    # ink (or nearly absent outside the strips) is no plan match.
    strip_boxes = [e["sample_box"] for e in entries if e.get("sample_box")]
    def _strip_ink(sid):
        tot = out_ink = 0.0
        for c in chains:
            if c.style_id != sid:
                continue
            m = np.asarray(c.pts).mean(axis=0)
            inside = any(b[0]-6 <= m[0] <= b[2]+6 and b[1]-8 <= m[1] <= b[3]+8
                         for b in strip_boxes)
            tot += c.length
            if not inside:
                out_ink += c.length
        return tot, out_ink
    self_joined: set = set()
    for e in entries:
        sid = e.get("sample_style")
        if sid is None:
            continue
        tot, out_ink = _strip_ink(sid)
        if out_ink < 400.0 or out_ink < 0.6 * tot:
            self_joined.add(sid)
            e["sample_style"] = None  # fall through to the matcher
    # unit-derived samples have no chained style: join by measured geometry
    # (class family + weight + colour + dash length within tolerance)
    reps: dict = {}
    for c in chains:
        r = reps.setdefault(c.style_id, c)
        if c.length > r.length:
            reps[c.style_id] = c
    for e in entries:
        if e.get("sample_style") is not None:
            continue
        if e.get("definition"):
            continue   # vocabulary row: never hunt a plan style for it
        best = None
        best_key = None
        for sid, r in reps.items():
            if sid in self_joined:
                continue
            if not r.klass.startswith("dash"):
                continue
            # class must match - EXCEPT for a COMPOUND swatch (two or more
            # dash lengths, max_dash well above the average): its slash
            # ticks don't chain (they slant off-axis), so the plan chains
            # as dash1 while the swatch reads dash2 (substation property line:
            # bars d45.7 g45.7). A plain swatch and a plan style of another
            # class are two linetypes: Res-A C2.0's "EXISTING IRRIGATION
            # DITCH FLOWLINE" (a dash-dot swatch) named 1,739 LF of dash1
            # water service through the old exact-weight exemption.
            compound = bool(e.get("max_dash")) and \
                e["max_dash"] > 1.6 * max(e["avg_dash"], 1e-6)
            if r.klass != e["klass"] and not compound:
                continue
            # +-2 weight classes: paper-space swatches print heavier than
            # the model-space line (substation PROPERTY LINE swatch weight 4 vs
            # the real d9-d45-g9 family at weight 2 - human-confirmed map)
            wdiff = abs(_weight_class(r.width) - e["weight"])
            if wdiff > 2:
                continue
            # near-WHITE candidates are the cores of OUTLINED thick lines
            # (substation property line: black hairline edges + white w3.3
            # core) - their visible color IS the outline, so the color
            # gate must not compare against the swatch's black
            white_core = r.color and min(r.color) > 0.9
            # 0.1 per channel (was 0.2): the swatch is drawn in the plan
            # style's own pen, and a 0.15 difference is another pen - the
            # irrigation-ditch row's (0.5, 0.62, 1.0) against the water
            # service's (0.35, 0.51, 0.99). Screened variants of one pen
            # differ by far more than 0.1 and were never caught by 0.2.
            if not white_core and e["color"] and r.color and \
                    max(abs(a - b) for a, b in zip(e["color"], r.color)) > LEGEND_COLOR_TOL:
                continue
            rd = (r.ink or r.length) / max(r.n_dashes, 1)
            # LTSCALE: swatch lengths are paper-space - test the window
            # against BOTH the swatch's average and its longest mark
            # (substation PL: swatch avg 10.2 / max 27.2 vs plan bars 45.7)
            fits = False
            for ref in (e["avg_dash"], e.get("max_dash") or 0):
                if ref <= 0:
                    continue
                lo, hi = sorted([ref, rd])
                if hi <= 1.9 * lo + 3.0:
                    fits = True
                    break
            if not fits:
                continue
            cand = sum(c2.length for c2 in chains if c2.style_id == sid)
            # closest WEIGHT wins first; true-color matches outrank the
            # white-core bypass; then closest COLOUR (0.05 buckets), then
            # ink. Colour before ink: the Res-A "7900 EXISTING CONTOUR
            # INTERVAL" swatch (grey 0.459) matched the 0.59-grey regular
            # contours at 9x the ink instead of the exact-0.46 index
            # family - a 0.13-off colour must not beat an exact one on
            # bulk. (Thick and thin variants of one linetype are still
            # different legend rows - human-confirmed.)
            if white_core or not (e["color"] and r.color):
                cbucket = 0 if white_core else 2
            else:
                cbucket = min(4, int(max(abs(a - b) for a, b in
                                         zip(e["color"], r.color)) / 0.05))
            key = (wdiff, 1 if white_core else 0, cbucket, -cand)
            if best is None or key < best_key:
                best, best_key = (sid, cand), key
        single_len = not e.get("max_dash") \
            or e["max_dash"] <= 1.6 * max(e["avg_dash"], 1e-6)
        if best is None and single_len:
            # SECOND TIER: bold dashed lines often chain as individual
            # SOLID bars (each long dash = one solid chain), so the dashed
            # legend entry finds no dashed style. Match a solid style whose
            # typical mark length fits the swatch's LONGEST bar, with
            # matching colour and near weight - the sheet's own legend
            # doing the matching, not a stored template.
            # COMPOUND swatches (dash-dot-dot: max_dash >> avg) never take
            # this tier - a uniform-bar solid style cannot be that
            # linetype (substation PL swatch max/avg 2.67 wrongly took the
            # 45.7pt fence bars; honest unmatched beats a wrong label).
            want = e.get("max_dash") or e["avg_dash"]
            for sid, r in reps.items():
                if r.klass != "solid":
                    continue
                if abs(_weight_class(r.width) - e["weight"]) > 1:
                    continue
                if e["color"] and r.color and \
                        max(abs(a - b) for a, b in zip(e["color"], r.color)) > LEGEND_COLOR_TOL:
                    continue
                sib = [c for c in chains if c.style_id == sid]
                if not (2 <= len(sib) <= 150):
                    continue
                med = float(np.median([c.length for c in sib]))
                lo, hi = sorted([want, med])
                if hi > 1.9 * lo + 3.0:
                    continue
                cand = sum(c.length for c in sib)
                if best is None or cand > best[1]:
                    best = (sid, cand)
        if best is None and e["klass"] == "solid":
            # THIRD TIER: a solid legend entry (PROPOSED CONTOUR) matches
            # a CURVY solid family - contours measure med len/chord >= 1.3
            # while straight border/wall buckets sit at 1.0. Straight
            # solid buckets stay unmatched (honest miss beats a smear).
            curv: dict = {}
            for c in chains:
                if c.klass != "solid" or len(c.pts) < 2:
                    continue
                ch2 = float(np.hypot(*(np.asarray(c.pts[-1], float)
                                       - np.asarray(c.pts[0], float))))
                if ch2 > 1.0:
                    curv.setdefault(c.style_id, []).append(c.length / ch2)
            for sid, r in reps.items():
                if sid in self_joined or r.klass != "solid":
                    continue
                if abs(_weight_class(r.width) - e["weight"]) > 2:
                    continue
                if e["color"] and r.color and \
                        max(abs(a - b) for a, b in zip(e["color"], r.color)) > LEGEND_COLOR_TOL:
                    continue
                rats = curv.get(sid) or []
                if not rats or float(np.median(rats)) < 1.3:
                    continue
                cand = sum(c.length for c in chains if c.style_id == sid)
                if best is None or cand > best[1]:
                    best = (sid, cand)
        if best is not None:
            e["sample_style"] = best[0]
    # one style, one name: when two entries claim the same style (PL and
    # ADJACENT are the same linetype at different weights), the first row
    # keeps it and later claimants go honestly unmatched. A runner-up
    # fallback was tried here (2026-08-21) and REVERTED: it relabeled
    # claim-conflicted texture rows (gravel / chip&seal / patio) and even
    # PROPERTY/TRACT LINE onto title-block styles - the claim-death is a
    # safety valve, not a bug. The colour-bucket ranking above is what
    # fixes the 7900 index-contour row; it needs no fallback.
    claimed: set = set()
    for e in entries:
        sid = e.get("sample_style")
        if sid is None:
            continue
        if sid in claimed and by_style_count.get(sid, 0) > 0 \
                and e is not next(x for x in entries
                                  if x.get("sample_style") == sid):
            e["sample_style"] = None
        else:
            claimed.add(sid)
    curvy_cache: dict = {}
    def _curvy(sid):
        if sid not in curvy_cache:
            ratios = []
            for c in chains:
                if c.style_id != sid or len(c.pts) < 2:
                    continue
                chord = float(np.hypot(*(np.asarray(c.pts[-1], float)
                                         - np.asarray(c.pts[0], float))))
                if chord > 1.0:
                    ratios.append(c.length / chord)
            curvy_cache[sid] = bool(ratios) and float(np.median(ratios)) >= 1.3
        return curvy_cache[sid]
    for e in entries:
        if e.get("definition"):
            # An abbreviation definition never labels linework, however
            # plausible the strip sample looked. Honest unmatched beats
            # 36,434 ft of existing contours named after a spot tag.
            e["sample_style"] = None
            continue
        distinctive = (
            e["klass"].startswith("dash")
            or (e["color"] and max(e["color"]) - min(e["color"]) > 0.15)
            or (e["color"] and min(e["color"]) > 0.35)
            or e["weight"] >= 2
            or (e.get("sample_style") is not None and _curvy(e["sample_style"]))
        )
        if not distinctive and by_style_count[e["sample_style"]] > 8:
            continue  # generic bucket: an honest miss beats a smear
        if not e["klass"].startswith("dash") \
                and by_style_count[e["sample_style"]] > 25 \
                and not _curvy(e["sample_style"]):
            # solid bucket too big to be one legend feature - UNLESS the
            # bucket is curvy (contour families measure med len/chord 2.4;
            # straight border/wall buckets sit at 1.0)
            continue
        if e["sample_style"] is not None:
            _assign_label(style_labels, e["sample_style"], e["label"])
        # compound swatch: label the OTHER distinctive styles drawn in the
        # same sample (hatch + centerline = one symbol). Same distinctive
        # gate as above so a generic thin black solid never gets smeared.
        for sid in e.get("co_styles", []):
            r = reps.get(sid)
            if r is None:
                continue
            d2 = (r.klass.startswith("dash")
                  or (r.color and max(r.color) - min(r.color) > 0.15)
                  or (r.color and min(r.color) > 0.35)
                  or _weight_class(r.width) >= 2)
            if d2 and by_style_count[sid] <= 150:
                _assign_label(style_labels, sid, e["label"])
        # pen-family extension: a symbol drawn with a DISTINCTIVE pen
        # (saturated color) fragments across styles when its dashes chain
        # differently run to run (Belle shear centerline: native 3-3 +
        # two re-measured dash2 variants, all red). Same color, dashed
        # class -> same symbol. Greys/blacks never extend - black is the
        # default pen for everything and would smear.
        base = reps.get(e.get("sample_style"))
        if base is not None and base.color and (
                max(base.color) - min(base.color) > 0.3
                or min(base.color) > 0.9):
            white_pen = min(base.color) > 0.9
            base_wc = _weight_class(base.width)
            base_dash = (base.ink or base.length) / max(base.n_dashes, 1)
            for sid, r in reps.items():
                if sid in style_labels or r.klass == "dash-text":
                    continue
                if not r.klass.startswith("dash") \
                        and not (white_pen and r.klass == "solid"):
                    continue
                if white_pen and abs(_weight_class(r.width) - base_wc) > 1:
                    continue
                # the same pen (0.1 per channel, as the matcher) AND the
                # same linetype: the sample's class, or its dash length
                # within the matcher's window when a variant re-measured
                # (Belle shear centreline: native 3-3 beside two dash2
                # readings of the same red symbol)
                if not (r.color and max(abs(a - b) for a, b in
                                        zip(base.color, r.color)) <= LEGEND_COLOR_TOL):
                    continue
                if r.klass != base.klass and r.klass.startswith("dash"):
                    rd = (r.ink or r.length) / max(r.n_dashes, 1)
                    lo, hi = sorted([base_dash, rd])
                    if hi > 1.9 * lo + 3.0:
                        continue
                _assign_label(style_labels, sid, e["label"])
    _extend_by_continuation(style_labels, chains)
    return {"entries": entries, "style_labels": style_labels}


def _extend_by_continuation(style_labels: dict, chains: list) -> None:
    """A labeled line that BREAKS mid-run keeps its name across the break.

    The chainer sometimes reads one physical line as two styles: on Res-A C3.0
    the grey existing contours (d9.0 g4.5) continue end-to-end into a sibling
    style reading d9.0 g44.3 - same 9pt dashes, gaps read one-dash-sparse -
    11 chains / 14,343 pt (5,976 ft at 1"=30') of contour footage that would
    otherwise sit unlabeled, invisible to the earthwork panel's contour
    suggestions, under a machine name.

    Evidence gates, all three required, measured against the smear cases:
      * >= 2 joints - chain ends within 12 pt, outgoing tangents opposed
        (dot < -0.6): geometric continuation, not proximity
      * joints >= 0.25 x the target style's chain count - a 174-chain
        generic-solid pool with 3 incidental joints must NOT inherit the
        name (that is the substation masonry smear with extra steps); a small
        fragment family with joints on a quarter of its chains is the same
        physical line
      * colors within 0.15 per channel, and never onto annotation class
    Label-layer only: no geometry, class or quantity moves.
    """
    from scipy.spatial import cKDTree

    labeled = set(style_labels)
    if not labeled:
        return
    by_sid: dict = defaultdict(list)
    for c in chains:
        by_sid[c.style_id].append(c)

    def _ends(c):
        p = np.asarray(c.pts, float)
        if len(p) < 2:
            return []
        out = []
        for a, b in ((p[1], p[0]), (p[-2], p[-1])):
            v = b - a
            n = float(np.hypot(*v))
            if n > 0:
                out.append((b, v / n))
        return out

    src_pts, src_dirs = [], []
    for sid in labeled:
        for c in by_sid.get(sid, ()):
            for pt, tv in _ends(c):
                src_pts.append(pt)
                src_dirs.append((sid, tv))
    if not src_pts:
        return
    tree = cKDTree(np.asarray(src_pts))

    joints: dict = defaultdict(int)
    for sid, cs in by_sid.items():
        if sid in labeled or not cs or cs[0].klass == "annotation":
            continue
        for c in cs:
            # count per END, up to two per chain: a fragment whose BOTH ends
            # continue into the labeled family is sandwiched inside the same
            # physical line - the strongest evidence, not double counting
            for pt, tv in _ends(c):
                for k in tree.query_ball_point(pt, 12.0):
                    ssid, stv = src_dirs[k]
                    if float(tv @ stv) < -0.6:
                        joints[(sid, ssid)] += 1
                        break

    import re as _re

    def _dg(c):
        m = _re.match(r"d([\d.]+) g([\d.]+)", c.dash_signature or "")
        return (float(m.group(1)), float(m.group(2))) if m else None

    def _sparse_kin(cs, ssid):
        """Same physical linetype read one-dash-sparse by the chainer.

        Res-A C3.0: existing contours are d9.0 g4.5 (period 13.5); their broken
        siblings read d9.0 g44.3 - the same 9pt dashes with the gap spanning
        an integer number of periods minus one dash ((44.3+9.0)/13.5 = 3.95).
        Demands: dash within 10%, color within 0.1, and gap+dash an integer
        multiple (2..6) of the parent period within 15%. This is cadence
        arithmetic, not proximity - a different linetype cannot satisfy it by
        accident and a generic solid pool has no cadence at all.
        """
        a, b = (by_sid[ssid] or [None])[0], cs[0]
        if a is None or not a.klass.startswith("dash")                 or not b.klass.startswith("dash"):
            return False
        da, db = _dg(a), _dg(b)
        if not da or not db:
            return False
        if abs(da[0] - db[0]) > 0.10 * max(da[0], db[0]):
            return False
        if a.color and b.color and                 max(abs(x - y) for x, y in zip(a.color, b.color)) > 0.10:
            return False
        period = da[0] + da[1]
        k = (db[1] + db[0]) / period
        return 2.0 <= k <= 6.0 and abs(k - round(k)) <= 0.15

    for (sid, ssid), n in sorted(joints.items(), key=lambda kv: -kv[1]):
        if sid in style_labels:
            continue
        cs = by_sid[sid]
        if n < 2:
            continue
        if n < 0.25 * len(cs) and not _sparse_kin(cs, ssid):
            continue
        a = cs[0].color
        b = (by_sid[ssid] or [None])[0]
        if a and b is not None and b.color and                 max(abs(x - y) for x, y in zip(a, b.color)) > 0.15:
            continue
        _assign_label(style_labels, sid, style_labels[ssid])

    # KIN BY CADENCE, no joints needed - two shapes measured on substation C-04
    # (2026-09-26 audit, "6 of 6 named" and still 22.8 % of the adjacent
    # property line and every index contour unnamed):
    #  * SCALED KIN: the same linetype plotted at a multiple of its scale.
    #    Index contours are the regular contour's pen at twice the LTSCALE
    #    (d18 g9 over d9 g4.5, 13,599 pt on C-04): dash AND gap both k x the
    #    named style's (k = 2 or 3), same pen, and both families curvy
    #    (contours bend; a straight border at 2x cadence is not kin).
    #  * COMPOUND KIN: a compound linetype (two or more dash classes) whose
    #    chain lost some marks reads with the same dash classes and a gap
    #    that spans whole periods - the ADJACENT PROPERTY LINE's d9 d42.9
    #    g63.0 variant beside the named d9 d45 g9 (63 + 9 = 4 x 18). Dash
    #    classes within 10 %, same pen and weight class, an integer period
    #    count within 15 %.
    def _vec(c):
        ds, gs = [], []
        for tok in (c.dash_signature or "").split():
            try:
                if tok[0] == "d":
                    ds.append(float(tok[1:]))
                elif tok[0] == "g":
                    gs.append(float(tok[1:]))
            except (ValueError, IndexError):
                return None
        if not ds or not gs:
            return None
        return sorted(ds), gs[0]

    from .chain import _sample_polyline

    def _samples(cs):
        out = []
        for c in cs:
            s, _ = _sample_polyline(np.asarray(c.pts, float), 4.0, 100000)
            if len(s):
                out.append(s)
        return np.vstack(out) if out else np.zeros((0, 2))

    _ptree: dict = {}

    def _among(cs, ssid):
        """The child runs AMONG the parent's lines (index contours sit between
        regular contours): median distance from the child's samples to the
        parent's within 80 pt, four fifths within 160 pt. A border drawn at
        twice the contour cadence sits at the edge of the field and fails.
        Measured on substation C-04: the thin index family's median is 56 pt,
        p80 108 pt, against the regular contours."""
        t = _ptree.get(ssid)
        if t is None:
            ps = _samples(by_sid.get(ssid) or [])
            t = cKDTree(ps) if len(ps) else False
            _ptree[ssid] = t
        if t is False:
            return False
        s = _samples(cs)
        if not len(s):
            return False
        d, _ = t.query(s)
        return float(np.median(d)) <= 80.0 and float(np.percentile(d, 80)) <= 160.0

    parents = []
    for ssid in list(style_labels):
        cs = by_sid.get(ssid) or []
        if not cs or not cs[0].klass.startswith("dash") or cs[0].klass == "dash-text":
            continue
        rep = max(cs, key=lambda c: c.length)
        v = _vec(rep)
        if v:
            parents.append((ssid, rep, v))
    for sid, cs in sorted(by_sid.items()):
        if sid in style_labels or not cs:
            continue
        rep = max(cs, key=lambda c: c.length)
        if not rep.klass.startswith("dash") or rep.klass == "dash-text":
            continue
        v = _vec(rep)
        if not v:
            continue
        ds, g = v
        for ssid, prep, (pds, pg) in parents:
            if not (rep.color and prep.color and
                    max(abs(x - y) for x, y in zip(rep.color, prep.color)) <= LEGEND_COLOR_TOL):
                continue
            if abs(_weight_class(rep.width) - _weight_class(prep.width)) > 1:
                continue
            hit = False
            if len(ds) == 1 and len(pds) == 1 and pds[0] > 0 and pg > 0:
                k = ds[0] / pds[0]
                kr = int(round(k))
                if kr in (2, 3) and abs(k - kr) <= 0.10 * kr \
                        and abs(g / pg - kr) <= 0.25 * kr and _among(cs, ssid):
                    hit = True
            elif len(ds) >= 2 and len(ds) == len(pds):
                if all(abs(a - b) <= 0.10 * max(a, b) for a, b in zip(ds, pds)):
                    period = pds[0] + pg
                    if period > 0:
                        n = (g + ds[0]) / period
                        if 1.0 <= n <= 6.0 and abs(n - round(n)) <= 0.15:
                            hit = True
            if hit:
                _assign_label(style_labels, sid, style_labels[ssid])
                break


def read_legend_sheet(words: list, chains: list) -> list:
    """Read a DEDICATED legend sheet (e.g. "C1.1 LEGEND & NOTES").

    Different problem from an inline legend box: here the legend is a tall
    column running most of the sheet height with dozens of rows, often with
    separate PROPOSED and EXISTING sample columns per label. So this does
    not anchor on the word "LEGEND" (which also appears in notes, and whose
    420pt neighbourhood misses most of the column). It anchors on the
    structure instead: many label rows, stacked vertically, each with a flat
    horizontal sample bar in a left-aligned strip beside it.

    Returns fingerprints for cross-sheet matching - the style ids here are
    meaningless on other sheets, so what transfers is (class, colour,
    weight, cadence).
    """
    rows = defaultdict(list)
    for w in words:
        t = str(w[4]).strip()
        if not t:
            continue
        rows[round(((w[1] + w[3]) / 2) / ROW_TOL)].append(w)

    cands = []
    for _, ws in sorted(rows.items()):
        ws.sort(key=lambda w: w[0])
        kept = []
        for w in ws:
            t = str(w[4]).strip()
            dup = any(str(k[4]).strip().upper() == t.upper()
                      and min(w[2], k[2]) - max(w[0], k[0]) > 0.5 * (w[2] - w[0])
                      for k in kept)
            if not dup:
                kept.append(w)
        # A legend sheet is laid out in COLUMNS - notes on the left, legend
        # on the right - so a page-wide row mixes them. Split the row where
        # a wide horizontal gap separates columns, or the "label" becomes a
        # note sentence and the sample strip is searched hundreds of pt away
        # from the actual legend.
        runs, run = [], [kept[0]]
        for prev, w in zip(kept, kept[1:]):
            if w[0] - prev[2] > 34.0:
                runs.append(run)
                run = [w]
            else:
                run.append(w)
        runs.append(run)

        for ri, rw in enumerate(runs):
            label = " ".join(str(w[4]).strip() for w in rw).strip()
            if not (3 <= len(label) <= 48):
                continue
            if not any(ch.isalpha() for ch in label):
                continue
            x0 = rw[0][0]
            x1 = rw[-1][2]
            cy = float(np.mean([(w[1] + w[3]) / 2 for w in rw]))
            def _wordy(lo, hi):
                # a sample cell holds at most a short inline token (- TV -);
                # several words there means prose (an abbreviations table's
                # definition column), never a line sample
                n = 0
                for ww in words:
                    wx, wy = (ww[0] + ww[2]) / 2, (ww[1] + ww[3]) / 2
                    if lo <= wx <= hi and abs(wy - cy) <= ROW_TOL:
                        n += 1
                return n > 2
            found = None
            for strip_w in (SAMPLE_W, SAMPLE_W * 0.45):
                if _wordy(x0 - strip_w, x0 - 4.0):
                    continue
                found = _sample_in_strip(chains, x0 - strip_w, x0 - 4.0, cy,
                                         min_span=30.0)
                if found is not None:
                    break
            if found is None:
                # TABLE legends (DESCRIPTION | EXISTING | PROPOSED) put the
                # sample to the RIGHT of the label; bound the strip by the
                # next run on the row so a second table column's label can
                # never be read as this row's sample
                lim = runs[ri + 1][0][0] - 6.0 if ri + 1 < len(runs)                     else x1 + SAMPLE_W
                hi = min(x1 + SAMPLE_W, lim)
                if hi - x1 >= 24.0 and not _wordy(x1 + 4.0, hi):
                    found = _sample_in_strip(chains, x1 + 4.0, hi, cy,
                                             min_span=min(30.0, 0.5 * (hi - x1)))
            if found is not None:
                cands.append({"label": label, "x0": x0, "cy": cy, **found})

    if len(cands) < 8:
        return []  # a handful of bar-beside-text rows is a table, not a legend

    # a legend column is left-aligned: keep every label-x cluster with
    # enough rows to be a real column (multi-column TABLE legends have
    # several), so stray bar-beside-text rows elsewhere cannot join in
    keep, used = [], set()
    for c in cands:
        if id(c) in used:
            continue
        near = [d for d in cands if abs(d["x0"] - c["x0"]) <= 26.0]
        if len(near) >= 8:
            for d in near:
                if id(d) not in used:
                    used.add(id(d))
                    keep.append(d)
    if len(keep) < 8:
        return []
    out, seen = [], set()
    for c in sorted(keep, key=lambda c: c["cy"]):
        key = (c["label"].upper(), round(c["cy"] / 4))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


# Cross-sheet AUTO-LABELLING BY GEOMETRY WAS TRIED AND REJECTED.
# Matching a plan style to a legend row on (class, weight, colour, cadence)
# looks reasonable and is wrong often enough to be dangerous. Scored against
# the CAD layer names as ground truth it put "STORM SEWER" on P1-FENCE and
# "SANITARY SEWER" on the water, gas, firewater and storm-swale runs - the
# legend holds exactly one dash-text row, so every dash-text style on the
# plan matched it. Tightening to rows with a legend-unique fingerprint made
# it worse (18 labels, ~10 wrong), because legend-side uniqueness does not
# make the plan's line that feature.
#
# What separates GAS LINE from WATER LINE is the letter printed inline, and
# an exploded glyph does not yield the letter. So the legend sheet is used as
# a VOCABULARY the reviewer picks from (read_legend_sheet above), never as an
# automatic label - a confident wrong trade name on billable footage is the
# one failure the estimator cannot catch by eye.


def _sample_from_units(units: list, x0: float, x1: float, cy: float):
    """Cadence of a legend swatch read straight from its raw marks."""
    # A mark contributes only if it LIVES in the strip - >=95% of its points
    # inside the (slightly widened) box - not merely if its centroid lands
    # here. The old centroid test admitted plan strokes CROSSING the legend
    # region: on Res-A C3.0 the strips beside the spot-elevation table and the
    # grading notes hold fragments of the grey existing-contour family, and
    # their cadence read as a plausible dash swatch, which is how
    # "DRAINAGE DIRECTION/SLOPE 2.0%" and a numbered note came to label
    # 87,441 pt of contours. A drawn swatch mark is always fully local - it
    # exists to sit beside its label.
    def _local(u):
        m = (u.pts[:, 0] >= x0 - 4.0) & (u.pts[:, 0] <= x1 + 4.0)             & (np.abs(u.pts[:, 1] - cy) <= SAMPLE_H + 4.0)
        return float(m.mean()) >= 0.95
    strip = [u for u in units
             if x0 < float(u.pts[:, 0].mean()) < x1
             and abs(float(u.pts[:, 1].mean()) - cy) <= SAMPLE_H
             and u.length >= 1.0 and not u.closed and _local(u)]
    if len(strip) < 3:
        return None
    # A linetype swatch is a HANDFUL of bars - the corpus's genuine samples
    # run 3-15 marks over a 30-110pt strip. A strip holding dozens of
    # sub-2pt marks is a TEXTURE field: on Res-A C3.0 the strips beside the
    # notes column hold 300+ dots of SE-P-GRAVEL HATCH stipple at ~1.6pt
    # each, and their "cadence" (avg 2.1) matched the grey contour family,
    # which is how "DRAINAGE DIRECTION/SLOPE 2.0%" came to name 105 chains
    # of existing contours. Texture is what the junk pruner exists to
    # remove from chains; the raw-unit path needs the same refusal.
    if len(strip) > 24:
        return None
    _lens0 = [float(u.length) for u in strip]
    if len(strip) >= 12 and float(np.median(_lens0)) < 2.0:
        return None
    strip.sort(key=lambda u: float(u.pts[:, 0].min()))
    lens = [float(u.length) for u in strip]
    gaps = []
    for a, b in zip(strip, strip[1:]):
        gaps.append(max(float(b.pts[:, 0].min() - a.pts[:, 0].max()), 0.0))
    span = float(strip[-1].pts[:, 0].max() - strip[0].pts[:, 0].min())
    if span < 30.0:
        return None
    klass, _sig = _classify_cadence(lens, [g for g in gaps if g > 0.5] or gaps)
    if not klass.startswith("dash"):
        # several distinct marks with real gaps IS a dash pattern even if
        # the classifier defaults solid on tiny samples
        klass = "dash2" if len(set(round(v / 4) for v in lens)) >= 2 else "dash1"
    wmax = max((u.width or 0.0) for u in strip)
    col = strip[int(np.argmax([u.length for u in strip]))].color
    tpl = []
    for k2, u in enumerate(strip):
        tpl.append(max(lens[k2], 1.0))
        if k2 < len(gaps):
            tpl.append(max(gaps[k2], 1.0))
    return {
        "klass": klass,
        "color": col,
        "weight": _weight_class(wmax),
        "avg_dash": float(np.median(lens)),
        "max_dash": float(max(lens)),
        "swatch_template": [round(v, 1) for v in tpl] if len(tpl) >= 2 else None,
        "sample_style": None,
    }


def _sample_in_strip(chains: list, x0: float, x1: float, cy: float,
                     min_span: float | None = None):
    """Characterize the linework inside the legend row's sample strip.
    A legend sample is a HORIZONTAL bar spanning most of the strip -
    leader lines and stray marks near note text don't qualify."""
    cands = []
    for c in chains:
        pts = np.asarray(c.pts)
        m = (pts[:, 0] >= x0) & (pts[:, 0] <= x1) \
            & (np.abs(pts[:, 1] - cy) <= SAMPLE_H)
        if m.mean() < 0.6 or m.sum() < 2:
            continue
        rise = float(pts[:, 1].max() - pts[:, 1].min())
        if rise > 8.0:
            continue
        cands.append(c)
    if not cands:
        return None
    # a sample bar may be broken into several same-style pieces (e.g. a
    # "PL" symbol interrupting the bar): span is judged per STYLE over the
    # union of its pieces, not per chain
    by_style = defaultdict(list)
    for c in cands:
        by_style[c.style_id].append(c)
    # A swatch bar is judged by its own plausible size, not by the strip's
    # width: legends that park labels far from the bars (substation p4) make the
    # strip wide, and 0.45x of wide rejected every genuine 38-98pt sample.
    need = min_span if min_span is not None else min(0.45 * (x1 - x0), 34.0)
    hits = []
    for cs in by_style.values():
        allx = np.concatenate([np.asarray(c.pts)[:, 0] for c in cs])
        if float(allx.max() - allx.min()) < need:
            continue
        hits.extend(cs)
    if not hits:
        return None
    # prefer real takeoff chains; an annotation-class hit (the swatch got
    # classified as a leader by the layerless pass) still gives cadence
    # but must not exact-join - its style is an annotation bucket
    real = [c for c in hits if c.klass != "annotation"]
    rep = max(real or hits, key=lambda c: c.length)
    # an annotation-class rep (the swatch itself got demoted) still tells
    # us the swatch's GEOMETRY: unbroken straight bar = solid linetype
    rep_klass = rep.klass
    if rep_klass == "annotation":
        rep_klass = "solid" if (rep.n_dashes or 0) <= 1 else "dash1"
    # the swatch's own cadence, marks in x order: what the sheet's legend
    # actually draws (paper-space) - the chip renders THIS, because the
    # matched style's absolute lengths differ under LTSCALE
    spans = []
    for c in hits:
        pts = np.asarray(c.pts)
        m = (pts[:, 0] >= x0) & (pts[:, 0] <= x1) \
            & (np.abs(pts[:, 1] - cy) <= SAMPLE_H)
        if m.any():
            xs = pts[m][:, 0]
            spans.append((float(xs.min()), float(xs.max())))
    spans.sort()
    tpl = []
    for k2, (a, b) in enumerate(spans):
        tpl.append(max(b - a, 1.0))
        if k2 + 1 < len(spans):
            tpl.append(max(spans[k2 + 1][0] - b, 1.0))
    if len(tpl) < 2 and getattr(rep, "template", None) \
            and len(rep.template) >= 2:
        # one chain = one x-span; the chain's own measured template IS the
        # swatch cadence (it is the swatch, drawn in paper space)
        tpl = list(rep.template)
    # How much of the winning chain actually lives in the strip. A drawn
    # swatch bar is FULLY contained (it exists to sit beside its label); a
    # fragment of plan linework crossing the legend region clears the 60%
    # gate above while its tail wanders off into the drawing. The caller
    # uses this to refuse exact joins from passers-by.
    _rp = np.asarray(rep.pts)
    _rm = (_rp[:, 0] >= x0 - 4.0) & (_rp[:, 0] <= x1 + 4.0)         & (np.abs(_rp[:, 1] - cy) <= SAMPLE_H + 4.0)
    if float(_rm.mean()) < 0.95:
        # the winner is a passer-by, not a drawn swatch: no sample at all
        # beats characterizing plan linework as a legend row's bar
        return None
    return {
        "klass": rep_klass,
        "color": rep.color,
        "contained": float(_rm.mean()),
        "weight": _weight_class(rep.width),
        "avg_dash": (rep.ink or rep.length) / max(rep.n_dashes, 1),
        "swatch_template": [round(v, 1) for v in tpl] if len(tpl) >= 2 else None,
        "sample_style": rep.style_id if rep.klass != "annotation" else None,
        # a compound swatch draws its symbol with several pens (Belle's
        # shear wall: hatch ticks + a native-dashed centerline). Every
        # qualifying style in the strip belongs to the definition, not
        # just the longest bar - the caller labels the distinctive ones.
        "co_styles": sorted({c.style_id for c in hits} - {rep.style_id}),
    }


def _style_matches(entry: dict, rep) -> bool:
    if entry["klass"] != rep.klass:
        return False
    if entry["weight"] != _weight_class(rep.width):
        return False
    ec, rc = entry["color"], rep.color
    if (ec is None) != (rc is None):
        return False
    if ec and max(abs(a - b) for a, b in zip(ec, rc)) > 0.15:
        return False
    if rep.klass.startswith("dash"):
        ad = (rep.ink or rep.length) / max(rep.n_dashes, 1)
        lo, hi = sorted([entry["avg_dash"], ad])
        if hi > 1.9 * lo + 3.0:
            return False
    return True
