// Rich hover tips on the toolkit rail (TIPVID, end of webui/app.js).
//
// CLIPS BACK, 2026-10-02. The first clips were pulled on 2026-10-01 (cropped
// from a staff-only client sheet). Ten new ones were recorded in the live app
// on a shared sheet and live in webui/tooldemos/ again. What this pins:
//
//   1. ASSETS - tooldemos/manifest.json and TIPVID_TIPS name the same rows
//      (file, caption, key); the folder holds exactly the manifest's files;
//      every clip is a webm under the 200 KB cap and is none of the ten
//      pulled files (their git blob hashes are listed below); captions name
//      no client job and no sheet number; only app.js names the folder.
//   2. LOGIC - the pure block between the TIPVID-PURE markers (lookup, clip
//      URL, popover placement math).
//   3. CONTRACT - 600ms hover intent, pointer-events:none, the armed-tool
//      gate, close on leave / click / Esc, native titles untouched, the frame
//      hook gets bottom + width (the redesign's ribbon placement reads them).
//   4. BEHAVIOUR - the real wiring driven against a stub DOM:
//      a. loading the page requests nothing;
//      b. each tool's FIRST hover requests its own clip, once, and nothing
//         else; a tool without a clip requests nothing; a second round of
//         hovers requests nothing;
//      c. a clip that fails to load: the tip stays, as text, with no video
//         in it and nothing logged, and that clip is never asked for again;
//      d. reduced motion: every tip opens as text, no video, no request;
//      e. never opens (and so requests nothing) while a canvas tool is armed;
//      f. a touch screen (hover: none / pointer: coarse) or a window under
//         768 px: every tip opens as text with its caption, no clip asked for.
//
// Dependency-free:  node tests/test_tooldemo_tips.js

"use strict";
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const ROOT = path.join(__dirname, "..");
const DEMOS = path.join(ROOT, "webui", "tooldemos");
const appSrc = fs.readFileSync(path.join(ROOT, "webui", "app.js"), "utf8");
const cssSrc = fs.readFileSync(path.join(ROOT, "webui", "style.css"), "utf8");
const htmlSrc = fs.readFileSync(path.join(ROOT, "webui", "index.html"), "utf8");

let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); }
  else console.log("ok:", msg);
}

// the single-line rail, in DOM order - the 16 buttons the feature must not touch
// (the multi-select group tkMulti keeps its live-rewritten titles only)
const RAIL = ["measureBtn", "splitBtn", "extendBtn", "cutSpanBtn", "flattenBtn", "drawBtn",
              "reclassBtn", "mergeBtn", "sepBtn", "removeBtn", "wzBtn",
              "wzClearBtn", "trimBtn", "faithfulBtn", "rotateBtn", "kbdBtn"];
// tools with a text tip and no clip (UI verify 2: Trim and Flatten had no tip)
const TEXT_TIPS = ["flattenBtn", "trimBtn"];
// the ten tools that carry a tip, each with its clip
const WANT_TIPS = ["measureBtn", "splitBtn", "extendBtn", "cutSpanBtn",
                   "drawBtn", "reclassBtn", "mergeBtn", "sepBtn",
                   "removeBtn", "wzBtn"];
const CAP_BYTES = 200 * 1024;
// git blob hashes of the ten clips pulled on 2026-10-01 (081e8f7^:webui/tooldemos/)
const PULLED = new Set([
  "b41613f89f21050484ba7a0759121c538b1c3e66", "7a049d9002f78aa3bc6cc351dee0b12e759b3790",
  "ab0e7a71f83548863bb62322955402a7dde37b76", "6db5ef6fbf1025efc1818120b27ab35fef573286",
  "6aa5890487961cc11995412fd3604f9e6c4b79e3", "4638370bec7fa71c252681f9f31f93528c1ce3d0",
  "2eca9b2e790b8094de9b8b9a00102f07741ebb11", "72c374ac129ce5f1ec554c5f900eb81769b2776f",
  "ccae52d4530e3d9eed085a4207ee3970f7b8fbf4", "ae69376637bc1a1a585b7567d0246416ae457434",
]);
// Every account reads these captions, so none may name a client's job or a
// sheet number (Koby's on-screen copy rule).
const CLIENT_WORDS = /\b(barn|swr|beaumont|ryno|stice|liberty|ellis|klayton|thielen|aqua vista|kayden|pierce|wells?)\b/i;
const SHEET_NO = /\b[A-Z]{1,2}-?\d{1,2}\.\d{1,2}\b|\b[A-Z]-\d{2}\b/;
const gitBlob = (buf) => crypto.createHash("sha1")
  .update(Buffer.concat([Buffer.from(`blob ${buf.length}\0`), buf])).digest("hex");

