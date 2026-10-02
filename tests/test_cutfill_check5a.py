"""CHECK 5A ON THE CUT / FILL SCREENS (2026-09-27, VERIFY-5A.md): the server side of the fixes, on a
synthetic project (two planes, a hand-made analysis, 1 pt = 1 ft):

* the limit in force as the server measures it (what the Limit card now shows): a ring that
  crosses itself is measured repaired, and says so;
* "Where the volume sits" counts each cell by its share inside the limit, like the totals: an
  area along the limit's edge never holds more than the limit;
* a scale in a sentence has two decimals at most (the host's hand-off notes, shown in the engine);
* the words: a sheet by its number or page (never the engine's key) and a button as it reads; a
  zero total prints as 0; one piece "lies" and "carries".

    python tests/test_cutfill_check5a.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
STORE = tempfile.mkdtemp(prefix="cf5a_")
os.environ["TAKEOFF_JOBS_DIR"] = STORE
os.environ["CUTFILL_OUT"] = os.path.join(STORE, "cutfill")
os.environ["CUTFILL_UI_DIR"] = str(ROOT / "webui" / "cutfill")

import pymupdf  # noqa: E402

from cutfill.project import Project, compute_cutfill, takeoff_preview  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


def planes_project(pid, z_existing=(100.8, 105.2), z_proposed=(102.0, 102.0), sheet="T1", pages=1):
    """A project whose first sheet's analysis is two planes of spot grades (x = 20 and x = 580)."""
    pdf = os.path.join(STORE, f"{pid}.pdf")
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=600, height=600)
    doc.save(pdf)
    doc.close()
    p = Project.create(pid, pid=pid)
    p.add_document(pdf, name=f"{pid}.pdf", copy=False)
    key = "d0p0"
    p.page_settings(key)["role"] = "both"
    p.data["settings"].update({"scale_ft_per_in": 72.0, "scale_source": "typed", "cell_ft": 5.0, "display_page": key})
    spots = []
    for y in range(40, 561, 40):
        for x, ze, zp in ((20.0, z_existing[0], z_proposed[0]), (580.0, z_existing[1], z_proposed[1])):
            spots.append({"id": len(spots), "label_id": None, "x": x, "y": float(y), "z": ze, "side": "existing", "tag": "EG", "anchor": "dot"})
            spots.append({"id": len(spots), "label_id": None, "x": x, "y": float(y), "z": zp, "side": "proposed", "tag": "FG", "anchor": "dot"})
    an = {"engine": "test", "key": key, "doc": 0, "page": 0, "width": 600.0, "height": 600.0, "sheet": sheet, "page_role": "both",
          "lines_from": "engine", "styles": [], "families": [], "gaps": [], "labels": [], "chains": [], "contours": [], "spots": spots,
          "counts": {}, "notes": [], "scale_candidates": [], "scale_default": None}
    os.makedirs(os.path.dirname(p.analysis_path(key)), exist_ok=True)
    json.dump(an, open(p.analysis_path(key), "w"))
    p.save()
    return p, key, an


# ---- 2. the limit in force, measured by the server (the Limit card shows this) ----------------------
p, key, an = planes_project("limit5a")
p.data["settings"]["region"] = [[100, 100], [500, 500], [500, 100], [100, 500]]      # a bow-tie: crosses itself
p.data["settings"]["limit"] = {"source": "drawn"}
p.save()
tp = takeoff_preview(p)
lm = tp.get("limit") or {}
check(lm.get("repaired") is True and abs(lm.get("area_sf", 0) - 80000.0) < 1 and abs(lm.get("perim_ft", 0) - (800 + 800 * 2 ** 0.5)) < 0.2
      and lm.get("n") == 4,
      f"the rows' limit carries the repaired area (80,000 SF, two triangles; the shoelace says 0), the perimeter and the flag: {lm}")
cg = next((r for r in tp["rows"] if r["label"] == "Clear & Grub"), None)
check(cg is not None and cg["qty"] == lm["area_sf"] and "repaired" in cg["basis"], "Clear & Grub stands on the same area and says so")

