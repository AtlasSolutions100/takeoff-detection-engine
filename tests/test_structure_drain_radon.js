// PERIMETER DRAIN AND RADON - the client half (2026-09-16; Beaumont call
// 2026-09-15, 32:10-32:49). stCalc carries drain_lf (the outline) and
// radon_lf (the outline inset by the wall thickness, mitred corners); the
// structure panel sums them into two LF rows; "Radon" files under Storm
// Drainage beside Perimeter Drain.
//
// run: node tests/test_structure_drain_radon.js   (fails on f98fa27)
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
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
const state = { result: { scale: { ft_per_paper_inch: 40 } } };
const S = new Function("state", [fnSrc("stFtPerPt"), fnSrc("stPolyArea"), fnSrc("stPolyPerim"), fnSrc("stPolyInsetPerim"), fnSrc("stCalc")].join("\n") + "\nreturn { stPolyInsetPerim, stCalc };")(state);
const FPP = 40 / 72, T = (8 / 12) / FPP;
const SQ = [[0, 0], [180, 0], [180, 180], [0, 180]];
const L = [[0, 0], [180, 0], [180, 90], [90, 90], [90, 180], [0, 180]];

check(!!fnSrc("stPolyInsetPerim"), "stPolyInsetPerim exists");
check(Math.abs(S.stPolyInsetPerim(SQ, T) - (720 - 8 * T)) < 1e-6, "a rectangle inset by t loses 8 t");
check(Math.abs(S.stPolyInsetPerim(L, T) - (720 - 10 * T + 2 * T)) < 1e-6, "an L: five convex corners lose 2 t each, the concave one gains 2 t");
check(S.stPolyInsetPerim(SQ, 1e6) === 0, "floors at 0");
{
  const c = S.stCalc({ pts: SQ, depth_ft: 6, wall_in: 8 });
  check(Math.abs(c.drain_lf - 400) < 0.01 && Math.abs(c.radon_lf - (400 - 8 * 8 / 12)) < 0.01, "stCalc: drain 400 LF, radon 394.67 LF inside an 8 in wall");
  const c2 = S.stCalc({ pts: SQ, depth_ft: 6 });
  check(Math.abs(c2.radon_lf - c.radon_lf) < 1e-9, "wall thickness defaults to 8 in");
}
// wiring
check(/const ST_LABELS = \["Mass Excavation", "Structure Excavation", "Structure Backfill", "Slab Prep",\s*"Perimeter Drain", "Radon"\];/.test(js), "the two rows are structure labels");
check(/"Perimeter Drain": "LF", "Radon": "LF"/.test(js), "in LF");
check(/sum\["Perimeter Drain"\] \+= c\.drain_lf;\s*sum\["Radon"\] \+= c\.radon_lf;/.test(js), "stApplyItems sums them");
check(/stNumInput\(d, "wall_in", "1", upd\)/.test(js) && /stNumInput\(it, "wall_in", "1", upd\)/.test(js), "the draft and the row ask for the wall thickness");
check(/wall_in: calcNum\(it\.wall_in\) !== null \? it\.wall_in : 8,/.test(js) && /if \(calcNum\(sv\.wall_in\) !== null\) st\.wall_in = sv\.wall_in;/.test(js), "saved work keeps the wall thickness");
check(/layback_hv: d\.layback_hv, wall_in: d\.wall_in \}\] \}\),/.test(js) && /"drain_lf", "radon_lf"\]\.some/.test(js), "the server cross-check sends and compares the new numbers");
check(/perimeter drain \$\{stFmt\(c\.drain_lf, 0\)\} LF · radon \$\{stFmt\(c\.radon_lf, 0\)\} LF/.test(fnSrc("stResultText")), "the result line reads both");
check(/"INFILTR", "DRAIN", "RADON"\]/.test(js), "RADON files under Storm Drainage beside PERIMETER DRAIN");
check(/<b>Perimeter Drain<\/b> is the length around the outside/.test(html) && /<b>Radon<\/b> is the\s+inside face/.test(html), "the hint explains both rows");
const av = (/app\.js\?v=(\d+)/.exec(html) || [])[1];
check(+av >= 180, "cache buster moved (app.js >= 180)");

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all drain-radon checks passed");
