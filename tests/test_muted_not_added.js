// SHOWN, NOT IN THE TAKEOFF, LOOKS IT (coordinator ruling 2026-10-03, on the empty-takeoff round). The drawing shows
// the engine's picks while the takeoff is empty, so a full-colour line must never be read as counted:
//   * a family drawn but not in the takeoff paints MUTED - lower opacity, thinner than its plotted weight, no group
//     paint - and an added family paints in its full colour and weight, side by side on the same sheet;
//   * selecting a muted line still lights it (magenta) - the pointer, said by the line card;
//   * the on-sheet legend says "shown, not in takeoff" for them.
// draw()'s chain loop is lifted out of webui/app.js and run against a recording canvas (the technique of
// tests/test_hover_off_family.js).
//
// run: node tests/test_muted_not_added.js       (fails on 925b338: both families paint alike)
"use strict";
const fs = require("fs");
const path = require("path");
const SRC = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };

const LOOP = (function () {
  const NEEDLE = "for (const ch of state.result.chains) {";
  for (let i = SRC.indexOf(NEEDLE); i >= 0; i = SRC.indexOf(NEEDLE, i + 1)) {
    let d = 0;
    for (let k = SRC.indexOf("{", i); k < SRC.length; k++) {
      if (SRC[k] === "{") d++;
      else if (SRC[k] === "}" && --d === 0) {
        const body = SRC.slice(i, k + 1);
        if (body.indexOf("const hot =") >= 0) return body;
        break;
      }
    }
  }
  return "";
})();
check(!!LOOP, "draw()'s chain loop is locatable");
const CV_GHOST = (SRC.match(/const CV_GHOST\s*=\s*"([^"]+)"/) || [])[1];
const CV_SELECT = (SRC.match(/const CV_SELECT\s*=\s*"([^"]+)"/) || [])[1];

function run(over) {
  const strokes = [];
  const c = { _d: [], globalAlpha: 1, strokeStyle: "", lineWidth: 0, lineCap: "", lineJoin: "", _s: [],
    save() { this._s.push([this._d, this.globalAlpha, this.strokeStyle, this.lineWidth]); },
    restore() { const s = this._s.pop(); if (s) [this._d, this.globalAlpha, this.strokeStyle, this.lineWidth] = s; },
    setLineDash(d) { this._d = d; }, beginPath() {}, moveTo() {}, lineTo() {},
    stroke() { strokes.push({ sid: cur.sid, color: this.strokeStyle, alpha: this.globalAlpha, width: this.lineWidth }); } };
  const cur = { sid: null };
  const state = Object.assign({
    result: { chains: [{ chain_id: 1, style_id: 7, class: "solid", width: 1.0, points: [[0, 0], [100, 0]] },
                       { chain_id: 2, style_id: 9, class: "solid", width: 1.0, points: [[0, 10], [100, 10]] }],
              styles: [{ style_id: 7, class: "solid" }, { style_id: 9, class: "solid" }] },
    excluded: new Set(), isolate: null,
    include: new Map([[7, true], [9, false]]),        // 7 added; 9 shown (an engine's pick), not added
    hotStyle: null, hotChain: null, selected: null, multiSel: new Set(), faithful: true, view: { k: 1 },
  }, over || {});
  const scope = { ctx: c, state, f: 1, k: 1, CV_SELECT, CV_GHOST,
    famShown: () => true,                             // both drawn: the eye shows the engine's picks
    chainCssColor: (ch) => { cur.sid = ch.style_id; return ch.style_id === 7 ? "#2266ff" : "#22aa44"; },
    dashArray: () => [], gcol: null };
  const names = Object.keys(scope);
  // the stroke records which family it belongs to through the colour call of that chain
  new Function(...names, LOOP)(...names.map(n => scope[n]));
  return strokes;
}

{
  const s = run();
  const added = s.filter(x => x.sid === 7), out = s.filter(x => x.sid === 9);
  check(added.length === 1 && out.length === 1, "both families are drawn (one stroke each)");
  check(added[0].alpha === 1 && added[0].width >= 1.4 && added[0].color === "#2266ff",
    "the ADDED family paints in its full colour, full opacity, at least its plotted weight: " + JSON.stringify(added[0]));
  check(out[0].alpha <= 0.4 && out[0].width < added[0].width,
    "the shown-but-not-added family paints MUTED - faint and thinner: " + JSON.stringify(out[0]));
}
{
  // a wall-group paint (the structure panel's colours) is not put on a family that is not in the takeoff
  const s = run({});
  const g = new Map([[1, "#ff00ff"], [2, "#ff00ff"]]);
  const strokes = [];
  const c = { _d: [], globalAlpha: 1, strokeStyle: "", lineWidth: 0, _s: [], save() {}, restore() {}, setLineDash() {},
    beginPath() {}, moveTo() {}, lineTo() {}, stroke() { strokes.push({ color: this.strokeStyle, alpha: this.globalAlpha }); } };
  const state = { result: { chains: [{ chain_id: 2, style_id: 9, class: "solid", width: 1, points: [[0, 0], [9, 0]] }],
                            styles: [{ style_id: 9, class: "solid" }] }, excluded: new Set(), isolate: null,
                  include: new Map([[9, false]]), hotStyle: null, hotChain: null, selected: null, multiSel: new Set(),
                  faithful: true, view: { k: 1 } };
  const scope = { ctx: c, state, f: 1, k: 1, CV_SELECT, CV_GHOST, famShown: () => true,
    chainCssColor: () => "#22aa44", dashArray: () => [], gcol: g };
  new Function(...Object.keys(scope), LOOP)(...Object.values(scope));
  check(strokes.length === 1 && strokes[0].color === "#22aa44" && strokes[0].alpha <= 0.4,
    "no group paint on a family not in the takeoff: its own colour, muted: " + JSON.stringify(strokes));
  void s;
}
{
  // selecting a muted line lights it (the selection colour), as before
  const s = run({ selected: 2 });
  const sel = s.filter(x => x.color === CV_SELECT);
  check(sel.length === 1 && sel[0].alpha === 1, "a selected line of a muted family is lit in the selection colour");
}
// the on-sheet legend's words
{
  const i = SRC.indexOf("function renderMiniLegend()");
  const body = SRC.slice(i, SRC.indexOf("\n}\n", i));
  check(/"shown, not in takeoff"/.test(body) && /"dimmed: shown, not in takeoff"/.test(body),
    "the on-sheet legend says 'shown, not in takeoff' for the families drawn but not added");
}
if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all muted-not-added checks passed");
