"""THE ACCOUNT FILE: A FAILED READ IS NEVER "NO ACCOUNTS" (2026-10-04; CRITIC-merge-1, the coordinator's F1 and F2).

WHAT WAS FOUND. takeoff_engine/users.py read a users.json it could not read - held by another program for an
instant (Windows does that while the file is replaced), cut short, damaged - as "nobody has an account":
  F1  users.save() then forgot the e-mail records (bid address, keyword rules, messages, held plans) of EVERY
      account; the mail reader did the same for the account whose message it was reading; the mail door dropped a
      message for that account as "no such address".
  F2  users.load() KEPT that answer, so a server with accounts answered with no sign-in at all until a restart; the
      next account made was written over an empty roster (every other account gone); and two saves at once lost
      each other's change (one fixed temp name, no lock).

THE RULES NOW, one check each (most fail on be46b84):
  A  the file itself: no file is still "no accounts"; a file that is there and can not be read is answered from
     the last roster read; a change is refused rather than made from a guess; a whole-roster save puts a damaged
     file right; only an account KNOWN to be leaving is forgotten (a removal still is)
  B  the server with a roster in memory: nobody unsigned gets in, a session and a sign-in go on working, the log
     says why
  C  a server STARTED on a file it can not read (its own process): every route that needs a sign-in answers 503,
     the sign-in and the mail door too - never an open answer, never the site password
  D  writes: threads and processes changing the file at once lose nothing; a writer waits for another process
     and gives up in words
  E  bids by e-mail while the file can not be read: the door still takes the message, the reader forgets nobody,
     and with no roster in memory the message stays spooled

Every name, address and word here is made up. Every request goes through the real middleware with no server
listening.

    python tests/test_account_file.py
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

ALICE, BOB, CAROL, STAFF = "alice@builder.example", "bob@rival.example", "carol@third.example", "staff@core.example"
PW = {ALICE: "orchid-lantern-4471", BOB: "basalt-meadow-9083", CAROL: "walnut-harbor-3318",
      STAFF: "granite-sparrow-2210"}
SITE_PW = "shared-site-password-1"
MAIL_ENV = ("INBOUND_HOOK_USER", "INBOUND_HOOK_PASS", "INBOUND_ADDRESS")
WINDOWS = os.name == "nt"


# ---- the parts that run in a process of their own ------------------------------------------------------------------
def _status(H, server, method, path, body=None, **kw):
    try:
        return H.call(server, method, path, body, **kw)[0]
    except Exception as e:                      # noqa: BLE001 - be46b84 raises out of the app on a sign-in here
        return f"raised {type(e).__name__}"


def child_fresh(store: str, mode: str, cookie: str, tag: str) -> None:
    """A server started on the store as it is: what it answers before it has ever read the account file."""
    os.environ["TAKEOFF_JOBS_DIR"] = store
    os.environ["TAKEOFF_LEDGER_DIR"] = store + "_lg"
    for k in ("TAKEOFF_HOSTED", "TAKEOFF_UI_PASSWORD"):
        os.environ.pop(k, None)
    if mode == "password":
        os.environ.update({"TAKEOFF_HOSTED": "1", "TAKEOFF_UI_PASSWORD": SITE_PW})
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        import _inbound_harness as H
        os.environ.update({"INBOUND_HOOK_USER": H.HOOK_USER, "INBOUND_HOOK_PASS": H.HOOK_PASS,
                           "INBOUND_ADDRESS": H.BASE_ADDRESS})
        import server
        server._classify_async = lambda *a, **k: None
        users_file = Path(store) / "users.json"
        before = users_file.read_bytes()
        out = {"the sign-in page": _status(H, server, "GET", "/login"),
               "me, no cookie": _status(H, server, "GET", "/api/me"),
               "me, a cookie": _status(H, server, "GET", "/api/me", cookie=cookie),
               "projects, no cookie": _status(H, server, "GET", "/api/projects"),
               "the app page, no cookie": _status(H, server, "GET", "/"),
               "sign-in, her password": _status(H, server, "POST", "/api/login",
                                                {"username": ALICE, "password": PW[ALICE]}),
               "sign-in, the site password": _status(H, server, "POST", "/api/login",
                                                     {"username": "anyone", "password": SITE_PW}),
               "the mail door": _status(H, server, "POST", "/api/inbound/postmark",
                                        H.payload(tag, [("plan.pdf", H.drawing_pdf(1))]), auth=H.basic(), marker=False)}
        out["file unchanged"] = users_file.read_bytes() == before
        # the file is put right while this process runs: it reads again, with nothing restarted
        users_file.write_bytes((Path(store) / "good.json").read_bytes())
        out["after: me, a cookie"] = _status(H, server, "GET", "/api/me", cookie=cookie)
        out["after: me, no cookie"] = _status(H, server, "GET", "/api/me")
    out["said"] = [ln for ln in err.getvalue().splitlines() if ln.startswith("[users]")][:4]
    print("RESULT " + json.dumps(out))


def child_bump(store: str, name: str, n: int) -> None:
    os.environ["TAKEOFF_JOBS_DIR"] = store
    from takeoff_engine import users
    out = {"ok": 0, "raised": {}}
    (Path(store) / f"ready-{os.getpid()}").write_text("")
    while not (Path(store) / "GO").exists():
        time.sleep(0.005)
    for _ in range(n):
        try:
            if users.bump_session(name):
                out["ok"] += 1
        except Exception as e:                  # noqa: BLE001
            out["raised"][type(e).__name__] = out["raised"].get(type(e).__name__, 0) + 1
    print("RESULT " + json.dumps(out))


def child_hold(store: str, seconds: float) -> None:
    os.environ["TAKEOFF_JOBS_DIR"] = store
    from takeoff_engine import users
    if not hasattr(users, "_one_writer"):
        print("no-lock", flush=True)
        return
    with users._one_writer():
        print("held", flush=True)
        time.sleep(seconds)


if len(sys.argv) > 1 and sys.argv[1] == "--fresh":
    child_fresh(*sys.argv[2:6])
    sys.exit(0)
if len(sys.argv) > 1 and sys.argv[1] == "--bump":
    child_bump(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    sys.exit(0)
if len(sys.argv) > 1 and sys.argv[1] == "--hold":
    child_hold(sys.argv[2], float(sys.argv[3]))
    sys.exit(0)


# ---- this process: a store of its own, a laptop (no hosted flag, no site password) with four accounts ---------------
_JOBS = Path(tempfile.mkdtemp(prefix="acct_file_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(_JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="acct_file_lg_")
for _k in ("TAKEOFF_HOSTED", "TAKEOFF_UI_PASSWORD") + MAIL_ENV:
    os.environ.pop(_k, None)

import _inbound_harness as H  # noqa: E402
import server  # noqa: E402
from takeoff_engine import inbound as I  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

os.environ.update({"INBOUND_HOOK_USER": H.HOOK_USER, "INBOUND_HOOK_PASS": H.HOOK_PASS, "INBOUND_ADDRESS": H.BASE_ADDRESS})
server._classify_async = lambda *a, **k: None
server._INBOUND_KINDS_WAIT_S = 0
server._UI_PASSWORD = None
server.HOSTED = False
H.install_fake_children(server)

P = _users.store_path()
NAMES = (ALICE, BOB, CAROL, STAFF)
Unreadable = getattr(_users, "Unreadable", None)
Busy = getattr(_users, "Busy", None)
CK = {}
fails = 0
checks = 0


def check(ok, msg, got=None):
    global fails, checks
    checks += 1
    print(("ok: " if ok else "FAIL: ") + msg + ("" if ok or got is None else f"  (got {str(got)[:400]})"))
    if not ok:
        fails += 1


def run(fn):
    try:
        fn()
    except Exception as e:                      # noqa: BLE001 - on be46b84 some tests land here
        check(False, f"{fn.__name__}: raised {type(e).__name__}: {str(e)[:200]}")
    finally:
        put_right()


def roster() -> dict:
    return {n: dict(_users.hash_password(PW[n], iterations=1000), **({"staff": True} if n == STAFF else {}))
            for n in NAMES}


def on_disk():
    try:
        d = json.loads(P.read_bytes())
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def put_right() -> None:
    """The four accounts are in the file, each with a cookie, a bid address, one keyword and one message."""
    global CK
    d = on_disk()
    if d is None or any(n not in d for n in NAMES):
        try:
            P.unlink()                          # written from nothing: the file may hold anything by now
        except OSError:
            pass
        _users.save(roster())
    _users.load()
    CK = {n: server._sign_session(n) for n in NAMES}
    for n in (ALICE, BOB, CAROL):
        if I.tag_of(n) is None:
            I.new_address(n)
        if not I.rules_of(n)["keywords"]:
            I.save_rules(n, ["quartzline"], [], None)
        if I.count_entries(n) == 0:
            I.save_entry(n, I.new_entry(n, {"id": f"first-{n}-{time.time()}", "from": "pat@sender.example",
                                            "subject": "made-up", "attachments": [], "attached": 0}, time.time()))


def mail(n) -> tuple:
    return bool(I.tag_of(n)), I.count_entries(n) > 0, len(I.rules_of(n)["keywords"])


def all_mail() -> dict:
    return {n.split("@")[0]: mail(n) for n in (ALICE, BOB, CAROL)}


FULL = {n.split("@")[0]: (True, True, 1) for n in (ALICE, BOB, CAROL)}


def cut() -> bytes:
    """The file cut short, as a write that stopped half way leaves it. -> its bytes before."""
    good = P.read_bytes()
    P.write_bytes(good[:len(good) // 2])
    return good


@contextlib.contextmanager
def read_refused():
    """An open of the account file FOR READING is refused (PermissionError) and every other open goes through:
    what a reader meets on Windows in the instant another thread or process replaces the file (measured: one
    writer, three readers, 2,000 replaces -> 77 to 208 opens refused). The write and the rename are not touched,
    so this is the case the review found: the read fails, the save goes through."""
    import builtins
    real = builtins.open

    def guarded(file, mode="r", *a, **k):
        if not isinstance(file, int) and "r" in mode and "+" not in mode and Path(os.fspath(file)) == P:
            raise PermissionError(13, "Permission denied")
        return real(file, mode, *a, **k)

    builtins.open = io.open = guarded           # pathlib reads through io.open, the module's own code through open
    try:
        yield
    finally:
        builtins.open = io.open = real


@contextlib.contextmanager
def held():
    """Windows only, and real: another program has the file open and shares nothing, so neither an open for
    reading nor a replace is let through."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                wintypes.DWORD, ctypes.c_void_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.CreateFileW(str(P), 0x80000000, 0, None, 3, 0x80, None)
    if h is None or h == ctypes.c_void_p(-1).value:
        raise OSError(f"could not hold the file (error {ctypes.get_last_error()})")
    try:
        yield
    finally:
        k32.CloseHandle(h)


