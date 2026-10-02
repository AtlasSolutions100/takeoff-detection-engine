// THE BID SHEET IN THE UI (2026-09-16; Wave 2 item 9). Settings -> Bid sheet
// (the line editor, save / reset per seat), the bid view's "in bid-sheet
// order" mode, the job's Bid sheet CSV link carrying the cross-sheet
// unticks - and the JS port of the matcher pinned to the Python original
// through tests/fixtures/bidsheet_cases.json + bidsheet_default.json.
//
// run: node tests/test_bidsheet_ui.js   (fails on cd9fadf)
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "..", "webui", "style.css"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
const cases = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "bidsheet_cases.json"), "utf8")).cases;
const DEF = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "bidsheet_default.json"), "utf8"));
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
// ---- markup ------------------------------------------------------------------------
for (const id of ["svGroupBidsheet", "svBidsheetSection", "svBsNote", "svBsTable", "svBsAdd", "svBsSave", "svBsReset", "svBsState", "bidSheetMode", "bidsheetLink"])
  check(html.includes(`id="${id}"`), `#${id} exists in index.html`);
check(/<a href="#bidsheet">Bid sheet<\/a>/.test(html), "the settings nav links the section");
check(html.indexOf('id="svGroupBidsheet"') < html.indexOf('id="svGroupAccount"') && html.indexOf('id="svGroupBidsheet"') > html.indexOf('id="svGroupPricing"'), "the group sits between Pricing and Account");
check(/first\s+line that matches wins, top to bottom/.test(html.replace(/\s+/g, " ")), "the note explains first-match-wins");
// ---- settings -----------------------------------------------------------------------
for (const n of ["renderSvBidsheet", "loadSvBs", "svBsSave", "bsLoad", "bsLineFor", "bsGroupRows", "bsCsvUrl", "bsLinkSync", "bsMode"])
  check(!!fnSrc(n), `${n} exists`);
