"""THE SCALE GATE'S SCORING (takeoff_engine.scale_gate), without a PDF.

The gate passes only with zero CONFIRMED-wrong pages; a proposed row never
fails it; a missing PDF is a SKIP that is named, never dropped. The page
runs themselves (detect_page + dimension check + profile) are exercised by
running the gate (see out/verify/2026-10-02/scale-verdict/).

Run:  python tests/test_scale_gate.py
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from takeoff_engine import scale_gate as G  # noqa: E402

FAILS = []


def check(ok, msg):
    print(("ok   " if ok else "FAIL ") + msg)
    if not ok:
        FAILS.append(msg)


def cap(v):
    return {"text": f'1" = {v}\'', "ft_per_paper_inch": v, "source": "text",
            "candidates": [{"text": f'1" = {v}\'', "ft_per_paper_inch": v}],
            "bar_ft_per_paper_inch": None, "pick": {"why": "single"}}


def app(scale=None, sugg=None, dim=None, profiles=None):
    return {"ok": True, "scale": scale, "suggestions": sugg, "dimcheck": dim,
            "profiles": profiles or [], "s": 0.1}


row = {"document": "a.pdf", "page": 0, "truth": 20.0, "confirmed": True, "proposed": False}
both = dict(cap(20.0), bar_ft_per_paper_inch=20.1)
check(G.judge(row, app(both), "")["result"] == "CONFIRMED-right", "caption + bar at the truth: CONFIRMED-right")
wrong = dict(cap(40.0), bar_ft_per_paper_inch=40.1)
check(G.judge(row, app(wrong), "")["result"] == "CONFIRMED-wrong", "caption + bar agreeing on a wrong value: CONFIRMED-wrong")
check(G.judge(row, app(cap(20.4)), "")["result"] == "UNCHECKED-right", "20.4 vs truth 20 is within 2 %: right")
check(G.judge(row, app(cap(20.5)), "")["result"] == "UNCHECKED-wrong", "20.5 vs truth 20 is 2.5 %: wrong")
j = G.judge(row, app(dict(cap(10.0), bar_ft_per_paper_inch=20.0)), "")
check(j["result"] == "CONFLICT" and j["truth_offered"] is True,
      "CONFLICT carries no value; the gate says whether the truth is among the choices")
check(G.judge(row, app(), "")["result"] == "NONE", "no witness: NONE")
check(G.judge(row, None, "PDF not found")["result"] == "SKIP", "a missing PDF is a SKIP with its reason")
check(G.judge(row, {"ok": False, "error": "boom", "s": 1}, "")["result"] == "ERROR", "a crash is an ERROR, kept")
check(G.judge(dict(row, truth=None), app(cap(20.0)), "")["result"] == "NO-TRUTH",
      "a proposed row with no value is NO-TRUTH, not scored")
pr = {"document": "x.pdf", "page": 0, "truth": 8.0}
j = G.judge(pr, app(dict(cap(4.0), bar_ft_per_paper_inch=4.0)), "")
check(j["result"] == "CONFIRMED-wrong" and j["result_profile"] == "CONFIRMED-wrong",
      "both columns scored")
t = G.totals([j, G.judge(row, None, "PDF not found")], "_profile")
check(t["CONFIRMED-wrong"] == 1 and t["SKIP"] == 1, "totals count SKIP rows in the profile-less column too")

# truth file loading: a confirmed file may not hold proposed rows
with tempfile.TemporaryDirectory() as d:
    p = pathlib.Path(d) / "t.json"
    p.write_text(json.dumps({"rows": [{"document": "a.pdf", "page_index": 1, "ft_per_paper_inch": 20,
                                       "confirmed_by_human": False}]}))
    try:
        G.load_truth(p, proposed=False)
        ok = False
    except SystemExit:
        ok = True
    check(ok, "a truth file with an unconfirmed row is refused")
    p.write_text(json.dumps({"entries": [{"document": "dir/a.pdf", "page_index": 2,
                                          "proposed_ft_per_paper_inch": 40.0, "confirmed_by_human": False}]}))
    r = G.load_truth(p, proposed=True)
    check(r[0]["truth"] == 40.0 and r[0]["page"] == 2 and r[0]["proposed"],
          "the census's proposed shape loads (proposed_ft_per_paper_inch)")
    # resolve: missing -> reason; by name with a wrong hash -> reason
    (pathlib.Path(d) / "sub").mkdir()
    (pathlib.Path(d) / "sub" / "b.pdf").write_bytes(b"%PDF-1.4 x")
    got, why = G.resolve({"document": "b.pdf", "sha256": "0" * 64}, pathlib.Path(d), [pathlib.Path(d)], {})
    check(got is None and "sha256" in why, "a file found by name with another hash is not used")
    got, why = G.resolve({"document": "b.pdf", "sha256": None}, pathlib.Path(d), [pathlib.Path(d)], {})
    check(got is not None and got.name == "b.pdf", "found by name under --plans-dir")
    got, why = G.resolve({"document": "nope/c.pdf"}, pathlib.Path(d), [], {})
    check(got is None and why == "PDF not found", "a missing relative path says so")
# CRITIC-scale-1 MINOR 4 / 5: doctored truth files
with tempfile.TemporaryDirectory() as d:
    dd = pathlib.Path(d)
    (dd / "b.pdf").write_bytes(b"%PDF-1.4 x")
    t = dd / "t.json"
    for bad in (None, 0, -40, "20", float("nan")):
        t.write_text(json.dumps({"rows": [{"document": "b.pdf", "page_index": 0, "ft_per_paper_inch": bad,
                                           "proposed_ft_per_paper_inch": 20, "confirmed_by_human": True}]}))
        rows = G.load_truth(t, proposed=False)
        j = G.judge(rows[0], None, "")
        check(rows[0]["bad_truth"] and j["result"] == "ERROR" and rows[0]["truth"] is None,
              f"a human-confirmed row with truth {bad!r} is an ERROR (never NO-TRUTH, never the proposed value)")
        code = G.main(["--truth", str(t), "--plans-dir", str(dd)])
        check(code == 1, f"...and it fails the gate even without --require (truth {bad!r}): exit {code}")
    t.write_text(json.dumps({"rows": []}))
    check(G.main(["--truth", str(t), "--require"]) == 1, "an empty truth file fails under --require")
    check(G.main(["--truth", str(dd / "missing.json"), "--require"]) == 1, "a missing truth file fails under --require")
    # the cache is keyed by name AND hash: a second row naming the same file with a wrong hash is judged on its own
    good = __import__("hashlib").sha256(b"%PDF-1.4 x").hexdigest()
    cache = {}
    r1 = G.resolve({"document": "b.pdf", "sha256": good}, dd, [dd], cache)
    r2 = G.resolve({"document": "b.pdf", "sha256": "0" * 64}, dd, [dd], cache)
    check(r1[0] is not None and r2[0] is None and "sha256" in r2[1],
          "two rows naming one file: the matching hash resolves, the wrong one is a SKIP of its own")
    cache = {}
    r2 = G.resolve({"document": "b.pdf", "sha256": "0" * 64}, dd, [dd], cache)
    r1 = G.resolve({"document": "b.pdf", "sha256": good}, dd, [dd], cache)
    check(r1[0] is not None, "...in either order (a bad first row no longer hides a good second one)")
# an app answer the verdict cannot read is that row's ERROR, not the gate's crash
j = G.judge(row, app({"ft_per_paper_inch": 20.0, "source": "magic", "candidates": []}), "")
check(j["result"] == "ERROR" and "verdict could not be worked out" in j["why"], "an unknown scale source: one ERROR row")
real = json.loads((G.DEFAULT_TRUTH).read_text(encoding="utf-8"))
check(len(real["rows"]) == 8 and all(r["confirmed_by_human"] for r in real["rows"]),
      "ground-truth/scales.json holds the 8 human-confirmed rows only")

print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
