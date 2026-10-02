// The DELIVERABLE has to say what the Suggested-disables group held out.
//
// WHY THIS SUITE EXISTS. Defect 0 stops counting drawing furniture - text,
// dimensions, leaders, title blocks - that the engine had been measuring and
// offering to price. Measured across 116 cached sheets that is 63 sheets
// (54%), 1,582 line families, 347,440 pt; on Kayden C5.1 alone it took the
// sheet total from 151,800 ft to 134,200 ft, an 11.6% drop.
//
// The panel on screen announced all of that loudly. The exported elements and
// the printed PDF said NOTHING - an adversary diffed the report before and
// after and it was word for word identical. So a client received a number
// 11.6% smaller than the sheet's linework with no way to learn why, which is
// the same failure as the false elevation-provenance claim earlier the same
// night, on the one surface that actually leaves the building.
//
// The three things pinned here are the three that can silently rot:
//
//   1. BOTH VOICES. Held out says NOT counted; ticked back on says counted
//      at the reviewer's direction. The second matters as much as the first -
//      an operator who deliberately counts annotation has made a decision the
//      report must carry, not hide.
//   2. SILENCE WHEN THERE IS NOTHING TO SAY. A sheet with no annotation-named
//      families must add no note at all. A caveat that appears on every
//      report is a caveat nobody reads.
//   3. FIRST IN THE LIST. server.py approve() keeps body.notes[:7]. A caveat
//      about the headline total outranks a caveat about one section of it, so
//      if anything is going to be trimmed it must not be this.
//
// webui/app.js is not a module and touches the DOM at load, so this extracts
// the one function by brace matching and runs it against stubs.
//
// Dependency-free, like the other suites:  node tests/test_report_holdout.js

"use strict";
const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "webui", "app.js"), "utf8");

let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); }
  else console.log("ok:", msg);
}

// ---- pull the function out by matching its braces -------------------------
function extract(name) {
  const start = SRC.indexOf("function " + name + "(");
  if (start < 0) return null;
  let i = SRC.indexOf("{", start), depth = 0;
  for (; i < SRC.length; i++) {
    if (SRC[i] === "{") depth++;
    else if (SRC[i] === "}") { depth--; if (!depth) return SRC.slice(start, i + 1); }
  }
  return null;
}

const src = extract("sgApproveNotes");
check(!!src, "sgApproveNotes() exists in webui/app.js");
if (!src) { console.error("\ncannot continue"); process.exit(1); }

// stubs: the four things the function reaches for
function build(styles, includedIds) {
  const state = { result: { styles }, include: new Map(styles.map(
    (s) => [s.style_id, includedIds.has(s.style_id)])) };
  const sgSuggested = (s) => !!s.suggested;
  const styleLenPt = (s) => s.len || 0;
  const fmtQty = (pt) => Math.round(pt) + " pt";
  return new Function("state", "sgSuggested", "styleLenPt", "fmtQty",
    src + "\n return sgApproveNotes();")(state, sgSuggested, styleLenPt, fmtQty);
}

const A = { style_id: 1, suggested: true, len: 1000 };
const B = { style_id: 2, suggested: true, len: 500 };
const REAL = { style_id: 3, suggested: false, len: 9000 };

// ---- 1. both voices -------------------------------------------------------
let n = build([A, B, REAL], new Set([3]));            // both held out
check(n.length === 1, "held out: exactly one note");
check(/NOT counted/.test(n[0]), "held out: says NOT counted");
check(/2 line families/.test(n[0]), "held out: counts the families");
check(/1500 pt/.test(n[0]), "held out: sums only the held-out footage");
check(!/9000/.test(n[0]), "held out: does not include real linework in the sum");

n = build([A, B, REAL], new Set([1, 2, 3]));          // operator ticked them on
check(n.length === 1, "ticked on: exactly one note");
check(/ARE\b/.test(n[0]) && /reviewer's direction/.test(n[0]),
  "ticked on: says they ARE counted, at the reviewer's direction");
check(!/NOT counted/.test(n[0]), "ticked on: does not also claim they were left out");

n = build([A, B, REAL], new Set([1, 3]));             // one each way
check(n.length === 2, "mixed: one note for each voice");

// ---- 2. silent when there is nothing to say -------------------------------
check(build([REAL], new Set([3])).length === 0,
  "a sheet with no annotation-named families adds NO note");
check(build([], new Set()).length === 0, "no styles at all adds no note");

// ---- 3. first in the list, because notes get trimmed at seven -------------
const compose = extract("approveNotes");
check(!!compose, "approveNotes() exists");
if (compose) {
  const order = ["sgApproveNotes", "famApproveNotes"]
    .map((f) => compose.indexOf(f));
  check(order.every((i) => i >= 0), "approveNotes composes both sources");
  check(order[0] < order[1],
    "the holdout note is FIRST - server.py keeps only body.notes[:7]");
}
check(/APPROVE_NOTES_MAX\s*=\s*7/.test(SRC),
  "the cap is 7, matching server.py approve() - it moved from 6 on 2026-09-02 when the earthwork block gained its contour-coverage note");

// ---- 4. the scope sentence cannot go stale after a group tick -------------
// It used to refresh only inside the `scoped` branch and not at all on the
// partial-restore early return, so two numbers in one panel disagreed by 52
// families / 18,600 ft until a reload.
const tick = extract("sgTickGroup");
check(!!tick, "sgTickGroup() exists");
if (tick) {
  const bars = (tick.match(/renderScopeBar\(\)/g) || []).length;
  check(bars >= 2, "sgTickGroup refreshes the scope sentence on BOTH paths"
    + "  (found " + bars + ", want >= 2)");
  check(!/if\s*\(scoped\)\s*\{\s*scopeApply\(\);\s*renderScopeBar\(\);\s*\}/.test(tick),
    "the scope sentence is no longer refreshed only when `scoped`");
}

console.log("\n" + (failures ? failures + " FAILED" : "all checks passed"));
process.exit(failures ? 1 : 0);
