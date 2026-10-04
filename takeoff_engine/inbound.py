"""Invitations to bid that arrive by e-mail and open as projects (2026-10-03).

THE OWNER'S RULE. "Users forward their invitations to bid to an address; we take the attachments, open a project,
run Detect and have it ready for review." The mail comes in through ONE inbound address at a mail service (Postmark
inbound): the service POSTs each message as JSON to POST /api/inbound/postmark (server.py). This module is every
part of that which needs nothing from the server: the three settings, each account's own address and keyword
rules, what is read from a message, which attachments are plans, and the record kept per message. server.py holds
the door, the routes and the glue that opens a project and queues Detect.

OFF BY DEFAULT. The feature exists only when all three settings are set (config()): INBOUND_HOOK_USER and
INBOUND_HOOK_PASS (the name and password the mail service sends with every POST) and INBOUND_ADDRESS (the service's
inbound address). With any one missing the door answers 404, the account routes answer 404 and the page draws
nothing.

WHOSE MAIL IT IS. Each account has its own tag (26 lower-case hex characters: 104 random bits). Its address is the
base address with "+<tag>" before the "@"; the mail service hands that part back as `MailboxHash`, and the account
is found from the tag ALONE - never from the sender, who is whoever wrote or forwarded the message. When
`MailboxHash` is empty the tag is read from the address the message was delivered to (`OriginalRecipient`); when
both are there and do not agree, the message is nobody's (recipient_tag). A tag nobody holds opens nothing. A new
tag replaces the old one, which stops working at once. A view-only account has no address, and a box with no
accounts has none either. An account that is REMOVED is forgotten here too (forget_account, called from
users.save): its tag, its rules, its records and its held plans go, so an account made later under the same name
starts with nothing of the old one's.

WHAT IS KEPT. PDF plans only: PDF attachments, and PDFs inside a .zip attachment (a name is never used as a path).
Per message the record holds: when, the sender, the subject, each file's name and what became of it. THE MESSAGE
TEXT IS NOT STORED and nothing is sent to any AI provider. A mail provider's own "confirm this forwarding address"
message (Gmail sends one) is no exception: confirmation() keeps two things out of it, a code made of digits and a
link to the provider's own confirmation page, and none of its words.

COUNT BEFORE YOU OPEN, ONE FILE AT A TIME (2026-10-04, after an outside review: one message of small zips, each
unpacking to a huge file, took the web process's memory). A message can come from anybody who knows a bid address,
so nothing in it decides how much this process holds:
  * only a message's first MAX_ATTACHMENTS attachments are looked at; the rest are counted and never decoded;
  * an attachment's size is worked out from its base64 text BEFORE it is decoded (b64_size), and a message past
    MAX_MESSAGE_BYTES is refused with nothing decoded;
  * each PDF or zip goes to the spool as it is decoded, one at a time, and is read from there as a FILE: a zip's
    table of contents is refused past ZIP_DIR_MAX_BYTES before it is read (_Capped), a member is tested by its
    first bytes before it is unpacked, and it is unpacked to disk in pieces (_unpack), with every byte counted
    against MAX_UNPACKED_BYTES for the whole message (gather);
  * a PDF is profiled, searched and opened from its file (profile, search_pdf), never from bytes held here;
  * a record lists at most MAX_LISTED files and counts the rest by reason (fold); the log gets at most
    LOG_FILES_MAX lines for refused files and one line for the rest; the list route sends the newest LIST_ENTRIES.

WHICH FILES ARE PLANS. The rule the bid hand-off already uses (bids.pick_files over the bid tool's file profile): a
page is a DRAWING SHEET when it is tabloid or larger or carries almost no text; a file reads as "drawings" when
most of its pages are sheets, as "text" when none is, else "mixed". Every drawings file opens as its own project;
if a message has none, its single largest file does - unless that one reads as text, which never opens. The page
test is profile() below, the same numbers as the bid tool's.

KEYWORD RULES (the account's own; both lists empty until it fills them). With KEYWORDS set, each PDF's text layer
is searched for them (whole words or phrases, case aside) and a plan in which none is found is NOT opened: it is
HELD, its record says so, and the person can open it anyway. An EXCLUSION word found in the subject or the sender
holds the whole message the same way. A held plan's bytes are kept HELD_DAYS days (HELD_MAX_BYTES per account,
oldest out first). A search sees only text the PDF carries as text: a scan, or lettering drawn as lines, has none,
and every result says how many pages had nothing to search - "not found" is never "not there".

Layout, all under <job store>/inbound/:
    addresses.json                      {account: {"tag", "at"}}
    rules.json                          {account: {"keywords", "exclude", "v"}}
    mail/<account key>/<id>.json        one record per message; <id> starts with the time, so names sort by time
    held/<account key>/<id>/<n>.bin     a plan that was held, until it is opened anyway or its days run out
    spool/<id>/                         a message's PDF and zip attachments between the door's 200 and the worker,
                                        so a restart loses nothing; removed when the message has been read. While
                                        it is read, the PDFs unpacked out of its zips sit beside them (u<k>.bin)

An admin loads one account's lists on the server (the words never pass through a tracked file):
    python -m takeoff_engine.inbound rules <account> --keywords words.txt --exclude skip.txt
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
import urllib.parse
import uuid
import zipfile
from pathlib import Path

import pymupdf

from takeoff_engine import bids as _bids
from takeoff_engine import users as _users

ENV_USER = "INBOUND_HOOK_USER"
ENV_PASS = "INBOUND_HOOK_PASS"
ENV_ADDRESS = "INBOUND_ADDRESS"

# ---- limits, each one said on screen (server.py /api/inbound `limits`) ---------------------------------------
MAX_BODY_BYTES = 80 * 1024 * 1024       # the door stops reading a POST here (the mail service's own messages are smaller)
MAX_MESSAGE_BYTES = 35 * 1024 * 1024    # one message's attachments, decoded: the mail service's own limit (35 MB)
MAX_ATTACHMENTS = 50                    # attachments of one message that are looked at; the rest are only counted
MAX_PDF_PAGES = 300                     # a longer PDF is not opened from e-mail (and only its first 300 pages are searched)
MAX_PER_DAY = 50                        # messages read per account in any 24 hours
MAX_PDFS = 20                           # PDFs looked at in one message (attached, or inside its zips)
AUTO_PAGES = 25                         # pages of one project that Detect runs on by itself
ZIP_MAX_FILES = 100                     # a zip with more entries is not unpacked
ZIP_MAX_BYTES = 200 * 1024 * 1024       # nor one whose table of contents adds up to more than this
ZIP_DIR_MAX_BYTES = 1024 * 1024         # a zip's table of contents: a longer one is refused before it is read
MAX_UNPACKED_BYTES = 200 * 1024 * 1024  # what ALL the zips of one message may unpack to, counted while unpacking
MAX_LISTED = 40                         # files listed one by one on a message's record; the rest are counted by reason
LOG_FILES_MAX = 10                      # refused files logged one by one (by the door, by the reader); then one line
LIST_ENTRIES = 30                       # messages the list route sends: the newest
READ_TRIES = 2                          # a spooled message is read at most twice (the second after a restart)
DOOR_SLOTS = 2                          # messages the door takes in at the same moment; the others wait their turn
SAME_FILE_S = 86400                     # the same bytes again within a day open nothing new
OVER_CAP_KEPT = 20                      # over-the-limit messages recorded one by one per day; the rest are counted
KEEP_ENTRIES = 200                      # records kept per account, newest first
CONFIRM_CODE_MIN, CONFIRM_CODE_MAX = 6, 12      # digits in a provider's confirmation code (Gmail's has 8 or 9)
CONFIRM_LINK_MAX = 400                  # characters in a provider's confirmation link
RULE_WORDS_MAX = 40                     # keywords (and exclusion words) per account
RULE_WORD_MIN, RULE_WORD_MAX = 2, 60    # characters in one keyword or phrase
FOUND_PAGES_KEPT = 50                   # pages listed per keyword (the count is always the whole count)
NO_TEXT_CHARS = 20                      # a page with fewer characters than this has nothing to search
HELD_DAYS = 7                           # a held plan is kept this long for "Open anyway"
HELD_MAX_BYTES = 1024 * 1024 * 1024     # ... and at most this much per account; the oldest go first
SUBJECT_MAX = 300
SENDER_MAX = 200
NAME_MAX = 120

_LOCK = threading.RLock()               # the address file, the rules and the records: one writer at a time
_ADDRESS = re.compile(r"^([A-Za-z0-9._\-]{1,64})@([A-Za-z0-9.\-]{1,255})$")
_TAG = re.compile(r"^[a-z0-9]{20,64}$")
TAG_BYTES = 13                          # 104 random bits, as 26 hex characters


# ---- the three settings ---------------------------------------------------------------------------------------
def config(env=None) -> "dict | None":
    """{"user", "password", "local", "domain"} when the feature is ON, else None. ON takes all three settings,
    non-empty, and an address shaped name@domain with no "+" of its own (the "+" is where an account's tag goes)."""
    e = os.environ if env is None else env
    user = str(e.get(ENV_USER) or "").strip()
    pw = str(e.get(ENV_PASS) or "").strip()
    m = _ADDRESS.match(str(e.get(ENV_ADDRESS) or "").strip())
    if not user or not pw or m is None or ":" in user:
        return None
    return {"user": user, "password": pw, "local": m.group(1), "domain": m.group(2).lower()}


def authorized(header, cfg: dict) -> bool:
    """The POST's `Authorization: Basic ...` against the two settings. Both halves are compared in constant time,
    and both are always compared: a wrong name answers no faster than a wrong password."""
    try:
        kind, _, b64 = str(header or "").strip().partition(" ")
        if kind.lower() != "basic":
            return False
        user, sep, pw = base64.b64decode(b64.strip(), validate=True).partition(b":")
        if not sep:
            return False
    except (ValueError, binascii.Error):
        return False
    ok_user = hmac.compare_digest(user, cfg["user"].encode("utf-8"))
    ok_pw = hmac.compare_digest(pw, cfg["password"].encode("utf-8"))
    return ok_user and ok_pw


# ---- where things live ----------------------------------------------------------------------------------------
def store_dir() -> Path:
    """Beside users.json in the job store, so it survives a redeploy and a throwaway store gets a throwaway inbox."""
    root = os.environ.get("TAKEOFF_JOBS_DIR")
    base = Path(root).expanduser() if root else Path(__file__).parent.parent / "out" / "jobs"
    return base / "inbound"


def _account_key(account) -> str:
    """A folder name for an account: a hash of its name, because an e-mail address is not a folder name."""
    return hashlib.sha256(str(account).encode("utf-8")).hexdigest()[:24]


def _write_json(path: Path, obj) -> None:
    """Atomic: a crash mid-write leaves the file as it was, never half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_bytes(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    os.replace(tmp, path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None


def _account_map(name: str) -> dict:
    d = _read_json(store_dir() / name)
    return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(d, dict) else {}


# ---- each account's address -----------------------------------------------------------------------------------
def may_have_address(account) -> bool:
    """An account on this box that is not view-only. No accounts, no addresses: with nobody signed in as anybody
    there is nobody for a project to belong to."""
    rec = _users.load().get(account) if account else None
    return isinstance(rec, dict) and str(rec.get("role") or "").strip().lower() != "viewer"


def tag_of(account) -> "str | None":
    rec = _account_map("addresses.json").get(account) if account else None
    tag = str(rec.get("tag") or "") if isinstance(rec, dict) else ""
    return tag if _TAG.match(tag) else None


def address_of(account, cfg: dict) -> "str | None":
    """The account's own address, or None when it has not asked for one (or may not have one)."""
    tag = tag_of(account) if may_have_address(account) else None
    return f"{cfg['local']}+{tag}@{cfg['domain']}" if tag else None


def new_address(account) -> "str | None":
    """Give the account a NEW tag (its first, or a replacement: the old one stops working at once, because the
    account is found from the tag on file and the old tag is no longer on file). Returns the tag, or None for an
    account that may not have one."""
    if not may_have_address(account):
        return None
    tag = secrets.token_hex(TAG_BYTES)
    with _LOCK:
        d = _account_map("addresses.json")
        d[account] = {"tag": tag, "at": time.time()}
        _write_json(store_dir() / "addresses.json", d)
    return tag


def account_exists(account) -> bool:
    """There is an account of this name on the box right now (view-only or not)."""
    return bool(account) and isinstance(_users.load().get(account), dict)


def forget_account(account) -> dict:
    """AN ACCOUNT THAT WAS REMOVED KEEPS NOTHING HERE: its tag, its keyword rules, its message records, its held
    plans and anything spooled for it all go. users.save() calls this for every name that leaves the account file
    and for every name that enters it, so an account made later under the same name starts clean whichever way the
    old one was removed (the Accounts page, the command line, a script). The projects themselves are not touched:
    they belong to the name, by the rule every other project follows. Returns what went, as counts."""
    gone = {"address": 0, "rules": 0, "records": 0, "held": 0, "spooled": 0}
    if not account:
        return gone
    with _LOCK:
        for name, key in (("addresses.json", "address"), ("rules.json", "rules")):
            d = _account_map(name)
            if account in d:
                d.pop(account)
                _write_json(store_dir() / name, d)
                gone[key] = 1
        try:
            names = [f for f in _mail_dir(account).iterdir() if f.is_file()]
        except OSError:
            names = []
        gone["records"] = sum(1 for f in names if f.name.endswith(".json"))
        try:
            gone["held"] = sum(1 for f in _held_dir(account).rglob("*.bin"))
        except OSError:
            pass
        shutil.rmtree(_mail_dir(account), ignore_errors=True)
        shutil.rmtree(_held_dir(account), ignore_errors=True)
        for acct, eid in spool_waiting():
            if acct == account:
                spool_drop(eid)
                gone["spooled"] += 1
    return gone


def accounts() -> list:
    """Every account that has asked for an address, by name."""
    return sorted(_account_map("addresses.json"))


def account_for(tag) -> "str | None":
    """The account a tag belongs to (the tag comes from recipient_tag). None for a tag nobody holds, a tag that
    was replaced, or an account that has since been removed or made view-only."""
    t = str(tag or "").strip().lower()
    if not _TAG.match(t):
        return None
    found = None
    for acct, rec in _account_map("addresses.json").items():
        if hmac.compare_digest(str(rec.get("tag") or "").encode("utf-8"), t.encode("utf-8")):
            found = acct
    return found if found is not None and may_have_address(found) else None


# ---- what is read from a message ------------------------------------------------------------------------------
def _line(v, cap: int) -> str:
    """One printable line: control characters and line breaks out, runs of space folded, cut to `cap`."""
    s = "".join(ch if ch.isprintable() else " " for ch in str(v or ""))
    return " ".join(s.split())[:cap]


def _plus_part(address) -> str:
    """The text between the first "+" and the "@" of one address, lower-cased ("" when it has none)."""
    s = str(address or "").strip()
    if s.endswith(">") and "<" in s:
        s = s[s.rfind("<") + 1:-1]
    local, at, _domain = s.rpartition("@")
    if not at or "+" not in local:
        return ""
    return local.split("+", 1)[1].strip().lower()


def recipient_tag(payload) -> tuple:
    """(tag, clash) for one inbound message. The tag is the mail service's `MailboxHash`. When that is EMPTY (a
    forward or a blind copy the service did not split) it is the part after the "+" in `OriginalRecipient`, the
    address the message was delivered to. When BOTH are there and they are not the same, the message is nobody's:
    clash is True and the tag is empty - one of the two would have to be picked, and a wrong pick puts one
    account's plans in another's list."""
    hashed = payload.get("MailboxHash")
    hashed = hashed.strip().lower() if isinstance(hashed, str) else ""
    rcpt = payload.get("OriginalRecipient")
    plus = _plus_part(rcpt) if isinstance(rcpt, str) else ""
    if hashed and plus and hashed != plus:
        return "", True
    return (hashed or plus), False


def message(payload) -> "dict | None":
    """What this feature reads from one inbound message in the mail service's JSON shape, or None when the body
    is not one (no MessageID, or attachments that are not a list). THE MESSAGE TEXT IS NOT READ HERE and so cannot
    be stored by anything downstream: the result carries the id, the tag, the sender, the subject and the
    attachments, and nothing else. Of the attachments only the first MAX_ATTACHMENTS are handed on; `attached` is
    how many the message carried."""
    if not isinstance(payload, dict):
        return None
    mid = payload.get("MessageID")
    if not isinstance(mid, str) or not mid.strip():
        return None
    files = payload.get("Attachments")
    if files is None:
        files = []
    if not isinstance(files, list):
        return None
    ff = payload.get("FromFull") if isinstance(payload.get("FromFull"), dict) else {}
    email = _line(ff.get("Email") or payload.get("From"), SENDER_MAX)
    name = _line(ff.get("Name") or payload.get("FromName"), SENDER_MAX)
    sender = (f"{name} <{email}>" if name and email and name != email else (email or name))[:SENDER_MAX]
    atts = []
    for a in files[:max(0, MAX_ATTACHMENTS)]:
        a = a if isinstance(a, dict) else {}             # not an attachment's shape: listed as unreadable
        atts.append({"name": a.get("Name"), "content": a.get("Content")})
    tag, clash = recipient_tag(payload)
    return {"id": mid.strip()[:200], "tag": tag, "clash": clash, "from": sender,
            "subject": _line(payload.get("Subject"), SUBJECT_MAX), "attachments": atts, "attached": len(files)}


def decode(content) -> "bytes | None":
    """An attachment's bytes from its base64 `Content`, or None when it is not base64."""
    if not isinstance(content, str):
        return None
    try:
        return base64.b64decode(content, validate=False)
    except (ValueError, binascii.Error):
        return None


def b64_bound(content) -> int:
    """A size an attachment's base64 `Content` cannot decode to more than, from its length alone (no pass over
    the text). The door adds these up first; b64_size is asked only when the sum is over the limit."""
    return len(content) * 3 // 4 if isinstance(content, str) else 0


def b64_size(content) -> int:
    """How many bytes an attachment's base64 `Content` decodes to, WITHOUT decoding it - so a message is measured
    before any of it is opened. Line breaks and spaces are not counted; any other stray character is, so the
    figure is never under the real one."""
    if not isinstance(content, str):
        return 0
    n = len(content) - content.count("\n") - content.count("\r") - content.count(" ")
    tail = content[-8:].rstrip()
    pad = 2 if tail.endswith("==") else 1 if tail.endswith("=") else 0
    return max(0, n * 3 // 4 - pad)


def b64_head(content) -> bytes:
    """An attachment's first bytes (up to 6), decoding only the first few characters of its base64 `Content`:
    enough to tell a PDF by how it starts, without decoding the attachment."""
    if not isinstance(content, str):
        return b""
    s = "".join(content[:64].split())[:8]
    s = s[:len(s) - len(s) % 4]
    try:
        return base64.b64decode(s, validate=False) if s else b""
    except (ValueError, binascii.Error):
        return b""


# A MAIL PROVIDER'S OWN CHECK THAT A FORWARDING ADDRESS IS REAL. Gmail (and Google Workspace) sends "Gmail
# Forwarding Confirmation" from one address, with a code and a link, and forwards nothing until the code is typed
# or the link opened. The subject is not tested: it is written in the account's own language.
#
# NOTHING OF ITS TEXT IS KEPT (2026-10-04). The message is recognised by its sender address, and a sender address
# is whatever the sender wrote: anybody who knows a bid address can send one. So out of such a message only two
# things are ever kept, each of a shape that cannot carry a sentence:
#     a CODE  - digits only, CONFIRM_CODE_MIN to CONFIRM_CODE_MAX of them;
#     a LINK  - https, on exactly the provider's own mail-settings host, on its "confirm" path, no query, no
#               fragment, no name or port in the address, at most CONFIRM_LINK_MAX characters.
# Neither found: nothing is kept and the message is an ordinary one. What a forged message can still plant is a
# wrong code (the provider refuses it) or a link to the provider's own page.
# Gmail's message carries TWO links to that host: the first confirms ("/mail/vf-..."), the second cancels
# ("/mail/uf-..."). Only the confirming path is ever kept, wherever it stands in the message.
_CONFIRM_SENDERS = {"forwarding-noreply@google.com": {"provider": "Gmail", "host": "mail-settings.google.com",
                                                      "path": "/mail/vf-"}}
_CONFIRM_URL = re.compile(r"https://[^\s<>\"'()\[\]{}|\\^`]+")
_CONFIRM_URL_OK = re.compile(r"[A-Za-z0-9:/._~%@+=,;!$&*-]+")
_CONFIRM_CODES = (re.compile(r"\(#\s*([0-9]+)\s*\)"), re.compile(r"code\s*:?\s*([0-9]+)", re.I))


def _confirm_code(v) -> "str | None":
    v = v if isinstance(v, str) else ""
    return v if v.isascii() and v.isdigit() and CONFIRM_CODE_MIN <= len(v) <= CONFIRM_CODE_MAX else None


def _confirm_link(v, rule: dict) -> "str | None":
    """`v` when it is the provider's own confirmation link, whole, else None."""
    if not isinstance(v, str) or not v or len(v) > CONFIRM_LINK_MAX or _CONFIRM_URL_OK.fullmatch(v) is None:
        return None
    try:
        u = urllib.parse.urlsplit(v)
    except ValueError:
        return None
    ok = (u.scheme == "https" and u.netloc == rule["host"] and u.path.startswith(rule["path"])
          and len(u.path) > len(rule["path"]) and not u.query and not u.fragment and "?" not in v and "#" not in v
          # no "." or ".." step (spelled out or as %2e), which a browser would walk back out of the confirm path
          and not any(urllib.parse.unquote(seg) in (".", "..") for seg in u.path.split("/")))
    return v if ok else None


def _confirm_rule(provider) -> "dict | None":
    return next((r for r in _CONFIRM_SENDERS.values() if r["provider"] == provider), None)


def confirmation(payload) -> "dict | None":
    """A provider's forwarding confirmation -> {"provider", "code", "link"} (either of the two may be None), or
    None when the message is not one or carries neither. Its text is read here to find the two and is not
    returned: nothing downstream can store it."""
    if not isinstance(payload, dict):
        return None
    ff = payload.get("FromFull") if isinstance(payload.get("FromFull"), dict) else {}
    sender = str(ff.get("Email") or payload.get("From") or "").strip().lower()
    rule = _CONFIRM_SENDERS.get(sender)
    if rule is None:
        return None
    text = payload.get("TextBody")
    text = text if isinstance(text, str) else ""
    subject = payload.get("Subject")
    subject = subject if isinstance(subject, str) else ""
    code = None
    for where in (subject, text):                    # the subject's "(#digits)" first, then the text's "code: digits"
        for rx in _CONFIRM_CODES:
            for m in rx.finditer(where[:20000]):
                code = code or _confirm_code(m.group(1))
    link = None
    for m in _CONFIRM_URL.finditer(text[:20000]):
        link = link or _confirm_link(m.group(0).rstrip(".,;:!"), rule)
    if code is None and link is None:
        return None
    return {"provider": rule["provider"], "code": code, "link": link}


def confirm_view(c) -> "dict | None":
    """What a stored confirmation may show: checked again as it is read, so a record written by an earlier build
    (which kept the message's text and a looser link) shows nothing but a code and a link of today's shape."""
    rule = _confirm_rule(c.get("provider")) if isinstance(c, dict) else None
    if rule is None:
        return None
    code, link = _confirm_code(c.get("code")), _confirm_link(c.get("link"), rule)
    if code is None and link is None:
        return None
    return {"provider": rule["provider"], "code": code, "link": link}


# ---- names and zips -------------------------------------------------------------------------------------------
_BAD_NAME = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]+')


def clean_name(name, fallback: str = "file") -> str:
    """A file name safe to show and to store under: the last part only (a name is never a path), the characters a
    file system refuses replaced, no leading or trailing dots, cut to NAME_MAX with its extension kept."""
    s = re.split(r"[\\/]", str(name or ""))[-1]
    s = " ".join(_BAD_NAME.sub("_", s).split()).strip(". ")
    if len(s) > NAME_MAX:
        stem, dot, ext = s.rpartition(".")
        s = (stem[:NAME_MAX - len(ext) - 1] + "." + ext) if dot and 0 < len(ext) <= 8 else s[:NAME_MAX]
    return s or fallback


def _is_pdf(name: str, raw: bytes) -> bool:
    return name.lower().endswith(".pdf") or raw[:5] == b"%PDF-"


def _is_zip(name: str, raw: bytes) -> bool:
    """By NAME only. A Word, Excel or PowerPoint file is a zip inside too, and unpacking one would list its
    insides as files somebody sent."""
    return name.lower().endswith(".zip")


def kind_of(name: str, raw: bytes) -> str:
    """"pdf" (the name ends .pdf, or the bytes start as a PDF), "zip" (the name ends .zip) or "other"."""
    return "pdf" if _is_pdf(name, raw) else "zip" if _is_zip(name, raw) else "other"


# ---- a message's PDFs, each a file on disk ------------------------------------------------------------------------
# Why a file was not opened, as a short code. A record lists MAX_LISTED files one by one and counts the rest under
# these four (fold), and the page turns the counts into one sentence.
NOT_PDF, OVER_PDFS, OVER_UNPACK, OTHER = "notpdf", "pdfs", "unpack", "other"
WHYS = (NOT_PDF, OVER_PDFS, OVER_UNPACK, OTHER)
_CHUNK = 64 * 1024                      # a zip member goes to disk in pieces of this size


def over_pdfs_reason() -> str:
    return f"not read: more than {MAX_PDFS} PDFs in one message"


def over_unpack_reason() -> str:
    return (f"not unpacked: the zips of one message may unpack to {MAX_UNPACKED_BYTES // (1024 * 1024)} MB "
            "in all, and this one is past that")


class _ZipDirTooLong(zipfile.BadZipFile):
    """A zip whose table of contents is longer than ZIP_DIR_MAX_BYTES."""


class _Capped:
    """A zip's own file as zipfile reads it, with one rule: NO SINGLE READ OF MORE THAN ZIP_DIR_MAX_BYTES. zipfile
    takes a zip's whole table of contents in one read and then builds an object for every entry in it, and a 35 MB
    zip can list 700,000 entries; here a table of contents past the cap is refused before a byte of it is read. A
    member is read in pieces of _CHUNK, far under the cap."""

    def __init__(self, path):
        self._fh = open(path, "rb")
        self._size = os.fstat(self._fh.fileno()).st_size

    def read(self, n=-1):
        left = max(0, self._size - self._fh.tell())
        want = left if n is None or n < 0 else min(int(n), left)
        if want > ZIP_DIR_MAX_BYTES:
            raise _ZipDirTooLong("the table of contents is past the cap")
        return self._fh.read(want)

    def seek(self, pos, whence=0):
        return self._fh.seek(pos, whence)

    def tell(self):
        return self._fh.tell()

    def seekable(self):
        return True

    def close(self):
        self._fh.close()


def _zip_open(path) -> tuple:
    """(zip, its file, its members, None), or (None, None, None, why the whole zip is refused). COUNTED BEFORE IT
    IS OPENED: the table of contents is capped as it is read (_Capped), then the number of files and the size the
    table of contents says they unpack to are tested, and only then is any member touched. The operating system's
    own droppings in a zip (__MACOSX/, ._name, .DS_Store) are not files anybody sent and are skipped."""
    fh = zf = None
    try:
        fh = _Capped(path)
        zf = zipfile.ZipFile(fh)
        infos = [i for i in zf.infolist() if not i.is_dir()]
        infos = [i for i in infos
                 if not i.filename.startswith("__MACOSX/")
                 and not re.split(r"[\\/]", i.filename)[-1].startswith("._")
                 and re.split(r"[\\/]", i.filename)[-1] != ".DS_Store"]
        why = None
        if len(infos) > ZIP_MAX_FILES:
            why = f"the zip holds {len(infos)} files (the limit is {ZIP_MAX_FILES}), so it was not unpacked"
        elif sum(max(0, int(i.file_size)) for i in infos) > ZIP_MAX_BYTES:
            why = f"the zip unpacks to more than {ZIP_MAX_BYTES // (1024 * 1024)} MB, so it was not unpacked"
    except _ZipDirTooLong:
        why, infos = (f"the zip's list of files is longer than {ZIP_DIR_MAX_BYTES // 1024} KB, so it was not "
                      "unpacked"), None
    except Exception:                                # noqa: BLE001 - whatever is wrong with it, it is not read
        why, infos = "the zip could not be read", None
    if why is not None:
        _zip_close(zf, fh)
        return None, None, None, why
    return zf, fh, infos, None


def _zip_close(zf, fh) -> None:
    for x in (zf, fh):
        try:
            if x is not None:
                x.close()
        except Exception:                            # noqa: BLE001
            pass


def _unlink(path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _unpack(zf, info, dest: Path, room: int) -> tuple:
    """One zip member whose name ends .pdf -> (what, bytes unpacked).
        "pdf"     it is a file at `dest`
        "notpdf"  its first bytes are not a PDF's: it was not unpacked
        "over"    it did not fit in `room` (what is left of MAX_UNPACKED_BYTES): unpacking stopped there
        "bad"     it could not be read out of the zip (damaged, or packed in a way this cannot read)
    The member goes to disk in pieces of _CHUNK; it is never whole in memory, and only "pdf" leaves a file."""
    n, over = 0, False
    try:
        with zf.open(info) as src:
            head = src.read(5)
            if head != b"%PDF-":
                return "notpdf", 0
            with open(dest, "wb") as out:
                piece = head
                while piece:
                    if n + len(piece) > room:
                        over = True
                        break
                    out.write(piece)
                    n += len(piece)
                    piece = src.read(_CHUNK)
    except Exception:                                # noqa: BLE001 - a damaged member costs that member only
        _unlink(dest)
        return "bad", n
    if over:
        _unlink(dest)
        return "over", max(0, room)                  # the room is spent: nothing after this one is unpacked
    return "pdf", n


def gather(items: list, work: Path) -> tuple:
    """(plans, refused, unpacked) for one spooled message. `items` are spool_list()'s rows: attachments that are
    already files on disk.
        plans    = [{"name", "zip": the zip it came out of or None, "path"}] - the message's first MAX_PDFS PDFs, in
                   the order they were attached (a zip's in the zip's own order), EACH A FILE: a PDF attachment is
                   its spooled file, a zip member is unpacked into `work`
        refused  = [{"name", "zip", "reason", "why"}] - every other file, `why` one of WHYS
        unpacked = the bytes unpacked out of every zip of the message together; never more than MAX_UNPACKED_BYTES
    Nothing is held in memory and no member's path is used for anything (only the last part of its name, cleaned).
    A zip inside a zip is not opened. Once MAX_PDFS PDFs are in hand, or the unpacked bytes are spent, no further
    member is unpacked: each is listed with that reason."""
    plans, refused, used, k = [], [], 0, 0
    for it in items:
        name = clean_name(it.get("name"))
        if it.get("kind") == "pdf":
            pdf = name if name.lower().endswith(".pdf") else name + ".pdf"
            if len(plans) >= MAX_PDFS:
                refused.append({"name": pdf, "zip": None, "reason": over_pdfs_reason(), "why": OVER_PDFS})
            else:
                plans.append({"name": pdf, "zip": None, "path": Path(it["path"])})
            continue
        if it.get("kind") != "zip":
            refused.append({"name": name, "zip": None, "reason": "not a PDF", "why": NOT_PDF})
            continue
        zf, fh, infos, why = _zip_open(it["path"])
        if why is not None:
            refused.append({"name": name, "zip": None, "reason": why, "why": OTHER})
            continue
        try:
            for i in infos:
                inner = clean_name(i.filename)
                line = {"name": inner, "zip": name}
                if i.flag_bits & 0x1:
                    refused.append(dict(line, reason="password-protected inside the zip", why=OTHER))
                elif inner.lower().endswith(".zip"):
                    refused.append(dict(line, reason="a zip inside the zip: not opened", why=OTHER))
                elif not inner.lower().endswith(".pdf"):
                    refused.append(dict(line, reason="not a PDF", why=NOT_PDF))
                elif len(plans) >= MAX_PDFS:
                    refused.append(dict(line, reason=over_pdfs_reason(), why=OVER_PDFS))
                elif used >= MAX_UNPACKED_BYTES:
                    refused.append(dict(line, reason=over_unpack_reason(), why=OVER_UNPACK))
                else:
                    dest = Path(work) / ("u%04d.bin" % k)
                    k += 1
                    what, n = _unpack(zf, i, dest, MAX_UNPACKED_BYTES - used)
                    used += n
                    if what == "pdf":
                        plans.append({"name": inner, "zip": name, "path": dest})
                    elif what == "notpdf":
                        refused.append(dict(line, reason="not a PDF inside, whatever its name says", why=OTHER))
                    elif what == "over":
                        refused.append(dict(line, reason=over_unpack_reason(), why=OVER_UNPACK))
                    else:
                        refused.append(dict(line, reason="could not be read out of the zip", why=OTHER))
            if not infos:
                refused.append({"name": name, "zip": None, "reason": "the zip is empty", "why": OTHER})
        finally:
            _zip_close(zf, fh)
    return plans, refused, used


def fold(lines: list, room: int) -> tuple:
    """(listed, more): the first `room` of `lines` are listed one by one, the rest are COUNTED by why they were
    not opened - {why: how many} - so a message with twenty thousand files makes a record of forty lines and four
    numbers, and none of the twenty thousand is dropped unsaid."""
    room = max(0, int(room))
    listed = [{k: v for k, v in f.items() if k != "why"} for f in lines[:room]]
    more = {}
    for f in lines[room:]:
        w = f.get("why") if f.get("why") in WHYS else OTHER
        more[w] = more.get(w, 0) + 1
    return listed, more


def add_counts(a, b) -> dict:
    """Two {why: how many} added, field by field (only the four known reasons, only whole positive numbers)."""
    out = {}
    for w in WHYS:
        n = 0
        for d in (a, b):
            v = d.get(w) if isinstance(d, dict) else None
            n += v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0
        if n:
            out[w] = n
    return out


def file_sha(path) -> str:
    """The SHA-256 of a file, read in pieces."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for piece in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(piece)
    return h.hexdigest()


def _open_pdf(src):
    """PyMuPDF's document for a PDF given as a FILE (a path: read from the file as it is needed, never loaded
    whole) or as bytes."""
    if isinstance(src, (bytes, bytearray, memoryview)):
        return pymupdf.open(stream=src, filetype="pdf")
    return pymupdf.open(str(src), filetype="pdf")


def _starts_as_pdf(src) -> bool:
    if isinstance(src, (bytes, bytearray, memoryview)):
        return bytes(src[:5]) == b"%PDF-"
    try:
        with open(src, "rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False


# ---- drawing or text: the bid hand-off's rule ---------------------------------------------------------------------
# A page is a DRAWING SHEET when it is tabloid or larger (short side >= 11 in = 792 pt AND long side >= 17 in =
# 1224 pt: 11x17 half-size sets and every larger sheet; letter and legal are not) OR when it carries almost no
# text. The same three numbers the bid tool profiles a bundle's PDFs with, so a file is judged the same way
# whether it came from a bid or from an e-mail.
_SHEET_SHORT_SIDE_PT = 792.0
_SHEET_LONG_SIDE_PT = 1224.0
_TEXT_PAGE_CHARS = 400
_PROFILE_SAMPLE = 80                    # pages read; a longer file's tail is extrapolated


def profile(src) -> dict:
    """{"ok", "why", "pages", "sheet_pages", "text_pages", "hint"} for one PDF - a file on disk (a path) or its
    bytes. ok False (with `why` in plain words) when PyMuPDF cannot open it, it needs a password or it has no page.
    hint is "drawings" (most pages are sheets), "text" (none is) or "mixed". Page sizes and the text layer only: no
    drawing is read."""
    bad = {"ok": False, "pages": None, "sheet_pages": None, "text_pages": None, "hint": "unknown"}
    if not _starts_as_pdf(src):
        return dict(bad, why="not a PDF inside, whatever its name says")
    try:
        doc = _open_pdf(src)
    except Exception:                                # noqa: BLE001 - any failure to open is the same answer
        return dict(bad, why="could not be opened: the PDF is damaged or incomplete")
    try:
        if doc.needs_pass:
            return dict(bad, why="password-protected: it could not be opened")
        n = int(doc.page_count)
        if n < 1:
            return dict(bad, why="the PDF has no pages")
        sheet = seen = 0
        for i in range(min(n, _PROFILE_SAMPLE)):
            pg = doc[i]
            r = pg.rect
            short, long_ = min(r.width, r.height), max(r.width, r.height)
            try:
                chars = len(pg.get_text("text").strip())
            except Exception:                        # noqa: BLE001 - a page whose text cannot be read has none
                chars = 0
            seen += 1
            if (short >= _SHEET_SHORT_SIDE_PT and long_ >= _SHEET_LONG_SIDE_PT) or chars < _TEXT_PAGE_CHARS:
                sheet += 1
        if n > seen:                                 # extrapolate the tail of a long file
            sheet = round(sheet * n / seen)
        hint = "drawings" if sheet >= max(1, 0.6 * n) else "text" if sheet == 0 else "mixed"
        return {"ok": True, "why": "", "pages": n, "sheet_pages": sheet, "text_pages": n - sheet, "hint": hint}
    except Exception:                                # noqa: BLE001
        return dict(bad, why="could not be read: the PDF is damaged or incomplete")
    finally:
        doc.close()


def choose(files: list) -> list:
    """Which of a message's readable PDFs open as projects - indexes into `files` ([{"size", "hint"}]). The bid
    chooser's own rule (bids.pick_files): every file that reads as drawings; when none does, the single largest.
    One difference, because nobody is there to untick it: a file that reads as text only never opens."""
    cands = [{"i": i, "size": int(f.get("size") or 0), "hint": f.get("hint")} for i, f in enumerate(files)]
    return [i for i in _bids.pick_files(cands) if files[i].get("hint") != "text"]


def _pages(n) -> str:
    return f"{n} page" + ("" if n == 1 else "s")


def why_not_opened(f: dict) -> str:
    """Why a readable PDF was not opened as a project, in plain words, with the numbers behind it."""
    n, s = int(f.get("pages") or 0), int(f.get("sheet_pages") or 0)
    if f.get("hint") == "text":
        return f"reads as a text document, not drawings ({_pages(n)}, none a drawing sheet)"
    return (f"reads as mostly text ({s} of {_pages(n)} a drawing sheet). "
            "Upload it yourself if it holds plans.")


# ---- which pages Detect runs on by itself ---------------------------------------------------------------------
# Every page the sheet triage does not call another trade, the most certain first: "core" (site work), "plan" (a
# floor or foundation plan), "maybe", then "unknown" (no text to judge by: a scan). The bid hand-off queues "core"
# only; measured 2026-10-03 on the two shared plan sets (11 pages), that leaves 6 undetected - the road layout and
# both road-grading sheets read "maybe", the three structural sheets read "plan" - and all 11 are plans.
_TIER_RANK = {"core": 0, "plan": 1, "maybe": 2, "unknown": 3}


def auto_pages(triage_pages, cap: int = None) -> dict:
    """{"pages": the page indexes to detect, in order, "wanted": how many qualified, "total": pages in the set,
    "cap"}. More than `cap` qualify: the first `cap` by rank then page order, and the card says so."""
    cap = AUTO_PAGES if cap is None else int(cap)
    rows = [r for r in (triage_pages or []) if isinstance(r, dict) and isinstance(r.get("page"), int)]
    want = sorted((r for r in rows if r.get("tier") in _TIER_RANK), key=lambda r: (_TIER_RANK[r["tier"]], r["page"]))
    return {"pages": [r["page"] for r in want][:max(0, cap)], "wanted": len(want), "total": len(rows), "cap": cap}


# ---- keyword rules --------------------------------------------------------------------------------------------
class StaleRules(Exception):
    """A save built on a list that has changed since it was read (another window saved first)."""


def clean_words(words) -> list:
    """A keyword list off the wire or out of a file: each one a single line with its spaces folded, RULE_WORD_MIN
    to RULE_WORD_MAX characters, no two the same (case aside), at most RULE_WORDS_MAX, in the order given."""
    out, seen = [], set()
    for w in words if isinstance(words, (list, tuple)) else []:
        if not isinstance(w, str):
            continue
        w = _line(w, RULE_WORD_MAX)
        if len(w) < RULE_WORD_MIN or w.casefold() in seen:
            continue
        seen.add(w.casefold())
        out.append(w)
        if len(out) >= RULE_WORDS_MAX:
            break
    return out


def rules_of(account) -> dict:
    """{"keywords", "exclude", "v"} for an account; both lists empty and v 0 until it saves some."""
    rec = _account_map("rules.json").get(account) if account else None
    rec = rec if isinstance(rec, dict) else {}
    try:
        v = max(0, int(rec.get("v") or 0))
    except (TypeError, ValueError):
        v = 0
    return {"keywords": clean_words(rec.get("keywords")), "exclude": clean_words(rec.get("exclude")), "v": v}


def save_rules(account, keywords, exclude, v=None) -> dict:
    """Replace an account's two lists. `v` is the version the caller read: when it is given and is not the one on
    file, nothing is written and StaleRules is raised. Returns the saved rules (with the new version)."""
    with _LOCK:
        cur = rules_of(account)
        if v is not None and int(v) != cur["v"]:
            raise StaleRules()
        d = _account_map("rules.json")
        d[account] = {"keywords": clean_words(keywords), "exclude": clean_words(exclude), "v": cur["v"] + 1,
                      "at": time.time()}
        _write_json(store_dir() / "rules.json", d)
    return rules_of(account)


def _word_re(word: str):
    """A keyword as a pattern: whole words only (no letter or digit on either side), case aside, and the spaces
    inside a phrase match any run of white space, so a phrase broken over two lines is still found."""
    body = r"\s+".join(re.escape(part) for part in word.split())
    return re.compile(r"(?<![^\W_])" + body + r"(?![^\W_])", re.I)


def excluded_by(words, *texts) -> "str | None":
    """The first exclusion word found (whole word, case aside) in any of `texts` - the subject and the sender."""
    for w in clean_words(list(words or [])):
        rx = _word_re(w)
        if any(rx.search(str(t or "")) for t in texts):
            return w
    return None


def search_pdf(src, words, max_pages: int = None) -> "dict | None":
    """Where each keyword is printed in one PDF's TEXT LAYER (`src` is a file on disk, or the PDF's bytes).
        {"found": [{"word", "pages": [0-based page indexes, the first FOUND_PAGES_KEPT], "count": pages it is on}],
         "searched": pages read, "no_text": how many of those had nothing to search, "pages": pages in the file}
    Only keywords that were found are listed. `no_text` is what keeps "not found" from reading as "not there": a
    scanned page, or one whose lettering is drawn as lines, carries no text and cannot be searched. EVERY page that
    has any text is searched, however little: a sheet whose only text is a short title still has that title found.
    A page counts as `no_text` when it has fewer than NO_TEXT_CHARS characters AND none of the keywords is in
    them. None when the file cannot be opened."""
    words = clean_words(list(words or []))
    cap = MAX_PDF_PAGES if max_pages is None else int(max_pages)
    try:
        doc = _open_pdf(src)
    except Exception:                                # noqa: BLE001
        return None
    try:
        if doc.needs_pass:
            return None
        n = int(doc.page_count)
        rxs = [(w, _word_re(w)) for w in words]
        hits = {w: [] for w in words}
        no_text = 0
        searched = min(n, cap)
        for i in range(searched):
            try:
                text = doc[i].get_text("text")
            except Exception:                        # noqa: BLE001 - a page whose text cannot be read has none
                text = ""
            hit = False
            for w, rx in rxs:
                if rx.search(text):
                    hits[w].append(i)
                    hit = True
            if not hit and len("".join(text.split())) < NO_TEXT_CHARS:
                no_text += 1
        found = [{"word": w, "pages": hits[w][:FOUND_PAGES_KEPT], "count": len(hits[w])} for w in words if hits[w]]
        return {"found": found, "searched": searched, "no_text": no_text, "pages": n}
    except Exception:                                # noqa: BLE001
        return None
    finally:
        doc.close()


def match_record(search) -> "dict | None":
    """The match record kept with a project, built field by field (an allow-list): which keywords were found on
    which pages, and how much of the file could be searched. None when no search was made (no keywords set)."""
    if not isinstance(search, dict):
        return None
    found = []
    for f in search.get("found") or []:
        if isinstance(f, dict) and isinstance(f.get("word"), str):
            pages = [int(p) for p in (f.get("pages") or []) if isinstance(p, int) and not isinstance(p, bool)]
            found.append({"word": f["word"][:RULE_WORD_MAX], "pages": pages[:FOUND_PAGES_KEPT],
                          "count": int(f.get("count") or len(pages))})
    return {"found": found[:RULE_WORDS_MAX], "searched": int(search.get("searched") or 0),
            "no_text": int(search.get("no_text") or 0), "pages": int(search.get("pages") or 0)}


# ---- the record kept per message --------------------------------------------------------------------------------
def _mail_dir(account: str) -> Path:
    return store_dir() / "mail" / _account_key(account)


def _message_key(message_id: str) -> str:
    return hashlib.sha256(str(message_id).encode("utf-8")).hexdigest()[:16]


def entry_id(message_id: str, now: float) -> str:
    """<milliseconds, 13 digits>-<hash of the MessageID>: names sort by time, and the same message is found by its
    ending whenever it arrives."""
    return "%013d-%s" % (int(now * 1000), _message_key(message_id))


_ENTRY_ID = re.compile(r"^\d{13}-[0-9a-f]{16}$")


def is_entry_id(eid) -> bool:
    return isinstance(eid, str) and _ENTRY_ID.match(eid) is not None


def _entry_names(account: str) -> list:
    """Record ids for an account, NEWEST FIRST."""
    try:
        names = [f.name[:-5] for f in _mail_dir(account).iterdir() if f.name.endswith(".json")]
    except OSError:
        return []
    return sorted((n for n in names if _ENTRY_ID.match(n)), reverse=True)


def load_entry(account: str, eid: str) -> "dict | None":
    if not is_entry_id(eid):
        return None
    d = _read_json(_mail_dir(account) / f"{eid}.json")
    return d if isinstance(d, dict) else None


def save_entry(account: str, entry: dict) -> None:
    """Write one record; the oldest beyond KEEP_ENTRIES go, and what was held for them goes too."""
    with _LOCK:
        _write_json(_mail_dir(account) / f"{entry['id']}.json", entry)
        for old in _entry_names(account)[KEEP_ENTRIES:]:
            try:
                (_mail_dir(account) / f"{old}.json").unlink()
            except OSError:
                pass
            held_drop(account, old)


def entries(account: str, limit: int = 30, since: float = None) -> list:
    """An account's records, newest first: at most `limit`, and with `since` only those received at or after it."""
    out = []
    for name in _entry_names(account):
        if since is not None and int(name[:13]) < int(since * 1000):
            break
        e = load_entry(account, name)
        if e is not None:
            out.append(e)
            if len(out) >= limit:
                break
    return out


