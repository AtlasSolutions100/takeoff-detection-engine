// FLATTEN: take the zigzag out of a run between two picks (2026-09-18).
//
// Measured on the 2026-09-18 call: the same property line read 356 ft in the
// panel and 347 ft measured straight - about 9 ft on 350, ~2.5% - because the
// detected polyline jitters between its vertices instead of running straight.
// Most lines on a sheet are clean; a few are "all zigzagged and weird" and
// the operator wants to fix those by hand: click the line, press R, click
// where the straight run starts and where it ends.
//
// The geometry here is the REAL functions lifted out of app.js - polyLen,
// projectOnChain, materialize, flattenClick - not a re-implementation, so a
// change to any of them fails this file rather than passing a copy of itself.
//
// Dependency-free:  node tests/test_flatten.js
"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "webui", "app.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "webui", "index.html"), "utf8");

let fails = 0;
const check = (ok, msg) => {
  if (ok) console.log("  ok: " + msg);
  else { fails++; console.error("  FAIL: " + msg); }
};
function fnSrc(name) {
  const j = SRC.indexOf("\nfunction " + name + "(");
  if (j < 0) return null;
  let d = 0;
  for (let k = SRC.indexOf("{", j); k < SRC.length; k++) {
    if (SRC[k] === "{") d++;
    else if (SRC[k] === "}" && --d === 0) return SRC.slice(j + 1, k + 1);
  }
  return null;
}

// --- 1. it is wired everywhere a two-click tool has to be ----------------
console.log("\n1. wiring");
check(/id="flattenBtn"/.test(HTML), "index.html has the button");
check(/\(R; F is line rendering\)/.test(HTML),
  "...and the tooltip says which key, and why it is not F");
check(/"measure", "extend", "cutspan", "flatten", "drawline"/.test(SRC),
  "it is a POINT_TOOLS member, so a click places a point");
check(/state\.tool === "flatten" && state\.selected !== null/.test(SRC),
  "the canvas dispatches its clicks");
check(/state\.tool === "flatten" && state\.flatA/.test(SRC),
  "the first anchor is drawn, so a half-taken pick is visible");
check(/"cutSpanBtn", "flattenBtn"\]/.test(SRC),
  "it is disabled with nothing selected");
check(/else if \(state\.tool === "flatten"\) cancelFlatten\(\);/.test(SRC),
  "it is released before an undo, like every other chain-owning tool");
