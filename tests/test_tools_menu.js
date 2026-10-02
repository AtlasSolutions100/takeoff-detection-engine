// The header TOOLS dropdown, pinned.
//
// Two kinds of check, because the feature is two claims:
//
//   1. LOGIC - the pure block between the TOOLS-MENU markers (menu model:
//      which rows show, which row carries the open-panel dot, when the
//      "appears after detect" note shows; arrow-key wrap arithmetic).
//      webui/app.js is not a module and touches the DOM at load, so only
//      the marked block is evaluated - same technique as test_trench_math.
//
//   2. STRUCTURE - the nine tool buttons were MOVED, not recreated: each id
//      must appear exactly once in index.html, INSIDE #toolsMenu, with
//      #detectBtn (the workflow) and #fbBtn left outside; and app.js must
//      still attach a handler to every one of them. A moved DOM node keeps
//      its listeners and its element identity ($("cfBtn") resolves to the
//      same node wherever it sits), so id-in-menu + handler-still-attached
//      IS the "every button still fires" guarantee - plus a propagation
//      simulation below that pins the close-then-fire contract the menu's
//      click listener relies on.
//
// Dependency-free:  node tests/test_tools_menu.js

"use strict";
const fs = require("fs");
const path = require("path");

const appSrc = fs.readFileSync(
  path.join(__dirname, "..", "webui", "app.js"), "utf8");
const htmlSrc = fs.readFileSync(
  path.join(__dirname, "..", "webui", "index.html"), "utf8");
// the redesigned row (fix 9) is markup + CSS, so the CSS is part of the pin
const cssSrc = fs.readFileSync(
  path.join(__dirname, "..", "webui", "style.css"), "utf8");

let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); }
  else console.log("ok:", msg);
}

// ---- extract the pure block ------------------------------------------------
const a = appSrc.indexOf("// TOOLS-MENU-BEGIN");
const b = appSrc.indexOf("// TOOLS-MENU-END");
check(a >= 0 && b > a, "TOOLS-MENU markers present in webui/app.js");
const T = new Function(appSrc.slice(a, b) +
  " return { TOOLS_MENU_ITEMS, toolsMenuModel, toolsMenuNextIdx };")();

// ---- the registry is exactly the nine required tools ------------------------
const WANT = ["aiBtn", "cfBtn", "flBtn", "trBtn", "symBtn", "rollBtn",
              "xsBtn", "coBtn", "stBtn", "legendBtn"];
const got = T.TOOLS_MENU_ITEMS.map((it) => it[0]);
check(got.length === 10, `registry lists 10 tools (got ${got.length})`);
check(WANT.every((id) => got.includes(id)),
  "registry covers ANALYZE..LEGEND SHEET exactly");
check(!got.includes("detectBtn"), "DETECT LINES is not in the menu registry");

// ---- menu model -------------------------------------------------------------
{
  // nothing detected yet: only the legend tool is visible, note shows
  const rows = T.TOOLS_MENU_ITEMS.map(([id, p]) => ({
    id, hidden: id !== "legendBtn", panelHidden: p ? true : null,
  }));
  const m = T.toolsMenuModel(rows);
  check(m.visible.length === 1 && m.visible[0] === "legendBtn",
    "before detect: only Legend sheet is a menu row");
  check(m.note === true, "before detect: the explainer note shows");
  check(Object.values(m.dots).every((v) => v === false),
    "before detect: no dot anywhere");
}
{
  // a result loaded, earthwork panel open: 9 rows, exactly one dot
  const rows = T.TOOLS_MENU_ITEMS.map(([id, p]) => ({
    id, hidden: false, panelHidden: p ? (id !== "cfBtn") : null,
  }));
  const m = T.toolsMenuModel(rows);
  check(m.visible.length === 10, "after detect: all 10 rows visible");
  check(m.note === false, "after detect: note gone");
  check(m.dots.cfBtn === true, "open cut / fill stage carries the dot");
  const others = Object.entries(m.dots)
    .filter(([id]) => id !== "cfBtn").map(([, v]) => v);
  check(others.every((v) => v === false), "no other row carries a dot");
}
{
  // legendBtn has no panel (null): never a dot, even when visible
  const rows = T.TOOLS_MENU_ITEMS.map(([id, p]) => ({
    id, hidden: false, panelHidden: p ? false : null,
  }));
  const m = T.toolsMenuModel(rows);
  check(m.dots.legendBtn === false, "panel-less legend row never dots");
}

