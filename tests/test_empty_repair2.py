"""THE TAKEOFF STARTS EMPTY - the second repair, after CRITIC-empty-2 (2026-10-04), the server half.

MAJOR-1: a page the live build (2.0) had approved - 41 rows by length, 44,793.4 ft - opened here with nothing in the
takeoff but one area row that came back by itself. ONE press of Approve, no question, exported that row only: the bid
sheet went from 69 lines to 40, and no copy of the 2.0 export was kept. The OUT OF DATE note's own "approve it again"
led to the same loss. What this file pins (the coordinator's R6 and R7):

  R6a. an approve that would replace an approval made on an earlier version is REFUSED until it says the person was
       asked (`replace_earlier`): 409, what would be replaced in the reply, and nothing written - not the export, not
       the scale record, not the takeoff record. An empty approve keeps its own refusal (it names Withdraw);
  R6b. with the yes, the replaced export is kept in the page's record of approvals taken out of the bid, exactly as
       Withdraw keeps one (who, when, the export), and the reply says so without naming anyone;
  R6c. POST .../putback puts it back byte for byte: the export, the bid sheet; the page is "approved on an earlier
       version" again and the next approve asks again. The approval it displaced is kept in its turn. Nothing is put
       back twice; another account cannot; an approval made on THIS build is never put back (approve again does that);
  R6d. a withdrawn earlier approval can be put back the same way;
  R6e. an approve over an approval made on this build asks nothing and keeps nothing (as before);
  R6f. a note that says "approve it again" on such a page says what that replaces - on the page, in the bid sheet
       and in the report; a page approved on this build gets no such words.
  R7 (MINOR-2). THE EXPORT IS EXACTLY THE RECORD: an approve that leaves out a line style the takeoff record holds is
       refused in words and writes nothing; the whole record passes.

Every request goes through the real middleware (sign-in, ownership, client gate) with no server listening.

    python tests/test_empty_repair2.py          (fails on a1e4dcd, the branch before this repair)
"""
import asyncio
import json
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
J = Path(tempfile.mkdtemp(prefix="empty_rp2_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(J)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="empty_rp2_lg_")
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
sts, chs = [], []
for k, s in enumerate((S_WAT, S_CRB, S_STM)):          # each style: two lines (300 pt and 220 pt)
    sts.append(dict(s))
    y = 100 + 60 * k
    chs.append(chain(len(chs) + 1, s, [[72.0, y], [372.0, y]]))
    chs.append(chain(len(chs) + 1, s, [[72.0, y + 20], [272.0, y + 20], [272.0, y + 40]]))
RES = {"page": {"width_pt": 612, "height_pt": 792}, "styles": sts, "chains": chs, "engine_sig": "sig-a",
       "scale": {"ft_per_paper_inch": 20.0, "text": "1\" = 20'", "source": "text"}}
reg, IDS = {}, {}
JOBS = (("P20", "alice-approved20.pdf", "alice"), ("W20", "alice-withdrawn20.pdf", "alice"),
        ("N10", "alice-this-build.pdf", "alice"), ("OUT", "alice-outdated20.pdf", "alice"),
        ("OWN", "alice-outdated-here.pdf", "alice"), ("H20", CARD, None), ("PRT", "alice-part.pdf", "alice"))
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
U.save({"alice": U.hash_password("orchid-lantern-4471"), "bob": U.hash_password("basalt-meadow-9083")})
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in ("alice", "bob")}
# the names this repair added: read with a default so the file runs to its end on the branch before it
ADDED = getattr(server, "EXPORT_ADDED_KEY", "takeoff_added")
PART = getattr(server, "TICKS_PART", "<no such sentence before this repair>")
NONE_KEPT = getattr(server, "PUTBACK_NONE", "<no such sentence before this repair>")
HOLDS = "2 rows by length (4 lines, 288.8 ft)"      # the 2.0 approval: water main + curb, 520 pt each at 1 in = 20 ft
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
        d = json.loads(b)
    except ValueError:
        d = None
    return st, d if isinstance(d, dict) else {"_not_an_object": b[:200].decode("utf-8", "replace")}


def snap(jid):
    """Every file of the job folder with its bytes ('writes nothing' is this not changing), the report's last print
    apart: every GET of the report writes it again."""
    return {f.name: f.read_bytes() for f in sorted((J / jid).iterdir()) if f.is_file() and f.name != "takeoff_report.pdf"}


def ticks(jid, who="alice"):
    return call("GET", f"/api/job/{jid}/page/0/ticks", who)[1]


def put(jid, styles, who="alice"):
    return call("POST", f"/api/job/{jid}/page/0/ticks", who, {"version": ticks(jid, who).get("version", 0), "styles": styles})


def approve(jid, labels, who="alice", **more):
    """As the page approves: the scale stamp it loaded, the takeoff record's version it holds."""
    stamp = call("GET", f"/api/job/{jid}/page/0/scale", who)[1]["stamp"]
    body = {"labels": {str(k): v for k, v in labels.items()}, "scale": None, "scale_seen": stamp, "extra_items": [],
            "ticks_version": ticks(jid, who).get("version", 0)}
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


def approved(jid, who="alice"):
    return call("GET", f"/api/job/{jid}/approved", who)[1]


def bid(jid, who="alice"):
    return call("GET", f"/api/job/{jid}/bidsheet.csv", who, raw=True)


def record(jid):
    f = J / jid / "p0_withdrawn.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def putback(jid, who="alice"):
    return call("POST", f"/api/job/{jid}/page/0/putback", who)


def export_bytes(jid):
    f = J / jid / "p0_elements.json"
    return f.read_bytes() if f.exists() else None


P20, W20, N10, OUT, OWN, H20, PRT = (IDS[k] for k in ("P20", "W20", "N10", "OUT", "OWN", "H20", "PRT"))
AREA = {"label": "P1-BLDG-HATCH", "qty": 23249.2, "unit": "SF", "_area": True}      # the row that came back by itself

# ---- set-up: four pages as 2.0 left them approved (water main + curb), two approved on this build ------------------
for jid, who in ((P20, "alice"), (W20, "alice"), (OUT, "alice"), (H20, "alice")):
    put(jid, [1, 2], who)
    st, d = approve(jid, {1: "WATER MAIN", 2: "CURB"}, who, prices={"WATER MAIN": 12.5, "CURB": 31.0})
    check(st == 200, f"set-up: {jid} approved with the water main and the curb", (st, str(d)[:160]))
    as_20(jid)
for jid in (N10, OWN):
    put(jid, [1, 2])
    st, d = approve(jid, {1: "WATER MAIN", 2: "CURB"})
    check(st == 200 and json.loads(export_bytes(jid)).get(ADDED) == 1, f"set-up: {jid} approved on this build", (st, str(d)[:160]))
for jid in IDS.values():                             # the reads that cache on their first call (the scale's own checks)
    for p in ("result", "calcstate", "scale", "elements", "confidence", "ticks"):
        call("GET", f"/api/job/{jid}/page/0/{p}", raw=True)
    call("GET", f"/api/job/{jid}/approved", raw=True)

# ---- R6a. the critic's press: nothing by length, one row that came back, Approve - refused until the person says yes --
exp20, bid20 = export_bytes(P20), bid(P20)
before = snap(P20)
st, d = approve(P20, {}, extra_items=[AREA])
det = d.get("detail") if isinstance(d, dict) else None
check(st == 409 and isinstance(det, dict) and (det.get("earlier") or {}).get("holds") == HOLDS,
      "R6a: one press of Approve on a page 2.0 approved, with only a row that came back by itself: REFUSED (it exported "
      "that row and dropped 2 rows by length), and the refusal says what would be replaced", (st, d))
check(snap(P20) == before and export_bytes(P20) == exp20 and bid(P20) == bid20,
      "R6a: ...and NOTHING is written: the 2.0 export and the bid sheet are byte for byte the same, no scale record, no "
      "takeoff record, no record of a kept approval", sorted(set(snap(P20)) ^ set(before)))
check(isinstance(det, dict) and isinstance(det.get("say"), str) and "asks first" in det["say"],
      "R6a: the refusal carries a plain sentence for a page that had not heard", det)
put(P20, [3])
before = snap(P20)
st, d = approve(P20, {3: "STORM PIPE"}, extra_items=[AREA])
check(st == 409 and isinstance(d.get("detail"), dict) and snap(P20) == before,
      "R6a: the same with a line style added here: asked first, nothing written", (st, d))
st, d = approve(P20, {3: "STORM PIPE"}, extra_items=[AREA], replace_earlier=False)
check(st == 409 and snap(P20) == before, "R6a: an approve that says NO replaces nothing and writes nothing", (st, d))
put(P20, [])
before = snap(P20)
st, d = approve(P20, {})
check(st == 409 and d.get("detail") == server.TAKEOFF_EMPTY + " " + server.TAKEOFF_EMPTY_APPROVED and snap(P20) == before,
      "R6a: an EMPTY approve keeps its own refusal: nothing in the takeoff, and it names Withdraw approval", (st, d))

# ---- R6b. yes: replaced, and the replaced export is kept as Withdraw keeps one ---------------------------------------
put(P20, [3])
st, d = approve(P20, {3: "STORM PIPE"}, extra_items=[AREA], replace_earlier=True)
exp1 = json.loads(export_bytes(P20))
check(st == 200 and {it["label"] for it in exp1["items"]} == {"STORM PIPE", "P1-BLDG-HATCH"} and exp1.get(ADDED) == 1,
      "R6b: with the person's yes the approve goes through: the export is the takeoff here", (st, str(d)[:200]))
rec = record(P20) or []
ent = rec[-1] if rec else {}
check(len(rec) == 1 and ent.get("export") == json.loads(exp20) and ent.get("who") == "alice" and ent.get("items") == 2
      and isinstance(ent.get("at"), str) and ent.get("how") == "replaced",
      "R6b: the replaced 2.0 export is KEPT in the page's record: the export itself, who, when, and how it left the bid",
      {k: v for k, v in ent.items() if k != "export"})
call("POST", f"/api/job/{W20}/page/0/withdraw")
wd = (record(W20) or [{}])[-1]
check(set(ent) - {"how"} == set(wd) == {"at", "who", "items", "export"},
      "R6b: ...exactly as Withdraw keeps one (the same record, the same four facts)", (sorted(ent), sorted(wd)))
check(isinstance(d, dict) and (d.get("replaced") or {}).get("holds") == HOLDS and "alice" not in json.dumps(d),
      "R6b: the approve's reply says what it replaced and kept, and names no one", d.get("replaced") if isinstance(d, dict) else d)
ap = approved(P20)
check(not ap.get("earlier") and (ap.get("kept") or {}).get("0", {}).get("holds") == HOLDS
      and (ap.get("kept") or {}).get("0", {}).get("how") == "replaced" and "alice" not in json.dumps(ap),
      "R6b: GET /approved: the page is approved here now, and its earlier approval is offered to put back (what it "
      "holds, never who)", ap.get("kept"))
check(ticks(P20).get("earlier") is None, "R6b: the page's line goes (the approval in the bid is this build's)")

# ---- R6c. put back: byte for byte, asked again at the next approve, nothing lost, nothing twice -----------------------
here = export_bytes(P20)
before = snap(P20)
st, d = putback(P20, "bob")
check(st in (403, 404) and snap(P20) == before, "R6c: another account cannot put it back: refused, nothing written", (st, d))
st, d = putback(P20)
check(st == 200 and export_bytes(P20) == exp20 and bid(P20) == bid20,
      "R6c: put back: the export is byte for byte the 2.0 file again, and so is the bid sheet", (st, d))
check((d.get("earlier") or {}).get("holds") == HOLDS and (ticks(P20).get("earlier") or {}).get("holds") == HOLDS
      and (approved(P20).get("earlier") or {}).get("0", {}).get("holds") == HOLDS and "alice" not in json.dumps(d),
      "R6c: the page says again that it was approved on an earlier version (the reply, the page's read, GET /approved)", d)
rec = record(P20) or []
check(len(rec) == 2 and (rec[0].get("back") or {}).get("who") == "alice" and rec[1].get("export") == json.loads(here)
      and rec[1].get("how") == "put back",
      "R6c: the record says who put it back and when, and KEEPS the approval it displaced (nothing a person approved is lost)",
      [{k: v for k, v in e.items() if k != "export"} for e in rec])
check(not (approved(P20).get("kept") or {}).get("0"), "R6c: nothing more is offered to put back", approved(P20).get("kept"))
before = snap(P20)
st, d = putback(P20)
check(st == 404 and d.get("detail") == NONE_KEPT and snap(P20) == before,
      "R6c: a second put back is refused in words and writes nothing", (st, d))
st, d = approve(P20, {3: "STORM PIPE"})
check(st == 409 and isinstance(d.get("detail"), dict) and snap(P20) == before,
      "R6c: the next approve ASKS AGAIN (the approval in the bid is the earlier version's once more); nothing written", (st, d))
st, d = approve(P20, {3: "STORM PIPE"}, replace_earlier=True)
check(st == 200 and len(record(P20) or []) == 3 and (approved(P20).get("kept") or {}).get("0", {}).get("holds") == HOLDS,
      "R6c: ...and with a yes it is replaced and kept again", (st, len(record(P20) or [])))
before = snap(N10)
st, d = putback(N10)
check(st == 404 and snap(N10) == before, "R6c: a page with no record: nothing to put back, nothing written", (st, d))

# ---- R6d. a withdrawn earlier approval can be put back the same way ---------------------------------------------------
w20 = json.loads(json.dumps(wd.get("export")))
check(bid(W20)[0] == 404 and (approved(W20).get("kept") or {}).get("0", {}).get("how") == "withdrawn",
      "R6d: a 2.0 approval that was withdrawn is out of the bid, and offered to put back", approved(W20))
st, d = putback(W20)
check(st == 200 and json.loads(export_bytes(W20)) == w20 and approved(W20).get("pages") == [0] and len(record(W20)) == 1
      and bid(W20)[0] == 200,
      "R6d: put back: the page is approved again with the 2.0 export, in the bid sheet; no second entry (nothing was "
      "displaced)", (st, d))

# ---- R6e. an approval made on this build: no question, nothing kept; never put back -----------------------------------
st, d = approve(N10, {1: "WATER MAIN", 2: "CURB"})
check(st == 200 and record(N10) is None and "replaced" not in d,
      "R6e: approving again over an approval made on THIS build asks nothing and keeps nothing (as before)", (st, record(N10)))
call("POST", f"/api/job/{N10}/page/0/withdraw")
before = snap(N10)
st, d = putback(N10)
check(st == 404 and snap(N10) == before and not approved(N10).get("kept"),
      "R6e: a withdrawn approval of this build is not put back by this act (approve again brings it back)", (st, d))

# ---- R6f. a note that says "approve it again" says what that replaces -------------------------------------------------
for jid in (OUT, OWN):
    stamp = call("GET", f"/api/job/{jid}/page/0/scale")[1]["stamp"]
    call("POST", f"/api/job/{jid}/page/0/scale/confirm", body={"ft_per_paper_inch": 30.0, "how": "typed", "scale_seen": stamp})
note = (approved(OUT).get("outdated") or {}).get("0", "")
check(note.startswith("OUT OF DATE") and "approve it again" in note and HOLDS in note and "replaces" in note
      and "asks first" in note and "kept" in note,
      "R6f: the OUT OF DATE note of a page 2.0 approved says what approving again replaces (2 rows by length, 4 lines, "
      "288.8 ft), that it asks first and that the replaced approval is kept", note)
csv = bid(OUT)[1].decode("utf-8-sig")
st, b = call("GET", f"/api/job/{OUT}/report.pdf", raw=True)
rep = " ".join(" ".join(p.get_text() for p in pymupdf.open(stream=b, filetype="pdf")).split()) if st == 200 else ""
check(HOLDS in csv and "replaces the approval made on an earlier version" in csv and HOLDS in rep,
      "R6f: ...the same words in the bid sheet's header and on the report", (csv[:300], st))
own = (approved(OWN).get("outdated") or {}).get("0", "")
check(own.startswith("OUT OF DATE") and "approve it again" in own and "earlier version" not in own,
      "R6f: a page approved on THIS build keeps the plain note (nothing of an earlier version to replace)", own)

# ---- a shared sheet: the same question for every account ---------------------------------------------------------------
put(H20, [1], "bob")
before = snap(H20)
st, d = approve(H20, {1: "WATER MAIN"}, "bob")
check(st == 409 and isinstance(d.get("detail"), dict) and snap(H20) == before,
      "shared sheet: an account's approve over the sheet's 2.0 approval is asked first too; nothing written", (st, d))
st, d = approve(H20, {1: "WATER MAIN"}, "bob", replace_earlier=True)
check(st == 200 and (record(H20) or [{}])[-1].get("who") == "bob" and "bob" not in json.dumps(approved(H20, "alice")),
      "shared sheet: with a yes it is replaced, kept with who did it, and no reply names him", (st, approved(H20, "alice").get("kept")))

# ---- R7 / MINOR-2. the export is exactly the record -------------------------------------------------------------------
put(PRT, [1, 3])
before = snap(PRT)
st, d = approve(PRT, {1: "WATER MAIN"})
check(st == 409 and d.get("detail") == PART and snap(PRT) == before,
      "MINOR-2: an approve that leaves out a style the takeoff record holds (a declared trade set the storm aside in "
      "one browser) is REFUSED in words and writes nothing: the bid held a part of the record", (st, d))
st, d = approve(PRT, {1: "WATER MAIN"}, extra_items=[AREA])
check(st == 409 and d.get("detail") == PART and snap(PRT) == before, "MINOR-2: ...with a calculator row beside it too", (st, d))
st, d = approve(PRT, {1: "WATER MAIN", 3: "STORM PIPE"})
exp = json.loads(export_bytes(PRT) or b"{}")
by = sorted(k for it in exp.get("items", []) for k in (it.get("by_style") or {}))
check(st == 200 and by == ["1", "3"] and ticks(PRT).get("styles") == [1, 3],
      "MINOR-2: the whole record is approved: the export's line styles are exactly the record's", (st, by))
st, d = approve(PRT, {1: "WATER MAIN", 2: "CURB", 3: "STORM PIPE"})
check(st == 409 and d.get("detail") == server.TICKS_STALE,
      "MINOR-2: ...and a style the record does NOT hold is still refused with the stale write's sentence (critic empty-1, "
      "MAJOR-4)", (st, d))

print(f"\n{'PASS' if not fails else 'FAIL'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
