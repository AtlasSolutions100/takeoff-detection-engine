"""Per-page input classification: vector / raster / hybrid.

Every downstream decision routes on this. A page that is one solid slab of
pixels is a raster page no matter how many stray paths it carries (borders,
stamps); a page with thousands of stroke paths and no significant image
coverage is a clean CAD export.

"One solid slab" is load-bearing and was not always so. The raster tracer
exists to recover linework that exists only as pixels, and the only page
where that is true is a page that IS a scan - one image, or one grid of
image tiles, standing in for the whole sheet. A cover sheet, a rendering
sheet, or a product-selection board is a normal vector sheet decorated with
a COLLAGE of photographs, and the SUM of those photo bboxes crosses any
coverage threshold while none of the sheet's linework is in pixels at all.
Tracing such a page invents geometry that never existed as linework:
measured on Res-C A0.0 (page index 0), the tracer returned 107
non-annotation chains / 23,391.2 pt in 64.3 s, of which 26 were parcel
boundaries traced inside a vicinity-map screenshot and 10 were the outlines
of the letter "D" in a heading.

Testing the largest SINGLE image is not enough either: PDF producers split
scans into strips (a custom-home scan p0 is 118 vertical bands, a mixed-use
set's p114 is 9 horizontal ones), and no single band is large. Hence
scan_coverage below - the largest contiguous block of image tiles that
actually fills its own bounding rectangle. See
out/verify/2026-08-22/misroute/VERIFY.md for the original measurement and
out/verify/2026-08-22/misroute-split/VERIFY.md for the full-corpus one.
"""

import math
from dataclasses import dataclass, asdict

import pymupdf


@dataclass
class PageClass:
    page_index: int
    kind: str  # "vector" | "raster" | "hybrid"
    path_count: int
    image_coverage: float  # SUM of image bboxes / page area (may double-count)
    # Largest contiguous, rectangle-filling block of images / page area. This,
    # not the sum, is what says "the sheet is a scan"; the routing test below
    # turns on it. Equals the largest single image whenever that image alone
    # is big enough, which is every non-tiled scan.
    scan_coverage: float
    text_chars: int
    width_pt: float
    height_pt: float

    def to_dict(self) -> dict:
        return asdict(self)


# Below this many stroke paths a page cannot meaningfully be a vector drawing;
# title-block borders alone are usually ~10-50 paths.
MIN_VECTOR_PATHS = 200
# Above this SCAN coverage the sheet is that image: a scan. Unchanged at 0.5,
# and it did not need tuning - what changed is the quantity it is applied to.
# Measured over every page of the client folders (208) and ../TakeoffPro AI
# (6,494 readable pages) with path_count < MIN_VECTOR_PATHS: pages kept
# raster land at scan_coverage 0.551-1.000 (the 0.551 is a two-tile scan of
# federal form SF-1444, a sewer project p319) and every page this test demotes
# reads 0.005-0.437 (the 0.437 is Northwest Pipe p25, a valve-catalogue
# dimension page whose sections are one bitmap - not a plan sheet). In
# the client folders the nearest demoted miss is Res-C p60, an interior-fixture
# photo board whose touching photos are rejected by the solidity test below,
# at 0.347. 0.5 sits 1.14x above the highest demoted page and 1.10x below
# the lowest kept scan - that 1.10x is the tightest number in this file, and
# it is why the solidity test exists rather than a higher coverage floor.
RASTER_COVERAGE = 0.5
# Between these, images are present but vector linework still carries signal.
HYBRID_COVERAGE = 0.05
# A block of image tiles only counts as a scan when the tiles FILL the
# rectangle they span. Split scans abut exactly, so they measure solidity
# 1.000 (118-band custom home, 26-band storage site, 9-band sewer, mixed-use,
# and 2-tile producers alike); 41 of the two corpora's 6,702 pages are kept
# raster ONLY by this block test - a largest-single-image rule would send
# them to the vector path, which yields nothing on them (paths 0-6). The one
# photo collage whose images touch into a big block, Res-C p60, measures
# 0.780 because the clump is ragged. 0.95 sits 5% below the structural value
# and 22% above the collage.
TILED_SOLIDITY = 0.95
# Page points per cell of the occupancy grid the block test runs on. Tiles of
# a split scan abut at 0 pt, so any cell size merges them; separate artwork on
# these sheets is divided by gutters of 40 pt and up, so 4 pt cannot bridge
# them. It also bounds the work: the largest sheet here is 3024 pt = 756 cells.
BLOCK_CELL_PT = 4.0


