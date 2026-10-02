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
ft, by = server._room_pick_scale(job, "no-such-job", 0, res, body)
check(ft == 8.0 and by == "on screen", "the scale on screen wins: %s %s" % (ft, by))
body2 = server.RoomPickBody(x=100, y=100)
ft, by = server._room_pick_scale(job, "no-such-job", 0, res, body2)
check(ft == 4.0 and by == "engine", "with nothing on screen and no recorded pick, the engine's reading: %s %s" % (ft, by))
body3 = server.RoomPickBody(x=100, y=100, scale_ft_per_in=1e9)
ft, by = server._room_pick_scale(job, "no-such-job", 0, res, body3)
check(ft == 4.0 and by == "engine", "an out-of-band on-screen number is refused, never substituted silently")
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

if fails:
    print("%d FAILED" % fails)
    sys.exit(1)
print("all passed")