def count_entries(account: str) -> int:
    return len(_entry_names(account))


def find_message(account: str, message_id: str) -> "dict | None":
    """The record of a message that already arrived (the mail service delivers one again when it saw no 200)."""
    key = _message_key(message_id)
    for name in _entry_names(account):
        if name.endswith("-" + key):
            e = load_entry(account, name)
            if e is not None and e.get("message_id") == message_id:
                return e
    return None


def new_entry(account: str, msg: dict, now: float) -> dict:
    """`files` lists at most MAX_LISTED files one by one; `unlisted` counts the rest by reason; `attached` is how
    many attachments the message carried and `unread` how many of them were past MAX_ATTACHMENTS and not looked
    at."""
    attached = int(msg.get("attached") or 0)
    return {"v": 1, "id": entry_id(msg["id"], now), "account": account, "message_id": msg["id"], "received": now,
            "from": msg["from"], "subject": msg["subject"], "status": "received", "reason": None, "counted": True,
            "again": 0, "more": 0, "confirm": None, "excluded": None, "pending": [], "files": [],
            "attached": attached, "unread": max(0, attached - len(msg.get("attachments") or [])), "unlisted": {}}


def read_today(account: str, now: float) -> int:
    """Messages READ for this account in the last 24 hours (the ones the daily limit counts)."""
    return sum(1 for e in entries(account, limit=10 ** 6, since=now - 86400) if e.get("counted"))


