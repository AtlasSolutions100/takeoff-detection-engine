"""Text handling for raster pages.

Two layers, used together:

1. detect_glyphs: connected-component text detection - no OCR engine.
   Finds glyph-sized ink components and clusters them into word rows.
   Removing the glyph components from the ink mask BEFORE thinning is what
   keeps annotation text from being traced into scribble chains, and the
   row boxes act as pseudo-words so dash-text bridging works even when the
   text content is unknown.

2. ocr_words: tesseract over the page render, returning real word strings
   with boxes. This is what turns a bridged gap into "SD" instead of "?",
   and it works for vector pages with exploded SHX text too.
"""

import cv2
import numpy as np

# glyph size limits in px at ~200 dpi (scaled by dpi/200 at call time)
GLYPH_MIN = 5
GLYPH_MAX = 46


def detect_glyphs(ink: np.ndarray, dpi_scale: float = 1.0):
    """Return (glyph_mask, row_boxes).

    glyph_mask: uint8 mask of components classified as text glyphs.
    row_boxes: list of (x0, y0, x1, y1) word-row rectangles in px.

    Only glyph COMPONENTS are masked (not whole row boxes), so linework
    passing through a text row is untouched.
    """
    gmin = max(int(GLYPH_MIN * dpi_scale), 3)
    gmax = int(GLYPH_MAX * dpi_scale)

    n, lbl, stats, _ = cv2.connectedComponentsWithStats(
        ink.astype(np.uint8), connectivity=8
    )
    glyph_ids = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not (gmin <= h <= gmax and 2 <= w <= gmax * 1.4):
            continue
        if min(w, h) < gmin * 0.6:
            continue  # thin slivers are dashes, not glyphs
        fill = area / max(w * h, 1)
        if fill < 0.12:
            continue
        # a dashed line is also a row of similar small components - but a
        # dash is a solid elongated bar (high fill), while glyphs are
        # strokes with holes and gaps (low fill)
        aspect = max(w, h) / max(min(w, h), 1)
        if fill > 0.55 and aspect >= 1.8:
            continue
        glyph_ids.append(i)
    if not glyph_ids:
        return np.zeros_like(ink, dtype=np.uint8), []

    glyph_mask = np.isin(lbl, glyph_ids).astype(np.uint8)

    # cluster glyphs into rows: dilate horizontally, keep clusters of >= 2
    kx = max(int(10 * dpi_scale), 5)
    rows = cv2.dilate(glyph_mask, np.ones((3, kx), np.uint8))
    rn, rlbl, rstats, _ = cv2.connectedComponentsWithStats(rows, connectivity=8)
    counts = np.zeros(rn, dtype=int)
    gy, gx = np.nonzero(glyph_mask)
    # count distinct glyph components per row cluster
    pairs = set(zip(rlbl[gy, gx].tolist(), lbl[gy, gx].tolist()))
    for r, _ in pairs:
        counts[r] += 1

    row_boxes = []
    solo = np.zeros(rn, dtype=bool)
    for r in range(1, rn):
        x, y, w, h, _ = rstats[r]
        if counts[r] >= 2:
            row_boxes.append((int(x), int(y), int(x + w), int(y + h)))
        else:
            solo[r] = True
    # lone "glyphs" with no neighbours are more likely symbols; unmask them
    if solo.any():
        lone = solo[rlbl] & (glyph_mask > 0)
        glyph_mask[lone] = 0

    return glyph_mask, row_boxes


def ocr_words(img: np.ndarray, px_to_pt: float, min_conf: int = 40,
              psm: int = 11) -> list:
    """Tesseract words as (x0, y0, x1, y1, text) tuples in pt coordinates.
    img: RGB uint8. Downscales very large renders for speed.

    `psm` is Tesseract's page-segmentation mode and DEFAULTS TO 11 - sparse
    text, no layout assumption - which is what every existing caller here
    has always used and still gets byte-for-byte. It is a parameter because
    a graphic scale bar's tick ladder is not sparse text: it is one printed
    LINE of digits, and psm 11 shreds it. Measured on a commercial set page index
    7 (bar 144.0 pt, ticks 5/5/10 ft, printed `0 5 10 20`): psm 11 returns
    `2`, `99`, `9`, `6`, `4`, `4` - one- and two-label rows the >=3-label
    guard refuses; psm 6 ("a single uniform block of text") returns
    `0 5 10 20` at conf 78/64/64/97, centred on the ticks. Only
    `scale_detect.graphic_scale_bar`'s zoom passes 6; the caption-window
    rescue stays at 11 on purpose (SPEC 7.4: psm 6 also reads the caption
    under the bar and would feed `_scan` new candidates ungated)."""
    import pytesseract

    scale = 1.0
    if max(img.shape[:2]) > 6500:
        scale = 6500 / max(img.shape[:2])
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    data = pytesseract.image_to_data(
        img, config=f"--psm {int(psm)}", output_type=pytesseract.Output.DICT
    )
    words = []
    f = px_to_pt / scale
    for i in range(len(data["text"])):
        t = (data["text"][i] or "").strip()
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            continue
        if not t or conf < min_conf:
            continue
        x, y = data["left"][i], data["top"][i]
        w, h = data["width"][i], data["height"][i]
        words.append((x * f, y * f, (x + w) * f, (y + h) * f, t))
    return words


def merge_words(ocr: list, pseudo: list) -> list:
    """OCR words win; pseudo row boxes fill in only where OCR saw nothing."""
    out = list(ocr)
    for p in pseudo:
        px0, py0, px1, py1 = p[:4]
        covered = False
        for w in ocr:
            ix0, iy0 = max(px0, w[0]), max(py0, w[1])
            ix1, iy1 = min(px1, w[2]), min(py1, w[3])
            if ix1 > ix0 and iy1 > iy0:
                covered = True
                break
        if not covered:
            out.append((px0, py0, px1, py1, "?"))
    return out
