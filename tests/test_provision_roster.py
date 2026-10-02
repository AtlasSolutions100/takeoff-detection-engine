"""The customer list is not in the image (2026-10-01).

provision_demo.py is COPY'd into the Docker image, and it used to carry the
eighteen customers' e-mail addresses as a list in its source - so anybody
holding the image learned who the competing customers are. The list now lives
in a roster file in the JOB STORE (a volume): <jobs dir>/provision-accounts.txt,
or --accounts PATH. This pins:

  1. no file the Dockerfile COPYs carries an e-mail address other than the
     two staff contacts Terms / Privacy already publish (and example ones);
  2. the script reads its customers from the roster file, by default from the
     store, or from --accounts;
  3. without a roster it creates the staff accounts only and leaves every
     account already in users.json exactly as it is;
  4. a malformed roster line refuses the run before anything is written;
  5. in-app account management is untouched: `python -m takeoff_engine.users
     add` still makes an account in the same store.

Runs the real script as a subprocess against throwaway stores, stdin closed.

    python tests/test_provision_roster.py
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import provision_demo  # noqa: E402

CHECKS = []


def ok(cond, msg, got=None):
    CHECKS.append((bool(cond), msg, got))


EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
STAFF = {s.lower() for s in provision_demo.STAFF}
ALLOWED_DOMAINS = ("example.com", "example.test", "company.com")

# ---- 1. what the image carries -----------------------------------------------
docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
copied = []
for line in docker.splitlines():
    m = re.match(r"\s*COPY\s+(.+?)\s+\S+\s*$", line)
    if m:
        copied += m.group(1).split()
ok("provision_demo.py" in copied, "the Dockerfile still ships provision_demo.py (the ops tool)")
files = []
for c in copied:
    p = ROOT / c.rstrip("/")
    if p.is_dir():
        files += [f for f in p.rglob("*") if f.is_file()]
    elif p.is_file():
        files.append(p)
ok(len(files) > 50, "the COPY list resolves to the image's files", len(files))
foreign = {}
for f in files:
    if f.suffix.lower() in (".png", ".ico", ".woff", ".woff2", ".webm", ".pyc"):
        continue
    try:
        text = f.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for a in EMAIL.findall(text):
        a = a.lower().rstrip(".")
        if a in STAFF or a.endswith(ALLOWED_DOMAINS):
            continue
        foreign.setdefault(f.relative_to(ROOT).as_posix(), set()).add(a)
ok(not foreign, "no file in the image carries an e-mail address but staff / example ones",
   {k: sorted(v)[:3] for k, v in foreign.items()})
src = (ROOT / "provision_demo.py").read_text(encoding="utf-8")
ok(not hasattr(provision_demo, "CLIENTS"), "provision_demo has no CLIENTS list any more")
ok({a.lower() for a in EMAIL.findall(src)} <= STAFF | {"you@example.com"},
   "provision_demo.py names only the two staff addresses",
   sorted({a.lower() for a in EMAIL.findall(src)} - STAFF))


# ---- 2-5. the script against throwaway stores --------------------------------
def store():
    return Path(tempfile.mkdtemp(prefix="provision_roster_test_"))


def run(st, *args, expect=0):
    env = dict(os.environ, TAKEOFF_JOBS_DIR=str(st), TAKEOFF_LEDGER_DIR=str(st / "ledger"))
    r = subprocess.run([sys.executable, "provision_demo.py", *args], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    ok(r.returncode == expect, f"provision_demo {' '.join(args) or '(bare)'} exits {expect}",
       (r.returncode, r.stdout[-400:], r.stderr[-400:]))
    return r.stdout + r.stderr


def accounts(st):
    p = st / "users.json"
    return json.loads(p.read_text()) if p.exists() else {}


C1, C2 = "first_synth@example.test", "second_synth@example.test"

# 2a. the roster in the store, by default
st = store()
(st / provision_demo.ROSTER_NAME).write_text(f"# customers\n{C1}\n\n{C2.upper()}  # shouting\n{C1}\n")
out = run(st)
acc = accounts(st)
ok(set(acc) == STAFF | {C1, C2}, "default roster in the store: staff + the two customers", sorted(acc))
ok(all(acc[u].get("must_change") for u in (C1, C2)) and not acc[C1].get("staff"),
   "customers are plain accounts that must choose a password")
ok("2 address(es)" in out, "the run says how many addresses it read and from where")

# 2b. --accounts PATH
st = store()
elsewhere = store() / "list.txt"
elsewhere.write_text(C2 + "\n")
run(st, "--accounts", str(elsewhere))
ok(set(accounts(st)) == STAFF | {C2}, "--accounts PATH reads that file", sorted(accounts(st)))

# 3. no roster: staff only, existing accounts untouched
st = store()
before = {C1: {"salt": "x", "hash": "y", "iterations": 1, "algo": "pbkdf2_sha256"}}
(st / "users.json").write_text(json.dumps(before))
out = run(st)
acc = accounts(st)
ok(set(acc) == STAFF | {C1}, "no roster: the staff accounts are added, nobody else", sorted(acc))
ok(acc[C1] == before[C1], "...and an account already on the box is left byte-for-byte")
ok("no customer roster" in out, "...and the run says there was no roster")

# 4. a bad line refuses before any write
st = store()
(st / provision_demo.ROSTER_NAME).write_text(f"{C1}\nnot an address\n")
run(st, expect=1)
ok(not (st / "users.json").exists() and not (st / "demo-credentials.txt").exists(),
   "a malformed roster writes nothing")

# 5. in-app / CLI account management is unchanged
st = store()
env = dict(os.environ, TAKEOFF_JOBS_DIR=str(st))
r = subprocess.run([sys.executable, "-m", "takeoff_engine.users", "add", "cli_synth@example.test",
                    "--password", "quartz-ember-5512"], cwd=str(ROOT), env=env,
                   capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
ok(r.returncode == 0 and "cli_synth@example.test" in accounts(st),
   "python -m takeoff_engine.users add still creates an account", (r.returncode, r.stderr[-300:]))

fails = [c for c in CHECKS if not c[0]]
for good, msg, got in CHECKS:
    print(("ok:   " if good else "FAIL: ") + msg
          + ("" if good or got is None else f"   got={got!r}"))
print(f"\n{len(CHECKS) - len(fails)}/{len(CHECKS)} checks passed")
sys.exit(1 if fails else 0)
