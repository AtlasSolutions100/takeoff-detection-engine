// COUNT EACH WALL ONCE, the panel (2026-09-17; Wave 2 item 11).
//
// The server pairs a wall's two faces and a footing's two edges
// (GET .../twins); the panel only applies each line's one-run share. This
// pins that the share is applied where the takeoff is built and nowhere it
// would lie: the style totals and the takeoff rows bill one run, the
// work-area loss stays about the mask, the export sends the families, the
// choice is per page and saved with the sheet, the switch shares the LF/EA
// cell, and the canvas draws what was left out.
//
// run: node tests/test_count_once_ui.js   (fails on the commit before it)
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "..", "webui", "style.css"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(name) {
  let start = js.indexOf("\nfunction " + name + "(");
  if (start < 0) start = js.indexOf("\nasync function " + name + "(");
  if (start < 0) return "";
  let depth = 0;
  for (let j = js.indexOf("{", start); j < js.length; j++) {
    if (js[j] === "{") depth++;
    else if (js[j] === "}" && --depth === 0) return js.slice(start + 1, j + 1);
  }
  return "";
}

// ---- the share and the billed length, run for real ------------------------------------------------
const helpers = ["onceFrac", "chainBilledPt", "onceFamily", "polySlice"].map(fnSrc);
check(helpers.every(Boolean), "onceFrac, chainBilledPt, onceFamily and polySlice exist");
const state = {
  once: new Set([5]),
  twins: { families: { "5": { kind: "wall", drawn_ft: 274, counted_ft: 140,
    chains: { "1": { d: 1400, c: 1400, skip: [] }, "2": { d: 1340, c: 0, skip: [[0, 1340]] },
              "3": { d: 100, c: 25, skip: [[25, 100]] } } } } },
};
const counted = { 1: 1400, 2: 1340, 3: 100, 9: 50 };
const H = new Function("state", "chainCountedPt", helpers.join("\n")
  + "\nreturn { onceFrac, chainBilledPt, onceFamily, polySlice };")(state, (ch) => counted[ch.chain_id]);
check(H.onceFrac({ chain_id: 1, style_id: 5 }) === 1 && H.onceFrac({ chain_id: 2, style_id: 5 }) === 0
      && H.onceFrac({ chain_id: 3, style_id: 5 }) === 0.25, "each line's share is its counted over drawn");
check(H.chainBilledPt({ chain_id: 2, style_id: 5 }) === 0 && H.chainBilledPt({ chain_id: 3, style_id: 5 }) === 25,
      "a line bills its counted length times its share");
check(H.onceFrac({ chain_id: 9, style_id: 5 }) === 1, "a line the pairing never saw (drawn after) counts in full");
check(H.onceFrac({ chain_id: 1, style_id: 6 }) === 1 && H.onceFamily(6) === null,
      "a family that is not a twin family is untouched");
state.once.delete(5);
check(H.chainBilledPt({ chain_id: 2, style_id: 5 }) === 1340, "switched off, every line counts as drawn");
state.once.add(5);
const sl = H.polySlice([[0, 0], [10, 0], [10, 10]], 5, 15);
check(sl.length === 3 && sl[0][0] === 5 && sl[1][0] === 10 && sl[2][1] === 5,
      `polySlice cuts a polyline between two arc lengths (${JSON.stringify(sl)})`);

// ---- where the share is applied ---------------------------------------------------------------------
const live = fnSrc("_liveStyleTotals");
check(/state\.wzRev \+ ":" \+ state\.onceRev/.test(live), "the style totals re-sum when the choice changes");
check(/\+ \(once === 1 \? counted : counted \* once\)\);/.test(live) && /_liveDrawn\.set\(ch\.style_id, \(_liveDrawn\.get\(ch\.style_id\) \|\| 0\) \+ counted\);/.test(live),
      "the style total bills one run and keeps the drawn footage beside it");
check(/if \(full - counted > 0\)/.test(live), "the work-area loss is still full minus counted - never the faces left out");
check(/t\.count \+= 1; t\.pt \+= chainBilledPt\(ch\); t\.chains\.push\(ch\);/.test(fnSrc("computeTakeoffRows")),
      "the takeoff rows (prices, CSV, report) bill one run");
