// A SHARED JOB'S ROWS AND PRICES STAY IN THIS BROWSER - the page side (release item 1, 2026-10-02; server side:
// tests/test_shared_calcstate_private.py). On a job every account opens (the record says job_shared), the page sends
// none of this seat's rows, prices or trades to the record and fills none in from it; its own copy (calcSave's key)
// stands, as v1.8.5 kept it for every job. An owner's own job is unchanged. The committed functions are cut out of
// webui/app.js and run here.
//
// run: node tests/test_shared_rows_ui.js
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(src, name) {
  let start = src.indexOf("async function " + name + "(");
  if (start < 0) start = src.indexOf("function " + name + "(");
  if (start < 0) throw new Error("no function " + name);
  let depth = 0;
  for (let j = src.indexOf("{", start); j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}" && --depth === 0) return src.slice(start, j + 1);
  }
  return "";
}

const local = new Map([["price:Curb", "31.5"], ["trade::Curb", "Concrete"]]);
const store = { getItem: (k) => (local.has(k) ? local.get(k) : null), setSession: (k, v) => local.set(k, v) };
const mkState = (shared, saved) => ({
  result: { engine_sig: "s" }, calcSaved: saved, calcShared: shared, calcRowsLoaded: true, calcRowsTouched: true,
  labels: new Map(), extraItems: [{ label: "Curb", qty: 120, unit: "LF" }], workZones: [[1, 2, 3, 4]], once: null,
  ckDone: null, tr: null, st: null, fl: null, cfFacts: null, ewDropped: false });
const payload = (st) => new Function("state", "store", "priceKey", "tradeKey", "TRADES", "flScreenScale", "calcNum", "flSavePayload",
  "const CF_ROW_RE = /x/;\n" + fnSrc(js, "cfIsRow") + "\n" + fnSrc(js, "flIsRowLabel") + "\n" + fnSrc(js, "calcStatePayload") + "\nreturn calcStatePayload;")(
  st, store, (l) => "price:" + l, (l) => "trade::" + l, ["Concrete"], () => null, (v) => v, () => null)();

{
  const p = payload(mkState(true, { job_shared: true }));
  check(!("rows" in p) && !("prices" in p) && !("trades" in p) && !("rows_scale" in p) && JSON.stringify(p.zones) === "[[1,2,3,4]]",
    "shared job: the save carries no rows, prices or trades (work areas still go): " + JSON.stringify(p));
  const q = payload(mkState(false, {}));
  check(JSON.stringify(q.rows) === JSON.stringify([{ label: "Curb", qty: 120, unit: "LF" }]) && q.prices.Curb === 31.5
    && q.trades.Curb === "Concrete", "own job: rows, prices and trades still go to the record: " + JSON.stringify(q));
}
{
  // a shared record that (from an older server) still carries someone's rows and prices fills nothing in
  const st = mkState(true, { job_shared: true, rows: [{ label: "Theirs", qty: 5, unit: "EA" }], prices: { Theirs: 77 },
    trades: { Theirs: "Concrete" } });
  new Function("state", "store", "priceKey", "tradeKey", "TRADES", fnSrc(js, "flIsRowLabel") + "\n" + fnSrc(js, "rowsApplySaved") + "\nreturn rowsApplySaved;")(
    st, store, (l) => "price:" + l, (l) => "trade::" + l, ["Concrete"])();
  check(st.extraItems.length === 1 && st.extraItems[0].label === "Curb" && !local.has("price:Theirs") && !local.has("trade::Theirs")
    && st.calcRowsLoaded === true, "shared job: this browser's rows stand, nobody else's rows or prices are filled in");
  const own = mkState(false, { rows: [{ label: "Mine", qty: 5, unit: "EA" }], prices: { Mine: 12 } });
  new Function("state", "store", "priceKey", "tradeKey", "TRADES", fnSrc(js, "flIsRowLabel") + "\n" + fnSrc(js, "rowsApplySaved") + "\nreturn rowsApplySaved;")(
    own, store, (l) => "price:" + l, (l) => "trade::" + l, ["Concrete"])();
  check(own.extraItems.length === 1 && own.extraItems[0].label === "Mine" && local.get("price:Mine") === "12",
    "own job: the record's rows and prices come back as before");
}
{
  const ld = fnSrc(js, "calcStateLoad");
  check(/state\.calcShared = false;/.test(ld) && /state\.calcShared = !!state\.calcSaved\.job_shared;/.test(ld)
    && ld.indexOf("state.calcShared = !!") < ld.indexOf("rowsApplySaved()"),
    "the load reads job_shared from the record before the rows are applied, and clears it for the next page");
}
console.log(fails ? `FAIL: ${fails} check(s)` : "PASS");
process.exit(fails ? 1 : 0);
