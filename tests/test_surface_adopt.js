// THE PANEL TAKES THE SHEET'S OWN WORD FOR WHICH SURFACE A CONTOUR IS ON.
//
// Until now webui/app.js built every row's surface from its own stroke-width
// median and ignored `surface` / `split_by`, which /earthwork/suggest had
// already put on every row from a real precedence: the sheet's legend, then
// colour screening, then that same width median as a last resort.
//
// WHAT THE OLD RULE COST, measured 2026-09-03 on Oncor C-04 (engine page
// index 3, printed sheet C-04). 115 labelled rows: 82 decided by the legend,
// 33 by ink, ZERO by width - and the width median disagreed with the sheet on
// 67 of them. The premise in the old comment, "existing plots lighter than
// proposed", is inverted on that drawing: EXISTING CONTOUR is drawn at 0.2 pt
// and 1.1 pt across three families while PROPOSED sits between them at
// 0.4-0.7 pt, so `w > wmed` selected the 1.1 pt chains - the existing ones.
// The panel reported the heavy existing contours as the proposed surface.
// Cut read 22,032.7 CY where the offline analyst's key for the sheet is
// ~2,002; on the server's split it reads 2,158.4.
//
// AND WHY THE PERSISTENCE HALF IS NOT OPTIONAL. calcStatePayload snapshots
// EVERY row, not the edited ones, and calcStateDirty() fires from 18
// unrelated gestures - a check-queue tick, a grading zone, the limit-of-work
// ring. So any sheet an operator merely visited already has its engine
// defaults frozen in the sidecar, and ewApplySaved wrote them back
// unconditionally. Without a marker for WHO chose a surface, this whole
// change would be invisible on every sheet anyone had touched: the old
// width-median guess would restore itself over the sheet's own legend, and
// nothing on screen would say why. That is the failure this suite exists to
// prevent, so it is tested harder than the adoption itself.
//
// Dependency-free:  node tests/test_surface_adopt.js

"use strict";
const fs = require("fs");
const path = require("path");
const SRC = fs.readFileSync(
  path.join(__dirname, "..", "webui", "app.js"), "utf8");
const HTML = fs.readFileSync(
  path.join(__dirname, "..", "webui", "index.html"), "utf8");

let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); }
  else console.log("ok:", msg);
}

function extract(name) {
  const start = SRC.indexOf("function " + name + "(");
  if (start < 0) return null;
  let depth = 0;
  for (let j = SRC.indexOf("{", start); j < SRC.length; j++) {
    if (SRC[j] === "{") depth++;
    else if (SRC[j] === "}" && --depth === 0) return SRC.slice(start, j + 1);
  }
  return null;
}

// ===========================================================================
// 1. the adoption, run as real code
// ===========================================================================
// The row build lives inside a long async function, so mirror the exact
// expression rather than the whole body. Guard: assert the source really
// contains this shape, so the mirror cannot drift away from the code.
check(/surface:\s*\(s\.split_by === "interval-infill" \|\| s\.split_by === "none"\)\s*\n?\s*\?\s*null/.test(SRC),
  "an interval-infill row - and a row the server could not split (\"none\") - "
  + "is excluded from the width fallback BY NAME, not merely nulled by a later pass");