def call(method, path, body=None, cookie=None, **kw):
    return H.call(server, method, path, body, cookie=cookie, **kw)


def me(cookie=None):
    return call("GET", "/api/me", cookie=cookie)[0]


@contextlib.contextmanager
def stderr_lines():
    buf, out = io.StringIO(), []
    with contextlib.redirect_stderr(buf):
        yield out
    out += [ln for ln in buf.getvalue().splitlines() if ln.startswith("[users]")]


def forget_what_was_read() -> None:
    """This process's memory of the roster, emptied: the state a server is in when it has just been started."""
    _users._CACHE = (None, None)
    if hasattr(_users, "_GOOD"):
        _users._GOOD = (None, None)


def left_beside() -> list:
    return sorted(f.name for f in _JOBS.iterdir() if f.name.startswith("users.json") and f.name not in ("users.json", "users.json.lock"))


# =====================================================================================================================
def a1_no_file_is_no_accounts():
    P.unlink()
    check(_users.load() == {} and me() == 200 and call("GET", "/api/me")[1].get("accounts") is False,
          "a1: NO account file is still 'no accounts': a laptop answers with no sign-in, as before")
    _users.save(roster())
    check(set(_users.load()) == set(NAMES) and me() == 401, "a1: the file made again: its accounts, and a sign-in is needed again")


