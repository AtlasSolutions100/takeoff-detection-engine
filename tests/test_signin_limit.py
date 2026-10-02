"""TWELVE WRONG PASSWORDS NO LONGER LOCK OUT EVERY CLIENT (2026-10-02, release item 4).

THE HOLE (v1.8.5 too). Sign-in allowed 12 failures in 5 minutes per ADDRESS (request.client.host), and behind the
reverse proxy every client most likely arrives from the proxy's one private address: twelve wrong passwords from
anyone blocked every client's sign-in for five minutes, and a script could repeat that all day.

NOW (server.py SIGN-IN LIMIT): failures count per NAME TRIED (12 in 5 minutes: only that name waits; another account
signs in meanwhile), and per address only when the address is public (60 in 5 minutes); loopback and the private ranges
are never blocked as an address. Both maps are capped. Failed sign-ins go to the activity log where the build has one
(the account name, never a password, never an unknown name's text).

NOT covered (said in the code): a slow guess spread over many names or many public addresses.

    python tests/test_signin_limit.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_JOBS = Path(tempfile.mkdtemp(prefix="signin_limit_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(_JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="signin_limit_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ.pop("TAKEOFF_UI_PASSWORD", None)

# the activity log as a build that has one writes it (takeoff_engine/audit.py is the redesign's): captured here
LOGGED = []
_fake = types.ModuleType("takeoff_engine.audit")
_fake.write = lambda ev: LOGGED.append(dict(ev)) or True
import takeoff_engine  # noqa: E402
_had_audit = "takeoff_engine.audit" in sys.modules or (ROOT / "takeoff_engine" / "audit.py").exists()
if not _had_audit:
    sys.modules["takeoff_engine.audit"] = _fake
    takeoff_engine.audit = _fake

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

server._UI_PASSWORD = None
PW = {"ann": "orchid-lantern-4471", "ben": "basalt-meadow-9083"}
_users.save({n: _users.hash_password(p) for n, p in PW.items()})

fails = 0


def check(ok, msg, got=None):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg + ("" if ok or got is None else f"  (got {got!r})"))
    if not ok:
        fails += 1


async def _asgi(body: bytes, addr: str):
    headers = [(b"host", b"testserver"), (b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode())]
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": "/api/login", "raw_path": b"/api/login", "query_string": b"",
             "root_path": "", "headers": headers, "client": (addr, 40000), "server": ("testserver", 80)}
    sent = {"b": False}
    out = {"status": None}

    async def receive():
        if not sent["b"]:
            sent["b"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(m):
        if m["type"] == "http.response.start":
            out["status"] = m["status"]

    await server.app(scope, receive, send)
    return out["status"]


def signin(name, pw, addr="172.18.0.5"):           # the proxy's private address, as on the box
    return asyncio.run(_asgi(json.dumps({"username": name, "password": pw}).encode(), addr))


def fresh():
    server._FAILS.clear()
    server._FAILS_ADDR.clear()
    LOGGED.clear()


# ---- 12 failures on one name block that name only --------------------------------------------------------------------
fresh()
got = [signin("ann", "wrong-guess-%d" % i) for i in range(12)]
check(got == [401] * 12, "twelve wrong passwords for ann: twelve 'do not match'", got)
check(signin("ann", PW["ann"]) == 429, "ann now waits, even with her right password")
check(signin("  ANN ", PW["ann"]) == 429, "...however the name is typed (case, spaces)")
check(signin("ben", PW["ben"]) == 200, "ben signs in from the same address during ann's block")
check(signin("ben", "one-wrong-try") == 401, "ben's own wrong password is a plain 401, not a block")
# five minutes on, ann signs in again
server._FAILS[server._fail_name("ann")] = [time.time() - 301] * 12
check(signin("ann", PW["ann"]) == 200, "when the five minutes are over ann signs in")

# ---- a private address is never blocked as an address ----------------------------------------------------------------
fresh()
for addr in ("10.0.0.9", "172.18.0.5", "192.168.1.20", "127.0.0.1", "fd12:3456::1", "::ffff:10.1.2.3", "::1"):
    bad = [signin("nobody-%d@x.test" % i, "x-%d" % i, addr) for i in range(100)]
    st = signin("ben", PW["ben"], addr)
    check(st == 200 and 429 not in bad, f"{addr}: 100 failures over many names never block the address", (st, set(bad)))
check(not server._FAILS_ADDR, "no private address is even counted")

# ---- a public address has a higher ceiling, and only that address waits -----------------------------------------------
fresh()
pub = "203.0.113.7"
bad = [signin("nobody-%d@x.test" % i, "x", pub) for i in range(60)]
check(bad == [401] * 60, "60 failures from one public address (over 60 names) are answered", set(bad))
check(signin("ben", PW["ben"], pub) == 429, "the 61st try from that public address waits")
check(signin("ben", PW["ben"], "198.51.100.1") == 200, "another public address signs ben in")
check(signin("ben", PW["ben"], "10.0.0.9") == 200, "...and so does the office behind the proxy")
fresh()
bad = [signin("nobody-%d@x.test" % i, "x", pub) for i in range(30)]
check(signin("ben", PW["ben"], pub) == 200, "one office's 30 mistakes in 5 minutes do not block it")

# ---- the maps stay bounded ------------------------------------------------------------------------------------------
fresh()
for i in range(server._FAIL_TRACK + 2000):
    server._note_fail("203.0.%d.%d" % (i // 250 % 250, i % 250 + 1), "spray-%d@x.test" % i)
check(len(server._FAILS) <= server._FAIL_TRACK and len(server._FAILS_ADDR) <= server._FAIL_TRACK,
      f"{server._FAIL_TRACK + 2000} names and addresses tried: at most {server._FAIL_TRACK} of each are kept",
      (len(server._FAILS), len(server._FAILS_ADDR)))
check("spray-0@x.test" not in server._FAILS and f"spray-{server._FAIL_TRACK + 1999}@x.test" in server._FAILS,
      "the oldest name goes first")
for _ in range(80):
    server._note_fail("203.0.113.9", "same@x.test")
check(len(server._FAILS["same@x.test"]) == server._FAIL_NAME_MAX and len(server._FAILS_ADDR["203.0.113.9"]) == server._FAIL_ADDR_MAX,
      "one name's or address's list holds at most its limit of times")

# ---- the activity log: the account tried, never a password, never an unknown name's text ------------------------------
fresh()
signin("ann", "s3cret-typed-wrong")
signin("my-password-typed-in-the-name-box", "x")
if _had_audit:
    # this build has its own activity log (the redesign's takeoff_engine/audit.py): read what it wrote
    lines = [json.loads(ln) for f in sorted((_JOBS / "audit").glob("*.jsonl"))
             for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    LOGGED[:] = [e for e in lines if e.get("action") == "login_failed"][-2:]
txt = json.dumps(LOGGED)
check([e.get("action") for e in LOGGED] == ["login_failed", "login_failed"] and LOGGED[0].get("name") == "ann",
      "each failed sign-in is one activity-log line naming the account tried", LOGGED)
check("s3cret" not in txt and "my-password-typed" not in txt and LOGGED[1].get("name") == "(no such account)",
      "no password is logged, and a name that is not an account is not kept as typed", LOGGED)

# ---- with no accounts (one site password) every name shares one count ----------------------------------------------
fresh()
_users.save({})
server._UI_PASSWORD = "site-word-5521"
got = [signin("name-%d" % i, "wrong") for i in range(12)]
check(got == [401] * 12 and signin("anyone", "site-word-5521") == 429,
      "single-password mode: 12 failures under 12 different names still make everyone wait (one password)")

print(f"\n{'PASS' if not fails else 'FAIL'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
