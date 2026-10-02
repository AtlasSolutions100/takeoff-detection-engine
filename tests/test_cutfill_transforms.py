"""THE OVERLAY'S PLACEMENT WITHOUT A COMPUTE (2026-09-28, Koby: "when we overlay ... also include that sheet's
topos"). The Overlay places another sheet - its image and now its contour lines - on the sheet shown. It used
the last compute's transforms only, so right after two matching points were picked it drew the other sheet
unmoved until a compute. GET /api/projects/{pid}/transforms answers the same page_transform the compute uses.

The critic's round (out/verify/2026-09-28/overlay-lines/CRITIC-ghost.md) moved the code to cutfill/placement.py
(M1: project.py is hashed into the reading stamps, so editing it read every sheet of every project again), which
repeats surfaces_for's display rule: section 3 runs the two side by side, so they cannot part silently.

    python tests/test_cutfill_transforms.py
"""
import inspect
import os
import random
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
STORE = tempfile.mkdtemp(prefix="cftr_")
os.environ["TAKEOFF_JOBS_DIR"] = STORE
os.environ["CUTFILL_OUT"] = os.path.join(STORE, "cutfill")
os.environ["CUTFILL_UI_DIR"] = str(ROOT / "webui" / "cutfill")

import pymupdf  # noqa: E402

from cutfill import placement  # noqa: E402
from cutfill import project as cfp  # noqa: E402
from cutfill import server as cfs  # noqa: E402
from cutfill.align import from_pairs  # noqa: E402
from cutfill.placement import display_for, transforms_for  # noqa: E402
from cutfill.project import Project  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


def disp(p):
    return display_for(p.data["settings"], p.data["pages"])


# ---- 1. the placement, before and after two matching points -------------------------------------------------
pdf = os.path.join(STORE, "two.pdf")
doc = pymupdf.open()
doc.new_page(width=600, height=600)
doc.new_page(width=600, height=600)
doc.save(pdf)
doc.close()
p = Project.create("two", pid="two")
p.add_document(pdf, name="two.pdf", copy=False)
p.page_settings("d0p0")["role"] = "both"
p.page_settings("d0p1")["role"] = "both"
p.data["settings"]["display_page"] = "d0p0"
p.save()

# no matching points and no reading: the other sheet gets no entry (the Overlay draws it unmoved), the sheet
# shown is itself
t0 = transforms_for(p)
check(t0["display"] == "d0p0" and t0["transforms"].get("d0p0", {}).get("source") == "self" and "d0p1" not in t0["transforms"],
      f"before any matching points: the sheet shown is itself, the other has no placement yet ({t0})")

# two matching points on sheet 2 (the Rose pair: a pure slide of +32.01 / +1095.97)
pairs = [{"src": [575.9, 477.7], "dst": [607.9, 1573.7]}, {"src": [1416.5, 477.7], "dst": [1448.5, 1573.7]}]
p.page_settings("d0p1")["align"] = {"pairs": pairs}
p.save()
t1 = transforms_for(p)
M = t1["transforms"]["d0p1"]["matrix"]
want = from_pairs([q["src"] for q in pairs], [q["dst"] for q in pairs])
check(t1["transforms"]["d0p1"]["source"] == "typed" and all(abs(M[i][j] - want[i][j]) < 1e-9 for i in range(2) for j in range(3))
      and abs(M[0][2] - 32.0) < 0.05 and abs(M[1][2] - 1096.0) < 0.05,
      f"after two matching points the placement is theirs, before any compute: shift {M[0][2]:.2f}, {M[1][2]:.2f}")

# the route answers the same, and it is JSON (no numpy in it)
cfs._projects["two"] = p
r = cfs.transforms("two")
check(r == t1 and isinstance(r["transforms"]["d0p1"]["matrix"][0][0], float), "the route answers transforms_for as plain JSON")

# a page added while the placements are worked out does not break the loop (critic M2: the pages were iterated
# live, so a concurrent add raised and the Overlay silently kept the old placement)
_real = placement.page_transform


def _adding(project, key, display):
    project.data["pages"].setdefault("d9p9", {"role": "none"})
    return _real(project, key, display)


placement.page_transform = _adding
try:
    t2 = transforms_for(p)
    check(t2["transforms"]["d0p1"]["source"] == "typed", "a page added meanwhile leaves the placements whole")
except RuntimeError as e:
    check(False, f"a page added meanwhile broke the loop: {e}")
finally:
    placement.page_transform = _real
    p.data["pages"].pop("d9p9", None)

# ---- 2. the sheet shown takes no part (role none): the first sheet that does is shown, as in the compute ------
p.page_settings("d0p0")["role"] = "none"
p.save()
check(disp(p) == "d0p1" and transforms_for(p)["display"] == "d0p1",
      f"a sheet shown with role none gives way to the first sheet that takes part: {disp(p)}")
p.page_settings("d0p1")["role"] = "none"
p.save()
check(disp(p) is None and transforms_for(p) == {"display": None, "transforms": {}},
      "no sheet takes part: no sheet shown, no placements")
cfs._projects.pop("two", None)

# ---- 3. the Overlay's display rule IS the compute's: surfaces_for's own lines, run side by side ------------------
# The rule is written twice (placement.py cannot be imported by project.py's stamped source without moving the
# stamps). The lines are taken from surfaces_for's source as it is now and run on 20,000 random page sets.
src = textwrap.dedent(inspect.getsource(cfp.surfaces_for))
lines = src.splitlines()
try:
    a = next(i for i, ln in enumerate(lines) if ln.strip().startswith('s = project.data["settings"]'))
    b = next(i for i, ln in enumerate(lines) if ln.strip() == "display = next(iter(active), None)")
    rule = textwrap.dedent("\n".join(lines[a:b + 1]))
except StopIteration:
    rule = None
check(rule is not None and "display_page" in (rule or ""),
      "surfaces_for's display rule is found in its source (if not, it changed: change placement.display_for with it)")
if rule is not None:
    rng = random.Random(7)
    ROLES = ["none", "existing", "proposed", "both", "grading", "weird", None]
    diff = 0
    for _ in range(20000):
        n = rng.randint(0, 5)
        pages = {}
        for i in range(n):
            rl = rng.choice(ROLES)
            pages[f"d0p{i}"] = {} if rl is None else {"role": rl}
        settings = {}
        dp = rng.choice(["absent", None, "", "d0p0", "d0p1", "d0p4", "d0p9"])
        if dp != "absent":
            settings["display_page"] = dp

        class _P:
            data = {"settings": settings, "pages": pages}
        ns = {"project": _P}
        exec(rule, ns)             # one namespace: the rule's comprehensions see its names
        if ns["display"] != display_for(settings, pages):
            diff += 1
    check(diff == 0, f"20,000 random page sets: placement.display_for and surfaces_for's rule differ in {diff}")

# ---- 4. the stamps: nothing in placement.py is hashed into a reading ---------------------------------------------
stamp_src = inspect.getsource(cfp._reader_source_stamp) + inspect.getsource(cfp._interp_source_stamp)
check("placement.py" not in stamp_src and cfp.page_transform is placement.page_transform,
      "placement.py is in neither reading stamp, and the compute's page_transform is placement's (a placement fix reads no sheet again)")

# ---- 5. the critic's R1: matching points remember the sheet they were picked against, and are turned around -------
pdf3 = os.path.join(STORE, "three.pdf")
doc = pymupdf.open()
for _ in range(3):
    doc.new_page(width=600, height=600)
doc.save(pdf3)
doc.close()
q = Project.create("three", pid="three")
q.add_document(pdf3, name="three.pdf", copy=False)
for k in ("d0p0", "d0p1", "d0p2"):
    q.page_settings(k)["role"] = "both"
ROSE = [{"src": [575.9, 477.7], "dst": [607.9, 1573.7]}, {"src": [1416.5, 477.7], "dst": [1448.5, 1573.7]}]
SLIDE = [{"src": [100.0, 100.0], "dst": [150.0, 90.0]}, {"src": [500.0, 100.0], "dst": [550.0, 90.0]}]
q.page_settings("d0p1")["align"] = {"pairs": ROSE, "dst": "d0p0"}     # 3.1 picked against 3.0
q.page_settings("d0p2")["align"] = {"pairs": SLIDE, "dst": "d0p1"}    # a third sheet picked against 3.1
q.save()


def tr(p, key, display):
    t = placement.page_transform(p, key, display)
    return t["source"], round(t["matrix"][0][2], 2), round(t["matrix"][1][2], 2), t.get("note", "")


s0, x0, y0, n0 = tr(q, "d0p1", "d0p0")
s1, x1, y1, n1 = tr(q, "d0p0", "d0p1")
check((s0, x0, y0) == ("typed", 32.0, 1096.0) and not n0 and (s1, x1, y1) == ("typed", -32.0, -1096.0) and "turned around" in n1,
      f"3.1's points place it on 3.0 as picked, and 3.0 on 3.1 turned around (it sat unmoved there): {x1}, {y1} ({n1})")
s2, x2, y2, n2 = tr(q, "d0p2", "d0p0")
s3, x3, y3, n3 = tr(q, "d0p0", "d0p2")
check((s2, x2, y2) == ("typed", 82.0, 1086.0) and (s3, x3, y3) == ("typed", -82.0, -1086.0) and "then" in n2 and "then" in n3,
      f"a sheet picked against another is placed through it, either way: {x2}, {y2} ({n2}) / {x3}, {y3}")
