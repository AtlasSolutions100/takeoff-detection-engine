"""Per-user logins for the hosted review UI.

WHY THIS EXISTS. The service has one shared site password and `/api/recent`
lists every document ever uploaded to the box. Once the boss has demoed to a
few clients, that box holds several clients' live bid documents side by side,
and anyone with the password can open all of them. That is fine while one
person drives every demo and fatal the moment a client is handed a login.

WHAT IT DOES NOT DO. This is authentication, not a security boundary against a
determined attacker who already has shell on the box: `users.json` sits next to
the job store, and anyone who can read the volume can read the hashes. It stops
one logged-in user from seeing another's documents, which is the actual threat
here.

INERT BY DEFAULT. With no `users.json`, `load()` returns an empty mapping and
the service keeps its existing single-password behaviour exactly. Creating the
file is what turns per-user mode on, so this can ship without touching a box
that is about to be demoed on.

Hashing is PBKDF2-HMAC-SHA256 from the standard library - no new dependency in
an image that already has to stay small. The iteration count is stored per
record so it can be raised later without invalidating existing passwords.

ROLES (2026-09-25). Each record carries `role`: "admin" (what `staff` always
meant - every document on the box, and the accounts), "estimator" (what a
non-staff account always was - their own documents plus the shared set) or
"viewer" (new: reads what an estimator could read and changes nothing; the
service refuses its writes). Records written before roles existed have no
`role` and are read by their `staff` flag, so no file has to be migrated.
Every write here keeps `staff` in step with the role (admin <=> staff True),
because older code, older tests and older copies of this service read
`staff` and must keep getting the right answer.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
import uuid
from pathlib import Path

ITERATIONS = 240_000
_ALGO = "pbkdf2_sha256"

ROLES = ("admin", "estimator", "viewer")


def norm_role(value) -> str | None:
    """A role name as stored, or None when it is not one. Case and spaces
    forgiven, because these arrive from a form and from hand-edited files."""
    s = str(value or "").strip().lower()
    return s if s in ROLES else None


def _role(rec: dict) -> str:
    """The role a record grants. A stated role wins; without one - every
    record from before roles, and any role string this code does not know -
    the staff flag decides, exactly as it always did."""
    r = norm_role(rec.get("role")) if isinstance(rec, dict) else None
    if r:
        return r
    return "admin" if isinstance(rec, dict) and rec.get("staff") else "estimator"


def _stamp_role(rec: dict, role: str) -> dict:
    """Write `role` into a record with `staff` in step with it."""
    rec["role"] = role
    if role == "admin":
        rec["staff"] = True
    else:
        rec.pop("staff", None)
    return rec


def store_path() -> Path:
    """Where the user file lives: beside the job store, so it survives a
    redeploy on the same volume the way detections do."""
    root = os.environ.get("TAKEOFF_JOBS_DIR")
    base = Path(root).expanduser() if root else Path(__file__).parent.parent / "out" / "jobs"
    return base / "users.json"


def hash_password(password: str, *, salt: str | None = None,
                  iterations: int = ITERATIONS) -> dict:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), iterations)
    return {"algo": _ALGO, "salt": salt, "iterations": iterations,
            "hash": dk.hex()}


def verify(password: str, record: dict) -> bool:
    """Constant-time check. An unknown algorithm fails closed rather than
    falling back to something weaker."""
    if not isinstance(record, dict) or record.get("algo") != _ALGO:
        return False
    try:
        salt = record["salt"]
        iterations = int(record["iterations"])
        expect = record["hash"]
    except (KeyError, TypeError, ValueError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), iterations)
    return hmac.compare_digest(dk.hex(), expect)


# ---------------------------------------------------------------------------
# ENDING SESSIONS (2026-10-02). A session cookie is a signed name + expiry; nothing on the server listed it, so none
# could be ended: not by sign-out, a password change or a staff reset. Each record now carries `sv`, a counter, and the
# cookie carries the counter it was made with (server.py _sign_session); a cookie whose counter is LOWER than the
# record's is not a session. No `sv` reads as 0, and a cookie from before this carries none (0 too), so the deploy
# itself signs nobody out. save() moves the counter on, whoever calls it (the API, the CLI, set_password): the
# password changed (a reset, a change) or the account was made a viewer (the read-only role, where a build has
# roles) -> +1. Staff taken away does not end sessions: rights are read from this file on every request anyway. A NEW
# account starts at the time in seconds, so an account removed and made again under the same name does not let a
# cookie of the old one back in. bump_session() is sign-out (every session of that account). v1.8.5 reads past the
# unknown field; its own writes drop it for the record they rewrite.
_SV = "sv"


def _sv_of(rec) -> int:
    try:
        return max(0, int(rec.get(_SV, 0))) if isinstance(rec, dict) else 0
    except (TypeError, ValueError):
        return 0


def _is_viewer(rec) -> bool:
    return isinstance(rec, dict) and str(rec.get("role") or "").strip().lower() == "viewer"


def session_version(username) -> int:
    """The counter a session of this account must carry (0 for no such account, or none yet)."""
    return _sv_of(load().get(username)) if username else 0


def _keep_sessions(users: dict, disk: dict) -> dict:
    """The counters for a store about to be written, against the roster the file holds (`disk`: read from the file
    itself by save(), inside the writer's lock - never load()'s cached dict, which a caller may have edited in place.
    {} when there is no file or it could not be read: every record then counts as new, which ends its sessions)."""
    import time as _t
    out = {}
    for name, rec in users.items():
        if not isinstance(rec, dict):
            out[name] = rec
            continue
        old = disk.get(name)
        sv = _sv_of(rec)
        if isinstance(old, dict):
            was = _sv_of(old)
            sv = max(sv, was)
            if sv <= was and (old.get("hash") != rec.get("hash") or (_is_viewer(rec) and not _is_viewer(old))):
                sv = was + 1
        else:
            sv = max(sv, int(_t.time()))
        out[name] = dict(rec, **{_SV: sv}) if sv > 0 else {k: v for k, v in rec.items() if k != _SV}
    return out


# ---------------------------------------------------------------------------
# THE ACCOUNT FILE: A FAILED READ IS NEVER "NO ACCOUNTS" (2026-10-04, CRITIC-merge-1; the coordinator's F1 and F2).
#
# WHAT IT DID. load() answered {} for a file it could not read or parse and KEPT that answer under the good
# file's time and size. On Windows an open is refused while another thread or process replaces the file, so
# sign-outs at the same moment left a server with four accounts answering as one with none - no sign-in at all -
# until it was restarted. Every change started from load(), so the next account made was written over an empty
# roster and every other account left the file. save() wrote through ONE fixed temp name with no lock, so two
# saves at once lost each other's change. And save() forgot the e-mail records of every name its own failed read
# did not show it. Measured on be46b84 with 4 threads and 3 processes changing one file: all four accounts gone
# from the file, their bid addresses, rules and messages wiped, 20,357 reads answered "no accounts".
#
# NOW.
#   * NO FILE is still "no accounts": the laptop with no sign-in, and a hosted box before its first account.
#   * A file that is THERE and can not be read (held by another program, damaged, not a roster) is never "no
#     accounts". load() goes on serving the last roster this process read or wrote; when it has none it raises
#     Unreadable, and the service refuses every request that needs a sign-in (server.py, _SessionAuth). Either
#     way one line on stderr says why. Nothing is cached about a read that failed: the next call reads again.
#   * ONE WRITER AT A TIME on the whole machine (_one_writer). A change - sign-out, password, add, remove, staff -
#     reads the FILE ITSELF inside that lock (_fresh: never the cache, never a guess), changes it and writes it
#     back, so no change is made from an old roster and none is lost to another writer. A file that can not be
#     read is not written over by a change. save(), which is handed a whole roster, is how one is put right.
#   * A write is a temp file OF ITS OWN NAME, on disk before it is renamed over the file: a reader finds the old
#     file or the new one, never half of one.
#   * E-MAIL RECORDS (F1): an account's are forgotten only when it is KNOWN to be leaving - it is in the roster
#     read from the file just before the write and not in the roster written. A read that failed forgets nothing.
class Unreadable(RuntimeError):
    """The account file is there and can not be read, and there is no earlier roster to go on. `why` says what
    is wrong with it, in words; `held` is True when another program had it open (on Windows, a moment's thing)."""

    def __init__(self, why: str, held: bool = False):
        super().__init__(f"the account file can not be read: {why}")
        self.why, self.held = why, held


class Busy(RuntimeError):
    """Another program was writing the account file and did not finish in LOCK_WAIT_S. Nothing was written."""


LOCK_WAIT_S = 30.0             # how long a writer waits for another process's write before it gives up (Busy)
_READ_WAIT_S = 1.0             # how long a read waits out a replace under way (Windows refuses the open meanwhile)
_REPLACE_WAIT_S = 3.0          # how long a write waits for readers to let go of the file (Windows again)
_HELD_QUIET_S = 2.0            # a file another program holds is not worth a log line until it has lasted this long
_WLOCK = threading.RLock()     # this process: one writer at a time
_WDEPTH = 0                    # how deep its holder is in (save() inside a change): the OS lock is taken once
_WREAD = threading.local()     # .roster: what the holder read with _fresh() - save() takes that as "before"
_GEN = 0                       # counts this process's writes, so a slow read never puts an older roster back
_CACHE: tuple = (None, None)   # (the file's state, the roster read at that state)
_GOOD: tuple = (None, None)    # (the file's path, the last roster this process read from it or wrote to it)
_FAILING = 0.0                 # when the reads began to fail (time.monotonic); 0.0 while they succeed
_SAID: tuple = (None, 0.0)     # (the problem last said on stderr, when): one line a problem, again after a minute


def _say(msg) -> None:
    """One line on stderr about the account file: a problem once, then again each minute it lasts; None = it
    reads again (said only after a problem was)."""
    global _SAID, _FAILING
    now = time.monotonic()
    if msg is None:
        _FAILING = 0.0
        if _SAID[0] is None:
            return
        _SAID, msg = (None, 0.0), "the account file reads again"
    elif _SAID[0] == msg and now - _SAID[1] < 60.0:
        return
    else:
        _SAID = (msg, now)
    try:
        print(f"[users] {msg}", file=sys.stderr, flush=True)
    except Exception:               # noqa: BLE001 - a log line never costs the request
        pass


def _read_file(p: Path, wait_s: float = _READ_WAIT_S) -> tuple:
    """(roster, state) as the file is on disk now; (None, None) when there is NO file. Raises Unreadable when
    there is one and it is not readable as the account file. `state` is taken BEFORE the file is opened, so a
    file replaced in between is read again at the next look rather than trusted. On Windows the open is refused
    while another program replaces the file: that is waited out for up to `wait_s` (0: one try)."""
    end = time.monotonic() + wait_s
    while True:
        try:
            st = os.stat(p)
            with open(p, "rb") as fh:
                raw = fh.read()
            break
        except FileNotFoundError:
            return None, None
        except PermissionError:
            if os.name != "nt" or time.monotonic() >= end:
                raise Unreadable("it could not be opened (another program holds it, or this service may not "
                                 "read it)", held=os.name == "nt") from None
            time.sleep(0.02)
        except OSError as e:
            raise Unreadable(f"it could not be opened ({type(e).__name__})") from None
    try:
        data = json.loads(raw)
    except ValueError:              # not JSON, not text, cut short - an empty file is not JSON either
        raise Unreadable("it is not readable JSON (damaged, or cut short)" if raw.strip() else "it is empty") from None
    if not isinstance(data, dict):
        raise Unreadable("it is not a list of accounts")
    return ({str(k): v for k, v in data.items() if isinstance(v, dict)},
            (str(p), st.st_mtime_ns, st.st_size, st.st_ino))


def _no_file() -> dict:
    global _CACHE, _GOOD
    _CACHE, _GOOD = (None, None), (None, None)
    return {}


def _last_good(p: Path, why: str, held: bool = False) -> dict:
    """What load() answers when the file is there and could not be read: the last roster this process read or
    wrote, or Unreadable when it has none - never {}. Said on stderr, except a file another program held for
    a moment (Windows, during a save): that is said only once it has lasted."""
    global _FAILING
    now = time.monotonic()
    _FAILING = _FAILING or now
    good = _GOOD
    if good[0] == str(p) and good[1] is not None:
        if not held or now - _FAILING >= _HELD_QUIET_S:
            _say(f"{p.name} can not be read ({why}); the accounts as last read are served until it can")
        return good[1]
    _say(f"{p.name} can not be read ({why}) and this process has no earlier read of it: nobody can be signed in "
         "until it can")
    raise Unreadable(why, held)


def load() -> dict:
    """username -> record.

    NO FILE: {} - no accounts, which leaves the service on its single shared password (on a laptop: with no
    sign-in), exactly as before. A file that is there and can NOT be read is never read as "no accounts" (THE
    ACCOUNT FILE, above): the last roster this process read or wrote is served, and when there is none this
    raises Unreadable. It used to answer {} - which kept the service in single-password mode "rather than
    locking everybody out of a box because a file got truncated", and so opened a box holding several clients'
    bids to whoever held the site password, or to anyone at all.

    Cached on the file's state (time, size, file id): this is consulted on EVERY request by the auth
    middleware, and re-reading and re-parsing a JSON file per request is a silly amount of syscall for data
    that changes when someone runs a CLI command. Editing the file still takes effect immediately, because any
    edit moves its time or size, and a replaced file is another file. A request never waits here once a roster
    is known: a read that fails then answers the last one at once.
    """
    global _CACHE, _GOOD
    p = store_path()
    try:
        st = os.stat(p)
    except FileNotFoundError:
        return _no_file()
    except OSError as e:
        return _last_good(p, f"it could not be looked at ({type(e).__name__})")
    cached = _CACHE
    if cached[0] == (str(p), st.st_mtime_ns, st.st_size, st.st_ino):
        return cached[1]
    gen = _GEN
    try:
        roster, state = _read_file(p, 0.0 if _GOOD[0] == str(p) else _READ_WAIT_S)
    except Unreadable as e:
        return _last_good(p, e.why, e.held)
    if roster is None:              # gone between the two looks
        return _no_file()
    if gen == _GEN:                 # no write of this process came between: what was read is the newest known
        _CACHE, _GOOD = (state, roster), (str(p), roster)
    _say(None)
    return roster


def _fresh() -> dict:
    """The roster as the FILE holds it now, as a copy the caller may change: what a change starts from, inside
    _one_writer. Never the cache and never a guess: no file is {}, and a file that can not be read raises
    Unreadable, so it is not written over by a change made from nothing."""
    global _CACHE, _GOOD
    p = store_path()
    try:
        roster, state = _read_file(p)
    except Unreadable as e:
        _say(f"{p.name} can not be read ({e.why}); a change to the accounts was refused and nothing was written")
        raise
    if roster is None:
        _WREAD.roster = {}
        return _no_file()
    _CACHE, _GOOD = (state, roster), (str(p), roster)
    _WREAD.roster = roster          # save() compares against THIS read: one read a change, made inside its lock
    _say(None)
    return dict(roster)


def _os_lock(fh, take: bool) -> None:
    """Take the OS lock on an open file's first byte without waiting (OSError while another holds it), or let
    go of it."""
    fh.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK if take else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), (fcntl.LOCK_EX | fcntl.LOCK_NB) if take else fcntl.LOCK_UN)


