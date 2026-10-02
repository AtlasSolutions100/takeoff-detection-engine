// UI fixes from the 2026-09-26 audit (PLAN.md sections 11 and 12), pinned
// from source the way the other UI tests do it, plus the pure helpers run:
//   * the ruler: right-click and Backspace remove the last point and never
//     add one or delete a line; one Esc removes a point, Esc with none ends;
//     arming the ruler clears the line selection; no context menu over an
//     armed ruler (D31)
//   * a room rename rebuilds the takeoff rows, so the export and the report
//     carry the new name (D27)
//   * the line types showing at the last pick travel with the rooms and are
//     put back after a reload (D27)
//   * the room pick sends the scale on screen; the room keeps the scale it
//     was measured at and Edit shape re-measures at it (D09)
//   * an upload is sniffed for %PDF- before it is sent (D35)
//   * the triage strip knows the "plan" tier and never dims it (D38)
//   * a demo card's tool hint opens the Flooring panel (D45)
//
// Dependency-free:  node tests/test_ui_flooring_counts_2026_09_26.js
"use strict";
const fs = require("fs");
const path = require("path");
const SRC = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
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
function block(startRx, endStr) {
  const m = startRx.exec(SRC);
  if (!m) return null;
  const end = SRC.indexOf(endStr, m.index);
  return end < 0 ? null : SRC.slice(m.index, end + endStr.length);
}

