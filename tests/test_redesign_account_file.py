"""THE REDESIGN'S OWN ACCOUNT FUNCTIONS UNDER THE ACCOUNT-FILE RULES (2026-10-04, the second merge of the fork).

The fork's fix round made the account file safe (takeoff_engine/users.py, THE ACCOUNT FILE; tests/test_account_file.py):
a file that is there and can not be read is never "no accounts", one writer at a time, each change read from the file
inside the writer's lock. The redesign has account code the fork does not have: ROLES (admin / estimator / viewer),
set_role, its own set_password / add_user / set_staff and command line (they carry the role), the role and team
routes, and the viewer gate in _SessionAuth. This file holds the same rules for THOSE:

  R  the functions, one by one: a change while the file can not be read is refused and nothing is written - not the
     file, not this process's memory; a write that fails leaves memory as it was; each change is read from the file
     INSIDE the lock (it waits for another writer and is made on what that writer left); the command line the same,
     with roles
  G  through the gate (the real middleware, no server listening): with a roster in memory the role routes answer 503
     and write nothing, a viewer stays read-only, nobody unsigned gets in; a server STARTED on a file it can not read
     answers 503 on every route that needs the accounts - the team routes and the viewer's writes too; and a request is
     judged on ONE read of the roster (a viewer removed while its request is being judged is never let through)
  S  THE STORM: threads and processes signing in, signing out, changing roles and saving at once, while requests
     with no cookie and a viewer's writes keep arriving: never an unauthenticated answer on a route that needs a
     sign-in, never a lost account, never a lost role

Every name and word here is made up.

    python tests/test_redesign_account_file.py
"""
import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THIS = str(Path(__file__).resolve())
sys.path.insert(0, str(ROOT))

ADMIN, EST, VIEW, OLD = "admin@t.example", "est@t.example", "view@t.example", "old-staff@t.example"
NAMES = (ADMIN, EST, VIEW, OLD)
ROLE = {ADMIN: "admin", EST: "estimator", VIEW: "viewer", OLD: "admin"}      # OLD: a record from before roles
PW = {ADMIN: "orchid-lantern-4471", EST: "basalt-meadow-9083", VIEW: "walnut-harbor-3318", OLD: "granite-sparrow-2210"}
SITE_PW = "shared-site-password-1"
READ_ONLY = "Read-only: your role is Viewer. Ask an admin for Estimator access."
ROLES3 = ("admin", "estimator", "viewer")
ENV_OFF = ("TAKEOFF_HOSTED", "TAKEOFF_UI_PASSWORD", "INBOUND_HOOK_USER", "INBOUND_HOOK_PASS", "INBOUND_ADDRESS")

# the storm's accounts: four nobody's role is changed on (they are signed out by every worker), four that sign in
# and out through the routes, the admin who works the accounts routes, and a viewer who keeps trying to write
S_SHARED = {"s-admin@storm.example": "admin", "s-est@storm.example": "estimator", "s-view@storm.example": "viewer",
            "s-old@storm.example": None}                # None: staff flag and no role (reads as admin)
S_PEOPLE = {"p-admin@storm.example": "admin", "p-est1@storm.example": "estimator", "p-view@storm.example": "viewer",
            "p-est2@storm.example": "estimator"}
S_BOSS, S_WATCH = "boss@storm.example", "watch@storm.example"
NEED_SIGN_IN = ("/api/me", "/api/projects", "/api/recent", "/", "/api/users", "/api/team/activity")

APP = None                                      # server.app, once a process has imported it


# ---- the ASGI driver: every request goes through the real middleware ------------------------------------------------
async def asgi(method, path, body=None, cookie=None, raw=None, ctype=None, marker=True):
    """-> (status, parsed JSON or None, the session cookie the answer set or "")."""
    path, _, query = path.partition("?")
    headers = [(b"host", b"testserver")]
    if marker:
        headers.append((b"x-core-client", b"3"))
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
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": urllib.parse.unquote(path), "raw_path": path.encode(), "query_string": query.encode(),
             "root_path": "", "headers": headers, "client": ("127.0.0.1", 1234), "server": ("testserver", 80)}
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

    await APP(scope, receive, send)
    try:
        js = json.loads(out["body"].decode() or "null")
    except (ValueError, UnicodeDecodeError):
        js = None
    ck = ""
    for k, v in out["headers"]:
        if k.lower() == b"set-cookie" and v.startswith(b"tk_session="):
            ck = v.decode().split(";")[0]
    return out["status"], js, ck


def call(method, path, body=None, cookie=None, **kw):
    return asyncio.run(asgi(method, path, body, cookie, **kw))


def status(method, path, body=None, cookie=None, **kw):
    try:
        return call(method, path, body, cookie, **kw)[0]
    except Exception as e:                      # noqa: BLE001 - an exception out of the app is an answer too
        return f"raised {type(e).__name__}"


def bump(d, k, n=1):
    d[k] = d.get(k, 0) + n


def quick_hash(users) -> None:
    """The storm measures the account FILE, not the hashing: every record it makes is hashed with a low count."""
    real = users.hash_password
    if getattr(real, "_quick", False):
        return

    def quick(password, *, salt=None, iterations=None):
        return real(password, salt=salt, iterations=1000)

    quick._quick, quick._real = True, real
    users.hash_password = quick