# pairs saved before they named their sheet: the only other sheet taking part
q.page_settings("d0p2")["role"] = "none"
q.page_settings("d0p1")["align"] = {"pairs": ROSE}
q.save()
s4, x4, y4, _ = tr(q, "d0p0", "d0p1")
check((s4, x4, y4) == ("typed", -32.0, -1096.0), f"old pairs with no sheet named: picked against the only other sheet: {x4}, {y4}")
tq = transforms_for(q)
check(tq["display"] == "d0p0" and "d0p1" in tq["transforms"],
      "a sheet placed only through another sheet's points is listed for the Overlay")

# ---- 6. neighbours along a match line: the same words at the same place prove nothing (the legend) --------------
def lab(t, x, y):
    return {"text": t, "bbox": [x, y, x + 40, y + 10], "value": None}


legend = [lab(w, 2100, 600 + 30 * i) for i, w in enumerate(["BW731.69", "C.B.", "R.O.W.", "TW731.69", "V.N.A.E."])]
far = [lab("CORNERSTONE", 50, 30), lab("SOUTHWEST", 2500, 1700)]           # anchors for the label extent (not in the band test's way)


class _Fake:
    def __init__(self, ana):
        self.ana = ana
        self.data = {"pages": {"a": {"role": "both"}, "b": {"role": "both"}}, "settings": {"display_page": "b"}}

    def page_settings(self, k):
        return self.data["pages"][k]

    def load_analysis(self, k):
        return self.ana[k]


def ana(extra):
    return {"width": 2592, "height": 1728, "labels": far + legend + extra}


with_ml = _Fake({"a": ana([lab("MATCH LINE A", 900, 1500)]), "b": ana([lab("MATCH LINE A", 900, 200)])})
no_ml = _Fake({"a": ana([]), "b": ana([])})
r_ml, r_no = placement.page_transform(with_ml, "a", "b"), placement.page_transform(no_ml, "a", "b")
check(r_ml["source"] == "unaligned" and "match line" in r_ml["note"] and r_no["source"] == "identity",
      f"sheets that meet at a match line are not 'the same frame' on their shared legend words ({r_ml['source']}); "
      f"without a match line the same words still mean one frame ({r_no['source']})")
# the critic 6's WRONG-2: the legend a few points apart on one sheet fit as "text" and placed the neighbour on that shift
shifted = {}
for dx in (3.0, 10.0, 150.0):
    moved = [lab(l["text"], l["bbox"][0] + dx, l["bbox"][1] + dx / 2) for l in legend]
    fk = _Fake({"a": {"width": 2592, "height": 1728, "labels": far + moved + [lab("MATCH LINE A", 900, 1500)]},
                "b": ana([lab("MATCH LINE A", 900, 200)])})
    shifted[dx] = placement.page_transform(fk, "a", "b")["source"]
    fk_no = _Fake({"a": {"width": 2592, "height": 1728, "labels": far + moved}, "b": ana([])})
    shifted[f"{dx} no match line"] = placement.page_transform(fk_no, "a", "b")["source"]
check(all(v == "unaligned" for k, v in shifted.items() if "no match line" not in str(k))
      and all(v != "unaligned" for k, v in shifted.items() if "no match line" in str(k)),
      f"at a match line no fit on the shared words places a sheet, shifted or not (it placed the neighbour on the legend's "
      f"shift); without one the words still fit: {shifted}")

# ---- 7. the compute leaves an unaligned sheet out and says so (it stacked it unmoved) -------------------------------
sf = inspect.getsource(cfp.surfaces_for)
check('tr.get("source") == "unaligned"' in sf and "it is left out. Pick two matching points" in sf
      and 'tr.get("source") == "unaligned"' in inspect.getsource(cfp.grade_at),
      "the compute and the ground-under-points lookup leave a sheet that cannot be placed out, and say so")

# ---- 8. the fifth critic's WRONG-2: at a match line, roles never prove one frame (they stacked neighbours again) -----
comp = _Fake({"a": ana([lab("MATCH LINE A", 900, 1500)]), "b": ana([lab("MATCH LINE A", 900, 200)])})
roles8 = {}
for ra in ("none", "existing", "proposed", "both", "grading"):
    for rb in ("none", "existing", "proposed", "both", "grading"):
        comp.data["pages"]["a"]["role"], comp.data["pages"]["b"]["role"] = ra, rb
        roles8[(ra, rb)] = placement.page_transform(comp, "a", "b")["source"]
check(set(roles8.values()) == {"unaligned"} and "do not show where they sit" in placement.page_transform(comp, "a", "b")["note"],
      f"neighbours at a match line are left out (and said) for all 25 role pairs - an existing sheet beside a grading "
      f"sheet too (the role rule stacked them): {sorted(set(roles8.values()))}")

# ---- 9. the fourth critic's r2: old pairs go to the sheet the ENGINE shows, not the settings' page that takes no part --
q.page_settings("d0p2")["role"] = "both"                  # three sheets take part again
q.page_settings("d0p1")["align"] = {"pairs": ROSE}          # old pairs (no sheet named), picked against 3.0
q.data["pages"]["d0p3"] = {"role": "none"}                  # the settings name a page that takes no part
q.data["settings"]["display_page"] = "d0p3"
s9, x9, y9, _ = tr(q, "d0p1", "d0p0")
check((s9, x9, y9) == ("typed", 32.0, 1096.0),
      f"old pairs with three sheets taking part go to the sheet shown (they went to the role-none page, and were lost): {s9} {x9} {y9}")
del q.data["pages"]["d0p3"]
q.data["settings"]["display_page"] = "d0p0"

# ---- 10. the fourth critic's r3: bad matching points on one sheet break no other placement -----------------------------
BAD = [{"src": [100.0, 100.0], "dst": [150.0, 90.0]}, {"src": [100.0, 100.0], "dst": [550.0, 90.0]}]   # one point twice
q.data["pages"]["d0p3"] = {"role": "none", "align": {"pairs": BAD, "dst": "d0p0"}}
try:
    s10, x10, y10, _ = tr(q, "d0p1", "d0p0")
    t10 = transforms_for(q)
    ok10 = (s10, x10, y10) == ("typed", 32.0, 1096.0) and "d0p1" in t10["transforms"]
except Exception as e:  # noqa: BLE001 - the failure this checks for
    ok10, s10 = False, f"raised {type(e).__name__}: {e}"
check(ok10, f"a role-none sheet's bad pairs no longer stop the other sheets' placement (it raised for all): {s10}")
q.page_settings("d0p1")["align"] = {"pairs": BAD, "dst": "d0p0"}
t10b = placement.page_transform(q, "d0p1", "d0p0")
check(t10b["source"] != "typed" and "cannot place it" in (t10b.get("note") or ""),
      f"a sheet's own bad pairs are said, and the words decide meanwhile: {t10b['source']} ({t10b.get('note')})")
del q.data["pages"]["d0p3"]
q.page_settings("d0p1")["align"] = {"pairs": ROSE, "dst": "d0p0"}

# ---- 10b. the fourth critic's m4: points picked on both sheets, each against the other, that disagree are said ---------
REV_OK = [{"src": [607.9, 1573.7], "dst": [575.9, 477.7]}, {"src": [1448.5, 1573.7], "dst": [1416.5, 477.7]}]
REV_BAD = [{"src": [607.9, 1573.7], "dst": [555.9, 477.7]}, {"src": [1448.5, 1573.7], "dst": [1396.5, 477.7]}]
q.page_settings("d0p0")["align"] = {"pairs": REV_OK, "dst": "d0p1"}
ok_note = placement.page_transform(q, "d0p1", "d0p0").get("note") or ""
q.page_settings("d0p0")["align"] = {"pairs": REV_BAD, "dst": "d0p1"}
bad_note = placement.page_transform(q, "d0p1", "d0p0").get("note") or ""
check("disagree" not in ok_note and "disagree by 20 pt" in bad_note,
      f"two sets of points that agree say nothing; two that part by 20 pt are said: '{bad_note}'")
# honest clicks: the same landmarks picked again with a couple of points of error are not called a disagreement
REV_NOISY = [{"src": [609.1, 1572.2], "dst": [576.4, 479.3]}, {"src": [1447.0, 1575.0], "dst": [1417.6, 476.1]}]
q.page_settings("d0p0")["align"] = {"pairs": REV_NOISY, "dst": "d0p1"}
noisy_note = placement.page_transform(q, "d0p1", "d0p0").get("note") or ""
check("disagree" not in noisy_note,
      f"two honest sets a click's error apart are not said (measured where the points were clicked, not at the corners): '{noisy_note}'")
q.page_settings("d0p0").pop("align", None)

# ---- 11. the scale in force and the sheet shown: nothing is dropped or moved; the compute refuses a scale read on
#          another sheet while the sheet shown prints none to check it against (critic 4 W1, critic 5 WRONG-1) -------
import json  # noqa: E402


def analysis(key, printed=None):
    Path(q.analysis_path(key)).write_text(json.dumps({"labels": [], "width": 600, "height": 600,
                                                      **({"scale_default": {"ft_per_in": printed, "text": f"1\"={printed}'"}}
                                                         if printed else {})}))
    # the analysis cache keys on the file's time, and two quick rewrites here share one (critic 8, MINOR-1: 47/48 in one
    # run of six): drop it, as the probes do
    q._an_cache.pop(key, None)


