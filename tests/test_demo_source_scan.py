"""A client's upload must NEVER become the demo, however it is named.

THE LEAK (760c198 and earlier). _demo_source(name) first looks for a SHARED
registry row (owner None) whose display name is the manifest name - correct,
that is how a seeded hosted box has its demo set. When there is none it
falls through to rglob("*.pdf") over PLAN_ROOTS and accepts ANY file whose
_display_name equals the name, caching it in _DEMO_SRC. _display_name strips
our own "upload_<hash>_" prefix, and the default PLAN_ROOTS ends with
JOBS_DIR - which on a box without the corpus beside the repo (the hosted box)
is the only root that exists. So on an UNSEEDED hosted box, a client upload
named exactly like a manifest plan is found by the scan, and _demo_job then
does _register(src, owner=None): a SHARED row over the client's private
file. Demonstrated live on 760c198: alice uploads "Belle Farms Structural
Plans.pdf"; bob POSTs /api/demo/<that name>/open -> 200, then opens and
renders alice's page, and it appears on bob's /api/recent. Production is
unseeded today, so the preconditions hold there.

THE FIX is inside _demo_source's scan, two belts (_demo_scan_refused):
  (a) a candidate under JOBS_DIR is never accepted - the only way into
      JOBS_DIR on a hosted box is an upload, and a staff seed there is a
      shared row the row loop above the scan already finds;
  (b) a candidate whose resolved path is registered to a row with an owner
      is never accepted, under any root.
A refused path is never cached in _DEMO_SRC. Upload and _register are
untouched: the legitimate seeding path (staff uploads a manifest name, the
upload route makes the row shared) is pinned here too, and so is the laptop
case (a corpus copy under a separate plan root is still found).

Environment is the DEGRADED default on purpose: TAKEOFF_HOSTED=1 and
TAKEOFF_PLAN_ROOTS pointed at the throwaway JOBS_DIR, which is exactly what
the hosted box's default PLAN_ROOTS collapses to when the corpus is absent.

Same ASGI driver as tests/test_reopen_owner.py, so the demo open, the job
routes and /api/recent all go through the real session middleware; the
upload goes through the real multipart route.

Dependency-free:  python tests/test_demo_source_scan.py
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_JOBS = tempfile.mkdtemp(prefix="demo_scan_test_")
os.environ["TAKEOFF_JOBS_DIR"] = _JOBS
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="demo_scan_test_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = _JOBS          # the degraded default
os.environ.pop("TAKEOFF_UI_PASSWORD", None)

import pymupdf  # noqa: E402

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

JOBS_DIR = server.JOBS_DIR
ALICE, BOB, STAFF = "alice_synth", "bob_synth", "staff_synth"

assert server.HOSTED, "this test is about the hosted box"
assert server.PLAN_ROOTS == [JOBS_DIR.resolve()], server.PLAN_ROOTS
assert server.DEMO_PLANS, "the manifest did not load"
# the name the adversary used, if the manifest still carries it
NAME = ("Belle Farms Structural Plans.pdf"
        if "Belle Farms Structural Plans.pdf" in server.DEMO_PLANS
        else sorted(server.DEMO_PLANS)[0])

# classification would spawn a thread that reads the PDF after the test has
# moved on; which file the demo resolves to does not depend on it
server._classify_async = lambda *a, **k: None


_BASE_PDF = None


def pdf_bytes(salt: bytes) -> bytes:
    """A one-page PDF whose bytes are a pure function of the salt - the same
    salt twice is the same upload (content-addressed path), two salts are
    two paths. The base document is built once because pymupdf stamps
    every new document differently."""
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


def reset_demo_cache():
    server._DEMO_SRC.clear()
    server._DEMO_SCAN_AT = 0.0             # make the next lookup SCAN
    if hasattr(server, "_DEMO_REFUSED"):
        server._DEMO_REFUSED.clear()


# ------------------------------------------------------------ ASGI driver
async def _asgi(method, path, body: bytes, cookie, ctype, query=b""):
    headers = [(b"host", b"testserver"), (b"x-core-client", b"2"),
               (b"content-type", ctype.encode()),
               (b"content-length", str(len(body)).encode())]
    if cookie is not None:
        headers.append((b"cookie", f"{server.SESSION_COOKIE}={cookie}".encode()))
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": method, "scheme": "http",
             # ASGI: `path` is decoded, `raw_path` is what was on the wire
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
    """POST /api/upload through the real multipart route."""
    bnd = "----takeoffdemoscan"
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


def recent_ids(cookie) -> set:
    st, recent = call("GET", "/api/recent", None, cookie)
    assert st == 200, (st, recent)
    return {r["job_id"] for r in recent}


def can_reach(jid: str, cookie) -> dict:
    st_open, _ = call("GET", f"/api/job/{jid}/open", None, cookie)
    st_img, _ = call("GET", f"/api/job/{jid}/page/0/image", None, cookie)
    return {"open": st_open, "recent": jid in recent_ids(cookie),
            "image": st_img}


CAN = {"open": 200, "recent": True, "image": 200}
CANNOT = {"open": 404, "recent": False, "image": 404}


def setup() -> dict:
    users_file = _users.store_path()
    if users_file.exists():
        users_file.unlink()
    server._UI_PASSWORD = None
    _users.save({ALICE: _users.hash_password("orchid-lantern-4471"),
                 BOB: _users.hash_password("basalt-meadow-9083"),
                 STAFF: dict(_users.hash_password("granite-sparrow-2210"),
                             staff=True)})
    assert set(_users.load()) == {ALICE, BOB, STAFF}
    server.JOBS.clear()
    reset_demo_cache()
    for p in JOBS_DIR.glob("*.pdf"):
        p.unlink()
    return {u: server._sign_session(u) for u in (ALICE, BOB, STAFF)}


def alice_uploads(ck, salt=b"alice") -> tuple:
    st, r = upload(NAME, pdf_bytes(salt), ck[ALICE])
    assert st == 200, (st, r)
    jid = r["job_id"]
    path = Path(server.JOBS[jid]["path"])
    assert server.JOBS[jid]["owner"] == ALICE, server.JOBS[jid]
    assert path.parent.resolve() == JOBS_DIR.resolve(), path
    assert server._display_name(path) == NAME, path
    return jid, path


# ================================================================== tests
def test_1_client_upload_never_becomes_the_demo():
    """The adversary's script, on the degraded hosted default."""
    ck = setup()
    a_jid, a_path = alice_uploads(ck)
    assert shared_rows() == {}, "precondition: the box is unseeded"

    reset_demo_cache()
    st, r = demo_open(ck[BOB])
    # THE ASSERTION THE LEAK FAILS: the demo open must say the plan is not on
    # this box. On 760c198 it answers 200 with a job over alice's upload.
    assert st == 410 and "not on this server" in str(r.get("detail", "")), \
        "bob opened alice's upload through the demo card " \
        "(status %s, body %r) - a client upload named like a demo plan " \
        "must not be served as the demo" % (st, r)
    assert rows_for(a_path) == {a_jid: ALICE}, \
        "a SHARED row was minted over alice's upload: %r" % rows_for(a_path)
    assert shared_rows() == {}, shared_rows()
    assert NAME not in server._DEMO_SRC, \
        "alice's path was cached as the demo source: %r" % server._DEMO_SRC
    assert server._demo_source(NAME) is None

    # what the card path sees: unavailable, and no thumbnail
    card = demo_card(ck[BOB])
    assert card["available"] is False and card["pages"] is None, card
    st, r = call("GET", "/api/demo/%s/thumb" % NAME, None, ck[BOB])
    assert st == 404, (st, r)

    # and the consequences: bob cannot reach alice's job by any route, alice
    # and staff still can
    assert can_reach(a_jid, ck[BOB]) == CANNOT, can_reach(a_jid, ck[BOB])
    assert can_reach(a_jid, ck[ALICE]) == CAN, can_reach(a_jid, ck[ALICE])
    assert can_reach(a_jid, ck[STAFF]) == CAN, can_reach(a_jid, ck[STAFF])

    # a second scan (the landing page repainting after the 30 s window) does
    # not change its mind
    reset_demo_cache()
    st, r = demo_open(ck[BOB])
    assert st == 410, (st, r)
    assert rows_for(a_path) == {a_jid: ALICE}


