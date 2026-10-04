// Shared harness for the flooring tests (not a test on its own): every `function fl...(` and
// `const FL_...` of webui/app.js, the REAL flFmt / flFtIn, priceLabel / priceKey, and stubs for
// the page (store, state, the DOM calls). Evaluated once per require; `fresh()` gives a new copy
// with an empty price book.
"use strict";
const fs = require("fs");
const path = require("path");
const SRC = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");

function grab(name) {
  const start = SRC.indexOf("function " + name + "(");
  if (start < 0) return null;
  let depth = 0;
  for (let j = SRC.indexOf("{", start); j < SRC.length; j++) {
    if (SRC[j] === "{") depth++;
    else if (SRC[j] === "}" && --depth === 0) return SRC.slice(start, j + 1);
  }
  return null;
}
// a `const NAME = ...;` statement, brackets balanced (an arrow function body may hold ';')
function grabConstStmt(name) {
  const key = "const " + name + " = ";
  const a = SRC.indexOf(key);
  if (a < 0) return null;
  let depth = 0, inStr = null;
  for (let j = a + key.length; j < SRC.length; j++) {
    const ch = SRC[j];
    if (inStr) { if (ch === "\\") { j++; continue; } if (ch === inStr) inStr = null; continue; }
    if (ch === '"' || ch === "'" || ch === "`") { inStr = ch; continue; }
    if (ch === "/" && SRC[j + 1] === "/") { j = SRC.indexOf("\n", j); continue; }
    if ("([{".includes(ch)) depth++;
    else if (")]}".includes(ch)) depth--;
    else if (ch === ";" && depth === 0) return SRC.slice(a, j + 1);
  }
  return null;
}
const fnNames = [...new Set([...SRC.matchAll(/^(?:async )?function (fl[A-Z]\w*)\(/gm)].map(m => m[1]))];
const constNames = [...new Set([...SRC.matchAll(/^const (FL_[A-Z_0-9]+) = /gm)].map(m => m[1]))];
const extra = ["priceLabel", "priceKey", "stPolyArea", "stPolyPerim"];
// an async function keeps its keyword
const grabFn = (n) => { const b = grab(n); return b && SRC.includes("async function " + n + "(") ? "async " + b : b; };

function fresh() {
  const book = new Map();
  const body = [
    "const store = { getItem: k => (book.has(k) ? book.get(k) : null), setItem: (k, v) => book.set(k, String(v)),",
    "  removeItem: k => book.delete(k), setSession: (k, v) => book.set(k, String(v)) };",
    "const state = { extraItems: [], fl: null, result: null, calcFlApplied: false, calcSavedLoaded: true, calcSaved: null };",
    "const $ = () => ({ hidden: true }); const said = []; const setStatus = (m, k) => said.push([m, k]); const draw = () => {}; const renderItemized = () => {};",
    "const calcStateDirty = () => { calls.dirty++; }; const renderStyles = () => {}; const setFamVis = () => {}; const renderFl = () => {};",
    "const polySnapClear = () => {}; const calls = { dirty: 0 };",
    // every request the page makes is recorded; a test says what the server answers (tests/test_fl_acts.py
    // holds the server's own behaviour)
    "const fetchLog = []; let fetchReply = () => ({ ok: false, status: 0, json: async () => ({}) });",
    "const fetch = async (u, o) => { fetchLog.push({ url: String(u), method: (o && o.method) || 'GET', body: o && o.body ? JSON.parse(o.body) : null }); return fetchReply(String(u), o); };",
    "const setReply = (f) => { fetchReply = f; }; const url = (x) => x;",
    "const location = { reload() {} }; const famShown = () => true;",
    ...constNames.map(grabConstStmt).filter(Boolean),
    grabConstStmt("flFmt"), grabConstStmt("flFtIn"), grabConstStmt("calcNum"),
    ...extra.map(n => grab(n)).filter(Boolean),
    ...fnNames.map(grabFn).filter(Boolean),
    "return { book, store, state, fetchLog, setReply, said, calls, " + [...constNames, "flFmt", "flFtIn", "calcNum", ...extra.filter(n => grab(n)), ...fnNames].join(", ") + " };",
  ].join("\n");
  return new Function("book", body)(book);
}
module.exports = { SRC, grab, grabConstStmt, fresh, fnNames, constNames };
