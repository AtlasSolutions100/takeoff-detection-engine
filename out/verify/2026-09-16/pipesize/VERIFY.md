# Pipe size on runs — headed (Edge) on :8100, app.js v=189, style v=94

Wave 2 item 8 (Beaumont prices storm per size; Pierce sets the trench
width by the pipe size). Local throwaway store only. Driver: scratchpad
`live_pipesize.py`; readback `readback.json`. No page or console errors.

| step | readback |
|---|---|
| Oncor C-03 (job bbf812d1c2e0, index 3), Tools → Callouts | after the leaders (61, cached) the panel's **Pipe sizes on runs** lists 4 families: `solid solid` 30" on 468.7 ft (1 leader) · 2,716 ft unsized; `dash1 d18.0 g9.0` 18" on 152.7 ft (1 leader) · 2,531 ft unsized; `dash1 d58.6 g21.6` 30" on 91.2 ft (1 leader) · 180 ft unsized → **name it 30"**; `solid solid` 18" on 20.5 ft (1 leader) · 244 ft unsized. The contour family a leader's tip had landed on is not listed (`P1-oncor-pipes.png`) |
| press "name it 30"" | `state.labels[35]` = `dash1 d58.6 g21.6 30"`; status "…named from the leader callout on its runs - the size now travels with the name"; the row re-reads "named 30" — the runs agree (30" on 91.2 ft (1 leader))" (`P2-oncor-named.png`) |
| Barn Civil C3.0 (index 2) | 7 families: `SE-P-UT-WATER-10''` named 10", `SE-P-UT-WATER-6''` named 6" (twice, two families of that layer), `SE-P-UT-WATER-SRV` 12" on 88.2 ft (1 leader) · 133.8 ft unsized → name it 12" (offered, not pressed — a 12" leader on a service line is the operator's call), `dash1 misc` 10" on 64.4 ft → name it 10", `SE-P-EOG MR` 6" on 17.7 ft → name it 6", `solid solid` 10" on 16 ft (too little) (`P3-barn-pipes.png`) |
| bid view, in bid-sheet order, line 16 Water | "2.3k ft · 5 rows" and under it **by size: 10" 1.8k ft · 6" 154 ft · no size in name 409 ft** — the only LF line with a size in its rows' names on this sheet (`P4-barn-bidview-bysize.png`) |

Naming goes through the same path as typing a family's name (colour
convention, decision memory, every view), so the size then reaches the CSV,
the bid sheet's by-size breakdown and the report by name. A family with two
sizes on its runs is said to be mixed and never named; one with under a
fifth of its length sized (and fewer than two sized runs) is listed but not
offered a name.

What this cannot see: the leader → line pairing is the callouts route's
nearest-line guess off OCR text ("OHE 18? RCP FL=586.30? OHE"), so a leader
can size the wrong family — the contour guard removes the worst of it
(three of Oncor's five sized leaders had landed on a contour), the
evidence and the unsized remainder are printed on every row, and only the
operator's press renames. Neither dev-store set prints inline size codes
(8"SD) on its runs, so that source is unit-tested only.

Pinned by `tests/test_pipesize.py` (size_token cases incl. 7'-2" and 218";
family_sizes from name / inline / leaders weighted by feet, named / single /
mixed, the contour guard, the naming threshold, the UI's names honoured; the
route with and without the callouts cache, 404s; the bid sheet's by-size on
LF lines and its CSV text) and `tests/test_pipesize_ui.js` (markup, the
panel's fetch after the leaders, psName through the rename path, the words
for single / too-little / mixed, by-size on every LF line in sheet mode,
busters); `test_orphan_job.py` pins the route (36 live). Earthwork gate
re-accepted for the fingerprint only (238 fields identical); chain gate
PASS.