def test_2_staff_seed_publishes_our_copy_and_leaves_alices_alone():
    """The legitimate case: staff upload the same-named plan (our copy,
    different bytes). The upload route makes THAT row shared; the demo
    resolves to it, and alice's row is untouched and still private."""
    ck = setup()
    a_jid, a_path = alice_uploads(ck)
    st, r = upload(NAME, pdf_bytes(b"our corpus copy"), ck[STAFF])
    assert st == 200, (st, r)
    s_jid = r["job_id"]
    s_path = Path(server.JOBS[s_jid]["path"])
    assert server.JOBS[s_jid]["owner"] is None, server.JOBS[s_jid]
    assert s_path != a_path

    reset_demo_cache()
    st, r = demo_open(ck[BOB])
    assert st == 200, (st, r)
    assert r["job_id"] == s_jid, "the demo must open the STAFF row: %r" % r
    assert server._demo_source(NAME) == s_path
    assert rows_for(a_path) == {a_jid: ALICE}, rows_for(a_path)
    assert rows_for(s_path) == {s_jid: None}, rows_for(s_path)
    assert can_reach(s_jid, ck[BOB]) == CAN, can_reach(s_jid, ck[BOB])
    assert can_reach(a_jid, ck[BOB]) == CANNOT, can_reach(a_jid, ck[BOB])
    assert can_reach(a_jid, ck[ALICE]) == CAN


