# Trench layback, utility presets, dry-utility buckets — headed (Edge) on :8100, app.js v=179

Barn C3.0 (local throwaway store), 1" = 30'. Driver: scratchpad
`live_trench_layback.py`; readback `readback.json`. No page or console errors.
Pipe run picked: the sheet's SE-P-ST… storm family, 1,000.45 LF, 6 in pipe,
bedding rules 2/4/99 ft at 6/12/18 in.

| step | readback |
|---|---|
| Utility = Sewer | both inverts 6 ft, layback 1 (1:1), lay back below 4 ft — the preset only fills the fields (`T1-sewer-preset-annot.png`) |
| Calculate, laid back | "dig 2,112.1 CY · bedding 446.6 CY · backfill 1,658.2 CY · 1,000 LF of pipe · sides laid back 1:1 where deeper than 4 ft" |
| layback 0, Calculate | "dig 778.1 CY · bedding 446.6 CY · backfill 324.2 CY · 1,000 LF of pipe · vertical sides" (`T2-vertical-annot.png`) |
| the V | dig difference 36,016.2 cf = 36 sf × 1,000.45 LF **exactly**; bedding unchanged; the whole wedge went to backfill |
| bid buckets on this sheet | P-UT-ELEC-UG, PROPOSED UNDERGROUND ELEC. (legend), P-UT-ELEC-VAULT → **Electric/Comm**; SE-P-UT-GAS, P-PROPANE → **Gas** (all were UNASSIGNED before) |

Pinned by `tests/test_trench_layback.js` (pure block: 9 sf column + 36 sf V
at 6 ft, threshold, 0.5:1, sloped 2→8 ft wedge only past 4 ft, bedding
untouched, wiring, hint), `tests/test_bid_dry_utilities.js` (buckets,
G/E label-only, SELECTED is not electric, wet systems unchanged) and the
updated pins in `tests/test_bidview_rules.js`. `tests/test_trench_math.js`
still passes untouched: with no layback the numbers are the old numbers.
