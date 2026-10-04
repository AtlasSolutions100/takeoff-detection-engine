"""THE TAKEOFF STARTS EMPTY - the repair after CRITIC-empty-1 (2026-10-03), the server half.

The owner, asked again: "don't bring anything over, start empty". The first build guessed a 2.0 page's takeoff back
from its export, from one browser, or from default picks, and each way put lines in a bid the estimator had left out
(BLOCKER-1, BLOCKER-2, MAJOR-1, MAJOR-2). The migration is gone. What this file pins:

  R1. NOTHING IS BROUGHT OVER.
      a. a page 2.0 APPROVED reads EMPTY here; its export, the bid-sheet CSV, the report and GET /approved are byte for
         byte what they were before and after it is opened - five times, by its owner, by staff, in any order - and
         nothing is written in its folder; the page is told so in one line (`earlier`), with what was approved behind
         it; it is NOT out of date for being empty; approving again replaces the old approval and the line goes;
         withdrawing takes it out;
      b. a shared sheet 2.0 approved: the same for every account;
      c. a page 2.0 SAVED (its page record, never approved) reads empty; its record - calculator rows, work area -
         stays byte for byte, and a save on this build adds no mark to it;
      d. a write that asks to bring ticks over (`migrate`) is refused in words and writes nothing: own job, staff,
         shared sheet;
      e. Reset then Detect: empty. A new job whose file has the same name: empty.
  R3. THE APPROVE IS HELD TO THE SERVER'S RECORD (MAJOR-4): an approve on another version of the takeoff, or one that
      would export a line style the record does not hold (by a style's label, a line's own label, or a drawn line put
      in such a style), is refused with the stale write's sentence and writes nothing; the takeoff the record holds
      and a family the person made on the page pass; a PART of the record is refused too (since CRITIC-empty-2: the
      export is exactly the record); on a shared sheet each account's approve is held to its own record.
  MINOR-3.  the refusals that only Withdraw can answer name it.
  MINOR-6.  two styles sharing one key: a tick stays on its number only on the detection it was made on; after a
            re-detect by another engine it is dropped and counted, never left on the twin that now wears the number.
  MINOR-7.  a record made on another engine's detection says so (`redetected`); an approve, or the next tick, writes
            it on this one.
  MINOR-9.  Reset names a withdrawn approval's record on its own, never under "other cached files".
  MINOR-11. an approve's write waits for a withdrawal in progress (one lock).

Every request goes through the real middleware (sign-in, ownership, client gate) with no server listening.

    python tests/test_empty_repair.py           (fails on 888b2c1, the branch before the repair)
"""
import asyncio
import json
import math
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
J = Path(tempfile.mkdtemp(prefix="empty_rp_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(J)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="empty_rp_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = str(J)
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("CUTFILL_OUT", None)

import pymupdf  # noqa: E402

CARD = "C4.0-SITE-GRADING-PLAN-Rev.1.pdf"       # a shared sheet (demo-plans.json shared_names)


def style(sid, klass, layer, color, width, sig, length):
    return {"style_id": sid, "suggested_label": layer, "class": klass, "layer": layer, "color": color,
            "width": width, "signature": sig, "length_pt": length, "n": 1, "texture": False}


def chain(cid, st, pts):
    ln = sum(math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) for i in range(1, len(pts)))
    return {"chain_id": cid, "style_id": st["style_id"], "class": st["class"], "color": st["color"],
            "width": st["width"], "layer": st["layer"], "length_pt": round(ln, 3), "points": pts}


S_WAT = style(1, "dashed", "C-WATR", [0.0, 0.0, 1.0], 0.5, "d6.0 g3.0", 400.0)
S_CRB = style(2, "solid", "C-CURB", [0.0, 0.0, 0.0], 1.0, "solid", 900.0)
S_STM = style(3, "dashed", "C-STRM", [0.0, 0.6, 0.0], 0.5, "d9.0 g4.5", 300.0)
S_ANN = style(4, "annotation", "C-ANNO", [0.0, 0.0, 0.0], 0.25, "sheet furniture", 100.0)


def result(styles, sig="sig-a"):
    """A detection: each style with two lines (300 pt and 220 pt)."""
    sts, chs = [], []
    for k, s in enumerate(styles):
        sts.append(dict(s))
        y = 100 + 60 * k
        chs.append(chain(len(chs) + 1, s, [[72.0, y], [372.0, y]]))
        chs.append(chain(len(chs) + 1, s, [[72.0, y + 20], [272.0, y + 20], [272.0, y + 40]]))
    return {"page": {"width_pt": 612, "height_pt": 792}, "styles": sts, "chains": chs, "engine_sig": sig,
            "scale": {"ft_per_paper_inch": 20.0, "text": "1\" = 20'", "source": "text"}}


RES = result([S_WAT, S_CRB, S_STM, S_ANN])
reg = {}
IDS = {}
JOBS = (("P20", "alice-approved20.pdf", "alice"), ("W20", "alice-withdrawn20.pdf", "alice"),
        ("S20", "alice-saved20.pdf", "alice"), ("H20", CARD, None), ("HLD", "alice-held.pdf", "alice"),
        ("TWN", "alice-twins.pdf", "alice"), ("RDT", "alice-redetect.pdf", "alice"), ("WDR", "alice-withdraw.pdf", "alice"),
        ("NEW", "alice-approved20.pdf", "alice"))            # NEW: another job, the same file name as P20
for i, (k, name, owner) in enumerate(JOBS):
    d = pymupdf.open()
    d.new_page(width=612, height=792)
    pdf = J / ("upload_%012x_%s" % (i + 1, name))
    d.save(str(pdf))
    d.close()
    jid = ("%s%012d" % (k.lower(), i))[:12]
    (J / jid).mkdir()
    (J / jid / "p0_result.json").write_text(json.dumps(RES))
    reg[jid] = {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2), "kinds": None,
                "sizes": [[612, 792]], "owner": owner, "bid": None}
    IDS[k] = jid
