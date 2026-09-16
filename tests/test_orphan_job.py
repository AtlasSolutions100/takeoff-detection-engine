"""An ORPHAN - a job directory still on disk after its registry row is gone -
must answer 404 "unknown job" on EVERY /api/job/{job_id}/... route, for
every caller, and its files must not move.

HOW AN ORPHAN HAPPENS. delete_job unlinks each file inside try/except and
then drops the registry row regardless. On Windows an unlink that hits an
open file fails silently, so the row goes and the directory stays: one
client's detection, pricing sidecar and approved deliverable, with no owner.

WHY IT WAS OPEN. Ownership lives in one middleware function, _forbidden_job,
and it returned None ("let the route answer its own 404") when the registry
had no row. 24 of the 27 job routes take no Request and go straight to
_job_dir(job_id), which returns (or creates) the directory - they cannot
answer anything. Reproduced 2026-08-31: account B POSTed calcstate and
approve on A's orphan and got two 200s. A sibling branch patched nine READ
routes one by one; the writes were missed. That is why this file does not
test routes one by one: it ENUMERATES them from the live app, so a route
added tomorrow is swept the day it lands, and pins the list so the addition
is visible in review.

CHECKED TO FAIL ON THE PRE-FIX COMMIT (ed50853): 1_gate (row missing ->
None), 2_sweep (calcstate GET 200, result 200, calcstate POST 200 and the
sidecar rewritten, approve 200, reset 200 and the directory emptied ...),
3_belt (approve/_calcstate_put/earthwork/map/zone/structure all proceed).

fastapi.testclient is unusable in this venv (it wants httpx2, see
tests/test_calcstate.py), so requests are pushed through the ASGI interface
directly - which is exactly what exercises the middleware, unlike calling
the route functions.

Dependency-free:  python tests/test_orphan_job.py
"""

import asyncio
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Both stores go somewhere disposable BEFORE server is imported (server.py
# resolves JOBS_DIR at import; the ledger would otherwise land in out/).
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="orphan_test_")
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="orphan_test_lg_")
# and the auth mode must be the one this process controls, not the shell's
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("TAKEOFF_HOSTED", None)

from fastapi import HTTPException  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

JOBS_DIR = server.JOBS_DIR
ORPHAN = "0f0f0f0f0f0f"          # shaped like a real uuid4().hex[:12]
REAL = "1a1a1a1a1a1a"            # a job that DOES have a row (owner alice)

RESULT = {"page": {"width_pt": 100.0, "height_pt": 100.0},
          "engine_sig": "cafe", "scale": None,
          "styles": [{"style_id": 2, "suggested_label": "dashed x",
                      "class": "dash1", "signature": "d4 g2",
                      "color": [0, 0, 0], "width": 1.4, "layer": "C-TOPO",
                      "length_pt": 500.0, "n": 3, "texture": False}],
          "chains": [{"chain_id": 5, "style_id": 2, "class": "dash1",
                      "color": [0, 0, 0], "width": 1.4, "layer": "C-TOPO",
                      "length_pt": 100.0, "ink_pt": 60.0, "gap_pt": 0.0,
                      "pickup_pt": 0.0, "n_dashes": 9,
                      "dash_signature": "d4 g2",
                      "points": [[0.0, 0.0], [10.0, 10.0]]}]}


def make_dir(jid: str) -> Path:
    """A job directory as a real session leaves it: detected, priced,
    approved. The marker strings are what an overwrite would destroy."""
    d = JOBS_DIR / jid
    d.mkdir(parents=True, exist_ok=True)
    (d / "p0_result.json").write_text(json.dumps(RESULT))
    (d / "p0_calcstate.json").write_text(json.dumps(
        {"v": 1, "marker": "ALICE-PRICING", "client": "Alice Client Co"}))
    (d / "p0_elements.json").write_text(json.dumps(
        {"elements": [], "marker": "ALICE-APPROVED"}))
    return d


def snapshot(d: Path) -> dict:
    return {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(d.iterdir()) if f.is_file()} if d.is_dir() else {}


# ------------------------------------------------------------ enumeration
def job_routes() -> list:
    """Every (method, path) the live app serves under /api/job/{job_id}."""
    out = []
    for r in server.app.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/api/job/{job_id}"):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                out.append((m, r.path))
    # destructive ones last, so a failing sweep shows the reads before the
    # directory is emptied
    return sorted(out, key=lambda mp: (mp[0] == "DELETE",
                                       mp[1].endswith("/reset"), mp))