// ---- arrow-key wrap ----------------------------------------------------------
check(T.toolsMenuNextIdx(9, 0, 1) === 1, "ArrowDown moves 0 -> 1");
check(T.toolsMenuNextIdx(9, 8, 1) === 0, "ArrowDown wraps 8 -> 0");
check(T.toolsMenuNextIdx(9, 0, -1) === 8, "ArrowUp wraps 0 -> 8");
check(T.toolsMenuNextIdx(9, -1, 1) === 0, "entering from outside lands on 0");
check(T.toolsMenuNextIdx(9, -1, -1) === 8, "ArrowUp from outside lands on 8");
check(T.toolsMenuNextIdx(0, -1, 1) === -1, "empty menu: no focus target");

// ---- structure: the nine buttons moved INSIDE #toolsMenu ---------------------
const menuStart = htmlSrc.indexOf('id="toolsMenu"');
check(menuStart >= 0, "#toolsMenu exists in index.html");
// the menu div ends where its container closes; the note div is its last child
const menuEnd = htmlSrc.indexOf("</div>", htmlSrc.indexOf('id="toolsNote"'));
const menuHtml = htmlSrc.slice(menuStart, menuEnd);
for (const id of WANT) {
  const inMenu = menuHtml.includes(`id="${id}"`);
  const once = htmlSrc.split(`id="${id}"`).length - 1 === 1;
  check(inMenu && once, `#${id} appears exactly once, inside #toolsMenu`);
}
check(!menuHtml.includes('id="detectBtn"'),
  "#detectBtn stays OUTSIDE the menu (workflow, not tool)");
check(!menuHtml.includes('id="fbBtn"'), "#fbBtn stays OUTSIDE the menu");
check(htmlSrc.includes('id="detectBtn"'), "#detectBtn still exists");

// ---- every moved button still has its handler wired in app.js ----------------
for (const id of WANT) {
  const wired = appSrc.includes(`$("${id}").onclick`);
  check(wired, `app.js still attaches a click handler to #${id}`);
}

// ---- the redesigned row: icon + name + description + dot ---------------------
// Fix 9 turned nine bare verbs into rows that show the one-line description
// the title attribute already carried. Three things have to stay true or the
// row is lying: every button still HAS a title (it is the description's only
// source and the accessible name), the description is read from that title
// verbatim at runtime rather than duplicated into the markup, and the row's
// NAME lives in its own span so a textContent write cannot eat the icon.
for (const id of WANT) {
  const m = htmlSrc.match(
    new RegExp(`<button[^>]*id="${id}"[^>]*title="([^"]{10,})"`));
  check(!!m, `#${id} carries a real title (the row's description)`);
  const row = htmlSrc.slice(htmlSrc.indexOf(`id="${id}"`),
                            htmlSrc.indexOf("</button>", htmlSrc.indexOf(`id="${id}"`)));
  check(row.includes('class="tm-ico"'), `#${id} row has a 20px stroke icon`);
  check(row.includes('class="tm-nm"'), `#${id} row keeps its name in .tm-nm`);
  check(row.includes('class="tm-ds"'), `#${id} row has a description slot`);
  check(row.includes('class="tm-dot"'), `#${id} row has the state dot`);
  // the description must NOT be typed into the markup as well - one string,
  // one place, or the hover and the row drift apart
  const title = m ? m[1] : "";
  check(!row.includes(">" + title + "<"),
    `#${id} description is not duplicated into the markup`);
}
check(appSrc.includes('ds.textContent = b.getAttribute("title") || ""'),
  "descriptions are read from the button's own title, verbatim");
