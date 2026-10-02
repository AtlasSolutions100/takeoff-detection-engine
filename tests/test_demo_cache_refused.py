"""The demo-source CACHE re-applies the client-file refusal on every hit.

THE HOLE (b528e74 and earlier). _demo_source's writers - the shared-row
loop and the plan-root scan - refuse a client's file (_demo_scan_refused:
under JOBS_DIR, or registered to an owner). Its cache branch did not:
'hit = _DEMO_SRC.get(name); if p.exists(): return p'. So a client's path
that reached _DEMO_SRC by any other route - written before the scan was
pinned and still in memory, a row whose owner was set after the cache
filled, a code path that writes the cache directly - was served as the
demo until the process restarted: demo open 200, a SHARED row minted over
the client's PDF, every account able to open and render it.

THE FIX: a cache hit is served only while it is still ours - the path of
a SHARED row, or an unowned corpus file the scan would accept now
(_demo_cache_ok). Anything else is dropped from the cache and the lookup
falls through to the writers, which refuse it as before.

Same ASGI driver and degraded hosted environment as
tests/test_demo_source_scan.py.

Dependency-free:  python tests/test_demo_cache_refused.py
"""

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_JOBS = tempfile.mkdtemp(prefix="demo_cache_test_")
os.environ["TAKEOFF_JOBS_DIR"] = _JOBS
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="demo_cache_test_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = _JOBS
os.environ.pop("TAKEOFF_UI_PASSWORD", None)

import pymupdf  # noqa: E402

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

JOBS_DIR = server.JOBS_DIR
ALICE, BOB, STAFF = "alice_synth", "bob_synth", "staff_synth"
NAME = ("Belle Farms Structural Plans.pdf"
        if "Belle Farms Structural Plans.pdf" in server.DEMO_PLANS
        else sorted(server.DEMO_PLANS)[0])

server._classify_async = lambda *a, **k: None

_BASE_PDF = None


def pdf_bytes(salt: bytes) -> bytes:
    global _BASE_PDF
    if _BASE_PDF is None:
        doc = pymupdf.open()
        doc.new_page(width=612, height=792)
        _BASE_PDF = doc.tobytes()
        doc.close()
    return _BASE_PDF + b"\n%" + salt + b"\n"


def rows_for(pdf: Path) -> dict:
    rp = os.path.normcase(str(Path(pdf).resolve()))
    out = {}
    for jid, j in server.JOBS.items():
        try:
            if os.path.normcase(str(Path(j.get("path", "")).resolve())) == rp:
                out[jid] = j.get("owner")
        except OSError:
            continue
    return out


def shared_rows() -> dict:
    return {jid: j.get("path") for jid, j in server.JOBS.items()
            if j.get("owner") is None}


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
    bnd = "----takeoffdemocache"
    body = (("--%s\r\nContent-Disposition: form-data; name=\"file\"; "
             "filename=\"%s\"\r\nContent-Type: application/pdf\r\n\r\n"
             % (bnd, filename)).encode() + data
            + ("\r\n--%s--\r\n" % bnd).encode())
    st, raw = asyncio.run(_asgi("POST", "/api/upload", body, cookie,
                                "multipart/form-data; boundary=" + bnd,
                                query=b"classify=0"))
    return st, json.loads(raw)


def demo_open(cookie):
    return call("POST", "/api/demo/%s/open" % NAME, None, cookie)


def demo_card(cookie) -> dict:
    st, d = call("GET", "/api/demo-plans", None, cookie)
    assert st == 200, (st, d)
    return {p["name"]: p for p in d["plans"]}[NAME]


def can_open(jid: str, cookie) -> int:
    st, _ = call("GET", f"/api/job/{jid}/open", None, cookie)
    return st


def poison(path: Path):
    """Put a path in the cache the way nothing legitimate does any more,
    and make sure the lookup cannot simply be rescued by a rescan."""
    server._DEMO_SRC[NAME] = str(path)
    server._DEMO_SCAN_AT = time.time()      # scanned "just now"


def setup() -> dict:
    users_file = _users.store_path()
    if users_file.exists():
        users_file.unlink()
    server._UI_PASSWORD = None
    _users.save({ALICE: _users.hash_password("orchid-lantern-4471"),
                 BOB: _users.hash_password("basalt-meadow-9083"),
                 STAFF: dict(_users.hash_password("granite-sparrow-2210"),
                             staff=True)})
    server.JOBS.clear()
    server._DEMO_SRC.clear()
    server._DEMO_SCAN_AT = 0.0
    if hasattr(server, "_DEMO_REFUSED"):
        server._DEMO_REFUSED.clear()
    for p in JOBS_DIR.glob("*.pdf"):
        p.unlink()
    return {u: server._sign_session(u) for u in (ALICE, BOB, STAFF)}


def alice_uploads(ck, salt=b"alice") -> tuple:
    st, r = upload(NAME, pdf_bytes(salt), ck[ALICE])
    assert st == 200, (st, r)
    jid = r["job_id"]
    path = Path(server.JOBS[jid]["path"])
    assert server.JOBS[jid]["owner"] == ALICE
    return jid, path


