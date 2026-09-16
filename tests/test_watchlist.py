"""WATCH-LIST CALLOUTS (2026-09-16; Wave 2 item 7; Beaumont: "so many call
outs everywhere" - a keyword list with the tank size read off the callout,
septic and propane apart). Blocks not lines (a wrapped callout), notes /
legend keys / numbered notes never count, sizes in gallons and inches, a
2-WAY qualifier, BY OTHERS / N.I.C. / RE. TO tags, in_scope apart from
count; the route on a fabricated sheet and on Barn Civil C2.0 when present.

    python tests/test_watchlist.py     (fails on 1dcded5)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = tempfile.mkdtemp(prefix="wl_")
os.environ["TAKEOFF_JOBS_DIR"] = JOBS
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="wl_lg_")

import pymupdf  # noqa: E402
import server  # noqa: E402
from takeoff_engine.watchlist import WATCH, _blocks, find_watch_callouts  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


def L(x0, y0, text, h=9.1, w=None):
    return (x0, y0, x0 + (w or 4.6 * len(text)), y0 + h, text)


# ---- blocks: a wrapped callout is one block, a note paragraph is not ------------------
lines = [
    L(2140.0, 249.5, "PROPOSED 1,500 GALLON"), L(2140.4, 259.1, "SEPTIC TANK; RE. TO OWTS"),
    L(1267.0, 1703.8, "PROPOSED 18,000 GAL"), L(1267.0, 1713.5, "BURIED PROPANE TANK;"),
    L(900.0, 400.0, "18\" CMP CULVERT"),
    L(500.0, 600.0, "INSTALL CLEANOUT & RE. TO MEP"), L(700.0, 600.0, "2-WAY CLEANOUT"),
    L(300.0, 700.0, "AREA INLET"), L(300.0, 730.0, "AREA INLET"), L(300.0, 760.0, "INSTALL WYE"),
    L(1100.0, 900.0, "PROPOSED BURIED PROPANE TANK BY OTHERS"),
    L(2016.0, 1716.0, "3.", h=8), L(2032.0, 1716.0, "UTILITY SERVICES, EXCLUDING WATER & SEWER, MAY BE INSTALLED IN COMBINED", h=8),
    L(2016.0, 1800.0, "12. ALL SITE RETAINING WALLS BY OTHERS, REFER TO STRUCTURAL PLANS.", h=8),
    L(100.0, 100.0, "C/O = CLEANOUT"),
    L(400.0, 1000.0, "PROVIDE CLEANOUTS AT ALL DOWNSPOUT LOCATIONS THAT ARE ROUTED TO UNDERGROUND", h=8),
    L(400.0, 1010.0, "STORM PIPE. FINAL LOCATION FOR ALL CLEANOUTS TO BE COORDINATED WITH ARCHITECT,", h=8),
    L(400.0, 1020.0, "NDS AREA INLETS: 6\" MIN. UNLESS OTHERWISE NOTED W/ POLISHED BRASS GRATES", h=8),
    L(1500.0, 300.0, "GREASE INTERCEPTOR N.I.C."),
    L(2000.0, 500.0, "SEPTIC TANK"), L(2000.0, 500.4, "SEPTIC TANK"),       # printed twice on top of itself
    L(600.0, 800.0, "7'-2\" AREA INLET"),                                     # a dimension, not a 2\" inlet (Barn C2.0)
    L(800.0, 1100.0, "FOR ALL CLEANOUTS PRIOR TO INSTALLATION."),           # the last line of a wrapped note (Barn C2.0)
    L(800.0, 1150.0, "CLEANOUTS SHALL BE BROUGHT TO GRADE"),
]
blocks = _blocks(lines)
texts = [" ".join(l[4] for l in b) for b in blocks]
check("PROPOSED 1,500 GALLON SEPTIC TANK; RE. TO OWTS" in texts, "a callout wrapped over two lines at one left edge is one block")
check("PROPOSED 18,000 GAL BURIED PROPANE TANK;" in texts, "the propane tank's two lines too")
check(not any(len(t) > 200 for t in texts) and not any("PROVIDE CLEANOUTS" in t for t in texts), "a long note line never enters a block")

r = find_watch_callouts(lines)
by = {g["kind"]: g for g in r["groups"]}
check([w["label"] for w in WATCH] == r["watch"] == ["Septic Tank", "Propane Tank", "Culvert", "Area Inlet", "Cleanout", "Wye"], "the six watch words, in order")
check(by["septic_tank"]["count"] == 2 and by["septic_tank"]["sizes"] == {"1,500 gal": 1} and by["septic_tank"]["tags"] == {"RE. TO OWTS": 1}, f"septic: the wrapped callout reads 1,500 gal with its RE. TO OWTS tag; the overprinted one counts once (got {by['septic_tank']})")
check(by["propane_tank"]["count"] == 2 and by["propane_tank"]["sizes"] == {"18,000 gal": 1} and by["propane_tank"]["tags"] == {"by others": 1} and by["propane_tank"]["in_scope"] == 1, f"propane: 18,000 gal, and the BY OTHERS one is counted but out of scope (got {by['propane_tank']})")
check(by["culvert"]["count"] == 1 and by["culvert"]["sizes"] == {'18"': 1}, f"an 18\" culvert (got {by['culvert']})")
check(by["cleanout"]["count"] == 2 and by["cleanout"]["sizes"] == {"2-way": 1} and by["cleanout"]["tags"] == {"RE. TO MEP": 1}, f"cleanouts: the 2-WAY qualifier and RE. TO MEP; the legend key and the note never count (got {by['cleanout']})")
check(by["area_inlet"]["count"] == 3 and by["area_inlet"]["sizes"] == {} and by["wye"]["count"] == 1, f"three area inlets (the 2\" of a 7'-2\" dimension is not a size) and a wye, the NDS note left out (got {by['area_inlet']})")
check(not any("PRIOR TO" in it["text"] or "SHALL" in it["text"] for it in r["items"]), "a wrapped note's fragment and spec language (SHALL) never count")
check("grease" not in by and all(it["kind"] != "cleanout" or "N.I.C." not in it["tags"] for it in r["items"]), "N.I.C. is a tag, not a watch word")
it = [x for x in r["items"] if x["text"].startswith("PROPOSED 1,500")][0]
check(it["x"] == 2140.0 and it["y"] == 249.5 and it["lines"] == 2 and it["size"] == 1500 and it["size_unit"] == "gal", "an item carries its position (top-left of the block), line count and size")
check(find_watch_callouts([]) == {"items": [], "groups": [], "watch": r["watch"]}, "no lines: nothing, the watch words still named")
check(find_watch_callouts([L(0, 0, "RE. TO MEP PLANS FOR CLEANOUT")])["items"][0]["tags"] == ["RE. TO MEP"], "RE. TO stops before PLANS / FOR")

# ---- the route ------------------------------------------------------------------------------
pdf = Path(JOBS) / "civil.pdf"
doc = pymupdf.open()
pg = doc.new_page(width=612, height=792)
pg.insert_text((300, 100), "PROPOSED 1,500 GALLON", fontsize=8)
pg.insert_text((300, 110), "SEPTIC TANK; RE. TO OWTS", fontsize=8)
pg.insert_text((100, 300), "18\" CMP CULVERT", fontsize=8)
pg.insert_text((100, 500), "C/O = CLEANOUT", fontsize=8)
doc.save(str(pdf)); doc.close()
jid = "wlroute1"
server._job_dir(jid).mkdir(parents=True, exist_ok=True)
server.JOBS[jid] = {"path": str(pdf), "pages": 1, "status": {}, "owner": None}
j = json.loads(bytes(server.watchlist(jid, 0).body))
g = {x["kind"]: x for x in j["groups"]}
check(set(g) == {"septic_tank", "culvert"} and g["septic_tank"]["sizes"] == {"1,500 gal": 1} and g["culvert"]["sizes"] == {'18"': 1}, f"the route reads the fabricated sheet: septic 1,500 gal and an 18\" culvert, the legend key ignored (got {g})")
for bad, want in (("nosuchjob", 404), (jid, 404)):
    try:
        server.watchlist(bad, 0 if bad != jid else 7)
        check(False, f"{bad}: must 404")
    except Exception as e:
        check(getattr(e, "status_code", None) == want, f"{'unknown job' if bad != jid else 'page past the end'} -> 404")

# ---- Barn Civil C2.0 when the client set is present (never committed) --------------------
barn = Path(r"C:\Users\kobys\OneDrive\Desktop\past\P\Takeoff Automation\client_meet\beaumont\SWR_Barn_Residence_-_Civil_4.7.26.pdf")
if barn.exists():
    from takeoff_engine.extract import page_lines
    d = pymupdf.open(str(barn))
    r1 = find_watch_callouts(page_lines(d[1]))
    g1 = {x["kind"]: x for x in r1["groups"]}
    check("septic_tank" in g1 and g1["septic_tank"]["sizes"].get("1,500 gal") == 1, f"Barn C2.0: the 1,500 gal septic tank (got {g1.get('septic_tank')})")
    check("propane_tank" in g1 and g1["propane_tank"]["sizes"].get("18,000 gal") == 1, f"Barn C2.0: the 18,000 gal buried propane tank (got {g1.get('propane_tank')})")
    r9 = find_watch_callouts(page_lines(d[9]))
    g9 = {x["kind"]: x for x in r9["groups"]}
    check(g9.get("area_inlet", {}).get("count", 0) >= 15 and g9.get("wye", {}).get("count", 0) >= 10, f"Barn C3.5: area inlets and wyes counted per callout (got inlets {g9.get('area_inlet', {}).get('count')}, wyes {g9.get('wye', {}).get('count')})")
    check(not any("PROVIDE CLEANOUTS" in it["text"] for it in r9["items"]), "Barn C3.5: the cleanout note is not a callout")
    d.close()
else:
    print("skip: Barn Civil set not present (client material stays local)")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all watchlist checks passed")
