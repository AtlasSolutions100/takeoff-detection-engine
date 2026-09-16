"""THE CONSENSUS PICK NEVER RAISES (2026-09-16). dimension_check grows the
consensus cluster with the tolerance measured from its seed value, then
picked each member word's reading among those within tolerance of the
cluster MEDIAN - a window that can exclude the very reading that made the
word a member (seed 9.8, 10% tol, members 9.0 / 9.1 / 9.8 / 10.7: median 9.45,
window 8.5-10.4, the word whose only reading is 10.7 has nothing inside it). On Barn
Structural S1-1.3 that was min() of an empty iterable and a 500 from
/scale/dimensions, which the sheet header depends on. Fails on f178176.

    python tests/test_dimcheck_pick.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from takeoff_engine.dimcheck import AGREE_TOL, _pick_reading  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


R = lambda v, cid: {"line_pt": 100.0, "chain_id": cid, "implied_ft_per_in": v}  # noqa: E731
p = _pick_reading([R(11.0, 1)], 9.0, 0.10)
check(p["implied_ft_per_in"] == 11.0, "a member word whose only reading sits outside the median's window keeps that reading (no crash)")
p = _pick_reading([R(12.0, 1), R(9.2, 2), R(8.0, 3)], 9.0, 0.10)
check(p["chain_id"] == 2, "with readings inside the window, the nearest to the median wins")
p = _pick_reading([R(12.0, 1), R(7.0, 2)], 9.0, 0.10)
check(p["chain_id"] == 2, "with none inside, the nearest overall wins (7 is 2 away, 12 is 3)")
check(0 < AGREE_TOL < 1, f"AGREE_TOL is a fraction ({AGREE_TOL})")

# the shape that raised, end to end through the same arithmetic the route uses
witnesses = [{"readings": [R(9.0, 1)]}, {"readings": [R(9.1, 2)]}, {"readings": [R(9.8, 3)]}, {"readings": [R(10.7, 4)]}]
pool = [(i, r["implied_ft_per_in"]) for i, w in enumerate(witnesses) for r in w["readings"]]
best_words, best_med = set(), None
import numpy as np  # noqa: E402
for _i, v in pool:
    members = [(j, u) for j, u in pool if abs(u - v) <= 0.10 * v]
    ws = {j for j, _u in members}
    if len(ws) > len(best_words):
        best_words, best_med = ws, float(np.median([u for _j, u in members]))
check(best_words == {0, 1, 2, 3} and abs(best_med - 9.45) < 1e-9, f"the cluster seeded at 9.8 holds all four words (window 8.82-10.78) with median 9.45, whose window 8.5-10.4 no longer holds the 10.7 word (got {best_words}, {best_med})")
try:
    picks = [_pick_reading(w["readings"], best_med, 0.10) for w in witnesses]
    check([p["chain_id"] for p in picks] == [1, 2, 3, 4], "every member gets a pick, the 10.7 word its own reading")
except ValueError as e:
    check(False, f"raised: {e}")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all dimcheck-pick checks passed")
