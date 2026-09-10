// TRIM AT A MATCH LINE (2026-09-10, Pierce Site Work call of 09-03: Jacob
// trims every run at the match line so a pipe that carries on to the next
// sheet is not counted twice; before this it was Split then Delete, one
// crossing line at a time).
//
// T arms the tool; click the printed match line (straight only) or two
// points to draw one; click the side to KEEP. Every shown, included line is
// cut at the infinite match line: kept pieces become new lines in the same
// family, far pieces and lines wholly beyond it leave the count. Hidden and
// already-removed lines are untouched. One undo step.
//
// run: node tests/test_trim_matchline.js   (fails on ed054fd, passes after)
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
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

// ---- wiring -----------------------------------------------------------------
check(/id="trimBtn"/.test(html), "the toolkit has a Trim button");
check(/<kbd>T<\/kbd>/.test(html), "the keyboard help lists T");
check(/k === "t"\) \{ e\.preventDefault\(\); \$\("trimBtn"\)\.click\(\)/.test(js), "T arms the tool without a selection");
check(/state\.tool === "trim" && state\.trim\) \{\s*const \[px, py\] = ptFromEvent\(e\);\s*return trimClick\(px, py\);/.test(js),
  "a canvas click goes to the trim tool while it is armed");
check(/state\.tool === "trim" && state\.trim\) \{[\s\S]{0,1200}ctx\.setLineDash/.test(js), "draw runs the match line out across the sheet");
check(/state\.tool !== "trim"\) return;\s*if \(e\.key === "Escape"\)/.test(js), "Esc puts the tool away");

// ---- geometry, run with stubs ---------------------------------------------
const names = ["trimStraight", "trimSideOf", "trimPieces", "trimClone", "trimRemove", "trimApply", "trimDisarm", "trimClick"];
const missing = names.filter(n => !fnSrc(n));
check(!missing.length, "trim functions exist: " + (missing.length ? "missing " + missing.join(", ") : "all"));

function scope() {
  const calls = [];
  const A = { chain_id: 1, style_id: 10, points: [[0, 0], [200, 0]], length_pt: 200 };          // crosses x=100
  const B = { chain_id: 2, style_id: 10, points: [[150, 10], [180, 10]], length_pt: 30 };       // wholly beyond
  const C = { chain_id: 3, style_id: 10, points: [[10, 10], [50, 10]], length_pt: 40 };         // wholly kept
  const D = { chain_id: 4, style_id: 20, points: [[150, 20], [190, 20]], length_pt: 40 };       // hidden family, beyond
  const E = { chain_id: 5, style_id: 10, points: [[120, 30], [190, 30]], length_pt: 70 };       // already removed
  const F = { chain_id: 6, style_id: 10, points: [[60, 40], [160, 40]], length_pt: 100, custom: true }; // custom, crosses
  const G = { chain_id: 7, style_id: 10, points: [[0, 20], [120, 20], [80, 40], [200, 40]], length_pt: 284.72 }; // crosses twice
  const M = { chain_id: 8, style_id: 30, points: [[100, -50], [100, 300]], length_pt: 350 };    // the printed match line
  const chains = [A, B, C, D, E, F, G, M];
  const state = { result: { chains, styles: [] }, excluded: new Set([5]), chainLabels: new Map([[7, "SEWER"]]),
                  nextChainId: 100, multiSel: new Set([1]), selected: 1, selPt: 0, clickPt: [1, 1], tool: "trim",
                  trim: { cut: null, first: null, chain: null }, view: { k: 1 } };
  const s = {
    state,
    famShown: (sid) => sid !== 20,
    chainHitAt: (px, py) => Math.abs(px - 100) < 6 ? M : null,
    captureUndo: (label, ids) => calls.push(["undo", label, ids.slice()]),
    chainLenPt: (c) => c.length_pt,
    fmtQty: (v) => Math.round(v) + " pt",
    updateSelBar: () => {},
    afterEdit: (t) => calls.push(["after", t]),
    setStatus: (t, c) => calls.push(["status", t, c]),
    renderStyles: () => {}, renderChips: () => {}, renderMiniLegend: () => {}, draw: () => {},
    $: () => ({ classList: { add() {}, remove() {} } }),
    IMG_DPI: 130,
  };
  const src = fnSrc("distToSeg") + "\n" + fnSrc("polyLen") + "\n" + names.map(fnSrc).join("\n")
    + "\nreturn { trimStraight, trimSideOf, trimPieces, trimApply, trimClick };";
  const keys = Object.keys(s);
  const api = new Function(...keys, src)(...keys.map(k => s[k]));
  return { api, state, calls, chains, A, B, C, D, E, F, G, M };
}

