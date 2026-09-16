"""PER-ZONE BASE THICKNESS (2026-09-16; Beaumont call 2026-09-15): a grading
zone carries a base course under the finished grade. The typed elevation is
the FINISHED grade, so a pad / plane is lowered by the stack before the
cut/fill is figured, a balance solves the subgrade and reports the finished
grade above it, and each layer comes back as a place volume = zone area x
thickness.

Fails on 86abed9 (no proposed_layers on GradingZoneBody, no layers in the
reply).

    python tests/test_zone_base.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="zb_")
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="zb_lg_")

import server  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


# a job whose page has a 72 ft-per-inch scale, so page points ARE feet
JID = "zonebase1"
d = server._job_dir(JID); d.mkdir(parents=True, exist_ok=True)
(d / "p0_result.json").write_text(json.dumps({"scale": {"ft_per_paper_inch": 72.0, "source": "test"}, "chains": []}))
server.JOBS[JID] = {"path": str(d / "nosuch.pdf"), "status": {}, "owner": None}
SQ = [[0, 0], [100, 0], [100, 100], [0, 100]]           # 100 x 100 ft = 10,000 SF


def spots(z_fn):
    return [{"x": x, "y": y, "z": z_fn(x, y)} for x, y in ((0, 0), (100, 0), (0, 100), (100, 100), (50, 50))]


def run(**kw):
    kw.setdefault("polygon", SQ)
    kw.setdefault("existing_points", spots(lambda x, y: 100.0))
    r = server.earthwork_zone(JID, 0, server.GradingZoneBody(**kw))
    return json.loads(bytes(r.body))


# ---- pad at finished 100 over flat existing 100 -----------------------------------
r0 = run(design={"mode": "pad", "elev_ft": 100.0})
check(r0["cut_yd3"] == 0 and r0["fill_yd3"] == 0 and r0["layers"] == [] and r0["subgrade_offset_ft"] == 0,
      "no base: nothing to dig, no layers, no offset")
r6 = run(design={"mode": "pad", "elev_ft": 100.0}, proposed_layers=[{"name": "Gravel", "thickness_in": 6}])
check(abs(r6["cut_yd3"] - 185.2) < 0.15 and r6["fill_yd3"] == 0,
      f"6 in of base: the dig goes to the subgrade 0.5 ft down = 10,000 x 0.5 / 27 = 185.2 CY cut (got {r6['cut_yd3']})")
check(r6["layers"] == [{"name": "Gravel", "thickness_in": 6.0, "volume_yd3": 185.2}],
      f"the layer comes back as a place volume of zone area x thickness (got {r6['layers']})")
check(r6["subgrade_offset_ft"] == 0.5, "the reply says how far below the typed elevation the earthwork stops")
r6f = run(design={"mode": "pad", "elev_ft": 100.5}, proposed_layers=[{"name": "Gravel", "thickness_in": 6}])
check(r6f["cut_yd3"] == 0 and r6f["fill_yd3"] == 0 and r6f["layers"][0]["volume_yd3"] == 185.2,
      "finished grade 0.5 ft up with a 6 in base: the subgrade meets the ground, only the base is placed")

# ---- two layers stack --------------------------------------------------------------
r2 = run(design={"mode": "pad", "elev_ft": 100.0},
         proposed_layers=[{"name": "Asphalt", "thickness_in": 3}, {"name": "Base", "thickness_in": 9}])
check(abs(r2["cut_yd3"] - 370.4) < 0.2 and [l["name"] for l in r2["layers"]] == ["Asphalt", "Base"]
      and abs(r2["layers"][0]["volume_yd3"] - 92.6) < 0.1 and abs(r2["layers"][1]["volume_yd3"] - 277.8) < 0.1,
      f"a 12 in stack digs 1 ft (370.4 CY) and places 92.6 + 277.8 CY (got {r2['cut_yd3']}, {r2['layers']})")

# ---- balance mode reports the finished grade --------------------------------------
rb = run(design={"mode": "balance"}, existing_points=spots(lambda x, y: 100.0 + 0.1 * x),
         proposed_layers=[{"name": "Base", "thickness_in": 6}])
check(abs(rb["balance_subgrade_ft"] - 105.0) < 0.02 and abs(rb["balance_elev_ft"] - 105.5) < 0.02,
      f"balance: the subgrade balances at the mean 105.0, the finished grade sits 0.5 ft up at 105.5 (got {rb.get('balance_subgrade_ft')}, {rb['balance_elev_ft']})")
check(abs(rb["balance_delta_ft"] - 0.5) < 0.02, "and the delta from the existing mean is the finished grade's")
check(abs(rb["layers"][0]["volume_yd3"] - 185.2) < 0.15, "the base still places 185.2 CY")

# ---- strip only: no design surface, so no base ------------------------------------
rs = run(design={"mode": "strip_only"}, strip_in=8, proposed_layers=[{"name": "Base", "thickness_in": 6}])
check(rs["layers"] == [] and rs["subgrade_offset_ft"] == 0, "strip-only has no design surface: a base is ignored, never placed on nothing")

# ---- junk rows are dropped like the whole-sheet route does ------------------------
rj = run(design={"mode": "pad", "elev_ft": 100.0}, proposed_layers=[{"name": "x", "thickness_in": "abc"}, {"thickness_in": 0}])
check(rj["layers"] == [] and rj["cut_yd3"] == 0, "unreadable or zero thickness rows do nothing")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all zone-base checks passed")
