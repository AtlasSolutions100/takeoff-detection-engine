// The review queue's ranking and thresholds, pinned.
//
// The CHECK THESE panel (webui/app.js, CHECK-QUEUE markers) is the
// triage surface: it decides which of up to 280 style rows and half a
// dozen page-level doubts an operator is told to look at first. A wrong
// ORDER there is a quiet failure - the sheet's most expensive doubt sits
// below the cap while twelve slivers sit above it - so the ordering
// rules are pinned here the same way the trench arithmetic is:
//
//   * severity before weight: a stop (two sources contradict / no scale)
//     outranks any warn, whatever the footage;
//   * weight is affected footage in paper points; page-wide doubts carry
//     the page times a stated share factor (1.0 contradictions/datum,
//     0.6 OCR scale, 0.5 unsettled multi-scale, 0.25 ranked multi-scale,
//     0.4 stale saved inputs, 0.35 schedule mismatch);
//   * per-style entries carry exactly the footage in doubt (the style's
//     length for an unnamed style, the welded+picked-up length for an
//     inferred one);
//   * thresholds: welded fraction must EXCEED 25%; a style must reach
//     max(500 pt, 1% of the page) to queue at all;
//   * the cap at 12 discloses (`more` count), checked-off entries sink
//     but never vanish, ties break on id so the order is deterministic.
//
// webui/app.js is not a module and touches the DOM at load, so this test
// evals ONLY the block between the CHECK-QUEUE markers - that block is
// kept pure (no DOM, no state.*, no fetch) for exactly this reason.
//
// Dependency-free like tests/test_gate.py:  node tests/test_check_queue.js

"use strict";
const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const a = src.indexOf("// CHECK-QUEUE-BEGIN");
const b = src.indexOf("// CHECK-QUEUE-END");
if (a < 0 || b < 0 || b <= a) {
  console.error("FAIL: CHECK-QUEUE markers not found in webui/app.js");
  process.exit(1);
}
const block = src.slice(a, b);
const T = new Function(block + `
  return { CK_CAP, CK_WELD_FRAC, CK_W_PAGE, CK_W_OCR, CK_W_MULTI,
           CK_W_MULTI_RANKED, CK_W_DROPS, CK_W_SCHED,
           ckStyleFloor, ckFt, ckDec, ckDrops, ckBuild, ckOrder };`)();

let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); }
  else console.log("ok:", msg);
}
function near(x, y, tol, msg) {
  check(Math.abs(x - y) <= (tol == null ? 1e-9 : tol),
        `${msg} (got ${x}, want ${y})`);
}
// a clean page: corroborated single scale, every check ran, nothing found
function inp(over) {
  return Object.assign({
    totalPt: 100000,
    ftPerIn: 30,
    styles: [],
    scale: { present: true, text: '1"=30\'', ftIn: 30, conflict: false,
             corroborated: true, barFt: 30, deltaPct: 0, source: "text",
             operator: false, multi: null },
    tag: null,
    datum: { state: "checked", conflict: false },
    spots: { state: "checked", n: 0, unresolved: 0 },
    drops: { state: "checked", ew: 0, tr: 0, sigChanged: false },
    marks: [],
  }, over);
}
function ids(entries) { return entries.map(e => e.id); }
function byId(entries, id) { return entries.find(e => e.id === id); }

// ---- formatting: never a foot the sheet cannot back ----------------------
check(T.ckFt(720, 30) === "300 ft", 'ckFt 720pt @30 -> "300 ft"');
check(T.ckFt(2400, 30) === "1.0k ft", 'ckFt 2400pt @30 -> "1.0k ft"');
check(T.ckFt(720, null) === "10 paper-inches",
      "ckFt without a scale reports paper units, not feet");
check(T.ckDec(5.333) === "5.33" && T.ckDec(30) === "30" && T.ckDec(0.5) === "0.5",
      "ckDec trims like the banner's confFt");

// ---- the style floor ------------------------------------------------------
near(T.ckStyleFloor(10000), 500, 0, "floor is 500 pt on a sparse sheet");
near(T.ckStyleFloor(100000), 1000, 0, "floor grows to 1% on a dense sheet");

// ---- a clean page queues nothing and pends nothing ------------------------
{
  const r = T.ckBuild(inp());
  check(r.entries.length === 0, "clean page: no entries");
  check(r.pending.length === 0, "clean page: no pending checks");
}