s = q.data["settings"]
before = {k: s.get(k) for k in ("scale_ft_per_in", "scale_source", "scale_page")}
cfs._projects["three"] = q
# a scale read in review on 3.0; 3.0 set to none, so 3.1 shows; 3.1 prints no scale the engine could read
s.update({"display_page": "d0p0", "scale_ft_per_in": 40.0, "scale_source": "read in review", "scale_page": "d0p0"})
q.page_settings("d0p0")["role"] = "none"
analysis("d0p1")
why = placement.foreign_scale(q)
res11 = cfs.compute("three")
check(why and "prints no scale the engine could read" in why and res11.get("error") == why
      and (s["scale_ft_per_in"], s["scale_source"], s["scale_page"]) == (40.0, "read in review", "d0p0"),
      f"the compute refuses a scale read on another sheet when the sheet shown prints none to check it (it used it, "
      f"silently: 6.25x on a sheet drawn at 100), and the scale itself is left as it was: {res11.get('error')}")
# the sheet shown prints the same scale: confirmed there, it computes; another scale: refused, naming both
analysis("d0p1", 40.0)
ok_same = placement.foreign_scale(q) is None
analysis("d0p1", 100.0)
why_other = placement.foreign_scale(q) or ""
check(ok_same and 'but page 2 prints 1" = 100 ft' in why_other and s["scale_page"] == "d0p0",
      f"a sheet shown that prints the same scale computes; one that prints another is refused, both named: {why_other}")
# the sheet's own review reading, asked of the host each time (placement.REVIEW_SCALE), confirms or refuses the same way
analysis("d0p1")
LIVE = {}
placement.REVIEW_SCALE = lambda project, key: LIVE.get(key)
LIVE["d0p1"] = 40.0
ok_review = placement.foreign_scale(q) is None
LIVE["d0p1"] = 100.0
why_review = placement.foreign_scale(q) or ""
check(ok_review and why_review.startswith('Page 2\'s review says 1" = 100 ft, but the cut / fill is set to 1" = 40 ft')
      and "type the right scale in the Scale card with page 2 on screen and press Enter (it sets the review's scale too)" in why_review,
      f"the sheet's own review reading confirms the scale (no print needed), or refuses it where it disagrees - the "
      f"review wins on the sheet shown, and the words say where the right scale is typed: {why_review}")
LIVE.clear()
# the other measures on the sheet shown refuse it too (critic 6, WRONG-1: the limit's rows priced 56 % high)
analysis("d0p1")
tk11 = cfs.takeoff_route("three")
wl11 = cfs.walls_route("three", "d0p1")
lc11 = cfs.limit_candidates_route("three", "d0p1")
check(tk11["rows"] == [] and "nothing checks it there" in tk11["notes"][0] and "nothing checks it there" in wl11["note"]
      and wl11["walls"] == [] and "nothing checks it there" in lc11["scale_refused"] and lc11["scale"] == "confirmed",
      "the limit's rows, the walls and the limit offers' lengths refuse a scale not checked for the sheet shown")
pick_src = inspect.getsource(cfs.limit_pick_route)
check('res["metrics"] = None' in pick_src and "_scale_refused(p," in pick_src,
      "a limit picked on such a sheet keeps its ring and measures no area")
# a sheet shown with no reading at all is refused by the compute too: the other sheets were measured on its frame at
# their own scale (critic 7, r-4: 1,637 CY for 1,072); its review reading, when the host has one, confirms it
os.remove(q.analysis_path("d0p1"))
why_unread = placement.foreign_scale(q) or ""
res_unread = cfs.compute("three")
LIVE["d0p1"] = 40.0
ok_unread_review = placement.foreign_scale(q) is None
LIVE.clear()
check("(it is not read yet)" in why_unread and res_unread.get("error") == why_unread and ok_unread_review,
      f"an unread sheet shown: refused by the compute as by every measure, unless its review reads the same scale: "
      f"{why_unread}")
# the fifth critic's round trip: confirmed on 3.0, the engine misreads 3.0 as 300, 3.1 prints 40
s.update({"scale_ft_per_in": 40.0, "scale_source": "confirmed in review", "scale_page": "d0p0"})
q.page_settings("d0p0")["role"] = "both"
analysis("d0p0", 300.0)
analysis("d0p1", 40.0)
trip = []
for shown in ("d0p1", "d0p0", "d0p1"):
    s["display_page"] = shown
    trip.append((shown, placement.foreign_scale(q), s["scale_ft_per_in"], s["scale_page"]))
check(all(w is None and fpi == 40.0 and pg == "d0p0" for _, w, fpi, pg in trip),
      f"a scale confirmed on 3.0 survives the sheet shown going to 3.1 and back: nothing re-attributed, dropped or "
      f"computed at the misread 300 (it was, 56x): {trip}")
# a scale typed in the engine is the operator's word for the sheet on screen when it was set: there it passes whatever
# the sheet prints; with no word for the sheet shown, that sheet's own readings decide (critic 11, WRONG-1: typed on 3.1
# it measured 3.0 at 40 for its 50, 36 % low, exported)
s.update({"scale_ft_per_in": 40.0, "scale_source": "typed", "scale_page": "d0p0", "display_page": "d0p1"})
analysis("d0p1", 300.0)
LIVE.pop("d0p1", None)
w_noword = placement.foreign_scale(q) or ""
q.page_settings("d0p1").update({"operator_scale": 40.0, "operator_scale_at": 1.0})
ok_word = placement.foreign_scale(q) is None
for f_ in ("operator_scale", "operator_scale_at"):
    q.page_settings("d0p1").pop(f_, None)
check(ok_word and "is not on record as set with" in w_noword and 'prints 1" = 300 ft' in w_noword,
      f"a scale typed with a sheet on screen is the operator's word for it and passes there, whatever it prints; with no "
      f"word for the sheet shown its own readings decide (critic 11, WRONG-1): {w_noword}")
for k in ("d0p0", "d0p1"):
    os.remove(q.analysis_path(k))
s.update(before)
s["display_page"] = "d0p0"
cfs._projects.pop("three", None)
srv = (ROOT / "cutfill" / "server.py").read_text(encoding="utf-8")
check("scale_for_shown" not in srv and "_scale_follows" not in srv and "why = foreign_scale(p)\n" in srv
      and "unread_ok" not in srv,
      "no route drops or moves a scale any more; the compute route asks foreign_scale first, an unread sheet included")
ejs = (ROOT / "webui" / "cutfill" / "app.js").read_text(encoding="utf-8")
check("scaleDropped" not in ejs and "scale_note" not in ejs, "the engine page has no dropped-scale path left")
ty_src = ejs[ejs.index("async function typeScale(ev)"):ejs.index("function renderDatum()")]
check("$('scaleIn').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); typeScale(); } });" in ejs
      and "if (ev && ev.type === 'change' && state.scaleSaved === tag) return;" in ty_src
      and ty_src.count("return;") == 2 and "scale_source === 'typed'" not in ty_src,
      "Enter in the Scale box sets the scale as typed even when the box already shows that number (critic 6, r-1), and "
      "every Enter goes to the server - decided from the page's old copy, one said \"Scale set\" and sent nothing (critic "
      "17, WRONG-3); only the change right after an Enter is not sent again")
check("const lost = [gk, state.key].find((k) => xfs && xfs[k] && xfs[k].source === 'unaligned');" in ejs
      and "pairsDst(o) === k" in ejs and "function pairsDst(o)" in ejs
      and "|| shownKey(); const src = state.key;" in ejs and "const d = s && s.display_page;" in ejs
      and "if (own && own.note)" in ejs,
      "the Overlay draws no unaligned sheet (m3); Reset clears the points that place the sheet from either side (m5); "
      "points are picked against the engine's sheet shown, never the last result's (m6, m-h); the sheet shown's own "
      "bad points are named on its card (m-e)")
# m-e on the server: the sheet shown's own bad points are named in its entry
q.page_settings("d0p0")["align"] = {"pairs": BAD, "dst": "d0p1"}
t12 = transforms_for(q)
check("cannot place anything" in ((t12["transforms"].get("d0p0") or {}).get("note") or ""),
      f"the sheet shown's own bad points are named (they were not): {(t12['transforms'].get('d0p0') or {}).get('note')}")
q.page_settings("d0p0").pop("align", None)

# ---- 13. the seventh critic (CRITIC-repairs-7.md): the review asked live, every reading of a sheet agrees, a stored
#          result that stands no more, the operator's own scale per sheet ------------------------------------------------
import asyncio  # noqa: E402

from fastapi import HTTPException  # noqa: E402

pdf7 = os.path.join(STORE, "seven.pdf")
doc = pymupdf.open()
for _ in range(2):
    doc.new_page(width=600, height=600)
doc.save(pdf7)
doc.close()
r7 = Project.create("seven", pid="seven")
r7.add_document(pdf7, name="seven.pdf", copy=False)
for k in ("d0p0", "d0p1"):
    r7.page_settings(k)["role"] = "both"
r7.save()
cfs._projects["seven"] = r7
s7 = r7.data["settings"]


