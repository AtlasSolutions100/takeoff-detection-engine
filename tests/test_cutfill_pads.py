"""BUILDING PADS (2026-09-27): a finished-floor height printed inside a closed outline is OFFERED
as a pad; the proposed surface is flat at that height over the outline only once the operator
turns the pad on. Synthetic sheets (1 pt = 1 ft):

1. what is offered: the smallest closed outline round the floor height; an outline that is the
   sheet's border, one too small, one left open, a floor height switched off: no pad;
2. the operator's record finds its pad again when a read moves the label across a rounding edge
   (a radius test), and finds nothing when the printed height changed (nothing is carried);
3. the volumes: untouched while no pad is on; with the pad on, the ground inside the outline is
   the floor height, what the proposed surface held inside the outline is left out, a line that
   ends at the wall stays;
4. the route: a pad is turned on and off by its key, the record keeps where and what; a pad
   with no outline is refused.

    python tests/test_cutfill_pads.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
STORE = tempfile.mkdtemp(prefix="cfpad_")
os.environ["TAKEOFF_JOBS_DIR"] = STORE
os.environ["CUTFILL_OUT"] = os.path.join(STORE, "cutfill")
os.environ["CUTFILL_UI_DIR"] = str(ROOT / "webui" / "cutfill")

import numpy as np  # noqa: E402
import pymupdf  # noqa: E402

from cutfill import elevations as E  # noqa: E402
from cutfill.chains import Chain  # noqa: E402
from cutfill.labels import make_label  # noqa: E402
from cutfill.project import Project, compute_cutfill, grade_at, pads_in_force  # noqa: E402

fails = 0


def check(ok, what):
    global fails
    print(("ok: " if ok else "FAIL: ") + what)
    if not ok:
        fails += 1


def chain(cid, pts, closed=False):
    P = np.asarray(pts, dtype=float)
    L = float(np.sqrt((np.diff(P, axis=0) ** 2).sum(axis=1)).sum())
    return Chain(id=cid, style=0, pts=P, length=L, members=[], closed=closed)


def rect(x0, y0, x1, y1, open_by=0.0):
    """A rectangle walked from its top-left corner; `open_by` leaves the last side that short."""
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0 + open_by)]


def floor(lid, text, x, y, z=None, used=True):
    lab = make_label(lid, 0, text, (x - 30, y - 5, x + 30, y + 5))
    lab.z = lab.value if z is None else z
    lab.used = used
    return lab


# ---- 1. what is offered ---------------------------------------------------------------------------
W = H = 2000.0
lot = chain(1, rect(200, 200, 900, 900))                     # a lot round the building
bldg = chain(2, rect(400, 400, 700, 600))                    # the building: 300 x 200
border = chain(3, rect(20, 20, 1980, 1980))                  # the sheet's border: 96 % of the sheet
shed = chain(4, rect(1200, 300, 1240, 340))                  # 40 x 40 = 1,600 pt2: too small
nearly = chain(5, rect(1200, 600, 1500, 800, open_by=5.0))   # its ends 5 pt apart: closed
left_open = chain(6, rect(1200, 1000, 1500, 1200, open_by=10.0))   # 10 pt apart: open
labs = [floor(10, "FFE=102.50", 550, 500), floor(11, "FF=99.0", 1220, 320), floor(12, "FFE 104.25", 1350, 700),
        floor(13, "FFE=98.00", 1350, 1100), floor(14, "FFE=97.00", 1700, 1700, used=False)]
check(all(l.kind == "floor" for l in labs), f"FFE / FF with a number is a floor height: {[l.kind for l in labs]}")
pads = E.find_floor_pads(labs, [lot, bldg, border, shed, nearly, left_open], W, H)
by = {p["label_id"]: p for p in pads}
check(sorted(by) == [10, 11, 12, 13], f"a floor height switched off is no offer; the others are listed: {sorted(by)}")
check(by[10]["chain"] == 2 and abs(by[10]["area_pt2"] - 60000.0) < 1 and len(by[10]["polygon"]) == 4 and by[10]["z"] == 102.5,
      f"the pad is the SMALLEST closed outline round the floor height (the building, not the lot): piece {by[10]['chain']}, "
      f"{by[10]['area_pt2']} pt2")
check(by[11]["polygon"] is None and by[11]["chain"] is None,
      "an outline under 2,000 pt2 is no building, and the border (over a quarter of the sheet) is no pad: listed, no outline")
check(by[12]["chain"] == 5, "an outline whose ends are 5 pt apart is closed")
check(by[13]["polygon"] is None, "one whose ends are 10 pt apart is open: no outline")
check(E.find_floor_pads([l for l in labs if l.id == 10], [lot, border], W, H)[0]["chain"] == 1,
      "with no building drawn the lot is the smallest outline: the offer names it, the operator decides")
pts = E.pad_points(by[10]["polygon"], step=12.0)
check(len(pts) > 400 and pts[:, 0].min() >= 400 - 1e-6 and pts[:, 0].max() <= 700 + 1e-6 and pts[:, 1].min() >= 400 - 1e-6
      and pts[:, 1].max() <= 600 + 1e-6, f"a pad stands on its outline and a grid inside it, nothing outside: {len(pts)} points")

# ---- 2. the record finds its pad again ------------------------------------------------------------
an0 = {"pads": pads}
rec = {"use": True, "at": by[10]["at"], "z": 102.5, "text": "FFE=102.50", "area_pt2": 60000.0}
ps = {"pads": {by[10]["key"]: dict(rec)}}
found, lost = pads_in_force(an0, ps)
check([p["label_id"] for p, _k, _r in found] == [10] and not lost, "a record finds its pad by its key")
moved = E.find_floor_pads([floor(10, "FFE=102.50", 550.6, 500.6)], [lot, bldg], W, H)
check(moved[0]["key"] != by[10]["key"], f"a label 0.6 pt off lands on another key ({by[10]['key']} -> {moved[0]['key']})")
found, lost = pads_in_force({"pads": moved}, ps)
check([p["key"] for p, _k, _r in found] == [moved[0]["key"]] and not lost,
      "the record still finds it: where the floor height is printed is a radius test, not a bucket")
other_z = E.find_floor_pads([floor(10, "FFE=103.50", 550, 500)], [lot, bldg], W, H)
found, lost = pads_in_force({"pads": other_z}, ps)
check(not found and len(lost) == 1, "the floor height printed there changed: the record finds nothing (it is said, never carried)")
far = E.find_floor_pads([floor(10, "FFE=102.50", 580, 500)], [lot, bldg], W, H)
found, lost = pads_in_force({"pads": far}, ps)
check(not found and len(lost) == 1, "the same height 30 pt away is another label: the record finds nothing")
two = E.find_floor_pads([floor(10, "FFE=102.50", 550, 500), floor(20, "FFE=102.50", 556, 500)], [lot, bldg], W, H)
found, lost = pads_in_force({"pads": two}, {"pads": {by[10]["key"]: dict(rec), "x": dict(rec, at=[555.0, 500.0])}})
check(sorted(p["label_id"] for p, _k, _r in found) == [10, 20] and not lost, "two records never take one pad")


# ---- 3. the volumes -------------------------------------------------------------------------------
def site(pid, with_lines=True):
    """Existing: a plane rising from 100.8 ft (x = 20) to 105.2 ft (x = 580). Proposed: flat at 102 ft.
    A building 200 x 200 in the middle with FFE = 104.00. With lines: a piece lying in the building
    that carries a (wrong) height of 110 ft, and a proposed contour that ends at the building's wall."""
    pdf = os.path.join(STORE, f"{pid}.pdf")
    doc = pymupdf.open()
    doc.new_page(width=600, height=600)
    doc.save(pdf)
    doc.close()
    p = Project.create(pid, pid=pid)
    p.add_document(pdf, name=f"{pid}.pdf", copy=False)
    key = "d0p0"
    p.page_settings(key)["role"] = "both"
    p.data["settings"].update({"scale_ft_per_in": 72.0, "scale_source": "typed", "cell_ft": 5.0, "display_page": key,
                               "region": [[200, 200], [400, 200], [400, 400], [200, 400]], "limit": {"source": "drawn"}})
    spots = []
    for y in range(40, 561, 40):
        for x, ze, zp in ((20.0, 100.8, 102.0), (580.0, 105.2, 102.0)):
            spots.append({"id": len(spots), "label_id": None, "x": x, "y": float(y), "z": ze, "side": "existing", "tag": "EG", "anchor": "dot"})
            spots.append({"id": len(spots), "label_id": None, "x": x, "y": float(y), "z": zp, "side": "proposed", "tag": "FG", "anchor": "dot"})
    lab = floor(7, "FFE=104.00", 300, 300)
    out = chain(1, rect(200, 200, 400, 400))
    pads_ = E.find_floor_pads([lab], [out], 600.0, 600.0)
    chains, contours, fams = [], [], []
    if with_lines:
        inside = [[230.0, 260.0], [370.0, 260.0]]                     # all of it in the building
        to_wall = [[40.0, 320.0], [120.0, 320.0], [200.0, 320.0]]     # ends ON the wall
        through = [[150.0, 350.0], [420.0, 350.0]]                    # 200 of its 270 pt in the building (74 %)
        chains = [{"id": 50, "style": 3, "pts": inside, "length": 140.0}, {"id": 51, "style": 3, "pts": to_wall, "length": 160.0},
                  {"id": 52, "style": 3, "pts": through, "length": 270.0}]
        contours = [{"id": 0, "style": 3, "z": 110.0, "how": "counted", "chain_ids": [50], "label_ids": [], "length": 140.0, "basis": []},
                    {"id": 1, "style": 3, "z": 102.0, "how": "label", "chain_ids": [51], "label_ids": [], "length": 160.0, "basis": []},
                    {"id": 2, "style": 3, "z": 110.0, "how": "counted", "chain_ids": [52], "label_ids": [], "length": 270.0, "basis": []}]
        fams = [{"style": 3, "styles": [3], "role": "proposed", "sig": "s3"}]
    an = {"engine": "test", "key": key, "doc": 0, "page": 0, "width": 600.0, "height": 600.0, "sheet": "T1", "page_role": "both",
          "lines_from": "engine", "styles": [], "families": fams, "gaps": [], "labels": [lab.to_json()], "chains": chains,
          "contours": contours, "spots": spots, "pads": pads_, "counts": {}, "notes": [], "scale_candidates": [], "scale_default": None}
    os.makedirs(os.path.dirname(p.analysis_path(key)), exist_ok=True)
    json.dump(an, open(p.analysis_path(key), "w"))
    p.save()
    return p, key, pads_[0]


