// GRADING ZONES ON THE SCREENS (2026-09-28): a zone's own takeoff rows are recognised everywhere the
// host reads cut / fill rows, the bid view files them on their line whatever the zone is called,
// "Show only" picks one zone's cells off the grid, and Align warns before it saves two pairs that
// would turn or resize a sheet (a job's sheet 3.1 sat turned 177 degrees).
//
// run: node tests/test_cutfill_zones_ui.js
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const ejs = fs.readFileSync(path.join(__dirname, "..", "webui", "cutfill", "app.js"), "utf8");
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

// ---- 1. the host knows a zone's rows ------------------------------------------------------------
const CF_ROW_RE = new Function(constSrc(js, "CF_ROW_RE") + "\nreturn CF_ROW_RE;")();
check(CF_ROW_RE.test("Site Cut – Island") && CF_ROW_RE.test("Site Fill – rest of the limit") && CF_ROW_RE.test("Site Cut")
  && CF_ROW_RE.test("Import Structural") && !CF_ROW_RE.test("Site Cutter") && !CF_ROW_RE.test("Site Cut Island"),
  "CF_ROW_RE takes a zone's own rows (and still refuses what is not the engine's)");
const bidCfKind = new Function(fnSrc(js, "bidCfKind") + "\nreturn bidCfKind;")();
check(bidCfKind("Site Cut – Island") === "cut" && bidCfKind("Site Fill – Lot A") === "fill" && bidCfKind("Export Cut") === "export"
  && bidCfKind("Site Cut") === "cut" && bidCfKind("Site Cut - export haul") === null, "the bid view's chips count a zone's rows as cut / fill (the engine's en dash only)");
const priceKey = new Function(fnSrc(js, "priceLabel") + "\n" + fnSrc(js, "priceKey") + "\nreturn priceKey;")();
check(priceKey("Site Cut – Island") === "price:Site Cut" && priceKey("Site Fill – rest of the limit") === "price:Site Fill"
  && priceKey("Export Cut") === "price:Export Cut" && priceKey("Site Cut") === "price:Site Cut",
  "a zone's row is priced as Site Cut / Site Fill (its dollars used to drop out of the total)");
const RESULT_ROWS = new Function(constSrc(ejs, "RESULT_ROWS") + "\nreturn RESULT_ROWS;")();
check(RESULT_ROWS.test("Site Cut – Island") && RESULT_ROWS.test("Site walls face") && !RESULT_ROWS.test("Clear & Grub"),
  "a refused compute drops a zone's rows from the engine's card too");

// ---- 2. the bid view files a zone's row on its line whatever the zone is called ------------------
// (BS_UTILITY_WORDS spans two lines in app.js and plays no part without a utility: a stand-in)
const bsSrc = [constSrc(js, "BS_NET_RE"), constSrc(js, "BS_NET_WORDS"), "const BS_UTILITY_WORDS = {};", constSrc(js, "BS_LABEL_ONLY"),
  constSrc(js, "BS_CF_PART_RE"), constSrc(js, "BS_LAYER_ROW_RE"), constSrc(js, "BS_DIRT_LAYER_RE"), constSrc(js, "BS_LAYER_VERB"),
  constSrc(js, "BS_CG_ROW_RE"), constSrc(js, "BS_CG_CURB_RE"), constSrc(js, "BS_CG_GUTTER_RE"),
  fnSrc(js, "bidNormText"), fnSrc(js, "bidTokenHit"), fnSrc(js, "bidNamesNet"), fnSrc(js, "bsForeignLayer"), fnSrc(js, "bsNamesLayer"),
  fnSrc(js, "bsLineFor")].join("\n");
const bsLineFor = new Function(bsSrc + "\nreturn bsLineFor;")();
const lines = [
  { n: 4, name: "Site Cut", re: "^(Site Cut|Cut \\(earthwork\\))$", match: ["SITE CUT", "=CUT"], not: ["BOULDER", "EXPORT", "IMPORT", "HAUL", "NET"] },
  { n: 5, name: "Site Fill", re: "^(Site Fill|Fill \\(earthwork\\))$", match: ["SITE FILL", "=FILL"], not: ["EXPORT", "IMPORT", "HAUL", "NET", "BACKFILL"] },
  { n: 26, name: "Export", match: ["EXPORT"] }];
check(bsLineFor("Site Cut – Export pad", null, lines, null, "cut").index === 0 && bsLineFor("Site Fill – Haul road", null, lines, null, "fill").index === 1
  && bsLineFor("Export Cut", null, lines, null, "net").index === 2,
  "a zone named 'Export pad' still files on Site Cut, never on the export line");
