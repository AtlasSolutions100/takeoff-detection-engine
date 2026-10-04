"""SHEETS FOR CUT / FILL, ONE OPEN FOR THE WHOLE JOB (2026-09-28, Koby: "why do we have to go back and press detect
and cut/fill on both sheets ... make the user select to do it on one sheet and then select all sheets needed").

Opening Cut / fill handed the engine the sheet on screen only; every later sheet started as "none" and was read with
its own Analyze button, so a two-sheet job went back to the sheet twice. The picker sends the ticked pages:

  list   -> GET /cutfill/sheets: fast without labels (is the job set up, how many pages); with labels every page,
            its printed sheet number, detected (by the engine running here) or not, being detected, its role
  open   -> each ticked, detected sheet joins as both surfaces with its own detection and starts being read, two
            at a time; one not detected (or detected by older code) is left out and said - the sheet on screen
            too; a sheet already in the job is never reported left out; a role the operator set stands; the
            review's decisions go with the sheet on screen only

The critic's round (out/verify/2026-09-28/sheet-picker/CRITIC-picker.md): W2 (sheet numbers), W3 (the sheet on
screen), W5 (in the job, not left out), R1 (older code), R2 (two reads at a time), R3 (a fast first answer),
M2 (a page not in the set), M10 (the roles read under the lock).

    python tests/test_cutfill_sheet_picker.py
"""
import inspect
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = Path(tempfile.mkdtemp(prefix="cfpick_")).resolve()
os.environ["TAKEOFF_JOBS_DIR"] = str(JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="cfpick_lg_")

import pymupdf  # noqa: E402
import server  # noqa: E402
# THE TAKEOFF STARTS EMPTY (2026-10-03): an approve with nothing in the takeoff is refused, so the approves here that
# are about the scale or the cut / fill rows carry one counted row ("Test count", 1 EA - a count needs no scale).
from cutfill.project import Project  # noqa: E402
from takeoff_engine.detect_page import engine_sig  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


# the reads the picker starts are recorded, not run (a unit test has no business reading sheets). With HOLD on, a
# started read shows as running in the project's jobs until the test ends it, the way the engine's own does; with
# it off it ends at once. The limit of two reads counts every project of the server (the third critic's r2), so a
# read left running in one section would hold the next section's reads back.
started = []
started_full = []                 # (project, key, full): a read made again from the sheet's lines
HOLD = {"on": False}


def _start(self, key, force=False, full=False):
    started.append((self.id, key, force))
    started_full.append((self.id, key, full))
    with self._lock:
        self.jobs[key] = {"status": "running" if HOLD["on"] else "done", "progress": "test", "full": False,
                          "rerun": None, "error": ""}
    return {"status": "queued"}


Project.start_analysis = _start


def read_done(p, key, pickle=b"read"):
    """A full read made AND interpreted, as the engine leaves it: the cached reading, then the analysis, written now."""
    os.makedirs(os.path.dirname(p.reading_path(key)), exist_ok=True)
    Path(p.reading_path(key)).write_bytes(pickle)
    Path(p.analysis_path(key)).write_text(json.dumps({"reader": "stub", "width": 612, "height": 792, "labels": []}))


def wait_started(n, secs=6.0):
    t0 = time.time()
    while len(started) < n and time.time() - t0 < secs:
        time.sleep(0.05)
    return len(started)


def settle():
    """Every read left running ends, a rerun it had queued with it (the engine's loop consumes it), and every queue
    has drained: the next section starts on a quiet server."""
    for pp in set(list(server._cutfill._projects.values()) + list(server._CF_READERS)):
        with pp._lock:
            for j in pp.jobs.values():
                if (j or {}).get("status") in ("running", "queued") and not (j or {}).get("host_wait"):
                    j["status"] = "done"
                    j["rerun"] = None
    t0 = time.time()
    while server._CF_QUEUES and time.time() - t0 < 10:
        time.sleep(0.05)


def seed(jid, pages=3, detected=(0, 1), texts=None):
    pdf = JOBS / f"{jid}.pdf"
    doc = pymupdf.open()
    for i in range(pages):
        pg = doc.new_page(width=612, height=792)
        pg.insert_text((72, 72), f"GRADING PLAN {i + 1}", fontsize=8)
        # the sheet number where a title block prints it (bottom right, the largest short number)
        t = (texts or {}).get(i, f"3.{i}")
        if t:
            pg.insert_text((540, 760), t, fontsize=22)
    doc.save(str(pdf))
    doc.close()
    d = server._job_dir(jid)
    d.mkdir(parents=True, exist_ok=True)
    for i in detected:
        (d / f"p{i}_result.json").write_text(json.dumps({"page": {"width_pt": 612, "height_pt": 792}, "chains": [],
                                                         "styles": [], "engine_sig": engine_sig()}))
    server.JOBS[jid] = {"path": str(pdf), "pages": pages, "status": {i: "done" for i in detected}, "owner": None,
                        "mtime": round(pdf.stat().st_mtime, 2), "sizes": [[612.0, 792.0]] * pages}
    return d


def roles(jid):
    p = server._cutfill.get_project(f"job_{jid}")
    return {k: v.get("role") for k, v in sorted(p.data["pages"].items())}


def refused(jid, body):
    try:
        server.cutfill_open(jid, body)
        return None
    except server.HTTPException as e:
        return e


# ---- 1. the list, before the job has a cut / fill ----------------------------------------------------------
jid = "pick1"
seed(jid)
head = server.cutfill_sheets(jid)
check(head == {"n": 3, "in_project": False}, f"without labels, only what the open needs first, fast: {head}")
lst = server.cutfill_sheets(jid, labels=1)
check([(x["page"], x["label"], x["detected"], x["stale"], x["running"], x["role"]) for x in lst["pages"]]
      == [(0, "3.0", True, False, False, None), (1, "3.1", True, False, False, None), (2, "3.2", False, False, False, None)]
      and lst["in_project"] is False,
      f"with labels: every page with its printed sheet number, detected or not, no role yet: {lst}")

# ---- 2. one open for the whole job ---------------------------------------------------------------------------
r = server.cutfill_open(jid, server.CutfillOpenBody(page=0, pages=[0, 1, 2], edits={"excluded_chain_ids": []}))
check(r["page"] == 0 and r["joined"] == [1] and r["skipped"] == [{"page": 2, "why": "not detected"}] and r["in_job"] == 2,
      f"the ticked, detected sheets join; the one not detected is left out and said; 2 take part: {r['joined']}, "
      f"{r['skipped']}, {r['in_job']}")
check(roles(jid) == {"d0p0": "both", "d0p1": "both", "d0p2": "none"},
      f"both ticked sheets are 'both' at once (the second no longer starts as 'none'): {roles(jid)}")
p = server._cutfill.get_project(f"job_{jid}")
check(p.page_settings("d0p1")["core_result"] == str(server.result_path(server._job_dir(jid), 1))
      and p.page_settings("d0p0")["core_result"] == str(server.result_path(server._job_dir(jid), 0)),
      "each sheet hands the engine its own detection")
wait_started(2)
check(sorted(k for _, k, f in started) == ["d0p0", "d0p1"] and not any(f for _, _, f in started),
      f"both start being read (no Analyze clicks): {started}")
check(p.data["settings"]["display_page"] == "d0p0", "the project opens on the sheet on screen")
lst2 = server.cutfill_sheets(jid, labels=1)
check([x["role"] for x in lst2["pages"]] == ["both", "both", None] and lst2["in_project"],
      f"the list now shows their roles: {[x['role'] for x in lst2['pages']]}")

# ---- 3. the old way still works: a sheet opened on its own after that ---------------------------------------
started.clear()
r2 = server.cutfill_open(jid, server.CutfillOpenBody(page=1))
time.sleep(0.3)
check(roles(jid)["d0p1"] == "both" and r2["joined"] == [] and not started,
      "opening from a sheet with no picker changes nothing the picker set, and starts no read")

# ---- 4. a role the operator set stands; a sheet left out before joins when ticked ----------------------------
p.page_settings("d0p1")["role"] = "existing"
p.page_settings("d0p0")["role"] = "none"
p.save()
started.clear()
server.cutfill_open(jid, server.CutfillOpenBody(page=0, pages=[0, 1]))
check(roles(jid)["d0p1"] == "existing" and roles(jid)["d0p0"] == "both",
      f"ticked again: the operator's 'existing' stands, a sheet set to none joins as both: {roles(jid)}")

# ---- 5. the sheet on screen left out of the picker ------------------------------------------------------------
jid2 = "pick2"
seed(jid2, pages=3, detected=(0, 1, 2))
started.clear()
r5 = server.cutfill_open(jid2, server.CutfillOpenBody(page=2, pages=[0, 1], edits={"excluded_chain_ids": [5]}))
p2 = server._cutfill.get_project(f"job_{jid2}")
check(r5["page"] == 0 and roles(jid2) == {"d0p0": "both", "d0p1": "both", "d0p2": "none"}
      and p2.data["settings"]["display_page"] == "d0p0" and not p2.page_settings("d0p0").get("core_edits"),
      f"the sheet on screen, not ticked, stays out; the first ticked sheet is shown; the screen's review is not "
      f"put on another sheet: {roles(jid2)}")

# ---- 6. refused: a page not in this set (M2), nothing ticked, nothing detected --------------------------------
e6 = refused(jid2, server.CutfillOpenBody(page=0, pages=[0, 7]))
check(e6 is not None and e6.status_code == 400 and "page 8 is not in this plan set (it has 3)" in str(e6.detail),
      f"a page this set does not have is refused, with the page named (it was dropped without a word): {e6 and e6.detail}")
e6b = refused(jid2, server.CutfillOpenBody(page=0, pages=[]))
check(e6b is not None and e6b.status_code == 400, "nothing ticked: refused")
jid6 = "pick6"
seed(jid6, pages=2, detected=())
e6c = refused(jid6, server.CutfillOpenBody(page=0, pages=[0, 1]))
check(e6c is not None and e6c.status_code == 400 and "detect them first" in str(e6c.detail)
      and not os.path.exists(os.path.join(server._cutfill.OUT, "job_pick6", "project.json")),
      f"no ticked sheet detected: refused before anything is made: {e6c and e6c.detail}")

# ---- 7. W3: the sheet on screen, ticked but not detected, is left out and said like any other ------------------
jid7 = "pick7"
seed(jid7, pages=2, detected=(1,))
started.clear()
r7 = server.cutfill_open(jid7, server.CutfillOpenBody(page=0, pages=[0, 1]))
p7 = server._cutfill.get_project(f"job_{jid7}")
check(r7["skipped"] == [{"page": 0, "why": "not detected"}] and r7["page"] == 1 and r7["in_job"] == 1
      and roles(jid7).get("d0p0") in (None, "none") and roles(jid7)["d0p1"] == "both"
      and p7.data["settings"]["display_page"] == "d0p1",
      f"the sheet on screen could not be detected: left out and said, the project opens on the one that joined "
      f"(it joined with no detection and no word): {r7['skipped']}, page {r7['page']}, {roles(jid7)}")

# ---- 8. R1: a detection made by older engine code is not handed over: listed, detected again first ------------
jid8 = "pick8"
d8 = seed(jid8, pages=2, detected=(0, 1))
(d8 / "p1_result.json").write_text(json.dumps({"page": {"width_pt": 612, "height_pt": 792}, "chains": [], "styles": [],
                                               "engine_sig": "older-code"}))
l8 = server.cutfill_sheets(jid8, labels=1)
check([(x["detected"], x["stale"]) for x in l8["pages"]] == [(True, False), (False, True)],
      f"the list: page 2 was detected by older code, so it is not 'detected' (the page itself asks for Detect): {l8['pages']}")
r8 = server.cutfill_open(jid8, server.CutfillOpenBody(page=0, pages=[0, 1]))
p8 = server._cutfill.get_project(f"job_{jid8}")
check(r8["skipped"] == [{"page": 1, "why": "detected by an older version: detect it again"}] and r8["joined"] == []
      and not p8.page_settings("d0p1").get("core_result"),
      f"the open leaves it out and says why, and hands nothing over: {r8['skipped']}")
# the second critic's R1: the plain open (no picker) of a sheet whose detection is older says so; a sheet already in
# the job with an older detection is named, never "left out"
(d8 / "p0_result.json").write_text(json.dumps({"page": {"width_pt": 612, "height_pt": 792}, "chains": [], "styles": [],
                                               "engine_sig": "older-code"}))
r8b = server.cutfill_open(jid8, server.CutfillOpenBody(page=0))
r8c = server.cutfill_open(jid8, server.CutfillOpenBody(page=0, pages=[0]))
check(r8b["older"] == [0] and r8c["older"] == [0] and r8c["skipped"] == [],
      f"an older detection taking part is said on every open (plain {r8b['older']}, picker {r8c['older']}), not 'left out'")

# ---- 9. W5: "Sheets…" sends the sheets in the job too: one read by the engine alone is never "left out" -------------
jid9 = "pick9"
seed(jid9, pages=3, detected=(0, 2))
server.cutfill_open(jid9, server.CutfillOpenBody(page=0, pages=[0]))
p9 = server._cutfill.get_project(f"job_{jid9}")
p9.page_settings("d0p1")["role"] = "existing"          # set in the engine's Sheets card, read there alone
p9.save()
r9 = server.cutfill_open(jid9, server.CutfillOpenBody(page=0, pages=[2, 0, 1]))
check(r9["skipped"] == [] and r9["joined"] == [2] and r9["in_job"] == 3 and roles(jid9)["d0p1"] == "existing",
      f"a sheet in the job with no detection here stays as it is and is not reported left out; 3 take part: "
      f"skipped {r9['skipped']}, joined {r9['joined']}, in_job {r9['in_job']}")

