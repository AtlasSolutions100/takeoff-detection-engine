"""THE TAB THAT NEVER HEARD THE SCALE CARD, AND THE ROWS REMOVED AFTER AN APPROVE (critic 23, RISK-1 and RISK-3).

RISK-1. A sheet has one scale, its review's (2026-09-29, "the review wins"). Typing the right scale in the Cut / fill
Scale card writes the sheet's review record. A review page that did not hear it - Cut / fill opened in a tab of its own,
a second tab, another seat - kept its old number, and its next Approve wrote that number over the reviewer's: refused for
its cut / fill rows but the review already at the old number, or ACCEPTED at the old number on a page without rows.
Now the page sends the stamp of the scale record it loaded (`scale_seen`), and the approve is refused, writing nothing,
when the record changed since and the page's scale disagrees with it. A page that sends no stamp is refused too (a page
from before the stamp also never asked the record). After a reload the same approve goes through at the review's number.

RISK-3. Approved with the cut / fill rows, then Remove from takeoff: the bid still priced the dirt and nothing said so.
Now GET /approved `outdated`, the bid sheet's header and the report say OUT OF DATE.

The engine's read is a stub (critics 10-23: existing contours z = 100..104, proposed 101..105, fill = area x 1 ft:
900.2 CY at 1" = 50 ft). Temp stores only, in-process, the real routes.

    python tests/test_scale_deaf_tab.py
"""
import asyncio
import csv
import io
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = Path(tempfile.mkdtemp(prefix="cfdeaf_")).resolve()
os.environ["TAKEOFF_JOBS_DIR"] = str(JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="cfdeaf_lg_")
os.environ["CUTFILL_OUT"] = str(JOBS / "cutfill")

import pymupdf  # noqa: E402
import server  # noqa: E402
from cutfill import project as cfproject  # noqa: E402
from cutfill.core_edits import sig_of  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from takeoff_engine.detect_page import engine_sig  # noqa: E402

assert str(server.JOBS_DIR.resolve()) == str(JOBS), "the host store must be the temp one"
cfs = server._cutfill
fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg, flush=True)
    if not ok:
        fails += 1


# ---- the engine's read, stubbed ----------------------------------------------------------------------------------------
Y = (150.0, 200.0, 250.0, 300.0, 350.0)
PRINTS = {}
RECT = [[160.0, 160.0], [440.0, 160.0], [440.0, 340.0], [160.0, 340.0]]
CF_ROW_RE = re.compile(r"^(Site Cut|Site Fill)( – .+)?$|^(Export Cut|Import Structural|Silt / Construction Fence|Clear & Grub|"
                       r"Strip Topsoil|Rough Place Topsoil|Export Topsoil|Import Topsoil|Site walls face)$")


def make_an(project, key, printed=None):
    chains, contours = [], []
    for i, y in enumerate(Y):
        for off, style, z0 in ((1, 1, 100.0), (11, 2, 101.0)):
            cid = off + i
            chains.append({"id": cid, "pts": [[150.0, y], [450.0, y]], "length": 300.0, "style": style})
            contours.append({"id": cid, "z": z0 + i, "style": style, "chain_ids": [cid], "label_ids": [], "how": "label",
                             "length": 300.0, "basis": []})
    an = {"reader": cfproject.READER, "interp": cfproject.INTERP,
          "lines_from": "engine+core joins" if project.core_result(key) is not None else "engine",
          "review": sig_of(project.page_settings(key).get("core_edits")), "width": 612, "height": 792,
          "sheet": "3." + key.split("p")[-1], "chains": chains, "contours": contours, "labels": [], "spots": [], "styles": [],
          "pads": [], "families": [{"style": 1, "role": "existing", "sig": "fam-e", "styles": [1]},
                                   {"style": 2, "role": "proposed", "sig": "fam-p", "styles": [2]}],
          "lines_read": None, "counts": {}}
    if printed:
        an["scale_default"] = {"ft_per_in": float(printed), "text": f"SCALE : 1''={printed:g}'"}
    return an