# ---- 3. "Where the volume sits" counts each cell by its share inside the limit, like the totals ----
p3, key3, _ = planes_project("hot5a", z_existing=(100.0, 100.0), z_proposed=(110.0, 110.0))     # 10 ft of fill all over
p3.data["settings"]["region"] = [[300, 100], [500, 300], [300, 500], [100, 300]]                  # a diamond: its edge cuts the cells
p3.data["settings"]["limit"] = {"source": "drawn"}
p3.save()
r3 = compute_cutfill(p3)
t3 = r3.get("totals") or {}
fills = [h for h in r3.get("hotspots") or [] if h["kind"] == "fill"]
check(not r3.get("error") and len(fills) == 1 and abs(t3["area_sf"] - 80000) < 1,
      f"a 10 ft fill over an 80,000 SF diamond: one fill area ({r3.get('error')}, {len(fills)} areas, {t3.get('area_sf')} SF)")
h = fills[0] if fills else {"volume_cy": 0, "area_sf": 0, "mean_depth_ft": 0, "cells": 0}
check(abs(h["volume_cy"] - t3.get("fill_cy", -1)) <= 0.2 and h["area_sf"] <= t3.get("area_sf", 0) + 1 and abs(h["mean_depth_ft"] - 10.0) < 0.01,
      f"the area holds the total fill on the limit's area, 10 ft deep - not more: {h['volume_cy']} CY on {h['area_sf']} SF "
      f"vs {t3.get('fill_cy')} CY on {t3.get('area_sf')} SF")
total = (t3.get("cut_cy") or 0) + (t3.get("fill_cy") or 0)
check(total and round(100 * h["volume_cy"] / total) <= 100, f"its share of the total is at most 100 % ({100 * h['volume_cy'] / max(total, 1):.2f} %)")
whole = h["cells"] * 5.0 * 5.0 * 10.0 / 27.0
check(whole > 1.01 * t3.get("fill_cy", 0), f"counted whole, the same {h['cells']} cells held {whole:,.0f} CY: more than the limit's {t3.get('fill_cy')} (what the card said)")

# ---- 5. a scale in a sentence: two decimals at most, the host's hand-off notes too -------------------
import server  # noqa: E402  (TAKEOFF_JOBS_DIR points at the scratch store)

got = server._cutfill_scale_handoff(20.0, "read in review", {"ft_per_paper_inch": 40.016655966932305, "set_by": "auto"})
check(got is not None and got[0] == 40.016655966932305 and '1" = 40.02 ft' in got[2] and "40.0166" not in got[2],
      f"the note of a scale handed over from a measured bar prints 40.02 (the number in force keeps every decimal): {got}")
got = server._cutfill_scale_handoff(20.016655966932305, "typed", {"ft_per_paper_inch": 50.0, "set_by": server._BY_REVIEWER})
check(got is not None and '(1" = 20.02 ft, typed)' in got[2] and '1" = 50 ft' in got[2], f"and the one a reviewer's pick replaces: {got and got[2]}")

# ---- 6. the words: a sheet by its name and a button as it reads; never an engine key ---------------
from cutfill import core_edits as ce  # noqa: E402

p6, key6, an6 = planes_project("words5a", pages=2)
zone = ce.normalize({"chains": []}, {"work_zones": [[100, 100, 300, 500]]})
p6.put_core_edits(key6, zone)                       # something was drawn in review after the sheet was read here
r6 = compute_cutfill(p6)
err = r6.get("error") or ""
check("sheet T1" in err and "Update" in err and "Analyze" not in err and "page 1" not in err and "d0p0" not in err,
      f"the early-compute refusal names the sheet by its number and the button as it reads (Update): {err}")
p6.jobs[key6] = {"status": "running", "progress": "applying your edits"}   # the host's open started the read again
err = compute_cutfill(p6).get("error") or ""
check(err.startswith("Sheet T1 is being read again") and "compute once it is read" in err and "Update" not in err,
      f"while that read is running it says so and names no button: {err}")