@contextlib.contextmanager
def _one_writer():
    """ONE WRITER AT A TIME ON THIS MACHINE: this process's lock, and an OS lock on a file beside the account
    file (users.json.lock) for every other process - the command line, a script, a second server. The OS lets
    go of its lock when the process holding it ends, so the file is never left locked. A writer already inside
    (save() called by a change) goes straight through. Readers never wait on it."""
    global _WDEPTH
    with _WLOCK:
        if _WDEPTH:
            _WDEPTH += 1
            try:
                yield
            finally:
                _WDEPTH -= 1
            return
        p = store_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        fh = open(p.with_name(p.name + ".lock"), "a+b")
        try:
            end = time.monotonic() + LOCK_WAIT_S
            while True:
                try:
                    _os_lock(fh, True)
                    break
                except OSError:
                    if time.monotonic() >= end:
                        raise Busy("the accounts are being saved by another program; nothing was changed") from None
                    time.sleep(0.01)
            _WDEPTH, _WREAD.roster = 1, None
            try:
                yield
            finally:
                _WDEPTH, _WREAD.roster = 0, None
                try:
                    _os_lock(fh, False)
                except OSError:
                    pass
        finally:
            fh.close()


def _writing(fn):
    """A change to the account file as ONE step: `fn` reads the file with _fresh(), changes the roster and
    save()s it, and no other writer - a thread here, another process - comes between the read and the write."""
    @functools.wraps(fn)
    def run(*a, **kw):
        with _one_writer():
            return fn(*a, **kw)
    return run


