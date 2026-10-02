"""The accuracy flag (was the BETA notice), pinned to the owner's words.

Owner, 2026-10-01: the chip says "Automated detection can be wrong", its
hover says "Double-check every quantity against the drawings.", and every
export plus the sign-in / change-password / Terms / Privacy bars carry the
full line "Automated detection can be wrong. Double-check every quantity
against the drawings." The word BETA, "still being refined" and "in active
development" leave all of those places. The flag stays non-dismissible and
on every surface it was on; the internal names (#betaBanner, .beta-chip,
/api/beta-notice) stay so the redesign's frame keeps finding it.

This test renders a real report PDF and reads its text back, so "the band is
stamped" is checked on the file a browser downloads, not on the source.
The legal BODY of terms.html / privacy.html ("beta software") is the owner's
legal text and is deliberately not checked here.

    python tests/test_accuracy_flag.py
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="accuracy_flag_test_")

import pymupdf  # noqa: E402

import server  # noqa: E402

CHIP = "Automated detection can be wrong"
HOVER = "Double-check every quantity against the drawings."
FLAG = "Automated detection can be wrong. Double-check every quantity against the drawings."
OLD = re.compile(r"\bBETA\b|still being refined|in active development|verify every quantity", re.I)

CHECKS = []


def ok(cond, msg, got=None):
    CHECKS.append((bool(cond), msg, got))


class _Req:
    def __init__(self, user=None, staff=False):
        self.scope = {"tk_user": user, "tk_staff": staff}


# ---- 1. the server's words ---------------------------------------------------------------------
n = server.beta_notice()
ok(n["chip"] == CHIP, "api: the chip text is the owner's", n.get("chip"))
ok(n["short"] == HOVER, "api: the hover is the owner's sentence", n.get("short"))
ok(n["flag"] == FLAG, "api: the full line is the owner's", n.get("flag"))
ok(n["long"].startswith(FLAG), "api: the export line starts with the full line", n.get("long"))
ok(len(n["long"]) <= 160, "api: the export line is not longer than a short caution", len(n["long"]))
for k, v in n.items():
    ok(not OLD.search(v), f"api: {k} carries no BETA / 'still being refined' / 'in active development'", v)

# ---- 2. the chip in index.html (the redesign reads its text and title at runtime) ---------------
html = (ROOT / "webui" / "index.html").read_text(encoding="utf-8")
m = re.search(r'<span id="betaBanner"[^>]*title="([^"]*)"[^>]*>([^<]*)</span>', html)
ok(m is not None, "index: #betaBanner is still a span with a title")
if m:
    ok(m.group(1) == HOVER, "index: the chip's title is the hover sentence", m.group(1))
    ok(m.group(2) == CHIP, "index: the chip's face is the short flag", m.group(2))
    ok('class="beta-chip"' in m.group(0), "index: the chip keeps its class (selectors)")
    ok("onclick" not in m.group(0) and "close" not in m.group(0).lower(), "index: the chip has no dismiss")
# the visible text of index.html (no comments) says BETA nowhere
_vis = re.sub(r"<!--.*?-->", "", html, flags=re.S)
ok(not re.search(r"\bBETA\b|beta notice", _vis), "index: no visible BETA / 'beta notice' anywhere")

# ---- 3. the bars of the four standalone pages -----------------------------------------------------
for page in ("login.html", "change-password.html", "terms.html", "privacy.html"):
    src = (ROOT / "webui" / page).read_text(encoding="utf-8")
    bar = re.search(r'<div class="beta">([^<]*)</div>', src)
    ok(bar is not None and bar.group(1) == FLAG, f"{page}: the bar carries the full line",
       bar and bar.group(1))

# ---- 4. app.js: the CSV fallback and the chip wiring -------------------------------------------
app = (ROOT / "webui" / "app.js").read_text(encoding="utf-8")
i = app.index("let BETA_CSV_TAIL")
tail = app[i:app.index(";", i)]
tail_txt = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', tail)).replace("\\n", "")
ok(tail_txt.startswith(FLAG), "app.js: the CSV fallback line starts with the full line", tail_txt)
ok(not OLD.search(tail_txt), "app.js: the CSV fallback carries no BETA")
ok("BETA_CSV_TAIL], {" in app, "app.js: the tail is still appended to the CSV blob")
ok("el.title = n.short" in app and "el.textContent = n.chip" in app,
   "app.js: the chip's title and face come from /api/beta-notice")

# ---- 5. the report PDF: every page carries the band, after branding ----------------------------
jid = "accflag01"
jd = server._job_dir(jid)
jd.mkdir(parents=True, exist_ok=True)
src_pdf = jd / "sheet.pdf"
d = pymupdf.open()
d.new_page(width=612, height=792)
d.save(src_pdf)
d.close()
(jd / "p0_elements.json").write_text(json.dumps({"imageDpi": 72, "elements": [], "items": []}))
server.JOBS[jid] = {"path": str(src_pdf), "pages": 1, "status": {0: "done"}, "owner": None}
try:
    resp = server.report_pdf(jid, _Req(user=None, staff=True))
    rep = pymupdf.open(resp.path)
    texts = [p.get_text() for p in rep]
    ok(len(texts) >= 2, "report: rendered at least the overlay and one text page", len(texts))
    norm = [re.sub(r"\s+", " ", t) for t in texts]
    ok(all(FLAG in t for t in norm), "report: EVERY page carries the full flag line",
       [FLAG in t for t in norm])
    ok(not any(OLD.search(t) for t in norm), "report: no page says BETA / 'in active development'")
except Exception as e:  # noqa: BLE001
    ok(False, "report: renders", repr(e))

src = __import__("inspect").getsource(server.report_pdf)
ok("BETA_LONG" in src and src.rindex("BETA_LONG") > src.rindex("_letterhead(tp)"),
   "report: the band is drawn from BETA_LONG, after every letterhead call")

# ---- 6. EVERY download / print path (critic bid-content-1, RISK-5) -----------------------------
# The table (grep: text/csv, application/pdf, Content-Disposition, Blob, download=):
#   takeoff CSV            webui/app.js (Blob + BETA_CSV_TAIL)                 flag: yes (section 4)
#   report PDF             GET /api/job/<id>/report.pdf                         flag: yes, every page (section 5)
#   bid-sheet CSV          GET /api/job/<id>/bidsheet.csv                       flag: yes (below)
#   marks-by-sheet CSV     webui/app.js rollCsv (Blob + BETA_CSV_TAIL)          flag: yes (below)
#   cut / fill CSV         GET /cutfill/api/projects/<pid>/export.csv           flag: yes (below)
#   cut / fill tab         /cutfill/ opened in its own tab                      flag: yes, chip (below)
#   cut / fill crew map    GET /cutfill/api/projects/<pid>/crewmap.png          flag: no - a picture of depths
#   approved elements JSON GET /api/job/<id>/page/<n>/elements (TakeoffDraft2)  flag: no - machine import format
#   price book CSV         webui/app.js (Settings -> prices)                    flag: no - prices, no quantity
#   feedback CSV           GET /api/feedback.csv (staff only)                   flag: no - feedback, no quantity
LINE = server.BETA_LONG
# 6a. the bid-sheet CSV: the flag is its last line, after a blank row; its header (title, template, approved
# sheets, note, then any OUT OF DATE / CHECK notes) is where it was
bj = "accflag02"
bd = server._job_dir(bj)
bpdf = bd / "bid.pdf"
dd = pymupdf.open()
dd.new_page(width=612, height=792)
dd.save(bpdf)
dd.close()
(bd / "p0_elements.json").write_text(json.dumps({"imageDpi": 72, "elements": [],
                                                  "items": [{"label": "Water", "qty": 120.0, "unit": "LF"}]}))
server.JOBS[bj] = {"path": str(bpdf), "pages": 1, "status": {0: "done"}, "owner": None}
try:
    r = server.bidsheet_csv(bj, _Req(user=None, staff=True), ignore=[])
    body = r.body.decode("utf-8-sig")
    lines = body.rstrip("\n").split("\n")
    ok(lines[-1] == LINE and lines[-2] == "", "bid-sheet CSV: ends with a blank row and the flag line", lines[-2:])
    ok(lines[0].startswith("CORE Takeoff - bid sheet") and lines[1].startswith("template")
       and lines[2].startswith("approved sheets") and lines[3].startswith("note"),
       "bid-sheet CSV: the header rows are untouched (OUT OF DATE / CHECK notes still go after row 4)", lines[:4])
    ok(sum(1 for ln in lines if ln == LINE) == 1, "bid-sheet CSV: the flag once")
except Exception as e:  # noqa: BLE001
    ok(False, "bid-sheet CSV: built", repr(e))
bsrc = __import__("inspect").getsource(server.bidsheet_csv)
ok(bsrc.index("parts[:4]") < bsrc.index("_csv_flag_line()"),
   "bid-sheet CSV: the header notes are inserted first, the flag appended after (their order stands)")
# 6b. the marks-by-sheet CSV (built in the browser, the same tail as the takeoff CSV)
rc = app[app.index('$("rollCsv").onclick'):]
rc = rc[:rc.index("\n};")]
ok("new Blob([csv + BETA_CSV_TAIL]" in rc, "marks-by-sheet CSV: ends with BETA_CSV_TAIL (from /api/beta-notice)")
# 6c. the cut / fill CSV: the engine's export ends with the host's line (one source: server._csv_flag_line)
import cutfill.server as cfs  # noqa: E402

ok(callable(cfs.EXPORT_NOTICE) and cfs.EXPORT_NOTICE() == server._csv_flag_line() == LINE,
   "cut / fill CSV: the host hands the engine its own line")
_fake = {"grid": {"nx": 1, "ny": 1, "eg": [100.0], "fg": [101.0], "x0": 0, "y0": 0, "cell_pt": 10},
         "totals": {"cut_cy": 0, "fill_cy": 1.0, "net_cy": 1.0, "area_sf": 25, "coverage": 1.0}, "cell_ft": 5.0}
_real_result = cfs.result
cfs.result = lambda pid: _fake
try:
    cbody = cfs.export_csv("job_x").body.decode("utf-8")
    clines = cbody.rstrip("\n").split("\n")
    ok(clines[-1] == LINE and clines[-2].strip() == "", "cut / fill CSV: ends with a blank row and the flag line",
       clines[-2:])
    ok(clines[0].startswith('"cut / fill cells.') or clines[0].startswith("cut / fill cells."),
       "cut / fill CSV: its own header line is untouched", clines[0][:40])
finally:
    cfs.result = _real_result
# 6d. the engine's own page in its own tab: the chip, non-dismissible, the server's words
cf_html = (ROOT / "webui" / "cutfill" / "index.html").read_text(encoding="utf-8")
cf_js = (ROOT / "webui" / "cutfill" / "app.js").read_text(encoding="utf-8")
mc = re.search(r'<span id="cfFlag"[^>]*title="([^"]*)"[^>]*>([^<]*)</span>', cf_html)
ok(mc and mc.group(1) == HOVER and mc.group(2) == CHIP, "cut / fill tab: the chip carries the owner's words",
   mc and mc.groups())
ok(mc and "onclick" not in mc.group(0) and "close" not in mc.group(0).lower(), "cut / fill tab: the chip has no dismiss")
sf = cf_js[cf_js.index("function showFlag()"):]
sf = sf[:sf.index("\n}\n")]
ok("window.self !== window.top" in sf and "fetch('/api/beta-notice')" in sf and "el.hidden = false" in sf,
   "cut / fill tab: shown when the page is its own tab, words from /api/beta-notice (the host's chip covers the frame)")
ok("showFlag();" in cf_js, "cut / fill tab: showFlag runs at load")
# 6e. nothing else downloads: every Blob / download / Content-Disposition is one of the rows above
import subprocess  # noqa: E402

grep = subprocess.run(["git", "-C", str(ROOT), "grep", "-n", "-E",
                       r"text/csv|application/pdf\"|Content-Disposition|new Blob|\.download *=",
                       "--", "server.py", "cutfill/*.py", "webui/*.js", "webui/cutfill/*.js"],
                      capture_output=True, text=True).stdout.splitlines()
KNOWN = ("feedback.csv", "takeoff.csv", "marks by sheet.csv", "price-book.csv", "bidsheet_", "cutfill.csv",
         "crew-map.png", "report.pdf", "takeoff_", "BETA_CSV_TAIL", "svSerializeCSV", "new Blob([csv",
         "media_type=\"text/csv\"", "media_type=\"application/pdf\"", "headers={\"Content-Disposition\"",
         "file.type")   # file.type: an upload check, not a download
unknown = [g for g in grep if not any(k in g for k in KNOWN)]
ok(not unknown, "every download path in the code is in the table above", unknown[:8])

# ---------------------------------------------------------------- report
fails = [c for c in CHECKS if not c[0]]
for good, msg, got in CHECKS:
    print(("ok:   " if good else "FAIL: ") + msg
          + ("" if good or got is None else f"   got={got!r}"))
print(f"\n{len(CHECKS) - len(fails)}/{len(CHECKS)} checks passed")
sys.exit(1 if fails else 0)
