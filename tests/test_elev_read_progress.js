// THE ELEVATION READ HAS TO SAY WHAT IT IS DOING, AND SAY WHEN IT FAILED.
//
// REPORTED (screen share, 2026-09-02). The earthwork panel sat on "reading
// elevation labels" on one machine while the same read finished on another,
// and the client asked "Is it not working? Is it dead? Is it hung?" He
// reloaded; after a while it appeared, and he was told drawing a limit of
// work might be why. Nothing in the panel told him it was working, how far
// along it was, or that it had failed.
//
// WHAT WAS MEASURED HERE (Oncor Cloudcrest engine page index 3 = printed
// C-04, 589 chains, throwaway store on :9970, 2026-09-02):
//
//   cold GET /api/job/<j>/page/3/earthwork/suggest    217.2 s, 200, 150 rows
//   warm (p3_elevgaps2.json on disk)                    1.4 s
//   two clients 3 s apart, cold                220.0 s and 219.3 s - TWO
//                                              full OCR passes for one answer
//
// So the read really is minutes long the first time, and the panel's one
// unchanging sentence was the whole of the feedback. Two separate defects:
//
//   (a) NO CLOCK. "reading this sheet's elevation labels - the first read of
//       a page takes a minute or more" reads the same at 5 s and at 5 min.
//       There is no honest PERCENTAGE to show - the server does the page in
//       one pytesseract loop and reports nothing until it is done - but
//       elapsed time is real and it was not shown.
//   (b) NO FLOOR UNDER THE FAILURE. Every failure surface in app.js is
//       reached by the fetch REJECTING. suggestOnce's fetch had no deadline,
//       so a socket that goes quiet leaves the panel claiming to work with
//       nothing scheduled that could ever change it.
//
// WHAT IS PINNED HERE
//   1. fmtElapsed - the format, including the minute boundary.
//   2. elevBusyLine - the clock is IN the sentence, and the sentence with no
//      read in flight is byte-identical to the old one.
//   3. suggestOnce parks t0 ON the request record, aborts at
//      ELEV_READ_CEILING_MS, and the rejection is marked `timeout`.
//   4. ewFailMsg gives that its own sentence: retryable, and NOT the
//      "run Detect" arm.
//   5. elevTickPaint rewrites only the surfaces still carrying its own
//      sentence, and stops itself when nothing is in flight.
//   6. renderSelElev's busy note carries the elapsed time too.
//
// Behavioural, against the real functions - a source grep for "elapsed"
// is satisfied by a version that never repaints anything.
//
// Dependency-free:  node tests/test_elev_read_progress.js

"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "webui", "app.js"), "utf8");

let fails = 0;
const check = (ok, msg) => {
  if (ok) console.log("  ok: " + msg);
  else { fails++; console.error("  FAIL: " + msg); }
};
const eq = (got, want, msg) =>
  check(got === want, msg + "  (got " + JSON.stringify(got) + ")");

function fnSrc(name) {
  let j = SRC.indexOf("\nfunction " + name + "(");
  if (j < 0) j = SRC.indexOf("\nasync function " + name + "(");
  if (j < 0) throw new Error("function not found in app.js: " + name);
  let d = 0;
  for (let k = SRC.indexOf("{", j); k < SRC.length; k++) {
    if (SRC[k] === "{") d++;
    else if (SRC[k] === "}" && --d === 0) return SRC.slice(j + 1, k + 1);
  }
  throw new Error("unbalanced braces reading " + name);
}
// A named constant, by name. Missing means the contract is missing, and the
// suite should say which one rather than throw.
function constSrc(name) {
  const m = SRC.match(new RegExp("\\nconst " + name + " = [\\s\\S]*?;\\n"));
  return m ? m[0] : "";
}

// ---------------------------------------------------------------------------
console.log("1. fmtElapsed: seconds, then minutes, with no invented precision");
// ---------------------------------------------------------------------------
const HAVE_FMT = SRC.indexOf("\nfunction fmtElapsed(") >= 0;
check(HAVE_FMT, "app.js has fmtElapsed - the read reports elapsed time");
const fmtElapsed = HAVE_FMT
  ? new Function(fnSrc("fmtElapsed") + "\nreturn fmtElapsed;")()
  : () => "";