// ---- scale entries ---------------------------------------------------------
{
  const r = T.ckBuild(inp({ scale: { present: false }, ftPerIn: null }));
  const e = byId(r.entries, "scale");
  check(!!e && e.sev === "stop", "no scale queues as stop");
  near(e.w, 100000 * T.CK_W_PAGE, 0, "no-scale weight = whole page");
}
{
  const r = T.ckBuild(inp({ scale: Object.assign(inp().scale,
    { conflict: true, corroborated: false, barFt: 20, deltaPct: 50 }) }));
  const e = byId(r.entries, "scale");
  check(!!e && e.sev === "stop", "caption-vs-bar conflict queues as stop");
  check(e.text.indexOf("20 ft") >= 0 && e.text.indexOf("50%") >= 0,
        "conflict entry states both readings and the split");
}
{
  const s = inp().scale; s.source = "ocr"; s.corroborated = null;
  const r = T.ckBuild(inp({ scale: s }));
  const e = byId(r.entries, "scale");
  check(!!e && e.sev === "warn", "uncorroborated OCR scale queues as warn");
  near(e.w, 100000 * T.CK_W_OCR, 0, "OCR weight = 0.6 x page");
}
{
  const s = inp().scale; s.source = "ocr"; s.corroborated = true;
  const r = T.ckBuild(inp({ scale: s }));
  check(!byId(r.entries, "scale"),
        "an OCR scale the drawn bar corroborates does not queue");
}
{
  const s = inp().scale; s.multi = { n: 3, lo: 10, hi: 40, settled: false };
  const r = T.ckBuild(inp({ scale: s }));
  const e = byId(r.entries, "scales");
  check(!!e, "unsettled multi-scale queues");
  near(e.w, 100000 * T.CK_W_MULTI, 0, "unsettled multi weight = 0.5 x page");
  s.settled = undefined; s.multi.settled = true;
  const e2 = byId(T.ckBuild(inp({ scale: s })).entries, "scales");
  near(e2.w, 100000 * T.CK_W_MULTI_RANKED, 0, "ranked multi weight = 0.25 x page");
  s.operator = true;
  check(!byId(T.ckBuild(inp({ scale: s })).entries, "scales"),
        "a reviewer-picked scale on a multi-scale page does not queue (decided)");
}
{
  const r = T.ckBuild(inp({ tag: { agrees: false, implied: 60, ratio: 4, n: 3 } }));
  const e = byId(r.entries, "scaletag");
  check(!!e && e.sev === "stop", "printed-sf contradiction queues as stop");
  check(e.text.indexOf("3 area figures") >= 0 && e.text.indexOf("4.0x") >= 0,
        "sf entry carries count and error ratio");
  check(!byId(T.ckBuild(inp({ tag: { agrees: true, implied: 30, ratio: 1, n: 3 } }))
        .entries, "scaletag"), "agreeing sf tags do not queue");
}

// ---- datum + spots ---------------------------------------------------------
{
  const r = T.ckBuild(inp({ datum: { state: "checked", conflict: true,
    notes: [[100, 7605.25], [100, 7700]], used: [100, 7605.25] } }));
  const e = byId(r.entries, "datum");
  check(!!e && e.sev === "warn" && e.act.t === "ew",
        "datum conflict queues as warn and points at Cut / fill");
  near(e.w, 100000 * T.CK_W_PAGE, 0, "datum weight = whole page");
  check(e.text.indexOf("7605.25") >= 0 && e.text.indexOf("7700") >= 0,
        "datum entry states both of the sheet's own numbers");
}
{
  const r = T.ckBuild(inp({ spots: { state: "checked", n: 100, unresolved: 40 } }));
  const e = byId(r.entries, "spots");
  near(e.w, 100000 * 0.4, 1e-9, "unresolved weight = unresolved share of page");
  check(e.head.indexOf("40 of 100") >= 0, "unresolved entry has its denominator");
  check(!byId(T.ckBuild(inp({ spots: { state: "checked", n: 100, unresolved: 0 } }))
        .entries, "spots"), "0 unresolved queues nothing");
}
{
  const r = T.ckBuild(inp({ datum: { state: "pending" },
                            spots: { state: "pending", n: 0, unresolved: 0 } }));
  check(r.entries.length === 0 && r.pending.length === 1
        && r.pending[0].indexOf("not answered yet") >= 0,
        "unfetched elevation checks pend instead of queueing");
  const r2 = T.ckBuild(inp({ datum: { state: "failed" },
                             spots: { state: "failed", n: 0, unresolved: 0 } }));
  check(r2.pending.length === 1 && r2.pending[0].indexOf("could not run") >= 0,
        "failed elevation checks say so, not silence");
}

