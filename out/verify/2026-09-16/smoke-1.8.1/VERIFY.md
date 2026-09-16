# 1.8.1 pre-push smoke — headed (Edge) on :8100, dev at 3377d00

Same driver as the 1.8.0 smoke (scratchpad `live_smoke_181.py`, twenty
client-flow steps with every page/console/5xx error captured per step;
readback `smoke.json`, one screenshot per step). Compared step by step
against `out/verify/2026-09-10/smoke-1.8/smoke.json`: nineteen steps
byte-identical, one differs only in the elevation read's wall time ("read
done in 2s" vs "1s"). Errors: the same single console 404 as 1.8.0 (a HEAD
on the CSV endpoint). Oncor C-04 still detects 589 lines in 65 styles at
1" = 30' (graphic bar); the earthwork read still yields 147 contours with
31 needing a side and the same cut 1,947 / fill 8,306 CY over 75,800 SF.

The four features new in this release were each verified headed on their
own commits: `out/verify/2026-09-10/join-families-delete`, `extend-end`,
`trim-matchline`, and `out/verify/2026-09-15/accounts`.
