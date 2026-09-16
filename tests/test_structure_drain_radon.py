"""PERIMETER DRAIN AND RADON FROM THE STRUCTURE OUTLINE (2026-09-16; Beaumont
call 2026-09-15, 32:10-32:49): perimeter drain = LF around the OUTSIDE of the
whole foundation; radon pipe = LF along the INSIDE face of the exterior walls
(the outline pulled in by the wall thickness, mitred corners).

Fails on f98fa27 (no drain_lf / radon_lf, no Perimeter Drain / Radon items).

    python tests/test_structure_drain_radon.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="stdr_")
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="stdr_lg_")

import server  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


FPP = 40.0 / 72.0                      # 40 ft to the paper inch
SQ = [[0, 0], [180, 0], [180, 180], [0, 180]]           # 100 ft square
L_SHAPE = [[0, 0], [180, 0], [180, 90], [90, 90], [90, 180], [0, 180]]   # an L, 100 x 100 with a 50 x 50 notch


def one(pts, **kw):
    kw.setdefault("depth_ft", 6)
    return server._structure_one(dict(name="House", points=pts, **kw), FPP)


# ---- the inset arithmetic ----------------------------------------------------------
t_pt = (8 / 12.0) / FPP                   # an 8 in wall in page points
check(abs(server._inset_perim([tuple(p) for p in SQ], t_pt) - (720 - 8 * t_pt)) < 1e-6, "a rectangle inset by t loses 8 t of perimeter")
per_L = 180 + 90 + 90 + 90 + 90 + 180      # the L's perimeter: 720 pt, same as the square
check(abs(server._inset_perim([tuple(p) for p in L_SHAPE], t_pt) - (per_L - 5 * 2 * t_pt + 1 * 2 * t_pt)) < 1e-6,
      "an L (five convex corners, one concave) loses 10 t and gains back 2 t")
check(server._inset_perim([tuple(p) for p in SQ], 1000) == 0.0, "a wall thicker than the shape floors the radon length at 0, never negative")
check(abs(server._inset_perim([tuple(p) for p in list(reversed(SQ))], t_pt) - (720 - 8 * t_pt)) < 1e-6, "orientation does not matter")

# ---- through the route function ---------------------------------------------------
r = one(SQ)
check(r["wall_in"] == 8.0 and abs(r["drain_lf"] - 400) < 0.05, "default 8 in wall; perimeter drain = the outline's 400 LF")
check(abs(r["radon_lf"] - (400 - 8 * 8 / 12.0)) < 0.05, f"radon = 400 - 8 x (8/12) = {400 - 8*8/12:.2f} LF inside an 8 in wall (got {r['radon_lf']})")
r10 = one(SQ, wall_in=10)
check(abs(r10["radon_lf"] - (400 - 8 * 10 / 12.0)) < 0.05, "a 10 in wall pulls the radon run in further")
rl = one(L_SHAPE)
check(abs(rl["drain_lf"] - 400) < 0.05 and abs(rl["radon_lf"] - (400 - 10 * 8 / 12.0 + 2 * 8 / 12.0)) < 0.05,
      f"the L: drain 400 LF, radon 400 - 10 x 0.667 + 2 x 0.667 = {400 - 8*8/12:.2f} LF (got {rl['radon_lf']})")

# ---- the route's item list ---------------------------------------------------------
JID = "stdrtest1"
d = server._job_dir(JID); d.mkdir(parents=True, exist_ok=True)
import json  # noqa: E402
(d / "p0_result.json").write_text(json.dumps({"scale": {"ft_per_paper_inch": 40.0, "source": "test"}, "chains": []}))
server.JOBS[JID] = {"path": str(d / "nosuch.pdf"), "status": {}, "owner": None}
resp = server.structure_calc(JID, 0, server.StructureBody(structures=[dict(name="House", points=SQ, depth_ft=6), dict(name="Garage", points=L_SHAPE, depth_ft=4)]))
resp = json.loads(bytes(resp.body)) if hasattr(resp, "body") else resp
items = {it["label"]: (it["qty"], it["unit"]) for it in resp["extra_items"]}
check(items.get("Perimeter Drain") == (800.0, "LF"), f"the page items carry Perimeter Drain 800 LF over two structures (got {items.get('Perimeter Drain')})")
check("Radon" in items and items["Radon"][1] == "LF" and abs(items["Radon"][0] - (2 * 400 - 8 * 8 / 12.0 - 8 * 8 / 12.0)) < 0.2, f"and Radon in LF (got {items.get('Radon')})")
check([it["label"] for it in resp["extra_items"]][:4] == ["Mass Excavation", "Structure Excavation", "Structure Backfill", "Slab Prep"], "the four volume rows keep their order ahead of the two lineal rows")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all drain-radon checks passed")
