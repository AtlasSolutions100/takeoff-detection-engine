// THE 2026-09-09 UI AUDIT, PINNED. Evidence: out/verify/2026-09-09/ui-audit.
//
//   A. earthwork rows were 4-5 lines tall (the provenance sentence wrapped
//      beside the buttons in a 200 px column; 3 rows to a screen of 150 on
//      Oncor C-04). The sentence now has the row's whole width on a second
//      line (.ew-src); the identity stays beside the buttons, one line.
//   B. the status bar quoted the graphic-bar provenance sentence verbatim -
//      the same sentence as the sheet-head chip, twice on one screen - and
//      clipped at 1180 px. scaleShort gives the bar the number.
//   C. the sheet card printed the document name under the top bar that
//      already carries it. Gone; the name rides as the card's tooltip.
//   D. a detected job with no preview render said "not opened yet" under
//      "all detected". It says "no preview yet".
//
// run: node tests/test_ui_audit_2026_09_09.js
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "..", "webui", "style.css"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(name) {
  const m = js.match(new RegExp("\\nfunction " + name + "\\([^)]*\\) \\{[\\s\\S]*?\\n\\}\\n"));
  return m ? m[0] : "";
}

// ---- A ----
const renderEw = fnSrc("renderEw");
check(/src\.className = "ew-src";\s*\n\s*src\.textContent = ewRowSource\(row\);/.test(renderEw),
  "A: the provenance sentence is its own element under the row (.ew-src), still on the row");
check(/meta\.textContent = `line \$\{row\.chain_id\} · w \$\{row\.width\}`;/.test(renderEw),
  "A: the identity beside the buttons is one line - line and weight, no sentence");
check(/src\.prepend\(cb\);/.test(renderEw) && !/meta\.prepend\(cb\);/.test(renderEw),
  "A: the evidence badge leads the provenance line, so the identity always fits beside the buttons");
check(/why\.className = "ew-inf-why";\s*\n\s*why\.textContent = row\.inferredWhy;\s*\n\s*src\.appendChild\(why\);/.test(renderEw),
  "A: an inferred row's sentence goes under the row too, in the class the disclosure test pins");
check(/div\.append\(z, meta, seg, x, src\);/.test(renderEw),
  "A: the source line is appended after the buttons, so the grid puts it on the next line");
check(/\.ew-src \{[^}]*grid-column: 1 \/ -1/.test(css),
  "A: .ew-src spans the row's full width in the grid");
check(/\.ew-meta \{[^}]*white-space: nowrap/.test(css),
  "A: the identity column no longer wraps");

// ---- B ----
const short = new Function(fnSrc("scaleShort") + "\nreturn scaleShort;")();
check(short({ text: `1"=20'`, ft_per_paper_inch: 20, source: "text" }) === `1"=20'`,
  "B: a short text reading is quoted as printed");
check(short({ text: `graphic bar 0-60 ft over 144.0pt (1" = 30', ticks)`, ft_per_paper_inch: 30, source: "graphic-bar" }) === `1" = 30'`,
  "B: the graphic-bar provenance sentence becomes the number on the status bar");
check(short({ text: `graphic bar 0-100 ft over 90.0pt (1" = 80.02', ticks)`, ft_per_paper_inch: 80.0167, source: "graphic-bar" }) === `1" = 80.02'`,
  "B: ...to two decimals when the bar did not land on a whole number");
check(short(null) === "", "B: no scale, empty");
check(/scale \$\{scaleShort\(sc\)\} \(\$\{sc\.source\}/.test(fnSrc("sheetFactsText")),
  "B: sheetFactsText uses it; the source in parentheses is unchanged");

// ---- C ----
const head = fnSrc("renderSheetHead");
check(!/className = "sh-name"/.test(head), "C: the sheet card no longer prints the document name");
check(/meta\.title = state\.docName \|\| "";/.test(head), "C: ...the full name rides as the chips row's tooltip");

// ---- D ----
check(/ph\.textContent = "no preview yet";/.test(js) && !/textContent = "not opened yet"/.test(js),
  "D: the project card placeholder says what is true - no preview render - not 'not opened yet'");

if (fails) { console.error("\n" + fails + " check(s) failed"); process.exit(1); }
console.log("\nall ui-audit checks passed");
