"""WALL PANELS AFTER CRITIC-wp-1 (2026-10-04): one block for each ruling and each finding repaired.
Every number, name and sentence here is made up for the test.

  R1 (BLOCKER-1)  the proposal's sentence never disagrees with the job: a blank the job itself knows
                  (its sheets, its sizes, its rooms' product words) is filled by the server from the
                  job as it is now and can not be typed over; a blank only a person knows stays typed
  R2 (MAJOR-1)    one suggestion a room in the job's sum; sizes that share a length are alternatives
  R3 (MAJOR-2)    a wall is measured at the scale of the view it is drawn in, by the function a
                  flooring room goes through; a view not known, or contested, gives no feet and says why
  R5              a comma between digits in the wall lengths is refused in words; a rate the job's own
                  copy does not hold follows the account's settings until the job takes it, a rate it
                  holds never moves by itself, and the screen says which is which; an absurd amount is
                  refused in words, never a 500; a letter the proposal's font can not print holds the
                  proposal by the field's name, never a "?"; the proposal's date is shown before
                  issuing and a person can change it
  minors          the sentence and notes can be taken without the rates (3); a price item changed in
                  Settings is said on the row that took it (4); the suggestion's arithmetic never
                  contradicts its count (9); a blank's name is read whatever its case and is at most
                  40 letters (11); words past a trillion (15); a job's delete enters a folder (16)
(R4, the settings store, is tests/test_wp_store.py.)

Synthetic jobs and accounts, every request through the real middleware with no server listening.

    python tests/test_wp_critic1.py
"""
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_JOBS = Path(tempfile.mkdtemp(prefix="wpcr1_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(_JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="wpcr1_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ.pop("TAKEOFF_UI_PASSWORD", None)

import pymupdf  # noqa: E402

ALICE, STAFF = "alice_synth", "staff_synth"


def box_chains(x0, y0, x1, y1, cid0=1):
    pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
    return [{"chain_id": cid0 + i, "style_id": 1, "points": [pts[i], pts[i + 1]],
             "length_pt": abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1])} for i in range(4)]


RESULT = {"page": {"width_pt": 612, "height_pt": 792}, "styles": [{"style_id": 1, "class": "solid", "width": 1.0, "n": 4, "color": [0, 0, 0]}],
          "chains": box_chains(100, 100, 388, 244), "openings": [], "scale": {"ft_per_paper_inch": 4.0, "text": "1/4\" = 1'-0\""}}
NAMES = ("alice-early.pdf", "alice-own.pdf", "alice-views.pdf", "alice-elevations.pdf", "alice-delete.pdf")
reg, JID = {}, {}
for i, name in enumerate(NAMES):
    jid = "wpk%09d" % i
    d = pymupdf.open()
    d.new_page(width=612, height=792)
    pdf = _JOBS / ("upload_%012x_%s" % (i + 1, name))
    d.save(str(pdf))
    d.close()
    (_JOBS / jid).mkdir()
    (_JOBS / jid / "p0_result.json").write_text(json.dumps(RESULT))
    reg[jid] = {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2), "kinds": None,
                "sizes": [[612, 792]], "owner": ALICE, "bid": None}
    JID[name] = jid
(_JOBS / "registry.json").write_text(json.dumps(reg, indent=1))
EARLY, OWN, VIEWS, ELEV, DEL = (JID[n] for n in NAMES)

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402
from takeoff_engine import wp_job as W  # noqa: E402
from takeoff_engine import wp_settings as S  # noqa: E402

server._classify_async = lambda *a, **k: None
_users.save({ALICE: _users.hash_password("orchid-lantern-4471"), STAFF: dict(_users.hash_password("copper-harbor-2265"), staff=True)})
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in (ALICE, STAFF)}
ACCT = hashlib.sha1(ALICE.encode()).hexdigest()[:16]
fails = 0


def check(ok, msg, got=None):
    global fails
    print(("  ok   " if ok else "  FAIL ") + msg + ("" if ok or got is None else f"  (got {str(got)[:600]})"))
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


def call(method, path, body=None, raw=False, rawbody=None, who=ALICE):
    """(status, answer). An exception out of the app is what a real server answers 500 for."""
    b = rawbody if rawbody is not None else (b"" if body is None else json.dumps(body).encode())
    try:
        st, data = asyncio.run(_asgi(method, path, CK[who], b))
    except Exception as e:  # noqa: BLE001
        return 500, (f"EXCEPTION {type(e).__name__}: {e}".encode() if raw else {"detail": f"EXCEPTION {type(e).__name__}: {e}"})
    if raw:
        return st, data
    try:
        return st, json.loads(data)
    except ValueError:
        return st, {"detail": data.decode("utf-8", "replace")}


VER = {}


def get(job):
    st, g = call("GET", f"/api/job/{job}/wallpanels")
    VER[job] = g["version"]
    return g


