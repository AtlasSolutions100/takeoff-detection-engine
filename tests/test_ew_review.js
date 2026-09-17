// The contour REVIEW SURFACE: how a row is classified, how the doubtful ones
// are found, and the exact words the coverage statement uses.
//
// WHAT THIS PINS, and why each half is here.
//
// A. THE CLASSIFICATION. The owner's business partner ran Calculate on a demo
//    sheet on 2026-09-02, saw by eye that two contours the sheet prints as
//    562 and 563 had been given 564 and 570, and asked for every contour line
//    to be named individually - "by the elevation the engine thinks it is, or
//    marked unknown". Five separations have to survive: no elevation at all;
//    digits a machine read off a picture (how="gap-ocr"); inferred rather
//    than read (inherited / interval-infill); the nearest printed number
//    BESIDE the line (how="radius"); and a label read out of the sheet's own
//    text (how="inline"). The fifth of those is not in the brief's list and
//    the measurement forced it: on Oncor C-04, 18 of the 20 `radius` rows
//    carry a fractional elevation on a sheet whose contours step by 1 ft, and
//    8 of those 18 carry a legend-keyed spot tag (4 BW, 2 EW, 1 TW, 1 ME;
//    10 is the tag count over all 20 radius rows - the earlier "10 of those"
//    did not reproduce, re-measured 2026-09-03). Folding
//    `radius` into "printed" is what let 551.2 BW pass as a contour.
//
// B. THE OFF-INTERVAL RANK, with both guards. A number that is not a step on
//    the sheet's own contour interval cannot be one of its contour
//    elevations. That is the sharpest signal in the measurement - it finds 23
//    of C-04's 150 rows and it is the class that holds the stolen spot
//    heights - and it is also the one that could condemn half a sheet if the
//    interval were read wrong. So: it needs EW_IV_MIN_STEPS agreeing steps
//    behind it, AND it switches itself off (saying why) when more than
//    EW_OFF_IV_MAX_SHARE of the elevations are off the step, because at that
//    point the interval reading is what is in doubt.
//
// C. THE WORDING. One function writes the coverage sentence for the panel,
//    for the basis block beside the number and for the printed report -
//    ewCoverageText - so the three cannot drift, which is the arrangement
//    ewBasisNote and ewCautionText already use. Every sentence must state
//    what is NOT covered, not only what is (CLAUDE.md s5: a silent cap
//    reports as full coverage), and must carry counts on both sides rather
//    than a bare percentage (s2).
//
// webui/app.js is not a module and touches the DOM at load, so this test
// evals ONLY the block between the EW-REVIEW markers - kept pure (no DOM, no
// state.*, no fetch) for exactly that reason, like the CHECK-QUEUE block.
//
// Dependency-free like tests/test_gate.py:  node tests/test_ew_review.js
"use strict";
const fs = require("fs");
const path = require("path");
const R = (...p) => fs.readFileSync(path.join(__dirname, "..", ...p), "utf8");
const js = R("webui", "app.js");
const html = R("webui", "index.html");
const css = R("webui", "style.css");

let fails = 0;
const check = (ok, msg) => {
  if (ok) console.log("ok: " + msg);
  else { fails++; console.error("FAIL: " + msg); }
};
const eq = (got, want, msg) =>
  check(got === want, msg + "  (got " + JSON.stringify(got) + ")");

const a = js.indexOf("// EW-REVIEW-BEGIN");
const b = js.indexOf("// EW-REVIEW-END");
if (a < 0 || b < 0 || b <= a) {
  console.error("FAIL: EW-REVIEW markers not found in webui/app.js - the "
    + "review surface's pure block is where the classification and the "
    + "coverage wording live");
  process.exit(1);
}
const T = new Function(js.slice(a, b) + `
  return { EW_REVIEW, EW_REVIEW_ORDER, EW_IV_MIN_STEPS, EW_IV_TOL_FT,
           EW_OFF_IV_MAX_SHARE, EW_COVERAGE_HEAD, ewOnInterval,
           ewRowClassBase, ewIntervalUse, ewReviewClasses, ewCoverageText,
           ewCoverageNote };`)();

