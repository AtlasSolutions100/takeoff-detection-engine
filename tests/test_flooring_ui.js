// FLOORING: rooms by floor type (CET, 2026-09-08).
//
// The pure half is evaluated - totals per type, the takeoff rows, the room
// line - and the panel's non-negotiables are pinned from source:
//   * a room with no floor type is NEVER silently outside the takeoff: it
//     is its own "unassigned" row in the totals and the panel says so
//   * base footage is the ring LESS the door openings on it
//   * one SF row and one LF row per type, only for types that have rooms,
//     with a unit price only when one was typed
//   * the pick sends the VISIBLE styles (the role assignment) and the
//     hand-drawn closures; a failed pick prints the server's own sentence
//   * closures, rooms and types persist under the additive "fl" key and
//     restore before anything renders (calcFlApplied mirrors calcStApplied)
//   * the tool ids are in POINT_TOOLS / POLY_DRAW_TOOLS, the button is in
//     the Tools menu, the overlay is drawn after the structure overlay
//
// Dependency-free:  node tests/test_flooring_ui.js
"use strict";
const fs = require("fs");
const path = require("path");
const SRC = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const HTML = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
const CSS = fs.readFileSync(path.join(__dirname, "..", "webui", "style.css"), "utf8");

let fails = 0;
function check(cond, msg) {
  console.log((cond ? "  ok   " : "  FAIL ") + msg);
  if (!cond) fails++;
}
function grab(name) {
  const start = SRC.indexOf("function " + name + "(");
  if (start < 0) return null;
  let depth = 0;
  for (let j = SRC.indexOf("{", start); j < SRC.length; j++) {
    if (SRC[j] === "{") depth++;
    else if (SRC[j] === "}" && --depth === 0) return SRC.slice(start, j + 1);
  }
  return null;
}
function grabConst(name) {
  const m = new RegExp("const " + name + " = ([^;]+);").exec(SRC);
  return m ? m[1] : null;
}

console.log("pure: the panel shows the server's figures");
// (2026-10-02, ruling P: the base, totals, rows and transitions are computed on the server - their cases are
// tests/test_fl_rooms.py; the panel reads them off each room's r.c and the sheet's totals and rows)
const H = require("./_fl_harness.js");
const P = H.fresh();
check(["flTotals", "flItems", "flBase", "flRoomLine", "flTransitions", "flMirror", "flRoomView", "flAct"].every(n => typeof P[n] === "function"), "helpers exist");
const C = (o) => Object.assign({ area_sf: 200, net_sf: 200, deduct_sf: 0, deduct_text: "", perim_lf: 60, base_lf: 51,
  base: { lf: 51, ring_lf: 60, openings: [{ w_lf: 3, on_edge: true }, { w_lf: 3, on_edge: true }, { w_lf: 3, on_edge: true }], openings_lf: 9, out_lf: 9, off_runs: 0, off_lf: 0, legacy: false, unplaced: 0 },
  wall: null, objects_sf: [], objects_wh: [], deductions_sf: [] }, o || {});