def act(job, a):
    """One act over the version last read. -> (status, answer); the version follows a write."""
    st, w = call("POST", f"/api/job/{job}/wallpanels/act", {"version": VER.get(job, 0), "act": a})
    if st == 200 and isinstance(w, dict) and "version" in w:
        VER[job] = w["version"]
    return st, w


def must(job, lst):
    w = None
    for a in lst:
        st, w = act(job, a)
        if st != 200 or not w.get("ok"):
            check(False, f"act {a} went through", (st, str(w)[:300]))
    return w


def said(w):
    return str(w.get("detail") or w.get("why") or "") if isinstance(w, dict) else str(w)


def pdf(job):
    st, b = call("GET", f"/api/job/{job}/wallpanels_proposal.pdf", raw=True)
    if st != 200:
        try:
            return st, json.loads(b)
        except ValueError:
            return st, {"detail": b.decode("utf-8", "replace")}
    return st, " ".join("".join(p.get_text() for p in pymupdf.open(stream=b, filetype="pdf")).split())


def line(w, lid):
    return next(x for x in w["computed"]["lines"] if x["id"] == lid)


def save_settings(s, who=ALICE):
    st, cur = call("GET", "/api/wallpanels/settings", who=who)
    st, x = call("POST", "/api/wallpanels/settings", {"settings": s, "rev": cur["settings"]["rev"]}, who=who)
    return st, x


TPL = "Panels {thickness} thick in {colour}, {finish} finish. {sheets} sheets of {sizes}."
SETTINGS = {"labor_rate": "41.37", "overtime_after_hours": "9", "overtime_rate": "17.25", "travel_per_mile": "0.93",
            "hotel_nightly": None, "profit_flat": "650", "profit_per_day": "733.37", "profit_flat_under_sheets": 6,
            "sizes": ["4x8", "4x10", "5x10"], "molding_types": ["vinyl"],
            "price_items": [{"id": "pa", "name": "Panel A", "per": "sqft", "price": "1.2345"}],
            "scope_template": TPL, "notes": ["One trip to the site.", "Net thirty."]}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
_lt = time.localtime()
TODAY = f"{MONTHS[_lt.tm_mon - 1]} {_lt.tm_mday}, {_lt.tm_year}"          # the server's date today, as a proposal prints it

# ---------------------------------------------------------------------------------------------------
print("R5: a rate the job's own copy does not hold follows the account's settings until the job takes it")
st, w = act(EARLY, {"kind": "room_add", "name": "Started early"})
check(st == 200 and w["rates"]["labor_rate"] is None and w["rates"]["sizes"] == [], "a job is started before the account has set anything: its copy holds no rate", w.get("rates"))
st, x = save_settings(SETTINGS)
check(st == 200, "then the account's rates are set (all but the hotel night)", (st, x))
g = get(EARLY)
check(g["rates"]["labor_rate"] == "41.37" and g["rates"]["sizes"] == ["4x8", "4x10", "5x10"] and g["computed"]["scope"]["template"] == TPL
      and g["computed"]["notes"] == ["One trip to the site.", "Net thirty."],
      "the job picks them up: the rates, the sizes, the sentence and the notes read as the settings have them", (g["rates"], g["computed"]["scope"].get("template")))
check(not any("labor rate" in m or "no hourly rate" in m for m in line(g, "labor")["missing"]) and not [h for h in g["computed"]["hold"] if "not set" in h and "Hotel" not in h and "company" not in h],
      "no line says 'not set' for a rate that is set in Settings", [h for h in g["computed"]["hold"] if "not set" in h])
check(len(g.get("rates_follow") or []) >= 9 and any("labor" in f_ and "41.37" in f_ for f_ in g["rates_follow"]) and g["rates_differ"] == [],
      "and the reply says which is which: these the job FOLLOWS (it holds none of its own), and nothing differs", g.get("rates_follow"))
on_disk = json.loads((_JOBS / EARLY / "wp_est" / f"{ACCT}.json").read_text())
check(on_disk["version"] == 1 and on_disk["doc"]["rates"]["labor_rate"] is None, "reading wrote nothing: the copy on disk is still empty")
w = must(EARLY, [{"kind": "job_set", "field": "days", "value": "2"}])
on_disk = json.loads((_JOBS / EARLY / "wp_est" / f"{ACCT}.json").read_text())
check(on_disk["doc"]["rates"]["labor_rate"] == "41.37" and on_disk["doc"]["rates"]["sizes"] == ["4x8", "4x10", "5x10"] and w.get("rates_follow") == []
      and on_disk["doc"]["rates"]["hotel_nightly"] is None,
      "at the job's next change the copy takes what it lacked: they are the job's own now (the hotel night, still not set, is not)",
      (on_disk["doc"]["rates"], w.get("rates_follow")))