console.log("ruler");
const measureClick = block(/if \(state\.tool === "measure"\) \{\n\s+\/\/ RIGHT-CLICK REMOVES/, "return;\n  }");
check(measureClick && /if \(e\.button === 2\) \{ measureDropLast\(\); return; \}/.test(measureClick)
      && measureClick.indexOf("e.button === 2") < measureClick.indexOf("state.measurePts.push"),
      "a right-click on the canvas removes the ruler's last point and never adds one");
const drop = grab("measureDropLast");
check(drop && /state\.measurePts\.pop\(\)/.test(drop) && /updateMeasureInfo\(\)/.test(drop)
      && /if \(state\.tool !== "measure"\) return false;/.test(drop),
      "measureDropLast pops one point, refreshes the readout, and does nothing unless the ruler is armed");
const del = block(/if \(\(e\.key === "Delete" \|\| e\.key === "Backspace"\) && !typing\(\)\) \{/, "else removeSelected();\n  }");
check(del && /if \(state\.tool === "measure"\) \{ measureDropLast\(\); return; \}/.test(del)
      && del.indexOf("measureDropLast") < del.indexOf("multiDeleteAll"),
      "Backspace with the ruler armed drops a ruler point BEFORE the delete handler can remove a line");
const esc = block(/if \(state\.tool === "measure"\) \{\n\s+\/\/ one Esc removes the last point/, "setStatus(\"\");\n    }");
check(esc && /if \(state\.measurePts\.length\) \{ measureDropLast\(\); return; \}/.test(esc)
      && /state\.tool = null; state\.measurePts = \[\];/.test(esc),
      "Esc removes a point while there are points, and ends the ruler when there are none");
const arm = block(/\$\("measureBtn"\)\.onclick = \(\) => \{/, "draw();\n};");
check(arm && /state\.selected = null; state\.selPt = null; state\.clickPt = null;\n\s+updateSelBar\(null\);/.test(arm)
      && /else if \(state\.selected !== null\)/.test(arm),
      "arming the ruler clears the line selection (so Backspace cannot reach a line)");
check(/cv\.addEventListener\("contextmenu", \(e\) => \{\n\s+if \(state\.tool === "measure"\) e\.preventDefault\(\);/.test(SRC),
      "no browser context menu opens over an armed ruler");
// the pure helper, run: Backspace on 3 points leaves 2, on 0 leaves 0, and off-tool it declines
const runDrop = new Function("state", "updateMeasureInfo", "setStatus", "draw",
  drop + "; return measureDropLast;");
{
  const st = { tool: "measure", measurePts: [[0, 0], [10, 0], [10, 10]] };
  const f = runDrop(st, () => {}, () => {}, () => {});
  check(f() === true && st.measurePts.length === 2, "3 points -> 2 on one drop");
  f(); f();
  check(f() === true && st.measurePts.length === 0, "0 points: the drop is a no-op that still claims the key");
  const st2 = { tool: null, measurePts: [[0, 0], [1, 1]] };
  check(runDrop(st2, () => {}, () => {}, () => {})() === false && st2.measurePts.length === 2,
        "with the ruler not armed the helper declines and touches nothing");
}

console.log("rename reaches the export");
const renderFl = grab("renderFl");
// (the hover text follows the name too: check 5B, 2026-09-27 - a long name is cut by its box)
check(renderFl && /nm\.oninput = \(\) => \{ r\.name = nm\.value; (nm\.title = nm\.value; )?flApplyItems\(\); \};/.test(renderFl),
      "a room rename rebuilds the takeoff rows (flApplyItems), not just the dirty flag");
const applyItems = grab("flApplyItems");
check(applyItems && /calcStateDirty\(\)/.test(applyItems) && /renderItemized\(\)/.test(applyItems),
      "...and flApplyItems marks the record dirty and repaints the itemized rows");

console.log("walls-only view after a reload");
const pick = grab("flPickAt");
check(pick && /fl\.wallStyles = flVisibleStyles\(\);/.test(pick), "a successful pick records the line types it was made with");
const save = grab("flSavePayload");
check(save && /wallStyles: Array\.isArray\(fl\.wallStyles\) && fl\.wallStyles\.length \? fl\.wallStyles\.slice\(\) : null/.test(save),
      "the record carries them");
const restore = grab("flApplySaved");
check(restore && /sv\.wallStyles/.test(restore) && /setFamVis\(s\.style_id, keep\.has\(s\.style_id\)\)/.test(restore)
      && /renderStyles\(\)/.test(restore),
      "a reload puts them back through the same style ticks the pick reads");
check(restore && restore.indexOf("sv.wallStyles") > restore.indexOf("fl.wallRows = !!sv.wallRows"),
      "...after the rest of the record, so nothing later overrides them");

console.log("the scale a room was measured at");
check(pick && /scale_ft_per_in: flScreenScale\(\)/.test(pick), "the pick sends the scale on screen");
check(pick && /scale_ft_per_in: \+j\.scale_ft_per_in > 0 \? \+j\.scale_ft_per_in : null/.test(pick) && /scale_by: j\.scale_by \|\| null/.test(pick),
      "the room keeps the scale the server used and who set it");
const fpp = grab("flRoomFpp");
const runFpp = new Function("stFtPerPt", fpp + "; return flRoomFpp;")(() => 4 / 72);
check(Math.abs(runFpp({ scale_ft_per_in: 8 }) - 8 / 72) < 1e-12 && Math.abs(runFpp({}) - 4 / 72) < 1e-12,
      "flRoomFpp: the room's own scale when it has one, else the sheet's");
const note = grab("flRoomScaleNote");
const runNote = new Function(note + "; return flRoomScaleNote;")();
check(/inset's own scale/.test(runNote({ scale_ft_per_in: 8, scale_by: "inset caption SCALE: 1/8\" = 1'-0\"" }))
      && runNote({ scale_ft_per_in: 8, scale_by: "on screen" }) === "",
      "the room line names an inset measurement and says nothing for the sheet's own scale");
check((SRC.match(/flEditRecompute\(r, flRoomFpp\(r\)\)/g) || []).length === 2 && !/flEditRecompute\(r, stFtPerPt\(\)\)/.test(SRC),
      "Edit shape re-measures at the room's own scale everywhere");
const draw2 = grab("flFinishDraw");
check(draw2 && /flInsetRemeasure\(room\)/.test(draw2) && /scale\/at\?/.test(grab("flInsetRemeasure")),
      "a drawn outline asks the server which caption governs its centre");

console.log("upload sniff");
const head = grab("pdfHeadOk");
const runHead = new Function(head + "; return pdfHeadOk;")();
check(runHead("%PDF-1.7\n%\xe2\xe3") === true && runHead("garbage %PDF-1.4") === true && runHead("hello world") === false && runHead("") === false,
      "pdfHeadOk: %PDF- anywhere in the head passes, anything else is refused");
const up = grab("uploadPdf");
check(up && /file\.slice\(0, 1024\)/.test(up) && /pdfHeadOk\(t\)/.test(up) && /uploadPdfNow\(file\)/.test(up)
      && !/xhr\.open/.test(up),
      "uploadPdf reads the first kilobyte before any transfer starts, and hands off to uploadPdfNow");
check(up && /is not a PDF inside/.test(up), "...and says so in the operator's terms");

console.log("triage plan tier");
const tier = /const TRI_TIER_WORD = \{[\s\S]*?\};/.exec(SRC);
check(tier && /plan: "a building plan/.test(tier[0]), "the strip has a word for a building plan");
check(/"tri-core", "tri-maybe", "tri-plan", "tri-other"/.test(SRC), "the strip clears the plan class with the others");
check(/r\.tier === "core" \|\| r\.tier === "maybe" \|\| r\.tier === "plan"/.test(SRC), "a plan counts as kept, never as what highlight dims");
check(/\.thumb\.tri-plan \{ border-top: 3px solid/.test(CSS), "style.css paints the plan tier");

console.log("demo card tool hint");
const demo = grab("pvOpenDemo");
check(demo && /tool === "flooring"/.test(demo) && /\$\("flBtn"\)\.click\(\)/.test(demo),
      "a card whose manifest entry names the flooring tool opens the Flooring panel after the fresh open");
check(demo && demo.indexOf("applyOpened(j") < demo.indexOf("tool === \"flooring\""), "...after applyOpened, on the sheet the card described");

console.log("no client names on screen");
const strings = SRC.replace(/\/\/[^\n]*/g, "").replace(/\/\*[\s\S]*?\*\//g, "");
check(!/Barn C3\.0|Beaumont|Pierce'?s/.test(strings), "no client job or client name in a UI string");

console.log("page rotation (view only)");
{
  const names = ["viewRot", "rotSize", "imgToRot", "rotToImg"];
  const R = new Function("state", names.map(grab).join("\n") + "\nreturn { " + names.join(", ") + " };");
  const st = { img: { width: 300, height: 200 }, view: { rot: 0 } };
  const r = R(st);
  for (const rot of [0, 90, 180, 270]) {
    st.view.rot = rot;
    const [W, H] = r.rotSize();
    check((rot === 90 || rot === 270) ? (W === 200 && H === 300) : (W === 300 && H === 200), `rotSize at ${rot}: ${W}x${H}`);
    let ok = true;
    for (const [px, py] of [[0, 0], [300, 0], [0, 200], [300, 200], [37, 151]]) {
      const [u, v] = r.imgToRot(px, py);
      const [bx, by] = r.rotToImg(u, v);
      if (Math.abs(bx - px) > 1e-9 || Math.abs(by - py) > 1e-9) ok = false;
      if (u < -1e-9 || v < -1e-9 || u > W + 1e-9 || v > H + 1e-9) ok = false;
    }
    check(ok, `imgToRot / rotToImg round-trip inside the rotated frame at ${rot}`);
  }
  st.view.rot = 90;
  check(JSON.stringify(r.imgToRot(0, 0)) === "[200,0]" && JSON.stringify(r.imgToRot(300, 200)) === "[0,300]",
        "a quarter turn clockwise: the top-left corner goes to the top-right, the bottom-right to the bottom-left");
  st.view.rot = 450;
  check(r.viewRot() === 90, "viewRot normalises to 0..359");
}
const drawSrc = grab("draw");
check(drawSrc && /ctx\.translate\(x, y\); ctx\.scale\(k, k\);\n\s*viewApplyRot\(ctx\);/.test(drawSrc),
      "draw() composes the quarter turn right after the view's translate and scale, before the sheet is drawn");
const pfe = grab("ptFromEvent");
check(pfe && /rotToImg\(/.test(pfe), "ptFromEvent inverts the turn: screen -> rotated frame -> the page's own frame");
check(/imgToRot\(xPt \* f, yPt \* f\)/.test(grab("centerOn")) && /imgToRot\(q\[0\] \* f, q\[1\] \* f\)/.test(grab("lineCardAnchor")),
      "centerOn and the line card anchor map through the turn");
const fv = grab("fitView");
check(fv && /rotSize\(\)/.test(fv), "fitView fits the rotated frame");
check(/\$\("rotateBtn"\)\.onclick = \(\) => rotateView\(90\)/.test(SRC) && /id="rotateBtn"/.test(fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8")),
      "the toolbar's Rotate button turns the view a quarter clockwise");
const rv = grab("rotateView");
check(rv && /store\.setItem\(rotKey\(\)/.test(rv) && /every length, area and count is unchanged/.test(rv),
      "the turn is remembered per page and the status says it is display only");
check(/store\.getItem\(rotKey\(\)/.test(SRC) && /state\.view\.rot = \[90, 180, 270\]\.includes\(rot\) \? rot : 0;/.test(SRC),
      "a page load restores the stored turn, and only a quarter turn");

if (fails) { console.log(fails + " FAILURE(S)"); process.exit(1); }
console.log("all green");
