// THE WALL PANEL CALCULATOR'S PAGE (webui/wallpanels.js), pinned.
//
//   1. WHERE IT SITS: a row in Tools -> Calculators, a panel in the tool dock, a section in
//      Settings, its script loaded after app.js.
//   2. THE PAGE OWNS NO NUMBER: it reads the server's strings and never does arithmetic on a length
//      or on money; every act it sends carries only the keys the server lists for that act
//      (takeoff_engine/wp_job.py ACT_KEYS is read here, so the two cannot drift apart).
//
// Dependency-free:  node tests/test_wp_ui.js

"use strict";
const fs = require("fs");
const path = require("path");
const read = (...p) => fs.readFileSync(path.join(__dirname, "..", ...p), "utf8");
const js = read("webui", "wallpanels.js");
const html = read("webui", "index.html");
const app = read("webui", "app.js");
const py = read("takeoff_engine", "wp_job.py");

let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); }
  else console.log("ok:", msg);
}

// ---- where it sits ---------------------------------------------------------
const menuAt = html.indexOf('id="toolsMenu"');
const menuEnd = html.indexOf("</div>", html.indexOf('id="toolsNote"'));
const menu = html.slice(menuAt, menuEnd);
const calc = menu.slice(menu.indexOf(">Calculators<"), menu.indexOf(">Counts<"));
check(calc.includes('id="wpBtn"') && /<span class="tm-nm">Wall panels<\/span>/.test(calc),
  "Wall panels is a row under Tools -> Calculators");
check(html.split('id="wpBtn"').length - 1 === 1 && html.split('id="wpPanel"').length - 1 === 1, "one button, one panel");
const dockAt = html.indexOf('<div id="toolDock"'), firstCard = html.indexOf('<section class="card"');
const panelAt = html.indexOf('<div id="wpPanel"');
check(panelAt > dockAt && panelAt < firstCard, "its panel is in the tool dock");
for (const id of ["wpHead", "wpClose", "wpHint", "wpBody", "wpActions", "wpResult"])
  check(html.includes(`id="${id}"`), `the panel has #${id}`);