def over_cap_today(account: str, now: float) -> list:
    """The over-the-limit records of the last 24 hours, newest first."""
    return [e for e in entries(account, limit=10 ** 6, since=now - 86400) if e.get("status") == "over"]


def same_file(account: str, sha: str, now: float, skip: str = None, alive=None) -> "tuple | None":
    """(record, file) for the same bytes that arrived for this account in the last SAME_FILE_S seconds and opened a
    project (or were themselves the same as an earlier one), newest first; None when there is none. `alive` is
    asked about each such file's project: one whose project has been DELETED since is passed over, so a person who
    deleted a project and forwards the message again gets it back instead of being told it is already there."""
    for e in entries(account, limit=10 ** 6, since=now - SAME_FILE_S):
        if e.get("id") == skip:
            continue
        for f in e.get("files") or []:
            if isinstance(f, dict) and f.get("sha") == sha and f.get("state") in ("opened", "same"):
                if alive is None or alive(f.get("job")):
                    return e, f
    return None


def latest_confirmation(account: str) -> "dict | None":
    """The newest forwarding confirmation on file for the account: {"provider", "code", "link", "at"} - a code
    and a link, never text (confirm_view)."""
    for e in entries(account, limit=KEEP_ENTRIES):
        c = confirm_view(e.get("confirm"))
        if c is not None:
            return dict(c, at=e.get("received"))
    return None