// ---- 2 (first: the assets check needs it). the pure block -----------------------
const a = appSrc.indexOf("// TIPVID-PURE-BEGIN");
const b = appSrc.indexOf("// TIPVID-PURE-END");
check(a >= 0 && b > a, "TIPVID-PURE markers present in webui/app.js");
const T = new Function(appSrc.slice(a, b) +
  " return { TIPVID_HOVER_DELAY_MS, TIPVID_TIPS, tipvidEntry, tipvidClip, tipvidPos, tipvidClipsOk };")();
const tips = T.TIPVID_TIPS;

// ---- 1. assets ------------------------------------------------------------------
check(fs.existsSync(DEMOS), "webui/tooldemos/ exists");
let manifest = null;
try { manifest = JSON.parse(fs.readFileSync(path.join(DEMOS, "manifest.json"), "utf8")); }
catch (e) { manifest = null; }
check(manifest && typeof manifest === "object", "tooldemos/manifest.json parses");
manifest = manifest || {};
check(JSON.stringify(Object.keys(manifest).sort()) === JSON.stringify(Object.keys(tips).sort()),
  "manifest rows == TIPVID_TIPS rows");
for (const id of Object.keys(tips)) {
  const m = manifest[id] || {}, t = tips[id];
  check(m.file === t.file && m.caption === t.caption && m.key === t.key,
    `${id}: manifest row matches app.js (file, caption, key)`);
}
const want = new Set(Object.values(tips).map((t) => t.file).filter(Boolean));
const onDisk = fs.existsSync(DEMOS) ? fs.readdirSync(DEMOS).filter((f) => f !== "manifest.json") : [];
check(onDisk.length === want.size && onDisk.every((f) => want.has(f)),
  `the folder holds exactly the manifest's clips, nothing else (${onDisk.join(", ")})`);
for (const f of want) {
  const p = path.join(DEMOS, f);
  const ok = fs.existsSync(p);
  check(ok, `${f}: file present`);
  if (!ok) continue;
  const buf = fs.readFileSync(p);
  check(buf.length > 4096 && buf.length < CAP_BYTES, `${f}: ${buf.length} bytes, under the ${CAP_BYTES} cap`);
  check(buf.slice(0, 4).toString("hex") === "1a45dfa3" && buf.includes(Buffer.from("webm")),
    `${f}: a WebM (EBML header, doctype webm)`);
  check(!PULLED.has(gitBlob(buf)), `${f}: not one of the ten pulled client clips`);
}
const webFiles = [];
(function walk(d) {
  for (const f of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, f.name);
    if (f.isDirectory()) { if (f.name !== "vendor" && f.name !== "fonts" && f.name !== "tooldemos") walk(p); }
    else if (/\.(js|html|css|json)$/.test(f.name)) webFiles.push(p);
  }
})(path.join(ROOT, "webui"));
const naming = webFiles.filter((p) => /tooldemos/.test(fs.readFileSync(p, "utf8")))
  .map((p) => path.relative(ROOT, p).replace(/\\/g, "/"));
check(naming.length === 1 && naming[0] === "webui/app.js",
  `only app.js names the clip folder - nothing preloads it (${naming.join(", ")})`);

check(T.TIPVID_HOVER_DELAY_MS === 600, "hover-intent delay is 600ms (the Photoshop pattern)");
check(WANT_TIPS.every((id) => id in tips), "all ten tools keep their tip");
check(TEXT_TIPS.every((id) => id in tips && T.tipvidClip(tips[id]) === null && tips[id].key),
  "Trim and Flatten have a text tip (caption + key), no clip");