J = "/api/job/{job_id}"
PINNED = sorted([
    ("GET", J + "/open"),
    ("GET", J + "/page/{page}/image"),
    ("POST", J + "/page/{page}/detect"),
    ("GET", J + "/page/{page}/status"),
    ("GET", J + "/kinds"),
    ("GET", J + "/triage"),
    ("GET", J + "/page/{page}/result"),
    ("POST", J + "/reset"),
    ("DELETE", J),
    ("GET", J + "/page/{page}/confidence"),
    ("POST", J + "/page/{page}/approve"),
    ("POST", J + "/legend-sheet"),
    # the drop route added 2026-09-02: same scan, a multipart file instead of
    # a filesystem path, because a path is a thing only the owner on his own
    # laptop can supply (and HOSTED refuses one outright)
    ("POST", J + "/legend-sheet-upload"),
    ("GET", J + "/page/{page}/scale"),
    ("GET", J + "/page/{page}/scale/dimensions"),
    ("GET", J + "/page/{page}/calcstate"),
    ("POST", J + "/page/{page}/calcstate"),
    ("GET", J + "/page/{page}/elements"),
    ("GET", J + "/report.pdf"),
    ("POST", J + "/page/{page}/analyze"),
    ("GET", J + "/page/{page}/earthwork/progress"),
    ("GET", J + "/page/{page}/earthwork/suggest"),
    ("POST", J + "/page/{page}/earthwork"),
    ("POST", J + "/page/{page}/earthwork/map"),
    ("GET", J + "/page/{page}/earthwork/map"),
    ("POST", J + "/page/{page}/earthwork/zone"),
    ("GET", J + "/page/{page}/callouts"),
    ("GET", J + "/page/{page}/footings"),
    ("GET", J + "/page/{page}/symbols/suggest"),
    ("GET", J + "/marks-rollup"),
    ("POST", J + "/page/{page}/structure"),
])

# Valid bodies, so a pre-fix failure shows the real exposure (200) and not a
# validation error hiding it. Anything not listed posts {}.
BODIES = {
    J + "/page/{page}/approve": {"labels": {}, "image_dpi": 300,
                                 "notes": ["BOB-WAS-HERE"]},
    J + "/page/{page}/calcstate": {"v": 1, "marker": "BOB-OVERWROTE",
                                   "client": "Bob Rival LLC"},
    J + "/page/{page}/earthwork/zone": {"polygon": [[0, 0], [50, 0],
                                                    [50, 50], [0, 50]]},
    J + "/page/{page}/structure": {"structures": []},
}