# ---- held plans: kept so the person can open them anyway --------------------------------------------------------
def _held_dir(account: str, eid: str = None) -> Path:
    d = store_dir() / "held" / _account_key(account)
    return d / eid if eid else d


def held_put(account: str, eid: str, n: int, src) -> None:
    """Keep one held plan: `src` is the plan's FILE (copied, never loaded) or its bytes."""
    d = _held_dir(account, eid)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / ("%03d.bin" % int(n))
    if isinstance(src, (bytes, bytearray, memoryview)):
        dest.write_bytes(src)
    else:
        shutil.copyfile(str(src), str(dest))


def _held_in_time(eid: str, now: float = None) -> bool:
    """A held plan is offered for HELD_DAYS days from its message's arrival - by the clock, whether or not the
    file has been swept yet."""
    now = time.time() if now is None else now
    return is_entry_id(eid) and int(eid[:13]) >= int((now - HELD_DAYS * 86400) * 1000)


def held_path(account: str, eid: str, n) -> "Path | None":
    """The file of a held plan that is still kept and still within its days, else None. A path, not bytes: Open
    anyway opens the plan from the file, so twenty held plans opened at once are never twenty files in memory."""
    if not _held_in_time(eid) or isinstance(n, bool) or not isinstance(n, int) or n < 0:
        return None
    p = _held_dir(account, eid) / ("%03d.bin" % n)
    return p if p.is_file() else None