# ---- 10. R2: two reads at a time; the others wait their turn, saying so --------------------------------------------
settle()
HOLD["on"] = True
jid10 = "pick10"
seed(jid10, pages=4, detected=(0, 1, 2, 3))
started.clear()
server.cutfill_open(jid10, server.CutfillOpenBody(page=0, pages=[0, 1, 2, 3]))
p10 = server._cutfill.get_project(f"job_{jid10}")
wait_started(2)
time.sleep(0.5)
waiting = {k: (p10.jobs.get(k) or {}).get("progress") for k in ("d0p2", "d0p3")}
check(sorted(k for _, k, _ in started) == ["d0p0", "d0p1"] and all(v == server._CF_WAIT for v in waiting.values()),
      f"four ticked sheets: two are read at once, the other two wait and say so: started {started}, waiting {waiting}")
with p10._lock:
    p10.jobs["d0p0"]["status"] = "done"
n = wait_started(3)
check(n == 3 and started[2][1] == "d0p2" and (p10.jobs.get("d0p3") or {}).get("host_wait"),
      f"a read that finishes lets the next one start, still two at a time: {started}")
with p10._lock:
    p10.jobs["d0p1"]["status"] = "done"
check(wait_started(4) == 4, f"and the last: {started}")

# the second critic's R2: ONE queue per project - a "Sheets…" while the picker's reads wait adds to it; still two
settle()
jid13 = "pick13"
seed(jid13, pages=6, detected=(0, 1, 2, 3, 4, 5))
started.clear()
server.cutfill_open(jid13, server.CutfillOpenBody(page=0, pages=[0, 1, 2, 3]))
p13 = server._cutfill.get_project(f"job_{jid13}")
wait_started(2)
server.cutfill_open(jid13, server.CutfillOpenBody(page=0, pages=[0, 1, 2, 3, 4, 5]))
time.sleep(2.0)
running13 = [k for k, j in p13.jobs.items() if j.get("status") == "running"]
check(len(started) == 2 and len(running13) == 2 and all((p13.jobs.get(k) or {}).get("host_wait") for k in ("d0p2", "d0p3", "d0p4", "d0p5")),
      f"an open and a 'Sheets…' together: still two reading, four waiting (it ran 4): started {[k for _, k, _ in started]}")
for k in ("d0p0", "d0p1", "d0p2", "d0p3"):
    with p13._lock:
        if (p13.jobs.get(k) or {}).get("status") == "running":
            p13.jobs[k]["status"] = "done"
    wait_started(len(started) + 1, secs=4)
check(wait_started(6) == 6 and sorted(k for _, k, _ in started) == [f"d0p{i}" for i in range(6)],
      f"every sheet is read in the end, both opens' ({len(started)} started)")

# the second critic's N1 (WRONG): a reset while sheets wait stops the queue; nothing more is read on the closed project
settle()
jid14 = "pick14"
seed(jid14, pages=4, detected=(0, 1, 2, 3))
started.clear()
server.cutfill_open(jid14, server.CutfillOpenBody(page=0, pages=[0, 1, 2, 3]))
p14 = server._cutfill.get_project(f"job_{jid14}")
wait_started(2)
server._cutfill_forget(jid14)
with p14._lock:
    for k in ("d0p0", "d0p1"):
        p14.jobs[k]["status"] = "done"
time.sleep(4.0)
check(len(started) == 2 and not any((j or {}).get("host_wait") for j in p14.jobs.values())
      and f"job_{jid14}" not in server._CF_QUEUES,
      f"after the reset the two waiting sheets are never read on the closed project, and nothing says they wait: started {started}")

# the second critic's N2: the sheet the project shows is the one opened when the sheet on screen takes no part (it
# went to the lowest page), and an added sheet does not take over the scale of the sheet shown
settle()
HOLD["on"] = False
jid15 = "pick15"
d15 = seed(jid15, pages=3, detected=(0, 1, 2))
for i, fpi in ((0, 100.0), (2, 50.0)):
    (d15 / f"p{i}_result.json").write_text(json.dumps({"page": {"width_pt": 612, "height_pt": 792}, "chains": [], "styles": [],
                                                       "engine_sig": engine_sig(), "scale": {"ft_per_paper_inch": fpi}}))
server.cutfill_open(jid15, server.CutfillOpenBody(page=2, pages=[2]))
p15 = server._cutfill.get_project(f"job_{jid15}")
s15 = p15.data["settings"]
check(s15.get("scale_ft_per_in") == 50.0 and s15.get("scale_page") == "d0p2", f"the grading sheet sets the scale: {s15.get('scale_ft_per_in')}")
r15 = server.cutfill_open(jid15, server.CutfillOpenBody(page=1, pages=[0, 2]))
check(r15["page"] == 2 and s15.get("scale_ft_per_in") == 50.0 and s15.get("scale_page") == "d0p2" and r15["joined"] == [0],
      f"'Sheets…' from a page not in the job opens on the sheet the project shows (it was the lowest page), and the added "
      f"sheet's 1\" = 100 ft does not replace 50: page {r15['page']}, scale {s15.get('scale_ft_per_in')}, joined {r15['joined']}")
r15b = server.cutfill_open(jid15, server.CutfillOpenBody(page=0, pages=[0, 2]))
check(r15b["page"] == 0 and s15.get("display_page") == "d0p0" and s15.get("scale_ft_per_in") == 100.0
      and s15.get("scale_page") == "d0p0",
      f"opened from the added sheet itself, the engine shows it, and ITS 1\" = 100 ft is in force (the third critic's w1: the "
      f"50 of another sheet was kept on this sheet's frame, volumes 4x low): shown {s15.get('display_page')}, "
      f"scale {s15.get('scale_ft_per_in')} of {s15.get('scale_page')}")

# ---- 18. the third critic's w1: the scale in force is always the sheet shown's -----------------------------------------
def scaled(d, page, fpi):
    (d / f"p{page}_result.json").write_text(json.dumps({"page": {"width_pt": 612, "height_pt": 792}, "chains": [], "styles": [],
                                                        "engine_sig": engine_sig(), "scale": {"ft_per_paper_inch": fpi}}))


settle()
# S11, the common way to add a sheet: the job set up on page 1 (1" = 40 ft); Tools > Cut / fill on page 2 (1" = 100 ft,
# it joins as none); then "Sheets…" with page 2 ticked (the page sends the sheets in the job too)
jid18 = "pick18"
d18 = seed(jid18, pages=2, detected=(0, 1))
scaled(d18, 0, 40.0)
scaled(d18, 1, 100.0)
server.cutfill_open(jid18, server.CutfillOpenBody(page=0, pages=[0]))
server.cutfill_open(jid18, server.CutfillOpenBody(page=1))
p18 = server._cutfill.get_project(f"job_{jid18}")
s18 = p18.data["settings"]
check(roles(jid18) == {"d0p0": "both", "d0p1": "none"} and s18["scale_ft_per_in"] == 40.0,
      f"set up: page 1 at 40 ft, page 2 opened plainly joins as none: {roles(jid18)}, {s18['scale_ft_per_in']}")
r18 = server.cutfill_open(jid18, server.CutfillOpenBody(page=1, pages=[1, 0]))
check(r18["page"] == 1 and s18["display_page"] == "d0p1" and s18["scale_ft_per_in"] == 100.0 and s18["scale_page"] == "d0p1",
      f"the engine shows page 2 and page 2's 1\" = 100 ft is in force (it kept page 1's 40 on page 2's frame: 6.25x low): "
      f"shown {s18['display_page']}, scale {s18['scale_ft_per_in']} of {s18['scale_page']}")
# the third critic's w2: the sheet it opened on was the one added
check(r18["added"] == [1] and r18["joined"] == [], f"the open says it added page 2 (it said 'added 0 sheets'): added {r18['added']}")
# each sheet handed over answers its review scale LIVE (the engine's check asks the host each time: placement
# .review_scale); nothing is copied onto the sheet - a copy went stale (critic 7, WRONG-1)
from cutfill import placement as cfpl  # noqa: E402

def rvft(p, k):
    return (cfpl.review_scale(p, k) or {}).get("ft")


check(cfpl.REVIEW_SCALE is server._cf_review_scale_live
      and rvft(p18, "d0p1") == 100.0 and rvft(p18, "d0p0") == 40.0
      and "review_scale" not in p18.page_settings("d0p0") and "review_scale" not in p18.page_settings("d0p1"),
      f"the sheet opened on and the other ticked sheet each answer their review scale, asked now, nothing kept: "
      f"{rvft(p18, 'd0p1')}, {rvft(p18, 'd0p0')}")
# the reviewer's pick moves it at once (no open in between), with its time; a pick retired gives the detection's back
(d18 / "p0_scale.json").write_text(json.dumps({"ft_per_paper_inch": 30.0, "text": "1\" = 30'", "set_by": server._BY_REVIEWER,
                                               "set_at": "2026-09-28T22:00:00"}))
rv_pick = cfpl.review_scale(p18, "d0p0")
(d18 / "p0_scale.json").unlink()
rv_back = cfpl.review_scale(p18, "d0p0")
check(rv_pick["ft"] == 30.0 and rv_pick["reviewer"] and rv_pick["at"] == time.mktime(time.strptime("2026-09-28T22:00:00", "%Y-%m-%dT%H:%M:%S"))
      and rv_back == {"ft": 40.0, "reviewer": False, "at": None, "writable": True},
      f"a pick made in review is answered at once, with its time, and its retirement too: {rv_pick}, {rv_back}")

# zones drawn on the sheet shown keep it shown: a sheet opened from the screen then says what it reads and leaves the
# scale alone, on the picker's open and on the plain one (which put the opened sheet's scale on the pinned frame)
jid19 = "pick19"
d19 = seed(jid19, pages=2, detected=(0, 1))
scaled(d19, 0, 40.0)
scaled(d19, 1, 100.0)
server.cutfill_open(jid19, server.CutfillOpenBody(page=0, pages=[0]))
p19 = server._cutfill.get_project(f"job_{jid19}")
s19 = p19.data["settings"]
s19["zones"] = [{"page": "d0p0", "name": "Island", "pts": [[0, 0], [100, 0], [100, 100]]}]
p19.save()
r19 = server.cutfill_open(jid19, server.CutfillOpenBody(page=1, pages=[1, 0]))
check(r19["page"] == 1 and s19["display_page"] == "d0p0" and s19["scale_ft_per_in"] == 40.0 and s19["scale_page"] == "d0p0",
      f"zones on page 1 keep it shown, and page 2's 100 ft is not put on page 1's frame: shown {s19['display_page']}, "
      f"scale {s19['scale_ft_per_in']} of {s19['scale_page']}")
server.cutfill_open(jid19, server.CutfillOpenBody(page=1))
check(roles(jid19)["d0p1"] == "both" and s19["display_page"] == "d0p0" and s19["scale_ft_per_in"] == 40.0,
      f"the plain open from page 2 (it takes part now) leaves it too (it put 100 on page 1's frame): {s19['scale_ft_per_in']}")

# ---- 19. the third critic's m4: "Sheets…" reads a stale sheet already in the job; a current one is not read again ------
from cutfill.project import INTERP, READER  # noqa: E402

settle()
jid20 = "pick20"
seed(jid20, pages=2, detected=(0, 1))
server.cutfill_open(jid20, server.CutfillOpenBody(page=0, pages=[0, 1]))
settle()
p20 = server._cutfill.get_project(f"job_{jid20}")
os.makedirs(os.path.dirname(p20.analysis_path("d0p0")), exist_ok=True)
# (the seeded detections carry no lines, so the engine reads these sheets alone: lines_from "engine")
Path(p20.analysis_path("d0p0")).write_text(json.dumps({"reader": READER, "interp": INTERP, "lines_from": "engine", "review": ""}))
Path(p20.analysis_path("d0p1")).write_text(json.dumps({"reader": "older", "interp": "older", "lines_from": "engine", "review": ""}))
check(not p20.analysis_stale("d0p0") and p20.analysis_stale("d0p1"), "set up: page 1's reading is current, page 2's is stale")
started.clear()
server.cutfill_open(jid20, server.CutfillOpenBody(page=0, pages=[0, 1]))
wait_started(1)
time.sleep(0.3)
check([k for _, k, _ in started] == ["d0p1"],
      f"the stale sheet in the job is read again (only new sheets were), the current one is not: {started}")

# ---- 20. the third critic's r1: a sheet detected again since the engine read it is read again, in full, and said --------
settle()
jid21 = "pick21"
d21 = seed(jid21, pages=1, detected=(0,))
server.cutfill_open(jid21, server.CutfillOpenBody(page=0))
p21 = server._cutfill.get_project(f"job_{jid21}")
rp = p21.reading_path("d0p0")
os.makedirs(os.path.dirname(rp), exist_ok=True)
Path(rp).write_bytes(b"a reading")
t_old = time.time() - 100
os.utime(rp, (t_old, t_old))                       # read 100 s ago ...
os.utime(d21 / "p0_result.json", None)             # ... and detected again now
started.clear()
started_full.clear()
r21 = server.cutfill_open(jid21, server.CutfillOpenBody(page=0))
wait_started(1)
check(r21["reread"] == [0] and started_full == [(f"job_{jid21}", "d0p0", True)],
      f"the plain open reads it again from its new lines, and says so (it said nothing; an Update read the old lines): "
      f"reread {r21['reread']}, started {started_full}")
