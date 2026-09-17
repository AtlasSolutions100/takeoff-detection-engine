// WHAT BACKS THIS NUMBER, on the panel (2026-09-17).
//
// A client on a live job: "we can't set a solid LOD -> Calculate cut and
// fill path, so we get no defensible CY. I won't invent volumes." The
// volume kernel is exact where the evidence is complete (measured: 0.00%
// on an analytic plane, +-0.4% across grid cell sizes), so what an
// estimator needs is not a better kernel but the checks on the inputs -
// and those were computed nowhere and shown nowhere.
//
// renderEwProof prints the reply's `proof`: the surface against the
// elevations the sheet itself prints as points, the same rows through the
// other surface model and a finer grid, and what region was counted. A
// check that CANNOT run says so and is never counted as a pass.
//
// Dependency-free:  node tests/test_volume_proof_ui.js
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
  if (j < 0) j = SRC.indexOf("\nasync function " + name + "(");
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

check(/id="ewProof"/.test(HTML), "index.html has the #ewProof slot");
check(/\.ew-proof\s*\{/.test(CSS) && /\.ew-proof\.v-warn/.test(CSS),
  "style.css styles it, with a state for a warning");
const src = fnSrc("renderEwProof");
check(!!src, "app.js has renderEwProof()");
// the call site is the Calculate handler, right where the basis block is
// rendered from the same reply
check(/renderEwBasis\(j, body\);\s*\n\s*renderEwProof\(j\);/.test(SRC),
  "...and Calculate renders it from the same reply as the basis block");

function render(proof) {
  const el = mkEl();
  new Function("$", "esc", (src || "function renderEwProof() {}") + "\nreturn renderEwProof;")(
    () => el, (s) => String(s))({ proof });
  return el;
}

const WARN = {
  verdict: "warn",
  limits: { residual_median_ft: 0.25, residual_bias_ft: 0.15, method_spread_pct: 5 },
  checks: [
    { id: "spots", state: "warn", text: "the existing surface sits 0.78 ft from this sheet's own 7 printed ground spots (median), bias +0.91 ft, worst 1.78 ft" },
    { id: "model", state: "warn", text: "read with the other surface model (clamped_ct) the same rows give 1,496 cut and 11,019 fill - 27.3% from this answer" },
    { id: "grid", state: "pass", text: "on a 1 ft grid instead of 2 ft the same surfaces give 2,055 cut - 0.3% from this answer" },
    { id: "region", state: "warn", text: "no limit of work is set, so the region is wherever the two surfaces overlap - that is not the graded area" },
  ],
  spot_residual: {
    existing: { side: "existing", n_checked: 7, median_abs_ft: 0.78, bias_ft: 0.91,
                worst: [{ x: 2330.8, y: 1127.4, tag: "ME", printed_ft: 583.5, surface_ft: 581.72, diff_ft: 1.78 }] },
    proposed: { side: "proposed", n_checked: 0, why: "this sheet prints no ground spot elevation on the proposed side" },
  },
};
{
  const el = render(WARN);
  const h = el.innerHTML;
  check(!el.classList.contains("off") && el.classList.contains("v-warn"),
    "a warning verdict shows the block and marks it");
  check(/1 of 4 checks pass/.test(h) && /check these before you bid it/.test(h),
    "it counts the checks that passed and says what to do  (" + h.slice(0, 110) + ")");
  check(/0\.78 ft from this sheet's own 7 printed ground spots/.test(h)
    && /27\.3% from this answer/.test(h) && /not the graded area/.test(h),
    "...and prints every check in the engine's own words");
  check(/worst point: the sheet prints <b>583\.5<\/b> \(ME\) where our existing surface reads <b>581\.72<\/b>/.test(h)
    && /we are low by <b>1\.78 ft<\/b>/.test(h),
    "the worst point is named so an estimator can walk to it on the sheet");
}
{
  const el = render({ verdict: "pass", checks: [
    { id: "spots", state: "pass", text: "the existing surface sits 0.05 ft from this sheet's own 12 printed ground spots (median), bias +0.01 ft, worst 0.12 ft" },
    { id: "model", state: "pass", text: "read with the other surface model the same rows give 2,050 cut - 0.3% from this answer" },
    { id: "grid", state: "pass", text: "on a 1 ft grid the same surfaces give 2,055 cut - 0.1% from this answer" },
    { id: "region", state: "pass", text: "the volume is bounded by the limit of work, so only ground inside it counts" },
  ] });
  check(el.classList.contains("v-pass") && /4 of 4 checks pass/.test(el.innerHTML)
    && /every check passes/.test(el.innerHTML),
    "all four passing reads as all four passing");
}
{
  // THE RULE THAT MATTERS: a check that could not run is not a pass.
  const el = render({ verdict: "cannot check", checks: [
    { id: "spots", state: "cannot check", text: "this sheet prints no ground spot elevation to check the surface against - the volume rests on the contour lines alone" },
    { id: "model", state: "pass", text: "read with the other surface model the same rows give 8,048 cut - 5.6% from this answer" },
    { id: "grid", state: "pass", text: "on a 1 ft grid the same surfaces give 8,518 cut - 0.1% from this answer" },
    { id: "region", state: "pass", text: "the volume is bounded by the limit of work" },
  ] });
  const h = el.innerHTML;
  check(/3 of 4 checks pass/.test(h) && /not everything could be checked/.test(h)
    && el.classList.contains("v-warn"),
    "'cannot check' is counted as neither passed nor ignored  (" + h.slice(0, 120) + ")");
  check(/rests on the contour lines alone/.test(h), "...and it says what is missing");
}
{
  const el = render(null);
  check(el.classList.contains("off") && el.innerHTML === "",
    "no proof in the reply (an older server, a refusal): the block stays out of the way");
}

if (fails) { console.error("\n" + fails + " proof-panel check(s) FAILED"); process.exit(1); }
console.log("\nall proof-panel checks passed");
