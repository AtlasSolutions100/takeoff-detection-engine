# Zone table and per-zone base thickness — headed (Edge) on :8100, app.js v=181

Barn C3.0 (engine page index 2 = printed sheet C3.0, local throwaway store),
scale 1" = 30' matched by the drawn bar. Drivers: scratchpad
`live_zone_base.py` (draw, declare, save, add, second zone), `live_zone_reload.py`
(fresh open: restore + recompute), `live_zone_table.py` (the table with the
earthwork panel open). Readbacks `readback.json`, `readback_reload.json`,
`readback_table.json`. No page or console errors.

Beaumont (call 2026-09-15): each graded region is its own line — area, cut,
fill, net, total — and a base course under a slab or a road is its own
quantity. The typed elevation is the FINISHED grade; the earthwork stops at
the subgrade below the base; the base is placed over the whole zone.

Two 100 × 80 ft zones (8,002 SF each, four real clicks each, closed on the
first corner) were placed where the sheet's existing contours cross them
(Zone 1 over 7725–7740, Zone 2 over 7695–7710).

| step | readback | expected |
|---|---|---|
| Zone 1 modal: pad at the existing mean 7731.4, strip 8″ (default), base **6 in of Gravel** | live line: "cut 669 CY · fill 770 CY · net 101 CY … base 6″ Gravel over 8,002 SF = **148 CY** — the cut / fill above are figured to the subgrade, 0.5 ft below the elevation typed" (`Z1-modal-base-annot.png`) | 8,002 × (6/12) / 27 = 148.2 CY ✓; subgrade offset 0.5 ft ✓ |
| Save | zone row summary "pad @ 7731.4 · strip 8" · base 6" Gravel"; row line "cut 669 · fill 770 CY · strip 183 CY · base 148 CY"; `#gzTable` one line: Zone 1 · 8,002 · 183 · 669 · 770 · **+101** · 1,439 · 148 | net = fill − cut = +101 (bring in); total = cut + fill = 1,439 ✓ |
| Add to takeoff | rows Zone 1 cut 669 · Zone 1 fill 770 · Zone 1 topsoil strip 183 · **Zone 1 place Gravel 148** CY (`Z4-takeoff-list-annot.png`) | the fourth row is the base |
| Zone 2: pad 1 ft above its mean, no base | table gains Zone 2 (280 / 656 / +376 / 936 / base 0) and **all zones** 16,004 SF · 375 · 949 · 1,426 · +477 · 2,375 · 148 (`Z6-table-two-zones-annot.png`) | column sums ✓ |
| Fresh open of the sheet | sidecar `gzones[0]` = base_in 6, base_name "Gravel"; both zones restore, recompute through the endpoint (layers Gravel 148.2 CY again), the table and the four rows come back | inputs persist, results never stored |

After the reload Zone 1 reads cut 668 / fill 769 (was 669 / 770): the
sidecar rounds drawn corners to 0.1 pt, so the cell grid shifts by a
hair — the documented calcstate behaviour, not a change in the math.

Pinned by `tests/test_zone_base.py` (route: no base → nothing; 6 in base →
185.2 CY cut over a flat 10,000 SF zone and a 185.2 CY layer; finished grade
0.5 ft up → only the base; a 3 + 9 in stack; balance reports subgrade 105.0 /
finished 105.5; strip-only ignores the base; junk rows dropped) and
`tests/test_zone_base.js` (gzBody / gzBase, modal inputs and collect, save,
reopen, calcstate save + restore, four labels, Add/Remove rows, table cells,
signed net, escaping, columns, all-zones row, CSS, hint, cache buster). Both
fail on 86abed9. Earthwork gate re-accepted for the fingerprint only
(server.py moved; 238 baseline fields identical).
