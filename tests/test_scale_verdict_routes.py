"""THE SCALE VERDICT ON THE WIRE, AND A PERSON'S RECORD (scale verdict slices S1 / S2).

S1: GET /api/job/{id}/page/{p}/scale carries `verdict` (CONFIRMED / UNCHECKED / CONFLICT / NONE), read-only; the
profile witness is off; the dimension witness is lazy ("pending" until GET .../scale/dimensions has run, which then
returns `scale_verdict` with it).
S2: POST .../scale/confirm records a person's decision now, with how (typed | calibrated | picked | confirmed) and
who; the approve and the Cut / fill Scale card write the same record. The coordinator's rulings of 2026-10-02:
  6  anyone who may approve may confirm (here: anyone who may open the job; core-redesign refuses a viewer by method)
  7  `who` is stored, served only to staff and to the person themself
  8  a confirm names its VALUE, must be the value in force, honours the stamp; on CONFLICT only `picked`
  9  a person's record is tied to the value: a re-detect does not drop it, the new reading shows under `disagree`
  10 a tombstone is no person

Requests go through the real app (session cookies, the client gate, the job rule), in a throwaway store.
Run:  python tests/test_scale_verdict_routes.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
J = Path(tempfile.mkdtemp(prefix="scale_verdict_routes_"))
LG = tempfile.mkdtemp(prefix="scale_verdict_routes_lg_")
os.environ["TAKEOFF_JOBS_DIR"] = str(J)
os.environ["TAKEOFF_LEDGER_DIR"] = LG
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = str(J)
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("CUTFILL_OUT", None)

import pymupdf  # noqa: E402

CARD = "C4.0-SITE-GRADING-PLAN-Rev.1.pdf"     # a shared card: every account may open and approve it


def cap(v, t):
    return {"text": t, "ft_per_paper_inch": v, "near_scale_keyword": True, "count": 1,
            "governs_pt": None, "governs_pct": None, "sites": 1}


# one printed caption, no bar: UNCHECKED
R_ONE = {"page": {"width_pt": 612, "height_pt": 792}, "chains": [], "styles": [], "engine_sig": "t1",
         "scale": {**cap(20.0, '1" = 20\''), "source": "text", "has_nts": False,
                   "candidates": [cap(20.0, '1" = 20\'')], "bar_ft_per_paper_inch": None,
                   "agrees": None, "delta_pct": None, "pick": {"why": "single"}}}
# a structural sheet's shape: caption 4 + bar 3.965 agree, the printed dimensions say 7.93 -> CONFLICT
R_DISAGREE = {"page": {"width_pt": 612, "height_pt": 792}, "chains": [], "styles": [], "engine_sig": "t1",
           "scale": {**cap(4.0, '1/4" - 1\'-0"'), "source": "text", "has_nts": True,
                     "candidates": [cap(4.0, '1/4" - 1\'-0"'), cap(8.0, '1/8"=1\'-0"')],
                     "bar_ft_per_paper_inch": 3.965, "agrees": True, "delta_pct": 0.9,
                     "pick": {"why": "governs"}}}
DIM_DISAGREE = {"verdict": "disagrees", "witnesses": [{}] * 7, "witness_consensus_n": 5,
             "witness_consensus_ft_per_in": 7.93, "n_agree": 0, "n_disagree": 7}

reg = {}
IDS = {}
for i, (k, name, owner, res) in enumerate((("S", CARD, None, R_ONE), ("A", "alice-own.pdf", "alice", R_DISAGREE))):
    d = pymupdf.open()
    d.new_page(width=612, height=792)
    pdf = J / ("upload_%012x_%s" % (i + 1, name))
    d.save(str(pdf))
    d.close()
    jid = k.lower() * 12
    (J / jid).mkdir()
    (J / jid / "p0_result.json").write_text(json.dumps(res))
    reg[jid] = {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2), "kinds": None,
                "sizes": [[612, 792]], "owner": owner, "bid": None}
    IDS[k] = jid
(J / "registry.json").write_text(json.dumps(reg))

import server  # noqa: E402
# THE TAKEOFF STARTS EMPTY (2026-10-03): an approve with nothing in the takeoff is refused, so the approves here that
# are about the scale carry one counted row ("Test count", 1 EA - a count needs no scale).
from takeoff_engine import users as U  # noqa: E402

server._classify_async = lambda *a, **k: None
U.save({"alice": U.hash_password("orchid-lantern-4471"), "bob": U.hash_password("basalt-meadow-9083"),
        "staff": dict(U.hash_password("granite-sparrow-2210"), staff=True)})
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in ("alice", "bob", "staff")}
S, A = IDS["S"], IDS["A"]
FAILS = []


def check(cond, msg, got=None):
    print(("ok   " if cond else "FAIL ") + msg + ("" if cond or got is None else f"  [{got}]"))
    if not cond:
        FAILS.append(msg)


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


def call(method, path, who, body=None):
    st, b = asyncio.run(_asgi(method, path, CK[who], json.dumps(body).encode() if body is not None else b""))
    try:
        return st, json.loads(b)
    except ValueError:
        return st, b


def scale(who, jid):
    st, d = call("GET", f"/api/job/{jid}/page/0/scale", who)
    assert st == 200, (st, d)
    return d


def confirm(who, jid, ft, how, stamp=None, text=None, measured="same"):
    """A `confirmed` sends the person's measure (CRITIC-scale-2 M1); by default one that agrees exactly."""
    if stamp is None:
        stamp = scale(who, jid)["stamp"]
    body = {"ft_per_paper_inch": ft, "how": how, "text": text, "scale_seen": stamp}
    if how == "confirmed" and measured is not None:
        # the measure itself (CRITIC-scale-3 MINOR 5): two points one paper inch apart and the true length typed
        body["measure"] = {"points": [[100.0, 100.0], [172.0, 100.0]], "true_ft": ft if measured == "same" else measured}
    return call("POST", f"/api/job/{jid}/page/0/scale/confirm", who, body)


