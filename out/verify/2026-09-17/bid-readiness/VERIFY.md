# The required-input contract — 2026-09-17

Headed Edge on `http://127.0.0.1:8100`, dev, `app.js?v=206`. Driver:
`scratchpad/live_readiness.py`. Readback: `readback.json`. Zero page
errors, zero console errors.

## What this is

Koby: *"even if it means users need to go and manually enter information,
we just need to make sure once all required information is inputted the
numbers are accurate."* That is only a promise the software can keep if it
**says what it still needs**. `earthwork.bid_readiness` answers it from the
same body the volume was computed from, so the contract and the number can
never describe different inputs.

Three states, and the distinction is the whole point:

| state | meaning |
|---|---|
| supplied | the estimator gave it, or confirmed the sheet's own value |
| **assumed** | the software chose a default — reported, never counted as supplied |
| missing | nothing has been said, and the number answers a different question |

READY requires every required item **supplied**. Nothing blocks the
Calculate button — an estimator checking a hunch does not want a wall.

## Why each item is on the list — measured today, on this tree

`scratchpad/probe_complete_input.py`, `probe_accept_all.py`:

| input | measured cost of leaving it open |
|---|---|
| elevations | C-04 re-run on random halves of its own rows: fill −24%. **Barn C3.0 swings 89–144% across draws at one row count.** Coverage by length: Barn 41%, C-04 67%, C2.1 71% |
| limit of work | Barn runs over 168,776 SF against the client's 224,058 — 23% short before any surface question |
| topsoil strip | 6 in off C-04: cut **−30%**, fill +13%; 12 in: cut −52% |
| dig depth | 12 in of pavement section on C-04: cut **+88%** |
| shrink / swell | in-place yards answer a different question than hauled and compacted ones |

## Live on Oncor C-04 — `R1-contract.png`

> Ready to bid? — 4 required inputs missing and 2 still assumed
> ✓ Scale **1 in = 30 ft** · graphic-bar
> ! Limit of work **100,540 SF** — with no limits line the region is wherever the two surfaces overlap
> ✓ Both surfaces **83 existing / 25 proposed lines** — 42 and 22 distinct elevations
> ! Elevations **170 of 339 lines (67% by length)** — 169 contour lines carry no height…
> ! Topsoil strip — nothing is stripped, so the cut starts at the ground the sheet draws…
> ! Dig depth below finish grade — …12 in moved a measured sheet's cut by 88%
> ~ Shrink and swell **in-place yards** [in-place yards are right]
> ~ Redistribute and reuse **site cut reused 100%, removals exported** [this balance is right]
> – over-excavation and subsidence · – rock and groundwater — not an input this software takes yet

## Confirming a default — `R2-confirmed.png`

One click on "in-place yards are right":

- the POST carries `confirmed: ["swell"]` (captured from the live request),
- the item turns **supplied**, its reason reading "confirmed by the
  estimator: no factor on this job",
- the verdict drops from "…and 2 still assumed" to "…and 1 still assumed",
- and it **recalculates**, so the volume, the contract and the ledger all
  record the same inputs.

Only the four defaults an estimator can legitimately adopt get a button
(strip, dig depth, swell, reuse). Nothing that has to be *typed* — a strip
depth off a soils report, a limit of work — gets one, and the two
not-built items are named and greyed rather than quietly omitted.

## Where the contract points

With the factors and the limit of work supplied, C-04 reads **one required
input missing: elevations** — 169 of 339 contour lines carry no height
(`scratchpad/probe_readiness_live.py`). That is the next build, and it is
the one the measurements say dominates everything else.

## Tests

- `tests/test_bid_readiness.py` — 4 parts, 21 checks.
- `tests/test_bid_readiness_ui.js` — 19 checks.

Both fail on `39903dc` (the function and the panel block do not exist).
