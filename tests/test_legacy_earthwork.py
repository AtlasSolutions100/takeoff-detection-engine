"""THE OLD EARTHWORK CALCULATOR'S ROWS COUNT NOWHERE (release blocker B1; the owner's decision, 2026-10-01 evening).

v1.8.5 had an Earthwork calculator; this app removed it (Cut / fill replaces it). Koby: "Clients were told this is beta,
so old earthwork work does not need to be kept. Do the simplest thing: the old calculator's earthwork rows count nowhere:
not in the takeoff, the bid sheet or the report. Those pages show one short note: 'Earthwork from the old calculator was
removed. Redo it in Cut / fill.' ... Leaving the old files on disk is fine, because it keeps an undo clean. Touch nothing
else." Design: docs/fable/LEGACY-EARTHWORK-DESIGN.md. Fixtures: tests/fixtures/v185_rehearsal - real files of the v1.8.5
rehearsal store (2026-10-01; the line geometry left out, no account names), and v1.8.5's own bid-sheet CSV of job 1.

  R1  every reader (bid CSV, on-screen bid via /elements, report, /approved) reads a v1.8.5 export without its old
      calculator rows; nothing else in it changes; the files are byte-identical after every read, a page load and a save
  R3  which rows: its CY rows by name; its limit of work's Clear & Grub / fence only when the record says the limit was
      in the takeoff (an Areas row of that name survives); a row with its own mark (cutfill) never
  R4  the note, once per affected page, in /approved, the CSV header and the report; never OUT OF DATE; the old caveats
      not printed; it goes when this app approves the page again
  R2  a test build's `earthwork-old` rows in a record are dropped (and the page then has the note)
  H   the engine's own rows keep every rule of rounds 16-23

    python tests/test_legacy_earthwork.py
"""
import csv
import hashlib
import io
import json
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = Path(tempfile.mkdtemp(prefix="ewrm_")).resolve()
os.environ["TAKEOFF_JOBS_DIR"] = str(JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="ewrm_lg_")

import pymupdf  # noqa: E402
import server  # noqa: E402
from cutfill import placement as cfpl  # noqa: E402
from cutfill.project import Project  # noqa: E402
from takeoff_engine.detect_page import engine_sig  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "v185_rehearsal"
NOTE = "Earthwork from the old calculator was removed. Redo it in Cut / fill."
fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


def _start(self, key, force=False, full=False):     # a unit test reads no sheet (as tests/test_cutfill_sheet_picker.py)
    with self._lock:
        self.jobs[key] = {"status": "done", "progress": "test", "full": False, "rerun": None, "error": ""}
    return {"status": "queued"}


Project.start_analysis = _start
REQ0 = types.SimpleNamespace(scope={})


def seed(jid, pages=1, w=2592.0, h=1728.0):
    pdf = JOBS / f"{jid}.pdf"
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page(width=w, height=h).insert_text((72, 72), f"GRADING PLAN {i + 1}", fontsize=8)
    doc.save(str(pdf))
    doc.close()
    d = server._job_dir(jid)
    d.mkdir(parents=True, exist_ok=True)
    for i in range(pages):
        (d / f"p{i}_result.json").write_text(json.dumps({"page": {"width_pt": w, "height_pt": h}, "chains": [], "styles": [],
                                                         "engine_sig": engine_sig(), "scale": {"ft_per_paper_inch": 20.0}}))
    server.JOBS[jid] = {"path": str(pdf), "pages": pages, "status": {i: "done" for i in range(pages)}, "owner": None,
                        "mtime": round(pdf.stat().st_mtime, 2), "sizes": [[w, h]] * pages}
    return d


def put(d, pno, name):
    (d / f"p{pno}_elements.json").write_bytes((FIX / f"{name}_p0_elements.json").read_bytes())
    (d / f"p{pno}_calcstate.json").write_bytes((FIX / f"{name}_p0_calcstate.json").read_bytes())


def disk(d):
    return {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(d.iterdir())
            if f.name.endswith(("_elements.json", "_calcstate.json"))}


def csv_of(jid):
    return server.bidsheet_csv(jid, REQ0, ignore=[]).body.decode("utf-8-sig")


def lines_of(text):
    """{bid line name: qty} of a bid-sheet CSV's numbered lines."""
    out = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 4 and row[0].strip().isdigit():
            out[row[1]] = row[3]
    return out


