"""THE REPORT PRINTS ITS MARKS IN ITS OWN FONT (UI verify 2026-10-02, minor M1).

The report printed the scale as "1· = 20·": the scale caption carried typographic inch / foot
marks and the report's base font has no such glyphs (it has Latin-1 and nothing past it), so
they came out as dots. Every insert_text of report_pdf now goes through server._ReportPage,
which folds what the font lacks (primes, curly quotes, en / em dash, ellipsis, bullet, <=, ->)
to plain characters. This test renders a report whose scale caption, row names and a note use
those characters, and finds 1" = 20' in its text and no replacement mark anywhere.

    python tests/test_report_font_marks.py     (fails on ea8bd62)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="rpfm_")
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="rpfm_lg_")

import pymupdf  # noqa: E402
import server  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


class _Req:
    def __init__(self, user=None, staff=True):
        self.scope = {"tk_user": user, "tk_staff": staff}


# the font itself: what round-trips and what does not (why the fold exists)
d = pymupdf.open()
lost = []
for ch in ["″", "′", "”", "’", "–", "—", "…", "≤", "→", "•"]:
    p = d.new_page()
    p.insert_text((50, 50), "a" + ch + "b", fontsize=8)
    if p.get_text().strip() != "a" + ch + "b":
        lost.append(ch)
kept = []
for ch in ["×", "°", "±", "½", "²", "é"]:
    p = d.new_page()
    p.insert_text((50, 50), "a" + ch + "b", fontsize=8)
    kept.append(p.get_text().strip() == "a" + ch + "b")
check(len(lost) == 10 and all(kept), f"the base font loses {len(lost)} of 10 typographic marks and keeps Latin-1 (x, degree, +-, 1/2, 2, e-acute)")

check(server._report_text('1″ = 20′') == '1" = 20\'' and server._report_text("1” = 20’") == '1" = 20\'',
      "the fold: primes and curly quotes become \" and '")
check(server._report_text("a–b … ≤ → · × ° ☃") == "a-b ... <= -> . x ° ?",
      "the fold: dash, ellipsis, <=, ->, middle dot, times sign fold; degree (Latin-1) stays; anything else is ?")

jid = "rpfm01"
jd = server._job_dir(jid)
jd.mkdir(parents=True, exist_ok=True)
pdf = jd / "sheet.pdf"
d = pymupdf.open()
d.new_page(width=612, height=792)
d.save(pdf)
d.close()
(jd / "p0_elements.json").write_text(json.dumps({
    "imageDpi": 72, "elements": [], "src_marks": 1,
    "scale": {"name": "1″ = 20′", "pixelsPerUnit": 3.6, "unit": "ft"},
    "notes": ["Wall area – perimeter × ceiling … ≤ 10′ → checked"],
    "items": [
        {"label": "6” WATER — C‑W‑PIPE", "count": 1, "length_ft": 120.0},
        {"label": "O-PIPE", "count": 2, "length_ft": 80.0},
    ]}))
server.JOBS[jid] = {"path": str(pdf), "pages": 1, "status": {0: "done"}, "owner": None}

rep = pymupdf.open(server.report_pdf(jid, _Req(), ignore=[]).path)
text = "\n".join(p.get_text() for p in rep)
check('1" = 20\'' in text, "the report's scale line reads 1\" = 20' " + repr([ln for ln in text.splitlines() if "Scale" in ln][:2]))
check("1·" not in text and "20·" not in text, "no dot where an inch or foot mark should be")
bad = sorted({c for c in text if c == "�" or ord(c) > 0xFF})
check(not bad, f"no character past the font's own set anywhere in the report ({[hex(ord(c)) for c in bad]})")
check('6" WATER - C-W-PIPE' in text, "a row name with curly quotes and dashes prints in plain marks")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all report font checks passed")
