"""How a counted bubble gets its NAME, kept apart from the detector.

takeoff_engine/marks.py finds and counts the bubbles and is one of the
files engine_sig fingerprints: a change there marks every cached detection
stale and moves the chain gate. Naming is governed by server.SYMBOLS_ALGO
instead (the symbol caches), and everything here changes only names:

  * read_scanned_schedule - marks.read_mark_schedule's harvest for a
    SCANNED schedule, with the close read of a cell the page read garbled
    and of a row it never saw (read_cell / column_cells), a prose refusal
    (_marks_are_a_family) and the family rule for re-read marks
    (_fits_family). Measured on the apartment set's window schedule: the page
    read yields W1L / W1R / W4 / W5, the close read adds W2L and W3L, the
    six rows the MARK column prints;
  * read_by_grammar - a bubble's code picture read glyph by glyph under
    the schedule's own shape, accepted only on an exact vocabulary match;
  * finish_naming - the post-pass the route runs after detect_marks: the
    grammar read for groups the word read left unnamed, and - when the set
    has no schedule at all - a name read off the tag itself, flagged so the
    panel and the rollup say so.

Every function copied from marks.py is named in its own docstring.
"""
from __future__ import annotations

import re

import cv2
import numpy as np

from . import marks
from .marks import (_MARK_HEADS, _NUM_HEADS, _QTY_WEAK, _TYPE_PREFIX, _as_mark, _clean,
                    _is_qty_head, _row_tol, _schedule_title, _txt)

_TAG_GRAMMAR = re.compile(r"^(?:[A-Z]{1,3}\d{1,3}[A-Z]?|[A-Z]{1,2}|\d{2,3})$")


