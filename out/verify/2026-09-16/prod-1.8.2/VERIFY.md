# Production check after the v1.8.2 deploy — headed (Edge) on https://core.workoptional.studio

Deploy run 35129381161 (tag v1.8.2) green at 17:39Z. On the box:
`~/takeoff/.tag.env` = `TAG=v1.8.2`, `takeoff-app-1` Up (healthy) on
`ghcr.io/atlassolutions100/takeoff-detection-engine:v1.8.2`, Caddy unchanged.

Signed in as a throwaway STAFF login created with the CLI on the box
(`users add smoke-182-staff@wo.test --staff --password <random 16>`) and
removed right after (`users remove`; 23 accounts left, none matching). Never
Koby's own password; no demo card opened (opening one clears its cached run).
Driver: scratchpad `live_prod_182.py`; readback `readback.json`. No page
errors, no console errors, no 5xx.

| check | live |
|---|---|
| build served | `app.js?v=182`, `style.css?v=87` |
| /api/me | staff true, accounts true, must_change false |
| new markup present | structure hint names Perimeter Drain and Radon; `#gzBaseIn` / `#gzBaseName` / `#gzTable` (no `#gzTotals`); trench hint says layback; `#ewBoundAuto`; `#scopeBar` |
| new code present | `stPolyInsetPerim`, `gzRenderTable`, `TR_UTILITY_PRESETS` defined; `BID_BUCKETS` has Gas and Electric/Comm after Water (12 buckets); `ST_LABELS` ends with Perimeter Drain, Radon |
| Settings → Accounts | nav and section visible to staff, 24 rows while the throwaway existed (`P2-accounts-masked-raw.png`, every sign-in name masked in the DOM before capture) |

The projects-page capture (P1) is withheld from the repo: as staff the
throwaway sees every account's uploads, and their document names are other
contractors' business. The readback carries no names.
