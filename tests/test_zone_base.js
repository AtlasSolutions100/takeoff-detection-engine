// ZONE TABLE + PER-ZONE BASE - the client half (2026-09-16; Beaumont call
// 2026-09-15). The modal asks for a base (thickness + name), the request
// carries it as proposed_layers, the zone's rows gain "<zone> place <name>",
// calcstate keeps it, and the panel renders one table over all zones
// (area / cut / fill / net / total, all-zones row; strip and base on each
// zone's row line and summed in the note - seven columns overflowed the panel).
//
// run: node tests/test_zone_base.js   (fails on 86abed9)
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "..", "webui", "style.css"), "utf8");
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
// a tiny harness: state / store / tradeKey stubs around the row functions
const state = { extraItems: [{ label: "Site Cut", qty: 5, unit: "CY" }] };
const sessions = {};
const store = { getItem: () => null, setSession: (k, v) => { sessions[k] = v; } };
const H = new Function("state", "store",
  "const tradeKey = l => 'trade:' + l; const ewR = v => Math.round(Number(v) || 0); const ewCY = v => Math.round(Number(v) || 0).toLocaleString();\n"
  + [fnSrc("gzBase"), fnSrc("gzRowLabels"), fnSrc("gzClearItems"), fnSrc("gzApplyItems"), fnSrc("gzBaseCY"), fnSrc("gzZoneCells"), fnSrc("gzNet"), fnSrc("gzEsc")].join("\n")
  + "\nreturn { gzBase, gzRowLabels, gzClearItems, gzApplyItems, gzBaseCY, gzZoneCells, gzNet, gzEsc };")(state, store);

// ---- request body ----
check(/function gzBody\(polygon, design, strip_in, base\)/.test(js) && /proposed_layers: b,/.test(js), "gzBody carries the base as proposed_layers");
check(/gzBody\(z\.polygon, z\.design, z\.strip_in, gzBase\(z\)\)/.test(js) && /gzBody\(gzModalPoly, c\.design, c\.strip_in, c\.base\)/.test(js), "both callers pass it (saved zone and the live modal)");
check(H.gzBase({ base_in: 6, base_name: " Gravel " }).thickness_in === 6 && H.gzBase({ base_in: 6, base_name: " Gravel " }).name === "Gravel", "gzBase reads thickness + trimmed name");
check(H.gzBase({}).thickness_in === 0 && H.gzBase({}).name === "Base", "no base -> 0 in, named Base");
check(H.gzBase({ base_in: -3 }).thickness_in === 0, "a negative thickness is no base");

// ---- modal ----
check(/<input id="gzBaseIn" type="number" min="0" step="1" value="0"/.test(html) && /<input id="gzBaseName" type="text" value="Base" maxlength="30"/.test(html), "the modal asks for the base thickness and name");
check(/const bv = parseFloat\(\(\$\("gzBaseIn"\) \|\| \{\}\)\.value\);/.test(fnSrc("gzModalCollect")) && /strip_in, base \};/.test(fnSrc("gzModalCollect")), "gzModalCollect returns the base with every design mode");
check(/base_in: c\.base\.thickness_in, base_name: c\.base\.name,/.test(js) && /z\.base_in = c\.base\.thickness_in; z\.base_name = c\.base\.name;/.test(js), "save writes it onto a new zone and an edited one");
check(/\$\("gzBaseIn"\)\.value = zone && Number\.isFinite\(\+zone\.base_in\) \? zone\.base_in : 0;/.test(js), "reopening a zone shows its base");
check(/"gzStrip", "gzBaseIn", "gzBaseName"\]/.test(js), "typing in either base box recalculates the live line");
check(/base \$\{lay\.thickness_in\}&Prime; \$\{gzEsc\(lay\.name\)\} over/.test(js) && /figured to the subgrade, \$\{j\.subgrade_offset_ft\} ft below/.test(js), "the live line says the base volume and that the dig stops at the subgrade");

