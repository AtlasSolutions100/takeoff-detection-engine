"""A BID SHEET SAVED ON v1.8.5 GAINS THE NET LINES (release check 2026-10-01,
PARITY.md section 3). v1.8.5's default had 25 lines and its store had no
version; the new default adds 26-29 (Export Cut, Import Structural, Export
Topsoil, Import Topsoil). A seat that saved its own sheet on v1.8.5 kept 25
lines, so after the replace its net rows fell under "not on the bid sheet"
and nothing said why. tests/fixtures/bidsheet_v185_store.json was written
by the LIVE v1.8.5 bidsheet.save itself (core-release-scratch/parity/
make_v185_store.py): an install sheet = the old default unchanged, and an
account sheet the client edited (a renamed line, a moved line, a removed
line, an added line).

    python tests/test_bidsheet_upgrade.py     (fails on 3b3a005)
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = tempfile.mkdtemp(prefix="bsup_")
os.environ["TAKEOFF_JOBS_DIR"] = JOBS
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="bsup_lg_")

import server  # noqa: E402
from takeoff_engine import bidsheet as bs  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


NEW = ["Export Cut", "Import Structural", "Export Topsoil", "Import Topsoil"]
FX = ROOT / "tests" / "fixtures" / "bidsheet_v185_store.json"
raw = json.loads(FX.read_text(encoding="utf-8"))
check("versions" not in raw and len(raw["install"]) == 25 and len(raw["accounts"]["estimator"]) == 25,
      "the fixture is v1.8.5's own file: no version, 25 lines on each sheet")
shutil.copy(FX, bs.store_path())

# an old job's export (the live calculator's names) plus the cut/fill engine's own net row
items = [{"label": l, "count": 1, "length_ft": None, "qty": q, "unit": "CY"} for l, q in
         [("Site Cut", 1234.0), ("Export Surplus Cut", 334.0), ("Import Structural", 50.0),
          ("Import Topsoil", 12.0), ("Zone 1 cut", 100.0), ("Export Topsoil", 7.0)]]
items.append({"label": "Property line", "count": 1, "length_ft": 812.4})
pages = [{"page": 0, "items": items}]


def totals(lines):
    r = bs.build(lines, pages)
    return ({L["name"]: L["qty"] for L in r["lines"] if L["rows"]},
            [(u["label"], u["qty"]) for u in r["unmatched"]])


# BEFORE: the v1.8.5 sheet as the new code read it without the upgrade
old_lines = bs.clean_lines(raw["install"])
lt, un = totals(old_lines)
check(lt.get("Site Cut") == 1334.0 and ("Export Surplus Cut", 334.0) in un and ("Import Structural", 50.0) in un
      and ("Import Topsoil", 12.0) in un and ("Export Topsoil", 7.0) in un,
      f"before: the net rows fall under 'not on the bid sheet' (lines {lt}, unmatched {un})")

# AFTER: loading the saved sheet appends 26-29
lines, scope, added = bs.resolve_full(None)
check(scope == "install" and len(lines) == 29 and [x["name"] for x in lines[25:]] == NEW and added == NEW,
      f"the install sheet gains the four lines at its end and says which ({added})")
check([{k: v for k, v in x.items() if k != "n"} for x in lines[:25]] == [{k: v for k, v in x.items() if k != "n"} for x in old_lines]
      and [x["n"] for x in lines] == list(range(1, 30)),
      "its own 25 lines are untouched, in order; the new ones numbered after them")
lt, un = totals(lines)
check(lt == {"Site Cut": 1334.0, "Export Cut": 334.0, "Import Structural": 50.0, "Export Topsoil": 7.0, "Import Topsoil": 12.0}
      and un == [("Property line", 812.4)],
      f"after: Site Cut 1,334 (1,234 + Zone 1 cut 100), Export Cut 334, Import Structural 50, Export Topsoil 7, "
      f"Import Topsoil 12; a row no line names stays listed with its quantity ({lt}, {un})")

acct, scope, added = bs.resolve_full("estimator")
mine = raw["accounts"]["estimator"]
check(scope == "account" and len(acct) == 29 and acct[:25] == bs.clean_lines(mine)
      and acct[1]["name"] == "Radon" and acct[13]["name"] == "24\" Culvert" and acct[24]["name"] == "Rock Excavation"
      and not any(x["name"] == "Propane Tank" for x in acct) and [x["name"] for x in acct[25:]] == NEW and added == NEW,
      "the client's edited sheet keeps its renamed, moved, removed and added lines and gains the four at its end")

# second load: no duplicates (nothing was written on the read)
check(json.loads(bs.store_path().read_text(encoding="utf-8")) == raw, "a read writes nothing")
lines2, _, added2 = bs.resolve_full(None)
check(lines2 == lines and added2 == NEW, "a second load is the same 29 lines, never 33")
again, add3 = bs.upgrade_lines(lines, 1)
check(len(again) == 29 and add3 == [], "even an already-upgraded list read as version 1 gains nothing (a line of that name is there)")

# the client removes one after the upgrade and renames another: it stays that way
edited = [dict(x) for x in acct if x["name"] != "Import Structural"]
for x in edited:
    if x["name"] == "Export Cut":
        x["name"] = "Haul Off"
got, scope = bs.save("estimator", edited)
st = json.loads(bs.store_path().read_text(encoding="utf-8"))
sv = json.loads(bs.versions_path().read_text(encoding="utf-8"))
check(sv["accounts"] == {"estimator": bs.TEMPLATE_VERSION} and sv["install"] is None
      and st["install"] == raw["install"] and "versions" not in st,
      "a save stamps only the seat's own sheet; the install sheet is written back as it was on disk, unstamped (critic RISK-2)")
check(bs.resolve_full(None)[2] == NEW, "so the install sheet's seats still get their own 'lines added' note")
for k in range(2):
    got, scope, added = bs.resolve_full("estimator")
    check(len(got) == 28 and not any(x["name"] == "Import Structural" for x in got) and "Haul Off" in [x["name"] for x in got]
          and not any(x["name"] == "Export Cut" for x in got) and added == [],
          f"load {k + 1} after the save: Import Structural stays deleted, Export Cut stays renamed Haul Off")
lt, un = totals(got)
check(lt.get("Haul Off") == 334.0 and ("Import Structural", 50.0) in un,
      "the deleted line's row is back under 'not on the bid sheet', visible with its 50 CY")

# ---- many seats in one file (critic RISK-2) ---------------------------------------------------
def fresh(store=None):
    """The fixture (or `store`) as the only bid-sheet state: no version sidecar."""
    if store is None:
        shutil.copy(FX, bs.store_path())
    else:
        bs.store_path().write_text(json.dumps(store), encoding="utf-8")
    if bs.versions_path().exists():
        bs.versions_path().unlink()


v185 = raw["install"]
multi = {"install": v185, "accounts": {"alice": v185, "bob": raw["accounts"]["estimator"], "carol": v185}}
fresh(multi)
a_lines, _, a_added = bs.resolve_full("alice")
a_keep = [x for x in a_lines if x["name"] != "Export Topsoil"]
bs.save("alice", a_keep)
st = json.loads(bs.store_path().read_text(encoding="utf-8"))
check(st["accounts"]["bob"] == multi["accounts"]["bob"] and st["accounts"]["carol"] == v185 and st["install"] == v185,
      "alice's Save writes bob's, carol's and the install sheet back exactly as they were on disk")
check(json.loads(bs.versions_path().read_text(encoding="utf-8")) == {"install": None, "accounts": {"alice": 2}},
      "and stamps only alice's")
for who in ("bob", "carol"):
    ln, _, add = bs.resolve_full(who)
    check(add == NEW and len(ln) == 29, f"{who} is upgraded on his own load, with his own note ({add})")
bs.save("bob", bs.resolve_full("bob")[0])
check(bs.resolve_full("bob")[2] == [] and bs.resolve_full("carol")[2] == NEW, "bob's own Save ends his note, not carol's")
ln, _, add = bs.resolve_full("alice")
check(add == [] and len(ln) == 28 and not any(x["name"] == "Export Topsoil" for x in ln), "alice's deleted line stays deleted")
# an undo to v1.8.5: its save rewrites bidsheet.json without any version (live bidsheet.save keeps
# only install / accounts) and with the 28 lines it read; the sidecar it never touches keeps the mark
undo = json.loads(bs.store_path().read_text(encoding="utf-8"))
undo["accounts"]["alice"] = ln
bs.store_path().write_text(json.dumps({"install": undo["install"], "accounts": undo["accounts"]}), encoding="utf-8")
ln2, _, add2 = bs.resolve_full("alice")
check(len(ln2) == 28 and add2 == [] and not any(x["name"] == "Export Topsoil" for x in ln2),
      "after an undo to v1.8.5 and back, alice's deleted Export Topsoil does not come back")
# a sheet stamped by a newer build is never stamped down; the old in-file key is still read
bs.versions_path().write_text(json.dumps({"install": None, "accounts": {"carol": 3}}), encoding="utf-8")
bs.save("carol", v185)
check(json.loads(bs.versions_path().read_text(encoding="utf-8"))["accounts"]["carol"] == 3, "a version 3 stays 3 on save")
fresh(dict(multi, versions={"install": None, "accounts": {"carol": 2}}))
check(bs.resolve_full("carol")[2] == [] and bs.resolve_full("alice")[2] == NEW,
      "a store from the first build of this fix (versions inside bidsheet.json) is still read per sheet")

# a client who already made a home for the net row on v1.8.5 is not given a second one
own = bs.clean_lines(raw["install"]) + [{"name": "Haul", "unit": "CY", "match": ["EXPORT", "HAUL"], "not": ["TOPSOIL"]}]
fresh({"install": own, "accounts": {}})
lines, _, added = bs.resolve_full(None)
check(added == ["Import Structural", "Export Topsoil", "Import Topsoil"] and len(lines) == 29,
      f"a v1.8.5 sheet with its own EXPORT line gets no Export Cut line beside it (added {added})")

# a full sheet (LINE_MAX) cannot grow: the rows stay visible, never dropped
full = [{"name": f"Line {i}", "unit": "EA", "match": [], "not": []} for i in range(bs.LINE_MAX)]
lines, added = bs.upgrade_lines(bs.clean_lines(full), 1)
check(len(lines) == bs.LINE_MAX and added == [] and len(bs.build(lines, pages)["unmatched"]) == len(items),
      "a sheet already at the line limit gains nothing and every row stays under 'not on the bid sheet'")

# the route the Settings editor reads
fresh()


class _Req:
    def __init__(self, user=None):
        self.scope = {"type": "http", "headers": []}
        self.cookies = {}
        self.headers = {}
        self.query_params = {}
        self._user = user


server._who = lambda req: getattr(req, "_user", None)
j = server.get_bidsheet(_Req("estimator"))
check(j["scope"] == "account" and len(j["lines"]) == 29 and j["added"] == NEW, "GET /api/bidsheet carries the added names")
j = server.post_bidsheet(server.BidsheetBody(lines=j["lines"]), _Req("estimator"))
check(len(j["lines"]) == 29 and j["added"] == [] and server.get_bidsheet(_Req("estimator"))["added"] == [],
      "after Save the note has nothing left to say")
check(server.get_bidsheet(_Req("nobody"))["scope"] == "install" and len(server.get_bidsheet(_Req("nobody"))["lines"]) == 29,
      "a seat on the install sheet sees the upgraded install sheet")

# the CSV route on a job: the old job's net rows land on the new lines
jid = "bsupjob"
d = server._job_dir(jid)
d.mkdir(parents=True, exist_ok=True)
server.JOBS[jid] = {"path": str(Path(JOBS) / "plan.pdf"), "pages": 1, "status": {}, "owner": None}
# an export this app wrote (src_marks): a v1.8.5 export's old Earthwork calculator rows count nowhere (the owner's decision,
# 2026-10-01; tests/test_legacy_earthwork.py) - the template's new lines are what is tested here
(d / "p0_elements.json").write_text(json.dumps({"items": items, "src_marks": 1}), encoding="utf-8")
fresh()
body = bytes(server.bidsheet_csv(jid, _Req(None), []).body).decode("utf-8")
check("4,Site Cut,CY,1334,2," in body and "26,Export Cut,CY,334,1," in body and "27,Import Structural,CY,50,1," in body
      and "28,Export Topsoil,CY,7,1," in body and "29,Import Topsoil,CY,12,1," in body and ",Property line,LF,812.4,1,p1" in body,
      "the CSV of a v1.8.5 seat: 1,334 / 334 / 50 / 7 / 12 on lines 4 and 26-29, the property line still listed")

# the Settings editor says so, and the page loads the new script
js = (ROOT / "webui" / "app.js").read_text(encoding="utf-8")
check("added at the end after the update" in js and "svBs.added" in js, "Settings names the lines it added until the seat saves")
html = (ROOT / "webui" / "index.html").read_text(encoding="utf-8")
check(int(__import__("re").search(r"app\.js\?v=(\d+)", html).group(1)) >= 254, "app.js cache buster moved")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all bidsheet-upgrade checks passed")
