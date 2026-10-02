"""Raster pipeline: page image -> StrokeUnits.

The whole point of this module is to end at the SAME representation the
vector path produces (StrokeUnit polylines with color/width), so chaining,
cadence classification, style discovery, junk pruning, and eval all run
unchanged on scanned or flattened pages.

Shape of the pipeline (same as the client's Blueprint Workbench prototype,
which validated it interactively):
  render page -> ink mask -> Zhang-Suen thinning to a 1px skeleton ->
  split skeleton at junctions -> trace arms into polylines -> measure
  width (distance transform) and color (source pixels) per polyline.

Dashes arrive as many small polylines; solid lines crossing others arrive
as arms meeting at a junction point - the chainer's touching-endpoint links
reassemble them, with a small direction tiebreak so straight continuations
pair up at crossings.
"""

from dataclasses import dataclass, replace

import cv2
import numpy as np
import pymupdf

from .extract import StrokeUnit

# Below this luminance a pixel is ink on a white-background plan.
INK_LEVEL = 185
# Raster measurements are noisy; quantize width/color so style grouping
# doesn't fragment. Width buckets in pt; color channels to this many levels.
WIDTH_BUCKET = 1.6
COLOR_LEVELS = 4
# An ink pixel counts as "coloured" when its RGB channel spread exceeds this
# fraction of full scale. 0.10 sits above JPEG/sensor chroma noise on a grey
# line and below any real CAD pen colour - see ink_chroma().
CHROMA_PX_SAT = 0.10


@dataclass(frozen=True)
class RasterCfg:
    """Knobs for the raster tracer, so the bench can A/B one at a time.

    Three defaults differ from the pipeline as it stood at main@35055f9 -
    ``threshold``, ``color_mode`` and ``deskew``. Each was changed only after
    it was measured on all three bench sheets in both the clean and the
    scan-degraded condition; the numbers, and the candidates that were
    REJECTED, are in out/verify/2026-08-21/raster-lab/VERIFY.md. Reproduce
    the old behaviour with the bench's pinned "base" variant, NOT with
    RasterCfg().
    """

    # --- thresholding -------------------------------------------------
    ink_level: int = INK_LEVEL
    # "global"  = one luminance cut for the whole page (pre-2026-08-21)
    # "sauvola" = local mean/stddev cut, for unevenly lit scans
    # "adaptive"= cv2 adaptive gaussian mean-C
    threshold: str = "sauvola"
    sauvola_window_pt: float = 12.0   # local window, in PAGE points
    sauvola_k: float = 0.22
    # Sauvola alone floods flat white paper with speckle; keep only pixels
    # that are ALSO at least this much darker than the local background.
    sauvola_min_drop: int = 22
    # --- pre-filtering ------------------------------------------------
    # "none" | "bilateral" | "nlm" - runs on the render before thresholding
    denoise: str = "none"
    # --- morphology ---------------------------------------------------
    # px of closing applied to the ink mask before thinning (hairline repair)
    bridge_px: int = 0
    # remove connected ink specks smaller than this many px (0 = off)
    despeckle_px: int = 0
    # --- vectorisation ------------------------------------------------
    approx_eps: float = 0.9
    width_bucket: float = WIDTH_BUCKET
    color_levels: int = COLOR_LEVELS
    # "raw"    = mean of the darkest sampled pixels, quantized (pre-2026-08-21)
    # "chroma" = same, but divided out by the paper-blend fraction first
    # "chroma2"= chroma + a two-level lightness family (measured WORSE than
    #            chroma under degradation - the lightness bucket flips)
    # "mono"   = every unit black (for genuinely greyscale scans)
    # "auto"   = "chroma" when the page's own ink carries real colour,
    #            "raw" when it is essentially greyscale (see ink_chroma)
    color_mode: str = "auto"
    # auto only: minimum fraction of ink pixels that must be coloured before
    # the page is treated as a colour sheet. Measured at 250dpi on the bench
    # sheets: Belle Farms 0.396 clean / 0.446 degraded (red linework),
    # C8.0 0.073 / 0.087, C4.0 0.043 / 0.042 (both greyscale, 90th-percentile
    # saturation 0.000). A client's two scans read 0.091 and 0.022. 0.15
    # sits 2.6x below the lowest colour page and 1.7x above the highest
    # greyscale one - the gap is wide, not knife-edge.
    auto_chroma_frac: float = 0.15
    # Ceiling on the chroma normalisation's gain. Unclamped, a nearly-white
    # sample is divided by a nearly-zero number and its sensor noise becomes
    # a saturated hue.
    color_gain_max: float = 1e9
    # chroma2 only: darkness that separates the "dark pen" family from the
    # "light pen" family.
    light_split: float = 0.55
    glyph_mask: bool = True
    # --- page-level ---------------------------------------------------
    # "hough" (pre-2026-08-21) | "none" | "proj" (projection-profile deskew)
    deskew: str = "proj"
    # Rotating the page resamples every pixel, which costs sharpness. Below
    # this much skew the resampling costs more than the skew does - measured,
    # see deskew().
    deskew_min_deg: float = 0.2
    # 0 = use the caller's dpi as-is; otherwise clamp the render dpi to the
    # source image's own pixel density (see _dpi_for_page)
    dpi_policy: str = "fixed"          # "fixed" | "source"
    dpi_min: int = 200
    dpi_max: int = 400