def report_text(jid):
    r = server.report_pdf(jid, REQ0, ignore=[])
    return " ".join(" ".join(pg.get_text() for pg in pymupdf.open(r.path)).split())


def approve(jid, page, items):
    try:
        server.approve(jid, page, server.ApproveBody(labels={}, extra_items=items, scale_seen=server._scale_stamp(jid, page)))
        return None
    except server.HTTPException as e:
        return e


OLD1 = ["Site Cut 646.0 CY", "Site Fill 749.0 CY", "Strip Topsoil 1999.0 CY", "Export Topsoil 1999.0 CY", "Zone 1 fill 472.0 CY",
        "Zone 1 topsoil strip 231.0 CY", "Silt / Construction Fence 1319.0 LF", "Clear & Grub 107931.0 SF", "Import Structural 103.0 CY"]
txt = lambda rows: [f"{it['label']} {it.get('qty')} {it.get('unit')}" for it in rows]   # noqa: E731

# ---- R3: which rows, on the rehearsal store's job 1 (C4.0: earthwork with a drawn limit and a grading zone) -------------
J1 = "ewr1"
D1 = seed(J1)
put(D1, 0, "job1_c4")
raw1 = json.loads((D1 / "p0_elements.json").read_text(encoding="utf-8"))
rec1 = json.loads((D1 / "p0_calcstate.json").read_text(encoding="utf-8"))
gone1 = server._ew_old_items(raw1, rec1)
view1 = server._export_view(J1, 0, raw1)
kept1 = [it for it in raw1["items"] if not any(it is g for g in gone1)]
check(txt(gone1) == OLD1 and view1["items"] == kept1 and len(view1["items"]) == 40
      and "C-PRO-WATER HYDRANT" in [it["label"] for it in view1["items"]]
      and {k: v for k, v in view1.items() if k != "items"} == {k: v for k, v in raw1.items() if k != "items"},
      f"R3: job 1 - the calculator's 9 rows (its volumes, topsoil, zone, balance and its limit's Clear & Grub and fence) "
      f"are removed; the other 40 rows and everything else in the export are exactly as they were: {txt(gone1)}")
# the limit's two rows only with the record's word; a row with its own mark never; an export this app wrote never
# F2 (critic legacy-5, RISK-1 / RISK-2): the limit's two rows go only when their numbers are the record's limit's own -
# its area and perimeter measured again from `ew_bound` at the export's scale (107,931.37 SF and 1,319.41 LF on job 1)
SCALE = {k: raw1[k] for k in ("scale", "imageDpi")}
geo = server._ew_limit_geom(raw1, rec1)
not_added = dict(rec1, ew=dict(rec1["ew"], added=False))
no_bound = {k: v for k, v in rec1.items() if k != "ew_bound"}
cg = lambda *qs: dict(SCALE, items=[{"label": "Clear & Grub", "qty": q, "unit": "SF"} for q in qs])   # noqa: E731
areas_cg = dict(SCALE, items=[{"label": "Clear & Grub", "qty": 5000.0, "unit": "SF"}, {"label": "Silt / Construction Fence", "qty": 300.0, "unit": "LF"},
                              {"label": "Trench excavation (8\" SS)", "qty": 40.0, "unit": "CY"}, {"label": "Backfill (8\" SS)", "qty": 30.0, "unit": "CY"},
                              {"label": "Bedding gravel (8\" SS)", "qty": 5.0, "unit": "CY"}, {"label": "Mass Excavation", "qty": 90.0, "unit": "CY"},
                              {"label": "Sheet fill area (P6-SHEET)", "qty": 731.9, "unit": "SF"}, {"label": "Pad fill area", "qty": 10.0, "unit": "SF"}])
check(round(geo["area"], 2) == 107931.37 and round(geo["perim"], 2) == 1319.41
      and server._ew_old_items(raw1, not_added) == gone1
      and server._ew_old_items(raw1, no_bound) == [it for it in gone1 if it["unit"] == "CY"]
      and server._ew_old_items(areas_cg, rec1) == [] and server._ew_old_items(areas_cg, {}) == []
      and txt(server._ew_old_items(cg(5000.0, 107931.0), rec1)) == ["Clear & Grub 107931.0 SF"]
      and server._ew_old_items(cg(107931.4), rec1) == [] and server._ew_old_items(cg(107930.0), rec1) == []
      and server._ew_old_items({"items": [{"label": "Site Cut", "qty": 5.0, "unit": "CY", "src": "cutfill"}]}, rec1) == []
      and server._ew_old_items(dict(raw1, src_marks=1), rec1) == [],
      "R3 / F2: the limit's Clear & Grub and fence go when their numbers are the record's limit's own, added or not "
      "(RISK-1); an Areas row of that name with another number stays, even with a limit on record (RISK-2) - one of two "
      "rows of the name goes, the one that is the limit's; without a limit on record they stay; trench, structure and area "
      "rows stay; a row marked cutfill named Site Cut stays; an export this app wrote is never touched")
