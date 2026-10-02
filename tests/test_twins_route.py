"""COUNT EACH WALL ONCE, the server (2026-09-17; Wave 2 item 11).

GET .../twins lists the families drawn as twin lines on a set that prints a
footing schedule; the footings route counts one run per footing in its
bottom-of-footing groups; approve takes count_once_style_ids, bills one run
for those families, stamps each element with onceFrac and says which rule
ran - while lengthPt stays as drawn.

A synthetic job: a 40 x 30 ft building at 0.1 ft/pt, its walls drawn as two
faces 0.75 ft apart and its footing as two dashed edges 2 ft apart, with a
CF2.0 (86'-0") callout on the page and a schedule that has a 2 ft footing.

    python tests/test_twins_route.py     (fails on the commit before it)
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = tempfile.mkdtemp(prefix="twr_")
os.environ["TAKEOFF_JOBS_DIR"] = JOBS
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="twr_lg_")

import pymupdf  # noqa: E402
import server  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


FPP = 0.1


def pt(ft):
    return ft / FPP


def rect(x0, y0, x1, y1):
    return [[pt(x0), pt(y0)], [pt(x1), pt(y0)], [pt(x1), pt(y1)], [pt(x0), pt(y1)], [pt(x0), pt(y0)]]


def plen(p):
    return sum(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in zip(p[:-1], p[1:]))


def make_job(jid, schedule_rows):
    pdf = Path(JOBS) / f"{jid}.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=612, height=792)
    pg.insert_text((pt(18), pt(-1.2) + 60), "CF2.0", fontsize=6)
    pg.insert_text((pt(18), pt(-1.2) + 67), "(86'-0\")", fontsize=6)
    doc.save(str(pdf)); doc.close()
    d = server._job_dir(jid)
    d.mkdir(parents=True, exist_ok=True)
    server.JOBS[jid] = {"path": str(pdf), "pages": 1, "status": {}, "owner": None}
    off = 60 * FPP                       # the drawing sits 60 pt down the page, under the callout

    def sh(poly):
        return [[x, y + 60] for x, y in poly]
    polys = {1: (5, "dash1", sh(rect(-1, -1, 41, 31))), 2: (5, "dash1", sh(rect(1, 1, 39, 29))),
             3: (6, "solid", sh(rect(0, 0, 40, 30))), 4: (6, "solid", sh(rect(0.75, 0.75, 39.25, 29.25)))}
    chains = [{"chain_id": cid, "class": cls, "style_id": sid, "length_pt": plen(p), "points": p,
               "color": [0, 0, 0], "width": 0.5}
              for cid, (sid, cls, p) in polys.items()]
    styles = [{"style_id": 5, "class": "dash1", "suggested_label": "footing", "length_pt": 0, "n": 2, "color": [0, 0, 0]},
              {"style_id": 6, "class": "solid", "suggested_label": "wall", "length_pt": 0, "n": 2, "color": [0, 0, 0]}]
    res = {"page": {"width_pt": 612, "height_pt": 792}, "scale": {"ft_per_paper_inch": FPP * 72, "text": "test"},
           "styles": styles, "chains": chains, "engine_sig": "t"}
    (d / "p0_result.json").write_text(json.dumps(res))
    # stamped like the reader writes it: an unstamped cache (from before the
    # 1-20 ft range check, 2026-09-26) is recomputed, not served
    (d / "footing_schedule.json").write_text(json.dumps(
        {"v": server._FOOTING_SCHEDULE_V, "rows": schedule_rows, "pages": [0], "scanned": 1, "rejected": []}))
    return d, off


d, _off = make_job("twr1", {"CF2.0": {"mark": "CF2.0", "width_ft": 2.0, "thickness_ft": 1.0, "page": 0}})
tw = json.loads(bytes(server.page_twins("twr1", 0).body))
fams = tw["families"]
check(set(fams) == {"5", "6"}, f"both twin families are offered ({sorted(fams)})")
check(fams["5"]["kind"] == "footing" and abs(fams["5"]["counted_ft"] - 148.0) < 1.0,
      f"the footing family: one run is its outer edge ({fams['5']['kind']}, {fams['5']['counted_ft']})")
check(fams["6"]["kind"] == "wall" and abs(fams["6"]["counted_ft"] - 140.0) < 1.0,
      f"the wall family: one run is its outside face ({fams['6']['kind']}, {fams['6']['counted_ft']})")
check("key" not in tw and tw["schedule_widths_ft"] == [2.0], "the route hides its cache key and names the widths it used")
check(fams["6"]["chains"]["4"]["c"] == 0 and fams["6"]["chains"]["4"]["skip"],
      "per line: drawn, counted and the stretches left out")
check((d / "p0_twins.json").exists(), "the pairing is cached beside the result")
mt = (d / "p0_twins.json").stat().st_mtime_ns
server.page_twins("twr1", 0)
check((d / "p0_twins.json").stat().st_mtime_ns == mt, "a second read is served from the cache")
time.sleep(0.02)
(d / "p0_result.json").write_text((d / "p0_result.json").read_text())
server.page_twins("twr1", 0)
check((d / "p0_twins.json").stat().st_mtime_ns != mt, "a re-detected page is paired again")

# the footing groups
fo = json.loads(bytes(server.footings("twr1", 0).body))
g = fo.get("groups") or {}
check(g.get("once") is True, f"the groups count one run ({fo.get('groups_reason')})")
tot = sum(r["lf_ft"] for r in g.get("groups") or [])
drawn = sum(r["lf_drawn_ft"] for r in g.get("groups") or [])
check(abs(tot - 148.0) < 2.0 and abs(drawn - 280.0) < 2.0, f"one run {tot} ft, as drawn {drawn} ft")

# approve, without and with count-once


def items(once):
    r = server.approve("twr1", 0, server.ApproveBody(scale_seen=server._scale_stamp("twr1", 0), labels={5: "FOOTING", 6: "WALL"}, count_once_style_ids=once))
    j = json.loads(bytes(r.body)) if hasattr(r, "body") else r
    exp = json.loads(Path(j["file"]).read_text()) if j.get("file") else {}
    return {x["label"]: x for x in j["items"]}, exp


it0, exp0 = items([])
it1, exp1 = items([5, 6])
check(abs(it0["FOOTING"]["length_ft"] - 280.0) < 1 and abs(it0["WALL"]["length_ft"] - 274.0) < 1,
      "as drawn, the itemization bills both faces and both edges")
check(abs(it1["FOOTING"]["length_ft"] - 148.0) < 1 and abs(it1["WALL"]["length_ft"] - 140.0) < 1,
      f"counted once it bills one run each ({it1['FOOTING']['length_ft']}, {it1['WALL']['length_ft']})")
check(it1["WALL"]["count"] == it0["WALL"]["count"] == 2, "the line count is unchanged - no line was deleted")
els = {e["chainId"] if "chainId" in e else e.get("chain_id"): e for e in exp1.get("elements", [])}
fr = sorted(e.get("onceFrac") for e in exp1.get("elements", []) if "onceFrac" in e)
check(fr and fr[0] == 0.0 and fr[-1] == 1.0, f"each element carries its one-run share ({fr})")
check(all("onceFrac" not in e for e in exp0.get("elements", [])), "nothing is stamped when nothing is counted once")
check(sorted(e["lengthPt"] for e in exp1["elements"]) == sorted(e["lengthPt"] for e in exp0["elements"]),
      "lengthPt stays as drawn - the share rides beside it")
co = exp1.get("countOnce") or {}
check(set(co) == {"5", "6"} and co["6"]["rule"].startswith("wall thickness") and co["5"]["countedFt"] == fams["5"]["counted_ft"],
      "the export says which families were counted once and by which rule")
it2, _ = items([99])
check(abs(it2["WALL"]["length_ft"] - 274.0) < 1, "an id that is not a twin family changes nothing")

# a set with no footing schedule offers nothing
make_job("twr2", {})
tw2 = json.loads(bytes(server.page_twins("twr2", 0).body))
check(tw2["families"] == {} and "no footing schedule" in tw2["reason"], f"no schedule, no switch ({tw2['reason']})")
it3 = server.approve("twr2", 0, server.ApproveBody(scale_seen=server._scale_stamp("twr2", 0), labels={6: "WALL"}, count_once_style_ids=[6]))
j3 = json.loads(bytes(it3.body))
check(abs({x["label"]: x for x in j3["items"]}["WALL"]["length_ft"] - 274.0) < 1,
      "count-once asked for on such a set bills as drawn")
try:
    server.page_twins("nope", 0)
    check(False, "an unknown job must 404")
except Exception as e:
    check(getattr(e, "status_code", None) == 404, "an unknown job is a 404")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all twins-route checks passed")
