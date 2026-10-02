// THE REDESIGN'S FRAME (shell.js / shell.css / responsive.css / welcome.*), pinned against the defects the live proof
// of 2026-10-01 found in a real browser (core-cutfill out/verify/2026-10-01/live-flag, live-bid, live-scale):
//   F1  after opening another job, "Bid sheet CSV" / "Report PDF" still pointed at the PREVIOUS job (money);
//   F2  on a job approved earlier, Summary's Report / Bid sheet buttons stayed disabled until a new Approve;
//   F3  the top bar ran past the window at 1440 and clipped "Double-chec" at 1024;
//   F4  the landing page had no accuracy-flag bar;
//   F5  the status bar's warnings were cut, the full text only in a hover title;
//   and the old calculator's removed earthwork: the frame says the server's note wherever it shows the numbers.
// The pure blocks of shell.js (SHELL-PURE, SB-FOLD) are evaluated on their own, with a small fake DOM for the fold;
// the layout rules are pinned as text (they were measured in Chrome: see the merge report).
//
// Dependency-free:  node tests/test_redesign_frame.js
"use strict";
const fs = require("fs");
const path = require("path");
const W = (f) => fs.readFileSync(path.join(__dirname, "..", "webui", f), "utf8");
const shell = W("shell.js"), shellCss = W("shell.css"), rspCss = W("responsive.css");
const welcome = W("welcome.html"), welcomeCss = W("welcome.css"), index = W("index.html");

let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); } else console.log("ok:", msg);
}
function block(src, a, b) {
  const i = src.indexOf(a), j = src.indexOf(b);
  if (i < 0 || j < i) throw new Error("markers " + a + " / " + b + " not found");
  return src.slice(i, j);
}

// ---------------------------------------------------------------- the pure block
const P = new Function(block(shell, "// SHELL-PURE-BEGIN", "// SHELL-PURE-END")
  + " return { approvalNotes, noteReadiness, linkPlan };")();
const NOTE = "Earthwork from the old calculator was removed. Redo it in Cut / fill.";
const info = { pages: [0, 2], outdated: { "2": "OUT OF DATE: page 3 was approved at 1\" = 20 ft ...", "4": "NOT IN THE BID: page 5 ..." },
               notes: { "0": "Page 1: " + NOTE, "2": "Page 3: " + NOTE } };
const job = P.approvalNotes(info, null);
check(job.length === 4 && job[0].startsWith("OUT OF DATE") && job[1].startsWith("NOT IN THE BID")
      && job[2] === "Page 1: " + NOTE && job[3] === "Page 3: " + NOTE,
      "the job's notes: every OUT OF DATE / NOT IN THE BID first, then each page's removed-earthwork note, by page");
check(JSON.stringify(P.approvalNotes(info, 0)) === JSON.stringify(["Page 1: " + NOTE]), "page 1's own note only");
check(P.approvalNotes(info, 2).length === 2 && P.approvalNotes(info, 2)[1] === "Page 3: " + NOTE,
      "a page with both: its OUT OF DATE, then its note");
check(P.approvalNotes(info, 1).length === 0 && P.approvalNotes(null, 0).length === 0, "no note, no answer: nothing said");
check(P.noteReadiness("Page 1: " + NOTE).label === "Earthwork" && P.noteReadiness(NOTE).value === "removed",
      "readiness names the removed earthwork");
check(P.noteReadiness("OUT OF DATE: x").value === "out of date" && P.noteReadiness("NOT IN THE BID: x").value === "not in the bid",
      "readiness names OUT OF DATE / NOT IN THE BID");

// F1 / F2: the links follow the job on screen and the server's approved pages
const A = "aaaaaaaaaaaa", B = "bbbbbbbbbbbb";
const pa = P.linkPlan(A, 0, [0], [], "A.pdf");
check(pa.report.show && pa.bid.show && pa.dl.show, "F2: job A approved earlier -> report, bid sheet and elements shown");
check(pa.bid.href === `/api/job/${A}/bidsheet.csv` && pa.report.href === `/api/job/${A}/report.pdf`
      && pa.dl.href === `/api/job/${A}/page/0/elements`, "F2: ...and they are A's");
const pb = P.linkPlan(B, 0, [], [], "B.pdf");
check(!pb.report.show && !pb.bid.show && !pb.dl.show, "F1: job B (nothing approved) -> every link hidden");
check(!pb.bid.href.includes(A) && !pb.report.href.includes(A), "F1: nothing of B's names job A");
const pb2 = P.linkPlan(B, 1, [0], [], "B.pdf");
check(pb2.bid.show && pb2.bid.href === `/api/job/${B}/bidsheet.csv` && !pb2.dl.show,
      "F1: B approved on page 1 -> B's bid sheet; page 2 is not approved -> no elements link");
check(P.linkPlan(A, 0, null, [], "A.pdf").bid.show === false, "no answer from the server: nothing shown");
const ig = P.linkPlan(B, 0, [0], ["A.pdf p1 :: SEWER", "B.pdf p1 :: WATER"], "B.pdf");
check(ig.bid.href === `/api/job/${B}/bidsheet.csv?ignore=${encodeURIComponent("1:WATER")}`
      && ig.report.href.endsWith("?ignore=" + encodeURIComponent("1:WATER")),
      "F1: another document's cross-sheet untick never reaches this job's bid sheet or report");

