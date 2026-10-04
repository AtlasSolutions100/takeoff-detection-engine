// TRENCH WORKS ON AN EMPTY TAKEOFF (critic empty-1, MAJOR-5). The Trench panel's run builder and its "pick the pipe
// run" list read only the styles IN the takeoff. While the takeoff started full that was every engine pick; now it
// starts empty, so on a new sheet the panel said "no utility line family is ticked on this sheet", listed no run and
// offered nothing to pick - and a tick made with the panel open changed nothing until it was closed and opened again.
//
// The cure built (the simpler of the two offered): the panel offers the sheet's utility lines WHETHER THEY ARE IN THE
// TAKEOFF OR NOT - the families the engine picks (what was listed after every Detect until now) plus anything the
// person added - and the picker lists every line family on the sheet. Pinned here, on the real functions:
//   1. with nothing in the takeoff, the run builder is asked about the engine-picked utility families (and sent their
//      lines); a family the engine does not pick (annotation, a short solid) joins only when the person adds it;
//   2. the runs come back and are listed: no "nothing is ticked" dead end;
//   3. a sheet with no family named as a utility says what to do (pick the pipe's family below);
//   4. the picker is built from every style with live lines, not from the takeoff.
//
// run: node tests/test_trench_empty_takeoff.js        (fails on 888b2c1)
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(name) {
  let start = js.indexOf("\nfunction " + name + "(");
  if (start < 0) start = js.indexOf("\nasync function " + name + "(");
  if (start < 0) return "function " + name + "() { return undefined; }   // not in this tree";
  start += 1;
  let depth = 0;
  for (let j = js.indexOf("{", start); j < js.length; j++) {
    if (js[j] === "{") depth++;
    else if (js[j] === "}" && --depth === 0) return js.slice(start, j + 1);
  }
  return "";
}
const constLine = (name) => { const i = js.indexOf("const " + name + " = "); return js.slice(i, js.indexOf("\n", i)); };

const STYLES = [
  { style_id: 1, class: "dashed", color: [0, 0, 0], length_pt: 4000, layer: "O-PIPE", suggested_label: "O-PIPE" },             // utility, engine pick
  { style_id: 2, class: "dashed", color: [0, 0, 0], length_pt: 2500, layer: "P-STORM", suggested_label: "STORM DRAIN" },      // utility, engine pick
  { style_id: 3, class: "annotation", color: [0, 0, 0], length_pt: 900, layer: "O-PIPE-LBL", suggested_label: "O-PIPE-LBL" }, // named like one, not picked
  { style_id: 4, class: "solid", color: [0, 0, 0], length_pt: 300, layer: "P-WATER", suggested_label: "WATER" },              // utility, short solid: not picked
  { style_id: 5, class: "solid", color: [0, 0, 0], length_pt: 9000, layer: "P1-CONCRETE", suggested_label: "P1-CONCRETE" },   // an engine pick, not a utility
  { style_id: 6, class: "dashed", color: [0, 0, 0], length_pt: 800, layer: "O-PIPE-TEXT", suggested_label: "O-PIPE-TEXT" },   // text: never a utility
];
function page(styles) {
  const posts = [];
  const state = { job: "j1", page: 0, gen: 1, result: { styles, chains: [], scale: { ft_per_paper_inch: 20 } },
                  include: new Map(), labels: new Map(), excluded: new Set(),
                  tr: { split: false, added: new Set(), results: {}, runInputs: {}, run_key: null, run_id: null, style_id: null },
                  trRuns: null };
  for (const s of styles) {
    state.include.set(s.style_id, false);                        // as loadResult leaves it: nothing in the takeoff
    state.labels.set(s.style_id, s.suggested_label);
    state.result.chains.push({ chain_id: 100 + s.style_id, style_id: s.style_id, class: s.class, points: [[0, 0], [s.length_pt, 0]] });
  }
  const env = {
    state, sgSuggested: () => false,
    fetch: async (url, opt) => {
      const body = JSON.parse(opt.body);
      posts.push({ url, body });
      return { ok: true, status: 200, json: async () => ({ runs: body.style_ids.map((sid, i) => ({ id: i + 1, name: "R" + (i + 1), style_id: sid, points: [] })),
                                                          structures: [], systems: [], notes: [] }) };
    },
    trRunKey: (r) => "run:" + r.id, trRunByKey: () => null, trRunById: (id) => ((state.trRuns && state.trRuns.runs) || []).find(r => r.id === id) || null,
    trClearItems() {}, trCalcRun: () => null, trApplyItems() {}, renderItemized() {},
  };
  const C = new Function(...Object.keys(env),
    [constLine("TR_UTIL_RX"), constLine("TR_NOT_UTIL_RX"), fnSrc("includeSeed"), fnSrc("trOffered"), fnSrc("trUtilityStyleIds"), fnSrc("trLoadRuns")].join("\n")
    + "\nreturn { includeSeed, trOffered, trUtilityStyleIds, trLoadRuns };")(...Object.values(env));
  return { C, state, posts };
}

