"""THE CIVIL SET THAT HUNG THE SERVER (2026-09-26; audit D01, D34).

Barn civil C4.3 prints "CF3.0 ... TOF: 7715'" in a wall-grade table. The
footing-schedule reader, scanning pages that never said FOOTING SCHEDULE,
took the 7,715 ft top-of-footing elevation for a footing width; the civil
set then "printed a schedule", the twin pass ran on every civil sheet with
a neighbour radius wider than the sheet (O(n^2)), and the server reached
7-17 GB. Four fences, each pinned here:

  1. the schedule reader bounds its rows (1-20 ft wide) and lists what it
     refused; the document reader reads only pages that say FOOTING
     SCHEDULE, and a cache written before the range check is not served;
  2. the twin pass never runs on a civil sheet: by the printed sheet number
     (C = civil) or by the page's words (triage "core"); an S sheet runs;
  3. a total-work cap per page answers "too big to check" instead of
     working (and an old pairing cache is not served under the new key);
  4. twin_lines counts neighbour pairs before listing them.

    python tests/test_protect_civil.py     (fails on the commit before it)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = tempfile.mkdtemp(prefix="pcv_")
os.environ["TAKEOFF_JOBS_DIR"] = JOBS
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="pcv_lg_")

import pymupdf  # noqa: E402
import server  # noqa: E402
from takeoff_engine import twin_lines as tw  # noqa: E402
from takeoff_engine.footing_callouts import read_footing_schedule, schedule_row_problem  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


# ---- 1. the schedule reader: a 7,715 ft "width" is a misread, never a row ----------------
def word(x, y, t, w=30, h=8):
    return (x, y, x + w, y + h, t)


table = [word(60, 100, "CF3.0"), word(120, 100, "7715'"), word(170, 100, "1'-0\""),
         word(60, 114, "CF2.0"), word(120, 114, "2'-0\""), word(170, 114, "1'-0\""),
         word(60, 128, "F5.0"), word(120, 128, "5'-0\""), word(170, 128, "5'-0\""), word(220, 128, "1'-0\"")]
s = read_footing_schedule(table)
check(set(s["rows"]) == {"CF2.0", "F5.0"}, f"the sane rows are read ({sorted(s['rows'])})")
check(len(s["rejected"]) == 1 and s["rejected"][0]["mark"] == "CF3.0"
      and s["rejected"][0]["width_ft"] == 7715.0 and "outside 1-20 ft" in s["rejected"][0]["why"],
      f"the 7,715 ft row is refused and says why ({s['rejected']})")
check(schedule_row_problem({"width_ft": 0.5}) and schedule_row_problem({"width_ft": 20.5})
      and schedule_row_problem({"width_ft": 3.0, "depth_ft": 40.0})
      and schedule_row_problem({"width_ft": 3.0, "length_ft": 3.0, "depth_ft": 1.0}) is None,
      "the range: 1-20 ft wide, a thickness under 10 ft, a pad under 40 ft")

# ---- the document reader: only pages that SAY footing schedule ---------------------------
FPP = 0.1


def pt(ft):
    return ft / FPP


def make_pdf(path, pages):
    """pages: list of (lines, sheet_words) - text lines placed down the page."""
    doc = pymupdf.open()
    for lines in pages:
        pg = doc.new_page(width=612, height=792)
        for (x, y, t) in lines:
            pg.insert_text((x, y), t, fontsize=7)
    doc.save(str(path))
    doc.close()


def table_lines(x0, y0, rows):
    out = []
    for i, row in enumerate(rows):
        x = x0
        for t in row:
            out.append((x, y0 + 14 * i, t)); x += 50
    return out


sched_rows = (("CF2.0", "2'-0\"", "1'-0\"", "(2)#5"), ("CF3.0", "3'-0\"", "1'-0\"", "(3)#5"))
pdf_a = Path(JOBS) / "no-heading.pdf"
make_pdf(pdf_a, [table_lines(60, 100, sched_rows) + [(60, 300, "TOF: 7715'")],
                 table_lines(60, 100, sched_rows) + [(60, 60, "FOOTING SCHEDULE")]])
da = server._job_dir("pcv_doc")
da.mkdir(parents=True, exist_ok=True)
server.JOBS["pcv_doc"] = {"path": str(pdf_a), "pages": 2, "status": {}, "owner": None}
out = server._doc_footing_schedule("pcv_doc", str(pdf_a))
check(out["pages"] == [1] and out["scanned"] == 1 and set(out["rows"]) == {"CF2.0", "CF3.0"},
      f"only the page that says FOOTING SCHEDULE is read (pages {out['pages']}, scanned {out['scanned']})")
check(all(r.get("page") == 1 for r in out["rows"].values()), "every row names the schedule page")
check((da / "footing_schedule.json").exists() and json.loads((da / "footing_schedule.json").read_text()).get("v") == server._FOOTING_SCHEDULE_V,
      "the cache carries the reader's version stamp")

pdf_b = Path(JOBS) / "table-no-words.pdf"
make_pdf(pdf_b, [table_lines(60, 100, sched_rows)])
db = server._job_dir("pcv_nowords")
db.mkdir(parents=True, exist_ok=True)
server.JOBS["pcv_nowords"] = {"path": str(pdf_b), "pages": 1, "status": {}, "owner": None}
out_b = server._doc_footing_schedule("pcv_nowords", str(pdf_b))
check(out_b["rows"] == {} and out_b["scanned"] == 0,
      "a set whose table is not headed FOOTING SCHEDULE has no schedule read - nothing else is scanned")

# a cache from before the range check, holding the misread row
(db / "footing_schedule.json").write_text(json.dumps(
    {"rows": {"CF3.0": {"mark": "CF3.0", "kind": "continuous", "width_ft": 7715.0, "page": 0}}, "pages": [0], "scanned": 13}))
out_c = server._doc_footing_schedule("pcv_nowords", str(pdf_b))
check(out_c["rows"] == {} and "CF3.0" not in out_c["rows"],
      "an old cache with a 7,715 ft row is recomputed, not served")
(db / "footing_schedule.json").write_text(json.dumps(
    {"v": server._FOOTING_SCHEDULE_V, "rows": {"CF3.0": {"mark": "CF3.0", "kind": "continuous", "width_ft": 7715.0, "page": 0}},
     "pages": [0], "scanned": 1, "rejected": []}))
out_d = server._doc_footing_schedule("pcv_nowords", str(pdf_b))
check(out_d["rows"] == {}, "even a stamped cache is refused when a row fails the range")


# ---- 2. + 3. the twin pass on a civil sheet, and the page cap --------------------------------
def rect(x0, y0, x1, y1):
    return [[pt(x0), pt(y0)], [pt(x1), pt(y0)], [pt(x1), pt(y1)], [pt(x0), pt(y1)], [pt(x0), pt(y0)]]


def plen(p):
    return sum(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in zip(p[:-1], p[1:]))


def make_job(jid, page_text, sheet_no=None, extra_chains=(), widths=(2.0,)):
    pdf = Path(JOBS) / f"{jid}.pdf"
    make_pdf(pdf, [[(72, 72 + 12 * i, t) for i, t in enumerate(page_text)]])
    d = server._job_dir(jid)
    d.mkdir(parents=True, exist_ok=True)
    server.JOBS[jid] = {"path": str(pdf), "pages": 1, "status": {}, "owner": None}

    def sh(poly):
        return [[x, y + 60] for x, y in poly]
    polys = {1: (5, "dash1", sh(rect(-1, -1, 41, 31))), 2: (5, "dash1", sh(rect(1, 1, 39, 29))),
             3: (6, "solid", sh(rect(0, 0, 40, 30))), 4: (6, "solid", sh(rect(0.75, 0.75, 39.25, 29.25)))}
    chains = [{"chain_id": cid, "class": cls, "style_id": sid, "length_pt": plen(p), "points": p,
               "color": [0, 0, 0], "width": 0.5}
              for cid, (sid, cls, p) in polys.items()] + list(extra_chains)
    styles = [{"style_id": 5, "class": "dash1", "suggested_label": "footing", "length_pt": 0, "n": 2, "color": [0, 0, 0]},
              {"style_id": 6, "class": "solid", "suggested_label": "wall", "length_pt": 0, "n": 2, "color": [0, 0, 0]}]
    res = {"page": {"width_pt": 612, "height_pt": 792}, "scale": {"ft_per_paper_inch": FPP * 72, "text": "test"},
           "styles": styles, "chains": chains, "engine_sig": "t", "sheet_no": sheet_no}
    (d / "p0_result.json").write_text(json.dumps(res))
    rows = {f"CF{w:g}": {"mark": f"CF{w:g}", "width_ft": w, "depth_ft": 1.0, "page": 0} for w in widths}
    (d / "footing_schedule.json").write_text(json.dumps(
        {"v": server._FOOTING_SCHEDULE_V, "rows": rows, "pages": [0], "scanned": 1, "rejected": []}))
    return d


def twins(jid):
    return json.loads(bytes(server.page_twins(jid, 0).body))


STRUCT_TEXT = ["FOUNDATION PLAN", "CF2.0", "(86'-0\")"]
CIVIL_TEXT = ["GRADING AND DRAINAGE PLAN", "EARTHWORK", "CONTOUR INTERVAL 1 FT", "STORM SEWER", "SANITARY SEWER"]

make_job("pcv_s", STRUCT_TEXT, sheet_no="S1-1.3")
t = twins("pcv_s")
check(set(t["families"]) == {"5", "6"} and t["reason"] is None,
      f"a structural sheet (S1-1.3) is paired as before ({sorted(t['families'])})")

make_job("pcv_c", STRUCT_TEXT, sheet_no="C3.0")
t = twins("pcv_c")
check(t["families"] == {} and "civil sheet" in (t["reason"] or "") and "C3.0" in t["reason"],
      f"a C sheet is refused by its sheet number, with the reason ({t['reason']})")

make_job("pcv_w", CIVIL_TEXT, sheet_no=None)
t = twins("pcv_w")
check(t["families"] == {} and "by its content" in (t["reason"] or "") and "GRADING" in t["reason"],
      f"a sheet with no number is refused by its words, naming them ({t['reason']})")

make_job("pcv_sw", CIVIL_TEXT, sheet_no="S2.0")
t = twins("pcv_sw")
check(set(t["families"]) == {"5", "6"}, "an S sheet runs whatever its notes say")

# the guard's answer is cached under the pass's own key and served again
make_job("pcv_c2", STRUCT_TEXT, sheet_no="C4.3")
d_c2 = server._job_dir("pcv_c2")
t1 = twins("pcv_c2")
cf = d_c2 / "p0_twins.json"
check(cf.exists() and json.loads(cf.read_text()).get("reason") == t1["reason"], "a refusal is cached beside the result")
mt = cf.stat().st_mtime_ns
twins("pcv_c2")
check(cf.stat().st_mtime_ns == mt, "and served from the cache on the next call")

# an old pairing cache (a key from before the fences) is never served
d_s = server._job_dir("pcv_s")
(d_s / "p0_twins.json").write_text(json.dumps({"key": "old-key", "families": {"9": {"kind": "wall"}}, "reason": None}))
t = twins("pcv_s")
check(set(t["families"]) == {"5", "6"}, "a pairing cached under an old key is recomputed")

# the page cap: three families under the per-family cap, over the page cap together
big = []
for k, sid in enumerate((21, 22, 23)):
    for j in range(2):
        p = rect(0, 100 + 10 * k + 3 * j, 20, 100 + 10 * k + 3 * j)
        # 15,000 ft a chain: 30,000 ft a family = 60,000 samples, the per-family
        # cap exactly; three such families are 180,000 on one sheet
        big.append({"chain_id": 100 + 2 * k + j, "class": "solid", "style_id": sid, "length_pt": 150_000.0,
                    "points": p, "color": [0, 0, 0], "width": 0.5})
make_job("pcv_big", STRUCT_TEXT, sheet_no="S1.0", extra_chains=big)
t = twins("pcv_big")
check(t["families"] == {} and "too big to check" in (t["reason"] or "") and "5 families" in (t["reason"] or ""),
      f"over the page cap nothing is paired and the answer says so ({t['reason']})")
check("181," in (t["reason"] or "") and "over the 120,000" in (t["reason"] or ""),
      "the reason counts the samples it would have needed against the cap")

# a schedule whose only width is a misread offers nothing (the same as no schedule)
make_job("pcv_wide", STRUCT_TEXT, sheet_no="S1.0", widths=(7715.0,))
t = twins("pcv_wide")
check(t["families"] == {} and "no footing schedule" in (t["reason"] or ""),
      "a cached schedule holding only a 7,715 ft width counts as no schedule")

# ---- 2b. foundation plans that are not numbered S / F (critic WRONG-1, 2026-10-01) ----------
# /footings and /footings/split are NOT guarded (the behaviour of 3b3a005): footing marks offered
# on a civil sheet are noise, marks lost on a foundation plan are worse. The twin pass's sheet
# guard lets a sheet whose own title says FOUNDATION PLAN run whatever its number (except a C
# sheet), and reads a sheet as civil by its number only when the prefix IS "C" (not CS-1).
def footings(jid):
    return json.loads(bytes(server.footings(jid, 0).body))


def split(jid):
    body = server.FootingSplitBody(points=[[pt(-1), pt(-1) + 60], [pt(41), pt(-1) + 60], [pt(41), pt(31) + 60]])
    return json.loads(bytes(server.footings_split(jid, 0, body).body))


# the critic's foundation plan (e_guard.py): marks with BOF, and ordinary general notes that
# say EXCAVATE / BACKFILL / FOUNDATION DRAIN TO DAYLIGHT / STORM / FINISH GRADING
FOUND = ["FOUNDATION PLAN  SCALE 1/4\" = 1'-0\"", "CF2.0", "(86'-0\")", "WALL", "WALL", "WALL", "CF2.0", "(84'-0\")",
         "GENERAL NOTES: 1. EXCAVATE TO UNDISTURBED NATIVE SOIL. OVER-EXCAVATION PER GEOTECH.",
         "2. BACKFILL WITH COMPACTED STRUCTURAL FILL. 3. PERIMETER FOUNDATION DRAIN TO DAYLIGHT / STORM.",
         "4. PROVIDE DRAINAGE AWAY FROM FOUNDATION AT FINISH GRADING."]
check(server.triage_score(" ".join(FOUND), "A2.0")["tier"] == "core"
      and server.triage_score(" ".join(FOUND), "A2.0")["kind"] == "foundation plan",
      "the fixture is the critic's case: its notes triage core, its title says foundation plan")
for jid, sn in (("pcf_a", "A2.0"), ("pcf_2", "2"), ("pcf_cs", "CS-1"), ("pcf_none", None), ("pcf_s", "S1-1.3")):
    make_job(jid, FOUND, sheet_no=sn)
    t = twins(jid)
    check(set(t["families"]) == {"5", "6"} and t["reason"] is None,
          f"a foundation plan numbered {sn or '(none)'} runs the twin pass ({t['reason']})")
    f = footings(jid)
    check(f["by_mark"] and f["groups"] and f["groups"]["groups"] and f["groups_reason"] is None and "refused" not in f,
          f"and /footings offers its marks and wall groups ({sn or 'no number'})")
    sp = split(jid)
    check("refused" not in sp and sp.get("perimeter_ft", 0) > 0, f"and /footings/split runs ({sn or 'no number'})")
# no Detect result yet: the marks are words only and are offered (3b3a005)
make_job("pcf_nodet", FOUND, sheet_no=None)
(server._job_dir("pcf_nodet") / "p0_result.json").unlink()
f = footings("pcf_nodet")
check(f["by_mark"] and f["groups"] is None and "Detect" in (f["groups_reason"] or ""),
      "before Detect a foundation plan's marks are offered; the groups wait for Detect")

# still refused: a C sheet even when titled FOUNDATION PLAN, and a civil sheet whose prefix only starts with C
make_job("pcf_c", FOUND, sheet_no="C4.3")
check(twins("pcf_c")["families"] == {} and "civil sheet (C4.3)" in (twins("pcf_c")["reason"] or ""),
      "a C sheet is refused by its number even when its title says FOUNDATION PLAN")
make_job("pcf_csciv", CIVIL_TEXT, sheet_no="CS-1")
t = twins("pcf_csciv")
check(t["families"] == {} and "by its content" in (t["reason"] or "") and "(CS-1)" not in (t["reason"] or ""),
      f"a CS-1 sheet is not civil by its number, but a grading sheet is still refused by its words ({t['reason']})")
make_job("pcf_civ", CIVIL_TEXT + ["SEE STRUCTURAL DRAWINGS"], sheet_no="A1.0")
check(twins("pcf_civ")["families"] == {}, "a grading sheet with no plan title stays refused whatever its letter")

# the triage sidecar's kind decides the same way as the live text
make_job("pcf_tri", CIVIL_TEXT, sheet_no="A2.0")
server.JOBS["pcf_tri"]["mtime"] = 7.0
(server._job_dir("pcf_tri") / "triage.json").write_text(json.dumps(
    {"version": server.TRIAGE_VERSION, "mtime": 7.0, "counts": {"core": 1},
     "pages": [{"page": 0, "tier": "core", "hits": [["GRADING", 1, 4]], "kind": "foundation plan"}]}))
check(set(twins("pcf_tri")["families"]) == {"5", "6"}, "a cached triage row whose kind is foundation plan allows the pass")

# civil sheets keep their footing marks (3b3a005): the route offers what the words say
make_job("pcf_cw", CIVIL_TEXT + ["CF2.0", "(86'-0\")"], sheet_no="C4.3")
check(footings("pcf_cw")["by_mark"], "a civil sheet's /footings answer is what it was before the batch (marks offered)")

# the schedule fence is unchanged: a foundation plan in a set with no schedule is refused by the twin pass
make_job("pcf_ns", FOUND, sheet_no="A2.0")
(server._job_dir("pcf_ns") / "footing_schedule.json").write_text(json.dumps(
    {"v": server._FOOTING_SCHEDULE_V, "rows": {}, "pages": [], "scanned": 0, "rejected": []}))
check(twins("pcf_ns")["reason"] == server._NO_SCHEDULE_WHY and footings("pcf_ns")["groups"]["groups"],
      "no schedule: the twin pass still refuses; the marks and wall groups stay (tests/test_bof_route.py)")

# ---- 4. twin_lines counts neighbour pairs before it lists them -----------------------------
d_s = server._job_dir("pcv_s")
res = json.loads((d_s / "p0_result.json").read_text())
walls = [c for c in res["chains"] if c["style_id"] == 6]
r_ok = tw.twin_shares(walls, FPP)
check(r_ok["refused"] is None and r_ok["pairs"] > 0 and abs(r_ok["counted_ft"] - 140.0) < 1.0,
      f"the wall family pairs as before and reports its pair count ({r_ok['pairs']})")
r_cap = tw.twin_shares(walls, FPP, max_pairs=10)
check(r_cap["refused"] and "too big to check" in r_cap["refused"] and r_cap["counted_ft"] == r_cap["drawn_ft"],
      f"over the pair cap the family is refused with the count, as drawn ({r_cap['refused']})")
r_w = tw.twin_shares(walls, FPP, widths_ft=[7715.0, 2.0], band_ft=tw.FOOTING_BAND_FT)
check("7715" not in r_w["rule"] and "2 ft" in r_w["rule"], f"a 7,715 ft width never reaches the pairing rule ({r_w['rule']})")
ft = tw.family_twins(walls, FPP, schedule_widths=[7715.0])
check("7715" not in str(ft.get("rule")), "family_twins drops a width over 20 ft too")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all civil-guard checks passed")
