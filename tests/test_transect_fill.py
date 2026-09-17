"""COUNTING ALONG A LINE THE ESTIMATOR DRAWS (2026-09-17).

Stage 1's ordering solver was stopped because software cannot recover which
contour comes next: adjacency means "one interval apart" 33-51% of the time
raw, and Barn C3.0's printed labels give no same-family adjacency at all
(FINDINGS 42). The information is not on the sheet in a recoverable form.

It is in the estimator's head, and one stroke across the fan states it. The
crossings along that stroke ARE the order; he gives the start, the step and
the direction; the software counts. Nothing is inferred.

MEASURED ON THE REAL SHEETS (scratchpad/probe_transect_fan.py, 2026-09-17):
strokes cast square across the fan from every printed label crossed 258
other printed labels across Oncor C-04, Barn C3.0 and C2.1, and the count
disagreed with NONE of them. The same strokes take coverage by length from
67% to 77% on C-04, 41% to 49% on Barn, 71% to 79% on C2.1.

The candidate set is the whole game. Counting every line on the page
instead of the anchor's own family walks the count 75 ft off on Barn
(-5.7 ft on C-04), because the stroke picks up the other surface's contours
on the way. That is the caller's job and this file pins the behaviour that
makes it survivable: a printed elevation is never overwritten, it is the
CHECK, and two lines the stroke cannot order are refused.

    python tests/test_transect_fill.py  (fails before transect_fill existed)
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


def band(y, x0=0.0, x1=1000.0, n=21):
    """One contour drawn left to right at height y on the page."""
    return np.column_stack([np.linspace(x0, x1, n), np.full(n, float(y))])


# five contours 100 pt apart; the stroke runs up the page across all five
LINES = [(i + 1, band(100 * (i + 1))) for i in range(5)]
UP = [[500.0, 50.0], [500.0, 550.0]]

# --- 1. the crossings are the order ---------------------------------------
print("\n1. the stroke states the order, the operator states the rest")
t = ew.transect_fill(LINES, UP, 550.0, 1.0)
check([(r["chain_id"], r["z"]) for r in t["rows"]]
      == [(1, 550.0), (2, 551.0), (3, 552.0), (4, 553.0), (5, 554.0)],
      "every line crossed gets the next step, in the order the stroke meets them")
check(t["n_crossed"] == 5 and not t["refused"],
      "...and the count is reported  (%s crossed)" % t["n_crossed"])
check([r["order"] for r in t["rows"]] == [1, 2, 3, 4, 5]
      and all(len(r["at"]) == 2 for r in t["rows"]),
      "each row carries its place in the count and where it was crossed")
down = ew.transect_fill(LINES, UP[::-1], 550.0, -1.0)
check([(r["chain_id"], r["z"]) for r in down["rows"]]
      == [(5, 550.0), (4, 549.0), (3, 548.0), (2, 547.0), (1, 546.0)],
      "drawn the other way and stepped down, it counts the other way")
half = ew.transect_fill(LINES, UP, 100.0, 0.5)
check([r["z"] for r in half["rows"]] == [100.0, 100.5, 101.0, 101.5, 102.0],
      "the step is the operator's, not the software's  (%s)" % [r["z"] for r in half["rows"]])

# --- 2. THE SHEET CHECKS THE COUNT ----------------------------------------
print("\n2. a printed elevation is never overwritten - it is the check")
t = ew.transect_fill(LINES, UP, 550.0, 1.0, printed={2: 551.0, 4: 560.0})
ids = [r["chain_id"] for r in t["rows"]]
check(2 not in ids and 4 not in ids,
      "a line the sheet already labels gets no counted value  (%s)" % ids)
ok2 = [c for c in t["checks"] if c["chain_id"] == 2][0]
bad = [c for c in t["checks"] if c["chain_id"] == 4][0]
check(ok2["agrees"] and abs(ok2["diff_ft"]) < 1e-9,
      "where the count meets a printed 551 it agrees, and says so")
check(not bad["agrees"] and abs(bad["diff_ft"] - 7.0) < 1e-9,
      "where the sheet prints 560 and the count says 553, the gap is stated: %s ft"
      % bad["diff_ft"])
check(t["agrees"] == 1 and t["disagrees"] == 1,
      "both are counted, so a caller can refuse the whole stroke on a disagreement")
check(all("at" in c for c in t["checks"]),
      "...and each disagreement says WHERE, so an estimator can walk to it")

# --- 3. what it refuses ----------------------------------------------------
print("\n3. the ordering problem in miniature: what the stroke cannot say")
TIE = LINES + [(99, band(300.5))]      # a twin stroke half a point away
t = ew.transect_fill(TIE, UP, 550.0, 1.0)
tied = {r["chain_id"] for r in t["refused"]}
check(tied == {3, 99},
      "two lines crossed at the same place are BOTH refused, not ordered by "
      "a coin toss  (%s)" % sorted(tied))
check(any("does not say which comes first" in r["why"] for r in t["refused"]),
      "...and the reason says what the estimator should do about it")
check(3 not in {r["chain_id"] for r in t["rows"]},
      "a refused line gets no value")
check(all(r["z"] is not None for r in t["rows"]),
      "...while the rest of the stroke still counts")
none = ew.transect_fill(LINES, [[2000.0, 50.0], [2000.0, 550.0]], 550.0, 1.0)
check(none["rows"] == [] and none["n_crossed"] == 0,
      "a stroke that crosses nothing fills nothing")
short = ew.transect_fill(LINES, [[500.0, 50.0]], 550.0, 1.0)
check(not short["rows"] and short["refused"],
      "a stroke of one point is refused, with a reason")

# --- 4. the shapes a real sheet throws at it -------------------------------
print("\n4. real linework")
# a contour that wanders back across the stroke counts ONCE, at first meeting
WIGGLE = [(7, np.array([[400.0, 200.0], [520.0, 200.0], [520.0, 260.0],
                        [480.0, 260.0], [480.0, 320.0], [600.0, 320.0]]))]
t = ew.transect_fill(WIGGLE, UP, 100.0, 1.0)
check(len(t["rows"]) == 1 and t["rows"][0]["z"] == 100.0,
      "a line the stroke meets three times is counted once  (%s rows)" % len(t["rows"]))
check("3 times" in (t["rows"][0].get("note") or ""),
      "...and it says so, because that is where a miscount hides  (%r)"
      % (t["rows"][0].get("note") or "")[:60])
# a dog-legged stroke, which is what a mouse actually draws
DOG = [[100.0, 50.0], [500.0, 250.0], [900.0, 550.0]]
t = ew.transect_fill(LINES, DOG, 10.0, 1.0)
check([r["chain_id"] for r in t["rows"]] == [1, 2, 3, 4, 5],
      "a stroke with a bend in it still counts along its own length  (%s)"
      % [r["chain_id"] for r in t["rows"]])
check([r["dist_pt"] for r in t["rows"]] == sorted(r["dist_pt"] for r in t["rows"]),
      "...and the distances are measured along the stroke, not as the crow flies")

print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
