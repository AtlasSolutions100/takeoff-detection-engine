"""THE WALL PANEL JOB'S ROUTES (2026-10-03). Every number here is made up for the test.

One document per job and per account; the browser sends acts, the server measures and computes.
Synthetic jobs, four synthetic accounts (two estimators, one staff, one viewer), every request
through the real middleware (sign-in, ownership, client gate) with no server listening:
  * opening a job writes nothing
  * the rates are the account's own: set by it, or loaded for it by staff from a file's contents;
    never another account's; every problem refuses the save and is listed; a stale save is refused
  * a write is versioned; a stale one is refused with its sentence; an act carrying a figure is refused
  * a wall picked or drawn on a sheet is measured by the server at the sheet's scale in force (on a
    sheet that prints several scales, at its own view's: tests/test_wp_critic1.py); with none in force
    nothing shows in feet
  * the screen, the worksheet (CSV) and the proposal (PDF) carry the same total; the proposal shows no
    rate, no days, no profit, no cost line; it is refused with the list while anything is missing
  * a job stands on the rates it took: a later change to the settings does not move it
  * on a shared sheet another account sees none of it; a viewer reads only

    python tests/test_wp_acts.py
"""
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_JOBS = Path(tempfile.mkdtemp(prefix="wpacts_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(_JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="wpacts_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ.pop("TAKEOFF_UI_PASSWORD", None)

import pymupdf  # noqa: E402

ALICE, BOB, STAFF, VIEW = "alice_synth", "bob_synth", "staff_synth", "view_synth"


def box_chains(x0, y0, x1, y1, cid0=1):
    pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
    return [{"chain_id": cid0 + i, "style_id": 1, "points": [pts[i], pts[i + 1]],
             "length_pt": abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1])} for i in range(4)]


RESULT = {"page": {"width_pt": 612, "height_pt": 792}, "styles": [{"style_id": 1, "class": "solid", "width": 1.0, "n": 4, "color": [0, 0, 0]}],
          "chains": box_chains(100, 100, 388, 244), "openings": [], "scale": {"ft_per_paper_inch": 4.0, "text": "1/4\" = 1'-0\""}}

reg, JID = {}, {}
for i, (name, owner, scale) in enumerate([("C3.0-Site-Plan-Rev.1.pdf", None, True), ("alice-own.pdf", ALICE, True),
                                          ("alice-noscale.pdf", ALICE, False)]):
    jid = "wpa%09d" % i
    d = pymupdf.open()
    d.new_page(width=612, height=792)
    pdf = _JOBS / ("upload_%012x_%s" % (i + 1, name))
    d.save(str(pdf))
    d.close()
    (_JOBS / jid).mkdir()
    (_JOBS / jid / "p0_result.json").write_text(json.dumps(RESULT if scale else dict(RESULT, scale=None)))
    reg[jid] = {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2), "kinds": None,
                "sizes": [[612, 792]], "owner": owner, "bid": None}
    JID[name] = jid
(_JOBS / "registry.json").write_text(json.dumps(reg, indent=1))
SH, OWN, NOS = (JID[n] for n in ("C3.0-Site-Plan-Rev.1.pdf", "alice-own.pdf", "alice-noscale.pdf"))

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402
from takeoff_engine.detect_page import _ENGINE_FILES  # noqa: E402

server._classify_async = lambda *a, **k: None
_users.save({ALICE: _users.hash_password("orchid-lantern-4471"), BOB: _users.hash_password("basalt-meadow-9083"),
             STAFF: dict(_users.hash_password("copper-harbor-2265"), staff=True),
             VIEW: dict(_users.hash_password("willow-ember-7730"), role="viewer")})
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in (ALICE, BOB, STAFF, VIEW)}
ACCT = {u: hashlib.sha1(u.encode()).hexdigest()[:16] for u in (ALICE, BOB, STAFF, VIEW)}
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


def call(method, path, cookie, body=None, raw=False):
    st, data = asyncio.run(_asgi(method, path, cookie, b"" if body is None else json.dumps(body).encode()))
    if raw:
        return st, data
    try:
        return st, json.loads(data)
    except ValueError:
        return st, data.decode("utf-8", "replace")


