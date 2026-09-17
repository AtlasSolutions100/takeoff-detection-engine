// AN ABSTAIN MUST LEAVE NOTHING BEHIND, AND A DROP MUST BE VISIBLE.
//
// THE DEFECT (measured 2026-09-02, live on :9855 against the DEM-truth
// outline sheet). Two halves, both in webui/app.js:
//
//  1. The 400 handler was one line:
//         $("ewResult").textContent = e.detail || ("failed (" + r.status + ")");
//         return;
//     Everything the PREVIOUS Calculate had painted stayed on screen beside
//     that sentence - the per-layer slabs in #ewLayerOut, the basis block,
//     the material balance - state.ew.result kept the superseded numbers,
//     and if the operator had pressed "Add to takeoff" those cubic yards
//     were still in state.extraItems and would still price, export and
//     print. A refusal that leaves a number on screen is not an abstain.
//     It was also about to render "[object Object]": the route's refusal
//     detail is now a structured object (message / code / surface / basis).
//
//  2. /earthwork/suggest has reported suggestions_dropped since the route
//     was written and NO SCREEN EVER RENDERED IT (grep across the repo:
//     two hits, both in server.py). With the ladder gate live an OCR-only
//     sheet whose reads do not step like contours now yields ZERO rows, and
//     the panel's empty state would have said "We found no contour
//     elevations on this sheet" - the one thing the reader knows is false.
//
//     node tests/test_ew_abstain_ui.js
"use strict";
const fs = require("fs");
const path = require("path");
const SRC = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const HTML = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
const CSS = fs.readFileSync(path.join(__dirname, "..", "webui", "style.css"), "utf8");

let fails = 0;
const check = (ok, msg) => {
  if (ok) console.log("ok: " + msg);
  else { fails++; console.error("FAIL: " + msg); }
};
const eq = (got, want, msg) =>
  check(got === want, msg + " (got " + JSON.stringify(got) + ")");

function fnSrc(name) {
  const j = SRC.indexOf("function " + name + "(");
  if (j < 0) throw new Error("no function " + name);
  let d = 0;
  for (let k = SRC.indexOf("{", j); k < SRC.length; k++) {
    if (SRC[k] === "{") d++;
    else if (SRC[k] === "}" && --d === 0) return SRC.slice(j, k + 1);
  }
  throw new Error("unbalanced braces reading " + name);
}

// ---------------------------------------------------------------------------
// THE DOM COMES FROM index.html, NOT FROM THE FUNCTION UNDER TEST.
//
// This harness used to build exactly two elements - "the elements ewRefused
// touches" - and `$` answered undefined for every other id. That is a
// STRUCTURAL blind spot, not a missing assertion: a DOM scoped to what the
// function does can only ever confirm that it does it, and cannot express
// the failure of a refusal to touch something. It hid a live defect for
// exactly that reason (part 6 below): ewRefused cleared every box in the
// panel and left state.ew.map set, so a full-sheet cut/fill depth map and
// its numbered ramp stayed painted on the drawing beside "No volume
// published". Had the harness owned #ewMapLegend, the check would have been
// writable.
//
// So the ids come from webui/index.html - the page the operator loads - and
// asking for one the page does not have THROWS instead of returning
// undefined. Narrowing this again would have to be done deliberately, here,
// against the real page.
// ---------------------------------------------------------------------------
const PAGE_IDS = [...HTML.matchAll(/\bid="([A-Za-z0-9_.:-]+)"/g)].map(m => m[1]);
check(PAGE_IDS.length > 100,
  "the harness DOM is built from every id in index.html (got "
  + PAGE_IDS.length + ")");
for (const id of ["ewResult", "ewLayerOut", "ewMapLegend", "ewMap", "ewDropped"])
  check(PAGE_IDS.includes(id),
    "...including #" + id + ", which the earthwork panel owns");

function mkEl(id) {
  const el = {
    id: id, innerHTML: "", _text: "", hidden: false, children: [],
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    appendChild(c) { this.children.push(c); return c; },
    get textContent() {
      return this._text + this.children.map(c => c.textContent).join("");
    },
    set textContent(v) { this._text = v; this.children = []; },
  };
  return el;
}

// THE 4010783 HANDLER, VERBATIM, so this file fails on BEHAVIOUR there and
// not on a stack trace: `git archive 4010783 | tar -x` has no ewRefused, and
// "the function is missing" proves only that the function is new. What has
// to fail is what the operator saw.
const OLD_HANDLER =
  'function ewRefused(detail, status) {\n'
  + '  $("ewResult").textContent = detail || ("failed (" + status + ")");\n'
  + '}';