def fake_analyze(project, key, prog=lambda m: None, full=False, core=None):
    sha = project.data["docs"][int(key[1:].split("p")[0])].get("sha", "")
    rd = None if full else project.get_reading(key, sha, core=core is not None)
    if rd is None and full:
        project.drop_reading(key)
    if rd is None:
        project.put_reading(key, sha, {"stub": key}, core=core is not None)
    an = make_an(project, key, PRINTS.get((project.id, key)))
    an["timing"] = {"cached": False, "total": 0.0}
    return an


cfproject.analyze_page = fake_analyze


def seed(jid, pages, scales):
    pdf = JOBS / f"{jid}.pdf"
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page(width=612, height=792).insert_text((520, 760), f"3.{i}", fontsize=22)
    doc.save(str(pdf))
    doc.close()
    d = server._job_dir(jid)
    d.mkdir(parents=True, exist_ok=True)
    for i in range(pages):
        res = {"page": {"width_pt": 612, "height_pt": 792},
               "chains": [{"chain_id": 1, "style_id": 1, "class": "solid", "color": [0, 0, 0], "length_pt": 10.0,
                           "points": [[0, 0], [11, 10]]}], "styles": [], "engine_sig": engine_sig()}
        if scales.get(i):
            res["scale"] = {"ft_per_paper_inch": scales[i], "text": f'1" = {scales[i]:g}\'', "source": "caption"}
        (d / f"p{i}_result.json").write_text(json.dumps(res))
    server.JOBS[jid] = {"path": str(pdf), "pages": pages, "status": {i: "done" for i in range(pages)}, "owner": None,
                        "mtime": round(pdf.stat().st_mtime, 2), "sizes": [[612.0, 792.0]] * pages}


class _Req:
    def __init__(self, body):
        self.body = body
        self.scope = {}

    async def json(self):
        return self.body


class FakeReq:
    def __init__(self, user):
        self.scope = {"tk_user": user, "tk_staff": False}
        self.cookies = {}


def route(name, pid, *args):
    r = getattr(cfs, name)(pid, *args)
    return asyncio.run(r) if asyncio.iscoroutine(r) else r


def open_job(jid, pages, scales, printed):
    """A job opened in Cut / fill from page 1 (sheet 3.0), its read settled, the sheet printing `printed`."""
    seed(jid, pages, scales)
    server.cutfill_open(jid, server.CutfillOpenBody(page=0))
    p = cfs.get_project(f"job_{jid}")
    t0 = time.time()
    while (p.jobs.get("d0p0") or {}).get("status") in ("running", "queued") and time.time() - t0 < 10:
        time.sleep(0.02)
    PRINTS[(p.id, "d0p0")] = printed
    with open(p.analysis_path("d0p0"), "w", encoding="utf-8") as f:
        json.dump(make_an(p, "d0p0", printed), f)
    p._an_cache.pop("d0p0", None)
    return p


def scale_card(p, ft):
    """The Cut / fill Scale card: typed with sheet 3.0 on screen (it writes the sheet's review record)."""
    # with the stamp of the review record it loaded, as the card sends it (a write with none is refused, CRITIC-scale-3)
    from cutfill import placement as _pl
    return route("patch_settings", p.id, _Req({"scale_ft_per_in": ft, "scale_source": "typed", "scale_sheet": "d0p0",
                                               "scale_seen": _pl.review_stamps(p).get("d0p0")}))


def compute(p):
    route("patch_settings", p.id, _Req({"region": RECT, "display_page": "d0p0"}))
    r = route("compute", p.id)
    return (r.get("totals") or {}).get("fill_cy")