def act(job, who, version, a):
    return call("POST", f"/api/job/{job}/wallpanels/act", CK[who], {"version": version, "act": a})


def acts(job, who, version, lst):
    w = None
    for a in lst:
        st, w = act(job, who, version, a)
        if st != 200 or not w.get("ok"):
            check(False, f"act {a} went through", (st, w if st != 200 else w.get("why")))
            return version, w
        version = w["version"]
    return version, w


def wp_file(job, who):
    return _JOBS / job / "wp_est" / f"{ACCT[who]}.json"


def line(w, lid):
    return next(x for x in w["computed"]["lines"] if x["id"] == lid)


SETTINGS = {"labor_rate": "41.37", "overtime_after_hours": "9", "overtime_rate": "17.25", "travel_per_mile": "0.93",
            "hotel_nightly": "141.19", "profit_flat": "650", "profit_per_day": "733.37", "profit_flat_under_sheets": 6,
            "sizes": ["4x8", "4x10"], "molding_types": ["vinyl"],
            "price_items": [{"id": "pa", "name": "Panel A", "per": "sqft", "price": "1.2345"}],
            "scope_template": "Panels {thickness} thick in {colour}, {finish} finish. {sheets} sheets of {sizes}.",
            "notes": ["One trip to the site.", "Net thirty."]}

print("opening a job writes nothing")
st, g0 = call("GET", f"/api/job/{OWN}/wallpanels", CK[ALICE])
check(st == 200 and g0["version"] == 0 and g0["stored"] is False and g0["rooms"] == [] and g0["computed"]["total"] == "0.00",
      "a job with no wall panels opens empty, version 0, not stored", g0)
for _ in range(3):
    call("GET", f"/api/job/{OWN}/wallpanels", CK[ALICE])
    call("GET", f"/api/job/{OWN}/wallpanels_worksheet.csv", CK[ALICE], raw=True)
check(not (_JOBS / OWN / "wp_est").exists(), "reads later: nothing written")
check(all(not x["amount"] for x in g0["computed"]["lines"] if x["id"] in ("labor", "overtime", "travel", "hotel", "profit")) and g0["settings_scope"] == "none",
      "an account that set nothing has no rate: no cost line has an amount", [(x["id"], x["amount"]) for x in g0["computed"]["lines"]])

print("the rates are the account's own")
st, s0 = call("GET", "/api/wallpanels/settings", CK[ALICE])
check(st == 200 and s0["scope"] == "none" and all(s0["settings"][k] is None for k in ("labor_rate", "overtime_rate", "hotel_nightly", "profit_flat")),
      "nothing set: every rate reads not set", s0.get("settings"))
st, x = call("POST", "/api/wallpanels/settings", CK[ALICE], {"settings": dict(SETTINGS, labour="40", hotel_nightly="1.234"), "rev": 0})
check(st == 422 and len(x.get("problems", [])) == 2 and "Nothing was saved" in x["detail"], "two problems: both listed, nothing saved", x)
st, s1 = call("POST", "/api/wallpanels/settings", CK[ALICE], {"settings": SETTINGS, "rev": 0})
check(st == 200 and s1["settings"]["rev"] == 1 and s1["settings"]["hotel_nightly"] == "141.19" and s1["scope"] == "account", "Alice saves hers", s1)
st, x = call("POST", "/api/wallpanels/settings", CK[ALICE], {"settings": dict(SETTINGS, labor_rate="1"), "rev": 0})
check(st == 409 and x["detail"] == server.WP_SETTINGS_STALE, "a save that read an older record is refused with its sentence", x)
st, sb = call("GET", "/api/wallpanels/settings", CK[BOB])
check(st == 200 and sb["scope"] == "none" and sb["settings"]["labor_rate"] is None and sb["accounts"] is None, "Bob has none of Alice's rates, and no account list", sb)
st, x = call("POST", "/api/wallpanels/settings", CK[BOB], {"settings": dict(SETTINGS, labor_rate="1"), "account": ALICE})
check(st == 404, "Bob naming Alice's account: 404, like any staff page", (st, x))
st, x = call("POST", "/api/wallpanels/settings/of", CK[BOB], {"account": ALICE})
check(st == 404, "and he can not read hers", (st, x))
st, sa = call("GET", "/api/wallpanels/settings", CK[ALICE])
check(sa["settings"]["labor_rate"] == "41.37" and sa["settings"]["rev"] == 1, "Alice's are untouched")
FILE = dict(SETTINGS, labor_rate="38.00", notes=["Bob's own note."], about="loaded by staff in a test")
st, x = call("POST", "/api/wallpanels/settings", CK[STAFF], {"settings": FILE, "account": BOB, "how": "file", "rev": 0})
check(st == 200 and x["account"] == BOB and x["settings"]["labor_rate"] == "38.00" and x["settings"]["saved"]["how"] == "loaded from a file"
      and BOB in (x["accounts"] or []), "staff load Bob's settings from a file's contents", x)