check(/count_once_style_ids: \[\.\.\.\(state\.once \|\| \[\]\)\]\.filter\(sid => onceFamily\(sid\)\),/.test(js),
      "the export sends the families counted once, and only real twin families");
check(!!fnSrc("styleDrawnPt"), "styleDrawnPt exists for the row's drawn footage");

// ---- the row ---------------------------------------------------------------------------------------------
const rs = fnSrc("renderStyles");
check(/once \(\$\{fmtQty\(styleDrawnPt\(s\.style_id\)\)\} drawn\)/.test(rs), "the meta line says once and as drawn");
check(/const onceBtn = onceButton\(s\);/.test(rs) && /mmCell\.append\(mm, onceBtn\);/.test(rs)
      && /row\.append\(marks, sw, main, mmCell, badge\);/.test(rs),
      "the switch shares the LF/EA cell - the row's grid keeps its columns");
const ob = fnSrc("onceButton");
check(/if \(!fam\) return null;/.test(ob), "no switch on a family that does not pair");
check(/b\.textContent = "once";/.test(ob) && /aria-pressed/.test(ob) && /e\.stopPropagation\(\); onceToggle\(s\.style_id\);/.test(ob),
      "the switch is a pressed/unpressed 'once' button that does not select the row");
check(/As drawn: \$\{ft\(fam\.drawn_ft\)\}\. One run per wall: \$\{ft\(fam\.counted_ft\)\}\./.test(ob),
      "its tooltip says both numbers in plain words");
check(/\.mm-cell \{/.test(css) && /\.once-toggle\.on \{/.test(css), "styles exist");

// ---- per page, saved with the sheet, loaded with it ---------------------------------------------------
const pr = fnSrc("pageReset");
check(/state\.twins = null; state\.once = new Set\(\); state\.onceRev\+\+;/.test(pr),
      "pageReset clears the twins and the choice - a style id means nothing on another sheet");
check(/out\.once = \[\.\.\.state\.once\]\.sort\(\(a, b\) => a - b\);/.test(js), "the choice is saved with the sheet");
const csl = fnSrc("calcStateLoad");
// 2026-09-26 (audit D01): the pairing is no longer asked for on page load -
// that call started O(n^2) work on every civil sheet of a set with a misread
// schedule. A saved choice asks for it itself (onceApplySaved), the Structure
// panel and the walls-once button do the rest (tests/test_twins_on_demand.js).
check(!/twinsLoad\(/.test(csl) && /twinsLoad\(state\.gen\)/.test(fnSrc("onceApplySaved")),
      "the twins are not loaded with the page; a saved choice loads them when it is applied");
check(/wzApplySaved\(\);\n  onceApplySaved\(\);/.test(csl), "the saved choice is applied before the snapshot, like the zones");
check(/if \(gen !== state\.gen\) return;/.test(fnSrc("twinsLoad")), "a late twins answer for another page is dropped");
check(/if \(!onceFamily\(sid\)\) return;/.test(fnSrc("onceToggle")) && /calcStateDirty\(\);/.test(fnSrc("onceToggle")),
      "toggling is refused for a non-twin family and marks the sheet dirty");

// ---- the canvas and the footing groups ------------------------------------------------------------------
const dr = fnSrc("draw");
check(/COUNTED ONCE: the stretches a count-once family leaves out/.test(dr)
      && /const seg = polySlice\(ch\.points, a, b\);/.test(dr) && /ctx\.strokeStyle = CV_ONCE_SKIP;/.test(dr),
      "the canvas knocks back what was left out and redraws it thin and dashed");
check(/if \(!state\.include\.get\(ch\.style_id\)\) continue;/.test(dr.slice(dr.indexOf("COUNTED ONCE"))),
      "an unticked family draws no count-once overlay");
const gt = fnSrc("stGroupsTable");
check(/const lfHead = g\.once \? "one run" : "footing line";/.test(gt), "the footing groups say 'one run' when they count once");
check(/as drawn, both edges: \$\{Math\.round\(r\.lf_drawn_ft\)\} ft/.test(gt), "and keep the drawn footage in the tooltip");
check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 197 && +((/style\.css\?v=(\d+)/.exec(html) || [])[1]) >= 99,
      "cache busters moved");

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all count-once ui checks passed");
