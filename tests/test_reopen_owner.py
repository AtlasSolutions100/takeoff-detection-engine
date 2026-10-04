"""Reopening a job must NEVER mint a shared (owner None) row for a client's
document.

THE LEAK (ac09cb3 and earlier). GET /api/job/{id}/open reads the registry
row and, when the row has no `sizes` (rows written before sizes existed, or
a classification that never finished), falls back to `_register(src)`.
_register's default owner is None, and None is the SHARED marker: the app
lists an owner-less row to every signed-in account, and every account may
open, render and detect it. So one reopen by the document's own owner - or
by staff on their behalf - produced a brand-new shared row pointing at that
owner's PDF. The ORIGINAL row kept its owner, which is why looking at that
one row would not have shown it: the leak is a SECOND row for the same file.

HOW _register BEHAVES, so the fix is the right one. Its reuse key is
(path, owner, mtime) and it only returns a reused row when that row already
has sizes. It never rewrites an existing row's owner - the only field it
repairs on reuse is a null mtime. So an owner-less call for a file whose
only row is owned by someone else cannot match anything and always takes
the fresh-row path, and the fresh row carries whatever owner the caller
passed. The fix is therefore at the call site: reopen passes the row's own
owner through, and this file pins that a reopen leaves every row for the
file with the owner the row started with, and that a stranger still cannot
reach the job the reopen returned.

Same ASGI driver as tests/test_orphan_job.py (fastapi.testclient wants a
different httpx than this venv has), so the request goes through the real
session middleware - which is where bob is stopped.

Dependency-free:  python tests/test_reopen_owner.py
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="reopen_test_")
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="reopen_test_lg_")
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("TAKEOFF_HOSTED", None)

import pymupdf  # noqa: E402

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

JOBS_DIR = server.JOBS_DIR
ALICE, BOB, STAFF = "alice_synth", "bob_synth", "staff_synth"

# classification would spawn a thread that reads the PDF after the test has
# moved on; the ownership question does not depend on it
server._classify_async = lambda *a, **k: None


def make_pdf(name: str) -> Path:
    p = JOBS_DIR / name
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    doc.save(str(p))
    doc.close()
    return p


def seed_row(jid: str, pdf: Path, owner) -> None:
    """A registry row as the pre-sizes era (or an interrupted open) left it:
    path and owner recorded, sizes never filled in."""
    server.JOBS[jid] = {"path": str(pdf), "pages": 1,
                        "mtime": round(pdf.stat().st_mtime, 2),
                        "kinds": None, "sizes": None, "owner": owner,
                        "status": {}}


def rows_for(pdf: Path) -> dict:
    return {jid: j.get("owner") for jid, j in server.JOBS.items()
            if j.get("path") == str(pdf)}


# ------------------------------------------------------------ ASGI driver
async def _asgi(method, path, body: bytes, cookie):
    headers = [(b"host", b"testserver"), (b"x-core-client", b"3"),
               (b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode())]
    if cookie is not None:
        headers.append((b"cookie", f"{server.SESSION_COOKIE}={cookie}".encode()))
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"",
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
    st, data = asyncio.run(_asgi(method, path, raw, cookie))
    try:
        return st, json.loads(data)
    except ValueError:
        return st, data


def listed(cookie) -> set:
    """Job ids this account sees on BOTH listing routes."""
    st, projects = call("GET", "/api/projects", None, cookie)
    assert st == 200, st
    st, recent = call("GET", "/api/recent", None, cookie)
    assert st == 200, st
    return ({p["job_id"] for p in projects["projects"]}
            | {r["job_id"] for r in recent})


def can_reach(jid: str, cookie) -> dict:
    """What one account can do with a job: open it, see it listed, render
    its first page."""
    st_open, _ = call("GET", f"/api/job/{jid}/open", None, cookie)
    st_img, _ = call("GET", f"/api/job/{jid}/page/0/image", None, cookie)
    return {"open": st_open, "listed": jid in listed(cookie), "image": st_img}


CAN = {"open": 200, "listed": True, "image": 200}
CANNOT = {"open": 404, "listed": False, "image": 404}


def setup_accounts() -> dict:
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


# ================================================================== tests
def _owned_row_reopened_by(who: str):
    """The shape of the leak, driven by `who` (the owner or staff)."""
    ck = setup_accounts()
    server.JOBS.clear()
    pdf = make_pdf(f"alice-{who}.pdf")
    seed_row("a1a1a1a1a1a1", pdf, ALICE)
    n_before = len(server.JOBS)

    st, r = call("GET", "/api/job/a1a1a1a1a1a1/open", None, ck[who])
    assert st == 200, (who, st, r)
    got = r["job_id"]
    assert r["sizes"], r

    # THE ASSERTION THE LEAK FAILS: every registry row for alice's file is
    # alice's. On the pre-fix tree the reopen mints a second row for the same
    # PDF with owner None, i.e. shared with every account on the box.
    assert rows_for(pdf) == {jid: ALICE for jid in rows_for(pdf)}, \
        f"a row for alice's PDF is not alice's after reopen by {who}: " \
        f"{rows_for(pdf)}"
    assert server.JOBS["a1a1a1a1a1a1"]["owner"] == ALICE
    assert len(server.JOBS) in (n_before, n_before + 1), len(server.JOBS)

    # and the consequences, on the job the reopen handed back
    assert can_reach(got, ck[BOB]) == CANNOT, (who, can_reach(got, ck[BOB]))
    assert can_reach("a1a1a1a1a1a1", ck[BOB]) == CANNOT
    assert can_reach(got, ck[ALICE]) == CAN, (who, can_reach(got, ck[ALICE]))
    assert can_reach(got, ck[STAFF]) == CAN, (who, can_reach(got, ck[STAFF]))
    return got


def test_1_owner_reopens_her_unsized_row():
    _owned_row_reopened_by(ALICE)


def test_2_staff_reopens_a_clients_unsized_row():
    """Staff reopening on a client's behalf must not hand the client's
    document to the other clients either - the row stays the client's, not
    staff's and not nobody's."""
    got = _owned_row_reopened_by(STAFF)
    assert server.JOBS[got]["owner"] == ALICE, server.JOBS[got]


def test_3_stranger_is_refused_and_nothing_is_minted():
    ck = setup_accounts()
    server.JOBS.clear()
    pdf = make_pdf("alice-bob.pdf")
    seed_row("a1a1a1a1a1a1", pdf, ALICE)
    st, r = call("GET", "/api/job/a1a1a1a1a1a1/open", None, ck[BOB])
    # 404, not 403: a 403 would confirm the document exists
    assert st == 404 and r == {"detail": "unknown job"}, (st, r)
    assert rows_for(pdf) == {"a1a1a1a1a1a1": ALICE}, rows_for(pdf)
    assert can_reach("a1a1a1a1a1a1", ck[BOB]) == CANNOT


def test_4_shared_row_stays_shared():
    """The demo corpus and anything registered before logins existed is
    owner None on purpose. A reopen must not privatise it to whoever
    happened to click first."""
    ck = setup_accounts()
    server.JOBS.clear()
    # named into a shared set: since 2026-10-01 an owner-less row outside the
    # demo cards / shared sets is staff-only (tests/test_shared_rows_manifest.py)
    pdf = make_pdf("C3.0-Site-Plan-Rev.1.pdf")             # a shared-set sheet, by its exact name
    seed_row("5e5e5e5e5e5e", pdf, None)
    st, r = call("GET", "/api/job/5e5e5e5e5e5e/open", None, ck[ALICE])
    assert st == 200, (st, r)
    got = r["job_id"]
    assert rows_for(pdf) == {jid: None for jid in rows_for(pdf)}, rows_for(pdf)
    for u in (ALICE, BOB, STAFF):
        assert can_reach(got, ck[u]) == CAN, (u, can_reach(got, ck[u]))


def test_5_register_never_replaces_an_owner_on_reuse():
    """_register at the source: a finished row (sizes present) matched by
    path+mtime is reused only for its OWN owner, and reuse never rewrites
    the owner - an owner-less call for someone else's file gets its own
    row and touches nothing of theirs."""
    server.JOBS.clear()
    pdf = make_pdf("reuse.pdf")
    r = server._register(pdf, classify=False, owner=ALICE)
    jid = r["job_id"]
    assert server.JOBS[jid]["owner"] == ALICE and server.JOBS[jid]["sizes"]
    again = server._register(pdf, classify=False, owner=ALICE)
    assert again["job_id"] == jid, "same file, same owner: reused"
    assert server.JOBS[jid]["owner"] == ALICE
    other = server._register(pdf, classify=False, owner=None)
    assert other["job_id"] != jid, "owner is part of the reuse key"
    assert server.JOBS[jid]["owner"] == ALICE, "alice's row is untouched"
    assert server.JOBS[other["job_id"]]["owner"] is None
    # an interrupted open (mtime None) is reclaimed by path+OWNER, and the
    # repair touches mtime only
    server.JOBS[jid]["mtime"] = None
    assert server._register(pdf, classify=False, owner=ALICE)["job_id"] == jid
    assert server.JOBS[jid]["owner"] == ALICE
    assert server.JOBS[jid]["mtime"] is not None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} tests passed")