def an7(key, printed=None):
    """A sheet's analysis with or without a printed scale (the cache is dropped: a rewrite can keep its mtime)."""
    Path(r7.analysis_path(key)).parent.mkdir(parents=True, exist_ok=True)
    Path(r7.analysis_path(key)).write_text(json.dumps({"labels": [], "width": 600, "height": 600,
                                                       **({"scale_default": {"ft_per_in": printed, "text": f"1\"={printed}'"}}
                                                          if printed else {})}))
    r7._an_cache.pop(key, None)


class _Req:
    def __init__(self, body):
        self._b = body

    async def json(self):
        return self._b


LIVE.clear()
an7("d0p0")
an7("d0p1")
# WRONG-1: 3.1 hands 50 over, 3.0 is shown; b888800 kept a copy of 3.0's review (50) taken before the reviewer
# corrected 3.0 to 40, and the copy confirmed the 50: Compute ran, 56 % high
s7.update({"display_page": "d0p0", "scale_ft_per_in": 50.0, "scale_source": "read in review", "scale_page": "d0p1"})
r7.page_settings("d0p0")["review_scale"] = 50.0
LIVE["d0p0"] = 40.0
w1 = placement.foreign_scale(r7) or ""
c1 = cfs.compute("seven")
check('review says 1" = 40 ft' in w1 and c1.get("error") == w1,
      f"WRONG-1: the review is asked now, not a copy taken at hand-over: the corrected 40 refuses the 50 (the copy's 50 "
      f"passed it, silently): {w1}")
r7.page_settings("d0p0").pop("review_scale", None)
# r-5: every reading of the sheet must agree - the review's 50 beside a print of 40 is refused on the print (the
# compute's scale_check refused there while the limit's rows went through), and a review of 40 beside a print of 50
# on the review
LIVE["d0p0"] = 50.0
an7("d0p0", 40.0)
w5a = placement.foreign_scale(r7) or ""
LIVE["d0p0"] = 40.0
an7("d0p0", 50.0)
w5b = placement.foreign_scale(r7) or ""
LIVE["d0p0"] = 50.0
ok5 = placement.foreign_scale(r7) is None
check('prints 1" = 40 ft' in w5a and 'review says 1" = 40 ft' in w5b and ok5,
      f"r-5: one agreeing reading is not enough when another disagrees; both agreeing pass: {w5a} / {w5b}")
# the sheet the scale was handed over from: its review corrected since refuses it there too; a scale only read in
# review that its own print contradicts is refused as the compute refuses it; confirmed in review, the compute warns
an7("d0p0")
s7.update({"scale_page": "d0p0"})
LIVE["d0p0"] = 40.0
w_own = placement.foreign_scale(r7) or ""
LIVE["d0p0"] = 50.0
ok_own = placement.foreign_scale(r7) is None
an7("d0p0", 40.0)
w_own_print = placement.foreign_scale(r7) or ""
s7["scale_source"] = "confirmed in review"
ok_own_confirmed = placement.foreign_scale(r7) is None
check('review says 1" = 40 ft' in w_own and ok_own and "two readings of this sheet disagree" in w_own_print and ok_own_confirmed,
      f"on its own sheet: a correction made in review since the hand-over refuses the scale; read in review against its "
      f"own print refuses like the compute; a reviewer's pick passes: {w_own} / {w_own_print}")
# r-2: the operator's scale typed on 3.0 is kept on 3.0; the host's open from 3.1 then hands a scale confirmed in
# review over, and 3.0 is no longer refused again (every round trip cost one more Enter); a different one refuses
an7("d0p0")
LIVE.clear()
s7.update({"display_page": "d0p0", "scale_ft_per_in": 50.0, "scale_source": "confirmed in review", "scale_page": "d0p1"})
refused_before = placement.foreign_scale(r7)
asyncio.run(cfs.patch_settings("seven", _Req({"scale_ft_per_in": 50.0, "scale_source": "typed"})))
op = r7.page_settings("d0p0").get("operator_scale")
s7.update({"scale_source": "confirmed in review", "scale_page": "d0p1"})        # what the host's open from 3.1 does
ok_op = placement.foreign_scale(r7) is None
r7.page_settings("d0p0")["operator_scale"] = 40.0
w_op = placement.foreign_scale(r7) or ""
check(bool(refused_before) and op == 50.0 and ok_op and 'was set to 1" = 40 ft here' in w_op,
      f"r-2: the scale typed on a sheet is kept on it and confirms a scale handed over later; one set differently refuses: "
      f"{op} / {w_op}")
r7.page_settings("d0p0").pop("operator_scale", None)
# the project the page reads names every sheet the scale in force must not measure (critic 7, r-1: the page's own
# measures - a limit being drawn, a zone's area - used it there)
s7.update({"scale_ft_per_in": 50.0, "scale_source": "read in review", "scale_page": "d0p1", "display_page": "d0p0"})
LIVE.update({"d0p0": 40.0, "d0p1": 50.0})
sm7 = cfs.get_project_summary("seven")
check(set(sm7.get("scale_refused") or {}) == {"d0p0"} and 'review says 1" = 40 ft' in sm7["scale_refused"]["d0p0"],
      f"the project names the sheets the scale must not measure: {sm7.get('scale_refused')}")
# WRONG-2 / WRONG-3: a stored result stands only while its sheet shown and its scale do, and that scale is not refused
LIVE.clear()
s7.update({"display_page": "d0p0", "scale_ft_per_in": 50.0, "scale_source": "typed", "scale_page": "d0p1",
           "region": [[100, 100], [500, 100], [500, 500], [100, 500]], "limit": {"source": "drawn"}, "takeoff": {"strip_in": 6}})
r7.page_settings("d0p0").update({"operator_scale": 50.0, "operator_scale_at": 1.0})   # typed with 3.0 on screen, as the page does
fake = {"display_page": "d0p0", "ft_per_in": 50.0, "grid": {"nx": 1, "ny": 1}, "zones": None, "walls": None, "warnings": [],
        "totals": {"cut_cy": 0.0, "fill_cy": 900.2, "net_cy": 900.2, "area_sf": 24305.6}}
fake["inputs"] = placement.inputs_sig(r7)          # what the compute route keeps (a result without it is an older one's)
Path(r7.dir, "result.json").write_text(json.dumps(fake))
cfs._results["seven"] = fake
tk_ok = cfs.takeoff_route("seven")
lab_ok = {r["label"] for r in tk_ok["rows"]}
check({"Site Fill", "Import Structural", "Clear & Grub", "Strip Topsoil"} <= lab_ok and "result_stale" not in tk_ok
      and not cfs.result("seven").get("stale_why"),
      f"a result that stands: its rows beside the limit's: {sorted(lab_ok)}")
# a scale the volumes do not feel is not a new one (Rose's bar reads 49.99994 beside a typed 50); 0.2 % is
s7["scale_ft_per_in"] = 49.99994348603031
same = cfs.result("seven").get("stale_why")
s7["scale_ft_per_in"] = 49.9
moved = cfs.result("seven").get("stale_why") or ""
s7["scale_ft_per_in"] = 50.0
check(same is None and 'then, 49.9 ft now' in moved,
      f"a result at 50 stands under 49.99994 and is stale under 49.9: {same} / {moved}")
# WRONG-3: a host open from 3.0 itself sets 40, confirmed in review, there: the volumes at 50 stood beside the limit's
# rows at 40, "computed": true, no note
s7.update({"scale_ft_per_in": 40.0, "scale_source": "confirmed in review", "scale_page": "d0p0"})
LIVE["d0p0"] = 40.0
tk3 = cfs.takeoff_route("seven")
res3 = cfs.result("seven")
lab3 = {r["label"]: r["qty"] for r in tk3["rows"]}
clear40 = 400 * 400 * (40 / 72) ** 2
check(not set(lab3) & {"Site Fill", "Import Structural", "Site Cut", "Export Cut"} and abs(lab3.get("Clear & Grub", 0) - clear40) < 1
      and tk3.get("computed") is False and tk3["notes"][0].startswith("No cut / fill rows: the scale changed since this compute")
      and 'then, 40 ft now' in (tk3.get("result_stale") or "") and res3.get("stale_why") == tk3.get("result_stale"),
      f"WRONG-3: volumes computed at 50 are not listed beside the limit's rows at 40; the result says it is stale and why: "
      f"{sorted(lab3)} / {tk3.get('result_stale')}")
# WRONG-2: the scale read on 3.1 refused on 3.0 now (its review reads 40): no rows at all, and the stored result is
# stale with the same words (its volumes were filed as fresh and the limit's rows dropped, silently)
s7.update({"scale_ft_per_in": 50.0, "scale_source": "read in review", "scale_page": "d0p1"})
tk2 = cfs.takeoff_route("seven")
res2 = cfs.result("seven")
check(tk2["rows"] == [] and 'review says 1" = 40 ft' in (tk2.get("result_stale") or "")
      and res2.get("stale_why") == tk2["result_stale"] and res2["totals"] == fake["totals"],
      f"WRONG-2: the refused scale makes the stored result stale, said on the takeoff and on the result: {tk2.get('result_stale')}")
codes = []
for fn in (lambda: cfs.crewmap_route("seven"), lambda: cfs.export_csv("seven")):
    try:
        fn()
        codes.append(None)
    except HTTPException as e:
        codes.append(e.status_code)
