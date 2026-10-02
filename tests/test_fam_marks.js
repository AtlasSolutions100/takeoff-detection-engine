// AN EYE, AND A SEPARATE MARK FOR "IN THE TAKEOFF".
//
// From the 2026-09-02 screen share, the owner's business partner: "Instead of
// checkboxes, there's one that's like an eye for on or off visually, aka
// rendered versus not rendered ... then another one which is some little
// symbol that means added to takeoff or not."
//
// Two facts rode one checkbox. famShown() read state.include for the canvas
// while every quantity read the same map for the numbers, so hiding a family
// to see under it and dropping it from the bid were literally the same click.
//
// THE THING THIS SUITE EXISTS TO PROTECT is that splitting them moved NOTHING.
// The owner and the partner discussed additive versus subtractive on the call
// and landed on leaving the defaults exactly as they are, so state.vis holds
// OVERRIDES ONLY and starts empty: with no eye ever clicked, famVis(sid) IS
// state.include.get(sid), and the app draws what it always drew - on the first
// paint, after memApply's inherited decisions, after a scope pass, after
// all/none. Section 2 below is that property, exhaustively.
//
// Also pinned:
//   * the two marks are two different controls, with aria-pressed and their
//     own titles - a second identical tick would be the same confusion again;
//   * every PICK path asks famShown (what you cannot see you cannot click),
//     and every QUANTITY path still asks state.include and nothing else;
//   * setFamVis re-couples: an override that agrees with the tick is deleted,
//     so the eye can always be put back rather than becoming a one-way trap.
//
// webui/app.js is not a module and touches the DOM at load, so this evals ONLY
// the marked FAM-VIS block, with `state` injected.
//
// Dependency-free:  node tests/test_fam_marks.js

"use strict";
const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(
  path.join(__dirname, "..", "webui", "app.js"), "utf8");
const cssSrc = fs.readFileSync(
  path.join(__dirname, "..", "webui", "style.css"), "utf8");

let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); }
  else console.log("ok:", msg);
}

const a = src.indexOf("// FAM-VIS-BEGIN");
const b = src.indexOf("// FAM-VIS-END");
check(a >= 0 && b > a, "FAM-VIS markers present in webui/app.js");
if (a < 0 || b <= a) process.exit(1);
const make = new Function("state",
  src.slice(a, b) + " return { famVis, famVisOverridden, setFamVis };");

function fresh(include) {
  const state = { include: new Map(Object.entries(include || {})), vis: new Map() };
  return [make(state), state];
}

// =========================================================================
// 1. THE MODEL
// =========================================================================
{
  const [F, st] = fresh({ a: true, b: false });
  check(F.famVis("a") === true, "a ticked family is shown");
  check(F.famVis("b") === false, "an unticked family is not");
  check(F.famVis("nosuch") === false, "an unknown family is not shown");
  check(F.famVisOverridden("a") === false, "nothing is overridden to start with");

  // hide something that IS in the takeoff
  F.setFamVis("a", false);
  check(st.vis.get("a") === false, "the override is recorded");
  check(F.famVis("a") === false, "...and the family is hidden");
  check(st.include.get("a") === true,
    "...and IT IS STILL IN THE TAKEOFF - the eye touches no number");
  check(F.famVisOverridden("a") === true, "...and it reads as overridden");

  // show something that is NOT in the takeoff
  F.setFamVis("b", true);
  check(F.famVis("b") === true, "an unticked family can be shown");
  check(st.include.get("b") === false, "...and it is still out of the takeoff");
  check(F.famVisOverridden("b") === true, "...and it reads as overridden");
}
// RE-COUPLING: an override that agrees with the tick is deleted, not stored.
// An eye you cannot put back is a trap, and a stored "true" that happens to
// match today would quietly diverge the next time a decision arrives from
// another sheet of the set.
{
  const [F, st] = fresh({ a: true });
  F.setFamVis("a", false);
  check(st.vis.size === 1, "one override");
  F.setFamVis("a", true);
  check(st.vis.size === 0, "clicking it back to agree DELETES the override");
  check(F.famVisOverridden("a") === false, "...so it follows the tick again");
  // and then a change to the tick carries the eye with it
  st.include.set("a", false);
  check(F.famVis("a") === false, "a re-coupled eye follows the takeoff mark");
}
// setting an override to the value it already has is idempotent
{
  const [F, st] = fresh({ a: true });
  F.setFamVis("a", false); F.setFamVis("a", false);
  check(st.vis.size === 1 && F.famVis("a") === false, "setting it twice is one override");
}

