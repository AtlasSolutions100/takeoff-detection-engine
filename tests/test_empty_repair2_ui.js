// THE TAKEOFF STARTS EMPTY - the second repair, after CRITIC-empty-2 (2026-10-04), the page half (server half:
// tests/test_empty_repair2.py). The real functions of webui/app.js run here over stub pages.
//
//   A. MAJOR-1, the question. Approve on a page whose approval was made on an earlier version asks ONE question first,
//      naming what is in the bid (the server's facts) and what the takeoff here holds; No sends nothing and changes
//      nothing; Yes sends the approve with `replace_earlier`; a page approved here is asked nothing. When the server
//      asks (the page had not heard) its sentence is shown and the next press asks.
//   B. the line "approved on an earlier version" is shown WHERE APPROVE IS PRESSED (the approve bar), not among the
//      takeoff's rows, and goes with the page.
//   C. put back: the button shows only while the server keeps an earlier approval for the page; it asks, posts the
//      act, and the page says again that it was approved on an earlier version.
//   D. R1 covers areas and counts: nothing the earlier version kept in THIS BROWSER is restored - not the area it had
//      ticked (the row that came back by itself), not a count or a trench row; its records are never read for a row,
//      never written; this build keeps its own under its own keys.
//   E. MINOR-1 / MINOR-2: a declared trade never takes a style the person added out of the takeoff, so the screen is
//      the person's set (the server's record) and an approve sends all of it; undo after the declaration was cleared
//      puts back the set and the screen together.
//   F. MINOR-3: a tick's write that fails for a reason that is not "stale" is KEPT and sent again by the next Approve;
//      if it fails again the approve says the real reason and offers to try again - never "changed in another tab".
//   G. MINOR-5 / MINOR-6: the two hover texts that had become false; the dead "carried over" block is gone.
//
// run: node tests/test_empty_repair2_ui.js        (fails on a1e4dcd, the branch before this repair)
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "..", "webui", "style.css"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
// a check whose body may throw on the tree before the repair (a function that is not there yet): a failure, not a crash
const tryCheck = (fn, msg) => { let ok = false, why = ""; try { ok = !!fn(); } catch (e) { why = "  [threw: " + (e && e.message) + "]"; } check(ok, msg + why); };
const has = (name) => js.includes("\nfunction " + name + "(") || js.includes("\nasync function " + name + "(");
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
const pageConst = (name) => { const i = js.indexOf("const " + name + " = "); return i < 0 ? "" : JSON.parse(js.slice(js.indexOf('"', i), js.indexOf("\n", i) - 1)); };
const PURE = js.slice(js.indexOf("// TICKS-PURE-BEGIN"), js.indexOf("// TICKS-PURE-END"));
const P = new Function(PURE + "\nreturn { tkHereWords: typeof tkHereWords === 'function' ? tkHereWords : null,"
  + " tkEarlierAskWords: typeof tkEarlierAskWords === 'function' ? tkEarlierAskWords : null,"
  + " tkPutBackAskWords: typeof tkPutBackAskWords === 'function' ? tkPutBackAskWords : null };")();
const EA = { head: "Approved on an earlier version: its numbers are still in the bid.",
             holds: "41 rows by length (328 lines, 44,793.4 ft) and 1 row counted or calculated",
             detail: "That approval holds 41 rows by length (328 lines, 44,793.4 ft) and 1 row counted or calculated. It stays in the bid sheet and the report as it is until this page is approved again or its approval is withdrawn." };
const AREA_ROW = { label: "P1-BLDG-HATCH", cnt: "SF", unit: "SF", qty: 23249.2, isCalc: true };

// ---- A. the question ---------------------------------------------------------------------------------------------------
const aA = js.indexOf("// APPROVE-FAIL-BEGIN"), aB = js.indexOf("// APPROVE-FAIL-END");
const rS = js.indexOf("async function approveRun() {");
let rE = -1;
for (let k = js.indexOf("{", rS), d = 0; k < js.length; k++) {
  if (js[k] === "{") d++;
  else if (js[k] === "}" && --d === 0) { rE = k + 1; break; }
}
const GLOBALS = new Set(["JSON", "Object", "Number", "Array", "String", "Set", "Map", "Promise", "Math", "parseFloat", "parseInt",
  "Boolean", "Error", "TypeError", "undefined", "Infinity", "NaN", "isFinite", "console", "Symbol"]);
