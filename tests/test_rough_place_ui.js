// ROUGH PLACE TOPSOIL BY AREA x DEPTH IN THE EARTHWORK PANEL (2026-09-16;
// Wave 2 item 6). A depth and a footprints-and-paving SF under the layer
// stack (with a "use n SF" suggestion from the sheet's structures and
// paving zones), carried in the run body and the saved state; "Import
// Topsoil" joins the earthwork item regex in both of its copies.
//
// run: node tests/test_rough_place_ui.js   (fails on the commit before it)
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
for (const id of ["ewPlace", "ewPlaceDepth", "ewPlaceLess", "ewPlaceUse", "ewPlaceNote"]) check(html.includes(`id="${id}"`), `#${id} exists`);
check(html.indexOf('id="ewExLayers"') < html.indexOf('id="ewPlace"') && html.indexOf('id="ewPlace"') < html.indexOf('id="ewPrLayers"'), "the line sits under the existing layers, above the proposed stack");
check(/Beaumont's rule \(bid sheet line 12\)/.test(html), "the tooltip names the rule and where it comes from");
for (const n of ["ewPlaceSync", "ewPlaceLessSuggest"]) check(!!fnSrc(n), `${n} exists`);
check(/  ewPlaceSync\(\);\n\}/.test(fnSrc("renderEwLayers")), "renderEwLayers syncs the line last");
const ps = fnSrc("ewPlaceSync");
check(/state\.ew\.placeDepthIn = isFinite\(v\) && v > 0 \? v : null; calcStateDirty\(\);/.test(ps) && /state\.ew\.placeLessSf = isFinite\(v\) && v >= 0 \? v : null; calcStateDirty\(\);/.test(ps), "typing stores the values and marks the saved state dirty");
check(/use\.hidden = !\(tot > 0\);/.test(ps) && /use\.onclick = \(\) => \{ state\.ew\.placeLessSf = tot;/.test(ps), "the suggestion button appears only with something to suggest and fills the SF on click");
check(/blank: the topsoil layer's respread % rule as before/.test(ps), "blank is said to mean the old rule");
const sg = fnSrc("ewPlaceLessSuggest");
check(/stCalc\(it\)\.area_sf/.test(sg) && /DRIVE\|PAV\|PATIO\|WALK\|PARK\|SLAB\|GRAVEL/.test(sg) && /z\.result/.test(sg), "the suggestion sums the structure outlines and the zones named like paving");
check(/o\.place_depth_in = state\.ew\.placeDepthIn;/.test(fnSrc("ewBody")) && /o\.place_less_sf = isFinite\(state\.ew\.placeLessSf\) && state\.ew\.placeLessSf > 0 \? state\.ew\.placeLessSf : 0;/.test(fnSrc("ewBody")), "the run body carries the depth and the SF only when a depth is set");
check(/placeDepthIn: calcNum\(state\.ew\.placeDepthIn\),\n      placeLessSf: calcNum\(state\.ew\.placeLessSf\),/.test(js), "the saved state carries both");
check(/if \(calcNum\(sv\.placeDepthIn\) !== null && sv\.placeDepthIn > 0\) state\.ew\.placeDepthIn = sv\.placeDepthIn;/.test(js) && /if \(calcNum\(sv\.placeLessSf\) !== null && sv\.placeLessSf >= 0\) state\.ew\.placeLessSf = sv\.placeLessSf;/.test(js), "and restores both");
check(/placeDepthIn: null,/.test(js) && /placeLessSf: null,/.test(js), "the earthwork state starts blank");
const copies = js.match(/Import Structural\$\|Import Topsoil\$\|\(Cut\|Fill\) \\\(earthwork\\\)\)\//g) || [];
check(copies.length === 2, `both copies of the earthwork item regex know "Import Topsoil" (${copies.length})`);
check(/\.ew-place \{/.test(css), "style exists");
check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 190 && +((/style\.css\?v=(\d+)/.exec(html) || [])[1]) >= 95, "cache busters moved");
if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all rough-place-ui checks passed");