# a picked limit: the chain in the page's result
JP = "ewpick"
DP = seed(JP)
(DP / "p0_elements.json").write_bytes((FIX / "job1_c4_p0_elements.json").read_bytes())
res_p = json.loads((DP / "p0_result.json").read_text())
res_p["chains"] = [{"chain_id": 7, "points": rec1["ew_bound"]["pts"] + [rec1["ew_bound"]["pts"][0]]}]
(DP / "p0_result.json").write_text(json.dumps(res_p))
rec_pick = dict(rec1, ew_bound={"chain_id": 7})
rec_gone = dict(rec1, ew_bound={"chain_id": 99})
check(txt(server._ew_old_items(raw1, rec_pick, JP, 0)) == OLD1
      and server._ew_old_items(raw1, rec_gone, JP, 0) == [it for it in gone1 if it["unit"] == "CY"],
      "F2: a picked limit is measured from its chain in the page's result; a chain the result no longer has proves nothing")

# ---- R1 / R4: every reader -------------------------------------------------------------------------------------------
before1 = disk(D1)
c1 = csv_of(J1)
ref = (FIX / "job1_c4_bidsheet_v185.csv").read_text(encoding="utf-8-sig")
new_l, ref_l = lines_of(c1), lines_of(ref)
EW_LINES = {"Clear & Grub", "Strip Topsoil", "Site Cut", "Site Fill", "Silt Fence", "Export Cut", "Import Structural",
            "Export Topsoil", "Import Topsoil"}
same = [n for n in ref_l if n in new_l and n not in EW_LINES]
diff = [n for n in same if ref_l[n] != new_l[n]]
check(NOTE in c1.split("\n\n", 1)[0] and c1.count(NOTE) == 1 and not diff and len(same) >= 15
      and all(new_l.get(n, "") == "" for n in ("Clear & Grub", "Strip Topsoil", "Site Cut", "Site Fill"))
      and new_l.get("Silt Fence") in ("", "887.6") and ref_l.get("Silt Fence") == "2206.6" and "Silt / Construction Fence 1319" not in c1
      and "Export Topsoil,CY,1999" not in c1 and "Import Structural,CY,103" not in c1 and "OUT OF DATE" not in c1,
      f"R1 / R4: the bid-sheet CSV - the old rows gone (Silt Fence loses the limit's 1,319 LF; the drawn P1-FENCE stays where the template files it), every other line "
      f"as v1.8.5 printed it ({len(same)} lines compared, differing: {diff}), the note once in the header")
el = server.get_elements(J1, 0)
el_items = json.loads(el.body)["items"] if hasattr(el, "body") else None
check(el_items == view1["items"], "R1: /elements (the on-screen All approved sheets) serves the export without the old rows")
ap1 = server.approved_pages(J1)
check(ap1.get("notes") == {"0": NOTE} and ap1.get("outdated") == {},
      f"R4: /approved - the note for the page (one page: no number in front), never OUT OF DATE: {ap1}")
t1 = report_text(J1)
check(t1.count(NOTE) >= 1 and "646" not in t1 and "107,931" not in t1 and "OLD CALCULATOR" not in t1 and "OUT OF DATE" not in t1,
      "R1 / R4: the report - the note, none of the old rows' numbers")
server.get_calcstate(J1, 0)
server._calcstate_put(J1, 0, json.dumps({"v": 1, "engine_sig": "x", "rows": [], "checks": {"done": ["a"]}}).encode())
rec_after = json.loads((D1 / "p0_calcstate.json").read_text(encoding="utf-8"))
after1 = disk(D1)
check(after1["p0_elements.json"] == before1["p0_elements.json"]
      and all(rec_after.get(k) == rec1.get(k) for k in ("ew", "ew_bound", "gzones")),
      "R1: the export is byte-identical after the CSV, /elements, /approved, the report, a page load and a save; the save "
      "keeps v1.8.5's ew / ew_bound / gzones (undo)")

