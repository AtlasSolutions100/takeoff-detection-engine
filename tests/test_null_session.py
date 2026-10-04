"""A session whose payload is {"u": null} is NOT a sign-in once accounts exist.

THE HOLE (b528e74 and earlier). Single-password mode - no users.json, just
TAKEOFF_UI_PASSWORD - is the state every box boots in, and /api/login there
issues a cookie whose signed payload is {"u": null, "exp": ...}. That cookie
lives seven days, and session.key persists across the switch to per-user
accounts, so it stays VALID after users.json appears. _read_session then
returns (True, None) and the middleware stores user=None: _forbidden_job
reads None as "single-password mode, let it through", and _is_staff() is
True whenever _who() is None. Demonstrated live on :9401 with three
accounts: /api/me 200 {user: null}, /api/recent listing alice's private
upload, /api/job/<alice>/open 200, her page image 200. A signed-in nobody
who outranked everybody.

THE FIX is at the one place the middleware derives `user`: with accounts on
the box, an ownerless session is treated exactly like no cookie at all, and
_is_staff is False for a nameless caller unless the box has no accounts.
Single-password mode is unchanged - the same cookie is the only signed-in
state that mode has, and test 2 pins that it keeps working.

The token is signed the way /api/login signs it, with the throwaway box's
own session.key (server._sign_session(None)), so what is being tested is
the middleware's reading of a GENUINE single-password cookie, not a forgery.

Same ASGI driver as tests/test_demo_source_scan.py, so every request goes
through the real session middleware.

Dependency-free:  python tests/test_null_session.py
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_JOBS = tempfile.mkdtemp(prefix="null_session_test_")
os.environ["TAKEOFF_JOBS_DIR"] = _JOBS
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="null_session_test_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = _JOBS
os.environ.pop("TAKEOFF_UI_PASSWORD", None)

import pymupdf  # noqa: E402
from starlette.requests import Request as _Req  # noqa: E402

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

JOBS_DIR = server.JOBS_DIR
ALICE, BOB, STAFF = "alice_synth", "bob_synth", "staff_synth"
SITE_PW = "shared-site-password-1"

server._classify_async = lambda *a, **k: None


def pdf_bytes(salt: bytes) -> bytes:
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    raw = doc.tobytes()
    doc.close()
    return raw + b"\n%" + salt + b"\n"


# ------------------------------------------------------------ ASGI driver
async def _asgi(method, path, body: bytes, cookie, ctype, query=b""):
    headers = [(b"host", b"testserver"), (b"x-core-client", b"3"),
               (b"content-type", ctype.encode()),
               (b"content-length", str(len(body)).encode())]
    if cookie is not None:
        headers.append((b"cookie", f"{server.SESSION_COOKIE}={cookie}".encode()))
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": quote(path).encode(),
             "query_string": query,
             "root_path": "", "headers": headers,
             "client": ("127.0.0.1", 40000), "server": ("testserver", 80)}
    sent = {"body": False}
    out = {"status": None, "body": b"", "headers": []}

    async def receive():
        if not sent["body"]:
            sent["body"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = msg.get("headers", [])
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    await server.app(scope, receive, send)
    return out["status"], out["body"], out["headers"]


def call(method, path, body=None, cookie=None):
    raw = b"" if body is None else json.dumps(body).encode()
    st, data, _ = asyncio.run(_asgi(method, path, raw, cookie,
                                    "application/json"))
    try:
        return st, json.loads(data)
    except ValueError:
        return st, data


def upload(filename: str, data: bytes, cookie):
    bnd = "----takeoffnullsession"
    body = (("--%s\r\nContent-Disposition: form-data; name=\"file\"; "
             "filename=\"%s\"\r\nContent-Type: application/pdf\r\n\r\n"
             % (bnd, filename)).encode() + data
            + ("\r\n--%s--\r\n" % bnd).encode())
    st, raw, _ = asyncio.run(_asgi("POST", "/api/upload", body, cookie,
                                   "multipart/form-data; boundary=" + bnd,
                                   query=b"classify=0"))
    try:
        return st, json.loads(raw)
    except ValueError:
        return st, raw


def probe(jid: str, cookie) -> dict:
    """The adversary's four requests, plus an upload: one dict per caller."""
    st_me, me = call("GET", "/api/me", None, cookie)
    st_rec, rec = call("GET", "/api/recent", None, cookie)
    st_open, _ = call("GET", f"/api/job/{jid}/open", None, cookie)
    st_img, _ = call("GET", f"/api/job/{jid}/page/0/image", None, cookie)
    st_up, _ = upload("probe.pdf", pdf_bytes(b"probe"), cookie)
    return {"me": st_me,
            "me_user": me.get("user") if st_me == 200 else "-",
            "recent": st_rec,
            "lists_alice": (isinstance(rec, list)
                            and any(r.get("job_id") == jid for r in rec)),
            "open": st_open, "image": st_img, "upload": st_up}


def per_user_mode() -> dict:
    users_file = _users.store_path()
    if users_file.exists():
        users_file.unlink()
    server._UI_PASSWORD = None
    _users.save({ALICE: _users.hash_password("orchid-lantern-4471"),
                 BOB: _users.hash_password("basalt-meadow-9083"),
                 STAFF: dict(_users.hash_password("granite-sparrow-2210"),
                             staff=True)})
    assert set(_users.load()) == {ALICE, BOB, STAFF}
    return {u: server._sign_session(u) for u in (ALICE, BOB, STAFF)}


