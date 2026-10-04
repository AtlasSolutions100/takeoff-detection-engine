"""BIDS BY E-MAIL AFTER ITS FIRST OUTSIDE REVIEW (2026-10-04): one test per finding, each failing on the commit
that was reviewed.

A message can come from anybody who knows a bid address. The review showed what one could do with that; these are
its findings and the coordinator's rulings on them, each as a test (the feature's own rulings are in
tests/test_inbound_email.py).

  R3  the smaller findings: a removed account is forgotten (a new one of the same name starts clean); the keywords
      route is off with the feature; MailboxHash empty -> the recipient's plus-part, a disagreement opens nothing;
      a malformed POST answers 400, not 500; a keyword in a sheet's short title is found; a file whose project was
      deleted opens again
  R2  A FORWARDING CONFIRMATION KEEPS NO FREE TEXT: a code made of digits and a link to the provider's own
      confirmation page, nothing else, and nothing at all when neither is found
  R1  COUNT BEFORE YOU OPEN, ONE FILE AT A TIME
      b1  small zips that each unpack to a huge file cost this process no memory: a member that is not a PDF by
          its first bytes is not unpacked, the others go to disk in pieces
      b2  what ALL the zips of one message unpack to is counted while unpacking and stops at one cap
      b3  only a message's first MAX_ATTACHMENTS attachments are looked at; the rest are counted, never decoded,
          and the record, the log and the list reply stay small
      b4  a zip's table of contents is capped before it is read; a record lists MAX_LISTED files and counts the rest
      b5  the door measures before it decodes, decodes one file at a time, and takes in DOOR_SLOTS messages at once
      b6  the reader works from files: several large PDFs are never in memory; a message that stopped the server
          twice is not read a third time, and one already read is not read again

Every name, address and word here is made up; the zips and PDFs are built here. Memory is measured with tracemalloc
(what Python itself allocates), so a check reads the same on any machine.

    python tests/test_inbound_critic1.py
"""
import gc
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import tracemalloc
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
_JOBS = Path(tempfile.mkdtemp(prefix="inb_crit_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(_JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="inb_crit_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
for _k in ("INBOUND_HOOK_USER", "INBOUND_HOOK_PASS", "INBOUND_ADDRESS"):
    os.environ.pop(_k, None)

import pymupdf  # noqa: E402

import _inbound_harness as H  # noqa: E402
import server  # noqa: E402
from takeoff_engine import inbound as I  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

server._classify_async = lambda *a, **k: None        # page kinds are not what is tested here
server._INBOUND_KINDS_WAIT_S = 0
H.install_fake_children(server)

ALICE, BOB, STAFF, TEMP = "alice@builder.example", "bob@rival.example", "staff@core.example", "temp@builder.example"
_users.save({ALICE: _users.hash_password("orchid-lantern-4471"), BOB: _users.hash_password("basalt-meadow-9083"),
             STAFF: dict(_users.hash_password("granite-sparrow-2210"), staff=True)})
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in (ALICE, BOB, STAFF)}
DOOR = "/api/inbound/postmark"
MB = 1024 * 1024
GMAIL = "forwarding-noreply@google.com"              # the one sender a forwarding confirmation is known by

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
    except Exception as e:                           # noqa: BLE001 - on the reviewed commit most tests land here
        check(False, f"{fn.__name__}: raised {type(e).__name__}: {str(e)[:200]}")


def on():
    os.environ.update({"INBOUND_HOOK_USER": H.HOOK_USER, "INBOUND_HOOK_PASS": H.HOOK_PASS, "INBOUND_ADDRESS": H.BASE_ADDRESS})


def off():
    for k in ("INBOUND_HOOK_USER", "INBOUND_HOOK_PASS", "INBOUND_ADDRESS"):
        os.environ.pop(k, None)


def post(pl, auth=...):
    return H.call(server, "POST", DOOR, pl, auth=H.basic() if auth is ... else auth, marker=False)


def read_all():
    server._INBOUND_Q.join()


def inbox(who):
    return H.call(server, "GET", "/api/inbound", cookie=CK[who])


def address(who):
    st, j = H.call(server, "POST", "/api/inbound/address", {}, cookie=CK[who])
    return j.get("address") if st == 200 else None


def tag_of(addr):
    return addr.split("+", 1)[1].split("@", 1)[0]


def my_tag(who):
    st, j = inbox(who)
    return tag_of(j.get("address") or address(who))


def newest(who):
    return (inbox(who)[1].get("entries") or [None])[0]


def entry_named(who, subject):
    return next((e for e in inbox(who)[1].get("entries") or [] if e["subject"] == subject), None)


def send(tag, files=(), **kw):
    pl = H.payload(tag, files, **kw)
    st, j = post(pl)
    read_all()
    return st, j, pl


def set_rules(who, keywords=(), exclude=()):
    v = (inbox(who)[1].get("rules") or {}).get("v")
    return H.call(server, "POST", "/api/inbound/rules", {"keywords": list(keywords), "exclude": list(exclude), "v": v},
                  cookie=CK[who])


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


def record_of(who, subject) -> "tuple":
    """(the record on disk, its size in bytes) for the account's message with this subject."""
    d = _JOBS / "inbound" / "mail" / hashlib.sha256(who.encode("utf-8")).hexdigest()[:24]
    for f in sorted(d.glob("*.json"), reverse=True):
        raw = f.read_bytes()
        rec = json.loads(raw)
        if rec.get("subject") == subject:
            return rec, len(raw)
    return None, 0


def spool_left() -> list:
    d = _JOBS / "inbound" / "spool"
    return sorted(p.name for p in d.rglob("*")) if d.exists() else []


def traced_peak(fn) -> int:
    """The most memory Python held at one moment while `fn` ran, in bytes (every thread; the reader is one)."""
    gc.collect()
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        base = tracemalloc.get_traced_memory()[0]
        fn()
        return tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()


def big_pdf(title: str, pad_mb: int) -> bytes:
    """A drawing sheet that weighs `pad_mb` MB: a file of noise is attached inside it, so it does not compress."""
    d = pymupdf.open(stream=H.drawing_pdf(1, title=title), filetype="pdf")
    d.embfile_add("pad.bin", os.urandom(pad_mb * MB))
    raw = d.tobytes()
    d.close()
    return raw


def tiny(n: int, ext: str = "txt") -> list:
    """n one-byte attachments in the mail service's shape."""
    return [{"Name": f"f{i:06d}.{ext}", "Content": "eA==", "ContentType": "text/plain", "ContentLength": 1}
            for i in range(n)]


DRAW = H.drawing_pdf(1, title="CRITIC ONE")


# =====================================================================================================================
def r3_a_removed_account_is_forgotten():
    on()
    send(my_tag(ALICE), [], subject="R3 somebody else's message")      # two other accounts, with something on file
    my_tag(BOB)
    store = dict(_users.load())
    store[TEMP] = _users.hash_password("walnut-harbor-3318")
    _users.save(store)
    ck = server._sign_session(TEMP)
    st, j = H.call(server, "POST", "/api/inbound/address", {}, cookie=ck)
    tag = tag_of(j["address"])
    H.call(server, "POST", "/api/inbound/rules", {"keywords": ["quartzline"], "exclude": ["mudlark paving"], "v": 0}, cookie=ck)
    send(tag, [("kept.pdf", H.drawing_pdf(1, title="KEPT", words=["quartzline"])),
               ("held.pdf", H.drawing_pdf(1, title="HELD", words=["nothing of note"]))], subject="R3 before the removal")
    st, j = H.call(server, "GET", "/api/inbound", cookie=ck)
    key = hashlib.sha256(TEMP.encode("utf-8")).hexdigest()[:24]
    inb = _JOBS / "inbound"
    held = list((inb / "held" / key).rglob("*.bin"))
    check(j["total"] == 1 and len(held) == 1 and j["rules"]["keywords"] == ["quartzline"],
          "r3: (an account with an address, rules, one message and one held plan)", (j["total"], len(held)))
    # removed the way the Accounts page removes it
    st, _ = H.call(server, "DELETE", f"/api/users/{TEMP}", cookie=CK[STAFF])
    files = {p: (inb / p).read_text(encoding="utf-8") for p in ("addresses.json", "rules.json")}
    check(st == 200 and tag not in files["addresses.json"] and TEMP not in files["addresses.json"]
          and TEMP not in files["rules.json"] and "quartzline" not in files["rules.json"]
          and not (inb / "mail" / key).exists() and not (inb / "held" / key).exists(),
          "r3: removing the account forgets its tag, its rules, its message records and its held plan",
          (st, sorted(p.name for p in (inb / "mail").iterdir())))
    n = len(server.JOBS)
    send(tag, [("after.pdf", H.drawing_pdf(1, title="AFTER"))], subject="R3 to the old tag")
    check(len(server.JOBS) == n and I.account_for(tag) is None, "r3: the old tag opens nothing")
    # made again under the same name: it starts clean
    _users.add_user(TEMP, "walnut-harbor-3318")
    ck = server._sign_session(TEMP)
    st, j = H.call(server, "GET", "/api/inbound", cookie=ck)
    check(st == 200 and j["address"] is None and j["total"] == 0 and j["entries"] == []
          and j["rules"] == {"keywords": [], "exclude": [], "v": 0} and j["confirm"] is None,
          "r3: an account made again under the same name has no address, no rules and no messages", j)
    send(tag, [("after-2.pdf", H.drawing_pdf(1, title="AFTER 2"))], subject="R3 to the old tag again")
    st, j2 = H.call(server, "POST", "/api/inbound/address", {}, cookie=ck)
    check(len(server.JOBS) == n and tag_of(j2["address"]) != tag,
          "r3: ... the old tag still opens nothing, and the address it asks for is a new one")
    # removed on the command line, and by a script that writes the account file itself: forgotten the same way
    tag2 = tag_of(j2["address"])
    env = dict(os.environ, TAKEOFF_JOBS_DIR=str(_JOBS), PYTHONIOENCODING="utf-8")
    out = subprocess.run([sys.executable, "-m", "takeoff_engine.users", "remove", TEMP], capture_output=True, text=True,
                         cwd=str(ROOT), env=env, timeout=120)
    check(out.returncode == 0 and tag2 not in (inb / "addresses.json").read_text(encoding="utf-8"),
          "r3: removed with `python -m takeoff_engine.users remove`: its tag is forgotten", (out.returncode, out.stdout[-200:]))
    store = dict(_users.load())
    store[TEMP] = _users.hash_password("walnut-harbor-3318")
    _users.save(store)
    tag3 = I.new_address(TEMP)
    store = dict(_users.load())
    store.pop(TEMP)
    _users.save(store)
    check(tag3 not in (inb / "addresses.json").read_text(encoding="utf-8") and I.tag_of(TEMP) is None,
          "r3: removed by writing the account file directly: forgotten too (every way goes through users.save)")
    # nothing of anybody else's went with it
    check(I.tag_of(ALICE) is not None and inbox(ALICE)[1]["total"] > 0 and I.tag_of(BOB) is not None,
          "r3: the other accounts' addresses and records are untouched")
    H.drain(server)


def r3_the_tag_and_the_recipient():
    on()
    ta, tb = my_tag(ALICE), my_tag(BOB)
    n = len(server.JOBS)
    # MailboxHash EMPTY: the tag is read from the address the message was delivered to
    send(ta, [("no-hash.pdf", H.drawing_pdf(1, title="NO HASH"))], subject="R3 empty hash", mailbox_hash="")
    e = entry_named(ALICE, "R3 empty hash")
    check(e is not None and len(server.JOBS) == n + 1 and e["files"][0]["state"] == "opened",
          "r3: MailboxHash empty, the tag in OriginalRecipient: the message opens for that account", e)
    # ... and only from THAT line: a tag that is on the To line alone opens nothing
    n = len(server.JOBS)
    pl = H.payload(ta, [("to-only.pdf", H.drawing_pdf(1, title="TO ONLY"))], subject="R3 to line only", mailbox_hash="")
    pl["OriginalRecipient"] = f"{H.BASE_LOCAL}@{H.BASE_DOMAIN}"
    post(pl)
    read_all()
    check(len(server.JOBS) == n and entry_named(ALICE, "R3 to line only") is None,
          "r3: a tag only on the To line (not in MailboxHash, not in OriginalRecipient) opens nothing")
    # both there, and they disagree: nobody's
    lines = len(audit_lines())
    send(tb, [("clash.pdf", H.drawing_pdf(1, title="CLASH"))], subject="R3 clash", mailbox_hash=ta)
    new = audit_lines()[lines:]
    check(len(server.JOBS) == n and entry_named(ALICE, "R3 clash") is None and entry_named(BOB, "R3 clash") is None
          and [x["action"] for x in new] == ["inbound_unknown_address"] and "two different" in new[0]["detail"]
          and ta not in json.dumps(new) and tb not in json.dumps(new),
          "r3: MailboxHash names one account and the recipient address another: it opens for NEITHER, with one log "
          "line (and no tag in it)", new)
    # both there and the same (the ordinary case), in any case of letters; and MailboxHash alone
    send(ta.upper(), [("same.pdf", H.drawing_pdf(1, title="SAME"))], subject="R3 agree")
    pl = H.payload(ta, [("hash-only.pdf", H.drawing_pdf(1, title="HASH ONLY"))], subject="R3 hash only")
    pl["OriginalRecipient"] = f"{H.BASE_LOCAL}@{H.BASE_DOMAIN}"
    post(pl)
    read_all()
    check(len(server.JOBS) == n + 2 and entry_named(ALICE, "R3 agree") and entry_named(ALICE, "R3 hash only"),
          "r3: the two agreeing (capitals aside), or MailboxHash alone, open as before")
    R = I.recipient_tag
    check(R({"MailboxHash": "", "OriginalRecipient": "base+Tag1@x.example"}) == ("tag1", False)
          and R({"MailboxHash": " TAG1 ", "OriginalRecipient": "base+tag1@x.example"}) == ("tag1", False)
          and R({"MailboxHash": "tag1", "OriginalRecipient": "base+tag2@x.example"}) == ("", True)
          and R({"MailboxHash": "tag1", "OriginalRecipient": "base@x.example"}) == ("tag1", False)
          and R({"MailboxHash": "", "OriginalRecipient": '"A Name" <base+tag9@x.example>'}) == ("tag9", False)
          and R({"MailboxHash": 5, "OriginalRecipient": ["base+tag1@x.example"]}) == ("", False)
          and R({}) == ("", False),
          "r3: the rule on its own: the hash, else the recipient's plus-part; both and different -> nobody's")
    H.drain(server)


def r3_small_ones():
    on()
    ta = my_tag(ALICE)
    # the two POSTs that answered 500 to a caller holding the hook's name and password
    for label, body in (("Attachments is a number", b'{"MessageID":"a1","MailboxHash":"x","Attachments":5}'),
                        ("Attachments is a string", b'{"MessageID":"a2","MailboxHash":"x","Attachments":"abc"}'),
                        ("JSON nested 100,000 deep", b'{"MessageID":"a3","x":' + b"[" * 100000 + b"]" * 100000 + b"}"),
                        ("not text at all", b"\xff\xfe\x00\x01" * 10)):
        st, j = H.call(server, "POST", DOOR, body, auth=H.basic(), marker=False)
        check(st == 400 and j == {"detail": "not an inbound message"}, f"r3: {label}: 400, not a server error", (st, j))
    st, j = H.call(server, "POST", DOOR, json.dumps({"MessageID": "a5", "MailboxHash": ta, "Subject": "R3 no list",
                                                     "Attachments": None}).encode(), auth=H.basic(), marker=False)
    read_all()
    check(st == 200 and entry_named(ALICE, "R3 no list") is not None, "r3: a message with no attachment list is a message")
    # the keywords route is part of the feature: off means it does not answer
    send(ta, [("kw-route.pdf", H.drawing_pdf(1, title="KW ROUTE"))], subject="R3 the keywords route")
    jid = entry_named(ALICE, "R3 the keywords route")["files"][0]["job"]
    st_on, j_on = H.call(server, "GET", f"/api/job/{jid}/keywords", cookie=CK[ALICE])
    off()
    try:
        st_off, j_off = H.call(server, "GET", f"/api/job/{jid}/keywords", cookie=CK[ALICE])
        st_staff, _ = H.call(server, "GET", f"/api/job/{jid}/keywords", cookie=CK[STAFF])
    finally:
        on()
    check(st_on == 200 and j_on["subject"] == "R3 the keywords route" and st_off == 404 and st_staff == 404
          and "R3 the keywords route" not in json.dumps(j_off),
          "r3: GET /api/job/{id}/keywords answers 404 while the feature is off (it used to hand back the e-mail's "
          "subject)", (st_on, st_off, j_off))
    # a keyword printed in a sheet's short title is found (a page with under 20 characters used to be skipped unsearched)
    def sheet(text):
        d = pymupdf.open()
        d.new_page(width=2592, height=1728).insert_text((200, 200), text, fontsize=30)
        raw = d.tobytes()
        d.close()
        return raw

    one = I.search_pdf(sheet("FOOTING"), ["footing"])
    other = I.search_pdf(sheet("FOOTING"), ["quartzline"])
    check(one == {"found": [{"word": "footing", "pages": [0], "count": 1}], "searched": 1, "no_text": 0, "pages": 1}
          and other == {"found": [], "searched": 1, "no_text": 1, "pages": 1}
          and I.search_pdf(H.scan_pdf(2), ["footing"])["no_text"] == 2,
          "r3: a sheet whose only text is the word FOOTING: the keyword 'footing' is found on it; a keyword that is "
          "not there still counts the page as having next to nothing to search", (one, other))
    set_rules(ALICE, ["footing"], [])
    try:
        n = len(server.JOBS)
        send(ta, [("short-title.pdf", sheet("FOOTING"))], subject="R3 short title")
        f = entry_named(ALICE, "R3 short title")["files"][0]
        check(len(server.JOBS) == n + 1 and f["state"] == "opened" and f["match"]["found"][0]["pages"] == [0],
              "r3: ... so that plan opens instead of being held as 'a scan'", f)
    finally:
        set_rules(ALICE, [], [])
    H.drain(server)
    # a project the person DELETED, the same file forwarded again the same day: it opens again
    raw = H.drawing_pdf(1, title="DELETED AND SENT AGAIN")
    n = len(server.JOBS)
    send(ta, [("again.pdf", raw)], subject="R3 first time")
    job = entry_named(ALICE, "R3 first time")["files"][0]["job"]
    send(ta, [("again.pdf", raw)], subject="R3 while it is still there")
    f = entry_named(ALICE, "R3 while it is still there")["files"][0]
    check(f["state"] == "same" and f["job"] == job and len(server.JOBS) == n + 1,
          "r3: (the same file again while its project exists: still nothing new)", f)
    H.drain(server)
    st, _ = H.call(server, "DELETE", f"/api/job/{job}", cookie=CK[ALICE])
    send(ta, [("again.pdf", raw)], subject="R3 after the delete")
    f = entry_named(ALICE, "R3 after the delete")["files"][0]
    check(st == 200 and f["state"] == "opened" and f.get("job") in server.JOBS and f["job"] != job
          and len(server.JOBS) == n + 1,
          "r3: the same file after its project was deleted opens again (it used to be refused for a day, with a "
          "link to a project that was gone)", f)
    H.drain(server)


# =====================================================================================================================
REAL_CONFIRM = ("user@example.com has requested to automatically forward mail to your email address.\n"
                "Confirmation code: 716599842\n\nCONFIRM-BODY-MARKER To allow it, please click the link below:\n\n"
                "https://mail-settings.google.com/mail/vf-%5BANGjdJ8made-up%5D-token\n\n"
                "If you clicked it by accident, click this link to cancel:\n"
                "https://mail-settings.google.com/mail/uf-%5BANGjdJ8made-up%5D-token\n\nThanks for using Gmail!")
LINK = "https://mail-settings.google.com/mail/vf-%5BANGjdJ8made-up%5D-token"


def r2_a_confirmation_keeps_no_text():
    on()
    tb = my_tag(BOB)
    send(tb, [], subject="(#716599842) Gmail Forwarding Confirmation - Receive Mail from user@example.com",
         sender=("Gmail Team", GMAIL), text=REAL_CONFIRM, html="<p>CONFIRM-HTML-MARKER</p>")
    st, j = inbox(BOB)
    c = j["confirm"]
    check(c is not None and sorted(c) == ["at", "code", "link", "provider"]
          and (c["provider"], c["code"], c["link"]) == ("Gmail", "716599842", LINK) and j["entries"][0]["confirm"] is True,
          "r2: a Gmail forwarding confirmation: its code and its confirming link are kept, with the provider's name - "
          "and no field for text", c)
    rec, _size = record_of(BOB, "(#716599842) Gmail Forwarding Confirmation - Receive Mail from user@example.com")
    check(b"CONFIRM-BODY-MARKER" not in store_bytes() and b"CONFIRM-HTML-MARKER" not in store_bytes()
          and "CONFIRM-" not in json.dumps(j) and rec["confirm"] == {"provider": "Gmail", "code": "716599842", "link": LINK}
          and b"requested to automatically forward" not in store_bytes(),
          "r2: none of the message's words are in the store or in a reply: the record holds the three fields", rec["confirm"])
    # MAJOR-1 as it was run: the same sender address, an outsider's own words
    phish = ("PHISHMARK4X2 Your CORE account is locked. Go to http://evil.example/login and type your password.\n"
             "https://mail.google.com/mail/u/0/?attacker-chosen-path=1\n" + ("LONGLINE" * 100) + "\n" + "x\n" * 5000)
    send(tb, [], subject="Gmail Forwarding Confirmation - Receive Mail from attacker", sender=("Gmail Team", GMAIL), text=phish)
    st, j = inbox(BOB)
    e = j["entries"][0]
    check(e["confirm"] is False and j["confirm"]["code"] == "716599842" and b"PHISHMARK4X2" not in store_bytes()
          and "PHISHMARK" not in json.dumps(j) and "No PDF was attached" in (e["reason"] or ""),
          "r2: a forged confirmation with no code and no link of the right shape keeps NOTHING: it is an ordinary "
          "message, and the outsider's words are nowhere", (e["confirm"], j["confirm"]))
    C = I.confirmation

    def conf(text="", subject="x", sender=GMAIL):
        return C({"From": sender, "FromFull": {"Email": sender, "Name": "Gmail Team"}, "Subject": subject, "TextBody": text})

    bad_links = ["http://mail-settings.google.com/mail/vf-abc",                       # not https
                 "https://mail.google.com/mail/vf-abc",                               # another of the provider's hosts
                 "https://mail-settings.google.com.evil.example/mail/vf-abc",         # the host is only a beginning
                 "https://evil.example/mail-settings.google.com/mail/vf-abc",         # the host is only in the path
                 "https://mail-settings.google.com@evil.example/mail/vf-abc",         # the host is only a name
                 "https://user@mail-settings.google.com/mail/vf-abc",                 # a name in front of the host
                 "https://mail-settings.google.com:8443/mail/vf-abc",                 # a port
                 "https://mail-settings.google.com/mail/uf-abc",                      # the CANCEL link
                 "https://mail-settings.google.com/mail/vf-abc?continue=https://evil.example",   # a query
                 "https://mail-settings.google.com/mail/vf-abc#frag",                 # a fragment
                 "https://mail-settings.google.com/mail/vf-abc/../../evil",           # steps back out of the path
                 "https://mail-settings.google.com/mail/vf-abc/%2e%2e/%2E%2E/evil",   # ... spelled as %2e
                 "https://mail-settings.google.com/mail/vf-",                         # nothing after the path
                 "https://mail-settings.google.com/other/vf-abc",                     # another path
                 "https://mail-settings.google.com/mail/vf-" + "a" * 500,             # too long
                 "HTTPS://MAIL-SETTINGS.GOOGLE.COM/mail/vf-abc"]                      # not written the provider's way
    kept = {u: conf("please click " + u + " now") for u in bad_links}
    check(all(v is None for v in kept.values()),
          "r2: sixteen links that are not https on exactly the provider's mail-settings host and its confirming path "
          "(a look-alike host, a name or a port, a query, a fragment, the cancel link, a way back out of the path): "
          "none is kept", {k: v for k, v in kept.items() if v is not None})
    both = conf("cancel: https://mail-settings.google.com/mail/uf-zzz\nconfirm: https://mail-settings.google.com/mail/vf-yyy.")
    check(both == {"provider": "Gmail", "code": None, "link": "https://mail-settings.google.com/mail/vf-yyy"},
          "r2: with the cancel link first and the confirming link second, the confirming one is the one kept "
          "(by its path, not by where it stands)", both)
    codes = {"12345": None, "123456": "123456", "123456789012": "123456789012", "1234567890123": None,
             "12345678901234567890": None, "１２３４５６７８": None, "12 345 678": None}
    got = {k: (conf(f"Confirmation code: {k}") or {}).get("code") for k in codes}
    check(got == codes and conf(subject="(#99427480) Gmail Forwarding Confirmation")["code"] == "99427480"
          and conf("code: 55555555", subject="(#44444444) x")["code"] == "44444444",
          "r2: a code is 6 to 12 plain digits (a longer run is not cut down to one); the subject's is read first", got)
    check(conf("a wrong code: 87654321 and words WORDS-OF-AN-OUTSIDER") == {"provider": "Gmail", "code": "87654321", "link": None}
          and conf(REAL_CONFIRM, sender="someone@elsewhere.example") is None
          and conf(REAL_CONFIRM, sender="forwarding-noreply@google.com.evil.example") is None,
          "r2: what a forged message CAN plant is a code of digits (or a link to the provider's own page): never a "
          "word; and only that one sender address is a confirmation at all")
    # a record written by the reviewed build (it kept the text, and a looser link): nothing of the text is served
    now = time.time()
    ent = I.new_entry(BOB, {"id": "old-confirm", "from": f"Gmail Team <{GMAIL}>", "subject": "old build", "attachments": [],
                            "attached": 0}, now + 5)
    ent.update(status="done", confirm={"provider": "Gmail", "text": "OLD-BUILD-TEXT type your password", "code": "12345",
                                       "link": "https://mail.google.com/mail/u/0/?x=1"})
    I.save_entry(BOB, ent)
    st, j = inbox(BOB)
    check("OLD-BUILD-TEXT" not in json.dumps(j) and j["confirm"]["code"] == "716599842"
          and j["entries"][0]["subject"] == "old build" and j["entries"][0]["confirm"] is False,
          "r2: a record from the reviewed build that holds a confirmation's text shows none of it (its 5-digit code "
          "and its link to another host are not of today's shape either)", j["confirm"])


# =====================================================================================================================
def b1_zip_bombs_cost_no_memory():
    """BLOCKER-1 as it was run: zips of a few KB, each one file of zeros named .pdf."""
    on()
    ta = my_tag(ALICE)
    n = len(server.JOBS)
    bomb = H.make_zip({"sheet.pdf": b"\0" * (20 * MB)})
    pl = H.payload(ta, [(f"z{i}.zip", bomb) for i in range(12)], subject="B1 zeros")

    def go():
        post(pl)
        read_all()

    peak = traced_peak(go)
    e = entry_named(ALICE, "B1 zeros")
    check(len(bomb) < 64 * 1024 and peak < 16 * MB,
          f"b1: twelve {len(bomb) // 1024} KB zips that unpack to 240 MB of zeros: the read peaks at "
          f"{peak / MB:.1f} MB of Python memory (under 16)", f"{peak / MB:.1f} MB")
    lines = [f for f in e["files"] if f["name"] == "sheet.pdf"]
    check(len(lines) == 12 and all(f["state"] == "refused" and "not a PDF inside" in f["reason"] for f in lines)
          and len(server.JOBS) == n and not spool_left(),
          "b1: each is listed as 'not a PDF inside' (told by its first bytes, never unpacked); nothing opens, nothing "
          "is left spooled", [(f["state"], f["reason"]) for f in lines][:2])
    # the same zips with a PDF's first bytes in front of the zeros: these ARE unpacked - to disk, up to the cap
    old = getattr(I, "MAX_UNPACKED_BYTES", None)
    I.MAX_UNPACKED_BYTES = 30 * MB
    try:
        junk = H.make_zip({"sheet.pdf": b"%PDF-1.4\n" + b"\0" * (20 * MB)})
        pl2 = H.payload(ta, [(f"p{i}.zip", junk) for i in range(8)], subject="B1 zeros behind a PDF header")

        def go2():
            post(pl2)
            read_all()

        peak2 = traced_peak(go2)
        e = entry_named(ALICE, "B1 zeros behind a PDF header")
        over = [f for f in e["files"] if "may unpack to 30 MB in all" in (f["reason"] or "")]
        check(peak2 < 16 * MB and len(over) == 7 and len(e["files"]) == 8 and len(server.JOBS) == n,
              f"b1: eight zips of a 20 MB 'PDF' of zeros (160 MB) against a 30 MB cap: one is unpacked to disk, seven "
              f"are 'not unpacked'; the read peaks at {peak2 / MB:.1f} MB", (f"{peak2 / MB:.1f} MB", len(over)))
        first = next(f for f in e["files"] if f not in over)
        check(first["state"] == "refused" and not spool_left()
              and not [p for p in (_JOBS / "inbound").rglob("u*.bin")],
              "b1: the one that was unpacked is not a readable PDF and is refused; no unpacked file is left behind",
              (first, spool_left()))
    finally:
        I.MAX_UNPACKED_BYTES = old
    H.drain(server)


def b2_one_count_for_the_whole_message():
    on()
    ta = my_tag(ALICE)
    d = [H.drawing_pdf(1, title=f"TOTAL {i}") for i in range(3)]
    old = getattr(I, "MAX_UNPACKED_BYTES", None)
    I.MAX_UNPACKED_BYTES = len(d[0]) + len(d[1]) // 2          # room for the first, and for half of the second
    try:
        n = len(server.JOBS)
        send(ta, [("a.zip", H.make_zip({"a.pdf": d[0]})), ("b.zip", H.make_zip({"b.pdf": d[1]})),
                  ("c.zip", H.make_zip({"c.pdf": d[2]}))], subject="B2 total")
        by = {f["name"]: f for f in entry_named(ALICE, "B2 total")["files"]}
        check(len(server.JOBS) == n + 1 and by["a.pdf"]["state"] == "opened"
              and all(by[k]["state"] == "refused" and "not unpacked" in by[k]["reason"] for k in ("b.pdf", "c.pdf")),
              "b2: three zips, room for one and a half PDFs: the first opens, the second stops at the cap, the third "
              "is not started - the count runs across every zip of the message", {k: v["state"] for k, v in by.items()})
        check(not spool_left(), "b2: the half-unpacked file is gone", spool_left())
    finally:
        I.MAX_UNPACKED_BYTES = old
    # the rule itself, on files: what a member that is not a PDF costs, and what is counted
    work = Path(tempfile.mkdtemp(prefix="inb_crit_work_"))
    z = work / "000.bin"
    z.write_bytes(H.make_zip({"big.pdf": b"\0" * (5 * MB), "real.pdf": d[0], "notes.txt": b"x" * 2000,
                              "inner.zip": H.make_zip({"x.pdf": d[1]})}))
    plans, refused, used = I.gather([{"n": 0, "name": "set.zip", "kind": "zip", "size": z.stat().st_size, "path": z}], work)
    why = {r["name"]: r["why"] for r in refused}
    check(used == len(d[0]) and [p["name"] for p in plans] == ["real.pdf"] and plans[0]["path"].read_bytes() == d[0]
          and sorted(p.name for p in work.iterdir()) == ["000.bin", plans[0]["path"].name],
          "b2: a 5 MB member that is not a PDF by its first bytes costs nothing: only the real PDF is unpacked, and "
          "only its bytes are counted", (used, len(d[0]), sorted(p.name for p in work.iterdir())))
    check(why == {"big.pdf": "other", "notes.txt": "notpdf", "inner.zip": "other"},
          "b2: the member that is not a PDF inside, the text file and the zip inside the zip are each listed with "
          "why (no nested zip is opened)", why)
    H.drain(server)


def b3_only_the_first_attachments():
    """MAJOR-2 as it was run: one message with 20,000 one-byte attachments."""
    on()
    ta = my_tag(ALICE)
    pl = H.payload(ta, [("plans.pdf", H.drawing_pdf(1, title="FLOOD"))], subject="B3 flood")
    pl["Attachments"] += tiny(20000)
    decoded = []
    real = I.decode
    I.decode = lambda c: (decoded.append(1), real(c))[1]
    lines0, n = len(audit_lines()), len(server.JOBS)
    try:
        t0 = time.time()
        st, _j = post(pl)
        took = time.time() - t0
        read_all()
    finally:
        I.decode = real
    print(f"   (the door answered in {took:.2f} s)")
    st2, j = inbox(ALICE)
    e = next(x for x in j["entries"] if x["subject"] == "B3 flood")
    rec, size = record_of(ALICE, "B3 flood")
    new = audit_lines()[lines0:]
    cap = getattr(I, "MAX_ATTACHMENTS", 0)
    check(st == 200 and len(decoded) == 1,
          "b3: of 20,001 attachments ONE is decoded (the PDF among the first fifty); the other 20,000 never are",
          len(decoded))
    check(e.get("attached") == 20001 and e.get("unread") == 20001 - cap and cap == 50,
          "b3: the record says how many the message had and how many were not looked at (20,001 and 19,951)",
          (e.get("attached"), e.get("unread")))
    check(len(e["files"]) <= I.MAX_LISTED and len(rec["files"]) <= I.MAX_LISTED and size < 16 * 1024
          and sum((e.get("unlisted") or {}).values()) + len(e["files"]) == cap,
          f"b3: the record lists {len(e['files'])} files and counts the rest; it is {size / 1024:.1f} KB on disk",
          (len(e["files"]), size, e.get("unlisted")))
    check(len(new) <= 1 + I.LOG_FILES_MAX + 1 + 1 and any("more file" in str(x.get("name")) for x in new),
          f"b3: the activity log grew by {len(new)} lines: the message, ten files, one line counting the rest, "
          "the project", [x["action"] for x in new][:16])
    body = json.dumps(j)
    check(st2 == 200 and len(body) < 64 * 1024,
          f"b3: the list reply for this account is {len(body) / 1024:.1f} KB", len(body))
    plan = next(f for f in e["files"] if f["name"] == "plans.pdf")
    check(plan["state"] == "opened" and len(server.JOBS) == n + 1,
          "b3: ... and the plan among the first fifty still opens", plan)
    # a PDF past the fiftieth attachment is not looked at, and the record says so
    n = len(server.JOBS)
    pl = H.payload(ta, [], subject="B3 a PDF in place 51")
    pl["Attachments"] = tiny(50) + H.payload(ta, [("late.pdf", H.drawing_pdf(1, title="LATE"))])["Attachments"]
    post(pl)
    read_all()
    e = entry_named(ALICE, "B3 a PDF in place 51")
    check(len(server.JOBS) == n and e.get("unread") == 1 and e.get("attached") == 51
          and not any(f["name"] == "late.pdf" for f in e["files"]),
          "b3: a PDF in place 51 is not looked at: nothing opens and the record counts one attachment not read", e)
    H.drain(server)


def b4_long_lists_stay_short():
    on()
    ta = my_tag(ALICE)
    # a zip whose table of contents is 1.4 MB: 25,000 empty files
    buf = Path(tempfile.mkdtemp(prefix="inb_crit_zip_")) / "many.zip"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for i in range(25000):
            zf.writestr(f"f{i:05d}.txt", b"")
    long_zip = buf.read_bytes()
    pl = H.payload(ta, [("many.zip", long_zip)], subject="B4 a long table of contents")

    def go():
        post(pl)
        read_all()

    peak = traced_peak(go)
    f = entry_named(ALICE, "B4 a long table of contents")["files"]
    check(len(f) == 1 and "list of files is longer than 1024 KB" in f[0]["reason"],
          "b4: a zip that lists 25,000 files (a 1.4 MB table of contents) is refused before its list is read", f)
    check(peak < 16 * MB,
          f"b4: ... at a peak of {peak / MB:.1f} MB of Python memory for a {len(long_zip) / MB:.1f} MB zip, door "
          "and reader together (no object is built per entry)", f"{peak / MB:.1f} MB")
    # fifty zips of a hundred text files each: 5,000 files that are not PDFs
    hundred = H.make_zip({f"note{i:03d}.txt": b"n" for i in range(100)})
    lines0 = len(audit_lines())
    send(ta, [(f"set{i:02d}.zip", hundred) for i in range(50)], subject="B4 five thousand files")
    st, j = inbox(ALICE)
    e = next(x for x in j["entries"] if x["subject"] == "B4 five thousand files")
    rec, size = record_of(ALICE, "B4 five thousand files")
    new = audit_lines()[lines0:]
    check(len(e["files"]) == I.MAX_LISTED and e["unlisted"] == {"notpdf": 5000 - I.MAX_LISTED} and size < 16 * 1024,
          f"b4: 5,000 files in fifty zips: {I.MAX_LISTED} are listed, 4,960 are counted as not PDFs; the record is "
          f"{size / 1024:.1f} KB", (len(e["files"]), e.get("unlisted"), size))
    check(len(new) == 1 + I.LOG_FILES_MAX + 1 and "4990 not PDFs" in str(new[-1].get("detail")),
          "b4: the log has the message, ten files and ONE line for the other 4,990", [x.get("name") for x in new][-2:])
    # the list route: the newest LIST_ENTRIES messages, each with at most MAX_LISTED files - whatever is on file.
    # A record as the reviewed build wrote it (every file listed) is cut the same way when it is sent.
    now = time.time()
    worst = {"name": "n" * I.NAME_MAX, "zip": "z" * I.NAME_MAX, "state": "refused", "reason": "r" * 300}
    for k in range(I.LIST_ENTRIES + 5):
        msg = {"id": f"synthetic-{k}", "from": "s" * I.SENDER_MAX, "subject": f"B4 synthetic {k:02d}", "attachments": [],
               "attached": 0}
        ent = I.new_entry(ALICE, msg, now + 10 + k)
        ent.update(status="done", counted=False, files=[dict(worst) for _ in range(5000 if k == 0 else I.MAX_LISTED)])
        I.save_entry(ALICE, ent)
    st, j = inbox(ALICE)
    body = json.dumps(j)
    old = next((x for x in j["entries"] if x["subject"] == "B4 synthetic 00"), None)
    check(len(j["entries"]) == I.LIST_ENTRIES and j["total"] > I.LIST_ENTRIES
          and all(len(x["files"]) <= I.MAX_LISTED for x in j["entries"]) and old is None,
          f"b4: the list route sends the newest {I.LIST_ENTRIES} messages of {j['total']}, none with more than "
          f"{I.MAX_LISTED} files", (len(j["entries"]), max(len(x["files"]) for x in j["entries"])))
    check(len(body) < 1024 * 1024,
          f"b4: thirty messages, each with forty files of the longest names and reasons there can be: the reply is "
          f"{len(body) / 1024:.0f} KB (under 1 MB)", len(body))
    view = server._inbound_view(ALICE, I.load_entry(ALICE, I.entry_id("synthetic-0", now + 10)), ALICE, False)
    check(len(view["files"]) == I.MAX_LISTED and view["unlisted"] == {"other": 5000 - I.MAX_LISTED},
          "b4: a record that lists 5,000 files (as the reviewed build wrote them) is sent as forty and a count",
          (len(view["files"]), view["unlisted"]))
    for name in list(I._entry_names(ALICE)):         # the made-up records go, so they are not in the next tests' way
        if (I.load_entry(ALICE, name) or {}).get("subject", "").startswith("B4 synthetic"):
            (I._mail_dir(ALICE) / f"{name}.json").unlink()
    H.drain(server)


def b5_the_door():
    on()
    ta = my_tag(ALICE)
    log = []
    real_d, real_a = I.decode, I.spool_add
    I.decode = lambda c: (log.append("decode"), real_d(c))[1]
    I.spool_add = lambda eid, k, raw: (log.append("spool"), real_a(eid, k, raw))[1]
    try:
        files = [(f"d{i}.pdf", H.drawing_pdf(1, title=f"DOOR {i}")) for i in range(4)]
        files += [("notes.txt", b"n" * 5000), ("set.zip", H.make_zip({"z.pdf": H.drawing_pdf(1, title="DOOR Z")})),
                  ("logo.png", b"\x89PNG\r\n\x1a\n" + b"p" * 5000)]
        n = len(server.JOBS)
        send(ta, files, subject="B5 one at a time")
        check(log == ["decode", "spool"] * 5 and len(server.JOBS) == n + 5,
              "b5: the door decodes one PDF or zip, writes it to the spool and only then decodes the next; a file "
              "that is neither is never decoded", log)
        # measured before it is decoded: a message over the size limit is refused with nothing decoded
        del log[:]
        old = I.MAX_MESSAGE_BYTES
        I.MAX_MESSAGE_BYTES = len(files[0][1]) + len(files[1][1]) - 1
        try:
            send(ta, files[:2], subject="B5 too big")
        finally:
            I.MAX_MESSAGE_BYTES = old
        e = entry_named(ALICE, "B5 too big")
        check(e["status"] == "refused" and log == [] and not spool_left(),
              "b5: a message one byte over the size limit is refused from the LENGTH of its base64 text: nothing is "
              "decoded, nothing spooled", (e["status"], log))
        sizes = [(len(raw), I.b64_size(H.payload(ta, [(nm, raw)])["Attachments"][0]["Content"])) for nm, raw in files]
        first400 = H.payload(ta, [files[0]])["Attachments"][0]["Content"][:400]       # 400 characters = 300 bytes
        wrapped = "\r\n".join(first400[i:i + 76] for i in range(0, 400, 76)) + "\r\n"
        check(all(a == b for a, b in sizes) and I.b64_size(wrapped) == 300 and I.b64_size(None) == 0
              and I.b64_head(H.payload(ta, [files[0]])["Attachments"][0]["Content"])[:5] == b"%PDF-",
              "b5: the size worked out from the base64 text is the decoded size to the byte (line breaks aside), and "
              "a PDF is told from its first eight characters", sizes[:3])
    finally:
        I.decode, I.spool_add = real_d, real_a
    # DOOR_SLOTS at a time: with every slot taken, a POST waits, then is told to come back; nothing is read
    took = [server._INBOUND_DOOR_SLOTS.acquire(blocking=False) for _ in range(I.DOOR_SLOTS)]
    wait = server._INBOUND_DOOR_WAIT_S
    server._INBOUND_DOOR_WAIT_S = 0.2
    try:
        total = inbox(ALICE)[1]["total"]
        st, j = post(H.payload(ta, [("slot.pdf", DRAW)], subject="B5 no slot"))
        check(all(took) and st == 503 and inbox(ALICE)[1]["total"] == total and not spool_left(),
              "b5: both slots taken: the POST gets 503 (the mail service sends it again) and nothing of it is kept",
              (st, j))
    finally:
        server._INBOUND_DOOR_WAIT_S = wait
        for t in took:
            if t:
                server._INBOUND_DOOR_SLOTS.release()
    st, _j = post(H.payload(ta, [("slot.pdf", DRAW)], subject="B5 a slot again"))
    read_all()
    check(st == 200 and entry_named(ALICE, "B5 a slot again")["files"][0]["state"] in ("opened", "same"),
          "b5: ... and with a slot free it is taken in as usual")
    H.drain(server)


def b6_the_reader_works_from_files():
    on()
    ta = my_tag(ALICE)
    big = [(f"big{i}.pdf", big_pdf(f"BIG {i}", 5)) for i in range(6)]
    pl = H.payload(ta, big, subject="B6 thirty megabytes of plans")
    n = len(server.JOBS)
    real = server._inbound_read
    server._inbound_read = lambda account, eid: None         # held back, so the door is not in the measurement
    try:
        post(pl)
        read_all()
    finally:
        server._inbound_read = real
    eid = entry_named(ALICE, "B6 thirty megabytes of plans")["id"]
    del pl

    def go():
        server._INBOUND_Q.put((ALICE, eid))
        read_all()

    peak = traced_peak(go)
    e = entry_named(ALICE, "B6 thirty megabytes of plans")
    check(sum(len(r) for _n, r in big) > 30 * MB and len(server.JOBS) == n + 6
          and all(f["state"] == "opened" for f in e["files"]),
          "b6: six 5 MB plans in one message all open", [f["state"] for f in e["files"]])
    check(peak < 8 * MB, f"b6: ... and reading them peaks at {peak / MB:.1f} MB of Python memory: each is hashed, "
                         "profiled and copied from its file, none is loaded", f"{peak / MB:.1f} MB")
    jobs = [server.JOBS[f["job"]] for f in e["files"]]
    check(all(Path(j["path"]).read_bytes() == raw for j, (_n, raw) in zip(jobs, big)) and not spool_left(),
          "b6: every project's file is the attachment, byte for byte; the spool is empty")
    H.drain(server)
    # held plans too: "Open all anyway" opens each from its file
    set_rules(ALICE, ["zzqxnomatchzz"], [])
    try:
        held = [(f"held{i}.pdf", big_pdf(f"HELD {i}", 3)) for i in range(4)]
        send(ta, held, subject="B6 held")
        e = entry_named(ALICE, "B6 held")
        n = len(server.JOBS)

        def go2():                                   # every held plan of the message, in one request
            H.call(server, "POST", "/api/inbound/open", {"entry": e["id"]}, cookie=CK[ALICE])

        peak = traced_peak(go2)
        check(all(f["state"] == "held" and f["kept"] for f in e["files"]) and len(server.JOBS) == n + 4 and peak < 8 * MB,
              f"b6: four 3 MB held plans opened anyway in one request: {peak / MB:.1f} MB of Python memory at the "
              "peak (each is opened from its kept file)", (len(server.JOBS) - n, f"{peak / MB:.1f} MB"))
    finally:
        set_rules(ALICE, [], [])
    H.drain(server)
    # a message that stopped the server while it was being read is read ONCE more after the restart, never a third time
    real = server._inbound_read
    n = len(server.JOBS)

    def dies(account, eid):                          # the read starts (it counts itself), then the server stops
        I.spool_try(eid)

    server._inbound_read = dies
    try:
        post(H.payload(ta, [("stops.pdf", H.drawing_pdf(1, title="STOPS"))], subject="B6 stops the server"))
        read_all()
        eid = entry_named(ALICE, "B6 stops the server")["id"]
        server._INBOUND_Q.put((ALICE, eid))          # the restart puts it back in line; it is cut short again
        read_all()
    finally:
        server._inbound_read = real
    server._INBOUND_Q.put((ALICE, eid))              # the second restart
    read_all()
    e = entry_named(ALICE, "B6 stops the server")
    check(e["status"] == "done" and "the server stopped while reading it" in (e["reason"] or "")
          and len(server.JOBS) == n and not spool_left() and e["files"][0]["state"] == "refused",
          "b6: a message whose read was cut short twice is not read a third time: its record says so and nothing "
          "stays spooled", e)
    server._inbound_read = dies
    try:
        post(H.payload(ta, [("restart.pdf", H.drawing_pdf(1, title="RESTART"))], subject="B6 one restart"))
        read_all()
        eid = entry_named(ALICE, "B6 one restart")["id"]
    finally:
        server._inbound_read = real
    server._INBOUND_Q.put((ALICE, eid))
    read_all()
    e = entry_named(ALICE, "B6 one restart")
    check(e["files"][0]["state"] == "opened" and len(server.JOBS) == n + 1,
          "b6: ... and one cut short ONCE (an ordinary restart) is read when the server is back", e["files"])
    # stopped AFTER the record was written and before the spool was emptied: the message is not read a second time
    drop = I.spool_drop
    I.spool_drop = lambda eid: None
    try:
        send(ta, [("twice.pdf", H.drawing_pdf(1, title="NOT TWICE"))], subject="B6 read, spool still there")
    finally:
        I.spool_drop = drop
    eid = entry_named(ALICE, "B6 read, spool still there")["id"]
    left = spool_left()
    server._INBOUND_Q.put((ALICE, eid))              # what the restart does with a spool it finds
    read_all()
    e = entry_named(ALICE, "B6 read, spool still there")
    check(left and not spool_left() and len(e["files"]) == 1 and e["files"][0]["state"] == "opened"
          and len(server.JOBS) == n + 2,
          "b6: a message that was read, whose spool was still there after a restart, is not read again: its record "
          "still lists its one file once", (left, e["files"]))
    H.drain(server)


def r1_the_limits_are_said():
    on()
    st, j = inbox(ALICE)
    lim = j["limits"]
    check(lim.get("attachments") == I.MAX_ATTACHMENTS == 50 and lim.get("unpacked_mb") == I.MAX_UNPACKED_BYTES // MB == 200
          and lim["pdfs"] == 20 and lim["pdf_pages"] == 300 and lim["per_day"] == 50 and lim["auto_pages"] == 25
          and lim["held_days"] == 7 and lim["message_mb"] == 35,
          "r1: the two new caps are in the page's own reply (50 attachments looked at, 200 MB unpacked), and the "
          "limits the first build chose are unchanged", lim)
    from takeoff_engine import detect_page as dp
    check("inbound.py" not in dp._ENGINE_FILES and "users.py" not in dp._ENGINE_FILES,
          "r1: no engine file was touched by any of this (inbound.py and users.py are not in the engine's list)")


for t in (r3_a_removed_account_is_forgotten, r3_the_tag_and_the_recipient, r3_small_ones,
          r2_a_confirmation_keeps_no_text, b1_zip_bombs_cost_no_memory, b2_one_count_for_the_whole_message,
          b3_only_the_first_attachments, b4_long_lists_stay_short, b5_the_door, b6_the_reader_works_from_files,
          r1_the_limits_are_said):
    run(t)

print(f"\n{checks} checks, {fails} failure(s)")
sys.exit(1 if fails else 0)