def record(jid):
    f = J / jid / "p0_scale.json"
    return json.loads(f.read_text()) if f.exists() else None


def dkey(jid):
    """The detection digest a cached dimension check is keyed on (CRITIC-scale-2 MINOR 4)."""
    return server._scale_detection_digest_of(json.loads((J / jid / "p0_result.json").read_text()))


# ================================================================ S1: the verdict on the wire
d = scale("bob", S)
v = d["verdict"]
check(v["state"] == "UNCHECKED" and v["value"] == 20.0 and v["dimensions"] == "pending" and v["profile"] == "off"
      and v["person"] is None, "S1: one caption, no check run yet: UNCHECKED at 20, dimensions pending, profile off",
      v)
check(d["in_force"] == 20.0, "S1 is read-only: in_force is the engine's 20 as before")
# a dimension check on file for this detection completes the verdict
(J / S / "p0_dimcheck.json").write_text(json.dumps({"key": {"scale": 20.0, "engine_sig": "t1", "detection": dkey(S)},
                                                    "check": {"verdict": "agrees", "witnesses": [{}] * 4,
                                                              "witness_consensus_n": 4,
                                                              "witness_consensus_ft_per_in": 20.1}}))
v = scale("bob", S)["verdict"]
check(v["state"] == "CONFIRMED" and v["dimensions"] == "checked", "S1: with the cached check, caption + dimensions agree:"
      " CONFIRMED", v["why"])
(J / S / "p0_dimcheck.json").unlink()
# the lazy route returns the check AND the verdict with it. A page with no stored chains cannot be checked
# (CRITIC-scale-2 MINOR 5): the route ANSWERS that (200, never a 500), and the verdict says "failed", not "checked"
st, dd = call("GET", f"/api/job/{S}/page/0/scale/dimensions", "bob")
check(st == 200 and dd.get("verdict") == "failed" and dd["scale_verdict"]["state"] == "UNCHECKED"
      and dd["scale_verdict"]["dimensions"] == "failed",
      "S1: GET .../scale/dimensions keeps its own `verdict` and adds `scale_verdict` (no chains: failed)",
      (st, str(dd)[:200]))
