"""The scanned-schedule close read (marks.read_mark_schedule, 2026-09-26,
audit D28), pinned on synthetic word lists with fake picture readers:

  * an OCR'd MARK column whose page read garbled a cell ("WAT" for W2L)
    is read again on that cell's box, and a row the page read never saw
    (W3L) is found from the table's own rules and read too;
  * a re-read candidate must FIT THE FAMILY the page read established:
    same head letters, a digit when the family's marks carry digits - so a
    W cut in two ("VV", "V2L") and a letters-only read ("WL", which the
    lookalike map turned into "W1") never enter;
  * an OCR'd table whose marks are not a family (Bill's TYPE table read as
    AN0 / S0 / V8 / MU5T / HO0O / A1L / F0R: prose under the lookalike
    map) is refused - a vocabulary of prose names nothing;
  * a second head on the SAME row (MARK, then WINDOW TYPE) is a later
    column of one table, not a mark column of its own;
  * a text-layer schedule is untouched by all of the above;
  * the no-schedule tag grammar admits door numbers, type letters and
    coded marks and nothing longer.

Dependency-free:  python tests/test_schedule_close_read.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from takeoff_engine import marks, marks_naming  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails += 1


def W(x0, y0, x1, y1, t):
    return (float(x0), float(y0), float(x1), float(y1), t)


# the Aqua Vista schedule as the page-level OCR returned it: MARK head, six
# rows 11 pt apart, W2L garbled to "wat", W3L missing (a rule fragment), a
# WINDOW TYPE head on the same row with descriptions under it
HEAD_Y = 117
rows = [("WIL", 123), ("WIR", 134), ("wat", 145), ("w4", 167), ("ws", 178)]
words = [W(136, 113, 149, 121, "MARK"), W(177, 113, 210, 121, "WINDOW"), W(212, 113, 230, 121, "TYPE"),
         W(300, 113, 320, 121, "QTY"), W(150, 154, 160, 156, "+�")]
for t, y in rows:
    words.append(W(137, y - 3, 146, y + 3, t))
for t, y in (("5'-0\"", 123), ("5'-0\"", 134), ("S01", 145), ("S5", 156), ("5'-0\"", 167)):
    words.append(W(178, y - 3, 200, y + 3, t))
words += [W(300, 120, 315, 126, "148"), W(300, 131, 315, 137, "15")]


def reread(word):
    """the picture reader: what a close read of each cell returns"""
    y = (word[1] + word[3]) / 2.0
    if 142 <= y <= 152:
        return ["WET", "W2L", "WA", "WL", "W"]        # the W2L row: split reads
    if 152 <= y <= 163:
        return ["W3L", "A"]                           # the W3L row (no page word)
    if 131 <= y <= 141:
        return ["VV1R", "A"]                          # the W1R row: W cut in two
    return []


def cells(hw):
    return [(132.3, 122.4, 152.6, 131.5), (132.3, 133.5, 152.6, 142.5), (132.3, 144.5, 152.6, 153.6),
            (132.3, 155.6, 152.6, 164.5), (132.3, 166.5, 152.6, 175.6), (132.3, 177.6, 152.6, 181.6)]


print("the close read")
s = marks_naming.read_scanned_schedule(words, reread=reread, cells=cells)
check(s["marks"] == ["W1L", "W1R", "W4", "W5", "W2L", "W3L"],
      "six rows: four from the page read, W2L off its garbled cell, W3L off the rule-bounded row: %s" % s["marks"])
check(s["qty"] == {"W1L": 148, "W1R": 15}, "quantities where the sheet prints them: %s" % s["qty"])
check(s["header"] == "MARK", "one table, headed MARK - the WINDOW TYPE head on the same row is a later column: %r" % s["header"])
check("VV1R" not in s["marks"] and "V2L" not in s["marks"] and "W1" not in s["marks"],
      "a W cut in two and a letters-only read never enter")
s2 = marks_naming.read_scanned_schedule(words)
check(s2["marks"] == ["W1L", "W1R", "W4", "W5"], "without a picture reader the page read stands alone: %s" % s2["marks"])

print("the family rule")
check(marks_naming._fits_family("W3L", ["W1L", "W1R", "W4"]), "W3L fits a W?L / W4 family")
check(not marks_naming._fits_family("V2L", ["W1L", "W1R", "W4"]), "V2L does not (head V)")
check(not marks_naming._fits_family("WL", ["W1L", "W1R", "W4"]), "WL does not (no digit in a family with digits)")
check(marks_naming._fits_family("C", ["A", "B", "D"]), "a bare letter fits a letter family")
check(not marks_naming._fits_family("W3L", ["W1L"]), "one page-read mark is no family yet")
check(marks_naming._marks_are_a_family(["W1L", "W1R", "W2L", "W3L", "W4", "W5"]), "the Aqua column is a family")
check(not marks_naming._marks_are_a_family(["AN0", "S0", "V8", "MU5T", "HO0O", "A1L", "F0R"]), "Bill's prose is not")
check(not marks_naming._marks_are_a_family(["S0", "A0", "A", "N0T"]), "two of four with one head is not (strict majority)")
check(marks_naming._marks_are_a_family(["1", "2", "3"]) and marks_naming._marks_are_a_family(["A", "B", "C", "D"]),
      "all numbers, or all letters, are")

print("prose refused on a scan")
prose = [W(400, 300, 420, 308, "TYPE"), W(430, 300, 470, 308, "NOTES"), W(480, 300, 500, 308, "QTY")]
for t, y in (("AN0", 312), ("S0", 323), ("V8", 334), ("MU5T", 345), ("HO0O", 356), ("A1L", 367), ("F0R", 378)):
    prose.append(W(400, y - 3, 425, y + 3, t))
sp = marks_naming.read_scanned_schedule(prose)
check(sp["marks"] == [] and sp["source"] == "none", "a TYPE table of prose under the lookalike map is not a schedule: %s" % sp["marks"])
st = marks.read_mark_schedule(prose, fuzzy=False)
check(len(st["marks"]) >= 2 and st["source"] == "text",
      "the guard is OCR-only by design: a real text layer prints what it prints and is harvested as before")

print("the tag grammar")
for good in ("001", "202", "A", "MM", "W2L", "D101", "SF2", "12"):
    check(bool(marks_naming._TAG_GRAMMAR.match(good)), "%r is a tag" % good)
for bad in ("OUT", "ITHE", "1", "1234", "WINDOW", "A1L2"):
    check(not marks_naming._TAG_GRAMMAR.match(bad), "%r is not" % bad)

if fails:
    print("%d FAILED" % fails)
    sys.exit(1)
print("all passed")
