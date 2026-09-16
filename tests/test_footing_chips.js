// FOOTING-MARK CHIPS in the structure panel (2026-09-16; Wave 2 item 1, UI
// half). The panel fetches /footings for the sheet, shows each mark with its
// count, schedule dims and bottom-of-footing elevations, and a click sets the
// draft's footing width and depth - the typed number still wins, and a mark
// without a schedule row is drawn as a guess.
//
// run: node tests/test_footing_chips.js   (fails on f178176)
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
check(!!fnSrc("stFootingsBlock") && !!fnSrc("stFetchFootings") && !!fnSrc("stApplyFooting"), "the three functions exist");
check(!!fnSrc("stMk") && !/[^t]mk\(/.test(fnSrc("stFootingsBlock")) && /document\.createElement\(tag\)/.test(fnSrc("stMk")), "the block builds elements with its own stMk (mk is a per-function local elsewhere - the headed run threw 'mk is not defined')");
check(/body\.appendChild\(stFootingsBlock\(st\)\);/.test(fnSrc("renderSt")), "renderSt places the block under the outline buttons");
check(/fetch\(`\/api\/job\/\$\{state\.job\}\/page\/\$\{state\.page\}\/footings`\)/.test(fnSrc("stFetchFootings")), "it fetches the page's footings route");
check(/if \(gen !== state\.gen \|\| state\.st !== st\) return;/.test(fnSrc("stFetchFootings")), "a page switch during the fetch drops the answer");

// a DOM-free harness around the block and the click
const calls = { status: [], rendered: 0 };
const H = new Function("state", "calls",
  "const $ = () => ({ textContent: '' }); const setStatus = (m, k) => calls.status.push([m, k || '']);\n"
  + "const renderSt = () => { calls.rendered++; }; const stResultText = () => ''; const stCalc = () => ({});\n"
  + "const stSync = () => state.st; const stFetchFootings = () => {}; const stGroupsTable = () => stMk('div', 'st-groups');\n"
  + "function stMk(tag, cls, text) { const el = { tag, className: cls || '', textContent: text || '', children: [], classList: { add(c) { el.className += ' ' + c; } }, appendChild(c) { el.children.push(c); return c; } }; return el; }\n"
  + fnSrc("stFt") + "\n" + fnSrc("stFootingsBlock") + "\n" + fnSrc("stApplyFooting")
  + "\nreturn { stFootingsBlock, stApplyFooting };")(null, calls);
const state = { job: "j", page: 3, gen: 1, st: { draft: null, footings: null } };
const H2 = new Function("state", "calls",
  "const $ = () => ({ textContent: '' }); const setStatus = (m, k) => calls.status.push([m, k || '']);\n"
  + "const renderSt = () => { calls.rendered++; }; const stResultText = () => ''; const stCalc = () => ({});\n"
  + "const stSync = () => state.st; const stFetchFootings = () => { calls.fetched = (calls.fetched || 0) + 1; }; const stGroupsTable = () => stMk('div', 'st-groups');\n"
  + "function stMk(tag, cls, text) { const el = { tag, className: cls || '', textContent: text || '', title: '', children: [], classList: { add(c) { el.className += ' ' + c; } }, appendChild(c) { el.children.push(c); return c; } }; return el; }\n"
  + fnSrc("stFt") + "\n" + fnSrc("stFootingsBlock") + "\n" + fnSrc("stApplyFooting")
  + "\nreturn { stFootingsBlock, stApplyFooting };")(state, calls);
let b = H2.stFootingsBlock(state.st);
check(calls.fetched === 1 && state.st.footings.loading && /reading the footing marks/.test(b.children[0].textContent), "first render starts the fetch and says so");
state.st.footings = { job: "j", page: 3, loading: false, err: null, data: {
  by_mark: { "CF2.0": { count: 14, unpaired: 1, bof: { "86": 3, "84.1667": 3 }, schedule: { from: "schedule", kind: "continuous", width_ft: 2, depth_ft: 1, page: 11, reinf: "(2)#5 BOTTOM #5@16\"" } },
             "F5.5": { count: 5, unpaired: 0, bof: { "96": 3 }, schedule: { from: "mark digits", kind: "spread", width_ft: 5.5, length_ft: 5.5, depth_ft: null } } },
  slabs: [{ text: "(87'-0\")", elev_ft: 87 }], schedule: { pages: [11], rows: 17 } } };
b = H2.stFootingsBlock(state.st);
const chips = b.children.find(c => c.className === "st-chips").children;
check(chips.length === 2 && chips[0].textContent.startsWith("CF2.0 ×14 · 2 ft wide × 1 ft deep · BOF"), "the busiest mark first, with its schedule width and depth and the bottom-of-footing elevations (" + chips[0].textContent + ")");
check(/86′ ×3/.test(chips[0].textContent) && /from the footing schedule \(page 12\)/.test(chips[0].title), "elevations carry their counts; the tooltip names the schedule page");
check(/st-chip-guess/.test(chips[1].className) && /5\.5 ft × 5\.5 ft/.test(chips[1].textContent) && /no schedule row/.test(chips[1].title), "a mark without a schedule row is a dashed guess chip from its digits");
check(/schedule read from page 12/.test(b.children[0].textContent) && b.children.some(c => /slab elevations printed: \(87'-0"\)/.test(c.textContent)), "the head names the schedule page; the slab elevations are listed");
// the click
H2.stApplyFooting("CF2.0", state.st.footings.data.by_mark["CF2.0"].schedule);
check(calls.status[calls.status.length - 1][1] === "err" && /outline first/.test(calls.status[calls.status.length - 1][0]), "no draft: the click says to outline first");
state.st.draft = { footing_w_ft: 2, footing_ft: 0, depth_ft: 8 };
H2.stApplyFooting("CF3.0", { from: "schedule", width_ft: 3, depth_ft: 1 });
check(state.st.draft.footing_w_ft === 3 && state.st.draft.footing_ft === 1 && state.st.draft.footing_mark === "CF3.0" && calls.rendered >= 1, "a click sets the draft's footing width and depth from the schedule row");
check(/3 ft wide, 1 ft below the slab \(from the footing schedule\)/.test(calls.status[calls.status.length - 1][0]), "and says where the numbers came from");
H2.stApplyFooting("F-24", { from: "mark digits", width_ft: 2, depth_ft: null });
check(state.st.draft.footing_w_ft === 2 && state.st.draft.footing_ft === 1 && /depth not in the schedule/.test(calls.status[calls.status.length - 1][0]), "a digits-only mark sets the width, keeps the depth, and asks for it");
check(/\.st-chip\.st-chip-guess \{ border-style: dashed; color: var\(--t-warn\); \}/.test(css), "guess chips are dashed and warning-coloured");
check(+((/style\.css\?v=(\d+)/.exec(html) || [])[1]) >= 88 && +((/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 183, "cache busters moved");

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all footing-chip checks passed");
