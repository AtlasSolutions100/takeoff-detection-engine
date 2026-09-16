"""ACCOUNTS FROM INSIDE THE APP (2026-09-15): the staff-only /api/users
routes, pushed through the ASGI interface so the auth middleware is in the
loop (the same technique as test_orphan_job.py; fastapi.testclient is
unusable in this venv).

Pinned:
  * a client account gets 404 on every account route (the page must not
    advertise itself);
  * staff lists, adds (12-char temporary password returned ONCE, must_change
    set, the new login works and /api/me reports must_change), refuses a
    duplicate case-insensitively, refuses a whitespace name, a short
    password and the shared default password;
  * staff cannot remove themselves; nobody can remove or demote the LAST
    staff account; a removed account's sign-in and old cookie both die;
  * reset issues a fresh temporary password and the old one stops working;
  * on a box with no accounts the gate answers 409 with the CLI command.

Fails on aeae460 (no such routes: every call is 404).

Dependency-free:  python tests/test_users_api.py
"""

import asyncio
import json
import os
import sys
import tempfile
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="users_api_")
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="users_api_lg_")
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("TAKEOFF_HOSTED", None)

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    if ok:
        print("ok:", msg)
    else:
        fails += 1
        print("FAIL:", msg)


def call(method, path, body=None, cookie=None):
    headers = [(b"host", b"testserver")]
    raw = b""
    if body is not None:
        raw = json.dumps(body).encode()
        headers.append((b"content-type", b"application/json"))
        headers.append((b"content-length", str(len(raw)).encode()))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    # ASGI: scope["path"] is the DECODED path (a browser sends %40, Starlette
    # matches {username} against "@"); raw_path keeps the bytes as sent.
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "http",
             "path": urllib.parse.unquote(path),
             "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": headers, "client": ("127.0.0.1", 1234),
             "server": ("testserver", 80)}
    out = {"status": None, "headers": [], "body": b""}

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = msg.get("headers", [])
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    asyncio.run(server.app(scope, receive, send))
    try:
        js = json.loads(out["body"].decode() or "null")
    except ValueError:
        js = None
    return out["status"], js, out["headers"]


def login(name, pw):
    st, js, hdrs = call("POST", "/api/login", {"username": name, "password": pw})
    if st != 200:
        return st, None
    ck = ""
    for k, v in hdrs:
        if k.lower() == b"set-cookie" and v.startswith(b"tk_session="):
            ck = v.decode().split(";")[0]
    return st, ck


# ---- fixtures: one staff, one client -----------------------------------------
_users.add_user("boss@wo.test", "bosspass123", staff=True)
_users.add_user("alice@client.test", "alicepass123")
st, BOSS = login("boss@wo.test", "bosspass123")
check(st == 200 and BOSS, "boss signs in")
st, ALICE = login("alice@client.test", "alicepass123")
check(st == 200 and ALICE, "alice signs in")

# ---- a client cannot see the page exists --------------------------------------
for m, p, b in [("GET", "/api/users", None), ("POST", "/api/users", {"username": "x@y.z"}),
                ("DELETE", "/api/users/boss%40wo.test", None),
                ("POST", "/api/users/boss%40wo.test/reset", {}),
                ("POST", "/api/users/boss%40wo.test/staff", {"staff": False})]:
    st, js, _ = call(m, p, b, ALICE)
    check(st == 404, f"client gets 404 on {m} {p}")

# ---- staff lists ---------------------------------------------------------------
st, js, _ = call("GET", "/api/users", None, BOSS)
check(st == 200 and js["me"] == "boss@wo.test", "staff lists accounts and is told who they are")
check([u["user"] for u in js["users"]] == ["alice@client.test", "boss@wo.test"], "list is sorted by name")
check(next(u for u in js["users"] if u["user"] == "boss@wo.test")["staff"] is True, "staff flag reported")

# ---- staff adds -----------------------------------------------------------------
st, js, _ = call("POST", "/api/users", {"username": "new@client.test", "staff": False}, BOSS)
check(st == 200 and js["ok"], "staff creates an account")
TEMP = js.get("temp_password") or ""
check(len(TEMP) == 12 and TEMP.isalnum(), "a 12-character temporary password comes back once")
check(js["must_change"] is True and js["staff"] is False, "the new account must change its password and is not staff")
check(_users.must_change("new@client.test"), "must_change is stored")
st, NEW = login("new@client.test", TEMP)
check(st == 200 and NEW, "the temporary password signs in")
st, js, _ = call("GET", "/api/me", None, NEW)
check(st == 200 and js["must_change"] is True and js["staff"] is False, "/api/me reports must_change for the new account")
st, js, _ = call("GET", "/api/users", None, NEW)
check(st == 403, "while the password is temporary the account can do nothing else (403 from the middleware)")

