// The MY PROJECTS landing page's pure presentation logic, pinned.
//
// Only the block between the PROJECTS-PURE markers is evaluated (app.js is
// not a module and touches the DOM at load) - same technique as
// test_trench_math.js. Structure checks at the bottom pin the view's
// contract with index.html: the view exists inside <main>, the brand block
// carries the Projects link, and the workbench columns it hides are the
// three the CSS names.
//
// Dependency-free:  node tests/test_projects_view.js

"use strict";
const fs = require("fs");
const path = require("path");

const appSrc = fs.readFileSync(
  path.join(__dirname, "..", "webui", "app.js"), "utf8");
const htmlSrc = fs.readFileSync(
  path.join(__dirname, "..", "webui", "index.html"), "utf8");
const cssSrc = fs.readFileSync(
  path.join(__dirname, "..", "webui", "style.css"), "utf8");

let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); }
  else console.log("ok:", msg);
}

const a = appSrc.indexOf("// PROJECTS-PURE-BEGIN");
const b = appSrc.indexOf("// PROJECTS-PURE-END");
check(a >= 0 && b > a, "PROJECTS-PURE markers present in webui/app.js");
const P = new Function(appSrc.slice(a, b) +
  " return { projMetaLine, projAgo, projShowsSharedBadge, projCountLine,"
  + " projMatches, projFilter, projSort, projReachLine, PROJ_SORTS,"
  + " projIsDemo, projTabRows, projTabNoun, projDefaultTab, projTabCount,"
  + " projEmptyMine, projEmptyDemo, PROJ_TABS,"
  + " projDemoAvailable, projDemoMeta, projDemoWhy, PROJ_DEMO_RULE };")();

// ---- the card's meta line -----------------------------------------------
check(P.projMetaLine(12, 5, 0) === "12 pages · 5 detected",
  "meta: partial detection counts");
check(P.projMetaLine(12, 12, 0) === "12 pages · all detected",
  "meta: full detection says so");
check(P.projMetaLine(12, 14, 0) === "12 pages · all detected",
  "meta: over-count (stale extra results) still reads all detected");
check(P.projMetaLine(3, 0, 0) === "3 pages · not detected",
  "meta: zero detection says not detected");
check(P.projMetaLine(1, 1, 0) === "1 page · all detected",
  "meta: singular page");
check(P.projMetaLine(48, 12, 3) === "48 pages · 12 detected · 3 approved",
  "meta: approved pages appear when present");
check(P.projMetaLine(48, 12, 0) === "48 pages · 12 detected",
  "meta: zero approved stays silent");
check(P.projMetaLine(0, 0, 0) === "0 pages · not detected",
  "meta: a zero-page row cannot claim all detected");

// ---- relative time -------------------------------------------------------
const NOW = 1_700_000_000;
check(P.projAgo(NOW, NOW - 30) === "just now", "ago: <90s is just now");
check(P.projAgo(NOW, NOW - 600) === "10 min ago", "ago: minutes");
check(P.projAgo(NOW, NOW - 7200) === "2 h ago", "ago: hours");
check(P.projAgo(NOW, NOW - 86400 * 3) === "3 d ago", "ago: days");
check(/^\d{4}-\d{2}-\d{2}$/.test(P.projAgo(NOW, NOW - 86400 * 90)),
  "ago: old sets show a date, not a huge day count");
check(P.projAgo(NOW, NOW + 500) === "just now",
  "ago: clock skew never shows a negative age");

// ---- shared badge rule -----------------------------------------------------
check(P.projShowsSharedBadge(
  [{ mine: true, shared: false }, { mine: false, shared: true }]) === true,
  "badge: mixed list labels the shared rows");
check(P.projShowsSharedBadge(
  [{ mine: false, shared: true }, { mine: false, shared: true }]) === false,
  "badge: all-shared list (single-password box) shows no labels");
check(P.projShowsSharedBadge(
  [{ mine: true, shared: false }]) === false,
  "badge: all-mine list shows no labels");
check(P.projShowsSharedBadge([]) === false, "badge: empty list");

// ---- count line: the cap is never silent ------------------------------------
check(P.projCountLine(0, 0) === "", "count: empty list says nothing");
check(P.projCountLine(7, 7) === "7", "count: uncapped shows the count");
check(P.projCountLine(200, 320) === "showing 200 of 320",
  "count: a capped list SAYS it is capped");

