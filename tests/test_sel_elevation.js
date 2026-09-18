// THE SELECTION PILL'S ELEVATION, AND WHY IT IS NOT A WORD IN A SENTENCE.
//
// Reported as "a selected contour stopped showing its elevation". The number
// was never missing: updateSelBar still computed it and still wrote it into
// #selInfo. It was CLIPPED. " · EL <z>" was the last token of the string in
// <span class="sel-txt">, and .sel-txt is the ONLY shrinkable child of that
// pill (overflow:hidden; text-overflow:ellipsis). Three flex:none controls
// landed on the same pill on 2026-08-30/31 - the family <select>, the "in
// list" button and the per-line colour swatch, 248.9 px between them - while
// .canvas-status stayed capped at a fraction of the canvas column. On a
// 1097 px window .sel-txt got 95 px for a 299 px string, so the tail went
// behind the ellipsis and the tail was the elevation.
//
// Measured on Oncor Cloudcrest, engine page index 3 = printed sheet C-04,
// contour chain 192, whose sheet prints 545 in the break of that dashed
// contour: DOM text "… · 62 points · EL 545", rendered as "…278 LF · 6…".
// Clipped at 1097 / 1280 / 1370 px, visible from ~1400 px up.
//
// WHAT IS PINNED HERE
//
//   1. THE STRING NO LONGER CARRIES THE NUMBER, and the chip does. Evaluated
//      for real against a fake DOM, because "is EL inside the shrinkable
//      span or outside it" is the entire bug and a source grep for "EL " is
//      satisfied by the broken version.
//   2. NOTHING RENDERS WHEN THERE IS NO ELEVATION. A non-contour selection
//      must not grow an empty pill.
//   3. THE CHIP CANNOT BE ELLIPSIZED. flex:none in CSS, and .sel-txt keeps
//      the ellipsis it is supposed to have - the point is which child gets
//      to shrink, so both halves are asserted.
//   4. ensureElev CARRIES THE GEN/JOB/PAGE GUARD that loadResult and poll
//      already carry. Without it a page switch mid-fetch leaves the previous
//      page's elevations in state.elevMap, and chain ids collide numerically
//      across pages - so a contour on the new sheet gets labelled with the
//      old sheet's elevation. A wrong number, not a missing one, and this
//      codebase's worst shape. Evaluated, not grepped.
//   5. THE FULL SENTENCE IS RECOVERABLE ON HOVER for whatever else clips.
//   6. THE CHIP SAYS WHERE THE NUMBER CAME FROM, AND SAYS IT HONESTLY.
//      Until 2026-08-31 the chip's tooltip read "read off the sheet's own
//      printed labels" for EVERY elevation, and an earlier version of this
//      file asserted that sentence was present - so the suite pinned a
//      claim that is false for a large minority of the rows.
//
//      Measured against the live service, Oncor Cloudcrest engine page
//      index 3 = printed sheet C-04 (GET /earthwork/suggest, re-measured
//      2026-09-01 on job e183a0d44d89): 150 suggestions, how = 69 gap-ocr
//      + 20 radius + 26 inherited + 35 interval-infill. 61 of 150 - 40.7% -
//      carry an elevation printed NOWHERE on the sheet: `inherited` was
//      carried across an end-to-end weld from another chain,
//      `interval-infill` was counted between two other contours.
//
//      THREE KINDS, NOT TWO (2026-09-01). `radius` was grouped with the
//      in-break reads and got a bare number, and that was the next lie
//      down: it is the NEAREST printed number within 16 pt, not this
//      line's own label. On chain 168 the nearest number, 2.7 pt away, is
//      "551.2 BW" - and this sheet's own legend prints "BW 100.0
//      PROPOSED BOTTOM OF WALL ELEVATION", so it is a retaining-wall spot
//      height, with a "550" contour leader a few points further off. 20 of
//      the 150 rows (13%) are radius. It is now its own kind, dotted, with
//      the distance - and the sheet's own tag when there is one - in the
//      chip TEXT.
//
//      So this suite asserts the DISTINCTION, in every direction and in the
//      chip's own visible text (not only its tooltip): a label read in the
//      line's break says nothing extra, a nearby number says how far and
//      what it is tagged, an inference says inferred, and a `how` the UI
//      does not recognise abstains instead of guessing any of them. A test
//      that only fails when somebody deletes a fixed sentence is worse than
//      no test - and a test whose fixture IS a false sentence is worse
//      still (see suite 5's infill relay).
//
// webui/app.js is not a module and touches the DOM at load, so the
// behavioural suites eval the extracted functions against a hand-built stub
// DOM - the same technique as MULTISELECT / LINE-FAMILY / EW-BUSY-STATE.
//
// Dependency-free:  node tests/test_sel_elevation.js

"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "webui", "app.js"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "webui", "style.css"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "webui", "index.html"), "utf8");

let fails = 0;
const check = (ok, msg) => {
  if (ok) console.log("  ok: " + msg);
  else { fails++; console.error("  FAIL: " + msg); }
};
const eq = (got, want, msg) =>
  check(got === want, msg + "  (got " + JSON.stringify(got) + ")");

function fnSrc(name) {
  let j = SRC.indexOf("\nfunction " + name + "(");
  if (j < 0) j = SRC.indexOf("\nasync function " + name + "(");
  if (j < 0) throw new Error("function not found in app.js: " + name);
  let d = 0;
  for (let k = SRC.indexOf("{", j); k < SRC.length; k++) {
    if (SRC[k] === "{") d++;
    else if (SRC[k] === "}" && --d === 0) return SRC.slice(j + 1, k + 1);
  }
  throw new Error("unbalanced braces reading " + name);
}

