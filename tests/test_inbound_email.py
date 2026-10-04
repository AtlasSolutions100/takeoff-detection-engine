"""INVITATIONS TO BID ARRIVE BY E-MAIL AND OPEN AS PROJECTS (2026-10-03): the rulings, one test each.

The mail service POSTs every inbound message as JSON to POST /api/inbound/postmark (server.py; the rules are in
takeoff_engine/inbound.py). Every request here goes through the real middleware (the ASGI driver), the messages
are hand-made in the mail service's documented shape, and Detect runs on the REAL scheduler with stand-in
children (tests/_inbound_harness.py). Nothing in this file is a client's: the address is the placeholder
0123456789abcdef0123456789abcdef@inbound.postmarkapp.com, the people are *.example, the keywords are made up.

   1  the door: 404 while off, 401 on wrong credentials, 200 before the work, no session and no page marker
   2  whose mail it is: the tag (MailboxHash) alone; an unknown tag opens nothing; a new address kills the old; no viewer
   3  what is kept: PDF plans (attached, or in a zip read safely); drawings open, text does not, each with a reason
   4  what is not: the message text is never stored and nothing leaves the box; of a forwarding confirmation only
      its code and its link are kept
   5  once only: the same MessageID, or the same bytes within a day, opens nothing new and says so (a project
      deleted since is no reason to refuse the file)
   6  Detect by itself, capped, one e-mailed page at a time (the person-first rule is tests/test_inbound_sched.py)
   7  limits said out loud: message size, pages per PDF, messages a day - recorded with the reason, never dropped
   9  privacy: another account learns nothing; every reply is an allow-list
  10  the activity log: a line per message, per project, per file refused
  11  docker-compose.yml: the three settings, empty by default
  13  keyword rules: found -> opened with the pages; none found / excluded -> held, "Open anyway"; kept with the project

What an outside review of this found, and the rulings on it, are tests/test_inbound_critic1.py.

    python tests/test_inbound_email.py
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
_JOBS = Path(tempfile.mkdtemp(prefix="inb_mail_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(_JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="inb_mail_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
for _k in ("INBOUND_HOOK_USER", "INBOUND_HOOK_PASS", "INBOUND_ADDRESS"):
    os.environ.pop(_k, None)                         # the feature starts OFF

import _inbound_harness as H  # noqa: E402
import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

try:
    from takeoff_engine import inbound as I  # noqa: E402
except ImportError:                                  # the commit before the feature: every test below fails, none crashes
    I = None

server._classify_async = lambda *a, **k: None        # page kinds are not what is tested here
server._INBOUND_KINDS_WAIT_S = 0
H.install_fake_children(server)

ALICE, BOB, STAFF, VIEW = "alice@builder.example", "bob@rival.example", "staff@core.example", "vera@builder.example"
_users.save({ALICE: _users.hash_password("orchid-lantern-4471"), BOB: _users.hash_password("basalt-meadow-9083"),
             STAFF: dict(_users.hash_password("granite-sparrow-2210"), staff=True),
             VIEW: dict(_users.hash_password("copper-thistle-5528"), role="viewer")})
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in (ALICE, BOB, STAFF, VIEW)}
DOOR = "/api/inbound/postmark"

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
    except Exception as e:                           # noqa: BLE001 - on the commit before the feature every test lands here
        check(False, f"{fn.__name__}: raised {type(e).__name__}: {str(e)[:200]}")


def on():
    os.environ.update({"INBOUND_HOOK_USER": H.HOOK_USER, "INBOUND_HOOK_PASS": H.HOOK_PASS, "INBOUND_ADDRESS": H.BASE_ADDRESS})


def off():
    for k in ("INBOUND_HOOK_USER", "INBOUND_HOOK_PASS", "INBOUND_ADDRESS"):
        os.environ.pop(k, None)


def post(pl, auth=...):
    """POST a message to the door the way the mail service does: Basic credentials, no cookie, no page marker."""
    st, j = H.call(server, "POST", DOOR, pl, auth=H.basic() if auth is ... else auth, marker=False)
    return st, j


def read_all():
    """Wait for the reader to finish everything the door has taken in."""
    server._INBOUND_Q.join()


def inbox(who):
    return H.call(server, "GET", "/api/inbound", cookie=CK[who])


def address(who):
    st, j = H.call(server, "POST", "/api/inbound/address", {}, cookie=CK[who])
    return j.get("address") if st == 200 else None


def tag_of(addr):
    return addr.split("+", 1)[1].split("@", 1)[0]


def projects(who):
    st, j = H.call(server, "GET", "/api/projects", cookie=CK[who])
    return [r for r in j.get("projects", [])] if st == 200 else []


def audit_lines():
    out = []
    for f in sorted((_JOBS / "audit").glob("audit-*.jsonl")) if (_JOBS / "audit").is_dir() else []:
        out += [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return out


def store_bytes() -> bytes:
    """Every byte under the job store except the PDFs themselves - what a stored message text would be found in."""
    out = b""
    for f in sorted(_JOBS.rglob("*")):
        if f.is_file() and f.suffix.lower() not in (".pdf", ".bin", ".png", ".key"):
            out += f.read_bytes() + b"\n"
    return out


def newest(who):
    st, j = inbox(who)
    return (j.get("entries") or [None])[0]


def spool_empty() -> bool:
    d = _JOBS / "inbound" / "spool"
    return not d.exists() or not any(d.iterdir())


def send(tag, files=(), **kw):
    pl = H.payload(tag, files, **kw)
    st, j = post(pl)
    read_all()
    return st, j, pl


def set_rules(who, keywords=(), exclude=()):
    st, j = inbox(who)
    v = (j.get("rules") or {}).get("v")
    return H.call(server, "POST", "/api/inbound/rules", {"keywords": list(keywords), "exclude": list(exclude), "v": v},
                  cookie=CK[who])


DRAW = H.drawing_pdf(2)
TEXT = H.text_pdf(3)


# =====================================================================================================================
def r1_the_door():
    off()
    # (the redesign: its sign-in middleware keeps each account's last-seen time in user_activity.json - users.note_seen -
    # written by the first signed-in request below. It is the frame's own record and nothing of the e-mail feature's,
    # so it is left out of "what was written" here.)
    stored = lambda: sorted(p.name for p in _JOBS.iterdir() if p.name != "user_activity.json")      # noqa: E731
    before = stored()
    st, j = post(H.payload("0" * 26, [("plans.pdf", DRAW)]))
    check(st == 404 and j == {"detail": "Not Found"}, "1: feature off: the door answers 404, like a route that is not there", (st, j))
    st2, j2 = inbox(ALICE)
    st3, _ = H.call(server, "POST", "/api/inbound/address", {}, cookie=CK[ALICE])
    st4, _ = H.call(server, "POST", "/api/inbound/rules", {"keywords": ["quartzline"]}, cookie=CK[ALICE])
    check(st2 == 404 and st3 == 404 and st4 == 404, "1: feature off: the signed-in e-mail routes answer 404 too", (st2, st3, st4))
    check(stored() == before and not (_JOBS / "inbound").exists(),
          "1: feature off: nothing is written", sorted(p.name for p in _JOBS.iterdir()))
    for missing in ("INBOUND_HOOK_USER", "INBOUND_HOOK_PASS", "INBOUND_ADDRESS"):
        on()
        os.environ[missing] = ""
        st, _ = post(H.payload("0" * 26))
        check(st == 404, f"1: {missing} empty: off (404)", st)
    on()
    pl = H.payload("0" * 26, [("plans.pdf", DRAW)])
    n_projects = len(server.JOBS)
    for label, auth in (("no credentials", None), ("a wrong password", H.basic(pw="wrong")),
                        ("a wrong name", H.basic(user="nobody")), ("not Basic", "Bearer " + H.HOOK_PASS),
                        ("garbage", "Basic !!!not-base64!!!")):
        st, j = post(pl, auth=auth)
        check(st == 401, f"1: {label}: 401", (st, j))
    st, j = H.call(server, "POST", DOOR, pl, cookie=CK[STAFF], marker=True)
    check(st == 401, "1: a signed-in staff session is not the mail service: 401 without the credentials", (st, j))
    check(len(server.JOBS) == n_projects and not (_JOBS / "inbound" / "mail").exists(), "1: a 401 reads and creates nothing")
    st, j = post(pl)
    check(st == 200 and j == {"ok": True}, "1: the right credentials, no session, no page marker: 200", (st, j))
    st, j = H.call(server, "POST", DOOR, b"this is not json", auth=H.basic(), marker=False)
    check(st == 400, "1: a body that is not a message: 400, nothing read", (st, j))
    old = I.MAX_BODY_BYTES
    I.MAX_BODY_BYTES = 2000
    try:
        st, j = post(H.payload("0" * 26, [("plans.pdf", DRAW)]))
    finally:
        I.MAX_BODY_BYTES = old
    check(st == 413, "1: a body past the ceiling is not read: 413", (st, j))
    src = (ROOT / "takeoff_engine" / "inbound.py").read_text(encoding="utf-8")
    body = src[src.index("def authorized("):src.index("# ---- where things live")]
    check(body.count("hmac.compare_digest(") == 2 and "==" not in body.split('"""')[2],
          "1: both halves of the credentials are compared with hmac.compare_digest, and nothing with ==")
    # 200 BEFORE the work: the door answers while the reader has not touched the message yet
    tag = tag_of(address(ALICE))
    gate = threading.Event()
    real = server._inbound_read

    def slow(account, eid):
        gate.wait(10)
        real(account, eid)

    server._inbound_read = slow
    try:
        t0 = time.time()
        st, j = post(H.payload(tag, [("held-up.pdf", H.drawing_pdf(1))]))
        took = time.time() - t0
        e = newest(ALICE)
        check(st == 200 and e is not None and e["status"] == "received" and e["pending"] == ["held-up.pdf"] and not e["files"],
              f"1: 200 in {took * 1000:.0f} ms with the message on record and nothing read yet (the work comes after)", (st, e))
    finally:
        gate.set()
        server._inbound_read = real
    read_all()
    e = newest(ALICE)
    check(e["status"] == "done" and e["files"] and e["files"][0]["state"] == "opened", "1: ... and the reader then opens it", e)
    H.drain(server)


