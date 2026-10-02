"""Mark-bubble counting on SCANNED architectural plans.

A "mark bubble" (also "tag") is the little outlined shape a drafter puts
next to an opening with a short code inside it.  On the sheet this module
was built against - a client's apartment window set, one architect - the
sheet's OWN "SYMBOL LEGEND" block spells the convention out, so none of it
is an industry guess:

    hexagon with W#      WINDOW TAG        <- the window estimator's unit
    hexagon with SF#     STOREFRONT TAG
    stadium with D###    DOOR TAG (rating below)
    diamond with L##     WALL TAG          (partition type - not an opening)
    circle with #/A###   detail/section callout (a cross-reference)

That legend is the ground for every shape assumption below.  Note two
consequences that a generic "circle = door, hexagon = window" rule gets
wrong here: the door tag is a STADIUM (rounded rectangle), not a circle,
and the storefront tag is the SAME hexagon as the window tag - only the
code inside separates them.  So the SHAPE finds candidates and the CODE
decides what each candidate is.

Why not the vector path (server.symbols_suggest)?  These PDFs are 300-dpi
print-and-scan images: no text layer, no vectors, nothing to group.  This
module works on the page raster instead.

Pipeline
--------
    render 300 dpi -> orient (a landscape sheet scanned onto a portrait
    page) -> deskew -> seed a tag template off the sheet itself -> match it
    in GRAYSCALE across the page -> gate on ring contrast -> isolate each
    bubble's interior -> group identical codes -> read one averaged image
    per group -> map back to page points for the UI.

Every constant below was measured on the marked-up window plans of the apartment set
page 1 at 300 dpi; the evidence is in out/verify/2026-08-21/ (the marks folder).

  * The tag is 31 x 35 px (0.10 x 0.12 in on paper), ~2 px stroke, ~12 px
    cap-height text inside.
  * INK_LEVEL: the outline only survives a narrow threshold band.  At
    V<170 it breaks into arcs; at V<235 it fattens into the wall beside it.
    210 sits mid-band.  This mask is used to FIND seeds, not to match.
  * MATCH_MIN: with grayscale matching the real hexagons on page 1 score
    0.70-1.00 and the nearest confusable class - the diamond WALL tags -
    tops out around 0.60.  Swept 0.58 / 0.62 / 0.65 over all five floor
    plans: 0.62 and 0.65 give identical mark counts, and 0.58 buys one
    extra W1L on page 3 at the cost of 19 extra wall tags on page 1.  0.62
    it is - low enough to take the faint tags the band gate now protects,
    high enough to leave the diamonds out.

Two things this design buys, both measured rather than assumed:

  * Highlighter immunity.  The client marks his prints with cyan/pink/
    orange highlighter.  Everything reads the HSV *value* channel (max of
    R,G,B), where saturated highlighter is still "white".  On page 1, 0.60%
    of the sheet is highlighter and only 0.015% is both highlighter-coloured
    and below the ink threshold.
  * Faint-print tolerance.  Matching in grayscale rather than on the binary
    mask is contrast-normalised, so a lightly-printed tag still correlates:
    the two palest tags on page 1 score 0.46/0.47 binary but 0.77/0.81
    grayscale.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import cv2
import numpy as np

# ---------------------------------------------------------------- constants

RENDER_DPI = 300          # the scans are 300 dpi; rendering higher only blurs
INK_LEVEL = 210           # seed-finding threshold - measured band, not a guess
MATCH_MIN = 0.62          # grayscale correlation floor, with the band gate on
RING_CONTRAST = 22        # ring must be this much darker than the corners
BAND_MIN = 8              # ...and so must EVERY third of it, top to bottom
NMS_FRAC = 0.62           # centres closer than this x tag width are one tag
MAX_SKEW_DEG = 6.0
MAX_TEMPLATES = 8         # matching cost is linear in these
TAG_MIN_PX, TAG_MAX_PX = 14, 70    # plausible tag bbox at 300 dpi
CLUSTER_LINK = 0.42       # code_dist between two pictures of the same code

_LOOKALIKE = str.maketrans({"I": "1", "L": "1", "O": "0", "Q": "0", "S": "5",
                            "Z": "2", "B": "8", "G": "6", "T": "1", "J": "1"})


@dataclass
class Tag:
    """One detected mark bubble."""
    x: int                       # bbox in the upright working raster, px
    y: int
    w: int
    h: int
    score: float
    code: str = ""
    off_schedule: str = ""       # a confident read that is not in the schedule
    group: int = -1              # index of the appearance group it read from
    cx_pt: float = 0.0           # centre in ORIGINAL page points (for the UI)
    cy_pt: float = 0.0
    kind: str = "unknown"        # window | storefront | door | legend |
                                 # other-tag | unknown
    family: int = -1             # which size family matched it

    def as_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h,
                "score": round(self.score, 3), "code": self.code,
                "off_schedule": self.off_schedule,
                "group": self.group, "kind": self.kind, "family": self.family,
                "cx": round(self.cx_pt, 1), "cy": round(self.cy_pt, 1)}


@dataclass
class MarkPage:
    """Everything the counter learned about one page."""
    tags: list[Tag] = field(default_factory=list)
    orient: int = 0              # degrees the raster was turned CCW to stand up
    skew_deg: float = 0.0
    tag_size: tuple = (0, 0)
    seeds: int = 0
    groups: list[dict] = field(default_factory=list)
    img_size: tuple = (0, 0)
    notes: list[str] = field(default_factory=list)
    legend_boxes: list = field(default_factory=list)   # working px, see _legend_boxes
    legend_rot: int | None = None      # quarter turn that read the heading
    # What TRADE these bubbles belong to, or why we will not say.  See
    # `scope_state` - the default is ABSTAIN, and abstaining is the answer
    # on every sheet measured so far.
    scope: dict = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        """Schedule marks only - the number the estimator is here for."""
        out: dict[str, int] = {}
        for t in self.tags:
            if t.kind in ("legend", "other-tag"):
                continue
            out[t.code or "?"] = out.get(t.code or "?", 0) + 1
        return dict(sorted(out.items()))

    def off_schedule(self) -> dict[str, int]:
        """Bubbles read confidently as something the schedule does not list
        (on the estimator's floor plans: the WALL tags, L04/L06/L26)."""
        out: dict[str, int] = {}
        for t in self.tags:
            if t.kind == "other-tag":
                out[t.off_schedule] = out.get(t.off_schedule, 0) + 1
        return dict(sorted(out.items()))


# --------------------------------------------------------------- raster prep

def render(pdf_path: str, page: int, dpi: int = RENDER_DPI) -> np.ndarray:
    """Page -> BGR image at `dpi`.  No orientation work yet."""
    import pymupdf
    doc = pymupdf.open(pdf_path)
    try:
        pg = doc[page]
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(dpi / 72.0, dpi / 72.0),
                            colorspace="rgb")
        img = np.frombuffer(pix.samples, np.uint8).reshape(
            pix.height, pix.width, 3)[:, :, ::-1].copy()   # RGB -> BGR
    finally:
        doc.close()
    return img


def value(img: np.ndarray) -> np.ndarray:
    """HSV value channel: highlighter stays bright, ink stays dark."""
    return img.max(axis=2) if img.ndim == 3 else img


def ink_mask(img: np.ndarray, level: int = INK_LEVEL) -> np.ndarray:
    return (value(img) < level).astype(np.uint8)


def orient_ccw(img: np.ndarray) -> int:
    """Degrees CCW the raster must turn to stand its text up.

    These sheets are landscape drawings printed on 11x17 and scanned onto a
    portrait page, so the content is a quarter turn off and the PDF does not
    declare it (page.rotation is 0).  Ask tesseract's orientation detector,
    which is what it is for; fall back to the page's own aspect otherwise.
    """
    h, w = img.shape[:2]
    guess = 90 if h > w else 0
    try:
        import pytesseract
        small = cv2.resize(img, None, fx=0.34, fy=0.34,
                           interpolation=cv2.INTER_AREA)
        osd = pytesseract.image_to_osd(small, output_type=pytesseract.Output.DICT)
        cw = int(osd.get("rotate", 0)) % 360        # tesseract reports CW
        if cw in (0, 90, 180, 270) and float(osd.get("orientation_conf", 0)) >= 1.0:
            return (360 - cw) % 360
    except Exception:
        pass
    return guess


def _rot_ccw(img: np.ndarray, deg: int) -> np.ndarray:
    if deg == 90:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if deg == 180:
        return cv2.rotate(img, cv2.ROTATE_180)
    if deg == 270:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    return img


