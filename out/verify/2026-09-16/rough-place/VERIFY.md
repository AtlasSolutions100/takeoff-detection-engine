# Rough Place Topsoil by area × depth — headed (Edge) on :8100, app.js v=190, style v=95

Wave 2 item 6 (Beaumont's bid sheet line 12 and his notes 2026-09-15:
topsoil goes back 6-8 in deep over the green area — the limit of work less
the building footprints and the paving; Estimate #853: Strip 5,730 CY,
Rough Place 2,865 CY, Export Topsoil 2,865 CY). Local throwaway store;
Barn Civil C3.0 (job 3bb69406cadc, index 2). Driver: scratchpad
`live_roughplace.py`; readback `readback.json`. No page or console errors.

| step | readback |
|---|---|
| earthwork panel, under the layer stack | a new line: **Rough place topsoil [depth] in deep over the limit less [SF] of footprints & paving**, with a "use 8,110 SF" button (8,110 SF of structure outlines on this sheet + 0 SF of zones named like paving); blank reads "blank: the topsoil layer's respread % rule as before". Typed 6 in, pressed use → 8,110 SF (`E1-panel-inputs.png`) |
| Calculate | the balance rows: **Rough Place Topsoil 3,098.8 CY** with basis "175,444.0 SF strip area − 8,110.0 SF footprints & paving = 167,334.0 SF × 6 in"; **Export Topsoil 1,233.2 CY** with basis "4,332.0 CY stripped − 3,098.8 CY rough placed at 6 in"; Import Structural unchanged (8,066.8 CY = fill 14,400.8 − reused 6,334.0). The topsoil layer's percent-based respread row is gone; `respread_total` 3,098.8, `export_total` 1,233.2 (`E2-balance-rows.png`) |
| Add to takeoff | rows Strip Topsoil 4,332 CY, Export Topsoil 1,233 CY, Rough Place Topsoil 3,099 CY; the bid view in bid-sheet order files them: 3. Strip Topsoil 4.5k CY · 2 rows, **12. Rough Place Topsoil 3.1k CY · 1 row** (`E3-bidview-line12.png`) |
| saved with the sheet | `p2_calcstate.json` on disk carries `ew.placeDepthIn: 6, placeLessSf: 8110` (the reload leg of the script landed on another page and was not exercised; the save and restore lines are pinned by the JS test) |

Against #853: his 2,865 CY placed of 5,730 stripped over 224,058 SF (his
Clear & Grub) reads as a 4.3-in placement over the whole area, or 6 in over
about 155,000 SF — i.e. he takes his drives, patios and paths off too, not
just the house. This sheet's state had no limit of work set (the strip area
is the 175,444 SF where the two surfaces overlap) and only the structure
outlines to subtract; both numbers are the estimator's to type, and the
row says which area and depth it used.

Pinned by `tests/test_rough_place.py` (the placed row and its basis, export
= stripped − placed, an import when placing more than was stripped with the
import factor, other layers keep their percent rows, no depth / blank / 0 /
a bad depth leave the old behaviour byte for byte — the earthwork baselines
rely on that; the body fields and the run's call) and
`tests/test_rough_place_ui.js` (the line's markup and placement, the
inputs' wiring, the suggestion button, the run body, calcstate save and
restore, "Import Topsoil" in both copies of the earthwork item regex,
busters). Earthwork gate re-accepted for the fingerprint only (238 fields
identical); chain gate PASS.