# ---- the parts that run in a process of their own ------------------------------------------------------------------
def child_hold_add(store: str, seconds: str, name: str, role: str) -> None:
    """Another program that holds the writer's lock for a while, then makes an account, then lets go."""
    os.environ["TAKEOFF_JOBS_DIR"] = store
    from takeoff_engine import users
    if not hasattr(users, "_one_writer"):
        print("no-lock", flush=True)
        return
    with users._one_writer():
        print("held", flush=True)
        time.sleep(float(seconds))
        users.add_user(name, "made-up-word-1", role=role)
    print("added", flush=True)


def child_fresh(store: str, mode: str, ck_admin: str, ck_view: str) -> None:
    """A server started on the store as it is: what the redesign's routes answer before the account file was ever
    read by this process."""
    global APP
    os.environ["TAKEOFF_JOBS_DIR"] = store
    os.environ["TAKEOFF_LEDGER_DIR"] = store + "_lg"
    for k in ENV_OFF:
        os.environ.pop(k, None)
    if mode == "password":
        os.environ.update({"TAKEOFF_HOSTED": "1", "TAKEOFF_UI_PASSWORD": SITE_PW})
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        import server
        server._classify_async = lambda *a, **k: None
        APP = server.app
        users_file = Path(store) / "users.json"
        before = users_file.read_bytes()
        out = {"the sign-in page": status("GET", "/login"),
               "me, no cookie": status("GET", "/api/me"),
               "me, the admin": status("GET", "/api/me", cookie=ck_admin),
               "me, the viewer": status("GET", "/api/me", cookie=ck_view),
               "the accounts, the admin": status("GET", "/api/users", cookie=ck_admin),
               "the team's activity, the admin": status("GET", "/api/team/activity", cookie=ck_admin),
               "a role change, the admin": status("POST", f"/api/users/{EST}/role", {"role": "viewer"}, cookie=ck_admin),
               "an account made with a role, the admin": status("POST", "/api/users",
                                                                {"username": "new@t.example", "role": "viewer"},
                                                                cookie=ck_admin),
               "a write, the viewer": status("POST", "/api/branding", {"company": "Viewer Co"}, cookie=ck_view),
               "the app page, no cookie": status("GET", "/"),
               "projects, no cookie": status("GET", "/api/projects"),
               "sign-in, the admin's password": status("POST", "/api/login", {"username": ADMIN, "password": PW[ADMIN]}),
               "sign-in, the site password": status("POST", "/api/login", {"username": "anyone", "password": SITE_PW})}
        out["file unchanged"] = users_file.read_bytes() == before
        # the file is put right while this process runs: it reads again, with nothing restarted
        users_file.write_bytes((Path(store) / "good.json").read_bytes())
        st, js, _ = call("GET", "/api/users", cookie=ck_admin)
        out["after: the accounts, the admin"] = [st, {r["user"]: r["role"] for r in (js or {}).get("users", [])}]
        st, js, _ = call("POST", "/api/branding", {"company": "Viewer Co"}, cookie=ck_view)
        out["after: a write, the viewer"] = [st, (js or {}).get("detail")]
        out["after: me, no cookie"] = status("GET", "/api/me")
    out["said"] = [ln for ln in err.getvalue().splitlines() if ln.startswith("[users]")][:4]
    print("RESULT " + json.dumps(out))


def storm_work(users, wid: str, rounds: int) -> dict:
    """One worker of the storm (a thread, or another process): sign-outs of the shared accounts, and accounts of
    its own made, re-roled with each of the redesign's functions, given a new password and removed."""
    out = {"bumps": {}, "kept": {}, "removed": [], "unsure": [], "raised": {}, "ops": 0}
    shared = list(S_SHARED)

    def op(what, fn, want=True):
        out["ops"] += 1
        try:
            got = fn()
        except Exception as e:                  # noqa: BLE001
            bump(out["raised"], f"{what} raised {type(e).__name__}")
            return False
        ok = isinstance(got, dict) if want is dict else got is True
        if not ok:
            bump(out["raised"], f"{what} answered {got!r} for an account that is there")
        return ok

    for r in range(rounds):
        acct = shared[r % len(shared)]
        if op("bump_session", lambda: users.bump_session(acct)):
            bump(out["bumps"], acct)
        if r % 4:
            continue
        i = r // 4
        name = f"temp-{wid}-{r}@storm.example"
        role = ROLES3[i % 3]
        if not op("add_user", lambda: users.add_user(name, "made-up-" + name, role=role), dict):
            out["unsure"].append(name)
            continue
        sure = True
        if op("set_staff on", lambda: users.set_staff(name, True)):
            role = "admin"
        else:
            sure = False
        if i % 3 == 0:
            if op("set_staff off", lambda: users.set_staff(name, False)):
                role = "estimator" if role == "admin" else role
            else:
                sure = False
        nxt = ROLES3[(i + 1) % 3]               # the role it ends on: each of the three in turn
        if op("set_role", lambda: users.set_role(name, nxt)):
            role = nxt
        else:
            sure = False
        if not op("set_password", lambda: users.set_password(name, "another-" + name)):
            sure = False
        if i % 4 == 1 and not op("add_user (a reset)", lambda: users.add_user(name, "reset-" + name), dict):
            sure = False
        if i % 2 == 1:
            if op("remove_user", lambda: users.remove_user(name)):
                out["removed"].append(name)
            else:
                out["unsure"].append(name)
        elif sure:
            out["kept"][name] = role
        else:
            out["unsure"].append(name)
    return out