// ---------------------------------------------------------------------------
console.log("\n1. every mechanism gets its own class, and its own words");
// ---------------------------------------------------------------------------
eq(T.ewRowClassBase({ z: 545, how: "inline" }), "read",
  "a label in this line's own break is READ from the sheet");
eq(T.ewRowClassBase({ z: 551.2, how: "radius" }), "nearby",
  "the nearest printed number is NOT the same claim - its own class");
eq(T.ewRowClassBase({ z: 570, how: "gap-ocr" }), "machine",
  "digits recognised off a picture are MACHINE-read");
eq(T.ewRowClassBase({ z: 554, how: "inherited" }), "inferred",
  "an elevation welded along the contour is inferred");
eq(T.ewRowClassBase({ z: 566, how: "interval-infill" }), "inferred",
  "...and so is one counted between two other contours");
eq(T.ewRowClassBase({ z: 100, how: "some-reader" }), "unknown",
  "a reader we have not been taught ABSTAINS - it is not called printed");
eq(T.ewRowClassBase({ z: NaN, how: "inline" }), "none",
  "a row with no finite elevation carries none, whatever its how");
eq(T.ewRowClassBase({ noElev: true }), "none",
  "a contour line the reader never elevated is in the same class");
eq(T.ewRowClassBase({ z: 545, manual: true }), "typed",
  "an elevation the operator typed is neither read nor inferred");
eq(T.ewRowClassBase({ z: 545, how: "gap-ocr", manual: true }), "typed",
  "...and typing over a machine read retires the machine's claim");
// The five separations the brief demands are each a DIFFERENT class.
{
  const k = ["none", "machine", "inferred", "read", "nearby"];
  eq(new Set(k).size, 5, "no elevation / machine-read / inferred / read from "
    + "the sheet / printed nearby are five distinct classes");
  for (const c of k) {
    check(!!T.EW_REVIEW[c], c + " is in the review table");
    check((T.EW_REVIEW[c].why || "").length > 40,
      c + " carries a sentence saying what it means, not just a word");
    check(!!T.EW_REVIEW[c].chip, c + " has a filter label");
    check(!!T.EW_REVIEW[c].badge && T.EW_REVIEW[c].badge.length <= 14,
      c + " has a SHORT row badge - the row shares a ~200 px column with an "
      + "input, three buttons and a sentence, and the first live pass "
      + "rendered the filter label as a two-line pill overflowing its own "
      + "background");
  }
  check(T.EW_REVIEW_ORDER.length === Object.keys(T.EW_REVIEW).length,
    "every class in the table is in the sweep order - a class with no "
    + "position in the order would be a group with no chip");
  const ranks = T.EW_REVIEW_ORDER.map(k2 => T.EW_REVIEW[k2].rank);
  check(ranks.every((v, i) => i === 0 || v > ranks[i - 1]),
    "the sweep order and the ranks agree, so 'most doubtful first' is one "
    + "definition and not two");
  check(T.EW_REVIEW.read.rank > T.EW_REVIEW.machine.rank
     && T.EW_REVIEW.machine.rank > T.EW_REVIEW.nearby.rank
     && T.EW_REVIEW.nearby.rank > T.EW_REVIEW["off-interval"].rank,
    "a number the sheet cannot be stepping by outranks a number printed "
    + "beside the line, which outranks a transcription, which outranks a "
    + "label read out of the sheet's own text");
}

// ---------------------------------------------------------------------------
console.log("\n2. the contour interval: the sharpest signal, with its guards");
// ---------------------------------------------------------------------------
check(T.ewOnInterval(562, 1) && T.ewOnInterval(564, 1),
  "562 and 564 are steps on a 1 ft interval");
check(!T.ewOnInterval(551.2, 1) && !T.ewOnInterval(583.9, 1),
  "551.2 and 583.9 are not");
check(T.ewOnInterval(551.2, null) && T.ewOnInterval(551.2, 0),
  "with NO readable interval nothing is off it - absence of evidence is "
  + "not evidence");
