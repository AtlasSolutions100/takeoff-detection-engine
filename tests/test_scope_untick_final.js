// AN UNTICK IS FINAL (critic empty-1, MAJOR-3; and MINOR-4, the scope sentence). The scope filter ("Whose scope is
// this?") sets rows aside without deciding anything: clearing the declaration must put back exactly the person's own
// takeoff. While the takeoff started full, a set-aside row "had always been in", so a row handed back to the filter was
// ticked on first. Now the takeoff starts EMPTY, and that put back - on screen, 4,205.8 ft live - a style the person
// had just taken out (its own mark; the group box, which also left it in the server's record; none).
//
// The rule pinned here: what the filter may put back is the PERSON'S OWN SET (state.tkSet, the server's record of what
// they added) and nothing else. So:
//   1. a set-aside row ticked by its own mark and unticked again stays out when the declaration is cleared, on screen
//      and in the set sent to the server;
//   2. the same through the group box, for many rows at once;
//   3. the same through "none";
//   4. the filter never takes out what the person added (since CRITIC-empty-2, MINOR-2: a declared trade sets aside
//      only rows the person has NOT added, so the screen is always their set), and it never writes to the server;
//   5. a row the person put back by hand stays through the clear; undo after none restores the person's set;
//   6. the scope sentence's count and footage are read live (they quoted the takeoff as the last filter pass saw it:
//      "the 0 in the takeoff (0 pt) stay in it" over two rows).
// The real functions of webui/app.js run here - the row mark's own click handler included - over a stub page.
//
// run: node tests/test_scope_untick_final.js        (fails on 888b2c1)
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(name) {
  let start = js.indexOf("\nfunction " + name + "(");
  if (start < 0) return "function " + name + "() { return false; }   // not in this tree";
  start += 1;
  let depth = 0;
  for (let j = js.indexOf("{", start); j < js.length; j++) {
    if (js[j] === "{") depth++;
    else if (js[j] === "}" && --depth === 0) return js.slice(start, j + 1);
  }
  return "";
}
// the takeoff mark's own click handler, as renderStyles builds it for a row
const hA = js.lastIndexOf("cb.onclick = (e) => { e.stopPropagation();", js.indexOf("const on = !state.include.get(s.style_id);"));
const hB = js.indexOf("draw(); };", hA);
check(hA > 0 && hB > hA, "the row's takeoff-mark handler is found in renderStyles");
const HANDLER = js.slice(hA + "cb.onclick = ".length, hB + "draw(); }".length);

const FNS = ["scopeApply", "scopeMine", "scopeKeepRow", "scopeTally", "sgTickGroup", "sgPartialMem", "setInclude",
             "setIncludeTransient", "incSnapshot", "incRestore", "incBulk", "incAfter"];