check(JSON.stringify(Object.keys(tips).sort()) === JSON.stringify([...WANT_TIPS, ...TEXT_TIPS].sort()),
  "the tip rows are exactly the ten clip tools plus the text-only ones");
for (const [id, ent] of Object.entries(tips)) {
  check(typeof ent.caption === "string" && ent.caption.length > 0, `${id}: caption present`);
  check(!CLIENT_WORDS.test(ent.caption || "") && !SHEET_NO.test(ent.caption || ""),
    `${id}: caption names no client job and no sheet number ("${ent.caption}")`);
  check("key" in ent, `${id}: key present (null allowed)`);
  check(htmlSrc.includes(`id="${id}"`), `${id}: referenced button id exists in index.html`);
  check(RAIL.includes(id), `${id}: is a toolkit rail button`);
  if (!TEXT_TIPS.includes(id))
    check(T.tipvidClip(ent) === "tooldemos/" + ent.file, `${id}: clip URL is tooldemos/${ent.file}`);
}
for (const id of ["faithfulBtn", "kbdBtn", "wzClearBtn", "rotateBtn"])
  check(!(id in tips), `${id}: no tip - native title behaviour only`);
check(T.tipvidEntry(tips, "measureBtn") !== null, "tipvidEntry finds a real row");
check(T.tipvidEntry(tips, "faithfulBtn") === null, "tipvidEntry: no row -> null");
check(T.tipvidEntry(null, "measureBtn") === null, "tipvidEntry: no table -> null");
check(T.tipvidEntry(tips, "toString") === null, "tipvidEntry: prototype names are not rows");
check(T.tipvidEntry({ x: { key: "Q" } }, "x") === null, "tipvidEntry: a row with no caption is not a tip");
check(T.tipvidClip({ caption: "c" }) === null, "tipvidClip: a row without a file is a text tip");
for (const bad of ["../x.webm", "a/b.webm", "x.mp4", "x.webm?y", "", 5])
  check(T.tipvidClip({ caption: "c", file: bad }) === null, `tipvidClip refuses ${JSON.stringify(bad)}`);
{
  const p = T.tipvidPos({ left: 12, right: 54, top: 300, height: 30 }, { w: 316, h: 380 }, 1600, 900, 10);
  check(p.x === 64 && p.y === 315 - 190, "popover opens right of the button, vertically centred");
  check(T.tipvidPos({ left: 12, right: 54, top: 860, height: 30 }, { w: 316, h: 380 }, 1600, 900, 10).y === 900 - 380 - 4,
    "bottom clamp keeps the popover on screen");
  check(T.tipvidPos({ left: 12, right: 54, top: 4, height: 30 }, { w: 316, h: 380 }, 1600, 900, 10).y === 4,
    "top clamp keeps the popover on screen");
  check(T.tipvidPos({ left: 500, right: 542, top: 300, height: 30 }, { w: 316, h: 380 }, 600, 900, 10).x === 500 - 10 - 316,
    "flips left when the right side cannot fit");
  check(T.tipvidPos({ left: 12, right: 54, top: 300, height: 30 }, { w: 700, h: 380 }, 600, 900, 10).x === 4,
    "cramped fallback clamps to the viewport edge");
}

