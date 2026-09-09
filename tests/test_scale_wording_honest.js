// The words beside the abstained-scale buttons say only what the ENGINE
// knows - never what the DRAWING has.
//
// Measured 2026-09-01 on Hurricane Storage Units sheet C.3.5 (page index 6,
// Plans 2/2026.02.13_Civil Design_HURRICANE STORAGE UNITS.pdf). The panel
// offered the key map's 1"=300' as an unchecked reading and said, beside it:
//
//   (a) "nothing else on this sheet - no drawn scale bar, no printed
//       square-foot figure - backs it up."  The sheet PRINTS a checkered
//       0 / 40' / 80' bar directly above HORIZ. SCALE: 1" = 40'. The engine
//       failed to READ it; the sentence said it does not EXIST. On this
//       sheet that bar is the one printed thing separating 1"=40' from the
//       key map's 1"=300' - 7.5x - so the sentence argued the operator out of
//       the only check available to him.
//   (b) "because this sheet's text is drawn as shapes rather than written
//       as text."  Nothing tests that. The page carries 643 text characters
//       in five embedded fonts; the caption sits inside a picture.
//   (c) the status bar read "no scale found" while the panel three lines
//       above it offered readings.
//
// This pins the fix and FAILS on 3524f5a (checked: 9 FAIL lines there).
// The rule it enforces: a sentence about the bar reads what the payload
// records (`bar_ft_per_paper_inch`), a sentence about corroboration is the
// tooltip's voice ("has been checked against"), and there is no "because"
// clause whose cause the code never tests.
//
// Source-level plus behavioural, like tests/test_scale_suggest_ui.js:
//
//     node tests/test_scale_wording_honest.js
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
// Rejoin `...` + `...` wrapping so assertions are about what is READ.
const flat = (s) => s.replace(/`\s*\+\s*`/g, "").replace(/\s+/g, " ");
// Only the prose the operator sees: template strings, not the comments
// that explain why the old sentence was wrong (those may quote it).
const prose = (s) => flat(s.split("\n").filter(l => !/^\s*\/\//.test(l))
  .join("\n"));

const rs = fn("renderScaleSuggest");
const clA = js.indexOf("function confLines()");
const cl = js.slice(clA, js.indexOf("\n}\n", clA));
const sf = fn("sheetFactsText");
check(rs.length > 0 && cl.length > 2000 && sf.length > 0,
  "renderScaleSuggest, confLines and sheetFactsText were located");
const rsP = prose(rs), clP = prose(cl);
// the banner's no-scale-with-readings branch, on its own: the promoted-OCR
// branch further down says "nothing else FOUND on this sheet backs it up",
// an engine claim on a path this change does not touch
const ab = cl.slice(cl.indexOf("if (!sc && sug.length) {"),
                    cl.indexOf("} else if (!sc) {"));
check(ab.length > 500, "the abstained branch of confLines was located");
const abP = prose(ab);

// ---- (a) the false claim about the DRAWING is gone --------------------
for (const [name, body] of [["renderScaleSuggest", rsP],
                            ["confLines (abstained branch)", abP]]) {
  check(!/nothing else on this sheet/.test(body),
    `${name}: no longer says "nothing else on this sheet"`);
  check(!/no drawn scale bar, no printed square-foot figure/.test(body),
    `${name}: no longer asserts the sheet has no drawn scale bar`);
  check(!/backs (it|them|\$\{[^}]*\}) up/.test(body),
    `${name}: no longer says nothing "backs it up"`);
  // ---- (b) the untested cause is gone --------------------------------
  check(!/because this sheet's text is drawn as shapes/.test(body)
     && !/drawn as shapes rather than written as text/.test(body),
    `${name}: the "because ... drawn as shapes" clause is gone`);
}
check(!/Nothing on this sheet corroborates it/.test(clP),
  "the accepted-suggestion banner no longer claims nothing on the SHEET "
  + "corroborates the pick");

// ---- the honest sentences are present, in the tooltip's voice ----------
check(/Nothing on this sheet has been checked against/.test(rsP),
  "renderScaleSuggest says nothing has been CHECKED against the reading");