# ================================================================== tests
def test_1_poisoned_cache_hit_is_refused():
    ck = setup()
    a_jid, a_path = alice_uploads(ck)
    poison(a_path)
    assert server._DEMO_SRC[NAME] == str(a_path)

    st, r = demo_open(ck[BOB])
    # THE ASSERTION THE HOLE FAILS: on b528e74 the cache hit is served,
    # the open answers 200 and a SHARED row is minted over alice's PDF.
    assert st == 410, \
        "bob opened alice's upload off the demo cache (status %s, body %r)" \
        % (st, r)
    assert rows_for(a_path) == {a_jid: ALICE}, \
        "a SHARED row was minted over alice's upload: %r" % rows_for(a_path)
    assert shared_rows() == {}, shared_rows()
    assert NAME not in server._DEMO_SRC, server._DEMO_SRC
    assert server._demo_source(NAME) is None

    # the card and the thumbnail agree, and poisoning again changes nothing
    poison(a_path)
    card = demo_card(ck[BOB])
    assert card["available"] is False and card["pages"] is None, card
    poison(a_path)
    st, _ = call("GET", "/api/demo/%s/thumb" % NAME, None, ck[BOB])
    assert st == 404, st
    assert can_open(a_jid, ck[BOB]) == 404
    assert can_open(a_jid, ck[ALICE]) == 200


def test_2_a_staff_seed_stays_cached_and_served():
    """The legitimate cache hit is untouched: a staff upload under JOBS_DIR
    is a shared row, and it must keep being served OFF THE CACHE without
    being logged as a collision."""
    ck = setup()
    a_jid, a_path = alice_uploads(ck)
    st, r = upload(NAME, pdf_bytes(b"our corpus copy"), ck[STAFF])
    assert st == 200, (st, r)
    s_jid = r["job_id"]
    s_path = Path(server.JOBS[s_jid]["path"])
    assert server.JOBS[s_jid]["owner"] is None

    st, r = demo_open(ck[BOB])
    assert st == 200 and r["job_id"] == s_jid, (st, r)
    assert server._DEMO_SRC.get(NAME) == str(s_path), server._DEMO_SRC
    # second open: a cache hit, still served, still cached
    st, r = demo_open(ck[BOB])
    assert st == 200 and r["job_id"] == s_jid, (st, r)
    assert server._DEMO_SRC.get(NAME) == str(s_path), server._DEMO_SRC
    assert not getattr(server, "_DEMO_REFUSED", set()), \
        "our own seed was logged as a client collision"
    assert rows_for(a_path) == {a_jid: ALICE}
    assert can_open(a_jid, ck[BOB]) == 404

    # the identical-bytes case: staff seed on alice's own path is still ours
    setup()
    ck = setup()
    a_jid, a_path = alice_uploads(ck, salt=b"identical")
    st, r = upload(NAME, pdf_bytes(b"identical"), ck[STAFF])
    s_jid = r["job_id"]
    assert Path(server.JOBS[s_jid]["path"]) == a_path
    for _ in range(2):
        st, r = demo_open(ck[BOB])
        assert st == 200 and r["job_id"] == s_jid, (st, r)
    assert server._DEMO_SRC.get(NAME) == str(a_path)
    assert rows_for(a_path) == {a_jid: ALICE, s_jid: None}


def test_3_corpus_hit_is_dropped_the_moment_a_client_owns_it():
    """The laptop case keeps working off the cache, and stops the moment
    the cached file becomes registered to a client."""
    ck = setup()
    corpus = Path(tempfile.mkdtemp(prefix="demo_cache_corpus_")).resolve()
    src = corpus / NAME
    src.write_bytes(pdf_bytes(b"corpus"))
    saved_roots = list(server.PLAN_ROOTS)
    server.PLAN_ROOTS[:] = [JOBS_DIR.resolve(), corpus]
    try:
        st, r = demo_open(ck[BOB])
        assert st == 200, (st, r)
        jid = r["job_id"]
        assert server._DEMO_SRC.get(NAME) == str(src)
        st, r = demo_open(ck[BOB])          # cache hit
        assert st == 200 and r["job_id"] == jid, (st, r)

        # now alice owns that file (and the shared row is gone)
        server.JOBS.clear()
        server.JOBS["a1a1a1a1a1a1"] = {
            "path": str(src), "pages": 1,
            "mtime": round(src.stat().st_mtime, 2), "kinds": None,
            "sizes": [[612.0, 792.0]], "owner": ALICE, "status": {}}
        assert server._DEMO_SRC.get(NAME) == str(src)   # still cached
        server._DEMO_SCAN_AT = time.time()
        st, r = demo_open(ck[BOB])
        assert st == 410, "cache served a file now registered to alice: %s %r" % (st, r)
        assert rows_for(src) == {"a1a1a1a1a1a1": ALICE}, rows_for(src)
        assert NAME not in server._DEMO_SRC
    finally:
        server.PLAN_ROOTS[:] = saved_roots


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} tests passed")
