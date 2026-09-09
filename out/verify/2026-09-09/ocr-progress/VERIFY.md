# the elevation read's progress readout, verified HEADED (Edge, Playwright headless=False) on :8100

Dev service on the throwaway store `demo-jobs3`; real `takeoff-jobs` untouched.
Driver: scratchpad `live_progress.py` — deletes Belle Farms ROAD GRADING 1 of 2
p0's `p0_elevgaps2*.json` so the read is COLD, presses EARTHWORK, and samples
`#ewBusy`, the bar width, the status bar and `state.suggestReq.progress` every
2 s until the rows land. Samples in `progress.json` (47 of them).

## What the panel showed, in the server's numbers

| t | bar | busy line |
|---|---|---|
| 0 s | – | reading this sheet's elevation labels — the first read of a page takes a minute or more… |
| 2 s | – | … 1s so far; the first read of a page takes a minute or more… (no report yet: the clock-only sentence) |
| 4 s | 1% | … looked at 10 of 1013 places, 0 labels read by OCR, 4s so far… |
| 44 s | 50% | … looked at 510 of 1013 places, 3 labels read by OCR, 44s so far… |
| 86 s | 93% | … looked at 940 of 1013 places, 4 labels read by OCR, 1m 25s so far… |
| 98 s | 99% | … 1m 37s so far… |
| 100 s | – | **read done in 1m 39s — 40 contours with an elevation — assign them below ↓** |

The fraction is regions visited / regions planned from
`gap_label_elevations` itself (1013 planned, 421 tesseract calls, 97 s —
`state.ew.ocr` after the read); nothing on the client invents it. The status
bar carried the same sentence throughout. After the rows landed
`/earthwork/progress` answered `{"phase": "done"}` and the sidecar was gone.
No page errors, no console errors.

## The UI pass on the same panel

Measured on the loaded panel at 1500x940: the disclosure block ("What this
number is…", four paragraphs) plus the grading-zone block filled the first
screen and the 40 contour rows sat below the fold, so before this change the
first screen looked identical before and after a 100 s read.

- the busy line's spot now carries a DONE line with the row count and time
- the disclosure's two middle paragraphs ("Where it applies", "What it does
  not have") fold behind a button; the claim ("not a measured earthwork
  quantity") and the cost ("2.3–2.5× high, measured once") stay on the face
  of the panel and nothing is dismissible — the block's own comment in
  index.html says why it must not be

Captures: `P10-…`, `P40-belle-progress-annotated.png` (mid-read),
`P-end-belle-loaded-annotated.png` (rows landed), `S1-belle-full-raw.png`.

Not changed, worth a look later: the rows still sit under the grading-zone
and limit-of-work blocks; moving the rows above them would put the working
surface on the first screen, but that reorders a panel other tests pin and
was not part of this pass.
