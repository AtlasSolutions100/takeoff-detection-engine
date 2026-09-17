// A SIDE PER SPOT TAG, DEPTHS PRINTED BY DEFAULT, THE ZONE SECTION WAITING
// VISIBLY (2026-09-16, dev). Three fixes from the live review:
//
//   - the panel put every switched-on spot grade on ONE side, so a sheet
//     printing ME (match existing) beside TW/BW (wall tops) could not use
//     both; the engine's own per-spot `side` was thrown away
//   - the cut/fill depths were opt-in on the map and in a new zone
//   - the grading-zone section refused clicks with a status line while the
//     sheet's elevations were still reading, instead of greying out
//
// run: node tests/test_spot_sides_ui.js   (fails on the commit before it)
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

// ---- the two helpers, run for real ---------------------------------------------------------
const side = fnSrc("ewSpotSideOf"), split = fnSrc("ewSpotsSplit");
check(!!side && !!split, "ewSpotSideOf and ewSpotsSplit exist");
const spots = [
  { tag: "ME", side: "existing", x: 1, y: 1, z: 100 },
  { tag: "ME", side: "existing", x: 2, y: 1, z: 101 },
  { tag: "TW", side: "proposed", x: 3, y: 1, z: 110 },
  { tag: "BW", side: "proposed", x: 4, y: 1, z: 108 },
  { tag: "XX", x: 5, y: 1, z: 109 },              // a tag the engine did not side
];
const env = { state: { ew: { spots, spotOn: { ME: true, TW: true, BW: true, XX: true }, spotTagSide: {} } } };
env.ewActiveSpots = () => env.state.ew.spots.filter(g => env.state.ew.spotOn[g.tag]);
const run = new Function("state", "ewActiveSpots", side + "\n" + split
  + "\nreturn { sideOf: ewSpotSideOf, split: ewSpotsSplit };")(env.state, env.ewActiveSpots);
check(run.sideOf("ME") === "existing" && run.sideOf("TW") === "proposed",
      "each tag takes the side the engine gave its grades");
check(run.sideOf("XX") === "proposed" && run.sideOf("NOPE") === "proposed",
      "a tag with no side from the engine is new ground, never a guess at existing");
env.state.ew.spotTagSide.TW = "existing";
check(run.sideOf("TW") === "existing" && run.sideOf("ME") === "existing",
      "the operator's answer for one tag outranks the engine and leaves the others alone");
env.state.ew.spotTagSide.TW = "proposed";
let s = run.split();
check(s.existing.length === 2 && s.proposed.length === 3
      && s.existing.every(p => p.z >= 100 && p.z <= 101),
      `both lists come back at once: ${s.existing.length} existing, ${s.proposed.length} proposed`);
check(Object.keys(s.existing[0]).sort().join(",") === "x,y,z",
      "the points carry x, y, z and nothing else");
s = run.split(g => g.x > 2);
check(s.existing.length === 0 && s.proposed.length === 3, "the filter crops both sides (the work zone)");
env.state.ew.spotOn.TW = false;
check(run.split().proposed.length === 2, "a tag switched off is in neither list");

// ---- the run body and the zone body send both ------------------------------------------------
const body = fnSrc("ewBody");
check(/if \(sides\.existing\.length\) o\.existing_points = sides\.existing;/.test(body)
      && /if \(sides\.proposed\.length\) o\.proposed_points = sides\.proposed;/.test(body),
      "the Calculate body sends both point lists, not one");
check(!/state\.ew\.spotSide === "existing" \? "existing_points"/.test(js),
      "nothing files every spot by the one old global side");
check(/return ewSpotsSplit\(\)\.existing;/.test(fnSrc("gzExistingPoints"))
      && /return ewSpotsSplit\(\)\.proposed;/.test(fnSrc("gzProposedPoints")),
      "a grading zone splits them the same way");

// ---- the panel ---------------------------------------------------------------------------------
const rend = fnSrc("renderEwSpots");
check(/\["existing", "all EXIST"\], \["proposed", "all PROP"\]/.test(rend)
      && /for \(const t of Object\.keys\(counts\)\) state\.ew\.spotTagSide\[t\] = key;/.test(rend),
      "the head's two buttons are bulk: they set every tag");
check(/sb\.className = "sp-side " \+ \(side === "existing" \? "on-ex" : "on-pr"\);/.test(rend),
      "every tag chip carries its own side button, inked like the row buttons");
check(/e\.preventDefault\(\); e\.stopPropagation\(\);/.test(rend),
      "swapping a side does not also switch the tag off (the button sits inside the label)");
check(/state\.ew\.spotTagSide\[t\] = side === "existing" \? "proposed" : "existing";/.test(rend),
      "the side button swaps that tag only");