def test_3_staff_seed_of_the_same_bytes_is_a_second_row_not_alices():
    """Uploads are content-addressed: staff uploading the SAME bytes alice
    did lands on alice's path. The upload route mints a separate shared
    row for it (owner is part of _register's reuse key); alice's row keeps
    its owner and the demo opens the shared one."""
    ck = setup()
    a_jid, a_path = alice_uploads(ck, salt=b"identical")
    st, r = upload(NAME, pdf_bytes(b"identical"), ck[STAFF])
    assert st == 200, (st, r)
    s_jid = r["job_id"]
    assert s_jid != a_jid
    assert Path(server.JOBS[s_jid]["path"]) == a_path
    assert rows_for(a_path) == {a_jid: ALICE, s_jid: None}, rows_for(a_path)

    reset_demo_cache()
    st, r = demo_open(ck[BOB])
    assert st == 200 and r["job_id"] == s_jid, (st, r)
    assert rows_for(a_path) == {a_jid: ALICE, s_jid: None}, rows_for(a_path)
    assert can_reach(a_jid, ck[BOB]) == CANNOT, can_reach(a_jid, ck[BOB])


def test_4_owned_copy_under_another_root_is_refused_then_laptop_case():
    """Belt (b) on its own, then the laptop case.

    A corpus-named PDF under a SEPARATE plan root (not JOBS_DIR) that is
    registered to alice - spelled differently in the registry (a ../
    segment), so the comparison has to be on resolved paths - is refused.
    Once no owned row points at it, the same file IS the demo: this is how
    a laptop out of the TakeoffPro AI corpus works and must keep working."""
    ck = setup()
    corpus = Path(tempfile.mkdtemp(prefix="demo_scan_corpus_")).resolve()
    (corpus / "sub").mkdir()
    src = corpus / NAME
    src.write_bytes(pdf_bytes(b"corpus"))
    saved_roots = list(server.PLAN_ROOTS)
    server.PLAN_ROOTS[:] = [JOBS_DIR.resolve(), corpus]
    try:
        # alice's row over the corpus file, spelled through sub/..
        spelled = corpus / "sub" / ".." / NAME
        assert str(spelled) != str(src) and spelled.resolve() == src
        server.JOBS["a1a1a1a1a1a1"] = {
            "path": str(spelled), "pages": 1,
            "mtime": round(src.stat().st_mtime, 2), "kinds": None,
            "sizes": [[612.0, 792.0]], "owner": ALICE, "status": {}}
        reset_demo_cache()
        st, r = demo_open(ck[BOB])
        assert st == 410, "bob opened alice's registered copy: %s %r" % (st, r)
        assert rows_for(src) == {"a1a1a1a1a1a1": ALICE}, rows_for(src)
        assert NAME not in server._DEMO_SRC, server._DEMO_SRC
        assert can_reach("a1a1a1a1a1a1", ck[BOB]) == CANNOT

        # the laptop case: nobody owns it, it is the corpus, it is the demo
        server.JOBS.clear()
        reset_demo_cache()
        st, r = demo_open(ck[BOB])
        assert st == 200, (st, r)
        jid = r["job_id"]
        assert Path(server.JOBS[jid]["path"]).resolve() == src
        assert server.JOBS[jid]["owner"] is None
        assert server._demo_source(NAME).resolve() == src
        assert can_reach(jid, ck[BOB]) == CAN, can_reach(jid, ck[BOB])
        assert can_reach(jid, ck[ALICE]) == CAN
    finally:
        server.PLAN_ROOTS[:] = saved_roots


def test_5_a_loose_pdf_in_jobs_dir_is_never_the_demo():
    """Belt (a) on its own, at the resolver: a corpus-named PDF loose in
    JOBS_DIR with NO registry row at all (an upload whose registry was
    lost, or a file somebody copied in) is not the demo either. Getting
    it published takes a staff upload, which makes a shared row."""
    setup()
    loose = JOBS_DIR / NAME
    loose.write_bytes(pdf_bytes(b"loose"))
    reset_demo_cache()
    assert server._demo_source(NAME) is None, server._DEMO_SRC
    assert NAME not in server._DEMO_SRC
    # and the same file behind our own upload prefix, still unregistered
    loose.unlink()
    loose = JOBS_DIR / ("upload_0123456789ab_" + NAME)
    loose.write_bytes(pdf_bytes(b"loose2"))
    reset_demo_cache()
    assert server._demo_source(NAME) is None, server._DEMO_SRC
    loose.unlink()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} tests passed")