# ---- R4: the old caveats (job Belle Farms: EARTHWORK block) and several pages -------------------------------------------
J2 = "ewr2"
D2 = seed(J2, pages=3)
put(D2, 0, "job_bf")
put(D2, 2, "job1_c4")
b2 = disk(D2)
rawb = json.loads((D2 / "p0_elements.json").read_text(encoding="utf-8"))
vb = server._export_view(J2, 0, rawb)
ap2 = server.approved_pages(J2)
t2 = report_text(J2)
c2 = csv_of(J2)
server.get_elements(J2, 0)
check([n[:20] for n in vb["notes"]] == [rawb["notes"][0][:20]] and len(rawb["notes"]) == 7
      and ap2.get("notes") == {"0": "Page 1: " + NOTE, "2": "Page 3: " + NOTE}
      and "contour interpolation" not in t2 and "Page 1: " + NOTE in t2 and "Page 3: " + NOTE in t2
      and c2.count("Page 1: " + NOTE) == 1 and c2.count("Page 3: " + NOTE) == 1 and disk(D2) == b2,
      f"R4: the old calculator's caveats are not printed (the reviewer's own note stays); several pages: 'Page N: ' in "
      f"front, once per page, in /approved, the report and the CSV; the files untouched: {ap2.get('notes')}")

# ---- R4 / R2: unapproved pages - a browser dropped rows; a test build's marked rows; saved inputs ---------------------------
J3 = "ewr3"
D3 = seed(J3, pages=3)
(D3 / "p0_calcstate.json").write_text(json.dumps({"v": 1, "ew_dropped": True}))
(D3 / "p1_calcstate.json").write_text(json.dumps({"v": 1, "rows": [{"label": "Site Fill", "qty": 900, "unit": "CY", "src": "earthwork-old"},
                                                                     {"label": "Paving", "qty": 3, "unit": "EA"}]}))
(D3 / "p2_calcstate.json").write_text(json.dumps({"v": 1, "rows": [{"label": "Paving", "qty": 3, "unit": "EA"}]}))
b3 = disk(D3)
g31 = server.get_calcstate(J3, 1)
check(g31["rows"] == [{"label": "Paving", "qty": 3, "unit": "EA"}] and g31.get("ew_dropped") is True and disk(D3) == b3,
      "R2: a test build's earthwork-old row is not shown, and the read writes nothing")
server._calcstate_put(J3, 1, json.dumps({"v": 1, "rows": [{"label": "Site Fill", "qty": 900, "unit": "CY", "src": "earthwork-old"}]}).encode())
r31 = json.loads((D3 / "p1_calcstate.json").read_text())
check(r31["rows"] == [] and r31.get("ew_dropped") is True, "R2: ...nor stored by a save (a stale tab of that build); the record says so")
n3 = server.approved_pages(J3).get("notes")
check(n3 == {"0": "Page 1: " + NOTE, "1": "Page 2: " + NOTE},
      f"R4: an unapproved page whose browser dropped old rows, or whose record held a test build's, has the note; a page "
      f"without old work has none: {n3}")

# ---- F3 (critic legacy-5, RISK-3): saved inputs alone say nothing ---------------------------------------------------------
JS = "ewr3s"
DS = seed(JS, pages=2)
(DS / "p0_calcstate.json").write_bytes((FIX / "job_bf_p0_calcstate.json").read_bytes())       # inputs, no export here
(DS / "p1_calcstate.json").write_text(json.dumps({"v": 1, "ew": {"rows": [{"chain_id": 3}], "added": False},
                                                  "gzones": [{"name": "Zone 1", "added": False}]}))   # a zone saved only
rs_cs = json.loads((DS / "p0_calcstate.json").read_text())
check(rs_cs["ew"].get("added") is True and server.approved_pages(JS).get("notes") == {} and server.approved_pages(JS).get("ew_removed") == [],
      f"F3: pages whose records hold only the old calculator's saved inputs (a calculation, a zone) and no removed row get "
      f"no note: {server.approved_pages(JS).get('notes')}")
