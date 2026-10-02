"""THE REPORT PDF PRINTS EVERY ROW LABEL WHOLE (live round, 2026-10-02).

The report's tables cut their row labels by character count: the page table and
the summary at 52 characters, the bid page's item column at 44. A typed sub-item
exports as "<name> (sub-item of <parent>)", so the bid page printed
"Removal of existing sidewalk (sub-item of" and lost the parent. Labels are now
wrapped inside their column (server._report_cell_lines, measured with the font)
and never cut; the other columns stay on the label's first line.

This test renders a report with long sub-item labels and finds each whole label
in the page table, the summary and the bid page, every word of it left of the
next column (x = 300).

    python tests/test_report_long_labels.py     (fails on 51c90b2)
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="rpll_")
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="rpll_lg_")

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


SHORT = "Removal of existing sidewalk (sub-item of P1-WALK)"
LONG = ("Removal of existing sidewalk and curb ramp to the nearest joint "
        "(sub-item of PROPOSED CONCRETE SIDEWALK PER DETAIL 4 ON SHEET C5.1)")
ODD = ("Decorative bollard light with stainless steel sleeve and anchor bolts "
       "(sub-item of PROPOSED PEDESTRIAN PLAZA PAVERS)")
LABELS = (SHORT, LONG, ODD)

jid = "rpll01"
jd = server._job_dir(jid)
jd.mkdir(parents=True, exist_ok=True)
pdf = jd / "sheet.pdf"
d = pymupdf.open()
d.new_page(width=612, height=792)
d.save(pdf)
d.close()
(jd / "p0_elements.json").write_text(json.dumps({"imageDpi": 72, "elements": [], "src_marks": 1, "items": [
    {"label": "P1-WALK", "count": 1, "length_ft": 120.0},
    {"label": SHORT, "count": 1, "length_ft": None, "qty": 340.0, "unit": "SF"},
    {"label": LONG, "count": 1, "length_ft": None, "qty": 512.0, "unit": "SF"},
    {"label": ODD, "count": 1, "length_ft": None, "qty": 6.0, "unit": "EA"},
]}))
server.JOBS[jid] = {"path": str(pdf), "pages": 1, "status": {0: "done"}, "owner": None}

rep = pymupdf.open(server.report_pdf(jid, _Req(), ignore=[]).path)
flat = lambda s: re.sub(r"\s+", " ", s).strip()


def page_kind(t):
    if "Takeoff summary" in t:
        return "summary"
    if re.search(r"(^|\n)Bid sheet\n", t):
        return "bid"
    if "Extended $" in t:
        return "table"
    return None


seen = {}
for p in rep:
    t = p.get_text()
    k = page_kind(t)
    if not k:
        continue
    seen.setdefault(k, []).append(p)

check(set(seen) >= {"table", "summary", "bid"}, f"the report has a page table, a summary and a bid page ({sorted(seen)})")

for kind, pages in sorted(seen.items()):
    words = [w for p in pages for w in p.get_text("words")]
    text = flat(" ".join(p.get_text() for p in pages))
    for lab in LABELS:
        if kind == "bid" and lab == SHORT and lab not in text:
            # a sub-item filed onto a named bid line prints that line's name, not the label
            continue
        check(lab in text, f"{kind}: the whole label is printed: {lab!r}")
    # no label word runs into the next column: everything left of x = 300 ends before it
    over = [w[4] for w in words if w[0] < 295 and w[2] > 300]
    check(not over, f"{kind}: no label word crosses into the next column at x = 300 ({over[:5]})")

# the bid page carries the unmatched long sub-item under "Not on the bid sheet", whole
bid_text = flat(" ".join(p.get_text() for p in seen.get("bid", [])))
check(ODD in bid_text, "bid page: the unmatched sub-item is printed whole under 'Not on the bid sheet'")

# the numbers stay on the label's first line (the column reader of test_bid_report_parity relies on it)
for p in seen.get("table", []):
    ws = p.get_text("words")
    first = [w for w in ws if w[4] == "Decorative" and w[0] < 60]
    qty = [w for w in ws if 355 <= w[0] < 435 and w[4] == "6"]
    check(first and qty and abs(first[0][1] - qty[0][1]) < 2,
          "page table: the quantity prints on the label's first line")

if fails:
    print(f"{fails} check(s) failed")
    sys.exit(1)
print("all checks passed")
