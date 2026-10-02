// THE OLD EARTHWORK CALCULATOR'S ROWS COUNT NOWHERE - the page side (release blocker B1; the owner's decision, 2026-10-01
// evening; server side: tests/test_legacy_earthwork.py). v1.8.5 kept a page's takeoff rows in the browser under calcKey()
// as {label, qty, unit} (never an Areas row). At the one move to this app's key (calcKey2) the old calculator's rows are
// not carried over, v1.8.5's key is left as it is, and the record hears once that the page had them (its note).
// The committed functions are cut out of webui/app.js and run here.
//
// run: node tests/test_legacy_earthwork_ui.js
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
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
const constLine = (name) => { const i = js.indexOf("const " + name + " = "); if (i < 0) throw new Error("no const " + name); return js.slice(i, js.indexOf("\n", i)); };
const NOTE = "Earthwork from the old calculator was removed. Redo it in Cut / fill.";
const tk = (st) => st.extraItems.map((it) => `${it.label} ${it.qty} ${it.unit}`);

// ---- the move: v1.8.5's rows without the old calculator's; its key untouched; the drop recorded once ------------------
{
  const mem = new Map(), log = [];
  const OLD_KEY = "doc::C4.0.pdf::p0::calc", NEW_KEY = "doc::C4.0.pdf::p0::calc2";
  // the rehearsal store's job 1, as v1.8.5 kept it in client A's browser (MANIFEST: "::calc keys duplicate rows that ARE
  // in approved exports"), plus a trench run, a structure row and a count
  const v185 = JSON.stringify([
    { label: "Site Cut", qty: 646, unit: "CY" }, { label: "Site Fill", qty: 749, unit: "CY" }, { label: "Strip Topsoil", qty: 1999, unit: "CY" },
    { label: "Clear & Grub", qty: 107931, unit: "SF" }, { label: "Silt / Construction Fence", qty: 1319, unit: "LF" },
    { label: "Export Topsoil", qty: 1999, unit: "CY" }, { label: "Import Structural", qty: 103, unit: "CY" },
    { label: "Zone 1 fill", qty: 472, unit: "CY" }, { label: "Zone 1 topsoil strip", qty: 231, unit: "CY" },
    { label: "Remove Asphalt", qty: 50, unit: "CY" }, { label: "Zone 1 place Base Course", qty: 20, unit: "CY" },
    { label: "Trench excavation (Water pressure pipe)", qty: 1021.2, unit: "CY" }, { label: "Backfill (Water pressure pipe)", qty: 784.5, unit: "CY" },
    { label: "Mass Excavation", qty: 90, unit: "CY" }, { label: "Perimeter Drain", qty: 300, unit: "LF" }, { label: "10x10 symbol", qty: 3, unit: "EA" }]);
  mem.set(OLD_KEY, v185);
  const store = { getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => { log.push(["set", k]); mem.set(k, String(v)); },
    removeItem: (k) => { log.push(["remove", k]); mem.delete(k); } };
  const st = { result: {}, extraItems: [], areaRows: [], areaInc: new Map(), areaLabels: new Map(), ewDropped: false };
  const C = new Function("state", "store", "calcKey", "calcKey2", "areaSelKey",
    constLine("EW_OLD_CY_RE") + "\n" + fnSrc(js, "ewOldRow") + "\n" + fnSrc(js, "calcSave") + "\n" + fnSrc(js, "calcRestore")
    + "\nreturn { calcSave, calcRestore };")(st, store, () => OLD_KEY, () => NEW_KEY, () => "areasel");
  C.calcRestore();
  const first = tk(st);
  const c2 = JSON.parse(mem.get(NEW_KEY));
  st.extraItems = []; st.ewDropped = false;
  C.calcRestore();
  const second = tk(st), dropped2 = st.ewDropped;
  C.calcSave();
  const afterSave = JSON.parse(mem.get(NEW_KEY));
  check(JSON.stringify(first) === JSON.stringify(["Trench excavation (Water pressure pipe) 1021.2 CY", "Backfill (Water pressure pipe) 784.5 CY",
    "Mass Excavation 90 CY", "Perimeter Drain 300 LF", "10x10 symbol 3 EA"])
    && c2.core === 2 && c2.rows.length === 5 && c2.ew_dropped === true && JSON.stringify(second) === JSON.stringify(first) && dropped2
    && afterSave.ew_dropped === true && afterSave.rows.length === 5
    && mem.get(OLD_KEY) === v185 && !log.some(([, k]) => k === OLD_KEY),
    "R2: the move drops the old calculator's 11 rows (its volumes, zone, layers, balance and its limit's Clear & Grub and "
    + "fence), keeps the trench, structure and count rows, never writes v1.8.5's key, and remembers the drop: " + JSON.stringify([first, c2]));
  // a build of 2026-10-01 kept marked old rows under calcKey2: dropped too; an unmarked "Site Cut" there is this app's
  mem.clear(); log.length = 0;
  mem.set(NEW_KEY, JSON.stringify({ core: 2, rows: [{ label: "Site Fill", qty: 900, unit: "CY", src: "earthwork-old" },
    { label: "Site Cut", qty: 5, unit: "CY", src: "cutfill" }, { label: "Paving", qty: 3, unit: "EA" }] }));
  st.extraItems = []; st.ewDropped = false;
  C.calcRestore();
  check(tk(st).join("|") === "Site Cut 5 CY|Paving 3 EA" && st.ewDropped && JSON.parse(mem.get(NEW_KEY)).rows.length === 2,
    "R2: a test build's marked old rows under this app's key are dropped; Cut / fill's own Site Cut stays");
  // a browser with nothing old: no drop said
  mem.clear(); mem.set(OLD_KEY, JSON.stringify([{ label: "Paving", qty: 3, unit: "EA" }]));
  st.extraItems = []; st.ewDropped = false;
  C.calcRestore();
  check(!st.ewDropped && !("ew_dropped" in JSON.parse(mem.get(NEW_KEY))), "R2: a page without old rows says nothing");
}
// ---- the record hears it once; the saved inputs ride along ----------------------------------------------------------
{
  const saved = { v: 1, ew: { rows: [{ chain_id: 3 }], added: true }, ew_bound: { pts: [[1, 2]] }, gzones: [{ name: "Zone 1" }] };
  const st = { result: { engine_sig: "s" }, calcSaved: saved, calcRowsLoaded: true, calcRowsTouched: false, labels: new Map(),
    extraItems: [], workZones: [], once: null, ckDone: null, tr: null, st: null, fl: null, cfFacts: null, ewDropped: true };
  const pay = new Function("state", "store", "priceKey", "tradeKey", "TRADES", "flScreenScale", "calcNum", "flSavePayload",
    "const CF_ROW_RE = /x/;\n" + fnSrc(js, "cfIsRow") + "\n" + fnSrc(js, "calcStatePayload") + "\nreturn calcStatePayload;")(
    st, { getItem: () => null }, (l) => "price:" + l, (l) => "trade::" + l, [], () => null, (v) => v, () => null)();
  check(pay.ew_dropped === true && JSON.stringify(pay.ew) === JSON.stringify(saved.ew) && JSON.stringify(pay.ew_bound) === JSON.stringify(saved.ew_bound)
    && JSON.stringify(pay.gzones) === JSON.stringify(saved.gzones) && !("ew_removed" in pay) && !("ew_put_back" in pay),
    "R2 / R4: a save says the page dropped old rows, and carries v1.8.5's ew / ew_bound / gzones untouched (undo)");
  const ld = fnSrc(js, "calcStateLoad"), push = fnSrc(js, "calcStatePush");
  check(/if \(state\.ewDropped && !state\.calcSaved\.ew_dropped\) \{ calcStateSent = null; calcStateDirty\(\); \}/.test(ld)
    && /approvalOutdated\(state\.gen\)/.test(push) && !/ewKeepLocal|ewRestore|ewOldMark/.test(ld),
    "R4: the load sends the drop to the record once, and the note is asked for when it lands; nothing is restored or kept");
}
// ---- the note: the page's lasting note is the server's, for this page --------------------------------------------------
{
  const els = { status: { parentNode: { insertBefore: (n) => { els.sbCf = n; } } }, sbCf: null };
  const doc = { createElement: () => ({ textContent: "", title: "" }) };
  const run = async (answer) => {
    els.sbCf = null;
    const st = { job: "A", page: 2, gen: 7, outdatedSeq: 0, cfLeft: null };
    const fn = new Function("state", "fetch", "$", "document", fnSrc(js, "approvalOutdated") + "\nreturn approvalOutdated;")(
      st, async () => ({ ok: true, json: async () => answer }), (id) => els[id], doc);
    await fn(7);
    return els.sbCf ? els.sbCf.textContent : "";
  };
  (async () => {
    const x = await run({ pages: [], outdated: {}, notes: { 2: "Page 3: " + NOTE } });
    const y = await run({ pages: [2], outdated: { 2: "OUT OF DATE: x" }, notes: { 2: "Page 3: " + NOTE } });
    const z = await run({ pages: [2], outdated: {}, notes: {} });
    check(x === "Page 3: " + NOTE + " · " && /^OUT OF DATE: x/.test(y) && z === "",
      "R4: the page's lasting note is its own note from /approved (OUT OF DATE, from other causes, first); none when it has none: "
      + JSON.stringify([x, y, z]));
    finish();
  })();
}
function finish() {
  // ---- R5: what kept the rows is gone ----------------------------------------------------------------------------------
  const gone = ["ewRemovedList", "ewPutBackList", "ewOldMark", "ewKeepLocal", "ewRestore", "ewIsOld", "ewOldReplace", "ewUndoReplace",
    "ewOldRemove", "ewOldPutBack", "ewOldSeatNote", "ewOldBarHint", "ewReplacedWords", "EW_CAN_PUT_BACK", "Removed old rows",
    "OLD CALCULATOR", "ew_removed: (", "ew_put_back", "_ew_approved"];
  const left = gone.filter((n) => js.includes(n));
  check(!left.length && /function cfIsRow\(it\) \{\n  return !!it && !it\._area && CF_ROW_RE\.test\(it\.label\);/.test(js),
    "R5: no remove / put back, no removed list, no restore, no tombstones, no old-row notes on the page; Cut / fill's rows "
    + "are told apart by name again: " + JSON.stringify(left));
  // F1 / F4 (critic legacy-5, WRONG-1 / RISK-4): the note as soon as a page opens, result or not; the approve says once
  const sp = fnSrc(js, "selectPage");
  check(/if \(st\.status !== "done"\) approvalOutdated\(gen\)\.catch\(\(\) => \{\}\);/.test(sp)
    && sp.indexOf("approvalOutdated(gen)") > sp.indexOf("fetch(`/api/job/${state.job}/page/${p}/status`)"),
    "F1: a page that opens without a current result (v1.8.5's 'detected with an older engine') asks for its note at once");
  check(/setStatus\(\(j\.ew_said \? j\.ew_said \+ " · " : ""\) \+ `exported \$\{nWord\(j\.exported, "element"\)\}`/.test(js),
    "F4: the approve's own sentence leads its status line when the server says it");
  check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 282, "the host page's cache buster moved (app.js)");
  console.log("\n" + (fails ? fails + " failure(s)" : "all ok"));
  process.exit(fails ? 1 : 0);
}