// the same unticks as app.js's own bsIgnoreQuery (fork 51c90b2, B4), key for key, so the two never write different hrefs
const appJs = W("app.js");
const bsiSrc = (appJs.match(/\nfunction bsIgnoreQuery\(\) \{[\s\S]*?\n\}\n/) || [""])[0];
check(!!bsiSrc, "app.js still has bsIgnoreQuery (the fork's untick filter)");
if (bsiSrc) {
  const bsi = new Function("state", bsiSrc + " return bsIgnoreQuery();");
  const keys = ["A.pdf p1 :: SEWER", "B.pdf p1 :: WATER", "B.pdf p12 :: 8\" DIP :: x p3 :: y", "B.pdf p2 :: GAS & ELEC",
                "B.pdf px :: BAD", "XB.pdf p1 :: OTHER"];
  for (const [doc, ks] of [["B.pdf", keys], ["A.pdf", keys], ["B.pdf", []], [undefined, ["? p4 :: Q"]]]) {
    const theirs = bsi({ docName: doc, xsIgnore: ks });
    const mine = P.linkPlan(B, 0, [0], ks, doc).bid.href.slice(`/api/job/${B}/bidsheet.csv`.length);
    check(theirs === mine, `the unticks for ${doc} agree with app.js bsIgnoreQuery (${JSON.stringify(mine)})`);
  }
}

// F1 / F2 wiring: when it runs. ONE owner of the links: app.js's applyOpened resets them and calls jobLinksSync, which
// is shell.js's linksSync (app.js's own copy showed elements.json for another approved page and fetched /approved twice)
const ap = (appJs.match(/\nfunction applyOpened\([\s\S]*?\n\}\n/) || [""])[0];
check(/jobLinksReset\(\);\s*jobLinksSync\(state\.job\)/.test(ap),
      "app.js's applyOpened hides the links and then asks jobLinksSync (the hook the frame takes over)");
check(/if \(F\("jobLinksSync"\)\) window\.jobLinksSync = function \(\) \{ return linksSync\(\); \};\s*else wrapAfter\("applyOpened", \(\) => \{ linksSync\(\); \}\);/.test(shell),
      "F1: a document opening re-checks the links, through app.js's own call (one request), or after it");
check(/new MutationObserver\(\(\) => \{ linksGen\+\+; linksSoon\(\); \}\)/.test(shell) && /linksObs = lo;/.test(shell),
      "an app.js write to the links voids an answer still on its way (asked before an Approve)");
const ls = block(shell, "async function linksSync()", "let linksT = 0;");
check((ls.match(/linksQuiet\(\);/g) || []).length === 2 && /linksObs\.takeRecords\(\)/.test(shell),
      "the frame's own writes to the links are not echoed back as app.js's (no second request, no self-voiding)");
check(/function onPage\(kind\) \{\s*linksSync\(\);/.test(shell), "F1: every page / result load re-checks them");
check(/a && !a\.hidden && \(!job \|\| !String\(a\.getAttribute\("href"\) \|\| ""\)\.includes\(`\/api\/job\/\$\{job\}\/`\)\)\) a\.hidden = true;/.test(shell),
      "F1: a link naming another job is hidden at once, before the server answers");