st, sb = call("GET", "/api/wallpanels/settings", CK[BOB])
check(sb["settings"]["labor_rate"] == "38.00" and sb["settings"]["notes"] == ["Bob's own note."], "Bob sees them as his own")
check(sb["settings"]["saved"]["by"] == "staff" and STAFF not in json.dumps(sb), "who loaded them reads 'staff' to Bob: no other account's name", sb["settings"]["saved"])
st, x = call("POST", "/api/wallpanels/settings/of", CK[STAFF], {"account": ALICE})
check(st == 200 and x["settings"]["labor_rate"] == "41.37", "staff can read any account's")
st, x = call("POST", "/api/wallpanels/settings", CK[STAFF], {"settings": FILE, "account": "nobody_synth"})
check(st == 404, "an account that does not exist: 404", (st, x))

print("writes are acts, versioned")
st, w = act(OWN, ALICE, 0, {"kind": "room_add", "name": "Kitchen"})
check(st == 200 and w["ok"] and w["version"] == 1 and w["stored"] and len(w["rooms"]) == 1 and w["rates_at"] and w["rates"]["labor_rate"] == "41.37",
      "the first act: version 1, stored, and the job takes the account's rates as they are", w)
rid = w["rooms"][0]["id"]
st, x = act(OWN, ALICE, 0, {"kind": "room_add", "name": "Stale"})
check(st == 409 and x["detail"] == server.WP_STALE, "a write over version 0 when the job is at 1 is refused with its sentence", x)
st, x = act(OWN, ALICE, 1, {"kind": "room_set", "id": rid, "field": "typed", "value": "8 + 8", "wall_lf": 500})
check(st == 400 and "wall_lf is a figure" in x["detail"], "an act carrying a figure is refused, the figure named", x)
st, x = act(OWN, ALICE, 1, {"kind": "wall_put", "room": rid, "wall": {"page": 0, "src": "line", "chain": 1, "len_pt": 99999, "a": [0, 0], "b": [1, 1]}})
check(st == 400 and "server's own act" in x["detail"], "a measured wall can not be sent by a browser", x)
st, x = act(OWN, ALICE, 1, {"kind": "total_set", "value": 1})
check(st == 400 and "unknown act" in x["detail"], "an unknown act is refused", x)
st, x = act(OWN, ALICE, 1, {"kind": "room_set", "id": rid, "field": "typed", "value": "8 + 8 - 6"})
check(st == 422 and "not a length" in x["detail"], "a slip in the wall lengths is refused in words", x)
st, g = call("GET", f"/api/job/{OWN}/wallpanels", CK[ALICE])
check(g["version"] == 1 and len(g["rooms"]) == 1 and g["rooms"][0]["typed"] == "", "nothing of the refused acts was written", g["rooms"])

print("walls: typed, picked and drawn - the server measures")
v, w = acts(OWN, ALICE, 1, [{"kind": "room_set", "id": rid, "field": "typed", "value": "8 + 8 + 12.5"},
                            {"kind": "room_set", "id": rid, "field": "height", "value": "8'"},
                            {"kind": "wall_pick", "room": rid, "page": 0, "chain": 1},
                            {"kind": "wall_draw", "room": rid, "page": 0, "seg": [100, 100, 100, 244]}])
f = w["computed"]["rooms"][rid]
check([x["lf"] for x in f["walls"]] == ["16", "8"] and f["wall_lf"] == "52.5",
      "line 1 (288 pt) and a drawn 144 pt at 1\" = 4 ft: 16 and 8 LF; with 28.5 typed the room is 52.5 LF", f)
