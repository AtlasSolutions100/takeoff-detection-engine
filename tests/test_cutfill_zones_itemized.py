"""GRADING ZONES ITEMIZED (2026-09-28, Koby: "total cut is 10k, engineer boxes lot a which is 3k and
then island which is 5k and then lot b which is 2k ... categorized in the takeoff and isolated on the
cut/fill map").

A synthetic two-plane sheet (existing z = 100 + 0.01 x, proposed flat 102, 1 pt = 1 ft):
- no limit drawn: the zones ARE the area, and they add up to the totals exactly;
- a drawn limit larger than the zones: the rest of it is its own part, and zones + rest = the totals;
- a zone reaching past the limit counts only inside it, and says so;
- zones that overlap for real are not itemized (the old way, with a warning); a sliver is not an overlap;
- the bid sheet files a zone's row on its Site Cut / Site Fill line whatever the zone is called.

    python tests/test_cutfill_zones_itemized.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
STORE = tempfile.mkdtemp(prefix="cfz_")
os.environ["TAKEOFF_JOBS_DIR"] = STORE
os.environ["CUTFILL_OUT"] = os.path.join(STORE, "cutfill")
os.environ["CUTFILL_UI_DIR"] = str(ROOT / "webui" / "cutfill")

import pymupdf  # noqa: E402

from cutfill.project import Project, compute_cutfill  # noqa: E402
from takeoff_engine import bidsheet  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


pdf = os.path.join(STORE, "planes.pdf")
doc = pymupdf.open()
doc.new_page(width=600, height=600)
doc.save(pdf)
doc.close()
p = Project.create("zones", pid="zones")
p.add_document(pdf, name="planes.pdf", copy=False)
key = "d0p0"
p.page_settings(key)["role"] = "both"
p.data["settings"].update({"scale_ft_per_in": 72.0, "scale_source": "typed", "cell_ft": 5.0, "display_page": key})
pts = []
for y in range(40, 561, 40):
    pts.append({"id": len(pts), "label_id": None, "x": 40.0, "y": float(y), "z": 100.4, "side": "existing", "tag": "EG", "anchor": "dot"})
    pts.append({"id": len(pts), "label_id": None, "x": 560.0, "y": float(y), "z": 105.6, "side": "existing", "tag": "EG", "anchor": "dot"})
    pts.append({"id": len(pts), "label_id": None, "x": 40.0, "y": float(y), "z": 102.0, "side": "proposed", "tag": "FG", "anchor": "dot"})
    pts.append({"id": len(pts), "label_id": None, "x": 560.0, "y": float(y), "z": 102.0, "side": "proposed", "tag": "FG", "anchor": "dot"})
an = {"engine": "test", "key": key, "doc": 0, "page": 0, "width": 600.0, "height": 600.0, "sheet": "T1", "page_role": "both", "lines_from": "engine",
      "styles": [], "families": [], "gaps": [], "labels": [], "chains": [], "contours": [], "spots": pts, "counts": {}, "notes": [],
      "scale_candidates": [], "scale_default": None}
os.makedirs(os.path.dirname(p.analysis_path(key)), exist_ok=True)
json.dump(an, open(p.analysis_path(key), "w"))


def sq(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


# ---- 1. no limit drawn: the zones are the area -------------------------------------------------------
p.data["settings"]["region"] = None
p.data["settings"]["zones"] = [{"id": 1, "name": "Lot A", "polygon": sq(100, 100, 250, 400), "section_ft": 0.0},
                               {"id": 2, "name": "Street", "polygon": sq(250, 100, 300, 400), "section_ft": 1.5},
                               {"id": 3, "name": "Lot B", "polygon": sq(300, 100, 450, 400), "section_ft": 0.0}]
p.save()
r = compute_cutfill(p)
check(not r.get("error"), f"compute: {r.get('error')}")
t = r["totals"]
zs = {z["name"]: z for z in r["zones"]}
check(r["zones_itemized"] and r["region_source"].startswith("the grading zones"),
      f"no limit drawn: the zones are the area ({r['region_source']})")
check(abs(sum(z["cut_cy"] for z in zs.values()) - t["cut_cy"]) < 0.2 and abs(sum(z["fill_cy"] for z in zs.values()) - t["fill_cy"]) < 0.2
      and abs(sum(z["area_sf"] for z in zs.values()) - t["area_sf"]) < 2 and abs(t["area_sf"] - 350 * 300) < 2,
      f"the zones add up to the totals: cut {[z['cut_cy'] for z in zs.values()]} = {t['cut_cy']}, area {t['area_sf']} (105,000)")
# the street, dug 1.5 ft under a flat 102 over existing 102.5..103: cut = 50 x 300 x (depth) / 27
exp = sum(max(0.0, (100 + 0.01 * (250 + 5 * k + 2.5)) - (102 - 1.5)) * 5 * 300 for k in range(10)) / 27
check(abs(zs["Street"]["cut_cy"] - exp) / exp < 0.02 and zs["Street"]["fill_cy"] == 0.0,
      f"the street zone is dug to its own 1.5 ft: cut {zs['Street']['cut_cy']} (expected about {exp:.0f})")
labels = [x["label"] for x in r["takeoff"]["rows"]]
check("Site Cut – Street" in labels and "Site Fill – Lot A" in labels and "Site Cut" not in labels,
      f"the takeoff carries one row pair per zone: {labels}")
check(r["takeoff"]["zones_total"].get("itemized") and not r["takeoff"]["zones_total"].get("rest"),
      "the takeoff's zone total says it is itemized, with no rest (no limit)")
# the zones replace the whole job when no limit is drawn: what they leave out is said, in SF and yards
# (both surfaces reach 520 x 520 ft; the zones hold 105,000 SF of it)
check(any("only the grading zones are computed (105,000 SF): 165,400 SF more has both surfaces (cut" in w and "left out" in w
          for w in r["warnings"]), f"what the zones leave out is said: {[w for w in r['warnings'] if 'left out' in w]}")

# ---- 2. a drawn limit larger than the zones: the rest is its own part ------------------------------
p.data["settings"]["region"] = sq(60, 60, 540, 540)
p.data["settings"]["section_ft"] = 0.5
p.save()
r2 = compute_cutfill(p)
t2 = r2["totals"]
rest = r2["zones_rest"]
zsum_c = sum(z["cut_cy"] for z in r2["zones"]) + rest["cut_cy"]
zsum_f = sum(z["fill_cy"] for z in r2["zones"]) + rest["fill_cy"]
check(rest and abs(rest["area_sf"] - (480 * 480 - 350 * 300)) < 2 and rest["section_ft"] == 0.5,
      f"the rest of the limit is its own part at the stack's depth: {rest}")
check(abs(zsum_c - t2["cut_cy"]) < 0.3 and abs(zsum_f - t2["fill_cy"]) < 0.3,
      f"zones + rest = the limit's totals: cut {zsum_c:.1f} / {t2['cut_cy']}, fill {zsum_f:.1f} / {t2['fill_cy']}")
check(abs(r2["zones"][1]["cut_cy"] - zs["Street"]["cut_cy"]) < 0.5,
      f"the stack does not reach inside a zone: the street keeps its own 1.5 ft ({r2['zones'][1]['cut_cy']} vs {zs['Street']['cut_cy']})")
check(any(x["label"] == "Site Fill – rest of the limit" or x["label"] == "Site Cut – rest of the limit" for x in r2["takeoff"]["rows"]),
      f"the rest files its own rows: {[x['label'] for x in r2['takeoff']['rows']]}")
check(any("the layer stack lowers the proposed surface 0.5 ft in the rest of the limit; Lot A (finished grade), Street (18 in), "
          "Lot B (finished grade) keep their own depth: the volumes are to subgrade" in w
          for w in r2["warnings"]), f"the stack's warning says where it applies and which zones keep their own depth: {r2['warnings']}")

# ---- 3. a zone past the limit counts only inside it -----------------------------------------------
p.data["settings"]["zones"][2]["polygon"] = sq(300, 100, 600, 400)
p.save()
r3 = compute_cutfill(p)
lotb = next(z for z in r3["zones"] if z["name"] == "Lot B")
check(abs(lotb["area_sf"] - 240 * 300) < 2 and any("Lot B" in w and "outside the limit" in w for w in r3["warnings"]),
      f"a zone past the limit counts only inside it, and says so: {lotb['area_sf']} SF; {[w for w in r3['warnings'] if 'Lot B' in w]}")

# ---- 4. overlaps: a sliver is tracing noise, a real overlap is not itemized ------------------------
p.data["settings"]["zones"][1]["polygon"] = sq(250, 100, 300.5, 400)      # the street 0.5 ft into Lot B: 150 SF shared
p.data["settings"]["zones"][2]["polygon"] = sq(300, 100, 450, 400)
p.save()
r4 = compute_cutfill(p)
check(r4["zones_itemized"] and any("share 150 SF" in w for w in r4["warnings"]),
      f"a 150 SF sliver along a shared edge is itemized and counted once: {[w for w in r4['warnings'] if 'share' in w]}")
# the yards that choice decides are said: 150 SF x (1.5 ft - 0 ft) / 27 = 8.3 CY
check(any("share 150 SF" in w and "that choice moves up to 8 CY" in w for w in r4["warnings"]),
      f"the sliver note says how many yards the smaller-zone rule moves: {[w for w in r4['warnings'] if 'share' in w]}")
p.data["settings"]["zones"][1]["polygon"] = sq(200, 100, 300, 400)       # half the street is also Lot A: 15,000 SF
p.save()
r5 = compute_cutfill(p)
check(not r5["zones_itemized"] and any("overlap by" in w and "not itemized" in w for w in r5["warnings"])
      and "Site Cut" in [x["label"] for x in r5["takeoff"]["rows"]],
      f"zones overlapping for real are not itemized; the takeoff keeps one pair: {[w for w in r5['warnings'] if 'overlap' in w]}")

# ---- 4b. what the first critic broke (CRITIC.md 2026-09-28) ------------------------------------------
# a zone with a corner on the limit's line, lying outside there: the clip is an area plus a point
p.data["settings"]["region"] = sq(60, 60, 540, 540)
# (the zone leaves over the top edge and comes back to touch the right edge at one point from outside:
# zone x limit = a polygon AND a point)
touch = [[400, 400], [500, 400], [500, 560], [560, 560], [560, 500], [540, 480], [560, 460], [580, 460], [580, 590], [400, 590]]
p.data["settings"]["zones"] = [{"id": 1, "name": "Corner", "polygon": touch, "section_ft": 0.0}]
p.save()
r6 = compute_cutfill(p)
check(not r6.get("error") and r6["zones_itemized"] and abs(r6["zones"][0]["area_sf"] - 100 * 140) < 1,
      f"a zone whose clip is an area plus a point computes (it used to stop the whole compute): {r6.get('error') or r6['zones'][0]}")
# a zone drawn on another sheet is not read in this sheet's frame
p.data["settings"]["zones"] = [{"id": 1, "name": "Here", "polygon": sq(100, 100, 250, 400), "section_ft": 0.0, "page": key},
                               {"id": 2, "name": "Elsewhere", "polygon": sq(250, 100, 450, 400), "section_ft": 1.0, "page": "d0p1"}]
p.save()
r7 = compute_cutfill(p)
check([z["name"] for z in r7["zones"]] == ["Here"] and any("Elsewhere" in w and "left out" in w for w in r7["warnings"]),
      f"a zone drawn on another sheet is left out, and said: {[z['name'] for z in r7['zones']]}; {[w for w in r7['warnings'] if 'Elsewhere' in w]}")
# a zone left blank digs to the limit's layer stack; one typed 0 is finished grade
p.data["settings"]["section_ft"] = 1.0
p.data["settings"]["zones"] = [{"id": 1, "name": "Blank", "polygon": sq(100, 100, 250, 400), "section_ft": None, "page": key},
                               {"id": 2, "name": "Zero", "polygon": sq(250, 100, 400, 400), "section_ft": 0.0, "page": key}]
p.save()
r8 = compute_cutfill(p)
z8 = {z["name"]: z for z in r8["zones"]}
check(z8["Blank"]["section_ft"] == 1.0 and z8["Zero"]["section_ft"] == 0.0 and not z8["Blank"]["section_typed"] and z8["Zero"]["section_typed"],
      f"a blank zone takes the stack's 1 ft, a zone typed 0 is finished grade: {z8['Blank']['section_ft']} / {z8['Zero']['section_ft']}")
check(any("the layer stack lowers the proposed surface 1 ft in the rest of the limit and the zone left blank (Blank); "
          "Zero (finished grade) keeps its own depth" in w for w in r8["warnings"]),
      f"the stack's warning names where it applies and the zone that keeps its own depth: {[w for w in r8['warnings'] if 'stack' in w]}")
p.data["settings"]["section_ft"] = 0.0
# a pad inside a lot: the smaller zone wins its area, whatever order they were drawn in
for order in ((0, 1), (1, 0)):
    zz = [{"id": 1, "name": "Lot", "polygon": sq(100, 100, 400, 400), "section_ft": 0.0, "page": key},
          {"id": 2, "name": "Pad", "polygon": sq(200, 200, 260, 260), "section_ft": 2.0, "page": key}]
    p.data["settings"]["zones"] = [zz[k] for k in order]
    p.save()
    r9 = compute_cutfill(p)
    z9 = {z["name"]: z for z in r9["zones"]}
    check(r9["zones_itemized"] and abs(z9["Pad"]["area_sf"] - 3600) < 1 and abs(z9["Lot"]["area_sf"] - (90000 - 3600)) < 1
          and any("Pad" in w and "inside" in w for w in r9["warnings"]),
          f"a pad inside a lot is itemized at its own depth (drawn {'lot first' if order == (0, 1) else 'pad first'}): "
          f"pad {z9['Pad']['area_sf']} SF, lot {z9['Lot']['area_sf']} SF")
# a 5 ft band two zones share is an overlap, not a sliver (it once passed as 2 % of two big zones)
p.data["settings"]["zones"] = [{"id": 1, "name": "North", "polygon": sq(100, 100, 400, 255), "section_ft": 0.0, "page": key},
                               {"id": 2, "name": "South", "polygon": sq(100, 250, 400, 400), "section_ft": 1.0, "page": key}]
p.save()
r10 = compute_cutfill(p)
check(not r10["zones_itemized"] and any("overlap by 1,500 SF" in w for w in r10["warnings"]),
      f"a 5 ft x 300 ft band is an overlap: not itemized ({[w for w in r10['warnings'] if 'overlap' in w]})")
# two zones with one name file two rows, not one label twice
p.data["settings"]["zones"] = [{"id": 1, "name": "Lot", "polygon": sq(100, 100, 250, 400), "section_ft": 0.0, "page": key},
                               {"id": 2, "name": "Lot", "polygon": sq(250, 100, 400, 400), "section_ft": 0.0, "page": key}]
p.save()
r11 = compute_cutfill(p)
lab11 = [x["label"] for x in r11["takeoff"]["rows"] if x["kind"] in ("cut", "fill")]
check(len(lab11) == len(set(lab11)) and any(x.endswith("Lot (2)") for x in lab11), f"two zones named Lot file distinct rows: {lab11}")
# each cell's own cut / fill add up to the totals (the cell CSV reads them)
g11 = r11["grid"]
cell_cy = 5.0 * 5.0 / 27.0
check(abs(sum(g11["cutc"]) * cell_cy - r11["totals"]["cut_cy"]) < 0.5 and abs(sum(g11["fillc"]) * cell_cy - r11["totals"]["fill_cy"]) < 0.5,
      f"the cells' own cut / fill add up to the totals: {sum(g11['cutc']) * cell_cy:.1f} / {r11['totals']['cut_cy']}")

# ---- 4c. what the second critic broke (CRITIC-2.md 2026-09-28) ----------------------------------------
# a zone traced twice is not "a pad in a lot": the smaller would take the whole area without a word
p.data["settings"]["zones"] = [{"id": 1, "name": "A", "polygon": sq(100, 100, 250, 400), "section_ft": 0.0, "page": key},
                               {"id": 2, "name": "A again", "polygon": sq(101, 100, 250, 400), "section_ft": 1.0, "page": key}]
p.save()
r12 = compute_cutfill(p)
check(not r12["zones_itemized"] and any("zone 'A again' covers 99 % of zone 'A' (44,700 SF): that reads as one area drawn twice" in w
                                        and "is fine" not in w for w in r12["warnings"]),
      f"a zone traced twice is refused as one area drawn twice (never 'a zone inside another is fine'): "
      f"{[w for w in r12['warnings'] if 'twice' in w or 'overlap' in w]}")
# a 20 x 20 ft corner joined to a 0.5 ft strip along the shared edge averages 1.7 ft wide: still an overlap
bump = [[249.5, 100], [400, 100], [400, 400], [230, 400], [230, 380], [249.5, 380]]
p.data["settings"]["zones"] = [{"id": 1, "name": "West", "polygon": sq(100, 100, 250, 400), "section_ft": 0.0, "page": key},
                               {"id": 2, "name": "East", "polygon": bump, "section_ft": 1.0, "page": key}]
p.save()
r13 = compute_cutfill(p)
check(not r13["zones_itemized"] and any("'West' and 'East' overlap by 540 SF" in w for w in r13["warnings"]),
      f"a real corner overlap joined to a thin strip is an overlap: {[w for w in r13['warnings'] if 'overlap' in w or 'share' in w]}")
# a hand-traced edge that zig-zags across the other zone's edge (under a foot either way) is a sliver
zig = [[100, 100], [250, 100]] + [[250.5 if k % 2 == 0 else 249.5, 100 + 20 * k] for k in range(1, 15)] + [[250, 400], [100, 400]]
p.data["settings"]["zones"] = [{"id": 1, "name": "West", "polygon": zig, "section_ft": 0.0, "page": key},
                               {"id": 2, "name": "East", "polygon": sq(250, 100, 400, 400), "section_ft": 1.0, "page": key}]
p.save()
r14 = compute_cutfill(p)
check(r14["zones_itemized"] and any("the zones share" in w and "thin strips and small overlaps" in w for w in r14["warnings"]),
      f"a zig-zag tracing along a shared edge is itemized, its strips counted once: {[w for w in r14['warnings'] if 'share' in w]}")
# ... and the same zig-zag with the 20 ft corner on the east zone is not
p.data["settings"]["zones"][1]["polygon"] = [[250, 100], [400, 100], [400, 400], [230, 400], [230, 380], [250, 380]]
p.save()
r15 = compute_cutfill(p)
check(not r15["zones_itemized"] and any("'West' and 'East' overlap by" in w for w in r15["warnings"]),
      f"a zig-zag plus a 20 ft corner overlap is an overlap: {[w for w in r15['warnings'] if 'overlap' in w or 'share' in w]}")
# every zone typed: the stack is used nowhere, and says so
p.data["settings"]["region"] = None
p.data["settings"]["section_ft"] = 0.5
p.data["settings"]["zones"] = [{"id": 1, "name": "A", "polygon": sq(100, 100, 250, 400), "section_ft": 0.0, "page": key},
                               {"id": 2, "name": "B", "polygon": sq(250, 100, 400, 400), "section_ft": 1.0, "page": key}]
p.save()
r16 = compute_cutfill(p)
check(r16["zones_itemized"] and any("the layer stack (0.5 ft) is used nowhere: every zone has its own depth (A (finished grade), B (12 in))" in w
                                    for w in r16["warnings"]),
      f"a stack no zone uses says so: {[w for w in r16['warnings'] if 'stack' in w]}")
# every zone typed at the stack's own depth: no advice to clear a depth (it would change nothing)
p.data["settings"]["zones"][0]["section_ft"] = 0.5
p.data["settings"]["zones"][1]["section_ft"] = 0.5
p.save()
r16b = compute_cutfill(p)
check(any("every zone has its own depth (A (6 in), B (6 in)), the same as the layer stack (0.5 ft)" in w for w in r16b["warnings"])
      and not any("Clear a zone's depth" in w for w in r16b["warnings"]),
      f"zones typed at the stack's depth are said to be the stack's: {[w for w in r16b['warnings'] if 'stack' in w]}")
# the zones cannot be itemized (the grid fails): the job falls back to where both surfaces reach, the
# zones are reported beside it, and no note about the itemized zones is left standing
import cutfill.project as _cp  # noqa: E402
_grid = _cp.cut_fill_grid


def _no_zones(*a, **kw):
    if kw.get("zones"):
        raise RuntimeError("boom")
    return _grid(*a, **kw)


_cp.cut_fill_grid = _no_zones
p.data["settings"]["zones"] = [{"id": 1, "name": "Lot", "polygon": sq(100, 100, 400, 400), "section_ft": 0.0, "page": key},
                               {"id": 2, "name": "Pad", "polygon": sq(200, 200, 260, 260), "section_ft": 2.0, "page": key}]
p.save()
try:
    r17 = compute_cutfill(p)
finally:
    _cp.cut_fill_grid = _grid
check(not r17.get("error") and not r17["zones_itemized"] and r17["region_source"].startswith("overlap of the two surfaces")
      and abs(r17["totals"]["area_sf"] - 520 * 520) < 2 and any("could not be itemized (boom)" in w for w in r17["warnings"])
      and not any("inside zone" in w or "only the grading zones" in w or "layer stack lowers" in w for w in r17["warnings"])
      and all("cut_cy" in z for z in r17["zones"]),
      f"a failed itemize falls back to the surfaces' overlap and leaves no itemized note: {r17.get('region_source')}, "
      f"{r17['totals']['area_sf']} SF; {r17['warnings']}")
# zones at 0 under a layer stack: the cell CSV prints each cell's own depth and still adds up to the totals
from cutfill import server as cfs  # noqa: E402
import csv as _csv  # noqa: E402
import io as _io  # noqa: E402
p.data["settings"]["region"] = sq(60, 60, 540, 540)
p.data["settings"]["section_ft"] = 0.5
p.data["settings"]["zones"] = [{"id": 1, "name": "A", "polygon": sq(100, 100, 250, 400), "section_ft": 0.0, "page": key},
                               {"id": 2, "name": "B", "polygon": sq(250, 100, 400, 400), "section_ft": 0.0, "page": key}]
p.save()
r18 = compute_cutfill(p)
from cutfill.placement import inputs_sig  # noqa: E402
cfs._results["zones"] = dict(r18, inputs=inputs_sig(p))    # as the compute route keeps it
rows18 = list(_csv.reader(_io.StringIO(cfs.export_csv("zones").body.decode("utf-8"))))
cfs._results.pop("zones", None)
hi = next(i for i, rw in enumerate(rows18) if rw[:2] == ["row", "col"])
head18, cells18 = rows18[hi], [rw for rw in rows18[hi + 1:] if rw]
ic, ifl, izd = head18.index("cut_cy"), head18.index("fill_cy"), head18.index("zone_depth_ft")
c18 = sum(float(rw[ic]) for rw in cells18)
f18 = sum(float(rw[ifl]) for rw in cells18)
depths = {float(rw[izd]) for rw in cells18}
check(abs(c18 - r18["totals"]["cut_cy"]) < 0.5 and abs(f18 - r18["totals"]["fill_cy"]) < 0.5 and depths == {0.0, 0.5},
      f"zones at 0 under a 0.5 ft stack: the CSV's cells add up to the totals ({c18:.1f} / {r18['totals']['cut_cy']} cut, "
      f"{f18:.1f} / {r18['totals']['fill_cy']} fill) with each cell's own depth {sorted(depths)}")
p.data["settings"]["section_ft"] = 0.0
# two zones with one id would swap their map cells and cards: the API refuses them, and saves nothing
import asyncio  # noqa: E402

from fastapi import HTTPException  # noqa: E402


class _R:
    def __init__(self, b):
        self._b = b

    async def json(self):
        return self._b


cfs._projects["zones"] = p
before = json.dumps(p.data["settings"]["zones"])
try:
    asyncio.run(cfs.patch_settings("zones", _R({"section_ft": 2.0, "zones": [{"id": 3, "name": "A", "polygon": sq(0, 0, 9, 9)},
                                                                            {"id": 3, "name": "B", "polygon": sq(9, 0, 18, 9)}]})))
    refused = None
except HTTPException as e:
    refused = e
check(refused is not None and refused.status_code == 409 and "share the id 3" in str(refused.detail)
      and json.dumps(p.data["settings"]["zones"]) == before and p.data["settings"]["section_ft"] == 0.0,
      f"two zones with one id are refused and nothing else in the request is applied: {getattr(refused, 'detail', None)}")
cfs._projects.pop("zones", None)
# the two surfaces' outlines only touch along a line (no ground in common): zones with no limit must not
# crash the compute (the overlap is a line, which has no parts; CRITIC-3 P2-6)
pt = Project.create("touch", pid="touch")
pt.add_document(pdf, name="planes.pdf", copy=False)
pt.page_settings(key)["role"] = "both"
pt.data["settings"].update({"scale_ft_per_in": 72.0, "scale_source": "typed", "cell_ft": 5.0, "display_page": key, "region": None,
                            "zones": [{"id": 1, "name": "Z", "polygon": sq(100, 100, 500, 400), "section_ft": 0.0, "page": key}]})
tp = []
for y in range(40, 561, 40):
    for x, zz, side in ((40.0, 100.4, "existing"), (300.0, 103.0, "existing"), (300.0, 102.0, "proposed"), (560.0, 102.0, "proposed")):
        tp.append({"id": len(tp), "label_id": None, "x": x, "y": float(y), "z": zz, "side": side, "tag": "EG" if side == "existing" else "FG",
                   "anchor": "dot"})
os.makedirs(os.path.dirname(pt.analysis_path(key)), exist_ok=True)
json.dump(dict(an, spots=tp), open(pt.analysis_path(key), "w"))
pt.save()
try:
    rt = compute_cutfill(pt)
    crashed = None
except Exception as e:      # noqa: BLE001
    rt, crashed = {}, e
check(crashed is None and (rt.get("error") or rt.get("totals") is not None),
      f"surfaces that only touch along a line: no crash ({crashed!r}; {rt.get('error') or rt.get('totals', {}).get('cut_cy')})")

# ---- 5. the bid sheet files a zone's row on its line whatever the zone is called ------------------
lines = bidsheet.DEFAULT_LINES
cut_i = next(i for i, ln in enumerate(lines) if ln.get("name") == "Site Cut")
fill_i = next(i for i, ln in enumerate(lines) if ln.get("name") == "Site Fill")
i1, _ = bidsheet.line_for("Site Cut – Export pad", None, lines, kind="cut")
i2, _ = bidsheet.line_for("Site Fill – Haul road", None, lines, kind="fill")
i3, _ = bidsheet.line_for("Site Cut", None, lines, kind="cut")
check(i1 == cut_i and i2 == fill_i and i3 == cut_i,
      f"a zone's row files on Site Cut / Site Fill even when its name holds a veto word: {i1}, {i2} (lines {cut_i}, {fill_i})")
i4, _ = bidsheet.line_for("Site Cut - export haul", None, lines, kind="cut")
check(i4 != cut_i, f"a typed row with a hyphen keeps the Site Cut line's vetoes (only the engine's en dash is a zone row): {i4}")

print("\n" + (f"{fails} failure(s)" if fails else "all ok"))
sys.exit(1 if fails else 0)