// ---- search: matching --------------------------------------------------------
{
  const r = { name: "Oncor Cloudcrest Substation Civils - 20251212.pdf" };
  check(P.projMatches(r, "") === true, "search: empty query matches everything");
  check(P.projMatches(r, "   ") === true, "search: blank query matches everything");
  check(P.projMatches(r, "oncor") === true, "search: case-insensitive");
  check(P.projMatches(r, "CLOUDCREST") === true, "search: upper-case query");
  check(P.projMatches(r, "crest") === true, "search: mid-word substring");
  check(P.projMatches(r, "oncor civils") === true,
    "search: every word must appear, order-independent");
  check(P.projMatches(r, "oncor barn") === false,
    "search: one missing word is no match");
  check(P.projMatches({ name: null }, "x") === false,
    "search: a nameless row never matches a real query");
  check(P.projFilter([r, { name: "stice.pdf" }], "stice").length === 1,
    "filter: keeps only matching rows");
  check(P.projFilter(null, "x").length === 0, "filter: no rows in, none out");
}

// ---- sort: every mode is total and deterministic -----------------------------
{
  const rows = [
    { name: "Oncor", when: 100, pages: 9, detected: 2 },
    { name: "belle", when: 300, pages: 3, detected: 2 },
    { name: "Kayden", when: 200, pages: 48, detected: 1 },
  ];
  check(P.projSort(rows, "when").map(r => r.when).join(",") === "300,200,100",
    "sort: last activity is newest first");
  check(P.projSort(rows, "name").map(r => r.name).join(",") === "belle,Kayden,Oncor",
    "sort: name is case-insensitive A-Z");
  check(P.projSort(rows, "pages").map(r => r.pages).join(",") === "48,9,3",
    "sort: pages is most first");
  check(P.projSort(rows, "detected").map(r => r.when).join(",") === "300,100,200",
    "sort: detected ties break on last activity, not input order");
  check(P.projSort(rows, "nonsense").map(r => r.when).join(",") === "300,200,100",
    "sort: an unknown mode falls back to last activity");
  check(P.projSort(rows, "when") !== rows, "sort: returns a copy, never in place");
  check(rows[0].name === "Oncor", "sort: the cached row order is untouched");
  check(P.PROJ_SORTS.length === 4, "sort: four modes offered");
}

// ---- the reach line: the search box may never overstate what it searched -----
// /api/projects replies with out[:200] plus the true total, so whenever
// total > loaded the browser is filtering a subset and MUST say so.
{
  check(P.projReachLine(0, 0, 0, "") === "",
    "reach: an empty server says nothing");
  const un = P.projReachLine(7, 7, 7, "");
  check(un.includes("All 7 plans") && un.includes("reach all of them"),
    "reach: uncapped list claims full reach");
  const cap = P.projReachLine(200, 200, 420, "");
  check(cap.includes("Newest 200 of 420") && cap.includes("not the other 220"),
    "reach: a capped list names the rows search CANNOT reach");
  const capQ = P.projReachLine(3, 200, 420, "onc");
  check(capQ.includes("3 of 200 match")
    && capQ.includes("searched the newest 200 of 420")
    && capQ.includes("may still be on the server"),
    "reach: a query over a capped list says a miss is not proof of absence");
  const unQ = P.projReachLine(1, 7, 7, "onc");
  check(unQ.includes("1 of 7 match") && unQ.includes("searched all 7 plans"),
    "reach: a query over an uncapped list claims full reach");
  check(P.projReachLine(1, 1, 1, "").includes("All 1 plan on this server"),
    "reach: singular plan reads 'plan', not 'plans'");
  check(P.projReachLine(5, 5, 5, "  ") === P.projReachLine(5, 5, 5, ""),
    "reach: a whitespace-only query is not a query");
}

