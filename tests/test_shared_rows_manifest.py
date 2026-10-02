"""Owner-less rows left over from an OLDER demo manifest are staff-only.

THE QUESTION (2026-10-01). The demo manifest shrank to the two shared plan
sets (Loves Lane civil sheets, Belle Farms). The live store already holds
SHARED rows (owner None) for the cards that were removed - they were seeded
under the old manifest. What does every account see after the manifest
shrinks?

BEFORE THIS FIX: the Demo tab dropped them (it renders from the manifest
alone), but owner None was still the "shared with everybody" marker on every
other route. So each removed card's sheet stayed on every account's
/api/projects as an ownerless project, on /api/recent, and it opened,
rendered and could be linked into cut / fill through the job routes. Shrinking
the manifest took the card away and left the drawing on every client's screen.

NOW (server._shared_for_all / _can_open): an owner-less row is open to every
account only when its display name is a demo card or starts with one of the
manifest's `shared_names` (the two shared sets' sheets, exact names, case aside). Any other owner-less row is
STAFF-ONLY: listed and openable by staff, invisible (404, unlisted) to
everybody else, and never deleted.

The store is written to disk BEFORE the server is imported, exactly as a
restarted live box loads it, and every request goes through the real session
middleware (same ASGI driver as tests/test_reopen_owner.py).

    python tests/test_shared_rows_manifest.py
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_JOBS = Path(tempfile.mkdtemp(prefix="shared_rows_test_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(_JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="shared_rows_test_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = str(_JOBS)
os.environ.pop("TAKEOFF_UI_PASSWORD", None)

import pymupdf  # noqa: E402

# ---- the store as the old manifest left it, on disk before the server starts ----
REMOVED = [   # the cards the 2026-10-01 manifest no longer carries (client sets)
    "Oncor Cloudcrest Substation Civils - 20251212.pdf_safe.pdf",
    "Church - Peony G-L w Shop Lot 1 Block 10 - Seer Stone 3-18-2026.pdf",
    "Approved Construction Set_2-17-26 (2).pdf",
    "001^A1.20 ADD3 DOOR AND WINDOW TYPES AND INFORMATION.pdf",
    "001^ ADD1 A2.41 SIGNAGE PLAN.pdf",
    "GCHC North Stairs Plan 1 Grading.pdf",
]
KEPT_CARD = "C4.0-SITE-GRADING-PLAN-Rev.1.pdf"       # still a card
SHARED_SHEET = "C3.0-Site-Plan-Rev.1.pdf"            # shared set, not a card
PRELOGIN = "someone-elses-set.pdf"                   # an old owner-less row
ALICE, BOB, STAFF = "alice_synth", "bob_synth", "staff_synth"

_base = pymupdf.open()
_base.new_page(width=612, height=792)
_BASE = _base.tobytes()
_base.close()

reg = {}
ROWS = {}
for i, name in enumerate(REMOVED + [KEPT_CARD, SHARED_SHEET, PRELOGIN, "alice-own.pdf"]):
    jid = "srow%08d" % i
    pdf = _JOBS / ("upload_%012x_%s" % (i + 1, name))      # how /api/upload stores it
    pdf.write_bytes(_BASE + b"\n%" + str(i).encode() + b"\n")
    (_JOBS / jid).mkdir()
    (_JOBS / jid / "p0_result.json").write_text("{}")
    owner = ALICE if name == "alice-own.pdf" else None
    reg[jid] = {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2),
                "kinds": None, "sizes": [[612, 792]], "owner": owner, "bid": None}
    ROWS[name] = jid
(_JOBS / "registry.json").write_text(json.dumps(reg, indent=1))

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

server._classify_async = lambda *a, **k: None

CHECKS = []


def ok(cond, msg, got=None):
    CHECKS.append((bool(cond), msg, got))


async def _asgi(method, path, cookie):
    headers = [(b"host", b"testserver"), (b"x-core-client", b"2"), (b"content-type", b"application/json"),
               (b"content-length", b"0")]
    if cookie is not None:
        headers.append((b"cookie", f"{server.SESSION_COOKIE}={cookie}".encode()))
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": headers,
             "client": ("127.0.0.1", 40000), "server": ("testserver", 80)}
    sent = {"body": False}
    out = {"status": None, "body": b""}

    async def receive():
        if not sent["body"]:
            sent["body"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    await server.app(scope, receive, send)
    return out["status"], out["body"]


def call(method, path, cookie):
    st, data = asyncio.run(_asgi(method, path, cookie))
    try:
        return st, json.loads(data)
    except ValueError:
        return st, data


_users.save({ALICE: _users.hash_password("orchid-lantern-4471"),
             BOB: _users.hash_password("basalt-meadow-9083"),
             STAFF: dict(_users.hash_password("granite-sparrow-2210"), staff=True)})
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in (ALICE, BOB, STAFF)}

ok(set(ROWS.values()) <= set(server.JOBS), "the old store loaded: every seeded row is in JOBS")
ok(not any(n in server.DEMO_PLANS for n in REMOVED), "the shipped manifest no longer carries the removed cards")
ok(KEPT_CARD in server.DEMO_PLANS, "...and still carries the kept card")


def seen(cookie) -> dict:
    st, pr = call("GET", "/api/projects", cookie)
    st2, rc = call("GET", "/api/recent", cookie)
    st3, dp = call("GET", "/api/demo-plans", cookie)
    assert st == 200 and st2 == 200 and st3 == 200, (st, st2, st3)
    cards = dp.get("plans", dp) if isinstance(dp, dict) else dp
    names = {c.get("name") for c in cards} if isinstance(cards, list) else set(cards)
    return {"projects": {p["job_id"]: p for p in pr["projects"]},
            "recent": {r["job_id"] for r in rc}, "cards": names}


for who in (ALICE, BOB):
    s = seen(CK[who])
    for name in REMOVED + [PRELOGIN]:
        jid = ROWS[name]
        ok(jid not in s["projects"] and jid not in s["recent"],
           f"{who}: removed / pre-login shared row {name[:24]!r} is on neither list")
        ok(name not in s["cards"], f"{who}: {name[:24]!r} is not a demo card")
        st_o, _ = call("GET", f"/api/job/{jid}/open", CK[who])
        st_i, _ = call("GET", f"/api/job/{jid}/page/0/image", CK[who])
        st_c, _ = call("GET", f"/cutfill/api/projects/job_{jid}", CK[who])
        ok(st_o == 404 and st_i == 404 and st_c == 404,
           f"{who}: {name[:24]!r} does not open, render or reach cut / fill", (st_o, st_i, st_c))
        st_d, _ = call("DELETE", f"/api/job/{jid}", CK[who])
        ok(st_d == 404, f"{who}: ...and cannot be deleted (404, not a 403 that confirms it)", st_d)
    for name in (KEPT_CARD, SHARED_SHEET):
        jid = ROWS[name]
        ok(jid in s["projects"] and jid in s["recent"], f"{who}: shared-set row {name!r} is still listed")
        ok(s["projects"][jid]["shared"] is True, f"{who}: ...as shared")
        st_o, _ = call("GET", f"/api/job/{jid}/open", CK[who])
        ok(st_o == 200, f"{who}: ...and still opens", st_o)
    ok(s["projects"][ROWS[KEPT_CARD]]["demo"] is True, f"{who}: the kept card's row is still a demo")
    ok(KEPT_CARD in s["cards"], f"{who}: the kept card is on the Demo tab")

own = seen(CK[ALICE])
ok(ROWS["alice-own.pdf"] in own["projects"], "alice still sees her own upload")
ok(ROWS["alice-own.pdf"] not in seen(CK[BOB])["projects"], "bob still does not see alice's")

# staff: every row, the hidden ones marked not-shared, and nothing deleted
s = seen(CK[STAFF])
for name in REMOVED + [PRELOGIN]:
    jid = ROWS[name]
    ok(jid in s["projects"] and s["projects"][jid]["shared"] is False,
       f"staff: {name[:24]!r} is listed, marked staff-only (shared False)")
    st_o, _ = call("GET", f"/api/job/{jid}/open", CK[STAFF])
    ok(st_o == 200, f"staff: {name[:24]!r} still opens", st_o)
ok(set(ROWS.values()) <= set(server.JOBS)
   and all((_JOBS / j / "p0_result.json").exists() for j in ROWS.values()),
   "nothing was deleted: every row and its job directory is still on disk")

# the rule in one place (getattr: on a server without the rule these FAIL, not crash)
_sfa = getattr(server, "_shared_for_all", lambda j: None)
ok(_sfa({"owner": None, "path": "/x/upload_0123456789ab_" + KEPT_CARD}),
   "_shared_for_all: a demo card name through the upload prefix")
ok(_sfa({"owner": None, "path": "/x/" + REMOVED[0]}) is False,
   "_shared_for_all: a removed card is not shared")
ok(_sfa({"owner": ALICE, "path": "/x/" + KEPT_CARD}) is False,
   "_shared_for_all: an owned row named like a card is never shared")

# provision_demo's prefixes (what --rewrite-owners keeps shared) are all in the
# server's list, so the two never disagree about which owner-less rows are shared
import provision_demo  # noqa: E402

_srv_names = getattr(server, "SHARED_NAMES", frozenset())
ok(provision_demo._shared_names() == set(_srv_names)
   and {n.casefold() for n in provision_demo.SHARED_NAMES} == set(_srv_names),
   "provision_demo and the server share exactly the same shared-set names (its fallback list too)",
   (sorted(provision_demo._shared_names() ^ set(_srv_names))))

# NEAR MISSES (critic bid-content-1, RISK-3): exact names only, case aside - a prefix match let these open for all
for near in ("C4.0-SITE-GRADING-PLAN for Smith Residence.pdf", "Belle Farms Road GRADING 1 of 2 (client copy).pdf",
             "C4.0-SITE-GRADING-PLAN-Rev.2.pdf", "C3.0-Site-Plan-Rev.1.pdf.pdf", "Belle Farms Structural Plans (1).pdf",
             "C4.0-SITE-GRADING-PLAN-Rev.1.pdf ", "x C4.0-SITE-GRADING-PLAN-Rev.1.pdf"):
    ok(_sfa({"owner": None, "path": "/x/upload_0123456789ab_" + near}) is False,
       f"near miss is staff-only: {near!r}")
for exact in ("c4.0-site-grading-plan-rev.1.pdf", "BELLE FARMS UPPER PARCEL ROAD GRADING 2 OF 2.PDF",
              "C10.4-TYPICAL-ROAD-SECTIONS-Rev.1.pdf"):
    ok(_sfa({"owner": None, "path": "/x/" + exact}) is True, f"an exact shared name opens for all, case aside: {exact!r}")

fails = [c for c in CHECKS if not c[0]]
for good, msg, got in CHECKS:
    print(("ok:   " if good else "FAIL: ") + msg
          + ("" if good or got is None else f"   got={got!r}"))
print(f"\n{len(CHECKS) - len(fails)}/{len(CHECKS)} checks passed")
sys.exit(1 if fails else 0)
