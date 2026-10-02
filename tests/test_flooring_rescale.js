// THE FLOORING ROOMS FOLLOW THE SCALE IN FORCE (check 4B, 2026-09-27). Rooms picked at 1/4" on
// a sheet then corrected to 1/8" stayed 177.9 SF where 711.6 follows, while the status said
// every quantity on the sheet was recalculated. flRescale() measures every room again from its
// own outline at the scale now in force - except a room measured at an inset's own caption.
//
// run: node tests/test_flooring_rescale.js
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "..", "webui", "style.css"), "utf8");
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
check(!!fnSrc("flRescale") && !!fnSrc("rowsFollowScale"), "flRescale and rowsFollowScale exist");
check(/flRescale\(\)/.test(fnSrc("rowsFollowScale")) && /trRecalcAdded\(\)/.test(fnSrc("rowsFollowScale")),
  "rowsFollowScale brings the flooring rooms and the trench runs to the scale in force");
check(/rowsFollowScale\(false\)/.test(fnSrc("scaleRepaint")), "a typed or put-back scale calls it (scaleRepaint)");
const pick = js.indexOf("every quantity on this sheet recalculated");
check(pick > 0 && /rowsFollowScale\(false\)/.test(js.slice(pick, pick + 400)), "a printed scale picked from the candidates calls it too");
// (selectPage calls scaleMemApply too, on a page that has no result yet: nothing to follow there)
const lr = fnSrc("loadResult");
const load = lr.indexOf("scaleMemApply();");
const follow = lr.indexOf("rowsFollowScale(true);");
check(load > 0 && follow > load && follow < lr.indexOf("renderSheetHead();") && lr.indexOf("calcStateLoad(gen)") < load,
  "and every page load does, once the job's rows are back and this seat's scale is in force, before the first paint "
  + "(the job's rows may stand on another seat's scale)");
check(/out\.rows_scale = rs/.test(fnSrc("calcStatePayload")) && /saved\.rows_scale/.test(fnSrc("calcStatePayload")),
  "the job's record says which scale its rows stand on");
{
  // as a page loads: the trench runs are calculated again only when the record's rows stand on
  // another scale; the flooring rooms know their own scale and are always looked at
  const seen = { tr: 0, fl: 0, status: [] };
  const R = (state) => new Function("state", "seen",
    "const trRecalcAdded = () => { seen.tr++; }; const flRescale = () => { seen.fl++; return state._moved || 0; };\n"
    + "const setStatus = (m, k) => seen.status.push([m, k]); const cfScaleTxt = (v) => String(v);\n"
    + fnSrc("flScreenScale") + "\n" + fnSrc("rowsFollowScale") + "\nreturn { rowsFollowScale };")(state, seen);
  R({ result: { scale: { ft_per_paper_inch: 4, text: "1/4\" = 1'-0\"" } }, calcSaved: { rows_scale: 8 }, _moved: 2 }).rowsFollowScale(true);
  check(seen.tr === 1 && seen.fl === 1 && seen.status.length === 1 && /2 flooring rooms were measured at another scale/.test(seen.status[0][0])
    && /1\/4" = 1'-0"/.test(seen.status[0][0]) && seen.status[0][1] === "warn",
    `rows saved at 1/8" by another seat, this sheet at 1/4": both are brought to 1/4" and the status says so (${JSON.stringify(seen.status)})`);
  seen.tr = 0; seen.fl = 0; seen.status = [];
  R({ result: { scale: { ft_per_paper_inch: 4 } }, calcSaved: { rows_scale: 4 }, _moved: 0 }).rowsFollowScale(true);
  check(seen.tr === 0 && seen.fl === 1 && seen.status.length === 0, "rows saved at this seat's scale: nothing is calculated again, nothing is said");
  seen.tr = 0; seen.fl = 0;
  R({ result: { scale: { ft_per_paper_inch: 4 } }, calcSaved: {}, _moved: 0 }).rowsFollowScale(true);
  check(seen.tr === 0 && seen.fl === 1, "a record from before the key: the rooms are looked at, the trench runs are left as they are");
  seen.tr = 0; seen.fl = 0; seen.status = [];
  R({ result: { scale: { ft_per_paper_inch: 8 } }, calcSaved: { rows_scale: 8 }, _moved: 3 }).rowsFollowScale(false);
  check(seen.tr === 1 && seen.fl === 1 && seen.status.length === 0, "a scale change in this seat always calculates both again (its own status line says so)");
}

