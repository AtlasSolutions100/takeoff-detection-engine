// STRUCTURE DIG AND BACKFILL, BEAUMONT'S WAY - the client half (2026-09-16).
// stCalc mirrors server._structure_one: dig = LF x Dt x (W + 0.5 hv Dt) / 27,
// backfill to the PROPOSED grade, blank fill depth assumed = dig and said so.
//
// run: node tests/test_structure_formula.js   (fails before the formula change)
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
const FPP = 40 / 72;
const state = { result: { scale: { ft_per_paper_inch: 40 } } };
const api = new Function("state", [fnSrc("stFtPerPt"), fnSrc("stPolyArea"), fnSrc("stPolyPerim"), fnSrc("stPolyInsetPerim"), fnSrc("stCalc")].join("\n") + "\nreturn stCalc;")(state);
const SQ = [[0, 0], [180, 0], [180, 180], [0, 180]];
const L = 400;

{
  const c = api({ pts: SQ, depth_ft: 6, footing_ft: 0, footing_w_ft: 2, layback_hv: 1, fill_depth_ft: NaN });
  check(Math.abs(c.perim_lf - L) < 0.01, "400 LF of wall");
  check(Math.abs(c.struct_cy - L * 6 * 5 / 27) < 0.1, "dig = 400 x 6 x (2 + 3) / 27 (Beaumont's 6 by 6 by 3 plus the footing)");
  check(c.dig_w_ft === 5 && c.backfill_assumed === true && c.backfill_cy === c.struct_cy, "blank fill depth: backfill = dig, flagged assumed; trench 5 ft wide outside the wall");
}
{
  const c = api({ pts: SQ, depth_ft: 6, footing_ft: 0, footing_w_ft: 2, layback_hv: 1, fill_depth_ft: 4 });
  check(Math.abs(c.backfill_cy - L * 4 * 4 / 27) < 0.1 && c.backfill_assumed === false, "fill depth 4: backfill figured to the proposed grade, not the dig");
}
{
  const c = api({ pts: SQ, depth_ft: 6, footing_ft: 1, footing_w_ft: 7.5, layback_hv: 0, fill_depth_ft: NaN });
  check(Math.abs(c.struct_cy - L * 7 * 7.5 / 27) < 0.1, "footing 1 ft below the slab, CF7.5 footing, no layback: 400 x 7 x 7.5 / 27");
}
{
  const c = api({ pts: SQ, depth_ft: 8, layback_ft: 4 });
  check(Math.abs(c.struct_cy - L * 8 * (2 + 4) / 27) < 0.1, "the old layback_ft is ignored; defaults 2 ft footing and 1:1 apply");
}

// ---- wiring --------------------------------------------------------------------
check(/fill_depth_ft: NaN, footing_ft: 0,\s*footing_w_ft: 2, layback_hv: 1(, wall_in: 8)?(, datum_ft: NaN)?(,[\s\S]{0,700}?)? \};/.test(js), "the panel's standing defaults carry the new fields");
check(/stNumInput\(d, "fill_depth_ft", "0.5", upd\)/.test(js) && /stNumInput\(d, "footing_w_ft", "0.5", upd\)/.test(js) && /stNumInput\(d, "layback_hv", "0.25", upd\)/.test(js), "the draft asks for fill depth, footing width and layback h:v");
check(!/stNumInput\([a-z]+, "layback_ft"/.test(js), "no input asks for a constant layback width any more");
check(/fill_depth_ft: isFinite\(\+d\.fill_depth_ft\)/.test(js) && /layback_hv: d\.layback_hv(, wall_in: d\.wall_in)?(,\s*datum_ft: [^}]*)? \}\] \}\),/.test(js), "the server cross-check sends the new fields");
check(/fill_depth_ft: calcNum\(it\.fill_depth_ft\) !== null \? it\.fill_depth_ft : NaN,/.test(js) && /layback_hv: calcNum\(it\.layback_hv\) !== null \? it\.layback_hv : 1,/.test(js), "saved work reloads the new fields with the new defaults");
check(/\(fill depth assumed = dig\)/.test(fnSrc("stResultText")), "the result line says when the fill depth was assumed");
check(/const digW = stCalc\(it\)\.dig_w_ft \|\| 0;/.test(js), "the canvas band is the trench's top width, not a constant");
check(/backfill \$\{stFmt\(stR1\(tot\.back\)\)\} CY/.test(js), "the multi-structure total sums backfill on its own");
check(/half the depth at 1:1/.test(html) && /6 &times; \(2 \+ 3\) = 30 SF/.test(html), "the hint states the rule with Beaumont's worked example");
const av = (/app\.js\?v=(\d+)/.exec(html) || [])[1];
check(+av >= 178, "cache buster moved (app.js >= 178)");

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all structure-formula checks passed");
