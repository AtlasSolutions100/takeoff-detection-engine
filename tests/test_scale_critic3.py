"""CRITIC-scale-3's findings, as tests (out/verify/2026-10-02/scale-verdict/CRITIC-scale-3.md). They fail on 9058370
and pass now.

M1  "Keep" over a disagreeing measure turned green on a page where caption and bar agree. Now the person's own measure
    is evidence: an `accepted` record whose measure disagrees is not confirmed whatever the witnesses say, and is
    flagged; one whose measure agrees is confirmed. All four corners (one reading / two kinds x agrees / disagrees).
M2  A counts-only approve on a disagreeing page was OUT OF DATE and printed the withdrawn 4. Now an approval with no
    scale is never out of date for a scale reason, and a withdrawn reading is not a change.
M3  The dimension check runs once per detection: at the end of Detect, and single-flight on the lazy path.
Minors: 1 /scale/at with no scale in force; 2 the server's scale decides (rooms/pick, runs); 3 an empty unit is not a
count; 4 the Scale card with no stamp; 5 the measure is computed by the server from the points; 6 the gate's page_index;
8 one scale line in the report; 9 cut / fill compute checks every sheet.

Run:  python tests/test_scale_critic3.py
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
J = Path(tempfile.mkdtemp(prefix="scale_critic3_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(J)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="scale_critic3_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = str(J)
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("CUTFILL_OUT", None)

import pymupdf  # noqa: E402

FAILS = []


def check(cond, msg, got=None):
    print(("ok   " if cond else "FAIL ") + msg + ("" if cond or got is None else f"  [{str(got)[:400]}]"))
    if not cond:
        FAILS.append(msg)


def cap(v, t):
    return {"text": t, "ft_per_paper_inch": v, "near_scale_keyword": True, "count": 1,
            "governs_pt": None, "governs_pct": None, "sites": 1}


CHAIN = {"chain_id": 1, "style_id": 1, "points": [[100, 100], [388, 100]], "length_pt": 288.0, "class": "solid",
         "color": [0, 0, 0]}
BASE = {"page": {"width_pt": 612, "height_pt": 792}, "chains": [CHAIN], "styles": [{"style_id": 1}],
        "engine_sig": "t3"}
R_DIS = dict(BASE, scale={**cap(4.0, '1/4" - 1\'-0"'), "source": "text", "has_nts": True,
                          "candidates": [cap(4.0, '1/4" - 1\'-0"')],
                          "bar_ft_per_paper_inch": 3.965, "agrees": True, "delta_pct": 0.9,
                          "pick": {"why": "governs", "bar_source": "native-ticks"}})
DIM_DIS = {"verdict": "disagrees", "witnesses": [{}] * 7, "witness_consensus_n": 5,
           "witness_consensus_ft_per_in": 7.93}
R_ONE = dict(BASE, scale={**cap(20.0, '1" = 20\''), "source": "text", "has_nts": False,
                          "candidates": [cap(20.0, '1" = 20\'')], "bar_ft_per_paper_inch": None,
                          "agrees": None, "delta_pct": None, "pick": {"why": "single"}})
R_AGREE = dict(BASE, scale={**cap(20.0, '1" = 20\''), "source": "text", "has_nts": False,
                            "candidates": [cap(20.0, '1" = 20\'')], "bar_ft_per_paper_inch": 20.0,
                            "agrees": True, "delta_pct": 0.0, "pick": {"why": "single", "bar_source": "native-ticks"}})
NO_DIM = {"verdict": "no-witnesses", "witnesses": []}

reg, JOB = {}, {}
for i, (k, res, dim) in enumerate((("dis", R_DIS, DIM_DIS), ("one", R_ONE, NO_DIM), ("agree", R_AGREE, NO_DIM),
                                    ("dis2", R_DIS, DIM_DIS), ("fresh", R_AGREE, None), ("flight", R_AGREE, None))):
    d = pymupdf.open()
    d.new_page(width=612, height=792)
    pdf = J / ("upload_%012x_%s.pdf" % (i + 1, k))
    d.save(str(pdf))
    d.close()
    jid = (k[0] * 11 + str(i))[:12]
    (J / jid).mkdir()
    (J / jid / "p0_result.json").write_text(json.dumps(res))
    if dim is not None:
        (J / jid / "p0_dimcheck.json").write_text(json.dumps({"key": {"scale": None, "engine_sig": "t3"}, "check": dim}))
    reg[jid] = {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2), "kinds": None,
                "sizes": [[612, 792]], "owner": "alice", "bid": None}
    JOB[k] = jid
(J / "registry.json").write_text(json.dumps(reg))

import server  # noqa: E402
# THE TAKEOFF STARTS EMPTY (2026-10-03): an approve with nothing in the takeoff is refused, so the approves here that
# are about the scale carry one counted row ("Test count", 1 EA - a count needs no scale).

for f in J.glob("*/p*_dimcheck.json"):
    c = json.loads(f.read_text())
    c["key"]["detection"] = server._scale_detection_digest_of(json.loads((f.parent / "p0_result.json").read_text()))
    f.write_text(json.dumps(c))
from takeoff_engine import users as U  # noqa: E402
from takeoff_engine import scale_verdict as SV  # noqa: E402

server._classify_async = lambda *a, **k: None
U.save({"alice": U.hash_password("lichen-harbor-3390")})
server._UI_PASSWORD = None
CK = server._sign_session("alice")


async def _asgi(method, path, body=b""):
    path, _, qs = path.partition("?")
    headers = [(b"host", b"testserver"), (b"x-core-client", b"3"), (b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode()), (b"cookie", f"{server.SESSION_COOKIE}={CK}".encode())]
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": qs.encode(),
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


def call(method, path, body=None):
    st, b = asyncio.run(_asgi(method, path, json.dumps(body).encode() if body is not None else b""))
    try:
        return st, json.loads(b)
    except ValueError:
        return st, b


def stamp(jid):
    return call("GET", f"/api/job/{jid}/page/0/scale")[1]["stamp"]


def takeoff(jid, labels):
    """As the page: the line styles about to be approved are ADDED to the page's takeoff first (it starts empty); the
    answer is the version of the takeoff record, which the approve names (the server holds the approve to it)."""
    tk = call("GET", f"/api/job/{jid}/page/0/ticks")[1]
    want = sorted(int(k) for k in (labels or {}))
    if want != tk.get("styles"):
        tk = call("POST", f"/api/job/{jid}/page/0/ticks", {"version": tk["version"], "styles": want})[1]
    return tk["version"]


def record(jid):
    f = J / jid / "p0_scale.json"
    return json.loads(f.read_text()) if f.exists() else None


def forget(jid):
    for n in ("p0_scale.json", "p0_elements.json"):
        (J / jid / n).unlink(missing_ok=True)
    server._PICK_LAST.pop(str(J / jid / "p0_scale.json"), None)


def measure(true_ft):
    """Two points one paper inch apart (72 pt) and the length typed: true_ft ft per inch."""
    return {"points": [[100.0, 100.0], [172.0, 100.0]], "true_ft": true_ft}


def section(name, fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(False, f"{name}: raised {type(e).__name__}: {e}")


W1 = [{"kind": "caption", "value": 20.0, "in_force": True}]
W2 = W1 + [{"kind": "bar", "value": 20.0, "in_force": True}]


# ================================================================ M1: the person's measure is evidence
def _m1():
    for wname, ws in (("one reading", W1), ("caption + bar agree", W2)):
        for mname, mft, want in (("agrees", 20.2, "CONFIRMED"), ("disagrees", 30.0, "UNCHECKED")):
            v = SV.verdict(ws, {"value": 20.0, "how": "accepted", "who": None, "when": "2026-10-02T10:00:00",
                                "measure": {"ft": mft, "at": "2026-10-02T10:00:00"}})
            check(v["state"] == want and v["person"]["measure_agrees"] is (mname == "agrees"),
                  f"M1 pure: {wname}, accepted, the person's measure {mname} -> {want}", (v["state"], v["why"]))
    v = SV.verdict(W2, {"value": 20.0, "how": "accepted", "who": None, "when": None})
    check(v["state"] == "CONFIRMED", "M1 pure: bare accepted (no measure) where two kinds already confirm stays confirmed")
    v = SV.verdict(W1, {"value": 20.0, "how": "accepted", "who": None, "when": None})
    check(v["state"] == "UNCHECKED", "M1 pure: bare accepted on one reading stays not confirmed")
    v = SV.verdict(W2, {"value": 20.0, "how": "accepted", "who": None, "when": None,
                        "measure_disagreed": {"ft": 30.0, "at": "2026-10-02T10:00:00"}})
    check(v["state"] == "UNCHECKED", "M1 pure: a record written by the last round (measure_disagreed only) is read too")
    # the route, on the two-kinds page (the critic's C4.0 shape)
    G = JOB["agree"]
    forget(G)
    st, d = call("POST", f"/api/job/{G}/page/0/scale/confirm",
                 {"ft_per_paper_inch": 20.0, "how": "accepted", "scale_seen": stamp(G), "measure": measure(30.0)})
    check(st == 200 and d["verdict"]["state"] == "UNCHECKED" and d["verdict"]["person"]["measure_agrees"] is False,
          "M1: Keep 20 over a measure of 30 on a caption + bar page -> not confirmed", (st, str(d)[:300]))
    st, d = call("POST", f"/api/job/{G}/page/0/approve", {"labels": {"1": "Pipe"}, "image_dpi": 300, "scale": None,
                                                          "scale_seen": stamp(G), "ticks_version": takeoff(G, {"1": "Pipe"})})
    exp = json.loads((J / G / "p0_elements.json").read_text())
    flags = server._scale_flag_words(G, [0])
    check(st == 200 and len(flags) == 1 and flags[0].startswith("Scale accepted on page 1; a measure on ")
          and flags[0].endswith("said 1 in = 30 ft"), "M1: the bid sheet / report flag names the measure", flags)
    check("a measure on" in exp["scale"]["name"] and "without measuring" not in exp["scale"]["name"]
          and exp["scale"]["state"] == "UNCHECKED" and exp["scale"]["setMeasure"]["agrees"] is False
          and "alice" not in json.dumps(exp),
          "M1: the export: not confirmed, the measure named and machine-readable, no name", exp["scale"])
    check("a measure on" in exp["scaleVerdict"]["words"] and "said 1 in = 30 ft" in exp["scaleVerdict"]["words"],
          "M1: scaleVerdict words", exp["scaleVerdict"]["words"])
    st, b = asyncio.run(_asgi("GET", f"/api/job/{G}/report.pdf"))
    doc = pymupdf.open(stream=b, filetype="pdf")
    flat = " ".join(" ".join(p.get_text() for p in doc).split())
    doc.close()
    check(st == 200 and "SAID 1 IN = 30 FT." in flat and "Scale accepted on this page; a measure on" in flat,
          "M1: the report says it, wrapped and whole (the live run cut it at '... SAID 1 I')", flat[:400])
    # a later measure that agrees clears it
    st, d = call("POST", f"/api/job/{G}/page/0/scale/confirm",
                 {"ft_per_paper_inch": 20.0, "how": "confirmed", "scale_seen": stamp(G), "measure": measure(20.1)})
    check(st == 200 and d["verdict"]["state"] == "CONFIRMED" and server._scale_flag_words(G, [0]) == [],
          "M1: a later measure that agrees confirms it; the flag clears", (st, server._scale_flag_words(G, [0])))


# ================================================================ M2: OUT OF DATE never on the verdict alone
def _m2():
    Dj = JOB["dis"]
    forget(Dj)
    check(server._page_scale(Dj, 0, R_DIS) is None, "M2 set-up: readings disagree, nothing in force")
    st, d = call("POST", f"/api/job/{Dj}/page/0/approve", {"labels": {}, "extra_items": [{"label": "Test count", "qty": 1, "unit": "EA"}], "image_dpi": 300, "scale": None,
                                                           "scale_seen": stamp(Dj)})
    exp = json.loads((J / Dj / "p0_elements.json").read_text())
    why = server._export_outdated_why(Dj, 0, exp)
    check(st == 200 and why is None, "M2: a counts-only approve on a disagreeing page is not out of date", (st, why))
    # the store written by the 2.0 code: approved at the engine's 4, now readings disagree
    old = dict(exp, scale={"pixelsPerUnit": 75.0, "unit": "ft", "name": '1/4" - 1\'-0" (auto, text)'}, imageDpi=300)
    (J / Dj / "p0_elements.json").write_text(json.dumps(old))
    why = server._export_outdated_why(Dj, 0, old)
    check(why is None, "M2: an approval at the engine's 4 (old store), readings found to disagree since: not out of date",
          why)
    st, b = asyncio.run(_asgi("GET", f"/api/job/{Dj}/approved"))
    check(st == 200 and b'reads 1\\" = 4 ft now' not in b and b"OUT OF DATE" not in b,
          "M2: /approved prints no OUT OF DATE and never the withdrawn 4 as the sheet's scale now", b[:300])
    # a person's later change still is
    st, d = call("POST", f"/api/job/{Dj}/page/0/scale/confirm",
                 {"ft_per_paper_inch": 7.93, "how": "picked", "scale_seen": stamp(Dj)})
    why = server._export_outdated_why(Dj, 0, old)
    check(st == 200 and why and "OUT OF DATE" in why and "7.93" in why,
          "M2: a person choosing 7.93 after the approval at 4: OUT OF DATE, as before", (st, why))
    # an approval with no scale, then a person sets one: counts only, so no scale reason either
    why = server._export_outdated_why(Dj, 0, exp)
    check(why is None, "M2: an approval with no scale (counts) is never out of date for a scale reason", why)


# ================================================================ M3: one check per detection
def _m3():
    import takeoff_engine.dimcheck as DC
    real = DC.dimension_check
    calls = []

    def slow(words, chains, ft):
        calls.append(ft)
        time.sleep(0.6)
        return real(words, chains, ft)
    DC.dimension_check = slow
    try:
        F = JOB["flight"]
        res = json.loads((J / F / "p0_result.json").read_text())
        out = []
        ths = [threading.Thread(target=lambda: out.append(server._scale_verdict_full(F, 0, res, None)))
               for _ in range(15)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        check(len(calls) == 1 and len(out) == 15 and all(v["dimensions"] == "checked" for v in out),
              "M3: fifteen callers on a page with no check on file -> ONE computation, all fifteen get it",
              (len(calls), [v.get("dimensions") for v in out][:3]))
        st, d = call("GET", f"/api/job/{F}/page/0/scale/dimensions")
        check(st == 200 and len(calls) == 1, "M3: the route then reads that cache (no second computation)", len(calls))
        # the detect job runs the check before it says "done"
        Fr = JOB["fresh"]
        server.JOBS[Fr]["status"][0] = "running"
        calls.clear()
        server._after_detect(Fr, 0)
        check(len(calls) == 1 and (J / Fr / "p0_dimcheck.json").exists() and server.JOBS[Fr]["status"][0] == "done",
              "M3: after a detection the check is computed and cached, THEN the status is done", (calls, server.JOBS[Fr]["status"]))
        res = json.loads((J / Fr / "p0_result.json").read_text())
        v = call("GET", f"/api/job/{Fr}/page/0/scale")[1]["verdict"]
        check(v["dimensions"] == "checked" and v["state"] == "CONFIRMED",
              "M3: a freshly detected page's first GET .../scale has the full verdict: no 'checking' phase", v)
        src = (ROOT / "server.py").read_text(encoding="utf-8")
        check('st = "checking"' in src and "target=_after_detect" in src,
              "M3: the scheduler hands a finished detection to _after_detect before 'done' (source)")
    finally:
        DC.dimension_check = real


# ================================================================ minors
def _minors():
    Dj, O, G = JOB["dis2"], JOB["one"], JOB["agree"]
    forget(Dj)
    forget(O)
    # 1 /scale/at with no scale in force: no value, the readings as offers; an inset caption is an offer too
    real_site = server._site_scale_at
    server._site_scale_at = lambda sites, x, y: {"fpi": 8.0, "text": '1/8" = 1\'-0"'}
    try:
        st, d = call("GET", f"/api/job/{Dj}/page/0/scale/at?x=300&y=300")
    finally:
        server._site_scale_at = real_site
    check(st == 200 and d["ft_per_paper_inch"] is None and d["scale_by"] is None
          and any(o["kind"] == "inset caption" and o["ft_per_paper_inch"] == 8.0 for o in d["offers"])
          and any(o["kind"] == "dimensions" for o in d["offers"]),
          "MINOR 1: /scale/at on a disagreeing page: no scale; the readings and the inset caption are offers", (st, d))
    # 2 the server's scale decides
    st, d = call("POST", f"/api/job/{Dj}/page/0/rooms/pick", {"x": 300, "y": 300, "scale_ft_per_in": 4.0})
    check(st == 409 and "no scale in force" in str(d), "MINOR 2: rooms/pick with a client scale on a disagreeing page -> 409",
          (st, d))
    st, d = call("POST", f"/api/job/{Dj}/page/0/runs", {"style_ids": [1], "scale_ft_per_in": 4.0})
    check(st == 409, "MINOR 2: runs with a client scale on a disagreeing page -> 409", (st, str(d)[:200]))
    st, d = call("POST", f"/api/job/{O}/page/0/runs", {"style_ids": [1], "scale_ft_per_in": 25.0})
    check(st == 409 and "not the sheet scale in force" in str(d), "MINOR 2: runs with another scale than the server's -> 409",
          (st, str(d)[:200]))
    st, d = call("POST", f"/api/job/{Dj}/page/0/runs", {"style_ids": [1]})
    check(st == 200 and d.get("unit") == "pt" and all(r.get("length_ft") in (None, 0) for r in d.get("runs") or []
                                                        if isinstance(r, dict)),
          "MINOR 2: runs on a disagreeing page with no client scale: pt, and no length_ft", (st, str(d)[:300]))
    check(st == 200 and d.get("systems") and all(sy.get("length_ft") is None for sy in d["systems"]),
          "CRITIC-scale-4 MINOR 2: ...and each system's length_ft is null, never a measured-looking 0",
          [sy.get("length_ft") for sy in d.get("systems") or []])
    st, d = call("POST", f"/api/job/{O}/page/0/runs", {"style_ids": [1], "scale_ft_per_in": 20.0})
    check(st == 200 and d.get("unit") == "ft", "MINOR 2: runs with the server's own scale -> feet", (st, str(d)[:200]))
    # ...and a room pick there with no client scale is measured in pt: the engine's withdrawn reading is not used
    import takeoff_engine.rooms as RM
    real_pick, seen = RM.pick_room, {}
    RM.pick_room = lambda *a, **k: (seen.update(k), {"ok": False, "why": "stub"})[1]
    try:
        st, d = call("POST", f"/api/job/{Dj}/page/0/rooms/pick", {"x": 300, "y": 300})
    finally:
        RM.pick_room = real_pick
    check(st == 200 and seen.get("scale") == 0.0 and seen.get("scale_by") is None,
          "MINOR 2: rooms/pick on a disagreeing page passes 'no scale' (0), never None (which meant the engine's)", seen)
    import inspect
    check("elif not (scale > 0):" in inspect.getsource(real_pick),
          "MINOR 2: rooms.pick_room reads 0 as no scale in force: pt only (source)")
    # 3 an empty unit is not a count
    st, d = call("POST", f"/api/job/{Dj}/page/0/approve",
                 {"labels": {}, "image_dpi": 300, "scale": None, "scale_seen": stamp(Dj),
                  "extra_items": [{"label": "Floor tile", "qty": 1200, "unit": ""}]})
    check(st == 409 and not (J / Dj / "p0_elements.json").exists(),
          "MINOR 3: a row with an empty unit on a page with no scale -> 409 (it is not a count)", (st, str(d)[:200]))
    st, d = call("POST", f"/api/job/{O}/page/0/approve",
                 {"labels": {}, "image_dpi": 300, "scale": None, "scale_seen": stamp(O),
                  "extra_items": [{"label": "Floor tile", "qty": 1200, "unit": ""}]})
    exp = json.loads((J / O / "p0_elements.json").read_text()) if st == 200 else {}
    rows = [it for it in exp.get("items") or [] if it.get("label") == "Floor tile"]
    check(st == 200 and rows and rows[0].get("unit") != "EA", "MINOR 3: ...and on a scaled page it is never exported as EA",
          rows)
    forget(O)
    # 4 the Scale card with no stamp
    from cutfill import placement as P
    server._cf_sheet_job_page = (lambda project, key, write=False: (G, 0))
    tok = server._CF_CALLER.set(("alice", False))
    t2 = P.REVIEW_SET_SEEN.set("")
    try:
        try:
            server._cf_review_scale_set(types.SimpleNamespace(), "d0p0", 25.0, "1\" = 25'")
            got = "no refusal"
        except server.HTTPException as e:
            got = (e.status_code, e.detail)
    finally:
        P.REVIEW_SET_SEEN.reset(t2)
        server._CF_CALLER.reset(tok)
    check(isinstance(got, tuple) and got[0] == 409 and "no stamp" in got[1],
          "MINOR 4: a Scale card write with no stamp is refused like a stale one", got)
    cf_src = (ROOT / "cutfill" / "server.py").read_text(encoding="utf-8")
    check('if body.get("scale_seen") else "")' in cf_src, "MINOR 4: the card's route sends '' for a missing stamp (source)")
    # 5 the measure: computed by the server from the points; a bare number is refused
    forget(O)
    st, d = call("POST", f"/api/job/{O}/page/0/scale/confirm",
                 {"ft_per_paper_inch": 20.0, "how": "confirmed", "scale_seen": stamp(O), "measured_ft_per_paper_inch": 20.0})
    check(st == 400 and record(O) is None, "MINOR 5: a bare measured number is not accepted", (st, d))
    st, d = call("POST", f"/api/job/{O}/page/0/scale/confirm",
                 {"ft_per_paper_inch": 20.0, "how": "confirmed", "scale_seen": stamp(O),
                  "measure": {"points": [[100, 100], [5000, 100]], "true_ft": 1361.1}})
    check(st == 400 and "off the page" in str(d), "MINOR 5: a point off the page is refused", (st, d))
    st, d = call("POST", f"/api/job/{O}/page/0/scale/confirm",
                 {"ft_per_paper_inch": 20.0, "how": "confirmed", "scale_seen": stamp(O),
                  "measure": {"points": [[100, 100], [244, 100]], "true_ft": 40.0}})
    rec = record(O)
    check(st == 200 and rec["how"] == "confirmed" and rec["measure"]["points"] == [[100.0, 100.0], [244.0, 100.0]]
          and rec["measure"]["true_ft"] == 40.0 and abs(rec["measure"]["ft"] - 20.0) < 1e-9 and rec["measured_ft"] == 20.0,
          "MINOR 5: 144 pt (2 in) typed as 40 ft -> the server works out 20 ft per inch, stores the points and the length",
          rec)
    forget(O)
    st, d = call("POST", f"/api/job/{O}/page/0/scale/confirm",
                 {"ft_per_paper_inch": 20.0, "how": "confirmed", "scale_seen": stamp(O),
                  "measure": {"points": [[100, 100], [244, 100]], "true_ft": 41.0}})
    check(st == 409 and record(O) is None, "MINOR 5: 2.5 % off -> refused", (st, d))
    # 6 the gate's page_index
    from takeoff_engine import scale_gate as G6
    t = Path(tempfile.mkdtemp(prefix="c3gate_")) / "t.json"
    good = "a" * 64
    t.write_text(json.dumps([{"document": f"{n}.pdf", "page_index": pi, "ft_per_paper_inch": 20.0, "sha256": good,
                              "confirmed_by_human": True} for n, pi in (("a", "1"), ("b", -1), ("c", 1.0), ("d", True),
                                                                        ("e", 0))]))
    rows = {r["document"]: r for r in G6.load_truth(t, proposed=False)}
    check(all(rows[f"{n}.pdf"]["bad_page"] for n in "abcd") and rows["e.pdf"]["bad_page"] is None,
          "MINOR 6: page_index '1', -1, 1.0, true are ERRORs; 0 is fine", {k: v["bad_page"] for k, v in rows.items()})
    j = G6.judge(rows["b.pdf"], None, "")
    check(j["result"] == "ERROR", "MINOR 6: such a row is judged ERROR", j)
    pdf = J / "gate_one_page.pdf"
    dd = pymupdf.open()
    dd.new_page()
    dd.save(str(pdf))
    dd.close()
    r = G6.run_page(str(pdf), 3)
    check(r["ok"] is False and "outside the document (1 pages)" in r["error"],
          "MINOR 6: a page_index past the end of the file is an ERROR", r)
    # 8 one scale line in the report: approved at 20, a person types 25 after
    st, d = call("POST", f"/api/job/{O}/page/0/approve", {"labels": {"1": "Pipe"}, "image_dpi": 300, "scale": None,
                                                          "scale_seen": stamp(O), "ticks_version": takeoff(O, {"1": "Pipe"})})
    exp = json.loads((J / O / "p0_elements.json").read_text())
    st2, d2 = call("POST", f"/api/job/{O}/page/0/scale/confirm",
                   {"ft_per_paper_inch": 25.0, "how": "typed", "scale_seen": stamp(O)})
    lines = server._scale_report_state_lines(O, 0, exp)
    check(st == 200 and st2 == 200 and lines == [] and server._export_outdated_why(O, 0, exp),
          "MINOR 8: out of date after a person's 25: no second scale line, the OUT OF DATE line says it", lines)
    forget(O)
    st, d = call("POST", f"/api/job/{O}/page/0/approve", {"labels": {"1": "Pipe"}, "image_dpi": 300, "scale": None,
                                                          "scale_seen": stamp(O), "ticks_version": takeoff(O, {"1": "Pipe"})})
    exp = json.loads((J / O / "p0_elements.json").read_text())
    lines = server._scale_report_state_lines(O, 0, exp)
    check(lines and lines[0].startswith("Scale check: ") and "20 ft" not in lines[0],
          "MINOR 8: otherwise the state line says how the scale stands without a second number", lines)
    # 9 cut / fill compute: every sheet
    from cutfill import project as CP
    real_rc = P.REVIEW_CONFLICT
    P.REVIEW_CONFLICT = lambda project, key: "This sheet's scale readings disagree. Choose one on the sheet." \
        if key == "d0p1" else None
    try:
        fake = types.SimpleNamespace(data={"settings": {"display_page": "d0p0", "scale_ft_per_in": 20.0},
                                           "pages": {"d0p0": {"role": "existing"}, "d0p1": {"role": "proposed"}},
                                           "docs": [{"pages": [{"label": "C4.0"}, {"label": "C4.1"}]}]},
                                     load_analysis=lambda key: None, jobs={}, review_stale=lambda k: False)
        out = CP.surfaces_for(fake)
        rc = P.review_conflicts(fake)
    finally:
        P.REVIEW_CONFLICT = real_rc
    check(isinstance(out, dict) and "Sheet C4.1's scale readings disagree" in str(out.get("error")),
          "MINOR 9: a linked sheet whose readings disagree refuses the compute, by name", out)
    check(rc == {"d0p1": "This sheet's scale readings disagree. Choose one on the sheet."},
          "the Scale card hears which sheets disagree (summary review_conflicts)", rc)
    ui = (ROOT / "webui" / "cutfill" / "app.js").read_text(encoding="utf-8")
    check("review_conflicts" in ui and "def && !conflict" in ui and "'readings disagree'" in ui and "/ in review/.test(" in ui,
          "the Scale card shows the words and no printed default on such a sheet (source)")


def _viewer():
    # a viewer account may not record a scale (the merge checklist's refusal, checked here on the branch too)
    global CK
    O = JOB["one"]
    forget(O)
    users = U.load()
    users["vera"] = dict(U.hash_password("tundra-quill-7781"), role="viewer")
    U.save(users)
    keep, CK = CK, server._sign_session("vera")
    try:
        st, d = call("POST", f"/api/job/{O}/page/0/scale/confirm",
                     {"ft_per_paper_inch": 20.0, "how": "accepted", "scale_seen": "s0"})
    finally:
        CK = keep
    check(st in (403, 404) and record(O) is None, "a viewer's POST to the confirm route is refused, nothing written",
          (st, str(d)[:200]))


section("viewer", _viewer)
section("M1", _m1)
section("M2", _m2)
section("M3", _m3)
section("minors", _minors)
print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