check(T.ewOnInterval(NaN, 1), "a row with no elevation is not 'off' either");
check(T.ewOnInterval(560, 5) && !T.ewOnInterval(562, 5),
  "the same test at a 5 ft interval");
// The two guards.
{
  const rows = [{ z: 550, how: "inline" }, { z: 551, how: "inline" }];
  const few = T.ewIntervalUse({ interval_ft: 1, modal_count: 3 }, rows);
  check(few.ok === false && /too few/.test(few.why),
    "an interval only " + 3 + " steps agree on is a sample, not a "
    + "convention - the rank is off and the panel says why");
  check(T.EW_IV_MIN_STEPS >= 3,
    "and the threshold is a named constant, not a literal in a branch");
  const half = T.ewIntervalUse({ interval_ft: 1, modal_count: 20 },
    [{ z: 550.5, how: "radius" }, { z: 551.5, how: "radius" },
     { z: 552.5, how: "radius" }, { z: 553, how: "inline" }]);
  check(half.ok === false && /off a 1 ft step/.test(half.why),
    "when most elevations miss the step, the STEP is what is in doubt - "
    + "the rank switches itself off and states the count");
  check(/no row is ranked on it/.test(half.why),
    "...and says plainly that nothing was ranked on it");
  const good = T.ewIntervalUse({ interval_ft: 1, modal_count: 23 },
    [{ z: 550, how: "inline" }, { z: 551, how: "inline" },
     { z: 552, how: "inline" }, { z: 551.2, how: "radius" }]);
  check(good.ok === true && /step by 1 ft/.test(good.why)
     && /23 agreeing steps/.test(good.why),
    "a well-supported interval is used, and the panel prints the evidence "
    + "behind it (the value AND how many steps agree)");
  const none = T.ewIntervalUse(null, rows);
  check(none.ok === false && /do not step/.test(none.why),
    "a sheet whose labels do not step says so rather than ranking blind");
}
// The measured C-04 population: 20 radius rows of which 18 are fractional,
// 26 inherited of which 5 are, 69 gap-ocr all integral. Off-interval must
// take the 23 and leave the other 127 in their own classes.
{
  const rows = [];
  const rad = [551.2, 554.3, 557.2, 557.4, 557.6, 558.5, 558.8, 566.8, 567.8,
               569.9, 570.9, 582.5, 583.5, 583.6, 583.9, 584.4, 584.7, 585.9,
               569.0, 586.0];
  for (const z of rad) rows.push({ z: z, how: "radius" });
  const inh = [554, 554, 556, 557.4, 557.4, 558.8, 560, 561, 564, 565, 567,
               569, 570, 570, 572, 575, 576, 579, 583, 583, 583, 583, 583.9,
               583.9, 589, 590];
  for (const z of inh) rows.push({ z: z, how: "inherited" });
  for (let i = 0; i < 69; i++) rows.push({ z: 545 + i, how: "gap-ocr" });
  for (let i = 0; i < 35; i++)
    rows.push({ z: 546 + i, how: "interval-infill", inferred: true });
  const r = T.ewReviewClasses(rows, 218,
    { interval_ft: 1, modal_count: 23, share: 0.411 });
  eq(r.iv.ok, true, "C-04's 1 ft interval survives both guards");
  eq(r.counts["off-interval"], 23,
    "23 of C-04's 150 rows carry a number this sheet cannot be stepping by "
    + "- 18 stolen from spot annotations and 5 welded on from those");
  eq(r.counts.nearby, 2,
    "the two radius rows that ARE on the step stay in their own class");
  eq(r.counts.machine, 69, "every gap-OCR row is machine-read");
  eq(r.counts.inferred, 56, "the inferred rows that are on the step");
  eq(r.counts.read, 0,
    "NOT ONE of C-04's elevations is read out of the sheet's own text");
  eq(r.counts.none, 218,
    "and the 218 contour lines with no elevation are in the counts, not "
    + "quietly left out of the list they are missing from");
  eq(Object.values(r.counts).reduce((s, v) => s + v, 0), 368,
    "every one of the sheet's 368 contour lines is in exactly one group");
}

