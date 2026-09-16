// THE DECLARED SCOPE FILES WHAT NOTHING ELSE NAMES (2026-09-16; Jeff Ellis,
// ECI Foundations, demo 2026-08-24 / plan J15). tradeFor's ladder ends in
// "General"; on a foundation sheet with no CAD layers and no legend that put
// every wall row of Jeff's own work under General in his export. Now an
// unnamed row falls to the ONE trade the operator declared for the document;
// two declared trades, or none, still read General.
//
// run: node tests/test_scope_trade_fallback.js   (fails on d0dc055)
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(name) {
  const start = js.indexOf("function " + name + "(");
  if (start < 0) return "";
  let depth = 0;
  for (let j = js.indexOf("{", start); j < js.length; j++) {
    if (js[j] === "{") depth++;
    else if (js[j] === "}" && --depth === 0) return js.slice(start, j + 1);
  }
  return "";
}
check(/let declared = "";\s*try \{\s*const t = \(scopeRec\(\)\.trades \|\| \[\]\)\.filter\(x => TRADES\.includes\(x\)\);/.test(fnSrc("tradeFor")), "the fallback reads the declaration inline, guarded");
const tradesLine = (/const TRADES = \[[^\]]*\];/s.exec(js) || [""])[0];
check(!!tradesLine, "TRADES found");
let declared = [];
const H = new Function("declared",
  tradesLine + "\nconst store = { getItem: () => null };\nconst tradeKey = l => 'trade::' + l;\n"
  + "const tradeAnnoLayers = () => false;\nconst scopeRec = () => ({ trades: declared() });\n"
  + fnSrc("tradeFor") + "\nreturn tradeFor;")(() => declared);

declared = [];
check(H("Unnamed d12", []) === "General", "no scope declared: an unnamed row is General (as before)");
declared = ["Footing & Foundation"];
check(H("Unnamed d12", []) === "Footing & Foundation", "one trade declared: the unnamed row is that trade");
check(H("solid 0.5", []) === "Footing & Foundation", "a pattern-description name too");
check(H("PROPOSED 8\" WATER MAIN", []) === "Wet Utilities", "a row that names another trade keeps it - the ladder decides first");
check(H("CONCRETE FOOTING", []) === "Footing & Foundation", "a named foundation row is unchanged");
check(H("SPAN DECK PANEL", []) === "Footing & Foundation", "Ellis S1.02's own callout text with no trade word files under his trade");
declared = ["Footing & Foundation", "Structures"];
check(H("Unnamed d12", []) === "General", "two trades declared: nobody said which, so General");
declared = ["Not a trade"];
check(H("Unnamed d12", []) === "General", "a declared name outside TRADES (the openings scopes) does not file rows");
check(/return declared \|\| "General";\s*\}$/.test(fnSrc("tradeFor")), "the fallback is the last rung of the ladder");
// a harness with NO scopeRec at all (the older tests lift tradeFor alone)
const bare = new Function(tradesLine + "\nconst store = { getItem: () => null };\nconst tradeKey = l => 'trade::' + l;\nconst tradeAnnoLayers = () => false;\n" + fnSrc("tradeFor") + "\nreturn tradeFor;")();
check(bare("Unnamed d12", []) === "General", "with no scopeRec in reach the row is General, nothing throws");

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all scope-trade-fallback checks passed");