check(/for \(const id of DL_LINKS\) \{ const a = byId\(id\); if \(a\) lo\.observe\(a/.test(shell),
      "F1: app.js's own writes to the links (Approve, untick) are followed");
const rs = block(shell, "async function renderSummary()", "/* ----------------------------------------------------------------- 3D */");
check(rs.indexOf("await linksSync();") > 0 && rs.indexOf("await linksSync();") < rs.indexOf('rep = byId("reportLink")'),
      "F2: Summary asks the server before it builds the Report / Bid sheet buttons");
check(/approvalNotesEl\(smScope === "approved" \? data\.notes : approvalNotes\(approvedInfo\(\), s\.page\)\)/.test(rs),
      "the note shows above All approved sheets AND This sheet");
check(/for \(const n of approvalNotes\(approvedInfo\(\), s\.page\)\)/.test(shell), "...and in Bid readiness");
check(/if \(notesEl\) box\.append\(notesEl\);/.test(shell) && /approvalNotesEl\(approvalNotes\(approvedInfo\(\), null\)\)/.test(shell),
      "...and with the Plans tiles (also when nothing counts any more)");
check(!/earthwork-old|exportOldItems|OLD CALCULATOR|ewOldSeatNote/.test(shell),
      "the old calculator's rows are not labelled, separated or judged by the frame (the server filters them)");

// ---------------------------------------------------------------- F5: the fold, on a fake DOM
const fold = new Function(block(shell, "// SB-FOLD-BEGIN", "// SB-FOLD-END") + " return installStatusFold;")();
function El(cls, id) {
  const attrs = {}, classes = new Set(cls ? cls.split(" ") : []);
  const e = {
    id, textContent: "", scrollWidth: 100, clientWidth: 100, listeners: {},
    classList: { contains: (c) => classes.has(c), add: (c) => classes.add(c), remove: (c) => classes.delete(c),
      toggle: (c, on) => { if (on === undefined ? !classes.has(c) : on) classes.add(c); else classes.delete(c); } },
    getAttribute: (k) => (k in attrs ? attrs[k] : null), setAttribute: (k, v) => { attrs[k] = String(v); },
    removeAttribute: (k) => { delete attrs[k]; }, hasAttribute: (k) => k in attrs,
    addEventListener: (t, f) => { e.listeners[t] = f; },
    closest: (sel) => (sel === "[data-fold]" && "data-fold" in attrs ? e : null),
  };
  Object.defineProperty(e, "className", { set: (v) => { classes.clear(); String(v).split(" ").filter(Boolean).forEach((c) => classes.add(c)); } });
  return e;
}
const bar = El("status-bar", "statusBar"), cf = El("sb-cf", "sbCf"), st = El("status", "status");
bar.querySelectorAll = () => [cf, st];
let resized = 0;
const F = fold(bar, { onResize: () => { resized++; } });
cf.textContent = "OUT OF DATE: page 1's approval still has cut / fill rows that were removed from the takeoff ..."; cf.scrollWidth = 900;
F.sync();
check(cf.hasAttribute("data-fold") && cf.getAttribute("tabindex") === "0" && cf.getAttribute("role") === "button"
      && cf.getAttribute("aria-expanded") === "false", "F5: a warning that does not fit becomes a button (keyboard too)");
check(!st.hasAttribute("data-fold"), "F5: an empty line is left alone");
bar.listeners.click({ target: cf });
check(bar.classList.contains("sb-open") && cf.getAttribute("aria-expanded") === "true" && resized === 1,
      "F5: a click / tap opens the bar (and the canvas is told)");
let prevented = false;
bar.listeners.keydown({ target: cf, key: "Enter", preventDefault: () => { prevented = true; } });
check(!bar.classList.contains("sb-open") && prevented, "F5: Enter folds it again");
bar.listeners.keydown({ target: cf, key: " ", preventDefault: () => {} });
check(bar.classList.contains("sb-open"), "F5: Space opens it");
bar.listeners.click({ target: cf });
st.textContent = 'Nothing was saved: the scale of this sheet was changed to 1" = 40 ft ..., and this page still shows 1" = 30 ft. Reload ...';
st.className = "status err"; st.scrollWidth = 1200;
F.sync();
check(bar.classList.contains("sb-open"), "F5: a refused approve that does not fit opens the bar by itself");
bar.listeners.click({ target: st });
F.sync();
check(!bar.classList.contains("sb-open"), "F5: ...once: folded by hand, the same refusal does not reopen it");
cf.textContent = ""; st.textContent = ""; bar.classList.add("sb-open"); F.sync();
check(!bar.classList.contains("sb-open"), "F5: nothing left to say: the bar closes");
check(/\.status-bar\.sb-open \.sb-cf, html\.shell-on \.status-bar\.sb-open \.status \{\s*flex: 1 1 100%; order: -1; white-space: normal; overflow: visible;/.test(shellCss),
      "F5: open, each warning wraps whole on its own row");
check(/\.status-bar \.sb-cf \{ flex: 1 1 100%; \}/.test(rspCss), "F5: on a wrapped (tablet / phone) bar the lasting note has its own line (was 0px wide)");

// the host's cut / fill bar grows with its note ("Sheet C3.0: Waiting in line. Another sheet is being read first." beside
// the scale and warnings: 3-5 lines at 40ch, measured in Chrome at 1024-1680; a fixed 46px cut them under the top bar
// and the engine's frame)
check(/html\.shell-on \.cf-bar \{ min-height: var\(--t-ribbon-h\); padding: 4px var\(--t-sp-3\);/.test(shellCss)
      && !/html\.shell-on \.cf-bar \{[^}]*[^-]height:/.test(shellCss),
      "the cut / fill bar's height is a floor, never a ceiling (the waiting-in-line note shows whole)");

// ---------------------------------------------------------------- F3: the top bar (measured in Chrome, pinned as text)
check(/^html\.shell-on header #modTabs \{ flex: 0 1 auto; min-width: 0; \}/m.test(rspCss),
      "F3: at every width the tabs give way (scroll) before the bar runs past the window");
check(/@media \(max-width: 1679\.98px\) \{\s*html\.shell-on header:has\(\.user-wrap\) #teamLink/.test(rspCss),
      "F3: below 1680 Team / Settings step into the account menu (1440 fit with the full chip)");
check(/@media \(max-width: 1360px\) \{[\s\S]*?\.beta-chip \{ font-size: 0; padding: 3px 7px; flex: none; overflow: visible; \}/.test(shellCss),
      "F3: the narrow chip is never clipped (no \"Double-chec\")");
check(/\.beta-chip::before \{ content: "Double-check"; font-size: 12px; \}/.test(shellCss), "F3: the narrow label is the owner's \"Double-check\"");
check(/html\.shell-on header \.beta-chip \{ flex: 0 0\.05 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; \}/.test(shellCss),
      "F3: the wide chip can only end in a visible ellipsis, last of all");
check(/>Automated detection can be wrong<\/span>/.test(index) && /title="Double-check every quantity against the drawings\."/.test(index),
      "F3: the chip keeps the owner's words and its hover sentence");

// ---------------------------------------------------------------- P3 (live round 2026-10-01, polish 2026-10-02)
// P3-1: at 1280 the Panels button ran 12px past the window (ribbon scrollWidth 1300 in 1280): the container widths
// were fixed numbers that drifted. shell.js ribbonFit now measures and steps; evaluated here on a fake ribbon whose
// width answers to the classes (numbers in the spirit of Chrome's at 1280 / 800 / 390 on the static files).
const fitFn = (rb) => new Function("ribbon", block(shell, "// RIBBON-FIT-BEGIN", "// RIBBON-FIT-END") + " return ribbonFit;")(rb);
function fakeRibbon(clientWidth, widths) {
  const cls = new Set();
  return {
    cls, clientWidth,
    classList: { add: (c) => cls.add(c), remove: (...c) => c.forEach((x) => cls.delete(x)), contains: (c) => cls.has(c) },
    get scrollWidth() {
      let w = widths.base;
      if (cls.has("rb-fit-1")) w -= widths.panelsWord;
      if (cls.has("rb-fit-2")) w -= widths.toolWords;
      if (cls.has("rb-fit-3")) w -= widths.togWords;
      return Math.max(w, clientWidth);
    },
  };
}
const W1280 = { base: 1347, panelsWord: 47, toolWords: 608, togWords: 0 };
let rb = fakeRibbon(1280, W1280); fitFn(rb)();
check(rb.cls.has("rb-fit-1") && rb.cls.has("rb-fit-2") && !rb.cls.has("rb-fit-3"),
      "P3-1: 1280 - the Panels word, then the tools' words step out until the ribbon fits (was 1300 in 1280)");
rb = fakeRibbon(1680, W1280); fitFn(rb)();
check(rb.cls.size === 0, "P3-1: 1680 - everything fits with its words: nothing steps out");
rb = fakeRibbon(1280, W1280); rb.cls.add("rb-fit-3"); fitFn(rb)();
check(!rb.cls.has("rb-fit-3"), "P3-1: a refit starts from the full ribbon (no class left from a narrower window)");
rb = fakeRibbon(784, { base: 868, panelsWord: 0, toolWords: 0, togWords: 100 }); fitFn(rb)();
check(rb.cls.has("rb-fit-3"), "P3-1: 800 (tablet) - the drawer toggles' words go too, and the Panels button is no longer under the pinned Takeoff toggle");
rb = fakeRibbon(374, { base: 962, panelsWord: 0, toolWords: 0, togWords: 100 }); fitFn(rb)();
check(!rb.cls.has("rb-fit-3"), "P3-1: a phone cannot fit: the toggles keep their words and the icon strip scrolls between them, as designed");
rb = fakeRibbon(0, W1280); rb.cls.add("rb-fit-2"); fitFn(rb)();
check(rb.cls.size === 0, "P3-1: a hidden ribbon (Plans / Summary / 3D) carries no fit class");
check(/new ResizeObserver\(\(\) => ribbonFit\(\)\)/.test(shell) && /new MutationObserver\(\(\) => \{ watch\(\); ribbonFit\(\); \}\)\.observe\(ribbon, \{ childList: true \}\)/.test(shell),
      "P3-1: the fit reruns when the window, the tools or the ribbon's children (dock.js's Panels button) change");
check(!/@container \(max-width: 1263\.98px\)/.test(rspCss) && !/@container \(max-width: 1319\.98px\)/.test(rspCss),
      "P3-1: the two drifted container widths are gone");
check(/html\.shell-on #ribbon:where\(\.rb-fit-1\) #dkPanelsBtn > span \{ display: none; \}/.test(rspCss)
      && /html\.shell-on #ribbon:where\(\.rb-fit-2\) \.toolkit \.tk \{ width: 32px; padding: 0; justify-content: center; \}/.test(rspCss)
      && /html\.shell-on\.rsp-on #ribbon:where\(\.rb-fit-3\) \.rsp-tog \.rsp-tog-t \{ display: none; \}/.test(rspCss),
      "P3-1: the steps drop words only, at shell.css's weight (the phone's 40px targets still win)");
// P3-2: "3D" ran 2-3px under Detect lines and its scroll fade greyed it at 1024: the tab strip gave up 2px while the
// document name still had room (shrink 200 / 40 / 1 shares by shrink x width). Measured after: strip whole, 8px gap
// at 1366 / 1280 / 1024.
check(/html\.shell-on header #docName \{ min-width: 56px; flex-shrink: 200000; \}/.test(rspCss)
      && /html\.shell-on header #pageSel \{ flex: 0 40000 auto; min-width: 92px; \}/.test(rspCss),
      "P3-2: the name, then the sheet picker give way strictly before the tabs (x1000)");
check(/@media \(min-width: 1024px\) \{ html\.shell-on header #modTabs \{ margin-right: var\(--t-sp-2\); \} \}/.test(rspCss),
      "P3-2: Detect lines keeps a gap from the last tab when the bar is full");
// P3-5: a long document name leaked the top 4px of its third line under the two-line clamp (the clamp hides only
// past the padding box, and the name had 4px of bottom padding)
check(/html\.shell-on \.pv-name \{ padding: 2px 0 0; margin-bottom: 4px;/.test(shellCss) && !/\.pv-name \{ padding: 2px 0 4px/.test(shellCss),
      "P3-5: the space under a card's name is a margin, so the clamp shows exactly two lines");
check(/html\.shell-on \.pv-card:focus-visible \.pv-name \{\s*-webkit-line-clamp: unset; line-clamp: none; \}/.test(shellCss),
      "P3-5: a keyboard focus on the card shows the whole name (the title has it on hover)");

// ---------------------------------------------------------------- UI verify 2026-10-02 (core-cutfill out/verify/2026-10-02/ui-verify)
// 5: the two Line styles marks were empty 24px boxes (the frame's `html.shell-on button` padding 5px 11px left the icon
// 0px wide, measured at 1440 and 1920) and the name box showed 3 letters (40px). Measured after: icons 16px, name 162px.
check(/html\.shell-on \.style-row \.mark \{ width: 24px; height: 24px; padding: 0; border-color: transparent; background: transparent; \}/.test(shellCss)
      && /html\.shell-on \.style-row \.mark svg \{ flex: none; width: 16px; height: 16px; \}/.test(shellCss),
      "5: the show / keep marks keep their icon (no frame padding inside a 24px button)");
check(/html\.shell-on \.style-row \.mm-toggle \{ padding: 1px 5px;/.test(shellCss) && /html\.shell-on \.style-row \.fam-toggle \{ padding: 0 3px;/.test(shellCss),
      "5: LF / EA and the expander take their own width back from the name");
check(/html\.shell-on #styleList \{ container-type: inline-size; \}/.test(shellCss)
      && /@container \(max-width: 520px\) \{[\s\S]*?\.style-row > \.style-main \{ grid-column: 3 \/ -1; grid-row: 1; \}/.test(shellCss)
      && /\.style-row > \.style-layer \{ grid-column: 1 \/ 4; grid-row: 2;/.test(shellCss),
      "5: in a rail the row takes two lines and the name spans the first");
check(/html\.shell-on \.style-row \.style-label \{ padding: 1px 2px; border: 0;/.test(shellCss),
      "5: the name is typed-over text, not a 32px field (the row is 77px, was 79px with a 40px name)");

// 6: after Detect at 1440 x 900 with the default panels the rails were unusable (Line styles list 51px - no row -,
// Takeoff 106px, Bid view 19px; phone Takeoff drawer 106px, Bid view 6px). dock.js now FITS each rail: the filling
// panel needs its controls plus its first rows, the others their head plus a few rows; what does not fit folds to its
// head (one click opens it), then the Line styles filters fold behind one button. Measured on C4.0 after Detect
// (style rows / takeoff rows in view): 1440x900 0/0 -> 7/7, 1366x768 0/0 -> 7/5, 1280x720 0/0 -> 6/5, 390 phone
// drawers -> 7/5, 768 tablet -> 6/5.
const dockJs = W("dock.js"), dockCss = W("dock.css");
check(/\{ id: "cardStyles",[^}]*fill: true,\s*rows: \["#styleList", "\.style-row", 6\], compact: 4 \}/.test(dockJs)
      && /\{ id: "cardTakeoff",[^}]*fill: true,\s*rows: \["#itemized", "\.item-row:not\(\.head\):not\(\.total\)", 5\] \}/.test(dockJs),
      "6: the filling panels need six style rows / five takeoff rows");
check(/\{ id: "cardBid",[^}]*fold: 1 \}/.test(dockJs) && /\{ id: "cardCheck",[^}]*fold: 2 \}/.test(dockJs)
      && /\{ id: "cardSheet",[^}]*fold: 3 \}/.test(dockJs) && /\{ id: "sheetsCard",[^}]*fold: 3,/.test(dockJs)
      && /\{ id: "modCard",[^}]*fold: 5 \}/.test(dockJs),
      "6: the order panels fold in (Bid view, Questions, then the Sheet card; Sheets, the Line styles filters, then the module's tools)");
check(/function fit\(d\) \{/.test(dockJs) && /      fit\(d\);\n    \}/.test(dockJs) && !/function floors\(d\)/.test(dockJs),
      "6: every refresh fits each rail from scratch (the old fixed floors are gone)");
check(/for \(const s of steps\) \{\s*if \(total\(\) <= H \+ 0\.5\) break;/.test(dockJs),
      "6: panels fold only while the rail cannot hold the rest");
check(/obs\.takeRecords\(\);\n  \}/.test(dockJs), "6: the fit's own class writes do not wake the observer (no refresh loop)");
check(/pinned\.add\(id\);/.test(dockJs) && /head\.addEventListener\("click", openIt, true\);/.test(dockJs)
      && /e\.key === "Enter" \|\| e\.key === " "/.test(dockJs),
      "6: one click, Enter or Space opens a folded panel, before app.js's own fold, and it stays open");
check(/setSize\(p\.id, sz && sz >= SLIVER \? sz : null\);/.test(dockJs) && /Math\.max\(SLIVER, Math\.min\(maxH/.test(dockJs),
      "6: a saved height that leaves a sliver is repaired on load, and a drag cannot make one");
check(/if \(tool && c\.classList\.contains\("dk-fill"\)\) steps\.push\(\{ c, rank: 9, kind: "fold" \}\);/.test(dockJs)
      && /return p\.rows \? rowsNeed\(c, p, 2\)/.test(dockJs),
      "6: beside an open tool panel the list keeps two rows or folds to its head (never a 12px sliver)");
check(/\.dk-panel\.dk-auto \{ flex: 0 0 auto !important; min-height: 0 !important;/.test(dockCss)
      && /\.dk-panel\.dk-auto > :not\(\.rc-head, \.side-head, \.takeoff-head, \.dk-head\) \{ display: none !important; \}/.test(dockCss),
      "6: a folded panel is its head, nothing more");
check(/#stylesHead > \.side-actions \{ flex: 1 1 100%; min-width: 0; flex-wrap: wrap;/.test(dockCss)
      && /id="incUndoBtn"/.test(index) && /id="incSeedBtn"/.test(index),
      "the Line styles buttons wrap: the fork's undo / engine's picks and Filters stay inside the panel (measured 1024-1440, 390)");
check(/const KEEP_OPEN = "\.ar-note";/.test(dockJs) && /pinned\.has\(c\.id\) \|\| c\.querySelector\(KEEP_OPEN\)\) continue;/.test(dockJs)
      && /n\.className = "ar-note"; n\.id = "arNote";/.test(appJs),
      "a panel holding Areas' 'measured again' note is never folded to fit (it folded away at 1366x600, 1024x560)");
check(/#cardStyles\.dk-compact > :is\(#colorChips, #scopeBar\) \{ display: none !important; \}/.test(dockCss)
      && /h\("button", \{ type: "button", class: "mini", id: "dkFiltersBtn"/.test(dockJs),
      "6: on a short rail the colour chips, the scope bar and the key fold behind Filters");
check(/html\.shell-on \.scope-bar:not\(\.note-open\) :is\(\.scope-chip:not\(\.on\), \.scope-note\) \{ display: none; \}/.test(shellCss)
      && /"Pick trades"/.test(shell),
      "6: the scope bar is one line until opened, and the declared trades always show");
check(/html\.shell-on \.itemized \.sub-add-line:not\(\.open\):not\(:focus-within\) \{ display: none; \}/.test(shellCss)
      && /html\.shell-on \.itemized \.item-row:focus-within \+ \.sub-add-line \{ display: block; \}/.test(shellCss),
      "6: \"+ add sub-item\" shows for the row being edited, not as 21px of blank under every row");

// 4: from Earthwork, "Projects" left #cfStage (fixed, z 35) over the Projects page; the frame closed it on tab presses
// only. Walked live: Projects / Back, Settings, Team and the seven other tabs each leave it, Earthwork brings it back.
check(/html\.shell-on body\.view-open :is\(#cfStage, #fl3d\) \{ display: none !important; \}/.test(shellCss),
      "4: Projects, Settings and Team hide the cut / fill page and the rooms 3D (hidden, not closed: back as it was)");
check(/if \(m\.id !== "earthwork" && m\.id !== "3d"\) \{ closeStage\(\); close3dOverlay\(\); \}/.test(shell),
      "4: every other tab still closes it, and Earthwork opens it again (openPrimary -> cfOpen)");

// 8: "Trench and Count do nothing on the first click after switching tabs": the tab had opened the tool's panel itself
// (openVia), so the first press - module card or Tools menu - was the toggle that closed it. Walked live on C4.0 at
// 1440: 5 tabs x (module card + Tools menu rows + Measure / Draw / Work area / Trim) after a tab switch = 80 presses,
// 66 opened with one click before, 79 after (the 80th, Earthwork's Structure in the module card, sits under the open
// cut / fill page by design; its Tools menu row opens with one click).
check(/if \(b && !b\.hidden\) \{ b\.click\(\); autoOpen = panelId; return; \}/.test(shell)
      && /if \(bb && !bb\.hidden\) \{ pending = null; bb\.click\(\); autoOpen = p\.panelId; return; \}/.test(shell),
      "8: the frame remembers the panel a tab opened by itself");
check(/function firstPressShows\(panelId\) \{\s*if \(!panelId \|\| autoOpen !== panelId \|\| !isOpen\(panelId\)\) return false;\s*autoOpen = null;\s*revealPanel\(panelId\);\s*return true;/.test(shell),
      "8: the first press on that tool shows the panel (once), it does not close it");
check(/onclick: \(\) => \{\s*if \(firstPressShows\(p\)\) return;/.test(shell),
      "8: ...from the module card");
check(/toolsMenu\.addEventListener\("click", \(e\) => \{[\s\S]{0,200}?firstPressShows\(BTN_PANEL\[b\.id\]\)[\s\S]{0,80}?e\.stopPropagation\(\); e\.preventDefault\(\);\s*call\("toolsMenuClose"\);\s*\}, true\);/.test(shell),
      "8: ...and from the Tools menu (captured before app.js's toggle; the menu still closes)");
check(/if \(t\.id && t\.id === autoOpen && t\.hidden\) autoOpen = null;/.test(shell),
      "8: once that panel closes, its button toggles as before");
check(/if \(side && R && R\.drawerMode && R\.drawer !== side\) R\.open\(side\);/.test(shell),
      "8: on a tablet / phone the press opens the drawer the panel is in");

// 10: a viewer's Projects page told them to upload, drop a PDF or open a demo - all refused - and offered five demo
// cards that each refuse. team.js / team.css: the Demo tab, Detect, Detect all and Approve stand down for a viewer;
// an empty Your projects says what a Viewer can do and who can change it (the Read-only chip stays).
const teamJs = W("team.js"), teamCss = W("team.css");
check(/body\.role-viewer :is\(#pvTabDemo, #detectBtn, #detectAllBtn, #sheetsDetectAll, #plDetectAll, #approveBtn\) \{ display: none !important; \}/.test(teamCss),
      "10: no refused button for a viewer: the Demo tab, Detect, Detect all (both proxies too), Approve");
check(/"A Viewer can open and export projects, but cannot upload, detect, price or approve\. There is no project this account can open yet\."/.test(teamJs)
      && /"To upload your plans, ask an admin on your team to change your role to Estimator\."/.test(teamJs),
      "10: the empty page says plainly what a viewer can do and who can change it");
{
  const vn = (teamJs.match(/const viewerNote = h\([\s\S]*?\)\);\n/) || [""])[0];
  check(!!vn && !/"button"|onclick|drop a PDF|demo plan/i.test(vn),
        "10: ...and offers nothing a viewer's press would see refused (no button in the note, no demo, no drop)");
}
check(/if \(viewer && demoTab && demoTab\.getAttribute\("aria-selected"\) === "true" && mineTab\) mineTab\.click\(\);/.test(teamJs)
      && /!\/could not\/i\.test\(pvEmptyEl\.textContent/.test(teamJs),
      "10: a viewer lands on Your projects; a failed list keeps app.js's own error line");
check(/const roChip = h\("span", \{ class: "hdr-ro", id: "roChip", hidden: true,/.test(teamJs), "10: the Read-only chip is kept");
// fork 92c8e40 gave app.js its own viewer sentence in #pvEmpty (projEmptyMine(_, viewOnly)): ONE note on screen,
// the frame's, which hides #pvEmpty while it shows
check(/function projEmptyMine\(demoCount, viewOnly\)/.test(appJs)
      && /html\.shell-on body\.role-viewer\.viewer-empty #pvEmpty \{ display: none; \}/.test(teamCss),
      "10: app.js's viewer sentence and the frame's note are never both on screen");

// Team page and Settings -> Accounts change roles by ONE rule (fork 92c8e40, app.js svRoleChange): same route, the
// same question before an admin, the same words after; your own role is disabled on both
{
  const cr = (teamJs.match(/async function changeRole\([\s\S]*?\n  \}\n/) || [""])[0];
  check(/svRoleChange\(\{ user, role: was \}, role\)/.test(cr) && /ch\.ask && !window\.confirm\(ch\.ask\)/.test(cr)
        && /toast\(ch \? ch\.done :/.test(cr), "the Team page's role pick goes through app.js svRoleChange (ask, route, words)");
  check(/function svRoleChange\(u, next\)/.test(appJs) && /"\/role", body: \{ role: next \}/.test(appJs),
        "app.js svRoleChange still sends a role-bearing account to /role");
  check(/"aria-label": `Role of \$\{u\.user\}`, disabled: self,/.test(teamJs), "your own role is not offered on the Team page either");
  check(/login_failed: \(e\) =>/.test(teamJs), "a failed sign-in (fork's sign-in limit) reads as words in the team feed");
  // client.js v2 (fork 152148c) says "You were signed out. Sign in again." on top after a 401; the Team page says the
  // same, never "no accounts on this server", and its refused actions use those words too
  const rd = (teamJs.match(/async function render\(\) \{[\s\S]*?const meB = me\.body/) || [""])[0];
  check(/if \(me\.status === 401\) \{[\s\S]*"You were signed out\. "[\s\S]*"Sign in again\."[\s\S]*return;\s*\}/.test(rd),
        "a signed-out Team page says so (not the no-accounts setup page)");
  check(/const failWords = \(r, dflt\) => \(r\.status === 401 \? "You were signed out\. Sign in again\."/.test(teamJs)
        && (teamJs.match(/failWords\(r, "/g) || []).length === 4 && !/r\.body && r\.body\.detail\) \|\| "/.test(teamJs),
        "every refused team action says the signed-out words on a 401");
  check(/var SAY = "You were signed out\.", LINK = "Sign in again\.";/.test(W("client.js")), "client.js's words are the same");
}

// L1: at 768 the Panels button ran 6px off the right edge and 104px under the pinned Takeoff toggle (the strip
// scrolls on a tablet / phone, and the button rode it). It is pinned beside the toggle now, from 360 to 1023; from
// 1024 to 1920 ribbonFit keeps it 8px inside the window (measured 360, 390, 414, 600, 700, 768, 800, 820, 900, 1000,
// 1024, 1100, 1180, 1280, 1366, 1440, 1600, 1920).
check(/if \(ribbon\.style\) ribbon\.style\.setProperty\("--rb-tog-w", \(tog \? tog\.offsetWidth : 0\) \+ "px"\);/.test(shell),
      "L1: ribbonFit hands responsive.css the Takeoff toggle's width");
check(/html\.shell-on\.rsp-on #ribbon #dkPanelsBtn \{ position: sticky; right: calc\(var\(--rb-tog-w, 0px\) \+ 6px\); z-index: 8;/.test(rspCss),
      "L1: where the strip scrolls, the Panels button is pinned beside the Takeoff toggle, never past the edge");
{
  rb = fakeRibbon(1280, W1280); fitFn(rb)();
  check(rb.cls.has("rb-fit-2"), "L1: the fit still runs on a ribbon without querySelector / style (the test's fake)");
}
// ...and every control is at least 32 x 32 on a tablet, a phone or a coarse pointer (scanned at 768, 390, 360: the
// workbench, both drawers, Projects, Settings - nothing under 32 left in the frame's pages)
check(/@media \(max-width: 1023\.98px\), \(pointer: coarse\) \{\s*html\.shell-on :is\(button, select, \[role="button"\]:not\(canvas\), \[role="tab"\], summary\):not\(\[hidden\]\) \{ min-height: 32px; \}/.test(rspCss),
      "L1: buttons, selects, tabs: 32px tall on touch sizes only (a fine pointer at 1024+ keeps the desk density)");
check(/:is\(\.dk-b, \.mark, \.mm-toggle, \.fam-toggle, #allBtn, #noneBtn, #mlToggle, \.ml-goto, \.sv-x, #scopeClose,\s*\.sg-fold, \.pv-more, \.sv-more, \.scope-more, \.lc-x, \.mini\.x\) \{ min-width: 32px;/.test(rspCss)
      && !/:is\(button, \[role="button"\]:not\(canvas\), summary\):not\(\[hidden\]\) \{ min-width: 32px; \}/.test(rspCss),
      "L1: only the narrow controls get a min-width (a blanket one let the module tabs shrink over each other at 390)");
check(/canvas\.swatch-line\[role="button"\] \{ box-sizing: content-box; padding: 11px 0; \}/.test(rspCss),
      "L1: the swatch's hit box is 32px tall, its drawing is not stretched");
check(/html\.shell-on \.style-row \{ grid-template-columns: 32px 32px 46px minmax\(0, 1fr\) auto 32px; column-gap: 4px; \}/.test(rspCss),
      "L1: with 32px controls a style row stays 64px (a 390 phone drawer: 6 style rows, 5 takeoff rows)");

// M2-M4 (UI verify 2026-10-02 minors)
check(!/edit quantities and prices in the right rail/.test(shell) && /quantities and prices are edited in the Takeoff panel, beside the sheet \(Lines tab\)/.test(shell),
      "M2: Summary names where prices are edited (it has no right rail)");
check(/\.sv-note\.sv-fold:not\(\.open\) \{[^}]*-webkit-line-clamp: 2;[^}]*max-height: 3\.1em;/.test(shellCss),
      "M3: a folded Settings note is two lines high, no half of a third (measured 37px at 18.6px lines)");
check(/history\.replaceState\(null, "", location\.pathname \+ location\.search \+ want\)/.test(shell)
      && /const want = "#job=" \+ s\.job;/.test(shell) && /if \(!s\.job \|\| document\.body\.classList\.contains\("view-open"\)\) return;/.test(shell),
      "M4: while a sheet is open the address is #job=<id> (app.js's bootJob opens it on a reload); Projects / Settings keep theirs");
check(/sessionStorage\.setItem\(OPEN_KEY, JSON\.stringify\(\{ job: s\.job, page: s\.page \}\)\)/.test(shell)
      && /if \(want !== S\(\)\.page && want >= 0 && want < \(\(S\(\)\.kinds \|\| \[\]\)\.length\)\) \{ goPage\(want\); return; \}/.test(shell),
      "M4: ...and this tab's page comes back with it");

// ---------------------------------------------------------------- F4: the landing page's bar
const LINE = "Automated detection can be wrong. Double-check every quantity against the drawings.";
const body = welcome.slice(welcome.indexOf("<body>"));
check(body.indexOf(`<div class="flag-bar" role="note">${LINE}</div>`) > 0
      && body.indexOf("flag-bar") < body.indexOf("<header"), "F4: /welcome opens with the full line, above its header");
check(/\.flag-bar \{\s*padding: 8px 16px; font-size: 12\.5px;/.test(welcomeCss) && !/\.flag-bar[^{]*\{[^}]*display:\s*none/.test(welcomeCss),
      "F4: styled like the sign-in bar and never hidden at any width");
check(/welcome\.css\?v=3/.test(welcome), "F4: welcome.css's cache buster raised");

console.log(failures ? `\n${failures} failure(s)` : "\nall redesign frame checks passed");
process.exit(failures ? 1 : 0);