(J / "registry.json").write_text(json.dumps(reg))

import server  # noqa: E402
from takeoff_engine import users as U  # noqa: E402

server._classify_async = lambda *a, **k: None
U.save({"alice": U.hash_password("orchid-lantern-4471"), "bob": U.hash_password("basalt-meadow-9083"),
        "sam": U.hash_password("quartz-harbor-2219")})
U.set_staff("sam", True)
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in ("alice", "bob", "sam")}
# the names this repair added: read with a default so the file runs to its end on the branch before the repair
ADDED = getattr(server, "EXPORT_ADDED_KEY", "takeoff_added")
NO_MIG = getattr(server, "TICKS_NO_MIGRATE", "<no such sentence before the repair>")
EMPTY_APPROVED = getattr(server, "TAKEOFF_EMPTY_APPROVED", "<no such sentence before the repair>")
EARLIER_HEAD = getattr(server, "EARLIER_HEAD", "<no such sentence before the repair>")
fails = 0


def check(cond, msg, got=None):
    global fails
    print(("ok: " if cond else "FAIL: ") + msg + ("" if cond or got is None else f"  (got {got!r})"))
    if not cond:
        fails += 1


async def _asgi(method, path, cookie, body=b""):
    path, _, query = path.partition("?")
    headers = [(b"host", b"testserver"), (b"x-core-client", b"3"), (b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode()),
               (b"cookie", f"{server.SESSION_COOKIE}={cookie}".encode())]
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": query.encode(),
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


def call(method, path, who="alice", body=None, raw=False):
    st, b = asyncio.run(_asgi(method, path, CK[who], json.dumps(body).encode() if body is not None else b""))
    if raw:
        return st, b
    try:
        return st, json.loads(b)
    except ValueError:
        return st, b


def snap(jid):
    """Every file of the job folder with its bytes: 'writes nothing' is this not changing. (Not the report's last
    print, takeoff_report.pdf: every GET of the report writes it again, on dev too, and this file asks for reports.)"""
    return {f.name: f.read_bytes() for f in sorted((J / jid).iterdir()) if f.is_file() and f.name != "takeoff_report.pdf"}


def ticks(jid, who="alice"):
    return call("GET", f"/api/job/{jid}/page/0/ticks", who)