check(codes == [409, 409], f"the crew map and the cells' CSV refuse a stale result (it drew its old depths): {codes}")
# the sheet shown moved: the volumes were measured on another frame
LIVE.clear()
s7.update({"scale_ft_per_in": 50.0, "scale_source": "typed", "display_page": "d0p1", "region": None, "limit": None})
res_d = cfs.result("seven")
check("the sheet shown changed since this compute (page 1 then, page 2 now)" in (res_d.get("stale_why") or ""),
      f"a result computed on another sheet shown is stale: {res_d.get('stale_why')}")
cfs._results["seven"] = {"error": "refused", "warnings": []}
check(cfs.result("seven") == {"error": "refused", "warnings": []}, "a stored refusal comes back as it is")
pick_src7 = inspect.getsource(cfs.limit_pick_route)
check("stitch_status(" in pick_src7 and '"gap": lm.get("gap_pt") or 0}, None)' in pick_src7,
      "a limit picked where the scale is refused names its gaps in points (it said 'largest 20 ft', then the refusal)")
# ---- 14. the eighth critic (CRITIC-repairs-8.md): a typed scale against a newer reviewer's pick, the operator's scale
#          outranked by one and kept on the sheet looked at, the fingerprint of a compute's inputs, the result an answer
#          judged, the sheets listed, two points that size a sheet wrong ----------------------------------------------
import time as _t  # noqa: E402

LIVE.clear()
placement.REVIEW_SCALE = lambda project, key: LIVE.get(key)
an7("d0p0")
an7("d0p1")
cfs._projects["seven"] = r7
s7.update({"display_page": "d0p0", "scale_ft_per_in": 50.0, "scale_source": "typed", "scale_page": "d0p1", "region": None,
           "limit": None, "zones": []})
for k in ("d0p0", "d0p1"):
    for f in ("operator_scale", "operator_scale_at"):
        r7.page_settings(k).pop(f, None)
# THE REVIEW WINS (Koby, 2026-09-29; it replaces "the later one wins" of critics 8-15): on the sheet shown a scale that
# disagrees with the sheet's review is refused whatever its time or source - a typing newer than the pick, a
# detection's reading, a word kept with no time - and said with where the right scale is typed. The review at the
# same number passes it
r7.page_settings("d0p0").update({"operator_scale": 50.0, "operator_scale_at": 1000.0})   # typed with 3.0 on screen
LIVE["d0p0"] = {"ft": 40.0, "reviewer": True, "at": 2000.0}
w2 = placement.foreign_scale(r7) or ""
c2 = cfs.compute("seven")
LIVE["d0p0"] = {"ft": 40.0, "reviewer": True, "at": 500.0}
w_older = placement.foreign_scale(r7) or ""
LIVE["d0p0"] = {"ft": 40.0, "reviewer": False, "at": None}
w_auto = placement.foreign_scale(r7) or ""
r7.page_settings("d0p0").pop("operator_scale_at", None)
LIVE["d0p0"] = {"ft": 40.0, "reviewer": True, "at": 2000.0}
w_legacy2 = placement.foreign_scale(r7) or ""
LIVE["d0p0"] = {"ft": 50.0, "reviewer": True, "at": 2000.0}
ok_legacy_same = placement.foreign_scale(r7) is None
r7.page_settings("d0p0").pop("operator_scale", None)
REV40 = 'Page 1\'s review says 1" = 40 ft, but the cut / fill is set to 1" = 50 ft'
check(all(w.startswith(REV40) for w in (w2, w_older, w_auto, w_legacy2)) and c2.get("error") == w2
      and "type the right scale in the Scale card with page 1 on screen and press Enter (it sets the review's scale too), or open Cut / "
          "fill again from page 1" in w2 and ok_legacy_same,
      f"the review wins on the sheet shown: a typed 50 is refused under a review of 40 whatever its time - a pick older "
      f"than the typing, a detection's reading, a word kept with no time - and the words say where to type the right "
      f"scale; the review at 50 passes it: {w2}")
# The Scale card with a review behind the sheet (placement.REVIEW_SET, the host's Approve record, faked here): typed with
# the sheet shown on screen the number is written in its review and becomes the scale in force, confirmed in review -
# the operator who finds the scale read wrong types the right one, and every number follows. Typed with another sheet on
# screen, it sets that sheet's review alone: critic 10, WRONG-1's door (an Enter on 3.1 lifted 3.0's refusal, and 900.2
# CY at 50 were exported for 576.1 at 40) stays shut
SETS = []
HOSTED = {"d0p0", "d0p1"}


def fake_set(project, key, ft, text):
    SETS.append((key, float(ft), text))
    if key not in HOSTED:
        return None
    if ft == 666.0:
        return False                                  # the review's record could not be written
    LIVE[key] = {"ft": float(ft), "reviewer": True, "at": _t.time()}
    return True


placement.REVIEW_SET = fake_set
LIVE["d0p0"] = {"ft": 40.0, "reviewer": True, "at": _t.time() - 60}
LIVE["d0p1"] = {"ft": 40.0, "reviewer": True, "at": _t.time() - 60}
s7.update({"scale_ft_per_in": 50.0, "scale_source": "typed", "scale_page": "d0p1", "display_page": "d0p0"})
s7.pop("scale_set_at", None)
asyncio.run(cfs.patch_settings("seven", _Req({"scale_ft_per_in": 50.0, "scale_source": "typed", "scale_sheet": "d0p1"})))
w_other = placement.foreign_scale(r7) or ""
other_after = (LIVE["d0p1"]["ft"], s7["scale_ft_per_in"], s7["scale_source"], s7["scale_page"], "scale_set_at" in s7,
               r7.page_settings("d0p1").get("operator_scale"))
# the operator types the right scale with the sheet shown on screen: written in its review, and the scale in force as the
# operator's own word ("typed", kept on the sheet with its time - a word typed before, 45, is replaced). Marked "confirmed
# in review" the compute's own check refused it wherever another sheet printed another scale, and no typing lifted it
r7.page_settings("d0p0").update({"operator_scale": 45.0, "operator_scale_at": _t.time()})
sm_here = asyncio.run(cfs.patch_settings("seven", _Req({"scale_ft_per_in": 40.0, "scale_source": "typed", "scale_sheet": "d0p0"})))
here_after = (LIVE["d0p0"]["ft"], s7["scale_ft_per_in"], s7["scale_source"], s7["scale_page"],
              r7.page_settings("d0p0").get("operator_scale"), bool(r7.page_settings("d0p0").get("operator_scale_at")))
ok_here = placement.foreign_scale(r7) is None
check(w_other.startswith(REV40) and other_after == (50.0, 50.0, "typed", "d0p1", False, None)
      and here_after == (40.0, 40.0, "typed", "d0p0", 40.0, True) and ok_here
      and r7.page_settings("d0p0")["operator_scale_at"] >= LIVE["d0p0"]["at"]
      and s7.get("scale_set_at", 0) > _t.time() - 60 and SETS[-1] == ("d0p0", 40.0, '1" = 40\'')
      and sm_here.get("review_scales") == {"d0p0": 40.0, "d0p1": 50.0} and not sm_here.get("scale_refused", {}).get("d0p0"),
      f"typed with another sheet on screen, the number is that sheet's review alone - the sheet shown stays refused; typed "
      f"with the sheet shown on screen it is written in its review and is the scale in force, the operator's word, and "
      f"the page is told each sheet's review scale: {w_other} / {other_after} / {here_after} / {sm_here.get('review_scales')}")
# the compute's own check (project.scale_check) takes a typed scale that another sheet's print disagrees with as a warning,
# never a refusal - the way past a misprinted or reduced sheet; handed over, it refuses
from cutfill.project import scale_check  # noqa: E402
err_typed, warn_typed = scale_check(40.0, "typed", "d0p0", [("d0p1", "page 2", 50.0, '1"=50\'')])
err_handed, _w = scale_check(40.0, "confirmed in review", "d0p0", [("d0p1", "page 2", 50.0, '1"=50\'')])
check(err_typed is None and warn_typed and "it came from another sheet" in (err_handed or ""),
      f"a typed scale another sheet's print disagrees with is warned, not refused (handed over, refused): {warn_typed}")
# a confirmed printed scale goes the same way, its printed words kept; the review's record unwritable: refused, said,
# and nothing changed. Critic 17: another sheet with no review here, or a sheet that takes no part, is refused and
# nothing is written (WRONG-4: typed on a sheet that takes no part, it rewrote that sheet's review); the sheet shown with
# no review keeps the engine's own typed scale (the operator's word), as before
asyncio.run(cfs.patch_settings("seven", _Req({"scale_ft_per_in": 40.0, "scale_source": '1"=40\'', "scale_sheet": "d0p0"})))
text_kept = SETS[-1]
before_bad = (dict(s7), dict(LIVE["d0p0"]))


def refused_patch(body):
    try:
        asyncio.run(cfs.patch_settings("seven", _Req(body)))
    except HTTPException as e:
        return e.status_code, str(e.detail)
    return None