def r2_whose_mail():
    on()
    st, j = inbox(BOB)
    check(st == 200 and j["enabled"] is True and j["address"] is None and j["can_address"] is True
          and not (_JOBS / "inbound" / "addresses.json").read_text().count(BOB),
          "2: an account has no address until it asks for one; reading the page writes nothing", j)
    a, b = address(ALICE), address(BOB)
    ta, tb = tag_of(a), tag_of(b)
    check(a == f"{H.BASE_LOCAL}+{ta}@{H.BASE_DOMAIN}" and re.fullmatch(r"[a-z0-9]{26}", ta) is not None,
          "2: the address is the base address with +<tag> before the @; the tag is 26 lower-case hex characters = 104 bits", a)
    check(ta != tb and I.TAG_BYTES * 8 >= 100, "2: two accounts, two different tags; at least 100 random bits", (ta, tb))
    n = len(server.JOBS)
    st, _j, _pl = send(ta, [("for-alice.pdf", H.drawing_pdf(1))])
    mine = [r for r in projects(ALICE) if r["name"] == "for-alice.pdf"]
    check(st == 200 and len(mine) == 1 and mine[0]["owner"] == ALICE and len(server.JOBS) == n + 1,
          "2: mail to alice's tag opens a project owned by alice", mine)
    # the tag alone: the To and ToFull lines name BOB; MailboxHash, and the address it was delivered to, are alice's
    # (the two disagreeing, and an empty MailboxHash, are tests/test_inbound_critic1.py's)
    pl = H.payload(ta, [("hash-decides.pdf", H.drawing_pdf(1))])
    pl["To"] = f"{H.BASE_LOCAL}+{tb}@{H.BASE_DOMAIN}"
    pl["ToFull"] = [{"Email": pl["To"], "Name": "", "MailboxHash": tb}]
    post(pl)
    read_all()
    check(any(r["name"] == "hash-decides.pdf" for r in projects(ALICE))
          and not any(r["name"] == "hash-decides.pdf" for r in projects(BOB)),
          "2: the account is found from the tag alone (MailboxHash), whatever the To lines say")
    n = len(server.JOBS)
    pl = H.payload(ta, [("no-hash.pdf", H.drawing_pdf(1))], mailbox_hash="")
    pl["OriginalRecipient"] = f"{H.BASE_LOCAL}@{H.BASE_DOMAIN}"
    post(pl)
    read_all()
    check(len(server.JOBS) == n, "2: alice's address on the To line alone (an empty MailboxHash, and not the address "
                                 "the message was delivered to) opens nothing")
    post(H.payload(ta.upper(), [("upper.pdf", H.drawing_pdf(1))]))
    read_all()
    check(any(r["name"] == "upper.pdf" for r in projects(ALICE)), "2: a tag that arrives in capitals still finds its account")
    n, lines = len(server.JOBS), len(audit_lines())
    st, j = post(H.payload("f" * 26, [("stray.pdf", H.drawing_pdf(1))], subject="STRAY-SUBJECT"))
    read_all()
    new = audit_lines()[lines:]
    check(st == 200 and j == {"ok": True} and len(server.JOBS) == n and spool_empty()
          and [x["action"] for x in new] == ["inbound_unknown_address"] and b"STRAY-SUBJECT" not in store_bytes(),
          "2: an unknown tag: 200, nothing created, nothing of the message kept, one log line", (st, j, new))
    a2 = address(ALICE)
    check(a2 != a and tag_of(a2) != ta, "2: New address gives a different tag", (a, a2))
    n = len(server.JOBS)
    post(H.payload(ta, [("old-tag.pdf", H.drawing_pdf(1))]))
    read_all()
    check(len(server.JOBS) == n, "2: the old tag stops working at once")
    post(H.payload(tag_of(a2), [("new-tag.pdf", H.drawing_pdf(1))]))
    read_all()
    check(any(r["name"] == "new-tag.pdf" for r in projects(ALICE)), "2: the new tag works")
    st, j = inbox(VIEW)
    st2, j2 = H.call(server, "POST", "/api/inbound/address", {}, cookie=CK[VIEW])
    check(st == 200 and j["address"] is None and j["can_address"] is False and j["rules"] is None and st2 == 403,
          "2: a view-only account has no address and cannot ask for one", (j, st2, j2))
    # an account that had a tag and was made view-only, or removed: its tag opens nothing
    store = dict(_users.load())
    store["temp@builder.example"] = _users.hash_password("walnut-harbor-3318")
    _users.save(store)
    tt = I.new_address("temp@builder.example")
    check(I.account_for(tt) == "temp@builder.example", "2: (a third account's tag resolves)")
    store = dict(_users.load())
    store["temp@builder.example"] = dict(store["temp@builder.example"], role="viewer")
    _users.save(store)
    was_viewer = I.account_for(tt)
    store = dict(_users.load())
    store.pop("temp@builder.example")
    _users.save(store)
    check(was_viewer is None and I.account_for(tt) is None,
          "2: a tag whose account became view-only, or was removed, opens nothing", (was_viewer, I.account_for(tt)))
    H.drain(server)


