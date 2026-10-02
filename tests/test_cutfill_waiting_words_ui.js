// A CUT / FILL READ WAITING FOR ANOTHER SAYS "WAITING IN LINE" WHERE THE CLIENT LOOKS (Koby, 2026-10-01: "When a
// client's cut / fill is waiting for another one, the screen should say it is waiting in line.").
// The engine answers a waiting read with {status: "queued", progress: "Waiting in line. Another sheet is being read
// first."} (tests/test_cutfill_waiting_words.py). Pinned here:
//   engine page  - the status line shows the sentence as it is ("Sheet C3.0: Waiting in line. ..."), not
//                  "Reading sheet C3.0: ...…"; the Sheets card's pill says "waiting in line"; the same holds on the
//                  first answer of Analyze; a read being stopped / still stopping is shown in its own words
//   host page    - the engine tells the host's cut / fill bar, which shows the words until the read moves on
//   no give-up   - the page keeps polling a waiting read; nothing turns it into an error on the page side
//
// run: node tests/test_cutfill_waiting_words_ui.js
"use strict";
const fs = require("fs");
const path = require("path");
const eng = fs.readFileSync(path.join(__dirname, "..", "webui", "cutfill", "app.js"), "utf8");
const host = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(src, name) {
  let start = src.indexOf("function " + name + "(");
  if (start < 0) return "";
  if (src.slice(start - 6, start) === "async ") start -= 6;
  let depth = 0;
  for (let j = src.indexOf("{", start); j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}" && --depth === 0) return src.slice(start, j + 1);
  }
  return "";
}
const WORDS = "Waiting in line. Another sheet is being read first.";
const STOPPING = "This sheet took more than 15 minutes to read and is being stopped.";
const STILL = "The last read of this sheet is still stopping. Try again in a minute.";

// ---------------------------------------------------------------- engine page
const names = ["readingLine", "readPill", "readingToHost", "pollAnalysis", "startAnalysis"];
check(names.every((n) => fnSrc(eng, n)), "the engine's status functions are found");
const posted = [], status = [];
let tick = null, answer = null, posts = 0;
const env = {
  state: { pid: "job_x", key: "d0p2", polls: {}, analyzes: {}, analysisMissing: {} },
  sheetName: () => "C3.0",
  postToHost: (m) => posted.push(m),
  setStatus: (t, o) => status.push({ t, o }),
  applyStatus: () => {}, stopPoll: () => { env.state.polls = {}; }, resolveWaiters: () => {}, firstLine: (s) => String(s).split("\n")[0],
  GET: async () => answer, POST: async () => { posts++; return answer; },
  setInterval: (f) => { tick = f; return 1; }, clearInterval: () => {},
  storeAnalysis: () => {}, ensureAnalysis: async () => {}, refreshTransforms: () => {}, readNotesLine: () => "",
  renderLineInfo: () => {}, markStale: () => {},
};
const keys = Object.keys(env);
const mk = new Function(...keys, names.map((n) => fnSrc(eng, n)).join("\n") + "\nreturn { " + names.join(", ") + " };");
const E = mk(...keys.map((k) => env[k]));

check(E.readingLine("d0p2", { status: "queued", progress: WORDS }) === "Sheet C3.0: " + WORDS,
  `status line of a waiting read: "${E.readingLine("d0p2", { status: "queued", progress: WORDS })}"`);
check(E.readingLine("d0p2", { status: "running", progress: "joining lines" }) === "Reading sheet C3.0: joining lines…",
  "a read at work still says its step");
check(E.readingLine("d0p2", { status: "running", progress: STOPPING }) === "Sheet C3.0: " + STOPPING,
  "a read being stopped says so in its own words");
check(E.readPill({ status: "queued", progress: WORDS }) === "waiting in line", "the Sheets card's pill: waiting in line");
check(E.readPill({ status: "queued", progress: "queued" }) === "queued…" && E.readPill({ status: "running", progress: "x" }) === "running…",
  "a read just started or at work keeps its pill");

(async () => {
  // the page's own polling of a waiting read
  E.pollAnalysis("d0p2");
  for (let i = 0; i < 40; i++) {               // 40 polls = a minute of waiting: still waiting, never an error
    answer = { status: "queued", progress: WORDS };
    await tick();
  }
  const last = status[status.length - 1];
  check(status.length === 40 && status.every((s) => s.t === "Sheet C3.0: " + WORDS && !(s.o && s.o.error)),
    `the status line says it waits in line on every poll, and never turns it into an error (${status.length} polls)`);
  check(posted.length === 40 && posted.every((m) => m.type === "reading" && m.key === "d0p2" && m.pid === "job_x"
    && m.text === "Sheet C3.0: " + WORDS), "each poll tells the host's bar the same words");
  answer = { status: "running", progress: "joining lines" };
  await tick();
  check(posted[posted.length - 1].text === "" && status[status.length - 1].t === "Reading sheet C3.0: joining lines…",
    "once it is read, the host's bar is told it moved on");

  // the first answer of Analyze, while another read holds the slot, or while this sheet's last read still stops
  status.length = 0; posted.length = 0; env.state.polls = {};
  answer = { status: "queued", progress: WORDS };
  await E.startAnalysis("d0p2", false, false);
  check(status[0] && status[0].t === "Sheet C3.0: " + WORDS && posted[0] && posted[0].text === "Sheet C3.0: " + WORDS,
    `Analyze's first answer already says it waits in line: "${status[0] && status[0].t}"`);
  status.length = 0; env.state.polls = {};
  answer = { status: "running", progress: STILL };
  await E.startAnalysis("d0p2", true, true);
  check(status[0] && status[0].t === "Sheet C3.0: " + STILL, `Re-read while the last read still stops: "${status[0] && status[0].t}"`);

  // ---------------------------------------------------------------- host page: the cut / fill bar
  const hn = ["cfReadingMessage", "cfReadingSay", "cfRenderBar", "cfSentence"];
  check(hn.every((n) => fnSrc(host, n)), "the host's bar functions are found");
  const els = {};
  const $ = (id) => (els[id] = els[id] || { textContent: "", title: "", disabled: false });
  const hstate = { job: "J1" };
  const cfState = { job: "J1", pid: "job_J1", summary: null, hint: "" };
  const henv = { $, state: hstate, cfState, cfRowsHere: () => false, cfAdded: () => false, CF_ADD_TITLE: "",
    ewOldBarHint: () => "", cfRows: () => [], cfScaleNote: () => "" };
  const hk = Object.keys(henv);
  const H = new Function(...hk, hn.map((n) => fnSrc(host, n)).join("\n") + "\nreturn { " + hn.join(", ") + " };")(
    ...hk.map((k) => henv[k]));
  check(H.cfReadingMessage({ type: "reading", pid: "job_J1", key: "d0p2", text: "Sheet C3.0: " + WORDS }) === true,
    "the host takes the engine's message for its own job");
  H.cfRenderBar();
  check($("cfNote").textContent.startsWith("Sheet C3.0: " + WORDS), `the host's cut / fill bar: "${$("cfNote").textContent}"`);
  check(H.cfReadingMessage({ type: "reading", pid: "job_OTHER", key: "d0p2", text: "x" }) === false,
    "a message from another job's engine is not shown");
  H.cfReadingMessage({ type: "reading", pid: "job_J1", key: "d0p2", text: "" });
  H.cfRenderBar();
  check(!$("cfNote").textContent.includes("Waiting in line"), "once the read moves on, the bar no longer says it waits");

  console.log(fails ? `\nFAILED: ${fails}` : "\nall ok");
  process.exit(fails ? 1 : 0);
})();
