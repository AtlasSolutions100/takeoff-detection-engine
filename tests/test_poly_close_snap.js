// SNAP THE LAST CLICK OF A HAND-DRAWN POLYGON BACK TO ITS FIRST CORNER.
//
// From the 2026-09-02 screen share. Drawing the limit of work the owner's
// business partner ended up with FIVE points where he wanted four: "if you
// could have a thing where it auto snaps when you're near, that'd be good."
//
// REPRODUCED LIVE ON 512f474 before this change, C3.0-Site-Plan-Rev.1.pdf at
// 22% zoom: four corners, then a fifth click 8 px from the first. The draft
// went to 5 corners and the running area slid 35,958 SF -> 35,598 SF. Nothing
// warned; a corner 8 px from the start is a corner, and the tool had no way
// to know it was meant as a close.
//
// THREE POLYGONS, ONE RULE. The operator draws three by hand and they had
// drifted into three near-identical click handlers:
//     ew-bound-draw   the limit of work
//     gz-draw         a grading zone
//     st-draw         a structure outline
// This suite pins that all three ask the SAME predicate, so "near enough to
// close" can never mean three different things.
//
// What is pinned:
//   1. GEOMETRY - the radius test to hand arithmetic, exactly on the boundary,
//      and the >= 3 rule (closing on two corners makes a line, not a polygon).
//   2. THE REPORTED CASE - the 4-corner draft and a click 8 px away, at the
//      zoom it happened at, closes instead of adding a fifth corner.
//   3. SCREEN-SPACE TOLERANCE - exactly 14 px at 22%, 100% and 400%, because
//      a radius fixed in page points is unusable at one end; and that it is
//      deliberately NOT the line snap's budget, whose flat 1.5 pt pad becomes
//      24.8 px of grab at 400%.
//   4. WIRING - all three click handlers test before they push, all three
//      render the preview, and every arm/finish/cancel/Esc path clears it.
//
// webui/app.js is not a module and touches the DOM at load, so this evals
// ONLY the marked blocks - POLY-SNAP, plus SNAP-PURE for the shipped
// snapTolFrom it is measured against (injected, not re-implemented).
//
// Dependency-free:  node tests/test_poly_close_snap.js

"use strict";
const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(
  path.join(__dirname, "..", "webui", "app.js"), "utf8");
const cssSrc = fs.readFileSync(
  path.join(__dirname, "..", "webui", "style.css"), "utf8");

let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); }
  else console.log("ok:", msg);
}

function block(name) {
  const a = src.indexOf(`// ${name}-BEGIN`);
  const b = src.indexOf(`// ${name}-END`);
  if (a < 0 || b <= a) {
    console.error(`FAIL: ${name} markers not found in webui/app.js`);
    process.exit(1);
  }
  return src.slice(a, b);
}

const S = new Function(block("SNAP-PURE") + block("POLY-SNAP") +
  " return { polyCloseHit, polyCloseTolFrom, POLY_CLOSE_PX, snapTolFrom };")();

const IMG_DPI = 130;          // app.js:37, "must match /image endpoint default"
const tolAt = (k) => S.polyCloseTolFrom(k, IMG_DPI);

// =========================================================================
// 1. GEOMETRY
// =========================================================================
{
  const sq = [[0, 0], [100, 0], [100, 100], [0, 100]];
  check(S.polyCloseHit(sq, 0, 0, 5) === true, "dead on the first corner closes");
  check(S.polyCloseHit(sq, 3, 4, 5) === true,
    "3-4-5 triangle: exactly ON the radius closes (<=, not <)");
  check(S.polyCloseHit(sq, 3, 4, 4.999) === false,
    "a hair outside the radius does not");
  check(S.polyCloseHit(sq, -3, -4, 5) === true, "the radius is a circle, not a box");
  check(S.polyCloseHit(sq, 4, 4, 5) === false,
    "the corner of the square that circumscribes it does NOT close (5.66 > 5)");
  // it is the FIRST corner, never the last: the last is where the rubber band
  // already starts, and closing onto it would just make a duplicate vertex
  check(S.polyCloseHit(sq, 0, 100, 5) === false,
    "being on the LAST corner is not a close");
  check(S.polyCloseHit(sq, 100, 0, 5) === false,
    "nor is being on any other corner");
}

