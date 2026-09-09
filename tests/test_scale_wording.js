// Every sentence about the scale says what the ENGINE knows - never a fact
// about the DRAWING the UI cannot know.
//
// Measured 2026-09-01 on Hurricane Storage Units C.3.5 (engine page index 6,
// Plans 2/2026.02.13_Civil Design_HURRICANE STORAGE UNITS.pdf). The sheet
// PRINTS three scales - HORIZ 1"=40', VERT 1"=10', a key map at 1"=300' -
// and a checkered 0/40'/80' bar. The engine recovers ONE caption (the key
// map's) and abstains. On that page, after 9e956bb had fixed the suggestion
// panel, three other surfaces still asserted a fact about the sheet:
//
//   confLines, no-scale branch   "No scale could be read off this sheet."
//   ckBuild, no-scale entry       "No scale could be read off this sheet."
//   renderAreas, pt2 arm          "no scale on this sheet - areas shown in pt2"
//
// plus the same shape in smaller print (the typed-box prompt "nothing on
// this sheet says what it was drawn to", the status bar's "no scale found",
// "nothing on the sheet backs it up", "there is nothing on the sheet to
// check that caption against", the calculators' "no scale on this page").
//
// The rule this pins: with no scale in force every one of those reads off
// what the engine RECORDED, through ONE function, noScaleSentence(nsug):
//
//   nsug = 0   "The engine did not read a scale on this sheet."
//   nsug = N   "The engine read N scale captions it will not stand behind
//               - check them."
//
// so a page carrying abstained candidates is told so on every surface, and
// a page carrying none is told what the engine did - never what the sheet
// lacks. FAILS on 9e956bb (checked against a git archive of it).
//
// Source-level plus behavioural, like tests/test_scale_wording_honest.js.
// What is behavioural: noScaleSentence and ckBuild (the CHECK-QUEUE block,
// evaluated standalone as tests/test_check_queue.js does), sheetFactsText
// and the scale section of confLines (extracted and run against a fake
// page state with two abstained candidates, its DOM-free helpers stubbed).
// What is a grep, stated as such: renderAreas and the typed-box prompt build
// DOM elements, so their arms are matched in the source.
//
//     node tests/test_scale_wording.js
"use strict";
const fs = require("fs");
const path = require("path");
const R = (...p) => fs.readFileSync(path.join(__dirname, "..", ...p), "utf8");
const js = R("webui", "app.js");
const html = R("webui", "index.html");