// ---- the two tabs ------------------------------------------------------------
// The split is read off the SERVER's demo flag and never re-derived from the
// name here: a client's own upload that happens to be called
// C8.0-STORM-LAYOUT-Rev.1.pdf is theirs, and must not join the demo tab.
{
  const rows = [
    { name: "Oncor.pdf", demo: true, mine: false, shared: true },
    { name: "C4.0.pdf", demo: true, mine: false, shared: true },
    { name: "mine-a.pdf", demo: false, mine: true, shared: false },
    { name: "C8.0-STORM-LAYOUT-Rev.1.pdf", demo: false, mine: true,
      shared: false },
  ];
  // two again since 2026-09-09: the Open bids tab was removed at Koby's call
  // (core only accepts the CRM's hand-off; the listing lives in the CRM)
  check(P.PROJ_TABS.join(",") === "demo,mine",
        "tabs: exactly two, demo first");
  check(P.projTabRows(rows, "demo").length === 2, "tab: demo keeps demo rows");
  check(P.projTabRows(rows, "mine").length === 2,
    "tab: mine keeps everything that is not a demo");
  check(P.projTabRows(rows, "mine").some(
    r => r.name === "C8.0-STORM-LAYOUT-Rev.1.pdf"),
    "tab: a NON-demo row keeping a demo plan's filename stays on Your projects");
  check(P.projIsDemo({ demo: true }) === true
    && P.projIsDemo({ demo: false }) === false
    && P.projIsDemo({}) === false && P.projIsDemo(null) === false,
    "tab: demo-ness is the server's flag, absent reads false");
  check(P.projTabRows(null, "demo").length === 0, "tab: no rows in, none out");

  // which tab a visitor lands on
  check(P.projDefaultTab(rows) === "mine",
    "default tab: an account with its own work lands on Your projects");
  check(P.projDefaultTab(rows.filter(r => r.demo)) === "demo",
    "default tab: an account with nothing of its own lands on Demo plans");
  check(P.projDefaultTab([]) === "mine",
    "default tab: an empty server lands on Your projects (whose empty state "
    + "names the one action that works)");
  check(P.projDefaultTab(rows.filter(r => !r.demo)) === "mine",
    "default tab: no demos on this box -> Your projects");
}

// ---- the tab labels' counts --------------------------------------------------
check(P.projTabCount(0, 0) === "", "tab count: an empty tab shows no number");
check(P.projTabCount(8, 8) === "8", "tab count: uncapped shows the total");
check(P.projTabCount(200, 416) === "200 of 416",
  "tab count: a capped tab says so on the tab itself");

// ---- the reach line, per tab -------------------------------------------------
// The whole point of the tabs is that ONE grid shows two populations, so the
// sentence under the search box has to describe the population it is looking
// at. "All 8 plans on this server" on the Demo tab of a box holding 12 rows
// is false, and this line exists to not say false things.
{
  const D = P.projTabNoun("demo"), M = P.projTabNoun("mine");
  check(D.many === "demo plans" && M.many === "projects",
    "reach: each tab has its own noun");
  const d = P.projReachLine(8, 8, 8, "", D);
  check(d.includes("All 8 demo plans on this server")
    && !d.includes("All 8 plans"),
    "reach: the demo tab says 'demo plans', not 'plans'");
  const m = P.projReachLine(4, 4, 4, "", M);
  check(m.includes("All 4 projects on this server"),
    "reach: Your projects says 'projects'");
  const dq = P.projReachLine(2, 8, 8, "belle", D);
  check(dq.includes("2 of 8 match") && dq.includes("searched all 8 demo plans"),
    "reach: a query on the demo tab counts the demo population");
  const mc = P.projReachLine(200, 200, 416, "", M);
  check(mc.includes("Newest 200 of 416 projects")
    && mc.includes("not the other 216"),
    "reach: the cap disclosure survives the noun change");
  const mcq = P.projReachLine(3, 200, 416, "onc", M);
  check(mcq.includes("a project missing here may still be on the server"),
    "reach: the singular noun is used in the 'may still be there' clause");
  check(P.projReachLine(1, 1, 1, "", D).includes("All 1 demo plan on this"),
    "reach: singular demo plan reads 'plan'");
}
// ...and the pre-tab wording is untouched when no noun is passed, so nothing
// that calls this with four arguments changed behaviour.
check(P.projReachLine(7, 7, 7, "").includes("All 7 plans on this server"),
  "reach: the 4-argument call still says 'plans' exactly as before");