const hall = { name: "HALL", uid: "h", area_sf: 200, perim_lf: 60, holes_sf: 4, doors: 3, type: 0, openings: [{}, {}, {}], c: C() };
check(P.flWallLf(hall) === 51 && P.flNetSf(hall) === 200 && P.flBase(hall).ring_lf === 60, "base, net and the base's parts are the server's (r.c)");
check(P.flWallLf({ c: { base_lf: null } }) === null && P.flNetSf({}) === null, "no figure from the server: null, never 0");
const line = P.flRoomLine(hall);
check(line.startsWith("200 SF · walls 51 LF (perimeter 60 − openings 9 (3) = base 51 LF)"), "room line: SF, base, doors and their footage: " + line);
check(line.includes("4 SF inside it not counted"), "room line: holes are said, not hidden");
check(P.flRoomLine({ area_sf: 26.4, perim_lf: 27.1, closures: 2, c: C({ base_lf: 25.6 }) }).includes("2 gaps closed by hand"), "room line: hand-closed gaps are said");
check(P.flRoomLine({ area_sf: 80, perim_lf: 36, source: "drawn", c: C({ base_lf: 36 }) }).includes("outline drawn by hand"), "room line: a drawn outline says so");
check(P.flRoomLine({ area_sf: null, area_pt2: 1234, c: {} }).startsWith("no scale set"), "room line: no scale = pt only");
const bath = { name: "MAIN BATH", area_sf: 53.1, perim_lf: 30, type: 1, openings: [{}],
               c: C({ area_sf: 53.1, net_sf: 42.4, deduct_sf: 10.7, deduct_text: "tub 9.2 SF, clicked out 1.5 SF", base_lf: 27.3,
                      base: { lf: 27.3, ring_lf: 30, openings: [{ w_lf: 2.7, on_edge: true }], openings_lf: 2.7, out_lf: 2.7, off_runs: 0, off_lf: 0, legacy: false, unplaced: 0 } }) };
check(/^53 SF − tub 9\.2 SF, clicked out 1\.5 SF = 42 SF · walls/.test(P.flRoomLine(bath)), "room line: gross − deductions = net: " + P.flRoomLine(bath));
const rm = { name: "BED #2", area_sf: 135.5, perim_lf: 48, ceiling_ft: 9.094, ceiling_by: "sheet", type: 0, openings: [{}],
             c: C({ area_sf: 135.5, net_sf: 135.5, base_lf: 45.3, wall: { gross: 436.5, net: 418.7, doors: 17.8, ft: 9.094, by: "sheet" } }) };