def held_has(account: str, eid: str, n) -> bool:
    return held_path(account, eid, n) is not None


def held_drop(account: str, eid: str, n=None) -> None:
    """Forget one held plan, or (n None) everything held for a message."""
    if not is_entry_id(eid):
        return
    d = _held_dir(account, eid)
    try:
        for f in ([d / ("%03d.bin" % int(n))] if n is not None else list(d.iterdir())):
            try:
                f.unlink()
            except OSError:
                pass
        if not any(d.iterdir()):
            d.rmdir()
    except OSError:
        pass


def _held_prune_dir(root: Path, now: float) -> int:
    """One account's held folder: what was held longer than HELD_DAYS goes, then the oldest until the folder is
    under HELD_MAX_BYTES. Returns how many plans went."""
    try:
        dirs = sorted((d for d in root.iterdir() if d.is_dir() and _ENTRY_ID.match(d.name)),
                      key=lambda d: d.name, reverse=True)                     # newest first
    except OSError:
        return 0
    gone, total = 0, 0
    for d in dirs:
        try:
            files = sorted(f for f in d.iterdir() if f.is_file())
        except OSError:
            continue
        size = 0
        for f in files:
            try:
                size += f.stat().st_size
            except OSError:
                pass
        if not _held_in_time(d.name, now) or total + size > HELD_MAX_BYTES:
            for f in files:
                try:
                    f.unlink()
                    gone += 1
                except OSError:
                    pass
            try:
                d.rmdir()
            except OSError:
                pass
        else:
            total += size
    return gone


