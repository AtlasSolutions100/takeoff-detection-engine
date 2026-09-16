// THE WATCH-LIST IN THE SHEET CALLOUTS PANEL (2026-09-16; Wave 2 item 7).
// Fetched first and without Detect, rendered above the leader rows: one
// row per watch word with count, sizes and tags, a "+ n EA" button that
// adds an ordinary EA row (BY OTHERS / N.I.C. left out and said so), item
// hover marks the spot, click jumps there.
//
// run: node tests/test_watchlist_ui.js   (fails on 1dcded5)
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "..", "webui", "style.css"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
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
check(html.includes('id="wlRows"') && html.indexOf('id="wlRows"') < html.indexOf('id="coRows"'), "#wlRows sits above the leader rows in the callouts panel");
check(/septic tank, propane tank, culvert, area inlet, cleanout, wye/.test(html), "the hint names the watch words");
for (const n of ["renderWatch", "wlAdd", "wlHas", "wlLabel", "wlMk"]) check(!!fnSrc(n), `${n} exists`);
const h = js.slice(js.indexOf('$("coBtn").onclick = async () => {'), js.indexOf("function renderWatch("));
check(h.indexOf("/watchlist`") > 0 && h.indexOf("/watchlist`") < h.indexOf("/callouts`") && h.indexOf("renderWatch();") < h.indexOf("if (!state.co)"), "the panel fetches and draws the watch-list before it asks for the leaders (which need Detect)");
check(/state\.watch = r\.ok \? await r\.json\(\) : \{ err: "failed \(" \+ r\.status \+ "\)" \}/.test(h), "a failed fetch is shown, not thrown");
check(/state\.co = null; state\.watch = null;/.test(js), "a page switch forgets the watch-list with the leaders");
const rw = fnSrc("renderWatch");
check(/wlMk\("button", "mini wl-add", wlHas\(g\) \? "in the takeoff" : `\+ \$\{g\.in_scope\} EA`\)/.test(rw), "the add button says how many EA it adds, or that the row is already in");
check(/add\.disabled = !\(g\.in_scope > 0\) \|\| wlHas\(g\);/.test(rw), "nothing to add when every one is BY OTHERS / N.I.C., or once added");
check(/li\.onclick = \(\) => centerOn\(it\.x, it\.y\);/.test(rw) && /state\.symMarks = \[\[it\.x, it\.y\]\]; draw\(\);/.test(rw), "an item click jumps there, hover marks the spot");
check(/"wl-item" \+ \(oos \? " oos" : ""\)/.test(rw), "an out-of-scope item is struck through");
check(/state\.extraItems\.push\(\{ label: wlLabel\(g\), qty: g\.in_scope, unit: "EA" \}\);\n  calcStateDirty\(\); renderItemized\(\); renderWatch\(\);/.test(fnSrc("wlAdd")), "adding pushes an EA row named by the group and its single size, saves, re-renders");
const H = new Function("state", fnSrc("wlLabel") + "\n" + fnSrc("wlHas") + "\nreturn { wlLabel, wlHas };")({ extraItems: [{ label: "Septic Tank 1,500 gal", qty: 1, unit: "EA" }] });
check(H.wlLabel({ label: "Septic Tank", sizes: { "1,500 gal": 1 } }) === "Septic Tank 1,500 gal" && H.wlLabel({ label: "Area Inlet", sizes: {} }) === "Area Inlet" && H.wlLabel({ label: "Culvert", sizes: { '18"': 1, '24"': 1 } }) === "Culvert", "the row's name carries the size only when there is one size");
check(H.wlHas({ label: "Septic Tank", sizes: { "1,500 gal": 1 } }) === true && H.wlHas({ label: "Wye", sizes: {} }) === false, "wlHas finds the row by that name");
check(/\.wl-row \{/.test(css) && /\.wl-item\.oos \{ text-decoration: line-through; \}/.test(css), "watch-list styles exist");
check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 188 && +((/style\.css\?v=(\d+)/.exec(html) || [])[1]) >= 93, "cache busters moved");
if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all watchlist-ui checks passed");
