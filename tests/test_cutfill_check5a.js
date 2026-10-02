// CHECK 5A ON THE CUT / FILL SCREENS (2026-09-27, VERIFY-5A.md "What is still wrong"): the screen
// parts of the fixes, pinned on the functions themselves (pulled out of the page scripts and run
// against stand-in elements) and, where a function needs the whole page, on its source.
//
// run: node tests/test_cutfill_check5a.js
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

// ---- 1. the rows that left the takeoff are said on the Cut / fill bar (V2) ------------------------
// An input change with the rows in the takeoff takes them out; the sentence used to go to the host's
// status line only, under the stage. The bar says it beside STALE and the button now.
const barSrc = [constSrc(js, "CF_ROW_RE"), fnSrc(js, "cfIsRow"), fnSrc(js, "cfAdded"), fnSrc(js, "cfRowsHere"), fnSrc(js, "cfRowsElsewhere"),
  fnSrc(js, "cfRows"), fnSrc(js, "cfScaleTxt"), fnSrc(js, "cfScaleNote"), fnSrc(js, "cfSentence"), fnSrc(js, "cfRenderBar")].join("\n");
function bar(summary, opts) {
  const o = opts || {};
  const els = { cfSum: { textContent: "" }, cfNote: { textContent: "", title: "" }, cfAdd: { textContent: "", title: "old title", disabled: false } };
  const state = { extraItems: o.items || [], result: o.noDetect ? null : {}, page: o.page || 0 };
  const cfState = { summary, rowsPage: o.rowsPage === undefined ? 0 : o.rowsPage, hint: o.hint || "" };
  const run = new Function("$", "state", "cfState", "CF_ADD_TITLE", barSrc + "\ncfRenderBar();");
  run((id) => els[id], state, cfState, "the default tooltip");
  return { sum: els.cfSum.textContent, note: els.cfNote.textContent, noteTitle: els.cfNote.title, add: els.cfAdd };
}
const vol = { cut_cy: 40363, fill_cy: 49640, net_cy: 9277, area_sf: 721815, scale_ft_per_in: 50, scale_source: "read in review", warnings: [], questions: [] };
const rowsIn = [{ label: "Site Cut", qty: 40363, unit: "CY" }, { label: "Site Fill", qty: 49640, unit: "CY" }];
let b = bar(Object.assign({}, vol, { stale: true }));
check(/STALE — its rows left the takeoff: Compute puts them back/.test(b.sum) && b.add.disabled && /left this page's takeoff/.test(b.add.title),
  "stale with this page's rows taken out: the bar says they left and Compute puts them back, beside STALE; the button says why: " + b.sum);
b = bar(Object.assign({}, vol, { stale: true }), { items: rowsIn });
check(/STALE — press Compute again/.test(b.sum) && !/left the takeoff/.test(b.sum), "stale with the rows still in: the plain STALE words");
b = bar(Object.assign({}, vol, { stale: true }), { rowsPage: 1 });
check(/STALE — press Compute again/.test(b.sum) && b.add.textContent === "In the takeoff on page 2",
  "stale with the rows on another page: nothing left THIS page, the button still names their page");
b = bar(Object.assign({}, vol), { items: rowsIn });
check(!/STALE/.test(b.sum) && b.add.title === "the default tooltip" && b.add.textContent === "Remove from takeoff",
  "a fresh result with the rows in: no STALE, the button's own tooltip is back (not a leftover one)");
b = bar({ error: "Not enough evidence: need contours or spot grades for both existing and proposed.", warnings: [] }, { hint: "a hint" });
check(/out of the takeoff until a compute goes through/.test(b.sum) && b.note === "a hint",
  "refused with the rows taken out: said on the bar in full (the 40-character note used to cut it); the note keeps the hint");
b = bar(null, { rowsPage: null });
check(!/out of the takeoff/.test(b.sum) && b.add.title === "the default tooltip", "no result and no rows anywhere: nothing about rows");
check(/the cut \/ fill rows left the takeoff \(an input changed\)/.test(js), "the host's own status line keeps its sentence too");
const html = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 229, "the host page's cache buster moved (app.js)");
// check 6A (D4): the note beside the sentence was cut when the bar was long - "1" = 40.02 ft (read in review) · 2 war..."
// (max-width 40ch, ellipsis, one line). It wraps now, and its whole text is the first line of its tooltip too.
const css = fs.readFileSync(path.join(__dirname, "..", "webui", "style.css"), "utf8");
const noteRule = (/\n\.cf-note \{[^}]*\}/.exec(css) || [""])[0];
check(noteRule && !/text-overflow:\s*ellipsis|white-space:\s*nowrap|overflow:\s*hidden/.test(noteRule) && /white-space:\s*normal/.test(noteRule),
  "the bar's note wraps and is never cut (no ellipsis, no single line, nothing hidden): " + noteRule.trim());
