// "1 line", not "1 lines" (UI verify 2, 2026-10-02: the bid view said "1 lines"). One small helper, nWord(n, one,
// many), in webui/app.js and webui/cutfill/app.js; every count + noun built for the bid view, the takeoff rows, the
// counts and the status lines goes through it.  Dependency-free:  node tests/test_plurals.js

"use strict";
const fs = require("fs");
const path = require("path");

const W = path.join(__dirname, "..", "webui");
let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); }
  else console.log("ok:", msg);
}
const fnOf = (js) => {
  const a = js.indexOf("function nWord(");
  return a < 0 ? null : new Function(js.slice(a, js.indexOf("\n}\n", a) + 3) + "return nWord;")();
};

for (const f of ["app.js", "cutfill/app.js"]) {
  const js = fs.readFileSync(path.join(W, f), "utf8");
  const nWord = fnOf(js);
  check(!!nWord, `${f}: nWord is there`);
  if (!nWord) continue;
  check(nWord(1, "line") === "1 line" && nWord(11, "line") === "11 lines" && nWord(0, "line") === "0 lines",
    `${f}: 1 line / 11 lines / 0 lines`);
  check(nWord(1, "family", "families") === "1 family" && nWord(2, "family", "families") === "2 families",
    `${f}: an odd plural is given whole`);
}

const app = fs.readFileSync(path.join(W, "app.js"), "utf8");
const uses = [
  [/" · " \+ nWord\(m\.cnt, "line"\)\);/, "bid view: the line count beside a quantity"],
  [/\$\{nWord\(styleLiveCount\(s\.style_id\), "line"\)\} · `\)\);/, "takeoff style row (drawn)"],
  [/\$\{nWord\(styleLiveCount\(s\.style_id\), "line"\)\} · \$\{s\.signature\}/, "takeoff style row (refreshed)"],
  [/\+ nWord\(p\.n, "line"\)\)\.join\(LF\)/, "takeoff: the layers of one item"],
  [/nWord\(next\.n, "line"\) \+ " of the "/, "status: isolated layer"],
  [/"acts on the " \+ nWord\(g\.nHit, "row"\) \+ " shown/, "takeoff group tick: rows shown"],
  [/`exported \$\{nWord\(j\.exported, "element"\)\}`/, "status: approve exported"],
  [/\$\{nWord\(j\.items\.length, "item"\)\} itemized/, "status: approve itemized"],
  [/setStatus\(nWord\(j\.pages, "page"\)\);/, "status: pages on open"],
  [/setStatus\(nWord\(state\.kinds\.length, "page"\)\);/, "status: pages after the kinds are read"],
  [/\$\{nWord\(state\.result\.chains\.length, "line"\)\} in \$\{nWord\(state\.result\.styles\.length, "style"\)\}/, "sheet facts"],
];
for (const [re, what] of uses) check(re.test(app), `app.js uses nWord: ${what}`);
for (const old of ['" · " + m.cnt + " lines"', "${styleLiveCount(s.style_id)} lines", '+ p.n + " lines"',
                   "`exported ${j.exported} elements`", "${j.items.length} items itemized", "setStatus(`${j.pages} pages`)"])
  check(!app.includes(old), `app.js: the old "${old}" is gone`);

const cf = fs.readFileSync(path.join(W, "cutfill", "app.js"), "utf8");
check(/\(\$\{nWord\(r\.doc\.n_pages, 'page'\)\}\)\. Mark each sheet's role/.test(cf), "cut / fill status: Added <doc> (1 page)");
check(/nWord\(f\.n_contours, 'line'\)/.test(cf) && /nWord\(f\.n_contours, 'contour'\)/.test(cf)
      && /nWord\(f\.n_contours, role \+ ' line'\)/.test(cf), "cut / fill family counts use nWord");

if (failures) { console.error(`\n${failures} FAILURE(S)`); process.exit(1); }
console.log("\nall plural checks passed");