check(/if \(k === "r"\) \{ e\.preventDefault\(\); \$\("flattenBtn"\)\.click\(\)/.test(SRC),
  "R is bound");
check(/if \(k === "f"\) \{ e\.preventDefault\(\); \$\("faithfulBtn"\)\.click\(\)/.test(SRC),
  "...and F still does what it always did (line rendering), untouched");

// --- 2. the geometry, on the real functions ------------------------------
console.log("\n2. what it does to the points");
const parts = ["polyLen", "projectOnChain", "materialize", "chainById",
               "dedupePts", "cancelFlatten", "flattenClick"].map(fnSrc);
check(parts.every(Boolean), "every function under test was found in app.js");

function harness(chain) {
  const state = {
    selected: chain.chain_id, result: { chains: [chain] },
    excluded: new Set(), chainLabels: new Map(), nextChainId: 900,
    view: { k: 1 }, tool: "flatten", flatA: null, wzRev: 0,
  };
  const log = { status: [], undo: [], edited: [] };
  const env = {
    state,
    IMG_DPI: 72,
    $: () => ({ classList: { add() {}, remove() {} }, hidden: false }),
    setStatus: (m, k) => log.status.push([m, k || ""]),
    captureUndo: (l) => log.undo.push(l),
    afterEdit: (m) => log.edited.push(m),
    updateSelBar: () => {},
    draw: () => {},
    fmtQty: (v) => String(Math.round(v)),
    fmtLen: (v) => Math.round(v) + " pt",
  };
  const body = parts.join("\n") + "\nreturn { flattenClick, polyLen };";
  const fn = new Function(...Object.keys(env), body)(...Object.values(env));
  return { fn, state, log };
}
const mk = (points) => ({
  chain_id: 1, points: points.map(p => p.slice()),
  length_pt: null, custom: false,
});

{
  // a straight run with a saw-tooth in the middle: ends at y=0, teeth +-6
  const pts = [[0, 0], [20, 6], [40, -6], [60, 6], [80, -6], [100, 0]];
  const ch = mk(pts);
  ch.length_pt = 0;
  const { fn, state, log } = harness(ch);
  ch.length_pt = fn.polyLen(pts);
  const before = fn.polyLen(pts);
  fn.flattenClick(0, 0);        // first pick: the start
  fn.flattenClick(100, 0);      // second pick: the end
  const out = state.result.chains.find(c => c.custom);
  check(!!out, "the edit lands on a materialised copy, not the detection");
  check(state.excluded.has(1), "...and the original is excluded from the export");
  const after = fn.polyLen(out.points);
  check(out.points.length === 2,
    "every vertex between the picks is gone  (" + out.points.length + " left)");
  check(Math.abs(after - 100) < 0.01,
    "the run is now the straight distance  (" + after.toFixed(2) + " vs 100)");
  check(after < before,
    "it got shorter: " + before.toFixed(1) + " -> " + after.toFixed(2) + " pt "
    + "(" + (100 * (before - after) / before).toFixed(1) + "% of the zigzag)");
  check(Math.abs(out.length_pt - after) < 0.01,
    "the measured length travels with the geometry  (" + out.length_pt.toFixed(2) + ")");
  check(log.undo.length === 1 && /flatten/.test(log.undo[0]),
    "it is undoable, and the undo says what it was  (" + log.undo[0] + ")");
  check(log.edited.length === 1 && /flattened/.test(log.edited[0]),
    "...and the operator is told what changed  (" + log.edited[0] + ")");
}
{
  // THE PICKS THEMSELVES MUST NOT MOVE. Straightening a run is a claim
  // about the middle, not about where the run begins and ends.
  const ch = mk([[0, 0], [20, 6], [40, -6], [60, 0]]);
  const { fn, state } = harness(ch);
  ch.length_pt = fn.polyLen(ch.points);
  fn.flattenClick(0, 0);
  fn.flattenClick(60, 0);
  const out = state.result.chains.find(c => c.custom);
  check(out.points[0][0] === 0 && out.points[0][1] === 0
    && Math.abs(out.points[out.points.length - 1][0] - 60) < 0.01,
    "the two ends stay exactly where they were picked");
}
{
  // clicked end-first: the same edit
  const ch = mk([[0, 0], [20, 6], [40, -6], [60, 0]]);
  const { fn, state } = harness(ch);
  ch.length_pt = fn.polyLen(ch.points);
  fn.flattenClick(60, 0);
  fn.flattenClick(0, 0);
  const out = state.result.chains.find(c => c.custom);
  check(out && out.points.length === 2,
    "picking the far end first flattens the same run  ("
    + (out ? out.points.length : "none") + " points)");
}
{
  // NOTHING BETWEEN THE PICKS IS NOT AN EDIT.
  const ch = mk([[0, 0], [50, 0], [100, 0]]);
  const { fn, state, log } = harness(ch);
  ch.length_pt = fn.polyLen(ch.points);
  fn.flattenClick(0, 0);
  fn.flattenClick(50, 0);
  check(!state.result.chains.some(c => c.custom),
    "two picks on an already-straight run change nothing");
  check(!state.excluded.has(1), "...the original is not excluded either");
  check(log.undo.length === 0, "...and nothing goes on the undo stack");
  check(log.status.some(([m, k]) => k === "err" && /already straight/.test(m)),
    "...and it says so  (" + (log.status[log.status.length - 1] || [])[0] + ")");
}
{
  // the case from the call: ~2.5% of a long run, recovered
  // amplitude chosen so the jitter costs about what he measured (~2.5%
  // on 350 ft), not the 12% a violent synthetic saw-tooth would
  const n = 60, span = 350, amp = 0.65;
  const pts = [];
  for (let i = 0; i <= n; i++)
    pts.push([i * span / n, (i % 2 ? amp : -amp) * (i && i < n ? 1 : 0)]);
  const ch = mk(pts);
  const { fn, state } = harness(ch);
  ch.length_pt = fn.polyLen(pts);
  const before = fn.polyLen(pts);
  fn.flattenClick(0, 0);
  fn.flattenClick(span, 0);
  const out = state.result.chains.find(c => c.custom);
  const after = fn.polyLen(out.points);
  const pct = 100 * (before - after) / before;
  console.log("     a 350-unit run with a " + amp + "-unit jitter: "
    + before.toFixed(1) + " -> " + after.toFixed(1) + " (" + pct.toFixed(1) + "% off)");
  check(pct > 1.5 && pct < 6,
    "a jitter like the one he measured costs a few percent, and flatten "
    + "recovers it  (" + pct.toFixed(1) + "%)");
}

console.log(fails ? "\n" + fails + " FAILED" : "\nall passed");
process.exit(fails ? 1 : 0);
