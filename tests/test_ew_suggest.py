"""Earthwork suggest: surface split precedence + label-in-gap matching.

Covers the rules built for the C-04 overhaul (2026-08-31):
  a. legend verdicts outrank color, color outranks width, and a family the
     legend matched to a NON-contour row never seeds;
  b. white/near-white strokes never seed (invisible mask ink);
  c. a monochrome sheet falls through to the width-median split;
  d. inline-gap label matching - a native token inside a chain's own break
     is that chain's z, outranks nearest-label radius hits, and is consumed
     so no other chain can steal it (C-04's junk rows were exactly that
     theft); gap-OCR of the break outranks a radius hit beyond the on-line
     distance and wins an inline disagreement.

Deliberately dependency-free, same as tests/test_gate.py: pytest is not
installed here. Plain asserts run everywhere:

    python tests/test_ew_suggest.py

The C-04-derived fixtures embed real polyline points from the verified
result (job e183a0d44d89 engine page index 3, sig f74c853f3270c297), so no
plan corpus is needed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from takeoff_engine.earthwork import (  # noqa: E402
    _gap_regions,
    _ink_kind,
    family_verdicts,
    split_rows_surface,
    suggest_elevations,
)

import numpy as np  # noqa: E402


class _Chain:
    def __init__(self, cid, pts, klass="solid", layer="", width=0.5,
                 color=(0.0, 0.0, 0.0), style_id=None):
        self.chain_id = cid
        self.pts = np.asarray(pts, float)
        d = np.diff(self.pts, axis=0)
        self.length = float(np.hypot(d[:, 0], d[:, 1]).sum())
        self.klass = klass
        self.layer = layer
        self.width = width
        self.color = list(color)
        self.style_id = style_id


def _hline(cid, y, x0=0.0, x1=200.0, step=4.0, skip=None, **kw):
    """Horizontal polyline, optionally with a break (skip=(xa, xb))."""
    xs = list(np.arange(x0, x1 + step / 2, step))
    if skip:
        xs = [x for x in xs if not (skip[0] < x < skip[1])]
    return _Chain(cid, [[x, y] for x in xs], **kw)


def _word(x, y, text, w=14.0, h=8.0):
    return (x - w / 2, y - h / 2, x + w / 2, y + h / 2, text)


# ---- family_verdicts ------------------------------------------------------

def test_family_verdicts():
    styles = [
        {"style_id": 1, "suggested_label": "EXISTING CONTOUR (legend)"},
        {"style_id": 2, "suggested_label": "PROPOSED CONTOUR (legend)"},
        {"style_id": 3, "suggested_label": "PROPERTY LINE (legend)"},
        {"style_id": 4, "suggested_label": "PROPOSED SWALE (legend)"},
        {"style_id": 5, "suggested_label": "PROPOSED GRADE BREAK (legend)"},
        {"style_id": 6, "suggested_label": "MAJOR CONTOUR (legend)"},
        {"style_id": 7, "suggested_label": "dash1 d18.0 g9.0"},  # no legend
        {"style_id": 8, "suggested_label": "ADJACENT PROPERTY LINE (legend)"},
    ]
    v = family_verdicts(styles)
    assert v[1]["role"] == "existing"
    assert v[2]["role"] == "proposed"
    assert v[3]["role"] == "noncontour"
    assert v[4]["role"] == "noncontour"
    assert v[5]["role"] == "noncontour"
    assert v[6]["role"] == "contour"  # contour, surface unknown
    assert 7 not in v  # unmatched family falls through, no verdict
    assert v[8]["role"] == "noncontour"


# ---- surface split precedence --------------------------------------------

def test_split_legend_beats_color_beats_width():
    verdicts = family_verdicts([
        {"style_id": 1, "suggested_label": "EXISTING CONTOUR (legend)"},
        {"style_id": 2, "suggested_label": "PROPOSED CONTOUR (legend)"},
    ])
    # chain 10: legend says EXISTING even though it is drawn dark and fat -
    # the legend verdict must win over both color and width
    chain_info = {
        10: {"style_id": 1, "color": [0.0, 0.0, 0.0], "width": 3.0},
        11: {"style_id": 2, "color": [0.6, 0.6, 0.6], "width": 0.2},
        20: {"style_id": None, "color": [0.55, 0.55, 0.55], "width": 1.0},
        21: {"style_id": None, "color": [0.55, 0.55, 0.55], "width": 1.0},
        30: {"style_id": None, "color": [0.0, 0.0, 0.0], "width": 0.4},
        31: {"style_id": None, "color": [0.0, 0.0, 0.0], "width": 0.4},
        40: {"style_id": None, "color": [0.1, 0.2, 0.9], "width": 9.9},
    }
    rows = [{"chain_id": cid, "z": 100.0 + i, "width": chain_info[cid]["width"]}
            for i, cid in enumerate(chain_info)]
    counts = split_rows_surface(rows, chain_info, verdicts)
    by = {r["chain_id"]: r for r in rows}
    assert by[10]["surface"] == "existing" and by[10]["split_by"] == "legend"
    assert by[11]["surface"] == "proposed" and by[11]["split_by"] == "legend"
    # both ink populations exist (2 gray, 2 dark) -> color screening runs
    assert by[20]["surface"] == "existing" and by[20]["split_by"] == "color"
    assert by[21]["surface"] == "existing" and by[21]["split_by"] == "color"
    assert by[30]["surface"] == "proposed" and by[30]["split_by"] == "color"
    assert by[31]["surface"] == "proposed" and by[31]["split_by"] == "color"
    # a real color gets no screening verdict: falls to width (9.9 > median)
    assert by[40]["surface"] == "proposed" and by[40]["split_by"] == "width"
    # polarity: the ink rule ran the conventional way round, and nothing in
    # this legend (no inked EXISTING/PROPOSED family) could confirm or flip it
    assert counts == {"legend": 2, "color": 4, "width": 1,
                      "polarity": "convention"}


def test_split_monochrome_falls_to_width():
    # black-and-white sheet (the Barn case): no grays, no legend verdicts -
    # every row splits by the width median, exactly the UI's current rule
    chain_info = {
        1: {"style_id": None, "color": [0.0, 0.0, 0.0], "width": 0.5},
        2: {"style_id": None, "color": [0.0, 0.0, 0.0], "width": 0.5},
        3: {"style_id": None, "color": [0.0, 0.0, 0.0], "width": 1.5},
    }
    rows = [{"chain_id": cid, "z": 10.0 + cid,
             "width": chain_info[cid]["width"]} for cid in chain_info]
    counts = split_rows_surface(rows, chain_info, {})
    by = {r["chain_id"]: r for r in rows}
    assert counts == {"legend": 0, "color": 0, "width": 3,
                      "polarity": "convention"}
    assert by[1]["surface"] == "existing"   # 0.5 <= median 0.5
    assert by[2]["surface"] == "existing"
    assert by[3]["surface"] == "proposed"   # 1.5 > median


def test_ink_kind():
    assert _ink_kind([0.55, 0.55, 0.55]) == "gray"
    assert _ink_kind([1.0, 1.0, 1.0]) == "white"
    assert _ink_kind([0.0, 0.0, 0.0]) == "dark"
    assert _ink_kind([0.1, 0.2, 0.9]) is None   # a real color
    assert _ink_kind(None) is None


# ---- exclusion: white never seeds, and cannot steal ------------------------

def test_white_excluded_and_label_reaches_contour():
    # a fat white mask stroke sits closer to the label than the real
    # contour does; excluded, it must neither seed nor absorb the label
    white = _hline(1, 103.0, width=3.2, color=(1.0, 1.0, 1.0))
    contour = _hline(2, 112.0, width=0.5, color=(0.55, 0.55, 0.55))
    words = [_word(100.0, 101.0, "548")]
    rows = suggest_elevations([white, contour], words, exclude={1})
    by = {r["chain_id"]: r for r in rows}
    assert 1 not in by, "excluded white chain seeded a row"
    assert by[2]["z"] == 548.0, "label did not reach the real contour"


# ---- inline-gap matching ---------------------------------------------------

def _arc_chain(cid, r=200.0, c=(200.0, 300.0), a0=-60.0, a1=60.0,
               skip=None, **kw):
    """Curved polyline on a circle, optional angular break (skip in deg)."""
    pts = []
    for a in np.arange(a0, a1, 1.4):  # ~4.9 pt vertex spacing at r=200
        if skip and skip[0] < a < skip[1]:
            continue
        rad = np.radians(a)
        pts.append([c[0] + r * np.cos(rad), c[1] + r * np.sin(rad)])
    return _Chain(cid, pts, **kw)


def test_inline_gap_match_on_curved_chain():
    # break of ~7 deg (~24 pt of arc) with the token at its center
    arc = _arc_chain(5, skip=(-3.5, 3.5), width=0.5)
    mid = (400.0, 300.0)  # point of the circle at angle 0
    words = [_word(mid[0], mid[1], "575")]
    rows = suggest_elevations([arc], words)
    assert len(rows) == 1
    r = rows[0]
    assert r["chain_id"] == 5 and r["z"] == 575.0
    # dist for an inline row is its perpendicular distance to the break
    # chord - small for a label sitting on its own line
    assert r["how"] == "inline" and r["dist"] <= 6.0


def test_inline_theft_contour_wins_heavy_line_gets_nothing():
    # THE C-04 junk shape: one token, printed inside the contour's own
    # break, with a heavy line passing closer to the token than the
    # contour's nearest drawn point. Radius matching alone hands the
    # token to the heavy line; the inline pass must give it to the
    # contour and CONSUME it.
    contour = _hline(1, 100.0, skip=(88.0, 118.0), klass="dash1", width=0.5)
    heavy = _hline(2, 104.0, klass="solid", width=3.0)
    words = [_word(102.0, 100.0, "560")]
    rows = suggest_elevations([contour, heavy], words)
    by = {r["chain_id"]: r for r in rows}
    assert by.get(1, {}).get("z") == 560.0, "contour lost its inline label"
    assert by[1]["how"] == "inline"
    assert 2 not in by, "heavy line stole the inline token"


# ---- ranking: inline / gap-OCR / radius -----------------------------------

def test_on_line_radius_beats_gap_ocr():
    # a native token 2 pt from the polyline is printed ON the line; the
    # PDF's own glyphs outrank a raster OCR of the same neighbourhood
    line = _hline(3, 100.0)
    words = [_word(50.0, 102.0, "500")]
    gap_z = {3: (510.0, 60.0, 100.0)}
    rows = suggest_elevations([line], words, gap_z=gap_z)
    assert rows[0]["z"] == 500.0 and rows[0]["how"] == "radius"


def test_gap_ocr_beats_far_radius():
    line = _hline(4, 100.0)
    words = [_word(50.0, 112.0, "500")]  # 12 pt away: near, not on-line
    gap_z = {4: (510.0, 60.0, 100.0)}
    rows = suggest_elevations([line], words, gap_z=gap_z)
    assert rows[0]["z"] == 510.0 and rows[0]["how"] == "gap-ocr"


def test_gap_ocr_wins_inline_disagreement_keeps_inline_agreement():
    # structurally different value: the OCR shows what the raster holds
    # inside the break; the inline token there was a stolen spot value
    # on C-04 (565.2-style over an exploded 569 label)
    contour = _hline(6, 100.0, skip=(88.0, 118.0))
    words = [_word(102.0, 100.0, "570.3")]
    rows = suggest_elevations([contour], words,
                              gap_z={6: (570.0, 102.0, 100.0)})
    assert rows[0]["z"] == 570.0 and rows[0]["how"] == "gap-ocr"
    # agreement: the native token stays (exact decimals survive)
    rows = suggest_elevations([contour], words,
                              gap_z={6: (570.3, 102.0, 100.0)})
    assert rows[0]["z"] == 570.3 and rows[0]["how"] == "inline"


def test_inline_beats_gap_ocr_misread_of_same_text():
    # equal length, one digit apart = the SAME text misread by the
    # raster pass (Barn C3.0: native 7725 in its own break, OCR 7715) -
    # the PDF's own glyphs win
    contour = _hline(7, 100.0, skip=(88.0, 118.0))
    words = [_word(102.0, 100.0, "7725")]
    rows = suggest_elevations([contour], words,
                              gap_z={7: (7715.0, 102.0, 100.0)})
    assert rows[0]["z"] == 7725.0 and rows[0]["how"] == "inline"


# ---- C-04-derived geometry fixtures ---------------------------------------

# chain 800 around its 567 label at ~(663, 877): the break is shredded into
# 14-18 pt sub-gaps by welded glyph stubs, and the run's total chord tops
# 110 pt - only the merge + per-step windows cover the label.
_C800 = [[733.98, 759.24], [733.74, 759.6], [722.7, 778.8], [721.68, 780.48],
         [711.36, 798.36], [709.56, 801.48], [699.96, 818.1], [697.5, 822.36],
         [688.62, 837.72], [685.44, 843.24], [677.34, 857.22], [673.44, 864.0],
         [666.12, 876.66], [661.44, 884.7], [654.9, 896.04], [649.56, 905.34],
         [643.74, 915.36], [588.72, 1010.58], [586.02, 1015.2],
         [584.58, 1017.72], [581.82, 1022.52], [580.44, 1024.8],
         [577.56, 1029.78], [576.36, 1031.94], [573.36, 1037.1]]

# chain 813's smooth curve digitised at a uniform ~11.3 pt vertex spacing:
# every jump would read as a "gap" under a fixed threshold, and v2's first
# cut merged the whole stretch into one bogus 150+ pt break
_C813 = [[1582.2, 1097.82], [1592.94, 1094.58], [1603.74, 1091.34],
         [1614.54, 1088.1], [1625.34, 1084.86], [1636.14, 1081.68],
         [1646.94, 1078.44], [1657.68, 1075.2], [1668.48, 1071.96],
         [1679.28, 1068.72], [1690.08, 1065.54], [1700.88, 1062.3],
         [1711.62, 1059.06], [1722.42, 1055.82], [1711.74, 1048.56],
         [1706.28, 1044.84], [1699.98, 1041.18]]


def test_c04_chain800_label_break_covered():
    regs = _gap_regions(np.asarray(_C800, float))
    assert regs, "chain 800 produced no regions at all"
    near = [r for r in regs
            if abs(r[0] - 663.0) < 25.0 and abs(r[1] - 877.0) < 25.0]
    assert near, ("no region covers the 567 label at (663,877); centers: "
                  + str([(round(r[0]), round(r[1])) for r in regs]))


def test_c04_chain813_uniform_curve_is_not_a_break():
    regs = _gap_regions(np.asarray(_C813, float))
    assert regs == [], ("a smooth curve read as label breaks: "
                        + str([(round(r[0]), round(r[1]), round(r[4]))
                               for r in regs]))


# ---- the tag printed WITH the number, and the no-default-provenance rule --

def test_radius_match_carries_the_sheets_own_spot_tag():
    """A TAGGED NUMBER IS NOT A CONTOUR LABEL, AND THE ROW HAS TO SAY SO.

    Measured live on Oncor Cloudcrest engine page index 3 = printed sheet
    C-04 (job e183a0d44d89, 2026-09-01): contour chain 168 came back
    z 551.2, how "radius", dist 2.7, label_xy (429.6, 733.0). Read straight
    out of the PDF at that point, the text is "551.2" with "BW" next to it
    on the same row and "556.1 TW" above. The sheet's OWN legend prints
    "BW 100.0  PROPOSED BOTTOM OF WALL ELEVATION" and "TW 100.0  PROPOSED
    TOP OF WALL ELEVATION" - so 551.2 is a retaining-wall spot height, and
    a "550" contour leader sits a few points further off. The chip showed a
    bare "EL 551.2" and nothing on screen said otherwise.

    GLOSSARY (read off this sheet's legend, not assumed): BW = bottom of
    wall, TW = top of wall. Both are SPOT heights, not contour labels.
    """
    contour = _hline(1, 100.0, klass="dash1", width=0.5)
    # the number 8 pt above the line, its tag printed just to the right -
    # the plan-body order, "551.2 BW"
    words = [_word(60.0, 108.0, "551.2"), _word(76.0, 108.0, "BW")]
    rows = suggest_elevations([contour], words)
    assert len(rows) == 1, rows
    r = rows[0]
    assert r["how"] == "radius", r
    assert r["label_tag"] == "BW", f"the sheet's own tag was dropped: {r}"


def test_the_legend_order_of_tag_and_number_is_read_too():
    # C-04's legend writes the pair the other way round: "BW 100.0".
    contour = _hline(1, 100.0, klass="dash1", width=0.5)
    words = [_word(76.0, 108.0, "551.2"), _word(60.0, 108.0, "TW")]
    rows = suggest_elevations([contour], words)
    assert rows[0]["label_tag"] == "TW", rows


def test_an_untagged_number_reports_no_tag_rather_than_a_guess():
    contour = _hline(1, 100.0, klass="dash1", width=0.5)
    words = [_word(60.0, 108.0, "551")]
    rows = suggest_elevations([contour], words)
    assert rows[0]["label_tag"] is None, rows


def test_a_tag_on_another_text_row_is_not_borrowed():
    # "556.1 TW" sits 20 pt above "551.2" on C-04; the rows must not merge.
    contour = _hline(1, 100.0, klass="dash1", width=0.5)
    words = [_word(60.0, 108.0, "551.2"), _word(76.0, 128.0, "TW")]
    rows = suggest_elevations([contour], words)
    assert rows[0]["label_tag"] is None, rows


def test_an_inherited_row_never_reports_a_distance_it_measured():
    # dist 999 is a SENTINEL: no label on that chain at all. It has to stay
    # distinguishable from a measurement, and `how` is what says so - a real
    # radius match can never exceed max_d_pt = 16.0.
    a = _hline(1, 100.0, x0=0.0, x1=100.0, klass="dash1", width=0.5)
    b = _hline(2, 100.0, x0=130.0, x1=240.0, klass="dash1", width=0.5)
    words = [_word(50.0, 104.0, "560")]
    rows = {r["chain_id"]: r for r in suggest_elevations([a, b], words)}
    assert rows[2]["how"] == "inherited", rows
    assert rows[2]["dist"] == 999.0, rows
    assert rows[1]["dist"] < 16.0, rows


def test_a_row_with_no_recorded_provenance_abstains_instead_of_saying_radius():
    """THE LATENT DEFECT, KILLED AT THE ROOT.

    The emit read `how.get(cid, "radius")`, so any future writer to `seen`
    that forgot to set `how` would be reported as a printed label - and the
    UI's abstain branch could never fire for that class of row, because the
    server's default is the opposite of abstaining. `_put` is now the only
    writer and takes `kind` positionally, so the omission is unwritable; if
    the invariant is ever broken anyway, the emit must say "unknown".
    """
    import inspect
    import re

    from takeoff_engine import earthwork as ew

    src = inspect.getsource(ew.suggest_elevations)
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert '"how": how.get(cid, "radius")' not in code, \
        "the emit still defaults an unrecorded provenance to a printed one"
    assert 'how.get(cid) or "unknown"' in code, code[-400:]
    # and every ASSIGNMENT to `seen` goes through the one writer that
    # records the provenance alongside it
    writes = [ln.strip() for ln in code.splitlines()
              if re.search(r"\bseen\[[^\]]*\]\s*=[^=]", ln)]
    assert len(writes) == 1 and writes[0].startswith("seen[cid] ="), writes
    assert "def _put(cid, d, v, cx, cy, kind" in code, \
        "`kind` must stay positional so it cannot be forgotten"


def test_printed_how_list_matches_what_the_reader_can_emit():
    from takeoff_engine.earthwork import PRINTED_HOWS, is_printed_how

    assert set(PRINTED_HOWS) == {"radius", "inline", "gap-ocr"}
    assert is_printed_how("gap-ocr") and not is_printed_how("inherited")
    assert not is_printed_how("interval-infill")
    assert not is_printed_how(None) and not is_printed_how("unknown")


def _main():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
    print(f"{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