(async () => {
  // ---- 1 + 2. nothing in the takeoff: the engine-picked utility families are offered, and their runs listed
  {
    const p = page(STYLES);
    check(JSON.stringify(p.C.trUtilityStyleIds()) === "[1,2]",
      "an EMPTY takeoff: the run builder is asked about the engine-picked utility families (O-PIPE, STORM DRAIN): " + JSON.stringify(p.C.trUtilityStyleIds()));
    await p.C.trLoadRuns();
    check(p.posts.length === 1 && /\/runs$/.test(p.posts[0].url) && JSON.stringify(p.posts[0].body.style_ids) === "[1,2]"
      && p.posts[0].body.chains.length === 2,
      "the panel asks the server for their runs and sends their lines (it asked nothing and said 'no utility line family is ticked')");
    check(p.state.trRuns && (p.state.trRuns.runs || []).length === 2 && !/ticked/.test(JSON.stringify(p.state.trRuns.notes || [])),
      "the runs are listed: " + JSON.stringify((p.state.trRuns.runs || []).map(r => r.name)));
    // the label look-alike (annotation) and the short solid are not engine picks: offered only once the person adds them
    p.state.include.set(4, true);
    check(JSON.stringify(p.C.trUtilityStyleIds()) === "[1,2,4]", "a utility family the engine does not pick joins when the person adds it (WATER)");
    p.state.include.set(3, true);
    check(JSON.stringify(p.C.trUtilityStyleIds()) === "[1,2,3,4]", "...and so does anything they add whose name is a utility's");
    p.state.include.set(5, true); p.state.include.set(6, true);
    check(JSON.stringify(p.C.trUtilityStyleIds()) === "[1,2,3,4]", "a family that is not a utility (concrete), or is its text, is never listed by name");
    // the family picked in the select reaches the run builder whatever it is called, in the takeoff or not
    p.state.include.set(5, false);
    p.state.tr.style_id = 5;
    check(p.C.trUtilityStyleIds().includes(5), "the family picked in 'pick the pipe run' joins the runs, in the takeoff or not");
  }
  // ---- 3. no family named as a utility: said, with what to do
  {
    const p = page([STYLES[4]]);
    await p.C.trLoadRuns();
    const note = ((p.state.trRuns || {}).notes || [])[0] || "";
    check(p.posts.length === 0 && /pick the pipe's line family below/.test(note) && !/ticked/.test(note),
      "a sheet with no family named as a utility says what to do: '" + note + "'");
  }
  // ---- 4. the picker lists every line family with live lines, in the takeoff or not
  {
    const rt = fnSrc("renderTr");
    const pick = rt.slice(rt.indexOf('sel.name = "tr-style"'), rt.indexOf("sel.onchange"));
    check(pick.length > 0 && !/state\.include\.get\(st\.style_id\)\) continue/.test(pick),
      "'pick the pipe run' no longer skips a family that is not in the takeoff");
    check(/trOffered\(/.test(pick) && /live\.get\(st\.style_id\)/.test(pick),
      "...it lists every family with live lines, the offered ones first");
    check(!/no utility line family is ticked|in the ticked utility families/.test(js),
      "no sentence of the panel tells the person a family must be ticked");
  }
  if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
  console.log("all trench / empty-takeoff checks passed");
})().catch(e => { console.error("FAIL: " + (e && e.stack || e)); process.exit(1); });
