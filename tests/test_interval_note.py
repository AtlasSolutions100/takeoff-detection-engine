"""THE CONTOUR INTERVAL THE SHEET STATES (2026-09-18).

Contours are drawn at the interval, but only every fourth or fifth one -
the INDEX contour - is labelled. Beaumont's Barn C3.0 draws 350 contour
lines and labels 26, every label a multiple of 5, so the modal step between
labels reads 5 ft: the index spacing, not the interval. The sheet says 1 ft
in its own notes, twice.

`contour_interval` has taken a `note_ft` argument since it was written -
"the sheet stating its interval is better evidence than any statistic over
the labels we happened to resolve" - and until now nothing passed one.
`read_interval_note` is what fills it.

WHAT THIS CHANGES, measured, so the test does not oversell it: on Barn C3.0
the counted elevations do NOT move (42 infilled rows, none differing),
because infill_intervals' `_minor_interval` consensus already derived the
finer step from the fans between adjacent labels. What moves is the number
the PANEL reports - the one an operator reads and quotes - and whether he
can set it. The arithmetic changes only where that consensus cannot form.

THE RULE THIS FILE PINS: two notes that disagree are not an answer. Picking
one is first-writer-wins (CLAUDE.md s5); both come back as candidates and
the operator types the one that applies.

    python tests/test_interval_note.py   (fails before read_interval_note)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import takeoff_engine.earthwork as ew  # noqa: E402

FAILS = []


def check(ok, msg):
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        FAILS.append(msg)


# --- 1. the phrasings a real sheet uses -----------------------------------
print("\n1. what a grading sheet actually writes")
CASES = [
    (["EXISTING CONTOURS ILLUSTRATED HEREIN ARE AT 1 FT INTERVAL"], 1.0),
    (["PROPOSED CONTOURS ARE AT 1 FT INTERVALS."], 1.0),
    (["CONTOUR INTERVAL = 2'"], 2.0),
    (["CONTOUR INTERVAL: 0.5 FT"], 0.5),
    (["2 FOOT CONTOUR INTERVAL"], 2.0),
    (["CONTOUR INTERVAL 1 FOOT"], 1.0),
    (["contour interval = 5 feet"], 5.0),
]
for lines, want in CASES:
    got = ew.read_interval_note(lines)
    check(got["interval_ft"] == want,
          "%-52s -> %s" % (lines[0][:50], got["interval_ft"]))
r = ew.read_interval_note(["EXISTING CONTOURS ILLUSTRATED HEREIN ARE AT 1 FT INTERVAL"])
check(r["source"] == "the sheet's own contour-interval note",
      "...and it says the value came from the sheet  (%s)" % r["source"])
check("1 FT INTERVAL" in (r["quote"] or ""),
      "...and hands back the sheet's own words, so the operator can check it")

# --- 2. two notes that agree, and two that do not -------------------------
print("\n2. the sheet speaking twice")
r = ew.read_interval_note(["EXISTING CONTOURS ILLUSTRATED HEREIN ARE AT 1 FT INTERVAL 2.",
                           "PROPOSED CONTOURS ARE AT 1 FT INTERVALS."])
check(r["interval_ft"] == 1.0 and r["n_notes"] == 1,
      "two notes saying the same thing are one answer  (%s)" % r["interval_ft"])
r = ew.read_interval_note(["CONTOUR INTERVAL 1 FOOT", "MAJOR CONTOUR INTERVAL 5 FT"])
check(r["interval_ft"] is None,
      "two notes that DISAGREE set nothing  (%s)" % r["interval_ft"])
check(len(r["candidates"]) == 2
      and {c["interval_ft"] for c in r["candidates"]} == {1.0, 5.0},
      "...and both are handed back, so the panel can show them  (%s)"
      % [c["interval_ft"] for c in r["candidates"]])
check("type the one that applies" in r["source"],
      "...and the reason tells the operator what to do  (%s)" % r["source"])

# --- 3. what must NOT be read as a contour interval -----------------------
print("\n3. the words that are not this")
for lines in (["SPOT ELEVATIONS SHOWN TO 1 FT ACCURACY"],
              ["INSPECT EROSION CONTROL AT 30 FT INTERVALS"],
              ["INSTALL POSTS AT 6 FT INTERVALS"],
              ["CONTOURS SHOWN FOR REFERENCE ONLY"],
              []):
    got = ew.read_interval_note(lines)
    check(got["interval_ft"] is None,
          "%-48s -> nothing" % ((lines[0][:46] if lines else "(no text)")))
check(ew.read_interval_note(["CONTOUR INTERVAL 900 FT"])["interval_ft"] is None,
      "an absurd interval is refused rather than believed")
check(ew.read_interval_note(["CONTOUR INTERVAL 0.01 FT"])["interval_ft"] is None,
      "...and so is one too small to be drawn")

# --- 4. it accepts what page_lines actually returns -----------------------
print("\n4. the shape the caller passes")
tuples = [(0.0, 0.0, 10.0, 10.0, "CONTOUR INTERVAL = 2'")]
check(ew.read_interval_note(tuples)["interval_ft"] == 2.0,
      "page_lines tuples (x0, y0, x1, y1, text) work, not just strings")

# --- 5. precedence: the sheet beats the labels ----------------------------
print("\n5. precedence")
LABELS = [7710.0, 7715.0, 7720.0, 7725.0, 7730.0]     # index contours only
bare = ew.contour_interval(LABELS)
check(bare["interval_ft"] == 5.0,
      "index-only labels imply 5 ft on their own  (%s)" % bare["interval_ft"])
withnote = ew.contour_interval(LABELS, note_ft=1.0)
check(withnote["interval_ft"] == 1.0
      and "note" in withnote["source"],
      "...and the sheet's note beats them  (%s, %s)"
      % (withnote["interval_ft"], withnote["source"]))
check(ew.contour_interval(LABELS, note_ft=0)["interval_ft"] == 5.0,
      "a zero note is not a note")
check(ew.contour_interval(LABELS, note_ft="x")["interval_ft"] == 5.0,
      "...nor is a value that will not parse")

print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