# the fifth critic's r-a: the full read cached its reading, then its interpretation failed (no new analysis): the old
# detection's lines still stand, so the mark stays and the next open reads again, and says so
Path(rp).write_bytes(b"read, not interpreted")
started_full.clear()
r21a = server.cutfill_open(jid21, server.CutfillOpenBody(page=0))
wait_started(len(started) + 1, secs=4)
check(r21a["reread"] == [0] and (f"job_{jid21}", "d0p0", True) in started_full,
      f"a re-read whose interpretation failed is made again (the mark was cleared by the new reading alone): {r21a['reread']}")
settle()
read_done(p21, "d0p0", b"read again")              # the full read made and interpreted
started.clear()
r21b = server.cutfill_open(jid21, server.CutfillOpenBody(page=0))
time.sleep(0.3)
check(r21b["reread"] == [] and not started, f"once read again, nothing more: {r21b['reread']}, {started}")
# the fifth critic's r-b: a read that ended with a full rerun still queued counts as running: the mark stays
os.utime(d21 / "p0_result.json", None)             # detected again
r21x = server.cutfill_open(jid21, server.CutfillOpenBody(page=0))
settle()
read_done(p21, "d0p0", b"the OLD file's read, ending")
with p21._lock:
    p21.jobs["d0p0"] = {"status": "done", "progress": "", "full": False, "rerun": "full", "error": ""}
server.cutfill_open(jid21, server.CutfillOpenBody(page=0))
check(r21x["reread"] == [0] and p21.page_settings("d0p0").get("core_redo_at"),
      "a read that ended with its full rerun still to come does not clear the mark (the moment between the two did)")
with p21._lock:
    p21.jobs["d0p0"]["rerun"] = None
read_done(p21, "d0p0", b"the full rerun")
server.cutfill_open(jid21, server.CutfillOpenBody(page=0))
check(not p21.page_settings("d0p0").get("core_redo_at"), "and clears it once the rerun is made")
# a sheet being read when its detection changed is read again in full when that read ends
os.utime(rp, (t_old, t_old))
with p21._lock:
    p21.jobs["d0p0"] = {"status": "running", "progress": "x", "full": False, "rerun": None, "error": ""}
server.cutfill_open(jid21, server.CutfillOpenBody(page=0))
check(p21.jobs["d0p0"].get("rerun") == "full", f"a read already running is read again in full after it: {p21.jobs['d0p0']}")
settle()
# a detection written WHILE the engine read the old file: the reading ends newer than the new file, so the times alone
# say nothing; the file is not the one handed over at the last open, and that says it
read_done(p21, "d0p0", b"read")
server.cutfill_open(jid21, server.CutfillOpenBody(page=0))          # hands the file over (recorded)
d21_sig = p21.page_settings("d0p0").get("core_sig")
(d21 / "p0_result.json").write_text(json.dumps({"page": {"width_pt": 612, "height_pt": 792}, "chains": [], "styles": [],
                                                "engine_sig": engine_sig(), "note": "detected again"}))
t_new = time.time() + 5
os.utime(rp, (t_new, t_new))                       # the read of the OLD file ended after the new detection
started_full.clear()
r21c = server.cutfill_open(jid21, server.CutfillOpenBody(page=0))
wait_started(len(started) + 1, secs=4)
check(d21_sig and r21c["reread"] == [0] and (f"job_{jid21}", "d0p0", True) in started_full,
      f"a detection made while the old one was being read is still caught (the file changed since the last open): "
      f"{r21c['reread']}, {started_full}")
settle()

# ---- 21. the third critic's r2: two reads at a time in the SERVER; a reset project's reads count until they end -------
settle()
HOLD["on"] = True
jid22 = "pick22"
seed(jid22, pages=2, detected=(0, 1))
started.clear()
server.cutfill_open(jid22, server.CutfillOpenBody(page=0, pages=[0, 1]))
wait_started(2)
p22 = server._cutfill.get_project(f"job_{jid22}")
server._cutfill_forget(jid22)                      # a reset: the project closes, its two reads run on
jid23 = "pick23"
seed(jid23, pages=2, detected=(0, 1))
started.clear()
server.cutfill_open(jid23, server.CutfillOpenBody(page=0, pages=[0, 1]))
p23 = server._cutfill.get_project(f"job_{jid23}")
time.sleep(2.0)
check(not started and all((p23.jobs.get(k) or {}).get("host_wait") for k in ("d0p0", "d0p1")),
      f"after a reset the closed project's two reads still count: the new project's wait (4 ran at once): {started}")
with p22._lock:
    for j in p22.jobs.values():
        j["status"] = "done"
check(wait_started(2) == 2, f"when they end, the new project's reads start: {started}")
settle()
HOLD["on"] = False

# ---- 21b. the fourth critic's W1: the scale in force and the sheet shown never part ---------------------------------------
settle()
# W1a: sheets 1 and 2 take part, sheet 2 shown at 100 ft; Tools > Cut / fill on page 3 (new to the job): the sheet shown
# stays sheet 2 (the settings named page 3, role none, and the engine showed the LOWEST page taking part, drawn at 40)
jid25 = "pick25"
d25 = seed(jid25, pages=3, detected=(0, 1, 2))
scaled(d25, 0, 40.0)
scaled(d25, 1, 100.0)
server.cutfill_open(jid25, server.CutfillOpenBody(page=1, pages=[0, 1]))
p25 = server._cutfill.get_project(f"job_{jid25}")
s25 = p25.data["settings"]
check(server._cf_display_for(s25, p25.data["pages"]) == "d0p1" and s25["scale_ft_per_in"] == 100.0,
      f"set up: sheet 2 shown at 100 ft: {s25['display_page']}, {s25['scale_ft_per_in']}")
r25 = server.cutfill_open(jid25, server.CutfillOpenBody(page=2))
check(roles(jid25)["d0p2"] == "none" and s25["display_page"] == "d0p1"
      and server._cf_display_for(s25, p25.data["pages"]) == "d0p1" and s25["scale_ft_per_in"] == 100.0,
      f"a page new to the job joins as none and the sheet shown stays sheet 2 with its 100 ft (it became the lowest page, "
      f"drawn at 40, under 100 ft: 6.25x high): shown {server._cf_display_for(s25, p25.data['pages'])}")
# W1b: sheet 1 shown at 40 ft; "Sheets…" from sheet 2, whose review read no scale: sheet 2 becomes the sheet shown and
# the 40 read on sheet 1 stays as it was (nothing is dropped: critic 5, WRONG-1). While sheet 2 prints no scale the
# engine could read, the engine's compute refuses it (it used it silently: critic 4, W1b)
jid26 = "pick26"
d26 = seed(jid26, pages=2, detected=(0, 1))
scaled(d26, 0, 40.0)
server.cutfill_open(jid26, server.CutfillOpenBody(page=0, pages=[0]))
p26 = server._cutfill.get_project(f"job_{jid26}")
s26 = p26.data["settings"]
r26 = server.cutfill_open(jid26, server.CutfillOpenBody(page=1, pages=[1, 0]))
Path(p26.analysis_path("d0p1")).write_text(json.dumps({"labels": [], "width": 612, "height": 792}))   # read: no scale printed
res26 = server._cutfill.compute(f"job_{jid26}")
check(s26["display_page"] == "d0p1" and s26["scale_ft_per_in"] == 40.0 and s26["scale_page"] == "d0p0"
      and "scale_note" not in r26 and "prints no scale the engine could read" in (res26.get("error") or ""),
      f"the sheet shown moves to sheet 2, which hands no scale: the 40 read on sheet 1 is kept with its sheet, and the "
      f"compute refuses it until sheet 2's scale is confirmed: {res26.get('error')}")
# the fourth critic's r6: a sheet the operator made "both" in the engine, never opened from the host, stays "both"
jid27 = "pick27"
seed(jid27, pages=2, detected=(0, 1))
server.cutfill_open(jid27, server.CutfillOpenBody(page=0, pages=[0]))
p27 = server._cutfill.get_project(f"job_{jid27}")
p27.page_settings("d0p1")["role"] = "both"                 # set in the engine's Sheets card
p27.save()
r27 = server.cutfill_open(jid27, server.CutfillOpenBody(page=1))
check(roles(jid27)["d0p1"] == "both" and r27["in_job"] == 2,
      f"the first host open of a sheet the engine made 'both' keeps it (it was set to none, silently): {roles(jid27)}")
settle()

# ---- 21c. the fourth critic's r1: a full re-read cut short (a restart, a failed read) is made again, and said ----------
jid28 = "pick28"
d28 = seed(jid28, pages=1, detected=(0,))
server.cutfill_open(jid28, server.CutfillOpenBody(page=0))
p28 = server._cutfill.get_project(f"job_{jid28}")
rp28 = p28.reading_path("d0p0")
os.makedirs(os.path.dirname(rp28), exist_ok=True)
Path(rp28).write_bytes(b"a reading")
t_old28 = time.time() - 100
os.utime(rp28, (t_old28, t_old28))
os.utime(d28 / "p0_result.json", None)                       # detected again
started_full.clear()
r28 = server.cutfill_open(jid28, server.CutfillOpenBody(page=0))
wait_started(len(started) + 1, secs=4)
mark = p28.page_settings("d0p0").get("core_redo_at")
check(r28["reread"] == [0] and mark, f"set up: the re-read is queued and marked: {r28['reread']}, {mark}")
# the full read drops the old reading, then the server stops before it writes a new one
os.remove(rp28)
p28.jobs.clear()
started_full.clear()
r28b = server.cutfill_open(jid28, server.CutfillOpenBody(page=0))
wait_started(len(started) + 1, secs=4)
check(r28b["reread"] == [0] and (f"job_{jid28}", "d0p0", True) in started_full,
      f"a re-read cut short is made again on the next open, and said (nothing was, and the old lines stood): "
      f"{r28b['reread']}, {started_full}")
# the full read made: a reading newer than the mark, nothing running -> the mark goes, nothing more is read
settle()
read_done(p28, "d0p0", b"read again")
r28c = server.cutfill_open(jid28, server.CutfillOpenBody(page=0))
check(r28c["reread"] == [] and not p28.page_settings("d0p0").get("core_redo_at"),
      f"once the full read is made the mark is cleared and nothing is said: {r28c['reread']}")
settle()

# ---- 21d. the fourth critic's r5: a reset project's reads the ENGINE started still count ------------------------------
HOLD["on"] = True
jid29 = "pick29"
seed(jid29, pages=2, detected=(0, 1))
server.cutfill_open(jid29, server.CutfillOpenBody(page=0))          # a plain open: no host queue
p29 = server._cutfill.get_project(f"job_{jid29}")
with p29._lock:                                                      # the engine's own Analyze on two sheets
    for k in ("d0p0", "d0p1"):
        p29.jobs[k] = {"status": "running", "progress": "engine", "full": False, "rerun": None, "error": ""}
server._cutfill_forget(jid29)                                        # a reset
jid30 = "pick30"
seed(jid30, pages=2, detected=(0, 1))
started.clear()
server.cutfill_open(jid30, server.CutfillOpenBody(page=0, pages=[0, 1]))
time.sleep(2.0)
check(not started, f"after a reset, the reads the engine itself started still count: the new project's wait (4 ran): {started}")
with p29._lock:
    for j in p29.jobs.values():
        j["status"] = "done"
check(wait_started(2) == 2, f"and start when those end: {started}")
settle()
HOLD["on"] = False
check("_CF_READERS_LOCK" in inspect.getsource(server._cf_busy) and "_CF_READERS_LOCK" in inspect.getsource(server._cf_reader),
      "the readers' set is copied and added to under its own lock (m8)")

# ---- 21e. the fourth critic's m7: the report header takes the triage of THIS copy of the PDF only ----------------------
jd31 = server._job_dir(jid25)
(jd31 / "triage.json").write_text(json.dumps({"version": server.TRIAGE_VERSION, "mtime": 1.0, "pages": [{"page": 0, "sheet_no": "C9.9"}]}))
check(server._triage_sheet_no(jd31, 0, 2.0) == (False, None) and server._triage_sheet_no(jd31, 0, 1.0) == (True, "C9.9"),
      "a triage made for another copy of the PDF is not used for the header")

# ---- 22. the third critic's m3: a detection read while it is written is never kept as its answer ------------------------
jid24 = "pick24"
d24 = seed(jid24, pages=1, detected=(0,))
f24 = d24 / "p0_result.json"
f24.write_text('{"page": {"width_pt": 612, "hei')
got1 = server._cf_detected(jid24, 0)
cached = str(server.result_path(server._job_dir(jid24), 0)) in server._CF_SIG_CACHE
f24.write_text(json.dumps({"page": {"width_pt": 612, "height_pt": 792}, "chains": [], "styles": [], "engine_sig": engine_sig()}))
got2 = server._cf_detected(jid24, 0)
check(got1 == (False, True) and not cached and got2 == (True, False),
      f"a half-written file reads as not current and is not kept; the whole file then reads as current: {got1}, {cached}, {got2}")

# ---- 11. W2: the sheet numbers a picker can trust --------------------------------------------------------------
# a number every page prints ("SHEET 3 OF 24") is nobody's sheet number
jid11 = "pick11"
seed(jid11, pages=3, detected=(), texts={0: "24", 1: "24", 2: "24"})
check([x["label"] for x in server.cutfill_sheets(jid11, labels=1)["pages"]] == ["", "", ""],
      "a number read on every page is not a sheet number: the pages are named by page only")
