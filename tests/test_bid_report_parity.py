"""THE REPORT'S BID PAGE AND THE BID-SHEET CSV GIVE THE SAME MONEY (critic
bid-content-1, RISK-6, 2026-10-01). The CSV honoured the cross-sheet panel's
unticks ("this sheet's contribution is already counted elsewhere") while the
report's bid page built from every approved row, so the same job printed a
higher line in the PDF than in the CSV and on screen. Both now take their
rows, template and unticks from one function (server._bid_build), and the
report link carries the same `ignore` query the CSV link does.

    python tests/test_bid_report_parity.py     (fails on b8c8b60)
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = tempfile.mkdtemp(prefix="bsrp_")
os.environ["TAKEOFF_JOBS_DIR"] = JOBS
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="bsrp_lg_")

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


# a two-sheet job: the same water main drawn on both sheets (the cross-sheet panel's case),
# plus a site cut on sheet 1
jid = "bsrp01"
jd = server._job_dir(jid)
jd.mkdir(parents=True, exist_ok=True)
pdf = jd / "sheet.pdf"
d = pymupdf.open()
for _ in range(2):
    d.new_page(width=612, height=792)
d.save(pdf)
d.close()
fam = lambda ft: {"label": "PROPOSED 8\" WATER MAIN", "count": 2, "length_ft": ft}
# an export this app wrote (src_marks): a v1.8.5 export's old Earthwork calculator rows count nowhere (2026-10-01)
(jd / "p0_elements.json").write_text(json.dumps({"imageDpi": 72, "elements": [], "src_marks": 1, "items": [
    fam(400.0), {"label": "Site Cut", "count": 1, "length_ft": None, "qty": 1234.0, "unit": "CY"}]}))
(jd / "p1_elements.json").write_text(json.dumps({"imageDpi": 72, "elements": [], "items": [fam(250.0)]}))
server.JOBS[jid] = {"path": str(pdf), "pages": 2, "status": {0: "done", 1: "done"}, "owner": None}


def csv_lines(ignore):
    body = bytes(server.bidsheet_csv(jid, _Req(), ignore=ignore).body).decode("utf-8-sig")
    out = {}
    for ln in body.splitlines():
        m = re.match(r"^(\d+),([^,]+),([A-Z]+),([\d.]*),", ln)
        if m and m.group(4):
            out[int(m.group(1))] = float(m.group(4))
    return out


def report_lines(ignore):
    rep = pymupdf.open(server.report_pdf(jid, _Req(), ignore=ignore).path)
    out, txt = {}, ""
    for p in rep:
        t = p.get_text()
        if "Bid sheet" in t.splitlines()[:6] or t.lstrip().startswith("Bid sheet") or "\nBid sheet\n" in t:
            txt = t
    # the bid page is drawn as positioned text: one word per insert, read back with their boxes
    for p in rep:
        if p.get_text() != txt:
            continue
        rows = {}
        for w in p.get_text("words"):
            rows.setdefault(round(w[1]), []).append(w)
        for _, ws in sorted(rows.items()):
            ws.sort(key=lambda w: w[0])
            first = [w for w in ws if w[0] < 65]
            qty = [w for w in ws if 335 <= w[0] < 415]
            if first and qty and first[0][4].isdigit():
                out[int(first[0][4])] = float(qty[0][4].replace(",", ""))
    return out, txt


for ign, what in (([], "no unticks"), (["1:PROPOSED 8\" WATER MAIN"], "sheet 2's water main unticked")):
    c = csv_lines(ign)
    r, txt = report_lines(ign)
    check(c == r and c.get(16) is not None,
          f"{what}: the report's bid page and the CSV give the same lines and totals (csv {c}, report {r})")
c0, c1 = csv_lines([]), csv_lines(["1:PROPOSED 8\" WATER MAIN"])
check(c0[16] == 650.0 and c1[16] == 400.0 and c1[4] == 1234.0,
      f"the untick takes 250 LF off line 16 Water (650 -> 400), Site Cut 1,234 either way ({c0}, {c1})")
# a key naming a page this job has not approved, or a label it does not carry, matches no row and
# changes nothing; the server cannot tell which DOCUMENT a "page:label" key came from, so the page
# sends only the open document's keys (app.js bsIgnoreQuery, B4)
check(csv_lines(["9:PROPOSED 8\" WATER MAIN", "1:GAS LINE", "x:junk", "5"]) == c0,
      "keys for a page not in the job, a label not on the page, or malformed keys leave the bid sheet as it was")
_, txt = report_lines(["1:PROPOSED 8\" WATER MAIN"])
check("1 row(s) unticked on the cross-sheet panel left out" in re.sub(r"\s+", " ", txt),
      "the report's bid page says what it left out, as the CSV does")

src = __import__("inspect").getsource
check("_bid_build(" in src(server.report_pdf) and "_bid_build(" in src(server.bidsheet_csv)
      and "_bidsheet.build(" not in src(server.report_pdf) and "_bidsheet.build(" not in src(server.bidsheet_csv),
      "one function builds both (no second copy of the rows)")
js = (ROOT / "webui" / "app.js").read_text(encoding="utf-8")
check("rl.href = bsReportUrl();" in js and "report.pdf` + bsIgnoreQuery()" in js
      and "if (rl && !rl.hidden) rl.href = bsReportUrl();" in js,
      "the report link carries the unticks and follows them like the CSV link")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all bid-report parity checks passed")
