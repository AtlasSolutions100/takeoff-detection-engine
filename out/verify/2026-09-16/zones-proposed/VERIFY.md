# Proposed-surface zones, the zone's name off the sheet, spot depths on the map — headed (Edge) on :8100, app.js v=191, style v=96

Wave 2 items 4 + 5 (Beaumont, call 2026-09-15: an engineer's volume exhibit
is one zone per building against the sheet's OWN proposed grade; the crew
gets the cut/fill map with the depths printed on it). Local throwaway
store; Barn Civil C3.0 (job 3bb69406cadc, engine index 2 = printed sheet
3). Drivers: scratchpad `live_zones2.py` (the zone and the map) and
`live_zones3.py` (re-capture of the modal after its label was put on one
line, and of the map with its toggle in view; the modal was CANCELLED, so
no fourth zone); readback `readback.json`. No page or console errors in
any run.

| step | readback |
|---|---|
| zone modal, a 4-corner zone round the structure outlines (23,754 SF), radio **Proposed contours on this sheet — the design the sheet draws** (note under it) | live line: **cut 744 CY · fill 733 CY · net −10 CY over 23,180 SF (the whole zone)** · topsoil strip 8″ = 572 CY · existing inside the zone mean 7719.15 ft from 34 contour rows · deepest cut 3.84 ft / fill 12.59 ft · the zone's own dz thumbnail (`Z0-modal-proposed.png`) |
| Save zone | saved as **"Zone 3"** — `suggested_name: null`: the only text inside the polygon is the sanitary-sewer label "SS" (and two "SCL" fragments), none of them a structure word, so the zone keeps its default; card summary **proposed contours · strip 8"**; result `mode: proposed`, cut 743.5 / fill 733.2 / net −10.3 CY, area 23,180 of 23,754 SF, coverage 1.0, 120 cell labels; the table row Zone 3 · 23,754 · 744 · 733 · −10 · 1,477 (`Z1-zone-proposed.png`) |
| Show cut/fill map, then **print depths** | the toggle appears only once the map's cached meta came back with labels: **226 depths at 30 ft** (block means, page pt); the canvas prints them in the zone numbers' ink — cut red with a minus, fill blue with a plus, one decimal, a halo behind each — over the whole sheet at 32 % (`Z2-map-depths.png`, toggle boxed yellow) and at 120 % (`Z3-map-depths-zoom.png`); untick → the numbers go, the map stays (`map_after_untick`) |

**The first run's finding (2026-09-16, before the fix in the same
commit):** the auto-name took the TALLEST text inside the zone, which on
this sheet is the pipe label **"SS"**; and because the rename ran on every
zone compute, the two zones restored from the sidecar were renamed
**"WL"** (the water-line label) — the added one's takeoff rows still said
"Zone 1". Fixed: only text carrying a structure word (`_ZONE_NAME_WORDS`)
may name a zone, notes / elevations / sheet references are refused by
their punctuation, stacked lines of one size join, and only a zone JUST
DRAWN (the modal's save for a new zone sets `autoName`), still "Zone N"
and not yet added, takes the suggestion. The throwaway store's two zones
were put back to "Zone 1" / "Zone 2" by hand before the second run.

**What is not shown live:** no sheet in the local store prints a
structure name inside a contoured footprint — the barn's own name is in
C3.0's title block (outside the existing surface, so the zone route
refuses there) and C2.0's "BARN / RESIDENCE" label sits on a site plan
with no contours (that leg was tried and dropped). The positive path is
pinned by `tests/test_zones_proposed.py` on a synthetic sheet through the
real route: BARN chosen over a taller "WL", a note and an elevation; GUIDE
RESIDENCE for the second zone; "POOL" over "HOUSE" joined to POOL HOUSE;
"SLAB ON GRADE" (no structure word) → no name; a two-point polygon or an
unknown job → no name, no crash; the page's text cached.

Unchanged and for the client to rule on: the map's tint is cut BLUE /
fill orange (the sheet map's palette, legend says so) while the printed
numbers — zone and map alike — are cut RED / fill blue.

Pinned by `tests/test_zones_proposed.py` (the mode, its layer offset,
coverage and labels where the surface stops, the three refusals, pad ≡
proposed at one elevation, `map_labels` on a synthetic grid, the route
with proposed rows / a base / no rows → 400, the auto-name cases, the
cache) and `tests/test_zones_proposed_ui.js` (the radio and its note, the
toggle markup, the proposed rows in the zone body, fresh-only auto-name,
the card summary, `named_from` persisted, the map meta fetch, the toggle's
repaint and hiding, the canvas ink and the sign rule, busters). Earthwork
baselines re-accepted for the fingerprint only (238 fields identical);
chain gate PASS.
