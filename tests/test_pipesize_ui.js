// PIPE SIZES ON RUNS IN THE UI (2026-09-16; Wave 2 item 8). The Sheet
// callouts panel gains a "Pipe sizes on runs" block fed by the pipesizes
// route with the UI's own family names; a nameless single-size family gets
// a "name it 8\"" button that renames it so the size travels by name; the
// bid view prints "by size" on every LF line in sheet mode.
//
// run: node tests/test_pipesize_ui.js   (fails on 4d15dc4)
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
check(html.includes('id="psRows"') && html.indexOf('id="wlRows"') < html.indexOf('id="psRows"') && html.indexOf('id="psRows"') < html.indexOf('id="coRows"'), "#psRows sits between the watch-list and the leader rows");
for (const n of ["loadPipeSizes", "renderPipes", "psName"]) check(!!fnSrc(n), `${n} exists`);
check(/renderCo\(\);\n  loadPipeSizes\(\);/.test(js), "the panel reads the sizes after the leaders are cached");
check(/state\.watch = null; state\.pipes = null;/.test(js), "a page switch forgets the sizes");
const lp = fnSrc("loadPipeSizes");
check(/for \(const \[sid, name\] of state\.labels\) labels\[sid\] = name;/.test(lp) && /\/pipesizes`/.test(lp) && /method: "POST"/.test(lp), "the route gets the UI's own family names");
check(/if \(!box \|\| !state\.result\) return;/.test(lp), "no detect result, no sizes (the route would 404)");
const pn = fnSrc("psName");
check(/state\.labels\.set\(f\.style_id, nm\);\n  colorBust\(\); memSave\(st\);\n  renderStyles\(\); renderItemized\(\); renderMiniLegend\(\);/.test(pn), "naming a family goes through the same path as typing its name: colour convention, memory, every view");
check(/if \(!st \|\| !f\.suggest\) return;/.test(pn), "no suggestion, no rename");
const rp = fnSrc("renderPipes");
check(/`name it \$\{f\.suggest\}`/.test(rp) && /too little of the run is sized to name it/.test(rp) && /split the family first, never guessed/.test(rp), "single (with or without enough evidence) and mixed are said in words");
check(/named \$\{f\.name_size\}` \+ \(f\.agrees === false \? ` \\u2014 but its runs print/.test(rp), "a named family whose runs print another size is flagged");
check(/if \(p\.leaders_note\) box\.appendChild/.test(rp), "the route's note about unread leaders is shown");
check(/row\.onmouseenter = \(\) => hotFam\(\[f\.style_id\]\);/.test(rp), "hovering a family lights its lines");
check(/if \(B\.bucket === "Storm Drainage" \|\| \(sheetMode && B\.line && B\.line\.unit === "LF"\)\) \{/.test(fnSrc("renderBidView")), "sheet mode prints by-size on every LF line");
check(/\.ps-row \.wl-line \{ flex-wrap: wrap; \}/.test(css), "style exists");
check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 189 && +((/style\.css\?v=(\d+)/.exec(html) || [])[1]) >= 94, "cache busters moved");
if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all pipesize-ui checks passed");
