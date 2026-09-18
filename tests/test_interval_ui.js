// THE CONTOUR INTERVAL ON SCREEN, AND SETTABLE (2026-09-18).
//
// Every elevation COUNTED between two labelled contours is derived from the
// interval, and the panel printed it as a bare number with nothing to argue
// with. A sheet labels only its index contours, so the number its labels
// imply is often the index spacing: Barn C3.0 draws 350 lines and labels 26,
// every label a multiple of 5, while the sheet's notes say 1 ft twice.
//
// So the panel shows the value, where it came from, the sheet's own words,
// and takes the estimator's own - which re-reads the page, because the
// counted rows are downstream of it.
//
// Dependency-free:  node tests/test_interval_ui.js
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
  const j = SRC.indexOf("\nfunction " + name + "(");
  if (j < 0) return null;
  let d = 0;
  for (let k = SRC.indexOf("{", j); k < SRC.length; k++) {
    if (SRC[k] === "{") d++;
    else if (SRC[k] === "}" && --d === 0) return SRC.slice(j + 1, k + 1);
  }
  return null;
}

const ctl = fnSrc("ewIntervalControl");
const set = fnSrc("ewSetInterval");
check(!!ctl, "app.js has ewIntervalControl()");
check(!!set, "app.js has ewSetInterval()");
check(/\.ew-cov-ivset/.test(CSS), "style.css styles it");
const ver = (re) => Number((HTML.match(re) || [])[1] || 0);
check(ver(/app\.js\?v=(\d+)/) >= 207 && ver(/style\.css\?v=(\d+)/) >= 103,
  "index.html busts the cache  (app v" + ver(/app\.js\?v=(\d+)/)
  + ", css v" + ver(/style\.css\?v=(\d+)/) + ")");

// THE REQUEST has to carry it, and the single-flight key has to include it,
// or typing a new interval is handed the answer counted at the old one.
check(/const ivq = state\.ewIntervalFt \? String\(state\.ewIntervalFt\)/.test(SRC)
  && /qs\.push\("interval_ft=" \+ encodeURIComponent\(ivq\)\)/.test(SRC),
  "the suggest request carries the operator's interval");
check(/\(qs\.length \? "\?" \+ qs\.join\("&"\) : ""\)/.test(SRC),
  "...and a plain read still sends no query at all (test_keep_reading_ui pins that)");
check(/const key = gen \+ "\|" \+ job \+ "\|" \+ page \+ "\|" \+ ivq/.test(SRC),
  "...and the single-flight key includes it, so a change re-reads the page");
check(/state\.ew = null/.test(set || "") && /ewEnsure\(\)/.test(set || ""),
  "setting it drops the loaded rows and re-reads");

// a tiny DOM good enough for a builder that only appends elements
function mkDoc() {
  const made = [];
  const el = () => ({
    className: "", textContent: "", type: "", id: "", step: "", min: "",
    placeholder: "", value: "", htmlFor: "", _kids: [], _ev: {},
    appendChild(c) { this._kids.push(c); return c; },
    addEventListener(n, f) { this._ev[n] = f; },
  });
  return {
    createElement(tag) { const e = el(); e.tag = tag; made.push(e); return e; },
    _made: made,
  };
}
function flat(node) {
  return [node].concat(...(node._kids || []).map(flat));
}
function build(cov, typed) {
  const doc = mkDoc();
  const state = { ewIntervalFt: typed || null };
  const fn = new Function("document", "state", ctl + "\nreturn ewIntervalControl;")(doc, state);
  return { node: fn(cov), state };
}

