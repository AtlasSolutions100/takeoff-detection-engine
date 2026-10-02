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

console.log("pure");
const names = ["flDeductSf", "flNetSf", "flDeductText", "flWallLf", "flCeiling", "flWallSf", "flTotals", "flTransitions", "flItems", "flRoomLine", "flRoomScaleNote"];
const bodies = names.map(grab);
check(bodies.every(Boolean), "helpers exist: " + names.join(", "));
const P = new Function(
  "const FL_UNASSIGNED = " + grabConst("FL_UNASSIGNED") + ";\n"
  + "const FL_DOOR_HT_FT = " + grabConst("FL_DOOR_HT_FT") + ";\n"
  + "const flFmt = " + grabConst("flFmt") + ";\n"
  + "const flFtIn = (ft) => { const f = Math.floor(ft), i = Math.round((ft - f) * 12 * 8) / 8; return f + \"'-\" + i + '\"'; };\n"
  + bodies.join("\n") + "\nreturn { " + names.join(", ") + " };")();

const types = [{ name: "Carpet", color: "#4e9af1", sf_price: 3.25, lf_price: 1.8 },
               { name: "Tile", color: "#4fc27a", sf_price: 0, lf_price: 0 }];
const rooms = [
  { name: "OFFICE", area_sf: 100, perim_lf: 40, openings_lf: 3, holes_sf: 0, doors: 1, type: 0 },
  { name: "HALL", area_sf: 200, perim_lf: 60, openings_lf: 9, holes_sf: 4, doors: 3, type: 0 },
  { name: "BATH", area_sf: 50, perim_lf: 30, openings_lf: 3, holes_sf: 0, doors: 1, type: 1 },
  { name: "closet", area_sf: 12, perim_lf: 14, openings_lf: 0, holes_sf: 0, doors: 0, type: null },
];
check(P.flWallLf(rooms[0]) === 37, "base = ring less openings (40 - 3)");
check(P.flWallLf({ perim_lf: null }) === null, "no scale: base is null, not 0");
check(P.flWallLf({ perim_lf: 2, openings_lf: 9 }) === 0, "openings cannot make the base negative");
const tot = P.flTotals(types, rooms);
check(tot.length === 3 && tot[2].i === -1 && tot[2].rooms === 1 && tot[2].sf === 12,
      "an untyped room is its own 'unassigned' row, never dropped");
check(tot[0].sf === 300 && tot[0].lf === 88 && tot[0].rooms === 2, "Carpet: 300 SF, 88 LF, 2 rooms");
check(tot[1].sf === 50 && tot[1].lf === 27, "Tile: 50 SF, 27 LF");
check(P.flTotals(types, []).length === 2, "no rooms: no unassigned row");
const items = P.flItems(types, rooms);
check(items.length === 4, "one SF row and one LF row per type with rooms (4)");
check(items[0].label === "Flooring — Carpet" && items[0].qty === 300 && items[0].unit === "SF" && items[0].unit_price === 3.25,
      "Flooring — Carpet 300 SF @ 3.25");
check(items[1].label === "Base/molding — Carpet" && items[1].qty === 88 && items[1].unit === "LF" && items[1].unit_price === 1.8,
      "Base/molding — Carpet 88 LF @ 1.80");
check(!("unit_price" in items[2]) && !("unit_price" in items[3]), "no price typed = no unit_price on the row");
check(!items.some(it => /unassigned/i.test(it.label)), "the unassigned row never reaches the takeoff");
check(P.flItems([{ name: "Carpet", sf_price: 0, lf_price: 0 }], [{ area_sf: 0, perim_lf: 0, type: 0 }]).length === 0,
      "a type whose rooms measure nothing adds no rows");