def read_cell(img: np.ndarray, word, px_per_pt: float, debug: dict | None = None) -> list[str]:
    """The close read of one scanned table cell: the word's own box off the
    upright page picture, padded a little, the table's rules taken out,
    read at 4x by three page-segmentation modes, free and with the mark
    alphabet as a whitelist. Returns the distinct cleaned strings, longest
    first; the caller decides which is a mark.

    Why the rules come out: the page-wide OCR reads a 12 px "W2L" with the
    row's horizontal rule an inch above it as "/ wat" - the rule is read as
    a slash and the glyphs as prose. A rule is a run of ink far longer than
    any glyph; a morphological opening with a long horizontal (then
    vertical) kernel isolates exactly those runs and they are painted out.
    `word` is a (x0, y0, x1, y1, text, ...) tuple in page points."""
    try:
        import pytesseract
    except Exception:
        return []
    if img is None or img.size == 0:
        return []
    H, W = img.shape[:2]
    pad = 3.0
    x0 = max(0, int((float(word[0]) - pad) * px_per_pt))
    y0 = max(0, int((float(word[1]) - pad) * px_per_pt))
    x1 = min(W, int((float(word[2]) + pad) * px_per_pt))
    y1 = min(H, int((float(word[3]) + pad) * px_per_pt))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return []
    cell = img[y0:y1, x0:x1]
    g = cv2.cvtColor(cell, cv2.COLOR_RGB2GRAY) if cell.ndim == 3 else cell
    _, ink = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # RULES OUT. A rule crosses the whole cell; a glyph covers under half of
    # it in either direction. Rows (and columns) whose ink, banded a few
    # pixels, spans more than half the crop are rules - on a skewed scan a
    # rule drops a few rows across the cell, which a straight opening kernel
    # broke into stumps that then counted as glyphs.
    clean = ink.copy()
    ch, cw = ink.shape
    rb = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 5)))
    rows = np.where((rb > 0).sum(axis=1) > 0.5 * cw)[0]
    for r in rows:
        clean[max(0, r - 2):r + 3, :] = 0
    cb = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 1)))
    cols = np.where((cb > 0).sum(axis=0) > 0.5 * ch)[0]
    for c in cols:
        clean[:, max(0, c - 2):c + 3] = 0
    big = cv2.resize(255 - clean, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    big = cv2.copyMakeBorder(big, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
    out: list[str] = []
    for psm in ("7", "8", "13"):
        for wl in ("", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"):
            cfg = f"--psm {psm}" + (f" -c tessedit_char_whitelist={wl}" if wl else "")
            try:
                raw = pytesseract.image_to_string(big, config=cfg)
            except Exception:
                continue
            t = _clean(raw)
            if t and t not in out:
                out.append(t)
    # GLYPH BY GLYPH. Read as a word, this scan's "2" comes back "A" at
    # every scale ("W2L" -> WAT / WA / WL, measured on the apartment set's
    # schedule). The cell's ink splits into its glyph blobs; each is read
    # on its own as a single character (psm 10), letters or digits, and the
    # reads are strung together in x order. A blob narrower than a fifth
    # of the tallest one is a speck (a dot, a rule stump) and is skipped.
    n_lab, lab, stats, _c = cv2.connectedComponentsWithStats(clean, connectivity=8)
    blobs = []
    for i in range(1, n_lab):
        x, y, w, h, a = (int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                         int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT]),
                         int(stats[i, cv2.CC_STAT_AREA]))
        if h >= 4 and a >= 6:
            blobs.append((x, y, w, h, i))
    # specks first (rule stumps, a dot), THEN the count: a glyph stands at
    # least 40 % as tall as the tallest and a fifth as wide
    if blobs:
        tallest = max(b[3] for b in blobs)
        blobs = [b for b in blobs if b[3] >= 0.4 * tallest and b[2] >= 0.2 * tallest]
    # GLYPHS THAT TOUCH are one blob: Otsu on the cell crop bloats a 4.5 pt
    # stroke until the apartment set's whole "W2L" is one 68 x 30 px blob. A blob
    # wider than 1.15 x its height (W and M, the widest glyphs, are about
    # 1.05; a W at this size measured 1.18) is cut at the thinnest column
    # of its middle half, and the cutting repeats on the halves ("W2L" ->
    # "W" | "2L" -> "2" | "L"), a few passes at most. Geometry cannot tell
    # a wide W from two glyphs at this size (an erosion, a junction test on
    # ink count and one on ink position were each measured failing), so a
    # W cut in two is allowed to happen: its halves read as V and V, and
    # the caller's family rule refuses a mark whose head the page never
    # printed (see _harvest).
    if debug is not None:
        debug["pre_split"] = [(x, y, w, h) for x, y, w, h, i in blobs]
    for _pass in range(3):
        split: list = []
        cut_any = False
        for x, y, w, h, i in blobs:
            if w > 1.15 * h and w >= 12:
                sub = (lab[y:y + h, x:x + w] == i).astype(np.uint8)
                prof = sub.sum(axis=0)
                lo, hi = int(0.3 * w), int(0.7 * w)
                if hi > lo:
                    cut = lo + int(np.argmin(prof[lo:hi]))
                    split.append((x, y, cut, h, i))
                    split.append((x + cut, y, w - cut, h, i))
                    cut_any = True
                    continue
            split.append((x, y, w, h, i))
        blobs = split
        if not cut_any:
            break
    if 2 <= len(blobs) <= 5:
        blobs.sort(key=lambda b: b[0])
        reads: list[dict] = []          # per glyph: {"any": c, "digit": c, "letter": c}
        Hc, Wc = clean.shape
        for x, y, w, h, i in blobs:
            # the intact ink under this blob, re-grown by the pixel the
            # erosion took (a cut blob keeps its half of the label)
            m = np.zeros_like(clean)
            m[y:y + h, x:x + w] = (lab[y:y + h, x:x + w] == i).astype(np.uint8)
            m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
            bx0, by0 = max(0, x - 1), max(0, y - 1)
            bx1, by1 = min(Wc, x + w + 1), min(Hc, y + h + 1)
            g1 = np.where((m[by0:by1, bx0:bx1] > 0) & (clean[by0:by1, bx0:bx1] > 0), 0, 255).astype(np.uint8)
            g1 = cv2.resize(g1, None, fx=6, fy=6, interpolation=cv2.INTER_CUBIC)
            g1 = cv2.copyMakeBorder(g1, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)
            got: dict[str, str] = {}
            for key, wl in (("any", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
                            ("digit", "0123456789"), ("letter", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")):
                try:
                    raw = pytesseract.image_to_string(
                        g1, config=f"--psm 10 -c tessedit_char_whitelist={wl}")
                except Exception:
                    continue
                t = _clean(raw)
                if len(t) == 1:
                    got[key] = t
            reads.append(got)
        # the free composition, and the one the MARK GRAMMAR asks for:
        # letters, then a digit, then a suffix letter (W2L). Read free, this
        # scan's "2" is an "A" every time; asked for a digit it is a 2.
        free = "".join(r.get("any", "?") for r in reads)
        if "?" not in free and free and free not in out:
            out.append(free)
        if len(reads) in (2, 3):
            want = ["letter", "digit"] + (["letter"] if len(reads) == 3 else [])
            gram = "".join(r.get(k, "?") for r, k in zip(reads, want))
            if "?" not in gram and gram not in out:
                out.append(gram)
        if debug is not None:
            debug["blobs"] = [(x, y, w, h) for x, y, w, h, i in blobs]
            debug["reads"] = reads
    if debug is not None:
        debug["n_blobs"] = len(blobs)
        debug["clean"] = clean
    out.sort(key=lambda s: -len(s))
    return out


def column_cells(img: np.ndarray, hw, px_per_pt: float, reach_pt: float = 90.0) -> list:
    """The cells of a scanned table's first column, from the table's OWN
    RULES: the horizontal rules under the header word `hw`, in a strip the
    header's width, define the rows; each gap between consecutive rules
    (at least 6 pt tall) is a cell. Returns [(x0, y0, x1, y1), ...] in page
    points, top to bottom, stopping at the first gap more than twice the
    median row pitch (the table's end) or at `reach_pt` below the header.
    For a row whose page-level OCR produced no word at all (the apartment set's
    W3L: the only "word" there was a 1.3 pt fragment of the rule)."""
    if img is None or img.size == 0:
        return []
    H, W = img.shape[:2]
    x0 = max(0, int((float(hw[0]) - 10) * px_per_pt))
    x1 = min(W, int((float(hw[2]) + 10) * px_per_pt))
    y0 = max(0, int(float(hw[3]) * px_per_pt))
    y1 = min(H, int((float(hw[3]) + reach_pt) * px_per_pt))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return []
    strip = img[y0:y1, x0:x1]
    g = cv2.cvtColor(strip, cv2.COLOR_RGB2GRAY) if strip.ndim == 3 else strip
    _, ink = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # a rule on a skewed scan drops a few rows across the strip, so no one
    # row holds it whole: band the ink a few rows tall and take the rows
    # where the band covers most of the strip's width. Glyph rows never
    # do (a mark is a third of the cell wide).
    band = cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 7)))
    frac = (band > 0).sum(axis=1) / float(max(1, x1 - x0))
    # a mark's glyphs cover under half the strip; a rule, even faint, over
    # half (the rule between the apartment set's W3L and W4 rows sat at 0.6)
    rows = np.where(frac >= 0.55)[0]
    if rows.size == 0:
        return []
    # group consecutive rule rows into one rule each
    ys: list[float] = []
    start = prev = int(rows[0])
    for r in rows[1:]:
        r = int(r)
        if r - prev > 2:
            ys.append((start + prev) / 2.0)
            start = r
        prev = r
    ys.append((start + prev) / 2.0)
    cells: list = []
    pitches: list[float] = []
    for a, b in zip(ys, ys[1:]):
        gap = b - a
        if gap < 6 * px_per_pt:
            continue
        if pitches and gap > 2.0 * float(np.median(pitches)):
            break
        pitches.append(gap)
        cells.append((float(hw[0]) - 4, (y0 + a) / px_per_pt + 1.0,
                      float(hw[2]) + 4, (y0 + b) / px_per_pt - 1.0))
    return cells


def _fits_family(code: str, marks: list) -> bool:
    """A mark RE-READ off the picture may only fill a gap in the family the
    page read already established: it must share the family's leading
    letters (the majority head of `marks`), and carry a digit when the
    family's marks do. A W cut in two reads V | V and would otherwise enter
    as "VV" or "V2L"; a letters-only read of a W?L cell ("WL", "WA") would
    enter through the lookalike map as "W1". With fewer than two page-read
    marks there is no family to fit, and nothing is accepted."""
    ms = [m for m in marks if m]
    if len(ms) < 2 or not code:
        return False
    if all(m.isalpha() for m in ms):
        return code.isalpha() and len(code) <= 2      # a type-letter family: any letter
    if all(m.isdigit() for m in ms):
        return code.isdigit()                        # a door-number family: any number
    heads: dict = {}
    for m in ms:
        h = re.match(r"[A-Z]*", m).group(0)
        heads[h] = heads.get(h, 0) + 1
    head = max(heads, key=heads.get)
    if re.match(r"[A-Z]*", code).group(0) != head:
        return False
    if any(ch.isdigit() for m in ms for ch in m) and not any(ch.isdigit() for ch in code):
        return False
    return True


def _marks_are_a_family(marks: list) -> bool:
    """A schedule's mark column is a FAMILY: W1L / W1R / W2L / W3L / W4 / W5,
    D101 / D102, A / B / C, 1 / 2 / 3. OCR of a scanned sheet under the
    lookalike map turns prose into "marks" - a client's floor plans yielded
    AN0 / S0 / V8 / MU5T / HO0O / A1L / F0R from a TYPE-headed detail table
    (MUST, HOOD, FOR), seven marks with seven different heads - and a
    vocabulary of prose names nothing while claiming a schedule was read.
    A family shares its leading letter(s) on MORE than half of its marks
    (the apartment set's second head, WINDOW TYPE, yielded S0 / A0 / A / N0T off
    its description column - two of four with one head - and a bare half
    let it through), or is all bare letters, or all bare numbers."""
    ms = [m for m in marks if m]
    if len(ms) < 2:
        return True
    if all(m.isalpha() for m in ms) or all(m.isdigit() for m in ms):
        return True
    heads = {}
    for m in ms:
        h = re.match(r"[A-Z]*", m).group(0) or m[:1]
        heads[h] = heads.get(h, 0) + 1
    return max(heads.values()) * 2 > len(ms)


def read_scanned_schedule(words: list, y_tol: float = 5.0,
                          reread=None, cells=None) -> dict:
    fuzzy = True
    """Read a MARK/QTY (or WINDOW TYPE/QTY) schedule out of a page's words.

    Returns {"marks": [code, ...] in sheet order, "qty": {code: n},
             "box": (x0, y0, x1, y1) or None, "header": "MARK"|"WINDOW TYPE",
             "fuzzy": [raw, ...], "source": "text"|"ocr"|"none"}.

    QTY is OPTIONAL.  A schedule with no quantity column - Mission Bell's
    A-6.00 lists 22 readable window types and never says how many - is still
    worth reading, because the type list IS the legal vocabulary the bubbles
    must come from, and constraining the read to it is what stops OCR
    inventing marks at 12 px glyph height.

    `fuzzy` is for schedules that had to be OCR'd off a scan: the mark
    column comes back as WIL / WIR / WS rather than W1L / W1R / W5, so the
    same lookalike map used on the bubbles is applied here.  It is off by
    default because on a real text layer it can only do harm. `reread` is
    the per-cell close read for such a scan (see _harvest); an OCR'd table
    whose marks are not a family (_marks_are_a_family) is prose and is not
    a schedule.
    """
    out = {"marks": [], "qty": {}, "box": None, "header": "",
           "fuzzy": [], "source": "none"}
    if not words:
        return out
    cands = []
    for hw in words:
        t = _txt(hw)
        num_head = t in _NUM_HEADS
        if t not in _MARK_HEADS and not num_head:
            continue
        hy = (hw[1] + hw[3]) / 2.0
        hx = (hw[0] + hw[2]) / 2.0
        row = [w for w in words
               if abs((w[1] + w[3]) / 2.0 - hy)
               <= _row_tol(y_tol, (w[0] + w[2]) / 2.0 - hx)]
        row.sort(key=lambda w: w[0])
        texts = [_txt(w) for w in row]
        # TWO HEADS ON ONE ROW ARE TWO COLUMNS OF ONE TABLE, and the mark
        # column is the first: a head with another head to its left within
        # the table's width is a later column (apartment set: MARK then WINDOW
        # TYPE; the type column's descriptions read S01 / S5 off a scan and
        # were merged into the vocabulary as marks).
        if any(w is not hw and _txt(w) in _MARK_HEADS + _NUM_HEADS
               and w[2] < hw[0] and hw[0] - w[2] < 400 for w in row):
            continue
        # "WINDOW TYPE" / "DOOR TYPE": the qualifier sits immediately left
        header = t
        if t == "TYPE":
            j = texts.index(t)
            if j and texts[j - 1] in _TYPE_PREFIX:
                header = texts[j - 1] + " TYPE"
            elif len(set(texts)) < 3:
                continue        # a bare "TYPE" with no table around it
        if num_head:
            # only with a real quantity column beside it, and it must be
            # the first column: "DOOR NO." mid-row is not a mark head
            if not any(_is_qty_head(x) for x in texts if x != t):
                continue
            # the row match has no x limit (a skewed scan needs the slope
            # widening), so "row" holds every word on the sheet at this
            # height: only a NEIGHBOUR to the left disqualifies
            if any(w is not hw and w[2] < hw[0] - 1 and hw[0] - w[2] < 60 for w in row):
                continue
            header = _schedule_title(words, hw, y_tol) + " NO."
        qcol = None
        for w in row:
            if _is_qty_head(_txt(w)):
                qcol = w
                break
        if qcol is None:
            for w in row:
                if _txt(w).rstrip(".") in [q.rstrip(".") for q in _QTY_WEAK]:
                    qcol = w
                    break
        # THE NEXT TABLE DOWN ENDS THIS ONE. Two schedules stacked in one
        # corner share a mark column x, and without a stop the upper one
        # harvested the lower one's marks as its own (house set A2.1: the
        # window table came back holding A-D as well as 1-3).
        y_stop = None
        for hw2 in words:
            if hw2 is hw or _txt(hw2) not in (_MARK_HEADS + _NUM_HEADS):
                continue
            if hw2[1] > hw[3] and abs((hw2[0] + hw2[2]) / 2.0 - hx) <= 40:
                y_stop = hw2[1] if y_stop is None else min(y_stop, hw2[1])
        cand = _harvest(words, hw, qcol, row, header, y_tol, fuzzy,
                        digits_ok=num_head, y_stop=y_stop, reread=reread, cells=cells)
        if cand and fuzzy and not _marks_are_a_family(cand["marks"]):
            continue                # OCR'd prose wearing a mark's shape
        if cand:
            cands.append(cand)
    if not cands:
        return out
    cands.sort(key=lambda c: -len(c["marks"]))
    best = dict(cands[0])
    # TWO SCHEDULES ON ONE SHEET (windows 1/2/3 and doors A-D): when their
    # marks do not collide, the vocabulary and quantities are the union - a
    # bubble reading "2" and one reading "B" are both on this sheet. The
    # box stays the largest table's; `tables` carries each one.
    best["tables"] = [{"header": c["header"], "marks": list(c["marks"]),
                       "qty": dict(c["qty"]), "box": c["box"]} for c in cands]
    for c in cands[1:]:
        if set(c["marks"]) & set(best["marks"]):
            continue
        best["marks"] = best["marks"] + [m for m in c["marks"] if m not in best["marks"]]
        best["qty"] = {**c["qty"], **best["qty"]}
        best["header"] = best["header"] + " + " + c["header"]
    return best


def _harvest(words: list, hw, qcol, row: list, header: str,
             y_tol: float, fuzzy: bool, digits_ok: bool = False,
             y_stop: float | None = None, reread=None, cells=None) -> dict | None:
    """Rows under one header: mark codes in the mark column, qty beside;
    nothing at or below `y_stop` (the next table's header) is this table's.

    `reread(word)` -> [str, ...] is the CLOSE READ of one mark-column cell
    off the picture, for a scanned schedule: the page-wide OCR pass reads
    12 px glyphs one row at a time with the table's rules in the way, and
    The apartment set's W2L came back "WAT", its W3L "+?" - two of six rows lost,
    two window types the bubbles could then never be named as. A cell
    whose page read is no mark is read again on its own box; the first
    reading that IS a mark stands (the lookalike map still applies)."""
    mx0, mx1 = hw[0] - 14, hw[2] + 14
    # THE TABLE'S EXTENT IS ITS OWN. `row` is every word on the sheet at
    # the header's height (the skew-tolerant match has no x limit), so
    # taking the table's edges from it made the RV garage notes 1,300 pt
    # away "left of" the window marks and disqualified them (house set
    # A2.1). The mark column is the first column: nothing of the table is
    # more than 30 pt left of its header, or 700 pt right of it.
    rx0 = max(min(w[0] for w in row) - 8, hw[0] - 30)
    rx1 = min(max(w[2] for w in row) + 8, hw[2] + 700)
    qcx = (qcol[0] + qcol[2]) / 2.0 if qcol is not None else None
    # The table's own two headers give its row slope, so a quantity 700 pt
    # away is looked for where that row ACTUALLY runs rather than inside a
    # band wide enough to swallow its neighbours. Widening symmetrically
    # instead gave every mark on a client's schedule the same quantity (148).
    slope = 0.0
    if qcol is not None:
        hxc, hyc = (hw[0] + hw[2]) / 2.0, (hw[1] + hw[3]) / 2.0
        qyc = (qcol[1] + qcol[3]) / 2.0
        if abs(qcx - hxc) > 1.0:
            slope = (qyc - hyc) / (qcx - hxc)
    marks, raws, qty, y_max = [], [], {}, hw[3]
    letters_only = True
    garbled: list = []
    for w in words:
        # by CENTRE, not box top: a 12 pt row pitch with 11 pt word boxes
        # puts the first row's top above the header's bottom (house set:
        # window "1" was dropped and the table read as two marks)
        if (w[1] + w[3]) / 2.0 <= hw[3] or not (mx0 <= (w[0] + w[2]) / 2.0 <= mx1):
            continue
        if y_stop is not None and w[1] >= y_stop - 2:
            continue
        t = _txt(w)
        code = _as_mark(t, fuzzy, digits_ok)
        if code is None:
            # a cell the page read garbled ("WAT" for W2L): kept for the close
            # read below, once the page-read marks have named the family
            if reread is not None and fuzzy and t and t.isalnum():
                garbled.append(w)
            continue
        # the mark column is the table's FIRST column: nothing else on this
        # row may sit to its left.  Without this, a "WINDOW TYPE" heading
        # over a hardware column harvests prose ("OR", "OF", "IN", "IS")
        wy = (w[1] + w[3]) / 2.0
        # under a "No." header a single letter to the left counts too: the
        # door table's "38" (of 38 1/2") sits beside the mark "A"
        if any(m[2] < w[0] - 1 and abs((m[1] + m[3]) / 2.0 - wy) <= y_tol
               and rx0 <= m[0] and (len(_txt(m)) > 1 or digits_ok) and _txt(m).isalnum()
               for m in words):
            continue
        if len(code) > 1:
            letters_only = False
        if code in marks:
            continue
        marks.append(code)
        raws.append(t)
        y_max = max(y_max, w[3])
        if qcx is None:
            continue
        wxc = (w[0] + w[2]) / 2.0
        want = wy + slope * (qcx - wxc)      # where this row runs at qcx
        mates = [m for m in words
                 if abs((m[1] + m[3]) / 2.0 - want) <= y_tol
                 and _txt(m).isdigit() and rx0 <= m[0] and m[2] <= rx1]
        if mates:
            q = min(mates, key=lambda m: abs((m[0] + m[2]) / 2.0 - qcx))
            if abs((q[0] + q[2]) / 2.0 - qcx) <= 30.0:
                qty[code] = int(_txt(q))
    # THE CLOSE READ, once the page-read marks have named the family. Two
    # kinds of gap: a cell the page read garbled ("WAT" for W2L) and a row
    # it never saw at all (W3L: its only "word" was a 1.3 pt fragment of
    # the rule) - the latter found from the table's own rules (`cells`).
    # A candidate is read with the lookalike map OFF (the letters are
    # reliable at this scale; the map turned "WL" into "W1") and must fit
    # the family (_fits_family): same head, a digit when the family has
    # digits. Only under a head that already yielded two marks.
    def _close_read(box) -> str | None:
        for alt in reread(box) or []:
            c = _as_mark(_clean(alt), False, digits_ok)
            if c is not None and c not in marks and _fits_family(c, marks):
                return c
        return None

    if fuzzy and reread is not None and len(marks) >= 2:
        for w in garbled:
            code = _close_read(w)
            if code is None:
                continue
            marks.append(code)
            raws.append(_txt(w))
            y_max = max(y_max, w[3])
            if len(code) > 1:
                letters_only = False
    if fuzzy and cells is not None and reread is not None and len(marks) >= 2:
        rows_seen = [((w[1] + w[3]) / 2.0) for w in words
                     if mx0 <= (w[0] + w[2]) / 2.0 <= mx1 and (w[1] + w[3]) / 2.0 > hw[3]
                     and (_as_mark(_txt(w), fuzzy, digits_ok) in marks or w in garbled)]
        for cb in cells(hw) or []:
            if y_stop is not None and cb[1] >= y_stop - 2:
                continue
            if any(cb[1] <= ry <= cb[3] for ry in rows_seen):
                continue
            code = _close_read((cb[0], cb[1], cb[2], cb[3], ""))
            if code is None:
                continue
            marks.append(code)
            raws.append("")
            y_max = max(y_max, cb[3])
            if len(code) > 1:
                letters_only = False
    if len(marks) < 2:
        return None
    if letters_only and len(marks) < 3:
        # two stray capitals under a "TYPE" heading is not a schedule
        return None
    return {"marks": marks, "qty": qty, "header": header,
            "source": "ocr" if fuzzy else "text",
            "fuzzy": [r for r, c in zip(raws, marks) if r != c],
            "box": (rx0, hw[1] - 4, rx1, y_max + 8)}


def _glyph_blobs(ink: np.ndarray) -> tuple:
    """The glyph blobs of a binarised code picture (ink = 255), specks out,
    wide blobs cut at the thinnest column of their middle half and the
    cutting repeated on the halves - the cell reader's rule (see read_cell
    for the measurements behind it). Returns (labels, [(x, y, w, h, id)])
    sorted left to right."""
    n_lab, lab, stats, _c = cv2.connectedComponentsWithStats(ink, connectivity=8)
    blobs = []
    for i in range(1, n_lab):
        x, y, w, h, a = (int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                         int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT]),
                         int(stats[i, cv2.CC_STAT_AREA]))
        if h >= 4 and a >= 6:
            blobs.append((x, y, w, h, i))
    if blobs:
        tallest = max(b[3] for b in blobs)
        blobs = [b for b in blobs if b[3] >= 0.4 * tallest and b[2] >= 0.2 * tallest]
    for _pass in range(3):
        split: list = []
        cut_any = False
        for x, y, w, h, i in blobs:
            if w > 1.15 * h and w >= 12:
                sub = (lab[y:y + h, x:x + w] == i).astype(np.uint8)
                prof = sub.sum(axis=0)
                lo, hi = int(0.3 * w), int(0.7 * w)
                if hi > lo:
                    cut = lo + int(np.argmin(prof[lo:hi]))
                    split.append((x, y, cut, h, i))
                    split.append((x + cut, y, w - cut, h, i))
                    cut_any = True
                    continue
            split.append((x, y, w, h, i))
        blobs = split
        if not cut_any:
            break
    blobs.sort(key=lambda b: b[0])
    return lab, blobs


