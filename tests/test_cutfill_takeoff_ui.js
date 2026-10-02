// THE ENGINE'S ROWS IN THE HOST (2026-09-26; PLAN.md section 8 and 2.3): cfRows takes the engine's
// own rows when it sent them (limit line, strip, balance, walls) and keeps the old four-row shape
// otherwise; the bid-view heading never sums cut, fill and the net into one CY figure; the
// structure panel's groups get a dig depth per group from the datum and the existing ground, with
// the printed elevation read as the bottom or the top of the footing.
//
// run: node tests/test_cutfill_takeoff_ui.js
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const ejs = fs.readFileSync(path.join(__dirname, "..", "webui", "cutfill", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "webui", "cutfill", "index.html"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(src, name) {
  const start = src.indexOf("function " + name + "(");
  if (start < 0) return "";
  let depth = 0;
  for (let j = src.indexOf("{", start); j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}" && --depth === 0) return src.slice(start, j + 1);
  }
  return "";
}
const constSrc = (src, name) => { const m = src.match(new RegExp("const " + name + " = [^\\n]+")); return m ? m[0] : ""; };

// ---- cfRows ----------------------------------------------------------------------------------
const cfRows = new Function(constSrc(js, "CF_ROW_RE") + "\n" + fnSrc(js, "cfRows") + "\nreturn cfRows;")();
let rows = cfRows({ cut_cy: 1000, fill_cy: 1600 });
check(JSON.stringify(rows.map((r) => [r.label, r.qty])) === JSON.stringify([["Site Cut", 1000], ["Site Fill", 1600], ["Import Structural", 600]]), "no engine rows: the four-row shape stands (fill-heavy: Import Structural)");
rows = cfRows({ cut_cy: 1000, fill_cy: 1600, rows: [
  { label: "Silt / Construction Fence", qty: 1124.9, unit: "LF", basis: "the sheet's erosion-control lines" },
  { label: "Clear & Grub", qty: 312044, unit: "SF", basis: "the limit" },
  { label: "Strip Topsoil", qty: 7704.8, unit: "CY", basis: "312,044 SF x 8 in" },
  { label: "Site Cut", qty: 1000, unit: "CY" }, { label: "Site Fill", qty: 1600, unit: "CY" },
  { label: "Rough Place Topsoil", qty: 3852.4, unit: "CY" }, { label: "Export Topsoil", qty: 3852.4, unit: "CY" },
  { label: "Import Structural", qty: 600, unit: "CY" }, { label: "Site walls face", qty: 1200, unit: "SF" },
  { label: "Something Else", qty: 5, unit: "EA" }, { label: "Export Cut", qty: 0, unit: "CY" } ] });
check(rows.length === 9 && rows[0].label === "Silt / Construction Fence" && rows[0].basis.indexOf("erosion") >= 0 && !rows.some((r) => r.label === "Something Else" || r.qty === 0),
  "engine rows are taken as sent, with their basis; an unknown label and a zero row never reach the takeoff");
const CF_ROW_RE = new Function(constSrc(js, "CF_ROW_RE") + "\nreturn CF_ROW_RE;")();
for (const l of ["Site Cut", "Site Fill", "Export Cut", "Import Structural", "Silt / Construction Fence", "Clear & Grub", "Strip Topsoil", "Rough Place Topsoil", "Export Topsoil", "Import Topsoil", "Site walls face"])
  check(CF_ROW_RE.test(l), "CF_ROW_RE names " + l + " (so a stale result removes it with the rest)");

// ---- the bid-view CY chip ------------------------------------------------------------------
// The merged chip (bid-money + beaumont-rows): cut / fill / net each on their own chip (the
// bucket's `cf`), every other CY row of the bucket by name when there are several.
const chipBlock = js.slice(js.indexOf("const chipText = (B) => {"), js.indexOf("let any = false;"));
check(chipBlock.length > 100 && /CUBIC YARDS ARE NEVER ONE NUMBER/.test(chipBlock), "the chip text has the CY rule");
const chipText = new Function("const abbr = (v) => v >= 1000 ? (v / 1000).toFixed(1) + 'k' : String(Math.round(v * 10) / 10); const unitOrder = (x) => x === 'ft' || x === 'pt' ? 0 : x === 'EA' ? 1 : 2;\n"
  + constSrc(js, "BS_CF_ORDER") + "\n" + fnSrc(js, "bidCfKind") + "\n" + chipBlock + "\nreturn chipText;")();
const members = [{ label: "Site Cut", unit: "CY", qty: 1000 }, { label: "Site Fill", unit: "CY", qty: 1600 }, { label: "Import Structural", unit: "CY", qty: 600 }, { label: "Silt Fence", unit: "LF", qty: 1125 }];
const txt = chipText({ cf: { cut: { CY: 1000 }, fill: { CY: 1600 }, import: { CY: 600 } }, byUnit: { LF: 1125 }, members });
check(txt.indexOf("3.2k CY") < 0 && /1\.0k CY cut/.test(txt) && /1\.6k CY fill/.test(txt) && /600 CY import/.test(txt) && /1\.1k LF/.test(txt),
  "the heading lists cut, fill and the net on their own chips and never their sum (was 3.2k CY): " + txt);
check(chipText({ cf: {}, byUnit: { CY: 900 }, members: [{ label: "Strip Topsoil", unit: "CY", qty: 900 }] }) === "900 CY", "one CY row still reads as its number");
const topsoil = [{ label: "Strip Topsoil", unit: "CY", qty: 7704.8 }, { label: "Rough Place Topsoil", unit: "CY", qty: 3852.4 }, { label: "Site Cut", unit: "CY", qty: 1000 }];
const txt2 = chipText({ cf: { cut: { CY: 1000 } }, byUnit: { CY: 11557.2 }, members: topsoil });
check(txt2.indexOf("11.6k CY") < 0 && /strip topsoil 7\.7k/.test(txt2) && /rough place topsoil 3\.9k/.test(txt2) && /1\.0k CY cut/.test(txt2),
  "strip and rough place are different dirt too: each by name, the cut on its own chip: " + txt2);

// ---- the structure groups: dig depth per group, printed = bottom or top of the footing -------------
const stSrc = [constSrc(js, "stFmt"), fnSrc(js, "stThicknessOf"), fnSrc(js, "stGroupsCalc"), "function stDatumOf(st) { return Number.isFinite(+st.datum_ft) ? +st.datum_ft : null; }"].join("\n");
const stGroupsCalc = new Function(stSrc + "\nreturn stGroupsCalc;")();
const d = { groups: { groups: [{ bof_ft: 83, lf_ft: 200, width_ft: 3, marks: { "CF3.0": 4 } }, { bof_ft: 86, lf_ft: 100, width_ft: 2, marks: { "CF2.0": 2 } }] },
            by_mark: { "CF3.0": { schedule: { depth_ft: 1.0 } }, "CF2.0": { schedule: { depth_ft: 1.0 } } } };
let st = { datum_ft: 7732, layback_hv: 1, printed_is: "bottom", groupE: { "83": 7721 }, groupGrade: { "86": { mean_ft: 7722 } } };
let c = stGroupsCalc(st, d);
check(c.groups["83"].dig_to_ft === 7715 && c.groups["83"].depth_ft === 6 && Math.abs(c.groups["83"].cy - 200 * 6 * (3 + 3) / 27) < 0.01,
  "group 83': bottom of footing 7715 civil, typed ground 7721 -> 6 ft deep, 200 x 6 x (3 + 3) / 27 = " + c.groups["83"].cy.toFixed(1) + " CY");
check(c.groups["86"].existing_from === "civil surface" && c.groups["86"].depth_ft === 4 && c.n_with === 2 && Math.abs(c.total_cy - (200 * 6 * 6 / 27 + 100 * 4 * (2 + 2) / 27)) < 0.01,
  "group 86' takes the civil surface's ground (7722) -> 4 ft; both groups sum: " + c.total_cy.toFixed(1) + " CY");
st.printed_is = "top";
c = stGroupsCalc(st, d);
check(c.groups["83"].dig_to_ft === 7714 && c.groups["83"].depth_ft === 7 && c.groups["86"].depth_ft === 5, "printed = top of footing: every group digs one schedule thickness (1 ft) deeper");
st = { datum_ft: NaN, layback_hv: 1, printed_is: "bottom", groupE: { "83": 7721 }, groupGrade: {} };
c = stGroupsCalc(st, d);
check(c.total_cy === null && c.n_without === 2, "no datum: no depth, nothing to add");
check(/printed elevation \(83/.test(fnSrc(js, "stGroupsTable")) && /existing ground from the civil surface/.test(fnSrc(js, "stGroupsTable")), "the groups table offers the printed-elevation setting and the civil-surface ground");
check(/top of wall/.test(fnSrc(js, "stFootingsBlock")), "the footing chips show the bracketed top of wall as printed");
check(/printed_is/.test(fnSrc(js, "calcStatePayload")) && /groupE/.test(fnSrc(js, "stApplySaved")), "the setting and the typed grounds are saved with the page and restored");

// ---- the engine page: the cards and the takeoff inputs -------------------------------------------
for (const id of ["btnLimitPick", "limitOffer", "layerList", "btnLayerAdd", "zoneList", "btnZone", "takeoffBox", "toStripIn", "toStripFactor", "toPlaceIn", "toPlaceLess", "toPlacePct", "toExport", "toImport", "toFence", "crewMapLink", "wallsBox", "wallAdd"])
  check(html.indexOf('id="' + id + '"') >= 0, "the engine page has #" + id);
const takeoffOpts = new Function("state", constSrc(ejs, "TO_FIELDS") + "\n" + fnSrc(ejs, "takeoffOpts") + "\nreturn takeoffOpts;")({ project: { settings: { takeoff: { strip_in: 8 } } } });
const o = takeoffOpts();
check(o.strip_in === 8 && o.strip_factor === 1.15 && o.export_factor === 1 && o.import_factor === 1 && o.fence_source === "auto", "the takeoff inputs default to neat factors, the 1.15 strip allowance and the automatic fence source");
const hs = fnSrc(ejs, "hostSummary");
check(/rows:/.test(hs) && /takeoff/.test(hs), "the summary posted to the host carries the rows");
for (const s of ["Barn", "Beaumont", "Pierce", "#853", "Estimate"]) {
  check(html.indexOf(s) < 0 && ejs.indexOf("'" + s) < 0 && fnSrc(js, "stGroupsTable").indexOf(s) < 0 && fnSrc(js, "stGroupsCalc").indexOf(s) < 0, "no client name in the new UI copy: " + s);
}
const cutfillPy = fs.readFileSync(path.join(__dirname, "..", "cutfill", "takeoff.py"), "utf8");
check(!/Beaumont|Barn|Pierce/.test(cutfillPy.replace(/#[^\n]*/g, "")), "no client name in the engine's row text");

console.log(fails ? "\n" + fails + " failure(s)" : "\nall ok");
process.exit(fails ? 1 : 0);