// ---------------------------------------------------------------------------
console.log("\n3. the coverage statement: what is NOT covered, in numbers");
// ---------------------------------------------------------------------------
// C-04's own numbers, with the set-aside classes the coverage dict has
// carried since 2026-09-03 (R2): the four filters above the family test
// used to drop their chains with no record at all, so the sentence claimed
// a share of a set it had quietly shrunk. 75 unclassed, and the sheet's
// legend excluded 4 families - the wider denominator is 368 + 75 + 12.
const C04 = { contour_chains: 368, with_elev: 150, no_elev: 218,
              contour_len_pt: 139582.7, with_elev_len_pt: 82309.6,
              unclassed_chains: 75, unclassed_len_pt: 21430.0,
              set_aside: {
                legend_noncontour: { n: 12, len_pt: 9100.0,
                  labels: ["PROPERTY LINE (legend)"] },
                excluded_other: { n: 0, len_pt: 0 },
                white: { n: 0, len_pt: 0 },
                unclassed: { n: 75, len_pt: 21430.0 },
                not_plan: { n: 3, len_pt: 8000.0 },
                no_chain_id: { n: 0, len_pt: 0 } },
              plan_chains: 455, plan_len_pt: 170112.7,
              page_chains: 458, page_len_pt: 178112.7 };
{
  const s = T.ewCoverageText(C04, null).join(" ");
  check(/368 contour lines/.test(s), "the denominator is stated");
  check(/150 carry an elevation/.test(s), "and the numerator");
  check(/218 carry none at all/.test(s),
    "and what is NOT covered, as a count - not left to subtraction");
  check(/59% of that contour footage/.test(s),
    "coverage by FOOTAGE too: C-04 is 41% of the lines and 59% of the "
    + "length, and a count-only statement understates it");
  // R2: and the SECOND share, over every stroke that could be a contour,
  // labelled as the floor. 82,309.6 / 170,112.7 = 48%.
  check(/Of all 455 lines of plan linework here \(170,113 pt\)/.test(s)
     && /elevated share is 48% - the floor/.test(s),
    "...and the wider share, which cannot overstate, said as the floor");
  check(/The 87 outside that count/.test(s)
     && /12 in families this sheet's legend named something other than a contour \(PROPERTY LINE\)/.test(s)
     && /75 in no contour family at all/.test(s)
     && /neither count/.test(s),
    "the cap on the denominator is said out loud, class by class, and "
    + "368 + 87 = the 455 the share above is over");
  check(/3 further lines \(8,000 pt\) are annotation or title-block geometry/
    .test(s),
    "...and the class deliberately left OUT of that denominator is named "
    + "with its count, so a reader who disagrees can add it back");
  check(!/^\s*\d+%/.test(s)
     && /\d+ of/.test(T.ewCoverageText(C04, { total: 41 })[2]),
    "no percentage travels without its two counts");
}
{
  const s = T.ewCoverageText(C04, { total: 41 });
  check(/41 of the 368 are assigned to a surface and behind this volume/
    .test(s[2]), "beside a volume it says how many contours are under it");
  check(/The other 327 are not in it/.test(s[2]),
    "...and how many of the sheet's contours are not");
}
// A sheet with no contour linework says nothing rather than dividing by zero.
eq(T.ewCoverageText({ contour_chains: 0, with_elev: 0, no_elev: 0,
                      contour_len_pt: 0, with_elev_len_pt: 0,
                      unclassed_chains: 0 }, null).length, 0,
  "a sheet with no identifiable contour linework makes no coverage claim");
eq(T.ewCoverageText(null, null).length, 0, "and neither does a missing read");
// Full coverage is stated as full coverage, without an empty clause.
{
  const s = T.ewCoverageText({ contour_chains: 12, with_elev: 12, no_elev: 0,
    contour_len_pt: 1000, with_elev_len_pt: 1000, unclassed_chains: 0 },
    null).join(" ");
  check(/12 carry an elevation \(100% of that contour footage\); 0 carry none at all\./
    .test(s), "a fully covered sheet reads as one, with no dangling clause");
}
// Singular/plural, because a one-line sheet is a real sheet.
{
  const s = T.ewCoverageText({ contour_chains: 1, with_elev: 1, no_elev: 0,
    contour_len_pt: 100, with_elev_len_pt: 100, unclassed_chains: 1 },
    null).join(" ");
  check(/1 contour line\./.test(s), "one line is a line, not lines");
  check(/That one line is in no contour family at all\./.test(s)
     && /If it is a contour it is in neither count\./.test(s),
    "...and one line set aside reads as one line, not as “The 1”");
}

