# Watch-list callouts — headed (Edge) on :8100, app.js v=188, style v=93

Wave 2 item 7 (Beaumont, call 2026-09-15: "so many call outs everywhere" —
he rejected a full callout dump and asked for a keyword watch-list with the
tank size read off the callout, septic and propane separated by type).
Local throwaway store only; Barn Civil (job 3bb69406cadc). Driver:
scratchpad `live_watchlist.py`; readback `readback.json`. No page or console
errors. Detect was NOT pressed on C2.0 — the watch-list is words only.

| step | readback |
|---|---|
| C2.0 (index 1), Tools → Callouts, no Detect | the panel opens with the **Watch-list** first: Septic Tank ×1 · 1,500 gal · RE. TO OWTS; Propane Tank ×1 · 18,000 gal · RE. TO MEP; Area Inlet ×4 · no size printed beside it; Cleanout ×9 · 2-way ×3 · RE. TO MEP ×6 — each with a "+ n EA" button and its printed items ("PROPOSED 1,500 GALLON SEPTIC TANK; RE. TO OWTS PLANS SUBMITTED SEPARATELY [RE. TO OWTS]" …); below it the leader list says "run Detect first" (`W1-c20-watchlist.png`) |
| click the septic item | the canvas centres on (2140, 249.5) pt — the wrapped two-line callout — and hovering it draws the mark there (`W2-c20-jump.png`) |
| "+ 1 EA" on the septic row | `state.extraItems` gains **Septic Tank 1,500 gal · 1 EA**; the button reads "in the takeoff" and is disabled; status "Septic Tank 1,500 gal: 1 EA added from the sheet's callouts" (`W3-c20-added.png`). That row files onto bid-sheet line 24 by its name (not shown here: the bid view needs a detect result to render) |
| C3.5 (index 9), the storm / foundation drain plan | Area Inlet ×19 · Cleanout ×3 (RE. TO MEP ×3) · Wye ×12, one count per printed callout; the "PROVIDE CLEANOUTS AT ALL DOWNSPOUT LOCATIONS…" note and the "NDS AREA INLETS: 6\" MIN…" note never count (`W4-c35-watchlist.png`) |

Two slips the real sheet exposed, fixed before this capture and pinned:
`7'-2" AREA INLET` had read the 2" of a feet-and-inches dimension as a pipe
size (the inch regex now refuses a `'-` / `′` tail), and the last line of a
wrapped note ("FOR ALL CLEANOUTS PRIOR TO INSTALLATION.") had counted as a
cleanout (a block whose first word is a continuation word, or that says
SHALL, is a note). Cleanouts on C2.0 went 10 → 9.

What this cannot see: whether 19 area inlets on C3.5 is the number Beaumont
would bid (each is a printed callout; a symbol with no text beside it is
not seen here — the symbol counter is the tool for that); "RE. TO MEP" is
a tag, not an exclusion — the estimator decides (BY OTHERS / N.I.C. are
left out of "+ n EA" and said so).

Pinned by `tests/test_watchlist.py` (blocks from stacked short lines, a
wrapped tank callout as one block, notes / legend keys / numbered notes /
fragments / SHALL never count, gallons and inches and 2-WAY, BY OTHERS /
N.I.C. / RE. TO tags, in_scope apart from count, overprinted text once,
positions; the route on a fabricated sheet and 404s; Barn C2.0 / C3.5 when
the client set is present, skipped otherwise) and `tests/test_watchlist_ui.js`
(markup, the panel fetches the watch-list before the leaders, add / hover /
jump wiring, names with a single size, styles, busters);
`test_orphan_job.py` pins the route (35 live). Earthwork gate re-accepted
for the fingerprint only (238 fields identical); chain gate PASS.