// ---- 3. the runtime contract, as written text --------------------------------
const end = appSrc.indexOf("// =============== end toolkit rich hover demos");
const wiring = appSrc.slice(b, end);
check(/#tipvid\s*{[^}]*pointer-events:\s*none/.test(cssSrc), "popover is pointer-events:none - a hint, never a click target");
check(/#tipvid video\s*{[^}]*width:\s*300px[^}]*aspect-ratio/.test(cssSrc),
  "the video has a fixed CSS box, so the tip is placed right before the clip loads");
check(!/\bfetch\s*\(|XMLHttpRequest|createElement\("(img|source|link|script|iframe)"\)|\.src\s*=|setAttribute\("href"/.test(wiring),
  "the hover wiring has no fetch, no XHR, and no URL-loading element but the clip's <video>");
check((wiring.match(/setAttribute\("src"/g) || []).length === 1, "exactly one place sets a src (the clip, on open)");
check(wiring.includes("if (state.tool) return;"), "never opens while a canvas tool is armed mid-gesture");
check(wiring.includes('addEventListener("pointerleave", closeTip)') && wiring.includes('addEventListener("pointerdown", closeTip)'),
  "instant close on leave and on click");
check(wiring.includes('e.key === "Escape"') && wiring.includes("closeTip"), "Esc closes the popover");
check(wiring.includes('btn.getAttribute("title")'), "popover text is the button's own title, read verbatim at open");
check(/tipvidPos\(\s*{[^}]*bottom:[^}]*width:/.test(wiring),
  "the frame hook gets bottom + width (the redesign's ribbon placement reads them)");
const btnRe = (id) => new RegExp(`<button[^>]*id="${id}"[^>]*title="([^"]{10,})"[^>]*>`);
for (const id of RAIL) check(!!htmlSrc.match(btnRe(id)), `#${id} keeps its native title attribute`);
const tkStart = htmlSrc.indexOf('<div class="toolkit">');
const tkEnd = htmlSrc.indexOf("</div>", htmlSrc.indexOf('id="kbdBtn"'));
check(tkStart >= 0 && !htmlSrc.slice(tkStart, tkEnd).includes("tooldemo"),
  "index.html toolkit markup untouched (no popover markup baked in)");

// ---- 4. behaviour: drive the real wiring against a stub DOM -------------------
function harness({ reduced = false, tool = null, noHover = false, coarse = false, width = 1600 } = {}) {
  const requests = [];     // every URL the page would ask for, in order
  const made = [];
  const errors = [];
  const timers = [];
  function el(tag) {
    const e = {
      tag, children: [], parent: null, attrs: {}, hidden: false, style: {}, textContent: "",
      className: "", id: "", offsetWidth: 316, offsetHeight: 90, listeners: {}, paused: true,
      setAttribute(k, v) {
        this.attrs[k] = String(v);
        if (k === "src" || k === "href") requests.push(String(v));
      },
      getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
      removeAttribute(k) { delete this.attrs[k]; },
      append(...xs) { for (const x of xs) if (typeof x === "object") { x.remove(); x.parent = this; this.children.push(x); } },
      prepend(x) { x.remove(); x.parent = this; this.children.unshift(x); },
      appendChild(x) { this.append(x); return x; },
      remove() { if (this.parent) { this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; } },
      addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); },
      fire(ev) { for (const fn of this.listeners[ev] || []) fn({ type: ev }); },
      play() { this.paused = false; return Promise.resolve(); },
      pause() { this.paused = true; }, load() {},
    };
    for (const k of ["src", "href"])
      Object.defineProperty(e, k, { set(v) { requests.push(String(v)); }, get() { return ""; } });
    made.push(e);
    return e;
  }
  const handlers = {};
  const btns = RAIL.map((id) => {
    const m = htmlSrc.match(btnRe(id));
    const bt = el("button");
    bt.id = id;
    bt.attrs.title = m ? m[1] : "";
    bt.addEventListener = (ev, fn) => { (handlers[id] = handlers[id] || {})[ev] = fn; };
    bt.matches = () => true;
    bt.getBoundingClientRect = () => ({ left: 0, right: 40, top: 100, bottom: 130, width: 40, height: 30 });
    return bt;
  });
  made.length = 0;
  const body = el("body");
  const documentStub = { querySelectorAll: () => btns, createElement: (t) => el(t), body, addEventListener() {} };
  const fetchStub = (url) => { requests.push("fetch " + url); return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve(null) }); };
  function XHRStub() { requests.push("xhr"); this.open = (m, u) => requests.push("xhr " + u); this.send = () => {}; }
  const placed = [];
  const posSpy = (btn, pop, vw, vh, gap) => { placed.push(btn); return T.tipvidPos(btn, pop, vw, vh, gap); };
  const run = new Function("document", "fetch", "XMLHttpRequest", "matchMedia", "setTimeout", "clearTimeout",
                           "state", "innerWidth", "innerHeight", "tipvidEntry", "tipvidClip", "tipvidPos",
                           "TIPVID_HOVER_DELAY_MS", "TIPVID_TIPS", "tipvidClipsOk", "console", wiring);
  const mq = (q) => ({ matches: /reduced-motion/.test(q) ? reduced
    : /hover:\s*none/.test(q) ? noHover : /pointer:\s*coarse/.test(q) ? coarse : false });
  run(documentStub, fetchStub, XHRStub, mq,
      (fn) => { timers.push(fn); return timers.length; }, () => {},
      { tool }, width, 900, T.tipvidEntry, T.tipvidClip, posSpy, 600, T.TIPVID_TIPS, T.tipvidClipsOk,
      { error: (...x) => errors.push(x.join(" ")), log() {}, warn: (...x) => errors.push(x.join(" ")) });
  const pop = () => body.children.find((c) => c.id === "tipvid");
  async function hover(id) {
    handlers[id].pointerenter();
    timers.splice(0).forEach((fn) => fn());
    await new Promise((r) => setImmediate(r));
    const p = pop();
    return p && !p.hidden ? p : null;
  }
  const leave = (id) => handlers[id].pointerleave();
  return { requests, made, errors, hover, leave, placed, pop };
}

