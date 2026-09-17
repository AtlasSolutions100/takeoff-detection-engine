// THE STANDING EARTHWORK WARNING SAYS WHAT IS TRUE NOW (2026-09-17).
//
// It quoted "about 2.4 times high" - the score of a build two fixes ago.
// Re-scored like for like on the same job: cut +43%, fill -36%. The export
// note repeated that figure and also said "no input on the panel can bound
// it", false since the limit of work landed. This pins the new wording,
// that no stale figure is left anywhere a reader sees, and that the export
// names the bound that applied.
//
// run: node tests/test_warning_copy.js   (fails on the commit before it)
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
const strip = (s) => s.replace(/\/\/[^\n]*/g, "").replace(/<!--[\s\S]*?-->/g, "");
const limit = (/<div class="ew-limit" id="ewLimit">([\s\S]*?)<button id="ewLimitToggle"/.exec(html) || [])[1] || "";
check(/off by a third or more, either way/.test(limit), "the warning says how far off, in both directions");
check(/It is off the most where\s+contour lines have no number\./.test(limit), "and where it is off the most, in plain words");
check(!/2\.4|1\.42/.test(limit) && !/times high/.test(strip(html)) && !/2\.4x|about 2\.4|1\.42x/.test(strip(js)),
      "no stale multiple is left in anything a reader sees");
check(/by up to about 40%, in opposite directions\./.test(js), "the export note carries the same re-scored figure");
check(!/no input on the panel can bound it/.test(strip(js)), "the export no longer says nothing can bound the region");
check(/\(j\.boundary_applied\s*\n?\s*\? "That region is bounded by the limit of work: only ground inside it is counted\."/.test(js),
      "it names the limit of work when one was applied");
check(/Set a limit of work to bound it\./.test(js), "and says how to bound it when none was");
check(!/Beaumont|#853/.test(limit), "the warning names no client's job");
// the folded "where it applies" is REWRITTEN at runtime (ewBoundRender):
// the short HTML default never showed while the long runtime copy stood
check(!/straight-sided envelope|bay and notch/.test(js)
      && /Each surface reaches only as far as its own "\n\s*\+ "lines, and the volume is counted where the two overlap\./.test(js),
      "the runtime 'where it applies' says the same short thing as the page");
if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all warning-copy checks passed");
