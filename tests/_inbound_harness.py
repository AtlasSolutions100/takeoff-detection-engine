"""Shared by tests/test_inbound_*.py: hand-made PDFs, a message in the mail service's documented JSON shape, an
ASGI driver, and stand-in detection children so the real scheduler runs with no real detection.

Not a test (the suite runs tests/test_*.py). Every name, address and word in here is made up: the base address is
the placeholder 0123456789abcdef0123456789abcdef@inbound.postmarkapp.com, the senders are *.example, and the
keywords are words nobody uses (quartzline, vellumgrade, "blue marrow pipe").
"""
import asyncio
import base64
import io
import json
import time
import uuid
import zipfile
from pathlib import Path

import pymupdf

BASE_LOCAL = "0123456789abcdef0123456789abcdef"
BASE_DOMAIN = "inbound.postmarkapp.com"
BASE_ADDRESS = f"{BASE_LOCAL}@{BASE_DOMAIN}"
HOOK_USER = "hook-user-for-tests"
HOOK_PASS = "hook-pass-for-tests"           # a placeholder, not a secret: it guards a throwaway store

CORPUS = [Path(__file__).resolve().parent.parent.parent / "TakeoffPro AI" / "Plans",
          Path(r"C:/Users/kobys/OneDrive/Desktop/past/P/Takeoff Automation/TakeoffPro AI/Plans")]


def shared_plan(name: str):
    """A PDF of the two shared plan sets (Loves Lane civil sheets, Belle Farms) when the corpus is on this
    machine, else None."""
    for root in CORPUS:
        if (root / name).exists():
            return root / name
    return None


# ---- hand-made PDFs -----------------------------------------------------------------------------------------------
LOREM = ("This agreement sets out the general conditions of the work, the schedule of values, the insurance the "
         "contractor shall carry, the notices each party shall give, and the manner in which changes are priced. ")


def drawing_pdf(pages: int = 1, words=(), title: str = "GRADING PLAN", page_words=None) -> bytes:
    """A large-format (36 x 24 in) drawing set: linework, a title the sheet triage reads as site work, and the
    given words. page_words = {page index: [words on that page only]}."""
    d = pymupdf.open()
    for i in range(pages):
        pg = d.new_page(width=2592, height=1728)
        pg.draw_rect(pymupdf.Rect(60, 60, 2532, 1668), width=2)
        for k in range(6):
            pg.draw_line((120 + 300 * k, 200), (400 + 300 * k, 1500), width=1)
        pg.insert_text((2000, 1600), f"C{i + 1}.0", fontsize=28)
        pg.insert_text((200, 140), f"SITE {title} - STORM DRAIN AND GRADING, SHEET {i + 1}", fontsize=24)
        for k, w in enumerate(list(words) + list((page_words or {}).get(i, []))):
            pg.insert_text((200, 260 + 60 * k), w, fontsize=18)
    raw = d.tobytes()
    d.close()
    return raw


def scan_pdf(pages: int = 2) -> bytes:
    """Large-format pages with linework and NO text layer at all - what a scanned set looks like to a search."""
    d = pymupdf.open()
    for i in range(pages):
        pg = d.new_page(width=2592, height=1728)
        pg.draw_rect(pymupdf.Rect(60, 60, 2532, 1668), width=2)
        pg.draw_line((100, 100 + 50 * i), (2400, 1600), width=1)
    raw = d.tobytes()
    d.close()
    return raw


def text_pdf(pages: int = 3, extra: str = "") -> bytes:
    """A letter-size text document: every page well over the 400 characters that make it a text page."""
    d = pymupdf.open()
    for i in range(pages):
        pg = d.new_page(width=612, height=792)
        pg.insert_textbox(pymupdf.Rect(72, 72, 540, 720), (LOREM * 6) + f" Page {i + 1}. " + extra, fontsize=10)
    raw = d.tobytes()
    d.close()
    return raw