def host_rows(p):
    t = route("takeoff_route", p.id)
    return [(x["label"], float(x["qty"]), x["unit"]) for x in t.get("rows") or []
            if CF_ROW_RE.match(str(x["label"])) and float(x["qty"]) > 0]


def loaded(jid, page):
    """What a review page loads (GET .../scale): the stamp it will send back, and the review's number."""
    j = server.get_scale_pick(jid, page)
    return j.get("stamp"), j.get("in_force")


NO_STAMP = object()


def approve(jid, page, rows, ft, seen=NO_STAMP):
    kw = dict(labels={}, extra_items=[{"label": l, "qty": q, "unit": u} for l, q, u in rows],
              scale=server.ApproveScale(ft_per_paper_inch=ft, text=f'1" = {ft:g}\'', picked_by_reviewer=True, how="typed"))
    if seen is not NO_STAMP:
        kw["scale_seen"] = seen
    try:
        r = server.approve(jid, page, server.ApproveBody(**kw))
        return {"ok": True, "stamp": json.loads(r.body).get("scale_stamp")}
    except HTTPException as e:
        return {"ok": False, "code": e.status_code, "detail": e.detail}


def pick_bytes(jid, page):
    f = server._scale_pick_path(jid, page)
    return f.read_bytes() if f.exists() else None


def export_bytes(jid, page):
    f = server._job_dir(jid) / f"p{page}_elements.json"
    return f.read_bytes() if f.exists() else None


def export_ft(jid, page):
    e = json.loads(export_bytes(jid, page))
    return round(300.0 / float(e["scale"]["pixelsPerUnit"]), 4)


def review_ft(jid, page):
    res = json.loads((server._job_dir(jid) / f"p{page}_result.json").read_text(encoding="utf-8"))
    return (server._page_scale(jid, page, res) or {}).get("ft_per_paper_inch")


def words(d):
    return d if isinstance(d, str) else json.dumps(d)


