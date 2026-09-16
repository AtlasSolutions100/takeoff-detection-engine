// ONE LIMITS PICK, THREE ROWS - the client half (2026-09-16, Beaumont call
// 2026-09-15 05:48-06:27). The picked or drawn limit of work is sent as the
// disturbed area; Add to takeoff then carries Clear & Grub (SF) and Silt /
// Construction Fence (LF) beside the layer rows; the sheet's own
// limits-of-disturbance family is offered with one click.
//
// run: node tests/test_ew_limit_rows.js   (fails on c4c5067)
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
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

// ---- wiring ------------------------------------------------------------------
check(/boundary_is_limit: !!state\.ewBound,/.test(fnSrc("ewBody")), "the request says whether the boundary is a real limit (never for the work-area box)");
check(/id="ewBoundAuto" class="mini" hidden/.test(html), "the panel has a hidden 'use the sheet's limit line' button");
const reSrc = (/const EW_ITEM_RE = (\/.*\/);/.exec(js) || [])[1];
const EW_ITEM_RE = reSrc ? eval(reSrc) : null;
check(EW_ITEM_RE && EW_ITEM_RE.test("Clear & Grub") && EW_ITEM_RE.test("Silt / Construction Fence") && EW_ITEM_RE.test("Strip Topsoil") && !EW_ITEM_RE.test("Clear & Grubbing extra"),
  "the two rows are earthwork rows (replaced on every Calculate), exact names only");
check(/LF around/.test(fnSrc("renderEwBound")) && /SF inside/.test(fnSrc("renderEwBound")), "the limit line reads SF inside and LF around");
check(/Clear & Grub and Strip Topsoil cover all of it/.test(fnSrc("renderEwArea")), "the area note says the limit is what the layers cover");

// ---- ewApplyItems with stubs ----------------------------------------------------
function apply(j) {
  const state = { extraItems: [] };
  const sess = new Map();
  const s = {
    state,
    ewClearItems: () => { state.extraItems.length = 0; },
    ewR: (v) => Math.round(v),
    ewItemLabel: (l) => (l.side === "existing" ? "Strip Topsoil" : "Place " + l.name),
    store: { getItem: (k) => sess.get(k) || null, setSession: (k, v) => sess.set(k, v) },
    tradeKey: (l) => "trade:" + l,
  };
  const names = Object.keys(s);
  new Function(...names, fnSrc("ewApplyItems") + "\nreturn ewApplyItems;")(...names.map(k => s[k]))(j);
  return { items: state.extraItems, sess };
}
{
  const r = apply({ cut_yd3: 100, fill_yd3: 50, layers: [{ name: "topsoil", side: "existing", volume_yd3: 20 }],
                    boundary: { area_sf: 142760.6, perim_ft: 2340.4, gap_ft: 0, is_limit: true } });
  const labels = r.items.map(i => i.label);
  check(labels.includes("Clear & Grub") && labels.includes("Silt / Construction Fence"), "a real limit adds Clear & Grub and the fence row");
  const cg = r.items.find(i => i.label === "Clear & Grub"), sf = r.items.find(i => i.label === "Silt / Construction Fence");
  check(cg.qty === 142761 && cg.unit === "SF", "Clear & Grub is the ring's area in SF (142,761)");
  check(sf.qty === 2340 && sf.unit === "LF", "the fence is the ring's perimeter in LF (2,340)");
  check(r.sess.get("trade:Clear & Grub") === "Earthwork & Grading" && r.sess.get("trade:Silt / Construction Fence") === "Earthwork & Grading", "both rows are filed under Earthwork & Grading unless the reviewer says otherwise");
  check(labels[0] === "Site Cut" && labels[1] === "Site Fill" && labels[2] === "Strip Topsoil", "the existing rows keep their order ahead of the new ones");
}
{
  const r = apply({ cut_yd3: 100, fill_yd3: 50, layers: [], boundary: { area_sf: 5000, perim_ft: 300, is_limit: false } });
  check(!r.items.some(i => /Clear|Fence/.test(i.label)), "a boundary that is only a crop adds no rows");
  const r2 = apply({ cut_yd3: 100, fill_yd3: 50, layers: [] });
  check(r2.items.length === 2, "no boundary at all: unchanged");
}

// ---- ewLodFamily with stubs ------------------------------------------------------
{
  const state = {
    labels: new Map([[1, "EXISTING CONTOUR"], [2, "LIMITS OF DISTURBANCE FENCING"], [3, "Unnamed d12"]]),
    excluded: new Set([9]),
    result: { styles: [{ style_id: 1, layer: "C-TOPO" }, { style_id: 2, layer: "BG-Limit" }, { style_id: 3, layer: "C-LIMIT-OF-DISTURBANCE" }, { style_id: 4, layer: "C-LOD" }],
              chains: [{ chain_id: 7, style_id: 2, length_pt: 300 }, { chain_id: 8, style_id: 3, length_pt: 900 }, { chain_id: 9, style_id: 3, length_pt: 5000 }, { chain_id: 10, style_id: 4, length_pt: 0 }] },
  };
  const src = (/const EW_LOD_RE = .*;\n/.exec(js) || [""])[0] + fnSrc("ewLodFamily") + "\nreturn ewLodFamily;";
  const fam = new Function("state", src)(state)();
  check(fam && fam.style_id === 3 && fam.len === 900, "the limit family is picked by name OR layer, by the longest footage still on the sheet (excluded lines do not count)");
  state.labels.set(3, "SILT FENCE"); state.result.styles[2].layer = "C-EROS";
  const fam2 = new Function("state", src)(state)();
  check(fam2 && fam2.style_id === 2, "with that family renamed, the LIMITS OF DISTURBANCE label wins");
  state.labels.set(2, "PROPERTY LINE"); state.result.styles[1].layer = "C-PROP";
  check(new Function("state", src)(state)() === null, "no limit family on the sheet -> null (the button stays hidden)");
}

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all limit-rows checks passed");