def r3_what_is_kept():
    on()
    ta = tag_of(address(ALICE))
    n = len(server.JOBS)
    send(ta, [("site-plans.pdf", DRAW), ("project-manual.pdf", TEXT), ("logo.png", b"\x89PNG\r\n\x1a\n" + b"x" * 40),
              ("notes.txt", b"call me"), ("broken.pdf", b"%PDF-1.7\n" + b"cut short and scrambled " * 80),
              ("fake.pdf", b"this is not a pdf at all"), ("locked.pdf", H.locked_pdf()),
              ("letter.docx", b"PK\x03\x04" + b"a word file is a zip inside")], subject="R3 one message, eight files")
    e = newest(ALICE)
    by = {f["name"]: f for f in e["files"]}
    check(len(server.JOBS) == n + 1 and by["site-plans.pdf"]["state"] == "opened" and by["site-plans.pdf"].get("job"),
          "3: of eight attachments only the drawing set opens, as one project", {k: v["state"] for k, v in by.items()})
    check(server.JOBS[by["site-plans.pdf"]["job"]]["owner"] == ALICE, "3: the project is owned by the account the mail was for")
    check("text document" in (by["project-manual.pdf"]["reason"] or "") and "3 pages" in by["project-manual.pdf"]["reason"],
          "3: a text PDF is listed with the reason and its numbers", by["project-manual.pdf"])
    check(by["logo.png"]["reason"] == "not a PDF" and by["notes.txt"]["reason"] == "not a PDF" and by["letter.docx"]["reason"] == "not a PDF",
          "3: files that are not PDFs are listed by name with the reason (a Word file is not unpacked as a zip)",
          (by["logo.png"], by["notes.txt"], by["letter.docx"]))
    check(("damaged" in (by["broken.pdf"]["reason"] or "") or "no pages" in (by["broken.pdf"]["reason"] or ""))
          and "not a PDF" in (by["fake.pdf"]["reason"] or "") and "password" in (by["locked.pdf"]["reason"] or "")
          and all(by[k]["state"] == "refused" for k in ("broken.pdf", "fake.pdf", "locked.pdf")),
          "3: a PDF that PyMuPDF cannot open (damaged, not a PDF inside, password-protected) is refused with the reason",
          (by["broken.pdf"]["reason"], by["fake.pdf"]["reason"], by["locked.pdf"]["reason"]))
    kept = [f.name for f in _JOBS.rglob("*") if f.is_file()]
    check(not any(w in k for k in kept for w in ("project-manual", "logo", "notes", "broken", "fake", "locked", "letter"))
          and spool_empty() and not list((_JOBS / "inbound").rglob("*.bin")),
          "3: nothing but the plan is kept: no refused file is on disk and the spool is empty")
    # each drawing PDF is its own project
    n = len(server.JOBS)
    send(ta, [("civil.pdf", H.drawing_pdf(1, title="CIVIL")), ("structural.pdf", H.drawing_pdf(1, title="STRUCTURAL"))])
    e = newest(ALICE)
    jobs = {f["job"] for f in e["files"] if f["state"] == "opened"}
    check(len(server.JOBS) == n + 2 and len(jobs) == 2, "3: two drawing PDFs in one message: two projects", e["files"])
    # the bid chooser's fallback: no drawings file -> the largest opens, unless it reads as text
    n = len(server.JOBS)
    send(ta, [("bid-package.pdf", H.mixed_pdf(3)), ("cover-letter.pdf", H.text_pdf(1))])
    by = {f["name"]: f for f in newest(ALICE)["files"]}
    check(len(server.JOBS) == n + 1 and by["bid-package.pdf"]["state"] == "opened" and by["cover-letter.pdf"]["state"] == "refused",
          "3: no drawings file: the largest (a mixed package) opens, the text file does not", by)
    n = len(server.JOBS)
    send(ta, [("sheets.pdf", H.drawing_pdf(1, title="SHEETS")), ("addendum.pdf", H.mixed_pdf(3, extra="addendum"))])
    by = {f["name"]: f for f in newest(ALICE)["files"]}
    check(len(server.JOBS) == n + 1 and by["sheets.pdf"]["state"] == "opened" and by["addendum.pdf"]["state"] == "refused"
          and "1 of 4 pages" in by["addendum.pdf"]["reason"],
          "3: with a drawings file present a mostly-text file is not opened, and says how many pages are sheets", by["addendum.pdf"])
    n = len(server.JOBS)
    send(ta, [("only-text.pdf", H.text_pdf(2, extra="alone"))])
    e = newest(ALICE)
    check(len(server.JOBS) == n and e["files"][0]["state"] == "refused" and "text document" in e["files"][0]["reason"],
          "3: a text document alone never opens (nobody is there to untick it)", e["files"])
    # a zip: read in memory, names are never paths
    outside = _JOBS.parent / "evil-outside.pdf"
    z = H.make_zip({"set/C1 grading.pdf": H.drawing_pdf(1, title="ZIP ONE"), "../../evil-outside.pdf": H.drawing_pdf(1, title="ZIP TWO"),
                    "C:\\Windows\\win-path.pdf": H.drawing_pdf(1, title="ZIP THREE"), "readme.txt": b"hello",
                    "__MACOSX/._C1 grading.pdf": b"junk", "inner.zip": H.make_zip({"x.pdf": DRAW})})
    n = len(server.JOBS)
    send(ta, [("bundle.zip", z)], subject="R3 a zip")
    e = newest(ALICE)
    by = {f["name"]: f for f in e["files"]}
    opened = sorted(f["name"] for f in e["files"] if f["state"] == "opened")
    check(len(server.JOBS) == n + 3 and opened == ["C1 grading.pdf", "evil-outside.pdf", "win-path.pdf"]
          and all(f["zip"] == "bundle.zip" for f in e["files"]),
          "3: the PDFs inside a zip open, each named by the last part of its name only", (opened, list(by)))
    paths = [Path(server.JOBS[f["job"]]["path"]) for f in e["files"] if f["state"] == "opened"]
    check(all(p.parent == _JOBS and p.name.startswith("upload_") for p in paths) and not outside.exists()
          and not list(_JOBS.parent.glob("evil-outside*")) and not any(_JOBS.rglob("win-path.pdf")),
          "3: no path from a zip is used: every file lands in the job store under its upload name, nothing outside", paths)
    check(by["readme.txt"]["reason"] == "not a PDF" and "zip inside the zip" in by["inner.zip"]["reason"]
          and not any("MACOSX" in k or k.startswith("._") for k in by),
          "3: other zip members are listed with the reason; a zip inside a zip is not opened", by)
    old = (I.ZIP_MAX_FILES, I.ZIP_MAX_BYTES)
    try:
        I.ZIP_MAX_FILES = 2
        n = len(server.JOBS)
        send(ta, [("many.zip", H.make_zip({f"s{i}.pdf": H.drawing_pdf(1, title=f"MANY {i}") for i in range(3)}))])
        f = newest(ALICE)["files"]
        check(len(server.JOBS) == n and len(f) == 1 and "3 files" in f[0]["reason"] and "limit is 2" in f[0]["reason"],
              "3: a zip with more files than the cap is refused whole, with the numbers", f)
        I.ZIP_MAX_FILES, I.ZIP_MAX_BYTES = old[0], 1024 * 1024
        n = len(server.JOBS)
        send(ta, [("bomb.zip", H.make_zip({"big.pdf": b"%PDF-1.4\n" + b"0" * (3 * 1024 * 1024)}))])
        f = newest(ALICE)["files"]
        check(len(server.JOBS) == n and len(f) == 1 and "unpacks to more than 1 MB" in f[0]["reason"],
              "3: a zip that unpacks past the size cap is refused whole (a 3 MB member in a few KB of zip)", f)
    finally:
        I.ZIP_MAX_FILES, I.ZIP_MAX_BYTES = old
    send(ta, [("corrupt.zip", b"PK\x03\x04" + b"not really a zip")])
    check("could not be read" in newest(ALICE)["files"][0]["reason"], "3: a zip that cannot be read is listed with the reason")
    # the two shared plan sets, when the corpus is on this machine
    c8, bf = H.shared_plan("C8.0-STORM-LAYOUT-Rev.1.pdf"), H.shared_plan("Belle Farms Structural Plans.pdf")
    if c8 is None or bf is None:
        print("skip: the shared plan sets (Loves Lane civil sheets, Belle Farms) are not on this machine")
    else:
        n = len(server.JOBS)
        send(ta, [(c8.name, c8.read_bytes()), (bf.name, bf.read_bytes())], subject="R3 the shared plan sets")
        by = {f["name"]: f for f in newest(ALICE)["files"]}
        check(len(server.JOBS) == n + 2 and by[c8.name]["state"] == "opened" and by[bf.name]["state"] == "opened"
              and by[c8.name]["pages"] == 1 and by[bf.name]["pages"] == 3,
              "3: a Loves Lane civil sheet (1 page) and Belle Farms Structural (3 pages) each open as a project", by)
        check(by[c8.name]["auto"]["queued"] == 1 and by[bf.name]["auto"]["queued"] == 3,
              "6: Detect is queued on every page of both (the storm layout reads as site work, the structural sheets as plans)",
              (by[c8.name]["auto"], by[bf.name]["auto"]))
    H.drain(server)