check(/\n\.cf-bar > button, \.cf-title \{ flex-shrink: 0; white-space: nowrap; \}/.test(css), "the bar's buttons and title keep one line while the note wraps beside them");
const longVol = Object.assign({}, vol, { scale_ft_per_in: 40.016655966932305, coverage: 0.82,
  warnings: ["Contours and spot grades disagree by 1.66 ft at 1 spot.", "18 % of the limit has no surface."], questions: ["Printed 5991 sits beside a proposed line that has no height. Is it that line's height?"], questions_total: 148 });
b = bar(longVol, { items: rowsIn, hint: "this sheet reads both surfaces" });
check(b.note === '1" = 40.02 ft (read in review) · surfaces cover 82 % of the limit · 2 warnings · 148 open questions · this sheet reads both surfaces'
  && b.noteTitle.split("\n")[0] === b.note && b.noteTitle.split("\n").length === 5 && /\n\? and 147 more in the Cut \/ fill panel$/.test(b.noteTitle),
  "a long note (the scale, the cover, the counts, the hint): the whole of it is the tooltip's first line, then each warning and question: " + b.noteTitle.split("\n").slice(0, 2).join(" / "));
b = bar({ no_volumes: true, scale_ft_per_in: 40.016655966932305, scale_source: "read in review", warnings: ["the limit crosses itself: repaired"],
  rows: [{ label: "Clear & Grub", qty: 468258.7, unit: "SF" }] }, { rowsPage: null });
check(b.noteTitle === '1" = 40.02 ft (read in review)\nthe limit crosses itself: repaired', "before any compute too: the note, then the warnings: " + JSON.stringify(b.noteTitle));
b = bar({ error: "Not enough evidence: need contours or spot grades for both existing and proposed.", warnings: [] }, { hint: "a hint" });
check(b.note === "a hint" && b.noteTitle === "a hint", "a refusal: the note is the hint, and so is its tooltip");
check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 233 && +((/style\.css\?v=(\d+)/.exec(html) || [])[1]) >= 110, "the host page's cache busters moved (app.js, style.css)");

// ---- 2. the Limit card shows the area the volumes use (V4) --------------------------------------
// A limit that crosses itself is repaired for the volumes and the rows (takeoff.ring_metrics); the
// card measured the ring as drawn in the browser (441,690 SF beside 468,259). It shows the server's
// measure now, and says the ring was repaired.
const limSrc = [constSrc(ejs, "nf0"), constSrc(ejs, "isNum"), fnSrc(ejs, "unNeg"), constSrc(ejs, "fmt0"), fnSrc(ejs, "scaleRefused"), fnSrc(ejs, "scaleFtPerPt"), fnSrc(ejs, "noScaleWords"),
  fnSrc(ejs, "polyArea"), fnSrc(ejs, "polyLen"), fnSrc(ejs, "displayKey"), fnSrc(ejs, "takeoffKey"), fnSrc(ejs, "renderLimit")].join("\n");
function limitCard(st, opts) {
  const o = opts || {};
  const els = { limitInfo: { textContent: "" }, btnLimit: { classList: { toggle() {} } }, btnLimitPick: { classList: { toggle() {} } } };
  const env = new Function("$", "state", "sheetName", limSrc + "\nreturn { renderLimit, takeoffKey };")((id) => els[id], st, (k) => "sheet " + k);
  if (o.fresh) st.takeoffKey = env.takeoffKey();
  env.renderLimit();
  return els.limitInfo.textContent;
}
const bow = [[0, 0], [400, 400], [400, 0], [0, 400]];          // crosses itself: the shoelace says 0
const proj = (region, scale) => ({ settings: { region, scale_ft_per_in: scale, display_page: "d0p0", limit: { source: "drawn" } } });
let st = { key: "d0p0", tool: null, limitPts: [], analyzes: {}, result: null, project: proj(bow, 72),
  takeoff: { rows: [], limit: { area_sf: 80000, perim_ft: 1931.4, repaired: true, n: 4, source: "drawn" } } };
let txt = limitCard(st, { fresh: true });
check(/80[,.\s]?000 SF inside \(it crosses itself: repaired, the area the volumes use\), 1[,.\s]?931 ft around/.test(txt),
  "a ring that crosses itself: the card shows the server's (repaired) area and says so: " + txt);
