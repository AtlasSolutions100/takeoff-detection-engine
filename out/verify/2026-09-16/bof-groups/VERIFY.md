# Walls grouped by bottom of footing — headed (Edge) on :8100, app.js v=186, style v=91

Wave 2 item 3 (Beaumont, call 2026-09-15: he colours every wall by the
elevation its footing bottoms at — his Bluebeam legend on S1-1.3 reads cyan
96'-98', red 85'-2", magenta 84'-2", blue 83'-0" — because the dig depth
differs per group). Local throwaway store only. Barn Structural S1-1.3 =
engine page index 14 (printed sheet 15), scale 3/16" = 1'-0" (0.0741 ft/pt),
556 chains, 45 footing callouts (44 carry a BOF), 19 WALL STEP words.
Driver: scratchpad `live_bof_groups.py`; readback `readback.json`. No page or
console errors.

| step | readback |
|---|---|
| open the Structure panel | the footings block ends with **Walls by bottom of footing**, 8 rows: 83′ (7,715′ civil) · 2 callouts CF3.0 ×2 · 3 ft · 103 ft; 84.167′ · 6 · 2 ft · 119 ft; 85.167′ · 1 · 2 ft · 38 ft; 86′ · 8 · 2 ft · 195 ft; 86.5′ · 13 · 4 ft · 258 ft; 87′ · 3 · 5 ft · 108 ft; 94.5′ · 2 · 2 ft · 18 ft; 96′ · 9 · 5.5 ft · 144 ft (983 ft in all). Head names the footing family `dash1 d6.8 g3.3` (64% of the callouts sit on it) and the as-drawn caveat; footer "1,131 ft of lines sit near no callout"; toggle on. Five columns fit the 337 px panel with no sideways scroll (`F1-table.png`) |
| canvas | 54 chains wear their group's colour while the panel is open (`F2-canvas-coloured.png`, every group's footing lines boxed in its colour). `F2-zoom-cluster-vs-pdf.png` = 3× crop of the 84'-2" / 85'-2" / 86' cluster beside the same region rendered from the PDF — which carries **Beaumont's own thick colour bands**: his red = 85'-2" and magenta = 84'-2", CORE's thin dashed strokes are red = 84'-2" and magenta = 85'-2" (colours by elevation order), so the same walls wear opposite colours. A comparison, not a defect |
| hover the 86.5′ row | `state.stHotChains` = 22 (its footing chains, including ones it shares with a neighbouring group) and the hover halo sits on them; leaving the row clears it (`F3-hover.png`) |
| pick tool on the longest footing line (981 pt) | no draft: the dashed footing outline is not a closed loop, so the pick refuses — expected, noted below |
| draw a 4-corner box round the busiest group | `POST …/footings/split` → perimeter 365.9 ft in 12 edge pieces; 83′ 56.6 / 84.167′ 8.3 / 86′ 30.1 / 86.5′ 41.7 / 87′ 110.4 / 94.5′ 31.3 / 96′ 87.3 (= 365.7 after rounding each), unassigned 0. The draft's edges are painted piece by piece in the group colours (`F4-draft-split-canvas.png`); the **outline** column fills, and 85.167′ reads — because the box never runs beside that callout (`F5-draft-split-table.png`); footer "this outline: 366 ft, all of it beside a callout" |
| toggle off / on, close the panel | `stGroupColorMap()` → null / 54 / null |

## Scored against Beaumont's own colouring (in the PDF)

The client's marked-up set is what we have, and his Bluebeam Ink bands are
12-pt vector strokes the engine already detects (40 wide chains, 978 ft).
Cutting his bands with the same rule as the footing lines (nearest callout by
true distance, WALL STEP boundaries, reach 10 ft) and reading his legend:

| his band | ft | lands in CORE's group |
|---|---|---|
| magenta 84'-2" | 63.2 | 84.167′ **100%** |
| cyan 96'-98' | 106.6 | 96′ **99%** |
| blue 83'-0" | 84.8 | 83′ 65%, 84.167′ 35% |
| red 85'-2" | 64.6 | 84.167′ 54%, 86′ 28%, 85.167′ 19% |
| orange (no legend line) | 49.6 | 94.5′ 100% |
| yellow (no legend line) | 535.4 | 86.5′ 46%, 87′ 28%, 86′ 16%, rest 10% |

Red is the honest miss, and it is partly his: 15 of his 64.6 red ft run on
the same walls as his magenta band (the shared side walls of the two rooms
— he painted the room outline, the sheet labels the wall), and the top wall
he paints red carries the printed WALL STEP and a CF2.0 (86'-0") callout at
its east end, which is where CORE cuts it. The first cut of this feature
(whole chain to its single nearest callout) sent that band 100% to 84'-2";
the piecewise rule is what shipped. His 19 "Polylength Measurement"
annotations (labelled `gas` — a tool preset name; they trace the dashed
footing family within 0.2-2.6 ft) total 704 ft of wall runs and agree with
the sheet scale to 0.1 ft; CORE's 983 ft of footing line is AS DRAWN (both
faces of a closed footing), so the two are not the same quantity until the
twin-face collapse (Wave 2 item 11) lands.

What this cannot see: his legend names four colours; yellow and orange are
read off where they land. The box in F4 is not a real wall outline — it
exercises the split's mechanics (edge pieces, WALL STEP boundaries, pieces
summing to the perimeter), which `tests/test_bof_groups.py` pins on
synthetic walls. A wing drawn in a different line style than the sheet's
majority footing family shows little footing line (94.5′: 18 ft; its lines
are family 12, listed in the cell's tooltip).

Pinned by `tests/test_bof_groups.py` (engine: one group per elevation, the
footing family by majority, feet per group, other families per group,
widths from the schedule or the mark's digits with the source said,
unassigned feet, colours by elevation order; a footing line past two
callouts split at the printed WALL STEP, else where the nearer callout by
true distance changes; a callout 4 ft off the wall beating two 60-70 ft
away; the outline split: a callout beside an edge claims the whole wall,
halfway / WALL STEP / far-step / same-elevation cases, a two-point run),
`tests/test_bof_route.py` (groups absent with the reason before Detect and
without a scale; groups with style labels, chain ids and chain_major once
both exist; the STEP word; the split route on a rectangle and on a run
with a printed STEP; 400 / 404s), `tests/test_bof_ui.js` (table, tooltips,
hover, toggle, the split fetch keyed by the draft's points, draw() colours
and weight, drawStructural's edge paint, CSS, busters),
`tests/test_orphan_job.py` (the POST route is pinned, 33 live). Earthwork
gate re-accepted for the fingerprint only (238 fields identical, scratchpad
`prove_baselines.py`); chain gate PASS.

Open: the split runs on the DRAFT outline only (a committed structure keeps
no split); the pick tool cannot trace the dashed footing outline into a
closed loop; the per-group dig depth still needs the civil surface at each
wall (Wave 3, S5) — the split is the wall length the dig formula will run
per group once it has that; `parse_ft_in` does not read a bare fraction of
an inch (`64'-1/4"`, three of his 19 measurements).