def mixed_pdf(text_pages: int = 3, extra: str = "") -> bytes:
    """Mostly text with ONE drawing sheet at the end (1 of 4 pages: under the 60 % that reads as drawings)."""
    d = pymupdf.open()
    for i in range(text_pages):
        pg = d.new_page(width=612, height=792)
        pg.insert_textbox(pymupdf.Rect(72, 72, 540, 720), (LOREM * 6) + f" Page {i + 1}. " + extra, fontsize=10)
    pg = d.new_page(width=2592, height=1728)
    pg.draw_rect(pymupdf.Rect(60, 60, 2532, 1668), width=2)
    pg.insert_text((200, 140), "SITE GRADING PLAN", fontsize=24)
    raw = d.tobytes()
    d.close()
    return raw


def locked_pdf() -> bytes:
    d = pymupdf.open()
    d.new_page(width=2592, height=1728).insert_text((200, 200), "GRADING PLAN", fontsize=24)
    raw = d.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="made-up-user", owner_pw="made-up-owner")
    d.close()
    return raw


def make_zip(members: dict) -> bytes:
    """{name inside the zip: bytes} -> zip bytes (names are written exactly as given, paths and all)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, raw in members.items():
            zf.writestr(name, raw)
    return buf.getvalue()


# ---- a message, in the mail service's documented shape ------------------------------------------------------------
def payload(tag: str, files=(), subject: str = "Invitation to bid: Elm Street storm drain",
            sender=("Pat Sender", "pat@sender.example"), mid: str = None,
            text: str = "BODY-MARKER-TEXT please see the attached plans",
            html: str = "<html><body><p>BODY-MARKER-HTML see attached</p></body></html>",
            to: str = None, mailbox_hash=...) -> dict:
    """files = [(name, bytes)] or [(name, bytes, content type)]. `mailbox_hash` defaults to `tag`."""
    name, email = sender
    addr = to or f"{BASE_LOCAL}+{tag}@{BASE_DOMAIN}"
    mh = tag if mailbox_hash is ... else mailbox_hash
    atts = []
    for f in files:
        ctype = f[2] if len(f) > 2 else ("application/pdf" if f[0].lower().endswith(".pdf") else "application/octet-stream")
        atts.append({"Name": f[0], "Content": base64.b64encode(f[1]).decode("ascii"), "ContentType": ctype,
                     "ContentLength": len(f[1])})
    return {"FromName": name, "MessageStream": "inbound", "From": email,
            "FromFull": {"Email": email, "Name": name, "MailboxHash": ""},
            "To": addr, "ToFull": [{"Email": addr, "Name": "", "MailboxHash": mh}],
            "Cc": "", "CcFull": [], "Bcc": "", "BccFull": [], "OriginalRecipient": addr,
            "Subject": subject, "MessageID": mid or str(uuid.uuid4()), "ReplyTo": "", "MailboxHash": mh,
            "Date": "Sat, 3 Oct 2026 16:33:54 -0500", "TextBody": text, "HtmlBody": html,
            "StrippedTextReply": "BODY-MARKER-REPLY", "Tag": "",
            "Headers": [{"Name": "X-Spam-Status", "Value": "No"}, {"Name": "X-Spam-Score", "Value": "-0.1"},
                        {"Name": "X-Spam-Tests", "Value": "DKIM_SIGNED,DKIM_VALID,DKIM_VALID_AU,SPF_PASS"}],
            "Attachments": atts}


def basic(user: str = HOOK_USER, pw: str = HOOK_PASS) -> str:
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


# ---- the ASGI driver (every request goes through the real middleware) ----------------------------------------------
async def _asgi(app, method, path, body: bytes, headers: list):
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

    await app(scope, receive, send)
    return out["status"], out["body"]


def call(server, method, path, body=None, cookie=None, auth=None, marker=True):
    """(status, parsed JSON or {"raw": ...}). cookie = a session token; auth = an Authorization header value;
    marker = the page's X-Core-Client header (what webui/client.js puts on every write)."""
    raw = body if isinstance(body, bytes) else (b"" if body is None else json.dumps(body).encode())
    headers = [(b"host", b"testserver"), (b"content-type", b"application/json"),
               (b"content-length", str(len(raw)).encode())]
    if marker:
        headers.append((b"x-core-client", b"3"))
    if cookie is not None:
        headers.append((b"cookie", f"{server.SESSION_COOKIE}={cookie}".encode()))
    if auth is not None:
        headers.append((b"authorization", auth.encode()))
    st, b = asyncio.run(_asgi(server.app, method, path, raw, headers))
    try:
        return st, json.loads(b)
    except ValueError:
        return st, {"raw": b[:200]}


