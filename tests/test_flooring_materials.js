// THE FLOORING PANEL OVER THE JOB'S DOCUMENT (rulings P-W, 2026-10-02; critic 3). The panel mirrors what the
// server holds and computed - rooms, types, figures, checks, the takeoff rows - and every write is ONE ACT over
// the version it read. It computes no figure and owns no state. The server's side is tests/test_fl_acts.py
// (routes) and tests/test_fl_job.py (the model and the money). Synthetic numbers only.
//
// Dependency-free:  node tests/test_flooring_materials.js
"use strict";
const H = require("./_fl_harness.js");
let fails = 0;
function check(cond, msg) {
  console.log((cond ? "  ok   " : "  FAIL ") + msg);
  if (!cond) fails++;
}
const sq = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
const reply = (body, status) => ({ ok: (status || 200) < 400, status: status || 200, json: async () => body });
// the document as GET /api/job/{id}/flooring answers it: one sheet, one room with its server figures
function docReply(version, extra) {
  const room = { uid: "r1", name: "GALLERY", source: "click", pts: sq(0, 0, 288, 144), area_pt2: 41472, perim_pt: 864,
                 openings: [{ id: "o1", x: 100, y: 0, w_pt: 54, w_ft: null, by: "from the swing", why: null }],
                 objects: [{ kind: "tub", area_pt2: 900, w_pt: 60, h_pt: 30, deduct: true, pts: sq(10, 10, 70, 40) }], deductions: [],
                 bridges: [], doors_unplaced: [], type: "tA", scale: { how: "view", ft: 4, fpi: 4, text: "1/4", multi: true } };
  return Object.assign({ ok: true, version, stored: true, shared: false,
    est: { types: [{ id: "tA", name: "Carpet", color: "#4e9af1", unit: "SF", rate_cents: 450, waste_pct: "7" }], lines: [], notes: [],
           removal: { on: false, desc: "R", rate_cents: null }, prep: { on: false, desc: "P", rate_cents: null }, walls: {} },
    sheets: { "0": { rooms: [room], aids: { closures: [], wallLines: [], wallAdd: [], wallDrop: [], wallStyles: [1], deductDefaults: {} }, last_type: "tA" } },
    computed: { lines: [{ id: "floor:tA", desc: "Supply & Install 137 s/f Carpet", qty: "137", rate: "4.50", amount: "616.50" }], total: "616.50",
                checks: [{ id: "scale:p0:r1", kind: "scale", open: true, uid: "r1", page: 0, text: "scale" },
                         { id: "open:p0:r1", kind: "openings", open: true, uid: "r1", page: 0, count: 1, text: "openings" }],
                open: [], unassigned: [],
                rooms: { "p0:r1": { area_sf: 128, net_sf: 125.2, deduct_sf: 2.8, deduct_text: "tub 2.8 SF", perim_lf: 48, base_lf: 45,
                                    base: { lf: 45, ring_lf: 48, openings: [{ id: "o1", x: 100, y: 0, w_lf: 3, on_edge: true, by: "from the swing" }],
                                            openings_lf: 3, out_lf: 3, off_runs: 0, off_lf: 0, legacy: false, unplaced: 0 },
                                    objects_sf: [2.8], objects_wh: [[3.3, 1.7]], deductions_sf: [], wall: null,
                                    scale_options: [{ fpi: 4, text: "1/4", ft: 4, net_sf: 125.2, current: true }, { fpi: 2, text: "1/2", ft: 2, net_sf: 31.3, current: false }],
                                    scale_words: "1/4 - the view title nearest it (an inference)", scale_digest: "dg-s", open_digest: "dg-o" } },
                pages: { "0": { totals: [{ i: 0, id: "tA", name: "Carpet", color: "#4e9af1", unit: "SF", sf: 125.2, lf: 45, rooms: 1, net: 125.2,
                                           order: 134, waste_set: true, waste_pct: 7, wall_sf: 0, wall_rooms: 0, sy: 13.9 }],
                                 items: [{ label: "Flooring — Carpet", qty: 134, unit: "SF", src: "flooring", details: [] },
                                         { label: "Base/molding — Carpet", qty: 45, unit: "LF", src: "flooring", details: [] }],
                                 transitions: { transitions: [], open: 0 } } } } }, extra || {});
}
function opened(version) {
  const P = H.fresh();
  P.state.job = "J1"; P.state.page = 0; P.state.result = { styles: [{ style_id: 1 }], chains: [], scale: { ft_per_paper_inch: 4, text: "1/4\" = 1'-0\"" } };
  P.setReply((u, o) => reply(docReply(version || 3)));
  return P;
}
const settle = () => new Promise(r => setTimeout(r, 0));