p, key, pad = site("pad-planes", with_lines=False)
r0 = compute_cutfill(p)
check(not r0.get("error") and abs(r0["totals"]["cut_cy"] - 40000.0 / 27.0) < 1.0 and r0["totals"]["fill_cy"] == 0.0,
      f"no pad on: the proposed plane (102 ft) against the existing one: cut {r0.get('totals', {}).get('cut_cy')} "
      f"(expected {40000 / 27:.1f}), fill {r0.get('totals', {}).get('fill_cy')}")
check(not any(e["kind"] == "pad" for e in r0["evidence"]) and not any("building pad" in w for w in r0["warnings"]),
      "and nothing of the pad is in the surface or the warnings: an offer changes nothing")
p.page_settings(key)["pads"] = {pad["key"]: {"use": False, "at": pad["at"], "z": pad["z"], "text": pad["text"], "area_pt2": pad["area_pt2"]}}
p.save()
r_off = compute_cutfill(p)
check(r_off["totals"] == r0["totals"] and len(r_off["evidence"]) == len(r0["evidence"]), "a pad turned off again: the same volumes, point for point")
p.page_settings(key)["pads"][pad["key"]]["use"] = True
p.save()
r1 = compute_cutfill(p)
check(not r1.get("error") and abs(r1["totals"]["fill_cy"] - 40000.0 / 27.0) < 1.0 and r1["totals"]["cut_cy"] == 0.0,
      f"the pad on: the ground in the outline is 104 ft, 1 ft over the existing plane on average: fill "
      f"{r1.get('totals', {}).get('fill_cy')} (expected {40000 / 27:.1f}), cut {r1.get('totals', {}).get('cut_cy')}")