if (HAVE_FMT) {
  eq(fmtElapsed(0), "0s", "zero");
  eq(fmtElapsed(4400), "4s", "under a minute, whole seconds");
  eq(fmtElapsed(59_400), "59s", "just under the boundary");
  eq(fmtElapsed(60_000), "1m 00s", "the boundary");
  eq(fmtElapsed(72_000), "1m 12s", "past it");
  eq(fmtElapsed(217_200), "3m 37s", "the measured cold C-04 read");
  eq(fmtElapsed(-5), "0s", "a clock skew reads zero, never negative");
}

// ---------------------------------------------------------------------------
console.log("2. the busy sentence carries the clock");
// ---------------------------------------------------------------------------
const CONSTS = constSrc("EW_SUGGEST_WAIT") + constSrc("EW_SUGGEST_HEAD")
  + constSrc("EW_SUGGEST_BUSY") + constSrc("EW_SUGGEST_FAIL")
  + constSrc("ELEV_READ_CEILING_MS") + constSrc("EW_SUGGEST_TIMEOUT");
check(/EW_SUGGEST_HEAD/.test(CONSTS),
  "the busy sentence has a named HEAD, so the clock can be spliced into it "
  + "and every surface can recognise its own line");
check(/ELEV_READ_CEILING_MS/.test(CONSTS), "the read has a named deadline");

function busyScope(t0) {
  const state = { suggestReq: t0 === null ? null : { t0 } };
  const names = ["state", "Date"];
  return new Function(...names,
    CONSTS + "\n" + fnSrc("fmtElapsed") + "\n" + fnSrc("elevReadElapsed")
    + "\n" + fnSrc("elevBusyLine")
    + "\nreturn { line: elevBusyLine, ms: elevReadElapsed, BUSY: EW_SUGGEST_BUSY,"
    + " HEAD: EW_SUGGEST_HEAD, CEIL: ELEV_READ_CEILING_MS,"
    + " TIMEOUT: EW_SUGGEST_TIMEOUT };")
    (state, { now: () => 1_000_000 });
}
{
  const idle = busyScope(null);
  eq(idle.ms(), null, "no request in flight, no clock");
  eq(idle.line(), idle.BUSY,
    "...and the sentence is exactly the one that was there before");

  const run = busyScope(1_000_000 - 72_000);
  eq(run.ms(), 72_000, "the clock reads off the request record's t0");
  const line = run.line();
  check(line.indexOf(run.HEAD) === 0,
    "the line still starts with the head every surface matches on");
  check(/1m 12s/.test(line),
    "THE FIX: the elapsed time is IN the sentence  (got "
    + JSON.stringify(line) + ")");
  check(/can take a minute/.test(line),
    "...and it still says what to expect (since 2026-09-17 'can take a "
    + "minute': the batched reader - FINDINGS 38)");
  check(!/%|\bof\b \d|\d+\s*\/\s*\d+/.test(line),
    "and with NO report from the server it invents no percentage or "
    + "fraction - a clock is all it honestly has");

  // SINCE 2026-09-09 THE SERVER DOES REPORT. gap_label_elevations tells the
  // route every ten regions where it is; /earthwork/progress serves it;
  // elevProgressPoll parks it on the request record. With a report the
  // line says where the read is, in the server's own numbers.
  const progScope = (progress) => {
    const state = { suggestReq: { t0: 1_000_000 - 72_000, progress } };
    return new Function("state", "Date",
      CONSTS + "\n" + fnSrc("fmtElapsed") + "\n" + fnSrc("elevReadElapsed")
      + "\n" + fnSrc("elevProgressNow") + "\n" + fnSrc("elevProgressFrac")
      + "\n" + fnSrc("elevBusyLine")
      + "\nreturn { line: elevBusyLine, frac: elevProgressFrac };")
      (state, { now: () => 1_000_000 });
  };
  const ocr = progScope({ phase: "ocr", pos: 156, planned: 796, calls: 90, reads: 12, seconds: 41 });
  check(/156 of 796 places/.test(ocr.line()) && /12 labels read by OCR/.test(ocr.line()) && /1m 12s/.test(ocr.line()),
    "with a server report the line says N of M places, reads, and the clock  (got "
    + JSON.stringify(ocr.line()) + ")");
  check(ocr.line().indexOf(run.HEAD) === 0, "...and still starts with the head");
  check(Math.abs(ocr.frac() - 156 / 796) < 1e-9,
    "...and the bar fraction is the server's pos / planned, nothing invented");
  const rend = progScope({ phase: "render", pos: 0, planned: 0 });
  check(/rendering the sheet/.test(rend.line()) && rend.frac() === null,
    "while the sheet is still rendering the line says so and there is no bar");
  const none = progScope(null);
  check(none.line() === line && none.frac() === null,
    "a request with no report yet reads exactly as the clock-only line");

  // the ceiling has to clear the slowest MEASURED read by a wide margin,
  // or it is a timeout on a working sheet
  check(run.CEIL > 300_000,
    "the deadline is far above the 217.2 s measured cold read  (got "
    + Math.round(run.CEIL / 1000) + "s)");
  check(/try again/i.test(run.TIMEOUT),
    "the timeout sentence offers a way back in");
  check(/background|keeps reading|cache/i.test(run.TIMEOUT),
    "...and says the retry is cheap, which is true: the server thread runs "
    + "on after the browser gives up and writes the page cache");
}