def r4_what_is_not_kept():
    on()
    ta = tag_of(address(ALICE))
    import socket
    import urllib.request

    import requests
    calls = []
    real = (socket.getaddrinfo, socket.create_connection, requests.Session.request, urllib.request.urlopen)

    def no_net(*a, **k):
        calls.append(str(a[:2]))
        raise OSError("no network in this test")

    # every way out by name or by URL (a host has to be looked up before anything is sent to it)
    socket.getaddrinfo = socket.create_connection = requests.Session.request = urllib.request.urlopen = no_net
    try:
        send(ta, [("quiet.pdf", H.drawing_pdf(1, title="QUIET"))], subject="R4 privacy",
             text="BODY-MARKER-TEXT the price is 4,200,000", html="<p>BODY-MARKER-HTML secret</p>")
        H.drain(server)
    finally:
        socket.getaddrinfo, socket.create_connection, requests.Session.request, urllib.request.urlopen = real
    e = newest(ALICE)
    check(e["files"][0]["state"] == "opened" and not calls,
          "4: a message is read, opened and detected with no network call at all (nothing can reach an AI provider)", calls)
    blob = store_bytes()
    check(b"BODY-MARKER" not in blob and b"4,200,000" not in blob,
          "4: the message text (TextBody, HtmlBody, StrippedTextReply) is in no file of the store")
    st, j = inbox(ALICE)
    check("BODY-MARKER" not in json.dumps(j), "4: ... and in no reply")
    rec = next(json.loads(f.read_text(encoding="utf-8")) for f in sorted((_JOBS / "inbound" / "mail").rglob("*.json"), reverse=True)
               if "R4 privacy" in f.read_text(encoding="utf-8"))
    check(rec["subject"] == "R4 privacy" and rec["from"] == "Pat Sender <pat@sender.example>" and rec["received"] > 0
          and rec["files"][0]["name"] == "quiet.pdf" and rec["files"][0]["state"] == "opened"
          and sorted(rec) == sorted(["v", "id", "account", "message_id", "received", "from", "subject", "status", "reason",
                                     "counted", "again", "more", "confirm", "excluded", "pending", "files",
                                     "attached", "unread", "unlisted"]),
          "4: stored per message: when, the sender, the subject, the file names and what became of each (and how many "
          "files were only counted) - nothing else", sorted(rec))
    # a mail provider's forwarding confirmation: its code and its link are kept, its text is not (the rest of that
    # rule - what a forged one can and cannot plant - is tests/test_inbound_critic1.py's)
    text = ("user@example.com has requested to automatically forward mail to your email address.\n"
            "Confirmation code: 716599842\n\nTo allow it, please click the link below to confirm the request:\n\n"
            "https://mail-settings.google.com/mail/vf-%5BANGjdJ8made-up%5D-token\n\nThanks for using Gmail!")
    pl = H.payload(ta, [], subject="(#716599842) Gmail Forwarding Confirmation - Receive Mail from user@example.com",
                   sender=("Gmail Team", "forwarding-noreply@google.com"), text=text, html="<p>html part</p>")
    post(pl)
    read_all()
    e = newest(ALICE)
    st, j = inbox(ALICE)
    c = j["confirm"]
    check(e["confirm"] is True and c is not None and c["provider"] == "Gmail" and "text" not in c
          and c["link"] == "https://mail-settings.google.com/mail/vf-%5BANGjdJ8made-up%5D-token" and c["code"] == "716599842",
          "4: a Gmail forwarding confirmation: its link and its code are kept so the person can finish forwarding", c)
    check(e["reason"] is None and "html part" not in json.dumps(j) and "Thanks for using Gmail" not in json.dumps(j)
          and b"Thanks for using Gmail" not in store_bytes(),
          "4: ... and none of its words; it is not called a message without a PDF", e)
    pl = H.payload(ta, [], subject="(#111222333) Gmail Forwarding Confirmation - Receive Mail from x@example.com",
                   sender=("Somebody Else", "someone@elsewhere.example"), text="LOOKALIKE-BODY click https://phish.example/x")
    post(pl)
    read_all()
    e = newest(ALICE)
    st, j = inbox(ALICE)
    check(e["confirm"] is False and b"LOOKALIKE-BODY" not in store_bytes() and j["confirm"]["code"] == "716599842"
          and "No PDF was attached" in e["reason"],
          "4: the same subject from any other sender is an ordinary message: its text is not kept", e)
    pl = H.payload(ta, [], sender=("Gmail Team", "forwarding-noreply@google.com"), subject="x",
                   text="LOOKALIKE-LINK go to https://evil.example/mail-settings.google.com/mail/vf-abc now")
    post(pl)
    read_all()
    st, j = inbox(ALICE)
    check(newest(ALICE)["confirm"] is False and j["confirm"]["code"] == "716599842"
          and "evil.example" not in json.dumps(j) and b"LOOKALIKE-LINK" not in store_bytes(),
          "4: only the provider's own confirmation page is ever offered as a link: a message from that sender with "
          "another link and no code keeps nothing", j["confirm"])


def r5_once_only():
    on()
    ta = tag_of(address(ALICE))
    raw = H.drawing_pdf(1, title="ONCE ONLY")
    n = len(server.JOBS)
    st, _j, pl = send(ta, [("once.pdf", raw)], subject="R5 first")
    e1 = newest(ALICE)
    job = e1["files"][0]["job"]
    total = inbox(ALICE)[1]["total"]
    lines = len(audit_lines())
    st, j = post(pl)                                  # the mail service delivers the SAME MessageID again
    read_all()
    e = newest(ALICE)
    new = [x["action"] for x in audit_lines()[lines:]]
    check(st == 200 and len(server.JOBS) == n + 1 and inbox(ALICE)[1]["total"] == total and e["id"] == e1["id"] and e["again"] == 1
          and new == ["inbound_again"],
          "5: the same MessageID again: 200, no new record, no new project, and the record says it came again", (e["again"], new))
    post(pl)
    read_all()
    check(newest(ALICE)["again"] == 2 and len(server.JOBS) == n + 1, "5: ... each time")
    # the same bytes in a NEW message, under another name, the same day
    send(ta, [("renamed copy.pdf", raw)], subject="R5 the same file again")
    e2 = newest(ALICE)
    f = e2["files"][0]
    check(len(server.JOBS) == n + 1 and e2["id"] != e1["id"] and f["state"] == "same" and f["job"] == job
          and "less than a day ago" in f["reason"],
          "5: the same bytes again within a day (another message, another name): nothing new, a link to the first project", f)
    # twice in ONE message (attached, and inside a zip)
    raw2 = H.drawing_pdf(1, title="TWICE IN ONE")
    send(ta, [("twice.pdf", raw2), ("twice.zip", H.make_zip({"twice-again.pdf": raw2}))])
    states = sorted(f["state"] for f in newest(ALICE)["files"])
    check(len(server.JOBS) == n + 2 and states == ["opened", "same"], "5: the same file twice in one message opens once", states)
    # the project DELETED, the same bytes again the same day: there is no project of it any more, so it opens again
    # (once only is about never making a second project; after the first review a deleted one is no longer a reason
    # to refuse the file for a day)
    H.drain(server)
    st, _ = H.call(server, "DELETE", f"/api/job/{job}", cookie=CK[ALICE])
    send(ta, [("once.pdf", raw)], subject="R5 after the delete")
    f = newest(ALICE)["files"][0]
    check(st == 200 and len(server.JOBS) == n + 2 and f["state"] == "opened" and f.get("job") not in (None, job),
          "5: the same bytes again the same day after the project was deleted: it opens again", f)
    again_job = f.get("job")
    earlier = next(x for x in inbox(ALICE)[1]["entries"] if x["subject"] == "R5 the same file again")["files"][0]
    check(earlier["state"] == "same" and earlier.get("gone") is True and "job" not in earlier,
          "5: ... and the earlier record that pointed at the deleted project says it is gone", earlier)
    # a day later the rule no longer applies - and while a project of it exists the same file still lands on that one
    old = I.SAME_FILE_S
    I.SAME_FILE_S = 0
    try:
        send(ta, [("once.pdf", raw)], subject="R5 a day later, and it is still there")
        f = newest(ALICE)["files"][0]
        check(len(server.JOBS) == n + 2 and f["state"] == "same" and f["job"] == again_job and "already one of your projects" in f["reason"],
              "5: after a day, while that project exists, the same file lands on it, never on a second one", f)
        H.drain(server)
        H.call(server, "DELETE", f"/api/job/{again_job}", cookie=CK[ALICE])
        send(ta, [("once.pdf", raw)], subject="R5 a day later")
        f = newest(ALICE)["files"][0]
        check(len(server.JOBS) == n + 2 and f["state"] == "opened", "5: ... and with no project of it left it opens a project again", f)
    finally:
        I.SAME_FILE_S = old
    # a file the account uploaded itself is left as it is: no note, no Detect
    mine = H.drawing_pdf(1, title="UPLOADED BY HAND")
    dest = _JOBS / f"upload_{hashlib.sha256(mine).hexdigest()[:12]}_by-hand.pdf"
    dest.write_bytes(mine)
    jid = server._register(dest, classify=False, owner=ALICE)["job_id"]
    send(ta, [("by-hand.pdf", mine)], subject="R5 a file already uploaded")
    f = newest(ALICE)["files"][0]
    check(f["state"] == "same" and f["job"] == jid and "inbound" not in server.JOBS[jid] and not server.JOBS[jid]["status"],
          "5: a project the account already had (its own upload) is left untouched: no e-mail note, no Detect", f)
    H.drain(server)