// a sheet: A and B are the declared trade's or unattributed; C and D are another trade's (set aside when declared)
const STYLES = [
  { style_id: 80, name: "A", length_pt: 15777, layer: "O-SURFACE-FG" },
  { style_id: 81, name: "B", length_pt: 9000, layer: "" },
  { style_id: 28, name: "C", length_pt: 15141, layer: "P1-CONCRETE", other: true },
  { style_id: 29, name: "D", length_pt: 3000, layer: "P1-GUT-FL", other: true },
];
function page() {
  const sent = [];                 // every set the page sends to the server (tkDirty)
  const T = { trades: [] };
  const state = { job: "j1", page: 0, result: { styles: STYLES.map(s => Object.assign({}, s)), scale: null },
                  include: new Map(), inheritedInc: new Map(), tkSet: new Set(), incUndo: null,
                  scopeV: new Map(), scopeKeep: new Set(), scopeOffBy: new Set(), scopeOwnerOn: new Set() };
  for (const s of state.result.styles) state.include.set(s.style_id, false);          // as loadResult leaves it
  const env = {
    state,
    scopeRec: () => ({ trades: T.trades.slice(), showAside: true }),
    scopeEvidenceForStyle: (s) => s,
    scopeVerdict: (s, trades) => (!trades.length ? { v: "unknown" } : s.other ? { v: "out" } : s.layer ? { v: "in" } : { v: "unknown" }),
    styleLenPt: (s) => s.length_pt,
    sgCurrentPlan: () => {
      const rows = state.result.styles;
      const n = rows.filter(s => state.include.get(s.style_id)).length;
      return { groups: [{ key: "dashed", name: "Dashed", rows, tick: { state: n === 0 ? "none" : n === rows.length ? "all" : "some", n: rows.length } }] };
    },
    tkDirty: () => { sent.push([...state.tkSet].sort((a, b) => a - b).join(",")); },
    tkFollow: () => {},
    renderScopeBar() {}, renderStyles() {}, renderChips() {}, renderItemized() {}, renderMiniLegend() {}, draw() {},
    renderIncBtns() {}, setStatus() {},
  };
  const C = new Function(...Object.keys(env), FNS.map(fnSrc).join("\n")
    + "\nfunction rowClick(s) { const sv = (state.scopeV && state.scopeV.get(s.style_id)) || { v: 'unknown' };"
    + " const h = " + HANDLER + "; h({ stopPropagation() {} }); }"
    + "\nreturn { rowClick, " + FNS.join(", ") + " };")(...Object.values(env));
  const st = (name) => state.result.styles.find(s => s.name === name);
  const on = () => state.result.styles.filter(s => state.include.get(s.style_id)).map(s => s.name).join("");
  const mine = () => state.result.styles.filter(s => state.tkSet.has(s.style_id)).map(s => s.name).join("");
  const declare = (yes) => { T.trades = yes ? ["Earthwork & Grading"] : []; C.scopeApply(); };
  const last = () => (sent.length ? sent[sent.length - 1] : "(nothing sent)");
  C.scopeApply();                  // as loadResult runs it, nothing declared
  return { C, state, st, on, mine, declare, sent, last };
}

