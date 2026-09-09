// A volume built from MACHINE-READ elevations must not read, on the panel
// or on the printed report, exactly like one built from the sheet's own
// text.
//
// THE DEFECT (measured 2026-09-02 on the exact-truth outline sheet
// dem_hurricane_bench_a_pad_2ft_outline.pdf, whose every label is a glyph
// outline, so its digits can only be recognised off a render). After the
// gap-OCR rows fix that sheet produces 17 `gap-ocr` contour rows and a
// volume: cut 1838.4 / fill 4697.1 CY against an exact raster truth of
// 3256.7 / 3157.3 - 44% low on cut, 49% high on fill, net sign flipped.
// ELEV_PROV["gap-ocr"].kind is "printed" (a label IS in that break), so
// ewAcceptedSplit counted all 17 as printed, ewInferredAccepted returned 0
// and ewBasisNote returned [] - the report's earthwork notes were WORD FOR
// WORD those of a fully native sheet, and the basis block beside the number
// said nothing either. On a mixed sheet it was worse than silent: "Of 18
// contour elevations accepted here, 1 are printed NOWHERE on the sheet"
// tells the estimator the other 17 were read off the drawing.
//
// THE RULE: `kind` answers "is a label printed on that line"; a second
// axis, `machine`, answers "were the digits recognised rather than read",
// and every surface that speaks about the basis of a volume says so. The
// clause comes FIRST because `approve` truncates the tail at
// APPROVE_NOTE_MAX.
//
//     node tests/test_ew_machine_read_note.js
"use strict";
const fs = require("fs");
const path = require("path");
const R = (...p) => fs.readFileSync(path.join(__dirname, "..", ...p), "utf8");
const js = R("webui", "app.js");
const html = R("webui", "index.html");
const css = R("webui", "style.css");

let fails = 0;
const check = (ok, msg) => {
  if (ok) console.log("ok: " + msg);
  else { fails++; console.error("FAIL: " + msg); }
};
const fn = (name) => {
  const a = js.indexOf("function " + name + "(");
  if (a < 0) throw new Error("no function " + name);
  const b = js.indexOf("\n}\n", a);
  return js.slice(a, b + 3);
};

// ---- the second axis exists on the machine-read entry -------------------
const prov = js.slice(js.indexOf("const ELEV_PROV = {"),
                      js.indexOf("const ELEV_PROV_UNKNOWN")).replace(/\s+/g, " ");
check(/"gap-ocr":\s*\{[^}]*machine:\s*true/.test(prov),
  "ELEV_PROV['gap-ocr'] carries machine: true");
check(!/\binline:\s*\{[^}]*machine:\s*true/.test(prov),
  "and a natively-read inline label does not");

// ---- the split counts it, across the printed/inferred buckets -----------
check(/machine:\s*0/.test(fn("ewAcceptedSplit")),
  "ewAcceptedSplit initialises a machine counter");
check(/prov\.machine/.test(fn("ewAcceptedSplit")),
  "and increments it from the provenance table, not a second list");

// ---- the printed report's sentence -------------------------------------
const APPROVE_NOTE_MAX = 400;
const mk = (o) => Object.assign({ total: 0, printed: 0, nearby: 0,
  nearbyTagged: 0, infill: 0, inherited: 0, manual: 0, unknown: 0,
  machine: 0 }, o);
// "use strict" scopes a bare eval, so bind the real source explicitly.
const ewBasisNote = eval("(" + fn("ewBasisNote") + ")");

const allMachine = ewBasisNote(mk({ total: 17, printed: 17, machine: 17 }));
check(allMachine.length === 1,
  "a sheet whose every elevation was machine-read gets a note at all "
  + "(this returned [] before the fix)");
check(/read by machine/.test(allMachine[0] || ""),
  "and the note says the digits were read by machine");
check(/check them against the drawing/i.test(allMachine[0] || ""),
  "and says what to do about it");

// Seen on the live report 2026-09-02: the machine-read sentence was
// followed by "Of 17 contour elevations accepted here, all are printed on
// the sheet", which is true of the LABELS and reads as a retraction of the
// caveat. Where every accepted elevation was machine-read, the second
// sentence carries the fact that was actually useful instead.
check(!/all are printed on the sheet/.test(allMachine[0] || ""),
  "an all-machine-read sheet does not follow the caveat with a sentence "
  + "that reads as taking it back");
check(/label the drafter drew/.test(allMachine[0] || ""),
  "it says the label is real and only the digits were recognised");
check(/all are printed on the sheet/.test(
        ewBasisNote(mk({ total: 20, printed: 20, machine: 8 }))[0] || ""),
  "while a PARTLY machine-read sheet keeps the printed count - there the "
  + "two sentences are about different rows");

const native = ewBasisNote(mk({ total: 17, printed: 17 }));
check(native.length === 0,
  "a fully native sheet is unchanged - still no note, so the disclosure "
  + "means something when it appears");
check(JSON.stringify(allMachine) !== JSON.stringify(native),
  "and the two sheets no longer produce identical report text");

const mixed = ewBasisNote(mk({ total: 18, printed: 17, machine: 17,
                               inherited: 1 }))[0] || "";
check(/read by machine/.test(mixed) && /printed NOWHERE/.test(mixed),
  "a mixed sheet keeps BOTH clauses - machine-read and printed-nowhere");
check(mixed.length <= APPROVE_NOTE_MAX,
  "and fits APPROVE_NOTE_MAX (" + mixed.length + " chars), so `approve` "
  + "truncates neither away");
check(mixed.indexOf("read by machine") < mixed.indexOf("printed NOWHERE"),
  "with the machine clause FIRST, because the tail is what truncation cuts");

// ---- the panel beside the number says the same thing --------------------
// 4000 until 2026-09-03: renderEwBasis gained the comment explaining why it
// remembers what it was painted from (R1 - a row edit has to repaint it),
// which pushed the machine-read line past the old window and turned this
// check red without any behaviour moving. Sized off the whole function now.
const at = js.indexOf('const box = $("ewBasis");');
const basis = js.slice(at, at + 7000);
check(/ewAcceptedSplit\(\)/.test(basis),
  "the basis block beside the volume reads the same split");
check(/read by machine/.test(basis),
  "and prints the same fact in the same words as the report");
check(/ew-basis-ocr/.test(basis), "in its own styled line");
check(/\.ew-basis \.ew-basis-ocr/.test(css), "which style.css styles");

// ---- the cache buster moved, or nobody sees any of this -----------------
check(+((/app\.js\?v=(\d+)/.exec(html) || [])[1] || 0) >= 160,
  "app.js cache buster at or past 160 (a later bump is fine; an older one is not)");
check(+((/style\.css\?v=(\d+)/.exec(html) || [])[1] || 0) >= 77,
  "style.css cache buster at or past 77");

console.log();
if (fails) { console.error(fails + " FAILED"); process.exit(1); }
console.log("all checks passed");
