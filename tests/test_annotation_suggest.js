// DEFECT 0: ANNOTATION AND TITLE-BLOCK LINE WORK PRICED AS PIPE.
//
// A style row defaults ON unless it is white, textured, or classed
// `annotation`. Leaders, note boxes, dimension lines and title-block rules
// are STRAIGHT STROKES, so the classifier correctly calls them solid/dash1
// and they default on. Nothing looked at the CAD layer the drafter put them
// on. tradeFor() then matched STORM inside `P6-TEXT-STORM-C8.0` and filed it
// under Wet Utilities - priced as pipe - never reading the TEXT in the same
// string.
//
// MEASURED 2026-09-01 over the 116 cached detections in the real job store
// (read-only):
//     63 of 116 sheets (54%) carry at least one such family
//     1,582 runs and 347,440 pt currently count that should not
// (out/verify/2026-09-01/annotation-pricing/MEASURED.md quotes 2,126 runs /
// 394,566 pt. Re-measured with loadResult's default-on rule applied exactly
// as loadResult applies it the figures are 1,582 / 347,440; counting every
// name-matched non-annotation family regardless of whether it currently
// counts gives 2,201 / 399,716. The sheet count, 63, reproduces exactly.)
//
// ===========================================================================
// THE MATCHER MUST BE TOKEN-BASED. THIS IS THE WHOLE TEST.
// ===========================================================================
// A naive substring test for DIM matches `SE-P-SEDIMENT LOG` - se-DIM-ent -
// which is real erosion-control line work: 31 style rows, 37 runs and
// 11,408 pt across the corpus. Silently stopping that counting is PRECISELY
// the defect being fixed, reintroduced by the fix. A plain token test in turn
// misses `0046599.02TTLBLK|G-TBLK-LOGO` and `TBLK_SHEET`, which genuinely are
// title blocks with the drawing number fused onto the token.
//
// Measured over the 1,095 distinct CAD layer names in the corpus, a substring
// test and this token test disagree on exactly two, and both are in the
// fixtures below:
//     SE-P-SEDIMENT LOG   substring says annotation, and it is WRONG
//     SP-TITLES           substring says annotation, token says no. TITLES
//                         is not TITLE, and the token list is deliberately
//                         not pluralised into ambiguity. 62 runs / 4,919 pt
//                         keep counting. Recorded as a KNOWN CONSERVATIVE
//                         MISS, not as a win: erring toward counting is the
//                         safe direction here, but it is still a miss.
//
// A third real title block, TTL-BLK-B4UDIG, is missed by BOTH tests: the
// hyphen splits TTLBLK into TTL and BLK and neither is a marker, because a
// three-letter marker is how a substring test creeps back in (TTL is also
// "total", BLK also "black"). 6 runs / 678 pt keep counting. Recorded as
// a miss, not defended as a design.
//
// THOSE THREE ARE NOT THE WHOLE OF WHAT IS MISSED, AND THE FIRST CUT OF THIS
// FILE READ AS IF THEY WERE. Re-swept 2026-09-01 on the headline's own basis
// (rows that count today): six more layers whose names read as drafting
// furniture still count, 574 runs / 108,743 pt - 31% on top of the 347,440 pt
// the change removes, led by ZTB_Liberty Park|TB-Logo at 56,744 pt and the
// four BORDER layers at 44,373 pt together. Every one is a MUST_NOT_MATCH
// fixture below, with its footage, and the reason they are not markers is
// there too. The two figures above were also on a different basis from the
// headline (all rows: 65 / 5,037 and 14 / 1,025); both bases are in
// out/verify/2026-09-01/honesty-defect-zero/VERIFY.md.
//
// ===========================================================================
// WHY A VISIBLE GROUP AND NOT A SILENT DEFAULT-OFF
// ===========================================================================
// Every one of those runs is a case where two signals DISAGREE: the layer
// name says annotation, the measured geometry says line work. That is medium
// confidence. Flipping 347,440 pt out of the tally with nothing on screen
// would make an engine judgement invisible - the same sin as silently
// counting it, in the other direction. So the rows arrive UNTICKED in a
// group of their own, the header carries the stakes (families, footage,
// dollars where a rate is set), each row says why, and hovering the header
// paints exactly what stops counting.
//
// Dependency-free:  node tests/test_annotation_suggest.js

"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "webui", "app.js"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "webui", "style.css"), "utf8");

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

// The pure block, evaluated whole - the same technique test_line_style_groups
// uses, and the reason the matcher was put inside those markers.
const PURE = (function () {
  const a = SRC.indexOf("// STYLEGROUP-PURE-BEGIN");
  const b = SRC.indexOf("// STYLEGROUP-PURE-END");
  if (a < 0 || b < 0) throw new Error("STYLEGROUP-PURE markers missing");
  return SRC.slice(a, b);
})();
const F = new Function(PURE + `
  return { SG_DEFS, SG_ANNO_EXACT, SG_ANNO_SUFFIX, sgAnnoName, sgAnnoMarker,
           sgSuggested, sgGroupOf, sgKeyOf, sgPlan, sgReveal };`)();

