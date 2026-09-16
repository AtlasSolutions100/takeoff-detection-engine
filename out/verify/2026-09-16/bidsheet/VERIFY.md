# The bid-sheet template and the job-level export — headed (Edge) on :8100, app.js v=187, style v=92

Wave 2 item 9 (Beaumont, call 2026-09-15 54:22-59:35: "one sheet with every
quantity, in my bid-sheet order and units"). Local throwaway store only;
the single-password dev box has no accounts, so a save lands on the
install seat (said on the page), exactly as the letterhead does. Driver:
scratchpad `live_bidsheet.py`; readback `readback.json`. No page or console
errors.

| step | readback |
|---|---|
| Settings → Bid sheet (`/#bidsheet`) | 25 lines in Beaumont's printed order with his units — 1 Mobilization LS … 19 Outside Slab Prep SF, his handwritten 20 Radon LF, 21 Site Drains LF, 22 Boulder walls SF, then 23 Silt Fence LF, 24 Septic Tank EA, 25 Propane Tank EA; each with its matches / not words and ↑ ↓ ✕; note "the printed sheet - Beaumont Excavating's standard, as scanned; edit it and Save to make it yours" (`S1-settings-default.png`) |
| remove line 10 (Mass Backfill, struck out on his sheet), Save | "unsaved changes" → "saved · 24 lines on this server"; reload: 24 lines, row 10 is now Slab Prep, `/api/bidsheet` scope `install` (`S2-settings-saved.png`) |
| Reset to the printed sheet | "back to the printed sheet", 25 lines, scope `default` |
| Barn Civil C3.0 (index 2), bid view, tick "in bid-sheet order" | the sheet's 25 lines in order: 3 Strip Topsoil 183 CY · 4 Site Cut 668 CY · 5 Site Fill 875 CY · 6 Mass Excavation 1.8k CY · 7 Perimeter Drain 420 LF · 8 / 9 Structure Ex / Backfill 466.7 CY · 11 Slab Prep 8.1k SF · 13 18" Culvert 267.5 ft (2 rows) · 14 Drive Sub Grade Prep 0 SF, 493.6 LF not summed (other unit) · 15 4" Sewer 407.9 ft (2) · 16 Water 2.3k ft (5) · 17 Elec/Phone/TV 1.5k ft (2) · 18 Gas 285.4 ft · 20 Radon 412 LF · 21 Site Drains 2.0k ft (5) · 22 Boulder walls 0 SF, 1.3k LF not summed · 23 Silt Fence 1.6k ft (the LOD line); 1, 2, 10, 12, 19, 24, 25 "— nothing on this line yet (unit)", dimmed; last "Not on the bid sheet: 66 rows … 166.4k ft · 148 CY — add a word to a line in Settings, or leave them out on purpose"; no bucket pickers (`B1-bidview-sheet-order.png`) |
| untick | the eleven name-rule buckets are back, 99 pickers |
| Approve & export | "exported 1228 elements · 99 items itemized"; the **Bid sheet CSV** link appears beside the Report PDF (`B2-links.png`); fetched: HTTP 200, `bidsheet.csv` (kept here) — one row per line in order, blanks kept: `2,Clear & Grub,SF,,0,` … `7,Perimeter Drain,LF,420,1,p3: Perimeter Drain 420 LF` … `15,"4"" Sewer",LF,408,2,p3: SE-P-UT-SEWER 69.8 LF; p3: SE-P-UT-SS 338.2 LF` … `23,Silt Fence,LF,1632.1,1,p3: BG-Limit of disturbance 1632.1 LF`, then "Not on the bid sheet" with 66 rows and their sheet |
| Report PDF | 7 pages; page 6 is "Bid sheet" — the same lines, blanks kept, "(493.6 LF not summed)" on Drive Sub Grade Prep, "Not on the bid sheet" following (`R1-report-bidsheet-page.png`; the PDF itself was rendered here and discarded — it carries the client's sheet) |

Clear & Grub and the site-wide cut / fill are blank because this page's
saved state has the earthwork rows removed from the takeoff (only the zone
rows, Mass Excavation and the structure rows are in it) — the sheet shows
what the takeoff has produced, nothing more.

The first run on this sheet misfiled four things and the printed default
was corrected before this capture: PROPOSED STORM SEWER on the sewer line
(STORM now vetoes it — it files on Site Drains), the OWTS field / chamber
trench lines on the sewer (OWTS dropped, FIELD vetoes), the contour layers
C-TOPO-MINR-E / C-TOPO-MAJR-E on electric through the one-letter E (TOPO /
CONTOUR veto), BG-L-Boulder Cut on Site Cut (BOULDER veto; it files on
Boulder walls). And the bid view's sheet mode files by the row's NAME only:
with the bucket view's CAD-layer pass it put 27k ft of contour on Boulder
walls and EX-GRAVEL / EX-BREAKLINES on Water — rows the CSV, built from the
approved export (which carries no layer), would never have shown. Screen
and file now agree line by line (Water 5 rows both ways).

What this cannot see: there is no ground truth for the sums beyond
Estimate #853, whose site-wide numbers come from a different reading of the
same sheet (FINDINGS 24); this verifies the FILING and the units, not the
quantities. The report page cannot honour the cross-sheet panel's unticks
(they live in the browser); only the CSV link carries them.

Pinned by `tests/test_bidsheet.py` (the printed default and its fixture,
the matcher on `tests/fixtures/bidsheet_cases.json`, build with units /
ignore / unmatched, the CSV text, the store's scopes and cleaning, a
corrupt file, GET / POST / CSV routes incl. 404s) and
`tests/test_bidsheet_ui.js` (markup, the editor, sheet mode, the CSV link,
the JS matcher on the same cases and group sums); `test_orphan_job.py`
pins the CSV route (34 live); `test_branding.py`'s letterhead count is 6.
Earthwork gate re-accepted for the fingerprint only (238 fields identical);
chain gate PASS.
