"""/api/ledger/stats lists by_job only for the caller's own jobs unless staff.

THE HOLE (b528e74 and earlier). The route returned ledger.stats() whole to
any signed-in account: by_job is keyed by job id - the one handle that
opens a document - so a contractor could read every other client's job
ids off it, and how many decisions each had recorded. No owner filter, no
staff gate.

THE FIX: a non-staff caller's by_job is filtered to the ids of jobs they
own; staff (and single-password mode, where there is nobody to keep apart)
see all of them. The aggregate counts carry no id and are unchanged.

Same ASGI driver as tests/test_demo_source_scan.py. The ledger is a
throwaway (TAKEOFF_LEDGER_DIR) and the events are written through
ledger.record, the same call every server hook makes.

Dependency-free:  python tests/test_ledger_stats_owner.py
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_JOBS = tempfile.mkdtemp(prefix="ledger_owner_test_")
os.environ["TAKEOFF_JOBS_DIR"] = _JOBS
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="ledger_owner_test_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = _JOBS
os.environ.pop("TAKEOFF_UI_PASSWORD", None)

import pymupdf  # noqa: E402

import server  # noqa: E402
from takeoff_engine import ledger, users as _users  # noqa: E402

ALICE, BOB, STAFF = "alice_synth", "bob_synth", "staff_synth"
ORPHAN = "0rphan0rphan"          # an id the registry no longer holds

server._classify_async = lambda *a, **k: None


def pdf_bytes(salt: bytes) -> bytes:
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    raw = doc.tobytes()
    doc.close()
    return raw + b"\n%" + salt + b"\n"


# ------------------------------------------------------------ ASGI driver
async def _asgi(method, path, body: bytes, cookie, ctype, query=b""):
    headers = [(b"host", b"testserver"), (b"x-core-client", b"2"),
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
    out = {"status": None, "body": b""}

    async def receive():
        if not sent["body"]:
            sent["body"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    await server.app(scope, receive, send)
    return out["status"], out["body"]


def call(method, path, body=None, cookie=None):
    raw = b"" if body is None else json.dumps(body).encode()
    st, data = asyncio.run(_asgi(method, path, raw, cookie, "application/json"))
    try:
        return st, json.loads(data)
    except ValueError:
        return st, data


def upload(filename: str, data: bytes, cookie):
    bnd = "----takeoffledgerowner"
    body = (("--%s\r\nContent-Disposition: form-data; name=\"file\"; "
             "filename=\"%s\"\r\nContent-Type: application/pdf\r\n\r\n"
             % (bnd, filename)).encode() + data
            + ("\r\n--%s--\r\n" % bnd).encode())
    st, raw = asyncio.run(_asgi("POST", "/api/upload", body, cookie,
                                "multipart/form-data; boundary=" + bnd,
                                query=b"classify=0"))
    return st, json.loads(raw)


def stats(cookie) -> dict:
    st, d = call("GET", "/api/ledger/stats", None, cookie)
    assert st == 200, (st, d)
    return d


def per_user_mode() -> dict:
    users_file = _users.store_path()
    if users_file.exists():
        users_file.unlink()
    server._UI_PASSWORD = None
    _users.save({ALICE: _users.hash_password("orchid-lantern-4471"),
                 BOB: _users.hash_password("basalt-meadow-9083"),
                 STAFF: dict(_users.hash_password("granite-sparrow-2210"),
                             staff=True)})
    server.JOBS.clear()
    return {u: server._sign_session(u) for u in (ALICE, BOB, STAFF)}


def seed() -> tuple:
    # a fresh ledger per scenario: ledger_dir() reads the env on every call
    os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="ledger_owner_test_lg_")
    ck = per_user_mode()
    st, r = upload("Alice Bid Set.pdf", pdf_bytes(b"alice"), ck[ALICE])
    assert st == 200, (st, r)
    a_jid = r["job_id"]
    st, r = upload("Bob Bid Set.pdf", pdf_bytes(b"bob"), ck[BOB])
    assert st == 200, (st, r)
    b_jid = r["job_id"]
    assert server.JOBS[a_jid]["owner"] == ALICE
    assert server.JOBS[b_jid]["owner"] == BOB
    for _ in range(3):
        assert ledger.record("style_label", a_jid, 0, None, {"label": "x"}, None)
    for _ in range(2):
        assert ledger.record("scale_set", b_jid, 0, None, {"ft": 20}, None)
    assert ledger.record("job_deleted", ORPHAN, None, None, {}, None)
    return ck, a_jid, b_jid


# ================================================================== tests
def test_1_a_client_sees_only_their_own_job_ids():
    ck, a_jid, b_jid = seed()
    full = ledger.stats()
    assert set(full["by_job"]) == {a_jid, b_jid, ORPHAN}, full

    bob = stats(ck[BOB])
    # THE ASSERTION THE HOLE FAILS: on b528e74 bob's by_job carries alice's
    # id (and the orphan's) with their counts.
    assert set(bob["by_job"]) == {b_jid}, \
        "bob can read other accounts' job ids off /api/ledger/stats: %r" \
        % bob["by_job"]
    assert bob["by_job"][b_jid] == 2, bob
    alice = stats(ck[ALICE])
    assert alice["by_job"] == {a_jid: 3}, alice
    # the aggregate counts carry no id and are the same for everyone
    for d in (bob, alice):
        assert d["total_events"] == full["total_events"] == 6, d
        assert d["by_event_type"] == full["by_event_type"], d


def test_2_staff_and_single_password_mode_see_all():
    ck, a_jid, b_jid = seed()
    staff = stats(ck[STAFF])
    assert staff["by_job"] == ledger.stats()["by_job"], staff
    assert set(staff["by_job"]) == {a_jid, b_jid, ORPHAN}
    # single-password mode: no accounts, one shared seat, nothing to hide
    _users.store_path().unlink()
    server._UI_PASSWORD = "shared-site-password-1"
    st, r = call("POST", "/api/login",
                 {"username": "whoever", "password": "shared-site-password-1"})
    assert st == 200, (st, r)
    one = stats(server._sign_session(None))
    assert set(one["by_job"]) == {a_jid, b_jid, ORPHAN}, one
    server._UI_PASSWORD = None


def test_3_signed_out_is_refused():
    per_user_mode()
    st, _ = call("GET", "/api/ledger/stats", None, None)
    assert st == 401, st


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} tests passed")