check(w["rooms"][0]["walls"][0]["len_pt"] == 288.0 and "lf" not in json.dumps(w["rooms"]), "the document keeps the line and its fingerprint, no feet")
sg = f["suggestion"]
check(sg["options"] and sg["options"][0]["count"] == 14 and sg["options"][0]["size"] == "4x8" and "52.5 LF ÷ 4 ft wide = 13.13, rounded up: 14" in sg["options"][0]["arith"]
      and sg["words"] == "geometry only: no waste, no openings", "the suggestion with its arithmetic and its words", sg)
check(w["sheets"] == [], "nothing was added to the sheets by itself")
st, x = act(OWN, ALICE, v, {"kind": "wall_pick", "room": rid, "page": 0, "chain": 99})
check(st == 200 and x["ok"] is False and "not in this sheet's detection" in x["why"] and x["version"] == v, "a line the sheet does not have: refused in words, nothing written", x.get("why"))
st, x = act(OWN, ALICE, v, {"kind": "wall_draw", "room": rid, "page": 0, "seg": [100, 100, 5000, 100]})
check(st == 200 and x["ok"] is False and "off the sheet" in x["why"], "a wall drawn off the sheet: refused", x.get("why"))
st, x = act(OWN, ALICE, v, {"kind": "wall_draw", "room": rid, "page": 3, "seg": [1, 1, 9, 9]})
check(st == 422, "a sheet the job does not have: refused", (st, x))
st, x = act(OWN, ALICE, v, {"kind": "wall_pick", "room": rid, "page": 0, "chain": 1})
check(st == 422 and "already" in x["detail"], "the same line twice: refused", x)
# the sheet is detected again and line 1 is another line now
moved = dict(RESULT, chains=[dict(c, length_pt=300.0, points=[[100, 100], [400, 100]]) if c["chain_id"] == 1 else c for c in RESULT["chains"]])
rf = _JOBS / OWN / "p0_result.json"
rf.write_text(json.dumps(moved))
os.utime(rf, (time.time() + 5, time.time() + 5))
st, g = call("GET", f"/api/job/{OWN}/wallpanels", CK[ALICE])
f = g["computed"]["rooms"][rid]
check(f["walls"][0]["lf"] is None and "detected again" in f["walls"][0]["words"] and f["wall_lf"] is None and not f["suggestion"]["options"],
      "the sheet is detected again and the picked line is not that line any more: not counted, said", f["walls"][0])
rf.write_text(json.dumps(RESULT))
os.utime(rf, (time.time() + 10, time.time() + 10))
st, g = call("GET", f"/api/job/{OWN}/wallpanels", CK[ALICE])
check(g["computed"]["rooms"][rid]["wall_lf"] == "52.5", "the same detection back: the same 52.5 LF")

print("no scale in force: nothing in feet")
st, n0 = act(NOS, ALICE, 0, {"kind": "room_add", "name": "Hall"})
nr = n0["rooms"][0]["id"]
vn, n = acts(NOS, ALICE, 1, [{"kind": "wall_pick", "room": nr, "page": 0, "chain": 1}, {"kind": "wall_draw", "room": nr, "page": 0, "seg": [100, 100, 100, 244]},
                             {"kind": "room_set", "id": nr, "field": "height", "value": "8'"}])
fn = n["computed"]["rooms"][nr]
check(all(x["lf"] is None and x["pt"] for x in fn["walls"]) and fn["wall_lf"] is None and not fn["suggestion"]["options"]
      and all("no scale is in force" in x["words"] for x in fn["walls"]) and " LF" not in json.dumps(fn),
      "the walls show in pt; no length in feet anywhere in the room; no suggestion", fn)

print("the costs, the worksheet and the proposal")
v, w = acts(OWN, ALICE, v, [{"kind": "room_set", "id": rid, "field": "thickness", "value": '.090"'},
                            {"kind": "room_set", "id": rid, "field": "colour", "value": "White"},
                            {"kind": "sheet_add", "size": "4x8"}])
