"""The review's decisions reach the cut / fill (PLAN 5.4, cutfill/core_edits.py):

* a piece of the engine's linework lying on a line ERASED in review is left out of the surfaces,
  unless the operator set something on it in the engine, and never a piece lying on a line the
  reviewer put in the erased line's place (a split, an extension);
* a pen whose linework the review NAMES as a surface's contours takes that surface;
* the WORK AREA is the limit when none is drawn;
* an analysis made before the review changed is not current, and compute refuses it.

Everything by geometry: the ids of the product's chains never reach the engine's pieces.
"""
import json
import os
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
STORE = tempfile.mkdtemp(prefix="cf-review-")
os.environ["CUTFILL_OUT"] = STORE

from cutfill import core_edits as ce  # noqa: E402
from cutfill.chains import Chain, StyleGroup  # noqa: E402
from cutfill.geom import poly_length  # noqa: E402

fails = 0


def check(ok, what):
    global fails
    print(("ok: " if ok else "FAIL: ") + what)
    if not ok:
        fails += 1


def chain(cid, style, pts, candidate=True):
    P = np.asarray(pts, dtype=float)
    L = poly_length(P)
    return Chain(id=cid, style=style, pts=P, members=[], length=L, ink=L, closed=False, candidate=candidate)


def wavy(y, x0=100.0, x1=900.0, n=81, amp=6.0):
    xs = np.linspace(x0, x1, n)
    return [[float(x), float(y + amp * np.sin(x / 60.0))] for x in xs]


# ---- a label names a surface only with a surface word and one side's word ---------------------------
for text, want in (("EXISTING CONTOUR", "existing"), ("EX. CONTOUR (1' INTERVAL)", "existing"),
                   ("PROPOSED CONTOUR", "proposed"), ("FINISHED GRADE CONTOUR", "proposed"),
                   ("PROPOSED 5' INDEX CONTOUR", "proposed"), ("EXISTING GROUND", "existing"),
                   ("EXISTING WATER LINE", None), ("PROPOSED STORM DRAIN", None), ("CONTOUR", None),
                   ("EXISTING AND PROPOSED CONTOURS", None), ("CE-TOPO-MINR", None), ("PROPOSED GRADE BREAK", None),
                   ("LIMIT OF GRADING", None), ("unlabeled", None), ("", None), (None, None)):
    check(ce.role_of_label(text) == want, f"role_of_label({text!r}) = {ce.role_of_label(text)!r} (want {want!r})")

# ---- the hand-over: the erased lines' geometry, the named lines, the work area -----------------------
core = {"chains": [
    {"chain_id": 11, "style_id": 1, "points": wavy(200)},          # existing contour (named by its family)
    {"chain_id": 12, "style_id": 1, "points": wavy(260)},          # existing contour, ERASED in review
    {"chain_id": 13, "style_id": 2, "points": wavy(320)},          # proposed contour
    {"chain_id": 14, "style_id": 3, "points": wavy(380)},          # a water line
    {"chain_id": 15, "style_id": 2, "points": wavy(440)},          # proposed, erased and SPLIT by the reviewer
]}
body = {"excluded_chain_ids": [12, 15, 999], "labels": {"1": "EXISTING CONTOUR", "2": "PROPOSED CONTOUR", "3": "EXISTING WATER LINE"},
        "chain_labels": {}, "added_chains": [{"points": wavy(440, 100, 500, 41)}, {"points": wavy(440, 500, 900, 41)}],
        "work_zones": [[150, 150, 850, 500], [300, 300, 310, 300.5]]}
ed = ce.normalize(core, body)
check(ed is not None and len(ed["erased"]) == 2 and len(ed["added"]) == 2 and ed["work_zones"] == [[150.0, 150.0, 850.0, 500.0]],
      f"normalize: two erased lines with their geometry (an unknown id carries none), two added, one real work zone: "
      f"{len(ed['erased'])} / {len(ed['added'])} / {ed['work_zones']}")
check(sorted(n["role"] for n in ed["named"]) == ["existing", "proposed"] and all(len(n["pts"]) >= 2 for n in ed["named"]),
      f"normalize: the named lines are the ones still standing (the erased ones carry no name): {[n['role'] for n in ed['named']]}")
check(len(ed["sig"]) == 12 and ce.normalize(core, body)["sig"] == ed["sig"], "the signature is stable")
check(ce.normalize(core, dict(body, excluded_chain_ids=[12]))["sig"] != ed["sig"], "and moves with the decisions")
check(ce.normalize(core, {}) is None and ce.normalize(core, None) is None and ce.normalize(None, {"excluded_chain_ids": [1]}) is None,
      "nothing erased, named or drawn: nothing to keep")
check(all(len(ln) <= 400 for ln in ed["erased"]) and max(len(ln) for ln in ed["erased"]) < 81,
      f"lines are kept by their shape, not by every vertex ({[len(ln) for ln in ed['erased']]} points of 81)")