// ---- calcstate ----
check(/base_in: isFinite\(\+z\.base_in\) \? \+z\.base_in : 0, base_name: z\.base_name \|\| "Base",/.test(js), "calcstate saves the base");
check(/base_in: isFinite\(\+s\.base_in\) \? Math\.max\(\+s\.base_in, 0\) : 0,\s*base_name: String\(s\.base_name \|\| "Base"\)\.slice\(0, 30\),/.test(js), "and restores it");

// ---- rows ----
const z = { name: "Pad A", base_in: 6, base_name: "Gravel", labels: null,
            result: { cut_yd3: 185.2, fill_yd3: 0, strip_yd3: 0, layers: [{ name: "Gravel", thickness_in: 6, volume_yd3: 185.2 }], zone_area_sf: 10000, area_sf: 10000 } };
check(JSON.stringify(H.gzRowLabels(z)) === JSON.stringify(["Pad A cut", "Pad A fill", "Pad A topsoil strip", "Pad A place Gravel"]), "four labels per zone, the fourth is the place row");
H.gzApplyItems(z);
const mine = state.extraItems.filter(i => /^Pad A/.test(i.label)).map(i => [i.label, i.qty, i.unit]);
check(JSON.stringify(mine) === JSON.stringify([["Pad A cut", 185, "CY"], ["Pad A place Gravel", 185, "CY"]]), "Add to takeoff puts the cut and the place row in (got " + JSON.stringify(mine) + ")");
check(sessions["trade:Pad A place Gravel"] === "Earthwork & Grading", "the place row is seeded to Earthwork & Grading like the others");
check(state.extraItems.some(i => i.label === "Site Cut"), "other systems' rows are untouched");
H.gzClearItems(z);
check(!state.extraItems.some(i => /^Pad A/.test(i.label)) && state.extraItems.length === 1, "Remove clears all four by exact label");

// ---- the table ----
check(JSON.stringify(H.gzZoneCells(z)) === JSON.stringify([10000, 185.2, 0, -185.2, 185.2]), "cells: area, cut, fill, net (fill - cut), total (cut + fill) - Beaumont's five");
check(H.gzBaseCY(z) === 185.2, "the base is read off the zone's layers for the row line and the all-zones note");
check(/all zones: strip \$\{ewCY\(strip\)\} CY \\u00b7 base \$\{ewCY\(base\)\} CY/.test(js), "the note under the table sums strip and base over all zones");
check(/\.gz-table td:first-child \{ max-width: 76px; overflow: hidden; text-overflow: ellipsis; \}/.test(css), "a long zone name truncates instead of pushing the numbers out of the panel");
check(H.gzNet(-185.2) === "−185" && H.gzNet(40) === "+40" && H.gzNet(0.2) === "0", "net carries its sign (+ in, - out)");
check(H.gzEsc('<b>&"') === "&lt;b&gt;&amp;&quot;", "zone names are escaped into the table");
check(/const GZ_TABLE_COLS = \["area", "cut", "fill", "net", "total"\];/.test(js) && /area in SF, the rest in CY/.test(js), "the five columns with the units in the note (seven overflowed the panel, and unit-suffixed headers still clipped)");
check(!/\.gz-table table \{[^}]*var\(--mono\)/.test(css), "the table uses the UI font (narrower digits) with tabular numbers, not mono");
check(/if \(done\.length > 1\) rows \+= line\("all zones", tot, "gz-tot"\);/.test(js), "an all-zones row once there are two");
check(/<div id="gzTable" class="gz-table" hidden><\/div>/.test(html) && !/id="gzTotals"/.test(html) && !/\$\("gzTotals"\)/.test(js), "the table replaces the old totals line");
check(/\.gz-table th\.num, \.gz-table td\.num \{ text-align: right; font-variant-numeric: tabular-nums; \}/.test(css), "numbers right-aligned, tabular");
check(/if \(z\.base_in > 0\) s \+= ` · base \$\{z\.base_in\}" \$\{gzBase\(z\)\.name\}`;/.test(js), "the design summary names the base");
const av = (/app\.js\?v=(\d+)/.exec(html) || [])[1];
check(+av >= 181, "cache buster moved (app.js >= 181)");

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all zone-base checks passed");
