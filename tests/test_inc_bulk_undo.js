// ALL / NONE / ENGINE'S PICKS / UNDO, SINCE THE TAKEOFF STARTS EMPTY (2026-10-03; the buttons' way back is UI verify
// 2026-10-02 #7). Pins the INC-BULK block of webui/app.js:
//   * after Detect nothing is in the takeoff: loadResult seeds every tick OFF (the engine's pick is no longer a tick);
//   * "Engine's picks" ADDS the engine's picks (includeSeed) to what the person added - it takes nothing out - and is
//     offered while one of them is not in; "all" adds every style; "none" empties the takeoff;
//   * "undo" puts back the exact takeoff of just before the last of those, ticks and the added set both, and sends it;
//   * every change goes through setInclude, so the server's record follows (tkDirty).
//
// run: node tests/test_inc_bulk_undo.js        (fails on e09a605: the seed was the takeoff, picks replaced it)
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };

const a = js.indexOf("// INC-BULK-BEGIN"), b = js.indexOf("// INC-BULK-END");
check(a > 0 && b > a, "INC-BULK block present");

// a page: five styles and the engine's pick for each
const styles = [
  { style_id: 1, class: "dashed", color: [0, 0, 0], length_pt: 900, layer: "C-STRM" },  // pick
  { style_id: 2, class: "solid", color: [0, 0, 0], length_pt: 200, layer: "C-TOPO" },    // short solid: not a pick
  { style_id: 3, class: "solid", color: [0, 0, 0], length_pt: 9000, layer: "C-WATR" },   // pick
  { style_id: 4, class: "annotation", color: [0, 0, 0], length_pt: 900, layer: "C-ANNO" }, // not
  { style_id: 5, class: "dashed", color: [1, 1, 1], length_pt: 900, layer: "C-EX" },     // white: not
];
const els = {};
const $ = id => (els[id] = els[id] || { hidden: true, onclick: null });
const state = { job: "j1", page: 0, result: { styles }, include: new Map(), inheritedInc: new Map(), incUndo: null,
                tkSet: new Set(), labels: new Map([[1, "STORM PIPE"]]) };
let status = "", dirty = 0, follows = 0, scoped = 0;
const env = {
  state, $,
  sgSuggested: () => false,
  // the scope filter's verdicts are worked out again after a bulk button and after undo (critic empty-2, MINOR-1; the
  // filter itself runs for real in tests/test_scope_untick_final.js)
  scopeApply: () => { scoped++; },
  // the real setInclude's contract: the tick, and the set the person added (the server's record)
  setInclude: (sid, on) => { state.include.set(sid, !!on); state.inheritedInc.delete(sid);
    if (on) state.tkSet.add(sid); else state.tkSet.delete(sid); dirty++; },
  tkDirty: () => { dirty++; },
  tkFollow: () => { follows++; },   // the scope sentence and the check queue follow the takeoff (critic empty-1, MINOR-4 / 5)
  renderStyles() {}, renderChips() {}, renderItemized() {}, renderMiniLegend() {}, draw() {},
  setStatus: m => { status = m; },
};
const F = new Function(...Object.keys(env), js.slice(a, b)
  + "\nreturn { includeSeed, incSnapshot, incRestore, renderIncBtns, incSeedDiffers };")(...Object.values(env));
const on = () => styles.filter(s => state.include.get(s.style_id)).map(s => s.style_id).join(",");
const added = () => [...state.tkSet].sort().join(",");

// as loadResult leaves a fresh page: every tick off
for (const s of styles) state.include.set(s.style_id, false);
check(styles.map(s => F.includeSeed(s)).join() === "true,false,true,false,false",
  "the engine's picks: long dashed, long solid; not the short solid, the annotation or the white");
F.renderIncBtns();
check(on() === "" && $("incUndoBtn").hidden === true && $("incSeedBtn").hidden === false,
  "a fresh page: nothing in the takeoff, no undo, Engine's picks offered");

// the person adds the short solid by hand, then Engine's picks
env.setInclude(2, true);
$("incSeedBtn").onclick();
check(on() === "1,2,3" && added() === "1,2,3",
  "Engine's picks ADDS the two picks and keeps the style the person added (nothing taken out): " + on());
check(/Engine's picks: 2 styles added to the takeoff/.test(status) && /undo/.test(status), "...and says how many: " + status);
F.renderIncBtns();
check($("incSeedBtn").hidden === true && $("incUndoBtn").hidden === false,
  "every pick in: Engine's picks is no longer offered; undo is");
$("incUndoBtn").onclick();
check(on() === "2" && added() === "2" && $("incUndoBtn").hidden === true,
  "undo takes the picks back out: only the hand-added style is left, and undo is spent");

// all -> none -> undo (one step)
$("allBtn").onclick();
check(on() === "1,2,3,4,5" && added() === "1,2,3,4,5" && /every style is in the takeoff/.test(status), "all: every style added");
$("noneBtn").onclick();
check(on() === "" && added() === "" && /the takeoff is empty/.test(status), "none: the takeoff is empty");
const d0 = dirty, f0 = follows, s0 = scoped;
$("incUndoBtn").onclick();
check(on() === "1,2,3,4,5" && added() === "1,2,3,4,5" && dirty > d0,
  "undo after none puts back the takeoff of just before (all), and sends it to the server");
check(scoped > s0, "...and the scope filter is run again over the set put back (critic empty-2, MINOR-1: the screen kept the "
  + "filtered view of the snapshot while the set held more)");
check(follows > f0, "...and the panels that quote the takeoff are told (undo writes the ticks without setInclude)");
check(state.labels.get(1) === "STORM PIPE", "names are never touched by these buttons");

// another page: the snapshot is not offered there
$("noneBtn").onclick();
state.page = 1;
F.renderIncBtns();
check($("incUndoBtn").hidden === true, "an undo taken on one sheet is not offered on another");
const before2 = on();
$("incUndoBtn").onclick();
check(on() === before2, "...and clicking it there changes nothing");

// wiring: loadResult seeds every tick OFF; pageReset clears the snapshot and the added set; the buttons exist
const lr = js.slice(js.indexOf("async function loadResult("), js.indexOf("async function loadResult(") + 7000);
check(/state\.include\.set\(s\.style_id, false\);/.test(lr) && !/state\.include\.set\(s\.style_id, includeSeed\(s\)\)/.test(lr),
  "loadResult starts every style OUT of the takeoff (no seed tick)");
check(/await tkLoad\(gen, await _tkP\);/.test(lr), "loadResult reads what the person added from the server before the first paint");
const pr = js.slice(js.indexOf("function pageReset()"), js.indexOf("function pageReset()") + 900);
check(/state\.incUndo = null;/.test(pr) && /state\.tkSet = new Set\(\);/.test(pr), "pageReset drops the snapshot and the added set");
check(/id="incUndoBtn" class="mini" hidden/.test(html) && /id="incSeedBtn" class="mini" hidden/.test(html)
  && html.indexOf('id="noneBtn"') < html.indexOf('id="incUndoBtn"'), "index.html: undo and Engine's picks sit right after all / none");
check(/>Engine's picks<\/button>/.test(html) && /Add the styles the engine picks to the takeoff/.test(html)
  && /title="Add every line style on this sheet to the takeoff\."/.test(html), "the buttons' words say ADD");
check(/renderIncBtns\(\);/.test(js.slice(js.indexOf("function renderStyles()"), js.indexOf("function renderStyles()") + 400)),
  "renderStyles keeps the two buttons up to date");

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all all / none / Engine's picks / undo checks passed");