# where the engine reads none, the host's own reading when shaped like a sheet number (not a lot label)
jid12 = "pick12"
d12 = seed(jid12, pages=3, detected=(), texts={0: "", 1: "", 2: "3.2"})
(d12 / "triage.json").write_text(json.dumps({"version": server.TRIAGE_VERSION, "mtime": server.JOBS[jid12]["mtime"], "pages": [
    {"page": 0, "sheet_no": "C4.1"}, {"page": 1, "sheet_no": "LOT910"}, {"page": 2, "sheet_no": "C9.9"}]}))
check([x["label"] for x in server.cutfill_sheets(jid12, labels=1)["pages"]] == ["C4.1", "", "3.2"],
      "the host's reading fills a gap when shaped like a sheet number (C4.1), never a lot label (LOT910), and the "
      "engine's reading comes first (3.2)")
# the second critic's W2: the engine's own reading must be shaped like a sheet number too
jid16 = "pick16"
seed(jid16, pages=4, detected=(), texts={0: "OF 62", 1: "LOT 12", 2: "1", 3: "C-04"})
check([x["label"] for x in server.cutfill_sheets(jid16, labels=1)["pages"]] == ["", "", "", "C-04"],
      "the engine's reading counts only when shaped like a sheet number: 'OF 62', 'LOT 12' and a bare '1' do not")
# the second critic's N3: the host's reading written after a first list is used by the next one (it was cached out)
jid17 = "pick17"
d17 = seed(jid17, pages=2, detected=(), texts={0: "", 1: ""})
first = [x["label"] for x in server.cutfill_sheets(jid17, labels=1)["pages"]]
(d17 / "triage.json").write_text(json.dumps({"version": server.TRIAGE_VERSION, "mtime": server.JOBS[jid17]["mtime"], "pages": [
    {"page": 0, "sheet_no": "C4.1"}, {"page": 1, "sheet_no": "C4.2"}]}))
second = [x["label"] for x in server.cutfill_sheets(jid17, labels=1)["pages"]]
check(first == ["", ""] and second == ["C4.1", "C4.2"],
      f"the host's reading, written after the first list, is in the next one: {first} then {second}")

# ---- the seventh critic's WRONG-1 through the host: the review corrected, or the sheet detected again, after the
#      hand-over. b888800 kept a copy of each sheet's review taken at the open; a plain open from 3.1 refreshed 3.1 only,
#      so 3.0's old 50 confirmed the 50 in force and Compute ran 56 % high, silently (S1), with no human step too (S2)
settle()
HOLD["on"] = False
jid77 = "pick77"
d77 = seed(jid77, pages=2, detected=(0, 1))
scaled(d77, 0, 50.0)                # 3.0: its detection read 50 (a misread)
scaled(d77, 1, 50.0)                # 3.1: 50
server.cutfill_open(jid77, server.CutfillOpenBody(page=1, pages=[0, 1]))
p77 = server._cutfill.get_project(f"job_{jid77}")
s77 = p77.data["settings"]
with p77._lock:                     # a limit drawn on 3.0 keeps 3.0 the sheet shown
    s77["display_page"] = "d0p0"
    s77["region"] = [[100, 100], [500, 100], [500, 500], [100, 500]]
    p77.page_settings("d0p0")["review_scale"] = 50.0          # b888800's copy, as a project from then holds it
    p77.save()
ok77 = cfpl.foreign_scale(p77) is None
(d77 / "p0_scale.json").write_text(json.dumps({"ft_per_paper_inch": 40.0, "text": "1\" = 40'", "set_by": server._BY_REVIEWER,
                                               "set_at": "2026-09-28T22:00:00"}))
server.cutfill_open(jid77, server.CutfillOpenBody(page=1))
w_s1 = cfpl.foreign_scale(p77) or ""
c_s1 = server._cutfill.compute(f"job_{jid77}")
check(ok77 and s77["scale_ft_per_in"] == 50.0 and s77["scale_page"] == "d0p1" and s77["display_page"] == "d0p0"
      and 'review says 1" = 40 ft' in w_s1 and c_s1.get("error") == w_s1
      and "review_scale" not in p77.page_settings("d0p0"),
      f"S1: the reviewer corrects 3.0 to 40 and Cut / fill opens again from 3.1: Compute on 3.0 is refused, said (it ran "
      f"at 50, silently); the old copy is gone: {c_s1.get('error')}")
(d77 / "p0_scale.json").unlink()
scaled(d77, 0, 40.0)                # 3.0 detected again: it reads 40 now (no human step)
st77 = os.stat(d77 / "p0_result.json")
os.utime(d77 / "p0_result.json", ns=(st77.st_atime_ns, st77.st_mtime_ns + 5_000_000))   # a new version (same size here)
server.cutfill_open(jid77, server.CutfillOpenBody(page=1))
w_s2 = cfpl.foreign_scale(p77) or ""
check('review says 1" = 40 ft' in w_s2 and server._cutfill.compute(f"job_{jid77}").get("error") == w_s2,
      f"S2: 3.0 detected again reads 40: Compute on 3.0 is refused, said (it ran at 50, silently): {w_s2}")

# ---- the eighth critic through the host: the engine asked without its page, the approve that would export rows the
#      engine does not stand behind, a store copied, the sheet shown held by volumes that stand ----------------------------
settle()
HOLD["on"] = False
jid88 = "pick88"
pid88 = f"job_{jid88}"
d88 = seed(jid88, pages=2, detected=(0, 1))
scaled(d88, 0, 50.0)
scaled(d88, 1, 50.0)
server.cutfill_open(jid88, server.CutfillOpenBody(page=0, pages=[0, 1]))
settle()
p88 = server._cutfill.get_project(pid88)
s88 = p88.data["settings"]
# r-5: a sheet whose detection path names another store (a project copied from it) is read in this one
other88 = Path(tempfile.mkdtemp(prefix="cfother_")) / jid88
other88.mkdir(parents=True)
(other88 / "p1_result.json").write_text(json.dumps({"scale": {"ft_per_paper_inch": 99.0}}))
p88.page_settings("d0p1")["core_result"] = str(other88 / "p1_result.json")
rv_copy = cfpl.review_scale(p88, "d0p1")
p88.page_settings("d0p1")["core_result"] = str(d88 / "p1_result.json")
check((rv_copy or {}).get("ft") == 50.0,
      f"r-5: a sheet whose detection names another store is read in this one (the review went unasked there): {rv_copy}")
# a stored result on 3.0 at 50 with a limit and a strip, as a compute leaves it (its fingerprint taken)
with p88._lock:
    s88.update({"display_page": "d0p0", "region": [[100, 100], [500, 100], [500, 500], [100, 500]], "limit": {"source": "drawn"},
                "takeoff": {"strip_in": 6}})
    fake88 = {"display_page": "d0p0", "ft_per_in": 50.0, "warnings": [], "computed_at": 4242.0,
              "totals": {"cut_cy": 0.0, "fill_cy": 900.2, "net_cy": 900.2, "area_sf": 24305.6}, "inputs": cfpl.inputs_sig(p88)}


def store88(res):
    Path(p88.dir, "result.json").write_text(json.dumps(res))
    server._cutfill._results[pid88] = res


store88(fake88)
seed("pick89", pages=1, detected=(0,))            # a job whose cut / fill was never opened
st88 = server._cf_state(jid88)
st88r = server._cf_state(jid88, rows=True)
rows88 = [(r["label"], r["qty"], r["unit"]) for r in (st88r.get("summary") or {}).get("rows") or []]
check(st88 == {"project": True, "why": None, "computed_at": 4242.0, "rows_page": None}
      and ("Site Fill", 900.0, "CY") in rows88 and any(r[0] == "Strip Topsoil" for r in rows88)
      and st88r["summary"]["computed_at"] == 4242.0 and server._cf_state("pick89")["project"] is False,
      f"the engine's word without its page: the result stands, its time, the rows now (a job with no cut / fill says so): {st88}, {rows88}")
# the approve guard: the engine's rows pass; rows at other numbers are refused; rows with the labels and no cut / fill
# behind them are refused (a reset swept it: critic 9, r-7 - unmarked here; rows the page marks as the old Earthwork
# calculator's pass as they are: tests/test_legacy_earthwork.py - they were
# exported as "typed by hand"); a row the Areas tool made is the operator's, whatever its name; rows on a page that does
# not hold the job's rows are refused (critic 9, WRONG-3: the same rows passed on two pages, the dirt exported twice)
items88 = [{"label": l, "qty": q, "unit": u} for l, q, u in rows88 if q > 0] + [{"label": "Paving", "qty": 3, "unit": "EA"}]
bad88 = [dict(it, qty=it["qty"] + 1) if it["label"] == "Site Fill" else it for it in items88]
area88 = [{"label": "Clear & Grub", "qty": 5000.0, "unit": "SF", "_area": True}, {"label": "Paving", "qty": 3, "unit": "EA"}]
none89 = server._cf_rows_refused("pick89", 0, items88) or ""
server.cutfill_rows(jid88, 0, server.CutfillRowsBody(added=True, pid=pid88))
other_page88 = server._cf_rows_refused(jid88, 1, items88) or ""
own_page88 = server._cf_rows_refused(jid88, 0, items88)
area_other88 = server._cf_rows_refused(jid88, 1, area88)
server.cutfill_rows(jid88, 0, server.CutfillRowsBody(added=False, pid=pid88))
check(server._cf_rows_refused(jid88, 0, items88) is None and "not the engine's numbers now" in (server._cf_rows_refused(jid88, 0, bad88) or "")
      and "no cut / fill behind them" in none89 and server._cf_rows_refused(jid88, 0, [{"label": "Paving", "qty": 3}]) is None
      and "belong to page 1's takeoff" in other_page88 and own_page88 is None and area_other88 is None
      and "host_rows" not in s88,
      f"the approve lets the engine's own rows through on their page, refuses rows at other numbers, rows with no cut / "
      f"fill behind them and rows on another page, and leaves an area row alone: {none89} / {other_page88}")
# WRONG-1 through the approve: the reviewer corrects 3.0 to 1" = 40' in an approve that carries the rows at 50 - the
# correction is recorded, nothing is exported, and it is said (the rows at 50 were exported, 56 % high)
code88 = det88 = None
try:
    server.approve(jid88, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid88, 0), labels={}, extra_items=items88,
                                                scale=server.ApproveScale(ft_per_paper_inch=40.0, text="1\" = 40'", picked_by_reviewer=True, how="typed")))
except server.HTTPException as e:
    code88, det88 = e.status_code, e.detail
pick88 = server._stored_scale_pick(jid88, 0) or {}
st_after = server._cf_state(jid88)
check(code88 == 409 and "review says 1\" = 40 ft" in str((det88 or {}).get("cutfill")) and pick88.get("ft_per_paper_inch") == 40.0
      and "review says 1\" = 40 ft" in str(st_after.get("why")) and not (d88 / "p0_elements.json").exists(),
      f"WRONG-1: an approve that corrects the scale under rows at the old one exports nothing and says why; the correction "
      f"is kept, and the engine's word says the rows stand no more: {code88} {det88}")
# WRONG-2 / r-6 / m8: the same 40 approved again is no new decision - the pick keeps its time (every approve rewrote it,
# and the cut / fill read each re-approve as a correction made after the operator's typed scale: 4 approves, 4
# refusals) and no second ledger label is written; the time is exact, epoch seconds, and the engine's review reads it
n_led88 = sum(1 for ev, _f in server.ledger.iter_events() if ev.get("event") == "scale_set" and ev.get("job") == jid88)
code88b = None
try:
    server.approve(jid88, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid88, 0), labels={}, extra_items=items88,
                                                scale=server.ApproveScale(ft_per_paper_inch=40.0, text="1\" = 40'", picked_by_reviewer=True, how="typed")))
except server.HTTPException as e:
    code88b = e.status_code
pick88b = server._stored_scale_pick(jid88, 0) or {}
n_led88b = sum(1 for ev, _f in server.ledger.iter_events() if ev.get("event") == "scale_set" and ev.get("job") == jid88)
live88 = server._cf_review_scale_live(p88, "d0p0") or {}
check(code88b == 409 and isinstance(pick88.get("at"), float) and pick88b.get("at") == pick88.get("at")
      and pick88b.get("set_at") == pick88.get("set_at") and n_led88b == n_led88 == 1
      and abs(float(live88.get("at") or 0) - pick88["at"]) < 1e-6 and live88.get("reviewer") is True,
      f"WRONG-2 / r-6 / m8: the same pick approved again keeps its time and writes no second label; the time is exact: "
      f"{pick88.get('at')} / {pick88b.get('at')} / ledger {n_led88} -> {n_led88b} / live {live88.get('at')}")
# r-2: volumes that stand hold the sheet shown - a visit from 3.1 to look does not move it (the rows left 3.0's takeoff
# and a false STALE stuck); volumes that stand no more do not hold it
(d88 / "p0_scale.json").unlink()
with p88._lock:
    s88.update({"region": None, "limit": None})
    fake88["inputs"] = cfpl.inputs_sig(p88)
store88(fake88)
stands = server._cf_state(jid88)["why"] is None
server.cutfill_open(jid88, server.CutfillOpenBody(page=1))
held = s88["display_page"]
store88(dict(fake88, display_page="d0p0", ft_per_in=40.0))
server.cutfill_open(jid88, server.CutfillOpenBody(page=1))
moved88 = s88["display_page"]
check(stands and held == "d0p0" and moved88 == "d0p1",
      f"r-2: an open from another sheet leaves the sheet shown under volumes that stand, and moves it from under stale "
      f"ones: {held}, {moved88}")
