# The contour interval: read from the sheet, settable by the estimator — 2026-09-18

Headed Edge on `http://127.0.0.1:8100`, dev, `app.js?v=207`.
Driver: `scratchpad/live_interval.py`. Readback: `readback.json`.
Zero page errors, zero console errors.

## What this is

Contours are drawn at the interval, but only every fourth or fifth one —
the **index contour** — carries a label. Barn C3.0 draws **350 contour
lines and labels 26**, every label a multiple of 5, so the modal step
between labels reads **5 ft**: the index spacing, not the interval. The
sheet states **1 ft** in its own notes, twice.

`contour_interval()` has taken a `note_ft` argument since it was written —
*"the sheet stating its interval is better evidence than any statistic over
the labels we happened to resolve"* — and nothing ever passed one.
`earthwork.read_interval_note` now fills it, and the estimator can override
it.

Precedence, the same everywhere: **what the operator typed → what the sheet
says → the modal step between labels.**

## What it does and does not change — measured, not assumed

I first reported that reading 5 ft made every counted elevation five times
too far apart. **That was wrong, and the measurement says so:** forcing 5 ft
and letting the note give 1 ft produce *identical* counted elevations on
that sheet — 42 infilled rows, zero differing, same volume. `infill_intervals`
already carries `_minor_interval`, a consensus guard that derives the finer
step from the fans between adjacent labels, and it was recovering the 1 ft
on its own.

So what this fixes is the **evidence, not the arithmetic**: the panel
reported 5 ft, which is the number an operator reads and quotes, and there
was no way to set it. The arithmetic will change only where that consensus
cannot form — fewer than three adjacent fans, or under 60% agreement — and
there the count falls back to the index spacing with nothing to catch it.

## Live on Barn C3.0 — `R1-interval.png`

> **Contour interval** `1` ft — sheet's contour-interval note
> *"EXISTING CONTOURS ILLUSTRATED HEREIN ARE AT 1 FT INTERVAL"*

The sheet's own words are quoted, so the number can be checked without
leaving the panel.

## Typing your own — `R2-typed.png`

Typing **2**:

- the request carries `interval_ft=2`, the single-flight key includes it, so
  the page is **re-read** rather than served the answer counted at 1 ft
  (rows 60 → 64, inferred 43 → 47),
- the reply reads `source: "the interval you typed"`,
- the control repaints to "— yours; clear the box to use the sheet's",
- and the coverage line **pushes back**: *"32 of 64 elevations are off a 2 ft
  step — more than the interval reading can carry, so the reading is what is
  in doubt and no row is ranked on it."* A wrong typed interval is caught by
  the same self-check that catches a wrong read one.

## Two defects fixed along the way, both found live

1. **The control did not repaint** after the re-read: rows went 60 → 64
   while the line still credited "the sheet's note" for a number the
   operator had just typed.
2. **A stated interval was judged as a statistic.** `ewIntervalUse` gates on
   `EW_IV_MIN_STEPS` agreeing label steps, which is meaningless for a sheet
   that states its interval — so the panel printed *"Only 0 steps between
   labelled elevations agree on 1 ft — too few to call it this sheet's
   interval"* directly above a control reading "sheet's contour-interval
   note". The same number, contradicted in two adjacent lines. The count
   gate now applies only to an interval **inferred** from labels; a stated
   one goes straight to the off-step self-check, which still earns its place.

## Abstaining

Where a sheet states two different intervals, nothing is set: both come back
as candidates, the box shows "not read", and the panel says *"the sheet
states 2 different intervals: 1 ft, 5 ft — type the one that applies."*
Picking one would be first-writer-wins (CLAUDE.md §5).

Sheets with no note are unchanged: Oncor C-04 and C2.1 still read 1.0 ft
from the modal step, same rows as before.

## Tests

- `tests/test_interval_note.py` — 5 parts, 27 checks (phrasings, agreement,
  disagreement, the words that are *not* this, precedence).
- `tests/test_interval_ui.js` — 22 checks including the stated-vs-inferred
  rule and that a plain read still sends no query string.

Both fail on `516d4c0`. Both gates pass; only the fingerprint moved (0 of
238 value fields differ). Python and JS sweeps clean.
