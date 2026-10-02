"""Tesseract-based readers for exploded (stroke-font) plan text.

Two readers:
  * tile OCR      - the whole page rendered and read in overlapping tiles
                    (horizontal spot grades, tags, notes, scale text)
  * patch OCR     - small rotated crops (contour labels sitting in line gaps)

Both go through ONE tesseract launch per batch (list-file mode) because a
process launch on Windows costs ~0.3-1 s.
"""
from __future__ import annotations

import io
import math
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

from .ingest import Document
from .readslot import check_current

TESS_CANDIDATES = [
    shutil.which("tesseract"),
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract", "/opt/homebrew/bin/tesseract",
]


def tesseract_path() -> str | None:
    for p in TESS_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


@dataclass
class OcrWord:
    text: str
    conf: float
    bbox: tuple                 # page points x0,y0,x1,y1 (axis aligned)
    page_img: int = 0           # index of the image in the batch
    line_key: tuple = ()        # (img, block, par, line) for grouping
    px_box: tuple = ()          # box in the source image (px)


class OcrTooLong(RuntimeError):
    """One tesseract run passed ocr_timeout(): it was killed and the read stops."""


DEFAULT_OCR_TIMEOUT_S = 300.0


def ocr_timeout() -> float:
    """CUTFILL_OCR_TIMEOUT_S, read on every call: how long ONE tesseract run (a batch of tiles or words) may take
    before it is killed. Unset, not a number or not above 0 means 300 s. Measured 2026-10-01: Res-A civil C3.0's
    whole-sheet OCR took 11.4 s for all its runs together (four at once), its word OCR 2.1 s."""
    try:
        v = float(str(os.environ.get("CUTFILL_OCR_TIMEOUT_S", "")).strip() or DEFAULT_OCR_TIMEOUT_S)
    except ValueError:
        v = DEFAULT_OCR_TIMEOUT_S
    return v if (v == v and 0 < v < float("inf")) else DEFAULT_OCR_TIMEOUT_S


def _run_tesseract(paths: list[str], psm: int, extra: list[str] | None = None, workdir: str | None = None) -> str:
    """Run one tesseract process over a list of images, return TSV text."""
    exe = tesseract_path()
    if exe is None:
        raise RuntimeError("tesseract not found")
    workdir = workdir or tempfile.mkdtemp(prefix="cf_ocr_")
    lst = os.path.join(workdir, "list_%d.txt" % (abs(hash(tuple(paths))) % 100000))
    with open(lst, "w", encoding="utf-8") as f:
        f.write("\n".join(paths) + "\n")
    outbase = lst[:-4] + "_out"
    cmd = [exe, lst, outbase, "--psm", str(psm), "-l", "eng"] + (extra or []) + ["tsv"]
    env = dict(os.environ)
    env.setdefault("OMP_THREAD_LIMIT", "1")
    limit = ocr_timeout()
    try:
        # a tesseract that never returns used to hold the read, and with it the server's only read slot, for
        # ever (critic RISK-1): past the limit the child is killed and the read stops, saying so
        subprocess.run(cmd, check=True, capture_output=True, env=env, timeout=limit)
    except subprocess.TimeoutExpired as e:
        raise OcrTooLong(f"Reading the printed text took more than {limit:g} seconds, so this sheet was not read. "
                         "Press Re-read to try again.") from e
    with open(outbase + ".tsv", encoding="utf-8", errors="replace") as f:
        return f.read()


def _parse_tsv(tsv: str) -> list[dict]:
    rows = []
    lines = tsv.splitlines()
    if not lines:
        return rows
    header = lines[0].split("\t")
    for ln in lines[1:]:
        parts = ln.split("\t")
        if len(parts) != len(header):
            continue
        r = dict(zip(header, parts))
        try:
            if int(r["level"]) != 5:
                continue
            conf = float(r["conf"])
        except ValueError:
            continue
        text = r["text"].strip()
        if not text or conf < 0:
            continue
        rows.append({"img": int(r["page_num"]) - 1, "block": int(r["block_num"]), "par": int(r["par_num"]),
                     "line": int(r["line_num"]), "word": int(r["word_num"]), "left": int(r["left"]),
                     "top": int(r["top"]), "w": int(r["width"]), "h": int(r["height"]), "conf": conf, "text": text})
    return rows