const calls = { applied: 0, rendered: 0, synced: 0 };
const mk = (state, panelHidden) => new Function("state", "calls", "panelHidden",
  "const FL_LABEL_RX = /^(Flooring|Base\\/molding|Waste|Wall area|Transition strips|Walls) \\u2014 /;\n"
  + "const $ = (id) => ({ hidden: panelHidden });\n"
  + "const flApplyItems = () => { calls.applied++; }; const renderFl = () => { calls.rendered++; };\n"
  + "const flSync = () => { calls.synced++; state.fl = state._saved; state.fl.result = state.result; return state.fl; };\n"
  + fnSrc("flScreenScale") + "\n" + fnSrc("flRescale") + "\nreturn { flRescale };")(state, calls, panelHidden);

// the checker's own room: 57,624.8 pt2, picked at 4 ft per inch (1/4" = 1'-0")
const result = { scale: { ft_per_paper_inch: 8 } };
const bedroom = () => ({ name: "PRIMARY BEDROOM", area_pt2: 57624.8, area_sf: 57624.8 * (4 / 72) ** 2, perim_pt: 956.0, perim_lf: 956.0 * 4 / 72,
  openings_lf: 3.0, holes_sf: 2.0, scale_ft_per_in: 4, scale_by: "on screen",
  objects: [{ kind: "tub", area_sf: 14.1, w_ft: 4.96, h_ft: 2.92, deduct: true }], deductions: [{ name: "clicked out", area_sf: 6.0 }] });
const inset = () => ({ name: "INSET ROOM", area_pt2: 14379.4, area_sf: 14379.4 * (8 / 72) ** 2, perim_pt: 480, perim_lf: 480 * 8 / 72,
  openings_lf: 0, holes_sf: 0, scale_ft_per_in: 8, scale_by: "inset caption SCALE: 1/8\" = 1'-0\"", objects: [], deductions: [] });
const drawn = () => ({ name: "DRAWN", area_pt2: 10000, area_sf: 10000 * (4 / 72) ** 2, perim_pt: 400, perim_lf: 400 * 4 / 72,
  openings_lf: 0, holes_sf: 0, scale_ft_per_in: null, scale_by: null, objects: [], deductions: [] });
let state = { result, extraItems: [], calcSavedLoaded: true, fl: { result, rooms: [bedroom(), inset(), drawn()] } };
let n = mk(state, false).flRescale();
const [b, i, d] = state.fl.rooms;
check(n === 2 && calls.applied === 1 && calls.rendered === 1, `two rooms moved, the rows were rebuilt once and the open panel redrawn (${n}, ${calls.applied}, ${calls.rendered})`);
check(Math.abs(b.area_sf - 711.4) < 0.1 && Math.abs(b.perim_lf - 956.0 * 8 / 72) < 1e-9, `the bedroom is measured again at 1/8": ${b.area_sf.toFixed(1)} SF (177.9 at 1/4"), walls ${b.perim_lf.toFixed(1)} LF`);
check(Math.abs(b.openings_lf - 6.0) < 1e-9 && Math.abs(b.holes_sf - 8.0) < 1e-9 && Math.abs(b.objects[0].area_sf - 56.4) < 1e-9
  && Math.abs(b.objects[0].w_ft - 9.92) < 1e-9 && Math.abs(b.deductions[0].area_sf - 24.0) < 1e-9,
  "its door openings, holes, fixtures and clicked-out shapes follow: feet x 2, square feet x 4");
