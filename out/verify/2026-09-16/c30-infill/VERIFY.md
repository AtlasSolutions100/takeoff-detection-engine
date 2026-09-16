# The two elevation-read defects behind Barn C3.0's fill — headed (Edge) on :8100, app.js v=182 (server + engine changed, UI unchanged)

SWR Barn Residence Civil, engine page index 2 = printed C3.0, 1 inch = 30 ft.
Local throwaway store. Driver: scratchpad `live_c30_infill.py` (the saved
earthwork rows were cleared from the throwaway sidecar so the panel read the
sheet again; the infill rows were then put on the side the panel suggested
by pressing each row's own EXIST / PROP button, 42 presses, the way an
operator confirming suggestions does). Readback `readback.json`. No page or
console errors.

Yesterday's reconciliation (FINDINGS section 24) found the design surface
was eight contours, one of them the 10 in water main, while 161 drawn
proposed contours carried no elevation. Root causes in the suggest route:

1. **Utility chains seeded as contours.** `_is_plan_chain` now refuses
   utility-token layers and the route excludes utility-named families with
   the reason "utility line, not a contour" (46 families on this sheet: water,
   storm, sewer, gas, electric, irrigation, vent drain).
2. **Siblings counted by style only.** The index contours (C-TOPO, labelled)
   and the 1 ft intermediates (the legend's PROPOSED CONTOUR families) are
   different linetypes by drafting convention, so no intermediate was ever a
   fan candidate. Candidates now join a side's fan by the legend's role.
3. **No minor interval.** Every fan between adjacent index labels was refused
   as "adjacent labels, nothing between" (133 of them) although each crossed
   four unlabelled siblings. The infill now reads that consensus as a
   step/(k+1) interval and counts again at 1 ft.

| | before (a75e228) | after |
|---|---|---|
| rows with a height | 17 (16 radius, 1 inline) | 60 (16 radius, 1 inline, 1 inherited, 42 interval-infill) |
| proposed surface | 8 rows, one a water main | 26 rows, no utility |
| contour footage with a height | 13% | 41% (`I3-result-annot.png`) |
| interval | 5 ft (index labels) | 1 ft minor under 5 ft index, said on every inferred row (`I2-rows-adopted-annot.png`) |
| surface model | clamped_ct | linear TIN (labels 1 ft apart) |
| overlap area | 298,704 SF | 168,776 SF (the water main no longer stretches the proposed hull) |
| **cut / fill / net** | **8,793 / 47,028 / +38,235 CY** | **6,323 / 13,418 / +7,095 CY** |
| Beaumont Estimate #853 | 5,551 / 17,123 / +11,572 CY | same |

Fill is now 22% under his line where it was 2.75x over; cut 14% over where
it was 58% over. The cut/fill map (`I4-map-annot.png`) puts the fill where
the grading is drawn instead of a blanket to the ring. Still open: 290 of
350 contour lines carry no height (count_mismatch 53, no_flanking_label 57,
not_whole_steps 31 from a 7730.5 anchor, repeat_crossing 29 on the two
sides), the strip term (0.67 ft over the overlap = about 4,200 CY of the
fill) and the area question from section 24.

Pinned by `tests/test_infill_minor_interval.py` (fails on a75e228): the
minor interval on a synthetic sheet, no interval invented from one stray
line or from labels with nothing between, the utility-name rules, and the
route end to end on a fabricated page (index labels read, the other family's
intermediates infilled by side, the water main refused under a printed
number and listed with its reason). Earthwork gate re-accepted for the
fingerprint only (server.py, earthwork.py; 238 fields identical); chain gate
untouched.
