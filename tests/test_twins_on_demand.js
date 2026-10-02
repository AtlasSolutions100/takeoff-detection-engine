// THE TWIN-LINE PAIRING IS ASKED FOR, NEVER RUN ON PAGE LOAD (2026-09-26;
// audit D01), and DELETE SAYS WHAT HAPPENED TO THE PDF (D06).
//
// One visit to a civil sheet used to call GET .../twins from calcStateLoad
// and, on a set with a misread footing schedule, start ~29 minutes of O(n^2)
// work on the server. The page load no longer asks; the Structure panel, a
// saved "once" choice and the walls-once button do. The delete flows read
// pdf_removed / pdf_why from the reply instead of announcing "removed".
//
// run: node tests/test_twins_on_demand.js   (fails on the commit before it)
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(name) {
  let start = js.indexOf("\nfunction " + name + "(");
  if (start < 0) start = js.indexOf("\nasync function " + name + "(");
  if (start < 0) return "";
  let depth = 0;
  for (let j = js.indexOf("{", start); j < js.length; j++) {
    if (js[j] === "{") depth++;
    else if (js[j] === "}" && --depth === 0) return js.slice(start + 1, j + 1);
  }
  return "";
}

// ---- who asks for the pairing ------------------------------------------------------------
const load = fnSrc("calcStateLoad");
check(load && !/twinsLoad\(/.test(load), "calcStateLoad (every page load) no longer calls twinsLoad");
check(/twinsLoad\(/.test(fnSrc("stFootingsBlock")), "the Structure panel asks for it when it opens");
check(/twinsLoad\(/.test(fnSrc("onceApplySaved")) && /state\.once\.size/.test(fnSrc("onceApplySaved")),
      "a saved once choice on the page asks for it (the saved switch must bill one run)");
check(fnSrc("twinsAsk") && /twinsLoad\(state\.gen, true\)/.test(fnSrc("twinsAsk")), "twinsAsk asks and reports");
check(/id="onceAskBtn"/.test(html) && /onceAskBtn.*onclick = twinsAsk/.test(js), "the walls-once button in the Line styles header is wired to it");
check(/onceAskBtn"\)\.hidden = !state\.result \|\| !!state\.twins/.test(fnSrc("renderStyles")),
      "the button shows until the pairing has been asked for on this page");
const tl = fnSrc("twinsLoad");
check(/state\.twinsAsked === gen/.test(tl) && /reason/.test(tl), "one request per page load; the server's reason is reported when asked");

// ---- twinsLoad reports the reason, run for real ------------------------------------------------
const said = [];
const fetches = [];
const state = { job: "j", page: 0, gen: 7, twins: null, onceRev: 0 };
const H = new Function("state", "fetch", "setStatus", "renderStyles", "renderItemized", "renderMiniLegend", "draw",
  tl + "\n" + fnSrc("twinsAsk") + "\nreturn { twinsLoad, twinsAsk };")(
  state,
  async (url) => { fetches.push(url); return { ok: true, json: async () => ({ families: {}, reason: "a civil sheet (C3.0) - counting walls once is for foundation plans" }) }; },
  (t, kind) => said.push([t, kind]), () => {}, () => {}, () => {}, () => {});
(async () => {
  await H.twinsLoad(7);
  await H.twinsLoad(7);
  check(fetches.length === 1, "a second implicit call on the same page load does not fetch again");
  state.result = {};
  H.twinsAsk();
  await new Promise(r => setTimeout(r, 5));
  check(fetches.length === 2 && said.some(s => /civil sheet \(C3\.0\)/.test(s[0]) && s[1] === "err"),
        "asked explicitly, the status line carries the server's reason: " + JSON.stringify(said[said.length - 1]));

  // ---- delete says the truth ----------------------------------------------------------------
  const D = new Function(fnSrc("pvDeleteSentence") + "\n" + fnSrc("pvBulkReport") + "\nreturn { pvDeleteSentence, pvBulkReport };")();
  check(D.pvDeleteSentence({ name: "a.pdf", pdf_removed: true }) === "removed a.pdf", "a PDF that went: removed <name>");
  const kept = D.pvDeleteSentence({ name: "a.pdf", pdf_removed: false, pdf_why: "its PDF stays: another document on this server is the same file" });
  check(/^removed the detections and cut \/ fill work for a\.pdf — its PDF stays/.test(kept), "a PDF that stayed says so, with the server's reason: " + kept);
  check(/pdf_removed === false/.test(fnSrc("pvDeleteOne")) && /pvDeleteSentence\(j/.test(fnSrc("pvDeleteOne")), "pvDeleteOne carries the sentence and pdf_kept");
  check(/alert\(res\.said\)/.test(fnSrc("pvRemove")), "the card's remove holds the kept-PDF sentence in a dialog before the reload");
  const removeBtn = js.slice(js.indexOf('$("docRemoveBtn").onclick'), js.indexOf('$("docRemoveBtn").onclick') + 1800);
  check(/pvDeleteSentence\(j, meta\.name\)/.test(removeBtn) && /alert\(said\)/.test(removeBtn), "the header's Remove button does the same");
  check(D.pvBulkReport([{ name: "a", ok: true }], 0) === "removed 1 project — permanently", "a bulk report with every PDF gone is unchanged");
  const bulk = D.pvBulkReport([{ name: "a", ok: true, pdf_kept: "its PDF stays: x" }, { name: "b", ok: true }], 0);
  check(!/permanently/.test(bulk) && /1 kept its PDF on this server \(its PDF stays: x\)/.test(bulk), "a kept PDF is never folded into permanently: " + bulk);

  if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
  console.log("all twins-on-demand checks passed");
})();
