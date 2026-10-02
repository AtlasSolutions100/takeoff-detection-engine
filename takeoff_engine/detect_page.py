"""Per-page detection pipeline, runnable in a child process.

This is the body of what used to be ``server._detect_worker``: open the
PDF, extract units, chain them, read the legend, detect scale / fills /
openings, flag texture styles, and write ``p{page}_result.json``.

It lives here - and NOT in server.py - because the service runs it in a
``multiprocessing`` child (spawn context). The child re-imports the target
function's module; if that module were ``server`` it would re-create the
FastAPI app, reload the job registry and mount static files on every
page. This module must therefore never import ``server``.

Contract: ``run_detect(pdf_path, page, job_dir)`` writes
``<job_dir>/p{page}_result.json`` with exactly the keys and content the
thread worker wrote. On failure it writes ``<job_dir>/p{page}_error.txt``
with the exception text (so the parent can surface ``error: <reason>``
across the process boundary) and re-raises.
"""

import json
import math
import re
from collections import defaultdict
from pathlib import Path

import pymupdf

from .chain import build_chains, last_drop_ledger
from .classify_page import classify_page
from .extract import extract_strokes, page_words
from .legend_detect import read_legend
from .scale_detect import detect_scale


def result_path(job_dir: str, page: int) -> Path:
    return Path(job_dir) / f"p{page}_result.json"


def error_path(job_dir: str, page: int) -> Path:
    return Path(job_dir) / f"p{page}_error.txt"


# Sheet-number tokens as title blocks actually print them, measured on three
# document families (2026-08-30): discipline letter(s), optional dash, short
# number, optional decimal - "C1.0" (a residential civil, 20.4pt spans), "C-04"
# (a substation set, 26.2pt), "C5.1" (a 48-page civil set, 40.2pt). Digits are capped at 3 so
# sheet series like C-101 pass while title-block job numbers (24105,
# 2024.15 - 4+ digits, no letter) never can. Digits-only tokens are accepted
# ONLY with a decimal point ("3.1"-style series): a bare integer in the
# title-block corner is far more often a revision number, a detail bubble or
# a date fragment than a sheet number, and a wrong sheet id on an estimate
# is worse than none.
_SHEET_TOKEN = re.compile(r"^[A-Z]{1,3}-?\d{1,3}(\.\d{1,2})?[A-Z]?$")
_SHEET_TOKEN_NUM = re.compile(r"^\d{1,2}\.\d{1,2}$")
# Cross-references to OTHER sheets print small: measured 5.6pt ("CF3.0"
# plumbing refs) and 9.1pt ("C2.0" callout) on Res-A C4.3, 10.1pt ("EOS-1")
# on substation C-05, vs 20.4-40.2pt for every real sheet number seen. The floor
# sits under the real ones with margin; the largest-wins rule handles pages
# that print both.
_SHEET_MIN_PT = 8.0


