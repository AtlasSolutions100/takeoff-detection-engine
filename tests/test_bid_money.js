// WRONG MONEY ON THE BID SHEET, PINNED (audit 2026-09-26, PLAN.md 1.2 / 2.1 /
// 2.2 / 2.3 / 7.4: defects D02, D03, D04, D05, D17).
//
//   D04  trench rows were keyed by the family label's first 40 characters:
//        adding a second family with the same name (six "PROPOSED STORM
//        SEWER (legend)" styles on one civil sheet) deleted the first
//        family's rows - 885.2 LF gone, -67.5 % against the estimate. Rows
//        are keyed by the style id (`run`) now, and calcSave keeps the id.
//   D03  the family row AND its "(run)" row both reached the bid line
//        (sewer 563.2 LF against 270): bsGroupRows / bidsheet.build replace
//        the family with its runs (tests/test_bidsheet_ui.js pins the screen
//        against the CSV line for line; this file pins the row keying).
//   D05  the legend's own sample bars were ticked takeoff lines (+33.6 ft on
//        every utility line of that sheet, 41 ft in a contour row):
//        legendSwatchIds finds them from the result and they start unticked.
//        Pinned on the legend regions of three real detect results
//        (tests/fixtures/legend_swatch_cases.json), including the fence plan
//        whose plan callouts were read as legend rows - its plan linework
//        must keep its tick.
//   D02 / D17  the cut/fill net row is tagged `kind: "net"`; the chips and
//        the matcher are pinned in test_bidview_rules.js / test_bidsheet*.
//
// webui/app.js is not a module and touches the DOM at load, so this evals
// the pure TRENCH-ROWS and LEGEND-SWATCH blocks standalone, and slices
// calcSave / calcRestore / cfRows by name with fakes for what they touch.
//
// run: node tests/test_bid_money.js
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const py = fs.readFileSync(path.join(__dirname, "..", "server.py"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(name) {
  const start = js.indexOf("function " + name + "(");
  if (start < 0) return "";
  let depth = 0;
  for (let j = js.indexOf("{", start); j < js.length; j++) {
    if (js[j] === "{") depth++;
    else if (js[j] === "}" && --depth === 0) return js.slice(start, j + 1);
  }
  return "";
}
function block(tag) {
  const a = js.indexOf("// " + tag + "-BEGIN"), b = js.indexOf("// " + tag + "-END");
  check(a >= 0 && b > a, tag + " markers present");
  return js.slice(a, b);
}

// ---- D04: trench rows keyed by their run, tagged with the family they replace -------------
// The merged model (bid-money + trenching): one LF row per depth band carrying the pipe size
// (kind trench_run - together they ARE the run's footage), dig / bedding / backfill, one
// structures row per system; every row carries run (the style id), runOf (the family's label)
// and utility; a run's rows are cleared by its own name, never another run's.
const state = { extraItems: [], labels: new Map(), tr: { added: new Set(["A", "B"]), results: {} } };
const nm = "PROPOSED STORM SEWER (legend)";
state.labels.set(169, nm); state.labels.set(168, nm);
const store = { getItem: () => null, setSession: () => {}, setItem: () => {}, removeItem: () => {} };
const TR = new Function("state", "trCY", "store", "tradeKey", "TR_SYSTEM_WORD", "TR_SYSTEM_UTIL", "TR_SYSTEM_TRADE",
  block("TRENCH-ROWS") + "\nreturn { trClearItems, trApplyItems, trRowKey, trIsRowOf };")(
  state, (v) => Math.round(v / 27 * 10) / 10, store, (l) => "trade:" + l,
  { STM: "storm", SS: "sewer" }, { STM: "storm", SS: "sewer" }, { STM: "Wet Utilities", SS: "Wet Utilities" });
const resA = { key: "A", style_id: 169, name: "STM-1", system: "STM", size: '6"', runFt: 885.2,
  bands: [{ lo: 0, hi: 6, lf: 800.2 }, { lo: 6, hi: 8, lf: 85.0 }], dig: 27 * 100, bed: 27 * 50, backfill: 27 * 40, structures: [1, 2] };
const resB = { key: "B", style_id: 168, name: "STM-2", system: "STM", size: '6"', runFt: 534.5,
  bands: [{ lo: 0, hi: 6, lf: 534.5 }], dig: 27 * 60, bed: 27 * 30, backfill: 27 * 25, structures: [2, 3] };
state.tr.results = { A: resA, B: resB };
TR.trApplyItems(resA);
TR.trApplyItems(resB);
const lf = state.extraItems.filter((x) => x.unit === "LF");
check(state.extraItems.length === 10 && lf.length === 3 && lf.map((x) => x.qty).sort().join(",") === "534.5,800.2,85",
  "two runs of two same-name families: both row sets survive (3 band rows, 6 CY rows, 1 structures row)");
check(lf.every((x) => x.kind === "trench_run" && x.runOf === nm && x.utility === "storm") && [...new Set(lf.map((x) => x.run))].sort().join(",") === "168,169",
  "every band row carries its style id, the family label and the utility (the bid sheet's replacement keys)");
check(state.extraItems.filter((x) => x.run === 169).map((x) => x.kind).join(",") === "trench_run,trench_run,trench_dig,trench_bed,trench_backfill",
  "the rows of one run share its style id and say which is which");
const st = state.extraItems.find((x) => x.kind === "trench_struct");
check(st && st.unit === "EA" && st.qty === 3 && st.label === "Structures (storm)", "one structures row per system, a shared manhole counted once (3, not 4)");
TR.trApplyItems({ ...resA, runFt: 900.0, bands: [{ lo: 0, hi: 6, lf: 900.0 }], dig: 27 * 110 });
check(state.extraItems.length === 9 && !state.extraItems.some((x) => x.qty === 800.2 || x.qty === 85)
      && state.extraItems.find((x) => x.run === 169 && x.unit === "LF").qty === 900
      && state.extraItems.find((x) => x.run === 168 && x.unit === "LF").qty === 534.5,
  "recalculating one run with other bands replaces only its own rows (no stale band left); the same-name family's run keeps its 534.5 LF");
state.tr.added.delete("B");
TR.trClearItems(resB);
check(state.extraItems.filter((x) => x.kind !== "trench_struct").every((x) => x.run === 169) && state.extraItems.find((x) => x.kind === "trench_struct").qty === 2,
  "removing one run leaves the other's rows alone and the structures row recounts (2)");
// a row saved before the model changed: "<run name> (run)" is cleared by its name once, an unrelated legacy row stays
state.extraItems = [{ label: "STM-1 (run)", qty: 500, unit: "LF" }, { label: "Water (run)", qty: 10, unit: "LF" }];
TR.trApplyItems(resA);
check(!state.extraItems.some((x) => x.qty === 500) && state.extraItems.some((x) => x.label === "Water (run)")
      && state.extraItems.filter((x) => x.unit === "LF" && x.run === 169).length === 2,
  "a legacy '(run)' row of the same run is replaced by its name; an unrelated legacy row stays");
check(TR.trIsRowOf("6\" storm 0-6 ft deep (STM-1)", "STM-1") && TR.trIsRowOf("STM-1 (run)", "STM-1") && !TR.trIsRowOf("6\" storm 0-6 ft deep (STM-10)", "STM-1"),
  "a row belongs to a run by the name that closes its label, exactly");

// ---- calcSave / calcRestore keep the provenance --------------------------------------
{
  const mem = new Map();
  const store = { getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: (k) => mem.delete(k) };
  const st = { result: {}, extraItems: [
    { label: "SE-P-UT-SS (run)", qty: 264.8, unit: "LF", run: 172, runOf: "SE-P-UT-SS", kind: "trench_run", utility: "sewer" },
    { label: "Export Cut", qty: 600, unit: "CY", kind: "net" },
    { label: "Old row", qty: 3, unit: "EA" },
    { label: "derived", qty: 1, unit: "SF", _area: true }],
    areaRows: [], areaInc: new Map(), areaLabels: new Map() };
  const C = new Function("state", "store", "calcKey", "calcKey2", "calcKey3", "areaSelKey",
    js.slice(js.indexOf("const EW_OLD_CY_RE = "), js.indexOf("\n", js.indexOf("const EW_OLD_CY_RE = "))) + "\n" + fnSrc("ewOldRow") + "\n"
    + fnSrc("calcEarlierHadOldEw") + "\n"
    + fnSrc("calcSave") + "\n" + fnSrc("calcRestore") + "\nreturn { calcSave, calcRestore };")(st, store, () => "calc", () => "calc2", () => "calc3", () => "areasel");
  C.calcSave();
  // this build stores {core: 3, rows} under its OWN key (G6: an earlier version's keys are never written - v1.8.5's
  // "calc", 2.0's "calc2"; critic empty-2: nor is any row restored from them)
  const stored = JSON.parse(mem.get("calc3"));
  const saved = stored.rows;
  check(stored.core === 3 && !mem.has("calc") && !mem.has("calc2") && saved.length === 3 && saved[0].run === 172 && saved[0].runOf === "SE-P-UT-SS" && saved[0].kind === "trench_run" && saved[0].utility === "sewer"
        && saved[1].kind === "net" && !("run" in saved[1]) && Object.keys(saved[2]).join(",") === "label,qty,unit",
    "calcSave keeps run / runOf / kind / utility only where a row has them; a derived row is not stored");
  st.extraItems = [];
  C.calcRestore();
  check(st.extraItems.length === 3 && st.extraItems[0].run === 172 && st.extraItems[0].utility === "sewer" && st.extraItems[1].kind === "net" && st.extraItems[2].unit === "EA",
    "calcRestore brings the provenance back, so a restored run row is still keyed by its style");
}

// ---- D02 / D17: the cut/fill hand-off tags its net row ------------------------------
{
  const cf = new Function(fnSrc("cfRows") + "\nreturn cfRows;")();
  const a = cf({ cut_cy: 1000, fill_cy: 400 }), b = cf({ cut_cy: 400, fill_cy: 1000 }), c = cf({ cut_cy: 1234, fill_cy: 0 });
  check(a.map((r) => r.label + ":" + r.qty).join("|") === "Site Cut:1000|Site Fill:400|Export Cut:600" && a[2].kind === "net" && !a[0].kind && !a[1].kind,
    "cut-heavy: Site Cut 1000, Site Fill 400, Export Cut 600 tagged net");
  check(b[2].label === "Import Structural" && b[2].qty === 600 && b[2].kind === "net", "fill-heavy: Import Structural 600 tagged net");
  check(c.length === 2 && c[1].label === "Export Cut" && c[1].qty === 1234 && c[1].kind === "net", "cut-only: the export equals the cut and is tagged net");
  // run, not pattern-matched: the row that reaches the takeoff carries the tag (and its basis)
  {
    const st = { extraItems: [] };
    const apply = new Function("state", "store", "tradeKey", "renderItemized", "calcSave", "calcStateDirty", "cfCompact",
      "const CF_ROW_RE = /^(Site Cut|Site Fill|Export Cut|Import Structural)$/;\n"
      + fnSrc("cfRows") + "\n" + fnSrc("cfIsRow") + "\n" + fnSrc("cfLeftNote") + "\n" + fnSrc("cfClearItems") + "\n"
      + fnSrc("cfApplyItems") + "\nreturn cfApplyItems;")(
      st, { getItem: () => null, setSession: () => {} }, (l) => "trade:" + l, () => {}, () => {}, () => {}, () => null);
    apply({ cut_cy: 1000, fill_cy: 400 });
    const net = st.extraItems.find((x) => x.label === "Export Cut");
    check(st.extraItems.length === 3 && net && net.kind === "net" && net.qty === 600 && net.unit === "CY"
          && !st.extraItems.some((x) => x.label === "Site Cut" && x.kind),
      "cfApplyItems carries the tag onto the takeoff row");
  }
}

// ---- D05: the legend's sample bars start unticked ------------------------------------
const LS = new Function(block("LEGEND-SWATCH") + "\nreturn { legendSwatchIds, SWATCH_BOX_MAX_CHAINS };")();
const LC = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "legend_swatch_cases.json"), "utf8")).cases;
for (const c of LC) {
  const ids = new Set(LS.legendSwatchIds(c.result));
  const missing = c.expect_unticked.filter((id) => !ids.has(id));
  const wrong = c.expect_kept.filter((id) => ids.has(id));
  const extra = [...ids].filter((id) => c.expect_unticked.indexOf(id) < 0);
  check(!missing.length && !wrong.length && !extra.length,
    `legend samples: ${c.name} - unticked ${ids.size} of ${c.result.chains.length} chains` + (missing.length ? " MISSING " + missing : "") + (wrong.length ? " WRONGLY UNTICKED " + wrong : "") + (extra.length ? " EXTRA " + extra : ""));
}
check(LS.legendSwatchIds({ legend_samples: [], chains: LC[0].result.chains, styles: LC[0].result.styles }).length === 0
      && LS.legendSwatchIds(null).length === 0, "no boxes, no result: nothing unticked");
