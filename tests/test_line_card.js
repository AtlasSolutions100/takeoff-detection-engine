// CLICK A LINE, SEE WHAT IT IS - WITHOUT DISTURBING THE LEFT-HAND LIST.
//
// From the 2026-09-02 screen share. The partner wanted the line list to open
// itself on every click: "anytime you click on something, you want to
// immediately know what it is." The owner refused, correctly: "sometimes you
// just want to look at it, you don't want to mess up what you're looking at on
// your left tab." The partner then proposed the middle ground himself: "either
// it should enlist or it should hover somewhere so you can see it."
//
// WHY THE SELECTION BAR WAS NOT ALREADY THE ANSWER, measured on 512f474 rather
// than argued: a line clicked at y=537 in a 1000px window put the readout at
// y=915 - 450 px from the click, bottom-right, in 12px type, sharing a strip
// with the family switcher, "in list" and the colour chip. Right words, wrong
// place. The card measures 150 px from the same click. The BAR IS UNTOUCHED -
// another branch owns its elevation chip - and this is purely additive.
//
// The two claims this suite exists to hold:
//
//   1. IT CHANGES NOTHING. The owner's objection is the requirement. The card
//      must not isolate, scroll, re-render, reveal a row, or write a tick; the
//      ONLY state it owns is whether it was dismissed. Verified here by
//      reading every write in the block, not by trusting the comment.
//   2. IT IS ON THE LINE. Anchored to the chain's own midpoint in screen
//      space, re-placed on every draw() frame so a pan or a zoom keeps it
//      there, and flipped rather than clipped at the edges of the stage.
//
// The geometry runs for real: lineCardAnchor and lineCardPlace are evaluated
// with a stub state and a stub DOM, and the placements are checked to hand.
//
// Dependency-free:  node tests/test_line_card.js

