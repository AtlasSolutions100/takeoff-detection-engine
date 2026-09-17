# What backs this number — headed (Edge) on :8100, app.js v=204

The proof block after a real **Calculate**, on the dev service running the
final code, local throwaway store. Driver: scratchpad `live_proof.py`;
readback `readback.json`. No page errors, no console errors.

Why it exists: the volume kernel is exact where the evidence is complete
(0.00% on an analytic plane, ±0.4% across grid cell sizes — measured in
`CUTFILL-ACCURACY-SPEC.md` §0), so a wrong cut/fill is a wrong INPUT, and
an estimator cannot see inputs. A client on a live job said the quiet part
out loud: *"we get no defensible CY. I won't invent volumes."*

## Oncor C-04 (engine page index 3), no limit of work set — `P1-proof.png`

> **What backs this number** — 1 of 4 checks pass, check these before you bid it
> - ! the existing surface sits **0.68 ft** from this sheet's own 7 printed ground spots (median, **cross-validated**), bias +0.61 ft, worst 1.37 ft
> - ! read with the other surface model (clamped_ct) the same rows give 1,510 cut and 10,919 fill — **27.2%** from this answer
> - ✓ on a 1 ft grid instead of 2 ft the same surfaces give 2,074 cut — 0.3% from this answer
> - ! no limit of work is set, so the region is wherever the two surfaces overlap — that is not the graded area
>
> worst point: the sheet prints **585.7** (ME) where our existing surface
> reads **584.33** — we are low by **1.37 ft**

Volume on this run: cut 2,074 / fill 9,611 CY over 100,540 SF, linear TIN.

## Barn C3.0 (engine page index 2) — `P2-proof.png`

Verdict warn, and the honest half is the first line: **"this sheet prints
no ground spot elevation to check the surface against — the volume rests
on the contour lines alone"**. A check that cannot run is reported as
`cannot check` and is never counted as a pass. Model spread 11.5%, grid
0.1% (pass), region unbounded on this run.

## The defect this verification caught, fixed before commit

The first live run read the spot residual as **0.00 ft** — because the
panel feeds the sheet's spot grades into the surface as points, so the
check was asking the surface to repeat itself. The same sheet read 0.91 ft
offline, where the spots were not inputs. A check that cannot fail is
worthless, so:

- a spot the surface was **not** built from is compared directly
  (`independent`);
- a spot it **was** built from is **held out** — the surface is rebuilt
  without a fifth of them and asked about that fifth, five times over
  (`cross-validated`, standard k-fold);
- the result says which ran, and every named point carries its own `how`.

Live C-04 then reads 0.68 ft cross-validated (bias +0.61) instead of 0.00.
Pinned by `tests/test_volume_proof.py` §6 ("a check that cannot fail is
worthless") and `tests/test_volume_proof_ui.js`; both fail on 054c5b9.