// ---- stale saved inputs ----------------------------------------------------
{
  const r = T.ckBuild(inp({ drops: { state: "checked", ew: 2, tr: 1,
                                     sigChanged: true } }));
  const e = byId(r.entries, "drops:ew"), t = byId(r.entries, "drops:tr");
  check(!!e && !!t, "ew and tr drops queue separately");
  near(e.w, 100000 * T.CK_W_DROPS, 0, "drops weight = 0.4 x page");
  check(e.text.indexOf("engine has changed") >= 0,
        "sig change is named on the entry");
  const p = T.ckBuild(inp({ drops: { state: "pending" } }));
  check(p.pending.some(x => x.indexOf("stale-input") >= 0),
        "unloaded sidecar pends the stale-input check");
}

// ---- ckDrops mirrors the apply arms ----------------------------------------
{
  const saved = { ew: { rows: [{ chain_id: 1 }, { chain_id: 99 }, null] },
                  ew_bound: { chain_id: 98 }, tr: { style_id: 7 } };
  const d = T.ckDrops(saved, [1, 2], [7]);
  check(d.ew === 2 && d.tr === 0,
        "missing contour chain + missing bound seed drop; live tr style does not");
  const d2 = T.ckDrops(saved, [1, 99, 98], []);
  check(d2.ew === 0 && d2.tr === 1, "renumbered trench style drops");
  const d3 = T.ckDrops({}, [], []);
  check(d3.ew === 0 && d3.tr === 0, "no saved record, no drops");
}

// ---- schedule mismatches ---------------------------------------------------
{
  const r = T.ckBuild(inp({ marks: [
    { code: "W1", n: 27, sched: 25 },
    { code: "D2", n: 4, sched: 4 },
    { code: "G9", n: 3, sched: undefined }] }));
  check(ids(r.entries).join(",") === "sym:W1",
        "only the contradicted mark queues; a matching or unquantified one does not");
  near(byId(r.entries, "sym:W1").w, 100000 * T.CK_W_SCHED + 2, 1e-9,
       "schedule weight = 0.35 x page + |delta| tiebreak");
  const p = T.ckBuild(inp({ marks: null }));
  check(p.pending.some(x => x.indexOf("press Count") >= 0),
        "Count never pressed pends the schedule check");
  // a schedule read on ANOTHER sheet counts the whole set: one sheet holding fewer is no
  // contradiction (check 4B, 2026-09-27: floor 1's 21 against the building's 148 read CHECK
  // on every floor); more than the schedule is one on any sheet
  const w = T.ckBuild(inp({ marks: [
    { code: "W1L", n: 21, sched: 148, setWide: true },
    { code: "W9", n: 30, sched: 28, setWide: true },
    { code: "W3", n: 21, sched: 28, setWide: false }] }));
  check(ids(w.entries).sort().join(",") === "sym:W3,sym:W9",
        "fewer than a set-wide schedule does not queue; more does, and a schedule on the sheet itself still does");
}
{
  // the Counts row says the same thing in words
  const at = src.indexOf("function symSetWide()");
  check(at > 0 && /info\.schedule_page !== state\.page/.test(src.slice(at, at + 300)),
        "symSetWide: a schedule read on another sheet of the set");
  const row = src.indexOf("for the whole set");
  check(row > 0 && /symSetWide\(\) && g\.n < g\.schedule_qty/.test(src.slice(row - 500, row)),
        "the Counts row: fewer than a set-wide schedule reads as a fact about the sheet, not CHECK");
}

// ---- per-style thresholds --------------------------------------------------
function sty(over) {
  return Object.assign({ sid: 5, pt: 9000, name: "dash1 12-6", legend: false,
    layerLeaf: "", named: false, included: true, weldPt: 0 }, over);
}
{
  const r = T.ckBuild(inp({ styles: [sty()] }));
  const e = byId(r.entries, "style:5");
  check(!!e && e.sev === "warn", "big unnamed unfiled style queues");
  near(e.w, 9000, 0, "unnamed weight = its own footage");
  check(e.head === "3.8k ft of linework has no name.",
        "unnamed entry reports real feet (9000pt @30 = 3750 ft)");
}
check(!byId(T.ckBuild(inp({ styles: [sty({ legend: true })] })).entries, "style:5"),
      "a legend-labelled style is decided, not queued");
check(!!byId(T.ckBuild(inp({ styles: [sty({ layerLeaf: "0" })] })).entries, "style:5"),
      'layer "0" counts as unfiled (AutoCAD default layer)');
