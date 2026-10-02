// THE SEVENTH AND EIGHTH CRITICS' ROUNDS ON THE SCREEN (out/verify/2026-09-28/CRITIC-repairs-7.md, -8.md). A stored
// result that stands no more (its sheet shown, scale or an input changed, or the scale is refused on its sheet now)
// comes back stale WITH the why, the host takes its rows out and says why; the page's own measures give no number on a
// sheet the scale in force must not measure. Round 8: the page's own stale marks and the server's are kept apart (the
// server's is set AND cleared by its answers), an answer about another result is ignored, a moved scale or a failed
// takeoff request posts stale, and the host asks the engine itself on every page load that holds the rows.
//
// run: node tests/test_cutfill_scale_stale_ui.js
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

(async () => {
  // ---- 1. the engine page: the scale not checked for a sheet measures nothing there --------------------------------
  const WHY = 'The scale 1" = 50 ft was read on sheet 3.1, but sheet 3.0 reads 1" = 40 ft in review: type sheet 3.0\'s scale in the Scale card and press Enter (volumes move with the square of the scale).';
  const state = { key: "d0p0", analyzes: {}, result: null, stale: false, staleWhy: "", staleLocal: false, staleServer: "", takeoffFailed: "",
    pid: "p", viewMode: "2d", project: { settings: { scale_ft_per_in: 50 }, scale_refused: { d0p0: WHY }, pages: {} } };
  const posted = [];
  const page = new Function("state", "postToHost", "renderResult", "refreshTransforms", "sheetName", "buildQuestions", "fmtScale",
    ["scaleRefused", "scaleFtPerPt", "noScaleWords", "staleNow", "markStale", "staleFromServer", "setResult", "hostSummary"].map((n) => fnSrc(ejs, n)).join("\n")
    + "\nreturn { scaleRefused, scaleFtPerPt, noScaleWords, staleNow, markStale, staleFromServer, setResult, hostSummary };")(
    state, (m) => posted.push(m), () => {}, () => {}, (k) => (k === "d0p0" ? "3.0" : "3.1"), () => [], (v) => String(Math.round(v * 100) / 100));
  check(page.scaleFtPerPt("d0p0") === 0 && Math.abs(page.scaleFtPerPt("d0p1") - 50 / 72) < 1e-12 && page.scaleFtPerPt() === 0,
    "no feet per point on a sheet the scale in force must not measure; the other sheet measures (critic 7, r-1)");
  check(/not checked for this sheet \(the Scale card says why\)/.test(page.noScaleWords("d0p0"))
    && page.noScaleWords("d0p1") === "set the scale for the area",
    "the words that stand in for the number say why: " + page.noScaleWords("d0p0"));
  state.key = "d0p1";
  check(/not checked for sheet 3\.0 \(its Scale card says why\)/.test(page.noScaleWords("d0p0")),
    "on another sheet the words name the sheet: " + page.noScaleWords("d0p0"));
  state.key = "d0p0";
  // ---- 2. stale: the page's own marks and the server's, apart ---------------------------------------------------------
  const R = { totals: { cut_cy: 0, fill_cy: 900.2, net_cy: 900.2, area_sf: 24305.6 }, ft_per_in: 50, warnings: [], computed_at: 1000.5 };
  page.setResult(Object.assign({}, R, { stale_why: "the scale changed since this compute (1\" = 50 ft then, 40 ft now)" }));
  const h1 = posted[posted.length - 1].result;
  check(state.stale === true && /50 ft then, 40 ft now/.test(state.staleWhy) && h1.stale === true && /40 ft now/.test(h1.stale_why || "")
    && h1.computed_at === 1000.5,
    "a reload of a result that stands no more: stale, the why kept and sent to the host with the compute's time (critic 7, WRONG-2)");
  page.setResult(Object.assign({}, R));
  check(state.stale === false && state.staleWhy === "" && posted[posted.length - 1].result.stale_why === null,
    "a result that stands is fresh and sends no why");
  const RW = "the scale 1\" = 50 ft was read on sheet 3.1, but sheet 3.0 reads 1\" = 40 ft in review";
  page.staleFromServer(RW, 1000.5);
  const sSet = state.stale && /reads 1" = 40 ft in review/.test(state.staleWhy) && /reads 1" = 40 ft/.test(page.hostSummary(state.result).stale_why);
  page.staleFromServer("", 1000.5);
  check(sSet && state.stale === false && state.staleWhy === "",
    "the server's answer sets the stale mark AND clears it when the result stands again (a false STALE stuck: critic 8, r-2)");
  page.staleFromServer(RW, 999.0);
  check(state.stale === false, "an answer about another result (a request that crossed a compute) is ignored (critic 8, r-3)");
  page.markStale();
  page.staleFromServer("", 1000.5);
  check(state.stale === true && state.staleWhy === "an input changed here since this compute",
    "this page's own change stands until a compute, whatever the server says of the stored result");
  page.setResult(Object.assign({}, R));
  // the page posts stale when it sees the scale moved or could not ask the server (critic 8, r-1: posted fresh, the
  // host re-filed the volumes at the old scale from the totals)
  state.project.settings = { scale_ft_per_in: 40, scale_source: "confirmed in review" };
  const hMoved = page.hostSummary(Object.assign({}, R, { ft_per_in: 50 }));
  state.project.settings = { scale_ft_per_in: 50, scale_source: "confirmed in review" };
  const hSame = page.hostSummary(Object.assign({}, R, { ft_per_in: 49.99994348603031 }));
  state.takeoffFailed = "500";
  const hFailed = page.hostSummary(Object.assign({}, R));
  state.takeoffFailed = "";
  check(hMoved.scale_ft_per_in === 50 && hMoved.scale_source === "the scale of that compute" && hMoved.stale === true
    && /the scale changed since this compute \(1" = 50 ft then, 40 ft now\)/.test(hMoved.stale_why || "")
    && hSame.scale_source === "confirmed in review" && hSame.stale === false
    && hFailed.stale === true && /could not be built just now/.test(hFailed.stale_why || ""),
    "the host hears a moved scale or a failed takeoff request as stale, never as fresh: " + hMoved.stale_why + " / " + hFailed.stale_why);
  state.project.settings = { scale_ft_per_in: 50 };
  state.result = { error: "refused" }; state.staleLocal = false; state.staleServer = ""; state.stale = false; state.staleWhy = "";
  page.staleFromServer("anything", 1000.5);
  check(state.stale === false, "a refusal is never marked stale (it has no volumes)");
  const refresh = fnSrc(ejs, "refreshTakeoff");
  check(/staleFromServer\(t\.result_stale \|\| '', t\.result_at\);/.test(refresh) && refresh.indexOf("staleFromServer(") < refresh.indexOf("postToHost(")
    && /state\.takeoffFailed = e\.message \|\| 'no answer';/.test(refresh) && /state\.takeoffFailed = '';/.test(refresh)
    && /t\.result_at > r0\.computed_at \+ 1e-6/.test(refresh),
    "refreshTakeoff sets or clears the server's mark before it posts the host, a failure is kept for the post, and a newer "
    + "stored result (another tab's compute) is taken in");
  check(/if \(r && r\.error !== 'not computed'\) setResult\(r\);/.test(fnSrc(ejs, "loadProject")),
    "a reload shows a stored refusal as one (it read 'Not computed yet' and the host 'an input changed')");
  const ty = fnSrc(ejs, "typeScale");
  check(/scale_sheet: forKey/.test(ty) && /const forKey = state\.scaleTypedFor \|\| state\.key;/.test(ty)
    && /const key = state\.key;/.test(fnSrc(ejs, "chooseScale")) && /scale_sheet: key/.test(fnSrc(ejs, "chooseScale")),
    "the operator's scale names the sheet on screen (critic 8, r-8)");
  // critic 9, m2: the same number goes to the server when ANY sheet's scale is refused (the sheet measured is not always
  // the one on screen), or when this sheet has no word of the operator's yet; the same number marks nothing stale on the
  // page (the server says whether the result stands); a new number does
  const typeRun = async (o) => {
    const sent = [], marks = [], told = [], said = [];
    // the result on the page: computed at 50 unless said (null: none)
    const st = { key: o.key || "d0p1", scaleTypedFor: o.typedFor || null, result: o.result === undefined ? { ft_per_in: 50 } : o.result,
      project: { settings: { scale_ft_per_in: o.inForce || 50, scale_source: o.source || "typed" }, scale_refused: o.refused || {}, pages: o.pages || {} } };
    const fn = new Function("$", "state", "patchSettings", "renderScale", "renderLimit", "markStale", "refreshTakeoff", "setStatus", "fmtScale",
      "scaleRefused", "ownSheetScale", "scaleSetWords", "tellHostScale", "reviewTook", ty + "\nreturn typeScale;")(() => ({ value: String(o.v) }), st,
      async (b) => { if (o.fail) throw new Error("no"); sent.push(b); }, () => {}, () => {}, () => marks.push("stale"), () => marks.push("refresh"),
      (m) => said.push(m), (v) => String(v), (k) => (st.project.scale_refused[k] || ""), (k) => (o.own && o.own[k] != null ? o.own[k] : null),
      (k, v) => `words ${k} ${v}`, (k, v, t) => told.push([k, v, t]), () => o.took !== false);
    for (const a of (o.calls || [undefined])) await fn(a);
    return { sent, marks, told, said };
  };
  const t1 = await typeRun({ v: 50, refused: { d0p0: WHY }, pages: { d0p1: { operator_scale: 50 } } });
  const t2 = await typeRun({ v: 50, pages: { d0p1: { operator_scale: 50 } } });
  const t3 = await typeRun({ v: 50, pages: {} });
  const t4 = await typeRun({ v: 45, pages: { d0p1: { operator_scale: 50 } } });
  // the number typed on 3.1 and sent when the box lost focus after a switch to 3.0 is still 3.1's word (it was sent as
  // 3.0's, and 3.0 was computed at 40: the live check of critic 11's round)
  const t5 = await typeRun({ v: 40, key: "d0p0", typedFor: "d0p1", pages: {} });
  check(t5.sent.length === 1 && t5.sent[0].scale_sheet === "d0p1"
    && /state\.scaleTypedFor = state\.key/.test(ejs) && /state\.scaleTypedFor = null/.test(fnSrc(ejs, "renderScale")),
    "a number typed on one sheet is sent as that sheet's word even when the box loses focus after a switch; the box "
    + "shown again clears it: " + JSON.stringify(t5.sent));
  const sp = fnSrc(ejs, "selectPage");
  check(/if \(state\.key !== key && sin && document\.activeElement === sin\) sin\.blur\(\);/.test(sp)
    && sp.indexOf("sin.blur()") < sp.indexOf("state.key = key"),
    "critic 12, m2: a sheet switch blurs the Scale box first, so a number still in it goes out for the sheet it was typed "
    + "on, before the switch");
  check(t1.sent.length === 1 && t1.sent[0].scale_sheet === "d0p1" && t2.sent.length === 1
    && t3.sent.length === 1 && !t3.marks.includes("stale") && t3.marks.includes("refresh")
    && t4.sent.length === 1 && t4.marks.includes("stale"),
    "m2 / critic 17, WRONG-3: the same number typed is sent - always, on an Enter (the page's copy of the project can be "
    + "old); it marks nothing stale on its own; a new number does: " + JSON.stringify([t1.sent.length, t2.sent.length, t3.marks, t4.marks]));
  // round 16 (the review wins): typed for a sheet that is not the sheet shown and has a review, the number is compared with
  // that sheet's own scale (3.1's review says 40, the scale in force is 50: typing 40 moves nothing there), the host is
  // told the sheet's new scale, and the status says what the typing set (the page's own words)
  const t6 = await typeRun({ v: 40, key: "d0p1", own: { d0p1: 40 }, pages: {} });
  const t7 = await typeRun({ v: 30, key: "d0p1", own: { d0p1: 40 }, pages: {} });
  const t7b = await typeRun({ v: 30, key: "d0p1", own: { d0p1: 40 }, pages: {}, took: false });
  check(t6.sent.length === 1 && !t6.marks.includes("stale") && !t7.marks.includes("stale")
    && JSON.stringify(t6.told) === JSON.stringify([["d0p1", 40, `1" = 40'`]]) && t6.said[0] === "words d0p1 40" && !t7b.told.length,
    "a number typed for another sheet with a review is that sheet's own: the host is told only when the review took it, the "
    + "status says what was set, and the page marks nothing stale on its own (the server judges: critic 17, WRONG-4): "
    + JSON.stringify([t6.told, t6.said, t7.marks, t7b.told]));
  // the live check of round 16: the review's 40 handed over at an open, the operator types back the 50 the volumes were
  // computed at - sent, and NOT marked stale here (the server says the result stands; the page's own mark kept the rows
  // out). A number the volumes were not computed at is marked; no result, nothing to mark
  const t8 = await typeRun({ v: 50, key: "d0p0", inForce: 40, result: { ft_per_in: 50 }, pages: {} });
  const t9 = await typeRun({ v: 40, key: "d0p0", inForce: 40, result: { ft_per_in: 50 }, pages: {} });
  const t10 = await typeRun({ v: 45, key: "d0p0", result: null, pages: {} });
  // the change a blur fires after an Enter already sent the number for the sheet is not sent again (it wrote 45 back into
  // 3.1's review over a decision made in between); a change with no Enter before it, or a second Enter, go; a refused number
  // is not sent again by the change after it (critic 18, MINOR-1: it went twice)
  const t11 = await typeRun({ v: 45, key: "d0p1", own: { d0p1: 40 }, pages: {}, calls: [undefined, { type: "change" }] });
  const t12 = await typeRun({ v: 45, key: "d0p1", own: { d0p1: 40 }, pages: {}, calls: [{ type: "change" }] });
  const t13 = await typeRun({ v: 45, key: "d0p1", own: { d0p1: 40 }, pages: {}, calls: [undefined, undefined] });
  const t14 = await typeRun({ v: 45, key: "d0p1", own: { d0p1: 40 }, pages: {}, calls: [undefined, { type: "change" }], fail: true });
  check(t8.sent.length === 1 && !t8.marks.includes("stale") && t9.marks.includes("stale") && t10.sent.length === 1 && !t10.marks.includes("stale")
    && t11.sent.length === 1 && t12.sent.length === 1 && t13.sent.length === 2 && t14.said.filter((m) => /Could not save/.test(m)).length === 1,
    "typing back the volumes' own scale marks nothing stale on the page; the change after an Enter is not a second send: "
    + JSON.stringify([t8.marks, t9.marks, t11.sent.length, t12.sent.length, t13.sent.length, t14.said]));
  // the host is told which review page a sheet came from (its detection's path, either slash), and nothing for a sheet
  // with none
  {
    const posted = [];
    const mk = new Function("state", "postToHost", [fnSrc(ejs, "sheetReviewPage"), fnSrc(ejs, "tellHostScale")].join("\n") + "\nreturn [sheetReviewPage, tellHostScale];");
    const stR = { pid: "job_A", project: { pages: { d0p0: { core_result: "C:\\jobs\\A\\p3_result.json" }, d0p1: { core_result: "/srv/jobs/A/p0_result.json" },
      d0p2: {}, d0p3: { core_result: "C:/jobs/A/p3_scale.json" } } } };
    const [srp, tell] = mk(stR, (m) => posted.push(m));
    tell("d0p0", 40, "1\" = 40'");
    tell("d0p2", 40, "x");
    check(JSON.stringify(srp("d0p0")) === JSON.stringify({ job: "A", page: 3 }) && JSON.stringify(srp("d0p1")) === JSON.stringify({ job: "A", page: 0 })
      && JSON.stringify(srp("d0p2")) === JSON.stringify({ job: "A", page: 2 }) && srp("d0p3") === null
      && JSON.stringify(srp("d0p9")) === JSON.stringify({ job: "A", page: 9 }) && srp("d1p0") === null
      && JSON.stringify(posted) === JSON.stringify([{ type: "scale", pid: "job_A", job: "A", page: 3, ft: 40, text: "1\" = 40'" },
        { type: "scale", pid: "job_A", job: "A", page: 2, ft: 40, text: "x" }]),
      "the host is told the review page a sheet answers to: its detection's, else (none on record) the job's own page - as "
      + "the host's _cf_sheet_job_page (critic 17, WRONG-1); one on record that names no detection, or another plan set's "
      + "sheet, none: " + JSON.stringify(posted));
  }
  // the page's helpers: a sheet's own scale only where it is not the sheet shown and has a review; the words name both
  {
    const own = new Function("state", "shownKey", fnSrc(ejs, "ownSheetScale") + "\nreturn ownSheetScale;");
    const stO = { project: { review_scales: { d0p0: 50, d0p1: 40 } } };
    const f = own(stO, () => "d0p0");
    const words = new Function("state", "ownSheetScale", "fmtScale", "sheetName", "shownKey", fnSrc(ejs, "scaleSetWords") + "\nreturn scaleSetWords;")(
      { project: { settings: { scale_ft_per_in: 50 } } }, f, (v) => String(v), (k) => ({ d0p0: "3.0", d0p1: "3.1" })[k], () => "d0p0");
    const alone = own({ project: { review_scales: {} } }, () => "d0p0");
    check(f("d0p1") === 40 && f("d0p0") === null && f("d0p9") === null && alone("d0p1") === null
      && words("d0p1", 40) === `Sheet 3.1's scale set: 1" = 40 ft (in its review too). The cut / fill measures sheet 3.0 at 1" = 50 ft.`
      && words("d0p0", 40) === `Scale set: 1" = 40 ft.` && words("d0p0", 40, "confirmed") === `Scale confirmed: 1" = 40 ft.`,
      "a sheet's own scale is shown where it is not the sheet shown and its review holds one; the status names what was set: "
      + words("d0p1", 40));
  }
  // critic 9, r-5: two tabs. A page holding a refusal takes in the job's newer good result (it posted the refusal over rows
  // that stood); a page holding a good result takes in the job's newer refusal (it posted its volumes as fresh beside the
  // limit's rows); the same result, or none stored, is not asked for again
  const rtRun = async (r0, t) => {
    const asked = [], set = [];
    const st = { pid: "p", result: r0, takeoff: null, takeoffFailed: "" };
    const GETf = async (url) => { asked.push(url); return /\/takeoff$/.test(url) ? t : t._stored; };
    const fn = new Function("state", "GET", "takeoffKey", "staleFromServer", "setResult", "renderResult", "renderTakeoff", "renderLimit",
      "postToHost", "hostSummary", refresh + "\nreturn refreshTakeoff;")(st, GETf, () => "k", () => {}, (r) => { set.push(r); st.result = r; },
      () => {}, () => {}, () => {}, () => {}, (r) => r);
    await fn();
    return { asked, set };
  };
  const GOOD = { totals: {}, computed_at: 222 };
  const P1 = await rtRun({ error: "refused" }, { rows: [], result_at: 222, result_refused: false, _stored: GOOD });
  const P2 = await rtRun({ totals: {}, computed_at: 111 }, { rows: [], result_at: null, result_refused: true, _stored: { error: "refused", warnings: [] } });
  const P3 = await rtRun({ totals: {}, computed_at: 222 }, { rows: [], result_at: 222, result_refused: false, _stored: GOOD });
  const P4 = await rtRun({ totals: {}, computed_at: 111 }, { rows: [], result_at: null, result_refused: false, _stored: { error: "not computed" } });
  check(P1.set.length === 1 && P1.set[0].computed_at === 222 && P2.set.length === 1 && P2.set[0].error === "refused"
    && P3.asked.length === 1 && !P3.set.length && P4.asked.length === 1 && !P4.set.length,
    "r-5: a page holding a refusal takes in the job's newer good result; a page holding a good result takes in the job's "
    + "newer refusal; the same result, or none stored, is not asked for: " + JSON.stringify([P1.asked.length, P2.asked.length, P3.asked.length, P4.asked.length]));
  // ---- 3. the page's own measures: the offers, the Takeoff card, the hover label ------------------------------------
  const offer = fnSrc(ejs, "renderLimitOffer");
  check(/const unmeasured = c\.erosion\.some\(\(f\) => f\.length_ft == null\);/.test(offer) && /length_pt/.test(offer)
    && /if \(c\.scale_refused\) box\.append/.test(offer) && /No length is measured here: \$\{c\.scale_refused\}/.test(offer),
    "the offers never add unmeasured lengths up as '0 ft' and say why no length is measured");
  check(/No rows: the scale in force is not checked for the sheet shown/.test(fnSrc(ejs, "renderTakeoff")),
    "the Takeoff card leads with the refusal, not 'pick or draw the limit', when the scale is not checked there");
  check(!/\* 50 \/ 72/.test(ejs) && /const fpo = scaleFtPerPt\(\);/.test(ejs),
    "the off-screen hover label uses the sheet's checked scale (it was a fixed 1\" = 50 ft)");
  check(/const zf = \(z\) => scaleFtPerPt\(z\.page/.test(fnSrc(ejs, "renderZones")) && /noScaleWords\(key\)/.test(fnSrc(ejs, "renderPads"))
    && /const fppL = scaleFtPerPt\(dk \|\| state\.key\);/.test(fnSrc(ejs, "renderLimit")),
    "a zone's area on its own sheet, a pad's area and a picked limit's gaps use the scale checked for their sheet");
  check(/const refused = scaleRefused\(state\.key\);/.test(fnSrc(ejs, "renderScale")) && /'not checked for this sheet'/.test(fnSrc(ejs, "renderScale")),
    "the Scale card says the scale in force is not checked for the sheet on screen, and why");

  // ---- 4. the host's bar and status say why the rows left; an empty row list files nothing -----------------------------
  const CF_RE = (js.match(/const CF_ROW_RE = [^\n]+/) || [""])[0];
  const barSrc = [CF_RE, "cfIsRow", "cfAdded", "cfRowsHere", "cfRowsElsewhere", "cfRows", "cfScaleTxt",
    "cfScaleNote", "cfSentence", "cfRenderBar"].map((n, i) => (i === 0 ? n : fnSrc(js, n))).join("\n");
  const bels = { cfSum: { textContent: "" }, cfNote: { textContent: "", title: "" }, cfAdd: { textContent: "", title: "", disabled: false } };
  const stale = { cut_cy: 0, fill_cy: 900, net_cy: 900, area_sf: 24306, scale_ft_per_in: 50, scale_source: "read in review",
    warnings: [], questions: [], stale: true, stale_why: "the scale changed since this compute (1\" = 50 ft then, 40 ft now)" };
  new Function("$", "state", "cfState", "CF_ADD_TITLE", barSrc + "\ncfRenderBar();")((id) => bels[id], { extraItems: [], result: {}, page: 0 },
    { summary: stale, rowsPage: 0, hint: "", notice: "opened with 2 sheets" }, "t");
  check(bels.cfNote.textContent.startsWith('STALE: the scale changed since this compute (1" = 50 ft then, 40 ft now). · opened with 2 sheets · ')
    && /STALE/.test(bels.cfSum.textContent) && bels.cfAdd.disabled && /left this page's takeoff \(the scale changed since this compute/.test(bels.cfAdd.title),
    "the bar leads with why the volumes are stale, and the button's tooltip says it too (critic 8, MINOR-5): " + bels.cfNote.textContent);
  const cfRows = new Function(CF_RE + "\n" + fnSrc(js, "cfRows") + "\nreturn cfRows;")();
  check(cfRows({ cut_cy: 0, fill_cy: 900, rows: [] }).length === 0 && cfRows({ cut_cy: 0, fill_cy: 900 }).length === 2,
    "an empty row list files no rows; the four rows are built only when a summary sends none at all (critic 8, r-1)");
  // the listener's words when the rows leave: a refusal, a stale result with its why, else "an input changed"
  const li = js.indexOf('if (e.data.type !== "result" || e.data.pid !== cfState.pid) return;');
  const lsrc = js.slice(js.lastIndexOf('window.addEventListener("message", (e) => {', li), js.indexOf("\n});", li) + 4);
  const said = [];
  const words = ["cfSentence", "cfLeftWords", "cfLeftReason", "cfLeftNote", "cfRowsLeft"].map((n) => fnSrc(js, n)).join("\n") + "\n";
  const run = (summary) => {
    const lst = { fn: null };
    new Function("window", "location", "state", "cfState", "cfRowsHere", "cfAdded", "cfApplyItems", "cfDropItems", "cfRows", "setStatus", "cfRenderBar",
      "cfRowsElsewhere", words + lsrc)({ addEventListener: (t, fn) => { lst.fn = fn; } }, { origin: "http://x" }, { job: "A", result: {} }, { job: "A", pid: "job_A", summary: null },
      () => true, () => true, () => said.push("apply"), () => said.push("drop"), () => [], (m) => said.push(m), () => {}, () => false);
    lst.fn({ origin: "http://x", data: { source: "cutfill", type: "result", pid: "job_A", result: summary } });
  };
  run(stale);
  run({ error: "The scale 1\" = 50 ft was read on sheet 3.1, but ..." });
  // critic 10, r-4: rows on a page this tab thinks is not the job's rows page leave only when the engine agrees - another
  // tab may have moved them here, and this tab's old record took the job's only rows off their page
  const runElse = async (answer, result) => {
    const out = { said: [], drops: 0, applied: 0 };
    const lst = { fn: null };
    const st = { job: "A", page: 2, gen: 5, result: {}, extraItems: [{ label: "Site Fill", qty: 900, unit: "CY" }] };
    const cfs = { job: "A", pid: "job_A", summary: null, rowsPage: 0 };
    new Function("window", "location", "state", "cfState", "cfRowsHere", "cfAdded", "cfApplyItems", "cfDropItems", "cfRows", "setStatus", "cfRenderBar",
      "cfRowsElsewhere", "cfEngineState", words + lsrc)({ addEventListener: (t, fn) => { lst.fn = fn; } }, { origin: "http://x" }, st, cfs,
      () => cfs.rowsPage === st.page || st.extraItems.length > 0, () => st.extraItems.length > 0, () => { out.applied++; },
      () => { out.drops++; st.extraItems = []; }, (s) => (s && s.rows) || [{ label: "Site Fill", qty: 1 }], (m) => out.said.push(m),
      () => {}, () => cfs.rowsPage !== st.page, async () => answer);
    lst.fn({ origin: "http://x", data: { source: "cutfill", type: "result", pid: "job_A", result: result || { cut_cy: 1, fill_cy: 900 } } });
    await new Promise((r) => setTimeout(r, 0));
    out.rowsPage = cfs.rowsPage; out.left = st.cfLeft;
    return out;
  };
  const e1 = await runElse({ project: true, rows_page: 2 });
  const e2 = await runElse({ project: true, rows_page: 0 });
  const e3 = await runElse({ project: true, rows_page: 2 }, { cut_cy: 1, fill_cy: 450, stale: true, stale_why: "the limit changed since this compute" });
  check(!e1.drops && e1.rowsPage === 2 && e1.applied === 1 && !e1.left && e2.drops === 1 && /page 1's takeoff/.test(e2.said[0] || "")
    && e2.left && /page 1's takeoff/.test(e2.left.reason) && e3.drops === 1 && /the limit changed/.test(e3.said[0] || ""),
    "critic 10, r-4 / critic 11, r-3: rows this tab thinks are on the wrong page leave only when the engine says so; when "
    + "the engine says they are here, this tab's record follows AND the post is followed (a new result applied, a stale "
    + "one taken out - it was dropped): " + JSON.stringify([e1.applied, e1.rowsPage, e2.said[0], e3.said[0]]));
  // round 16 (the review wins): the cut / fill's Scale card set a page's review scale - the host forgets its own memory of
  // that page's scale (it would go back at the next Approve and undo it), and the chip follows when the page is on screen;
  // another plan set, another cut / fill, another origin, or a number that is none, change nothing
  const scaleRun = (data, o = {}) => {
    const out = { removed: [], set: [], repaint: 0, restores: 0 };
    const lst = { fn: null };
    const st = { job: "A", page: o.page == null ? 0 : o.page, docName: "plan.pdf", result: { scale: { ft_per_paper_inch: 50, text: "1\" = 50'", agrees: true } } };
    const storeF = { removeItem: (k) => out.removed.push(k), setItem: (k, v) => out.set.push([k, v]) };
    // fix/scale-deaf-tab: the listener also asks the review record again (scaleRestore: its stamp and its number)
    new Function("window", "location", "state", "cfState", "store", "scaleTypedObj", "scaleTypedKey", "scaleRepaint", "scaleRestore", words + lsrc)(
      { addEventListener: (t, fn) => { lst.fn = fn; } }, { origin: "http://x" }, st, { job: o.cfJob || "A", pid: "job_A", summary: null }, storeF,
      new Function(fnSrc(js, "scaleTypedObj") + "\nreturn scaleTypedObj;")(),
      () => "scaletyped::" + st.docName + "::p" + st.page, () => { out.repaint++; }, async () => { out.restores++; });
    lst.fn({ origin: o.origin || "http://x", data: Object.assign({ source: "cutfill", type: "scale", pid: "job_A", job: "A", page: 0, ft: 40, text: "1\" = 40'" }, data) });
    out.scale = st.result.scale;
    return out;
  };
  const sOn = scaleRun({});
  const sOff = scaleRun({ page: 1 });
  const sNone = [scaleRun({ job: "B" }), scaleRun({ pid: "job_B" }), scaleRun({}, { cfJob: "B" }), scaleRun({ page: "0" }), scaleRun({ ft: 0 }),
    scaleRun({}, { origin: "http://evil" })];
  check(sOn.scale.ft_per_paper_inch === 40 && sOn.scale.text === "1\" = 40'" && sOn.scale.agrees === null && sOn.repaint === 1
    && JSON.stringify(sOn.removed) === JSON.stringify(["scaletyped::plan.pdf::p0", "scale::plan.pdf::p0"])
    && sOn.set.length === 1 && sOn.set[0][0] === "scaletyped::plan.pdf::p0" && JSON.parse(sOn.set[0][1]).ft === 40
    && JSON.stringify(sOff.removed) === JSON.stringify(["scaletyped::plan.pdf::p1", "scale::plan.pdf::p1"]) && !sOff.set.length && !sOff.repaint
    && sOff.scale.ft_per_paper_inch === 50 && sOn.restores === 1 && !sOff.restores
    && sNone.every((r) => !r.removed.length && !r.set.length && !r.repaint && !r.restores && r.scale.ft_per_paper_inch === 50),
    "the host hears a scale set in the cut / fill's Scale card: its memory of that page goes, the chip follows on screen; "
    + "another set, cut / fill or origin, or no number, changes nothing: " + JSON.stringify([sOn.removed, sOff.removed]));
  // critic 20, MINOR-3 / critic 21: this page's approval out of date is said on the page's lasting note (only the downloads
  // said it); another page's or an older load's answer, or the cut / fill rows' own note, leave it
  {
    const outRun = async (o) => {
      const els = {};
      const parent = { insertBefore(seg) { els.sbCf = seg; } };
      const st = { job: "A", page: o.page == null ? 0 : o.page, gen: 3, cfLeft: o.cfLeft || null };
      const run = new Function("state", "$", "document", "fetch", fnSrc(js, "approvalOutdated") + "\nreturn approvalOutdated;")(
        st, (id) => (id === "status" ? { parentNode: parent } : els[id] || null),
        { createElement: () => ({ id: "", className: "", textContent: "", title: "" }) },
        async () => ({ ok: true, json: async () => ({ pages: [0, 1], outdated: { "0": "OUT OF DATE: page 1 was approved at 1\" = 40 ft" } }) }));
      await run(o.gen == null ? 3 : o.gen);
      return { text: els.sbCf ? els.sbCf.textContent : "", note: st.outdatedNote };
    };
    const oOn = await outRun({}), oOther = await outRun({ page: 1 }), oOld = await outRun({ gen: 2 }), oCf = await outRun({ cfLeft: { reason: "x" } });
    check(/^OUT OF DATE: page 1 was approved at 1" = 40 ft · $/.test(oOn.text) && oOn.note === oOn.text
      && !oOther.text && !oOld.text && !oCf.text,
      "this page's approval out of date is said on its lasting note; another page, an older load or the rows' own note leave "
      + "it: " + JSON.stringify([oOn.text, oOther.text, oOld.text, oCf.text]));
    check(/if \(state\.outdatedNote && \$\("sbCf"\) && \$\("sbCf"\)\.textContent === state\.outdatedNote\)/.test(js)
      && /approvalOutdated\(gen\)\.catch/.test(fnSrc(js, "loadResult")),
      "the note is asked on every page load and goes with a new approval");
    // critic 22, RISK-1: an approve asks again (one that fixed nothing keeps the note), the rows' note going asks again,
    // an answer that this page's approval stands clears our note, and only the newest answer counts
    {
      const els = { sbCf: { textContent: "OUT OF DATE: x · ", title: "x" } };
      const st = { job: "A", page: 0, gen: 3, cfLeft: null, outdatedNote: "OUT OF DATE: x · ", outdatedSeq: 0 };
      let answer = { pages: [0], outdated: {} };
      const run = new Function("state", "$", "document", "fetch", fnSrc(js, "approvalOutdated") + "\nreturn approvalOutdated;")(
        st, (id) => (id === "status" ? { parentNode: { insertBefore() {} } } : els[id] || null),
        { createElement: () => ({}) }, async () => ({ ok: true, json: async () => answer }));
      await run(3);
      const cleared = els.sbCf.textContent === "" && st.outdatedNote === "";
      // two asks in flight: the older answer, arriving last, is not shown
      let release;
      const slow = new Promise((r) => { release = r; });
      const run2 = new Function("state", "$", "document", "fetch", fnSrc(js, "approvalOutdated") + "\nreturn approvalOutdated;")(
        st, (id) => (id === "status" ? { parentNode: { insertBefore() {} } } : els[id] || null),
        { createElement: () => ({ id: "", className: "", textContent: "", title: "" }) },
        async () => { const a = answer; await slow; return { ok: true, json: async () => a }; });
      answer = { pages: [0], outdated: { "0": "OUT OF DATE: old" } };
      const p1 = run2(3);                            // asked first (answers "out of date")
      answer = { pages: [0], outdated: {} };
      const p2 = run2(3);                            // asked second (answers "stands")
      release(); await p1; await p2;
      const newest = els.sbCf.textContent === "";
      check(cleared && newest && /approvalOutdated\(state\.gen\)\.catch/.test(js.slice(js.indexOf("const j = await r.json();", js.indexOf("approveNotesDropped") - 3000)))
        && /if \(!reason && typeof approvalOutdated === "function" && state\.job\) approvalOutdated\(state\.gen\)/.test(fnSrc(js, "cfLeftNote")),
        "critic 22, RISK-1: an approve and the rows' note going ask again; a standing approval clears the note; only the "
        + "newest answer counts: " + JSON.stringify([cleared, newest]));
    }
  }
  // critic 11, m4: with no page on record a Compute refiles nothing - the words say Add as well
  const lw = new Function(words + "\nreturn cfLeftWords;")();
  check(/Compute, then Add to takeoff, puts them back/.test(lw({ stale: true, stale_why: "x" }, null))
    && /\. Compute puts them back$/.test(lw({ stale: true, stale_why: "x" }, 0)),
    "critic 11, m4: the words say how the rows come back - Compute, or Compute then Add where no page is on record");
  check(said[0] === "drop" && /left the takeoff: the scale changed since this compute \(1" = 50 ft then, 40 ft now\)\. Compute puts them back/.test(said[1])
    && said[2] === "drop" && /the engine refused the compute \(the panel says why\)/.test(said[3]),
    "the status says why the rows left: the stale why, or the refusal (both said 'an input changed'): " + JSON.stringify(said));

  // ---- 5. back on the page that holds the rows: they follow the engine's word --------------------------------------------
  // (a) what the engine's page said while another page was on screen (critic 7's WRONG-2 / WRONG-3, one page over);
  // (b) the engine itself, asked by route: a correction in review reached the engine and never the takeoff (critic 8,
  // WRONG-1), and rows from an older compute follow the engine's last one
  const recSrc = [CF_RE, "cfIsRow", "cfAdded", "cfRowsHere", "cfRowsElsewhere", "cfRows", "cfClearItems", "cfApplyItems", "cfDropItems", "cfCompact", "cfSentence",
    "cfLeftWords", "cfLeftReason", "cfLeftNote", "cfRowsLeft", "cfBackWords", "cfReconcile", "cfEngineState"].map((n, i) => (i === 0 ? n : fnSrc(js, n))).join("\n");
  const rec = async (summary, items, opts = {}) => {
    const out = { said: [], asked: [], st: { job: "A", page: 1, gen: 7, result: {}, extraItems: items.map((x) => Object.assign({}, x)),
      cfFacts: opts.facts || null, calcSaved: null } };
    const cf = { job: opts.job || "A", pid: "job_A", summary, rowsPage: 1 };
    const fetchFake = async (url) => {
      out.asked.push(url);
      const body = /rows=1/.test(url) ? (opts.full || { project: true, why: null })
        : (opts.engine || { project: true, why: null, computed_at: 2000, rows_page: null });
      return { ok: true, json: async () => body };
    };
    const fn = new Function("state", "cfState", "setStatus", "renderItemized", "calcSave", "calcStateDirty", "store", "tradeKey", "fetch",
      recSrc + "\nreturn cfReconcile;")(out.st, cf, (m) => out.said.push(m), () => {}, () => {}, () => {},
      { getItem: () => "x", setSession: () => {} }, (l) => l, fetchFake);
    await fn();
    return out;
  };
  const filed = [{ label: "Site Fill", qty: 900, unit: "CY" }, { label: "Import Structural", qty: 900, unit: "CY", kind: "net" }, { label: "Paving", qty: 3, unit: "EA" }];
  const r1 = await rec(stale, filed);
  check(!r1.st.extraItems.some((it) => /Site Fill|Import Structural/.test(it.label)) && r1.st.extraItems.some((it) => it.label === "Paving")
    && /left the takeoff: the scale changed since this compute/.test(r1.said[0] || ""),
    "a stale result heard on another page takes this page's cut / fill rows out when it comes back, and says why: " + r1.said[0]);
  const freshS = { cut_cy: 0, fill_cy: 576, net_cy: 576, area_sf: 15556, stale: false, warnings: [], computed_at: 2000,
    rows: [{ label: "Site Fill", qty: 576, unit: "CY" }, { label: "Import Structural", qty: 576, unit: "CY" }] };
  const r2 = await rec(freshS, filed);
  check(r2.st.extraItems.some((it) => it.label === "Site Fill" && it.qty === 576) && /follow the volumes computed while another page/.test(r2.said[0] || "")
    && r2.st.cfFacts && r2.st.cfFacts.computed_at === 2000,
    "a fresh result computed meanwhile updates them, and the rows keep its compute's time: " + JSON.stringify(r2.st.extraItems.map((it) => [it.label, it.qty])));
  const at576 = [{ label: "Site Fill", qty: 576, unit: "CY" }, { label: "Import Structural", qty: 576, unit: "CY", kind: "net" }];
  const r3 = await rec(freshS, at576, { engine: { project: true, why: null, computed_at: 2000 }, facts: { computed_at: 2000 } });
  const r4 = await rec(stale, filed, { job: "B" });
  check(!r3.said.length && r3.asked.length === 1 && !r4.said.length && r4.st.extraItems.length === 3
    && r4.asked.every((u) => /\/api\/job\/A\//.test(u)),
    "the same rows at the engine's last compute are left alone, and another plan set's word touches nothing (the page's own job is asked)");
  // the engine itself says the rows do not stand - the frame's last word was fresh, or there is none (after a reload)
  const EW = "the scale 1\" = 50 ft was handed over from sheet 3.0's review, which now reads 1\" = 40 ft (a correction or a new detection made since)";
  const r5 = await rec(freshS, at576, { engine: { project: true, why: EW, computed_at: 2000 }, facts: { computed_at: 2000 } });
  const r6 = await rec(null, filed, { engine: { project: true, why: EW, computed_at: 2000 } });
  check(!r5.st.extraItems.some((it) => /Site Fill/.test(it.label)) && /left the takeoff: the scale 1" = 50 ft was handed over from sheet 3\.0's review/.test(r5.said[0] || "")
    && !r6.st.extraItems.some((it) => /Site Fill/.test(it.label)) && r6.st.extraItems.some((it) => it.label === "Paving") && r6.said.length === 1,
    "the engine asked by route takes out rows it does not stand behind - with the frame's fresh word in hand, and after a reload with none (critic 8, WRONG-1): " + r6.said[0]);
  // rows from an older compute: the engine's rows now come in
  const FULL = { project: true, why: null, summary: Object.assign({}, freshS, { computed_at: 3000, rows: [{ label: "Site Fill", qty: 610, unit: "CY" }] }) };
  const r7 = await rec(null, filed, { engine: { project: true, why: null, computed_at: 3000 }, facts: { computed_at: 2000 }, full: FULL });
  check(r7.st.extraItems.some((it) => it.label === "Site Fill" && it.qty === 610) && !r7.st.extraItems.some((it) => it.label === "Import Structural")
    && /follow the engine's last compute/.test(r7.said[0] || "") && r7.asked.some((u) => /rows=1/.test(u)) && r7.st.cfFacts.computed_at === 3000,
    "rows filed from an older compute follow the engine's last one, asked by route: " + JSON.stringify(r7.st.extraItems.map((it) => [it.label, it.qty])));
  // critic 9: the engine's rows (unmarked here: a row the page marks as the old Earthwork calculator's is kept instead -
  // tests/test_legacy_earthwork_ui.js) with no cut / fill behind them (r-7: a reset swept it), rows on a
  // page that does not hold the job's rows (WRONG-3), and volumes with no compute behind them (r-7: opened again after a
  // reset, never computed) leave, said - they stayed silently until an approve; an area row named like the engine's is
  // the operator's and stays, and nothing is asked for it (m3)
  const r8 = await rec(null, filed, { engine: { project: false } });
  const r9 = await rec(null, filed, { engine: { project: true, why: null, computed_at: 2000, rows_page: 0 } });
  const LIMIT = { project: true, why: null, summary: { no_volumes: true, computed_at: null,
    rows: [{ label: "Silt / Construction Fence", qty: 638.9, unit: "LF" }] } };
  const r10 = await rec(null, filed, { engine: { project: true, why: null, computed_at: null, rows_page: 1 }, full: LIMIT });
  const r11 = await rec(stale, [{ label: "Clear & Grub", qty: 5000, unit: "SF", _area: true }, { label: "Paving", qty: 3, unit: "EA" }],
    { engine: { project: false } });
  check(!r8.st.extraItems.some((it) => /Site Fill/.test(it.label)) && r8.st.extraItems.some((it) => it.label === "Paving")
    && /no cut \/ fill behind them/.test(r8.said[0] || "")
    && !r9.st.extraItems.some((it) => /Site Fill/.test(it.label)) && /the job's rows are in page 1's takeoff/.test(r9.said[0] || "")
    && !r10.st.extraItems.some((it) => /Site Fill|Import Structural/.test(it.label)) && r10.st.extraItems.some((it) => /Silt/.test(it.label))
    && /the engine holds no compute for them/.test(r10.said[0] || "")
    && r11.st.extraItems.some((it) => it._area) && !r11.said.length && r11.asked.length === 0,
    "critic 9: rows with no cut / fill behind them, rows on another page than the job's, and volumes with no compute leave, "
    + "said; an area row named 'Clear & Grub' is the operator's: " + JSON.stringify([r8.said[0], r9.said[0], r10.said[0]]));
  // the reason lasts (the status bar's lasting side): the page load's own words overwrote the transient line, and the
  // rows left silently (the live check of critic 9's round); rows that come back take the note away
  check(r1.st.cfLeft && /the scale changed since this compute/.test(r1.st.cfLeft.reason)
    && /no cut \/ fill behind them/.test((r8.st.cfLeft || {}).reason || "") && /page 1's takeoff/.test((r9.st.cfLeft || {}).reason || "")
    && /no compute for the volumes/.test((r10.st.cfLeft || {}).reason || "") && /the scale 1" = 50 ft was handed over/.test((r6.st.cfLeft || {}).reason || "")
    && r2.st.cfLeft === null && r7.st.cfLeft === null && /left the takeoff/.test((r1.st.cfLeft || {}).words || ""),
    "the reason the rows left is kept for the status bar's lasting side, the whole sentence with it; rows that come back clear it: "
    + JSON.stringify([r1.st.cfLeft, r8.st.cfLeft && r8.st.cfLeft.reason, r10.st.cfLeft && r10.st.cfLeft.reason]));
  check(/if \(typeof cfReconcile === "function"\) cfReconcile\(\)\.catch\(\(\) => \{\}\);/.test(fnSrc(js, "loadResult"))
    && /state\.cfFacts = null;/.test(fnSrc(js, "pageReset")),
    "every page load ends with the reconcile, after its takeoff is back; the rows' facts are the page's own");
  // ---- 6. the approve: nothing is exported with rows the engine does not stand behind ----------------------------------
  const ap = js.slice(js.indexOf('async function approveRun() {'), js.indexOf('async function approveRun() {') + 30000);
  check(/e\.detail\.cutfill/.test(ap) && /cfEngineState\(job0, true\)/.test(ap) && /nothing exported: the cut \/ fill rows/.test(ap)
    && /const job0 = state\.job, page0 = state\.page, gen0 = state\.gen;/.test(ap),
    "a refused approve (409, the engine does not stand behind the rows) brings the engine's rows in or takes them out, and says nothing was exported");
  // the branch itself, run (critic 9, WRONG-3): rows on a page that does not hold the job's rows are not replaced, they
  // leave; a page switch while the engine is asked files nothing on the page then on screen; with no page recorded, the
  // engine's rows come in on the page approved and that page is recorded
  const bStart = ap.indexOf('if (e && e.detail && typeof e.detail === "object" && e.detail.cutfill) {');
  let bEnd = -1;
  for (let j = ap.indexOf("{", bStart), d = 0; j < ap.length; j++) {
    if (ap[j] === "{") d++;
    else if (ap[j] === "}" && --d === 0) { bEnd = j + 1; break; }
  }
  const branch = ap.slice(bStart, bEnd);
  const run409 = async (full, o = {}) => {
    const out = { said: [], applied: [], dropped: 0, posted: [] };
    const st = { job: "A", page: o.page0 == null ? 1 : o.page0, gen: 3, extraItems: [{ label: "Site Fill", qty: 900, unit: "CY" }] };
    const cfs = { job: "A", pid: "job_A", rowsPage: null };
    const f = new Function("e", "job0", "page0", "gen0", "state", "cfState", "cfEngineState", "cfApplyItems", "cfAdded", "cfDropItems", "cfRows",
      "cfSentence", "setStatus", "fetch", "cfLeftNote", "cfBackWords", "cfBackThenApprove", "return (async () => { " + branch + " })();");
    await f({ detail: { cutfill: "they are not the engine's numbers now" } }, "A", st.page, 3, st, cfs,
      async () => { if (o.turn) st.page = 7; return full; }, (s) => out.applied.push(s), () => st.extraItems.length > 0,
      () => { out.dropped++; st.extraItems = []; }, (s) => s.rows || [], (t) => String(t), (m) => out.said.push(m),
      async (url, init) => { out.posted.push([url, JSON.parse(init.body)]); return { ok: true }; }, (r) => { out.note = r; },
      (rp) => (Number.isInteger(rp) ? "Compute puts them back" : "Compute, then Add, puts them back"),
      (rp) => "Compute, then approve again.");
    out.rowsPage = cfs.rowsPage;
    return out;
  };
  // critic 11, r-2: another page's approved export still carries the job's rows - the refusal names it and the rows
  // stay on this page (the page they belong on): nothing dropped, nothing re-filed, the engine not asked
  const xStart = ap.indexOf('if (e && e.detail && typeof e.detail === "object" && e.detail.cutfill && Number.isInteger(e.detail.export_page)) {');
  let xEnd = -1;
  for (let j = ap.indexOf("{", xStart), d = 0; j < ap.length; j++) {
    if (ap[j] === "{") d++;
    else if (ap[j] === "}" && --d === 0) { xEnd = j + 1; break; }
  }
  const xOut = { said: [], asked: 0, dropped: 0 };
  await new Function("e", "setStatus", "cfSentence", "cfEngineState", "cfDropItems", "return (async () => { " + ap.slice(xStart, xEnd) + " })();")(
    { detail: { cutfill: "page 1's approved takeoff still carries the job's cut / fill rows: approve page 1 again without them first", export_page: 0 } },
    (m) => xOut.said.push(m), (t) => String(t), async () => { xOut.asked++; return null; }, () => { xOut.dropped++; });
  check(xStart > 0 && /nothing exported: page 1's approved takeoff still carries/.test(xOut.said[0] || "") && !xOut.asked && !xOut.dropped,
    "critic 11, r-2: the approve refused for another page's export keeps the rows here and says which page to approve again: " + xOut.said[0]);
  const SUM = { rows: [{ label: "Site Fill", qty: 610, unit: "CY" }] };
  const a409 = await run409({ project: true, why: null, rows_page: 0, summary: SUM });
  const b409 = await run409({ project: true, why: null, rows_page: null, summary: SUM }, { turn: true });
  const c409 = await run409({ project: true, why: null, rows_page: null, summary: SUM }, { page0: 2 });
  check(branch.length > 200 && !a409.applied.length && a409.dropped === 1 && /the job's rows are in page 1's takeoff/.test(a409.said[0] || "")
    && !b409.applied.length && !b409.dropped && /nothing exported from page 2/.test(b409.said[0] || "")
    && c409.applied.length === 1 && c409.rowsPage === 2 && c409.posted.length === 1 && /\/page\/2\/cutfill\/rows$/.test(c409.posted[0][0])
    && c409.posted[0][1].added === true && /page 1's takeoff/.test(a409.note || "") && c409.note === undefined,
    "WRONG-3: the approve's refusal files no second copy - rows on another page than the job's leave, a page switch "
    + "meanwhile files nothing, with no page recorded the engine's rows come in on the page approved, recorded: "
    + JSON.stringify([a409.said[0], b409.said[0], c409.posted.map((p) => p[0])]));

  console.log(fails ? "\n" + fails + " failure(s)" : "\nall ok");
  process.exit(fails ? 1 : 0);
})();