st, x = save_settings(dict(SETTINGS, labor_rate="45", hotel_nightly="141.19"))
g = get(EARLY)
check(g["rates"]["labor_rate"] == "41.37" and any("41.37 here, 45.00 in your settings now" in d_ for d_ in g["rates_differ"]),
      "a rate that is SET never moves by itself: the account changes its labor rate, the job keeps 41.37 and says what differs", (g["rates"]["labor_rate"], g["rates_differ"]))
check(g["rates"]["hotel_nightly"] == "141.19" and len(g.get("rates_follow") or []) == 1 and "hotel" in g["rates_follow"][0] and "141.19" in g["rates_follow"][0]
      and not any("hotel" in d_ for d_ in g["rates_differ"]),
      "a rate the copy never had follows the settings as soon as it is set, and is listed as followed - not as a difference", (g["rates"]["hotel_nightly"], g.get("rates_follow")))
st, ws = call("GET", f"/api/job/{EARLY}/wallpanels_worksheet.csv", raw=True)
check("follows your settings now" in ws.decode("utf-8") and "taken from your settings" in ws.decode("utf-8"), "the worksheet says which rates are followed and which were taken later")

print("minor 3: the sentence and notes can be taken without the rates")
st, x = save_settings(dict(SETTINGS, labor_rate="45", hotel_nightly="141.19", scope_template="Changed sentence, {sheets} of {sizes}.", notes=["A corrected note."]))
g = get(EARLY)
check(g["differ_parts"] == {"rates": True, "words": True} if "differ_parts" in g else False, "the reply says both parts differ: the rates, and the proposal's words", g.get("differ_parts"))
st, w = act(EARLY, {"kind": "rates_take", "part": "words"})
check(st == 200 and w["computed"]["scope"]["template"] == "Changed sentence, {sheets} of {sizes}." and w["computed"]["notes"] == ["A corrected note."]
      and w["rates"]["labor_rate"] == "41.37" and any("45.00" in d_ for d_ in w["rates_differ"]) and not any("sentence" in d_ or "notes" in d_ for d_ in w["rates_differ"]),
      "taking the words only: the job's sentence and notes are the settings'; its labor rate is still its own 41.37", (st, said(w), w.get("rates_differ") if isinstance(w, dict) else None))
st, w = act(EARLY, {"kind": "rates_take", "part": "rates"})
check(st == 200 and w["rates"]["labor_rate"] == "45.00" and w["rates_differ"] == [], "taking the rates: nothing differs any more", (st, said(w)))
st, w = act(EARLY, {"kind": "rates_take", "part": "everything-else"})
check(st == 422 and "no such part" in said(w), "a part that is not one is refused in words", (st, said(w)))
save_settings(dict(SETTINGS, hotel_nightly="141.19"))

print("minor 4: a price item changed in Settings is said on the row that took it")
w = must(OWN, [{"kind": "room_add", "name": "Kitchen"}, {"kind": "sheet_add", "size": "4x8"}])
rid, sid = w["rooms"][0]["id"], w["sheets"][0]["id"]
w = must(OWN, [{"kind": "sheet_set", "id": sid, "field": "count", "value": "14"}, {"kind": "sheet_price_item", "id": sid, "item": "pa"}])
check(w["sheets"][0]["price_cents"] == 3950 and not (w["computed"].get("price_notes") or []), "a row takes a price item: 39.50 a sheet, nothing to say yet", w["sheets"][0])
save_settings(dict(SETTINGS, hotel_nightly="141.19", price_items=[{"id": "pa", "name": "Panel A", "per": "sqft", "price": "2.00"}]))
g = get(OWN)
check(g["sheets"][0]["price_cents"] == 3950 and len(g["computed"].get("price_notes") or []) == 1 and "reads 2.00 a square foot in your settings now" in g["computed"]["price_notes"][0]
      and "keeps 1.2345" in line(g, "sheet:" + sid)["how"],
      "the item's price changes in Settings: the row keeps its price (a price never moves by itself) and says what the item reads now", g["computed"].get("price_notes"))
save_settings(dict(SETTINGS, hotel_nightly="141.19", price_items=[]))
g = get(OWN)
check(any("not in your settings any more" in n for n in g["computed"].get("price_notes") or []), "the item is removed from Settings: the row says so", g["computed"].get("price_notes"))
save_settings(dict(SETTINGS, hotel_nightly="141.19"))
must(OWN, [{"kind": "rates_take"}, {"kind": "sheet_set", "id": sid, "field": "price", "value": "39.50"}])

