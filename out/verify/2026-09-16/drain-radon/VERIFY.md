# Perimeter Drain and Radon rows off the structure outline — headed (Edge) on :8100, app.js v=180

Barn C3.0 (engine page index 2 = printed sheet C3.0, local throwaway store),
scale 1" = 30' matched by the drawn bar. Driver: scratchpad
`live_drain_radon.py`; readback `readback.json`. No page or console errors.

Beaumont's rule (call 2026-09-15, 32:10-32:49): the perimeter drain is the
lineal footage around the OUTSIDE of the whole foundation; the radon pipe is
the lineal footage along the INSIDE face of the exterior walls. Both come off
the outline the structure calculator already has; the radon run is that
outline pulled in by the wall thickness (typed, default 8 in), with mitred
corners: every convex corner shortens the loop by 2 × t × tan(turn/2) and
every concave corner lengthens it by the same. A rectangle loses 8 t.

The real mouse drew an L (120 × 90 ft with a 60 × 45 ft notch, six clicks,
all six registered, closed on the first corner): 420 LF of wall, 8,110 SF
(the clicks landed 0.4 pt off the intended row, 8,100 SF ideal). Five convex
corners and one concave.

| step | readback | expected |
|---|---|---|
| wall 8 in (default) | result line "perimeter drain 420 LF · radon 415 LF"; stCalc drain 420.00, radon 414.67 (`R1-L-outline-wall8-annot.png`) | 420 − 5 × 2 × 0.667 + 1 × 2 × 0.667 = 420 − 5.33 = 414.67 ✓ |
| type wall 12 in | "radon 412 LF" | 420 − 8 × 1.0 = 412 ✓ |
| Add to takeoff | row meta "drain 420 · radon 412 LF"; "… perimeter drain 420 LF · radon 412 LF · server agrees ✓" (`R3-added-annot.png`); Itemized takeoff carries **Perimeter Drain LF 420.0** and **Radon LF 412.0** after Slab Prep (`R4-takeoff-list-annot.png`) | server `_inset_perim` is the same arithmetic; the cross-check now compares drain_lf and radon_lf too |
| bid buckets (`bidBucketFor` in the live page) | Perimeter Drain → Storm Drainage, Radon → Storm Drainage | RADON token added beside PERIMETER DRAIN |

The volume rows are unchanged by this work: mass 8,109.9 × 6 / 27 = 1,802.2 CY,
dig 420 × 6 × (2 + 3) / 27 = 466.7 CY, both as before.

Pinned by `tests/test_structure_drain_radon.py` (`_inset_perim` on the square,
the L, an over-thick wall floors at 0, orientation; the route's two new
items in LF, order after the four volume rows) and
`tests/test_structure_drain_radon.js` (stPolyInsetPerim mirror, stCalc
default wall, labels/units/sums, inputs, calcstate, cross-check fields,
result wording, RADON bucket token, hint, cache buster). Both fail on f98fa27.
Earthwork gate re-accepted for the fingerprint only (server.py moved;
238 baseline fields identical, prove script VERDICT: ONLY THE FINGERPRINT MOVED).