// ---------------------------------------------------------------------------
console.log("\n4. the same words on paper, inside the note cap");
// ---------------------------------------------------------------------------
{
  const note = T.ewCoverageNote(C04, { total: 41 }, 400);
  eq(note.length, 1, "the report gets exactly one coverage note");
  check(note[0].startsWith(T.EW_COVERAGE_HEAD),
    "headed, so a reader of the PDF who never saw the panel knows what it is");
  check(note[0].length <= 400,
    "inside server.py approve()'s per-note cap  (len " + note[0].length + ")");
  // The words are the panel's, not a second author's: the note is the head
  // plus the panel's first k sentences, verbatim and in order, for some k.
  // (Checked as an exact prefix rather than sentence by sentence - the cap
  // drops the tail WHOLE, and "includes" would pass a note that had
  // reordered or reworded the sentences it kept.)
  {
    const parts = T.ewCoverageText(C04, { total: 41 });
    let k = -1;
    for (let i = parts.length; i >= 1; i--)
      if (note[0] === T.EW_COVERAGE_HEAD + parts.slice(0, i).join(" ")) {
        k = i; break;
      }
    check(k > 0, "the report is the panel's first " + k + " sentences "
      + "verbatim, of " + parts.length + " - no second author");
    check(k >= 2, "...and the FLOOR sentence, the one that cannot overstate, "
      + "is inside the cap rather than dropped off the tail");
  }
  // A tail that will not fit is dropped WHOLE, never cut mid-sentence.
  const tight = T.ewCoverageNote(C04, { total: 41 }, 200)[0];
  check(tight.length <= 200, "a tighter cap still fits  (len "
    + tight.length + ")");
  check(/\.$/.test(tight),
    "...and ends on a full stop - a whole sentence was dropped, not half of "
    + "one (the rule ewBasisNote and ewCautionText apply)");
  eq(T.ewCoverageNote(null, null, 400).length, 0,
    "no coverage read, no note - the report invents nothing");
}

// ---------------------------------------------------------------------------
console.log("\n5. it is wired to the three surfaces, and to the sheet");
// ---------------------------------------------------------------------------
check(/id="ewCoverage"/.test(html), "the panel has a coverage block");
check(/id="ewFilters"/.test(html), "...and a filter bar for the review");
check(/function renderEwCoverage\(\)/.test(js)
   && /renderEwCoverage\(\);/.test(js),
  "the panel head renders the coverage statement");
// ...and all three read ewCovNow(), the LIVE restatement, never the served
// dict: `state.ew.cov` is captured once and an operator can delete a row
// after it was captured (see ewCoverageLive, and tests/test_ew_live_
// coverage.js for the arithmetic).
// Since 2026-09-16 the block prints the FIRST sentence - the count - on its
// face and folds the arithmetic behind a button (four sentences in a row
// pushed the volumes off the panel's first screen). Same source, same
// sentences, nothing dropped: that is what this pins.
check(/const covSents = ewCoverageText\(ewCovNow\(\), covAcc\);/.test(js)
   && /btn\.textContent = "how this number was counted "/.test(js)
   && /if \(covSents\.length\) line\(esc\(covSents\[0\]\)\)\.className = "ew-basis-cov";/.test(js)
   && /for \(const sent of covSents\.slice\(1\)\)/.test(js),
  "the basis block BESIDE THE NUMBER renders the same sentences");
check(/\.\.\.ewCoverageNote\(ewCovNow\(\), acc, APPROVE_NOTE_MAX\)/
  .test(js), "and the printed report carries them through ewApproveNotes");