// The provenance table and its lookup, lifted whole: updateSelBar calls
// elevProvenance, and the point of this suite is what that table decides.
const PROV_SRC = (function () {
  const a = SRC.indexOf("const ELEV_PROV = {");
  if (a < 0) throw new Error("ELEV_PROV not found in app.js");
  const f = SRC.indexOf("\nfunction elevProvenance(how) {", a);
  if (f < 0) throw new Error("elevProvenance not found in app.js");
  return fnSrc("elevRadiusMark") + "\n" + SRC.slice(a, f)
       + fnSrc("elevProvenance")
       + "\n" + fnSrc("elevProvenanceDetail")
       + "\n" + fnSrc("elevChipTag");
})();
const elevProvenance = new Function(PROV_SRC + "\nreturn elevProvenance;")();

// The anchored readout, lifted the same way. On a build that does not have
// it this compiles to a no-op, so the suites below report WHICH half of the
// contract is missing instead of dying inside fnSrc.
const SELELEV_SRC = SRC.indexOf("\nfunction renderSelElev(") >= 0
  ? fnSrc("fmtElapsed") + "\n" + fnSrc("elevReadElapsed") + "\n"
    + fnSrc("renderSelElev")
  : "function renderSelElev() {}";

// --- the smallest DOM updateSelBar can run against -------------------------
function elem(tag) {
  return {
    tagName: tag, className: "", textContent: "", title: "", type: "",
    hidden: false, disabled: false, style: {}, children: [],
    appendChild(c) { this.children.push(c); return c; },
    setAttribute() {}, querySelector() { return null; },
    get _text() {
      return this.textContent
        + this.children.map(c => c._text !== undefined ? c._text : "").join("");
    },
  };
}
function byClass(node, cls) {
  return node.children.filter(c => (c.className || "").split(" ").indexOf(cls) >= 0);
}

function renderAll(opts) {
  const els = {};
  // aiBtn is in this list because updateSelBar gates it on state.result, the
  // same way it gates drawBtn - see the comment there and suite A of
  // tests/test_page_reset.js
  for (const id of ["splitBtn", "extendBtn", "sepBtn", "cutSpanBtn",
                    "flattenBtn", "drawBtn", "removeBtn", "selInfo",
                    "aiBtn", "selElev"]) els[id] = elem("div");
  const state = {
    result: { styles: [{ style_id: 7, suggested_label: "dash1 d18.0 g51.7" }] },
    chainLabels: new Map(), labels: new Map(),
    selPt: null, selected: null,
    elev: opts.elev,
  };
  const ch = { chain_id: 192, style_id: 7, class: "dash1",
               points: new Array(62).fill([0, 0]) };
  const scope = {
    $: (id) => els[id],
    state,
    document: { createElement: elem },
    ensureElev: () => {},
    chainById: () => ch,
    chainLenPt: () => 278,
    fmtQty: (v) => v + " LF",
    wzChainFrac: () => 1,
    wzActive: () => false,
    chainCountedPt: () => 0,
    familySelect: () => { const e = elem("select"); e.className = "fam-sel"; return e; },
    familySwitch: () => {}, famNewFromSelection: () => {},
    revealStyleRow: () => {},
    chainColorInfo: () => ({ src: "engine", css: "#888" }),
    colorSrcLabel: () => "x", colorPopForChain: () => {},
    ELEV_FAIL: "could not read this sheet's elevations \u2014 retry",
  };
  const names = Object.keys(scope);
  // renderSelElev is compiled INTO the same scope, exactly as it lives in
  // app.js. This suite is about WHICH ELEMENT each half writes into, so
  // neither half may be stubbed out.
  new Function(...names,
    PROV_SRC + "\n" + SELELEV_SRC + "\n" + fnSrc("updateSelBar")
    + "\nreturn updateSelBar;")
    (...names.map(n => scope[n]))(opts.noChain ? null : ch);
  return { info: els.selInfo, elev: els.selElev };
}
const renderBar = (opts) => renderAll(opts).info;
// The number itself, wherever it now lives - the suites below are about what
// it SAYS; suite 1 is the one about where it is.
function chipEl(opts) {
  return byClass(renderAll(opts).elev, "sel-el")[0];
}
// The shape ensureElev actually stores. Two rules in one helper: the state
// is a status + a map, never a sentinel, and the map's value is an object,
// never a bare number.
const ELEV = (z, how, src, dist, tag) => ({
  status: "ready",
  byChain: new Map([[192, { z, how, src: src || "",
                            dist: dist === undefined ? null : dist,
                            tag: tag || null }]]),
});