def r6_detect():
    on()
    ta = tag_of(address(ALICE))
    H.drain(server)
    server.TAKEOFF_WORKERS = 4
    send(ta, [("first.pdf", H.drawing_pdf(3, title="FIRST")), ("second.pdf", H.drawing_pdf(2, title="SECOND"))], subject="R6 detect")
    e = newest(ALICE)
    f1, f2 = (next(f for f in e["files"] if f["name"] == n) for n in ("first.pdf", "second.pdf"))
    bg = [k for k, v in H.running(server).items() if v.get("bg")]
    with server._SCHED:
        line = list(server._SCHED_BG)
    check(f1["auto"]["queued"] == 3 and f2["auto"]["queued"] == 2 and len(bg) == 1 and len(H.running(server)) == 1 and len(line) == 4,
          "6: Detect is queued by itself on every plan page of both projects; ONE page runs though four workers are free",
          (f1["auto"], f2["auto"], bg, line))
    check(bg[0][0] == f1["job"] and all(k[0] == f1["job"] for k in line[:2]) and all(k[0] == f2["job"] for k in line[2:]),
          "6: one e-mailed project at a time: the first project's pages, then the second's", (bg, line))
    st, j = inbox(ALICE)
    a1, a2 = j["entries"][0]["files"][0]["auto"], j["entries"][0]["files"][1]["auto"]
    check(j["busy"] is True and a1["running"] is True and a1["done"] == 0 and a1["waiting"] is False,
          "6: the list says the first project is detecting (0 of 3 so far)", a1)
    check(a2["running"] is True and a2["waiting"] is True and a2["done"] == 0,
          "6: ... and that the second is WAITING its turn, not detecting", a2)
    H.finish_running(server)
    H.wait_until(lambda: newest(ALICE)["files"][0]["auto"]["done"] == 1)
    check(newest(ALICE)["files"][0]["auto"]["done"] == 1, "6: ... 1 of 3 once the first page's result is on disk")
    H.drain(server)
    st, j = inbox(ALICE)
    a1, a2 = j["entries"][0]["files"][0]["auto"], j["entries"][0]["files"][1]["auto"]
    check(a1["done"] == 3 and a2["done"] == 2 and not a1["running"] and not a2["running"] and j["busy"] is False
          and all(st_ == "done" for st_ in server.JOBS[f1["job"]]["status"].values()),
          "6: Detect finishes by itself: 3 of 3 and 2 of 2, nothing left running", (a1, a2))
    card = next(r for r in projects(ALICE) if r["job_id"] == f1["job"])
    check(card["inbound"] == {"subject": "R6 detect", "at": card["inbound"]["at"], "queued": 3, "wanted": 3, "pages": 3,
                              "cap": I.AUTO_PAGES, "found": []} and card["detected"] == 3,
          "8: the project card carries the e-mail's subject and how many pages Detect ran on", card["inbound"])
    # a very large set: capped, and the card says how many were done
    old = I.AUTO_PAGES
    I.AUTO_PAGES = 2
    try:
        send(ta, [("large-set.pdf", H.drawing_pdf(5, title="LARGE SET"))], subject="R6 a large set")
        f = newest(ALICE)["files"][0]
        with server._SCHED:
            queued = [k for k in list(server._SCHED_BG) + list(server._SCHED_RUNNING) if k[0] == f["job"]]
        check(f["auto"]["queued"] == 2 and f["auto"]["wanted"] == 5 and f["auto"]["total"] == 5 and f["auto"]["cap"] == 2
              and sorted(k[1] for k in queued) == [0, 1],
              "6: a set over the cap: Detect is queued on the first 2 of 5 pages only, and the record says so", (f["auto"], queued))
        card = next(r for r in projects(ALICE) if r["job_id"] == f["job"])
        check(card["inbound"]["queued"] == 2 and card["inbound"]["wanted"] == 5 and card["inbound"]["pages"] == 5
              and card["inbound"]["cap"] == 2, "6: ... and so does the card (2 of 5, cap 2)", card["inbound"])
    finally:
        I.AUTO_PAGES = old
    # the pages the sheet triage calls another trade are left to the person; a scan (no text) is detected
    pg = I.auto_pages([{"page": 0, "tier": "other"}, {"page": 1, "tier": "maybe"}, {"page": 2, "tier": "core"},
                       {"page": 3, "tier": "unknown"}, {"page": 4, "tier": "plan"}], cap=3)
    check(pg == {"pages": [2, 4, 1], "wanted": 4, "total": 5, "cap": 3},
          "6: which pages: site work first, then building plans, then maybes, then pages with no text; never another trade's", pg)
    H.drain(server)


