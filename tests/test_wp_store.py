"""THE WALL PANEL SETTINGS STORE IS SAFE (critic wp-1, MAJOR-3; 2026-10-04). Every number is made up.

`wallpanels.json` is ONE file for every account. Before this, a save read it whole and wrote it whole
with no lock, and a file that could not be read was "nothing set". Measured by the critic on this
machine: two accounts saving at the same moment lost 112 of 312 saves answered 200 and 88 more
answered 500; and after a stray comma made by hand every account read "nothing set", and the next
save by any account rewrote the file with that ONE account.

  * a MISSING file means nothing is set (and reading makes no file)
  * two accounts saving at once, hundreds of saves: none lost, none refused
  * one account saving while another reads: the reader never sees "nothing set"; no save fails
  * two more PROCESSES saving at the same time (the terminal loader's path): none lost
  * a save touches ONE record: every other account's record goes back as it was read
  * a DAMAGED file is never "nothing set": reads say so, the write is refused, the file stays as it
    is, a copy of it is kept beside it under another name, and Settings and the job's reply say so;
    mended by a person, everything reads again

Synthetic accounts, every request through the real middleware with no server listening.

    python tests/test_wp_store.py
"""
import asyncio
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
_JOBS = Path(tempfile.mkdtemp(prefix="wpstore_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(_JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="wpstore_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ.pop("TAKEOFF_UI_PASSWORD", None)

import pymupdf  # noqa: E402

ALICE, BOB, STAFF = "alice_synth", "bob_synth", "staff_synth"
RESULT = {"page": {"width_pt": 612, "height_pt": 792}, "styles": [], "chains": [], "openings": [],
          "scale": {"ft_per_paper_inch": 4.0, "text": "1/4\" = 1'-0\""}}
d = pymupdf.open()
d.new_page(width=612, height=792)
pdf = _JOBS / "upload_000000000001_bob-own.pdf"
d.save(str(pdf))
d.close()
BOBJ = "wps000000000"
(_JOBS / BOBJ).mkdir()
(_JOBS / BOBJ / "p0_result.json").write_text(json.dumps(RESULT))
(_JOBS / "registry.json").write_text(json.dumps({BOBJ: {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2), "kinds": None,
                                                        "sizes": [[612, 792]], "owner": BOB, "bid": None}}, indent=1))

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402
from takeoff_engine import wp_settings as S  # noqa: E402

server._classify_async = lambda *a, **k: None
_users.save({ALICE: _users.hash_password("orchid-lantern-4471"), BOB: _users.hash_password("basalt-meadow-9083"),
             STAFF: dict(_users.hash_password("copper-harbor-2265"), staff=True)})
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in (ALICE, BOB, STAFF)}
fails = 0


def check(ok, msg, got=None):
    global fails
    print(("  ok   " if ok else "  FAIL ") + msg + ("" if ok or got is None else f"  (got {str(got)[:500]})"))
    if not ok:
        fails += 1


