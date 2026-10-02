// The bid view's grouping rule table, pinned.
//
// The bid view (EXCAVATION-PLAN.md section 7 item 1) regroups the itemized
// takeoff rows into bid-schedule buckets by a deterministic keyword table -
// no AI, no new quantities. These tests pin three things:
//
//   1. ORDER. First-match-wins is only deterministic if the order is
//      pinned: SILT FENCE must be erosion control before FENCE can make it
//      landscape, STORM SEWER must be storm before SEWER makes it sanitary,
//      HEADWALL must be a storm structure before WALL makes it a wall,
//      "Export Asphalt" must stay an earthwork calculator row before
//      ASPHALT makes it paving.
//   2. THE LABEL OUTRANKS THE LAYER. A FIRE HYDRANT filed on a PROP-LOT
//      layer is a hydrant; a garbled label on an ESMT layer is an easement.
//   3. THE SUMS ARE SUMS. bidGroupRows adds the rows it was given, per
//      unit, and never anywhere else; an unknown override name is ignored,
//      a valid one wins over the rule.
//
// webui/app.js is not a module and touches the DOM at load, so this test
// evals ONLY the block between the BIDVIEW-RULES markers - that block is
// kept pure (no DOM, no state.*) for exactly this reason. Same technique
// as tests/test_trench_math.js.
//
// Dependency-free like tests/test_gate.py:  node tests/test_bidview_rules.js

"use strict";
const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const a = src.indexOf("// BIDVIEW-RULES-BEGIN");
const b = src.indexOf("// BIDVIEW-RULES-END");
if (a < 0 || b < 0 || b <= a) {
  console.error("FAIL: BIDVIEW-RULES markers not found in webui/app.js");
  process.exit(1);
}
const block = src.slice(a, b);
const T = new Function(block + `
  return { BID_BUCKETS, BID_UNASSIGNED, BID_RULES, BID_LABEL_ONLY,
           bidNormText, bidTokenHit, bidBucketFor, bidSizeToken,
           bidGroupRows, bidBySize };`)();

let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); }
  else console.log("ok:", msg);
}
function bucket(label, layer) { return T.bidBucketFor(label, layer).bucket; }

// ---- the bucket list itself, pinned (display order; UNASSIGNED last) ----
check(T.BID_BUCKETS.length === 12, "twelve buckets (Gas and Electric/Comm joined on 2026-09-16 - Beaumont bids each dry utility's trench as its own line)");
check(T.BID_BUCKETS[T.BID_BUCKETS.length - 1] === T.BID_UNASSIGNED,
      "UNASSIGNED is the last bucket");
check(new Set(T.BID_BUCKETS).size === T.BID_BUCKETS.length,
      "bucket names are unique");

// ---- rule 1: the app's own calculator rows ------------------------------
// EW_ITEM_RE left with the earthwork calculator (2026-09-25); the bid rule
// keeps its own list of the row names the calculators produce - the cut /
// fill engine's Site Cut / Site Fill / Export / Import Structural included.
const reRule = T.BID_RULES.find((r) => r.re);
check(!!reRule && reRule.bucket === "Earthwork",
      "the regex rule exists and files under Earthwork");
check(reRule && reRule.re.test("Site Cut") && reRule.re.test("Site Fill")
      && reRule.re.test("Export Cut") && reRule.re.test("Import Structural"),
      "the rule names the cut / fill engine's rows");
check(bucket("Site Cut", "") === "Earthwork", "Site Cut -> Earthwork");
check(bucket("Strip Topsoil", "") === "Earthwork", "Strip Topsoil -> Earthwork");
check(bucket("Import Structural", "") === "Earthwork", "Import Structural -> Earthwork");
// ORDER PIN: the calculator rule beats the paving keyword
check(bucket("Export Asphalt", "") === "Earthwork",
      "Export Asphalt stays an earthwork row (before ASPHALT->paving)");
check(bucket("Mass Excavation", "") === "Earthwork", "Mass Excavation -> Earthwork");
check(bucket("Structure Backfill", "") === "Earthwork", "Structure Backfill -> Earthwork");
check(bucket("Slab Prep", "") === "Site Concrete/Paving",
      "Slab Prep -> hardscape (#853 bills it by the SF)");

// ---- rule 2: erosion & sediment control, and its order pins ----
check(bucket("SILT FENCE", "") === "Erosion & Sediment Control",
      "SILT FENCE -> E&SC (order: before FENCE->landscape)");