// ---------------------------------------------------------------------------
console.log("1. the elevation is not on the selection pill at all");
// ---------------------------------------------------------------------------
// THE MECHANISM, not the symptom. Nothing was ever clipped: measured live on
// Oncor Cloudcrest engine page index 3 = printed C-04, the chip rendered
// 166/166 px visible in all 24 of {1280,1366,1440,1920} px x {100,110,125}%
// zoom, panel open and closed, with a 24-character family name - down to a
// 1024 px CSS viewport where .sel-txt was squeezed to 29.8 px. It could not
// be the clipped child either: #selInfo overflows to the RIGHT and the chip
// was its SECOND child, so "in list" and the colour swatch were the two
// things behind the ellipsis.
//
// What it did was MOVE. .canvas-status is bottom-anchored, CENTRED
// (left:50% + translateX(-50%)) and flex-wrap:wrap, and the pill's width
// changes with the description, the provenance wording and which of its five
// children exist. Over 14 contours at 1536 px the chip's left edge ranged
// 920.4 -> 1023.0 px; hovering any style row put #ghostInfo in the same
// wrapping row and lifted the chip 785.4 -> 747.4 px ("it just moved ... now
// it's on the bottom"). So the fix is not more room on that bar - it is a
// place of its own, and that is what is pinned here.
{
  const r = renderAll({ elev: ELEV(545, "inline") });
  const txt = byClass(r.info, "sel-txt")[0];

  check(!!txt, "the pill still has its .sel-txt sentence");
  eq(byClass(r.info, "sel-el").length, 0,
    "THE FIX: no elevation chip is appended to the selection pill - it "
    + "cannot be moved by a sibling that is not there");
  check(r.info._text.indexOf("EL ") < 0,
    "...and no part of the pill carries 'EL '");
  check(txt.textContent.indexOf("EL") < 0,
    "the elevation is NOT inside .sel-txt either - that span is the only "
    + "child that can shrink, so its tail is what an ellipsis eats");
  check(txt.textContent.indexOf("278 LF") >= 0
        && txt.textContent.indexOf("62 points") >= 0,
    "the rest of the sentence is unchanged");

  const chip = byClass(r.elev, "sel-el")[0];
  check(!!chip, "the elevation is rendered into #selElev instead");
  eq(chip && chip.textContent, "EL 545", "...and that box carries the number");
  eq(r.elev.hidden, false, "...and the box is shown");
  const cap = byClass(r.elev, "se-cap")[0];
  check(!!cap && /elevation/i.test(cap.textContent),
    "a constant caption names it, so there is one place to learn to look");
}
{
  // nothing selected: the box goes away with the pill, one writer for both
  const r = renderAll({ elev: ELEV(545, "inline"), noChain: true });
  eq(r.elev.hidden, true, "no selection, no readout");
  eq(r.info.textContent, "", "...and no pill either");
}

// ---------------------------------------------------------------------------
console.log("2. no number is a SENTENCE, never a blank");
// ---------------------------------------------------------------------------
// THE OTHER HALF OF THE REPORT: "some of these you click and it says the
// height and some it doesn't". On C-04 that is literally true - 439 of 589
// chains, and 110 of the 218 in a contour family, have no elevation - and
// the old code rendered NOTHING for them. So "this line has none", "the read
// is still running" (217.2 s cold on that sheet) and "the read failed" were
// one and the same blank, and the operator could only read it as "the number
// is hiding somewhere".
{
  const CASES = [
    ["a line the sheet never labelled",
     { status: "ready", byChain: new Map([[999, { z: 12, how: "radius" }]]) },
     /none printed for this line/i],
    ["answered, and the sheet labels no contours at all",
     { status: "ready", byChain: new Map() }, /none printed for this line/i],
    ["still loading", { status: "loading", byChain: null }, /reading/i],
    ["never asked", { status: "idle", byChain: null }, /not read yet/i],
    ["the read FAILED", { status: "failed", byChain: null }, /failed/i],
  ];
  const said = [];
  for (const [name, ev, want] of CASES) {
    const r = renderAll({ elev: ev });
    eq(byClass(r.elev, "sel-el").length, 0, name + ": no number is claimed");
    const note = byClass(r.elev, "se-none")[0];
    check(!!note, name + ": ...but the box still says something");
    check(!!note && want.test(note.textContent),
      name + ": and it says which state this is  (got "
      + JSON.stringify(note && note.textContent) + ")");
    check(!!note && note.title && note.title.length > 40,
      name + ": with the long form on hover");
    if (note) said.push(note.textContent);
    eq(byClass(r.info, "sel-el").length, 0, name + ": still nothing on the pill");
  }
  // loading / failed / none / not-asked are FOUR facts. Two of them reading
  // the same is the bug this suite exists to stop coming back.
  eq(new Set(said).size, 4,
    "the five states produce four distinct sentences (the two `ready` arms "
    + "are one fact) - THE FIXED DEFECT: they were all the empty string");
}
{
  // z present but null - the server does emit null z on spot grades, and an
  // "EL null" readout would be worse than the sentence
  const r = renderAll({ elev: ELEV(null, "radius") });
  eq(byClass(r.elev, "sel-el").length, 0, "a null elevation claims no number");
  eq(byClass(r.elev, "se-none").length, 1, "...and says so instead");
}
{
  // 0 is a real elevation. It is falsy; the guard must be a null/undefined
  // test, not truthiness (CLAUDE.md 5: "0.0 is not None").
  const chip = chipEl({ elev: ELEV(0, "inline") });
  check(!!chip, "elevation 0 still renders - falsy is not missing");
  eq(chip && chip.textContent, "EL 0", "...and reads EL 0");
}

