// PROPOSED-SURFACE ZONES + PRINTED DEPTHS ON THE MAP, UI (2026-09-16; Wave 2
// items 4 and 5). The zone modal's "proposed" mode (the request carries the
// PROPOSED rows and spot grades), the zone named from the text inside it
// while it still wears "Zone N", the map legend's "print depths" toggle
// and the canvas painting one number per block in the zone numbers' ink.
//
// run: node tests/test_zones_proposed_ui.js   (fails on the commit before it)
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
check(/<input type="radio" name="gzMode" value="proposed">/.test(html) && /Proposed contours on this sheet/.test(html), "the modal offers the proposed mode");
check(html.indexOf('value="strip_only"') < html.indexOf('value="proposed"'), "it comes after the declared designs");
check(html.includes('id="ewMapNumsWrap"') && html.includes('id="ewMapNums"') && /print depths/.test(html), "the map legend has a print-depths toggle");
for (const n of ["gzProposedRows", "gzProposedPoints"]) check(!!fnSrc(n), `${n} exists`);
check(/r\.surface === "proposed" && isFinite\(r\.z\)/.test(fnSrc("gzProposedRows")) && /state\.ew\.spotSide !== "proposed"/.test(fnSrc("gzProposedPoints")), "the proposed rows and spot grades are gathered like the existing ones");
check(/proposed_rows: gzProposedRows\(\)\.map\(r => \(\{ chain_id: r\.chain_id, z: r\.z \}\)\),\n    proposed_points: gzProposedPoints\(\),/.test(fnSrc("gzBody")), "the zone request carries them");
check(/return \{ design: \{ mode \}, strip_in, base \};/.test(fnSrc("gzModalCollect")), "a mode with no inputs (strip only, proposed) collects as {mode}");
const gc = fnSrc("gzCompute");
check(/const fresh = !!z\.autoName; delete z\.autoName;/.test(gc) && /if \(fresh && !z\.added && j\.suggested_name && \/\^Zone \\d\+\$\/\.test\(z\.name \|\| ""\)\) \{\n        z\.name = String\(j\.suggested_name\)\.slice\(0, 40\);\n        z\.named_from = "text";\n        calcStateDirty\(\);/.test(gc), "only a zone just drawn, still 'Zone N' and not yet added takes the structure's name - a restored zone never renames itself");
const save = js.slice(js.indexOf('$("gzMSave").onclick'), js.indexOf('$("gzMCancel").onclick'));
check(/name: "Zone " \+ state\.gzSeq\+\+,[\s\S]*autoName: true,[\s\S]*state\.gzones\.push\(z\);/.test(save) && !/z\.autoName = true/.test(save.slice(save.indexOf("} else {"))), "the modal marks a NEW zone as fresh, never an edited one");
check(/else if \(d\.mode === "proposed"\)\n    s = "proposed contours"/.test(fnSrc("gzDesignSummary")), "the zone card says 'proposed contours', not 'strip only'");
check(/named_from: z\.named_from === "text" \? "text" : null,/.test(js) && /named_from: s\.named_from === "text" \? "text" : null,/.test(js), "where the name came from persists with the zone");
check(/named from the text printed inside it/.test(fnSrc("renderGz")), "the name box says so");
const fm = fnSrc("ewFetchMap");
check(/earthwork\/map\?meta=1/.test(fm) && /state\.ew\.map\.labels = m\.labels \|\| \[\];/.test(fm), "after the map, its meta brings the block depths");
check(/nw\.hidden = !\(state\.ew\.map\.labels && state\.ew\.map\.labels\.length\);/.test(fm), "the toggle shows only when there are depths to print");
check(/if \(\$\("ewMapNums"\)\) \$\("ewMapNums"\)\.onchange = \(\) => draw\(\);/.test(js), "the toggle repaints");
const dr = fnSrc("draw");
check(/if \(lab && lab\.length && \$\("ewMapNums"\) && \$\("ewMapNums"\)\.checked\) \{/.test(dr) && /ctx\.fillStyle = dzv < 0 \? CV_GZ_CUT_LAB : dzv > 0 \? CV_GZ_FILL_LAB : CV_GZ_ZERO_LAB;/.test(dr.slice(dr.indexOf("PRINTED DEPTHS"))), "the map's numbers use the zone numbers' ink: cut red, fill blue, with a halo");
check(/const t = \(dzv > 0 \? "\+" : ""\) \+ dzv\.toFixed\(1\);/.test(dr.slice(dr.indexOf("PRINTED DEPTHS"))), "fill carries a plus, cut its minus, one decimal");
check(/#ewMapNumsWrap \{/.test(css), "style exists");
check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 191 && +((/style\.css\?v=(\d+)/.exec(html) || [])[1]) >= 96, "cache busters moved");
if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all proposed-zone-ui checks passed");