def read_sheet_number(page) -> "str | None":
    """The sheet number this page's title block PRINTS, or None.

    'Sheet 4' meaning engine index 3 has already caused confusion (CLAUDE.md
    Section 5: page numbering is ambiguous); the printed number - "C3.1" - is
    the only name the client's own drawings use. It sits in a title-block
    cell in the bottom-right of the sheet in DISPLAY space, and it is the
    largest sheet-number-shaped text there (the sheet NAME can be larger,
    but "FOUNDATION DRAIN PLAN" is not number-shaped).

    Method: text spans from get_text("dict") mapped to display space
    (get_text reports UNROTATED coordinates - same trap extract.page_words
    documents; page.rect is already display space), kept only inside the
    bottom-right corner (both x and y past 70% of the sheet), matched
    against the token shapes above, largest font size wins, nearest to the
    corner on a tie. Lettered tokens outrank digits-only ones.

    ABSTAINS (returns None) when nothing qualifies: raster/scanned pages
    have no text spans there, and a guessed sheet id is worse than an index.
    Measured 2026-08-30: 15/15 on a residential civil set (C1.0-C5.1, incl.
    C4.3 whose name row is a foundation plan), 9/9 on a substation set (C-01..C-09),
    43/48 on a 48-page civil set - the 5 abstains (idx 0/6/16/30/37) are
    section pages whose bottom-right corner was render-verified to hold
    border ink and no text at all. tests/test_sheet_no.py asserts all of it.
    """
    rect = page.rect  # rotation-aware: this IS display space
    W, H = rect.width, rect.height
    if W <= 0 or H <= 0:
        return None
    rot = page.rotation_matrix if page.rotation else None
    try:
        blocks = page.get_text("dict").get("blocks", [])
    except Exception:
        return None

    def display_center(bbox):
        x0, y0, x1, y1 = bbox
        if rot is not None:
            a = pymupdf.Point(x0, y0) * rot
            b = pymupdf.Point(x1, y1) * rot
            x0, x1 = sorted((a.x, b.x))
            y0, y1 = sorted((a.y, b.y))
        return (x0 + x1) / 2.0, (y0 + y1) / 2.0

    cands = []  # (lettered, size_pt, -corner_dist, token)

    def consider(text, size, bbox):
        t = "".join(text.split()).upper()
        if not t or len(t) > 10 or size < _SHEET_MIN_PT:
            return
        lettered = bool(_SHEET_TOKEN.match(t))
        if not lettered and not _SHEET_TOKEN_NUM.match(t):
            return
        cx, cy = display_center(bbox)
        if cx < 0.70 * W or cy < 0.70 * H:
            return
        d = math.hypot((W - cx) / W, (H - cy) / H)
        cands.append((lettered, size, -d, t))

    for b in blocks:
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            spans = ln.get("spans", [])
            for sp in spans:
                consider(sp.get("text", ""), sp.get("size", 0.0),
                         sp.get("bbox", (0, 0, 0, 0)))
            if len(spans) > 1:
                # exploded text: "C" and "4.3" can arrive as separate spans
                # of one PDF line - consider the joined line too
                joined = "".join(sp.get("text", "") for sp in spans)
                bb = [min(sp["bbox"][0] for sp in spans),
                      min(sp["bbox"][1] for sp in spans),
                      max(sp["bbox"][2] for sp in spans),
                      max(sp["bbox"][3] for sp in spans)]
                consider(joined, max(sp.get("size", 0.0) for sp in spans), bb)

    if not cands:
        return None
    lettered = [c for c in cands if c[0]]
    pool = lettered or cands
    best = max(c[1] for c in pool)
    # 0.5pt: same-cell text repeats at identical size; distinct cells differ
    # by whole points (20.4 vs 9.1 on Res-A C4.3)
    top = [c for c in pool if c[1] >= best - 0.5]
    return max(top, key=lambda c: c[2])[3]


# The modules whose code decides what a stored result contains. A result is
# cached per job on (pdf path, mtime), which correctly reuses work when the
# DRAWING is unchanged but says nothing about whether the CODE changed. That
# hid a fix in plain sight on 2026-08-22: the scale reader was corrected from
# "SCALE 5'-2"" to "1 inch = 20 ft.", the API returned 20.0, and the browser
# kept showing 5'-2" in a confident green chip read off the stored file. An
# estimator who detected a page before a fix keeps the wrong number with no
# way to know. regress.cached_extract keys its cache on extract.py's mtime
# for exactly this reason; this is the same idea one level up.
_ENGINE_FILES = ("extract.py", "chain.py", "scale_detect.py",
                 "legend_detect.py", "detect_page.py", "raster.py",
                 "text_detect.py", "areas.py", "marks.py",
                 # classify_page.py picks WHICH of the above even runs, so a
                 # change there can swap every chain on the page. It was
                 # missing from this list until 2026-08-22, which meant the
                 # routing fix for Res-C A0.0 would have read back out of
                 # the cache as the old raster result.
                 "classify_page.py",
                 # __init__.py runs on EVERY import of this package and no
                 # import statement names it, so a patch there moves every
                 # detection on every sheet without touching a listed file.
                 # Demonstrated 2026-09-01 (adversarial review, F6): a
                 # build_chains wrapper appended there changed counts on all
                 # ten gate sheets with engine_sig unchanged. Same hole, same
                 # fix as regress._EARTHWORK_ROOTS.
                 "__init__.py")


