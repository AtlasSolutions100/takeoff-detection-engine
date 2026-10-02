// THE OVERLAY SHOWS THE OTHER SHEET'S TOPOS (2026-09-28, Koby: "when we overlay ... since we also show the sheet it
// only makes sense to also include that sheet's topos"). The Overlay placed another sheet's IMAGE only; its
// contour lines were read and loaded but never drawn. Now they are drawn with it - placed by the engine's own
// page_transform (GET /transforms, so right after Align, no compute needed) - existing / proposed / no height, a
// line erased on that sheet or a pen that is not a contour left out.
//
// The critic's round (out/verify/2026-09-28/overlay-lines/CRITIC-ghost.md):
//   W1  the lines drawn or edited on that sheet are drawn too (an edited line had vanished)
//   W2  both ends of the placement from one frame, fetched again whenever the sheet shown can move
//   R2  the Overlay's lines look like the other sheet's (dashed, thinner, fainter, heights tagged with the sheet),
//       and stay off while the pick or edit tool is on
//   R3  mapped once per sheet / read / project / placement, culled to the view, points within 1.5 px skipped
//   R4  one ask for the overlaid sheet's read, not one per frame
//   M2  an older answer landing late is dropped; a failed ask falls back and says so
//
// run: node tests/test_cutfill_ghost_lines.js
"use strict";
const fs = require("fs");
const path = require("path");
const ejs = fs.readFileSync(path.join(__dirname, "..", "webui", "cutfill", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "webui", "cutfill", "index.html"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(src, name) {
  let start = src.indexOf("function " + name + "(");
  if (start < 0) return "";
  if (src.slice(start - 6, start) === "async ") start -= 6;
  let depth = 0;
  for (let j = src.indexOf("{", start); j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}" && --depth === 0) return src.slice(start, j + 1);
  }
  return "";
}
const NAMES = ["applyM", "invM", "mulM", "rectsOverlap", "famRole", "pageSettings", "pageEdits", "hiddenChains",
  "indexAnalysis", "ghostLinePieces", "ghostPiecesCached", "placeMatrix", "drawGhostLines", "refreshTransforms"];
const helpers = NAMES.map((n) => fnSrc(ejs, n)).join("\n");
check(NAMES.every((n) => fnSrc(ejs, n)), "every function under test is found in app.js");
function mk(env) {
  const f = new Function("state", "C", "I3", "pageMatrix", "displayKey", "ctx", "Path2D", "visRect", "sheetName", "fmtZ",
    "haloText", "GET", "setStatus", "renderAlign", "requestDraw", "renderZones",
    helpers + "\nreturn { ghostLinePieces, ghostPiecesCached, placeMatrix, indexAnalysis, applyM, drawGhostLines, refreshTransforms };");
  return f(env.state, C, I3, env.pageMatrix || (() => I3), env.displayKey || (() => "d0p0"), env.ctx || null, env.Path2D || null,
    env.visRect || (() => [-1e9, -1e9, 1e9, 1e9]), env.sheetName || ((k) => (k === "d0p1" ? "3.1" : "3.0")),
    (v) => String(Math.round(v * 100) / 100), env.haloText || (() => {}), env.GET || (async () => null),
    env.setStatus || (() => {}), () => {}, () => {}, env.renderZones || (() => {}));
}
const C = { ex: "EX", pr: "PR", noz: "NOZ", ignore: "IGN" };
const I3 = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
const T = [[1, 0, 32], [0, 1, 1096], [0, 0, 1]];                  // the Rose slide

// ---- 1. which of the overlaid sheet's read lines are drawn, and where --------------------------------------
const state = { project: { pages: { d0p1: { family_roles: { "3": "existing" }, edits: { lines: {}, deleted: [44] } } } } };
const f = mk({ state });
const an = f.indexAnalysis({
  families: [{ style: 1, role: "existing" }, { style: 2, role: "proposed" }, { style: 3, role: "ignore" }, { style: 4, role: "ignore" }],
  chains: [{ id: 10, pts: [[0, 0], [10, 0]] }, { id: 20, pts: [[0, 5], [10, 5]] }, { id: 30, pts: [[0, 9], [10, 9]] },
           { id: 40, pts: [[0, 12], [10, 12]] }, { id: 44, pts: [[0, 14], [10, 14]] }, { id: 50, pts: [[0, 20], [10, 20]] },
           { id: 60, pts: [[500, 500], [510, 500]] }],
  contours: [{ id: 1, style: 1, z: 576, chain_ids: [10] },        // existing, with a height
             { id: 2, style: 2, z: null, chain_ids: [20] },       // proposed, no height yet
             { id: 3, style: 3, z: 577, chain_ids: [30, 44] },    // "ignore" by the engine, existing by the operator; 44 erased
             { id: 4, style: 4, z: 578, chain_ids: [40] },        // a pen that is not a contour
             { id: 5, style: 1, z: 575, chain_ids: [50], how: "legend" },   // a legend sample
             { id: 6, style: 2, z: 579, chain_ids: [60] }],       // far away (drawGhostLines culls it, section 3)
});
const pcs = f.ghostLinePieces("d0p1", an, T);
const byId = Object.fromEntries(pcs.map((p) => [p.id, p]));
check(pcs.length === 4 && byId.c1 && byId.c2 && byId.c3 && byId.c6 && !byId.c4 && !byId.c5,
  "drawn: existing, proposed and the operator's existing pick; never a non-contour pen or a legend sample: " + pcs.map((p) => p.id).join(","));
check(byId.c1.color === "EX" && byId.c2.color === "NOZ" && byId.c3.color === "EX" && byId.c6.color === "PR" && byId.c2.z === null,
  "colours as on the sheet itself: green existing, orange proposed, pink for no height (line 2 is proposed but has no height)");
check(pcs.every((p) => p.pts.length === 2) && byId.c1.pts[0][0] === 32 && byId.c1.pts[0][1] === 1096
  && JSON.stringify(byId.c1.box) === "[32,1096,42,1096]" && byId.c3.pts[0][1] === 1105,
  "each piece is placed by T (+32 / +1096) with its box in this sheet's points, and the erased piece of line 3 (chain 44) is left out");

// ---- 1b. W1: the lines drawn or edited on that sheet ------------------------------------------------------
const st1b = { project: { pages: { d0p1: { edits: { deleted: [], lines: {
  L1: { pts: [[100, 100], [140, 100]], z: 580, role: "existing", source: "drawn" },               // drawn
  L2: { pts: [[0, 0], [5, 1], [10, 0]], z: 576.5, role: "proposed", chain: 10, source: "edit" },  // an edit of chain 10
  L3: { pts: [[0, 40], [10, 40]], z: null, role: "proposed", source: "drawn" },                     // drawn, no height yet
  L4: { pts: [[0, 50]], z: 570, role: "existing" } } } } } } };                                       // one point: not a line
const f1b = mk({ state: st1b });
const an1b = f1b.indexAnalysis({
  families: [{ style: 1, role: "existing" }, { style: -1, role: "existing" }],
  chains: [{ id: 10, pts: [[0, 0], [10, 0]] }, { id: 10000001, user: "L1", pts: [[100, 100], [140, 100]] }],
  contours: [{ id: 1, style: 1, z: 576, chain_ids: [10] }, { id: 7, style: -1, z: 580, chain_ids: [10000001], user: "L1" }],
});
const p1b = f1b.ghostLinePieces("d0p1", an1b, T);
const b1b = Object.fromEntries(p1b.map((p) => [p.id, p]));
check(p1b.length === 3 && b1b.uL1 && b1b.uL2 && b1b.uL3 && !b1b.c1 && !b1b.c7,
  "the drawn line and the edited line are drawn (from the edits, like that sheet's own view); the edited line's read piece and the read copy of the drawn line are not: " + p1b.map((p) => p.id).join(","));
check(b1b.uL1.color === "EX" && b1b.uL1.z === 580 && b1b.uL2.color === "PR" && b1b.uL2.z === 576.5 && b1b.uL2.pts.length === 3
  && b1b.uL3.color === "NOZ" && b1b.uL1.pts[0][0] === 132 && b1b.uL1.pts[0][1] === 1196,
  "their colours and heights are the operator's (existing 580, proposed 576.5 with its moved point, pink for no height), placed by T");

// ---- 2. the placement: both ends from one frame -------------------------------------------------------------
const last = () => [[1, 0, 7], [0, 1, 7], [0, 0, 1]];
const g = mk({ state: { xf: { display: "d0p0", transforms: { d0p0: { matrix: I3 }, d0p1: { matrix: T } } } }, pageMatrix: last });
check(g.placeMatrix("d0p1")[0][2] === 32 && g.placeMatrix("d0p0")[0][2] === 0,
  "right after Align the Overlay uses the engine's placement of the sheet (and the sheet shown is itself)");
const h = mk({ state: { xf: { display: "d0p1", transforms: { d0p1: { matrix: I3 } } } }, pageMatrix: last, displayKey: () => "d0p0" });
check(h.placeMatrix("d0p1")[0][2] === 0 && h.placeMatrix("d0p0")[0][2] === 0,
  "the engine's frame is used whatever the settings say is shown (critic W2 / R5), and a sheet with no entry sits unmoved in it, never on the last compute's placement");
const k0 = mk({ state: { xf: null }, pageMatrix: () => [[1, 0, 9], [0, 1, 9], [0, 0, 1]] });
const k1 = mk({ state: { xf: { display: null, transforms: {} } }, pageMatrix: () => [[1, 0, 9], [0, 1, 9], [0, 0, 1]] });
check(k0.placeMatrix("d0p1")[0][2] === 9 && k1.placeMatrix("d0p1")[0][2] === 9,
  "no placement fetched yet (or no sheet takes part): the last compute's, both ends");

// ---- 3. how they are drawn: R2 look, R3 culling and point skipping --------------------------------------------
function fakeCanvas() {
  const log = { strokes: [], dash: [], texts: [] };
  class P2 { constructor() { this.m = 0; this.l = 0; } moveTo() { this.m++; } lineTo() { this.l++; } }
  const ctx = {
    save() {}, restore() {}, setLineDash(d) { log.dash.push(d.slice()); },
    stroke(p) { log.strokes.push({ color: this.strokeStyle, width: this.lineWidth, alpha: this.globalAlpha, dash: log.dash[log.dash.length - 1], moves: p.m, lines: p.l }); },
  };
  return { ctx, P2, log };
}
const fc = fakeCanvas();
const dense = [];
for (let i = 0; i <= 100; i++) dense.push([i * 0.1, 30]);          // 101 points 0.1 pt apart: 10 pt long
const st3 = { view: { k: 1, x: 0, y: 0 }, project: { pages: { d0p1: { family_roles: {}, edits: { lines: {}, deleted: [] } } } } };
const f3 = mk({ state: st3, ctx: fc.ctx, Path2D: fc.P2, visRect: () => [0, 0, 400, 1200],
  haloText: (t, x, y, col, font) => fc.log.texts.push({ t, col, font }) });
const an3 = f3.indexAnalysis({
  families: [{ style: 1, role: "existing" }],
  chains: [{ id: 1, pts: dense }, { id: 2, pts: [[3000, 3000], [3010, 3000]] }],
  contours: [{ id: 1, style: 1, z: 576, chain_ids: [1] }, { id: 2, style: 1, z: 575, chain_ids: [2] }],
});
f3.drawGhostLines("d0p1", an3, T);
const s0 = fc.log.strokes[0] || {};
check(fc.log.strokes.length === 1 && s0.color === "EX" && s0.moves === 1,
  "only the piece in view is stroked (the one 3,000 pt away is culled): " + JSON.stringify(fc.log.strokes));
check(s0.lines === 7, `a point within 1.5 px of the last one kept is skipped, the end point kept: 100 segments drawn as ${s0.lines}`);
check(JSON.stringify(s0.dash) === "[16,6]" && s0.width === 1.1 && s0.alpha === 0.7,
  "the Overlay's lines are long-dashed, thinner (1.1 px against the sheet's own 1.5) and fainter (0.7), whatever their colour");
st3.view.k = 2;                                                   // k * 72 >= 90: heights show
fc.log.strokes.length = 0; fc.log.texts.length = 0;
f3.drawGhostLines("d0p1", an3, T);
check(fc.log.texts.length === 1 && fc.log.texts[0].t === "576 · 3.1" && /italic/.test(fc.log.texts[0].font) && !/bold/.test(fc.log.texts[0].font),
  "zoomed in, a height is tagged with the sheet it comes from, in italic, not the sheet's own bold: " + JSON.stringify(fc.log.texts));
// at k 2 the 1.5 px is 0.75 pt: every 8th point of 0.1 pt is kept (0.8 pt on), 12 of them, and the end point
check((fc.log.strokes[0] || {}).lines === 13, "zoomed in twice as far, about twice as many points are kept (the 1.5 px is on screen): " + (fc.log.strokes[0] || {}).lines);

// ---- 4. R3: mapped once, again only when something it rests on changes -------------------------------------------
const st4 = { project: { pages: { d0p1: {} } }, ghostCache: null };
const f4 = mk({ state: st4 });
const an4 = f4.indexAnalysis({ families: [{ style: 1, role: "existing" }], chains: [{ id: 1, pts: [[0, 0], [5, 0]] }],
  contours: [{ id: 1, style: 1, z: 1, chain_ids: [1] }] });
const a1 = f4.ghostPiecesCached("d0p1", an4, T);
const a2 = f4.ghostPiecesCached("d0p1", an4, T.map((r) => r.slice()));
st4.project = { pages: { d0p1: {} } };
const a3 = f4.ghostPiecesCached("d0p1", an4, T);
const a4 = f4.ghostPiecesCached("d0p1", an4, I3);
check(a1 === a2 && a3 !== a2 && a4 !== a3 && a4[0].pts[0][0] === 0,
  "the same sheet, read, project and placement reuse the mapped pieces; a new project (roles, edits) or placement maps again");

// ---- 5. M2: the latest ask wins; a failed ask falls back and says so ---------------------------------------------
(async () => {
  const st5 = { pid: "p", xfSeq: 0, xf: null, ghost: { key: "d0p1" } };
  let release; const slow = new Promise((r) => { release = r; });
  let n = 0;
  const f5 = mk({ state: st5, GET: async () => (++n === 1 ? slow : { display: "d0p1", transforms: { d0p1: { matrix: I3 } } }) });
  const first = f5.refreshTransforms(); const second = f5.refreshTransforms();
  await second;
  release({ display: "d0p0", transforms: { d0p0: { matrix: I3 } } });
  await first;
  check(st5.xf && st5.xf.display === "d0p1", "an older answer landing after a newer one is dropped: " + JSON.stringify(st5.xf));
  const said = []; let zonesDrawn = 0;
  const f6 = mk({ state: st5, GET: async () => { throw new Error("500"); }, setStatus: (m, o) => said.push([m, o]),
    renderZones: () => { zonesDrawn++; } });
  await f6.refreshTransforms();
  check(st5.xf === null && !said.length && /Could not check where the sheets sit now \(500\): the Overlay draws them unmoved\./.test(st5.xfError),
    "a failed ask drops the fetched placement and says so in the Sheet alignment card, not over the status line (a refused compute's words stay); with no compute, 'unmoved': " + st5.xfError);
  check(zonesDrawn === 1, "the zones card is drawn again when the sheet shown changes (here: it became unknown)");
  st5.result = { transforms: { d0p1: { matrix: T } } };
  await f6.refreshTransforms();
  check(/a sheet where the last compute placed it, unmoved when that compute left it out\./.test(st5.xfError) && zonesDrawn === 1,
    "with a compute, the card says the last compute's placement stands, and that a sheet it left out is drawn unmoved (critic 3, m11); the zones are not drawn again when nothing moved");
  const f7 = mk({ state: st5, GET: async () => ({ display: "d0p0", transforms: {} }) });
  await f7.refreshTransforms();
  check(st5.xfError === "" && st5.xf.display === "d0p0", "a good answer clears the error");

  // ---- 6. wiring ------------------------------------------------------------------------------------------------
  const dg = fnSrc(ejs, "drawGhost");
  check(/mulM\(invM\(placeMatrix\(state\.key\)\), placeMatrix\(gk\)\)/.test(dg) && /drawGhostLines\(gk, an, T\)/.test(dg)
    && !/state\.ghost\.alpha <= 0\) return/.test(dg),
    "the Overlay draws the image and the lines with one placement; lines still show with the image slider at 0");
  const hideSet = (ejs.match(/const GHOST_HIDE_TOOLS = new Set\(\[([^\]]*)\]\)/) || [])[1] || "";
  check(/!GHOST_HIDE_TOOLS\.has\(state\.tool\)/.test(dg) && ["'pick'", "'edit'", "'limitpick'", "'align'"].every((t) => hideSet.includes(t))
    && !hideSet.includes("'draw'") && !hideSet.includes("'zone'"),
    "its lines stay off while a tool that clicks this sheet's own ink is on (pick, edit, limit pick, matching points), and stay for drawing a line or a zone (tracing over them): " + hideSet);
  // the alignment button and the zones card use the engine's sheet shown, as the card does (the second critic's WRONG-1)
  // (before the placement answers: the same rule on the page's copy, never a page that takes no part - the fourth critic's m6)
  check(/const dst = \(state\.xf && state\.xf\.display\) \|\| shownKey\(\);/.test(fnSrc(ejs, "startAlign")),
    "'Pick 2 matching points' aligns onto the engine's sheet shown (the one the Sheet alignment card names)");
  check(/const dsp = \(state\.xf && state\.xf\.display\) \|\| s\.display_page;/.test(fnSrc(ejs, "renderZones"))
    && /left out of the volumes shown on sheet \$\{sheetName\(dsp\)\}/.test(fnSrc(ejs, "renderZones")),
    "the zones card says a zone is left out by the engine's sheet shown (the compute's zone rule)");
  check(/if \(!state\.result\) refreshTransforms\(\);/.test(fnSrc(ejs, "loadProject")),
    "opening a project asks for the placement once (setResult asks when a result loads)");
  check(/!state\.polls\[gk\] && !state\.analysisLoads\[gk\]/.test(dg) && /state\.ghostAsk/.test(dg) && /5000/.test(dg),
    "R4: the overlaid sheet's read is asked for once (not while it is polled or loading, at most once each 5 s), not every frame");
  check(/refreshTransforms\(\)/.test(fnSrc(ejs, "afterLimitPicked")), "W2: a limit picked (it sets the sheet shown) fetches the placement again");
  const show = ejs.slice(ejs.indexOf("e.data.type === 'show'"), ejs.indexOf("e.data.type === 'show'") + 1400);
  check(/refreshTransforms\(\)/.test(show), "the host's open ('show': it sets the sheet shown and the roles) fetches it again");
  const sr = fnSrc(ejs, "setResult");
  check(/\n\s*refreshTransforms\(\);/.test(sr) && !/if \(r && !r\.error\) refreshTransforms/.test(sr),
    "every compute fetches it again, a refused one too (it set the sheet shown first)");
  check(/display_page !== before\) refreshTransforms\(\)/.test(fnSrc(ejs, "patchSettings")),
    "a settings change that moves the sheet shown fetches it again");
  const pa = fnSrc(ejs, "pollAnalysis");
  check(/r\.status === 'done'[\s\S]*refreshTransforms\(\)[\s\S]*r\.status === 'error'/.test(pa)
    && /await ensureAnalysis\(key\); refreshTransforms\(\); return;/.test(fnSrc(ejs, "startAnalysis")),
    "a read finishing fetches it again (a sheet placed by its printed words can move)");
  const ra = fnSrc(ejs, "renderAlign");
  check(/const dk = xf \? xf\.display : displayKey\(\)/.test(ra), "the Sheet alignment card names the engine's sheet shown (R5)");
  check(/id="ghostLines" checked/.test(html) && /\$\('ghostLines'\)\.addEventListener\('change'/.test(ejs),
    "an 'its lines' box sits by the Overlay, ticked, and redraws when changed");
  check(/markStale\(\); refreshTransforms\(\);[\s\S]{0,40}setStatus\('Alignment saved\. The Overlay shows it now/.test(ejs),
    "saving two matching points fetches the placement again (the Overlay moves at once)");
  check(/const dst = a\.dst;/.test(ejs) && /await patchPage\(src, \{ align: \{ pairs, dst \} \}\)/.test(ejs),
    "the matching points are saved with the sheet they were picked against (the engine turns them around when that sheet is not shown)");

  console.log(fails ? "\n" + fails + " failure(s)" : "\nall ok");
  process.exit(fails ? 1 : 0);
})();