check(/cov: j\.contour_coverage \|\| null/.test(js),
  "the panel state holds the server's coverage block");
check(/unelev: \(j\.contour_coverage && j\.contour_coverage\.unelevated\)/
  .test(js),
  "...and the names of every contour line that carries no elevation");
// The review surface itself.
check(/function ewReviewView\(\)/.test(js),
  "one function builds the classified, filtered, ordered list - so the chip "
  + "counts, the rows on screen and the card count cannot disagree");
check(/function ewStubRow\(stub\)/.test(js) && /function ewAdoptStub\(/.test(js),
  "a contour with NO elevation is a row you can find and fix in place");
check(/ewAppendRow\(cid\)/.test(js.slice(js.indexOf("function ewAdoptStub("))),
  "...and fixing it goes through the same ewAppendRow the + Add contour "
  + "button uses, so a typed elevation is a `typed` row like any other");
check(/if \(!revealChain\(row\.chain_id\)\)/.test(js),
  "clicking a row goes list -> sheet through revealChain (select, centre, "
  + "flash), the gesture the per-family line list already uses");
check(/\["ewRowsCount", "#ewRows \.ew-row",\s*\n\s*\(\) => \(state\.ew && state\.ew\.rows\)/
  .test(js),
  "the card count knows the WHOLE contour list, so a filtered view reads "
  + "'23 of 368' and never reports the smaller number as the total");
check(/and " \+ view\.hidden \+ " more in this group/.test(js),
  "the row cap discloses itself and offers the rest");
// Styling exists for every class the chips and badges can wear, AND THE
// SELECTOR MATCHES WHAT app.js EMITS. The first version of this check asked
// only whether a rule existed and spelled the class its own way, so it
// passed against a stylesheet whose every rule was dead: app.js builds
// "c-" + key and "k-" + cls (hyphen), style.css declared ".coffinterval"
// (none). Measured live 2026-09-03 - every chip the same grey, every badge
// the same info-blue, the whole stop/warn/ok vocabulary reaching the screen
// as text only. So build the class the SAME WAY the source does and look
// for THAT.
const CHIP_CLS = (key) => "c-" + key.replace(/[^a-z]/g, "");
const BADGE_CLS = (cls) => "k-" + cls.replace(/[^a-z]/g, "");
check(/" c-" \+ key\.replace\(\/\[\^a-z\]\/g, ""\)/.test(js),
  "the chip class is still built as \"c-\" + key, the shape this check "
  + "mirrors (if that changes, change it here too)");
for (const k of ["offinterval", "nearby", "machine", "inferred", "read"])
  check(css.includes(".ew-chip." + CHIP_CLS(k))
        || css.includes(".ew-cls." + BADGE_CLS(k)),
    "the '" + k + "' class is styled under the selector app.js actually "
    + "emits (" + CHIP_CLS(k) + " / " + BADGE_CLS(k) + ")");
for (const k of ["none", "typed", "unknown"])
  check(css.includes(".ew-cls." + BADGE_CLS(k)),
    "and the '" + k + "' badge too - it is the dim outline that says no "
    + "elevation was read");
check(/\.ew-coverage \{/.test(css), "the coverage block is styled");
check(/\.ew-cls \{[^}]*white-space: nowrap/.test(css),
  "...and the row badge cannot wrap out of its own pill");
check(/cb\.textContent = EW_REVIEW\[it\.cls\]\.badge;/.test(js),
  "the row wears the short badge, the filter bar wears the long label");
// NOTHING HERE MAY MOVE A NUMBER. The review surface adds no arithmetic:
// no call in the pure block writes a z, a surface or a volume.
{
  const blk = js.slice(a, b);
  check(!/\.z\s*=/.test(blk) && !/\.surface\s*=/.test(blk),
    "the classification block assigns no elevation and no surface - it "
    + "makes rows FINDABLE, it does not correct them");
  check(!/fetch\(/.test(blk) && !/document\./.test(blk)
     && !/state\./.test(blk),
    "...and it stays pure, so this test can hold it to that");
}

console.log();
if (fails) { console.error(fails + " FAILED"); process.exit(1); }
console.log("all checks passed");