st.project = proj(bow, 50);                                    // the scale moved since the rows were measured
txt = limitCard(st);
check(/measuring/.test(txt) && !/80[,.\s]?000/.test(txt), "a measure made at another scale is not shown as the limit's area: " + txt);
st.takeoff = { rows: [], notes: ["No scale: type the sheet scale or confirm a printed one."] };
txt = limitCard(st, { fresh: true });
check(/set the scale for the area/.test(txt), "the server could not measure it (no scale in force there): said so, no browser guess");
st.takeoff.limit = { area_sf: 160000, perim_ft: 1600, repaired: false, n: 4 };
st.project = proj([[100, 100], [500, 100], [500, 500], [100, 500]], 72);
txt = limitCard(st, { fresh: true });
check(/160[,.\s]?000 SF inside, 1[,.\s]?600 ft around/.test(txt) && !/repaired/.test(txt), "a plain ring: its area, no word about a repair: " + txt);
st.tool = "limit"; st.limitPts = [[0, 0], [100, 0], [100, 100], [0, 100]];
txt = limitCard(st);
check(/4 corners · 10[,.\s]?000 SF inside/.test(txt), "a ring still being drawn is measured as drawn: " + txt);
const refresh = fnSrc(ejs, "refreshTakeoff");
check(/state\.takeoffKey = key/.test(refresh) && /renderLimit\(\)/.test(refresh), "the rows' answer records what it measured and redraws the Limit card");
check(/refreshTakeoff\(\)/.test(fnSrc(ejs, "typeScale")) && /refreshTakeoff\(\)/.test(fnSrc(ejs, "chooseScale")),
  "a typed or confirmed scale measures the limit and its rows again");
const showBlock = ejs.slice(ejs.indexOf("e.data.type === 'show'"), ejs.indexOf("e.data.type === 'show'") + 900);
check(/refreshTakeoff\(\)/.test(showBlock), "the host's open (which can hand over another scale) measures them again too");
const ehtml = fs.readFileSync(path.join(__dirname, "..", "webui", "cutfill", "index.html"), "utf8");
check(+((/ui\/app\.js\?v=(\d+)/.exec(ehtml) || [])[1]) >= 31, "the engine page's cache buster moved (ui/app.js)");

// ---- 3. "Where the volume sits": a share of the total is never over 100 % (V15 e) ------------------
// The areas count each cell by its share inside the limit on the server now (project._hotspots,
// tests/test_cutfill_check5a.py); the card also caps the share for a result stored before that.
check(/Math\.min\(100, Math\.round\(100 \* h\.volume_cy \/ total\)\)/.test(fnSrc(ejs, "renderResult")), "the card's share is capped at 100 %");
check(+((/ui\/app\.js\?v=(\d+)/.exec(ehtml) || [])[1]) >= 32, "and the engine page's cache buster moved again");

// ---- 5. every print of a scale has two decimals at most (V13) ------------------------------------
const bar15 = 40.016655966932305;
b = bar({ no_volumes: true, scale_ft_per_in: bar15, scale_source: "read in review", warnings: [],
  rows: [{ label: "Clear & Grub", qty: 468258.7, unit: "SF" }] }, { rowsPage: null });
check(b.note === '1" = 40.02 ft (read in review)', "before any compute (the limit's rows only) the bar prints the scale with two decimals and its source: " + b.note);
b = bar(Object.assign({}, vol, { scale_ft_per_in: bar15 }), { items: rowsIn });
check(b.note.indexOf('1" = 40.02 ft (read in review)') === 0, "after a compute too: " + b.note);
b = bar({ no_volumes: true, scale_ft_per_in: null, warnings: [], rows: [] }, { rowsPage: null });
check(b.note === "scale not set in the panel", "no scale: said so");
for (const [name, src] of [["the host's cut / fill block", js.slice(js.indexOf("// =============== CUT / FILL"), js.indexOf("// =============== end CUT / FILL"))], ["the engine page", ejs]])
  check(!/\$\{[\w.]*ft_per_in\}/.test(src), name + " prints no raw scale number (every one goes through the formatter)");
