"""THE BID-SHEET TEMPLATE AND THE JOB-LEVEL EXPORT (2026-09-16; Wave 2 item
9; Beaumont: "one sheet with every quantity, in my bid-sheet order and
units"). The printed default, the matcher (shared cases with the JS port),
the build over approved pages with units and unticks, the CSV, the
per-seat store, and the routes on a fabricated job.

    python tests/test_bidsheet.py     (fails on cd9fadf)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = tempfile.mkdtemp(prefix="bs_")
os.environ["TAKEOFF_JOBS_DIR"] = JOBS
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="bs_lg_")

import server  # noqa: E402
from takeoff_engine import bidsheet as bs  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


# ---- the printed default ------------------------------------------------------------
D = bs.DEFAULT_LINES
check(len(D) == 25 and [d["n"] for d in D] == list(range(1, 26)), "25 lines, numbered as printed")
check([d["name"] for d in D[:5]] == ["Mobilization", "Clear & Grub", "Strip Topsoil", "Site Cut", "Site Fill"], "the sheet's first five lines in his order")
check(D[0]["unit"] == "LS" and D[1]["unit"] == "SF" and D[2]["unit"] == "CY" and D[6]["unit"] == "LF" and D[10]["unit"] == "SF", "units as he wrote them beside the printed lines")
check(D[19]["name"] == "Radon" and D[20]["name"] == "Site Drains" and D[21]["name"] == "Boulder walls", "his three handwritten lines follow the printed nineteen")
check(all(d["unit"] in bs.UNITS for d in D), "every default unit is an allowed unit")
fx = json.load(open(ROOT / "tests" / "fixtures" / "bidsheet_default.json", encoding="utf-8"))
check(fx == D, "the committed default fixture equals the module (tests/test_bidsheet_ui.js reads it, so the JS port cannot drift)")

# ---- matching (shared cases with the JS port) ------------------------------------------
cases = json.load(open(ROOT / "tests" / "fixtures" / "bidsheet_cases.json", encoding="utf-8"))["cases"]
bad = []
for c in cases:
    i, how = bs.line_for(c["label"], None, D)
    got = None if i is None else D[i]["n"]
    if got != c["n"]:
        bad.append((c["label"], c["n"], got, how))
check(not bad, f"every shared case files onto its printed line ({len(cases)} cases)" + (f" - wrong: {bad}" if bad else ""))
check(bs.line_for("SD", "C-STRM-PIPE", D)[0] == 20 and bs.line_for("solid solid", "C-UTIL-WATR", D) == (15, "WATR"), "the CAD layer is a second pass (a WATR layer files an unnamed family on Water)")
check(bs.line_for("solid solid", "C-UTIL-W", D)[0] is None, "a one-letter utility token (W) never fires from the layer - too many CAD paths end in one letter")
check(bs.line_for("solid", "E", D)[0] is None, "a one-letter utility token never fires from the layer alone")
check(bs.token_hit(bs.norm_text("BACKFILL"), "=FILL") is False and bs.token_hit(bs.norm_text("SITE FILL"), "=FILL") is True, "=WORD stands alone")
check(bs.token_hit(bs.norm_text("SDWK"), "SD") is False and bs.token_hit(bs.norm_text("8 SD PIPE"), "SD") is True, "a token of three letters or fewer stands alone")

# ---- the build over approved pages -------------------------------------------------------
pages = [
    {"page": 2, "items": [
        {"label": "Clear & Grub", "count": 1, "length_ft": None, "qty": 142761, "unit": "SF"},
        {"label": "Strip Topsoil", "count": 1, "length_ft": None, "qty": 5730, "unit": "CY"},
        {"label": "Site Cut", "count": 1, "length_ft": None, "qty": 6323, "unit": "CY"},
        {"label": "Silt / Construction Fence", "count": 1, "length_ft": None, "qty": 1161, "unit": "LF"},
        {"label": "PROPOSED 4\" RIGID PERFORATED PVC FOOTER DRAIN PIPE", "count": 12, "length_ft": 1449.6},
        {"label": "8\" PVC STORM", "count": 3, "length_ft": 400.0},
        {"label": "SEPTIC TANK 1500 GAL", "count": 1, "length_ft": None},
        {"label": "Over-excavation", "count": 1, "length_ft": None, "qty": 3919, "unit": "CY"},
        {"label": "4\" SLAB ON GRADE", "count": 4, "length_ft": 220.0},
    ]},
    {"page": 5, "items": [
        {"label": "8\" PVC STORM", "count": 2, "length_ft": 300.0},
        {"label": "Perimeter Drain", "count": 1, "length_ft": None, "qty": 320, "unit": "LF", "qty_set_by": "reviewer"},
    ]},
]
r = bs.build(D, pages)
L = {x["n"]: x for x in r["lines"]}
check(L[2]["qty"] == 142761 and L[3]["qty"] == 5730 and L[4]["qty"] == 6323, "calculator rows land on their lines with their quantities")
check(L[7]["qty"] == 1769.6 and len(L[7]["rows"]) == 2, f"the footer drain family (LF) and the Perimeter Drain row add into line 7 (got {L[7]['qty']})")
check(L[21]["qty"] == 700.0 and [x["page"] for x in L[21]["rows"]] == [2, 5], "storm pipe on two sheets adds up, once per sheet")
check(L[24]["qty"] == 1 and L[24]["rows"][0]["unit"] == "EA", "an object count is EA")
check(L[1]["qty"] is None and L[1]["rows"] == [], "Mobilization is lump sum: blank")
check(L[11]["qty"] == 0.0 and L[11]["other"] == {"LF": 220.0} and L[11]["rows"][0]["ok"] is False, "a slab family measured in feet is listed on Slab Prep but not summed into SF")
check([x["label"] for x in r["unmatched"]] == ["Over-excavation"], f"a row no line names is listed, never dropped (got {[x['label'] for x in r['unmatched']]})")
check(L[23]["qty"] == 1161, "the fence lands on line 23")
r2 = bs.build(D, pages, ignore={(5, "8\" PVC STORM")})
check({x["n"]: x["qty"] for x in r2["lines"]}[21] == 400.0 and r2["ignored"] == 1, "an unticked sheet contribution is left out and counted as ignored")
csv_text = bs.to_csv(r, "plan.pdf", "default")
lines = csv_text.splitlines()
check(lines[0].startswith("CORE Takeoff - bid sheet,plan.pdf") and "printed sheet" in lines[1], "the CSV names the document and the template's scope")
check(any(l.startswith("2,Clear & Grub,SF,142761,1,") for l in lines) and any(l.startswith("1,Mobilization,LS,,0,") for l in lines), "one row per line in order, blanks kept")
check(any(l.startswith("7,Perimeter Drain,LF,1769.6,2,") for l in lines), "a summed line prints its sum and its row count")
check(any("Not on the bid sheet" in l for l in lines) and any(l.startswith(",Over-excavation,CY,3919,1,p3") for l in lines), "the unmatched rows print at the end with their sheet")
check(any("not summed: other unit" in l for l in lines), "a row kept out of a sum says so")

# ---- the per-seat store --------------------------------------------------------------------
lines0, scope0 = bs.resolve("alice")
check(scope0 == "default" and [x["name"] for x in lines0] == [x["name"] for x in D], "no file: the printed default, said so")
mine = [{"name": "Clear & Grub", "unit": "sf", "match": "clear, grub", "not": []},
        {"name": "Radon", "unit": "LF", "match": ["RADON"], "re": "^Radon", "note": "  interior  face "},
        {"name": "", "unit": "LF"}, {"name": "Bad unit", "unit": "BAGS"}, "junk",
        {"name": "Water", "unit": "LF", "match": ["W"], "re": "(["}]
got, scope = bs.save("alice", mine)
check(scope == "account" and [x["name"] for x in got] == ["Clear & Grub", "Radon", "Water"] and [x["n"] for x in got] == [1, 2, 3], f"saved lines are cleaned and renumbered; blank names, bad units and junk are dropped (got {[x['name'] for x in got]})")
check(got[0]["unit"] == "SF" and got[0]["match"] == ["CLEAR", "GRUB"] and got[1]["note"] == "interior face" and "re" not in got[2], "units upper-cased, a comma list split, notes tidied, a bad regex dropped")
check(bs.resolve("bob")[1] == "default", "another account still sees the default")
bs.save(None, [{"name": "Install line", "unit": "EA"}])
check(bs.resolve("bob")[1] == "install" and bs.resolve("alice")[1] == "account", "the install sheet is the fallback for accounts without their own")
bs.save("alice", [])
check(bs.resolve("alice") == ([], "account"), "an empty sheet saved by an account is a decision, not a fall-through")
bs.save("alice", None)
check(bs.resolve("alice")[1] == "install", "saving None removes the account's sheet")
bs.store_path().write_text("{not json", encoding="utf-8")
check(bs.resolve("alice")[1] == "default", "a corrupt store reads as the default, never a 500")
bs.store_path().unlink()

# ---- routes ---------------------------------------------------------------------------------
class _Req:
    def __init__(self, user=None):
        self.scope = {"type": "http", "headers": []}
        self.cookies = {}
        self.headers = {}
        self.query_params = {}
        self._user = user


def _who(req):
    return getattr(req, "_user", None)


server._who = _who
j = server.get_bidsheet(_Req("alice"))
check(j["scope"] == "default" and len(j["lines"]) == 25 and j["units"][0] == "LS" and len(j["default"]) == 25, "GET /api/bidsheet: the default with its scope, the unit list and the printed sheet for a reset")
j = server.post_bidsheet(server.BidsheetBody(lines=[{"name": "Only line", "unit": "CY", "match": ["=CUT"]}]), _Req("alice"))
check(j["scope"] == "account" and j["lines"][0]["name"] == "Only line" and server.get_bidsheet(_Req("bob"))["scope"] == "default", "POST saves the seat's sheet and no other seat sees it")
j = server.post_bidsheet(server.BidsheetBody(lines=None), _Req("alice"))
check(j["scope"] == "default", "POST null resets the seat to the printed sheet")

jid = "bsjob1"
d = server._job_dir(jid)
d.mkdir(parents=True, exist_ok=True)
server.JOBS[jid] = {"path": str(Path(JOBS) / "plan.pdf"), "pages": 6, "status": {}, "owner": None}
try:
    server.bidsheet_csv(jid, _Req("alice"), [])
    check(False, "no approved pages must 404")
except Exception as e:
    check(getattr(e, "status_code", None) == 404, f"no approved pages -> 404 ({getattr(e, 'status_code', e)})")
for pg in pages:
    (d / f"p{pg['page']}_elements.json").write_text(json.dumps({"items": pg["items"]}), encoding="utf-8")
resp = server.bidsheet_csv(jid, _Req("alice"), [])
body = bytes(resp.body).decode("utf-8")
check(resp.media_type == "text/csv" and "attachment" in resp.headers.get("content-disposition", "") and "2,Clear & Grub,SF,142761,1," in body, "the CSV route returns an attachment built from the approved pages")
resp = server.bidsheet_csv(jid, _Req("alice"), ["5:8\" PVC STORM", "junk"])
check("21,Site Drains,LF,400,1," in bytes(resp.body).decode("utf-8"), "the ignore query leaves an unticked sheet contribution out; a malformed key is ignored")
try:
    server.bidsheet_csv("nosuchjob", _Req("alice"), [])
    check(False, "unknown job must 404")
except Exception as e:
    check(getattr(e, "status_code", None) == 404, "unknown job -> 404")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all bidsheet checks passed")