// ---------------------------------------------------------------------------
console.log("3. suggestOnce: a clock on the record, and a deadline on the wire");
// ---------------------------------------------------------------------------
const onceSrc = fnSrc("suggestOnce");
function onceScope(opts) {
  const o = opts || {};
  const state = { gen: 1, job: "J", page: 3, suggestReq: null, selected: null };
  const seen = { aborts: 0, timers: [], cleared: 0, ticks: 0, stops: 0 };
  const scope = {
    state,
    fetch: (u, init) => new Promise((res, rej) => {
      if (init && init.signal) {
        init.signal.addEventListener("abort", () => {
          seen.aborts++;
          const e = new Error("aborted");
          e.name = "AbortError";
          rej(e);
        });
      }
      if (o.answer) setTimeout(() => res(o.answer), 0);
    }),
    ELEV_READ_CEILING_MS: 5,          // the real value, shortened for a test
    fmtElapsed: (ms) => String(ms),
    elevTickStart: () => { seen.ticks++; },
    elevTickStop: () => { seen.stops++; },
    renderSelElev: () => {}, repaintSelElev: () => {},
    chainById: () => null,
    AbortController,
    setTimeout, clearTimeout,
  };
  const names = Object.keys(scope);
  const fn = new Function(...names, onceSrc + "\nreturn suggestOnce;")
    (...names.map(n => scope[n]));
  return { fn, state, seen };
}
const suite3 = (async () => {
  {
    const s = onceScope({ answer: { ok: true, json: async () => ({ suggestions: [] }) } });
    const t = Date.now();
    const p = s.fn();
    check(!!s.state.suggestReq && typeof s.state.suggestReq.t0 === "number",
      "the request record carries t0 - THE CLOCK LIVES ON THE RECORD, so it "
      + "cannot outlive the flight it belongs to");
    check(Math.abs(s.state.suggestReq.t0 - t) < 2000, "...and t0 is now");
    eq(s.seen.ticks, 1, "the ticker is started with the request");
    await p;
    eq(s.state.suggestReq, null, "the record is dropped when it settles");
    check(s.seen.stops >= 1, "...and the ticker is stopped with it");
  }
  {
    // A REQUEST THAT NEVER ANSWERS. This is the reported hang: the promise
    // simply never settles, so no failure surface anywhere downstream is
    // ever reached.
    const s = onceScope({});           // fetch that never resolves
    let err = null;
    await s.fn().catch(e => { err = e; });
    check(!!err, "THE FIXED DEFECT: a read that never answers now REJECTS "
      + "instead of hanging forever");
    eq(s.seen.aborts, 1, "...because the request is aborted at the deadline");
    check(err && err.timeout === true,
      "the rejection is marked `timeout`, so the UI can tell it from a "
      + "dropped connection and from a 404");
    eq(err && err.status, 0, "...with no HTTP status, because none arrived");
    eq(s.state.suggestReq, null, "and the record is not left parked");
  }
  {
    // ...and the deadline must not fire on a read that DID answer
    const s = onceScope({ answer: { ok: true, json: async () => ({ suggestions: [1] }) } });
    const j = await s.fn();
    check(!!j, "a read that answers is unaffected");
    await new Promise(r => setTimeout(r, 30));
    eq(s.seen.aborts, 0,
      "...and its deadline timer is cleared, so nothing aborts afterwards");
  }
})();

