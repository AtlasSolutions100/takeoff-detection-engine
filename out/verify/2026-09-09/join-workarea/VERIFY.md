# Join joins again; the work area reaches earthwork; the panel's notes in plain words — headed (Edge) on :8100

Belle Farms Upper Parcel ROAD GRADING 1 of 2, engine page 0 (job 064435770e66,
throwaway store). Driver: scratchpad `live_join_workarea.py`; readback in
`readback.json`.

## J. Join (J key)

Reported: "it used to connect two halves as one line; now it extends to
where I click." Cause: since 330d7e9 a click on the other piece's BODY went
to the tee path (`connectToPoint`), which grows the selected line to the
click point and leaves the other piece as its own line. `mergeClick` now
always merges end to end; the tee stays in Extend.

| step | readback |
|---|---|
| pair picked | chains 160 and 164, same family, nearest ends 2 pt apart (two pieces of one contour), 229.4 pt + 200.7 pt |
| J then click on 164's body (its middle point) | status: **"merged 2 lines into 1 — 432 pt total, 2 pt bridged"**; chain 164 excluded; tool back to null |
| the J prompt | "join: click the other piece of this line. The two pieces become one line, end to end (Esc cancels)" |

432 = 229.4 + 200.7 + 2, i.e. both pieces whole plus the gap - not one
piece grown by a connector. Capture `J1-joined-raw.png`. (Undo restored it.)

## W. Work area → earthwork

Reported: a work-area box did not reach the earthwork tab. Now a contour
with none of its length inside the boxes is out of the list and out of the
surfaces, spot grades outside are not sent, one box is the limit of work
when none is set, and the panel says how many it left out.

| | no box | one box over the left half | box cleared |
|---|---|---|---|
| rows listed (with no-height stubs) | 334 | 199 | 334 |
| contours reported outside | 0 | 135 | 0 |
| EXIST rows sent to the volume | 40 | 28 | – |
| boundary sent | none | the box (4 points) | none |
| Limit of work line | "not set — the whole overlap of both surfaces" | "work area box — 4 points — 907,476 SF" | "not set …" |
| note above the rows | – | "135 contours are outside the work area, so they are left out of this list and out of the volume. Clear the work area to get them back." | gone |

The box was set through the same state + `wzChanged()` the drag tool ends
in. Captures `W1-workarea-raw.png`, `W1b-workarea-panel-crop-raw.png`.

## P. The notes, rewritten plainly

`P1b-panel-notes-crop-raw.png`: a six-step "How to do this" list at the
top (each row is one line; click it and it lights up; drop it if it is not
a contour; pick EXIST / PROP / skip; fix the number; draw a work area or
limit; press Calculate), then the two-sentence claim and the one-job cost
in short sentences. Row tooltips, the evidence-class explanations, the
grading-zone hint and the spot-grade line were rewritten the same way.
Every phrase a test pins ("NO label on this line", "may not be this line",
"typed in by you", "A GUESS", "NO SIDE YET") is still in place.