g = grade_at(p, key, [[300, 300], [210, 390], [399, 201], [20, 300]], surface="proposed")
check(g["z"][:3] == [104.0, 104.0, 104.0] and g["z"][3] == 102.0, f"flat at the floor height everywhere in the outline, the site's own grade away from it: {g['z']}")
ge = grade_at(p, key, [[300, 300]], surface="existing")
check(abs(ge["z"][0] - 103.0) < 0.01, f"the existing ground under the building is untouched: {ge['z']}")
w = [x for x in r1["warnings"] if "building pad" in x]
check(len(w) == 1 and "'FFE=104.00'" in w[0] and "104.00 ft" in w[0] and "40,000 SF" in w[0] and "turned on by you" in w[0] and "left out" not in w[0],
      f"the result says it, once: {w}")
src = r1["sources"][0]
check(src["proposed_points"] == sum(1 for e in r1["evidence"] if e["surface"] == "proposed"), "the Sources card counts the pad's points with the sheet's")
pe = [e for e in r1["evidence"] if e["kind"] == "pad"]
check(len(pe) > 100 and all(e["z"] == 104.0 and e["ref"]["label"] == 7 and e["ref"]["text"] == "FFE=104.00" for e in pe),
      f"every pad point names the printed floor height it stands on: {len(pe)} points")