code_bad = refused_patch({"scale_ft_per_in": 666.0, "scale_source": "typed", "scale_sheet": "d0p0"})
unchanged = (dict(s7), dict(LIVE["d0p0"])) == before_bad
HOSTED.discard("d0p1")
n_sets = len(SETS)
code_none = refused_patch({"scale_ft_per_in": 45.0, "scale_source": "typed", "scale_sheet": "d0p1"})
unchanged_none = (dict(s7), r7.page_settings("d0p1").get("operator_scale")) == (before_bad[0], None)
HOSTED.add("d0p1")
r7.page_settings("d0p1")["role"] = "none"
code_nopart = refused_patch({"scale_ft_per_in": 45.0, "scale_source": "typed", "scale_sheet": "d0p1"})
nopart_unwritten = len(SETS) == n_sets + 1 and LIVE["d0p1"]["ft"] == 50.0     # REVIEW_SET was not even asked
r7.page_settings("d0p1")["role"] = "both"
HOSTED.discard("d0p0")
asyncio.run(cfs.patch_settings("seven", _Req({"scale_ft_per_in": 40.0, "scale_source": "typed", "scale_sheet": "d0p0"})))
own_word = (r7.page_settings("d0p0").get("operator_scale"), s7["scale_source"], s7["scale_ft_per_in"])
HOSTED.add("d0p0")
check(text_kept == ("d0p0", 40.0, '1"=40\'') and code_bad and code_bad[0] == 503 and "could not be recorded in the review" in code_bad[1]
      and unchanged and code_none and code_none[0] == 409 and "is not the sheet shown and has no review on record here" in code_none[1]
      and unchanged_none and code_nopart and code_nopart[0] == 409 and "takes no part in the cut / fill" in code_nopart[1]
      and nopart_unwritten and own_word == (40.0, "typed", 40.0),
      f"a printed scale confirmed keeps its words in the review; an unwritable review record refuses the typing and changes "
      f"nothing; another sheet with no review, or one that takes no part, is refused and nothing is written; the sheet "
      f"shown with no review keeps the engine's own typed scale: {code_bad} / {code_none} / {code_nopart} / {own_word}")
# critic 17, WRONG-2: the page route refuses the host's own records of a sheet (a `core_result` sent there pointed a
# sheet at another client's job, whose review the Scale card then wrote)
codes_host = []
for bad_key in ("core_result", "core_sig", "host_seen", "operator_scale"):
    try:
        asyncio.run(cfs.patch_page("seven", "d0p1", _Req({bad_key: "C:/elsewhere/jobs/other/p0_result.json"})))
        codes_host.append(None)
    except HTTPException as e:
        codes_host.append(e.status_code)
check(codes_host == [403] * 4 and r7.page_settings("d0p1").get("core_result") is None,
      f"the page route refuses the host's own records of a sheet: {codes_host}")
placement.REVIEW_SET = None
for k in ("d0p0", "d0p1"):
    r7.page_settings(k).pop("operator_scale", None)
    r7.page_settings(k).pop("operator_scale_at", None)
# run alone (no review hook at all): the page is told no review scale
placement.REVIEW_SCALE = None
alone_rs = cfs.get_project_summary("seven").get("review_scales")
placement.REVIEW_SCALE = lambda project, key: LIVE.get(key)
check(alone_rs == {}, f"run alone, the page is told no sheet's review scale: {alone_rs}")
# r-4: an old typo of 50 on 3.0, then the reviewer's 40 on 3.0, then 40 handed over from 3.1: every live reading of 3.0
# says 40, and the old 50 no longer refuses it; an operator's scale newer than the pick still counts
s7.update({"scale_ft_per_in": 40.0, "scale_source": "read in review", "scale_page": "d0p1"})
r7.page_settings("d0p0").update({"operator_scale": 50.0, "operator_scale_at": 1000.0})
LIVE["d0p0"] = {"ft": 40.0, "reviewer": True, "at": 2000.0}
ok_r4 = placement.foreign_scale(r7) is None
r7.page_settings("d0p0")["operator_scale_at"] = 3000.0
w_r4 = placement.foreign_scale(r7) or ""
check(ok_r4 and 'was set to 1" = 50 ft here' in w_r4,
      f"r-4: the operator's scale for a sheet counts only while no newer reviewer's pick holds that sheet: {w_r4}")
# r-8: kept on the sheet the operator looked at (the page names it), with its time; a name that is no sheet falls back
# to the sheet shown; MINOR-6: cleared with the scale
for k in ("d0p0", "d0p1"):
    for f in ("operator_scale", "operator_scale_at"):
        r7.page_settings(k).pop(f, None)
asyncio.run(cfs.patch_settings("seven", _Req({"scale_ft_per_in": 50.0, "scale_source": "typed", "scale_sheet": "d0p1"})))
on1 = (r7.page_settings("d0p1").get("operator_scale"), bool(r7.page_settings("d0p1").get("operator_scale_at")),
       r7.page_settings("d0p0").get("operator_scale"))
asyncio.run(cfs.patch_settings("seven", _Req({"scale_ft_per_in": 50.0, "scale_source": "typed", "scale_sheet": "nope"})))
on_disp = r7.page_settings("d0p0").get("operator_scale")
asyncio.run(cfs.patch_settings("seven", _Req({"scale_ft_per_in": None})))
cleared = [r7.page_settings(k).get(f) for k in ("d0p0", "d0p1") for f in ("operator_scale", "operator_scale_at")]
check(on1 == (50.0, True, None) and on_disp == 50.0 and cleared == [None] * 4 and "scale_sheet" not in s7
      and "scale_set_at" not in s7,
      f"r-8: the operator's scale is kept on the sheet looked at, with its time; MINOR-6: it goes with a cleared scale, "
      f"on every sheet it was kept on, with the time it was set (critic 9, m1): {on1}, {on_disp}, {cleared}")
# r-7: the fingerprint of what a compute reads: each change says what changed; bookkeeping changes nothing; a result
# kept before the fingerprint stands
LIVE.clear()
s7.update({"scale_ft_per_in": 50.0, "scale_source": "typed", "display_page": "d0p0",
           "region": [[100, 100], [500, 100], [500, 500], [100, 500]], "zones": []})
r7.page_settings("d0p0").update({"operator_scale": 50.0, "operator_scale_at": 1.0})   # typed with 3.0 on screen, as the page does
base = {"display_page": "d0p0", "ft_per_in": 50.0, "warnings": [], "computed_at": 111.0,
        "totals": {"cut_cy": 0.0, "fill_cy": 900.2, "net_cy": 900.2, "area_sf": 24305.6}}
res_in = dict(base, inputs=placement.inputs_sig(r7))
w_fresh = placement.stale_result(r7, res_in)
s7["region"] = [[100, 100], [300, 100], [300, 300], [100, 300]]
w_lim = placement.stale_result(r7, res_in) or ""
w_legacy = placement.stale_result(r7, base)
s7["region"] = [[100, 100], [500, 100], [500, 500], [100, 500]]
s7["zones"] = [{"id": 1, "name": "z", "polygon": [[0, 0], [10, 0], [10, 10]], "page": "d0p0"}]
w_z = placement.stale_result(r7, res_in) or ""
s7["zones"] = []
r7.page_settings("d0p1")["role"] = "none"
w_left = placement.stale_result(r7, res_in) or ""
r7.page_settings("d0p1")["role"] = "both"
r7.page_settings("d0p0")["typed_heights"] = {"7": 101.5}
w_rec = placement.stale_result(r7, res_in) or ""
r7.page_settings("d0p0").pop("typed_heights")
st1 = os.stat(r7.analysis_path("d0p1"))
os.utime(r7.analysis_path("d0p1"), ns=(st1.st_atime_ns, st1.st_mtime_ns + 5_000_000))
w_read = placement.stale_result(r7, res_in) or ""
res_in2 = dict(base, inputs=placement.inputs_sig(r7))
r7.page_settings("d0p0")["host_seen"] = True
r7.note("d0p0", "a note")
r7.page_settings("d0p0")["core_sig"] = "3:4"
r7.page_settings("d0p0")["core_result"] = "C:/elsewhere/jobs/abc/p0_result.json"     # a copied or moved store (r-2)
w_book = placement.stale_result(r7, res_in2)
for f in ("host_seen", "core_sig", "core_result"):
    r7.page_settings("d0p0").pop(f, None)
check(w_fresh is None and "the limit changed since this compute" in w_lim
      and w_legacy == "the result is from an older version of this tool"
      and "the grading zones changed" in w_z and "page 2 left the cut / fill" in w_left
      and "page 1's typed heights or label fixes changed" in w_rec and "page 2 was read again" in w_read
      and w_book is None,
      f"r-7: a change the page made (limit, zones, a sheet left, a typed height, a new reading) makes the result stale, "
      f"said by name (critic 9, m6); bookkeeping and the detection's path do not (r-2); a result kept without the record "
      f"is an older version's (r-1): {w_lim} / {w_rec} / {w_read} / {w_legacy}")
srv14 = (ROOT / "cutfill" / "server.py").read_text(encoding="utf-8")
check("sig = None if why else inputs_sig(p)" in srv14 and 'res["inputs"] = sig' in srv14,
      "the compute route keeps the fingerprint, taken before it runs, with every result it computes")