# the fields a whole-job view reads: /approved `notes` per page, `ew_removed` (the pages), `ew_note`; /elements `ew_note`
apf = server.approved_pages(J2)
elf = json.loads(server.get_elements(J2, 2).body)
check(apf.get("ew_removed") == [0, 2] and apf.get("ew_note") == NOTE and elf.get("ew_note") == "Page 3: " + NOTE
      and "ew_note" not in json.loads((D2 / "p2_elements.json").read_text(encoding="utf-8")),
      "F1: /approved lists the pages (`ew_removed`) with the sentence (`ew_note`) beside the per-page `notes`; /elements "
      "carries the page's `ew_note` (in the answer only)")

# ---- R4: approved again by this app - a marked export without old rows; the note goes ---------------------------------
def approve_ans(jid, page, items):
    return json.loads(server.approve(jid, page, server.ApproveBody(labels={}, extra_items=items,
                                                                   scale_seen=server._scale_stamp(jid, page))).body)


a1 = approve_ans(J1, 0, [{"label": "Paving", "qty": 3, "unit": "EA"}])
a1b = approve_ans(J1, 0, [{"label": "Paving", "qty": 3, "unit": "EA"}])
check(a1.get("ew_said") == "Approved. The old calculator's earthwork is not in it." and "ew_said" not in a1b,
      f"F4: the approve that takes the old earthwork out of the bid says so once, in its answer: {a1.get('ew_said')} / "
      f"{a1b.get('ew_said')}")
e1 = None
x1 = json.loads((D1 / "p0_elements.json").read_text(encoding="utf-8"))
check(e1 is None and x1.get("src_marks") == 1 and not server._ew_old_items(x1, rec1)
      and [it["label"] for it in x1["items"]] == ["Paving"] and server.approved_pages(J1).get("notes") == {}
      and NOTE not in csv_of(J1) and "ew_old_notes" not in x1 and "ew_removed" not in x1,
      f"R4: approved again by this app: a marked export, no old rows, the note goes: {getattr(e1, 'detail', None)}")

# ---- H: a job born in this app - the engine's own rows keep every rule ---------------------------------------------------
JH = "ewh"
DH = seed(JH, w=612, h=792)
eh = approve(JH, 0, [{"label": "Site Fill", "qty": 900.0, "unit": "CY", "src": "cutfill"}])
check(eh is not None and eh.status_code == 409 and "no cut / fill behind them" in str(eh.detail),
      f"H: the engine's rows with no cut / fill behind them are still refused: {getattr(eh, 'detail', None)}")
server.cutfill_open(JH, server.CutfillOpenBody(page=0, pages=[0]))
PH = server._cutfill.get_project(f"job_{JH}")
with PH._lock:
    PH.data["settings"].update({"display_page": "d0p0", "region": [[100, 100], [500, 100], [500, 500], [100, 500]],
                                "limit": {"source": "drawn"}, "takeoff": {"strip_in": 6}, "scale_ft_per_in": 20.0,
                                "scale_source": "typed"})
    res = {"display_page": "d0p0", "ft_per_in": 20.0, "warnings": [], "computed_at": 9600.4,
           "totals": {"cut_cy": 0.0, "fill_cy": 600.4, "net_cy": 600.4, "area_sf": 24305.6}, "inputs": cfpl.inputs_sig(PH)}
Path(PH.dir, "result.json").write_text(json.dumps(res))
server._cutfill._results[PH.id] = res
eng = [{"label": r["label"], "qty": r["qty"], "unit": r["unit"], "src": "cutfill"}
       for r in (server._cf_state(JH, rows=True).get("summary") or {}).get("rows") or [] if (r.get("qty") or 0) > 0]
server.cutfill_rows(JH, 0, server.CutfillRowsBody(added=True, pid=PH.id, rows=eng))
eh2 = approve(JH, 0, eng)
xh = json.loads((DH / "p0_elements.json").read_text(encoding="utf-8")) if (DH / "p0_elements.json").exists() else {}
check(eng and eh2 is None and xh.get("src_marks") == 1 and server._export_view(JH, 0, xh) is xh
      and sorted(it["label"] for it in xh["items"]) == sorted(r["label"] for r in eng)
      and server.approved_pages(JH).get("notes") == {} and server.approved_pages(JH).get("outdated") == {},
      f"H: a job born in this app: its Cut / fill rows (Site Fill, Clear & Grub, ...) export and read as they are, no "
      f"note: {getattr(eh2, 'detail', None)}")

print("\n" + (f"{fails} failure(s)" if fails else "all ok"))
sys.exit(1 if fails else 0)
