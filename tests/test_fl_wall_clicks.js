// WALL CLICKS SET, NEVER TOGGLE (critic 5, minor 7, 2026-10-02). A walk run twice turned its Shift+clicked line back
// off: Shift+click toggled. Now a plain click ticks a type, Ctrl+click unticks it, Shift+click makes one line a wall,
// Ctrl+Shift+click makes it not one, and a click that would change nothing changes nothing (it says so).
//
// run: node tests/test_fl_wall_clicks.js
"use strict";
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
function rig(shownTypes, add, drop) {
  const W = { acts: [], said: [], vis: new Set(shownTypes) };
  const fl = { wallAdd: add.slice(), wallDrop: drop.slice() };
  const env = {
    flSync: () => fl, flChainAt: () => ({ chain_id: 42, style_id: 7, length_pt: 60 }),
    famShown: (sid) => W.vis.has(sid), setFamVis: (sid, on) => { if (on) W.vis.add(sid); else W.vis.delete(sid); },
    flAct: (a) => { W.acts.push(a); if (a.kind === "aids_line") { fl.wallAdd = fl.wallAdd.filter(x => x !== a.id); fl.wallDrop = fl.wallDrop.filter(x => x !== a.id);
      if (a.to === "wall") fl.wallAdd.push(a.id); if (a.to === "not") fl.wallDrop.push(a.id); } return Promise.resolve({}); },
    setStatus: (m) => W.said.push(m), renderStyles: () => {}, renderFl: () => {}, draw: () => {},
    flStyleSig: () => "sig", flJobFor: () => null, flFmt: (v) => String(v),
    state: { page: 0, result: { styles: [{ style_id: 7, class: "solid", width: 0.5 }] } },
  };
  const f = new Function(...Object.keys(env), fnSrc("flWallClick") + "\nreturn flWallClick;")(...Object.values(env));
  return { W, fl, click: (shift, untick) => f(1, 1, shift, untick) };
}
{
  const r = rig([], [], []);
  r.click(true, false);
  check(r.fl.wallAdd.includes(42), "Shift+click on a line of an unticked type: that line is a wall");
  r.click(true, false);
  check(r.fl.wallAdd.includes(42) && r.W.acts.length === 1 && /already a wall/.test(r.W.said.at(-1)),
    "Shift+click again: nothing changes, nothing is sent, it says it is already a wall (was: toggled back off)");
  r.click(true, true);
  check(!r.fl.wallAdd.includes(42) && r.W.acts.length === 2, "Ctrl+Shift+click: that line is not a wall");
  r.click(true, true);
  check(r.W.acts.length === 2 && /not a wall/.test(r.W.said.at(-1)), "Ctrl+Shift+click again: nothing changes");
}
{
  const r = rig([7], [], []);
  r.click(true, false);
  check(r.W.acts.length === 0 && /already a wall/.test(r.W.said.at(-1)), "a line of a ticked type, Shift+click: already a wall, nothing sent");
  r.click(true, true);
  check(r.fl.wallDrop.includes(42), "Ctrl+Shift+click on it: that line is not a wall (its type stays ticked)");
  r.click(true, false);
  check(!r.fl.wallDrop.includes(42) && r.W.acts.at(-1).to === "auto", "Shift+click: a wall again (back to its type)");
}
{
  const r = rig([], [], []);
  r.click(false, false);
  check(r.W.vis.has(7), "a plain click ticks the line's type");
  r.click(false, false);
  check(r.W.vis.has(7) && /already a wall/.test(r.W.said.at(-1)), "a plain click again: still ticked (no toggle)");
  r.click(false, true);
  check(!r.W.vis.has(7), "Ctrl+click unticks it");
}
console.log("\n" + fails + " failure(s)");
process.exit(fails ? 1 : 0);