# ---------------------------------------------------------------------------------------------------
print("R1 (BLOCKER-1): the proposal's sentence never disagrees with the job")
call("POST", "/api/branding", {"company": "Example Panels Co", "contact": "555-0100 example.test"})
must(OWN, [{"kind": "room_set", "id": rid, "field": "thickness", "value": '.090"'}, {"kind": "room_set", "id": rid, "field": "colour", "value": "White"},
           {"kind": "job_set", "field": "days", "value": "2"}, {"kind": "job_set", "field": "hours", "value": "8"}, {"kind": "job_set", "field": "miles", "value": "0"},
           {"kind": "job_set", "field": "nights", "value": "0"}, {"kind": "job_set", "field": "freight", "value": "0"},
           {"kind": "job_set", "field": "project", "value": "Example Diner"}, {"kind": "blank_set", "name": "finish", "value": "pebbled"}])
st, w = act(OWN, {"kind": "blank_set", "name": "sheets", "value": "14"})
check(st == 422 and "filled by the job itself" in said(w) and "sheet rows" in said(w), "the number of sheets can not be typed for the proposal: the job fills it, and says where to change it", (st, said(w)))
st, w = act(OWN, {"kind": "blank_set", "name": "sizes", "value": "4x8"})
check(st == 422 and "filled by the job itself" in said(w), "nor the sizes", (st, said(w)))
st, w = act(OWN, {"kind": "blank_set", "name": "colour", "value": "Ivory"})
check(st == 422 and "filled by the job itself" in said(w) and "rooms" in said(w), "nor a product word the rooms hold", (st, said(w)))
st, t0 = pdf(OWN)
check(st == 200 and 'Panels .090" thick in White, pebbled finish. 14 sheets of 4x8.' in t0, "the proposal as priced: 14 sheets of 4x8", (st, str(t0)[:300]))
# the estimator revises the rows: 90 of 4x10 (the critic's scenario)
must(OWN, [{"kind": "sheet_set", "id": sid, "field": "count", "value": "90"}, {"kind": "sheet_set", "id": sid, "field": "size", "value": "4x10"}])
g = get(OWN)
st, t1 = pdf(OWN)
check(st == 200 and "90 sheets of 4x10" in t1 and "14 sheets" not in t1 and "4x8" not in t1,
      "the rows change to 90 of 4x10: read back from the PDF, the sentence follows the job", (st, str(t1)[:300]))
bl = {b["name"]: b for b in g["computed"]["scope"]["blanks"]}
check(bl["sheets"]["value"] == "90" and bl["sheets"].get("kind") == "sheets" and bl["sizes"]["value"] == "4x10" and bl["finish"].get("kind") == "typed"
      and bl["colour"].get("kind") == "rooms", "each blank says who fills it: the sheet rows, the rooms, or a person", bl)
# a document an earlier build wrote, with values typed over the job's own blanks
f = _JOBS / OWN / "wp_est" / f"{ACCT}.json"
old = json.loads(f.read_text())
old["doc"]["job"]["blanks"] = {"sheets": "14", "sizes": "4x8", "colour": "Ivory", "finish": "pebbled"}
f.write_text(json.dumps(old))
g = get(OWN)
st, t2 = pdf(OWN)
check(st == 200 and "in White, pebbled finish. 90 sheets of 4x10." in t2 and "Ivory" not in t2 and "14 sheets" not in t2,
      "a job an earlier build saved with a count, a size and a colour typed over: they are not read - the job's own values print", (st, str(t2)[:300]))
check(g["job"]["blanks"] == {"finish": "pebbled"}, "and only the blank a person alone knows is kept", g["job"]["blanks"])
# rooms that differ
w = must(OWN, [{"kind": "room_add", "name": "Restroom"}])
r2 = w["rooms"][1]["id"]
w = must(OWN, [{"kind": "room_set", "id": r2, "field": "colour", "value": "Almond"}])
st, t3 = pdf(OWN)
check(st == 200 and "in White and Almond, pebbled finish" in t3, "two rooms give two colours: both are printed, in the rooms' order, and nothing is held for it", (st, str(t3)[:300]))
must(OWN, [{"kind": "room_set", "id": r2, "field": "colour", "value": "Grey"}])
st, t4 = pdf(OWN)
check(st == 200 and "in White and Grey, pebbled finish" in t4 and "Almond" not in t4, "a room's colour changes: the sentence follows", (st, str(t4)[:300]))
must(OWN, [{"kind": "room_set", "id": r2, "field": "colour", "value": "white"}])
check("in White, pebbled" in pdf(OWN)[1], "the same colour in another case is one colour")
# no count: open, held, and it says where
w = must(OWN, [{"kind": "sheet_set", "id": sid, "field": "count", "value": ""}])
st, p = pdf(OWN)
check(st == 409 and any("sheets is blank" in m and "it is not typed" in m for m in p.get("missing", [])),
      "a row with no count: the blank is open, the proposal is held, and the list says the job fills it - it is not typed", (st, p))