check(/\?\s*s\.surface\s*\n?\s*:\s*\(\(s\.width \|\| 0\) <= wmed/.test(SRC),
  "the server's surface wins, and the width median is the fallback");
check(/splitBy:\s*s\.split_by \|\| null/.test(SRC),
  "the row carries WHICH tier decided");

const rowSurface = (s, wmed) =>
  (s.split_by === "interval-infill" || s.split_by === "none") ? null
    : (s.surface === "existing" || s.surface === "proposed") ? s.surface
    : ((s.width || 0) <= wmed ? "existing" : "proposed");

// C-04's actual shape: existing drawn at 0.2 and 1.1, proposed between them.
// The width median at 0.7 calls the 1.1 pt EXISTING chains "proposed".
const wmed = 0.7;
const c04 = [
  { chain_id: 1, width: 0.2, surface: "existing", split_by: "legend" },
  { chain_id: 2, width: 1.1, surface: "existing", split_by: "legend" },
  { chain_id: 3, width: 1.1, surface: "existing", split_by: "legend" },
  { chain_id: 4, width: 0.4, surface: "proposed", split_by: "legend" },
  { chain_id: 5, width: 0.7, surface: "proposed", split_by: "legend" },
  { chain_id: 6, width: 0.6, surface: "existing", split_by: "color" },
];
const got = c04.map((s) => rowSurface(s, wmed));
const oldRule = c04.map((s) => ((s.width || 0) <= wmed ? "existing" : "proposed"));
check(JSON.stringify(got) === JSON.stringify(c04.map((s) => s.surface)),
  "every row takes the side the sheet says  (" + got.join(",") + ")");
check(oldRule[1] === "proposed" && oldRule[2] === "proposed",
  "...and the old rule really did call the 1.1 pt EXISTING chains proposed "
  + "- the regression this pins  (" + oldRule.join(",") + ")");
check(got[5] === "existing", "a colour-screened grey row reads existing");

// no legend, no ink, ONE line weight: since 2026-09-08 the server ABSTAINS
// (split_by "none", surface null) instead of filing every row as existing,
// and the client must honour that rather than fall through to its own width
// median - which is exactly the rule that produced the Belle Farms
// "labelled EXIST or skipped" symptom. The row starts on skip.
const mono = [
  { chain_id: 1, width: 0.5, surface: null, split_by: "none" },
  { chain_id: 2, width: 0.5, surface: null, split_by: "none" },
];
check(mono.map((s) => rowSurface(s, 0.5)).every((v) => v === null),
  "a one-weight sheet the server could not split leaves every row on skip - "
  + "the client does NOT re-run the width median over it");
// ...while a sheet whose weights DO separate still takes the server's width split
const two = [
  { chain_id: 1, width: 0.5, surface: "existing", split_by: "width" },
  { chain_id: 2, width: 1.1, surface: "proposed", split_by: "width" },
];
check(JSON.stringify(two.map((s) => rowSurface(s, 0.5))) === JSON.stringify(["existing", "proposed"]),
  "a two-weight sheet still takes the server's width split");

check(rowSurface({ width: 0.2, surface: null, split_by: "interval-infill" },
                 wmed) === null,
  "an inferred row gets NO surface even though its width is under the median");

// the two literals another suite pins - keep them
check(/row\.surface = null;/.test(SRC) && /row\.inferred = true;/.test(SRC),
  "the inferred-row statements test_ew_inferred_disclosure.js pins survive");

// ===========================================================================
// 2. persistence: an engine default must never outrank a better engine
// ===========================================================================
const payload = SRC.slice(SRC.indexOf("rows: state.ew.rows.map(r => ({"),
                          SRC.indexOf("layers: {"));
check(/surface_by: r\.surfaceBy/.test(payload),
  "calcStatePayload records WHO chose the surface");
check(/split_by: r\.splitBy/.test(payload),
  "...and which tier the engine used, so the note survives a reload");

const applied = extract("ewApplySaved");
check(!!applied, "ewApplySaved() found");
check(applied && /if \(r\.surface_by === "operator"\)/.test(applied),
  "a saved surface is restored ONLY when an operator chose it");
// `row.surface = surf;` still exists - it MUST, that is how an operator's
// own choice is restored. What must not exist is that line running for a row
// nobody chose. So pin the ORDER: the guard opens before the assignment, and
// the assignment appears exactly once in the function.
if (applied) {
  const guard = applied.indexOf('if (r.surface_by === "operator")');
  const assign = applied.indexOf("row.surface = surf;");
  const nAssign = (applied.match(/row\.surface = surf;/g) || []).length;
  check(guard >= 0 && assign > guard,
    "`row.surface = surf;` sits INSIDE the operator guard, not before it");
  check(nAssign === 1,
    "...and there is no second, ungated copy of it  (" + nAssign + ")");
}

// Two places set the operator marker, and both are correct: the click that
// makes the decision, and the restore that carries a past decision forward.
// A third would mean something else is minting operator authority.
const clicks = SRC.match(/row\.surfaceBy = "operator";/g) || [];
check(clicks.length === 2,
  "exactly two places mark a row operator-set: the click and the restore  ("
  + clicks.length + ")");
check(/b\.onclick = \(\) => \{[\s\S]{0,240}row\.surfaceBy = "operator";/.test(SRC),
  "...one of them is the EXIST/PROP/skip button");
check(applied && /row\.surfaceBy = "operator";/.test(applied),
  "...and the other is ewApplySaved carrying a past choice forward, so a "
  + "restored decision does not decay into an engine default on the next save");

// ===========================================================================
// 3. the disclosure
// ===========================================================================
const note = extract("ewSplitNote");
check(!!note, "ewSplitNote() found");
if (note) {
  const F = new Function(note + "\n return ewSplitNote;")();
  check(/you put this contour/i.test(F({ surfaceBy: "operator", splitBy: "legend" })),
    "an operator's own choice says so, outranking the engine's word");
  check(/legend/i.test(F({ splitBy: "legend" })),
    "a legend-decided row names the legend");
  check(/inked|grey|black/i.test(F({ splitBy: "color" })),
    "a colour-decided row says it is a drafting convention, not a statement "
    + "of the sheet");
  const w = F({ splitBy: "width" });
  check(/guess/i.test(w) && /check this one against/i.test(w),
    "a width-decided row is called a GUESS and tells the operator to check "
    + "it against the paper  (" + w.slice(0, 60) + "...)");
  check(/counted between/i.test(F({ splitBy: "interval-infill" })),
    "an infill row explains why it starts on skip");
  check(F(null) === "" && /not recorded/i.test(F({})),
    "no row, and a row with no provenance, both degrade honestly");
}

// ===========================================================================
// 4. the cache buster
// ===========================================================================
check(+((/app\.js\?v=(\d+)/.exec(HTML) || [])[1] || 0) >= 160,
  "app.js cache buster at or past 160 (a later bump is fine; an older one is not)");

console.log(failures ? "\n" + failures + " FAILED" : "\nall passed");
process.exit(failures ? 1 : 0);