check(/on the ground now/.test(rend) && /tops of walls and pipes, not ground/.test(rend)
      && !/will be used as \$\{state\.ew\.spotSide\}/.test(rend),
      "the note counts both sides instead of naming one");
check(/calcStateDirty\(\); renderEwSpots\(\); draw\(\);/.test(rend), "a change is saved with the sheet");
check(/spotTagSide: Object\.assign\(\{\}, state\.ew\.spotTagSide\)/.test(js)
      && /for \(const \[t, v\] of Object\.entries\(sv\.spotTagSide\)\)/.test(js),
      "the sides persist with the sheet");
// "proposed" was the SHIPPED DEFAULT: carrying it forward as a per-tag
// answer pinned ME to the proposed surface on every sheet already worked
// on, which is the defect this whole change removes. Measured live on
// Oncor C-04 before this line: all four tags came back PROP.
check(/\} else if \(sv\.spotSide === "existing"\) \{/.test(js)
      && /state\.ew\.spotTagSide\[g\.tag\] = "existing";/.test(js)
      && !/spotTagSide\[g\.tag\] = sv\.spotSide/.test(js),
      "an old sidecar carries only a side the operator can have chosen - the default carries nothing");
check(/spotTagSide: \{\},/.test(js), "the panel starts with no overrides - the engine's reading stands");

// ---- the depths print by default -----------------------------------------------------------------
check(/<input type="checkbox" id="ewMapNums" checked>/.test(html),
      "the map's depths print unless the box is unticked");
check(/added: false, showNums: true,/.test(js), "a zone just drawn prints its depths");
const fm = fnSrc("ewFetchMap");
check(/const mine = state\.ew\.map = \{ img, bbox/.test(fm)
      && /if \(state\.ew && state\.ew\.map === mine\) \{/.test(fm)
      && /if \(!state\.ew \|\| state\.ew\.map !== mine\) return;/.test(fm),
      "two map fetches in flight: each writes back only to the map it made");
check(/added: !!s\.added, showNums: s\.show_nums !== false,/.test(js),
      "a saved zone prints them unless it was explicitly turned off");

// ---- the basis line names both sides ---------------------------------------------------------------
const basis = fnSrc("renderEwBasis");
check(/const nEx = \(\(body && body\.existing_points\) \|\| \[\]\)\.length;/.test(basis)
      && /const nPr = \(\(body && body\.proposed_points\) \|\| \[\]\)\.length;/.test(basis)
      && /nEx && nPr \? `\$\{nEx\} on the ground now, \$\{nPr\} on the new ground`/.test(basis),
      "the basis line says how many spot grades went on each surface, not one name for all of them");
check(!/const side = \(body && body\.existing_points\) \? "existing" : "proposed";/.test(js),
      "nothing names one surface for every spot grade any more");

// ---- the grading-zone section waits visibly --------------------------------------------------------
const gate = fnSrc("gzGate");
check(!!gate, "gzGate exists");
for (const [re, what] of [[/Detect the sheet first/, "no detection"],
                          [/Reading this sheet\\u2019s elevations/, "still reading"],
                          [/another page\\u2019s/, "another page's elevations"],
                          [/did not read/, "the read failed"],
                          [/Press EARTHWORK to read this sheet\\u2019s elevations first/, "panel never opened"]])
  check(re.test(gate), `it says why it is waiting: ${what}`);
check(/sec\.classList\.toggle\("gz-wait", !!why\);/.test(gate), "the section greys while it waits");
check(/#gzSection\.gz-wait \.ew-bound-row button \{ opacity: \.45; pointer-events: none; \}/.test(css),
      "greyed means the buttons cannot be pressed");
check(/gzGate\(\);\n  if \(!box\) return;/.test(fnSrc("renderGz")), "renderGz gates");
check(/if \(typeof gzGate === "function"\) gzGate\(\);/.test(fnSrc("ewBusyPut")),
      "and the reading progress re-gates it, so it clears the moment the read ends");
// ewBusyPut's FIRST call lands before state.ewLoading is set, so the gate
// showed "press EARTHWORK" right through a read until the flag itself
// re-gated it (measured live on Oncor C-04). Three call sites: the progress
// tick, the flag going up, the flag coming down.
const gateCalls = (js.match(/if \(typeof gzGate === "function"\) gzGate\(\);/g) || []).length;
check(gateCalls >= 3, `the gate re-runs the moment the loading flag moves, both ways (${gateCalls} call sites)`);
check(/state\.ewLoading = p;/.test(js) && /if \(state\.ewLoading === p\) state\.ewLoading = null;/.test(js),
      "...around the flag this panel already used");
check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 192 && +((/style\.css\?v=(\d+)/.exec(html) || [])[1]) >= 97,
      "cache busters moved");

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all spot-side / depths / zone-gate checks passed");