check(/wall area 419 SF at 9'-1/.test(P.flRoomLine(rm)) && /from the sheet/.test(P.flRoomLine(rm)), "room line says the wall area, the height and its source: " + P.flRoomLine(rm));
check(/no height read, type one/.test(P.flRoomLine({ area_sf: 100, perim_lf: 40, ceiling_text: "VAULTED CEILING", c: C({ base_lf: 40 }) })),
      "a vaulted note with no height asks for one instead of guessing");
P.state.fl = { types: [P.flNewType("Carpet", 0, { id: "tC" }), P.flNewType("Tile", 1, { id: "tT" })],
               page: { totals: [{ i: 0, id: "tC", name: "Carpet", sf: 300, lf: 88, rooms: 2 }, { i: -1, name: "unassigned", color: null, sf: 12, rooms: 1 }],
                       items: [{ label: "Flooring — Carpet", qty: 300, unit: "SF", src: "flooring" }], transitions: { transitions: [{ a: 0, b: 1 }], open: 2 } } };
const tot = P.flTotals();
check(tot.length === 3 && tot[0].sf === 300 && tot[1].name === "Tile" && tot[1].rooms === 0 && tot[2].i === -1 && tot[2].color === P.FL_UNASSIGNED,
      "the type rows are the server's, one per type in the estimate's order (a type with no rooms shows empty); the unassigned row is grey");
check(P.flTotals(null, null, { types: P.state.fl.types, page: { totals: [] } }).length === 2
      && P.flTotals(null, null, { types: P.state.fl.types, page: { totals: [] } }).every(t => t.rooms === 0 && t.sf === 0),
      "a sheet with no rooms on the server still lists every type (empty) - the panel renders (it threw on an empty sheet)");
check(P.flItems()[0].src === "flooring" && P.flItems() !== P.state.fl.page.items, "the takeoff rows are copies of the server's");
check(P.flTransitions().open === 2, "the transitions are the server's");
const LRX = new Function("return " + grabConst("FL_LABEL_RX") + ";")();
check(["Flooring — x", "Base/molding — x", "Integral base — x", "Waste — x", "Wall area — x", "Transition strips — x", "Walls — interior (centreline)"].every(l => LRX.test(l))
      && !LRX.test("Mass Excavation"), "FL_LABEL_RX owns every row kind the flooring makes, so a repaint replaces them all");
const DD = new Function("return " + grabConst("FL_DEDUCT_DEFAULTS") + ";")();
check(DD.tub === true && DD.shower === true && DD.cabinet === false && DD.appliance === false && DD.toilet === false && DD.furniture === false && DD.shape === false,
      "defaults: tubs and showers out, cabinets IN (Koby: not cabinets), everything else floored under");
const dc = grab("flDeductClick");
check(dc && /kind: "deduct_at", page: state\.page, uid: fl\.rooms\[ri\]\.uid, x: px, y: py/.test(dc),
      "deduct click: one act; the server toggles a listed object, puts a clicked-out shape back, or measures the cell (refused when most of the room)");
check(/"fl-close", "fl-deduct",/.test(SRC) && /const FL_TOOLS = \["fl-pick", "fl-draw", "fl-close", "fl-deduct"(, "fl-edit")?(, "fl-base")?(, "fl-open")?(, "fl-wall")?(, "fl-stroke")?\]/.test(SRC), "the deduct tool is a POINT_TOOL and a flooring tool");
const dr2 = grab("drawFlooring");
check(dr2 && /if \(o\.deduct\) hatch\(o\.pts\);/.test(dr2) && /for \(const d of \(r\.deductions \|\| \[\]\)\) hatch\(d\.pts\);/.test(dr2), "deducted objects and clicked-out shapes are hatched on the sheet");
const rf3 = grab("renderFl");
check(rf3 && /take out of the flooring by default:/.test(rf3) && /FL_KIND_GROUPS/.test(rf3) && /fl-objs/.test(rf3) && /mode\("Deduct", "fl-deduct"/.test(rf3),
      "the panel has the per-kind defaults, a per-room object list with ticks, and the Deduct button");

console.log("walls by type, from source");
const wc = grab("flWallClassOf");
check(wc && /shear/.test(wc) && /ext\(erior\)\?/.test(wc) && /wall \(unclassified\)/.test(wc), "class by layer / legend name: shear, exterior, interior, unclassified");
const WC = new Function("const FL_WALL_CLASSES = " + grabConst("FL_WALL_CLASSES") + ";\n" + wc + "\nreturn flWallClassOf;")();
check(WC({ style_id: 1, layer: "A-Exterior Wall Line" }, {}) === "exterior", "A-Exterior Wall Line -> exterior");
check(WC({ style_id: 2, layer: "A-wall" }, {}) === "interior", "A-wall on a layered sheet -> interior");
check(WC({ style_id: 3, layer: "", suggested_label: "SHEAR WALL (legend)" }, {}) === "shear", "a SHEAR WALL legend label -> shear");
check(WC({ style_id: 4, layer: "", suggested_label: "solid solid" }, {}) === "wall (unclassified)", "a heavy unnamed solid -> unclassified, never guessed");
check(WC({ style_id: 4, layer: "", suggested_label: "solid solid" }, { wallClass: { 4: "exterior" } }) === "exterior", "the operator's class wins");
check(WC({ style_id: 5, layer: "A-Wall Hatch" }, {}) === "not a wall" && WC({ style_id: 6, layer: "A-Flooring" }, {}) === "not a wall",
      "the hatch between a wall's faces and the flooring boundaries enclose rooms but are not wall lines");
const fsrc = grab("flFaceShare");
const FS = new Function(fsrc + "\nreturn flFaceShare;")();
const twoFace = [{ points: [[0, 0], [200, 0]] }, { points: [[0, 6], [200, 6]] }, { points: [[300, 0], [300, 100]] }, { points: [[306, 0], [306, 100]] }];
check(FS(twoFace).share > 0.99, "two parallel lines 6 pt apart: every segment has a partner (drawn as two faces)");
check(FS([{ points: [[0, 0], [200, 0]] }, { points: [[0, 40], [200, 40]] }]).share === 0, "lines 40 pt apart are not a wall's two faces");
const wr = grab("flWallRows");
check(wr && /centreline/.test(wr) && /faces per wall/.test(wr), "centreline rows say the line footage and the faces they divided by");
const ap2 = grab("flApplyItems");
check(ap2 && /if \(fl\.wallRows\) for \(const it of flWallRows\(fl\)\)/.test(ap2), "centreline rows reach the takeoff only when ticked");
const rf2 = grab("renderFl");
check(rf2 && /"Walls by type"/.test(rf2) && /"Label wall lines by type"/.test(rf2) && /add centreline wall LF rows/.test(rf2),
      "the panel has the walls block, the relabel button and the centreline checkbox");
check(rf2 && /classify the \$\{flWallStyles\(fl\)\.length\} wall line types/.test(rf2) && /key: "wallClass", value: Object\.assign\(\{\}, fl\.wallClass, \{ \[s\.style_id\]: sel\.value \}\)/.test(rf2),
      "one class select per wall line type, folded under the table");
// (2026-10-02: the waste input is its own: blank = not set, never a silent 0)
check(rf2 && /waste %/.test(rf2) && /fl-num fl-waste/.test(rf2) && /wi\.placeholder = "not set"/.test(rf2)
      && /field: "waste", value: wi\.value\.trim\(\)/.test(rf2),
      "the types table has a waste % column; a blank one is NOT SET (null), not 0");
check(rf2 && /ceiling height for rooms with no note/.test(rf2) && /fl-clg/.test(rf2), "a default ceiling input, and a per-room height box");
check(rf2 && /transition/.test(rf2) && /doorway.*to rooms not picked yet/.test(rf2), "the result line counts transitions and open doorways");
const lw = grab("flLabelWalls");
check(lw && /state\.labels\.set\(s\.style_id, name\)/.test(lw) && /memSave\(s\)/.test(lw), "relabelling goes through the label map and memSave, the style row's own path");

console.log("the room's fields come from the job document");
const view = grab("flRoomView");
check(view && /scale_ft_per_in: \+sc\.ft > 0 \? \+sc\.ft : null/.test(view) && /objects_sf/.test(view) && /deductions_sf/.test(view),
      "each room on screen is the stored room with the server's figures (its scale, its fixtures' areas)");
const mir = grab("flMirror");
check(mir && /fl\.default_ceiling_ft = a\.default_ceiling_ft/.test(mir) && /fl\.wallClass = a\.wallClass/.test(mir) && /fl\.closures = a\.closures/.test(mir),
      "the sheet's aids (default height, wall classes, gaps closed, walls drawn) are the document's");
console.log("details reach the panel, the CSV and the payload");
check(/details: Array\.isArray\(it\.details\) \? it\.details : null/.test(SRC), "computeTakeoffRows carries details on calculator rows");
check(!!grab("detailNote") && /box\.appendChild\(detailNote\(it\)\)/.test(SRC), "the itemized panel prints a detail note under the row");
check(/`  part: \$\{dt\.name\}`/.test(SRC) && /`part of \$\{it\.label\}`/.test(SRC), "the CSV lists each part under its row, named as part of it");

console.log("panel, from source");
const pick = grab("flPickAt");
check(pick && /kind: "room_pick", page: state\.page, x: px, y: py, styles: flVisibleStyles\(\)/.test(pick), "the pick sends the visible styles (the server adds the sheet's gaps and walls by hand)");
check(/if \(d\.ok === false\) setStatus\(d\.why \|\| "refused", "err"\)/.test(grab("flAct")), "a failed pick prints the server's own sentence");
check(pick && /if \(fl\.busy\) return;/.test(pick), "no double pick while one is in flight");
const vis = grab("flVisibleStyles");
check(vis && /famShown\(id\)/.test(vis), "visible = famShown, the canvas's own answer");
const close = grab("flCloseClick");
check(close && /kind: "aids_add", page: state.page, key: "closures", value: seg/.test(close) && /state\.tool = "fl-pick"/.test(close),
      "close a gap: two clicks make a closure and hand the tool back to Pick");
const fin = grab("flFinishDraw");
check(fin && /kind: "room_draw"/.test(fin), "draw outline: one act, the server measures it (shoelace, marked drawn)");
const apply = grab("flApplyItems");
check(apply && /filter\(it => !FL_LABEL_RX\.test\(it\.label\) && it\.src !== "flooring"\)/.test(apply), "re-apply replaces only its own rows");
// (2026-10-02, ruling H: no price book and no name memory for flooring - the job estimate on the server is
// the one home of every rate; the takeoff rows carry none)
check(apply && !/priceKey\(it\.label\)/.test(apply) && !/price_key|unit_price/.test(grab("flItems"))
      && !/flCarryPrices|flmem/.test(SRC),
      "no price book, no name memory: the rows carry no price and nothing re-fills one by name");
check(apply && !/calcStateDirty\(\)/.test(apply), "...and marks nothing dirty: the rows are the server's (ruling Q)");
check(!/out\.fl = /.test(SRC), "the page record never carries a flooring part from this page (the job document holds it)");
check(/stApplySaved\(\);\n  \/\/ THE JOB'S FLOORING is read \(never written\) as the page loads/.test(SRC) && /if \(!flJobFor\(\)\) flJobLoad\(\); else flSync\(\);/.test(SRC),
      "as a page loads the job's flooring is READ (one GET) where the other panels restore");
check(/"fl-pick", "fl-draw", "fl-close",/.test(SRC), "the three tools are POINT_TOOLS");
check(/POLY_DRAW_TOOLS = new Set\(\[.*"fl-draw"\]\)/.test(SRC), "fl-draw is a polygon tool (close-snap)");
check(/\["flBtn", "flPanel"\],\n  \["stBtn", "stPanel"\]/.test(SRC), "Flooring sits in the Tools menu before Structure");
check(/drawStructural\(ctx, k\);\n  drawFlooring\(ctx, k\);/.test(SRC), "the flooring overlay is drawn after the structure overlay");
const dr = grab("drawFlooring");
check(dr && /FL_UNASSIGNED/.test(dr) && /ctx\.fill\(\)/.test(dr), "rooms are filled in their type's colour, grey when untyped");
check(dr && /FL_CLOSE_COLOR/.test(dr), "closures are drawn on the sheet");
const rf = grab("renderFl");
check(rf && /fl-unassigned/.test(rf) && /not in the takeoff/.test(rf), "the panel says when rooms have no type");
check(rf && /"Pick rooms"/.test(rf) && /"Close a gap"/.test(rf) && /"Draw outline"/.test(rf), "three mode buttons");
check(rf && /NO SCALE SET/.test(rf), "the panel says when there is no scale");

console.log("walls only");
const Wf = new Function(grab("flWallStyleIds") + "\nreturn flWallStyleIds;")();
const layered = [
  { style_id: 1, layer: "A-wall", class: "solid", width: 0.7, n: 42 },
  { style_id: 2, layer: "A-Flooring", class: "solid", width: 0.8, n: 58 },
  { style_id: 3, layer: "A-Exterior Wall Line", class: "solid", width: 0.4, n: 9 },
  { style_id: 4, layer: "A-Wall Hatch", class: "solid", width: 2.6, n: 47 },
  { style_id: 5, layer: "A-Door", class: "dash1", width: 0.7, n: 15 },
  { style_id: 6, layer: "A-Door-Window", class: "solid", width: 2.2, n: 2 },
  { style_id: 7, layer: "A-Dim", class: "solid", width: 0.4, n: 60 },
  { style_id: 8, layer: "I-Furn", class: "solid", width: 0.7, n: 32 },
];
let wl = Wf(layered);
check(wl.by === "layer name" && wl.ids.join(",") === "1,2,3,4",
      "with CAD layers: wall, flooring, exterior and wall hatch are in; door, door-window, dim, furniture are out");
const unnamed = [
  { style_id: 17, layer: "", class: "solid", width: 3.2, n: 48 },
  { style_id: 0, layer: "", class: "solid", width: 1.5, n: 38 },
  { style_id: 33, layer: "", class: "dash1", width: 0.8, n: 10, suggested_label: "SHEAR WALL (legend)" },
  { style_id: 26, layer: "", class: "solid", width: 0.2, n: 13 },
  { style_id: 31, layer: "", class: "solid", width: 1.9, n: 1 },
  { style_id: 14, layer: "", class: "dash1", width: 3.2, n: 7 },
];
wl = Wf(unnamed);
check(wl.by === "line weight" && wl.ids.join(",") === "17,0,33",
      "without layers: heavy solids with 3+ lines and a WALL legend label; thin solids, singletons and dashes are out");
check(Wf([]).ids.length === 0, "no styles: nothing kept");
const rfw = grab("renderFl");
check(rfw && /"Show walls only"/.test(rfw) && /flShowWallsOnly\(\)/.test(rfw), "the panel has the Show walls only button");
const swo = grab("flShowWallsOnly");
check(swo && /setFamVis\(s\.style_id, keep\.has\(s\.style_id\)\)/.test(swo), "it sets visibility through setFamVis, the checkboxes' own path");
check(swo && /chosen by \$\{by\}/.test(swo), "and says which rule chose");

console.log("html/css");
check(/id="flBtn" hidden/.test(HTML), "index.html: the Tools button, hidden until a sheet is open");
check(/id="flPanel" class="ew-panel" hidden/.test(HTML), "index.html: the panel");
for (const id of ["flHead", "flClose", "flHint", "flTypes", "flBody", "flRows", "flActions", "flResult"])
  check(new RegExp('id="' + id + '"').test(HTML), "index.html: #" + id);
const v = /app\.js\?v=(\d+)/.exec(HTML);
check(v && Number(v[1]) >= 166, "index.html: cache-buster moved (got " + (v && v[1]) + ")");
check(/\.fl-types-tbl \{/.test(CSS) && /\.fl-swatch \{/.test(CSS) && /\.fl-row-top \{/.test(CSS), "style.css: the panel's rules");

// ---- THE POINT EDITOR and THE 3D VIEW (2026-09-09) ----
console.log("point editor");
check(/"fl-edit"/.test(grabConst("POINT_TOOLS") || "") , "fl-edit is a point tool (crosshair cursor)");
check(/const FL_TOOLS = \[[^\]]*"fl-edit"/.test(SRC), "fl-edit is in FL_TOOLS (Esc releases it, the click dispatcher routes it)");
const edgeAt = new Function(grab("flEditEdgeAt") + "; return flEditEdgeAt;")();
const vertAt = new Function(grab("flEditVertexAt") + "; return flEditVertexAt;")();
const sq = [[0, 0], [10, 0], [10, 10], [0, 10]];
let ed = edgeAt(sq, 5, 0.4, 1.5);
check(ed && ed.i === 0 && Math.abs(ed.at[0] - 5) < 1e-9 && Math.abs(ed.at[1]) < 1e-9, "a press near the top edge lands ON the edge at the projection (5,0)");
ed = edgeAt(sq, 10.3, 7, 1.5);
check(ed && ed.i === 1 && Math.abs(ed.at[0] - 10) < 1e-9 && Math.abs(ed.at[1] - 7) < 1e-9, "the right edge: index 1, projection (10,7)");
check(edgeAt(sq, 5, 5, 1.5) === null, "the middle of the room is on no edge");
check(edgeAt(sq, 0.5, 9.5, 1.5) && edgeAt(sq, 0.5, 9.5, 1.5).i === 3 || edgeAt(sq, 0.5, 9.5, 1.5).i === 2, "a corner-adjacent press still finds an edge");
check(vertAt(sq, 9.6, 10.3, 1.5) === 2 && vertAt(sq, 5, 5, 1.5) === -1, "the nearest corner within tolerance, or -1");
const md = /addEventListener\("mousedown", \(e\) => \{\n  if \(state\.tool !== "fl-edit" \|\| e\.target !== cv \|\| e\.button !== 0\) return;[\s\S]*?\n\}, true\);/.exec(SRC);
check(!!md, "the editor's mousedown runs in the CAPTURE phase on the window");
check(md && /r\.pts\.splice\(ed\.i \+ 1, 0, ed\.at\)/.test(md[0]), "a press on an edge inserts a corner at the projection, right after the edge's first point");
check(md && /e\.preventDefault\(\); e\.stopImmediatePropagation\(\);/.test(md[0]), "and stops the sheet's own pan / vertex-drag from seeing it");
check(md && /if \(!ed\) return;/.test(md[0]), "a press off the outline is left to the pan (and the click that selects another room)");
const mu = /addEventListener\("mouseup", \(e\) => \{\n  if \(state\.tool !== "fl-edit"\) return;[\s\S]*?\n\}, true\);/.exec(SRC);
check(mu && /flEditAfter\(r, r\.pts\.slice\(\), d\.moved \? "corner moved" : "corner set"\)/.test(mu[0]) && /kind: "room_edit"/.test(grab("flEditAfter")),
      "mouseup sends the new outline as one act: the server measures it at the room's own scale");
const rmv = grab("flEditRemoveVertex");
check(rmv && /if \(r\.pts\.length <= 3\) return setStatus\("a room keeps at least 3 corners"/.test(rmv), "a corner cannot be removed below 3");
check(/if \(\(e\.key === "Delete" \|\| e\.key === "Backspace"\) && state\.tool === "fl-edit"\)/.test(SRC), "Delete / Backspace removes the hovered corner while editing");
check(/if \(state\.tool !== "fl-edit"\) return;\n  const r = flEditRoom\(\);[\s\S]{0,200}flEditRemoveVertex\(vi\)/.test(SRC), "double-click on a corner removes it");
const rl = grab("flRoomLine");
check(rl && /r\.source === "edited"\) s \+= " \\u00b7 shape edited by hand"/.test(rl), "the room line says 'shape edited by hand'");
check(/"source": r\.get\("source"\) if r\.get\("source"\) in \("click", "drawn", "edited"\) else "click"/.test(require("fs").readFileSync(require("path").join(__dirname, "..", "takeoff_engine", "fl_job.py"), "utf8")),
      "a stored room keeps 'edited' (the server's room record)");
const rfe = grab("renderFl");
check(rfe && /mode\("Edit shape", "fl-edit"/.test(rfe), "the panel has the Edit shape mode");
check(rfe && /mk\("button", "mini", "edit"\)/.test(rfe) && /flEditSelect\(k\)/.test(rfe), "each room row has its own edit button that selects that room");
check(rfe && /top\.append\(dot, nm, sel, exb, clg, ed, rm\)/.test(rfe), "placed before the remove button (an untyped room's exclude button beside its type)");
const dis = grab("flDisarm");
check(dis && /fl\.edit = null/.test(dis), "disarming forgets the edited room");
const dfe = grab("drawFlooring");
check(dfe && /state\.tool === "fl-edit" && fl\.edit && fl\.edit\.room !== null/.test(dfe) && /CV_VERTEX_ON/.test(dfe), "the canvas draws a handle on every corner of the selected room in the sheet's own vertex colours");
check(dfe && /hv\.e !== undefined && hv\.at/.test(dfe), "and marks the spot on a hovered edge where a press would add one");

console.log("3D view");
const FL3D = fs.readFileSync(path.join(__dirname, "..", "webui", "fl3d.js"), "utf8");
check(rfe && /mk\("button", "mini", "3D view"\)/.test(rfe) && /b3\.onclick = flOpen3d/.test(rfe), "the panel has the 3D view button");
check(rfe && /b3\.disabled = !fl\.rooms\.length/.test(rfe), "disabled until a room exists");
const o3 = grab("flOpen3d");
check(o3 && /s\.src = "fl3d\.js\?v=\d+"/.test(o3) && /window\.fl3dOpen\(fl, \{/.test(o3), "flOpen3d loads fl3d.js on first press, then hands it the flooring state");
check(o3 && /fpp: stFtPerPt\(\)/.test(o3) && /wallH: fl\.default_ceiling_ft \|\| 9/.test(o3), "with the sheet scale and the panel's default ceiling height");
check(/VENDOR = \["vendor\/three\.min\.js", "vendor\/OrbitControls\.js"\]/.test(FL3D), "fl3d.js loads the VENDORED three.js and OrbitControls, on demand");
check(!/https?:\/\/(cdn|unpkg|jsdelivr)/.test(FL3D), "no CDN: the field office renders it offline");
for (const f of ["three.min.js", "OrbitControls.js", "THREE-LICENSE"])
  check(fs.existsSync(path.join(__dirname, "..", "webui", "vendor", f)), "webui/vendor/" + f + " is in the tree");
const texKind = new Function(FL3D.slice(FL3D.indexOf("function texKind("), FL3D.indexOf("// ---- the view")) + "; return texKind;")();
check(texKind("Carpet") === "carpet" && texKind("Tile") === "tile" && texKind("Hardwood") === "wood" && texKind("LVP") === "lvp"
      && texKind("Laminate") === "lvp" && texKind("Porcelain tile") === "tile" && texKind("Type 3") === "plain" && texKind(null) === "plain",
      "a floor type's NAME picks its texture: carpet / tile / wood / LVP, else plain");
check(/TEX = \{\n    carpet\(hex\)/.test(FL3D) && /tile\(hex\)/.test(FL3D) && /wood\(hex\)/.test(FL3D) && /lvp\(hex\)/.test(FL3D) && /plain\(hex\)/.test(FL3D),
      "the five textures are drawn procedurally on a canvas, tinted with the type's colour");
check(/tex\.repeat\.set\(1 \/ t\.sizeFt, 1 \/ t\.sizeFt\)/.test(FL3D), "texture repeat is in FEET (a 1 ft tile is 1 ft on the floor)");
check(/for \(const o of \(r\.objects \|\| \[\]\)\) if \(o\.deduct && o\.pts && o\.pts\.length >= 3\) holes\.push\(o\.pts\);/.test(FL3D)
      && /for \(const d of \(r\.deductions \|\| \[\]\)\) if \(d\.pts && d\.pts\.length >= 3\) holes\.push\(d\.pts\);/.test(FL3D),
      "deducted fixtures and clicked-out shapes are holes in the floor slab");
check(/const hFt = \(r\.ceiling_ft > 0 \? r\.ceiling_ft : \(fl\.default_ceiling_ft > 0 \? fl\.default_ceiling_ft : opts\.wallH\)\) \|\| 9;/.test(FL3D),
      "wall height: the room's own ceiling, else the panel default, else the bar's number, else 9 ft");
check(/new THREE\.Vector2\(w\[0\], -w\[1\]\)/.test(FL3D) && /floor\.rotation\.x = -Math\.PI \/ 2/.test(FL3D), "page y runs down; the floor is laid so the plan reads the right way round from above");
check(/id="fl3dClose"/.test(FL3D) && /window\.fl3dClose = close/.test(FL3D) && /view\.dispose\(\)/.test(FL3D), "close disposes the renderer");
check(/#fl3d \{ position: fixed; inset: 0; z-index: 60/.test(CSS) && /#fl3dStage \{ flex: 1/.test(CSS), "style.css: the overlay");
check(/<b>Edit shape<\/b>/.test(HTML) && /<b>3D view<\/b>/.test(HTML), "index.html: the hint names both");
const v2 = /app\.js\?v=(\d+)/.exec(HTML);
check(v2 && Number(v2[1]) >= 177, "index.html: cache-buster moved for the editor (got " + (v2 && v2[1]) + ")");

console.log(fails ? "\n" + fails + " FAILURE(S)" : "\nall green");
process.exit(fails ? 1 : 0);
