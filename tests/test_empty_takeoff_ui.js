// THE TAKEOFF STARTS EMPTY - the page half (2026-10-03; server half: tests/test_empty_takeoff.py and, for the repair
// after CRITIC-empty-1, tests/test_empty_repair.py). Pins the TICKS block of webui/app.js and the places that read it:
//   1. the words: the takeoff header, the approve refusal, the tick control's help line;
//   2. a fresh page opens EMPTY and writes nothing; an explicit record is applied as it is;
//   3. NOTHING IS BROUGHT OVER from an earlier version: with 2.0's tick records still in this browser's storage the
//      page reads none of them, shows the takeoff EMPTY and sends nothing - whatever the server calls the page; a
//      page 2.0 approved says so in one line, by what the takeoff here holds, with what was approved folded behind it;
//      a server that does not answer is said, and ticks are then not sent;
//   4. a tick sends the whole ADDED set on the version the server last gave; a change made while one is in flight
//      goes after it on the new version; a stale tab is told and stops writing;
//   5. the eye: after Detect the drawing shows the engine's picks while the takeoff is empty (one rule for every page);
//   6. a re-detect: what it did to the ticks, in one sentence (the styles dropped; another engine's detection);
//   7. the panels that quote the takeoff - the scope sentence, "Check these" - are drawn again after a tick, once;
//   8. setInclude writes nothing to the browser's storage; approve refuses an empty takeoff before asking the server,
//      sends the ticks first, refuses when the page holds no version of the server's record, and names the version;
//      the Takeoff panel says what to do; the Bid readiness row.
//
// run: node tests/test_empty_takeoff_ui.js      (fails on 888b2c1: the page brought 2.0's ticks over)
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(name) {
  let start = js.indexOf("\nfunction " + name + "(");
  if (start < 0) start = js.indexOf("\nasync function " + name + "(");
  if (start < 0) return "function " + name + "() { throw new Error('no function " + name + " in webui/app.js'); }";
  start += 1;
  let depth = 0;
  for (let j = js.indexOf("{", start); j < js.length; j++) {
    if (js[j] === "{") depth++;
    else if (js[j] === "}" && --depth === 0) return js.slice(start, j + 1);
  }
  return "";
}
const constLine = (name) => { const i = js.indexOf("const " + name + " = "); if (i < 0) return "const " + name + " = '';"; return js.slice(i, js.indexOf("\n", i)); };

// ---- 1. the words -------------------------------------------------------------------------------------------------
check(constLine("TK_EMPTY_HEAD").includes(JSON.stringify("Nothing in the takeoff yet. Tick the styles you want, or press Engine's picks.")),
  "the takeoff header's sentence");
check(constLine("TK_EMPTY_APPROVE").includes(JSON.stringify("Nothing is in the takeoff. Tick the styles you want first.")),
  "the approve refusal's sentence (the server's words exactly)");
const srv = fs.readFileSync(path.join(__dirname, "..", "server.py"), "utf8");
check(srv.includes('TAKEOFF_EMPTY = "Nothing is in the takeoff. Tick the styles you want first."'), "...the same words on the server");
check(constLine("TK_EMPTY_APPROVED").includes(JSON.stringify("To take this page out of the bid, use Withdraw approval."))
  && srv.includes('TAKEOFF_EMPTY_APPROVED = "To take this page out of the bid, use Withdraw approval."'),
  "on an approved page the refusal names Withdraw approval, in the same words on the page and the server (MINOR-3)");
check(/add it to the takeoff/.test(constLine("TK_HELP")) && /Nothing is in the takeoff until you add it/.test(constLine("TK_HELP")),
  "the help line says a tick ADDS and nothing is in until added");
check(/"add it to the takeoff\."/.test(fnSrc("styleTakeBtn")) && /TK_HELP/.test(fnSrc("styleTakeBtn"))
  && /not counted until you add it/.test(fnSrc("styleTakeBtn")), "the tick control's hover says the new rule");

