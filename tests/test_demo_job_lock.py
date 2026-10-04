"""A demo open whose registry row vanishes mid-open must return, not hang.

THE HANG (b528e74 and earlier). _demo_job's re-derive branch - the row for
this demo exists but its recorded mtime no longer matches the PDF - re-reads
the document, then takes JOBS_LOCK to write the new page count and sizes
into the SAME row. When the row was gone by then it called
_register(src, owner=None) from INSIDE that with-block. JOBS_LOCK is a
plain threading.Lock, not an RLock, and _register takes it too: the open
deadlocked, and because every route touches JOBS under that lock, every
request after it queued behind the dead one. One demo open whose row was
deleted (or reset by a concurrent open) while the PDF was being re-read
took the whole service down until a restart.

THE FIX: decide under the lock, call _register after the with-block.

The reproduction pops the row from JOBS at the exact moment the branch
re-reads the PDF (a pymupdf proxy on the server module), then drives
POST /api/demo/<name>/open through the real app on a daemon thread with a
timeout. On b528e74 the thread never returns; fixed, the route answers
200 with a freshly registered shared row and the lock is free again.

Same ASGI driver as tests/test_demo_source_scan.py.

Dependency-free:  python tests/test_demo_job_lock.py
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_JOBS = tempfile.mkdtemp(prefix="demo_lock_test_")
os.environ["TAKEOFF_JOBS_DIR"] = _JOBS
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="demo_lock_test_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = _JOBS
os.environ.pop("TAKEOFF_UI_PASSWORD", None)

import pymupdf  # noqa: E402

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

JOBS_DIR = server.JOBS_DIR
BOB = "bob_synth"
NAME = ("Belle Farms Structural Plans.pdf"
        if "Belle Farms Structural Plans.pdf" in server.DEMO_PLANS
        else sorted(server.DEMO_PLANS)[0])
TIMEOUT_S = 10.0

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


class _PopOnOpen:
    """server.pymupdf stand-in: the first open of `target` pops `jid` from
    the registry - the row vanishing exactly between _demo_job's two
    locked sections - then behaves like pymupdf."""

    def __init__(self, target: Path, jid: str):
        self.target, self.jid, self.fired = str(target), jid, 0

    def __getattr__(self, k):
        return getattr(pymupdf, k)

    def open(self, *a, **kw):
        if a and str(a[0]) == self.target and self.jid in server.JOBS:
            with server.JOBS_LOCK:
                server.JOBS.pop(self.jid, None)
            self.fired += 1
        return pymupdf.open(*a, **kw)


def setup():
    users_file = _users.store_path()
    if users_file.exists():
        users_file.unlink()
    server._UI_PASSWORD = None
    _users.save({BOB: _users.hash_password("basalt-meadow-9083")})
    server.JOBS.clear()
    server._DEMO_SRC.clear()
    server._DEMO_SCAN_AT = 0.0
    corpus = Path(tempfile.mkdtemp(prefix="demo_lock_corpus_")).resolve()
    src = corpus / NAME
    src.write_bytes(pdf_bytes(b"corpus"))
    # a SHARED row for the demo whose recorded mtime is stale, so _demo_job
    # takes the re-derive branch rather than the fresh one
    jid = "d0d0d0d0d0d0"
    server.JOBS[jid] = {"path": str(src), "pages": 1, "mtime": 1.0,
                        "kinds": None, "sizes": [[612.0, 792.0]],
                        "owner": None, "status": {}}
    assert server._demo_source(NAME) == src
    return src, jid, server._sign_session(BOB)


def test_1_row_popped_during_reread_returns_instead_of_hanging():
    src, jid, bob = setup()
    proxy = _PopOnOpen(src, jid)
    saved = server.pymupdf
    server.pymupdf = proxy
    result = {}

    def go():
        result["resp"] = call("POST", "/api/demo/%s/open" % NAME, None, bob)

    t = threading.Thread(target=go, daemon=True)
    try:
        t.start()
        t.join(TIMEOUT_S)
        # THE ASSERTION THE HANG FAILS: on b528e74 the thread is still
        # alive, blocked in _register on the lock _demo_job already holds.
        assert not t.is_alive(), \
            "the demo open has not returned after %.0f s with the row " \
            "popped mid-open: _register called under JOBS_LOCK" % TIMEOUT_S
        assert proxy.fired == 1, "the row was not popped mid-open"
        st, r = result["resp"]
        assert st == 200, (st, r)
        new_jid = r["job_id"]
        assert new_jid != jid and new_jid in server.JOBS, (new_jid, server.JOBS)
        assert server.JOBS[new_jid]["owner"] is None
        assert Path(server.JOBS[new_jid]["path"]) == src
        assert r["demo"] is True and r["pages"] == 1, r
    finally:
        server.pymupdf = saved

    # the lock is free, and the next open reuses that one row
    assert server.JOBS_LOCK.acquire(timeout=1.0), "JOBS_LOCK is still held"
    server.JOBS_LOCK.release()
    st, r2 = call("POST", "/api/demo/%s/open" % NAME, None, bob)
    assert st == 200 and r2["job_id"] == new_jid, (st, r2)
    assert sum(1 for j in server.JOBS.values() if j.get("owner") is None) == 1


def test_2_row_present_is_repaired_in_place():
    """The ordinary re-derive: the row stays, its mtime/pages/sizes are
    rewritten, no second row is minted."""
    src, jid, bob = setup()
    st, r = call("POST", "/api/demo/%s/open" % NAME, None, bob)
    assert st == 200 and r["job_id"] == jid, (st, r)
    assert server.JOBS[jid]["mtime"] == round(src.stat().st_mtime, 2)
    assert len(server.JOBS) == 1, server.JOBS


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} tests passed")