const line = P.flRoomLine(rooms[1]);
check(line.startsWith("200 SF · walls 51 LF (3 doors, −9 LF)"), "room line: SF, base, doors and their footage: " + line);
check(line.includes("4 SF inside it not counted"), "room line: holes are said, not hidden");
check(P.flRoomLine({ area_sf: 26.4, perim_lf: 27.1, openings_lf: 1.5, doors: 1, closures: 2 }).includes("2 gaps closed by hand"),
      "room line: hand-closed gaps are said");
check(P.flRoomLine({ area_sf: 80, perim_lf: 36, source: "drawn" }).includes("outline drawn by hand"), "room line: a drawn outline says so");
check(P.flRoomLine({ area_sf: null, area_pt2: 1234 }).startsWith("no scale set"), "room line: no scale = pt only");

console.log("ceiling height -> wall area");
const flx = { default_ceiling_ft: null, result: { scale: { ft_per_paper_inch: 4 } } };
const rm = { name: "BED #2", area_sf: 135.5, perim_lf: 48, openings_lf: 2.67, doors: 1, ceiling_ft: 9.094, ceiling_by: "sheet", type: 0 };
check(P.flCeiling(rm, flx).ft === 9.094 && P.flCeiling(rm, flx).by === "sheet", "a room's own note is the height, marked from the sheet");
check(P.flCeiling({ perim_lf: 40 }, flx) === null, "no note and no default: no height");
check(P.flCeiling({ perim_lf: 40 }, { default_ceiling_ft: 9 }).by === "default", "the panel default fills a room with no note, marked default");
check(P.flCeiling({ perim_lf: 40, ceiling_ft: 10, ceiling_by: "typed" }, { default_ceiling_ft: 9 }).ft === 10, "a typed height wins over the default");
const wa = P.flWallSf(rm, flx);
check(wa && Math.abs(wa.gross - 48 * 9.094) < 0.01, "wall area gross = perimeter x height (48 x 9'-1 1/8\")");
check(wa && Math.abs(wa.doors - 2.67 * 6.667) < 0.01 && Math.abs(wa.net - (wa.gross - wa.doors)) < 1e-9,
      "door openings come out at door height (6'-8\"), not floor to ceiling - the wall over the door is wall");
check(P.flWallSf({ perim_lf: 40 }, flx) === null && P.flWallSf({ perim_lf: null, ceiling_ft: 9 }, flx) === null,
      "no height or no scale: no wall area, never 0");