def held_prune(account: str, now: float) -> int:
    """Sweep one account's held plans (after each of its messages). A plan that is gone is no longer offered: its
    record says a plan that was not opened is kept HELD_DAYS days."""
    return _held_prune_dir(_held_dir(account), now)


def held_prune_all(now: float) -> int:
    """Sweep every account's held plans (at start, and every hour the reader is idle), so a held plan does not
    outlive its days on disk just because its account got no more mail."""
    try:
        roots = [d for d in (store_dir() / "held").iterdir() if d.is_dir()]
    except OSError:
        return 0
    return sum(_held_prune_dir(d, now) for d in roots)


# ---- the spool: a message's PDFs and zips between the door's 200 and the worker ---------------------------------
def _spool_dir(eid: str) -> Path:
    return store_dir() / "spool" / eid


def spool_add(eid: str, n: int, raw: bytes) -> int:
    """ONE attachment goes to the spool, as the door decodes it: the door never holds two decoded at once. Files
    are named by number, never by the attachment's name. Returns its size."""
    d = _spool_dir(eid)
    d.mkdir(parents=True, exist_ok=True)
    (d / ("%03d.bin" % int(n))).write_bytes(raw)
    return len(raw)


def spool_commit(account: str, eid: str, files: list) -> None:
    """files = [{"n", "name", "kind": "pdf" | "zip", "size"}] for what spool_add wrote. Written last and BEFORE the
    door answers 200, so a message the mail service will not send again is on disk; a spool with no manifest is a
    message the door never answered for (spool_sweep removes it)."""
    rows = [{"n": int(f["n"]), "name": str(f["name"]), "kind": str(f["kind"]), "size": int(f["size"])} for f in files]
    _write_json(_spool_dir(eid) / "manifest.json", {"account": account, "entry": eid, "files": rows, "tries": 0})


