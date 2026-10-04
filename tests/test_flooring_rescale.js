// THE FLOORING ROOMS AND THE SCALE IN FORCE (check 4B, 2026-09-27; ruling P, 2026-10-02). Rooms picked at 1/4"
// on a sheet then corrected to 1/8" stayed 177.9 SF where 711.6 follows, with nothing said. The rooms are the
// server's now: a sheet whose scale on screen is not the one its rooms stand on says so and offers one click to
// measure them again; nothing changes by itself.
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

// THE ROOMS STAND ON THE SCALE THEY WERE MEASURED AT (ruling P/Q, 2026-10-02): the server's record. A sheet whose
// scale on screen is not that one SAYS SO (Approve records the scale on screen; the server measures the rooms again)
// and nothing is changed or written by itself: a page that merely loads with another seat's typed scale writes nothing.
const mk = (state) => new Function("state",
  "const $ = (id) => ({ hidden: true });\n" + fnSrc("flScreenScale") + "\n" + fnSrc("flRescale") + "\nreturn { flRescale };")(state);
const result = { scale: { ft_per_paper_inch: 8 } };
const room = (how, ft, area) => ({ name: how, uid: how, area_sf: area, scale: { how, ft }, c: { net_sf: area } });
let state = { result, fl: { result, rooms: [room("sheet", 4, 177.9), room("view", 8, 50), room("chosen", 2, 10), room("sheet", 8, 30)] } };
const before = JSON.stringify(state.fl.rooms);
let n = mk(state).flRescale();
check(n === 1 && state.fl.rescaleOffer && state.fl.rescaleOffer.n === 1 && state.fl.rescaleOffer.ft === 8,
  `one room measured at the sheet's scale stands on 1/4" with the sheet on screen at 1/8": offered, not applied (${JSON.stringify(state.fl.rescaleOffer)})`);
check(JSON.stringify(state.fl.rooms) === before, "no room's figures moved: they are the server's until the estimator acts");
check(!/fetch\(|flAct\(/.test(fnSrc("flRescale")), "flRescale sends nothing");
state.fl.rooms[0].scale.ft = 8;
check(mk(state).flRescale() === 0 && state.fl.rescaleOffer === null, "the same scale: nothing offered");
state = { result: { scale: null }, fl: { result: null, rooms: [room("sheet", 4, 1)] } };
state.fl.result = state.result;
check(mk(state).flRescale() === 0, "no scale in force: nothing offered");
const panel = fnSrc("renderFl");
// critic 4, B1: sheet_rescale took the browser's number as the sheet's scale. The rooms follow the sheet's RECORDED
// scale on the server; the panel says so and points at Approve, and sends no number
check(/if \(fl\.rescaleOffer\)/.test(panel) && /set it on the sheet.s scale to record it/.test(panel)
  && !/kind: "sheet_rescale"[^}]*ft:/.test(panel),
  "the panel says the rooms stand on the sheet's recorded scale and that Approve records the one on screen; it sends no scale");

// the room's name box
check(/\.fl-row-top \.fl-name \{[^}]*min-width: 120px/.test(css) && /\.fl-row-top \{[^}]*flex-wrap: wrap/.test(css),
  "a room's name keeps a readable width and the row's controls wrap under it");

console.log("\n" + fails + " failure(s)");
process.exit(fails ? 1 : 0);