// ---- 1. its own mark: tick a set-aside row, untick it, clear the declaration ---------------------------------------
{
  const p = page();
  p.declare(true);
  check(p.on() === "" && p.state.scopeV.get(28).v === "out", "an empty takeoff, a trade declared: C is set aside, nothing is in");
  p.C.rowClick(p.st("C"));
  check(p.on() === "C" && p.mine() === "C" && p.state.scopeV.get(28).v === "kept", "the person ticks C by its own mark: in (put back by hand)");
  p.C.rowClick(p.st("C"));
  check(p.on() === "" && p.mine() === "" && p.last() === "", "they untick it: out, and the set sent to the server is empty");
  p.declare(false);
  check(p.on() === "", "the declaration is cleared: C STAYS OUT of the takeoff (it came back: 4,205.8 ft live) - on: '" + p.on() + "'");
  check(p.mine() === "" && p.last() === "", "...and the server's record is still empty");
}
// ---- 2. the group box ------------------------------------------------------------------------------------------------
{
  const p = page();
  p.declare(true);
  p.C.sgTickGroup("dashed", true);
  check(p.on() === "ABCD" && p.mine() === "ABCD", "the group box ticks the whole group, the set-aside rows too (put back): " + p.on());
  p.C.sgTickGroup("dashed", false);
  check(p.on() === "" && p.mine() === "" && p.last() === "",
    "unticking the group takes EVERY row out of the person's set - C and D too (they stayed in the server's record): mine '" + p.mine() + "', sent '" + p.last() + "'");
  p.declare(false);
  check(p.on() === "" && p.mine() === "", "the declaration is cleared: nothing comes back (18 styles, 4,746.8 ft did live) - on: '" + p.on() + "'");
}
// ---- 3. none ---------------------------------------------------------------------------------------------------------
{
  const p = page();
  p.C.setInclude(80, true); p.C.setInclude(28, true);
  p.declare(true);
  // (since CRITIC-empty-2, MINOR-2: a style the person added is never set aside - screen = record = export)
  check(p.on() === "AC" && p.mine() === "AC" && p.state.scopeV.get(28).v === "kept",
    "the person added A and C; a trade is declared: C, another trade's, STAYS in the takeoff (kept: they added it)");
  p.C.incBulk("the takeoff is empty", () => false);
  check(p.on() === "" && p.mine() === "" && p.state.scopeV.get(28).v === "out",
    "none: the takeoff is empty, the person's set too, and C is set aside like any row they have not added");
  p.declare(false);
  check(p.on() === "", "the declaration is cleared: C does not come back (none took it out) - on: '" + p.on() + "'");
}
// ---- 4. the filter never takes out what the person added, and writes nothing ----------------------------------------
{
  const p = page();
  p.C.setInclude(80, true); p.C.setInclude(28, true);
  const n = p.sent.length;
  p.declare(true);
  check(p.on() === "AC" && p.mine() === "AC" && p.sent.length === n,
    "declaring a trade leaves both of the person's rows in the takeoff (the screen is their set) and sends nothing to the server");
  const t = p.C.scopeTally();
  check(t.on === 2 && t.kept === 1 && Math.round(t.ftKept) === 15141 && t.out === 1 && t.ftOut === 0,
    "the scope sentence counts C as kept (15,141 pt in the takeoff) and D as set aside with nothing out of the takeoff");
  p.declare(false);
  check(p.on() === "AC" && p.mine() === "AC" && p.sent.length === n,
    "clearing it changes nothing in the takeoff (A and C), and still sends nothing");
}
// ---- 5. a put-back stays; undo after none restores the person's set --------------------------------------------------
{
  const p = page();
  p.C.setInclude(80, true);
  p.declare(true);
  p.C.rowClick(p.st("C"));
  p.declare(false);
  check(p.on() === "AC" && p.mine() === "AC", "a row the person put back by hand is theirs: it stays through the clear");
  const q = page();
  q.C.setInclude(80, true); q.C.setInclude(28, true);
  q.declare(true);
  q.C.incBulk("the takeoff is empty", () => false);
  q.C.incRestore(q.state.incUndo);
  check(q.on() === "AC" && q.mine() === "AC", "undo after none, a trade still declared: A and C are back, on screen and in the person's set");
  q.declare(false);
  check(q.on() === "AC", "...and clearing the declaration changes nothing: " + q.on());
}
// ---- 6. the scope sentence reads the takeoff live (MINOR-4) ---------------------------------------------------------
{
  const p = page();                                              // nothing declared, nothing in the takeoff
  check(p.C.scopeTally().on === 0, "a new page: the sentence counts 0 in the takeoff");
  p.C.setInclude(80, true); p.C.setInclude(81, true);            // two ticks; no filter pass runs
  const t = p.C.scopeTally();
  check(t.on === 2 && Math.round(t.ftUnknown) === 15777 + 9000,
    `after two ticks the sentence counts the 2 in the takeoff and their footage (it said 0 and 0 pt): on ${t.on}, ${Math.round(t.ftUnknown)} pt`);
}
// the wiring the page needs for 6: the bar is drawn again after a tick (tkFollow; tests/test_empty_takeoff_ui.js 7)
check(/renderScopeBar\(\)/.test(fnSrc("tkFollow")) && /tkFollow\(\)/.test(fnSrc("setInclude")),
  "a tick draws the scope sentence again (tkFollow)");
// ...and under a declared trade it no longer calls the rows the sheet says nothing about "kept and ticked": on an
// empty takeoff none of them is ticked (seen live: "71 it does not say, kept and ticked (0 ft)")
{
  const bar = fnSrc("renderScopeBar").replace(/\/\/[^\n]*/g, "");
  check(!/kept and ticked/.test(bar) && /left as they are/.test(bar),
    "the declared-trade sentence says the unattributed rows are 'left as they are', not 'kept and ticked'");
}

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all scope / untick checks passed");
