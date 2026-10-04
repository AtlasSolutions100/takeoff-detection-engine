// WHAT THE MERGE CRITIC FOUND, THE PAGE HALF (CRITIC-merge-1, 2026-10-04; server half: tests/test_merge_critic1.py).
// The real functions of webui/app.js run here over a stub page and a stub server.
//
//   A. MINOR-1: a takeoff change the server did not keep waits FOR ITS OWN PAGE. It used to wait in one place, so a tick
//      on another page took that place, and coming back to the page dropped it without a word.
//        a1  the review's steps: the third tick's write fails, the person ticks on the next page - the waiting set is
//            sent for ITS page once the server keeps a write again, and is on the page when the person comes back
//        a2  the server still refuses it: back on its page the set is on screen again, sent again, and the approve
//            bar says it is not saved
//        a3  no tick anywhere else: leaving and coming back sends it again (it was dropped silently)
//        a4  the record moved meanwhile (another tab): the server's takeoff shows and the page SAYS a change was lost
//        a5  an Approve on another page does not go while a set of an earlier page still can not be saved
//        a6  an older set of a page never lands over a newer one of the same page
//   B. the Projects page asks for the e-mail list only when the server says bids by e-mail is on (`inbound` in
//      GET /api/projects). Off: no request at all, so no 404 in the console on every load.
//
// run: node tests/test_merge_critic1_ui.js        (fails on be46b84)
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
const wait = (ms) => new Promise(r => setTimeout(r, ms || 400));

// ---- A. a set the server did not keep waits for its own page -------------------------------------------------------
// One job, two sheets. `answer(page, n)`: what the stub server does with the n-th write of that page: "ok", 500, "down".
function page(answer) {
  const FNS = ["tkKey", "tkApply", "tkDirty", "tkWrite", "tkFlush", "tkUnsavedWords", "tkUnsavedApproveWords", "tkUnsavedShow",
               "tkBarFit", "tkRedetectWords"];
  const server = { 0: { version: 1, styles: [] }, 1: { version: 1, styles: [] } };
  const posts = [], said = [], count = { 0: 0, 1: 0 };
  const note = { hidden: true, textContent: "" };                 // the approve bar's note (#tkUnsaved)
  const STYLES = { 0: [28, 80, 91], 1: [5, 6] };
  const state = { job: "j1", page: 0, result: {}, tkSet: new Set(), include: new Map(), tkNote: "", tkEarlier: null };
  const fetch = async (u, opt) => {
    const pg = +/\/page\/(\d+)\/ticks/.exec(u)[1];
    const b = JSON.parse(opt.body);
    const a = answer(pg, count[pg]++, b);
    posts.push({ page: pg, styles: b.styles, answer: a });
    if (a === "down") throw new TypeError("Failed to fetch");
    if (a === 500) return { ok: false, status: 500, json: async () => ({ detail: "Internal Server Error" }) };
    if (b.version !== server[pg].version) return { ok: false, status: 409, json: async () => ({ detail: "changed in another tab" }) };
    server[pg] = { version: b.version + 1, styles: b.styles };
    return { ok: true, status: 200, json: async () => ({ version: server[pg].version, styles: b.styles }) };
  };
  const env = { state, fetch, setStatus: (m, k) => said.push([m, k]), renderItemized() {}, setTimeout, clearTimeout,
                window: { innerWidth: 1440 }, $: (id) => (id === "tkUnsaved" ? note : null) };
  const C = new Function(...Object.keys(env),
    [constLine("TK_STALE_NOTE"), constLine("TK_LOST_NOTE"), constLine("tk"), ...FNS.map(fnSrc)].join("\n")
    + "\nreturn { tk, " + FNS.join(", ") + " };")(...Object.values(env));
  // open a sheet the way the page does: every style unticked, then the server's record applied (tkLoad -> tkApply)
  const open = (pg) => {
    state.page = pg;
    state.include = new Map(STYLES[pg].map(s => [s, false]));
    state.tkSet = new Set();
    state.tkNote = "";
    C.tkApply({ version: server[pg].version, styles: server[pg].styles.slice() });
  };
  const tick = (sid) => { state.include.set(sid, true); state.tkSet.add(sid); C.tkDirty(); };
  const waits = () => (C.tk.pending ? 1 : 0) + (C.tk.waiting ? C.tk.waiting.size : 0);
  return { C, state, server, posts, said, note, open, tick, waits, on: () => [...state.tkSet].sort((a, b) => a - b) };
}