check(bucket("TREE PROTECTION FENCE", "") === "Erosion & Sediment Control",
      "TREE PROTECTION FENCE -> E&SC (order: before TREE)");
check(bucket("INLET PROTECTION", "") === "Erosion & Sediment Control",
      "INLET PROTECTION -> E&SC (order: before INLET->storm)");
check(bucket("PROPOSED SEDIMENT CONTROL LOG (legend)", "") === "Erosion & Sediment Control",
      "SEDIMENT log -> E&SC");
check(bucket("SE-P-RIPRAP RD3", "") === "Erosion & Sediment Control",
      "RIPRAP -> E&SC");

// ---- rule 3: clearing / limits ----
check(bucket("BG-Limit of disturbance", "") === "Clearing/Limits",
      "Limit of disturbance -> Clearing/Limits (Barn C3.0's own layer)");
check(bucket("CLEAR & GRUB", "") === "Clearing/Limits", "CLEAR & GRUB -> Clearing/Limits");
check(bucket("", "X|C-LOD") === "Clearing/Limits", "LOD layer word -> Clearing/Limits");

// ---- rule 4: reference linework, and the label-over-layer pins ----
check(bucket("PROPERTY/TRACT LINE (legend)", "") === "Landscape/Other",
      "PROPERTY line -> Landscape/Other");
check(bucket("C-U2-ROAD-ROW", "") === "Landscape/Other",
      "ROAD-ROW -> Landscape/Other (ROW word before ROAD->paving)");
check(bucket("C-U2-ESMT-UTIL", "") === "Landscape/Other", "ESMT -> Landscape/Other");
check(bucket("C-U2-BNDY", "") === "Landscape/Other", "BNDY -> Landscape/Other");
check(bucket("C-U2-PROP-LOT", "") === "Landscape/Other", "PROP LOT -> Landscape/Other");
check(bucket("SE-P-CENTERLINE", "") === "Landscape/Other", "CENTERLINE -> Landscape/Other");
// LABEL OUTRANKS LAYER: hydrant label on a lot-line layer is a hydrant
check(bucket("EXISTING FIRE HYDRANT (legend)",
             "Kayden Springs U2 - CAD Base|C-U1-PROP-LOT") === "Water",
      "hydrant label on a PROP-LOT layer -> Water (label pass first)");
// ...and a garbled label falls through to the layer pass
check(bucket("$nWonLo (legend)",
             "Kayden Springs U2 - CAD Base|C-U2-ESMT-SSWR") === "Landscape/Other",
      "garbled label on an ESMT-SSWR layer -> easement, not sewer");

// ---- rule 5: earthwork keywords, with the word-bound pins ----
check(bucket("EXISTING CONTOUR (TRUE NORTH SURVEY) (legend)", "") === "Earthwork",
      "CONTOUR -> Earthwork");
check(bucket("7900 EXISTING CONTOUR INTERVAL (TRUE NORTH SURVEY) (legend)", "") === "Earthwork",
      "contour interval row -> Earthwork");
check(bucket("C-TOPO", "") === "Earthwork", "TOPO -> Earthwork");
check(bucket("SE-P-SWALE", "") === "Earthwork", "SWALE -> Earthwork");
check(bucket("FINISH GRADE", "") === "Earthwork", "=GRADE word-match works");
check(bucket("PATHS TO SUBGRADE", "") === "Site Concrete/Paving",
      "PATHS TO SUBGRADE -> paving (=GRADE must NOT match inside SUBGRADE)");
check(bucket("BERMUDA SOD", "") === "Landscape/Other",
      "BERMUDA SOD -> landscape (=BERM must NOT match inside BERMUDA)");

// ---- rule 6: storm drainage, and its order pins ----
check(bucket("PROPOSED STORM SEWER (legend)", "") === "Storm Drainage",
      "STORM SEWER -> storm (order: before SEWER->sanitary)");
check(bucket("SE-P-STORM-PIPE BR IN", "") === "Storm Drainage", "STORM pipe -> storm");
check(bucket("HEADWALL", "") === "Storm Drainage",
      "HEADWALL -> storm (order: before WALL->walls)");
check(bucket("CATCH BASIN", "") === "Storm Drainage", "CATCH BASIN -> storm");
// WITHDRAWN token, pinned out: a bare CB read Barn's whole
// BG-SWCR-CB-PLANTING xref namespace as catch basins.
check(bucket("", "BG-SWCR-CB-MATERIALS|BG-L-Whatever") !== "Storm Drainage",
      "an xref project code CB must not read as a catch basin");