(async () => {
  // a + b: load, first hovers, second hovers
  {
    const H = harness();
    check(H.requests.length === 0, `loading the page requests nothing (${H.requests.join(", ")})`);
    const opened = [];
    for (const id of RAIL) {
      const before = H.requests.length;
      const p = await H.hover(id);
      const got = H.requests.slice(before);
      if (WANT_TIPS.includes(id)) {
        check(!!p, `${id}: the tip opens`);
        if (!p) continue;
        opened.push(id);
        const title = p.children.find((c) => c.className === "tv-title");
        const cap = p.children.find((c) => c.className === "tv-cap");
        const vid = p.children.find((c) => c.tag === "video");
        check(title && title.textContent === (htmlSrc.match(btnRe(id)) || [])[1] && cap && cap.textContent === tips[id].caption,
          `${id}: the tip shows the button's title and its caption`);
        check(vid && vid.attrs.src === "tooldemos/" + tips[id].file && p.children[0] === vid && !vid.paused,
          `${id}: its own clip plays at the top of the tip`);
        check(p.children.filter((c) => c.tag === "video").length === 1, `${id}: one video in the tip, never two`);
        check(got.length === 1 && got[0] === "tooldemos/" + tips[id].file,
          `${id}: first hover requests its clip, once, and nothing else (${got.join(", ")})`);
      } else if (TEXT_TIPS.includes(id)) {
        check(!!p && !p.children.some((c) => c.tag === "video") && got.length === 0 &&
              p.children.some((c) => c.className === "tv-cap" && c.textContent.startsWith(tips[id].caption)) &&
              p.children.some((c) => c.className === "tv-title" && c.textContent === (htmlSrc.match(btnRe(id)) || [])[1]),
          `${id}: a text tip opens (title + caption), no video, no request (${got.join(", ")})`);
      } else {
        check(!p && got.length === 0, `${id}: no tip, no request (${got.join(", ")})`);
      }
      H.leave(id);
      if (p) check(p.hidden && p.children.every((c) => c.tag !== "video" || c.paused), `${id}: leaving hides the tip and pauses the clip`);
    }
    check(opened.length === WANT_TIPS.length, `every tool with a tip opens it (${opened.length} of ${WANT_TIPS.length})`);
    const n = H.requests.length;
    for (const id of RAIL) { await H.hover(id); H.leave(id); }
    check(H.requests.length === n, `a second round of hovers requests nothing (${H.requests.slice(n).join(", ")})`);
    check(H.made.filter((e) => e.tag === "video").length === WANT_TIPS.length, "one <video> per clip, made on its first open");
    check(!H.made.some((e) => ["img", "source", "link", "script", "iframe"].includes(e.tag)),
      "no other URL-loading element is ever created");
    check(H.placed.every((r) => "bottom" in r && "width" in r), "every placement passes bottom + width to the frame hook");
    check(H.errors.length === 0, `no console error or warning (${H.errors.join("; ")})`);
  }
  // c: a clip that fails to load
  {
    const H = harness();
    const p = await H.hover("splitBtn");
    const vid = p.children.find((c) => c.tag === "video");
    const placedBefore = H.placed.length;
    vid.fire("error");
    check(!p.hidden && !p.children.some((c) => c.tag === "video"),
      "a failed clip leaves the tip open as text, no video box in it");
    check(p.children.some((c) => c.className === "tv-cap" && c.textContent === tips.splitBtn.caption),
      "the failed clip's caption is still there");
    check(H.placed.length === placedBefore + 1, "the text tip is re-placed at its new height");
    H.leave("splitBtn");
    const n = H.requests.length;
    const p2 = await H.hover("splitBtn");
    check(p2 && !p2.children.some((c) => c.tag === "video") && H.requests.length === n,
      "a failed clip is never asked for again; its tip opens as text");
    const p3 = await H.hover("measureBtn");
    check(p3 && p3.children.some((c) => c.tag === "video"), "other tools keep their clips");
    check(H.errors.length === 0, `the failure logs nothing (${H.errors.join("; ")})`);
  }
  // d: reduced motion
  {
    const H = harness({ reduced: true });
    let ok = 0;
    for (const id of WANT_TIPS) {
      const p = await H.hover(id);
      if (p && !p.children.some((c) => c.tag === "video") &&
          p.children.some((c) => c.className === "tv-cap" && c.textContent === tips[id].caption)) ok++;
      H.leave(id);
    }
    check(ok === WANT_TIPS.length, `reduced motion: all ten tips open as text (${ok})`);
    check(H.requests.length === 0 && !H.made.some((e) => e.tag === "video"),
      `reduced motion: no video made, no request (${H.requests.join(", ")})`);
  }
  // f: a phone / touch screen / narrow window: text only, no clip asked for
  for (const env of [{ noHover: true, coarse: true, width: 390 }, { noHover: true }, { coarse: true },
                     { width: 767 }]) {
    const H = harness(env);
    let ok = 0;
    for (const id of WANT_TIPS) {
      const p = await H.hover(id);
      if (p && !p.children.some((c) => c.tag === "video") &&
          p.children.some((c) => c.className === "tv-cap" && c.textContent === tips[id].caption)) ok++;
      H.leave(id);
    }
    const tag = JSON.stringify(env);
    check(ok === WANT_TIPS.length, `${tag}: every tip opens as text with its caption (${ok})`);
    check(H.requests.length === 0 && !H.made.some((e) => e.tag === "video"),
      `${tag}: no video made, no clip requested (${H.requests.join(", ")})`);
  }
  {
    const H = harness({ width: 768 });
    const p = await H.hover("splitBtn");
    check(p && p.children.some((c) => c.tag === "video"), "a 768 px window with a mouse still gets the clip");
  }
  check(T.tipvidClipsOk({ width: 1440 }) && !T.tipvidClipsOk({ width: 1440, coarse: true }) &&
        !T.tipvidClipsOk({ width: 1440, noHover: true }) && !T.tipvidClipsOk({ width: 1440, reduced: true }) &&
        !T.tipvidClipsOk({ width: 500 }) && !T.tipvidClipsOk(null),
    "tipvidClipsOk: a clip only for a wide window with a hovering, fine pointer and motion allowed");
  // e: armed tool
  {
    const H = harness({ tool: "measure" });
    for (const id of WANT_TIPS) { const p = await H.hover(id); check(!p, `${id}: no tip while a canvas tool is armed`); }
    check(H.requests.length === 0, "an armed tool: no request");
  }
  if (failures) { console.error(`\n${failures} FAILURE(S)`); process.exit(1); }
  console.log("\nall tooldemo tip checks passed");
})();