// ---------------------------------------------------------------------------
console.log("4. a read that never answered has its own sentence");
// ---------------------------------------------------------------------------
{
  const mk = (failed, result) => {
    const state = { ewFailed: failed, result: result };
    return new Function("state",
      constSrc("EW_NEED_DETECT") + CONSTS + "\n" + fnSrc("ewFailMsg")
      + "\nreturn { msg: ewFailMsg(), NEED: EW_NEED_DETECT,"
      + " FAIL: EW_SUGGEST_FAIL, TIMEOUT: EW_SUGGEST_TIMEOUT };")(state);
  };
  const t = mk({ status: 0, detail: "", timeout: true }, {});
  eq(t.msg, t.TIMEOUT,
    "a timed-out read gets the timeout sentence - THE FIXED DEFECT: it used "
    + "to be indistinguishable from a panel that was still working");
  check(t.msg !== t.FAIL, "...which is not the generic failure sentence");
  check(!/run Detect/.test(t.msg),
    "...and does not blame Detect, which ran fine");

  const d = mk({ status: 0, detail: "", timeout: false }, {});
  eq(d.msg, d.FAIL, "a dropped connection still gets the generic sentence");
  const n = mk({ status: 404, detail: "run detect first", timeout: false }, null);
  eq(n.msg, n.NEED,
    "and the unretryable 404 arm still wins over everything - a timeout flag "
    + "must not resurrect a retry that can never work");
}

// ---------------------------------------------------------------------------
console.log("5. the tick repaints what it owns and nothing else");
// ---------------------------------------------------------------------------
{
  function el(text) {
    return { textContent: text, hidden: false, className: "",
             querySelector() { return null; } };
  }
  function tickScope(t0, surfaces) {
    const state = { suggestReq: t0 === null ? null : { t0 }, selected: null };
    const put = [];
    const els = {
      ewBusy: el(surfaces.busy), ewRows: el(""), status: el(surfaces.status),
    };
    els.ewRows.querySelector = () => surfaces.hint || null;
    const scope = {
      state,
      $: (id) => els[id] || null,
      Date: { now: () => 1_000_000 },
      ewBusyPut: (t) => { els.ewBusy.textContent = t; put.push(t); },
      setStatus: (t) => { els.status.textContent = t; },
      renderSelElev: () => {}, repaintSelElev: () => {}, repaintSelElev: () => {},
      chainById: () => null,
      clearInterval: () => {}, setInterval: () => 1,
    };
    const names = Object.keys(scope);
    const api = new Function(...names,
      CONSTS + "\n" + fnSrc("fmtElapsed") + "\n" + fnSrc("elevReadElapsed")
      + "\n" + fnSrc("elevBusyLine") + "\nlet _elevTicker = null;\n"
      + fnSrc("elevTickStart") + "\n" + fnSrc("elevTickStop") + "\n"
      + fnSrc("elevTickPaint")
      + "\nreturn { paint: elevTickPaint, HEAD: EW_SUGGEST_HEAD };")
      (...names.map(n => scope[n]));
    return { api, els, put };
  }
  const HEAD = "reading this sheet's elevation labels";
  {
    const hint = { textContent: "reading elevation labels…" };
    const s = tickScope(1_000_000 - 95_000,
      { busy: HEAD + " — x…", status: HEAD + " — x",
        hint: hint });
    s.api.paint();
    check(/1m 35s/.test(s.els.ewBusy.textContent),
      "the panel's busy line gets the clock  (got "
      + JSON.stringify(s.els.ewBusy.textContent) + ")");
    check(/1m 35s/.test(s.els.status.textContent),
      "so does the status bar - the two surfaces every other long operation "
      + "in this file writes to");
    check(/1m 35s/.test(hint.textContent),
      "and the in-panel hint where the rows will appear");
  }
  {
    // THE RULE THAT KEEPS A ONCE-A-SECOND REPAINT HONEST
    const s = tickScope(1_000_000 - 95_000,
      { busy: "removed 12 lines", status: "detecting…" });
    s.api.paint();
    eq(s.els.status.textContent, "detecting…",
      "a status bar carrying somebody else's message is left alone");
    eq(s.els.ewBusy.textContent, "removed 12 lines",
      "...and so is the panel's busy line");
  }
  {
    const s = tickScope(null, { busy: HEAD + " — x…", status: HEAD });
    s.api.paint();
    eq(s.put.length, 0,
      "with nothing in flight the tick writes nothing and stops itself");
  }
}

