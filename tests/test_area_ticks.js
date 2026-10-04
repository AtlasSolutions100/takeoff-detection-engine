// AREA TICKS NEVER LAND ON ANOTHER FILL (2026-10-02, the areas fill-rule fix's follow-up).
// An Areas tick used to be stored as the group's POSITION in a list ordered by area. Any re-detect
// reorders it - on the fill-rule fix 345 positions on 154 corpus pages came to name a different fill -
// and a ticked row then priced that other fill without a word. Now a tick carries the detection it
// was made on (engine_sig) and a key for the fill (layer, fill colour, geometry fingerprint):
//   * ticks from another detection (or a build that stored positions only) are not applied; the
//     panel says "Areas were measured again. Tick the rows you want." until a tick or a dismiss
//   * ticks made on the current detection survive a reload
//   * a key that is no longer on the page is dropped and counted, never re-pointed
//   * two fills on one layer with (to the eye) one colour keep their own ticks and labels
//   * a dropped tick takes its row out of the takeoff (the rows are derived from the ticks)
// The committed functions are cut out of webui/app.js and run here.
//
// run: node tests/test_area_ticks.js
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(name) {
  let start = js.indexOf("\nfunction " + name + "(");
  if (start < 0) throw new Error("no function " + name);
  start += 1;
  let depth = 0;
  for (let j = js.indexOf("{", start); j < js.length; j++) {
    if (js[j] === "{") depth++;
    else if (js[j] === "}" && --depth === 0) return js.slice(start, j + 1);
  }
  return "";
}
const constLine = (name) => { const i = js.indexOf("const " + name + " = "); if (i < 0) throw new Error("no const " + name); return js.slice(i, js.indexOf("\n", i)); };
const NOTE = "Areas were measured again. Tick the rows you want.";
check(constLine("AR_REMEASURED").includes(JSON.stringify(NOTE)), "the note's text is the agreed sentence");

// a detection: groups ordered by area, each over its regions (what page_areas emits)
function det(sig, groups) {
  const regions = [], gs = [];
  groups.forEach((g, gi) => {
    const ids = g.regs.map(b => { regions.push({ region_id: regions.length, bbox: b, n_rings: 2 }); return regions.length - 1; });
    gs.push({ group_id: gi, layer: g.layer, fill: g.fill, n: ids.length, region_ids: ids, area_pt2: g.pt2, perim_pt: 10, name: null });
  });
  return { engine_sig: sig, scale: { ft_per_paper_inch: 20 }, areas: { groups: gs, regions } };
}
const ASPH = { layer: "C-PAVE-ASPH", fill: [0.5, 0.5, 0.5], regs: [[0, 0, 100, 100]], pt2: 10000 };
const STRIPE = { layer: "C-PAVE-STRIPING", fill: [1, 1, 0], regs: [[10, 10, 20, 15]], pt2: 50 };
const HATCH = { layer: "G-HATCH", fill: [0, 0, 0], regs: [[200, 200, 240, 220]], pt2: 800 };
// v1.8.5 / dev: [asphalt, striping]; after the fix the hatch is measured and lands at position 1
const OLD = det("sig-old", [ASPH, STRIPE]);
const NEW = det("sig-new", [ASPH, HATCH, STRIPE]);