// ---------------------------------------------------------------------------
console.log("3. the readout's position cannot depend on anything else");
// ---------------------------------------------------------------------------
// The geometry, pinned as the rule that produced it. A box whose offsets are
// two constants against a corner has a position that is a constant; a flex
// child of a centred, wrapping row does not.
{
  check(/id="selElev"/.test(HTML), "#selElev exists in the markup");
  // it is a child of the sheet area, NOT of the pill row that wraps
  const stage = HTML.slice(HTML.indexOf('<section id="stage">'));
  const status = stage.indexOf('class="canvas-status"');
  const elev = stage.indexOf('id="selElev"');
  check(elev >= 0 && (status < 0 || elev < status),
    "#selElev is a sibling of .canvas-status, never inside it");

  const rule = (CSS.match(/\n\.sel-elev \{[^}]*\}/) || [""])[0];
  check(/position:\s*absolute/.test(rule), ".sel-elev is positioned");
  check(/[;{\s]top:\s*\d+px/.test(rule) && /[;{\s]right:\s*\d+px/.test(rule),
    "...against a CORNER, in constant px - so its position is the same at "
    + "every window width and every browser zoom");
  check(!/left:\s*50%/.test(rule) && !/translateX/.test(rule),
    "...and NOT centred - centring is what made it move when the pill's "
    + "width changed (920.4 -> 1023.0 px over 14 contours)");
  check(!/flex-wrap:\s*wrap/.test(rule),
    "...and it does not share a wrapping row with anything");
  check(/[;{\s]width:\s*\d+px/.test(rule),
    "...and its WIDTH is a constant too - right-anchored but shrink-to-fit, "
    + "its left edge still moved 1288.9 -> 1389.0 px over the same 14 "
    + "contours, because the provenance wording changes length");
  check(/align-items:\s*flex-start/.test(rule),
    "...with the content left-aligned inside it, so the digits start on one "
    + "pixel rather than ending on one");
  check(/max-width/.test(rule),
    "it is capped at the sheet area, so a long provenance sentence wraps "
    + "inside it instead of pushing anything off screen");
  const zi = (rule.match(/z-index:\s*(\d+)/) || [])[1];
  const csZ = ((CSS.match(/\n\.canvas-status \{[^}]*\}/) || [""])[0]
               .match(/z-index:\s*(\d+)/) || [])[1];
  check(zi && csZ && +zi >= +csZ,
    "it sits at or above the pill in the stack, so nothing on the sheet "
    + "overlay covers it  (got " + zi + " vs " + csZ + ")");
  // and the number itself still cannot be squeezed or ellipsized
  const selEl = (CSS.match(/\n\.sel-el \{[^}]*\}/) || [""])[0];
  check(/flex:\s*none/.test(selEl), ".sel-el is flex:none");
  check(!/text-overflow/.test(selEl), ".sel-el has no ellipsis of its own");
  const selTxt = (CSS.match(/\n\.sel-txt \{[^}]*\}/) || [""])[0];
  check(/text-overflow:\s*ellipsis/.test(selTxt),
    ".sel-txt keeps its ellipsis - the pill's own sentence may still clip, "
    + "and the number is no longer on that bar to clip with it");
  // the source must not quietly grow the chip back onto the pill
  const bar = fnSrc("updateSelBar");
  check(!/"sel-el"/.test(bar) && !/'sel-el'/.test(bar),
    "updateSelBar builds no .sel-el of its own - THE FIXED DEFECT");
  check(/renderSelElev\(/.test(bar),
    "...it delegates the elevation to the anchored readout");
}

// ---------------------------------------------------------------------------
console.log("4. the whole sentence is recoverable on hover");
// ---------------------------------------------------------------------------
{
  const info = renderBar({ elev: ELEV(545, "gap-ocr") });
  const txt = byClass(info, "sel-txt")[0];
  eq(txt.title, txt.textContent,
    "whatever DOES clip (a long family label, the point count) is on the title");
}