check(bucket("SE-P-Infiltr-chamber", "") === "Storm Drainage", "INFILTR -> storm");
check(bucket("P-VENT DRAIN", "") === "Storm Drainage", "bare DRAIN -> storm");
check(bucket('PROPOSED 4" RIGID FOOTER DRAIN', "") === "Storm Drainage",
      "FOOTER DRAIN -> storm (#853's perimeter-drain family)");
check(bucket("FOTER DRAIN", "") === "Storm Drainage",
      "the drafter's own typo FOTER -> storm (section 15 item 14)");
// short-code word bounding: SD is a word, never a fragment
check(bucket("", "X|C-SD-PIPE") === "Storm Drainage", "SD as a word -> storm");
check(bucket("C-SDWK", "") !== "Storm Drainage",
      "SD must not match inside SDWK (sidewalk)");

// ---- rule 7: sanitary sewer ----
check(bucket("SANITARY SEWER MANHOLE (legend)", "") === "Sanitary Sewer",
      "sanitary manhole -> Sanitary Sewer");
check(bucket("C-U2-SSWR-PIPE", "") === "Sanitary Sewer",
      "SSWR -> sanitary (order: before PIPE->generic)");
check(bucket("8\" SS", "") === "Sanitary Sewer", "SS word -> sanitary");
check(bucket("CROSS SECTION", "") !== "Sanitary Sewer",
      "SS must not match inside CROSS");
check(bucket("FORCE MAIN", "") === "Sanitary Sewer", "FORCE MAIN -> sanitary");
check(bucket("SE-P-OWTS-PDchmbr-trench sys", "") === "Sanitary Sewer",
      "OWTS (septic) -> sanitary (order: before TRENCH fallback)");
check(bucket("MANHOLE", "") === "Sanitary Sewer", "bare MANHOLE -> sanitary");
check(bucket("STORM MANHOLE", "") === "Storm Drainage",
      "STORM MANHOLE -> storm (order pin)");

// ---- rule 8: water ----
check(bucket("SE-P-UT-WATER-10''", "") === "Water", "WATER -> Water");
check(bucket("C-U2-WATR-8IN-LINE", "") === "Water", "WATR -> Water");
check(bucket("PROPOSED GATE VALVE (legend)",
             "Kayden Springs U2 - CAD Base|C-U2-ROAD-CNTR") === "Water",
      "GATE VALVE label on a ROAD layer -> Water (label pass first)");
check(bucket("W", "") === "Water", "bare W label (lettered run) -> Water");
check(T.BID_LABEL_ONLY.indexOf("W") >= 0, "W is label-only");
check(bucket("something", "R.O.W.") !== "Water",
      "W in a layer path never reads as water (label-only token)");

// ---- rule 9: walls ----
check(bucket("CONCRETE RETAINING WALL", "") === "Walls",
      "CONCRETE RETAINING WALL -> Walls (order: before CONCRETE->paving)");
check(bucket("BG-L-Retaining Wall arch", "") === "Walls", "retaining wall -> Walls");
check(bucket("", "BR FND - BARN EAST|A-WALL") === "Walls",
      "A-WALL layer -> Walls (C4.3's building-wall layer)");

// ---- bedding-before-gravel pin ----
check(bucket("Bedding gravel (run A)", "") === "Earthwork",
      "Bedding gravel (unnamed run) -> Earthwork (before GRAVEL->paving)");
check(bucket("Bedding gravel (8\" SANITARY SEWER)", "") === "Sanitary Sewer",
      "Bedding gravel (named run) -> the run's own system");

// ---- rule 10: site concrete / paving ----
check(bucket("CONCRETE SIDEWALK", "") === "Site Concrete/Paving", "SIDEWALK -> paving");
check(bucket("C-U2-ROAD-DRWY", "") === "Site Concrete/Paving", "ROAD/DRWY -> paving");
check(bucket("C-U2-ROAD-GUT", "") === "Site Concrete/Paving", "=GUT word -> paving");
check(bucket("C-U2-ROAD-FOC", "") === "Site Concrete/Paving", "FOC -> paving");
check(bucket("C-U2-ROAD-SWLK", "") === "Site Concrete/Paving", "SWLK -> paving");
check(bucket("BG-L-Conc", "") === "Site Concrete/Paving", "CONC -> paving");
check(bucket("BG-L-Paver type 7", "") === "Site Concrete/Paving", "PAVER -> paving");
check(bucket("SE-P-GRAVEL HTCH GC2", "") === "Site Concrete/Paving",
      "GRAVEL -> paving (section 0's aggregate/base family)");