def skew_deg(ink: np.ndarray) -> float:
    """Scan skew from the long straight linework, in degrees.

    Deliberately local rather than reusing raster._deskew, so this module
    does not ride on another module's private helper.
    """
    lines = cv2.HoughLinesP((ink * 255), 1, np.pi / 1800, 200,
                            minLineLength=600, maxLineGap=6)
    if lines is None:
        return 0.0
    angs = []
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        a = math.degrees(math.atan2(float(y2 - y1), float(x2 - x1)))
        if abs(a) < MAX_SKEW_DEG:
            angs.append(a)
        elif abs(abs(a) - 90) < MAX_SKEW_DEG:
            angs.append(a - 90 if a > 0 else a + 90)
    if len(angs) < 6:
        return 0.0
    return float(np.median(angs))


def _compose(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Affine composition a . b, both 2x3."""
    return (np.vstack([a, [0, 0, 1]]) @ np.vstack([b, [0, 0, 1]]))[:2]


def upright(img: np.ndarray,
            force: int | None = None) -> tuple[np.ndarray, np.ndarray, int, float]:
    """Orient + deskew.

    Returns (working image, undo, orient_ccw_deg, skew_deg) where `undo` is a
    2x3 affine mapping WORKING pixel -> ORIGINAL render pixel, so detections
    can be reported in the page's own coordinates.
    """
    deg = orient_ccw(img) if force is None else force % 360
    rot = _rot_ccw(img, deg)
    h0, w0 = img.shape[:2]
    # working px -> original px for the quarter turn. Verified numerically
    # against cv2.rotate with a single marked pixel (they are easy to get
    # transposed, and a transposed overlay puts every marker in the wrong
    # place on the canvas while the counts stay right).
    if deg == 90:            # ROTATE_90_COUNTERCLOCKWISE
        r_undo = np.array([[0.0, -1.0, w0 - 1.0], [1.0, 0.0, 0.0]])
    elif deg == 270:         # ROTATE_90_CLOCKWISE
        r_undo = np.array([[0.0, 1.0, 0.0], [-1.0, 0.0, h0 - 1.0]])
    elif deg == 180:
        r_undo = np.array([[-1.0, 0.0, w0 - 1.0], [0.0, -1.0, h0 - 1.0]])
    else:
        r_undo = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])

    sk = skew_deg(ink_mask(rot))
    if abs(sk) < 0.15:
        return rot, r_undo, deg, sk
    h, w = rot.shape[:2]
    fwd = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), sk, 1.0)
    work = cv2.warpAffine(rot, fwd, (w, h), flags=cv2.INTER_LINEAR,
                          borderValue=(255, 255, 255))
    undo = _compose(r_undo, cv2.invertAffineTransform(fwd))
    return work, undo, deg, sk


def to_page_pt(undo: np.ndarray, dpi: int, x: float, y: float) -> tuple[float, float]:
    """Working px -> original page points (72 dpi), the UI's coordinates."""
    ox = undo[0, 0] * x + undo[0, 1] * y + undo[0, 2]
    oy = undo[1, 0] * x + undo[1, 1] * y + undo[1, 2]
    return ox * 72.0 / dpi, oy * 72.0 / dpi


# ------------------------------------------------------------- the template

def seed_tags(ink: np.ndarray) -> list[tuple]:
    """Unambiguous tags found by pure geometry, to cut a template from.

    A tag that happens to touch no linework traces as a clean convex ring:
    an outer contour of tag size, high solidity, a hole inside it, 5-7
    corners after simplification.  Most tags on a busy sheet do NOT qualify
    (they touch a wall) - which is exactly why the template match exists -
    but a handful always do, and a handful is all we need.
    """
    cnts, hier = cv2.findContours(ink * 255, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return []
    hier = hier[0]
    out = []
    for i, c in enumerate(cnts):
        x, y, w, h = cv2.boundingRect(c)
        if not (TAG_MIN_PX <= w <= TAG_MAX_PX and TAG_MIN_PX <= h <= TAG_MAX_PX):
            continue
        if hier[i][2] < 0:                      # no hole -> not an outline
            continue
        if not (0.7 < w / float(h) < 1.45):
            continue
        a = cv2.contourArea(c)
        hull = cv2.contourArea(cv2.convexHull(c))
        if not hull or a / hull < 0.92 or a / float(w * h) < 0.6:
            continue
        nv = len(cv2.approxPolyDP(c, 0.035 * cv2.arcLength(c, True), True))
        if not (5 <= nv <= 8):
            continue
        out.append((x, y, w, h))
    return out


def size_families(seeds: list[tuple]) -> list[tuple]:
    """Group the seeds into congruent-size families, biggest family first.

    A sheet carries several bubble shapes at once - on the estimator's A110 the
    window hexagon (31x35 px), the wall-tag diamond, the small keynote
    circle (~14x18 px) and the detail callout - and they are NOT
    interchangeable: mixing sizes into one template set makes the keynote
    circles outvote the window tags 12 seeds to 4.  Each family is matched
    and counted separately and the CODE decides which family is which.

    Returns [(w, h, [seed, ...]), ...].
    """
    from collections import Counter
    if not seeds:
        return []
    # The 3 px bucket only PROPOSES candidate centres and the order they are
    # tried in; family membership is the +/-3 px radius test on the line
    # below, and THAT is the proximity question. The bucket itself is NOT
    # bin-shift invariant - adding 1 or 2 px to every seed's w and h (same
    # relative geometry, every bin edge moved) changes the family list on all
    # 20 raster pages in the client's folder - so it must never be read as the
    # answer, only as a seed for the radius test.
    # Measured end-to-end over 19 of those pages, this bucket vs a bin-free
    # mode seek (every seed proposes its own centre, same radius membership,
    # same dedup): 10 pages identical, 9 different, and the differences run
    # BOTH ways - window set 2 A-2.0 24/45, 68/92, 101/193, 144/192, 101/153,
    # 97/136 (mode seek higher) but Windows-and-Balcony p1 48/35 and Interior
    # Storefront p0 46/37 (lower).
    #   * Apartment set p0-p5, the ONLY sheet family with a human-confirmed key
    #     (F68/F72), is identical on every page: 0/32/35/34/34/35. Its window
    #     hexagon is 31x35 and the next family up is 25x28 - many bin widths
    #     apart, so no edge can fall between them.
    #   * The 9 that move have no confirmed bubble count, and on window set 2
    #     every extra detection reads as unnamed, i.e. the mode seek is
    #     pulling in room tags and grid bubbles (F70's failure mode), not
    #     finding marks. Which number is right there is UNKNOWN.
    # So the bucket stays until a human-confirmed count exists for one of
    # those sheets. Do not swap it on principle.
    buckets = Counter((round(w / 3.0) * 3, round(h / 3.0) * 3)
                      for _, _, w, h in seeds)
    out = []
    for (bw, bh), _ in buckets.most_common():
        fam = [s for s in seeds if abs(s[2] - bw) <= 3 and abs(s[3] - bh) <= 3]
        if len(fam) < 2:
            continue
        w = int(np.median([s[2] for s in fam]))
        h = int(np.median([s[3] for s in fam]))
        if any(abs(w - o[0]) <= 2 and abs(h - o[1]) <= 2 for o in out):
            continue          # overlapping buckets picked the same family
        out.append((w, h, fam))
    return out


def _patch_box(x, y, w, h, tw, th) -> tuple[int, int]:
    return int(round(x + w / 2.0 - tw / 2.0)), int(round(y + h / 2.0 - th / 2.0))


# ---------------------------------------------------------------- the match

@dataclass
class Family:
    """One congruent bubble family and the masks learned from its seeds."""
    w: int
    h: int
    pad: int
    ring: np.ndarray             # bool: the outline stroke
    corner: np.ndarray           # bool: paper outside the bubble
    inner: np.ndarray            # bool: inside the bubble, clear of the stroke
    bands: list                  # the ring split into top / middle / bottom


def build_family(gray: np.ndarray, ink: np.ndarray, seeds: list[tuple],
                 tag_w: int, tag_h: int) -> tuple[Family, list[np.ndarray]] | None:
    """Learn the family's masks and cut its templates, from the sheet itself.

    Everything here is measured off the seeds rather than drawn from a
    shape assumption, so the same code counts hexagons, diamonds, circles
    and stadiums without being told which it is looking at.
    """
    pad = 3
    tw, th = tag_w + 2 * pad, tag_h + 2 * pad
    H, W = gray.shape[:2]
    gpats, bpats = [], []
    for (x, y, w, h) in seeds:
        x0, y0 = _patch_box(x, y, w, h, tw, th)
        if x0 < 0 or y0 < 0 or x0 + tw > W or y0 + th > H:
            continue
        gpats.append(gray[y0:y0 + th, x0:x0 + tw])
        bpats.append(ink[y0:y0 + th, x0:x0 + tw].astype(np.float32))
        if len(gpats) >= MAX_TEMPLATES:
            break
    if not gpats:
        return None
    mean = np.mean(bpats, axis=0)
    ring = mean >= 0.75          # ink in nearly every seed = the outline
    corner = mean <= 0.02        # white in every seed = outside the bubble
    if ring.sum() < 30 or corner.sum() < 30:
        return None
    # inside the bubble = fill the ring, then pull back past the stroke, so
    # neither the outline nor a wall line grazing the box can reach the code
    pts = cv2.findNonZero(ring.astype(np.uint8))
    inner = np.zeros((th, tw), np.uint8)
    cv2.fillConvexPoly(inner, cv2.convexHull(pts), 1)
    ero = max(3, int(round(min(tag_w, tag_h) * 0.16)) | 1)
    inner = cv2.erode(inner, np.ones((ero, ero), np.uint8)).astype(bool)
    if inner.sum() < 60:
        return None
    # the ring in three horizontal bands.  A bubble is inked all the way
    # round; the false positive that survives everything else on this
    # document - the client's solid marker bar redacting the permit stamp -
    # is inked in ONE band only.  Requiring all three is what let the match
    # threshold come down from 0.65 to 0.58 and pick up the W4 tags.
    b1, b2 = th // 3, 2 * th // 3
    bands = []
    for lo, hi in ((0, b1), (b1, b2), (b2, th)):
        b = np.zeros_like(ring)
        b[lo:hi] = ring[lo:hi]
        bands.append(b)
    if min(int(b.sum()) for b in bands) < 12:
        return None
    return Family(tag_w, tag_h, pad, ring, corner, inner, bands), gpats


def find_tags(gray: np.ndarray, fam: Family, gpats: list[np.ndarray],
              thr: float = MATCH_MIN) -> list[Tag]:
    """Grayscale multi-template match + non-max suppression + a ring gate.

    Several templates rather than one average: a template carries its own
    code text, so a single "W1L" template scores W1L tags 0.7-1.0 but W1R
    tags only 0.58-0.65.  Taking the max over templates cut from different
    tags puts every code on the same footing without blurring the ring.
    """
    th, tw = gpats[0].shape[:2]
    best = None
    for t in gpats:
        r = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED)
        best = r if best is None else np.maximum(best, r)
    ys, xs = np.where(best >= thr)
    if not len(ys):
        return []
    nms = max(4, int(fam.w * NMS_FRAC))
    out: list[Tag] = []
    kx: list[int] = []
    ky: list[int] = []
    for i in np.argsort(-best[ys, xs]):
        px, py = int(xs[i]), int(ys[i])
        if any(abs(px - a) < nms and abs(py - b) < nms for a, b in zip(kx, ky)):
            continue
        g = gray[py:py + th, px:px + tw]
        if g.shape != (th, tw):
            continue
        # a real bubble is a dark ring on clean paper, inked all the way
        # round: this kills the faint ghosts correlation alone accepts, and
        # the client's solid marker bars, which only ink one band
        cm = g[fam.corner].mean()
        if g[fam.ring].mean() - cm < RING_CONTRAST:
            continue
        if min(g[b].mean() for b in fam.bands) - cm < BAND_MIN:
            continue
        kx.append(px)
        ky.append(py)
        out.append(Tag(x=px + fam.pad, y=py + fam.pad, w=fam.w, h=fam.h,
                       score=float(best[py, px])))
    return out


# ------------------------------------------------------------------ the code

def isolate_code(gray: np.ndarray, t: Tag, fam: Family) -> np.ndarray | None:
    """The code text alone, with the bubble outline masked away.

    Erasing the outline is what makes the read possible at all: at 12 px
    cap height the ring's arcs get classified as glyphs and the string
    comes back as noise.  The mask is the family's own learned interior,
    not this patch's ink, so a wall line grazing the bubble cannot pull the
    mask open (it did, when the mask was the patch's convex hull).
    """
    y0, x0 = t.y - fam.pad, t.x - fam.pad
    h, w = fam.inner.shape[:2]
    if y0 < 0 or x0 < 0 or y0 + h > gray.shape[0] or x0 + w > gray.shape[1]:
        return None
    g = gray[y0:y0 + h, x0:x0 + w].astype(np.float32).copy()
    g[~fam.inner] = 0.0
    if g.max() < 40:
        return None
    return g


CANON = (48, 28)


def _canonical(g: np.ndarray, size: tuple = CANON) -> np.ndarray | None:
    """Normalise a code image so two instances of one code are the same
    picture: drop specks, crop to the ink, scale by ONE factor (aspect is
    information - "W4" is not a squashed "W1L") and centre on a fixed
    canvas."""
    m = (g >= max(40.0, g.max() * 0.45)).astype(np.uint8)
    n, _, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = [i for i in range(1, n) if st[i, cv2.CC_STAT_AREA] >= 6]
    if not keep:
        return None
    x0 = min(st[i, cv2.CC_STAT_LEFT] for i in keep)
    y0 = min(st[i, cv2.CC_STAT_TOP] for i in keep)
    x1 = max(st[i, cv2.CC_STAT_LEFT] + st[i, cv2.CC_STAT_WIDTH] for i in keep)
    y1 = max(st[i, cv2.CC_STAT_TOP] + st[i, cv2.CC_STAT_HEIGHT] for i in keep)
    sub = g[y0:y1, x0:x1]
    if sub.size == 0 or sub.shape[0] < 3 or sub.shape[1] < 3:
        return None
    sub = sub / max(sub.max(), 1.0)
    k = min(size[0] / sub.shape[1], size[1] / sub.shape[0])
    nw, nh = max(2, int(round(sub.shape[1] * k))), max(2, int(round(sub.shape[0] * k)))
    sub = cv2.resize(sub, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.zeros((size[1], size[0]), np.float32)
    oy, ox = (size[1] - nh) // 2, (size[0] - nw) // 2
    out[oy:oy + nh, ox:ox + nw] = sub
    return out


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    d = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
    return float((a * b).sum() / d) if d else 0.0


CODE_BANDS = 4


def code_dist(a: np.ndarray, b: np.ndarray, n: int = CODE_BANDS) -> float:
    """Distance between two code pictures, per character band.

    Whole-image correlation is the wrong metric for marks that differ in ONE
    character: W1L and W2L share the W and the L, so two thirds of the
    picture agrees and the difference is averaged away.  Measured on floor 3,
    where four real W2L tags exist: whole-image distance put W1L-vs-W2L as
    close as 0.142 while W1L-vs-W1L ran to 0.395, so no threshold could
    separate them - and the four W2L tags were silently absorbed into the
    W1L group and counted as W1L.  Scoring the worst of four column bands
    instead pulls them apart: W2L-vs-W2L tops out at 0.393 and W1L-vs-W2L
    never comes closer than 0.454.

    Splitting one code across several groups is harmless (each group reads
    the same code and the counts add up); merging two codes is not, so the
    metric is deliberately biased towards splitting.
    """
    w = a.shape[1] // n
    return max(1.0 - _ncc(a[:, k * w:(k + 1) * w], b[:, k * w:(k + 1) * w])
               for k in range(n))


def group_codes(canon: list[np.ndarray | None],
                link: float = CLUSTER_LINK) -> list[list[int]]:
    """Group tags whose code pictures are the same picture.

    The codes are printed type, so two W1L tags are pixel-siblings and a
    W1L and a W1R are not.  Grouping first, then reading ONE averaged image
    per group, is the whole trick: averaging 20 noisy 12-px scans produces a
    glyph tesseract can actually read, and it makes the read a per-CODE
    decision instead of 20 independent coin flips.

    Leader clustering (assign to the nearest centroid, else start a new
    group, then one re-assignment pass) rather than full agglomerative:
    the same answer on this data at a fraction of the cost, and a page can
    legitimately produce hundreds of candidate bubbles.
    """
    idx = [i for i, c in enumerate(canon) if c is not None]
    if not idx:
        return []
    cents: list[np.ndarray] = []
    members: list[list[int]] = []
    for i in idx:
        v = canon[i]
        bs, bj = 9.0, -1
        for j, c in enumerate(cents):
            s = code_dist(v, c)
            if s < bs:
                bs, bj = s, j
        if bj >= 0 and bs <= link:
            members[bj].append(i)
            n = len(members[bj])
            cents[bj] = cents[bj] * ((n - 1) / n) + v / n
        else:
            cents.append(v.copy())
            members.append([i])
    # one clean-up pass: an early member may now sit nearer another centroid
    for _ in range(2):
        moved = False
        for j, mem in enumerate(members):
            for i in list(mem):
                if len(mem) == 1:
                    continue
                bs, bj = 9.0, j
                for k, c in enumerate(cents):
                    s = code_dist(canon[i], c)
                    if s < bs:
                        bs, bj = s, k
                if bj != j and bs <= link:
                    mem.remove(i)
                    members[bj].append(i)
                    moved = True
        if not moved:
            break
        cents = [np.mean([canon[i] for i in m], axis=0) for m in members]
    members = [m for m in members if m]
    members.sort(key=len, reverse=True)
    return members


def _shift(a: np.ndarray, dx: int, dy: int) -> np.ndarray:
    out = np.zeros_like(a)
    h, w = a.shape
    sy0, sy1 = max(0, dy), min(h, h + dy)
    sx0, sx1 = max(0, dx), min(w, w + dx)
    out[sy0:sy1, sx0:sx1] = a[sy0 - dy:sy1 - dy, sx0 - dx:sx1 - dx]
    return out


def align_average(imgs: list[np.ndarray], rad: int = 2) -> np.ndarray:
    """Average code pictures after nudging each onto the running mean.

    Without this the average of twenty tags is a smear: each crop is
    normalised off its own ink bbox, and one stray pixel of the outline
    moves the bbox by one, so the glyphs drift a pixel or two apart.  Two
    passes of an integer +/-2 px search sharpen the average enough that
    tesseract reads it - measured on page 4, where the same group went from
    unreadable to "W1L".
    """
    if len(imgs) == 1:
        return imgs[0].copy()
    mean = np.mean(imgs, axis=0)
    for _ in range(2):
        aligned = []
        for im in imgs:
            bs, bshift = -2.0, (0, 0)
            for dy in range(-rad, rad + 1):
                for dx in range(-rad, rad + 1):
                    s = _ncc(_shift(im, dx, dy), mean)
                    if s > bs:
                        bs, bshift = s, (dx, dy)
            aligned.append(_shift(im, *bshift))
        new = np.mean(aligned, axis=0)
        if _ncc(new, mean) > 0.999:
            return new
        mean = new
    return mean


def _clean(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _lev(a: str, b: str) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def snap(raw: str, vocab: list[str]) -> tuple[str, float]:
    """Snap an OCR string onto the schedule's own vocabulary.

    Constrained reading, not free reading: the schedule says which codes can
    legally appear, so a read of "WIL" resolves to W1L (I-for-1 is the
    classic confusion at this glyph size) instead of inventing a mark.
    Returns (code, penalty); penalty 0 = exact, higher = weaker evidence.

    Note the whitelist route does NOT work here and was measured failing:
    with `tessedit_char_whitelist` set to the schedule's letters, tesseract
    drops the characters it actually saw ("WIL" -> "WL") and the string
    becomes unsnappable.  Read free, snap afterwards.
    """
    t = _clean(raw)
    if not t:
        return "", 100.0
    if t in vocab:
        return t, 0.0
    t2 = t.translate(_LOOKALIKE)
    for v in vocab:
        if t2 == v.translate(_LOOKALIKE):
            return v, 4.0
    # NO fuzzy fallback beyond the lookalike map.  It was measured turning a
    # real "W2L" - which the averaged picture reads correctly to a human but
    # which tesseract returns as "WA" - into a confident "W4", because W4 is
    # one edit away.  A wrong mark is worse for the estimator than no mark:
    # the panel shows an unread group he can name in one click, whereas a
    # wrong name is silent and ends up on the supplier order.
    return "", 100.0


_PSMS = ("7", "8", "6", "10", "13")


def read_image(g: np.ndarray, vocab: list[str],
               flip: bool = False) -> tuple[str, float, str, int]:
    """Read one isolated code picture.

    Returns (code, vote weight, raw read, how many PSMs agreed on the raw).
    """
    try:
        import pytesseract
    except Exception:
        return "", 0.0, "", 0
    if flip:
        g = cv2.rotate(g, cv2.ROTATE_180)
    v = (255 - g).clip(0, 255).astype(np.uint8)
    big = cv2.resize(v, None, fx=8, fy=8, interpolation=cv2.INTER_CUBIC)
    big = cv2.GaussianBlur(big, (0, 0), 2)
    _, bb = cv2.threshold(big, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bb = cv2.copyMakeBorder(bb, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)
    votes: dict[str, float] = {}
    raws: list[str] = []
    for psm in _PSMS:
        try:
            raw = pytesseract.image_to_string(bb, config=f"--psm {psm}").strip()
        except Exception:
            return "", 0.0, "", 0
        if not _clean(raw):
            continue
        raws.append(_clean(raw))
        code, pen = snap(raw, vocab)
        if code:
            votes[code] = votes.get(code, 0.0) + (2.0 if pen == 0 else
                                                  1.5 if pen <= 4 else 0.4)
    # the commonest free read, kept so an out-of-schedule bubble can SAY what
    # it looked like ("read L04, not in the schedule") instead of just "?".
    # `agree` counts how many page-segmentation modes produced it: a read
    # three or more of them agree on is a real string, just not one of this
    # schedule's marks - almost always a neighbouring tag family (the WALL
    # tags on the estimator's A110 read L04/L06 and land here).
    from collections import Counter
    top_raw, agree = ("", 0)
    if raws:
        top_raw, agree = Counter(raws).most_common(1)[0]
    if not votes:
        return "", 0.0, top_raw, agree
    code = max(votes, key=votes.get)
    return code, votes[code], top_raw, agree


# A read is only believed when the page-setter modes agree well enough:
# one exact vocabulary hit (2.0), or several lookalike-corrected ones.
# Measured: real groups on the estimator's A110 land at 2.0-6.0, while the keynote
# circles' noise tops out at 1.2.
READ_MIN_WEIGHT = 1.5


# ------------------------------------------------------------------- driver

# How a legend block is HEADED, enumerated from the corpus rather than
# guessed.  Over the 6,714 vector pages under ../TakeoffPro AI/{Plans,
# Plans 2,<contractor>} and the client folders (out/verify/2026-08-22/
# <scope>/work/headings.py, output in HEADINGS.txt) the heading of a block
# that keys a sheet's symbols reads:
#     LEGEND (173 lines) / "LEGEND:" (18)      - much the commonest form
#     <qualifier> LEGEND (24 distinct forms: SPOT ELEVATION LEGEND, LINE
#         STYLE LEGEND, SURVEY SYMBOL LEGEND, PLUMBING SYMBOLS LEGEND, ...)
#     <qualifier> SYMBOLS (COMMON FRAMING SYMBOLS:, ELECTRICAL SYMBOLS,
#         LITHOLOGIC SYMBOLS, WELL CONSTRUCTION SYMBOLS, ... - and window
#         set 2's FLOOR SYMBOLS)
# The pattern this function used to require - the word SYMBOL immediately
# followed by a word starting LEGEN - fires on 13 of the corpus's 502,992
# text lines, in 7 spellings.  The bare phrase "SYMBOL LEGEND" is ONE of
# those 13; the other 12 all carry a qualifier in front (SURVEY / SITE / DRY
# UTILITY / WATER UTILITY / SANITARY SEWER / STORM DRAIN SYMBOL LEGEND).
# The rule below fires on 358 lines in 59 spellings, of which 14 are plainly
# not headings ("VARIES BY LEGEND" x10, "ENGRAVED LEGEND" x2, "SEE KEYNOTE
# LEGEND", "LEGEND10").  On the estimator's window set 2 the old pattern fires on
# nothing at all: the block is headed FLOOR SYMBOLS, and it counted itself 34
# times across the six sheets.
#
# The invariant that IS stable across all of those is that the block is
# named by WHAT IT IS - the heading's LAST word is LEGEND(S) or SYMBOL(S).
# startswith(), not equality, because OCR clips the tail off a scan: the apartment
# set's heading comes back as SYMBOL (with a stray quote glued on the
# front) followed by LEGEN! , and window set 2's as FLOOR SYMBOLS.
_LEGEND_TAIL = ("LEGEN", "SYMBO")
_LEGEND_MAX_WORDS = 5        # the longest real form measured is 5 words
                             # ("SANITARY SEWER UTILITY SYMBOL LEGEND",
                             # "STORM DRAIN UTILITY SYMBOL LEGEND"), and the
                             # old rule DID catch those, so stopping at 4
                             # would regress two corpus sheets.  The cap is
                             # only there to keep a sentence out of the match.
# A legend prints ONE key per symbol type, so its key column is short: the
# biggest measured is 6 (window set 2 FLOOR SYMBOLS - the 100 door key, the A
# window key and the SD/CO/OS/DS sensor keys); the apartment set's SYMBOL LEGEND puts
# 1 counted-family key inside the box.  A box holding more than this many is
# not a key column, it is plan area, and it is REJECTED rather than allowed to
# drop real openings with no symptom.  That bound matters because the heading
# rule above casts a far wider net than the pattern it replaces - over the
# corpus text layer it matches 358 lines where the old one matched 13.
LEGEND_MAX_KEYS = 12
# How far LEFT of the heading's left edge the key column may start.  The keys
# and the heading share the block's left margin, so this is 0 when the OCR
# reads the heading whole; it is needed because the OCR clips a scan - on
# A-2.00 "FLOOR SYMBOLS" comes back as "DR SYMBOLS" and the anchor lands 47 px
# right of the keys.  Measured worst case 47 px over the six window set 2 and
# five apartment-set sheets; 120 is 2.5x that.  It only widens the SEARCH: the
# box returned is drawn round the seeds actually found.
_LEGEND_FIND_LEFT = 120
_LEGEND_PAD = 6
# The quarter turns tried, in order.  0 first because that is where the
# heading is when upright() got the page right, which is every apartment page.
_LEGEND_ROTS = (0, 90, 270, 180)


def _legend_lines(d: dict, f: float) -> list[tuple]:
    """psm-11 words -> heading candidates, in reading order.

    The trigger is the LAST word of the heading (LEGEND / SYMBOLS / ...);
    the ANCHOR the box hangs off is the FIRST, because heading and key
    column share the block's left margin.  Finding the first word means
    walking left from the trigger, and that walk is a baseline + gap test
    rather than tesseract's own line grouping: psm 11 sometimes puts
    "SYMBOL" and "LEGEND" in one line group and sometimes swallows the whole
    legend into one, and a rule that depends on which it did that run stops
    firing at random (measured on apartment set p4, where the block IS read but
    the grouping buried it).

    Returns [(text, x0, y0, x1, y1), ...] in FULL-scale px of the OCR frame.
    """
    ws = []
    for i, w in enumerate(d["text"]):
        t = _clean(w)
        if not t:
            continue
        ws.append((t, d["left"][i] / f, d["top"][i] / f,
                   (d["left"][i] + d["width"][i]) / f,
                   (d["top"][i] + d["height"][i]) / f))
    out = []
    for i, (t, x0, y0, x1, y1) in enumerate(ws):
        if not t.startswith(_LEGEND_TAIL):
            continue
        hh = max(1.0, y1 - y0)
        cy = (y0 + y1) / 2.0
        txt, ax0, ay0, ay1 = t, x0, y0, y1
        j, n = i - 1, 1
        while j >= 0 and n < _LEGEND_MAX_WORDS:
            pt, px0, py0, px1, py1 = ws[j]
            gap = ax0 - px1
            if abs((py0 + py1) / 2.0 - cy) > 0.6 * hh or not (0 <= gap <= 1.5 * hh):
                break
            txt, ax0, ay0, ay1 = pt + " " + txt, px0, min(ay0, py0), max(ay1, py1)
            j -= 1
            n += 1
        out.append((txt, int(ax0), int(ay0), int(x1), int(ay1)))
    out.sort(key=lambda t: (t[2], t[1]))
    return out


def _legend_boxes(img: np.ndarray,
                  seeds: list[tuple] = ()) -> tuple[list, int | None]:
    """Every symbol-legend block on the sheet, as boxes in `img` pixels.

    A legend's key symbols are real tags with real codes in them, so a
    legend counts itself unless it is excluded.

    A legend block IS a heading with a COLUMN OF SYMBOLS under it, each
    paired with descriptive text beside it.  The heading (see _LEGEND_TAIL)
    says WHERE to look; the symbols found there - seeds, the free-standing
    outlined shapes seed_tags returns - set how far the box actually
    reaches, left and down.  Seeds are a refinement, NOT a gate: measured on
    the apartment set, whose SYMBOL LEGEND prints 11 rows, exactly ONE row traces as
    a seed on p1 and NONE does on p2, p3, p4 or p5 - so requiring a seed
    would stop excluding a legend this module has excluded correctly since
    F72.  With no seed under it the search window stands as the box, which
    is what the function did before this change.

    Two more things this has to survive that the single-phrase version did
    not:

      * the heading is not one fixed phrase - see _LEGEND_TAIL above;
      * THE PAGE MAY NOT BE STANDING UP.  upright() asks tesseract's OSD
        which way is up, and OSD is a quarter turn wrong on five of the six
        window set 2 scans (measured 2026-08-22: it stood engine pages
        0,1,2,4,5 at 0/180 deg where upright is 90).  A heading rule that
        only reads `img` as handed to it can never fire on those pages, so
        the OCR is retried at each quarter turn until a heading appears and
        the box is mapped back through the exact 90-degree transform.  The
        turn that read it is returned so the caller can say which one did.

    A candidate whose heading sits INSIDE an already-accepted box is
    dropped: it is a row of that legend, not a new block.  Measured need -
    on A-2.30 the legend row "(N) DOOR SYMBOL" is itself a line ending in
    SYMBOL and would otherwise open a second box inside the first.

    Returns ([(x0, y0, x1, y1), ...], rotation_deg or None).
    """
    try:
        import pytesseract
    except Exception:
        return [], None
    h, w = img.shape[:2]
    # the heading is set large, so a half-scale page reads it fine and OCRs
    # in a couple of seconds instead of a couple of minutes
    f = 0.5
    v = value(img)
    for deg in _LEGEND_ROTS:
        rot = _rot_ccw(v, deg)
        rh, rw = rot.shape[:2]
        small = cv2.resize(rot, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        try:
            d = pytesseract.image_to_data(small, config="--psm 11",
                                          output_type=pytesseract.Output.DICT)
        except Exception:
            return [], None
        cands = _legend_lines(d, f)
        if not cands:
            continue
        # px of `img` <-> px of the ROTATED frame: the same quarter turn
        # upright() undoes (verified there against cv2.rotate).
        if deg == 90:
            undo = lambda x, y: (w - 1 - y, x)          # noqa: E731
            fwd = lambda X, Y: (Y, w - 1 - X)           # noqa: E731
        elif deg == 270:
            undo = lambda x, y: (y, h - 1 - x)          # noqa: E731
            fwd = lambda X, Y: (h - 1 - Y, X)           # noqa: E731
        elif deg == 180:
            undo = lambda x, y: (w - 1 - x, h - 1 - y)  # noqa: E731
            fwd = lambda X, Y: (w - 1 - X, h - 1 - Y)   # noqa: E731
        else:
            undo = lambda x, y: (x, y)                  # noqa: E731
            fwd = lambda X, Y: (X, Y)                   # noqa: E731
        rseeds = []
        for (sx, sy, sw, sh) in seeds:
            cx, cy = fwd(sx + sw / 2.0, sy + sh / 2.0)
            s = max(sw, sh) / 2.0
            rseeds.append((cx, cy, s))
        boxes, taken = [], []
        for _txt_, x, y, x1, y1 in cands:
            if any(bx0 <= x <= bx1 and by0 <= y <= by1
                   for bx0, by0, bx1, by1 in taken):
                continue
            # WHERE TO LOOK for the key column.  The 30/220/900 window is
            # the one this function has always used; the change is that the
            # symbols found inside it now set the box's real extent:
            #   * LEFT  - out to the leftmost key, because the OCR can clip
            #     the heading's first word off a scan and move its anchor
            #     right (measured: A-2.00's "FLOOR SYMBOLS" reads
            #     "DR SYMBOLS", anchor 47 px right of the keys it names, and
            #     the old -30 px margin missed all four of them);
            #   * BOTTOM - up to the lowest key, so a legend nine rows deep
            #     does not black out three inches of drawing under it.
            # With no symbol under the heading the window stands as it did
            # before, and LEGEND_MAX_KEYS is what stops it deleting a wall
            # of real openings.
            win = (x - _LEGEND_FIND_LEFT, y - 10, x + 220, y + 900)
            taken.append(win)
            # strictly BELOW the heading: the closed counters of the heading's
            # own letters trace as seeds too, and counting them collapses the
            # box onto the heading line (measured: it did, on A-2.50 and on
            # apartment set p4/p5, and the legend then counted itself again).
            col = [s for s in rseeds
                   if win[0] <= s[0] <= win[2] and y1 < s[1] <= win[3]]
            bx0 = min([x - 30] + [c[0] - c[2] - _LEGEND_PAD for c in col])
            bx1 = x + 220
            by0 = y - 10
            by1 = (min(y + 900, max(c[1] + c[2] for c in col) + _LEGEND_PAD)
                   if col else y + 900)
            xs, ys = zip(*(undo(px, py) for px in (bx0, bx1)
                           for py in (by0, by1)))
            boxes.append((max(0, int(min(xs))), max(0, int(min(ys))),
                          min(w, int(max(xs))), min(h, int(max(ys)))))
        if boxes:
            return boxes, deg
    return [], None


# ------------------------------------------------------- cross-family dedup

def dedup_across_families(tags: list[Tag]) -> list[Tag]:
    """Drop a second detection of the SAME printed symbol by another family.

    find_tags suppresses non-maxima INSIDE one family and nothing suppresses
    them across families, so when size_families legitimately hands the same
    population to two near-congruent families every symbol in it is emitted
    twice.  Measured on the estimator's window set 2 A-2.30 (engine page index 3 =
    printed sheet 4 of 6): families 39x43 (46 tags), 37x37 (54) and 43x42
    (44) produce 144 detections over 100 sites - 44 pairs, every pair one
    39x43 with one 43x42, centres at most 0.64 pt apart, and the 37x37
    circles never involved.  The other nine pages measured (window set 2
    p0/p1/p2/p4/p5, apartment set p1-p5) have ZERO overlapping pairs, including
    the four that also keep two families - so this is a positional test, not
    a size test, and it cannot merge two genuinely different symbols that
    happen to be similar sizes.

    NOT the same change as replacing size_families' 3 px bucket: the bucket
    is a legitimate seed for the +/-3 px radius test (see the comment there,
    which stands), and swapping it moves the family list on pages that have
    no confirmed count.  This drops duplicate SITES and leaves the bucket
    alone.

    The survivor is explicitly ranked - never first-writer-wins: the higher
    correlation score, then the family with more seeds behind its templates,
    then family order, then position, so the result does not depend on the
    order tags happen to arrive in.
    """
    order = sorted(range(len(tags)),
                   key=lambda i: (-tags[i].score, tags[i].family,
                                  tags[i].y, tags[i].x))
    keep: list[int] = []
    for i in order:
        t = tags[i]
        hit = False
        for j in keep:
            k = tags[j]
            nms = max(4, int(max(t.w, k.w) * NMS_FRAC))
            if (abs((t.x + t.w / 2.0) - (k.x + k.w / 2.0)) < nms
                    and abs((t.y + t.h / 2.0) - (k.y + k.h / 2.0)) < nms):
                hit = True
                break
        if not hit:
            keep.append(i)
    return [tags[i] for i in sorted(keep)]


# ------------------------------------------------------------- trade scoping

def scope_state(tags: list[Tag], trade: str | None) -> dict:
    """Whether these bubbles can be narrowed to one trade, or why not.

    THE DEFAULT IS TO ABSTAIN, and on every sheet measured so far abstaining
    is the answer.  Refusing to narrow, and saying so, beats guessing a scope
    and silently dropping real openings - measured against the estimator's answer key
    on window set 2 (out/verify/2026-08-22/, the truth SCORE.md, 285
    openings), where each of the three obvious geometric shortcuts is wrong:

      * SHAPE.  This sheet set's own FLOOR SYMBOLS legend says circle =
        (N) DOOR SYMBOL, hexagon = (N) WINDOW SYMBOL.  But 100 of the estimator's 285
        openings ARE circles - balcony doors 001 x75 and 003 x10, the one
        printed 002, storefront doors 004 x2 / 005 x1, and interior
        storefront doors 100-107 x11 - so "keep the hexagons" throws away 35%
        of his takeoff and recall falls 0.993 -> 0.646.
      * CODE SHAPE.  Letter vs number does not separate them either, for the
        same reason: 001-005 and 100-107 are numeric AND his, while 006-008,
        108-111, 200-202 and G01-G03 are numeric and somebody else's.
        Recall would fall to 0.649.
      * WHICH SCHEDULE.  All 40 of his codes and all 10 of the other trade's
        are printed on a schedule row of this project, so "is it scheduled"
        decides nothing at all.

    What DOES separate them is the schedule row's own material cell, read
    from the vector text layer of Window - Storefront - Door schedules.pdf
    (dumped in out/verify/2026-08-22/<scope>/SCHEDULES.txt): 001/003 VINYL
    "UNIT PATIO", 004/005 and 100-107 ALUM "WITH TEMPERED GLASS LITE",
    against 006-008 STEEL "EXIT STAIR"/"TRASH", 108-111 STEEL and 200-208
    SC,WOOD.  Over the 50 codes this project prints - the estimator's 40 and the other
    trade's 10 - that rule is right on 46; it is wrong on the three aluminium
    yard GATES G01-G03, which are ALUM but are not glazing work, and it has
    nothing to say about 002, whose row reads NOT USED.

    But that schedule lives in a DIFFERENT PDF from the plans, and on these
    sheets no bubble is named at all, so nothing here can act on it.  Two
    things have to arrive from outside this module first: (1) the schedule
    rows for the whole PROJECT, not just this file, and (2) the trade being
    taken off.  Until they do, this returns abstained and the caller shows
    every symbol it found.

    And even with them, a code the schedules do not list must come back
    "unknown", never "out" - dropping a real opening with no symptom is the
    one failure this engine must not have.  Measured on the 151 out-of-trade
    detections, reading each bubble independently at 8x: 90 read as a
    scheduled non-glazed door and would go, 49 read as nothing the schedules
    list and must stay, 12 read as an in-scope code.  Against that, of the
    283 true positives exactly ONE reads as a scheduled non-glazed door (a
    003 read as 008) - so a code-driven filter of this OCR quality costs one
    real opening in 285.
    """
    named = sum(1 for t in tags if t.code)
    counted = sum(1 for t in tags if t.kind != "legend")
    return {"state": "abstained", "trade": trade,
            "reason": ("no-code-read" if not named else
                       "no-trade-scope-supplied"),
            "counted": counted, "named": named, "unnamed": counted - named}


# ------------------------------------------------------- the sheet's schedule

# Plain English for a reader who is not an estimator: a "schedule" on an
# architectural sheet is a TABLE, not a calendar.  One row per mark, with the
# size, the material and (sometimes) how many of them the job needs.  The
# estimator counts the bubbles on the plan and checks the total against this
# table's QTY column; that cross-check is the whole point of reading it.

# The column that holds the mark.  Different offices head it differently -
# the apartment set's A830 says MARK, window set 2's A-6.00 says WINDOW TYPE - and
# both mean "the code you will see inside the bubbles".
_MARK_HEADS = ("MARK", "TYPE", "SYMBOL", "TAG")
_TYPE_PREFIX = ("WINDOW", "DOOR", "STOREFRONT", "GLAZING", "FRAME", "OPENING")
# A residential schedule heads its mark column "No." (a builder's house
# set A2.1: WINDOW SCHEDULE No. 1/2/3, DOOR SCHEDULE No. A/B/C/D, each
# with a QTY column). "No." is a mark head ONLY when a strong quantity head
# sits on the same row - alone it is the weak quantity head it always was.
_NUM_HEADS = ("NO", "NO.", "#", "NUMBER")
# The column that holds how many.  NO. is ambiguous (it also heads "DOOR NO.")
# so it is accepted only when nothing better is on the header row.
_QTY_HEADS = ("QTY", "QTY.", "QUANTITY", "COUNT", "TOTAL", "EA")
_QTY_WEAK = ("NO", "NO.", "#")

# A mark code: one to three letters then an optional number then an optional
# suffix letter (W1L, SF2, D101A), or a bare letter used as a type name
# (window set 2 heads its window types A..Z).
_MARK_RE = re.compile(r"^[A-Z]{1,3}-?\d{1,3}[A-Z]?$")
_LETTER_RE = re.compile(r"^[A-Z]{1,2}$")


def _txt(w) -> str:
    return str(w[4]).strip().upper()


# A wide table on a SKEWED scan does not have a flat header row. The estimator's
# apartment-set schedule is OCR'd off a 300 dpi scan that marks.upright measures
# at 2.2 deg, and its MARK and QUANTITY headers sit ~700 pt apart, so their
# y centres differ by ~28 pt - six times the flat y_tol. The row match has to
# widen with horizontal distance or a quantity column is simply never seen.
# 0.045 covers ~2.6 deg, above every skew measured on his sheets. On a real
# text layer dx-widening costs nothing because dy there is ~0.
SKEW_SLOPE = 0.045


def _row_tol(y_tol: float, dx: float) -> float:
    """y tolerance for two words claimed to be on the same table row."""
    return y_tol + SKEW_SLOPE * abs(dx)


def _is_qty_head(t: str) -> bool:
    """QTY column header, tolerant of OCR noise.

    A table rule touching the word comes back glued to it - the estimator's schedule
    OCRs as "LQUANTITY" - so a head token is accepted when it sits inside a
    slightly longer word as well as when it matches outright.
    """
    if t in _QTY_HEADS:
        return True
    core = "".join(c for c in t if c.isalpha())
    for q in _QTY_HEADS:
        qa = "".join(c for c in q if c.isalpha())
        if len(qa) >= 3 and core.endswith(qa) and len(core) - len(qa) <= 2:
            return True
    return False


def read_mark_schedule(words: list, y_tol: float = 5.0,
                       fuzzy: bool = False) -> dict:
    """Read a MARK/QTY (or WINDOW TYPE/QTY) schedule out of a page's words.

    Returns {"marks": [code, ...] in sheet order, "qty": {code: n},
             "box": (x0, y0, x1, y1) or None, "header": "MARK"|"WINDOW TYPE",
             "fuzzy": [raw, ...], "source": "text"|"ocr"|"none"}.

    QTY is OPTIONAL.  A schedule with no quantity column - window set 2's
    A-6.00 lists 22 readable window types and never says how many - is still
    worth reading, because the type list IS the legal vocabulary the bubbles
    must come from, and constraining the read to it is what stops OCR
    inventing marks at 12 px glyph height.

    `fuzzy` is for schedules that had to be OCR'd off a scan: the mark
    column comes back as WIL / WIR / WS rather than W1L / W1R / W5, so the
    same lookalike map used on the bubbles is applied here.  It is off by
    default because on a real text layer it can only do harm.
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
                        digits_ok=num_head, y_stop=y_stop)
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


def _schedule_title(words: list, hw, y_tol: float) -> str:
    """The word before SCHEDULE in the title above a "No." header (WINDOW /
    DOOR), or "" when no title is within 60 pt above it."""
    hx = (hw[0] + hw[2]) / 2.0
    best = ""
    for w in words:
        if _txt(w) != "SCHEDULE":
            continue
        wy = (w[1] + w[3]) / 2.0
        if not (hw[1] - 60 <= wy <= hw[1]):
            continue
        if abs((w[0] + w[2]) / 2.0 - hx) > 400:
            continue
        left = [m for m in words if abs((m[1] + m[3]) / 2.0 - wy) <= y_tol
                and m[2] <= w[0] + 1 and w[0] - m[2] < 40]
        if left:
            t = _txt(max(left, key=lambda m: m[2]))
            if t in _TYPE_PREFIX:
                best = t
    return best


# Characters an OCR pass confuses at drafting-text size, both directions.
_AMBIG = {"I": "1", "L": "1", "O": "0", "S": "5", "Z": "2", "B": "8", "G": "6",
          "1": "I", "0": "O", "5": "S", "2": "Z", "8": "B", "6": "G"}
_STRICT_MARK = re.compile(r"^[A-Z]{1,2}\d{1,3}[A-Z]?$")


def _fuzzy_mark(t: str) -> str | None:
    """Best mark-shaped reading of an OCR'd word, or None.

    A blanket translate is wrong - it turns "WIL" into "W11" - so every
    ambiguous character is tried both ways and the variant that fits the
    mark grammar (letters, then digits, then an optional suffix letter)
    wins.  "WIL" -> W1L, "WS" -> W5, "WIR" -> W1R.
    """
    amb = [i for i, c in enumerate(t) if c in _AMBIG]
    if not amb or len(amb) > 4 or len(t) > 5:
        return None
    for bits in range(1 << len(amb)):
        v = list(t)
        for k, i in enumerate(amb):
            if bits >> k & 1:
                v[i] = _AMBIG[t[i]]
        s = "".join(v)
        if _STRICT_MARK.match(s):
            return s
    return None


def _as_mark(t: str, fuzzy: bool, digits_ok: bool = False) -> str | None:
    """One word -> a mark code, or None. `digits_ok` admits a bare 1-2 digit
    number - only under a "No." header, where the numbers ARE the marks."""
    if _MARK_RE.match(t):
        return t
    if len(t) == 1 and t.isalpha():
        return t              # a bare type letter: window set 2 heads A..Z
    if digits_ok and t.isdigit() and len(t) <= 2:
        return t
    if fuzzy and t[:1].isalpha():
        return _fuzzy_mark(t)
    return None


def _harvest(words: list, hw, qcol, row: list, header: str,
             y_tol: float, fuzzy: bool, digits_ok: bool = False,
             y_stop: float | None = None) -> dict | None:
    """Rows under one header: mark codes in the mark column, qty beside;
    nothing at or below `y_stop` (the next table's header) is this table's."""
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
    # instead gave every mark on the estimator's schedule the same quantity (148).
    slope = 0.0
    if qcol is not None:
        hxc, hyc = (hw[0] + hw[2]) / 2.0, (hw[1] + hw[3]) / 2.0
        qyc = (qcol[1] + qcol[3]) / 2.0
        if abs(qcx - hxc) > 1.0:
            slope = (qyc - hyc) / (qcx - hxc)
    marks, raws, qty, y_max = [], [], {}, hw[3]
    letters_only = True
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
    if len(marks) < 2:
        return None
    if letters_only and len(marks) < 3:
        # two stray capitals under a "TYPE" heading is not a schedule
        return None
    return {"marks": marks, "qty": qty, "header": header,
            "source": "ocr" if fuzzy else "text",
            "fuzzy": [r for r, c in zip(raws, marks) if r != c],
            "box": (rx0, hw[1] - 4, rx1, y_max + 8)}


RESCUE_MIN = 0.62         # a groupless average must look this like a named one
RESCUE_MARGIN = 0.07      # ...and this much more like it than like any other


def _rescue(gdicts: list[dict], gfirst: int, means: list[np.ndarray],
            groups: list[list[int]], tags: list[Tag]) -> None:
    """Give an unread group the code of the named group it looks like.

    Some appearance groups average badly - a couple of instances sitting on
    a wall line, or a group of two - and come back unread even though the
    page has already resolved that same code from a cleaner group of twenty.
    Comparing the two averaged pictures is stronger evidence than a second
    OCR attempt, so the page's own best read is reused, but only when the
    match is decisive AND unambiguous between codes.
    """
    named = [(gi, gdicts[gfirst + gi]["code"]) for gi in range(len(groups))
             if gdicts[gfirst + gi]["code"]]
    if not named:
        return
    for gi in range(len(groups)):
        g = gdicts[gfirst + gi]
        if g["code"]:
            continue
        per: dict[str, float] = {}
        for nj, code in named:
            s = 1.0 - code_dist(means[gi], means[nj])
            per[code] = max(per.get(code, -2.0), s)
        code = max(per, key=per.get)
        others = [v for k, v in per.items() if k != code]
        if per[code] < RESCUE_MIN:
            continue
        if others and per[code] - max(others) < RESCUE_MARGIN:
            continue
        g["code"] = code
        g["weight"] = round(per[code], 2)
        g["raw"] = (g.get("raw") or "") + " ~matched"
        for i in groups[gi]:
            tags[i].code = code


DEFAULT_VOCAB = ["W1", "W2", "W3", "W4", "W5", "SF1", "SF2", "SF3"]
MAX_FAMILIES = 3          # bubble families matched per page
MAX_GROUP_READS = 14      # appearance groups OCR'd per family
CHAR_PX = 7.0             # measured advance of one code character at 300 dpi


def detect_marks(pdf_path: str, page: int, vocab: list[str] | None = None,
                 dpi: int = RENDER_DPI, thr: float = MATCH_MIN,
                 with_ocr: bool = True, img: np.ndarray | None = None,
                 debug: dict | None = None,
                 max_families: int = MAX_FAMILIES,
                 force_orient: int | None = None,
                 _retry: bool = True) -> MarkPage:
    """Count the mark bubbles on one scanned page.

    `vocab` is the legal code list - normally the MARK column of the sheet's
    own schedule.  Passing it is what turns free OCR (which reads "WIL",
    "WlL", "NIL" at this glyph size) into a constrained classification.
    """
    src = render(pdf_path, page, dpi) if img is None else img
    work, undo, deg, sk = upright(src, force_orient)
    gray = (255 - value(work)).astype(np.float32)     # ink high
    ink = ink_mask(work)
    mp = MarkPage(orient=deg, skew_deg=round(sk, 3),
                  img_size=(work.shape[1], work.shape[0]))

    vocab = [v.upper() for v in (vocab or DEFAULT_VOCAB)]
    # A vocabulary of single letters cannot constrain anything.  The apartment set's
    # schedule lists W1L/W1R/W2L - three characters, so a wrong read almost
    # never lands on a legal mark.  Window set 2's schedule (another office) lists
    # window types A..Z, and with 21 one-character codes virtually ANY glyph
    # the OCR emits is "in vocabulary": measured on that sheet, the page came
    # back with 88 unread and confident-looking counts for A/B/C/G/Q/S that
    # are grid bubbles and sensor dots, not windows.  Counting still works
    # there; NAMING does not, so it is withheld rather than guessed.
    weak_vocab = max((len(v) for v in vocab), default=0) < 2
    if weak_vocab:
        mp.notes.append(
            "this schedule's marks are single letters, which is too weak to "
            "tell one bubble's code from another at this glyph size - "
            "bubbles are counted but left unnamed for you to name")
    seeds = seed_tags(ink)
    mp.seeds = len(seeds)
    fams = size_families(seeds)
    # Only families whose bubble can physically hold the longest legal code
    # are mark bubbles.  Measured on this sheet: "W1L" spans ~21 px inside a
    # 31 px hexagon at 300 dpi, i.e. ~7 px per character plus the stroke and
    # its clearance.  This is what separates a WINDOW TAG from the ~14 px
    # keynote circle that carries a single digit - the small one is a real
    # repeated symbol, it just cannot be a mark from this schedule.
    need = CHAR_PX * (dpi / 300.0) * max(len(v) for v in vocab) + 8
    small = [f for f in fams if f[0] < need]
    big = [f for f in fams if f[0] >= need]
    fams = big[:max_families]
    # A silent cap reports as full coverage: the audit measured 44 marks at
    # this default vs 49 with the cap raised, BOTH runs presenting as
    # complete. The cap stays (each family costs a build + tag scan + up to
    # MAX_GROUP_READS OCR reads), but what it cut is now on the record so
    # an operator - or a raised max_families call - knows to look.
    if len(big) > max_families:
        cut = big[max_families:]
        mp.notes.append(
            f"{len(cut)} more repeated-symbol famil"
            f"{'y' if len(cut) == 1 else 'ies'} big enough to hold a mark "
            f"({', '.join(f'{f[0]}x{f[1]}px' for f in cut[:4])}) "
            f"{'was' if len(cut) == 1 else 'were'} NOT checked against the "
            f"schedule (family cap {max_families}) - their symbols are not "
            f"in these counts")
    if small:
        mp.notes.append(
            f"{len(small)} smaller repeated symbol famil"
            f"{'y' if len(small) == 1 else 'ies'} on this page "
            f"({', '.join(f'{f[0]}x{f[1]}px' for f in small[:4])}) are too "
            f"small to hold a {max(len(v) for v in vocab)}-character mark - "
            f"not counted as marks")
    if not fams:
        mp.notes.append("no repeated outlined symbol big enough to carry a "
                        "mark from this schedule")
        return mp
    mp.tag_size = (fams[0][0], fams[0][1])
    lbs, lrot = _legend_boxes(work, seeds) if with_ocr else ([], None)
    mp.legend_boxes, mp.legend_rot = lbs, lrot

    all_tags: list[Tag] = []
    dbg_codes, dbg_canon, dbg_groups = [], [], []
    upright_votes = flipped_votes = 0
    for fi, (tw, th, seedset) in enumerate(fams):
        built = build_family(gray, ink, seedset, tw, th)
        if built is None:
            continue
        fam, gpats = built
        tags = find_tags(gray, fam, gpats, thr)
        if not tags:
            continue
        for t in tags:
            cx, cy = t.x + t.w / 2.0, t.y + t.h / 2.0
            t.cx_pt, t.cy_pt = to_page_pt(undo, dpi, cx, cy)
            t.family = fi
        base = len(all_tags)
        if with_ocr:
            codes = [isolate_code(gray, t, fam) for t in tags]
            canon = [_canonical(c) if c is not None else None for c in codes]
            groups = group_codes(canon)
            gfirst = len(mp.groups)
            means: list[np.ndarray] = []
            for gi, members in enumerate(groups):
                code, weight, raw, agree = "", 0.0, "", 0
                avg = align_average([canon[i] for i in members]) * 255.0
                means.append(avg)
                if gi < MAX_GROUP_READS:
                    code, weight, raw, agree = read_image(avg, vocab)
                    if not code and len(members) == 1:
                        # a lone tag has no siblings to average with
                        code, weight, raw, agree = read_image(
                            codes[members[0]], vocab)
                    if weight < READ_MIN_WEIGHT or weak_vocab:
                        code = ""
                    if not code:
                        # tesseract's page-orientation detector picks 270 for
                        # 90 now and then (measured: it did on page 4 of this
                        # very document).  A 180-degree read of the code
                        # picture is the cheap check, and its vote decides
                        # whether the whole page gets re-run the other way up.
                        fc, fw, _, _ = read_image(avg, vocab, flip=True)
                        if fc and fw >= READ_MIN_WEIGHT:
                            flipped_votes += len(members)
                    else:
                        upright_votes += len(members)
                # a string most modes agreed on that is not in the schedule
                # is a NEIGHBOURING tag family, not an unread window mark
                off = raw if (not code and agree >= 3 and len(raw) >= 2) else ""
                mp.groups.append({"family": fi, "w": tw, "h": th,
                                  "n": len(members), "code": code,
                                  "raw": raw, "off_schedule": off,
                                  "weight": round(weight, 1)})
                for i in members:
                    tags[i].code = code
                    tags[i].off_schedule = off
                    tags[i].group = len(mp.groups) - 1
            _rescue(mp.groups, gfirst, means, groups, tags)
            dbg_codes += codes
            dbg_canon += canon
            dbg_groups += [[base + i for i in m] for m in groups]
        else:
            mp.groups.append({"family": fi, "w": tw, "h": th,
                              "n": len(tags), "code": "", "raw": "",
                              "weight": 0.0})
        all_tags += tags

    # One printed symbol, one detection: see dedup_across_families.  Done
    # after every family has matched, because that is the first moment two
    # families' answers can be compared, and the group tallies are corrected
    # so the debug view does not keep counting the tags that were dropped.
    if len(fams) > 1:
        kept = dedup_across_families(all_tags)
        if len(kept) != len(all_tags):
            mp.notes.append(
                f"{len(all_tags) - len(kept)} duplicate detections removed: "
                f"the same printed symbol matched by two size families")
            live = set(id(t) for t in kept)
            for g in mp.groups:
                g["n"] = 0
            for t in kept:
                if 0 <= t.group < len(mp.groups):
                    mp.groups[t.group]["n"] += 1
            all_tags = [t for t in all_tags if id(t) in live]

    # Legend keys are marked AFTER dedup, so each box is judged on the tags
    # that would actually be counted.  A box holding more than LEGEND_MAX_KEYS
    # of them is rejected and SAID SO, rather than quietly deleting whatever
    # is under it - see the constant for the measurement behind that bound.
    kept_boxes = 0
    for b in lbs:
        inside = [t for t in all_tags
                  if b[0] <= t.x + t.w / 2.0 <= b[2]
                  and b[1] <= t.y + t.h / 2.0 <= b[3]]
        if len(inside) > LEGEND_MAX_KEYS:
            mp.notes.append(
                f"a block headed like a legend at ({b[0]},{b[1]}) holds "
                f"{len(inside)} symbols - too many for a key column, so they "
                f"are COUNTED, not dropped; check that corner by eye")
            continue
        kept_boxes += 1
        for t in inside:
            t.kind = "legend"
    if kept_boxes:
        mp.notes.append(
            f"{kept_boxes} symbol-legend block"
            f"{'' if kept_boxes == 1 else 's'} found on this sheet - their "
            f"key symbols are excluded from the count"
            + ("" if not lrot else
               f" (the heading only read with the page turned {lrot} deg "
               f"from where it was stood up)"))

    # (no point retrying the other way up when the vocabulary was too weak to
    # read either way - nothing would vote)
    if _retry and not weak_vocab and flipped_votes > upright_votes \
            and flipped_votes >= 3:
        mp2 = detect_marks(pdf_path, page, vocab, dpi, thr, with_ocr, src,
                           debug, max_families, (deg + 180) % 360, False)
        mp2.notes.append(f"page orientation corrected: the detector's first "
                         f"pass stood the sheet at {deg} deg and no code read "
                         f"as a mark; re-run at {(deg + 180) % 360} deg")
        return mp2

    for t in all_tags:
        if t.kind == "legend":
            continue
        t.kind = ("storefront" if t.code.startswith("SF")
                  else "door" if t.code.startswith("D")
                  else "window" if t.code.startswith("W")
                  else "other-tag" if t.off_schedule
                  else "unknown")
    mp.tags = all_tags
    mp.scope = scope_state(all_tags, None)
    if mp.scope["state"] == "abstained" and mp.scope["counted"]:
        s = mp.scope
        mp.notes.append(
            f"NOT narrowed to a trade: {s['counted']} symbols were found and "
            f"{s['unnamed']} of them could not be named, and nothing in this "
            f"document says which codes belong to your trade - so every "
            f"symbol found is listed, including any that belong to somebody "
            f"else. Give the counter this project's window/door schedules, "
            f"or narrow it by hand."
            if s["reason"] == "no-code-read" else
            f"NOT narrowed to a trade: no trade was given, so all "
            f"{s['counted']} symbols found are listed.")
    if debug is not None:
        debug.update(work=work, gray=gray, codes=dbg_codes, canon=dbg_canon,
                     groups=dbg_groups, undo=undo, families=fams)
    return mp