"use strict";
const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(
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

const A = src.indexOf("// LINE-CARD ===");
const B = src.indexOf("// LINE-CARD-END");
check(A >= 0 && B > A, "LINE-CARD block present in webui/app.js");
if (A < 0 || B <= A) process.exit(1);
const block = src.slice(A, B);
// the ban scan below is about CODE, not about the comments that explain the
// bans - strip the line comments first, or the block's own "no revealStyleRow"
// note trips it
const code = block.replace(/^\s*\/\/.*$/gm, "");

// =========================================================================
// 1. IT CHANGES NOTHING - the owner's objection, as a test
// =========================================================================
// Everything the card is forbidden to do. Each of these is a real function in
// this file that WOULD disturb the left list or the sheet.
for (const banned of ["revealStyleRow", "renderStyles(", "setInclude(", "setFamVis(",
                      "scrollIntoView", "state.isolate =", "state.include.set",
                      "state.labels.set", "captureUndo", "state.excluded",
                      "renderItemized", "renderChips", "calcStateDirty"]) {
  check(!code.includes(banned),
    `the card never calls/writes ${banned}`);
}
// the only state it owns
{
  const writes = (code.match(/^\s*(?:let |const )?(?:state\.)?[A-Za-z_$][\w.$]*\s*=(?!=)/gm) || [])
    .map(w => w.trim().replace(/\s*=$/, ""))
    .filter(w => !/^(const|let)\b/.test(w));
  const allowed = new Set(["lineCardOff", "lineCardFor",
    "el.textContent", "head.className", "sw.className", "nm.className",
    "nm.textContent", "x.type", "x.className", "x.textContent", "x.title",
    "x.onclick", "facts.className", "facts.textContent", "w.className",
    "w.textContent", "row.className", "tk.className", "tk.textContent",
    "hv.className", "hv.textContent", "ec.className", "ec.textContent",
    "ec.title", "el.hidden", "el.style.left", "el.style.top"]);
  const bad = writes.filter(w => !allowed.has(w));
  check(bad.length === 0,
    "every assignment in the block is to the card's own DOM or its own two flags"
    + (bad.length ? " — stray: " + bad.join(", ") : ""));
  check(!/state\.\w+\s*=(?!=)/.test(code),
    "nothing in the block assigns to state.* at all");
}
// it is a readout: the only interactive thing on it is the dismiss x
check((code.match(/\.onclick =/g) || []).length === 1,
  "exactly one click handler on the whole card (the dismiss x)");
check(/x\.onclick = \(e\) => \{ e\.stopPropagation\(\); lineCardOff = true; lineCardHide\(\); \}/
  .test(block), "...and it only hides the card");
check(/\.line-card \{[^}]*pointer-events:\s*none/.test(cssSrc),
  "the card takes no pointer, so it cannot eat a click meant for the sheet");
check(/\.line-card \.lc-x \{ pointer-events: auto/.test(cssSrc),
  "...except on the x");

// =========================================================================
// 2. IT IS ON THE LINE - the geometry, run
// =========================================================================
const helperSrc = (name) => { const a = src.indexOf("function " + name + "("); let d = 0; for (let j = src.indexOf("{", a); j < src.length; j++) { if (src[j] === "{") d++; else if (src[j] === "}" && --d === 0) return src.slice(a, j + 1); } return ""; };
const G = new Function("state", "IMG_DPI", "$", "chainById",
  // the anchor goes through the view's quarter turn (rotate a sideways page): the three helpers
  ["viewRot", "imgToRot", "rotToImg"].map(helperSrc).join("\n") + "\n" + block + " return { lineCardAnchor, lineCardPlace, lineCardHide };");

function harness(view, stageW, stageH, cardW, cardH) {
  const el = { hidden: false, offsetWidth: cardW, offsetHeight: cardH,
               style: {}, textContent: "x" };
  const stage = { clientWidth: stageW, clientHeight: stageH };
  const state = { view, selected: 1 };
  const $ = (id) => (id === "lineCard" ? el : id === "stage" ? stage : null);
  return [G(state, 130, $, () => null), el, state];
}
{
  // anchor: the MIDDLE point of the chain, through the live view transform
  const [F] = harness({ x: 100, y: 50, k: 2 }, 1000, 800, 200, 80);
  const ch = { points: [[0, 0], [36, 72], [999, 999]] };   // midpoint = [36,72]
  const at = F.lineCardAnchor(ch);
  // 36 pt * (130/72) px/pt * 2 + 100 = 230;  72 * 1.80555 * 2 + 50 = 310
  check(Math.abs(at[0] - 230) < 1e-6 && Math.abs(at[1] - 310) < 1e-6,
    `anchor is the chain midpoint through the view transform (got ${at})`);
  check(F.lineCardAnchor({ points: [] }) === null, "a pointless chain has no anchor");
}
{
  // default placement: up and to the right of the anchor, by the gap
  const [F, el] = harness({ x: 0, y: 0, k: 1 }, 1000, 800, 200, 80);
  F.lineCardPlace({ points: [[0, 0], [216, 216], [0, 0]] });  // anchor 390,390
  check(el.style.left === (390 + 18) + "px" && el.style.top === (390 - 80 - 18) + "px",
    `placed above-right of the line (got ${el.style.left},${el.style.top})`);
}
{
  // near the RIGHT edge it flips to the left instead of being clipped
  const [F, el] = harness({ x: 940, y: 400, k: 1 }, 1000, 800, 200, 80);
  F.lineCardPlace({ points: [[0, 0], [0, 0], [0, 0]] });      // anchor 940,400
  check(parseFloat(el.style.left) === 940 - 200 - 18,
    `flips to the left of the line at the right edge (got ${el.style.left})`);
  check(parseFloat(el.style.left) + 200 <= 1000 - 10,
    "...and lands wholly inside the stage");
}
{
  // near the TOP it flips below
  const [F, el] = harness({ x: 400, y: 20, k: 1 }, 1000, 800, 200, 80);
  F.lineCardPlace({ points: [[0, 0], [0, 0], [0, 0]] });      // anchor 400,20
  check(parseFloat(el.style.top) === 20 + 18,
    `flips below the line at the top edge (got ${el.style.top})`);
}
{
  // a corner: both flips at once, still fully inside
  const [F, el] = harness({ x: 995, y: 2, k: 1 }, 1000, 800, 200, 80);
  F.lineCardPlace({ points: [[0, 0], [0, 0], [0, 0]] });
  const L = parseFloat(el.style.left), T = parseFloat(el.style.top);
  check(L >= 10 && L + 200 <= 990 && T >= 10 && T + 80 <= 790,
    `top-right corner: the card is still wholly on the stage (got ${L},${T})`);
}
{
  // the line panned off the stage: clamped, never negative
  const [F, el] = harness({ x: -4000, y: -3000, k: 1 }, 1000, 800, 200, 80);
  F.lineCardPlace({ points: [[0, 0], [0, 0], [0, 0]] });
  check(parseFloat(el.style.left) === 10 && parseFloat(el.style.top) === 10,
    "a line panned far off screen clamps the card to the padding");
}
{
  // no selection and no chain: the card goes away rather than sticking
  const [F, el] = harness({ x: 0, y: 0, k: 1 }, 1000, 800, 200, 80);
  el.hidden = false;
  F.lineCardPlace(null);
  check(el.hidden === true, "nothing to anchor to: the card hides");
}

// =========================================================================
// 3. WIRED TO THE SELECTION, AND TO EVERY FRAME
// =========================================================================
check(/<div id="lineCard" class="line-card" role="status" aria-live="polite" hidden>/
  .test(htmlSrc), "#lineCard exists, and is an aria-live status region");
{
  const i = htmlSrc.indexOf('id="lineCard"');
  const stage = htmlSrc.indexOf('<section id="stage">');
  check(stage >= 0 && i > stage, "it lives inside #stage, over the sheet");
}
{
  const i = src.indexOf("function updateSelBar(ch) {");
  const body = src.slice(i, src.indexOf("\n  if (!ch) {", i));
  // ONE guarded call into the card, and nothing else: test_sel_elevation.js
  // evaluates updateSelBar in isolation against a hand-listed stub scope, and
  // that suite belongs to the branch reworking this bar's elevation chip.
  check(/if \(typeof lineCardOnSelect === "function"\) lineCardOnSelect\(ch\);/
    .test(body), "updateSelBar hands the selection to the card, guarded");
  check((body.match(/lineCard/g) || []).length === 2,
    "...and that guarded call is the ONLY mention of the card in the bar");
  const j = src.indexOf("function lineCardOnSelect(ch) {");
  const on = src.slice(j, src.indexOf("\n}", j));
  check(/if \(ch && ch\.chain_id !== lineCardFor\) lineCardOff = false/.test(on),
    "a NEW line un-dismisses the card; the same line stays dismissed");
  check(/lineCardFor = ch \? ch\.chain_id : null/.test(on),
    "...and the card remembers which line it is describing");
  check(/lineCardSync\(\)/.test(on), "...then rebuilds it");
}
check(/if \(typeof lineCardPlace === "function"\) lineCardPlace\(null\);/.test(src),
  "draw() re-places it every frame, so a pan or a zoom keeps it on its line");
{
  // it must come AFTER the canvas has finished painting, in the same frame
  const iDraw = src.indexOf("function draw() {");
  const iEnd = src.indexOf("if (typeof lineCardPlace ===", iDraw);
  const iRestore = src.lastIndexOf("ctx.restore();", iEnd);
  check(iEnd > iRestore && iRestore > iDraw,
    "...at the end of draw(), after the sheet is painted");
}

// =========================================================================
// 4. WHAT IT SAYS
// =========================================================================
check(/drawPatternSample\(sw, st, 52, 14\)/.test(block),
  "it shows the family's real pattern, the same sample the style row draws");
check(/\$\{ch\.class\} · \$\{fmtQty\(chainLenPt\(ch\)\)\} · \$\{ch\.points\.length\} points/
  .test(block), "class, length and point count, in the bar's own wording");
check(/in the takeoff/.test(block) && /NOT in the takeoff/.test(block),
  "it answers the question the operator is usually actually asking");
check(/hidden by the eye/.test(block),
  "...and says when the family is hidden but still counted (FAM-VIS)");
check(/wzActive\(\) && wf < 1/.test(block),
  "with a work area drawn, it says how much of the line actually counts");
// (the elevation chip left with the earthwork reader on 2026-09-25: the
// cut / fill engine shows every line's height on its own page)

// =========================================================================
// 5. THE SELECTION BAR IS UNTOUCHED
// =========================================================================
// Another branch is reworking the bar's elevation chip. This item must be
// purely additive to it: same element, same class, same children, same order.
{
  const i = src.indexOf("function updateSelBar(ch) {");
  const body = src.slice(i, src.indexOf("\nfunction ", i + 10));
  for (const kept of ['const txt = document.createElement("span");',
                      'txt.className = "sel-txt";'])
    check(body.includes(kept), `the bar still builds: ${kept}`);
  // THE CHIP MOVED, ON PURPOSE. The branch this section was written to stay
  // additive to has landed: the elevation is no longer a chip on this bar,
  // it is its own anchored readout (renderSelElev / #selElev), because on
  // the bar its screen position was a function of how long the description
  // happened to be. What this section still has to prove is what it always
  // meant - the line card did not disturb the bar, and did not take the
  // elevation with it.
  // (the elevation readout itself left with the earthwork reader, 2026-09-25)
  check(!/elc\.className = "sel-el"/.test(body),
    "while no longer building the chip on the bar itself (that moved)");
}
check(/id="selInfo" class="tk-info"/.test(htmlSrc),
  "#selInfo is unchanged in the markup");

console.log(failures ? `\n${failures} FAILED` : "\nall line-card checks passed");
process.exit(failures ? 1 : 0);
