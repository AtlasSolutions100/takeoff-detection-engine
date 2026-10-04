"""WITHDRAW A PAGE'S APPROVAL (coordinator ruling, 2026-10-03, on the empty-takeoff round).

An approve with nothing in the takeoff is refused since the takeoff starts empty, so a page whose takeoff became empty
after it was approved could not be approved "again" to clear its OUT OF DATE note, and the bid kept counting it. The
explicit act (POST /api/job/{id}/page/{p}/withdraw, server.withdraw_approval):

  1. the page's approved export leaves: GET /approved no longer lists the page, the bid-sheet CSV and the report no
     longer count it, the other approved page is untouched;
  2. its OUT OF DATE note goes with the export it judged;
  3. recorded with who and when (p<N>_withdrawn.json), the withdrawn export kept inside the record; the reply names no one;
  4. a page that is not approved: refused in words, nothing written; the empty approve stays refused;
  5. approving the page again puts it back in the bid; a second withdrawal is a second entry.
  6. the page: a "Withdraw approval" button, shown only on an approved page, asks first, then posts the act.

Every request goes through the real middleware (sign-in, ownership, client gate) with no server listening.

    python tests/test_withdraw_approval.py        (fails on 925b338: there is no withdraw route)
"""
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
J = Path(tempfile.mkdtemp(prefix="withdraw_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(J)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="withdraw_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = str(J)
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("CUTFILL_OUT", None)

import pymupdf  # noqa: E402


def result(name):
    st = {"style_id": 1, "suggested_label": name, "class": "solid", "layer": "C-" + name, "color": [0, 0, 0],
          "width": 1.0, "signature": "solid", "length_pt": 300.0, "n": 1, "texture": False}
    ch = {"chain_id": 1, "style_id": 1, "class": "solid", "color": [0, 0, 0], "width": 1.0, "layer": st["layer"],
          "length_pt": 300.0, "points": [[72.0, 100.0], [372.0, 100.0]]}
    return {"page": {"width_pt": 612, "height_pt": 792}, "styles": [st], "chains": [ch], "engine_sig": "s",
            "scale": {"ft_per_paper_inch": 20.0, "text": "1\" = 20'", "source": "text"}}


JID = "wdrw00000001"
d = pymupdf.open()
for _ in range(2):
    d.new_page(width=612, height=792)
pdf = J / "upload_000000000001_alice-two.pdf"
d.save(str(pdf))
d.close()
(J / JID).mkdir()
(J / JID / "p0_result.json").write_text(json.dumps(result("CURB")))
(J / JID / "p1_result.json").write_text(json.dumps(result("WALK")))
(J / "registry.json").write_text(json.dumps({JID: {"path": str(pdf), "pages": 2, "mtime": round(pdf.stat().st_mtime, 2),
                                                   "kinds": None, "sizes": [[612, 792]] * 2, "owner": "alice", "bid": None}}))

import server  # noqa: E402
from takeoff_engine import users as U  # noqa: E402

server._classify_async = lambda *a, **k: None
U.save({"alice": U.hash_password("orchid-lantern-4471"), "bob": U.hash_password("basalt-meadow-9083")})
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in ("alice", "bob")}
fails = 0


def check(cond, msg, got=None):
    global fails
    print(("ok: " if cond else "FAIL: ") + msg + ("" if cond or got is None else f"  (got {got!r})"))
    if not cond:
        fails += 1


async def _asgi(method, path, cookie, body=b""):
    headers = [(b"host", b"testserver"), (b"x-core-client", b"3"), (b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode()), (b"cookie", f"{server.SESSION_COOKIE}={cookie}".encode())]
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


def call(method, path, who="alice", body=None, raw=False):
    st, b = asyncio.run(_asgi(method, path, CK[who], json.dumps(body).encode() if body is not None else b""))
    if raw:
        return st, b
    try:
        return st, json.loads(b)
    except ValueError:
        return st, b


def takeoff(page, sids):
    """As the page: the line styles about to be approved are ADDED to the page's takeoff first (it starts empty); the
    answer is the version of the takeoff record, which the approve names (the server holds the approve to it)."""
    tk = call("GET", f"/api/job/{JID}/page/{page}/ticks")[1]
    if sorted(sids) != tk.get("styles"):
        tk = call("POST", f"/api/job/{JID}/page/{page}/ticks", body={"version": tk["version"], "styles": sorted(sids)})[1]
    return tk["version"]


def approve(page, label, ft=None):
    stamp = call("GET", f"/api/job/{JID}/page/{page}/scale")[1]["stamp"]
    sc = {"ft_per_paper_inch": ft, "text": f"1\" = {ft:g}'", "picked_by_reviewer": True, "how": "typed"} if ft else None
    return call("POST", f"/api/job/{JID}/page/{page}/approve",
                body={"labels": {"1": label}, "scale": sc, "scale_seen": stamp, "ticks_version": takeoff(page, [1])})


def bid_text():
    st, b = call("GET", f"/api/job/{JID}/bidsheet.csv", raw=True)
    return st, b.decode("utf-8-sig")


def report_text():
    st, b = call("GET", f"/api/job/{JID}/report.pdf", raw=True)
    if st != 200:
        return st, ""
    rp = pymupdf.open(stream=b, filetype="pdf")
    return st, " ".join(p.get_text() for p in rp)


D = J / JID
check(approve(0, "CURB")[0] == 200 and approve(1, "WALK")[0] == 200, "set-up: both pages approved")
check(call("GET", f"/api/job/{JID}/approved")[1]["pages"] == [0, 1], "set-up: GET /approved lists pages 1 and 2")
# page 1's scale changes after its approve: OUT OF DATE (the case a re-approve used to clear)
stamp = call("GET", f"/api/job/{JID}/page/0/scale")[1]["stamp"]
st, _ = call("POST", f"/api/job/{JID}/page/0/scale/confirm", body={"ft_per_paper_inch": 40.0, "how": "typed", "scale_seen": stamp})
od = call("GET", f"/api/job/{JID}/approved")[1].get("outdated") or {}
check(st == 200 and od.get("0", "").startswith("OUT OF DATE"), "set-up: page 1 is OUT OF DATE (its scale changed)", od)
st, b = bid_text()
_, r = report_text()
check("CURB" in b and "WALK" in b and "CURB" in r, "set-up: the bid sheet and the report count both pages")
exp0 = json.loads((D / "p0_elements.json").read_text())

# ---- 4. a page that is not approved is refused, nothing written; the empty approve stays refused ----------------
before = sorted(f.name for f in D.iterdir())
st, dd = call("POST", f"/api/job/{JID}/page/5/withdraw")
check(st == 404 and dd.get("detail") == server.WITHDRAW_NONE and sorted(f.name for f in D.iterdir()) == before,
      "4: withdrawing a page that is not approved is refused in words; nothing is written", (st, dd))
st, dd = call("POST", f"/api/job/{JID}/page/0/approve", body={"labels": {}, "scale_seen": call("GET", f"/api/job/{JID}/page/0/scale")[1]["stamp"],
                                                              "ticks_version": call("GET", f"/api/job/{JID}/page/0/ticks")[1]["version"]})
check(st == 409 and dd.get("detail") == server.TAKEOFF_EMPTY + " " + getattr(server, "TAKEOFF_EMPTY_APPROVED", "(no such sentence before the repair)"),
      "4: the empty approve stays refused, and on a page that is approved the refusal names Withdraw approval (critic "
      "empty-1, MINOR-3)", (st, dd))
st, dd = call("POST", f"/api/job/{JID}/page/0/withdraw", who="bob")
check(st == 404 and (D / "p0_elements.json").exists(), "4: another account cannot withdraw it (not its job)", st)

# ---- 1-3. withdraw page 1 ----------------------------------------------------------------------------------------
st, dd = call("POST", f"/api/job/{JID}/page/0/withdraw")
check(st == 200 and dd.get("approved") == [1] and dd.get("items") == 1 and "alice" not in json.dumps(dd),
      "1: withdrawn; the reply says which pages stay approved and names no one", (st, dd))
check(not (D / "p0_elements.json").exists() and (D / "p1_elements.json").exists(),
      "1: page 1's approved export left; page 2's stays")
ap = call("GET", f"/api/job/{JID}/approved")[1]
check(ap["pages"] == [1] and "0" not in (ap.get("outdated") or {}),
      "1-2: GET /approved lists only page 2, and page 1's OUT OF DATE note is gone", ap)
st, b = bid_text()
_, r = report_text()
check(st == 200 and "CURB" not in b and "WALK" in b, "1: the bid sheet no longer counts page 1 (CURB gone, WALK stays)")
check("CURB" not in r and "WALK" in r, "1: the report no longer counts page 1")
rec = json.loads((D / "p0_withdrawn.json").read_text())
check(isinstance(rec, list) and len(rec) == 1 and rec[0]["who"] == "alice" and re.match(r"\d{4}-\d\d-\d\dT", rec[0]["at"])
      and rec[0]["export"] == exp0, "3: recorded with who and when, the withdrawn export kept in the record", rec)
st, dd = call("POST", f"/api/job/{JID}/page/0/withdraw")
check(st == 404 and dd.get("detail") == server.WITHDRAW_NONE, "4: withdrawing it twice is refused (it is not approved now)")

# ---- 5. approve again puts it back; a second withdrawal is a second entry -----------------------------------------
check(approve(0, "CURB", 40.0)[0] == 200 and call("GET", f"/api/job/{JID}/approved")[1]["pages"] == [0, 1]
      and "CURB" in bid_text()[1], "5: approving page 1 again puts it back in the bid")
call("POST", f"/api/job/{JID}/page/0/withdraw")
check(len(json.loads((D / "p0_withdrawn.json").read_text())) == 2, "5: a second withdrawal is a second entry")
st, _ = asyncio.run(_asgi("POST", f"/api/job/{JID}/page/1/withdraw", CK["alice"]))
check(st == 200 and call("GET", f"/api/job/{JID}/approved")[1]["pages"] == [], "the last approved page can be withdrawn too")
st, _ = report_text()
check(st == 404, "...and the report then says there is no approved page", st)

# ---- 6. the page ---------------------------------------------------------------------------------------------------
js = (ROOT / "webui" / "app.js").read_text(encoding="utf-8")
html = (ROOT / "webui" / "index.html").read_text(encoding="utf-8")
blk = js[js.index("// WITHDRAW-BEGIN"):js.index("// WITHDRAW-END")]
check('id="withdrawBtn" class="mini" hidden' in html and ">Withdraw approval</button>" in html,
      "6: the page has a 'Withdraw approval' button, hidden until the page is approved")
check(blk.index("confirm(") < blk.index("/withdraw`") and 'method: "POST"' in blk,
      "6: it asks first, then posts the act")
check(re.search(r'\$\("withdrawBtn"\)\.hidden = !\(Array\.isArray\(j\.pages\) && j\.pages\.includes\(page\)\)', js) is not None,
      "6: shown only when GET /approved lists this page (approvalOutdated)")
pr = js[js.index("function pageReset()"):js.index("\n}\n", js.index("function pageReset()"))]
check('$("withdrawBtn").hidden = true' in pr, "6: hidden again on every page switch")

print(f"\n{'PASS' if not fails else 'FAIL'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