DEFAULT_CFG = RasterCfg()


def _cfg(cfg: "RasterCfg | None") -> RasterCfg:
    return cfg if cfg is not None else DEFAULT_CFG


def _bump(stats, key, val):
    if stats is not None:
        stats[key] = val


def _capturing(stats) -> bool:
    """Whether the caller wants the intermediate MASKS kept, not just the
    counters. A full-page mask is ~54 MB at 250dpi and there are three of
    them, so a caller that just wants pixel counts must not get handed them
    by accident: opt in with ``stats["_capture"] = True``."""
    return stats is not None and bool(stats.get("_capture"))


def source_dpi(page: pymupdf.Page, min_coverage: float = 0.5) -> float:
    """Effective dpi of the page's own SCAN image, or 0 if it has none.

    A print-and-scan sheet is one image covering the whole sheet; rendering
    it above the dpi the scanner produced only interpolates, and rendering
    below throws away ink the scanner did capture.

    ``min_coverage`` is what keeps this honest, and it is not optional: an
    ordinary vector sheet carries small embedded images - a firm's logo, an
    aerial inset, north arrows - and without the guard the largest of THOSE
    sets the whole-page render dpi. Measured on the bench sheets: Belle
    Farms reports 240 dpi from a 90x135pt logo covering 0.3% of the page,
    and C8.0 reports 400 dpi from an 810x394pt inset covering 7.1%. Neither
    is a scan. The 0.5 default matches classify_page's RASTER_COVERAGE, so
    this only speaks for pages that really are one big scan.
    """
    page_area = max(page.rect.width * page.rect.height, 1.0)
    best = 0.0
    for info in page.get_image_info():
        bbox = pymupdf.Rect(info["bbox"]) & page.rect
        if bbox.is_empty or bbox.width < 50 or bbox.height < 50:
            continue
        if (bbox.width * bbox.height) / page_area < min_coverage:
            continue
        wpx, hpx = info.get("width", 0), info.get("height", 0)
        if not wpx or not hpx:
            continue
        d = max(wpx / (bbox.width / 72.0), hpx / (bbox.height / 72.0))
        best = max(best, d)
    return best


def _dpi_for_page(page: pymupdf.Page, dpi: int, cfg: RasterCfg) -> int:
    if cfg.dpi_policy != "source":
        return dpi
    sd = source_dpi(page)
    if sd <= 0:
        return dpi
    return int(round(min(max(sd, cfg.dpi_min), cfg.dpi_max)))


def extract_raster_units(page: pymupdf.Page, dpi: int = 200,
                         words_out: list | None = None,
                         cfg: RasterCfg | None = None,
                         stats: dict | None = None) -> list[StrokeUnit]:
    """Full raster page -> StrokeUnits (+ words appended to words_out).

    Runs deskew, glyph masking, and OCR around the core tracer, so the
    units the chainer sees are deskewed clean linework, and words_out gets
    real OCR strings plus pseudo-boxes for rows OCR missed.
    """
    cfg = _cfg(cfg)
    dpi = _dpi_for_page(page, dpi, cfg)
    _bump(stats, "render_dpi", dpi)
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace="rgb")
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)

    img, undo = deskew(img, cfg=cfg, stats=stats)
    units = units_from_image(img, px_to_pt=72.0 / dpi, undo_px=undo,
                             words_out=words_out, dpi=dpi, cfg=cfg, stats=stats)
    return units


