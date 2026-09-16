# Headed smoke before the 1.8.2 push — Edge on :8100 (throwaway store), app.js v=182

Driver: scratchpad `live_smoke_182.py` (the 1.8.1 smoke with one change: a
sheet detected before today's chain.py change says "detected with an older
engine — press Detect", so the script presses Detect like a client would).
Captures `01-…png` to `20-…png`, readback `smoke.json`.

20 steps, every page error / console error / 5xx captured per step:
login page · projects (7 demo + 19 own) · demo tab · search · card click
open · Oncor C-04 open (p0 not detected) · p3 detected after Detect (589
lines, 65 styles, graphic bar 1" = 30') · style row click · Tools menu
(Earthwork, Trench, Structure, Count, Whole job, Across sheets, Callouts,
Legend sheet, Analyze) · count panel · earthwork open (147 contours, read
cached 2 s) · rows · Calculate (cut 1,947 · fill 8,306 CY over 75,800 SF) ·
trench open · keyboard help · CSV route probe · Settings · light theme · back
to sheet · 1100 px viewport (no horizontal scroll, header 52 px).

Errors: 1 — the script's own HEAD probe on `/export.csv` answers 404 (the
route is GET only; same as the 1.8.1 smoke). Nothing else.
