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

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
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


def bump_session(username) -> bool:
    """End every session of this account (sign-out). False when there is no such account."""
    store = dict(load())
    rec = store.get(username)
    if not isinstance(rec, dict):
        return False
    store[username] = dict(rec, **{_SV: _sv_of(rec) + 1})
    save(store)
    return True


def _keep_sessions(users: dict) -> dict:
    """The counters for a store about to be written, against the one on disk (read from the file itself: callers
    edit load()'s cached dict in place, so the cache already holds the new records)."""
    try:
        disk = json.loads(store_path().read_text())
        disk = disk if isinstance(disk, dict) else {}
    except (OSError, ValueError):
        disk = {}
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


_CACHE: tuple = (None, None)


def load() -> dict:
    """username -> record. Empty when the file is missing, unreadable, or
    malformed - which keeps the service in single-password mode rather than
    locking everybody out of a box because a file got truncated.

    Cached on (mtime, size): this is consulted on EVERY request by the auth
    middleware, and re-reading and re-parsing a JSON file per request is a
    silly amount of syscall for data that changes when someone runs a CLI
    command. Editing the file still takes effect immediately, because any
    edit moves mtime or size.
    """
    global _CACHE
    p = store_path()
    try:
        st = p.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        _CACHE = (None, {})
        return {}
    if _CACHE[0] == key:
        return _CACHE[1]
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        _CACHE = (key, {})
        return {}
    if not isinstance(data, dict):
        _CACHE = (key, {})
        return {}
    out = {str(k): v for k, v in data.items() if isinstance(v, dict)}
    _CACHE = (key, out)
    return out


def save(users: dict) -> None:
    users = _keep_sessions(users)       # sessions end on a password change, a reset, a move to viewer (above)
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(users, indent=1, sort_keys=True))
    tmp.replace(p)                      # atomic, so a crash cannot truncate it
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass                            # best effort; Windows dev boxes ignore it


def must_change(username: str) -> bool:
    """True while this account is still on the password somebody else chose.

    Everyone is issued the same starting password, so until it is changed the
    account is only as private as that word is secret. The service refuses to
    do anything but change it, which is the bit that makes a shared default
    tolerable rather than reckless.
    """
    rec = load().get(username)
    return bool(isinstance(rec, dict) and rec.get("must_change"))


def set_password(username: str, password: str) -> bool:
    """Replace a password and clear the must-change flag. Keeps the role
    (and so `staff`): a viewer that changes its password stays a viewer."""
    store = load()
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


def role_of(username: str) -> str | None:
    """admin / estimator / viewer for this account, None when there is no
    such account. Exact stored spelling, like is_staff - the session carries
    the canonical name that check() returned."""
    rec = load().get(username)
    return _role(rec) if isinstance(rec, dict) else None


def set_role(username: str, role: str) -> bool:
    """Give an account a role (staff kept in step). False when there is no
    such account; ValueError on a role that is not one. The rules about WHO
    may do this - the last admin, your own role - are the API's (server.py);
    a person at the CLI is trusted the way the CLI always trusted them."""
    want = norm_role(role)
    if want is None:
        raise ValueError(f"unknown role {role!r}: use one of {', '.join(ROLES)}")
    store = load()
    name = find(username)
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


def find(username: str) -> str | None:
    """The stored spelling of a name, matched the way sign-in matches it
    (case-insensitively), or None."""
    want = (username or "").strip().lower()
    if not want:
        return None
    for n in load():
        if n.lower() == want:
            return n
    return None


def temp_password() -> str:
    """A starting password to hand over once: 12 characters from an alphabet
    with no look-alikes (no 0/O, no 1/l/I), so it survives being read out
    over the phone."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))


def add_user(username: str, password: str, *, staff: bool = False,
             must_change: bool = False, role: str | None = None) -> dict:
    """Create an account, or reset an existing one's password. `role` wins
    over `staff` (staff=True alone still means admin); with neither, a reset
    keeps the role the account had - so resetting a viewer's password can
    never promote it - and a new account is an estimator. must_change is set
    only when asked for, and a reset clears it unless asked again."""
    store = load()
    name = find(username) or (username or "").strip()
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


def remove_user(username: str) -> bool:
    store = load()
    name = find(username)
    if name is None:
        return False
    store.pop(name)
    save(store)
    return True


def set_staff(username: str, flag: bool) -> bool:
    """The staff toggle, in roles: on = admin; off takes an admin down to
    estimator and leaves anyone else's role alone - "not staff" is already
    true of a viewer, and reading it as "estimator" would promote one."""
    store = load()
    name = find(username)
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
            # drops out on the next write instead of lingering. An unreadable
            # users.json reads as no accounts: write nothing rather than
            # wipe everybody's stamps over a truncated file.
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

    users = load()
    if args.cmd == "list":
        if not users:
            print("no users defined - the service is in single-password mode")
        for n in sorted(users):
            flags = f"  [{_role(users[n])}]" +                     ("  [must change password]" if users[n].get("must_change") else "")
            print(f"{n}{flags}")
        print(f"\nstore: {store_path()}")
        return 0

    if args.cmd == "remove":
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