// ---------------------------------------------------------------------------
console.log("5. the chip says where the number came from - printed vs not");
// ---------------------------------------------------------------------------
// The five `how` values /earthwork/suggest emits, and what each one means.
// Counts are the live C-04 read (150 rows) recorded in the header.
// A LABEL READ OUT OF THIS LINE'S OWN BREAK is the only thing that gets a
// bare number. `radius` used to be in here and that was the defect: it is
// the nearest printed number within 16 pt, and on C-04 chain 168 the
// nearest number 2.7 pt away is "551.2 BW" - a wall spot height keyed in
// the sheet's OWN legend ("BW 100.0  PROPOSED BOTTOM OF WALL ELEVATION"),
// with a "550" contour leader a few points further off. The chip read a
// bare "EL 551.2" with a solid border, indistinguishable from a real
// in-break read, and every caveat lived in the tooltip.
const PRINTED = ["inline", "gap-ocr"];
const NOT_PRINTED = ["inherited", "interval-infill"];
for (const how of PRINTED) {
  const p = elevProvenance(how);
  eq(p.kind, "printed", how + " is a label the sheet actually prints");
  const chip = chipEl({ elev: ELEV(545, how) });
  check(!/inferred|unknown/.test(chip.className),
    how + ": and carries no inferred/unknown class");
  check(chip.textContent.indexOf("EL 545") === 0,
    how + ": the chip leads with the number");
}
{
  // ONLY A TEXT-LAYER READ GETS A BARE NUMBER.
  const chip = chipEl({ elev: ELEV(545, "inline") });
  eq(chip.textContent, "EL 545",
    "inline: nothing is added - the number IS the sheet's own text");
  eq(chip.className, "sel-el", "inline: and the chip is unadorned");
}
// ---------------------------------------------------------------------------
console.log("5c. an OCR'd elevation does not look like a printed one");
// ---------------------------------------------------------------------------
// `inline` and `gap-ocr` make the same claim about the SOURCE - the sheet
// prints a label in this contour's own break - and it is TRUE for both. They
// do NOT make the same claim about the DIGITS: `inline` copies text out of
// the PDF, `gap-ocr` transcribes a raster because the drafter exploded the
// label to vectors, and a transcription can disagree with what is printed.
//
// THE ROUND THAT ADDED THIS CHIP ALSO ADDED A FALSE SENTENCE AND PINNED IT
// HERE. The tooltip read "The sheet DOES print THIS number in the break in
// this contour - that part is not in doubt", which asserts that the digits
// on screen are the digits on the sheet - the one thing an OCR pass cannot
// promise, and false on the very row the same round recorded as a misread
// (chain 417 below). The assertion under it, /DOES print/, was satisfied by
// the false wording. It now pins "a number" and REJECTS "this number".
//
// MEASURED live on Oncor C-04 (job e183a0d44d89, fresh detect 2026-09-01):
// gap-ocr is 69 of 150 rows - 46%, the LARGEST bucket - and the one class
// where the digits can be wrong. Confirmed wrong on that sheet: chain 417,
// label_xy (2100.7, 1484.2), returned 551.0, where the source PDF at 600 dpi
// prints a collapsed stack reading 582 / 582 / 581 / 579 / 580 and no 551.
//
// Until 2026-09-01 both rendered a bare "EL 545" with a solid border and the
// whole caveat lived in a tooltip. The caveat now travels ON the number.
{
  const ocr = chipEl({ elev: ELEV(545, "gap-ocr") });
  const inl = chipEl({ elev: ELEV(545, "inline") });
  check(ocr.textContent !== inl.textContent,
    "THE FIXED DEFECT: an OCR'd elevation no longer renders byte-identical "
    + "to one read from the text layer");
  eq(ocr.textContent, "EL 545 · read from a picture",
    "the caveat is in the CHIP TEXT, in the same grammar as "
    + "'· inferred' and '· source unknown'");
  check(/\bocr\b/.test(ocr.className),
    "...and a class, so the styling has something to hang on");
  // IT MUST NOT OVERCLAIM IN THE OTHER DIRECTION. The provenance is true:
  // the label really is printed in this line's break. Calling it inferred
  // would be a fresh lie, and the whole point is that the risk is
  // TRANSCRIPTION, not source.
  check(!/inferred/.test(ocr.textContent) && !/inferred/.test(ocr.className),
    "it is NOT marked inferred - the sheet does print a label in this break");
  eq(elevProvenance("gap-ocr").kind, "printed",
    "...and it still counts as printed wherever the split is taken by kind");
  check(!/picture/.test(inl.textContent),
    "a text-layer read carries no such caveat, so the two are told apart");
  // the border vocabulary is unchanged: solid = this line's own label.
  // gap-ocr keeps it, because that claim is the one that is true.
  const cssOcr = (CSS.match(/\n\.sel-el\.ocr \{[^}]*\}/) || [""])[0];
  check(!!cssOcr, "the OCR chip has a rule of its own");
  check(!/border-style/.test(cssOcr),
    "...which does NOT change the border style: solid still means 'read in "
    + "this line's own break', and for an OCR'd label that is still true");
  check(/border-color/.test(cssOcr) && /background/.test(cssOcr),
    "...it tints instead, so the difference is visible without lying about "
    + "the source");
  // and the tooltip says which half is uncertain
  const t = ocr.title;
  check(/(DOES|does) print a number/.test(t),
    "the tooltip affirms the SOURCE - the sheet does print a number in this "
    + "break - rather than casting doubt on it");
  check(!/(DOES|does) print this number/.test(t)
        && !/(DOES|does) print the number (shown|above)/.test(t),
    "...and does NOT claim the sheet prints THIS number: the digits on "
    + "screen are the reader's, and on Oncor C-04 chain 417 they were 551 "
    + "where the sheet prints 582 / 582 / 581 / 579 / 580");
  check(/read by machine|machine|OCR|picture/i.test(t)
        && /(disagree|wrong|misread|prints nowhere)/i.test(t),
    "...and names the transcription as the thing that can fail");
}
for (const how of NOT_PRINTED) {
  const p = elevProvenance(how);
  eq(p.kind, "inferred", how + " is NOT printed anywhere on the sheet");
  const chip = chipEl({ elev: ELEV(580, how) });
  check(chip && /inferred/.test(chip.textContent),
    how + ": the word is in the CHIP TEXT, where the number is - not only "
    + "in a tooltip nobody opens");
  eq(chip && chip.textContent, "EL 580 · inferred", how + ": exact chip text");
  check(/\binferred\b/.test(chip.className),
    how + ": ...and a class, so the tell survives greyscale (dashed border)");
  check(/NOT printed/.test(chip.title),
    how + ": the tooltip states it plainly too");
  check(!/read off (the )?(sheet|a label)/i.test(chip.title),
    how + ": and it must NOT claim the sheet printed it - THE FIXED DEFECT");
}
{
  // the two mechanisms are genuinely different and the tooltip must not
  // blur them: a weld that joined the wrong two objects is a different
  // failure from a miscount, and only one of them has a server sentence
  const a = elevProvenance("inherited").why;
  const b = elevProvenance("interval-infill").why;
  check(a !== b, "the two inferred mechanisms explain themselves differently");
  check(/join/i.test(a), "inherited says the value was carried across a join");
  check(/counted between/i.test(b), "interval-infill says it was counted");
  // THE SERVER'S SENTENCE RIDES THROUGH VERBATIM, AND THAT IS THE POINT:
  // the UI must relay what the reader actually claimed and must never
  // upgrade it. An earlier version of this suite used
  //   "counted between labelled 579 (line 140) and 583 (line 282)"
  // as its fixture - a sentence that is FALSE on the sheet it was measured
  // from. On Oncor C-04 chains 140 and 282 are both `inherited`: no label
  // is printed on either, and 13 of the 35 infilled rows cited an anchor
  // like that. So the suite carried the false claim as its own example of
  // correct output. The server now words each anchor by what backs it
  // (earthwork.infill_intervals `anchor_how`, asserted in
  // tests/test_ew_suggest.py); the fixtures below are the two sentences it
  // can really produce, and what is pinned here is that the UI carries the
  // difference through instead of flattening it.
  const backed = chipEl({
    elev: ELEV(580, "interval-infill",
      "counted between labelled 579 (line 140) and labelled 583 (line 282) "
      + "at the sheet's 1 ft contour interval - not a printed label, "
      + "confirm against the sheet"),
  });
  check(/line 140/.test(backed.title) && /line 282/.test(backed.title),
    "the server's src sentence rides along in the tooltip");
  const unbacked = chipEl({
    elev: ELEV(580, "interval-infill",
      "counted between 579 (line 140, itself unlabelled) and 583 "
      + "(line 282, itself unlabelled) at the sheet's 1 ft contour "
      + "interval - NEITHER end carries a printed label, confirm against "
      + "the sheet"),
  });
  check(/itself unlabelled/.test(unbacked.title)
        && /NEITHER end/.test(unbacked.title),
    "an anchor that carries no printed label says so, through the UI");
  check(!/labelled 579/.test(unbacked.title),
    "...and the UI adds no 'labelled' the server did not say - THE FIXED "
    + "DEFECT was a fixture that asserted pass-through of a false sentence");
}
// ---------------------------------------------------------------------------
// 5b. THE CLIENT'S STATIC HALF MUST NOT CONTRADICT THE SERVER SENTENCE IT IS
//     GLUED TO. The chip title is `prov.why + elevProvenanceDetail(ev)` - one
//     string, two authors. The static half read "counted between two LABELLED
//     contours" and the server half, appended in the same breath, frequently
//     says the ends are NOT labelled.
//
//     MEASURED live, Oncor Cloudcrest engine page index 3 = printed sheet
//     C-04 (job e183a0d44d89, fresh detect, 2026-09-01, GET
//     /earthwork/suggest): 35 interval-infill rows, anchors_printed 2 on 22
//     rows, 1 on 2 rows, 0 on 11. So 13 of 35 (37%) carried a tooltip whose
//     first sentence asserted "labelled" over its own second sentence saying
//     one or neither end is.
//
//     Pinned two ways, because pinning the wording alone would only catch
//     somebody deleting today's sentence:
//       (a) the STATIC half may not make a positive claim about the anchors
//           at all - it cannot know, it is the same string on every row;
//       (b) COMPOSED with each of the three sentences the server can really
//           produce, the tooltip may not be self-contradictory.
// ---------------------------------------------------------------------------
{
  const why = elevProvenance("interval-infill").why;
  const POSITIVE =
    /\btwo\s+labell?ed\b|\bboth\s+labell?ed\b|\blabell?ed\s+(contours|anchors|ends)\b/i;
  check(!POSITIVE.test(why),
    "the static half makes NO positive claim that the anchors are labelled - "
    + "it is the same sentence on all 35 rows and on 13 of them that claim "
    + "is false  (got: " + JSON.stringify(why.slice(0, 90)) + "…)");

  // the three sentences earthwork.infill_intervals can emit, verbatim in
  // shape (see _anchor / tail there and tests/test_ew_suggest.py)
  const SRC = [
    ["both ends labelled",
     "counted between labelled 579 (line 140) and labelled 583 (line 282) at "
     + "the sheet's 1 ft contour interval - not a printed label, confirm "
     + "against the sheet"],
    ["one end labelled",
     "counted between labelled 579 (line 140) and 583 (line 282, itself "
     + "unlabelled) at the sheet's 1 ft contour interval - one end carries "
     + "no printed label either, confirm against the sheet"],
    ["neither end labelled",
     "counted between 579 (line 140, itself unlabelled) and 583 (line 282, "
     + "itself unlabelled) at the sheet's 1 ft contour interval - NEITHER "
     + "end carries a printed label, confirm against the sheet"],
  ];
  for (const [name, src] of SRC) {
    const chip = chipEl({
      elev: ELEV(580, "interval-infill", src) });
    const t = chip.title;
    check(t.indexOf(src) >= 0, name + ": the server's sentence rides through");
    const serverDenies = /itself unlabelled|NEITHER end|no printed label/i.test(t);
    check(!(serverDenies && POSITIVE.test(t)),
      name + ": the tooltip does not assert 'labelled' and deny it in the "
      + "same breath - THE FIXED DEFECT (13 of C-04's 35 infill rows)");
  }
}
{
  // A RADIUS HIT IS ITS OWN KIND, AND THE CAVEAT IS ON THE NUMBER.
  // "printed" is a claim about the NUMBER, never about whose number it is.
  // Measured live on C-04 (job e183a0d44d89, engine page index 3 = printed
  // sheet C-04) chain 168: z 551.2, how "radius", dist 2.7, label_xy
  // (429.6, 733.0). Read straight out of the PDF at that point the text is
  // "551.2" followed on the SAME text row by "BW", with "556.1 TW" above
  // it; and the sheet's own LEGEND prints "BW 100.0  PROPOSED BOTTOM OF
  // WALL ELEVATION" / "TW 100.0  PROPOSED TOP OF WALL ELEVATION". So that
  // number is a retaining-wall spot height, not this contour's label.
  // GLOSSARY (read off this sheet's legend, not assumed): BW = bottom of
  // wall, TW = top of wall - spot heights, not contour labels.
  const p = elevProvenance("radius");
  eq(p.kind, "nearby",
    "radius is neither a label read nor an inference - its own third kind");
  const chip = chipEl({ elev: ELEV(551.2, "radius", "", 2.7) });
  eq(chip.textContent, "EL 551.2 \u00b7 label 2.7 pt away",
    "the distance is in the CHIP TEXT - THE FIXED DEFECT: it used to read a "
    + "bare 'EL 551.2', solid-bordered, with the caveat in a tooltip");
  check(/\bnearby\b/.test(chip.className),
    "...and a class, so the tell survives greyscale (dotted border)");
  check(!/inferred/.test(chip.textContent),
    "it does not overclaim either: the number IS printed somewhere");

  // THE SHEET'S OWN TAG, whenever the matched token carries one.
  const tagged = chipEl({
    elev: ELEV(551.2, "radius", "", 2.7, "BW") });
  eq(tagged.textContent, "EL 551.2 \u00b7 \u201cBW\u201d label 2.7 pt away",
    "a tagged number shows the sheet's own tag ON the number");
  check(/BW/.test(tagged.title) && /legend/i.test(tagged.title),
    "...and the tooltip sends the reader to the sheet's legend for it");
  check(!/bottom of wall/i.test(tagged.title),
    "...and does NOT expand the abbreviation: the sheet's legend is where "
    + "its meaning lives, and inventing one is what CLAUDE.md §4 forbids");
  check(/assum/i.test(chip.title),
    "the tooltip calls the ownership an assumption, not a reading");
  check(!/^Read off a label/.test(chip.title),
    "...and never says the sheet printed it FOR THIS LINE");

  const far = chipEl({ elev: ELEV(551.2, "radius", "", null) });
  check(!/pt from this line/.test(far.title),
    "with no distance recorded it invents none");
  eq(far.textContent, "EL 551.2 \u00b7 label nearby, not in the break",
    "...and still says the number is not this line's own label");
}
{
  // ABSTAIN. A `how` this UI has not been taught must not be guessed either
  // way - not "printed" (the old lie) and not "inferred" (a new one).
  for (const [name, map] of [
    ["an unrecognised how", ELEV(545, "some-new-reader")],
    ["no how at all", ELEV(545, undefined)],
    ["a bare number, the pre-fix shape",
     { status: "ready", byChain: new Map([[192, 545]]) }],
  ]) {
    const chip = chipEl({ elev: map });
    check(!!chip, name + ": the number is still shown");
    eq(chip.textContent, "EL 545 · source unknown",
      name + ": ...and the chip says it does not know where it came from");
    check(!/read off/i.test(chip.title) && !/NOT printed/.test(chip.title),
      name + ": the tooltip claims neither printed nor inferred");
  }
  eq(elevProvenance("some-new-reader").kind, "unknown",
    "an unknown reader tag resolves to the abstain row");
}
{
  // the honest vocabulary is the one the app already uses one screen away:
  // the earthwork panel's per-row badge. One word for one distinction.
  check(/ew-inf-tag/.test(SRC) && /"inferred"/.test(SRC),
    "the earthwork row badge still exists - the chip reuses its word");
  check(/\.sel-el\.inferred \{[^}]*border-style:\s*dashed/.test(CSS),
    "the inferred chip is dashed, like the earthwork panel's inferred input");
  check(/\.sel-el\.unknown \{[^}]*border-style:\s*dashed/.test(CSS),
    "and so is the abstain chip");
}

