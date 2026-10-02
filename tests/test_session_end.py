"""SESSIONS CAN BE ENDED (2026-10-02, release item 3).

THE HOLE (v1.8.5 too). The session cookie was a signed name + expiry and nothing on the server listed it, so a copied
cookie survived sign-out, a password change left every other session signed in, and after a staff reset an OLD cookie
was held at "choose a new password" and ALLOWED to choose it: a reset could hand the account to whoever held a stolen
session.

NOW: each account record carries a session counter `sv` (users.py, ENDING SESSIONS), the cookie carries the counter it
was made with, and a cookie whose counter is lower than the account's is not a session. It moves on at sign-out (every
session of that account), a password change (the browser that changed it gets a fresh cookie), a staff reset, and staff
rights taken away; a removed account made again under the same name does not let an old cookie back in. A cookie made
before this deploy (no counter) still signs in, and users.json stays readable by v1.8.5's own loader.

Every request goes through the real middleware with no server listening.

    python tests/test_session_end.py
"""
import asyncio
import base64
import hashlib
import hmac
import http.cookies
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_JOBS = Path(tempfile.mkdtemp(prefix="sess_end_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(_JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="sess_end_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ.pop("TAKEOFF_UI_PASSWORD", None)

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

server._classify_async = lambda *a, **k: None
server._UI_PASSWORD = None
PW = {"ann": "orchid-lantern-4471", "ben": "basalt-meadow-9083", "boss": "granite-sparrow-2210",
      "deb": "copper-willow-5530"}
# the store as v1.8.5 left it: plain records, no counter (written straight to the file, as the live box holds it)
_users.store_path().write_text(json.dumps({
    "ann": _users.hash_password(PW["ann"]), "ben": _users.hash_password(PW["ben"]),
    "boss": dict(_users.hash_password(PW["boss"]), staff=True),
    "deb": dict(_users.hash_password(PW["deb"]), staff=True)}, indent=1, sort_keys=True))

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
    out = {"status": None, "body": b"", "cookie": None}

    async def receive():
        if not sent["b"]:
            sent["b"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(m):
        if m["type"] == "http.response.start":
            out["status"] = m["status"]
            for k, v in m.get("headers", []):
                if k.lower() == b"set-cookie":
                    c = http.cookies.SimpleCookie()
                    c.load(v.decode())
                    if server.SESSION_COOKIE in c:
                        out["cookie"] = c[server.SESSION_COOKIE].value
        elif m["type"] == "http.response.body":
            out["body"] += m.get("body", b"")

    await server.app(scope, receive, send)
    return out


def call(method, path, cookie=None, body=None):
    return asyncio.run(_asgi(method, path, cookie, b"" if body is None else json.dumps(body).encode()))


def login(name, pw):
    r = call("POST", "/api/login", None, {"username": name, "password": pw})
    assert r["status"] == 200 and r["cookie"], (name, r["status"], r["body"][:200])
    return r["cookie"]


def me(cookie):
    return call("GET", "/api/me", cookie)["status"]


def v185_cookie(name):
    """A cookie exactly as v1.8.5's _sign_session made it: {"u", "exp"}, no counter."""
    raw = base64.urlsafe_b64encode(json.dumps({"u": name, "exp": int(time.time()) + 7 * 86400},
                                              separators=(",", ":")).encode()).rstrip(b"=")
    return raw.decode() + "." + hmac.new(server._session_secret(), raw, hashlib.sha256).hexdigest()[:32]


# ---- the deploy itself signs nobody out ----------------------------------------------------------------------------
old = {n: v185_cookie(n) for n in PW}
check(all(me(c) == 200 for c in old.values()), "a v1.8.5-shaped cookie (no counter) still signs in after the upgrade")
check(_users.store_path().read_text().count('"sv"') == 0, "reading the store adds no counter to anyone's record")

# ---- sign-out ends every session of that account ---------------------------------------------------------------------
a1, a2 = login("ann", PW["ann"]), login("ann", PW["ann"])
copy = a1
r = call("POST", "/api/logout", a1)
check(r["status"] == 200, "sign-out answers 200", r["status"])
check(me(copy) == 401 and me(a2) == 401 and me(old["ann"]) == 401,
      "after sign-out a copied cookie, her other browser and her pre-deploy cookie are all signed out")
check(me(old["ben"]) == 200, "another account's sessions are untouched by her sign-out")
a3 = login("ann", PW["ann"])
check(me(a3) == 200, "she signs in again normally")

# ---- a password change: this browser stays in, every other session ends ------------------------------------------
b1, b2 = login("ben", PW["ben"]), login("ben", PW["ben"])
r = call("POST", "/api/change-password", b1, {"password": "new-harbor-7781"})
check(r["status"] == 200 and r["cookie"], "the password change answers 200 with a fresh cookie", r["status"])
check(me(r["cookie"]) == 200, "the browser that changed it stays signed in")
check(me(b1) == 401 and me(b2) == 401 and me(old["ben"]) == 401,
      "his old cookie in that browser, his other browser and his pre-deploy cookie are signed out")

# ---- a staff reset: an OLD cookie can no longer choose the new password ---------------------------------------------
boss = login("boss", PW["boss"])
stolen = login("ann", PW["ann"])
r = call("POST", "/api/users/ann/reset", boss)
check(r["status"] == 200, "staff reset answers 200", r["status"])
temp = json.loads(r["body"])["temp_password"]
check(me(stolen) == 401 and me(a3) == 401, "after the reset her old sessions are signed out")
r = call("POST", "/api/change-password", stolen, {"password": "thief-chosen-0001"})
check(r["status"] == 401, "an old cookie after the reset CANNOT set the password (was 200)", r["status"])
check(call("POST", "/api/login", None, {"username": "ann", "password": "thief-chosen-0001"})["status"] == 401,
      "...and the thief's word does not sign in")
t = login("ann", temp)
check(call("GET", "/api/projects", t)["status"] == 403, "the one-time password's session is held at 'choose a new password'")
r = call("POST", "/api/change-password", t, {"password": "ann-own-choice-42"})
check(r["status"] == 200 and me(r["cookie"]) == 200 and me(t) == 401,
      "a session made with the one-time password AFTER the reset sets it; the one-time session then ends")

# ---- a move to the read-only role (viewer, where a build has roles) ends that account's sessions -------------------
d1 = login("deb", PW["deb"])
r = call("POST", "/api/users/deb/staff", boss, {"staff": False})
check(r["status"] == 200 and me(d1) == 200 and call("GET", "/api/users", d1)["status"] == 404,
      "taking staff away: she stays signed in and loses the staff pages at once (rights are read per request)")
store = dict(_users.load())
store["deb"] = dict(store["deb"], role="viewer")   # what the redesign's set_role writes for "viewer"
_users.save(store)
check(me(d1) == 401, "made a viewer: her sessions end")
d2 = login("deb", PW["deb"])
store = dict(_users.load())
store["deb"] = dict(store["deb"], role="viewer", note="unrelated edit")
_users.save(store)
check(me(d2) == 200, "a save that leaves a viewer a viewer signs nobody out")
store = dict(_users.load())
store["deb"] = {k: v for k, v in store["deb"].items() if k not in ("role", "note")}
_users.save(store)
r = call("POST", "/api/users/deb/staff", boss, {"staff": True})
check(r["status"] == 200 and me(d2) == 200, "a role given back or raised does not sign her out")

# ---- removal ends them; an account made again under the same name does not bring an old cookie back -------------
e1 = login("ben", "new-harbor-7781")
r = call("DELETE", "/api/users/ben", boss)
check(r["status"] == 200 and me(e1) == 401, "removing an account ends its sessions (as before)")
r = call("POST", "/api/users", boss, {"username": "ben", "password": "brand-new-ben-99"})
check(r["status"] == 200 and me(e1) == 401 and me(old["ben"]) == 401,
      "the same name made again: his old cookies stay signed out")

# ---- the counter on disk, and v1.8.5's own loader reads the file -------------------------------------------------
disk = json.loads(_users.store_path().read_text())
check(all(isinstance(disk[n].get("sv"), int) and disk[n]["sv"] > 0 for n in ("ann", "ben", "deb")),
      "the counter is a plain integer field on the records of the accounts it moved for", disk)
check(disk["boss"].get("sv") is None, "an account nothing happened to still has no counter field", disk["boss"])
LIVE = Path("C:/Users/kobys/core-release-scratch/parity/live/takeoff_engine/users.py")
if LIVE.exists():
    spec = importlib.util.spec_from_file_location("users_v185", LIVE)
    v185 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v185)
    got = v185.load()
    check(set(got) == set(disk) and v185.check("ann", "ann-own-choice-42") == "ann"
          and v185.check("ben", "brand-new-ben-99") == "ben" and v185.is_staff("deb"),
          "v1.8.5's loader reads the file this code wrote: every account, its password and its staff flag", sorted(got))
else:
    print("note: v1.8.5 extract not on this machine; its loader was not run")

print(f"\n{'PASS' if not fails else 'FAIL'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