def run_batch(images: list[np.ndarray], psm: int, extra: list[str] | None = None, workers: int = 4) -> list[list[dict]]:
    """OCR a list of grayscale arrays; returns per-image word rows (tsv dicts)."""
    if not images:
        return []
    check_current()                      # a read told to stop (readslot) starts no new OCR batch
    workdir = tempfile.mkdtemp(prefix="cf_ocr_")
    try:
        paths = []
        for i, arr in enumerate(images):
            p = os.path.join(workdir, f"img_{i:05d}.png")
            Image.fromarray(arr).save(p)
            paths.append(p)
        n = len(paths)
        workers = max(1, min(workers, n, (os.cpu_count() or 2)))
        chunks = [paths[i::workers] for i in range(workers)]
        chunks = [c for c in chunks if c]
        per_img: list[list[dict]] = [[] for _ in range(n)]

        def job(chunk):
            tsv = _run_tesseract(chunk, psm, extra, workdir)
            rows = _parse_tsv(tsv)
            for r in rows:
                gi = int(os.path.basename(chunk[r["img"]])[4:9])
                r["img"] = gi
                per_img[gi].append(r)

        with ThreadPoolExecutor(max_workers=len(chunks)) as ex:
            list(ex.map(job, chunks))
    finally:
        # also when a tesseract run was killed past its time: its tiles never stay behind in TEMP (critic 2)
        shutil.rmtree(workdir, ignore_errors=True)
    return per_img


# --------------------------------------------------------------------------
def ocr_page_tiles(doc: Document, page: int, dpi: float = 200, tile: int = 1800, overlap: int = 160,
                   workers: int = 4) -> list[OcrWord]:
    """Whole-page sparse OCR (psm 11) in overlapping tiles. Returns words in page points."""
    gray = doc.render_array(page, dpi)
    H, W = gray.shape
    step = tile - overlap
    tiles = []
    origins = []
    y = 0
    while True:
        x = 0
        while True:
            sub = gray[y:y + tile, x:x + tile]
            if sub.size and (sub < 200).any():
                tiles.append(np.ascontiguousarray(sub))
                origins.append((x, y))
            if x + tile >= W:
                break
            x += step
        if y + tile >= H:
            break
        y += step
    rows_per = run_batch(tiles, psm=11, workers=workers)
    f = 72.0 / dpi
    words: list[OcrWord] = []
    for ti, rows in enumerate(rows_per):
        ox, oy = origins[ti]
        for r in rows:
            L, T, w, h = r["left"] + ox, r["top"] + oy, r["w"], r["h"]
            if h < 4 or w < 3:
                continue
            words.append(OcrWord(text=r["text"], conf=r["conf"], bbox=(L * f, T * f, (L + w) * f, (T + h) * f),
                                 page_img=ti, line_key=(ti, r["block"], r["par"], r["line"]), px_box=(L, T, L + w, T + h)))
    return _dedupe(words)


def _dedupe(words: list[OcrWord]) -> list[OcrWord]:
    """Words read twice in tile overlaps: keep the higher-confidence copy."""
    out: list[OcrWord] = []
    words = sorted(words, key=lambda w: -w.conf)
    centers = []
    for w in words:
        cx = 0.5 * (w.bbox[0] + w.bbox[2])
        cy = 0.5 * (w.bbox[1] + w.bbox[3])
        dup = False
        for (x, y, t) in centers:
            if abs(x - cx) < 3 and abs(y - cy) < 3 and t == w.text:
                dup = True
                break
        if not dup:
            centers.append((cx, cy, w.text))
            out.append(w)
    return out


def group_lines(words: list[OcrWord], max_gap_factor: float = 1.6) -> list[list[OcrWord]]:
    """Group OCR words into text lines: same tesseract line key, or horizontally adjacent
    boxes of similar height with a gap under `max_gap_factor` x height."""
    by_key: dict[tuple, list[OcrWord]] = {}
    for w in words:
        by_key.setdefault(w.line_key, []).append(w)
    lines: list[list[OcrWord]] = []
    for key, ws in by_key.items():
        ws.sort(key=lambda w: w.bbox[0])
        cur = [ws[0]]
        for w in ws[1:]:
            prev = cur[-1]
            h = max(prev.bbox[3] - prev.bbox[1], w.bbox[3] - w.bbox[1])
            vert_ok = abs((prev.bbox[1] + prev.bbox[3]) - (w.bbox[1] + w.bbox[3])) < 0.8 * h
            gap = w.bbox[0] - prev.bbox[2]
            if vert_ok and gap < max_gap_factor * h:
                cur.append(w)
            else:
                lines.append(cur)
                cur = [w]
        lines.append(cur)
    return lines