check(/Nothing on this sheet has been checked against/.test(abP),
  "confLines' abstained branch says nothing has been CHECKED against the "
  + "reading");
check(/Nothing on this sheet has been checked against it/.test(clP),
  "and the accepted-suggestion banner uses the same voice");
check(/scaleBarSentence\(\)/.test(rs) && /scaleBarSentence\(\)/.test(ab),
  "both blocks take the bar sentence from ONE function that reads the "
  + "payload, not from prose");

// ---- the bar sentence is read off the data ------------------------------
const T = new Function("state",
  fn("scaleBarSentence") + fn("scaleSuggestions") + fn("scaleShort") + fn("sheetFactsText")
  + "return { scaleBarSentence, scaleSuggestions, sheetFactsText };");
const st = (sug, scale) => ({
  result: { scale: scale || null, scale_suggestions: sug,
            chains: new Array(914), styles: new Array(180) },
});
const SUG = { source: "ocr", bar_ft_per_paper_inch: null,
  candidates: [{ text: `1"=300'`, ft_per_paper_inch: 300 }] };
const SUG3 = { source: "ocr", bar_ft_per_paper_inch: null,
  candidates: [{ text: `1" = 40'`, ft_per_paper_inch: 40 },
               { text: `1" = 10'`, ft_per_paper_inch: 10 },
               { text: `1"=300'`, ft_per_paper_inch: 300 }] };
const nobar = T(st(SUG)).scaleBarSentence();
check(nobar === "The engine did not find a scale bar on this sheet that it "
      + "could read.",
  "with no bar reading recorded it says the ENGINE did not find one it "
  + "could read - never that the sheet has none: " + JSON.stringify(nobar));
check(!/no drawn scale bar|there is no|has no scale bar/.test(nobar),
  "and that sentence never asserts absence on the drawing");
const withbar = T(st({ ...SUG, bar_ft_per_paper_inch: 40.0167 }))
  .scaleBarSentence();
check(/measured a drawn scale bar at 40\.02 ft per inch/.test(withbar)
   && /did not match it against/.test(withbar),
  "and if the payload ever carries a bar reading, the sentence reports it "
  + "as measured-but-unmatched rather than pretending it is absent");

// ---- (c) the status bar agrees with the panel -------------------------
check(T(st(SUG)).sheetFactsText()
      === "914 lines in 180 styles · no scale set — 1 unchecked reading "
      + "offered, none chosen",
  "status bar, one reading: NO SCALE SET first, then the count, then that "
  + "none is chosen");
check(T(st(SUG3)).sheetFactsText()
      === "914 lines in 180 styles · no scale set — 3 unchecked readings "
      + "offered, none chosen",
  "status bar, three readings: the count matches the number of buttons");
check(T(st(null)).sheetFactsText()
      === "914 lines in 180 styles · no scale set — the engine read none",
  "a page with neither a scale nor readings says no scale is SET and that "
  + "the engine read none - not that the sheet has none");
check(T(st(SUG, { text: `1"=20'`, source: "text", agrees: null }))
  .sheetFactsText() === `914 lines in 180 styles · scale 1"=20' (text)`,
  "a page with a scale in force is unchanged - the suggestion arm is only "
  + "reached with NO scale");
check(!/no scale found/.test(T(st(SUG)).sheetFactsText()),
  "the line no longer says 'no scale found' beside a panel that offers one");

// ---- nothing about the selection logic moved --------------------------
check(!/state\.result\.scale\s*=/.test(rs) && !/\.on\b\s*=/.test(rs)
   && !/" on"/.test(rs),
  "renderScaleSuggest still pre-selects nothing");
check(/for \(const c of uniq\)/.test(rs)
   && !/\.slice\(|\.sort\(|Math\.max|\[0\]/.test(rs),
  "and still shows every reading, unranked");

// ---- the cache buster moved, or nobody sees any of this ----------------
check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1] || 0) >= 160,
  "app.js cache buster at or past 160 (a later bump is fine; an older one is not)");

console.log();
if (fails) { console.error(fails + " FAILED"); process.exit(1); }
console.log("all checks passed");