def a2_a_damaged_file_is_answered_from_the_last_roster():
    good = cut()
    with stderr_lines() as said:
        got = _users.load()
    check(set(got) == set(NAMES), "a2: a file cut short: load() answers the last roster read, never {}", sorted(got))
    check(len(said) == 1 and "can not be read" in said[0] and "not readable JSON" in said[0],
          "a2: ... and one line on stderr says why", said)
    with stderr_lines() as again:
        for _ in range(50):
            _users.load()
    check(again == [], "a2: ... one line, not one a request", again[:3])
    for kind, raw in (("empty", b""), ("not a list of accounts", b'["a", "b"]')):
        P.write_bytes(raw)
        got = _users.load()
        check(set(got) == set(NAMES), f"a2: a file that is {kind}: the last roster, never {{}}", sorted(got))
    P.write_bytes(good)
    with stderr_lines() as said:
        got = _users.load()
    check(set(got) == set(NAMES) and any("reads again" in ln for ln in said), "a2: put right: it reads again, and says so", said)


def a3_a_change_is_refused_not_made_from_a_guess():
    cut()
    damaged = P.read_bytes()
    try:
        _users.add_user("new@builder.example", "willow-anchor-9917")
        made = "made"
    except Exception as e:                      # noqa: BLE001
        made = type(e).__name__
    check(made == "Unreadable" and P.read_bytes() == damaged,
          "a3: an account made while the file can not be read is REFUSED and the file is not written over "
          "(it was written over with that one account: every other account gone)", (made, sorted(on_disk() or [])))
    P.write_bytes(damaged)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    out = subprocess.run([sys.executable, "-m", "takeoff_engine.users", "add", "cli@builder.example", "--password",
                          "willow-anchor-9917"], capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=120)
    check(out.returncode == 1 and "refused" in out.stdout and P.read_bytes() == damaged,
          "a3: the command line the same: refused in words, exit 1, the file as it was",
          (out.returncode, out.stdout[-200:], out.stderr[-200:]))