// ===========================================================================
console.log("1. the matcher, against fixtures taken from the real corpus");
// ===========================================================================
// Verbatim from MEASURED.md, which took them off the cached detections.
const MUST_MATCH = [
  "00-NOTES", "P6-TEXT-STORM-C8.0", "G-ANNO-TTLB-THIN", "BG-ANNO-SYMB",
  "SP-TEXT-DIM", "TXT-NOTES", "TXT-PS", "DETAIL-DIM", "G-TTLB",
  "0046599.02TTLBLK|G-TBLK-LOGO", "TBLK_SHEET", "TITLEBLOCK-SHEET",
];
// The corpus's other big movers, so the fixture set is not just the note's.
const MUST_MATCH_MORE = [
  "TXT-COVER", "TXT - GRADING", "0046599.02TTLBLK|TITLEBLOCK - SHEET",
  "DIMS", "G-ANNO-TTLB", "DETAIL-TEXT", "P6-DETAIL-TEXT",
];
for (const n of MUST_MATCH.concat(MUST_MATCH_MORE))
  check(F.sgAnnoName(n) === true, "matches: " + n);

// THE ONE THAT MUST NOT MATCH, AND WHY IT IS THE POINT.
const MUST_NOT_MATCH = [
  ["SE-P-SEDIMENT LOG",
   "se-DIM-ent: real erosion-control line work, 37 runs / 11,408 pt across "
   + "the corpus. A substring test kills it, which IS the defect"],
  ["SP-TITLES",
   "TITLES is not TITLE - a known conservative miss, 62 runs / 4,919 pt "
   + "that keep counting. Erring toward counting is the safe direction"],
  ["TTL-BLK-B4UDIG",
   "a REAL title block the matcher misses: the hyphen splits TTLBLK into "
   + "TTL and BLK, and neither is a marker because three-letter markers are "
   + "how a substring test creeps back in. 6 runs / 678 pt keep counting. "
   + "A miss, recorded as one"],
  // THE MISSES THE FIRST CUT OF THIS FILE DID NOT NAME, AND THEY ARE THE BIG
  // ONES. Measured 2026-09-01 over the same 116 cached detections, on the
  // same basis as the headline (rows that COUNT TODAY): six layers whose
  // names read as drafting furniture, 574 runs / 108,743 pt still counting -
  // 31% on top of the 347,440 pt the change removes, and the four BORDER
  // layers alone (34 runs / 44,373 pt) outweigh the biggest family it does
  // remove (TXT-PS, 44,605 pt). Pinned here so the size of the miss cannot
  // change quietly. They are NOT markers on purpose: this corpus also carries
  // `BG-L-Metal edger`, so a planting-bed BORDER is a plausible real family
  // under that word, and nobody has opened these six sheets. A wrong catch
  // here is the defect being fixed, reintroduced by the fix.
  ["ZTB_Liberty Park|TB-Logo",
   "a title-block logo, 537 runs / 56,744 pt - the LARGEST single miss. TB "
   + "is not TBLK and LOGO is not a marker"],
  ["BORDER", "sheet border, 16 runs / 27,979 pt on 6 sheets"],
  ["SP-BORDER", "sheet border, 9 runs / 11,531 pt"],
  ["BG-SWCR-CB-MATERIALS|BG-SHEET MATCHLINE",
   "a match line, 3 runs / 7,626 pt"],
  ["SP-BORDER-LINE", "sheet border, 7 runs / 2,898 pt"],
  ["SE-BORDER", "sheet border, 2 runs / 1,965 pt"],
  ["P6-SHEET",
   "901 runs / 281,410 pt, and NOT claimed as a miss: SHEET names no "
   + "drawing element, and calling 281k pt furniture off a layer name is "
   + "exactly the reach this matcher exists to avoid. Here so that adding "
   + "SHEET as a marker has to argue with a number first"],
  // constructed neighbours of every marker that could plausibly be fused
  ["C-STORM-MAIN", "no annotation token anywhere"],
  ["SEDIMENT", "the bare word, in case the token rule ever gains a suffix"],
  ["V-NOTED-EASEMENT", "NOTED is not NOTE"],
  ["CU-STRM-LABELLED", "LABELLED is not LABEL"],
  ["C-DIMPLE", "DIMPLE is not DIM"],
  ["A-TEXTURE", "TEXTURE is not TEXT"],
  ["P-ANNOY", "ANNOY is not ANNO"],
  ["", "an empty layer name is not a claim about anything"],
  [null, "and neither is a missing one"],
];
for (const [n, why] of MUST_NOT_MATCH)
  check(F.sgAnnoName(n) === false,
    "does NOT match " + JSON.stringify(n) + " — " + why);

{
  // A SUBSTRING TEST WOULD FAIL THESE. Stated as an executable contrast so
  // the reason the matcher is shaped this way cannot be refactored away.
  const substring = (n) => {
    const U = String(n == null ? "" : n).toUpperCase();
    return F.SG_ANNO_EXACT.some((m) => U.indexOf(m) >= 0);
  };
  check(substring("SE-P-SEDIMENT LOG") === true
        && F.sgAnnoName("SE-P-SEDIMENT LOG") === false,
    "a substring test DOES match SE-P-SEDIMENT LOG and the token test does "
    + "not — this is the difference the fix turns on");
  check(substring("SP-TITLES") === true && F.sgAnnoName("SP-TITLES") === false,
    "...and the same on SP-TITLES");
  // ...and the suffix rule is what keeps the fused title blocks
  const plainToken = (n) => String(n == null ? "" : n).split(/[-_|.\s/]+/)
    .some((t) => F.SG_ANNO_EXACT.indexOf(t.toUpperCase()) >= 0);
  check(plainToken("0046599.02TTLBLK|G-TBLK-LOGO") === true,
    "0046599.02TTLBLK|G-TBLK-LOGO is caught by its G-TBLK token anyway");
  check(plainToken("0046599.02TTLBLK") === false
        && F.sgAnnoName("0046599.02TTLBLK") === true,
    "...but a bare fused 02TTLBLK needs the ENDS-WITH rule, which is why it "
    + "exists");
  // every suffix marker is also an exact marker, so the two lists agree
  for (const s of F.SG_ANNO_SUFFIX)
    check(F.SG_ANNO_EXACT.indexOf(s) >= 0,
      "suffix marker " + s + " is also an exact marker (one vocabulary)");
  // and no suffix marker is short enough to act like a substring test
  for (const s of F.SG_ANNO_SUFFIX)
    check(s.length >= 4,
      "suffix marker " + s + " is long enough that ENDS-WITH is not a "
      + "substring test in disguise");
}
{
  // the row's explanation quotes the token that did it, verbatim
  eq(F.sgAnnoMarker("P6-TEXT-STORM-C8.0"), "TEXT",
    "the marker is named, so the operator can disagree with the evidence");
  eq(F.sgAnnoMarker("G-ANNO-TTLB-THIN"), "ANNO / TTLB",
    "...and every marker when a name carries more than one");
  eq(F.sgAnnoMarker("0046599.02TTLBLK|G-TBLK-LOGO"), "02TTLBLK / TBLK",
    "...including one caught by the suffix rule");
}