# ---- refusals on add ------------------------------------------------------------
st, js, _ = call("POST", "/api/users", {"username": "NEW@Client.test"}, BOSS)
check(st == 409, "a duplicate (case-insensitive) is refused with 409")
st, js, _ = call("POST", "/api/users", {"username": "two words"}, BOSS)
check(st == 400, "a name with whitespace is refused")
st, js, _ = call("POST", "/api/users", {"username": ""}, BOSS)
check(st == 400, "an empty name is refused")
st, js, _ = call("POST", "/api/users", {"username": "s@c.test", "password": "short"}, BOSS)
check(st == 400, "a short chosen password is refused")
st, js, _ = call("POST", "/api/users", {"username": "s@c.test", "password": server.DEFAULT_PASSWORD}, BOSS)
check(st == 400, "the shared default password is refused")
st, js, _ = call("POST", "/api/users", {"username": "chosen@c.test", "password": "chosen-by-boss-1"}, BOSS)
check(st == 200 and js["must_change"] is True and js["temp_password"] == "chosen-by-boss-1",
      "a chosen password is accepted but still must be changed at first sign-in")

# ---- staff toggle, the self rule and the last-staff rule -----------------------------
st, js, _ = call("POST", "/api/users/boss%40wo.test/staff", {"staff": False}, BOSS)
check(st == 400 and "own role" in js["detail"], "you cannot change your own role (the owner demoted himself on day one)")
st, js, _ = call("POST", "/api/users/boss%40wo.test/staff", {"staff": True}, BOSS)
check(st == 400, "not even to re-state it")
st, js, _ = call("POST", "/api/users/new%40client.test/staff", {"staff": True}, BOSS)
check(st == 200 and _users.is_staff("new@client.test"), "staff can promote an account to staff")
st, js, _ = call("POST", "/api/users/new%40client.test/staff", {"staff": False}, BOSS)
check(st == 200 and not _users.is_staff("new@client.test"), "and demote it again while another staff remains")
st, js, _ = call("POST", "/api/users/nobody%40x.test/staff", {"staff": True}, BOSS)
check(st == 404, "unknown account -> 404")

# ---- remove ------------------------------------------------------------------------
st, js, _ = call("DELETE", "/api/users/boss%40wo.test", None, BOSS)
check(st == 400, "you cannot remove yourself")
st, js, _ = call("DELETE", "/api/users/nobody%40x.test", None, BOSS)
check(st == 404, "removing an unknown account -> 404")
st, js, _ = call("DELETE", "/api/users/alice%40client.test", None, BOSS)
check(st == 200 and js["removed"] == "alice@client.test", "staff removes a client account")
st, _ = login("alice@client.test", "alicepass123")
check(st == 401, "the removed account can no longer sign in")
st, js, _ = call("GET", "/api/me", None, ALICE)
check(st == 401, "and its old cookie is dead")
# the last-staff rule from the OTHER side: promote new, let new (with a
# settled password) demote the boss - allowed, because new remains staff -
# and the boss loses the page at once; then new cannot demote itself.
st, js, _ = call("POST", "/api/users/new%40client.test/staff", {"staff": True}, BOSS)
_users.set_password("new@client.test", "new-chose-this-1")   # clears must_change
st, NEW2 = login("new@client.test", "new-chose-this-1")
check(st == 200 and NEW2, "new signs in with a settled password as staff")
st, js, _ = call("POST", "/api/users/boss%40wo.test/staff", {"staff": False}, NEW2)
check(st == 200, "another staff account can demote the boss while it remains staff")
st, js, _ = call("GET", "/api/users", None, BOSS)
check(st == 404, "a demoted account immediately loses the page")
st, js, _ = call("POST", "/api/users/new%40client.test/staff", {"staff": False}, NEW2)
check(st == 400, "the remaining staff cannot demote itself either (self rule, and last staff)")
_users.set_staff("boss@wo.test", True)      # restore for the rest
st, js, _ = call("POST", "/api/users/new%40client.test/staff", {"staff": False}, BOSS)
check(st == 200, "restored boss demotes new")

# ---- reset -------------------------------------------------------------------------
_users.set_password("new@client.test", "settled-by-them-1")   # they changed it again
check(not _users.must_change("new@client.test"), "fixture: new chose their own password")
st, js, _ = call("POST", "/api/users/new%40client.test/reset", {}, BOSS)
check(st == 200 and len(js["temp_password"]) == 12 and js["must_change"] is True, "reset issues a fresh temporary password")
st, _ = login("new@client.test", "settled-by-them-1")
check(st == 401, "the old password stops working")
st, ck = login("new@client.test", js["temp_password"])
check(st == 200, "the new temporary password works")
check(_users.must_change("new@client.test"), "and must be changed at sign-in")

# ---- no accounts at all: the gate names the CLI ---------------------------------------
_users.save({})
st, js, _ = call("GET", "/api/users", None, None)
check(st == 409 and "takeoff_engine.users add" in (js or {}).get("detail", ""),
      "on a box with no accounts the gate answers 409 and names the CLI command")
st, js, _ = call("POST", "/api/users", {"username": "first@wo.test"}, None)
check(st == 409, "the first account cannot be created from the browser")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all users-api checks passed")
