"""A cut / fill link does not outlive the caller's right to see the linked plan set.

THE HOLE (critic bid-content-1, WRONG-2). A cut / fill project can hold another plan set's sheets (POST
/api/job/<id>/cutfill/link). The link was checked once, when it was made. After the demo list shrank (2026-10-01),
the removed cards' owner-less rows became staff-only - but a client project that had linked one BEFORE kept
rendering its drawing (page image 200), reading its detection and writing its review scale (p0_scale.json written
into the staff-only job).

THE RULE NOW. Every reach of a linked job asks the host's own rule (_can_open) for the CALLER OF THIS REQUEST, at
request time (server._CF_CALLER, set by the session middleware):
  * the linked document's page routes (image, clip, analysis, analyze, walls, limit candidates, page settings)
    answer 403 in plain words;
  * the project-wide routes (compute, result, takeoff, export.csv, crewmap.png, transforms, grade_at, limit/pick,
    and the host's /cutfill/grade) answer 409 in plain words while such a sheet still takes part;
  * the hooks read and write nothing there (detection record, review scale read AND write);
  * opening Cut / fill takes those sheets out (role "none"): document, link, records and readings stay - nothing is
    deleted - and the reply says so.
Staff still reach it. A link to a still-shared set still works, for clients too.

Real session middleware (same ASGI driver as tests/test_reopen_owner.py); throwaway store written before import.

    python tests/test_cutfill_link_visibility.py
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
J = Path(tempfile.mkdtemp(prefix="cf_link_vis_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(J)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="cf_link_vis_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = str(J)
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("CUTFILL_OUT", None)

import pymupdf  # noqa: E402

ALICE, BOB, STAFF = "alice_synth", "bob_synth", "staff_synth"
HIDDEN_NAME = "GCHC North Stairs Plan 1 Grading.pdf"      # a card removed 2026-10-01: owner-less, now staff-only
SHARED_NAME = "C4.0-SITE-GRADING-PLAN-Rev.1.pdf"          # a demo card: owner-less and shared
RES = {"page": {"width_pt": 612, "height_pt": 792}, "chains": [], "styles": []}

reg = {}
IDS = {}
for i, (k, name, owner) in enumerate((("A", "alice-civil.pdf", ALICE), ("H", HIDDEN_NAME, None),
                                      ("S", SHARED_NAME, None))):
    d = pymupdf.open()
    d.new_page(width=612, height=792).insert_text((72, 72), "SHEET " + k)
    pdf = J / ("upload_%012x_%s" % (i + 1, name))
    d.save(str(pdf))
    d.close()
    jid = ("%s" % k.lower()) * 12
    (J / jid).mkdir()
    (J / jid / "p0_result.json").write_text(json.dumps(RES))
    reg[jid] = {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2), "kinds": None,
                "sizes": [[612, 792]], "owner": owner, "bid": None}
    IDS[k] = jid
(J / "registry.json").write_text(json.dumps(reg, indent=1))

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

server._classify_async = lambda *a, **k: None
_users.save({ALICE: _users.hash_password("orchid-lantern-4471"), BOB: _users.hash_password("basalt-meadow-9083"),
             STAFF: dict(_users.hash_password("granite-sparrow-2210"), staff=True)})
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in (ALICE, BOB, STAFF)}
A, H, S = IDS["A"], IDS["H"], IDS["S"]
PID = f"job_{A}"
CHECKS = []


def ok(cond, msg, got=None):
    CHECKS.append((bool(cond), msg, got))


async def _asgi(method, path, cookie, body=b""):
    q = b""
    if "?" in path:
        path, qs = path.split("?", 1)
        q = qs.encode()
    headers = [(b"host", b"testserver"), (b"x-core-client", b"2"), (b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode()),
               (b"cookie", f"{server.SESSION_COOKIE}={cookie}".encode())]
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": q,
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
    return st, b.decode("utf-8", "replace")


# ---- the history: alice linked both sets while the removed card was still a card --------------
server.DEMO_PLANS[HIDDEN_NAME] = {"page": 0, "shows": "the old card", "tool": ""}
for k in ("H", "S"):
    st, b = call("POST", f"/api/job/{A}/cutfill/link", ALICE, {"job": IDS[k]})
    ok(st == 200, f"setup: alice links {k} while it is shared", (st, b[:200]))
del server.DEMO_PLANS[HIDDEN_NAME]
ok(not server._shared_for_all(server.JOBS[H]) and server._shared_for_all(server.JOBS[S]),
   "setup: the removed card's row is now staff-only, the kept card's row shared")
p = server._cutfill.get_project(PID)
with p._lock:
    docs = [Path(d["path"]).name for d in p.data["docs"]]
    links = dict(p.data.get("host_links") or {})
    for k in ("d1p0", "d2p0"):
        p.page_settings(k)["role"] = "existing"
    p.save()
DH = next(i for i, n in enumerate(docs) if n.endswith(HIDDEN_NAME))
DS = next(i for i, n in enumerate(docs) if n.endswith(SHARED_NAME))
KH, KS = f"d{DH}p0", f"d{DS}p0"
ok(links.get(str(DH)) == H and links.get(str(DS)) == S, "setup: both links are recorded", links)
ok(p.page_settings(KH).get("core_result"), "setup: the hidden sheet's detection record is set")

# ---- alice (client): the hidden set's pages are refused in words ------------------------------
PAGE_ROUTES = [("GET", "image"), ("GET", "clip?x=10&y=10"), ("GET", "analysis"), ("POST", "analyze"),
               ("GET", "walls"), ("GET", "limit_candidates")]
for m, r in PAGE_ROUTES:
    st, b = call(m, f"/cutfill/api/projects/{PID}/pages/{KH}/{r}", ALICE)
    ok(st == 403 and "can no longer open" in b, f"alice {m} pages/<hidden>/{r.split('?')[0]}: 403 in words", (st, b[:160]))
st, b = call("PATCH", f"/cutfill/api/projects/{PID}/pages/{KH}", ALICE, {"role": "existing"})
ok(st == 403, "alice PATCH pages/<hidden>: 403", (st, b[:160]))
WIDE = [("POST", f"/cutfill/api/projects/{PID}/compute"), ("GET", f"/cutfill/api/projects/{PID}/result"),
        ("GET", f"/cutfill/api/projects/{PID}/takeoff"), ("GET", f"/cutfill/api/projects/{PID}/export.csv"),
        ("GET", f"/cutfill/api/projects/{PID}/crewmap.png"), ("GET", f"/cutfill/api/projects/{PID}/transforms"),
        ("POST", f"/cutfill/api/projects/{PID}/grade_at"), ("POST", f"/cutfill/api/projects/{PID}/limit/pick"),
        ("POST", f"/api/job/{A}/page/0/cutfill/grade")]
for m, path in WIDE:
    st, b = call(m, path, ALICE, {})
    ok(st == 409 and "can no longer open" in b, f"alice {m} {path.split(A)[-1]}: 409 in words while the sheet takes part",
       (st, b[:160]))

# the hooks, as alice's request runs them: nothing read, nothing written
tok = server._CF_CALLER.set((ALICE, False))
try:
    ok(server._cf_sheet_job_page(p, KH) is None and server._cf_sheet_job_page(p, KH, write=True) is None,
       "alice: the hidden sheet's review is neither read nor written (_cf_sheet_job_page)")
    ok(server._cf_record_reachable(p, KH, p.page_settings(KH)["core_result"]) is False,
       "alice: its detection record is not reachable")
    ok(server._cf_review_scale_live(p, KH) is None, "alice: its review scale is not read")
    ok(server._cf_review_scale_set(p, KH, 30.0, "1\" = 30'") is None and not (J / H / "p0_scale.json").exists(),
       "alice: its review scale is NOT written (no p0_scale.json in the staff-only job)")
    # the still-shared set: read and written, the host's rule (anyone who may open a job may approve it)
    ok(server._cf_sheet_job_page(p, KS) == (S, 0), "alice: the shared set's review is read")
    ok(server._cf_review_scale_set(p, KS, 30.0, "1\" = 30'") is True and (J / S / "p0_scale.json").exists(),
       "alice: the shared set's review scale is written, as the host's approve writes it")
finally:
    server._CF_CALLER.reset(tok)
st, b = call("GET", f"/cutfill/api/projects/{PID}/pages/{KS}/image", ALICE)
ok(st == 200, "alice: the shared set's page image still renders", st)
st, b = call("GET", f"/cutfill/api/projects/{PID}/pages/d0p0/image", ALICE)
ok(st == 200, "alice: her own sheet still renders", st)

# a background read (no request): the project owner's view, the same answer
ok(server._CF_CALLER.get() is None and server._cf_sheet_job_page(p, KH) is None,
   "outside a request the project owner's view applies: still nothing")

# ---- bob: not his project at all (unchanged) ---------------------------------------------------
st, b = call("GET", f"/cutfill/api/projects/{PID}/pages/{KH}/image", BOB)
ok(st == 404, "bob: alice's project is not his (404, unchanged)", st)

# ---- staff: still reaches everything ------------------------------------------------------------
st, b = call("GET", f"/cutfill/api/projects/{PID}/pages/{KH}/image", STAFF)
ok(st == 200, "staff: the hidden set's page still renders", st)
st, b = call("GET", f"/cutfill/api/projects/{PID}/result", STAFF)
ok("can no longer open" not in b, "staff: project-wide routes are not refused for it", (st, b[:120]))
tok = server._CF_CALLER.set((STAFF, True))
try:
    ok(server._cf_sheet_job_page(p, KH) == (H, 0), "staff: the hidden sheet's review is read")
finally:
    server._CF_CALLER.reset(tok)

# ---- opening Cut / fill takes the hidden sheets out; nothing is deleted ------------------------
tok = server._CF_CALLER.set((ALICE, False))
try:
    with p._lock:
        out = server._cf_detach_hidden(p, A)
finally:
    server._CF_CALLER.reset(tok)
ok(out == [KH], "the open's detach takes out exactly the hidden sheet", out)
with p._lock:
    ok(p.page_settings(KH).get("role") == "none" and p.page_settings(KS).get("role") == "existing",
       "...its role is none; the shared sheet keeps its role")
    ok((p.data.get("host_links") or {}).get(str(DH)) == H and len(p.data["docs"]) == len(docs)
       and p.page_settings(KH).get("core_result"),
       "...its document, link and detection record stay (nothing deleted)")
st, b = call("GET", f"/cutfill/api/projects/{PID}/result", ALICE)
ok("can no longer open" not in b, "after the detach the project-wide routes answer again", (st, b[:120]))
st, b = call("GET", f"/cutfill/api/projects/{PID}/pages/{KH}/image", ALICE)
ok(st == 403, "...and the hidden page itself is still refused", st)
src = Path(server.__file__).read_text(encoding="utf-8")
ok("hidden_out = _cf_detach_hidden(p, job_id)" in src and '"hidden_note"' in src,
   "cutfill_open detaches and its reply carries the note")
app = (ROOT / "webui" / "app.js").read_text(encoding="utf-8")
ok("if (j.hidden_note) parts.push(j.hidden_note);" in app, "the host bar prints the note")

fails = [c for c in CHECKS if not c[0]]
for good, msg, got in CHECKS:
    print(("ok:   " if good else "FAIL: ") + msg + ("" if good or got is None else f"   got={got!r}"))
print(f"\n{len(CHECKS) - len(fails)}/{len(CHECKS)} checks passed")
sys.exit(1 if fails else 0)