def _tiled_block_coverage(boxes: list, clip: pymupdf.Rect,
                          page_area: float) -> float:
    """Coverage of the largest contiguous block of image tiles that fills its
    own bounding rectangle, or 0.0 when no such block exists.

    Answers "is some rectangular region of this sheet nothing but pixels?"
    without decoding a single image. Runs only on the pages where the cheap
    tests are ambiguous (images cover the sheet in total, but no one image
    does): 97 of the 6,702 pages of the client folders + ../TakeoffPro AI.
    """
    import cv2
    import numpy as np

    cell = BLOCK_CELL_PT
    w = max(int(math.ceil(clip.width / cell)), 1)
    h = max(int(math.ceil(clip.height / cell)), 1)
    grid = np.zeros((h, w), np.uint8)
    for r in boxes:
        x0 = max(int(math.floor((r.x0 - clip.x0) / cell)), 0)
        y0 = max(int(math.floor((r.y0 - clip.y0) / cell)), 0)
        x1 = min(int(math.ceil((r.x1 - clip.x0) / cell)), w)
        y1 = min(int(math.ceil((r.y1 - clip.y0) / cell)), h)
        if x1 > x0 and y1 > y0:
            grid[y0:y1, x0:x1] = 1
    if not grid.any():
        return 0.0
    n, _lbl, stats, _cent = cv2.connectedComponentsWithStats(grid, connectivity=4)
    best = 0.0
    cell_area = cell * cell
    for i in range(1, n):
        filled = int(stats[i, cv2.CC_STAT_AREA])
        span = int(stats[i, cv2.CC_STAT_WIDTH]) * int(stats[i, cv2.CC_STAT_HEIGHT])
        if span <= 0 or filled / span < TILED_SOLIDITY:
            continue  # a ragged clump of artwork, not a scan cut into tiles
        best = max(best, filled * cell_area / page_area)
    return min(best, 1.0)


def classify_page(page: pymupdf.Page) -> PageClass:
    path_count = len(page.get_drawings())
    page_area = max(page.rect.width * page.rect.height, 1.0)

    # Clip images to the page: CAD exports sometimes place images with bboxes
    # hanging off-sheet.
    #
    # KNOWN-WRONG ON ROTATED PAGES, LEFT SO ON PURPOSE. get_image_info()
    # reports bboxes in UNROTATED space (the space get_drawings() uses) while
    # page.rect is rotation-aware, so on a /Rotate 90 or 270 sheet this clip
    # throws away the part of the image that sticks out of the rotated rect
    # and both coverages below read low by up to min(w,h)/max(w,h) (Res-C
    # p16/p17, full-page scans on /Rotate 90 sheets: 0.714, not 1.000). The
    # correct clip is `page.rect * page.derotation_matrix`. It is NOT applied
    # because HYBRID_COVERAGE (0.05) was calibrated against the low number:
    # correcting it lifts 14 /Rotate 270 plan-corpus sheets from vector to
    # hybrid, and on four of them extract_hybrid_image_units() traces an
    # embedded screenshot of a schedule table as linework (a mixed-use set p36:
    # +46 chains, +11,037.3 pt = 3,065.9 ft at its 1"=20'). Until the hybrid
    # tracer can tell a pasted table/photo from a scanned drawing, the low
    # measurement is the safer product. Deferred, with the page list, in
    # out/verify/2026-08-22/misroute-split/DEFERRED-derotation-clip.md.
    # The routing below does not need the correction: measured over all
    # 6,702 readable pages of the client folders + ../TakeoffPro AI, every
    # rotated page kept as raster still reads scan_coverage >= 0.647 under
    # this clip (floor: an aviation set p72 and Bid Tab Example p0, both
    # /Rotate 270), and the raster set is page-for-page identical to the one
    # the corrected clip produces.
    clip = page.rect

    boxes = []
    img_area = 0.0
    max_img_area = 0.0
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"]) & clip
        if r.is_empty:
            continue
        boxes.append(r)
        a = r.width * r.height
        img_area += a
        max_img_area = max(max_img_area, a)
    coverage = min(img_area / page_area, 1.0)
    scan_coverage = min(max_img_area / page_area, 1.0)
    if scan_coverage < RASTER_COVERAGE <= coverage and len(boxes) > 1:
        # No single image is the sheet, but the images together might still
        # be one scan the producer cut into tiles. Only this band pays for
        # the occupancy-grid test.
        scan_coverage = max(scan_coverage,
                            _tiled_block_coverage(boxes, clip, page_area))

    # A slab of pixels covering the sheet = a scan, and only a scan belongs on
    # the raster tracer. Testing the SUM instead sent every photo-collage
    # sheet there (see the module docstring); testing text or path counts
    # instead would send the OCR'd, vector-ruled scans in a client's Window -
    # Storefront - Door schedules.pdf (11,838 text chars and 107 table rules
    # over a full-page 150-dpi image) to the vector path, which cannot see
    # their content at all.
    if scan_coverage >= RASTER_COVERAGE and path_count < MIN_VECTOR_PATHS:
        kind = "raster"
    elif path_count >= MIN_VECTOR_PATHS and coverage < HYBRID_COVERAGE:
        kind = "vector"
    elif path_count >= MIN_VECTOR_PATHS:
        kind = "hybrid"
    else:
        # Few paths and no image large enough to be a scan: a cover sheet, a
        # rendering sheet, an estimate form, or a near-empty page. Whatever
        # linework it has is already vector, so read that and trace nothing.
        # (Was: "raster if img_area > 0", which made a firm's logo enough to
        # hand the whole page to the tracer - that is how a client's
        # estimate PDF, 0.5% image coverage, ended up being traced.)
        kind = "vector"

    return PageClass(
        page_index=page.number,
        kind=kind,
        path_count=path_count,
        image_coverage=round(coverage, 3),
        scan_coverage=round(scan_coverage, 3),
        text_chars=len(page.get_text()),
        width_pt=page.rect.width,
        height_pt=page.rect.height,
    )