def storm_read(users, stop, out: dict, pause: float = 0.0) -> None:
    while not stop():
        try:
            r = users.load()
            bump(out, "reads")
            if not r:
                bump(out, "NO ACCOUNTS")
            elif not all(s in r for s in S_SHARED):
                bump(out, "a shared account missing")
        except Exception as e:                  # noqa: BLE001
            bump(out, "load raised " + type(e).__name__)
        if pause:
            time.sleep(pause)


def _wait_go(store: Path) -> None:
    end = time.time() + 180
    while not (store / "GO").exists() and time.time() < end:
        time.sleep(0.005)


def child_storm(store: str, kind: str, wid: str, rounds: str) -> None:
    os.environ["TAKEOFF_JOBS_DIR"] = store
    from takeoff_engine import users
    quick_hash(users)
    (Path(store) / f"ready-{wid}").write_text("")
    _wait_go(Path(store))
    if kind == "w":
        print("RESULT " + json.dumps(storm_work(users, wid, int(rounds))))
    else:
        out = {}
        storm_read(users, lambda: (Path(store) / "STOP").exists(), out)
        print("RESULT " + json.dumps(out))


if len(sys.argv) > 1 and sys.argv[1] == "--hold-add":
    child_hold_add(*sys.argv[2:6])
    sys.exit(0)
if len(sys.argv) > 1 and sys.argv[1] == "--fresh":
    child_fresh(*sys.argv[2:6])
    sys.exit(0)
if len(sys.argv) > 1 and sys.argv[1] == "--storm":
    child_storm(*sys.argv[2:6])
    sys.exit(0)