def put(jid, version, styles, who="alice", migrate=None):
    b = {"version": version, "styles": styles}
    if migrate:
        b["migrate"] = migrate
    return call("POST", f"/api/job/{jid}/page/0/ticks", who, b)


def approve(jid, labels, who="alice", version=None, **more):
    """As the page approves: `version` is the takeoff record's version it names (default: the one the server holds)."""
    stamp = call("GET", f"/api/job/{jid}/page/0/scale", who)[1]["stamp"]
    body = {"labels": {str(k): v for k, v in labels.items()}, "scale": None, "scale_seen": stamp, "extra_items": []}
    if version != "none":
        body["ticks_version"] = ticks(jid, who)[1].get("version", 0) if version is None else version
    body.update(more)
    return call("POST", f"/api/job/{jid}/page/0/approve", who, body)


def as_20(jid):
    """Leave this page as the live build (2.0) left an approved one: its export carries no mark of this build, and no
    takeoff record exists (2.0 kept its ticks in a browser)."""
    f = J / jid / "p0_elements.json"
    exp = json.loads(f.read_text())
    exp.pop(ADDED, None)
    f.write_text(json.dumps(exp, indent=1))
    (J / jid / "p0_ticks.json").unlink(missing_ok=True)


def opens(jid, who, ticks_too=True):
    """What a page asks when it opens this sheet (reads only)."""
    for p in ("result", "calcstate", "scale", "elements", "confidence") + (("ticks",) if ticks_too else ()):
        call("GET", f"/api/job/{jid}/page/0/{p}", who, raw=True)
    call("GET", f"/api/job/{jid}/approved", who, raw=True)


def prime(jid):
    """The reads that cache on their first call on dev too (the scale's dimension check, the report's last print),
    made once before a snapshot - every read EXCEPT the takeoff's, so what the snapshots judge is this change."""
    opens(jid, "alice", ticks_too=False)
    call("GET", f"/api/job/{jid}/report.pdf", "alice", raw=True)


def bid(jid, who="alice"):
    return call("GET", f"/api/job/{jid}/bidsheet.csv", who, raw=True)


def report_text(jid, who="alice"):
    st, b = call("GET", f"/api/job/{jid}/report.pdf", who, raw=True)
    if st != 200:
        return st, ""
    return st, "\f".join(p.get_text() for p in pymupdf.open(stream=b, filetype="pdf"))


P20, W20, S20, H20, HLD, TWN, RDT, WDR, NEW = (IDS[k] for k in ("P20", "W20", "S20", "H20", "HLD", "TWN", "RDT", "WDR", "NEW"))

# ---- set-up: three pages as 2.0 left them approved (water main + curb, priced), one as 2.0 left it saved ------------
for jid, who in ((P20, "alice"), (W20, "alice"), (H20, "alice")):
    put(jid, 0, [1, 2], who)
    st, d = approve(jid, {1: "WATER MAIN", 2: "CURB"}, who, prices={"WATER MAIN": 12.5, "CURB": 31.0})
    check(st == 200, f"set-up: {jid} approved with the water main and the curb", (st, str(d)[:160]))
    as_20(jid)
REC20 = {"v": 1, "zones": [[10, 10, 300, 300]], "rows": [{"label": "Pavement (edge)", "qty": 120.0, "unit": "LF"}],
         "checks": {"done": ["style:2"]}}
(J / S20 / "p0_calcstate.json").write_text(json.dumps(REC20, indent=1))
for jid in IDS.values():
    prime(jid)

# ---- R1a. a page 2.0 approved: empty here, its approval untouched ---------------------------------------------------
exp0 = (J / P20 / "p0_elements.json").read_bytes()
bid0, rep0, apr0 = bid(P20), report_text(P20), call("GET", f"/api/job/{P20}/approved", raw=True)
before = snap(P20)
st, d = ticks(P20)
check(st == 200 and d.get("state") == "fresh" and d.get("styles") == [] and d.get("version") == 0,
      "R1a: a page 2.0 approved reads EMPTY here: nothing is brought over from its export", (st, d))
