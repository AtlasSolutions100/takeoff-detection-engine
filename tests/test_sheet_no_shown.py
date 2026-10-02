"""The sheet number the host SHOWS (takeoff_engine/sheet_no.py) - the page list, the sheet heading, the report header.

Rose Valley 2B sheet 3.0 was listed as "LOT910": the old reader (detect_page.read_sheet_number) puts any lettered
token ahead of a number, and a lot label in the plan body (13.5 pt, inside the bottom-right 30 %) beat the title
block's "3.0" (16.1 pt). The shown-sheet reader refuses lot / block labels and the OF of "2 OF 6" before the old
reader's own ranking (a first version let the largest win: the fourth critic's r8, a spot grade over "C3.0"). It
lives outside detect_page.py because that file is in engine_sig: editing it would mark every detected page out of
date. The corpus expectations of tests/test_sheet_no.py must still hold.

    python tests/test_sheet_no_shown.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="sheetno_")
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="sheetno_lg_")

import pymupdf  # noqa: E402

from takeoff_engine import regress  # noqa: E402
from takeoff_engine.detect_page import read_sheet_number as old_reader  # noqa: E402
from takeoff_engine.detect_page import _ENGINE_FILES  # noqa: E402
from takeoff_engine.sheet_no import read_sheet_number  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


def page(items, w=2592, h=1728):
    doc = pymupdf.open()
    pg = doc.new_page(width=w, height=h)
    for text, x, y, size in items:
        pg.insert_text((x, y), text, fontsize=size)
    return doc, pg


# ---- 1. Rose's case: a lot label in the plan body beside the title block's number ---------------------------------
d1, p1 = page([("3.0", 2436, 1680, 16.1), ("LOT910", 1808, 1418, 13.5), ("LOT901", 1812, 1304, 13.5)])
check(old_reader(p1) == "LOT910" and read_sheet_number(p1) == "3.0",
      f"the title block's 3.0 wins over a lot label (the old reader said {old_reader(p1)})")
# ---- 2. lettered first, as the old reader: over a plain one printed as large, and over a LARGER decimal ---------------
d2, p2 = page([("C3.1", 2400, 1680, 20.0), ("3.1", 2300, 1680, 20.0)])
check(read_sheet_number(p2) == "C3.1", "at the same size a lettered sheet number still wins")
d2b, p2b = page([("C3.0", 2400, 1680, 14.0), ("60.25", 2300, 1640, 16.0)])
check(read_sheet_number(p2b) == "C3.0" == old_reader(p2b),
      f"a larger decimal in the corner (a spot grade) does not name the sheet over C3.0 (the fourth critic's r8): {read_sheet_number(p2b)}")
# ---- 3. lot and block labels never; a bare integer never; the 8 pt floor --------------------------------------------
d3, p3 = page([("LOT12", 2400, 1680, 30.0), ("BLK4", 2300, 1600, 30.0)])
d4, p4 = page([("7", 2500, 1690, 30.0)])
d5, p5 = page([("C4.0", 2500, 1690, 6.0)])
check(read_sheet_number(p3) is None and read_sheet_number(p4) is None and read_sheet_number(p5) is None,
      "a page with only lot / block labels, a bare integer, or a number under 8 pt abstains")
# ---- 3b. the OF of a sheet count ("2 OF 6" read as OF62: White Sulfer 466-470, the third critic's m5) -------------------
d6, p6 = page([("OF62", 2450, 1690, 18.0)])
d7, p7 = page([("OF62", 2450, 1690, 18.0), ("C5.2", 2300, 1650, 14.0)])
check(read_sheet_number(p6) is None and read_sheet_number(p7) == "C5.2",
      f"an OF token is never a sheet number; the real one beside it is read ({read_sheet_number(p7)})")
# ---- 4. it stays out of the detection's fingerprint -----------------------------------------------------------------
check("sheet_no.py" not in _ENGINE_FILES, "sheet_no.py is not in engine_sig: fixing it marks no detection out of date")

# ---- 5. the corpus: what tests/test_sheet_no.py asserts of the old reader holds for this one ---------------------------
def _open(rel):
    try:
        return pymupdf.open(regress.corpus_path(rel))
    except (SystemExit, RuntimeError):
        return None


BARN = "../client_meet/beaumont/SWR_Barn_Residence_-_Civil_4.7.26.pdf"
BARN_EXPECT = {0: "C1.0", 1: "C2.0", 2: "C3.0", 3: "C3.1", 4: "C3.2", 5: "C3.3", 6: "C3.4", 7: "C3.5", 8: "C3.6",
               9: "C4.0", 10: "C4.1", 11: "C4.2", 12: "C4.3", 13: "C5.0", 14: "C5.1"}
doc = _open(BARN)
if doc is None:
    print("SKIP barn: corpus not found beside the repo")
else:
    got = {i: read_sheet_number(doc[i]) for i in range(len(doc))}
    check(got == BARN_EXPECT, f"Beaumont Barn civil: 15/15 printed sheet numbers ({got})")
k = Path(r"C:/Users/kobys/takeoff-plans/kayden-springs-u2.pdf")
if k.exists():
    kd = pymupdf.open(str(k))
    got = {i: read_sheet_number(kd[i]) for i in range(len(kd))}
    check(got[38] == "C5.1" and got[39] == "C5.2" and all(got[i] is None for i in (0, 6, 16, 30, 37)),
          "Kayden Springs u2: C5.1 / C5.2 read, the five blank corners abstain")
else:
    print("SKIP kayden")
od = _open("../TakeoffPro AI/Oncor Cloudcrest Substation Civils - 20251212.pdf_safe.pdf")
if od is None:
    print("SKIP oncor")
else:
    got = {i: read_sheet_number(od[i]) for i in range(len(od))}
    check(got == {i: f"C-{i + 1:02d}" for i in range(len(od))}, "Oncor: C-01..C-09")
rose = Path(r"C:/Users/kobys/Downloads/Rose Valley Phase 2B - Grading Plan.pdf")
if rose.exists():
    rd = pymupdf.open(str(rose))
    check([read_sheet_number(rd[i]) for i in range(len(rd))] == ["3.0", "3.1"], "Rose Valley 2B: 3.0 and 3.1")
else:
    print("SKIP rose")

# ---- 6. the host: the page list uses it, and the report header takes the page list's reading --------------------------
import server  # noqa: E402

jid = "shown1"
pdf = Path(os.environ["TAKEOFF_JOBS_DIR"]) / "rose-like.pdf"
dd = pymupdf.open()
pp = dd.new_page(width=2592, height=1728)
pp.insert_text((2436, 1680), "3.0", fontsize=16.1)
pp.insert_text((1808, 1418), "LOT910", fontsize=13.5)
dd.save(str(pdf))
dd.close()
server.JOBS[jid] = {"path": str(pdf), "pages": 1, "status": {}, "owner": None, "mtime": round(pdf.stat().st_mtime, 2),
                    "sizes": [[2592.0, 1728.0]]}
server._job_dir(jid).mkdir(parents=True, exist_ok=True)
t = server.job_triage(jid)
check(t["version"] == server.TRIAGE_VERSION and t["pages"][0]["sheet_no"] == "3.0",
      f"the page list reads 3.0 (it read LOT910), under a new triage version: {t['pages'][0]['sheet_no']}")
check(server._triage_sheet_no(server._job_dir(jid), 0) == (True, "3.0"),
      "the report header takes the page list's reading when the set is triaged")
(server._job_dir(jid) / "triage.json").write_text(json.dumps({"version": server.TRIAGE_VERSION - 1, "pages": [{"page": 0, "sheet_no": "X"}]}))
check(server._triage_sheet_no(server._job_dir(jid), 0) == (False, None), "an older triage is not used (the detection's stands)")
js = (ROOT / "webui" / "app.js").read_text(encoding="utf-8")
check("const sheetNo = tri ? tri.sheet_no : (state.result && state.result.sheet_no);" in js
      and 'if (typeof renderSheetHead === "function") renderSheetHead();   // the heading names the sheet by this reading too' in js,
      "the sheet heading shows the page list's reading, and repaints when it arrives")

print("\n" + (f"{fails} failure(s)" if fails else "all ok"))
sys.exit(1 if fails else 0)