# ------------------------------------------------------------ ASGI driver
async def _asgi(method, path, body: bytes, cookie):
    headers = [(b"host", b"testserver"),
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
    return asyncio.run(_asgi(method, path, raw, cookie))


def sweep(jid: str, cookie, who: str) -> list:
    """Hit every job route as `who`; return the (method, path, status,
    detail) rows that did NOT answer 404 'unknown job'."""
    bad = []
    for m, tpl in job_routes():
        path = tpl.replace("{job_id}", jid).replace("{page}", "0")
        body = BODIES.get(tpl, {}) if m in ("POST", "PUT") else None
        st, raw = call(m, path, body, cookie)
        try:
            detail = json.loads(raw).get("detail")
        except (ValueError, AttributeError):
            detail = raw[:60]
        if not (st == 404 and detail == "unknown job"):
            bad.append((who, m, path, st, detail))
    return bad


def _err(fn, *a):
    try:
        fn(*a)
        return None
    except HTTPException as e:
        return e


# ================================================================== tests
def test_0_every_job_route_is_enumerated():
    live = sorted(job_routes())
    assert live == PINNED, (
        "the job-route list moved - a route was added or removed. Add it "
        "to PINNED (it IS swept below already; this pin is so the addition "
        "is seen in review):\n  +" + "\n  +".join(
            f"{m} {p}" for m, p in sorted(set(live) - set(PINNED)))
        + "\n  -" + "\n  -".join(
            f"{m} {p}" for m, p in sorted(set(PINNED) - set(live))))
    assert len(live) == 31, len(live)   # +scale/dimensions, +earthwork/progress, +footings


def test_1_gate_fails_closed_on_a_missing_row():
    server.JOBS.clear()
    server.JOBS[REAL] = {"path": "x.pdf", "pages": 1, "owner": "alice",
                         "status": {}}
    fj = server._forbidden_job
    for who, staff in ((None, True), ("alice", False), ("bob", False),
                       ("staff1", True)):
        r = fj(f"/api/job/{ORPHAN}/page/0/calcstate", who, staff)
        assert r is not None and r.status_code == 404, (who, staff, r)
        r = fj(f"/api/job/{ORPHAN}", who, staff)
        assert r is not None and r.status_code == 404, (who, staff, r)
    # an id that could never have a row is refused too, not passed through
    r = fj("/api/job/../kinds", "bob", False)
    assert r is not None and r.status_code == 404, r
    # unchanged: a real row keeps its ownership answers
    assert fj(f"/api/job/{REAL}/kinds", "alice", False) is None
    assert fj(f"/api/job/{REAL}/kinds", "staff1", True) is None
    assert fj(f"/api/job/{REAL}/kinds", None, True) is None
    r = fj(f"/api/job/{REAL}/kinds", "bob", False)
    assert r is not None and r.status_code == 404, r
    # and non-job paths are none of its business
    assert fj("/api/recent", "bob", False) is None
    assert fj("/api/jobs/anything", "bob", False) is None


def test_2_sweep_every_route_in_every_auth_mode():
    server.JOBS.clear()
    d = make_dir(ORPHAN)
    make_dir(REAL)
    server.JOBS[REAL] = {"path": "x.pdf", "pages": 1, "owner": "alice",
                         "status": {}, "kinds": ["site"], "sizes": [[1, 1]]}
    before = snapshot(d)
    assert len(before) == 3, before
    bad = []

    # (a) laptop mode: no accounts, no site password, no cookie
    users_file = _users.store_path()
    if users_file.exists():
        users_file.unlink()
    server._UI_PASSWORD = None
    assert not _users.load()
    bad += sweep(ORPHAN, None, "laptop")

    # (b) per-user mode: the former owner, a stranger, and staff
    _users.save({"alice": _users.hash_password("orchid-lantern-4471"),
                 "bob": _users.hash_password("basalt-meadow-9083"),
                 "staff1": dict(_users.hash_password("granite-sparrow-2210"),
                                staff=True)})
    assert set(_users.load()) == {"alice", "bob", "staff1"}
    ck = {u: server._sign_session(u) for u in ("alice", "bob", "staff1")}
    for u in ("alice", "bob", "staff1"):
        bad += sweep(ORPHAN, ck[u], u)
    # the REAL job keeps working for the people who may see it - this is the
    # staff read-through, which is a feature, and the owner's own access
    for u, want in (("alice", 200), ("staff1", 200), ("bob", 404)):
        st, _ = call("GET", f"/api/job/{REAL}/kinds", None, ck[u])
        assert st == want, (u, st)

    # (c) single-password mode: accounts absent, site password set,
    #     the cookie carries no username
    users_file.unlink()
    assert not _users.load()
    server._UI_PASSWORD = "site-pw-for-test"
    try:
        bad += sweep(ORPHAN, server._sign_session(None), "single-password")
        st, _ = call("GET", f"/api/job/{REAL}/kinds", None,
                     server._sign_session(None))
        assert st == 200, st
    finally:
        server._UI_PASSWORD = None

    assert not bad, "routes that did not answer 404 'unknown job':\n" + \
        "\n".join(f"  {w:16} {m:6} {p:48} {s} {dt!r}" for w, m, p, s, dt in bad)
    after = snapshot(d)
    assert after == before, {k: (before.get(k), after.get(k))
                             for k in set(before) | set(after)
                             if before.get(k) != after.get(k)}


def test_3_write_routes_refuse_directly_too():
    """Tests and internal callers bypass the middleware: the write routes
    carry their own row check."""
    server.JOBS.clear()
    d = make_dir(ORPHAN)
    before = snapshot(d)
    poly = server.GradingZoneBody(polygon=[[0, 0], [50, 0], [50, 50], [0, 50]])
    calls = [
        ("approve", server.approve, ORPHAN, 0,
         server.ApproveBody(labels={}, notes=["BOB"])),
        ("calcstate", server._calcstate_put, ORPHAN, 0, b'{"marker":"BOB"}'),
        ("earthwork", server.earthwork_calc, ORPHAN, 0, server.EarthworkBody()),
        ("earthwork/map", server.earthwork_map, ORPHAN, 0,
         server.EarthworkBody()),
        ("earthwork/zone", server.earthwork_zone, ORPHAN, 0, poly),
        ("structure", server.structure_calc, ORPHAN, 0,
         server.StructureBody()),
    ]
    for name, fn, *args in calls:
        e = _err(fn, *args)
        assert e is not None and e.status_code == 404 \
            and e.detail == "unknown job", (name, e)
    assert snapshot(d) == before
    # and with the row present the same calls get PAST the row check
    # (whatever they answer next is about the body, not about the job)
    server.JOBS[ORPHAN] = {"path": "x.pdf", "pages": 1, "owner": "alice",
                           "status": {}}
    e = _err(server._calcstate_put, ORPHAN, 0, b'{"marker":"ALICE-2"}')
    assert e is None, e
    assert json.loads((d / "p0_calcstate.json").read_text())["marker"] == "ALICE-2"
    server.JOBS.clear()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} tests passed")