sm = ce.summary_of(ed)
check(sm == {"sig": ed["sig"], "erased": 2, "added": 2, "named": 2, "work_zones": [[150.0, 150.0, 850.0, 500.0]]}, f"the page settings keep counts only: {sm}")

# ---- the engine's own pieces: shorter, numbered differently, a little off the product's line ---------
styles = [StyleGroup(id=0, color=(0.5, 0.5, 0.5), gray=0.5, width=0.5, layer="", dashed=True, n_candidates=4, cand_length=1600.0),
          StyleGroup(id=1, color=(0, 0, 0), gray=0.0, width=0.7, layer="", dashed=False, n_candidates=4, cand_length=1600.0),
          StyleGroup(id=2, color=(0, 0, 1), gray=0.1, width=0.5, layer="", dashed=False, n_candidates=1, cand_length=800.0)]
eng = [chain(0, 0, wavy(200.4, 100, 480, 39)), chain(1, 0, wavy(200.4, 520, 900, 39)),          # on chain 11
       chain(2, 0, wavy(260.3, 100, 480, 39)), chain(3, 0, wavy(260.3, 520, 900, 39)),          # on chain 12 (erased)
       chain(4, 1, wavy(320.0, 100, 900, 81)),                                                   # on chain 13
       chain(5, 2, wavy(380.0, 100, 900, 81)),                                                   # the water line
       chain(6, 1, wavy(440.2, 100, 500, 41)), chain(7, 1, wavy(440.2, 500, 900, 41)),          # on the two halves of 15
       chain(8, 0, [[100, 260], [104, 260]])]                                                    # a 4-pt stub
out, length, held = ce.erased_pieces(eng, ed)
check(out == {2, 3} and held == 0 and abs(length - (eng[2].length + eng[3].length)) < 0.1,
      f"the two pieces on the erased contour are left out; the halves the reviewer put in place of the split line stand: {sorted(out)}")
out2, _, held2 = ce.erased_pieces(eng, ed, keep={3})
check(out2 == {2} and held2 == 1, f"a piece the operator set something on in the engine is kept, and counted: {sorted(out2)}, held {held2}")
check(ce.erased_pieces(eng, None) == (set(), 0.0, 0) and ce.erased_pieces(eng, {"erased": []}) == (set(), 0.0, 0), "no erased lines: nothing is left out")
# a long engine piece only partly on an erased line is not erased (under 60 % of it)
long_piece = chain(9, 0, wavy(260.3, 100, 900, 81))
part = ce.normalize({"chains": [{"chain_id": 1, "style_id": 1, "points": wavy(260, 100, 400, 31)}]}, {"excluded_chain_ids": [1]})
check(ce.erased_pieces([long_piece], part)[0] == set(), "an erased line covering 3/8 of a piece does not take the piece out")

roles = ce.roles_from_review([c for c in eng if c.id not in out], styles, ed)      # as interpret calls it: the erased pieces are out
check(roles.get(0, (None,))[0] == "existing" and "EXISTING CONTOUR" in roles[0][1] and roles.get(1, (None,))[0] == "proposed" and 2 not in roles,
      f"the pens take the surface the review names; the water line's pen takes none: {roles}")
# a pen the review names both ways is left to the engine's own reading
both = ce.normalize({"chains": [{"chain_id": 1, "style_id": 1, "points": wavy(200)}, {"chain_id": 2, "style_id": 2, "points": wavy(260)}]},
                    {"labels": {"1": "EXISTING CONTOUR", "2": "PROPOSED CONTOUR"}})
r2 = ce.roles_from_review([chain(0, 0, wavy(200.2)), chain(1, 0, wavy(260.2))], [styles[0]], both)
check(r2.get(0, ("x",))[0] is None and "both ways" in r2[0][1], f"named both ways: no surface is forced, and it is said: {r2}")
# too little of the pen is named: nothing is forced
few = ce.roles_from_review([chain(0, 0, wavy(200.2, 100, 300, 21))] + [chain(i, 0, wavy(500 + 20 * i)) for i in range(1, 4)],
                           [StyleGroup(id=0, color=(0, 0, 0), gray=0, width=0.5, layer="", dashed=False, n_candidates=4, cand_length=2600.0)],
                           ce.normalize({"chains": [{"chain_id": 1, "style_id": 1, "points": wavy(200, 100, 300, 21)}]}, {"labels": {"1": "EXISTING CONTOUR"}}))
check(few == {}, f"200 pt named of a 2,600-pt pen forces nothing: {few}")

wa = ce.work_area(ed)
check(wa is not None and abs(wa.area - 700 * 350) < 1e-6 and ce.work_area(None) is None and ce.work_area({"work_zones": []}) is None,
      "the work area is the union of its boxes")

# ---- a project: the file beside the analysis, the settings' summary, staleness, the limit ------------
import pymupdf  # noqa: E402
from cutfill.project import Project, compute_cutfill  # noqa: E402