check(html.indexOf('src="wallpanels.js') > html.indexOf('src="app.js'), "wallpanels.js loads after app.js");
check(/<link rel="stylesheet" href="wallpanels\.css/.test(html), "its stylesheet is linked");
check(/\["wpBtn", "wpPanel"\]/.test(app), "the menu registry in app.js lists it (the open-panel dot reads it)");
check(html.includes('<a href="#wallpanels">Wall panels</a>') && html.includes('id="svGroupWallpanels"')
  && /wallpanels: "svGroupWallpanels"/.test(app), "Settings has a Wall panels group and its address lands on it");
for (const id of ["svWpNote", "svWpBody", "svWpSave", "svWpLoad", "svWpFile", "svWpState"])
  check(html.includes(`id="${id}"`), `the settings section has #${id}`);
check(/id="svWpLoad"[^>]*hidden/.test(html), "the load-from-a-file button is hidden until the server says the caller is staff");

// ---- the page owns no number -----------------------------------------------
const code = js.replace(/\/\/[^\n]*/g, "");          // comments out
check(!/parseFloat|parseInt|toFixed|Math\.round|Math\.ceil|Math\.floor|Number\(/.test(code),
  "no number is parsed, rounded or formatted on the page");
check(!/_cents\b/.test(code), "the page reads no cents: every amount it shows is a string the server printed");
// (a + beside one of these names is the joining of two strings for the screen; times, over and minus never are)
check(!/\b(amount|total|lf|rate|qty|price|count|pt)\b\s*[*\/-]\s*[\w(]|[*\/-]\s*\(?\s*\w*\.(amount|total|lf|rate|qty|price|count|pt)\b/.test(code)
  && !/\b(amount|total|lf|rate|qty|price|count)\b\s*\+\s*\d/.test(code) && !/[-+*\/]=/.test(code.replace(/[!=<>]=+/g, "")),
  "no arithmetic on an amount, a total, a rate, a quantity or a length");
check((code.match(/fetch\(`\/api\/job\/\$\{wp\.job\}\/wallpanels\/act`/g) || []).length === 1
  && /body: JSON\.stringify\(\{ version: wp\.d\.version, act \}\)/.test(code),
  "every write is one act over the version this tab read, through one function");
check(/r\.status === 409[\s\S]{0,80}wp\.stale = true/.test(code) && /if \(wp\.stale\) \{ setStatus\(WP_STALE_TEXT/.test(code),
  "a stale tab stops writing and says so");

// the keys the server allows for each act
const block = py.slice(py.indexOf("ACT_KEYS = {"), py.indexOf("}", py.indexOf("ACT_KEYS = {")));
const allowed = {};
for (const m of block.matchAll(/"(\w+)":\s*\(([^)]*)\)/g))
  allowed[m[1]] = [...m[2].matchAll(/"(\w+)"/g)].map(x => x[1]);
check(Object.keys(allowed).length >= 15 && allowed.wall_draw.join() === "room,page,seg", "the server's act list was read");
const sent = [...code.matchAll(/wpAct\(\{\s*kind:\s*"(\w+)"([^}]*)\}/g)];
check(sent.length >= 15, `the page's acts were read (${sent.length} call sites)`);
const seen = new Set();
for (const m of sent) {
  const kind = m[1];
  seen.add(kind);
  const keys = [...m[2].matchAll(/[,{]\s*(\w+)\s*(?::|,|$)/g)].map(x => x[1]);
  const extra = keys.filter(k => !(allowed[kind] || []).includes(k));
  check(!!allowed[kind] && extra.length === 0, `act ${kind} carries only ${JSON.stringify(allowed[kind] || "?")}` + (extra.length ? ` (found ${extra})` : ""));
}
for (const kind of Object.keys(allowed))
  check(seen.has(kind), `the page can send ${kind}`);
check(!seen.has("wall_put"), "the page never sends the server's own act");

// ---- the words the ruling asks for are the server's, not retyped here -------
check(!/geometry only/.test(code.replace(/id="wpHint"[\s\S]*?<\/div>/, "")) || /sg\.words/.test(code),
  "the suggestion's caveat is printed from the server's own words");
check(/sg\.words/.test(code) && /o\.arith/.test(code), "a suggestion shows its arithmetic and its caveat");
check(/wp\.held = d\.missing/.test(code) && /The proposal is held until these are set/.test(code),
  "a refused proposal shows the server's list of what is missing");

// ---- after CRITIC-wp-1 (2026-10-04) ------------------------------------------
// BLOCKER-1: a blank the job itself knows has no box - the page can not type over the job
{
  const tAt = code.indexOf('if (b.kind === "typed") {'), eAt = code.indexOf("} else {", tAt), bAt = code.indexOf('kind: "blank_set"');
  check(tAt > 0 && bAt > tAt && bAt < eAt && code.indexOf('kind: "blank_set"', bAt + 1) < 0,
    "a box to type a blank is drawn only for a blank no part of the job holds");
}
check(/o\.alt \? "Or instead: " : "Suggested: "/.test(code), "a room's other size of the same length is offered as an alternative, never a second suggestion");
check(/jset\("date"\)/.test(code) && /"Date on the proposal: " \+ \(dt\.text/.test(code), "the proposal's date is shown before issuing, with a box to change it");
check(/d\.rates_follow/.test(code) && /d\.settings_problem/.test(code) && /p\.problem/.test(code),
  "the page says which rates the job follows, and says when the settings could not be read");
check(/kind: "rates_take", part: "words"/.test(code) && /kind: "rates_take", part: "rates"/.test(code), "the sentence and notes can be taken without the rates");
check(/state\.excluded\.has\(w\.chain\)/.test(code), "a picked line erased on this screen is said on its wall");
check(/cw\.short/.test(code), "why a wall has no feet on the sheet is the server's word");
check(/c\.price_notes/.test(code), "a price item changed in Settings is shown where the job says what differs");

if (failures) { console.error(`\n${failures} FAILURE(S)`); process.exit(1); }
console.log("\nall wall panel page checks passed");