server._cutfill._results.pop(pid88, None)

# ---- the tenth critic: with no page on record the first approve that carries the job's rows records its page (the same
#      rows passed on two pages); a detection read mid-write answers with its last whole reading (the result went stale
#      for a moment, and a page load took the rows out) --------------------------------------------------------------------
settle()
jid90 = "pick90"
pid90 = f"job_{jid90}"
d90 = seed(jid90, pages=2, detected=(0, 1))
scaled(d90, 0, 50.0)
scaled(d90, 1, 50.0)
server.cutfill_open(jid90, server.CutfillOpenBody(page=0, pages=[0, 1]))
settle()
p90 = server._cutfill.get_project(pid90)
s90 = p90.data["settings"]
with p90._lock:
    s90.update({"display_page": "d0p0", "region": [[100, 100], [500, 100], [500, 500], [100, 500]], "limit": {"source": "drawn"},
                "takeoff": {"strip_in": 6}})
    s90.pop("host_rows", None)
    fake90 = {"display_page": "d0p0", "ft_per_in": 50.0, "warnings": [], "computed_at": 5151.0,
              "totals": {"cut_cy": 0.0, "fill_cy": 900.2, "net_cy": 900.2, "area_sf": 24305.6}, "inputs": cfpl.inputs_sig(p90)}
Path(p90.dir, "result.json").write_text(json.dumps(fake90))
server._cutfill._results[pid90] = fake90
items90 = [{"label": r["label"], "qty": r["qty"], "unit": r["unit"]}
           for r in (server._cf_state(jid90, rows=True).get("summary") or {}).get("rows") or [] if (r.get("qty") or 0) > 0]
code90a = code90b = None
try:
    server.approve(jid90, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 0), labels={}, extra_items=items90))
except server.HTTPException as e:
    code90a = (e.status_code, e.detail)
claimed90 = (s90.get("host_rows") or {}).get("page")
try:
    server.approve(jid90, 1, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 1), labels={}, extra_items=items90))
except server.HTTPException as e:
    code90b = (e.status_code, e.detail)
check(items90 and code90a is None and claimed90 == 0 and code90b and code90b[0] == 409
      and "belong to page 1's takeoff" in str(code90b[1]),
      f"r-1: with no page on record the first approve carrying the rows records its page, and the same rows on another "
      f"page are refused: {claimed90}, {code90b}")
live90 = server._cf_review_scale_live(p90, "d0p1")             # the whole reading, kept
f90 = d90 / "p1_result.json"
whole90 = f90.read_text(encoding="utf-8")
f90.write_text(whole90[: len(whole90) // 2], encoding="utf-8")   # the detector's in-place write, caught half way
live90_mid = server._cf_review_scale_live(p90, "d0p1")
why90_mid = server._cf_state(jid90)["why"]
f90.write_text(whole90, encoding="utf-8")
check((live90 or {}).get("ft") == 50.0 and (live90_mid or {}).get("ft") == 50.0 and why90_mid is None,
      f"r-2: a detection read mid-write answers with its last whole reading, and the result stands: {live90_mid}, {why90_mid}")
# the eleventh critic, r-2: page 1's export (approved above) carries the rows, marked; the rows then move to page 2
# (Remove, Add) and page 2's approve would export them again - refused, naming the page; an Areas row alone passes
exp90 = json.loads((d90 / "p0_elements.json").read_text(encoding="utf-8"))
marked90 = sorted({it.get("label") for it in exp90.get("items") or [] if it.get("src") == "cutfill"})
server.cutfill_rows(jid90, 0, server.CutfillRowsBody(added=False, pid=pid90))
server.cutfill_rows(jid90, 1, server.CutfillRowsBody(added=True, pid=pid90))
code90c = None
try:
    server.approve(jid90, 1, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 1), labels={}, extra_items=items90))
except server.HTTPException as e:
    code90c = (e.status_code, e.detail)
code90d = None
try:
    server.approve(jid90, 1, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 1), labels={}, extra_items=[{"label": "Clear & Grub", "qty": 5000.0, "unit": "SF",
                                                                           "_area": True}]))
except server.HTTPException as e:
    code90d = (e.status_code, e.detail)
check("Site Fill" in marked90 and code90c and code90c[0] == 409 and (code90c[1] or {}).get("export_page") == 0
      and "page 1's approved takeoff still carries" in str((code90c[1] or {}).get("cutfill")) and code90d is None,
      f"critic 11, r-2: another page's approved export still carrying the rows refuses them here, naming it; an Areas "
      f"row named alike does not: {marked90}, {code90c}")
# the eleventh critic, r-1: the pick file is written in place again (a temp + replace failed on Windows under a reader);
# a read caught half way answers with the last whole pick, never "no pick"
server.approve(jid90, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 0), labels={}, extra_items=[{"label": "Test count", "qty": 1, "unit": "EA"}],
                                            scale=server.ApproveScale(ft_per_paper_inch=40.0, text="1\" = 40'", picked_by_reviewer=True, how="typed")))
pk90 = server._stored_scale_pick(jid90, 0) or {}
fpk90 = d90 / "p0_scale.json"
whole_pk90 = fpk90.read_text()
fpk90.write_text(whole_pk90[: len(whole_pk90) // 2])
pk90_mid = server._stored_scale_pick(jid90, 0) or {}
fpk90.write_text(whole_pk90)
check(pk90.get("ft_per_paper_inch") == 40.0 and pk90_mid.get("ft_per_paper_inch") == 40.0
      and not list(d90.glob("p0_scale.json.*")),
      f"critic 11, r-1: the pick is written in place, and a read caught half way keeps the last whole pick: {pk90_mid}")
# the twelfth critic, r-1: going back to the engine's own reading retires the pick with a tombstone written in place
# (an unlink was refused on Windows under a reader, silently, and the old pick stood); read as no pick
n_ret90 = sum(1 for ev, _f in server.ledger.iter_events() if ev.get("event") == "scale_pick_retired" and ev.get("job") == jid90)
eng90 = (server._page_scale(jid90, 0, json.loads((d90 / "p0_result.json").read_text(encoding="utf-8"))) or {})
server.approve(jid90, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 0), labels={}, extra_items=[{"label": "Test count", "qty": 1, "unit": "EA"}],
                                            scale=server.ApproveScale(ft_per_paper_inch=50.0, text="1\" = 50'", picked_by_reviewer=False)))
tomb90 = json.loads((d90 / "p0_scale.json").read_text()) if (d90 / "p0_scale.json").exists() else None
n_ret90b = sum(1 for ev, _f in server.ledger.iter_events() if ev.get("event") == "scale_pick_retired" and ev.get("job") == jid90)
check(server._stored_scale_pick(jid90, 0) is None and (tomb90 or {}).get("retired") is True and n_ret90b == n_ret90 + 1
      and (server._cf_review_scale_live(p90, "d0p0") or {}).get("ft") == 50.0
      and (server._cf_review_scale_live(p90, "d0p0") or {}).get("reviewer") is True,     # going back is a dated decision (critic 14)
      f"critic 12, r-1: a retired pick is a tombstone written in place, read as no pick; the review reads the engine's "
      f"own scale again: {tomb90}, before {eng90.get('ft_per_paper_inch')}")
# the twelfth critic, WRONG-2: an Areas row the operator named "Site Fill" (square feet), exported, is not the job's cut /
# fill rows - it blocked every cut / fill approve for good
server.approve(jid90, 1, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 1), labels={}, extra_items=[{"label": "Site Fill", "qty": 5000.0, "unit": "SF", "_area": True}]))
exp90b = json.loads((d90 / "p1_elements.json").read_text(encoding="utf-8"))
src90b = [(it.get("label"), it.get("src")) for it in exp90b.get("items") or []]
blk90 = server._cf_rows_exported_elsewhere(jid90, 0, items90)
check(("Site Fill", "area") in src90b and (blk90 is None or blk90[0] != 1),
      f"critic 12, WRONG-2: an exported Areas row named 'Site Fill' is marked as the Areas tool's and blocks nothing: "
      f"{src90b}, {blk90}")
# the thirteenth critic (and critic 12's m5): an export from before the marks that holds only the limit's rows counts -
# its topsoil in cubic yards, its fence in linear feet (the topsoil was counted twice); Clear & Grub (square feet) does
# not, whatever made it
f1e = d90 / "p1_elements.json"
keep_e = f1e.read_text(encoding="utf-8")
legacy = json.loads(keep_e)
legacy["items"] = [{"label": "Strip Topsoil", "qty": 517.6, "unit": "CY"}, {"label": "Clear & Grub", "qty": 24305.6, "unit": "SF"}]
f1e.write_text(json.dumps(legacy), encoding="utf-8")
blk_top = server._cf_rows_exported_elsewhere(jid90, 0, items90)
legacy["items"] = [{"label": "Clear & Grub", "qty": 24305.6, "unit": "SF"}]
f1e.write_text(json.dumps(legacy), encoding="utf-8")
blk_cg = server._cf_rows_exported_elsewhere(jid90, 0, items90)
f1e.write_text(keep_e, encoding="utf-8")
# MARKS DECIDE (critic legacy-2, the coordinator's design): an unmarked export is v1.8.5's, its rows the old Earthwork
# calculator's, never the job's cut / fill rows - so neither blocks Cut / fill's own page (a CHECK note says both exist)
check(blk_top is None and blk_cg is None,
      f"critic 12, m5 (changed by the legacy-earthwork design): an unmarked export's rows are the old calculator's and block "
      f"no Cut / fill approve: {blk_top}, {blk_cg}")
# the thirteenth critic, m-c: the same pick on file is not written again - an unwritable file failed every re-approve
# (503); a new number still fails, said. m-b: a tombstone written is the last whole reading at once
import stat as _stat  # noqa: E402
server.approve(jid90, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 0), labels={}, extra_items=[{"label": "Test count", "qty": 1, "unit": "EA"}],
                                            scale=server.ApproveScale(ft_per_paper_inch=40.0, text="1\" = 40'", picked_by_reviewer=True, how="typed")))
fpk = d90 / "p0_scale.json"
os.chmod(fpk, _stat.S_IREAD)
codes_c = []
for want in (40.0, 45.0):
    try:
        server.approve(jid90, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 0), labels={}, extra_items=[{"label": "Test count", "qty": 1, "unit": "EA"}],
                                                    scale=server.ApproveScale(ft_per_paper_inch=want, text=f"1\" = {want:g}'", picked_by_reviewer=True, how="typed")))
        codes_c.append(None)
    except server.HTTPException as e:
        codes_c.append(e.status_code)
os.chmod(fpk, _stat.S_IREAD | _stat.S_IWRITE)
server.approve(jid90, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 0), labels={}, extra_items=[{"label": "Test count", "qty": 1, "unit": "EA"}],
                                            scale=server.ApproveScale(ft_per_paper_inch=50.0, text="1\" = 50'", picked_by_reviewer=False)))
whole_new = json.dumps({"ft_per_paper_inch": 45.0, "set_by": "reviewer", "set_at": "x", "at": 1.0}, indent=1)
fpk.write_text(whole_new[: len(whole_new) // 2])
half_read = server._stored_scale_pick(jid90, 0)
fpk.write_text(json.dumps({"retired": True, "at": 1.0}, indent=1))
check(codes_c == [None, 503] and half_read is None,
      f"critic 13, m-c / m-b: the same pick is not rewritten (an unwritable file passes a re-approve, a new number fails, "
      f"said); right after a retire a half-written read answers no pick: {codes_c}, {half_read}")
# the fourteenth critic, WRONG-1: the reviewer's 40, the operator's 40 typed after it (as the refusals ask), then the
# reviewer back to the engine's 50 - that retire is a reviewer's decision, dated: the typed 40 is refused (it stood, and
# 576 CY were exported beside the page's 50, 36 % low)
server.approve(jid90, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 0), labels={}, extra_items=[{"label": "Test count", "qty": 1, "unit": "EA"}],
                                            scale=server.ApproveScale(ft_per_paper_inch=40.0, text="1\" = 40'", picked_by_reviewer=True, how="typed")))
with p90._lock:
    s90.update({"scale_ft_per_in": 40.0, "scale_source": "typed", "display_page": "d0p0"})
    p90.page_settings("d0p0").update({"operator_scale": 40.0, "operator_scale_at": time.time() + 1})
ok14_before = cfpl.foreign_scale(p90) is None
server.approve(jid90, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 0), labels={}, extra_items=[{"label": "Test count", "qty": 1, "unit": "EA"}],
                                            scale=server.ApproveScale(ft_per_paper_inch=50.0, text="1\" = 50'", picked_by_reviewer=False)))
time.sleep(1.2)                          # the tombstone's time must follow the operator's word (set 1 s ahead above)
server.approve(jid90, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 0), labels={}, extra_items=[{"label": "Test count", "qty": 1, "unit": "EA"}],
                                            scale=server.ApproveScale(ft_per_paper_inch=40.0, text="1\" = 40'", picked_by_reviewer=True, how="typed")))
server.approve(jid90, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 0), labels={}, extra_items=[{"label": "Test count", "qty": 1, "unit": "EA"}],
                                            scale=server.ApproveScale(ft_per_paper_inch=50.0, text="1\" = 50'", picked_by_reviewer=False)))