must(OWN, [{"kind": "sheet_set", "id": sid, "field": "count", "value": "90"}])
# a blank's name is read whatever its case; a height is one height however it is written
st, x = save_settings(dict(SETTINGS, hotel_nightly="141.19", scope_template="{Sheets} sheets of {SIZES}, {Height} high, {Finish} finish."))
must(OWN, [{"kind": "rates_take"}, {"kind": "room_set", "id": rid, "field": "height", "value": "8'"}, {"kind": "room_set", "id": r2, "field": "height", "value": '96"'}])
g = get(OWN)
check(g["computed"]["scope"]["text"] == "90 sheets of 4x10, 8'-0\" high, pebbled finish.",
      "{Sheets}, {SIZES} and {Height} are the job's own blanks whatever their case; 8' and 96\" are one height", g["computed"]["scope"]["text"])
st, x = save_settings(dict(SETTINGS, hotel_nightly="141.19", scope_template="Panels {" + "a" * 41 + "}."))
check(st == 422 and any("41 letters" in p_ for p_ in x.get("problems", [])), "a blank whose name no job could type (41 letters) is refused in Settings", (st, x))
# any other figure or field the job holds: a figure can not be a typed blank at all; the job's own boxes fill theirs
for fig in ("total", "days", "labor_rate", "Profit"):
    st, x = save_settings(dict(SETTINGS, hotel_nightly="141.19", scope_template="Panels for {" + fig + "}."))
    check(st == 422 and any("figure of the job itself" in p_ for p_ in x.get("problems", [])),
          f"a blank named {{{fig}}} is refused in Settings: a figure typed by hand would not follow the job", (st, str(x)[:200]))
st, x = save_settings(dict(SETTINGS, hotel_nightly="141.19", scope_template="{project} at {location}, {date}: {sheets} sheets."))
must(OWN, [{"kind": "rates_take"}, {"kind": "job_set", "field": "location", "value": "12 Example Street"}])
g = get(OWN)
check(g["computed"]["scope"]["text"] == f"Example Diner at 12 Example Street, {TODAY}: 90 sheets.",
      "{project}, {location} and {date} are the job's own boxes: filled from them", g["computed"]["scope"]["text"])
st, w = act(OWN, {"kind": "blank_set", "name": "project", "value": "Another name"})
check(st == 422 and "filled by the job itself" in said(w), "and not typed over", (st, said(w)))
must(OWN, [{"kind": "job_set", "field": "project", "value": "Example Cafe"}])
check(get(OWN)["computed"]["scope"]["text"].startswith("Example Cafe at 12 Example Street"), "the project's name changes: the sentence follows")
must(OWN, [{"kind": "job_set", "field": "project", "value": "Example Diner"}, {"kind": "job_set", "field": "location", "value": ""}])
save_settings(dict(SETTINGS, hotel_nightly="141.19"))
must(OWN, [{"kind": "rates_take"}])

# ---------------------------------------------------------------------------------------------------
print("R5: the proposal's date is shown before issuing and a person can change it")
today = TODAY
g = get(OWN)
dt = g["computed"].get("date") or {}
check(dt.get("text") == today and dt.get("typed") is False and "today" in str(dt.get("by")), "before issuing the reply shows the date the proposal will print: today's, and says so", dt)
st, w = act(OWN, {"kind": "job_set", "field": "date", "value": "10/6/2026"})
check(st == 200 and w["computed"]["date"]["text"] == "October 6, 2026" and w["computed"]["date"]["typed"] is True, "a person types another date", (st, said(w)))
st, t5 = pdf(OWN)
check(st == 200 and "October 6, 2026" in t5 and (today == "October 6, 2026" or today not in t5), "the PDF prints the date that was shown", str(t5)[:200])
for bad in ("soon", "2/30/2026", "10/6/26", "13/1/2026"):
    st, w = act(OWN, {"kind": "job_set", "field": "date", "value": bad})
    check(st == 422 and "is not a date" in said(w) and "month/day/year" in said(w), f"{bad!r} is refused in words, with how to write a date", (st, said(w)))
for good, want in (("Oct 7 2026", "October 7, 2026"), ("2026-10-08", "October 8, 2026"), ("9 October 2026", "October 9, 2026"), ("November 3, 2026", "November 3, 2026")):
    st, w = act(OWN, {"kind": "job_set", "field": "date", "value": good})
    check(st == 200 and w["computed"]["date"]["text"] == want, f"{good!r} is {want}", (st, said(w)))
st, w = act(OWN, {"kind": "job_set", "field": "date", "value": ""})
check(st == 200 and w["computed"]["date"]["text"] == today and w["job"]["date"] is None, "cleared, it is today's again", (st, said(w)))

print("R5: a letter the proposal's font can not print holds the proposal by the field's name - never a '?'")
must(OWN, [{"kind": "job_set", "field": "location", "value": "Łódź 東京 Москва"}])
st, p = pdf(OWN)
check(st == 409 and any("the location" in m and "Ł" in m and "can not print" in m for m in p.get("missing", [])),
      "a location in letters outside the font: refused, the field named with its letters", (st, str(p)[:400]))