const SHEET = { interval: {
  interval_ft: 1.0, source: "the sheet's own contour-interval note",
  note: { interval_ft: 1.0, quote: "EXISTING CONTOURS ILLUSTRATED HEREIN ARE AT 1 FT INTERVAL",
          candidates: [{ interval_ft: 1.0, quote: "..." }], n_notes: 1 } } };
{
  const { node } = build(SHEET, null);
  const all = flat(node);
  const txt = all.map(n => n.textContent).join(" | ");
  const inp = all.find(n => n.tag === "input");
  check(!!inp && inp.placeholder === "1",
    "the value the engine used is shown  (" + (inp || {}).placeholder + ")");
  check(!inp.value, "...and the box is empty, because the operator has not typed one");
  check(/the sheet's own contour-interval note/.test(txt),
    "the source is stated, not just the number");
  check(/ARE AT 1 FT INTERVAL/.test(txt),
    "...and the sheet's own words are quoted, so it can be checked");
}
{
  const { node } = build(SHEET, 2.5);
  const all = flat(node);
  const inp = all.find(n => n.tag === "input");
  const txt = all.map(n => n.textContent).join(" | ");
  check(inp.value === 2.5, "a typed interval fills the box  (" + inp.value + ")");
  check(/yours/.test(txt) && /clear the box/.test(txt),
    "...and the panel says it is yours and how to undo it  (" + txt.slice(-64) + ")");
}
{
  // THE CASE THAT MATTERS: the sheet says two different things
  const two = { interval: { interval_ft: null,
    source: "the sheet states 2 different contour intervals - type the one that applies",
    note: { interval_ft: null, quote: null, n_notes: 2, candidates: [
      { interval_ft: 1.0, quote: "CONTOUR INTERVAL 1 FOOT" },
      { interval_ft: 5.0, quote: "MAJOR CONTOUR INTERVAL 5 FT" }] } } };
  const { node } = build(two, null);
  const all = flat(node);
  const txt = all.map(n => n.textContent).join(" | ");
  const inp = all.find(n => n.tag === "input");
  check(inp.placeholder === "not read",
    "nothing is claimed when the sheet disagrees with itself  (" + inp.placeholder + ")");
  check(/2 different intervals/.test(txt) && /1 ft, 5 ft/.test(txt),
    "...both values are shown  (" + txt.slice(-80) + ")");
  check(/type the one that applies/.test(txt),
    "...and the operator is told to settle it");
}
{
  const none = { interval: { interval_ft: null, source: "not read", note: {} } };
  const { node } = build(none, null);
  const all = flat(node);
  check(all.find(n => n.tag === "input").placeholder === "not read",
    "a sheet with no note still offers the box");
  check(!all.some(n => /“/.test(n.textContent || "")),
    "...and quotes nothing it never read");
}

// A STATED INTERVAL IS NOT A STATISTIC. ewIntervalUse gates on how many
// label steps agree, which says nothing about a sheet that states its
// interval - and judging one by the other printed "Only 0 steps ... agree
// on 1 ft" directly above a control reading "sheet's contour-interval
// note": the same number, contradicted in two lines.
const use = fnSrc("ewIntervalUse");
check(!!use && /ivStated/.test(use),
  "ewIntervalUse knows whether the interval was STATED or inferred");
function ivUse(raw) {
  return new Function("EW_IV_MIN_STEPS", "EW_OFF_IV_MAX_SHARE", "ewOnInterval",
    use + "\nreturn ewIntervalUse;")(5, 0.4, () => true)(raw, []);
}
{
  const r = ivUse({ interval_ft: 1, modal_count: 0, typed_ft: null,
                    note: { interval_ft: 1 } });
  check(r.ok === true,
    "a sheet-stated interval is usable with 0 agreeing label steps");
  check(/states a 1 ft contour interval in its notes/.test(r.why),
    "...and the sentence credits the sheet  (" + r.why + ")");
  const t = ivUse({ interval_ft: 2, modal_count: 0, typed_ft: 2, note: {} });
  check(/You set this sheet's contour interval to 2 ft/.test(t.why),
    "...and a typed one credits the operator  (" + t.why + ")");
  const i = ivUse({ interval_ft: 5, modal_count: 1, typed_ft: null, note: {} });
  check(i.ok === false && /too few/.test(i.why),
    "...while one INFERRED from a single label step is still refused");
}

console.log(fails ? "\n" + fails + " FAILED" : "\nall passed");
process.exit(fails ? 1 : 0);
