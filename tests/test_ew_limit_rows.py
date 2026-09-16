"""ONE LIMITS PICK, THREE ROWS (2026-09-16, Beaumont Estimating call
2026-09-15 05:48-06:27): the limits-of-disturbance line's length is the
silt / construction fence, everything inside it is Clear & Grub and Strip
Topsoil.

Pinned here, through the route function with a synthetic job (the same
fixture shape as test_ew_map_refusal.py):
  * boundary_metrics: shoelace area in SF, closed-ring perimeter in ft, the
    closure gap in ft, None under three points;
  * /earthwork with a boundary but boundary_is_limit false (every recorded
    body): layer_area_sf stays the surface overlap, area_source says so, and
    the reply still carries the ring's numbers with is_limit false;
  * boundary_is_limit true: layer_area_sf = the ring's area, area_source
    "limit-of-work outline", boundary.is_limit true;
  * a typed strip_area_sf wins over both.

Fails on c4c5067 (EarthworkBody has no boundary_is_limit; no boundary key).

    python tests/test_ew_limit_rows.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp(prefix="ewlimit_")
os.environ["TAKEOFF_JOBS_DIR"] = str(Path(_TMP) / "jobs")
os.environ["TAKEOFF_LEDGER_DIR"] = str(Path(_TMP) / "ledger")

import server  # noqa: E402
from takeoff_engine.earthwork import boundary_metrics  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


# ---- the pure helper -------------------------------------------------------------
FPP = 40.0 / 72.0                      # 40 ft to the paper inch
ring = [[90, 90], [170, 90], [170, 170], [90, 170]]          # 80 x 80 pt, open
m = boundary_metrics(ring, FPP)
side_ft = 80 * FPP
check(m is not None and abs(m["area_sf"] - side_ft * side_ft) < 0.2, f"area of an 80x80 pt ring at 40 ft/in = {side_ft*side_ft:.1f} SF (got {m and m['area_sf']})")
check(abs(m["perim_ft"] - 4 * side_ft) < 0.2, f"perimeter closes the ring: {4*side_ft:.1f} ft (got {m['perim_ft']})")
check(abs(m["gap_ft"] - side_ft) < 0.1 and m["n"] == 4, "an open ring reports the distance between its drawn ends (they are joined for the area and the perimeter)")
m2 = boundary_metrics(ring + [[90, 90]], FPP)
check(abs(m2["area_sf"] - m["area_sf"]) < 0.01 and abs(m2["perim_ft"] - m["perim_ft"]) < 0.01 and m2["gap_ft"] == 0.0,
      "repeating the first point at the end changes neither area nor perimeter, and the gap is 0")
m3 = boundary_metrics([[90, 90], [170, 90], [170, 170], [90, 160]], FPP)
check(abs(m3["gap_ft"] - 70 * FPP) < 0.1, "a ring whose ends do not meet reports the gap in feet")
check(boundary_metrics([[0, 0], [1, 1]], FPP) is None and boundary_metrics(None, FPP) is None, "fewer than three points -> None")

# ---- through the route ---------------------------------------------------------
JID = "ewlimittest1"
d = server._job_dir(JID)
d.mkdir(parents=True, exist_ok=True)


def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


RESULT = {
    "scale": {"ft_per_paper_inch": 40.0, "source": "test"},
    "chains": [
        {"chain_id": 205, "points": _box(100, 100, 160, 160), "length_pt": 240.0},
        {"chain_id": 214, "points": _box(110, 110, 150, 150), "length_pt": 160.0},
        {"chain_id": 206, "points": _box(102, 102, 158, 158), "length_pt": 224.0},
        {"chain_id": 215, "points": _box(112, 112, 148, 148), "length_pt": 144.0},
    ],
}
(d / "p0_result.json").write_text(json.dumps(RESULT))
server.JOBS[JID] = {"path": str(d / "nosuch.pdf"), "status": {}, "owner": None}

GOOD = dict(existing=[{"chain_id": 205, "z": 100.0}, {"chain_id": 206, "z": 102.0}],
            proposed=[{"chain_id": 214, "z": 104.0}, {"chain_id": 215, "z": 106.0}],
            existing_layers=[{"name": "topsoil", "thickness_in": 6}])


def run(**kw):
    return server._earthwork_run(JID, 0, server.EarthworkBody(**GOOD, **kw))


base = run()
check("boundary" not in base and base.get("area_source") == "surface overlap", "no boundary: layers use the surface overlap and say so")
overlap = base["layer_area_sf"]

crop = run(boundary=ring)
check(crop["layer_area_sf"] == overlap and crop["area_source"] == "surface overlap",
      "a boundary that is only a crop (boundary_is_limit false, every recorded body) leaves the layer area at the overlap")
check(crop["boundary"]["is_limit"] is False and abs(crop["boundary"]["area_sf"] - m["area_sf"]) < 0.01 and abs(crop["boundary"]["perim_ft"] - m["perim_ft"]) < 0.01,
      "the reply still carries the ring's SF and LF, flagged as not a limit")

lim = run(boundary=ring, boundary_is_limit=True)
check(lim["area_source"] == "limit-of-work outline" and abs(lim["layer_area_sf"] - round(m["area_sf"])) < 1.0,
      f"a limit of work makes the layer area the ring's own area ({m['area_sf']} SF, got {lim['layer_area_sf']})")
check(lim["boundary"]["is_limit"] is True, "boundary.is_limit true on the reply")
strip = next(l for l in lim["layers"] if l["name"] == "topsoil")
check(abs(strip["volume_yd3"] - round(m["area_sf"] * 6 / 12.0 / 27.0, 1)) < 0.2, "Strip Topsoil CY = the ring's area x 6 in")
check(lim["cut_yd3"] == crop["cut_yd3"] and lim["fill_yd3"] == crop["fill_yd3"], "cut and fill themselves do not move: the boundary masked them identically before")

typed = run(boundary=ring, boundary_is_limit=True, strip_area_sf=12345.0)
check(typed["layer_area_sf"] == 12345 and typed["area_source"] == "typed strip area", "a typed strip area still wins over the limit")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all limit-rows checks passed")
