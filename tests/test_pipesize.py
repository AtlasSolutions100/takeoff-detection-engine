"""PIPE SIZE ON RUNS (2026-09-16; Wave 2 item 8; Beaumont prices storm per
size, Pierce sets the trench width by it). size_token (the bid view's rule
ported), family_sizes from the name / inline codes / leaders weighted by
run length with the named / single / mixed verdicts, the route with the
callouts cache and the UI's names, and the bid sheet's by-size breakdown
on LF lines.

    python tests/test_pipesize.py     (fails on 4d15dc4)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = tempfile.mkdtemp(prefix="ps_")
os.environ["TAKEOFF_JOBS_DIR"] = JOBS
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="ps_lg_")

import server  # noqa: E402
from takeoff_engine import bidsheet as bs  # noqa: E402
from takeoff_engine.pipesize import family_sizes, size_token  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


# ---- size_token --------------------------------------------------------------------------
for text, want in (("SE-P-UT-WATER-10''", '10"'), ("18\" CMP CULVERT", '18"'), ("12-IN RCP", '12"'), ("8 inch PVC", '8"'),
                   ("7'-2\" AREA INLET", None), ("C-TOPO-12", None), ("218\" RCP", None), ("Storm 8” PVC", '8"'), (None, None)):
    check(size_token(text) == want, f"size_token({text!r}) -> {want!r} (got {size_token(text)!r})")

# ---- family_sizes ------------------------------------------------------------------------
def ch(cid, sid, L, inline=()):
    return {"chain_id": cid, "style_id": sid, "class": "solid", "length_pt": L, "inline_labels": list(inline), "points": [[0, 0], [L, 0]]}


chains = [ch(1, 1, 100, ["8\"SD"]), ch(2, 1, 100, ["8\"SD", "WS"]), ch(3, 1, 150),           # storm: two inline codes + a leader on chain 3
          ch(4, 2, 200), ch(5, 2, 60),                                                        # water: the size is in the NAME
          ch(6, 3, 100, ["8\"SS"]), ch(7, 3, 50, ["12\"SS"]),                                 # sewer: mixed
          ch(8, 4, 80),                                                                       # nothing anywhere
          ch(9, 5, 40, ["E8\"W"]),                                                            # an existing 8" water code on a family named 10''
          ch(11, 6, 60), ch(12, 6, 60), ch(13, 6, 60), ch(14, 6, 60), ch(15, 6, 10),          # a family with one short leader-sized run
          ch(16, 7, 300),                                                                     # a contour a leader's tip landed on
          {"chain_id": 10, "style_id": 1, "class": "annotation", "length_pt": 999, "inline_labels": ["8\"SD"], "points": [[0, 0], [1, 1]]}]
styles = [{"style_id": 1, "suggested_label": "SE-P-UT-STORM", "layer": ""}, {"style_id": 2, "suggested_label": "SE-P-UT-WATER-10''", "layer": ""},
          {"style_id": 3, "suggested_label": "SE-P-UT-SS", "layer": ""}, {"style_id": 4, "suggested_label": "solid solid", "layer": "C-TOPO"},
          {"style_id": 5, "suggested_label": "solid solid", "layer": "P-UT-WATER-10''"}, {"style_id": 6, "suggested_label": "solid solid", "layer": ""},
          {"style_id": 7, "suggested_label": "EXISTING CONTOUR (legend)", "layer": ""}]
callouts = [{"text": "8\" PVC FL=586.30", "target_chain_id": 3}, {"text": "218\" RCP", "target_chain_id": 3},
            {"text": "12\" RCP", "target_chain_id": None}, {"text": "7'-2\"", "target_chain_id": 4},
            {"text": "18\" RCP FL=576.93", "target_chain_id": 15}, {"text": "30\" RCP", "target_chain_id": 16}]
f = family_sizes(chains, styles, None, callouts, ft_per_pt=0.5)
by = {r["style_id"]: r for r in f}
check(set(by) == {1, 2, 3, 5, 6}, f"families with a size anywhere are listed; the one with none and the contour a leader landed on are not (got {sorted(by)})")
check(by[6]["verdict"] == "single" and by[6]["suggest"] is None and by[6]["by_size"]['18"']["ft"] == 5.0, f"one short leader-sized run (5 of 125 ft) is single but too little to name the family (got {by[6]})")
s1 = by[1]
check(s1["verdict"] == "single" and s1["suggest"] == '8"' and s1["by_size"] == {'8"': {"ft": 175.0, "chains": 3, "inline": 2, "leader": 1}} and s1["no_size_ft"] == 0.0 and s1["total_ft"] == 175.0, f"storm: one size from two inline codes and a leader, weighted by feet at 0.5 ft/pt; the annotation chain and the OCR '218\"' are ignored (got {s1})")
check(by[2]["verdict"] == "named" and by[2]["name_size"] == '10"' and by[2]["by_size"] == {} and by[2]["no_size_ft"] == 130.0 and by[2]["agrees"] is None, f"water: the size is in the name; the 7'-2\" leader is not a size (got {by[2]})")
check(by[3]["verdict"] == "mixed" and by[3]["suggest"] is None and list(by[3]["by_size"]) == ['8"', '12"'], f"sewer: two sizes on its runs -> mixed, no suggestion, biggest run first (got {by[3]})")
check(by[5]["verdict"] == "named" and by[5]["name_size"] == '10"' and by[5]["agrees"] is False and list(by[5]["by_size"]) == ['8"'], "a family named 10'' by its layer whose run prints E8\"W is flagged: the name and the run disagree")
check([r["style_id"] for r in f] == [1, 2, 6, 3, 5], f"longest family first (175, 130, 125, 75, 20 ft; got {[r['style_id'] for r in f]})")
f2 = family_sizes(chains, styles, {1: 'SE-P-UT-STORM 8"'}, callouts, 0.5)
check({r["style_id"]: r["verdict"] for r in f2}[1] == "named", "the UI's own name for the family (with a size) makes it named")
check(family_sizes([], styles) == [] and family_sizes(chains, [])[0:0] == [], "no chains or no styles: nothing")

# ---- the route --------------------------------------------------------------------------------
jid = "psroute1"
d = server._job_dir(jid)
d.mkdir(parents=True, exist_ok=True)
server.JOBS[jid] = {"path": str(Path(JOBS) / "x.pdf"), "pages": 4, "status": {}, "owner": None}
try:
    server.pipesizes(jid, 3, server.PipeSizesBody())
    check(False, "no detect result must 404")
except Exception as e:
    check(getattr(e, "status_code", None) == 404, "no detect result -> 404")
(d / "p3_result.json").write_text(json.dumps({"page": 3, "scale": {"ft_per_paper_inch": 36.0, "text": "1\" = 36'"}, "styles": styles, "chains": chains, "engine_sig": "t"}))
j = json.loads(bytes(server.pipesizes(jid, 3, server.PipeSizesBody()).body))
check(j["leaders"] is None and "open Sheet callouts" in (j["leaders_note"] or "") and j["unit"] == "ft", "without a callouts cache the route says the leaders are not read yet")
fam = {r["style_id"]: r for r in j["families"]}
check(fam[1]["verdict"] == "single" and fam[1]["by_size"]['8"']["leader"] == 0 and fam[1]["total_ft"] == 175.0, "inline codes alone size the storm family, at the sheet's scale")
(d / "p3_callouts.json").write_text(json.dumps({"callouts": callouts}))
j = json.loads(bytes(server.pipesizes(jid, 3, server.PipeSizesBody(labels={"1": 'SE-P-UT-STORM 8"'})).body))
fam = {r["style_id"]: r for r in j["families"]}
check(j["leaders"] == 6 and fam[1]["by_size"]['8"']["leader"] == 1 and fam[1]["verdict"] == "named", f"with the cache the leader counts; the UI's names are honoured (got leaders {j['leaders']}, {fam[1]['verdict']})")
try:
    server.pipesizes("nosuchjob", 0, server.PipeSizesBody())
    check(False, "unknown job must 404")
except Exception as e:
    check(getattr(e, "status_code", None) == 404, "unknown job -> 404")

# ---- the bid sheet's by-size breakdown on LF lines --------------------------------------------
pages = [{"page": 2, "items": [{"label": "SE-P-UT-WATER-10''", "count": 1, "length_ft": 1756.4}, {"label": "SE-P-UT-WATER-6''", "count": 5, "length_ft": 154.0},
                                {"label": "SE-P-UT-WATER-SRV", "count": 2, "length_ft": 222.0}, {"label": "Perimeter Drain", "count": 1, "length_ft": None, "qty": 420, "unit": "LF"}]}]
r = bs.build(bs.DEFAULT_LINES, pages)
L = {x["n"]: x for x in r["lines"]}
check(L[16]["by_size"] == {'10"': 1756.4, '6"': 154.0} and L[16]["no_size_ft"] == 222.0, f"Water: feet per size from the row names, the unsized rows apart (got {L[16].get('by_size')}, {L[16].get('no_size_ft')})")
check(L[7]["by_size"] == {} and L[7]["no_size_ft"] == 420.0, "a line whose rows carry no size has an empty breakdown")
csv = bs.to_csv(r, "plan.pdf", "default")
line16 = [l for l in csv.splitlines() if l.startswith("16,Water,")][0]
check("by size: 10 in 1756.4 ft; 6 in 154 ft; no size 222 ft" in line16, f"the CSV prints the breakdown on the line, sizes as '10 in' so the inch mark never doubles inside a quoted cell (got {line16[-90:]})")
check(not any("by size" in l for l in csv.splitlines() if l.startswith("7,Perimeter Drain")), "no breakdown printed when no row carries a size")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all pipesize checks passed")