tomb14 = server._stored_scale_tombstone(jid90, 0) or {}
live14 = server._cf_review_scale_live(p90, "d0p0") or {}
w14 = cfpl.foreign_scale(p90) or ""
# RISK-1: a pick with no time at all, approved again with the same number, is dated by that approve
(d90 / "p0_scale.json").write_text(json.dumps({"ft_per_paper_inch": 45.0, "set_by": "reviewer"}, indent=1))
server._PICK_LAST.pop(str(d90 / "p0_scale.json"), None)
server.approve(jid90, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 0), labels={}, extra_items=[{"label": "Test count", "qty": 1, "unit": "EA"}],
                                            scale=server.ApproveScale(ft_per_paper_inch=45.0, text="1\" = 45'", picked_by_reviewer=True, how="typed")))
dated14 = server._stored_scale_pick(jid90, 0) or {}
with p90._lock:
    p90.page_settings("d0p0").pop("operator_scale", None)
    p90.page_settings("d0p0").pop("operator_scale_at", None)
check(ok14_before and tomb14.get("ft_per_paper_inch") == 50.0 and live14.get("reviewer") is True and live14.get("ft") == 50.0
      and w14.startswith('Sheet 3.0\'s review says 1" = 50 ft, but the cut / fill is set to 1" = 40 ft')
      and isinstance(dated14.get("at"), float) and dated14.get("set_at"),
      f"critic 14, WRONG-1 / RISK-1: going back to the engine's reading is a dated reviewer decision, and it refuses a "
      f"scale typed before it; a pick with no time is dated by its next approve: {w14} / {dated14}")
# critic 15, RISK-2: the go-back holds for what the engine reads NOW - after a re-detect to 45 the typed 40 made before
# the go-back is still refused (it stood, 21 % low); RISK-1: the refusal names the review's number. m1: a retire where
# the engine merely came round to the reviewer's own number keeps the pick's time (no new decision)
with p90._lock:
    s90.update({"scale_ft_per_in": 40.0, "scale_source": "typed", "display_page": "d0p0"})
    p90.page_settings("d0p0").update({"operator_scale": 40.0, "operator_scale_at": time.time()})
time.sleep(1.1)
server.approve(jid90, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 0), labels={}, extra_items=[{"label": "Test count", "qty": 1, "unit": "EA"}],
                                            scale=server.ApproveScale(ft_per_paper_inch=50.0, text="1\" = 50'", picked_by_reviewer=False)))
scaled(d90, 0, 45.0)                                                   # detected again: the engine now reads 45
live15 = server._cf_review_scale_live(p90, "d0p0") or {}
w15 = cfpl.foreign_scale(p90) or ""
scaled(d90, 0, 50.0)
server.approve(jid90, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 0), labels={}, extra_items=[{"label": "Test count", "qty": 1, "unit": "EA"}],
                                            scale=server.ApproveScale(ft_per_paper_inch=40.0, text="1\" = 40'", picked_by_reviewer=True, how="typed")))
at_pick15 = (server._stored_scale_pick(jid90, 0) or {}).get("at")
scaled(d90, 0, 40.0)                                                   # the engine comes round to the reviewer's 40
server.approve(jid90, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid90, 0), labels={}, extra_items=[{"label": "Test count", "qty": 1, "unit": "EA"}],
                                            scale=server.ApproveScale(ft_per_paper_inch=40.0, text="1\" = 40'", picked_by_reviewer=False)))
# scale verdict S2 (rulings 9 / 10): the reviewer's own 40 now STANDS as their record (no tombstone, which reads as "no
# person"), with its time kept - m1's "no new decision" holds
tomb15 = server._stored_scale_pick(jid90, 0) or {}
scaled(d90, 0, 50.0)
with p90._lock:
    p90.page_settings("d0p0").pop("operator_scale", None)
    p90.page_settings("d0p0").pop("operator_scale_at", None)
check(live15.get("ft") == 45.0 and live15.get("reviewer") is True
      and w15.startswith('Sheet 3.0\'s review says 1" = 45 ft, but the cut / fill is set to 1" = 40 ft')
      and isinstance(at_pick15, float) and tomb15.get("at") == at_pick15 and tomb15.get("ft_per_paper_inch") == 40.0,
      f"critic 15, RISK-2 / RISK-1 / m1: the go-back holds after a re-detect and the refusal names the review's number; "
      f"an engine that came round to the pick leaves the reviewer's record standing, its time kept: {w15} / "
      f"{tomb15.get('at')} vs {at_pick15}")
server._cutfill._results.pop(pid90, None)

# ---- round 16 (Koby, 2026-09-29): THE REVIEW WINS, and a scale read wrong is typed right in either place. The Scale
#      card writes the sheet's review (placement.REVIEW_SET = _cf_review_scale_set, the Approve's own record): the sheet
#      shown's number is also the scale in force, the operator's word ("typed"); another sheet's sets its review alone ---
import asyncio  # noqa: E402


class _Req:
    def __init__(self, body):
        self._b = body

    async def json(self):
        return self._b


def typed(pid, ft, sheet, source="typed"):
    # with the stamp of the sheet's review record, as the Scale card sends it (none is refused, CRITIC-scale-3 MINOR 4)
    from cutfill import placement as _pl
    seen = _pl.review_stamps(server._cutfill.get_project(pid)).get(sheet)
    return asyncio.run(server._cutfill.patch_settings(pid, _Req({"scale_ft_per_in": ft, "scale_source": source,
                                                                 "scale_sheet": sheet, "scale_seen": seen})))


settle()
jid96 = "pick96"
pid96 = f"job_{jid96}"
d96 = seed(jid96, pages=2, detected=(0, 1))
scaled(d96, 0, 50.0)                # the detection misread 3.0 as 50
scaled(d96, 1, 40.0)
server.cutfill_open(jid96, server.CutfillOpenBody(page=0, pages=[0, 1]))
settle()
p96 = server._cutfill.get_project(pid96)
s96 = p96.data["settings"]
set_ok = cfpl.REVIEW_SET is server._cf_review_scale_set


def exported96(page):
    """The scale the page's Approve exports at (the review's record, as the export reads it)."""
    return (server._page_scale(jid96, page, json.loads((d96 / f"p{page}_result.json").read_text(encoding="utf-8"))) or {}).get("ft_per_paper_inch")


# the operator finds 3.0 read wrong and types 40 in the Scale card with 3.0 (the sheet shown) on screen
sm96 = typed(pid96, 40.0, "d0p0")
pk96 = server._stored_scale_pick(jid96, 0) or {}
check(set_ok and s96["display_page"] == "d0p0" and pk96.get("ft_per_paper_inch") == 40.0 and pk96.get("set_by") == server._BY_REVIEWER
      and isinstance(pk96.get("at"), float) and s96["scale_ft_per_in"] == 40.0 and s96["scale_source"] == "typed"
      and s96["scale_page"] == "d0p0" and p96.page_settings("d0p0").get("operator_scale") == 40.0
      and cfpl.foreign_scale(p96) is None and exported96(0) == 40.0
      and (sm96.get("review_scales") or {}).get("d0p0") == 40.0 and "d0p0" not in (sm96.get("scale_refused") or {}),
      f"typed in the Scale card on the sheet shown, 40 is the sheet's review pick (what its Approve exports at) and the "
      f"scale in force, the operator's word: {pk96} / {s96['scale_ft_per_in']} {s96['scale_source']} / {exported96(0)}")
# the engine's own reading typed back: since scale verdict S2 a person's record of 50, how "typed" (typed in the card,
# whatever it equals: CRITIC-scale-1 M2), dated - no longer a tombstone (ruling 10); the review, the export and the cut / fill
# read 50
typed(pid96, 50.0, "d0p0")
tomb96 = server._stored_scale_pick(jid96, 0) or {}
live96 = server._cf_review_scale_live(p96, "d0p0") or {}
check(tomb96.get("how") == "typed" and isinstance(tomb96.get("at"), float)
      and tomb96.get("ft_per_paper_inch") == 50.0 and live96.get("ft") == 50.0
      and live96.get("reviewer") is True and s96["scale_ft_per_in"] == 50.0 and exported96(0) == 50.0
      and cfpl.foreign_scale(p96) is None,
      f"the engine's own reading typed back is a person's dated record of it (typed: CRITIC-scale-1 M2); review, "
      f"export and cut / fill "
      f"read 50: {tomb96}, {live96}")
# typed with 3.1 on screen (not the sheet shown): 3.1's review alone; the scale in force measures 3.0 still, and the page
# is told each sheet's review scale (the card shows 3.1's own there)
typed(pid96, 30.0, "d0p1")
pk96b = server._stored_scale_pick(jid96, 1) or {}
sm96b = server._cutfill.get_project_summary(pid96)
check(pk96b.get("ft_per_paper_inch") == 30.0 and exported96(1) == 30.0 and s96["scale_ft_per_in"] == 50.0
      and s96["scale_page"] == "d0p0" and sm96b.get("review_scales") == {"d0p0": 50.0, "d0p1": 30.0}
      and "d0p1" in (sm96b.get("scale_refused") or {}) and "d0p0" not in (sm96b.get("scale_refused") or {}),
      f"typed with another sheet on screen, the number is that sheet's review alone; the sheet shown keeps its scale: "
      f"{pk96b} / {s96['scale_ft_per_in']} of {s96['scale_page']} / {sm96b.get('review_scales')}")
# a number no review takes is refused in the review's words, nothing changed; an unwritable record refuses too, said
fpk96 = d96 / "p0_scale.json"
before96 = (s96["scale_ft_per_in"], s96["scale_source"], fpk96.read_text())
codes96 = []
for bad in (0.00001, 99999.0):
    try:
        typed(pid96, bad, "d0p0")
        codes96.append(None)
    except server.HTTPException as e:
        codes96.append((e.status_code, "not a usable number of feet per paper inch" in str(e.detail)))
after96 = (s96["scale_ft_per_in"], s96["scale_source"], fpk96.read_text())
os.chmod(fpk96, _stat.S_IREAD)
code96w = None
try:
    typed(pid96, 45.0, "d0p0")
except server.HTTPException as e:
    code96w = (e.status_code, e.detail)
os.chmod(fpk96, _stat.S_IREAD | _stat.S_IWRITE)
check(codes96 == [(400, True), (400, True)] and after96 == before96 and bool(code96w) and code96w[0] == 503
      and "could not be recorded in the review" in str(code96w[1]) and s96["scale_ft_per_in"] == 50.0,
      f"a number no review takes is refused in the review's words and changes nothing; an unwritable record refuses the "
      f"typing, said: {codes96} / {code96w}")
# the reviewer's correction in the review (an Approve at 40) refuses the cut / fill's 50 on the sheet shown, saying where
# to type; the operator types 40 there, and review, export and cut / fill hold one scale
server.approve(jid96, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid96, 0), labels={}, extra_items=[{"label": "Test count", "qty": 1, "unit": "EA"}],
                                            scale=server.ApproveScale(ft_per_paper_inch=40.0, text="1\" = 40'", picked_by_reviewer=True, how="typed")))
w96 = cfpl.foreign_scale(p96) or ""
typed(pid96, 40.0, "d0p0")
check(w96.startswith('Sheet 3.0\'s review says 1" = 40 ft, but the cut / fill is set to 1" = 50 ft')
      and "type the right scale in the Scale card with sheet 3.0 on screen" in w96
      and cfpl.foreign_scale(p96) is None and s96["scale_ft_per_in"] == 40.0 and exported96(0) == 40.0,
      f"a correction made in the review refuses the cut / fill's old scale until the right one is typed (or Cut / fill is "
      f"opened again), then review, export and cut / fill agree: {w96}")
server._cutfill._results.pop(pid96, None)

# ---- critic 17 (CRITIC-repairs-17.md) ------------------------------------------------------------------------------------
# WRONG-1: Cut / fill opened before Detect records no detection on the sheet, and the review went unasked: the sheet
# answers to the job's own detection of its page now, and the Scale card reaches it
settle()
jid97 = "pick97"
pid97 = f"job_{jid97}"
d97 = seed(jid97, pages=1, detected=())
server.cutfill_open(jid97, server.CutfillOpenBody(page=0))
settle()
p97 = server._cutfill.get_project(pid97)
cr97 = p97.page_settings("d0p0").get("core_result")            # none on record: not detected at the open
scaled(d97, 0, 50.0)                                           # Detect, with the stage open: the review reads 50
server.JOBS[jid97]["status"] = {0: "done"}
rv97 = cfpl.review_scale(p97, "d0p0")
typed(pid97, 40.0, "d0p0")
pk97 = server._stored_scale_pick(jid97, 0) or {}
check(cr97 is None and p97.page_settings("d0p0").get("role") in ("both", "existing", "proposed", "grading")
      and (rv97 or {}).get("ft") == 50.0 and pk97.get("ft_per_paper_inch") == 40.0,
      f"critic 17, WRONG-1: a sheet opened before Detect answers to the job's own detection of its page, and the Scale card "
      f"writes it: {cr97} / {rv97} / {pk97}")
# ... and a backstop that needs no review hook: rows computed on this page at 50, the page approved at 40 - refused, said
keep_rs = cfpl.REVIEW_SCALE
cfpl.REVIEW_SCALE = None                                       # the rule above blind, as it was for that sheet
with p97._lock:
    s97 = p97.data["settings"]
    s97.update({"display_page": "d0p0", "region": [[100, 100], [500, 100], [500, 500], [100, 500]], "limit": {"source": "drawn"},
                "scale_ft_per_in": 50.0, "scale_source": "typed"})
    p97.page_settings("d0p0").pop("operator_scale", None)
    p97.page_settings("d0p0").pop("operator_scale_at", None)
    fake97 = {"display_page": "d0p0", "ft_per_in": 50.0, "warnings": [], "computed_at": 7777.0,
              "totals": {"cut_cy": 0.0, "fill_cy": 900.2, "net_cy": 900.2, "area_sf": 24305.6}, "inputs": cfpl.inputs_sig(p97)}
