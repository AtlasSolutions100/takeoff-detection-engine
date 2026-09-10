# Trim at a match line (T) — headed (Edge) on :8100, app.js v=174

From the Pierce Site Work call of 2026-09-03: Jacob trims every run at the
sheet's match line so a pipe that carries on to the next sheet is not
counted twice. Before this: Split then Delete, one crossing line at a time.
Driver: scratchpad `live_trim_matchline.py`; readback `readback.json`. No
page or console errors.

No sheet loaded on the dev store prints a match line as text (searched
every upload and the Plans corpus: every "MATCH" hit is a note such as
"MATCH EXISTING ELEVATION"). So the drawn-line path is exercised end to
end on Oncor C-04, and the pick-a-printed-line path is exercised on a
printed straight line up to the point of cutting, then cancelled.

## Oncor Cloudcrest, engine page index 3 = printed C-04

### Drawn match line, keep the left side

The real mouse: T, click at (1711, 432) pt on empty sheet, click at
(1711, 1278) pt, then click 300 pt LEFT of the line. Before: 454 shown
lines, 164,328.6 pt. Precomputed from the geometry: 19 shown lines cross
x = 1711 and 205 lie wholly right of it.

| step | readback |
|---|---|
| T | tool armed, button lit; status "trim at a match line: click the match line on the sheet — or click two points to draw one — then click the side to KEEP (Esc cancels)" |
| first click | "match line: first point set — click its second point" |
| second click | dashed magenta line run out across the whole sheet (`T1-match-line-drawn-annot.png`); "match line drawn — now click the side to KEEP; every shown line on the other side is trimmed away" |
| click left of it | **"trimmed at the match line — 19 line(s) cut (21 piece(s) kept), 205 removed beyond it, 22.1k LF dropped (Ctrl+Z puts it all back)"** — 19 and 205 match the precomputed counts; 251 shown lines, 111,249.3 pt; **0** shown lines have any point right of the line; 21 kept pieces end exactly ON it; Reset edits appears (`T2-trimmed-annot.png`). Style rows moved: "Unnamed (solid) 197 ft → 0 ft", work area 27.1k → 26.1k ft. |
| Ctrl+Z | "undo: trim at a match line — 19 cut, 205 removed — reversed"; 454 lines, 164,328.6 pt — byte-identical count and footage (`T3-undone-annot.png`) |

22.1k LF dropped = 53,079 pt at 1" = 30' (164,328.6 − 111,249.3 = 53,079.3 pt ✓).

### Printed line as the match line, then cancel

T, click chain 3 (a straight 39-point, 675 pt line): "match line: chain 3
— now click the side to KEEP…", cut set to its chord (`P1-status.png`).
Esc: "trim cancelled — nothing changed"; 589 chains, 0 excluded — nothing
moved.

Captures: `T1-match-line-drawn-annot.png`, `T2-trimmed-annot.png`,
`T3-undone-annot.png`, `T*-status.png`, `P1-status.png`; raw beside them.