// the real approveRun over a stub page. `o.earlier`: what the server said of the page's approval; `o.rows`: the Takeoff
// panel's rows; `o.answer`: what the person answers to a question; `o.flush`: what tkFlush answers; `o.pending`: a set
// the server did not keep
function approver(fetchImpl, o = {}) {
  const out = { status: [], els: {}, asked: [] };
  const env = {
    state: { job: "j1", page: 0, gen: 1, include: new Map(o.include || []), labels: new Map([[1, "STORM"]]), chainLabels: new Map(),
             extraItems: o.extra || [], excluded: new Set(), lineColor: new Map(), once: new Set(), workZones: [],
             tkEarlier: o.earlier || null, result: { chains: [], styles: [{ style_id: 1 }] } },
    subExportRows: () => [],
    nWord: new Function(js.slice(js.indexOf("function nWord("), js.indexOf("\n}\n", js.indexOf("function nWord(")) + 3) + "return nWord;")(),
    computeTakeoffRows: () => ({ groups: [{ items: o.rows || [] }] }),
    tradeLayerMap: () => new Map(), approveScaleParts: () => ({ scale: { ft_per_paper_inch: 20 }, scale_seen: null }),
    tradeFor: () => "General", styleColorInfo: () => ({ src: "engine" }), colorNorm: () => null,
    famMineExportStyles: () => [], onceFamily: () => false, wzActive: () => false, flIsRowLabel: () => false,
    store: { getItem: () => null }, priceKey: l => "price:" + l, approveNotes: () => [],
    setStatus: (m, k) => { out.status.push([m, k]); },
    fetch: fetchImpl,
    confirm: (m) => { out.asked.push(m); return !!o.answer; },
    approveScaleRecorded() {}, approvalOutdated: () => Promise.resolve(), approveNotesDropped: () => 0, APPROVE_NOTES_MAX: 6,
    cfSentence: x => String(x), cfEngineState: async () => null, cfApplyItems() {}, cfState: {}, cfRows: () => [],
    cfAdded: () => false, cfDropItems() {}, cfBackThenApprove: () => "", cfLeftNote() {},
    tkFlush: async () => (o.flush === undefined ? true : o.flush),
    TK_EMPTY_APPROVE: "Nothing is in the takeoff. Tick the styles you want first.",
    TK_EMPTY_APPROVED: "To take this page out of the bid, use Withdraw approval.",
    tk: { ver: new Map([["j1:0", 0]]), pending: o.pending || null, why: o.why || "" },
    tkKey: (j, p) => j + ":" + p, TK_STALE_APPROVE: pageConst("TK_STALE_APPROVE"),
    TK_STALE_SERVER: pageConst("TK_STALE_SERVER"), TK_STALE_NOTE: pageConst("TK_STALE_NOTE"),
    TK_EARLIER_NO: pageConst("TK_EARLIER_NO"), TK_EARLIER_PRESS: pageConst("TK_EARLIER_PRESS"),
    tkHereWords: P.tkHereWords, tkEarlierAskWords: P.tkEarlierAskWords,
    renderItemized() { out.painted = (out.painted || 0) + 1; },
    $: id => (out.els[id] = out.els[id] || { hidden: true, href: "" }), url: u => u, bsReportUrl: () => "r", bsCsvUrl: () => "c",
  };
  // the page's own words for a change the server did not keep (F), and the approve bar's note: the real functions, over
  // this page's `tk`
  if (has("tkUnsavedApproveWords")) {
    const U = new Function("tk", "state", "$", ["tkUnsavedWords", "tkUnsavedApproveWords", "tkUnsavedShow", "tkBarFit"].map(fnSrc).join("\n")
      + "\nreturn { tkUnsavedApproveWords, tkUnsavedShow };")(env.tk, env.state, env.$);
    env.tkUnsavedApproveWords = U.tkUnsavedApproveWords; env.tkUnsavedShow = U.tkUnsavedShow;
  }
  const proxy = new Proxy(env, { has: (t, k) => !GLOBALS.has(k) && typeof k === "string", get: (t, k) => (k in t ? t[k] : undefined) });
  const F = new Function("env", "with (env) {\n" + js.slice(aA, aB) + "\n" + js.slice(rS, rE) + "\nreturn { approveRun, approveClick }; }")(proxy);
  const press = () => F.approveClick(F.approveRun, env.setStatus);
  const last = () => out.status[out.status.length - 1] || ["", ""];
  return { out, env, press, last };
}
const resp = (status, obj) => ({ ok: status >= 200 && status < 300, status, json: async () => obj });

