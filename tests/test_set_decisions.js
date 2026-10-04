// THE STYLE FINGERPRINT, AND NO TICK RECORD OF 2.0'S IS READ (the repair after CRITIC-empty-1, 2026-10-03).
//
// This file pinned the set-level include / exclude decisions (EXCAVATION-PLAN section 7 item 5): a hand tick on one
// sheet recorded against the whole document. The takeoff now starts empty and what is in it is the server's record,
// page by page; nothing is brought over from an earlier version. So the set-level record, the per-page record
// (includeKey), the resolver that ranked the two, and the frozen copy of 2.0's default picks are GONE from webui/app.js
// - they were the code that read 2.0's browser records, and every way of rebuilding a 2.0 takeoff from them put lines
// in a bid the estimator had left out. Two things are pinned here:
//
//   * styleFp - still the key the label record travels on, and the identity the server's tick record is keyed by
//     (server.py _style_key is the same key): the EXACT signature and the FULL layer. The measured alternative - layer
//     leaf + class + cadence rounded to 1 pt - fuses rows the engine split ON THE SAME PAGE for 714/2,664 rows of one
//     set (26.8%) and 262/1,059 of another (24.7%); styleFp fused 0/2,664 and 0/1,059;
//   * none of the readers of 2.0's tick records exists any more.
//
// webui/app.js is not a module and touches the DOM at load, so this evals ONLY the block between the SET-DECISIONS
// markers - that block is kept pure (no DOM, no state.*, no store.*) for exactly this reason.
//
// Dependency-free like tests/test_gate.py:  node tests/test_set_decisions.js   (fails on 888b2c1: the readers exist)

"use strict";
const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const a = src.indexOf("// SET-DECISIONS-BEGIN");
const b = src.indexOf("// SET-DECISIONS-END");
if (a < 0 || b < 0 || b <= a) {
  console.error("FAIL: SET-DECISIONS markers not found in webui/app.js");
  process.exit(1);
}
const block = src.slice(a, b);
const T = new Function(block + `
  return { weightClass, rawColor, styleFp };`)();

let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); }
  else console.log("ok:", msg);
}

// A real title-block style, fields verbatim from an offline detect run on this tree (style 265 of engine page index 2
// = printed sheet C3.0 of a civil set; 7,576.7 pt of title-block linework).
const ttlb = { style_id: 265, class: "annotation", width: 0.8,
               color: [0, 0, 0], signature: "sheet furniture",
               layer: "G-ANNO-TTLB" };

// ---- the fingerprint ---------------------------------------------------
{
  const k = T.styleFp(ttlb);
  check(k === "annotation|w2|0,0,0|sheet furniture|L=G-ANNO-TTLB", "styleFp = class | weight class | colour | signature | layer, verbatim");
  // the same style met on ANOTHER page of the set derives the same key - style_id and page are not in it
  check(T.styleFp(Object.assign({}, ttlb, { style_id: 7 })) === k, "same family on another page, same key");
}

// ---- the engine's distinctions survive in the key ---------------------
{
  // weight-class edge: 0.3 vs 0.4 pt straddle the 0.35 hairline limit - the engine holds these as two families
  const w1 = Object.assign({}, ttlb, { width: 0.3 });
  const w2 = Object.assign({}, ttlb, { width: 0.4 });
  check(T.styleFp(w1) !== T.styleFp(w2), "weight-class edge keeps two families two keys");
  // near-twin cadences of ONE page, fields verbatim from an offline detect run: 'd8.8 g4.5' vs 'd9.1 g4.5', the same
  // utility-easement linetype reaching the sheet directly and through the XREF. Rounding cadences to 1 pt (plus a
  // leaf layer) would fuse them; the exact signature and the full layer keep them apart.
  const c1 = { style_id: 8, class: "dash1", width: 0.3, color: [0, 0, 0],
               signature: "d8.8 g4.5", layer: "CAD Base|C-U2-ESMT-UTIL" };
  const c2 = { style_id: 149, class: "dash1", width: 0.3, color: [0, 0, 0],
               signature: "d9.1 g4.5", layer: "C-U2-ESMT-UTIL" };
  check(T.styleFp(c1) !== T.styleFp(c2), "near-twin cadences (0.3 pt apart, same page) stay two keys");
  // XREF host prefix stays in the key: BARN EAST|S-FNDN and PRIM BDRM|S-FNDN are two keys
  const x1 = Object.assign({}, ttlb, { layer: "BARN EAST|S-FNDN" });
  const x2 = Object.assign({}, ttlb, { layer: "PRIM BDRM|S-FNDN" });
  check(T.styleFp(x1) !== T.styleFp(x2), "XREF host prefix keeps two hosts two keys");
}

// ---- nothing reads 2.0's tick records any more -------------------------
{
  const code = src.replace(/\/\/[^\n]*/g, "");          // comments may still tell the history
  for (const name of ["setDecEligible", "setDecKey", "setDecResolve", "setIncludeSetWide", "includeKey",
                      "includeSeed20", "tk20Set", "tk20HasRecords"])
    check(!new RegExp("\\b" + name + "\\b").test(code), `${name} is gone from webui/app.js (it read or wrote 2.0's tick records)`);
  check(!/::setinc::/.test(code) && !/::inc::/.test(code),
    "no key of 2.0's tick records (::inc::, ::setinc::) is built anywhere: none is read or written");
}

if (failures) {
  console.error(failures + " failure(s)");
  process.exit(1);
}
console.log("all style-fingerprint and no-2.0-tick-record tests passed");
