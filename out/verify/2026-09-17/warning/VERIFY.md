# The earthwork warning, re-scored — headed (Edge) on :8100, app.js v=200

Barn Civil C3.0 (job 3bb69406cadc, engine page index 2 = printed sheet 3),
local throwaway store. Driver: scratchpad `live_warning.py`; readback
`readback.json`. No page or console errors.

**Why it changed.** The standing block said the one scored job "came out
about 2.4 times high". That figure is from the build before FINDINGS 26.
Re-scored today on the same sheet and inputs (scratchpad
`probe_manual_inputs.py`), like for like with the client's estimate layout:
cut +43%, fill −36% — off in both directions, not a multiple in one.

| capture | readback |
|---|---|
| `W1-warning.png` | the block on its face: "…read off the drawing, not measured, and it can be **off by a third or more, either way**. It is off the most where contour lines have no number. Use it as a rough check and price from your own take-off." No client's job named. |
| `W2-warning-open.png` | the fold opened. "Where it applies" reads the short version. Before this the app replaced the page's short sentence at run time with a 70-word one about convex hulls, so the short copy written on 2026-09-16 never showed. |
| export note, bounded | "…over 168,776 SF. That region is bounded by the limit of work: only ground inside it is counted." |
| export note, unbounded | "…That region is NOT the graded area: it is where the two convex hulls of the assigned contours overlap. Set a limit of work to bound it." — it used to say "no input on the panel can bound it", false since 2026-09-10 |
| export note, the score | "…On the one job scored against a real bid, cut and fill were each off by up to about 40%, in opposite directions." |

The block under the volume says the same short thing when no limit is set
("…where the two surfaces overlap, a box round the lines").

Pinned by `tests/test_warning_copy.js` (fails on the commit before it) and
the existing `test_ui_audit_2026_09_09.js` phrase check.