if (!missing.length) {
  // straightness
  {
    const { api } = scope();
    check(!!api.trimStraight([[0, 0], [50, 0.4], [100, 0]]), "a nearly straight printed line is accepted as a match line");
    check(api.trimStraight([[0, 0], [50, 30], [100, 0]]) === null, "a bent line is refused");
    check(api.trimStraight([[0, 0], [3, 0]]) === null, "a stub under 5 pt is refused");
    check(Math.sign(api.trimSideOf([[100, 0], [100, 10]], [50, 5])) === -Math.sign(api.trimSideOf([[100, 0], [100, 10]], [150, 5])),
      "the two sides of the match line have opposite signs");
  }
  // pieces: a double crossing yields two kept runs ending ON the line
  {
    const { api } = scope();
    const cut = [[100, 0], [100, 10]];
    const keepSign = Math.sign(api.trimSideOf(cut, [50, 0]));
    const p = api.trimPieces([[0, 20], [120, 20], [80, 40], [200, 40]], cut, keepSign);
    check(p.keep.length === 2 && p.drop.length === 2, "a line crossing twice splits into 2 kept and 2 dropped runs");
    check(p.keep[0][p.keep[0].length - 1][0] === 100 && p.keep[1][0][0] === 100 && p.keep[1][p.keep[1].length - 1][0] === 100,
      "kept runs end exactly on the match line");
  }
  // the whole gesture: click the printed match line, then the keep side
  {
    const r = scope();
    r.api.trimClick(101, 50);
    check(r.state.trim.cut && r.state.trim.chain === 8, "clicking a printed straight line makes it the match line");
    const out = r.api.trimClick(30, 5);     // keep the LEFT side (x < 100)
    const alive = r.state.result.chains.filter(c => !r.state.excluded.has(c.chain_id));
    check(out && out.crossing === 3 && out.beyond === 1, `3 lines cut and 1 removed beyond (got ${JSON.stringify(out)})`);
    check(r.state.excluded.has(2) && r.state.excluded.has(1) && r.state.excluded.has(7), "the far-only line and the cut originals leave the count");
    check(!r.state.result.chains.includes(r.F) && !r.state.excluded.has(6), "a custom crossing line is spliced out, not excluded");
    check(!r.state.excluded.has(3) && !r.state.excluded.has(4) && !r.state.excluded.has(8), "a wholly kept line, a hidden family's line, and the match line itself are untouched");
    const news = alive.filter(c => c.chain_id >= 100);
    check(news.length === 4, `4 kept pieces were made (A, F, G twice) - got ${news.length}`);
    const a1 = news.find(c => c.points[0][0] === 0 && c.points[0][1] === 0);
    check(a1 && a1.points.length === 2 && a1.points[1][0] === 100 && Math.round(a1.length_pt) === 100 && a1.style_id === 10 && a1.custom === true,
      "A's kept piece runs 0..100 in the same family, 100 pt");
    const gs = news.filter(c => c.points[0][1] === 20 || c.points[0][1] === 30);
    check(gs.length === 2 && gs.every(c => r.state.chainLabels.get(c.chain_id) === "SEWER"), "G's two kept pieces carry its name");
    check(Math.abs(news.reduce((t, c) => t + c.length_pt, 0) + out.dropped - (200 + 100 + 284.72 + 30)) < 0.5,
      "kept + dropped footage equals the footage of every line touched");
    const undo = r.calls.find(c => c[0] === "undo");
    check(undo && undo[2].length === 4 && undo[1].includes("3 cut, 1 removed"), "one undo step names every touched line");
    const after = r.calls.find(c => c[0] === "after");
    check(after && /3 line\(s\) cut \(4 piece\(s\) kept\), 1 removed beyond it, \d+ pt dropped/.test(after[1]),
      "the status counts what was cut, kept, removed and dropped");
    check(r.state.tool === null && r.state.trim === null && r.state.selected === null && !r.state.multiSel.has(1),
      "the tool disarms, the selection clears, and the cut line leaves the multi-selection");
  }
  // drawn match line, keep the RIGHT side; nothing to do says so and changes nothing
  {
    const r = scope();
    r.api.trimClick(300, -10);   // off every line: first point
    check(r.state.trim.first && !r.state.trim.cut, "a click on empty sheet starts a drawn match line");
    r.api.trimClick(300, 10);
    check(r.state.trim.cut && r.state.trim.chain === null, "the second click finishes it");
    const out = r.api.trimClick(50, 5);   // keep x < 300: everything
    check(out === null && r.calls.some(c => c[0] === "status" && /nothing changed/.test(c[1])) && !r.calls.some(c => c[0] === "undo"),
      "with nothing beyond the line, no undo step is pushed and the status says nothing changed");
    check(r.state.tool === null, "the tool still disarms");
  }
}

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all trim checks passed");