async def _asgi(method, path, cookie, body=b""):
    headers = [(b"host", b"testserver"), (b"x-core-client", b"3"), (b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode())]
    if cookie:
        headers.append((b"cookie", f"{server.SESSION_COOKIE}={cookie}".encode()))
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": headers, "client": ("127.0.0.1", 40000), "server": ("testserver", 80)}
    sent = {"b": False}
    out = {"status": None, "body": b""}

    async def receive():
        if not sent["b"]:
            sent["b"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(m):
        if m["type"] == "http.response.start":
            out["status"] = m["status"]
        elif m["type"] == "http.response.body":
            out["body"] += m.get("body", b"")

    await server.app(scope, receive, send)
    return out["status"], out["body"]


def call(method, path, who, body=None):
    """(status, answer). An exception out of the app is what a real server answers 500 for."""
    try:
        st, data = asyncio.run(_asgi(method, path, CK[who] if who else None, b"" if body is None else json.dumps(body).encode()))
    except Exception as e:  # noqa: BLE001
        return 500, {"detail": f"EXCEPTION {type(e).__name__}: {e}"}
    try:
        return st, json.loads(data)
    except ValueError:
        return st, data.decode("utf-8", "replace")


def state(user):
    """(record, scope, problem) by the store's own answer; on a build without it, what `resolve` says."""
    if hasattr(S, "state"):
        return S.state(user)
    rec, scope = S.resolve(user)
    return rec, scope, None


def stored(user):
    """One account's record as the file holds it NOW, read here - not through the store's own reader. (A read that
    lands on the instant of a replace is tried again: on Windows the file can not be opened for that instant.)"""
    for _ in range(100):
        try:
            return (json.loads(S.store_path().read_text(encoding="utf-8")).get("accounts") or {}).get(user)
        except PermissionError:
            time.sleep(0.02)
        except (OSError, ValueError):
            return None
    return None


SETTINGS = {"labor_rate": "41.37", "overtime_after_hours": "9", "overtime_rate": "17.25", "travel_per_mile": "0.93",
            "hotel_nightly": "141.19", "profit_flat": "650", "profit_per_day": "733.37", "profit_flat_under_sheets": 6,
            "sizes": ["4x8", "4x10"], "scope_template": "Panels, {sheets} sheets of {sizes}.", "notes": ["A made-up note."]}

print("a missing file means nothing is set")
rec, scope, problem = state(ALICE)
st, g = call("GET", "/api/wallpanels/settings", ALICE)
check(scope == "none" and problem is None and rec["labor_rate"] is None and st == 200 and g["scope"] == "none" and not g.get("problem"),
      "no file: every rate reads not set, and no problem is said", (scope, problem, g.get("scope"), g.get("problem")))
check(not S.store_path().exists(), "and reading made no file")

for who, rate in ((ALICE, "11.11"), (BOB, "22.22"), (STAFF, "33.33")):
    st, x = call("POST", "/api/wallpanels/settings", who, {"settings": dict(SETTINGS, labor_rate=rate), "rev": 0})
    check(st == 200, f"{who} saves a first record", (st, x))

print("two accounts saving at once, hundreds of saves: none lost, none refused")
N = 150
tally = {ALICE: {"ok": 0, "bad": [], "lost": 0}, BOB: {"ok": 0, "bad": [], "lost": 0}}


def saver(who, base):
    rev = stored(who)["rev"]
    for i in range(N):
        val = f"{base + i}.00"
        st, x = call("POST", "/api/wallpanels/settings", who, {"settings": dict(SETTINGS, labor_rate=val), "rev": rev})
        if st != 200:
            tally[who]["bad"].append((st, str(x)[:120]))
            rev = (stored(who) or {}).get("rev", rev)
            continue
        tally[who]["ok"] += 1
        rev = x["settings"]["rev"]
        now = stored(who)                    # nobody else writes this account: what was just saved must be there
        if not now or now.get("labor_rate") != val:
            tally[who]["lost"] += 1


ts = [threading.Thread(target=saver, args=(ALICE, 1000)), threading.Thread(target=saver, args=(BOB, 5000))]
for t in ts:
    t.start()
for t in ts:
    t.join()
check(tally[ALICE]["ok"] == N and tally[BOB]["ok"] == N and not tally[ALICE]["bad"] and not tally[BOB]["bad"],
      f"all {2 * N} saves were answered 200 (none refused, none a 500)", {k: (v["ok"], v["bad"][:2]) for k, v in tally.items()})
check(tally[ALICE]["lost"] == 0 and tally[BOB]["lost"] == 0,
      "every save answered 200 was in the store a moment later (none put back by the other account's write)",
      {k: v["lost"] for k, v in tally.items()})
a, b, s_ = stored(ALICE), stored(BOB), stored(STAFF)
check(bool(a) and bool(b) and a["rev"] == N + 1 and b["rev"] == N + 1 and a["labor_rate"] == f"{1000 + N - 1}.00" and b["labor_rate"] == f"{5000 + N - 1}.00",
      f"both records stand at rev {N + 1} with the last value each account saved", (a and (a["rev"], a["labor_rate"]), b and (b["rev"], b["labor_rate"])))
check(bool(s_) and s_["labor_rate"] == "33.33" and s_["rev"] == 1, "and the third account's record was not touched", s_)

print("one account saving while another reads: the reader never sees 'nothing set'")
call("POST", "/api/wallpanels/settings", BOB, {"settings": dict(SETTINGS, labor_rate="22.22"), "rev": stored(BOB)["rev"]})
stop = {"v": False}
seen = {"reads": 0, "none": 0, "bad": 0}


def reader():
    while not stop["v"]:
        st, g = call("GET", f"/api/job/{BOBJ}/wallpanels", BOB)
        seen["reads"] += 1
        if st != 200:
            seen["bad"] += 1
        elif g["settings_scope"] != "account" or g["rates"]["labor_rate"] != "22.22":
            seen["none"] += 1


t = threading.Thread(target=reader)
t.start()
rev, bad = stored(ALICE)["rev"], []
for i in range(N):
    st, x = call("POST", "/api/wallpanels/settings", ALICE, {"settings": dict(SETTINGS, labor_rate=f"{50 + i}.00"), "rev": rev})
    if st == 200:
        rev = x["settings"]["rev"]
    else:
        bad.append((st, str(x)[:120]))
        rev = (stored(ALICE) or {}).get("rev", rev)
stop["v"] = True
t.join()
check(not bad, f"{N} saves beside a reader: none failed", bad[:3])
check(seen["reads"] > 0 and seen["none"] == 0 and seen["bad"] == 0,
      f"{seen['reads']} reads beside the saves: every one showed the reader its own rate", seen)

print("two more processes saving at the same time: none lost")
CHILD = ("import sys, os; sys.path.insert(0, sys.argv[1]); from takeoff_engine import wp_settings as S\n"
         "raw = {'labor_rate': '1.00', 'sizes': ['4x8']}\n"
         "for i in range(int(sys.argv[3])):\n"
         "    S.save(sys.argv[2], dict(raw, labor_rate=str(100 + i) + '.00'), by='child', at='now')\n")
M = 40
env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
kids = [subprocess.Popen([sys.executable, "-c", CHILD, str(ROOT), name, str(M)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for name in ("proc_one_synth", "proc_two_synth")]
rev, bad = stored(ALICE)["rev"], []
for i in range(M):
    st, x = call("POST", "/api/wallpanels/settings", ALICE, {"settings": dict(SETTINGS, labor_rate=f"{700 + i}.00"), "rev": rev})
    if st == 200:
        rev = x["settings"]["rev"]
    else:
        bad.append((st, str(x)[:120]))
        rev = (stored(ALICE) or {}).get("rev", rev)
outs = [(k.wait(timeout=300), (k.stdout.read() or b"").decode("utf-8", "replace")[-300:]) for k in kids]
p1, p2, a = stored("proc_one_synth"), stored("proc_two_synth"), stored(ALICE)
check(all(rc == 0 for rc, _o in outs) and not bad, "both processes and this one finished every save", (outs, bad[:2]))
check(bool(p1) and bool(p2) and p1["rev"] == M and p2["rev"] == M and p1["labor_rate"] == f"{100 + M - 1}.00" and p2["labor_rate"] == f"{100 + M - 1}.00"
      and bool(a) and a["labor_rate"] == f"{700 + M - 1}.00",
      f"each process's record stands at rev {M} with its last value, and this process's saves are all there",
      (p1 and (p1["rev"], p1["labor_rate"]), p2 and (p2["rev"], p2["labor_rate"]), a and a["labor_rate"]))
check(stored(BOB)["labor_rate"] == "22.22" and stored(STAFF)["labor_rate"] == "33.33", "and the other accounts' records are as they were")

print("a save touches one record: every other record goes back as it was read")
f = S.store_path()
raw = json.loads(f.read_text(encoding="utf-8"))
raw["accounts"][BOB]["kept_by_hand"] = "a key a later build might add"
raw["accounts"][STAFF]["hotel_nightly"] = "12.345"            # a value changed by hand that the reader can not take
f.write_text(json.dumps(raw, indent=1, sort_keys=True), encoding="utf-8")
st, x = call("POST", "/api/wallpanels/settings", ALICE, {"settings": dict(SETTINGS, labor_rate="77.77"), "rev": stored(ALICE)["rev"]})
check(st == 200 and stored(ALICE)["labor_rate"] == "77.77", "Alice saves hers", (st, x))
check((stored(BOB) or {}).get("kept_by_hand") == "a key a later build might add" and (stored(STAFF) or {}).get("hotel_nightly") == "12.345",
      "Bob's and staff's records are in the file exactly as they were (not rewritten through the reader)",
      ((stored(BOB) or {}).get("kept_by_hand"), (stored(STAFF) or {}).get("hotel_nightly")))
st, g = call("GET", "/api/wallpanels/settings", STAFF)
check(st == 200 and g["settings"]["hotel_nightly"] is None and any("hotel_nightly" in u for u in g["settings"].get("unreadable") or []),
      "a value the reader can not take reads not set AND is named as unreadable - never passed off as never set", g["settings"].get("unreadable"))
raw["accounts"][STAFF]["hotel_nightly"] = "141.19"
raw["accounts"][ALICE] = stored(ALICE)
f.write_text(json.dumps(raw, indent=1, sort_keys=True), encoding="utf-8")

print("a damaged file is never 'nothing set'")
good = f.read_bytes()
damaged = good.rstrip()[:-1] + b",}\n"                          # a stray comma made by hand
f.write_bytes(damaged)
rec, scope, problem = state(BOB)
check(scope == "damaged" and bool(problem), "the store's own answer: damaged, with the reason - not 'none'", (scope, problem))
st, g = call("GET", "/api/wallpanels/settings", BOB)
check(st == 200 and g.get("scope") == "damaged" and "could not be read" in str(g.get("problem")) and "tell us" in str(g.get("problem")),
      "Settings says so in a sentence (Bob does not read 'nothing set')", (st, g.get("scope"), g.get("problem")))
check("wallpanels.json" not in str(g.get("problem")), "an account is not told the server's file names", g.get("problem"))
st, gs = call("GET", "/api/wallpanels/settings", STAFF)
check("wallpanels.json" in str(gs.get("problem")), "staff are told which file it is", gs.get("problem"))
st, g = call("GET", f"/api/job/{BOBJ}/wallpanels", BOB)
check(st == 200 and "could not be read" in str(g.get("settings_problem")) and any("could not be read" in h for h in g["computed"]["hold"])
      and not g["computed"]["issue"], "the job's reply says so, and the proposal is held with that reason", (g.get("settings_problem"), g["computed"]["hold"][:3]))
st, x = call("POST", "/api/wallpanels/settings", ALICE, {"settings": dict(SETTINGS, labor_rate="88.88")})
check(st == 503 and "Nothing was saved" in str(x.get("detail")) and "could not be read" in str(x.get("detail")), "a save is refused in words", (st, x))
check(f.read_bytes() == damaged, "and the file is as it was: nothing was written over it (Bob's and staff's records are still in it)")
kept = [p for p in f.parent.iterdir() if p.name.startswith(f.name + ".damaged-")]
check(len(kept) == 1 and kept[0].read_bytes() == damaged, "a copy of the damaged file was kept beside it under another name", [p.name for p in kept])
st, x2 = call("POST", "/api/wallpanels/settings", STAFF, {"settings": dict(SETTINGS, labor_rate="99.99"), "account": BOB})
check(st == 503 and kept and kept[0].name in str(x2.get("detail")) and len([p for p in f.parent.iterdir() if ".damaged-" in p.name]) == 1,
      "staff's refused save names the copy; the same damage is kept once, not once a try", (st, x2))
st, w = call("POST", f"/api/job/{BOBJ}/wallpanels/act", BOB, {"version": 0, "act": {"kind": "room_add", "name": "Kitchen"}})
check(st == 200 and w.get("ok") and w["version"] == 1, "the job itself still takes what a person does (a room is added)", (st, str(w)[:200]))
st, x = call("POST", f"/api/job/{BOBJ}/wallpanels/act", BOB, {"version": 1, "act": {"kind": "rates_take"}})
check(st == 422 and "could not be read" in str(x.get("detail")), "but an act that reads the settings is refused in words (never 'nothing set' taken as rates)", (st, x))
for what, body in (("an empty file", b""), ("a file that is a list", b"[]"), ("accounts that are a list", b'{"accounts": []}'),
                   ("a record that is not a record", b'{"accounts": {"bob_synth": 5}}'), ("bytes that are not text", b"\xff\xfe\x00{")):
    f.write_bytes(body)
    check(state(BOB)[1] == "damaged", f"{what}: damaged, not 'nothing set'", state(BOB)[1:])
f.write_bytes(good)                                           # mended by a person
rec, scope, problem = state(BOB)
st, x = call("POST", "/api/wallpanels/settings", ALICE, {"settings": dict(SETTINGS, labor_rate="88.88"), "rev": stored(ALICE)["rev"]})
check(scope == "account" and problem is None and rec["labor_rate"] == "22.22" and st == 200 and stored(ALICE)["labor_rate"] == "88.88"
      and stored(BOB)["labor_rate"] == "22.22", "mended by a person: Bob's rate reads again, and a save goes through", (scope, problem, st))

print("the terminal loader holds to the same rules")
f.write_bytes(damaged)
src = _JOBS / "to-load.json"
src.write_text(json.dumps({"labor_rate": "5.00"}), encoding="utf-8")
r = subprocess.run([sys.executable, "-m", "takeoff_engine.wp_settings", "load", str(src), "--account", BOB], cwd=str(ROOT), env=env,
                   capture_output=True, text=True, timeout=120)
check(r.returncode == 1 and "nothing was written" in r.stdout and f.read_bytes() == damaged,
      "loading into a damaged store is refused and the file is left as it is", (r.returncode, r.stdout[-300:], r.stderr[-200:]))
f.write_bytes(good)
r = subprocess.run([sys.executable, "-m", "takeoff_engine.wp_settings", "load", str(src), "--account", BOB], cwd=str(ROOT), env=env,
                   capture_output=True, text=True, timeout=120)
check(r.returncode == 0 and stored(BOB)["labor_rate"] == "5.00" and stored(STAFF)["labor_rate"] == "33.33",
      "into a sound store it loads one account's record and leaves the others", (r.returncode, r.stdout[-200:]))

print(f"\n{fails} FAILED" if fails else "\nall passed")
sys.exit(1 if fails else 0)