def r7_limits():
    on()
    ta = tag_of(address(BOB))
    st, j = inbox(BOB)
    check(j["limits"] == {"message_mb": I.MAX_MESSAGE_BYTES // 1048576, "pdf_pages": I.MAX_PDF_PAGES, "per_day": I.MAX_PER_DAY,
                          "pdfs": I.MAX_PDFS, "auto_pages": I.AUTO_PAGES, "held_days": I.HELD_DAYS,
                          "words": I.RULE_WORDS_MAX, "word_max": I.RULE_WORD_MAX,
                          "attachments": I.MAX_ATTACHMENTS, "unpacked_mb": I.MAX_UNPACKED_BYTES // 1048576}
          and j["limits"]["message_mb"] == 35,
          "7: the limits in force are in the page's own reply, so the page can say them", j["limits"])
    old = (I.MAX_MESSAGE_BYTES, I.MAX_PDF_PAGES, I.MAX_PER_DAY, I.OVER_CAP_KEPT, I.MAX_PDFS)
    try:
        n, lines = len(server.JOBS), len(audit_lines())
        big, other = H.drawing_pdf(1, title="TOO BIG"), H.drawing_pdf(1, title="OTHER")
        I.MAX_MESSAGE_BYTES = len(big) + len(other) - 1          # one byte under what the two attachments add up to
        send(ta, [("too-big.pdf", big), ("other.pdf", other)], subject="R7 size")
        e = newest(BOB)
        check(len(server.JOBS) == n and e["status"] == "refused" and "over the limit" in e["reason"]
              and [f["name"] for f in e["files"]] == ["too-big.pdf", "other.pdf"] and all(f["state"] == "refused" for f in e["files"])
              and spool_empty(),
              "7: a message over the size limit: recorded with the reason and its file names, nothing read, nothing spooled", e)
        check(any(x["action"] == "inbound_refused" for x in audit_lines()[lines:]), "7: ... with its log line")
        I.MAX_MESSAGE_BYTES = old[0]
        I.MAX_PDF_PAGES = 2
        n = len(server.JOBS)
        send(ta, [("long.pdf", H.drawing_pdf(3, title="LONG")), ("short.pdf", H.drawing_pdf(1, title="SHORT"))], subject="R7 pages")
        by = {f["name"]: f for f in newest(BOB)["files"]}
        check(len(server.JOBS) == n + 1 and by["long.pdf"]["state"] == "refused" and "3 pages: over the limit of 2" in by["long.pdf"]["reason"]
              and by["short.pdf"]["state"] == "opened",
              "7: a PDF over the page limit is refused with the numbers; the other PDF in the message still opens", by["long.pdf"])
        I.MAX_PDF_PAGES = old[1]
        I.MAX_PDFS = 2
        n = len(server.JOBS)
        send(ta, [(f"p{i}.pdf", H.drawing_pdf(1, title=f"P{i}")) for i in range(3)], subject="R7 pdf count")
        by = {f["name"]: f for f in newest(BOB)["files"]}
        check(len(server.JOBS) == n + 2 and "more than 2 PDFs" in by["p2.pdf"]["reason"],
              "7: more PDFs than the cap in one message: the extra one is listed, not read", by["p2.pdf"])
        I.MAX_PDFS = old[4]
        # messages a day
        read = I.read_today(BOB, time.time())
        I.MAX_PER_DAY, I.OVER_CAP_KEPT = read + 1, 2
        n = len(server.JOBS)
        send(ta, [("last-one.pdf", H.drawing_pdf(1, title="LAST ONE"))], subject="R7 the last one today")
        check(len(server.JOBS) == n + 1, "7: the message that reaches the daily limit is still read")
        lines = len(audit_lines())
        send(ta, [("over-1.pdf", H.drawing_pdf(1, title="OVER 1"))], subject="R7 over the limit 1")
        e = newest(BOB)
        check(len(server.JOBS) == n + 1 and e["status"] == "over" and e["subject"] == "R7 over the limit 1"
              and f"limit of {read + 1} messages a day" in e["reason"] and e["files"][0]["name"] == "over-1.pdf",
              "7: one past the daily limit: recorded with the sender, subject, file names and the reason; nothing read", e)
        send(ta, [("over-2.pdf", DRAW)], subject="R7 over the limit 2")
        total = inbox(BOB)[1]["total"]
        for i in range(3):
            send(ta, [(f"flood-{i}.pdf", DRAW)], subject=f"R7 flood {i}")
        e = newest(BOB)
        new = [x for x in audit_lines()[lines:] if x["action"] == "inbound_refused"]
        check(inbox(BOB)[1]["total"] == total and e["subject"] == "R7 over the limit 2" and e["more"] == 3 and len(new) == 5
              and len(server.JOBS) == n + 1,
              "7: a flood past the limit is counted on the record (and 3 more), one log line each - never dropped unsaid", (e["more"], len(new)))
    finally:
        I.MAX_MESSAGE_BYTES, I.MAX_PDF_PAGES, I.MAX_PER_DAY, I.OVER_CAP_KEPT, I.MAX_PDFS = old
    H.drain(server)


def r9_privacy():
    on()
    ta = tag_of(address(ALICE))
    send(ta, [("private-plans.pdf", H.drawing_pdf(1, title="PRIVATE"))], subject="R9 PRIVATE-SUBJECT", sender=("Gina Contractor", "gina@gc.example"))
    f = newest(ALICE)["files"][0]
    jid = f["job"]
    st, bj = inbox(BOB)
    text = json.dumps(bj)
    check(st == 200 and "R9 PRIVATE-SUBJECT" not in text and "private-plans" not in text and jid not in text and "gina@" not in text,
          "9: another account's e-mail page shows nothing of it: no subject, no sender, no file name, no project id")
    check(not any(r["job_id"] == jid for r in projects(BOB))
          and jid not in json.dumps(H.call(server, "GET", "/api/recent", cookie=CK[BOB])[1]),
          "9: the project is not on another account's Projects page or recents")
    codes = [H.call(server, m, p, b, cookie=CK[BOB])[0] for m, p, b in (
        ("GET", f"/api/job/{jid}/open", None), ("GET", f"/api/job/{jid}/keywords", None), ("GET", f"/api/job/{jid}/page/0/status", None),
        ("POST", f"/api/job/{jid}/page/0/detect", {}), ("DELETE", f"/api/job/{jid}", None))]
    check(codes == [404] * 5 and jid in server.JOBS, "9: every job route answers 404 to another account, as for a project that is not there", codes)
    st, j = H.call(server, "POST", "/api/inbound/open", {"entry": newest(ALICE)["id"]}, cookie=CK[BOB])
    check(st == 404, "9: another account cannot reach a record by its id", (st, j))
    sj = inbox(STAFF)[1]
    check(any(r["job_id"] == jid and r["inbound"]["subject"] == "R9 PRIVATE-SUBJECT" for r in projects(STAFF))
          and "R9 PRIVATE-SUBJECT" not in json.dumps(sj["entries"]),
          "9: staff see the project (the existing rule), with its e-mail note; the e-mail list is each account's own")
    # every reply is an allow-list
    st, j = inbox(ALICE)
    e = j["entries"][0]
    check(sorted(j) == ["address", "busy", "can_address", "confirm", "enabled", "entries", "limits", "rules", "total"]
          and sorted(e) == ["again", "at", "attached", "confirm", "excluded", "files", "from", "id", "more", "pending", "reason",
                            "status", "subject", "unlisted", "unread"]
          and set(e["files"][0]) <= {"n", "name", "zip", "state", "reason", "pages", "anyway", "job", "auto", "gone", "why", "kept", "match"},
          "9: the list reply carries exactly the listed fields", (sorted(j), sorted(e), sorted(e["files"][0])))
    raw = json.dumps(j)
    sha = hashlib.sha256(b"").hexdigest()
    check('"sha"' not in raw and '"message_id"' not in raw and '"account"' not in raw and len(sha) == 64
          and not re.search(r"[0-9a-f]{64}", raw) and str(_JOBS) not in raw and "upload_" not in raw,
          "9: no file hash, message id, account name, path or stored file name leaves the server")
    st, j = post(H.payload("e" * 26, [("x.pdf", DRAW)]))
    st2, j2 = post(H.payload(ta, [("y.txt", b"y")]))
    read_all()
    check((st, j) == (st2, j2) == (200, {"ok": True}), "9: the door's answer is the same for an address nobody has and for a real one")
    H.drain(server)


def r10_the_log():
    on()
    ta = tag_of(address(ALICE))
    lines = len(audit_lines())
    send(ta, [("logged-plans.pdf", H.drawing_pdf(1, title="LOGGED")), ("logged-spec.pdf", H.text_pdf(2, extra="logged")),
              ("logged.docx", b"PK\x03\x04docx-not-a-zip-we-open")], subject="R10 the log", text="BODY-MARKER-TEXT in the log test")
    new = audit_lines()[lines:]
    acts = sorted(x["action"] for x in new)
    check(acts == ["inbound_file_refused", "inbound_file_refused", "inbound_message", "inbound_project"],
          "10: one line per message received, per project opened, per file refused (1 + 1 + 2)", acts)
    msg = next(x for x in new if x["action"] == "inbound_message")
    prj = next(x for x in new if x["action"] == "inbound_project")
    ref = sorted(x["name"] for x in new if x["action"] == "inbound_file_refused")
    check(msg["user"] == ALICE and msg["name"] == "R10 the log" and "pat@sender.example" in msg["detail"] and "3 file(s)" in msg["detail"]
          and prj["user"] == ALICE and prj["job"] in server.JOBS and prj["name"] == "logged-plans.pdf" and ref == ["logged-spec.pdf", "logged.docx"],
          "10: each line names the account, the file or the project, and the reason", (msg, prj, ref))
    blob = (b"".join(f.read_bytes() for f in (_JOBS / "audit").glob("*.jsonl")))
    check(b"BODY-MARKER" not in blob and ta.encode() not in blob and set(new[0]) == {"ts", "user", "action", "job", "name", "page", "detail"}
          and b"\r" not in blob,
          "10: the log holds no message text and no address tag; each line has the activity log's seven fields, LF-ended")
    H.drain(server)


def r11_config():
    yml = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for name in ("INBOUND_HOOK_USER", "INBOUND_HOOK_PASS", "INBOUND_ADDRESS"):
        m = re.search(r"\n((?:[ \t]*#[^\n]*\n)+)[ \t]*" + name + r": \$\{" + name + r":-\}\n", yml)
        check(m is not None, f"11: docker-compose.yml passes {name} with an EMPTY default (off) and a comment above it")
    env_block = yml[yml.index("environment:"):yml.index("volumes:")]
    check(not re.search(r"INBOUND_[A-Z_]+: [^$\n]", env_block), "11: no value for any of them is written in the file")
    # 12: the engine files are untouched - and cannot be reached by this feature at all
    from takeoff_engine import detect_page as dp
    eng = ROOT / "takeoff_engine"
    check("inbound.py" not in dp._ENGINE_FILES and (eng / "inbound.py").exists()
          and not any("inbound" in (eng / f).read_text(encoding="utf-8") for f in dp._ENGINE_FILES),
          "12: inbound.py is not an engine file and no engine file mentions it: the engine signature cannot move with it")


def r13_keyword_rules():
    on()
    ta, tb = tag_of(address(ALICE)), tag_of(address(BOB))
    st, j = inbox(ALICE)
    check(j["rules"] == {"keywords": [], "exclude": [], "v": 0}, "13: both lists are empty by default", j["rules"])
    n = len(server.JOBS)
    send(ta, [("no-rules.pdf", H.drawing_pdf(1, title="NO RULES"))])
    f = newest(ALICE)["files"][0]
    check(len(server.JOBS) == n + 1 and f["state"] == "opened" and "match" not in f,
          "13: no keywords set: every drawing PDF opens (ruling 3) and nothing is searched", f)
    st, j = set_rules(ALICE, ["  Quartzline ", "quartzline", "blue   marrow pipe", "x", 7, "vellumgrade"], ["Mudlark Paving"])
    check(st == 200 and j["rules"]["keywords"] == ["Quartzline", "blue marrow pipe", "vellumgrade"] and j["rules"]["exclude"] == ["Mudlark Paving"]
          and j["rules"]["v"] == 1 and j["left_out"] == 2,
          "13: the lists are saved cleaned (spaces folded, a repeat and a one-letter word left out, and it says how many)", j)
    st, j = H.call(server, "POST", "/api/inbound/rules", {"keywords": ["stale"], "exclude": [], "v": 0}, cookie=CK[ALICE])
    check(st == 409 and "another window" in j["detail"] and inbox(ALICE)[1]["rules"]["keywords"][0] == "Quartzline",
          "13: a save built on a list that has changed since is refused in words, and nothing is written", (st, j))
    st, j = H.call(server, "POST", "/api/inbound/rules", {"keywords": ["x1"], "exclude": []}, cookie=CK[VIEW])
    check(st == 403, "13: a view-only account has no rules to save", (st, j))
    many = [f"word{i:03d}" for i in range(I.RULE_WORDS_MAX + 5)]
    check(len(I.clean_words(many)) == I.RULE_WORDS_MAX and I.clean_words(["a" * 200])[0] == "a" * I.RULE_WORD_MAX,
          "13: at most 40 words a list, 60 characters a word")
    # FOUND: opened, with the pages
    words = {0: ["furnish QUARTZLINE coating", "quartzlines are not it"], 1: ["BLUE MARROW", "PIPE at the wall"], 2: ["nothing here"],
             3: ["Vellumgrade, and quartzline."]}
    n = len(server.JOBS)
    send(ta, [("matched.pdf", H.drawing_pdf(4, title="MATCHED", page_words=words))], subject="R13 found")
    f = newest(ALICE)["files"][0]
    found = {x["word"]: x for x in (f.get("match") or {}).get("found", [])}
    check(len(server.JOBS) == n + 1 and f["state"] == "opened" and list(found) == ["Quartzline", "blue marrow pipe", "vellumgrade"]
          and found["Quartzline"]["pages"] == [0, 3] and found["Quartzline"]["count"] == 2 and found["vellumgrade"]["pages"] == [3],
          "13: keywords found: the plan opens, and the record lists each keyword with its pages (whole words, case aside)", f.get("match"))
    check(found["blue marrow pipe"]["pages"] == [1] and f["match"]["searched"] == 4 and f["match"]["no_text"] == 0 and f["match"]["pages"] == 4,
          "13: 'quartzlines' is not 'quartzline'; a phrase wrapped over two lines is found; 4 of 4 pages searched", f["match"])
    phrase = I.search_pdf(H.drawing_pdf(1, words=["BLUE  MARROW\nPIPE", "marrowpipe blue"]), ["blue marrow pipe", "marrow"])
    check([(x["word"], x["pages"]) for x in phrase["found"]] == [("blue marrow pipe", [0]), ("marrow", [0])]
          and I.search_pdf(H.drawing_pdf(1, words=["bluemarrow pipes"]), ["blue marrow pipe", "marrow"])["found"] == [],
          "13: a phrase is found across a line break and extra spaces, and never inside a longer word", phrase)
    want = [{"word": "Quartzline", "pages": [0, 3], "count": 2}, {"word": "blue marrow pipe", "pages": [1], "count": 1},
            {"word": "vellumgrade", "pages": [3], "count": 1}]
    st, j = H.call(server, "GET", f"/api/job/{f['job']}/keywords", cookie=CK[ALICE])
    check(st == 200 and j == {"from_email": True, "subject": "R13 found", "at": j["at"],
                              "match": {"found": want, "searched": 4, "no_text": 0, "pages": 4}},
          "13: the match record is kept WITH THE PROJECT (keyword -> pages) and served field by field", j)
    reg = json.loads((_JOBS / "registry.json").read_text())[f["job"]]["inbound"]
    check(reg["match"]["found"] == want, "13: ... in the project's own registry row, so it survives a restart", reg)
    card = next(r for r in projects(ALICE) if r["job_id"] == f["job"])
    check(card["inbound"]["found"] == ["Quartzline", "blue marrow pipe", "vellumgrade"], "13: the card names the keywords that were found", card["inbound"])
    st0, j0 = H.call(server, "GET", f"/api/job/{f['job']}/keywords", cookie=CK[BOB])
    other = next(r["job_id"] for r in projects(ALICE) if r["name"] == "no-rules.pdf")
    st1, j1 = H.call(server, "GET", f"/api/job/{other}/keywords", cookie=CK[ALICE])
    check(st0 == 404 and st1 == 200 and j1["match"] is None and j1["from_email"] is True,
          "13: another account gets 404 for it; a project that arrived with no keywords set has no match record (null, not empty)", (st0, j1))
    # NONE FOUND: held, with Open anyway
    n = len(server.JOBS)
    send(ta, [("unmatched.pdf", H.drawing_pdf(2, title="UNMATCHED", words=["plain asphalt and curb"])),
              ("specs.pdf", H.text_pdf(2, extra="Section 09: quartzline finish, see drawings."))], subject="R13 none found")
    e = newest(ALICE)
    by = {x["name"]: x for x in e["files"]}
    f = by["unmatched.pdf"]
    check(len(server.JOBS) == n and f["state"] == "held" and f["reason"] == "none of your keywords found" and f["why"] == "keywords"
          and f["kept"] is True and "job" not in f and f["match"] == {"found": [], "searched": 2, "no_text": 0, "pages": 2},
          "13: no keyword in a plan: it is NOT opened, the record says 'none of your keywords found', and it is kept", f)
    check(by["specs.pdf"]["state"] == "refused" and by["specs.pdf"]["match"]["found"][0]["word"] == "Quartzline"
          and by["specs.pdf"]["match"]["found"][0]["pages"] == [0, 1],
          "13: every PDF is searched: the text document is still not a plan, and its line says the keyword is on its pages", by["specs.pdf"])
    st, j = H.call(server, "POST", "/api/inbound/open", {"entry": e["id"], "n": f["n"]}, cookie=CK[ALICE])
    g = next(x for x in j["entry"]["files"] if x["name"] == "unmatched.pdf") if st == 200 else {}
    check(st == 200 and len(server.JOBS) == n + 1 and g["state"] == "opened" and g["anyway"] is True and server.JOBS[g["job"]]["owner"] == ALICE
          and g["auto"]["queued"] == 2,
          "13: Open anyway opens it as the account's project and Detect starts by itself", (st, g))
    st, j = H.call(server, "POST", "/api/inbound/open", {"entry": e["id"], "n": f["n"]}, cookie=CK[ALICE])
    held_left = list((_JOBS / "inbound" / "held").rglob("*.bin"))
    check(st == 409 and len(server.JOBS) == n + 1 and not held_left, "13: ... once; the kept copy is gone after it opened", (st, held_left))
    k = H.call(server, "GET", f"/api/job/{g['job']}/keywords", cookie=CK[ALICE])[1]
    check(k["match"] == {"found": [], "searched": 2, "no_text": 0, "pages": 2}, "13: its match record says: searched, nothing found", k)
    # a scan: nothing to search is said plainly
    send(ta, [("scanned.pdf", H.scan_pdf(3))], subject="R13 a scan")
    f = newest(ALICE)["files"][0]
    check(f["state"] == "held" and f["match"] == {"found": [], "searched": 3, "no_text": 3, "pages": 3},
          "13: a scan has no text: held, and the record says 3 of 3 pages had nothing to search - not 'not there'", f)
    half = I.search_pdf(H.drawing_pdf(2, page_words={0: ["quartzline"]}), ["quartzline"])
    check(half["no_text"] == 0 and I.search_pdf(H.scan_pdf(1), ["quartzline"])["no_text"] == 1
          and I.search_pdf(b"junk", ["quartzline"]) is None, "13: no_text counts only pages with nothing to search", half)
    # EXCLUSION: the subject or the sender
    n = len(server.JOBS)
    send(ta, [("excluded.pdf", H.drawing_pdf(1, title="EXCLUDED", words=["quartzline everywhere"])), ("memo.txt", b"m")],
         subject="ITB from MUDLARK   PAVING - Elm Street")
    e = newest(ALICE)
    f = next(x for x in e["files"] if x["name"] == "excluded.pdf")
    check(len(server.JOBS) == n and e["status"] == "skipped" and e["excluded"] == "Mudlark Paving" and f["state"] == "held"
          and f["reason"] == "skipped by your exclusion rule" and f["why"] == "exclude" and f["kept"] is True
          and f["match"]["found"][0]["word"] == "Quartzline",
          "13: an exclusion word in the SUBJECT: the message is listed as skipped by the rule, nothing opens, the plan is kept", e)
    st, j = H.call(server, "POST", "/api/inbound/open", {"entry": e["id"]}, cookie=CK[ALICE])
    g = next(x for x in j["entry"]["files"] if x["name"] == "excluded.pdf") if st == 200 else {}
    check(st == 200 and len(server.JOBS) == n + 1 and g["state"] == "opened" and g["anyway"] is True,
          "13: Open anyway on the message opens its plan", (st, g))
    set_rules(ALICE, ["quartzline"], ["rival-gc.example"])
    n = len(server.JOBS)
    send(ta, [("from-them.pdf", H.drawing_pdf(1, title="FROM THEM", words=["quartzline"]))], subject="R13 sender",
         sender=("Estimating", "bids@rival-gc.example"))
    e = newest(ALICE)
    check(len(server.JOBS) == n and e["status"] == "skipped" and e["excluded"] == "rival-gc.example",
          "13: an exclusion word in the SENDER does the same", e)
    send(ta, [("near-miss.pdf", H.drawing_pdf(1, title="NEAR MISS", words=["quartzline"]))], subject="R13 near miss",
         sender=("Estimating", "bids@not-rival-gc.examples"))
    e = newest(ALICE)
    check(e["status"] == "done" and e["files"][0]["state"] == "opened", "13: whole words only: 'not-rival-gc.examples' is not excluded", e)
    check(I.excluded_by(["paving"], "Repaving the lot", "x@y.example") is None and I.excluded_by(["paving"], "PAVING, phase 2", "") == "paving",
          "13: 'paving' excludes 'PAVING, phase 2' and not 'Repaving'")
    # the rules are each account's own
    n = len(server.JOBS)
    send(tb, [("bobs.pdf", H.drawing_pdf(1, title="BOBS", words=["no keyword of alice here"]))], subject="from Mudlark Paving",
         sender=("Estimating", "bids@rival-gc.example"))
    f = newest(BOB)["files"][0]
    check(len(server.JOBS) == n + 1 and f["state"] == "opened" and "match" not in f and inbox(BOB)[1]["rules"]["keywords"] == [],
          "13: one account's keywords and exclusions do nothing to another account's mail", f)
    # a held plan is kept HELD_DAYS days, then no longer offered
    set_rules(ALICE, ["quartzline"], [])
    send(ta, [("expires.pdf", H.drawing_pdf(1, title="EXPIRES", words=["nothing"]))], subject="R13 expiry")
    e = newest(ALICE)
    days = I.HELD_DAYS
    on_disk = list((_JOBS / "inbound" / "held").glob(f"*/{e['id']}/*.bin"))      # this message's held plan
    I.HELD_DAYS = 0                                   # its days are over by the clock; the file has not been swept yet
    try:
        f0 = newest(ALICE)["files"][0]
        st0, j0 = H.call(server, "POST", "/api/inbound/open", {"entry": e["id"], "n": 0}, cookie=CK[ALICE])
    finally:
        I.HELD_DAYS = days
    check(len(on_disk) == 1 and on_disk[0].exists() and f0["kept"] is False and st0 == 410 and newest(ALICE)["files"][0]["kept"] is True,
          "13: a held plan stops being offered when its days are over, by the clock - even before its file is swept", (f0, st0))
    gone = I.held_prune_all(time.time() + (I.HELD_DAYS + 1) * 86400)
    f = newest(ALICE)["files"][0]
    st, j = H.call(server, "POST", "/api/inbound/open", {"entry": e["id"], "n": 0}, cookie=CK[ALICE])
    check(gone >= 1 and not on_disk[0].exists() and f["state"] == "held" and f["kept"] is False and st == 410
          and f"{I.HELD_DAYS} days" in j["detail"],
          "13: the sweep (every idle hour, and at start) removes it from disk; Open anyway says it is no longer kept", (gone, f, st, j))
    old = I.HELD_MAX_BYTES
    try:
        send(ta, [("budget-1.pdf", H.drawing_pdf(1, title="BUDGET 1", words=["nothing"]))], subject="R13 budget 1")
        first = newest(ALICE)
        I.HELD_MAX_BYTES = 10
        send(ta, [("budget-2.pdf", H.drawing_pdf(1, title="BUDGET 2", words=["nothing"]))], subject="R13 budget 2")
        check(newest(ALICE)["files"][0]["kept"] is False
              and next(x for x in inbox(ALICE)[1]["entries"] if x["id"] == first["id"])["files"][0]["kept"] is False,
              "13: past the kept-plans budget the oldest go first (here the budget is 10 bytes, so both)")
    finally:
        I.HELD_MAX_BYTES = old
    # the words are account data: not in the log
    lines = audit_lines()
    said = json.dumps([x for x in lines if x["action"] != "inbound_message"]).lower()     # a subject is the sender's own words
    check("quartzline" not in said and "mudlark" not in said and "vellumgrade" not in said and "rival-gc" not in said
          and any(x["action"] == "inbound_rules" for x in lines) and any(x["action"] == "inbound_file_held" for x in lines),
          "13: the log counts the rules and the held plans and never holds a keyword or an exclusion word")
    # an admin loads one account's lists on the server
    kw, ex = _JOBS / "kw.txt", _JOBS / "ex.txt"
    kw.write_bytes("# made-up words\nquartzline\n\nGranite Wren Tile\nquartzline\n".encode("utf-8"))
    ex.write_bytes("mudlark paving\n".encode("utf-8"))
    env = dict(os.environ, TAKEOFF_JOBS_DIR=str(_JOBS), PYTHONIOENCODING="utf-8")
    out = subprocess.run([sys.executable, "-m", "takeoff_engine.inbound", "rules", BOB.upper(), "--keywords", str(kw), "--exclude", str(ex)],
                         capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=120)
    r = inbox(BOB)[1]["rules"]
    check(out.returncode == 0 and r["keywords"] == ["quartzline", "Granite Wren Tile"] and r["exclude"] == ["mudlark paving"]
          and "2 keyword(s), 1 exclusion word(s)" in out.stdout and "quartzline" not in out.stdout,
          "13: an admin loads one account's lists from files on the server (the words are not echoed)", (out.returncode, out.stdout, out.stderr[-300:]))
    out = subprocess.run([sys.executable, "-m", "takeoff_engine.inbound", "rules", "nobody@nowhere.example", "--clear"],
                         capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=120)
    check(out.returncode == 1 and "no such account" in out.stdout, "13: ... and an unknown account is refused", out.stdout)
    H.drain(server)


def r1_off_again():
    """Turned off on a box that has e-mailed projects: nothing of the feature shows, and nothing is lost."""
    on()
    had = [r for r in projects(ALICE) if r.get("inbound")]
    off()
    try:
        rows = projects(ALICE)
        st, _ = inbox(ALICE)
        st2, _ = post(H.payload("0" * 26))
        st3, _ = H.call(server, "POST", "/api/inbound/open", {"entry": "x"}, cookie=CK[ALICE])
        check(had and len(rows) >= len(had) and not any(r.get("inbound") for r in rows) and (st, st2, st3) == (404, 404, 404),
              "1: turned off later: the cards lose their e-mail note, the list and every e-mail route answer 404; the projects stay",
              (len(had), st, st2, st3))
    finally:
        on()
    check(len([r for r in projects(ALICE) if r.get("inbound")]) == len(had) and inbox(ALICE)[0] == 200,
          "1: turned on again: the notes and the list are back (the record was kept)")


for t in (r1_the_door, r2_whose_mail, r3_what_is_kept, r4_what_is_not_kept, r5_once_only, r6_detect, r7_limits, r9_privacy,
          r10_the_log, r11_config, r13_keyword_rules, r1_off_again):
    run(t)

print(f"\n{checks} checks, {fails} failure(s)")
sys.exit(1 if fails else 0)