# r-3: the takeoff answer names the result it judged
cfs._results["seven"] = dict(res_in2)
check(cfs.takeoff_route("seven").get("result_at") == 111.0, "the takeoff answer names the result it judged (critic 8, r-3)")
# MINOR-4: only sheets that take part or have been read are listed
s7.update({"scale_ft_per_in": 50.0, "scale_source": "read in review", "scale_page": "d0p0"})
LIVE["d0p1"] = 40.0
listed = set(placement.scale_refusals(r7))
r7.page_settings("d0p1")["role"] = "none"
os.remove(r7.analysis_path("d0p1"))
r7._an_cache.pop("d0p1", None)
unlisted = set(placement.scale_refusals(r7))
r7.page_settings("d0p1")["role"] = "both"
an7("d0p1")
LIVE.clear()
check("d0p1" in listed and "d0p1" not in unlisted,
      f"MINOR-4: a sheet that takes no part and was never read is not listed: {sorted(listed)} / {sorted(unlisted)}")
# r-6: two points that size a sheet 1.25x while both sheets read 1" = 50 ft: not placed, said; sheets drawn at 50 and
# 40, the same points place it (1.25x is right there)
r7.page_settings("d0p1")["align"] = {"pairs": [{"src": [100.0, 100.0], "dst": [125.0, 125.0]},
                                               {"src": [500.0, 100.0], "dst": [625.0, 125.0]}], "dst": "d0p0"}
an7("d0p0", 50.0)
an7("d0p1", 50.0)
t_same = placement.page_transform(r7, "d0p1", "d0p0")
an7("d0p0", 40.0)
t_diff = placement.page_transform(r7, "d0p1", "d0p0")
an7("d0p0")
an7("d0p1")
t_unknown = placement.page_transform(r7, "d0p1", "d0p0")
r7.page_settings("d0p1").pop("align", None)
check(t_same.get("source") == "unaligned" and "size it 1.250x" in (t_same.get("note") or "") and "(1.000x)" in (t_same.get("note") or "")
      and t_diff.get("source") == "typed" and t_unknown.get("source") == "typed",
      f"r-6: points that size a sheet off the two sheets' own scales leave it out, said (1,637 CY for 1,072); sheets at "
      f"50 and 40 are placed by the same points; unknown scales place it as before: {t_same.get('note')}")
# ---- 15. the ninth critic (CRITIC-repairs-9.md): another sheet's scale in the record, the record's form, each record
#          named, the size check's allowance for honest clicks, the takeoff answer on a refused compute ---------------
LIVE.clear()
an7("d0p0", 50.0)
an7("d0p1")
for k in ("d0p0", "d0p1"):
    for f in ("operator_scale", "operator_scale_at", "align", "pads", "core_edits"):
        r7.page_settings(k).pop(f, None)
s7.update({"display_page": "d0p0", "scale_ft_per_in": 50.0, "scale_source": "typed", "scale_page": None,
           "region": [[100, 100], [500, 100], [500, 500], [100, 500]], "zones": []})
s7.pop("scale_set_at", None)
# WRONG-1: 3.1 reads 50 in review at the compute, then the reviewer corrects it to 40: placement leaves 3.1 out now, and
# the stored 900.2 CY stood everywhere and the approve exported it. The same scale said once more changes nothing
LIVE["d0p1"] = {"ft": 50.0, "reviewer": True, "at": 100.0}
res9 = dict(base, inputs=placement.inputs_sig(r7))
w9_fresh = placement.stale_result(r7, res9)
LIVE["d0p1"] = {"ft": 40.0, "reviewer": True, "at": 200.0}
w9_other = placement.stale_result(r7, res9) or ""
LIVE["d0p1"] = {"ft": 50.0, "reviewer": True, "at": 100.0}
r7.page_settings("d0p1").update({"operator_scale": 50.0, "operator_scale_at": 300.0})
w9_same = placement.stale_result(r7, res9)
r7.page_settings("d0p1").pop("operator_scale")
r7.page_settings("d0p1").pop("operator_scale_at")
# the reviewer types the bar's 49.99994 over as 50 (Rose 3.1, the live check): the volumes cannot tell, the result stands
LIVE["d0p1"] = {"ft": 49.99994348603031, "reviewer": False, "at": None}
res9h = dict(base, inputs=placement.inputs_sig(r7))
LIVE["d0p1"] = {"ft": 50.0, "reviewer": True, "at": 400.0}
w9_hair = placement.stale_result(r7, res9h)
LIVE["d0p1"] = {"ft": 50.0, "reviewer": True, "at": 100.0}
check(w9_fresh is None and "page 2's scale readings changed since this compute" in w9_other and w9_same is None
      and w9_hair is None,
      f"WRONG-1: a correction of another sheet's scale in review makes the result stale, said (the approve exported "
      f"900.2 CY the engine could not compute any more); the same scale said once more, or 49.99994 typed over as 50, "
      f"does not: {w9_other}")
# m6: each record is named; m7: the review's decisions handed over in another order are the same decisions; m9: the
# points of a sheet that takes no part (a placement chains through it) count
r7.page_settings("d0p0")["core_edits"] = {"erased": [1, 2], "names": {}}
res9b = dict(base, inputs=placement.inputs_sig(r7))
r7.page_settings("d0p0")["core_edits"] = {"erased": [2, 1], "names": {}}
w9_order = placement.stale_result(r7, res9b)
r7.page_settings("d0p0")["core_edits"] = {"erased": [2, 1, 3], "names": {}}
w9_dec = placement.stale_result(r7, res9b) or ""
r7.page_settings("d0p0")["core_edits"] = {"erased": [1, 2], "names": {}}
r7.page_settings("d0p0")["pads"] = {"p1": {"ffe": 101.0}}
w9_pad = placement.stale_result(r7, res9b) or ""
r7.page_settings("d0p0").pop("pads")
r7.data["pages"]["d0p9"] = {"role": "none", "align": None}
res9c = dict(base, inputs=placement.inputs_sig(r7))
r7.data["pages"]["d0p9"]["align"] = {"pairs": [{"src": [0, 0], "dst": [10, 10]}, {"src": [100, 0], "dst": [110, 10]}], "dst": "d0p0"}
w9_none = placement.stale_result(r7, res9c) or ""
r7.data["pages"].pop("d0p9")
r7.page_settings("d0p0").pop("core_edits")
check(w9_order is None and "page 1's review decisions changed" in w9_dec and "page 1's building pads changed" in w9_pad
      and w9_none == "a sheet's matching points changed since this compute",
      f"m6 / m7 / m9: each record named, the same decisions in another order the same, a no-part sheet's points counted: "
      f"{w9_dec} / {w9_pad} / {w9_none}")
# r-1: a record kept in an older form is an older version's too
sig9 = placement.inputs_sig(r7)
old_form = dict(base, inputs={k: v for k, v in sig9.items() if k != "v"})
check(sig9.get("v") == placement.SIG_V and placement.stale_result(r7, old_form) == "the result is from an older version of this tool"
      and placement.stale_result(r7, dict(base, inputs=sig9)) is None,
      "r-1: a result whose record is in an older form (or has none) does not stand; the record of now does")
# r-4: two points 100 pt apart, the second clicked 4 pt off on one sheet (both sheets at 50): sized 1.04x, placed (a flat
# 2 % left out 40 % of honest fits); the same points on another landmark (1.25x) are left out, and the words offer both
# causes - the points, or a sheet's scale reading
an7("d0p0", 50.0)
an7("d0p1", 50.0)
r7.page_settings("d0p1")["align"] = {"pairs": [{"src": [100.0, 100.0], "dst": [100.0, 100.0]},
                                               {"src": [200.0, 100.0], "dst": [204.0, 100.0]}], "dst": "d0p0"}
t_honest = placement.page_transform(r7, "d0p1", "d0p0")
tol100 = placement._size_tolerance(r7, [("d0p1", "d0p0", "as picked")])
r7.page_settings("d0p1")["align"]["pairs"][1]["dst"] = [225.0, 100.0]
t_wrong = placement.page_transform(r7, "d0p1", "d0p0")
r7.page_settings("d0p1")["align"]["pairs"][1]["dst"] = [100.5, 100.0]
t_close = placement._size_tolerance(r7, [("d0p1", "d0p0", "as picked")])
r7.page_settings("d0p1").pop("align", None)
check(t_honest.get("source") == "typed" and abs(tol100 - (0.04 + 4 / 104)) < 1e-9 and t_wrong.get("source") == "unaligned"
      and "size it 1.250x" in (t_wrong.get("note") or "") and "or check the two sheets' scales" in (t_wrong.get("note") or "")
      and t_close >= 1.0,
      f"r-4: the size check allows for honest clicks (1.04x on points 100 pt apart: placed, allowance {tol100:.4f}); "
      f"1.25x is left out and the words name both causes; points at one place check nothing: {t_wrong.get('note')}")
# r-5: the takeoff answer says when the job's last compute was refused (a page holding a good result takes it in)
cfs._results["seven"] = {"error": "refused", "warnings": []}
tk_ref = cfs.takeoff_route("seven")
cfs._results["seven"] = dict(res_in2)
tk_good = cfs.takeoff_route("seven")
check(tk_ref.get("result_refused") is True and tk_ref.get("result_at") is None and tk_good.get("result_refused") is False
      and tk_good.get("result_at") == 111.0,
      f"r-5: the takeoff answer says the last compute was refused: {tk_ref.get('result_refused')} / {tk_good.get('result_refused')}")