def _vocab_shape(vocab: list[str]) -> dict:
    """{length: [set of classes per position]}: which positions of the
    schedule's marks are digits and which letters (W1L / W1R / W4 / W5 ->
    length 3: letter, digit, letter; length 2: letter, digit)."""
    shape: dict = {}
    for v in vocab or []:
        v = v.upper()
        cls = shape.setdefault(len(v), [set() for _ in v])
        for i, ch in enumerate(v):
            cls[i].add("digit" if ch.isdigit() else "letter")
    return shape


def read_by_grammar(g: np.ndarray, vocab: list[str], trace: list | None = None) -> str:
    """Read a code picture GLYPH BY GLYPH under the schedule's own grammar.

    Read as a word, this scan's "2" is an "A" and its trailing "L" is
    dropped: every W2L bubble on the apartment set's plans read "WA" (measured,
    four of four on the 3rd floor), and a two-glyph "W4" read WD / CO /
    HAN one bubble at a time. The schedule says what shape a mark has -
    letter, digit, letter - so each glyph blob of the picture is read on
    its own as a single character with the whitelist its POSITION calls
    for, and the string is accepted only when it IS one of the schedule's
    marks. A wall tag (L04) reads L-0-4 and matches nothing. Returns the
    mark or ""."""
    try:
        import pytesseract
    except Exception:
        return ""
    if g is None or g.size == 0 or not vocab:
        return ""
    shape = _vocab_shape(vocab)
    v = (255 - g).clip(0, 255).astype(np.uint8)          # ink dark on white
    big = cv2.resize(v, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    big = cv2.GaussianBlur(big, (0, 0), 1.2)
    _, ink = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    lab, blobs0 = _glyph_blobs(ink)
    if not blobs0:
        return ""
    H, W = ink.shape
    vocab_u = [x.upper() for x in vocab]

    def _read_one(box, cls) -> str:
        # a blob is (x, y, w, h, id): the intact ink inside its box, or a
        # merged / cut box (id None) read as the ink the box holds
        x, y, w, h, i = box
        bx0, by0, bx1, by1 = max(0, x - 1), max(0, y - 1), min(W, x + w + 1), min(H, y + h + 1)
        if i is None:
            g1 = np.where(ink[by0:by1, bx0:bx1] > 0, 0, 255).astype(np.uint8)
        else:
            m = np.zeros_like(ink)
            m[y:y + h, x:x + w] = (lab[y:y + h, x:x + w] == i).astype(np.uint8)
            m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
            g1 = np.where((m[by0:by1, bx0:bx1] > 0) & (ink[by0:by1, bx0:bx1] > 0), 0, 255).astype(np.uint8)
        if g1.size == 0:
            return ""
        g1 = cv2.resize(g1, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        g1 = cv2.copyMakeBorder(g1, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)
        wl = ("0123456789" if cls == {"digit"} else
              "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if cls == {"letter"} else
              "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        try:
            raw = pytesseract.image_to_string(g1, config=f"--psm 10 -c tessedit_char_whitelist={wl}")
        except Exception:
            return ""
        t = _clean(raw)
        return t if len(t) == 1 else ""

    def _fit(blobs: list, n: int) -> list | None:
        # THE GRAMMAR SAYS HOW MANY GLYPHS. Fewer blobs than that: two
        # glyphs touch (the averaged "2L" is 88 x 91 px, no wider than a
        # glyph), so the widest blob is cut at the thinnest column of its
        # middle half until the count fits. More: one glyph broke (a W in
        # two pieces), so the two closest blobs are merged. Two moves at
        # most, or the picture is not this shape.
        bl = list(blobs)
        for _ in range(2):
            if len(bl) == n:
                return bl
            if len(bl) < n:
                # the blob to cut is the one with the thinnest column in its
                # middle half, relative to its height - a foot junction is a
                # few pixels tall, a W's centre carries two strokes. Width
                # cannot pick it: the joined "2L" (88 x 91) is narrower than
                # the W beside it (101 x 91).
                best, bj, bcut = None, None, None
                for k, (x, y, w, h, i) in enumerate(bl):
                    if w < 8:
                        continue
                    col = np.where(ink[y:y + h, x:x + w] > 0, 1, 0).sum(axis=0)
                    lo, hi = int(0.3 * w), int(0.7 * w)
                    if hi <= lo:
                        continue
                    c = lo + int(np.argmin(col[lo:hi]))
                    score = float(col[c]) / max(1.0, float(h))
                    if best is None or score < best:
                        best, bj, bcut = score, k, c
                if bj is None:
                    return None
                x, y, w, h, i = bl[bj]
                bl[bj:bj + 1] = [(x, y, bcut, h, None), (x + bcut, y, w - bcut, h, None)]
            else:
                gaps = [bl[k + 1][0] - (bl[k][0] + bl[k][2]) for k in range(len(bl) - 1)]
                k = int(np.argmin(gaps))
                a, b = bl[k], bl[k + 1]
                x0, y0 = min(a[0], b[0]), min(a[1], b[1])
                x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
                bl[k:k + 2] = [(x0, y0, x1 - x0, y1 - y0, None)]
        return bl if len(bl) == n else None

    # the picture's own count first, then the other lengths the schedule has
    lengths = sorted(shape, key=lambda n: (n != len(blobs0), n))
    for n in lengths:
        bl = _fit(blobs0, n)
        if bl is None:
            if trace is not None:
                trace.append({"n": n, "fit": None})
            continue
        chars = [_read_one(b, cls) for b, cls in zip(bl, shape[n])]
        if trace is not None:
            trace.append({"n": n, "blobs": [(b[0], b[1], b[2], b[3]) for b in bl], "chars": chars})
        if any(not c for c in chars):
            continue
        s = "".join(chars)
        if s in vocab_u:
            return s
    return ""


def finish_naming(mp, dbg: dict, vocab: list | None) -> set:
    """The post-pass on a MarkPage: names the word read left blank.

    `dbg` is detect_marks' debug dict (codes, canon, groups per tag in the
    detector's own order). With a schedule (`vocab`), a group the word read
    could not snap is read glyph by glyph under the schedule's shape
    (read_by_grammar) - the averaged picture first, a lone tag's own
    picture next. With NO schedule in the set, a group whose free read
    three or more segmentation modes agree on and that is shaped like a
    mark (_TAG_GRAMMAR: a door number, a type letter, a coded mark) takes
    that read as its name, flagged from_tag. Kinds follow the name (a bare
    number is a door number, a bare letter a window type) and the scope is
    recomputed. Returns the set of codes named off the tag."""
    from_tag: set = set()
    groups_idx = dbg.get("groups") or []
    canon = dbg.get("canon") or []
    codes = dbg.get("codes") or []
    vocab_u = [v.upper() for v in (vocab or [])]
    for gi, members in enumerate(groups_idx):
        if gi >= len(mp.groups):
            break
        g = mp.groups[gi]
        if g.get("code"):
            continue
        pics = [canon[i] for i in members if i < len(canon) and canon[i] is not None]
        if not pics:
            continue
        avg = marks.align_average(pics) * 255.0
        code = ""
        if vocab_u:
            code = read_by_grammar(avg, vocab_u)
            if not code and len(members) == 1 and members[0] < len(codes) and codes[members[0]] is not None:
                code = read_by_grammar(codes[members[0]], vocab_u)
        else:
            _c, _w, raw, agree = marks.read_image(avg, [])
            if (not raw or agree < 3) and len(members) == 1 and members[0] < len(codes) and codes[members[0]] is not None:
                _c, _w, raw, agree = marks.read_image(codes[members[0]], [])
            if raw and agree >= 3 and _TAG_GRAMMAR.match(raw):
                code = raw
                from_tag.add(code)
        if not code:
            continue
        g["code"] = code
        g["off_schedule"] = ""
        g["named_by"] = "grammar" if vocab_u else "tag"
        for t in mp.tags:
            if t.group == gi and t.kind != "legend":
                t.code = code
                t.off_schedule = ""
                t.kind = ("storefront" if code.startswith("SF") else "door" if code.startswith("D")
                          else "window" if code.startswith("W") else "door" if code.isdigit()
                          else "window" if (code.isalpha() and len(code) <= 2) else t.kind)
    if from_tag or any(g.get("named_by") == "grammar" for g in mp.groups):
        mp.scope = marks.scope_state(mp.tags, None)
    return from_tag