check(JSON.stringify(LS.legendSwatchIds(LC[0].result)) === JSON.stringify(LS.legendSwatchIds(LC[0].result)), "deterministic: same result, same ids, twice");
// the substation contour row: the sample bar (41.05 ft) leaves the family
{
  const r = LC[1].result;
  const bar = r.chains.find((c) => c.chain_id === 843);
  check(bar && bar.style_id === 41 && Math.abs(bar.length_pt / 72 * 30 - 41.05) < 0.05, "chain 843 is the contour row's 98.5 pt sample bar (41.05 ft at 1 in = 30 ft)");
}
// wiring: unticked at load, kept out of the trench dropdown, said on the sheet facts
check(/memApply\(\);[^]*?swatchExclude\(\);[^]*?calcRestore\(\);/.test(fnSrc("loadResult")), "loadResult unticks the sample bars right after the saved decisions are applied");
check(/for \(const id of state\.result\._swatchIds\) state\.excluded\.add\(id\);/.test(fnSrc("swatchExclude")), "swatchExclude adds the ids to state.excluded (the set the takeoff rows and the trench run already respect)");
check(/if \(!state\.excluded\.has\(c\.chain_id\)\) live\.set\(c\.style_id/.test(fnSrc("renderTr")) && /if \(!n\) continue;/.test(fnSrc("renderTr")),
  "the trench dropdown counts live lines only and skips a style with none (a legend sample on its own CAD layer)");
check(/legend sample line/.test(fnSrc("swatchFactsText")) && /swatchCount\(\)/.test(fnSrc("swatchFactsText"))
      && /seg\.textContent = sheetFactsText\(\) \+ swatchFactsText\(\);/.test(fnSrc("renderSheetFacts")),
  "the sheet facts segment says how many legend sample lines were left out (beside sheetFactsText, whose sentence other tests pin)");

// ---- the export carries what the bid sheet needs ------------------------------------
check(/\.\.\.state\.extraItems\.map\(it => \(\{\n\s*\.\.\.it,/.test(js), "the approve payload spreads every extra row's fields (run / runOf / kind / utility travel with it)");
check(/_prov\["run_sid"\] = int\(_rs\)/.test(py) && /_prov\["run_of"\]/.test(py) && /_prov\["utility"\]/.test(py) && /_prov\["kind"\]/.test(py) && /\*\*_prov,/.test(py),
  "approve copies run_sid / run_of / kind / utility onto the exported calculator row");
check(/"by_style": \{k: round\(v \/ 72\.0 \* ft_per_in, 1\)/.test(py) && /t\["by_sid"\]\[_sk\] = t\["by_sid"\]\.get\(_sk, 0\.0\) \+ _pt/.test(py),
  "approve writes each family's footage per style id (by_style) for the replacement");

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all bid-money checks passed");