// ---- the two empty states are different sentences ----------------------------
// An empty tab is not an empty server, and these two tabs are empty for
// unrelated reasons: nothing uploaded vs. the box was never seeded.
{
  const mine = P.projEmptyMine(8);
  check(mine.includes("+ New project") && mine.includes("drop a PDF"),
    "empty (yours): names the action that works");
  check(mine.includes("8 demo plans"),
    "empty (yours): points at the demo tab when there is one");
  check(!P.projEmptyMine(0).includes("demo"),
    "empty (yours): says nothing about demos on a box that has none");

  const none = P.projEmptyDemo(8, 0, ["a.pdf", "b.pdf"]);
  check(none.includes("None of the 8 demo plans")
    && none.includes("seed-demo.sh"),
    "empty (demo): an unseeded box says it is unseeded, and how to fix it");
  check(none.includes("Missing: a.pdf, b.pdf."),
    "empty (demo): names which files are missing");
  const some = P.projEmptyDemo(8, 3, ["c.pdf"]);
  check(some.includes("3 of 8 demo plans are on this server"),
    "empty (demo): a partly-seeded box says how far it got");
  check(P.projEmptyDemo(0, 0, []).includes("no demo set configured"),
    "empty (demo): no manifest is its own message, not a seeding nag");
}

// ---- structure -------------------------------------------------------------
check(/<main>[\s\S]*id="projectsView"/.test(htmlSrc),
  "#projectsView lives inside <main>");
check(htmlSrc.indexOf('id="projectsView"') < htmlSrc.indexOf('id="pageStrip"'),
  "#projectsView sits before the workbench columns");
const brand = htmlSrc.slice(htmlSrc.indexOf('class="brand"'),
                            htmlSrc.indexOf("</div>", htmlSrc.indexOf('class="brand"')));
check(brand.includes('id="projectsLink"'),
  "the Projects link rides the brand block");
check(cssSrc.includes("main.pv-open #pageStrip")
  && cssSrc.includes("main.pv-open #sidebar")
  && cssSrc.includes("main.pv-open #stage"),
  "the CSS toggle hides exactly the three workbench columns");
for (const id of ["pvGrid", "pvEmpty", "pvBack", "pvCount",
                  "pvSearch", "pvSort", "pvReach",
                  "pvTabDemo", "pvTabMine", "pvTabDemoN", "pvTabMineN"]) {
  check(htmlSrc.includes(`id="${id}"`), `#${id} exists in index.html`);
}

// ---- WHERE THE "OPEN A PLAN" STRIP WENT --------------------------------------
// The strip is gone from the top of #projectsView. Every id it held is either
// still reachable somewhere honest or provably referenced nowhere - a dead id
// that another file still calls is the failure mode this block guards.
const pvView = htmlSrc.slice(htmlSrc.indexOf('id="projectsView"'),
                             htmlSrc.indexOf('id="settingsView"'));
const svView = htmlSrc.slice(htmlSrc.indexOf('id="settingsView"'),
                             htmlSrc.indexOf('id="pageStrip"'));
check(!htmlSrc.includes("pv-open-strip") && !htmlSrc.includes("pv-open-note"),
  "the 'Open a plan' strip is gone from the markup");