check(P.flWallSf({ perim_lf: 40, openings_lf: 3, ceiling_ft: 6 }, flx).doors === 18, "a ceiling lower than a door: the opening is capped at the ceiling");
const lineC = P.flRoomLine(rm, flx);
check(/wall area 419 SF at 9'-1/.test(lineC) && /from the sheet/.test(lineC), "room line says the wall area, the height and its source: " + lineC);
check(/no height read, type one/.test(P.flRoomLine({ area_sf: 100, perim_lf: 40, ceiling_text: "VAULTED CEILING" }, flx)),
      "a vaulted note with no height asks for one instead of guessing");

console.log("waste, transitions, details");
const types2 = [{ name: "Carpet", color: "#4e9af1", sf_price: 3.25, lf_price: 0, waste_pct: 10 },
                { name: "Tile", color: "#4fc27a", sf_price: 0, lf_price: 0, waste_pct: 0 }];
const B1 = { x0: 100, y0: 200, x1: 100, y1: 254, r: 54 };       // a 3'-0" door at 1/4"
const B2 = { x0: 300, y0: 200, x1: 300, y1: 236, r: 36 };       // a 2'-0" door
const rooms2 = [
  { name: "HALL", area_sf: 200, perim_lf: 60, openings_lf: 5, doors: 2, type: 0, bridges: [B1, B2], ceiling_ft: 9, ceiling_by: "sheet" },
  { name: "BATH", area_sf: 50, perim_lf: 30, openings_lf: 3, doors: 1, type: 1, bridges: [{ x0: 100, y0: 254, x1: 100, y1: 200, r: 54 }] },
  { name: "BED", area_sf: 150, perim_lf: 50, openings_lf: 2, doors: 1, type: 0, bridges: [B2] },
];
const tr = P.flTransitions(rooms2, types2);
check(tr.transitions.length === 1 && tr.transitions[0].ta === 0 && tr.transitions[0].tb === 1 && tr.transitions[0].width_pt === 54,
      "HALL/BATH share one door and differ in type: ONE transition, the door's width (bridge matched end-for-end)");
check(tr.open === 0, "HALL/BED share the other door but are both Carpet: no transition, no open doorway");
const tr2 = P.flTransitions([rooms2[0]], types2);
check(tr2.transitions.length === 0 && tr2.open === 2, "a picked room's doorways to unpicked rooms are counted as open, not as transitions");
check(P.flTransitions([{ type: null, bridges: [B1] }, { type: 1, bridges: [B1] }], types2).transitions.length === 0,
      "an untyped room makes no transition");
// GEOMETRY (2026-09-26, audit D27): a bridge is drawn as wall for every fill,
// so both rooms' outlines run along it, but a fill lists it only when its
// raster contour passes within 5 px of the midpoint - Seer Stone A2.1's
// PRIMARY BATH bedroom door sat in one room's list. The room on the other
// side is found by its OUTLINE now: within a wall's thickness of the opening.
const gSq = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
const Bx = { x0: 200, y0: 100, x1: 200, y1: 136, r: 36 };        // a door on the wall x = 200, y 100..136
const gBedroom = { name: "BEDROOM", type: 0, pts: gSq(0, 0, 200, 300), bridges: [Bx] };          // lists the bridge
const gBath = { name: "BATH", type: 1, pts: gSq(200, 0, 400, 300), bridges: [] };                // does not - its outline runs on x = 200
const trG = P.flTransitions([gBedroom, gBath], types2);
check(trG.transitions.length === 1 && trG.open === 0 && trG.transitions[0].width_pt === 36,
      "a one-sided bridge still makes a transition when the other room's outline runs along it: " + JSON.stringify(trG));
const far = { name: "BED2", type: 1, pts: gSq(600, 0, 800, 300), bridges: [] };                 // nowhere near the door
const trF = P.flTransitions([gBedroom, far], types2);
check(trF.transitions.length === 0 && trF.open === 1, "a room whose outline is nowhere near the opening is not on its side");
// a hand-drawn closure across a cased opening between two types is a strip too
const wic = { name: "W.I.C.", type: 0, pts: gSq(0, 300, 200, 500), bridges: [] };
const bath2 = { name: "BATH", type: 1, pts: gSq(200, 300, 400, 500), bridges: [] };
const trC = P.flTransitions([wic, bath2], types2, [[200, 380, 200, 420]]);
check(trC.transitions.length === 1 && trC.transitions[0].src === "closure" && Math.abs(trC.transitions[0].width_pt - 40) < 1e-9,
      "a closure between two rooms of different types counts as a transition, the closure's length wide: " + JSON.stringify(trC));
check(P.flTransitions([wic, { name: "HALL", type: 0, pts: gSq(200, 300, 400, 500), bridges: [] }], types2, [[200, 380, 200, 420]]).transitions.length === 0,
      "a closure between two rooms of the SAME type is not a transition");
const items2 = P.flItems(types2, rooms2, flx);
const lab = items2.map(it => it.label);
check(lab[0] === "Flooring — Carpet" && items2[0].qty === 350 && items2[0].details.length === 2
      && items2[0].details[0].name === "HALL" && items2[0].details[0].qty === 200 && items2[0].details[0].unit === "SF",
      "the flooring row carries its rooms as details: " + JSON.stringify(items2[0].details));
check(lab[1] === "Waste — Carpet (10%)" && items2[1].qty === 35 && items2[1].unit_price === 3.25,
      "10% waste is its own row at the type's $/SF, the measured row untouched: " + lab[1] + " " + items2[1].qty);
check(lab[2] === "Base/molding — Carpet" && items2[2].qty === 103 && items2[2].details[0].note === "2 doors out",
      "base row details name the doors taken out: " + JSON.stringify(items2[2].details[0]));
check(!lab.some(l => /Waste — Tile/.test(l)), "no waste row for a type with 0%");
const wallRow = items2.find(it => /^Wall area/.test(it.label));
check(wallRow && Math.abs(wallRow.qty - (60 * 9 - 5 * 6.667)) < 0.06 && wallRow.details.length === 1 && /from the sheet/.test(wallRow.details[0].note),
      "one wall-area row for the rooms that have a height, with each room's basis: " + JSON.stringify(wallRow && wallRow.details));
const trRow = items2.find(it => /^Transition strips/.test(it.label));
check(trRow && trRow.label === "Transition strips — Carpet / Tile" && trRow.qty === 1 && trRow.unit === "EA"
      && /HALL ↔ BATH/.test(trRow.details[0].name) && /3'-0"/.test(trRow.details[0].note),
      "transition strips: one EA row per type pair, each door named with its width: " + JSON.stringify(trRow && trRow.details));
check(lab.join("|") === "Flooring — Carpet|Waste — Carpet (10%)|Base/molding — Carpet|Flooring — Tile|Base/molding — Tile|Wall area — perimeter × ceiling|Transition strips — Carpet / Tile",
      "row order: per type (floor, waste, base), then wall area, then transitions: " + lab.join("|"));
const LRX = new Function("return " + grabConst("FL_LABEL_RX") + ";")();
check(lab.every(l => LRX.test(l)) && LRX.test("Walls — interior (centreline)") && !LRX.test("Mass Excavation"),
      "FL_LABEL_RX owns every row kind the panel writes, so re-apply replaces them all");

console.log("what the flooring stops at: objects and deductions");
const namesD = ["flDeductSf", "flNetSf", "flDeductText"];
const D = new Function("const flFmt = " + grabConst("flFmt") + ";\n" + namesD.map(grab).join("\n") + "\nreturn { " + namesD.join(", ") + " };")();
const bath = { name: "MAIN BATH", area_sf: 53.1, perim_lf: 30, openings_lf: 2.7, doors: 1, type: 1,
               objects: [{ kind: "tub", area_sf: 9.2, deduct: true }, { kind: "cabinet", area_sf: 2.2, deduct: false }, { kind: "toilet", area_sf: 1.9, deduct: false }],
               deductions: [{ name: "clicked out", area_sf: 1.5 }] };
check(Math.abs(D.flDeductSf(bath) - 10.7) < 1e-9, "deducted = ticked objects + clicked-out shapes (9.2 + 1.5)");
check(Math.abs(D.flNetSf(bath) - 42.4) < 1e-9, "net flooring = area less the deductions (53.1 - 10.7)");
check(D.flNetSf({ area_sf: null }) === null, "no scale: net is null, not 0");
check(D.flNetSf({ area_sf: 5, objects: [{ kind: "tub", area_sf: 9, deduct: true }] }) === 0, "a deduction cannot make a room negative");
check(D.flDeductText(bath) === "tub 9.2 SF, clicked out 1.5 SF", "the deductions in words: " + D.flDeductText(bath));
const totD = P.flTotals(types2, [bath], flx);
check(Math.abs(totD[1].sf - 42.4) < 1e-9, "the type total uses the NET area");
const itD = P.flItems(types2, [bath], flx);
const flRow = itD.find(it => it.label === "Flooring — Tile");
check(flRow && flRow.qty === 42.4 && flRow.details[0].qty === 42.4 && /53\.1 SF less tub 9\.2 SF, clicked out 1\.5 SF/.test(flRow.details[0].note),
      "the flooring row and its room line carry the net figure and say what came out: " + JSON.stringify(flRow && flRow.details[0]));
check(/^53 SF − tub 9\.2 SF, clicked out 1\.5 SF = 42 SF · walls/.test(P.flRoomLine(bath, flx)), "room line: gross − deductions = net: " + P.flRoomLine(bath, flx));
check(/base = ring/.test("base = ring") && P.flWallLf(bath) === 27.3, "base footage is untouched by a deduction");
const DD = new Function("return " + grabConst("FL_DEDUCT_DEFAULTS") + ";")();
check(DD.tub === true && DD.shower === true && DD.cabinet === false && DD.appliance === false && DD.toilet === false && DD.furniture === false && DD.shape === false,
      "defaults: tubs and showers out, cabinets IN (Koby: not cabinets), everything else floored under");
const of = grab("flObjFrom");
check(of && /deduct: !!dflt\[kind\] && !o\.guessed, manual: false/.test(of), "a new object takes the panel default for its kind; a guessed kind is never deducted on its own");
const dc = grab("flDeductClick");
check(dc && /flPointIn\(o\.pts, px, py\)/.test(dc) && /o\.deduct = !o\.deduct; o\.manual = true;/.test(dc), "deduct click: a listed object under the click toggles, and is marked manual");
check(dc && /styles: all, closures: fl\.closures/.test(dc) && /j\.area_sf >= 0\.5 \* \(r\.area_sf \|\| 0\)/.test(dc),
      "...else the cell under the click is asked of the engine with EVERY line type as wall, and a cell that is most of the room is refused");
const ad = grab("flApplyDeductDefaults");
check(ad && /if \(!o\.manual\) o\.deduct = !!fl\.deductDefaults\[o\.kind\] && !o\.guessed;/.test(ad), "a changed default reaches every object nobody ticked by hand");
check(/"fl-close", "fl-deduct",/.test(SRC) && /const FL_TOOLS = \["fl-pick", "fl-draw", "fl-close", "fl-deduct"(, "fl-edit")?\]/.test(SRC), "the deduct tool is a POINT_TOOL and a flooring tool");
const dr2 = grab("drawFlooring");
check(dr2 && /if \(o\.deduct\) hatch\(o\.pts\);/.test(dr2) && /for \(const d of \(r\.deductions \|\| \[\]\)\) hatch\(d\.pts\);/.test(dr2), "deducted objects and clicked-out shapes are hatched on the sheet");
const rf3 = grab("renderFl");
check(rf3 && /take out of the flooring by default:/.test(rf3) && /FL_KIND_GROUPS/.test(rf3) && /fl-objs/.test(rf3) && /mode\("Deduct", "fl-deduct"/.test(rf3),
      "the panel has the per-kind defaults, a per-room object list with ticks, and the Deduct button");
const sp2 = grab("flSavePayload");
check(sp2 && /objects: \(r\.objects \|\| \[\]\)/.test(sp2) && /deductions: \(r\.deductions \|\| \[\]\)/.test(sp2) && /deductDefaults: Object\.assign/.test(sp2), "objects, deductions and the defaults persist");
const sv2 = grab("flApplySaved");
check(sv2 && /r\.objects/.test(sv2) && /r\.deductions/.test(sv2) && /sv\.deductDefaults/.test(sv2), "...and restore");

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
check(rf2 && /classify the \$\{flWallStyles\(fl\)\.length\} wall line types/.test(rf2) && /fl\.wallClass\[s\.style_id\] = sel\.value/.test(rf2),
      "one class select per wall line type, folded under the table");
check(rf2 && /waste %/.test(rf2) && /num\("waste_pct"\)/.test(rf2), "the types table has a waste % column");
check(rf2 && /ceiling height for rooms with no note/.test(rf2) && /fl-clg/.test(rf2), "a default ceiling input, and a per-room height box");
check(rf2 && /transition/.test(rf2) && /doorway.*to rooms not picked yet/.test(rf2), "the result line counts transitions and open doorways");
const lw = grab("flLabelWalls");
check(lw && /state\.labels\.set\(s\.style_id, name\)/.test(lw) && /memSave\(s\)/.test(lw), "relabelling goes through the label map and memSave, the style row's own path");

console.log("persistence of the new fields");
const sp = grab("flSavePayload");
check(sp && /waste_pct/.test(sp) && /ceiling_ft/.test(sp) && /ceiling_by/.test(sp) && /bridges/.test(sp)
      && /default_ceiling_ft/.test(sp) && /wallClass/.test(sp) && /wallFaces/.test(sp) && /wallRows/.test(sp),
      "the sidecar carries waste, ceilings, bridges, the default height and the wall classing");
const sv = grab("flApplySaved");
check(sv && /sv\.default_ceiling_ft/.test(sv) && /sv\.wallClass/.test(sv) && /r\.bridges/.test(sv) && /ceiling_by === "typed"/.test(sv),
      "...and restores them, a typed height staying typed");
const pk = grab("flPickAt");
check(pk && /j\.ceiling/.test(pk) && /bridges: \(j\.bridges_used \|\| \[\]\)/.test(pk), "a pick keeps the ceiling note and the door bridges the server used");
console.log("details reach the panel, the CSV and the payload");
check(/details: Array\.isArray\(it\.details\) \? it\.details : null/.test(SRC), "computeTakeoffRows carries details on calculator rows");
check(!!grab("detailNote") && /box\.appendChild\(detailNote\(it\)\)/.test(SRC), "the itemized panel prints a detail note under the row");
check(/`  part: \$\{dt\.name\}`/.test(SRC) && /`part of \$\{it\.label\}`/.test(SRC), "the CSV lists each part under its row, named as part of it");

console.log("panel, from source");
const pick = grab("flPickAt");
check(pick && /styles: flVisibleStyles\(\), closures: fl\.closures/.test(pick), "the pick sends visible styles and closures");
check(pick && /setStatus\(j\.why, "err"\)/.test(pick), "a failed pick prints the server's own sentence");
check(pick && /if \(fl\.busy\) return;/.test(pick), "no double pick while one is in flight");
const vis = grab("flVisibleStyles");
check(vis && /famShown\(id\)/.test(vis), "visible = famShown, the canvas's own answer");
const close = grab("flCloseClick");
check(close && /fl\.closures\.push\(seg\)/.test(close) && /state\.tool = "fl-pick"/.test(close),
      "close a gap: two clicks make a closure and hand the tool back to Pick");
const fin = grab("flFinishDraw");
check(fin && /source: "drawn"/.test(fin) && /stPolyArea\(pts\)/.test(fin), "draw outline: shoelace area, marked drawn");
const apply = grab("flApplyItems");
check(apply && /filter\(it => !FL_LABEL_RX\.test\(it\.label\)\)/.test(apply), "re-apply replaces only its own rows");
check(apply && /store\.setItem\(priceKey\(it\.label\), String\(it\.unit_price\)\)/.test(apply),
      "a typed $/SF or $/LF is written to the price book, which is what the export prices from");
check(apply && /calcStateDirty\(\)/.test(apply), "...and marks the sidecar dirty");
const saved = grab("flApplySaved");
check(saved && /if \(state\.calcFlApplied \|\| !state\.calcSavedLoaded\) return;/.test(saved), "restore runs once, after the sidecar answered");
check(saved && /sv\.closures/.test(saved) && /sv\.types/.test(saved) && /sv\.rooms/.test(saved), "restore brings back types, rooms and closures");
check(/out\.fl = flSavePayload\(fl\);/.test(SRC) && /else if \(saved\.fl\) out\.fl = saved\.fl;/.test(SRC),
      "calcstate carries fl, and keeps a saved fl when the panel was never opened");
check(/state\.calcStApplied = false;\n  state\.calcFlApplied = false;/.test(SRC), "calcFlApplied resets where calcStApplied does");
check(/stApplySaved\(\);\n  flApplySaved\(\);/.test(SRC), "flApplySaved runs where stApplySaved runs");
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
const recompute = new Function(grab("stPolyArea") + ";" + grab("stPolyPerim") + ";" + grab("flEditRecompute") + "; return flEditRecompute;")();
const rr = recompute({ pts: [[0, 0], [10, 0], [10, 10], [0, 10]], openings_lf: 3, ceiling_ft: 9, objects: [{ deduct: true, area_sf: 1 }] }, 0.5);
check(rr.area_pt2 === 100 && rr.perim_pt === 40 && rr.area_sf === 25 && rr.perim_lf === 20, "recompute: area and perimeter from the polygon at the scale (100 pt² → 25 SF, 40 pt → 20 LF)");
check(rr.source === "edited" && rr.openings_lf === 3 && rr.ceiling_ft === 9 && rr.objects.length === 1, "the source becomes 'edited'; door openings, ceiling and objects stay as picked");
check(recompute({ pts: sq }, null).area_sf === null, "no scale: area_sf stays null, never 0");
const md = /addEventListener\("mousedown", \(e\) => \{\n  if \(state\.tool !== "fl-edit" \|\| e\.target !== cv \|\| e\.button !== 0\) return;[\s\S]*?\n\}, true\);/.exec(SRC);
check(!!md, "the editor's mousedown runs in the CAPTURE phase on the window");
check(md && /r\.pts\.splice\(ed\.i \+ 1, 0, ed\.at\)/.test(md[0]), "a press on an edge inserts a corner at the projection, right after the edge's first point");
check(md && /e\.preventDefault\(\); e\.stopImmediatePropagation\(\);/.test(md[0]), "and stops the sheet's own pan / vertex-drag from seeing it");
check(md && /if \(!ed\) return;/.test(md[0]), "a press off the outline is left to the pan (and the click that selects another room)");
const mu = /addEventListener\("mouseup", \(e\) => \{\n  if \(state\.tool !== "fl-edit"\) return;[\s\S]*?\n\}, true\);/.exec(SRC);
check(mu && /flEditRecompute\(r, flRoomFpp\(r\)\)/.test(mu[0]) && /flApplyItems\(\); renderFl\(\); draw\(\);/.test(mu[0]), "mouseup recomputes the room at ITS OWN scale (an inset room stays at the inset caption) and re-applies the takeoff rows");
const rmv = grab("flEditRemoveVertex");
check(rmv && /if \(r\.pts\.length <= 3\) return setStatus\("a room keeps at least 3 corners"/.test(rmv), "a corner cannot be removed below 3");
check(/if \(\(e\.key === "Delete" \|\| e\.key === "Backspace"\) && state\.tool === "fl-edit"\)/.test(SRC), "Delete / Backspace removes the hovered corner while editing");
check(/if \(state\.tool !== "fl-edit"\) return;\n  const r = flEditRoom\(\);[\s\S]{0,200}flEditRemoveVertex\(vi\)/.test(SRC), "double-click on a corner removes it");
const rl = grab("flRoomLine");
check(rl && /r\.source === "edited"\) s \+= " \\u00b7 shape edited by hand"/.test(rl), "the room line says 'shape edited by hand'");
const asv = grab("flApplySaved");
check(asv && /r\.source === "drawn" \|\| r\.source === "edited" \? r\.source : "click"/.test(asv), "a restored room keeps 'edited'");
const rfe = grab("renderFl");
check(rfe && /mode\("Edit shape", "fl-edit"/.test(rfe), "the panel has the Edit shape mode");
check(rfe && /mk\("button", "mini", "edit"\)/.test(rfe) && /flEditSelect\(k\)/.test(rfe), "each room row has its own edit button that selects that room");
check(rfe && /top\.append\(dot, nm, sel, clg, ed, rm\)/.test(rfe), "placed before the remove button");
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
