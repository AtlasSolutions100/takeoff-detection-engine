# Production check after the v1.8.3 deploy — headed (Edge) on https://core.workoptional.studio

Deploy run 35142866720 (tag v1.8.3) green. On the box: `~/takeoff/.tag.env` =
`TAG=v1.8.3`, `takeoff-app-1` Up (healthy) on
`ghcr.io/atlassolutions100/takeoff-detection-engine:v1.8.3`, Caddy unchanged.

Signed in as a throwaway STAFF login created with the CLI on the box
(`users add smoke-183-staff@wo.test --staff --password <random 16>`) and
removed right after (25 accounts left, none matching). Never Koby's own
password; no demo card opened. Driver: scratchpad `live_prod_183.py`;
readback `readback.json`. Errors: ['console: Failed to load resource: the server responded with a status of 404 ()', 'console: Failed to load resource: the server responded with a status of 404 ()'].

| check | live |
|---|---|
| build served | app.js ['v=184'], style.css ['v=89'] |
| /api/me | {'staff': True, 'accounts': True, 'must_change': False} |
| new code present | stFootingsBlock, stDatumBlock, stMk, stCivil, gzRenderTable, stPolyInsetPerim all defined |
| new routes | /datum and /page/0/footings answer 404 for an unknown job, as every job route must |
| Settings → Accounts | nav and section visible to staff, 26 rows while the throwaway existed (`P1-accounts-masked-raw.png`, sign-in names masked before capture) |

The projects page is not captured (as staff the throwaway sees other
contractors' document names).
