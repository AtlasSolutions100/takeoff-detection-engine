// THE TAB THAT NEVER HEARD THE SCALE CARD, ON THE PAGE (critic 23, RISK-1). tests/test_scale_deaf_tab.py is the server.
//
// A. The server is the one source of a sheet's scale: scaleRestore asks the review record on EVERY load, also when this
//    browser saved a scale for the page (it returned before asking, so a saved 45 beat the reviewer's 50 through every
//    reload), and the review wins: the saved scale is dropped and that is said. It keeps the record's stamp for Approve.
// B. The approve sends that stamp, and takes the new one from its answer.
// C. Cut / fill in a tab of its own posts a scale change to the page that opened it (same origin only); the host's
//    listener is the same one the frame posts to.
//
// run: node tests/test_scale_deaf_tab_ui.js
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const ejs = fs.readFileSync(path.join(__dirname, "..", "webui", "cutfill", "app.js"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(src, name) {
  let start = src.indexOf("async function " + name + "(");
  if (start < 0) start = src.indexOf("function " + name + "(");
  if (start < 0) return "";
  let depth = 0;
  for (let j = src.indexOf("{", start); j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}" && --depth === 0) return src.slice(start, j + 1);
  }
  return "";
}

// ---- A. scaleRestore --------------------------------------------------------------------------------------------------
const ENG = { ft_per_paper_inch: 45, text: '1" = 45\'', source: "caption", agrees: true, candidates: [{ ft_per_paper_inch: 45, text: '1" = 45\'' }] };
async function restore(o) {
  const mem = new Map(Object.entries(o.saved || {}));
  const store = { getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: (k) => mem.delete(k) };
  const st = { job: "J", page: 0, gen: 7, docName: "plan.pdf", result: { scale: Object.assign({}, o.screen || ENG) } };
  const out = { said: [], repaint: 0, asked: 0 };
  const fetch = async (u) => {
    out.asked++;
    if (o.move) st.gen = 8;                                     // the page changed while the record was asked
    return { ok: true, json: async () => o.answer };
  };
  const run = new Function("state", "store", "fetch", "setStatus", "scaleRepaint", "scaleSuggestions", "suggestedScaleObj", "renderSheetHead",
    "const SCALE_FT_MIN = 0.0833, SCALE_FT_MAX = 1000;\n"
    + ["scaleSane", "scaleTypedObj", "confFt", "scaleRestore", "scaleSeenKey"].map((n) => fnSrc(js, n)).join("\n")
    + '\nfunction scaleKey() { return "scale::" + state.docName + "::p" + state.page; }'
    + '\nfunction scaleTypedKey() { return "scaletyped::" + state.docName + "::p" + state.page; }'
    + "\nreturn scaleRestore;")(st, store, fetch, (m, k) => out.said.push([m, k]), () => { out.repaint++; }, () => [], (s) => s, () => {});
  await run(7);
  out.scale = st.result.scale; out.seen = st.result._scaleSeen; out.mem = Object.fromEntries(mem);
  return out;
}
const PICK50 = { stamp: "rA", recorded: true, reviewer: { ft_per_paper_inch: 50, text: '1" = 50\'' }, engine: { ft_per_paper_inch: 45, text: '1" = 45\'', source: "caption" } };
const TOMB = { stamp: "rT", recorded: true, reviewer: null, engine: { ft_per_paper_inch: 45, text: '1" = 45\'', source: "caption" } };
const NONE = { stamp: "none", recorded: false, reviewer: null, engine: { ft_per_paper_inch: 45, text: '1" = 45\'', source: "caption" } };
const TYPED40 = Object.assign({}, ENG, { ft_per_paper_inch: 40, text: '1" = 40\'', typed_by_reviewer: true, picked_by_reviewer: true, agrees: null });

(async () => {
  // A1: this browser saved a typed 40 (scaleMemApply put it on screen); the review record says 50
  const a1 = await restore({ saved: { "scaletyped::plan.pdf::p0": JSON.stringify({ ft: 40, text: '1" = 40\'' }) }, screen: TYPED40, answer: PICK50 });
  check(a1.asked === 1 && a1.scale.ft_per_paper_inch === 50 && a1.repaint === 1 && a1.seen === "rA"
    && !a1.mem["scale::plan.pdf::p0"] && JSON.parse(a1.mem["scaletyped::plan.pdf::p0"] || "{}").ft === 50,
    "A: a scale this browser saved (40) no longer stops the page asking the record; the review's 50 wins, the browser now "
    + "holds 50, the stamp is kept: " + JSON.stringify([a1.asked, a1.scale.ft_per_paper_inch, a1.seen]));
  check(a1.said.length === 1 && /This sheet's scale is 1" = 50 ft, from its review\. The scale this browser had saved \(1" = 40 ft\) was dropped\./.test(a1.said[0][0]),
    "A: ...and it is said in one plain line: " + JSON.stringify(a1.said));
  // A2: the browser picked the engine's own 45 earlier (the candidate key); the record says 50
  const a2 = await restore({ saved: { "scale::plan.pdf::p0": "45" }, answer: PICK50 });
  check(a2.asked === 1 && a2.scale.ft_per_paper_inch === 50 && a2.scale.typed_by_reviewer === true && !a2.mem["scale::plan.pdf::p0"]
    && /\(1" = 45 ft\) was dropped/.test((a2.said[0] || [""])[0]),
    "A: a saved pick of the engine's 45 (critic 23, case C) gives way to the record's 50, said");
  // A3: the record went BACK to the engine's reading (a tombstone); the browser had typed 40
  const a3 = await restore({ saved: { "scaletyped::plan.pdf::p0": JSON.stringify({ ft: 40, text: '1" = 40\'' }) }, screen: TYPED40, answer: TOMB });
  check(a3.scale.ft_per_paper_inch === 45 && a3.scale.typed_by_reviewer === false && a3.scale.picked_by_reviewer === false
    && a3.scale.text === '1" = 45\'' && !a3.mem["scaletyped::plan.pdf::p0"] && a3.seen === "rT" && /\(1" = 40 ft\) was dropped/.test((a3.said[0] || [""])[0]),
    "A: a record that went back to the engine's reading (a tombstone) wins too: the engine's 45, not a typed number");
  // A4: no record on file: no review decision - this browser's own unapproved pick stands, the stamp is kept
  const a4 = await restore({ saved: { "scaletyped::plan.pdf::p0": JSON.stringify({ ft: 40, text: '1" = 40\'' }) }, screen: TYPED40, answer: NONE });
  check(a4.asked === 1 && a4.scale.ft_per_paper_inch === 40 && a4.seen === "none" && !a4.said.length && !a4.repaint,
    "A: no record on file: nothing to give way to - the browser's 40 stays, nothing said, the stamp 'none' kept");
  // A5: the record agrees with the screen
  const a5 = await restore({ saved: { "scaletyped::plan.pdf::p0": JSON.stringify({ ft: 50, text: '1" = 50\'' }) },
    screen: Object.assign({}, TYPED40, { ft_per_paper_inch: 50 }), answer: PICK50 });
  check(a5.scale.ft_per_paper_inch === 50 && a5.seen === "rA" && !a5.said.length && !a5.repaint, "A: a record that agrees: the stamp only");
  // A6: the page changed while the record was asked: nothing of it lands
  const a6 = await restore({ saved: {}, answer: PICK50, move: true });
  check(a6.scale.ft_per_paper_inch === 45 && a6.seen === undefined && !a6.said.length, "A: an answer for a page that is gone changes nothing");
  // A: the restore runs on every load (loadResult), and again when the Scale card is heard (the listener)
  const lr = fnSrc(js, "loadResult");
  const li = js.indexOf('if (e.data.type === "scale") {');
  const lsc = js.slice(li, js.indexOf("\n    return;\n  }", li));
  check(/scaleMemApply\(\);[\s\S]*scaleRestore\(gen\);/.test(lr) && /scaleRestore\(state\.gen\)/.test(lsc),
    "A: loadResult asks the record after the browser's memory is applied, and the listener asks it again on a heard post");

  // ---- B. the approve carries the stamp, and takes the new one --------------------------------------------------------
  const ap = js.slice(js.indexOf('async function approveRun() {'), js.indexOf("// ---------- legend sheet vocabulary"));
  // (fix/scale-deaf-tab-2: the payload's scale part is one function, approveScaleParts, held against the screen in S1)
  check(/const \{ scale: scaleOnScreen, scale_seen: scaleSeenSent \} = approveScaleParts\(\);/.test(ap)
    && /scale: scaleOnScreen,/.test(ap) && /scale_seen: scaleSeenSent,/.test(ap)
    && /approveScaleRecorded\(j\.scale_stamp,/.test(ap) && /approveScaleRecorded\(e\.detail\.scale_stamp,/.test(ap)
    && /scale_seen: \(state\.result && state\.result\._scaleSeen\) \|\| null/.test(fnSrc(js, "approveScaleParts")),
    "B: Approve sends the stamp of the record the page loaded, and takes the record's new stamp from its answer (and from a "
    + "cut / fill refusal that came after the scale was recorded)");

  // ---- S1 (critic 24, RISK-1): a scale typed here and not approved yet survives a page switch, Restore and a reload ------
  // A page: localStorage (shared by every load and every tab of this browser), the server's record of page 0 (stamp +
  // number), and the page's own functions out of app.js. load() is loadResult's scale steps (selectPage, Restore and a
  // reload all end there): a fresh result, scaleMemApply, then scaleRestore. Every path ends by holding the approve
  // payload (approveScaleParts) against the scale the screen shows, and the chip beside it.
  const have = (n) => !!fnSrc(js, n);
  const srv = { stamp: "s1", rec: 50, n: 1 };
  const mem = new Map();
  const mkPage = () => {
    const st = { job: "J", page: 0, gen: 0, docName: "plan.pdf", result: null };
    const store = { getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: (k) => mem.delete(k) };
    const fetch = async () => ({ ok: true, json: async () => (st.page === 0
      ? { stamp: srv.stamp, recorded: true, reviewer: { ft_per_paper_inch: srv.rec, text: `1" = ${srv.rec}'` }, engine: { ft_per_paper_inch: 45, text: '1" = 45\'', source: "caption" } }
      : { stamp: "none", recorded: false, reviewer: null, engine: { ft_per_paper_inch: 45, text: '1" = 45\'', source: "caption" } }) });
    const names = ["scaleSane", "scaleTypedObj", "confFt", "scaleKey", "scaleTypedKey", "applyTypedScale", "scaleMemApply", "scaleRestore",
      "scaleSeenKey", "scaleMarkSeen", "approveScaleParts", "approveScaleRecorded", "scaleRecordChip"].filter(have);
    const fallback = ""      // the page before this fix had none of these: its approve built the same scale part inline
      + (have("scaleSeenKey") ? "" : "function scaleSeenKey() { return 'n/a'; }\n")
      + (have("scaleMarkSeen") ? "" : "function scaleMarkSeen() {}\n")
      + (have("scaleRecordChip") ? "" : "function scaleRecordChip() { return null; }\n")
      + (have("approveScaleRecorded") ? "" : "function approveScaleRecorded(s) { state.result._scaleSeen = s; }\n")
      + (have("approveScaleParts") ? "" : "function approveScaleParts() { const scOn = state.result && state.result.scale; return { scale: scOn ? "
        + "{ ft_per_paper_inch: +scOn.ft_per_paper_inch } : null, scale_seen: (state.result && state.result._scaleSeen) || null }; }\n");
    const f = new Function("state", "store", "fetch", "setStatus", "scaleRepaint", "scaleSuggestions", "suggestedScaleObj", "renderSheetHead",
      "const SCALE_FT_MIN = 0.0833, SCALE_FT_MAX = 1000;\nconst scaleUndo = { key: null, scale: undefined };\n"
      + names.map((n) => fnSrc(js, n)).join("\n") + "\n" + fallback
      + "return { applyTypedScale, scaleMemApply, scaleRestore, approveScaleParts, approveScaleRecorded, scaleRecordChip };")(
      st, store, fetch, () => {}, () => {}, () => [], (s) => s, () => {});
    const page = {
      st,
      async load(p) {
        st.page = p; st.gen++;
        st.result = { scale: { ft_per_paper_inch: 45, text: '1" = 45\'', source: "caption", agrees: true, candidates: [] } };
        f.scaleMemApply();
        await f.scaleRestore(st.gen);
      },
      type: (ft) => f.applyTypedScale(ft, `1" = ${ft}'`),
      // the approve, with the server's stamp rule (_scale_seen_check): what it sends, and whether the server takes it
      approve() {
        const pl = f.approveScaleParts();
        const ft = pl.scale && pl.scale.ft_per_paper_inch;
        const shown = st.result.scale && st.result.scale.ft_per_paper_inch;
        let ok = !!pl.scale_seen;
        if (ok && pl.scale_seen !== srv.stamp && Math.abs(ft - srv.rec) > 1e-9) ok = false;
        if (ok) { srv.rec = ft; srv.stamp = "s" + (++srv.n); f.approveScaleRecorded(srv.stamp, ft); }
        return { sent: ft, shown, ok };
      },
      chip: () => f.scaleRecordChip(),
    };
    return page;
  };
  const otherTab = (ft) => { srv.rec = ft; srv.stamp = "s" + (++srv.n); };      // another tab / seat / the Scale card
  const reset = () => { mem.clear(); srv.rec = 50; srv.stamp = "s" + (++srv.n); };
  const paths = {};
  // path 1: type -> switch page -> back -> approve
  reset();
  let pg = mkPage();
  await pg.load(0); pg.type(40); await pg.load(1); await pg.load(0);
  paths.switch = Object.assign({ chip: pg.chip() }, pg.approve());
  // path 2: type -> Restore (loadResult again) -> approve
  reset();
  pg = mkPage();
  await pg.load(0); pg.type(40); await pg.load(0);
  paths.restore = Object.assign({ chip: pg.chip() }, pg.approve());
  // path 3: type -> reload (a new page; localStorage kept) -> approve
  reset();
  pg = mkPage();
  await pg.load(0); pg.type(40);
  pg = mkPage();
  await pg.load(0);
  paths.reload = Object.assign({ chip: pg.chip() }, pg.approve());
  // path 4: type -> another tab changes the review (55) -> switch away and back -> approve: the newer record wins, said
  reset();
  pg = mkPage();
  await pg.load(0); pg.type(40); otherTab(55); await pg.load(1); await pg.load(0);
  paths.otherBack = Object.assign({ chip: pg.chip() }, pg.approve());
  // path 4b: type -> another tab changes the review -> approve without leaving the page: refused by the stamp, as now
  reset();
  pg = mkPage();
  await pg.load(0); pg.type(40); otherTab(55);
  paths.otherStay = pg.approve();
  // path 5: a typing saved by the page before this fix (no stamp with it): older than any record, the record wins
  reset();
  mem.set("scaletyped::plan.pdf::p0", JSON.stringify({ ft: 40, text: '1" = 40\'' }));
  pg = mkPage();
  await pg.load(0);
  paths.legacy = Object.assign({ chip: pg.chip() }, pg.approve());
  console.log("S1 paths: " + JSON.stringify(paths));
  const same = Object.values(paths).every((p) => p.sent === p.shown);
  check(same, "S1: on every path the approve sends exactly the scale the screen shows");
  for (const k of ["switch", "restore", "reload"])
    check(paths[k].shown === 40 && paths[k].sent === 40 && paths[k].ok && /^not recorded: the sheet's record says 1" = 50 ft - set the scale again to record this one$/.test(paths[k].chip || ""),
      `S1 (${k}): the 40 typed after the record stays on screen, says it is not recorded (no word of approving: the takeoff may be empty), and the approve sends and records `
      + `40 - it went back to the record's 50 and exported 50 with nothing flagged: ` + JSON.stringify(paths[k]));
  check(paths.otherBack.shown === 55 && paths.otherBack.sent === 55 && paths.otherBack.ok
    && /^your saved 1" = 40 ft was dropped: the review changed it after you set it$/.test(paths.otherBack.chip || ""),
    "S1: the review changed elsewhere AFTER the typing: the newer record (55) wins, and the chip beside the scale says the 40 "
    + "was dropped (not a status line another message overwrites): " + JSON.stringify(paths.otherBack));
  check(paths.otherStay.sent === 40 && !paths.otherStay.ok, "S1: ...and a page that stayed put sends its 40 and is refused by the stamp, as before");
  check(paths.legacy.shown === 50 && paths.legacy.sent === 50 && /dropped/.test(paths.legacy.chip || ""),
    "S1: a typing saved with no stamp (the page before this fix) counts as older than any record: the record wins, said");

  // ---- C. Cut / fill in a tab of its own tells the page that opened it --------------------------------------------------
  const post = (win) => new Function("window", "location", fnSrc(ejs, "postToHost") + "\n" + fnSrc(ejs, "postToOpener") + "\nreturn postToHost;")(win, { origin: "http://x" });
  const sentTo = [];
  const opener = { closed: false, postMessage: (m, o) => sentTo.push(["opener", m, o]) };
  const tab = { opener };
  tab.parent = tab;                                     // a top-level tab: its parent is itself
  post(tab)({ type: "scale", pid: "job_A", job: "A", page: 0, ft: 50, text: '1" = 50\'' });
  post(tab)({ type: "result", pid: "job_A", result: {} });
  const framed = { opener, parent: { postMessage: (m, o) => sentTo.push(["parent", m, o]) } };
  post(framed)({ type: "scale", pid: "job_A", job: "A", page: 0, ft: 50 });
  const alone = { opener: null };
  alone.parent = alone;
  post(alone)({ type: "scale", pid: "job_A", job: "A", page: 0, ft: 50 });
  const closed = { opener: { closed: true, postMessage: () => sentTo.push(["closed"]) } };
  closed.parent = closed;
  post(closed)({ type: "scale", pid: "job_A", job: "A", page: 0, ft: 50 });
  check(sentTo.length === 2 && sentTo[0][0] === "opener" && sentTo[0][1].source === "cutfill" && sentTo[0][1].type === "scale"
    && sentTo[0][1].ft === 50 && sentTo[0][2] === "http://x" && sentTo[1][0] === "parent",
    "C: in a tab of its own a Scale-card change is posted to the page that opened it, same origin only; results are not; "
    + "in the frame it goes to the parent only; no opener or a closed one, nothing: " + JSON.stringify(sentTo.map((x) => x[0])));
  // the host hears it with the frame's own listener: the message is checked by origin, project and job, never by sender
  const lsrc = js.slice(js.lastIndexOf('window.addEventListener("message", (e) => {', li), li);
  check(/e\.origin !== location\.origin/.test(lsrc) && !/e\.source/.test(lsrc),
    "C: the host's listener takes the post whether the frame or the tab sent it (origin, project and job checked)");

  console.log(fails ? `\n${fails} FAILED` : "\nall ok");
  process.exit(fails ? 1 : 0);
})();