// =========================================================================
// 2. DEFAULTS DID NOT MOVE - the property this whole item rests on
// =========================================================================
{
  // Exhaustive over what a style row can be: ticked, unticked, and a style_id
  // state.include has never heard of (a row rendered before the seed lands).
  for (const v of [true, false, undefined]) {
    const [F] = fresh(v === undefined ? {} : { s: v });
    check(F.famVis("s") === !!v,
      `untouched, famVis === !!include for include=${String(v)}`);
  }
  // and over a whole sheet's worth, in the shape loadResult seeds
  const inc = {};
  for (let i = 0; i < 200; i++) inc["s" + i] = (i % 3 === 0);
  const [F] = fresh(inc);
  let same = 0;
  for (const k of Object.keys(inc)) if (F.famVis(k) === inc[k]) same++;
  check(same === 200,
    `200 styles, no eye touched: every one renders exactly as before (${same}/200)`);
}

// =========================================================================
// 3. famShown - the canvas's answer - and isolate
// =========================================================================
{
  const i = src.indexOf("function famShown(sid)");
  check(i >= 0, "famShown exists");
  const body = src.slice(i, src.indexOf("\n}", i));
  check(/famVis\(sid\)/.test(body), "famShown reads famVis, not state.include");
  check(!/state\.include/.test(body), "...and nothing else");
  check(/state\.isolate !== null \? sid === state\.isolate/.test(body),
    "isolate still wins over the eye, in both directions");
}

// =========================================================================
// 4. TWO CONTROLS, NOT TWO TICKS
// =========================================================================
check(!/cb\.type = "checkbox"; cb\.checked = !!state\.include\.get\(s\.style_id\)/
  .test(src), "the style row's checkbox is gone");
check(/function styleEyeBtn\(s\)/.test(src) && /function styleTakeBtn\(s\)/.test(src),
  "the row builds two distinct marks");