// =========================================================================
// 2. THE >= 3 RULE - a two-corner draft is still being drawn
// =========================================================================
{
  check(S.polyCloseHit([[0, 0]], 0, 0, 50) === false,
    "one corner: nothing to close, even sitting on it");
  check(S.polyCloseHit([[0, 0], [10, 0]], 0, 0, 50) === false,
    "two corners: a close would make a LINE - the click stays a corner");
  check(S.polyCloseHit([[0, 0], [10, 0], [10, 10]], 0, 0, 50) === true,
    "three corners: now a close makes a polygon, so it is offered");
  check(S.polyCloseHit([], 0, 0, 50) === false, "empty draft");
  check(S.polyCloseHit(null, 0, 0, 50) === false, "no draft at all");
  check(S.polyCloseHit(undefined, 0, 0, 50) === false, "undefined draft");
}

// =========================================================================
// 3. THE REPORTED CASE, at the zoom it was reported at
// =========================================================================
{
  // Live repro on 512f474: C3.0 at 22% zoom (state.view.k = 0.2199 measured),
  // a rectangle drawn at 300/600 x 200/450 screen px, then a click 6 px right
  // and 5 px down from the first corner - 7.8 px away.
  const k = 0.2199;
  const tol = tolAt(k);
  const perPagePt = k * (IMG_DPI / 72);      // screen px per page pt
  // the draft in PAGE POINTS, as the app stores it
  const d = [[0, 0], [300 / perPagePt, 0],
             [300 / perPagePt, 250 / perPagePt], [0, 250 / perPagePt]];
  const off = [6 / perPagePt, 5 / perPagePt];  // the click, 7.81 screen px out
  check(Math.round(Math.hypot(6, 5) * 100) / 100 === 7.81,
    "the reported click is 7.81 screen px from the first corner");
  check(S.polyCloseHit(d, off[0], off[1], tol) === true,
    "...and it CLOSES the four-corner draft instead of making a fifth corner");
  // and the near miss that must still place a corner: a genuine corner 40 px
  // from the start (a narrow notch back toward the origin) is not stolen
  const far = [40 / perPagePt, 0];
  check(S.polyCloseHit(d, far[0], far[1], tol) === false,
    "a deliberate corner 40 px from the start is still a corner");
}

// =========================================================================
// 4. THE SAME PIXEL BUDGET AT EVERY ZOOM
// =========================================================================
{
  // A radius fixed in page points is a different number of pixels at 22% and
  // at 400%: unusable at one end. Measured here at the three zooms the app
  // actually gets driven at.
  for (const k of [0.22, 1, 4]) {
    const tol = tolAt(k);
    const perPagePt = k * (IMG_DPI / 72);
    const px = tol * perPagePt;
    check(Math.abs(px - S.POLY_CLOSE_PX) < 1e-9,
      `zoom ${k}x: the reach is exactly ${S.POLY_CLOSE_PX} screen px (got ${px.toFixed(2)})`);
    // and it is NOT snapTolFrom's, whose flat 1.5 pt pad becomes 24.8 px of
    // grab at 400% - the zoom at which a greedy close is worst
    const shipped = S.snapTolFrom(S.POLY_CLOSE_PX, k, IMG_DPI) * perPagePt;
    check(shipped > px,
      `zoom ${k}x: the line-snap budget would have been ${shipped.toFixed(1)} px`);
  }
  // the page-point radius really does move with the zoom
  check(Math.abs(tolAt(0.22) / tolAt(4) - 4 / 0.22) < 1e-9,
    "the page-point radius scales exactly inversely with the zoom");
}

