// Deleted-suggest-row resurrection guard (source-level, like
// test_spot_datum_guard.js). The earthwork restore path MERGES saved rows
// into a fresh /earthwork/suggest, so a deletion the operator made must be
// persisted (ew.removed) and re-applied after the merge - otherwise the
// suggest re-seeds junk the operator already killed (found live on Oncor
// C-04: property-line chain 0 resurrected with z 550.8 and manufactured a
// phantom 17 ft cut).
const fs = require("fs");
const js = fs.readFileSync(require("path").join(__dirname, "..", "webui", "app.js"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };

check(/removed: \[\],/.test(js), "state.ew initialises a removed list");
check(/state\.ew\.removed = state\.ew\.removed \|\| \[\]\)\.push\(row\.chain_id\)/.test(js),
  "the row × handler records the deleted chain id");
check(/state\.ew\.rows = state\.ew\.rows\.filter\(r => r !== row\);[\s\S]{0,400}?calcStateDirty\(\);/.test(js),
  "the row × handler marks the record dirty");
// the operator stamp (row.surfaceBy = "operator") sits between the two
// statements since the server split was adopted; the pin is that the flip
// is followed by a repaint AND a dirty mark, not that they share a line
check(/row\.surface = key;\s*(?:row\.surfaceBy = "operator";\s*)?renderEw\(\); calcStateDirty\(\);/.test(js),
  "the EXIST/PROP flip marks the record dirty");
check(/removed: \(state\.ew\.removed \|\| \[\]\)\.slice\(\)/.test(js),
  "calcStatePayload persists the removed list");
check(/sv\.removed\.filter\(id => !savedIds\.has\(id\)\)/.test(js),
  "restore drops resurrected rows unless the saved rows still carry them");
check(/state\.ew\.removed = state\.ew\.removed\.filter\(id => id !== cid\)/.test(js),
  "ewAppendRow forgives a prior deletion of the same chain");

if (fails) { console.error(fails + " check(s) failed"); process.exit(1); }
console.log("all ew-removed guard checks passed");