Path(p97.dir, "result.json").write_text(json.dumps(fake97))
server._cutfill._results[pid97] = fake97
items97 = [{"label": "Site Fill", "qty": 900.0, "unit": "CY"}]
why97 = server._cf_rows_refused(jid97, 0, items97, {"ft_per_paper_inch": 40.0}) or ""
why97b = server._cf_rows_refused(jid97, 0, items97, {"ft_per_paper_inch": 50.0}) or ""
cfpl.REVIEW_SCALE = keep_rs
server._cutfill._results.pop(pid97, None)
check('these cut / fill rows were computed at 1" = 50 ft, but this page is approved at 1" = 40 ft' in why97
      and "were computed at" not in why97b,
      f"critic 17, WRONG-1: rows computed on the page at 50 are refused when the page is approved at 40, whatever the "
      f"review hook saw; at 50 the backstop says nothing: {why97} / {why97b}")
# WRONG-2: a sheet's record pointing at another owner's job is neither read nor written (the Scale card set another
# client's review scale through it); the same owner's job, or the project's own, is
settle()
jid98a, jid98b, jid98c = "pick98a", "pick98b", "pick98c"
d98a = seed(jid98a, pages=2, detected=(0, 1))
scaled(d98a, 0, 50.0)
scaled(d98a, 1, 50.0)
d98b = seed(jid98b, pages=1, detected=(0,))
scaled(d98b, 0, 30.0)
d98c = seed(jid98c, pages=1, detected=(0,))
scaled(d98c, 0, 20.0)
server.JOBS[jid98a]["owner"] = "alice"
server.JOBS[jid98b]["owner"] = "bob"
server.JOBS[jid98c]["owner"] = "alice"
server.cutfill_open(jid98a, server.CutfillOpenBody(page=0, pages=[0, 1]))
settle()
p98 = server._cutfill.get_project(f"job_{jid98a}")
with p98._lock:
    p98.page_settings("d0p1")["core_result"] = str(d98b / "p0_result.json")     # an old record pointed at bob's job
rv_other = cfpl.review_scale(p98, "d0p1")
set_other = server._cf_review_scale_set(p98, "d0p1", 45.0, "x")
wrote_other = (d98b / "p0_scale.json").exists()
with p98._lock:
    p98.page_settings("d0p1")["core_result"] = str(d98c / "p0_result.json")     # alice's own other job (a link)
rv_same = cfpl.review_scale(p98, "d0p1")
check(rv_other is None and set_other is None and not wrote_other and (rv_same or {}).get("ft") == 20.0,
      f"critic 17, WRONG-2: another owner's job is neither read nor written through a sheet's record; the same owner's "
      f"is: {rv_other} / {set_other} / {rv_same}")
# WRONG-4: a sheet that takes no part keeps its own review scale - the Scale card refuses, said - and the page is told
# that sheet's own scale to show
with p98._lock:
    p98.page_settings("d0p1")["core_result"] = str(d98a / "p1_result.json")
    p98.page_settings("d0p1")["role"] = "none"
    p98.data["settings"]["display_page"] = "d0p0"
code98 = None
try:
    typed(f"job_{jid98a}", 25.0, "d0p1")
except server.HTTPException as e:
    code98 = (e.status_code, str(e.detail))
sm98 = server._cutfill.get_project_summary(f"job_{jid98a}")
check(bool(code98) and code98[0] == 409 and "takes no part in the cut / fill" in code98[1]
      and not (d98a / "p1_scale.json").exists() and (sm98.get("review_scales") or {}).get("d0p1") == 50.0,
      f"critic 17, WRONG-4: a sheet that takes no part keeps its review scale (refused, said), and the page is told its own "
      f"scale to show: {code98} / {sm98.get('review_scales')}")

# ---- critic 18 (CRITIC-repairs-18.md) ------------------------------------------------------------------------------------
import types  # noqa: E402

REQ0 = types.SimpleNamespace(scope={})         # single-password mode: the link route's owner check does not apply
settle()
# RISK-1: a set of another owner linked by the host (staff may link any set) - the host records which job the document
# is, and its review is READ, so the rule holds (the sheet shown refused where it disagrees), but never written (409)
jid99a, jid99b, jid99c, jid99d = "pick99a", "pick99b", "pick99c", "pick99d"
d99a = seed(jid99a, pages=1, detected=(0,))
scaled(d99a, 0, 50.0)
d99b = seed(jid99b, pages=2, detected=(0,))
scaled(d99b, 0, 40.0)
server.JOBS[jid99a]["owner"] = "alice"
server.JOBS[jid99b]["owner"] = "bob"
server.cutfill_open(jid99a, server.CutfillOpenBody(page=0))
settle()
lk_b = server.cutfill_link(jid99a, server.CutfillLinkBody(job=jid99b), REQ0)
p99 = server._cutfill.get_project(f"job_{jid99a}")
kb = f"d{lk_b['doc']}p0"
with p99._lock:
    p99.page_settings(kb)["role"] = "both"
    p99.data["settings"].update({"display_page": kb, "scale_ft_per_in": 50.0, "scale_source": "typed", "scale_page": kb})
# since critic bid-content-1 (WRONG-2) a linked set is reached with the host's rule for the CALLER: bob's set is
# staff's to read (staff linked it and works in alice's project here); alice herself, who may not open bob's job,
# reads nothing of it (tests/test_cutfill_link_visibility.py)
_tok99 = server._CF_CALLER.set(("alice", False))
rv99_alice = cfpl.review_scale(p99, kb)
server._CF_CALLER.reset(_tok99)
check(rv99_alice is None, f"bid-content-1 WRONG-2: alice reads nothing of bob's set through the link ({rv99_alice})")
_tok99 = server._CF_CALLER.set(("staff_synth", True))
rv99 = cfpl.review_scale(p99, kb)
w99 = cfpl.foreign_scale(p99) or ""
code99 = None
try:
    typed(f"job_{jid99a}", 45.0, kb)                         # another number: refused, the review's named
except server.HTTPException as e:
    code99 = (e.status_code, str(e.detail))
typed(f"job_{jid99a}", 40.0, kb)                             # its own number: taken, nothing written (critic 19, WRONG-1)
server._CF_CALLER.reset(_tok99)
s99 = p99.data["settings"]
check((p99.data.get("host_links") or {}).get(str(lk_b["doc"])) == jid99b and (rv99 or {}).get("ft") == 40.0
      and (rv99 or {}).get("writable") is False
      and 'review says 1" = 40 ft' in w99 and "type 40 in the Scale card" in w99 and "another owner's plan set" in w99
      and bool(code99) and code99[0] == 409 and 'says 1" = 40 ft' in code99[1] and "another owner" in code99[1]
      and s99["scale_ft_per_in"] == 40.0 and cfpl.foreign_scale(p99) is None and not (d99b / "p0_scale.json").exists(),
      f"critic 18, RISK-1 / critic 19, WRONG-1: a set of another owner linked by the host: its review is read (the sheet "
      f"shown refused where it disagrees, the words name the number to type), never written; its own number is taken and "
      f"measures (it was refused too: a dead end): {rv99} / {w99[:90]} / {code99}")
# RISK-3: a sheet of a linked set (same owner) detected after the link has no record, and answers to that set's review
# all the same (host_links); its scale can be typed (it was refused: "detect it in review first" - it was detected)
d99c = seed(jid99c, pages=2, detected=(0,))
scaled(d99c, 0, 50.0)
server.JOBS[jid99c]["owner"] = "alice"
lk_c = server.cutfill_link(jid99a, server.CutfillLinkBody(job=jid99c), REQ0)
kc1 = f"d{lk_c['doc']}p1"
scaled(d99c, 1, 50.0)                          # its page 2, detected after the link: no record
with p99._lock:
    p99.page_settings(kc1)["role"] = "existing"
rec_c = p99.page_settings(kc1).get("core_result")
rv_c = cfpl.review_scale(p99, kc1)
typed(f"job_{jid99a}", 45.0, kc1)
pk_c = server._stored_scale_pick(jid99c, 1) or {}
check(rec_c is None and (rv_c or {}).get("ft") == 50.0 and pk_c.get("ft_per_paper_inch") == 45.0,
      f"critic 18, RISK-3: a linked sheet detected after the link answers to its set's review, and its scale is set there: "
      f"{rec_c} / {rv_c} / {pk_c}")
# RISK-2: a record pointing a sheet at another owner's job that the host did not link (planted while the page route took
# any key) is dropped at the next open, so it feeds no lines; the host's own cross-owner link survives
d99d = seed(jid99d, pages=1, detected=(0,))
scaled(d99d, 0, 30.0)
server.JOBS[jid99d]["owner"] = "carol"
kb1 = f"d{lk_b['doc']}p1"
with p99._lock:
    p99.page_settings(kb1)["core_result"] = str(d99d / "p0_result.json")
server.cutfill_open(jid99a, server.CutfillOpenBody(page=0))
settle()
check(p99.page_settings(kb1).get("core_result") is None and Path(str(p99.page_settings(kb).get("core_result"))).parent.name == jid99b,
      f"critic 18, RISK-2: a record naming a job this project may not reach is dropped at the open; the host's link stays: "
      f"{p99.page_settings(kb1).get('core_result')} / {p99.page_settings(kb).get('core_result')}")
# critic 19, RISK-2: a staff link made before the host recorded its links (no host_links) is adopted at the open - its
# document IS that job's PDF - not dropped (its sheets lost their detection, and the volumes moved)
with p99._lock:
    p99.data.pop("host_links", None)
server.cutfill_open(jid99a, server.CutfillOpenBody(page=0))
settle()
adopted = (p99.data.get("host_links") or {}).get(str(lk_b["doc"]))
check(adopted == jid99b and Path(str(p99.page_settings(kb).get("core_result"))).parent.name == jid99b,
      f"critic 19, RISK-2: an older staff link is adopted at the open, its record kept: {adopted}")
# critic 19, MINOR-1: a plan set that is the same PDF as the job's own (one stored file for two uploads) is not linked
# onto the job's own sheets
jid99e = "pick99e"
seed(jid99e, pages=1, detected=(0,))
server.JOBS[jid99e]["path"] = server.JOBS[jid99a]["path"]
server.JOBS[jid99e]["owner"] = "bob"
code_same = None
try:
    server.cutfill_link(jid99a, server.CutfillLinkBody(job=jid99e), REQ0)
except server.HTTPException as e:
    code_same = e.status_code
check(code_same == 400 and (p99.data.get("host_links") or {}).get("0") is None,
      f"critic 19, MINOR-1: a set that is the job's own PDF is not linked onto its sheets: {code_same}")
# critic 17 and 19, RISK-3: page 1 of pick96 was approved at 40; its scale typed to 50 in the Scale card afterwards makes
# that approval OUT OF DATE - said by the approved list and in the bid sheet's header (the report says it above the
# page's table): its quantities were read at the old scale, silently
before96 = server.approved_pages(jid96).get("outdated")
typed(pid96, 50.0, "d0p0")
ap96 = server.approved_pages(jid96)
csv96 = server.bidsheet_csv(jid96, REQ0, ignore=[]).body.decode("utf-8-sig")
report_src = inspect.getsource(server.report_pdf)
check(before96 == {} and 'page 1 was approved at 1" = 40 ft, but its scale is now 1" = 50 ft' in (ap96.get("outdated") or {}).get("0", "")
      and 'OUT OF DATE,"page 1 was approved at 1"" = 40 ft' in csv96.split("\n\n", 1)[0]
      and 'OUT OF DATE,"OUT OF DATE' not in csv96 and "_old = _export_outdated(job_id, pno, exp)" in report_src,
      f"critic 17 / 19, RISK-3: an approval whose page's scale changed since is said to be out of date, above every "
      f"number, once (critic 20, MINOR-10): {ap96.get('outdated')}")

# ---- critic 20 (CRITIC-repairs-20.md) ------------------------------------------------------------------------------------
# WRONG-1: the job's cut / fill rows approved on page 1 were measured on page 2 (the sheet shown); page 2's scale then
# changes - page 1's own scale did not, and its export (900 CY at 50) was not said out of date. It is now: the approve's
# own guard judges the export's rows
settle()
jid20 = "pick20w"
pid20 = f"job_{jid20}"
d20 = seed(jid20, pages=2, detected=(0, 1))
scaled(d20, 0, 50.0)
scaled(d20, 1, 50.0)
server.cutfill_open(jid20, server.CutfillOpenBody(page=1, pages=[0, 1]))
settle()
p20 = server._cutfill.get_project(pid20)
s20 = p20.data["settings"]
with p20._lock:
    s20.update({"display_page": "d0p1", "region": [[100, 100], [500, 100], [500, 500], [100, 500]], "limit": {"source": "drawn"},
                "takeoff": {"strip_in": 6}})
    s20.pop("host_rows", None)
    fake20 = {"display_page": "d0p1", "ft_per_in": 50.0, "warnings": [], "computed_at": 6262.0,
              "totals": {"cut_cy": 0.0, "fill_cy": 900.2, "net_cy": 900.2, "area_sf": 24305.6}, "inputs": cfpl.inputs_sig(p20)}
Path(p20.dir, "result.json").write_text(json.dumps(fake20))
server._cutfill._results[pid20] = fake20
items20 = [{"label": r["label"], "qty": r["qty"], "unit": r["unit"]}
           for r in (server._cf_state(jid20, rows=True).get("summary") or {}).get("rows") or [] if (r.get("qty") or 0) > 0]
