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
check(/\/api\/job\/\$\{state\.job\}\/bidsheet\.csv/.test(fnSrc("bsCsvUrl")) && /encodeURIComponent\(m\[1\] \+ ":" \+ m\[2\]\)/.test(fnSrc("bsCsvUrl")), "the link carries the cross-sheet unticks as page:label");
check(/renderXs\(\);\n        bsLinkSync\(\);/.test(js), "an untick on the cross-sheet panel refreshes the link");
// ---- the matcher: same answers as the Python original ----------------------------------
const H = new Function("store", fnSrc("bidNormText") + "\n" + fnSrc("bidTokenHit") + "\n" + fnSrc("bidSizeToken") + "\nconst BS_LABEL_ONLY = [\"W\", \"G\", \"E\"];\n"
  + fnSrc("bsLineFor") + "\n" + fnSrc("bsGroupRows") + "\nreturn { bsLineFor, bsGroupRows };")({});
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
check(G.length === DEF.length + 1 && G[1].byUnit.SF === 1000 && G[20].byUnit.ft === 400, "one group per line in order, plus the leftovers");
check(G[10].n === 1 && G[10].byUnit.ft === undefined && G[10].other.LF === 220 && G[10].members[0].sheetOk === false, "a slab family in feet is listed on Slab Prep but kept out of the SF sum");
check(G[DEF.length].bucket === "Not on the bid sheet" && G[DEF.length].members[0].label === "Over-excavation", "the unmatched row is in the leftovers, never dropped");
// ---- styles, busters ------------------------------------------------------------------
check(/\.sv-bs input\.sv-bs-match \{/.test(css) && /\.bid-bucket\.bid-empty summary \{ opacity: \.55; \}/.test(css) && /\.bm-nounit \{/.test(css), "editor, empty-line and not-summed styles exist");
check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 187 && +((/style\.css\?v=(\d+)/.exec(html) || [])[1]) >= 92, "cache busters moved");
if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all bidsheet-ui checks passed");