// ---------------------------------------------------------------------------
console.log("6. ensureElev: page guard, three real states, and a retry");
// ---------------------------------------------------------------------------
// state.elev used to be `elevMap`, one value doing three jobs: undefined =
// never asked, null = in flight, Map = answered. Its catch wrote
// `new Map()`, so a dropped connection was byte-identical to "this sheet
// labels no contours" - and because the entry guard was
// `if (state.elevMap !== undefined) return`, that verdict was PERMANENT.
// 182.4 s of silence on a cold Oncor C-04 with no way to ask again.
const BODY = { suggestions: [
  { chain_id: 192, z: 545, how: "gap-ocr" },
  { chain_id: 95, z: 580, how: "interval-infill", inferred: true,
    src: "counted between labelled 579 and 583" },
] };
function ensureScope(opts) {
  const o = opts || {};
  const state = { elev: { status: "idle", byChain: null },
                  gen: 1, job: "J", page: 3, selected: null };
  const said = [];
  let calls = 0;
  const scope = {
    state,
    suggestOnce: async () => {
      calls++;
      if (o.mid) o.mid(state);
      if (o.fail && calls <= (o.failTimes === undefined ? 1e9 : o.failTimes))
        throw new Error("network");
      return BODY;
    },
    setStatus: (t, cls) => said.push({ t, cls }),
    ELEV_FAIL: "could not read this sheet's elevations — retry",
    EW_SUGGEST_TIMEOUT: "no answer in 12 minutes — press EARTHWORK to retry",
    updateSelBar: () => {},
    chainById: () => null,
  };
  const names = Object.keys(scope);
  const fn = new Function(...names, fnSrc("ensureElev") + "\nreturn ensureElev;")
    (...names.map(n => scope[n]));
  return { state, said, fn, calls: () => calls };
}
function runEnsure(mid) {
  const s = ensureScope({ mid });
  return s.fn().then(() => s.state);
}
runEnsure(null).then(st => {
  eq(st.elev.status, "ready", "the happy path lands in `ready`");
  const got = st.elev.byChain.get(192);
  check(got && got.z === 545, "...with the map built");
  check(got && got.how === "gap-ocr",
    "and it keeps `how` alongside the number - an elevation with no "
    + "provenance is what let the chip claim every one of them was printed");
  const inf = st.elev.byChain.get(95);
  check(inf && inf.how === "interval-infill" && /579/.test(inf.src),
    "...and the server's src sentence for an infilled row");
  return runEnsure(s => { s.page = 2; });
}).then(st => {
  check(!(st.elev.byChain instanceof Map),
    "a PAGE switch mid-fetch: page 3's elevations are NOT written onto page 2 "
    + "(chain ids collide across pages, so this silently mislabels a line)");
  return runEnsure(s => { s.gen++; });
}).then(st => {
  check(!(st.elev.byChain instanceof Map), "a generation bump mid-fetch is dropped too");
  return runEnsure(s => { s.job = "K"; });
}).then(async st => {
  check(!(st.elev.byChain instanceof Map), "and a JOB switch mid-fetch");
  // the same three-part guard loadResult and poll already carry
  check(/gen !== state\.gen \|\| job !== state\.job \|\| page !== state\.page/
        .test(fnSrc("ensureElev").replace(/\s+/g, " ")),
    "the guard tests all three of gen, job and page, like loadResult and poll");

  // (a) FAILURE IS ITS OWN STATE, and it is not an empty answer
  {
    const s = ensureScope({ fail: true, failTimes: 1 });
    await s.fn();
    eq(s.state.elev.status, "failed", "a dropped request lands in `failed`");
    check(s.state.elev.byChain === null,
      "...and NOT in an empty Map - THE REPORTED BUG: an empty map is the "
      + "answer 'this sheet labels no contours', which is a different fact");
    check(s.said.length === 1 && s.said[0].cls === "err",
      "the operator is told, once, as an error");
    // (b) AND IT IS RETRYABLE - the next ask really re-asks
    await s.fn();
    eq(s.state.elev.status, "ready", "asking again after a failure succeeds");
    eq(s.calls(), 2, "...because it actually issued a second request");
    check(s.state.elev.byChain.get(192).z === 545, "and the map is there");
  }
  // (c) an answered EMPTY sheet is `ready`, and is never re-asked
  {
    const s = ensureScope({});
    s.state.elev = { status: "ready", byChain: new Map() };
    await s.fn();
    eq(s.calls(), 0,
      "a sheet that answered with no contours is not asked over and over");
  }
  // (d) LOADING is not IDLE: a second caller while one is in flight adds no
  //     request of its own
  {
    const s = ensureScope({});
    const a = s.fn();
    const b = s.fn();
    await Promise.all([a, b]);
    eq(s.calls(), 1, "two overlapping asks issue ONE request");
  }
  if (fails) { console.error("\n" + fails + " check(s) failed"); process.exit(1); }
  console.log("\nall selection-elevation checks passed");
}).catch(e => {
  console.error("  FAIL: ensureElev suite threw " + (e && e.stack || e));
  process.exit(1);
});