// ===========================================================================
console.log("\n2. the tiers: only a DISAGREEMENT gets the new group");
// ===========================================================================
// detect_page.py seeds suggested_label from the layer tail, so a fixture
// without one would not exercise the label-keyed rate lookup at all.
const st = (o) => {
  const s = Object.assign(
    { style_id: 1, class: "solid", layer: "", length_pt: 1000, signature: "s" },
    o);
  if (s.suggested_label === undefined)
    s.suggested_label = String(s.layer || "").split("|").pop();
  return s;
};
{
  // tier 1: class annotation AND name annotation -> high confidence,
  // unchanged, stays in the Annotation group and stays default-off
  const a = st({ class: "annotation", layer: "TXT-NOTES" });
  eq(F.sgSuggested(a), false,
    "class annotation + annotation name is NOT a disagreement — it is "
    + "today's high-confidence case and it does not move");
  eq(F.sgGroupOf(a), "annotation", "...so it renders under Annotation");

  // tier 1b: class annotation, name says nothing. Also unchanged.
  const b = st({ class: "annotation", layer: "C-STORM-MAIN" });
  eq(F.sgSuggested(b), false, "class annotation alone is not a disagreement");
  eq(F.sgGroupOf(b), "annotation", "...and it stays where it was");

  // tier 2: THE DISAGREEMENT
  for (const cls of ["solid", "dash1", "dash2", "dash3", "dash-text", "hatchy"]) {
    const c = st({ class: cls, layer: "P6-TEXT-STORM-C8.0" });
    eq(F.sgSuggested(c), true,
      cls + " geometry under an annotation layer name IS the disagreement");
    eq(F.sgGroupOf(c), "suggested", "...and it renders in its own group");
  }

  // tier 3: neither -> counted, as now
  const d = st({ class: "dash1", layer: "SE-P-SEDIMENT LOG" });
  eq(F.sgSuggested(d), false,
    "SE-P-SEDIMENT LOG is not suggested off — it keeps counting");
  eq(F.sgGroupOf(d), "dashed", "...and stays in Dashed where it belongs");

  // NO LAYER AT ALL is not evidence either way. 23% of chains corpus-wide
  // carry none, and whole documents (Oncor C-04 among them) carry none.
  eq(F.sgSuggested(st({ layer: "" })), false,
    "no CAD layer means no disagreement to report");
  eq(F.sgSuggested(st({ layer: null })), false, "...and nor does a null one");
  eq(F.sgGroupOf(st({ layer: "" })), "solid", "...the row stays put");

  // THE OPERATOR'S OWN LABEL MUST NOT MOVE ROWS IN AND OUT OF THE GROUP.
  // The claim is about what the DRAFTER wrote; renaming a family is the
  // operator disagreeing with the engine, not new evidence for it.
  eq(F.sgSuggested(st({ layer: "C-STORM-MAIN",
                        suggested_label: "STORM TEXT NOTES" })), false,
    "only the CAD layer is consulted, never the label");
}

