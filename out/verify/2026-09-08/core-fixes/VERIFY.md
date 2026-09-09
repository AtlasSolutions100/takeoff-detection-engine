# core fixes, verified in a HEADED browser (Edge, Playwright headless=False) on :8100

Dev service on the throwaway store `demo-jobs3` (real `takeoff-jobs` untouched:
registry mtime still 2026-08-31 16:06). Driver: scratchpad `live_core_fixes.py`;
DOM readbacks in `dom.json`; annotations by `annotate_core.py`
(red = the claim, green = checked and clean, numbers keyed to the band under
each crop).

## A. Belle Farms Upper Parcel ROAD GRADING 1 of 2, engine page 0 — the split abstains

Sheet: one line weight (0.5 pt) on all 40 contour rows, no legend row naming
existing/proposed, one ink. Before: every row filed EXISTING by the width
median. Now:

| what | DOM readback |
|---|---|
| rows | 40 |
| `splitBy / surface` | `none / skip` × 40 |
| hint text | "40 contours have no side yet. This sheet has no legend row naming existing or proposed, one ink, and one line weight, so nothing on it says which contours are which. They add nothing to the volume until you choose." + All existing / All proposed |
| row tooltip | "NO SIDE YET. This sheet gave us nothing to split its contours with …" |
| after **All existing** | `operator / existing` × 40, hint gone |

Captures: `A1-belle-unsplit-raw.png` (full), `A1b-belle-rows-crop-annotated.png`,
`A2-belle-all-existing-raw.png`, `A2b-belle-rows-after-crop-annotated.png`.

## C. Belle Farms Structural Plans, engine page index 0 = printed A1.00 — the dimension witness

Sheet prints two scales (1/8" = 1'-0" and 1/4" - 1'-0"). The engine chose
**4.00 ft/in** and the bar chip says "matches drawn bar". The new chip:
**"7 printed dimensions say ~7.9 ft/in — CHECK"**. Route readback:
verdict `disagrees`, 0 of 7 agree with 4, consensus 7.863 ft/in over 7
witnesses (44', 32', 80', 16', 50', 97', 54' each measured between their
own grid ticks; crops of the 44' and 16' lines in `../dimcheck/`).
The 44'-0" string sits on a vertical line from grid 1 to grid 5 that
measures 403 pt = 5.6 in, which is 8 ft/in, not 4. This is a real miss on
a real sheet that the check now shows the operator; it does NOT change the
scale by itself.

Captures: `C1-struct-head-raw.png` (full), `C1b-struct-head-crop-annotated.png`.

## D. Barn C3.0, engine page 0 — a collinear surface is refused, not a 500

Through the live route (`POST /earthwork`, same body shape the panel sends):

| body | before | now |
|---|---|---|
| one straight chain a side (734 / 735) | 500 Internal Server Error | 400 `surface_collinear`: "the existing surface's samples (1 contour, 0 spot grades) all lie on ONE straight line, so they enclose no area…" |
| three collinear spot grades a side | 500 | 400 `surface_collinear` (0 contours, 3 spot grades) |

## B. Barn C3.0, engine page 0 — a cut-short OCR read is shown as cut short

Unloaded, this sheet's OCR pass completes in 87 s (382 calls, 0 rows), so
the cap never fires on it at the default 900 s. To show the disclosure the
service was started with a DELIBERATE `TAKEOFF_OCR_BUDGET_S=30` for this
check only (a configuration, stated here; nothing pre-baked - the elevgaps
cache was deleted first), then restored to defaults.

| what | DOM readback |
|---|---|
| `state.ew.ocr` | planned 796, calls 17, 31.2 s, stopped_early true, reason `cap`, chains_read 0 |
| `#ewDropped` text | "Label reading stopped early. The reader looked at 17 of 796 places on this sheet in 31 s and hit its time limit, so some printed elevations may be missing from this list. Add any you can read with + Add contour." |

17 calls in 31 s is 1.8 s per call: the box was loaded by another agent's
work at the time (0.23 s per call unloaded), which is the very reason the
idle clock is off by default (FINDINGS section 3).

Captures: `B1-c30-ocr-cut-raw.png` (full), `B1b-c30-ocr-cut-crop-annotated.png`.

## Gate

Chain gate: PASS, `engine_sig` unchanged (no detect code moved). Earthwork
gate: `earthwork_sig` moved (server.py, earthwork.py, new dimcheck.py) and
was re-accepted; `prove_baselines.py` walked the three baselines field by
field against the pre-change copies - see the commit message for its
output.
