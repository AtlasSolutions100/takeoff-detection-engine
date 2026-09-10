# Join across families in one press; a point can leave a three-point line — headed (Edge) on :8100

Both from the Pierce Site Work call of 2026-09-03 (on screen, transcript).
Drivers: scratchpad `live_join_families.py`, `live_delete_point.py`;
readbacks `readback.json`, `readback_delete.json`. No page or console errors.

## J. Join across families — Belle Farms ROAD GRADING 1 of 2, engine page 0

The closest-ended pair of visible chains in DIFFERENT families: chain 669
("dash1 d36.0 g18.0", 1,869.6 pt) and chain 966 ("solid solid",
1,891.4 pt), nearest ends 1.1 pt apart - two pieces of one run the detector
filed under two families, the case that needed R then J before.

| step | readback |
|---|---|
| select 669, press J, click 966's body | status: **"merged 2 lines into 1 — 3762 pt total, 1 pt bridged — 1891 pt moved from solid solid into dash1 d36.0 g18.0 to join (Ctrl+Z puts it back)"** |
| the merged line | family 15 (the selected line's), 1,474 points, 3,762 pt; both originals excluded |
| Ctrl+Z | "undo: join across families — 3.1k LF — reversed"; 669 back in family 15, 966 back in family 47 |

One gesture, one undo step, the footage move said out loud. A same-family
join is unchanged (pinned in the test).

## D. Point delete on a three-point line — Oncor Cloudcrest engine page index 3 = printed C-04

42 three-point lines on the sheet, 13 in shown families. Chain 59 (3
points): zoomed to 4x, the real mouse clicked its middle vertex.

| step | readback |
|---|---|
| mouse click on the vertex | `selPt` 1; status "point 2 of 3 picked — drag it, or Delete removes it" (before: "point moved — line now …" with nothing moved) |
| Delete | "point removed — line now 84 pt"; the line has 2 points; the original is excluded and the edited copy is selected (before: "too few points left — remove the whole line instead", and the next Delete with no point picked took the whole line) |
| Ctrl+Z | the original is back with 3 points |

Captures: `J1-joined-across-families-raw.png`, `J2-after-undo-raw.png`,
`D1-vertex-picked-raw.png`, `D2-point-deleted-raw.png`.
