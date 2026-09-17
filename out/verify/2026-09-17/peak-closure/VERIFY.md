# Peaks and low points: closing the innermost contour — 2026-09-17

Headed Edge on `http://127.0.0.1:8100`, dev branch, `app.js?v=205`.
Driver: `scratchpad/live_closure.py`. Readback: `readback.json`.
Zero page errors, zero console errors on both sheets.

## What was changed

A surface built from contours cannot rise above its highest contour, so a
summit comes out flat and a pond bottom a floor. `earthwork.closure_points`
puts ONE point inside the innermost closed ring — half a contour interval
past it, the hand-takeoff convention — and the volume routes feed it into
the TIN. `close_peaks: false` turns it off; a ground spot inside the ring
beats the convention outright.

## The accuracy claim, against closed-form truth

`tests/test_peak_closure.py` part 5, on the analytic dome
`z = 600 − r²/4000` integrated above a 540 datum over a fixed region:

| case | exact | before | after |
|---|---|---|---|
| dome, 10 ft contour interval | 826,007 CY | 799,406 (**−3.22%**) | 807,160 (**−2.28%**) |
| dome, 1 ft contour interval | 826,007 CY | 825,535 (−0.06%) | 825,613 (−0.05%) |

At a 10 ft interval closure recovers about a third of the truncation error;
at 1 ft the rings already reach the top, so it cannot spoil a sheet it does
not apply to. (`scratchpad/proto_peak_closure.py` measured the same −3.21%
→ −2.28%, and −1.34% if an estimator types the true summit — that is the
ceiling on what closure can do.)

## What it does on the real sheets — nothing, and that is the finding

`scratchpad/score_closure.py`, both volume routes, closure off then on:

| sheet | cut off → on | fill off → on | closed |
|---|---|---|---|
| Oncor C-04 p3 | 2,056 → 2,056 CY | 9,727 → 9,727 | none; 1 ring refused |
| Barn C3.0 p2 | 8,048 → 8,048 CY | 9,884 → 9,884 | no closed ring at all |
| C2.1 Grading p0 | (refuses: the existing surface carries one elevation) | | 2 nested rings, innermost not closed |

**Not one scored number moves.** The corpus has no legitimate closed
innermost contour ring: Barn has none, C2.1's two rings are nested with the
innermost one drawn open, and C-04's single ring is not a contour at all.

## The refusal, live on C-04 — `P1-proof.png`

C-04's only closed ring is chain 685: **five points, 28,600 SF, elevation
569.9 assigned by `radius`** on a sheet whose contour interval is 1.0 ft. A
pad or structure outline that caught a nearby spot label. Closing it would
have added half a foot of fill over two thirds of an acre, silently.

The panel says so instead (orange box in the capture):

> Peaks and low points: left 1 ring open
> · proposed 569.9 ring, 28,600 SF — not closed
>   drawn with 4 vertices - a contour is a polyline of many; this is a pad
>   or structure outline that caught a nearby elevation

Three guards produce that refusal, each measured on this ring:
vertex count (a CAD contour is a polyline of many; this is a rectangle),
the elevation being off the family's own 1 ft grid, and "innermost" tested
by how much of another contour lies inside rather than by one midpoint —
three lower contours had their midpoint inside this ring and none had half
its length inside.

## Barn C3.0 — `P2-proof.png`

No closed ring, so the panel says nothing about peaks. Volumes and every
proof check are unchanged from the volume-proof run earlier today.

## Defect found and fixed during this work

The refusal record passed `a`, a stale leftover from the ring-collection
loop, while the iteration variable was `area` — so every refusal stated the
LAST ring's area instead of its own (C-04's read 12,223 SF against the true
28,605). Caught by the area assertion in part 2 of the test; the collection
variable is now `ring_area` so the shadowing cannot come back.

## Tests

- `tests/test_peak_closure.py` — 5 parts, 18 checks. Errors out on `9c57c03`
  (`closure_points` does not exist).
- `tests/test_peak_closure_ui.js` — 15 checks. 12 fail on `9c57c03`.

## Still open

Breaklines — the other half of stage 2 — are not built. The measured case
for them is unchanged (C-04 discards 3 grade breaks + 3 swales, 1,403 ft;
Barn 22 retaining walls + 7 swales, 1,718 ft), but so is the measured fact
that C-04's seven residual spots all sit in ordinary contour brackets, so
breaklines are not what is causing the +0.61 ft bias there.
