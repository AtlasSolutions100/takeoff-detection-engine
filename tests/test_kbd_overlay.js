// The keyboard-map overlay's honesty, pinned.
//
// The #kbdHelp overlay (webui/index.html, KBD-MAP markers) documents the
// app's shortcuts for the operator. A documented key that is not bound is
// a lie the operator acts on, and a bound key that is not documented is a
// feature nobody finds - so this test walks every <kbd> row in the overlay
// and demands the real binding exists in webui/app.js, and walks the known
// global bindings and demands each has a row. It parses source as TEXT
// (app.js is not a module and touches the DOM at load; the overlay is
// static HTML), the same reason test_check_queue.js evals only a marked
// block.
//
// ONE deliberate exception, whitelisted by exact token: the "Ctrl+Z" row
// is a placeholder for the undo another builder is landing in this same
// UI wave. When their binding lands, move it from PLACEHOLDERS into
// BINDINGS; if their work is dropped, the row must be deleted.
//
// Dependency-free like tests/test_gate.py:  node tests/test_kbd_overlay.js

"use strict";
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");

let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); }
  else console.log("ok:", msg);
}

// ---- extract the overlay block ----
const a = html.indexOf("KBD-MAP-BEGIN");
const b = html.indexOf("KBD-MAP-END");
check(a >= 0 && b > a, "KBD-MAP markers present in index.html");
const block = a >= 0 && b > a ? html.slice(a, b) : "";

// every <kbd> token the overlay shows
const shown = [...block.matchAll(/<kbd>(.*?)<\/kbd>/g)].map(m => m[1].trim());
check(shown.length > 0, `overlay lists ${shown.length} key rows`);

// ---- each shown token -> the regex its real binding must satisfy ----
// (regexes match webui/app.js at provenance-and-gate; they are anchored on
// the comparison expressions, not line numbers, so edits above the
// handlers cannot break them)
const BINDINGS = {
  "Scroll":        /addEventListener\("wheel"/,                     // canvas zoom
  "Drag":          /cv\.addEventListener\("mousedown"/,             // pan / vertex drag
  "Esc":           /e\.key === "Escape"/,                           // layered cascade + tools
  "M":             /k === "m"/,
  "Enter":         /e\.key === "Enter"/,                            // finish draw/outline
  "S":             /k === "s"/,
  "X":             /k === "x"/,
  "E":             /k === "e"/,
  "D":             /k === "d"/,
  "T":             /k === "t"/,                                     // trim at a match line
  "R":             /e\.key === "r" \|\| e\.key === "R"/,
  "J":             /e\.key === "j" \|\| e\.key === "J"/,
  "Del / ⌫":  /e\.key === "Delete" \|\| e\.key === "Backspace"/,
  "Del / &#9003;": /e\.key === "Delete" \|\| e\.key === "Backspace"/,
  "I":             /k === "i"/,
  "F":             /k === "f"/,
  "L":             /k === "l"/,
  "?":             /e\.key === "\?"/,
  "Ctrl+Z":        /undoRedo\(/,                                    // UNDO-CORE landing
  "⇧+drag":    /e\.shiftKey && !state\.tool/,                   // marquee multi-select
  "&#8679;+drag":  /e\.shiftKey && !state\.tool/,
  // Ctrl/Cmd+click multi-select: the canvas half (handleCanvasClick) and the
  // style-row half (renderStyles' row.onclick) are two call sites of one
  // gesture, so the rule below is anchored on the canvas guard and the
  // reverse walk under "the gesture reaches both surfaces" pins the other.
  "Ctrl+click":    /\(e\.ctrlKey \|\| e\.metaKey\) && !e\.shiftKey/,
};
const PLACEHOLDERS = new Set([]);   // Ctrl+Z graduated when UNDO-CORE landed in the same wave

for (const tok of shown) {
  if (PLACEHOLDERS.has(tok)) {
    console.log("ok: placeholder row allowed (by whitelist):", tok);
    continue;
  }
  const re = BINDINGS[tok];
  if (!re) { failures++; console.error("FAIL: overlay shows unknown key with no binding rule:", tok); continue; }
  check(re.test(js), `overlay row "${tok}" has its binding in app.js (${re})`);
}

// ---- the reverse walk: every known global binding has a row ----
// (single-key review shortcuts + the delete key; tool-scoped Enter/Esc are
// covered by the Tools rows above)
const MUST_SHOW = ["M", "S", "X", "E", "D", "R", "J", "I", "F", "L", "?", "Esc", "Enter"];
for (const k of MUST_SHOW)
  check(shown.includes(k), `binding "${k}" is documented in the overlay`);
check(shown.some(t => /^Del \/ /.test(t)), 'Delete/Backspace is documented in the overlay');
check(shown.includes("Ctrl+Z"), "the Ctrl+Z undo row is present");
check(shown.includes("Ctrl+click"), "the Ctrl+click multi-select row is present");
// the gesture reaches BOTH surfaces the overlay row promises: a line on the
// canvas, and a whole family from its row in the style list
check(/msPickChain\(/.test(js) && /msPickFamily\(/.test(js),
  "Ctrl+click is wired on the canvas AND on the style rows");

// ---- the overlay's plumbing ----
check(/id="kbdHelp"[^>]*hidden/.test(html), "#kbdHelp starts hidden");
check(/\$\("kbdBtn"\)\.onclick/.test(js), "the toolkit ? button is wired in app.js");
check(/kh\.contains\(e\.target\)/.test(js), "click-outside-closes is wired in app.js");
check(/e\.key === "\?"/.test(js), 'the "?" key toggle exists in app.js');
check(/kbdHelp"\)\.hidden = true/.test(js), "Esc path closes the overlay in app.js");

// ---- toolkit: every button carries a title; the groups are separated ----
const tkBlock = html.slice(html.indexOf('<div class="toolkit">'), html.indexOf('<div class="canvas-status">'));
check(tkBlock.length > 0, "toolkit block found");
const tkButtons = [...tkBlock.matchAll(/<button[^>]*class="tk[^"]*"[^>]*>/g)].map(m => m[0]);
check(tkButtons.length >= 13, `toolkit has ${tkButtons.length} buttons (measure..help)`);
for (const btn of tkButtons) {
  const id = (btn.match(/id="([^"]+)"/) || [])[1] || btn;
  check(/title="[^"]{10,}"/.test(btn), `toolkit button ${id} has a real title`);
}
const seps = (tkBlock.match(/tk-sep/g) || []).length;
check(seps === 5, `toolkit has 5 group separators (got ${seps}): measure | geometry | family | delete | work area | view`);
check(/id="kbdBtn"/.test(tkBlock), "the ? help button sits in the toolkit column");

// ---- first-open hint: the four-step how-it-works ----
const hint = html.slice(html.indexOf('id="hint"'), html.indexOf('<div class="toolkit">'));
check(/Open a plan to begin/.test(hint), "hint keeps its original lead line");
for (const step of ["Open", "Detect", "CHECK THESE", "Approve"])
  check(hint.includes(step), `hint walks the "${step}" step`);

if (failures) { console.error(failures + " failure(s)"); process.exit(1); }
console.log("all keyboard-overlay checks passed");
