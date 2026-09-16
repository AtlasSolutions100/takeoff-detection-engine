// WALLS BY BOTTOM OF FOOTING IN THE STRUCTURE PANEL (2026-09-16; Wave 2
// item 3). The footings block gains a "Walls by bottom of footing" table
// (swatch, BOF both ways, callouts, schedule width, footing-line feet AS
// DRAWN, the draft outline's split); the canvas wears the group colours
// while the panel is open, a hovered row lights its lines, and the draft
// outline's edges are painted per group from the split route.
//
// run: node tests/test_bof_ui.js   (fails on f22916c)
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "..", "webui", "style.css"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
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
for (const n of ["stGroupMap", "stGroupColorMap", "stGroupsTable", "stFetchSplit", "stEnsureSplit", "stSplitOf", "stPaintSplit"])
  check(!!fnSrc(n), `${n} exists`);
check(/box\.appendChild\(stGroupsTable\(st, d\)\);/.test(fnSrc("stFootingsBlock")), "the footings block ends with the groups table");
check(/d\.groups_reason \|\| "no callout on this sheet carries an elevation"/.test(fnSrc("stGroupsTable")), "no groups: the route's reason is shown, else a plain sentence");
check(/g\.outline_note/.test(fnSrc("stGroupsTable")) && /Hover a row to light its lines/.test(fnSrc("stGroupsTable")), "the as-drawn caveat and the hover hint are printed with the table");
check(/stCivil\(r\.bof_ft, dat\)/.test(fnSrc("stGroupsTable")), "BOF is shown in civil feet too once a datum is known");
check(/r\.width_from === "schedule" \? "" : " \?"/.test(fnSrc("stGroupsTable")), "a width that came from the mark's digits carries a question mark");
check(/state\.stHotChains = new Set\(r\.chain_ids \|\| \[\]\); draw\(\);/.test(fnSrc("stGroupsTable")) && /state\.stHotChains = null; draw\(\);/.test(fnSrc("stGroupsTable")), "hovering a row lights the group's lines and leaving clears it");
check(/cb\.id = "stPaintGroups"/.test(fnSrc("stGroupsTable")) && /st\.paintGroups = cb\.checked; draw\(\);/.test(fnSrc("stGroupsTable")), "the colour-the-walls toggle repaints");
check(/if \(st\.draft\) stEnsureSplit\(st\);/.test(fnSrc("stGroupsTable")), "a draft outline asks for its split");
check(/footings\/split`/.test(fnSrc("stFetchSplit")) && /method: "POST"/.test(fnSrc("stFetchSplit")) && /points: st\.draft\.pts/.test(fnSrc("stFetchSplit")), "the split is fetched by POSTing the draft's points");
check(/st\.split\.key !== key\) return;/.test(fnSrc("stFetchSplit")), "a split that arrives for an outline no longer drawn is dropped");
check(/JSON\.stringify\(st\.draft\.pts\)/.test(fnSrc("stSplitKey")), "the split is keyed by the draft's points, so a moved vertex refetches");
check(/\$\("stPanel"\)\.hidden\) return null;/.test(fnSrc("stGroupColorMap")) && /st\.paintGroups === false/.test(fnSrc("stGroupColorMap")), "group colours are off when the panel is closed or the toggle is off");
check(/st\.groupColor = stGroupMap\(j\);/.test(fnSrc("stFetchFootings")) && /renderSt\(\); draw\(\);/.test(fnSrc("stFetchFootings")), "the colour map is built when the footings arrive, and the canvas repaints");
const draw = fnSrc("draw");
check(/const gcol = stGroupColorMap\(\);/.test(draw) && /\(gc \|\| chainCssColor\(ch, st2\)\)/.test(draw), "draw() paints a grouped chain in its group's colour, everything else as before");
check(/gc \? wPt \* 3\.0 : wPt/.test(draw), "a grouped chain is drawn heavier");
check(/!!\(state\.stHotChains && state\.stHotChains\.has\(ch\.chain_id\)\)/.test(draw), "a hovered group counts as hot in the chain loop (and a state without the field is fine)");
check(/stHotChains: null,/.test(js), "state carries the hovered group");
check(/stPaintSplit\(ctx, k, f, st\);/.test(fnSrc("drawStructural")), "drawStructural paints the draft's edges per group");
check(/e\.bof_ft == null\) continue;/.test(fnSrc("stPaintSplit")) && /\* e\.t0\) \* f/.test(fnSrc("stPaintSplit")), "an edge beside no callout stays uncoloured; coloured edges follow the split's t0..t1");
check(/\.st-groups table \{/.test(css) && /\.st-sw \{/.test(css) && /\.st-groups th\.num, \.st-groups td\.num \{ text-align: right;/.test(css), "table, swatch and numeric-column styles exist");
check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 185 && +((/style\.css\?v=(\d+)/.exec(html) || [])[1]) >= 90, "cache busters moved");
if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all bof-ui checks passed");
