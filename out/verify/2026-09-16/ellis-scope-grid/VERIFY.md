# Grid bubbles lettered by live text + the declared scope files unnamed rows — headed (Edge) on :8100, app.js v=182

Ellis Foundation S1.02 (`ellis_plan.pdf`, printed page 43 = engine index 42,
1/8" = 1'-0" read from the caption). The client PDF was uploaded to the LOCAL
throwaway store only (job 41cff518a862 in demo-jobs3); it never touches the
hosted box. Driver: scratchpad `live_ellis_scope.py`; readback
`readback.json`. One console 404 (the reuse probe on /api/jobs); no page errors.

Jeff Ellis (ECI Foundations, demo 2026-08-24): grid-line bubbles must not be
counted or measured (J16), and a "Footing and foundation" scope should carry
his items (J15).

## J16 — engine (CLI, same page)

| run | classes | takeoff length | grid chains |
|---|---|---|---|
| before (d0dc055) | solid 180 · dash1 31 · dash2 33 · annotation 44 | 47,994 pt = 5,333 ft | 0 (the pass returned early: 6 vector-glyph bubbles of 58) |
| after | solid 131 · dash1 31 · dash2 7 · annotation 119 | 34,102 pt = 3,789 ft | 88 chains, 16,345 pt |

Removed 13,892 pt = 1,544 ft = 28.9% of the sheet's takeoff length. The
diagnostic (`diag_grid.py`): 61 circle candidates, 58 uniform (r 8.2 pt), only
6 with vector ink inside, 58 with a short text word centred inside. Three
holes fixed in `chain._grid_annotation`: text-lettered bubbles; the terminal
test's 45 pt "beyond" ball held the tested chain's own far endpoint at r 8
(2 r + 20 = 36 pt), so no axis was ever a terminal anchor and the pen gate on
the perpendicular-set rule was off; a jogged axis (grid 1, 8 pt step) is
accepted when both ends terminate at lettered bubbles. Pinned by
`tests/test_grid_text_bubbles.py` (synthetic sheet; fails on d0dc055).

## Headed

| step | readback | expected |
|---|---|---|
| Detect on index 42 | 21 styles, 13 ticked (33,742 pt); 5 annotation styles (17,675 pt) all unticked, three of them "annotation grid" (13,501 · 2,689 · 155 pt); bar reads **3.7k ft** (`E1-detected-annot.png`); at 120% the top grid row's bubbles 2-12 and their axes carry no traced line, the walls beside them do (`E3-canvas-grid-row-annot.png`) | grid out of the takeoff; walls in |
| Scope bar: click **Footing & Foundation** | rec.trades = [Footing & Foundation] (document); all 21 verdicts "unknown" (no layer, no legend on this sheet) so nothing is hidden; **all 10 itemized rows read Footing & Foundation** in their trade pick, the group heads "Footing & Foundation 3.7k ft" (`E2-scope-itemized-annot.png`) | before: every one of them read General |

Residual, said plainly: the three detail-callout circles (4/SD6, a larger
radius than the grid bubbles) are still traced as solid linework — a detail
callout is not a grid bubble and this pass does not claim it. Pinned by
`tests/test_scope_trade_fallback.js` (one declared trade files unnamed rows;
none or two stay General; named rows unchanged; fails on d0dc055).

Gates: chain gate re-accepted (engine_sig only; the corpus table shows no
count moved), earthwork baselines re-accepted for the fingerprint (chain.py
is in its 28-file hash; prove: 238 fields identical).