function harness(mem) {
  const store = { getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, String(v)), removeItem: (k) => mem.delete(k) };
  const st = { result: null, extraItems: [], areaRows: [], areaInc: new Map(), areaLabels: new Map(), areaNote: "", ewDropped: false };
  const fns = ["arData", "arGroup", "arRegionsOf", "arLabel", "arFpi", "arSf", "arLf", "arApplyItems", "arKey", "arSelPayload",
    "arSelRestore", "arNoteClear", "ewOldRow", "calcEarlierHadOldEw", "calcSave", "calcRestore"];
  // "areasel" stands for THIS build's own key (areaSelKey); what an earlier version kept under its own key is never
  // read (tests/test_empty_repair2_ui.js)
  const C = new Function("state", "store", "calcKey", "calcKey2", "calcKey3", "areaSelKey", "renderItemized",
    constLine("EW_OLD_CY_RE") + "\n" + constLine("AR_REMEASURED") + "\n" + fns.map(fnSrc).join("\n")
    + "\nreturn { " + fns.join(", ") + " };")(st, store, () => "calc", () => "calc2", () => "calc3", () => "areasel", () => {});
  const load = (result) => {   // what loadResult does for these: a fresh page, then calcRestore
    st.result = JSON.parse(JSON.stringify(result));
    st.extraItems = []; st.areaRows = []; st.areaInc = new Map(); st.areaLabels = new Map(); st.areaNote = "";
    C.calcRestore(); C.arApplyItems();
  };
  return { st, C, load, mem };
}
const areaItems = (st) => st.extraItems.filter(it => it._area).map(it => `${it.label} ${it.qty} ${it.unit}`);

// ---- 1. v1.8.5's stored ticks (positions only) + the new detection -> nothing ticked, the note, no row ----------
{
  const h = harness(new Map());
  // a v1.8.5 browser ticked STRIPING, which sat at position 1, and named it
  h.mem.set("areasel", JSON.stringify({ rows: [{ group_id: 1, unit: "SF" }], inc: [1], labels: [[1, "Pavement striping"]] }));
  h.load(NEW);
  check(h.st.areaInc.size === 0 && h.st.areaRows.length === 0 && h.st.areaLabels.size === 0,
    "v1.8.5 position ticks are not applied to the new detection (position 1 is now the hatch)");
  check(areaItems(h.st).length === 0, "no Areas row reaches the takeoff from them: " + JSON.stringify(areaItems(h.st)));
  check(h.st.areaNote === NOTE + " 1 earlier tick was not kept.", "the panel's note says so: " + JSON.stringify(h.st.areaNote));
  // the note persists over a reload until the user acts
  h.load(NEW);
  check(h.st.areaNote.startsWith(NOTE) && h.st.areaInc.size === 0, "a reload keeps the note and still ticks nothing");
  // dismiss
  h.C.arNoteClear();
  h.load(NEW);
  check(h.st.areaNote === "" && !h.mem.has("areasel"), "dismissed: the note is gone and does not come back on reload");
}

// ---- 2. this build's ticks made on ANOTHER detection -> not applied either -----------------------------------------
{
  const h = harness(new Map());
  h.load(OLD);
  h.st.areaInc.set(1, true); h.st.areaRows.push({ group_id: 1, unit: "SF" }); h.st.areaLabels.set(1, "Striping");
  h.C.arApplyItems(); h.C.calcSave();
  const saved = JSON.parse(h.mem.get("areasel"));
  check(saved.v === 2 && saved.engine_sig === "sig-old" && saved.rows[0].key.startsWith("C-PAVE-STRIPING|1,1,0|"),
    "a tick is stored with the detection and the fill's key: " + JSON.stringify(saved.rows[0]));
  h.load(NEW);
  check(h.st.areaInc.size === 0 && areaItems(h.st).length === 0 && h.st.areaNote === NOTE + " 1 earlier tick was not kept.",
    "ticks made on another detection are not applied; the row leaves the takeoff; the note counts 1");
  // ticking again clears the note and works on the new detection
  h.st.areaInc.set(2, true); h.st.areaRows.push({ group_id: 2, unit: "SF" }); h.C.arNoteClear(); h.C.arApplyItems();
  check(h.st.areaNote === "" && areaItems(h.st).join() === "unnamed area (rgb 255,255,0) 3.9 SF",
    "re-ticked on the new detection: the note goes, the row is the striping: " + JSON.stringify(areaItems(h.st)));
}

