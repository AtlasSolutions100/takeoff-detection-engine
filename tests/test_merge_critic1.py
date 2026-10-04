"""WHAT THE MERGE CRITIC FOUND, THE SERVER HALF (CRITIC-merge-1, 2026-10-04): its smaller findings, one test each,
each failing on be46b84. (The account file - MAJOR-1 and the finding older than the merge - is
tests/test_account_file.py; the page half is tests/test_merge_critic1_ui.js.)

  M2  a restart while an e-mailed plan is being opened: the project the message was opening is FINISHED when the
      message is read again - its line says "opened", its card says where it came from, Detect runs on it - instead
      of "already one of your projects; nothing new was opened". The same when the file is forwarded again later.
  M3  spooled attachments that belong to no message (a door stopped before it answered) go at the next start
      whatever their age, when a message is recorded, and in the reader's hourly pass - not only at some later start
      once they are ten minutes old. What a door is taking in at this moment is never touched.
  M4  the record of approvals taken out of the bid is bounded: the newest three, and the one approval a put back
      would restore however old it is. Put back still works after any number of withdrawals.
  M5  the server says whether bids by e-mail is on in two replies the page already reads (GET /api/config,
      GET /api/projects), so the page need not ask a route that answers 404
  M6  "2 different panel thicknesses", not "thicknesss"

Every name, address and word here is made up; the PDFs are built here.

    python tests/test_merge_critic1.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
_JOBS = Path(tempfile.mkdtemp(prefix="merge_c1_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(_JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="merge_c1_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = str(_JOBS)
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("CUTFILL_OUT", None)
for _k in ("INBOUND_HOOK_USER", "INBOUND_HOOK_PASS", "INBOUND_ADDRESS"):
    os.environ.pop(_k, None)

import pymupdf  # noqa: E402

ALICE, BOB = "alice@builder.example", "bob@rival.example"


def result(name):
    st = {"style_id": 1, "suggested_label": name, "class": "solid", "layer": "C-" + name, "color": [0, 0, 0],
          "width": 1.0, "signature": "solid", "length_pt": 300.0, "n": 1, "texture": False}
    ch = {"chain_id": 1, "style_id": 1, "class": "solid", "color": [0, 0, 0], "width": 1.0, "layer": st["layer"],
          "length_pt": 300.0, "points": [[72.0, 100.0], [372.0, 100.0]]}
    return {"page": {"width_pt": 612, "height_pt": 792}, "styles": [st], "chains": [ch], "engine_sig": "s",
            "scale": {"ft_per_paper_inch": 20.0, "text": "1\" = 20'", "source": "text"}}


# a job with two detected pages, for the record of approvals (seeded the way tests/test_withdraw_approval.py does)
JID = "mc1w00000001"
_d = pymupdf.open()
for _ in range(2):
    _d.new_page(width=612, height=792)
_pdf = _JOBS / "upload_000000000001_two-pages.pdf"
_d.save(str(_pdf))
_d.close()
(_JOBS / JID).mkdir()
(_JOBS / JID / "p0_result.json").write_text(json.dumps(result("CURB")))
(_JOBS / JID / "p1_result.json").write_text(json.dumps(result("WALK")))
(_JOBS / "registry.json").write_text(json.dumps({JID: {"path": str(_pdf), "pages": 2, "mtime": round(_pdf.stat().st_mtime, 2),
                                                       "kinds": None, "sizes": [[612, 792]] * 2, "owner": ALICE, "bid": None}}))

import _inbound_harness as H  # noqa: E402
import server  # noqa: E402
from takeoff_engine import inbound as I  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402
from takeoff_engine import wp_job as W  # noqa: E402
from takeoff_engine import wp_settings as S  # noqa: E402

SORTED = []                                          # every call that starts the sorting of a project's pages
server._classify_async = lambda *a, **k: SORTED.append(a[0])
server._INBOUND_KINDS_WAIT_S = 0
H.install_fake_children(server)
_users.save({ALICE: _users.hash_password("orchid-lantern-4471", iterations=1000),
             BOB: _users.hash_password("basalt-meadow-9083", iterations=1000)})
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in (ALICE, BOB)}
D = _JOBS / JID
SPOOL = _JOBS / "inbound" / "spool"

fails = 0
checks = 0


def check(ok, msg, got=None):
    global fails, checks
    checks += 1
    print(("ok: " if ok else "FAIL: ") + msg + ("" if ok or got is None else f"  (got {str(got)[:400]})"))
    if not ok:
        fails += 1


def run(fn):
    try:
        fn()
    except Exception as e:                           # noqa: BLE001 - on be46b84 some tests land here
        check(False, f"{fn.__name__}: raised {type(e).__name__}: {str(e)[:200]}")


def call(method, path, body=None, who=ALICE, **kw):
    return H.call(server, method, path, body, cookie=CK[who] if who else None, **kw)


def on():
    os.environ.update({"INBOUND_HOOK_USER": H.HOOK_USER, "INBOUND_HOOK_PASS": H.HOOK_PASS, "INBOUND_ADDRESS": H.BASE_ADDRESS})


def off():
    for k in ("INBOUND_HOOK_USER", "INBOUND_HOOK_PASS", "INBOUND_ADDRESS"):
        os.environ.pop(k, None)


def read_all():
    server._INBOUND_Q.join()


def inbox(who=ALICE):
    return call("GET", "/api/inbound", who=who)[1]


def my_tag(who=ALICE):
    j = inbox(who)
    addr = j.get("address") or call("POST", "/api/inbound/address", {}, who=who)[1]["address"]
    return addr.split("+", 1)[1].split("@", 1)[0]


def post(tag, files, subject):
    return H.call(server, "POST", "/api/inbound/postmark", H.payload(tag, files, subject=subject), auth=H.basic(), marker=False)


def entry(subject, who=ALICE):
    return next((e for e in inbox(who).get("entries") or [] if e["subject"] == subject), None)


def line(e, name):
    return next((f for f in (e or {}).get("files") or [] if f["name"] == name), None)


def projects_named(name, who=ALICE):
    return [r for r in call("GET", "/api/projects", who=who)[1].get("projects", []) if r["name"] == name]


class Stop(BaseException):
    """The server stops here: nothing after this line of the read happens, and nothing handles it."""


def restart():
    """What a new process has: the registry and the stores on disk, nothing in memory, and its start-up resume."""
    server._INBOUND_THREAD.clear()
    with server.JOBS_LOCK:
        server.JOBS.clear()
    server._load_registry()
    server._INBOUND_STARTED = time.time()
    server._inbound_resume()
    read_all()
    H.drain(server)


# =====================================================================================================================
def m2_a_restart_while_a_plan_is_being_opened():
    on()
    tag = my_tag()
    first = H.drawing_pdf(2, title="M2 FIRST")
    real_wait, real_hook = server._inbound_wait_kinds, threading.excepthook

    def dies(jid):
        raise Stop()

    server._inbound_wait_kinds = dies                # ... while the first plan's pages are being sorted
    threading.excepthook = lambda a: None
    try:
        st, _ = post(tag, [("m2-first.pdf", first), ("m2-second.pdf", H.drawing_pdf(1, title="M2 SECOND"))], "M2 stopped mid-read")
        read_all()
    finally:
        server._inbound_wait_kinds, threading.excepthook = real_wait, real_hook
    e = entry("M2 stopped mid-read")
    jids = [jid for jid, j in server.JOBS.items() if j.get("owner") == ALICE and str(j.get("path", "")).endswith("_m2-first.pdf")]
    check(st == 200 and e is not None and e["status"] == "received" and len(jids) == 1 and line(e, "m2-first.pdf") is None,
          "m2: (the server stopped while the first plan was being opened: its project is registered, the message is "
          "still to be read)", (st, e and e["status"], jids))
    jid = jids[0] if jids else None
    SORTED.clear()
    restart()
    e = entry("M2 stopped mid-read")
    f1, f2 = line(e, "m2-first.pdf"), line(e, "m2-second.pdf")
    check(e is not None and e["status"] == "done" and f1 is not None and f1["state"] == "opened" and f1.get("job") == jid,
          "m2: after the restart the message is read again and the plan it was opening reads OPENED, as the same "
          "project (it read 'already one of your projects; nothing new was opened')", f1)
    check(f2 is not None and f2["state"] == "opened", "m2: ...and the plan it had not reached is opened too", f2)
    rows = projects_named("m2-first.pdf")
    check(len(rows) == 1 and rows[0]["job_id"] == jid and isinstance(rows[0].get("inbound"), dict)
          and rows[0]["inbound"].get("subject") == "M2 stopped mid-read",
          "m2: one project for it, and its card says which e-mail it came in (it had no note)", rows)
    st, kw = call("GET", f"/api/job/{jid}/keywords")
    check(st == 200 and kw.get("from_email") is True, "m2: ...the project knows it came by e-mail", kw)
    auto = (f1 or {}).get("auto") or {}
    check(auto.get("queued", 0) >= 1 and auto.get("done") == auto.get("queued")
          and len(list((_JOBS / str(jid)).glob("p*_result.json"))) == auto.get("queued"),
          "m2: ...and Detect ran on its sheets by itself (it never did: the page stayed idle)", auto)
    check(jid in SORTED, "m2: ...after its pages were sorted again (the sorting died with the server)", SORTED)
    reg = json.loads((_JOBS / "registry.json").read_text())
    check("inbound_opening" not in reg.get(jid, {}) and reg.get(jid, {}).get("inbound", {}).get("entry") is not None,
          "m2: the mark 'an e-mail is opening this' is gone from the registry once the note is written", sorted(reg.get(jid, {})))

    # the same file forwarded AGAIN after an opening that was never finished (the message was given up)
    again = H.drawing_pdf(1, title="M2 AGAIN")
    server._inbound_wait_kinds = dies
    threading.excepthook = lambda a: None
    try:
        post(tag, [("m2-again.pdf", again)], "M2 given up")
        read_all()
    finally:
        server._inbound_wait_kinds, threading.excepthook = real_wait, real_hook
    server._INBOUND_THREAD.clear()
    eid = next(x["id"] for x in I.entries(ALICE, limit=5) if x["subject"] == "M2 given up")
    server._inbound_failed(ALICE, eid, server._InboundStopped())      # what the reader does after the server stopped twice
    post(tag, [("m2-again.pdf", again)], "M2 forwarded again")
    read_all()
    H.drain(server)
    f = line(entry("M2 forwarded again"), "m2-again.pdf")
    rows = projects_named("m2-again.pdf")
    check(f is not None and f["state"] == "opened" and len(rows) == 1 and isinstance(rows[0].get("inbound"), dict)
          and rows[0]["inbound"].get("subject") == "M2 forwarded again",
          "m2: a file forwarded again after an opening that never finished is opened, not called 'already one of your "
          "projects'", (f, [r.get("inbound") for r in rows]))
    H.drain(server)


# =====================================================================================================================
def leftover(age_s: float) -> Path:
    """Attachments of a message whose door was stopped before it answered: a spool folder with NO manifest, made
    `age_s` seconds ago (a negative age: a message a door is taking in at this moment, a little in the future)."""
    eid = I.new_entry(ALICE, {"id": f"m3-{age_s}-{time.time()}", "from": "x", "subject": "s", "attachments": [],
                              "attached": 0}, time.time() - age_s)["id"]
    I.spool_add(eid, 0, b"%PDF-1.4 made-up attachment")
    return SPOOL / eid


def m3_spool_left_overs():
    on()
    tag = my_tag()
    read_all()
    crashed = leftover(5)                            # the door was stopped five seconds before the restart
    restart()
    arriving = leftover(-2)                          # ... and a message a door of the NEW process is taking in
    server._inbound_resume()
    check(not crashed.exists(),
          "m3: at a start, what a stopped door left goes whatever its age (it stayed until a LATER start: it was "
          "not yet ten minutes old)", sorted(p.name for p in SPOOL.iterdir()) if SPOOL.exists() else [])
    check(arriving.exists(), "m3: ...and what a door of this process is taking in at that moment is not touched")
    old, fresh = leftover(660), leftover(0)
    st, _ = post(tag, [("m3-plan.pdf", H.drawing_pdf(1, title="M3 PLAN"))], "M3 a message is recorded")
    read_all()
    H.drain(server)
    check(st == 200 and not old.exists(),
          "m3: while the service runs, a left-over older than ten minutes goes when a message is recorded (it "
          "stayed until a restart)")
    check(fresh.exists() and arriving.exists(), "m3: ...a folder made a moment ago is left alone (a door may be writing it)")
    old = leftover(660)
    idle = getattr(server, "_inbound_idle", None)
    if idle is not None:
        idle()
    check(idle is not None and not old.exists() and fresh.exists(),
          "m3: the reader's hourly pass clears such a left-over too (it swept the held plans only)")
    for p in (fresh, arriving):
        I.spool_drop(p.name)


# =====================================================================================================================
def takeoff(page, sids):
    tk = call("GET", f"/api/job/{JID}/page/{page}/ticks")[1]
    if sorted(sids) != tk.get("styles"):
        tk = call("POST", f"/api/job/{JID}/page/{page}/ticks", {"version": tk["version"], "styles": sorted(sids)})[1]
    return tk["version"]


def approve(page, label):
    stamp = call("GET", f"/api/job/{JID}/page/{page}/scale")[1]["stamp"]
    return call("POST", f"/api/job/{JID}/page/{page}/approve",
                {"labels": {"1": label}, "scale": None, "scale_seen": stamp, "ticks_version": takeoff(page, [1])})


def record(page):
    f = D / f"p{page}_withdrawn.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def m4_the_record_of_approvals_is_bounded():
    keep = getattr(server, "WITHDRAWN_KEEP", None)
    labels = []
    for n in range(8):                               # page 1 approved and withdrawn eight times
        labels.append(f"CURB {n}")
        ok = approve(0, labels[-1])[0] == 200 and call("POST", f"/api/job/{JID}/page/0/withdraw")[0] == 200
        if not ok:
            check(False, f"m4: (approve and withdraw #{n + 1})")
            return
    rec = record(0)
    check(keep == 3 and len(rec) == 3,
          "m4: after eight withdrawals the record holds the newest THREE approvals taken out of the bid (it held all "
          "eight, each with its whole export)", (keep, len(rec)))
    kept = [e["export"]["items"][0]["label"] for e in rec if isinstance(e.get("export"), dict) and e["export"].get("items")]
    check(kept == labels[-3:] and all(e.get("who") == ALICE and e.get("at") for e in rec),
          "m4: ...the last three, oldest first, each with who, when and its export", kept)
    size8 = (D / "p0_withdrawn.json").stat().st_size
    approve(0, "CURB 8")
    call("POST", f"/api/job/{JID}/page/0/withdraw")
    check(abs((D / "p0_withdrawn.json").stat().st_size - size8) < 200,
          "m4: ...and one more withdrawal does not make the record bigger", ((D / "p0_withdrawn.json").stat().st_size, size8))

    # an approval made on an EARLIER VERSION (its export carries no mark), withdrawn: the one a put back restores
    approve(1, "WALK")
    f = D / "p1_elements.json"
    exp = json.loads(f.read_text())
    exp.pop(server.EXPORT_ADDED_KEY, None)
    f.write_text(json.dumps(exp, indent=1))
    earlier = f.read_bytes()
    ap = call("GET", f"/api/job/{JID}/approved")[1]
    st, _ = call("POST", f"/api/job/{JID}/page/1/withdraw")
    check(st == 200 and "1" in (ap.get("earlier") or {}) and "1" in (call("GET", f"/api/job/{JID}/approved")[1].get("kept") or {}),
          "m4: (an approval made on an earlier version is withdrawn: the page offers to put it back)")
    for n in range(5):                               # ... then five approvals of this build, each withdrawn
        approve(1, f"WALK {n}")
        call("POST", f"/api/job/{JID}/page/1/withdraw")
    rec = record(1)
    marks = [bool(e["export"].get(server.EXPORT_ADDED_KEY)) for e in rec]
    check(len(rec) == 4 and marks == [False, True, True, True],
          "m4: five withdrawals later the record holds the newest three AND the earlier version's approval, which a "
          "put back would restore (it held all six)", (len(rec), marks))
    ap = call("GET", f"/api/job/{JID}/approved")[1]
    st, j = call("POST", f"/api/job/{JID}/page/1/putback")
    check("1" in (ap.get("kept") or {}) and st == 200 and f.exists() and f.read_bytes() == earlier,
          "m4: Put back is still offered and puts that approval back in the bid byte for byte", (st, j))
    check(len(record(1)) <= 4 and call("POST", f"/api/job/{JID}/page/1/putback")[0] == 404,
          "m4: ...the record stays bounded after it, and there is nothing more to put back", len(record(1)))
    call("POST", f"/api/job/{JID}/page/1/withdraw")


# =====================================================================================================================
def m5_the_page_is_told_whether_bids_by_email_is_on():
    off()
    cfg, prj = call("GET", "/api/config")[1], call("GET", "/api/projects")[1]
    check(cfg.get("inbound") is False and prj.get("inbound") is False and call("GET", "/api/inbound")[0] == 404,
          "m5: bids by e-mail OFF: GET /api/config and GET /api/projects both say so (the page asked /api/inbound to "
          "find out, and got a 404 in its console on every load)", (cfg.get("inbound"), prj.get("inbound")))
    on()
    cfg, prj = call("GET", "/api/config")[1], call("GET", "/api/projects")[1]
    check(cfg.get("inbound") is True and prj.get("inbound") is True and call("GET", "/api/inbound")[0] == 200,
          "m5: ON: both say so, and the list answers", (cfg.get("inbound"), prj.get("inbound")))


# =====================================================================================================================
def m6_the_plural():
    rec, bad = S.clean({"sizes": ["4x8"], "scope_template": "Panels {thickness} thick, rated {fire_rating}."})
    d = W.default_doc()
    for a in ({"kind": "room_add", "name": "A"}, {"kind": "room_add", "name": "B"}):
        W.apply_act(d, a, "tester", "2026-10-04 09:00", rec)
    ra, rb = d["rooms"][0]["id"], d["rooms"][1]["id"]
    for rid, fld, v in ((ra, "thickness", '.090"'), (rb, "thickness", '.120"'), (ra, "fire", "Class A"), (rb, "fire", "Class C")):
        W.apply_act(d, {"kind": "room_set", "id": rid, "field": fld, "value": v}, "tester", "2026-10-04 09:00", rec)
    bl = {x["name"]: x for x in W.compute(d, rec, company="Example Co")["scope"]["blanks"]}
    check(not bad and bl["thickness"]["by"] == "from the rooms (2 different panel thicknesses)",
          "m6: two rooms, two thicknesses: '2 different panel thicknesses' (it printed 'thicknesss')", bl["thickness"]["by"])
    check(bl["fire_rating"]["by"] == "from the rooms (2 different fire ratings)", "m6: ...and the other words are as they were",
          bl["fire_rating"]["by"])


for fn in (m4_the_record_of_approvals_is_bounded, m5_the_page_is_told_whether_bids_by_email_is_on, m6_the_plural,
           m2_a_restart_while_a_plan_is_being_opened, m3_spool_left_overs):
    run(fn)

print(f"\n{checks} checks, {fails} failed")
print(f"{'PASS' if not fails else 'FAIL'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
