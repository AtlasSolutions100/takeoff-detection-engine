// SHEETS FOR CUT / FILL on the screen (2026-09-28, Koby: "why do we have to go back and press detect and cut/fill
// on both sheets"). A plan set of several pages asks once which sheets the job needs; the ticked ones not detected
// yet are detected; then ONE open hands them all to the engine. A job already set up opens straight in.
//
// The critic's round (out/verify/2026-09-28/sheet-picker/CRITIC-picker.md): W1 (an Open left running took over a
// second dialog), W4 (messages under the stage), W5 (the count), R1 (older code), R3 (the open landing on another
// job; a fast first answer), R4 (the screen's review waits for its result), M3 (being detected), M4 ("Sheets…"
// shows instead of reloading), M6 (no "Sheets…" on a one-page set).
//
// run: node tests/test_cutfill_sheet_picker_ui.js
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(src, name) {
  const start = src.indexOf("function " + name + "(");
  if (start < 0) return "";
  const pre = src.slice(Math.max(0, start - 6), start) === "async " ? "async " : "";
  let depth = 0;
  for (let j = src.indexOf("{", start); j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}" && --depth === 0) return pre + src.slice(start, j + 1);
  }
  return "";
}
// a very small DOM: elements with children, text, checkbox state and the two queries the picker makes
function el(tag) {
  return { tag, type: "", children: [], className: "", textContent: "", checked: false, disabled: false, value: "",
    hidden: true, cls: new Set(), src: "",
    get classList() { const c = this.cls; return { add: (x) => c.add(x), remove: (x) => c.delete(x), contains: (x) => c.has(x) }; },
    append(...c) { this.children.push(...c); }, set innerHTML(v) { this.children = []; }, focus() {},
    querySelectorAll() { const out = []; const walk = (n) => { for (const k of n.children || []) { if (k.tag === "input") out.push(k); walk(k); } }; walk(this); return out; } };
}
function world(opts) {
  const els = { cfsList: el("div"), cfsMsg: el("span"), cfsOpen: el("button"), cfSheetsModal: el("div"), cfStage: el("div"), cfFrame: el("iframe") };
  const posted = [];
  els.cfFrame.contentWindow = { postMessage: (m) => posted.push(m) };
  const calls = { fetch: [], detect: [], opened: [], shown: [], bar: 0, posted };
  const state = { job: "J", page: 0, kinds: new Array(opts.pages || 2).fill(null), gen: 1, result: opts.noResult ? null : {},
    resultLoaded: opts.noResult ? null : { job: "J", page: 0, gen: 1 } };
  const cfState = { job: "J", pid: "job_J", summary: null };
  const fetchStub = async (url, o) => {
    calls.fetch.push([url, o && o.body ? JSON.parse(o.body) : null]);
    if (url.includes("/cutfill/sheets")) return { ok: true, json: async () => opts.list };
    if (url.endsWith("/cutfill")) return { ok: true, json: async () => (opts.openReply || { pid: "job_J", page: 0, joined: [1], skipped: [], rows_page: null, in_job: 2 }) };
    return { ok: true, json: async () => ({}) };
  };
  const doc = { createElement: (t) => el(t) };
  const src = ["cfSheetsList", "cfSheetsRender", "cfsStatusText", "cfsRowStatus", "cfSkippedText", "cfOpenNotice", "cfSheetsShow",
    "cfSheetsClose", "cfSheetsGo", "cfSheetsAdd"].map((n) => fnSrc(js, n)).join("\n");
  const detect = opts.detect || (async (job, p) => { calls.detect.push(p); return "done"; });
  const f = new Function("state", "cfState", "$", "document", "fetch", "cfDetectPage", "cfOpen", "setStatus", "cfRenderBar",
    "cfFrameUrl", "location", "cfReviewEdits",
    "let cfsState = null;\n" + src + "\nreturn { cfSheetsShow, cfSheetsGo, cfSheetsClose, cfSheetsAdd, cfsRowStatus, get cs() { return cfsState; } };")(
    state, cfState, (id) => els[id], doc, fetchStub, detect,
    async (o) => { calls.opened.push(o); }, () => {}, () => { calls.bar++; }, (pid) => "/cutfill/?embed=1&pid=" + pid, { origin: "http://x" },
    () => ({ excluded_chain_ids: [9] }));
  return { els, calls, state, cfState, f };
}
const list = { in_project: false, pages: [
  { page: 0, label: "3.0", detected: true, role: null },
  { page: 1, label: "3.1", detected: false, role: null },
  { page: 2, label: "5.0", detected: true, role: null }] };