def single_password_mode():
    users_file = _users.store_path()
    if users_file.exists():
        users_file.unlink()
    assert _users.load() == {}
    server._UI_PASSWORD = SITE_PW


def alice_uploads(ck) -> str:
    st, r = upload("Alice Private Bid Set.pdf", pdf_bytes(b"alice"), ck[ALICE])
    assert st == 200, (st, r)
    assert server.JOBS[r["job_id"]]["owner"] == ALICE
    return r["job_id"]


def fake_request(user) -> _Req:
    return _Req({"type": "http", "method": "GET", "path": "/", "headers": [],
                 "tk_user": user, "tk_staff": False})


# ================================================================== tests
def test_1_null_session_fails_closed_once_accounts_exist():
    """The adversary's script: a genuine single-password cookie replayed on
    a box that now has three accounts."""
    server.JOBS.clear()
    ck = per_user_mode()
    a_jid = alice_uploads(ck)

    null = server._sign_session(None)
    assert server._read_session(null) == (True, None), \
        "precondition: the cookie is validly signed for this box"

    # what a caller with NO cookie gets - the bar the null cookie must meet
    anon = probe(a_jid, None)
    assert anon == {"me": 401, "me_user": "-", "recent": 401,
                    "lists_alice": False, "open": 401, "image": 401,
                    "upload": 401}, anon

    got = probe(a_jid, null)
    # THE ASSERTION THE HOLE FAILS. On b528e74 this is
    # {"me": 200, "me_user": None, "recent": 200, "lists_alice": True,
    #  "open": 200, "image": 200, "upload": 200}.
    assert got == anon, \
        "a {\"u\": null} cookie was honoured on a per-user box: %r " \
        "(an anonymous caller gets %r)" % (got, anon)

    # and a person hitting the app itself is sent to the login page
    st, body, hdrs = asyncio.run(_asgi("GET", "/", b"", null,
                                       "application/json"))
    assert st == 303, (st, body)
    loc = dict((k.decode(), v.decode()) for k, v in hdrs).get("location", "")
    assert loc.startswith("/login"), loc

    # a real account is untouched
    assert probe(a_jid, ck[ALICE])["open"] == 200
    assert probe(a_jid, ck[BOB])["open"] == 404
    assert probe(a_jid, ck[STAFF])["open"] == 200

    # and nothing the null caller attempted registered a row
    assert all(j.get("owner") is not None for j in server.JOBS.values()), \
        server.JOBS


def test_2_single_password_mode_is_unchanged():
    """The same cookie, on a box with NO accounts, is the only signed-in
    state that mode has and must keep working exactly as before."""
    server.JOBS.clear()
    ck = per_user_mode()
    a_jid = alice_uploads(ck)
    single_password_mode()

    # the real login route mints it: the payload is {"u": null}
    st, r = call("POST", "/api/login",
                 {"username": "whoever", "password": SITE_PW})
    assert st == 200 and r == {"ok": True, "user": None}, (st, r)
    null = server._sign_session(None)
    assert server._read_session(null) == (True, None)

    got = probe(a_jid, null)
    assert got == {"me": 200, "me_user": None, "recent": 200,
                   "lists_alice": True, "open": 200, "image": 200,
                   "upload": 200}, got
    st, me = call("GET", "/api/me", None, null)
    assert me["accounts"] is False and me["staff"] is False, me

    # no cookie at all is still refused in that mode
    st, _ = call("GET", "/api/recent", None, None)
    assert st == 401, st

    # the upload the null caller made is a single-password row (owner None)
    assert any(j.get("owner") is None for j in server.JOBS.values())
    server.JOBS.clear()


def test_3_is_staff_needs_a_box_with_no_accounts():
    per_user_mode()
    assert server._is_staff(fake_request(None)) is False, \
        "a nameless caller counted as staff on a per-user box"
    assert server._is_staff(fake_request(ALICE)) is False
    single_password_mode()
    assert server._is_staff(fake_request(None)) is True, \
        "single-password mode must still treat the one seat as staff"


def test_4_a_deleted_accounts_cookie_is_not_a_sign_in():
    """A name the box no longer knows is no session either: carol logs
    in, her account is removed, and her still-valid cookie must meet the
    same bar as no cookie at all."""
    server.JOBS.clear()
    ck = per_user_mode()
    a_jid = alice_uploads(ck)
    carol = "carol_synth"
    store = _users.load()
    _users.save(dict(store, **{carol: _users.hash_password("lilac-harbor-7732")}))
    cookie = server._sign_session(carol)
    assert probe(a_jid, cookie)["me"] == 200, "precondition: carol is signed in"
    # the account goes away while the cookie is still out
    _users.save({k: v for k, v in _users.load().items() if k != carol})
    assert carol not in _users.load()
    anon = probe(a_jid, None)
    got = probe(a_jid, cookie)
    assert got == anon,         "a deleted account's cookie was honoured: %r (anonymous gets %r)" % (got, anon)
    assert probe(a_jid, ck[ALICE])["open"] == 200
    assert all(j.get("owner") is not None for j in server.JOBS.values()),         server.JOBS


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} tests passed")