check(b.scale_ft_per_in === 8 && b.scale_by === "on screen", "and it records the scale it stands on now");
check(Math.abs(i.area_sf - 14379.4 * (8 / 72) ** 2) < 1e-9 && /^inset caption/.test(i.scale_by), "a room measured at an inset's own caption keeps that scale");
check(Math.abs(d.area_sf - 10000 * (8 / 72) ** 2) < 1e-9 && d.scale_ft_per_in === 8, `a hand-drawn room (no scale recorded) follows too, from what its own numbers imply: ${d.area_sf.toFixed(1)} SF`);
{
  // the round trip is exact: the server's own feet (rounded its way) are scaled by the ratio, never
  // replaced by the outline times the scale (53.1 LF came back 53.07, a wall-area line 0.3 SF off)
  const srv = { name: "BED", area_pt2: 57624.8, area_sf: 177.9, perim_pt: 955.2, perim_lf: 53.1, openings_lf: 2.5, holes_sf: 0,
                scale_ft_per_in: 4, scale_by: "on screen", objects: [], deductions: [] };
  const st2 = { result: { scale: { ft_per_paper_inch: 8 } }, extraItems: [], calcSavedLoaded: true, fl: { result: null, rooms: [srv] } };
  st2.fl.result = st2.result;
  mk(st2, true).flRescale();
  const at8 = [srv.area_sf, srv.perim_lf];
  st2.result.scale.ft_per_paper_inch = 4;
  mk(st2, true).flRescale();
  check(Math.abs(at8[0] - 711.6) < 1e-9 && Math.abs(at8[1] - 106.2) < 1e-9 && srv.area_sf === 177.9 && srv.perim_lf === 53.1 && srv.openings_lf === 2.5,
    `4 -> 8 -> 4 returns the very numbers the pick gave: ${srv.area_sf} SF, ${srv.perim_lf} LF (at 1/8": ${at8[0]} / ${at8[1]})`);
}
// the same scale again: nothing moves, nothing is rebuilt
calls.applied = 0; calls.rendered = 0;
check(mk(state, false).flRescale() === 0 && calls.applied === 0, "the same scale again moves nothing");
// back to 1/4": the first numbers return exactly
state.result.scale.ft_per_paper_inch = 4;
mk(state, true).flRescale();
check(Math.abs(state.fl.rooms[0].area_sf - 57624.8 * (4 / 72) ** 2) < 1e-9 && Math.abs(state.fl.rooms[0].objects[0].area_sf - 14.1) < 1e-9 && calls.rendered === 0,
  "put back to 1/4\": the first numbers return; a closed panel is not drawn");
// a fresh session: the panel was never opened, the rooms are in the job's record
calls.synced = 0; calls.applied = 0;
state = { result: { scale: { ft_per_paper_inch: 8 } }, extraItems: [{ label: "Flooring — Carpet", qty: 738.4, unit: "SF" }], calcSavedLoaded: true,
          fl: null, _saved: { rooms: [bedroom()] } };
n = mk(state, true).flRescale();
check(n === 1 && calls.synced === 1 && calls.applied === 1 && Math.abs(state.fl.rooms[0].area_sf - 711.4) < 0.1,
  "a session that never opened the Flooring panel brings the saved rooms up and measures them again");
state = { result: { scale: { ft_per_paper_inch: 8 } }, extraItems: [{ label: "Site Cut", qty: 1, unit: "CY" }], calcSavedLoaded: true, fl: null, _saved: { rooms: [bedroom()] } };
calls.synced = 0;
check(mk(state, true).flRescale() === 0 && calls.synced === 0, "no flooring rows in the takeoff: the panel's state is left alone");
state = { result: { scale: null }, extraItems: [], calcSavedLoaded: true, fl: { result: null, rooms: [bedroom()] } };
check(mk(state, true).flRescale() === 0, "no scale in force: nothing moves");

// the room's name box
check(/\.fl-row-top \.fl-name \{[^}]*min-width: 120px/.test(css) && /\.fl-row-top \{[^}]*flex-wrap: wrap/.test(css),
  "a room's name keeps a readable width and the row's controls wrap under it");

console.log("\n" + fails + " failure(s)");
process.exit(fails ? 1 : 0);
