# Barn C3.0 vs Beaumont Estimate #853 — reconciliation run (diagnosis only), headed (Edge) on :8100, app.js v=182

SWR Barn Residence Civil, engine page index 2 = printed C3.0, scale
1 inch = 30 ft (caption, matches the drawn bar). Local throwaway store.
Driver: scratchpad `live_c30_recon.py` (the sheet said "detected with an older
engine — press Detect" after today's chain.py change, so Detect was pressed
first, as a client would); analysis `recon_c30.py` on the ring the app
stitched; readback `readback.json` (ring points, full result, three variants
through the same endpoint), `cutfill-map.png` (the endpoint's map), `dz.npy`.
No page or console errors.

| step | readback |
|---|---|
| Earthwork panel | 17 contours with a height (read cached, 4 s); "use the sheet's BG-Limit of disturbance" offered |
| Limit picked | 10 lines stitched, 329 points, 312,044 SF inside, 2,159 LF around, 247.8 ft of gaps bridged (the ring on the sheet: `../lod-rows/L2-ring-picked-annot.png` from the same pick earlier today; `R2-ring-and-map-raw.png` here landed on the house at 78%, kept as the raw it is) |
| Strip 8 in, Calculate | cut 8,793 CY · fill 47,028 CY · net 38,235 CY over 298,704 SF · deepest cut 19.33 / fill 15.67 ft · existing lowered 0.67 ft (`R1-panel-result-annot.png`) |
| Rows in the takeoff | Site Cut 8,793 · Site Fill 47,028 · Strip Topsoil 7,705 · Clear & Grub 312,044 SF · Silt / Construction Fence 2,159 LF · Export Topsoil 7,705 · Import Structural 38,235 |
| Variants (same endpoint) | no strip: 10,131 / 40,991 · no limit: 13,691 / 49,410 over 317,632 SF · neither: 15,315 / 43,192 |

His Estimate #853 (4/29/2026): Clear & Grub 224,058 SF · Strip 5,730 CY ·
Site Cut 5,551 · Site Fill 17,123 · Import Structural 11,581 · Silt fence
1,161 LF. The table and the root-cause ranking are in
`ground-truth/swr-barn-civil_p2.json` and FINDINGS section 24.

The in-process analysis (same surfaces, same ring, `recon_c30.py`) reproduces
the app's 8,793 / 47,028 exactly and shows where the fill is: 43,800 CY in
cells 4 ft or deeper, 28,543 CY more than 25 ft from any proposed contour that
carries an elevation. The design surface is 8 rows — seven C-TOPO index
contours and the 10 in water main (chain 6152, 1,756 ft) read as a 7720
contour — while 161 drawn proposed-contour chains (36,365 ft) carry no height.

Nothing in the engine was changed for this item.
