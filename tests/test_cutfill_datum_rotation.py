"""Cut/fill reader: datum note forms, truncated civil spot grades, bare integers, and text-layer
labels on a rotated page (the audit's blockers B1 / D12 / D33)."""
import os
import sys

import pymupdf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cutfill.labels import make_label, parse_text, resolve_values  # noqa: E402
from cutfill.ingest import Document  # noqa: E402


def test_datum_forms():
    for text, real, arch in [("[100'=7732']", 7732.0, False), ("ARCH. 100' = CIVIL 7732.0'", 7732.0, True),
                             ("NOTE: ARCHITECTURAL 100' = CIVIL 7923.0'", 7923.0, True), ("100.00 = 8800.00", 8800.0, False)]:
        r = parse_text(text)
        assert r["kind"] == "datum", text
        assert r["datum"] == (100.0, real), text
        assert r["datum_arch"] is arch, text
    assert parse_text('SCALE: 1"=50\'')["kind"] == "scale"
    assert parse_text("7605.25 -> 05.25) AND DO NOT REFERENCE ARCHITECTURAL ELEVATIONS.")["truncated_note"]
    assert parse_text("ALL SPOT ELEVATIONS ILLUSTRATED HEREIN ARE CIVIL ELEVATIONS AND DO NOT CORRELATE TO ARCH. ELEVATIONS.")["truncated_note"]


def _lab(i, text):
    return make_label(i, 0, text, (0.0, 0.0, 20.0, 8.0), 0.0, "text", 100.0, 0.0)


def test_truncated_spots_take_the_base_not_the_datum():
    labs = [_lab(0, "FG: 48.07"), _lab(1, "TW: 35.00"), _lab(2, "FFE: 32.16"), _lab(3, "10"), _lab(4, "0")]
    s = resolve_values(labs, datum=(100.0, 7732.0), contour_range=None, abbrev_base=7700.0, datum_off=True)
    assert labs[0].z == 7748.07 and labs[1].z == 7735.0 and labs[2].z == 7732.16
    assert labs[3].z is None and labs[4].z is None          # bare integers are not elevations
    assert s["abbreviated"] == 3 and s["datum_applied"] == 0
    # the same labels with the architectural datum applied blindly (the old behaviour) would sit 68 ft low
    labs2 = [_lab(0, "FG: 48.07")]
    resolve_values(labs2, datum=(100.0, 7732.0), contour_range=None)
    assert labs2[0].z == 48.07 + 7632.0


def test_project_datum_still_applies_when_the_sheet_says_nothing():
    labs = [_lab(0, "87.50"), _lab(1, "100.50")]
    s = resolve_values(labs, datum=(100.0, 8800.0), contour_range=None)
    assert labs[0].z == 8787.5 and labs[1].z == 8800.5 and s["datum_applied"] == 2


def test_text_lines_follow_the_page_rotation(tmp_path=None):
    import tempfile
    d = tempfile.mkdtemp()
    path = os.path.join(d, "rot.pdf")
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    page.draw_line((50, 100), (150, 100), color=(0, 0, 0), width=1)    # a line, unrotated frame
    page.insert_text((60, 95), "TW: 35.00", fontsize=8)                  # its label just above it
    page.set_rotation(270)
    doc.save(path); doc.close()
    dd = Document(path)
    strokes = dd.strokes(0)
    lines = dd.text_lines(0)
    assert strokes and lines
    tl = [t for t in lines if "35.00" in t.text][0]
    lx0, ly0, lx1, ly1 = tl.bbox
    sx = [p[0] for s in strokes for p in s.pts]; sy = [p[1] for s in strokes for p in s.pts]
    # the label's box lies within 30 pt of the line in the SAME (rotated) frame
    assert min(abs(lx0 - min(sx)), abs(lx1 - max(sx))) < 60 and min(abs(ly0 - min(sy)), abs(ly1 - max(sy))) < 30, (tl.bbox, (min(sx), min(sy), max(sx), max(sy)))
    # the writing direction turned with the page
    assert abs(tl.dir[0]) < 0.01 and abs(abs(tl.dir[1]) - 1.0) < 0.01, tl.dir


if __name__ == "__main__":
    test_datum_forms()
    test_truncated_spots_take_the_base_not_the_datum()
    test_project_datum_still_applies_when_the_sheet_says_nothing()
    test_text_lines_follow_the_page_rotation()
    print("ok")