pdf = os.path.join(STORE, "planes.pdf")
doc = pymupdf.open()
doc.new_page(width=600, height=600)
doc.save(pdf)
doc.close()
p = Project.create("review", pid="review")
p.add_document(pdf, name="planes.pdf", copy=False)
key = "d0p0"
p.page_settings(key)["role"] = "both"
p.data["settings"].update({"scale_ft_per_in": 72.0, "scale_source": "typed", "cell_ft": 5.0, "display_page": key})
pts_e, pts_p = [], []
for y in range(80, 521, 40):
    pts_e.append({"id": len(pts_e), "label_id": None, "x": 80.0, "y": float(y), "z": 100.8, "side": "existing", "tag": "EG", "anchor": "dot"})
    pts_e.append({"id": len(pts_e), "label_id": None, "x": 520.0, "y": float(y), "z": 105.2, "side": "existing", "tag": "EG", "anchor": "dot"})
    pts_p.append({"id": 100 + len(pts_p), "label_id": None, "x": 80.0, "y": float(y), "z": 102.0, "side": "proposed", "tag": "FG", "anchor": "dot"})
    pts_p.append({"id": 100 + len(pts_p), "label_id": None, "x": 520.0, "y": float(y), "z": 102.0, "side": "proposed", "tag": "FG", "anchor": "dot"})
an = {"engine": "test", "key": key, "doc": 0, "page": 0, "width": 600.0, "height": 600.0, "sheet": "T1", "page_role": "both", "lines_from": "engine",
      "styles": [], "families": [], "gaps": [], "labels": [], "chains": [], "contours": [], "spots": pts_e + pts_p, "counts": {}, "notes": [],
      "scale_candidates": [], "scale_default": None}
os.makedirs(os.path.dirname(p.analysis_path(key)), exist_ok=True)
json.dump(an, open(p.analysis_path(key), "w"))
p.save()
res0 = compute_cutfill(p)
check(not res0.get("error") and res0["region_source"].startswith("overlap"), f"no limit, no review: the overlap of the two surfaces ({res0.get('error')})")
check(p.core_edits(key) is None and not p.review_stale(key), "no review handed over: nothing in force, nothing stale")
zone_only = ce.normalize({"chains": []}, {"work_zones": [[100, 100, 300, 500]]})
check(p.put_core_edits(key, zone_only) is True and p.put_core_edits(key, zone_only) is False, "put_core_edits says when the review changed")
check(p.page_settings(key)["core_edits"]["sig"] == zone_only["sig"] and os.path.exists(p.core_edits_path(key))
      and p.core_edits(key)["work_zones"] == [[100.0, 100.0, 300.0, 500.0]], "the lines live in a file beside the analysis, the settings name it")
check(p.review_stale(key) and p.analysis_stale(key), "an analysis made before the review changed is not current")
r = compute_cutfill(p)
check(bool(r.get("error")) and "review" in r["error"] and "sheet T1" in r["error"] and "Update" in r["error"] and "Analyze" not in r["error"],
      f"compute refuses it and names the sheet as the screen does (its printed number) and the button as it reads: {r.get('error')}")
an["review"] = zone_only["sig"]                      # the sheet is read again with the review
json.dump(an, open(p.analysis_path(key), "w"))
p._an_cache.pop(key, None)
check(not p.review_stale(key), "read again with the review: current")
r = compute_cutfill(p)
check(not r.get("error") and r["region_source"].startswith("the work area drawn in review") and abs(r["totals"]["area_sf"] - 200 * 400) < 1
      and r["limit"] is None,
      f"the work area is the limit when none is drawn ({r.get('region_source')}, {r.get('totals', {}).get('area_sf')} SF); it makes no limit rows")
# a drawn limit outranks the work area
p.data["settings"]["region"] = [[100, 100], [500, 100], [500, 500], [100, 500]]
r = compute_cutfill(p)
check(not r.get("error") and r["region_source"] == "drawn" and abs(r["totals"]["area_sf"] - 160000) < 1, "a drawn limit outranks the work area")
p.data["settings"]["region"] = None
# two separate boxes: the largest is the limit and the rest is named
two = ce.normalize({"chains": []}, {"work_zones": [[100, 100, 300, 500], [350, 100, 450, 300]]})
p.put_core_edits(key, two)
an["review"] = two["sig"]
json.dump(an, open(p.analysis_path(key), "w"))
p._an_cache.pop(key, None)
r = compute_cutfill(p)
check(not r.get("error") and abs(r["totals"]["area_sf"] - 80000) < 1 and any("separate parts" in w and "20,000 SF" in w for w in r["warnings"]),
      f"two separate boxes: the largest is the limit, the other is named with its area: {[w for w in r['warnings'] if 'separate' in w]}")
# a stray file is never applied
json.dump(dict(two, sig="0" * 12), open(p.core_edits_path(key), "w"))
check(p.core_edits(key) is None, "a file that is not the one the settings name is not applied")
check(p.put_core_edits(key, None) is True and p.page_settings(key)["core_edits"] is None and not os.path.exists(p.core_edits_path(key)),
      "clearing the review removes the file")

print("\n%d failure(s)" % fails)
sys.exit(1 if fails else 0)