check(/renderSvBook\(\);\n  renderSvBidsheet\(\);/.test(fnSrc("renderSettings")), "renderSettings draws the bid-sheet section");
check(/bidsheet: "svGroupBidsheet"/.test(fnSrc("svHashTarget")), "#bidsheet lands on the section");
const rs = fnSrc("renderSvBidsheet");
check(/name\.oninput = \(\) => \{ ln\.name = name\.value; touch\(\); \};/.test(rs) && /sel\.onchange = \(\) => \{ ln\.unit = sel\.value; touch\(\); \};/.test(rs), "typing edits the model, never re-renders (a focused field is never rebuilt)");
check(/lines\.splice\(i - 1, 0, lines\.splice\(i, 1\)\[0\]\)/.test(rs) && /lines\.splice\(i \+ 1, 0, lines\.splice\(i, 1\)\[0\]\)/.test(rs) && /lines\.splice\(i, 1\); touch\(\); renderSvBidsheet\(\);/.test(rs), "move up / down / remove re-render");
check(/\$\("svBsReset"\)\.onclick = \(\) => svBsSave\(null\);/.test(rs) && /body: JSON\.stringify\(\{ lines \}\)/.test(fnSrc("svBsSave")), "Reset posts null; Save posts the list");
check(/the printed sheet as scanned/.test(rs), "the default is named for what it is");
// ---- the bid view ----------------------------------------------------------------------
const bv = fnSrc("renderBidView");
check(/const sheetMode = !!\(bsMode\(\) && bsSheet && bsSheet\.lines\);/.test(bv) && /sheetMode \? bsGroupRows\(rows, bsSheet\.lines\) : bidGroupRows\(/.test(bv), "sheet mode regroups the same rows onto the sheet's lines");
check(/if \(bsMode\(\) && bsSheet === undefined\) bsLoad\(\)\.then\(\(\) => renderItemized\(\)\);/.test(bv), "the sheet is fetched once, then the view re-renders");
check(/if \(!B\.n && !sheetMode\) continue;/.test(bv) && /"— nothing on this line yet \("/.test(bv), "a sheet's empty line is drawn (dimmed), a bucket's is not");
check(/if \(!sheetMode\) \{   \/\/ a bucket pick means nothing on the sheet's lines/.test(bv), "the bucket picker is hidden in sheet mode");
check(/not summed: " \+ m\.sheetUnit \+ " on a "/.test(bv), "a row in another unit says it is not summed");
check(/store\.setItem\("bid\.sheetMode", cb\.checked \? "1" : "0"\); renderItemized\(\);/.test(js), "the head toggle remembers the mode and re-renders");
// ---- the CSV link ------------------------------------------------------------------------
check(/const bl = \$\("bidsheetLink"\);\n  if \(bl\) \{ bl\.href = bsCsvUrl\(\); bl\.hidden = false; \}/.test(js), "Approve exposes the Bid sheet CSV link");
check(/\/api\/job\/\$\{state\.job\}\/bidsheet\.csv/.test(fnSrc("bsCsvUrl")) && /bsIgnoreQuery\(\)/.test(fnSrc("bsCsvUrl"))
      && /encodeURIComponent\(m\[1\] \+ ":" \+ m\[2\]\)/.test(fnSrc("bsIgnoreQuery")), "the link carries the cross-sheet unticks as page:label");
check(/renderXs\(\);\n        bsLinkSync\(\);/.test(js), "an untick on the cross-sheet panel refreshes the link");
// ---- the matcher: same answers as the Python original ----------------------------------
// the whole pure BIDVIEW-RULES block: bsLineFor / bsGroupRows lean on the
// net-row, utility and run-replacement helpers that live beside them
const bvA = js.indexOf("// BIDVIEW-RULES-BEGIN"), bvB = js.indexOf("// BIDVIEW-RULES-END");
check(bvA >= 0 && bvB > bvA, "BIDVIEW-RULES markers present");
const H = new Function("store", js.slice(bvA, bvB) + "\nreturn { bsLineFor, bsGroupRows, bidCfKind, bidRunReplacement };")({});
let bad = [];
for (const c of cases) {
  const hit = H.bsLineFor(c.label, null, DEF);
  const got = hit.index < 0 ? null : DEF[hit.index].n;
  if (got !== c.n) bad.push([c.label, c.n, got]);
}
check(bad.length === 0, `every shared case files onto its printed line in the JS port too (${cases.length} cases)` + (bad.length ? " - wrong: " + JSON.stringify(bad) : ""));
check(H.bsLineFor("solid solid", "C-UTIL-WATR", DEF).index === 15 && H.bsLineFor("solid solid", "C-UTIL-W", DEF).index === -1 && H.bsLineFor("solid", "E", DEF).index === -1, "the CAD layer is a second pass; a one-letter token never fires from the layer");
const rows = [{ label: "Clear & Grub", unit: "SF", qty: 1000, cnt: 1, sids: [], isCalc: true },
              { label: "4\" SLAB ON GRADE", unit: "ft", qty: 220, cnt: 4, sids: [1] },
              { label: "8\" PVC STORM", unit: "ft", qty: 400, cnt: 3, sids: [2] },
              { label: "Over-excavation", unit: "CY", qty: 39, cnt: 1, sids: [], isCalc: true }];
const G = H.bsGroupRows(rows, DEF);
// the storm family's 400 ft sums as 400 LF - the LINE's unit, the CSV's
// unit - not as a separate "ft" chip beside a run row's "LF" (D03)
check(G.length === DEF.length + 1 && G[1].byUnit.SF === 1000 && G[20].byUnit.LF === 400 && G[20].byUnit.ft === undefined, "one group per line in order, plus the leftovers; feet sum in the line's LF");
const Gp = H.bsGroupRows([{ label: "8\" PVC STORM", unit: "pt", qty: 400, cnt: 3, sids: [2] }], DEF);
check(Gp[20].n === 1 && Object.keys(Gp[20].byUnit).length === 0 && Gp[20].other.PT === 400 && Gp[20].members[0].sheetOk === false, "an unscaled sheet's points are listed on the line, never summed as feet");
check(G[10].n === 1 && G[10].byUnit.ft === undefined && G[10].other.LF === 220 && G[10].members[0].sheetOk === false, "a slab family in feet is listed on Slab Prep but kept out of the SF sum");
check(G[DEF.length].bucket === "Not on the bid sheet" && G[DEF.length].members[0].label === "Over-excavation", "the unmatched row is in the leftovers, never dropped");
// ---- the screen files exactly what the CSV files (audit 2026-09-26: D02, D03, D04, D17, 7.4) ----
// The same approved-row cases tests/test_bidsheet.py runs through
// bidsheet.build, mapped to the rows renderBidView hands bsGroupRows, must
// give the same line totals: one number on screen and in the file.
const RC = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "bidsheet_rows.json"), "utf8")).cases;
const toRows = (items) => items.map((it) => it.unit
  ? { label: it.label, unit: it.unit, qty: it.qty, cnt: 1, sids: [], isCalc: true, kind: it.kind,
      run: it.run_sid, runOf: it.run_of, utility: it.utility }
  : { label: it.label, unit: "ft", qty: it.length_ft, cnt: it.count,
      sids: Object.keys(it.by_style || {}).map(Number), bySid: it.by_style || null });
const lineTotal = (B) => {
  let t = 0;
  for (const k of Object.keys(B.byUnit)) t += B.byUnit[k];
  for (const kind of Object.keys(B.cf)) for (const k of Object.keys(B.cf[kind])) t += B.cf[kind][k];
  return Math.round(t * 10) / 10;
};
for (const c of RC) {
  const g = H.bsGroupRows(toRows(c.items), DEF);
  const got = {}, other = {};
  for (const B of g) {
    if (!B.line) continue;
    const t = lineTotal(B);
    if (B.n && t) got[String(B.line.n)] = t;
    if (Object.keys(B.other).length) other[String(B.line.n)] = B.other;
  }
  const un = g[g.length - 1].members.map((m) => m.label);
  check(JSON.stringify(got) === JSON.stringify(c.lines) && JSON.stringify(other) === JSON.stringify(c.other) && JSON.stringify(un) === JSON.stringify(c.unmatched),
    "screen = CSV: " + c.name + " (got " + JSON.stringify(got) + " other " + JSON.stringify(other) + " unmatched " + JSON.stringify(un) + ")");
}
check(H.bidCfKind("Site Cut") === "cut" && H.bidCfKind("Fill (earthwork)") === "fill" && H.bidCfKind("Export Surplus Cut") === "export" && H.bidCfKind("Import Structural") === "import" && H.bidCfKind("Strip Topsoil") === null,
  "bidCfKind names the cut / fill / net rows and nothing else");
{
  const cg = DEF.concat([{ n: 30, name: "Curb & Gutter", unit: "LF", match: [], not: [] }]);
  check(H.bsLineFor("CURB AND GUTTER", null, cg).index === 29 && H.bsLineFor("24\" C&G", null, cg).index === 29
        && H.bsLineFor("ROOF GUTTER", null, cg).index === 20 && H.bsLineFor("CURB AND GUTTER", null, DEF).index === -1,
    "curb and gutter: a line named curb, else not on the sheet; a roof gutter stays on Site Drains");
}
check(H.bidCfKind("Export Asphalt") === null && H.bidCfKind("Export Cut") === "export" && H.bidCfKind("Export Surplus") === "export",
  "a layer's own export row (Export Asphalt) is not the cut / fill net on the heading chip");
{
  // a seat that named the layer on its own line gets the row there; a bare EXPORT or ASPHALT does not take it
  const own = DEF.concat([{ n: 30, name: "Paving", unit: "SF", match: ["ASPHALT"], not: [] },
                          { n: 31, name: "Asphalt haul", unit: "CY", match: ["EXPORT ASPHALT"], not: [] },
                          { n: 32, name: "Asphalt removal", unit: "CY", match: ["REMOVE ASPHALT"], not: [] }]);
  check(H.bsLineFor("Export Asphalt", null, own).index === 30 && H.bsLineFor("Remove Asphalt", null, own).index === 31
        && H.bsLineFor("Removal of asphalt", null, own).index === 31 && H.bidCfKind("Export cut (net)") === "export"
        && H.bsLineFor("Rough Place Asphalt", null, own).index === -1 && H.bsLineFor("Asphalt paving", null, own).index === 29,
    "a layer row files only on a line whose token names the layer and its verb");
}
const sew = H.bsGroupRows(toRows(RC[4].items), DEF)[14];
check(sew.members.some((m) => m.label === "SE-P-UT-SS" && m.counted === 0 && m.replaced === 264.8 && m.runs === 1) && sew.members.some((m) => m.kind === "trench_run" && m.utility === "sewer"),
  "the replaced family row says so on screen (counted 0, replaced 264.8, one run) and the run row carries its utility");
check(/replaced by its run row/.test(bv) && /not run counts/.test(bv) && /m\.utility && \(m\.kind \|\| ""\)\.indexOf\("trench"\) === 0/.test(bv),
  "renderBidView prints the replacement note and the run's utility on the member row");
check(/const chipText = \(B\) => \{/.test(bv) && /for \(const k of BS_CF_ORDER\)/.test(bv) && !/chipText\(B\.byUnit\)/.test(bv),
  "the heading chip is built per cut / fill / net kind, never from one per-unit sum");
// ---- styles, busters ------------------------------------------------------------------
check(/\.sv-bs input\.sv-bs-match \{/.test(css) && /\.bid-bucket\.bid-empty summary \{ opacity: \.55; \}/.test(css) && /\.bm-nounit \{/.test(css), "editor, empty-line and not-summed styles exist");
check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 187 && +((/style\.css\?v=(\d+)/.exec(html) || [])[1]) >= 92, "cache busters moved");
if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all bidsheet-ui checks passed");
