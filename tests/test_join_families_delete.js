// JOIN ACROSS FAMILIES IN ONE PRESS; A POINT CAN LEAVE A THREE-POINT LINE
// (2026-09-10, both from the Pierce Site Work call of 09-03, on screen).
//
//   1. The detector files the two halves of one contour under two families
//      often enough that the demo needed Reclassify then Join on "all 50".
//      mergeEnds now moves the other piece into the SELECTED line's family
//      and merges - one gesture, ONE undo step - and says which family lost
//      the footage. A same-family join is unchanged.
//   2. deleteVertex refused any line with <= 3 points ("too few points
//      left"); a line needs two, so the guard is <= 2. A vertex click with
//      no drag no longer reports "point moved".
//
// run: node tests/test_join_families_delete.js
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
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

// ---- 1. join across families, run with stubs -------------------------------
{
  const me = fnSrc("mergeEnds");
  check(!!me && !/use Reclassify \(R\) first, then merge/.test(me),
    "JOIN: mergeEnds no longer refuses a different family");
  check(/tgt = reclassCore\(tgt, sel\.style_id, sel\);/.test(me),
    "JOIN: the other piece is moved into the SELECTED line's family before the merge");
  check(/captureUndo\(\(moved \? `join across families/.test(me),
    "JOIN: one undo step covers the reclass and the merge");
  check(/moved from \$\{moved\.from\} into \$\{moved\.to\}/.test(me),
    "JOIN: the status line says which family lost the footage");

  function scope(sameFamily) {
    const calls = [];
    const chains = new Map();
    const A = { chain_id: 1, style_id: 10, points: [[0, 0], [100, 0]], length_pt: 100 };
    const B = { chain_id: 2, style_id: sameFamily ? 10 : 20, points: [[104, 0], [200, 0]], length_pt: 96 };
    chains.set(1, A); chains.set(2, B);
    let next = 50;
    const state = { labels: new Map([[10, "EXISTING CONTOUR"], [20, "Unnamed d18"]]), excluded: new Set(),
                    result: { styles: [{ style_id: 10 }, { style_id: 20 }], chains: [A, B] }, selected: 1, selPt: null };
    const s = {
      state,
      chainById: (id) => chains.get(id) || null,
      familyName: (st) => st ? "family " + st.style_id : "",
      setStatus: (t, c) => calls.push(["status", t]),
      confirm: () => true,
      fmtLen: (v) => Math.round(v) + " pt",
      fmtQty: (v) => Math.round(v) + " pt",
      captureUndo: (label, ids) => calls.push(["undo", label, ids.slice()]),
      reclassCore: (ch, sid, donor) => { const c = { ...ch, points: ch.points.map(p => p.slice()), style_id: sid, chain_id: next++, custom: true }; chains.set(c.chain_id, c); calls.push(["reclass", ch.chain_id, sid]); return c; },
      materialize: (ch) => { const c = { ...ch, points: ch.points.map(p => p.slice()), chain_id: next++, custom: true }; chains.set(c.chain_id, c); return c; },
      cancelMerge: () => calls.push(["cancel"]),
      updateSelBar: () => {},
      afterEdit: (m) => calls.push(["after", m]),
      renderStyles: () => {}, renderChips: () => {}, renderMiniLegend: () => {},
    };
    const names = Object.keys(s);
    const fn = new Function(...names, me + "\nreturn mergeEnds;")(...names.map(n => s[n]));
    fn(A, B);
    return { calls, state, chains };
  }
  const x = scope(false);
  const undo = x.calls.filter(c => c[0] === "undo");
  check(undo.length === 1 && /join across families/.test(undo[0][1]) && undo[0][2].join(",") === "1,2",
    "JOIN, run: exactly ONE undo entry, labelled as a join across families, covering both pieces  (" + JSON.stringify(undo) + ")");
  check(x.calls.some(c => c[0] === "reclass" && c[1] === 2 && c[2] === 10),
    "JOIN, run: the other piece (2) was moved into the selected line's family (10)");
  const merged = x.chains.get(x.state.selected);
  check(merged && merged.style_id === 10 && merged.points.length === 4 && Math.round(merged.length_pt) === 200,
    "JOIN, run: the merged line is in family 10 with both pieces' points and 100 + 96 + 4 pt  (" + (merged && merged.points.length) + " pts, " + (merged && Math.round(merged.length_pt)) + " pt)");
  const after = x.calls.find(c => c[0] === "after");
  check(after && /moved from Unnamed d18 into EXISTING CONTOUR/.test(after[1]) && /Ctrl\+Z/.test(after[1]),
    "JOIN, run: the status names the families and the way back  (" + (after && after[1]) + ")");
  const y = scope(true);
  const undo2 = y.calls.filter(c => c[0] === "undo");
  check(undo2.length === 1 && /merge of 2 lines/.test(undo2[0][1]) && !y.calls.some(c => c[0] === "reclass"),
    "JOIN, run: a same-family join is unchanged - no reclass, the old undo label");
}

// ---- 2. point delete on a three-point line ---------------------------------
{
  const dv = fnSrc("deleteVertex");
  check(/if \(ch\.points\.length <= 2\) \{/.test(dv) && !/points\.length <= 3/.test(dv),
    "DELETE: the guard is two points, the minimum for a line, not three");
  function run(nPts) {
    const calls = [];
    const ch = { chain_id: 7, points: Array.from({ length: nPts }, (_, i) => [i * 10, i % 2 ? 5 : 0]), length_pt: 100 };
    const state = { selected: 7, selPt: 1 };
    const s = {
      state,
      chainById: () => ch,
      setStatus: (t, c) => calls.push(["status", t]),
      captureUndo: (l, ids) => calls.push(["undo", l]),
      materialize: (c) => c,
      polyLen: (pts) => pts.length,     // any positive proxy will do here
      updateSelBar: () => {},
      afterEdit: (m) => calls.push(["after", m]),
      fmtLen: (v) => v + "",
    };
    const names = Object.keys(s);
    const fn = new Function(...names, dv + "\nreturn deleteVertex;")(...names.map(n => s[n]));
    const r = fn();
    return { r, calls, ch };
  }
  const three = run(3);
  check(three.r === true && three.ch.points.length === 2 && three.calls.some(c => c[0] === "undo"),
    "DELETE, run: a point leaves a three-point line, leaving two  (" + three.ch.points.length + " left)");
  const two = run(2);
  check(two.r === true && two.ch.points.length === 2 && two.calls.some(c => c[0] === "status" && /two points/.test(c[1])),
    "DELETE, run: a two-point line refuses, with the reason  (" + JSON.stringify(two.calls[0]) + ")");
  const mu = js.slice(js.indexOf("if (state.dragPt) {\n    const moved = dragCap;"), js.indexOf("if (state.dragPt) {\n    const moved = dragCap;") + 700);
  check(/if \(ch && moved\) \{[\s\S]*?afterEdit\(`point moved/.test(mu) && /point \$\{state\.selPt \+ 1\} of \$\{ch\.points\.length\} picked/.test(mu),
    "CLICK: a vertex click with no drag picks the point and says so; 'point moved' only after a real move");
}

if (fails) { console.error("\n" + fails + " check(s) failed"); process.exit(1); }
console.log("\nall join-families / delete checks passed");
