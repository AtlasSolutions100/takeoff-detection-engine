"""A SHARED JOB'S PAGE RECORD HOLDS NOBODY'S ROWS OR PRICES (2026-10-02, release item 1).

THE HOLE. Since D42 (2026-09-26) the page saved its takeoff rows and the saver's unit prices and trades into the page's
server record (webui/app.js calcStatePayload) and filled them in for the next person who opened the page
(rowsApplySaved). On a job every account opens - a demo card or a shared sheet (owner None) - that put one contractor's
unit prices and rows on another contractor's screen. v1.8.5 kept rows and prices in each browser only.

NOW (server.py _calcstate_put / get_calcstate, _calcstate_shared): on a shared job the write keeps none of the caller's
`rows`, `prices`, `trades`, `rows_scale` or flooring prices (what the file already held stays as it was, and is never
served), and the read returns none of them, plus `job_shared: true` so the page stops sending them. The inputs that are
shared by design (work areas, trench setup, ...) still round-trip. An owner's own job is unchanged.

Every request goes through the real middleware (sign-in, ownership, client gate) with no server listening.

    python tests/test_shared_calcstate_private.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_JOBS = Path(tempfile.mkdtemp(prefix="shared_cs_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(_JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="shared_cs_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ.pop("TAKEOFF_UI_PASSWORD", None)

import pymupdf  # noqa: E402

SHARED_SHEET = "C3.0-Site-Plan-Rev.1.pdf"        # one of the shared sets' sheets (demo-plans.json shared_names)
ALICE, BOB = "alice_synth", "bob_synth"
reg = {}
JID = {}
for i, (name, owner) in enumerate([(SHARED_SHEET, None), ("alice-own.pdf", ALICE)]):
    jid = "scs%09d" % i
    d = pymupdf.open()
    d.new_page(width=612, height=792)
    pdf = _JOBS / ("upload_%012x_%s" % (i + 1, name))
    d.save(str(pdf))
    d.close()
    (_JOBS / jid).mkdir()
    (_JOBS / jid / "p0_result.json").write_text("{}")
    reg[jid] = {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2), "kinds": None,
                "sizes": [[612, 792]], "owner": owner, "bid": None}
    JID[name] = jid
(_JOBS / "registry.json").write_text(json.dumps(reg, indent=1))

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

server._classify_async = lambda *a, **k: None
_users.save({ALICE: _users.hash_password("orchid-lantern-4471"), BOB: _users.hash_password("basalt-meadow-9083")})
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in (ALICE, BOB)}

fails = 0


def check(ok, msg, got=None):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg + ("" if ok or got is None else f"  (got {got!r})"))
    if not ok:
        fails += 1


async def _asgi(method, path, cookie, body=b""):
    headers = [(b"host", b"testserver"), (b"x-core-client", b"2"), (b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode())]
    if cookie:
        headers.append((b"cookie", f"{server.SESSION_COOKIE}={cookie}".encode()))
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": headers, "client": ("127.0.0.1", 40000), "server": ("testserver", 80)}
    sent = {"b": False}
    out = {"status": None, "body": b""}

    async def receive():
        if not sent["b"]:
            sent["b"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(m):
        if m["type"] == "http.response.start":
            out["status"] = m["status"]
        elif m["type"] == "http.response.body":
            out["body"] += m.get("body", b"")

    await server.app(scope, receive, send)
    return out["status"], out["body"]


def call(method, path, cookie, body=None):
    st, data = asyncio.run(_asgi(method, path, cookie, b"" if body is None else json.dumps(body).encode()))
    try:
        return st, json.loads(data)
    except ValueError:
        return st, data


def blob(tag):
    return {"v": 1, "engine_sig": "s", "zones": [[1, 2, 3, 4]],
            "rows": [{"label": f"{tag} Curb", "qty": 120, "unit": "LF"}],
            "prices": {f"{tag} Curb": 31.5}, "trades": {f"{tag} Curb": "Concrete"},
            "rows_scale": {"ft_per_in": 40},
            "fl": {"types": [{"name": "Carpet", "color": "#888", "sf_price": 4.25, "lf_price": 1.5, "waste_pct": 10}],
                   "rooms": []}}


SH = JID[SHARED_SHEET]
URL = f"/api/job/{SH}/page/0/calcstate"
REC = _JOBS / SH / "p0_calcstate.json"

# what was stored on the shared job before this fix (an earlier save of A's): left in the file, never served
REC.write_text(json.dumps({"v": 1, "rows": [{"label": "Old Curb", "qty": 9, "unit": "LF"}],
                           "prices": {"Old Curb": 99.0}, "trades": {"Old Curb": "Concrete"}}))
st, got = call("GET", URL, CK[BOB])
check(st == 200 and not any(k in got for k in ("rows", "prices", "trades")) and got.get("job_shared") is True,
      "rows / prices / trades stored on a shared job before the fix are not served (the record says job_shared)", got)

# account A saves rows + prices on the shared job
st, _ = call("POST", URL, CK[ALICE], blob("A"))
check(st == 200, "A's save on the shared job is accepted (no error on the page)", st)
disk = json.loads(REC.read_text())
check("A Curb" not in json.dumps(disk) and "31.5" not in json.dumps(disk.get("prices")),
      "A's rows, prices and trades are not written to the shared job's record", disk)
check(disk.get("rows") == [{"label": "Old Curb", "qty": 9, "unit": "LF"}] and disk.get("prices") == {"Old Curb": 99.0},
      "what the file already held stays as it was (not deleted)", disk)
check(disk["fl"]["types"][0]["sf_price"] == 0 and disk["fl"]["types"][0]["lf_price"] == 0
      and disk["fl"]["types"][0]["waste_pct"] == 10 and disk.get("zones") == [[1, 2, 3, 4]],
      "flooring prices are not stored; the flooring types, waste and work areas are", disk.get("fl"))
check("job_shared" not in disk, "the read-side marker is never stored")

for who in (BOB, ALICE):
    st, got = call("GET", URL, CK[who])
    txt = json.dumps(got)
    check(st == 200 and "A Curb" not in txt and "Old Curb" not in txt and "31.5" not in txt and "4.25" not in txt
          and not any(k in got for k in ("rows", "prices", "trades", "rows_scale")),
          f"{who} reads the shared record: no rows, prices, trades or flooring prices of anybody", got)
    check(got.get("zones") == [[1, 2, 3, 4]] and got["fl"]["types"][0]["name"] == "Carpet",
          f"{who}: what is shared by design (work areas, flooring types) still comes back", got)

# A's own job: the round trip still works
OWN = f"/api/job/{JID['alice-own.pdf']}/page/0/calcstate"
st, _ = call("POST", OWN, CK[ALICE], blob("A"))
st2, got = call("GET", OWN, CK[ALICE])
check(st == 200 and st2 == 200 and got.get("rows") == blob("A")["rows"] and got.get("prices") == {"A Curb": 31.5}
      and got.get("trades") == {"A Curb": "Concrete"} and got["fl"]["types"][0]["sf_price"] == 4.25
      and "job_shared" not in got,
      "on A's own job rows, prices, trades and flooring prices round-trip as before", got)
st, _ = call("GET", OWN, CK[BOB])
check(st == 404, "B cannot read A's own job (unchanged)", st)

# no accounts on the box (one person, single-password mode): nothing changes there either
_users.save({})
os.environ.pop("TAKEOFF_HOSTED", None)
server.HOSTED = False
check(server._calcstate_shared(SH) is False, "with no accounts on the box an owner-less job keeps its rows (one person)")

print(f"\n{'PASS' if not fails else 'FAIL'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