(async () => {
  {
    // a1: the review's steps. Sheet 0: two ticks kept, the third write fails. Sheet 1: one tick, kept.
    const p = page((pg, n) => (pg === 0 && n === 2 ? 500 : "ok"));
    p.open(0);
    p.tick(80); await wait();
    p.tick(28); await wait();
    p.tick(91); await wait();
    check(JSON.stringify(p.server[0].styles) === "[28,80]" && p.waits() === 1 && p.note.hidden === false,
      "a1: (the third tick's write is refused: the server holds two styles, the set of three waits, the approve bar says so)");
    p.open(1);
    p.tick(5); await wait(); await wait(200);
    check(JSON.stringify(p.server[1].styles) === "[5]", "a1: the tick on the next sheet is kept");
    check(JSON.stringify(p.server[0].styles) === "[28,80,91]" && p.waits() === 0,
      "a1: ...and the set that waited is sent for ITS sheet once the server keeps a write again (it was thrown away: "
      + "the tick on the next sheet took its place): " + JSON.stringify(p.server[0].styles));
    p.open(0);
    check(JSON.stringify(p.on()) === "[28,80,91]" && p.state.tkNote === "" && p.note.hidden === true,
      "a1: back on the first sheet all three styles are in the takeoff: " + JSON.stringify(p.on()));
  }
  {
    // a2: the server goes on refusing sheet 0's set while sheet 1's writes go through
    const p = page((pg, n) => (pg === 0 && n >= 1 ? 500 : "ok"));
    p.open(0);
    p.tick(80); await wait();
    p.tick(28); await wait();
    p.open(1);
    p.tick(5); await wait(); await wait(200);
    check(JSON.stringify(p.server[0].styles) === "[80]" && p.waits() === 1,
      "a2: a set the server still refuses is KEPT while the person works on another sheet: " + p.waits());
    const before = p.posts.length;
    p.open(0);
    await wait(100);
    check(JSON.stringify(p.on()) === "[28,80]",
      "a2: back on its sheet the set is on screen again - the takeoff is what the person ticked, not one style short: " + JSON.stringify(p.on()));
    check(p.posts.length === before + 1 && p.posts[before].page === 0 && JSON.stringify(p.posts[before].styles) === "[28,80]",
      "a2: ...and it is sent again for that sheet");
    check(p.note.hidden === false && /not saved/.test(p.note.textContent) && /error 500/.test(p.note.textContent),
      "a2: ...and while it still is not kept the approve bar says so, with the reason: " + JSON.stringify(p.note.textContent));
  }
  {
    // a3: no tick anywhere else. The server is down for the write, the person looks at another sheet and comes back
    const p = page((pg, n) => (pg === 0 && n === 1 ? "down" : "ok"));
    p.open(0);
    p.tick(80); await wait();
    p.tick(28); await wait();
    p.open(1);
    p.open(0);
    await wait(100);
    check(JSON.stringify(p.on()) === "[28,80]" && JSON.stringify(p.server[0].styles) === "[28,80]" && p.waits() === 0
      && p.note.hidden === true,
      "a3: leaving the sheet and coming back sends the waiting set again and it is kept (it was dropped without a word: "
      + "screen and server one style short): " + JSON.stringify([p.on(), p.server[0].styles]));
  }
  {
    // a4: the record moved meanwhile - another tab ticked something else on sheet 0
    const p = page((pg, n) => (pg === 0 && n === 1 ? "down" : "ok"));
    p.open(0);
    p.tick(80); await wait();
    p.tick(28); await wait();
    p.open(1);
    p.server[0] = { version: p.server[0].version + 1, styles: [91] };
    const before = p.posts.length;
    p.open(0);
    await wait(100);
    check(JSON.stringify(p.on()) === "[91]" && p.posts.length === before,
      "a4: the takeoff was changed somewhere else meanwhile: the server's takeoff shows and the old set is not sent over it");
    check(/was not saved/.test(p.state.tkNote) && /changed somewhere else/.test(p.state.tkNote) && p.waits() === 0,
      "a4: ...and the page SAYS a change made here was lost (it said nothing): " + JSON.stringify(p.state.tkNote));
  }
  {
    // a5: Approve on another sheet while an earlier sheet's set can not be saved
    const p = page((pg, n) => (pg === 0 && n >= 1 ? 500 : "ok"));
    p.open(0);
    p.tick(80); await wait();
    p.tick(28); await wait();
    p.open(1);
    p.tick(5); await wait(); await wait(200);
    let ok = null;
    try { ok = await p.C.tkFlush(); } catch (e) { ok = "threw " + e.message; }
    check(ok === false && p.waits() === 1,
      "a5: the flush before an Approve answers NO while a set of an earlier sheet still can not be saved, and keeps it "
      + "(it answered yes: the set was gone): " + JSON.stringify([ok, p.waits()]));
  }
  {
    // a6: everything fails for a while; sheet 0 gets a newer set; then the server answers again
    let up = false;
    const p = page(() => (up ? "ok" : "down"));
    p.open(0);
    p.tick(80); await wait();                 // set A = [80]: not kept
    p.open(1);
    p.tick(5); await wait();                  // sheet 1's set: not kept; A waits for sheet 0
    p.open(0); await wait(100);               // A is on screen again, sent again, not kept
    p.tick(28); await wait();                 // set B = [28, 80]: not kept
    up = true;
    let ok = null;
    try { ok = await p.C.tkFlush(); } catch (e) { ok = "threw " + e.message; }
    check(ok === true && JSON.stringify(p.server[0].styles) === "[28,80]" && JSON.stringify(p.server[1].styles) === "[5]"
      && p.waits() === 0,
      "a6: when the server answers again every sheet gets ITS newest set - an older set of a sheet never lands over a newer "
      + "one: " + JSON.stringify([ok, p.server[0].styles, p.server[1].styles]));
  }

  // ---- B. bids by e-mail off: the page does not ask for the list ----------------------------------------------------
  {
    const mk = () => {
      const asked = [];
      let painted = 0;
      const env = { fetch: async (u) => { asked.push(u); return { ok: true, json: async () => ({ enabled: true, entries: [], busy: false }) }; },
                    inbRender() { painted++; }, pvIsOpen: () => true, $: () => ({ hidden: true }), renderSvInbound() {},
                    inbRefreshCards() {}, setTimeout: () => 0, clearTimeout() {}, document: { visibilityState: "visible" } };
      const C = new Function(...Object.keys(env), "let inbData, inbTimer = null, inbSig = '', inbOn;\n"
        + ["inbAsk", "inbLoad", "inbSchedule"].map(fnSrc).join("\n")
        + "\nreturn { inbAsk, state: () => ({ inbData, inbOn }) };")(...Object.values(env));
      return { C, asked, painted: () => painted };
    };
    {
      const p = mk();
      let threw = "";
      try { await p.C.inbAsk(false); await p.C.inbAsk(undefined); } catch (e) { threw = e.message; }
      check(!threw && p.asked.length === 0 && p.C.state().inbData === null && p.painted() >= 1,
        "b: the server said bids by e-mail is OFF: /api/inbound is not asked for at all - now or on the next look - and the "
        + "section is drawn hidden (it was asked on every Projects load: a 404 in the console each time) " + JSON.stringify([threw, p.asked]));
    }
    {
      const p = mk();
      let threw = "";
      try { await p.C.inbAsk(undefined); } catch (e) { threw = e.message; }
      const before = p.asked.length;
      try { await p.C.inbAsk(true); await p.C.inbAsk(undefined); } catch (e) { threw = threw || e.message; }
      check(!threw && before === 0 && JSON.stringify(p.asked) === '["/api/inbound","/api/inbound"]',
        "b: nothing is asked before the server has said; once it says ON the list is asked for, and again on each look: "
        + JSON.stringify([threw, before, p.asked]));
    }
    const lp = fnSrc("loadProjects").replace(/\/\/[^\n]*/g, ""), sv = fnSrc("renderSvInbound").replace(/\/\/[^\n]*/g, "");
    check(/inbAsk\(d \? d\.inbound : undefined\)/.test(lp) && !/inbLoad\(\)/.test(lp)
      && lp.indexOf('fetch("/api/projects")') < lp.indexOf("inbAsk("),
      "b: the Projects page decides from GET /api/projects' own `inbound` (it no longer calls the list's route by itself)");
    check(/inbData === undefined && !svInbAsked && inbOn\b/.test(sv),
      "b: Settings asks for it only on a server that has said it is on");
    check(/cfg\.inbound/.test(js.slice(js.indexOf('fetch("/api/config")'), js.indexOf('fetch("/api/config")') + 500)),
      "b: ...which the page knows from GET /api/config when it starts");
    const html = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
    check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 312, "the cache buster moved (app.js >= 312)");
  }

  if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
  console.log("all merge-critic page checks passed");
})().catch(e => { console.error("FAIL: " + (e && e.stack || e)); process.exit(1); });
