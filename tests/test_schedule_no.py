"""SCHEDULES HEADED "No." (2026-09-08, Seer Stone A2.1).

A residential set heads its mark column "No." and quantifies with QTY:

    WINDOW SCHEDULE                        DOOR SCHEDULE
    No.  ROUGH OPENING  LOCATION  OP  QTY  No.  ROUGH OPENING  MAKE  DESCRIPTION  QTY
    1    48" X 48"      LNDRY     SL  1    A    38 1/2" X ...  ...   3-0 (S708)   1
    2    60" X 48"      VARIES    SL  8    B    ...                               1
    3    60" X 24"      RV GAR.   F   2    C    ...                               1
                                           D    ...                               3

read_mark_schedule saw neither: "No." was only a weak QTY head, a bare
"2" was never a mark, and only the largest table was returned. Pinned:
  * "No." is a mark head when a strong QTY head shares its row, and the
    title word above it (WINDOW / DOOR) names the table
  * bare 1-2 digit marks are admitted under that header only
  * two non-colliding tables on one sheet give the UNION vocabulary and
    quantities, and `tables` carries each
  * a "No." with no QTY beside it is still not a mark head
  * a bare "2" is still not a mark under a MARK header

Run:  python tests/test_schedule_no.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from takeoff_engine.marks import read_mark_schedule  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def W(x, y, text, w=None, h=6.0):
    w = w if w is not None else 3.2 * len(text)
    return (x, y, x + w, y + h, text, 0, 0, 0)


def table(x0, y0, title, cols, rows):
    """title on one line, header row 12 pt under, rows 10 pt apart."""
    words = [W(x0, y0, title.split()[0]), W(x0 + 40, y0, "SCHEDULE")]
    hy = y0 + 12
    xs = [x0 + 0, x0 + 30, x0 + 110, x0 + 170, x0 + 200]
    for x, c in zip(xs, cols):
        words.append(W(x, hy, c))
    for i, r in enumerate(rows):
        ry = hy + 10 * (i + 1)
        for x, c in zip(xs, r):
            if c:
                words.append(W(x, ry, c))
    return words


win = table(1600, 100, "WINDOW SCHEDULE", ["No.", "ROUGH", "LOCATION", "OP", "QTY"],
            [["1", "48\"", "LNDRY", "SL", "1"], ["2", "60\"", "VARIES", "SL", "8"], ["3", "60\"", "RV", "F", "2"]])
door = table(1600, 190, "DOOR SCHEDULE", ["No.", "ROUGH", "MAKE", "DESCRIPTION", "QTY"],
             [["A", "38", "THERMA-TRU", "3-0", "1"], ["B", "38", "THERMA-TRU", "3-0", "1"],
              ["C", "38", "THERMA-TRU", "3-0", "1"], ["D", "38", "THERMA-TRU", "3-0", "3"]])
noise = [W(300, 500, "2x6"), W(320, 500, "WALL"), W(700, 800, "12"), W(900, 900, "No.")]

print("both tables")
r = read_mark_schedule(win + door + noise)
check(r["source"] == "text", "a schedule was read (source text), got %s" % r["source"])
check(set(r["marks"]) == {"1", "2", "3", "A", "B", "C", "D"}, "union vocabulary 1/2/3 + A-D, got %s" % r["marks"])
check(r["qty"] == {"1": 1, "2": 8, "3": 2, "A": 1, "B": 1, "C": 1, "D": 3}, "quantities from both, got %s" % r["qty"])
check("DOOR NO." in r["header"] and "WINDOW NO." in r["header"], "header names both tables: %s" % r["header"])
check(len(r.get("tables") or []) == 2, "tables carries each schedule")
check(r["marks"][:4] == ["A", "B", "C", "D"], "the larger table leads (doors, 4 marks)")

print("one table")
r1 = read_mark_schedule(win + noise)
check(r1["marks"] == ["1", "2", "3"] and r1["qty"] == {"1": 1, "2": 8, "3": 2}, "windows alone: 1/2/3 with 1/8/2")
check(r1["header"] == "WINDOW NO.", "header WINDOW NO., got %s" % r1["header"])

print("guards")
noqty = table(1600, 100, "WINDOW SCHEDULE", ["No.", "ROUGH", "LOCATION", "OP", "SIZE"],
              [["1", "48\"", "LNDRY", "SL", "x"], ["2", "60\"", "VARIES", "SL", "x"], ["3", "60\"", "RV", "F", "x"]])
check(read_mark_schedule(noqty)["source"] == "none", "No. without a QTY column is not a mark head")
mark_tbl = table(1600, 100, "WINDOW SCHEDULE", ["MARK", "ROUGH", "LOCATION", "OP", "QTY"],
                 [["W1", "48\"", "LNDRY", "SL", "1"], ["2", "60\"", "VARIES", "SL", "8"], ["W3", "60\"", "RV", "F", "2"]])
rm = read_mark_schedule(mark_tbl)
check("2" not in rm["marks"] and rm["marks"] == ["W1", "W3"], "under MARK a bare 2 is still not a mark: %s" % rm["marks"])
check(read_mark_schedule([]) == {"marks": [], "qty": {}, "box": None, "header": "", "fuzzy": [], "source": "none"},
      "no words: the empty answer, unchanged")

print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