// =========================================================================
// 5. WIRING - all three polygons, one predicate, and the preview
// =========================================================================
// every hand-drawn polygon's click handler tests BEFORE it pushes a corner
for (const [fn, finish] of [["stDrawClick", "stFinishDraw"]]) {
  const i = src.indexOf(`function ${fn}(`);
  check(i >= 0, `${fn} exists`);
  const body = src.slice(i, src.indexOf("\n}", i));
  const iHit = body.indexOf("polyCloseHit(");
  const iPush = Math.max(body.indexOf(".push(["), body.indexOf("drawPts.push"));
  check(iHit >= 0, `${fn} asks polyCloseHit`);
  check(iPush >= 0 && iHit < iPush,
    `${fn} asks it BEFORE pushing a corner (or the corner lands anyway)`);
  check(body.includes(`return ${finish}()`),
    `${fn} closes through ${finish}, the same path Enter uses`);
  check(body.includes("polyCloseTolPt()"),
    `${fn} uses the shared screen-px tolerance, not a local number`);
}
// one draft lookup covers all three tools
{
  const i = src.indexOf("function polyActiveDraft()");
  check(i >= 0, "polyActiveDraft() exists");
  const body = src.slice(i, src.indexOf("\n}", i));
  for (const t of ["st-draw"])
    check(body.includes(`"${t}"`), `polyActiveDraft knows about ${t}`);
}
// ...and the preview dies at the state.tool CHOKEPOINT, not at each of the
// ~20 places that can take a tool away. That is the lesson syncCursor's own
// comment already records; the close-snap had to learn it too.
{
  check(/const POLY_DRAW_TOOLS = new Set\(\["st-draw", "fl-draw"\]\)/
    .test(src), "POLY_DRAW_TOOLS names the two polygon tools (structure, flooring), once");
  const i = src.indexOf("function syncCursor()");
  const body = src.slice(i, src.indexOf("\n}", i));
  check(/POLY_DRAW_TOOLS\.has\(state\.tool\)/.test(body),
    "syncCursor drops the preview whenever the armed tool is not one of them");
  check(/state\.polySnap = false/.test(body) && /remove\("snap-close"\)/.test(body),
    "...clearing both the state and the cursor class");
}
// pageReset() clears it as a PLAIN STATE WRITE, not through polySnapClear:
// tests/test_page_reset.js evaluates the reset in isolation, and a page reset
// must not depend on the draw layer being loaded
{
  const i = src.indexOf("function pageReset()");
  const body = src.slice(i, src.indexOf("\n}\n", i));
  check(/state\.polySnap = false/.test(body), "pageReset clears the preview");
  check(!/polySnapClear\(\)/.test(body), "...without calling into the draw layer");
}
// the preview is armed on hover for all three
{
  const n = (src.match(/polySnapHover\(/g) || []).length;
  check(n >= 2, `polySnapHover is called from the structure draw's mousemove handler (${n} refs incl. its definition)`);
  for (const h of ["state.st.hover"]) {
    const i = src.indexOf(h + " = ptFromEvent(e);");
    check(i >= 0, `${h} is set on mousemove`);
    check(src.slice(i, i + 200).includes("polySnapHover("),
      `...and that handler arms the close preview`);
  }
}
// ...and the preview is what the canvas draws: the band goes to the FIRST
// corner, and the ring marks it, in every one of the three
{
  check((src.match(/drawPolyCloseRing\(/g) || []).length >= 2,
    "drawPolyCloseRing is drawn for the structure draft");
  for (const first of ["P[0]"])
    check(src.includes(`drawPolyCloseRing(${first},`),
      `the ring lands on ${first}, the corner that will be closed onto`);
  // the rubber band retargets, which is what makes the snap PREDICTABLE
  // (the limit-of-work and grading-zone drafts left with the earthwork calculator, 2026-09-25)
  check(/if \(st\.hover && !state\.polySnap\) ctx\.lineTo/.test(src),
    "structure outline: the free-hover leg stands down while snapping");
}
// the preview never survives the tool that owns it
{
  const n = (src.match(/polySnapClear\(\)/g) || []).length;
  check(n >= 4, `polySnapClear is called on every arm/finish/cancel/Esc path (${n})`);
  for (const fn of ["stFinishDraw", "stDisarm"]) {
    const i = src.indexOf(`function ${fn}(`);
    check(i >= 0 && src.slice(i, src.indexOf("\n}", i)).includes("polySnapClear()"),
      `${fn} clears the preview`);
  }
  // an Esc that pops a corner can take the draft below 3, where nothing closes
  check((src.match(/\.pop\(\);\s*\n\s*polySnapClear\(\)/g) || []).length >= 1,
    "the Esc backup clears the preview after popping a corner");
}
// the operator is TOLD the close exists, in all three tools, from one string
{
  check(/const POLY_CLOSE_HINT = /.test(src), "POLY_CLOSE_HINT is one string");
  const i = src.indexOf("const POLY_CLOSE_HINT = ");
  const line = src.slice(i, src.indexOf(";", i));
  check(/click the first corner/.test(line),
    "...and it says clicking the first corner closes the shape");
  check((src.match(/POLY_CLOSE_HINT/g) || []).length >= 3,
    "...and every arming and per-corner message uses it");
  check(!/Enter\/double-click closes, Esc backs up/.test(src),
    "no tool is left telling the operator the old, incomplete story");
}
// the coarse tell
check(/canvas#view\.snap-close\s*{\s*cursor:\s*pointer/.test(cssSrc),
  "the cursor changes while the next click would close");
{
  const iSnap = cssSrc.indexOf("canvas#view.snap-close");
  const iPoint = cssSrc.indexOf("canvas#view.tool-point");
  const iPan = cssSrc.indexOf("canvas#view.panning");
  check(iPoint >= 0 && iSnap > iPoint,
    "...it wins over the armed-tool crosshair (same specificity, later wins)");
  check(iPan > iSnap, "...and panning still wins over it");
}

console.log(failures ? `\n${failures} FAILED` : "\nall close-snap checks passed");
process.exit(failures ? 1 : 0);
