"""CRITIC-scale-1's reproductions, as tests (out/verify/2026-10-02/scale-verdict/CRITIC-scale-1.md).

B1  a Confirm decided while the dimension check was still pending wrote "confirmed" at 4 on a structural sheet whose
    printed dimensions say 7.93 (FACTS A2 truth 8). Now the decision computes the check first: CONFLICT -> 409, no
    record. Uses the shared Belle Farms Structural Plans sheet (engine page index 0) and its real detection.
M1  a tab opened before a re-detect approved the engine's OLD number (20 after the reading became 25) and it was stored
    as a person's pick. Now the stamp carries the detection: refused, no record. And a number that is none of the
    readings, sent with no `how`, is not recorded.
M2  `how` was whatever the client said. Now the server derives it: a "picked" number that is no reading is `typed`;
    the Cut / fill Scale card's typing is `typed` even when it equals the number in force; its choose control is
    `confirmed` only on a full verdict with that number in force.
Minors: a record without set_by is no person; a cached check with no engine signature is not used; the export words
follow `how`; the stamp is never a constant.

Run:  python tests/test_scale_critic1.py
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
J = Path(tempfile.mkdtemp(prefix="scale_critic1_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(J)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="scale_critic1_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = str(J)
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("CUTFILL_OUT", None)

import pymupdf  # noqa: E402

BELLE = ROOT.parent / "TakeoffPro AI" / "Plans" / "Belle Farms Structural Plans.pdf"
for cand in (BELLE, Path("C:/Users/kobys/OneDrive/Desktop/past/p/Takeoff Automation/TakeoffPro AI/Plans/"
                         "Belle Farms Structural Plans.pdf")):
    if cand.exists():
        BELLE = cand
        break
FAILS = []


def check(cond, msg, got=None):
    print(("ok   " if cond else "FAIL ") + msg + ("" if cond or got is None else f"  [{str(got)[:300]}]"))
    if not cond:
        FAILS.append(msg)


def cap(v, t):
    return {"text": t, "ft_per_paper_inch": v, "near_scale_keyword": True, "count": 1,
            "governs_pt": None, "governs_pct": None, "sites": 1}


# ---- the jobs: B (the real structural sheet, alice's), C (a bar-only sheet, alice's)
reg = {}
if not BELLE.exists():
    print("SKIP-REQUIRED: the shared Belle Farms Structural Plans PDF is not on this machine")
    sys.exit(1)
pdf_b = J / "upload_00000000000b_structural.pdf"
d = pymupdf.open(str(BELLE))
one = pymupdf.open()
one.insert_pdf(d, from_page=0, to_page=0)
one.save(str(pdf_b))
one.close()
d.close()
from takeoff_engine.detect_page import detect_page, engine_sig  # noqa: E402
RES_B = json.loads(json.dumps(detect_page(str(pdf_b), 0)))
RES_B["engine_sig"] = engine_sig()
d = pymupdf.open()
d.new_page(width=612, height=792)
pdf_c = J / "upload_00000000000c_barsheet.pdf"
d.save(str(pdf_c))
d.close()
BAR = {"text": "graphic bar 0-40 ft over 144.0pt (1\" = 20', ticks)", "ft_per_paper_inch": 20.0,
       "near_scale_keyword": True, "count": 1, "governs_pt": None, "governs_pct": None, "sites": 0,
       "source": "graphic-bar", "has_nts": False,
       "candidates": [{"text": "graphic bar 0-40 ft over 144.0pt (1\" = 20', ticks)", "ft_per_paper_inch": 20.0}],
       "bar_ft_per_paper_inch": None, "agrees": None, "delta_pct": None, "pick": {"why": "bar", "bar_source": "native-ticks"}}
RES_C = {"page": {"width_pt": 612, "height_pt": 792}, "chains": [], "styles": [], "scale": BAR, "engine_sig": "c1"}
for jid, pdf, res in (("b" * 12, pdf_b, RES_B), ("c" * 12, pdf_c, RES_C)):
    (J / jid).mkdir()
    (J / jid / "p0_result.json").write_text(json.dumps(res))
    reg[jid] = {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2), "kinds": None,
                "sizes": [[612, 792]], "owner": "alice", "bid": None}
(J / "registry.json").write_text(json.dumps(reg))

import server  # noqa: E402
# THE TAKEOFF STARTS EMPTY (2026-10-03): an approve with nothing in the takeoff is refused, so the approves here that
# are about the scale carry one counted row ("Test count", 1 EA - a count needs no scale).
from takeoff_engine import users as U  # noqa: E402

server._classify_async = lambda *a, **k: None
U.save({"alice": U.hash_password("orchid-lantern-4471"),
        "staff": dict(U.hash_password("granite-sparrow-2210"), staff=True)})
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in ("alice", "staff")}
B, C = "b" * 12, "c" * 12


async def _asgi(method, path, cookie, body=b""):
    headers = [(b"host", b"testserver"), (b"x-core-client", b"3"), (b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode()),
               (b"cookie", f"{server.SESSION_COOKIE}={cookie}".encode())]
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": headers, "client": ("127.0.0.1", 1), "server": ("testserver", 80)}
    sent = {"b": False}
    out = {"s": None, "b": b""}

    async def receive():
        if not sent["b"]:
            sent["b"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(m):
        if m["type"] == "http.response.start":
            out["s"] = m["status"]
        elif m["type"] == "http.response.body":
            out["b"] += m.get("body", b"")
    await server.app(scope, receive, send)
    return out["s"], out["b"]


def call(method, path, who="alice", body=None):
    st, b = asyncio.run(_asgi(method, path, CK[who], json.dumps(body).encode() if body is not None else b""))
    try:
        return st, json.loads(b)
    except ValueError:
        return st, b


def scale(jid):
    st, d = call("GET", f"/api/job/{jid}/page/0/scale")
    assert st == 200, (st, d)
    return d


def record(jid):
    f = J / jid / "p0_scale.json"
    return json.loads(f.read_text()) if f.exists() else None


res25 = json.loads(json.dumps(RES_C))
res25["scale"]["ft_per_paper_inch"] = 25.0
res25["scale"]["text"] = "graphic bar 0-50 ft over 144.0pt (1\" = 25', ticks)"
res25["scale"]["candidates"] = [{"text": res25["scale"]["text"], "ft_per_paper_inch": 25.0}]


def section(name, fn):
    """One reproduction: an exception inside it is its FAIL, and the next section still runs."""
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        check(False, f"{name}: raised {type(e).__name__}: {e}")


def _b1():
    dc = J / B / "p0_dimcheck.json"
    check(not dc.exists() and scale(B)["verdict"]["dimensions"] == "pending",
          "B1 set-up: the real structural sheet, its dimension check NOT run yet (the verdict says pending)")
    g = scale(B)
    st, d = call("POST", f"/api/job/{B}/page/0/scale/confirm",
                 body={"ft_per_paper_inch": 4.0, "how": "confirmed", "scale_seen": g["stamp"]})
    check(st == 409 and record(B) is None and "disagree" in str(d) and "7.9" in str(d),
          "B1: Confirm 4 while the check is pending -> the check is computed first: CONFLICT, 409 naming the choices, "
          "nothing on disk", (st, d))
    check(dc.exists(), "B1: ...and the check it computed is cached for the screen")
    dc.unlink()
    real = server._scale_dimcheck_compute
    server._scale_dimcheck_compute = lambda *a, **k: None
    try:
        st, d = call("POST", f"/api/job/{B}/page/0/scale/confirm",
                     body={"ft_per_paper_inch": 4.0, "how": "confirmed", "scale_seen": scale(B)["stamp"]})
    finally:
        server._scale_dimcheck_compute = real
    # (CRITIC-scale-2: a check that cannot be made resolves to "not confirmed at most"; a Confirm is a measure that
    # agreed, so one sent without a measure is refused whatever the check says)
    check(st == 409 and record(B) is None and "measure" in str(d),
          "B1: a check that cannot be computed: a Confirm with no measure is refused in words, nothing on disk", (st, d))
    st, d = call("POST", f"/api/job/{B}/page/0/scale/confirm",
                 body={"ft_per_paper_inch": 4.0, "how": "confirmed", "scale_seen": scale(B)["stamp"]})
    check(st == 409 and record(B) is None, "B1: after the check has run: still refused with the choices", st)
    dv = [c["value"] for c in scale(B)["verdict"]["choices"] if c["kind"] == "dimensions"][0]
    st, d = call("POST", f"/api/job/{B}/page/0/scale/confirm",
                 body={"ft_per_paper_inch": dv, "how": "picked", "scale_seen": scale(B)["stamp"]})
    check(st == 200 and record(B)["how"] == "picked" and abs(record(B)["ft_per_paper_inch"] - dv) < 1e-9,
          f"B1: a pick of the dimensions' {dv} is stored as picked", (st, record(B)))


def _m1():
    st0 = scale(C)["stamp"]
    check(st0 != "none" and st0.startswith("s"), "M1: the stamp is never the constant 'none'", st0)
    old_stamp = st0
    (J / C / "p0_result.json").write_text(json.dumps(res25))           # the reading moves 20 -> 25 (a re-detect)
    check(scale(C)["stamp"] != old_stamp, "M1: a re-detect that reads another number changes the stamp")
    st, d = call("POST", f"/api/job/{C}/page/0/approve",
                 body={"labels": {}, "extra_items": [{"label": "Test count", "qty": 1, "unit": "EA"}], "scale": {"ft_per_paper_inch": 20.0, "text": "1\" = 20'", "picked_by_reviewer": False},
                       "scale_seen": old_stamp})
    check(st == 409 and record(C) is None,
          "M1: the stale tab's approve of the OLD 20 is refused, and no person's record is written", (st, d))
    st, d = call("POST", f"/api/job/{C}/page/0/approve",
                 body={"labels": {}, "extra_items": [{"label": "Test count", "qty": 1, "unit": "EA"}], "scale": {"ft_per_paper_inch": 33.0, "text": "1\" = 33'", "picked_by_reviewer": True},
                       "scale_seen": scale(C)["stamp"]})
    check(st == 409 and record(C) is None,
          "M1: an approve of a number that is none of the readings, with no how, records nothing (409)", (st, d))


def _m2():
    st, d = call("POST", f"/api/job/{C}/page/0/approve",
                 body={"labels": {}, "extra_items": [{"label": "Test count", "qty": 1, "unit": "EA"}], "scale": {"ft_per_paper_inch": 33.0, "text": "1\" = 33'", "picked_by_reviewer": True,
                                               "how": "picked"}, "scale_seen": scale(C)["stamp"]})
    check(st == 200 and record(C)["how"] == "typed",
          "M2: an approve claiming 'picked' for a number no reading made is recorded typed", (st, record(C)))
    st, d = call("POST", f"/api/job/{C}/page/0/scale/confirm",
                 body={"ft_per_paper_inch": 34.0, "how": "picked", "scale_seen": scale(C)["stamp"]})
    check(st == 200 and record(C)["how"] == "typed" and record(C)["ft_per_paper_inch"] == 34.0,
          "M2: the confirm route stores a 'pick' that is no reading as typed", record(C))
    st, d = call("POST", f"/api/job/{C}/page/0/approve",
                 body={"labels": {}, "extra_items": [{"label": "Test count", "qty": 1, "unit": "EA"}], "scale": {"ft_per_paper_inch": 25.0, "text": "1\" = 25'", "picked_by_reviewer": False},
                       "scale_seen": scale(C)["stamp"]})
    check(st == 200 and (record(C) or {}).get("retired") is True and scale(C)["verdict"]["person"] is None,
          "an approve of the engine's own 25 over a person's 34 is the go-back: a tombstone, no person (ruling 10)",
          record(C))
    # a sheet that also prints a 1" = 5' caption: approving that reading records it as picked
    res5 = json.loads(json.dumps(res25))
    res5["scale"] = {**cap(25.0, "1 in = 25 ft"), "source": "text", "has_nts": False,
                     "candidates": [cap(25.0, "1 in = 25 ft"), cap(5.0, "1 in = 5 ft")], "bar_ft_per_paper_inch": None,
                     "agrees": None, "delta_pct": None, "pick": {"why": "governs"}}
    (J / C / "p0_result.json").write_text(json.dumps(res5))
    st, d = call("POST", f"/api/job/{C}/page/0/approve",
                 body={"labels": {}, "extra_items": [{"label": "Test count", "qty": 1, "unit": "EA"}], "scale": {"ft_per_paper_inch": 5.0, "text": "1\" = 5'", "picked_by_reviewer": True},
                       "scale_seen": scale(C)["stamp"]})
    check(st == 200 and record(C)["how"] == "picked", "M2: an approve of another printed reading (5) is picked", record(C))
    (J / C / "p0_result.json").write_text(json.dumps(res25))
    st, d = call("POST", f"/api/job/{C}/page/0/approve",
                 body={"labels": {}, "extra_items": [{"label": "Test count", "qty": 1, "unit": "EA"}], "scale": {"ft_per_paper_inch": 26.6667, "text": "x", "picked_by_reviewer": True,
                                               "how": "calibrated"}, "scale_seen": scale(C)["stamp"]})
    check(st == 200 and record(C)["how"] == "calibrated", "M2: calibrated is taken from the page", record(C))
    # the Cut / fill Scale card: typed is typed; its choose control may confirm only on a full verdict
    from cutfill import placement as cfpl  # noqa: E402
    fake = type("P", (), {})()
    server._cf_sheet_job_page = (lambda project, key, write=False: (C, 0))
    (J / C / "p0_scale.json").unlink()
    server._PICK_LAST.pop(str(J / C / "p0_scale.json"), None)
    tok = server._CF_CALLER.set(("alice", False))
    try:
        ok = server._cf_review_scale_set(fake, "d0p0", 25.0, "1\" = 25'")
        check(ok is True and record(C)["how"] == "typed",
              "M2: the Scale card's TYPING of the number in force is typed (it was 'confirmed')", record(C))
        (J / C / "p0_scale.json").unlink()
        server._PICK_LAST.pop(str(J / C / "p0_scale.json"), None)
        t2 = cfpl.REVIEW_SET_CHOSEN.set(True)
        try:
            ok = server._cf_review_scale_set(fake, "d0p0", 25.0, "graphic bar")
        finally:
            cfpl.REVIEW_SET_CHOSEN.reset(t2)
        check(ok is True and record(C)["how"] == "accepted",
              "M2 / CRITIC-scale-2 M1: the Scale card's CHOOSE control on the number in force is a click, not a "
              "measure -> accepted", record(C))
    finally:
        server._CF_CALLER.reset(tok)


def _minors():
    (J / C / "p0_scale.json").write_text(json.dumps({"ft_per_paper_inch": 40.0, "how": "confirmed"}))
    server._PICK_LAST.pop(str(J / C / "p0_scale.json"), None)
    v = scale(C)["verdict"]
    check(v["person"] is None and scale(C)["reviewer"] is None,
          "MINOR 3: a hand-edited record without set_by is no person's record", v)
    (J / C / "p0_scale.json").unlink()
    server._PICK_LAST.pop(str(J / C / "p0_scale.json"), None)
    nosig = json.loads(json.dumps(RES_C))
    nosig.pop("engine_sig")
    (J / C / "p0_result.json").write_text(json.dumps(nosig))
    (J / C / "p0_dimcheck.json").write_text(json.dumps({"key": {"scale": 99.0, "engine_sig": None},
                                                        "check": {"witnesses": [{}] * 4, "witness_consensus_n": 4,
                                                                  "witness_consensus_ft_per_in": 20.1}}))
    v = scale(C)["verdict"]
    check(v["dimensions"] == "pending", "MINOR 6: a cached check with no engine signature is never used", v)
    for how, words in (("typed", "typed by the reviewer"), ("calibrated", "set by measuring a known length"),
                       ("picked", "chosen by the reviewer from the sheet's readings")):
        nm = server._scale_name({"set_by": "reviewer", "set_how": how, "ft_per_paper_inch": 25.0, "text": "1\" = 25'",
                                 "engine_ft_per_paper_inch": 20.0, "engine_text": "bar"})
        check(f"({words};" in nm, f"M2: the export says '{words}' for {how}", nm)
    nm = server._scale_name({"set_by": "reviewer", "ft_per_paper_inch": 25.0, "text": "1\" = 25'",
                             "engine_ft_per_paper_inch": 20.0, "engine_text": "bar"})
    check("(set by the reviewer;" in nm, "a record from before `how` keeps the old words", nm)
    lines = server._scale_report_lines({"setBy": "reviewer", "setHow": "typed", "ftPerPaperInch": 25.0,
                                        "engineFtPerPaperInch": 20.0, "engineText": "bar", "name": "x"})
    check("TYPED BY THE REVIEWER" in lines[0], "the report says typed", lines)


section("B1 (Confirm decided on a pending check)", _b1)
# M1 and M2 start from the bar-only sheet at 20 with no record (the B1 section touches only the structural sheet)
section("M1 (a stale tab's approve of the old reading)", _m1)
section("M2 (how is derived on the server)", _m2)
section("minors", _minors)

shutil.rmtree(J, ignore_errors=True)
print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