// ===========================================================================
console.log("\n3. sgPlan puts them in the group, and reports the stakes");
// ===========================================================================
{
  const styles = [
    st({ style_id: 1, class: "solid", layer: "C-CURB", length_pt: 5000 }),
    st({ style_id: 2, class: "dash1", layer: "SE-P-SEDIMENT LOG", length_pt: 2483 }),
    st({ style_id: 3, class: "annotation", layer: "TXT-NOTES", length_pt: 900 }),
    st({ style_id: 4, class: "dash1", layer: "P6-TEXT-STORM-C8.0", length_pt: 7408 }),
    st({ style_id: 5, class: "dash2", layer: "P6-TEXT-STORM-C8.0", length_pt: 2267 }),
    st({ style_id: 6, class: "solid", layer: "G-TTLB", length_pt: 1033 }),
  ];
  // the tick state a fresh load produces: suggested rows arrive UNTICKED
  const on = new Set([1, 2]);
  const plan = F.sgPlan(styles, { isOn: (s) => on.has(s.style_id) });
  const by = new Map(plan.groups.map((g) => [g.key, g]));

  check(by.has("suggested"), "the group exists on a sheet that has such rows");
  const g = by.get("suggested");
  eq(g.name, "Suggested disables", "and it is named for what it is");
  eq(g.nAll, 3, "three families disagree");
  eq(g.tick.state, "none", "all three arrive unticked");
  eq(g.lenOn, 0, "...so nothing in it is counted");
  eq(g.lenOff, 7408 + 2267 + 1033,
    "and lenOff is the footage the suggestion is HOLDING OUT — the number "
    + "a silent default-off would never have shown");
  eq(g.lenAll, 10708, "lenAll is on + off");
  eq(g.suggested, true, "the group flags itself so the header can differ");

  // the rows that must NOT have moved
  eq(by.get("solid").nAll, 1, "C-CURB stays in Solid");
  eq(by.get("dashed").nAll, 1, "SE-P-SEDIMENT LOG stays in Dashed");
  eq(by.get("dashed").lenOn, 2483,
    "...and it is still COUNTED — the whole point of the token matcher");
  eq(by.get("annotation").nAll, 1, "the annotation-classed row is untouched");
  eq(plan.nAll, 6, "every style is accounted for exactly once");

  // ORDER: the operator reads Solid, Dashed, Annotation, then the suggestion.
  eq(plan.groups.map((x) => x.key).join(","),
    "solid,dashed,annotation,suggested",
    "the group renders after the three real ones, not buried among them");

  // a group with no such rows must not appear at all - no empty header on
  // the 53 of 116 sheets that have none, and none on Oncor C-04, which
  // carries no CAD layers whatsoever
  const none = F.sgPlan([st({ style_id: 1, layer: "" })], {});
  check(!none.groups.some((x) => x.key === "suggested"),
    "a sheet with no disagreement grows no header for one");

  // TICKING ONE BACK ON IS VISIBLE IN THE HEADER, not silently absorbed
  const on2 = new Set([1, 2, 4]);
  const g2 = F.sgPlan(styles, { isOn: (s) => on2.has(s.style_id) })
    .groups.find((x) => x.key === "suggested");
  eq(g2.tick.state, "some", "putting one back makes the group part-ticked");
  eq(g2.lenOn, 7408, "...the header says what was put back");
  eq(g2.lenOff, 3300, "...and what is still held out");

  // and the reveal path knows where the row went
  const rv = F.sgReveal(styles[3], {});
  eq(rv.group, "suggested",
    "revealStyleRow sends the operator to the right header, not to Dashed");
}

// ===========================================================================
console.log("\n4. loadResult seeds them OFF - the actual behaviour change");
// ===========================================================================
// The real seeding loop, lifted out of loadResult and run, rather than a
// grep for the function name: the point is which rows end up false.
{
  const body = fnSrc("loadResult");
  const a = body.indexOf("for (const s of state.result.styles) {");
  if (a < 0) throw new Error("loadResult's include-seed loop moved");
  let d = 0, end = -1;
  for (let k = body.indexOf("{", a); k < body.length; k++) {
    if (body[k] === "{") d++;
    else if (body[k] === "}" && --d === 0) { end = k + 1; break; }
  }
  const loop = body.slice(a, end);
  const styles = [
    st({ style_id: 1, class: "solid", layer: "C-CURB", length_pt: 5000 }),
    st({ style_id: 2, class: "dash1", layer: "SE-P-SEDIMENT LOG", length_pt: 2483 }),
    st({ style_id: 3, class: "annotation", layer: "TXT-NOTES", length_pt: 900 }),
    st({ style_id: 4, class: "dash1", layer: "P6-TEXT-STORM-C8.0", length_pt: 7408 }),
    st({ style_id: 5, class: "solid", layer: "G-TTLB", length_pt: 1033 }),
    st({ style_id: 6, class: "solid", layer: "C-PAD", length_pt: 300 }),
    st({ style_id: 7, class: "solid", layer: "C-EX", length_pt: 9000,
         color: [0.99, 0.99, 0.99] }),
    st({ style_id: 8, class: "dash1", layer: "C-HATCH", texture: true }),
  ];
  const state = { result: { styles }, include: new Map(), labels: new Map(),
                  seedLabels: new Map() };
  // the seed rule itself moved into includeSeed (UI verify 2026-10-02 #7: "engine's picks" reuses it)
  const includeSeed = new Function("sgSuggested", fnSrc("includeSeed") + "\nreturn includeSeed;")(F.sgSuggested);
  new Function("state", "sgSuggested", "includeSeed", loop)(state, F.sgSuggested, includeSeed);
  const inc = state.include;
  eq(inc.get(1), true, "ordinary long solid line work still counts");
  eq(inc.get(2), true,
    "SE-P-SEDIMENT LOG STILL COUNTS — the regression this fix must not cause");
  eq(inc.get(3), false, "annotation-classed rows are off, as before");
  eq(inc.get(4), false, "P6-TEXT-STORM-C8.0 (dash1) arrives UNTICKED");
  eq(inc.get(5), false, "G-TTLB (solid, 1,033 pt) arrives UNTICKED");
  eq(inc.get(6), false, "a short unnamed solid is off for the old reason");
  eq(inc.get(7), false, "white line work is off for the old reason");
  eq(inc.get(8), false, "texture is off for the old reason");
}

