// The trench calculator's WIDTH-BY-PIPE-SIZE conventions, the bedding
// ENVELOPE, and the depth SERIES integrator (fix/trenching, 2026-09-26;
// PLAN.md 7.1 / 7.2, defects D18 / D19).
//
// Every number below is one the audit derived BY HAND from a printed detail
// or from the client's own worked example (accuracy-audit/trenching/measured/
// tr_math_barn.json, trench_math_cases.json), so the arithmetic is pinned to
// the sheet, not to itself:
//
//   * a 2.125-in water service, 173.3 LF, the set's printed detail (width
//     Bc + 18" = 9 in each side, bedding 6" below + 12" above, 7 ft minimum
//     cover): dig 82.6 CY (vertical walls, the lower bound), bedding 17.9 CY.
//     The old square-box envelope read 64.6 CY here (+261 %);
//   * the client's rule, 8-in pipe, 1,330 LF at 7 ft, OD + 6 in each side:
//     dig 574.7 CY (his own hand number);
//   * a 6.275-in drain, printed detail OD + 12 in each side, 4" below + 6"
//     above, 100 LF at 4.5 ft: bedding 11.9 CY per 100 LF;
//   * bands over a series: the LF per band sums to the run, a piece sitting
//     ON an edge is the shallower band, the profile's 27 stations from the
//     audit's Barn C4.2 read give 122.0 CY under the old depth-class table.
//
// Dependency-free like tests/test_trench_math.js:  node tests/test_trench_series.js

"use strict";
const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const a = src.indexOf("// TRENCH-MATH-BEGIN");
const b = src.indexOf("// TRENCH-MATH-END");
if (a < 0 || b < 0 || b <= a) { console.error("FAIL: TRENCH-MATH markers not found"); process.exit(1); }
const T = new Function(src.slice(a, b) + `
  return { trNormRules, trClrAt, trHoleArea, trEnvArea, trRunVolumes, trTotals, trCY, trDepthBands,
           trConvWidth, trHoleAreaW, trEnvAreaW, trSeriesVolumes, trSeriesBands, trMapSeries };`)();

let failures = 0;
function check(cond, msg) { if (!cond) { failures++; console.error("FAIL:", msg); } else console.log("ok:", msg); }
function near(x, y, tol, msg) { check(Math.abs(x - y) <= tol, `${msg} (got ${x}, want ${y} +-${tol})`); }

const clr = (inches) => ({ mode: "clr", clr_ft: inches / 12 });
const cy = (v) => T.trCY(v);
function run(conv, odIn, lenFt, d0, d1, bed, lb) {
  const odFt = odIn / 12;
  const v = T.trSeriesVolumes(conv, odFt, [[0, d0], [lenFt, d1]], lb || null, bed);
  const pipe = Math.PI * (odFt / 2) ** 2 * lenFt;
  const t = T.trTotals(v.dig, v.env, pipe);
  return { dig: cy(t.dig), bed: cy(t.bed), backfill: cy(t.backfill), lf: v.lf };
}

// ---- the printed water detail (Bc + 18", 6" below, 12" above, 7 ft cover) ----
{
  const od = 2.125, depth = 7 + 6 / 12 + od / 12;   // cover + pipe + bedding under it = the trench bottom
  const r = run(clr(9), od, 173.3, depth, depth, { below_ft: 0.5, above_ft: 1.0 });
  near(r.dig, 82.6, 0.05, "water service 173.3 LF, printed detail: dig 82.6 CY (vertical walls, lower bound)");
  near(r.bed, 17.9, 0.05, "...bedding 17.9 CY as an envelope (6 in under, pipe, 12 in over, at the trench width)");
  near(r.lf, 173.3, 1e-9, "...over the whole run");
  // the old reading, for the record: the default depth-class table (18 in
  // each side past 4 ft) as a square box = 64.6 CY, the +261 % the audit measured
  const rs = T.trNormRules([{ to_ft: 2, clr_in: 6 }, { to_ft: 4, clr_in: 12 }, { to_ft: 99, clr_in: 18 }]);
  const sq = T.trEnvArea(rs, od / 12, depth) * 173.3 - Math.PI * (od / 24) ** 2 * 173.3;
  near(cy(sq), 64.6, 0.1, "the square-box envelope under the default table read 64.6 CY here (+261 %) - replaced");
}

// ---- the client's rule: 8 in, 1,330 LF at 7 ft, OD + 6 in each side ----------
{
  const r = run(clr(6), 8, 1330, 7, 7, { below_ft: 0.5, above_ft: 1.0 });
  near(r.dig, 574.7, 0.05, "8 in x 1,330 LF x 7 ft, OD + 6 in each side: dig 574.7 CY (the client's hand number)");
  near(T.trConvWidth(clr(6), 8 / 12, 7) * 12, 20, 1e-9, "the trench is 20 in wide for an 8 in pipe, at any depth");
  near(T.trConvWidth(clr(6), 6 / 12, 3) * 12, 18, 1e-9, "6 in pipe -> 18 in trench, 12 in pipe -> 24 in (the rule as he stated it)");
  near(T.trConvWidth(clr(6), 12 / 12, 3) * 12, 24, 1e-9, "12 in pipe -> 24 in trench");
}