p6.jobs.pop(key6, None)
an6["sheet"] = ""                                   # a sheet with no printed number: its page
json.dump(an6, open(p6.analysis_path(key6), "w"))
p6._an_cache.pop(key6, None)
err = compute_cutfill(p6).get("error") or ""
check("after page 1 was read" in err and "d0p0" not in err, f"a sheet with no printed number is named by its page: {err}")
an6["sheet"] = "T1"
an6["review"] = zone["sig"]                         # read again with the review: current
json.dump(an6, open(p6.analysis_path(key6), "w"))
p6._an_cache.pop(key6, None)
p6.page_settings("d0p1")["role"] = "both"           # a second sheet in the volumes, never read here
r6 = compute_cutfill(p6)
ws = r6.get("warnings") or []
check(not r6.get("error") and any(w.startswith("Page 2 is not read yet") and "Analyze" in w for w in ws) and not any("d0p1" in w for w in ws),
      f"a sheet in the volumes that was never read is named by its page, never by its key: {ws}")

# a zero prints as 0: the fill-only diamond has no cut cell, and a sum over none is -0.0
import math  # noqa: E402

check(math.copysign(1.0, t3["cut_cy"]) == 1.0 and json.dumps(t3["cut_cy"]) == "0.0",
      f"no cut anywhere: the total cut is 0.0, not -0.0 (it printed \"Cut -0.0 CY\"): {json.dumps(t3['cut_cy'])}")
p3.data["settings"]["zones"] = [{"id": 1, "name": "north", "polygon": [[250, 150], [350, 150], [350, 250], [250, 250]], "section_ft": 0}]
p3.save()
z3 = (compute_cutfill(p3).get("zones") or [{}])[0]
check(z3.get("cut_cy") == 0 and math.copysign(1.0, z3["cut_cy"]) == 1.0, f"and a zone's: {z3.get('cut_cy')!r}")
from PIL import ImageDraw  # noqa: E402

from cutfill.project import crew_map  # noqa: E402

said, _orig_text = [], ImageDraw.ImageDraw.text


def _spy(self, xy, text, *a, **k):
    said.append(str(text))
    return _orig_text(self, xy, text, *a, **k)


ImageDraw.ImageDraw.text = _spy
try:
    crew_map(p3, dict(r3, totals=dict(r3["totals"], cut_cy=-0.0, net_cy=-0.4)), block_ft=50.0)   # a result stored before
finally:
    ImageDraw.ImageDraw.text = _orig_text
head = " ".join(s for s in said if "CY" in s)
check("cut 0 CY" in head and "-0" not in head, f"the crew map's header prints a stored -0.0 as 0: {head}")
from cutfill.project import _review_erased_notes  # noqa: E402

one = _review_erased_notes(1, 12.4, 1)
two = _review_erased_notes(2, 40.0, 3)
check(one[0].startswith("1 piece of linework (12 pt) lies on") and one[1].startswith("1 piece erased in review carries a height")
      and " lie on " in two[0] and "3 pieces erased in review carry a height" in two[1],
      f"one piece lies / carries, several lie / carry: {one} {two}")
check(_review_erased_notes(0, 0.0, 0) == [], "nothing erased: nothing said")

# the named-in-review note says, per group of pens, what each read before this read; a pen whose
# surface moved is never unsaid (check 5A: "2 pens are existing here, named in review ... (read from
# the sheet alone: proposed)" when one had read ignore, and w0.7 went ignore -> proposed unsaid)
from cutfill.elevations import Family  # noqa: E402
from cutfill.project import _review_pen_notes  # noqa: E402

NAMED = "named in review: “EXISTING CONTOUR”"


def pen(style, width, role, why, gray=0.0, dashed=False):
    return Family(style=style, role=role, role_reason=why, styles=[style], color=(gray, gray, gray), gray=gray, width=width, dashed=dashed)


def before_of(fams):
    return {"review": "old", "families": [f.to_json() for f in fams]}


was = [pen(14, 1.9, "proposed", "solid dark linework"), pen(15, 1.1, "ignore", "only 2 contour label(s) read on it"),
       pen(3, 0.7, "ignore", "only 1 contour label(s) read on it"), pen(2, 0.5, "existing", "screened (gray) linework", gray=0.6, dashed=True),
       pen(1, 0.0, "ignore", "only 0 contour label(s) read on it")]
now = [pen(14, 1.9, "existing", NAMED), pen(15, 1.1, "existing", NAMED), pen(3, 0.7, "proposed", "solid dark linework"),
       pen(2, 0.5, "existing", "screened (gray) linework", gray=0.6, dashed=True), pen(1, 0.0, "ignore", "only 0 contour label(s) read on it"),
       Family(style=-1, role="proposed", role_reason="drawn / edited by you", styles=[-1], forced=True, width=1.0)]