must(OWN, [{"kind": "job_set", "field": "location", "value": "Café Niño ½ “curly” ‘quotes’ – dash"}, {"kind": "blank_set", "name": "finish", "value": "漢字"}])
st, p = pdf(OWN)
check(st == 409 and len(p.get("missing", [])) == 1 and "the scope sentence's finish" in p["missing"][0],
      "Latin-1 letters and the marks the page folds are fine; a blank's value outside the font is named instead", (st, str(p)[:400]))
must(OWN, [{"kind": "blank_set", "name": "finish", "value": "pebbled"}])
st, t6 = pdf(OWN)
check(st == 200 and "Café Niño ½ \"curly\" 'quotes' - dash" in t6 and "?" not in t6, "then it issues: accents print, curly quotes and the dash as their plain twins, no '?' anywhere", (st, str(t6)[:300]))
must(OWN, [{"kind": "job_set", "field": "location", "value": ""}])

# ---------------------------------------------------------------------------------------------------
print("R5: an absurd amount is refused in words, never a 500")
w = must(OWN, [{"kind": "line_add"}])
lid = w["lines"][0]["id"]
HUGE = ("9" * 30, "1e30", "1e999999999")
for fld in ("freight", "profit", "labor_rate"):
    for v in HUGE:
        st, w = act(OWN, {"kind": "job_set", "field": fld, "value": v})
        check(st == 422 and "too large" in said(w), f"{fld} {v[:12]!r}: refused in words", (st, said(w)[:160]))
for a, what in (({"kind": "sheet_set", "id": sid, "field": "price", "value": "9" * 30}, "a price a sheet"),
                ({"kind": "line_set", "id": lid, "field": "price", "value": "1e30"}, "a typed line's price")):
    st, w = act(OWN, a)
    check(st == 422 and "too large" in said(w), f"{what}: refused in words", (st, said(w)[:160]))
st, w = call("POST", f"/api/job/{OWN}/wallpanels/act", rawbody=('{"version": %d, "act": {"kind": "job_set", "field": "freight", "value": 1e308}}' % VER[OWN]).encode())
check(st == 422 and "too large" in said(w), "a JSON number of 1e308: refused in words", (st, said(w)[:160]))
st, x = save_settings(dict(SETTINGS, hotel_nightly="9" * 30))
check(st == 422 and any("too large" in p_ for p_ in x.get("problems", [])), "a rate of thirty 9s in Settings: refused in words", (st, str(x)[:200]))
st, x = save_settings(dict(SETTINGS, price_items=[{"name": "X", "per": "sqft", "price": "1e30"}]))
check(st == 422 and any("too large" in p_ for p_ in x.get("problems", [])), "a price item of 1e30: refused in words", (st, str(x)[:200]))
check(get(OWN)["job"]["freight_cents"] == 0, "nothing of it was written")
must(OWN, [{"kind": "line_del", "id": lid}])

# ---------------------------------------------------------------------------------------------------
print("R5: a comma in the typed wall lengths")
for typed in ("1,250", "8,5", "10,000", "8 + 1,250.5"):
    st, w = act(OWN, {"kind": "room_set", "id": rid, "field": "typed", "value": typed})
    check(st == 422 and "comma directly between two digits" in said(w) and "+" in said(w) and "nothing was saved" in said(w),
          f"{typed!r}: refused in words, with what to write instead", (st, said(w)[:200]))
st, w = act(OWN, {"kind": "room_set", "id": rid, "field": "typed", "value": "1,250"})
check("1250 for one length" in said(w) and "1.250 for a decimal" in said(w) and "1 + 250 for two lengths" in said(w), "1,250: write 1250, 1.250 or 1 + 250", said(w))
check(get(OWN)["rooms"][0]["typed"] == "", "nothing was saved")
for typed, lf in (("8, 12.5", "20.5"), ("8 , 12.5", "20.5"), ("8'-6\", 4'", "12.5"), ("1250", "1,250")):
    st, w = act(OWN, {"kind": "room_set", "id": rid, "field": "typed", "value": typed})
    check(st == 200 and w["computed"]["rooms"][rid]["typed_lf"] == lf, f"{typed!r} is {lf} LF: a comma that stands apart from the digits separates two lengths",
          (st, said(w), w.get("computed", {}).get("rooms", {}).get(rid, {}).get("typed_lf") if isinstance(w, dict) else None))

# ---------------------------------------------------------------------------------------------------
print("R2 (MAJOR-1): one suggestion a room in the job's sum")
REC = S.clean(SETTINGS)[0]
doc = W.default_doc()
for a in ({"kind": "room_add", "name": "Room A"}, {"kind": "room_add", "name": "Room B"}):
    W.apply_act(doc, a, "t", "2026-10-04 00:00", REC)