ea = d.get("earlier") or {}
check(ea.get("head") == EARLIER_HEAD == "Approved on an earlier version: its numbers are still in the bid.",
      "R1a: ...and the page is told so in one line: 'Approved on an earlier version: its numbers are still in the bid.'", ea)
check("2 rows by length (4 lines, 288.8 ft)" in str(ea.get("detail")) and "approved again" in str(ea.get("detail"))
      and "withdrawn" in str(ea.get("detail")),
      "R1a: ...with what was approved behind it: 2 rows by length, 4 lines, 288.8 ft (the bid's own two rows: 520 pt at "
      "1 in = 20 ft = 144.4 ft each)", ea)
for who in ("alice", "sam", "alice", "sam", "alice"):
    opens(P20, who)
check(snap(P20) == before, "R1a: five opens - the owner, staff, in turn - write NOTHING in the page's folder",
      sorted(set(snap(P20)) ^ set(before)) or [k for k in before if snap(P20).get(k) != before[k]])
check((J / P20 / "p0_elements.json").read_bytes() == exp0 and not (J / P20 / "p0_ticks.json").exists(),
      "R1a: the 2.0 export is byte for byte what it was, and no takeoff record was made")
check(bid(P20) == bid0 and bid(P20, "sam")[1] == bid0[1], "R1a: the bid-sheet CSV is byte for byte the same after the opens "
      "(the owner's, and staff's)")
check(report_text(P20) == rep0 and "WATER MAIN" in rep0[1] and "CURB" in rep0[1],
      "R1a: the report's text is the same after the opens, and still carries the 2.0 approval")
apr = call("GET", f"/api/job/{P20}/approved", raw=True)
aj = json.loads(apr[1])
check(apr == apr0 and aj.get("pages") == [0] and not aj.get("outdated"),
      "R1a: GET /approved is the same bytes; the page is approved and NOT out of date for having an empty takeoff", aj)
check((aj.get("earlier") or {}).get("0", {}).get("head") == EARLIER_HEAD,
      "R1a: ...and a whole-job reader gets the page's line from it (`earlier`)", aj.get("earlier"))
# an approve with nothing in the takeoff cannot take the page out of the bid: refused, and the refusal names Withdraw
st, d = approve(P20, {})
check(st == 409 and d.get("detail") == server.TAKEOFF_EMPTY + " " + EMPTY_APPROVED and snap(P20) == before,
      "R1a / MINOR-3: an empty approve on it is refused, names Withdraw approval, and writes nothing", (st, d))
# the person adds the storm pipe: the old approval still stands in the bid until they approve
st, d = put(P20, 0, [3])
check(st == 200 and d.get("styles") == [3] and (ticks(P20)[1].get("earlier") or {}).get("head") == EARLIER_HEAD
      and (J / P20 / "p0_elements.json").read_bytes() == exp0 and bid(P20) == bid0,
      "R1a: adding a style here changes nothing in the bid: the 2.0 approval stands, and the line stays", (st, d))
# (the approve says the person was asked first: critic empty-2, MAJOR-1; tests/test_empty_repair2.py pins the question)
st, d = approve(P20, {3: "STORM PIPE"}, replace_earlier=True)
exp1 = json.loads((J / P20 / "p0_elements.json").read_text())
check(st == 200 and {it["label"] for it in exp1["items"]} == {"STORM PIPE"} and exp1.get(ADDED) == 1,
      "R1a: approving again replaces the old approval with what was added here (the export says so)", (st, sorted(exp1)))
check(ticks(P20)[1].get("earlier") is None and not call("GET", f"/api/job/{P20}/approved")[1].get("earlier"),
      "R1a: ...and the line goes", ticks(P20)[1])
csv = bid(P20)[1].decode("utf-8-sig")
check("STORM PIPE" in csv and "WATER MAIN" not in csv, "R1a: the bid sheet now holds the storm pipe, not the 2.0 rows")
# withdrawing a 2.0 approval takes it out, and the line goes with it
st, d = call("POST", f"/api/job/{W20}/page/0/withdraw")
check(st == 200 and ticks(W20)[1].get("earlier") is None and ticks(W20)[1].get("styles") == [] and bid(W20)[0] == 404,
      "R1a: withdrawing a 2.0 approval takes it out of the bid; the page is empty and says nothing more", (st, ticks(W20)[1]))