// ---------------------------------------------------------------------------
console.log("6. the elevation readout shows the clock too");
// ---------------------------------------------------------------------------
{
  function elem(tag) {
    return { tagName: tag, className: "", textContent: "", title: "",
             hidden: false, style: {}, children: [],
             appendChild(c) { this.children.push(c); return c; },
             setAttribute() {}, querySelector() { return null; } };
  }
  function readout(elev, t0) {
    const box = elem("div");
    const state = { result: {}, elev: elev,
                    suggestReq: t0 === null ? null : { t0 } };
    const scope = {
      $: () => box, state, document: { createElement: elem },
      Date: { now: () => 1_000_000 },
      ELEV_FAIL: "retry",
      elevChipTag: () => "", elevProvenanceDetail: () => "",
    };
    const names = Object.keys(scope);
    new Function(...names,
      fnSrc("fmtElapsed") + "\n" + fnSrc("elevReadElapsed") + "\n"
      + fnSrc("renderSelElev") + "\nreturn renderSelElev;")
      (...names.map(n => scope[n]))({ chain_id: 1 }, undefined, null);
    return box.children.map(c => c.textContent).join("|");
  }
  const t = readout({ status: "loading", byChain: null }, 1_000_000 - 217_200);
  check(/3m 37s/.test(t),
    "the readout says how long the read has been going  (got "
    + JSON.stringify(t) + ")");
  const idle = readout({ status: "idle", byChain: null }, 1_000_000 - 30_000);
  check(/reading/i.test(idle),
    "a read the EARTHWORK panel started - state.elev still `idle` - is "
    + "reported as a read in progress, not as 'not read yet'");
  const none = readout({ status: "ready", byChain: new Map() }, null);
  check(/none printed/i.test(none) && !/reading/i.test(none),
    "and with nothing in flight it goes back to the settled sentence");
}

// ---------------------------------------------------------------------------
console.log("7. a failed read repaints the readout, it does not freeze it");
// ---------------------------------------------------------------------------
// Measured live at T+720 s on the 12-minute deadline: the panel, the busy
// line and the status bar all switched to the timeout sentence and the
// ELEVATION box was still reading "reading this sheet's labels…". Cause:
// suggestOnce's own settle handler repaints first, while state.elev is
// still `loading`, and nothing repainted after ensureElev wrote `failed`.
const suite7 = (async () => {
  const painted = [];
  const bars = [];
  let asks = 0;
  const state = { elev: { status: "idle", byChain: null }, gen: 1, job: "J",
                  page: 3, selected: 42 };
  const said = [];
  const scope = {
    state,
    suggestOnce: async () => {
      asks++;
      throw Object.assign(new Error("x"), { timeout: true });
    },
    setStatus: (t, cls) => said.push({ t, cls }),
    ELEV_FAIL: "generic",
    EW_SUGGEST_TIMEOUT: "no answer in 12 minutes",
    // updateSelBar RE-ASKS out of the `failed` state - that is the
    // operator's retry. If the failure handler calls it, the read re-fires
    // as fast as it can fail.
    updateSelBar: (ch) => { bars.push(ch); },
    renderSelElev: (ch) => painted.push(ch),
    // the tick goes through the wrapper, which re-derives the reading
    // from byChain - a chain-only repaint is what printed a false
    // "none printed for this line" over a loaded elevation
    repaintSelElev: (ch) => painted.push(ch),
    chainById: (id) => ({ chain_id: id }),
  };
  const names = Object.keys(scope);
  await new Function(...names, fnSrc("ensureElev") + "\nreturn ensureElev;")
    (...names.map(n => scope[n]))();
  eq(state.elev.status, "failed", "the read is recorded as failed");
  eq(said.length && said[0].t, "no answer in 12 minutes",
    "the status bar gets the TIMEOUT sentence, not the generic one");
  eq(painted.length, 1,
    "THE FIXED DEFECT: the readout is repainted after `failed` is written, "
    + "so the elevation box stops claiming to be reading");
  check(painted[0] && painted[0].chain_id === 42,
    "...for the line that is actually selected");
  eq(bars.length, 0,
    "and it repaints the READOUT ONLY, never the whole selection bar - "
    + "updateSelBar re-asks out of `failed`, so calling it here is a retry "
    + "loop that re-fires the read as fast as it can fail (reproduced live "
    + "on a dropped connection: the panel showed its failure sentence while "
    + "state.elev sat on `loading` forever)");
  eq(asks, 1, "...so exactly one request was issued");
})();

Promise.all([suite3, suite7]).then(() => {
  if (fails) { console.error("\n" + fails + " check(s) failed"); process.exit(1); }
  console.log("\nall elevation-read progress checks passed");
}).catch(e => {
  console.error("  FAIL: an async suite threw " + (e && e.stack || e));
  process.exit(1);
});