check(!/\.pv-open-(strip|note|lab)\s*[,{]/.test(cssSrc),
  "...and its CSS rules went with it, rather than styling nothing");
// UPLOAD: repurposed in place as the page's one primary action.
check(pvView.includes('id="uploadBtn"') && pvView.includes("+ New project"),
  "#uploadBtn stayed on the Projects page as '+ New project'");
check(pvView.includes('id="fileInput"'),
  "#fileInput came with it - the button is inert without the input it clicks");
check(appSrc.includes('$("uploadBtn").onclick = () => $("fileInput").click()'),
  "the upload handler is unchanged");
check(cssSrc.includes("#detectBtn, .pv-new"),
  "the primary-button rule follows the button (.pv-up -> .pv-new), so the "
  + "page still has exactly one filled button");
// PATH / SAMPLE / OPEN: relocated to Settings, same ids and handlers.
for (const id of ["sampleSel", "pathInput", "openBtn"]) {
  check(!pvView.includes(`id="${id}"`), `#${id} left the Projects page`);
  check(svView.includes(`id="${id}"`), `#${id} arrived in Settings`);
}
check(svView.includes('id="svMachineSection"')
  && svView.includes('id="svGroupMachine"')
  && htmlSrc.includes('id="svNavMachine"'),
  "Settings has a 'This machine' group, and a nav entry for it");
check(appSrc.includes('"svGroupMachine", "svMachineSection", "svNavMachine"'),
  "the hosted-box branch hides the whole section, not just its three "
  + "controls - an empty group headed 'This machine' reads as a broken page");
check(!appSrc.includes('$("uploadBtn").textContent = "Upload plans"'),
  "the hosted branch no longer rewrites the button's label over '+ New project'");
// RECENTS: retired. Nothing may still reach for it.
check(!htmlSrc.includes('id="recentSel"'), "#recentSel is retired from the markup");
check(!cssSrc.includes("#recentSel"), "...and from the stylesheet");
check(!appSrc.includes('$("recentSel").onchange'),
  "...and its reopen handler is gone (pvOpenJob uses the same /open route)");
// loadRecent() itself must SURVIVE: RECENT_META is what tells #docRemoveBtn
// whether the server considers the open document deletable, and an early
// return on a missing <select> would have silently disarmed that button.
check(appSrc.includes("async function loadRecent()"),
  "loadRecent() survives the <select> it used to fill");
{
  const lr = appSrc.slice(appSrc.indexOf("async function loadRecent()"),
                          appSrc.indexOf("// job_id -> the row"));
  const metaAt = lr.indexOf("RECENT_META = {}");
  const guardAt = lr.indexOf("if (!sel) return;");
  check(metaAt > 0 && guardAt > metaAt,
    "loadRecent fills RECENT_META BEFORE it gives up on the absent <select>");
  check(lr.indexOf("syncRemoveBtn()") > metaAt,
    "...and re-syncs the remove button from it");
}
// the grid's rows also feed RECENT_META, so a document older than the newest
// 60 /api/recent returns still knows whether it may be deleted
check(appSrc.includes("if (!RECENT_META[r.job_id]) RECENT_META[r.job_id] = r"),
  "renderProjects folds its rows into RECENT_META");

// ---- the demo set is DATA, not a hard-coded list in the UI --------------------
check(fs.existsSync(path.join(__dirname, "..", "demo-plans.json")),
  "demo-plans.json exists");
{
  const man = JSON.parse(fs.readFileSync(
    path.join(__dirname, "..", "demo-plans.json"), "utf8"));
  const names = Object.keys(man.plans);
  check(names.length >= 4, "manifest lists at least the four plans asked for");
  for (const want of ["Oncor Cloudcrest Substation Civils - 20251212.pdf_safe.pdf",
                      "C4.0-SITE-GRADING-PLAN-Rev.1.pdf",
                      "Belle Farms Structural Plans.pdf",
                      "Belle Farms Upper Parcel ROAD GRADING 1 of 2.pdf"])
    check(names.includes(want), `manifest carries the named demo "${want}"`);
  for (const n of names) {
    check(typeof man.plans[n].shows === "string" && man.plans[n].shows.length > 20,
      `"${n}" says what it demonstrates`);
    check(Number.isInteger(man.plans[n].page) && man.plans[n].page >= 0,
      `"${n}" names the page to land on`);
  }
  // PROVENANCE. A client's drawing must never become a demo. These are the
  // two directories the brief excludes by name, plus the live-bid filenames.
  for (const n of names)
    check(!/ryno|stice|liberty|beaumont|kayden|ellis|klayton|swr_/i.test(n),
      `"${n}" is not a client document`);
}
// the UI must not carry its own copy of the list
check(!appSrc.includes("C4.0-SITE-GRADING-PLAN-Rev.1.pdf"),
  "app.js hard-codes no demo filename - the server sends the flag");
// the sort <select> offers exactly the modes PROJ_SORTS knows how to apply -
// an option with no comparator would silently fall back to last activity.
//
// SCOPED to the #pvSort element. Both checks read the WHOLE of index.html
// until 2026-08-31: the count one failed the moment any other panel grew a
// <select> (the earthwork surface-model picker was the first, and it would
// have gone on tripping on every future one while never once looking at
// #pvSort), and the per-mode one would have passed on an <option> belonging
// to some other control. Same checks, same intent, right scope - they are
// now actually statements about the sort control.
const pvSortSel = htmlSrc.slice(
  htmlSrc.indexOf('<select id="pvSort">'),
  htmlSrc.indexOf("</select>", htmlSrc.indexOf('<select id="pvSort">')));
for (const m of P.PROJ_SORTS)
  check(pvSortSel.includes(`<option value="${m}"`),
    `#pvSort offers the "${m}" mode`);
check((pvSortSel.match(/<option value="[a-z]+"/g) || []).length
  === P.PROJ_SORTS.length,
  "#pvSort offers no mode PROJ_SORTS cannot sort");
// search and sort are re-renders over the cached rows, never a refetch: a
// fetch on either control would re-hit /api/projects on every keystroke
const paint = appSrc.slice(appSrc.indexOf("function pvPaint()"),
                           appSrc.indexOf("function pvCard("));
check(!paint.includes("fetch("),
  "pvPaint filters the cached rows and never refetches");
check(appSrc.includes('$("pvSearch").addEventListener("input", pvPaint)'),
  "the search box repaints on every keystroke");
check(appSrc.includes('$("pvSort").addEventListener("change", pvPaint)'),
  "the sort control repaints on change");
// delete moved behind the overflow menu, but the confirm flow did not change
check(appSrc.includes("pvCloseMenus(); pvRemove(r);"),
  "the overflow menu's delete still calls pvRemove");
// UPDATED 2026-09-01, with the reason. This used to assert that the confirm
// SENTENCE was a literal inside pvRemove:
//     appSrc.includes('confirm("Remove \\"" + r.name + "\\" from this server?')
// Multi-select + bulk delete moved that sentence into pvBulkConfirm(), which
// builds it for one project and for forty from one place, so the literal is
// no longer in pvRemove and the old source-substring check could only fail.
// What it was PROTECTING - that the ⋯ menu keeps asking in #docRemoveBtn's
// words - is now pinned far harder in tests/test_projects_bulk_delete.js
// ("CONFIRM PARITY"), which compares the STRING pvBulkConfirm actually
// returns for one row against a literal copy of the shipped sentence,
// character for character. A source-substring test cannot catch a reflow
// that keeps the substring; that one can. All this needs to hold here is
// that pvRemove still routes through the shared builder rather than growing
// a second copy of the wording.
check(appSrc.includes("if (!confirm(pvBulkConfirm([r], []))) return;"),
  "pvRemove asks through the shared pvBulkConfirm builder");
check(!/confirm\("Remove \\"" \+ r\.name/.test(appSrc),
  "pvRemove keeps no second copy of the confirm wording");
check(!appSrc.includes('x.className = "pv-x"'),
  "the always-armed card ✕ is gone (delete is behind the ⋯ menu)");
// The empty state's promised wording is in the renderer.
// CHANGED, deliberately: this used to pin the single string "No plans yet —
// drop a PDF anywhere or use Upload." There is no longer ONE empty state.
// The grid now has two tabs and they are empty for unrelated reasons - "you
// have not uploaded anything" and "this box was never seeded with the demo
// set" - so one sentence covering both would have to be vague about which,
// which is the thing the landing page's honesty rules exist to prevent. The
// two replacements are pinned by projEmptyMine/projEmptyDemo above; what is
// pinned HERE is that pvPaint still reaches for them and still names the
// upload path in the one an empty box actually shows.
check(appSrc.includes("projEmptyMine(demoTotal)")
  && appSrc.includes("projEmptyDemo(pvDemoInfo.expected"),
  "empty state: pvPaint picks the message that fits the empty tab");
check(P.projEmptyMine(0).includes("drop a PDF anywhere on this window"),
  "empty state: the drop target is still named in words, as it was");
// opening a card goes through the same reopen route the recents select uses
check(appSrc.includes("/api/job/${r.job_id}/open"),
  "cards open through the recents' own /open route");

// ---- THE DEMO TAB IS THE MANIFEST, AND A DEMO OPENS FRESH -------------------
// CHANGED, deliberately, and it REPLACES the interim collapse test that stood
// here. That test pinned projTabRows folding the demo tab's job rows down to
// one card per display name - a workaround for the tab being built from job
// rows at all, which is what the owner corrected: "the demo plans should be
// just those couple of plans and should be fresh everytime they are opened,
// no pre-detect or anything". The tab is now rendered from demo-plans.json,
// so there are no duplicate rows left to collapse and the collapse is gone.
// (That block also sat AFTER the failure report at the bottom of this file,
// so its checks could not fail the run. These do.)
{
  const rows = [
    { name: "A.pdf", demo: true, detected: 3, when: 900 },
    { name: "A.pdf", demo: true, detected: 0, when: 100 },
    { name: "B.pdf", demo: false, mine: true, when: 5 },
    { name: "B.pdf", demo: false, mine: true, when: 6 },
  ];
  check(P.projTabRows(rows, "demo").length === 2,
    "projTabRows no longer collapses: it is a plain filter over JOB rows, "
    + "and nothing paints its demo branch");
  check(!appSrc.includes("ONE CARD PER DEMO PLAN"),
    "the interim collapse-by-name is gone from app.js");
  check(P.projTabRows(rows, "mine").length === 2,
    "your projects are still never collapsed - two uploads of one filename "
    + "are two of the operator's own documents");

  // the tab's population comes from the manifest route, not from pvRows
  check(appSrc.includes('fetch("/api/demo-plans")'),
    "the demo tab is loaded from /api/demo-plans");
  check(/const demoRows = pvDemoPlans;/.test(appSrc),
    "pvPaint draws the demo tab from the manifest, not from job rows");
  // ...so a box with NO demo jobs still shows every demo card
  check(P.projDefaultTab([{ demo: false, mine: true }], 7) === "mine"
    && P.projDefaultTab([], 7) === "demo"
    && P.projDefaultTab([], 0) === "mine",
    "default tab counts demos from the manifest when it is given one");
  check(P.projDefaultTab([{ demo: true }]) === "demo",
    "...and falls back to counting demo rows when it is not, unchanged");

  // OPENING ONE IS FRESH. The card must not use the plain reopen route: that
  // hands back whatever this box already detected, which for a demo is a
  // takeoff computed weeks ago replayed as though it were live.
  check(appSrc.includes('"/api/demo/" + encodeURIComponent(p.name) + "/open"'),
    "a demo card opens through the fresh-open route, not /api/job/{id}/open");
  check(/async function pvOpenDemo[\s\S]{0,2400}memKeysFor\(j\.name\)/.test(appSrc),
    "...and the fresh open also forgets THIS BROWSER's saved decisions for "
    + "the document, or the next visitor meets the last one's labels");
  check(/PROJ_DEMO_RULE/.test(appSrc)
    && /pvTab === "demo"[\s\S]{0,220}PROJ_DEMO_RULE/.test(appSrc),
    "the fresh-open rule is stated in the UI, on the demo tab");
  check(P.PROJ_DEMO_RULE.includes("open fresh")
    && P.PROJ_DEMO_RULE.includes("press Detect")
    && P.PROJ_DEMO_RULE.includes("pre-detected"),
    "the rule says what happens and what the visitor does about it");

  // the card's own line. A demo has no detected count worth printing - the
  // honest answer is always none, and "0 detected" reads as a failure.
  check(P.projDemoMeta({ available: true, pages: 26 })
    === "26 pages · opens undetected", "demo meta: pages plus the rule");
  check(P.projDemoMeta({ available: true, pages: 1 })
    === "1 page · opens undetected", "demo meta: singular page");
  check(P.projDemoMeta({ available: true, pages: null })
    === "opens undetected",
    "demo meta: an unknown page count is omitted, never guessed");
  check(!P.projDemoMeta({ available: true, pages: 26 }).includes("detected ")
    && !/\bdetected\b/.test(P.projDemoMeta({ available: true, pages: 3 })
        .replace("undetected", "")),
    "demo meta: never prints a detected count");

  // HONEST DEGRADATION: a demo whose PDF is not on this box still gets a
  // card, and the card says so, rather than erroring or vanishing.
  check(P.projDemoAvailable({ available: true }) === true
    && P.projDemoAvailable({ available: false }) === false
    && P.projDemoAvailable(null) === false,
    "availability is the server's flag, absent reads false");
  check(P.projDemoMeta({ available: false }) === "not on this server",
    "an unavailable demo says so on its meta line");
  check(P.projDemoWhy({}).includes("seed-demo.sh"),
    "...and its card names how a box gets the demo library");
  check(/pv-card" \+ \(here \? "" : " pv-away"\)/.test(appSrc),
    "an unavailable demo card is marked, not dropped");
  check(cssSrc.includes(".pv-card.pv-away"),
    "and style.css gives it a look of its own");
  check(!/pv-away[^}]*#[0-9a-fA-F]{3}/.test(
    cssSrc.slice(cssSrc.indexOf(".pv-card.pv-away"),
                 cssSrc.indexOf(".pv-shows"))),
    "the new rules carry no raw hex colour - tokens only");

  // the thumbnail is a page RASTER, which is honest to render ahead of time;
  // a cached DETECTION is not, and is what the open sweeps.
  check(appSrc.includes('"/api/demo/" + encodeURIComponent(p.name) + "/thumb"'),
    "the demo thumbnail is the manifest page's raster");
}

if (failures) { console.error(`\n${failures} FAILURE(S)`); process.exit(1); }
console.log("\nall projects view checks passed");

