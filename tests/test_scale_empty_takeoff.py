"""THE SCALE IS RECORDED WITHOUT AN APPROVE (coordinator ruling 3, 2026-10-03, on the empty-takeoff round).

An approve with nothing in the takeoff is refused since the takeoff starts empty, so a scale must never wait for one.
On a page with nothing in its takeoff and no approval:
  1. the Scale card's type / pick / calibrate record the scale now (POST .../scale/confirm), and the export is untouched;
  2. "put it back" to the engine's own reading is recorded now too (POST .../scale/back: the retire's tombstone, no
     person), and only from a page that saw the record in force (a stale stamp is refused, nothing written);
  3. no Scale-card action, status line or chip says anything about approving.

    python tests/test_scale_empty_takeoff.py      (fails on 925b338: no scale/back route; the words say "Approve")
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
J = Path(tempfile.mkdtemp(prefix="scale_empty_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(J)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="scale_empty_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = str(J)
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("CUTFILL_OUT", None)

import pymupdf  # noqa: E402

JID = "scemp0000001"
d = pymupdf.open()
d.new_page(width=612, height=792)
pdf = J / "upload_000000000001_alice-one.pdf"
d.save(str(pdf))
d.close()
(J / JID).mkdir()
(J / JID / "p0_result.json").write_text(json.dumps({
    "page": {"width_pt": 612, "height_pt": 792}, "styles": [], "chains": [], "engine_sig": "s",
    "scale": {"ft_per_paper_inch": 20.0, "text": "1\" = 20'", "source": "text"}}))
(J / "registry.json").write_text(json.dumps({JID: {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2),
                                                   "kinds": None, "sizes": [[612, 792]], "owner": "alice", "bid": None}}))

import server  # noqa: E402
from takeoff_engine import users as U  # noqa: E402

server._classify_async = lambda *a, **k: None
U.save({"alice": U.hash_password("orchid-lantern-4471")})
server._UI_PASSWORD = None
CK = server._sign_session("alice")
fails = 0


def check(cond, msg, got=None):
    global fails
    print(("ok: " if cond else "FAIL: ") + msg + ("" if cond or got is None else f"  (got {got!r})"))
    if not cond:
        fails += 1


async def _asgi(method, path, body=b""):
    headers = [(b"host", b"testserver"), (b"x-core-client", b"3"), (b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode()), (b"cookie", f"{server.SESSION_COOKIE}={CK}".encode())]
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


def call(method, path, body=None):
    st, b = asyncio.run(_asgi(method, path, json.dumps(body).encode() if body is not None else b""))
    try:
        return st, json.loads(b)
    except ValueError:
        return st, b


def scale():
    return call("GET", f"/api/job/{JID}/page/0/scale")[1]


D = J / JID
check(not (D / "p0_elements.json").exists(), "set-up: nothing approved, nothing in the takeoff")
# 1. typed, recorded now
st, dd = call("POST", f"/api/job/{JID}/page/0/scale/confirm", {"ft_per_paper_inch": 30.0, "how": "typed", "scale_seen": scale()["stamp"]})
check(st == 200 and scale()["in_force"] == 30.0 and not (D / "p0_elements.json").exists(),
      "1: a typed scale on a page with an empty takeoff is recorded now; nothing is exported", (st, scale().get("in_force")))
# 2. back to the engine's reading, recorded now
stamp = scale()["stamp"]
st, dd = call("POST", f"/api/job/{JID}/page/0/scale/back", {"scale_seen": "s-stale"})
check(st == 409 and scale()["in_force"] == 30.0 and scale()["stamp"] == stamp,
      "2: put back from a page that did not see the record now in force: refused, nothing written", (st, dd))
st, dd = call("POST", f"/api/job/{JID}/page/0/scale/back", {"scale_seen": stamp})
rec = json.loads((D / "p0_scale.json").read_text())
check(st == 200 and scale()["in_force"] == 20.0 and rec.get("retired") is True and (dd.get("verdict") or {}).get("person") is None,
      "2: put it back: the engine's 1\" = 20' is in force again, recorded as the retire (a tombstone, no person)", (st, rec))
check(isinstance(dd.get("stamp"), str) and dd["stamp"] == scale()["stamp"] and not (D / "p0_elements.json").exists(),
      "2: ...with the record's new stamp for the page, and still nothing exported")
# 3. the words
# the code's own words only: comments explain the history (they say "Approve" on purpose) and are stripped
js = re.sub(r"(?m)^\s*//[^\n]*$", "", (ROOT / "webui" / "app.js").read_text(encoding="utf-8"))
js = re.sub(r"\s//[^\n`\"']*$", "", js, flags=re.M)


def fn(name):
    i = js.index("\nfunction " + name + "(") if ("\nfunction " + name + "(") in js else js.index("\nasync function " + name + "(")
    return js[i:js.index("\n}\n", i)]


chip = fn("scaleRecordChip")
check("pprove" not in chip, "3: the chip beside the scale says nothing about approving", chip[-260:])
put = js[js.index('un.textContent = "put it back";'):js.index("row.appendChild(un);")]
check("pprove" not in put and "svRecord(" in put and "svBackToEngine(" in put,
      "3: 'put it back' records the scale itself (a person's number again, or the engine's reading) and says nothing of approving")
sug = js[js.index('b.textContent = `use ${(+c.ft_per_paper_inch).toFixed(2)} ft/in`;'):]
sug = sug[:sug.index("row.appendChild(b);")]
check("pprove" not in sug and "svRecord(" in sug, "3: picking a reading the engine did not trust records it and says nothing of approving")
check("pprove" not in fn("scalePersonSet") and "pprove" not in fn("svBackToEngine").replace("approveScaleRecorded", ""),
      "3: the typed box and the measure's 'set scale' record through the scale route, no approve")

print(f"\n{'PASS' if not fails else 'FAIL'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
