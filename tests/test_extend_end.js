// EXTEND GROWS THE END THE OPERATOR MEANT (2026-09-10, Pierce Site Work
// call of 09-03: "it goes to the closest end point... funky", on screen).
//
// extendTo grew whichever end was nearer the DESTINATION click, so on a
// short line the end flipped with where the operator pointed. The end is
// now chosen from what the operator did to the line: a picked vertex that
// IS an end wins; else the end nearer where the line was clicked to select
// it; else the old nearest-to-destination rule. The armed status names the
// end, and after an extension the SAME end stays armed so E, click, E,
// click walks one end along.
//
// run: node tests/test_extend_end.js   (fails on babd771, passes after)
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

const helper = fnSrc("extendEndIndex");
check(!!helper, "an end-choice helper exists (extendEndIndex)");
const et = fnSrc("extendTo");
check(!!et && /extendEndIndex\(/.test(et) && !/dFirst < dLast/.test(et),
  "extendTo asks the helper which end grows instead of the nearer-to-destination rule");
check(/state\.selPt = atFirst \? 0 : pts\.length - 1;/.test(et) && /state\.clickPt = dest\.slice\(\);/.test(et),
  "after an extension the new end stays the armed one (selPt + clickPt move with it)");
const btn = js.slice(js.indexOf('$("extendBtn").onclick'), js.indexOf('$("extendBtn").onclick') + 1200);
check(/state\.extendEnd = /.test(btn) && /grows to your next click/.test(btn),
  "the armed status names the end that grows");
check(/state\.tool === "extend" && !state\.extendAnchor && state\.extendEnd !== null/.test(js),
  "draw rings the end that will grow while extend is armed");

// ---- run extendTo with stubs ------------------------------------------------
function run(setup) {
  const calls = [];
  const chains = new Map();
  const A = { chain_id: 1, style_id: 10, points: [[0, 0], [100, 0]], length_pt: 100 };
  chains.set(1, A);
  let next = 50;
  const state = Object.assign({ selected: 1, selPt: null, clickPt: null, extendEnd: null, extendAnchor: null, tool: "extend",
                                snapHit: null, snapMsg: "", excluded: new Set() }, setup);
  const s = {
    state,
    chainById: (id) => chains.get(id) || null,
    snapToChain: () => null,
    captureUndo: (label, ids) => calls.push(["undo", label]),
    fmtQty: (v) => Math.round(v) + " pt",
    materialize: (ch) => { const c = { ...ch, points: ch.points.map(p => p.slice()), chain_id: next++, custom: true }; chains.set(c.chain_id, c); return c; },
    snapWeld: () => null,
    updateSelBar: () => {},
    afterEdit: (t) => calls.push(["after", t]),
    renderStyles: () => {}, renderChips: () => {}, renderMiniLegend: () => {},
    chainLenPt: (c) => c.length_pt,
    setStatus: (t) => calls.push(["status", t]),
    draw: () => {},
  };
  const src = (helper || "function extendEndIndex(){return -1}") + "\n" + fnSrc("cancelExtend") + "\n" + et
    + "\nreturn { extendTo, extendEndIndex };";
  const names = Object.keys(s);
  const api = new Function(...names, src)(...names.map(k => s[k]));
  return { api, state, calls, chains };
}

// destination [55,30] is NEARER the right end (54.1 pt) than the left (62.6 pt)
// 1. the line was clicked near its LEFT end to select it -> the left end grows
{
  const r = run({ clickPt: [10, 0] });
  r.api.extendTo(55, 30);
  const c = r.chains.get(r.state.selected);
  check(c.points[0][0] === 55 && c.points[0][1] === 30,
    "clicked near the left end, pointed nearer the right: the LEFT end grew");
  check(Math.round(c.length_pt) === 163, "the added length is from the chosen end (62.6 pt), not the nearer one");
  check(r.state.selPt === 0 && r.state.clickPt[0] === 55, "the new left end is the armed end for the next E");
}
// 2. the RIGHT end's vertex was picked (selPt = last) even though the click was near the left
{
  const r = run({ clickPt: [10, 0], selPt: 1 });
  r.api.extendTo(20, 40);
  const c = r.chains.get(r.state.selected);
  check(c.points[c.points.length - 1][0] === 20 && c.points.length === 3 && c.points[0][0] === 0,
    "a picked end vertex wins over the selection click: the RIGHT end grew");
  check(r.state.selPt === 2, "the new right end is the armed end for the next E");
}
// 3. nothing known about how the line was selected -> the old nearer-to-destination rule
{
  const r = run({});
  r.api.extendTo(55, 30);
  const c = r.chains.get(r.state.selected);
  check(c.points[c.points.length - 1][0] === 55, "with no click or pick, the end nearer the destination grows (unchanged)");
}
// 4. a mid-line picked vertex is not an end -> falls through to the selection click
{
  const r = run({ clickPt: [95, 0], selPt: 1 });
  r.state.selPt = null; // a body pick is cleared by the click handler; the click point remains
  r.api.extendTo(40, 30);
  const c = r.chains.get(r.state.selected);
  check(c.points[c.points.length - 1][0] === 40, "clicked near the right end, pointed nearer the left: the RIGHT end grew");
}
// 5. the armed end set by the button is honoured by extendTo even if selPt changed meanwhile
{
  const r = run({ clickPt: [10, 0] });
  r.state.extendEnd = r.api.extendEndIndex(r.chains.get(1), null);
  check(r.state.extendEnd === 0, "the button arms the left end from the selection click");
  r.api.extendTo(90, 30);
  const c = r.chains.get(r.state.selected);
  check(c.points[0][0] === 90, "the ringed end is the one that grows, whatever the destination is nearer to");
  check(r.state.extendEnd === null && r.state.tool === null, "extend disarms after the click");
}

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all extend-end checks passed");