sid = w["sheets"][0]["id"]
v, w = acts(OWN, ALICE, v, [{"kind": "sheet_set", "id": sid, "field": "count", "value": "14"},
                            {"kind": "sheet_price_item", "id": sid, "item": "pa"},
                            {"kind": "job_set", "field": "days", "value": "3.25"}, {"kind": "job_set", "field": "hours", "value": "9.5"},
                            {"kind": "job_set", "field": "miles", "value": "117.5"}, {"kind": "job_set", "field": "nights", "value": "3"},
                            {"kind": "job_set", "field": "project", "value": "Example Diner"}, {"kind": "line_add"}])
lid = w["lines"][0]["id"]
v, w = acts(OWN, ALICE, v, [{"kind": "line_set", "id": lid, "field": "what", "value": "Adhesive"}, {"kind": "line_set", "id": lid, "field": "qty", "value": "7.5"},
                            {"kind": "line_set", "id": lid, "field": "unit", "value": "tubes"}, {"kind": "line_set", "id": lid, "field": "price", "value": "6.49"}])
c = w["computed"]
check(w["sheets"][0]["price_cents"] == 3950 and c["lines"][0]["amount"] == "553.00", "a price item a square foot: 1.2345 x 32 SF = 39.50 a sheet; 14 sheets = 553.00", c["lines"][0])
check((line(w, "labor")["amount"], line(w, "overtime")["amount"], line(w, "travel")["amount"], line(w, "hotel")["amount"], line(w, "profit")["amount"])
      == ("1,277.30", "28.03", "109.28", "423.57", "2,383.45"), "labor, overtime, travel, hotel and profit as the account's rates give them",
      [(x["id"], x["qty"], x["rate"], x["amount"]) for x in c["lines"]])
st, pr = call("GET", f"/api/job/{OWN}/wallpanels_proposal.pdf", CK[ALICE])
check(st == 409 and pr["detail"].startswith("The proposal is held") and any("Freight: not typed" in m for m in pr["missing"])
      and any("finish is blank" in m for m in pr["missing"]) and any("company name is not set" in m for m in pr["missing"]) and len(pr["missing"]) == 3,
      "the proposal is refused with the list: freight not typed, a blank of the sentence, no company name", pr)
check(pr["missing"] == c["hold"], "the same list the screen shows")
call("POST", "/api/branding", CK[ALICE], {"company": "Example Panels Co", "contact": "555-0100 example.test"})
v, w = acts(OWN, ALICE, v, [{"kind": "job_set", "field": "freight", "value": "185.45"}, {"kind": "blank_set", "name": "finish", "value": "pebbled"}])
c = w["computed"]
total = c["total"]
check(c["issue"] and total == "5,008.76" and c["total_cents"] == sum(x["amount_cents"] for x in c["lines"]),
      "nothing missing: the total is the sum of the printed lines, 5,008.76", (total, c["hold"]))
st, pdfb = call("GET", f"/api/job/{OWN}/wallpanels_proposal.pdf", CK[ALICE], raw=True)
txt = "".join(p.get_text() for p in pymupdf.open(stream=pdfb, filetype="pdf")) if st == 200 else ""
flat = " ".join(txt.split())
check(st == 200 and "Example Panels Co" in flat and "Example Diner" in flat and 'Panels .090" thick in White, pebbled finish. 14 sheets of 4x8.' in flat
      and "$5,008.76" in flat and "Five Thousand Eight Dollars and Seventy-Six Cents" in flat and "One trip to the site." in flat and "Net thirty." in flat,
      "the proposal: the company block, the project, the scope sentence, the price in figures and in words, the notes", flat[:600])
leaks = [s for s in ("41.37", "17.25", "0.93", "141.19", "733.37", "650", "185.45", "3.25", "9.5", "117.5", "30.875", "1,277.30", "2,383.45", "553.00", "39.50", "6.49",
                     "Labor", "Overtime", "Travel", "Hotel", "Freight", "Profit", "Adhesive", "days", "hours", "Kitchen") if s in flat]