check(!byId(T.ckBuild(inp({ styles: [sty({ layerLeaf: "C-ROAD-CNTR" })] })).entries,
      "style:5"), "a real layer name is a name - not queued");
check(!byId(T.ckBuild(inp({ styles: [sty({ named: true })] })).entries, "style:5"),
      "an operator-named style is decided, not queued");
check(!byId(T.ckBuild(inp({ styles: [sty({ included: false })] })).entries, "style:5"),
      "a style not in the takeoff is not queued");
check(!byId(T.ckBuild(inp({ styles: [sty({ pt: 800 })] })).entries, "style:5"),
      "below the 1%-of-page floor (1000 pt here): not queued");
check(!!byId(T.ckBuild(inp({ totalPt: 50000, styles: [sty({ pt: 800 })] })).entries,
      "style:5"), "the same 800 pt queues on a sparser sheet (floor 500)");
{
  const at = T.ckBuild(inp({ styles: [sty({ layerLeaf: "C-X", weldPt: 2500, pt: 10000 })] }));
  check(!byId(at.entries, "weld:5"), "welded fraction exactly 25% does NOT queue (must exceed)");
  const over = T.ckBuild(inp({ styles: [sty({ layerLeaf: "C-X", weldPt: 2600, pt: 10000 })] }));
  const e = byId(over.entries, "weld:5");
  check(!!e, "welded fraction 26% queues");
  near(e.w, 2600, 0, "weld weight = the inferred footage itself");
  check(e.head.indexOf("26%") === 0, "weld entry leads with the fraction");
  check(e.text.indexOf("1.1k ft") >= 0,
        "weld entry reports the inferred footage in feet (2600pt @30 = 1083)");
}

// ---- cross-source ranking --------------------------------------------------
{
  // all four page-wide warns on one page: datum > multi > drops > schedule
  const s = inp().scale; s.multi = { n: 2, lo: 10, hi: 30, settled: false };
  const r = T.ckBuild(inp({
    scale: s,
    datum: { state: "checked", conflict: true, notes: [[100, 100], [100, 200]], used: [100, 100] },
    drops: { state: "checked", ew: 1, tr: 0, sigChanged: false },
    marks: [{ code: "W1", n: 10, sched: 8 }],
  }));
  const ord = T.ckOrder(r.entries, [], T.CK_CAP, false);
  check(ids(ord.shown).join(",") === "datum,scales,drops:ew,sym:W1",
        "page-wide warn order: datum > multi-scale > stale inputs > schedule");
}
{
  // severity beats weight: a stop on a tiny page outranks a giant warn style
  const r = T.ckBuild(inp({ totalPt: 1000, ftPerIn: null,
    scale: { present: false },
    styles: [sty({ pt: 900000 })] }));
  const ord = T.ckOrder(r.entries, [], T.CK_CAP, false);
  check(ord.shown[0].id === "scale" && ord.shown[0].sev === "stop",
        "stop outranks a warn carrying 900x the footage");
}

// ---- cap, disclosure, check-offs -------------------------------------------
{
  const styles = [];
  for (let i = 0; i < 15; i++) styles.push(sty({ sid: i, pt: 20000 - i * 1000 }));
  const r = T.ckBuild(inp({ totalPt: 300000, styles: styles }));
  check(r.entries.length === 15, "15 qualifying styles build 15 entries");
  const ord = T.ckOrder(r.entries, [], T.CK_CAP, false);
  check(ord.shown.length === 12 && ord.more === 3 && ord.total === 15,
        "cap shows 12 and DISCLOSES the other 3");
  const all = T.ckOrder(r.entries, [], T.CK_CAP, true);
  check(all.shown.length === 15 && all.more === 0, "showAll opens the cap");
  check(ord.shown[0].w >= ord.shown[1].w, "entries rank by weight desc");
  // check one off: it sinks below every unchecked entry but stays listed
  const done = [ord.shown[0].id];
  const after = T.ckOrder(r.entries, done, T.CK_CAP, true);
  check(after.shown[after.shown.length - 1].id === done[0],
        "a checked-off entry sinks to the bottom, never vanishes");
  check(after.unchecked === 14, "unchecked count excludes the checked-off entry");
}
{
  // deterministic ties: same sev, same weight -> id order
  const es = [{ id: "b", sev: "warn", w: 5 }, { id: "a", sev: "warn", w: 5 }];
  const ord = T.ckOrder(es, [], 12, false);
  check(ids(ord.shown).join(",") === "a,b", "ties break on id, deterministically");
}

if (failures) { console.error(failures + " failure(s)"); process.exit(1); }
console.log("all checks passed");
