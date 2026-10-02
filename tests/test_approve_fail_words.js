// A FAILED APPROVE SAYS SO (UI verify 2026-10-02 #9). With the approve request aborted the page
// showed nothing (status empty, "Failed to fetch" only in the console) and a contractor believes
// it approved. Runs the real approveRun out of webui/app.js against a stubbed fetch that fails
// every way it can - no answer, a 500 with an HTML body, a refusal with and without `detail`, a
// list of field errors, a 200 that is not JSON - plus a throw while the payload is built, and
// checks that each one leaves a plain sentence in the status line and the button usable.
//
// run: node tests/test_approve_fail_words.js
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };

const a = js.indexOf("// APPROVE-FAIL-BEGIN"), b = js.indexOf("// APPROVE-FAIL-END");
check(a > 0 && b > a, "APPROVE-FAIL block present");
const s = js.indexOf("async function approveRun() {");
let e = -1;
for (let k = js.indexOf("{", s), d = 0; k < js.length; k++) {
  if (js[k] === "{") d++;
  else if (js[k] === "}" && --d === 0) { e = k + 1; break; }
}
check(s > 0 && e > s, "approveRun found");
check(/\$\("approveBtn"\)\.onclick = \(\) => approveClick\(approveRun, setStatus\);/.test(js),
  "the button runs approveRun through approveClick, which catches what approveRun throws");
check(!/approveBtn"\)\.disabled = true/.test(js.slice(s, e)), "approveRun never disables the button, so it stays usable");

const GLOBALS = new Set(["JSON", "Object", "Number", "Array", "String", "Set", "Map", "Promise", "Math", "parseFloat",
  "Boolean", "Error", "TypeError", "undefined", "Infinity", "NaN", "isFinite", "console", "Symbol"]);
function harness(fetchImpl, o = {}) {
  const out = { status: [], els: {} };
  const env = {
    state: { job: "j1", page: 0, gen: 1, include: new Map([[1, true]]), labels: new Map([[1, "STORM"]]), chainLabels: new Map(),
             extraItems: [], excluded: new Set(), lineColor: new Map(), once: new Set(), workZones: [],
             result: { chains: [], styles: [{ style_id: 1 }] } },
    subExportRows: () => [],
    nWord: new Function(js.slice(js.indexOf("function nWord("), js.indexOf("\n}\n", js.indexOf("function nWord(")) + 3) + "return nWord;")(),
    computeTakeoffRows: o.throwInPayload ? () => { throw new Error("rows broke"); } : () => ({ groups: [] }),
    tradeLayerMap: () => new Map(), approveScaleParts: () => ({ scale: { ft_per_paper_inch: 20 }, scale_seen: null }),
    tradeFor: () => "General", styleColorInfo: () => ({ src: "engine" }), colorNorm: () => null,
    famMineExportStyles: () => [], onceFamily: () => false, wzActive: () => false,
    store: { getItem: () => null }, priceKey: l => "price:" + l, approveNotes: () => [],
    setStatus: (m, k) => { out.status.push([m, k]); },
    fetch: fetchImpl,
    approveScaleRecorded() {}, approvalOutdated: () => Promise.resolve(), approveNotesDropped: () => 0, APPROVE_NOTES_MAX: 6,
    cfSentence: x => String(x), cfEngineState: async () => null, cfApplyItems() {}, cfState: {}, cfRows: () => [],
    cfAdded: () => false, cfDropItems() {}, cfBackThenApprove: () => "", cfLeftNote() {},
    $: id => (out.els[id] = out.els[id] || { hidden: true, href: "" }), url: u => u, bsReportUrl: () => "r", bsCsvUrl: () => "c",
  };
  const proxy = new Proxy(env, { has: (t, k) => !GLOBALS.has(k) && typeof k === "string",
                                 get: (t, k) => (k in t ? t[k] : k === Symbol.unscopables ? undefined : undefined) });
  // non-strict body so `with` can hand approveRun its page around it
  const F = new Function("env", "with (env) {\n" + js.slice(a, b) + "\n" + js.slice(s, e)
    + "\nreturn { approveRun, approveClick, approveFailText, APPROVE_NO_ANSWER }; }");
  return { out, F: F(proxy), env };
}
const resp = (status, text) => ({ ok: status >= 200 && status < 300, status,
  json: async () => JSON.parse(text), text: async () => text });

(async () => {
  const cases = [
    ["no answer (the verifier's aborted POST)", () => Promise.reject(new TypeError("Failed to fetch")),
      m => /^Approve did not go through: the server did not answer\./.test(m)],
    ["a 500 with an HTML page", async () => resp(500, "<html>Internal Server Error</html>"),
      m => /^Approve did not go through: the server had a problem \(error 500\)\. Nothing new is approved\./.test(m)],
    ["a 502 from the proxy, empty body", async () => resp(502, ""), m => /server had a problem \(error 502\)/.test(m)],
    ["a refusal with no detail", async () => resp(403, "{}"), m => m === "Approve was refused (error 403). Nothing new is approved."],
    ["a refusal with detail: the server's words, whole", async () => resp(403, JSON.stringify({ detail: "Read-only: your role is Viewer. Ask an admin to change it." })),
      m => m === "Read-only: your role is Viewer. Ask an admin to change it."],
    ["a list of field errors", async () => resp(422, JSON.stringify({ detail: [{ loc: ["body", "scale"], msg: "bad number" }] })),
      m => m === "Approve was refused: scale: bad number"],
    ["a 200 that is not JSON", async () => resp(200, "<html>proxy login</html>"),
      m => /^The server answered Approve but its reply could not be read\./.test(m)],
  ];
  for (const [name, f, want] of cases) {
    const h = harness(f);
    let threw = null;
    await h.F.approveClick(h.F.approveRun, h.env.setStatus).catch(x => { threw = x; });
    const last = h.out.status[h.out.status.length - 1] || ["", ""];
    check(!threw && last[1] === "err" && want(last[0]), name + ": " + JSON.stringify(last[0]));
  }
  // a throw while the payload is built (before any request) is caught and said
  {
    const h = harness(async () => resp(200, "{}"), { throwInPayload: true });
    const errs = []; const ce = console.error; console.error = x => errs.push(x);
    await h.F.approveClick(h.F.approveRun, h.env.setStatus);
    console.error = ce;
    const last = h.out.status[h.out.status.length - 1] || ["", ""];
    check(last[1] === "err" && /^Approve stopped on this page before it finished \(rows broke\)\. Press Approve again\.$/.test(last[0]),
      "a throw inside approveRun is said, not lost: " + JSON.stringify(last[0]));
  }
  // and the success path still reports the export
  {
    const h = harness(async () => resp(200, JSON.stringify({ exported: 12, items: [], overrides: [], override_warnings: [] })));
    await h.F.approveClick(h.F.approveRun, h.env.setStatus);
    const last = h.out.status[h.out.status.length - 1] || ["", ""];
    check(/exported 12 elements/.test(last[0]) && last[1] === undefined && h.out.els.reportLink && h.out.els.reportLink.hidden === false,
      "a good approve still says what it exported and shows the report link");
    check(h.out.status[0][0] === "approving…", "while it waits, the status says approving…");
  }
  if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
  console.log("all approve-failure checks passed");
})();
