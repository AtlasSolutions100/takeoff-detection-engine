# Headed smoke before the 1.8.3 push — Edge on :8100 (throwaway store), app.js v=184

Driver: scratchpad `live_smoke_183.py` (the 1.8.2 smoke, folder renamed).
Captures `01-…png` to `20-…png`, readback `smoke.json`.

20 steps, every page error / console error / 5xx captured per step: login ·
projects (7 demo + 20 own) · demo tab · search · card click open · Oncor C-04
open · p3 detected (589 lines, 65 styles) · style row click · Tools menu ·
count panel · earthwork open (**161 contours with a height, 45 still need a
side** — the minor-interval infill reaches Oncor too; 147 before) · rows ·
Calculate on the saved rows (cut 1,947 · fill 8,306 CY, unchanged) · trench ·
keyboard help · CSV route probe · Settings · light theme · back to sheet ·
1100 px viewport.

Errors: 1 — the script's own HEAD probe on `/export.csv` answers 404 (the
route is GET only; same as the 1.8.1 and 1.8.2 smokes). Nothing else.