# ---- stand-in detection children ------------------------------------------------------------------------------------
class FakeProc:
    """multiprocessing.Process's shape. start() runs nothing; the test ends it with finish() (which writes the
    result file a real child would) or the scheduler ends it with terminate()."""
    made = []
    sig = "test-sig"

    def __init__(self, target=None, args=(), name=None, daemon=None):
        self.args, self.name = args, name
        self.alive, self.exitcode, self.stopped = False, None, False
        self.t_start = None
        FakeProc.made.append(self)

    @property
    def key(self):
        return Path(self.args[2]).name, int(self.args[1])

    def start(self):
        self.alive, self.t_start = True, time.time()

    def is_alive(self):
        return self.alive

    def terminate(self):
        if self.alive:
            self.alive, self.exitcode, self.stopped = False, -15, True

    kill = terminate

    def join(self, timeout=None):
        pass

    def close(self):
        pass

    def finish(self):
        """The child wrote its result and exited."""
        d = Path(self.args[2])
        d.mkdir(parents=True, exist_ok=True)
        (d / f"p{int(self.args[1])}_result.json").write_text(json.dumps(
            {"page": {"width_pt": 2592, "height_pt": 1728}, "chains": [], "styles": [], "engine_sig": FakeProc.sig,
             "scale": {"ft_per_paper_inch": 20.0}}))
        self.alive, self.exitcode = False, 0


class FakeCtx:
    Process = FakeProc


def install_fake_children(server):
    from takeoff_engine.detect_page import engine_sig
    FakeProc.sig = engine_sig()
    FakeProc.made.clear()
    server._MP = FakeCtx()


def running(server) -> dict:
    with server._SCHED:
        return {k: dict(v) for k, v in server._SCHED_RUNNING.items()}


def wait_until(fn, timeout: float = 8.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.03)
    return bool(fn())


def finish_running(server, only_bg: bool = None) -> int:
    """End every stand-in child that is running (only the e-mail line's with only_bg True) and wait until the
    scheduler has reaped them. Returns how many."""
    with server._SCHED:
        procs = [(k, v["proc"]) for k, v in server._SCHED_RUNNING.items()
                 if only_bg is None or bool(v.get("bg")) == only_bg]
    for _k, p in procs:
        p.finish()
    wait_until(lambda: not any(p is v["proc"] for v in running(server).values() for _k, p in procs))
    return len(procs)


def drain(server, timeout: float = 20.0) -> int:
    """Let every queued page 'detect': finish children until nothing runs and nothing waits. Returns how many ran."""
    n, end = 0, time.time() + timeout
    while time.time() < end:
        with server._SCHED:
            idle = not server._SCHED_RUNNING and not server._SCHED_QUEUE and not getattr(server, "_SCHED_BG", ())
        if idle:
            break
        n += finish_running(server)
        time.sleep(0.05)
    # the dimension check runs after a child (the page stays "running" until it has): let it settle
    wait_until(lambda: not any(st == "running" for j in server.JOBS.values() for st in (j.get("status") or {}).values()), 5.0)
    return n
