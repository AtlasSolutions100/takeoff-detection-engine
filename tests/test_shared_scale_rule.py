"""Who may set the review scale of a SHARED sheet, and who then reads it - pinned as it stands (2026-10-01).

THE HOST'S RULE, found by request (critic bid-content-1, WRONG-3; same in live v1.8.5):
  * any account that may open a job may approve it (_forbidden_job is the only gate), and an approve records the
    reviewer's scale on the JOB (p<N>_scale.json in the job directory) - per job, not per account;
  * so on a shared (owner-less) sheet, one client's approve at 1" = 30 is what every other account's review reads;
  * opening the sheet's DEMO card sweeps the job directory, and the pick with it (a shared sheet that is not a card
    is never swept, except by a reset).
This is the owner's call to change, not this test's: it pins the rule so a change to it is deliberate.

CUT / FILL OBEYS THE SAME RULE (server._cf_job_writable): through a link, a client writes a shared sheet's review
scale exactly when the host would let that client approve it, and never a set it may not open.

    python tests/test_shared_scale_rule.py
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
J = Path(tempfile.mkdtemp(prefix="shared_scale_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(J)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="shared_scale_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = str(J)
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("CUTFILL_OUT", None)

import pymupdf  # noqa: E402

CARD = "C4.0-SITE-GRADING-PLAN-Rev.1.pdf"
RES = {"page": {"width_pt": 612, "height_pt": 792}, "chains": [], "styles": [],
       "scale": {"ft_per_paper_inch": 20.0, "text": "1\" = 20'", "source": "caption"}}
reg = {}
IDS = {}
for i, (k, name, owner) in enumerate((("S", CARD, None), ("A", "alice-own.pdf", "alice"))):
    d = pymupdf.open()
    d.new_page(width=612, height=792)
    pdf = J / ("upload_%012x_%s" % (i + 1, name))
    d.save(str(pdf))
    d.close()
    jid = k.lower() * 12
    (J / jid).mkdir()
    (J / jid / "p0_result.json").write_text(json.dumps(RES))
    reg[jid] = {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2), "kinds": None,
                "sizes": [[612, 792]], "owner": owner, "bid": None}
    IDS[k] = jid
(J / "registry.json").write_text(json.dumps(reg))

import server  # noqa: E402
from takeoff_engine import users as U  # noqa: E402

server._classify_async = lambda *a, **k: None
U.save({"alice": U.hash_password("orchid-lantern-4471"), "bob": U.hash_password("basalt-meadow-9083"),
        "staff": dict(U.hash_password("granite-sparrow-2210"), staff=True)})
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in ("alice", "bob", "staff")}
S, A = IDS["S"], IDS["A"]
CHECKS = []


def ok(cond, msg, got=None):
    CHECKS.append((bool(cond), msg, got))


async def _asgi(method, path, cookie, body=b""):
    headers = [(b"host", b"testserver"), (b"x-core-client", b"2"), (b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode()),
               (b"cookie", f"{server.SESSION_COOKIE}={cookie}".encode())]
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": headers, "client": ("127.0.0.1", 1), "server": ("testserver", 80)}
    sent = {"b": False}
    out = {"s": None, "b": b""}

    async def receive():
        if not sent["b"]:
            sent["b"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(m):
        if m["type"] == "http.response.start":
            out["s"] = m["status"]
        elif m["type"] == "http.response.body":
            out["b"] += m.get("body", b"")
    await server.app(scope, receive, send)
    return out["s"], out["b"]


def call(method, path, who, body=None):
    st, b = asyncio.run(_asgi(method, path, CK[who], json.dumps(body).encode() if body is not None else b""))
    try:
        return st, json.loads(b)
    except ValueError:
        return st, b


def in_force(who, jid):
    st, d = call("GET", f"/api/job/{jid}/page/0/scale", who)
    return st, (d or {}).get("in_force") if isinstance(d, dict) else None


# ---- the host ----------------------------------------------------------------------------------
ok(in_force("bob", S) == (200, 20.0), "before: bob's review of the shared card reads the engine's 1\" = 20")
st, d = call("POST", f"/api/job/{S}/page/0/approve", "alice",
             {"labels": {}, "scale": {"ft_per_paper_inch": 30.0, "text": "1\" = 30'", "picked_by_reviewer": True},
              "scale_seen": call("GET", f"/api/job/{S}/page/0/scale", "alice")[1]["stamp"]})
ok(st == 200, "HOST RULE: a client (alice) may approve a shared job, with a scale", (st, str(d)[:160]))
ok((J / S / "p0_scale.json").exists(), "...and the pick is recorded ON THE JOB (p0_scale.json), not per account")
ok(in_force("bob", S) == (200, 30.0), "...so bob's review now reads alice's 1\" = 30 (one pick for every account)")
st, d = call("POST", f"/api/job/{A}/page/0/approve", "bob", {"labels": {}})
ok(st == 404, "a job bob may not open, he may not approve (404)", st)
st, d = call("POST", "/api/demo/" + CARD.replace(" ", "%20") + "/open", "bob")
ok(st == 200 and not (J / S / "p0_scale.json").exists(), "opening the demo card sweeps the pick", st)
(J / S / "p0_result.json").write_text(json.dumps(RES))            # the card open swept the detection too: detect again
ok(in_force("bob", S) == (200, 20.0), "...and once detected again, the engine's reading is back for everyone")

# ---- cut / fill: the same rule ----------------------------------------------------------------
st, d = call("POST", f"/api/job/{A}/cutfill/link", "alice", {"job": S})
ok(st == 200, "alice links the shared card into her own cut / fill", (st, str(d)[:160]))
p = server._cutfill.get_project(f"job_{A}")
key = f"d{d['doc']}p0"
for who, staff in (("alice", False), ("staff", True)):
    tok = server._CF_CALLER.set((who, staff))
    try:
        ok(server._cf_job_writable(A, S) == server._can_open(server.JOBS[S], who, staff),
           f"{who}: cut / fill may write the shared set's review exactly when the host lets {who} approve it")
    finally:
        server._CF_CALLER.reset(tok)
tok = server._CF_CALLER.set(("alice", False))
try:
    ok(server._cf_review_scale_set(p, key, 40.0, "1\" = 40'") is True, "alice sets it through the Scale card")
finally:
    server._CF_CALLER.reset(tok)
ok(in_force("bob", S) == (200, 40.0), "...and bob reads it - the host's rule, no wider and no narrower")
tok = server._CF_CALLER.set(("bob", False))
try:
    ok(server._cf_job_writable(A, A) is True and server._cf_job_readable(A, IDS["A"]) is True,
       "the project's own job is always its own")
finally:
    server._CF_CALLER.reset(tok)

fails = [c for c in CHECKS if not c[0]]
for good, msg, got in CHECKS:
    print(("ok:   " if good else "FAIL: ") + msg + ("" if good or got is None else f"   got={got!r}"))
print(f"\n{len(CHECKS) - len(fails)}/{len(CHECKS)} checks passed")
sys.exit(1 if fails else 0)