def _write_file(p: Path, users: dict) -> None:
    """The whole file at once, by the holder of _one_writer: a temp file of this write's OWN name (two writers
    never share one - a fixed name let one save's bytes land in another's file), on disk before the rename, then
    renamed over the account file. The rename is retried a moment on Windows, where it is refused while a reader
    has the file open. A temp file a writer left when it died mid-write is cleared first: it holds the hashes."""
    for old in p.parent.glob(p.name + ".*.tmp"):
        try:
            old.unlink()
        except OSError:
            pass
    tmp = p.with_name(f"{p.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(users, indent=1, sort_keys=True))
            fh.flush()
            os.fsync(fh.fileno())
        end = time.monotonic() + _REPLACE_WAIT_S
        while True:
            try:
                os.replace(tmp, p)  # atomic, so a crash cannot truncate it
                break
            except PermissionError:
                if os.name != "nt":
                    raise
                if time.monotonic() >= end:
                    raise Busy("the account file is held open by another program; nothing was changed") from None
                time.sleep(0.01)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass                        # best effort; Windows dev boxes ignore it


def _forget_mail(names) -> None:
    """BIDS BY E-MAIL (takeoff_engine/inbound.py, 2026-10-04). An account that is REMOVED keeps nothing there: its
    bid address, its keyword rules, its message records and its held plans go, so an account made later under the
    same name starts clean - it used to get the old one's address and its whole list of messages back. Done HERE,
    beside the session counter and for the same reason: every way an account is removed goes through save() (the
    Accounts page, the command line, a script). ONLY a name KNOWN to be leaving (save, below): a name that enters
    the file is no longer forgotten, so a roster written back over a missing or unreadable file costs nobody
    their records. Best effort: a failure here is said on stderr and never costs the account change itself."""
    for name in sorted(names):
        try:
            from takeoff_engine import inbound as _inbound
            _inbound.forget_account(name)
        except Exception as e:          # noqa: BLE001 - by design
            try:
                print(f"[users] an account's e-mail records could not be cleared ({e!r})", file=sys.stderr)
            except Exception:           # noqa: BLE001
                pass


def save(users: dict) -> None:
    """Write the whole roster, as one writer (THE ACCOUNT FILE, above). A change to one account belongs in a
    function under @_writing, which reads the file first; this is for a caller that holds the whole roster (a
    test, the provisioning tool), and it is how a file that can not be read is put right."""
    global _CACHE, _GOOD, _GEN
    with _one_writer():
        p = store_path()
        # who has an account until this write: what the change that called this read inside the same lock, else
        # the file now ({}: there is no file)
        before = getattr(_WREAD, "roster", None)
        if before is None:
            try:
                before = _read_file(p)[0] or {}
            except Unreadable:
                before = None                   # NOT KNOWN: the file is there and could not be read
        # sessions end on a password change, a reset, a move to viewer (ENDING SESSIONS, above)
        users = _keep_sessions(users, before or {})
        _write_file(p, users)
        written = {str(k): v for k, v in users.items() if isinstance(v, dict)}
        _GEN += 1
        _CACHE, _GOOD = (None, None), (str(p), written)     # the next load() reads the file; until it can, this
        _WREAD.roster = written
        if before is not None:
            # removed: nothing of that account is left in the e-mail store. Only what the read just before SHOWED
            # leaving - when that read failed nobody is forgotten (it used to forget every account then)
            _forget_mail(set(before) - set(written))


@_writing
def bump_session(username) -> bool:
    """End every session of this account (sign-out). False when there is no such account."""
    store = _fresh()
    rec = store.get(username)
    if not isinstance(rec, dict):
        return False
    store[username] = dict(rec, **{_SV: _sv_of(rec) + 1})
    save(store)
    return True


def must_change(username: str) -> bool:
    """True while this account is still on the password somebody else chose.

    Everyone is issued the same starting password, so until it is changed the
    account is only as private as that word is secret. The service refuses to
    do anything but change it, which is the bit that makes a shared default
    tolerable rather than reckless.
    """
    rec = load().get(username)
    return bool(isinstance(rec, dict) and rec.get("must_change"))


@_writing
def set_password(username: str, password: str) -> bool:
    """Replace a password and clear the must-change flag. Keeps the role
    (and so `staff`): a viewer that changes its password stays a viewer."""
    store = _fresh()
    rec = store.get(username)
    if not isinstance(rec, dict):
        return False
    new = _stamp_role(hash_password(password), _role(rec))
    store[username] = new              # must_change deliberately not carried
    save(store)
    return True


def is_staff(username: str) -> bool:
    """Staff see every document on the box and may delete any of them.

    Needed because ownership is per person: once the demo corpus belongs to
    one account, nobody else on the team can open it, and duplicating twenty
    pre-detected sheets per colleague is absurd. It is also how a client's
    uploaded bid set gets cleaned off the box after a meeting.

    Staff IS the admin role; read through it so a record whose role says
    otherwise (a hand edit left a stale staff flag) is not staff.
    """
    rec = load().get(username)
    return isinstance(rec, dict) and _role(rec) == "admin"


def role_of(username: str, store: dict | None = None) -> str | None:
    """admin / estimator / viewer for this account, None when there is no
    such account. Exact stored spelling, like is_staff - the session carries
    the canonical name that check() returned. `store`: the roster to look in
    (the one a request was signed in against); otherwise load()'s."""
    rec = (load() if store is None else store).get(username)
    return _role(rec) if isinstance(rec, dict) else None


@_writing
def set_role(username: str, role: str) -> bool:
    """Give an account a role (staff kept in step). False when there is no
    such account; ValueError on a role that is not one. The rules about WHO
    may do this - the last admin, your own role - are the API's (server.py);
    a person at the CLI is trusted the way the CLI always trusted them.

    One step, like every change here (THE ACCOUNT FILE, above): the roster is
    read from the file inside the writer's lock, so a role is never set on an
    old roster (which put back accounts another writer had removed, and lost
    the ones it had made), and a file that can not be read is not written
    over - the change is refused (Unreadable) and nothing is written."""
    want = norm_role(role)
    if want is None:
        raise ValueError(f"unknown role {role!r}: use one of {', '.join(ROLES)}")
    store = _fresh()
    name = find(username, store)
    if name is None:
        return False
    store[name] = _stamp_role(dict(store[name]), want)
    save(store)
    return True


def check(username: str, password: str) -> str | None:
    """Return the canonical username on success, else None.

    Usernames are matched case-insensitively because people type them into a
    browser prompt from memory, but the stored spelling is what gets returned
    and recorded as the owner - so `Scott` and `scott` are one account and
    cannot end up owning two separate sets of documents.
    """
    users = load()
    if not users:
        return None
    want = (username or "").strip().lower()
    for name, rec in users.items():
        if name.lower() == want and verify(password, rec):
            return name
    return None


# ---------------------------------------------------------------------------
# ACCOUNT MANAGEMENT. Shared by the CLI below and the staff-only /api/users
# routes in server.py (2026-09-15), so a record made from a browser and one
# made at a shell are the same record. Before this, every account on the
# hosted box was created over ssh; the boss could not hand a client a login
# without the developer at a terminal.

def list_users() -> list:
    """[{user, staff, must_change, role}] sorted by name, case-insensitively.
    `staff` is read through the role, so the two can never disagree here."""
    store = load()
    return [{"user": n, "staff": _role(r) == "admin",
             "must_change": bool(r.get("must_change")), "role": _role(r)}
            for n, r in sorted(store.items(), key=lambda kv: kv[0].lower())]


def find(username: str, store: dict | None = None) -> str | None:
    """The stored spelling of a name, matched the way sign-in matches it
    (case-insensitively), or None. `store`: the roster to look in - a change
    passes the one it just read from the file (_fresh); otherwise load()'s."""
    want = (username or "").strip().lower()
    if not want:
        return None
    for n in (load() if store is None else store):
        if n.lower() == want:
            return n
    return None


def temp_password() -> str:
    """A starting password to hand over once: 12 characters from an alphabet
    with no look-alikes (no 0/O, no 1/l/I), so it survives being read out
    over the phone."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))


@_writing
def add_user(username: str, password: str, *, staff: bool = False,
             must_change: bool = False, role: str | None = None) -> dict:
    """Create an account, or reset an existing one's password. `role` wins
    over `staff` (staff=True alone still means admin); with neither, a reset
    keeps the role the account had - so resetting a viewer's password can
    never promote it - and a new account is an estimator. must_change is set
    only when asked for, and a reset clears it unless asked again."""
    store = _fresh()
    name = find(username, store) or (username or "").strip()
    existed = name in store
    if role is not None:
        want = norm_role(role)
        if want is None:
            raise ValueError(f"unknown role {role!r}: use one of {', '.join(ROLES)}")
    elif staff:
        want = "admin"
    else:
        want = _role(store[name]) if existed else "estimator"
    rec = _stamp_role(hash_password(password), want)
    if must_change:
        rec["must_change"] = True
    store[name] = rec
    save(store)
    return {"user": name, "staff": want == "admin", "role": want,
            "must_change": bool(rec.get("must_change")), "existed": existed}


@_writing
def remove_user(username: str) -> bool:
    store = _fresh()
    name = find(username, store)
    if name is None:
        return False
    store.pop(name)
    save(store)
    return True


@_writing
def set_staff(username: str, flag: bool) -> bool:
    """The staff toggle, in roles: on = admin; off takes an admin down to
    estimator and leaves anyone else's role alone - "not staff" is already
    true of a viewer, and reading it as "estimator" would promote one."""
    store = _fresh()
    name = find(username, store)
    if name is None:
        return False
    cur = _role(store[name])
    want = "admin" if flag else ("estimator" if cur == "admin" else cur)
    store[name] = _stamp_role(dict(store[name]), want)
    save(store)
    return True


# ---------------------------------------------------------------------------
# LAST ACTIVE (2026-09-25). When each account was last seen - any signed-in
# request - and last signed in, for the team page. Its own file,
# JOBS_DIR/user_activity.json = {username: {"last_active", "last_login"}}
# (ISO UTC), and NEVER users.json: that file is the credential store, it is
# consulted on every request through load()'s (mtime, size) cache, and
# rewriting it once a minute per person would churn that cache and put the
# password hashes one torn write away from a lockout.
#
# THROTTLED: at most one write per account per ACTIVITY_EVERY_S. The newest
# sighting is always kept in memory and activity() merges it in, so the team
# page is exact within this process even while the file lags by up to a
# minute; the file only has to survive a restart, and "about when" is all a
# restart can cost. Best effort throughout: note_seen never raises, and a
# failed write costs a timestamp, never the request it rode on.
ACTIVITY_EVERY_S = 60.0
_ACT_LOCK = threading.Lock()
_ACT_MEM: dict = {}          # username -> newest known {"last_active", "last_login"}
_ACT_WRITTEN: dict = {}      # username -> time.monotonic() of its last write
_ACT_FIELDS = ("last_active", "last_login")


def activity_path() -> Path:
    return store_path().parent / "user_activity.json"


def _read_activity() -> dict:
    try:
        d = json.loads(activity_path().read_text())
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in d.items() if isinstance(v, dict)} \
        if isinstance(d, dict) else {}


def _newer(a: dict, b: dict) -> dict:
    """Per field, the later of two ISO-UTC stamps (same format, so the
    string order IS the time order)."""
    out = {}
    for k in _ACT_FIELDS:
        vals = [v for v in (a.get(k), b.get(k)) if isinstance(v, str) and v]
        out[k] = max(vals) if vals else None
    return out


def note_seen(username: str | None, *, login: bool = False) -> bool:
    """Record that `username` made a signed-in request (and signed in, when
    `login`). True when this call wrote the file."""
    if not username:
        return False
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with _ACT_LOCK:
            rec = _ACT_MEM.setdefault(username, {})
            rec["last_active"] = now
            if login:
                rec["last_login"] = now
            last = _ACT_WRITTEN.get(username)
            mono = time.monotonic()
            if last is not None and mono - last < ACTIVITY_EVERY_S:
                return False
            # stamped BEFORE the write: an unwritable volume is retried once
            # a minute, not hammered on every request
            _ACT_WRITTEN[username] = mono
            # Only accounts that still exist are kept, so an account removed
            # at the CLI (another process - forget_activity never ran here)
            # drops out on the next write instead of lingering. A users.json
            # that can not be read is NOT "no accounts" (THE ACCOUNT FILE,
            # above): load() answers the last roster this process read, and
            # with none it raises - caught below, so nothing is written and
            # nobody's stamps are wiped over a truncated file. No file at all
            # is no accounts: nothing to write for.
            known = load()
            if not known:
                return False
            data = {u: v for u, v in _read_activity().items() if u in known}
            for u, r in _ACT_MEM.items():   # one write flushes everyone's
                if u in known:
                    data[u] = _newer(data.get(u) or {}, r)
            p = activity_path()
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
            tmp.replace(p)                  # atomic, like save()
        return True
    except Exception:                       # noqa: BLE001 - by design
        return False


def activity() -> dict:
    """{username: {"last_active", "last_login"}} - the file, with this
    process's newer sightings merged over it."""
    try:
        data = _read_activity()
        with _ACT_LOCK:
            for u, r in _ACT_MEM.items():
                data[u] = _newer(data.get(u) or {}, r)
        return data
    except Exception:                       # noqa: BLE001 - never fail a page
        return {}


def forget_activity(username: str) -> None:
    """Drop a removed account's stamps, so a new account made later under
    the same name does not start life "last active three months ago"."""
    try:
        with _ACT_LOCK:
            _ACT_MEM.pop(username, None)
            _ACT_WRITTEN.pop(username, None)
            data = _read_activity()
            if data.pop(username, None) is not None:
                p = activity_path()
                tmp = p.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
                tmp.replace(p)
    except Exception:                       # noqa: BLE001 - best effort
        pass


def _cli() -> int:
    import argparse
    import getpass

    ap = argparse.ArgumentParser(
        prog="python -m takeoff_engine.users",
        description="Manage logins for the hosted review UI. "
                    "With no users defined the service stays on its single "
                    "shared password.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="add or reset a user's password")
    a.add_argument("username")
    a.add_argument("--password", help="omit to be prompted (preferred: a "
                                      "password on the command line lands in "
                                      "the shell history)")
    a.add_argument("--staff", action="store_true",
                   help="can see and delete EVERY document on the box, not "
                        "just their own. For your own team only. Same as "
                        "--role admin.")
    a.add_argument("--role", choices=ROLES,
                   help="admin = --staff (every document, and the accounts); "
                        "estimator = their own documents plus the shared set "
                        "(a new account's default); viewer = reads what an "
                        "estimator could, changes nothing. Wins over --staff. "
                        "Omitted on a password reset: the role is kept.")
    a.add_argument("--force-change", action="store_true",
                   help="make them choose a new password at next sign-in. Use "
                        "whenever YOU picked the password rather than they "
                        "did - the account is not private until they change it.")
    sub.add_parser("list", help="list usernames")
    r = sub.add_parser("remove", help="remove a user")
    r.add_argument("username")
    args = ap.parse_args()
    try:
        return _cli_run(args, getpass)
    except (Unreadable, Busy) as e:
        # a file that is there and can not be read is never changed from a guess (THE ACCOUNT FILE)
        print(f"refused: {e}. Nothing was written to {store_path()}.")
        return 1


def _cli_run(args, getpass) -> int:
    if args.cmd == "list":
        users = load()
        if not users:
            print("no users defined - the service is in single-password mode")
        for n in sorted(users):
            flags = f"  [{_role(users[n])}]" +                     ("  [must change password]" if users[n].get("must_change") else "")
            print(f"{n}{flags}")
        print(f"\nstore: {store_path()}")
        return 0

    if args.cmd == "remove":
        with _one_writer():             # read from the file and written back as one step
            users = _fresh()
            if args.username not in users:
                print(f"no such user: {args.username}")
                return 1
            users.pop(args.username)
            save(users)
        print(f"removed {args.username}; {len(users)} user(s) left")
        if not users:
            print("WARNING: no users left, so the service falls back to the "
                  "single shared password and every document becomes visible "
                  "to anyone holding it.")
        return 0

    pw = args.password or getpass.getpass(f"password for {args.username}: ")
    if len(pw) < 8:
        print("refused: use at least 8 characters")
        return 1
    with _one_writer():                 # read from the file and written back as one step (after the prompt)
        users = _fresh()
        existed = args.username in users
        # --role wins, --staff means admin, and with neither a password reset
        # keeps the role the account had (it used to keep the staff flag the
        # same way) while a new account is an estimator
        role = args.role or ("admin" if args.staff else
                             (_role(users[args.username]) if existed else "estimator"))
        rec = _stamp_role(hash_password(pw), role)
        if args.force_change:
            rec["must_change"] = True
        users[args.username] = rec
        save(users)
    print(f"{'updated' if existed else 'added'} {args.username} [{role}]"
          f"{' (ADMIN - sees every document)' if role == 'admin' else ''}"
          f"{' (VIEWER - read-only)' if role == 'viewer' else ''}"
          f"{' (must change at next sign-in)' if rec.get('must_change') else ''}; "
          f"{len(users)} user(s) total -> {store_path()}")
    if not any(_role(r) == "admin" for r in users.values()):
        print("WARNING: no admin account on this server, so nobody can manage "
              "accounts from the browser. Add one with --role admin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
