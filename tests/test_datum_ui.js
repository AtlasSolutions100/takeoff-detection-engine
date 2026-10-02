// THE DATUM IN THE STRUCTURE PANEL (2026-09-16; Wave 2 item 2). A datum
// input on the panel, the set's own datum notes as chips (a click sets it,
// a numberless note is shown but cannot be clicked), datum_ft on drafts /
// rows / calcstate / the POST body, and the footing chips' BOF elevations
// shown both ways once a datum is known.
//
// run: node tests/test_datum_ui.js   (fails on 0e71b80)
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
check(!!fnSrc("stDatumBlock") && !!fnSrc("stFetchDatum") && !!fnSrc("stCivil"), "the datum block, its fetch and the conversion exist");
check(/body\.appendChild\(stDatumBlock\(st\)\);/.test(fnSrc("renderSt")), "renderSt places the datum block after the footing chips");
check(/fetch\(`\/api\/job\/\$\{state\.job\}\/datum`\)/.test(fnSrc("stFetchDatum")), "it fetches the document-wide datum route");
check(/wall_in: 8, datum_ft: NaN(,[\s\S]{0,700}?)? \};/.test(js), "the panel starts with no datum");
check((js.match(/datum_ft: st\.datum_ft \};/g) || []).length === 2, "a picked or drawn draft copies the panel's datum");
check(/stNumInput\(d, "datum_ft", "0\.01", upd\)/.test(js) && /stNumInput\(it, "datum_ft", "0\.01", upd\)/.test(js), "the draft and every committed row carry their own datum input");
check(/datum_ft: Number\.isFinite\(\+d\.datum_ft\) \? \+d\.datum_ft : null \}\] \}\),/.test(js), "the server cross-check body carries it");
check(/wall_in: it\.wall_in, datum_ft: Number\.isFinite\(\+it\.datum_ft\) \? \+it\.datum_ft : null,/.test(js) && /datum_ft: calcNum\(it\.datum_ft\) !== null \? \+it\.datum_ft : NaN,/.test(js) && /if \(calcNum\(sv\.datum_ft\) !== null\) st\.datum_ft = \+sv\.datum_ft;/.test(js), "calcstate saves and restores it on rows and on the panel");

// harness
const calls = { status: [], rendered: 0, dirty: 0, fetched: 0 };
const mkStub = "function stMk(tag, cls, text) { const el = { tag, className: cls || '', textContent: text || '', title: '', disabled: false, children: [], classList: { add(c) { el.className += ' ' + c; } }, appendChild(c) { el.children.push(c); return c; } }; return el; }\n";
const state = { job: "j", page: 14, gen: 1, st: { draft: null, datum_ft: NaN, footings: null, datum: null } };
const H = new Function("state", "calls",
  "const document = { createElement: (t) => ({ tag: t, value: '', className: '', placeholder: '' }) };\n"
  + "const setStatus = (m, k) => calls.status.push([m, k || '']); const renderSt = () => { calls.rendered++; }; const calcStateDirty = () => { calls.dirty++; };\n"
  + "const stFetchDatum = () => { calls.fetched++; }; const stFetchFootings = () => {}; const stGroupsTable = () => stMk('div', 'st-groups');\n" + mkStub
  + fnSrc("stCivil") + "\n" + fnSrc("stFt") + "\n" + fnSrc("stDatumBlock") + "\n" + fnSrc("stFootingsBlock")
  + "\nreturn { stDatumBlock, stFootingsBlock, stCivil };")(state, calls);
check(H.stCivil(86, 7732) === "7,718′" && H.stCivil(84.1667, 7732) === "7,716.17′", "86'-0\" on a 7732 datum reads 7,718 civil (rounded to 0.01)");
let b = H.stDatumBlock(state.st);
check(calls.fetched === 1 && /looking for a datum note/.test(b.children[1].textContent), "the first render asks the set for its datum notes");
const inp = b.children[0].children[0];
check(inp.tag === "input" && inp.placeholder === "e.g. 5000" && inp.value === "", "the input is empty until the operator types");
state.st.datum = { job: "j", loading: false, err: null, data: { notes: [
  { page: 2, kind: "datum", label: "100'-0\" equals", value_ft: 7732, text: "[100'=7732']" },
  { page: 2, kind: "level", label: "MAIN LEVEL ENTRY", value_ft: 7733.25, text: "MAIN LEVEL ENTRY:7733.25'" },
  { page: 3, kind: "note", label: "MAIN LEVEL SUBFLOOR ELEVATION", value_ft: null, text: "DATUM ELEVATION 100'-0\" EQUALS TOP OF MAIN LEVEL SUBFLOOR ELEVATION" }] } };
b = H.stDatumBlock(state.st);
const chips = b.children.find(c => c.className === "st-chips").children;
check(chips.length === 3 && chips[0].textContent === "100'-0\" equals: 7732′ (page 3)" && /printed: \[100'=7732'\]/.test(chips[0].title), "the datum statement is a chip with its printed text and page (" + chips[0].textContent + ")");
check(chips[2].disabled && /st-chip-guess/.test(chips[2].className) && /no number/.test(chips[2].textContent), "the numberless structural note is shown but cannot be clicked");
chips[0].onclick();
check(state.st.datum_ft === 7732 && calls.dirty >= 1 && /datum set: 100′-0″ = 7732′/.test(calls.status[calls.status.length - 1][0]), "a click sets the panel's datum and says so");
// BOF both ways in the footing chips once a datum is known
state.st.footings = { job: "j", page: 14, loading: false, err: null, data: { by_mark: { "CF2.0": { count: 3, unpaired: 0, bof: { "86": 2, "84.1667": 1 }, schedule: { from: "schedule", kind: "continuous", width_ft: 2, depth_ft: 1, page: 11 } } }, slabs: [], schedule: { pages: [11], rows: 1 } } };
const fb = H.stFootingsBlock(state.st);
const chip = fb.children.find(c => c.className === "st-chips").children[0];
check(/BOF 86′ \(7,718′ civil\) ×2, 84\.1667′ \(7,716\.17′ civil\)/.test(chip.textContent), "with a datum, every BOF reads both ways: " + chip.textContent);
state.st.draft = { datum_ft: 7700 };
const fb2 = H.stFootingsBlock(state.st);
check(/86′ \(7,686′ civil\)/.test(fb2.children.find(c => c.className === "st-chips").children[0].textContent), "a draft's own datum wins over the panel's");
check(/\.st-datum \{ display: flex; flex-direction: column; gap: 4px; margin-top: 6px; \}/.test(css), "datum block styled");
check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 184 && +((/style\.css\?v=(\d+)/.exec(html) || [])[1]) >= 89, "cache busters moved");

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all datum-ui checks passed");