ra, rb = doc["rooms"][0]["id"], doc["rooms"][1]["id"]
for r_, fld, v in ((ra, "typed", "28.5"), (ra, "height", "8'"), (rb, "typed", "263.31"), (rb, "height", "10'")):
    W.apply_act(doc, {"kind": "room_set", "id": r_, "field": fld, "value": v}, "t", "2026-10-04 00:00", REC)
c = W.compute(doc, REC)
ob = c["rooms"][rb]["suggestion"]["options"]
check([(o["size"], o["count"], bool(o.get("alt"))) for o in ob] == [("4x10", 66, False), ("5x10", 53, True)],
      "Room B (263.31 LF at 10 ft): 66 of 4x10, OR 53 of 5x10 - each with its own count, the second marked an alternative", ob)
check(c["suggested"] == [{"size": "4x8", "count": 8}, {"size": "4x10", "count": 66}] and sum(x["count"] for x in c["suggested"]) == 74,
      "the job's line adds ONE choice a room: 8 of 4x8 and 66 of 4x10 = 74 sheets (the narrowest of the shortest length that fits) - not 127", c["suggested"])
check("not added here" in c["suggested_words"] and "1 room" in c["suggested_words"] and "geometry only: no waste, no openings" in c["suggested_words"],
      "and says that a room's other size is listed on the room, not added", c["suggested_words"])
ws = W.worksheet_csv(c, doc, REC, "plan.pdf")
check("or (instead, not as well): 263.31 LF ÷ 5 ft wide" in ws and "suggested in all: 8 of 4x8, 66 of 4x10 (" in ws and "53 of 5x10 (" not in ws,
      "the worksheet says the same: the alternative is 'or', and the sum has one size a room")
_r, bad = S.clean(dict(SETTINGS, sizes=["4x8", "8x4"]))
check(any("same sheet as 4x8" in b_ for b_ in bad), "a size list that names one sheet two ways (4x8 and 8x4) is refused in Settings", bad)

print("minor 9: the suggestion's arithmetic never contradicts its count")
sz = [S.size_dims(s) for s in ("4x8", "4x10", "5x10")]
for lf, want in (("52.01", "52.01 LF ÷ 4 ft wide = a little over 13, rounded up: 14 sheets of 4x8"), ("0.01", "0.01 LF ÷ 4 ft wide = a little over 0, rounded up: 1 sheet of 4x8"),
                 ("51.99", "51.99 LF ÷ 4 ft wide = a little under 13, rounded up: 13 sheets of 4x8"), ("32", "32 LF ÷ 4 ft wide = 8: 8 sheets of 4x8"),
                 ("42.5", "42.5 LF ÷ 4 ft wide = 10.63, rounded up: 11 sheets of 4x8")):
    got = W.suggest(Decimal(lf), Fraction(8), sz)["options"][0]["arith"]
    check(got.startswith(want), f"{lf} LF: {want.split(' = ', 1)[1]}", got)

# ---------------------------------------------------------------------------------------------------
print("R3 (MAJOR-2): a wall is measured at the scale of the view it is drawn in")


def sites_file(job, sites):
    (_JOBS / job / "p0_scale_sites.json").write_text(json.dumps({"key": f"{server.JOBS[job].get('mtime')}|3", "sites": sites}))


def site(fpi, text, x0, x1, title):
    return {"fpi": fpi, "text": text, "box": [x0 + 10.0, 705.0, x0 + 90.0, 717.0], "rule": [float(x0), float(x1), 700.0], "compound": False, "title": title, "nts": False}


# a sheet with a FLOOR PLAN at 1/4" = 1'-0" (its title bar x 50-380) and an ENLARGED TOILET ROOM PLAN at 1/2" = 1'-0" (x 420-590)
sites_file(VIEWS, [site(4.0, "1/4\" = 1'-0\"", 50, 380, "FLOOR PLAN"), site(2.0, "1/2\" = 1'-0\"", 420, 590, "ENLARGED TOILET ROOM PLAN")])
w = must(VIEWS, [{"kind": "room_add", "name": "Toilet"}, {"kind": "room_add", "name": "Plan"}, {"kind": "room_add", "name": "Far"}, {"kind": "room_add", "name": "Line"}])
rt, rp, rf, rl = (r_["id"] for r_ in w["rooms"])
w = must(VIEWS, [{"kind": "wall_draw", "room": rt, "page": 0, "seg": [440, 660, 560, 660]}, {"kind": "wall_draw", "room": rp, "page": 0, "seg": [100, 660, 244, 660]},
                 {"kind": "wall_draw", "room": rf, "page": 0, "seg": [440, 600, 560, 600]}, {"kind": "wall_pick", "room": rl, "page": 0, "chain": 1}])
F = w["computed"]["rooms"]
wt = F[rt]["walls"][0]
check(wt["lf"] == "3.33" and "120.00 pt at 1\" = 2 ft = 3.33 LF" in wt["words"] and "its view: 1/2\" = 1'-0\"" in wt["words"] and F[rt]["wall_lf"] == "3.33",
      "a wall drawn inside the enlarged plan (120 pt): 3.33 LF at that view's 1\" = 2 ft, and it names the view - not 6.67 at the sheet's scale", wt)
