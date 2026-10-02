// CHECK 6A ON THE CUT / FILL SCREENS (2026-09-27, VERIFY-6A.md "What is still wrong"): the screen
// defects it found, pinned on the functions themselves (pulled out of the page scripts and run
// against stand-in elements) and, where a function needs the whole page, on its source.
//
// run: node tests/test_cutfill_check6a.js
"use strict";
const fs = require("fs");
const path = require("path");
const ejs = fs.readFileSync(path.join(__dirname, "..", "webui", "cutfill", "app.js"), "utf8");
const ehtml = fs.readFileSync(path.join(__dirname, "..", "webui", "cutfill", "index.html"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(src, name) {
  const start = src.indexOf("function " + name + "(");
  if (start < 0) return "";
  let depth = 0;
  for (let j = src.indexOf("{", start); j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}" && --depth === 0) return src.slice(start, j + 1);
  }
  return "";
}
const constSrc = (src, name) => { const m = src.match(new RegExp("const " + name + " = [^\\n]+")); return m ? m[0] : ""; };
const ev = (v) => +((/ui\/app\.js\?v=(\d+)/.exec(v) || [])[1]);

// A stand-in for the page's el(): a node that keeps its tag, attributes, children and text.
function node(tag, attrs, ...kids) {
  const n = { tag, attrs: attrs || {}, kids: [], style: {}, hidden: false,
    append(...more) { for (const k of more.flat(Infinity)) if (k != null && k !== false) n.kids.push(k); },
    replaceChildren(...more) { n.kids = []; n.append(...more); },
    addEventListener() {}, focus() {}, select() {} };
  n.append(kids);
  Object.defineProperty(n, "text", { get: () => n.kids.map((k) => (typeof k === "object" ? k.text : String(k))).join("") });
  return n;
}
const find = (n, pred) => { if (!n || typeof n !== "object") return null; if (pred(n)) return n; for (const k of n.kids || []) { const f = find(k, pred); if (f) return f; } return null; };

// ---- D1. a pen merged into a family is that family's pen (the canvas, the Lines card, the popups, the story) ----
// Home 2 Suites C3: the legend names two black pens proposed (0.4 and 0.5 wide); the read puts them in ONE family
// (styles [1, 13], style 1). indexAnalysis filled the lookup with each family's primary pen only, so a line of
// pen 13 (the 5-ft contours 5990, 5995) found no family: drawn gray, left out of the Lines card, its popup offered
// "make existing / make proposed" - while the volumes used it as proposed (compute_cutfill gives every pen of a
// family the family's role).
const C_STUB = { ex: "EX", pr: "PR", noz: "NOZ", ignore: "IGN", accent: "ACC" };
const D1_SRC = [constSrc(ejs, "nf0"), constSrc(ejs, "nf1"), constSrc(ejs, "isNum"), fnSrc(ejs, "unNeg"), constSrc(ejs, "fmt0"), constSrc(ejs, "fmt1"),
  constSrc(ejs, "fmtZ"), fnSrc(ejs, "uniq"), fnSrc(ejs, "shortName"), fnSrc(ejs, "pageSettings"), fnSrc(ejs, "famRole"), fnSrc(ejs, "indexAnalysis"),
  fnSrc(ejs, "penDesc"), fnSrc(ejs, "penWords"), fnSrc(ejs, "penFromSig"),
  fnSrc(ejs, "contourStyle"), fnSrc(ejs, "pageEdits"), fnSrc(ejs, "lineRows"), fnSrc(ejs, "longestChain"), constSrc(ejs, "HOW_TEXT"), fnSrc(ejs, "howText"),
  fnSrc(ejs, "segDistXY"), fnSrc(ejs, "simplifyPts"), fnSrc(ejs, "popupAnchor"), fnSrc(ejs, "openContourPopup"), fnSrc(ejs, "openPiecePopup"),
  fnSrc(ejs, "sourceDetail"), fnSrc(ejs, "buildQuestions")].join("\n");
const SIG1 = "0.00,0.00,0.00|w0.4|s|", SIG13 = "0.00,0.00,0.00|w0.5|s|", SIG5 = "0.50,0.50,0.50|w0.5|d|";
function h2s() {
  return {
    families: [
      { style: 1, styles: [1, 13], sig: SIG1, role: "proposed", role_reason: "legend: = PROPOSED 1' CONTOUR", interval: 1, gray: 0, dashed: false, width: 0.4, layer: "",
        color: [0, 0, 0], n_contours: 3, n_with_z: 3, n_labels: 5, length_total: 1000 },
      { style: 5, styles: [5], sig: SIG5, role: "existing", role_reason: "legend: = EXISTING 1' CONTOUR", interval: 1, gray: 0.5, dashed: true, width: 0.5, layer: "",
        color: [0.5, 0.5, 0.5], n_contours: 1, n_with_z: 1, n_labels: 1, length_total: 300 },
    ],
    styles: [{ id: 1, sig: SIG1, color: [0, 0, 0], gray: 0, width: 0.4, dashed: false, layer: "" }, { id: 13, sig: SIG13, color: [0, 0, 0], gray: 0, width: 0.5, dashed: false, layer: "" },
      { id: 5, sig: SIG5, color: [0.5, 0.5, 0.5], gray: 0.5, width: 0.5, dashed: true, layer: "" }, { id: 3, sig: "0.70,0.70,0.70|w0.3|s|", color: [0.7, 0.7, 0.7], gray: 0.7, width: 0.3, dashed: false, layer: "" }],
    chains: [{ id: 100, style: 13, pts: [[0, 0], [400, 0]], length: 400 }, { id: 101, style: 13, pts: [[0, 50], [300, 50]], length: 300 },
      { id: 102, style: 13, pts: [[0, 60], [200, 60]], length: 200 }, { id: 200, style: 1, pts: [[0, 100], [300, 100]], length: 300 },
      { id: 300, style: 5, pts: [[0, 150], [300, 150]], length: 300 }, { id: 9684, style: 3, pts: [[0, 200], [100, 200]], length: 100 }],
    contours: [
      { id: 1519, style: 13, chain_ids: [100], z: 5990, how: "label", label_ids: [7], length: 400 },
      { id: 1520, style: 13, chain_ids: [101], z: 5995, how: "counted", basis: [{ k: 5, dir: 1, z: 5990 }], length: 300 },
      { id: 1521, style: 13, chain_ids: [102], z: null, how: "", length: 200 },
      { id: 20, style: 1, chain_ids: [200], z: 5991, how: "label", length: 300 },
      { id: 30, style: 5, chain_ids: [300], z: 5990, how: "label", length: 300 },
    ],
    labels: [{ id: 7, text: "5990", bbox: [0, 0, 10, 5], kind: "contour", chain_ids: [100], value: 5990 }], spots: [], notes: [],
  };
}
function engine(ps) {
  const st = { key: "d0p0", project: { pages: { d0p0: ps || {} } }, analyzes: {}, selected: null };
  const popups = [];
  const env = new Function("state", "el", "C", "showPopup", "requestDraw", "toPage", "ensureAnalysis", "goToLine", "hidePopup",
    D1_SRC + "\nreturn { indexAnalysis, famRole, contourStyle, lineRows, openContourPopup, openPiecePopup, sourceDetail, buildQuestions };")(
    st, node, C_STUB, (n) => popups.push(n), () => {}, (x, y) => [x, y], () => null, (r) => { st.went = r; }, () => {});
  const an = env.indexAnalysis(h2s());
  st.analyzes.d0p0 = an;
  return { st, an, env, popups };
}
let E = engine();
check(E.an._fam.get(13) === E.an._fam.get(1) && E.an._fam.get(1).style === 1 && E.an._fam.get(5).role === "existing" && !E.an._fam.get(3),
  "every pen of a family finds its family (pen 13 -> the family of pen 1); a pen in no family finds none");
check(E.env.famRole("d0p0", E.an, 13) === "proposed" && E.env.famRole("d0p0", E.an, 1) === "proposed" && E.env.famRole("d0p0", E.an, 5) === "existing"
  && E.env.famRole("d0p0", E.an, 3) === "ignore", "the merged pen reads its family's surface: proposed (it answered ignore)");
const ct = (id) => E.an._contour.get(id);
check(E.env.contourStyle(E.an, ct(1519)).color === "PR" && E.env.contourStyle(E.an, ct(1520)).color === "PR" && E.env.contourStyle(E.an, ct(30)).color === "EX",
  "the canvas draws the 5990 and 5995 lines of the merged pen orange, the proposed colour (they were gray)");
let rows = E.env.lineRows(E.an);
check(rows.some((r) => r.id === 1519 && r.role === "proposed" && r.z === 5990) && rows.some((r) => r.id === 1520 && r.role === "proposed"),
  "the Lines card lists them as proposed (a search for 5990 found only the existing line): " + rows.map((r) => `#${r.id} ${r.role}`).join(", "));
E.env.openContourPopup(ct(1519), 10, 10, E.an._chain.get(100));
let h4 = find(E.popups[E.popups.length - 1], (n) => n.tag === "h4");
check(h4 && /proposed ⇄/.test(h4.text) && !/make existing|make proposed/.test(h4.text) && E.st.selected && E.st.selected.role === "proposed",
  "its popup names its surface (proposed ⇄), not 'make existing / make proposed': " + (h4 && h4.text));
E.env.openPiecePopup(E.an._chain.get(101), 10, 10);
h4 = find(E.popups[E.popups.length - 1], (n) => n.tag === "h4");
check(h4 && /\(proposed\)/.test(h4.text), "the piece popup of a merged pen's piece says proposed (an._fam.get(piece.style)): " + (h4 && h4.text));
const story = E.env.sourceDetail({ kind: "contour", page: "d0p0", z: 5995, ref: { how: "counted", contour: 1520, basis: [{ k: 5, dir: 1, z: 5990 }] } });
check(story === "contour 5995 – counted 5 × 1 ft above 5990", "the cell story finds the merged pen's family and its interval (an._fam.get(ct.style)): " + story);
let qs = E.env.buildQuestions(E.an);
check(qs.some((q) => /proposed line #1521/.test(q.text)), "the Questions card asks about the merged pen's line with no height as a proposed line (it was skipped as ignored)");
// the operator's pick for the family - the Families card sets it by the family's primary pen - holds for the merged pen too
E = engine({ style_roles: { [SIG1]: "existing" } });
rows = E.env.lineRows(E.an);
check(E.env.famRole("d0p0", E.an, 13) === "existing" && E.env.contourStyle(E.an, E.an._contour.get(1519)).color === "EX"
  && rows.some((r) => r.id === 1519 && r.role === "existing"), "the family set to existing in the Families card (style_roles by the family's pen): pen 13's lines follow");
E = engine({ family_roles: { "1": "existing" } });
check(E.env.famRole("d0p0", E.an, 13) === "existing" && E.env.contourStyle(E.an, E.an._contour.get(1519)).color === "EX"
  && E.env.lineRows(E.an).some((r) => r.id === 1519 && r.role === "existing"), "a pick not yet migrated (family_roles by the primary pen): pen 13's lines follow it too");
E = engine({ style_roles: { [SIG1]: "ignore" } });
check(E.env.famRole("d0p0", E.an, 13) === "ignore" && E.env.contourStyle(E.an, E.an._contour.get(1519)).color === "IGN" && !E.env.lineRows(E.an).some((r) => r.id === 1519),
  "the family set to ignore: pen 13's lines leave the Lines card with it");
// the canvas says what the volumes use: the compute's rule (cutfill/project.py compute_cutfill), transliterated
const computeRole = (an, ps, style) => {
  let out = "ignore";
  for (const f of an.families) {
    const sr = ps.style_roles || {}, ov = ps.family_roles || {};
    const r = sr[f.sig] != null ? sr[f.sig] : ov[String(f.style)] != null ? ov[String(f.style)] : f.role;
    if ((f.styles || [f.style]).includes(style)) out = r;
  }
  return out;
};
let same = 0, total = 0;
for (const ps of [{}, { style_roles: { [SIG1]: "existing" } }, { style_roles: { [SIG5]: "ignore" } }, { family_roles: { "1": "ignore" } },
  { family_roles: { "13": "existing" } }, { style_roles: { [SIG13]: "existing" } }, { style_roles: { [SIG1]: "ignore" }, family_roles: { "5": "proposed" } }]) {
  E = engine(ps);
  for (const s of [1, 13, 5, 3]) { total++; if (E.env.famRole("d0p0", E.an, s) === computeRole(E.an, ps, s)) same++; }
}
check(same === total, `the page's surface of every pen is the compute's, under seven sets of picks: ${same} of ${total}`);
// the Questions card's family question goes to a line of any pen of the family
{
  const an0 = h2s();
  an0.families.push({ style: 40, styles: [40, 41], sig: "0.30,0.30,0.30|w0.2|s|", role: "ignore", gray: 0.3, dashed: false, width: 0.2, layer: "", n_contours: 25, length_total: 5000 });
  an0.chains.push({ id: 400, style: 41, pts: [[0, 300], [500, 300]], length: 500 });
  an0.contours.push({ id: 60, style: 41, chain_ids: [400], z: null, how: "", length: 500 });
  const st = { key: "d0p0", project: { pages: { d0p0: {} } }, analyzes: {} };
  const env = new Function("state", "el", "C", "showPopup", "requestDraw", "toPage", "ensureAnalysis", "goToLine",
    D1_SRC + "\nreturn { indexAnalysis, buildQuestions };")(st, node, C_STUB, () => {}, () => {}, (x, y) => [x, y], () => null, (r) => { st.went = r; });
  const an = env.indexAnalysis(an0); st.analyzes.d0p0 = an;
  const q = env.buildQuestions(an).find((x) => x.kind === "style");
  if (q) q.go();
  check(q && st.went && st.went.id === 60, "the family question's Go finds a line of the family's second pen (it looked for the primary pen only)");
}
check(ev(ehtml) >= 37, "the engine page's cache buster moved (ui/app.js)");

// ---- D2. every note of the read reaches the screen: a card of its own, and the status line says how many more ----
// The engine held the read's notes (the legend's pens, pieces drawn like curbs and walls set aside, a pen moved to the
// other surface, heights on no line, a floor height, the named-in-review notes) and only notes[0] reached the screen,
// on the status line after a read (pollAnalysis).
const NOTES = [
  "the legend shows a pen for every contour row: 9 other pens are not taken for contours (set one in the Families card if it is)",
  "99 pieces in a contour pen are drawn like curbs and walls (straight lines and arcs, 46,406 pt): not taken for contours (type a height on one that is)",
  "a dark pen (gray 0.0 · solid · w0.7) carries screened heights like the existing ground's (12 of 14): it is read as EXISTING (set it in the Families card if it is not)",
  "5 screened contour heights stand on no line of their pen: " + "left out; ".repeat(30) + "the end of a long note",
  "1 printed floor height inside a closed outline is offered as a building pad (Building pads card)",
];
function notesCard(an) {
  const box = { innerHTML: "", kids: [], append(n) { box.kids.push(n); } };
  const els = { noteList: box, noteCount: { textContent: "x" } };
  new Function("$", "state", "el", fnSrc(ejs, "renderNotes") + "\nrenderNotes();")((id) => els[id], { key: "d0p0", analyzes: an ? { d0p0: an } : {} }, node);
  return { rows: box.kids, count: els.noteCount.textContent };
}
let nc = notesCard({ notes: NOTES });
check(nc.count === "5" && nc.rows.length === 5 && nc.rows.every((r, i) => r.text === NOTES[i] && /noterow/.test(r.attrs.class)),
  "the card lists all five notes of the read, each whole (the longest is " + NOTES[3].length + " characters), and its title counts them: " + nc.count);
nc = notesCard({ notes: [] });
check(nc.count === "0" && nc.rows.length === 1 && nc.rows[0].text === "No notes: the read had nothing to say.", "a read with no notes says so: " + (nc.rows[0] && nc.rows[0].text));
nc = notesCard(null);
check(nc.count === "" && nc.rows[0].text === "Read the sheet first.", "a sheet not read yet: the card says to read it");
check(/\.noterow \.grow\{white-space:normal;overflow:visible;overflow-wrap:anywhere/.test(fnSrc(ejs, "injectCss")),
  "a note wraps and is never cut (the list rows cut their text with an ellipsis)");
check(/renderChanges\(\); renderNotes\(\);/.test(fnSrc(ejs, "renderAllPanels")), "the card is drawn with every other card (a new read, another sheet)");
const secNotes = ehtml.indexOf('id="secNotes"');
check(secNotes > ehtml.indexOf('id="secChanges"') && secNotes < ehtml.indexOf('id="secLines"')
  && /<h3>Notes of this read <span id="noteCount" class="pill"><\/span><\/h3>/.test(ehtml) && /id="noteList"/.test(ehtml),
  "the card sits beside What changed, its count in its title");
check(/<div id="noteList" class="list"><\/div>/.test(ehtml), "the card grows with its notes: no scroll box whose edge would cut a note (a read has a handful)");
const notesLine = new Function(fnSrc(ejs, "readNotesLine") + "\nreturn readNotesLine;")();
check(notesLine(NOTES) === " " + NOTES[0] + " (+ 4 more in Notes of this read)" && notesLine(NOTES.slice(0, 1)) === " " + NOTES[0] && notesLine([]) === "",
  "the status line keeps the first note and says how many more the card holds: " + notesLine(NOTES).slice(-36));
check(/\+ readNotesLine\(notes\)/.test(fnSrc(ejs, "pollAnalysis")), "the status line after a read uses it");
check(ev(ehtml) >= 38, "the engine page's cache buster moved (ui/app.js)");

// ---- D3. no internal key on screen: a pen by its words, never its signature or its style number ----
// What changed printed the raw pen signature ("0.00,0.00,0.00|w0.4|s|"), the line-piece popup "Line piece #9684 ·
// style 3", the review screen's legend chips "applied to style 11". The words are the Families card's.
const RAW_SIG = /\d\.\d\d,\d\.\d\d,\d\.\d\d\|w/;
const W = new Function(D1_SRC + "\nreturn { penDesc, penWords, penFromSig };")();
check(W.penDesc({ gray: 0, dashed: false, width: 0.4, layer: "" }) === "gray 0.0 · solid · w0.4"
  && W.penDesc({ gray: 0.52, dashed: true, width: 0.5, layer: "C-TOPO-EXIST-MINOR-LINES" }) === "gray 0.5 · dashed · w0.5 · layer C-TOPO-EXIST-MI…",
  "a pen in the Families card's words (gray, solid or dashed, width, layer): " + W.penDesc({ gray: 0.52, dashed: true, width: 0.5, layer: "C-TOPO-EXIST-MINOR-LINES" }));
check(/const desc = penDesc\(f\);/.test(fnSrc(ejs, "renderFamilies")), "the Families card names its pens with the same words");
check(W.penFromSig("0.00,0.00,0.00|w0.4|s|") === "gray 0.0 · solid · w0.4" && W.penFromSig("0.50,0.50,0.50|w0.5|d|C-TOPO") === "gray 0.5 · dashed · w0.5 · layer C-TOPO",
  "a signature from a reading made before the server named its pens is read as the same words");
function changesCard(ch) {
  const items = [];
  const box = { innerHTML: "", append(n) { items.push(n); } };
  const st = { key: "d0p0", analyzes: { d0p0: { changes: ch } } };
  new Function("$", "state", "el", D1_SRC + "\n" + fnSrc(ejs, "renderChanges") + "\nrenderChanges();")((id) => box, st, node);
  return items.map((n) => ({ text: n.text, title: n.attrs.title || "" }));
}
const famRow = (sig, pen, extra) => Object.assign({ sig, pen, before: { role: "proposed", contours: 12, with_z: 10, length_with_z: 1234 },
  after: { role: "existing", contours: 12, with_z: 10, length_with_z: 1234 }, role_changed: true }, extra || {});
let cc = changesCard({ fresh_read: true, reader_changed: true, engine: ["0.9 reader 3+aaaaaaaaaaaa interpretation bbbbbbbbbbbb", "0.9 reader 3+cccccccccccc interpretation dddddddddddd"],
  records: {}, records_before: {}, heights: {}, labels: {}, families: [
    famRow("0.00,0.00,0.00|w1.9|s|", "gray 0.0 · solid · w1.9"),
    famRow("0.00,0.00,0.00|w0.4|s|", "gray 0.0 · solid · w0.4", { new: true, before: null, role_changed: false, after: { role: "proposed", contours: 3, with_z: 2 } }),
    famRow("0.50,0.50,0.50|w0.3|d|", "gray 0.5 · dashed · w0.3", { gone: true, after: null, role_changed: false }),
    famRow("0.00,0.00,0.00|w0.7|s|", null),                                     // a reading made before the words: read from its signature
    famRow("switched to proposed by you (proposed)", null, { role_changed: false, after: { role: "proposed", contours: 2, with_z: 2 } })] });
const allText = cc.map((r) => r.text).join(" | ");
check(!RAW_SIG.test(allText), "no raw pen signature in the card's words: " + allText.slice(0, 160));
check(cc.some((r) => r.text === "Pen gray 0.0 · solid · w1.9: proposed → existing, 12 → 12 lines, 10 → 10 with a height (1,234 → 1,234 pt)."
  .replace(/1,234/g, (s) => new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(1234)) && r.title === "pen signature 0.00,0.00,0.00|w1.9|s|"),
  "a pen that moved surface is named by its words; its signature is the row's tooltip (for support)");
check(cc.some((r) => /^New pen gray 0\.0 · solid · w0\.4: proposed, 3 lines, 2 with a height\.$/.test(r.text))
  && cc.some((r) => /^Pen gray 0\.5 · dashed · w0\.3 \(proposed\) is no longer a contour family\.$/.test(r.text)), "a new pen and a pen gone, by their words");
check(cc.some((r) => /^Pen gray 0\.0 · solid · w0\.7: /.test(r.text)), "a row of a reading made before the words is named from its signature");
check(cc.some((r) => /^Switched to proposed by you \(proposed\): /.test(r.text)), "the operator's own lines keep their name");
check(cc[0].text === "Fresh read by changed reader code; every line was read again." && /3\+aaaaaaaaaaaa.*3\+cccccccccccc/.test(cc[0].title)
  && !/[0-9a-f]{12}/.test(allText), "the code stamps (hashes) are the row's tooltip, not its words: " + cc[0].text);
cc = changesCard({ fresh_read: false, interp_changed: true, engine: ["0.9 reader 3+a interpretation b", "0.9 reader 3+a interpretation c"], records: {}, heights: {}, labels: {}, families: [] });
check(cc[0].text === "Same reading of the sheet, interpreted again by changed code." && /interpretation c/.test(cc[0].title), "and on a re-interpretation");
// the piece popup: "Line piece #9684 · pen gray 0.7 · solid · w0.3 (ignore)", its style number in the tooltip
E = engine();
E.env.openPiecePopup(E.an._chain.get(9684), 10, 10);
h4 = find(E.popups[E.popups.length - 1], (n) => n.tag === "h4");
check(h4 && h4.text === "Line piece #9684 · pen gray 0.7 · solid · w0.3 (ignore)" && !/style \d/.test(h4.text) && /line style 3/.test(h4.attrs.title || ""),
  "the line-piece popup names its pen in words (it said 'style 3'); the number stays in its tooltip: " + (h4 && h4.text));
E.env.openPiecePopup(E.an._chain.get(101), 10, 10);
h4 = find(E.popups[E.popups.length - 1], (n) => n.tag === "h4");
check(h4 && h4.text === "Line piece #101 · pen gray 0.0 · solid · w0.5 (proposed)", "a merged pen's piece: its own pen, its family's surface: " + (h4 && h4.text));
// the pick tool's status line: "Line style 13 set to proposed" -> the pen in words
(async () => {
  const st = { key: "d0p0", project: { pages: { d0p0: {} } }, analyzes: {}, view: { k: 1 }, pickRole: "proposed" };
  const said = [];
  const env = new Function("state", "patchPage", "setStatus", "cancelTool", "renderFamilies", "markStale", "startAnalysis", "el",
    D1_SRC + "\n" + fnSrc(ejs, "hitChain") + "\nasync " + fnSrc(ejs, "pickClick") + "\nreturn { indexAnalysis, pickClick };")(
    st, async () => ({}), (m) => said.push(m), () => {}, () => {}, () => {}, async () => {}, node);
  st.analyzes.d0p0 = env.indexAnalysis(h2s());
  await env.pickClick(200, 0.5);                                 // on chain 100 (pen 13)
  st.pickRole = "ignore";
  await env.pickClick(50, 200.2);                                // on chain 9684 (pen 3)
  check(said[0] === "Pen gray 0.0 · solid · w0.5 set to proposed. Updating the sheet…" && said[1] === "Pen gray 0.7 · solid · w0.3 set to not a contour. Updating the sheet…",
    "the pick tool's status names the pen in words (it said 'Line style 13 set to proposed'): " + said.join(" | "));
  // the review screen's legend chips
  const hjs = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
  const hhtml = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
  const sgDefs = hjs.slice(hjs.indexOf("const SG_DEFS = ["), hjs.indexOf("];", hjs.indexOf("const SG_DEFS = [")) + 2);
  const legendSrc = [sgDefs, fnSrc(hjs, "sgKeyOf"), fnSrc(hjs, "layerSplit"), fnSrc(hjs, "penWords"), fnSrc(hjs, "renderLegend")].join("\n");
  const hostPen = new Function(legendSrc + "\nreturn penWords;")();
  check(hostPen({ color: [0, 0, 0], class: "solid", width: 0.4, layer: "" }) === "gray 0.0 · solid · w0.4"
    && hostPen({ color: [0.5, 0.5, 0.5], class: "dash1", width: 0.5, layer: "XREF-SITE|C-TOPO-EXIST" }) === "gray 0.5 · dashed · w0.5 · layer C-TOPO-EXIST"
    && hostPen({ color: [0, 0, 1], class: "solid", width: 0.35, layer: "0" }) === "gray 0.1 · solid · w0.35", "the review screen names a pen in the same words");
  const mk = (tag) => { const n = { tag, kids: [], style: {}, className: "", title: "", appendChild(k) { n.kids.push(k); return k; },
    getContext: () => ({ setLineDash() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {} }) }; return n; };
  const doc = { createElement: mk, createTextNode: (t) => ({ tag: "#text", text: t }) };
  const box = { innerHTML: "", hidden: true, kids: [], appendChild(k) { box.kids.push(k); } };
  const head = { hidden: true, classList: { contains: () => false } };
  const hstate = { labels: new Map([[11, "EXISTING 1' CONTOUR (legend)"]]), result: {
    legend: [{ sample_style: 11, label: "EXISTING 1' CONTOUR", klass: "dash1" }, { sample_style: 26, label: "PROPOSED 1' CONTOUR", klass: "solid" },
      { sample_style: null, label: "PROPERTY LINE", klass: "dash2" }],
    styles: [{ style_id: 11, color: [0.5, 0.5, 0.5], class: "dash1", width: 0.5, layer: "" }, { style_id: 26, color: [0, 0, 0], class: "solid", width: 0.4, layer: "" }], chains: [] } };
  new Function("$", "state", "document", "cssColor", "hotFam", "hotFamOff", legendSrc + "\nrenderLegend();")(
    (id) => (id === "legendBox" ? box : head), hstate, doc, () => "#000", () => {}, () => {});
  const titles = box.kids.map((k) => k.title);
  check(titles[0] === "applied to the pen gray 0.5 · dashed · w0.5" && titles[1] === "click to label the pen gray 0.0 · solid · w0.4 \"PROPOSED 1' CONTOUR\" — hover to highlight"
    && !titles.some((t) => /style \d/.test(t)), "the legend chips' tooltips name the pen in words (they said 'applied to style 11'): " + titles.slice(0, 2).join(" | "));
  check(+((/app\.js\?v=(\d+)/.exec(hhtml) || [])[1]) >= 232 && ev(ehtml) >= 39, "both cache busters moved (app.js, ui/app.js)");

  // ---- D5. a scale is printed with two decimals at most on the review screen's sheet head; the Scale box shows it whole ----
  // The review screen's sheet head printed the graphic bar's reading as the reader wrote it: "graphic bar 0-80 ft over 143.9pt
  // (1" = 40.0167', ticks)" (scale_detect prints it with :g), and "drawn bar says 40.016655966932305 ft/in" when they disagree.
  {
  const BAR = 40.016655966932305;
  const barText = `graphic bar 0-80 ft over 143.9pt (1" = ${+BAR.toPrecision(6)}', ticks)`;       // what the reader writes
  const headSrc = [fnSrc(hjs, "nWord"), fnSrc(hjs, "cfScaleTxt"), fnSrc(hjs, "scaleSaid"), fnSrc(hjs, "scaleShort"), fnSrc(hjs, "sheetFactsText"), fnSrc(hjs, "renderSheetHead")].join("\n");
  const said = new Function(headSrc + "\nreturn scaleSaid;")();
  check(barText === `graphic bar 0-80 ft over 143.9pt (1" = 40.0167', ticks)` && said({ text: barText }) === `graphic bar 0-80 ft over 143.9pt (1" = 40.02', ticks)`,
    "a graphic bar's reading: its measured number with two decimals at most: " + said({ text: barText }));
  check(said({ text: `graphic bar 0-100 ft over 90.0pt (1" = 80', labels)` }) === `graphic bar 0-100 ft over 90.0pt (1" = 80', labels)`
    && said({ text: `SCALE: 1" = 40.0167'` }) === `SCALE: 1" = 40.0167'` && said({ text: `1"=20'` }) === `1"=20'` && said(null) === "",
    "a whole number stays whole; a printed caption is quoted as printed (it is the sheet's own words)");
  function head(sc) {
    const mkEl = (tag) => { const n = { tag, className: "", textContent: "", title: "", hidden: false, innerHTML: "", kids: [], append(...k) { n.kids.push(...k); } }; return n; };
    const sheetHead = mkEl("div"), facts = { className: "" }, seg = { textContent: "" };
    const hst = { job: "j1", page: 0, kinds: ["grading"], docName: "x.pdf", result: { sheet_no: "C3", scale: sc, chains: [], styles: [] } };
    new Function("$", "state", "document", "scaleSuggestions", "dimCheckFor", "runStateNow", "renderScalePick", "renderScaleTyped", "renderScaleSuggest",
      "renderConfidence", "runFlagSync", "swatchFactsText", "renderZoomFact", headSrc + "\n" + fnSrc(hjs, "renderSheetFacts") + "\nrenderSheetHead();")(
      (id) => ({ sheetHead, sheetFacts: facts, sbSheet: seg })[id], hst, { createElement: mkEl }, () => [], () => null, () => ({ chip: "detected", chipCls: "ok" }),
      () => {}, () => {}, () => {}, () => {}, () => {}, () => "", () => {});
    return { chips: sheetHead.kids[0].kids.map((c) => c.textContent), facts: seg.textContent };
  }
  const bar = { text: barText, ft_per_paper_inch: BAR, source: "graphic-bar" };
  let hd = head(Object.assign({}, bar, { agrees: null }));
  check(hd.chips.includes(`graphic bar 0-80 ft over 143.9pt (1" = 40.02', ticks)`) && !hd.chips.join(" ").includes("40.0167"),
    "the sheet head prints the bar's reading with 40.02 (it printed 40.0167): " + hd.chips.join(" | "));
  hd = head({ text: `1" = 50'`, ft_per_paper_inch: 50, source: "text", agrees: false, bar_ft_per_paper_inch: BAR, delta_pct: 20 });
  check(hd.chips.includes("drawn bar says 40.02 ft/in · 20% apart — CHECK") && /but the drawn bar says 40\.02 ft\/in/.test(hd.facts) && !/40\.01665/.test(hd.chips.join(" ") + hd.facts),
    "a caption the drawn bar disagrees with: the bar's number with two decimals, in the head and on the status line: " + hd.chips.find((c) => /^drawn bar/.test(c)));
  hd = head({ text: `1" = 40'`, ft_per_paper_inch: 40, source: "text", agrees: true, bar_ft_per_paper_inch: BAR, delta_pct: 0.04 });
  check(hd.chips.includes(`1" = 40' ✓ matches drawn bar`), "a caption the bar agrees with is quoted as printed");
  hd = head(Object.assign({}, bar, { agrees: null, typed_by_reviewer: true }));
  const hd2 = head(Object.assign({}, bar, { agrees: null, from_suggestion: true }));
  check(hd.chips.some((c) => /40\.02', ticks\) — SET BY HAND$/.test(c)) && hd2.chips.some((c) => /40\.02', ticks\) — YOU PICKED AN UNCHECKED READING$/.test(c)),
    "and every other chip of a bar's reading (set by hand, picked)");
  // the engine's Scale box: as wide as its number
  const box = (settings, own, key) => {
    const els = { scaleList: { innerHTML: "", hints: [], append(x) { this.hints.push(x); } }, scaleIn: { value: "", style: {} },
      scaleStatus: { textContent: "", className: "", title: "" } };
    new Function("$", "state", "el", "document", "shortName", "chooseScale", "ownSheetScale", "sheetName", "shownKey",
      [fnSrc(ejs, "fmtScale"), fnSrc(ejs, "fitScaleBox"), fnSrc(ejs, "scaleRefused"), fnSrc(ejs, "renderScale")].join("\n") + "\nrenderScale();")(
      (id) => els[id], { project: { settings }, analyzes: {}, key: key || "d0p0" }, (_t, _a, ...kids) => ({ text: kids.join("") }),
      { activeElement: null }, (s) => s, () => {}, (k) => (own && own[k] != null ? own[k] : null), (k) => ({ d0p0: "3.0", d0p1: "3.1" })[k],
      () => "d0p0");
    els.scaleIn.status = els.scaleStatus.textContent;
    els.scaleIn.hints = els.scaleList.hints.map((h) => h.text);
    return els.scaleIn;
  };
  const chOf = (inp) => +((/^calc\((\d+)ch \+ 26px\)$/.exec(inp.style.width) || [])[1]);
  const b40 = box({ scale_ft_per_in: BAR, scale_source: "read in review" }), b120 = box({ scale_ft_per_in: 120.25 }), b0 = box({ scale_ft_per_in: null });
  check(b40.value === "40.02" && chOf(b40) === 6 && chOf(b120) === 7 && chOf(b0) === 5,
    `the Scale box is as wide as its number and a digit more: 40.02 -> ${b40.style.width}, 120.25 -> ${b120.style.width}, empty -> ${b0.style.width}`);
  // round 16: on a sheet that is not the sheet shown, with a review, the box shows that sheet's own scale (typed there it
  // sets that sheet's review; showing the scale in force, the box snapped back to it after the Enter) and says so
  const bOwn = box({ scale_ft_per_in: 50, scale_source: "confirmed in review" }, { d0p1: 40 }, "d0p1");
  check(bOwn.value === "40" && bOwn.status === "this sheet, in review"
    && bOwn.hints.some((h) => h === "This is sheet 3.1's own scale, from its review. The cut / fill measures sheet 3.0, the sheet shown."),
    "on another sheet with a review the box shows that sheet's own scale, and the card says the cut / fill measures the "
    + "sheet shown: " + JSON.stringify([bOwn.value, bOwn.status, bOwn.hints]));
  check(/id="scaleIn" type="number" step="1" min="1" style="width:80px"/.test(ehtml) && /\$\('scaleIn'\)\.addEventListener\('input', \(\) => \{ state\.scaleTypedFor = state\.key; fitScaleBox\(\$\('scaleIn'\)\); \}\)/.test(fnSrc(ejs, "bindUi")),
    "before the page script sizes it the box is 80 px (it was 56), and it grows while a longer number is typed");
  check(+((/app\.js\?v=(\d+)/.exec(hhtml) || [])[1]) >= 234 && ev(ehtml) >= 40, "both cache busters moved again (app.js, ui/app.js)");
  }
  console.log(fails ? "\n" + fails + " failure(s)" : "\nall ok");
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error("FAIL: " + (e && e.stack || e)); process.exit(1); });
