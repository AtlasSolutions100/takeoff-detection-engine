// KEEP READING: A STOPPED LABEL READ CARRIES ON FROM THE PANEL (2026-09-17).
//
// "If it keeps timing out and not reading all of the elevations, how can we
// improve that ... or just allowing it to read for longer if it needs to"
// (Koby, 2026-09-17). The server now keeps a stopped read's place and
// /earthwork/suggest?more=1 carries it on. The panel side, pinned here
// against the real functions:
//
//   1. suggestOnce asks for ?more=1 exactly once when state.ewMore is set;
//   2. the "stopped early" line counts PLACES CHECKED (regions_seen) - the
//      old line put the reader's call count over its place count, two
//      different units - says which limit stopped it, and offers a Keep
//      reading button only when the server kept a place to carry on from;
//   3. ewKeepReading writes the saved record BEFORE it drops the rows (they
//      are rebuilt from it), takes the volume on screen down, flags the
//      request, and refuses when saved choices could not be loaded.
//
// Dependency-free:  node tests/test_keep_reading_ui.js
"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "webui", "app.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "webui", "index.html"), "utf8");

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
function mkEl(id) {
  const el = {
    id, innerHTML: "", hidden: false, textContent: "", className: "",
    _btn: null,
    classList: { add() {}, remove() {}, toggle() {} },
    querySelector(sel) {
      if (sel !== "#ewMoreBtn" || !/id="ewMoreBtn"/.test(this.innerHTML)) return null;
      if (!this._btn) this._btn = { onclick: null };
      return this._btn;
    },
  };
  return el;
}

const suites = [];

// ---------------------------------------------------------------------------
console.log("1. suggestOnce asks the server to carry on, once");
// ---------------------------------------------------------------------------
const onceSrc = fnSrc("suggestOnce");
check(!!onceSrc, "suggestOnce is in app.js");
suites.push((async () => {
  const urls = [];
  const state = { gen: 1, job: "J", page: 2, suggestReq: null, selected: null, ewMore: true };
  const scope = {
    state,
    fetch: (u) => { urls.push(u); return Promise.resolve({ ok: true, json: async () => ({ suggestions: [] }) }); },
    ELEV_READ_CEILING_MS: 60000, fmtElapsed: String,
    elevTickStart() {}, elevTickStop() {}, repaintSelElev() {}, chainById: () => null,
    AbortController, setTimeout, clearTimeout,
  };
  const names = Object.keys(scope);
  const fn = new Function(...names, (onceSrc || "function suggestOnce() {}") + "\nreturn suggestOnce;")
    (...names.map(n => scope[n]));
  await fn();
  await fn();
  check(urls[0] === "/api/job/J/page/2/earthwork/suggest?more=1",
    "with state.ewMore set, the request carries ?more=1  (" + urls[0] + ")");
  check(state.ewMore === false, "...and the flag is spent on that request");
  check(urls[1] === "/api/job/J/page/2/earthwork/suggest",
    "the next request is a plain read  (" + urls[1] + ")");
})());

// ---------------------------------------------------------------------------
console.log("2. the stopped-early line: places checked, which limit, Keep reading");
// ---------------------------------------------------------------------------
const dropSrc = fnSrc("renderEwDropped");
function drop(ocr, dropped) {
  const els = { ewDropped: mkEl("ewDropped") };
  const calls = [];
  const state = { ew: { dropped: dropped || 0, ladder: null, ocr } };
  new Function("$", "state", "esc", "ewKeepReading",
    dropSrc + "\nreturn renderEwDropped;")(
    (id) => els[id], state, String, () => calls.push("keep"))();
  return { box: els.ewDropped, calls };
}
{
  const r = drop({ stopped_early: true, stop_reason: "cap", regions_seen: 426,
                   ocr_calls: 431, patches_planned: 1901, seconds: 900.3, resumable: true });
  const h = r.box.innerHTML;
  check(!r.box.hidden && /checked 426 of 1901 places/.test(h),
    "the line counts places CHECKED against places planned  (" + h.slice(0, 120) + ")");
  check(/15 min/.test(h) && /time limit/.test(h), "...says how long, and that the time limit stopped it");
  check(/id="ewMoreBtn"[^>]*>Keep reading</.test(h), "...and offers Keep reading");
  check(r.box._btn && typeof r.box._btn.onclick === "function",
    "the button is wired");
  if (r.box._btn && r.box._btn.onclick) r.box._btn.onclick();
  check(r.calls.length === 1, "...to ewKeepReading");
}
{
  const r = drop({ stopped_early: true, stop_reason: "calls", regions_seen: 996,
                   patches_planned: 2620, seconds: 51, resumable: false }, 3);
  const h = r.box.innerHTML;
  check(/reading limit/.test(h) && /51 s/.test(h), "a pass the call budget stopped says READING limit");
  check(!/ewMoreBtn/.test(h), "no kept place, no Keep reading button");
  check(/3 candidate rows/.test(h), "the dropped-rows sentence still follows it");
}
{
  const r = drop({ stopped_early: true, stop_reason: "cap", ocr_calls: 426,
                   patches_planned: 1901, seconds: 900.3 });
  const h = r.box.innerHTML;
  check(/made 426 reads over 1901 places/.test(h) && !/looked at 426 of/.test(h),
    "a record from before regions_seen says READS, not 'looked at N of M places'");
}
{
  const r = drop({ stopped_early: false, regions_seen: 1644, patches_planned: 1644 }, 0);
  check(r.box.hidden === true, "a finished read with nothing dropped shows nothing");
}