// ---- the printed drain detail: OD + 12 in each side, 4" below, 6" above ----------
{
  const r = run(clr(12), 6.275, 100, 4.5, 4.5, { below_ft: 4 / 12, above_ft: 6 / 12 });
  near(r.bed, 11.9, 0.05, "drain detail, 100 LF at 4.5 ft: bedding 11.9 CY per 100 LF (was 22.8 as a square)");
  near(r.dig, 42.0, 0.05, "...dig 42.0 CY (width OD + 24 in)");
}

// ---- the envelope can never out-measure the hole --------------------------------
{
  const conv = clr(6), od = 0.5;
  near(T.trEnvAreaW(conv, od, 0.8, { below_ft: 0.5, above_ft: 1.0 }), T.trConvWidth(conv, od, 0.8) * 0.8, 1e-9,
       "a 0.8 ft deep hole: the envelope is clamped to the hole (bed + pipe + cover would be 2.0 ft)");
  const r = run(conv, 6, 100, 1.0, 1.0, { below_ft: 0.5, above_ft: 1.0 });
  check(r.bed + r.backfill <= r.dig + 1e-9, "bed + backfill <= dig at 1 ft deep");
}

// ---- the depth-class table through the series integrator = trRunVolumes ------------
{
  const rules = [{ to_ft: 2, clr_in: 6 }, { to_ft: 4, clr_in: 12 }, { to_ft: 99, clr_in: 18 }];
  const rs = T.trNormRules(rules);
  const conv = { mode: "rules", rs };
  for (const [d0, d1] of [[2, 8], [1, 1], [3.5, 4.5], [8, 2]]) {
    const v0 = T.trRunVolumes(rs, 0.5, 100, d0, d1, 60, null);
    const v1 = T.trSeriesVolumes(conv, 0.5, [[0, d0], [100, d1]], null, { below_ft: 0, above_ft: 0 });
    near(v1.dig, v0.dig, 1e-6, `rules mode, typed ${d0} -> ${d1} ft: the series integral equals trRunVolumes' dig`);
  }
  // with a 1:1 layback the wedge is quadratic: sub-slicing keeps it within noise of N = 60
  const lb = { hv: 1, from: 4 };
  const v0 = T.trRunVolumes(rs, 0.5, 100, 2, 8, 60, lb);
  const v1 = T.trSeriesVolumes(conv, 0.5, [[0, 2], [100, 8]], lb, { below_ft: 0, above_ft: 0 });
  near(v1.dig, v0.dig, 0.02 * v0.dig, "...and with a 1:1 layback within 2 %");
}

// ---- the audit's Barn C4.2 profile read (27 stations, sta 10..270 every 10 ft) -------
{
  const depths = [2.51, 2.65, 2.51, 2.39, 2.59, 3.07, 3.62, 4.19, 4.76, 5.22, 5.33, 5.27, 5.12, 4.99,
                  5.26, 6.04, 5.6, 4.89, 4.26, 3.74, 3.22, 3.07, 3.19, 2.79, 2.42, 2.04, 1.67];
  const series = depths.map((d, i) => [10 + 10 * i, d]);
  const rs = T.trNormRules([{ to_ft: 2, clr_in: 6 }, { to_ft: 4, clr_in: 12 }, { to_ft: 99, clr_in: 18 }]);
  const v = T.trSeriesVolumes({ mode: "rules", rs }, 8.4 / 12, series, null, { below_ft: 0, above_ft: 0 });
  near(cy(v.dig), 122.0, 0.1, "the audit's drawn-profile depths under the old table: dig 122.0 CY (typed ends gave 47.4)");
  near(v.lf, 260, 1e-9, "over 260 ft of stations");
  const bt = T.trSeriesBands(series, 6, 2, 64);
  near(bt.rows.reduce((s, r) => s + r.lf, 0), 260, 1e-9, "band LF sums to the run");
  near(bt.rows[0].lf, 258.6, 0.1, "0-6 ft: 258.6 LF");
  near(bt.rows[1].lf, 1.4, 0.1, "6-8 ft: 1.4 LF (the 6.04 ft dip)");
}

// ---- bands over a series: edges, sums, mapping --------------------------------------
{
  const bt = T.trSeriesBands([[0, 6], [100, 6]], 6, 2, 64);
  near(bt.rows[0].lf, 100, 1e-9, "a run sitting ON the 6 ft edge is all in 0-6");
  const bt2 = T.trSeriesBands([[0, 6.01], [100, 6.01]], 6, 2, 64);
  near(bt2.rows[1].lf, 100, 1e-9, "...and 6.01 ft is all in 6-8");
  const bt3 = T.trSeriesBands([[0, 6], [100, 14]], 6, 2, 64);
  check(bt3.rows.length === 5 && bt3.rows.slice(1).every(r => Math.abs(r.lf - 25) < 1e-9), "6 -> 14 ft linear: 25 LF in each of 6-8, 8-10, 10-12, 12-14");
  const m = T.trMapSeries([[98.76, 8.57], [597.76, 7.23]], 98.76, 597.76, 514.6);
  near(m.stretch, 514.6 / 499, 1e-9, "a 499-ft profile mapped onto a 514.6-ft plan run stretches x1.0313 (the audit's method)");
  near(m.series[1][0], 514.6, 1e-9, "...and its last station lands on the run's end");
  check(T.trSeriesBands([[0, 3]], 6, 2, 64) === null, "one sample is not a series");
}

if (failures) { console.error(failures + " failure(s)"); process.exit(1); }
console.log("all trench-series checks passed");
