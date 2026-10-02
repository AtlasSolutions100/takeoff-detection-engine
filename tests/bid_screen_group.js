// Helper for tests/test_bid_screen_csv_parity.py (not a test on its own: the suite runs tests/test_*.js).
// Runs the SCREEN's bid-sheet grouping (webui/app.js bsGroupRows, the BIDVIEW-RULES block, with the
// sub-item export label from the SUBITEM-PURE block) over the rows in argv[2] and writes, to argv[3]:
//   rows:    one record per filed row {label, unit, n (line number or null), ok, counted}
//   lines:   {n: {sum, other}} per line in the line's unit, as the screen's chips add them
//   export:  the approve export of the same rows (parent items, then each sub-item as subExportRows
//            names it), which the Python side files with takeoff_engine.bidsheet.build
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const block = (tag) => {
  const a = js.indexOf("// " + tag + "-BEGIN"), b = js.indexOf("// " + tag + "-END");
  if (a < 0 || b < a) throw new Error("block " + tag + " not found");
  return js.slice(a, b);
};
const H = new Function("store", block("SUBITEM-PURE") + "\n" + block("BIDVIEW-RULES")
  + "\nreturn { bsGroupRows, subExportLabel };")({ getItem: () => null, setItem() {}, removeItem() {} });
const inp = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const lines = inp.lines;
const g = H.bsGroupRows(inp.rows, lines);
const rows = [];
const tot = {};
for (const B of g) {
  const n = B.line ? (B.line.n || null) : null;
  let sum = 0;
  for (const k of Object.keys(B.byUnit)) sum += B.byUnit[k];
  for (const kind of Object.keys(B.cf)) for (const k of Object.keys(B.cf[kind])) sum += B.cf[kind][k];
  if (n !== null) tot[n] = { sum: Math.round(sum * 10) / 10, other: Object.fromEntries(
    Object.entries(B.other).map(([u, v]) => [u, Math.round(v * 10) / 10])) };
  for (const m of B.members)
    rows.push({ label: m.label, unit: m.sheetUnit, n, ok: n === null ? false : !!m.sheetOk,
                counted: typeof m.counted === "number" ? Math.round(m.counted * 10) / 10 : null });
}
const exp = [];
for (const r of inp.rows) {
  if (r.unit === "ft") exp.push({ label: r.label, count: r.cnt || 1, length_ft: r.qty });
  else exp.push({ label: r.label, count: 1, length_ft: null, qty: r.qty, unit: r.unit });
  for (const s of (r.subs || [])) {
    if (!Number.isFinite(s.qty) || s.qty <= 0) continue;
    exp.push({ label: H.subExportLabel(r.label, s.rec.name, 80), count: 1, length_ft: null, qty: s.qty, unit: s.rec.unit });
  }
}
fs.writeFileSync(process.argv[3], JSON.stringify({ rows, lines: tot, export: exp }));
