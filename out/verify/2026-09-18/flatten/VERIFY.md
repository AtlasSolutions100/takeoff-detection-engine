# Flatten — 2026-09-18

Headed Edge on `http://127.0.0.1:8100`, dev, `app.js?v=208`.
Driver: `scratchpad/live_flatten.py`. Readback: `readback.json`.
Zero page errors, zero console errors.

## Why

Christian, on the 2026-09-18 call, measuring the same property line two
ways: the panel read **356 ft** where his own straight measurement read
**347** — about 9 ft on 350, **~2.5%** — because the detected polyline
jitters between its vertices instead of running straight. Most lines on a
sheet are clean; a few are *"all zigzagged and weird"*. He asked for a
manual fix: click the line, press a key, click where the straight run starts
and where it ends.

## What it does

Pick two points on the selected line; every vertex **strictly between** them
is replaced by one straight segment. The two picks themselves do not move —
straightening a run is a claim about the middle, not about where the run
begins and ends.

Same path as every other line edit: `materialize` clones the chain and
excludes the original, so the export sees the edit; it is undoable; the
measured `length_pt` travels with the geometry by the same proportional rule
`cutSpanClick` uses.

**The key is R, not F.** `f` is already the line-rendering toggle and has
been since long before this was asked for; moving it would break a shortcut
people use all day. The tooltip says so: *"(R; F is line rendering)"*.

## Live on Oncor C-04 — `F1-before.png`, `F2-after.png`

Driven with **real mouse clicks**, mapped through the inverse of
`ptFromEvent`, on a line the script picked automatically as the wobbliest
long one on screen:

| | before | after |
|---|---|---|
| points | 29 | **2** |
| length | 491.9 pt | **352.9 pt** (−28.3%) |

352.9 pt is exactly the straight-line distance between the two picks. The
original is excluded from the export, the selection bar reads *"147 LF · 2
points"*, and the status line says *"flattened — 139 pt of zigzag removed"*.

**Be careful reading that −28.3%.** The script chose the *wobbliest*
candidate (39% longer than straight), which is a genuinely crooked line
rather than the ~2.5% jitter Christian measured. The tool did exactly what
it was told; that number is not an accuracy claim. **Flatten is a manual
tool and the operator decides what is jitter and what is a real bend** — run
it across a true curve and you will lose real length. The realistic case is
pinned in the unit test instead: a 350-unit run with a jitter sized to
Christian's own measurement reads 358.4 and flattens to 350.0, **2.3%
recovered**.

## A defect found and fixed while writing the test

`projectOnChain` snaps to a nearby vertex, so picking a line's own corner
returns that corner as the projected point with the index one past it — and
the slice then wrote the vertex **twice**: `[p0] + [p0, p5] + [p5]`. The
length was right (a zero-length segment adds nothing) but the chain carried
duplicate consecutive vertices into the export and into every renderer
downstream. `dedupePts` strips them; the test now asserts 2 points, not 4.

A second thing the live run caught: the first attempt's clicks were computed
from the canvas rect alone, and the first pick landed on a vertex sitting
**under a floating panel** — so it never reached `handleCanvasClick`, and the
tool sat waiting for an end point that had already been spent. The driver now
requires `document.elementFromPoint` to be the canvas before it will click
there. That is a test-harness fix, not a product one, but it is exactly the
kind of thing that would have made this look like it worked when it had not.

## Tests

`tests/test_flatten.js` — 24 checks in two parts:

- **wiring**: the button, the tooltip naming R and why not F, POINT_TOOLS
  membership, the click dispatch, the first-anchor marker, disabled with
  nothing selected, released before an undo, R bound, and **F still bound to
  line rendering**.
- **geometry**, on the real `polyLen` / `projectOnChain` / `materialize` /
  `dedupePts` / `flattenClick` lifted out of `app.js` rather than
  re-implemented: vertices between the picks removed, the run becomes the
  straight distance, the picks do not move, picking end-first gives the same
  edit, `length_pt` follows the geometry, it is undoable, and two picks on an
  already-straight run **change nothing, exclude nothing and say so**.

Fails on `7b96ca4`.