check(not leaks, "it shows no rate, no days, no hours, no profit, no cost line, no price a sheet, no room", leaks)
st, ws = call("GET", f"/api/job/{OWN}/wallpanels_worksheet.csv", CK[ALICE], raw=True)
wst = ws.decode("utf-8")
check(st == 200 and f'Total,,,,"{total}"' in wst and "41.37" in wst and "Profit,3.25,days,733.37" in wst and "issued" in wst and "Kitchen" in wst,
      "the worksheet (CSV) carries the breakdown and the same total as the screen and the PDF", wst[:300])
wl = wst.rstrip("\n").split("\n")
check(wl[-1] == server.BETA_LONG and wl[-2] == "" and "Automated detection" not in flat,
      "the worksheet ends with a blank row and the accuracy line (it carries lengths off detected lines); the customer's proposal carries nothing of ours", wl[-2:])

print("the job stands on the rates it took")
st, s2 = call("POST", "/api/wallpanels/settings", CK[ALICE], {"settings": dict(SETTINGS, hotel_nightly="150", scope_template="Changed {sheets}."), "rev": 1})
check(st == 200 and s2["settings"]["rev"] == 2, "Alice changes her hotel rate and her sentence", s2)
st, g = call("GET", f"/api/job/{OWN}/wallpanels", CK[ALICE])
check(g["computed"]["total"] == total and g["version"] == v and any("hotel" in d_ and "150.00" in d_ for d_ in g["rates_differ"]) and len(g["rates_differ"]) == 2
      and "pebbled finish" in g["computed"]["scope"]["text"], "the job's total and sentence do not move; it says what differs", (g["computed"]["total"], g["rates_differ"]))
st, w = act(OWN, ALICE, v, {"kind": "rates_take"})
v = w["version"]
check(st == 200 and line(w, "hotel")["amount"] == "450.00" and w["rates_differ"] == [] and w["computed"]["scope"]["text"] == "Changed 14.",
      "on request the job takes the rates as they are now", (line(w, "hotel"), w["rates_differ"]))
st, n2 = act(SH, ALICE, 0, {"kind": "room_add", "name": "New job"})
check(n2["rates"]["hotel_nightly"] == "150.00", "a new job starts on the rates as they are now")

print("privacy: per account; on a shared sheet another account sees none of it")
va = n2["version"]
ra = n2["rooms"][0]["id"]
va, wa = acts(SH, ALICE, va, [{"kind": "room_set", "id": ra, "field": "name", "value": "AliceSecretRoom"},
                              {"kind": "room_set", "id": ra, "field": "mfr", "value": "AcmeSecretMfr"},
                              {"kind": "room_set", "id": ra, "field": "typed", "value": "77.77"},
                              {"kind": "wall_pick", "room": ra, "page": 0, "chain": 2},
                              {"kind": "job_set", "field": "project", "value": "SecretProject"}, {"kind": "job_set", "field": "freight", "value": "9999.99"},
                              {"kind": "sheet_add", "size": "4x10"}, {"kind": "line_add"}])
va, wa = acts(SH, ALICE, va, [{"kind": "line_set", "id": wa["lines"][0]["id"], "field": "what", "value": "SecretLine"},
                              {"kind": "sheet_set", "id": wa["sheets"][0]["id"], "field": "price", "value": "88.88"}])
SECRETS = ("AliceSecretRoom", "AcmeSecretMfr", "77.77", "SecretProject", "9999.99", "9,999.99", "SecretLine", "88.88", "41.37", "150.00")
st, b0 = call("GET", f"/api/job/{SH}/wallpanels", CK[BOB])
bt = json.dumps(b0)
check(st == 200 and b0["version"] == 0 and b0["rooms"] == [] and b0["sheets"] == [] and b0["lines"] == [] and b0["rates"]["labor_rate"] == "38.00"
      and not [s for s in SECRETS if s in bt], "Bob opens the same shared sheet: his own empty job, his own rates, none of Alice's rooms, words or figures",
      [s for s in SECRETS if s in bt])
for path in (f"/api/job/{SH}/wallpanels_worksheet.csv", f"/api/job/{SH}/wallpanels_proposal.pdf", f"/api/job/{SH}/page/0/calcstate", f"/api/job/{SH}/flooring"):
    st, body = call("GET", path, CK[BOB], raw=True)
    check(not [s for s in SECRETS if s.encode() in body], f"Bob {path.rsplit('/', 1)[-1]}: none of Alice's strings", body[:200])