check(bucket("AGGREGATE BASE", "") === "Site Concrete/Paving",
      "AGGREGATE -> paving (and =GATE must NOT match inside AGGREGATE)");
check(bucket("BG-L-Stair type 3", "") === "Site Concrete/Paving", "STAIR -> paving");
check(bucket("HAUL ROAD", "") === "Earthwork",
      "HAUL ROAD -> Earthwork (order: HAUL before ROAD)");

// ---- rule 11: landscape / other ----
check(bucket("BG-L-PLNT-TREE-STEM", "") === "Landscape/Other", "PLNT -> landscape");
check(bucket("1Tree", "BG-SWCR-CB-PLANTING|1Tree") === "Landscape/Other",
      "1Tree row -> landscape via its PLANTING layer (=TREE cannot see 1TREE)");
check(bucket("STREET", "") !== "Landscape/Other",
      "=TREE must NOT match inside STREET");
check(bucket("BG-L-Metal edger", "") === "Landscape/Other", "EDGER -> landscape");
check(bucket("BG-L-Boulders", "") === "Landscape/Other", "BOULDER -> landscape");
check(bucket("BG-L-Pool", "") === "Landscape/Other", "POOL -> landscape");
check(bucket("CHAIN LINK FENCE", "") === "Landscape/Other", "FENCE -> landscape");
check(bucket("PIPE RAIL", "") === "Landscape/Other",
      "PIPE RAIL -> landscape (order: RAIL before PIPE->generic)");
check(bucket("BG-building-CSA", "") === "Landscape/Other", "BUILDING -> landscape");

// ---- rule 12: a pipe named only by material ----
check(bucket("8\" PVC (run)", "") === "Storm Drainage",
      "bare PVC -> storm (the one measured instance: C4.3 style 106)");
check(bucket("PROPOSED PVC SURFACE STORM DRAIN PIPE", "") === "Storm Drainage",
      "explicit storm PVC -> storm (via STORM, not the material)");
check(bucket("8\" PVC SANITARY", "") === "Sanitary Sewer",
      "PVC SANITARY -> sanitary (order: system before material)");

// ---- rule 13: trench fallback ----
check(bucket("Trench excavation (run A)", "") === "Earthwork",
      "unnamed trench dig -> Earthwork");
check(bucket("Trench excavation (8\" SANITARY SEWER)", "") === "Sanitary Sewer",
      "named trench dig -> the run's own system");
check(bucket("Backfill (run A)", "") === "Earthwork", "unnamed backfill -> Earthwork");

// ---- the honest catch-all ----
check(bucket("solid solid", "") === T.BID_UNASSIGNED,
      "a signature-named row -> UNASSIGNED (no keyword invents a family)");
check(bucket("dash1 d1.0 g7.6", "") === T.BID_UNASSIGNED,
      "a dash-signature row -> UNASSIGNED");
check(bucket("P-UT-ELEC-UG", "") === "Electric/Comm",
      "dry utilities -> their own bucket since 2026-09-16 (was UNASSIGNED on purpose before Beaumont's bid sheet asked for the line)");
check(bucket("BG-THERMAL WELLS", "IMEG-SW-UTILITIES|BG-THERMAL WELLS") === T.BID_UNASSIGNED,
      "thermal wells -> UNASSIGNED");
check(bucket("", "") === T.BID_UNASSIGNED, "empty row -> UNASSIGNED");

// ---- size tokens: read from the name, never inferred ----
check(T.bidSizeToken('8" PVC') === '8"', 'size 8" from quote mark');
check(T.bidSizeToken("SE-P-UT-WATER-10''") === '10"', "size 10'' from double apostrophe");
check(T.bidSizeToken("C-U2-WATR-8IN-LINE") === '8"', "size 8IN from IN suffix");
check(T.bidSizeToken("12-INCH RCP") === '12"', "size 12-INCH");
check(T.bidSizeToken("SE-P-STORM-PIPE BR IN") === null,
      "IN with no digits before it is not a size");
check(T.bidSizeToken("8 PVC") === null,
      "a bare number with no inch mark is NOT a size");

