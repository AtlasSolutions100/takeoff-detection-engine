# A side per spot tag, depths by default, the panel in plain words — headed (Edge) on :8100, app.js v=196, style v=98

Five fixes from an evening of the app being driven for real, verified on
the sheet they were reported from: Oncor Cloudcrest C-04 (job
bbf812d1c2e0, engine page index 3 = printed C-04), the one printing 21
spot grades tagged ME / TW / BW / EW. Local throwaway store. Drivers:
scratchpad `live_spotsides.py` (V1–V5) and `live_basisfold.py` (V6);
offline measurement `probe_spot_tags.py`. Readback `readback.json`. No page
or console errors in any run.

| step | readback |
|---|---|
| grading zones, before the panel is open and again while the labels read | the section is greyed, its buttons refuse the mouse (`pointer-events: none`), and it says which of five things it is waiting for — "Press EARTHWORK to read this sheet's elevations first." then "Reading this sheet’s elevations. Zones open when that finishes." while `#ewBusy` shows "reading this sheet's elevation labels — the first read of a page takes a minute or more…". It clears the moment the read ends (`gate_after.wait` false) (`V1-zone-waiting.png`, `V1b-zone-reading.png`) |
| spot grades | four chips, each with its own side, seeded from the engine's reading: **BW 5 PROP · EW 4 PROP · ME 7 EXIST · TW 5 PROP**. The note reads "21 of 21 in use: 7 on the ground now, 14 on the new ground." The run body carries **7 existing_points and 14 proposed_points** — before this it carried 21 on one side, or forced ME off (`V2-spot-sides.png`) |
| swapping one tag | TW → EXIST moves only TW: the chips read ME EXIST, TW EXIST, BW/EW PROP and the body goes 12 / 9. The tag stays switched on, which it did not when the button sat unguarded inside the label (`V3-side-swapped.png`) |
| the standing warning | 45 words on its face, the rest folded, and **no client's job named anywhere on the page** (`limit_has_client` false) (`V4-short-warning.png`) |
| Calculate, then the map | cut 1,508.9 / fill 7,107.0 / net 5,598.2 CY over 61,736 SF; the map printed **87 depths at 30 ft with nothing switched on** — the box starts ticked and turns them off (`V5-map-depths-default.png`) |
| under the volume | 5 lines and 124 words on the face, **179 words folded** behind "how this number was counted ▸"; opening it brings every coverage sentence back (303 words, `aria-expanded` true). Nothing was dropped (`V6-basis-folded.png`, `V6b-basis-open.png`) |

## What the wall tags are worth

Through the product route, on the operator's own rows, 6-point limit and
switches (`probe_spot_tags.py`, four runs of `POST …/earthwork`):

| spot grades fed to the proposed surface | cut CY | fill CY | area SF |
|---|---|---|---|
| contours only | 1,359 | 6,025 | 54,648 |
| + ME (7) on the existing surface | 1,362 | 6,025 | 54,648 |
| + the 14 wall tags (TW, BW, EW) as proposed | 1,506 | 7,107 | 61,736 |

The live run above reproduces the third row through the browser (1,508.9 /
7,107.0 / 61,736). So the 14 wall points reach **13% more ground and carry
18% more fill** than the contours alone — they are the only proposed
elevations out in that eastern strip. They are the top and bottom of a
retaining wall, not ground, which is what the panel's own note says. ME,
the one tag that IS ground, moves the number by 3 CY. The panel now lets
the operator put each tag where it belongs; it does not decide for them.

## Two defects the verification found, both fixed before this folder was written

1. **The old global side came back as a per-tag answer.** Every sheet
   already worked on carries `spotSide: "proposed"` in its sidecar, which
   was the shipped default and not anyone's decision. Restoring it as an
   override pinned ME to the proposed surface on exactly the sheets this
   change was for: the first run came back with all four tags on PROP. Only
   a side the operator can only have chosen ("existing") is carried now.
2. **Two map fetches raced.** Calculate refetches the map whenever the tick
   is on, so the slower POST's meta landed on the faster one's map object
   and the canvas printed no depths at all while the endpoint was serving
   87. Each fetch now writes back only to the map it created.

A third, smaller one: the basis line said "plus 21 spot grades on the
existing surface" — one surface named for all of them, picked from
whichever list was non-empty. It names both now.

Pinned by `tests/test_spot_sides_ui.js` (the two helpers executed against a
synthetic spot set, both bodies, the chips and their guarded click, the
bulk buttons, persistence and the old-sidecar rule, the depths defaults,
the map race, the basis line, the gate's five reasons) and by the updated
assertions in `test_zones_proposed_ui.js`, `test_ew_review.js`,
`test_rough_place_ui.js` and `test_bidsheet_ui.js`. No engine file moved,
so the earthwork baselines are untouched.
