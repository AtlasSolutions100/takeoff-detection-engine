"""THE CUT / FILL STORE FOLLOWS THE JOB (2026-09-26; audit D06, D07).

Opening the Earthwork tab makes the engine's Project for the job, which
holds the PDF open (pymupdf) and keeps its own store under
JOBS_DIR/cutfill/job_<id>: the datum and limit, typed heights, the reading
cache, the analysis and the computed volumes. Before this:
  * delete kept the client's PDF on Windows (the handle) and the cutfill
    folder on any OS, still listed the project, and the UI said "removed";
  * reset and a demo's fresh open kept the datum and the result, and
    Analyze answered "done" in 0.02 s with the old volumes.
Pinned here on the server functions with a real one-page PDF:
  delete  -> PDF gone (handle released), cutfill/job_<id> gone, Project
             evicted, not listed, not openable; the reply says pdf_removed
             and, when the PDF stays, why (a shared upload)
  reset   -> preview counts the store; the sweep removes it and evicts the
             Project; a fresh open has no datum, no analysis, no result
  demo open -> the same sweep
  listing -> only jobs in the registry are cut / fill projects

    python tests/test_cutfill_store.py     (fails on the commit before it)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = Path(tempfile.mkdtemp(prefix="cfs_")).resolve()
os.environ["TAKEOFF_JOBS_DIR"] = str(JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="cfs_lg_")

import pymupdf  # noqa: E402
import server  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


class _Req:
    def __init__(self, user=None, staff=False):
        self.scope = {"tk_user": user, "tk_staff": staff}


def make_pdf(path):
    doc = pymupdf.open()
    pg = doc.new_page(width=612, height=792)
    pg.insert_text((72, 72), "GRADING PLAN 100", fontsize=8)
    pg.draw_line((100, 100), (300, 100))
    doc.save(str(path))
    doc.close()


def seed(jid, pdf_name, owner="alice"):
    pdf = JOBS / pdf_name
    if not pdf.exists():
        make_pdf(pdf)
    d = server._job_dir(jid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "p0_result.json").write_text(json.dumps({"page": {"width_pt": 612, "height_pt": 792}, "chains": [],
                                                  "styles": [], "engine_sig": "t"}))
    server.JOBS[jid] = {"path": str(pdf), "pages": 1, "status": {0: "done"}, "owner": owner,
                        "mtime": round(pdf.stat().st_mtime, 2), "sizes": [[612.0, 792.0]]}
    return pdf, d


def open_cutfill(jid):
    """What the Earthwork tab does: the project, a handle on the PDF, a
    datum, an analysis and a result on disk and in memory."""
    r = server.cutfill_open(jid, server.CutfillOpenBody(page=0))
    pid = r["pid"]
    p = server._cutfill.get_project(pid)
    p.doc(0)                                    # the engine reads the sheet: the handle
    p.data["settings"]["datum"] = [100.0, 7715.0]
    p.save()
    with open(p.analysis_path("d0p0"), "w", encoding="utf-8") as f:
        json.dump({"status": "done", "families": []}, f)
    p._an_cache["d0p0"] = (0.0, {"status": "done"})
    p._readings["d0p0"] = b"reading"
    with open(os.path.join(p.dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump({"totals": {"cut_cy": 1.0}}, f)
    server._cutfill._results[pid] = {"totals": {"cut_cy": 1.0}}
    return pid, p, Path(p.dir)


# ---- delete really deletes ------------------------------------------------------------
pdf1, d1 = seed("cfs00000001", "upload_aaaaaaaaaaaa_one.pdf")
pid1, p1, cfd1 = open_cutfill("cfs00000001")
check(cfd1.is_dir() and (cfd1 / "project.json").exists() and (cfd1 / "result.json").exists(),
      "opening the Earthwork tab made the engine's store for the job")
check(any(r["id"] == pid1 for r in server._cutfill.list_projects()), "the open job is listed as a cut / fill project")
if os.name == "nt":
    try:
        os.remove(pdf1)
        held = False
        make_pdf(pdf1)                          # put it back for the real test
    except PermissionError:
        held = True
    check(held, "on Windows the engine's open handle keeps the PDF from being deleted (the defect)")

r = server.delete_job("cfs00000001", _Req("alice"))
check(r["ok"] and r["pdf_removed"] is True and r["pdf_why"] is None and not pdf1.exists(),
      f"delete: the PDF is gone - the engine's handle was closed first ({r})")
check(r["cutfill_removed"] is True and r["cutfill_failed"] == 0 and not cfd1.exists(),
      "delete: cutfill/job_<id> is gone")
check(pid1 not in server._cutfill._projects and pid1 not in server._cutfill._results and p1._closed,
      "delete: the cached Project and result are evicted, the Project closed")
check(not any(r2["id"] == pid1 for r2 in server._cutfill.list_projects()), "delete: the job is absent from /cutfill/api/projects")
try:
    server._cutfill.get_project(pid1)
    check(False, "delete: the project can no longer be opened")
except Exception as e:
    check(getattr(e, "status_code", None) == 404, "delete: opening the removed project is a 404")
check("cfs00000001" not in server.JOBS and not d1.exists(), "delete: the registry row and the job folder are gone")

# a shared upload: the PDF stays for the other job, and the reply says nothing of it (release item 2, 2026-10-02:
# "another document on this server is the same file" told a contractor a rival holds the same bid set)
pdf2, d2 = seed("cfs00000002", "upload_bbbbbbbbbbbb_two.pdf")
seed("cfs00000003", "upload_bbbbbbbbbbbb_two.pdf")           # the same bytes, another job
pid2, p2, cfd2 = open_cutfill("cfs00000002")
r = server.delete_job("cfs00000002", _Req("alice"))
check(r["pdf_removed"] is True and r["pdf_why"] is None and pdf2.exists() and "cfs00000003" in server.JOBS,
      f"a PDF another job shares stays for that job, and the reply reads like any removal ({r})")
check("same file" not in json.dumps(r) and "another" not in json.dumps(r),
      "the reply names no other document")
check(r["cutfill_removed"] is True and not cfd2.exists(), "its cut / fill store still goes")

# ---- reset and demo open are fresh ----------------------------------------------------------
pdf3, d3 = seed("cfs00000004", "upload_cccccccccccc_three.pdf")
pid3, p3, cfd3 = open_cutfill("cfs00000004")
pre = server.reset_job("cfs00000004", preview=True)
check(pre["preview"] and pre["counts"].get("cutfill", 0) >= 3 and pre["total"] >= 4,
      f"the reset preview counts the cut / fill store ({pre['counts']})")
check(cfd3.exists() and pid3 in server._cutfill._projects, "a preview changes nothing")
r = server.reset_job("cfs00000004")
check(not cfd3.exists() and pid3 not in server._cutfill._projects and pid3 not in server._cutfill._results,
      "reset: the cut / fill store is gone and the Project evicted")
check(r["removed"] >= 4, f"reset counts the store's files among what it removed ({r['removed']})")
check("cfs00000004" in server.JOBS and pdf3.exists(), "reset keeps the job and its PDF")
r2 = server.cutfill_open("cfs00000004", server.CutfillOpenBody(page=0))
p3b = server._cutfill.get_project(r2["pid"])
check(p3b is not p3 and p3b.data["settings"]["datum"] is None, "after reset the project opens fresh: no datum")
check(not os.path.exists(p3b.analysis_path("d0p0")) and p3b.analysis_status("d0p0")["status"] != "done"
      and not p3b._readings and not os.path.exists(os.path.join(p3b.dir, "result.json")),
      "after reset there is no analysis, no reading cache and no result: Analyze must read the sheet again")
res = server._cutfill.result(r2["pid"])
check(res.get("error") == "not computed", f"the result route says not computed ({res})")

# demo open: a shared job whose PDF sits under a plan root
corpus = Path(tempfile.mkdtemp(prefix="cfs_corpus_")).resolve()
server.PLAN_ROOTS.append(corpus)
demo_pdf = corpus / "demo.pdf"
make_pdf(demo_pdf)
server.DEMO_PLANS = {"demo.pdf": {"page": 0, "shows": "a demo"}}
server._DEMO_SRC.clear()
server._DEMO_SCAN_AT = 0.0
d5 = server._job_dir("cfs00000005")
d5.mkdir(parents=True, exist_ok=True)
(d5 / "p0_result.json").write_text("{}")
server.JOBS["cfs00000005"] = {"path": str(demo_pdf), "pages": 1, "status": {0: "done"}, "owner": None,
                              "mtime": round(demo_pdf.stat().st_mtime, 2), "sizes": [[612.0, 792.0]]}
pid5, p5, cfd5 = open_cutfill("cfs00000005")
r = server.demo_open("demo.pdf")
check(r["job_id"] == "cfs00000005" and r["fresh"]["failed"] == 0, f"the demo opens on its one job ({r['job_id']}, fresh {r['fresh']})")
check(not cfd5.exists() and pid5 not in server._cutfill._projects, "demo open: the cut / fill store is gone and the Project evicted")
check(r["fresh"]["removed"] >= 4, "demo open counts the store's files in what it swept")

# ---- only registry jobs are projects ----------------------------------------------------------
stray = JOBS / "cutfill" / "job_deadbeef0000"
stray.mkdir(parents=True, exist_ok=True)
(stray / "project.json").write_text(json.dumps({"id": "job_deadbeef0000", "name": "left behind", "docs": [], "pages": {},
                                                 "settings": {}}))
ids = [x["id"] for x in server._cutfill.list_projects()]
check("job_deadbeef0000" not in ids, f"a folder with no registry row is not listed ({ids})")
try:
    server._cutfill.get_project("job_deadbeef0000")
    check(False, "a folder with no registry row cannot be opened")
except Exception as e:
    check(getattr(e, "status_code", None) == 404, "a folder with no registry row is a 404")
check(server._cutfill_job_dir("../evil") is None and server._cutfill_job_dir("cfs00000004") is not None,
      "the store path is a direct child of JOBS_DIR/cutfill or nothing")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all cut / fill store checks passed")