hs = [s_["text"] for h in r1["hotspots"] for s_ in h["sources"]]
check(any(t.startswith("building pad at its printed floor height 104 ft") for t in hs), f"and Where the volume sits names the pad: {hs[:2]}")

p, key, pad = site("pad-lines", with_lines=True)
r0 = compute_cutfill(p)
z_in = grade_at(p, key, [[300, 260]], surface="proposed")["z"][0]
check(not r0.get("error") and abs(z_in - 110.0) < 0.01, f"no pad on: the piece in the building holds the surface at its own 110 ft: {z_in}")
p.page_settings(key)["pads"] = {pad["key"]: {"use": True, "at": pad["at"], "z": pad["z"], "text": pad["text"], "area_pt2": pad["area_pt2"]}}
p.save()
r1 = compute_cutfill(p)
g = grade_at(p, key, [[300, 260], [300, 350], [120, 320], [410, 350], [170, 350]], surface="proposed")
check(g["z"][0] == 104.0 and g["z"][1] == 104.0, f"the pad on: 104 ft where the two pieces lay in the building: {g['z'][:2]}")
check(g["z"][2] == 102.0, f"the contour that ends at the wall stays (102 ft on it): {g['z'][2]}")
check(abs(g["z"][3] - 110.0) > 1.0 and abs(g["z"][4] - 110.0) > 1.0,
      f"a piece with three quarters of its length in the building is no contour: its ends outside are left out too: {g['z'][3:]}")
prop_now = r1["sources"][0]["proposed_points"]
n_pad = sum(1 for e in r1["evidence"] if e["kind"] == "pad")
n_wall = sum(1 for e in r1["evidence"] if e["kind"] == "contour" and e["ref"].get("chain") == 51)
n_spot = sum(1 for e in r1["evidence"] if e["kind"] == "spot" and e["surface"] == "proposed")
check(prop_now == n_pad + n_spot + n_wall and n_wall == 28,
      f"the proposed surface is the pad, the spot grades and the whole contour to the wall (its end ON the wall too): "
      f"{prop_now} = {n_pad} + {n_spot} + {n_wall}")
