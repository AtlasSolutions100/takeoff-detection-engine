# Flatten keeps corners — 2026-09-18

Headed Edge on `http://127.0.0.1:8100`, dev, `app.js?v=209`.
Driver: `scratchpad/live_flatten2.py`. Readback: `readback.json`.
Zero page errors, zero console errors.

## What was wrong with the first version

Flatten replaced **everything** between the two picks with one straight
segment. Right for jitter along a run; destructive across a corner.
Measured on an L-shaped perimeter (200 across, 200 down, jitter on each leg),
`scratchpad/probe_flatten_L.js`:

| picks | first version | now |
|---|---|---|
| **across the corner** | 406.7 → **282.8 pt** — the bare diagonal, corner gone, **30.5% deleted** | 406.7 → **400.0 pt**, 3 points, **corner kept**, only the 1.7% jitter removed |
| rectangle, opposite corners | 320 → 276.6 pt | 320 → **320.0 pt**, both corners kept, nothing lost |
| within one leg | corner survives | unchanged |

One careless pick could quietly delete a third of a perimeter.

## How it tells a corner from a wobble

Douglas–Peucker over the picked span: every run between corners comes out
straight, every corner stays where the drawing put it. Pure jitter has no
corners, so it still collapses to one segment — the case it was built for is
unchanged.

**The 4.0 pt tolerance is measured, not chosen.**
`scratchpad/probe_jitter_scale.py`, three sheets, 222,760 vertices:

| tolerance | vertices surviving | total length |
|---|---|---|
| **0.5 pt** | 22% (so 78% vanish) | −0.03% / −0.23% / −0.25% |
| 1.0 pt | 17–19% | −0.08% / −0.45% / −0.59% |
| 3.0 pt | 9–13% | −0.41% / **−1.68%** / **−2.59%** |

Genuine jitter is the population below half a point: 78% of all vertices
carrying essentially no length. By 3 pt the tolerance is eating real shape,
because contours are genuinely curved. **4.0 pt sits in the gulf** — eight
times above the noise, far below any real bend — and because Douglas–Peucker
keeps whatever deviates, a gentle curve keeps its shape rather than being
straightened into a chord.

## Live on Oncor C-04 — `F2-after.png`

Real mouse clicks, on a line the script picked for having both corners and
jitter worth removing:

| | before | after |
|---|---|---|
| points | 36 | 7 |
| length | 232.2 pt | **220.2 pt** (−5.2%) |

Status: *"flattened — 12 pt of zigzag removed, 1 corner kept"*. The original
is excluded from the export.

The picks snapped a little inside the line's ends (the vertex-snap radius is
~21 pt at 20% zoom), so the untouched tails remain — which is why 7 points,
not 4. **Only the picked span is flattened; the rest of the line is left
alone**, which is the intended behaviour.

## Two refusals, and they say different things

The first version answered *"that run is already straight"* to everything it
declined, including a line that was refused **because it was bent** — which
reads as a broken tool. Now:

- **every bend bigger than trace noise**: *"every bend in that span is bigger
  than trace noise, so they look like real shape and nothing was flattened —
  hold Shift on the second click to straighten it anyway."*
- **nothing to remove**: *"nothing to flatten — those points already sit on
  straight runs (4 corners)."* Seen live on chain 254: 102 points, 4 real
  corners, Douglas–Peucker reduces it to 6 points but the length moves
  **0.16 pt** — densely-sampled clean legs, nothing to gain, so it declines
  rather than churn the chain.

**Shift on the second click forces the straight chord** — the behaviour the
first version had. Taking that away outright would have removed a capability
that shipped this morning, and the operator sometimes does know better than a
4 pt threshold. The refusal offers it at the moment it is needed.

## Tests

`tests/test_flatten.js` — 30 checks. New cases: the L keeps its corner at the
drawing's own coordinates and the length survives (the diagonal would have
been 282.8); a rectangle keeps every corner and every foot; bends bigger than
trace noise are refused with the real reason and the Shift hint; Shift takes
it; and the original jitter case still collapses to one segment.
