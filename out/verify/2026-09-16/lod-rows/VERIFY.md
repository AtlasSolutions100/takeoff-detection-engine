# One limits pick, three rows — headed (Edge) on :8100, app.js v=177

SWR Barn Residence Civil (client set, local throwaway store only), engine
page index 2 = printed C3.0, detected hybrid, scale 1" = 30' matched by the
drawn bar. Driver: scratchpad `live_lod_rows.py`; readback `readback.json`.
No page or console errors.

| step | readback |
|---|---|
| open Earthwork | 17 contours with a height; the limit row offers **"use the sheet's BG-Limit of disturbance"** (the LOD family found by CAD layer; `L1-limit-offered-annot.png`) |
| one click | "10 lines stitched (BG-Limit of disturbance) — 329 points — **312,044 SF inside, 2,159 LF around**"; status "7 gaps bridged, largest 153 ft"; the ends are 248 ft apart and joined for the ring (`L2-ring-picked-annot.png`) |
| area note | "Blank = the limit of work you set (312,044 SF): Clear & Grub and Strip Topsoil cover all of it." |
| Strip topsoil 8 in, Calculate | `area_source` "limit-of-work outline", `layer_area_sf` 312,044, reply carries `boundary {area_sf 312044, perim_ft 2159, gap_ft 247.8, is_limit true}`; Strip Topsoil 7,704.8 CY; cut 8,793 / fill 47,028 CY over the 298,704 SF overlap inside the ring |
| Add to takeoff | Site Cut 8,793 CY · Site Fill 47,028 CY · Strip Topsoil 7,705 CY · **Clear & Grub 312,044 SF** · **Silt / Construction Fence 2,159 LF** (`L3-rows-added-annot.png`) |

Honest reading of the numbers: the sheet's BG-Limit of disturbance family
encloses 312,044 SF, while Estimate #853 bids Clear & Grub at 224,058 SF
and the client's own C3.0 markup on the call read 142,760.6 SF. The pick,
the rows and the arithmetic are right; WHICH line he strips to, and whether
the stitched ring (7 bridges, one 153 ft) is the ring he means, is the
reconciliation item (Wave 1 #8) and a question for him. The panel's
standing disclosure (2.3-2.5x high on this job) is unchanged by this.

Pinned by `tests/test_ew_limit_rows.py` (boundary_metrics; the route with
boundary_is_limit false/true; typed area wins) and
`tests/test_ew_limit_rows.js` (request flag, rows, EW_ITEM_RE, the LOD
family pick by name or layer). Earthwork gate re-accepted with proof
(238 fields identical); chain gate PASS.
