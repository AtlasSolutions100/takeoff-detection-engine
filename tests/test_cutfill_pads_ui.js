// BUILDING PADS, the screen side (2026-09-27): the Building pads card of the cut / fill engine.
// The functions are pulled out of the page script and run against stand-in elements.
//
// run: node tests/test_cutfill_pads_ui.js
"use strict";
const fs = require("fs");
const path = require("path");
const ejs = fs.readFileSync(path.join(__dirname, "..", "webui", "cutfill", "app.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "..", "webui", "cutfill", "index.html"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(src, name) {
  let start = src.indexOf("async function " + name + "(");
  if (start < 0) start = src.indexOf("function " + name + "(");
  if (start < 0) return "";
  let depth = 0;
  for (let j = src.indexOf("{", src.indexOf(")", start)); j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}" && --depth === 0) return src.slice(start, j + 1);
  }
  return "";
}
const constSrc = (src, name) => { const m = src.match(new RegExp("const " + name + " = [^\\n]+")); return m ? m[0] : ""; };

// a stand-in for the page's el(): a plain node with its attributes, its handlers and its text
function el(tag, attrs, ...kids) {
  const n = { tag, attrs: attrs || {}, kids: [], checked: false, disabled: false };
  const add = (k) => { if (k == null || k === false) return; if (Array.isArray(k)) k.forEach(add); else n.kids.push(k); };
  kids.forEach(add);
  n.append = (...more) => more.forEach(add);
  return n;
}
const text = (n) => (typeof n === "string" ? n : n.kids.map(text).join(""));
const find = (n, tag) => (typeof n === "string" ? [] : (n.tag === tag ? [n] : []).concat(...n.kids.map((k) => find(k, tag))));

const src = [constSrc(ejs, "nf0"), constSrc(ejs, "isNum"), fnSrc(ejs, "unNeg"), constSrc(ejs, "fmt0"), constSrc(ejs, "fmtZ"), fnSrc(ejs, "ptsBbox"),
  fnSrc(ejs, "scaleRefused"), fnSrc(ejs, "scaleFtPerPt"), fnSrc(ejs, "noScaleWords"), fnSrc(ejs, "renderPads"), fnSrc(ejs, "setPad")].join("\n");
