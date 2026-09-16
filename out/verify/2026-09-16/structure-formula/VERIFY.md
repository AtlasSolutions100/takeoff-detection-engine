# Structure dig and backfill, Beaumont's way — headed (Edge) on :8100, app.js v=178

Barn C3.0 (local throwaway store), scale 1" = 30' matched by the drawn bar.
Driver: scratchpad `live_struct_formula.py`; readback `readback.json`. No
page or console errors. The real mouse drew an outline (the fourth click
landed on the close snap, so the shape is a 4,992 SF triangle with 341.16 LF
of wall — the arithmetic below is on that ring).

Beaumont's rule (call 2026-09-15, 28:00-31:17; bid sheet items 8 and 9):
dig = wall LF × Dt × (footing width + half of Dt at 1:1); backfill = the
same section figured from the footing bottom to the PROPOSED grade.

| step | readback | expected |
|---|---|---|
| dig 6, footing 0 below, width 2, layback 1:1, fill blank | "structure dig 379.1 CY · backfill 379.1 CY **(fill depth assumed = dig)**", trench 5 ft wide outside the wall (`S1-draft-assumed-annot.png`) | 341.16 × 6 × (2 + 3) / 27 = 379.07 ✓ |
| type fill 4 | "backfill 202.2 CY", no assumed tag | 341.16 × 4 × (2 + 2) / 27 = 202.17 ✓ |
| Add to takeoff | "… server agrees ✓"; rows Mass Excavation 1,109.4 CY · Structure Excavation 379.1 CY · Structure Backfill 202.2 CY · Slab Prep 4,992.3 SF; the orange band on the canvas is the 5 ft trench top width (`S3-added-server-check-annot.png`) | mass 4,992.3 × 6 / 27 = 1,109.4 ✓ |

Before this, the same outline would have read dig 341.16 × 6 × 4 / 27 =
303.3 CY (a constant 4 ft layback width) and backfill identical to it.
Pinned by `tests/test_structure_formula.py` (route function: the 6-by-6-by-3
example, fill depth, footing below slab, no layback, 2:1, defaults, the old
layback_ft ignored, negative width refused) and `tests/test_structure_formula.js`
(stCalc mirror, inputs, calcstate, result wording, band, hint). Earthwork
gate re-accepted for the fingerprint only (238 fields identical).