# that shape BEFORE its dimension check has run: caption + bar agree, but CONFIRMED must wait for the check
v = scale("alice", A)["verdict"]
check(v["state"] == "UNCHECKED" and v.get("would_be") == "CONFIRMED" and v["value"] == 4.0
      and v["dimensions"] == "pending" and "until the printed-dimension check has run" in v["why"],
      "S1: never CONFIRMED while the dimension check is pending (caption + bar agree at 4, truth 8)", v)
# that shape: CONFLICT
(J / A / "p0_dimcheck.json").write_text(json.dumps({"key": {"scale": 4.0, "engine_sig": "t1", "detection": dkey(A)}, "check": DIM_DISAGREE}))
v = scale("alice", A)["verdict"]
check(v["state"] == "CONFLICT" and v["value"] is None
      and sorted(c["kind"] for c in v["choices"]) == ["bar", "caption", "dimensions"],
      "S1: that shape is CONFLICT with every reading a choice", v["why"])
check(scale("alice", A)["in_force"] is None,
      "S4: a page whose readings disagree has nothing in force (S1 was read-only; S4 enforces)")

# ================================================================ S2: confirm, honestly (ruling 8)
before = record(S)
st, d = confirm("alice", S, 20.0, "confirmed", stamp="rnot-the-stamp")
check(st == 409 and record(S) == before, "8: a page that did not see the record in force cannot confirm (stamp)", st)
st, d = confirm("alice", S, 25.0, "confirmed")
check(st == 409 and "not" in str(d) and record(S) == before, "8: confirming a number that is not in force is refused",
      (st, str(d)[:160]))
st, d = confirm("alice", S, 20.0, "guessed")
check(st == 400 and record(S) == before, "an unknown `how` is refused", st)
st, d = confirm("alice", S, 0.0, "typed")
check(st == 400 and record(S) == before, "a number outside the band is refused (the review's own refusal)", st)
st, d = confirm("alice", S, 20.0, "confirmed")
rec = record(S)
check(st == 200 and rec and rec["how"] == "confirmed" and rec["who"] == "alice" and rec["ft_per_paper_inch"] == 20.0
      and rec.get("set_at") and isinstance(rec.get("at"), float),
      "6 / 8: alice (who may approve the shared card) confirms the 20 in force; the record says how, who, when", rec)
check(d["verdict"]["state"] == "CONFIRMED" and d["verdict"]["person"]["how"] == "confirmed",
      "the confirm answers with the new verdict: CONFIRMED by a person", d["verdict"]["why"])
check(d["stamp"] != before and d["stamp"] == scale("bob", S)["stamp"], "the record's stamp moved (Approve sees it)")

# ---- ruling 7: who is served only to staff and to the person
for who, want in (("alice", "alice"), ("staff", "alice"), ("bob", None)):
    g = scale(who, S)
    check((g["reviewer"] or {}).get("who") == want and g["verdict"]["person"]["who"] == want,
          f"7: {who} sees who set it: {want!r}", (g["reviewer"], g["verdict"]["person"]))
check("set_at" in (scale("bob", S)["reviewer"] or {}), "7: bob still sees the date (\"set by hand\" and when)")

# ---- the approve keeps a person's record of the same number (rulings 9 / 10)
st, d = call("POST", f"/api/job/{S}/page/0/approve", "bob",
             {"labels": {}, "extra_items": [{"label": "Test count", "qty": 1, "unit": "EA"}], "scale": {"ft_per_paper_inch": 20.0, "text": '1" = 20\'', "picked_by_reviewer": False},
              "scale_seen": scale("bob", S)["stamp"]})
check(st == 200 and record(S) == rec, "an approve of the confirmed number keeps alice's record as it is (no tombstone)",
      (st, record(S)))
