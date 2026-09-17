# The label reader finishes, and a stopped read carries on — headed (Edge) on :8100, app.js v=202

Barn Civil (job 3bb69406cadc), local throwaway store, dev service on the
final code. Engine page index 2 = printed sheet C3.0; engine page index 3
= printed sheet C3.1. Driver: scratchpad `live_reader.py`; readbacks
`readback-keep.json` / `readback-full.json`. No page errors, no console
errors in either run. Captures are the ANNOTATED ones only, 256-colour
(14.1 MB → 5.0 MB; a 12 MB capture push was refused with HTTP 408 this
morning); the unannotated originals stayed in the scratchpad.

## What the stored reads said before (the old reader, same store)

| sheet | stored read |
|---|---|
| C3.0 | `stopped_early: true, "cap"` — 426 reads over 1,901 places in 900.3 s, one value (826, on a thermal-well line) |
| C3.1 | `stopped_early: false` — but 1,400 reads over 2,620 places: the old 1,400-call budget, which that reader did not report |

Both were put back in the store before these runs, so each page starts
where a user with an old read starts.

## A. Keep reading — C3.0, service started with `TAKEOFF_OCR_BUDGET_S=2`

The 2 s clock is a TEST SETTING on the dev service, so the read stops on
purpose and the button can be exercised; the shipped default is 900 s, and
at that default this sheet reads in one pass (§B, and FINDINGS 38 §4:
1,644 places in 28.7 s offline). Nothing else is staged: the stale 900 s
cache above is what the panel found.

| capture | what it shows |
|---|---|
| `K2-stopped.png` | round 1 stopped: "**Label reading stopped early.** It checked 341 of 1644 places in 7 s and hit its time limit, so some elevations may be missing. **Keep reading** or add any you can read with **+ Add contour**." |
| `K2b-continuing.png` | round 2 in flight, the count carrying on from 341 |
| `K3-stopped.png`, `K3b-continuing.png` | round 2 stopped at 694; round 3 reads from 694 — "looked at 694 of 1644 places, 3 labels read by OCR, 7s so far", bar at 42% |
| `K4/K5-stopped.png`, `K4b/K5b-continuing.png` | 1,091 then 1,461 places |
| `K9-finished.png` | finished after 4 clicks: 1,644 of 1,644 places, `resumed=true`, the stopped-early line GONE, 60 contours with a height |

Rounds (readback): 341 → 694 → 1,091 → 1,461 → 1,644 places; 192 → 384 →
576 → 768 → **861 reads**, which is exactly what one uncut read of this
sheet costs (FINDINGS 38 §4). 11–15 s of wall time per round on this box
(page render ~4 s + the 2 s clock + the batches in flight + the rest of
the route). The kept place (`p2_elevgaps2.resume.json`) exists between
rounds and is deleted when the read completes; `resumable` follows it.
Rows stay 60 of 350 contour lines throughout — the fuller read finds no
new heights on this sheet (FINDINGS 38 §5-6).

**A defect this run found and fixed:** on the first pass (v=201) the old
"stopped early" box, button and all, stayed on screen while the next round
was reading. `ewKeepReading` now clears it and `renderEw` repaints it from
the new read; pinned by `tests/test_keep_reading_ui.js`
("notice-during-read"), which fails without the fix.

## B. A full read — C3.1, service on its default 900 s clock

`F1-reading.png`, `F2-done.png`: the cache the OLD reader left (1,400
reads, "not stopped early") is read again by itself — the server re-reads a
cut-short cache from an older reader, including one its 1,400-call budget
stopped — and the new pass covers **2,620 of 2,620 places, 2,011 reads,
205.8 s, `stopped_early: false`**, 88 tesseract launches, 0 batch
fallbacks. No "stopped early" line on the panel. The sheet still yields 0
contour rows and says why in its own words: "6 candidate rows were read off
this sheet and dropped… these 5 elevations do not step like contours: the
commonest gap between adjacent values is 7.3 ft and it occurs 1 time in 4
(25%)." That is the ladder gate refusing dimension text read as
elevations, which is correct on this sheet (its heights are 7,7xx).

## Timing on this box varies, and it is not the reader

The same C3.0 read measured 5.2 s, 28.7 s and (on a first live run,
mid-afternoon) minutes, on identical code and inputs: Windows process
launches here cost 0.3–1.2 s and the box has other tenants. During that
slow live run each round also spent 1.5–4 min outside the reader, with the
service process burning CPU that no request explains; it did not reproduce
in four later runs (10–15 s a round), and the cause is NOT identified —
carried as an open item in FINDINGS 38. The route itself, with the read
cached, profiles at 4.6 s (scratchpad `probe_suggest_profile.py`).