# ---- R1b. a shared sheet 2.0 approved: the same for every account ---------------------------------------------------
hb, hbid = snap(H20), bid(H20)
got = {}
for who in ("bob", "alice", "sam", "bob"):
    opens(H20, who)
    got[who] = ticks(H20, who)[1]
check(all(g.get("state") == "fresh" and g.get("styles") == [] and (g.get("earlier") or {}).get("head") == EARLIER_HEAD
          for g in got.values()),
      "R1b: a shared sheet 2.0 approved opens EMPTY for every account, each told its numbers are still in the bid", got)
check(snap(H20) == hb and bid(H20, "bob") == hbid, "R1b: ...and their opens, in any order, write nothing; the bid sheet "
      "is the same bytes")

# ---- R1c. a page 2.0 saved, never approved: empty; its record stays ------------------------------------------------
sb = snap(S20)
st, d = ticks(S20)
check(st == 200 and d.get("state") == "fresh" and d.get("styles") == [] and d.get("earlier") is None,
      "R1c: a page 2.0 saved but did not approve reads EMPTY (not 'legacy': no browser is asked for its ticks)", (st, d))
for who in ("sam", "alice", "sam"):
    opens(S20, who)
check(snap(S20) == sb, "R1c: staff looking first, then the owner: nothing is written; the 2.0 record is byte for byte the same")
st, cs = call("GET", f"/api/job/{S20}/page/0/calcstate")
check(st == 200 and cs.get("zones") == REC20["zones"] and cs.get("rows") == REC20["rows"] and cs.get("checks") == REC20["checks"],
      "R1c: its calculator rows, work area and check-offs are served as 2.0 kept them", cs)
call("POST", f"/api/job/{S20}/page/0/calcstate", body=dict(REC20, zones=[[10, 10, 300, 320]]))
saved = json.loads((J / S20 / "p0_calcstate.json").read_text())
check("tk" not in saved and saved.get("zones") == [[10, 10, 300, 320]],
      "R1c: a save of the record on this build stores what the page sent and adds no mark of its own", sorted(saved))

# ---- R1d. `migrate` is refused, in words, and writes nothing --------------------------------------------------------
sb = snap(S20)
for who, src in (("alice", "browser"), ("alice", "seed"), ("sam", "seed")):
    st, d = put(S20, 0, [1, 2, 3], who, migrate={"source": src})
    check(st == 409 and d.get("detail") == NO_MIG and snap(S20) == sb,
          f"R1d: a write that asks to bring 2.0's ticks over ({who}, {src}) is refused in words and writes nothing", (st, d))
check(ticks(S20)[1].get("styles") == [], "R1d: ...the page is still empty")
st, d = put(H20, 0, [1, 2, 3], "bob", migrate={"source": "browser"})
check(st == 409 and d.get("detail") == NO_MIG and snap(H20) == hb, "R1d: the same on a shared sheet", (st, d))

# ---- R1e. Reset then Detect: empty. A new job with a same-named file: empty -----------------------------------------
put(S20, 0, [1])
st, d = call("POST", f"/api/job/{S20}/reset")
(J / S20).mkdir(exist_ok=True)
(J / S20 / "p0_result.json").write_text(json.dumps(RES))                  # Detect again
st2, d2 = ticks(S20)
check(st == 200 and st2 == 200 and d2.get("state") == "fresh" and d2.get("styles") == [] and d2.get("earlier") is None,
      "R1e: Reset this document, then Detect: the takeoff is EMPTY", (st, d2))
st, d = ticks(NEW)
check(st == 200 and d.get("state") == "fresh" and d.get("styles") == [] and d.get("earlier") is None
      and not (J / NEW / "p0_ticks.json").exists(),
      "R1e: a new job whose file has the name of one 2.0 approved is EMPTY, and nothing is written", (st, d))

