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
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

ITERATIONS = 240_000
_ALGO = "pbkdf2_sha256"


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
    """Replace a password and clear the must-change flag. Keeps `staff`."""
    store = load()
    rec = store.get(username)
    if not isinstance(rec, dict):
        return False
    new = hash_password(password)
    if rec.get("staff"):
        new["staff"] = True
    store[username] = new              # must_change deliberately not carried
    save(store)
    return True


def is_staff(username: str) -> bool:
    """Staff see every document on the box and may delete any of them.

    Needed because ownership is per person: once the demo corpus belongs to
    one account, nobody else on the team can open it, and duplicating twenty
    pre-detected sheets per colleague is absurd. It is also how a client's
    uploaded bid set gets cleaned off the box after a meeting.
    """
    rec = load().get(username)
    return bool(isinstance(rec, dict) and rec.get("staff"))


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
    """[{user, staff, must_change}] sorted by name, case-insensitively."""
    store = load()
    return [{"user": n, "staff": bool(r.get("staff")),
             "must_change": bool(r.get("must_change"))}
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
             must_change: bool = False) -> dict:
    """Create an account, or reset an existing one's password. Keeps an
    existing staff flag on a reset unless staff is re-stated; must_change is
    set only when asked for, and a reset clears it unless asked again."""
    store = load()
    name = find(username) or (username or "").strip()
    existed = name in store
    rec = hash_password(password)
    if staff or (existed and store[name].get("staff")):
        rec["staff"] = True
    if must_change:
        rec["must_change"] = True
    store[name] = rec
    save(store)
    return {"user": name, "staff": bool(rec.get("staff")),
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
    store = load()
    name = find(username)
    if name is None:
        return False
    rec = dict(store[name])
    if flag:
        rec["staff"] = True
    else:
        rec.pop("staff", None)
    store[name] = rec
    save(store)
    return True


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
                        "just their own. For your own team only.")
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
            flags = ("  [staff]" if users[n].get("staff") else "") +                     ("  [must change password]" if users[n].get("must_change") else "")
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
    rec = hash_password(pw)
    # keep an existing staff flag on a password reset unless it is re-stated
    if args.staff or (existed and users[args.username].get("staff")):
        rec["staff"] = True
    if args.force_change:
        rec["must_change"] = True
    users[args.username] = rec
    save(users)
    print(f"{'updated' if existed else 'added'} {args.username}"
          f"{' (STAFF - sees every document)' if rec.get('staff') else ''}"
          f"{' (must change at next sign-in)' if rec.get('must_change') else ''}; "
          f"{len(users)} user(s) total -> {store_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
