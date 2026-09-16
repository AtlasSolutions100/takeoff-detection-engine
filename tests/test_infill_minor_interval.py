"""THE MINOR INTERVAL, SIBLINGS BY SIDE, AND NO UTILITY CONTOURS (2026-09-16;
Barn C3.0 reconciliation, FINDINGS section 24).

On Barn C3.0 the labelled contours are the 5 ft index lines and the sheet
draws 1 ft intermediates between them in other linetype families. The
infill refused every fan between adjacent labels ("adjacent labels, nothing
between", 133 of them), the same-style rule kept the 161 intermediates out
of every fan, and the 10 in water main took a 7720 label by radius and
became a quarter of the proposed surface. Three fixes, three checks:

  1. infill_intervals: adjacent-label fans that consistently cross k
     siblings imply a step/(k+1) interval; the fan is counted again at it.
  2. the suggest route groups candidates by SIDE via the legend's role, so
     index and intermediate families count together.
  3. a utility-named layer or family never seeds and never joins a fan.

Synthetic sheet (pt == ft): index contours 100 / 105 / 110 as horizontal
lines with printed labels, four unlabelled intermediates between each pair
in another family, and a water main lying under a stray "108" label.
Fails on a75e228.

    python tests/test_infill_minor_interval.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = tempfile.mkdtemp(prefix="infill_")
os.environ["TAKEOFF_JOBS_DIR"] = JOBS
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="infill_lg_")

from takeoff_engine.earthwork import (_is_plan_chain, infill_intervals,  # noqa: E402
                                      is_utility_name)

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


def hline(y, x0=50.0, x1=550.0):
    return np.array([[x0, y], [x1, y]], float)


# ---- 1. the minor interval inside infill_intervals -------------------------------
assigned = [{"chain_id": 1, "pts": hline(100), "z": 100.0, "family": "A"},
            {"chain_id": 2, "pts": hline(150), "z": 105.0, "family": "A"},
            {"chain_id": 3, "pts": hline(200), "z": 110.0, "family": "A"}]
cands = [{"chain_id": 10 + i, "pts": hline(100 + 10 * (i + 1)), "family": "A"} for i in range(4)] \
      + [{"chain_id": 20 + i, "pts": hline(150 + 10 * (i + 1)), "family": "A"} for i in range(4)]
r = infill_intervals(assigned, cands, anchor_how={1: "radius", 2: "radius", 3: "radius"})
got = {row["chain_id"]: row["z"] for row in r["rows"]}
want = {10: 101.0, 11: 102.0, 12: 103.0, 13: 104.0, 20: 106.0, 21: 107.0, 22: 108.0, 23: 109.0}
check(got == want, f"four unlabelled lines between 5 ft index labels read 101..104 and 106..109 (got {got})")
iv = r["interval"]
check(iv.get("interval_ft") == 1.0 and iv.get("index_interval_ft") == 5.0, f"the interval reports 1 ft minor under 5 ft index labels (got {iv})")
check("4 unlabelled contours" in str(iv.get("source")) and "1 ft interval" in str(iv.get("source")), f"and says how it got there: {iv.get('source')}")
check(all(row["how"] == "interval-infill" and row["inferred"] for row in r["rows"]), "every row is an inferred interval-infill suggestion")
check("1 ft contour interval" in r["rows"][0]["src"], f"the row sentence names the 1 ft interval: {r['rows'][0]['src'][:90]}")
check(r.get("refused_at_index_interval", {}).get("adjacent_labels_nothing_between", 0) >= 3, "the index-step pass recorded the adjacent refusals it turned into the minor interval")
# a sheet whose labels ARE its interval: nothing between adjacent labels -> unchanged
r0 = infill_intervals(assigned, [], anchor_how={})
check(r0["rows"] == [] and r0["interval"].get("interval_ft") == 5.0 and "index_interval_ft" not in r0["interval"], "no siblings between adjacent labels: the 5 ft interval stands, nothing invented")
# one stray line between two labels is not an interval
r1 = infill_intervals(assigned[:2], [{"chain_id": 30, "pts": hline(125), "family": "A"}], anchor_how={})
check(r1["rows"] == [] and "index_interval_ft" not in r1["interval"], "one line between two labels does not make a 2.5 ft interval (needs a consensus of fans)")

# ---- 2. utility names -------------------------------------------------------------
check(is_utility_name("SE-P-UT-WATER-10''") and is_utility_name("PROPOSED WATER LINE (legend)") and is_utility_name("C-STRM-PIPE"), "water main layer, water line legend and storm pipe layer are utility names")
check(not is_utility_name("C-TOPO") and not is_utility_name("PROPOSED CONTOUR (legend)") and not is_utility_name("EXISTING CONTOUR (TRUE NORTH SURVEY) (legend)"), "contour families are not")
check(not is_utility_name("C-TOPO-DRAINAGE") and not is_utility_name("WATERSHED"), "DRAINAGE / WATERSHED as whole words are not utility tokens")


class _C:
    def __init__(self, layer, klass="solid", length=500.0):
        self.layer, self.klass, self.length = layer, klass, length


check(not _is_plan_chain(_C("SE-P-UT-WATER-10''")) and _is_plan_chain(_C("C-TOPO")) and _is_plan_chain(_C("")), "_is_plan_chain refuses the water main layer and keeps topo and blank layers")

# ---- 3. the route: siblings by side, a utility chain never joins ------------------
import pymupdf  # noqa: E402
import server  # noqa: E402

jid = "infillroute1"
d = server._job_dir(jid); d.mkdir(parents=True, exist_ok=True)
pdf = Path(JOBS) / "sheet.pdf"
doc = pymupdf.open(); pg = doc.new_page(width=612, height=792)
for y, z in ((100, "100"), (150, "105"), (200, "110")):
    pg.insert_text((300, y + 3), z, fontsize=8)          # a label on each index line
pg.insert_text((300, 253), "108", fontsize=8)              # a stray number over the water main, no contour near it
doc.save(str(pdf)); doc.close()


def chain(cid, sid, y, layer="", klass="solid"):
    return {"chain_id": cid, "style_id": sid, "class": klass, "layer": layer, "color": [0.0, 0.0, 0.0],
            "width": 0.5, "length_pt": 500.0, "points": [[50.0, y], [550.0, y]], "dash_signature": klass,
            "n_dashes": 0, "label_texts": [], "inline_labels": [], "gap_pt": 0.0, "ink_pt": 0.0}


chains = [chain(1, 1, 100), chain(2, 1, 150), chain(3, 1, 200)]                   # index, family 1
chains += [chain(10 + i, 2, 100 + 10 * (i + 1), klass="dash1") for i in range(4)]  # intermediates, family 2
chains += [chain(20 + i, 2, 150 + 10 * (i + 1), klass="dash1") for i in range(4)]
chains += [chain(40, 3, 250, layer="SE-P-UT-WATER-10''")]                          # the water main, under the stray label
styles = [{"style_id": 1, "suggested_label": "PROPOSED CONTOUR (legend)", "layer": "", "class": "solid", "color": [0, 0, 0], "width": 0.5, "n": 3, "length_pt": 1500.0},
          {"style_id": 2, "suggested_label": "PROPOSED 1-FT CONTOURS (legend)", "layer": "", "class": "dash1", "color": [0, 0, 0], "width": 0.5, "n": 8, "length_pt": 4000.0},
          {"style_id": 3, "suggested_label": "SE-P-UT-WATER-10''", "layer": "SE-P-UT-WATER-10''", "class": "solid", "color": [0, 0, 0], "width": 0.5, "n": 1, "length_pt": 500.0}]
(d / "p0_result.json").write_text(json.dumps({"scale": {"ft_per_paper_inch": 72.0, "source": "test"}, "chains": chains, "styles": styles}))
server.JOBS[jid] = {"path": str(pdf), "pages": 1, "status": {}, "owner": None}
resp = server.earthwork_suggest(jid, 0)
j = json.loads(bytes(resp.body)) if hasattr(resp, "body") else resp
rows = j["suggestions"]
byc = {r["chain_id"]: r for r in rows}
check(all(byc.get(c, {}).get("z") == z for c, z in ((1, 100.0), (2, 105.0), (3, 110.0))), f"the three index labels are read (got {[(c, byc.get(c, {}).get('z')) for c in (1, 2, 3)]})")
inf = {c: r for c, r in byc.items() if r.get("how") == "interval-infill"}
got_inf = {c: r["z"] for c, r in inf.items()}
check(got_inf == want, f"the eight intermediates in the OTHER family are infilled by side (got {got_inf})")
check(any(f.get("style_id") == 3 for f in j["families_excluded"]) and not any("TTLB" in str(f.get("label")) for f in j["families_excluded"] if f.get("reason", "").startswith("utility")), "only utility families carry the utility reason")
check(all(r.get("for_surface") == "proposed" and r.get("inferred") for r in inf.values()), "as inferred proposed-side suggestions")
check(40 not in byc, "the water main never becomes a contour, even under a printed number")
util = [f for f in j["families_excluded"] if f.get("reason") == "utility line, not a contour"]
check(util and util[0]["style_id"] == 3 and util[0]["n_chains"] == 1, f"and the excluded list says why (got {util})")
diag = j["interval_infill"]["proposed"]
check(diag["interval"].get("interval_ft") == 1.0 and diag["interval"].get("index_interval_ft") == 5.0, f"the route's diag carries the minor interval (got {diag['interval']})")
check(diag["families"] == ["side:proposed"], f"the fan's family is the side (got {diag['families']})")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all infill-minor-interval checks passed")