// ---- the harness: the TICKS block's functions over a stub server -------------------------------------------------
const PURE = js.slice(js.indexOf("// TICKS-PURE-BEGIN"), js.indexOf("// TICKS-PURE-END"));
const FNS = ["tkFetch", "tkLoad", "tkApply", "tkDirty", "tkWrite", "tkFlush", "tkKey", "tkFollow", "includeSeed",
             "famVisDefault", "famVis", "famVisOverridden", "setFamVis", "setInclude", "tkReadiness",
             // a change the server did not keep for a reason that is not "stale": its words and the approve bar's note
             // (critic empty-2, MINOR-3; behaviour: tests/test_empty_repair2_ui.js)
             "tkUnsavedWords", "tkUnsavedApproveWords", "tkUnsavedShow", "tkBarFit"];
function harness(styles, server, mem) {
  const posts = [], said = [], reads = [], painted = { scope: 0, check: 0 };
  const state = { job: "j1", page: 0, gen: 1, result: { styles, engine_sig: "sig" }, include: new Map(),
                  inheritedInc: new Map(), tkSet: new Set(), tkSeed: new Map(), tkNote: "", tkEarlier: null,
                  vis: new Map(), labels: new Map() };
  for (const s of styles) state.include.set(s.style_id, false);          // as loadResult leaves it
  // the browser's storage, holding whatever 2.0 left there: every read is recorded (a tick record must never be read)
  const store = { getItem: (k) => { reads.push(k); return mem.has(k) ? mem.get(k) : null; },
                  setItem: (k, v) => { throw new Error("a tick wrote the browser's storage: " + k); },
                  removeItem: () => { throw new Error("removeItem"); } };
  const fetch = async (url, opt) => {
    if (!opt || !opt.method || opt.method === "GET") {
      const j = server.get();
      return j ? { ok: true, status: 200, json: async () => j } : { ok: false, status: 503, json: async () => ({}) };
    }
    const body = JSON.parse(opt.body);
    posts.push(body);
    const [st, j] = server.post(body);
    return { ok: st === 200, status: st, json: async () => j };
  };
  const env = { state, store, fetch, setStatus: (m, c) => said.push([m, c]), $: () => null,
    docKey: () => "doc::d", sgSuggested: (s) => !!s.sug, computeTakeoffRows: () => server.rows || { groups: [] },
    renderScopeBar: () => { painted.scope++; }, renderCheckQueue: () => { painted.check++; },
    renderItemized: () => { painted.items = (painted.items || 0) + 1; },
    // ONLY so this file also runs to its end on the tree BEFORE the repair, whose page read 2.0's tick records under
    // these keys: the repaired page has no such function and never calls them (8. wiring; tests/test_set_decisions.js)
    includeKey: (st) => "doc::d::p0::sig::inc::" + st.style_id, setDecKey: (d, st) => d + "::setinc::" + st.layer };
  const OLD = ["setDecResolve", "setDecEligible"].filter(n => js.includes("\nfunction " + n + "(")).map(fnSrc);
  const C = new Function(...Object.keys(env),
    [constLine("TK_EMPTY_APPROVE"), constLine("TK_STALE_NOTE"), PURE, constLine("tk"), "let tkFollowQueued = false;", ...OLD, ...FNS.map(fnSrc)].join("\n")
    + "\nreturn { " + FNS.concat(["tk",
        "tkEarlierWords: typeof tkEarlierWords === 'function' ? tkEarlierWords : () => ''",
        "tkRedetectWords: typeof tkRedetectWords === 'function' ? tkRedetectWords : () => ''"]).join(", ") + " };")(...Object.values(env));
  const on = () => styles.filter(s => state.include.get(s.style_id)).map(s => s.style_id).join(",");
  return { C, state, posts, said, on, reads, painted };
}
const ST = [
  { style_id: 1, class: "dashed", color: [0, 0, 0], length_pt: 900, layer: "C-STRM" },      // the engine picks it
  { style_id: 2, class: "solid", color: [0, 0, 0], length_pt: 200, layer: "C-TOPO" },       // not (short solid)
  { style_id: 3, class: "solid", color: [0, 0, 0], length_pt: 9000, layer: "C-WATR" },      // picks
  { style_id: 4, class: "annotation", color: [0, 0, 0], length_pt: 900, layer: "C-ANNO" },  // not
  { style_id: 5, class: "dashed", color: [0, 0, 0], length_pt: 900, layer: "" },            // picks (no layer)
  { style_id: 6, class: "dashed", color: [0, 0, 0], length_pt: 900, layer: "G-ANNO-TEXT", sug: true },  // not (suggested)
];
const STALE = "This page's takeoff was changed in another tab or on another computer. Reload to see it.";
// the server: a versioned record; a write that asks to bring ticks over is refused (and counted: none may be sent)
const ticker = (rec) => ({ rec, migrates: 0, get() { return this.rec; },
  post(b) {
    if (b.migrate) { this.migrates++; return [409, { detail: "Nothing is brought over from an earlier version: this page's takeoff starts empty. Reload this page." }]; }
    if (b.version !== this.rec.version) return [409, { detail: STALE }];
    this.rec = { state: "explicit", version: this.rec.version + 1, styles: b.styles };
    return [200, this.rec];
  } });
