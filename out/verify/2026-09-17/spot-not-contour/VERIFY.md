# A spot grade is not a contour's height — headed (Edge) on :8100, app.js v=203

Oncor Cloudcrest Substation Civils (job bbf812d1c2e0), engine page index 3
= printed sheet **C-04**, local throwaway store, dev service on the final
code. Driver: scratchpad `live_spot_seed.py` + `live_spot_crops.py`;
readbacks `readback.json`, `readback-crops.json`. No page errors, no
console errors.

## What was wrong

A spot grade is a POINT the design sets (a wall top, a finish grade, a
rim); a contour is a LINE at one height. The sheet says which it means by
printing a tag beside the number. The nearest-label match ignored the tag:
measured on this sheet (scratchpad `probe_tagged_rows.py`), **14 contour
rows carried a tagged spot's height — 12 of them a wall tag (BW 4, TW 5,
EW 3) and 4 of those spread further along the contour by inheritance.**
The chip showed the tag, so the panel disclosed it, but the number still
went into the surface and the volume.

## What the captures show

| capture | what it shows |
|---|---|
| `S1-panel.png` | the refusal on the panel's face: "**17 numbers beside these lines are spot grades the sheet tags BW, EW, TW** — a point on what is being built, not a contour height. No line took its height from them." |
| `S2-canvas-zoom.png` | C-04 at **473%**: the three wall spots printed along one gray dashed contour — 551.2 BW, 556.1 TW, 557.2 BW — each circled as refused, with the line's own **550** label above |
| `S3-source-crop.png` | the same window cut from the PDF at 4x, for the same comparison off the drawing |

**The line reads its own label now.** Chain 168 — the case named in
`suggest_elevations`' own comment — returns `z = 550`, `how = gap-ocr`,
0.5 pt off the line: the label printed in its own break, not the 551.2 BW
2.7 pt away. Readback: `rows 170`, `builtTagRows 0`, `spotRefused {n: 17,
tags: {BW: 6, EW: 5, TW: 6}}`, coverage `170 of 339` contour lines with a
height.

## What it does to the number

Volume through the route on the rows the server itself sides (83 existing
/ 25 proposed, linear surfaces, no limit of work):

| | cut CY | vs the offline analyst's 2,002 | fill CY |
|---|---|---|---|
| before (scratchpad `probe_c04_calc.json`) | 2,279 | +14% | 9,841 |
| **after (live, this run)** | **2,055.7** | **+3%** | 9,727 |

Two tags are deliberately NOT refused: **EG** and **ME** (existing ground,
match existing) are the existing surface's own points, and refusing them
as well moved this sheet's cut by 0 CY (2,056 either way) — so the
narrower rule is the one taken. Barn C3.0 has no tagged rows at all and is
unchanged (cut 8,075 / fill 10,469 on the same unbounded inputs before and
after).

The spot grades themselves are not lost: they are read as POINTS by the
spot-grade pass (21 on this sheet), where wall tops stay off the ground by
default and the operator chooses per tag.

Pinned by `tests/test_spot_not_contour.py` (including chain 168's real
geometry and word boxes as a fixture) and a case in
`tests/test_ew_abstain_ui.js`; both fail on 6211579. NOTE: the earthwork
gate did NOT move — its C-04 cases replay a fixed row set, so row
suggestion is outside it (FINDINGS 39).
