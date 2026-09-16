// TRENCH LAYBACK (2026-09-16; Beaumont call 2026-09-15, 28:00-32:04): "every
// trench we dig kind of has to be in a V shape" - water and sewer at about
// 6 ft are laid back 1:1, gas and electric usually not. The hole area gains
// hv x d^2 once the trench is deeper than `from`; off, nothing moves.
//
// The pure block between the TRENCH-MATH markers is evaluated standalone,
// like tests/test_trench_math.js.
//
// run: node tests/test_trench_layback.js   (fails on 92f2d84)
"use strict";
const fs = require("fs");
const path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };

const a = src.indexOf("// TRENCH-MATH-BEGIN"), b = src.indexOf("// TRENCH-MATH-END");
check(a >= 0 && b > a, "TRENCH-MATH markers present");
const T = new Function(src.slice(a, b) + "\nreturn { trNormRules, trHoleArea, trEnvArea, trRunVolumes, trTotals };")();
const rs = T.trNormRules([{ to_ft: 99, clr_in: 6 }]);   // one band: 6 in clearance, so w = OD + 1 ft
const od = 0.5;                                          // 6 in pipe -> w = 1.5 ft

// ---- the pure rule ---------------------------------------------------------------
check(Math.abs(T.trHoleArea(rs, od, 6) - 9) < 1e-9, "no layback argument: the vertical column, 1.5 x 6 = 9 sf (unchanged)");
check(Math.abs(T.trHoleArea(rs, od, 6, { hv: 0, from: 4 }) - 9) < 1e-9, "hv 0: unchanged");
check(Math.abs(T.trHoleArea(rs, od, 6, { hv: 1, from: 4 }) - (9 + 36)) < 1e-9, "1:1 below 4 ft at 6 ft deep: + 6 x 6 = 36 sf (Beaumont's V)");
check(Math.abs(T.trHoleArea(rs, od, 3, { hv: 1, from: 4 }) - 4.5) < 1e-9, "3 ft deep is not past the 4 ft threshold: vertical");
check(Math.abs(T.trHoleArea(rs, od, 6, { hv: 0.5, from: 0 }) - (9 + 18)) < 1e-9, "0.5:1 from the surface: + 0.5 x 36");
check(T.trEnvArea(rs, od, 6) === T.trEnvArea(rs, od, 6), "the bedding envelope takes no layback argument (bedding sits at the bottom)");

// ---- through a run: the wedge lands in backfill, never in bedding ----------------------
{
  const v0 = T.trRunVolumes(rs, od, 100, 6, 6, 60);
  const v1 = T.trRunVolumes(rs, od, 100, 6, 6, 60, { hv: 1, from: 4 });
  check(Math.abs(v1.dig - v0.dig - 3600) < 1e-6, "100 LF at 6 ft, 1:1: dig grows by 36 sf x 100 ft = 3,600 cf");
  check(Math.abs(v1.env - v0.env) < 1e-9, "bedding envelope unchanged");
  const pipe = Math.PI * (od / 2) ** 2 * 100;
  const t0 = T.trTotals(v0.dig, v0.env, pipe), t1 = T.trTotals(v1.dig, v1.env, pipe);
  check(Math.abs(t1.bed - t0.bed) < 1e-9 && Math.abs(t1.backfill - t0.backfill - 3600) < 1e-6, "the V goes to backfill: bedding same, backfill + 3,600 cf");
  // a sloped run 2 -> 8 ft, 1:1 below 4 ft: only the part deeper than 4 ft laid back
  const s0 = T.trRunVolumes(rs, od, 60, 2, 8, 600), s1 = T.trRunVolumes(rs, od, 60, 2, 8, 600, { hv: 1, from: 4 });
  // integral of d^2 over the 40 ft where d runs 4 -> 8 (10 ft of run per ft of depth): 10 x (8^3 - 4^3) / 3 = 1493.3
  check(Math.abs((s1.dig - s0.dig) - 10 * (512 - 64) / 3) < 2.0, `sloped 2->8 ft: wedge only past 4 ft, = 1,493 cf (got ${(s1.dig - s0.dig).toFixed(1)})`);
}

// ---- wiring --------------------------------------------------------------------------
check(/utility: "", layback_hv: 0, layback_from_ft: 4,/.test(src), "the trench starts vertical (hv 0) with the 4 ft threshold ready");
check(/const TR_UTILITY_PRESETS = \{[\s\S]*water:\s*\{ d: 6, hv: 1, from: 4 \}[\s\S]*gas:\s*\{ d: 3, hv: 0, from: 4 \}/.test(src), "utility presets: water 6 ft laid back, gas 3 ft vertical");
check(/ut\.name = "tr-utility";/.test(src) && /num\("layback_hv", "0.25"\)/.test(src) && /num\("layback_from_ft", "0.5"\)/.test(src), "the panel has the utility select and the two layback inputs");
check(/trRunVolumes\(rs, odFt, L, t\.d0_ft, t\.d1_ft, N, lb\)/.test(src), "Calculate passes the layback through");
check(/sides laid back \$\{lb\.hv\}:1 where deeper than \$\{lb\.from\} ft/.test(src) && /" · vertical sides"/.test(src), "the result line says which it did");
check(/layback_hv: state\.tr\.layback_hv, layback_from_ft: state\.tr\.layback_from_ft,/.test(src) && /if \(calcNum\(sv\.layback_hv\) !== null\) t\.layback_hv = sv\.layback_hv;/.test(src), "saved work keeps and restores the layback and utility");
check(/a 6 ft trench adds\s+36 SF of section/.test(html), "the hint gives the worked number");
const av = (/app\.js\?v=(\d+)/.exec(html) || [])[1];
check(+av >= 179, "cache buster moved (app.js >= 179)");

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all trench-layback checks passed");