# ---- R3. the approve is held to the server's record (MAJOR-4) -------------------------------------------------------
D = J / HLD
put(HLD, 0, [1])                                                  # tab 1 added the water main: the record is [1], v1
hb = snap(HLD)
st, d = approve(HLD, {2: "CURB"}, version=0)                      # tab 2 (its tick refused as stale) presses Approve
check(st == 409 and d.get("detail") == server.TICKS_STALE and snap(HLD) == hb,
      "R3: an approve on another version of the takeoff is refused with the stale write's sentence; nothing written "
      "(no export, no scale record)", (st, d, sorted(snap(HLD))))
st, d = approve(HLD, {2: "CURB"}, version=1)
check(st == 409 and d.get("detail") == server.TICKS_STALE and snap(HLD) == hb,
      "R3: the right version but a style the record does not hold: refused the same way; nothing written", (st, d))
st, d = approve(HLD, {2: "CURB"}, version="none")
check(st == 409 and d.get("detail") == server.TICKS_STALE and snap(HLD) == hb,
      "R3: an approve that names no version at all, on a page with a record: refused; nothing written", (st, d))
st, d = approve(HLD, {1: "WATER MAIN"}, chain_labels={"3": "CURB (one line)"})
check(st == 409 and d.get("detail") == server.TICKS_STALE and snap(HLD) == hb,
      "R3: a line's own label on a style the record does not hold: refused; nothing written", (st, d))
drawn = {"chain_id": 900, "style_id": 2, "class": "solid", "color": [0, 0, 0], "length_pt": 100.0,
         "points": [[100.0, 500.0], [200.0, 500.0]]}
st, d = approve(HLD, {1: "WATER MAIN"}, added_chains=[drawn], chain_labels={"900": "CURB (drawn)"})
check(st == 409 and d.get("detail") == server.TICKS_STALE and snap(HLD) == hb,
      "R3: a drawn line put in a detected style the record does not hold: refused; nothing written", (st, d))
st, d = approve(HLD, {1: "WATER MAIN"})
exp = json.loads((D / "p0_elements.json").read_text()) if (D / "p0_elements.json").exists() else {}
check(st == 200 and {it["label"] for it in exp.get("items", [])} == {"WATER MAIN"},
      "R3: the takeoff the record holds is approved", (st, str(d)[:160]))
mine = dict(drawn, style_id=77)                                   # a family the person made on the page (no record of it)
st, d = approve(HLD, {1: "WATER MAIN", 77: "MY FAMILY"}, added_chains=[mine], operator_created_style_ids=[77])
exp = json.loads((D / "p0_elements.json").read_text())
check(st == 200 and {it["label"] for it in exp["items"]} == {"WATER MAIN", "MY FAMILY"},
      "R3: a family the person made on the page (in no record) is approved with it", (st, str(d)[:160]))
put(HLD, 1, [1, 3])
hb = snap(HLD)
st, d = approve(HLD, {1: "WATER MAIN"})
check(st == 409 and d.get("detail") == getattr(server, "TICKS_PART", None) and snap(HLD) == hb,
      "R3: a PART of the record's takeoff is refused too, in words, nothing written (critic empty-2, MINOR-2: the "
      "export is exactly the record; a declared trade no longer sets aside a style the person added)", (st, str(d)[:160]))
st, d = approve(HLD, {1: "WATER MAIN", 3: "STORM PIPE"})
check(st == 200, "R3: ...the whole record is approved", (st, str(d)[:160]))
# a shared sheet: each account's approve is held to ITS record
call("POST", f"/api/job/{H20}/page/0/withdraw", "alice")
put(H20, 0, [2], "alice")
put(H20, 0, [1], "bob")
hb = snap(H20)
st, d = approve(H20, {2: "CURB"}, "bob")
check(st == 409 and d.get("detail") == server.TICKS_STALE and snap(H20) == hb,
      "R3: on a shared sheet bob's approve is held to BOB's record: alice's curb is not his to approve", (st, d))
st, d = approve(H20, {1: "WATER MAIN"}, "bob")
check(st == 200, "R3: ...his own water main is", (st, str(d)[:160]))

# ---- MINOR-3. the refusals that only Withdraw can answer name it -----------------------------------------------------
src = (ROOT / "server.py").read_text(encoding="utf-8")
check(src.count("withdraw its approval if ") == 2,
      "MINOR-3: both 'approve page N again without its cut / fill rows' sentences also say to withdraw its approval "
      "when nothing else is in its takeoff", src.count("withdraw its approval if "))

