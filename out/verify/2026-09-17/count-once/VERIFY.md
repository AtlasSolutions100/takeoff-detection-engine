# Count each wall once — headed (Edge) on :8100, app.js v=198, style v=99

Wave 2 item 11. A foundation plan draws each wall as its two faces and each
continuous footing as its two edges, so a family measured as drawn bills
every wall about twice. Both clients take one run per wall: Jeff measures
"the outside dimension of wall" and uses that one line for footing and wall
alike (footing video 05:48–05:57); Beaumont's own 19 length measurements on
Barn S1-1.3 total **704.4 ft**, where the dashed footing family is 1,055 ft
as drawn.

Sheets, local throwaway store only (client material):
- **Barn Structural S1-1.3** — job fa8627f870f1, engine page index 14 =
  printed sheet 15 of the set (S1-1.3), scale 3/16" = 1'-0".
- **Ellis S1.02** — job 41cff518a862, engine page index 42 = printed page
  43 (S1.02), scale 1/8" = 1'-0", page rotated 90°.

Drivers: scratchpad `live_twins.py`, `live_twins_crops.py`,
`live_twins_groups.py`; annotation `annotate_twins.py`; readback
`readback.json`. No page or console errors in any run.

## Ground truth

| sheet | source | expected | before | after |
|---|---|---|---|---|
| Barn S1-1.3, dashed footing family | the client's own 19 Bluebeam "Polylength Measurement" annotations in the PDF (client-asserted; they agree with the sheet scale to 0.1 ft) | 704.4 ft | 1,055.5 ft as drawn (+49.8%) | **760.3 ft** one run (+7.9%) |
| Barn S1-1.3, the same family inside the bottom-of-footing groups | same | 704.4 ft | 982.5 ft (+39.5%) | **716.9 ft** (+1.8%) |
| Ellis S1.02, solid wall family | **no measured reference** — Jeff's own numbers are for the Dibble job, which we do not have | — | 1,470.9 ft | 1,118.8 ft (unscored) |

What the Barn score cannot see: it is a total. His 19 measurements are the
runs he chose to colour; the per-group split was scored separately in
FINDINGS 30 (magenta 100%, cyan 99%, blue 65%, red 19% of his bands in the
matching group). The whole family counts 56 ft more than his total; the
groups — lines near a printed callout — land within 12.5 ft of it.

Ellis, proposed for confirmation: inside the plan area the wall family goes
from 1,117 ft drawn to 858 ft; 354 ft of the family is the sheet border and
title-block frames (also double lines, 93 ft of them left out). That family
mixes walls with frames, leaders and grid lines, so its one-run total is not
a wall total until those are switched off.

## Steps

| capture | what it shows |
|---|---|
| `C1-row-as-drawn.png` | the footing family row before: "1.1k LF · 66 lines", takeoff row **1,055.5 ft** |
| `C2-row-once.png` | "once" pressed: "760 LF once (1.1k LF drawn) · 66 lines", takeoff row **760.3 ft**; the button's tooltip says both numbers in plain words |
| `C3-canvas-full.png` | the family on its own across the sheet (the coloured bands are the client's markup inside the PDF) |
| `C3b-canvas-mid.png` | a CF3.0 (83'-0") footing at 120%: the outer edge solid, the inner edge thin and dotted — along the run and round the corner |
| `C4-canvas-zoom.png`, `C4-side-by-side.png` | the same footing at **450%** beside the PDF cut to the same window: the two dashed edges 3 ft apart with the wall between them; the app keeps the outer one |
| `C5-groups-one-run.png` | the structure panel's elevation groups, column **"one run"**: 54 / 70 / 23 / 148 / 196 / 76 / 18 / 133 ft = 716.9 ft; the tooltip keeps the drawn footage. With "colour the walls by group" on, the inner edge is faded, not painted (see the defect below) |
| reload | the choice came back from the page's saved inputs: `once: [11]`, 760.3 ft |
| `C6-ellis-zoom.png`, `C6-side-by-side.png` | Ellis at **450%** beside the PDF: one line of a wall counted, its twin dotted |
| `C6b-ellis-mid.png` | Ellis at 130% along the walls |
| `C7-ellis-full.png` | the Ellis family on its own, with the border frames noted |

Offered switches: 11 families on Barn S1-1.3 (7 wall, 4 footing), 6 on
Ellis — only families where at least 15% of the length pairs. A set with no
footing schedule offers none.

## Defect found and fixed during this verification

With the structure panel's "colour the walls by group" on, the group colour
is drawn 3× the plotted weight, wider than the first knock-back stroke, so
the left-out inner edge still wore the group colour and looked counted. The
knock-back now covers the widest stroke the chain can have (C5 re-shot
after the fix).

## Pinned by

`tests/test_twin_lines.py` (ten synthetic plans with known answers — outside
face kept, interior wall once, a broken inside face, footing edges at the
schedule widths only, a door gap, a three-line ledge wall, two walls close
together, what must not pair, the sample cap — plus the Barn sheet when the
local store has it), `tests/test_twins_route.py` (the route, its cache, the
one-run groups, the export's `onceFrac` and `countOnce`, lengthPt unchanged,
no schedule no switch, 404), `tests/test_count_once_ui.js` (the share and
billed length run for real, where they are applied, the row switch sharing
the LF/EA cell, per page and saved, the canvas overlay, the groups column),
and the new route in `tests/test_orphan_job.py` (37 routes). All three new
tests fail on the commit before this one.