def _manifest(eid: str) -> "dict | None":
    m = _read_json(_spool_dir(eid) / "manifest.json") if is_entry_id(eid) else None
    ok = isinstance(m, dict) and isinstance(m.get("files"), list) and isinstance(m.get("account"), str)
    return m if ok else None


def spool_list(eid: str) -> "tuple | None":
    """(account, [{"n", "name", "kind", "size", "path"}]) for a spooled message - WHERE each attachment is, never
    its bytes - or None when the message is not (fully) there."""
    m = _manifest(eid)
    if m is None:
        return None
    d, items = _spool_dir(eid), []
    for f in m["files"]:
        try:
            p = d / ("%03d.bin" % int(f["n"]))
            size = p.stat().st_size
        except (OSError, KeyError, TypeError, ValueError):
            continue
        items.append({"n": int(f["n"]), "name": str(f.get("name") or "file"), "kind": str(f.get("kind") or "other"),
                      "size": size, "path": p})
    return m["account"], items


def spool_try(eid: str) -> int:
    """Count one more read of a spooled message and return how many there have been, this one included. The reader
    asks before it touches the message: one that has been started READ_TRIES times and is still spooled stopped
    the server each time, and is not read again."""
    with _LOCK:
        m = _manifest(eid)
        if m is None:
            return 0
        try:
            tries = max(0, int(m.get("tries") or 0)) + 1
        except (TypeError, ValueError):
            tries = 1
        m["tries"] = tries
        _write_json(_spool_dir(eid) / "manifest.json", m)
        return tries


