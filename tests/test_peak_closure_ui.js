// AN ASSUMED ELEVATION THE ESTIMATOR CANNOT SEE (2026-09-17).
//
// A surface built from contours stops at its highest contour, so a summit
// comes out flat and a pond bottom a floor (measured: -3.22% of the fill on
// the analytic dome at a 10 ft interval). The engine now puts one point
// inside the innermost ring, half a contour interval past it - the
// hand-takeoff convention, NOT a reading off the sheet. That has to be on
// screen with the area it covers, or it is exactly the confident wrong
// number this panel exists to prevent.
//
// The refusals are just as load-bearing. Oncor C-04's only closed ring is
// chain 685: five points, 28,600 SF, 569.9 on a sheet whose interval is
// 1 ft - a pad outline that caught a nearby spot label. It is NOT closed,
// and a skip nobody can see reads as "this sheet has no peaks".
//
// Dependency-free:  node tests/test_peak_closure_ui.js
"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "webui", "app.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "webui", "index.html"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "webui", "style.css"), "utf8");

let fails = 0;
const check = (ok, msg) => {
  if (ok) console.log("  ok: " + msg);
  else { fails++; console.error("  FAIL: " + msg); }
};

function fnSrc(name) {
  let j = SRC.indexOf("\nfunction " + name + "(");
  if (j < 0) return null;
  let d = 0;
  for (let k = SRC.indexOf("{", j); k < SRC.length; k++) {
    if (SRC[k] === "{") d++;
    else if (SRC[k] === "}" && --d === 0) return SRC.slice(j + 1, k + 1);
  }
  return null;
}
function mkEl() {
  return {
    innerHTML: "", hidden: false,
    classList: {
      _s: new Set(["off"]),
      add(...c) { c.forEach(x => this._s.add(x)); },
      remove(...c) { c.forEach(x => this._s.delete(x)); },
      contains(c) { return this._s.has(c); },
      toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); },
    },
  };
}

const proofSrc = fnSrc("renderEwProof");
const closeSrc = fnSrc("closureLine");
check(!!closeSrc, "app.js has closureLine()");
check(/\.ep-closure/.test(CSS), "style.css styles the closure disclosure");
// AT LEAST the version this change shipped in. Pinning the exact number
// makes every later bump fail a test that has nothing to do with it - this
// one broke on v=206 the same afternoon it was written.
const ver = (re) => Number((HTML.match(re) || [])[1] || 0);
check(ver(/app\.js\?v=(\d+)/) >= 205 && ver(/style\.css\?v=(\d+)/) >= 101,
  "index.html busts the cache for both changed files  (app v"
  + ver(/app\.js\?v=(\d+)/) + ", css v" + ver(/style\.css\?v=(\d+)/) + ")");

function render(j) {
  const el = mkEl();
  new Function("$", "esc",
    (proofSrc || "") + "\n" + (closeSrc || "") + "\nreturn renderEwProof;")(
    () => el,
    (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;"))(j);
  return el;
}

// the shapes the engine actually sends (scratchpad/score_closure.py)
const SUMMIT = {
  surface: "existing", kind: "summit", ring_z: 552, z: 552.5, area_sf: 9503,
  source: "convention", applied: true, closed: true, interval_ft: 1,
  why: "nothing is printed inside this summit: carried half a contour interval "
     + "(0.5 ft) above the 552 ring, the hand-takeoff convention - type the height "
     + "if the sheet or the survey says otherwise",
};
const PAD = {
  surface: "proposed", kind: "not closed", ring_z: 569.9, z: null, area_sf: 28605,
  source: "refused", applied: false, closed: false, interval_ft: 1,
  why: "drawn with 4 vertices - a contour is a polyline of many; this is a pad or "
     + "structure outline that caught a nearby elevation",
};
const PROOF = { verdict: "pass", checks: [
  { id: "grid", state: "pass", text: "on a 1 ft grid the same surfaces give 2,055 cut" },
] };

{
  const h = render({ proof: PROOF, closures: [SUMMIT] }).innerHTML;
  check(/Peaks and low points: carried the surface to the top of 1 high point/.test(h),
    "the summary says what was carried, in one line  (" + h.slice(-260, -120) + ")");
  check(/assumed/.test(h) && /type a spot to override/.test(h),
    "...and that it is an assumption the estimator can override");
  check(/552 ring carried to <b>552\.5<\/b> over 9,503 SF/.test(h),
    "the folded detail gives the height and the area it covers");
  check(/hand-takeoff convention/.test(h),
    "...and the engine's own reason travels with it");
  check(/<details class="ep-closure">/.test(h) && /<summary>/.test(h),
    "load-bearing detail is folded, not deleted");
}
{
  const h = render({ proof: PROOF, closures: [PAD] }).innerHTML;
  check(/left 1 ring open/.test(h),
    "a ring that was NOT closed is stated, not swallowed  (" + h.slice(-300, -160) + ")");
  check(/569\.9 ring, 28,605 SF/.test(h) && /pad or structure outline/.test(h),
    "...with the area at stake and the reason an estimator can check");
  check(!/assumed/.test(h),
    "...and nothing was assumed, so it does not claim an assumption");
}
{
  // the disclosure must outlive the proof block: a sheet whose spots are
  // all wall tags has no checks to show and would otherwise swallow it
  const el = render({ closures: [SUMMIT] });
  check(!el.classList.contains("off") && /1 high point/.test(el.innerHTML),
    "a page with no proof block still shows what was assumed");
  const bare = render({ proof: PROOF });
  check(!/ep-closure/.test(bare.innerHTML),
    "a page with no closed ring says nothing about peaks");
  const empty = render({});
  check(empty.classList.contains("off") && empty.innerHTML === "",
    "and an empty reply leaves the block off entirely");
}
{
  const h = render({ proof: PROOF, closures: [SUMMIT, PAD,
    Object.assign({}, SUMMIT, { kind: "low point", ring_z: 5803, z: 5802.5 })] }).innerHTML;
  check(/1 high point and 1 low point/.test(h) && /left 1 ring open/.test(h),
    "several rings read as a sentence, not a list of codes  (" + h.slice(-340, -200) + ")");
}

console.log(fails ? "\n" + fails + " FAILED" : "\nall passed");
process.exit(fails ? 1 : 0);