_SIG_CACHE: str = ""


def engine_sig() -> str:
    """Fingerprint of the code that produces a detect result.

    CONTENT, not mtime. This hashed `stat().st_mtime_ns` until 2026-08-25, and
    a container image rebuild writes every file fresh - so a deploy that
    changed nothing in this package still moved the signature and marked every
    cached result stale. Measured on the live box after the v1.0.0 deploy:
    stored 36279aab064ac27d, recomputed 2c85e7633b183487, and all twenty
    pre-detected demo pages came back `{"status":"idle","stale":true}`. The
    plans were sitting right there and the next demo would have re-detected
    every one of them.

    Hashing the bytes means an identical build keeps the cache and a real code
    change still invalidates it, which is what the signature was always meant
    to express. Cached per process: the files cannot change under a running
    service, and this is consulted on every status poll.
    """
    global _SIG_CACHE
    if _SIG_CACHE:
        return _SIG_CACHE
    import hashlib

    h = hashlib.sha1()
    here = Path(__file__).parent
    for name in _ENGINE_FILES:
        try:
            h.update(name.encode() + b":")
            h.update(hashlib.sha1((here / name).read_bytes()).digest())
            h.update(b"|")
        except OSError:
            h.update(f"{name}:missing|".encode())
    _SIG_CACHE = h.hexdigest()[:16]
    return _SIG_CACHE