check(bsLineFor("Site Cut - export haul", null, lines, null, "cut").index !== 0, "a typed row with a hyphen keeps the Site Cut line's vetoes");

// ---- 3. "Show only": one zone's cells ------------------------------------------------------------
const isoIndex = new Function("state", fnSrc(ejs, "isoIndex") + "\nreturn isoIndex;");
const g = { zone: [0, 0, 1, -1], zone_ids: [7, 9] };
check(isoIndex({ isoZone: 9 })(g) === 1 && isoIndex({ isoZone: 7 })(g) === 0 && isoIndex({ isoZone: "rest", result: { zones_rest: { cut_cy: 1 } } })(g) === -1
  && isoIndex({ isoZone: null })(g) === null && isoIndex({ isoZone: 42 })(g) === null && isoIndex({ isoZone: 9 })({}) === null,
  "Show only maps a zone id to its cells (the rest of the limit is -1; an unknown zone or an older grid shows everything)");
check(isoIndex({ isoZone: "rest", result: {} })(g) === null,
  "the rest shown alone, then a compute without a rest: the map shows everything again (it used to stay blank)");
check(/if \(zi != null && g\.zone\[idx\] !== zi\) continue;/.test(fnSrc(ejs, "drawGrid")), "the map skips the other zones' cells while one is shown alone");

// ---- 4. Align warns before a turn or a resize ----------------------------------------------------
const alignCheck = new Function(constSrc(ejs, "ALIGN_MAX_TURN_DEG") + "\n" + constSrc(ejs, "ALIGN_MAX_SCALE") + "\n" + fnSrc(ejs, "alignCheck") + "\nreturn alignCheck;")();
const good = [{ src: [562.9, 477.7], dst: [594.9, 1573.7] }, { src: [1429.4, 477.7], dst: [1461.4, 1573.7] }];
const crossed = [{ src: [560.2, 489.6], dst: [1464.5, 1448.6] }, { src: [1432.6, 520.7], dst: [592.4, 1457.3] }];
const resized = [{ src: [0, 0], dst: [10, 10] }, { src: [1000, 0], dst: [1060, 10] }];
check(alignCheck(good) === null, "two pairs that only slide the sheet (Rose 3.1 onto 3.0) save without a word");
const c = alignCheck(crossed) || "";
check(/turn the sheet 17[0-9]°/.test(c), "the crossed pairs of a real job are caught: " + c);
check(/change its size 5 %/.test(alignCheck(resized) || ""), "a 5 % resize (1,000 pt apart on one sheet, 1,050 on the other) is caught: " + alignCheck(resized));
check(/too close/.test(alignCheck([{ src: [0, 0], dst: [5, 5] }, { src: [3, 0], dst: [8, 5] }]) || ""), "two points almost on top of each other are caught");
check(/const chk = alignCheck\(pairs\);/.test(fnSrc(ejs, "alignClick")) && /window\.confirm/.test(fnSrc(ejs, "alignClick")),
  "Align asks before it saves pairs that turn or resize the sheet");

// ---- 5. the price book: a zone's rows share the Site Cut / Site Fill price -----------------------
const priceLabel = new Function(fnSrc(js, "priceLabel") + "\nreturn priceLabel;")();
check(priceLabel("Site Cut – Island") === "Site Cut" && priceLabel("Site Fill – rest of the limit") === "Site Fill"
  && priceLabel("Export Cut") === "Export Cut" && priceLabel("Site Cut - export haul") === "Site Cut - export haul",
  "a zone's row is priced under its Site Cut / Site Fill name (the engine's en dash only)");
const svBookRows = new Function(fnSrc(js, "priceLabel") + "\n" + fnSrc(js, "svBookRows") + "\nreturn svBookRows;")();
const book = svBookRows(["Site Cut"], [{ label: "Site Cut – Island", unit: "CY" }, { label: "Site Cut – Lot A", unit: "CY" },
  { label: "Site Fill – Island", unit: "CY" }]);
check(book.map(r => r.label).join("|") === "Site Cut|Site Fill" && book[0].inUse && book[0].stored && book[1].unit === "CY",
  "the price book lists one Site Cut and one Site Fill row for all the zones (one row per zone all set the same price)");
const svImportPlan = new Function(fnSrc(js, "priceLabel") + "\n" + fnSrc(js, "svImportPlan") + "\nreturn svImportPlan;")();
const ip = svImportPlan([{ label: "Site Cut – Island", unit: "CY", price: 4 }], ["Site Cut"]);
check(ip.overwrites.length === 1 && ip.overwrites[0].label === "Site Cut" && !ip.adds.length,
  "an imported zone row sets the Site Cut price it shares, and is counted as overwriting it");
