# Extend grows the end the operator meant — headed (Edge) on :8100, app.js v=173

From the Pierce Site Work call of 2026-09-03 (on screen): "it goes to the
closest end point... funky". Extend grew whichever end was nearer the
DESTINATION click. Now a picked end vertex wins, else the end nearer where
the line was clicked to select it, else the old rule. Driver: scratchpad
`live_extend_end.py`; readback `readback.json`. No page or console errors.

## Oncor Cloudcrest, engine page index 3 = printed C-04, chain 14 (solid, 2 points, 144 pt, title block)

The real mouse clicked the line 15% in from its LEFT end to select it, then
E, then a point 12 pt right / 22 pt up of the RIGHT end (25 pt from the
right end, 158 pt from the left).

| step | readback |
|---|---|
| E | `extendEnd` = the left end's index; status **"extend: the left end (ringed) grows to your next click — …"**; magenta ring on the left end (`E1-armed-ring-annot.png`) |
| click nearer the RIGHT end | the LEFT end grew: 3 points, +158 pt (status "+66 LF — now 126 LF"); the right end untouched (`E2-left-end-grew-annot.png`). Before: the right end would have grown by 25 pt. |
| E again | the NEW end is armed and ringed ("the top end (ringed) grows…") — repeated E/click walks one end (`E3-same-end-armed-annot.png`) |
| Esc, Ctrl+Z | "undo: extend — 66 LF — reversed"; chain 14 back with 2 points |

`E*-status.png` are the status-bar crops. The fold-back shape in E2 is
what was asked for (grow the left end toward a point on the right) — the
point is that the operator's end grew, not the nearer one.
