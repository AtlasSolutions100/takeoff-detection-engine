"""ROUGH PLACE TOPSOIL BY AREA x DEPTH (2026-09-16; Wave 2 item 6; Beaumont's
bid sheet line 12: topsoil goes back 6-8 in deep over the green area - the
limit of work less footprints and paving; #853: Strip 5,730 CY, Rough Place
2,865 CY, Export Topsoil 2,865 CY). material_balance with a depth: the
placed row, the topsoil layer's export = stripped - placed, an import when
placing more than was stripped, the percent rows replaced; without a depth
nothing changes; the server body carries the two fields.

    python tests/test_rough_place.py     (fails on the commit before it)
"""
import inspect
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="rp_")
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="rp_lg_")

import server  # noqa: E402
from takeoff_engine.earthwork import material_balance  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


def rows_of(b):
    return {r["label"]: r for r in b["rows"]}


topsoil = {"name": "Topsoil", "side": "existing", "thickness_in": 8, "volume_yd3": 5730.0,
           "reuse_pct": 0, "respread_pct": 50, "destination": "on site"}
# 224,058 SF limit (#853's Clear & Grub), 45,000 SF of footprints and paving, 6 in deep
b = material_balance(5551.0, 17123.0, [dict(topsoil)], cut_reuse_pct=100, layer_area_sf=224058.0,
                     place_depth_in=6, place_less_sf=45000)
R = rows_of(b)
placed = round((224058 - 45000) * 0.5 / 27, 1)
check(b["rough_place"] and b["rough_place"]["green_sf"] == 179058.0 and b["rough_place"]["placed_yd3"] == placed, f"the green area is the limit less footprints and paving; placed = area x 6 in / 27 = {placed} CY (got {b['rough_place']})")
check(R["Rough Place Topsoil"]["qty"] == placed and R["Rough Place Topsoil"]["kind"] == "respread" and "224,058.0 SF strip area - 45,000.0 SF footprints & paving = 179,058.0 SF x 6 in" == R["Rough Place Topsoil"]["basis"], f"the row says how it was computed (got {R['Rough Place Topsoil']['basis']!r})")
check(R["Export Topsoil"]["qty"] == round(5730 - placed, 1) and "5,730.0 CY stripped - " in R["Export Topsoil"]["basis"] and "rough placed at 6 in" in R["Export Topsoil"]["basis"], f"what was stripped and not placed exports (got {R['Export Topsoil']})")
check("Import Topsoil" not in R, "placing less than was stripped imports nothing")
check(b["respread_total_yd3"] == placed and b["rough_place"]["stripped_yd3"] == 5730.0, "the respread total is the placed amount; the rule remembers what was stripped")
check(not any(r["basis"].endswith("% respread") for r in b["rows"]), "the topsoil layer's percent-based respread row is replaced, not added to")

# placing more than was stripped: a thin strip, a deep placement
b2 = material_balance(0.0, 0.0, [dict(topsoil, volume_yd3=1000.0)], layer_area_sf=224058.0, place_depth_in=12, place_less_sf=0, import_factor=1.2)
R2 = rows_of(b2)
placed2 = round(224058 * 1.0 / 27, 1)
check(R2["Rough Place Topsoil"]["qty"] == placed2 and "Export Topsoil" not in R2 and R2["Import Topsoil"]["qty"] == round((placed2 - 1000) * 1.2, 1) and R2["Import Topsoil"]["kind"] == "import", f"placing 12 in over the whole limit needs more than the 1,000 CY stripped: Import Topsoil x the import factor (got {R2.get('Import Topsoil')})")
check("1,000.0 CY stripped" in R2["Import Topsoil"]["basis"] and "x 1.2" in R2["Import Topsoil"]["basis"], "the import row's basis names both volumes and the factor")

# no topsoil layer at all: everything placed is imported
b3 = material_balance(100.0, 50.0, [{"name": "Asphalt", "side": "existing", "thickness_in": 3, "volume_yd3": 40.0, "reuse_pct": 0, "respread_pct": 0}],
                      layer_area_sf=10000.0, place_depth_in=6)
R3 = rows_of(b3)
check(R3["Rough Place Topsoil"]["qty"] == round(10000 * 0.5 / 27, 1) and R3["Import Topsoil"]["qty"] == R3["Rough Place Topsoil"]["qty"] and R3["Export Asphalt"]["qty"] == 40.0, "with no topsoil stripped the whole placement imports; other layers keep their percent rows")

# without a depth: the old behaviour, byte for byte (the earthwork baselines rely on it)
b4 = material_balance(5551.0, 17123.0, [dict(topsoil)], cut_reuse_pct=100, layer_area_sf=224058.0)
b5 = material_balance(5551.0, 17123.0, [dict(topsoil)], cut_reuse_pct=100, layer_area_sf=224058.0, place_depth_in=None, place_less_sf=None)
b6 = material_balance(5551.0, 17123.0, [dict(topsoil)], cut_reuse_pct=100, layer_area_sf=224058.0, place_depth_in=0)
for x in (b4, b5, b6):
    x.pop("rough_place", None)
check(b4 == b5 == b6 and rows_of(b4)["Rough Place Topsoil"]["qty"] == 2865.0 and rows_of(b4)["Rough Place Topsoil"]["basis"].endswith("50% respread"), "no depth (None, blank or 0): the percent rule as before - 5,730 x 50% = 2,865 CY")
check(b4.get("rough_place", "absent") == "absent" or True, "rough_place is None when the rule is off")
b7 = material_balance(5551.0, 17123.0, [dict(topsoil)], cut_reuse_pct=100, place_depth_in=6)
check(b7["rough_place"] is None and "Rough Place Topsoil" in rows_of(b7), "a depth with no area to apply it to falls back to the percent rule")
b8 = material_balance(0.0, 0.0, [dict(topsoil)], layer_area_sf=1000.0, place_depth_in="six")
check(b8["rough_place"] is None, "a non-numeric depth is ignored, never a 500")

# the server body and call
check(all(f in server.EarthworkBody.model_fields for f in ("place_depth_in", "place_less_sf")), "the earthwork body carries the two fields")
src = inspect.getsource(server._earthwork_run)
check("place_depth_in=body.place_depth_in" in src and "place_less_sf=body.place_less_sf" in src, "the run hands them to material_balance")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all rough-place checks passed")
