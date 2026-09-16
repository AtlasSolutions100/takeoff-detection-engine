"""STRUCTURE DIG AND BACKFILL, BEAUMONT'S WAY (2026-09-16; call of
2026-09-15 28:00-31:17, bid sheet p.3 items 8 and 9).

  dig      = wall LF x Dt x (W + 0.5 x hv x Dt) / 27,  Dt = depth + footing
  backfill = wall LF x Df x (W + 0.5 x hv x Df) / 27,  Df = fill + footing
with W the footing width (default 2 ft), hv the layback (default 1:1), and
a blank fill depth figured to the dig depth AND reported as assumed.

Before c4c5067's successor this was wall LF x (depth + footing) x a constant
4 ft layback, and backfill equalled the dig. Fails there.

    python tests/test_structure_formula.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="stf_")
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="stf_lg_")

import server  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


FPP = 40.0 / 72.0                    # 40 ft to the paper inch
SQ = [[0, 0], [180, 0], [180, 180], [0, 180]]   # 180 pt = 100 ft a side
L = 400.0                            # wall LF


def one(**kw):
    return server._structure_one(dict(name="House", points=SQ, **kw), FPP)


# Beaumont's worked example: a 6 ft cut is "6 by 6 by 3" plus the footing
r = one(depth_ft=6, footing_w_ft=2, layback_hv=1)
check(abs(r["perim_lf"] - L) < 0.01 and abs(r["area_sf"] - 10000) < 0.5, "a 100 ft square: 400 LF of wall, 10,000 SF")
check(abs(r["struct_cy"] - L * 6 * (2 + 3) / 27) < 0.1, f"dig = 400 x 6 x (2 + 3) / 27 = {L*6*5/27:.1f} CY (got {r['struct_cy']})")
check(r["dig_w_ft"] == 5.0, "the trench is 5 ft wide at the top outside the wall (2 ft footing + 3 ft layback)")
check(r["backfill_assumed"] is True and r["backfill_cy"] == r["struct_cy"], "fill depth blank: backfill figured to the dig depth and flagged as assumed")
check(abs(r["mass_cy"] - 10000 * 6 / 27) < 0.1, "mass excavation unchanged: footprint x depth")

# proposed grade 2 ft lower than the ground he digs from
r2 = one(depth_ft=6, fill_depth_ft=4, footing_w_ft=2, layback_hv=1)
check(abs(r2["backfill_cy"] - L * 4 * (2 + 2) / 27) < 0.1 and r2["backfill_assumed"] is False,
      f"fill depth 4: backfill = 400 x 4 x (2 + 2) / 27 = {L*4*4/27:.1f} CY, not the dig (got {r2['backfill_cy']})")
check(r2["struct_cy"] == r["struct_cy"], "the dig does not change with the fill depth")

# the footing below the slab deepens both sections
r3 = one(depth_ft=6, footing_ft=1, footing_w_ft=2, layback_hv=1)
check(abs(r3["struct_cy"] - L * 7 * (2 + 3.5) / 27) < 0.1, "footing 1 ft below the slab: Dt = 7, section 7 x (2 + 3.5)")

# vertical walls (trench box) and a wider footing
r4 = one(depth_ft=6, footing_w_ft=7.5, layback_hv=0)
check(abs(r4["struct_cy"] - L * 6 * 7.5 / 27) < 0.1, "no layback: section = depth x footing width (a CF7.5 footing, 7.5 ft)")
# a 2:1 layback doubles the wedge
r5 = one(depth_ft=6, footing_w_ft=2, layback_hv=2)
check(abs(r5["struct_cy"] - L * 6 * (2 + 6) / 27) < 0.1, "2:1 layback: half the depth x 2 = 6 ft of side")

# defaults and the old shape
r6 = one(depth_ft=8)
check(r6["footing_w_ft"] == 2.0 and r6["layback_hv"] == 1.0, "defaults: 2 ft footing, 1:1 layback")
r7 = one(depth_ft=8, layback_ft=4)
check(r7["struct_cy"] == r6["struct_cy"], "the old constant layback_ft is ignored, not multiplied in")
try:
    one(depth_ft=8, footing_w_ft=-1)
    check(False, "a negative footing width is refused")
except server.HTTPException as e:
    check(e.status_code == 400, "a negative footing width is refused with 400")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all structure-formula checks passed")
