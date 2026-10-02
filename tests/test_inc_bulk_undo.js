// ALL / NONE HAVE A WAY BACK (UI verify 2026-10-02 #7). "all" / "none" on the line styles
// overwrote every keep / drop tick the engine chose (C4.0: 44.8k -> 71.0k ft) and only "Reset this
// document" - which also deletes names and the detection - brought them back. Pins the INC-BULK
// block of webui/app.js: "undo" puts back the exact ticks AND their saved records, "engine's picks"
// puts back only the seeded keep / drop ticks (names untouched), and the seed is the one loadResult
// uses.
//
// run: node tests/test_inc_bulk_undo.js
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };

const a = js.indexOf("// INC-BULK-BEGIN"), b = js.indexOf("// INC-BULK-END");
check(a > 0 && b > a, "INC-BULK block present");

// a page: five styles, the engine's seed for each, then a saved hand decision on s3 and a name on s1
const styles = [
  { style_id: 1, class: "dashed", color: [0, 0, 0], length_pt: 900, layer: "C-STRM" },  // seed on
  { style_id: 2, class: "solid", color: [0, 0, 0], length_pt: 200, layer: "C-TOPO" },    // short solid: off
  { style_id: 3, class: "solid", color: [0, 0, 0], length_pt: 9000, layer: "C-WATR" },   // on; saved off by hand
  { style_id: 4, class: "annotation", color: [0, 0, 0], length_pt: 900, layer: "C-ANNO" }, // off
  { style_id: 5, class: "dashed", color: [1, 1, 1], length_pt: 900, layer: "C-EX" },     // white: off
];
const mem = new Map();
const store = { getItem: k => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: k => mem.delete(k) };
const els = {};
const $ = id => (els[id] = els[id] || { hidden: true, onclick: null });
const state = { job: "j1", page: 0, result: { styles }, include: new Map(), inheritedInc: new Map(), incUndo: null,
                labels: new Map([[1, "STORM PIPE"]]) };
let status = "";
const calls = [];
const env = {
  state, store, $,
  includeKey: st => "doc::p0::sig::inc::" + st.style_id,
  sgSuggested: () => false,
  setInclude: (sid, on) => { state.include.set(sid, !!on); state.inheritedInc.delete(sid);
    const st = styles.find(x => x.style_id === sid); if (st) store.setItem("doc::p0::sig::inc::" + sid, on ? "1" : "0"); },
  renderStyles: () => calls.push("renderStyles"), renderChips() {}, renderItemized() {}, renderMiniLegend() {}, draw() {},
  setStatus: m => { status = m; },
};
const F = new Function(...Object.keys(env), js.slice(a, b)
  + "\nreturn { includeSeed, incSnapshot, incRestore, renderIncBtns, incSeedDiffers };")(...Object.values(env));

// seed, as loadResult does it; then the saved decision on s3 (memApply), and one inherited tick on s1
for (const s of styles) state.include.set(s.style_id, F.includeSeed(s));
const seed = new Map(state.include);
check([...seed.values()].join() === "true,false,true,false,false", "the seed: long dashed on, short solid off, long solid on, annotation off, white off");
state.include.set(3, false); store.setItem("doc::p0::sig::inc::3", "0");
state.inheritedInc.set(1, true);
const before = JSON.stringify([...state.include]), beforeMem = JSON.stringify([...mem]);
F.renderIncBtns();
check($("incUndoBtn").hidden === true && $("incSeedBtn").hidden === false,
  "before any all / none: no undo; 'engine's picks' shows because s3 differs from the seed");

// none -> all (the verifier's clicks)
$("noneBtn").onclick();
check([...state.include.values()].every(v => v === false) && $("incUndoBtn").hidden === false && /undo/.test(status),
  "none: every tick off, undo offered, the status says so");
$("allBtn").onclick();
check([...state.include.values()].every(v => v === true), "all: every tick on");
// undo is ONE step: back to the 'none' state
$("incUndoBtn").onclick();
check([...state.include.values()].every(v => v === false) && $("incUndoBtn").hidden === true,
  "undo after all puts back the ticks of just before it (none), and is spent");

// the exact-restore case: from the hand-made state, none, then undo
for (const [k, v] of JSON.parse(before)) state.include.set(k, v);
mem.clear(); for (const [k, v] of JSON.parse(beforeMem)) mem.set(k, v);
state.inheritedInc = new Map([[1, true]]); state.incUndo = null;
$("noneBtn").onclick();
$("incUndoBtn").onclick();
check(JSON.stringify([...state.include]) === before && JSON.stringify([...mem]) === beforeMem && state.inheritedInc.get(1) === true,
  "undo restores the exact ticks, their saved records (and the never-saved stay unsaved) and the inherited mark");

// engine's picks: only the seeded ticks come back; the name stays
$("allBtn").onclick();
$("incSeedBtn").onclick();
check(JSON.stringify([...state.include]) === JSON.stringify([...seed]) && state.labels.get(1) === "STORM PIPE"
  && /engine's picks/.test(status) && !F.incSeedDiffers(),
  "engine's picks: keep / drop back to the seed (s3 on again), the name untouched");
check(mem.get("doc::p0::sig::inc::3") === "1", "engine's picks is saved like any tick, so a reload shows the same");
$("incUndoBtn").onclick();
check([...state.include.values()].every(v => v === true), "engine's picks can be undone too (back to all)");

// another page: the snapshot is not offered there
$("noneBtn").onclick();
state.page = 1;
F.renderIncBtns();
check($("incUndoBtn").hidden === true, "an undo taken on one sheet is not offered on another");
const before2 = JSON.stringify([...state.include]);
$("incUndoBtn").onclick();
check(JSON.stringify([...state.include]) === before2, "...and clicking it there changes nothing");

// wiring: loadResult seeds through includeSeed; pageReset clears the snapshot; the buttons exist
check(/state\.include\.set\(s\.style_id, includeSeed\(s\)\);/.test(js), "loadResult seeds the ticks with includeSeed (one rule for both)");
const pr = js.slice(js.indexOf("function pageReset()"), js.indexOf("function pageReset()") + 800);
check(/state\.incUndo = null;/.test(pr), "pageReset drops the snapshot");
check(/id="incUndoBtn" class="mini" hidden/.test(html) && /id="incSeedBtn" class="mini" hidden/.test(html)
  && html.indexOf('id="noneBtn"') < html.indexOf('id="incUndoBtn"'), "index.html: undo and engine's picks sit right after all / none");
check(/renderIncBtns\(\);/.test(js.slice(js.indexOf("function renderStyles()"), js.indexOf("function renderStyles()") + 400)),
  "renderStyles keeps the two buttons up to date");

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all all / none undo checks passed");