# ---- this process: a store of its own, a laptop (no hosted flag, no site password) with four accounts ---------------
_JOBS = Path(tempfile.mkdtemp(prefix="rd_acct_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(_JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="rd_acct_lg_")
for _k in ENV_OFF:
    os.environ.pop(_k, None)

import pymupdf  # noqa: E402

import server  # noqa: E402
from takeoff_engine import audit as _audit  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

server._classify_async = lambda *a, **k: None
server._enqueue_detect = lambda j, p: False
server._UI_PASSWORD = None
server.HOSTED = False
APP = server.app

P = _users.store_path()
CHILD_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
CK = {}
fails = 0
checks = 0


def check(ok, msg, got=None):
    global fails, checks
    checks += 1
    print(("ok: " if ok else "FAIL: ") + msg + ("" if ok or got is None else f"  (got {str(got)[:500]})"))
    if not ok:
        fails += 1


def run(fn):
    try:
        fn()
    except Exception as e:                      # noqa: BLE001 - on the trees before this round some tests land here
        check(False, f"{fn.__name__}: raised {type(e).__name__}: {str(e)[:300]}")


def roster() -> dict:
    out = {}
    for n in NAMES:
        rec = _users.hash_password(PW[n], iterations=1000)
        if n == OLD:
            rec["staff"] = True                 # written before roles existed: the staff flag and no role
        else:
            rec["role"] = ROLE[n]
            if ROLE[n] == "admin":
                rec["staff"] = True
        out[n] = rec
    return out


def on_disk():
    try:
        d = json.loads(P.read_bytes())
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def put_right() -> None:
    """The four accounts and nothing else are in the file, read by this process, each with a cookie."""
    global CK
    try:
        P.unlink()                              # written from nothing: the file may hold anything by now
    except OSError:
        pass
    _users.save(roster())
    _users.load()
    CK = {n: f"{server.SESSION_COOKIE}={server._sign_session(n)}" for n in NAMES}


def cut() -> bytes:
    """The file cut short, as a write that stopped half way leaves it. -> its bytes before."""
    good = P.read_bytes()
    P.write_bytes(good[:len(good) // 2])
    return good


def memory() -> str:
    """Everything this process holds about the accounts, as load() answers it."""
    return json.dumps(_users.load(), sort_keys=True)


def outcome(fn):
    try:
        return fn()
    except Exception as e:                      # noqa: BLE001
        return type(e).__name__


def role_in(d, name):
    rec = (d or {}).get(name)
    return _users._role(rec) if isinstance(rec, dict) else None


def in_step(rec) -> bool:
    """`staff` says what the role says (admin <=> staff True), for a record that states a role."""
    return isinstance(rec, dict) and ((rec.get("staff") is True) == (_users._role(rec) == "admin"))


def left_beside() -> list:
    return sorted(f.name for f in _JOBS.iterdir()
                  if f.name.startswith("users.json") and f.name not in ("users.json", "users.json.lock"))


def audit_actions(*actions) -> int:
    n = 0
    for f in sorted(_audit.audit_dir().glob("audit-*.jsonl")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            if ln.strip() and json.loads(ln).get("action") in actions:
                n += 1
    return n


def pdf_bytes(salt: bytes) -> bytes:
    doc = pymupdf.open()
    doc.new_page(width=612, height=792)
    raw = doc.tobytes()
    doc.close()
    return raw + b"\n%" + salt + b"\n"


def upload(filename, data, cookie):
    bnd = "----rdacctboundary"
    raw = (("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\nContent-Type: application/pdf"
            "\r\n\r\n" % (bnd, filename)).encode() + data + ("\r\n--%s--\r\n" % bnd).encode())
    return call("POST", "/api/upload?classify=0", raw=raw, ctype="multipart/form-data; boundary=" + bnd, cookie=cookie)


# the redesign's own changes, each as (what it is called here, the call, what the file must show afterwards)
def changes():
    return (
        ("set_role", lambda: _users.set_role(EST, "viewer"),
         lambda d: role_in(d, EST) == "viewer" and in_step(d[EST])),
        ("set_staff", lambda: _users.set_staff(EST, True),
         lambda d: role_in(d, EST) == "admin" and in_step(d[EST])),
        ("set_password", lambda: _users.set_password(VIEW, "a-new-word-1234"),
         lambda d: _users.verify("a-new-word-1234", d[VIEW]) and role_in(d, VIEW) == "viewer"),
        ("add_user (a password reset)", lambda: bool(_users.add_user(VIEW, "a-reset-word-12")),
         lambda d: _users.verify("a-reset-word-12", d[VIEW]) and role_in(d, VIEW) == "viewer"),
        ("add_user (a new viewer)", lambda: bool(_users.add_user("new@t.example", "a-first-word-12", role="viewer")),
         lambda d: role_in(d, "new@t.example") == "viewer" and "staff" not in d["new@t.example"]),
    )


# =====================================================================================================================
def r1_a_change_is_refused_not_made_from_memory():
    for label, fn, _shows in changes():
        put_right()
        cut()
        damaged, before = P.read_bytes(), memory()
        got = outcome(fn)
        check(got == "Unreadable" and P.read_bytes() == damaged and memory() == before,
              f"r1: {label} while the file can not be read is REFUSED (Unreadable): the file is not written over "
              "from this process's memory, and that memory is as it was",
              (got, "file written" if P.read_bytes() != damaged else "file kept",
               "memory changed" if memory() != before else "memory kept"))


def r2_a_write_that_fails_leaves_memory_as_it_was():
    real = _users._write_file

    def refused(p, users):
        raise _users.Busy("the account file is held open by another program; nothing was changed")

    for label, fn, _shows in changes():
        put_right()
        on_file, before = P.read_bytes(), memory()
        _users._write_file = refused
        try:
            got = outcome(fn)
        finally:
            _users._write_file = real
        check(got == "Busy" and P.read_bytes() == on_file and memory() == before,
              f"r2: {label} whose write fails (Busy): this process does not go on serving the change it could not "
              "write - a role or a password that is in memory and not in the file",
              (got, "memory changed" if memory() != before else "memory kept"))


def _holder(seconds: float, name: str):
    k = subprocess.Popen([sys.executable, THIS, "--hold-add", str(_JOBS), str(seconds), name, "viewer"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT), env=CHILD_ENV)
    return k, k.stdout.readline().strip()


def r3_a_change_is_read_inside_the_lock():
    for i, (label, fn, shows) in enumerate(changes()):
        put_right()
        other = f"made-meanwhile-{i}@t.example"
        k, word = _holder(1.2, other)
        t0 = time.time()
        got = outcome(fn)                       # the other program holds the lock: this waits for it
        took = time.time() - t0
        k.communicate(timeout=120)
        d = on_disk() or {}
        check(word == "held" and got is True and took >= 0.8,
              f"r3: {label} WAITS while another program is changing the accounts, and is then made",
              (word, got, round(took, 2)))
        check(role_in(d, other) == "viewer" and all(n in d for n in NAMES) and bool(shows(d)),
              f"r3: ... on the roster that program left: the account it made meanwhile is there AND {label}'s change "
              "(a change read before the lock wrote an old roster back: the other account gone)",
              (sorted(d), {n: role_in(d, n) for n in sorted(d)}))


def _cli(*args):
    return subprocess.run([sys.executable, "-m", "takeoff_engine.users", *args], capture_output=True, text=True,
                          cwd=str(ROOT), env=CHILD_ENV, timeout=180)


def r4_the_command_line_with_roles():
    put_right()
    name = "cli@t.example"
    o = _cli("add", name, "--password", "willow-anchor-9917", "--role", "viewer")
    d = on_disk() or {}
    check(o.returncode == 0 and f"added {name} [viewer]" in o.stdout and "VIEWER - read-only" in o.stdout
          and role_in(d, name) == "viewer" and "staff" not in d.get(name, {}) and all(n in d for n in NAMES),
          "r4: the command line makes a viewer (--role viewer): the role and no staff flag, every other account kept",
          (o.returncode, o.stdout[-200:], o.stderr[-200:]))
    o = _cli("add", name, "--password", "willow-anchor-0000")
    check(o.returncode == 0 and f"updated {name} [viewer]" in o.stdout and role_in(on_disk(), name) == "viewer",
          "r4: a password reset at the command line keeps the viewer a viewer", (o.returncode, o.stdout[-200:]))
    o = _cli("add", name, "--password", "willow-anchor-1111", "--staff")
    d = on_disk() or {}
    check(o.returncode == 0 and role_in(d, name) == "admin" and d.get(name, {}).get("staff") is True,
          "r4: --staff is the admin role, staff kept in step", (o.returncode, o.stdout[-200:]))
    o = _cli("list")
    check(o.returncode == 0 and f"{name}  [admin]" in o.stdout and f"{VIEW}  [viewer]" in o.stdout
          and f"{OLD}  [admin]" in o.stdout, "r4: list names each account's role (a record from before roles: admin)",
          o.stdout[-400:])
    # the command line's change is read inside the lock, like the functions'
    other = "made-meanwhile-cli@t.example"
    k, word = _holder(2.0, other)
    o = _cli("add", "cli2@t.example", "--password", "willow-anchor-2222", "--role", "estimator")
    k.communicate(timeout=120)
    d = on_disk() or {}
    check(word == "held" and o.returncode == 0 and role_in(d, other) == "viewer" and role_in(d, "cli2@t.example") == "estimator"
          and role_in(d, name) == "admin",
          "r4: an account made at the command line while another program is changing the accounts is made on the "
          "roster that program left (the account it made meanwhile is kept)", (word, o.returncode, sorted(d)))
    o = _cli("remove", name)
    d = on_disk() or {}
    check(o.returncode == 0 and name not in d and all(n in d for n in NAMES) and other in d,
          "r4: remove takes that account out and nobody else", (o.returncode, sorted(d)))
    # a file that can not be read: refused in words, nothing written
    put_right()
    cut()
    damaged = P.read_bytes()
    for what, args in (("add --role viewer", ("add", "cli3@t.example", "--password", "willow-anchor-3333", "--role", "viewer")),
                       ("a reset with --role admin", ("add", VIEW, "--password", "willow-anchor-4444", "--role", "admin")),
                       ("remove", ("remove", EST)), ("list", ("list",))):
        o = _cli(*args)
        check(o.returncode == 1 and "refused" in o.stdout and P.read_bytes() == damaged,
              f"r4: the command line's {what} at a file that can not be read: refused in words, exit 1, the file as it was",
              (o.returncode, o.stdout[-200:], o.stderr[-200:]))


def r5_the_role_of_a_name_in_a_given_roster():
    put_right()
    check(_users.role_of(VIEW) == "viewer" and _users.role_of(VIEW, {VIEW: {"role": "admin", "staff": True}}) == "admin"
          and _users.role_of(VIEW, {}) is None and _users.role_of(OLD, _users.load()) == "admin",
          "r5: role_of(name, roster) answers from the roster it is given (the one a request was signed in against); "
          "without one, from load()")


# =====================================================================================================================
def g1_the_role_routes_with_a_roster_in_memory():
    put_right()
    st, js, _ = call("GET", "/api/users", cookie=CK[ADMIN])
    rows0 = {r["user"]: r["role"] for r in (js or {}).get("users", [])}
    good = cut()
    damaged, before = P.read_bytes(), memory()
    n_audit = audit_actions("user_role", "user_add", "user_remove", "user_reset_password")
    got = {"me, no cookie": status("GET", "/api/me"), "the accounts, no cookie": status("GET", "/api/users"),
           "the team's activity, no cookie": status("GET", "/api/team/activity"),
           "the app page, no cookie": status("GET", "/"),
           "the accounts, an estimator": status("GET", "/api/users", cookie=CK[EST]),
           "the team's activity, the admin": status("GET", "/api/team/activity", cookie=CK[ADMIN])}
    want = {"me, no cookie": 401, "the accounts, no cookie": 401, "the team's activity, no cookie": 401,
            "the app page, no cookie": 303, "the accounts, an estimator": 404, "the team's activity, the admin": 200}
    check(got == want, "g1: the file cut short, a roster in memory: nobody unsigned reaches the team routes, an "
                       "estimator is still not an admin, the admin still is", {k: v for k, v in got.items() if v != want[k]})
    st, js, _ = call("GET", "/api/users", cookie=CK[ADMIN])
    rows = {r["user"]: r["role"] for r in (js or {}).get("users", [])}
    check(st == 200 and rows == rows0 == ROLE, "g1: ... the accounts page lists every account with its role, from "
                                                "the roster as last read", (st, rows))
    st, js, _ = call("POST", "/api/branding", {"company": "Viewer Co"}, cookie=CK[VIEW])
    st2, js2, _ = call("GET", "/api/me", cookie=CK[VIEW])
    check(st == 403 and (js or {}).get("detail") == READ_ONLY and st2 == 200 and (js2 or {}).get("role") == "viewer",
          "g1: ... a viewer is still a viewer: its write is refused (403 read-only), its reads go on", (st, js, st2, js2))
    writes = (("a role change", "POST", f"/api/users/{EST}/role", {"role": "viewer"}, ADMIN),
              ("the staff switch", "POST", f"/api/users/{EST}/staff", {"staff": True}, ADMIN),
              ("an account made with a role", "POST", "/api/users", {"username": "new@t.example", "role": "viewer"}, ADMIN),
              ("a password reset", "POST", f"/api/users/{VIEW}/reset", None, ADMIN),
              ("an account removed", "DELETE", f"/api/users/{EST}", None, ADMIN),
              ("a password change, by a viewer", "POST", "/api/change-password", {"password": "a-brand-new-word-1"}, VIEW))
    for what, method, path, body, who in writes:
        st, js, _ = call(method, path, body, cookie=CK[who])
        # (only the status and the sentence are shown: an answer that made an account carries its one-time password)
        check(st == 503 and "can not read its accounts" in str((js or {}).get("detail")) and P.read_bytes() == damaged
              and memory() == before,
              f"g1: {what} while the file can not be read is told so (503) - not answered as done, nothing written, "
              "this process's roster as it was",
              (st, (js or {}).get("detail"), "memory changed" if memory() != before else "memory kept"))
        if P.read_bytes() != damaged or memory() != before:
            put_right()
            cut()
            damaged, before = P.read_bytes(), memory()
    check(audit_actions("user_role", "user_add", "user_remove", "user_reset_password") == n_audit,
          "g1: ... and the activity log claims none of them (no account line for a change that was not made)")
    check(left_beside() == [], "g1: ... no temp file is left beside the account file", left_beside())
    P.write_bytes(good)
    put_right()
    st, js, _ = call("POST", f"/api/users/{EST}/role", {"role": "viewer"}, cookie=CK[ADMIN])
    check(st == 200 and role_in(on_disk(), EST) == "viewer" and _users.role_of(EST) == "viewer",
          "g1: the file put right: the same role change is made, and it is in the file", (st, js))


def g2_started_on_a_file_it_can_not_read():
    put_right()
    for mode in ("laptop", "password"):
        store = Path(tempfile.mkdtemp(prefix=f"rd_acct_fresh_{mode}_"))
        for f in ("users.json", "session.key"):
            (store / f).write_bytes((_JOBS / f).read_bytes())
        good = (store / "users.json").read_bytes()
        (store / "good.json").write_bytes(good)
        (store / "users.json").write_bytes(good[:len(good) // 2])
        out = subprocess.run([sys.executable, THIS, "--fresh", str(store), mode, CK[ADMIN], CK[VIEW]],
                             capture_output=True, text=True, cwd=str(ROOT), env=CHILD_ENV, timeout=300)
        line = next((ln for ln in out.stdout.splitlines() if ln.startswith("RESULT ")), None)
        if line is None:
            check(False, f"g2 ({mode}): the started server gave no result", (out.returncode, out.stderr[-300:]))
            continue
        got = json.loads(line[7:])
        where = "no hosted flag, no site password" if mode == "laptop" else "hosted, a site password set"
        closed = {k: v for k, v in got.items()
                  if k not in ("the sign-in page", "file unchanged", "said") and not k.startswith("after: ")}
        check(len(closed) == 12 and all(v == 503 for v in closed.values()),
              f"g2 ({where}): started on a file cut short, EVERY route of the redesign that needs the accounts "
              "answers 503 - the team routes, a role change, the viewer's write, the sign-in - never 200, never 403",
              {k: v for k, v in closed.items() if v != 503})
        check(got["the sign-in page"] == 200 and got["file unchanged"] is True and len(got["said"]) >= 1
              and "nobody can be signed in" in got["said"][0],
              f"g2 ({where}): the sign-in page still shows, the file is not written over, and the log says why",
              (got["the sign-in page"], got["file unchanged"], got["said"]))
        check(got["after: the accounts, the admin"] == [200, ROLE] and got["after: a write, the viewer"] == [403, READ_ONLY]
              and got["after: me, no cookie"] == 401,
              f"g2 ({where}): the file put right: the same process answers again - the admin sees every role, the "
              "viewer is read-only, nobody unsigned gets in",
              (got["after: the accounts, the admin"], got["after: a write, the viewer"], got["after: me, no cookie"]))


def g3_a_request_is_judged_on_one_read_of_the_roster():
    put_right()
    n_jobs = len(server.JOBS)
    real = _users.load
    got = {}
    for k in range(1, 9):
        seen = [0]

        def gone_from_the_kth_read():
            seen[0] += 1
            r = real()
            return r if seen[0] < k else {n: v for n, v in r.items() if n != VIEW}

        _users.load = gone_from_the_kth_read
        try:
            got[k] = upload(f"viewer-upload-{k}.pdf", pdf_bytes(b"viewer%d" % k), CK[VIEW])[0]
        finally:
            _users.load = real
    check(all(v in (401, 403) for v in got.values()) and len(server.JOBS) == n_jobs,
          "g3: a viewer whose account leaves the file WHILE its request is being judged is answered 401 (no such "
          "account) or 403 (read-only), whichever read the removal falls on - never let through as a write (the role "
          "was read a second time, found nothing, and the upload went in)", (got, len(server.JOBS) - n_jobs))


# =====================================================================================================================
def _pw(name: str) -> str:
    return "made-up-" + name


def _storm_roster() -> dict:
    out = {}
    for n, role in list(S_SHARED.items()) + list(S_PEOPLE.items()) + [(S_BOSS, "admin"), (S_WATCH, "viewer")]:
        rec = _users.hash_password(_pw(n))
        if role is None:
            rec["staff"] = True
        else:
            rec["role"] = role
            if role == "admin":
                rec["staff"] = True
        out[n] = rec
    return out


async def _swarm(threads, kids, pairs: int, api_n: int) -> dict:
    """The requests of the storm, all at once on one loop (as the server runs them): people signing in and out,
    requests with no cookie, a viewer's writes, and an admin working the accounts routes."""
    done = asyncio.Event()
    res = {"pairs": {}, "signouts": {}, "anon": {}, "viewer": {}, "api": {}, "kept": {}, "removed": [], "unsure": []}

    async def person(who, role):
        for _ in range(pairs):
            st1, _js, ck = await asgi("POST", "/api/login", {"username": who, "password": _pw(who)})
            st2, js2, _ = await asgi("GET", "/api/me", cookie=ck)
            mine = st2 == 200 and (js2 or {}).get("user") == who and (js2 or {}).get("role") == role
            st3, _js, _ = await asgi("POST", "/api/logout", cookie=ck)
            if st3 == 200:
                bump(res["signouts"], who)
            st4, _js, _ = await asgi("GET", "/api/me", cookie=ck)
            bump(res["pairs"], f"sign-in {st1}, me {st2}{'' if mine or st2 != 200 else ' (NOT this account and its role)'}, "
                               f"sign-out {st3}, me afterwards {st4}")

    async def nobody():
        while not done.is_set():
            for path in NEED_SIGN_IN:
                st, _js, _ = await asgi("GET", path)
                bump(res["anon"], f"{path} {st}")
            await asyncio.sleep(0.001)

    async def watcher():
        _st, _js, ck = await asgi("POST", "/api/login", {"username": S_WATCH, "password": _pw(S_WATCH)})
        while not done.is_set():
            st, js, _ = await asgi("POST", "/api/branding", {"company": "Viewer Co"}, cookie=ck)
            bump(res["viewer"], f"a write {st}")
            st, js, _ = await asgi("GET", "/api/me", cookie=ck)
            bump(res["viewer"], f"me {st} {(js or {}).get('role')}")
            await asyncio.sleep(0.001)

    async def boss():
        _st, _js, ck = await asgi("POST", "/api/login", {"username": S_BOSS, "password": _pw(S_BOSS)})
        for i in range(api_n):
            name = f"api-{i}@storm.example"
            role = ROLES3[i % 3]
            a, _js, _ = await asgi("POST", "/api/users", {"username": name, "role": role}, cookie=ck)
            nxt = ROLES3[(i + 1) % 3]
            b, _js, _ = await asgi("POST", f"/api/users/{name}/role", {"role": nxt}, cookie=ck)
            c, _js, _ = await asgi("POST", f"/api/users/{name}/staff", {"staff": True}, cookie=ck)
            last = ROLES3[(i + 2) % 3]
            d, _js, _ = await asgi("POST", f"/api/users/{name}/role", {"role": last}, cookie=ck)
            e, _js, _ = await asgi("POST", f"/api/users/{name}/reset", None, cookie=ck)
            bump(res["api"], f"add {a}, role {b}, staff {c}, role {d}, reset {e}")
            if (a, b, c, d, e) != (200,) * 5:
                res["unsure"].append(name)
            elif i % 2:
                f, _js, _ = await asgi("DELETE", f"/api/users/{name}", None, cookie=ck)
                bump(res["api"], f"remove {f}")
                (res["removed"] if f == 200 else res["unsure"]).append(name)
            else:
                res["kept"][name] = last

    async def writers_done():
        while any(t.is_alive() for t in threads) or any(k.poll() is None for k in kids):
            await asyncio.sleep(0.05)

    front = [asyncio.create_task(person(w, r)) for w, r in S_PEOPLE.items()] + \
            [asyncio.create_task(boss()), asyncio.create_task(writers_done())]
    back = [asyncio.create_task(nobody()) for _ in range(2)] + [asyncio.create_task(watcher())]
    await asyncio.gather(*front)
    done.set()
    await asyncio.gather(*back)
    return res


def s_the_storm(n_thr: int = 3, n_proc: int = 2, rounds: int = 60, pairs: int = 15, api_n: int = 12):
    quick_hash(_users)
    try:
        P.unlink()
    except OSError:
        pass
    _users.save(_storm_roster())
    _users.load()
    fixed = dict(S_SHARED, **S_PEOPLE, **{S_BOSS: "admin", S_WATCH: "viewer"})
    d0 = on_disk()
    sv0 = {n: int(d0[n].get("sv", 0)) for n in fixed}
    for f in list(_JOBS.glob("ready-*")) + [_JOBS / "GO", _JOBS / "STOP"]:
        if f.exists():
            f.unlink()
    errs = [open(_JOBS / f"storm-child-{i}.err", "w") for i in range(n_proc + 1)]
    kids = [subprocess.Popen([sys.executable, THIS, "--storm", str(_JOBS), "w", f"p{i}", str(rounds)],
                             stdout=subprocess.PIPE, stderr=errs[i], text=True, cwd=str(ROOT), env=CHILD_ENV)
            for i in range(n_proc)]
    rkid = subprocess.Popen([sys.executable, THIS, "--storm", str(_JOBS), "r", "reader", "0"],
                            stdout=subprocess.PIPE, stderr=errs[n_proc], text=True, cwd=str(ROOT), env=CHILD_ENV)
    end = time.time() + 120
    while len(list(_JOBS.glob("ready-*"))) < n_proc + 1 and time.time() < end:
        time.sleep(0.02)
    results, reads = [None] * n_thr, [dict(), dict()]
    stop = threading.Event()

    def thr(i):
        _wait_go(_JOBS)
        results[i] = storm_work(_users, f"t{i}", rounds)

    th = [threading.Thread(target=thr, args=(i,)) for i in range(n_thr)]
    rt = [threading.Thread(target=storm_read, args=(_users, stop.is_set, reads[i], 0.0005)) for i in range(2)]
    [t.start() for t in th + rt]
    t0 = time.time()
    (_JOBS / "GO").write_text("go")
    web = asyncio.run(_swarm(th, kids, pairs, api_n))
    [t.join() for t in th]
    outs, died = [r for r in results if r], []
    for i, k in enumerate(kids):
        o, _e = k.communicate(timeout=600)
        line = next((ln for ln in o.splitlines() if ln.startswith("RESULT ")), None)
        if line:
            outs.append(json.loads(line[7:]))
        else:
            died.append(f"worker process {i} gave no result")
    took = time.time() - t0
    stop.set()
    (_JOBS / "STOP").write_text("stop")
    [t.join() for t in rt]
    o, _e = rkid.communicate(timeout=120)
    line = next((ln for ln in o.splitlines() if ln.startswith("RESULT ")), None)
    if line:
        reads.append(json.loads(line[7:]))
    else:
        died.append("the reading process gave no result")
    [f.close() for f in errs]
    if len(outs) != n_thr + n_proc:
        died.append(f"{n_thr + n_proc - len(outs)} worker(s) gave no result")

    # ---- the sums
    disk = on_disk() or {}
    raised, bumps, kept, removed, unsure, ops = {}, {}, dict(web["kept"]), list(web["removed"]), list(web["unsure"]), 0
    for w in outs:
        ops += w["ops"]
        for k, v in w["raised"].items():
            bump(raised, k, v)
        for k, v in w["bumps"].items():
            bump(bumps, k, v)
        kept.update(w["kept"])
        removed += w["removed"]
        unsure += w["unsure"]
    rd = {}
    for r in reads:
        for k, v in r.items():
            bump(rd, k, v)
    n_pairs = sum(web["pairs"].values())
    good_pair = "sign-in 200, me 200, sign-out 200, me afterwards 401"
    anon_open = {k: v for k, v in web["anon"].items() if str(k.rsplit(" ", 1)[1]).startswith("2")}
    anon_other = {k: v for k, v in web["anon"].items()
                  if k.rsplit(" ", 1)[1] != ("303" if k.startswith("/ ") else "401")}
    lost = [n for n in fixed if n not in disk] + [n for n in kept if n not in disk]
    back = [n for n in removed if n in disk]
    want_role = dict({n: (r or "admin") for n, r in fixed.items()}, **kept)
    lost_roles = {n: (w, role_in(disk, n)) for n, w in want_role.items() if n in disk and role_in(disk, n) != w}
    out_of_step = [n for n in want_role if n in disk and not in_step(disk[n])]
    old_rewritten = "role" in disk.get("s-old@storm.example", {"role": 1})
    short = {n: (bumps.get(n, 0), int(disk.get(n, {}).get("sv", 0)) - sv0[n]) for n in S_SHARED
             if n in disk and bumps.get(n, 0) != int(disk[n].get("sv", 0)) - sv0[n]}
    short.update({n: (web["signouts"].get(n, 0), int(disk.get(n, {}).get("sv", 0)) - sv0[n]) for n in S_PEOPLE
                  if n in disk and web["signouts"].get(n, 0) != int(disk[n].get("sv", 0)) - sv0[n]})
    try:
        agrees = set(_users.load()) == set(disk)
    except Exception as e:                      # noqa: BLE001
        agrees = type(e).__name__
    print(f"    the storm: {n_thr} threads + {n_proc} processes x {rounds} rounds, {n_pairs} sign-in / sign-out pairs, "
          f"{api_n} accounts through the routes, in {took:.1f} s: {ops + sum(web['api'].values())} changes by the "
          f"workers and the accounts routes, {sum(bumps.values()) + sum(web['signouts'].values())} sign-outs, "
          f"{len(kept)} accounts kept, {len(removed)} removed, {rd.get('reads', 0)} reads, "
          f"{sum(web['anon'].values())} requests with no cookie, {sum(web['viewer'].values())} by the viewer; "
          f"{len(disk)} accounts in the file afterwards")
    check(not raised and not died, "s: no change raised and none was answered 'no such account' for an account that "
                                   "is there; no worker died", (raised, died))
    check(rd.get("reads", 0) > 0 and set(rd) == {"reads"},
          "s: no read answered 'no accounts', none without a shared account, none raised", rd)
    check(n_pairs == len(S_PEOPLE) * pairs and set(web["pairs"]) == {good_pair},
          f"s: every sign-in / sign-out pair ({n_pairs}) answered 200, 200 with its own name and role, 200, and 401 "
          "for the cookie just signed out", web["pairs"])
    check(sum(web["anon"].values()) > 0 and not anon_open,
          "s: UNAUTHENTICATED ANSWERS (2xx to a request with no cookie on a route that needs a sign-in): 0", anon_open)
    check(not anon_other, "s: ... each was 401 (303 to the sign-in page for the app page): no 5xx either", anon_other)
    check(sum(web["viewer"].values()) > 0 and set(web["viewer"]) == {"a write 403", "me 200 viewer"},
          "s: the viewer was read-only all the way through: every write 403, every read its own role", web["viewer"])
    check(set(web["api"]) <= {"add 200, role 200, staff 200, role 200, reset 200", "remove 200"} and sum(web["api"].values()) > 0,
          "s: the accounts routes answered 200 every time (add with a role, role, staff, role, reset, remove)", web["api"])
    check(not lost and not unsure, f"s: LOST ACCOUNTS: 0 of {len(fixed)} that were there and {len(kept)} made and kept",
          (lost, unsure))
    check(not back, f"s: accounts BACK after they were removed: 0 of {len(removed)}", back)
    check(not lost_roles and not out_of_step and not old_rewritten,
          f"s: LOST ROLES: 0 of {len(want_role)} - each account has the last role its one changer set (the others' "
          "sign-outs, password changes and saves kept it), staff in step, and the record from before roles was not "
          "given one", (lost_roles, out_of_step, old_rewritten))
    check(not short, "s: every sign-out is in the file: each counter moved by exactly its own sign-outs "
                     "(said, in the file)", short)
    check(left_beside() == [] and agrees is True,
          "s: no temp file is left beside the account file, and this process reads what the file holds",
          (left_beside(), agrees))


for fn in (r1_a_change_is_refused_not_made_from_memory, r2_a_write_that_fails_leaves_memory_as_it_was,
           r3_a_change_is_read_inside_the_lock, r4_the_command_line_with_roles, r5_the_role_of_a_name_in_a_given_roster,
           g1_the_role_routes_with_a_roster_in_memory, g2_started_on_a_file_it_can_not_read,
           g3_a_request_is_judged_on_one_read_of_the_roster, s_the_storm):
    run(fn)

print(f"\n{checks} checks, {fails} failed")
print(f"{'PASS' if not fails else 'FAIL'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
