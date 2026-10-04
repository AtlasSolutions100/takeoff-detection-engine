"""THE FLOORING JOB'S ROUTES (rulings P-W, 2026-10-02; critic 3's reproductions).

One document per job and per account holds the estimate and the rooms; the browser sends acts, the
server measures and computes. Synthetic jobs, two synthetic accounts, every request through the real
middleware (sign-in, ownership, client gate) with no server listening. Each block names the critic's
finding it reproduces - each FAILS on e408ab4:
  * B1 / Q  opening a job writes nothing; a migrated job opens at the same total every time
  * B2 / R  no flag closes a check: the whole-estimate PUT is gone, an "ack" act is refused, a price
            check closes only when a price is set
  * B3 / S  another account's page write changes nothing of this account's; a shared sheet's export,
            bid sheet and estimate carry none of this account's words or prices to another account
  * B4 / Q  a 2.0 job is migrated once, its prices and types kept, and opens at the same total
  * M3      a stale tab's act is refused - a deleted room does not come back
  * W       a room's openings check closes with the count the estimator saw; any change reopens it

    python tests/test_fl_acts.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_JOBS = Path(tempfile.mkdtemp(prefix="flacts_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(_JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="flacts_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ.pop("TAKEOFF_UI_PASSWORD", None)

import pymupdf  # noqa: E402

ALICE, BOB = "alice_synth", "bob_synth"


def box_chains(x0, y0, x1, y1, cid0=1):
    pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
    return [{"chain_id": cid0 + i, "style_id": 1, "points": [pts[i], pts[i + 1]],
             "length_pt": abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1])} for i in range(4)]


RESULT = {"page": {"width_pt": 612, "height_pt": 792}, "styles": [{"style_id": 1, "class": "solid", "width": 1.0, "n": 4, "color": [0, 0, 0]}],
          "chains": box_chains(100, 100, 388, 244), "openings": [], "scale": {"ft_per_paper_inch": 4.0, "text": "1/4\" = 1'-0\""}}

reg, JID = {}, {}
for i, (name, owner) in enumerate([("C3.0-Site-Plan-Rev.1.pdf", None), ("alice-own.pdf", ALICE), ("alice-20.pdf", ALICE),
                                   ("alice-v4.pdf", ALICE)]):
    jid = "fla%09d" % i
    d = pymupdf.open()
    d.new_page(width=612, height=792)
    pdf = _JOBS / ("upload_%012x_%s" % (i + 1, name))
    d.save(str(pdf))
    d.close()
    (_JOBS / jid).mkdir()
    (_JOBS / jid / "p0_result.json").write_text(json.dumps(RESULT))
    reg[jid] = {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2), "kinds": None,
                "sizes": [[612, 792]], "owner": owner, "bid": None}
    JID[name] = jid
(_JOBS / "registry.json").write_text(json.dumps(reg, indent=1))
SH, OWN, J20, JV4 = (JID[n] for n in ("C3.0-Site-Plan-Rev.1.pdf", "alice-own.pdf", "alice-20.pdf", "alice-v4.pdf"))

# a 2.0 page record (ade3738): types with $/SF and $/LF on the type, rooms with the type's index; the
# takeoff panel's label price for a type that carried none on itself
ROOM20 = {"name": "Chamber", "pts": [[100, 100], [388, 100], [388, 244], [100, 244]], "area_sf": 288.0, "area_pt2": 41472.0,
          "perim_lf": 72.0, "perim_pt": 864.0, "openings_lf": 3.0, "holes_sf": 0, "doors": 1, "closures": 0, "type": 0,
          "source": "click", "bridges": [], "objects": [], "deductions": []}
ROOM20b = dict(ROOM20, name="Assistant", type=1, area_sf=200.0, area_pt2=28800.0, perim_lf=60.0, perim_pt=720.0, openings_lf=0.0,
               pts=[[400, 100], [520, 100], [520, 340], [400, 340]])
(_JOBS / J20 / "p0_calcstate.json").write_text(json.dumps({
    "v": 1, "engine_sig": "s", "prices": {"Flooring — LVP": 3.0},
    "fl": {"types": [{"name": "Carpet", "color": "#4e9af1", "sf_price": 4, "lf_price": 2, "waste_pct": 0},
                     {"name": "LVP", "color": "#f2a33a", "sf_price": 0, "lf_price": 0, "waste_pct": 10}],
           "rooms": [ROOM20, ROOM20b], "closures": [], "seq": 3}}))
# a page record and an estimate of the round of 2026-10-02 (fl_v 4): rooms with q, their type in the
# estimate's assign, an acknowledgement of a price check that was forged by a direct write
V4ROOM = {"name": "Gallery", "uid": "ra", "pts": [[100, 100], [388, 100], [388, 244], [100, 244]], "area_sf": 288.0,
          "area_pt2": 41472.0, "perim_lf": 72.0, "perim_pt": 864.0, "source": "click", "openings": [], "base_off": [],
          "q": {"net_sf": 2880, "base_lf": 1}}
(_JOBS / JV4 / "p0_calcstate.json").write_text(json.dumps({"v": 1, "engine_sig": "s", "fl": {"fl_v": 4, "rooms": [V4ROOM]}}))

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

server._classify_async = lambda *a, **k: None
_users.save({ALICE: _users.hash_password("orchid-lantern-4471"), BOB: _users.hash_password("basalt-meadow-9083")})
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in (ALICE, BOB)}
ACCT = {u: __import__("hashlib").sha1(u.encode()).hexdigest()[:16] for u in (ALICE, BOB)}
(_JOBS / JV4 / "fl_est").mkdir()
(_JOBS / JV4 / "fl_est" / f"{ACCT[ALICE]}.json").write_text(json.dumps({"version": 7, "est": {
    "types": [{"id": "tC", "name": "Carpet", "unit": "SF", "rate_cents": None, "waste_pct": "7"}],
    "assign": {"p0:ra": "tC"}, "acks": {"price:tC": {"what": "forged", "by": "x", "at": "y"}}}}))
fails = 0


def check(ok, msg, got=None):
    global fails
    print(("  ok   " if ok else "  FAIL ") + msg + ("" if ok or got is None else f"  (got {str(got)[:400]})"))
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
    return call("POST", f"/api/job/{job}/flooring/act", CK[who], {"version": version, "act": a})


def est_file(job, who):
    return _JOBS / job / "fl_est" / f"{ACCT[who]}.json"


print("B1 / Q: opening a job writes nothing")
st, g0 = call("GET", f"/api/job/{OWN}/flooring", CK[ALICE])
check(st == 200 and g0["version"] == 0 and g0["stored"] is False and [t["name"] for t in g0["est"]["types"]] == ["Carpet", "LVP", "Tile", "Hardwood"],
      "a job with no flooring opens with the four default types, version 0, not stored", g0)
for _ in range(3):
    call("GET", f"/api/job/{OWN}/flooring", CK[ALICE])
check(not est_file(OWN, ALICE).exists(), "four reads later: no file written")
st, w = act(OWN, ALICE, 0, {"kind": "room_pick", "page": 0, "x": 240, "y": 170, "styles": [1], "scale_ft_per_in": 4.0,
                             "type": g0["est"]["types"][0]["id"]})
check(st == 200 and w["ok"] and w["version"] == 1 and len(w["sheets"]["0"]["rooms"]) == 1, "a pick is the first act: version 1, one room", w if st != 200 else w.get("why"))
room = w["sheets"]["0"]["rooms"][0] if st == 200 and w.get("sheets", {}).get("0", {}).get("rooms") else {}
key = f"p0:{room.get('uid')}"
fig = (w.get("computed", {}).get("rooms") or {}).get(key, {})
check(abs((fig.get("area_sf") or 0) - 128.0) < 2.6, "the server measured it: 288 x 144 pt at 1/4\" is 16 x 8 ft = 128 SF; the fill reads within 2 %", fig.get("area_sf"))
st, dup = act(OWN, ALICE, 1, {"kind": "room_pick", "page": 0, "x": 250, "y": 170, "styles": [1], "scale_ft_per_in": 4.0})
check(st == 200 and dup["ok"] is False and "already on the list" in dup["why"] and dup["version"] == 1,
      "a click inside a listed room is refused with words, nothing written", dup.get("why"))
v = 1
st, w = act(OWN, ALICE, v, {"kind": "room_pick", "page": 0, "x": 20, "y": 20, "styles": [1], "scale_ft_per_in": 4.0})
check(st == 200 and w["ok"] is False and "sheet edge" in (w.get("why") or ""), "a leak is refused with the engine's words", w.get("why"))

print("B2 / R: no flag closes a check")
st, x = call("PUT", f"/api/job/{OWN}/flooring", CK[ALICE], {"version": v, "est": {"acks": {"price:x": {"what": "ok"}}}})
check(st == 410, "the whole-estimate write is gone (410): a page from the earlier build is told to reload", st)
st, x = act(OWN, ALICE, v, {"kind": "ack", "key": f"price:{g0['est']['types'][0]['id']}"})
check(st == 422 and "unknown act" in json.dumps(x), "an 'ack' act is refused", x)
st, x = act(OWN, ALICE, v, {"kind": "type_set", "id": g0["est"]["types"][0]["id"], "field": "acks", "value": {"price": True}})
check(st == 422, "no type field is a flag", x)
st, x = act(OWN, ALICE, v, {"kind": "scale_ack", "page": 0, "uid": room.get("uid"), "fpi": 4, "digest": "x"})
check(st == 422, "a scale acknowledgement only through the room's own route", x)
st, c = call("GET", f"/api/job/{OWN}/flooring_estimate.csv", CK[ALICE])
check(st == 409 and any("no price" in t for t in c.get("open", [])), "the customer estimate is held: the carpet has no price", c)
tid = g0["est"]["types"][0]["id"]
for a in ({"kind": "type_set", "id": tid, "field": "rate", "value": "4.50"}, {"kind": "type_set", "id": tid, "field": "waste", "value": "0"},
          {"kind": "type_set", "id": tid, "field": "base_no_charge", "value": True}):
    st, w = act(OWN, ALICE, v, a)
    v = w.get("version", v)
st, c = call("GET", f"/api/job/{OWN}/flooring_estimate.csv", CK[ALICE])
check(st == 409 and [t for t in c.get("open", []) if "opening" in t] and not [t for t in c.get("open", []) if "price" in t or "waste" in t],
      "the price and waste checks closed by their data; the room's openings check is still open", c)
op = [k for k in w["computed"]["checks"] if k["kind"] == "openings"][0]
st, x = call("POST", f"/api/job/{OWN}/page/0/rooms/{room['uid']}/openings/acknowledge", CK[ALICE], {"version": v, "count": op["count"] + 1, "digest": op["digest"]})
check(st == 422 and "is not the" in json.dumps(x), "the openings confirmed with a count the room does not have: refused", x)
st, w = call("POST", f"/api/job/{OWN}/page/0/rooms/{room['uid']}/openings/acknowledge", CK[ALICE], {"version": v, "count": op["count"], "digest": op["digest"]})
check(st == 200 and w["ok"], "confirmed with the count it has", w)
v = w["version"]
st, c = call("GET", f"/api/job/{OWN}/flooring_estimate.csv", CK[ALICE], raw=True)
check(st == 200 and b"Supply & Install" in c, "nothing open: the customer estimate is issued", c[:200])
st, pdfb = call("GET", f"/api/job/{OWN}/flooring_estimate.pdf", CK[ALICE], raw=True)
txt = pymupdf.open(stream=pdfb, filetype="pdf")[0].get_text() if st == 200 else ""
check(st == 200 and "Estimate" in txt and "TOTAL" in txt and "Supply & Install" in txt and "BETA" not in txt.upper()[:0] + txt,
      "the customer estimate as its own PDF: Estimate, the lines, TOTAL (ruling U)", txt[:300])
st, w = act(OWN, ALICE, v, {"kind": "opening_add", "page": 0, "uid": room["uid"], "x": 200, "y": 100.5})
check(st == 200 and w["ok"], "an opening added on the wall", w.get("why"))
v = w["version"]
check(any(k["kind"] == "openings" and k["open"] for k in w["computed"]["checks"]), "the list changed: its openings check is open again (W)")
oid = [o for o in w["sheets"]["0"]["rooms"][0]["openings"]][-1]["id"]
st, x = act(OWN, ALICE, v, {"kind": "opening_width", "page": 0, "uid": room["uid"], "oid": oid, "value": "36"})
check(st == 422 and "unit" in json.dumps(x), "a bare number is not a width", x)
st, w = act(OWN, ALICE, v, {"kind": "opening_width", "page": 0, "uid": room["uid"], "oid": oid, "value": "3'-0\""})
v = w["version"]
b = w["computed"]["rooms"][key]["base"]
check(st == 200 and abs(b["lf"] - (b["ring_lf"] - 3.0)) < 0.01, "a 3'-0\" opening is 3 ft out of the base (server-computed)", b)

print("M3: a stale tab's act is refused")
st, w = act(OWN, ALICE, v, {"kind": "room_name", "page": 0, "uid": room["uid"], "name": "Chamber"})
v_tab1 = w["version"]
st, w = act(OWN, ALICE, v_tab1, {"kind": "room_del", "page": 0, "uid": room["uid"]})
check(st == 200 and not w["sheets"]["0"]["rooms"], "tab 1 deletes the room")
st, x = act(OWN, ALICE, v_tab1, {"kind": "room_name", "page": 0, "uid": room["uid"], "name": "Back"})
check(st == 409 and x.get("detail") == server.FL_STALE, "tab 2 (stale) renames it: refused with the sentence", x)
st, g = call("GET", f"/api/job/{OWN}/flooring", CK[ALICE])
check(not g["sheets"]["0"]["rooms"], "the deleted room stays deleted", g["sheets"]["0"]["rooms"])

print("B3 / S: another account changes nothing of this account's")
st, a0 = call("GET", f"/api/job/{SH}/flooring", CK[ALICE])
va = a0["version"]
ta = a0["est"]["types"][0]["id"]
for a in ({"kind": "type_set", "id": ta, "field": "rate", "value": "9.99"}, {"kind": "type_set", "id": ta, "field": "mfr", "value": "AcmeSecretMfr"},
          {"kind": "type_set", "id": ta, "field": "colour", "value": "ColourSecret"}, {"kind": "notes_set", "notes": ["A's secret note"]},
          {"kind": "line_add", "desc": "Secret freight deal"}):
    st, w = act(SH, ALICE, va, a)
    va = w.get("version", va)
st, w = act(SH, ALICE, va, {"kind": "room_draw", "page": 0, "pts": [[100, 100], [388, 100], [388, 244], [100, 244]], "scale_ft_per_in": 4.0, "type": ta})
check(st == 200 and w["ok"], "A traces a room on the shared sheet", w.get("why"))
va = w["version"]
ra = w["sheets"]["0"]["rooms"][0]
a_total = w["computed"]["total"]
a_net = w["computed"]["rooms"][f"p0:{ra['uid']}"]["net_sf"]
forged = {"v": 1, "fl": {"fl_v": 4, "rooms": [dict(ra, q={"net_sf": a_net * 10, "base_lf": 1, "scale_by": "work area", "unplaced": 0})]}}
st, _x = call("POST", f"/api/job/{SH}/page/0/calcstate", CK[BOB], forged)
st, a1 = call("GET", f"/api/job/{SH}/flooring", CK[ALICE])
check(a1["computed"]["total"] == a_total and a1["computed"]["rooms"][f"p0:{ra['uid']}"]["net_sf"] == a_net,
      f"B posts the page record with q x 10: A's room stays {a_net:.1f} SF and her total {a_total}", a1["computed"]["total"])
st, b0 = call("GET", f"/api/job/{SH}/flooring", CK[BOB])
bt = json.dumps(b0)
check(st == 200 and not (b0["sheets"].get("0") or {}).get("rooms") and all(s not in bt for s in ("AcmeSecretMfr", "ColourSecret", "secret note", "Secret freight", "9.99")),
      "B's own flooring on the sheet: none of A's rooms, words or prices", [s for s in ("AcmeSecretMfr", "ColourSecret", "secret note", "Secret freight", "9.99") if s in bt])
st, x = act(SH, BOB, b0["version"], {"kind": "room_name", "page": 0, "uid": ra["uid"], "name": "mine"})
check(st == 422, "B can not act on A's room: it is not in his document", x)
ap = {"labels": {}, "scale_seen": server._scale_stamp(SH, 0), "image_dpi": 100, "extra_items": [{"label": "Flooring — Carpet · AcmeSecretMfr / ColourSecret", "qty": 999, "unit": "SF", "unit_price": 9.99}],
      "prices": {"Flooring — Carpet · AcmeSecretMfr / ColourSecret": 9.99}, "fl_est_version": va}
st, x = call("POST", f"/api/job/{SH}/page/0/approve", CK[ALICE], ap)
check(st == 200, "A approves the shared page", x if st != 200 else None)
exp = json.loads((_JOBS / SH / "p0_elements.json").read_text())
fl_rows = [it for it in exp["items"] if it.get("src") == "flooring"]
et = json.dumps(exp)
check(fl_rows and all(it.get("unit_price") is None and it.get("fl_acct") == ACCT[ALICE] for it in fl_rows)
      and "AcmeSecretMfr" not in et and "9.99" not in et and "999" not in json.dumps([it["qty"] for it in exp["items"]]),
      "the export carries the SERVER's flooring rows: A's account, no price, no product words; the browser's row with 999 SF is gone", exp["items"])
st, eb = call("GET", f"/api/job/{SH}/page/0/elements", CK[BOB])
check(st == 200 and not [it for it in eb.get("items", []) if str(it.get("label", "")).startswith(("Flooring", "Base/molding"))],
      "B reads the approved export: no flooring row of A's", eb.get("items") if isinstance(eb, dict) else eb)
st, ea = call("GET", f"/api/job/{SH}/page/0/elements", CK[ALICE])
check(st == 200 and [it for it in ea.get("items", []) if it.get("src") == "flooring"], "A reads it: her own flooring rows are there")
st, bs = call("GET", f"/api/job/{SH}/bidsheet.csv", CK[BOB], raw=True)
check(st == 200 and b"AcmeSecretMfr" not in bs and b"Flooring" not in bs, "B's bid sheet: none of A's flooring", bs[:300])
st, rp = call("GET", f"/api/job/{SH}/report.pdf", CK[BOB], raw=True)
rtxt = "".join(p.get_text() for p in pymupdf.open(stream=rp, filetype="pdf")) if st == 200 else ""
check(st == 200 and all(s not in rtxt for s in ("AcmeSecretMfr", "ColourSecret", "secret note", "Secret freight", "9.99")) and "Flooring" not in rtxt,
      "B's report: none of A's flooring rows, words, prices or estimate", (st, [s for s in ("AcmeSecretMfr", "9.99", "Flooring") if s in rtxt]))
st, rpa = call("GET", f"/api/job/{SH}/report.pdf", CK[ALICE], raw=True)
atxt = "".join(p.get_text() for p in pymupdf.open(stream=rpa, filetype="pdf")) if st == 200 else ""
check(st == 200 and "Flooring" in atxt and "Estimate" in atxt, "A's own report: her flooring rows and her estimate page (held: the room's openings are not confirmed)", (st, atxt[:200]))
for path in (f"/api/job/{SH}/flooring_worksheet.csv", f"/api/job/{SH}/flooring_estimate.csv", f"/api/job/{SH}/page/0/calcstate"):
    st, body = call("GET", path, CK[BOB], raw=True)
    check(all(s.encode() not in body for s in ("AcmeSecretMfr", "ColourSecret", "secret note", "Secret freight", "9.99")),
          f"B {path.rsplit('/', 1)[-1]}: none of A's strings", body[:200])

print("B4 / Q: a 2.0 job is migrated once and opens at the same total")
check(not est_file(J20, ALICE).exists(), "before the first visit: no document")
st, m1 = call("GET", f"/api/job/{J20}/flooring", CK[ALICE])
types = {t["name"]: t for t in m1["est"]["types"]}
check(st == 200 and m1["version"] == 1 and m1["computed"]["migrated"] and types["Carpet"]["rate_cents"] == 400
      and types["Carpet"]["base_rate_cents"] == 200 and types["LVP"]["rate_cents"] == 300 and types["LVP"]["waste_pct"] == "10",
      "the first read migrates once: Carpet $4/SF + $2/LF base, LVP $3/SF from the 2.0 label price, its 10 % waste", m1["est"]["types"])
rooms20 = m1["sheets"]["0"]["rooms"]
check([r["type"] for r in rooms20] == [types["Carpet"]["id"], types["LVP"]["id"]], "each room keeps its 2.0 type", [r["type"] for r in rooms20])
mt = est_file(J20, ALICE).stat().st_mtime_ns
totals = []
for _ in range(5):
    st, m = call("GET", f"/api/job/{J20}/flooring", CK[ALICE])
    totals.append((m["version"], m["computed"]["total"]))
check(len(set(totals)) == 1 and est_file(J20, ALICE).stat().st_mtime_ns == mt, f"opened five times: the same version and total {totals[0]}, nothing written", totals)
visit = {"v": 1, "engine_sig": "s", "fl": {"fl_v": 4, "rooms": [dict(ROOM20, type=None), dict(ROOM20b, type=None)]}}
call("POST", f"/api/job/{J20}/page/0/calcstate", CK[ALICE], visit)
st, m = call("GET", f"/api/job/{J20}/flooring", CK[ALICE])
disk = json.loads((_JOBS / J20 / "p0_calcstate.json").read_text())
check(m["computed"]["total"] == totals[0][1] and disk["fl"].get("types"), "an earlier build's visit re-saving the page without its types changes nothing (the page's flooring part is never taken from a browser)",
      (m["computed"]["total"], list(disk["fl"])))
st, ws = call("GET", f"/api/job/{J20}/flooring_worksheet.csv", CK[ALICE], raw=True)
check(b"Migrated" in ws and b"2.0" in ws, "the worksheet records the migration and the 2.0 money", ws[-400:])
items = m["computed"]["pages"]["0"]["items"]
check(any(it["label"].startswith("Waste — LVP") for it in items) and all(it.get("src") == "flooring" and "unit_price" not in it for it in items),
      "a 2.0 type keeps its own takeoff rows (net, then waste, unrounded), unpriced", [(i["label"], i["qty"]) for i in items])

print("round of 2026-10-02: its estimate and page rooms migrate; a forged acknowledgement does not")
st, m4 = call("GET", f"/api/job/{JV4}/flooring", CK[ALICE])
r4 = (m4["sheets"].get("0") or {}).get("rooms") or []
check(st == 200 and m4["version"] == 8 and len(r4) == 1 and r4[0]["type"] == "tC", "the room and its type from the old estimate's assign", m4.get("version"))
f4 = m4["computed"]["rooms"].get("p0:ra", {})
check(abs((f4.get("net_sf") or 0) - 288.0) < 0.5, "its figures are computed again from its outline: 288 SF, not the 2,880 its q said", f4.get("net_sf"))
check(any(k["kind"] == "price" and k["open"] for k in m4["computed"]["checks"]), "the forged price acknowledgement is not carried: the price check is open")

print(f"\n{fails} FAILED" if fails else "\nall passed")
sys.exit(1 if fails else 0)