# ruling 9: a re-detect that reads another number does not drop it, and shows the new reading
(J / S / "p0_result.json").write_text(json.dumps(dict(R_ONE, scale={**R_ONE["scale"], **cap(40.0, '1" = 40\''),
                                                                    "candidates": [cap(40.0, '1" = 40\'')]})))
g = scale("alice", S)
check(g["in_force"] == 20.0 and g["verdict"]["state"] == "CONFIRMED" and g["verdict"]["value"] == 20.0
      and [w["value"] for w in g["verdict"]["disagree"]] == [40.0],
      "9: after a re-detect to 40 the person's 20 stands and the 40 is listed as disagreeing", g["verdict"])
(J / S / "p0_result.json").write_text(json.dumps(R_ONE))

# ---- typed and calibrated through the approve
st, d = call("POST", f"/api/job/{S}/page/0/approve", "bob",
             {"labels": {}, "extra_items": [{"label": "Test count", "qty": 1, "unit": "EA"}], "scale": {"ft_per_paper_inch": 30.0, "text": '1" = 30\'', "picked_by_reviewer": True, "how": "typed"},
              "scale_seen": scale("bob", S)["stamp"]})
rec = record(S)
check(st == 200 and rec["how"] == "typed" and rec["who"] == "bob" and rec["ft_per_paper_inch"] == 30.0,
      "an approve of a number no reading made records typed, by bob", rec)
st, d = call("POST", f"/api/job/{S}/page/0/approve", "alice",
             {"labels": {}, "extra_items": [{"label": "Test count", "qty": 1, "unit": "EA"}], "scale": {"ft_per_paper_inch": 26.6667, "text": "24' over a measured line",
                                      "picked_by_reviewer": True},
              "scale_seen": scale("alice", S)["stamp"]})
rec = record(S)
check(st == 200 and rec["how"] == "calibrated" and rec["who"] == "alice",
      "the Measure tool's 'set scale' through the approve records calibrated", rec)
check(server._scale_name(server._page_scale(S, 0, R_ONE)).startswith("24' over a measured line (set by measuring a "
                                                                       "known length"),
      "a calibrated number's export name says it was set by measuring a known length (CRITIC-scale-1 M2)")
st, d = confirm("alice", S, 22.0, "typed")
check(st == 200 and record(S)["how"] == "typed" and record(S)["ft_per_paper_inch"] == 22.0,
      "typed through the confirm route records at once, before any approve")
# back to the engine's 20 by an approve: the go-back retires the pick into a tombstone, as today -> no person (10)
st, d = call("POST", f"/api/job/{S}/page/0/approve", "alice",
             {"labels": {}, "extra_items": [{"label": "Test count", "qty": 1, "unit": "EA"}], "scale": {"ft_per_paper_inch": 20.0, "text": '1" = 20\'', "picked_by_reviewer": False},
              "scale_seen": scale("alice", S)["stamp"]})
check(st == 200 and (record(S) or {}).get("retired") is True and scale("alice", S)["verdict"]["person"] is None,
      "10: going back to the engine's own number by Approve leaves a tombstone, and a tombstone is no person",
      record(S))
st, d = confirm("alice", S, 20.0, "confirmed")
check(st == 200 and record(S)["how"] == "confirmed",
      "after a tombstone, Confirm writes a person's record of the engine's number")
check(server._scale_name(server._page_scale(S, 0, R_ONE)) == '1" = 20\' (read by the engine, checked by the reviewer by measuring a known length)',
      "the export names a confirmed number as read by the engine and checked by measuring, not 'set by the reviewer'",
      server._scale_name(server._page_scale(S, 0, R_ONE)))
lines = server._scale_report_lines({"setBy": "reviewer", "ftPerPaperInch": 20.0, "engineFtPerPaperInch": 20.0,
                                    "engineText": '1" = 20\'', "setHow": "confirmed", "name": "x"})