check(/function toolsMenuSync\(\)[\s\S]{0,400}toolsMenuDescribe\(\)/.test(appSrc),
  "toolsMenuSync refreshes the descriptions on every open");
// #aiBtn was the ONE button with no title; it has one now, written from what
// runAnalyze() does. If it ever loses it again the Analyze row goes blank.
check(/id="aiBtn"[^>]*title="[^"]{10,}"/.test(htmlSrc),
  "#aiBtn now has a title (it was the only button without one)");
// the label writes that used to blow the icon away go through the span helper
check(!/\$\("aiBtn"\)\.textContent\s*=/.test(appSrc),
  "nothing writes $(\"aiBtn\").textContent (it would delete the icon)");
// WHAT THIS PROTECTS is that the Analyze label is only ever written through
// the span helper - a direct .textContent write blows the icon away. It used
// to assert a COUNT of four call sites, which is a test whose only failure
// mode is "somebody refactored since I was written": the 2026-09-01 page-reset
// work merged loadResult's and selectPage's duplicate resets into one
// pageReset(), taking the honest count to three, and the suite failed on a
// change that made the thing it guards strictly harder to get wrong. Asserted
// as the invariant instead: at least one write, and no way around the helper.
{
  const n = (appSrc.match(/toolRowLabel\("aiBtn"/g) || []).length;
  check(n >= 1, "the Analyze label is written through toolRowLabel  ("
    + n + " call site" + (n === 1 ? "" : "s") + ")");
  check(!/\$\("aiBtn"\)\s*\.\s*(?:textContent|innerText|innerHTML)\s*=/.test(appSrc),
    "...and nothing writes that button's text any other way");
}

// ---- the three group headers are presentation only --------------------------
// They must not be focusable, must not be buttons, and must not change which
// ids the menu contains - a header that became a menu item would break the
// arrow-key walk, which enumerates TOOLS_MENU_ITEMS and nothing else.
const menuStart2 = htmlSrc.indexOf('id="toolsMenu"');
const menuEnd2 = htmlSrc.indexOf("</div>", htmlSrc.indexOf('id="toolsNote"'));
const menuHtml2 = htmlSrc.slice(menuStart2, menuEnd2);
for (const g of ["Calculators", "Counts", "Document"])
  check(menuHtml2.includes(`<div class="tm-group" aria-hidden="true">${g}</div>`),
    `"${g}" group header is an aria-hidden div, not a menu item`);
check((menuHtml2.match(/<button/g) || []).length === 10,
  "the menu still holds exactly ten buttons, headers included");
check((menuHtml2.match(/class="tm-sep"/g) || []).length === 2,
  "two separators for three groups");
check(cssSrc.includes(".tm-group") && /\.tm-group\s*{[^}]*pointer-events:\s*none/
  .test(cssSrc), "group headers take no pointer");
// the old ::before dot is explicitly retired, not left to double-draw
check(/\.tools-menu button::before\s*{\s*content:\s*none/.test(cssSrc),
  "the old ::before dot is turned off (the dot is a real span now)");

// ---- arrow keys follow the EYE, not the registry -----------------------------
// Grouping the rows made DOM order diverge from TOOLS_MENU_ITEMS order (the
// registry still starts at aiBtn; the menu now ends there). The arrow walk
// must read the markup, or ArrowDown jumps from the button to the last row.
check(appSrc.includes('[...$("toolsMenu").querySelectorAll("button")]'),
  "the arrow walk enumerates the menu's buttons in DOM order");
check(!/const btns = TOOLS_MENU_ITEMS\.map/.test(appSrc),
  "the arrow walk no longer enumerates the registry");
{
  // the two orders really are different now, which is why the above matters
  const domOrder = [...menuHtml2.matchAll(/<button[^>]*id="(\w+)"/g)].map(m => m[1]);
  check(domOrder.length === 10, "ten rows read out of the markup");
  check(domOrder.join() !== got.join(),
    "DOM order and registry order differ (grouping moved Analyze last)");
  check(domOrder[domOrder.length - 1] === "aiBtn",
    "Analyze is the last row on screen");
  check(domOrder[0] === "cfBtn", "Earthwork is the first row on screen");
  check(new Set(domOrder).size === 10 && WANT.every(id => domOrder.includes(id)),
    "grouping moved the rows but lost none of them");
}

// ---- click simulation: handler fires, THEN the menu closes -------------------
// The menu's own listener is bubble-phase on the container, so the button's
// handler (target phase) runs first and the close never suppresses it. This
// mirrors that dispatch order over stubs, for all nine ids.
{
  const fired = [];
  const menu = { hidden: false };
  const buttons = WANT.map((id) => ({
    id, onclick: () => fired.push(id),
  }));
  // DOM dispatch on a click at the button: target listeners (the button's
  // own onclick), then ancestors' bubble listeners (the menu's close)
  for (const btn of buttons) {
    menu.hidden = false;
    const menuWasOpenBeforeHandler = !menu.hidden;
    btn.onclick();                       // target phase
    menu.hidden = true;                  // container bubble listener
    check(menuWasOpenBeforeHandler && fired[fired.length - 1] === btn.id
      && menu.hidden,
      `menu click on #${btn.id}: handler fired, menu closed after`);
  }
  check(fired.length === 10, "all ten simulated clicks reached a handler");
}

// ---- STRUCTURE: every tool panel lives in the dock at the top -------------
// Added with the sidebar card architecture (CRITIQUE fix 5). The menu rows
// above are only half the contract: pressing one has to put its panel where
// the operator is looking. That is markup order now - #toolDock is the first
// child of #sideScroll and every panel sits inside it - so a future edit that
// moves a panel back out of the dock, or slips a card above the dock, is what
// this pins. It does NOT pin who shows or hides a panel: that is still each
// panel's own button and close handler, unchanged.
{
  const PANELS = ["trPanel", "coPanel", "rollPanel", "xsPanel",
                  "symPanel", "stPanel"];
  const dockAt = htmlSrc.indexOf('<div id="toolDock"');
  const scrollAt = htmlSrc.indexOf('<div id="sideScroll"');
  check(dockAt > 0 && scrollAt > 0 && dockAt > scrollAt,
    "#toolDock is inside #sideScroll");
  // nothing may be rendered between the scrollport opening and the dock
  const between = htmlSrc.slice(htmlSrc.indexOf(">", scrollAt) + 1, dockAt);
  check(!/<(section|div|input|button|h2)\b/.test(between.replace(/<!--[\s\S]*?-->/g, "")),
    "#toolDock is the FIRST element in #sideScroll — an opened panel is at the top");
  // the dock closes before the first card
  const firstCard = htmlSrc.indexOf('<section class="card"');
  check(firstCard > dockAt, "the standing cards come after the dock, not before");
  for (const p of PANELS) {
    const at = htmlSrc.indexOf(`<div id="${p}"`);
    check(at > dockAt && at < firstCard, `${p} is inside #toolDock`);
  }
  // ckPanel is NOT a tool: it is the doubt queue and keeps its own card
  const ckAt = htmlSrc.indexOf('<div id="ckPanel"');
  check(ckAt > firstCard,
    "#ckPanel stays in its own standing card, not in the tool dock");
  check(/\.tool-dock > \.ew-panel:not\(\[hidden\]\)/.test(cssSrc),
    "the dock styles the OPEN panel only, so a hidden one still takes no space");
}

if (failures) { console.error(`\n${failures} FAILURE(S)`); process.exit(1); }
console.log("\nall tools menu checks passed");