# ---- MINOR-6 / MINOR-7. twins, and a record made on another engine's detection --------------------------------------
TW_A = style(5, "dashed", "C-TWIN", [0.2, 0.2, 0.2], 0.5, "d5.0 g5.0", 400.0)
TW_B = style(6, "dashed", "C-TWIN", [0.2, 0.2, 0.2], 0.5, "d5.0 g5.0", 900.0)     # the same key as TW_A
(J / TWN / "p0_result.json").write_text(json.dumps(result([S_WAT, TW_A, TW_B], "sig-a")))
st, d = put(TWN, 0, [5])
check(st == 200 and d.get("styles") == [5] and ticks(TWN)[1].get("styles") == [5] and not ticks(TWN)[1].get("redetected"),
      "MINOR-6: two styles sharing one key, the SAME detection: the tick stays on its own number (5)", (st, d))
# detected again by another engine: the twins trade numbers (5 is now the 900 pt one)
(J / TWN / "p0_result.json").write_text(json.dumps(result([S_WAT, dict(TW_B, style_id=5), dict(TW_A, style_id=6)], "sig-b")))
d = ticks(TWN)[1]
check(d.get("styles") == [] and d.get("dropped") == 1,
      "MINOR-6: after a re-detect by another engine the twin tick is DROPPED and counted, not left on the style that "
      "now wears number 5", d)
(J / RDT / "p0_result.json").write_text(json.dumps(result([S_WAT, S_CRB, S_STM, S_ANN], "sig-a")))
put(RDT, 0, [1, 3])
check(ticks(RDT)[1].get("redetected") is False, "MINOR-7: a record on the detection it was made on: not 'redetected'",
      ticks(RDT)[1])
(J / RDT / "p0_result.json").write_text(json.dumps(result([S_WAT, S_CRB, S_STM, S_ANN], "sig-b")))
d = ticks(RDT)[1]
check(d.get("styles") == [1, 3] and d.get("dropped") == 0 and d.get("redetected") is True,
      "MINOR-7: the same styles kept by key on another engine's detection: said (`redetected`), though none dropped", d)
rb = snap(RDT)
ticks(RDT)
check(snap(RDT) == rb, "MINOR-7: ...and reading it writes nothing")
st, _ = approve(RDT, {1: "WATER MAIN", 3: "STORM PIPE"})
d = ticks(RDT)[1]
check(st == 200 and d.get("redetected") is False and d.get("styles") == [1, 3] and d.get("version") == 1,
      "MINOR-7: an approve of that takeoff writes the record on this detection (same styles, same version)", (st, d))

# ---- MINOR-9. Reset names a withdrawn approval's record -------------------------------------------------------------
put(WDR, 0, [1])
approve(WDR, {1: "WATER MAIN"})
call("POST", f"/api/job/{WDR}/page/0/withdraw")
st, d = call("POST", f"/api/job/{WDR}/reset?preview=true")
c = (d or {}).get("counts") or {}
check(st == 200 and c.get("withdrawn") == 1 and (J / WDR / "p0_withdrawn.json").exists(),
      "MINOR-9: Reset's preview names the withdrawn approval's record on its own (the only copy of that export)", c)

# ---- MINOR-11. an approve's write waits for a withdrawal in progress -------------------------------------------------
lock = getattr(server, "_WITHDRAW_LOCK")
out = {}
exp_f = J / WDR / "p0_elements.json"
lock.acquire()
try:
    th = threading.Thread(target=lambda: out.update(r=approve(WDR, {1: "WATER MAIN"})))
    th.start()
    th.join(2.5)
    waited = th.is_alive() and not exp_f.exists()
finally:
    lock.release()
th.join(30)
check(waited and (out.get("r") or [0])[0] == 200 and exp_f.exists(),
      "MINOR-11: while a withdrawal holds its lock an approve does not write its export; it goes through after",
      (waited, (out.get("r") or [0])[0]))

print(f"\n{'PASS' if not fails else 'FAIL'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
