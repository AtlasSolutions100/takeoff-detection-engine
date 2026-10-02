"""THE TEAM MODEL (2026-09-25): roles, last-active, the audit log, and the
two activity feeds - pushed through the ASGI interface so the session
middleware is in the loop (the same driver as test_users_api.py;
fastapi.testclient is unusable in this venv).

Pinned:
  * back-compat: a record with staff and no role is an admin, one with
    neither is an estimator, a stated role wins over the staff flag, and
    every write keeps `staff` in step with the role; a password reset keeps
    the role (a viewer is never escalated by one);
  * a signed-in VIEWER gets 403 {"detail": "Read-only: ..."} on every
    non-GET under /api/ and /cutfill/api/ - upload, detect, approve,
    calcstate, reset, delete, demo open, cut / fill open and compute - and
    nothing moves on disk; logout, change-password and feedback still work;
    reads answer 200; the two GETs that change something (reopening an
    unsized row re-registers it; /api/bids/{id}/plans makes Bid Sniper
    download) are refused to a viewer too;
  * admin-only: /api/users, /api/users/{u}/role and /api/team/activity
    answer 404 to anyone else, 409 on a single-password box;
  * the last admin can be neither removed nor demoted, by anyone, self
    included;
  * /api/users rows carry role, last_active, last_login and projects;
    last-active is its own file, throttled, and never rewrites users.json;
    a failed activity write never fails the request;
  * audit lines for user_add / user_role / user_reset_password / login /
    sheet_detect / sheet_approve / project_delete, with the actor in `user`,
    the target in `name`, and no password anywhere in the log;
  * GET /api/job/{id}/activity: another estimator's job is refused like any
    job route (404 unknown job), an admin sees who, the owner does not get a
    `user` field at all; ledger events merge in under their action names and
    an approve is listed once, not twice.

Dependency-free:  python tests/test_team_roles.py
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Every store goes somewhere disposable BEFORE server is imported (server.py
# resolves JOBS_DIR at import; the ledger follows TAKEOFF_LEDGER_DIR).
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="team_roles_")
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="team_roles_lg_")
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("TAKEOFF_HOSTED", None)

import pymupdf  # noqa: E402

import server  # noqa: E402
from takeoff_engine import audit as _audit  # noqa: E402
from takeoff_engine import ledger as _ledger  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

JOBS_DIR = server.JOBS_DIR
READ_ONLY = "Read-only: your role is Viewer. Ask an admin for Estimator access."
server._classify_async = lambda *a, **k: None       # no background classify
_QUEUED = []
server._enqueue_detect = lambda j, p: _QUEUED.append((j, p)) or False

fails = 0


def check(ok, msg):
    global fails
    if ok:
        print("ok:", msg)
    else:
        fails += 1
        print("FAIL:", msg)


# ------------------------------------------------------------ ASGI driver
def call(method, path, body=None, cookie=None, raw=None, ctype=None, marker=True):
    """body -> JSON; raw + ctype -> anything else (multipart). marker=False
    leaves out the page's X-Core-Client header (server._client_gate)."""
    path, _, query = path.partition("?")
    headers = [(b"host", b"testserver")]
    if marker:
        headers.append((b"x-core-client", b"2"))
    data = b""
    if raw is not None:
        data = raw
        headers.append((b"content-type", ctype.encode()))
    elif body is not None:
        data = json.dumps(body).encode()
        headers.append((b"content-type", b"application/json"))
    headers.append((b"content-length", str(len(data)).encode()))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "http",
             "path": urllib.parse.unquote(path), "raw_path": path.encode(),
             "query_string": query.encode(), "root_path": "",
             "headers": headers, "client": ("127.0.0.1", 1234),
             "server": ("testserver", 80)}
    out = {"status": None, "headers": [], "body": b""}
    sent = {"body": False}

    async def receive():
        if not sent["body"]:
            sent["body"] = True
            return {"type": "http.request", "body": data, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = msg.get("headers", [])
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    asyncio.run(server.app(scope, receive, send))
    try:
        js = json.loads(out["body"].decode() or "null")
    except (ValueError, UnicodeDecodeError):
        js = None
    return out["status"], js, out["headers"]


def cookie_from(hdrs):
    ck = ""
    for k, v in hdrs:
        if k.lower() == b"set-cookie" and v.startswith(b"tk_session="):
            ck = v.decode().split(";")[0]
    return ck


def login(name, pw):
    st, js, hdrs = call("POST", "/api/login", {"username": name, "password": pw})
    if st != 200:
        return st, None
    return st, cookie_from(hdrs)


def pdf_bytes(salt: bytes) -> bytes:
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    raw = doc.tobytes()
    doc.close()
    return raw + b"\n%" + salt + b"\n"


def upload(filename, data, cookie):
    bnd = "----teamrolesboundary"
    raw = (("--%s\r\nContent-Disposition: form-data; name=\"file\"; "
            "filename=\"%s\"\r\nContent-Type: application/pdf\r\n\r\n"
            % (bnd, filename)).encode() + data + ("\r\n--%s--\r\n" % bnd).encode())
    return call("POST", "/api/upload?classify=0", raw=raw,
                ctype="multipart/form-data; boundary=" + bnd, cookie=cookie)


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


def audit_lines() -> list:
    out = []
    for f in sorted(_audit.audit_dir().glob("audit-*.jsonl")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                out.append(json.loads(ln))
    return out


def audit_bytes() -> str:
    return "".join(f.read_text(encoding="utf-8")
                   for f in sorted(_audit.audit_dir().glob("audit-*.jsonl")))


# ================================================ 1. roles in users.json
_users.save({
    "legacy-staff@t": dict(_users.hash_password("x" * 10), staff=True),
    "legacy-client@t": _users.hash_password("x" * 10),
    "clash@t": dict(_users.hash_password("x" * 10), staff=True, role="viewer"),
    "shout@t": dict(_users.hash_password("x" * 10), role=" Admin "),
    "odd-staff@t": dict(_users.hash_password("x" * 10), staff=True, role="superuser"),
    "odd@t": dict(_users.hash_password("x" * 10), role="superuser"),
})
check(_users.role_of("legacy-staff@t") == "admin" and _users.is_staff("legacy-staff@t"),
      "back-compat: staff and no role -> admin")
check(_users.role_of("legacy-client@t") == "estimator" and not _users.is_staff("legacy-client@t"),
      "back-compat: no role, not staff -> estimator")
check(_users.role_of("clash@t") == "viewer" and not _users.is_staff("clash@t"),
      "a stated role wins over a stale staff flag (a viewer is never staff)")
check(_users.role_of("shout@t") == "admin", "a role is read case- and space-insensitively")
check(_users.role_of("odd-staff@t") == "admin" and _users.role_of("odd@t") == "estimator",
      "an unknown role string falls back to the staff flag")
check(_users.role_of("nobody@t") is None, "no account -> no role")
rows = {r["user"]: r for r in _users.list_users()}
check(rows["legacy-staff@t"]["role"] == "admin" and rows["legacy-staff@t"]["staff"] is True
      and rows["clash@t"]["staff"] is False,
      "list_users reports the role, and staff follows it")

r = _users.add_user("v@t", "viewer-pass-1", role="viewer")
rec = _users.load()["v@t"]
check(r["role"] == "viewer" and r["staff"] is False and rec.get("role") == "viewer"
      and "staff" not in rec, "add_user(role=viewer) stores the role and no staff flag")
r = _users.add_user("s@t", "staff-pass-11", staff=True)
check(r["role"] == "admin" and _users.load()["s@t"].get("staff") is True,
      "add_user(staff=True) is an admin, staff kept in sync")
r = _users.add_user("w@t", "wins-pass-11", role="estimator", staff=True)
check(r["role"] == "estimator" and not _users.is_staff("w@t"), "role wins over staff")
r = _users.add_user("V@T", "viewer-pass-2")
check(r["existed"] and r["user"] == "v@t" and r["role"] == "viewer",
      "a password reset (add_user, no role) keeps the viewer a viewer")
r = _users.add_user("s@t", "staff-pass-22")
check(r["role"] == "admin", "and keeps an admin an admin")
_users.set_password("v@t", "viewer-pass-3")
check(_users.role_of("v@t") == "viewer", "set_password keeps the role")
_users.set_staff("v@t", False)
check(_users.role_of("v@t") == "viewer", "set_staff(False) on a viewer does not promote it")
_users.set_staff("v@t", True)
check(_users.role_of("v@t") == "admin" and _users.load()["v@t"].get("staff") is True,
      "set_staff(True) -> admin")
_users.set_staff("v@t", False)
check(_users.role_of("v@t") == "estimator" and "staff" not in _users.load()["v@t"],
      "set_staff(False) on an admin -> estimator")
check(_users.set_role("v@t", "viewer") and _users.role_of("v@t") == "viewer",
      "set_role sets it")
try:
    _users.set_role("v@t", "owner")
    check(False, "set_role refuses an unknown role")
except ValueError:
    check(True, "set_role refuses an unknown role")
check(_users.set_role("nobody@t", "viewer") is False, "set_role on no account -> False")

# ================================================ 2. the team on a live app
_users.save({})
_users.add_user("admin@t", "admin-pass-1", role="admin")
_users.add_user("est@t", "est-pass-111", role="estimator")
_users.add_user("est2@t", "est2-pass-11", role="estimator")
_users.add_user("view@t", "view-pass-11", role="viewer")
_users.add_user("quiet@t", "quiet-pass-1", role="estimator")     # never signs in
PASSWORDS = ["admin-pass-1", "est-pass-111", "est2-pass-11", "view-pass-11",
             "quiet-pass-1"]
ck = {}
for u, pw in (("admin@t", "admin-pass-1"), ("est@t", "est-pass-111"),
              ("est2@t", "est2-pass-11"), ("view@t", "view-pass-11")):
    st, ck[u] = login(u, pw)
    check(st == 200 and ck[u], f"{u} signs in")
ADMIN, EST, EST2, VIEW = ck["admin@t"], ck["est@t"], ck["est2@t"], ck["view@t"]

for c, want in ((ADMIN, "admin"), (EST, "estimator"), (VIEW, "viewer")):
    st, js, _ = call("GET", "/api/me", None, c)
    check(st == 200 and js["role"] == want and js["staff"] is (want == "admin"),
          f"/api/me says role {want}")

# a SHARED job (owner None - the demo set's marker) the viewer may read, and
# an estimator's own upload
shared_pdf = JOBS_DIR / "shared-plan.pdf"
shared_pdf.write_bytes(pdf_bytes(b"shared"))
# Since the fork's 2026-10-01 rule an owner-less row is open to every account only when its name is a demo card or
# a shared-set sheet, exactly (server._shared_for_all; tests/test_projects_api.py does the same)
server.SHARED_NAMES = frozenset(set(server.SHARED_NAMES) | {"shared-plan.pdf"})
SHARED = server._register(shared_pdf, classify=False, owner=None)["job_id"]
(server._job_dir(SHARED) / "p0_result.json").write_text(json.dumps(RESULT))
st, js, _ = upload("Estimator Bid Set.pdf", pdf_bytes(b"est"), EST)
check(st == 200 and server.JOBS[js["job_id"]]["owner"] == "est@t", "the estimator uploads")
EJOB = js["job_id"]
(server._job_dir(EJOB) / "p0_result.json").write_text(json.dumps(RESULT))

# ================================================ 3. the viewer is read-only
n_jobs = len(server.JOBS)
refused = [
    ("POST", "/api/job/%s/page/0/detect" % SHARED, {}),
    ("POST", "/api/job/%s/page/0/approve" % SHARED, {"labels": {"2": "x"}}),
    ("POST", "/api/job/%s/page/0/calcstate" % SHARED, {"v": 1, "marker": "VIEWER"}),
    ("POST", "/api/job/%s/reset" % SHARED, {}),
    ("DELETE", "/api/job/%s" % SHARED, None),
    ("POST", "/api/job/%s/cutfill" % SHARED, {"page": 0}),
    ("POST", "/api/job/%s/page/0/structure" % SHARED, {"structures": []}),
    ("POST", "/cutfill/api/projects/job_%s/compute" % SHARED, {}),
    ("PATCH", "/cutfill/api/projects/job_%s/settings" % SHARED, {"x": 1}),
    ("POST", "/cutfill/api/projects", {"name": "v"}),
    ("POST", "/api/demo/whatever.pdf/open", {}),
    ("POST", "/api/open-path", {"path": str(shared_pdf)}),
    ("POST", "/api/branding", {"company": "Viewer Co"}),
    ("POST", "/api/users", {"username": "x@t"}),
    ("POST", "/api/users/est%40t/role", {"role": "viewer"}),
]
for m, p, b in refused:
    st, js, _ = call(m, p, b, VIEW)
    check(st == 403 and (js or {}).get("detail") == READ_ONLY,
          f"viewer {m} {p.split('?')[0]} -> 403 read-only")
st, js, _ = upload("Viewer Upload.pdf", pdf_bytes(b"viewer"), VIEW)
check(st == 403 and js.get("detail") == READ_ONLY, "viewer upload -> 403 read-only")
check(len(server.JOBS) == n_jobs, "nothing the viewer sent registered a job")
check(not (JOBS_DIR / SHARED / "p0_elements.json").exists()
      and not (JOBS_DIR / SHARED / "p0_calcstate.json").exists()
      and (JOBS_DIR / SHARED / "p0_result.json").exists(),
      "no approve export, no calcstate sidecar, and the detection still there")
check(not (JOBS_DIR / "cutfill" / ("job_" + SHARED)).exists(), "no cut / fill project made")
check(not _QUEUED, "no detection was queued")

# the order of the gates (server._SessionAuth): the viewer gate answers before the client gate, so a viewer
# on a page without the X-Core-Client marker still reads "Read-only"; the team routes obey the client gate
st, js, _ = call("POST", "/api/job/%s/page/0/calcstate" % SHARED, {"v": 1}, VIEW, marker=False)
check(st == 403 and (js or {}).get("detail") == READ_ONLY,
      "viewer write without the page marker -> 403 read-only (viewer gate first)")
st, js, _ = call("POST", "/api/users/est%40t/role", {"role": "viewer"}, ADMIN, marker=False)
check(st == 409 and (js or {}).get("detail") == server.CLIENT_OLD_WORDS
      and _users.role_of("est@t") == "estimator",
      "admin role change without the page marker -> 409, the role does not move")

for p in ("/api/me", "/api/projects", "/api/recent", "/api/job/%s/open" % SHARED,
          "/api/job/%s/kinds" % SHARED, "/api/job/%s/page/0/image" % SHARED,
          "/api/job/%s/page/0/result" % SHARED, "/api/job/%s/page/0/calcstate" % SHARED,
          "/api/job/%s/page/0/scale" % SHARED, "/api/job/%s/activity" % SHARED,
          "/api/branding", "/cutfill/api/health"):
    st, js, _ = call("GET", p, None, VIEW)
    check(st == 200, f"viewer GET {p} -> 200")
st, js, _ = call("GET", "/api/projects", None, VIEW)
check([r["job_id"] for r in js["projects"]] == [SHARED],
      "a viewer sees what an estimator would: the shared set, not the estimator's upload")

# the GETs that change something
UNSIZED = "0badc0ffee00"
server.JOBS[UNSIZED] = {"path": str(shared_pdf), "pages": 1, "mtime": None,
                        "owner": None, "status": {}}
n_jobs = len(server.JOBS)
st, js, _ = call("GET", "/api/job/%s/open" % UNSIZED, None, VIEW)
check(st == 403 and js.get("detail") == READ_ONLY and len(server.JOBS) == n_jobs,
      "reopening an unsized row would re-register it: refused to a viewer, registry untouched")
st, js, _ = call("GET", "/api/job/%s/open" % UNSIZED, None, EST)
check(st == 200 and js.get("sizes"), "the same reopen by an estimator goes through")
st, js, _ = call("GET", "/api/bids/some-bid/plans", None, VIEW)
check(st == 403 and js.get("detail") == READ_ONLY,
      "GET /api/bids/{id}/plans makes Bid Sniper download: refused to a viewer")

# what a viewer may still send
st, js, _ = call("POST", "/api/feedback", {"message": "read-only is fine"}, VIEW)
check(st == 200 and js.get("ok"), "viewer POST /api/feedback -> 200")
st, js, _ = call("POST", "/api/change-password", {"password": "view-pass-22"}, VIEW)
check(st == 200 and _users.role_of("view@t") == "viewer",
      "viewer POST /api/change-password -> 200, still a viewer")
st, VIEW = login("view@t", "view-pass-22")
check(st == 200 and VIEW, "the viewer signs in with the new password")
PASSWORDS.append("view-pass-22")
st, js, hdrs = call("POST", "/api/logout", {}, VIEW)
check(st == 200 and any(k.lower() == b"set-cookie" for k, _ in hdrs),
      "viewer POST /api/logout -> 200 and the cookie is cleared")
# must_change still gates a viewer, and change-password is its way out
_users.add_user("view@t", "view-pass-33", must_change=True)
st, VIEW = login("view@t", "view-pass-33")
st, js, _ = call("GET", "/api/projects", None, VIEW)
check(st == 403 and "new password" in js["detail"], "a viewer on a temporary password is sent to change it")
OLD_VIEW = VIEW
st, js, hdrs = call("POST", "/api/change-password", {"password": "view-pass-44"}, VIEW)
check(st == 200 and not _users.must_change("view@t"), "and can change it")
# a password change ends the account's other sessions (fork 92c8e40: a session counter in the cookie) and re-issues
# this browser's cookie: the viewer goes on with the FRESH one, and the one it signed in with no longer opens anything
VIEW = cookie_from(hdrs)
check(bool(VIEW) and VIEW != OLD_VIEW, "the change re-issues this browser's cookie")
st, js, _ = call("GET", "/api/me", None, OLD_VIEW)
check(st == 401, "the cookie from before the change no longer signs in")
st, js, _ = call("GET", "/api/me", None, VIEW)
check(st == 200 and js["role"] == "viewer", "the fresh cookie does, still a viewer")
PASSWORDS += ["view-pass-33", "view-pass-44"]

# ================================================ 4. admin-only endpoints
for c, who in ((EST, "estimator"), (VIEW, "viewer")):
    for p in ("/api/users", "/api/team/activity"):
        st, js, _ = call("GET", p, None, c)
        check(st == 404 and js == {"detail": "not found"}, f"{who} GET {p} -> 404")
st, js, _ = call("POST", "/api/users/est2%40t/role", {"role": "admin"}, EST)
check(st == 404, "estimator POST /api/users/{u}/role -> 404")
st, js, _ = call("GET", "/api/team/activity", None, ADMIN)
check(st == 200 and isinstance(js.get("events"), list), "admin GET /api/team/activity -> 200")

# ================================================ 5. accounts: add, role, reset
st, js, _ = call("POST", "/api/users", {"username": "new@t", "role": "viewer"}, ADMIN)
check(st == 200 and js["role"] == "viewer" and js["staff"] is False
      and _users.role_of("new@t") == "viewer", "POST /api/users with a role")
TEMP1 = js.get("temp_password") or ""
st, js, _ = call("POST", "/api/users", {"username": "new2@t", "staff": True, "role": "estimator"}, ADMIN)
check(st == 200 and js["role"] == "estimator" and js["staff"] is False, "role wins over staff on add")
TEMP2 = js.get("temp_password") or ""
st, js, _ = call("POST", "/api/users", {"username": "new3@t", "staff": True}, ADMIN)
check(st == 200 and js["role"] == "admin" and js["staff"] is True, "staff alone still makes an admin")
TEMP3 = js.get("temp_password") or ""
st, js, _ = call("POST", "/api/users", {"username": "new4@t", "role": "owner"}, ADMIN)
check(st == 400 and _users.find("new4@t") is None, "an unknown role on add -> 400, nothing made")

st, js, _ = call("POST", "/api/users/new%40t/role", {"role": "estimator"}, ADMIN)
check(st == 200 and js == {"ok": True, "user": "new@t", "role": "estimator", "staff": False},
      "POST /api/users/{u}/role -> {ok, user, role, staff}")
st, js, _ = call("POST", "/api/users/new%40t/role", {"role": "superuser"}, ADMIN)
check(st == 400 and _users.role_of("new@t") == "estimator", "unknown role -> 400")
st, js, _ = call("POST", "/api/users/ghost%40t/role", {"role": "viewer"}, ADMIN)
check(st == 404, "no such account -> 404")
st, js, _ = call("POST", "/api/users/new%40t/reset", {}, ADMIN)
check(st == 200 and js["role"] == "estimator" and _users.role_of("new@t") == "estimator",
      "reset keeps the role")
TEMP4 = js.get("temp_password") or ""
st, js, _ = call("POST", "/api/users/new2%40t/staff", {"staff": True}, ADMIN)
check(st == 200 and _users.role_of("new2@t") == "admin" and js["role"] == "admin",
      "the staff route still promotes (to admin)")
st, js, _ = call("POST", "/api/users/new2%40t/staff", {"staff": False}, ADMIN)
check(st == 200 and _users.role_of("new2@t") == "estimator", "and demotes (to estimator)")
st, js, _ = call("DELETE", "/api/users/new2%40t", None, ADMIN)
check(st == 200, "an admin removes an account")
for t in (TEMP1, TEMP2, TEMP3, TEMP4):
    check(len(t) == 12, "a temporary password came back once")

# ================================================ 6. the last admin
_users.remove_user("new3@t")                     # leave admin@t the only admin
check([r["user"] for r in _users.list_users() if r["role"] == "admin"] == ["admin@t"],
      "fixture: admin@t is the last admin")
st, js, _ = call("POST", "/api/users/admin%40t/role", {"role": "viewer"}, ADMIN)
check(st == 409 and "last admin" in js["detail"] and _users.role_of("admin@t") == "admin",
      "the last admin cannot demote themselves (409)")
st, js, _ = call("POST", "/api/users/admin%40t/role", {"role": "estimator"}, ADMIN)
check(st == 409, "to any role")
st, js, _ = call("POST", "/api/users/admin%40t/staff", {"staff": False}, ADMIN)
check(st == 400 and _users.is_staff("admin@t"), "nor through the staff route")
st, js, _ = call("DELETE", "/api/users/admin%40t", None, ADMIN)
check(st == 400 and _users.find("admin@t"), "nor remove themselves")
st, js, _ = call("POST", "/api/users/est2%40t/role", {"role": "admin"}, ADMIN)
check(st == 200 and _users.role_of("est2@t") == "admin", "promote a second admin")
st, js, _ = call("POST", "/api/users/admin%40t/role", {"role": "estimator"}, EST2)
check(st == 200 and _users.role_of("admin@t") == "estimator",
      "with two admins, one may demote the other")
st, js, _ = call("GET", "/api/users", None, ADMIN)
check(st == 404, "the demoted account loses the admin pages at once")
st, js, _ = call("POST", "/api/users/est2%40t/role", {"role": "viewer"}, EST2)
check(st == 409 and _users.role_of("est2@t") == "admin", "and now est2 is the last admin: 409")
st, js, _ = call("DELETE", "/api/users/est2%40t", None, EST2)
check(st == 400 and _users.find("est2@t"), "nor can est2 remove itself")
st, js, _ = call("POST", "/api/users/admin%40t/role", {"role": "admin"}, EST2)
check(st == 200 and _users.role_of("admin@t") == "admin", "restore admin@t")
st, js, _ = call("POST", "/api/users/est2%40t/role", {"role": "estimator"}, EST2)
check(st == 400 and "own role" in js["detail"], "an admin cannot change their own role (not the last one either)")
st, js, _ = call("POST", "/api/users/est2%40t/role", {"role": "estimator"}, ADMIN)
check(st == 200 and _users.role_of("est2@t") == "estimator", "admin@t demotes est2 back")
n_audit = len(audit_lines())
st, js, _ = call("POST", "/api/users/est2%40t/role", {"role": "estimator"}, ADMIN)
check(st == 200 and len(audit_lines()) == n_audit, "re-stating a role is a no-op: 200, nothing audited")

# the fork's session counter (92c8e40, users._keep_sessions) through THIS build's role route: a move to viewer ends
# the account's sessions (it must not keep writing from a tab it already had open); estimator <-> admin does not
_users.add_user("rv@t", "rv-pass-12345", role="estimator")
PASSWORDS.append("rv-pass-12345")
st, RV = login("rv@t", "rv-pass-12345")
st, js, _ = call("POST", "/api/users/rv%40t/role", {"role": "admin"}, ADMIN)
st2, js2, _ = call("GET", "/api/me", None, RV)
check(st == 200 and st2 == 200 and js2["role"] == "admin", "estimator -> admin: the open session goes on, as an admin")
st, js, _ = call("POST", "/api/users/rv%40t/role", {"role": "viewer"}, ADMIN)
st2, js2, _ = call("GET", "/api/me", None, RV)
check(st == 200 and st2 == 401, "admin -> viewer over /role: the account's open session has ended")
st, RV = login("rv@t", "rv-pass-12345")
st2, js2, _ = call("GET", "/api/me", None, RV)
check(st == 200 and js2["role"] == "viewer", "a new sign-in is a viewer")
st, js, _ = call("POST", "/api/job/%s/page/0/calcstate" % SHARED, {"v": 1}, RV)
check(st == 403 and (js or {}).get("detail") == READ_ONLY, "and the viewer gate holds for it")
_users.remove_user("rv@t")

# ================================================ 7. /api/users rows
st, js, _ = call("GET", "/api/users", None, ADMIN)
rows = {r["user"]: r for r in js["users"]}
check(st == 200 and all({"user", "staff", "must_change", "role", "last_active",
                         "last_login", "projects"} <= set(r) for r in rows.values()),
      "every /api/users row carries role, last_active, last_login, projects")
check(rows["est@t"]["projects"] == 1 and rows["view@t"]["projects"] == 0,
      "projects counts the registry jobs each account owns")
check(rows["view@t"]["role"] == "viewer" and rows["admin@t"]["role"] == "admin",
      "rows carry the role")
la = rows["est@t"]["last_active"] or ""
check(len(la) == 20 and la.endswith("Z") and rows["est@t"]["last_login"],
      "a signed-in account has an ISO-UTC last_active and a last_login")
check(rows["quiet@t"]["last_active"] is None and rows["quiet@t"]["last_login"] is None,
      "an account that never signed in reports nulls")
act_file = JOBS_DIR / "user_activity.json"
disk = json.loads(act_file.read_text())
check("est@t" in disk and disk["est@t"].get("last_login"),
      "last-active lives in its own file, user_activity.json")
check(all("last_active" not in r and "last_login" not in r
          for r in json.loads(_users.store_path().read_text()).values()),
      "and never in users.json")

# the throttle: at most one write per account per 60 s
users_mtime = _users.store_path().stat().st_mtime_ns
_users._ACT_WRITTEN.pop("quiet@t", None)
check(_users.note_seen("quiet@t") is True, "the first sighting of an account is written")
before = act_file.read_bytes()
time.sleep(1.1)
check(_users.note_seen("quiet@t") is False and act_file.read_bytes() == before,
      "a second one within 60 s is not written")
check(_users.activity()["quiet@t"]["last_active"] > json.loads(before)["quiet@t"]["last_active"],
      "...but it is what the API reports (held in memory)")
_users._ACT_WRITTEN["quiet@t"] -= 61
check(_users.note_seen("quiet@t") is True, "once 60 s have passed the next sighting is written")
check(_users.store_path().stat().st_mtime_ns == users_mtime, "users.json was not rewritten by any of it")

# a failed activity write never fails a request
blocker = JOBS_DIR / "not-a-dir"
blocker.write_text("x")
_real_path = _users.activity_path
_users.activity_path = lambda: blocker / "user_activity.json"
_users._ACT_WRITTEN.clear()
check(_users.note_seen("est@t") is False, "an unwritable activity file: note_seen says False")
_users._ACT_WRITTEN.clear()          # so the middleware's own call tries, and fails
st, js, _ = call("GET", "/api/me", None, EST)
check(st == 200 and js["user"] == "est@t", "and a request whose write fails still succeeds")
_users.activity_path = _real_path

# ================================================ 8. audit lines
st, js, _ = call("POST", "/api/job/%s/page/0/detect" % EJOB, {}, EST)
check(st == 200 and _QUEUED == [(EJOB, 0)], "the estimator's detect is queued")
st, js, _ = call("POST", "/api/job/%s/page/0/approve" % EJOB,
                 {"labels": {"2": "SEWER"}, "image_dpi": 300,
                  # the page sends the scale record's stamp it loaded (server._scale_seen_check)
                  "scale_seen": server._scale_stamp(EJOB, 0)}, EST)
check(st == 200 and js["exported"] == 1, "the estimator approves their sheet")
lines = audit_lines()
by = {}
for e in lines:
    by.setdefault(e["action"], []).append(e)
check(all(set(e) == {"ts", "user", "action", "job", "name", "page", "detail"} for e in lines),
      "every audit line has exactly the seven fields")
check(all(e["ts"].endswith("Z") and len(e["ts"]) == 20 for e in lines), "ts is UTC ISO with a Z")
ua = [e for e in by.get("user_add", []) if e["name"] == "new@t"]
check(ua and ua[0]["user"] == "admin@t" and ua[0]["detail"] == "viewer" and ua[0]["job"] is None,
      "user_add: actor in user, target in name, the role in detail")
ur = [e for e in by.get("user_role", []) if e["name"] == "admin@t"]
check(any(e["user"] == "est2@t" and e["detail"] == "admin->estimator" for e in ur),
      "user_role: detail is old->new")
check(any(e["name"] == "new@t" and e["user"] == "admin@t" for e in by.get("user_reset_password", [])),
      "user_reset_password recorded with its target")
check(any(e["name"] == "new2@t" for e in by.get("user_remove", [])), "user_remove recorded")
lg = [e for e in by.get("login", []) if e["user"] == "est@t"]
check(lg and lg[0]["name"] is None and lg[0]["job"] is None, "login: the account in user, name null")
up = by.get("project_upload", [])
check(any(e["job"] == EJOB and e["user"] == "est@t" and e["name"] == "Estimator Bid Set.pdf"
          for e in up), "project_upload: the job, the actor, the display name")
sd = by.get("sheet_detect", [])
check(sd and sd[-1]["job"] == EJOB and sd[-1]["page"] == 0 and sd[-1]["detail"] == "started",
      "sheet_detect recorded")
sa = by.get("sheet_approve", [])
check(sa and sa[-1]["job"] == EJOB and sa[-1]["page"] == 0 and sa[-1]["user"] == "est@t"
      and sa[-1]["name"] == "Estimator Bid Set.pdf", "sheet_approve recorded")
check(not any(e["user"] == "view@t" and e["action"] not in ("login",) for e in lines),
      "nothing the viewer was refused left an audit line")

# ================================================ 9. a project's history
# ledger events the audited endpoints never see: a trench, a structure, and an
# approve from before the audit log existed (an hour ago, no audit twin)
_ledger.record("trench_set", EJOB, 0, None, {"depth": 6}, None)
_ledger.record("structure_add", EJOB, 0, None, {"name": "Structure 1"}, {"name": "Structure 1"})
_ledger.record("chain_excluded", EJOB, 0, {"label": "x"}, None, {"chain_id": 9})
_ledger.record("approve_export", EJOB, 0, None, {"n_elements": 3, "items": [1, 2]},
               None, ts=time.time() - 3600)
_ledger.record("trench_set", SHARED, 0, None, {"depth": 1}, None)   # another job's

st, js, _ = call("GET", "/api/job/%s/activity" % EJOB, None, EST)
ev = js["events"] if st == 200 else []
acts = [e["action"] for e in ev]
check(st == 200 and ev, "the owner reads their project's history")
check(all("user" not in e for e in ev), "the owner (not an admin) gets no user field at all")
check(all(set(e) == {"ts", "action", "page", "detail"} for e in ev), "rows are {ts, action, page, detail}")
check([e["ts"] for e in ev] == sorted((e["ts"] for e in ev), reverse=True), "newest first")
check("trench_set" in acts and "structure_set" in acts, "ledger events merge in under their action names")
check(any(e["action"] == "structure_set" and e["detail"] == "Structure 1 added" for e in ev),
      "structure_add reads as structure_set, 'Structure 1 added'")
check("chain_excluded" not in acts and "style_label" not in acts,
      "an approve's per-chain parts stay out of the history")
check(acts.count("sheet_approve") == 1 and acts.count("approve_export") == 1,
      "this approve listed once (its ledger twin dropped); the older one kept")
old = [e for e in ev if e["action"] == "approve_export"]
check(old and old[0]["detail"] == "3 lines, 2 items" and old[0]["ts"].endswith("Z"),
      "the older approve_export: plain detail, UTC stamp")
check(sum(1 for e in ev if e["action"] == "trench_set") == 1, "another job's events are not mixed in")
st, js, _ = call("GET", "/api/job/%s/activity?limit=2" % EJOB, None, EST)
check(st == 200 and len(js["events"]) == 2, "limit is honoured")

st, js, _ = call("GET", "/api/job/%s/activity" % EJOB, None, EST2)
check(st == 404 and js == {"detail": "unknown job"}, "another estimator's job -> 404 unknown job")
st, js, _ = call("GET", "/api/job/%s/activity" % EJOB, None, VIEW)
check(st == 404, "and a viewer's too")
st, js, _ = call("GET", "/api/job/%s/activity" % EJOB, None, ADMIN)
ev = js["events"] if st == 200 else []
check(st == 200 and all("user" in e for e in ev), "an admin gets a user field on every row")
check(any(e["user"] == "est@t" and e["action"] == "sheet_approve" for e in ev),
      "and sees who approved")
check(all(e["user"] is None for e in ev if e["action"] in ("trench_set", "structure_set")),
      "ledger rows name nobody")
st, js, _ = call("GET", "/api/job/%s/activity" % SHARED, None, VIEW)
check(st == 200 and all("user" not in e for e in js["events"]), "a viewer reads a shared job's history, no names")
st, js, _ = call("GET", "/api/job/0f0f0f0f0f0f/activity", None, ADMIN)
check(st == 404 and js == {"detail": "unknown job"}, "no registry row -> 404, even for an admin")

# ================================================ 10. delete, the team feed
st, js, _ = call("DELETE", "/api/job/%s" % EJOB, None, EST)
check(st == 200, "the estimator deletes their project")
pd = [e for e in audit_lines() if e["action"] == "project_delete"]
check(pd and pd[-1]["job"] == EJOB and pd[-1]["user"] == "est@t"
      and pd[-1]["name"] == "Estimator Bid Set.pdf", "project_delete recorded with the name")
st, js, _ = call("GET", "/api/team/activity?limit=500", None, ADMIN)
tev = js["events"]
check(st == 200 and tev and tev[0]["action"] == "project_delete", "the team feed is newest first")
check(all(set(e) == {"ts", "user", "action", "job", "name", "page", "detail"} for e in tev),
      "team events are whole audit lines")
st, js, _ = call("GET", "/api/team/activity?limit=3", None, ADMIN)
check(len(js["events"]) == 3, "limit is honoured")

text = audit_bytes()
for pw in PASSWORDS + [TEMP1, TEMP2, TEMP3, TEMP4]:
    check(pw and pw not in text, "no password in the audit log (%s...)" % pw[:3])
check("tk_session" not in text and "hash" not in text, "no cookie, no hash")

# ================================================ 11. single-password mode
_users.save({})
server._UI_PASSWORD = "site-pw-for-team-test"
try:
    st, SITE = login("whoever", "site-pw-for-team-test")
    check(st == 200 and SITE, "single-password mode signs in")
    st, js, _ = call("GET", "/api/me", None, SITE)
    check(st == 200 and js["role"] is None and js["user"] is None, "/api/me: no account, no role")
    st, js, _ = call("GET", "/api/team/activity", None, SITE)
    check(st == 409, "/api/team/activity -> 409 like /api/users")
    st, js, _ = call("POST", "/api/users/x%40t/role", {"role": "viewer"}, SITE)
    check(st == 409, "/api/users/{u}/role -> 409")
    lg = [e for e in audit_lines() if e["action"] == "login"]
    check(lg[-1]["user"] is None, "a single-password sign-in is audited with user null")
finally:
    server._UI_PASSWORD = None

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all team-roles checks passed")