(async () => {
  check(["cfSheetsList", "cfSheetsRender", "cfSkippedText", "cfSheetsShow", "cfSheetsClose", "cfSheetsGo", "cfSheetsAdd", "cfOpen", "cfRenderBar"]
    .every((n) => fnSrc(js, n)), "every function under test is found in app.js");
  // ---- 1. the list: the sheet on screen ticked, the others offered, what happens to each said ----------
  const w = world({ list, pages: 3 });
  w.f.cfSheetsShow(list, false);
  const rows = w.els.cfsList.children;
  const boxes = w.els.cfsList.querySelectorAll();
  check(!w.els.cfSheetsModal.hidden && rows.length === 3 && boxes[0].checked && !boxes[1].checked && !boxes[2].checked,
    "the picker lists every page; the sheet on screen starts ticked");
  check(rows[0].children[1].textContent === "3.0 · page 1" && rows[1].children[2].textContent === "not detected yet: detected first"
    && rows[0].children[2].textContent === "detected", "each row names the sheet and says what happens to it");
  // M3 / R1: a page being detected, and one detected by older code, say so
  const odd = { in_project: false, pages: [{ page: 0, label: "", detected: false, running: true, role: null },
    { page: 1, label: "", detected: false, stale: true, role: null }] };
  const wo = world({ list: odd, pages: 2 });
  wo.f.cfSheetsShow(odd, false);
  check(wo.els.cfsList.children[0].children[2].textContent === "being detected now"
    && wo.els.cfsList.children[1].children[2].textContent === "detected by an older version: detected again first"
    && wo.els.cfsList.children[0].children[1].textContent === "page 1",
    "a page being detected says so, one detected by an older version is detected again first, and a page with no trusted number is named by page only");
  // a page's detection moving on while the picker is open updates its row, and its tick stays
  const ul = JSON.parse(JSON.stringify(list));
  const wu = world({ list: ul, pages: 3 });
  wu.f.cfSheetsShow(ul, false);
  const ub = wu.els.cfsList.querySelectorAll();
  ub[1].checked = true;
  wu.f.cfsRowStatus(1, "running");
  const t1 = wu.els.cfsList.children[1].children[2].textContent;
  wu.f.cfsRowStatus(1, "done");
  const t2 = wu.els.cfsList.children[1].children[2].textContent;
  check(t1 === "being detected now" && t2 === "detected" && ul.pages[1].detected === true && ub[1].checked
    && wu.els.cfsList.querySelectorAll()[1] === ub[1],
    `a detection that moves on while the picker is open is said in its row, the tick kept: "${t1}" then "${t2}"`);
  // ---- 2. Open: the ticked sheet not detected is detected first, then ONE open with every ticked sheet --
  boxes[1].checked = true;
  await w.f.cfSheetsGo();
  check(JSON.stringify(w.calls.detect) === "[1]", `only the ticked sheet not detected is detected: ${JSON.stringify(w.calls.detect)}`);
  check(w.calls.opened.length === 1 && JSON.stringify(w.calls.opened[0].pages) === "[0,1]" && w.els.cfSheetsModal.hidden,
    `then one open hands the engine every ticked sheet: ${JSON.stringify(w.calls.opened)}`);
  // ---- 3. nothing ticked: said, nothing opened ------------------------------------------------------------
  const w2 = world({ list, pages: 3 });
  w2.f.cfSheetsShow(list, false);
  for (const b of w2.els.cfsList.querySelectorAll()) b.checked = false;
  await w2.f.cfSheetsGo();
  check(!w2.calls.opened.length && /tick at least one sheet/.test(w2.els.cfsMsg.textContent) && !w2.els.cfSheetsModal.hidden,
    "nothing ticked: it says so and opens nothing");
  // ---- 4. W1: an Open left running when the dialog is closed never opens anything --------------------------
  let release;
  const gate = new Promise((res) => { release = res; });
  const wr = world({ list, pages: 3, detect: async (job, p) => { wr.calls.detect.push(p); await gate; return "done"; } });
  wr.f.cfSheetsShow(list, false);
  const rb = wr.els.cfsList.querySelectorAll();
  rb[1].checked = true;                                         // dialog 1: 3.0 and 3.1
  const go1 = wr.f.cfSheetsGo();                                // detecting 3.1 ...
  wr.f.cfSheetsClose();                                         // Esc during the detection
  wr.f.cfSheetsShow(list, false);                               // Cut / fill again: dialog 2, only 3.0 ticked, left alone
  release();
  await go1;
  check(!wr.calls.opened.length && !wr.els.cfSheetsModal.hidden && wr.f.cs && wr.f.cs.lst === list,
    `the first Open, closed mid-detection, ends there: dialog 2 stays up and nothing opens (it had opened 3.0 and 3.1): ${JSON.stringify(wr.calls.opened)}`);
  // ---- 5. R4: the sheet on screen is loaded to the end (its work area comes back after the result) before the open -----
  const wn = world({ list, pages: 3, noResult: true });
  wn.f.cfSheetsShow(list, false);
  setTimeout(() => { wn.state.result = { chains: [] }; }, 200);                        // the result alone is not enough
  setTimeout(() => { wn.state.resultLoaded = { job: "J", page: 0, gen: 1 }; }, 500);    // loadResult has ended
  const t0 = Date.now();
  await wn.f.cfSheetsGo();
  check(wn.calls.opened.length === 1 && Date.now() - t0 >= 450,
    `the open waits until the sheet on screen has loaded to the end (the work area comes back after the result): ${Date.now() - t0} ms`);
  // M-a: a sheet on screen that could not be detected is not waited for (its result cannot come)
  const wf = world({ list, pages: 3, noResult: true, detect: async (job, p) => { wf.calls.detect.push(p); return p === 0 ? "error" : "done"; } });
  const lf = JSON.parse(JSON.stringify(list)); lf.pages[0].detected = false;
  wf.f.cfSheetsShow(lf, false);
  const tA = Date.now();
  await wf.f.cfSheetsGo();
  check(wf.calls.opened.length === 1 && Date.now() - tA < 1000,
    `a sheet on screen that could not be detected is not waited for (it waited 15 s): ${Date.now() - tA} ms`);
  // ---- 6. from the bar ("Sheets…"): the sheets in the job stay ticked and fixed; the new ones are added --
  const inJob = { in_project: true, pages: [{ page: 0, label: "3.0", detected: true, role: "both" }, { page: 1, label: "3.1", detected: true, role: null },
    { page: 2, label: "3.2", detected: true, role: null }] };
  const w3 = world({ list: inJob, pages: 3, openReply: { pid: "job_J", page: 0, joined: [1], skipped: [{ page: 2, why: "not detected" }], rows_page: null, in_job: 3 } });
  w3.f.cfSheetsShow(inJob, true);
  const b3 = w3.els.cfsList.querySelectorAll();
  check(b3[0].checked && b3[0].disabled && !b3[1].checked && w3.els.cfsList.children[0].children[2].textContent === "in the cut / fill (both)",
    "from the bar, a sheet already in the job is ticked and fixed (its role changes in the Sheets card)");
  b3[1].checked = true; b3[2].checked = true;
  await w3.f.cfSheetsGo();
  const sent = w3.calls.fetch.find(([u]) => u.endsWith("/cutfill"));
  check(sent && JSON.stringify(sent[1].pages) === "[1,2,0]",
    `adding sends the new sheets with the ones already in: ${JSON.stringify(sent && sent[1])}`);
  // the third critic's m6: the sheet on screen goes, so the review's decisions go with it (the dialog says they do)
  check(sent && JSON.stringify(sent[1].edits) === '{"excluded_chain_ids":[9]}',
    `the sheet on screen is among them: the review's decisions go with it: ${JSON.stringify(sent && sent[1].edits)}`);
  const w3b = world({ list: inJob, pages: 3 });
  w3b.state.page = 2;
  await w3b.f.cfSheetsAdd("J", [0, 1]);
  const sentB = w3b.calls.fetch.find(([u]) => u.endsWith("/cutfill"));
  check(sentB && !("edits" in sentB[1]), "the sheet on screen not among them: no review is sent (it is that sheet's)");
  // W4 / W5 / M4: said on the bar as what happened; the engine is told to show, not reloaded
  check(w3.cfState.notice === "added 1 sheet · left out: page 3 (not detected)" && w3.calls.bar === 1,
    `what the add did is said on the cut / fill bar, as what happened (a role changed later cannot make it false): ${w3.cfState.notice}`);
  check(w3.calls.posted.length === 1 && w3.calls.posted[0].type === "show" && w3.calls.posted[0].key === "d0p0" && w3.els.cfFrame.src === "",
    `the engine is told to show (it reads the project again and keeps each sheet's view), not reloaded: ${JSON.stringify(w3.calls.posted)}`);
  // M-d: "Sheets…" on a cut / fill that belongs to another plan set does nothing but say so; the add goes to its own job
  const sh = js.slice(js.indexOf('$("cfSheets").onclick'), js.indexOf('$("cfSheets").onclick') + 700);
  check(/if \(cfState\.job !== job\) \{ cfState\.notice = "this cut \/ fill belongs to another plan set/.test(sh)
    && /if \(state\.job !== job \|\| cfState\.job !== job\) return;/.test(fnSrc(js, "cfSheetsAdd")),
    "M-d: after a job switch with the cut / fill still up, 'Sheets…' says whose it is and adds nothing");
  // R1 (second round): a sheet in the job whose detection an older version made says so; the open's words name it
  const ws = world({ list, pages: 2 });
  const ls = { in_project: true, pages: [{ page: 0, label: "3.0", detected: false, stale: true, role: "both" }, { page: 1, label: "", detected: false, stale: true, role: null }] };
  ws.f.cfSheetsShow(ls, true);
  check(ws.els.cfsList.children[0].children[2].textContent === "in the cut / fill (both) · detected by an older version: detect it again"
    && ws.els.cfsList.children[1].children[2].textContent === "detected by an older version: detected again first",
    "a sheet in the job detected by an older version says so; one not in the job is detected again first");
  const on = new Function("cfSkippedText", fnSrc(js, "cfOpenNotice") + "\nreturn cfOpenNotice;")(
    new Function(fnSrc(js, "cfSkippedText") + "\nreturn cfSkippedText;")());
  check(on({ in_job: 2, skipped: [{ page: 2, why: "not detected" }], older: [0] }) === "opened with 2 sheets · left out: page 3 (not detected) · page 1 detected by an older version: detect again"
    && on({ older: [0] }) === "page 1 detected by an older version: detect again" && on({ joined: [3, 4] }, true) === "added 2 sheets",
    "the open's words: what it opened with, what was left out, which sheet an older version detected");
  // the third critic's w2 and r1: the server's count of sheets added (the one opened on too); a sheet detected again
  check(on({ added: [1], joined: [] }, true) === "added 1 sheet" && on({ reread: [1] }) === "page 2 detected again: being read again",
    "'added' counts the sheet the open went to when it was new (it said 'added 0 sheets'); a sheet detected again is said");
  // the fourth critic's m9: a linked set's re-read sheets are counted, on the plain open too
  check(on({ reread_linked: 2 }) === "2 sheets of a linked plan set detected again: being read again"
      && /\|\| j\.reread_linked( \|\| j\.hidden_note)?\)/.test(fnSrc(js, "cfOpenRun")) && !/scale_note/.test(js),
    "a linked plan set's re-read sheets are said, on either open; no dropped-scale words are left (nothing is dropped)");
  // critic bid-content-1, WRONG-2: sheets of a linked set the account may no longer open, taken out by the open, are said
  check(on({ hidden_note: "1 sheet(s) comes from a plan set your account can no longer open" }).includes("can no longer open")
      && /hidden_note: j\.hidden_note/.test(fnSrc(js, "cfOpenRun")),
    "the open's note on sheets taken out (a linked set this account may no longer open) is said, on either open");
  // ---- 7. the opener --------------------------------------------------------------------------------------------
  // the third critic's m7: a second call gets the running open's promise (it returned at once, and the redesign's 3D
  // button then found the stage closed)
  let endRun;
  const runs = [], ends = [];
  const ost = { job: "A" };
  const opener = new Function("cfOpenRun", "state", "let cfOpening = null, cfOpeningJob = null;\n" + fnSrc(js, "cfOpen") + "\nreturn cfOpen;")(
    (o) => { runs.push(o); return new Promise((res) => { endRun = res; ends.push(res); }); }, ost);
  const pA = opener({ a: 1 }), pB = opener({ b: 2 });
  endRun("done");
  const [ra, rb2] = await Promise.all([pA, pB]);
  const pC = opener({ c: 3 }); endRun("again"); const rc = await pC;
  check(pA === pB && runs.length === 2 && ra === "done" && rb2 === "done" && rc === "again",
    "R3 / m7: a second click while an open is on its way waits for that open and ends with it; the next click opens again");
  // the fourth critic's m1: an open on its way for ANOTHER plan set does not swallow a click for the set on screen
  const pD = opener({ d: 4 });                 // set A's open, on its way
  ost.job = "B";                               // set B opened meanwhile
  const pE = opener({ e: 5 });
  check(pD !== pE && runs.length === 4, "a click for the plan set now on screen opens it (it waited for the other set's open)");
  ends.forEach((f) => f("end"));
  await Promise.all([pD, pE]);
  const open = fnSrc(js, "cfOpenRun");
  check(/if \(!pages && state\.kinds\.length > 1\)/.test(open) && /cfSheetsList\(job, false\)/.test(open)
    && /head && head\.n > 1 && !head\.in_project/.test(open) && /cfSheetsList\(job, true\)/.test(open)
    && /return cfSheetsShow\(lst, false\)/.test(open) && /\.\.\.\(pages \? \{ pages \} : \{\}\)/.test(open),
    "Cut / fill asks for the sheets on a set's first open only (a fast answer first, the sheet numbers only for the picker), and passes the ticked ones");
  check(/const job = state\.job, page = state\.page;/.test(open) && /if \(moved\(\)\) return setStatus\(""\);/.test(open)
    && /fetch\(`\/api\/job\/\$\{job\}\/cutfill`/.test(open) && /if \(state\.job !== job\) return;/.test(open)
    && /setStatus\("checking this plan set's cut \/ fill…"\)/.test(open),
    "R3: the open belongs to the job and page it started on (another set opened meanwhile never receives it), and says it is busy");
  check(/cfState\.notice = pages \? cfOpenNotice\(j\)/.test(open) && !/setStatus\(`left out/.test(open)
    && /const opening = pages \? "opening the cut \/ fill with the ticked sheets…" : "opening the cut \/ fill…";\n  setStatus\(opening\);/.test(open),
    "W4: the picker's open says what it did on the bar, not under the stage; R3: it says it is opening while it works");
  // a fresh engine frame opens on the sheet Cut / fill was opened from (live check 2026-09-28: opened from 3.1 it showed
  // 3.0, the last compute's sheet, while the volumes used 3.1)
  const fu = new Function("document", fnSrc(js, "cfFrameUrl") + "\nreturn cfFrameUrl;")({ documentElement: { dataset: { theme: "" } } });
  const ejs = fs.readFileSync(path.join(__dirname, "..", "webui", "cutfill", "app.js"), "utf8");
  check(fu("job_J", "d0p1").endsWith("&key=d0p1") && !/key=/.test(fu("job_J"))
    && /\$\("cfFrame"\)\.src = cfFrameUrl\(j\.pid, "d0p" \+ \(Number\.isInteger\(j\.page\) \? j\.page : page\)\);/.test(open)
    && /const asked = EMBED && PARAMS\.get\('pid'\) === pid \? PARAMS\.get\('key'\) : null;/.test(ejs)
    && /\(asked && pageInfo\(asked\) \? asked : null\) \|\| \(state\.result && state\.result\.display_page\)/.test(ejs),
    "a fresh engine frame opens on the sheet Cut / fill was opened from (it opened on the last compute's sheet)");
  check(/if \(\$\("status"\)\.textContent === opening\) setStatus\(""\);/.test(open) && !/\n  setStatus\(""\);\n  if \(state\.job !== job\) return;/.test(open),
    "m9: the open's words leave the status line, and only they (whatever was said meanwhile stays)");
  check(/signal: ac\.signal/.test(open) && /setTimeout\(\(\) => ac\.abort\(\), 90000\)/.test(open) && /no answer in 90 s/.test(open),
    "m7: an open that never answers ends after 90 s and says so (every later click waits for it)");
  // ---- 7b. the third critic's w3: the stage belongs to the plan set that opened it ------------------------------
  const ap = fnSrc(js, "applyOpened");
  check(ap.indexOf("cfCloseForJob()") > 0 && ap.indexOf("cfCloseForJob()") < ap.indexOf("state.job = j.job_id"),
    "opening another plan set closes the cut / fill first, before the new set is on screen");
  const sw = { cfStage: { hidden: false }, cfSheetsModal: { hidden: false }, cfFrame: { src: "/cutfill/?pid=job_A", getAttribute() { return this.src; } } };
  const closeFor = new Function("$", "toolsMenuSync",
    "let cfState = { job: 'A', pid: 'job_A', summary: { cut_cy: 12000 }, notice: 'x' }; let cfsState = { lst: {} };\n"
    + fnSrc(js, "cfClose") + "\n" + fnSrc(js, "cfSheetsClose") + "\n" + fnSrc(js, "cfCloseForJob")
    + "\ncfCloseForJob(); return { cfState, cfsState };")((id) => sw[id], () => {});
  check(sw.cfStage.hidden && sw.cfSheetsModal.hidden && closeFor.cfState.job === null && closeFor.cfState.pid === null
    && closeFor.cfsState === null && sw.cfFrame.src === "about:blank",
    `the stage and the picker close, the old set's frame is emptied, and nothing names its project: ${JSON.stringify(closeFor.cfState)}, frame ${sw.cfFrame.src}`);
  // the result listener: a result of set A with set B on screen changes nothing (U5: it replaced B's rows and saved them)
  const li = js.indexOf('if (e.data.type !== "result" || e.data.pid !== cfState.pid) return;');
  const lsrc = js.slice(js.lastIndexOf('window.addEventListener("message", (e) => {', li), js.indexOf("\n});", li) + 4);
  const applied = [];
  const lst8 = { fn: null };
  new Function("window", "location", "state", "cfState", "cfRowsHere", "cfAdded", "cfApplyItems", "cfDropItems", "cfRows", "setStatus", "cfRenderBar",
    lsrc)({ addEventListener: (t, fn) => { lst8.fn = fn; } }, { origin: "http://x" }, { job: "B", result: {} }, { job: "A", pid: "job_A", summary: null },
    () => true, () => true, (s) => applied.push(s), () => applied.push("drop"), () => [], () => {}, () => {});
  lst8.fn({ origin: "http://x", data: { source: "cutfill", type: "result", pid: "job_A", result: { cut_cy: 12000 } } });
  check(lst8.fn && !applied.length, `a result of another plan set is never written into the takeoff on screen: ${JSON.stringify(applied)}`);
  const addSrc = js.slice(js.indexOf('$("cfAdd").onclick = () => {'), js.indexOf('$("cfAdd").onclick = () => {') + 400);
  check(/if \(cfState\.job !== state\.job\) return setStatus\("this cut \/ fill belongs to another plan set/.test(addSrc),
    "'Add to takeoff' refuses the volumes of another plan set");
  check(/if \(run\.job && run\.job !== state\.job\) return cfSheetsClose\(\);/.test(fnSrc(js, "cfSheetsGo"))
    && /cfsState = \{ lst, adding: !!adding, job: state\.job \};/.test(fnSrc(js, "cfSheetsShow")),
    "a picker shown for another plan set adds nothing to this one");
  check(/cfState\.notice = "";/.test(fnSrc(js, "cfClose")), "M-c: what an open did is cleared when the cut / fill closes");
  check(/\$\("cfSheets"\)\.hidden = !\(state\.kinds && state\.kinds\.length > 1\)/.test(open), "M6: no Sheets… on a one-page set");
  // ---- 8. the bar leads with the notice ----------------------------------------------------------------------
  const barSrc = [(js.match(/const CF_ROW_RE = [^\n]+/) || [""])[0], "cfIsRow", "cfAdded", "cfRowsHere", "cfRowsElsewhere", "cfRows", "cfScaleTxt",
    "cfScaleNote", "cfSentence", "cfRenderBar"].map((n, i) => (i === 0 ? n : fnSrc(js, n))).join("\n");
  const bels = { cfSum: { textContent: "" }, cfNote: { textContent: "", title: "" }, cfAdd: { textContent: "", title: "", disabled: false } };
  new Function("$", "state", "cfState", "CF_ADD_TITLE", barSrc + "\ncfRenderBar();")((id) => bels[id], { extraItems: [], result: {}, page: 0 },
    { summary: null, rowsPage: null, hint: "", notice: "opened with 2 sheets · left out: page 3 (not detected)" }, "t");
  check(bels.cfNote.textContent.startsWith("opened with 2 sheets · left out: page 3 (not detected)"),
    `the bar's note leads with what the open did: ${bels.cfNote.textContent}`);
  // M-b: after the scale warning, which the bar puts first on purpose
  const bels2 = { cfSum: { textContent: "" }, cfNote: { textContent: "", title: "" }, cfAdd: { textContent: "", title: "", disabled: false } };
  const vol = { cut_cy: 1, fill_cy: 1, net_cy: 0, area_sf: 1, scale_ft_per_in: 50, scale_source: "read in review",
    warnings: ["the volumes move with the square of the scale"], questions: [] };
  new Function("$", "state", "cfState", "CF_ADD_TITLE", barSrc + "\ncfRenderBar();")((id) => bels2[id], { extraItems: [], result: {}, page: 0 },
    { summary: vol, rowsPage: null, hint: "", notice: "opened with 2 sheets" }, "t");
  check(bels2.cfNote.textContent.startsWith("SCALE DISAGREES WITH THE SHEET · opened with 2 sheets · "),
    `the scale warning stays first, the open's words next: ${bels2.cfNote.textContent}`);
  // a sheet the compute left out (not lined up): said first in capitals too - the volumes cover part of the job only
  const bels3 = { cfSum: { textContent: "" }, cfNote: { textContent: "", title: "" }, cfAdd: { textContent: "", title: "", disabled: false } };
  const vol3 = Object.assign({}, vol, { warnings: ["Sheet 3.1 is not lined up with the sheet shown (…): it is left out. Pick two matching points in Sheet alignment."] });
  new Function("$", "state", "cfState", "CF_ADD_TITLE", barSrc + "\ncfRenderBar();")((id) => bels3[id], { extraItems: [], result: {}, page: 0 },
    { summary: vol3, rowsPage: null, hint: "", notice: "opened with 2 sheets" }, "t");
  check(bels3.cfNote.textContent.startsWith("A SHEET IS LEFT OUT · opened with 2 sheets · "),
    `a sheet left out of the volumes leads the bar's note, before the open's words: ${bels3.cfNote.textContent}`);
  // the fifth critic's r-e: a sheet left out because it is not read yet leads the same way (it got no lead)
  const bels4 = { cfSum: { textContent: "" }, cfNote: { textContent: "", title: "" }, cfAdd: { textContent: "", title: "", disabled: false } };
  const vol4 = Object.assign({}, vol, { warnings: ["Sheet 3.1 is not read yet (Analyze, in the Sheets card): it is left out."] });
  new Function("$", "state", "cfState", "CF_ADD_TITLE", barSrc + "\ncfRenderBar();")((id) => bels4[id], { extraItems: [], result: {}, page: 0 },
    { summary: vol4, rowsPage: null, hint: "", notice: "" }, "t");
  check(bels4.cfNote.textContent.startsWith("A SHEET IS LEFT OUT · "),
    `a sheet not read yet, left out, leads the bar's note too: ${bels4.cfNote.textContent}`);
  check(/id="cfSheetsModal"/.test(html) && /id="cfSheets" class="mini"/.test(html) && /id="cfsOpen"/.test(html)
    && /go along for the sheet on screen only/.test(html),
    "the picker and the bar's Sheets… button are in the page, and the picker says the review goes with the sheet on screen only");
  console.log(fails ? "\n" + fails + " failure(s)" : "\nall ok");
  process.exit(fails ? 1 : 0);
})();