// ---- 3. ticks made on the CURRENT detection survive a reload -------------------------------------------------------
{
  const h = harness(new Map());
  h.load(NEW);
  h.st.areaInc.set(2, true); h.st.areaRows.push({ group_id: 2, unit: "SF" }); h.st.areaRows.push({ group_id: 2, unit: "LF" });
  h.st.areaLabels.set(2, "Striping");
  h.st.areaInc.set(0, true); h.st.areaRows.push({ group_id: 0, unit: "SF", label: "Asphalt to subgrade" });
  h.C.arApplyItems(); h.C.calcSave();
  const before = areaItems(h.st);
  h.load(NEW);
  check(JSON.stringify(areaItems(h.st)) === JSON.stringify(before) && before.length === 3 && h.st.areaNote === "",
    "same detection: every tick, row and label comes back, no note: " + JSON.stringify(before));
}

// ---- 4. a key that vanished is dropped and counted, never re-pointed ----------------------------------------------
{
  const h = harness(new Map());
  h.load(NEW);
  h.st.areaInc.set(1, true); h.st.areaRows.push({ group_id: 1, unit: "SF" });   // the hatch
  h.st.areaInc.set(0, true); h.st.areaRows.push({ group_id: 0, unit: "SF" });   // the asphalt
  h.C.calcSave();
  // same engine, but the drawing's hatch moved (a revised PDF): its fingerprint no longer matches, and
  // position 1 still exists - the old code would have ticked whatever sits there
  const MOVED = det("sig-new", [ASPH, { ...HATCH, regs: [[300, 200, 340, 220]] }, STRIPE]);
  h.load(MOVED);
  check(h.st.areaInc.get(0) === true && !h.st.areaInc.get(1) && !h.st.areaInc.get(2),
    "the asphalt tick survives; the hatch tick is dropped, not moved to whatever is at its position");
  check(h.st.areaNote === NOTE + " 1 earlier tick was not kept.", "the drop is counted in the note: " + JSON.stringify(h.st.areaNote));
  check(areaItems(h.st).length === 1, "only the asphalt row is in the takeoff");
}

// ---- 5. two fills on one layer that look the same colour keep their own ticks -------------------------------------
{
  const A = { layer: "C-CONC", fill: [0.6, 0.6, 0.6], regs: [[0, 0, 50, 50]], pt2: 2500 };
  const B = { layer: "C-CONC", fill: [0.601, 0.6, 0.6], regs: [[100, 0, 140, 40]], pt2: 1600 };   // rgb 153 both
  const D1 = det("s", [A, B]);
  const h = harness(new Map());
  h.load(D1);
  check(h.C.arKey(h.st.result.areas.groups[0]) !== h.C.arKey(h.st.result.areas.groups[1]), "their keys differ");
  h.st.areaInc.set(1, true); h.st.areaRows.push({ group_id: 1, unit: "SF" }); h.st.areaLabels.set(1, "Sidewalk");
  h.st.areaLabels.set(0, "Slab");
  h.C.calcSave();
  // the same detection served with the two in the other order (ties on area order are not promised)
  const D2 = det("s", [B, A]);
  h.load(D2);
  const gB = h.st.result.areas.groups.find(g => g.fill[0] === 0.601), gA = h.st.result.areas.groups.find(g => g.fill[0] === 0.6);
  check(h.st.areaInc.get(gB.group_id) === true && !h.st.areaInc.get(gA.group_id),
    "the tick follows its own fill to its new position");
  check(h.st.areaLabels.get(gB.group_id) === "Sidewalk" && h.st.areaLabels.get(gA.group_id) === "Slab" && h.st.areaNote === "",
    "each keeps its own label; nothing was dropped");
}

// ---- 6. a page with nothing ticked stores nothing -----------------------------------------------------------------
{
  const h = harness(new Map());
  h.load(NEW);
  h.C.calcSave();
  check(!h.mem.has("areasel") && h.st.areaNote === "", "no ticks, no note: no stored selection");
}

if (fails) { console.error(`\n${fails} FAILED`); process.exit(1); }
console.log("\nall passed");
