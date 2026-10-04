"""Edge behaviour from the 2026-09-26 audit (PLAN.md section 12):

  * a damaged PDF upload answers 400 and leaves NO orphan in the store; a
    file that is not a PDF by content is refused the same way (D35). The
    guard is _pdf_or_400 and it runs BEFORE the upload writes a byte -
    pinned on the route's own source, since fastapi.testclient is not
    usable in this venv (see tests/test_calcstate.py);
  * the report's 16 pt titles wrap inside the 512 pt text width of a
    Letter page (D47): the audit measured a title ending at x = 734.7 on a
    612 pt page;
  * the room pick's scale helper takes the scale on screen first, the
    reviewer's recorded pick second, the engine's reading third, and an
    inset caption only where a title-bar rule spans the point (D09).

Dependency-free like tests/test_gate.py:  python tests/test_upload_report_edges.py
"""

import inspect
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="edges_test_")

import pymupdf  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import server  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails += 1


def status_of(fn, *a):
    try:
        fn(*a)
    except HTTPException as e:
        return e.status_code, e.detail
    return None, None


print("upload guard")
good = pymupdf.open()
good.new_page(width=612, height=792)
good_bytes = good.tobytes()
good.close()
check(status_of(server._pdf_or_400, good_bytes, "plans.pdf") == (None, None), "a real one-page PDF passes")
st, why = status_of(server._pdf_or_400, b"hello, this is a text file\n", "not-a-plan.txt")
check(st == 400 and "not a PDF" in why and "not-a-plan.txt" in why, "a text file is refused by CONTENT with 400, naming the file: %r" % why)
st, why = status_of(server._pdf_or_400, b"hello", "not-a-plan.pdf")
check(st == 400, "...whatever its name says")
damaged = good_bytes[: len(good_bytes) // 2]
st, why = status_of(server._pdf_or_400, damaged, "smoke-damaged.pdf")
check(st == 400 and "damaged" in why, "a PDF cut in half is refused with 400 and says it is damaged: %r" % why)
st, why = status_of(server._pdf_or_400, b"", "empty.pdf")
check(st == 400, "an empty body is refused")
src = inspect.getsource(server.upload)
check("_pdf_or_400(raw, file.filename)" in src and src.index("_pdf_or_400(") < src.index("dest.write_bytes(raw)"),
      "the route refuses BEFORE it writes: no orphan upload_<hash>_ file for a refused upload")
store = Path(os.environ["TAKEOFF_JOBS_DIR"])
check(not list(store.glob("upload_*")), "the guard itself writes nothing to the store")

print("report titles wrap")
lines = server._report_title_lines("Sheet C3.1 (page 4) - Itemized takeoff")
check(lines == ["Sheet C3.1 (page 4) - Itemized takeoff"], "a short title is one line, unchanged")
long = "upload_784c5659b4b9_Scanned Take-off by Floor Plan with a very long export name from the drawing program.pdf - Takeoff summary"
lines = server._report_title_lines(long)
check(len(lines) >= 2 and " ".join(lines) == long, "a long title wraps on word boundaries and loses nothing: %d lines" % len(lines))
check(all(pymupdf.get_text_length(ln, fontsize=16) <= server._REPORT_TEXT_W for ln in lines),
      "every line fits the 512 pt text width (x from 50 to 562 on a 612 pt page)")
word = "A" * 120
lines = server._report_title_lines(word)
check(len(lines) >= 2 and "".join(lines) == word
      and all(pymupdf.get_text_length(ln, fontsize=16) <= server._REPORT_TEXT_W for ln in lines),
      "one word wider than the page is cut by characters rather than left running off it")
check(server._report_title_lines("") == [""], "an empty title is one empty line")

print("room pick scale order")
body = server.RoomPickBody(x=100, y=100, scale_ft_per_in=8)
res = {"scale": {"ft_per_paper_inch": 4.0}}
job = {"path": "nope.pdf", "mtime": 0}
# no sites on disk for a job that has no PDF: the site pass answers []
# THE SERVER'S SCALE DECIDES (CRITIC-scale-3 MINOR 2): a scale on screen that is not the one in force is refused
try:
    server._room_pick_scale(job, "no-such-job", 0, res, body)
    got = "not refused"
except server.HTTPException as e:
    got = e.status_code
check(got == 409, "a scale sent that is not the sheet's in force (8 vs 4) is refused: %s" % got)
body2 = server.RoomPickBody(x=100, y=100)
ft, by = server._room_pick_scale(job, "no-such-job", 0, res, body2)
check(ft == 4.0 and by == "engine", "with nothing on screen and no recorded pick, the engine's reading: %s %s" % (ft, by))
body3 = server.RoomPickBody(x=100, y=100, scale_ft_per_in=1e9)
try:
    server._room_pick_scale(job, "no-such-job", 0, res, body3)
    got = "not refused"
except server.HTTPException as e:
    got = e.status_code
check(got == 409, "an out-of-band on-screen number is refused out loud, never substituted silently: %s" % got)

sites = [{"fpi": 8.0, "text": "SCALE: 1/8\" = 1'-0\"", "box": [146.8, 1206.0, 221.8, 1215.7],
          "rule": [138.4, 540.4, 1203.1], "compound": False},
         {"fpi": 4.0, "text": "SCALE: 1/4\" = 1'-0\"", "box": [516.9, 1677.7, 592.1, 1687.4],
          "rule": [508.6, 2078.9, 1674.8], "compound": False}]
s = server._site_scale_at(sites, 260.2, 740.3)
check(s is not None and s["fpi"] == 8.0, "a point above the inset's title bar, inside its span, belongs to the inset")
s = server._site_scale_at(sites, 838.1, 550.7)
check(s is not None and s["fpi"] == 4.0, "a point BESIDE the inset's bar (the RV garage) is the main plan's, not the nearer bar's")
s = server._site_scale_at(sites, 300.0, 1300.0)
check(s is None, "a point below every title bar belongs to no view")
s = server._site_scale_at([dict(sites[0], compound=True)], 260.2, 740.3)
check(s is None, "a compound caption (one line, two values) decides nothing")

print("the scale of a room on a sheet that prints several (synthetic sheets)")
V = server._room_view_scale
def site(fpi, text, x0, x1, y):
    return {"fpi": fpi, "text": text, "box": [x0, y + 2, x0 + 80, y + 12], "rule": [x0, x1, y], "compound": False}
# one printed scale: nothing is decided here
one = [site(4.0, "A 1/4", 100, 1500, 1500)]
check(V(one, 700, 700)["multi"] is False, "a sheet that prints one scale is left to the sheet's scale")
# a WIDE main plan (bar spans the room, nothing nearer): its view - and, on a sheet that prints several
# scales, flagged until the estimator confirms it once (ruling L: views can not be told apart reliably yet)
wide = [site(4.0, "PLAN 1/4", 100, 1500, 1500), site(8.0, "KEY 1/8", 1700, 2000, 1500)]
v = V(wide, 700, 700)
check(v["multi"] and v["site"]["fpi"] == 4.0 and v["check"] and "confirm" in v["why"], "a room over the bar that spans it, nearest below: its view, to be confirmed once")
# a plan with a SHORT caption off to the left and an elevation at 1/2" printed under the plan, spanning the room
ti = [site(4.0, "PLAN 1/4", 300, 520, 1200), site(2.0, "ELEV 1/2", 640, 1080, 1600)]
v = V(ti, 1060, 1000)
check(v["site"]["fpi"] == 4.0 and v["check"] and "nearest view title below" in v["why"] and "inference" in v["why"],
      "plan above its own short caption, elevation's bar spanning it further down: the plan's 1/4 (nearest), FLAGGED")
v = V(ti, 900, 1400)
check(v["site"]["fpi"] == 2.0 and v["check"], "a point inside the elevation itself takes the elevation's caption (to be confirmed once)")
# an ENLARGED plan at 3/16" whose short caption spans none of its rooms, a key plan at 1/16" lower down,
# the sheet read at 1/16": the room above the 3/16" caption is 3/16" (9x the area at 1/16"), flagged
ex = [site(16.0, "KEY 1/16", 100, 280, 1600), site(5.3333, "PLAN 3/16", 100, 500, 700)]
v = V(ex, 1400, 600)
check(v["site"]["fpi"] == 5.3333 and v["check"], "a room in the enlarged plan: 3/16 (the nearest title below), flagged")
v = V(ex, 1000, 1300)
check(v["site"]["fpi"] == 16.0, "a room in the key plan (below the 3/16 caption): 1/16")
# the 16x case: enlarged plan at 1/2" (short caption, left), a 1/4" view's bar below spanning, sheet 1/8"
sx = [site(2.0, "ENL 1/2", 300, 520, 1200), site(4.0, "VIEW 1/4", 640, 1080, 1600), site(8.0, "SHEET 1/8", 1800, 2300, 1650)]
v = V(sx, 1060, 1000)
check(v["site"]["fpi"] == 2.0 and v["check"], "the enlarged plan's room: 1/2 (not the sheet's 1/8, 16x), flagged")
# the mirror case (a house set's RV garage): spanned by the main plan's bar, an inset's bar nearer.
# The bars alone cannot tell it from the case above, so it is FLAGGED with the main plan one click away.
hs = [site(8.0, "INSET 1/8", 138, 540, 1203), site(4.0, "PLAN 1/4", 508, 2079, 1675)]
v = V(hs, 838.1, 550.7)
check(v["check"] and {c["fpi"] for c in v["choices"]} == {8.0, 4.0},
      "a room beside an inset, spanned by the main plan's bar: flagged, both printed scales offered")
v = V(hs, 260.2, 740.3)
check(v["site"]["fpi"] == 8.0 and v["check"], "a room inside the inset, over its own bar: the inset's 1/8 (to be confirmed once)")
# THE ROOM'S EXTENT, not the click (ruling L; critic 2, M3): the title below whose bar overlaps the room most
#   the plan at 1/4 (bar x 100-700 at y 1500), a storefront elevation at 1/2 printed BELOW the plan's right
#   part (bar x 650-1400 at y 1600); a room x 300-800 above both: it overlaps the plan's bar by 400, the
#   elevation's by 150 - the plan's, contested by the elevation
sf = [site(4.0, "PLAN 1/4", 100, 700, 1500), site(2.0, "ELEV 1/2", 650, 1400, 1600)]
v = V(sf, 1000, 1200, [300, 900, 800, 1400])          # the click is over the elevation's bar...
check(v["site"]["fpi"] == 4.0 and v["check"] and "contested" not in v["why"],
      "...but the room's outline is nearest the plan's bar (100 pt vs 200): 1/4, flagged until confirmed: %s" % v["why"])
v = V(sf, 1000, 1200, [900, 900, 1300, 1400])
check(v["site"]["fpi"] == 2.0 and "contested" in v["why"] and v.get("contested", {}).get("fpi") == 4.0,
      "a room over the elevation's bar, the plan's bar 224 pt off (within 20 pct of 200): 1/2 by distance, both below - contested: %s" % v["why"])
# THE CAPTION BESIDE THE PLAN (an existing-conditions sheet): the plan's 3/16 title printed to the LEFT
# of its rooms at mid-height, a key plan's 1/16 title far below. Nearest in any direction: 3/16.
bs = [site(5.3333, "PLAN 3/16", 120, 510, 730), site(16.0, "KEY 1/16", 120, 300, 1670)]
v = V(bs, 1700, 760, [1340, 700, 2090, 840])
check(v["site"]["fpi"] == 5.3333 and v["check"] and "nearest" in v["why"] and "contested" not in v["why"],
      "a room beside its plan's caption (830 pt left) with a key's caption 1330 pt off: 3/16, flagged: %s" % v["why"])
# two titles about equally near (within 20 pct): the one BELOW the room wins, still flagged
eq = [site(5.3333, "PLAN 3/16", 100, 300, 600), site(4.0, "VIEW 1/4", 500, 900, 1110)]
v = V(eq, 700, 600, [500, 500, 900, 1000])     # 1/4 bar 110 below; 3/16 bar 200 to the left (110 x 1.2 < 200: not near)
check(v["site"]["fpi"] == 4.0, "110 pt below vs 200 pt beside: the nearest, below: %s" % v["why"])
eq2 = [site(5.3333, "PLAN 3/16", 100, 395, 600), site(4.0, "VIEW 1/4", 500, 900, 1110)]
v = V(eq2, 700, 600, [500, 500, 900, 1000])    # 3/16 bar 105 to the left, 1/4 bar 110 below: within 20 pct
check(v["site"]["fpi"] == 4.0 and "below it" in v["why"] and "contested" not in v["why"] and v["check"],
      "105 pt beside vs 110 pt below: equally near, the one below wins, flagged: %s" % v["why"])
eq3 = [site(5.3333, "PLAN 3/16", 100, 395, 600), site(4.0, "VIEW 1/4", 950, 1300, 700)]
v = V(eq3, 700, 600, [500, 500, 900, 1000])    # 105 left, 50 right: not within 20 pct - nearest (1/4)
check(v["site"]["fpi"] == 4.0 and "contested" not in v["why"], "50 pt vs 105 pt, neither below: the nearest: %s" % v["why"])
eq4 = [site(5.3333, "PLAN 3/16", 100, 395, 600), site(4.0, "VIEW 1/4", 1000, 1300, 700)]
v = V(eq4, 700, 600, [500, 500, 900, 1000])    # 105 left, 100 right, neither below: contested
check("contested" in v["why"] and "neither is below" in v["why"] and v["check"] and v["site"]["fpi"] == 4.0,
      "100 pt vs 105 pt, neither below: contested, said: %s" % v["why"])
# A ROOM IS A PLAN-VIEW OBJECT (ruling V; critic 3, M1). The kitchen sheet as measured: FLOOR PLAN at 1/4 (bar
# x 357-579 at y 1235) and STOREFRONT ELEVATION at 1/2 (bar x 665-1102 at y 1669); the customer area x 464-1423,
# y 955-1138. By distance the elevation could win a room near it; by title the plan is the only room view.
def tsite(fpi, text, x0, x1, y, title):
    d = site(fpi, text, x0, x1, y)
    d["title"] = title
    return d
tc = [tsite(4.0, "SCALE: 1/4", 357, 579, 1235, "FLOOR PLAN"), tsite(2.0, "SCALE: 1/2", 665, 1102, 1669, "STOREFRONT ELEVATION")]
v = V(tc, 1300, 1010, [464, 955, 1423, 1138])
check(v["site"]["fpi"] == 4.0 and v["check"] and "PLAN" in v["why"] and {c["fpi"] for c in v["choices"]} == {4.0, 2.0},
      "the kitchen's customer area: 1/4 (the only PLAN), the elevation's 1/2 still a choice, flagged: %s" % v["why"])
v = V(tc, 900, 1500, [700, 1400, 1100, 1600])  # a room right over the elevation's bar
check(v["site"]["fpi"] == 4.0, "even a room over the elevation's bar defaults to the plan: an elevation is never a room's default")
# EX-1 as measured: KEY PLAN 1/16 (x 122-297 at y 1668) and EXISTING FLOOR PLAN 3/16 (x 122-514 at y 730), the
# customer area x 1340-2092, y 698-840: both say PLAN, the nearest is the 3/16
ex1 = [tsite(16.0, "SCALE: 1/16", 122, 297, 1668, "KEY PLAN"), tsite(5.3333, "SCALE: 3/16", 122, 514, 730, "EXISTING FLOOR PLAN")]
v = V(ex1, 1700, 760, [1340, 698, 2092, 840])
check(v["site"]["fpi"] == 5.3333 and v["check"], "EX-1's customer area: both titles say PLAN, the nearest (3/16): %s" % v["why"])
# only elevations / sections: no default at all, the estimator chooses
el = [tsite(2.0, "SCALE: 1/2", 100, 400, 800, "NORTH ELEVATION"), tsite(1.0, "SCALE: 1", 500, 700, 800, "WALL SECTION")]
v = V(el, 300, 700, [200, 600, 400, 780])
check(v["site"] is None and v["check"] and "choose" in v["why"], "a sheet of elevations and sections: no default, choose: %s" % v["why"])
# a caption printed NOT TO SCALE (Belle A1.00: "1/8" = 1'-0"" with "NOT TO SCALE" over it, under a FLOOR PLAN title;
# the sheet's other caption, untitled, reads 1/4", and the plan's dimensions agree with 1/4"): never the default
be = [dict(tsite(8.0, "1/8", 210, 415, 1567, "ARCHITECTURAL FLOOR PLAN"), nts=True), tsite(4.0, "Scale: 1/4", 1963, 2037, 1590, "")]
v = V(be, 1850, 375, [1700, 270, 1960, 420])
check(v["site"]["fpi"] == 4.0 and v["check"] and {c["fpi"] for c in v["choices"]} == {8.0, 4.0},
      "a NOT TO SCALE caption is never a room's default, even titled PLAN; it stays a choice: %s" % v["why"])
v = V(sx, 400, 1000, [300, 900, 520, 1150])   # the 16x sheet by outline: the 1/2 title 50 pt below it
check(v["site"]["fpi"] == 2.0 and v["check"], "the 16x sheet's enlarged-plan room by its outline: 1/2, flagged: %s" % v["why"])
v = V(sf, 2000, 600, [1900, 500, 2100, 700])
check(v["check"] and v["site"] is not None and "nearest" in v["why"], "no bar under the room's width: the nearest title, flagged: %s" % v["why"])
check(V(hs, 300, 1720)["check"] and V(hs, 300, 1720)["why"].startswith("no view title"), "below every title: flagged, said")
# through the route's function: the printed value as a RATIO to the sheet's reading
orig_sites = server._scale_sites
server._scale_sites = lambda *a, **k: ex
try:
    info = server._room_scale_info(job, "no-such-job", 0, {"scale": {"ft_per_paper_inch": 16.0}},
                                   server.RoomPickBody(x=1400, y=600))
    check(abs(info["ft"] - 5.3333) < 1e-6 and info["by"].startswith("inset caption") and info["check"],
          "the room is measured at 3/16 (5.333 ft/in), flagged: %s %s" % (info["ft"], info["by"]))
    # since the scale verdict (merged 2026-10-02) the server's scale decides: a hand calibration is the reviewer's
    # record on the sheet (written by the scale route at once), and a page sending another number is refused
    orig_ps = server._page_scale
    server._page_scale = lambda *a, **k: {"ft_per_paper_inch": 17.6, "set_by": server._BY_REVIEWER, "set_how": "calibrated"}
    info = server._room_scale_info(job, "no-such-job", 0, {"scale": {"ft_per_paper_inch": 16.0}},
                                   server.RoomPickBody(x=1400, y=600, scale_ft_per_in=17.6))
    check(abs(info["ft"] - 5.3333 * 1.1) < 1e-6 and {round(c["ft"], 3) for c in info["choices"]} == {17.6, round(5.3333 * 1.1, 3)},
          "a sheet calibrated by hand 10 pct off its printed reading carries the 10 pct into the view: %s" % info["ft"])
    info = server._room_scale_info(job, "no-such-job", 0, {"scale": {}},
                                   server.RoomPickBody(x=1400, y=600, scale_ft_per_in=17.6))
    check(info["ft"] == 17.6 and info["check"] and "set by hand" in info["why"],
          "no printed reading to compare with: the scale in force stands, flagged")
    try:
        server._room_scale_info(job, "no-such-job", 0, {"scale": {"ft_per_paper_inch": 16.0}},
                                server.RoomPickBody(x=1400, y=600, scale_ft_per_in=16.0))
        got = "taken"
    except server.HTTPException as e:
        got = e.status_code
    check(got == 409, "a page sending another scale than the sheet's record is refused (the server's scale decides): %s" % got)
    server._page_scale = lambda *a, **k: None
    info = server._room_scale_info(job, "no-such-job", 0, {"scale": {"ft_per_paper_inch": 16.0}},
                                   server.RoomPickBody(x=1400, y=600))
    check(info["ft"] is None and info["multi"] and all(c["ft"] is None for c in info["choices"]) and "offers" in info["why"],
          "no scale in force (readings disagree): no value, every printed caption an offer: %s" % info["why"])
    server._page_scale = orig_ps
finally:
    server._scale_sites = orig_sites

if fails:
    print("%d FAILED" % fails)
    sys.exit(1)
print("all passed")