def a4_a_whole_roster_save_puts_it_right_and_forgets_nobody():
    cut()
    _users.save(roster())
    check(set(on_disk() or {}) == set(NAMES), "a4: a whole roster saved over a damaged file puts it right", on_disk())
    check(all_mail() == FULL, "a4: ... and NOBODY's bid address, rules or messages are forgotten (F1: all were)", all_mail())


def a5_a_save_whose_read_is_refused_forgets_nobody():
    good = dict(_users.load())
    with read_refused():
        try:
            P.read_bytes()
            refused = False
        except PermissionError:
            refused = True
        _users.save(good)
    check(refused, "a5: (an open of the file for reading is refused, as during a replace on Windows)")
    check(set(on_disk() or {}) == set(NAMES) and all_mail() == FULL,
          "a5: a save whose own read of the file is refused forgets NOBODY's e-mail records (F1: all were)", all_mail())


def a6_a_sign_out_whose_read_is_refused_changes_nothing():
    sv = {n: _users.session_version(n) for n in NAMES}
    before = P.read_bytes()
    with read_refused():
        st, j = call("POST", "/api/logout", cookie=CK[ALICE])
    check(st == 503 and "can not read its accounts" in str(j.get("detail")) and P.read_bytes() == before,
          "a6: a sign-out while the file can not be read is told so (503), not answered as done", (st, j))
    check(all_mail() == FULL, "a6: ... and nobody's e-mail records are forgotten (F1: one sign-out wiped all four)", all_mail())
    check({n: _users.session_version(n) for n in NAMES} == sv and me(CK[ALICE]) == 200 and left_beside() == [],
          "a6: ... nothing was written: every counter as it was, her session still hers, no temp file left", left_beside())


def a7_only_a_known_leaver_is_forgotten():
    _users.remove_user(CAROL)
    got = all_mail()
    check(got["carol"] == (False, False, 0) and got["alice"] == got["bob"] == (True, True, 1),
          "a7: an account that IS removed keeps nothing in the e-mail store; the others keep theirs", got)
    put_right()
    good = dict(_users.load())
    P.unlink()                                  # the file moved away for a moment, then the roster written back
    _users.save(good)
    check(set(on_disk() or {}) == set(NAMES) and all_mail() == FULL,
          "a7: a roster written back over a MISSING file forgets nobody: no account was seen leaving", all_mail())


def a8_a_replaced_file_is_another_file():
    _users.load()
    st = P.stat()
    twin = ALICE[:-1] + "X"                     # a name of the same length: the file's size does not change
    tmp = P.with_name("users.json.swap")
    # a file of the SAME size and the SAME time with another account in it, as another process's save can leave
    # within one tick of the clock (two sign-outs: the counter moves, the size does not)
    tmp.write_bytes(P.read_bytes().replace(ALICE.encode(), twin.encode()))
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(tmp, P)
    now = P.stat()
    got = _users.load()
    check(now.st_size == st.st_size and now.st_mtime_ns == st.st_mtime_ns,
          "a8: (a file of the same size and the same time, with another account in it, replaced the file)",
          (now.st_size, st.st_size, now.st_mtime_ns, st.st_mtime_ns))
    check(ALICE not in got and twin in got,
          "a8: load() reads the file that replaced it, not the roster it remembered for that time and size", sorted(got))