def section(name, fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - a crash is a failure of the section, said
        import traceback
        check(False, f"{name} raised {type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}")


# ---- B (a): the deaf approve carries the page's cut / fill rows ------------------------------------------------------------
def b_rows():
    jid = "deafa"
    p = open_job(jid, 1, {0: 45.0}, 50.0)              # the engine read 45; the sheet prints 50
    deaf, _ = loaded(jid, 0)                           # the host page loaded before the Scale card was used
    scale_card(p, 50.0)
    fill = compute(p)
    rows = host_rows(p)
    server.cutfill_rows(jid, 0, server.CutfillRowsBody(added=True, pid=p.id, rows=[]))
    pick0 = pick_bytes(jid, 0)
    check(fill == 900.2 and review_ft(jid, 0) == 50.0 and rows, f"set-up: the Scale card's 50 is the review's, 900.2 CY ({fill})")
    a = approve(jid, 0, rows, 45.0, deaf)
    check(not a["ok"] and a.get("code") == 409 and 'changed to 1" = 50 ft' in words(a.get("detail"))
          and "Reload the page" in words(a.get("detail")),
          "B(a): the deaf page's approve at its old 45, with the rows, is refused and says the scale changed to 50 and to "
          "reload: " + words(a.get("detail"))[:220])
    check(pick_bytes(jid, 0) == pick0 and review_ft(jid, 0) == 50.0 and export_bytes(jid, 0) is None,
          "B(a): ...and it wrote NOTHING: the reviewer's 50 stands (it was retired to 45 by the refused approve), no export")
    seen, shown = loaded(jid, 0)                       # the reload: the page shows the review's number
    b = approve(jid, 0, rows, shown, seen)
    check(shown == 50.0 and b["ok"] and export_ft(jid, 0) == 50.0,
          f"B(a), no loop: after the reload the same approve goes through at the review's {shown}")


# ---- B (b): the deaf approve carries no rows (they are on page 2) ----------------------------------------------------------
def b_norows():
    jid = "deafb"
    p = open_job(jid, 2, {0: 45.0, 1: 50.0}, 50.0)
    deaf, _ = loaded(jid, 0)
    scale_card(p, 50.0)
    compute(p)
    rows = host_rows(p)
    server.cutfill_rows(jid, 1, server.CutfillRowsBody(added=True, pid=p.id, rows=[]))
    a2 = approve(jid, 1, rows, 50.0, loaded(jid, 1)[0])
    a1 = approve(jid, 0, [("Curb", 200.0, "LF")], 50.0, loaded(jid, 0)[0])        # a page that heard approves page 1
    check(a2["ok"] and a1["ok"] and export_ft(jid, 0) == 50.0, "set-up: page 2 approved with the rows, page 1 at 50")
    pick0, exp0 = pick_bytes(jid, 0), export_bytes(jid, 0)
    a = approve(jid, 0, [("Curb", 200.0, "LF")], 45.0, deaf)
    check(not a["ok"] and a.get("code") == 409 and 'changed to 1" = 50 ft' in words(a.get("detail")),
          "B(b): the deaf page's approve of page 1 (no rows) at 45 is refused - it was ACCEPTED, exporting the linework at "
          "45 where the sheet prints 50: " + words(a.get("detail"))[:160])
    check(pick_bytes(jid, 0) == pick0 and export_bytes(jid, 0) == exp0 and review_ft(jid, 0) == 50.0
          and not (server.approved_pages(jid).get("outdated") or {}),
          "B(b): ...nothing written (the pick and page 1's export are byte for byte the same), nothing out of date")
    seen, shown = loaded(jid, 0)
    b = approve(jid, 0, [("Curb", 200.0, "LF")], shown, seen)
    check(b["ok"] and export_ft(jid, 0) == 50.0, "B(b), no loop: reloaded, the same approve goes through at 50")


# ---- B: no stamp; a stale stamp that agrees; a page's own changes; the stamp of a refused approve --------------------------
def b_rules():
    jid = "deafc"
    seed(jid, 1, {0: 45.0})
    s0, _ = loaded(jid, 0)
    approve(jid, 0, [("Curb", 200.0, "LF")], 50.0, s0)       # the review: 50
    pick0, exp0 = pick_bytes(jid, 0), export_bytes(jid, 0)
    a = approve(jid, 0, [("Curb", 200.0, "LF")], 40.0)
    check(not a["ok"] and a.get("code") == 409 and "Reload the page" in words(a.get("detail"))
          and pick_bytes(jid, 0) == pick0 and export_bytes(jid, 0) == exp0,
          "no stamp (a page from before it, which never asks the record either): refused, nothing written: "
          + words(a.get("detail")))
    a = approve(jid, 0, [("Curb", 200.0, "LF")], 50.0)
    check(not a["ok"] and a.get("code") == 409, "no stamp: refused even when its number agrees (a reload makes it a page "
                                                "that sends one)")
    a = approve(jid, 0, [("Curb", 200.0, "LF")], 50.0, s0)
    check(a["ok"], "a stamp older than the record, but the page shows the review's own 50: never refused for the stamp alone")
    s1, _ = loaded(jid, 0)
    c = approve(jid, 0, [("Curb", 200.0, "LF")], 40.0, s1)
    check(c["ok"] and review_ft(jid, 0) == 40.0 and isinstance(c.get("stamp"), str) and c["stamp"] != s1,
          "a page that saw the record changes the scale on purpose (40): accepted, and its answer carries the new stamp")
    d = approve(jid, 0, [("Curb", 200.0, "LF")], 35.0, c["stamp"])
    check(d["ok"] and review_ft(jid, 0) == 35.0, "...its next change (35), sent with that stamp, is accepted too")
    e = approve(jid, 0, [("Curb", 200.0, "LF")], 30.0, s1)
    check(not e["ok"] and 'changed to 1" = 35 ft' in words(e.get("detail")) and review_ft(jid, 0) == 35.0,
          "a page still holding an older stamp sends 30: refused, the 35 stands")


# ---- B: the Scale card CONFIRMS the engine's own reading on a sheet with no record --------------------------------------------
def b_confirm():
    jid = "deafd"
    p = open_job(jid, 1, {0: 45.0}, 45.0)
    deaf, _ = loaded(jid, 0)
    scale_card(p, 45.0)
    j = server.get_scale_pick(jid, 0)
    tomb = json.loads(pick_bytes(jid, 0) or b"null")
    # scale verdict S2 (coordinator rulings 8 / 10, 2026-10-02): the confirm is a PERSON'S record - how "confirmed",
    # dated - where it used to be a tombstone, which reads as "no person"
    # CRITIC-scale-1 M2: a number TYPED in the Scale card is recorded as typed, even when it is the engine's own
    check(isinstance(tomb, dict) and tomb.get("how") == "typed" and tomb.get("ft_per_paper_inch") == 45.0
          and tomb.get("at") and not tomb.get("retired") and j.get("recorded") is True
          and j.get("stamp") != deaf and j.get("in_force") == 45.0 and (j.get("reviewer") or {}).get("how") == "typed",
          "the Scale card's typing of the engine's 45 is recorded (a person's dated record, how typed); "
          "it wrote nothing else, so a page holding another number never heard it: " + json.dumps(tomb))
    a = approve(jid, 0, [("Curb", 200.0, "LF")], 40.0, deaf)
    check(not a["ok"] and 'changed to 1" = 45 ft' in words(a.get("detail")) and review_ft(jid, 0) == 45.0,
          "...so that page's approve of its own unapproved 40 is refused, the 45 stands")


# ---- B: GET .../scale gives the number and its stamp from ONE read -------------------------------------------------------------
def b_get():
    jid = "deafe"
    seed(jid, 1, {0: 45.0})
    j0 = server.get_scale_pick(jid, 0)
    approve(jid, 0, [("Curb", 200.0, "LF")], 50.0, j0.get("stamp"))
    j1 = server.get_scale_pick(jid, 0)
    # CRITIC-scale-1 M1: the stamp is never a constant; it covers the record AND the detection's reading
    check(j0.get("stamp") == server._scale_stamp_of(None, server._scale_detection_digest(jid, 0))
          and j0.get("recorded") is False and j1.get("stamp") == server._scale_stamp(jid, 0)
          and j1.get("stamp") != j0.get("stamp") and (j1.get("reviewer") or {}).get("ft_per_paper_inch") == 50.0,
          f"GET .../scale: the stamp of no record on this detection, the record's own stamp after "
          f"({j0.get('stamp')} -> {j1.get('stamp')})")


# ---- D: approved with the rows, then Remove from takeoff ---------------------------------------------------------------------
def d_removed():
    jid = "deaff"
    p = open_job(jid, 1, {0: 50.0}, 50.0)
    compute(p)
    rows = host_rows(p)
    server.cutfill_rows(jid, 0, server.CutfillRowsBody(added=True, pid=p.id, rows=[]))
    a = approve(jid, 0, rows, 50.0, loaded(jid, 0)[0])
    check(a["ok"] and not (server.approved_pages(jid).get("outdated") or {}), "set-up: approved with the rows, nothing said")
    server.cutfill_rows(jid, 0, server.CutfillRowsBody(added=False, pid=p.id, rows=[]))
    od = server.approved_pages(jid).get("outdated") or {}
    w = od.get("0") or ""
    check(w.startswith("OUT OF DATE: page 1's approval still has cut / fill rows that were removed from the takeoff")
          and "approve page 1 again" in w,
          "D: Remove from takeoff after the approve: GET /approved says OUT OF DATE and what to do: " + w)
    r = server.bidsheet_csv(jid, FakeReq(None), ignore=[])
    head = [x for x in csv.reader(io.StringIO(r.body.decode("utf-8-sig"))) if x and x[0] == "OUT OF DATE"]
    check(any("removed from the takeoff" in " ".join(x) for x in head), "D: ...and so does the bid sheet's header")
    server.report_pdf(jid, FakeReq(None), ignore=[])
    doc = pymupdf.open(str(server._job_dir(jid) / "takeoff_report.pdf"))
    txt = " ".join(" ".join(pg.get_text().split()) for pg in doc)
    doc.close()
    check("removed from the takeoff" in txt, "D: ...and the report")
    # (the takeoff starts empty, 2026-10-03: an approve with nothing in the takeoff is refused, so the page approved
    # again carries the curb the estimator still bids)
    b = approve(jid, 0, [("Curb", 200.0, "LF")], 50.0, loaded(jid, 0)[0])
    check(b["ok"] and not (server.approved_pages(jid).get("outdated") or {}),
          "D: the advice followed (approve page 1 again): the note goes")
    # the old Earthwork calculator's rows are not the engine's: a v1.8.5 export (no marks) carrying them is not said removed
    e = json.loads(export_bytes(jid, 0))
    e.pop("src_marks", None)
    e["items"] = [{"label": "Site Fill", "qty": 1500.0, "unit": "CY", "count": 1}]
    (server._job_dir(jid) / "p0_elements.json").write_text(json.dumps(e))
    w2 = (server.approved_pages(jid).get("outdated") or {}).get("0") or ""
    check("removed from the takeoff" not in w2, "D: old Earthwork calculator rows are never said removed: " + (w2 or "(none)"))


# ---- S2 (critic 24, MINOR-1): the stamp check and the scale write are one step -----------------------------------------
def s2_race():
    import threading
    jid = "deafg"
    seed(jid, 1, {0: 45.0})
    s0, _ = loaded(jid, 0)
    approve(jid, 0, [("Curb", 200.0, "LF")], 50.0, s0)        # the review: 50
    s1, _ = loaded(jid, 0)                                     # two tabs load it: both hold s1
    real = server._page_scale
    inside = threading.Event()

    def slow(job_id, page, res, sent=None, **kw):
        # an injected pause in tab A's approve, between its stamp check and its write (the natural window is 0.24 ms);
        # keywords pass through (the approve now names who: scale verdict S2)
        if sent is not None and threading.current_thread().name == "tabA":
            inside.set()
            time.sleep(0.6)
        return real(job_id, page, res, sent, **kw)

    out = {}
    server._page_scale = slow
    try:
        ta = threading.Thread(name="tabA", target=lambda: out.__setitem__("A", approve(jid, 0, [("Curb", 200.0, "LF")], 40.0, s1)))
        ta.start()
        inside.wait(5)
        tb = threading.Thread(name="tabB", target=lambda: out.__setitem__("B", approve(jid, 0, [("Curb", 200.0, "LF")], 35.0, s1)))
        tb.start()
        ta.join(10)
        tb.join(10)
    finally:
        server._page_scale = real
    a, b = out.get("A") or {}, out.get("B") or {}
    check(a.get("ok") and not b.get("ok") and b.get("code") == 409 and 'changed to 1" = 40 ft' in words(b.get("detail"))
          and review_ft(jid, 0) == 40.0 and export_ft(jid, 0) == 40.0,
          "S2: two tabs with one fresh stamp, tab B's approve landing inside tab A's check-and-write: A's 40 is written, B "
          "(35) is refused - both were accepted, the record at one number and the export at the other: "
          + json.dumps([a.get("ok"), b.get("ok"), words(b.get("detail"))[:90]]))


for _n, _f in (("S2", s2_race), ("B(a)", b_rows), ("B(b)", b_norows), ("B rules", b_rules), ("B confirm", b_confirm), ("B get", b_get),
               ("D", d_removed)):
    section(_n, _f)
print("\nall ok" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
