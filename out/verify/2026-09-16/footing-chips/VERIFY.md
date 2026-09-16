# Footing marks, the footing schedule, and the chips — headed (Edge) on :8100, app.js v=183

Wave 2 item 1 (Beaumont call 2026-09-15 28:00-31:17: "CF3.0 vs CF7.5
doubles the dig"; Jeff Ellis: footings by schedule mark). Local throwaway
store only; the Barn structural set was uploaded there (job fa8627f870f1)
and the Ellis set was already there (41cff518a862). Driver: scratchpad
`live_footing_chips.py`; readback `readback.json`. No page or console errors.

## Barn Structural S1-1.3 (printed 15, engine index 14), 3/16" = 1'-0"

| step | readback |
|---|---|
| Structure panel opens | "Footing marks printed on this sheet — click one to set the draft's footing width and depth · **schedule read from page 12**" (S0-0.4) |
| chips (`F1-barn-chips-annot.png`) | CF2.0 ×14 · 2 ft wide × 1 ft deep · BOF 86′ ×4, 87′, 96′ ×2, 84.1667′ ×3, 86.5′ ×2, 85.1667′, 94.5′ — then F5.5 ×5, CF3.0 ×4 (BOF 83′ ×2, 96′ ×2), CF5.0 ×3, CF7.0 ×3, CF4.0 ×3, F4.0 ×2, F3.0 ×2, F10.0x6.0 ×2 (10 × 6 × 1.33 ft deep), CF9.5, F7.5 (7.5 × 7.5 × 1.5 ft deep), F5.0, F9.0, F6.0, F10.0x7.0, F10.0x8.0 — every one from the schedule (tooltip names page 12 and the reinforcing); slab elevations printed: (87'-0"), (85'-10"), (84'-8"), (87'-6"), (84'-2"), (100'-2") |
| click CF2.0 with no outline | status "CF2.0: pick or draw the outline first, then click the mark to set its footing" (first run) |
| draw an outline, click CF2.0 (`F2-barn-chip-applied-annot.png`) | draft footing width **2** and footing below slab **1** (the inputs read 2 / 1), `footing_mark` CF2.0; status "CF2.0: footing 2 ft wide, 1 ft below the slab (from the footing schedule)"; the result line recomputes (mass 88.9 CY · structure dig 186.5 CY · … on the drawn triangle) |

The drawn outline closed at three corners again (the fourth real click hit
the close snap, as in the structure-formula run); the chip claim does not
depend on the shape.

## Ellis Foundation S1.02 (engine index 42), 1/8" = 1'-0"

Chips from the notes page's FOOTING SCHEDULE (page 51): F-24 ×15 · 2 ft wide ×
0.83 ft deep · F-20 ×11 · S-36 ×12 (3 × 3 × 0.83 ft) · S-60 ×8 (5 × 5 × 1 ft) ·
S-48 ×2, each tooltip "from the footing schedule (page 51) · #4 BARS …", every
mark "with no elevation beside it" (that set prints none)
(`F3-ellis-chips-annot.png`).

## Found on the way

`/scale/dimensions` answered 500 on S1-1.3: `dimension_check` picked a
consensus word's reading among those within tolerance of the cluster MEDIAN,
but the cluster was grown from the SEED value, so a member word could have
nothing inside the median's window and `min()` raised. `_pick_reading` falls
back to the nearest reading; `tests/test_dimcheck_pick.py` pins the shape.

Pinned by `tests/test_footing_callouts.py` (feet-inch parsing incl. the
three quote glyphs; marks CF/F/F-nn/S-nn/F10.0x6.0; fallback from digits;
callouts pairing with the nearest parenthetical / bracket, unpaired beyond
60 pt, slab tags set apart; the schedule as two sub-tables on one row band,
Ellis-style inches, one mark is not a schedule; and the real Barn S0-0.4 /
S1-1.3 and Ellis pages when present, skipped and said when not),
`tests/test_footings_route.py` (a fabricated two-page set through the route,
per-job cache, 404s) and `tests/test_footing_chips.js` (block, fetch, click,
guess styling, cache busters). Earthwork gate re-accepted for the fingerprint
only (238 fields identical).
