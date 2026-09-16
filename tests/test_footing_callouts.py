"""FOOTING CALLOUTS AND THE FOOTING SCHEDULE (2026-09-16; Beaumont call
2026-09-15 28:00-31:17, Jeff Ellis demo 2026-08-24; Wave 2 item 1).

Pure-function checks on words shaped like the sheets print them (Barn
S0-0.4 / S1-1.3 by IMEG, Ellis's Van Sickle notes sheet), then the same
reader on the real client sheets when they are on this machine (skipped,
and said so, when they are not - the files never enter the repo).

    python tests/test_footing_callouts.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from takeoff_engine.footing_callouts import (find_footing_callouts, join_schedule,  # noqa: E402
                                             mark_fallback, parse_ft_in, parse_mark,
                                             read_footing_schedule)

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


def W(x, y, text, w=None, h=6.0):
    w = w if w is not None else 4.0 * len(text)
    return (x, y, x + w, y + h, text)


# ---- feet-inch parsing --------------------------------------------------------------
for t, want in (("2'-6\"", 2.5), ("42\"", 3.5), ("(86'-0\")", 86.0), ("[87'-0\"]", 87.0), ("1'-0\"", 1.0),
                ("7'-6 1/2\"", 7.5417), ("(84'-2\")", 84.1667), ("10\"", 0.8333), ("[100'-9", 100.75)):
    got = parse_ft_in(t)
    check(got is not None and abs(got - want) < 0.001, f"parse_ft_in({t!r}) = {got} (want {want})")
check(parse_ft_in("CF2.0") is None and parse_ft_in("BOTTOM") is None and parse_ft_in("#5@12\"") is None, "marks, words and rebar are not dimensions")

# ---- marks --------------------------------------------------------------------------
check(parse_mark("CF3.0") == {"mark": "CF3.0", "prefix": "CF", "n1": 3.0, "n2": None, "unit": "ft"}, "CF3.0 parses as a continuous mark in feet")
check(parse_mark("F10.0x6.0")["n2"] == 6.0, "F10.0x6.0 carries both sides")
check(parse_mark("F-24")["unit"] == "in" and parse_mark("S-36")["unit"] == "in", "Ellis's F-24 / S-36 read as inches")
check(parse_mark("F5.5")["unit"] == "ft" and parse_mark("BOTTOM") is None and parse_mark("SW-1") is None, "F5.5 is feet; words and shear-wall marks are not footing marks")
fb = mark_fallback("CF3.0")
check(fb["width_ft"] == 3.0 and fb["kind"] == "continuous" and fb["from"] == "mark digits", "fallback from the digits: CF3.0 is 3 ft wide, and says it came from the mark")
check(mark_fallback("F-24")["width_ft"] == 2.0 and mark_fallback("S-36") == {"mark": "S-36", "width_ft": 3.0, "length_ft": 3.0, "depth_ft": None, "kind": "spread", "from": "mark digits"}, "F-24 is 2 ft wide, S-36 a 3 ft square pad")
check(mark_fallback("F10.0x6.0")["length_ft"] == 10.0 and mark_fallback("F10.0x6.0")["width_ft"] == 6.0, "a rectangular pad keeps both sides")

# ---- callouts on a plan -------------------------------------------------------------
words = [W(100, 100, "CF2.0"), W(100, 112, "(86'-0\")"), W(140, 100, "[87'-0\"]"),      # a wall: mark, BOF below, TOW beside
         W(300, 100, "CF3.0"), W(300, 112, "(83'-0\")"),                              # another wall
         W(300, 300, "CF3.0"), W(300, 312, "(83'-0\")"),
         W(500, 100, "F5.5"), W(500, 112, "(96'-0\")"),
         W(700, 100, "CF2.0"),                                                        # a mark with no elevation near it
         W(700, 400, "(84'-2\")"),                                                    # an elevation 300 pt away: not his
         W(900, 100, "EL"), W(912, 100, "(87'-0\")"), W(950, 100, "T.O."), W(970, 100, "SLAB")]  # a slab elevation
c = find_footing_callouts(words)
by = {(r["mark"], r["x"]): r for r in c["callouts"]}
check(by[("CF2.0", 110.0)]["bof_ft"] == 86.0 and by[("CF2.0", 110.0)]["tow_ft"] == 87.0, "CF2.0 takes the (86'-0\") below it as bottom of footing and the [87'-0\"] beside it as top of wall")
check(by[("CF3.0", 310.0)]["bof_ft"] == 83.0 and by[("CF3.0", 310.0)]["bof_text"] == "(83'-0\")", "CF3.0 pairs with (83'-0\")")
check(by[("CF2.0", 710.0)]["bof_ft"] is None, "a mark with no elevation within 60 pt stays unpaired - not guessed from 300 pt away")
check(c["slabs"] == [{"elev_ft": 87.0, "text": "(87'-0\")", "x": 928.0, "y": 103.0}], f"EL (87'-0\") T.O. SLAB is a slab elevation, not a footing's (got {c['slabs']})")
check(c["by_mark"]["CF3.0"] == {"count": 2, "bof": {"83": 2}, "unpaired": 0} and c["by_mark"]["CF2.0"]["count"] == 2 and c["by_mark"]["CF2.0"]["unpaired"] == 1, f"per-mark summary counts and elevations (got {c['by_mark']})")

# ---- the schedule, two sub-tables on one row band (Barn S0-0.4) ---------------------
sched = []
rows_f = [("F2.5", "2'-6\"", "2'-6\"", "1'-0\"", "(3)#5", "BOTTOM"), ("F3.0", "3'-0\"", "3'-0\"", "1'-0\"", "(3)#5", "BOTTOM"),
          ("F7.5", "7'-6\"", "7'-6\"", "1'-6\"", "(8)#5", "BOTTOM"), ("F10.0x6.0", "10'-0\"", "6'-0\"", "1'-4\"", "(8)#5", "BOTTOM")]
rows_cf = [("CF2.0", "2'-0\"", "1'-0\"", "(2)#5", "BOTTOM", "#5@16\""), ("CF3.0", "3'-0\"", "1'-0\"", "(3)#5", "BOTTOM", "#5@12\""),
           ("CF9.5", "9'-6\"", "1'-6\"", "(10)#5", "BOTTOM", "#6@9\"")]
for i, r in enumerate(rows_f):
    x = 1420.0
    for t in r:
        sched.append(W(x, 1094 + 13 * i, t)); x += 4 * len(t) + 10
for i, r in enumerate(rows_cf):
    x = 1720.0
    for t in r:
        sched.append(W(x, 1094 + 13 * i, t)); x += 4 * len(t) + 10
sched.append(W(1395, 1048, "FOOTING")); sched.append(W(1440, 1048, "SCHEDULE"))
s = read_footing_schedule(sched)
check(s["found"] and s["columns"] == 2, f"two mark columns found (got {s['columns']})")
r = s["rows"]
check(r["F3.0"]["kind"] == "spread" and r["F3.0"]["length_ft"] == 3.0 and r["F3.0"]["width_ft"] == 3.0 and r["F3.0"]["depth_ft"] == 1.0, f"F3.0 = 3 x 3 x 1'-0\" (got {r.get('F3.0')})")
check(r["F7.5"]["length_ft"] == 7.5 and r["F7.5"]["depth_ft"] == 1.5, "F7.5 = 7'-6\" square x 1'-6\"")
check(r["F10.0x6.0"]["length_ft"] == 10.0 and r["F10.0x6.0"]["width_ft"] == 6.0 and abs(r["F10.0x6.0"]["depth_ft"] - 1.3333) < 0.001, "the rectangular pad keeps length and width apart")
check(r["CF3.0"] and r["CF3.0"]["kind"] == "continuous" and r["CF3.0"]["width_ft"] == 3.0 and r["CF3.0"]["depth_ft"] == 1.0 and r["CF3.0"]["length_ft"] is None, f"CF3.0 = 3'-0\" wide x 1'-0\" continuous from the right-hand sub-table (got {r.get('CF3.0')})")
check(r["CF3.0"]["reinf"] == "(3)#5 BOTTOM #5@12\"" and r["F3.0"]["reinf"] == "(3)#5 BOTTOM", "reinforcing kept as printed")
check("F2.5" in r and r["F2.5"]["reinf"] == "(3)#5 BOTTOM", "the F row does not swallow the CF row beside it")
joined = join_schedule(c, s)
check(joined["CF3.0"]["schedule"]["from"] == "schedule" and joined["CF3.0"]["schedule"]["width_ft"] == 3.0, "a called-out mark carries its schedule row")
check(joined["F5.5"]["schedule"]["from"] == "mark digits" and joined["F5.5"]["schedule"]["width_ft"] == 5.5, "a mark the schedule does not list falls back to its digits, flagged")

# ---- Ellis-style inches -------------------------------------------------------------
ell = []
for i, r in enumerate((("F-20", "20\"", "10\"", "(2)", "#4", "CONT"), ("F-24", "24\"", "10\"", "(3)", "#4", "CONT"),
                       ("S-36", "36\"", "36\"", "10\"", "(4)", "#4", "BARS", "EACH", "WAY"), ("S-42", "42\"", "42\"", "10\"", "(5)", "#4", "BARS", "EACH", "WAY"))):
    x = 72.0
    for t in r:
        ell.append(W(x, 620 + 7 * i, t)); x += 4 * len(t) + 8
e = read_footing_schedule(ell)["rows"]
check(e["F-24"]["kind"] == "continuous" and abs(e["F-24"]["width_ft"] - 2.0) < 0.001 and abs(e["F-24"]["depth_ft"] - 0.8333) < 0.001, f"F-24 = 24 in wide x 10 in continuous (got {e.get('F-24')})")
check(e["S-42"]["kind"] == "spread" and abs(e["S-42"]["length_ft"] - 3.5) < 0.001 and e["S-42"]["reinf"].startswith("#4"), f"S-42 = 42 in square pad (got {e.get('S-42')})")
check(read_footing_schedule([W(10, 10, "CF2.0"), W(10, 40, "(86'-0\")")])["found"] is False, "one mark on a plan is not a schedule")

# ---- the real sheets, when present (client files stay local) -------------------------
B = Path(r"C:\Users\kobys\OneDrive\Desktop\past\P\Takeoff Automation\client_meet\beaumont")
barn = B / "SWR_Barn_Residence_-_Structural_4.7.26.pdf"
if barn.exists():
    import pymupdf
    from takeoff_engine.extract import page_words
    d = pymupdf.open(str(barn))
    s11 = read_footing_schedule(page_words(d[11]))
    r = s11["rows"]
    check(r.get("F3.0", {}).get("length_ft") == 3.0 and r["F3.0"]["depth_ft"] == 1.0, f"Barn S0-0.4: F3.0 = 3 x 3 x 1'-0\" ({r.get('F3.0')})")
    check(r.get("F7.5", {}).get("length_ft") == 7.5 and r["F7.5"]["depth_ft"] == 1.5, f"Barn S0-0.4: F7.5 = 7'-6\" square x 1'-6\" ({r.get('F7.5')})")
    check(r.get("CF2.0", {}).get("width_ft") == 2.0 and r["CF2.0"]["depth_ft"] == 1.0 and r["CF2.0"]["kind"] == "continuous", f"Barn S0-0.4: CF2.0 = 2'-0\" x 1'-0\" continuous ({r.get('CF2.0')})")
    check(r.get("CF9.5", {}).get("width_ft") == 9.5 and r["CF9.5"]["depth_ft"] == 1.5, f"Barn S0-0.4: CF9.5 = 9'-6\" x 1'-6\" ({r.get('CF9.5')})")
    check(r.get("F10.0x6.0", {}).get("length_ft") == 10.0 and r["F10.0x6.0"]["width_ft"] == 6.0 and abs(r["F10.0x6.0"]["depth_ft"] - 1.3333) < 0.001, f"Barn S0-0.4: F10.0x6.0 = 10 x 6 x 1'-4\", a wider mark 11 pt left of the column ({r.get('F10.0x6.0')})")
    c14 = find_footing_callouts(page_words(d[14]))
    bm = c14["by_mark"]
    check(bm.get("CF3.0", {}).get("count") == 4 and bm["CF3.0"]["bof"].get("83") == 2, f"Barn S1-1.3: CF3.0 x4, two of them at (83'-0\") ({bm.get('CF3.0')})")
    check(bm.get("CF2.0", {}).get("count") == 14 and bm["CF2.0"]["bof"].get("84.1667") == 3, f"Barn S1-1.3: CF2.0 x14, three at (84'-2\") ({bm.get('CF2.0')})")
    check(any(abs(sl["elev_ft"] - 87.0) < 0.001 for sl in c14["slabs"]), f"Barn S1-1.3: a T.O. SLAB at 87'-0\" ({c14['slabs'][:3]})")
    j = join_schedule(c14, s11)
    check(j["CF2.0"]["schedule"]["from"] == "schedule" and j["CF2.0"]["schedule"]["depth_ft"] == 1.0, "Barn: the plan's CF2.0 carries the S0-0.4 row")
else:
    print("skip: Barn structural set not on this machine (client file)")
ellis = B / "ellis_plan.pdf"
if ellis.exists():
    import pymupdf
    from takeoff_engine.extract import page_words
    d = pymupdf.open(str(ellis))
    e = read_footing_schedule(page_words(d[50]))["rows"]
    check(e.get("S-42", {}).get("kind") == "spread" and abs(e["S-42"]["length_ft"] - 3.5) < 0.001 and abs(e["S-42"]["depth_ft"] - 0.8333) < 0.001, f"Ellis p51: S-42 = 42 x 42 x 10 in ({e.get('S-42')})")
    check(e.get("F-24", {}).get("kind") == "continuous" and abs(e["F-24"]["width_ft"] - 2.0) < 0.001, f"Ellis p51: F-24 = 24 in continuous ({e.get('F-24')})")
    c43 = find_footing_callouts(page_words(d[42]))["by_mark"]
    check(c43.get("F-24", {}).get("count") == 15 and c43.get("S-36", {}).get("count") == 12, f"Ellis p43: F-24 x15, S-36 x12 ({ {k: v['count'] for k, v in c43.items()} })")
else:
    print("skip: Ellis set not on this machine (client file)")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all footing-callout checks passed")