// ---------------------------------------------------------------------------
// (This block is SYNCHRONOUS and sits after the async ensureElev suite on
// purpose: it runs first, so its failures are already in `fails` when that
// suite's .then() prints the summary and sets the exit code. Do not wrap it
// in a promise without moving the summary.)
// THE READOUT MAY NOT CONTRADICT WHAT IS LOADED (2026-09-03, blocking).
// renderSelElev takes the reading as an argument. Three callers had only the
// chain, passed none, and the no-number branch printed "none printed for
// this line" over a contour whose elevation was in byChain the whole time -
// measured on Oncor C-04 chain 102: "EL 558.5 · “BW” label 10.6 pt away"
// became "none printed for this line" the moment Tools > Earthwork started a
// second suggest flight, permanently, with state.elev.status "ready".
// A false denial is worse than the blank it replaced.
{
  const src = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"),
                              "utf8");
  const chainOnly = [...src.matchAll(/renderSelElev\(chainById\([^)]*\)\)/g)];
  check(chainOnly.length === 0,
    "no caller passes renderSelElev a chain with no reading (found "
    + chainOnly.length + ")");
  check(/function repaintSelElev\(ch\)/.test(src),
    "the chain-only callers go through repaintSelElev");
  const w = src.slice(src.indexOf("function repaintSelElev(ch)"),
                      src.indexOf("function renderSelElev(ch"));
  check(/state\.elev\.byChain instanceof Map/.test(w)
        && /\.get\(ch\.chain_id\)/.test(w),
    "and it re-derives the reading from byChain, the one place that has it");
  check(/elevProvenance\(/.test(w),
    "and its provenance with it, so the wording cannot drift from the number");
  // the box sits ON the drawing: it must not eat clicks meant for a line
  const css = fs.readFileSync(path.join(__dirname, "..", "webui", "style.css"),
                              "utf8");
  const box = css.slice(css.indexOf(".sel-elev {"), css.indexOf(".sel-elev[hidden]"));
  check(/pointer-events:\s*none/.test(box),
    "the readout container lets clicks through to the sheet under it");
  check(/\.sel-elev \.sel-el \{[^}]*pointer-events:\s*auto/.test(
          css.replace(/\s+/g, " ").replace(/\.sel-elev \.sel-el \{/g, "\n.sel-elev .sel-el {"))
        || /\.sel-elev \.sel-el[^{]*\{[\s\S]{0,200}?pointer-events:\s*auto/.test(css),
    "while the text itself still takes a hover, for the provenance tooltip");
}
