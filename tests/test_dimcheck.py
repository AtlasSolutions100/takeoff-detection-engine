"""THE PRINTED DIMENSIONS ARE A WITNESS TO THE SCALE.

takeoff_engine.dimcheck pairs every lone dimension string (60', 120'-0")
with the drawn line it labels and turns each into a ft-per-inch reading -
printed feet over the line's paper length - then says whether the scale in
force agrees with the sheet's own dimensions. Measured 2026-09-08 on Belle
Farms Structural Plans p0 (1/8" = 1'-0" caption, 8 ft/in): 7 of 7 lone
dimensions read 7.6-7.9 ft/in against their own lines; against a wrong
4 ft/in the same seven say "disagrees, they agree with each other at 7.9".

The synthetic sheet below is built the way a CAD sheet draws dimensions
(the shapes that broke the first three pairing rules, each named):

  * ONE dimension line across three bays, a tick at every grid - the
    string is never at the middle of the run (Belle Structural)
  * 45-degree slash ticks, not perpendicular extension lines (Revit)
  * a text-mask rectangle behind each string whose edges end on the line
    exactly like ticks ("16'" was once measured across its own mask)
  * an overall dimension centred on its own run, which the bay dims'
    extension lines also end on (the "97'" case: two candidate spans)
  * feet inside a sentence ("70' x 9' PULLTHROUGH STALL", Barn C3.0), a
    decimal elevation ("1140.96'", Belle ROAD GRADING), and bar ticks under
    a SCALE caption - none of which is a dimension

Run:  python tests/test_dimcheck.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from takeoff_engine.dimcheck import dimension_check, parse_dim_ft  # noqa: E402

FAILS = []


def check(ok, msg):
    if ok:
        print("ok:", msg)
    else:
        FAILS.append(msg)
        print("FAIL:", msg)


# ---------------------------------------------------------------- parsing
for txt, ft in (("60'", 60.0), ("120'-0\"", 120.0), ("12'-6\"", 12.5),
                ("12'-6 1/2\"", 12.5 + 1 / 24.0), ("(60')", 60.0),
                ("44’", 44.0), ("35.5'", None), ("1140.96'", None),
                ("60", None), ("6\"", None), ("R=19'", None), ("0'", None)):
    got = parse_dim_ft(txt)
    check((got is None and ft is None) or (got is not None and ft is not None and abs(got - ft) < 1e-9),
          "parse %-12r -> %s" % (txt, got))


# ---------------------------------------------------------------- the sheet
# 8 ft/in: 1 pt = 8/72 ft. Grid at x = 100, 340, 580, 700 (bays 240, 240,
# 120 pt = 26.67', 26.67', 13.33'); overall 600 pt = 66.67'.
FT_PER_IN = 8.0


def ft_of(pt):
    return pt / 72.0 * FT_PER_IN


def word(cx, cy, text, w=None, h=8.0):
    w = w if w is not None else 5.5 * len(text)
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, text)


def seg(x0, y0, x1, y1, cid):
    return {"chain_id": cid, "points": [[x0, y0], [x1, y1]]}


def build(scale_words=True):
    chains, words = [], []
    # bay dimension line y=200 from x=90 to 710 (overshoots the grids a little)
    chains.append(seg(90, 200, 710, 200, 1))
    # overall dimension line y=150, x=100..700
    chains.append(seg(100, 150, 700, 150, 2))
    # extension lines from the plan (y=400) up through the bay line to the
    # overall line - so they END on the overall line too
    for i, gx in enumerate((100, 340, 580, 700)):
        chains.append(seg(gx, 400, gx, 150, 10 + i))
        # 45-degree slash ticks on the bay line
        chains.append(seg(gx - 4, 204, gx + 4, 196, 20 + i))
    # the strings, each with a text-mask rectangle whose edges touch the line
    for i, (x0, x1) in enumerate(((100, 340), (340, 580), (580, 700))):
        cx = (x0 + x1) / 2
        t = "%d'" % round(ft_of(x1 - x0)) if i < 2 else "13'-4\""
        words.append(word(cx, 192, t))
        w = 5.5 * len(t) + 4
        chains.append(seg(cx - w / 2, 199, cx - w / 2, 186, 30 + i))
        chains.append(seg(cx + w / 2, 199, cx + w / 2, 186, 40 + i))
        chains.append(seg(cx - w / 2, 186, cx + w / 2, 186, 50 + i))
    words.append(word(400, 142, "66'-8\""))
    # a sentence with feet in it, over a stray line of its own
    words.extend([word(150, 300, "70'"), word(160, 300, "x"), word(171, 300, "9'"),
                  word(212, 300, "PULLTHROUGH")])
    chains.append(seg(120, 306, 240, 306, 60))
    # a decimal elevation beside a short leader
    words.append(word(500, 330, "1140.96'"))
    chains.append(seg(480, 336, 520, 336, 61))
    # a graphic bar with ticks under a SCALE caption
    if scale_words:
        words.append(word(650, 360, "SCALE"))
        chains.append(seg(600, 380, 700, 380, 70))
        words.extend([word(600, 372, "0"), word(650, 372, "30'"), word(700, 372, "60'")])
    return words, chains


words, chains = build()
d = dimension_check(words, chains, FT_PER_IN)
check(d["verdict"] == "agrees" and d["n_agree"] == 4 and d["n_disagree"] == 0,
      "at the true 8 ft/in the four dimensions AGREE, 4 of 4  (%s %d/%d)"
      % (d["verdict"], d["n_agree"], d["n_disagree"]))
texts = sorted(w["text"] for w in d["witnesses"])
check(texts == ["13'-4\"", "27'", "27'", "66'-8\""],
      "...and they are the three bays and the overall, nothing else  (%s)" % texts)
check(d["n_in_sentence"] == 2, "the 70' and 9' inside a sentence were not witnesses  (%d)" % d["n_in_sentence"])
check(d["n_bar_ticks_skipped"] == 2, "the bar ticks under SCALE were skipped  (%d)" % d["n_bar_ticks_skipped"])
check(all(w["line_pt"] > 100 for w in d["witnesses"]),
      "no string was measured across its own text mask  (%s)"
      % [w["line_pt"] for w in d["witnesses"]])
ov = [w for w in d["witnesses"] if w["text"] == "66'-8\""][0]
check(abs(ov["line_pt"] - 600) < 1.0,
      "the overall dimension took its own run, not the bay its extension "
      "lines cut it into  (%.1f pt)" % ov["line_pt"])

d2 = dimension_check(words, chains, FT_PER_IN / 2)
check(d2["verdict"] == "disagrees" and d2["witness_consensus_n"] == 4
      and abs(d2["witness_consensus_ft_per_in"] - 8.0) < 0.4,
      "at a wrong 4 ft/in the same four DISAGREE and say ~8 together  (%s n=%s at %s)"
      % (d2["verdict"], d2["witness_consensus_n"], d2["witness_consensus_ft_per_in"]))
check("agree with EACH OTHER" in d2["why"] and "Check the scale" in d2["why"],
      "...in a sentence that tells the operator what to do: " + d2["why"][:80])

d3 = dimension_check(words, chains, None)
check(d3["verdict"] == "no-scale" and "imply about 8" in d3["why"],
      "with no scale in force the readings are reported, not judged  (%s: %s)"
      % (d3["verdict"], d3["why"][:70]))

# fewer than MIN_WITNESSES is not a check
few_words = [w for w in words if w[4] in ("27'", "SCALE", "0", "30'", "60'")][:1] \
    + [w for w in words if w[4] == "66'-8\""]
d4 = dimension_check(few_words, chains, FT_PER_IN)
check(d4["verdict"] == "inconclusive" and "fewer than 3" in d4["why"],
      "two witnesses are inconclusive, whatever they say  (%s)" % d4["verdict"])

d5 = dimension_check([w for w in words if parse_dim_ft(w[4]) is None], chains, FT_PER_IN)
check(d5["verdict"] == "no-witnesses" and d5["n_agree"] == 0,
      "a sheet with no dimension strings says so and checks nothing")

# a rotated (vertical) dimension pairs the same way
vwords = [word(96, 250, "26'", w=8.0, h=16.5)]
vchains = [seg(100, 100, 100, 400, 1), seg(200, 100, 100, 100, 2), seg(200, 400, 100, 400, 3),
           seg(96, 250 - 12, 96, 250 + 12, 4), seg(92, 238, 92, 262, 5)]
d6 = dimension_check(vwords + [word(300, 50, "x"), word(320, 50, "y")] , vchains, 8.0)
w6 = d6["witnesses"]
check(len(w6) == 1 and abs(w6[0]["line_pt"] - 300) < 1.0,
      "a vertical dimension is measured along its vertical line  (%s)"
      % [(w["text"], w["line_pt"]) for w in w6])

print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
