// THE REQUIRED-INPUT CONTRACT ON SCREEN (2026-09-17).
//
// "Once all required information is inputted the numbers are accurate" is
// only a promise the software can keep if it SAYS what it still needs. The
// panel prints every input the volume depends on as supplied / assumed /
// missing, and reads READY only when nothing required is either of the
// last two. An assumed default can be adopted as the estimator's own
// answer with one button, which lands in the body as `confirmed`.
//
// Dependency-free:  node tests/test_bid_readiness_ui.js
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
  const el = {
    innerHTML: "", _handlers: [],
    classList: {
      _s: new Set(["off"]),
      add(...c) { c.forEach(x => this._s.add(x)); },
      remove(...c) { c.forEach(x => this._s.delete(x)); },
      contains(c) { return this._s.has(c); },
      toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); },
    },
    // enough of the DOM to run the confirm wiring: find data-confirm ids
    querySelectorAll(sel) {
      if (!/data-confirm/.test(sel)) return [];
      const ids = [...el.innerHTML.matchAll(/data-confirm="([^"]+)"/g)].map(m => m[1]);
      return ids.map(id => ({
        dataset: { confirm: id },
        addEventListener(_e, fn) { el._handlers.push({ id, fn }); },
      }));
    },
  };
  return el;
}

check(/id="ewReady"/.test(HTML), "index.html has the #ewReady slot");
check(/\.ew-ready\s*\{/.test(CSS) && /\.ew-ready\.r-ready/.test(CSS)
  && /\.ew-ready\.r-open/.test(CSS),
  "style.css styles it with a state for ready and a state for open");
// AT LEAST the version this shipped with - pinning it exactly makes every
// later bump fail a test that has nothing to do with it.
const ver = (re) => Number((HTML.match(re) || [])[1] || 0);
check(ver(/app\.js\?v=(\d+)/) >= 206 && ver(/style\.css\?v=(\d+)/) >= 102,
  "index.html busts the cache for both changed files  (app v"
  + ver(/app\.js\?v=(\d+)/) + ", css v" + ver(/style\.css\?v=(\d+)/) + ")");
const src = fnSrc("renderEwReady");
check(!!src, "app.js has renderEwReady()");
check(/renderEwProof\(j\);\s*\n\s*renderEwReady\(j\);/.test(SRC),
  "...and it is rendered from the same reply as the proof block");
check(/confirmed: \(state\.ewConfirmed \|\| \[\]\)\.slice\(\)/.test(SRC),
  "the body sends what the estimator has confirmed, every time");

const CONFIRMABLE = SRC.slice(SRC.indexOf("const CONFIRMABLE"),
  SRC.indexOf("}", SRC.indexOf("const CONFIRMABLE")) + 1);
const CONF_FN = fnSrc("ewConfirmInput");
check(!!CONF_FN && /state\.ewConfirmed/.test(CONF_FN) && /ewCalc/.test(CONF_FN),
  "confirming records the id and recalculates, so the number and the "
  + "contract cannot disagree");

function render(readiness) {
  const el = mkEl();
  new Function("$", "esc", "state",
    CONFIRMABLE + "\n" + (src || "") + "\n" + (CONF_FN || "")
    + "\nreturn { render: renderEwReady, confirm: typeof ewConfirmInput === 'function'"
    + " ? ewConfirmInput : null };")(
    (id) => (id === "ewReady" ? el : null),
    (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;"),
    {}).render({ readiness });
  return el;
}

const OPEN = {
  ready: false, n_missing: 4, n_assumed: 2,
  sentence: "4 required inputs missing and 2 still assumed",
  items: [
    { id: "scale", label: "Scale", state: "supplied", value: "1 in = 30 ft", why: "graphic bar", required: true },
    { id: "limit_of_work", label: "Limit of work", state: "missing", value: "100,540 SF", why: "with no limits line the region is wherever the two surfaces overlap", required: true },
    { id: "elevations", label: "Elevations", state: "missing", value: "170 of 339 lines (67% by length)", why: "169 contour lines carry no height", required: true },
    { id: "strip", label: "Topsoil strip", state: "missing", value: null, why: "nothing is stripped", required: true },
    { id: "swell", label: "Shrink and swell", state: "assumed", value: "in-place yards", why: "no factor is applied", required: true },
    { id: "reuse", label: "Redistribute and reuse", state: "assumed", value: "site cut reused 100%", why: "the default", required: true },
    { id: "rock_water", label: "rock and groundwater", state: "not built", value: null, why: "not an input this software takes yet", required: false },
  ],
};

{
  const el = render(OPEN);
  const h = el.innerHTML;
  check(!el.classList.contains("off") && el.classList.contains("r-open"),
    "an incomplete job shows the block and marks it open");
  check(/Ready to bid\?/.test(h) && /4 required inputs missing and 2 still assumed/.test(h),
    "the verdict is the engine's own sentence  (" + h.slice(0, 120) + ")");
  check(/170 of 339 lines \(67% by length\)/.test(h)
    && /169 contour lines carry no height/.test(h),
    "the biggest item carries its number and its consequence");
  check(/ri-missing/.test(h) && /ri-assumed/.test(h) && /ri-supplied/.test(h),
    "supplied, assumed and missing are told apart in the markup, not only in words");
  check(/data-confirm="swell"/.test(h) && /in-place yards are right/.test(h),
    "an assumed default can be adopted right there");
  check(!/data-confirm="elevations"/.test(h) && !/data-confirm="limit_of_work"/.test(h),
    "...but nothing that has to be TYPED gets a button that pretends otherwise");
  check(!/data-confirm="rock_water"/.test(h) && /ri-not-built/.test(h),
    "a thing the software cannot take is named, greyed, and not confirmable");
  check(el._handlers.length === 2 && el._handlers.every(h2 => h2.fn),
    "every confirm button is wired  (" + el._handlers.length + ")");
}
{
  const done = { ready: true, n_missing: 0, n_assumed: 0,
    sentence: "every input this number depends on has been supplied",
    items: OPEN.items.map(i => Object.assign({}, i,
      { state: i.state === "not built" ? "not built" : "supplied" })) };
  const el = render(done);
  check(el.classList.contains("r-ready")
    && /every input this number depends on has been supplied/.test(el.innerHTML),
    "a complete job reads ready, in plain words");
  check(!/data-confirm/.test(el.innerHTML),
    "...with nothing left to confirm");
}
{
  const el = render(null);
  check(el.classList.contains("off") && el.innerHTML === "",
    "no readiness in the reply (an older server) leaves the block out of the way");
  const err = render({ error: "ValueError: x" });
  check(err.classList.contains("off"),
    "...and a readiness that failed to compute does not print half a contract");
}

console.log(fails ? "\n" + fails + " FAILED" : "\nall passed");
process.exit(fails ? 1 : 0);
