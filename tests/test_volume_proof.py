"""WHAT BACKS THE VOLUME: the checks, and what they refuse to claim.

Measured basis for the whole idea (2026-09-17,
out/verify/2026-08-31/cutfill-contour-method/synth_out.json re-read, and
scratchpad/probe_enterprise_audit.py): the interpolation kernel is within
0.5% of exact on analytic surfaces (0.00% on a plane) and the grid moves a
real sheet's cut by +-0.4% across cell sizes 1-8 ft. So a wrong cut/fill is
a wrong INPUT, and the estimator cannot see inputs. `volume_proof` checks
them and says so:

  1. the surface against the elevations the sheet PRINTS as points - ground
     tags only (ME/EG now, FG/FIN/FFE/PE/HP/LP/FL designed; a wall top or a
     rim is not ground). On Oncor C-04 this found the existing surface
     sitting 0.91 ft below all seven of the sheet's match-existing spots.
  2. the same rows through the other surface model (they differ by 30% on
     Stice's rows, so the spread is real uncertainty).
  3. the same rows on a finer grid.
  4. whether the region is the estimator's own limit of work.

Pinned here on planes, where the right answer is arithmetic: a uniform 4 ft
lift over a 1% ramp.

    python tests/test_volume_proof.py    (fails on the commit before it)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

import takeoff_engine.earthwork as ew  # noqa: E402

FAILS = []


def check(ok, msg):
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        FAILS.append(msg)


FT_PER_PT = 1.0            # 1 pt = 1 ft keeps the arithmetic readable


def ramp(z0):
    """Three parallel contours 100 pt apart, 1 ft of rise each: the linear
    TIN through them is exactly z = z0 + y / 100."""
    s = ew.Surface("s")
    for i, z in enumerate((z0, z0 + 1.0, z0 + 2.0)):
        y = i * 100.0
        s.contours.append((np.array([[0.0, y], [200.0, y], [400.0, y]]), z))
    return s


def spot(x, y, z, tag, side):
    return {"x": x, "y": y, "z": z, "tag": tag, "side": side,
            "resolved": True, "res": "printed"}


EX, PR = ramp(100.0), ramp(104.0)          # a uniform 4 ft lift
TRUE_EX = lambda y: 100.0 + y / 100.0      # noqa: E731
BOX = [[0.0, 0.0], [400.0, 0.0], [400.0, 200.0], [0.0, 200.0]]

# --- 1. spot_residuals: ground tags only, and the arithmetic --------------
spots = [spot(200.0, 50.0, TRUE_EX(50.0) + 1.0, "ME", "existing"),   # 1 ft high
         spot(100.0, 150.0, TRUE_EX(150.0) + 0.5, "EG", "existing"),  # 0.5 high
         spot(300.0, 100.0, 999.0, "TW", "proposed")]                 # a WALL
r = ew.spot_residuals(EX, spots, "existing")
print("   existing residuals:", {k: r.get(k) for k in
                                 ("n_printed", "n_checked", "median_abs_ft", "bias_ft", "max_abs_ft")})
check(r["n_printed"] == 2 and r["n_checked"] == 2,
      "only the GROUND spots of that side are checked - the wall top is not one  (%s)"
      % {k: r.get(k) for k in ("n_printed", "n_checked")})
check(r["median_abs_ft"] == 0.75 and r["bias_ft"] == 0.75 and r["max_abs_ft"] == 1.0,
      "the residuals are the arithmetic: 1.0 and 0.5 ft high -> median 0.75, bias +0.75, worst 1.0")
check(r["worst"] and r["worst"][0]["diff_ft"] == 1.0 and r["worst"][0]["tag"] == "ME",
      "...and the worst point is named, with what the sheet printed and what we read  (%s)"
      % (r["worst"][0] if r.get("worst") else None))
r2 = ew.spot_residuals(PR, spots, "proposed")
check(r2["n_checked"] == 0 and "why" in r2 and "no ground spot" in r2["why"],
      "a side with no ground spot printed on it ABSTAINS and says why  (%s)" % r2.get("why"))

# --- 2. volume_proof: the four checks, and the volume untouched -----------
vol = ew.cut_fill(EX, PR, FT_PER_PT, cell_ft=2.0, boundary=BOX)
before = dict(vol)
p = ew.volume_proof(EX, PR, vol, spots, FT_PER_PT, cell_ft=2.0, boundary=BOX)
check(vol["cut_yd3"] == before["cut_yd3"] and vol["fill_yd3"] == before["fill_yd3"],
      "the proof does not touch the volume it explains")
ids = [c["id"] for c in p["checks"]]
print("   checks:", [(c["id"], c["state"]) for c in p["checks"]])
check(ids == ["spots", "model", "grid", "region"],
      "all four checks are reported, in order  (%s)" % ids)
st = {c["id"]: c["state"] for c in p["checks"]}
check(st["spots"] == "warn",
      "a 0.75 ft bias against the sheet's own points is a WARN, not a pass  (%s)" % st["spots"])
check(st["region"] == "pass", "a bounded run passes the region check")
check(st["model"] == "pass" and st["grid"] == "pass",
      "on planes the two models and the finer grid agree, so both pass  (%s, %s)"
      % (st["model"], st["grid"]))
check(p["verdict"] == "warn", "one warn makes the verdict warn  (%s)" % p["verdict"])
ms = p.get("model_spread") or {}
check(ms.get("other_model") == "clamped_ct" and ms.get("other_fill_yd3") is not None
      and ms.get("fill_spread_pct") is not None,
      "the other model's own numbers travel with the spread  (%s)" % ms)
# a 4 ft lift everywhere is pure fill: cut is 0 both ways, and 0 against 0
# has no honest percentage - None, never a fabricated 0%
check(vol["cut_yd3"] == 0 and ms.get("cut_spread_pct") is None,
      "a quantity that is zero under both methods reports no spread rather than a made-up one")

# --- 3. a clean run passes, and an unbounded one warns about the region ---
p2 = ew.volume_proof(EX, PR, vol, [spot(200.0, 50.0, TRUE_EX(50.0), "ME", "existing")],
                     FT_PER_PT, cell_ft=2.0, boundary=BOX)
check(p2["verdict"] == "pass" and all(c["state"] == "pass" for c in p2["checks"]),
      "a surface that matches the sheet's point, bounded, on agreeing methods: pass  (%s)"
      % [(c["id"], c["state"]) for c in p2["checks"]])
vol3 = ew.cut_fill(EX, PR, FT_PER_PT, cell_ft=2.0)
p3 = ew.volume_proof(EX, PR, vol3, spots, FT_PER_PT, cell_ft=2.0, boundary=None)
check({c["id"]: c["state"] for c in p3["checks"]}["region"] == "warn"
      and "not the graded area" in [c["text"] for c in p3["checks"] if c["id"] == "region"][0],
      "with no limit of work the region is called out in words")

# --- 4. nothing to check is never a pass ----------------------------------
p4 = ew.volume_proof(EX, PR, vol, [], FT_PER_PT, cell_ft=2.0, boundary=BOX)
sp = [c for c in p4["checks"] if c["id"] == "spots"][0]
check(sp["state"] == "cannot check" and p4["verdict"] == "cannot check",
      "a sheet that prints no ground spot cannot be checked - and that is NOT a pass  (%s)"
      % p4["verdict"])
p5 = ew.volume_proof(EX, PR, {"cut_yd3": None}, spots, FT_PER_PT)
check(p5.get("checks") == [] and "why" in p5,
      "no published volume, nothing to back  (%s)" % p5.get("why"))

# --- 5. the limits are declared, not buried -------------------------------
check(set(ew.PROOF_LIMITS) == {"residual_median_ft", "residual_bias_ft", "method_spread_pct"}
      and p["limits"] == ew.PROOF_LIMITS,
      "the thresholds ride with the answer so a reader can disagree with them  (%s)" % p.get("limits"))

# --- 6. A CHECK THAT CANNOT FAIL IS WORTHLESS ----------------------------
# The panel feeds the sheet's spot grades into the surface as points, so
# asking the surface about those same points is asking it to repeat
# itself: live on Oncor C-04 that read 0.00 ft where the same sheet read
# 0.91 ft with the spots left out. A spot that IS an input must be held
# out and the surface rebuilt without it.
IN_SPOT = spot(200.0, 50.0, TRUE_EX(50.0) + 1.0, "ME", "existing")   # 1 ft high
FREE_SPOT = spot(100.0, 150.0, TRUE_EX(150.0) + 0.5, "EG", "existing")
EX_FED = ramp(100.0)
EX_FED.points.append((200.0, 50.0, IN_SPOT["z"]))      # as the panel sends it
naive = ew.spot_residuals(EX_FED, [IN_SPOT], "existing")
print("   fed-in spot:", {k: naive.get(k) for k in
                          ("n_checked", "how", "n_from_surface", "median_abs_ft", "bias_ft")})
check(naive.get("how") == "cross-validated" and naive.get("n_from_surface") == 1,
      "a spot the surface was BUILT from is cross-validated, not compared with itself  (%s)"
      % naive.get("how"))
check(naive.get("median_abs_ft") == 1.0,
      "...and held out, it reports the real 1.0 ft miss instead of a tautological 0.00  (%s)"
      % naive.get("median_abs_ft"))
mixed = ew.spot_residuals(EX_FED, [IN_SPOT, FREE_SPOT], "existing")
check(mixed.get("how") == "cross-validated + independent" and mixed["n_checked"] == 2,
      "a sheet with both kinds says so  (%s)" % mixed.get("how"))
check(all(w.get("how") in ("independent", "cross-validated") for w in mixed["worst"]),
      "...and every named point carries how it was checked")

print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