const flush = () => new Promise(r => setTimeout(r, 400));

(async () => {
  // ---- 2. fresh: empty, nothing sent
  {
    const srv1 = ticker({ state: "fresh", version: 0, styles: [] });
    const h = harness(ST, srv1, new Map());
    await h.C.tkLoad(1, srv1.get());
    check(h.on() === "" && h.state.tkSet.size === 0 && h.posts.length === 0 && h.state.tkNote === "",
      "a fresh page opens with NOTHING in the takeoff and sends nothing");
    check(h.C.famVis(1) && h.C.famVis(3) && !h.C.famVis(2) && !h.C.famVis(4),
      "...and the drawing shows the engine's picks (1, 3) while nothing is in the takeoff - not the others");
    // a tick ADDS: the whole added set, on the version the server gave
    h.C.setInclude(2, true);
    await flush();
    check(h.posts.length === 1 && JSON.stringify(h.posts[0]) === JSON.stringify({ version: 0, styles: [2] }),
      "a tick sends the set the person added (style 2), on version 0: " + JSON.stringify(h.posts));
    check(h.C.famVis(2), "a style the person added is shown");
    // two quick changes, one in flight: sent in order, each on the version the last returned
    h.C.setInclude(1, true);
    await flush();
    h.C.setInclude(3, true);
    h.C.setInclude(1, false);
    await flush();
    check(JSON.stringify(h.posts.slice(1)) === JSON.stringify([{ version: 1, styles: [1, 2] }, { version: 2, styles: [2, 3] }]),
      "later changes go on the version each write returned (never 'all minus unticks'): " + JSON.stringify(h.posts.slice(1)));
    // another tab wrote meanwhile: refused, said, and this tab stops writing that page
    srv1.rec.version = 9;
    h.C.setInclude(4, true);
    await flush();
    check(h.said.some(([m, c]) => /changed in another tab/.test(m) && c === "err"), "a stale write is said in plain words");
    check(h.state.tkNote === STALE + " Until you reload, ticks made in this tab are not kept and Approve is refused." && h.painted.items >= 1,
      "...and stays in view above the takeoff's rows (the status line clips, and the next message replaces it): " + h.state.tkNote);
    check(h.C.tk.ver.get("j1:0") === undefined && (await h.C.tkFlush()) === true,
      "...the tab then holds NO version of the record, and nothing waits to be sent (so Approve must ask for the version: MAJOR-4)");
    const n = h.posts.length;
    h.C.setInclude(5, true);
    await flush();
    check(h.posts.length === n && h.said.some(([m]) => /not being saved: reload/.test(m)),
      "...and the tab sends nothing more for that page until a reload");
  }
  // ---- explicit record
  {
    const srv1 = ticker({ state: "explicit", version: 4, styles: [1, 3], dropped: 2 });
    const h = harness(ST, srv1, new Map());
    await h.C.tkLoad(1, srv1.get());
    check(h.on() === "1,3" && [...h.state.tkSet].join() === "1,3" && h.posts.length === 0,
      "a record is applied exactly (1 and 3 in the takeoff) and nothing is sent");
    check(/detected again: 2 styles you had added are no longer on it/.test(h.state.tkNote),
      "a re-detect that dropped two added styles says so once: " + h.state.tkNote);
  }
  // ---- 3. NOTHING IS BROUGHT OVER: 2.0's tick records in this browser are never read, whatever the server says
  {
    const mem = new Map([
      ["doc::d::p0::sig::inc::3", "0"],             // 2.0: the person dropped the water by hand on this page
      ["doc::d::p0::sig::inc::2", "1"],             // ...and added the short solid
      ["doc::d::setinc::C-ANNO", "1"],              // a set-level add made on another sheet
    ]);
    for (const rec of [{ state: "fresh", version: 0, styles: [] },                       // every page with no record
                       { state: "fresh", version: 0, styles: [], may_migrate: true },    // what the first build's server said
                       { state: "legacy", version: 0, styles: [] }]) {                   // ...of a page 2.0 saved
      const srv1 = ticker(rec);
      const h = harness(ST, srv1, mem);
      await h.C.tkLoad(1, srv1.get());
      await flush();
      const what = rec.state + (rec.may_migrate ? " + may_migrate" : "");
      check(h.on() === "" && h.state.tkSet.size === 0 && h.posts.length === 0 && srv1.migrates === 0,
        `a browser that ticked this page on 2.0, server says '${what}': the takeoff is EMPTY and nothing is sent (on: '${h.on()}', posts: ${h.posts.length})`);
      check(h.reads.length === 0, `...and not one record is read from the browser's storage (read: ${JSON.stringify(h.reads)})`);
      check(h.C.famVis(1) && h.C.famVis(3) && !h.C.famVis(2),
        "...its drawing is the engine's picks, like any new page (the eye does not follow 2.0's ticks)");
      // the page still works: a tick is kept by the server on version 0
      h.C.setInclude(3, true);
      await flush();
      check(h.posts.length === 1 && JSON.stringify(h.posts[0]) === JSON.stringify({ version: 0, styles: [3] }),
        "...and the first tick here is an ordinary write of what was added: " + JSON.stringify(h.posts));
    }
  }
  {
    // a page 2.0 APPROVED: empty here, and told so - one line, by what the takeoff here holds
    const EA = { head: "Approved on an earlier version: its numbers are still in the bid.",
                 detail: "That approval holds 38 rows by length (221 lines, 41,742.6 ft). It stays in the bid sheet and the report as it is until this page is approved again or its approval is withdrawn." };
    const srv1 = ticker({ state: "fresh", version: 0, styles: [], earlier: EA });
    const h = harness(ST, srv1, new Map());
    await h.C.tkLoad(1, srv1.get());
    check(h.on() === "" && h.posts.length === 0 && h.state.tkEarlier && h.state.tkEarlier.head === EA.head,
      "a page 2.0 approved opens EMPTY, keeps what the server said about the old approval, and sends nothing");
    const W = h.C.tkEarlierWords;
    check(W(EA, 0, 0) === "Approved on an earlier version: its numbers are still in the bid. The takeoff here starts empty. Add what you are bidding and approve again to replace them.",
      "its one line, nothing in the takeoff: " + W(EA, 0, 0));
    check(W(EA, 0, 3) === "Approved on an earlier version: its numbers are still in the bid. No line style is in the takeoff here. Add what you are bidding and approve again to replace them.",
      "...with only calculator rows here (the server kept them): " + W(EA, 0, 3));
    check(W(EA, 2, 2) === "Approved on an earlier version: its numbers are still in the bid. Approve again to replace them with the takeoff here.",
      "...once line styles are added: " + W(EA, 2, 2));
    check(!/OUT OF DATE/.test(W(EA, 0, 0) + W(EA, 0, 3) + W(EA, 2, 2)), "it is never called OUT OF DATE");
    const el = fnSrc("tkEarlierEl");
    check(/createElement\("details"\)/.test(el) && /createElement\("summary"\)/.test(el) && /e\.detail/.test(el)
      && /tkEarlierWords\(e, nStyles, nRows\)/.test(el),
      "the Takeoff panel shows it as one line with what was approved folded behind it (details / summary), not deleted");
    // a record with nothing about an old approval clears it
    const srv2 = ticker({ state: "explicit", version: 2, styles: [1] });
    const h2 = harness(ST, srv2, new Map());
    h2.state.tkEarlier = EA;
    await h2.C.tkLoad(1, srv2.get());
    check(h2.state.tkEarlier === null, "a page approved on this build says nothing of the kind");
  }
  {
    // the server did not answer: shown empty, said, and no tick is sent
    const h = harness(ST, ticker({ state: "fresh", version: 0, styles: [] }), new Map());
    await h.C.tkLoad(1, null);
    h.C.setInclude(1, true);
    await flush();
    check(/did not say what is in this page's takeoff/.test(h.state.tkNote) && h.posts.length === 0
      && h.said.some(([m]) => /not being saved/.test(m)), "no answer from the server: said on the panel, and ticks are not sent");
  }
  // ---- 6. a re-detect, in one sentence
  {
    const h = harness(ST, ticker({ state: "fresh", version: 0, styles: [] }), new Map());
    const R = h.C.tkRedetectWords;
    check(R(0, false) === "" && R(2, false) === "This sheet was detected again: 2 styles you had added are no longer on it, so they are out of the takeoff. The others you added are still in.",
      "styles dropped by a re-detect: " + R(2, false));
    check(R(0, true) === "This sheet was detected again after an engine update. The styles you added are still in the takeoff, but their lines and lengths may have changed: check the rows before you approve.",
      "another engine's detection, none dropped (MINOR-7: it said nothing): " + R(0, true));
    check(R(1, true) === "This sheet was detected again after an engine update: 1 style you had added is no longer on it, so it is out of the takeoff. The others you added are still in, but their lines and lengths may have changed: check the rows before you approve.",
      "...and with one dropped: " + R(1, true));
    const srv1 = ticker({ state: "explicit", version: 3, styles: [1, 3], dropped: 0, redetected: true });
    const g = harness(ST, srv1, new Map());
    await g.C.tkLoad(1, srv1.get());
    check(/detected again after an engine update/.test(g.state.tkNote), "the page says it above the rows: " + g.state.tkNote);
    const srv2 = ticker({ state: "explicit", version: 3, styles: [], dropped: 0, redetected: true });
    const e = harness(ST, srv2, new Map());
    await e.C.tkLoad(1, srv2.get());
    check(e.state.tkNote === "", "...but not over an empty takeoff (there is nothing to check)");
  }
  // ---- 7. the panels that quote the takeoff follow a tick (MINOR-4, MINOR-5): one repaint per task
  {
    const srv1 = ticker({ state: "fresh", version: 0, styles: [] });
    const h = harness(ST, srv1, new Map());
    await h.C.tkLoad(1, srv1.get());
    h.C.setInclude(1, true);
    await Promise.resolve();
    check(h.painted.scope === 1 && h.painted.check === 1,
      `after one tick the scope sentence and the check queue are drawn again (scope ${h.painted.scope}, check ${h.painted.check})`);
    for (const s of ST) h.C.setInclude(s.style_id, true);          // a bulk button: six ticks in one task
    await Promise.resolve();
    check(h.painted.scope === 2 && h.painted.check === 2,
      `a bulk button repaints them once, not once per style (scope ${h.painted.scope}, check ${h.painted.check})`);
    check(/tkFollow\(\)/.test(fnSrc("incRestore")), "undo, which writes the ticks without setInclude, tells them too");
  }
  // ---- the Bid readiness row
  {
    const srv1 = ticker({ state: "fresh", version: 0, styles: [] });
    const h = harness(ST, srv1, new Map());
    const r0 = h.C.tkReadiness();
    srv1.rows = { groups: [{ items: [{}, {}] }, { items: [{}] }] };
    const r1 = h.C.tkReadiness();
    check(r0.label === "Takeoff" && r0.value === "empty" && r0.state === "missing" && r1.value === "3 items" && r1.state === "supplied",
      "Bid readiness: 'Takeoff: empty' (missing) / 'Takeoff: 3 items'");
  }

  // ---- 8. wiring
  const tb = js.slice(js.indexOf("// TICKS-BEGIN"), js.indexOf("// TICKS-END")).replace(/\/\/[^\n]*/g, "");
  check(!/migrate/.test(tb) && !/store\./.test(tb) && !/localStorage/.test(tb) && !/may_migrate|legacy/.test(tb),
    "the TICKS block asks the server to bring nothing over and never touches the browser's storage");
  const si = fnSrc("setInclude");
  check(/tkDirty\(\)/.test(si) && !/store\.setItem/.test(si), "setInclude sends to the server and writes nothing to the browser");
  const ma = fnSrc("memApply");
  check(!/state\.include\.set/.test(ma.replace(/\/\/[^\n]*/g, "")), "memApply no longer puts saved ticks back (the server's record does)");
  const ap = fnSrc("approveRun");
  const iEmpty = ap.indexOf("TK_EMPTY_APPROVE"), iFlush = ap.indexOf("await tkFlush()"), iPost = ap.indexOf("/approve`");
  check(iEmpty > 0 && iFlush > iEmpty && iPost > iFlush, "approve: refuses an empty takeoff first, then sends the ticks, then approves");
  const iVer = ap.indexOf("tk.ver.get(tkKey(job0, page0))"), iStale = ap.indexOf("TK_STALE_APPROVE");
  check(iVer > iFlush && iStale > iVer && iStale < iPost && /ticks_version: tkVer/.test(ap),
    "approve: after the ticks are sent it needs the version of the server's record this page holds, refuses without "
    + "one, and names it in the request (MAJOR-4; behaviour: tests/test_approve_fail_words.js)");
  check(/TK_EMPTY_APPROVED/.test(ap.slice(iEmpty, iFlush)) && /withdrawBtn/.test(ap.slice(iEmpty, iFlush)),
    "approve: on an approved page the empty refusal names Withdraw approval");
  const ri = fnSrc("renderItemized");
  check(/TK_EMPTY_HEAD/.test(ri) && /!groups\.some\(g => g\.items\.length\)/.test(ri) && /state\.tkNote/.test(ri),
    "the Takeoff panel says what to do while it is empty, and the page's note");
  check(/tkEarlierEl\(groups\)/.test(ri), "...and the line of a page approved on an earlier version");
  check(/state\.tkEarlier = null/.test(fnSrc("pageReset")) && /state\.tkEarlier = null/.test(fnSrc("wdClick"))
    && /state\.tkEarlier = null/.test(ap),
    "that line goes with the page, with a withdrawal, and when the page is approved again");
  check(/state\.areaInc = new Map\(\)/.test(fnSrc("pageReset")) && !/areaInc\.set\(/.test(fnSrc("loadResult")),
    "Areas start empty too: nothing ticks an area at load (only the person's own stored ticks come back)");
  check(/Tick to add this area to the takeoff/.test(fnSrc("renderAreas")), "the Areas tick says the same rule");

  if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
  console.log("all empty-takeoff page checks passed");
})().catch(e => { console.error("FAIL: " + (e && e.stack || e)); process.exit(1); });