w = [x for x in r1["warnings"] if "building pad" in x]
check(len(w) == 1 and "left out" in w[0] and "2 lines lying mostly inside the building" in w[0], f"what was left out is said: {w}")
p.page_settings(key)["pads"][pad["key"]]["area_pt2"] = 30000.0
p.save()
r2 = compute_cutfill(p)
check(any("40,000 SF now, 30,000 SF when you turned it on" in x for x in r2["warnings"]), "an outline that reads another size than the one turned on is said")
an = json.load(open(p.analysis_path(key)))
an["pads"] = E.find_floor_pads([floor(7, "FFE=105.00", 300, 300)], [chain(1, rect(200, 200, 400, 400))], 600.0, 600.0)
json.dump(an, open(p.analysis_path(key), "w"))
p._an_cache.pop(key, None)
r3 = compute_cutfill(p)
check(not any(e["kind"] == "pad" for e in r3["evidence"]) and any("is not printed there" in x and "FFE=104.00" in x for x in r3["warnings"]),
      "the sheet prints another floor height now: the pad turned on is not used, and the result says so")

# ---- 4. the route ---------------------------------------------------------------------------------
from cutfill import server as S  # noqa: E402


class Req:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


p, key, pad = site("pad-route", with_lines=False)
S._projects[p.id] = p
out = asyncio.run(S.patch_page(p.id, key, Req({"pads": {pad["key"]: {"use": True}}})))
rec = out["pages"][key]["pads"][pad["key"]]
check(rec["use"] is True and rec["at"] == pad["at"] and rec["z"] == 104.0 and rec["text"] == "FFE=104.00" and rec["area_pt2"] == pad["area_pt2"] and rec["set"] > 0,
      f"turning a pad on records where its floor height is printed, what it says and the outline's size: {rec}")
a = S.analysis(p.id, key)
check(a["status"] == "done" and [pd["use"] for pd in a["data"]["pads"]] == [True], "the read then says the pad is on (the card shows what the volumes use)")
asyncio.run(S.patch_page(p.id, key, Req({"pads": {pad["key"]: {"use": False}}})))
check([pd["use"] for pd in S.analysis(p.id, key)["data"]["pads"]] == [False] and p.page_settings(key)["pads"][pad["key"]]["use"] is False,
      "turned off: the record stays, off")
asyncio.run(S.patch_page(p.id, key, Req({"pads": {pad["key"]: None}})))
check(p.page_settings(key)["pads"] == {}, "a null takes the record away")
# a record an older key holds is replaced, never doubled
p.page_settings(key)["pads"] = {"old-key": {"use": True, "at": [pad["at"][0] + 0.6, pad["at"][1]], "z": 104.0, "text": "FFE=104.00", "area_pt2": 40000.0}}
p.save()
asyncio.run(S.patch_page(p.id, key, Req({"pads": {pad["key"]: {"use": False}}})))
check(list(p.page_settings(key)["pads"]) == [pad["key"]] and p.page_settings(key)["pads"][pad["key"]]["use"] is False,
      f"a record an older key held for the same pad is replaced, not doubled: {list(p.page_settings(key)['pads'])}")
for body, why in (({"pads": {"no-such-pad": {"use": True}}}, "a pad that is not on this read"),):
    try:
        asyncio.run(S.patch_page(p.id, key, Req(body)))
        check(False, f"{why}: expected a refusal")
    except Exception as e:      # noqa: BLE001 - HTTPException
        check(getattr(e, "status_code", None) == 409, f"{why} is refused: {getattr(e, 'detail', e)}")
an = json.load(open(p.analysis_path(key)))
an["pads"] = E.find_floor_pads([floor(7, "FFE=104.00", 300, 300)], [], 600.0, 600.0)
json.dump(an, open(p.analysis_path(key), "w"))
p._an_cache.pop(key, None)
try:
    asyncio.run(S.patch_page(p.id, key, Req({"pads": {an["pads"][0]["key"]: {"use": True}}})))
    check(False, "a pad with no outline: expected a refusal")
except Exception as e:      # noqa: BLE001
    check(getattr(e, "status_code", None) == 409 and "outline" in str(getattr(e, "detail", "")), f"a pad with no outline cannot be turned on: {getattr(e, 'detail', e)}")

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILED'}")
sys.exit(1 if fails else 0)