st, x = act(SH, BOB, 0, {"kind": "room_set", "id": ra, "field": "name", "value": "mine"})
check(st == 422 and "not on this job" in x["detail"], "Bob can not act on Alice's room: it is not in his document", x)
st, wb = act(SH, BOB, 0, {"kind": "room_add", "name": "BobRoom"})
st, a1 = call("GET", f"/api/job/{SH}/wallpanels", CK[ALICE])
check(a1["version"] == va and [r["name"] for r in a1["rooms"]] == ["AliceSecretRoom"] and "BobRoom" not in json.dumps(a1), "Bob's own act leaves Alice's job as it was")
check(sorted(p.name for p in (_JOBS / SH / "wp_est").iterdir()) == sorted(f"{ACCT[u]}.json" for u in (ALICE, BOB))
      and not (_JOBS / SH / "p0_calcstate.json").exists() and not (_JOBS / SH / "p0_elements.json").exists(),
      "on disk: one document per account, and nothing of it in the sheet's own records")
for m, path in (("GET", f"/api/job/{OWN}/wallpanels"), ("GET", f"/api/job/{OWN}/wallpanels_worksheet.csv"), ("GET", f"/api/job/{OWN}/wallpanels_proposal.pdf")):
    st, x = call(m, path, CK[BOB])
    check(st == 404, f"Alice's own job is not Bob's to open: {path.rsplit('/', 1)[-1]} 404", (st, x))
st, x = act(OWN, BOB, v, {"kind": "room_add", "name": "x"})
check(st == 404, "nor to write", (st, x))
st, x = call("GET", f"/api/job/{OWN}/wallpanels", CK[STAFF])
check(st == 200 and x["rooms"] == [] and "Kitchen" not in json.dumps(x), "staff may open the job, and see their own (empty) wall panels on it - not Alice's")

print("a viewer reads only")
st, gv = call("GET", f"/api/job/{SH}/wallpanels", CK[VIEW])
check(st == 200 and gv["view_only"] is True, "a viewer opens the job", gv.get("view_only"))
st, x = act(SH, VIEW, 0, {"kind": "room_add", "name": "x"})
# (the redesign: its viewer gate refuses every write of a Viewer BEFORE the route runs - server._SessionAuth, by method -
# so the sentence a viewer reads is the frame's "Read-only: your role is Viewer ...", not the route's own WP_VIEW_ONLY,
# which still stands behind it. Either way: 403, and nothing written.)
check(st == 403 and x["detail"] in (server.WP_VIEW_ONLY, getattr(server, "VIEWER_READ_ONLY", None)) and not wp_file(SH, VIEW).exists(),
      "a viewer's act is refused with its sentence; nothing written", x)
st, x = call("POST", "/api/wallpanels/settings", CK[VIEW], {"settings": SETTINGS})
check(st == 403, "and so is a viewer's settings save", (st, x))
st, x = call("GET", f"/api/job/{SH}/wallpanels", None)
check(st == 401, "signed out: 401", st)

print("a deleted job leaves no estimate of any account behind")
call("POST", f"/api/job/{NOS}/flooring/act", CK[ALICE], {"version": 0, "act": {"kind": "type_add", "name": "Sheet vinyl"}})
check(wp_file(NOS, ALICE).exists() and (_JOBS / NOS / "fl_est" / f"{ACCT[ALICE]}.json").exists(),
      "before: the job holds Alice's wall panel job and her flooring estimate, a folder each")
st, x = call("DELETE", f"/api/job/{NOS}", CK[ALICE])
check(st == 200 and not (_JOBS / NOS / "wp_est").exists() and not (_JOBS / NOS / "fl_est").exists() and not (_JOBS / NOS).exists(),
      "deleted: both folders and the job's directory are gone (they were left on disk before)", (st, x, sorted(p.name for p in (_JOBS / NOS).glob("*")) if (_JOBS / NOS).exists() else None))

print("engine files stay untouched")
check(not [n for n in _ENGINE_FILES if n.startswith("wp_")], "no wall panel module is an engine file (the engine signature does not move)", _ENGINE_FILES)

print(f"\n{fails} FAILED" if fails else "\nall passed")
sys.exit(1 if fails else 0)