check(/function renderPads\(/.test(src) && /async function setPad\(/.test(src) && /const fmtZ = /.test(src), "the card's functions are in the page script");

function card(st, hooks) {
  const box = el("div", {});
  Object.defineProperty(box, "innerHTML", { set() { box.kids.length = 0; }, get() { return ""; } });
  const h = Object.assign({ patchPage: async () => ({}), markStale() { h.stale = (h.stale || 0) + 1; }, setStatus(m, o) { h.status = [m, o]; },
    hotPts(pts, role, t) { h.hot = [pts, role, t]; }, hotOff() { h.hot = null; }, centreOn(k, b, z) { h.centre = [k, b, z]; } }, hooks || {});
  const env = new Function("$", "state", "el", "patchPage", "markStale", "setStatus", "hotPts", "hotOff", "centreOn",
    src + "\nreturn { renderPads, setPad };")((id) => (id === "padList" ? box : null), st, el, (...a) => h.patchPage(...a), () => h.markStale(),
    (m, o) => h.setStatus(m, o), (...a) => h.hotPts(...a), () => h.hotOff(), (...a) => h.centreOn(...a));
  env.renderPads();
  return { box, env, h };
}
const SQ = [[400, 400], [854.8, 400], [854.8, 854.8], [400, 854.8]];          // 454.8 x 454.8 = 206,843 pt2
const pad = (over) => Object.assign({ key: "627_627_5993.00", label_id: 7, text: "FFE=5993", z: 5993, at: [627.4, 627.4], chain: 11934,
  polygon: SQ, area_pt2: 206843.0, use: false }, over || {});
const state = (pads, scale) => ({ key: "d0p0", analyzes: pads === null ? {} : { d0p0: { pads } }, result: null,
  project: { settings: { scale_ft_per_in: scale }, pages: { d0p0: {} } } });

let c = card(state(null, 20));
check(/Read the sheet first/.test(text(c.box)), "no read yet: the card says to read the sheet");
c = card(state([], 20));
check(/No floor height \(FFE, FF\) is printed on this sheet/.test(text(c.box)), "a read with no floor height: the card says so");

c = card(state([pad(), pad({ key: "90_90_5990.50", text: "FF 5990.5", z: 5990.5, at: [90, 90], chain: null, polygon: null, area_pt2: null })], 20));
let rows = c.box.kids;
let ticks = find(c.box, "input");
check(rows.length === 2 && ticks.length === 2, "one row and one switch for each floor height");
check(/"FFE=5993" 5993(\.00)? ft/.test(text(rows[0]).replace(/,/g, "")) && /15[,.\s]?96\d SF/.test(text(rows[0])),
  "the row names the printed text, its height and the outline's size at the scale in force (206,843 pt2 at 1 in = 20 ft = 15,960 SF): " + text(rows[0]));
check(ticks[0].checked === false && ticks[0].disabled === false, "an offer is off until the operator turns it on");
check(ticks[1].disabled === true && /no closed outline round it/.test(text(rows[1])), "a floor height with no outline cannot be turned on, and says why");
rows[0].attrs.onmouseenter();
check(c.h.hot && c.h.hot[0].length === 5 && c.h.hot[0][4] === SQ[0] && c.h.hot[1] === "proposed" && /building pad/.test(c.h.hot[2]),
  "hovering the row lights the CLOSED outline on the sheet, in the proposed surface's colour");
rows[0].attrs.onmouseleave();
check(c.h.hot === null, "and leaving it puts the light out");
rows[1].attrs.onmouseenter();
check(c.h.hot === null, "a row with no outline lights nothing");
find(rows[0], "span")[0].attrs.onclick();
check(c.h.centre && c.h.centre[0] === "d0p0" && c.h.centre[1][0] === 400 && c.h.centre[1][2] === 854.8, "a click goes to the outline");
find(rows[1], "span")[0].attrs.onclick();
check(c.h.centre[1][0] === 50 && c.h.centre[1][2] === 130, "or to the floor height itself where no outline is drawn");
c = card(state([pad()], null));
check(/set the scale for the area/.test(text(c.box)), "with no scale the size is not guessed");
c = card(state([pad({ use: true })], 20));
check(find(c.box, "input")[0].checked === true, "a pad the engine says is on shows on");

(async () => {
  // turning one on: the record goes to the engine by the pad's key, the result goes stale
  let sent = null;
  const st = state([pad()], 20);
  c = card(st, { patchPage: async (k, body) => { sent = [k, body]; return {}; } });
  await c.env.setPad(st.analyzes.d0p0.pads[0], true);
  check(JSON.stringify(sent) === JSON.stringify(["d0p0", { pads: { "627_627_5993.00": { use: true } } }]), "turning a pad on sends its key and the switch: " + JSON.stringify(sent));
  check(st.analyzes.d0p0.pads[0].use === true && c.h.stale === 1 && /turned on\. Compute to apply it\./.test(c.h.status[0]),
    "the pad shows on, the result is stale, and the status says to compute: " + c.h.status[0]);
  check(find(c.box, "input")[0].checked === true, "the card is drawn again with the switch on");
  await c.env.setPad(st.analyzes.d0p0.pads[0], false);
  check(sent[1].pads["627_627_5993.00"].use === false && st.analyzes.d0p0.pads[0].use === false && /turned off/.test(c.h.status[0]), "and off again");
  // a refusal from the engine: nothing changes on the screen, the reason is shown
  const st2 = state([pad()], 20);
  c = card(st2, { patchPage: async () => { throw new Error("That building pad is not on this read of the sheet: read the sheet again."); } });
  await c.env.setPad(st2.analyzes.d0p0.pads[0], true);
  check(st2.analyzes.d0p0.pads[0].use === false && !c.h.stale && /Could not save the building pad: That building pad is not on this read/.test(c.h.status[0])
    && c.h.status[1] && c.h.status[1].error === true && find(c.box, "input")[0].checked === false,
    "a refused switch stays off, nothing goes stale, and the engine's reason is shown");

  // the cell story names a pad point
  const sd = new Function("state", "ensureAnalysis", "fmtZ", "uniq", fnSrc(ejs, "sourceDetail") + "\nreturn sourceDetail;")(
    { analyzes: { d0p0: { _label: new Map(), _contour: new Map(), _fam: new Map() } } }, () => {}, (z) => String(z), (a) => a);
  const line = sd({ kind: "pad", page: "d0p0", z: 5993, ref: { pad: "627_627_5993.00", label: 7, text: "FFE=5993" } });
  check(line === 'building pad – floor height "FFE=5993", turned on by you', "a cell on a pad says where its height comes from: " + line);

  check(/renderLimitOffer\(\); renderPads\(\);/.test(fnSrc(ejs, "renderAllPanels")), "the card is drawn with the other panels");
  check(/<section class="card" id="secPads">[\s\S]*?<h3>Building pads<\/h3>[\s\S]*?id="padList"/.test(html), "the page has the card");
  check(/only where the outline is the building/.test(html) && /layer stack lowers a pad with the rest of the site/.test(html),
    "and its hint says when not to turn one on, and what a layer stack does to it");
  check(+((/ui\/app\.js\?v=(\d+)/.exec(html) || [])[1]) >= 36, "the engine page's cache buster moved");
  console.log(fails ? `\n${fails} FAILED` : "\nALL PASS");
  process.exit(fails ? 1 : 0);
})();