def a9_a_temp_file_of_a_writer_that_died_is_cleared():
    dead = P.with_name("users.json.0badc0de.tmp")
    dead.write_text("{}")
    _users.bump_session(BOB)
    check(not dead.exists() and left_beside() == [],
          "a9: a temp file left by a writer that died (it holds the hashes) is gone after the next save", left_beside())
    if not WINDOWS:
        check((P.stat().st_mode & 0o777) == 0o600, "a9: the account file is readable by its owner only", oct(P.stat().st_mode))


# =====================================================================================================================
def b1_nobody_unsigned_gets_in():
    for kind in ("cut short", "empty"):
        put_right()
        good = P.read_bytes()
        P.write_bytes(good[:len(good) // 2] if kind == "cut short" else b"")
        got = {"me, no cookie": me(), "projects, no cookie": call("GET", "/api/projects")[0],
               "the app page, no cookie": call("GET", "/")[0], "me, her cookie": me(CK[ALICE]),
               "sign-in, her password": call("POST", "/api/login", {"username": ALICE, "password": PW[ALICE]})[0],
               "sign-in, a wrong password": call("POST", "/api/login", {"username": ALICE, "password": "not-her-word-1"})[0],
               "accounts, as staff": call("GET", "/api/users", cookie=CK[STAFF])[0],
               "accounts, as her": call("GET", "/api/users", cookie=CK[ALICE])[0]}
        want = {"me, no cookie": 401, "projects, no cookie": 401, "the app page, no cookie": 303, "me, her cookie": 200,
                "sign-in, her password": 200, "sign-in, a wrong password": 401, "accounts, as staff": 200,
                "accounts, as her": 404}
        check(got == want, f"b1: the file {kind}, a roster in memory: nobody unsigned gets in; a session, a sign-in "
                           "and staff rights go on as they were", {k: v for k, v in got.items() if v != want[k]})
        P.write_bytes(good)


def b2_the_site_password_is_no_way_in():
    put_right()
    good = cut()
    server._UI_PASSWORD = SITE_PW
    try:
        st, j = call("POST", "/api/login", {"username": "anyone", "password": SITE_PW})
    finally:
        server._UI_PASSWORD = None
    check(st == 401, "b2: with a site password set and the file cut short, the site password signs NOBODY in "
                     "(it did: as nobody, who sees every document)", (st, j))
    P.write_bytes(good)


def b3_a_removed_file_after_accounts():
    put_right()
    P.unlink()
    check(_users.load() == {} and me() == 200, "b3: the file REMOVED after accounts existed: no accounts, at once (a "
                                                "missing file is still that)")


# =====================================================================================================================
def c_started_on_a_file_it_can_not_read():
    put_right()
    tag = I.tag_of(ALICE)
    for mode in ("laptop", "password"):
        store = Path(tempfile.mkdtemp(prefix=f"acct_fresh_{mode}_"))
        for f in ("users.json", "session.key"):
            (store / f).write_bytes((_JOBS / f).read_bytes())
        if (_JOBS / "inbound").is_dir():
            import shutil
            shutil.copytree(_JOBS / "inbound", store / "inbound")
        good = (store / "users.json").read_bytes()
        (store / "good.json").write_bytes(good)
        (store / "users.json").write_bytes(good[:len(good) // 2])
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        out = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--fresh", str(store), mode, CK[ALICE], tag],
                             capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=300)
        line = next((ln for ln in out.stdout.splitlines() if ln.startswith("RESULT ")), None)
        if line is None:
            check(False, f"c ({mode}): the started server gave no result", (out.returncode, out.stderr[-300:]))
            continue
        got = json.loads(line[7:])
        closed = {k: got[k] for k in ("me, no cookie", "me, a cookie", "projects, no cookie", "the app page, no cookie",
                                      "sign-in, her password", "sign-in, the site password", "the mail door")}
        where = "no hosted flag, no site password" if mode == "laptop" else "hosted, a site password set"
        check(all(v == 503 for v in closed.values()),
              f"c ({where}): started on a file cut short, EVERY route that needs the accounts answers 503 - the "
              "session, the sign-in, the site password, the mail door", {k: v for k, v in closed.items() if v != 503})
        check(got["the sign-in page"] == 200 and got["file unchanged"],
              f"c ({where}): the sign-in page still shows, and the file is not written over", got)
        check(len(got["said"]) >= 1 and "can not be read" in got["said"][0] and "nobody can be signed in" in got["said"][0],
              f"c ({where}): the log says why", got["said"])
        check(got["after: me, a cookie"] == 200 and got["after: me, no cookie"] == 401,
              f"c ({where}): the file put right: the same process signs people in again, nothing restarted",
              (got["after: me, a cookie"], got["after: me, no cookie"]))


# =====================================================================================================================
def d1_threads_at_once_lose_nothing():
    put_right()
    sv0 = {n: _users.session_version(n) for n in NAMES}
    done, raised, short = {}, [], []
    stop = threading.Event()

    def signs_out(k):
        for r in range(30):
            n = (ALICE, BOB)[(k + r) % 2]
            try:
                if _users.bump_session(n):
                    done[(k, r)] = n
            except Exception as e:              # noqa: BLE001
                raised.append(type(e).__name__)

    def makes(k):
        for r in range(4):
            n = f"temp-{k}-{r}@builder.example"
            try:
                _users.add_user(n, "made-up-word-1")
                done[("made", k, r)] = n
                if r % 2:
                    _users.remove_user(n)
                    done[("gone", k, r)] = n
            except Exception as e:              # noqa: BLE001
                raised.append(type(e).__name__)

    def reads():
        while not stop.is_set():
            try:
                got = _users.load()
                if not all(n in got for n in NAMES):
                    short.append(len(got))
            except Exception as e:              # noqa: BLE001
                raised.append("load " + type(e).__name__)

    th = [threading.Thread(target=signs_out, args=(k,)) for k in range(4)] + \
         [threading.Thread(target=makes, args=(k,)) for k in range(2)]
    rd = [threading.Thread(target=reads) for _ in range(2)]
    [t.start() for t in th + rd]
    [t.join() for t in th]
    stop.set()
    [t.join() for t in rd]
    d = on_disk() or {}
    n_out = {n: sum(1 for k, v in done.items() if len(k) == 2 and v == n) for n in (ALICE, BOB)}
    made = {v for k, v in done.items() if k[0] == "made"}
    gone = {v for k, v in done.items() if k[0] == "gone"}
    check(raised == [], "d1: 4 threads signing out and 2 making and removing accounts at once: no change raised",
          {k: raised.count(k) for k in set(raised)})
    check(short == [], "d1: ... no read answered without one of the four accounts", (len(short), short[:5]))
    check(all(n in d for n in NAMES) and sum(n_out.values()) == 120
          and all(int(d.get(n, {}).get("sv", 0)) - sv0[n] == n_out[n] for n in (ALICE, BOB)),
          "d1: ... every one of the 120 sign-outs is in the file (each counter moved by exactly its own)",
          {n: (int(d.get(n, {}).get("sv", 0)) - sv0[n], n_out[n]) for n in (ALICE, BOB)})
    check(len(made) == 8 and all(n in d for n in made - gone) and not any(n in d for n in gone),
          "d1: ... every account made is there and every one removed is gone", (len(made), sorted(set(d) - set(NAMES))))
    check(left_beside() == [] and all_mail() == FULL, "d1: ... no temp file left, nobody's e-mail records touched",
          (left_beside(), all_mail()))
    _users.save({n: v for n, v in _users.load().items() if n in NAMES})


def d2_processes_at_once_lose_nothing():
    put_right()
    sv0 = _users.session_version(ALICE)
    try:
        (_JOBS / "GO").unlink()
    except OSError:
        pass
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    kids = [subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--bump", str(_JOBS), ALICE, "40"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT), env=env)
            for _ in range(2)]
    H.wait_until(lambda: len(list(_JOBS.glob("ready-*"))) >= 2, 120)   # both are up: the three start together
    (_JOBS / "GO").write_text("go")
    mine, raised = 0, {}
    for _ in range(40):
        try:
            mine += 1 if _users.bump_session(ALICE) else 0
        except Exception as e:                  # noqa: BLE001
            raised[type(e).__name__] = raised.get(type(e).__name__, 0) + 1
    theirs = 0
    for k in kids:
        o, e = k.communicate(timeout=300)
        r = json.loads(next(ln for ln in o.splitlines() if ln.startswith("RESULT "))[7:])
        theirs += r["ok"]
        for kk, v in r["raised"].items():
            raised[kk] = raised.get(kk, 0) + v
    for f in list(_JOBS.glob("ready-*")) + [_JOBS / "GO"]:
        f.unlink()
    d = on_disk() or {}
    check(raised == {} and mine + theirs == 120, "d2: 3 PROCESSES signing one account out 40 times each: no change raised",
          (raised, mine + theirs))
    check(set(d) == set(NAMES) and int(d.get(ALICE, {}).get("sv", 0)) - sv0 == mine + theirs,
          "d2: ... no account lost and every sign-out is in the file (the counter moved by exactly 120)",
          (sorted(d), int(d.get(ALICE, {}).get("sv", 0)) - sv0, mine + theirs))
    check(set(_users.load()) == set(NAMES) and _users.session_version(ALICE) == int(d.get(ALICE, {}).get("sv", 0))
          and left_beside() == [],
          "d2: ... this process reads what the others wrote, and no temp file is left", left_beside())


def _holder(seconds: float):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    k = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--hold", str(_JOBS), str(seconds)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT), env=env)
    return k, k.stdout.readline().strip()


def d3_a_writer_waits_for_another_process():
    put_right()
    k, word = _holder(1.5)
    t0 = time.time()
    ok = word == "held" and _users.bump_session(BOB)
    took = time.time() - t0
    k.communicate(timeout=60)
    check(bool(ok) and took >= 0.8, "d3: while another process is writing the accounts, a change here WAITS for it "
                                    "and is then made", (word, round(took, 2)))
    wait = getattr(_users, "LOCK_WAIT_S", None)
    k, word = _holder(3.0)
    before = P.read_bytes()
    try:
        _users.LOCK_WAIT_S = 0.3
        try:
            _users.bump_session(BOB)
            got = "made"
        except Exception as e:                  # noqa: BLE001
            got = type(e).__name__
        st, j = call("POST", "/api/logout", cookie=CK[CAROL])
    finally:
        _users.LOCK_WAIT_S = wait
    check(word == "held" and got == "Busy" and P.read_bytes() == before,
          "d3: a writer that waits too long gives up in words (Busy) and writes nothing", (word, got))
    check(st == 503 and "being saved by another program" in str(j.get("detail")),
          "d3: ... and the page is told so (503), not handed a 500", (st, j))
    k.communicate(timeout=60)
    check(me(CK[CAROL]) == 200 and call("POST", "/api/logout", cookie=CK[CAROL])[0] == 200 and me(CK[CAROL]) == 401,
          "d3: ... her session was not ended by the sign-out that was refused; the next one ends it")


def d4_a_file_nothing_can_replace():
    if not WINDOWS:
        check(True, "d4: (not Windows: a rename over an open file is never refused here)")
        return
    put_right()
    good = dict(_users.load())
    before = P.read_bytes()
    wait = getattr(_users, "_REPLACE_WAIT_S", None)
    try:
        _users._REPLACE_WAIT_S = 0.3
        with held():
            try:
                _users.save(good)
                got = "saved"
            except Exception as e:              # noqa: BLE001
                got = type(e).__name__
    finally:
        _users._REPLACE_WAIT_S = wait
    check(got == "Busy" and P.read_bytes() == before and left_beside() == [] and all_mail() == FULL,
          "d4: a file another program holds against a replace: the save gives up in words (Busy), the file is as it "
          "was, no temp file is left and nobody is forgotten", (got, left_beside(), all_mail()))


# =====================================================================================================================
def read_all():
    server._INBOUND_Q.join()


def post(tag, name):
    """One message with one plan in it (its title is its own, so no two of these are 'the same file')."""
    return call("POST", "/api/inbound/postmark",
                H.payload(tag, [(name, H.drawing_pdf(1, words=["quartzline"], title=name.upper()))]),
                auth=H.basic(), marker=False)


def inbox(who):
    return call("GET", "/api/inbound", cookie=CK[who])[1]


def e1_the_door_still_knows_the_account():
    put_right()
    n = inbox(ALICE)["total"]
    good = cut()
    st, j = post(I.tag_of(ALICE), "at-the-door.pdf")
    read_all()
    P.write_bytes(good)
    H.drain(server)
    got = inbox(ALICE)
    check(st == 200 and got["total"] == n + 1,
          "e1: a message that arrives while the file can not be read is still taken in for its account (it was "
          "dropped as 'no account has this address', with a 200 so it was never sent again)", (st, got["total"], n))


def e2_the_reader_forgets_nobody():
    put_right()
    n = inbox(ALICE)["total"]
    real = I.gather
    state = {}

    def gather_then_cut(*a, **k):
        state["good"] = cut()                   # the file turns unreadable while the message is being read
        return real(*a, **k)

    I.gather = gather_then_cut
    try:
        st, j = post(I.tag_of(ALICE), "mid-read.pdf")
        read_all()
    finally:
        I.gather = real
    if "good" in state:
        P.write_bytes(state["good"])
    H.drain(server)
    got = inbox(ALICE)
    check("good" in state and st == 200, "e2: (the file was cut short while the reader had the message)", (st, sorted(state)))
    check(mail(ALICE) == (True, True, 1) and got.get("address") is not None and got["total"] == n + 1,
          "e2: the account whose message was being read keeps its address, rules and messages (F1: all forgotten, as "
          "'removed while its message was being read')", (mail(ALICE), got.get("total"), n))
    e = next((x for x in got.get("entries") or [] if any(f["name"] == "mid-read.pdf" for f in x["files"])), None)
    check(e is not None and e["status"] == "done" and e["files"][0]["state"] == "opened",
          "e2: ... and its plan opened as a project", e and (e["status"], e["files"][0]["state"]))


def e3_with_no_roster_in_memory_the_message_stays_spooled():
    put_right()
    n = inbox(ALICE)["total"]
    gate = threading.Event()
    real = server._inbound_read

    def gated(account, eid):
        gate.wait(30)
        return real(account, eid)

    server._inbound_read = gated
    good = None
    try:
        st, j = post(I.tag_of(ALICE), "kept-in-the-spool.pdf")       # taken in while all is well; the reader held back
        good = cut()
        forget_what_was_read()                                        # ... then: as after a restart, on a damaged file
        gate.set()
        read_all()
    finally:
        server._inbound_read = real
    spool = _JOBS / "inbound" / "spool"
    kept = [f.name for f in spool.rglob("*") if f.is_file()] if spool.exists() else []
    check(st == 200 and len(kept) > 0,
          "e3: a spooled message the reader can not place - the file unreadable, no roster in memory - STAYS "
          "spooled (it was dropped: its account read as removed)", (st, kept))
    P.write_bytes(good)
    _users.load()
    check(mail(ALICE) == (True, True, 1), "e3: ... and the account's records are as they were", mail(ALICE))
    for account, eid in I.spool_waiting():                            # what the next start does (_inbound_resume)
        server._inbound_enqueue(account, eid)
    read_all()
    H.drain(server)
    CK.update({m: server._sign_session(m) for m in NAMES})
    got = inbox(ALICE)
    e = next((x for x in got.get("entries") or [] if any(f["name"] == "kept-in-the-spool.pdf" for f in x["files"])), None)
    check(got["total"] == n + 1 and e is not None and e["status"] == "done" and e["files"][0]["state"] == "opened",
          "e3: ... once the file reads again the message is read and its plan opens", e and (e["status"], e["files"]))


put_right()
for fn in (a1_no_file_is_no_accounts, a2_a_damaged_file_is_answered_from_the_last_roster,
           a3_a_change_is_refused_not_made_from_a_guess, a4_a_whole_roster_save_puts_it_right_and_forgets_nobody,
           a5_a_save_whose_read_is_refused_forgets_nobody, a6_a_sign_out_whose_read_is_refused_changes_nothing,
           a7_only_a_known_leaver_is_forgotten, a8_a_replaced_file_is_another_file,
           a9_a_temp_file_of_a_writer_that_died_is_cleared,
           b1_nobody_unsigned_gets_in, b2_the_site_password_is_no_way_in, b3_a_removed_file_after_accounts,
           c_started_on_a_file_it_can_not_read,
           d1_threads_at_once_lose_nothing, d2_processes_at_once_lose_nothing, d3_a_writer_waits_for_another_process,
           d4_a_file_nothing_can_replace,
           e1_the_door_still_knows_the_account, e2_the_reader_forgets_nobody,
           e3_with_no_roster_in_memory_the_message_stays_spooled):
    run(fn)

print(f"\n{checks} checks, {fails} failed")
print(f"{'PASS' if not fails else 'FAIL'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
