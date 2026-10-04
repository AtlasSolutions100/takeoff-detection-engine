"""AN ACT CARRIES GEOMETRY AND CHOICES ONLY; EVERY FIGURE IS THE SERVER'S (critic 4, 2026-10-02).

Critic 4 (B1) sent the act route a whole room - a 10 x 10 pt outline claiming 1,000,000 pt2 and
"multi": false - and the customer estimate went from $13,789.50 to $260,630.50 with no open check.
deduct_put, opening_put and sheet_rescale took their figures from the browser the same way. M1: a
room with no floor type was silently left out of an issued estimate, and a floor-prep line was issued
at 0 SF. Each block below FAILS on 8116831:
  * the measured acts (room_put, deduct_put, opening_put) are the server's own: refused at the route
  * a figure key anywhere in an act is refused (400, named), for every act kind
  * sheet_rescale carries only a printed scale; a work area's scale is one the sheet prints
  * the critic's forged room, written on disk: 100 pt2, and an open scale check on a multi-scale sheet
  * a stored document whose figures are hand-edited is measured again on read, its check open
  * the sheet's recorded scale changing under a room reopens its scale check, even on a one-scale sheet
  * an untyped room holds the estimate until it is typed or excluded (the exclusion is on the worksheet);
    an area line at 0 SF is never issued

    python tests/test_fl_figures.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_JOBS = Path(tempfile.mkdtemp(prefix="flfig_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(_JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="flfig_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ.pop("TAKEOFF_UI_PASSWORD", None)

import pymupdf  # noqa: E402

ALICE = "alice_synth"


def box_chains(x0, y0, x1, y1, cid0=1):
    pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
    return [{"chain_id": cid0 + i, "style_id": 1, "points": [pts[i], pts[i + 1]],
             "length_pt": abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1])} for i in range(4)]


RESULT = {"page": {"width_pt": 612, "height_pt": 792}, "styles": [{"style_id": 1, "class": "solid", "width": 1.0, "n": 8, "color": [0, 0, 0]}],
          "chains": box_chains(100, 100, 388, 244) + box_chains(100, 400, 244, 544, 10), "openings": [],
          "scale": {"ft_per_paper_inch": 4.0, "text": "1/4\" = 1'-0\""}}
reg, JID = {}, {}
for i, name in enumerate(("multi.pdf", "single.pdf", "untyped.pdf", "legacy.pdf")):
    jid = "flf%09d" % i
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
MJ, SJ, UJ, LJ = (JID[n] for n in ("multi.pdf", "single.pdf", "untyped.pdf", "legacy.pdf"))

import server  # noqa: E402
from takeoff_engine import users as _users, fl_job as _fj  # noqa: E402

server._classify_async = lambda *a, **k: None
_users.save({ALICE: _users.hash_password("orchid-lantern-4471")})
server._UI_PASSWORD = None
CK = server._sign_session(ALICE)
ACCT = __import__("hashlib").sha1(ALICE.encode()).hexdigest()[:16]
# the multi-scale sheet: a FLOOR PLAN at 1/4" under the rooms, a STOREFRONT ELEVATION at 1/2" (the kitchen's two captions)
SITES = [{"fpi": 4.0, "text": "SCALE: 1/4", "box": [100, 560, 300, 572], "rule": [100, 300, 575], "compound": False, "title": "FLOOR PLAN", "nts": False},
         {"fpi": 2.0, "text": "SCALE: 1/2", "box": [400, 700, 600, 712], "rule": [400, 600, 715], "compound": False, "title": "STOREFRONT ELEVATION", "nts": False}]
_orig_sites = server._scale_sites
server._scale_sites = lambda job, job_id, page: SITES if job_id == MJ else _orig_sites(job, job_id, page)
fails = 0


def check(ok, msg, got=None):
    global fails
    print(("  ok   " if ok else "  FAIL ") + msg + ("" if ok or got is None else f"  (got {str(got)[:500]})"))
    if not ok:
        fails += 1


async def _asgi(method, path, body=b""):
    headers = [(b"host", b"testserver"), (b"x-core-client", b"3"), (b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode()), (b"cookie", f"{server.SESSION_COOKIE}={CK}".encode())]
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


def call(method, path, body=None, raw=False):
    try:
        st, data = asyncio.run(_asgi(method, path, b"" if body is None else json.dumps(body).encode()))
    except Exception as e:          # noqa: BLE001 - a server error is a result here (an earlier build raised on some acts)
        return 500, ({} if not raw else b"") if raw else {"detail": f"server error: {e}"}
    if raw:
        return st, data
    try:
        return st, json.loads(data)
    except ValueError:
        return st, data.decode("utf-8", "replace")


def get(job):
    return call("GET", f"/api/job/{job}/flooring")[1]


def act(job, a):
    return call("POST", f"/api/job/{job}/flooring/act", {"version": get(job)["version"], "act": a})


def est_file(job):
    return _JOBS / job / "fl_est" / f"{ACCT}.json"


def close_room_checks(job):
    """Confirm every open openings / scale check through its own route, as the estimator does."""
    for c in get(job)["computed"]["checks"]:
        if not c["open"]:
            continue
        if c["kind"] == "openings":
            call("POST", f"/api/job/{job}/page/{c['page']}/rooms/{c['uid']}/openings/acknowledge",
                 {"version": get(job)["version"], "count": c["count"], "digest": c["digest"]})
        elif c["kind"] == "scale" and c.get("options"):
            cur = next((o for o in c["options"] if o.get("current")), c["options"][0])
            call("POST", f"/api/job/{job}/page/{c['page']}/rooms/{c['uid']}/scale/acknowledge",
                 {"version": get(job)["version"], "fpi": cur["fpi"], "digest": c["digest"]})


def price(job):
    g = get(job)
    t = g["est"]["types"][0]["id"]
    for a in ({"kind": "type_set", "id": t, "field": "rate", "value": "4.00"}, {"kind": "type_set", "id": t, "field": "waste", "value": "0"},
              {"kind": "type_set", "id": t, "field": "base_no_charge", "value": True}):
        act(job, a)
    return t


print("B1: the measured acts are the server's own; a figure in an act is refused, named")
t = price(MJ)
st, w = act(MJ, {"kind": "room_pick", "page": 0, "x": 240, "y": 170, "styles": [1], "scale_ft_per_in": 4.0, "type": t})
check(st == 200 and w["ok"], "Alice picks a room on the two-scale sheet", w.get("why") if isinstance(w, dict) else w)
room = w["sheets"]["0"]["rooms"][0]
key = f"p0:{room['uid']}"
f0 = w["computed"]["rooms"][key]
check(room["scale"]["multi"] and abs(f0["area_sf"] - 128.0) < 2.6, "the sheet's two captions: multi from the sheet; 128 SF at 1/4\"", (room["scale"]["multi"], f0["area_sf"]))
close_room_checks(MJ)
g = get(MJ)
st, c0 = call("GET", f"/api/job/{MJ}/flooring_estimate.csv", raw=True)
check(st == 200 and not g["computed"]["open"], "its checks confirmed: the customer estimate is issued", g["computed"]["open"])
total0, v0 = g["computed"]["total"], g["version"]
mt0 = est_file(MJ).stat().st_mtime_ns
FORGED = {"uid": "rforged1", "pts": [[160, 160], [200, 160], [200, 200], [160, 200]], "area_pt2": 1000000, "perim_pt": 1,
          "scale": {"how": "sheet", "ft": 4.0, "multi": False}, "type": t}
st, x = act(MJ, {"kind": "room_put", "page": 0, "room": FORGED})
check(st == 400 and "server's own act" in json.dumps(x), "critic 4's room_put (1,000,000 pt2 on a 40 x 40 pt outline) is refused at the route", (st, x))
st, x = act(MJ, {"kind": "deduct_put", "page": 0, "uid": room["uid"], "deduction": {"pts": [[1, 1], [2, 1], [2, 2]], "area_pt2": 999999}})
check(st == 400, "deduct_put with its own area: refused", (st, x))
st, x = act(MJ, {"kind": "opening_put", "page": 0, "uid": room["uid"], "opening": {"x": 200, "y": 100, "w_pt": 9999, "by": "as drawn"}})
check(st == 400, "opening_put with its own width: refused", (st, x))
st, x = act(MJ, {"kind": "sheet_rescale", "page": 0, "ft": 26.742})
check(st == 400 and "ft" in json.dumps(x), "sheet_rescale with a number of the browser's (26.742): refused, the field named", (st, x))
refused = []
for kind in sorted(_fj.ACT_KINDS):
    st, x = act(MJ, {"kind": kind, "page": 0, "uid": room["uid"], "id": t, "area_pt2": 1})
    if st != 400 and not (kind in ("scale_ack", "openings_ack", "measure_ack") and st == 422):
        refused.append((kind, st))
check(not refused, f"every one of the {len(_fj.ACT_KINDS)} act kinds with area_pt2 in it: refused (400, named)", refused)
for a, what in (({"kind": "room_draw", "page": 0, "pts": [[400, 100], [500, 100], [500, 200]], "scale": {"multi": False}}, "scale"),
                ({"kind": "room_pick", "page": 0, "x": 170, "y": 470, "styles": [1], "extra": {"net_sf": 5}}, "net_sf"),
                ({"kind": "opening_add", "page": 0, "uid": room["uid"], "x": 200, "y": 100, "w_pt": 40}, "w_pt"),
                ({"kind": "aids_set", "page": 0, "key": "zoneScales", "value": [{"box": [0, 0, 600, 600], "fpi": 4.0, "text": "x", "multi": True}]}, "multi")):
    st, x = act(MJ, a)
    check(st == 400 and what in json.dumps(x), f"{a['kind']} carrying {what} (nested too): refused, named", (st, x))
st, x = act(MJ, {"kind": "sheet_rescale", "page": 0, "fpi": 3.0})
check(st == 422 and "prints" in json.dumps(x), "sheet_rescale naming a scale the sheet does not print: refused", (st, x))
st, x = act(MJ, {"kind": "aids_set", "page": 0, "key": "zoneScales", "value": [{"box": [0, 0, 600, 600], "fpi": 26.742, "text": "mine"}]})
check(st == 422 and "prints" in json.dumps(x), "a work area at a scale the sheet does not print: refused", (st, x))
g = get(MJ)
check(g["computed"]["total"] == total0 and g["version"] == v0 and est_file(MJ).stat().st_mtime_ns == mt0,
      f"after all of it: the same version, the same total {total0}, nothing written", (g["version"], g["computed"]["total"]))

print("B1: the critic's forged room, written on disk, is measured from its outline and its sheet")
d = json.loads(est_file(MJ).read_text(encoding="utf-8"))
d["doc"]["sheets"]["0"]["rooms"].append(FORGED)
est_file(MJ).write_text(json.dumps(d), encoding="utf-8")
g = get(MJ)
fr = next(r for r in g["sheets"]["0"]["rooms"] if r["uid"] == "rforged1")
ff = g["computed"]["rooms"]["p0:rforged1"]
check(abs(fr["area_pt2"] - 1600.0) < 1e-6 and abs(ff["area_sf"] - 1600 * (4.0 / 72) ** 2) < 1e-6,
      "the forged room reads 1,600 pt2 (4.9 SF at 1/4\"), not 1,000,000", (fr["area_pt2"], ff["area_sf"]))
check(fr["scale"]["multi"] is True and any(c["kind"] == "scale" and c["open"] and c["uid"] == "rforged1" for c in g["computed"]["checks"]),
      "its sheet prints two scales: multi is the sheet's (true), and its scale check is open", fr["scale"]["multi"])
check(any(c["kind"] == "measure" and c["open"] and c["uid"] == "rforged1" for c in g["computed"]["checks"]),
      "it carries no seal of the server's: measured again from its outline, and said", [c["text"] for c in g["computed"]["open"]])
st, x = call("GET", f"/api/job/{MJ}/flooring_estimate.csv")
check(st == 409, "the customer estimate is held", (st, x))

print("B1: a stored document whose figures are hand-edited is measured again on read")
d = json.loads(est_file(MJ).read_text(encoding="utf-8"))
d["doc"]["sheets"]["0"]["rooms"] = [r for r in d["doc"]["sheets"]["0"]["rooms"] if r["uid"] != "rforged1"]
est_file(MJ).write_text(json.dumps(d), encoding="utf-8")
g = get(MJ)
check(g["computed"]["total"] == total0 and not g["computed"]["open"], "the forged room gone: the estimate as before", g["computed"]["open"])
d = json.loads(est_file(MJ).read_text(encoding="utf-8"))
r0 = d["doc"]["sheets"]["0"]["rooms"][0]
r0["area_pt2"] = r0["area_pt2"] * 10
r0["scale"]["multi"] = False
est_file(MJ).write_text(json.dumps(d), encoding="utf-8")
g = get(MJ)
f1 = g["computed"]["rooms"][key]
check(abs(f1["area_sf"] - 128.0) < 2.6 and g["sheets"]["0"]["rooms"][0]["scale"]["multi"] is True,
      "its area x 10 and multi false, edited on disk: read back at its outline's 128 SF, multi from the sheet", (f1["area_sf"], g["sheets"]["0"]["rooms"][0]["scale"]["multi"]))
check(any(c["kind"] == "measure" and c["open"] for c in g["computed"]["checks"]) and call("GET", f"/api/job/{MJ}/flooring_estimate.csv")[0] == 409,
      "and it says so: an open check, the estimate held", [c["text"] for c in g["computed"]["open"]])
st, w = act(MJ, {"kind": "room_edit", "page": 0, "uid": room["uid"], "pts": g["sheets"]["0"]["rooms"][0]["pts"]})
check(st == 200 and w["ok"] and not any(c["kind"] == "measure" for c in w["computed"]["checks"]),
      "measured again by the server (its shape saved): the check closes", (st, w.get("why") if isinstance(w, dict) else w))

print("B1: the sheet's recorded scale changing under a room reopens its check, on a one-scale sheet too")
t2 = price(SJ)
st, w = act(SJ, {"kind": "room_pick", "page": 0, "x": 240, "y": 170, "styles": [1], "scale_ft_per_in": 4.0, "type": t2})
rs = w["sheets"]["0"]["rooms"][0]
check(st == 200 and rs["scale"]["multi"] is False, "a room on a sheet that prints one scale", rs["scale"])
close_room_checks(SJ)
check(call("GET", f"/api/job/{SJ}/flooring_estimate.csv")[0] == 200, "issued at 1/4\"")
(_JOBS / SJ / "p0_scale.json").write_text(json.dumps({"ft_per_paper_inch": 8.0, "text": "1/8\" = 1'-0\"", "set_by": server._BY_REVIEWER,
                                                      "set_at": "2026-10-02T12:00:00", "at": 1.0}))
g = get(SJ)
fs = g["computed"]["rooms"][f"p0:{rs['uid']}"]
sc = [c for c in g["computed"]["checks"] if c["kind"] == "scale" and c["open"]]
check(abs(fs["area_sf"] - 512.0) < 10.5 and sc and "changed under it" in sc[0]["text"],
      "the reviewer records 1/8\" on the sheet: the room reads 4x (512 SF) and its scale check is open, said", (fs["area_sf"], [c["text"] for c in sc]))
check(call("GET", f"/api/job/{SJ}/flooring_estimate.csv")[0] == 409, "held until acknowledged")
close_room_checks(SJ)
g = get(SJ)
check(not g["computed"]["open"] and call("GET", f"/api/job/{SJ}/flooring_estimate.csv")[0] == 200, "acknowledged at the sheet's scale now: issued", g["computed"]["open"])
st, w = act(SJ, {"kind": "room_draw", "page": 0, "pts": [[100, 400], [244, 400], [244, 544], [100, 544]], "scale_ft_per_in": 6.0, "type": t2})
check(st == 409 and "not the sheet scale in force" in json.dumps(w),
      "a room drawn with another scale on screen (6) than the sheet's record (8): refused - the server's scale decides (scale verdict, merged)", (st, w))

print("M1: an untyped room holds the estimate until typed or excluded; no area line at 0 SF")
t3 = price(UJ)
act(UJ, {"kind": "area_set", "which": "prep", "field": "on", "value": True})
act(UJ, {"kind": "area_set", "which": "prep", "field": "rate", "value": "1.25"})
st, w = act(UJ, {"kind": "room_draw", "page": 0, "pts": [[100, 100], [388, 100], [388, 244], [100, 244]], "scale_ft_per_in": 4.0})
ru = w["sheets"]["0"]["rooms"][0]
act(UJ, {"kind": "room_type", "page": 0, "uid": ru["uid"], "type": None})
g = get(UJ)
op = [c["text"] for c in g["computed"]["open"]]
check(any("has no floor type" in x for x in op) and any("0 SF" in x for x in op) and call("GET", f"/api/job/{UJ}/flooring_estimate.csv")[0] == 409,
      "the critic's kitchen: the only room untyped - its check open, prep at 0 SF open, the estimate held (was issued with prep 0)", op)
st, w = act(UJ, {"kind": "room_draw", "page": 0, "pts": [[100, 400], [244, 400], [244, 544], [100, 544]], "scale_ft_per_in": 4.0})
rt = [r for r in w["sheets"]["0"]["rooms"] if r["uid"] != ru["uid"]][0]
act(UJ, {"kind": "room_type", "page": 0, "uid": rt["uid"], "type": t3})
close_room_checks(UJ)
g = get(UJ)
check([c["text"] for c in g["computed"]["open"]] and all("has no floor type" in c["text"] for c in g["computed"]["open"]),
      "a second room typed: the untyped one still holds the estimate (critic 4: it was silently left out)", [c["text"] for c in g["computed"]["open"]])
st, w = act(UJ, {"kind": "room_exclude", "page": 0, "uid": ru["uid"], "on": True})
st, c = call("GET", f"/api/job/{UJ}/flooring_estimate.csv", raw=True)
st2, ws = call("GET", f"/api/job/{UJ}/flooring_worksheet.csv", raw=True)
check(st == 200 and b"Floor prep" in c or b"prep" in c.lower(), "excluded on purpose: the estimate is issued", c[:300])
check(b"Excluded" in ws and ru["name"].encode() in ws, "the worksheet records the room left out, by whom and when", ws[-500:])

print("B1: an earlier build's document (no seals) with critic 4's room in it")
legacy = {"version": 4, "doc": {"v": 5, "est": _fj.default_est(True), "migrated": None,
                                "sheets": {"0": {"rooms": [dict(FORGED, type=None, source="click")], "aids": {}, "seq": 2}}}}
legacy["doc"]["sheets"]["0"]["rooms"][0]["type"] = legacy["doc"]["est"]["types"][0]["id"]
(_JOBS / LJ / "fl_est").mkdir()
est_file(LJ).write_text(json.dumps(legacy), encoding="utf-8")
g = get(LJ)
lm = [c for c in g["computed"]["checks"] if c["kind"] == "measure" and c["open"]]
check(abs(g["sheets"]["0"]["rooms"][0]["area_pt2"] - 1600.0) < 1e-6 and lm and "re-pick the room to restore its fill measure" in lm[0]["text"],
      "critic 5, minor 3 - no band any more: an earlier build's pick is measured again from its outline (1,600 pt2) and says re-pick it",
      (g["sheets"]["0"]["rooms"][0]["area_pt2"], [c["text"] for c in g["computed"]["open"]]))
st, x = call("POST", f"/api/job/{LJ}/page/0/rooms/rforged1/measure/acknowledge", {"version": g["version"], "digest": "x"})
check(st == 422, "acknowledged with a digest that is not the room's: refused", (st, x))
st, w = call("POST", f"/api/job/{LJ}/page/0/rooms/rforged1/measure/acknowledge", {"version": g["version"], "digest": lm[0]["digest"]})
check(st == 200 and not any(c["kind"] == "measure" for c in w["computed"]["checks"]) and w["sheets"]["0"]["rooms"][0]["measure_ack"],
      "acknowledged as it is, with the digest shown: the check closes, who and when kept", (st, w.get("detail") if isinstance(w, dict) else w))

print("a measured room stays the server's: a fixture's own area is not its outline's (found live on a two-sheet set)")
rm = _fj.clean_room({"uid": "rx", "source": "click", "pts": [[100, 100], [388, 100], [388, 244], [100, 244]], "area_pt2": 41200.0,
                     "objects": [{"kind": "cabinet", "area_pt2": 300.0, "pts": [[150, 150], [200, 150], [200, 200], [150, 200]], "deduct": False}],
                     "scale": {"how": "sheet", "ft": 4.0}})
doc = {"sheets": {"0": {"rooms": [json.loads(json.dumps(rm))], "aids": _fj.clean_aids(None)}}}
_fj.reconcile(doc, {0: None}, trusted=True)
check(not doc["sheets"]["0"]["rooms"][0].get("unverified") and doc["sheets"]["0"]["rooms"][0]["area_pt2"] == 41200.0,
      "after an act the server's own rooms are trusted (the cabinet's 300 pt2 against its 2,500 pt2 box flagged the first pick of every walk)")
doc = {"sheets": {"0": {"rooms": [json.loads(json.dumps(rm))], "aids": _fj.clean_aids(None)}}}
_fj.reconcile(doc, {0: None}, legacy=True)
check(doc["sheets"]["0"]["rooms"][0].get("unverified") == _fj.LEGACY_WHY and doc["sheets"]["0"]["rooms"][0]["area_pt2"] == 288 * 144,
      "an earlier build's pick: no band, measured again from its outline (41,472 pt2), flagged re-pick or acknowledge")

print("critic 5, B1: every outline is a room on its sheet - refused on write, flagged on read")
st, x = act(UJ, {"kind": "room_draw", "page": 0, "pts": [[5000, 5000], [5400, 5000], [5400, 5400], [5000, 5400]], "scale_ft_per_in": 4.0, "type": t3})
check(st == 400 and "off the sheet" in json.dumps(x), "critic 5's square at x 5,000 on a 612 pt page: refused (400), the reason named", (st, x))
for pts, why, what in (([[100, 100], [200, 200], [200, 100], [100, 200]], "crosses itself", "a bow-tie"),
                       ([[100, 100], [100.1, 100], [100, 100.1]], "less than 1 SF", "a 0.1 pt triangle"),
                       ([[100, 100], [200, 100], [100, 100], [200, 100]], "3 distinct corners", "two corners only"),
                       ([[-1.5, -1.5], [613.5, -1.5], [613.5, 793.5], [-1.5, 793.5]], "more than the whole sheet", "a box past the whole sheet"),
                       ([[100, 100], [700, 100], [700, 200], [100, 200]], "off the sheet", "one corner past the right edge")):
    st, x = act(UJ, {"kind": "room_draw", "page": 0, "pts": pts, "scale_ft_per_in": 4.0, "type": t3})
    check(st == 400 and why in json.dumps(x), f"{what}: refused (400), '{why}'", (st, x))
st, x = act(UJ, {"kind": "room_draw", "page": 0, "pts": [[0.5, 0.5], [100, 0.5], [100, 100], [0.5, 100]], "scale_ft_per_in": 4.0, "type": t3})
check(st == 200 and x.get("ok"), "a room drawn on the sheet's edge (inside the 2 pt margin) is taken", (st, x.get("detail") if isinstance(x, dict) else x))
before = get(UJ)["computed"]["total"]
d = json.loads(est_file(UJ).read_text(encoding="utf-8"))
off = dict(d["doc"]["sheets"]["0"]["rooms"][-1], uid="roff", pts=[[5000, 5000], [5400, 5000], [5400, 5400], [5000, 5400]])
d["doc"]["sheets"]["0"]["rooms"].append(off)
est_file(UJ).write_text(json.dumps(d), encoding="utf-8")
g = get(UJ)
oc = [c for c in g["computed"]["checks"] if c["kind"] == "outline" and c["uid"] == "roff"]
check(oc and oc[0]["open"] and "off the sheet" in oc[0]["text"] and call("GET", f"/api/job/{UJ}/flooring_estimate.csv")[0] == 409
      and not any(ln["src"] == "floor" and "6,0" in ln["qty"] for ln in g["computed"]["lines"])
      and not any("roff" in json.dumps(it) for it in g["computed"]["pages"]["0"]["items"]),
      "the same square written on disk: flagged, in no line and no takeoff row, the estimate held", [c["text"] for c in g["computed"]["open"]])

print("critic 5, minor 1: the seal binds the sheet and the PDF; its key is its own")
d = json.loads(est_file(UJ).read_text(encoding="utf-8"))
d["doc"]["sheets"]["0"]["rooms"] = [r for r in d["doc"]["sheets"]["0"]["rooms"] if r["uid"] != "roff"]
est_file(UJ).write_text(json.dumps(d), encoding="utf-8")
call("GET", f"/api/job/{UJ}/flooring")
d = json.loads(est_file(UJ).read_text(encoding="utf-8"))
r1 = [r for r in d["doc"]["sheets"]["0"]["rooms"] if r["source"] == "drawn" and not r.get("excluded")][0]
d["doc"]["sheets"]["7"] = {"rooms": [dict(r1, uid="rp7")], "aids": {}, "seq": 2}
est_file(UJ).write_text(json.dumps(d), encoding="utf-8")
g = get(UJ)
check(any(c["kind"] == "outline" and c["uid"] == "rp7" and "no sheet 8" in c["text"] for c in g["computed"]["open"]),
      "a sheet filed under page 8 of a 1-page job: flagged, counted nowhere", [c["text"] for c in g["computed"]["open"]])
d = json.loads(est_file(UJ).read_text(encoding="utf-8"))
d["doc"]["sheets"].pop("7", None)
est_file(UJ).write_text(json.dumps(d), encoding="utf-8")
seal0 = r1["seal"]
check(seal0 != server._fl_seal(UJ, ACCT, _fj.clean_room(r1), 1) and seal0 == server._fl_seal(UJ, ACCT, _fj.clean_room(r1), 0),
      "the seal carries the page: the same room sealed for sheet 2 is another seal")
kf = _JOBS / "fl_seal.key"
check(kf.exists() and kf.read_bytes().strip() != server._session_secret(), "the seal's key is its own file in the store (fl_seal.key), not session.key")

print("critic 5, minor 6: a room traced again after exclude and delete starts with the sheet's last type")
g = get(UJ)
for r in g["sheets"]["0"]["rooms"]:
    act(UJ, {"kind": "room_del", "page": 0, "uid": r["uid"]})
d = json.loads(est_file(UJ).read_text(encoding="utf-8"))
d["doc"]["sheets"]["0"]["last_type"] = None
est_file(UJ).write_text(json.dumps(d), encoding="utf-8")
st, w = act(UJ, {"kind": "room_draw", "page": 0, "pts": [[100, 100], [388, 100], [388, 244], [100, 244]], "scale_ft_per_in": 4.0, "type": t3})
rid = w["sheets"]["0"]["rooms"][-1]["uid"]
act(UJ, {"kind": "room_del", "page": 0, "uid": rid})
st, w = act(UJ, {"kind": "room_draw", "page": 0, "pts": [[100, 100], [388, 100], [388, 244], [100, 244]], "scale_ft_per_in": 4.0})
check(st == 200 and w["sheets"]["0"]["rooms"][-1]["type"] == t3, "deleted, traced again with no type named: the sheet's last type", w["sheets"]["0"]["rooms"][-1]["type"])

print("minor 4: the customer PDF carries the account's letterhead once a company is set")
close_room_checks(MJ)
st, pdfb = call("GET", f"/api/job/{MJ}/flooring_estimate.pdf", raw=True)
check(st == 200, "the two-scale job's estimate is issued again (its room measured again and confirmed)", [c["text"] for c in get(MJ)["computed"]["open"]])
t0 = pymupdf.open(stream=pdfb, filetype="pdf")[0].get_text() if st == 200 else ""
st2, _b = call("POST", "/api/branding", {"company": "Synthetic Floor Co", "contact": "555-0100 · floors@example.test"})
st, pdfb = call("GET", f"/api/job/{MJ}/flooring_estimate.pdf", raw=True)
t1 = pymupdf.open(stream=pdfb, filetype="pdf")[0].get_text() if st == 200 else ""
check(st2 == 200 and "Synthetic Floor Co" not in t0 and "Synthetic Floor Co" in t1 and "555-0100" in t1,
      "no company: no letterhead; a company set: it heads the customer estimate, with its contact line", (st2, t1[:200]))

print("minor 5: a drawn room starts with the floor type a pick would")
st, w = act(UJ, {"kind": "room_draw", "page": 0, "pts": [[300, 400], [380, 400], [380, 480], [300, 480]], "scale_ft_per_in": 4.0})
check(st == 200 and w["sheets"]["0"]["rooms"][-1]["type"] == t3, "drawn with no type named: the sheet's last type", w["sheets"]["0"]["rooms"][-1]["type"])

print("minor 1: a long description is refused in words, never cut")
st, x = act(UJ, {"kind": "line_add", "desc": "x" * 201})
check(st == 422 and "201" in json.dumps(x), "201 characters: refused, said", (st, x))
st, w = act(UJ, {"kind": "line_add", "desc": "Supply & install transition strips, brushed aluminium, Schluter RENO-T, colour \"Ivory\" ½ inch, at every door"})
check(st == 200 and w["est"]["lines"][-1]["desc"].endswith("at every door"), "a 95-character line is kept whole", w["est"]["lines"][-1]["desc"] if st == 200 else x)

print("critic 5, minor 3: a document written by this branch's round 7 (doc v6) reads as an earlier build's")
d6 = json.loads(est_file(SJ).read_text(encoding="utf-8"))
d6["doc"]["v"] = 6
est_file(SJ).write_text(json.dumps(d6), encoding="utf-8")
g = get(SJ)
check(any(c["kind"] == "measure" and c["open"] and "re-pick the room" in c["text"] for c in g["computed"]["checks"])
      and call("GET", f"/api/job/{SJ}/flooring_estimate.csv")[0] == 409,
      "its pick measured again from the outline, flagged, the estimate held until re-picked or acknowledged", [c["text"] for c in g["computed"]["open"]])

print("the merge: how the page-level scale verdict stands decides how the rooms stand")
def _doc_with(facts):
    t0 = _fj.clean_type({"id": "tA", "name": "Carpet", "rate_cents": 400, "waste_pct": "0", "base_no_charge": True}, 0)
    est = _fj.default_est(False)
    est["types"] = [t0]
    rr = _fj.clean_room({"uid": "rv", "source": "drawn", "pts": [[100, 100], [388, 100], [388, 244], [100, 244]], "type": "tA",
                         "scale": {"how": "sheet", "ft": 4.0}, "openings": []})
    dd = {"v": _fj.DOC_V, "est": est, "sheets": {"0": {"rooms": [rr], "aids": _fj.clean_aids(None), "seq": 2}}, "migrated": None}
    dd = _fj.clean_doc(dd)
    _fj.reconcile(dd, {0: facts}, trusted=True, n_pages=1)
    r = dd["sheets"]["0"]["rooms"][0]
    r["open_ack"] = {"digest": _fj.openings_digest(r, None), "count": 0, "by": "x", "at": "y"}
    return dd, _fj.compute(dd)
dd, cc = _doc_with({"ft": None, "sheet": "none", "words": "the scale readings disagree: caption 1/8\" against dimensions 1/4\"",
                    "eng": 8.0, "choices": [], "multi": False, "wh": [612, 792]})
nc = [c["text"] for c in cc["open"] if c["kind"] == "noscale"]
check(nc and "readings disagree" in nc[0] and cc["rooms"]["p0:rv"]["area_sf"] is None,
      "readings disagree (no scale in force): the room in pt only, held, with the verdict's words", nc)
dd, cc = _doc_with({"ft": 4.0, "sheet": "amber", "words": "one reading (the caption), not checked", "eng": 4.0, "choices": [], "multi": False,
                    "wh": [612, 792]})
ws = _fj.worksheet_csv(cc, dd)
check(not cc["open"] and cc["sheet_flags"] and "sheet scale not confirmed" in ws,
      "accepted / one reading: the value, nothing held, 'sheet scale not confirmed' on the worksheet", (cc["open"], cc["sheet_flags"]))
dd, cc = _doc_with({"ft": 4.0, "sheet": "clean", "words": None, "eng": 4.0, "choices": [], "multi": False, "wh": [612, 792]})
check(not cc["open"] and not cc["sheet_flags"], "confirmed / set by hand: clean", (cc["open"], cc["sheet_flags"]))

print(f"\n{fails} FAILED" if fails else "\nall passed")
sys.exit(1 if fails else 0)
