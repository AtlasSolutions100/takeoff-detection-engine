// When a drawn bar was read INDEPENDENTLY of the caption and contradicts
// it, the suggestions block must say so - not that nothing was checked.
//
// THE DEFECT (2026-09-02, scale stage 1c). Before that stage a page only
// abstained after the bar reader had returned NOTHING, so two sentences
// were safe: scaleBarSentence()'s "measured a drawn scale bar ... but did
// not match it against these readings", and the fixed clause "Nothing on
// this sheet has been checked against them - no scale bar, no printed
// square-foot figure." Stage 1c adds the opposite abstention: the bar WAS
// read, independently, and CONTRADICTS the caption - which is why the
// reading was not promoted. On the six corpus pages that take this arm the
// refused values are 2x to 40x wrong (RC Ontario p10 offers 10 against a
// bar of 20; Hurricane p1 offers 1.0 against 40), and both sentences told
// the operator no evidence existed either way, one click from accepting it.
//
//     node tests/test_scale_bar_contradiction_ui.js
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
  const a = js.indexOf("function " + name + "(");
  if (a < 0) return null;
  const b = js.indexOf("\n}\n", a);
  return js.slice(a, b + 3);
};

const src = fn("scaleBarSentence");
check(!!src, "scaleBarSentence exists");
const chk = fn("scaleBarChecked");
check(!!chk, "scaleBarChecked exists - the clause needs a fact to test");

// "use strict" scopes a bare eval; bind the real sources explicitly.
const scaleBarSentence = eval("(" + src + ")");
const scaleBarChecked = chk ? eval("(" + chk + ")") : () => false;

const setState = (bar, why, nCands) => {
  global.state = { result: { scale_suggestions: {
    bar_ft_per_paper_inch: bar,
    candidates: Array.from({ length: nCands || 1 }, () => ({})),
    pick: { why: why },
  } } };
};

// ---- the new arm: a bar was read and it contradicts -------------------
setState(20.0, "abstained-ocr-single", 1);
const contra = scaleBarSentence();
check(/CONTRADICTS/.test(contra),
  "a contradicted reading is described as contradicted, in the sentence "
  + "the operator reads above the button");
check(/independently/.test(contra),
  "and says the bar was read independently of the caption - which is the "
  + "whole basis for trusting it over the reading");
check(/20\.00/.test(contra), "and quotes the bar's own value");
check(scaleBarChecked() === true,
  "and the page counts as checked, so the 'nothing has been checked' "
  + "clause is suppressed");

// ---- the old arms are unchanged ---------------------------------------
setState(20.0, "abstained-ocr", 1);
check(/did not match it against these readings/.test(scaleBarSentence()),
  "a bar that was measured but never compared keeps its old sentence");
check(scaleBarChecked() === false, "and does NOT count as checked");

setState(null, "abstained-ocr", 2);
check(/did not find a scale bar/.test(scaleBarSentence()),
  "a page with no readable bar keeps its old sentence");
check(scaleBarChecked() === false, "and does not count as checked either");

// ---- the block suppresses the false clause ----------------------------
const at = js.indexOf("const checked = scaleBarChecked();");
check(at > 0, "the suggestions block asks whether anything was checked");
const block = js.slice(at, at + 900);
check(/checked \?\s*""/.test(block.replace(/\s+/g, " ")),
  "and prints NOTHING in place of the 'nothing has been checked' clause "
  + "when something was");
check(/Nothing else on this sheet has been/.test(block),
  "while the un-checked page still gets that clause");

check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1] || 0) >= 160,
  "app.js cache buster at or past 160 (a later bump is fine; an older one is not)");

console.log();
if (fails) { console.error(fails + " FAILED"); process.exit(1); }
console.log("all checks passed");
