// Abstained scale readings, surfaced as SUGGESTIONS that pick nothing.
//
// The engine recovers real scale captions off sheets whose text is plotted as
// glyph outlines and deliberately ABSTAINS - promotion was measured
// confidently wrong twice in one evening (Thielen ID-1.04's eaten 0" read as
// 40 where truth is 4; Hurricane C.3.5, which prints THREE scales and whose
// OCR recovered only the key map's 1"=300', 7.5x wrong as an "uncontested
// single"). 26 corpus pages now carry candidates, 12 of them 2-3 each.
//
// This pins the properties that make surfacing them safe. Every one of these
// is a way the feature could quietly become the thing the abstention exists
// to prevent:
//
//   1. NOTHING IS PRE-SELECTED. renderScaleSuggest never sets the picker's
//      `on` class, and it never writes state.result.scale.
//   2. ALL of them are shown. The loop iterates every deduped suggestion; it
//      does not sort, slice, max or "prefer".
//   3. Each says WHERE it was read and WHY it is not trusted, in plain words.
//   4. Picking one lands through ONE path (suggestedScaleObj), stamped
//      picked_by_reviewer + from_suggestion, with every corroboration field
//      cleared - so it can never wear the banner's green.
//   5. The type-it-yourself box stays and is rendered ABOVE the suggestions.
//   6. A page with NO suggestions renders none of this and keeps its honest
//      empty state.
//
// Source-level plus behavioural, like tests/test_bidview_rules.js:
//
//     node tests/test_scale_suggest_ui.js
const fs = require("fs");
const path = require("path");
const R = (...p) => fs.readFileSync(path.join(__dirname, "..", ...p), "utf8");
const js = R("webui", "app.js");
const css = R("webui", "style.css");
const html = R("webui", "index.html");
const srv = R("server.py");
const dp = R("takeoff_engine", "detect_page.py");

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
// A sentence a reader sees is not a substring of the source: both languages
// wrap it across lines and rejoin with `+`. Undo the wrapping (and Python's
// implicit adjacent-string join) before matching on prose, so the assertion
// is about what the operator READS, not about where a line happened to end.
const flat = (s) => s
  .replace(/`\s*\+\s*`/g, "")            // JS template concatenation
  .replace(/["']\s*\n\s*f?["']/g, "")    // Python adjacent / f-string join
  .replace(/\s+/g, " ");

// ---- the engine side is actually emitting them --------------------------
check(/"scale_suggestions": scale_suggestions/.test(dp),
  "detect_page emits scale_suggestions beside scale");
check(/scale_suggestions, _sc = _sc, None/.test(dp),
  "and routes an abstained reading OUT of scale, so no consumer reads it "
  + "as a scale in force");

// ---- 1/2/4. behavioural: the reading list and the object a pick makes ----
const T = new Function("state",
  fn("scaleSuggestions") + fn("sugWhere") + fn("suggestedScaleObj")
  + "return { scaleSuggestions, sugWhere, suggestedScaleObj };");

const SUG = {
  source: "ocr", has_nts: false,
  candidates: [
    { text: `1"=20'`, ft_per_paper_inch: 20, origin: "ocr-caption-window",
      at: [100, 1500, 140, 1512] },
    { text: `1"=5'`, ft_per_paper_inch: 5, origin: "ocr-caption-window",
      at: [1400, 60, 1440, 72] },
    // a duplicate value: deduped, exactly as the promoted picker dedupes
    { text: `1" = 20'`, ft_per_paper_inch: 20, origin: "ocr-caption-window",
      at: [700, 900, 740, 912] },
  ],
};
const stateWith = (sug, scale) => ({
  result: { scale: scale || null, scale_suggestions: sug,
            page: { width_pt: 1584, height_pt: 1224 } },
});

let A = T(stateWith(SUG));
check(A.scaleSuggestions().length === 2,
  "two distinct readings from three candidates - deduped on value");
check(A.scaleSuggestions()[0].ft_per_paper_inch === 20
   && A.scaleSuggestions()[1].ft_per_paper_inch === 5,
  "in the ENGINE's own order - not sorted, not ranked, not 'preferred'");
check(T(stateWith(null)).scaleSuggestions().length === 0,
  "a page with no suggestions offers none");
check(T(stateWith({ candidates: [] })).scaleSuggestions().length === 0,
  "an empty candidate list offers none");
check(T(stateWith({ candidates: [{ text: "x", ft_per_paper_inch: 0 }] }))
  .scaleSuggestions().length === 0,
  "a zero reading is not offered - it would price everything at nothing");

// where on the sheet, in words, and never invented
check(A.sugWhere(SUG.candidates[0]) === "the bottom-left of the sheet",
  "a caption low and left is described as the bottom-left of the sheet");
check(A.sugWhere(SUG.candidates[1]) === "the top-right of the sheet",
  "a caption high and right is described as the top-right of the sheet");
check(A.sugWhere({ ft_per_paper_inch: 20 }) === "",
  "a candidate with no recorded position says nothing about position");
check(T({ result: { scale_suggestions: SUG, page: null } })
  .sugWhere(SUG.candidates[0]) === "",
  "and no position is claimed when the page size is unknown");

const obj = A.suggestedScaleObj(SUG.candidates[0]);
check(obj.ft_per_paper_inch === 20 && obj.text === `1"=20'`,
  "an accepted suggestion carries its own value and caption");
check(obj.picked_by_reviewer === true,
  "it is stamped picked_by_reviewer - what makes the approve record a "
  + "reviewer decision server-side");
check(obj.from_suggestion === true,
  "and from_suggestion - what makes every sentence downstream say WHICH "
  + "kind of reviewer decision it was");
check(obj.typed_by_reviewer === false,
  "and NOT typed_by_reviewer: nobody typed this");
check(obj.agrees === null && obj.delta_pct === null
   && obj.bar_ft_per_paper_inch === null,
  "every corroboration field is cleared - nothing was measured against a "
  + "value the engine refused to promote, so green is unreachable");
check(obj.origin === "ocr-caption-window",
  "the reading's provenance is carried onto the scale in force");

// ---- 1. nothing is pre-selected -----------------------------------------
const rs = fn("renderScaleSuggest");
check(rs.length > 0, "renderScaleSuggest exists");
check(!/\bsh-scale-pick\b[^"`]*\bon\b/.test(rs) && !/" on"/.test(rs)
   && !/\.on\b\s*=/.test(rs),
  "no suggestion button is ever given the picker's `on` state - a "
  + "highlighted suggestion is a default, and a default is the one thing "
  + "this block must not be");
check(!/state\.result\.scale\s*=/.test(rs),
  "rendering the suggestions never sets the scale - only a click does");
check(/No scale is set on this sheet/.test(rs),
  "the headline states the sheet is unscaled BEFORE it offers anything");

// ---- 2. all of them, side by side ---------------------------------------
check(/for \(const c of uniq\)/.test(rs),
  "it loops over every deduped suggestion");
check(!/\.slice\(|\.sort\(|Math\.max|\[0\]/.test(rs),
  "and never slices, sorts or takes the first - 12 of the 26 pages carry "
  + "2-3, and silently preferring one is the defect the abstention prevents");

// ---- 3. where it was read and why it is not trusted, in plain words -----
const rsFlat = flat(rs);
check(/read by machine off a picture of the caption/.test(rsFlat),
  "it says the reading was made by machine off a picture of the caption");
check(/digits can be misread/.test(rsFlat),
  "it says the digits can be misread");
// 2026-09-01: this used to pin "nothing else on this sheet ... backs it
// up" - a claim about the DRAWING that was false on Hurricane C.3.5, which
// prints the bar the engine failed to read. The honest sentence is about the
// ENGINE. Matched on prose only, so a comment quoting the old words cannot
// satisfy it (tests/test_scale_wording_honest.js has the full contract).
const rsProse = flat(rs.split("\n").filter(l => !/^\s*\/\//.test(l)).join("\n"));
check(/Nothing on this sheet has been checked against/.test(rsProse),
  "it says nothing has been CHECKED against the reading - the engine's "
  + "result, not a claim about what the sheet prints");
check(!/nothing else on this sheet/.test(rsProse)
   && !/drawn as shapes/.test(rsProse),
  "and no longer asserts what the sheet lacks, or why");
check(/may also have MISSED other scales|may have missed others printed here/
  .test(rsFlat),
  "and it says the reader may have MISSED other scales printed on the "
  + "sheet - the sampling-bias trap that made a lone survivor 7.5x wrong");
check(/type the scale yourself above/.test(rsFlat),
  "and it points back at typing the scale yourself");

// ---- 5. the typed box stays, and stays above ----------------------------
check(js.indexOf("renderScaleTyped(el);") > 0
   && js.indexOf("renderScaleSuggest(el);")
      > js.indexOf("renderScaleTyped(el);"),
  "the type-it-yourself box is rendered ABOVE the suggestions");
check(/no scale is set — you can type it yourself/.test(js),
  "and its prompt no longer claims the sheet says nothing about its scale");

// ---- 6. the honest empty state is not regressed -------------------------
check(/if \(!uniq\.length\) return;/.test(rs),
  "a page with no suggestions renders no suggestion block at all");
check(/noScaleSentence\(sug\.length\)/.test(js),
  "and the banner's no-scale sentence for it is read off the engine's "
  + "record (noScaleSentence), never asserted about the sheet");

// ---- the chip and the banner tell the truth about the state -------------
check(/no scale set — \$\{_nsug\} unchecked reading/.test(js),
  "the sheet-header chip says NO SCALE SET first, then the count");
const clA = js.indexOf("function confLines()");
const cl = js.slice(clA, js.indexOf("\n}\n", clA));
const clFlat = flat(cl);
check(clA > 0 && cl.length > 2000, "confLines() was located");
check(/if \(!sc && sug\.length\)/.test(cl),
  "the banner has a branch for 'no scale, but readings on offer'");
check(cl.indexOf("sc.from_suggestion") > 0
   && cl.indexOf("sc.from_suggestion") < cl.indexOf("sc.agrees === true"),
  "and the accepted-suggestion branch is ordered BEFORE any branch that "
  + "can print green, so green is structurally unreachable for it");
check(/No scale is in force on this sheet/.test(clFlat),
  "the no-scale-with-suggestions banner still leads with NO SCALE");
check(/will not stand behind/.test(clFlat),
  "and says the engine will not stand behind what it read");
check(/read by machine off a picture of the caption/.test(clFlat),
  "and the banner uses the same plain words as the block below it");

// ---- the decision travels: approve -> record -> report ------------------
check(/picked_by_reviewer: !!scOn\.picked_by_reviewer/.test(js),
  "the approve payload carries the reviewer flag, unchanged");
check(/_BASIS_SUGGESTION = "engine-suggestion"/.test(srv),
  "the server names the third reviewer case");
check(/def _scale_suggestion_for/.test(srv)
   && /abs\(v - float\(ft\)\) < 1e-6/.test(srv),
  "and decides it on the VALUE, to 1e-6 - never on a browser flag");
check(/"basis": _BASIS_SUGGESTION/.test(srv)
   && /export\["scale"\]\.update/.test(srv),
  "the export's scale block carries the basis");
const srvFlat = flat(srv);
check(/accepting a reading the engine recovered but would not vouch for/
  .test(srvFlat), "the export NAME says what was accepted");
check(/would not stand behind/.test(srvFlat)
   && /read by character recognition off a picture of the caption/
      .test(srvFlat),
  "and the printed report PDF says it on its own face");
check(/"offered": _scale_offered\(sugg\)/.test(srv),
  "the training ledger records every reading the operator was offered - "
  + "the label that makes an abstention trainable");

// ---- the block is styled, and never in the accent colour ----------------
check(/\.sh-suggest\b/.test(css) && /\.sh-suggest-why\b/.test(css),
  "the suggestion block is styled");
check(!/\.sh-suggest[^{]*\{[^}]*var\(--accent\)/.test(css),
  "and never in the accent colour reserved for a corroborated reading");

// ---- the cache buster moved, or nobody sees any of this -----------------
check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1] || 0) >= 160,
  "app.js cache buster at or past 160 (a later bump is fine; an older one is not)");
check(+((/style\.css\?v=(\d+)/.exec(html) || [])[1] || 0) >= 77,
  "style.css cache buster at or past 77");

console.log();
if (fails) { console.error(fails + " FAILED"); process.exit(1); }
console.log("all checks passed");