const HAS_NEW = /function ewRefused\(/.test(SRC);
check(HAS_NEW,
  "webui/app.js has an ewRefused() that renders the abstain "
  + "(4010783 wrote e.detail straight into #ewResult and returned)");

function run(detail, opts) {
  opts = opts || {};
  const els = {};
  for (const id of PAGE_IDS) els[id] = mkEl(id);
  // the map legend is `hidden` in index.html until a map is painted; here it
  // starts SHOWN, because the state under test is "a map was painted and
  // then the volume was refused"
  els.ewMapLegend.hidden = false;
  els.ewMap.checked = true;
  const calls = [];
  const state = {
    ew: { result: { cut_yd3: 17997.6, fill_yd3: 1.0 }, added: !!opts.added,
          rows: [],
          // the bitmap draw() paints under the linework, and its ramp
          map: { img: { width: 240, height: 180 }, bbox: [0, 0, 100, 80],
                 refDz: 3.4 } },
    extraItems: [{ label: "Site Fill", qty: 17998, unit: "CY" },
                 { label: "Concrete", qty: 12, unit: "CY" }],
  };
  const said = [];
  const f = new Function(
    "$", "state", "document", "ewClearItems", "renderEwBasis",
    "renderEwBalance", "renderEwTake", "renderItemized", "setStatus", "draw",
    "ewMapNote",
    (HAS_NEW ? fnSrc("ewRefused") : OLD_HANDLER)
    + "\nreturn ewRefused;");
  const doc = {
    createElement(tag) { const e = mkEl(tag); e.tag = tag; return e; },
    createTextNode(t) { return { textContent: t, children: [] }; },
  };
  const ewClearItems = () => {
    calls.push("ewClearItems");
    state.extraItems = state.extraItems.filter(
      it => !/^(Site Cut|Site Fill|Strip Topsoil|Remove |Rough Place |Place |Export |Import Structural$)/.test(it.label));
  };
  const $ = (id) => {
    if (!(id in els)) throw new Error("no #" + id + " in index.html");
    return els[id];
  };
  f($, state, doc, ewClearItems,
    (j) => calls.push("renderEwBasis:" + JSON.stringify(j)),
    (j) => calls.push("renderEwBalance:" + JSON.stringify(j)),
    () => calls.push("renderEwTake"),
    () => calls.push("renderItemized"),
    (t, cls) => said.push({ t, cls }),
    () => calls.push("draw"),
    // the cut/fill map's own refusal slot (tests/test_ew_map_note.js owns
    // its content); a refusal must clear it along with the map itself
    (d, s) => calls.push("ewMapNote:" + JSON.stringify([d, s])))(detail, 400);
  return { els, state, calls, said };
}

// ---------------------------------------------------------------------------
console.log("1. the structured refusal renders as words, not [object Object]");
// ---------------------------------------------------------------------------
const DETAIL = {
  message: "the existing surface carries only ONE elevation (324 ft) across "
    + "1 contour and 0 spot grades.",
  code: "surface_no_relief",
  surface: { name: "existing", n_contours: 1, n_spots: 0,
             distinct_elevations: 1, z_min_ft: 324, z_max_ft: 324 },
  basis: {
    existing: { name: "existing", n_contours: 1, n_spots: 0,
                distinct_elevations: 1, z_min_ft: 324, z_max_ft: 324 },
    proposed: { name: "proposed", n_contours: 1, n_spots: 0,
                distinct_elevations: 1, z_min_ft: 826, z_max_ft: 826 },
    ranges_overlap: false, range_gap_ft: 502,
  },
};
{
  const r = run(DETAIL);
  const txt = r.els.ewResult.textContent;
  check(!/\[object Object\]/.test(txt),
    "the object detail is never stringified into the panel");
  check(/No volume published/.test(txt), "it leads with the abstain");
  check(/only ONE elevation \(324 ft\)/.test(txt),
    "it carries the server's own sentence verbatim");
  check(r.els.ewResult.classList.contains("ew-refused"),
    "the result block is marked .ew-refused");
  const html = r.els.ewResult.children.map(c => c.innerHTML || "").join(" ");
  check(/<b>existing<\/b>/.test(html) && /<b>proposed<\/b>/.test(html),
    "BOTH surfaces are named, not only the one that tripped the guard");
  check(/324/.test(html) && /826/.test(html),
    "...with what each one actually held");
  eq(r.said.length, 1, "the status bar says it once");
  eq((r.said[0] || {}).cls, "err", "...as an error");
}

// ---------------------------------------------------------------------------
console.log("2. nothing from the previous calculation survives");
// ---------------------------------------------------------------------------
{
  const r = run(DETAIL, { added: true });
  eq(r.state.ew.result, null, "state.ew.result is cleared");
  eq(r.state.ew.added, false, "the takeoff consent is withdrawn");
  eq(r.els.ewLayerOut.innerHTML, "", "the per-layer slabs are cleared");
  check(r.calls.includes("renderEwBasis:null"),
    "the basis block is re-rendered with no result");
  check(r.calls.includes("renderEwBalance:null"),
    "the material balance is re-rendered with no result");
  check(r.calls.includes("renderItemized"), "the itemised takeoff re-renders");
  eq(r.state.extraItems.length, 1,
    "the earthwork lines are OUT of the priced takeoff");
  eq(r.state.extraItems[0].label, "Concrete",
    "...and nothing else was touched");
  check(/removed from the takeoff/.test(r.els.ewResult.textContent),
    "and the removal is stated, not silent");
}
{
  const r = run(DETAIL, { added: false });
  check(!/removed from the takeoff/.test(r.els.ewResult.textContent),
    "nothing is claimed removed when nothing had been added");
}

// ---------------------------------------------------------------------------
console.log("3. a plain-string 400 still renders (the route's other errors)");
// ---------------------------------------------------------------------------
{
  const r = run("no scale on this page - type one into the scale box");
  // Capitalised: the engine writes these to sit mid-sentence in a log line,
  // and here one FOLLOWS a full stop, so it opens a sentence (seen on
  // :8100 2026-09-02: "No volume published. the proposed surface ...").
  check(/No scale on this page/.test(r.els.ewResult.textContent),
    "a string detail is shown, opening a sentence after the headline");
  check(/No volume published/.test(r.els.ewResult.textContent),
    "...under the same headline");
}
{
  const r = run(undefined);
  check(/ailed \(400\)/.test(r.els.ewResult.textContent),
    "a body with no detail at all still says something");
}

// ---------------------------------------------------------------------------
console.log("4. the caller hands the object over, and clears the class again");
// ---------------------------------------------------------------------------
const calc = SRC.slice(SRC.indexOf('$("ewCalc").onclick'),
                       SRC.indexOf("// AN ABSTAIN MUST LEAVE NOTHING BEHIND"));
check(/ewRefused\(e\.detail, r\.status\)/.test(calc),
  "the !r.ok branch calls ewRefused with the whole detail");
check(!/\$\("ewResult"\)\.textContent = e\.detail/.test(calc),
  "the old one-line handler is gone");
check(/classList\.remove\("ew-refused"\)/.test(calc),
  "a successful Calculate takes the refusal styling back off");

// ---------------------------------------------------------------------------
console.log("5. suggestions_dropped finally reaches a screen");
// ---------------------------------------------------------------------------
check(/id="ewDropped"/.test(HTML), "index.html has the #ewDropped slot");
check(/\.ew-dropped\s*\{/.test(CSS), "style.css styles it");
const HAS_DROP = /function renderEwDropped\(/.test(SRC);
check(HAS_DROP, "webui/app.js has a renderEwDropped() at all");
const drop = HAS_DROP ? fnSrc("renderEwDropped")
  : "function renderEwDropped() { /* 4010783: no screen renders suggestions_dropped */ }";
check(/state\.ew && state\.ew\.dropped/.test(drop),
  "renderEwDropped reads the count off the panel state");
check(/lad\.verdict\.why/.test(drop),
  "...and prints the ladder verdict's own sentence when that is the reason");
check(/renderEwDropped\(\);/.test(fnSrc("renderEw")),
  "renderEw calls it, so it repaints with the rows");
check(/dropped: j\.suggestions_dropped \|\| 0/.test(SRC),
  "the suggest payload's count is stored on state.ew");
check(/ladder: j\.ocr_ladder \|\| null/.test(SRC),
  "so is the ladder verdict");

{
  // executed, on the live shape the route returns for the crafted P2 body
  const els = { ewDropped: mkEl("ewDropped") };
  const state = { ew: { dropped: 3, ladder: {
    applied: true, dropped: 3, kept: 0,
    verdict: { laddered: false, testable: true, interval_ft: 116,
               repeats: 1, n_gaps: 2, share: 0.5,
               why: "these 3 elevations do not step like contours: the "
                  + "commonest gap between adjacent values is 116 ft and it "
                  + "occurs 1 time in 2 (50%)." } } } };
  new Function("$", "state", "esc", drop + "\nreturn renderEwDropped;")(
    (id) => els[id], state, (s) => String(s))();
  const box = els.ewDropped;
  check(box.hidden === false, "3 dropped rows: the block is shown");
  check(/3 candidate rows/.test(box.innerHTML),
    "it says how many were read and thrown away");
  check(/do not step like contours/.test(box.innerHTML),
    "it says WHY, in the reader's own words");
  check(/Add contour/.test(box.innerHTML),
    "and how to disagree with it");
}
{
  const els = { ewDropped: mkEl("ewDropped") };
  new Function("$", "state", "esc", drop + "\nreturn renderEwDropped;")(
    (id) => els[id], { ew: { dropped: 0, ladder: null } }, (s) => String(s))();
  check(els.ewDropped.hidden === true,
    "nothing dropped: the block stays out of the way");
}
{
  // SPOT GRADES REFUSED AS LINE HEIGHTS (2026-09-17). A number the sheet
  // tags BW / TW / FG is a point on what is being built, and twelve
  // contour lines on Oncor C-04 were taking one as their height. The
  // refusal is disclosed here, in the same block as the other drops -
  // with nothing else dropped and no read cut short, this line alone has
  // to open the block.
  const els = { ewDropped: mkEl("ewDropped") };
  new Function("$", "state", "esc", drop + "\nreturn renderEwDropped;")(
    (id) => els[id],
    { ew: { dropped: 0, ladder: null, ocr: { stopped_early: false },
            spotRefused: { n: 17, tags: { BW: 6, EW: 5, TW: 6 } } } },
    (s) => String(s))();
  const h = els.ewDropped.innerHTML;
  check(els.ewDropped.hidden === false && /17 numbers beside these lines are spot grades/.test(h),
    "17 refused spot grades: the block opens and says so  (" + h.slice(0, 120) + ")");
  check(/BW, EW, TW/.test(h) && /not a contour height/.test(h),
    "...naming the sheet's own tags and why they are not line heights");
  check(/No line took its height from them/.test(h),
    "...and that nothing used them");
}

// ---------------------------------------------------------------------------
console.log("6. the cut/fill map comes off the drawing with the number");
// ---------------------------------------------------------------------------
// THE DEFECT (live on the DEM outline sheet, e6ac867): ewRefused cleared the
// result, the layer slabs, the basis block, the balance and the takeoff -
// and never touched state.ew.map. draw() paints state.ew.map.img under the
// linework whenever it is set, so the panel said "No volume published" while
// a full-sheet colour depth map and a ramp reading "cut −3.4 ft … +3.4 ft
// fill" stayed on the sheet. A depth map is a quantity drawn instead of
// typed.
{
  const r = run(DETAIL);
  eq(r.state.ew.map, null,
    "state.ew.map is dropped, so draw() has no depth map left to paint");
  eq(r.els.ewMapLegend.hidden, true,
    "the depth ramp legend is hidden with it");
  check(r.calls.includes("draw"),
    "and draw() is called, so the sheet repaints without the map "
    + "(clearing the state alone leaves the last frame on screen)");
  eq(r.els.ewMap.checked, true,
    "the map TOGGLE stays ticked - it is the operator's standing request, "
    + "and ewCalc refetches on the next Calculate that succeeds");
  check(r.calls.includes("ewMapNote:[null,null]"),
    "and any earlier map refusal note is cleared with it");
}
// the same guarantee stated against the source, so a later edit that drops
// the call is caught even if the harness is rewritten. HAS_NEW guards the
// read for the same reason OLD_HANDLER exists: on 4010783 there is no
// ewRefused to slice, and this file must fail on behaviour, not on a stack
// trace.
const REF = HAS_NEW ? fnSrc("ewRefused") : OLD_HANDLER;
check(/state\.ew\.map = null/.test(REF),
  "ewRefused clears state.ew.map by name");
check(/draw\(\);/.test(REF),
  "ewRefused repaints the canvas");

console.log(fails ? "\n" + fails + " FAILED" : "\nall checks passed");
process.exit(fails ? 1 : 0);
