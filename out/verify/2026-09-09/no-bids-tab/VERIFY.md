# the Open bids tab is gone from core; the CRM hand-off still lands — headed (Edge) on :8100

Koby's call (2026-09-09): core does not list open bids. It only has to ACCEPT
"send to CORE for takeoff" from the CRM, which is a separate, separately
charged product. Driver: scratchpad `live_no_bids_tab.py`; readback in
`no_bids_tab.json`. Bid Sniper was started on :8200 for the check.

| what | readback |
|---|---|
| tabs on Projects | `Demo plans 7`, `Your projects 17` — two; `#pvTabBids` not in the DOM |
| `/#bid=bonfire:utah:251864` in a fresh tab | the chooser opened: "Which files are the plans? — Reggio Circle Underdrain RFB", 4 files, 1 ticked (the 6.2 MB drawing set) |
| Open selected | job `dd3572301d75` opened, `3-Reggio_Circle_Underdrain_Bid_Issue.pdf`, status "opened … from the bid — detection queued on the pages that look like site work"; Projects closed |

Content-addressed: the pull landed on the job this listing already had, so a
second hand-off does not duplicate the project. No page or console errors.

Captures: `N1-projects-two-tabs-raw.png`, `N2-handoff-chooser-raw.png`,
`N2-handoff-raw.png`.

Server routes are untouched (`GET /api/bids`, `GET /api/bids/{id}/plans`,
`POST /api/bids/{id}/pull`): the hand-off uses the last two, and the CRM may
use the first. `tests/test_bids.py` (49 checks) still passes.
