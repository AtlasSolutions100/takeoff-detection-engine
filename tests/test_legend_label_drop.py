"""THE LEGEND'S SAMPLE NUMBER IS NOT A CONTOUR ELEVATION.

Oncor Cloudcrest engine page index 7 = printed C-08 prints "468" on both
its PROPOSED CONTOUR and EXISTING CONTOUR legend swatches. The sheet's
contours run 530-586 ft. On 2026-09-08 the suggest list carried two rows at
468.0 - chain 959 (existing, `inline`) and chain 602 (proposed, `radius`),
both 124 pt horizontal swatch lines at x 2056-2180 - 60 to 120 ft below
every real label, one on each surface.

The route's per-row test could not catch them, measured on the stored
result: the legend reader's box for "468 PROPOSED CONTOUR" ends at
x = 2109.6 while the label sits at x = 2120.3 (11 pt past it, 5 pt past the
6 pt pad), and the EXISTING CONTOUR row was not read as a legend row at all.
The MERGED legend block - the eleven row boxes bridged, exactly what the
spot-grade reader already uses - spans x 1706-2191, y 248-649 and holds
both. The fixture below is C-08's own eleven boxes, verbatim.

On the pre-fix commit `label_in_legend` does not exist and the route's
inline test returned False for both points; the first two checks below are
the ones that pin the fix.

Run:  python tests/test_legend_label_drop.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import takeoff_engine.earthwork as _ew  # noqa: E402

FAILS = []


def check(ok, msg):
    if ok:
        print("ok:", msg)
    else:
        FAILS.append(msg)
        print("FAIL:", msg)


label_in_legend = getattr(_ew, "label_in_legend", None)
if label_in_legend is None:
    # the pre-fix shape: the route's own per-row test, reproduced so the
    # behavioural checks run (and fail) rather than the import dying
    def label_in_legend(xy, boxes):
        x, y = xy
        return any(b[0] - 6 <= x <= b[2] + 6 and b[1] - 8 <= y <= b[3] + 8
                   for b in boxes)

# Oncor C-08 p7 legend_samples, as stored (label, box) - the boxes are what
# the route sees; the labels are here so a reader knows which row is which
C08 = [
    ("PROPERTY LINE", [1954.9, 248.0, 2190.9, 266.0]),
    ("PROPOSED SWALE", [1954.9, 267.3, 2190.9, 285.3]),
    ("468 PROPOSED CONTOUR", [1873.6, 286.6, 2109.6, 304.6]),
    ("PROPOSED LIMITS OF DISTURBANCE", [1955.0, 344.5, 2191.0, 362.5]),
    ("EROSION CONTROL NOTES", [1851.2, 513.9, 2087.2, 531.9]),
    ("EX-FO", [1706.1, 544.8, 1942.1, 562.8]),
    ("ALL SWPPP CONTROLS.", [1833.5, 553.2, 2069.5, 571.2]),
    ("MAINTENANCE OR MODIFICATION, AND", [1832.9, 582.2, 2068.9, 600.2]),
    ("EMPLOYED (WHETHER CALLED OUT ON", [1832.9, 591.9, 2068.9, 609.9]),
    ("DIRECTLY ON THE SITE MAP.", [1832.9, 601.6, 2068.9, 619.6]),
    ("EXISTING CONTOURS.", [1832.9, 630.6, 2068.9, 648.6]),
]
BOXES = [b for _l, b in C08]

check(label_in_legend((2120.3, 295.8), BOXES),
      "C-08's PROPOSED CONTOUR '468' (chain 602, 11 pt past its short row box) is in the legend")
check(label_in_legend((2120.3, 314.4), BOXES),
      "C-08's EXISTING CONTOUR '468' (chain 959, on a row the reader missed) is in the legend")
# the sheet's real labels are nowhere near: the contours sit left of the
# title-block column. A point 100 pt left of the block, and one below it.
check(not label_in_legend((1600.0, 400.0), BOXES),
      "a label 100 pt left of the legend block is NOT in the legend")
check(not label_in_legend((2000.0, 700.0), BOXES),
      "a label 50 pt below the legend block is NOT in the legend")
check(not label_in_legend((2120.3, 1200.0), BOXES),
      "a label far down the sheet in the same column is NOT in the legend")
check(not label_in_legend([None, None], BOXES) and not label_in_legend(None, BOXES),
      "a row with no label position is never 'in the legend'")
check(not label_in_legend((2120.3, 295.8), []),
      "no legend boxes at all: nothing is dropped")

# two legends in different corners stay two blocks: a point between them
# is plan, not legend (the bridge only joins boxes whose x-ranges overlap)
TWO = [[100.0, 100.0, 300.0, 118.0], [100.0, 120.0, 300.0, 138.0],
       [1500.0, 1200.0, 1700.0, 1218.0], [1500.0, 1220.0, 1700.0, 1238.0]]
check(not label_in_legend((800.0, 600.0), TWO),
      "a point between two separate legend blocks is not in either")
check(label_in_legend((200.0, 119.0), TWO) and label_in_legend((1600.0, 1219.0), TWO),
      "...while a point inside each block's row gap is")

print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