// ---- the grouping: sums are sums, overrides win, unknowns are ignored ----
const rows = [
  { label: "PROPOSED STORM SEWER (legend)", layer: "", unit: "ft", qty: 529 },
  { label: '18" RCP', layer: "", unit: "ft", qty: 300 },
  { label: '18" RCP', layer: "", unit: "EA", qty: 3 },
  { label: "C-U2-SSWR-PIPE", layer: "", unit: "ft", qty: 3982 },
  { label: "Site Cut", layer: "", unit: "CY", qty: 13677 },
  { label: "solid solid", layer: "", unit: "ft", qty: 16760 },
  { label: "BG-THERMAL WELLS", layer: "", unit: "ft", qty: 10084 },
];
const ov = (label) =>
  label === "BG-THERMAL WELLS" ? "Water"
  : label === "solid solid" ? "No Such Bucket" : null;
const g = T.bidGroupRows(rows, ov);
check(g.length === T.BID_BUCKETS.length, "every bucket returned, display order");
check(g.map((x) => x.bucket).join("|") === T.BID_BUCKETS.join("|"),
      "bucket order is BID_BUCKETS order");
const by = {};
for (const B of g) by[B.bucket] = B;
check(by["Storm Drainage"].byUnit.ft === 829, "storm ft = 529 + 300 (a sum)");
check(by["Storm Drainage"].byUnit.EA === 3, "storm EA kept separate from ft");
check(by["Storm Drainage"].n === 3, "storm member count = 3");
check(by["Sanitary Sewer"].byUnit.ft === 3982, "sanitary ft = its one row");
// Cut, fill and the net each keep their own chip (audit 2026-09-26, D17):
// byUnit never holds a cut / fill / net row, B.cf[kind][unit] does.
check(by["Earthwork"].cf.cut.CY === 13677 && by["Earthwork"].byUnit.CY === undefined,
      "Site Cut CY = its one row, on the cut chip and never in the per-unit sum");
const g2 = T.bidGroupRows([
  { label: "Site Cut", layer: "", unit: "CY", qty: 1000 },
  { label: "Site Fill", layer: "", unit: "CY", qty: 400 },
  { label: "Export Cut", layer: "", unit: "CY", qty: 600, kind: "net" },
  { label: "Strip Topsoil", layer: "", unit: "CY", qty: 200 },
], () => null).find((B) => B.bucket === "Earthwork");
check(g2.cf.cut.CY === 1000 && g2.cf.fill.CY === 400 && g2.cf.export.CY === 600 && g2.byUnit.CY === 200 && g2.n === 4,
      "a cut-heavy sheet's Earthwork bucket: 1,000 cut, 400 fill, 600 export, 200 other CY - never one 2,200 CY figure");
const g3 = T.bidGroupRows([
  { label: "Site Cut", layer: "", unit: "CY", qty: 400 },
  { label: "Site Fill", layer: "", unit: "CY", qty: 1000 },
  { label: "Import Structural", layer: "", unit: "CY", qty: 600, kind: "net" },
], () => null).find((B) => B.bucket === "Earthwork");
check(g3.cf.import.CY === 600 && g3.cf.fill.CY === 1000 && g3.cf.cut.CY === 400 && Object.keys(g3.byUnit).length === 0,
      "a fill-heavy sheet keeps the import apart the same way");
check(by["Water"].byUnit.ft === 10084 && by["Water"].n === 1,
      "a VALID override moves the row (thermal wells -> Water)");
check(by["Water"].members[0].via === "override", "override rows say so");
check(by["Water"].members[0].ruleBucket === T.BID_UNASSIGNED,
      "the rule's own verdict is preserved beside the override");
check(by[T.BID_UNASSIGNED].byUnit.ft === 16760 && by[T.BID_UNASSIGNED].n === 1,
      "an UNKNOWN override name is ignored - the row stays where the rule put it");
check(by["Storm Drainage"].members[0].qty === 529
      && by["Storm Drainage"].members[1].qty === 300,
      "members sorted by qty desc");
const bs = T.bidBySize(by["Storm Drainage"].members, "ft");
check(bs['18"'] === 300 && bs["no size in name"] === 529,
      "by-size splits on the name's own token and says when there is none");

// ---- determinism: same input, same output, twice ----
check(JSON.stringify(T.bidGroupRows(rows, ov))
        === JSON.stringify(T.bidGroupRows(rows, ov)),
      "grouping is deterministic");

if (failures) { console.error(failures + " failure(s)"); process.exit(1); }
console.log("all bid-view rule tests passed");