st, fs = call("POST", f"/api/job/{VIEWS}/page/0/rooms/scale", {"x": 500, "y": 660, "box": [440, 660, 560, 660]})
check(st == 200 and fs.get("ft_per_paper_inch") == 2.0, "the flooring side answers the same 2 ft per inch for that place (one rule, one function)", (st, fs))
check(hasattr(server, "_wp_wall_view") and hasattr(server, "_room_scale_decide")
      and "_room_scale_decide(" in open(ROOT / "server.py", encoding="utf-8").read().split("def _wp_wall_view")[1].split("\ndef ")[0],
      "the wall's view is decided by the function a flooring room goes through (_room_scale_decide), not by a rule of its own")
wpn = F[rp]["walls"][0]
check(wpn["lf"] == "8" and "at 1\" = 4 ft = 8 LF (the sheet's own view)" in wpn["words"], "a wall in the floor plan (144 pt): 8 LF at 1\" = 4 ft, the sheet's own view", wpn)
check(wt.get("state") == "amber" and wpn.get("state") == "amber" and any("more than one scale" in fl and "view" in fl for fl in F[rt]["flags"]),
      "a view told by its title is an inference: never the clean colour, and the room says to check each wall's view", (wt.get("state"), F[rt]["flags"]))
wf = F[rf]["walls"][0]
check(wf["lf"] is None and wf["pt"] == "120.00" and "two view titles are as near this wall" in wf["words"] and "type this wall's length" in wf["words"]
      and F[rf]["wall_lf"] is None and not F[rf]["suggestion"]["options"] and " LF" not in json.dumps(F[rf]),
      "a wall two view titles are as near (its readings disagree): no feet anywhere, and it says why and what to do", wf)
wl = F[rl]["walls"][0]
check(wl["lf"] is None and "two view titles" in wl["words"] and wl.get("short") == "its view's scale is not known", "a picked line goes through the same rule", wl)
# a sheet whose only scales are an elevation's and a section's: no view a wall can be given
sites_file(ELEV, [site(2.0, "1/2\" = 1'-0\"", 50, 380, "INTERIOR ELEVATION"), site(1.0, "1\" = 1'-0\"", 420, 590, "SECTION A")])
w = must(ELEV, [{"kind": "room_add", "name": "Elevation"}])
w = must(ELEV, [{"kind": "wall_draw", "room": w["rooms"][0]["id"], "page": 0, "seg": [100, 660, 244, 660]}])
we = list(w["computed"]["rooms"].values())[0]["walls"][0]
check(we["lf"] is None and "is not known" in we["words"] and "elevation" in we["words"], "a sheet with no plan view: the wall's view is not known - no feet, and it says why", we)
# a sheet that prints one scale: the sheet's scale, as before
w = must(OWN, [{"kind": "wall_pick", "room": rid, "page": 0, "chain": 1}])
wo = w["computed"]["rooms"][rid]["walls"][0]
check(wo["lf"] == "16" and "288.00 pt at 1\" = 4 ft = 16 LF" in wo["words"] and "its view" not in wo["words"], "a sheet that prints one scale: the sheet's scale in force, as before", wo)

# ---------------------------------------------------------------------------------------------------
print("minor 15: the amount in words past a trillion")
check(W.amount_words(10 ** 17) == "One Quadrillion Dollars" and W.amount_words(6_600_000_000_000_000 * 100 + 5) == "Six Quadrillion Six Hundred Trillion Dollars and Five Cents",
      "a quadrillion dollars is said, not dropped", W.amount_words(10 ** 17))
try:
    W.amount_words(10 ** 25)
    raised = False
except ValueError:
    raised = True
check(raised, "a number with no name is refused, never printed short of its figures")

print("minor 16: a job's delete enters a folder made by hand inside its estimates")
must(DEL, [{"kind": "room_add", "name": "x"}])
(_JOBS / DEL / "wp_est" / "made-by-hand").mkdir()
(_JOBS / DEL / "wp_est" / "made-by-hand" / "note.txt").write_text("x")
st, x = call("DELETE", f"/api/job/{DEL}")
check(st == 200 and not (_JOBS / DEL).exists(), "the job's directory is gone, the folder inside it too", (st, x, (_JOBS / DEL).exists()))

print("nothing of it is an engine file")
from takeoff_engine.detect_page import _ENGINE_FILES  # noqa: E402

check(not [n for n in _ENGINE_FILES if n.startswith("wp_") or n == "server.py"], "the engine signature does not move", _ENGINE_FILES)

print(f"\n{fails} FAILED" if fails else "\nall passed")
sys.exit(1 if fails else 0)
