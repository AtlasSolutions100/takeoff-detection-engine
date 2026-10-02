"""The profile reader and the run builder (fix/trenching, 2026-09-26;
PLAN.md 7.1 / 7.3, defects D19 and the manhole-to-manhole runs).

Two layers, both dependency-free:

  1. SYNTHETIC. A profile drawn with pymupdf on a blank page - station and
     elevation labels, grid rules, a dashed existing-ground line, a solid
     finished-grade line, a pipe as two lines a pipe-diameter apart, a flat
     stretch of pipe, a leader from a label crossing the ground - and the
     read must give the depths the drawing was built from at every station
     within 0.05 ft, name the lines from their labels, ignore the grid and
     the leader, and keep the flat pipe (a grid-rule filter once deleted a
     flat pipe on a real sheet). A synthetic plan with a tee, a bend and two
     structure labels must come back as the runs an estimator would list.

  2. THE SHEETS, when the corpus is on this machine (skipped, and said so,
     when it is not): the two profile sheets the audit measured by hand.
     Gardiner W-1 (Plans 2, engine page index 297) depth to invert every
     station: min 7.11, max 10.14, mean 8.76 ft (the audit's independent
     read, followup-2/engineer-truth/gardiner_w1_profile_depth.json) and
     bands 6-8 / 8-10 / 10-12 of 146 / 331 / 38 LF once mapped onto the
     514.6-ft plan run. The Barn C4.2 storm profile (client_meet, page index
     11): the 24 printed inverts on the graph within 0.1 ft of the read, and
     the audit's window (sta 10-270 every 10 ft) giving a mean depth of 3.79
     ft to the finished grade.

    python tests/test_profile_read.py
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402

from takeoff_engine import profile as P  # noqa: E402
from takeoff_engine import runs as R  # noqa: E402
from takeoff_engine.extract import extract_strokes, page_lines  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


# ---- 1. a synthetic profile -----------------------------------------------------------
# page 800 x 500 pt. Stations 0..400 ft along x at 1.5 pt/ft (1" = 48') from
# x = 100; elevations 100..130 ft along y at 6 pt/ft (1" = 12'), 130 at y = 100.
X0, PT_PER_FT_H = 100.0, 1.5
Y130, PT_PER_FT_V = 100.0, 6.0
def X(s): return X0 + s * PT_PER_FT_H
def Y(e): return Y130 + (130.0 - e) * PT_PER_FT_V
def ground(s): return 124.0 - 0.01 * s + 2.0 * math.sin(s / 60.0)          # existing, dashed
def grade(s): return 125.0 - 0.012 * s                                      # finished, solid
def invert(s): return 118.0 - 0.02 * s if s < 250 else 113.0                 # flat past 2+50

doc = pymupdf.open()
pg = doc.new_page(width=800, height=500)
sh = pg.new_shape()
# grid rules (horizontal every 10 ft, vertical every 50 ft) and their labels
for e in (100, 110, 120, 130):
    sh.draw_line((X(0), Y(e)), (X(400), Y(e)))
for s in range(0, 401, 50):
    sh.draw_line((X(s), Y(100)), (X(s), Y(130)))
sh.finish(color=(0.6, 0.6, 0.6), width=0.3, closePath=False)
sh.commit()
for e in (100, 110, 120, 130):
    pg.insert_text((X(0) - 30, Y(e) + 3), str(e), fontsize=7)
for s in range(0, 401, 50):
    pg.insert_text((X(s) - 8, Y(100) + 12), f"{s // 100}+{s % 100:02d}", fontsize=7)
# existing ground: dashes 6 ft long every 8 ft, in a thin gray pen
sh = pg.new_shape()
s = 0.0
while s < 400:
    pts = [(X(q), Y(ground(q))) for q in (s, s + 2, s + 4, min(s + 6, 400))]
    for a, b in zip(pts, pts[1:]):
        sh.draw_line(a, b)
    s += 8
sh.finish(color=(0.4, 0.4, 0.4), width=0.4, closePath=False)
sh.commit()
# finished grade: solid red
sh = pg.new_shape()
pts = [(X(q), Y(grade(q))) for q in range(0, 401, 10)]
for a, b in zip(pts, pts[1:]):
    sh.draw_line(a, b)
sh.finish(color=(1, 0, 0), width=0.7, closePath=False)
sh.commit()
# the pipe: invert and crown 0.667 ft apart (an 8-in pipe), black 0.6, from 0+20 to 3+80
sh = pg.new_shape()
for off in (0.0, 0.667):
    pts = [(X(q), Y(invert(q) + off)) for q in list(range(20, 250, 10)) + [250, 300, 350, 380]]
    for a, b in zip(pts, pts[1:]):
        sh.draw_line(a, b)
sh.finish(color=(0, 0, 0), width=0.6, closePath=False)
sh.commit()
# labels with leaders: the ground's leader shares the ground's pen and
# crosses the pipe zone; the grade's label sits on the line
pg.insert_text((X(300), Y(129)), "EXISTING GROUND", fontsize=7)
sh = pg.new_shape()
sh.draw_line((X(300) + 30, Y(128.5)), (X(330), Y(ground(330))))
sh.finish(color=(0.4, 0.4, 0.4), width=0.4, closePath=False)
sh.commit()
pg.insert_text((X(150), Y(grade(150)) - 3), "FINAL GRADE", fontsize=7)
pg.insert_text((X(200), Y(invert(200)) + 10), '8" PVC', fontsize=7)
pg.insert_text((X(20) - 10, Y(100) + 24), "GRADE BREAK STA = 0+20.00 ELEV = 117.60", fontsize=6)

units = extract_strokes(pg)
regs = P.find_profiles(pg, units)
check(len(regs) == 1, f"one profile region on the synthetic page ({len(regs)})")
rd = P.read_profile(pg, regs[0], units) if regs else None
check(rd is not None, "the region reads")
if rd:
    for ln in rd.surfaces + rd.pipes:
        print(f"   line {ln.role:8s} {ln.kind:9s} {ln.pen:40s} cov {ln.coverage:.2f} sta {ln.sta0:.0f}-{ln.sta1:.0f} two {ln.two_line} od {ln.od_ft} label {ln.label!r}")
if rd:
    st = rd.station
    check(abs(st.ft_per_paper_inch - 48.0) < 0.05, f"station axis 1\" = 48' ({st.ft_per_paper_inch:.3f}), snapped to the vertical rules")
    check(abs(rd.elevation.ft_per_paper_inch - 12.0) < 0.05, f"elevation axis 1\" = 12' ({rd.elevation.ft_per_paper_inch:.3f})")
    kinds = [(s.kind, s.label) for s in rd.surfaces]
    check(len(rd.surfaces) == 2 and rd.surfaces[0].kind == "finished" and rd.surfaces[1].kind == "existing",
          f"two surfaces, finished first, named off the sheet: {kinds}")
    check(len(rd.pipes) == 1 and rd.pipes[0].two_line and abs(rd.pipes[0].od_ft - 0.667) < 0.03,
          f"one two-line pipe {rd.pipes[0].od_ft if rd.pipes else None} ft tall")
    check(rd.pipes and (rd.pipes[0].label or "").startswith('8"'), f"the pipe carries its label ({rd.pipes[0].label if rd.pipes else None})")
    # the finished-grade series, station by station, against the drawing
    ser = P.depth_series(rd, 0, 0)
    worst = max(abs(d - (grade(s) - invert(s))) for s, d in ser) if ser else 99
    check(ser and ser[0][0] <= 21 and ser[-1][0] >= 379 and worst < 0.05,
          f"finished grade to invert reads the drawing at {len(ser)} stations {ser[0][0] if ser else '-'}..{ser[-1][0] if ser else '-'}, worst {worst:.3f} ft")
    flat = [d for s, d in ser if 300 <= s <= 370]
    check(len(flat) >= 60, f"the FLAT stretch of pipe (3+00 to 3+70) is read ({len(flat)} stations) - a grid-rule filter must not eat it")
    ser2 = P.depth_series(rd, 1, 0)
    errs2 = [abs(d - (ground(s) - invert(s))) for s, d in ser2]
    worst2 = max(errs2) if errs2 else 99
    mean2 = sum(errs2) / len(errs2) if errs2 else 99
    # the leader's tail converges onto the line: its last stations are within
    # the chord test's 0.8 ft, so the worst station is bounded by that and the
    # mean error over the run stays at the sampling noise
    check(ser2 and worst2 < 0.8 and mean2 < 0.03 and len(ser2) >= 340,
          f"existing ground (dashed, interpolated across its gaps) to invert: {len(ser2)} stations, mean error {mean2:.3f} ft, worst {worst2:.3f} ft - the leader across it is refused")
    cb = [c for c in rd.checks if c["kind"] == "callout"]
    check(cb and abs(cb[0]["diff"]) < 0.05 and "pipe" in cb[0]["line"],
          f"the printed grade break is matched to the pipe invert within 0.05 ft ({cb[0] if cb else None})")
doc.close()

# ---- 1b. a synthetic plan with a tee, a bend and two structures -------------------------
doc = pymupdf.open()
pg = doc.new_page(width=600, height=400)
chains = [
    # main: MH-A at (100,200) east to a bend at (300,200), then north-east to (400,120)
    {"chain_id": 1, "style_id": 5, "class": "dash1", "dash_signature": "d", "length_pt": 200.0,
     "points": [[100, 200], [300, 200]], "inline_labels": ['8"SS']},
    {"chain_id": 2, "style_id": 5, "class": "dash1", "dash_signature": "d", "length_pt": 128.06,
     "points": [[300, 200], [400, 120]], "inline_labels": []},
    # lateral: ends ON the main at (200,200), from (200,320)
    {"chain_id": 3, "style_id": 5, "class": "dash1", "dash_signature": "d", "length_pt": 120.0,
     "points": [[200, 320], [200, 200]], "inline_labels": ['4"SS']},
    # a leader from the label "SSMH-2" to the structure at (400,120), another family
    {"chain_id": 9, "style_id": 7, "class": "solid", "dash_signature": "solid", "length_pt": 40.0,
     "points": [[430, 90], [401, 119]], "inline_labels": []},
    {"chain_id": 10, "style_id": 7, "class": "solid", "dash_signature": "solid", "length_pt": 40.0,
     "points": [[70, 170], [99, 199]], "inline_labels": []},
]
styles = [{"style_id": 5, "suggested_label": "PROPOSED SANITARY SEWER", "layer": "C-SSWR"},
          {"style_id": 7, "suggested_label": "leaders", "layer": "C-ANNO"}]
words = [(430, 80, 470, 90, "SSMH-2 RIM=101.0"), (40, 160, 70, 170, "SSMH-1"), (150, 205, 175, 212, "SS-1")]
out = R.build_runs(chains, styles, [5], 20.0 / 72.0, words=words, split_at_structures=True)
names = sorted((r["name"], r["from"], r["to"], round(r["length_ft"], 1), r["size"]) for r in out["runs"])
check(out["system"] == "SS", f"system SS from the family name ({out['system']})")
check(out["structures_on_runs"] == 2, f"two structures on the runs from the two labels' leaders ({out['structures_on_runs']})")
check(any({r["from"], r["to"]} == {"SSMH-1", "SSMH-2"} for r in out["runs"]),
      f"the main runs manhole to manhole THROUGH the tee (straightest pair): {names}")
main = [r for r in out["runs"] if {r["from"], r["to"]} == {"SSMH-1", "SSMH-2"}]
check(main and abs(main[0]["length_ft"] - (200 + 128.06) * 20 / 72) < 0.1, "...and measures the whole main")
check(main and main[0]["size"] == '8"' and main[0]["size_from"] == "inline", f"...sized 8\" off its inline code ({main[0]['size'] if main else None})")
check(main and main[0]["name"] == "SS-1" and main[0]["name_from"] == "sheet", f"...named SS-1 off the sheet ({main[0]['name'] if main else None})")
lat = [r for r in out["runs"] if r["size"] == '4"']
check(len(lat) == 1 and lat[0]["to"] == "junction" and abs(lat[0]["length_ft"] - 120 * 20 / 72) < 0.1,
      f"the lateral is its own run, ending at the junction ({[(r['from'], r['to']) for r in lat]})")
out2 = R.build_runs(chains, styles, [5], 20.0 / 72.0, words=words, split_at_structures=False)
check(len(out2["runs"]) == 2 and out2["structures_on_runs"] == 2,
      f"with structures not splitting: the same two runs, structures still counted ({len(out2['runs'])}, {out2['structures_on_runs']})")

# ---- 2. the sheets, when they are here -----------------------------------------------
GARD = ROOT.parent / "TakeoffPro AI" / "Plans 2" / "Gardiner water main 2025.pdf"
BARN = ROOT.parent / "client_meet" / "beaumont" / "SWR_Barn_Residence_-_Civil_4.7.26.pdf"
# the audit's independent read: beside the corpus when the worktree sits next to it, else at
# the audit's own folder on this machine
_AUD_REL = Path("cutfill-engine") / "out" / "verify" / "2026-09-26" / "accuracy-audit" / "followup-2" / "engineer-truth" / "gardiner_w1_profile_depth.json"
AUD = next((c for c in (ROOT.parent / _AUD_REL,
                        Path(r"C:/Users/kobys/OneDrive/Desktop/past/P/Takeoff Automation") / _AUD_REL)
            if c.exists()), ROOT.parent / _AUD_REL)


def bands(series, first=6.0, step=2.0):
    out = {}
    for (s0, d0), (s1, d1) in zip(series, series[1:]):
        L = s1 - s0
        lo, hi = min(d0, d1), max(d0, d1)
        edges = [0.0, first]
        while edges[-1] < hi + step:
            edges.append(edges[-1] + step)
        for a, b in zip(edges, edges[1:]):
            if hi - lo < 1e-9:
                frac = 1.0 if (a < d0 <= b or (d0 == 0 and a == 0)) else 0.0
            else:
                frac = max(0.0, (min(b, hi) - max(a, lo)) / (hi - lo))
            if frac > 0:
                out[f"{a:g}-{b:g}"] = out.get(f"{a:g}-{b:g}", 0.0) + frac * L
    return out


if GARD.exists():
    d = pymupdf.open(str(GARD))
    pg = d[297]
    units = extract_strokes(pg)
    regs = P.find_profiles(pg, units)
    rd = P.read_profile(pg, regs[0], units) if regs else None
    check(rd is not None and rd.pipes and abs(rd.pipes[0].od_ft - 0.667) < 0.02,
          f"Gardiner W-1: the 8-in main reads as a two-line pipe {rd.pipes[0].od_ft if rd and rd.pipes else None} ft tall")
    ser = P.depth_series(rd, 0, 0) if rd else []
    ds = [v for _, v in ser]
    check(ser and abs(min(ds) - 7.11) < 0.05 and abs(max(ds) - 10.14) < 0.05 and abs(sum(ds) / len(ds) - 8.76) < 0.03,
          f"...depth to invert min {min(ds) if ds else 0:.2f} / max {max(ds) if ds else 0:.2f} / mean {sum(ds) / len(ds) if ds else 0:.2f} ft (audit: 7.11 / 10.14 / 8.76)")
    if not AUD.exists():
        print("SKIP: the audit's Gardiner W-1 per-station read is not on this machine (" + str(AUD) + ")")
    if AUD.exists() and ser:
        rows = {round(r["sta"]): r for r in json.load(open(AUD))["rows"] if "depth_to_invert" in r}
        diffs = [abs(v - rows[round(s)]["depth_to_invert"]) for s, v in ser if round(s) in rows]
        within = sum(1 for x in diffs if x <= 0.05)
        check(diffs and within >= 0.98 * len(diffs), f"...{within} of {len(diffs)} stations within 0.05 ft of the audit's independent read")
    if ser:
        b = bands(ser)
        st = 514.6 / (ser[-1][0] - ser[0][0])          # the plan run's 514.6 LF over the profile's stationing
        got = tuple(round(b.get(k, 0) * st) for k in ("6-8", "8-10", "10-12"))
        check(abs(got[0] - 146) <= 4 and abs(got[1] - 331) <= 4 and abs(got[2] - 38) <= 4,
              f"...bands 6-8 / 8-10 / 10-12 = {got} LF on the 514.6-ft run (audit 146 / 331 / 38)")
    d.close()
else:
    print("SKIP: Gardiner water main 2025.pdf not on this machine")

if BARN.exists():
    d = pymupdf.open(str(BARN))
    pg = d[11]
    units = extract_strokes(pg)
    regs = P.find_profiles(pg, units)
    rd = P.read_profile(pg, regs[0], units) if regs else None
    check(rd is not None and len(rd.surfaces) == 2 and rd.surfaces[0].kind == "finished" and rd.surfaces[1].kind == "existing",
          "Barn C4.2: FINAL GRADE (solid red) and EXISTING GRADE (dashed) read as its two surfaces")
    chk = [c for c in (rd.checks if rd else []) if c["diff"] is not None and "pipe" in (c["line"] or "")]
    worst = max(abs(c["diff"]) for c in chk) if chk else 99
    check(len(chk) >= 20 and worst <= 0.1, f"...{len(chk)} printed inverts on the graph within {worst:.3f} ft of the read pipe invert (<= 0.1)")
    ser = P.depth_series(rd, 0, 0) if rd else []
    win = [v for s, v in ser if 10 <= s <= 270 and abs(s - round(s / 10) * 10) < 1e-6]
    check(len(win) == 27 and abs(sum(win) / 27 - 3.79) < 0.02, f"...the audit's window (sta 10-270 every 10 ft): mean depth {sum(win) / len(win) if win else 0:.2f} ft to the finished grade (audit 3.79)")
    d.close()
else:
    print("SKIP: the Barn civil set is not on this machine")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all profile-read checks passed")
