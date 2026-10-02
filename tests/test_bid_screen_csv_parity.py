"""THE SCREEN AND THE CSV FILE EVERY ROW THE SAME WAY (live proof 2026-10-01, B1).

The bid sheet is computed twice: on screen by webui/app.js bsGroupRows (the
operator's working takeoff, before Approve) and for the CSV / report by
takeoff_engine.bidsheet.build (the approved export). The live proof found a
typed sub-item, "Removal of existing sidewalk" under P1-WALK, on line 18 on
screen (it rode with its parent) and under "Not on the bid sheet" in the CSV
(filed by its exported name). Two implementations stay, so this test runs
BOTH over one generated row set and fails on any difference:

  - every name in tests/fixtures/bid_parity_names.json (critic 24's near-miss
    set, the bid-content critic's 133 rows, the live proof's sub-items), the
    shared matcher cases and the rows fixture;
  - as calculator rows in every unit, as measured line families (feet), and
    as declared sub-items under a measured parent and a calculator parent;
  - on the default sheet.

Compared: each row's line (or "not on the bid sheet"), whether it is summed,
what it counts, and every line's total and not-summed units.

    python tests/test_bid_screen_csv_parity.py     (fails on 9a4f6e5)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="bspar_")

from takeoff_engine import bidsheet as bs  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


fx = ROOT / "tests" / "fixtures"
names = set(json.loads((fx / "bid_parity_names.json").read_text(encoding="utf-8"))["names"])
names |= {c["label"] for c in json.loads((fx / "bidsheet_cases.json").read_text(encoding="utf-8"))["cases"]}
for c in json.loads((fx / "bidsheet_rows.json").read_text(encoding="utf-8"))["cases"]:
    names |= {it["label"] for it in c["items"]}
names = sorted(names)
UNITS = ["CY", "SF", "LF", "EA", "LS", "SY", "TON", "GAL", "FT", "feet", "lf", "sf", "pt"]
SUB_UNITS = ["CY", "SF", "LF", "EA", "LS", "lb", "FT"]

rows = []
for nm in names:
    for u in UNITS:
        rows.append({"label": nm, "unit": u, "qty": 7.0, "cnt": 1, "sids": [], "isCalc": True})
    rows.append({"label": nm, "unit": "ft", "qty": 11.0, "cnt": 2, "sids": []})
# sub-items: every name, in several units, under a measured parent and a calculator parent
for parent in ({"label": "P1-WALK", "unit": "ft", "qty": 300.0, "cnt": 3, "sids": []},
               {"label": "Concrete slab", "unit": "SF", "qty": 900.0, "cnt": 1, "sids": [], "isCalc": True}):
    subs = [{"rec": {"name": nm, "unit": u, "price": None, "mode": "fixed"}, "qty": 5.0}
            for nm in names for u in SUB_UNITS]
    subs.append({"rec": {"name": "zero qty", "unit": "SF", "price": None}, "qty": 0})
    rows.append(dict(parent, subs=subs))

work = Path(tempfile.mkdtemp(prefix="bspar_w_"))
lines = bs.default_lines()
# PUNCTUATED MATCH WORDS (UI verify 2026-10-02 #2): a seat's own line with words typed with a
# hyphen, slash, underscore, dot, lower case and doubled spaces, and rows that carry them, so both
# sides clean the typed word the way they clean the row name
PUNCT_N = len(lines) + 1
PUNCT_WORDS = ["O-PIPE", "=storm/drain", "fire_line", "W.M.", "=DUCT  BANK", "-", "c-strm-x"]
lines.append({"n": PUNCT_N, "name": "Storm pipe", "unit": "LF", "match": PUNCT_WORDS, "not": ["=no-go"]})
PUNCT_ROWS = ["O-PIPE", "C-STRM-O-PIPE", "o pipe", "STORM DRAIN/INLET", "FIRE LINE 6 IN", "Fire_Line",
              "WM 8 IN", "W.M. 8", "DUCT BANK", "DUCT-BANK-2", "C-STRM-X", "O-PIPE NO-GO", "OPIPE"]
rows += [{"label": nm, "unit": "ft", "qty": 13.0, "cnt": 1, "sids": []} for nm in PUNCT_ROWS]
(work / "in.json").write_text(json.dumps({"rows": rows, "lines": lines}), encoding="utf-8")
node = shutil.which("node")
r = subprocess.run([node, str(ROOT / "tests" / "bid_screen_group.js"), str(work / "in.json"), str(work / "out.json")],
                   capture_output=True, text=True)
check(r.returncode == 0, "the screen's grouping ran" + ("" if r.returncode == 0 else ": " + r.stderr[-800:]))
scr = json.loads((work / "out.json").read_text(encoding="utf-8"))

# the CSV side: the same rows as Approve exports them, on one approved page (the screen shows one
# sheet's rows, so a trench "(run)" row replaces its family on both sides alike)
res = bs.build(lines, [{"page": 0, "items": scr["export"]}])
csv_rows = []
for L in res["lines"]:
    for x in L["rows"]:
        csv_rows.append((x["label"], x["unit"], L["n"], bool(x["ok"]), x["counted"]))
for x in res["unmatched"]:
    csv_rows.append((x["label"], x["unit"], None, False, x["counted"]))
scr_rows = [(x["label"], x["unit"], x["n"], bool(x["ok"]), x["counted"]) for x in scr["rows"]]

n_sub = sum(1 for e in scr["export"] if " (sub-item of " in e["label"])
check(len(scr_rows) == len(csv_rows) == len(scr["export"]) and n_sub >= len(names) * len(SUB_UNITS) * 2,
      f"both sides filed every row ({len(scr_rows)} on screen, {len(csv_rows)} in the CSV, {n_sub} of them sub-items)")
a, b = Counter(scr_rows), Counter(csv_rows)
diff = sorted(set((a - b).elements()) | set((b - a).elements()), key=str)[:12]
check(a == b, "every row files on the same line, summed the same, counting the same, on screen and in the CSV"
      + (f" - first differences: {diff}" if diff else ""))
bad = []
for L in res["lines"]:
    s = scr["lines"].get(str(L["n"])) or {"sum": 0.0, "other": {}}
    q = L["qty"] if L["qty"] is not None else 0.0
    if abs(s["sum"] - q) > 0.05 or s["other"] != L["other"]:
        bad.append((L["n"], s, q, L["other"]))
check(not bad, f"every line's total and not-summed units agree ({len(res['lines'])} lines)" + (f" - {bad[:4]}" if bad else ""))

# the live proof's row, named: off line 18 on both sides
lp = [x for x in scr_rows if x[0] == "Removal of existing sidewalk (sub-item of P1-WALK)" and x[1] == "SF"]
check(lp and all(x[2] is None for x in lp), f"the live proof's typed sub-item is under 'not on the bid sheet' on screen too ({lp})")

# the verifier's case, named: a line matching "O-PIPE" takes the O-PIPE rows on both sides
want = {"O-PIPE": PUNCT_N, "C-STRM-O-PIPE": PUNCT_N, "o pipe": PUNCT_N, "FIRE LINE 6 IN": PUNCT_N,
        "DUCT-BANK-2": PUNCT_N, "C-STRM-X": PUNCT_N, "O-PIPE NO-GO": None, "OPIPE": None}
got_s = {x[0]: x[2] for x in scr_rows if x[0] in want and x[1] in ("ft", "LF")}
got_c = {x[0]: x[2] for x in csv_rows if x[0] in want and x[1] in ("ft", "LF")}
check(got_s == want and got_c == want, f"punctuated match words file the same rows on screen and in the CSV "
      f"(screen {got_s}, CSV {got_c}, want {want})")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all screen = CSV parity checks passed")