(async () => {
  {
    // the critic's page: 2.0 approved 41 rows by length; here 0 line styles and ONE row that came back by itself
    let n = 0;
    const h = approver(async () => { n++; return resp(200, { exported: 0, items: [{}] }); },
                       { earlier: EA, rows: [AREA_ROW], extra: [{ label: "P1-BLDG-HATCH", qty: 23249.2, unit: "SF", _area: true }], answer: false });
    await h.press();
    const q = h.out.asked[0] || "";
    check(h.out.asked.length === 1 && n === 0,
      `A: Approve on a page 2.0 approved ASKS first, and with the answer No sends NOTHING (asked ${h.out.asked.length}, requests ${n}: `
      + "one press exported the area row alone and dropped 41 rows by length)");
    check(/41 rows by length \(328 lines, 44,793\.4 ft\) and 1 row counted or calculated/.test(q) && /approved on an earlier version/.test(q),
      "A: the question names what is in the bid and would be replaced (rows, lines, feet): " + JSON.stringify(q.slice(0, 200)));
    check(/The takeoff here, which would replace it: 1 row counted or calculated\. No row by length is in it\./.test(q),
      "A: ...and what would replace it: the one row, and that no row by length is in it");
    check(/kept and\s+can be put back/.test(q) && /Cancel changes nothing/.test(q), "A: ...that the earlier approval is kept, and that No changes nothing");
    check(h.last()[0] === "Nothing changed: the approval made on an earlier version is still in the bid." && h.env.state.tkEarlier === EA,
      "A: after No the status says nothing changed, and the page still says it was approved on an earlier version: " + JSON.stringify(h.last()[0]));
  }
  {
    let sent = null, n = 0;
    const h = approver(async (u, opt) => { n++; sent = JSON.parse(opt.body);
      return resp(200, { exported: 34, items: [{}, {}], replaced: { at: "2026-10-04T10:12:00", holds: EA.holds } }); },
      { earlier: EA, include: [[1, true]], rows: [{ label: "STORM", cnt: "34", unit: "ft", qty: 8588.4 }], answer: true });
    await h.press();
    check(h.out.asked.length === 1 && n === 1 && !!sent && sent.replace_earlier === true,
      `A: with the answer Yes the approve is sent ONCE and says the person was asked (replace_earlier: ${sent && sent.replace_earlier})`);
    check(/which would replace it: 1 row by length \(34 lines, 8,588\.4 ft\)\./.test(h.out.asked[0] || ""),
      "A: the question counted the takeoff's rows by length, their lines and feet");
    check(/exported 34 elements/.test(h.last()[0]) && /replaced the approval made on an earlier version \(41 rows by length/.test(h.last()[0])
      && /kept/.test(h.last()[0]) && /Put back earlier approval/.test(h.last()[0]) && h.env.state.tkEarlier === null,
      "A: the status says what was replaced, that it is kept and how to put it back; the page's line goes: " + JSON.stringify(h.last()[0]));
  }
  {
    let sent = null;
    const h = approver(async (u, opt) => { sent = JSON.parse(opt.body); return resp(200, { exported: 2, items: [{}] }); },
      { include: [[1, true]], rows: [{ label: "STORM", cnt: "2", unit: "ft", qty: 10 }], answer: false });
    await h.press();
    check(h.out.asked.length === 0 && !!sent && !("replace_earlier" in sent) && /exported 2 elements/.test(h.last()[0]),
      "A: a page with no approval of an earlier version is asked NOTHING and approves as before");
  }
  {
    // the server asks (the earlier approval was put back from another tab: this page had not heard)
    const SAY = "This page's approval in the bid was made on an earlier version, and approving replaces it. Nothing was approved. Press Approve again: it asks first, and No changes nothing.";
    let n = 0;
    const h = approver(async () => { n++; return resp(409, { detail: { earlier: EA, say: SAY } }); },
      { include: [[1, true]], rows: [{ label: "STORM", cnt: "2", unit: "ft", qty: 10 }], answer: false });
    await h.press();
    check(n === 1 && h.last()[0] === SAY && h.last()[1] === "err" && h.env.state.tkEarlier === EA && h.out.painted >= 1,
      "A: when the SERVER asks, its sentence is shown, and the page takes what it said (the line shows): " + JSON.stringify(h.last()[0]));
    await h.press();
    check(h.out.asked.length === 1 && n === 1, "A: ...and the next press asks the question before it sends anything");
  }
  // ---- the words -----------------------------------------------------------------------------------------------------
  tryCheck(() => P.tkHereWords([]) === "no rows"
    && P.tkHereWords([{ items: [{ label: "a", cnt: "3", unit: "ft", qty: 100.25 }, { label: "b", cnt: "1", unit: "ft", qty: 2 }] },
                      { items: [{ label: "c", unit: "EA", qty: 4, isObj: true }, AREA_ROW] }])
       === "2 rows by length (4 lines, 102.3 ft) and 2 rows counted or calculated",
    "A: what the takeoff here holds, in the server's own words for an approval (rows by length with lines and feet; rows counted or calculated)");
  tryCheck(() => /Put the earlier approval of page 3 back in the bid\?/.test(P.tkPutBackAskWords({ holds: EA.holds }, 2, true))
    && /41 rows by length/.test(P.tkPutBackAskWords({ holds: EA.holds }, 2, true))
    && /takes the place of the approval that is in the bid now; that one is kept/.test(P.tkPutBackAskWords({ holds: EA.holds }, 2, true))
    && !/takes the place/.test(P.tkPutBackAskWords({ holds: EA.holds }, 2, false))
    && /Cancel changes nothing/.test(P.tkPutBackAskWords({ holds: EA.holds }, 2, false)),
    "C: the put-back question names what comes back, says what happens to the approval in the bid now, and that No changes nothing");

  // ---- B. the line where Approve is pressed ---------------------------------------------------------------------------
  {
    const bar = html.slice(html.indexOf('<div class="approve-bar">'), html.indexOf('<div class="links-bar">'));
    check(/id="tkEarlierSlot"/.test(bar) && bar.indexOf('id="tkEarlierSlot"') < bar.indexOf('id="approveBtn"'),
      "B: the approve bar holds the slot for the line, above the Approve button (index.html)");
    const ri = fnSrc("renderItemized").replace(/\/\/[^\n]*/g, "");
    check(/tkEarlierShow\(tkEarlierEl\(groups\)\)/.test(ri) && !/box\.appendChild\(_ea\)/.test(ri),
      "B: the Takeoff panel no longer holds the line among its rows (it sat 1,949 px down the column): it goes to the approve bar");
    check(/if \(!state\.result\) \{[\s\S]{0,400}tkEarlierShow\(null\)/.test(ri), "B: ...and a sheet with no detection clears it");
    const slot = { hidden: true, textContent: "x", kids: [], appendChild(c) { this.kids.push(c); } };
    const win = { innerWidth: 1440 };
    const mkShow = () => new Function("$", "window", fnSrc("tkBarFit") + "\n" + fnSrc("tkEarlierShow") + "\nreturn tkEarlierShow;")(
      (id) => (id === "tkEarlierSlot" ? slot : null), win);
    tryCheck(() => {
      const show = mkShow();
      const el = { id: "tkEarlier" };
      show(el);
      const shown = slot.hidden === false && slot.kids.length === 1 && slot.kids[0] === el;
      slot.kids = [];
      show(null);
      return shown && slot.hidden === true && slot.kids.length === 0;
    }, "B: tkEarlierShow puts the line in the slot and shows it; with no line the slot is hidden and empty");
    tryCheck(() => {
      // the column is 400 px wide and starts 128 px in: on a 390 px window a full-width line runs off the right edge
      const show = mkShow();
      slot.style = { maxWidth: "" };
      slot.getBoundingClientRect = () => ({ left: 140, right: 516, width: 376 });
      win.innerWidth = 1440; show({ id: "tkEarlier" });
      const wide = slot.style.maxWidth;
      win.innerWidth = 390; show({ id: "tkEarlier" });
      return wide === "" && slot.style.maxWidth === "242px";
    }, "B: on a 390 px window the line is held to the part of the approve bar inside the window (242 px), so none of it runs off "
       + "the edge; on a wide window nothing is set");
    check(/\.approve-bar \.tk-earlier-slot \{ flex: 1 0 100%/.test(css), "B: the slot takes a full row of the approve bar (style.css)");
    check(/id="tkUnsaved"[^>]* hidden/.test(bar) && bar.indexOf('id="tkUnsaved"') < bar.indexOf('id="approveBtn"')
      && /\.approve-bar \.tk-unsaved \{ flex: 1 0 100%/.test(css) && /\$\("tkUnsaved"\)\.hidden = true/.test(fnSrc("pageReset")),
      "F: the approve bar also holds the note for a takeoff change the server did not keep (hidden until there is one, and on every page switch)");
    check(/createElement\("details"\)/.test(fnSrc("tkEarlierEl")) && /createElement\("summary"\)/.test(fnSrc("tkEarlierEl")),
      "B: it is still one plain line with what was approved folded behind it (details / summary)");
  }

  // ---- C. put back -----------------------------------------------------------------------------------------------------
  {
    const run = async (answer, reply, kept) => {
      const els = {}, said = [], asked = [], posts = [];
      const $ = (id) => (els[id] = els[id] || { hidden: id !== "withdrawBtn", href: "" });
      const st = { job: "A", page: 2, gen: 7, tkKept: kept, tkEarlier: null };
      const env = { state: st, $, confirm: (m) => { asked.push(m); return answer; },
        fetch: async (u, opt) => { posts.push([u, opt && opt.method]); return { ok: reply[0] === 200, status: reply[0], json: async () => reply[1] }; },
        setStatus: (m, k) => said.push([m, k]), renderItemized: () => { st.painted = 1; }, url: (u) => u, bsReportUrl: () => "r", bsCsvUrl: () => "c",
        approvalOutdated: async () => {}, tkPutBackAskWords: P.tkPutBackAskWords };
      const fn = new Function(...Object.keys(env), fnSrc("pbClick") + "\nreturn pbClick;")(...Object.values(env));
      await fn();
      return { st, els, said, asked, posts };
    };
    const KEPT = { at: "2026-10-04T10:12:00", how: "replaced", holds: EA.holds };
    let a = null, b = null, c = null;
    try {
      a = await run(false, [200, {}], KEPT);
      b = await run(true, [200, { ok: true, earlier: EA }], KEPT);
      c = await run(true, [404, { detail: "No earlier approval of this page is kept, so there is nothing to put back." }], KEPT);
    } catch (e) { /* no put back on the tree before the repair */ }
    check(!!a && a.asked.length === 1 && a.posts.length === 0, "C: put back asks first; No sends nothing");
    check(!!b && b.posts.length === 1 && /\/api\/job\/A\/page\/2\/putback$/.test(b.posts[0][0]) && b.posts[0][1] === "POST"
      && b.st.tkEarlier === EA && b.st.painted === 1 && b.els.putBackBtn.hidden === true && b.els.reportLink.hidden === false
      && /earlier approval is back in the bid: 41 rows by length/.test((b.said[b.said.length - 1] || [""])[0]),
      "C: Yes posts the act; the page says again that it was approved on an earlier version, the button goes, the downloads show");
    check(!!c && /nothing to put back/.test((c.said[c.said.length - 1] || [""])[0]) && (c.said[c.said.length - 1] || [])[1] === "err",
      "C: a refusal is said in the server's words");
    // the button shows only while the server keeps one for THIS page; pageReset hides it
    const els = { status: { parentNode: { insertBefore() {} } }, withdrawBtn: { hidden: true }, putBackBtn: { hidden: true } };
    const ao = async (answer, page) => {
      const st = { job: "A", page, gen: 7, outdatedSeq: 0, cfLeft: null };
      els.putBackBtn.hidden = true;
      await new Function("state", "fetch", "$", "document", fnSrc("approvalOutdated") + "\nreturn approvalOutdated;")(
        st, async () => ({ ok: true, json: async () => answer }), (id) => els[id], { createElement: () => ({}) })(7);
      return { shown: els.putBackBtn.hidden === false, kept: st.tkKept };
    };
    const x = await ao({ pages: [2], outdated: {}, notes: {}, kept: { 2: KEPT } }, 2);
    const y = await ao({ pages: [2], outdated: {}, notes: {}, kept: { 5: KEPT } }, 2);
    check(x.shown && x.kept && x.kept.holds === EA.holds && !y.shown && !y.kept,
      "C: the Put back button shows only while the server keeps an earlier approval for this page (GET /approved `kept`)");
    check(/state\.tkKept = null/.test(fnSrc("pageReset")) && /\$\("putBackBtn"\)\.hidden = true/.test(fnSrc("pageReset"))
      && /id="putBackBtn" class="mini" hidden/.test(html), "C: ...it is hidden in the markup and again on every page switch");
  }

  // ---- D. nothing the earlier version kept in this browser is restored (areas, counts) -------------------------------
  {
    const det = { engine_sig: "sig-20", scale: { ft_per_paper_inch: 20 },
      areas: { groups: [{ group_id: 0, layer: "P1-BLDG-HATCH", fill: [0.8, 0.8, 0.8], n: 1, region_ids: [0], area_pt2: 301300, perim_pt: 10, name: null }],
               regions: [{ region_id: 0, bbox: [10, 10, 500, 600], n_rings: 2 }] } };
    const mem = new Map(), reads = [], writes = [];
    const store = { getItem: (k) => { reads.push(k); return mem.has(k) ? mem.get(k) : null; },
      setItem: (k, v) => { writes.push(k); mem.set(k, String(v)); }, removeItem: (k) => { writes.push(k); mem.delete(k); } };
    const st = { page: 0, result: null, extraItems: [], areaRows: [], areaInc: new Map(), areaLabels: new Map(), areaNote: "", ewDropped: false };
    const fns = ["calcKey", "calcKey2", "calcKey3", "areaSelKey", "arData", "arGroup", "arRegionsOf", "arLabel", "arFpi", "arSf", "arLf",
      "arApplyItems", "arKey", "arSelPayload", "arSelRestore", "arNoteClear", "ewOldRow", "calcEarlierHadOldEw", "calcSave", "calcRestore"];
    let C = null;
    try {
      C = new Function("state", "store", "docKey", "renderItemized",
        constLine("EW_OLD_CY_RE") + "\n" + constLine("AR_REMEASURED") + "\n" + fns.map(fnSrc).join("\n")
        + "\nreturn { " + fns.join(", ") + " };")(st, store, () => "doc::C4.0.pdf", () => {});
    } catch (e) { /* cannot be built */ }
    const load = () => {
      st.result = JSON.parse(JSON.stringify(det));
      st.extraItems = []; st.areaRows = []; st.areaInc = new Map(); st.areaLabels = new Map(); st.areaNote = "";
      reads.length = 0; writes.length = 0;
      C.calcRestore(); C.arApplyItems();
    };
    // what the live 2.0 page left in this browser for the page: the area it had ticked (its own key, its own stamp), and
    // its calculator rows (a count, a trench run) under its own key
    const K20_AREA = "doc::C4.0.pdf::p0::areasel", K20_ROWS = "doc::C4.0.pdf::p0::calc2", K185_ROWS = "doc::C4.0.pdf::p0::calc";
    st.result = JSON.parse(JSON.stringify(det));
    let key = "";
    try { key = C.arKey(det.areas.groups[0]); } catch (e) { /* */ }
    const A20 = JSON.stringify({ v: 2, engine_sig: "sig-20", rows: [{ key, group_id: 0, unit: "SF" }], inc: [{ key, group_id: 0 }], labels: [] });
    const R20 = JSON.stringify({ core: 2, rows: [{ label: "Light pole", qty: 205, unit: "EA" },
      { label: "O-PIPE (run)", qty: 456.7, unit: "LF", run: 12, runOf: "O-PIPE", kind: "trench_run", utility: "storm" }] });
    const R185 = JSON.stringify([{ label: "Mass Excavation", qty: 90, unit: "CY" }]);
    mem.set(K20_AREA, A20); mem.set(K20_ROWS, R20); mem.set(K185_ROWS, R185);
    tryCheck(() => { load(); return st.areaInc.size === 0 && st.areaRows.length === 0 && st.extraItems.filter(it => it._area).length === 0; },
      "D: the area 2.0 had ticked in this browser does NOT come back (it did: P1-BLDG-HATCH 23,249.2 SF, the one row an approve then exported)");
    tryCheck(() => { load(); return st.extraItems.length === 0; },
      "D: nor does a count or a trench row 2.0 or v1.8.5 kept in this browser: the takeoff starts empty");
    tryCheck(() => { load(); return st.areaNote === "" && !st.ewDropped; }, "D: ...with no note about areas measured again (nothing was), and no old-earthwork note");
    tryCheck(() => { load(); return !reads.includes(K20_AREA) && writes.length === 0
        && mem.get(K20_AREA) === A20 && mem.get(K20_ROWS) === R20 && mem.get(K185_ROWS) === R185; },
      "D: 2.0's area record is never read, opening writes nothing, and the earlier versions' records stay byte for byte (an undo finds them)");
    // this build's own ticks: kept under its OWN keys, and they come back
    tryCheck(() => {
      load();
      st.areaInc.set(0, true); st.areaRows.push({ group_id: 0, unit: "SF" });
      st.extraItems.push({ label: "Light pole", qty: 3, unit: "EA" });
      C.arApplyItems(); C.calcSave();
      const mineA = "doc::C4.0.pdf::p0::areasel3", mineR = "doc::C4.0.pdf::p0::calc3";
      const wroteOwn = mem.has(mineA) && mem.has(mineR) && JSON.parse(mem.get(mineR)).core === 3
        && mem.get(K20_AREA) === A20 && mem.get(K20_ROWS) === R20 && mem.get(K185_ROWS) === R185;
      load();
      return wroteOwn && st.areaInc.get(0) === true && st.extraItems.filter(it => it._area).length === 1
        && st.extraItems.some(it => it.label === "Light pole" && it.qty === 3);
    }, "D: an area ticked and a row added on THIS build are kept under this build's own keys (2.0's untouched) and come back on a reload");
  }

  // ---- E. MINOR-1 / MINOR-2: the screen is the person's set, whatever is declared -------------------------------------
  {
    const hA = js.lastIndexOf("cb.onclick = (e) => { e.stopPropagation();", js.indexOf("const on = !state.include.get(s.style_id);"));
    const hB = js.indexOf("draw(); };", hA);
    const HANDLER = js.slice(hA + "cb.onclick = ".length, hB + "draw(); }".length);
    const FNS = ["scopeApply", "scopeMine", "scopeKeepRow", "scopeTally", "setInclude", "setIncludeTransient", "incSnapshot", "incRestore",
                 "incBulk", "incAfter"];
    const STYLES = [{ style_id: 80, name: "A", length_pt: 15777, layer: "O-SURFACE-FG" },
                    { style_id: 28, name: "C", length_pt: 15141, layer: "P1-CONCRETE", other: true }];
    const page = () => {
      const sent = [], T = { trades: [] };
      const state = { job: "j1", page: 0, result: { styles: STYLES.map(s => Object.assign({}, s)), scale: null },
                      include: new Map(), inheritedInc: new Map(), tkSet: new Set(), incUndo: null,
                      scopeV: new Map(), scopeKeep: new Set(), scopeOffBy: new Set(), scopeOwnerOn: new Set() };
      for (const s of state.result.styles) state.include.set(s.style_id, false);
      const env = { state, scopeRec: () => ({ trades: T.trades.slice(), showAside: true }), scopeEvidenceForStyle: (s) => s,
        scopeVerdict: (s, trades) => (!trades.length ? { v: "unknown" } : s.other ? { v: "out" } : { v: "in" }),
        styleLenPt: (s) => s.length_pt, tkDirty: () => { sent.push([...state.tkSet].sort((a, b) => a - b).join(",")); }, tkFollow: () => {},
        renderScopeBar() {}, renderStyles() {}, renderChips() {}, renderItemized() {}, renderMiniLegend() {}, draw() {}, renderIncBtns() {}, setStatus() {} };
      const C = new Function(...Object.keys(env), FNS.map(fnSrc).join("\n")
        + "\nfunction rowClick(s) { const sv = (state.scopeV && state.scopeV.get(s.style_id)) || { v: 'unknown' };"
        + " const h = " + HANDLER + "; h({ stopPropagation() {} }); }\nreturn { rowClick, " + FNS.join(", ") + " };")(...Object.values(env));
      const on = () => state.result.styles.filter(s => state.include.get(s.style_id)).map(s => s.name).join("");
      const mine = () => state.result.styles.filter(s => state.tkSet.has(s.style_id)).map(s => s.name).join("");
      // the line styles an approve names: every style ticked on screen (approveRun: `labels`)
      const labels = () => [...state.include].filter(([, inc]) => inc).map(([sid]) => sid).sort((a, b) => a - b).join(",");
      const declare = (yes) => { T.trades = yes ? ["Earthwork & Grading"] : []; C.scopeApply(); };
      C.scopeApply();
      return { C, state, on, mine, labels, declare, sent, st: (n) => state.result.styles.find(s => s.name === n) };
    };
    {
      // MINOR-2 (e3.py): two styles ticked, a trade declared (it set P1-CONCRETE aside), approve
      const p = page();
      p.C.rowClick(p.st("C")); p.C.rowClick(p.st("A"));
      const n = p.sent.length;
      p.declare(true);
      check(p.on() === "AC" && p.mine() === "AC" && p.labels() === "28,80",
        "MINOR-2: two styles added, a trade declared: BOTH stay in the takeoff, so an approve names both - the export is the "
        + `record (it named one: 4,382.6 ft of 8,588.4 ft) - on screen '${p.on()}', the set '${p.mine()}', approve names '${p.labels()}'`);
      check(p.state.scopeV.get(28).v === "kept" && p.sent.length === n,
        "MINOR-2: the other trade's style is shown as kept (the person added it), and declaring wrote nothing to the server");
      // a reload under the declaration: the record comes back, the filter runs, and the screen is still the record
      const q = page();
      q.state.tkSet = new Set([28, 80]);
      for (const sid of q.state.tkSet) q.state.include.set(sid, true);          // as tkApply leaves it
      q.declare(true);
      check(q.on() === "AC" && q.mine() === "AC", "MINOR-2: after a reload under the declaration the screen is still the record: " + q.on());
      // a style the person has NOT added is still set aside, and one they take out is set aside again
      const r = page();
      r.C.rowClick(r.st("A"));
      r.declare(true);
      const aside = r.state.scopeV.get(28).v === "out" && r.on() === "A";
      r.C.rowClick(r.st("C")); r.C.rowClick(r.st("C"));
      check(aside && r.on() === "A" && r.mine() === "A" && r.state.scopeV.get(28).v === "out",
        "MINOR-2: a style the person has not added is still set aside; added then taken out, it is out for good and set aside again");
    }
    {
      // MINOR-1 (d3.py scope, B): tick two, declare, none, show everything, undo
      const p = page();
      p.C.rowClick(p.st("C")); p.C.rowClick(p.st("A"));
      p.declare(true);
      p.C.incBulk("the takeoff is empty", () => false);
      check(p.on() === "" && p.mine() === "", "MINOR-1: none under a declared trade: the takeoff and the set are empty");
      p.declare(false);
      p.C.incRestore(p.state.incUndo);
      check(p.on() === "AC" && p.mine() === "AC" && p.sent[p.sent.length - 1] === "28,80",
        `MINOR-1: undo after the declaration was cleared puts back BOTH styles on screen, as in the set sent to the server (the `
        + `screen showed one, the record held two) - on screen '${p.on()}', the set '${p.mine()}', sent '${p.sent[p.sent.length - 1]}'`);
      // the same with the trade still declared at the undo
      const q = page();
      q.C.rowClick(q.st("C")); q.C.rowClick(q.st("A"));
      q.declare(true);
      q.C.incBulk("the takeoff is empty", () => false);
      q.C.incRestore(q.state.incUndo);
      check(q.on() === "AC" && q.mine() === "AC", "MINOR-1: ...and with the trade still declared: " + q.on());
      // a snapshot taken while the screen showed LESS than the set (an older page state) still restores the set
      const r = page();
      r.state.tkSet = new Set([28, 80]); r.state.include.set(80, true); r.state.include.set(28, false);
      const snap = r.C.incSnapshot();
      r.C.incBulk("the takeoff is empty", () => false);
      r.C.incRestore(snap);
      check(r.on() === "AC" && r.mine() === "AC", "MINOR-1: undo restores the person's SET to the screen, whatever the screen showed at the snapshot: " + r.on());
    }
  }

  // ---- F. MINOR-3: a write that failed is kept, sent again, and Approve says the real reason ---------------------------
  {
    const FNS = ["tkKey", "tkDirty", "tkWrite", "tkFlush", "tkUnsavedWords", "tkUnsavedApproveWords", "tkUnsavedShow", "tkBarFit"];
    const page = (answers) => {
      const posts = [], said = [];
      const note = { hidden: true, textContent: "" };            // the approve bar's note (#tkUnsaved)
      const state = { job: "j1", page: 0, result: {}, tkSet: new Set([80]), tkNote: "" };
      let version = 1, i = 0;
      const fetch = async (u, opt) => {
        const b = JSON.parse(opt.body); posts.push(b);
        const a = answers[Math.min(i++, answers.length - 1)];
        if (a === "down") throw new TypeError("Failed to fetch");
        if (a === 500) return { ok: false, status: 500, json: async () => ({ detail: "Internal Server Error" }) };
        if (a === 409) return { ok: false, status: 409, json: async () => ({ detail: pageConst("TK_STALE_SERVER") }) };
        version = b.version + 1;
        return { ok: true, status: 200, json: async () => ({ version, styles: b.styles }) };
      };
      const env = { state, fetch, setStatus: (m, k) => said.push([m, k]), renderItemized() {}, setTimeout, clearTimeout,
                    $: (id) => (id === "tkUnsaved" ? note : null) };
      const C = new Function(...Object.keys(env), [constLine("TK_STALE_NOTE"), constLine("tk"), ...FNS.map(fnSrc)].join("\n")
        + "\nreturn { tk, " + FNS.join(", ") + " };")(...Object.values(env));
      C.tk.ver.set("j1:0", 1);
      const tick = (sid) => { state.tkSet.add(sid); C.tkDirty(); };
      return { C, state, posts, said, tick, note, last: () => (said[said.length - 1] || ["", ""]) };
    };
    const wait = () => new Promise(r => setTimeout(r, 400));
    {
      const p = page([500, 200]);
      p.tick(28);
      await wait();
      const m = p.last()[0];
      check(p.posts.length === 1 && !!p.C.tk.pending && JSON.stringify(p.C.tk.pending.styles) === "[28,80]" && p.C.tk.ver.get("j1:0") === 1,
        "MINOR-3: a tick's write answered 500: the set is KEPT to be sent again (it was dropped), and the tab still holds its version");
      check(/not saved/.test(m) && /error 500/.test(m) && /Internal Server Error/.test(m) && /sent again/.test(m) && p.last()[1] === "err"
        && !/another tab|another computer/.test(m) && p.state.tkNote === "",
        "MINOR-3: the status says the real reason and that it is sent again - not only 'Internal Server Error', nothing about another tab: " + JSON.stringify(m));
      check(p.note.hidden === false && p.note.textContent === m,
        "MINOR-3: ...and the same sentence stays in view in the approve bar while the set waits (the status line clips, and the next message replaces it)");
      let ok = null;
      try { ok = await p.C.tkFlush(); } catch (e) { ok = "threw " + e.message; }
      check(ok === true && p.posts.length === 2 && JSON.stringify(p.posts[1]) === JSON.stringify({ version: 1, styles: [28, 80] })
        && !p.C.tk.pending && p.C.tk.ver.get("j1:0") === 2,
        "MINOR-3: the flush before an approve sends the kept set again on the same version; kept this time, the approve can go: " + JSON.stringify([ok, p.posts.length]));
      check(p.note.hidden === true && p.note.textContent === "", "MINOR-3: ...and the note goes once the set is kept");
    }
    {
      const p = page([500]);
      p.tick(28);
      await wait();
      let ok = null, words = "";
      try { ok = await p.C.tkFlush(); words = p.C.tkUnsavedApproveWords(); } catch (e) { ok = "threw " + e.message; }
      check(ok === false && !!p.C.tk.pending && p.posts.length === 2,
        "MINOR-3: while the server keeps refusing, the flush answers no after ONE more try, and the set is still kept: " + JSON.stringify([ok, p.posts.length]));
      check(/^Approve did not go through: your last takeoff change could not be saved \(the server answered error 500: Internal Server Error\)\./.test(words)
        && /Nothing was approved\. Press Approve again to try again\.$/.test(words) && !/another tab|another computer|Reload/.test(words),
        "MINOR-3: Approve's own sentence says the real reason and offers to try again: " + JSON.stringify(words));
    }
    {
      const p = page(["down", 200]);
      p.tick(28);
      await wait();
      check(!!p.C.tk.pending && /the server did not answer/.test(p.last()[0]) && /sent again/.test(p.last()[0]),
        "MINOR-3: no answer at all is kept and said the same way: " + JSON.stringify(p.last()[0]));
    }
    {
      const p = page([409]);
      p.tick(28);
      await wait();
      check(!p.C.tk.pending && p.C.tk.ver.get("j1:0") === undefined && /changed in another tab/.test(p.last()[0]),
        "MINOR-3: a write the server refuses as STALE is still what it was: not kept, the tab holds no version, and the stale sentence is said");
    }
    {
      // approveRun: a kept set that still cannot be sent stops the approve with that sentence, and sends no approve
      let n = 0;
      const h = approver(async () => { n++; return resp(200, { exported: 1, items: [] }); },
        { include: [[1, true]], rows: [], flush: false, pending: { job: "j1", page: 0, styles: [1] }, why: "the server answered error 500: Internal Server Error" });
      await h.press();
      check(n === 0 && h.last()[1] === "err" && /could not be saved \(the server answered error 500: Internal Server Error\)/.test(h.last()[0])
        && /Press Approve again to try again/.test(h.last()[0]) && !/another tab|Reload/.test(h.last()[0]),
        "MINOR-3: Approve with a change the server did not keep: no approve is sent, and the real reason is said with the offer to "
        + "try again (it said 'changed in another tab or on another computer'): " + JSON.stringify(h.last()[0]));
      check(!!h.out.els.tkUnsaved && h.out.els.tkUnsaved.hidden === false && h.out.els.tkUnsaved.textContent === h.last()[0],
        "MINOR-3: ...and that sentence is put in the approve bar, where it stays in view");
      const g = approver(async () => { n++; return resp(200, { exported: 1, items: [] }); }, { include: [[1, true]], rows: [], flush: false });
      await g.press();
      check(/Reload this page/.test(g.last()[0]), "MINOR-3: ...while a change refused as stale (nothing waits to be sent) still says to reload");
    }
  }

  // ---- G. MINOR-5 / MINOR-6 ----------------------------------------------------------------------------------------------
  {
    const head = fnSrc("sgHeadEl").replace(/\/\/[^\n]*/g, "");
    check(!/its lines from the sheet/.test(head) && !/draws nothing on the sheet/.test(head),
      "MINOR-5: the group box and its footage no longer say unticking takes lines off the sheet / draws nothing (the engine's picks stay drawn, dimmed)");
    check(/drawn dimmed/.test(head) && /still drawn on the sheet, dimmed/.test(head), "MINOR-5: they say what is true: shown, dimmed, not in the takeoff");
    const code = js.replace(/\/\/[^\n]*/g, "");
    check(!/inheritedInc/.test(code) && !/carried over from other sheets/.test(code) && !/Carried over from another sheet/.test(code),
      "MINOR-6: the dead 'carried over from other sheets' block and its never-filled record are gone");
  }

  if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
  console.log("all second-repair page checks passed");
})().catch(e => { console.error("FAIL: " + (e && e.stack || e)); process.exit(1); });