// the engine's Scale box: a value, shown the way every print is; the number in force is not changed
const scaleSrc = [fnSrc(ejs, "fmtScale"), fnSrc(ejs, "fitScaleBox"), fnSrc(ejs, "scaleRefused"), fnSrc(ejs, "renderScale")].join("\n");
function scaleBox(settings, an) {
  const els = { scaleList: { innerHTML: "", append() {} }, scaleIn: { value: "", style: {} }, scaleStatus: { textContent: "", className: "", title: "" } };
  // the sheet on screen is the sheet shown here: no sheet's own review scale is shown (round 16's ownSheetScale)
  new Function("$", "state", "el", "document", "shortName", "chooseScale", "ownSheetScale", "sheetName", "shownKey", scaleSrc + "\nrenderScale();")(
    (id) => els[id], { project: { settings }, analyzes: { d0p0: an }, key: "d0p0" }, () => ({}), { activeElement: null }, (s) => s, () => {},
    () => null, (k) => k, () => "d0p0");
  return els;
}
let sb = scaleBox({ scale_ft_per_in: bar15, scale_source: "read in review" }, null);
check(sb.scaleIn.value === "40.02" && sb.scaleStatus.textContent === "read in review", "the Scale box shows 40.02 for a scale in force of 40.0166559669...: " + sb.scaleIn.value);
sb = scaleBox({ scale_ft_per_in: null }, { scale_candidates: [], scale_default: { ft_per_in: bar15, text: "graphic bar" } });
check(sb.scaleIn.value === "40.02", "an unconfirmed printed default shows the same way");
// check 6A (D5): the box was 56 px and showed "40.0" and half a digit; it is as wide as its number now
const boxCh = (v) => +((/calc\((\d+)ch/.exec(v.style.width || "") || [])[1]);
check(boxCh(sb.scaleIn) >= sb.scaleIn.value.length + 1, "the Scale box is as wide as 40.02 and a digit more: " + sb.scaleIn.style.width);
check(/typeScale/.test(ejs) && /\$\('scaleIn'\)\.addEventListener\('change', typeScale\)/.test(ejs), "only a typed change saves the box (the number in force is kept otherwise)");
check(+((/ui\/app\.js\?v=(\d+)/.exec(ehtml) || [])[1]) >= 33 && +((/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 230, "both cache busters moved");

// ---- 6. the words (V15 c, V6, V15 e, V15 f, V15 d) -----------------------------------------------
// the bar quotes the engine's first sentence with ONE period ("again.. (the panel says why)")
b = bar({ error: "Lines were erased or named in review after sheet 3.1 was read here: press Update on it in the Sheets card, then compute again.", warnings: [] }, { rowsPage: null });
check(/then compute again\. \(the panel says why\)$/.test(b.sum) && !/\.\./.test(b.sum), "a one-sentence refusal keeps one period: " + b.sum);
b = bar({ error: "Not enough evidence: need contours or spot grades for both existing and proposed.", warnings: [] }, { rowsPage: null });
check(/and proposed\. \(the panel says why\)$/.test(b.sum) && !/\.\./.test(b.sum), "and so does this one (it printed \"proposed.. (\"): " + b.sum);
b = bar({ error: "the scale in force came from another sheet. Confirm this sheet's scale in the Scale card (volumes move with the square of the scale).", warnings: [] }, { rowsPage: null });
check(/another sheet\. \(the panel says why\)$/.test(b.sum), "a longer message: its first sentence, one period: " + b.sum);
check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 231, "the host page's cache buster moved (app.js)");
// a zero prints as 0 ("Cut -0.0 CY": a sum over no cut cells is -0.0)
const fmts = new Function([constSrc(ejs, "nf1"), constSrc(ejs, "nf0"), constSrc(ejs, "isNum"), fnSrc(ejs, "unNeg"),
  constSrc(ejs, "fmt1"), constSrc(ejs, "fmt0")].join("\n") + "\nreturn { fmt1, fmt0 };")();
check(fmts.fmt1(-0) === "0.0" && fmts.fmt0(-0) === "0" && fmts.fmt1(-0.04) === "0.0" && fmts.fmt0(-0.4) === "0",
  `a negative zero, or a small negative that rounds to zero, prints as 0: ${fmts.fmt1(-0)} / ${fmts.fmt0(-0)} / ${fmts.fmt1(-0.04)}`);
check(/^-1.234\.5$/.test(fmts.fmt1(-1234.5).replace(/−/, "-")) && fmts.fmt1(0) === "0.0" && fmts.fmt0(13027.8).replace(/\D/g, "") === "13028",
  "a real negative keeps its sign; other numbers print as before: " + fmts.fmt1(-1234.5));
// "What changed": one piece is / is kept, several are
function changes(ra) {
  const items = [];
  const node = (tag, attrs, ...kids) => ({ text: kids.flat(Infinity).filter((k) => k != null && k !== false).map((k) => (typeof k === "object" ? k.text : String(k))).join("") });
  const box = { innerHTML: "", append(n) { items.push(n.text); } };
  const st = { key: "d0p0", analyzes: { d0p0: { changes: { records: {}, records_before: {}, heights: {}, labels: {}, families: [] }, review: "sig", review_applied: ra } } };
  new Function("$", "state", "el", [constSrc(ejs, "nf0"), constSrc(ejs, "isNum"), fnSrc(ejs, "unNeg"), constSrc(ejs, "fmt0"), constSrc(ejs, "fmtZ"),
    fnSrc(ejs, "renderChanges")].join("\n") + "\nrenderChanges();")((id) => box, st, node);
  return items.join(" | ");
}
let ctext = changes({ erased: 1, erased_pt: 12, held: 1, named: {} });
check(/1 piece on lines erased there is left out/.test(ctext) && /1 piece erased there is kept: you set a height or a surface on it here/.test(ctext),
  "one piece: is left out, is kept, on it (it said \"1 erased there are kept\"): " + ctext);
ctext = changes({ erased: 3, erased_pt: 90, held: 2, named: {} });
check(/3 pieces on lines erased there are left out/.test(ctext) && /2 pieces erased there are kept: you set a height or a surface on them here/.test(ctext),
  "several: are left out, are kept, on them: " + ctext);
// a refused compute: the Takeoff rows card never lists the last result's rows beside the refusal
function rowsCard(result, stale) {
  const node = (tag, attrs, ...kids) => {
    const n = { tag, texts: [], append(...more) { for (const k of more.flat(Infinity)) if (k != null && k !== false) n.texts.push(typeof k === "object" ? k.text : String(k)); } };
    n.append(kids);
    Object.defineProperty(n, "text", { get: () => n.texts.join(" ") });
    return n;
  };
  const box = node("div");
  const els = { takeoffBox: box, crewMapLink: { hidden: true }, crewMapBlockLab: { hidden: true }, crewMapBlock: { value: 30 } };
  const st = { pid: "p", stale: !!stale, result, takeoff: { computed: true, basis: "neat line", notes: [], zones_total: { n: 1, cut_cy: 5, fill_cy: 6, area_sf: 70 },
    rows: [{ label: "Silt / Construction Fence", qty: 7399, unit: "LF", kind: "fence" }, { label: "Clear & Grub", qty: 468259, unit: "SF", kind: "clear" },
      { label: "Site Cut", qty: 39626, unit: "CY", kind: "cut" }, { label: "Site Fill", qty: 49640, unit: "CY", kind: "fill" },
      { label: "Import Structural", qty: 10014, unit: "CY", kind: "import" }, { label: "Site walls face", qty: 1200, unit: "SF", kind: "walls" }] } };
  new Function("$", "state", "el", "BASE", "fillTakeoffInputs", [constSrc(ejs, "RESULT_ROWS"), constSrc(ejs, "nf0"), constSrc(ejs, "isNum"), fnSrc(ejs, "unNeg"),
    constSrc(ejs, "fmt0"), fnSrc(ejs, "renderTakeoff")].join("\n") + "\nrenderTakeoff();")((id) => els[id], st, node, "", () => {});
  return box.text;
}
let card = rowsCard({ error: "Lines were erased or named in review after sheet 3.1 was read here: press Update on it in the Sheets card, then compute again." });
check(!/Site Cut|Site Fill|Import Structural|Site walls face|zones:/.test(card) && /Clear & Grub/.test(card) && /Silt \/ Construction Fence/.test(card)
  && /The last compute was refused \(the Result card says why\): no cut \/ fill rows until one goes through\./.test(card),
  "refused: the volume rows (39,626 / 10,014 CY), the walls and the zones leave the card, the limit's rows stay and it says why: " + card);
card = rowsCard({ totals: { cut_cy: 39626, fill_cy: 49640 }, grid: null });
check(/Site Cut/.test(card) && /Import Structural/.test(card) && /zones: 1/.test(card) && !/refused/.test(card), "a result that stands: its rows are listed as before");
check(/if \(r\.error\) renderTakeoff\(\)/.test(fnSrc(ejs, "runCompute")), "a refusal redraws the card at once (the server's rows come back after)");
check(+((/ui\/app\.js\?v=(\d+)/.exec(ehtml) || [])[1]) >= 35, "the engine page's cache buster moved (ui/app.js)");

console.log(fails ? "\n" + fails + " failure(s)" : "\nall ok");
process.exit(fails ? 1 : 0);
