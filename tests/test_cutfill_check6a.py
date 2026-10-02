"""CHECK 6A ON THE CUT / FILL SCREENS (2026-09-27, VERIFY-6A.md): the server side of the fixes.

* D3 - What changed printed the raw pen signature ("0.00,0.00,0.00|w0.4|s|"): each of its family rows
  carries the pen in the Families card's words now (`pen`), from this reading's record of the pen (a
  pen gone since, from the previous reading's); the operator's own lines keep their name; the
  signature stays in the row for support.

    python tests/test_cutfill_check6a.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
STORE = tempfile.mkdtemp(prefix="cf6a_")
os.environ["TAKEOFF_JOBS_DIR"] = STORE
os.environ["CUTFILL_OUT"] = os.path.join(STORE, "cutfill")
os.environ["CUTFILL_UI_DIR"] = str(ROOT / "webui" / "cutfill")

import pymupdf  # noqa: E402

from cutfill.elevations import Family  # noqa: E402
from cutfill.project import Project, _changes_since, _pen_words  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


def empty_project(pid):
    pdf = os.path.join(STORE, f"{pid}.pdf")
    doc = pymupdf.open()
    doc.new_page(width=600, height=600)
    doc.save(pdf)
    doc.close()
    p = Project.create(pid, pid=pid)
    p.add_document(pdf, name=f"{pid}.pdf", copy=False)
    p.save()
    return p, "d0p0"


SIG_19 = "0.00,0.00,0.00|w1.9|s|"
SIG_04 = "0.00,0.00,0.00|w0.4|s|"
SIG_07 = "0.00,0.00,0.00|w0.7|s|"
SIG_03 = "0.50,0.50,0.50|w0.3|d|C-TOPO-EXIST-MINOR-LINES"


def fam(style, sig, role, width, gray=0.0, dashed=False, layer="", words=True):
    f = {"style": style, "sig": sig, "styles": [style], "role": role, "role_reason": "test"}
    if words:
        f.update({"gray": gray, "width": width, "dashed": dashed, "layer": layer, "color": [gray] * 3})
    return f


def style(sid, sig, width, gray=0.0, dashed=False, layer=""):
    return {"id": sid, "sig": sig, "color": [gray] * 3, "gray": gray, "width": width, "dashed": dashed, "layer": layer}


def contour(cid, sty, z, length=500.0):
    return {"id": cid, "style": sty, "chain_ids": [cid], "z": z, "how": "label" if z is not None else "", "length": length}


def reading(families, styles, contours, cached=True):
    return {"families": families, "styles": styles, "contours": contours, "labels": [], "spots": [], "timing": {"cached": cached},
            "engine": "t", "reader": "r1", "interp": "i1"}


p, key = empty_project("changes6a")
prev = reading(
    [fam(1, SIG_19, "proposed", 1.9), fam(3, SIG_03, "existing", 0.3, gray=0.5, dashed=True, layer="C-TOPO-EXIST-MINOR-LINES"),
     fam(7, SIG_07, "ignore", 0.7)],
    [style(1, SIG_19, 1.9), style(3, SIG_03, 0.3, 0.5, True, "C-TOPO-EXIST-MINOR-LINES"), style(7, SIG_07, 0.7), style(4, SIG_04, 0.4)],
    [contour(10, 1, 5990.0), contour(30, 3, 5989.0)])
res = reading(
    # w1.9 moved to existing (named in review); w0.4 is new, and its family record carries no words (a
    # reading from before families did): its words come from its style record; w0.7 moved ignore -> proposed;
    # the gray dashed pen is gone; the operator's switched lines are their own family
    [fam(1, SIG_19, "existing", 1.9), fam(4, SIG_04, "proposed", 0.4, words=False), fam(7, SIG_07, "proposed", 0.7),
     {"style": -4, "styles": [-4], "role": "proposed", "role_reason": "switched to proposed by you", "sig": SIG_19}],
    [style(1, SIG_19, 1.9), style(4, SIG_04, 0.4), style(7, SIG_07, 0.7)],
    [contour(10, 1, 5990.0), contour(40, 4, 5991.0), contour(70, 7, 5992.0), contour(90, -4, 5993.0)])
ch = _changes_since(prev, res, p, key)
rows = {r["sig"]: r for r in ch["families"]}
print(json.dumps([{k: r[k] for k in ("sig", "pen", "new", "gone", "role_changed")} for r in ch["families"]], ensure_ascii=False))
check(rows[SIG_19]["pen"] == "gray 0.0 · solid · w1.9" and rows[SIG_19]["role_changed"],
      f"a pen that moved surface carries its words: {rows[SIG_19]['pen']!r} (the row said only {SIG_19!r})")
check(rows[SIG_04]["pen"] == "gray 0.0 · solid · w0.4" and rows[SIG_04]["new"],
      f"a new pen whose family record says nothing of its pen: the words from its style record: {rows[SIG_04]['pen']!r}")
check(rows[SIG_03]["pen"] == "gray 0.5 · dashed · w0.3 · layer C-TOPO-EXIST-MI…" and rows[SIG_03]["gone"],
      f"a pen gone since the last read: the words from the previous reading's record: {rows[SIG_03]['pen']!r}")
check(rows[SIG_07]["pen"] == "gray 0.0 · solid · w0.7", "a pen that went from ignore to proposed: its words")
own = [r for r in ch["families"] if r["sig"].startswith("switched to proposed by you")]
check(len(own) == 1 and own[0]["pen"] is None, f"the operator's own lines keep their name (no pen words): {own and own[0]['sig']!r}")
check(all(r["sig"] for r in ch["families"]), "every row keeps its signature (the page's tooltip, for support)")
# the notes' words (interpret: "a dark pen (...) carries screened heights ...") are unchanged, from a family
f = Family(style=2, role="existing", role_reason="", gray=0.0, width=0.7, dashed=False, layer="C-TOPO-PROP-MAJOR-CONTOURS")
check(_pen_words(f) == "gray 0.0 · solid · w0.7 · layer C-TOPO-PROP-MAJ…" and _pen_words(f) == _pen_words({"gray": 0.0, "width": 0.7, "dashed": False,
      "layer": "C-TOPO-PROP-MAJOR-CONTOURS"}), f"_pen_words names a family and a stored record alike: {_pen_words(f)!r}")

print("\n" + (f"{fails} failure(s)" if fails else "all ok"))
sys.exit(1 if fails else 0)
