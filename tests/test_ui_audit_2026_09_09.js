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

// ---- A ---- (the earthwork row layout left with the earthwork calculator on
// 2026-09-25; the callout-row rule it shared a fix with is still pinned)
check(/\.co-row \{ grid-template-columns: 1fr auto; \}/.test(css),
  "F: a callout row's text gets the row, not the contour row's 60 px first column");

// ---- B ----
const short = new Function(fnSrc("scaleShort") + "\nreturn scaleShort;")();
check(short({ text: `1"=20'`, ft_per_paper_inch: 20, source: "text" }) === `1"=20'`,
  "B: a short text reading is quoted as printed");
check(short({ text: `graphic bar 0-60 ft over 144.0pt (1" = 30', ticks)`, ft_per_paper_inch: 30, source: "graphic-bar" }) === `1" = 30'`,
  "B: the graphic-bar provenance sentence becomes the number on the status bar");
check(short({ text: `graphic bar 0-100 ft over 90.0pt (1" = 80.02', ticks)`, ft_per_paper_inch: 80.0167, source: "graphic-bar" }) === `1" = 80.02'`,
  "B: ...to two decimals when the bar did not land on a whole number");
check(short(null) === "", "B: no scale, empty");
check(/scale \$\{scaleShort\(sc\)\} \(\$\{_how \|\| sc\.source\}/.test(fnSrc("sheetFactsText")),
  "B: sheetFactsText uses it; the source in parentheses (a person's scale: how they set it, scale verdict S3)");

// ---- C ----
const head = fnSrc("renderSheetHead");
check(!/className = "sh-name"/.test(head), "C: the sheet card no longer prints the document name");
check(/meta\.title = state\.docName \|\| "";/.test(head), "C: ...the full name rides as the chips row's tooltip");

// ---- D ----
check(/ph\.textContent = "no preview yet";/.test(js) && !/textContent = "not opened yet"/.test(js),
  "D: the project card placeholder says what is true - no preview render - not 'not opened yet'");

// ---- E (reported with a screenshot): the header belongs to the view on screen ----
check(/function viewChromeSync\(\)/.test(js)
   && /document\.body\.classList\.toggle\("view-open", pv \|\| sv\);/.test(js),
  "E: opening Projects or Settings puts view-open on the body, where CSS can reach the header");
check(/viewChromeSync\(\);/.test(fnSrc("showProjects")) && /viewChromeSync\(\);/.test(fnSrc("showSettings")),
  "E: both full-screen views call it, opening and closing");
check(/body\.view-open header #docName, body\.view-open header #pageSel,\s*\n\s*body\.view-open header #detectBtn, body\.view-open header #toolsBtn,\s*\n\s*body\.view-open \.status-bar #docRemoveBtn \{ display: none !important; \}/.test(css),
  "E: the last sheet's name, page select, Detect, Tools and Remove-from-server stand down while a view is up");
check(/setStatus\(""\)/.test(fnSrc("viewChromeSync")),
  "E: the transient status line about the sheet that just left the screen is cleared");
check(/pl\.setAttribute\("aria-current", "page"\)/.test(fnSrc("viewChromeSync")) && /\.projects-link\[aria-current\]/.test(css),
  "E: the open view's nav link is marked current");

if (fails) { console.error("\n" + fails + " check(s) failed"); process.exit(1); }
console.log("\nall ui-audit checks passed");