def run_detect(pdf_path: str, page: int, job_dir: str) -> None:
    out_dir = Path(job_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = detect_page(pdf_path, page)
        result["engine_sig"] = engine_sig()
        result_path(job_dir, page).write_text(json.dumps(result))
    except Exception as e:  # surfaced to the UI by the parent process
        try:
            error_path(job_dir, page).write_text(str(e))
        except OSError:
            pass
        raise


def detect_page(pdf_path: str, page: int) -> dict:
    """The pure pipeline: returns the result dict for one page."""
    doc = pymupdf.open(pdf_path)
    pg = doc[page]
    pc = classify_page(pg)
    # printed sheet number, read up front: a reader crash must never cost
    # the detection, so it degrades to the abstain value
    try:
        sheet_no = read_sheet_number(pg)
    except Exception:
        sheet_no = None
    if pc.kind == "raster":
        from .raster import extract_raster_units

        words: list = []
        units = extract_raster_units(pg, words_out=words)
    else:
        units = extract_strokes(pg)
        words = page_words(pg)
        has_layers = any(u.layer for u in units[:300])
        if pc.text_chars < 1500 and not has_layers:
            # sparse text + no CAD layers = exploded annotations with no
            # metadata; OCR fills in word boxes for junk pruning +
            # dash-text labels. Layered pages skip OCR - layer names are
            # strictly better metadata and OCR words only add noise.
            try:
                import numpy as _np

                from .text_detect import ocr_words

                _pix = pg.get_pixmap(matrix=pymupdf.Matrix(150 / 72, 150 / 72),
                                     colorspace="rgb")
                _img = _np.frombuffer(_pix.samples, dtype=_np.uint8).reshape(
                    _pix.height, _pix.width, 3)
                words = list(words) + ocr_words(_img, px_to_pt=72.0 / 150)
            except Exception:
                pass
        if pc.kind == "hybrid":
            from .raster import extract_hybrid_image_units

            units.extend(extract_hybrid_image_units(pg))
    chains = build_chains(units, words=words)
    # What the pruning stages threw away on this page, and why. Read
    # immediately: the ledger is per-thread and the next build_chains on this
    # thread clears it. Every one of these was an unconditional delete with no
    # record, so "the last 2%" had no denominator - see chain.last_drop_ledger.
    _drops = last_drop_ledger()

    _legend_labels: dict = {}
    styles: dict = defaultdict(lambda: {
        "n": 0, "length_pt": 0.0, "class": "", "color": None,
        "width": 0.0, "layer": "", "signature": "",
    })
    for c in chains:
        s = styles[c.style_id]
        s["n"] += 1
        s["length_pt"] += c.length
        s["class"] = c.klass
        s["color"] = c.color
        s["width"] = c.width
        s["layer"] = c.layer
        s["signature"] = c.dash_signature
    style_list = []
    for sid, s in styles.items():
        label = s["layer"].split("|")[-1] if s["layer"] else \
            f'{s["class"]} {s["signature"]}'.strip()
        label = _legend_labels.get(sid, label)
        style_list.append({
            "style_id": sid, "suggested_label": label,
            "length_pt": round(s["length_pt"], 1), **{
                k: s[k] for k in ("n", "class", "color", "width",
                                  "layer", "signature")},
        })
    style_list.sort(key=lambda s: -s["length_pt"])

    # legend reading: OCR words help here even on layered pages (the
    # legend text is often exploded strokes); these words are used ONLY
    # for the legend, not for chaining
    words_legend = list(words)
    if pc.text_chars < 1500 and pc.kind != "raster":
        try:
            import numpy as _np2

            from .text_detect import ocr_words as _ocr

            _pix2 = pg.get_pixmap(matrix=pymupdf.Matrix(150 / 72, 150 / 72),
                                  colorspace="rgb")
            _img2 = _np2.frombuffer(_pix2.samples, dtype=_np2.uint8).reshape(
                _pix2.height, _pix2.width, 3)
            words_legend += _ocr(_img2, px_to_pt=72.0 / 150)
        except Exception:
            pass
    legend = read_legend(words_legend, chains, units=units)
    _legend_labels.update(legend["style_labels"])
    # swatch locations: the legend's own sample bars must never be
    # counted as takeoff objects (Belle p2 grew a 25th 'shear wall')
    legend_samples = [e["sample_box"] for e in legend.get("entries", [])
                      if e.get("sample_box")]
    # the swatch bars themselves are sheet furniture: demote the
    # chains living inside the legend strips so they never reach the
    # style pool as takeoff linework
    import numpy as _np
    for c in chains:
        if c.klass == "annotation" or len(c.pts) < 2:
            continue
        m = _np.asarray(c.pts).mean(axis=0)
        for b in legend_samples:
            if b[0]-6 <= m[0] <= b[2]+6 and b[1]-8 <= m[1] <= b[3]+8 \
                    and c.length <= 1.5 * (b[2] - b[0]):
                c.klass = "annotation"
                c.dash_signature = "legend swatch"
                break
    for st in style_list:
        if st["style_id"] in _legend_labels:
            st["suggested_label"] = _legend_labels[st["style_id"]] + " (legend)"
    # THE SCALE BOUNDARY. `scale` means "the scale in force on this page", and
    # every consumer downstream - areas, door swings, the export's
    # pixelsPerUnit, ~40 read sites in webui/app.js - treats a truthy value as
    # exactly that. An ABSTAINED reading is not that: the engine recovered
    # captions it will not vouch for, and promoting one was measured
    # confidently wrong twice in one evening (CLAUDE.md section 5, the fourth
    # shape).
    #
    # So the two travel in different fields. `scale` stays None on an
    # abstained page - the same value it has always had there, which is what
    # makes carrying the candidates non-regressive - and the survivors ride in
    # `scale_suggestions` for the review UI to OFFER and a person to pick,
    # never to apply. A page with no reading AND no candidates gets None for
    # both, so the honest "no scale on this sheet" state is untouched.
    _sc = detect_scale(pg).to_dict()
    scale_suggestions = None
    if _sc and _sc.get("abstained"):
        scale_suggestions, _sc = _sc, None
    scale = _sc
    from .extract import fill_boxes, door_swings
    try:
        fills = fill_boxes(pg)
    except Exception:
        fills = []
    # filled AREA regions: square-foot takeoff (hardscape, pools, washes).
    # Its own get_drawings() walk on purpose - see areas.py's docstring; the
    # stroke path stays untouched so the vector gate cannot move.
    try:
        from .areas import page_areas

        areas = page_areas(pg, scale, words)
    except Exception:
        areas = {"scale": {}, "regions": [], "groups": [],
                 "unmatched_legend": [], "legend_classes": [], "truncated": 0}
    try:
        openings = door_swings(
            units, (scale or {}).get("ft_per_paper_inch"), words)
    except Exception:
        openings = []
    # texture styles: a shading fill is MANY parallel chains at a tight
    # regular pitch. Measured: stair set 'SHADING ASPHALT' strips pitch
    # 1-4pt; real parallel features sit far wider (contours 20-40pt,
    # curb pairs are 2 members). Flagged styles default OFF in the UI
    # and render faint - they are area shading, not takeoff linework.
    by_style_chains = {}
    for c in chains:
        by_style_chains.setdefault(c.style_id, []).append(c)
    for st in style_list:
        cs = by_style_chains.get(st["style_id"], [])
        st["texture"] = False
        if len(cs) < 15:
            continue
        dirs = []
        mids = []
        for c in cs:
            pts = _np.asarray(c.pts)
            v = pts[-1] - pts[0]
            n = float(_np.hypot(*v))
            if n < 1e-6:
                continue
            dirs.append(v / n)
            mids.append(pts.mean(axis=0))
        if len(dirs) < 15:
            continue
        D = _np.asarray(dirs)
        ang = _np.arctan2(D[:, 1], D[:, 0]) * 2
        R = float(_np.hypot(_np.mean(_np.cos(ang)), _np.mean(_np.sin(ang))))
        if R < 0.80:
            continue  # 0.80: stair-set EXST-CONCRETE shading bends with the
                      # slope and measured R=0.84; real mixed-direction
                      # linework sits far lower (L-PVMT-CONC: 0.21)
        mean_ang = _np.arctan2(_np.mean(_np.sin(ang)), _np.mean(_np.cos(ang))) / 2
        nrm = _np.array([-_np.sin(mean_ang), _np.cos(mean_ang)])
        off = _np.sort(_np.asarray(mids) @ nrm)
        gaps = _np.diff(off)
        gaps = gaps[gaps > 0.05]
        # pitch 9.0: measured shading pitches 1-4pt (stair set South) and
        # 6.2-6.6pt (stair set North EXST-CONCRETE, escaped the first 6.0
        # gate by 0.2-0.6pt); nearest real parallel features are
        # contour fields at 18-40pt - margin stays > 2x
        if len(gaps) >= 10 and float(_np.median(gaps)) <= 9.0:
            st["texture"] = True
    return {
        "page": pc.to_dict(),
        # Disclosed, per the pattern `areas` already sets: a cap or a filter
        # that reports nothing reads downstream as full coverage.
        "drops": _drops,
        "sheet_no": sheet_no,
        "scale": scale,
        # Readings the engine recovered but WILL NOT vouch for - offered to
        # the reviewer, never in force. None on every page that reads a scale
        # and on every page that prints none. See the scale boundary above.
        "scale_suggestions": scale_suggestions,
        "legend": legend["entries"],
        "styles": style_list,
        "chains": [c.to_dict() for c in chains],
        "fills": fills,
        "areas": areas,
        "openings": openings,
        "legend_samples": legend_samples,
    }