# ---- 16. the eleventh critic (CRITIC-repairs-11.md): with no pick on the sheet shown, a scale typed on another sheet
#          measured it at that sheet's scale (3.0 at 40 for its 50, 36 % low, exported); a refusal on a sheet that is
#          not the sheet shown asked for a typing, which changes the one scale - the sheet shown's -----------------------
LIVE.clear()
for k in ("d0p0", "d0p1"):
    for f in ("operator_scale", "operator_scale_at", "align"):
        r7.page_settings(k).pop(f, None)
an7("d0p0")
an7("d0p1")
LIVE["d0p0"] = 50.0                                                   # 3.0: the detection reads 50, no pick
LIVE["d0p1"] = {"ft": 40.0, "reviewer": True, "at": 100.0}            # 3.1: the reviewer set 40
s7.update({"display_page": "d0p0", "scale_ft_per_in": 40.0, "scale_source": "typed", "scale_page": "d0p0", "region": None,
           "limit": None, "zones": []})
r7.page_settings("d0p1").update({"operator_scale": 40.0, "operator_scale_at": _t.time()})   # typed 40 with 3.1 on screen
w11_shown = placement.foreign_scale(r7) or ""
ok11_other = placement.foreign_scale(r7, "d0p1") is None
c11 = cfs.compute("seven")
# the other way round: 50 typed with 3.0 on screen; 3.1 reads 40 - refused there, with no typing advice
r7.page_settings("d0p1").pop("operator_scale", None)
r7.page_settings("d0p1").pop("operator_scale_at", None)
s7["scale_ft_per_in"] = 50.0
r7.page_settings("d0p0").update({"operator_scale": 50.0, "operator_scale_at": _t.time()})
ok11_shown = placement.foreign_scale(r7) is None
w11_other = placement.foreign_scale(r7, "d0p1") or ""
check(w11_shown.startswith('Page 1\'s review says 1" = 50 ft, but the cut / fill is set to 1" = 40 ft')
      and c11.get("error") == w11_shown and ok11_other and ok11_shown
      and 'nothing on page 2 is measured with it' in w11_other and "make page 2 the sheet shown" in w11_other
      and "type its scale" not in w11_other,
      f"critic 11, WRONG-1: a scale typed with another sheet on screen does not measure the sheet shown when its own "
      f"readings disagree (Compute refused); a refusal on a sheet that is not the sheet shown gives no typing advice: "
      f"{w11_shown} / {w11_other}")
for k in ("d0p0", "d0p1"):
    r7.page_settings(k).pop("operator_scale", None)
    r7.page_settings(k).pop("operator_scale_at", None)
# critic 12, WRONG-1: a one-page project's typed scale with no word kept is the page's word of unknown time - older than
# any reviewer's pick (an exemption let a correction to 40 pass under a typed 50, exported 56 % high)
p1only = r7.data["pages"].pop("d0p1")
LIVE.clear()
s7.update({"display_page": "d0p0", "scale_ft_per_in": 50.0, "scale_source": "typed", "scale_page": "d0p0"})
an7("d0p0")
ok12_nopick = placement.foreign_scale(r7) is None
LIVE["d0p0"] = {"ft": 40.0, "reviewer": True, "at": 100.0}
w12_pick = placement.foreign_scale(r7) or ""
LIVE["d0p0"] = 40.0                                     # a detection reading: the review wins over the typing too
w12_reading = placement.foreign_scale(r7) or ""
r7.data["pages"]["d0p1"] = p1only
check(ok12_nopick and 'review says 1" = 40 ft' in w12_pick and w12_reading.startswith(REV40),
      f"critic 12, WRONG-1: one page - a typed scale with no word kept stands while no review reads the sheet; a review "
      f"that disagrees refuses it, a reviewer's pick or the detection's reading (the review wins): {w12_pick} / {w12_reading}")
# critic 17, RISK-2: a sheet placed as the same frame (or by shared words) sits at 1:1 (or the words' fit); when the two
# sheets' own scales say another size it is left out and said, as two matching points are - 3.1's review set to 40 from
# the Scale card with 3.0 shown at 50 was placed as the same frame and measured at 50, silently. Scales that agree, or
# one unknown: placed as before
LIVE.clear()
an7("d0p0")
an7("d0p1")
for k in ("d0p0", "d0p1"):
    r7.page_settings(k).pop("align", None)
LIVE.update({"d0p0": 50.0, "d0p1": 40.0})
t_off = placement.page_transform(r7, "d0p1", "d0p0")
LIVE["d0p1"] = 50.0
t_same = placement.page_transform(r7, "d0p1", "d0p0")
LIVE.pop("d0p1")
t_unknown = placement.page_transform(r7, "d0p1", "d0p0")
check(t_off.get("source") == "unaligned" and "placed as the same frame at 1.000x" in (t_off.get("note") or "")
      and 'reads 1" = 40 ft' in (t_off.get("note") or "") and "(0.800x)" in (t_off.get("note") or "")
      and t_same.get("source") == "identity" and t_unknown.get("source") == "identity",
      f"critic 17, RISK-2: a sheet placed as the same frame whose scale says another size is left out, said; the same "
      f"scale, or one unknown, places it: {t_off.get('note')}")
# critic 18, WRONG-1: the same with both sheets printing 1" = 50' - the review's 40 on 3.1 corrects its print. Sized by
# "readings that agree" 3.1 had no size (review 40, print 50), and it went in at 1:1, silently; the review wins, so its
# size is the review's. Without a review, the print and the operator's word decide as before
an7("d0p0", 50.0)
an7("d0p1", 50.0)
LIVE.update({"d0p0": 50.0, "d0p1": 40.0})
sc_rev = placement.sheet_scale(r7, "d0p1")
t_print = placement.page_transform(r7, "d0p1", "d0p0")
LIVE.clear()
sc_print = placement.sheet_scale(r7, "d0p1")
t_print_same = placement.page_transform(r7, "d0p1", "d0p0")
an7("d0p0")
an7("d0p1")
check(sc_rev == 40.0 and sc_print == 50.0 and t_print.get("source") == "unaligned" and "(0.800x)" in (t_print.get("note") or "")
      and t_print_same.get("source") == "identity",
      f"critic 18, WRONG-1: a sheet whose review corrects its print is sized by its review, and one placed as the same "
      f"frame at the other size is left out, said: {sc_rev} / {sc_print} / {t_print.get('note')}")
# critic 19, RISK-3: the sheet shown's own scale reaches the volumes as the scale in force alone (checked apart), so a
# change in its "scale readings" alone does not make a stored result stale (the new meaning of a sheet's scale made
# every such result stale once, falsely); another sheet's still does
LIVE.clear()
s7.update({"display_page": "d0p0", "scale_ft_per_in": 50.0, "scale_source": "typed", "scale_page": "d0p0",
           "region": [[100, 100], [500, 100], [500, 500], [100, 500]], "zones": []})
for k in ("d0p0", "d0p1"):
    r7.page_settings(k).pop("align", None)
r7.page_settings("d0p0").update({"operator_scale": 50.0, "operator_scale_at": 1.0})
res19 = dict(base, inputs=placement.inputs_sig(r7))
res19["inputs"]["sheets"]["d0p0"]["records"]["scale readings"] = None           # as an older record held it
w19_two = placement.stale_result(r7, res19) or ""                              # 3.1 takes part too: it counts
r7.page_settings("d0p1")["role"] = "none"
res19a = dict(base, inputs=placement.inputs_sig(r7))
res19a["inputs"]["sheets"]["d0p0"]["records"]["scale readings"] = None
w19_shown = placement.stale_result(r7, res19a)                                  # the sheet shown alone: it does not
r7.page_settings("d0p1")["role"] = "both"
res19b = dict(base, inputs=placement.inputs_sig(r7))
res19b["inputs"]["sheets"]["d0p1"]["records"]["scale readings"] = 40.0
w19_other = placement.stale_result(r7, res19b) or ""
r7.page_settings("d0p0").pop("operator_scale", None)
r7.page_settings("d0p0").pop("operator_scale_at", None)
# critic 19, WRONG-1: a review this project may read but not set (another owner's set, linked here): the refusal names
# the number to type, and offers no step that is refused; critic 20, RISK-2: its print is named when it disagrees
LIVE["d0p0"] = {"ft": 40.0, "reviewer": True, "at": 1.0, "writable": False}
w19_ro = placement.foreign_scale(r7) or ""
an7("d0p0", 50.0)
w20_ro_print = placement.foreign_scale(r7) or ""
an7("d0p0")
LIVE.clear()
check(w19_shown is None and "page 1's scale readings changed" in w19_two and "page 2's scale readings changed" in w19_other
      and "type 40 in the Scale card with page 1 on screen" in w19_ro and "another owner's plan set" in w19_ro
      and "it sets the review's scale too" not in w19_ro and "Its print says" not in w19_ro
      and 'Its print says 1" = 50 ft: if the review is wrong, it is fixed there.' in w20_ro_print,
      f"critic 19, RISK-3 / critic 20, WRONG-2: the sheet shown's scale readings make no result stale while it is the only "
      f"sheet taking part, and do when another takes part (they size its placement); a read-only review's refusal names "
      f"the number to type, and its print when it disagrees: {w19_shown} / {w19_two} / {w20_ro_print}")
LIVE.clear()
cfs._projects.pop("seven", None)
cfs._results.pop("seven", None)
placement.REVIEW_SCALE = None

print("\n" + (f"{fails} failure(s)" if fails else "all ok"))
sys.exit(1 if fails else 0)