// ---------------------------------------------------------------------------
console.log("3. ewKeepReading: save first, then re-read, then rebuild");
// ---------------------------------------------------------------------------
const keepSrc = fnSrc("ewKeepReading");
check(!!keepSrc, "app.js has ewKeepReading");
function keepScope(o) {
  const log = [];
  const els = {};
  const $ = (id) => (els[id] = els[id] || mkEl(id));
  const state = Object.assign({
    gen: 4, job: "J", page: 2, result: { chains: [] }, ewLoading: null,
    calcSavedLoaded: true,
    ew: { job: "J", page: 2, rows: [{ chain_id: 1 }], result: { cut_yd3: 5 }, map: { img: 1 }, added: true },
  }, o || {});
  const scope = {
    state, $,
    ewIsThisPage: () => !!state.ew && state.ew.page === state.page && state.ew.job === state.job,
    calcStateDirty: () => log.push("dirty:" + !!state.ew),
    calcStatePush: async () => { log.push("push:" + !!state.ew); },
    ewClearItems: () => log.push("clearItems"),
    ewMapNote: () => {}, renderEwBasis: () => {}, renderEwBalance: () => {},
    renderItemized: () => {}, draw: () => log.push("draw"),
    ewEnsure: async () => {
      const box = $("ewDropped");
      log.push("ensure:more=" + state.ewMore + ":ew=" + !!state.ew);
      log.push("notice-during-read:" + (box.hidden ? "hidden" : "SHOWN") + ":" + (box.innerHTML ? "HAS-TEXT" : "empty"));
      state.ew = { rows: [] };
      return true;
    },
    ewBusyPut: (t) => log.push("busy:" + t), setStatus: (t, c) => log.push("status:" + (c || "") + ":" + t),
    ewFailMsg: () => "fail", ewDoneLine: () => "done line",
    EW_SUGGEST_BUSY: "reading", EW_NEED_DETECT: "detect",
    renderEw: () => log.push("renderEw"), renderEwLayers() {}, renderEwSpots() {},
    renderEwArea() {}, renderEwTake() {}, renderGz() {}, renderCheckQueue() {},
  };
  const names = Object.keys(scope);
  const fn = new Function(...names, (keepSrc || "async function ewKeepReading() {}") + "\nreturn ewKeepReading;")
    (...names.map(n => scope[n]));
  return { fn, log, state, els };
}
suites.push((async () => {
  {
    const s = keepScope();
    const oldEw = s.state.ew;
    // the stopped-early line of the read being carried on, as renderEwDropped left it
    s.els.ewDropped = mkEl("ewDropped");
    s.els.ewDropped.innerHTML = '<div class="ew-ocr-cut"><b>Label reading stopped early.</b> '
      + '<button id="ewMoreBtn">Keep reading</button></div>';
    await s.fn();
    check(s.log.includes("notice-during-read:hidden:empty"),
      "the old 'stopped early' line and its button are gone while the next round reads "
      + "(seen live still on screen) (" + s.log.filter(x => x.startsWith("notice")).join() + ")");
    const iPush = s.log.indexOf("push:true");
    const iEnsure = s.log.findIndex(x => x.startsWith("ensure:"));
    check(iPush >= 0 && iEnsure > iPush,
      "the saved record is written (with the rows still there) BEFORE the re-read  (" + s.log.join(" | ") + ")");
    check(s.log[iEnsure] === "ensure:more=true:ew=false",
      "the re-read goes out with the rows dropped and ?more=1 flagged");
    check(oldEw.result === null && oldEw.map === null && s.log.includes("clearItems"),
      "the volume on screen, its map and its takeoff lines come down first");
    check(/re-read/.test(s.els.ewResult.textContent), "...and the result box says why");
    check(s.log.includes("busy:done line") && s.log.includes("renderEw"),
      "when the rows are back the panel says so and repaints");
  }
  {
    const s = keepScope({ calcSavedLoaded: false });
    await s.fn();
    check(!s.log.some(x => x.startsWith("ensure:")) && s.log.some(x => /^status:err:/.test(x)) && !!s.state.ew,
      "saved choices that could not be loaded: no re-read, the rows stay, and it says why");
  }
  {
    const s = keepScope({ ewLoading: Promise.resolve(true) });
    await s.fn();
    check(!s.log.some(x => x.startsWith("ensure:") || x.startsWith("push")),
      "a read already in flight: nothing happens");
  }
})());

check(/app\.js\?v=(\d+)/.test(HTML) && +HTML.match(/app\.js\?v=(\d+)/)[1] >= 202,
  "index.html busts the app.js cache (v=" + (HTML.match(/app\.js\?v=(\d+)/) || [])[1] + ")");

Promise.all(suites).then(() => {
  if (fails) { console.error("\n" + fails + " keep-reading check(s) FAILED"); process.exit(1); }
  console.log("\nall keep-reading checks passed");
});