for (const [fn, cls] of [["styleEyeBtn", "mark-eye"], ["styleTakeBtn", "mark-take"]]) {
  const i = src.indexOf(`function ${fn}(s)`);
  const body = src.slice(i, src.indexOf("\n}\n", i));
  check(body.includes('b.type = "button"'), `${fn} builds a real button`);
  check(body.includes(cls), `${fn} carries .${cls}`);
  check(/setAttribute\("aria-pressed"/.test(body), `${fn} reports aria-pressed`);
  check(/setAttribute\("aria-label"/.test(body), `${fn} has an accessible name`);
  check(/b\.title =/.test(body), `${fn} says in words which fact it is`);
}
// the eye's own words have to make the no-number promise, because that is the
// exact thing the owner cannot verify for himself
{
  const i = src.indexOf("function styleEyeBtn(s)");
  const body = src.slice(i, src.indexOf("\n}\n", i));
  check(/no footage, no count and no/.test(body),
    "the eye's tooltip states that it changes no footage, count or price");
  check(/the takeoff is unchanged/.test(body),
    "...and so does the status line it writes when clicked");
  check(/setFamVis\(s\.style_id/.test(body) && !/setInclude\(/.test(body),
    "the eye writes ONLY state.vis - it can never reach state.include");
}
// the takeoff mark still runs the whole original tick path
{
  const i = src.indexOf("cb.onclick = (e) => { e.stopPropagation();");
  const body = src.slice(i, src.indexOf("draw(); };", i));
  check(/setInclude\(s\.style_id, on\)/.test(body), "the takeoff mark calls setInclude");
  check(/scopeKeepRow\(s, true\)/.test(body) && /scopeKeepRow\(s, false\)/.test(body),
    "...and the scope put-back arms are intact");
  check(/setIncludeSetWide\(s, cbChecked\)/.test(body),
    "...and a hand tick still speaks for the set");
}
// the key, once, above the rows, built from the SAME icon constants the rows
// use - so it can never draw a mark the rows do not
{
  check(/function styleMarksKey\(\)/.test(src), "there is a one-line key");
  const i = src.indexOf("function styleMarksKey()");
  const body = src.slice(i, src.indexOf("\n}\n", i));
  check(body.includes("EYE_OPEN") && body.includes("TAKE_IN"),
    "the key draws the same two glyphs the rows do");
  check(/shown on the drawing/.test(body) && /counted in the takeoff/.test(body),
    "...and names both facts in plain words");
  check(/box\.appendChild\(styleMarksKey\(\)\)/.test(src),
    "renderStyles puts it at the top of the list");
}
// the two off-states are legible as a system: both are the glyph plus a slash
{
  check(/const EYE_OFF\s*= EYE_OPEN\.concat\(\["M4 20 20 4"\]\)/.test(src),
    "the hidden eye is the eye plus a slash");
  check(/const TAKE_OUT = \["M4 7h8M4 12h6M4 17h5", "M4 20 20 4"\]/.test(src),
    "the out-of-takeoff mark is the list plus the SAME slash");
}

// =========================================================================
// 5. PICK PATHS FOLLOW THE EYE; QUANTITY PATHS DO NOT
// =========================================================================
// what you cannot see, you cannot click
for (const fn of ["chainHitAt", "snapToChain", "marqueePick", "stPickAt"]) {
  const i = src.indexOf(`function ${fn}(`);
  check(i >= 0, `${fn} exists`);
  const body = src.slice(i, src.indexOf("\n}\n", i));
  check(/famShown\(ch\.style_id\)/.test(body) || /famShown\(/.test(body),
    `${fn} gates on famShown`);
  check(!/!state\.include\.get\(ch\.style_id\)\) continue/.test(body),
    `${fn} no longer inlines the old include test`);
}
// no pick path anywhere still carries the inlined expression famShown replaced
check(!/state\.isolate !== null \? ch\.style_id !== state\.isolate\s*\n\s*:\s*!state\.include\.get\(ch\.style_id\)/
  .test(src), "no copy of the old isolate/include pick gate is left");
// and the numbers are untouched: these read state.include and only that
for (const fn of ["splitItems", "sheetOverCapture", "tradeLayerMap"]) {
  const i = src.indexOf(`function ${fn}(`);
  check(i >= 0, `${fn} exists`);
  const body = src.slice(i, src.indexOf("\n}\n", i));
  check(/state\.include\.get\(/.test(body),
    `${fn} still counts by state.include`);
  check(!/famVis\(|famShown\(/.test(body),
    `${fn} does NOT consult the eye - hiding a line must not move a number`);
}
// the on-canvas mini legend is a legend for the DRAWING, so it follows the eye
{
  const i = src.indexOf("function renderMiniLegend()");
  const body = src.slice(i, src.indexOf("\n}\n", i));
  check(/filter\(st => famVis\(st\.style_id\)\)/.test(body),
    "the mini legend lists what is drawn, not what is counted");
}

// the ghost pill must not keep saying "NOT counted": a family hidden by the
// EYE is ghosted on hover and IS still in the takeoff, and telling the owner
// otherwise reads as a quantity that silently left the bid
{
  const i = src.indexOf("function ghostBarSync()");
  const body = src.slice(i, src.indexOf("\n}\n", i));
  check(/const nIn = off\.filter\(sid => !!state\.include\.get\(sid\)\)\.length/.test(body),
    "the ghost pill counts how many of the ghosted families are still counted");
  check(/Hidden by the eye; still counted in the takeoff\./.test(body),
    "...and says so when they all are");
  check(/NOT counted in the takeoff\./.test(body),
    "...keeps the original sentence when none of them is");
  check(/hidden by the eye but still counted; /.test(body),
    "...and splits the count when the ghosted set is mixed");
  check(/ghostBarKey = key/.test(body) && /\+ "\|" \+ tail/.test(body),
    "the repaint key includes the sentence, so a change of meaning repaints");
}

// =========================================================================
// 6. THE OVERRIDES ARE PER PAGE
// =========================================================================
{
  const i = src.indexOf("function pageReset()");
  const body = src.slice(i, src.indexOf("\n}\n", i));
  check(/state\.vis = new Map\(\)/.test(body),
    "pageReset clears the eye overrides with the ticks they shadow");
  const iInc = body.indexOf("state.include = new Map()");
  const iVis = body.indexOf("state.vis = new Map()");
  check(iInc >= 0 && iVis > iInc && iVis - iInc < 200,
    "...right beside them, so the pair cannot be reset one without the other");
}

// =========================================================================
// 7. THE STYLING SAYS WHICH FACT IS WHICH
// =========================================================================
check(/\.style-row \{ grid-template-columns: 44px 46px 1fr auto auto auto; \}/
  .test(cssSrc), "the row's first column was widened for two marks");
check(/\.mark-take\.on \{ color: var\(--t-accent\)/.test(cssSrc),
  "in-the-takeoff is the accent - the colour this app already uses for counted");
check(/\.mark-eye\.on \{ color: var\(--t-text\)/.test(cssSrc),
  "the lit eye is the plain text colour, not a second accent");
check(/\.mark-eye\.ovr::after/.test(cssSrc),
  "an eye set by hand carries a mark of its own");
check(/\.style-row\.novis \.swatch-line \{ opacity/.test(cssSrc),
  "a hidden family's pattern swatch goes faint");
check(/\.style-row\.off \{ opacity/.test(cssSrc),
  "...and .off - not counted - is still a SEPARATE, unchanged treatment");
check(/\.mark:focus-visible \{ outline/.test(cssSrc), "both marks are keyboard-visible");

console.log(failures ? `\n${failures} FAILED` : "\nall eye/takeoff-mark checks passed");
process.exit(failures ? 1 : 0);