server.approve(jid20, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid20, 0), labels={}, extra_items=items20))
before20 = server.approved_pages(jid20).get("outdated")
typed(pid20, 40.0, "d0p1")                                    # page 2's scale typed to 40 in the Scale card
ap20 = server.approved_pages(jid20)
server._cutfill._results.pop(pid20, None)
check(items20 and before20 == {} and "page 1's approved cut / fill rows no longer stand" in (ap20.get("outdated") or {}).get("0", "")
      and "compute in the Cut / fill panel, then approve page 1 again" in (ap20.get("outdated") or {}).get("0", ""),
      f"critic 20, WRONG-1: rows approved on page 1 but measured on page 2 are said out of date when page 2's scale changes "
      f"(the export carried 900 CY where the cut / fill now gives 576.1, silently): {ap20.get('outdated')}")
# WRONG-3: a read-only review with no scale at all: nothing to agree with or write - the sheet shown takes the operator's
# number (every typing was refused: a dead end)
_tok20 = server._CF_CALLER.set(("staff_synth", True))       # bob's set is staff's to read here (bid-content-1 WRONG-2)
scaled(d99b, 0, None)                                        # bob's page 1 now reads no scale
with p99._lock:
    p99.data["settings"].update({"display_page": kb, "scale_ft_per_in": 50.0, "scale_source": "typed", "scale_page": kb})
code20 = None
try:
    typed(f"job_{jid99a}", 45.0, kb)
except server.HTTPException as e:
    code20 = (e.status_code, str(e.detail))
ok20_noscale = (code20 is None and p99.data["settings"]["scale_ft_per_in"] == 45.0 and cfpl.foreign_scale(p99) is None
                and not (d99b / "p0_scale.json").exists())
# MINOR-1: a read-only review's own number is put in force exactly (40.3 typed, within its 1 %: 40 in force)
scaled(d99b, 0, 40.0)
typed(f"job_{jid99a}", 40.3, kb)
exact20 = p99.data["settings"]["scale_ft_per_in"]
server._CF_CALLER.reset(_tok20)
check(ok20_noscale and exact20 == 40.0,
      f"critic 20, WRONG-3 / MINOR-1: a read-only review with no scale takes the operator's number (it was a dead end); one "
      f"within 1 % of a read-only review puts the review's own number in force: {code20} / {exact20}")
# MINOR-7: a PDF already linked here as another plan set is not linked again as a third job's; MINOR-9: a link on the
# job's own PDF (doc 0), left from before, is dropped at the open
jid20f = "pick20f"
seed(jid20f, pages=1, detected=(0,))
server.JOBS[jid20f]["path"] = server.JOBS[jid99b]["path"]
server.JOBS[jid20f]["owner"] = "carol"
code20f = None
try:
    server.cutfill_link(jid99a, server.CutfillLinkBody(job=jid20f), REQ0)
except server.HTTPException as e:
    code20f = e.status_code
with p99._lock:
    p99.data.setdefault("host_links", {})["0"] = jid99b
server.cutfill_open(jid99a, server.CutfillOpenBody(page=0))
settle()
check(code20f == 409 and (p99.data.get("host_links") or {}).get(str(lk_b["doc"])) == jid99b
      and "0" not in (p99.data.get("host_links") or {}),
      f"critic 20, MINOR-7 / MINOR-9: a PDF linked as one set is not relinked as another's; a link on the job's own PDF is "
      f"dropped at the open: {code20f} / {p99.data.get('host_links')}")

# ---- critic 21 (CRITIC-repairs-21.md) ------------------------------------------------------------------------------------
settle()
jid21 = "pick21w"
pid21 = f"job_{jid21}"
d21 = seed(jid21, pages=1, detected=(0,))
scaled(d21, 0, 50.0)
server.cutfill_open(jid21, server.CutfillOpenBody(page=0))
settle()
p21 = server._cutfill.get_project(pid21)
s21 = p21.data["settings"]
with p21._lock:
    s21.update({"display_page": "d0p0", "region": [[100, 100], [500, 100], [500, 500], [100, 500]], "limit": {"source": "drawn"},
                "takeoff": {"strip_in": 6}})
    s21.pop("host_rows", None)
    fake21 = {"display_page": "d0p0", "ft_per_in": 50.0, "warnings": [], "computed_at": 7373.0,
              "totals": {"cut_cy": 0.0, "fill_cy": 900.2, "net_cy": 900.2, "area_sf": 24305.6}, "inputs": cfpl.inputs_sig(p21)}
Path(p21.dir, "result.json").write_text(json.dumps(fake21))
server._cutfill._results[pid21] = fake21
items21 = [{"label": r["label"], "qty": r["qty"], "unit": r["unit"]}
           for r in (server._cf_state(jid21, rows=True).get("summary") or {}).get("rows") or [] if (r.get("qty") or 0) > 0]
fill21 = next(it["qty"] for it in items21 if it["label"] == "Site Fill")
# WRONG-1: the estimator types a quantity over the Site Fill row: the approve's guard checked the row's own number, and
# so does the out-of-date check now (by the typed one it was OUT OF DATE for ever)
server.approve(jid21, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid21, 0), labels={}, extra_items=items21,
                                            quantity_overrides=[server.QtyOverride(label="Site Fill", kind="ft", qty=fill21 + 50,
                                                                                   unit="CY", engine_qty=fill21)]))
exp21 = json.loads((d21 / "p0_elements.json").read_text(encoding="utf-8"))
typed_row = next((it for it in exp21["items"] if it.get("label") == "Site Fill"), {})
ok21_typed = typed_row.get("qty") == fill21 + 50 and server.approved_pages(jid21).get("outdated") == {}
# WRONG-2: the scale typed to 40 - the rows leave the takeoff (stale) and the page is approved without them: the dirt left
# every export, silently. Said now: the page holds the job's rows and its export carries none; the scale branch says
# compute first
typed(pid21, 40.0, "d0p0")
why21_scale = (server.approved_pages(jid21).get("outdated") or {}).get("0", "")
server.approve(jid21, 0, server.ApproveBody(scale_seen=server._scale_stamp(jid21, 0), labels={}, extra_items=[{"label": "Test count", "qty": 1, "unit": "EA"}]))
why21_none = (server.approved_pages(jid21).get("outdated") or {}).get("0", "")
server._cutfill._results.pop(pid21, None)
check(ok21_typed and "compute in the Cut / fill panel, then approve page 1 again with its cut / fill rows" in why21_scale
      and "holds the job's cut / fill rows, but its approved export carries none" in why21_none,
      f"critic 21, WRONG-1 / WRONG-2: a typed quantity over a cut / fill row is judged by the row's own number (no false "
      f"OUT OF DATE); an approval without the rows the page holds is said, and the scale branch says compute first: "
      f"{why21_scale[:90]} / {why21_none[:110]}")
# RISK-2: an export from before the marks, on a job with no cut / fill at all, holding an Areas "Clear & Grub" row: only
# its scale is judged (it read as cut / fill rows with none behind them)
jid21b = "pick21b"
d21b = seed(jid21b, pages=1, detected=(0,))
scaled(d21b, 0, 50.0)
(d21b / "p0_elements.json").write_text(json.dumps({"imageDpi": 300, "scale": {"pixelsPerUnit": 6.0, "unit": "ft"},
                                                   "items": [{"label": "Clear & Grub", "qty": 5000.0, "unit": "SF"}], "elements": []}))
out21b = server.approved_pages(jid21b).get("outdated")
# RISK-3: a link to a set deleted since is no claim: the same PDF uploaded again as another set links
server.JOBS.pop(jid99b, None)
code21 = None
try:
    server.cutfill_link(jid99a, server.CutfillLinkBody(job=jid20f), REQ0)
except server.HTTPException as e:
    code21 = e.status_code
check(out21b == {} and code21 is None and (p99.data.get("host_links") or {}).get(str(lk_b["doc"])) == jid20f,
      f"critic 21, RISK-2 / RISK-3: an unmarked Areas row on a job with no cut / fill is not said out of date; a set deleted "
      f"since does not block its PDF's new link: {out21b} / {code21}")

# ---- critic 22 (CRITIC-repairs-22.md) ------------------------------------------------------------------------------------
# WRONG-1: exports from before the marks carry the cut / fill rows unmarked - judged by name AND unit again (dropping them
# let stale, doubled and old-earthwork dirt reach the bid silently)
settle()
jid22 = "pick22w"
pid22 = f"job_{jid22}"
d22 = seed(jid22, pages=2, detected=(0, 1))
scaled(d22, 0, 50.0)
scaled(d22, 1, 50.0)
server.cutfill_open(jid22, server.CutfillOpenBody(page=0, pages=[0, 1]))
settle()
p22 = server._cutfill.get_project(pid22)
s22 = p22.data["settings"]


def fake22(fill):
    with p22._lock:
        s22.update({"display_page": "d0p0", "region": [[100, 100], [500, 100], [500, 500], [100, 500]], "limit": {"source": "drawn"},
                    "takeoff": {"strip_in": 6}, "scale_ft_per_in": 50.0, "scale_source": "typed"})
        res = {"display_page": "d0p0", "ft_per_in": 50.0, "warnings": [], "computed_at": 8000.0 + fill,
               "totals": {"cut_cy": 0.0, "fill_cy": fill, "net_cy": fill, "area_sf": 24305.6}, "inputs": cfpl.inputs_sig(p22)}
    Path(p22.dir, "result.json").write_text(json.dumps(res))
    server._cutfill._results[pid22] = res


def legacy22(pno, rows):
    """An export from before the marks: the cut / fill's rows with no `src`, at the page's own scale."""
    (d22 / f"p{pno}_elements.json").write_text(json.dumps({"imageDpi": 300, "scale": {"pixelsPerUnit": 6.0, "unit": "ft"},
                                                           "elements": [], "items": [dict(r) for r in rows]}))


fake22(900.2)
rows22 = [{"label": r["label"], "qty": r["qty"], "unit": r["unit"]}
          for r in (server._cf_state(jid22, rows=True).get("summary") or {}).get("rows") or [] if (r.get("qty") or 0) > 0]
server.cutfill_rows(jid22, 0, server.CutfillRowsBody(added=True, pid=pid22))       # page 1 holds the rows
legacy22(0, rows22)
ok22_same = server.approved_pages(jid22).get("outdated") == {}
fake22(600.4)                                               # the limit made smaller and computed: the engine gives 600
stale22 = (server.approved_pages(jid22).get("outdated") or {}).get("0", "")
fake22(900.2)
legacy22(1, rows22)                                         # the same rows in page 2's old export too: counted twice
twice22 = (server.approved_pages(jid22).get("outdated") or {}).get("1", "")
(d22 / "p1_elements.json").unlink()
server._cutfill._results.pop(pid22, None)
# MARKS DECIDE (critic legacy-2, the coordinator's design): an export without `src_marks` is v1.8.5's, so these
# unmarked rows are the old Earthwork calculator's - never judged against the engine. On the page that holds the job's
# Cut / fill rows it is OUT OF DATE whatever the engine's number (Cut / fill was added there in their place); on another
# page it is a CHECK note, not OUT OF DATE (this app's own pre-mark exports exist only on test stores: accepted)
# (the owner's decision, 2026-10-01 evening: these rows count nowhere - what is out of date is that the approval carries
# none of the job's cut / fill rows this page holds; round 22's judging of unmarked rows against the engine stays deleted)
check(rows22 and not ok22_same
      and stale22.startswith("OUT OF DATE: page 1 holds the job's cut / fill rows, but its approved export carries none")
      and twice22 == "" and "not the engine's numbers" not in stale22,
      f"critic 22, WRONG-1 (changed by the legacy-earthwork design): unmarked exports are the old calculator's - OUT OF DATE "
      f"on the Cut / fill page, nothing OUT OF DATE on another (a CHECK note): {stale22[:160]} / {twice22[:160]}")
# RISK-2: the page that holds the job's rows never approved - the dirt is in no export: said in the approved list (the
# review page shows it) and in the bid sheet's header
jid22b = "pick22b"
pid22b = f"job_{jid22b}"
d22b = seed(jid22b, pages=2, detected=(0, 1))
scaled(d22b, 0, 50.0)
scaled(d22b, 1, 50.0)
server.cutfill_open(jid22b, server.CutfillOpenBody(page=0, pages=[0, 1]))
settle()
server.cutfill_rows(jid22b, 0, server.CutfillRowsBody(added=True, pid=pid22b))    # rows added on page 1, not approved
server.approve(jid22b, 1, server.ApproveBody(scale_seen=server._scale_stamp(jid22b, 1), labels={}, extra_items=[{"label": "Test count", "qty": 1, "unit": "EA"}]))           # page 2 approved, without them
ap22b = server.approved_pages(jid22b)
csv22b = server.bidsheet_csv(jid22b, REQ0, ignore=[]).body.decode("utf-8-sig")
check((ap22b.get("outdated") or {}).get("0", "").startswith("NOT IN THE BID: page 1 holds the job's cut / fill rows, but it is not approved")
      and 'NOT IN THE BID,"page 1 holds the job' in csv22b.split("\n\n", 1)[0],
      f"critic 22, RISK-2: the rows' page never approved is said NOT IN THE BID, in the approved list and the bid sheet: "
      f"{ap22b.get('outdated')}")

print("\n" + (f"{fails} failure(s)" if fails else "all ok"))
sys.exit(1 if fails else 0)