# --------------------------------------------------------------------------
def gap_patch(page_arr: np.ndarray, dpi: float, center: tuple, angle: float, length: float,
              half_h: float = 7.0, pad: float = 7.0) -> np.ndarray | None:
    """Cut the rotated rectangle around a line gap out of a pre-rendered page and
    return it upright (gap horizontal)."""
    cx, cy = center
    half_l = length / 2 + pad
    f = dpi / 72.0
    c, s = math.cos(angle), math.sin(angle)
    corners = [(cx + dx * c - dy * s, cy + dx * s + dy * c) for dx in (-half_l, half_l) for dy in (-half_h, half_h)]
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    H, W = page_arr.shape
    x0, y0 = int(max(0, (min(xs) - 1) * f)), int(max(0, (min(ys) - 1) * f))
    x1, y1 = int(min(W, (max(xs) + 1) * f)), int(min(H, (max(ys) + 1) * f))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    img = Image.fromarray(page_arr[y0:y1, x0:x1])
    rot = img.rotate(math.degrees(angle), expand=True, fillcolor=255, resample=Image.BICUBIC)
    pw, ph = int(2 * half_l * f), int(2 * half_h * f)
    Wr, Hr = rot.size
    box = (max(0, (Wr - pw) // 2), max(0, (Hr - ph) // 2), min(Wr, (Wr + pw) // 2), min(Hr, (Hr + ph) // 2))
    out = np.asarray(rot.crop(box))
    if out.size == 0:
        return None
    return np.pad(out, ((12, 12), (16, 16)), constant_values=255)


def ocr_gap_patches(doc: Document, page: int, gaps, dpi: float = 300, workers: int = 4,
                    min_ink: float = 0.004, page_arr: np.ndarray | None = None) -> dict[int, list[tuple[str, float, bool]]]:
    """OCR every gap both upright and flipped. Returns {gap_id: [(text, conf, flipped), ...]}."""
    if not gaps:
        return {}
    if page_arr is None:
        page_arr = doc.render_array(page, dpi)
    patches = []
    meta = []
    for g in gaps:
        ang = g.angle
        # text is normally placed to read left-to-right / bottom-to-top: normalise to (-90, 90]
        a = math.degrees(ang)
        if a > 90 or a <= -90:
            ang = ang + math.pi
        p = gap_patch(page_arr, dpi, g.center, ang, g.length)
        if p is None:
            continue
        core = p[12:-12, 16:-16]
        if core.size == 0 or (core < 160).mean() < min_ink:
            continue
        patches.append(p)
        meta.append((g.id, False))
        patches.append(np.ascontiguousarray(np.rot90(p, 2)))
        meta.append((g.id, True))
    if not patches:
        return {}
    rows_per = run_batch(patches, psm=7, extra=["-c", "tessedit_char_whitelist=0123456789.+-"], workers=workers)
    out: dict[int, list[tuple[str, float, bool]]] = {}
    for (gid, flipped), rows in zip(meta, rows_per):
        if not rows:
            continue
        rows.sort(key=lambda r: r["left"])
        text = "".join(r["text"] for r in rows)
        conf = float(np.mean([r["conf"] for r in rows]))
        out.setdefault(gid, []).append((text, conf, flipped))
    return out


def ink_gray(rgb: np.ndarray, box_px: tuple) -> float:
    """Luminance (0..1) of the text's own ink inside a box of an RGB render: the darkest fifth of
    the dark pixels. The median of all dark pixels was the background where a label sits on a
    screened fill (the hotel site C3: black proposed spot grades over gray parking fill read 0.51 to
    0.80 and were sided "existing"; their glyph ink was 20-31 of 255)."""
    x0, y0, x1, y1 = [int(v) for v in box_px]
    sub = rgb[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if sub.size == 0:
        return 0.0
    lum = 0.299 * sub[:, :, 0] + 0.587 * sub[:, :, 1] + 0.114 * sub[:, :, 2]
    dark = lum[lum < 215]
    if dark.size == 0:
        return 1.0
    return float(np.percentile(dark, 20)) / 255.0

def render_cluster(cluster, strokes_by_idx: dict, dpi: float = 300, pad_pt: float = 2.0,
                   min_px: float = 2.2, fill_closed: bool = False) -> np.ndarray | None:
    """Draw a word from its own vector strokes, upright, on a clean canvas.
    Nothing else from the sheet (lines, leaders, hatch) can get into the crop.

    A path the PDF both fills and strokes (`also_filled`) is drawn FILLED with its outline, as
    the sheet prints it. It used to be drawn as its outline alone (`filled` is kept for
    fill-only paths, so that a shape with an outline stays linework), and a word set in such
    letters came out hollow: the hotel site C3 prints its contour heights that way, and one
    "5990" was read 5980, 5990, 3990 or 9990 from one picture size to the next. A closed loop
    of a stroke font (the 0 of a single-line letter) is stroked only and stays a line.
    `fill_closed` fills every closed stroke (for a look at a word; not used by the reader)."""
    f = dpi / 72.0
    u = np.array([math.cos(cluster.angle), math.sin(cluster.angle)])
    n = np.array([-u[1], u[0]])
    c = np.array(cluster.center)
    W = int((cluster.length + 2 * pad_pt) * f) + 2
    H = int((cluster.height + 2 * pad_pt) * f) + 2
    if W < 6 or H < 6 or W > 4000 or H > 600:
        return None
    canvas = np.zeros((H, W), dtype=bool)
    fill_mask = np.zeros((H, W), dtype=bool)
    lines = Image.new("L", (W, H), 0)
    dl = ImageDraw.Draw(lines)
    # the word's letters (glyphs.find_text_clusters): in outline lettering the open strokes of the
    # line the word is printed on are the word's, and are not shown to the OCR
    for idx in (getattr(cluster, "letter_idx", None) or cluster.stroke_idx):
        s = strokes_by_idx.get(idx)
        if s is None:
            continue
        q = s.pts - c
        pts = [(float(q_ @ u) * f + W / 2, float(q_ @ n) * f + H / 2) for q_ in q]
        solid = getattr(s, "also_filled", False) and s.closed     # (a reading kept from before has no such field)
        if (s.filled or solid or (fill_closed and s.closed)) and len(pts) >= 3:
            m = Image.new("L", (W, H), 0)
            ImageDraw.Draw(m).polygon(pts, fill=255)
            fill_mask ^= np.asarray(m) > 0            # even-odd: holes in 0, 6, 8, 9 stay open
            if solid:                                 # ... and its outline, at the width it is printed
                dl.line(pts + [pts[0]], fill=255, width=max(1, int(round(s.width * f))), joint="curve")
        elif len(pts) >= 2:
            w = int(round(max(min_px, s.width * f)))
            dl.line(pts, fill=255, width=w, joint="curve")
            if s.closed:
                dl.line([pts[-1], pts[0]], fill=255, width=w)
    canvas |= fill_mask
    canvas |= np.asarray(lines) > 0
    out = np.where(canvas, 0, 255).astype(np.uint8)
    return np.pad(out, ((10, 10), (14, 14)), constant_values=255)


def ocr_text_clusters(page_arr: np.ndarray | None, dpi: float, clusters, workers: int = 4, pad: float = 2.5,
                      whitelist: str | None = None, strokes: list | None = None) -> None:
    """OCR each glyph cluster upright and flipped; fills cluster.text/conf/flipped.
    With `strokes` the word is drawn from its own vectors (preferred); otherwise it is
    cut out of the page render."""
    if not clusters:
        return
    sidx = {s.idx: s for s in strokes} if strokes is not None else None
    patches = []
    meta = []
    for c in clusters:
        p = None
        if sidx is not None:
            p = render_cluster(c, sidx, dpi)
        if p is None and page_arr is not None:
            p = gap_patch(page_arr, dpi, c.center, c.angle, c.length, half_h=c.height / 2 + pad, pad=pad + 1.5)
        if p is None:
            continue
        patches.append(p)
        meta.append((c, False))
        patches.append(np.ascontiguousarray(np.rot90(p, 2)))
        meta.append((c, True))
    # pass 1: free text (tags, notes); pass 2: digits only (contour labels, spot grades)
    rows_free = run_batch(patches, psm=7, extra=None, workers=workers)
    rows_dig = run_batch(patches, psm=7, extra=["-c", "tessedit_char_whitelist=0123456789.+-"], workers=workers)
    reads: dict = {}                # (word, read from the other side) -> [score, text, confidence, is a number]
    for (c, flipped), rf, rd in zip(meta, rows_free, rows_dig):
        text, conf, numeric = _pick_read(rf, rd)
        if text:
            reads[(c.id, flipped)] = [_read_score(text, conf, numeric, flipped), text, conf, numeric]
    if sidx is not None:
        _second_look(clusters, sidx, dpi, reads, workers)
    for c in clusters:
        cand = [(reads[(c.id, fl)], fl) for fl in (False, True) if (c.id, fl) in reads]
        if not cand:
            continue
        (score, text, conf, numeric), flipped = max(cand, key=lambda t: t[0][0])
        c.text = text
        c.conf = conf
        c.flipped = flipped
        # THE READ FROM THE OTHER SIDE IS KEPT where both sides read as a number: a height printed
        # along its line may stand either way up, and "5990" read from the wrong side is "0665",
        # a number too. Which one the sheet prints is decided where the sheet's own range of
        # heights is known (labels.upright_heights), not here by a preference for one side.
        other = next((r for r, fl in cand if fl != flipped), None)
        c.alt_text, c.alt_conf = (other[1], other[2]) if (other is not None and other[3] and numeric) else ("", 0.0)


def _read_score(text: str, conf: float, numeric: bool, flipped: bool) -> float:
    score = conf + (20.0 if numeric else (8.0 if _text_plausible(text) else 0.0))
    if flipped:
        score -= 20.0          # labels are placed to read from the bottom / right of the sheet
    return score


def _meaning(text: str):
    """What a read SAYS, for counting the looks that agree: the number and its tag ("92.82 H.P."
    and "92.82 HP" say one thing). None for a read that is no height."""
    from .labels import clean_read, parse_text
    p = parse_text(clean_read(text))
    if p["value"] is None or p["kind"] in ("datum", "scale", "percent", "station"):
        return None
    return (round(float(p["value"]), 3), p["tag"])


def _second_look(clusters, sidx: dict, dpi: float, reads: dict, workers: int) -> None:
    """A NUMBER IS READ MORE THAN ONCE. One picture of a word gives one read, and the read of a
    digit turns on a degree of tilt or two points of margin: the hotel site C3, one printed "5990"
    read 5980 in a tight crop and 5990 in every other; a "5991" read 5997 level and 5991 three
    degrees either side. Every read that is a number (from either side of the word) is made
    three times more - with a wider margin, and turned 3 degrees each way - and the reads vote;
    a tie stays with the first read. A height is a number that goes into a volume: it is worth
    four looks.
    A NUMBER WITH ITS TAG is a height as well ("92.82 H.P.", a spot grade), and the looks are
    counted by what they SAY - the number and the tag - not by their letters: the same sheet's
    "92.82 H.P." read "92.82 H.-P." in the first picture (no spot grade) and "92.82 H.P.",
    "92.82 H.P.", "92.82 HP." in the three others. Such a word is looked at again from the side
    it reads best from (its letters say which way up it stands); two looks that agree are needed
    to overrule a first read that says nothing."""
    import re
    from types import SimpleNamespace
    from .labels import clean_read, parse_text
    turn = math.radians(3.0)
    by_id = {c.id: c for c in clusters}
    patches, owner = [], []
    for (cid, flipped), (score, text, _conf, numeric) in reads.items():
        c = by_id.get(cid)
        if c is None:
            continue
        if not numeric:
            other = reads.get((cid, not flipped))
            if len(text) > 24 or not re.search(r"\d{2,5}", text) or (other is not None and other[0] > score):
                continue
            # a slope, a station, a scale, a datum note: the read says what the word is
            if parse_text(clean_read(text))["kind"] in ("percent", "station", "scale", "datum"):
                continue
        for da in (0.0, turn, -turn):
            v = SimpleNamespace(angle=c.angle + da, center=c.center, length=c.length + 3.0, height=c.height + 3.0,
                                stroke_idx=c.stroke_idx, letter_idx=getattr(c, "letter_idx", None))
            p = render_cluster(v, sidx, dpi, pad_pt=2.5)
            if p is None:
                continue
            patches.append(np.ascontiguousarray(np.rot90(p, 2)) if flipped else p)
            owner.append((cid, flipped))
    if not patches:
        return
    rows_free = run_batch(patches, psm=7, extra=None, workers=workers)
    rows_dig = run_batch(patches, psm=7, extra=["-c", "tessedit_char_whitelist=0123456789.+-"], workers=workers)
    more: dict = {}
    for key, rf, rd in zip(owner, rows_free, rows_dig):
        text, conf, numeric = _pick_read(rf, rd)
        if text:
            more.setdefault(key, []).append((text, conf, numeric))
    for key, again in more.items():
        _score, first, conf, numeric = reads[key]
        said = _meaning(first)
        votes: dict = {} if said is None else {said: [(first, conf, numeric)]}
        for text, cf, num in again:
            m = _meaning(text)
            # (a number's looks are numbers: a look that reads a word has no vote on it)
            if m is not None and (num or not numeric):
                votes.setdefault(m, []).append((text, cf, num))
        if not votes:
            continue
        top = max(len(v) for v in votes.values())
        winners = [m for m, v in votes.items() if len(v) == top]
        if said in winners or top < 2:
            continue
        win = max(winners, key=lambda m: float(np.mean([v[1] for v in votes[m]])))
        text, _cf, num = max(votes[win], key=lambda v: v[1])
        cf = float(np.mean([v[1] for v in votes[win]]))
        reads[key] = [_read_score(text, cf, num, key[1]), text, cf, num]


_NUMLIKE = {"S": "5", "s": "5", "O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "B": "8", "Z": "2", "z": "2",
            "G": "6", "g": "9", "q": "9", "A": "4", "T": "7", "b": "6", "D": "0", "Q": "0"}
_RE_NUM = None


def _pick_read(rf: list, rd: list) -> tuple[str, float, bool]:
    """Choose between the free read and the digits-only read of one word.
    Returns (text, confidence, is_numeric)."""
    import re
    global _RE_NUM
    if _RE_NUM is None:
        _RE_NUM = re.compile(r"^\d{1,5}(\.\d{1,3})?$")
    from .labels import clean_read

    def join(rows):
        rows = sorted(rows, key=lambda r: r["left"])
        return " ".join(r["text"] for r in rows).strip(), (float(np.mean([r["conf"] for r in rows])) if rows else 0.0)

    free, cf = join(rf) if rf else ("", 0.0)
    dig, cd = join(rd) if rd else ("", 0.0)
    free_c = clean_read(free) if free else ""
    dig_c = clean_read(dig) if dig else ""
    conf = max(cf, cd)
    core = free_c.replace(" ", "")
    n_digit = sum(ch.isdigit() for ch in core)
    if core and _RE_NUM.match(core):
        return free_c, cf if cf > 0 else conf, True
    # A SLOPE KEEPS ITS PERCENT SIGN. The digits-only pass has no sign to read and reads a digit
    # ("1.83%" -> "1.834"), and a digits read as long as the free read was taken for the number
    # (below): a slope became a number whenever the two lengths happened to agree.
    if re.fullmatch(r"\d{1,3}(\.\d{1,3})?%", core):
        return free, cf, False
    # a mostly-digit read with stroke-font look-alikes (S for 5, O for 0 ...)
    if core and n_digit >= 0.5 * len(core):
        mapped = "".join(_NUMLIKE.get(ch, ch) for ch in core)
        if _RE_NUM.match(mapped):
            return mapped, conf, True
        # a colon between the feet and the hundredths is the decimal point with something over
        # it (the hotel site C3: a leader's arrow head lands on the point of "88.93 FL"; the free
        # read is "88:93", the digits-only read "88393")
        if re.fullmatch(r"\d{2,4}:\d{2}", core):
            return core.replace(":", "."), conf, True
        if dig_c and _RE_NUM.match(dig_c.replace(" ", "")) and len(dig_c.replace(" ", "")) == len(core):
            return dig_c.replace(" ", ""), conf, True
    # tagged numbers ("558.5 BW") and everything else keep the free read
    if free:
        return free, cf, False
    if dig_c and _RE_NUM.match(dig_c.replace(" ", "")):
        return dig_c.replace(" ", ""), cd, True
    return "", 0.0, False


def _text_plausible(t: str) -> bool:
    import re
    return bool(re.search(r"\d", t)) and not re.search(r"[^0-9A-Za-z.,'\"%=+\-/() ]", t)