// ===========================================================================
console.log("\n5. tradeFor's annotation test reads the CAD LAYER, first");
// ===========================================================================
// CLAUDE.md section 5, "first-writer-wins discards the right answer": a
// first-match-wins ladder over substrings with no explicit ranking. STORM
// won inside `P6-TEXT-STORM-C8.0` and the row was filed as pipe.
//
// THE FIRST CUT OF THAT FIX WAS THE SAME DEFECT ONE MIRROR OVER. It ran the
// matcher over `label` - the OPERATOR's / the LEGEND's words, which is what
// the itemised panel and the export's `trades:` map group by - and returned
// unconditionally above the whole ladder. Measured with the shipped ladder:
//
//   PROPOSED 8" WATER MAIN - SEE NOTE 4              General, not Wet Utilities
//   PROPOSED 4" PVC FOOTER DRAIN PIPE (SEE NOTE 3)   General, not Wet Utilities
//   SILT FENCE, SEE NOTES                            General, not Fencing
//   CONTOUR LABEL                                    General, not Earthwork
//
// NOTE / NOTES / LABEL beat an explicit trade keyword standing in the same
// string, with no ranking. No corpus layer does this today - all 22 labels
// the early return moves across the 116 cached detections are genuine text
// layers - but legend English already reaches tradeFor: `ADDENDUM 1 NOTES
// (legend)` (4,058 pt) and `CONSTRUCTION NOTES (legend)` (1,221 pt) are both
// in the corpus. It was one legend row away.
//
// So the early return takes the CAD LAYER as its own argument, which is what
// sgSuggested() consults and for the reason its comment gives: the layer is
// what the DRAFTER wrote. No layer evidence => the ladder decides alone.
{
  const TR = SRC.slice(SRC.indexOf("const TRADES = ["),
                       SRC.indexOf("function tradeFor(label, layers)"));
  check(TR.indexOf("function tradeAnnoLayers") >= 0,
    "the lifted slice really contains tradeAnnoLayers (the boundary string "
    + "still matches tradeFor's signature)");
  const saved = new Map();
  const scope = { store: { getItem: (k) => saved.get(k) || null } };
  const names = Object.keys(scope);
  const tradeFor = new Function(...names,
    PURE + "\n" + TR + fnSrc("tradeFor") + "\nreturn tradeFor;")
    (...names.map((n) => scope[n]));

  // ---- with the CAD layer supplied, the early return does its job --------
  eq(tradeFor("P6-TEXT-STORM-C8.0", "P6-TEXT-STORM-C8.0"), "General",
    "THE FIXED DEFECT: an annotation layer is no longer filed as Wet "
    + "Utilities on the strength of the word STORM");
  eq(tradeFor("TXT-NOTES", "TXT-NOTES"), "General", "TXT-NOTES is not a trade");
  eq(tradeFor("G-ANNO-TTLB-THIN", "G-ANNO-TTLB-THIN"), "General",
    "nor is a title block");
  eq(tradeFor("DETAIL-DIM", "DETAIL-DIM"), "General", "nor a dimension layer");
  eq(tradeFor("P5-GRADE-LBL-TEXT-2", "P5-GRADE-LBL-TEXT-2"), "General",
    "...and GRADE inside a TEXT layer no longer wins either");
  // the layer is read even when the operator has RENAMED the row: that is
  // the whole point of consulting the drafter's name instead of the label
  eq(tradeFor("storm line, keep", "P6-TEXT-STORM-C8.0"), "General",
    "a renamed row is still judged on the layer the drafter wrote");

  // ---- THE REGRESSION THIS SUITE EXISTS FOR ------------------------------
  // the same four labels with NO layer evidence keep their trade keyword.
  eq(tradeFor('PROPOSED 8" WATER MAIN - SEE NOTE 4'), "Wet Utilities",
    "an operator's label that merely CITES a note is still Wet Utilities — "
    + "NOTE must not beat WATER MAIN");
  eq(tradeFor('PROPOSED 4" PVC FOOTER DRAIN PIPE (SEE NOTE 3)'), "Wet Utilities",
    "...nor beat DRAIN PIPE");
  eq(tradeFor("SILT FENCE, SEE NOTES"), "Fencing & Sitework",
    "...nor beat SILT FENCE");
  eq(tradeFor("CONTOUR LABEL"), "Earthwork & Grading",
    "...and LABEL must not beat CONTOUR");
  // LEGEND ENGLISH, verbatim from the corpus, on its real CAD layer. Both of
  // these land in General either way — their labels carry no trade word — so
  // the assertion that matters is that they get there by the ladder falling
  // through and NOT by an early return. The third line is the proof: same
  // layer, a legend label that does name a trade, and the trade survives.
  const RWAY = "23-084 Billings #3 Lot Lines|VZ-PROP-RWAY";
  eq(tradeFor("ADDENDUM 1 NOTES (legend)", RWAY), "General",
    "corpus legend row, 4,058 pt: General, and nothing in the layer said so");
  eq(tradeFor("CONSTRUCTION NOTES (legend)", "V-TRAF"), "General",
    "corpus legend row, 1,221 pt, same");
  eq(tradeFor("PROPOSED STORM DRAIN — SEE NOTES (legend)", RWAY), "Wet Utilities",
    "THE ONE THAT WAS ONE LEGEND ROW AWAY: a legend label carrying both a "
    + "trade word and the word NOTES keeps its trade, because the layer "
    + "behind it is real line work");

  // ---- what counts as layer evidence ------------------------------------
  eq(tradeFor("P6-TEXT-STORM-C8.0", []), "Wet Utilities",
    "an EMPTY layer list is not evidence: 23% of chains corpus-wide carry no "
    + "layer, and absence must not be read as a verdict");
  eq(tradeFor("P6-TEXT-STORM-C8.0", [""]), "Wet Utilities",
    "...and neither is a blank layer name");
  eq(tradeFor("P6-TEXT-STORM-C8.0", [null]), "Wet Utilities",
    "...or a missing one");
  eq(tradeFor("mixed row", ["TXT-NOTES", "C-STORM-MAIN"]), "General",
    "a row fed by TWO families, only one of them named TEXT, is NOT swept to "
    + "General by the early return (it lands there by the ladder here, but "
    + "not by the annotation test — the next assertion is the one that "
    + "proves it)");
  eq(tradeFor("storm main", ["TXT-NOTES", "C-STORM-MAIN"]), "Wet Utilities",
    "...proof: the same mixed pair with a trade word in the label keeps its "
    + "trade. Every contributing layer has to say annotation");
  eq(tradeFor("storm main", ["TXT-NOTES", "G-ANNO-TTLB"]), "General",
    "...and when every contributing layer DOES say annotation, it fires");

  // ---- EVERY OTHER TRADE IS UNCHANGED -----------------------------------
  // An early return that swallowed real linework would be a worse defect
  // than the one it fixes.
  eq(tradeFor("PROPOSED STORM DRAIN PIPE"), "Wet Utilities",
    "real storm pipe is still Wet Utilities");
  eq(tradeFor("SE-P-SEDIMENT LOG", "SE-P-SEDIMENT LOG"), "General",
    "the sediment log is not annotation — and se-DIM-ent must not make it so");
  eq(tradeFor("EXISTING CONTOUR"), "Earthwork & Grading", "contours unchanged");
  eq(tradeFor("CONCRETE FOOTING"), "Footing & Foundation", "footings unchanged");
  eq(tradeFor("CONCRETE SIDEWALK"), "Paving & Concrete", "flatwork unchanged");
  eq(tradeFor("SILT FENCE"), "Fencing & Sitework", "sitework unchanged");
  eq(tradeFor("ELECTRIC CONDUIT"), "Dry Utilities", "dry utilities unchanged");
  eq(tradeFor("SHEAR WALL"), "Structures", "structures unchanged");

  // AND THE OPERATOR STILL OUTRANKS THE ENGINE. A trade set by hand is a
  // decision; the early return must sit behind it, not in front of it.
  saved.set("trade::P6-TEXT-STORM-C8.0", "Wet Utilities");
  eq(tradeFor("P6-TEXT-STORM-C8.0", "P6-TEXT-STORM-C8.0"), "Wet Utilities",
    "a trade the operator set by hand still wins over the annotation test");
  saved.clear();

  // the ranking is real, not incidental: the annotation test must appear
  // before the first trade arm in the source...
  const t = fnSrc("tradeFor");
  check(t.indexOf("tradeAnnoLayers(layers)") >= 0
        && t.indexOf("tradeAnnoLayers(layers)") < t.indexOf("Earthwork & Grading"),
    "the annotation test is FIRST in the ladder, not appended to the end");
  // ...and it must not have quietly gone back to reading the label
  check(!/sgAnnoName\s*\(\s*(label|L)\s*\)/.test(t),
    "tradeFor never runs the annotation matcher over the LABEL — that is the "
    + "defect, and it is cheap to reintroduce by deleting one argument");
}