let fails = 0;
const check = (ok, msg) => {
  if (ok) console.log("ok: " + msg);
  else { fails++; console.error("FAIL: " + msg); }
};
const fn = (name) => {
  const a = js.indexOf(`function ${name}(`);
  if (a < 0) return "";
  const b = js.indexOf("\n}\n", a);
  return b < 0 ? js.slice(a) : js.slice(a, b + 3);
};
// Rejoin the `...` + `...` and "..." + "..." wrapping so assertions are
// about what is READ, not where a source line happened to end.
const flat = (s) => s.replace(/[`"]\s*\+\s*[`"]/g, "").replace(/\s+/g, " ");
// Only the prose the operator sees: full-line comments stripped.
const prose = (s) => flat(s.split("\n").filter(l => !/^\s*\/\//.test(l))
  .join("\n"));

// ---- (a) no forbidden drawing-fact phrase anywhere in app.js -----------
// Matched over the WHOLE file, comments included: the last fix was nearly
// satisfied by a comment quoting the old sentence, so comments may not
// quote these either.
const FORBIDDEN = [
  [/no scale on this (sheet|page)/i, "no scale on this sheet/page"],
  [/no scale could be read off/i, "No scale could be read off"],
  [/could be read off this sheet/i, "could be read off this sheet"],
  [/no scale found/i, "no scale found"],
  [/nothing on this sheet says what it was drawn to/i,
    "nothing on this sheet says what it was drawn to"],
  [/there is nothing on the sheet/i, "there is nothing on the sheet"],
  [/nothing on the sheet (backs|corroborates)/i, "nothing on the sheet backs/corroborates"],
  [/nothing on this sheet corroborates/i, "Nothing on this sheet corroborates"],
  [/no drawn scale bar, no printed/i, "no drawn scale bar, no printed ..."],
  [/no scale caption was (found|read) on this sheet/i,
    "No scale caption was found/read on this sheet"],
  [/no drawn scale bar and no printed area figure were found here/i,
    "... were found here"],
  [/no printed .N sf. tag on this sheet/i, "no printed N sf tag on this sheet"],
  [/NO SCALE — pt only/, "NO SCALE — pt only"],
  [/["`]no scale["`]/, "a bare \"no scale\" string"],
  [/nothing on the sheet settled/i, "nothing on the sheet settled"],
];
const flatAll = flat(js);
for (const [re, label] of FORBIDDEN) {
  const m = flatAll.match(re);
  check(!m, `app.js never says "${label}"`
    + (m ? ` - found: ...${flatAll.slice(Math.max(0, m.index - 40), m.index + 60)}...` : ""));
}

// ---- the shared sentence, evaluated ---------------------------------------
const a = js.indexOf("// CHECK-QUEUE-BEGIN"), b = js.indexOf("// CHECK-QUEUE-END");
check(a > 0 && b > a, "CHECK-QUEUE markers located");
let Q = null;
try {
  Q = new Function(js.slice(a, b)
    + "\nreturn { noScaleSentence, ckBuild, CK_W_PAGE };")();
} catch (e) {
  check(false, "the CHECK-QUEUE block evaluates standalone and exports "
    + "noScaleSentence: " + e.message);
}
const ns = Q ? Q.noScaleSentence : () => "";
const bad = (s) => FORBIDDEN.some(([re]) => re.test(s));
check(ns(0) === "The engine did not read a scale on this sheet.",
  "noScaleSentence(0) says what the engine did, not what the sheet lacks: "
  + JSON.stringify(ns(0)));
check(/^The engine read 2 scale captions it will not stand behind — check them\.$/
  .test(ns(2)), "noScaleSentence(2) names the two abstained captions and "
  + "tells the operator to check them: " + JSON.stringify(ns(2)));
check(/^The engine read one scale caption it will not stand behind — check it\.$/
  .test(ns(1)), "noScaleSentence(1) is the singular of the same: "
  + JSON.stringify(ns(1)));
check(!bad(ns(0)) && !bad(ns(1)) && !bad(ns(2)),
  "and none of the three carries a forbidden phrase");
{
  const at = js.indexOf("function noScaleSentence(");
  const above = js.slice(Math.max(0, at - 1500), at).replace(/\s*\n\/\/ ?/g, " ");
  check(at > 0 && /never what the DRAWING/.test(above),
    "noScaleSentence states its rule in the comment above it, where the "
    + "next editor will read it");
}

// ---- a fake page: two abstained candidates, nothing in force ----------
const SUG2 = { source: "ocr", bar_ft_per_paper_inch: null, has_nts: false,
  candidates: [{ text: `1" = 40'`, ft_per_paper_inch: 40, at: [2100, 400] },
               { text: `1" = 10'`, ft_per_paper_inch: 10, at: [2100, 420] }] };
const st = (sug, scale) => ({
  page: 6, conf: null,
  result: { scale: scale || null, scale_suggestions: sug,
            page: { width_pt: 2592, height_pt: 1728 },
            chains: new Array(914), styles: new Array(180) },
});

// ---- site 1: the check queue (ckBuild, evaluated) -----------------------
if (Q) {
  const inp = (scale) => ({ totalPt: 100000, ftPerIn: null, styles: [],
    scale, tag: null, datum: { state: "checked", conflict: false },
    spots: { state: "checked", n: 0, unresolved: 0 },
    drops: { state: "checked", ew: 0, tr: 0, sigChanged: false }, marks: [] });
  const e2 = Q.ckBuild(inp({ present: false, nsug: 2 })).entries
    .find(e => e.id === "scale");
  check(!!e2 && e2.sev === "stop" && e2.w === 100000 * Q.CK_W_PAGE,
    "check queue, two candidates: still a stop entry at the whole-page "
    + "weight (behaviour unchanged)");
  check(!!e2 && e2.head === ns(2),
    "check queue, two candidates: the head is noScaleSentence(2), so it "
    + "mentions the candidates: " + JSON.stringify(e2 && e2.head));
  check(!!e2 && /readings are offered in the sheet header/.test(e2.text)
     && /check each against the drawing/.test(e2.text),
    "and its body points at the header where they are offered");
  const e0 = Q.ckBuild(inp({ present: false, nsug: 0 })).entries
    .find(e => e.id === "scale");
  check(!!e0 && e0.head === ns(0) && /use the scale box/.test(e0.text),
    "check queue, no candidates: says the engine read none, keeps the old "
    + "body: " + JSON.stringify(e0 && e0.head));
  const eu = Q.ckBuild(inp({ present: false })).entries
    .find(e => e.id === "scale");
  check(!!eu && eu.head === ns(0),
    "check queue, nsug absent (old callers): reads as none, never throws");
  check(!!e2 && !bad(e2.head + " " + e2.text)
     && !!e0 && !bad(e0.head + " " + e0.text),
    "neither queue entry carries a forbidden phrase");
}
const ckc = fn("ckCollect");
check(/present: false, nsug: scaleSuggestions\(\)\.length/.test(ckc),
  "ckCollect hands the queue the abstained-candidate count off the same "
  + "list the header offers (source-level: ckCollect reads state)");

// ---- site 2: the banner (confLines' scale section, evaluated) -----------
{
  const c0 = js.indexOf("function confLines()");
  const c1 = js.indexOf("// ---- several scales printed", c0);
  check(c0 > 0 && c1 > c0, "confLines' scale section located");
  let CL = null;
  try {
    CL = new Function("state", "scaleUndo",
      js.slice(a, b)                       // noScaleSentence
      + fn("scaleSuggestions") + fn("sugWhere") + fn("scaleBarSentence")
      + fn("confFt") + fn("confScale") + fn("confQuote") + fn("esc")
      + "function confTagCheck() { return null; }\n"
      + "function scaleCandidates() { return []; }\n"
      + "function scaleTypedKey() { return 'k'; }\n"
      + js.slice(c0, c1) + "\nreturn out;\n}\n"
      + "return confLines();")
  } catch (e) {
    check(false, "confLines' scale section evaluates with its helpers "
      + "stubbed: " + e.message);
  }
  const run = (s) => { try { return CL(s, { key: null, scale: null }); }
                       catch (e) { return [{ level: "threw", html: e.message }]; } };
  const two = run(st(SUG2));
  check(two.length >= 1 && two[0].level === "stop",
    "banner, two candidates: still RED (behaviour unchanged): "
    + JSON.stringify(two.map(l => l.level)));
  const h2 = two.length ? two[0].html : "";
  check(/No scale is in force on this sheet/.test(h2),
    "banner, two candidates: leads with the STATE (no scale in force)");
  check(/2 readings/.test(h2) && /will not stand behind/.test(h2)
     && /1 inch = 40 ft/.test(h2) && /1 inch = 10 ft/.test(h2),
    "and names both abstained candidates the engine will not stand behind");
  check(!bad(h2), "and carries no forbidden phrase");
  const none = run(st({ source: "ocr", bar_ft_per_paper_inch: null,
                        candidates: [] }));
  const h0 = none.length ? none[0].html : "";
  check(none.length >= 1 && none[0].level === "stop"
     && h0.startsWith("<b>The engine did not read a scale on this sheet.</b>"),
    "banner, no candidates: still RED, and its lead sentence is "
    + "noScaleSentence(0) - the engine read none, not the sheet has none: "
    + JSON.stringify(h0.slice(0, 80)));
  check(/check the sheet itself rather than assuming it has none/.test(h0),
    "and it still tells the operator the reader misses printed captions");
  check(!bad(h0), "and carries no forbidden phrase");
  const clSrc = js.slice(c0, c1);
  check(/noScaleSentence\(sug\.length\)/.test(clSrc),
    "the branch reads the count off the data, not a literal");
}

// ---- site 3: the areas card (renderAreas builds DOM: source-level) ------
{
  const ra = fn("renderAreas");
  const raP = prose(ra);
  check(ra.length > 0, "renderAreas located");
  check(/noScaleSentence\(scaleSuggestions\(\)\.length\)/.test(ra),
    "areas card, pt2 arm: the sentence is noScaleSentence over the "
    + "abstained-candidate list, so with two candidates it reads "
    + JSON.stringify(ns(2)) + " (source-level: the card builds DOM)");
  check(/<\/b> Areas shown in pt², no SF\./.test(raP),
    "and the pt2 tail still says areas are shown in pt2, no SF");
  check(/the engine found no printed “N sf” tag to check it against/.test(raP),
    "the card's tag line says the engine FOUND none, not that the sheet "
    + "has none");
  check(!bad(raP), "renderAreas carries no forbidden phrase");
}

// ---- the smaller print, same rule ---------------------------------------
{
  const P = prose(js);
  check(/the engine read no scale here — you can type it yourself:/.test(P),
    "the typed-box prompt on a page with no candidates says the engine "
    + "read none");
  check(/no scale set — type one below/.test(P),
    "the header chip says no scale is SET");
  check(/The engine read no scale caption on this sheet, so it measured the ruler/.test(P),
    "the graphic-bar banner is in the engine's voice");
  check(/The engine found no drawn scale bar and no printed area figure it could check that caption against/.test(P),
    "the single-caption banner says what the engine found, not what the "
    + "sheet has");
  check(/nothing has been checked against it - check 1 in =/.test(P),
    "the queue's OCR entry says nothing has been CHECKED against it");
  check(/no scale set on this page — volumes need feet/.test(P)
     && /NO SCALE SET — pt only/.test(P),
    "the calculators say no scale is SET on this page");
}

// ---- the status bar, evaluated ------------------------------------------
{
  const T = new Function("state",
    fn("scaleSuggestions") + fn("scaleShort") + fn("sheetFactsText")
    + "return { sheetFactsText };");
  const s2 = T(st(SUG2)).sheetFactsText();
  check(s2 === "914 lines in 180 styles · no scale set — 2 unchecked "
      + "readings offered, none chosen",
    "status bar, two candidates: mentions them: " + JSON.stringify(s2));
  const s0 = T(st(null)).sheetFactsText();
  check(s0 === "914 lines in 180 styles · no scale set — the engine read none",
    "status bar, none: no scale SET, the engine read none: "
    + JSON.stringify(s0));
}

// ---- nothing about the selection moved ----------------------------------
check(/if \(!uniq\.length\) return;/.test(fn("renderScaleSuggest"))
   && !/state\.result\.scale\s*=/.test(fn("renderScaleSuggest")),
  "renderScaleSuggest still renders nothing without candidates and still "
  + "pre-selects nothing");

// ---- the cache buster moved, or nobody sees any of this ----------------
check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1] || 0) >= 160,
  "app.js cache buster at or past 160 (a later bump is fine; an older one is not)");

console.log();
if (fails) { console.error(fails + " FAILED"); process.exit(1); }
console.log("all checks passed");
