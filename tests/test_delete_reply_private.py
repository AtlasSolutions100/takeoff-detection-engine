"""THE DELETE REPLY SAYS NOTHING ABOUT OTHER DOCUMENTS (2026-10-02, release item 2).

THE HOLE. DELETE /api/job/{id} answered "its PDF stays: another document on this server is the same file" when another
account had uploaded the identical file, and the page showed it. In bidding that tells a contractor a rival holds the
same bid set.

NOW: deleting a project whose PDF another account also uploaded gives the same reply, and so the same words on screen
(webui/app.js pvDeleteSentence), as deleting one nobody else has. The other account's file still stays and its
project still opens.

    python tests/test_delete_reply_private.py
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_JOBS = Path(tempfile.mkdtemp(prefix="del_reply_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(_JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="del_reply_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ.pop("TAKEOFF_UI_PASSWORD", None)

import pymupdf  # noqa: E402

ALICE, BOB = "alice_synth", "bob_synth"


def make_pdf(path: Path, text: str) -> None:
    d = pymupdf.open()
    d.new_page(width=612, height=792).insert_text((72, 72), text)
    d.save(str(path))
    d.close()


SAME = _JOBS / "upload_aaaaaaaaaaaa_bid set.pdf"        # both accounts uploaded these bytes (content-addressed)
ONLY = _JOBS / "upload_bbbbbbbbbbbb_bid set.pdf"        # only alice has this one
make_pdf(SAME, "SAME")
make_pdf(ONLY, "ONLY")
reg = {}
for jid, pdf, owner in (("dra000000001", SAME, ALICE), ("drb000000001", SAME, BOB), ("dra000000002", ONLY, ALICE)):
    (_JOBS / jid).mkdir()
    (_JOBS / jid / "p0_result.json").write_text("{}")
    reg[jid] = {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2), "kinds": None,
                "sizes": [[612, 792]], "owner": owner, "bid": None}
(_JOBS / "registry.json").write_text(json.dumps(reg, indent=1))

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

server._classify_async = lambda *a, **k: None
_users.save({ALICE: _users.hash_password("orchid-lantern-4471"), BOB: _users.hash_password("basalt-meadow-9083")})
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in (ALICE, BOB)}

fails = 0


def check(ok, msg, got=None):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg + ("" if ok or got is None else f"  (got {got!r})"))
    if not ok:
        fails += 1


async def _asgi(method, path, cookie):
    headers = [(b"host", b"testserver"), (b"x-core-client", b"2"), (b"content-length", b"0"),
               (b"cookie", f"{server.SESSION_COOKIE}={cookie}".encode())]
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": headers, "client": ("127.0.0.1", 40000), "server": ("testserver", 80)}
    sent = {"b": False}
    out = {"status": None, "body": b""}

    async def receive():
        if not sent["b"]:
            sent["b"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(m):
        if m["type"] == "http.response.start":
            out["status"] = m["status"]
        elif m["type"] == "http.response.body":
            out["body"] += m.get("body", b"")

    await server.app(scope, receive, send)
    try:
        return out["status"], json.loads(out["body"])
    except ValueError:
        return out["status"], out["body"]


st1, shared = asyncio.run(_asgi("DELETE", "/api/job/dra000000001", CK[ALICE]))
st2, alone = asyncio.run(_asgi("DELETE", "/api/job/dra000000002", CK[ALICE]))
check(st1 == 200 and st2 == 200, "both deletions answer 200", (st1, st2))
check(shared == alone, "the reply is the same whether or not another account uploaded the same file", (shared, alone))
check("same file" not in json.dumps(shared) and "another" not in json.dumps(shared),
      "the reply names no other document", shared)
check(SAME.exists() and not ONLY.exists(), "the other account's file is not removed; alice's only copy is")
st, got = asyncio.run(_asgi("GET", "/api/job/drb000000001/open", CK[BOB]))
check(st == 200, "bob's project still opens", st)
st, got = asyncio.run(_asgi("GET", "/api/job/dra000000001/open", CK[ALICE]))
check(st == 404, "alice's deleted project is gone", st)

# the words on screen: webui/app.js pvDeleteSentence, run on both replies
js = (ROOT / "webui" / "app.js").read_text(encoding="utf-8")
i = js.index("function pvDeleteSentence(")
j = js.index("\n}\n", i) + 2
prog = js[i:j] + "\nconst r = JSON.parse(process.argv[1]);\nconsole.log(JSON.stringify(r.map((x) => pvDeleteSentence(x, 'x'))));"
try:
    out = subprocess.run(["node", "-e", prog, json.dumps([shared, alone])], capture_output=True, text=True, timeout=60)
    words = json.loads(out.stdout)
    check(words[0] == words[1] == "removed bid set.pdf", "the on-screen sentence is the same for both", words)
except (OSError, ValueError) as e:
    check(False, f"node could not render the sentence ({e!r})")

print(f"\n{'PASS' if not fails else 'FAIL'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