// ===========================================================================
console.log("\n5b. both trade callers pass the CAD layer, from one map");
// ===========================================================================
// A fix that only reached one of the two call sites would leave the export's
// `trades:` map disagreeing with the itemised panel it is supposed to mirror.
{
  const mapSrc = fnSrc("tradeLayerMap");
  check(/state\.result[\s\S]*styles/.test(mapSrc),
    "tradeLayerMap reads the STYLE rows — where the CAD layer lives");
  check(/state\.include\.get/.test(mapSrc),
    "...only the included ones, the same gate computeTakeoffRows applies");
  check(/state\.labels\.get\(s\.style_id\) \|\| "unlabeled"/.test(mapSrc),
    "...and keys them by the SAME label expression computeTakeoffRows uses, "
    + "or the map would miss the rows it is built for");
  check(!/chainLabels|extraItems/.test(mapSrc),
    "a chain label or a calculator row has no drafter's layer behind it and "
    + "must not be given one");

  // Both callers, in the source, with COMMENTS STRIPPED — prose above a fix
  // must not satisfy the assertion the fix is supposed to earn, and this
  // file's own comments name tradeFor() a dozen times. A call with no second
  // argument is the shape of the defect.
  const CODE = SRC.replace(/\/\/[^\n]*/g, "").replace(/\/\*[\s\S]*?\*\//g, "");
  const live = [];
  for (let i = CODE.indexOf("tradeFor("); i >= 0;
           i = CODE.indexOf("tradeFor(", i + 1)) {
    if (/[\w$.]/.test(CODE[i - 1] || "")) continue;          // ...tradeForX(
    if (/function\s+$/.test(CODE.slice(Math.max(0, i - 20), i))) continue;
    let d = 0, k = CODE.indexOf("(", i);
    for (; k < CODE.length; k++) {
      if (CODE[k] === "(") d++;
      else if (CODE[k] === ")" && --d === 0) break;
    }
    live.push(CODE.slice(i, k + 1));
  }
  check(live.length === 2,
    "exactly two live tradeFor call sites — the panel and the approve payload"
    + "  (found " + JSON.stringify(live) + ")");
  for (const c of live)
    check(/^tradeFor\([^,]+,/.test(c),
      "call site passes a layer argument: " + c);
  check(SRC.indexOf("tradeFor(it.label, trLayers.get(it.label))") >= 0,
    "the itemised panel passes the layers for the row's own label");
  check(SRC.indexOf("tradeFor(l, trLayers.get(l))") >= 0,
    "and the approve payload passes them from the same map");
}

// ===========================================================================
console.log("\n6. the header carries the stakes, and hover paints them");
// ===========================================================================
function elem(tag) {
  const e = {
    tagName: tag, className: "", textContent: "", title: "", type: "",
    hidden: false, checked: false, indeterminate: false, dataset: {},
    children: [], style: {},
    append(...cs) { cs.forEach((c) => this.children.push(c)); },
    appendChild(c) { this.children.push(c); return c; },
    setAttribute() {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    querySelector(sel) {
      const want = sel.replace(".", "");
      const hit = (n) => {
        if ((n.className || "").split(" ").indexOf(want) >= 0) return n;
        for (const c of n.children || []) { const r = hit(c); if (r) return r; }
        return null;
      };
      for (const c of this.children) { const r = hit(c); if (r) return r; }
      return null;
    },
    get _text() {
      return this.textContent
        + this.children.map((c) => (c._text === undefined ? "" : c._text)).join(" ");
    },
  };
  return e;
}
function head(opts) {
  const o = opts || {};
  const rows = o.rows || [
    st({ style_id: 4, class: "dash1", layer: "P6-TEXT-STORM-C8.0", length_pt: 7408 }),
    st({ style_id: 5, class: "dash2", layer: "P6-TEXT-STORM-C8.0", length_pt: 2267 }),
    st({ style_id: 6, class: "solid", layer: "G-TTLB", length_pt: 1033 }),
  ];
  const on = o.on || new Set();
  const plan = F.sgPlan(rows.concat(
    [st({ style_id: 1, class: "solid", layer: "C-CURB", length_pt: 5000 })]),
    { isOn: (s) => on.has(s.style_id) });
  const g = plan.groups.find((x) => x.key === "suggested");
  const hot = [];
  const scope = {
    document: { createElement: elem },
    state: {
      result: { scale: { ft_per_paper_inch: 20 } },
      include: new Map([...on].map((k) => [k, true])),
      measureMode: new Map(o.ea ? [[o.ea, "object"]] : []),
      labels: new Map(),
    },
    store: { getItem: (k) => (o.rates || {})[k] || null },
    priceKey: (l) => "price:" + l,
    styleLenPt: (s) => s.length_pt,
    fmtQty: (pt) => Math.round(pt / 72 * 20) + " LF",
    LF: "\n",
    hotFam: (sids) => hot.push(sids),
    hotFamOff: () => hot.push(null),
    sgSetClosed: () => {}, renderStyles: () => {}, sgTickGroup: () => {},
  };
  const names = Object.keys(scope);
  const el = new Function(...names,
    PURE + "\n" + fnSrc("sgSuggestMoney") + "\n" + fnSrc("sgSuggestStakesInto")
    + "\n" + fnSrc("sgHeadEl") + "\nreturn sgHeadEl;")
    (...names.map((n) => scope[n]))(g);
  return { el, g, hot };
}
{
  const { el, g } = head({});
  const txt = el._text;
  check(/Suggested disables/.test(txt), "the header names the group");
  check(/3 families/.test(txt),
    "the header says HOW MANY families the suggestion covers");
  // 10,708 pt at 1in = 20ft is 2,974 ft - the figure MEASURED.md quotes for
  // this exact sheet (C8.0-STORM-LAYOUT-Rev.1, layer P6-TEXT-STORM-C8.0)
  check(/2974 LF held out/.test(txt),
    "...and the FOOTAGE it is holding out of the takeoff, in the sheet's "
    + "own units — the number a silent default-off would have hidden");
  check(/layer names say annotation, geometry says line work/.test(txt),
    "...and, in one line, why the engine is only suggesting");
  check(/nothing counted/.test(txt),
    "the ordinary footage chip still reads honestly for an all-off group");
  eq(g.tick.state, "none", "(and the group really is all-off)");
}
{
  // THE PRICED DELTA, where a rate exists. "no rate set" and "$0" are
  // different facts and the header must not conflate them.
  const bare = head({}).el._text;
  check(!/\$/.test(bare),
    "with no rate set the header states no dollar figure at all");
  const priced = head({ rates: { "price:P6-TEXT-STORM-C8.0": "31" } }).el._text;
  // two of the three rows carry that label: (7408 + 2267) / 72 * 20 = 2687.5 ft
  check(/\$83,3\d\d at your rates/.test(priced),
    "with a rate set it prices the suggestion  (got: "
    + (priced.match(/\$[\d,]+/) || ["none"])[0] + ")");
  check(!/\$0 /.test(priced), "and does not print a bare $0");
}
{
  // a row the operator ticked BACK ON is reported, not absorbed
  const { el } = head({ on: new Set([4]) });
  const txt = el._text;
  check(/2058 LF you put back/.test(txt),
    "footage the operator ticked back on is named separately");
  check(/917 LF held out/.test(txt), "...and the remainder is still stated");
}
{
  // an EA row is excluded from the money rather than guessed at
  const { el } = head({ ea: 4, rates: { "price:P6-TEXT-STORM-C8.0": "31" } });
  const titles = (function walk(n) {
    return [n.title || ""].concat(
      (n.children || []).map(walk).reduce((a, b) => a.concat(b), []));
  })(el).join(" ");
  check(/priced per each/.test(titles),
    "a row priced per each is named, not multiplied by a per-foot rate");
}
{
  // HOVER PAINTS EXACTLY WHAT STOPS COUNTING
  const { el, hot } = head({});
  check(typeof el.onmouseenter === "function",
    "the header has a hover handler");
  el.onmouseenter();
  check(Array.isArray(hot[0]) && hot[0].join(",") === "4,5,6",
    "...which lights EVERY family in the group, and only those  (got "
    + JSON.stringify(hot[0]) + ")");
  el.onmouseleave();
  eq(hot[1], null, "...and clears it on the way out");
}
{
  // an ORDINARY group gets no stakes line and no hover - the new behaviour
  // is scoped to the group that needs it
  const rows = [st({ style_id: 1, class: "solid", layer: "C-CURB",
                     length_pt: 5000 })];
  const plan = F.sgPlan(rows, { isOn: () => true });
  const g = plan.groups.find((x) => x.key === "solid");
  eq(g.suggested, false, "the Solid group does not flag itself as suggested");
}
{
  // THE STAKES LINE MUST NOT GO STALE ON AN IN-PLACE REFRESH.
  // renderStyles() rebuilds every row and throws away the scroll position on
  // a 280-row sheet, so an edit refreshes the headers through sgSyncHeads()
  // instead. That function rewrote the footage chip and the count and left
  // the stakes line asserting the figure the group had BEFORE the edit - a
  // confident wrong number on the one line the operator is meant to decide
  // by. Asserted by RUNNING it: build the header at three families, delete
  // one, sync, read the line back.
  let plan = null;
  let head = null;
  const el2 = (tag) => ({
    tagName: tag, className: "", textContent: "", title: "", type: "",
    hidden: false, checked: false, indeterminate: false, dataset: {},
    children: [], style: {}, innerHTML: "",
    append(...cs) { cs.forEach((c) => this.children.push(c)); },
    appendChild(c) { this.children.push(c); return c; },
    setAttribute() {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    querySelector(sel) {
      const want = sel.replace(".", "");
      const hit = (n) => {
        if ((n.className || "").split(" ").indexOf(want) >= 0) return n;
        for (const c of n.children || []) { const r = hit(c); if (r) return r; }
        return null;
      };
      for (const c of this.children) { const r = hit(c); if (r) return r; }
      return null;
    },
    get _text() {
      return this.textContent + this.children.map(
        (c) => (c._text === undefined ? "" : c._text)).join(" ");
    },
  });
  const rows3 = [
    st({ style_id: 4, class: "dash1", layer: "P6-TEXT-STORM-C8.0", length_pt: 7408 }),
    st({ style_id: 5, class: "dash2", layer: "P6-TEXT-STORM-C8.0", length_pt: 2267 }),
    st({ style_id: 6, class: "solid", layer: "G-TTLB", length_pt: 1033 }),
  ];
  const scope = {
    document: { createElement: el2, querySelectorAll: () => (head ? [head] : []) },
    state: { result: { styles: rows3, scale: { ft_per_paper_inch: 20 } },
             include: new Map(), measureMode: new Map(), labels: new Map() },
    store: { getItem: () => null },
    priceKey: (l) => "price:" + l,
    styleLenPt: (s) => s.length_pt,
    fmtQty: (pt) => Math.round(pt / 72 * 20) + " LF",
    LF: "\n",
    hotFam: () => {}, hotFamOff: () => {},
    sgSetClosed: () => {}, renderStyles: () => {}, sgTickGroup: () => {},
    sgCurrentPlan: () => plan,
  };
  const names2 = Object.keys(scope);
  const mk = new Function(...names2,
    PURE + "\n" + fnSrc("sgSuggestMoney") + "\n" + fnSrc("sgSuggestStakesInto")
    + "\n" + fnSrc("sgHeadEl") + "\n" + fnSrc("sgSyncHeads")
    + "\nreturn { sgHeadEl, sgSyncHeads };")
    (...names2.map((n) => scope[n]));

  plan = F.sgPlan(rows3, { isOn: () => false });
  head = mk.sgHeadEl(plan.groups.find((x) => x.key === "suggested"));
  head.dataset.group = "suggested";
  check(/2974 LF held out/.test(head._text),
    "the header starts at 2,974 LF across three families");

  // an edit removes the biggest family; the plan now has two
  const rows2 = rows3.slice(1);
  scope.state.result.styles = rows2;
  plan = F.sgPlan(rows2, { isOn: () => false });
  mk.sgSyncHeads();
  check(/917 LF held out/.test(head._text),
    "...and the in-place refresh moves it to 917 LF — THE FIXED DEFECT: it "
    + "used to keep asserting 2,974 after the line work was gone  (got "
    + JSON.stringify((head._text.match(/[\d,]+ LF held out/) || ["none"])[0])
    + ")");
  check(/2 families/.test(head._text), "...and the family count with it");
}

// ===========================================================================
console.log("\n7. the CSS says 'look at this', never 'this is broken'");
// ===========================================================================
{
  const hd = (CSS.match(/\n\.sg-head\.sg-suggest \{[^}]*\}/) || [""])[0];
  check(!!hd, "the suggested header has a rule of its own");
  check(/var\(--t-warn/.test(hd),
    "...in the warn vocabulary the app already uses for 'look at this'");
  check(!/--t-(err|danger|bad)/.test(hd),
    "...and NOT in the error vocabulary: nothing here is broken, the engine "
    + "is unsure");
  const sub = (CSS.match(/\n\.sg-sub \{[^}]*\}/) || [""])[0];
  check(/flex-basis:\s*100%/.test(sub),
    "the stakes line gets its own line — it must never share with the "
    + "footage chip");
  const rowr = (CSS.match(/\n\.style-row-sug \{[^}]*\}/) || [""])[0];
  check(/box-shadow/.test(rowr) && !/border-left/.test(rowr),
    "the row marker is an inset shadow, not a border: .style-row is a grid "
    + "and a 3px border would move every column on exactly these rows");
  check(/\n\.style-sug \{[^}]*grid-column:\s*3 \/ -1/.test(CSS),
    "the per-row reason spans the row like the CAD layer and scope lines");
}

if (fails) {
  console.error("\n" + fails + " check(s) failed");
  process.exit(1);
}
console.log("\nall annotation-suggest checks passed");
