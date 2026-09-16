# The architectural datum per structure — headed (Edge) on :8100, app.js v=184

Wave 2 item 2 (Beaumont, call 2026-09-15: "the arch 100 datum can differ per
building; typed per structure, auto-read only as a suggestion, never blind").
Local throwaway store. Driver: scratchpad `live_datum.py`; readback
`readback.json`. No page or console errors.

| step | readback |
|---|---|
| Barn Civil C3.0 (index 2), Structure panel | datum block: input empty, chips from the sheet's own words — **100'-0" equals: 7732′ (page 3)** (printed `[100'=7732']`), T.O. FFE 7735.25′, MAIN LEVEL ENTRY 7733.25′, MAIN LEVEL 7732.25′, LOWER LEVEL FFE 7719.125′ (`D1-civil-datum-chips-annot.png`) |
| click the 7732 chip | input reads 7732, panel datum 7732; nothing else changed on the civil sheet (no marks there) |
| Barn Structural S1-1.3 (index 14) | block says "this set prints no datum statement or FFE summary — the civil set usually does ([100'=7732'] on Barn C3.0); type the value here"; chips read BOF 86′ ×4, 87′, … |
| type 7732 | every chip's BOF now reads both ways: **CF2.0 ×14 · 2 ft wide × 1 ft deep · BOF 86′ (7,718′ civil) ×4, 87′ (7,719′ civil), 96′ (7,728′ civil) ×2, 84.1667′ (7,716.17′ civil) ×3, …** (`D3-struct-bof-both-ways-annot.png`) |
| draw an outline, click CF2.0, Add to takeoff | the draft carries datum 7732; the committed row keeps datum_ft 7732, footing_w_ft 2, footing_ft 1, footing_mark CF2.0; "server agrees ✓" |

The datum is a suggestion with its evidence (page, printed text); only the
typed value applies, per structure. The structural set's own note ("DATUM
ELEVATION 100'-0" EQUALS TOP OF MAIN LEVEL SUBFLOOR ELEVATION", Guide S1-1.0)
is shown as a chip that cannot be clicked, because it says what the datum is,
not what it equals.

Pinned by `tests/test_datum_notes.py` (the reader on the Barn lines and the
Guide note, dedup, civil_of, the document-wide route on a fabricated set,
404, and the real Barn civil set when present) and `tests/test_datum_ui.js`
(block, fetch, chips, the numberless note disabled, the click, calcstate,
POST body, BOF both ways with the draft's datum winning). Earthwork gate
re-accepted for the fingerprint only (238 fields identical).

Open: turning BOF and slab elevations into the dig depth needs the civil
existing grade on the structural sheet (Wave 3, S5); the datum lives in the
civil job and is typed into the structural one — cross-job reading is not
attempted.