check(len(lines) == 1 and "CHECKED BY THE REVIEWER BY MEASURING A KNOWN LENGTH" in lines[0], "the report says the same",
      lines)

# ---- CONFLICT: only a pick (ruling 8)
st, d = confirm("alice", A, 4.0, "confirmed")
check(st == 409 and "disagree" in str(d) and "dimensions" in str(d) and record(A) is None,
      "8: Confirm on a CONFLICT is refused, naming the choices", (st, str(d)[:240]))
st, d = confirm("alice", A, 9.0, "picked")
check(st == 200 and record(A)["how"] == "typed" and record(A)["ft_per_paper_inch"] == 9.0,
      "M2: a 'pick' that is none of the page's readings is stored as TYPED (a person entered it)", (st, record(A)))
st, d = confirm("alice", A, 7.93, "picked")
rec = record(A)
check(st == 200 and rec["how"] == "picked" and rec["ft_per_paper_inch"] == 7.93
      and d["verdict"]["state"] == "CONFIRMED" and d["verdict"]["value"] == 7.93,
      "8: picking the dimensions' 7.93 on the CONFLICT records picked, CONFIRMED by a person", rec)
st, d = confirm("alice", A, 8.0, "picked")
check(st == 200 and record(A)["ft_per_paper_inch"] == 8.0, "8: the printed 1/8\" caption (8) is a pickable reading too")
# a stranger's page
st, d = confirm("bob", A, 8.0, "confirmed", stamp="none")
check(st == 404, "6: bob may not confirm on a job he may not open (404, as for Approve)", st)

# ---- the training ledger carries no names
names = []
for f in Path(LG).rglob("*.jsonl"):
    for ln in f.read_text(encoding="utf-8").splitlines():
        e = json.loads(ln)
        if e.get("event") == "scale_set":
            names.append(json.dumps(e))
check(names and all('"who"' not in n and "alice" not in n and "bob" not in n for n in names),
      f"no scale_set event in the training ledger names a person ({len(names)} events)")

# ---- the Cut / fill Scale card writes the same record
st, d = call("POST", f"/api/job/{A}/cutfill/link", "alice", {"job": S})
check(st == 200, "alice links the shared card into her own cut / fill", (st, str(d)[:160]))
p = server._cutfill.get_project(f"job_{A}")
key = f"d{d['doc']}p0"
(J / S / "p0_scale.json").unlink()
server._PICK_LAST.pop(str(J / S / "p0_scale.json"), None)
tok = server._CF_CALLER.set(("alice", False))
try:
    ok = server._cf_review_scale_set(p, key, 20.0, '1" = 20\'')
finally:
    server._CF_CALLER.reset(tok)
rec = record(S)
check(ok is True and rec and rec.get("how") == "typed" and rec.get("who") == "alice" and not rec.get("retired"),
      "the Scale card's TYPING of the number in force writes a person's record, how typed (CRITIC-scale-1 M2)", rec)
(J / S / "p0_scale.json").unlink()
server._PICK_LAST.pop(str(J / S / "p0_scale.json"), None)
from cutfill import placement as _cfpl
tok = server._CF_CALLER.set(("alice", False))
tc = _cfpl.REVIEW_SET_CHOSEN.set(True)
try:
    ok = server._cf_review_scale_set(p, key, 20.0, '1" = 20\'')
finally:
    _cfpl.REVIEW_SET_CHOSEN.reset(tc)
    server._CF_CALLER.reset(tok)
check(ok is True and record(S).get("how") == "accepted",
      "the Scale card's CHOOSE control on the number in force: accepted (a click is not a measure, CRITIC-scale-2 M1)",
      record(S))
tok = server._CF_CALLER.set(("alice", False))
try:
    ok = server._cf_review_scale_set(p, key, 50.0, '1" = 50\'')
finally:
    server._CF_CALLER.reset(tok)
check(ok is True and record(S)["how"] == "typed" and record(S)["ft_per_paper_inch"] == 50.0,
      "the Scale card's typing of another number records typed", record(S))

print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