def _skew_hough(gray: np.ndarray, max_deg: float) -> float | None:
    """Shipped estimator: median axis-deviation of long Hough segments."""
    edges = (gray < INK_LEVEL).astype(np.uint8) * 255
    lines = cv2.HoughLinesP(edges, 1, np.pi / 720, threshold=80,
                            minLineLength=120, maxLineGap=4)
    if lines is None or len(lines) < 8:
        return None
    # OpenCV 5 returns (N, 4); OpenCV <=4 returned (N, 1, 4). Normalise:
    # under (N, 4) the old "lines[:, 0]" silently became a column of x1
    # scalars and the unpack below threw on every deskew-reaching page.
    lines = lines.reshape(-1, 4)
    devs = []
    for x1, y1, x2, y2 in lines:
        ang = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        dev = (ang + 45) % 90 - 45  # deviation from nearest axis
        if abs(dev) <= max_deg:
            devs.append(dev)
    if len(devs) < 8:
        return None
    return float(np.median(devs))


def _skew_projection(gray: np.ndarray, max_deg: float) -> float | None:
    """Projection-profile skew, scored on the page's LONGEST straight runs.

    Why not Hough: that estimator votes with at most a few dozen segments
    and needs 8 of them to clear a 120px length filter, so a sheet whose
    long lines are dashed, curved (contours) or broken by a bad scan
    silently returns "no skew". Measured on the bench sheets rasterized at
    250dpi: it reported 0.00 deg for a page skewed by 0.40, and 0.52 deg for
    a page that was perfectly square. It is wrong in both directions.

    A projection profile uses EVERY ink pixel: rotate the ink mask by a
    candidate angle and sum ink per row and per column. The scoring function
    matters. Summing squared differences across the whole profile rewards
    ANY dense parallel family, and civil sheets carry two - the sheet frame
    and the site grid, which on the C4.0/C8.0 sheets sit 0.8 deg apart. That
    score ties between them and picks the wrong one half the time (measured:
    +0.36 where the truth was -0.40). Summing only the TALLEST FEW bins
    keeps the sheet border and title-block rules, which are the only lines
    that run the full width of the page, and that peak is unambiguous:
    correct on all 6 bench cases (3 sheets x clean/degraded).

    Coarse-to-fine, three passes.
    """
    ink = (gray < INK_LEVEL).astype(np.float32)
    if ink.sum() < 500:
        return None
    h, w = ink.shape
    center = (w / 2.0, h / 2.0)
    k = max(5, min(h, w) // 250)

    def score(a: float) -> float:
        M = cv2.getRotationMatrix2D(center, a, 1.0)
        r = cv2.warpAffine(ink, M, (w, h), flags=cv2.INTER_NEAREST,
                           borderValue=0)
        return float(np.sort(r.sum(axis=1))[-k:].sum()
                     + np.sort(r.sum(axis=0))[-k:].sum())

    best = 0.0
    step = max_deg / 6.0
    lo, hi = -max_deg, max_deg
    for _ in range(3):
        cands = np.arange(lo, hi + 1e-9, step)
        vals = [score(float(a)) for a in cands]
        best = float(cands[int(np.argmax(vals))])
        lo, hi = best - step, best + step
        step /= 5.0
    return best


def deskew(img: np.ndarray, max_deg: float = 6.0,
           cfg: RasterCfg | None = None, stats: dict | None = None):
    """Estimate scan skew and rotate to square if beyond 0.2 degrees.

    Returns (image, undo) where undo is the affine matrix mapping deskewed
    px back to original px - traced geometry goes through it so output still
    registers with the original page render."""
    cfg = _cfg(cfg)
    if cfg.deskew == "none":
        _bump(stats, "skew_deg", 0.0)
        return img, None
    small = cv2.resize(img, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    if cfg.deskew == "proj":
        skew = _skew_projection(gray, max_deg)
        if skew is None:
            skew = _skew_hough(gray, max_deg)
    else:
        skew = _skew_hough(gray, max_deg)
    if skew is None:
        _bump(stats, "skew_deg", None)
        return img, None
    _bump(stats, "skew_deg", round(skew, 3))
    if abs(skew) < cfg.deskew_min_deg:
        return img, None
    h, w = img.shape[:2]
    center = (w / 2, h / 2)
    M = cv2.getRotationMatrix2D(center, skew, 1.0)
    fixed = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR,
                           borderValue=(255, 255, 255))
    undo = cv2.invertAffineTransform(M)
    if stats is not None:
        stats["deskew_M"] = M
    return fixed, undo


# back-compat alias: the private name predates the cfg-aware entry point
_deskew = deskew


def extract_hybrid_image_units(page: pymupdf.Page,
                               cfg: RasterCfg | None = None) -> list[StrokeUnit]:
    """Raster-trace the embedded images of a hybrid page, in place.

    Runs only on the image XObjects (the vector strokes are extracted by the
    vector path - re-tracing a full-page render would double-count them).
    Photo underlays (aerials, site photos) are detected by their tonal
    profile and skipped: linework images are bimodal white-paper/dark-ink.
    """
    cfg = _cfg(cfg)
    units: list[StrokeUnit] = []
    doc = page.parent
    seen = set()
    for info in page.get_image_info(xrefs=True):
        xref = info.get("xref", 0)
        if not xref or xref in seen:
            continue
        seen.add(xref)
        bbox = pymupdf.Rect(info["bbox"]) & page.rect
        if bbox.is_empty or bbox.width < 20 or bbox.height < 20:
            continue  # logos, stamps
        try:
            raw = doc.extract_image(xref)
            arr = np.frombuffer(raw["image"], dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception:
            continue
        if img is None or img.shape[0] < 40 or img.shape[1] < 40:
            continue
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        # cap resolution: enormous scans gain nothing above ~250dpi
        max_px = int(max(bbox.width, bbox.height) / 72.0 * 250)
        if max(img.shape[:2]) > max_px > 0:
            f = max_px / max(img.shape[:2])
            img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)

        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        ink_frac = float((gray < INK_LEVEL).mean())
        mid_frac = float(((gray >= 100) & (gray <= 220)).mean())
        if ink_frac > 0.30 or mid_frac > 0.40:
            continue  # photo-like content, not linework

        h, w = gray.shape
        sx, sy = bbox.width / w, bbox.height / h
        for u in units_from_image(img, px_to_pt=(sx + sy) / 2.0,
                                  transform=(bbox.x0, bbox.y0, sx, sy),
                                  cfg=cfg):
            units.append(u)
    return units


def ink_channel(img: np.ndarray) -> np.ndarray:
    """Per-pixel 'darkness' the threshold runs on.

    Colored linework can be light in luminance but saturated, so the shipped
    rule takes a pixel as ink when EITHER the luminance OR the darkest
    channel says so. ``min(gray, min_channel + 20) < INK_LEVEL`` is exactly
    that rule expressed as one channel, which lets a LOCAL threshold use the
    same evidence a global one did.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    minc = img.min(axis=2).astype(np.int16) + 20
    return np.minimum(gray.astype(np.int16), np.clip(minc, 0, 255)).astype(np.uint8)


def ink_mask(img: np.ndarray, cfg: RasterCfg, px_to_pt: float) -> np.ndarray:
    """Binary ink mask under the configured thresholding strategy."""
    if cfg.denoise == "bilateral":
        img = cv2.bilateralFilter(img, 5, 45, 5)
    elif cfg.denoise == "nlm":
        img = cv2.fastNlMeansDenoisingColored(img, None, 5, 5, 5, 15)

    v = ink_channel(img)
    if cfg.threshold == "global":
        return v < cfg.ink_level

    # window in px from a window in page points, forced odd and >= 15
    win = int(round(cfg.sauvola_window_pt / max(px_to_pt, 1e-6)))
    win = max(15, win | 1)
    if cfg.threshold == "adaptive":
        binm = cv2.adaptiveThreshold(v, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY_INV, win, 10)
        local = binm > 0
    else:  # sauvola
        thr = cv2.ximgproc.niBlackThreshold(
            v, 255, cv2.THRESH_BINARY_INV, win, cfg.sauvola_k,
            binarizationMethod=cv2.ximgproc.BINARIZATION_SAUVOLA)
        local = thr > 0
    # Sauvola/adaptive both hallucinate texture on blank paper (the local
    # stddev of pure white is sensor noise). Require a real drop below the
    # local background AND keep the global cut as a floor - anything the
    # global rule already called ink stays ink.
    bg = cv2.blur(v, (win, win))
    real = v.astype(np.int16) < (bg.astype(np.int16) - cfg.sauvola_min_drop)
    return (local & real) | (v < cfg.ink_level)


def quantize_color(color, cfg: RasterCfg) -> tuple:
    """Sampled pixel colour -> the style key the chainer groups on.

    Why "raw" is fragile on scans: a thin line never reaches its own colour
    in the render. Every pixel of it is a blend  c = 1 - a*(1 - ink)  of the
    ink with white paper, and the blend fraction `a` moves with line width,
    anti-aliasing, blur and JPEG. Quantizing c to 4 levels per channel then
    scatters ONE line across several style buckets, and chain.py groups by
    style BEFORE it links anything - so the dashes of one line end up in
    different groups and can never be chained.

    "chroma" removes `a` before quantizing: (1 - c) is exactly a*(1 - ink),
    so its DIRECTION is blend-invariant. Normalising by its largest channel
    recovers the saturated ink colour, which is the property that actually
    identifies the pen. Measured on Belle Farms p0 rasterized at 250dpi with
    scan degradation: 18 distinct colours -> 5, and dash1 coverage 20.7% ->
    (see the bench table). The cost is that grey and black collapse
    together, so a sheet that distinguishes styles by grey level alone loses
    that separation - width and cadence still separate them.
    """
    c = np.asarray(color, dtype=np.float64)
    if cfg.color_mode == "mono":
        return (0.0, 0.0, 0.0)
    if cfg.color_mode in ("chroma", "chroma2"):
        d = 1.0 - c
        m = float(d.max())
        if m > 0.05:                 # anything fainter is paper, not ink
            g = min(1.0 / m, cfg.color_gain_max)
            if cfg.color_mode == "chroma2":
                # keep a COARSE lightness family alongside the normalised
                # hue: two buckets, so a sheet that separates styles by grey
                # level keeps that separation, but a blur that moves the
                # grey level a little cannot flip the bucket.
                g *= 1.0 if m >= cfg.light_split else 0.55
            c = np.clip(1.0 - d * g, 0.0, 1.0)
    q = cfg.color_levels - 1
    return tuple(round(float(x) * q) / q for x in c)


def ink_chroma(img: np.ndarray, ink: np.ndarray,
               max_samples: int = 200_000) -> tuple[float, float]:
    """How much of this page's ink carries real colour.

    Returns (fraction of ink pixels whose channel spread exceeds
    CHROMA_PX_SAT, 90th-percentile channel spread over ink pixels), both
    0..1. Subsampled - a full-page ink set runs to millions of pixels and
    the statistic does not need them.
    """
    ys, xs = np.nonzero(ink)
    if not len(ys):
        return 0.0, 0.0
    step = max(1, len(ys) // max_samples)
    px = img[ys[::step], xs[::step]].astype(np.int16)
    sat = (px.max(axis=1) - px.min(axis=1)) / 255.0
    return float(np.mean(sat > CHROMA_PX_SAT)), float(np.percentile(sat, 90))


def units_from_image(img: np.ndarray, px_to_pt: float,
                     transform: tuple | None = None,
                     undo_px: np.ndarray | None = None,
                     words_out: list | None = None,
                     dpi: int = 200,
                     cfg: RasterCfg | None = None,
                     stats: dict | None = None) -> list[StrokeUnit]:
    from .text_detect import detect_glyphs, merge_words, ocr_words

    cfg = _cfg(cfg)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    ink = ink_mask(img, cfg, px_to_pt)
    if stats is not None:
        stats["px_total"] = int(ink.size)
        stats["ink_px"] = int(ink.sum())
        if _capturing(stats):
            stats["mask_ink_raw"] = ink.copy()

    if cfg.color_mode == "auto":
        # decide ONCE per page, before any quantisation: chroma
        # normalisation recovers a saturated pen's identity but collapses
        # grey levels, so it must only run where a pen colour exists to
        # recover.
        sat_frac, sat_p90 = ink_chroma(img, ink)
        picked = "chroma" if sat_frac >= cfg.auto_chroma_frac else "raw"
        cfg = replace(cfg, color_mode=picked)
        if stats is not None:
            stats["chroma_sat_frac"] = round(sat_frac, 4)
            stats["chroma_sat_p90"] = round(sat_p90, 4)
            stats["color_mode_used"] = picked

    # strip text glyphs before thinning: annotation text otherwise traces
    # into scribble chains and breaks lines it collides with
    if cfg.glyph_mask:
        glyph_mask, row_boxes = detect_glyphs(ink, dpi_scale=dpi / 200.0)
        ink = ink & (glyph_mask == 0)
    else:
        glyph_mask, row_boxes = np.zeros_like(ink, dtype=np.uint8), []
    if stats is not None:
        stats["glyph_px"] = int(glyph_mask.sum())
        stats["ink_after_glyph_px"] = int(ink.sum())

    if words_out is not None:
        pseudo = [(x0 * px_to_pt, y0 * px_to_pt, x1 * px_to_pt, y1 * px_to_pt)
                  for x0, y0, x1, y1 in row_boxes]
        try:
            ocr = ocr_words(img, px_to_pt)
        except Exception:
            ocr = []
        merged = merge_words(ocr, pseudo)
        if undo_px is not None:
            # words were found in the deskewed frame; map their boxes back
            # to the original frame like the geometry
            fixed = []
            for x0, y0, x1, y1, *rest in merged:
                cs = np.array([[x0, y0], [x1, y1]]) / px_to_pt
                cs = np.column_stack([
                    undo_px[0, 0] * cs[:, 0] + undo_px[0, 1] * cs[:, 1] + undo_px[0, 2],
                    undo_px[1, 0] * cs[:, 0] + undo_px[1, 1] * cs[:, 1] + undo_px[1, 2],
                ]) * px_to_pt
                fixed.append((float(cs[:, 0].min()), float(cs[:, 1].min()),
                              float(cs[:, 0].max()), float(cs[:, 1].max()),
                              *(rest or ["?"])))
            merged = fixed
        words_out.extend(merged)

    mask = ink.astype(np.uint8) * 255

    if cfg.despeckle_px > 0:
        n, lbl, st, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        keep = np.zeros(n, dtype=bool)
        keep[1:] = st[1:, cv2.CC_STAT_AREA] >= cfg.despeckle_px
        keep[0] = False
        mask = (keep[lbl].astype(np.uint8)) * 255
    if cfg.bridge_px > 0:
        k = cfg.bridge_px * 2 + 1
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    if stats is not None:
        stats["mask_final_px"] = int((mask > 0).sum())
        if _capturing(stats):
            stats["mask_final"] = mask > 0

    skel = cv2.ximgproc.thinning(mask)
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 3)
    if stats is not None:
        stats["skel_px"] = int((skel > 0).sum())
        if _capturing(stats):
            stats["mask_skel"] = skel > 0

    paths = _trace_skeleton(skel > 0)
    if stats is not None:
        stats["paths"] = len(paths)
        stats["path_px"] = int(sum(len(p) for p in paths))
        if _capturing(stats):
            stats["trace_pts"] = [np.asarray(p, dtype=np.float32) for p in paths]

    units: list[StrokeUnit] = []
    h, w = gray.shape
    for path in paths:
        pts_px = np.asarray(path, dtype=np.float32)
        if len(pts_px) >= 3 and cfg.approx_eps > 0:
            approx = cv2.approxPolyDP(pts_px.reshape(-1, 1, 2), cfg.approx_eps, False)
            pts_px = approx.reshape(-1, 2).astype(np.float32)
        if len(pts_px) < 2:
            # isolated dot: synthesize a tiny segment so it can chain
            p = pts_px[0]
            pts_px = np.array([p, p + 0.5], dtype=np.float32)

        # sample width and color along the ORIGINAL (unsimplified) trace
        samp = np.asarray(path[:: max(1, len(path) // 12)], dtype=int)
        ys = samp[:, 1].clip(0, h - 1)
        xs = samp[:, 0].clip(0, w - 1)
        width_px = float(np.median(dist[ys, xs]) * 2.0)
        rgb = img[ys, xs].astype(np.float64) / 255.0
        # sample colors can straddle white gaps/antialiasing; take the
        # darkest half as the line's color
        lum = rgb.mean(axis=1)
        dark = rgb[lum <= np.median(lum)]
        color = dark.mean(axis=0) if len(dark) else rgb.mean(axis=0)
        color = quantize_color(color, cfg)

        wb = cfg.width_bucket
        width_pt = max(round(width_px * px_to_pt / wb) * wb, wb)
        pts_px = pts_px.astype(np.float64)
        if undo_px is not None:
            # back from the deskewed frame to the original page render
            pts_px = np.column_stack([
                undo_px[0, 0] * pts_px[:, 0] + undo_px[0, 1] * pts_px[:, 1] + undo_px[0, 2],
                undo_px[1, 0] * pts_px[:, 0] + undo_px[1, 1] * pts_px[:, 1] + undo_px[1, 2],
            ])
        if transform is not None:
            ox, oy, sx, sy = transform
            pts = np.column_stack([
                ox + pts_px[:, 0] * sx,
                oy + pts_px[:, 1] * sy,
            ])
        else:
            pts = pts_px * px_to_pt
        units.append(
            StrokeUnit(pts=pts, color=color, width=width_pt, dashes="[] 0",
                       closed=False, layer="")
        )
    if stats is not None:
        stats["units"] = len(units)
        stats["unit_pts"] = int(sum(len(u.pts) for u in units))
    return units


def _trace_skeleton(S: np.ndarray) -> list:
    """Split a 1px skeleton at junctions and trace each arm into an ordered
    pixel path [(x, y), ...]. Arms are terminated with their junction
    cluster's centroid so arms that meet at a crossing share a touching
    endpoint the chainer can join."""
    S8 = S.astype(np.uint8)
    deg = cv2.filter2D(S8, -1, np.ones((3, 3), np.uint8), borderType=cv2.BORDER_CONSTANT) - S8
    junction = S & (deg >= 3)
    path_px = S & ~junction

    # junction clusters -> centroid per cluster, lookup by pixel
    n_lbl, lbl = cv2.connectedComponents(junction.astype(np.uint8), connectivity=8)
    cent = np.zeros((n_lbl, 2))
    if n_lbl > 1:
        ys, xs = np.nonzero(junction)
        for label, x, y in zip(lbl[ys, xs], xs, ys):
            cent[label][0] += x
            cent[label][1] += y
        counts = np.bincount(lbl[ys, xs], minlength=n_lbl).clip(1)
        cent[:, 0] /= counts
        cent[:, 1] /= counts

    H, W = S.shape
    NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

    pdeg = cv2.filter2D(path_px.astype(np.uint8), -1, np.ones((3, 3), np.uint8),
                        borderType=cv2.BORDER_CONSTANT) - path_px.astype(np.uint8)
    visited = np.zeros_like(S, dtype=bool)
    paths = []

    def junction_touch(y, x):
        """Centroid of a junction cluster adjacent to (y, x), if any."""
        for dy, dx in NB:
            yy, xx = y + dy, x + dx
            if 0 <= yy < H and 0 <= xx < W and junction[yy, xx]:
                c = cent[lbl[yy, xx]]
                return (float(c[0]), float(c[1]))
        return None

    def walk(y, x):
        path = []
        start_j = junction_touch(y, x)
        if start_j:
            path.append(start_j)
        while True:
            visited[y, x] = True
            path.append((float(x), float(y)))
            nxt = None
            for dy, dx in NB:
                yy, xx = y + dy, x + dx
                if 0 <= yy < H and 0 <= xx < W and path_px[yy, xx] and not visited[yy, xx]:
                    nxt = (yy, xx)
                    break
            if nxt is None:
                end_j = junction_touch(y, x)
                if end_j:
                    path.append(end_j)
                return path
            y, x = nxt

    # arm tips first (degree<=1 within path pixels), then leftover cycles
    ys, xs = np.nonzero(path_px & (pdeg <= 1))
    for y, x in zip(ys, xs):
        if not visited[y, x]:
            paths.append(walk(y, x))
    ys, xs = np.nonzero(path_px)
    for y, x in zip(ys, xs):
        if not visited[y, x]:
            paths.append(walk(y, x))
    return paths