(async () => {
console.log("B1 / Q. opening a job writes nothing");
{
  const P = opened(3);
  P.flSync();
  await settle(); await settle();
  check(P.fetchLog.length === 1 && P.fetchLog[0].method === "GET" && /\/api\/job\/J1\/flooring$/.test(P.fetchLog[0].url),
        "the panel's first look is one GET of the job's flooring - no PUT, no act: " + JSON.stringify(P.fetchLog.map(f => f.method + " " + f.url)));
  check(P.calls.dirty === 0, "and the page record is not marked dirty (no save of any kind is queued)");
  const fl = P.state.fl;
  check(fl.rooms.length === 1 && fl.rooms[0].type === 0 && fl.rooms[0].typeId === "tA" && fl.types[0].name === "Carpet",
        "the sheet's rooms and the job's types come from the document; a room's type is the document's");
  check(P.flNetSf(fl.rooms[0]) === 125.2 && P.flWallLf(fl.rooms[0]) === 45 && P.flBase(fl.rooms[0]).ring_lf === 48
        && P.flDeductSf(fl.rooms[0]) === 2.8 && fl.rooms[0].objects[0].area_sf === 2.8,
        "every figure on screen is the server's (r.c): net 125.2 SF, base 45 LF, the tub's 2.8 SF");
  check(P.state.extraItems.length === 2 && P.state.extraItems.every(it => it.src === "flooring") && P.state.extraItems[0].qty === 134,
        "the takeoff's flooring rows are the server's, as they are");
  for (let i = 0; i < 3; i++) { P.flApplyItems(); P.flMirror(); P.flRescale(); }
  check(P.fetchLog.length === 1 && P.calls.dirty === 0, "repainting, mirroring and looking at the scale again write nothing");
  const cs = H.grab("calcStatePayload");
  check(cs && !/flSavePayload/.test(cs) && /it\.src !== "flooring"/.test(cs), "the page record carries no flooring part and no server row");
  check(!/function flSyncAssign|function flJobSave|function flSavePayload|function flApplySaved|function flJobBind/.test(H.SRC),
        "no code that rewrote the job's assignments or saved the panel's state is left");
}

console.log("P. every control sends one act over the version it read");
{
  const P = opened(7);
  P.flSync();
  await settle(); await settle();
  P.setReply((u, o) => reply(docReply(8, { said: "rate saved" })));
  const t = P.state.fl.types[0];
  await P.flSetPrice(t, "floor", "49.75");
  const a = P.fetchLog[P.fetchLog.length - 1];
  check(a.method === "POST" && /\/flooring\/act$/.test(a.url) && a.body.version === 7
        && JSON.stringify(a.body.act) === JSON.stringify({ kind: "type_set", id: "tA", field: "rate", value: "49.75" }),
        "a typed rate is the act type_set rate \"49.75\" over version 7: " + JSON.stringify(a.body));
  check(t.rate_cents === 450 && P.flJobFor().version === 8, "the panel changes nothing itself: the reply (version 8) is what it shows");
  check(P.said.some(s => s[0] === "rate saved"), "and says what the server said");
  await P.flSetUnit(t, "SY");
  check(P.fetchLog[P.fetchLog.length - 1].body.act.field === "unit" && P.fetchLog[P.fetchLog.length - 1].body.version === 8,
        "a unit change is an act (the server converts the rate); the next act carries version 8");
  const rf = H.grab("renderFl");
  for (const [rx, what] of [[/kind: "type_set", id: t\.id, field: "color"/, "a type's colour"], [/kind: "type_set", id: t\.id, field: "name"/, "its name"],
                            [/kind: "type_set", id: t\.id, field: "waste"/, "its waste"], [/kind: "type_del", id: t\.id/, "removing a type"],
                            [/kind: "type_add"/, "adding one"], [/kind: "room_type", page: state\.page, uid: r\.uid/, "a room's type"],
                            [/kind: "room_del", page: state\.page, uid: r\.uid/, "removing a room"], [/kind: "room_ceiling"/, "a room's ceiling"],
                            [/kind: "opening_width", page: state\.page, uid: r\.uid, oid: o\.id/, "an opening's width"],
                            [/kind: "opening_del", page: state\.page, uid: r\.uid, oid: o\.id/, "removing an opening"],
                            [/kind: "object_toggle"/, "a fixture in or out"], [/kind: "deduct_del"/, "a shape back in"],
                            [/kind: "aids_set", page: state\.page, key: "deductDefaults"/, "the deduction defaults"],
                            [/kind: "aids_set", page: state\.page, key: "zoneScales"/, "a work area's scale"]]) {
    check(rx.test(rf), what + " is an act");
  }
  const re = H.grab("renderFlEstimate");
  check(re && /kind: "area_set"/.test(re) && /kind: "line_set"/.test(re) && /kind: "line_move"/.test(re) && /kind: "line_del"/.test(re)
        && /kind: "line_add"/.test(re) && /kind: "notes_set"/.test(re), "the estimate's lines, area lines and notes are acts");
  check(!/\.oninput = \(\) => \{[^}]*flAct/.test(rf + re), "a text box acts once, when it is left (onchange) - never per keystroke");
}

console.log("M3. a stale tab writes nothing more");
{
  const P = opened(3);
  P.flSync();
  await settle(); await settle();
  P.setReply(() => reply({ detail: "This estimate was changed on another computer. Reload to see it.", version: 9 }, 409));
  await P.flAct({ kind: "room_name", page: 0, uid: "r1", name: "X" });
  const n = P.fetchLog.length;
  check(P.flJobFor().stale === true && P.said.some(s => /changed on another computer/.test(s[0])), "the server refuses the stale act: the tab says so and goes read-only");
  await P.flAct({ kind: "room_del", page: 0, uid: "r1" });
  check(P.fetchLog.length === n, "and sends nothing more");
  check(/fl_est_version: typeof flJobFor === "function" && flJobFor\(\) \? flJobFor\(\)\.version : null/.test(H.SRC),
        "every approve carries the version this tab holds (the server refuses a stale one)");
}

console.log("R. checks close by acts: a room's scale and its openings, for exactly what is shown");
{
  const P = opened(3);
  P.flSync();
  await settle(); await settle();
  P.setReply(() => reply(docReply(4)));
  const r = P.state.fl.rooms[0];
  await P.flRoomSetScale(r, r.c.scale_options[1]);
  const a = P.fetchLog[P.fetchLog.length - 1];
  check(/\/page\/0\/rooms\/r1\/scale\/acknowledge$/.test(a.url) && a.body.fpi === 2 && a.body.digest === "dg-s" && a.body.version === 3,
        "a scale acknowledged through the room's own route, as the value chosen and the digest of what was shown: " + JSON.stringify(a.body));
  await P.flAckOpenings(P.state.fl.rooms[0]);
  const b = P.fetchLog[P.fetchLog.length - 1];
  check(/\/rooms\/r1\/openings\/acknowledge$/.test(b.url) && b.body.count === 1 && b.body.digest === "dg-o",
        "a room's openings confirmed with the count shown (1) and its digest");
  const rf = H.grab("renderFl");
  check(/for \(const o of \(r\.c && r\.c\.scale_options\) \|\| \[\]\)/.test(rf) && /\$\{lbl\}: \$\{flFmt\(o\.net_sf\)\} SF/.test(rf),
        "the room row offers EVERY printed scale side by side with its area - never a lone confirm (critic 3, M1)");
  check(/confirm \$\{ops\.length\} opening/.test(rf) && /flAckOpenings\(r\)/.test(rf), "the openings list carries its one confirmation, with the count");
  check(!/function flAck\(/.test(H.SRC) && !/\backs\b/.test(H.grab("renderFlEstimate")) && !/est\.acks/.test(H.SRC), "no flag is ever written by the page");
}

console.log("T. the generic price book never touches a flooring row");
{
  const P = H.fresh();
  check(P.priceLabel("Flooring — Carpet") !== "Flooring — Carpet" && P.priceKey("Base/molding — Carpet") !== "price:Base/molding — Carpet",
        "a flooring row has no key in the book: a price typed by its label (or one a 2.0 build left) prices nothing");
  check(P.priceLabel("Site Cut – Island") === "Site Cut" && P.priceLabel("Water main") === "Water main", "other rows are priced as before");
  check(/if \(flIsRowLabel\(it\.label\)\) \{\s*pin\.disabled = true;/.test(H.SRC) && /unit_price: flIsRowLabel\(it\.label\) \? null/.test(H.SRC),
        "its price box is shut ('estimate') and an approve sends it no price");
  check(/!flIsRowLabel\(lab\) && !store\.getItem\(priceKey\(lab\)\)\) store\.setSession/.test(H.SRC)
        && /const price = flIsRowLabel\(it\.label\) \? null :/.test(H.SRC) && /const p = flIsRowLabel\(lab\) \? NaN :/.test(H.SRC),
        "a page record's saved label prices (a 2.0 page's) never price a flooring row, in the panel, the CSV or the record (the 5-opens run caught it)");
}

console.log("pick aids: walls by hand, gaps, drawn walls - acts on the sheet");
{
  const P = opened(3);
  P.flSync();
  await settle(); await settle();
  P.setReply(() => reply(docReply(4)));
  const fl = P.state.fl;
  P.state.tool = "fl-stroke";
  P.flCloseClick(100, 200, true); P.flCloseClick(100, 260, true);
  await settle(); await settle();
  const a = P.fetchLog[P.fetchLog.length - 1];
  check(a.body && a.body.act.kind === "aids_add" && a.body.act.key === "wallLines" && JSON.stringify(a.body.act.value) === "[100,200,100,260]",
        "two clicks draw one wall: the act aids_add wallLines, appended on the server (two quick strokes never overwrite each other)");
  check(P.state.tool === "fl-stroke", "the tool stays armed for the next wall");
  P.flCloseClick(10, 10); P.flCloseClick(10, 40);
  await settle(); await settle();
  check(P.fetchLog[P.fetchLog.length - 1].body.act.kind === "aids_add" && P.fetchLog[P.fetchLog.length - 1].body.act.key === "closures", "a gap closed is the act aids_add closures");
  const wc = H.grab("flWallClick");
  check(/kind: "aids_line", page: state\.page, id, to/.test(wc) && /kind: "walls_mem", sig/.test(wc) && /setFamVis\(sid, on\)/.test(wc),
        "Shift+click a line is an act on the sheet's lines by hand; a type's tick shows it and keeps it in the job's memory (an act)");
  check(/your own wall ticks on this job/.test(H.grab("flWallStyleIds")), "Show walls only offers the job's own ticks first");
  check(/if \(!untick && famShown\(sid\)\)/.test(wc) && /const on = !untick;/.test(wc) && /flWallClick\(px, py, !!e\.shiftKey, !!\(e\.ctrlKey \|\| e\.metaKey\)\)/.test(H.SRC),
        "a plain click only ticks a type (a second wall of a ticked type stays ticked); Ctrl+click unticks it (critic 3, minor 1)");
  const pk = H.grab("flPickAt");
  check(/kind: "room_pick", page: state\.page, x: px, y: py, styles: flVisibleStyles\(\)/.test(pk) && /zone: flZoneAt\(px, py\)/.test(pk)
        && !/closures:/.test(pk), "a pick is one act: the click, the line types showing, the work area; the server adds the sheet's own aids");
  P.state.workZones = [[10, 10, 100, 80]];
  check(P.flZoneAt(50, 40)[3] === 80 && P.flZoneAt(150, 40) === null, "flZoneAt: the box holding the click, or none");
}

console.log("W. a door with no record: one click on the wall");
{
  const oc = H.grab("flOpenClick");
  check(/kind: "opening_add", page: state\.page, uid: r\.uid/.test(oc) && /reads its width off the swing/.test(oc),
        "Base: add an opening is one act on the wall; the server reads the width off a swing drawn there with no door record");
}

console.log("money as typed");
{
  const P = H.fresh();
  check(P.flCents("5.555") === 556 && P.flCents("49.75") === 4975 && P.flCents("$1,080") === 108000 && P.flCents("12,345.50") === 1234550
        && P.flCents("-1") === null && P.flCents("abc") === null, "typed money -> cents exactly, half up past the cent; junk is no price");
  check(P.flCents("49,75") === null, "\"49,75\" is refused, never $4,975 (critic 3, minor 3)");
  check(P.flMoney(955200) === "9,552.00" && P.flMoney(5) === "0.05", "cents shown as the document prints them");
}

console.log(fails ? `\n${fails} FAILURE(S)` : "\nall passed");
process.exit(fails ? 1 : 0);
})();
