"""THE ARCHITECTURAL DATUM, READ AS A SUGGESTION (2026-09-16; Wave 2 item 2;
Beaumont call 2026-09-15: "the arch 100 datum can differ per building").
Barn Civil C3.0 prints [100'=7732'] and an FFE SUMMARY; Guide Structural
S1-1.0 prints only what the datum is. The reader returns each with its
page and text; civil_of converts an architectural elevation on a datum.
Then the document-wide route on a fabricated set and, when present, the
real Barn civil set (client file, skipped and said when absent).

    python tests/test_datum_notes.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = tempfile.mkdtemp(prefix="datum_")
os.environ["TAKEOFF_JOBS_DIR"] = JOBS
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="datum_lg_")

from takeoff_engine.datum_notes import civil_of, find_datum_notes  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


civil = "OOM\nWING\nT.O. FFE:7735.25'\n[100'=7732']\nFFE SUMMARY:\nMAIN LEVEL ENTRY:7733.25'\nMAIN LEVEL:7732.25'\nLOWER LEVEL FFE: 7719.125'\nFIN: 33.25\nFFE: 33.25\nTC: 32.87\n"
struct = "FOUNDATION NOTES:\n1. DATUM ELEVATION 100\u2019-0\u201d EQUALS TOP OF \nMAIN LEVEL SUBFLOOR ELEVATION. SEE CIVIL \nFOR USGS DATUM.\n2. ALL SLAB-ON-GRADES ARE 5\u201d THICK\n"
notes = find_datum_notes([(2, civil), (3, struct)])
kinds = [(n["kind"], n["label"], n["value_ft"], n["page"]) for n in notes]
check(kinds[0] == ("datum", "100'-0\" equals", 7732.0, 2), f"the statement [100'=7732'] comes first, page 2 (got {kinds[0]})")
lv = {n["label"].upper(): n["value_ft"] for n in notes if n["kind"] == "level"}
check(lv.get("MAIN LEVEL ENTRY") == 7733.25 and lv.get("MAIN LEVEL") == 7732.25 and lv.get("LOWER LEVEL FFE") == 7719.125 and lv.get("T.O. FFE") == 7735.25, f"the FFE summary's levels with their civil elevations (got {lv})")
check(not any(n["value_ft"] is not None and n["value_ft"] < 100 for n in notes), "shortened spot labels (FFE: 33.25) are not datum suggestions")
nt = [n for n in notes if n["kind"] == "note"]
check(len(nt) == 1 and nt[0]["page"] == 3 and nt[0]["value_ft"] is None and "MAIN LEVEL SUBFLOOR" in nt[0]["label"].upper(), f"the structural note says what 100'-0\" is, with no number (got {nt})")
check(find_datum_notes([(0, civil), (1, civil)]) == find_datum_notes([(0, civil)]), "the same line on two pages is one suggestion")
check(civil_of(86.0, 7732.0) == 7718.0 and civil_of(87.0, 7732) == 7719.0 and civil_of(None, 7732) is None and civil_of(86, "x") is None, "civil_of: 86'-0\" on a 7732 datum is 7718.0; missing values give None")

# ---- the route on a fabricated two-page set ---------------------------------------
import pymupdf  # noqa: E402
import server  # noqa: E402

pdf = Path(JOBS) / "set.pdf"
doc = pymupdf.open()
p0 = doc.new_page(width=612, height=792)
y = 100
for line in ("[100'=7732']", "FFE SUMMARY:", "MAIN LEVEL ENTRY:7733.25'", "LOWER LEVEL FFE: 7719.125'"):
    p0.insert_text((80, y), line, fontsize=9); y += 14
p1 = doc.new_page(width=612, height=792)
p1.insert_text((80, 100), "DATUM ELEVATION 100'-0\" EQUALS TOP OF MAIN LEVEL SUBFLOOR ELEVATION. SEE CIVIL FOR USGS DATUM.", fontsize=7)
doc.save(str(pdf)); doc.close()
jid = "datumroute1"
server._job_dir(jid).mkdir(parents=True, exist_ok=True)
server.JOBS[jid] = {"path": str(pdf), "pages": 2, "status": {}, "owner": None}
r = server.datum_notes(jid)
j = json.loads(bytes(r.body)) if hasattr(r, "body") else r
ks = [(n["kind"], n["value_ft"], n["page"]) for n in j["notes"]]
check(ks[0] == ("datum", 7732.0, 0) and ("level", 7733.25, 0) in ks and ("level", 7719.125, 0) in ks and any(k[0] == "note" and k[2] == 1 for k in ks), f"the route reads the whole set: datum, levels, note (got {ks})")
check((server._job_dir(jid) / "datum_notes.json").exists() and j["pages"] == 2, "cached per job, pages counted")
try:
    server.datum_notes("nosuchjob")
    check(False, "unknown job must 404")
except Exception as e:
    check(getattr(e, "status_code", None) == 404, f"unknown job -> 404 ({getattr(e, 'status_code', e)})")

# ---- the real Barn civil set, when present ---------------------------------------
barn = Path(r"C:\Users\kobys\OneDrive\Desktop\past\P\Takeoff Automation\client_meet\beaumont\SWR_Barn_Residence_-_Civil_4.7.26.pdf")
if barn.exists():
    d = pymupdf.open(str(barn))
    notes = find_datum_notes([(i, d[i].get_text()) for i in range(len(d))])
    dat = [n for n in notes if n["kind"] == "datum"]
    check(dat and dat[0]["value_ft"] == 7732.0 and dat[0]["page"] == 2, f"Barn Civil: [100'=7732'] on C3.0 (page index 2) (got {dat[:2]})")
    lv = {n["label"].upper(): n["value_ft"] for n in notes if n["kind"] == "level"}
    check(lv.get("MAIN LEVEL ENTRY") == 7733.25 and lv.get("LOWER LEVEL FFE") == 7719.125, f"Barn Civil: the FFE summary levels (got {lv})")
else:
    print("skip: Barn civil set not on this machine (client file)")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all datum-note checks passed")
