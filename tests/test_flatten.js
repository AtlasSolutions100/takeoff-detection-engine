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
               "dpSimplify", "dedupePts", "cancelFlatten", "flattenClick"].map(fnSrc);
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
    FLATTEN_CORNER_PT: 4.0,
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
  // JITTER: a straight run wobbling by less than the trace noise threshold.
  // Measured on three real sheets, genuine jitter sits below 0.5 pt; this
  // is 1.5 pt, still well inside the 4 pt corner tolerance.
  const pts = [[0, 0], [20, 1.5], [40, -1.5], [60, 1.5], [80, -1.5], [100, 0]];
  const ch = mk(pts);
  const { fn, state, log } = harness(ch);
  ch.length_pt = fn.polyLen(pts);
  const before = fn.polyLen(pts);
  fn.flattenClick(0, 0);
  fn.flattenClick(100, 0);
  const out = state.result.chains.find(c => c.custom);
  check(!!out, "the edit lands on a materialised copy, not the detection");
  check(state.excluded.has(1), "...and the original is excluded from the export");
  const after = fn.polyLen(out.points);
  check(out.points.length === 2,
    "pure jitter collapses to one segment  (" + out.points.length + " points)");
  check(Math.abs(after - 100) < 0.01,
    "...the straight distance  (" + after.toFixed(2) + ")");
  check(Math.abs(out.length_pt - after) < 0.01,
    "the measured length travels with the geometry  (" + out.length_pt.toFixed(2) + ")");
  check(log.undo.length === 1 && /flatten/.test(log.undo[0]), "it is undoable");
  check(log.edited.length === 1 && !/corner/.test(log.edited[0]),
    "...and claims no corners, because there were none  (" + log.edited[0] + ")");
}
{
  // AN L-SHAPED PERIMETER, PICKED ACROSS THE CORNER. This is the case that
  // made the first version dangerous: it replaced the whole span with the
  // diagonal, 406.7 pt -> 282.8, deleting 30% of a perimeter in one click.
  const L = [];
  for (let x = 0; x <= 200; x += 20)
    L.push([x, x && x < 200 ? (x / 20 % 2 ? 1.5 : -1.5) : 0]);
  for (let y = 20; y <= 200; y += 20)
    L.push([200 + (y / 20 % 2 ? 1.5 : -1.5) * (y < 200 ? 1 : 0), y]);
  const ch = mk(L);
  const { fn, state, log } = harness(ch);
  ch.length_pt = fn.polyLen(L);
  const before = fn.polyLen(L);
  fn.flattenClick(0, 0);
  fn.flattenClick(200, 200);
  const out = state.result.chains.find(c => c.custom);
  const after = fn.polyLen(out.points);
  check(out.points.length === 3,
    "the L keeps its corner: start, corner, end  (" + out.points.length + " points)");
  const c = out.points[1];
  check(Math.abs(c[0] - 200) < 2 && Math.abs(c[1]) < 2,
    "...and the corner is where the drawing put it  ("
    + c.map(v => v.toFixed(1)).join(", ") + ")");
  check(after > 395 && after < 402,
    "the legs are straight and the length survives: "
    + before.toFixed(1) + " -> " + after.toFixed(1)
    + " pt (the diagonal would have been 282.8)");
  check(/1 corner kept/.test(log.edited[0]),
    "...and the operator is told the corner was kept  (" + log.edited[0] + ")");
}
{
  // a closed rectangle, picked across two corners: nothing should be lost
  const R = [[0, 0], [100, 0], [100, 60], [0, 60], [0, 0]];
  const ch = mk(R);
  const { fn, state } = harness(ch);
  ch.length_pt = fn.polyLen(R);
  const before = fn.polyLen(R);
  fn.flattenClick(0, 0);
  fn.flattenClick(100, 60);
  const out = state.result.chains.find(c => c.custom);
  const after = out ? fn.polyLen(out.points) : before;
  check(Math.abs(after - before) < 0.5,
    "a rectangle keeps every corner and every foot  ("
    + before.toFixed(1) + " -> " + after.toFixed(1) + ")");
}
{
  // THE PICKS THEMSELVES MUST NOT MOVE.
  // long enough that the removed jitter clears the "did anything happen"
  // floor: 10 teeth of 2 pt over 200 saves about 4 pt
  const jit = [[0, 0]];
  for (let x = 20; x < 200; x += 20) jit.push([x, x / 20 % 2 ? 2 : -2]);
  jit.push([200, 0]);
  const ch = mk(jit);
  const { fn, state } = harness(ch);
  ch.length_pt = fn.polyLen(ch.points);
  fn.flattenClick(0, 0);
  fn.flattenClick(200, 0);
  const out = state.result.chains.find(c => c.custom);
  check(out.points[0][0] === 0 && out.points[0][1] === 0
    && Math.abs(out.points[out.points.length - 1][0] - 200) < 0.01,
    "the two ends stay exactly where they were picked");
}
{
  // clicked end-first: the same edit
  const jit2 = [[0, 0]];
  for (let x = 20; x < 200; x += 20) jit2.push([x, x / 20 % 2 ? 2 : -2]);
  jit2.push([200, 0]);
  const ch = mk(jit2);
  const { fn, state } = harness(ch);
  ch.length_pt = fn.polyLen(ch.points);
  fn.flattenClick(200, 0);
  fn.flattenClick(0, 0);
  const out = state.result.chains.find(c => c.custom);
  check(out && out.points.length === 2,
    "picking the far end first flattens the same run  ("
    + (out ? out.points.length : "none") + " points)");
}
{
  // a bend too big to be noise is REFUSED, and the reason is the real one
  const ch = mk([[0, 0], [20, 6], [40, -6], [60, 6], [80, -6], [100, 0]]);
  const { fn, state, log } = harness(ch);
  ch.length_pt = fn.polyLen(ch.points);
  fn.flattenClick(0, 0);
  fn.flattenClick(100, 0);
  check(!state.result.chains.some(c => c.custom),
    "bends bigger than trace noise are kept, not flattened");
  const last = (log.status[log.status.length - 1] || [])[0] || "";
  check(/bigger than trace noise/.test(last) && /real shape/.test(last),
    "...and it says THAT, not 'already straight'  (" + last.slice(0, 70) + ")");
  check(/hold Shift/.test(last),
    "...and offers the way through it");
}
{
  // ...and Shift takes it
  const ch = mk([[0, 0], [20, 6], [40, -6], [60, 6], [80, -6], [100, 0]]);
  const { fn, state } = harness(ch);
  ch.length_pt = fn.polyLen(ch.points);
  fn.flattenClick(0, 0);
  fn.flattenClick(100, 0, true);
  const out = state.result.chains.find(c => c.custom);
  check(out && out.points.length === 2,
    "Shift on the second click straightens it anyway  ("
    + (out ? out.points.length : "none") + " points)");
}
{
  // two picks on an already-straight run: still nothing, still says so
  const ch = mk([[0, 0], [50, 0], [100, 0]]);
  const { fn, state, log } = harness(ch);
  ch.length_pt = fn.polyLen(ch.points);
  fn.flattenClick(0, 0);
  fn.flattenClick(50, 0);
  check(!state.result.chains.some(c => c.custom),
    "an already-straight run changes nothing");
  check(log.undo.length === 0, "...and nothing goes on the undo stack");
  check(log.status.some(([m, k]) => k === "err"
        && /already sit on straight runs/.test(m)),
    "...and says why, accurately  ("
    + ((log.status[log.status.length - 1] || [])[0] || "").slice(0, 60) + ")");
}
{
  // the case from the call: a jitter sized to his own measurement
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
    "a jitter like the one he measured is recovered  (" + pct.toFixed(1) + "%)");
}

console.log(fails ? "\n" + fails + " FAILED" : "\nall passed");
process.exit(fails ? 1 : 0);