notes5, moved5 = _review_pen_notes(now, before_of(was), True)
check(len(notes5) == 2 and moved5 == 2
      and notes5[0] == (f"2 pens are existing here, {NAMED} (before this read the gray 0.0 · solid · w1.9 pen read proposed; "
                        f"the gray 0.0 · solid · w1.1 pen read ignore): change them in the Families card if the name is wrong"),
      f"the check's read: each named pen says what IT read before (w1.9 proposed, w1.1 ignore): {notes5[:1]}")
check(len(notes5) == 2 and notes5[1] == ("1 pen not named in review changed surface with this read (the gray 0.0 · solid · w0.7 pen read "
                                         "ignore before, proposed now): check it in the Families card"),
      f"and the pen that moved without a name of its own is said: {notes5[1:]}")
check(not any("w0.5" in n or "w0 " in n or "drawn" in n for n in notes5), "a pen that kept its surface, and the lines drawn by the operator, are not said")
n_same, m_same = _review_pen_notes(now, dict(before_of(now), review="new"), False)
check(n_same == [] and m_same == 0, f"the next read with the same names: nothing moved, nothing said ({n_same})")
n_first, m_first = _review_pen_notes(now, None, False)
check(len(n_first) == 1 and m_first == 0 and "(the pens gray 0.0 · solid · w1.9; gray 0.0 · solid · w1.1)" in n_first[0]
      and "before" not in n_first[0] and "sheet alone" not in n_first[0],
      f"a first read names its named pens and claims no before: {n_first}")
back = [pen(14, 1.9, "proposed", "set by you"), pen(15, 1.1, "ignore", "only 2 contour label(s) read on it"),
        pen(3, 0.7, "ignore", "only 1 contour label(s) read on it")]
n_back, m_back = _review_pen_notes(back, before_of(now), True)
check(len(n_back) == 1 and m_back == 0 and n_back[0].startswith("2 pens not named in review changed surface with this read (")
      and "the gray 0.0 · solid · w1.1 pen read existing before, ignore now" in n_back[0]
      and "the gray 0.0 · solid · w0.7 pen read proposed before, ignore now" in n_back[0] and "w1.9" not in n_back[0],
      f"the name removed: the pens that went back are said; the one the operator set himself is his own word: {n_back}")
n_new, _ = _review_pen_notes([pen(16, 0.3, "existing", NAMED, dashed=True)] + now[:1], before_of(was), True)
check(any("the gray 0.0 · dashed · w0.3 pen read ignore" in n for n in n_new), f"a pen that was no contour pen before read ignore: {n_new}")
other_code = {"review": "old", "families": [dict(f.to_json(), sig="x|" + str(i)) for i, f in enumerate(was)]}
n_oc, m_oc = _review_pen_notes(now, other_code, True)
check(len(n_oc) == 1 and "before" not in n_oc[0] and m_oc == 0, f"a previous analysis whose pens cannot be named here is no before: {n_oc}")
src = open(os.path.join(ROOT, "cutfill", "project.py"), encoding="utf-8").read()
q_at, n_at = src.find("qualify_families(families, contours, labels=labels)"), src.find("_review_pen_notes(families, prev, review_changed)")
check(0 < q_at < n_at and "interpret(project, key, doc, info, rd, ps, prog, prev=prev)" in src,
      "the notes are made once the pens' surfaces are final (after qualify_families), with the previous analysis")
# and through interpret itself: a synthetic reading, two pens with no label (both set aside), the
# review naming the dark one "EXISTING CONTOUR"
import numpy as np  # noqa: E402

from cutfill.chains import Chain, StyleGroup  # noqa: E402
from cutfill.geom import poly_length  # noqa: E402
from cutfill.project import interpret  # noqa: E402


def wavy(y, x0=100.0, x1=900.0, n=81, amp=6.0):
    xs = np.linspace(x0, x1, n)
    return np.array([[x, y + amp * np.sin(x / 60.0)] for x in xs])


def chain_of(cid, style, P):
    return Chain(id=cid, style=style, pts=P, members=[], length=poly_length(P), ink=poly_length(P), closed=False, candidate=True)