def spool_work(eid: str) -> Path:
    """Where the PDFs unpacked out of a spooled message's zips go while it is read: beside its attachments, as
    u<k>.bin. What an earlier read that was cut short left there is removed first."""
    d = _spool_dir(eid)
    try:
        for f in d.iterdir():
            if f.name.startswith("u") and f.name.endswith(".bin"):
                _unlink(f)
    except OSError:
        pass
    return d


def spool_drop(eid: str) -> None:
    if is_entry_id(eid):
        shutil.rmtree(_spool_dir(eid), ignore_errors=True)


def spool_sweep(now: float, older_s: float = 600.0) -> int:
    """Remove spooled attachments that belong to no message: a folder with no manifest, older than `older_s` (the
    door was stopped before it could answer, so the mail service sent the message again). Returns how many."""
    gone = 0
    try:
        dirs = [p for p in (store_dir() / "spool").iterdir() if p.is_dir()]
    except OSError:
        return 0
    for d in dirs:
        old = _ENTRY_ID.match(d.name) is None or int(d.name[:13]) < int((now - older_s) * 1000)
        if old and not (d / "manifest.json").is_file():
            shutil.rmtree(d, ignore_errors=True)
            gone += 1
    return gone


def spool_waiting() -> list:
    """[(account, entry id)] still spooled - what a restart has to pick up - oldest first."""
    out = []
    try:
        names = sorted(p.name for p in (store_dir() / "spool").iterdir() if p.is_dir())
    except OSError:
        return out
    for eid in names:
        m = _read_json(_spool_dir(eid) / "manifest.json")
        if _ENTRY_ID.match(eid) and isinstance(m, dict) and isinstance(m.get("account"), str):
            out.append((m["account"], eid))
    return out


# ---- the activity log -----------------------------------------------------------------------------------------
# One line per message received, per project opened, per file refused. Where the build has the team's activity log
# (takeoff_engine/audit.py) the line goes through it; this build has none, so the same line is written here, in
# that log's own layout (<job store>/audit/audit-YYYY-MM.jsonl, one JSON object per line, the same seven fields),
# and the log reads these lines as its own the day it arrives. Never a password, a tag, a keyword, a file's
# contents or a path; never the message text. Best effort: a line that cannot be written never costs the message.
_AUDIT_FIELDS = ("ts", "user", "action", "job", "name", "page", "detail")
_AUDIT_CAPS = {"user": 200, "action": 40, "job": 64, "name": 200, "detail": 200}
_AUDIT_LOCK = threading.Lock()
_AUDIT_WARNED = False


def audit(action: str, user=None, job=None, name=None, detail=None) -> bool:
    global _AUDIT_WARNED
    event = {"user": user, "action": action, "job": job, "name": name, "detail": detail}
    try:
        from takeoff_engine import audit as _team_log      # the team's log, where the build has one
    except ImportError:
        _team_log = None
    try:
        if _team_log is not None:
            return bool(_team_log.write(event))
        g = time.gmtime()
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", g), "page": None}
        for k, cap in _AUDIT_CAPS.items():
            v = event.get(k)
            s = str(v).strip() if v is not None else ""
            rec[k] = s[:cap] if s else None
        if not rec["action"]:
            return False
        root = os.environ.get("TAKEOFF_JOBS_DIR")
        base = Path(root).expanduser() if root else Path(__file__).parent.parent / "out" / "jobs"
        d = base / "audit"
        d.mkdir(parents=True, exist_ok=True)
        line = json.dumps({k: rec.get(k) for k in _AUDIT_FIELDS}, ensure_ascii=False)
        with _AUDIT_LOCK:
            with open(d / time.strftime("audit-%Y-%m.jsonl", g), "ab") as fh:     # binary: LF on every platform
                fh.write(line.encode("utf-8") + b"\n")
        return True
    except Exception as e:                           # noqa: BLE001 - by design
        if not _AUDIT_WARNED:
            _AUDIT_WARNED = True
            try:
                print(f"[inbound] an activity line could not be written ({e!r}); the message itself is unaffected.",
                      file=sys.stderr)
            except Exception:                        # noqa: BLE001
                pass
        return False


# ---- an admin loads one account's lists on the server ------------------------------------------------------------
def _words_file(path: str) -> list:
    """One keyword or phrase per line; blank lines and lines starting with # are skipped."""
    text = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def _cli() -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m takeoff_engine.inbound",
        description="The e-mail door's account data. Run it where the job store is (TAKEOFF_JOBS_DIR).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rules", help="show or load ONE account's keyword and exclusion lists")
    r.add_argument("account", help="the account's sign-in name")
    r.add_argument("--keywords", metavar="FILE", help="a text file, one keyword or phrase per line; REPLACES the list")
    r.add_argument("--exclude", metavar="FILE", help="the same, for the exclusion words")
    r.add_argument("--clear", action="store_true", help="empty both lists")
    r.add_argument("--show", action="store_true", help="print the words (otherwise only how many)")
    args = ap.parse_args()

    name = _users.find(args.account)
    if name is None:
        print(f"no such account: {args.account}")
        return 1
    cur = rules_of(name)
    if args.clear or args.keywords or args.exclude:
        try:
            kw = [] if args.clear else (_words_file(args.keywords) if args.keywords else cur["keywords"])
            ex = [] if args.clear else (_words_file(args.exclude) if args.exclude else cur["exclude"])
        except OSError as e:
            print(f"could not read the list: {e}")
            return 1
        cur = save_rules(name, kw, ex)
        dropped = (len(kw) - len(cur["keywords"])) + (len(ex) - len(cur["exclude"]))
        print(f"saved for {name}: {len(cur['keywords'])} keyword(s), {len(cur['exclude'])} exclusion word(s)"
              + (f"; {dropped} line(s) were left out (a repeat, shorter than {RULE_WORD_MIN} characters, or past "
                 f"the limit of {RULE_WORDS_MAX})" if dropped else ""))
        audit("inbound_rules", user=name, detail=f"loaded on the server: {len(cur['keywords'])} keyword(s), "
                                                f"{len(cur['exclude'])} exclusion word(s)")
    else:
        print(f"{name}: {len(cur['keywords'])} keyword(s), {len(cur['exclude'])} exclusion word(s)")
    if args.show:
        for label, words in (("keywords", cur["keywords"]), ("exclusion words", cur["exclude"])):
            print(f"{label}:")
            for w in words:
                print(f"  {w}")
    print(f"store: {store_dir() / 'rules.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