const svParseCSV = new Function(fnSrc(js, "svSplitCsvLine") + "\n" + fnSrc(js, "svParseCSV") + "\nreturn svParseCSV;")();
const parsed = svParseCSV(String.fromCharCode(0xfeff) + "label,unit,price\nSite Cut,CY,4.5\n");
check(parsed.rows.length === 1 && parsed.rows[0].label === "Site Cut" && parsed.rows[0].price === 4.5 && !parsed.bad.length,
  "a price book saved with a BOM (Excel's CSV UTF-8, and our export) reads back: its header is still the header");
check(/new Blob\(\[String\.fromCharCode\(0xfeff\) \+ svSerializeCSV\(rows\)\]/.test(js),
  "the price-book export leads with a BOM, so Excel reads it as UTF-8");
check(/Priced as \$\{priceLabel\(it\.label\)\}/.test(js), "a zone row's rate cell says it is priced as Site Cut / Site Fill");

// ---- 6. "Show only" is off for a zone that holds no cell of its own --------------------------------
const el = (tag, attrs, text) => ({ tag, attrs, text });
const isoButton = (st) => new Function("state", "el", "$", "renderZones", "requestDraw", "setStatus", "setMapVisible", "fmt0",
  fnSrc(ejs, "isoButton") + "\nreturn isoButton;")(st, el, () => ({ checked: true }), () => {}, () => {}, () => {}, () => {}, String);
const st = { isoZone: null, result: { grid: { zone: [0, 0, -1], zone_ids: [7, 9] }, zones_rest: { cut_cy: 1 } } };
const bLot = isoButton(st)(7, "Lot", { cut_cy: 5, fill_cy: 1, area_sf: 900 });
const bPad = isoButton(st)(9, "Pad", { cut_cy: 2, fill_cy: 0, area_sf: 960 });
const bRest = isoButton(st)("rest", "the rest of the limit", { cut_cy: 1, fill_cy: 0, area_sf: 50 });
const bNone = isoButton({ isoZone: null, result: null })(7, "Lot", { cut_cy: 5, fill_cy: 1 });
check(!bLot.attrs.disabled && bPad.attrs.disabled === true && /narrower than half a grid cell/.test(bPad.attrs.title)
  && !bRest.attrs.disabled && bNone.attrs.disabled === true && /Compute first/.test(bNone.attrs.title),
  "Show only is off, and says why, for a zone with no cell of its own (it used to blank the map)");
// the reason follows why: outside the limit, drawn on another sheet; and the rest owns only the cells the map draws
const bOut = isoButton(st)(9, "Outside", { cut_cy: 0, fill_cy: 0, area_sf: 0 });
const bElse = isoButton(st)(7, "Lot", { cut_cy: 5, fill_cy: 1, area_sf: 900 }, "Drawn on sheet 3.1, so it is left out of these volumes: nothing to show");
const stL = { isoZone: null, result: { grid: { zone: [0, 0, -1, -1], zone_ids: [7], eg: [1, 1, null, null], fg: [2, 2, null, null] }, zones_rest: { cut_cy: 77 } } };
const bRestL = isoButton(stL)("rest", "the rest of the limit", { cut_cy: 77, fill_cy: 0, area_sf: 1596 });
check(bOut.attrs.disabled === true && /lies outside the limit/.test(bOut.attrs.title)
  && bElse.attrs.disabled === true && /Drawn on sheet 3\.1/.test(bElse.attrs.title)
  && bRestL.attrs.disabled === true,
  "Show only says 'outside the limit' / 'drawn on sheet X', and the rest with no drawn cell is off (it drew 0 cells)");
check(/r\.grid\.section_ft \|\| r\.grid\.zsec\.some\(\(v\) => v\)/.test(ejs),
  "the Result card says 'to subgrade' only when a cell was lowered (zones all at 0 are finished grade)");
// a plain Site Cut row in an imported price book wins over a zone's row, in either order
const ipA = svImportPlan([{ label: "Site Cut – Lot", unit: "CY", price: 5 }, { label: "Site Cut", unit: "CY", price: 3 }], []);
const ipB = svImportPlan([{ label: "Site Cut", unit: "CY", price: 3 }, { label: "Site Cut – Lot", unit: "CY", price: 5 }], []);
check(ipA.adds.length === 1 && ipA.adds[0].price === 3 && ipB.adds.length === 1 && ipB.adds[0].price === 3,
  "a price book holding both 'Site Cut' and 'Site Cut – Lot' imports the plain row's price, whatever the order");
check(/every zone's " \+ priceLabel\(label\) \+ " row shares it/.test(js), "adding a zone row's price by hand says every zone shares it");

console.log(fails ? "\n" + fails + " failure(s)" : "\nall ok");
process.exit(fails ? 1 : 0);