pi_, _, _ = planes_project("interp5a")
pi_.page_settings("d0p0")["core_result"] = None
rd = {"strokes": [], "glyph_ids": set(), "gaps": [], "labels": [], "texts": [], "timing": {}, "n_text_numbers": 0, "n_clusters": 0,
      "ocr_used": False,
      "styles": [StyleGroup(id=0, color=(0, 0, 0), gray=0.0, width=1.9, layer="", dashed=False, n_candidates=4, cand_length=3200.0),
                 StyleGroup(id=1, color=(0.6, 0.6, 0.6), gray=0.6, width=0.5, layer="", dashed=True, n_candidates=4, cand_length=3200.0)],
      "chains": [chain_of(i, 0, wavy(100 + 60 * i)) for i in range(4)] + [chain_of(10 + i, 1, wavy(350 + 60 * i)) for i in range(4)]}
pi_.put_core_edits("d0p0", ce.normalize({"chains": [{"chain_id": 100 + i, "style_id": 5, "points": wavy(100 + 60 * i).tolist()} for i in range(4)]},
                                        {"labels": {"5": "EXISTING CONTOUR"}}))
ps_ = pi_.snapshot_page_settings("d0p0")
doc_ = pi_.doc(0)
said_ = lambda r: [n for n in r["notes"] if " pen" in n]  # noqa: E731
first = interpret(pi_, "d0p0", doc_, doc_.page_info(0), rd, ps_)
fams = {f["style"]: (f["role"], f["role_reason"]) for f in first["families"]}
check(fams.get(0, ("",))[0] == "existing" and fams.get(1, ("",))[0] == "ignore" and len(said_(first)) == 1 and "before" not in said_(first)[0],
      f"interpret, a first read with the name: the named pen is said, no before claimed: {said_(first)}")
alone = {"review": "", "families": [dict(f, role="ignore", role_reason="only 0 contour label(s) read on it") for f in first["families"]]}
trans = interpret(pi_, "d0p0", doc_, doc_.page_info(0), rd, ps_, prev=alone)
check(said_(trans) == ["1 pen is existing here, named in review: “EXISTING CONTOUR” (before this read the gray 0.0 · solid · w1.9 pen "
                       "read ignore): change it in the Families card if the name is wrong"] and trans["review_applied"].get("moved") == 1,
      f"the read that brings the name: what the pen read before (ignore, not the first guess): {said_(trans)}")
steady = interpret(pi_, "d0p0", doc_, doc_.page_info(0), rd, ps_, prev=trans)
check(said_(steady) == [] and steady["review_applied"].get("moved") == 0, f"the next read with the same name: nothing moved, nothing said: {said_(steady)}")

# a refused compute: the rows the server gives (the Takeoff rows card's source) carry nothing made
# from the last result - the card drops those rows at once too (tests/test_cutfill_check5a.js)
from cutfill import server as cfs  # noqa: E402

p7, key7, _ = planes_project("refuse5a")
p7.data["settings"]["region"] = [[100, 100], [500, 100], [500, 500], [100, 500]]
p7.data["settings"]["limit"] = {"source": "drawn"}
p7.save()
cfs._projects["refuse5a"] = p7
cfs.PROJECT_OK = None                                 # the host (imported above) lists only its jobs' projects
VOL = ("Site Cut", "Site Fill", "Export Cut", "Import Structural", "Site walls face")
ok7 = cfs.compute("refuse5a")
tp7 = takeoff_preview(p7)
check(not ok7.get("error") and tp7["computed"] and any(x["label"] in VOL for x in tp7["rows"]), "a compute that stands: its volume rows are in the rows")
p7.put_core_edits(key7, zone)                         # the review changed after the read: the next compute is refused
bad7 = cfs.compute("refuse5a")
tp7 = takeoff_preview(p7)
check(bool(bad7.get("error")) and not tp7["computed"] and not any(x["label"] in VOL for x in tp7["rows"])
      and any(x["label"] == "Clear & Grub" for x in tp7["rows"]),
      f"refused: no row made from a result stands, the limit's rows do ({[x['label'] for x in tp7['rows']]})")


print("\n%d failure(s)" % fails)
sys.exit(1 if fails else 0)
