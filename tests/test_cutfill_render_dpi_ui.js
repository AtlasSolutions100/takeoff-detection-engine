// A VERY LARGE PAGE DRAWN AT A LOWER DPI STAYS IN PLACE (2026-10-01, critic CRITIC-mem-1 WRONG-2).
// The server lowers the dpi of a page past 40 million pixels at 100 dpi (cutfill/server.py RENDER_MAX_PX). The
// sheet shown and the zoomed view were drawn into the page rectangle, but the Overlay drew the other sheet's
// picture at 72 / 100 of its pixel size: a lowered picture came out shrunk and out of place, with no word.
// Pinned: the Overlay draws into the overlaid sheet's page rectangle whatever the picture's pixel size; a picture
// that came back at a lower dpi says so once in the status, and its record keeps the dpi it really has.
//
// run: node tests/test_cutfill_render_dpi_ui.js
"use strict";
const fs = require("fs");
const path = require("path");
const ejs = fs.readFileSync(path.join(__dirname, "..", "webui", "cutfill", "app.js"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(src, name) {
  let start = src.indexOf("function " + name + "(");
  if (start < 0) return "";
  let depth = 0;
  for (let j = src.indexOf("{", start); j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}" && --depth === 0) return src.slice(start, j + 1);
  }
  return "";
}
const ghostSrc = fnSrc(ejs, "drawGhost");
const imgSrc = fnSrc(ejs, "ensureImage");
check(ghostSrc && imgSrc, "drawGhost and ensureImage are found in app.js");

// --- the Overlay: a 14,400 pt page whose picture came back at 31 dpi (6,200 px), not 100 (20,000 px)
const calls = [];
const ctx = { save() {}, restore() {}, setTransform() {}, transform() {}, set globalAlpha(v) {},
  drawImage: (...a) => calls.push(a) };
const img = { naturalWidth: 6200, naturalHeight: 6200 };
const state = { ghost: { key: "d1p0", alpha: 0.5 }, key: "d0p0", xf: null, images: { d1p0: { img, ready: true, dpi: 100 } },
  view: { k: 1, x: 0, y: 0 }, analyzes: {}, polls: {}, analysisLoads: {}, ghostAsk: {}, tool: null };
const pageSize = (k) => (k === "d1p0" ? { w: 14400, h: 14400 } : { w: 612, h: 792 });
const f = new Function("state", "ctx", "dpr", "pageSize", "placeMatrix", "mulM", "invM", "ensureImage", "$", "GHOST_HIDE_TOOLS",
  "drawGhostLines", "ensureAnalysis", "setStatus", "sheetName", ghostSrc + "\nreturn drawGhost;");
const I = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
const drawGhost = f(state, ctx, 1, pageSize, () => I, (a) => a, (a) => a, () => {}, () => null, new Set(), () => {}, () => {},
  () => {}, (k) => k);
drawGhost();
check(calls.length === 1, "the Overlay draws the other sheet's picture once");
const c = calls[0] || [];
check(c.length === 5 && c[3] === 14400 && c[4] === 14400,
  `a lowered picture is drawn into its page rectangle (14,400 x 14,400 pt), not at 72/100 of its pixels: ${JSON.stringify(c.slice(1))}`);

// --- the picture says when it came back at a lower dpi
const said = [];
function Image() { this.naturalWidth = 0; }
const st2 = { pid: "p", images: {} };
const g = new Function("state", "Image", "pageSize", "imageUrl", "setStatus", "requestDraw", "sheetName",
  imgSrc + "\nreturn ensureImage;");
const ensure = g(st2, Image, pageSize, () => "u", (m) => said.push(m), () => {}, (k) => "C-1");
ensure("d1p0");
const rec = st2.images.d1p0;
rec.img.naturalWidth = 6200;
rec.img.onload();
check(rec.ready && Math.abs(rec.dpi - 31) < 0.1, `the record keeps the dpi the picture really has (${rec.dpi.toFixed(1)})`);
check(said.length === 1 && /very large/.test(said[0]) && /less detail/.test(said[0]), `the status says so once: ${said[0]}`);
ensure("d0p0");
st2.images.d0p0.img.naturalWidth = 850;          // 612 pt at 100 dpi
st2.images.d0p0.img.onload();
check(st2.images.d0p0.dpi === 100 && said.length === 1, "an ordinary sheet at 100 dpi is not mentioned");

console.log(fails ? `\nFAILED: ${fails}` : "\nall ok");
process.exit(fails ? 1 : 0);
